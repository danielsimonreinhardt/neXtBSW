"""Unit tests for tools/nxconfig with synthetic profiles (test/) and the repository's demo project.

Run: .venv/Scripts/python -m unittest tools/nxconfig/test_nxconfig.py
"""

import copy
import functools
import json
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))

import nxconfig  # noqa: E402
from nxconfig import checks, config as nxconfig_config, memory, powerstage, sensed, sensor  # noqa: E402
from nxconfig.model import Manifest  # noqa: E402

TEST_MCUDB = HERE / "test" / "mcudb"
TEST_BOARD = yaml.safe_load((HERE / "test/profiles/boards/testboard/board.yaml").read_text(encoding="utf-8"))
TEST_PROJECT = yaml.safe_load((HERE / "test/profiles/projects/testproject/project.yaml").read_text(encoding="utf-8"))
# PLAT-62: the own full-featured test profile (synthetic testboard_full/testproject_full) instead of the eval board
# project, which stays private (PLAT-46 F10) - a test against it would be red in the exported open tree. What
# proves something about the eval board itself uses EVAL_DEMO_PROJECT and is skipped where it is missing.
DEMO_PROJECT = REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full" / "project.yaml"
EVAL_DEMO_PROJECT = REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml"
EVAL_CHARGE_PROJECT = REPO / "projects" / "evalboard_s32k144_charge" / "project.yaml"
FULL_BOARD = HERE / "test" / "profiles" / "boards" / "testboard_full" / "board.yaml"
FULL_DBC = DEMO_PROJECT.parent / "TestFull.dbc"
eval_board = unittest.skipUnless(EVAL_DEMO_PROJECT.is_file(),
                                 "private eval board project not in this tree (exported open tree, PLAT-62)")
# IO-24 expected the warning "PWM group MOT1 has no power_stage" ([("warning", "pwm[0]")]) on every check of the demo
# project. IO-26 entered the facts of the MOT1 power stage and the project margin: the demo project is free of
# messages again (values: EvalPowerStageTest).
EVAL_OPEN_POWER_STAGE = []


def on_eval_board(test):
    """PLAT-62: the test states values of the eval board itself (its crystal, power stage, sensors, messages) - it runs
    against the eval board demo where it exists (private tree) and is skipped in the exported open tree. DEMO_PROJECT
    points to the eval board demo while the test runs; everything else in this file uses the own profiles."""

    @functools.wraps(test)
    def run(self, *args, **kwargs):
        if not EVAL_DEMO_PROJECT.is_file():
            raise unittest.SkipTest("private eval board project not in this tree (exported open tree, PLAT-62)")
        module = globals()
        saved = module["DEMO_PROJECT"]
        module["DEMO_PROJECT"] = EVAL_DEMO_PROJECT
        try:
            return test(self, *args, **kwargs)
        finally:
            module["DEMO_PROJECT"] = saved

    return run


def findings(messages) -> list[tuple[str, str]]:
    return [(m.severity, m.yaml_path) for m in messages]


# SYS-95 (DFA-4 a): the synthetic testboard's PWM group BRIDGE has no gate driver, so every check of the synthetic
# profiles would carry the warning "no safe_off output ... pwm.BRIDGE" on top of what a test is about. The warning
# itself is tested in test_pwmgate.py (own profiles) and by PwmGateInSyntheticProfileTest below; in every other test
# of this module it is switched off, in-process only (a test that runs the CLI in a subprocess still sees it).
_PWMGATE_CHECK = checks.pwmgate.check
_pwmgate_off = mock.patch.object(checks.pwmgate, "check", lambda cfg, rep: [])


def setUpModule():
    _pwmgate_off.start()


def tearDownModule():
    _pwmgate_off.stop()


def with_can_ranks(project: dict, rank: int = 0) -> dict:
    """ARCH-33: a rank for every CAN bus of the project (project can.<bus>) that has none yet.

    On a derivative with interrupt ranks a used CAN bus without one is an error; tests about something else give
    the synthetic buses (VEH, DIAG) a rank, so that their expected messages stay the ones they are about.
    """
    ranks = project.setdefault("interrupts", {}).setdefault("ranks", [])
    named = {r.get("name") for r in ranks if r.get("source") == "can"}
    ranks.extend({"source": "can", "name": name, "rank": rank} for name in project.get("can", {}) if name not in named)
    return project


EMPTY_RTE = ("schema_version: 2\ntasks: [{ name: swc, period_ms: 10, priority: 30, stack_bytes: 2048 }]\n"
             "components: []\n")


def with_empty_rte(project: dict, folder: Path) -> dict:
    """TOOL-114: a copy of a real project on a board of boards/ is built, so it needs an ECU description. A test
    about something else gives it the smallest one - one task, no component - instead of the demo's."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "rte.yaml").write_text(EMPTY_RTE, encoding="utf-8")
    project["project"]["rte"] = "rte.yaml"
    # SYS-87: without components nothing writes the indicator, so a confirmation that it does (the demo's) would be
    # reported as superfluous - true, but not what these tests are about
    (project.get("safety") or {}).pop("safe_state_output_shared_with_application", None)
    return project


def only_used_can_ranks(project: dict) -> dict:
    """ARCH-33 counterpart: after a test narrows use.can, the rank of a bus it no longer uses would be a warning."""
    used = project["use"]["can"]
    if used != "all" and "interrupts" in project:
        project["interrupts"]["ranks"] = [r for r in project["interrupts"]["ranks"]
                                          if r["source"] != "can" or r["name"] in used]
    return project


class Profiles(unittest.TestCase):
    """Writes (modified) copies of the synthetic board and project into a temporary tree."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, board=None, project=None) -> Path:
        board_dir = self.tmp / "boards" / "testboard"
        project_dir = self.tmp / "projects" / "testproject"
        board_dir.mkdir(parents=True, exist_ok=True)
        project_dir.mkdir(parents=True, exist_ok=True)
        (board_dir / "board.yaml").write_text(yaml.safe_dump(TEST_BOARD if board is None else board, sort_keys=False),
                                              encoding="utf-8")
        path = project_dir / "project.yaml"
        path.write_text(yaml.safe_dump(TEST_PROJECT if project is None else project, sort_keys=False),
                        encoding="utf-8")
        return path

    def check(self, board=None, project=None, mcudb=TEST_MCUDB):
        return nxconfig.check(self.write(board, project), mcudb)

    def errors(self, board=None, project=None, mcudb=TEST_MCUDB) -> list[nxconfig.Message]:
        _, messages = self.check(board, project, mcudb)
        return [m for m in messages if m.severity == "error"]

    def expect(self, fragment, board=None, project=None, level=None, path=None):
        errors = self.errors(board, project)
        match = [m for m in errors if fragment in m.text]
        self.assertTrue(match, f"'{fragment}' not in {[str(m) for m in errors]}")
        if level is not None:
            self.assertEqual(match[0].file.name, f"{level}.yaml")
        if path is not None:
            self.assertEqual(match[0].yaml_path, path)
        return match[0]

    @staticmethod
    def board():
        return copy.deepcopy(TEST_BOARD)

    @staticmethod
    def project():
        return copy.deepcopy(TEST_PROJECT)


class PwmGateInSyntheticProfileTest(Profiles):
    """SYS-95: the switched-off pwmgate check of this module is real - with it the synthetic profile warns once."""

    def test_synthetic_bridge_warns(self):
        with mock.patch.object(checks.pwmgate, "check", _PWMGATE_CHECK):
            _, messages = self.check()
        self.assertEqual(findings(messages), [("warning", "pwm.BRIDGE")])
        self.assertIn("AoU-07", messages[0].text)

    def test_switched_off_elsewhere(self):
        _, messages = self.check()
        self.assertEqual(messages, [])


class LoadTest(Profiles):
    def test_synthetic_project(self):
        config, messages = self.check()
        self.assertEqual(messages, [])
        self.assertEqual(config.derivative.derivative.family, "testfam")
        self.assertEqual([c.name for c in config.selected("can")], ["VEH", "DIAG"])
        self.assertEqual([c.name for c in config.selected("adc")], ["VBAT"])
        self.assertEqual([c.name for c in config.selected("dio.outputs")], ["LED_GREEN", "LED_RED"])
        self.assertEqual([(g.name, u.frequency_hz) for g, u in config.selected_pwm()], [("BRIDGE", 16000)])
        self.assertIsNone(config.rte_file)
        self.assertIsNone(config.board.spi[0].sck.pad)
        self.assertEqual(config.board.adc[0].scale.divider.r_top, 30000)

    def test_selection_keeps_board_order(self):
        project = self.project()
        project["use"]["can"] = ["DIAG", "VEH"]
        config, _ = self.check(project=project)
        self.assertEqual([c.name for c in config.selected("can")], ["VEH", "DIAG"])

    def test_omitted_selection_means_none(self):
        project = self.project()
        del project["use"]["uart"]
        config, _ = self.check(project=project)
        self.assertEqual(config.selected("uart"), [])

    @on_eval_board
    def test_repository_demo_project(self):
        config, messages = nxconfig.check(DEMO_PROJECT)
        self.assertEqual(findings(messages), EVAL_OPEN_POWER_STAGE)
        self.assertEqual(config.board.board.name, "evalboard_s32k144")
        self.assertEqual(config.derivative_file, REPO / "mcudb" / "s32k1xx" / "S32K144.yaml")
        self.assertEqual(config.rte_file, DEMO_PROJECT.parent / "rte.yaml")
        self.assertEqual([c.name for c in config.selected("input_capture")], ["PWM_IN"])

    def test_load_raises_with_all_errors(self):
        project = self.project()
        project["use"]["can"] = ["NOPE"]
        project["pwm"]["MISSING"] = {"frequency_hz": 1, "deadtime_ns": 1}
        with self.assertRaises(nxconfig.ConfigErrors) as ctx:
            nxconfig.load(self.write(project=project), TEST_MCUDB)
        self.assertEqual(len(ctx.exception.messages), 2)
        self.assertIn("no can entry NOPE", str(ctx.exception))
        self.assertIn("no PWM group MISSING", str(ctx.exception))


class SchemaTest(Profiles):
    def test_type_error_has_path_and_line(self):
        project = self.project()
        project["pwm"]["BRIDGE"]["frequency_hz"] = "fast"
        m = self.expect("valid integer", project=project, level="project", path="pwm.BRIDGE.frequency_hz")
        text = (self.tmp / "projects/testproject/project.yaml").read_text(encoding="utf-8").splitlines()
        self.assertIn("frequency_hz: fast", text[m.line - 1])
        self.assertIn("project.yaml:", str(m))

    def test_board_schema_errors(self):
        cases = [
            (lambda b: b["pwm"][0].update(mode="independent"), "Input should be 'complementary'", "pwm[0].mode"),
            (lambda b: b["pwm"][0].update(dead_time=1), "Extra inputs are not permitted", "pwm[0].dead_time"),
            (lambda b: b["pwm"][0]["active"].pop("low_side"), "Field required", "pwm[0].active.low_side"),
            (lambda b: b["pwm"][0].update(pairs=[]), "at least 1 item", "pwm[0].pairs"),
            (lambda b: b["dio"]["inputs"][0].update(pull="strong"), "'up' or 'down'", "dio.inputs[0].pull"),
            (lambda b: b["adc"][0]["scale"].update(factor=2), "both factor and divider", "adc[0].scale"),
            (lambda b: b["adc"][1].update(scale={"factor": 0}), "greater than 0", "adc[1].scale.factor"),
            (lambda b: b["adc"][1].update(scale={"factor": float("inf")}), "finite number", "adc[1].scale.factor"),
            (lambda b: b["adc"][1].update(scale={"offset_mv": 1.5}), "valid integer", "adc[1].scale.offset_mv"),
            (lambda b: b["adc"][1].update(scale={"offset_mv": 1 << 40}), "less than or equal", "adc[1].scale.offset_mv"),
            (lambda b: b["dio"]["outputs"][0].update(name="LED-1"), "should match pattern", "dio.outputs[0].name"),
            (lambda b: b["board"].update(adc_reference_mv=True), "valid integer", "board.adc_reference_mv"),
            # TOOL-66: a format this tree does not read is caught before the schema, with the reason
            (lambda b: b.update(schema_version=2), "older than the files", "schema_version"),
        ]
        for change, fragment, path in cases:
            with self.subTest(fragment=fragment):
                board = self.board()
                change(board)
                self.expect(fragment, board=board, level="board", path=path)

    def test_selection_type(self):
        project = self.project()
        project["use"]["dio"] = "some"
        self.expect("Input should be 'all'", project=project, path="use.dio")

    def test_missing_board_and_derivative(self):
        project = self.project()
        project["project"]["board"] = "../../boards/nothere"
        m = self.expect("board ../../boards/nothere not found", project=project, level="project",
                        path="project.board")
        self.assertIsNotNone(m.line)
        board = self.board()
        board["board"]["mcu"] = "NOMCU"
        self.expect("no derivative description NOMCU.yaml", board=board, level="board", path="board.mcu")

    def test_invalid_yaml(self):
        path = self.write()
        path.write_text("project: [unclosed\n", encoding="utf-8")
        config, messages = nxconfig.check(path, TEST_MCUDB)
        self.assertIsNone(config)
        self.assertIn("invalid YAML", messages[0].text)


# ARCH-34: ECU description of the synthetic project (two CAN buses VEH/DIAG, no eval board names)
TEST_ECU = {
    "schema_version": 2,        # RTE-22: paths below are relative to the rte.yaml this is written to
    "tasks": [{"name": "fast", "period_ms": 5, "priority": 25, "stack_bytes": 1024,
               "runnables": ["Ctrl.Run5ms", {"runnable": "Ctrl.Run50ms", "offset_ms": 10}]},
              {"name": "slow", "period_ms": 50, "priority": 35, "stack_bytes": 1536}],
    "components": [{"name": "Ctrl", "path": "swc/Ctrl"}, {"name": "Mon", "path": "swc/Mon"}],
    "can": {"dbc": "projects/testproject/veh.dbc", "node": "ECU", "bus": "DIAG", "task": "fast",
            "monitor": ["Status"],
            "qualifiers": {"Cmd": {"timeout_ms": 200, "debounce_ms": 20, "on_error": "init",
                                   "e2e": {"profile": "P01", "data_id": 0x123, "data_id_mode": "both", "crc": "Crc",
                                           "counter": "Cnt"}},
                           "Plain": None}},
    "uds": {"bus": "VEH"},
    "connections": {"Ctrl.In.In": "dio.in.KEY", "Ctrl.Out.Out": ["dio.out.LED_GREEN", "rte"]},
}


class EcuDescriptionTest(Profiles):
    """ARCH-34: rte.yaml (project.rte) is checked against its schema with file, line and YAML path."""

    def check_ecu(self, ecu, project=None):
        project = self.project() if project is None else project
        project["project"]["rte"] = "rte.yaml"
        path = self.write(project=project)
        (path.parent / "rte.yaml").write_text(yaml.safe_dump(ecu, sort_keys=False), encoding="utf-8")
        return nxconfig.check(path, TEST_MCUDB)

    def test_valid_description_is_loaded_as_model(self):
        config, messages = self.check_ecu(TEST_ECU)
        self.assertEqual(messages, [])
        ecu = config.rte
        self.assertEqual([(t.name, [(r.runnable, r.offset_ms) for r in t.runnables or []]) for t in ecu.tasks],
                         [("fast", [("Ctrl.Run5ms", 0), ("Ctrl.Run50ms", 10)]), ("slow", [])])
        self.assertEqual(ecu.can.qualifiers["Cmd"].e2e.data_id_mode, "BOTH")        # case does not matter
        self.assertIsNone(ecu.can.qualifiers["Plain"])
        self.assertEqual(ecu.connections, {"Ctrl.In.In": "dio.in.KEY", "Ctrl.Out.Out": ["dio.out.LED_GREEN", "rte"]})
        self.assertEqual((ecu.uds.bus, ecu.can.bus), ("VEH", "DIAG"))
        # without project.rte there is nothing to check
        config, messages = self.check()
        self.assertEqual((config.rte, messages), (None, []))

    def test_schema_errors_with_file_line_and_path(self):
        def change(fn):
            ecu = copy.deepcopy(TEST_ECU)
            fn(ecu)
            return ecu

        cases = [
            (lambda e: e["tasks"][1].update(name="Slow_2"), "should match pattern", "tasks[1].name"),
            (lambda e: e["tasks"][1].update(name="idle"), "task name idle is reserved", "tasks[1].name"),
            (lambda e: e["tasks"][0].update(period_ms=0), "greater than 0", "tasks[0].period_ms"),
            (lambda e: e["tasks"][0].pop("stack_bytes"), "Field required", "tasks[0].stack_bytes"),
            (lambda e: e["tasks"][0]["runnables"][1].update(offset=10), "Extra inputs", "tasks[0].runnables[1].offset"),
            (lambda e: e["tasks"][0]["runnables"][1].update(offset_ms=-1), "greater than or equal to 0",
             "tasks[0].runnables[1].offset_ms"),
            (lambda e: e.update(base_period_ms=10), "not allowed together with tasks", "base_period_ms"),
            (lambda e: e["components"].append({"name": "Ctrl", "path": "x"}), "component Ctrl configured twice",
             "components"),
            # TOOL-59: `components: []` is an empty project (test_newproject.py); a list it stays
            (lambda e: e.update(components="none"), "valid list", "components"),
            (lambda e: e["can"].pop("node"), "Field required", "can.node"),
            (lambda e: e["can"].update(bus="CAN-1"), "should match pattern", "can.bus"),
            (lambda e: e["can"]["qualifiers"]["Cmd"].update(on_error="keep"), "'hold' or 'init'",
             "can.qualifiers.Cmd.on_error"),
            (lambda e: e["can"]["qualifiers"]["Cmd"]["e2e"].update(profile="P05"), "'P01', 'P02' or 'P11'",
             "can.qualifiers.Cmd.e2e.profile"),
            (lambda e: e["can"]["qualifiers"]["Cmd"]["e2e"].update(data_id="0x123"), "valid integer",
             "can.qualifiers.Cmd.e2e.data_id"),
            (lambda e: e["uds"].update(request_id=0x7E0), "Extra inputs", "uds.request_id"),
            (lambda e: e["connections"].update({"Ctrl.Out.Out": []}), "at least 1 item",
             "connections.Ctrl.Out.Out"),
            (lambda e: e["connections"].update({"Ctrl.In.In": 5}), "valid string", "connections.Ctrl.In.In"),
            (lambda e: e.update(ecu_state={"post_run_ms": 750}), "project.yaml modules.ecuState", "ecu_state"),
            (lambda e: e.update(xcp={"master_id": 1}), "Extra inputs", "xcp"),      # XCP is a project value
        ]
        for fn, fragment, path in cases:
            with self.subTest(path=path, fragment=fragment):
                config, messages = self.check_ecu(change(fn))
                self.assertIsNone(config)
                errors = [m for m in messages if m.severity == "error"]
                self.assertEqual(len(errors), 1, [str(m) for m in errors])
                m = errors[0]
                self.assertIn(fragment, m.text)
                self.assertEqual((m.file.name, m.yaml_path), ("rte.yaml", path))
                text = m.file.read_text(encoding="utf-8").splitlines()
                # line of the offending key; a missing key: the line of the mapping that lacks it
                key = path.rsplit(".", 1)[-1].split("[")[0]
                if fragment != "Field required":
                    self.assertIn(f"{key}:", text[m.line - 1])
                else:
                    self.assertNotIn(f"{key}:", text[m.line - 1])
                self.assertIn(f"rte.yaml:{m.line} ({path})", str(m))

    def test_all_findings_of_the_file_at_once(self):
        ecu = copy.deepcopy(TEST_ECU)
        ecu["tasks"][0]["priority"] = "high"
        ecu["can"]["dbc"] = ""
        ecu["uds"]["bus"] = None
        _, messages = self.check_ecu(ecu)
        self.assertEqual(findings(messages), [("error", "tasks[0].priority"), ("error", "can.dbc"),
                                              ("error", "uds.bus")])

    def test_unreadable_description(self):
        project = self.project()
        project["project"]["rte"] = "rte.yaml"
        path = self.write(project=project)
        (path.parent / "rte.yaml").write_text("tasks: [unclosed\n", encoding="utf-8")
        config, messages = nxconfig.check(path, TEST_MCUDB)
        self.assertIsNone(config)
        self.assertEqual((messages[0].file.name, messages[0].text[:12]), ("rte.yaml", "invalid YAML"))
        (path.parent / "rte.yaml").write_text("- components\n", encoding="utf-8")
        _, messages = nxconfig.check(path, TEST_MCUDB)
        self.assertIn("expected a mapping at the top level of rte.yaml", messages[0].text)
        # a missing file stays a finding of project.rte in project.yaml
        (path.parent / "rte.yaml").unlink()
        self.expect("not found", project=project, level="project", path="project.rte")

    def test_read_rte_on_its_own(self):
        ecu_file = self.tmp / "own.yaml"
        ecu_file.write_text(yaml.safe_dump({"schema_version": 2,      # RTE-22
                                            "components": [{"name": "A", "path": "a"}], "uds": {"bus": 3}}),
                            encoding="utf-8")
        ecu, messages, tree = nxconfig_config.read_rte(ecu_file)
        self.assertIsNone(ecu)
        self.assertEqual([(m.file, m.yaml_path, m.line) for m in messages], [(ecu_file, "uds.bus", 6)])
        self.assertEqual(list(tree), ["components", "schema_version", "uds"])   # safe_dump sorts the keys

    def test_repository_projects_fit_the_schema(self):
        for project in sorted((REPO / "projects").glob("*/project.yaml")):
            with self.subTest(project=project.parent.name):
                config, messages = nxconfig.check(project)
                self.assertIsNotNone(config, [str(m) for m in messages])
                self.assertEqual(config.rte is None, config.rte_file is None)
                self.assertFalse([m for m in messages if m.file is not None and m.file.name == "rte.yaml"])


class BoardCheckTest(Profiles):
    def test_board_errors(self):
        def pair(i, **kw):
            return lambda b: b["pwm"][0]["pairs"][i].update(kw)
        cases = [
            (lambda b: b["board"].update(package="LQFP100"), "has no package LQFP100", "board.package"),
            (lambda b: b["can"][0].update(instance="FlexCAN0"), "has no instance FlexCAN0", "can[0].instance"),
            (lambda b: b["uart"][0].update(instance="TIM2"), "TIM2 is a timer instance, expected uart",
             "uart[0].instance"),
            (lambda b: b["can"][1]["rx"].update(pin="PTC2"), "invalid pin name 'PTC2'", "can[1].rx.pin"),
            (lambda b: b["can"][1].update(phy="SBC"), "unknown device SBC", "can[1].phy"),
            (lambda b: b["spi"][0].update(device="FLASH"), "unknown device FLASH", "spi[0].device"),
            (lambda b: b["adc"][0].update(channel="SE0"), "unknown channel 'SE0' of ADC1", "adc[0].channel"),
            (lambda b: b["dio"]["outputs"][1].update(name="key"), "duplicate name key", "dio.outputs[1].name"),
            (lambda b: b["devices"][1].update(name="PHY_A"), "duplicate name PHY_A", "devices[1].name"),
            (lambda b: b["input_capture"][1].update(channel=4), "TIM2 has channels 0..3", "input_capture[1].channel"),
            (lambda b: b["pwm"][0]["channels"][1].update(channel=0), "channel 0 used twice",
             "pwm[0].channels[1].channel"),
            (lambda b: b["pwm"][0]["channels"][1].update(channel=7), "TIM1 has channels 0..3",
             "pwm[0].channels[1].channel"),
            (pair(0, high_side="NOPE"), "high_side NOPE is not in channels of BRIDGE", "pwm[0].pairs[0].high_side"),
            (pair(0, low_side="BRIDGE_A_HS"), "channel BRIDGE_A_HS is already used by pair BRIDGE_A",
             "pwm[0].pairs[0].low_side"),
            (lambda b: b["pwm"][0]["pairs"].append({"name": "BRIDGE", "high_side": "X", "low_side": "Y"}),
             "duplicate name BRIDGE", "pwm[0].pairs[1].name"),
            (lambda b: b["pwm"][0]["channels"].append({"name": "BRIDGE_B_HS", "channel": 2, "pin": "PB4"}),
             "channels BRIDGE_B_HS belong to no pair", "pwm[0].pairs"),
        ]
        for change, fragment, path in cases:
            with self.subTest(fragment=fragment):
                board = self.board()
                change(board)
                self.expect(fragment, board=board, level="board", path=path)

    def test_derivative_errors(self):
        mcudb = self.tmp / "mcudb"
        shutil.copytree(TEST_MCUDB, mcudb)
        file = mcudb / "testfam" / "TESTMCU64.yaml"
        doc = yaml.safe_load(file.read_text(encoding="utf-8"))
        doc["derivative"]["name"] = "OTHER"
        doc["instances"]["TIM2"].pop("channels")
        doc["instances"]["ADC1"]["channel_pattern"] = "IN("
        file.write_text(yaml.safe_dump(doc), encoding="utf-8")
        texts = [m.text for m in self.errors(mcudb=mcudb)]
        self.assertTrue(any("does not match the file name TESTMCU64.yaml" in t for t in texts), texts)
        self.assertTrue(any("timer instance needs channels" in t for t in texts), texts)
        self.assertTrue(any("invalid regular expression 'IN('" in t for t in texts), texts)


class ProjectCheckTest(Profiles):
    def test_project_errors(self):
        cases = [
            (lambda p: p["use"].update(can=["VEH", "BODY"]), "has no can entry BODY", "use.can[1]"),
            (lambda p: p["use"].update(dio=["KEY", "LED_BLUE"]), "has no dio entry LED_BLUE", "use.dio[1]"),
            (lambda p: p["pwm"].update(MOTOR={"frequency_hz": 1, "deadtime_ns": 1}), "has no PWM group MOTOR",
             "pwm.MOTOR"),
            (lambda p: p["adc_internal"][0].update(instance="ADC2"), "has no ADC instance ADC2",
             "adc_internal[0].instance"),
            (lambda p: p["adc_internal"][0].update(channel="BANDGAP"), "unknown internal channel 'BANDGAP'",
             "adc_internal[0].channel"),
            (lambda p: p["adc_internal"][0].update(name="VBAT"), "duplicate ADC channel name VBAT",
             "adc_internal[0].name"),
            (lambda p: p["use"].update(dio=["KEY", "LED_GREEN"]), "'LED_RED' must be a digital output used",
             "safety.safe_state_output"),
            (lambda p: p["safety"].update(locked_pin="KEY"), "'KEY' must be a digital output used",
             "safety.locked_pin"),
            (lambda p: p["safety"].update(test_pin="PZ1"), "invalid pin name 'PZ1'", "safety.test_pin"),
            (lambda p: p["project"].update(rte="rte.yaml"), "not found", "project.rte"),
        ]
        for change, fragment, path in cases:
            with self.subTest(fragment=fragment):
                project = self.project()
                change(project)
                self.expect(fragment, project=project, level="project", path=path)

    def test_duplicate_selection_is_a_warning(self):
        project = self.project()
        project["use"]["uart"] = ["CONSOLE", "CONSOLE"]
        config, messages = self.check(project=project)
        self.assertIsNotNone(config)
        self.assertEqual([(m.severity, m.yaml_path) for m in messages], [("warning", "use.uart[1]")])

    def test_adc_without_reference(self):
        board = self.board()
        del board["board"]["adc_reference_mv"]
        self.expect("defines no board.adc_reference_mv", board=board, level="project", path="use.adc")


class ResourceTest(Profiles):
    def test_timer_counter_pwm_vs_input_capture(self):
        project = self.project()
        project["use"]["input_capture"] = "all"   # SPEED is on TIM1 like the bridge PWM
        m = self.expect("TIM1 counter is used by input_capture SPEED and by pwm BRIDGE", project=project,
                        level="project", path="use.input_capture")
        self.assertIsNotNone(m.line)

    def test_input_captures_share_a_timer(self):
        config, messages = self.check()
        self.assertIsNotNone(config, [str(m) for m in messages])
        self.assertEqual({ic.instance for ic in config.selected("input_capture")}, {"TIM2"})

    def test_pwm_alone_or_capture_alone(self):
        project = self.project()
        project["use"]["input_capture"] = ["SPEED"]
        project["pwm"] = {}
        config, messages = self.check(project=project)
        self.assertIsNotNone(config, [str(m) for m in messages])

    def test_conflicts(self):
        cases = [
            # pin of a used digital output also used by a CAN bus
            (lambda b, p: b["dio"]["outputs"][0].update(pin="PA11"), "pin PA11 is used by dio LED_GREEN and by can VEH (rx)"),
            (lambda b, p: b["uart"].append({"name": "DEBUG", "instance": "USART2", "rx": {"pin": "PB7"},
                                            "tx": {"pin": "PB6"}}), "USART2 is used by uart DEBUG and by uart CONSOLE"),
            (lambda b, p: b["adc"][0].update(channel="IN2", pin="PA1") or p["use"].update(adc="all"),
             "ADC1 IN2 is used by adc TEMP_EXT and by adc VBAT"),
            (lambda b, p: b["input_capture"][2].update(channel=0, pin="PA15"),
             "TIM2 channel 0 is used by input_capture RC_IN2"),
            (lambda b, p: p["adc_internal"].append({"name": "VREF2", "instance": "ADC1", "channel": "VREFINT"}),
             "ADC1 VREFINT is used by adc_internal VREF2 and by adc_internal VREFINT"),
            (lambda b, p: p["safety"].update(test_pin="PC13"), "pin PC13 is used by safety test_pin and by dio KEY"),
            (lambda b, p: b["pwm"][0]["channels"][0].update(pin="PA10") or p["use"].update(input_capture=[]), None),
        ]
        for change, fragment in cases:
            with self.subTest(fragment=fragment):
                board, project = self.board(), self.project()
                change(board, project)
                if fragment is None:
                    config, messages = self.check(board, project)  # SPEED unused: its pin is free for the PWM
                    self.assertIsNotNone(config, [str(m) for m in messages])
                else:
                    self.expect(fragment, board=board, project=project, level="project")

    def test_unused_board_entries_do_not_conflict(self):
        board = self.board()
        board["dio"]["outputs"].append({"name": "ALT_USE", "pin": "PA11"})   # alternative use of the CAN pin
        project = self.project()
        project["use"]["dio"] = ["KEY", "LED_GREEN", "LED_RED"]
        config, messages = self.check(board, project)
        self.assertIsNotNone(config, [str(m) for m in messages])

    def test_demo_project_pilot_duty_conflicts_with_motor_pwm(self):
        doc = yaml.safe_load(DEMO_PROJECT.read_text(encoding="utf-8"))
        doc["use"]["input_capture"] = "all"
        doc["project"]["board"] = (DEMO_PROJECT.parent / doc["project"]["board"]).resolve().as_posix()   # PLAT-62
        doc["project"]["rte"] = str((DEMO_PROJECT.parent / "rte.yaml").as_posix())
        doc["project"]["diag"] = str((DEMO_PROJECT.parent / "diag.yaml").as_posix())
        path = self.tmp / "project.yaml"
        path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        _, messages = nxconfig.check(path)
        self.assertEqual([m.text for m in messages], ["FTM0 counter is used by input_capture PILOT_DUTY and by pwm MOT1"])


class PinmuxTest(Profiles):
    """Pin functions against the derivative's pin multiplexing (board package), without a generator run."""

    def derivative(self, change) -> Path:
        mcudb = self.tmp / "mcudb"
        if not mcudb.exists():
            shutil.copytree(TEST_MCUDB, mcudb)
        file = mcudb / "testfam" / "TESTMCU64.yaml"
        doc = yaml.safe_load(file.read_text(encoding="utf-8"))
        change(doc)
        file.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        return mcudb

    def test_function_errors(self):
        cases = [
            (lambda b, p: b["can"][0]["rx"].update(pin="PA10"),
             "TESTMCU64 QFP64 has no function FDCAN1_RX on PA10 (available: GPIO, TIM1_CH0, TIM1_CH3)",
             "board", "can[0].rx.pin"),
            (lambda b, p: b["spi"][0]["pcs"].update(index=1), "has no function SPI1_PCS1 on PA4", "board",
             "spi[0].pcs.pin"),
            (lambda b, p: b["uart"][0]["tx"].update(pin="PC0"), "TESTMCU64 QFP64 has no pin PC0", "board",
             "uart[0].tx.pin"),
            (lambda b, p: b["adc"][1].update(channel="IN3"), "has no function ADC1_IN3 on PA1", "board",
             "adc[1].pin"),
            (lambda b, p: b["pwm"][0]["channels"][1].update(pin="PB0"), "has no function TIM1_CH1 on PB0", "board",
             "pwm[0].channels[1].pin"),
            (lambda b, p: b["input_capture"][0].update(channel=2), "has no function TIM1_CH2 on PA10", "board",
             "input_capture[0].pin"),
            (lambda b, p: b["dio"]["outputs"][0].update(pin="PH3"), "TESTMCU64 QFP64 has no pin PH3", "board",
             "dio.outputs[0].pin"),
            (lambda b, p: p["safety"].update(test_pin="PD2"), "TESTMCU64 QFP64 has no pin PD2", "project",
             "safety.test_pin"),
        ]
        for change, fragment, level, path in cases:
            with self.subTest(fragment=fragment):
                board, project = self.board(), self.project()
                change(board, project)
                m = self.expect(fragment, board=board, project=project, level=level, path=path)
                self.assertIsNotNone(m.line)

    def test_unused_board_entries_are_checked_too(self):
        board = self.board()
        board["input_capture"][0]["pin"] = "PB0"   # SPEED is not used by the project
        self.expect("has no function TIM1_CH3 on PB0", board=board, level="board", path="input_capture[0].pin")

    def test_no_function_check_after_other_errors(self):
        board = self.board()
        board["can"][0]["instance"] = "FDCAN9"
        board["input_capture"][1]["channel"] = 4
        texts = [m.text for m in self.errors(board=board)]
        self.assertEqual(len(texts), 2, texts)   # instance and channel range, no follow-up function errors

    def test_package_without_data_is_a_warning(self):
        board = self.board()
        board["board"]["package"] = "QFP48"
        board["uart"][0]["tx"]["pin"] = "PC0"
        config, messages = self.check(board)
        self.assertIsNotNone(config, [str(m) for m in messages])
        self.assertEqual([(m.severity, m.yaml_path) for m in messages], [("warning", "board.package")])
        self.assertIn("no pin multiplexing data for package QFP48", messages[0].text)

    def test_partial_data_checks_nothing(self):
        mcudb = self.derivative(lambda d: d["pinmux"]["QFP64"].update(complete=False))
        board = self.board()
        board["can"][0]["rx"]["pin"] = "PA10"
        config, messages = self.check(board, mcudb=mcudb)
        self.assertIsNotNone(config, [str(m) for m in messages])
        self.assertEqual([m.severity for m in messages], ["warning"])
        self.assertIn("is partial", messages[0].text)

    def test_pinmux_file(self):
        def to_file(d):
            d["pinmux"]["QFP64"] = {"file": "TESTMCU64_QFP64.pinmux.yaml"}
        mcudb = self.derivative(to_file)
        table = {"schema_version": 1, "part": "TESTMCU64_QFP64", "selector": "AFR", "source": "synthetic",
                 "signals": yaml.safe_load((TEST_MCUDB / "testfam/TESTMCU64.yaml").read_text(
                     encoding="utf-8"))["pinmux"]["QFP64"]["signals"]}
        file = mcudb / "testfam" / "TESTMCU64_QFP64.pinmux.yaml"
        file.write_text(yaml.safe_dump(table, sort_keys=False), encoding="utf-8")
        config, messages = self.check(mcudb=mcudb)
        self.assertEqual(messages, [])
        self.assertEqual(nxconfig.checks.pinmux(config).selector("FDCAN2_RX", "PB12")["imcr"], 3)

        board = self.board()
        board["can"][0]["rx"]["pin"] = "PA10"
        self.assertIn("has no function FDCAN1_RX on PA10", self.errors(board, mcudb=mcudb)[0].text)

        table["signals"]["GPIO"]["PZ1"] = 0
        table["signals"]["TIM1_CH1"]["PA9"] = -1
        file.write_text(yaml.safe_dump(table, sort_keys=False), encoding="utf-8")
        errors = self.errors(mcudb=mcudb)
        self.assertEqual({(m.file.name, m.yaml_path) for m in errors},   # one message per selector type
                         {("TESTMCU64_QFP64.pinmux.yaml", "signals.TIM1_CH1.PA9")}, [str(m) for m in errors])
        self.assertIsNotNone(errors[0].line)
        table["signals"]["TIM1_CH1"]["PA9"] = 6
        file.write_text(yaml.safe_dump(table, sort_keys=False), encoding="utf-8")
        errors = self.errors(mcudb=mcudb)
        self.assertEqual([(m.file.name, m.yaml_path, m.text) for m in errors],
                         [("TESTMCU64_QFP64.pinmux.yaml", "signals.GPIO.PZ1", "invalid pin name 'PZ1' for TESTMCU64")])
        file.unlink()
        self.assertEqual([(m.yaml_path, m.text) for m in self.errors(mcudb=mcudb)],
                         [("pinmux.QFP64.file", "pin multiplexing file TESTMCU64_QFP64.pinmux.yaml not found")])


class DerivativeTest(Profiles):
    def test_derivative_level_checks(self):
        def change(d):
            d["memory"] += [{"name": "RAM", "kind": "ram", "start": 0x20008000, "size": 0x1000},
                            {"name": "DATA", "kind": "data_flash", "start": 0x08100000, "size": 0x1000},
                            {"name": "FLASH2", "kind": "code_flash", "start": 0x08200000, "size": 0x1800,
                             "sector_size": 0x1000}]
            d["pinmux"]["LQFP100"] = {"signals": {}}
            d["device_header"] = {"file": "testmcu.h", "describes": "TESTMCU99"}
            d["derivative"]["example"] = True
        mcudb = PinmuxTest.derivative(self, change)
        _, messages = self.check(mcudb=mcudb)
        found = {(m.severity, m.yaml_path, m.text) for m in messages}
        expected = {
            ("error", "memory[2].name", "duplicate memory region RAM"),
            ("error", "memory[2]", "RAM overlaps RAM"),
            ("error", "memory[3]", "data_flash region DATA needs sector_size"),
            ("error", "memory[4].size", "size 0x1800 of FLASH2 is no multiple of sector_size 0x1000"),
            ("error", "pinmux.LQFP100", "pinmux for unknown package LQFP100 (packages: QFP64, QFP48)"),
            ("warning", "derivative.example",
             "TESTMCU64 is a format example of the derivative database, not confirmed data"),
        }
        self.assertTrue(expected <= found, found - expected)
        self.assertTrue(any(m.yaml_path == "device_header.describes" and "TESTMCU99.yaml" in m.text
                            for m in messages), found)

    def test_device_header_superset(self):
        def change(d):
            d["device_header"] = {"file": "testmcu.h", "describes": "TESTMCU64"}
        mcudb = PinmuxTest.derivative(self, change)
        config, messages = self.check(mcudb=mcudb)
        self.assertEqual(messages, [])
        self.assertIs(nxconfig.checks.described_derivative(config), config.derivative)
        # a smaller "superset" derivative lacking instances of this one
        small = yaml.safe_load((mcudb / "testfam" / "TESTMCU64.yaml").read_text(encoding="utf-8"))
        small["derivative"]["name"] = "TESTMCU32"
        del small["instances"]["FDCAN2"], small["instances"]["USART3"]
        (mcudb / "testfam" / "TESTMCU32.yaml").write_text(yaml.safe_dump(small), encoding="utf-8")
        PinmuxTest.derivative(self, lambda d: d["device_header"].update(describes="TESTMCU32"))
        self.assertEqual([(m.yaml_path, m.text) for m in self.errors(mcudb=mcudb)],
                         [("device_header.describes", "device header of TESTMCU32 lacks instances FDCAN2, USART3")])

    def test_schema(self):
        cases = [
            (lambda d: d["pinmux"]["QFP64"].update(file="x.yaml"), "exactly one of file and signals", "pinmux.QFP64"),
            (lambda d: d["memory"][0].update(kind="rom"), "Input should be", "memory[0].kind"),
            (lambda d: d["memory"][0].update(size=0), "greater than 0", "memory[0].size"),
            (lambda d: d["instances"]["TIM1"].update(first_channel=-1), "greater than or equal to 0",
             "instances.TIM1.first_channel"),
        ]
        for change, fragment, path in cases:
            with self.subTest(fragment=fragment):
                shutil.rmtree(self.tmp / "mcudb", ignore_errors=True)
                mcudb = PinmuxTest.derivative(self, change)
                errors = self.errors(mcudb=mcudb)
                match = [m for m in errors if fragment in m.text]
                self.assertTrue(match, [str(m) for m in errors])
                self.assertEqual(match[0].yaml_path, path)
                self.assertEqual(match[0].file.name, "TESTMCU64.yaml")

    def test_core_and_fpu(self):
        """PLAT-09: core from the core table, FPU variant possible for it; findings with YAML path and line."""
        known = "cortex-m0plus, cortex-m4f, cortex-m7f, cortex-m33, cortex-r4f"
        cases = [
            (lambda d: d["derivative"].update(core="cortex-m3"),
             ("derivative.core", f"unknown core cortex-m3 (known: {known})")),
            (lambda d: d["derivative"].update(core="cortex-m7"),            # FPU encoded in the core name: m7f
             ("derivative.core", f"unknown core cortex-m7 (known: {known})")),
            (lambda d: d["derivative"].update(fpu="dp"),
             ("derivative.fpu", "core cortex-m33 has no FPU variant dp (possible: sp, none)")),
            (lambda d: d["derivative"].update(core="cortex-m0plus", fpu="sp"),
             ("derivative.fpu", "core cortex-m0plus has no FPU variant sp (possible: none)")),
            (lambda d: d["derivative"].pop("fpu"), ("derivative.fpu", "core cortex-m33 needs fpu (sp, none)")),
            (lambda d: d["derivative"].update(core="cortex-m7f", fpu=None),
             ("derivative.fpu", "core cortex-m7f needs fpu (sp, dp)")),
            (lambda d: d["derivative"].update(core="cortex-r4f", fpu="sp"),     # ARCH-23: VFPv3-D16 only
             ("derivative.fpu", "core cortex-r4f has no FPU variant sp (possible: dp)")),
        ]
        for change, expected in cases:
            with self.subTest(expected=expected):
                shutil.rmtree(self.tmp / "mcudb", ignore_errors=True)
                mcudb = PinmuxTest.derivative(self, change)
                errors = self.errors(mcudb=mcudb)
                self.assertEqual([(m.yaml_path, m.text) for m in errors], [expected])
                self.assertEqual(errors[0].file.name, "TESTMCU64.yaml")
                self.assertIsInstance(errors[0].line, int)
        # schema: only none, sp, dp
        shutil.rmtree(self.tmp / "mcudb")
        errors = self.errors(mcudb=PinmuxTest.derivative(self, lambda d: d["derivative"].update(fpu="half")))
        self.assertEqual([m.yaml_path for m in errors], ["derivative.fpu"])
        # the only variant of a core may be omitted, a matching one may be given
        for core, fpu in (("cortex-m4f", None), ("cortex-m4f", "sp"), ("cortex-m0plus", "none"),
                          ("cortex-m7f", "dp"), ("cortex-m33", "none"), ("cortex-r4f", None), ("cortex-r4f", "dp")):
            with self.subTest(core=core, fpu=fpu):
                shutil.rmtree(self.tmp / "mcudb")
                mcudb = PinmuxTest.derivative(self, lambda d: d["derivative"].update(core=core, fpu=fpu))
                self.assertEqual(self.errors(mcudb=mcudb), [])

    def test_nvic_priority_bits_and_features(self):
        """PLAT-10: NVIC priority bits possible for the core (ARMv6-M: 2), LMEM cache as derivative feature."""
        cases = [
            (lambda d: d["derivative"].update(nvic_priority_bits=2),
             ("derivative.nvic_priority_bits", "core cortex-m33 implements 3..8 NVIC priority bits, not 2")),
            (lambda d: d["derivative"].update(core="cortex-m0plus", fpu=None, nvic_priority_bits=4),
             ("derivative.nvic_priority_bits", "core cortex-m0plus implements 2 NVIC priority bits, not 4")),
        ]
        ranked = with_can_ranks(self.project())         # ARCH-33: with priority bits every used CAN bus needs one
        for change, expected in cases:
            with self.subTest(expected=expected):
                shutil.rmtree(self.tmp / "mcudb", ignore_errors=True)
                errors = self.errors(project=ranked, mcudb=PinmuxTest.derivative(self, change))
                self.assertEqual([(m.yaml_path, m.text) for m in errors], [expected])
                self.assertEqual(errors[0].file.name, "TESTMCU64.yaml")
                self.assertIsInstance(errors[0].line, int)
        # schema: 2..8 bits, known features only
        for change, path in ((lambda d: d["derivative"].update(nvic_priority_bits=9), "derivative.nvic_priority_bits"),
                             (lambda d: d["derivative"].update(nvic_priority_bits=1), "derivative.nvic_priority_bits"),
                             (lambda d: d["derivative"].update(features=["code_cache"]), "derivative.features[0]")):
            with self.subTest(path=path):
                shutil.rmtree(self.tmp / "mcudb", ignore_errors=True)
                errors = self.errors(mcudb=PinmuxTest.derivative(self, change))
                self.assertEqual([m.yaml_path for m in errors], [path])
        for core, fpu, bits in (("cortex-m0plus", None, 2), ("cortex-m4f", None, 4), ("cortex-m7f", "sp", 3),
                                ("cortex-m33", "sp", 8), ("cortex-m33", "sp", None)):
            with self.subTest(core=core, bits=bits):
                shutil.rmtree(self.tmp / "mcudb", ignore_errors=True)
                mcudb = PinmuxTest.derivative(self, lambda d: d["derivative"].update(
                    core=core, fpu=fpu, nvic_priority_bits=bits, features=["lmem_cache"]))
                self.assertEqual(self.errors(project=ranked if bits else None, mcudb=mcudb), [])
        from nxconfig import checks, config as cfgmod
        # ARCH-23: priority bits for exactly the NVIC cores
        self.assertEqual(set(checks.NVIC_PRIORITY_BITS),
                         {n for n, c in cfgmod.CORES.items() if c.interrupt_controller == "nvic"})
        self.assertIn("cortex-m4f", checks.NVIC_PRIORITY_BITS)
        self.assertNotIn("cortex-r4f", checks.NVIC_PRIORITY_BITS)

    def test_mpu_regions_of_the_derivative(self):
        """ARCH-16: number of MPU region descriptors as a derivative value; the entry is optional but typed."""
        for change, paths in ((lambda d: d.update(mpu={"regions": 0}), ["mpu.regions"]),
                              (lambda d: d.update(mpu={"regions": "eight"}), ["mpu.regions"]),
                              (lambda d: d.update(mpu={"region_count": 8}),
                               ["mpu.regions", "mpu.region_count"])):    # missing and unknown key both reported
            with self.subTest(paths=paths):
                shutil.rmtree(self.tmp / "mcudb", ignore_errors=True)
                errors = self.errors(mcudb=PinmuxTest.derivative(self, change))
                self.assertEqual([m.yaml_path for m in errors], paths)
                self.assertEqual(errors[0].file.name, "TESTMCU64.yaml")
        # a count with evidence passes, and so does a derivative without an MPU (bswgen then generates no macro)
        for mpu in ({"regions": 16, "source": "test data"}, None):
            with self.subTest(mpu=mpu):
                shutil.rmtree(self.tmp / "mcudb", ignore_errors=True)
                mcudb = PinmuxTest.derivative(self, lambda d: d.update(mpu=mpu) if mpu else d.pop("mpu", None))
                self.assertEqual(self.errors(mcudb=mcudb), [])

    def test_timer_channels_from_one(self):
        mcudb = PinmuxTest.derivative(self, lambda d: d["instances"]["TIM2"].update(first_channel=1))
        # RC_IN uses TIM2 channel 0: out of range; RC_IN2 channel 1 is the first channel
        errors = self.errors(mcudb=mcudb)
        self.assertEqual([(m.yaml_path, m.text) for m in errors],
                         [("input_capture[1].channel", "TIM2 has channels 1..4")])

    def test_repository_mcudb(self):
        """Every derivative in mcudb passes the derivative checks for every package with pin multiplexing data."""
        from types import SimpleNamespace

        from nxconfig import checks, config as cfgmod, model
        files = sorted(p for p in (REPO / "mcudb").glob("*/*.yaml") if not p.name.endswith(".pinmux.yaml"))
        expected = {"S32K142", "S32K144", "S32K146", "S32K148"}
        if (REPO / "mcudb" / "s32k3xx").is_dir():       # TOOL-244: private families, missing in the exported tree
            expected |= {"S32K344", "STM32G474RE"}
        self.assertGreaterEqual({p.stem for p in files}, expected)
        for file in files:
            rep = cfgmod.Reporter()
            d = cfgmod._read(rep, "derivative", file, model.Derivative)
            with self.subTest(derivative=file.stem):
                self.assertIsNotNone(d, [str(m) for m in rep.messages])
                self.assertTrue(d.memory, "memory regions")
                if cfgmod.CORES[d.derivative.core].interrupt_controller == "nvic":
                    self.assertIsNotNone(d.derivative.nvic_priority_bits, "nvic_priority_bits (PLAT-10)")
                else:                           # ARCH-23: a VIM derivative names its channels instead
                    self.assertIsNotNone(d.vim, "vim (ARCH-23)")
                pinned = set()                  # signal prefixes over all packages with complete tables
                for package in d.derivative.packages:
                    cfg = SimpleNamespace(derivative=d, derivative_file=file,
                                          board=SimpleNamespace(board=SimpleNamespace(package=package)))
                    rep = cfgmod.Reporter()
                    patterns = checks._derivative(cfg, rep)
                    errors = [str(m) for m in rep.messages if m.severity == "error"]
                    self.assertEqual(errors, [], f"{file.stem} {package}")
                    self.assertEqual(any("format example" in m.text for m in rep.messages), d.derivative.example)
                    mux = patterns.pinmux
                    if mux is None or not mux.complete:
                        continue
                    # absent instances have no signals; not every instance is bonded out in every package
                    # (S32K148 LQFP100 without QSPI), so presence is checked over all packages below
                    prefixes = {s.split("_")[0] for s in mux.signals}
                    pinned |= prefixes
                    described = checks.described_derivative(cfg)
                    absent = [n for n in described.instances if n not in d.instances and n in prefixes]
                    self.assertEqual(absent, [], f"{file.stem} {package}")
                if pinned:                      # instance names of the derivative match the signal names
                    # (the pins of a GPIO port, class gpio, have the family neutral function GPIO)
                    self.assertEqual([n for n, i in d.instances.items() if n not in pinned
                                      and not (i.class_ == "gpio" and "GPIO" in pinned)], [], file.stem)


class InterruptPriorityTest(Profiles):
    """PLAT-21: interrupt priorities as ranks against the NVIC priority bits of the derivative and the syscall level.

    The synthetic profile (testboard/testproject, Cortex-M33) gets the priority bits through a copy of the
    derivative, so the checks are not tested against the eval board only.
    """

    def mcudb(self, bits=4, core=None, fpu=None):
        def change(d):
            d["derivative"]["nvic_priority_bits"] = bits
            if core is not None:
                d["derivative"].update(core=core, fpu=fpu)
        shutil.rmtree(self.tmp / "mcudb", ignore_errors=True)
        return PinmuxTest.derivative(self, change)

    def project_with(self, ranks, max_syscall_level=5, complete=True):
        """complete: the used CAN buses without an entry in `ranks` get rank 0 behind them (ARCH-33)."""
        project = self.project()
        interrupts = {"ranks": copy.deepcopy(ranks)}
        if max_syscall_level is not None:
            interrupts["max_syscall_level"] = max_syscall_level
        project["interrupts"] = interrupts
        return with_can_ranks(project) if complete else project

    def test_used_can_bus_without_rank(self):
        """ARCH-33: a used CAN bus without rank is an error of nxconfig (was a bswgen warning), file/line/YAML path."""
        expected = ("CAN bus DIAG is used, but has no interrupt rank: the board port cannot set the level of its "
                    "message buffer interrupts (interrupts.ranks: {source: can, name: DIAG, rank: <n>})")
        project = self.project_with([{"source": "can", "name": "VEH", "rank": 0}], complete=False)
        errors = [m for m in self.messages(project) if m.severity == "error"]
        self.assertEqual([(m.yaml_path, m.text) for m in errors], [("interrupts.ranks", expected)])
        self.assertEqual(errors[0].text, checks.can_rank_missing("DIAG"))    # the text bswgen repeats
        self.assertEqual(errors[0].file.name, "project.yaml")
        self.assertIsInstance(errors[0].line, int)
        # without an interrupts section the finding points at the bus entry, one per bus, still with a line
        _, messages = self.check(project=self.project(), mcudb=self.mcudb())
        errors = [m for m in messages if m.severity == "error"]
        self.assertEqual([m.yaml_path for m in errors], ["can.VEH", "can.DIAG"])
        self.assertTrue(all(isinstance(m.line, int) and m.file.name == "project.yaml" for m in errors))
        self.assertIn("CAN bus VEH is used, but has no interrupt rank", errors[0].text)
        # a bus the project does not use needs no rank
        project["use"]["can"] = ["VEH"]
        self.assertEqual(self.messages(project), [])
        # a derivative without priority bits describes no ranks: nothing to demand (the profile without bits)
        self.assertEqual(self.errors(), [])

    def messages(self, project, mcudb=None):
        _, messages = self.check(project=project, mcudb=mcudb or self.mcudb())
        return messages

    def test_levels_of_the_ranks(self):
        project = self.project_with([{"source": "can", "name": "VEH", "rank": 0},
                                    {"source": "uart", "name": "CONSOLE", "rank": 3}])
        config, messages = self.check(project=project, mcudb=self.mcudb())
        self.assertEqual(messages, [])
        prio = checks.interrupt_priorities(config)
        self.assertEqual((prio.bits, prio.max_syscall_level, prio.rank_count), (4, 5, 11))
        self.assertEqual(prio.level("can", "VEH"), 5)
        self.assertEqual(prio.level("uart", "CONSOLE"), 8)
        # same project on a derivative with 3 priority bits: the levels shrink with the bits, no project change
        config, messages = self.check(project=self.project_with(project["interrupts"]["ranks"], 1),
                                      mcudb=self.mcudb(bits=3))
        self.assertEqual(messages, [])
        prio = checks.interrupt_priorities(config)
        self.assertEqual((prio.bits, prio.max_syscall_level, prio.rank_count), (3, 1, 7))
        self.assertEqual((prio.level("can", "VEH"), prio.level("uart", "CONSOLE")), (1, 4))

    def test_default_syscall_level_per_architecture(self):
        ranks = [{"source": "can", "name": "VEH", "rank": 0}]
        config, messages = self.check(project=self.project_with(ranks, None), mcudb=self.mcudb())
        self.assertEqual(messages, [])                                  # ARMv8-M default: level 1
        self.assertEqual(checks.interrupt_priorities(config).max_syscall_level, 1)
        config, messages = self.check(project=self.project_with(ranks, None),
                                      mcudb=self.mcudb(bits=2, core="cortex-m0plus", fpu=None))
        self.assertEqual(messages, [])                                  # ARMv6-M: no BASEPRI, level 0
        prio = checks.interrupt_priorities(config)
        self.assertEqual((prio.max_syscall_level, prio.rank_count, prio.level("can", "VEH")), (0, 4, 0))

    def test_rank_does_not_fit(self):
        cases = [
            # bits, core, fpu, syscall level, rank -> message
            (4, None, None, 5, 11, "rank 11 of can VEH does not fit: 4 NVIC priority bits of TESTMCU64 and "
                                   "max_syscall_level 5 leave ranks 0..10 (NVIC levels 5..15)"),
            (3, None, None, 2, 6, "leave ranks 0..5 (NVIC levels 2..7)"),
            (2, "cortex-m0plus", None, None, 4, "leave ranks 0..3 (NVIC levels 0..3)"),
        ]
        for bits, core, fpu, level, rank, expected in cases:
            with self.subTest(bits=bits, rank=rank):
                project = self.project_with([{"source": "can", "name": "VEH", "rank": rank}], level)
                errors = [m for m in self.messages(project, self.mcudb(bits, core, fpu)) if m.severity == "error"]
                self.assertEqual([m.yaml_path for m in errors], ["interrupts.ranks[0].rank"])
                self.assertIn(expected, errors[0].text)
                self.assertEqual(errors[0].file.name, "project.yaml")
                self.assertIsInstance(errors[0].line, int)
        # the last available rank still fits
        project = self.project_with([{"source": "can", "name": "VEH", "rank": 10}])
        self.assertEqual(self.messages(project), [])

    def test_max_syscall_level(self):
        cases = [
            (4, None, None, 0, "max_syscall_level 0 must be 1..15 on core cortex-m33 (4 NVIC priority bits of "
                               "TESTMCU64; 0 would switch the RTOS masking off)"),
            (4, None, None, 16, "max_syscall_level 16 must be 1..15 on core cortex-m33"),
            (2, "cortex-m0plus", None, 1, "core cortex-m0plus has no BASEPRI: the RTOS masks every interrupt "
                                          "(PRIMASK) and max_syscall_level must be 0, not 1"),
        ]
        for bits, core, fpu, level, expected in cases:
            with self.subTest(level=level, core=core):
                project = self.project_with([], level)
                errors = [m for m in self.messages(project, self.mcudb(bits, core, fpu)) if m.severity == "error"]
                self.assertEqual([m.yaml_path for m in errors], ["interrupts.max_syscall_level"])
                self.assertIn(expected, errors[0].text)
                self.assertEqual(errors[0].file.name, "project.yaml")
                self.assertIsInstance(errors[0].line, int)

    def test_unknown_and_unused_entries(self):
        project = self.project_with([{"source": "can", "name": "NOPE", "rank": 0}])
        errors = [m for m in self.messages(project) if m.severity == "error"]
        self.assertEqual([(m.yaml_path, m.text) for m in errors],
                         [("interrupts.ranks[0].name",
                           "board testboard has no can entry NOPE (available: VEH, DIAG)")])
        # the board has the PWM group, but the project uses no such entry: warning, the macro stays unused
        project = self.project_with([{"source": "adc", "name": "TEMP_EXT", "rank": 0}])
        messages = self.messages(project)
        self.assertEqual([(m.severity, m.yaml_path, m.text) for m in messages],
                         [("warning", "interrupts.ranks[0].name",
                           "rank for adc TEMP_EXT, which the project does not use (use.adc)")])

    def test_duplicate_rank(self):
        project = self.project_with([{"source": "can", "name": "VEH", "rank": 0},
                                    {"source": "uart", "name": "CONSOLE", "rank": 1},
                                    {"source": "can", "name": "VEH", "rank": 2}])
        errors = [m for m in self.messages(project) if m.severity == "error"]
        self.assertEqual([(m.yaml_path, m.text) for m in errors],
                         [("interrupts.ranks[2].name", "can VEH already has a rank (interrupts.ranks[0].name)")])

    def test_derivative_without_priority_bits(self):
        """A project with interrupt priorities needs the bits; the message names the derivative file."""
        project = self.project_with([{"source": "can", "name": "VEH", "rank": 0}])
        shutil.rmtree(self.tmp / "mcudb", ignore_errors=True)
        mcudb = PinmuxTest.derivative(self, lambda d: d["derivative"].pop("nvic_priority_bits", None))
        errors = [m for m in self.messages(project, mcudb) if m.severity == "error"]
        self.assertEqual([(m.yaml_path, m.text) for m in errors],
                         [("derivative.nvic_priority_bits",
                           "TESTMCU64 has no nvic_priority_bits, but project testproject describes interrupt "
                           "priorities (interrupts)")])
        self.assertEqual(errors[0].file.name, "TESTMCU64.yaml")
        # without interrupt priorities the derivative stays usable (nvic_priority_bits is optional)
        self.assertEqual(self.errors(mcudb=mcudb), [])

    def test_schema(self):
        for ranks, path in (([{"source": "timer", "name": "VEH", "rank": 0}], "interrupts.ranks[0].source"),
                            ([{"source": "can", "name": "VEH", "rank": -1}], "interrupts.ranks[0].rank"),
                            ([{"source": "can", "name": "VEH"}], "interrupts.ranks[0].rank"),
                            ([{"source": "can", "name": "VEH", "rank": 0, "level": 8}], "interrupts.ranks[0].level")):
            with self.subTest(path=path):
                errors = self.errors(project=self.project_with(ranks), mcudb=self.mcudb())
                self.assertEqual([m.yaml_path for m in errors], [path])

    def test_demo_project_keeps_the_levels_verified_at_the_board(self):
        """The eval board demo keeps CAN level 8 and UART-RX level 9 (IO-17) with ranks 3 and 4."""
        config = nxconfig.load(DEMO_PROJECT)
        prio = checks.interrupt_priorities(config)
        self.assertEqual((prio.bits, prio.max_syscall_level, prio.rank_count), (4, 5, 11))
        self.assertEqual(prio.level("can", "CAN0"), 8)
        self.assertEqual(prio.level("uart", "UART"), 9)
        # ARCH-07: every used CAN bus has a rank; COM-05: the diagnostic CAN CAN1 at the same level as CAN0
        self.assertEqual(prio.level("can", "CAN1"), 8)


R4F_BOARD = yaml.safe_load((HERE / "test/profiles/boards/testboard_r4f/board.yaml").read_text(encoding="utf-8"))
R4F_PROJECT = yaml.safe_load((HERE / "test/profiles/projects/testproject_r4f/project.yaml").read_text(encoding="utf-8"))


class VimInterruptTest(Profiles):
    """ARCH-23: core model with interrupt controller; on a VIM core (synthetic TESTR4F, Cortex-R4F) the ranks order
    the channels that are not fixed, there are no NVIC priority bits and no syscall level."""

    def r4f(self, project=None, board=None, derivative=None) -> tuple:
        mcudb = self.tmp / "mcudb"
        shutil.rmtree(mcudb, ignore_errors=True)
        shutil.copytree(TEST_MCUDB, mcudb)
        if derivative is not None:
            file = mcudb / "testfam" / "TESTR4F.yaml"
            doc = yaml.safe_load(file.read_text(encoding="utf-8"))
            derivative(doc)
            file.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        board_dir, project_dir = self.tmp / "boards" / "testboard_r4f", self.tmp / "projects" / "testproject_r4f"
        board_dir.mkdir(parents=True, exist_ok=True)
        project_dir.mkdir(parents=True, exist_ok=True)
        (board_dir / "board.yaml").write_text(yaml.safe_dump(board or R4F_BOARD, sort_keys=False), encoding="utf-8")
        path = project_dir / "project.yaml"
        path.write_text(yaml.safe_dump(project or R4F_PROJECT, sort_keys=False), encoding="utf-8")
        return nxconfig.check(path, mcudb)

    @staticmethod
    def project_with(ranks=None, **interrupts):
        project = copy.deepcopy(R4F_PROJECT)
        if ranks is not None:
            interrupts["ranks"] = ranks
        project["interrupts"] = {**project["interrupts"], **interrupts}
        return project

    def errors_of(self, messages):
        return [(m.yaml_path, m.text) for m in messages if m.severity == "error"]

    def test_core_table_controller_fields(self):
        cores = nxconfig_config.CORES
        self.assertEqual({n: c.interrupt_controller for n, c in cores.items()},
                         {"cortex-m0plus": "nvic", "cortex-m4f": "nvic", "cortex-m7f": "nvic", "cortex-m33": "nvic",
                          "cortex-r4f": "vim"})
        r4f = cores["cortex-r4f"]
        self.assertEqual((r4f.mcpu, r4f.fpus, r4f.architecture), ("cortex-r4f", {"dp": "vfpv3-d16"}, "armv7r"))
        self.assertEqual((r4f.nvic_priority_bits, r4f.max_syscall_default), (None, None))
        for name, core in cores.items():
            with self.subTest(core=name):
                nvic = core.interrupt_controller == "nvic"
                self.assertEqual(core.nvic_priority_bits is not None, nvic)
                self.assertEqual(core.max_syscall_default is not None, nvic)
        # NVIC facts only with an NVIC, and always with it
        Core = nxconfig_config.Core
        for args in (("x", {}, "armv7r", "vim", (3, 8), 1), ("x", {}, "armv7r", "vim", None, 0),
                     ("x", {}, "armv7m", "nvic"), ("x", {}, "armv7m", "nvic", (3, 8)), ("x", {}, "armv7m", "gic")):
            with self.subTest(args=args), self.assertRaises(ValueError):
                Core(*args)

    def test_profile_orders_the_free_channels(self):
        config, messages = self.r4f()
        self.assertEqual(messages, [])
        self.assertIsNone(checks.interrupt_priorities(config))        # no NVIC levels for a VIM core
        order = checks.vim_channel_order(config)
        self.assertEqual((order.channels, order.fixed), (48, (0, 1)))
        self.assertEqual(order.free_channels[:3], (2, 3, 4))
        self.assertEqual(len(order.free_channels), 46)
        # BUS1 has rank 0 although it is listed second
        self.assertEqual(order.order, (("can", "BUS1"), ("can", "BUS2")))
        self.assertEqual((order.position("can", "BUS1"), order.position("can", "BUS2")), (0, 1))
        self.assertEqual(order.ranks, {("can", "BUS1"): 0, ("can", "BUS2"): 1})
        # equal ranks keep the YAML order; fixed channels in the middle are skipped
        project = self.project_with([{"source": "can", "name": "BUS2", "rank": 3},
                                     {"source": "can", "name": "BUS1", "rank": 3}])
        config, messages = self.r4f(project, derivative=lambda d: d["vim"]["fixed"].append(
            {"channel": 3, "request": "SYNTH_MID", "kind": "irq"}))
        self.assertEqual(messages, [])
        order = checks.vim_channel_order(config)
        self.assertEqual(order.order, (("can", "BUS2"), ("can", "BUS1")))
        self.assertEqual((order.fixed, order.free_channels[:3]), ((0, 1, 3), (2, 4, 5)))
        # an NVIC core has no channel order
        config, _ = self.check()
        self.assertIsNone(checks.vim_channel_order(config))

    def test_no_syscall_level_on_a_vim(self):
        _, messages = self.r4f(self.project_with(max_syscall_level=0))
        errors = [m for m in messages if m.severity == "error"]
        self.assertEqual([m.yaml_path for m in errors], ["interrupts.max_syscall_level"])
        self.assertIn("core cortex-r4f has a VIM: an interrupt that may call the RTOS is an IRQ, an FIQ never does",
                      errors[0].text)
        self.assertEqual(errors[0].file.name, "project.yaml")
        self.assertIsInstance(errors[0].line, int)

    def test_ranks_that_get_no_channel(self):
        def two_channels_one_fixed(d):
            d["vim"].update(channels=2, fixed=[{"channel": 0, "request": "SYNTH_FIQ", "kind": "fiq"}])
        _, messages = self.r4f(derivative=two_channels_one_fixed)
        # BUS2 (rank 1, listed first) loses against BUS1 (rank 0)
        self.assertEqual(self.errors_of(messages),
                         [("interrupts.ranks[0].rank", "can BUS2 gets no channel: TESTR4F has 2 VIM channels, 1 of "
                                                       "them fixed, which leaves 1 for 2 ranked entries")])
        # rank values themselves are only an order: a large rank fits as long as there is a channel
        _, messages = self.r4f(self.project_with([{"source": "can", "name": "BUS1", "rank": 1000},
                                                  {"source": "can", "name": "BUS2", "rank": 2000}]))
        self.assertEqual(messages, [])
        # ARCH-33: on a VIM, too, every used CAN bus needs a rank (it orders the channel of its interrupts)
        _, messages = self.r4f(self.project_with([{"source": "can", "name": "BUS1", "rank": 1000}]))
        self.assertEqual(self.errors_of(messages), [("interrupts.ranks", checks.can_rank_missing("BUS2"))])

    def test_shared_rank_entry_checks(self):
        project = self.project_with([{"source": "can", "name": "BUS1", "rank": 0},
                                     {"source": "can", "name": "NOPE", "rank": 1},
                                     {"source": "can", "name": "BUS1", "rank": 2}])
        _, messages = self.r4f(project)
        # the misspelt entry leaves BUS2 without a rank (ARCH-33): both findings, the missing rank first
        self.assertEqual(self.errors_of(messages),
                         [("interrupts.ranks", checks.can_rank_missing("BUS2")),
                          ("interrupts.ranks[1].name", "board testboard_r4f has no can entry NOPE (available: BUS1, BUS2)"),
                          ("interrupts.ranks[2].name", "can BUS1 already has a rank (interrupts.ranks[0].name)")])

    def test_derivative_interrupt_facts_follow_the_controller(self):
        cases = [
            (lambda d: d["derivative"].update(nvic_priority_bits=4),
             [("derivative.nvic_priority_bits",
               "core cortex-r4f has a VIM, not an NVIC: the priority is the channel number, there are no NVIC "
               "priority bits (describe vim.channels instead)")]),
            (lambda d: d["vim"]["fixed"].append({"channel": 48, "request": "SYNTH_X", "kind": "irq"}),
             [("vim.fixed[2].channel", "channel 48 (SYNTH_X) does not exist: TESTR4F has VIM channels 0..47")]),
            (lambda d: d["vim"]["fixed"].append({"channel": 1, "request": "SYNTH_X", "kind": "irq"}),
             [("vim.fixed[2].channel", "channel 1 is already fixed (vim.fixed[1])")]),
            (lambda d: d["vim"].update(channels=2),
             [("vim.fixed", "all 2 VIM channels of TESTR4F are fixed, none is left for interrupt ranks"),
              ("interrupts.ranks[1].rank", "can BUS1 gets no channel: TESTR4F has 2 VIM channels, 2 of them fixed, "
                                           "which leaves 0 for 2 ranked entries"),
              ("interrupts.ranks[0].rank", "can BUS2 gets no channel: TESTR4F has 2 VIM channels, 2 of them fixed, "
                                           "which leaves 0 for 2 ranked entries")]),
            (lambda d: d.pop("vim"),
             [("vim", "TESTR4F has no vim (channel count, fixed channels), but project testproject_r4f describes "
                      "interrupt priorities (interrupts)")]),
        ]
        for change, expected in cases:
            with self.subTest(expected=expected[0]):
                _, messages = self.r4f(derivative=change)
                self.assertEqual(self.errors_of(messages), expected)
                errors = [m for m in messages if m.severity == "error"]
                self.assertEqual(errors[0].file.name, "TESTR4F.yaml")
        # the error lines point into the derivative where the key exists
        _, messages = self.r4f(derivative=cases[0][0])
        self.assertIsInstance(messages[0].line, int)
        # without interrupt ranks a VIM derivative without vim stays usable (like nvic_priority_bits)
        project = copy.deepcopy(R4F_PROJECT)
        del project["interrupts"]
        _, messages = self.r4f(project, derivative=lambda d: d.pop("vim"))
        self.assertEqual(messages, [])
        # a VIM on an NVIC core is an error in the derivative
        mcudb = PinmuxTest.derivative(self, lambda d: d.update(vim={"channels": 8}))
        errors = self.errors(mcudb=mcudb)
        self.assertEqual([(m.yaml_path, m.text) for m in errors],
                         [("vim", "core cortex-m33 has an NVIC, not a VIM (use nvic_priority_bits)")])
        self.assertEqual(errors[0].file.name, "TESTMCU64.yaml")
        self.assertIsInstance(errors[0].line, int)

    def test_vim_schema(self):
        for change, paths in ((lambda d: d["vim"].update(channels=1), ["vim.channels"]),
                              (lambda d: d["vim"]["fixed"][0].update(kind="nmi"), ["vim.fixed[0].kind"]),
                              (lambda d: d["vim"]["fixed"][0].pop("request"), ["vim.fixed[0].request"]),
                              (lambda d: d["vim"].update(priority_bits=4), ["vim.priority_bits"])):
            with self.subTest(paths=paths):
                _, messages = self.r4f(derivative=change)
                self.assertEqual([m.yaml_path for m in messages if m.severity == "error"], paths)


FAMILY_FACTS_OVERLAY = yaml.safe_load(
    (HERE / "test/profiles/derivatives/testr4f_family_facts.yaml").read_text(encoding="utf-8"))


class FamilyFactsTest(Profiles):
    """PLAT-69 (schema proposal of PLAT-36): derivative facts of the rm4x kind - clock domain per instance, GPIO
    ports as instances, output divider in stages, flash wait states, default VIM channel per request, header from
    a register description - on the synthetic TESTR4F with test/profiles/derivatives/testr4f_family_facts.yaml."""

    r4f = VimInterruptTest.r4f

    def facts(self, change=None):
        """Derivative callback for r4f(): the overlay laid over TESTR4F, then `change`."""
        def apply(doc):
            overlay = copy.deepcopy(FAMILY_FACTS_OVERLAY)
            doc["vim"].update(overlay.pop("vim"))
            doc["instances"].update(overlay.pop("instances"))
            doc.update(overlay)
            if change is not None:
                change(doc)
        return apply

    def test_profile_loads_without_messages(self):
        config, messages = self.r4f(derivative=self.facts())
        self.assertEqual([str(m) for m in messages], [])
        d = config.derivative
        self.assertEqual({n: i.clock.domain for n, i in d.instances.items()},
                         {"CAN1": "VCLKA1", "CAN2": "VCLKA1", "UART1": "VCLK", "GIOA": "VCLK", "GIOB": "VCLK"})
        self.assertEqual([(n, i.channels) for n, i in d.instances.items() if i.class_ == "gpio"],
                         [("GIOA", 8), ("GIOB", 8)])
        self.assertEqual(d.clock.pll.output_divider_stages, [[1, 2], [1, 3]])
        self.assertEqual([b.data for b in d.clock.flash_wait_states.bands], [0, 1, 2])
        self.assertEqual(d.vim.requests["CAN2_LEVEL0"], 7)
        self.assertEqual(d.device_header.generated_by, "regdesc2header")
        # the channel order of the ranks does not change with the requests (the generator moves them)
        order = checks.vim_channel_order(config)
        self.assertEqual((order.fixed, order.free_channels[:2]), ((0, 1), (2, 3)))

    def test_schema_errors_have_the_yaml_path(self):
        def clock(instance, **values):
            return lambda d: d["instances"][instance]["clock"].update(values)
        cases = [
            (clock("CAN1", select="bus"), "instances.CAN1.clock",
             "clock domain VCLKA1 has no clock select: give domain alone, without select"),
            (clock("UART1", max_hz=1000, divider=[1]), "instances.UART1.clock", "without divider, max_hz"),
            (lambda d: d["instances"]["UART1"]["clock"].pop("domain"), "instances.UART1.clock",
             "select (bus, core or none) or domain"),
            (lambda d: d["instances"]["GIOA"].update({"class": "gpio_port"}), "instances.GIOA.class",
             "'i2s' or 'gpio'"),
            (lambda d: d["clock"]["pll"].update(output_dividers=[1, 2, 3, 4, 5, 6]), "clock.pll",
             "every product of output_divider_stages in ascending order (5 values from 1 to 6)"),
            (lambda d: d["clock"]["pll"].update(output_divider_stages=[[1, 6]], output_dividers=[1, 2, 3, 4, 5, 6]),
             "clock.pll", "output_divider_stages needs at least two stages"),
            (lambda d: d["clock"]["pll"].update(output_divider_stages=[[2, 1], [1, 3]]), "clock.pll",
             "minimum above maximum"),
            (lambda d: d["clock"]["flash_wait_states"]["bands"][1].update(max_hz=50000000),
             "clock.flash_wait_states", "strictly ascending max_hz, got 50000000, 50000000, 160000000"),
            (lambda d: d["clock"]["flash_wait_states"].update(bands=[]), "clock.flash_wait_states.bands",
             "at least 1 item"),
            (lambda d: d["clock"]["flash_wait_states"]["bands"][0].update(data=-1),
             "clock.flash_wait_states.bands[0].data", "greater than or equal to 0"),
            (lambda d: d["vim"]["requests"].update(LATE=48), "vim", "requests.LATE: channel 48 does not exist "
                                                                    "(channels 0..47)"),
            (lambda d: d["vim"]["requests"].update(TWICE=5), "vim", "requests.TWICE: channel 5 is already the "
                                                                    "default channel of CAN1_LEVEL0"),
            (lambda d: d["device_header"].update(generated_by="halcogen"), "device_header.generated_by",
             "'regdesc2header'"),
        ]
        for change, path, fragment in cases:
            with self.subTest(path=path, fragment=fragment):
                _, messages = self.r4f(derivative=self.facts(change))
                errors = [m for m in messages if m.severity == "error"]
                self.assertEqual([m.yaml_path for m in errors], [path], [str(m) for m in errors])
                self.assertIn(fragment, errors[0].text)
                self.assertEqual(errors[0].file.name, "TESTR4F.yaml")

    def test_new_entries_are_facts_with_own_verify(self):
        """ARCH-37: the new entries record their reads like every derivative value; a nested entry (the wait state
        table in clock) is no value of its parent, a band (no Fact of its own) is part of the table's value."""
        model = nxconfig.model
        self.assertIn("domain", model.value_fields(model.InstanceClock))
        self.assertEqual(model.value_fields(model.FlashWaitStates), {"clock", "bands"})
        self.assertNotIn("flash_wait_states", model.value_fields(model.DerivativeClock))
        self.assertLessEqual({"output_divider_stages", "post_divider_input_max_hz"},
                             model.value_fields(model.PllLimits))
        self.assertIn("requests", model.value_fields(model.DerivativeVim))
        config, messages = self.r4f(derivative=self.facts(lambda d: d["clock"]["flash_wait_states"].update(
            verify=True)))
        # no check of the project reads the wait states yet: no warning
        self.assertEqual([str(m) for m in messages], [])
        use = model.DerivativeUse()
        with use:
            config.derivative.clock.flash_wait_states.bands[0].data
            config.derivative.instances["CAN1"].clock.domain
        self.assertEqual([list(e.fields) for e in use.entries.values()], [["bands"], ["domain"]])


RM46_PROFILE = HERE / "test/profiles/projects/testproject_rm46/project.yaml"
# TOOL-244: the RM46 test profiles are private (family rm4x not published); the export deselects Rm4xClockTest
# (tools/test/tests.yaml narrow), so loading them must not fail at import there
_RM46_BOARD_FILE = HERE / "test/profiles/boards/testboard_rm46/board.yaml"
RM46_BOARD = yaml.safe_load(_RM46_BOARD_FILE.read_text(encoding="utf-8")) if _RM46_BOARD_FILE.is_file() else None
RM46_PROJECT = yaml.safe_load(RM46_PROFILE.read_text(encoding="utf-8")) if RM46_PROFILE.is_file() else None
MHZ = 1_000_000


class Rm4xClockTest(Profiles):
    """PLAT-70: family clock model MODELS["rm4x"] on the real derivative RM46L852 (mcudb/rm4x) with the synthetic
    board/project testboard_rm46/testproject_rm46 (16 MHz crystal, 160 MHz system clock), and the relation checks of
    the rm4x-like derivative facts (clock domains, flash wait states, gpio ports, clock model inputs)."""

    def rm46(self, project=None, board=None, derivative=None) -> tuple:
        mcudb = REPO / "mcudb"
        if derivative is not None:
            mcudb = self.tmp / "mcudb"
            shutil.rmtree(mcudb, ignore_errors=True)
            shutil.copytree(REPO / "mcudb" / "rm4x", mcudb / "rm4x",
                            ignore=shutil.ignore_patterns("tools", "registers", "__pycache__"))
            file = mcudb / "rm4x" / "RM46L852.yaml"
            doc = yaml.safe_load(file.read_text(encoding="utf-8"))
            derivative(doc)
            file.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        board_dir, project_dir = self.tmp / "boards" / "testboard_rm46", self.tmp / "projects" / "testproject_rm46"
        board_dir.mkdir(parents=True, exist_ok=True)
        project_dir.mkdir(parents=True, exist_ok=True)
        (board_dir / "board.yaml").write_text(yaml.safe_dump(board or RM46_BOARD, sort_keys=False), encoding="utf-8")
        path = project_dir / "project.yaml"
        path.write_text(yaml.safe_dump(project or RM46_PROJECT, sort_keys=False), encoding="utf-8")
        return nxconfig.check(path, mcudb)

    @staticmethod
    def clock_only(system_hz: int) -> dict:
        """The profile without peripherals: only the clock tree is derived."""
        project = copy.deepcopy(RM46_PROJECT)
        for key in ("use", "can", "uart", "interrupts"):
            project.pop(key)
        project["clock"]["system_hz"] = system_hz
        return project

    @staticmethod
    def errors_of(messages):
        return [(m.yaml_path, m.text) for m in messages if m.severity == "error"]

    def test_profile_clock_plan(self):
        config, messages = nxconfig.check(RM46_PROFILE)        # the checked-in profile against the real mcudb
        self.assertEqual([str(m) for m in messages], [])
        plan = config.clock
        self.assertEqual((plan.family, plan.system_source, plan.system_hz), ("rm4x", "PLL1", 160 * MHZ))
        # 16 MHz / NR 1 * NF 30 = VCO 480 MHz, / (OD 3 * R 1); post-ODCLK 160 MHz <= 400 MHz
        pll = plan.pll
        self.assertEqual((pll.input_divider, pll.multiplier, pll.output_divider, pll.vco_hz), (1, 30, 3, 480 * MHZ))
        self.assertEqual(plan.pll_stages, (3, 1))
        self.assertEqual({d: int(f) // MHZ for d, f in plan.domains.items()},
                         {"core": 160, "GCLK": 160, "HCLK": 160, "VCLK": 80, "VCLK2": 80, "VCLK3": 80, "VCLK4": 80,
                          "VCLKA1": 80, "VCLKA3_S": 80, "VCLKA3_DIVR": 80, "VCLKA4_S": 80, "VCLKA4_DIVR": 80,
                          "RTICLK": 80})
        self.assertEqual((plan.dividers["VCLK"], plan.dividers["VCLK2"], plan.dividers["VCLKA1"],
                          plan.dividers["RTICLK"]), (2, 2, 1, 1))
        band = plan.flash_wait_states                         # 110 < 160 <= 165 MHz
        self.assertEqual((band.max_hz, band.data, band.address, band.pipeline), (165 * MHZ, 2, 0, True))
        self.assertEqual(plan.peripheral_source, {"DCAN1": "VCLKA1", "DCAN2": "VCLKA1", "LIN": "VCLK"})
        self.assertEqual({i: int(f) // MHZ for i, f in plan.peripheral_hz.items()}, {"DCAN1": 80, "DCAN2": 80, "LIN": 80})
        veh, diag = plan.can["VEH"], plan.can["DIAG"]
        self.assertEqual((veh.prescaler, veh.quanta, float(veh.sample_point)), (10, 16, 0.875))
        self.assertEqual((diag.prescaler, diag.quanta, diag.bitrate), (20, 16, 250_000))
        uart = plan.uart["CONSOLE"]                           # 80 MHz / (16 * 43) = 116279 Bd, +0.94 %
        self.assertEqual((uart.oversampling, uart.divider), (16, 43))
        self.assertEqual(plan.reset_core_hz, 16 * MHZ)        # GCLK from OSCIN after reset

    def test_other_system_clocks(self):
        cases = [
            # system clock: source, VCLK and VCLK4 in MHz, wait state band (data, address, pipeline)
            (180 * MHZ, "PLL1", 90, 90, (3, 1, True)),
            (220 * MHZ, "PLL1", 110, 110, (3, 1, True)),       # VCLK4 max 150 MHz: HCLK / 2
            (100 * MHZ, "PLL1", 100, 100, (1, 0, True)),
            (16 * MHZ, "OSCIN", 16, 16, (0, 0, False)),
            (9_600_000, "HFLPO", 9.6, 9.6, (0, 0, False)),
        ]
        for system_hz, source, vclk, vclk4, band in cases:
            with self.subTest(system_hz=system_hz):
                config, messages = self.rm46(self.clock_only(system_hz))
                self.assertEqual([str(m) for m in messages], [])
                plan = config.clock
                self.assertEqual(plan.system_source, source)
                self.assertEqual((float(plan.domains["VCLK"] / MHZ), float(plan.domains["VCLK4"] / MHZ)), (vclk, vclk4))
                self.assertLessEqual(plan.domains["VCLK"], 110 * MHZ)
                self.assertEqual(plan.domains["VCLK2"] % plan.domains["VCLK"], 0)
                fws = plan.flash_wait_states
                self.assertEqual((fws.data, fws.address, fws.pipeline), band)
                self.assertEqual(plan.pll is not None, source == "PLL1")
                if plan.pll is not None:
                    self.assertLessEqual(plan.pll.vco_hz / plan.pll_stages[0], 400 * MHZ)       # post-ODCLK
                    self.assertEqual(plan.pll_stages[0] * plan.pll_stages[1], plan.pll.output_divider)

    def test_stage_split(self):
        from nxconfig import clock
        pll = nxconfig.load(RM46_PROFILE).derivative.clock.pll
        vco = 544 * MHZ
        self.assertEqual(clock._stage_split(vco, 2, pll), (2, 1))
        self.assertIsNone(clock._stage_split(vco, 1, pll))            # post-ODCLK 544 MHz > 400 MHz
        self.assertEqual(clock._stage_split(vco, 64, pll), (2, 32))
        self.assertEqual(clock._stage_split(vco, 256, pll), (8, 32))
        self.assertIsNone(clock._stage_split(vco, 37, pll))           # 37 = 1 * 37 only, R ends at 32

    def test_findings_have_the_yaml_path(self):
        def project(**changes):
            p = copy.deepcopy(RM46_PROJECT)
            for key, value in changes.items():
                p[key] = value
            return p

        def clock(**values):
            return lambda d: d["clock"].update(values)

        def entry(*keys, **values):
            def change(d):
                node = d
                for key in keys:
                    node = node[key]
                node.update(values)
            return change

        no_osc = copy.deepcopy(RM46_BOARD)
        del no_osc["board"]["clock"]
        cycle = [entry("clock", "domain_dividers", "VCLK", parent="VCLK2"),
                 entry("clock", "domain_dividers", "VCLK2", parent="VCLK", multiple_of=None)]
        cases = [
            # (project, board, derivative change) -> file, [paths of all errors], fragment of the first error
            ((project(clock={"system_hz": 230 * MHZ}), None, None), "project.yaml", ["clock.system_hz"],
             "system clock 230 MHz exceeds the maximum GCLK of RM46L852 (220 MHz, clock.max_hz.GCLK in "
             "RM46L852.yaml)"),
            ((project(clock={"system_hz": 100_000_001}), None, None), "project.yaml", ["clock.system_hz"],
             "cannot be derived exactly from the oscillator 16 MHz with PLL1 of RM46L852"),
            ((None, no_osc, None), "project.yaml", ["clock.system_hz"],
             "board testboard_rm46 has no oscillator (board.clock.sosc_hz) for the PLL"),
            ((project(can={"VEH": {"bitrate": 1_000_003}, "DIAG": {"bitrate": 250_000}}), None, None),
             "project.yaml", ["can.VEH.bitrate"], "CAN VEH: 1000003 bit/s cannot be derived exactly from 80000000 Hz"),
            ((project(peripheral_clocks={"DCAN1": {"select": "SOSC"}}), None, None), "project.yaml",
             ["peripheral_clocks.DCAN1"], "DCAN1 has no clock select of its own: it runs on the clock domain VCLKA1 "
                                          "(instances.DCAN1.clock.domain in RM46L852.yaml)"),
            ((None, None, entry("clock", "max_hz", VCLK=5 * MHZ)), "project.yaml", ["clock.system_hz"],
             "VCLK cannot be divided from HCLK 160 MHz to at most 5 MHz (clock.max_hz.VCLK, dividers 1..16"),
            ((None, None, entry("clock", "max_hz", VCLK2=60 * MHZ)), "project.yaml", ["clock.system_hz"],
             "VCLK2 cannot be divided from HCLK 160 MHz to at most 60 MHz and to an integer multiple of VCLK (80 MHz)"),
            ((None, None, entry("clock", "oscillator_monitor", factor=1)), "board.yaml", ["board.clock.sosc_hz"],
             "outside the window of the clock monitor of RM46L852 (HFLPO 9.6 MHz / 1 < f < HFLPO * 1"),
            ((None, None, lambda d: d["clock"]["flash_wait_states"]["bands"][3].update(max_hz=200 * MHZ)),
             "RM46L852.yaml", ["clock.flash_wait_states.bands[3].max_hz"],
             "the last band ends at 200 MHz, below the maximum HCLK of RM46L852 (220 MHz, clock.max_hz.HCLK)"),
            ((None, None, entry("clock", "flash_wait_states", clock="FCLK")), "RM46L852.yaml",
             ["clock.flash_wait_states.clock"], "flash clock FCLK is no key of clock.max_hz"),
            ((None, None, entry("instances", "DCAN1", "clock", domain="VCLKA9")), "RM46L852.yaml",
             ["instances.DCAN1.clock.domain"], "clock domain VCLKA9 is no key of clock.max_hz (available: GCLK, "),
            ((None, None, lambda d: d["instances"]["GIOA"].pop("channels")), "RM46L852.yaml", ["instances.GIOA"],
             "gpio instance needs channels"),
            ((None, None, lambda d: d["clock"]["domain_dividers"].pop("VCLKA1")), "RM46L852.yaml",
             ["instances.DCAN1.clock.domain", "instances.DCAN2.clock.domain"],
             "RM46L852: DCAN1 (used by can VEH) runs on VCLKA1, which the rm4x clock model does not set up"),
            ((None, None, lambda d: [d["clock"].pop(k) for k in ("source_numbers", "external_max_hz")]), "RM46L852.yaml",
             ["clock"],
             "the rm4x clock model needs the numbers of the clock sources (clock.source_numbers)"),
            ((None, None, entry("clock", "source_numbers", "numbers", HFLPO=1)), "RM46L852.yaml",
             ["clock.source_numbers"], "numbers.HFLPO: number 1 is already the number of PLL1"),
            ((None, None, entry("clock", "external_max_hz", "max_hz", EXTCLKIN9=MHZ)), "RM46L852.yaml",
             ["clock.external_max_hz.max_hz.EXTCLKIN9"], "EXTCLKIN9 is no clock source of clock.source_numbers"),
            ((None, None, entry("clock", "oscillator_monitor", reference="XLPO")), "RM46L852.yaml",
             ["clock.oscillator_monitor.reference"], "XLPO is no internal clock of clock.internal"),
            ((None, None, entry("clock", "domain_dividers", "VCLK", parent="HCLKX")), "RM46L852.yaml",
             ["clock.domain_dividers.VCLK.parent"], "HCLKX is no clock of RM46L852"),
            ((None, None, lambda d: [change(d) for change in cycle]), "RM46L852.yaml",
             ["clock.domain_dividers.VCLK.parent", "clock.domain_dividers.VCLK2.parent"],
             "the parents of VCLK form a cycle (VCLK -> VCLK2 -> VCLK)"),
            ((None, None, entry("clock", "domain_dividers", "RTICLK", "other_source", max_of="VCLK9")),
             "RM46L852.yaml", ["clock.domain_dividers.RTICLK.other_source.max_of"], "VCLK9 is no clock of RM46L852"),
            ((None, None, entry("clock", "domain_dividers", "VCLK", dividers=[2, 1])), "RM46L852.yaml",
             ["clock.domain_dividers.VCLK"], "dividers need strictly ascending values, got 2, 1"),
        ]
        for (proj, board, change), file, paths, fragment in cases:
            with self.subTest(fragment=fragment):
                _, messages = self.rm46(proj, board, change)
                errors = [m for m in messages if m.severity == "error"]
                self.assertEqual([m.yaml_path for m in errors], paths, [str(m) for m in errors])
                self.assertIn(fragment, errors[0].text)
                self.assertEqual(errors[0].file.name, file)
                self.assertIsInstance(errors[0].line, int)

    def test_unconfirmed_clock_facts_the_plan_reads(self):
        """ARCH-37: the model reads the domain of the used instances, the dividers of every domain and the wait
        states; GIOA/GIOB (verify: true, domain not in the data manual) stay silent while no project uses them."""
        def mark(d):
            d["instances"]["DCAN1"]["clock"]["verify"] = True
            d["clock"]["domain_dividers"]["VCLKA1"]["verify"] = True
            d["clock"]["flash_wait_states"]["verify"] = True
            d["instances"]["MIBSPI1"]["clock"]["verify"] = True          # not used by the project
        _, messages = self.rm46(derivative=mark)
        warnings = [(m.yaml_path, m.text) for m in messages if m.severity == "warning"]
        self.assertEqual([p for p, _ in warnings], ["clock.flash_wait_states.verify",
                                                    "clock.domain_dividers.VCLKA1.verify",
                                                    "instances.DCAN1.clock.verify"])
        self.assertIn("(clock, bands)", warnings[0][1])
        self.assertIn("the clock plan", warnings[2][1])
        self.assertEqual(self.errors_of(messages), [])


class CommandLineTest(Profiles):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(HERE), *args], capture_output=True, text=True,
                              encoding="utf-8", env={**__import__("os").environ, "PYTHONUTF8": "1"})

    def test_ok_and_error(self):
        r = self.run_cli("check", str(DEMO_PROJECT))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("testproject_full: ok", r.stdout)
        project = self.project()
        project["use"]["can"] = ["BODY"]
        path = self.write(project=project)
        r = self.run_cli("check", str(path), "--json")
        self.assertEqual(r.returncode, 1)
        messages = json.loads(r.stdout)
        # the synthetic derivative is not in the repository mcudb: the board's mcu is reported first
        self.assertEqual(messages[0]["path"], "board.mcu")
        self.assertIsInstance(messages[0]["line"], int)


SWITCH_BOARD = yaml.safe_load((HERE / "test/profiles/boards/testboard_switches/board.yaml").read_text(encoding="utf-8"))
SWITCH_PROJECT = yaml.safe_load(
    (HERE / "test/profiles/projects/testproject_switches/project.yaml").read_text(encoding="utf-8"))


class ModuleCheckTest(Profiles):
    """Module checks in nxconfig (ARCH-12): assignment, connect, parameters, manifests.

    Synthetic profile testboard_switches/testproject_switches (two BTS5030 switches on TESTMCU64) with the real
    manifest libs/bsw/highSideSwitch/module.yaml, plus manifests written by the tests.
    """

    def write(self, board=None, project=None) -> Path:
        project = copy.deepcopy(SWITCH_PROJECT if project is None else project)
        project["project"]["board"] = "../../boards/testboard"          # directory written by Profiles.write
        return super().write(SWITCH_BOARD if board is None else board, project)

    @staticmethod
    def board():
        return copy.deepcopy(SWITCH_BOARD)

    @staticmethod
    def project():
        return copy.deepcopy(SWITCH_PROJECT)

    def line_text(self, message) -> str:
        return message.file.read_text(encoding="utf-8").splitlines()[message.line - 1]

    def test_profile(self):
        config, messages = self.check()
        self.assertEqual(messages, [])
        integration = config.integration
        self.assertEqual([(d.name, d.module) for d in integration.devices],
                         [("LAMP_SW", "highSideSwitch"), ("PUMP_SW", "highSideSwitch")])
        lamp, pump = integration.devices
        self.assertEqual(lamp.parameters["cycle_ms"], 10)               # module default
        self.assertEqual(pump.parameters["cycle_ms"], 5)
        self.assertEqual(pump.connect, {"is": "PUMP_IS", "in": "PUMP_IN", "den": "PUMP_DEN"})
        self.assertEqual((lamp.verify, pump.verify), (False, True))
        self.assertNotIn("verify", pump.parameters)

    def test_device_without_module_entry_is_not_used(self):
        project = self.project()
        del project["modules"]["LAMP_SW"]
        project["use"]["dio"] = ["PUMP_IN", "PUMP_DEN"]                  # LAMP_* signals unused: no complaint
        config, messages = self.check(project=project)
        self.assertEqual(messages, [])
        self.assertEqual([d.name for d in config.integration.devices], ["PUMP_SW"])

    def test_board_connect_errors(self):
        cases = [
            (lambda b: b["devices"][0]["connect"].update({"is": "KEY"}), "devices[0].connect.is",
             "connection is of module highSideSwitch needs a analog_input (board adc), KEY is a digital_input"),
            (lambda b: b["devices"][0]["connect"].update({"in": "LAMP_IS"}), "devices[0].connect.in",
             "needs a digital_output (board dio.outputs), LAMP_IS is a analog_input"),
            (lambda b: b["devices"][0]["connect"].update({"in": "NOPE"}), "devices[0].connect.in",
             "NOPE is no board entry"),
            (lambda b: b["devices"][0]["connect"].update({"foo": "KEY"}), "devices[0].connect.foo",
             "module highSideSwitch has no connection foo (connections: in, den, is)"),
            (lambda b: b["devices"][1]["connect"].pop("den"), "devices[1].connect",
             "device PUMP_SW (highSideSwitch) needs connect: den"),
            (lambda b: b["devices"][1]["connect"].update({"in": "LAMP_IN"}), "devices[1].connect.in",
             "LAMP_IN is already connected to LAMP_SW (in)"),
            (lambda b: b["devices"][1].update({"module": "nope"}), "devices[1].module",
             "unknown module nope (device drivers:"),
            (lambda b: b["devices"][1].update({"module": "canSystem"}), "devices[1].module",
             "module canSystem is a service, not a device driver"),
            (lambda b: b["devices"][1].update({"part": "VN7003"}), "devices[1].part",
             "part VN7003 of device PUMP_SW is not served by module highSideSwitch (parts: BTS5030-1EJA)"),
            (lambda b: b["devices"][2].update({"connect": {"in": "KEY"}}), "devices[2].connect",
             "device EEPROM has no module (no module.yaml lists part 25LC320), connect needs one"),
        ]
        for change, path, fragment in cases:
            with self.subTest(path=path, fragment=fragment):
                board = self.board()
                change(board)
                m = self.expect(fragment, board=board, level="board", path=path)
                self.assertIsNotNone(m.line)
                key = path.rsplit(".", 1)[-1]
                self.assertIn(key.split("[")[0], self.line_text(m))

    def test_unused_device_connections_are_checked_too(self):
        board = self.board()
        board["devices"][0]["connect"]["is"] = "KEY"
        project = self.project()
        del project["modules"]["LAMP_SW"]
        self.expect("KEY is a digital_input", board=board, project=project, path="devices[0].connect.is")

    def test_parameter_errors(self):
        def lamp(**values):
            def change(p):
                p["modules"]["LAMP_SW"].update(values)
            return change

        cases = [
            (lamp(overcurrent_ma="fast"), "modules.LAMP_SW.overcurrent_ma", "expected an integer, got 'fast'"),
            (lamp(overcurrent_ma=True), "modules.LAMP_SW.overcurrent_ma", "expected an integer, got True"),
            (lamp(console=5), "modules.LAMP_SW.console", "expected a string, got 5"),
            (lamp(cycle_ms=0), "modules.LAMP_SW.cycle_ms", "cycle_ms: 0 is out of range 1..1000 ms"),
            (lamp(fault_ma=1000001), "modules.LAMP_SW.fault_ma", "out of range 1..1000000 mA"),
            (lamp(console="Lamp"), "modules.LAMP_SW.console", "'Lamp' does not match"),
            (lamp(colour=1), "modules.LAMP_SW.colour", "module highSideSwitch has no parameter colour (parameters: "
                                                         "cycle_ms, overcurrent_ma"),
            (lamp(verify="yes"), "modules.LAMP_SW.verify", "verify must be true or false"),
            (lambda p: p["modules"]["LAMP_SW"].pop("fault_ma"), "modules.LAMP_SW",
             "missing parameter fault_ma of module highSideSwitch"),
            (lamp(fault_ma=1500), "modules.LAMP_SW.fault_ma",
             "constraint of module highSideSwitch not met: fault_ma > overcurrent_ma (fault_ma 1500, overcurrent_ma 1500)"),
            # right operand from the module default (cycle_ms 10): the message points to the given value
            (lamp(overcurrent_debounce_ms=5), "modules.LAMP_SW.overcurrent_debounce_ms",
             "overcurrent_debounce_ms >= cycle_ms (overcurrent_debounce_ms 5, cycle_ms 10)"),
            (lamp(console="pump_2"), "modules.PUMP_SW.console", "console pump_2 is already used by device LAMP_SW"),
            (lamp(console="sbc"), "modules.LAMP_SW.console", "console sbc is already used by module sbcUja113x"),
            (lambda p: p["modules"].update({"NOPE": {}}), "modules.NOPE",
             "board testboard_switches has no device NOPE (devices with a module: LAMP_SW, PUMP_SW)"),
            (lambda p: p["modules"].update({"EEPROM": {}}), "modules.EEPROM", "device EEPROM (25LC320) has no module"),
        ]
        for change, path, fragment in cases:
            with self.subTest(path=path, fragment=fragment):
                project = self.project()
                change(project)
                config, messages = self.check(project=project)
                self.assertIsNone(config)
                match = [m for m in messages if fragment in m.text]
                self.assertTrue(match, f"'{fragment}' not in {[str(m) for m in messages]}")
                self.assertEqual((match[0].file.name, match[0].yaml_path, match[0].severity),
                                 ("project.yaml", path, "error"))
                self.assertIsNotNone(match[0].line)
                self.assertIn(path.split(".")[-1], self.line_text(match[0]))

    def test_connected_signal_not_used_is_a_warning(self):
        cases = [
            ({"dio": ["LAMP_IN", "LAMP_DEN", "PUMP_DEN"]}, "modules.PUMP_SW", ["LAMP_SW"],
             "device PUMP_SW (highSideSwitch) is connected to PUMP_IN (in), but the project does not use it (use.dio): "
             "PUMP_SW is not instantiated"),
            ({"adc": ["PUMP_IS"]}, "modules.LAMP_SW", ["PUMP_SW"],
             "is connected to LAMP_IS (is), but the project does not use it (use.adc)"),
        ]
        for use, path, remaining, fragment in cases:
            with self.subTest(path=path):
                project = self.project()
                project["use"].update(use)
                config, messages = self.check(project=project)
                (warning,) = messages
                self.assertEqual((warning.severity, warning.yaml_path), ("warning", path))
                self.assertIn(fragment, warning.text)
                self.assertEqual([d.name for d in config.integration.devices], remaining)

    def test_all_parameter_errors_at_once(self):
        project = self.project()
        project["modules"]["LAMP_SW"].update(cycle_ms=0, fault_ma="x", foo=1)
        del project["modules"]["PUMP_SW"]["console"]
        errors = self.errors(project=project)
        self.assertEqual(sorted(m.yaml_path for m in errors),
                         ["modules.LAMP_SW.cycle_ms", "modules.LAMP_SW.fault_ma", "modules.LAMP_SW.foo",
                          "modules.PUMP_SW"])

    # ---- IO-21: current levels in mA against the characteristic of the IS channel (sensed.py)
    def test_sensed_levels_of_the_profile(self):
        config, messages = self.check()
        self.assertEqual(messages, [])
        levels = {(lv.device, lv.parameter): lv for lv in config.sensed_levels}
        self.assertEqual(sorted(levels), sorted((d, p) for d in ("LAMP_SW", "PUMP_SW")
                                                for p in ("overcurrent_ma", "fault_ma", "open_load_ma", "off_max_ma")))
        # LAMP_IS: 1500 * U / 1000 ohm, U = raw * 3300 / 4095 -> 1500 mA first reached at raw 1241 (1500.0 mA ->
        # 1500), raw 1240 gives 1498.9 -> 1499
        lamp = levels["LAMP_SW", "overcurrent_ma"]
        self.assertEqual((lamp.channel, lamp.first_raw, lamp.last_raw, lamp.first_value, lamp.first_mv),
                         ("LAMP_IS", 1241, 4095, 1500, 1000))
        # open load 0 mA: every code reaches it, the warning never comes (level 0 = off)
        self.assertEqual(levels["PUMP_SW", "open_load_ma"].first_raw, 0)
        lines = sensed.info(config)
        self.assertIn("LAMP_SW (highSideSwitch) overcurrent_ma 1500 mA: channel LAMP_IS value >= level at raw "
                      "1241..4095 (raw 1241 = 1500 mA, 1000 mV at the pin; 1.2 mA per code)", lines)
        self.assertEqual(len(lines), 8)

    def test_sensed_level_at_the_end_of_the_range(self):
        # 4950 mA is exactly full scale of LAMP_IS: reachable (by raw 4095 only); one more is never reached
        project = self.project()
        project["modules"]["LAMP_SW"].update(fault_ma=4950)
        config, messages = self.check(project=project)
        self.assertEqual(messages, [])
        fault = next(lv for lv in config.sensed_levels if (lv.device, lv.parameter) == ("LAMP_SW", "fault_ma"))
        self.assertEqual((fault.first_raw, fault.last_raw, fault.first_value), (4095, 4095, 4950))
        project["modules"]["LAMP_SW"].update(fault_ma=4951)
        m = self.expect("fault_ma 4951 mA is outside the range the channel LAMP_IS of connection is can report "
                        "(0..4950 mA over raw 0..4095, board adc[0]): the level is never reached",
                        project=project, level="project", path="modules.LAMP_SW.fault_ma")
        self.assertIn("fault_ma", self.line_text(m))

    def test_sensed_levels_need_the_board_facts(self):
        board = self.board()
        del board["adc"][0]["sensor"]                   # LAMP_IS without characteristic: pin voltage in mV
        m = self.expect("ADC LAMP_IS: device LAMP_SW (highSideSwitch) compares overcurrent_ma, fault_ma, open_load_ma, "
                        "off_max_ma (mA) with connection is, but the channel has no sensor block (its value is the pin "
                        "voltage in mV): the board facts that convert it into mA are missing",
                        board=board, level="board", path="adc[0]")
        self.assertIn("LAMP_IS", self.line_text(m))
        board = self.board()
        board["adc"][1]["sensor"] = {"kind": "divider", "r_top_ohm": 10000, "r_bottom_ohm": 10000, "source": "test"}
        self.expect("ADC PUMP_IS: device PUMP_SW (highSideSwitch) compares overcurrent_ma, fault_ma, open_load_ma, "
                    "off_max_ma (mA) with connection is, but the channel's sensor (divider) delivers mV, not mA",
                    board=board, level="board", path="adc[1].sensor")
        board = self.board()
        del board["adc"][0]["sensor"]
        board["adc"][0]["fitted"] = False
        project = self.project()
        project["use"]["adc"] = ["LAMP_IS", "PUMP_IS"]
        errors = self.errors(board=board, project=project)
        self.assertIn("ADC LAMP_IS: device LAMP_SW (highSideSwitch) compares overcurrent_ma, fault_ma, open_load_ma, "
                      "off_max_ma (mA) with connection is, but the channel is marked fitted: false",
                      [m.text for m in errors])

    def test_sensed_levels_on_unconfirmed_facts_warn(self):
        board = self.board()
        board["adc"][1]["sensor"]["verify"] = True
        config, messages = self.check(board=board)
        (warning,) = messages
        self.assertEqual((warning.severity, warning.yaml_path), ("warning", "adc[1].sensor"))
        self.assertIn("the characteristic of the channel is not confirmed (source missing or verify: true), so the "
                      "levels of PUMP_SW are not either", warning.text)
        self.assertEqual(len(config.sensed_levels), 8)

    @eval_board
    def test_eval_board_levels_keep_their_adc_codes(self):
        # IO-21: the projects of the eval board moved from IS voltages (mV, compared with adc.mv = raw * 4096 / 4095
        # rounded down) to load currents (mA, compared with adc.phys of CURRENTSENSE_KL30B). Over every converter
        # code both comparisons decide alike, so the switch trips at the same ADC codes as before.
        old = {"overcurrent_ma": (2000, ">="), "fault_ma": (3500, ">="), "open_load_ma": (20, "<"),
               "off_max_ma": (500, ">")}
        compare = {">=": int.__ge__, ">": int.__gt__, "<": int.__lt__}
        for project in (EVAL_DEMO_PROJECT, EVAL_CHARGE_PROJECT):
            with self.subTest(project=project.parent.name):
                config, messages = nxconfig.check(project)
                self.assertEqual(messages, [])
                plan = config.adc_sensors["CURRENTSENSE_KL30B"]
                (kl30b,) = [d for d in config.integration.devices if d.name == "KL30B_SW"]
                for parameter, (mv, op) in old.items():
                    level = kl30b.parameters[parameter]
                    for raw in range(plan.max_raw + 1):
                        pin_mv = raw * 4096 // 4095
                        ma = sensor.evaluate(plan, raw)[0]
                        self.assertEqual(compare[op](ma, level), compare[op](pin_mv, mv),
                                         f"{parameter} {level} mA at raw {raw} ({ma} mA, {pin_mv} mV)")

    def test_sense_in_the_manifest(self):
        manifest = (REPO / "libs/bsw/highSideSwitch/module.yaml").read_text(encoding="utf-8")
        cases = [
            (manifest.replace("unit: mA, sense: is, description: load current at or above which it is",
                              "unit: mA, sense: den, description: load current at or above which it is"),
             "parameters.overcurrent_ma.sense", "sense: den is no analog_input connection of highSideSwitch "
                                                "(analog_input connections: is)"),
            (manifest.replace("pattern: \"^[a-z][a-z0-9_]{0,15}$\",", "pattern: \"^[a-z][a-z0-9_]{0,15}$\", sense: is,"),
             "parameters.console.sense", "console is compared with the physical value of is: it must be an int "
                                         "parameter with unit (is str without unit)"),
        ]
        for text, path, fragment in cases:
            with self.subTest(path=path):
                base = self.tmp / "modules" / "highSideSwitch"
                base.mkdir(parents=True, exist_ok=True)
                self.assertNotEqual(text, manifest)
                (base / "module.yaml").write_text(text, encoding="utf-8")
                modules = nxconfig_config.load_modules([self.tmp / "modules"])
                valid, messages = checks._manifests(modules)
                self.assertNotIn("highSideSwitch", valid)
                match = [m for m in messages if fragment in m.text]
                self.assertTrue(match, [m.text for m in messages])
                self.assertEqual(match[0].yaml_path, path)

    def test_schema(self):
        board = self.board()
        board["devices"][0]["connect"] = ["LAMP_IN"]
        self.expect("valid dictionary", board=board, level="board", path="devices[0].connect")
        project = self.project()
        project["modules"]["LAMP_SW"]["cycle_ms"] = [10]
        self.expect("valid integer", project=project, level="project", path="modules.LAMP_SW.cycle_ms")

    def manifest_dirs(self, **extra):
        base = self.tmp / "modules"
        for name, text in extra.items():
            (base / name).mkdir(parents=True, exist_ok=True)
            (base / name / "module.yaml").write_text(text, encoding="utf-8")
        return (REPO / "libs" / "bsw", base)

    def test_manifest_consistency(self):
        dirs = self.manifest_dirs(badSchema="\n".join([
            "module: badSchema",
            "kind: device_driver",
            "parts: [NOTHING]",
            "parameters:",
            "  no_range: { type: int }",
            "  too_wide: { type: int, min: -1, max: 4294967295 }",
            "  bad_default: { type: int, min: 0, max: 5, default: 9 }",
            "  flag: { type: bool, min: 0 }",
            "  name: { type: str, pattern: '([' }",
            "  verify: { type: bool }",
            "constraints:",
            "  - too_wide >> 2",
            "  - name > 3",
            "console: instance",
            ""]))
        config, messages = nxconfig.check(self.write(), TEST_MCUDB, dirs)
        self.assertIsNone(config)
        found = {m.yaml_path: m for m in messages if m.file.parent.name == "badSchema"}
        expected = {
            "parameters.no_range": "int parameter no_range needs min and max",
            "parameters.too_wide": "range -1..4294967295 of too_wide does not fit a 32-bit integer",
            "parameters.bad_default.default": "default of bad_default: 9 is out of range 0..5",
            "parameters.flag": "min/max only apply to int parameters (flag is bool)",
            "parameters.name.pattern": "invalid regular expression '(['",
            "parameters.verify": "verify is reserved",
            "constraints[0]": "invalid constraint 'too_wide >> 2'",
            "constraints[1]": "constraint 'name > 3': name is no int parameter of badSchema",
            "console": "console: instance needs a str parameter console",
        }
        self.assertEqual(set(found), set(expected))
        for path, fragment in expected.items():
            self.assertIn(fragment, found[path].text)
            self.assertIsNotNone(found[path].line)
        self.assertEqual(found["console"].line, 14)

    def test_device_of_an_inconsistent_manifest_gets_no_follow_up_errors(self):
        dirs = self.manifest_dirs(badSwitch="module: badSwitch\nkind: device_driver\nparts: [VN7003]\n"
                                            "parameters: { x: { type: int } }\n")
        board = self.board()
        board["devices"][1].update(part="VN7003", module="badSwitch")
        project = self.project()
        config, messages = nxconfig.check(self.write(board, project), TEST_MCUDB, dirs)
        errors = [m for m in messages if m.severity == "error"]
        self.assertEqual([m.yaml_path for m in errors], ["parameters.x"])
        self.assertIsNone(config)

    def test_other_module_with_defaults_and_bool(self):
        # A second module with only defaults, bool and signed int: an empty modules entry instantiates it.
        dirs = self.manifest_dirs(buzzer="\n".join([
            "module: buzzer",
            "kind: device_driver",
            "parts: [BUZ*]",
            "connect: { out: { kind: digital_output } }",
            "parameters:",
            "  enabled: { type: bool, default: true }",
            "  offset: { type: int, min: -100, max: 100, default: -5 }",
            ""]))
        board = self.board()
        board["devices"][2] = {"name": "BUZZER", "part": "BUZ1", "connect": {"out": "LAMP_IN"}}
        board["devices"][0]["connect"]["in"] = "PUMP_IN"
        board["dio"]["outputs"].append({"name": "SPARE", "pin": "PB6"})
        board["devices"][1]["connect"]["in"] = "SPARE"
        project = self.project()
        project["modules"]["BUZZER"] = {}
        config, messages = nxconfig.check(self.write(board, project), TEST_MCUDB, dirs)
        self.assertEqual(messages, [])
        buzzer = config.integration.instances("buzzer")[0]
        self.assertEqual(buzzer.parameters, {"enabled": True, "offset": -5})


S32K_BOARD = yaml.safe_load((HERE / "test/profiles/boards/testboard_s32k_8mhz/board.yaml").read_text(encoding="utf-8"))
S32K_PROJECT = yaml.safe_load(
    (HERE / "test/profiles/projects/testproject_s32k_8mhz/project.yaml").read_text(encoding="utf-8"))
# PLAT-22: profile of the test derivative TESTK1X, whose instances differ from the S32K144 of the eval board
GATES_BOARD = yaml.safe_load(
    (HERE / "test/profiles/boards/testboard_s32k1_gates/board.yaml").read_text(encoding="utf-8"))
GATES_PROJECT = yaml.safe_load(
    (HERE / "test/profiles/projects/testproject_s32k1_gates/project.yaml").read_text(encoding="utf-8"))


class TimingKernelTest(unittest.TestCase):
    """Family-neutral calculation kernels (timing.py) with hand-calculated values."""

    def test_pll(self):
        from nxconfig import timing
        limits = timing.PllLimits((8_000_000, 16_000_000), (180_000_000, 320_000_000), (1, 8), (16, 47), (2,))
        settings = list(timing.pll_settings(40_000_000, limits))
        self.assertTrue(all(8_000_000 <= s.input_hz <= 16_000_000 and 180_000_000 <= s.vco_hz <= 320_000_000
                            for s in settings))
        for_80 = sorted((s for s in settings if s.output_hz / 2 == 80_000_000), key=timing.pll_preference)
        # 40/3 * 24, 40/4 * 32, 40/5 * 40 all give VCO 320 MHz; the integer 10 MHz reference wins
        self.assertEqual([(s.input_divider, s.multiplier) for s in for_80], [(4, 32), (5, 40), (3, 24)])
        self.assertEqual(list(timing.pll_settings(4_000_000, limits)), [])     # 4 MHz below the reference minimum

    def test_can_bit_timing(self):
        from fractions import Fraction

        from nxconfig import timing
        flexcan = timing.CanTimingLimits((1, 256), (1, 8), (1, 8), (2, 8), (1, 4), (8, 25))
        t, _ = timing.can_bit_timing(40_000_000, 500_000, Fraction(13, 16), flexcan, sjw=2, prop_seg=7)
        self.assertEqual((t.prescaler, t.prop_seg, t.phase_seg1, t.phase_seg2, t.sjw), (5, 7, 5, 3, 2))
        t, _ = timing.can_bit_timing(40_000_000, 500_000, Fraction(13, 16), flexcan)
        self.assertEqual((t.prescaler, t.prop_seg, t.phase_seg1, t.phase_seg2, t.sjw), (5, 8, 4, 3, 3))
        t, _ = timing.can_bit_timing(8_000_000, 125_000, Fraction(7, 8), flexcan)    # 64 = 2.56 x 25: 16 tq x 4
        self.assertEqual((t.prescaler, t.quanta, t.sample_point), (4, 16, Fraction(7, 8)))
        # 96 = 4 x 24 tq would need 20 tq before the sample point (prop + phase 1 at most 16): 6 x 16 tq
        t, problem = timing.can_bit_timing(48_000_000, 500_000, Fraction(7, 8), flexcan)
        self.assertEqual((t.prescaler, t.quanta, t.sample_point), (6, 16, Fraction(7, 8)))
        t, problem = timing.can_bit_timing(40_000_000, 333_333, Fraction(7, 8), flexcan)
        self.assertIsNone(t)
        self.assertIn("cannot be derived exactly", problem)
        # a fixed propagation segment moves the choice to another prescaler (10 tq, 80 %) before it fails
        t, _ = timing.can_bit_timing(40_000_000, 500_000, Fraction(13, 16), flexcan, prop_seg=1)
        self.assertEqual((t.prescaler, t.quanta, t.prop_seg, t.phase_seg1, t.sample_point), (8, 10, 1, 6, Fraction(4, 5)))
        t, problem = timing.can_bit_timing(40_000_000, 500_000, Fraction(13, 16), flexcan, prop_seg=9)
        self.assertIsNone(t)
        self.assertIn("with prop_seg_tq 9", problem)

    def test_baud_divider(self):
        from nxconfig import timing
        lpuart = timing.BaudLimits((4, 32), (1, 8191), 16)
        s = timing.baud_divider(48_000_000, 115_200, lpuart)
        self.assertEqual((s.oversampling, s.divider), (16, 26))     # equal error for OSR 4/8/13/16/26/32: reset value
        s = timing.baud_divider(40_000_000, 115_200, lpuart)
        self.assertEqual((s.oversampling, s.divider), (29, 12))     # 114943 Bd, -0.22 %
        self.assertAlmostEqual(float(s.error(115_200)), 40e6 / 348 / 115200 - 1)
        s = timing.baud_divider(1_000_000, 921_600, lpuart)          # best effort, the caller judges the error
        self.assertEqual((s.oversampling, s.divider), (4, 1))
        self.assertLess(s.error(921_600), -0.7)


class ClockTest(Profiles):
    """Clock model (clock.py) with the s32k1xx test profile (8 MHz crystal, 64 MHz) and the eval board."""

    def write(self, board=None, project=None) -> Path:
        project = copy.deepcopy(S32K_PROJECT if project is None else project)
        project["project"]["board"] = "../../boards/testboard"
        return super().write(S32K_BOARD if board is None else board, project)

    def check(self, board=None, project=None, mcudb=nxconfig.MCUDB):
        return nxconfig.check(self.write(board, project), mcudb)

    def messages(self, board=None, project=None):
        _, messages = self.check(board, project)
        return messages

    def expect(self, fragment, board=None, project=None, level=None, path=None, severity="error"):
        found = [m for m in self.messages(board, project) if fragment in m.text and m.severity == severity]
        self.assertTrue(found, f"'{fragment}' not in {[str(m) for m in self.messages(board, project)]}")
        if level is not None:
            self.assertEqual(found[0].file.name, f"{level}.yaml")
        if path is not None:
            self.assertEqual(found[0].yaml_path, path)
        return found[0]

    @staticmethod
    def board():
        return copy.deepcopy(S32K_BOARD)

    @staticmethod
    def project():
        return copy.deepcopy(S32K_PROJECT)

    def test_profile_8mhz_64mhz(self):
        config, messages = self.check()
        self.assertEqual(messages, [])
        p = config.clock
        self.assertEqual((p.system_source, p.pll.input_divider, p.pll.multiplier, p.pll.vco_hz), ("SPLL", 1, 32, 256e6))
        self.assertEqual((p.domains["core"], p.domains["bus"], p.dividers["slow"]), (64_000_000, 32_000_000, 3))
        self.assertEqual((p.peripheral_hz["FlexCAN0"], p.peripheral_hz["LPUART1"], p.counter_hz("FTM1")),
                         (8_000_000, 48_000_000, 64_000_000))
        veh, diag = p.can["VEH"], p.can["DIAG"]
        # 8 MHz / 500 kbit/s = 16 tq, 87.5 % -> phase 2 = 2 tq, propagation segment at its maximum 8
        self.assertEqual((veh.prescaler, veh.prop_seg, veh.phase_seg1, veh.phase_seg2, veh.sjw), (1, 8, 5, 2, 2))
        self.assertEqual((diag.prescaler, diag.prop_seg, diag.phase_seg1, diag.phase_seg2, diag.sjw), (2, 7, 4, 4, 4))
        self.assertEqual((p.uart["CONSOLE"].oversampling, p.uart["CONSOLE"].divider), (16, 13))

    def test_system_clock_sources(self):
        cases = [(48_000_000, "FIRC", 1), (8_000_000, "SOSC", 1), (24_000_000, "FIRC", 2), (80_000_000, "SPLL", 2),
                 (1_000_000, "SOSC", 8), (6_000_000, "FIRC", 8)]
        for hz, source, divcore in cases:
            with self.subTest(hz=hz):
                project = self.project()
                project["clock"]["system_hz"] = hz
                del project["pwm"]                     # 25 kHz from 1 MHz is a bswgen matter, not tested here
                config, messages = self.check(project=project)
                self.assertEqual([m for m in messages if m.severity == "error"], [])
                self.assertEqual((config.clock.system_source, config.clock.dividers["core"]), (source, divcore))
                self.assertLessEqual(config.clock.domains["bus"], 48_000_000)
                self.assertLessEqual(config.clock.domains["slow"], 26_670_000)

    def test_without_crystal(self):
        board = self.board()
        del board["board"]["clock"]
        project = self.project()
        project["clock"]["system_hz"] = 48_000_000
        project["use"]["can"] = []
        project["can"] = {}
        only_used_can_ranks(project)
        config, messages = self.check(board, project)
        self.assertEqual(messages, [])
        self.assertEqual((config.clock.system_source, config.clock.pll), ("FIRC", None))
        self.assertEqual(config.clock.peripheral_source["FTM1"], "FIRC")        # SPLL not available
        project["clock"]["system_hz"] = 64_000_000
        self.expect("cannot be derived exactly from FIRC, SIRC (no oscillator)", board, project, "project",
                    "clock.system_hz")
        project = self.project()
        project["clock"]["system_hz"] = 48_000_000
        self.expect("FlexCAN runs from the oscillator clock SOSCDIV2", board, project, "project", "can.VEH")

    def test_errors_with_yaml_path(self):
        def board_clock(hz):
            return lambda b, p: b["board"].update(clock={"sosc_hz": hz})

        cases = [
            (board_clock(50_000_000), "outside the range of S32K144", "board", "board.clock.sosc_hz"),
            (board_clock(6_000_000), "crystal in the high range", "board", "board.clock.sosc_hz"),
            (lambda b, p: p["clock"].update(system_hz=96_000_000), "exceeds the maximum core clock", "project",
             "clock.system_hz"),
            # 8 MHz * 23..47 / 2 / DIVCORE: 46..80 MHz in 2-MHz steps, 61 MHz is not reachable
            (lambda b, p: p["clock"].update(system_hz=61_000_000), "possible e.g.: 80 MHz, 78 MHz, 76 MHz", "project",
             "clock.system_hz"),
            (lambda b, p: p["can"]["VEH"].update(bitrate=300_000), "cannot be derived exactly", "project",
             "can.VEH.bitrate"),
            (lambda b, p: p["can"]["VEH"].update(prop_seg_tq=1), "fits the segment limits with prop_seg_tq 1",
             "project", "can.VEH"),
            (lambda b, p: p["can"]["VEH"].update(sjw_tq=3), "with sjw_tq 3", "project", "can.VEH"),
            # 1 Mbit/s at 8 MHz: 8 tq; 50 % would leave no propagation segment -> 62.5 %, 12.5 pp off
            (lambda b, p: p["can"]["DIAG"].update(bitrate=1_000_000, sample_point_percent=50),
             "sample point 62.50 % instead of 50.00 %", "project", "can.DIAG.sample_point_percent"),
            # ARCH-42: the profile uses VEH only (DIAG is on FlexCAN1 with 16 message buffers)
            (lambda b, p: p["can"].pop("VEH"), "can.VEH.bitrate is missing", "project", "can"),
            (lambda b, p: p["can"].update(BODY={"bitrate": 500_000}), "has no can entry BODY", "project", "can.BODY"),
            (lambda b, p: p["uart"].pop("CONSOLE"), "uart.CONSOLE.baudrate is missing", "project", "uart"),
            # 48 MHz / 2.9 Mbit/s = 16.55: OSR x SBR 17 -> -2.64 %
            (lambda b, p: p["uart"]["CONSOLE"].update(baudrate=2_900_000), "2.64 % error", "project",
             "uart.CONSOLE.baudrate"),
            (lambda b, p: p["uart"]["CONSOLE"].update(baudrate=2_900_000), "more than 2 %", "project",
             "uart.CONSOLE.baudrate"),
            (lambda b, p: p["can"]["VEH"].update(sample_point_percent=99), "less than or equal to 95", "project",
             "can.VEH.sample_point_percent"),
        ]
        for change, fragment, level, path in cases:
            with self.subTest(fragment=fragment):
                board, project = self.board(), self.project()
                change(board, project)
                self.expect(fragment, board, project, level, path)

    def test_warnings(self):
        project = self.project()
        project["uart"]["CONSOLE"]["baudrate"] = 1_000_000           # 48 MHz / 48 = 1 Mbit/s exact? 16 x 3 -> 0 %
        self.assertEqual(self.messages(project=project), [])
        project["uart"]["CONSOLE"]["baudrate"] = 1_500_000           # 48 MHz / 32 exact
        self.assertEqual(self.messages(project=project), [])
        project["uart"]["CONSOLE"]["baudrate"] = 1_200_000           # 48 MHz / 40 = OSR 20 x 2 exact
        self.assertEqual(self.messages(project=project), [])
        project["uart"]["CONSOLE"]["baudrate"] = 1_280_000           # 48 MHz / 37.5: 37 +1.35 %, 38 -1.3 %
        self.expect("1.32 % error", project=project, path="uart.CONSOLE.baudrate", severity="warning")
        project = self.project()
        project["can"]["DIAG"]["sample_point_percent"] = 73          # 16 tq: 75 % (2 pp) is the nearest
        self.expect("sample point 75.00 % instead of 73.00 %", project=project, severity="warning",
                    path="can.DIAG.sample_point_percent")
        project = self.project()
        project["use"]["can"] = ["VEH"]                      # ARCH-07: no warning, the bit timing of the
        only_used_can_ranks(project)                         # unused bus is generated as well
        config, messages = self.check(project=project)
        self.assertEqual((messages, sorted(config.clock.can)), ([], ["DIAG", "VEH"]))
        project["can"].pop("DIAG")                           # without values: no timing for the unused bus
        config, messages = self.check(project=project)
        self.assertEqual((messages, sorted(config.clock.can)), ([], ["VEH"]))
        project["can"]["BODY"] = {"bitrate": 500000}
        self.expect("has no can entry BODY", project=project, path="can.BODY")

    @on_eval_board
    def test_eval_board_demo_project(self):
        config, messages = nxconfig.check(DEMO_PROJECT)
        self.assertEqual(findings(messages), EVAL_OPEN_POWER_STAGE)
        p = config.clock
        # previous hand-written boardClock.h: SOSC 40 MHz / PREDIV 4 * MULT 32 = VCO 320, SPLL 160, DIVCORE 2
        self.assertEqual((p.system_source, p.pll.input_divider, p.pll.multiplier), ("SPLL", 4, 32))
        self.assertEqual((p.dividers["core"], p.dividers["bus"], p.dividers["slow"]), (2, 2, 3))
        can0 = p.can["CAN0"]
        self.assertEqual((can0.prescaler, can0.prop_seg, can0.phase_seg1, can0.phase_seg2, can0.sjw), (5, 7, 5, 3, 2))
        self.assertEqual((p.uart["UART"].oversampling, p.uart["UART"].divider), (16, 26))
        self.assertEqual(p.peripheral_hz["LPSPI1"], 48_000_000)

    def test_family_without_model_gets_neutral_checks_only(self):
        config, messages = nxconfig.check(Profiles.write(self, TEST_BOARD, TEST_PROJECT), TEST_MCUDB)
        self.assertEqual(messages, [])
        self.assertIsNone(config.clock)
        project = copy.deepcopy(TEST_PROJECT)
        del project["uart"]
        _, messages = nxconfig.check(Profiles.write(self, TEST_BOARD, project), TEST_MCUDB)
        self.assertEqual([m.text for m in messages], ["uart CONSOLE is used (use.uart), but uart.CONSOLE.baudrate "
                                                      "is missing"])

    def test_derivative_clock_limits(self):
        mcudb = self.tmp / "mcudb"
        shutil.copytree(REPO / "mcudb", mcudb, ignore=shutil.ignore_patterns("sources", "tools"))
        file = mcudb / "s32k1xx" / "S32K144.yaml"
        text = file.read_text(encoding="utf-8").replace("vco_hz: [180000000, 320000000]", "vco_hz: [320000000, 180000000]")
        file.write_text(text, encoding="utf-8")
        _, messages = nxconfig.check(self.write(), mcudb)
        found = [m for m in messages if "minimum 320000000 is above maximum 180000000" in m.text]
        self.assertEqual((found[0].file.name, found[0].yaml_path), ("S32K144.yaml", "clock.pll.vco_hz"))
        text = re.sub(r"\nclock:\n(  .*\n)+", "\n", file.read_text(encoding="utf-8"))
        file.write_text(text, encoding="utf-8")
        _, messages = nxconfig.check(self.write(), mcudb)
        self.assertIn("no clock data (clock) in the derivative database", " ".join(m.text for m in messages))


class PccClockTest(Profiles):
    """PLAT-22/ARCH-18: the derivative offers the functional clocks of a peripheral, the project chooses one.

    Profile testboard_s32k1_gates/testproject_s32k1_gates with the test derivative TESTK1X
    (tools/nxconfig/test/mcudb/s32k1xx), whose instances differ from the S32K144 of the eval board: FTM6/FTM7
    instead of FTM2..FTM5, LPI2C1 on the bus clock, no ADC1/LPSPI1/LPSPI2/LPUART2/ENET, FTM7 without a default,
    QSPI without clock data, FTM6 with an output limit. 8 MHz crystal, 64 MHz core from the PLL, 32 MHz bus.
    """

    # clock gates of the OpenBSW driver TESTK1X has, in the order of clockConfig.cpp, then the other instances
    GATES = ["FTM0", "FTM1", "ADC0", "LPSPI0", "LPIT", "LPTMR0", "FLEXIO", "LPI2C0", "LPUART0", "LPUART1", "FTM6"]

    def write(self, board=None, project=None) -> Path:
        project = copy.deepcopy(GATES_PROJECT if project is None else project)
        project["project"]["board"] = "../../boards/testboard"
        return super().write(GATES_BOARD if board is None else board, project)

    @staticmethod
    def board():
        return copy.deepcopy(GATES_BOARD)

    @staticmethod
    def project():
        return copy.deepcopy(GATES_PROJECT)

    def derivative(self, change) -> Path:
        """Copy of the test derivative database with `change` applied to TESTK1X (built-in errors)."""
        mcudb = self.tmp / "mcudb"
        if not mcudb.exists():
            shutil.copytree(TEST_MCUDB, mcudb)
        file = mcudb / "s32k1xx" / "TESTK1X.yaml"
        doc = yaml.safe_load(file.read_text(encoding="utf-8"))
        change(doc)
        file.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        return mcudb

    def messages(self, board=None, project=None, mcudb=TEST_MCUDB):
        return self.check(board, project, mcudb)[1]

    def only_error(self, board=None, project=None, mcudb=TEST_MCUDB):
        errors = [m for m in self.messages(board, project, mcudb) if m.severity == "error"]
        self.assertEqual(len(errors), 1, [str(m) for m in errors])
        self.assertIsNotNone(errors[0].line)
        return errors[0]

    def test_clocks_of_the_derivative(self):
        config, messages = self.check()
        # ARCH-18: no warning for FTM6 any more - tools/bswgen writes the PCC register of every used instance,
        # so the clock model does not judge what the OpenBSW driver happens to switch on (see test_bswgen.py).
        self.assertEqual(messages, [])
        p = config.clock
        self.assertEqual((p.system_source, p.domains["core"], p.domains["bus"]), ("SPLL", 64_000_000, 32_000_000))
        self.assertEqual(list(p.peripheral_source), self.GATES)
        self.assertEqual(p.peripheral_output["FTM1"], "SPLLDIV1")           # SPLL, DIV1 output (timer)
        self.assertEqual(p.peripheral_output["LPUART1"], "FIRCDIV2")
        self.assertEqual((p.peripheral_hz["FTM1"], p.peripheral_hz["LPUART1"]), (128_000_000, 48_000_000))
        # select: bus - the bus domain, no PCC clock select and therefore no CLOCK_SOURCE macro
        self.assertEqual(p.peripheral_hz["LPI2C1"], 32_000_000)
        self.assertNotIn("LPI2C1", p.peripheral_source)
        # select: none - FlexCAN chooses its clock in CTRL1.CLKSRC (oscillator clock SOSCDIV2)
        self.assertEqual(p.peripheral_hz["FlexCAN0"], 8_000_000)
        self.assertNotIn("FlexCAN0", p.peripheral_source)
        # instances the derivative does not have get no clock and no macro (the driver guards them with #ifdef)
        for gate in ("FTM2", "FTM3", "FTM4", "FTM5", "ADC1", "LPSPI1", "LPSPI2", "LPUART2", "ENET"):
            self.assertNotIn(gate, p.peripheral_source, gate)
            self.assertNotIn(gate, p.peripheral_hz, gate)
        self.assertEqual(p.uart["CONSOLE"].oversampling * p.uart["CONSOLE"].divider, 48_000_000 // 115_200)
        # every clock of this project comes from a default of the derivative, none from the project
        self.assertEqual(set(p.peripheral_origin.values()), {"derivative"})

    def test_used_instance_without_a_choice_is_an_error(self):
        """ARCH-18: FTM7 offers sources but has no default - the project has to choose (PLAT-22, finding 1)."""
        project = self.project()
        project["use"]["input_capture"] = "all"          # TACHO on FTM7, which has no default in TESTK1X
        m = self.only_error(project=project)
        self.assertEqual((m.file.name, m.yaml_path), ("project.yaml", "use.input_capture"))
        self.assertIn("FTM7 is used by input_capture TACHO (use.input_capture in project.yaml), but no functional "
                      "clock is chosen: TESTK1X offers SOSCDIV1, FIRCDIV1 (instances.FTM7.clock.sources in "
                      "TESTK1X.yaml) and names no default, so the project has to choose one "
                      "(peripheral_clocks.FTM7)", m.text)
        # with a choice the project runs through, and the clock reaches the plan
        project["peripheral_clocks"] = {"FTM7": {"select": "SOSC"}}      # only one output: divider optional
        config, messages = self.check(project=project)
        self.assertEqual(messages, [])
        self.assertEqual((config.clock.peripheral_output["FTM7"], config.clock.peripheral_hz["FTM7"],
                          config.clock.peripheral_origin["FTM7"]), ("SOSCDIV1", 8_000_000, "project"))

    def test_used_instance_without_any_clock_data_is_an_error(self):
        """An instance without a `clock` entry at all (like QSPI) stays an error of the derivative database."""
        mcudb = self.derivative(lambda d: d["instances"]["FTM6"].pop("clock"))
        m = self.only_error(mcudb=mcudb)
        self.assertEqual((m.file.name, m.yaml_path), ("TESTK1X.yaml", "instances.FTM6.clock"))
        self.assertIn("FTM6 is used by pwm FAN (pwm.FAN in project.yaml), but the derivative database gives it "
                      "no clock data (instances.FTM6.clock)", m.text)

    def test_built_in_errors_of_the_derivative(self):
        def drop_clock(instance):
            return lambda d: d["instances"][instance].pop("clock")

        def drop_default(instance):
            return lambda d: d["instances"][instance]["clock"].pop("default")

        cases = [
            # a gate the driver writes unconditionally: without the macro the board port does not compile
            (drop_clock("FTM0"), "clock driver switches the clock of FTM0 on and needs CLOCK_SOURCE_FTM0",
             "instances.FTM0.clock"),
            (drop_default("FTM0"), "clock driver switches the clock of FTM0 on and needs CLOCK_SOURCE_FTM0",
             "instances.FTM0.clock"),
            (drop_clock("LPUART0"), "needs CLOCK_SOURCE_LPUART0", "instances.LPUART0.clock"),
            (lambda d: d["instances"].pop("LPI2C0"), "writes PCC_LPI2C0 unconditionally", "instances"),
            (lambda d: d["clock"]["pcc"].pop("LPIT"),
             "describes neither an instance LPIT nor a clock gate clock.pcc.LPIT", "instances"),
            # optional gate: the header of this derivative has no PCC_ADC1_INDEX, but the instance exists
            (lambda d: d["instances"].update(ADC1={"class": "adc", "resolution_bits": 12,
                                                   "channel_pattern": "SE([0-9])"}),
             "needs CLOCK_SOURCE_ADC1", "instances.ADC1.clock"),
            # ARCH-18: a default above the limit of the asynchronous output (FTM6 max_hz 100 MHz, SPLLDIV1 128 MHz)
            (lambda d: d["instances"]["FTM6"]["clock"]["default"].update(select="SPLL"),
             "FTM6 would run at 128 MHz from SPLLDIV1, more than the maximum functional clock of 100 MHz that "
             "TESTK1X allows (instances.FTM6.clock.max_hz); choose another source (peripheral_clocks.FTM6) or "
             "another system clock", "instances.FTM6.clock.default"),
        ]
        for change, fragment, path in cases:
            with self.subTest(fragment=fragment):
                m = self.only_error(mcudb=self.derivative(change))
                self.assertEqual((m.file.name, m.yaml_path), ("TESTK1X.yaml", path))
                self.assertIn(fragment, m.text)
                shutil.rmtree(self.tmp / "mcudb", ignore_errors=True)

    def test_built_in_errors_change_the_calculated_values(self):
        """Wrong clock data must not stay unnoticed: the derived values change with it."""
        def default(instance, select):
            return lambda d: d["instances"][instance]["clock"]["default"].update(select=select)

        cases = [
            # FTM1 on the oscillator instead of the PLL: counter input of the PWM driver and the macro change
            (default("FTM1", "SOSC"), "FTM1", "SOSCDIV1", 8_000_000),
            # console on the oscillator instead of FIRC: the UART divider is calculated from 8 MHz
            (default("LPUART1", "SOSC"), "LPUART1", "SOSCDIV2", 8_000_000),
            # ADC on the PLL: the ADC clock of the serialized access changes (48 MHz limit raised for this case)
            (lambda d: (d["instances"]["ADC0"]["clock"].pop("max_hz"),
                        d["instances"]["ADC0"]["clock"]["default"].update(select="SPLL")),
             "ADC0", "SPLLDIV2", 128_000_000),
        ]
        for change, gate, output, hz in cases:
            with self.subTest(gate=gate):
                config, messages = self.check(mcudb=self.derivative(change))
                self.assertEqual([m for m in messages if m.severity == "error"], [])
                self.assertEqual((config.clock.peripheral_output[gate], config.clock.peripheral_hz[gate]),
                                 (output, hz))
                shutil.rmtree(self.tmp / "mcudb", ignore_errors=True)
        # the console divider is calculated from the changed clock: 8 MHz / 69 instead of 48 MHz / 416
        config, messages = self.check(mcudb=self.derivative(default("LPUART1", "SOSC")))
        self.assertEqual([m for m in messages if m.severity == "error"], [])
        setting = config.clock.uart["CONSOLE"]
        self.assertEqual(setting.oversampling * setting.divider, 69)

    def test_schema_of_the_clock_entry(self):
        cases = [
            (lambda d: d["instances"]["FTM0"]["clock"].update(sources=["PLL2"]), "Input should be 'SOSC'"),
            (lambda d: d["instances"]["FTM0"]["clock"].pop("divider"),
             "clock sources SOSC, SIRC, FIRC, SPLL need divider (the asynchronous outputs DIV1/DIV2 the instance "
             "is wired to, e.g. [2])"),
            (lambda d: d["instances"]["LPI2C1"]["clock"].update(divider=[2]),
             "clock bus has no clock select and therefore no divider"),
            (lambda d: d["instances"]["LPI2C1"]["clock"].update(max_hz=1000),
             "clock bus has no clock select and therefore no max_hz"),
            (lambda d: d["instances"]["FTM0"]["clock"].update(divider=[3]), "Input should be 1 or 2"),
            (lambda d: d["instances"]["FTM0"]["clock"].update(pcs=1), "Extra inputs are not permitted"),
            # sources, select and (PLAT-69) domain are the forms of the entry, never two and never none
            (lambda d: d["instances"]["FTM0"]["clock"].update(select="bus"),
             "clock entry needs either sources (with divider), select (bus, core or none) or domain"),
            (lambda d: d["instances"]["LPI2C1"]["clock"].pop("select"),
             "clock entry needs either sources (with divider), select (bus, core or none) or domain"),
            # the default has to be one of the offered sources and outputs
            (lambda d: d["instances"]["FTM0"]["clock"]["default"].update(select="SIRC", divider=2),
             "default divider 2 is not one of the outputs 1"),
            (lambda d: d["instances"]["FTM0"]["clock"].update(sources=["FIRC", "SPLL"]),
             "default clock SOSC is not one of the sources FIRC, SPLL"),
            (lambda d: d["instances"]["FTM0"]["clock"]["default"].pop("divider"),
             "default clock SOSC needs divider (1)"),
        ]
        for change, fragment in cases:
            with self.subTest(fragment=fragment):
                messages = self.messages(mcudb=self.derivative(change))
                self.assertTrue([m for m in messages if fragment in m.text], [str(m) for m in messages])
                shutil.rmtree(self.tmp / "mcudb", ignore_errors=True)

    def test_s32k148_instances_without_a_default(self):
        """ARCH-09 B5/ARCH-18 with the repository derivative: FTM6 and LPI2C1 of the S32K148 have no default,
        the project has to choose their functional clock."""
        board = yaml.safe_load((REPO / "boards/buildtest_s32k148/board.yaml").read_text(encoding="utf-8"))
        project = yaml.safe_load((REPO / "projects/buildtest_s32k148_min/project.yaml").read_text(encoding="utf-8"))
        board["pwm"][0]["instance"] = "FTM6"             # [MUX] FTM6_CH0 PTB20, FTM6_CH1 PTB21 (LQFP176)
        board["pwm"][0]["channels"] = [{"name": "MOT1_U_PWM_HS", "channel": 0, "pin": "PTB20"},
                                       {"name": "MOT1_U_PWM_LS", "channel": 1, "pin": "PTB21"}]
        # ARCH-17: the build profile uses no PWM group any more (a board without a motor bridge must link).
        # This test needs a *used* FTM instance to reach the clock model, so it selects the board group itself.
        project["pwm"] = {"MOT1": {"frequency_hz": 20000, "deadtime_ns": 1000, "verify": True}}
        board["i2c"] = [{"name": "SENSOR", "instance": "LPI2C1", "sda": {"pin": "PTD8"}, "scl": {"pin": "PTD9"}}]
        project["use"]["i2c"] = "all"
        project["project"].pop("rte", None)
        project["project"].pop("diag", None)
        (self.tmp / "boards/testboard").mkdir(parents=True)
        (self.tmp / "projects/testproject").mkdir(parents=True)
        (self.tmp / "boards/testboard/board.yaml").write_text(yaml.safe_dump(board, sort_keys=False),
                                                              encoding="utf-8")
        project["project"]["board"] = "../../boards/testboard"
        path = self.tmp / "projects/testproject/project.yaml"
        path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        errors = [m for m in nxconfig.check(path)[1] if m.severity == "error"]
        self.assertEqual([(m.file.name, m.yaml_path) for m in errors],
                         [("project.yaml", "pwm.MOT1"), ("project.yaml", "use.i2c")])
        self.assertIn("S32K148: FTM6 is used by pwm MOT1 (pwm.MOT1 in project.yaml), but no functional clock is "
                      "chosen: S32K148 offers SOSCDIV1, SIRCDIV1, FIRCDIV1, SPLLDIV1 "
                      "(instances.FTM6.clock.sources in S32K148.yaml) and names no default, so the project has "
                      "to choose one (peripheral_clocks.FTM6)", errors[0].text)
        self.assertIn("LPI2C1 is used by i2c SENSOR", errors[1].text)
        for m in errors:
            self.assertIsNotNone(m.line)
        # the project decides and the clocks are in the plan (ARCH-18): FTM6 from the crystal, LPI2C1 from FIRC
        project["peripheral_clocks"] = {"FTM6": {"select": "SOSC", "divider": 1},
                                        "LPI2C1": {"select": "FIRC", "divider": 2}}
        path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        config, messages = nxconfig.check(path)
        # this project chooses no margin; the synthetic power stage of the build profile carries a [SYN] source since
        # TOOL-243 (F8: no verify: true in the open profile) and counts as confirmed
        self.assertEqual(findings(messages), [("warning", "pwm.MOT1")])
        self.assertEqual((config.clock.peripheral_output["FTM6"], config.clock.peripheral_output["LPI2C1"]),
                         ("SOSCDIV1", "FIRCDIV2"))
        self.assertEqual(config.clock.peripheral_origin["FTM6"], "project")

    def test_project_chooses_the_functional_clock(self):
        """ARCH-18: peripheral_clocks of the project replaces the default of the derivative."""
        project = self.project()
        project["peripheral_clocks"] = {"LPUART1": {"select": "SOSC", "divider": 2},
                                        "FTM6": {"select": "FIRC", "divider": 1},
                                        "LPIT": {"select": "FIRC", "divider": 2}}
        config, messages = self.check(project=project)
        self.assertEqual(messages, [])
        p = config.clock
        self.assertEqual((p.peripheral_output["LPUART1"], p.peripheral_hz["LPUART1"]), ("SOSCDIV2", 8_000_000))
        self.assertEqual((p.peripheral_output["FTM6"], p.peripheral_hz["FTM6"]), ("FIRCDIV1", 48_000_000))
        self.assertEqual((p.peripheral_output["LPIT"], p.peripheral_hz["LPIT"]), ("FIRCDIV2", 48_000_000))
        # order: the gates of the driver first (LPIT before LPUART1), then the other instances of the derivative
        self.assertEqual([g for g, o in p.peripheral_origin.items() if o == "project"],
                         ["LPIT", "LPUART1", "FTM6"])
        # derived values follow the choice: the console divider is calculated from 8 MHz instead of 48 MHz
        self.assertEqual(p.uart["CONSOLE"].oversampling * p.uart["CONSOLE"].divider, 69)
        # the unchosen instances keep the default of the derivative
        self.assertEqual((p.peripheral_output["FTM0"], p.peripheral_origin["FTM0"]), ("SOSCDIV1", "derivative"))

    def test_rejected_choices_of_the_project(self):
        """ARCH-18: every project choice is checked against the derivative, with file, line and YAML path."""
        cases = [
            # source the derivative does not offer for this instance
            ({"FTM7": {"select": "SPLL", "divider": 1}},
             "FTM7 cannot run from SPLL; TESTK1X allows SOSC, FIRC (instances.FTM7.clock.sources)",
             "peripheral_clocks.FTM7.select"),
            # output the instance is not wired to
            ({"FTM0": {"select": "SOSC", "divider": 2}},
             "FTM0 is not wired to the output SOSCDIV2; TESTK1X offers SOSCDIV1 (instances.FTM0.clock.divider)",
             "peripheral_clocks.FTM0.divider"),
            # two outputs, none named: the project has to decide
            ({"LPUART1": {"select": "SOSC"}},
             "LPUART1 needs divider (the asynchronous output of SOSC): TESTK1X offers 1, 2 "
             "(instances.LPUART1.clock.divider)", "peripheral_clocks.LPUART1"),
            # limit of the asynchronous output (ARCH-06): FTM6 allows 100 MHz, SPLLDIV1 is 128 MHz here
            ({"FTM6": {"select": "SPLL", "divider": 1}},
             "FTM6 would run at 128 MHz from SPLLDIV1, more than the maximum functional clock of 100 MHz that "
             "TESTK1X allows (instances.FTM6.clock.max_hz)", "peripheral_clocks.FTM6"),
            # instance without a clock select: FlexCAN picks its clock in CTRL1.CLKSRC, LPI2C1 runs on the bus
            ({"FlexCAN0": {"select": "SOSC", "divider": 2}},
             "FlexCAN0 has no clock select: its functional clock is none (instances.FlexCAN0.clock.select in "
             "TESTK1X.yaml)", "peripheral_clocks.FlexCAN0"),
            ({"LPI2C1": {"select": "FIRC", "divider": 2}},
             "LPI2C1 has no clock select: its functional clock is bus", "peripheral_clocks.LPI2C1"),
            # instance without any clock data in the database
            ({"QSPI": {"select": "FIRC", "divider": 1}},
             "the derivative database gives QSPI no clock data (instances.QSPI.clock with sources, divider and a "
             "source), so there is nothing to choose from", "peripheral_clocks.QSPI"),
            # instance the derivative does not have at all
            ({"FTM4": {"select": "SOSC", "divider": 1}},
             "TESTK1X has no peripheral instance FTM4 (derivative database: instances, clock.pcc)",
             "peripheral_clocks.FTM4"),
        ]
        for choice, fragment, path in cases:
            with self.subTest(fragment=fragment):
                project = self.project()
                project["peripheral_clocks"] = choice
                errors = [m for m in self.messages(project=project) if m.severity == "error"]
                found = [m for m in errors if fragment in m.text]
                self.assertTrue(found, [str(m) for m in errors])
                self.assertEqual((found[0].file.name, found[0].yaml_path), ("project.yaml", path))
                self.assertIsNotNone(found[0].line)

    def test_choice_for_an_unused_instance_is_a_warning(self):
        project = self.project()
        project["peripheral_clocks"] = {"FTM1": {"select": "SOSC", "divider": 1}}   # FTM1 is not used
        config, messages = self.check(project=project)
        self.assertEqual([m.severity for m in messages], ["warning"])
        self.assertEqual((messages[0].file.name, messages[0].yaml_path),
                         ("project.yaml", "peripheral_clocks.FTM1"))
        self.assertIn("FTM1 is not used by the project (use.*, pwm, adc_internal, bootloader), setting ignored",
                      messages[0].text)
        # ignored: the default of the derivative still applies
        self.assertEqual(config.clock.peripheral_output["FTM1"], "SPLLDIV1")

    def test_choice_the_board_cannot_provide(self):
        board, project = self.board(), self.project()
        del board["board"]["clock"]                       # no crystal: no SOSC, no PLL
        project["clock"]["system_hz"] = 48_000_000
        project["use"]["can"], project["can"] = [], {}
        only_used_can_ranks(project)
        project["peripheral_clocks"] = {"FTM6": {"select": "SOSC", "divider": 1}}
        errors = [m for m in self.messages(board, project) if m.severity == "error"]
        self.assertEqual(len(errors), 1, [str(m) for m in errors])
        self.assertIn("FTM6 cannot run from SOSC: board testboard_s32k1_gates does not provide it "
                      "(available: FIRC, SIRC)", errors[0].text)
        self.assertEqual((errors[0].file.name, errors[0].yaml_path),
                         ("project.yaml", "peripheral_clocks.FTM6.select"))
        # without a choice the model falls back to FIRC like the clock driver does (unchanged behaviour)
        del project["peripheral_clocks"]
        config, messages = self.check(board, project)
        self.assertEqual(messages, [])
        self.assertEqual(config.clock.peripheral_source["FTM1"], "FIRC")

    def test_repository_derivatives_describe_every_gate_the_driver_needs(self):
        """Every s32k1xx derivative of mcudb has clock data for the gates the OpenBSW clock driver writes."""
        from nxconfig import clock as nxclock, config as cfgmod, model
        files = sorted(p for p in (REPO / "mcudb/s32k1xx").glob("*.yaml") if not p.name.endswith(".pinmux.yaml"))
        self.assertGreaterEqual({p.stem for p in files}, {"S32K116", "S32K142", "S32K144", "S32K146", "S32K148"})
        for file in files:
            with self.subTest(derivative=file.stem):
                rep = cfgmod.Reporter()
                d = cfgmod._read(rep, "derivative", file, model.Derivative)
                self.assertIsNotNone(d, [str(m) for m in rep.messages])
                gates = {g for g in nxclock.S32K1_DRIVER_GATES
                         if nxclock.S32K1_GATE_INSTANCES.get(g, g) in d.instances or g in d.clock.pcc}
                self.assertGreaterEqual(gates, nxclock.S32K1_DRIVER_GATES_REQUIRED, "unconditional gates")
                for gate in gates:
                    name = nxclock.S32K1_GATE_INSTANCES.get(gate, gate)
                    entry = d.instances[name].clock if name in d.instances else d.clock.pcc[gate]
                    self.assertIsNotNone(entry, f"{file.stem}: {name} has no clock")
                    self.assertTrue(entry.source or entry.verify, f"{file.stem}: {name} clock without a source")
                    # ARCH-18: a gate of the driver needs a default - its CLOCK_SOURCE macro is generated for
                    # every project, whether the project uses the instance or not
                    self.assertTrue(entry.select is not None or entry.default is not None,
                                    f"{file.stem}: {name} has neither a default nor select")
                # every entry offers only sources the derivative has (S32K11x has no SPLL)
                internal = set(d.clock.internal) | {"SPLL" if d.clock.pll is not None else "SOSC"}
                for name, inst in d.instances.items():
                    if inst.clock is not None and inst.clock.sources:
                        unknown = set(inst.clock.sources) - internal - {"SOSC"}
                        self.assertEqual(unknown, set(), f"{file.stem}: {name} offers {unknown}")


class I2cBusTest(Profiles):
    """TOOL-120: `flexio_pin` of an I2C bus is needed exactly on a FlexIO instance, and a used I2C bus on an instance
    without a driver of the family port is refused by nxconfig, not by the build.

    Profile testboard_s32k1_gates/testproject_s32k1_gates with the test derivative TESTK1X (LPI2C1 and FlexIO); for
    the FlexIO cases a copy of the test derivative database gets the invented functions FlexIO_D0/FlexIO_D1 on the
    pins of the bus. Test data only, no eval board.
    """

    FLEXIO_BUS = {"name": "SENSOR", "instance": "FlexIO", "sda": {"pin": "PTA2", "flexio_pin": 0},
                  "scl": {"pin": "PTA3", "flexio_pin": 1}}

    def write(self, board=None, project=None) -> Path:
        project = copy.deepcopy(GATES_PROJECT if project is None else project)
        project["project"]["board"] = "../../boards/testboard"
        return super().write(GATES_BOARD if board is None else board, project)

    def mcudb(self) -> Path:
        """Copy of the test derivative database, TESTK1X with FlexIO data pins on PTA2/PTA3 (invented)."""
        mcudb = self.tmp / "mcudb"
        if not mcudb.exists():
            shutil.copytree(TEST_MCUDB, mcudb)
            file = mcudb / "s32k1xx" / "TESTK1X.yaml"
            doc = yaml.safe_load(file.read_text(encoding="utf-8"))
            doc["pinmux"]["LQFP64"]["signals"].update({"FlexIO_D0": {"PTA2": 4}, "FlexIO_D1": {"PTA3": 4}})
            file.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        return mcudb

    def flexio_board(self, **pins):
        board = copy.deepcopy(GATES_BOARD)
        bus = copy.deepcopy(self.FLEXIO_BUS)
        for role, value in pins.items():
            if value is None:
                bus[role].pop("flexio_pin")
            else:
                bus[role]["flexio_pin"] = value
        board["i2c"] = [bus]
        return board

    def test_an_i2c_instance_needs_no_flexio_pin(self):
        """The gap of TOOL-106: an I2C bus on a real I2C instance without flexio_pin is valid and carries a
        project that uses it (use.i2c: all)."""
        self.assertNotIn("flexio_pin", GATES_BOARD["i2c"][0]["sda"])
        config, messages = self.check()
        self.assertEqual([str(m) for m in messages], [])
        self.assertEqual([b.name for b in config.selected("i2c")], ["SENSOR"])
        bus = config.board.i2c[0]
        self.assertEqual((bus.sda.flexio_pin, checks.bus_signal(config, "i2c", bus, "sda")), (None, "LPI2C1_SDA"))

    def test_a_flexio_instance_needs_the_data_pin(self):
        """On a FlexIO the data pin decides the pin function: missing, it is an error at its YAML path in
        board.yaml, with a line - for each pin that lacks it."""
        mcudb = self.mcudb()
        _, messages = self.check(self.flexio_board(sda=None), mcudb=mcudb)
        errors = [m for m in messages if m.severity == "error"]
        self.assertEqual([(m.file.name, m.yaml_path) for m in errors], [("board.yaml", "i2c[0].sda.flexio_pin")])
        self.assertIsNotNone(errors[0].line)
        self.assertIn("FlexIO is a FlexIO instance: I2C bus SENSOR needs the FlexIO data pin D<n> its SDA pin PTA2",
                      errors[0].text)
        _, messages = self.check(self.flexio_board(sda=None, scl=None), mcudb=mcudb)
        self.assertEqual([m.yaml_path for m in messages if m.severity == "error"],
                         ["i2c[0].sda.flexio_pin", "i2c[0].scl.flexio_pin"])

    def test_a_flexio_bus_with_its_data_pins(self):
        """With both data pins the FlexIO bus is checked against FlexIO_D<n> and carries the project."""
        config, messages = self.check(self.flexio_board(), mcudb=self.mcudb())
        self.assertEqual([str(m) for m in messages], [])
        bus = config.board.i2c[0]
        self.assertEqual([checks.bus_signal(config, "i2c", bus, r) for r in ("sda", "scl")],
                         ["FlexIO_D0", "FlexIO_D1"])
        # the data pin really is what is checked: D1 does not exist on PTA2
        _, messages = self.check(self.flexio_board(sda=1), mcudb=self.mcudb())
        self.assertEqual([(m.yaml_path, m.text) for m in messages if m.severity == "error"],
                         [("i2c[0].sda.pin", "TESTK1X LQFP64 has no function FlexIO_D1 on PTA2 "
                                             "(available: FlexIO_D0, GPIO, LPI2C1_SDA)")])

    def test_a_flexio_pin_on_an_i2c_instance_is_a_warning(self):
        """Given on a real I2C instance, flexio_pin is never read: a warning at its path, not silence."""
        board = copy.deepcopy(GATES_BOARD)
        board["i2c"][0]["scl"]["flexio_pin"] = 1
        _, messages = self.check(board)
        self.assertEqual(findings(messages), [("warning", "i2c[0].scl.flexio_pin")])
        self.assertIn("LPI2C1 is an I2C instance, not a FlexIO: flexio_pin is not read", messages[0].text)

    def built(self, config):
        """checks._i2c_drivers as for a board that is built with the family port: the temporary tree gets the
        port template of s32k1xx next to its boards/ folder (built_with_family_port, ARCH-58)."""
        template = self.tmp / "platforms" / "s32k1xx" / "port" / "CMakeLists.txt"
        template.parent.mkdir(parents=True, exist_ok=True)
        template.write_text("# family port template (test)\n", encoding="utf-8")
        self.assertTrue(checks.built_with_family_port(config, self.tmp))
        rep = nxconfig_config.Reporter(sources=dict(config.sources))
        checks._i2c_drivers(config, rep, self.tmp)
        return rep.messages

    def test_a_used_i2c_bus_without_a_driver_is_refused(self):
        """s32k1xx has an I2C driver only on FlexIO (bspFlexioI2c), none for LPI2C: a used bus on LPI2C1 is an
        error in use.i2c of project.yaml - there, not in the build (bswgen repeats the same text)."""
        config, messages = self.check()
        self.assertEqual(messages, [])              # a configuration-only profile is never built: nothing to say
        found = self.built(config)
        self.assertEqual([(m.severity, m.file.name, m.yaml_path) for m in found],
                         [("error", "project.yaml", "use.i2c")])
        self.assertIsNotNone(found[0].line)
        self.assertEqual(found[0].text, checks.i2c_driver_problem(config, config.board.i2c[0]))
        self.assertIn("I2C bus SENSOR is on LPI2C1 (class i2c); the family port s32k1xx has no driver for it - "
                      "its I2C driver is the FlexIO I2C master (platforms/s32k1xx/bsp/bspFlexioI2c) and runs only "
                      "on FlexIO of TESTK1X: put the bus on one of them (board.yaml i2c[0].instance", found[0].text)
        project = copy.deepcopy(GATES_PROJECT)
        project["use"]["i2c"] = ["SENSOR"]
        config, _ = self.check(project=project)
        self.assertEqual([m.yaml_path for m in self.built(config)], ["use.i2c[0]"])
        project["use"]["i2c"] = []                  # not used: the board may describe the LPI2C
        config, _ = self.check(project=project)
        self.assertEqual(self.built(config), [])

    def test_a_used_flexio_bus_has_its_driver(self):
        config, messages = self.check(self.flexio_board(), mcudb=self.mcudb())
        self.assertEqual(messages, [])
        self.assertEqual(self.built(config), [])
        self.assertIsNone(checks.i2c_driver_problem(config, config.board.i2c[0]))


# ARCH-22: synthetic clock limits for the test derivative TESTK1X (table of the expected dividers in the file)
CLOCK_LIMITS = yaml.safe_load(
    (HERE / "test/profiles/derivatives/testk1x_clock_limits.yaml").read_text(encoding="utf-8"))


class ClockOutputLimitTest(Profiles):
    """ARCH-22: limits of the asynchronous outputs and of the bus clock with the SPLL as system clock.

    Fact and decision apart (doc/architecture/README.md, section 4): the derivative names the maximum of every
    output DIV1/DIV2 (clock.output_max_hz) and of a domain while a source feeds the system clock
    (clock.system_source_max_hz); the clock model derives the dividers. Profile testboard_s32k1_gates/
    testproject_s32k1_gates with TESTK1X plus test/profiles/derivatives/testk1x_clock_limits.yaml.
    """

    # same profile helpers as PccClockTest (not inherited, so its tests do not run a second time)
    derivative, messages, only_error = PccClockTest.derivative, PccClockTest.messages, PccClockTest.only_error

    def write(self, board=None, project=None) -> Path:
        project = copy.deepcopy(GATES_PROJECT if project is None else project)
        project["project"]["board"] = "../../boards/testboard"
        return super().write(GATES_BOARD if board is None else board, project)

    @staticmethod
    def project():
        return copy.deepcopy(GATES_PROJECT)

    DIVIDERS = {"SPLLDIV1": 4, "SPLLDIV2": 8, "FIRCDIV1": 1, "FIRCDIV2": 4, "SIRCDIV1": 1, "SIRCDIV2": 4,
                "SOSCDIV1": 2, "SOSCDIV2": 1}
    OUTPUTS = {"SPLLDIV1": 32_000_000, "SPLLDIV2": 16_000_000, "FIRCDIV1": 48_000_000, "FIRCDIV2": 12_000_000,
               "SIRCDIV1": 8_000_000, "SIRCDIV2": 2_000_000, "SOSCDIV1": 4_000_000, "SOSCDIV2": 8_000_000}

    @staticmethod
    def with_limits(change=None):
        def apply(doc):
            clock = copy.deepcopy(CLOCK_LIMITS["clock"])
            doc["clock"].update(clock)
            if change is not None:
                change(doc)
        return apply

    def limited(self, change=None) -> Path:
        shutil.rmtree(self.tmp / "mcudb", ignore_errors=True)
        return self.derivative(self.with_limits(change))

    def test_dividers_follow_the_limits(self):
        config, messages = self.check(mcudb=self.limited())
        self.assertEqual(messages, [])
        p = config.clock
        self.assertEqual(p.output_dividers, self.DIVIDERS)
        self.assertEqual(p.outputs, self.OUTPUTS)
        self.assertEqual(p.sources, {"FIRC": 48_000_000, "SIRC": 8_000_000, "SOSC": 8_000_000, "SPLL": 128_000_000})
        # BUS_CLK: 20 MHz with the SPLL as system clock -> DIVBUS 4 instead of 2 (the general 48 MHz limit)
        self.assertEqual((p.system_source, p.domains["bus"], p.dividers["bus"]), ("SPLL", 16_000_000, 4))
        # the functional clocks come from the divided outputs, the bus clock of LPI2C1 from the divided bus
        self.assertEqual(p.peripheral_hz["FTM1"], 32_000_000)          # SPLLDIV1 (default SPLL)
        self.assertEqual(p.peripheral_hz["FTM0"], 4_000_000)           # SOSCDIV1
        self.assertEqual(p.peripheral_hz["LPUART1"], 12_000_000)       # FIRCDIV2, console
        self.assertEqual(p.peripheral_hz["LPIT"], 2_000_000)           # SIRCDIV2
        self.assertEqual(p.peripheral_hz["FlexCAN0"], 8_000_000)       # SOSCDIV2 undivided
        self.assertEqual(p.peripheral_hz["LPI2C1"], 16_000_000)
        # the UART divider is calculated from the divided 12 MHz
        self.assertEqual(p.uart["CONSOLE"].clock_hz, 12_000_000)
        # every output within its limit, and the divider is the smallest one that achieves it
        limits = CLOCK_LIMITS["clock"]["output_max_hz"]["max_hz"]
        for output, d in p.output_dividers.items():
            self.assertLessEqual(p.outputs[output], limits[output], output)
            self.assertTrue(d == 1 or p.sources[output[:4]] / (d // 2) > limits[output], output)

    def test_without_limits_the_outputs_stay_undivided(self):
        """A derivative without output_max_hz keeps the previous behavior (all dividers 1, no silent change)."""
        config, messages = self.check()
        self.assertEqual(messages, [])
        p = config.clock
        self.assertEqual(set(p.output_dividers.values()), {1})
        self.assertEqual((p.outputs["SPLLDIV1"], p.domains["bus"]), (128_000_000, 32_000_000))

    def test_other_system_clock_source_keeps_the_general_bus_limit(self):
        """system_source_max_hz.SPLL only applies with the SPLL as system clock: FIRC 48 MHz keeps bus 48 MHz."""
        project = self.project()
        project["clock"]["system_hz"] = 48_000_000
        config, messages = self.check(project=project, mcudb=self.limited())
        self.assertEqual(messages, [])
        self.assertEqual((config.clock.system_source, config.clock.domains["bus"]), ("FIRC", 48_000_000))
        self.assertEqual(config.clock.output_dividers["SPLLDIV1"], 4)  # the PLL still runs, its limits still apply

    def test_limit_decides_whether_an_instance_fits(self):
        """FTM6 (max_hz 100 MHz) on the SPLL: undivided 128 MHz is an error, SPLLDIV1 / 4 = 32 MHz is not."""
        project = self.project()
        project["peripheral_clocks"] = {"FTM6": {"select": "SPLL"}}
        errors = [m for m in self.messages(project=project) if m.severity == "error"]
        self.assertEqual([m.yaml_path for m in errors], ["peripheral_clocks.FTM6"])
        config, messages = self.check(project=project, mcudb=self.limited())
        self.assertEqual(messages, [])
        self.assertEqual(config.clock.peripheral_hz["FTM6"], 32_000_000)

    def test_built_in_errors_with_yaml_path(self):
        def limits(**changes):
            return lambda d: d["clock"]["output_max_hz"]["max_hz"].update(changes)

        def spll(**changes):
            return lambda d: d["clock"]["system_source_max_hz"]["SPLL"]["max_hz"].update(changes)

        cases = [
            # misspelt output: the limit would silently stay unused
            (limits(SPLLDIV3=1000), "TESTK1X.yaml", "clock.output_max_hz.max_hz.SPLLDIV3",
             "SPLLDIV3 is no asynchronous clock output of TESTK1X"),
            # an output without a limit would stay undivided
            (lambda d: d["clock"]["output_max_hz"]["max_hz"].pop("SOSCDIV2"), "TESTK1X.yaml",
             "clock.output_max_hz.max_hz", "no limit for SOSCDIV2"),
            # a limit no divider reaches (128 MHz / 64 = 2 MHz)
            (limits(SPLLDIV2=1_000_000), "TESTK1X.yaml", "clock.output_max_hz.max_hz.SPLLDIV2",
             "SPLLDIV2 cannot be divided from SPLL 128 MHz to at most 1 MHz"),
            # a source or domain the family model does not know
            (lambda d: d["clock"]["system_source_max_hz"].update(PLL={"max_hz": {"bus": 1}}), "TESTK1X.yaml",
             "clock.system_source_max_hz.PLL", "PLL is no system clock source of the s32k1xx clock model"),
            (spll(apb1=1), "TESTK1X.yaml", "clock.system_source_max_hz.SPLL.max_hz.apb1",
             "apb1 is no clock domain of the s32k1xx clock model"),
            # the project's system clock is above the core limit that applies with the SPLL
            (spll(core=48_000_000), "project.yaml", "clock.system_hz",
             "system clock 64 MHz from SPLL exceeds the maximum core clock of TESTK1X with SPLL as system clock "
             "(48 MHz, clock.system_source_max_hz.SPLL.max_hz.core in TESTK1X.yaml)"),
            # DIVBUS 1..16 does not reach the bus limit that applies with the SPLL
            (spll(bus=1_000_000), "project.yaml", "clock.system_hz",
             "bus clock cannot be divided below 1 MHz (clock.system_source_max_hz.SPLL.max_hz.bus in TESTK1X.yaml"),
        ]
        for change, file, path, fragment in cases:
            with self.subTest(path=path, fragment=fragment):
                m = self.only_error(mcudb=self.limited(change))
                self.assertEqual((m.file.name, m.yaml_path), (file, path))
                self.assertIn(fragment, m.text)

    def test_built_in_errors_change_the_calculated_values(self):
        """A wrong limit must not stay unnoticed: the derived dividers and clocks change with it."""
        def limits(**changes):
            return lambda d: d["clock"]["output_max_hz"]["max_hz"].update(changes)

        cases = [
            (limits(SPLLDIV1=64_000_000), "SPLLDIV1", 2, "FTM1", 64_000_000),       # limit exactly 128 / 2
            (limits(SPLLDIV1=63_999_999), "SPLLDIV1", 4, "FTM1", 32_000_000),       # 1 Hz less: next divider
            (limits(FIRCDIV2=48_000_000), "FIRCDIV2", 1, "LPUART1", 48_000_000),
            (limits(SIRCDIV2=8_000_000), "SIRCDIV2", 1, "LPIT", 8_000_000),
            (limits(SOSCDIV1=8_000_000), "SOSCDIV1", 1, "FTM0", 8_000_000),
        ]
        for change, output, divider, instance, hz in cases:
            with self.subTest(output=output, hz=hz):
                config, _ = self.check(mcudb=self.limited(change))
                self.assertIsNotNone(config.clock)
                self.assertEqual((config.clock.output_dividers[output], config.clock.peripheral_hz[instance]),
                                 (divider, hz))
        # the bus limit with the SPLL: 32 MHz allows DIVBUS 2 again
        config, _ = self.check(mcudb=self.limited(
            lambda d: d["clock"]["system_source_max_hz"]["SPLL"]["max_hz"].update(bus=32_000_000)))
        self.assertEqual((config.clock.dividers["bus"], config.clock.peripheral_hz["LPI2C1"]), (2, 32_000_000))

    def test_repository_derivatives_and_projects_within_the_limits(self):
        """S32K1xx RM Rev. 13 Table 27-1 and 27.4 at the real derivatives and every s32k1xx project of the repo.

        S32K14x: SPLLDIV1 <= 80 MHz, SPLLDIV2 <= 40 MHz, BUS_CLK <= 40 MHz with the SPLL as system clock; the
        defaults FTM1/FTM3 = SPLL of the S32K144 then run from SPLLDIV1 within its limit (finding 2 of ARCH-20).
        """
        from nxconfig import clock as nxclock, config as cfgmod, model
        for stem in ("S32K116", "S32K142", "S32K144", "S32K146", "S32K148"):
            with self.subTest(derivative=stem):
                rep = cfgmod.Reporter()
                d = cfgmod._read(rep, "derivative", REPO / f"mcudb/s32k1xx/{stem}.yaml", model.Derivative)
                self.assertIsNotNone(d, [str(m) for m in rep.messages])
                limits = d.clock.output_max_hz
                self.assertTrue(limits is not None and limits.source, stem)
                sources = ["FIRC", "SIRC", "SOSC"] + (["SPLL"] if d.clock.pll is not None else [])
                self.assertEqual(set(limits.max_hz), {f"{s}DIV{n}" for s in sources for n in (1, 2)})
                if d.clock.pll is not None:
                    self.assertEqual((limits.max_hz["SPLLDIV1"], limits.max_hz["SPLLDIV2"]), (80_000_000, 40_000_000))
                    self.assertEqual(d.clock.system_source_max_hz["SPLL"].max_hz, {"bus": 40_000_000})
                    self.assertTrue(d.clock.system_source_max_hz["SPLL"].source)
                else:
                    self.assertEqual(d.clock.system_source_max_hz, {})
        # PLAT-62: the own test profile with the open projects; the eval board projects where they exist
        # TOOL-243c: likewise the S32K116EVB profile (private until PLAT-13)
        projects = [p for p in (EVAL_DEMO_PROJECT, EVAL_CHARGE_PROJECT) if p.is_file()] + [
            p for p in (REPO / "projects" / name / "project.yaml" for name in ("buildtest_s32k148_min",
                                                                                 "s32k116evb_minimal")) if p.is_file()] + [
            DEMO_PROJECT]
        for project_file in projects:
            name = project_file.parent.name
            with self.subTest(project=name):
                config, messages = nxconfig.check(project_file)
                self.assertEqual([m for m in messages if m.severity == "error"], [])
                p = config.clock
                limits = config.derivative.clock.output_max_hz.max_hz
                self.assertEqual(set(p.outputs), set(limits))
                for output, hz in p.outputs.items():
                    self.assertLessEqual(hz, limits[output], output)
                if p.system_source == "SPLL":
                    self.assertLessEqual(p.domains["bus"], 40_000_000)
                for gate, output in p.peripheral_output.items():
                    self.assertLessEqual(p.peripheral_hz[nxclock.S32K1_GATE_INSTANCES.get(gate, gate)],
                                         limits[output], gate)
        # the eval board: SPLL 160 MHz -> SPLLDIV1 / 2 = 80 MHz (FTM1/FTM3 default), SPLLDIV2 / 4 = 40 MHz
        config, _ = nxconfig.check(DEMO_PROJECT)
        p = config.clock
        self.assertEqual((p.sources["SPLL"], p.output_dividers["SPLLDIV1"], p.output_dividers["SPLLDIV2"]),
                         (160_000_000, 2, 4))
        self.assertEqual((p.peripheral_output["FTM1"], p.peripheral_hz["FTM1"], p.peripheral_hz["FTM3"]),
                         ("SPLLDIV1", 80_000_000, 80_000_000))
        self.assertEqual({o: d for o, d in p.output_dividers.items() if not o.startswith("SPLL")},
                         {"FIRCDIV1": 1, "FIRCDIV2": 1, "SIRCDIV1": 1, "SIRCDIV2": 1, "SOSCDIV1": 1, "SOSCDIV2": 1})
        self.assertEqual((p.domains["bus"], p.dividers["bus"]), (40_000_000, 2))


class AdcAccessTest(Profiles):
    """Serialized ADC access (adc.py, IO-19): conversion time, polling bound and the interrupt suspension check.

    Own profile testboard_s32k_8mhz/testproject_s32k_8mhz (8 MHz crystal, 64 MHz core, 32 MHz bus, one internal
    ADC channel) plus the eval board demo project, whose numbers are the ones measured at the board in IO-16.
    """

    def write(self, board=None, project=None) -> Path:
        project = copy.deepcopy(S32K_PROJECT if project is None else project)
        project["project"]["board"] = "../../boards/testboard"
        return super().write(S32K_BOARD if board is None else board, project)

    def check(self, board=None, project=None, mcudb=nxconfig.MCUDB):
        return nxconfig.check(self.write(board, project), mcudb)

    @staticmethod
    def project():
        return copy.deepcopy(S32K_PROJECT)

    @staticmethod
    def board():
        return copy.deepcopy(S32K_BOARD)

    def plan(self, board=None, project=None):
        config, messages = self.check(board, project)
        self.assertIsNotNone(config, [str(m) for m in messages])
        return config.adc_access, messages

    def test_profile_defaults(self):
        # Defaults of the module (42 + 1 + 28 + 5 = 76 ADC clocks) with the clocks of this profile:
        # ADC0 runs on FIRCDIV2 (48 MHz), bus 32 MHz, core 64 MHz.
        plan, messages = self.plan()
        self.assertEqual(messages, [])
        self.assertEqual((plan.family, plan.instances, plan.used), ("s32k1xx", ("ADC0",), True))
        self.assertEqual((plan.adc_clock_hz, plan.bus_clock_hz, plan.core_clock_hz),
                         (48_000_000, 32_000_000, 64_000_000))
        self.assertEqual((plan.resolution_bits, plan.compare_clocks, plan.conversion_clocks), (12, 28, 76))
        self.assertEqual(plan.conversion_ns, 1584 + 157)        # ceil(76/48 us) + ceil(5/32 us)
        self.assertEqual(plan.poll_cycles, 102 + 10)            # ceil(76 * 64/48) + ceil(5 * 64/32)
        self.assertEqual(plan.poll_limit, 112 * 16)
        self.assertEqual((plan.policy, plan.lock_timeout_ms, plan.max_suspend_us), ("interrupt_lock", 100, 50))

    def test_eval_board_numbers_of_io16(self):
        # The line checked at the board must not change: conversion 1709 ns (76 ADC clocks at 48000000 Hz),
        # poll limit 2192 (factor 16).
        config, messages = nxconfig.check(DEMO_PROJECT)
        self.assertEqual(findings(messages), EVAL_OPEN_POWER_STAGE)
        plan = config.adc_access
        self.assertEqual((plan.conversion_clocks, plan.adc_clock_hz), (76, 48_000_000))
        self.assertEqual((plan.conversion_ns, plan.poll_limit, plan.safety_factor), (1709, 2192, 16))
        self.assertEqual(plan.instances, ("ADC0", "ADC1"))

    def test_converter_settings_change_the_bound(self):
        cases = [
            ({"sample_clocks": 12}, 46),                        # 12 + 1 + 28 + 5
            ({"averages": 4}, 4 * 76),
            ({"clock_divider": 4}, 76),                         # same clocks, four times the time
        ]
        for values, clocks in cases:
            with self.subTest(**values):
                project = self.project()
                project.setdefault("modules", {})["bspAdcAccess"] = values
                plan, messages = self.plan(project=project)
                self.assertEqual([m for m in messages if m.severity == "error"], [])
                self.assertEqual(plan.conversion_clocks, clocks)
        slow = self.project()
        slow.setdefault("modules", {})["bspAdcAccess"] = {"clock_divider": 4}
        plan, _ = self.plan(project=slow)
        self.assertEqual((plan.adc_clock_hz, plan.conversion_ns), (12_000_000, 6334 + 157))
        self.assertEqual(plan.poll_limit, (406 + 10) * 16)      # ceil(76 * 64/12) = 406
        # a larger safety factor scales the bound, nothing else
        factor = self.project()
        factor.setdefault("modules", {})["bspAdcAccess"] = {"poll_safety_factor": 32}
        doubled, _ = self.plan(project=factor)
        base, _ = self.plan()
        self.assertEqual(doubled.poll_limit, 2 * base.poll_limit)
        self.assertEqual(doubled.conversion_ns, base.conversion_ns)

    def test_interrupt_suspension_warning(self):
        # 32 averaged conversions of 76 clocks: 2432 ADC clocks = 50667 ns, above the 50 us of the project
        project = self.project()
        project.setdefault("modules", {})["bspAdcAccess"] = {"averages": 32}
        config, messages = self.check(project=project)
        self.assertIsNotNone(config)
        (warning,) = messages
        self.assertEqual((warning.severity, warning.yaml_path, warning.file.name),
                         ("warning", "modules.bspAdcAccess.max_interrupt_suspend_us", "project.yaml"))
        self.assertIsNotNone(warning.line)
        self.assertIn("suspends the interrupts for its whole duration", warning.text)
        self.assertIn("more than the 50 us", warning.text)
        self.assertIn("rtos_mutex", warning.text)
        self.assertGreater(config.adc_access.conversion_ns, 50_000)
        # accepting the suspension removes the warning, the derived values stay the same
        project["modules"]["bspAdcAccess"]["max_interrupt_suspend_us"] = 60
        config2, messages2 = self.check(project=project)
        self.assertEqual(messages2, [])
        self.assertEqual(config2.adc_access.conversion_ns, config.adc_access.conversion_ns)

    def test_rtos_mutex_is_judged_by_the_exclusive_path(self):
        # With the mutex only callers that cannot wait suspend the interrupts, but they convert and restart the
        # conversion they interrupted: two conversions.
        project = self.project()
        project.setdefault("modules", {})["bspAdcAccess"] = {
            "lock_policy": "rtos_mutex", "averages": 16, "max_interrupt_suspend_us": 40}
        config, messages = self.check(project=project)
        plan = config.adc_access
        self.assertEqual(plan.suspend_ns, 2 * plan.conversion_ns)
        (warning,) = messages
        self.assertIn("a caller that cannot wait", warning.text)
        self.assertIn("two conversions", warning.text)
        # the same setting with the interrupt lock is judged by one conversion, which still fits
        project["modules"]["bspAdcAccess"]["lock_policy"] = "interrupt_lock"
        _, messages = self.check(project=project)
        self.assertEqual(messages, [])

    def test_family_values_only(self):
        for values, fragment, path in [
                ({"clock_divider": 3}, "the s32k1xx converter divides by 1, 2, 4, 8", "clock_divider"),
                ({"averages": 2}, "averages over 1, 4, 8, 16, 32 conversions", "averages")]:
            with self.subTest(**values):
                project = self.project()
                project.setdefault("modules", {})["bspAdcAccess"] = values
                _, messages = self.check(project=project)
                errors = [m for m in messages if m.severity == "error"]
                self.assertTrue([m for m in errors if fragment in m.text], [str(m) for m in errors])
                self.assertEqual(errors[0].yaml_path, f"modules.bspAdcAccess.{path}")
                self.assertIsNotNone(errors[0].line)

    def test_project_without_adc(self):
        # No ADC channel: the bound is still derived (the module is compiled in) but nothing is checked
        project = self.project()
        del project["adc_internal"]
        plan, messages = self.plan(project=project)
        self.assertEqual(messages, [])
        self.assertFalse(plan.used)
        self.assertEqual(plan.instances, ("ADC0", "ADC1"))      # instances of the derivative
        project.setdefault("modules", {})["bspAdcAccess"] = {"averages": 32}
        _, messages = self.check(project=project)
        self.assertEqual(messages, [])                          # no warning about a conversion that never happens

    def test_no_model_without_the_family(self):
        # Another family: no ADC timing model, therefore no derived values and no check (TESTMCU64)
        config, messages = nxconfig.check(self.write(SWITCH_BOARD, self.switch_project()), TEST_MCUDB)
        self.assertEqual(messages, [])
        self.assertIsNone(config.adc_access)
        self.assertIn("bspAdcAccess", config.integration.modules)

    @staticmethod
    def switch_project():
        project = copy.deepcopy(SWITCH_PROJECT)
        project["project"]["board"] = "../../boards/testboard"
        return project


class ProjectModuleParameterTest(Profiles):
    """Modules without a board device (module.yaml project_parameters, IO-19): project.yaml modules.<module>."""

    def write(self, board=None, project=None) -> Path:
        project = copy.deepcopy(SWITCH_PROJECT if project is None else project)
        project["project"]["board"] = "../../boards/testboard"
        return super().write(SWITCH_BOARD if board is None else board, project)

    @staticmethod
    def project():
        return copy.deepcopy(SWITCH_PROJECT)

    def modules_of(self, project=None):
        config, messages = self.check(project=project)
        self.assertIsNotNone(config, [str(m) for m in messages])
        return config.integration.modules, messages

    def test_defaults_without_a_project_entry(self):
        # The module is part of the build whether the project names it or not: the defaults apply
        uses, messages = self.modules_of()
        self.assertEqual(messages, [])
        self.assertIn("bspAdcAccess", uses)
        use = uses["bspAdcAccess"]
        self.assertEqual(use.parameters["lock_policy"], "interrupt_lock")
        self.assertEqual(use.parameters["poll_safety_factor"], 16)
        self.assertFalse(use.verify)

    def test_project_values_and_verify(self):
        project = self.project()
        project["modules"]["bspAdcAccess"] = {"poll_safety_factor": 8, "lock_policy": "rtos_mutex", "verify": True}
        uses, messages = self.modules_of(project)
        self.assertEqual(messages, [])
        use = uses["bspAdcAccess"]
        self.assertEqual((use.parameters["poll_safety_factor"], use.parameters["lock_policy"]), (8, "rtos_mutex"))
        self.assertEqual(use.parameters["sample_clocks"], 42)       # untouched default
        self.assertTrue(use.verify)

    def test_parameter_errors_have_path_and_line(self):
        cases = [
            ({"poll_safety_factor": 0}, "modules.bspAdcAccess.poll_safety_factor", "is out of range 1..4096"),
            ({"lock_policy": "spinlock"}, "modules.bspAdcAccess.lock_policy", "does not match"),
            ({"sample_clocks": True}, "modules.bspAdcAccess.sample_clocks", "expected an integer, got True"),
            ({"colour": 1}, "modules.bspAdcAccess.colour", "module bspAdcAccess has no parameter colour"),
        ]
        for values, path, fragment in cases:
            with self.subTest(fragment=fragment):
                project = self.project()
                project["modules"]["bspAdcAccess"] = values
                config, messages = self.check(project=project)
                self.assertIsNone(config)
                match = [m for m in messages if fragment in m.text]
                self.assertTrue(match, [str(m) for m in messages])
                self.assertEqual((match[0].yaml_path, match[0].file.name, match[0].severity),
                                 (path, "project.yaml", "error"))
                self.assertIsNotNone(match[0].line)

    def test_unknown_key_names_both_possibilities(self):
        project = self.project()
        project["modules"]["NOPE"] = {}
        error = self.expect("has no device NOPE", project=project, level="project", path="modules.NOPE")
        self.assertIn("devices with a module: LAMP_SW, PUMP_SW", error.text)
        self.assertIn("modules with parameters: bspAdcAccess", error.text)

    def test_device_driver_cannot_be_configured_as_a_module(self):
        # highSideSwitch has instances: its values belong to a device, not to the module
        project = self.project()
        project["modules"]["highSideSwitch"] = {"cycle_ms": 10}
        error = self.expect("takes no parameters from the project", project=project, path="modules.highSideSwitch")
        self.assertIn("module highSideSwitch (device_driver)", error.text)

    def test_manifest_errors(self):
        base = "module: {name}\nkind: {kind}\nproject_parameters: true\n"
        cases = [
            ("driverWithParams", base.format(name="driverWithParams", kind="device_driver")
             + "parts: [X1]\nparameters:\n  a: { type: int, min: 0, max: 1 }\n",
             "is a device driver: its parameters are values of an instance"),
            ("noParams", base.format(name="noParams", kind="service"),
             "has no parameters the project could set"),
            ("wiredService", base.format(name="wiredService", kind="service")
             + "connect:\n  in: { kind: digital_output }\nparameters:\n  a: { type: int, min: 0, max: 1 }\n",
             "needs a board device"),
        ]
        for name, text, fragment in cases:
            with self.subTest(module=name):
                base_dir = self.tmp / "modules" / name
                base_dir.mkdir(parents=True, exist_ok=True)
                (base_dir / "module.yaml").write_text(text, encoding="utf-8")
                dirs = (*nxconfig_config.MODULE_DIRS, self.tmp / "modules")
                _, messages = nxconfig.check(self.write(), TEST_MCUDB, dirs)
                match = [m for m in messages if fragment in m.text]
                self.assertTrue(match, [str(m) for m in messages])
                self.assertEqual((match[0].file.name, match[0].yaml_path), ("module.yaml", "project_parameters"))
                self.assertIsNotNone(match[0].line)
                shutil.rmtree(base_dir)

    def test_port_modules_come_from_the_family_template_and_the_board_port(self):
        # PLAT-23: safeSystem has its manifest in the family port template (platforms/s32k1xx/port), so every
        # board of the family finds it - before, the build-test board needed a copy of the file (ARCH-09 B6).
        # Nothing in the tool names a board or a family: both come from the project (config.port_module_dirs).
        family_port = REPO / "platforms" / "s32k1xx" / "port"
        # PLAT-62: the eval board (with a board port under ecu/platforms) where its project exists
        for project, board in (*([(EVAL_DEMO_PROJECT, "evalboard_s32k144")] if EVAL_DEMO_PROJECT.is_file() else []),
                               (REPO / "projects/buildtest_s32k148_min/project.yaml", "buildtest_s32k148")):
            with self.subTest(project=project.parent.name):
                config, _ = nxconfig.check(project)
                self.assertIsNotNone(config)
                self.assertIn("safeSystem", config.integration.modules)
                # ARCH-32: tolerance of the reference check, default of the manifest (the nominal value is a board fact)
                self.assertEqual(config.integration.parameters("safeSystem"),
                                 {"adc_read_error_limit": 10, "adc_reference_tolerance_mv": 200})
                self.assertEqual(nxconfig_config.port_module_dirs(board, "s32k1xx"),
                                 (family_port, REPO / "ecu" / "platforms" / board))
        # a board without a port and a family without a template simply contribute nothing
        self.assertEqual(nxconfig_config.port_module_dirs("no_such_board", "s32k1xx"), (family_port,))
        self.assertEqual(nxconfig_config.port_module_dirs("no_such_board", "no_such_family"), ())
        uses, _ = self.modules_of()
        self.assertNotIn("safeSystem", uses)


class EcuStateProjectParameterTest(Profiles):
    """SYS-08: the service ecuState takes its parameters from project.yaml modules.ecuState (project_parameters),
    checked here with file, line and YAML path - on the synthetic profile with the CAN buses VEH and DIAG."""

    def with_values(self, values):
        project = self.project()
        project.setdefault("modules", {})["ecuState"] = values
        return project

    def test_defaults_and_project_values(self):
        config, messages = self.check()
        self.assertIsNotNone(config, [str(m) for m in messages])
        defaults = config.integration.parameters("ecuState")
        # the values the user confirmed for SYS-01 are the defaults of the module (unchanged by SYS-08)
        self.assertEqual({k: defaults[k] for k in ("min_run_ms", "post_run_ms", "shutdown_timeout_ms",
                                                   "sleep_timeout_ms", "wakeup_during_shutdown", "after_watchdog_reset")},
                         {"min_run_ms": 5000, "post_run_ms": 2000, "shutdown_timeout_ms": 5000, "sleep_timeout_ms": 2000,
                          "wakeup_during_shutdown": "restart", "after_watchdog_reset": "run"})
        config, messages = self.check(project=self.with_values({"wakeup_can": "DIAG", "wakeup_local": "LOCAL0,LOCAL3",
                                                                "verify": True}))
        self.assertIsNotNone(config, [str(m) for m in messages])
        use = config.integration.modules["ecuState"]
        self.assertEqual((use.parameters["wakeup_can"], use.parameters["wakeup_local"], use.verify),
                         ("DIAG", "LOCAL0,LOCAL3", True))
        for text in ("all", "none", "VEH,DIAG"):
            with self.subTest(wakeup_can=text):
                config, messages = self.check(project=self.with_values({"wakeup_can": text}))
                self.assertIsNotNone(config, [str(m) for m in messages])

    def test_names_are_checked_with_line_and_path(self):
        cases = [
            ({"wakeup_can": "CAN0"}, "wakeup_can", "wakeup_can: CAN0 is no CAN bus of the project (use.can: VEH, DIAG)"),
            ({"wakeup_can": "VEH,DIAG,VEH"}, "wakeup_can", "wakeup_can: a CAN bus is named twice (VEH,DIAG,VEH)"),
            ({"wakeup_local": "LOCAL2,LOCAL2"}, "wakeup_local", "wakeup_local: a name is named twice (LOCAL2,LOCAL2)"),
            ({"sleep_timeout_ms": 100}, "sleep_timeout_ms", "sleep_timeout_ms: 100 is out of range 101..3600000 ms"),
        ]
        for values, name, fragment in cases:
            with self.subTest(values=values):
                error = self.expect(fragment, project=self.with_values(values), level="project",
                                    path=f"modules.ecuState.{name}")
                self.assertIsNotNone(error.line)

    def test_unused_bus_is_no_name(self):
        # DIAG exists on the board, but a project that does not use it cannot wake up over it
        project = self.with_values({"wakeup_can": "DIAG"})
        project["use"]["can"] = ["VEH"]
        project["can"].pop("DIAG")
        self.expect("DIAG is no CAN bus of the project (use.can: VEH)", project=project,
                    path="modules.ecuState.wakeup_can")

    def test_names_need_a_str_parameter(self):
        text = ("module: namesOnInt\nkind: service\nproject_parameters: true\n"
                "parameters:\n  a: { type: int, min: 0, max: 1, names: can_bus }\n")
        base_dir = self.tmp / "modules" / "namesOnInt"
        base_dir.mkdir(parents=True)
        (base_dir / "module.yaml").write_text(text, encoding="utf-8")
        _, messages = nxconfig.check(self.write(), TEST_MCUDB, (*nxconfig_config.MODULE_DIRS, self.tmp / "modules"))
        match = [m for m in messages if "names only applies to str parameters" in m.text]
        self.assertTrue(match, [str(m) for m in messages])
        self.assertEqual((match[0].file.name, match[0].yaml_path), ("module.yaml", "parameters.a.names"))


S32K146_CAN_BOARD = yaml.safe_load((HERE / "test/profiles/boards/testboard_s32k146_can/board.yaml")
                                   .read_text(encoding="utf-8"))
S32K146_CAN_PROJECT = yaml.safe_load((HERE / "test/profiles/projects/testproject_s32k146_can/project.yaml")
                                     .read_text(encoding="utf-8"))


class MessageBufferTest(Profiles):
    """ARCH-42: a used CAN bus on a FlexCAN instance with fewer message buffers than the family driver needs.

    Own profile testboard_s32k146_can/testproject_s32k146_can (S32K146: FlexCAN0/1 with 32 buffers, FlexCAN2 with
    16, mcudb message_buffers from S32K-RM Table 55-1). FlexCANDevice of OpenBSW transmitted from buffers 16..31, so
    a bus on FlexCAN2 would stop the ECU at start-up; since COM-26 the family port's driver splits the buffers of each
    instance. The check itself is kept for a driver with a larger requirement (openbsw_driver(): 32, the old one) and
    reports with file, line and YAML path.
    """

    @staticmethod
    def openbsw_driver():
        """The requirement before COM-26: FlexCANDevice of OpenBSW needed 32 buffers on every instance."""
        from unittest import mock
        return mock.patch.dict(checks.CAN_DRIVER_MESSAGE_BUFFERS,
                               {"s32k1xx": (32, "FlexCANDevice transmits from buffers 16..31")})

    def write(self, board=None, project=None) -> Path:
        project = copy.deepcopy(S32K146_CAN_PROJECT if project is None else project)
        project["project"]["board"] = "../../boards/testboard"
        return super().write(copy.deepcopy(S32K146_CAN_BOARD) if board is None else board, project)

    def check(self, board=None, project=None, mcudb=nxconfig.MCUDB):
        return nxconfig.check(self.write(board, project), mcudb)

    def errors(self, board=None, project=None, mcudb=nxconfig.MCUDB):
        _, messages = self.check(board, project, mcudb)
        return [m for m in messages if m.severity == "error"]

    @staticmethod
    def with_service(use):
        project = copy.deepcopy(S32K146_CAN_PROJECT)
        project["use"]["can"] = use
        project["interrupts"]["ranks"].append({"source": "can", "name": "SERVICE", "rank": 3})
        return project

    def test_buses_on_instances_with_32_buffers_are_ok(self):
        config, messages = self.check()
        self.assertEqual(messages, [])
        self.assertEqual([(b.name, config.derivative.instances[b.instance].message_buffers)
                          for b in config.selected("can")], [("POWERTRAIN", 32), ("BODY", 32)])

    def test_bus_on_an_instance_with_16_buffers_is_served_since_com26(self):
        config, messages = self.check(project=self.with_service(["POWERTRAIN", "SERVICE", "BODY"]))
        self.assertEqual([str(m) for m in messages if m.severity == "error"], [])
        service = next(b for b in config.board.can if b.name == "SERVICE")
        self.assertEqual(config.derivative.instances[service.instance].message_buffers, 16)
        self.assertIsNone(checks.can_message_buffers_problem(config, service))
        # the smallest instance the split serves (tools/bswgen CAN_MIN_MESSAGE_BUFFERS)
        self.assertEqual(checks.FLEXCAN_DRIVER_MESSAGE_BUFFERS, 8)

    def test_bus_on_an_instance_with_16_buffers_is_an_error_at_its_use_entry(self):
        with self.openbsw_driver():
            (error,) = self.errors(project=self.with_service(["POWERTRAIN", "SERVICE", "BODY"]))
        self.assertEqual((error.file.name, error.yaml_path), ("project.yaml", "use.can[1]"))
        self.assertIsInstance(error.line, int)
        self.assertEqual(error.text, "CAN bus SERVICE is on FlexCAN2, which has 16 message buffers on the S32K146; the "
                         "CAN driver of s32k1xx needs 32 (FlexCANDevice transmits from buffers 16..31): the first "
                         "access to buffer 16 is an access error and the ECU stops when CAN starts - use an instance "
                         "with 32 buffers or leave SERVICE out of use.can (the bootloader may still use it)")
        config = nxconfig.load(self.write())               # the text bswgen repeats as its last line of defence
        service = next(b for b in config.board.can if b.name == "SERVICE")
        with self.openbsw_driver():
            self.assertEqual(error.text, checks.can_message_buffers_problem(config, service))

    def test_use_all_reports_at_use_can(self):
        with self.openbsw_driver():
            (error,) = self.errors(project=self.with_service("all"))
        self.assertEqual((error.yaml_path, error.text.split(" is on")[0]), ("use.can", "CAN bus SERVICE"))
        self.assertIsInstance(error.line, int)

    def test_the_same_instance_on_another_derivative(self):
        # FlexCAN2 of the S32K148 has 32 buffers: the same board with the bigger derivative is fine
        board = copy.deepcopy(S32K146_CAN_BOARD)
        board["board"].update(mcu="S32K148", package="LQFP100")
        self.assertEqual(self.errors(board, self.with_service("all")), [])
        # S32K142: FlexCAN1 has 16 buffers (and there is no FlexCAN2)
        board["board"].update(mcu="S32K142", package="LQFP64")
        board["can"] = board["can"][:2]
        board["devices"] = board["devices"][:2]
        project = copy.deepcopy(S32K146_CAN_PROJECT)
        project["can"].pop("SERVICE")
        project["clock"]["system_hz"] = 48_000_000
        self.assertEqual(self.errors(board, project), [])               # COM-26: 16 buffers are served
        with self.openbsw_driver():
            (error,) = self.errors(board, project)
        self.assertEqual(error.yaml_path, "use.can[1]")
        self.assertIn("CAN bus BODY is on FlexCAN1, which has 16 message buffers on the S32K142", error.text)

    def test_unknown_buffers_and_other_families_are_not_checked(self):
        # message_buffers is optional (None: unknown), and only families with a known driver requirement are checked
        self.assertEqual(set(checks.CAN_DRIVER_MESSAGE_BUFFERS), {"s32k1xx"})
        self.assertEqual(checks.CAN_DRIVER_MESSAGE_BUFFERS["s32k1xx"][0], checks.FLEXCAN_DRIVER_MESSAGE_BUFFERS)
        config = nxconfig.load(self.write())
        service = next(b for b in config.board.can if b.name == "SERVICE")
        with self.openbsw_driver():
            self.assertIsNotNone(checks.can_message_buffers_problem(config, service))
        config.derivative = config.derivative.model_copy(update={"instances": {
            **config.derivative.instances,
            "FlexCAN2": config.derivative.instances["FlexCAN2"].model_copy(update={"message_buffers": None})}})
        with self.openbsw_driver():
            self.assertIsNone(checks.can_message_buffers_problem(config, service))

    def test_every_flexcan_instance_of_the_s32k1xx_derivatives_has_its_buffers(self):
        """S32K-RM Rev. 13 Table 55-1 "FlexCAN instances and features" (p. 1787)."""
        expected = {"S32K116": [32], "S32K142": [32, 16], "S32K144": [32, 16, 16], "S32K146": [32, 32, 16],
                    "S32K148": [32, 32, 32]}
        for name, buffers in expected.items():
            with self.subTest(derivative=name):
                doc = yaml.safe_load((REPO / "mcudb/s32k1xx" / f"{name}.yaml").read_text(encoding="utf-8"))
                can = {k: v for k, v in doc["instances"].items() if v["class"] == "can"}
                self.assertEqual([can[f"FlexCAN{i}"].get("message_buffers") for i in range(len(can))], buffers)


BL_PROJECT =yaml.safe_load((HERE / "test/profiles/projects/testproject_bootloader/project.yaml")
                            .read_text(encoding="utf-8"))
S32K142_BL_BOARD = yaml.safe_load((HERE / "test/profiles/boards/testboard_s32k142_bl/board.yaml")
                                  .read_text(encoding="utf-8"))
S32K142_BL_PROJECT = yaml.safe_load((HERE / "test/profiles/projects/testproject_s32k142_bl/project.yaml")
                                    .read_text(encoding="utf-8"))


class MemoryTest(Profiles):
    """Memory partitioning and bootloader section (memory.py, ARCH-08) with the synthetic profile
    testproject_bootloader (flash at 0x08000000, one RAM region, no SBC), the S32K142 profile and the eval board."""

    def project(self):
        project = copy.deepcopy(BL_PROJECT)
        project["project"]["board"] = "../../boards/testboard"
        return project

    def layout(self, board=None, project=None, mcudb=TEST_MCUDB):
        config, messages = self.check(board, self.project() if project is None else project, mcudb)
        self.assertEqual([str(m) for m in messages if m.severity == "error"], [])
        return config

    def expect(self, fragment, board=None, project=None, level="project", path=None, mcudb=TEST_MCUDB):
        _, messages = self.check(board, project, mcudb)
        errors = [m for m in messages if m.severity == "error"]
        match = [m for m in errors if fragment in m.text]
        self.assertTrue(match, f"'{fragment}' not in {[str(m) for m in errors]}")
        self.assertEqual((match[0].file.name, match[0].yaml_path), (f"{level}.yaml", path))
        self.assertIsInstance(match[0].line, int)
        return match[0]

    @staticmethod
    def values(layout) -> dict:
        return {"app": (layout.app_start, layout.app_end), "record": layout.record_address,
                "bootloader_ram": (layout.bootloader_ram.start, layout.bootloader_ram.size),
                "ram": (layout.ram_start, layout.ram_end), "noinit": layout.noinit_start,
                "boot_request": layout.boot_request_address, "sector": layout.sector_size,
                "phrase": layout.program_size}

    def test_profile(self):
        config = self.layout()
        self.assertEqual(self.values(config.memory), {
            "app": (0x08004000, 0x0803C000), "record": 0x0803E000, "bootloader_ram": (0x20000000, 0x2000),
            "ram": (0x20000000, 0x20010000), "noinit": 0x2000FE00, "boot_request": 0x2000FFF0,
            "sector": 0x2000, "phrase": 8})
        self.assertEqual((config.bootloader.can, config.bootloader.hooks), ("DIAG", ()))

    def test_eval_board_layout_equals_the_flashed_bootloader(self):
        """Values of bl_config.h, the bootloader/updater linker scripts and application.dld.in before ARCH-08 -
        except the application end: BL-03 puts the device key into the sector below the record (0x7E000)."""
        config, messages = nxconfig.check(DEMO_PROJECT)
        self.assertEqual(findings(messages), EVAL_OPEN_POWER_STAGE)
        self.assertEqual((config.memory.key_address, config.memory.key_size), (0x7E000, 0x1000))
        self.assertEqual(self.values(config.memory), {
            "app": (0x8000, 0x7E000), "record": 0x7F000, "bootloader_ram": (0x1FFF8000, 0x8000),
            "ram": (0x1FFF8000, 0x20007000), "noinit": 0x20006C00, "boot_request": 0x20006FF0,
            "sector": 0x1000, "phrase": 8})
        self.assertEqual((config.memory.flash_start, config.memory.flash_size, config.memory.stack_size),
                         (0, 0x80000, 0x1000))
        self.assertEqual([(r.region, r.start, r.size) for r in config.memory.app_ram],
                         [("SRAM_L", 0x1FFF8000, 0x8000), ("SRAM_U", 0x20000000, 0x7000)])
        # the SBC is the transceiver of CAN0 and has a bootloader hook: switched on before CAN
        self.assertEqual([(h.device, h.module, h.init) for h in config.bootloader.hooks], [("SBC", "sbcUja113x", True)])
        self.assertEqual(config.clock.reset_core_hz, 48_000_000)

    def test_sbc_watchdog_default_leave_and_serve(self):
        """BL-28: bootloader.sbc_watchdog - default leave, serve only with an SBC the bootloader switches on whose
        hook can serve its watchdog; messages with YAML path. Eval board SBC and the synthetic profile without SBC."""
        board = yaml.safe_load(FULL_BOARD.read_text(encoding="utf-8"))      # PLAT-62: SBC and passive CAN1
        project = yaml.safe_load(DEMO_PROJECT.read_text(encoding="utf-8"))
        project["project"]["board"] = "../../boards/testboard"
        project["project"].pop("rte")
        project["project"].pop("diag", None)
        plan = self.layout(board, project, nxconfig.MCUDB).bootloader
        self.assertEqual((plan.sbc_watchdog, plan.watchdog_served_by), ("leave", ()))
        self.assertEqual([(h.device, h.init, h.serve_watchdog) for h in plan.hooks], [("SBC", True, False)])

        project["bootloader"]["sbc_watchdog"] = "serve"
        plan = self.layout(board, project, nxconfig.MCUDB).bootloader
        self.assertEqual((plan.sbc_watchdog, plan.watchdog_served_by), ("serve", ("sbcUja113x",)))
        self.assertEqual([(h.device, h.init, h.serve_watchdog) for h in plan.hooks], [("SBC", True, True)])
        # the bus override of the build (BOOTLOADER_CAN_BUS CAN1, passive transceiver): the SBC is only read then
        rep = nxconfig.config.Reporter()
        self.assertIsNone(nxconfig.memory.bootloader_plan(self.layout(board, project, nxconfig.MCUDB), rep, "CAN1"))
        self.assertIn("serve needs the SBC switched on by the bootloader: SBC is only read",
                      [m.text for m in rep.messages if m.severity == "error"][0])
        project["bootloader"] = {"can": "CAN1", "sbc_watchdog": "serve"}
        self.expect("serve needs the SBC switched on by the bootloader: SBC is only read (add it to bootloader.init)",
                    board, project, path="bootloader.sbc_watchdog", mcudb=nxconfig.MCUDB)
        project["bootloader"] = {"can": "CAN1", "init": ["SBC"], "sbc_watchdog": "serve"}
        self.assertEqual(self.layout(board, project, nxconfig.MCUDB).bootloader.watchdog_served_by, ("sbcUja113x",))
        project["bootloader"]["sbc_watchdog"] = "on"
        self.expect("Input should be 'leave' or 'serve'", board, project, path="bootloader.sbc_watchdog",
                    mcudb=nxconfig.MCUDB)

    def test_sbc_watchdog_serve_without_sbc(self):
        """BL-28, architecture question 1: a board without SBC keeps leave; serve is refused with its YAML path."""
        project = self.project()
        self.assertEqual(self.layout(project=project).bootloader.sbc_watchdog, "leave")
        project["bootloader"]["sbc_watchdog"] = "serve"
        self.expect("serve needs a used device whose bootloader hook serves an SBC watchdog (modules: sbcUja113x)",
                    project=project, path="bootloader.sbc_watchdog")

    def test_other_bus_default_and_explicit_init(self):
        board = yaml.safe_load(FULL_BOARD.read_text(encoding="utf-8"))      # PLAT-62: SBC and passive CAN1
        project = yaml.safe_load(DEMO_PROJECT.read_text(encoding="utf-8"))
        project["project"]["board"] = "../../boards/testboard"
        project["project"].pop("rte")
        project["project"].pop("diag", None)
        config = self.layout(board, project, nxconfig.MCUDB)
        plan = nxconfig.memory.bootloader_plan(config, nxconfig.config.Reporter(), "CAN1")
        # DIAG_PHY is passive: the SBC is only read (watchdog state for the updater)
        self.assertEqual((plan.can, [(h.device, h.init) for h in plan.hooks]), ("CAN1", [("SBC", False)]))
        project["bootloader"] = {"can": "CAN1", "init": ["SBC"]}
        config = self.layout(board, project, nxconfig.MCUDB)
        self.assertEqual([(h.device, h.init) for h in config.bootloader.hooks], [("SBC", True)])
        # without the SBC's SPI bus the transceiver of CAN0 cannot be switched on
        project["bootloader"] = {"can": "CAN0"}
        project["use"]["spi"] = ["MOT1_GD_SPI"]
        # no CAN bus in the application (and so no CAN bus to wake up from)
        project["use"]["can"] = []
        project["modules"]["ecuState"]["wakeup_can"] = "none"
        only_used_can_ranks(project)
        self.expect("transceiver SBC of CAN0 needs its bootloader hook, but the project does not use device SBC",
                    board, project, path="bootloader.can", mcudb=nxconfig.MCUDB)

    def test_s32k142_profile(self):
        project = copy.deepcopy(S32K142_BL_PROJECT)
        project["project"]["board"] = "../../boards/testboard"
        config = self.layout(S32K142_BL_BOARD, project, nxconfig.MCUDB)
        self.assertEqual(self.values(config.memory), {
            "app": (0x6000, 0x3F000), "record": 0x3F800, "bootloader_ram": (0x1FFFC000, 0x4000),
            "ram": (0x1FFFC000, 0x20003000), "noinit": 0x20002D00, "boot_request": 0x20002FF0,
            "sector": 0x800, "phrase": 8})
        # the bootloader bus gets its bit timing although the project lists all buses anyway
        diag = config.clock.can["DIAG"]
        self.assertEqual((diag.prescaler, diag.prop_seg, diag.phase_seg1, diag.phase_seg2, diag.sjw), (4, 7, 4, 4, 4))
        # the application uses VEH only: the clock model still calculates DIAG for the bootloader
        project["use"]["can"] = ["VEH"]
        config = self.layout(S32K142_BL_BOARD, project, nxconfig.MCUDB)
        self.assertEqual(sorted(config.clock.can), ["DIAG", "VEH"])

    def test_bootloader_bus_not_used_by_the_application(self):
        project = self.project()
        project["use"]["can"] = ["VEH"]
        config, messages = self.check(project=project)
        self.assertEqual(messages, [])
        # ARCH-07: a configured bus the application does not use is not reported either - its bit timing is
        # generated for the bootloader bus and for the build option BOOTLOADER_CAN_BUS (ClockTest)
        project["bootloader"]["can"] = "VEH"
        _, messages = self.check(project=project)
        self.assertEqual(messages, [])

    def test_errors_with_yaml_path(self):
        def mem(change):
            def apply(project):
                change(project["memory"])
            return apply

        cases = [
            (mem(lambda m: m.update(flash="RAM")), "TESTMCU64 has no code_flash region RAM (available: FLASH)",
             "memory.flash"),
            (mem(lambda m: m["bootloader"].update(size=0x5000)),
             "bootloader size 0x5000 ends inside a sector of FLASH (nearest sector boundaries 0x4000, 0x6000)",
             "memory.bootloader.size"),
            (mem(lambda m: m["bootloader"].update(size=0x3E000)), "leaves no application area in FLASH",
             "memory.bootloader.size"),
            (mem(lambda m: m["bootloader"].update(ram="FLASH")), "TESTMCU64 has no ram region FLASH",
             "memory.bootloader.ram"),
            (mem(lambda m: m["bootloader"].update(ram_size=0x20000)), "bootloader RAM 0x20000 exceeds RAM",
             "memory.bootloader.ram_size"),
            (mem(lambda m: m["bootloader"].update(stack_size=0x2000)), "stack 0x2000 leaves no bootloader RAM",
             "memory.bootloader.stack_size"),
            (mem(lambda m: m["bootloader"].pop("ram_size")),
             "bootloader RAM 0x20000000..0x2000FFFF overlaps the application no-init area 0x2000FE00..0x2000FFFF",
             "memory.bootloader.ram"),
            (mem(lambda m: m["application"].update(ram=["RAM", "RAM"])), "RAM region RAM listed twice",
             "memory.application.ram[1]"),
            (mem(lambda m: m["application"].update(noinit_size=0x20000)), "no-init area 0x20000 exceeds RAM",
             "memory.application.noinit_size"),
            (mem(lambda m: m["application"].update(noinit_size=0x0E)), "must be a multiple of 4 and hold the boot request",
             "memory.application.noinit_size"),
            (lambda p: p.pop("memory"), "the bootloader needs the memory partitioning (memory)", "bootloader"),
            (lambda p: p["bootloader"].update(can="BODY"), "board testboard has no CAN bus BODY (available: VEH, DIAG)",
             "bootloader.can"),
            (lambda p: p["can"].pop("DIAG"), "CAN DIAG of the bootloader needs its bit rate: can.DIAG.bitrate is missing",
             "bootloader.can"),
            (lambda p: p["bootloader"].update(init=["PHY_B"]), "device PHY_B (canPhyPassive) has no bootloader hook",
             "bootloader.init[0]"),
            (lambda p: p["bootloader"].update(init=["EEPROM"]), "device EEPROM is not used by the project",
             "bootloader.init[0]"),
            (lambda p: p["memory"].update(nvm=1), "Extra inputs are not permitted", "memory.nvm"),
        ]
        for change, fragment, path in cases:
            with self.subTest(fragment=fragment):
                project = self.project()
                change(project)
                self.expect(fragment, project=project, path=path)

    def test_noinit_minimum_of_the_family(self):
        """PLAT-29 (finding of PLAT-12): nxconfig accepted noinit_size 0x100 and only the linker script refused it
        (hard fault dump 0x144 + failed-start record 8 + boot request 0x10). The minimum comes from the family: the
        synthetic family of the test profile gets the layout header of s32k1xx in a temporary tree."""
        def noinit(value):
            project = self.project()
            project["memory"]["application"]["noinit_size"] = value
            return project
        header = (nxconfig_config.family_ports_dir() / "s32k1xx" / "hardFaultHandler" / "include" / "hardFaultHandler"
                  / "hardFaultDumpLayout.h")
        with tempfile.TemporaryDirectory() as tree:
            target = Path(tree) / "testfam" / "hardFaultHandler" / "include" / "hardFaultHandler"
            target.mkdir(parents=True)
            shutil.copy(header, target)
            with mock.patch.object(memory, "family_ports_dir", return_value=Path(tree)):
                config = self.layout(project=noinit(0x15C))               # exactly the minimum
                minimum, text = memory.noinit_minimum(config)
                self.assertEqual(minimum, 0x15C)
                self.assertEqual(text, "hard fault dump 0x144 + failed-start record 0x8 + boot request 0x10")
                message = self.expect("no-init area 0x100 is smaller than the minimum 0x15C of the family testfam",
                                      project=noinit(0x100), path="memory.application.noinit_size")
                self.assertIn(text, message.text)
                self.expect("smaller than the minimum 0x15C", project=noinit(0x158),
                            path="memory.application.noinit_size")
        # without the layout header (this family has none) only the boot request counts, as before
        self.assertEqual(memory.noinit_minimum(self.layout(project=noinit(0x100))), (0x10, "boot request 0x10"))

    def test_noinit_minimum_of_the_real_family_and_the_profiles(self):
        """s32k1xx: the value of the linker script header; every project of the repository keeps it with room."""
        fake = types.SimpleNamespace(derivative=types.SimpleNamespace(derivative=types.SimpleNamespace(
            family="s32k1xx")), running_tree=nxconfig_config.REPO)
        self.assertEqual(memory.noinit_minimum(fake)[0], 0x15C)
        for project in sorted((nxconfig_config.REPO / "projects").glob("*/project.yaml")):
            data = yaml.safe_load(project.read_text(encoding="utf-8"))
            application = (data.get("memory") or {}).get("application") or {}
            if "noinit_size" in application and not project.parent.name.startswith("nucleo"):
                with self.subTest(project=project.parent.name):
                    self.assertGreaterEqual(application["noinit_size"], 0x15C)

    def test_hot_ram_part_as_project_value(self):
        """PLAT-12: RAM functions and hot part of the application RAM from the project, checked against the RAM."""
        def mem(change):
            def apply(p):
                change(p["memory"]["application"])
            return apply
        project = self.project()
        project["memory"]["application"].update(ramfunctions_size=0x400, hot_ram_size=0x800)
        layout = self.layout(project=project).memory
        self.assertEqual((layout.ramfunctions_size, layout.hot_ram_size), (0x400, 0x800))
        self.assertEqual((self.layout().memory.ramfunctions_size, self.layout().memory.hot_ram_size), (None, None))
        cases = [
            (mem(lambda a: a.update(ramfunctions_size=0x402)), "ramfunctions_size 0x402 must be a multiple of 4",
             "memory.application.ramfunctions_size"),
            (mem(lambda a: a.update(ramfunctions_size=0x400, hot_ram_size=0x400)),
             "hot RAM 0x400 leaves no room for hot data behind the RAM functions (0x400 bytes",
             "memory.application.hot_ram_size"),
            (mem(lambda a: a.update(hot_ram_size=0x800)),       # default RAM functions 0x1000 of the template
             "hot RAM 0x800 leaves no room for hot data behind the RAM functions (0x1000 bytes",
             "memory.application.hot_ram_size"),
            (mem(lambda a: a.update(hot_ram_size=0x10000)), "leave no room for data, heap and stack",
             "memory.application.hot_ram_size"),
            (mem(lambda a: a.update(ramfunctions_size=0x10000)), "RAM functions 0x10000 and no-init area",
             "memory.application.ramfunctions_size"),
        ]
        for change, fragment, path in cases:
            with self.subTest(fragment=fragment):
                project = self.project()
                change(project)
                self.expect(fragment, project=project, path=path)

    def test_build_settings_features_and_pll(self):
        """PLAT-12: NX_FEATURES and NX_CLOCK_PLL for the platform CMake (derivative without cache/SPLL)."""
        settings = nxconfig_config.build_settings(nxconfig.load(DEMO_PROJECT, nxconfig.MCUDB))[0]
        self.assertEqual((settings["NX_FEATURES"], settings["NX_CLOCK_PLL"]), (["lmem_cache"], "ON"))
        settings = nxconfig_config.build_settings(nxconfig.load(REPO / "projects/s32k116evb_minimal/project.yaml", nxconfig.MCUDB))[0]
        self.assertEqual((settings["NX_FEATURES"], settings["NX_CLOCK_PLL"]), ([], "OFF"))
        self.assertEqual(settings["NX_CORE_ARCHITECTURE"], "armv6m")

    def test_s32k116evb_profile_checks(self):
        """PLAT-12: the S32K116EVB profile passes, the SBC without driver is reported as passive transceiver."""
        config, messages = nxconfig.check(REPO / "projects/s32k116evb_minimal/project.yaml")
        self.assertIsNotNone(config, [str(m) for m in messages])
        self.assertEqual([m.text for m in messages if m.severity == "error"], [])
        # PLAT-12: the message about the SBC without driver belongs to a CAN bus the application uses; the lean
        # profile has none (use.can empty, the bootloader keeps CAN0), so it is not reported any more
        self.assertFalse(any("UJA1169" in m.text and "passive transceiver" in m.text for m in messages))
        # PLAT-32: checked-in RTD header (hal_nxp) with adapter, no longer generated from the workstation SVD
        self.assertIsNone(config.derivative.device_header.generated_by)
        self.assertEqual(config.derivative.device_header.file, "3rdparty/nxp/S32K116.h")
        self.assertIsNone(config.derivative.clock.pll)

    def test_application_ram_must_be_contiguous(self):
        def derivative(d):
            d["memory"].append({"name": "RAM2", "kind": "ram", "start": 0x20020000, "size": 0x1000})
        mcudb = PinmuxTest.derivative(self, derivative)
        project = self.project()
        project["memory"]["application"]["ram"] = ["RAM", "RAM2"]
        self.expect("application RAM must be contiguous in address order: RAM ends at 0x20010000, RAM2 starts at "
                    "0x20020000", project=project, path="memory.application.ram[1]", mcudb=mcudb)

    def test_derivative_flash_data(self):
        cases = [
            (lambda d: d["memory"][0].pop("program_size"), "TESTMCU64", "memory[0]",
             "FLASH: program_size (smallest programmable unit) is missing, the bootloader needs it"),
            (lambda d: d["memory"][0].update(sector_size=None, sectors=[{"size": 0x4000, "count": 4},
                                                                        {"size": 0x10000, "count": 3}]),
             "project", "memory.flash",
             "FLASH of TESTMCU64 has sectors of different sizes (0x4000, 0x10000): the bootloader core needs one "
             "sector size (PLAT-16)"),
            (lambda d: d["memory"][0].update(sector_size=None, sectors=[{"size": 0x4000, "count": 4}]),
             "TESTMCU64", "memory[0].sectors", "sectors of FLASH cover 0x10000 bytes, the region has 0x40000"),
            (lambda d: d["memory"][0].update(sectors=[{"size": 0x2000, "count": 32}]),
             "TESTMCU64", "memory[0].sectors", "FLASH: give either sector_size or sectors"),
            (lambda d: d["memory"][0].update(program_size=3), "TESTMCU64", "memory[0].program_size",
             "sectors of FLASH are no multiple of program_size 3"),
            (lambda d: d["memory"][1].update(program_size=8), "TESTMCU64", "memory[1]",
             "sectors and program_size only apply to flash regions (RAM is ram)"),
        ]
        for change, level, path, text in cases:
            with self.subTest(text=text):
                shutil.rmtree(self.tmp / "mcudb", ignore_errors=True)
                mcudb = PinmuxTest.derivative(self, change)
                self.expect(text, project=self.project(), level=level, path=path, mcudb=mcudb)

    def test_unequal_sector_table(self):
        """Sector table of a flash with unequal sectors (STM32F4 style): boundaries follow the table."""
        def change(d):
            d["memory"][0].update(sector_size=None, sectors=[{"size": 0x4000, "count": 4}, {"size": 0x10000, "count": 3}])
        mcudb = PinmuxTest.derivative(self, change)
        region = nxconfig.model.MemoryRegion.model_validate(
            {"name": "F", "kind": "code_flash", "start": 0x08000000, "size": 0x40000,
             "sectors": [{"size": 0x4000, "count": 4}, {"size": 0x10000, "count": 3}]})
        self.assertEqual([hex(s) for s, _ in region.sector_table()][:6],
                         ["0x8000000", "0x8004000", "0x8008000", "0x800c000", "0x8010000", "0x8020000"])
        self.assertEqual(region.sector_table()[-1], (0x08030000, 0x10000))
        _, messages = self.check(project=self.project(), mcudb=mcudb)
        self.assertEqual([m.yaml_path for m in messages if m.severity == "error"], ["memory.flash"])

    def test_missing_diagnostics_section(self):
        project = self.project()
        project.pop("diagnostics")
        self.expect("the diagnostic addresses are needed too", project=project, path="memory")


class DiagnosticsTest(Profiles):
    """Diagnostic addresses (diag.py, COM-11): CAN ids, ISO-TP values, identification and search area."""

    def project(self):
        project = copy.deepcopy(BL_PROJECT)
        project["project"]["board"] = "../../boards/testboard"
        return project

    def plan(self, project=None, board=None, mcudb=TEST_MCUDB):
        config, messages = self.check(board, self.project() if project is None else project, mcudb)
        self.assertEqual([str(m) for m in messages if m.severity == "error"], [])
        return config.diagnostics

    def messages(self, project=None, board=None, mcudb=TEST_MCUDB):
        _, messages = self.check(board, project, mcudb)
        return messages

    def expect(self, fragment, project=None, board=None, path=None, severity="error", mcudb=TEST_MCUDB):
        found = [m for m in self.messages(project, board, mcudb) if m.severity == severity]
        match = [m for m in found if fragment in m.text]
        self.assertTrue(match, f"'{fragment}' not in {[str(m) for m in found]}")
        self.assertEqual((match[0].file.name, match[0].yaml_path), ("project.yaml", path))
        self.assertIsInstance(match[0].line, int)
        return match[0]

    def test_profile_values(self):
        plan = self.plan()
        self.assertEqual((plan.request_id, plan.functional_id, plan.response_id), (0x611, 0x6DF, 0x622))
        self.assertEqual((plan.padding, plan.block_size, plan.stmin_ms, plan.n_cr_ms, plan.n_bs_ms),
                         (0x39, 0, 0, 700, 800))
        self.assertEqual((plan.tester_address, plan.ecu_address), (0xF5, 0x4B))
        self.assertEqual(plan.version_prefix, "nextbsw-bl")
        self.assertEqual(plan.version_string_prefix, "nextbsw-bl ")
        # family testfam reserves no flash at the start: the search area is the whole bootloader area
        self.assertEqual((plan.scan_start, plan.scan_end, plan.scan_start_from_project),
                         (0x08000000, 0x08004000, False))

    def test_eval_board_values_equal_the_hand_written_ones(self):
        """Values of bl_config.h and AppDiagConfig.h before COM-11, and the bus from rte.yaml (ARCH-07)."""
        config, messages = nxconfig.check(DEMO_PROJECT)
        self.assertEqual(findings(messages), EVAL_OPEN_POWER_STAGE)
        plan = config.diagnostics
        self.assertEqual((plan.request_id, plan.functional_id, plan.response_id), (0x7E0, 0x7DF, 0x7E8))
        self.assertEqual((plan.padding, plan.block_size, plan.stmin_ms, plan.n_cr_ms, plan.n_bs_ms),
                         (0xCC, 0, 0, 1000, 1000))
        self.assertEqual((plan.tester_address, plan.ecu_address), (0xF1, 0x2A))
        self.assertEqual((plan.version_prefix, plan.scan_start, plan.scan_end), ("nextbsw-bl", 0x410, 0x8000))
        self.assertEqual(plan.bus, "CAN0")                          # rte.yaml uds.bus
        self.assertEqual(nxconfig.diag.scan_offset("s32k1xx"), 0x410)

    def test_second_build_profile_has_other_addresses(self):
        config, messages = nxconfig.check(REPO / "projects/buildtest_s32k148_min/project.yaml")
        self.assertEqual([str(m) for m in messages], [])
        plan = config.diagnostics
        self.assertEqual((plan.request_id, plan.functional_id, plan.response_id), (0x6F0, 0x6DF, 0x6F8))
        self.assertEqual((plan.padding, plan.block_size, plan.stmin_ms), (0xAA, 8, 1))
        self.assertEqual(plan.version_prefix, "NX148-BL")          # own identification, shorter than the default

    def test_default_identification_is_neutral(self):
        """PLAT-08: without diagnostics.bootloader.version_prefix a project gets nextbsw-bl, no derivative name."""
        self.assertEqual(nxconfig.model.BootloaderDiag().version_prefix, "nextbsw-bl")

    def test_errors_with_yaml_path(self):
        def diag(change):
            def apply(project):
                change(project["diagnostics"])
            return apply

        cases = [
            (diag(lambda d: d.update(request_id=0x800)),
             "CAN id 0x800 is outside the 11-bit range", "diagnostics.request_id"),
            (diag(lambda d: d.update(response_id=0x1FFFF)),
             "CAN id 0x1FFFF is outside the 11-bit range", "diagnostics.response_id"),
            (diag(lambda d: d.update(response_id=0x611)),
             "response_id 0x611 is also request_id", "diagnostics.response_id"),
            (diag(lambda d: d.update(functional_id=0x622)),
             "functional_id 0x622 is also response_id", "diagnostics.functional_id"),
            (diag(lambda d: d["isotp"].update(stmin_ms=0x80)),
             "STmin 128 ms cannot be encoded in the flow control frame", "diagnostics.isotp.stmin_ms"),
            (diag(lambda d: d["isotp"].update(stmin_ms=5, n_cr_ms=5)),
             "N_Cr 5 ms is not longer than STmin 5 ms", "diagnostics.isotp.n_cr_ms"),
            (diag(lambda d: d["isotp"].update(stmin_ms=5, block_size=2, n_bs_ms=4)),
             "N_Bs 4 ms is not longer than STmin 5 ms", "diagnostics.isotp.n_bs_ms"),
            (diag(lambda d: d["bootloader"].update(scan_start=0x08004000)),
             "search area 0x8004000 is outside the bootloader area 0x8000000..0x8003FFF of FLASH",
             "diagnostics.bootloader.scan_start"),
            (diag(lambda d: d.update(isotp={"padding": 0x100})),
             "less than or equal to 255", "diagnostics.isotp.padding"),
            (diag(lambda d: d["bootloader"].update(version_prefix="nextbsw bl")),
             "should match pattern", "diagnostics.bootloader.version_prefix"),
            (diag(lambda d: d.update(response_ids=1)), "Extra inputs are not permitted", "diagnostics.response_ids"),
        ]
        for change, fragment, path in cases:
            with self.subTest(fragment=fragment):
                project = self.project()
                change(project)
                self.expect(fragment, project=project, path=path)

    def test_warnings(self):
        project = self.project()
        project["diagnostics"]["request_id"] = 0x7EE
        self.expect("is the id of the updater's status frames", project=project, path="diagnostics.request_id",
                    severity="warning")
        project = self.project()
        project["diagnostics"]["isotp"]["stmin_ms"] = 10
        self.expect("take at least 1460 ms (146 consecutive frames): flashing becomes very slow",
                    project=project, path="diagnostics.isotp.stmin_ms", severity="warning")
        self.assertEqual(self.plan(project).stmin_ms, 10)           # a warning keeps the plan

    def test_bootloader_and_application_on_different_buses(self):
        project = self.project()
        project["project"]["rte"] = "rte.yaml"
        self.rte(project, uds_bus="VEH")                            # bootloader.can is DIAG
        self.expect("the bootloader answers on DIAG, the application on VEH", project=project,
                    path="bootloader.can", severity="warning")

    def rte(self, project, uds_bus="VEH", dbc=None):
        """Minimal ECU description next to the written project: DBC and diagnostic bus.

        RTE-22: `can.dbc` is relative to the rte.yaml, and this one is written into a scratch folder - so the
        demo DBC is named absolutely instead of through a repository root that does not exist there.
        """
        dbc = dbc if dbc is not None else FULL_DBC.as_posix()
        rte = {"schema_version": 2, "can": {"dbc": dbc, "node": "EVB", "bus": "VEH"}, "uds": {"bus": uds_bus},
               "components": [{"name": "Swc", "path": "swc/Swc"}]}     # ARCH-34: schema needs one component
        directory = self.tmp / "projects" / "testproject"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "rte.yaml").write_text(yaml.safe_dump(rte, sort_keys=False), encoding="utf-8")
        project["project"]["rte"] = "rte.yaml"
        return project

    def test_collision_with_a_dbc_message_of_the_project(self):
        project = self.rte(self.project())
        # TestFull.dbc (PLAT-62, the demo matrix): EVB_Status 272 (0x110), EVB_Analog 273, PC_Command 528 (0x210)
        project["diagnostics"]["request_id"] = 0x110
        # the message quotes can.dbc as the description spells it; this fixture names the demo DBC absolutely
        # (RTE-22: relative would be relative to the rte.yaml in the scratch folder)
        self.expect("request_id 0x110 is also the CAN id of message EVB_Status of "
                    f"{FULL_DBC.as_posix()} on bus VEH",
                    project=project, path="diagnostics.request_id")
        project = self.rte(self.project())
        project["diagnostics"]["response_id"] = 0x210
        self.expect("response_id 0x210 is also the CAN id of message PC_Command", project=project,
                    path="diagnostics.response_id")
        # the ids of the profile do not collide (only the bus warning of this profile stays)
        project = self.rte(self.project())
        self.assertEqual([m.text for m in self.messages(project) if m.severity == "error"], [])
        # a DBC whose signals run on another bus than the diagnostic stack is not compared
        project = self.rte(self.project(), uds_bus="DIAG")
        project["diagnostics"]["request_id"] = 0x110
        self.assertEqual([m.text for m in self.messages(project) if m.severity == "error"], [])

    def test_dbc_parser(self):
        text = "BO_ 272 EVB_Status: 8 EVB\n SG_ Led1 : 0|1@1+ (1,0) [0|1] \"\" PC\nBO_ 2147483920 Ext: 8 PC\n"
        self.assertEqual(nxconfig.diag.dbc_messages(text), {272: "EVB_Status", 2147483920: "Ext"})


class PowerStageTest(Profiles):
    """IO-24: minimum dead time and diagnostic values from generic power stage facts (tools/nxconfig/powerstage.py).

    The synthetic board has a fast stage with confirmed facts (test/profiles/boards/testboard): switch
    max(0, (30 + 10) - 8) = 32 ns, driver 55 - 40 = 15 ns, project margin 100 ns -> 147 ns < 500 ns of the project.
    """

    def stage(self, board):
        return board["pwm"][0]["power_stage"]

    def plan(self, board=None, project=None):
        config, messages = self.check(board, project)
        return (config.power_stages.get("BRIDGE") if config is not None else None), messages

    def slow(self, board):
        """A slow stage: turn-off delay and fall time ten times longer, a slow driver path."""
        stage = self.stage(board)
        stage["switch"].update(turn_off_delay_ns=300, fall_time_ns=100)
        stage["driver"].update(turn_on_delay_ns=40, turn_off_delay_ns=140)
        return board              # switch 400 - 8 = 392 ns + driver 100 ns + margin 100 ns = 592 ns

    def test_fast_stage(self):
        plan, messages = self.plan()
        self.assertEqual(messages, [])
        self.assertEqual((plan.switch_term_ns, plan.driver_term_ns, plan.margin_ns, plan.min_deadtime_ns),
                         (32, 15, 100, 147))
        self.assertTrue(plan.confirmed)
        self.assertEqual(plan.turn_on_ns, 20)                               # 8 + 12, not shortened
        self.assertEqual((plan.short_circuit_min_ma, plan.short_circuit_max_ma), (62_500, 100_000))  # 500 mV / 8, / 5 mOhm
        self.assertEqual(plan.gate_current_ua, 480)                         # 30 nC * 16 kHz

    def test_dead_time_exactly_at_the_minimum(self):
        project = self.project()
        project["pwm"]["BRIDGE"]["deadtime_ns"] = 147
        self.assertEqual(self.plan(project=project)[1], [])
        project["pwm"]["BRIDGE"]["deadtime_ns"] = 146
        m = self.expect("below the minimum dead time of 147 ns", project=project, level="project",
                        path="pwm.BRIDGE.deadtime_ns")
        self.assertIn("switch 32 ns + driver 15 ns + margin 100 ns", m.text)
        self.assertIsNotNone(m.line)

    def test_slow_stage_is_an_error_with_confirmed_facts(self):
        board = self.slow(self.board())
        plan, messages = self.plan(board)
        self.assertIsNone(plan)
        self.assertEqual(findings(messages), [("error", "pwm.BRIDGE.deadtime_ns")])
        self.assertIn("500 ns is below the minimum dead time of 592 ns", messages[0].text)
        self.assertEqual(messages[0].file.name, "project.yaml")
        project = self.project()
        project["pwm"]["BRIDGE"]["deadtime_ns"] = 600
        plan, messages = self.plan(board, project)
        self.assertEqual((messages, plan.min_deadtime_ns), ([], 592))

    def test_slow_stage_with_unconfirmed_facts_is_a_warning(self):
        for change, path in ((lambda s: s["driver"].update(verify=True), "pwm[0].power_stage.driver"),
                             (lambda s: s["switch"].pop("source"), "pwm[0].power_stage.switch"),
                             (lambda s: s["gate"].update(verify=True), "pwm[0].power_stage.gate")):
            with self.subTest(path=path):
                board = self.slow(self.board())
                change(self.stage(board))
                plan, messages = self.plan(board)
                self.assertEqual(findings(messages), [("warning", path), ("warning", "pwm.BRIDGE.deadtime_ns")])
                self.assertIn("rests on unconfirmed facts", messages[0].text)
                self.assertEqual(messages[0].file.name, "board.yaml")
                self.assertFalse(plan.confirmed)
                self.assertEqual(plan.min_deadtime_ns, 592)

    def test_facts_missing(self):
        board = self.board()
        del board["pwm"][0]["power_stage"]
        plan, messages = self.plan(board)
        self.assertEqual(findings(messages), [("warning", "pwm[0]")])
        self.assertIn("has no power_stage", messages[0].text)
        self.assertIsNotNone(messages[0].line)
        self.assertEqual((plan.min_deadtime_ns, plan.confirmed), (None, False))
        # a dead time of 1 ns is not rejected: nothing is known, nothing is assumed
        project = self.project()
        project["pwm"]["BRIDGE"]["deadtime_ns"] = 1
        self.assertEqual(findings(self.plan(board, project)[1]), [("warning", "pwm[0]")])

        cases = [
            (lambda s: s["switch"].pop("fall_time_ns"), "pwm[0].power_stage.switch", "fall_time_ns"),
            (lambda s: s.pop("switch"), "pwm[0].power_stage", "turn_on_delay_ns, turn_off_delay_ns, fall_time_ns"),
            (lambda s: s["driver"].pop("turn_on_delay_ns"), "pwm[0].power_stage.driver", "turn_on_delay_ns"),
            (lambda s: s.pop("driver"), "pwm[0].power_stage", "power_stage.driver"),
        ]
        for change, path, fragment in cases:
            with self.subTest(path=path, fragment=fragment):
                board = self.board()
                change(self.stage(board))
                plan, messages = self.plan(board)
                self.assertEqual([m.severity for m in messages], ["warning"] * len(messages))
                match = [m for m in messages if m.yaml_path == path and fragment in m.text]
                self.assertTrue(match, [str(m) for m in messages])
                self.assertIn("minimum dead time is not derived", match[0].text)
                self.assertIsNone(plan.min_deadtime_ns)

    def test_unused_group_is_not_reported(self):
        board = self.board()
        del board["pwm"][0]["power_stage"]
        project = self.project()
        del project["pwm"]
        config, messages = self.check(board, project)
        self.assertEqual((messages, config.power_stages), ([], {}))

    def test_missing_margin(self):
        project = self.project()
        del project["pwm"]["BRIDGE"]["deadtime_margin_ns"]
        plan, messages = self.plan(project=project)
        self.assertEqual(findings(messages), [("warning", "pwm.BRIDGE")])
        self.assertIn("no deadtime_margin_ns chosen", messages[0].text)
        self.assertEqual((plan.margin_ns, plan.min_deadtime_ns), (None, 47))

    def test_gate_resistance_scales_only_towards_a_longer_dead_time(self):
        cases = [
            # (on, off) resistance of the board: k = (1000 + R) / (1000 + 4700)
            ((4700, 2200), 32),            # smaller turn-off resistor: off path stays 40 ns
            ((4700, 9400), 65),            # k_off 10400/5700: 40 * 1.8246 = 72.98 - 8 -> 65 ns
            ((2350, 4700), 36),            # k_on 3350/5700: 8 * 0.5877 = 4.70 -> 40 - 4.70 -> 36 ns
            ((9400, 4700), 32),            # larger turn-on resistor: on path not lengthened
        ]
        for (on, off), switch in cases:
            with self.subTest(on=on, off=off):
                board = self.board()
                self.stage(board)["gate"].update(turn_on_resistance_mohm=on, turn_off_resistance_mohm=off)
                plan, messages = self.plan(board)
                self.assertEqual(messages, [])
                self.assertEqual(plan.switch_term_ns, switch)

    def test_times_unscaled_without_gate_data(self):
        board = self.board()
        del self.stage(board)["gate"]
        plan, messages = self.plan(board)
        self.assertEqual(findings(messages), [("warning", "pwm[0].power_stage")])
        self.assertIn("used as measured in its test circuit", messages[0].text)
        self.assertEqual((plan.switch_term_ns, plan.confirmed), (32, True))
        board = self.board()
        del self.stage(board)["switch"]["test_gate_resistance_mohm"]
        plan, messages = self.plan(board)
        self.assertEqual(findings(messages), [("warning", "pwm[0].power_stage.gate")])
        self.assertIn("switch.test_gate_resistance_mohm", messages[0].text)
        self.assertEqual(plan.switch_term_ns, 32)

    def test_diagnostics(self):
        board = self.board()
        self.stage(board)["short_circuit"]["blanking_ns"] = 19
        plan, messages = self.plan(board)
        self.assertEqual(findings(messages), [("warning", "pwm[0].power_stage.short_circuit.blanking_ns")])
        self.assertIn("ends before the switch has turned on (20 ns)", messages[0].text)
        board = self.board()
        del self.stage(board)["switch"]["r_ds_on_mohm"]
        plan, messages = self.plan(board)
        self.assertEqual(findings(messages), [("warning", "pwm[0].power_stage.short_circuit.vds_threshold_mv")])
        self.assertEqual((plan.short_circuit_min_ma, plan.short_circuit_max_ma), (None, None))
        board = self.board()
        del self.stage(board)["switch"]["rise_time_ns"]
        _, messages = self.plan(board)
        self.assertEqual(findings(messages), [("warning", "pwm[0].power_stage.short_circuit.blanking_ns")])
        board = self.board()
        del self.stage(board)["short_circuit"]
        del self.stage(board)["switch"]["gate_charge_nc"]
        plan, messages = self.plan(board)
        self.assertEqual(messages, [])      # no detection and no gate charge: optional, nothing to check
        self.assertEqual((plan.short_circuit_max_ma, plan.gate_current_ua, plan.min_deadtime_ns), (None, None, 147))

    def test_fixed_units_in_int32(self):
        cases = [
            (lambda s: s["switch"].update(fall_time_ns=1 << 31), "pwm[0].power_stage.switch.fall_time_ns"),
            (lambda s: s["switch"].update(fall_time_ns=10.5), "pwm[0].power_stage.switch.fall_time_ns"),
            (lambda s: s["driver"].update(turn_on_delay_ns=-1), "pwm[0].power_stage.driver.turn_on_delay_ns"),
            (lambda s: s["gate"].update(turn_on_resistance_ohm=4.7), "pwm[0].power_stage.gate.turn_on_resistance_ohm"),
        ]
        for change, path in cases:
            with self.subTest(path=path):
                board = self.board()
                change(self.stage(board))
                errors = self.errors(board=board)
                self.assertEqual([m.yaml_path for m in errors], [path])
                self.assertEqual(errors[0].file.name, "board.yaml")
        project = self.project()
        project["pwm"]["BRIDGE"]["deadtime_margin_ns"] = -1
        self.expect("greater than or equal to 0", project=project, level="project",
                    path="pwm.BRIDGE.deadtime_margin_ns")

    def test_synthetic_example_of_the_build_profile(self):
        """boards/buildtest_s32k148: format example (IO-24); since TOOL-243 with the source "[SYN] synthetic test
        profile, no hardware" instead of verify: true (F8), so its minimum dead time counts as confirmed."""
        board = yaml.safe_load((REPO / "boards/buildtest_s32k148/board.yaml").read_text(encoding="utf-8"))
        project = yaml.safe_load((REPO / "projects/buildtest_s32k148_min/project.yaml").read_text(encoding="utf-8"))
        project["project"]["board"] = "../../boards/testboard"
        project["project"].pop("rte", None)
        project["project"].pop("diag", None)
        project["pwm"] = {"MOT1": {"frequency_hz": 20000, "deadtime_ns": 1000, "deadtime_margin_ns": 50}}
        config, messages = nxconfig.check(self.write(board, project), nxconfig.MCUDB)
        self.assertEqual(findings(messages), [])
        plan = config.power_stages["MOT1"]
        self.assertEqual((plan.switch_term_ns, plan.driver_term_ns, plan.min_deadtime_ns), (165, 40, 255))
        self.assertEqual((plan.short_circuit_min_ma, plan.short_circuit_max_ma, plan.gate_current_ua),
                         (57_142, 100_000, 1200))
        project["pwm"]["MOT1"]["deadtime_ns"] = 200
        _, messages = nxconfig.check(self.write(board, project), nxconfig.MCUDB)
        # confirmed facts: a dead time below the derived minimum is an error, no longer a warning
        self.assertEqual(findings(messages), [("error", "pwm.MOT1.deadtime_ns")])

    def test_no_component_names_in_the_tool(self):
        """treiber-granularitaet.md sections 2/5: the derivation knows facts, not parts."""
        source = (HERE / "powerstage.py").read_text(encoding="utf-8")
        for word in ("TMC", "IRF", "BSC", "IPD", "Infineon", "Trinamic", "evalboard", "S32K"):
            self.assertNotIn(word, source)


SENSOR_BOARD = yaml.safe_load((HERE / "test/profiles/boards/testboard_sensors/board.yaml").read_text(encoding="utf-8"))
SENSOR_PROJECT = yaml.safe_load(
    (HERE / "test/profiles/projects/testproject_sensors/project.yaml").read_text(encoding="utf-8"))


class SensorTest(Profiles):
    """IO-18: sensor characteristics - integer conversion to signed physical values, board sensor and project load.

    Own profile testboard_sensors/testproject_sensors (S32K116 without FPU, VREFH 3300 mV, one channel per kind, an NTC
    and a table load, synthetic values). Reference: the physical formulas in floating point, the integer chain of
    tools/nxconfig/sensor.py must stay within the rounding of one unit.
    """

    # board adc index of each channel of the profile
    INDEX = {"BAT_V": 0, "MOTOR_I": 1, "TEMP_NTC": 2, "LEVEL": 3, "PRESSURE": 4, "PUMP_IS": 5, "OFFSET_V": 6, "SPARE": 7}

    def write(self, board=None, project=None) -> Path:
        project = copy.deepcopy(SENSOR_PROJECT if project is None else project)
        project["project"]["board"] = "../../boards/testboard"
        return super().write(SENSOR_BOARD if board is None else board, project)

    def check(self, board=None, project=None, mcudb=nxconfig.MCUDB):
        return nxconfig.check(self.write(board, project), mcudb)

    def errors(self, board=None, project=None, mcudb=nxconfig.MCUDB):
        return [m for m in self.check(board, project, mcudb)[1] if m.severity == "error"]

    @staticmethod
    def board():
        return copy.deepcopy(SENSOR_BOARD)

    @staticmethod
    def project():
        return copy.deepcopy(SENSOR_PROJECT)

    def sensor(self, board, name) -> dict:
        return board["adc"][self.INDEX[name]].setdefault("sensor", {})

    def test_profile_plans(self):
        config, messages = self.check()
        self.assertEqual(messages, [])
        plans = config.adc_sensors
        self.assertEqual(list(plans), ["BAT_V", "MOTOR_I", "TEMP_NTC", "LEVEL", "PRESSURE", "PUMP_IS", "OFFSET_V"])
        self.assertEqual({n: p.unit for n, p in plans.items()},
                         {"BAT_V": "mV", "MOTOR_I": "mA", "TEMP_NTC": "ddegC", "LEVEL": "permille", "PRESSURE": "mV",
                          "PUMP_IS": "mA", "OFFSET_V": "mV"})
        self.assertFalse(plans["LEVEL"].confirmed)                     # verify: true on the board sensor
        self.assertTrue(plans["MOTOR_I"].confirmed)
        # amplifier output range 20..3200 mV -> raw codes, pull-up equal to VREFH -> the last code is not computable
        self.assertEqual((plans["MOTOR_I"].ok_low, plans["MOTOR_I"].ok_high), (25, 3970))
        self.assertEqual((plans["TEMP_NTC"].calc_high, plans["TEMP_NTC"].ok_high), (4094, 4094))
        # signed: negative current, negative voltage after the offset (adc.mv stops at 0)
        self.assertEqual((plans["MOTOR_I"].value_min, plans["MOTOR_I"].value_max), (-16500, 16500))
        self.assertEqual(plans["OFFSET_V"].value_min, -100)

    def test_board_stage_against_the_formulas(self):
        """Every valid raw code: the integer result is the physical formula rounded half away from zero."""
        config, _ = self.check()
        v, m = 3300, 4095
        formulas = {
            "BAT_V": lambda u: u * 57000 / 10000,
            "MOTOR_I": lambda u: (u - 1650) / (50 * 2000e-6),
            "PRESSURE": lambda u: 5000 - u * 1.5,
            "PUMP_IS": lambda u: 5000 * u / 2200,
            "OFFSET_V": lambda u: u * 2 - 100,
            "LEVEL": lambda u: 1000 * u / (4000 - 300 - u),        # board value in ohm before the table
        }
        for name, formula in formulas.items():
            plan = config.adc_sensors[name]
            for raw in range(plan.ok_low, plan.ok_high + 1):
                exact = formula(raw * v / m)
                value = nxconfig.sensor.board_value(plan, raw)
                self.assertLessEqual(abs(value - exact), 0.5 + 1e-6, (name, raw, value, exact))

    def test_loads(self):
        config, _ = self.check()
        ntc, level = config.adc_sensors["TEMP_NTC"], config.adc_sensors["LEVEL"]
        self.assertEqual(ntc.points[0], (359, 1250))                    # 125 degC
        self.assertIn((10000, 250), ntc.points)                         # R_n at 25 degC
        self.assertEqual(ntc.points[-1], (401860, -400))                # -40 degC
        self.assertLessEqual(ntc.max_error, 10)
        # 25 degC: U = 1650 mV -> raw 2047.5; both neighbours read 25.0 +- 0.1 degC
        for raw in (2047, 2048):
            value, quality = nxconfig.sensor.evaluate(ntc, raw)
            self.assertLessEqual(abs(value - 250), 1)
            self.assertEqual(quality, nxconfig.sensor.QUALITY_OK)
        # outside the table (hotter than 125 degC / colder than -40 degC): end value, quality RANGE
        self.assertEqual(nxconfig.sensor.evaluate(ntc, 100), (1250, nxconfig.sensor.QUALITY_RANGE))
        self.assertEqual(nxconfig.sensor.evaluate(ntc, 4000), (-400, nxconfig.sensor.QUALITY_RANGE))
        # table: 0..200 ohm -> 0..400 permille, above 1000 ohm held at 1000 with RANGE
        raw_300_ohm = next(r for r in range(4096) if nxconfig.sensor.board_value(level, r) >= 300)
        value, quality = nxconfig.sensor.evaluate(level, raw_300_ohm)
        self.assertEqual(quality, nxconfig.sensor.QUALITY_OK)
        self.assertLessEqual(abs(value - (400 + (nxconfig.sensor.board_value(level, raw_300_ohm) - 200) * 500 / 400)),
                             0.5)
        self.assertEqual(nxconfig.sensor.evaluate(level, 4000)[1], nxconfig.sensor.QUALITY_RANGE)

    def test_rounding_and_interpolation_reference(self):
        s = nxconfig.sensor
        self.assertEqual([s.div_round(n, 4) for n in (-6, -5, -2, -1, 0, 1, 2, 5, 6)], [-2, -1, -1, 0, 0, 0, 1, 1, 2])
        self.assertEqual(s.div_round(-7, 3), -2)
        points = ((0, 0), (10, 100), (20, 50))
        self.assertEqual([s.interpolate(points, x) for x in (-1, 0, 5, 10, 15, 20, 21)],
                         [(0, False), (0, True), (50, True), (100, True), (75, True), (50, True), (50, False)])

    @on_eval_board
    def test_eval_board_characteristics(self):
        """Eval board (IO-18): values from the data sheets and the board test 2026-09-16."""
        config, messages = nxconfig.check(DEMO_PROJECT)
        self.assertEqual(findings(messages), EVAL_OPEN_POWER_STAGE)
        plans, s = config.adc_sensors, nxconfig.sensor
        self.assertNotIn("MOTOR_NTC", plans)                           # R148 not fitted
        # phase current (INA240A1, 4 mOhm) and BEMF dividers fitted since 2026-09-21 (user)
        for name in ("ADC_CURRENT_U", "ADC_CURRENT_V", "ADC_CURRENT_W", "BEMF_U", "BEMF_V", "BEMF_W"):
            self.assertIn(name, plans)
        self.assertEqual(config.project.adc_loads, {})                  # no invented NTC characteristic
        # proximity: 1474 ohm plug at 5012 mV over U_D48 235 mV -> 3903 mV -> raw 3902 -> 1474 ohm; 3900 mV -> 1467
        u = (5012 - 235) * 1474 / (1474 + 330)
        self.assertEqual(round(u), 3903)
        self.assertEqual(s.evaluate(plans["PROXY_ANALOG"], round(u * 4095 / 4096)), (1474, s.QUALITY_OK))
        self.assertEqual(s.evaluate(plans["PROXY_ANALOG"], 3899), (1467, s.QUALITY_OK))
        self.assertEqual(s.evaluate(plans["PROXY_ANALOG"], 4095)[1], s.QUALITY_RANGE)   # no plug
        # INA181A1 (G 20) on 50 mOhm around VREF_2 2048 mV: 1 mA per mV; board test I_BAT 2109 mV -> about +61 mA
        self.assertEqual(s.evaluate(plans["12VBAT_CURRENT"], 2108), (61, s.QUALITY_OK))
        # INA240A3 (G 100) on 500 uOhm around 5012 mV / 2: 20 mA per mV, zero at 2506 mV
        self.assertEqual(s.evaluate(plans["SENSE_TSCURRENT"], 2505)[0], -8)
        self.assertEqual((plans["SENSE_TSCURRENT"].value_min, plans["SENSE_TSCURRENT"].value_max), (-50120, 31800))
        # BTS5030 k_ILIS 2180 over R166 1.2 kOhm: full scale 7441 mA
        self.assertEqual(plans["CURRENTSENSE_KL30B"].value_max, 7441)
        self.assertEqual(plans["CURRENTSENSE_KL30B"].unit, "mA")
        # channels without sensor: board scale in mV (SENSE_TSVOLTAGE 24 V at the board test)
        self.assertEqual(s.evaluate(plans["SENSE_TSVOLTAGE"], 2015)[0], 24003)

    def test_board_errors(self):
        cases = []
        b = self.board()
        self.sensor(b, "MOTOR_I")["reference"] = {"rail": "REF_2V5"}
        cases.append((b, "unknown rail REF_2V5", "adc[1].sensor.reference.rail"))
        b = self.board()
        self.sensor(b, "MOTOR_I")["output_min_mv"] = 3200
        cases.append((b, "output_max_mv 3200 must be above output_min_mv 3200", "adc[1].sensor.output_max_mv"))
        b = self.board()
        self.sensor(b, "LEVEL")["diode_mv"] = 4000
        cases.append((b, "diode_mv 4000 is not below the supply 4000 mV", "adc[3].sensor.diode_mv"))
        b = self.board()
        self.sensor(b, "PUMP_IS")["device"] = "FAN_SW"
        cases.append((b, "unknown device FAN_SW", "adc[5].sensor.device"))
        b = self.board()
        b["devices"][0]["connect"]["is"] = "OFFSET_V"
        cases.append((b, "device PUMP_SW is not wired to this channel", "adc[5].sensor.device"))
        b = self.board()
        b["adc"][self.INDEX["SPARE"]]["sensor"] = {"kind": "divider", "r_top_ohm": 1, "r_bottom_ohm": 1}
        cases.append((b, "marked fitted: false; a circuit that is not fitted has no sensor", "adc[7].sensor"))
        b = self.board()
        b["adc"][self.INDEX["BAT_V"]]["scale"] = {"divider": {"r_top": 47000, "r_bottom": 10000}}
        cases.append((b, "scale and sensor both describe the circuit", "adc[0].scale"))
        b = self.board()
        self.sensor(b, "PRESSURE").update(gain_num=2000000000, gain_den=1)
        cases.append((b, "does not fit int32 (adc.phys)", "adc[4].sensor"))
        b = self.board()
        self.sensor(b, "MOTOR_I").update(gain=2147483647, gain_den=2147483646, shunt_uohm=2147483647)
        cases.append((b, "the integer conversion overflows int64 at raw 0", "adc[1].sensor"))
        for board, fragment, path in cases:
            with self.subTest(fragment=fragment):
                error = self.expect(fragment, board, level="board", path=path)
                self.assertIsNotNone(error.line)

    def test_schema_errors(self):
        b = self.board()
        self.sensor(b, "MOTOR_I")["reference"] = {"rail": "REF_1V65", "mv": 1650}
        self.expect("give either rail or mv", b, level="board")
        b = self.board()
        self.sensor(b, "TEMP_NTC")["supply"] = {"mv": 3300, "ratio_permille": 500}
        self.expect("ratio_permille only applies to a rail", b, level="board")
        b = self.board()
        self.sensor(b, "PRESSURE")["gain_num"] = 0
        self.expect("gain_num must not be 0", b, level="board")
        b = self.board()
        self.sensor(b, "PUMP_IS")["kind"] = "thermocouple"
        self.assertTrue(self.errors(b))

    def test_project_errors(self):
        cases = []
        p = self.project()
        p["adc_loads"]["NOPE"] = {"kind": "table", "unit": "mV", "points": [[0, 0], [1, 1]]}
        cases.append((p, None, "adc load NOPE: no board ADC channel NOPE", "adc_loads.NOPE"))
        p = self.project()
        p["adc_loads"]["OFFSET_V"] = {"kind": "table", "unit": "mV", "points": [[0, 0], [1, 1]]}
        cases.append((p, None, "board channel OFFSET_V has no sensor block", "adc_loads.OFFSET_V"))
        p = self.project()
        p["adc_loads"]["SPARE"] = {"kind": "table", "unit": "mV", "points": [[0, 0], [1, 1]]}
        cases.append((p, None, "board marks channel SPARE as not fitted", "adc_loads.SPARE"))
        p = self.project()
        p["use"]["adc"] = ["BAT_V", "MOTOR_I", "TEMP_NTC"]
        cases.append((p, None, "adc load LEVEL: channel LEVEL is not used by the project", "adc_loads.LEVEL"))
        p = self.project()
        p["adc_loads"]["MOTOR_I"] = copy.deepcopy(p["adc_loads"]["TEMP_NTC"])
        cases.append((p, None, "an NTC needs a resistance from the board", "adc_loads.MOTOR_I.kind"))
        p = self.project()
        p["adc_loads"]["LEVEL"]["points"][2][0] = 200
        cases.append((p, None, "board values of the points must ascend (200 then 200)", "adc_loads.LEVEL.points[2][0]"))
        p = self.project()
        p["adc_loads"]["LEVEL"]["points"] = [[-2000000000, -2000000000], [2000000000, 2000000000]]
        cases.append((p, None, "segment 0 (-2000000000..2000000000) overflows the int64 interpolation",
                      "adc_loads.LEVEL.points"))
        p = self.project()
        p["adc_loads"]["TEMP_NTC"]["t_min_ddegc"] = 1250
        cases.append((p, None, "t_max_ddegc 1250 must be above t_min_ddegc 1250", "adc_loads.TEMP_NTC.t_max_ddegc"))
        p = self.project()
        p["adc_loads"]["TEMP_NTC"]["step_ddegc"] = 1
        cases.append((p, None, "points from t_min to t_max, at most 255", "adc_loads.TEMP_NTC.step_ddegc"))
        p = self.project()
        p["adc_loads"]["TEMP_NTC"].update(t_min_ddegc=-2700, step_ddegc=100)
        cases.append((p, None, "does not fit int32 (higher t_min_ddegc)", "adc_loads.TEMP_NTC.t_min_ddegc"))
        p = self.project()
        p["adc_loads"]["TEMP_NTC"].update(r_nominal_ohm=10, t_min_ddegc=1000, t_max_ddegc=1500, step_ddegc=10)
        cases.append((p, None, "give the same resistance", "adc_loads.TEMP_NTC.step_ddegc"))
        for project, board, fragment, path in cases:
            with self.subTest(fragment=fragment):
                error = self.expect(fragment, board, project, level="project", path=path)
                self.assertIsNotNone(error.line)

    def test_warnings(self):
        # a board scale beyond int32 stays valid for adc.mv (uint32), only adc.phys is not available
        b = self.board()
        b["adc"][self.INDEX["OFFSET_V"]]["scale"] = {"factor": 1000000}
        config, messages = self.check(board=b)
        self.assertEqual(findings(messages), [("warning", "adc[6].scale")])
        self.assertIn("does not fit int32 (adc.phys); adc.phys.OFFSET_V is not available", messages[0].text)
        self.assertNotIn("OFFSET_V", config.adc_sensors)
        p = self.project()
        p["adc_loads"]["TEMP_NTC"].update(step_ddegc=400, max_error_ddegc=5)
        _, messages = self.check(project=p)
        self.assertEqual(findings(messages), [("warning", "adc_loads.TEMP_NTC.step_ddegc")])
        self.assertRegex(messages[0].text, r"the table deviates up to [\d.]+ degC from the NTC model")
        p = self.project()
        p["use"]["adc"] = list(self.INDEX)                              # the same channels, but named
        _, messages = self.check(project=p)
        self.assertEqual(findings(messages), [("warning", "use.adc")])
        self.assertIn("ADC channel SPARE is used, but board adc[7] marks its circuit as not fitted", messages[0].text)


class PwmCounterTest(Profiles):
    """ARCH-33: frequency and dead time of a used PWM group against the PWM counter of the family, in nxconfig.

    Until ARCH-33 only bswgen saw these limits (message without file, line and YAML path). Profile: the s32k1xx test
    profile with 8 MHz crystal and 64 MHz core (group FAN on FTM1, counter clock 64 MHz), plus the eval board demo.
    """

    def write(self, board=None, project=None) -> Path:
        project = copy.deepcopy(S32K_PROJECT if project is None else project)
        project["project"]["board"] = "../../boards/testboard"
        return super().write(S32K_BOARD if board is None else board, project)

    def errors_of(self, project):
        _, messages = nxconfig.check(self.write(project=project), nxconfig.MCUDB)
        return [m for m in messages if m.severity == "error"]

    def fan(self, **values):
        project = copy.deepcopy(S32K_PROJECT)
        project["pwm"]["FAN"].update(values)
        return project

    def test_messages_with_yaml_path(self):
        cases = [
            # 63 x 16 / 64 MHz = 15.75 us
            ({"deadtime_ns": 15_751}, "pwm.FAN.deadtime_ns",
             "PWM group FAN: dead time 15751 ns exceeds the FlexTimer maximum of 15750 ns (63 x 16 periods of the "
             "counter clock 64000000 Hz of FTM1)"),
            # 100 kHz = 640 ticks, 5000 ns = 320 ticks: 2 x 320 >= 640
            ({"frequency_hz": 100_000, "deadtime_ns": 5_000}, "pwm.FAN.deadtime_ns",
             "PWM group FAN: dead time 5000 ns must be shorter than half the PWM period (5000 ns at 100000 Hz): two "
             "dead times per period"),
            # 64 MHz / 900 kHz = 71 ticks < 100
            ({"frequency_hz": 900_000}, "pwm.FAN.frequency_hz",
             "PWM group FAN: frequency 900000 Hz leaves 71 counter ticks per period at the counter clock 64000000 Hz "
             "of FTM1, at least 100 needed (duty resolution)"),
            # 64 MHz / 128 / 5 Hz = 100000 ticks > 65535
            ({"frequency_hz": 5}, "pwm.FAN.frequency_hz",
             "PWM group FAN: frequency 5 Hz is too low for the counter clock 64000000 Hz of FTM1 (largest prescaler "
             "128, at most 65535 ticks per period)"),
        ]
        for values, path, text in cases:
            with self.subTest(values=values):
                errors = self.errors_of(self.fan(**values))
                self.assertEqual([(m.yaml_path, m.text) for m in errors], [(path, text)])
                self.assertEqual(errors[0].file.name, "project.yaml")
                self.assertIsInstance(errors[0].line, int)
        # the limits themselves are accepted: 15750 ns = 63 x 16 ticks; at 100 kHz 4750 ns = 19 x 16 = 304 < 640 / 2,
        # while 4751 ns rounds up to 20 x 16 = 320 (the dead time is never shortened)
        self.assertEqual(self.errors_of(self.fan(deadtime_ns=15_750)), [])
        self.assertEqual(self.errors_of(self.fan(frequency_hz=100_000, deadtime_ns=4_750)), [])
        self.assertEqual([m.yaml_path for m in self.errors_of(self.fan(frequency_hz=100_000, deadtime_ns=4_751))],
                         ["pwm.FAN.deadtime_ns"])

    def test_demo_project_message_of_the_guide(self):
        """The error DOC-02 provoked (doc/guide/build-and-flash.md) now comes from nxconfig check with its path."""
        project = yaml.safe_load(DEMO_PROJECT.read_text(encoding="utf-8"))
        project["project"]["board"] = (DEMO_PROJECT.parent / project["project"]["board"]).resolve().as_posix()  # PLAT-62
        with_empty_rte(project, self.tmp / "demo")          # TOOL-114: a built project needs one
        project["project"].pop("diag", None)
        project["pwm"]["MOT1"]["deadtime_ns"] = 40_000
        path = self.tmp / "demo" / "project.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        _, messages = nxconfig.check(path)
        errors = [m for m in messages if m.severity == "error"]
        self.assertEqual([(m.yaml_path, m.text) for m in errors],
                         [("pwm.MOT1.deadtime_ns", "PWM group MOT1: dead time 40000 ns exceeds the FlexTimer maximum "
                           "of 12600 ns (63 x 16 periods of the counter clock 80000000 Hz of FTM0)")])
        self.assertRegex(str(errors[0]), r"^project\.yaml:\d+ \(pwm\.MOT1\.deadtime_ns\): error: PWM group MOT1")
        # the same copy without the CAN rank: the other gap of DOC-02
        project["pwm"]["MOT1"]["deadtime_ns"] = 1_000
        project["use"]["can"] = ["CAN0"]                    # COM-05: the demo also uses CAN1; one message suffices
        project["interrupts"]["ranks"] = [r for r in project["interrupts"]["ranks"] if r["source"] != "can"]
        path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        _, messages = nxconfig.check(path)
        errors = [m for m in messages if m.severity == "error"]
        self.assertEqual([(m.yaml_path, m.text) for m in errors],
                         [("interrupts.ranks", "CAN bus CAN0 is used, but has no interrupt rank: the board port "
                           "cannot set the level of its message buffer interrupts (interrupts.ranks: {source: can, "
                           "name: CAN0, rank: <n>})")])
        self.assertRegex(str(errors[0]), r"^project\.yaml:\d+ \(interrupts\.ranks\): error: CAN bus CAN0")

    def test_kernel_values(self):
        """pwm_timing() is the one rule nxconfig and bswgen share: values of the eval board group MOT1."""
        limits = checks.PWM_COUNTERS["s32k1xx"]
        t = checks.pwm_timing(limits, "MOT1", "FTM0", 80_000_000, 20_000, 1_000)
        # 80 MHz / 20 kHz = 4000 ticks, prescaler 1; 1000 ns = 80 clock ticks = 20 x DTPS 4
        self.assertEqual((t.prescaler_shift, t.period_ticks, t.frequency_hz, t.deadtime_divider, t.deadtime_ticks),
                         (0, 4000, 20_000, 4, 20))
        t = checks.pwm_timing(limits, "G", "FTM1", 64_000_000, 100, 1_000)
        self.assertEqual((t.prescaler_shift, t.period_ticks), (4, 40_000))    # 640000 ticks / 16
        with self.assertRaises(checks.PwmTimingError) as ctx:
            checks.pwm_timing(limits, "G", "FTM1", 64_000_000, 20_000, 16_000)
        self.assertEqual(ctx.exception.key, "deadtime_ns")

    def test_other_family_and_instance_are_left_to_the_generator(self):
        """No counter model for the family (synthetic testfam): nxconfig demands nothing it cannot know."""
        self.assertNotIn("testfam", checks.PWM_COUNTERS)
        project = copy.deepcopy(TEST_PROJECT)
        project["pwm"]["BRIDGE"]["deadtime_ns"] = 10_000_000
        _, messages = nxconfig.check(Profiles.write(self, None, project), TEST_MCUDB)
        self.assertEqual([m for m in messages if m.severity == "error"], [])


class EvalPowerStageTest(Profiles):
    """IO-26: power stage facts of the eval board (TK5R1P08QM, TMC6200, 2 Ohm gate resistors) and the demo margin.

    switch (t_off 119 ns entered as t_d(off), t_f 68 ns, t_d(on) 0 ns; see board.yaml): (119 + 68) * 1 - 0 = 187 ns
    (gate 2 Ohm < 4.7 Ohm of the data sheet test: no scaling), driver 110 - 100 = 10 ns, margin 300 ns -> 497 ns,
    below the 1000 ns of the project.
    """

    def demo_copy(self, **pwm) -> Path:
        project = yaml.safe_load(DEMO_PROJECT.read_text(encoding="utf-8"))
        project["project"]["board"] = (DEMO_PROJECT.parent / project["project"]["board"]).resolve().as_posix()  # PLAT-62
        with_empty_rte(project, self.tmp / "demo")          # TOOL-114: a built project needs one
        project["project"].pop("diag", None)
        project["pwm"]["MOT1"].update(pwm)
        path = self.tmp / "demo" / "project.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        return path

    @on_eval_board
    def test_demo_minimum_dead_time(self):
        config, messages = nxconfig.check(DEMO_PROJECT)
        self.assertEqual(findings(messages), [])
        plan = config.power_stages["MOT1"]
        self.assertEqual((plan.switch_term_ns, plan.driver_term_ns, plan.margin_ns, plan.min_deadtime_ns),
                         (187, 10, 300, 497))
        self.assertTrue(plan.confirmed)                                 # every fact with source, none verify
        self.assertEqual(plan.deadtime_ns, 1000)
        self.assertEqual(plan.gate_current_ua, 1120)                    # 56 nC * 20 kHz
        self.assertEqual(plan.turn_on_ns, 64)                           # t_d(on) 0 (lower bound) + t_r 64
        self.assertIsNone(plan.short_circuit_max_ma)                    # no fixed detector on the board
        # IO-35: R_DS(on) 5.1 mOhm max. (data sheet p.3) in micro-ohms, no hot maximum in the data sheet
        self.assertEqual((plan.r_ds_on_uohm, plan.r_ds_on_hot_uohm, plan.vds_threshold_mv), (5100, None, None))
        # the TMC6200 settings of the demo stay at the device reset values: nothing derived, nothing reported
        self.assertEqual([(s.device, s.parameter, s.kind, s.value, s.short_circuit_max_ma)
                          for s in plan.driver_settings],
                         [("MOT1_GD", "short_to_ground_mv", "vds_threshold_high_side_mv", 0, None),
                          ("MOT1_GD", "short_to_supply_mv", "vds_threshold_low_side_mv", 0, None),
                          ("MOT1_GD", "short_delay_ns", "blanking_ns", 0, None)])

    @on_eval_board
    def test_demo_short_circuit_current_from_the_tmc6200_setting(self):
        """IO-35: a low-side level of 625 mV (TMC6200 level 6) with R_DS(on) 5.1 mOhm trips at or below 122.5 A."""
        project = yaml.safe_load(DEMO_PROJECT.read_text(encoding="utf-8"))
        project["project"]["board"] = (DEMO_PROJECT.parent / project["project"]["board"]).resolve().as_posix()  # PLAT-62
        with_empty_rte(project, self.tmp / "demo")          # TOOL-114: a built project needs one
        project["project"].pop("diag", None)
        project["modules"].setdefault("MOT1_GD", {}).update(short_to_supply_mv=625, short_delay_ns=750)
        path = self.tmp / "demo" / "project.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        config, messages = nxconfig.check(path)
        self.assertEqual(findings(messages), [])
        settings = {s.parameter: s for s in config.power_stages["MOT1"].driver_settings}
        self.assertEqual((settings["short_to_supply_mv"].short_circuit_min_ma,
                          settings["short_to_supply_mv"].short_circuit_max_ma), (None, 122_549))
        info = powerstage.info(config)
        self.assertIn("PWM group MOT1: MOT1_GD short_to_supply_mv 625 mV (low side): trips at or below 122.5 A "
                      "(R_DS(on) 5100 µΩ at 25 °C; no hot R_DS(on), the current at high temperature is lower)", info)
        self.assertIn("PWM group MOT1: MOT1_GD short_delay_ns 750 ns, the switch has turned on after 64 ns", info)

    @on_eval_board
    def test_demo_dead_time_at_the_limit(self):
        """Confirmed facts: a dead time below the minimum is an error, not a warning (safety relevant, IO-15)."""
        _, messages = nxconfig.check(self.demo_copy(deadtime_ns=497))
        self.assertEqual(findings(messages), [])
        _, messages = nxconfig.check(self.demo_copy(deadtime_ns=496))
        self.assertEqual(findings(messages), [("error", "pwm.MOT1.deadtime_ns")])
        self.assertIn("496 ns is below the minimum dead time of 497 ns the power stage needs (switch 187 ns + driver "
                      "10 ns + margin 300 ns)", messages[0].text)

    @on_eval_board
    def test_demo_without_margin(self):
        _, messages = nxconfig.check(self.demo_copy(deadtime_margin_ns=None))
        self.assertEqual(findings(messages), [("warning", "pwm.MOT1")])
        self.assertIn("minimum dead time of 197 ns contains no margin", messages[0].text)


class GateDriverShortCircuitTest(Profiles):
    """IO-35: short-circuit detection set by the gate driver (project parameters marked power_stage in module.yaml)
    against R_DS(on) in micro-ohms of the board.

    Own profile, not the eval board: testboard_s32k_8mhz (S32K144) with a TMC6200 "GD" on its own SPI bus driving the
    PWM group FAN; the switch has R_DS(on) 2.7 mOhm at 25 degC and 4.5 mOhm hot (not representable in whole mOhm), no
    fixed detector on the board. turn-on 8 + 12 = 20 ns.
    """

    GD = {"name": "GD", "part": "TMC6200-TA", "module": "gdTmc6200", "connect": {"drv_en": "LED", "fault": "KEY"}}
    SPI = {"name": "SPI_A", "instance": "LPSPI1", "sck": {"pin": "PTD0"}, "sin": {"pin": "PTD1"},
           "sout": {"pin": "PTD2"}, "pcs": {"pin": "PTD3", "index": 0}, "device": "GD"}

    def write(self, board=None, project=None) -> Path:
        project = copy.deepcopy(S32K_PROJECT if project is None else project)
        project["project"]["board"] = "../../boards/testboard"
        return super().write(S32K_BOARD if board is None else board, project)

    def profile(self, **settings) -> tuple[dict, dict]:
        board, project = copy.deepcopy(S32K_BOARD), copy.deepcopy(S32K_PROJECT)
        board["devices"].append(dict(self.GD))
        board["spi"] = [dict(self.SPI)]
        group = board["pwm"][0]
        group["gate_driver"] = "GD"
        # IO-45: a TMC6200 drives three phases (module.yaml phases: 3) - one pair per phase, FTM1 CH2..CH5 of the
        # S32K144 LQFP64 (the pins of tools/bswgen/test/profiles/boards/testboard_safe_outputs)
        group["pairs"] += [{"name": "FAN_B", "high_side": "FAN_B_HS", "low_side": "FAN_B_LS"},
                           {"name": "FAN_C", "high_side": "FAN_C_HS", "low_side": "FAN_C_LS"}]
        group["channels"] += [{"name": "FAN_B_HS", "channel": 2, "pin": "PTC14"},
                              {"name": "FAN_B_LS", "channel": 3, "pin": "PTC15"},
                              {"name": "FAN_C_HS", "channel": 4, "pin": "PTA10"},
                              {"name": "FAN_C_LS", "channel": 5, "pin": "PTA11"}]
        switch = group["power_stage"]["switch"]
        del switch["r_ds_on_mohm"], switch["r_ds_on_hot_mohm"]
        switch.update(r_ds_on_uohm=2700, r_ds_on_hot_uohm=4500)
        del group["power_stage"]["short_circuit"]
        project["use"]["spi"] = ["SPI_A"]
        if settings:
            project["modules"] = {"GD": settings}
        return board, project

    def run_check(self, board, project):
        config, messages = nxconfig.check(self.write(board, project), nxconfig.MCUDB)
        return config, messages

    def settings(self, config) -> dict:
        return {s.parameter: s for s in config.power_stages["FAN"].driver_settings}

    def test_defaults_derive_nothing(self):
        config, messages = self.run_check(*self.profile())
        self.assertEqual(findings(messages), [])
        settings = self.settings(config)
        self.assertEqual(sorted(settings), ["short_delay_ns", "short_to_ground_mv", "short_to_supply_mv"])
        self.assertEqual({(s.value, s.short_circuit_min_ma, s.short_circuit_max_ma) for s in settings.values()},
                         {(0, None, None)})
        self.assertEqual(config.integration.devices[-1].parameters["short_filter_ns"], 0)
        self.assertIn("PWM group FAN: GD short_to_supply_mv not set, the device keeps its own setting (nothing "
                      "derived)", powerstage.info(config))

    def test_thresholds_give_the_current_range(self):
        board, project = self.profile(short_to_ground_mv=1560, short_to_supply_mv=1000, short_filter_ns=2000,
                                      short_delay_ns=1500)
        config, messages = self.run_check(board, project)
        self.assertEqual(findings(messages), [])
        plan = config.power_stages["FAN"]
        self.assertEqual((plan.r_ds_on_uohm, plan.r_ds_on_hot_uohm), (2700, 4500))
        settings = self.settings(config)
        # I = V / R: 1000 mV / 4.5 mOhm = 222.2 A (hot) .. / 2.7 mOhm = 370.4 A (25 degC), rounded down to mA
        self.assertEqual((settings["short_to_supply_mv"].short_circuit_min_ma,
                          settings["short_to_supply_mv"].short_circuit_max_ma), (222_222, 370_370))
        self.assertEqual((settings["short_to_ground_mv"].short_circuit_min_ma,
                          settings["short_to_ground_mv"].short_circuit_max_ma), (346_666, 577_777))
        self.assertEqual(settings["short_delay_ns"].kind, "blanking_ns")
        info = powerstage.info(config)
        self.assertIn("PWM group FAN: GD short_to_supply_mv 1000 mV (low side): trips at or below 222.2 A .. 370.4 A "
                      "(R_DS(on) 2700 µΩ at 25 °C, 4500 µΩ hot)", info)
        self.assertIn("PWM group FAN: GD short_to_ground_mv 1560 mV (high side): trips at or below 346.7 A .. "
                      "577.8 A (R_DS(on) 2700 µΩ at 25 °C, 4500 µΩ hot)", info)
        self.assertIn("PWM group FAN: GD short_delay_ns 1500 ns, the switch has turned on after 20 ns", info)

    def test_old_milliohm_form_is_the_same(self):
        """Counter-check: 3 mOhm in the old field gives the same current as 3000 micro-ohms in the new one."""
        results = []
        for fields in ({"r_ds_on_mohm": 3, "r_ds_on_hot_mohm": 5}, {"r_ds_on_uohm": 3000, "r_ds_on_hot_uohm": 5000}):
            board, project = self.profile(short_to_supply_mv=625)
            switch = board["pwm"][0]["power_stage"]["switch"]
            del switch["r_ds_on_uohm"], switch["r_ds_on_hot_uohm"]
            switch.update(fields)
            config, messages = self.run_check(board, project)
            self.assertEqual(findings(messages), [])
            s = self.settings(config)["short_to_supply_mv"]
            results.append((config.power_stages["FAN"].r_ds_on_uohm, s.short_circuit_min_ma, s.short_circuit_max_ma))
        self.assertEqual(results, [(3000, 125_000, 208_333)] * 2)

    def test_on_resistance_stated_once(self):
        board, project = self.profile()
        board["pwm"][0]["power_stage"]["switch"]["r_ds_on_mohm"] = 3
        errors = [m for m in self.run_check(board, project)[1] if m.severity == "error"]
        self.assertEqual(findings(errors), [("error", "pwm[0].power_stage.switch")])
        self.assertIn("r_ds_on_uohm and r_ds_on_mohm both given", errors[0].text)
        board, project = self.profile()
        switch = board["pwm"][0]["power_stage"]["switch"]
        del switch["r_ds_on_uohm"]
        switch["r_ds_on_mohm"] = 3_000_000                          # 3 kOhm: no int32 in micro-ohms
        errors = [m for m in self.run_check(board, project)[1] if m.severity == "error"]
        self.assertIn("does not fit in r_ds_on_uohm", errors[0].text)

    def test_missing_on_resistance_is_a_warning(self):
        board, project = self.profile(short_to_supply_mv=1000)
        switch = board["pwm"][0]["power_stage"]["switch"]
        del switch["r_ds_on_uohm"], switch["r_ds_on_hot_uohm"]
        config, messages = self.run_check(board, project)
        self.assertEqual(findings(messages), [("warning", "modules.GD.short_to_supply_mv")])
        self.assertEqual(messages[0].file.name, "project.yaml")
        self.assertIn("is not derived, the board states no R_DS(on) (pwm[0].power_stage.switch.r_ds_on_uohm)",
                      messages[0].text)
        self.assertIn("PWM group FAN: GD short_to_supply_mv 1000 mV (low side): current not derived (no R_DS(on))",
                      powerstage.info(config))

    def test_board_detector_and_driver_setting_both(self):
        board, project = self.profile(short_to_supply_mv=1000)
        board["pwm"][0]["power_stage"]["short_circuit"] = {"vds_threshold_mv": 500, "source": "synthetic"}
        config, messages = self.run_check(board, project)
        self.assertEqual(findings(messages), [("warning", "modules.GD.short_to_supply_mv")])
        self.assertIn("short-circuit detection stated twice", messages[0].text)
        plan = config.power_stages["FAN"]
        self.assertEqual((plan.short_circuit_min_ma, plan.short_circuit_max_ma), (111_111, 185_185))
        self.assertIn("PWM group FAN: board short-circuit detector at 500 mV trips at 111.1 A .. 185.2 A "
                      "(R_DS(on) 2700 µΩ at 25 °C, 4500 µΩ hot)", powerstage.info(config))

    def test_blanking_shorter_than_the_turn_on(self):
        board, project = self.profile(short_delay_ns=750)
        board["pwm"][0]["power_stage"]["switch"]["rise_time_ns"] = 800             # 8 + 800 ns > 750 ns
        _, messages = self.run_check(board, project)
        self.assertEqual(findings(messages), [("warning", "modules.GD.short_delay_ns")])
        self.assertIn("short-circuit blanking of GD (short_delay_ns) of 750 ns ends before the switch has turned on "
                      "(808 ns)", messages[0].text)

    def test_gate_driver_without_pwm_group(self):
        board, project = self.profile(short_to_ground_mv=1000)
        del board["pwm"][0]["gate_driver"]
        config, messages = self.run_check(board, project)
        # IO-45: and the enable rule of the driver has no PWM group to look at (board devices[2] is GD)
        self.assertEqual(findings(messages), [("warning", "devices[2]"), ("warning", "modules.GD.short_to_ground_mv")])
        by_path = {m.yaml_path: m for m in messages}
        self.assertIn("GD drives no PWM group of the project", by_path["modules.GD.short_to_ground_mv"].text)
        self.assertEqual(by_path["devices[2]"].file.name, "board.yaml")
        self.assertIn("gate driver GD is bound to no PWM group (board pwm[].gate_driver): the enable rule of GD "
                      "(gdTmc6200: all 3 phases in one state) is not applied, and its enable belongs to no safe-output "
                      "group", by_path["devices[2]"].text)
        self.assertEqual(config.power_stages["FAN"].driver_settings, ())

    def test_gate_driver_group_with_other_pair_count(self):
        """IO-45: one pair per phase of the gate driver (module.yaml phases), else the enable rule is not applied."""
        for pairs in (1, 2):
            with self.subTest(pairs=pairs):
                board, project = self.profile()
                group = board["pwm"][0]
                group["pairs"] = group["pairs"][:pairs]
                group["channels"] = group["channels"][:2 * pairs]
                config, messages = self.run_check(board, project)
                self.assertEqual(findings(messages), [("warning", "pwm[0].pairs")])
                self.assertEqual(messages[0].file.name, "board.yaml")
                self.assertIn(f"PWM group FAN has {pairs} pair{'s' if pairs > 1 else ''}, its gate driver GD "
                              f"(gdTmc6200) drives 3 phases: the enable rule of GD", messages[0].text)
                (gd,) = [d for d in config.integration.devices if d.name == "GD"]
                self.assertEqual(gd.pwm, "FAN")        # still bound: its enable joins the group of FAN (SYS-117)
        # three pairs: nothing to say
        config, messages = self.run_check(*self.profile())
        self.assertEqual(findings(messages), [])

    def test_gate_driver_group_not_used_by_the_project(self):
        """IO-45: a project without the power stage (ARCH-17) gets no warning; the device has no group then."""
        board, project = self.profile()
        project.pop("pwm")
        config, messages = self.run_check(board, project)
        self.assertEqual([p for p in findings(messages) if p[1].startswith(("pwm", "devices"))], [])
        (gd,) = [d for d in config.integration.devices if d.name == "GD"]
        self.assertIsNone(gd.pwm)

    def test_phases_only_for_a_gate_driver(self):
        """IO-45: module.yaml phases belongs to a module that provides the role gate_driver."""
        folder = self.tmp / "manifests" / "noGdPhases"
        folder.mkdir(parents=True)
        (folder / "module.yaml").write_text("module: noGdPhases\nkind: device_driver\nparts: ['X*']\nphases: 3\n",
                                            encoding="utf-8")
        manifests, messages = checks._manifests(nxconfig_config.load_modules((folder.parent,)))
        self.assertEqual([(m.severity, m.yaml_path) for m in messages], [("error", "phases")])
        self.assertIn("phases only applies to modules that provide the role gate_driver", messages[0].text)
        self.assertNotIn("noGdPhases", manifests)
        shutil.rmtree(folder.parent)

    def test_values_outside_the_steps_are_rejected(self):
        for key, value, fragment in (("short_filter_ns", 500, "500 is not one of 0, 100, 1000, 2000, 3000 ns"),
                                     ("short_delay_ns", 1000, "1000 is not one of 0, 750, 1500 ns"),
                                     ("short_to_supply_mv", 1561, "1561 is out of range 0..1560 mV")):
            with self.subTest(key=key):
                errors = [m for m in self.run_check(*self.profile(**{key: value}))[1] if m.severity == "error"]
                self.assertEqual(findings(errors), [("error", f"modules.GD.{key}")])
                self.assertIn(fragment, errors[0].text)

    def test_manifest_rules(self):
        """power_stage only on int parameters in the right unit of a gate driver module; values in min..max."""
        base = ("module: {name}\nkind: device_driver\nparts: ['X*']\n{provides}parameters:\n  p: {spec}\n")
        cases = [
            ("noGd", "", "{ type: int, min: 0, max: 9, default: 0, unit: mV, power_stage: vds_threshold_mv }",
             "power_stage only applies to modules that provide the role gate_driver"),
            ("unitGd", "provides: [gate_driver]\n",
             "{ type: int, min: 0, max: 9, default: 0, unit: ns, power_stage: vds_threshold_mv }",
             "must be an int parameter in mV (is int in ns)"),
            ("stepsGd", "provides: [gate_driver]\n", "{ type: int, min: 0, max: 9, default: 0, values: [0, 10] }",
             "values 10 of p are out of range 0..9"),
            ("strGd", "provides: [gate_driver]\n", "{ type: str, default: a, values: [1] }",
             "values only applies to int parameters"),
        ]
        for name, provides, spec, fragment in cases:
            with self.subTest(name=name):
                folder = self.tmp / "manifests" / name
                folder.mkdir(parents=True)
                (folder / "module.yaml").write_text(base.format(name=name, provides=provides, spec=spec),
                                                    encoding="utf-8")
                manifests, messages = checks._manifests(nxconfig_config.load_modules((folder.parent,)))
                texts = [m.text for m in messages if m.yaml_path.startswith("parameters.p")]
                self.assertTrue(any(fragment in t for t in texts), texts)
                self.assertNotIn(name, manifests)
                shutil.rmtree(folder.parent)


# ARCH-37: verify: true on used and unused values of TESTMCU64 (table in the profile file)
VERIFY_OVERLAY = yaml.safe_load(
    (HERE / "test/profiles/derivatives/testmcu64_verify.yaml").read_text(encoding="utf-8"))


class UnconfirmedDerivativeTest(Profiles):
    """ARCH-37 (finding DOC-08): nxconfig check warns about every verify: true value of the derivative the project
    uses - read by a check of the project or needed by a generator - and about no other, one warning per entry."""

    USED = {"instances.ADC1.verify", "clock.oscillator.verify", "clock.verify", "mpu.verify"}

    def derivative(self, change=None, flags=True) -> Path:
        """Test mcudb with the overlay laid over TESTMCU64; flags=False: the same values without verify."""
        def apply(doc):
            overlay = copy.deepcopy(VERIFY_OVERLAY)
            if not flags:
                def strip(node):
                    if isinstance(node, dict):
                        node.pop("verify", None)
                        for value in node.values():
                            strip(value)
                    elif isinstance(node, list):
                        for value in node:
                            strip(value)
                strip(overlay)
            doc["instances"].update(overlay.pop("instances"))
            doc.update(overlay)
            if change is not None:
                change(doc)
        shutil.rmtree(self.tmp / "mcudb", ignore_errors=True)
        return PinmuxTest.derivative(self, apply)

    def unconfirmed(self, board=None, project=None, mcudb=None) -> dict[str, nxconfig.Message]:
        config, messages = self.check(board, project, self.derivative() if mcudb is None else mcudb)
        self.assertIsNotNone(config, [str(m) for m in messages])
        found = [m for m in messages if "verify: true" in m.text]
        self.assertEqual([str(m) for m in messages if m not in found], [])
        paths = [m.yaml_path for m in found]
        self.assertEqual(len(paths), len(set(paths)), "one warning per entry")
        return {m.yaml_path: m for m in found}

    def test_used_values_warn_with_position(self):
        mcudb = self.derivative()
        found = self.unconfirmed(mcudb=mcudb)
        self.assertEqual(set(found), self.USED)
        lines = (mcudb / "testfam" / "TESTMCU64.yaml").read_text(encoding="utf-8").splitlines()
        for m in found.values():
            self.assertEqual((m.severity, m.file.name), ("warning", "TESTMCU64.yaml"))
            self.assertIn("verify: true", lines[m.line - 1], str(m))
        self.assertEqual([m.line for m in found.values()], sorted(m.line for m in found.values()))
        # what is unconfirmed and what the project needs it for
        self.assertIn("clock.oscillator (min_hz, max_hz) of TESTMCU64", found["clock.oscillator.verify"].text)
        self.assertIn("the clock plan", found["clock.oscillator.verify"].text)
        self.assertIn("clock (max_hz)", found["clock.verify"].text)
        # read by the check of adc_internal, and switched on for the board channel VBAT
        self.assertIn("instances.ADC1 (internal_channels) of TESTMCU64", found["instances.ADC1.verify"].text)
        self.assertIn("internal ADC channels", found["instances.ADC1.verify"].text)
        self.assertTrue(found["instances.ADC1.verify"].text.endswith(", adc VBAT"))
        self.assertIn("mpu (regions)", found["mpu.verify"].text)
        self.assertIn("NX_MPU_REGIONS", found["mpu.verify"].text)

    def test_unused_values_do_not_warn(self):
        # USART3 has no board entry, the flash is not partitioned by testproject (no memory section)
        found = self.unconfirmed()
        self.assertNotIn("instances.USART3.verify", found)
        self.assertNotIn("memory[0].verify", found)

    def test_counter_check_without_verify(self):
        _, messages = self.check(mcudb=self.derivative(flags=False))
        self.assertEqual([str(m) for m in messages], [])

    def test_counter_check_use_follows_the_project(self):
        # without crystal the oscillator range is only checked against itself (min <= max): not used
        board = self.board()
        del board["board"]["clock"]
        self.assertEqual(set(self.unconfirmed(board=board)), self.USED - {"clock.oscillator.verify"})
        # without ADC channels ADC1 is not used, although the board wires two channels to it
        project = self.project()
        project["use"]["adc"] = []
        del project["adc_internal"]
        self.assertEqual(set(self.unconfirmed(project=project)), self.USED - {"instances.ADC1.verify"})
        # a project with bootloader partitions the flash: now the sector geometry is used
        project = copy.deepcopy(BL_PROJECT)
        project["project"]["board"] = "../../boards/testboard"
        found = self.unconfirmed(project=project)
        self.assertIn("memory[0].verify", found)
        self.assertIn("memory layout of bootloader and application", found["memory[0].verify"].text)
        self.assertIn("sector_size", found["memory[0].verify"].text)

    def test_used_pins_one_warning(self):
        def unconfirmed_pins(d):
            d["pinmux"]["QFP64"]["verify"] = True
        found = self.unconfirmed(mcudb=self.derivative(unconfirmed_pins))
        text = found["pinmux.QFP64.verify"].text
        self.assertIn("pin PA5 (spi MEM_SPI (sck))", text)
        self.assertRegex(text, r"and \d+ more$")

    def test_reads_are_recorded_only_during_the_check(self):
        config, _ = self.check(mcudb=self.derivative())
        self.assertIsNone(nxconfig.model._USE.get())
        use = nxconfig.model.DerivativeUse()
        config.derivative.clock.oscillator.min_hz                   # outside `with use`: nothing recorded
        self.assertEqual(use.entries, {})
        with use:
            config.derivative.clock.oscillator.min_hz
            config.derivative.clock.oscillator.verify               # bookkeeping key: no value
            config.derivative.clock.oscillator                      # nested entry: no value of clock
        self.assertEqual([list(e.fields) for e in use.entries.values()], [["min_hz"]])


class DeviceCountTest(Profiles):
    """ARCH-44: one provider of the role power_supply (one SBC) and exactly the SPI buses a module requires, found by
    nxconfig with file, line and YAML path instead of by the compiler in bsp/sbc/sbcConfiguration.h; the SBC as
    CAN transceiver only (finding of ARCH-41: bare next() in checks.integrate).

    Profile testboard_s32k_8mhz (S32K144 LQFP64, real mcudb) with PHY_A replaced by a UJA113x on VEH, and two SPI
    buses on LPSPI1/LPSPI2. The rule reads module.yaml (provides, requires.spi_device); synthetic manifests show it
    does not depend on the part: a PMIC module providing power_supply counts, a module requiring two SPI buses gets
    two. The test of the same rule against the port header is BswGenSbcConfigurationProbeTest in
    tools/bswgen/test_bswgen.py.
    """

    SBC = {"name": "SBC", "part": "UJA1131HW/FD/5V/4Y", "module": "sbcUja113x"}
    BUSES = [{"name": "SPI_A", "instance": "LPSPI1", "sck": {"pin": "PTD0"}, "sin": {"pin": "PTD1"},
              "sout": {"pin": "PTD2"}, "pcs": {"pin": "PTD3", "index": 0}},
             {"name": "SPI_B", "instance": "LPSPI2", "sck": {"pin": "PTC15"}, "sin": {"pin": "PTC0"},
              "sout": {"pin": "PTC1"}, "pcs": {"pin": "PTC14", "index": 0}}]

    def write(self, board=None, project=None) -> Path:
        path = super().write(S32K_BOARD if board is None else board, S32K_PROJECT if project is None else project)
        # the rank of the used CAN bus stays; the board directory is the one Profiles.write creates
        text = path.read_text(encoding="utf-8").replace("boards/testboard_s32k_8mhz", "boards/testboard")
        path.write_text(text, encoding="utf-8")
        return path

    def check(self, board=None, project=None, mcudb=nxconfig_config.MCUDB, dirs=None):
        if dirs is None:
            return nxconfig.check(self.write(board, project), mcudb)
        return nxconfig.check(self.write(board, project), mcudb, dirs)

    def sbc_board(self, *devices: dict, spi_devices=("SBC", "SBC")) -> dict:
        """PHY_A becomes the SBC (transceiver of VEH); SPI_A/SPI_B lead to the devices in spi_devices."""
        board = copy.deepcopy(S32K_BOARD)
        board["devices"][0] = dict(self.SBC)
        board["can"][0]["phy"] = "SBC"
        board["devices"].extend(devices)
        board["spi"] = [dict(bus, **({"device": d} if d else {})) for bus, d in zip(self.BUSES, spi_devices)]
        return board

    @staticmethod
    def sbc_project(spi) -> dict:
        project = copy.deepcopy(S32K_PROJECT)
        project["use"]["spi"] = spi
        return project

    def manifest_dirs(self, **extra):
        return ModuleCheckTest.manifest_dirs(self, **extra)

    def error_messages(self, board, project, dirs=None) -> list[nxconfig.Message]:
        config, messages = self.check(board, project, dirs=dirs)
        errors = [m for m in messages if m.severity == "error"]
        self.assertEqual(config is None, bool(errors))
        return errors

    def assert_line_names(self, message, fragment: str):
        self.assertIsNotNone(message.line, str(message))
        self.assertIn(fragment, message.file.read_text(encoding="utf-8").splitlines()[message.line - 1], str(message))

    def test_one_sbc_on_one_bus_is_accepted(self):
        for spi, bus in ((["SPI_A"], "SPI_A"), (["SPI_B"], "SPI_B")):
            with self.subTest(spi=spi):
                config, messages = self.check(self.sbc_board(), self.sbc_project(spi))
                self.assertEqual([m for m in messages if m.severity == "error"], [], [str(m) for m in messages])
                [sbc] = config.integration.instances("sbcUja113x")
                self.assertEqual((sbc.spi, sbc.can_phy), ([bus], ["VEH"]))

    def test_sbc_on_two_used_buses(self):
        for spi, path, bus in ((["SPI_A", "SPI_B"], "use.spi[1]", "SPI_B"), (["SPI_B", "SPI_A"], "use.spi[0]", "SPI_B"),
                               ("all", "use.spi", "spi:")):
            with self.subTest(spi=spi):
                [m] = self.error_messages(self.sbc_board(), self.sbc_project(spi))
                self.assertEqual((m.file.name, m.yaml_path), ("project.yaml", path))
                self.assertIn("device SBC (sbcUja113x) needs exactly 1 used SPI bus (module requires spi_device: 1), "
                              "the project uses 2: SPI_A, SPI_B", m.text)
                self.assert_line_names(m, bus)

    def test_two_sbc(self):
        second = {"name": "SBC2", "part": "UJA1132HW/5V/4Y"}         # module from the part (parts: UJA1132*)
        board = self.sbc_board(second, spi_devices=("SBC", "SBC2"))
        [m] = self.error_messages(board, self.sbc_project(["SPI_A", "SPI_B"]))
        self.assertEqual((m.file.name, m.yaml_path), ("project.yaml", "use.spi[1]"))
        self.assertIn("device SBC2 (sbcUja113x) is a second provider of the role power_supply: the project already "
                      "uses SBC (sbcUja113x)", m.text)
        self.assert_line_names(m, "SPI_B")
        # on the board only, not used by the project: no finding (a board may carry more than the project needs)
        _, messages = self.check(board, self.sbc_project(["SPI_A"]))
        self.assertEqual([m for m in messages if m.severity == "error"], [])

    def test_the_role_counts_not_the_part(self):
        dirs = self.manifest_dirs(pmicTest="\n".join([
            "module: pmicTest", "kind: device_driver", "parts: [PMIC*]",
            "connect: { en: { kind: digital_output } }", "provides: [power_supply]", ""]))
        board = self.sbc_board({"name": "PMIC", "part": "PMIC1", "connect": {"en": "LED"}}, spi_devices=("SBC", None))
        project = self.sbc_project(["SPI_A"])
        project["modules"] = {"PMIC": {}}
        [m] = self.error_messages(board, project, dirs)
        self.assertEqual((m.file.name, m.yaml_path), ("project.yaml", "modules.PMIC"))
        self.assertIn("device PMIC (pmicTest) is a second provider of the role power_supply", m.text)
        self.assert_line_names(m, "PMIC")
        # the same module without the role: no finding
        dirs = self.manifest_dirs(pmicTest="\n".join([
            "module: pmicTest", "kind: device_driver", "parts: [PMIC*]",
            "connect: { en: { kind: digital_output } }", "provides: [switched_output]", ""]))
        self.assertEqual(self.error_messages(board, project, dirs), [])

    def test_required_bus_count_comes_from_the_manifest(self):
        dirs = self.manifest_dirs(twoBus="module: twoBus\nkind: device_driver\nparts: [TWO*]\n"
                                         "requires: { spi_device: 2 }\n")
        board = self.sbc_board({"name": "DUAL", "part": "TWO1"}, spi_devices=("DUAL", "DUAL"))
        board["devices"][0], board["can"][0]["phy"] = {"name": "PHY_A", "part": "TJA1051"}, "PHY_A"   # no SBC here
        config, messages = self.check(board, self.sbc_project("all"), dirs=dirs)
        self.assertEqual([m for m in messages if m.severity == "error"], [], [str(m) for m in messages])
        self.assertEqual(config.integration.instances("twoBus")[0].spi, ["SPI_A", "SPI_B"])
        [m] = self.error_messages(board, self.sbc_project(["SPI_B"]), dirs)
        self.assertEqual(m.yaml_path, "use.spi[0]")
        self.assertIn("device DUAL (twoBus) needs exactly 2 used SPI buses (module requires spi_device: 2), the "
                      "project uses 1: SPI_B", m.text)

    def test_sbc_only_as_can_transceiver(self):
        """Finding of ARCH-41: an SBC that no spi[].device names, used as can.phy only. Reported, no exception."""
        board = self.sbc_board(spi_devices=(None, None))
        for spi in ([], ["SPI_A"]):
            with self.subTest(spi=spi):
                [m] = self.error_messages(board, self.sbc_project(spi))
                self.assertEqual((m.file.name, m.yaml_path), ("board.yaml", "devices[0]"))
                self.assertIn("device SBC (sbcUja113x) needs an SPI bus with device: SBC", m.text)
                self.assert_line_names(m, "SBC")
        # its bus on the board, but not used by the project: reported at the bus
        [m] = self.error_messages(self.sbc_board(spi_devices=(None, "SBC")), self.sbc_project(["SPI_A"]))
        self.assertEqual((m.file.name, m.yaml_path), ("board.yaml", "spi[1].device"))
        self.assertIn("CAN bus VEH uses device SBC as transceiver, but the project does not use its SPI bus SPI_B",
                      m.text)


class CanWakeupFrameTest(Profiles):
    """COM-21 (diff proposals 1-2 of COM-07): project.yaml modules.<sbc>.can_wakeup_frame, checked with YAML path,
    bit rate derived from can.<bus>.bitrate. Profile of DeviceCountTest (not the eval board): UJA1131HW/FD as
    transceiver of VEH on FlexCAN0, VEH at 500 kbit/s (250 kbit/s in one case), SBC on SPI_A."""

    SBC, BUSES = DeviceCountTest.SBC, DeviceCountTest.BUSES
    sbc_board = DeviceCountTest.sbc_board
    sbc_project = staticmethod(DeviceCountTest.sbc_project)
    error_messages = DeviceCountTest.error_messages

    def write(self, board=None, project=None) -> Path:
        path = super().write(board, project)
        text = path.read_text(encoding="utf-8").replace("boards/testboard_s32k_8mhz", "boards/testboard")
        path.write_text(text, encoding="utf-8")
        return path

    def check(self, board=None, project=None, mcudb=nxconfig_config.MCUDB, dirs=None):
        return nxconfig.check(self.write(board, project), mcudb)

    def frame_project(self, frame, bitrate=None, ecu_state=None) -> dict:
        project = self.sbc_project(["SPI_A"])
        project["modules"] = {"SBC": {"can_wakeup_frame": frame}}
        if ecu_state is not None:
            project["modules"]["ecuState"] = ecu_state
        if bitrate is not None:
            project["can"]["VEH"]["bitrate"] = bitrate
        return project

    def test_standard_frame_takes_the_bus_bitrate(self):
        config, messages = self.check(self.sbc_board(), self.frame_project({"id": 0x123}))
        self.assertEqual([m for m in messages if m.severity != "info"], [], [str(m) for m in messages])
        [sbc] = config.integration.instances("sbcUja113x")
        f = sbc.can_wakeup_frame
        self.assertEqual((f.bus, f.bitrate, f.id, f.extended, f.compare_mask, f.dlc, f.data_mask, f.fd_passive),
                         ("VEH", 500000, 0x123, False, 0x7FF, None, [], False))
        # the frame is no module parameter; the parameters are the defaults of the battery monitor (IO-33)
        self.assertNotIn("can_wakeup_frame", sbc.parameters)
        self.assertEqual(sbc.parameters["undervoltage_mv"], 9000)

    def test_extended_frame_with_data(self):
        frame = {"id": 0x18DA10F1, "extended": True, "compare_mask": 0x1FFFFF00, "dlc": 2, "data_mask": [0, 4],
                 "fd_passive": True}
        config, messages = self.check(self.sbc_board(), self.frame_project(frame, bitrate=250000))
        self.assertIsNotNone(config, [str(m) for m in messages])
        f = config.integration.instances("sbcUja113x")[0].can_wakeup_frame
        self.assertEqual((f.bitrate, f.id, f.extended, f.compare_mask, f.dlc, f.data_mask, f.fd_passive),
                         (250000, 0x18DA10F1, True, 0x1FFFFF00, 2, [0, 4], True))

    def test_without_frame_nothing_changes(self):
        config, _ = self.check(self.sbc_board(), self.sbc_project(["SPI_A"]))
        self.assertIsNone(config.integration.instances("sbcUja113x")[0].can_wakeup_frame)

    def test_errors_with_path(self):
        base = "modules.SBC.can_wakeup_frame"
        cases = [
            ({"id": 0x800}, None, f"{base}.id", "id 0x800 is wider than 11 bit (extended: true"),
            ({"id": 0x20000000, "extended": True}, None, f"{base}.id", "id 0x20000000 is wider than 29 bit"),
            ({"id": 0x100, "compare_mask": 0xFFF}, None, f"{base}.compare_mask", "compare_mask 0xFFF is wider than 11"),
            ({"id": 0x100, "dlc": 2, "data_mask": [1]}, None, f"{base}.data_mask", "data_mask has 1 entries, dlc is 2"),
            ({"id": 0x100, "dlc": 1, "data_mask": [0]}, None, f"{base}.data_mask", "would never wake the ECU"),
            ({"id": 0x100, "data_mask": [1]}, None, f"{base}.data_mask", "data_mask needs dlc"),
            ({"id": 0x100}, 83333, base, "CAN bus VEH runs at 83333 bit/s (can.VEH.bitrate), partial networking "
                                         "decodes only 50000, 100000, 125000, 250000, 500000, 1000000 bit/s"),
        ]
        for frame, bitrate, path, fragment in cases:
            with self.subTest(frame=frame, bitrate=bitrate):
                errors = self.error_messages(self.sbc_board(), self.frame_project(frame, bitrate))
                match = [m for m in errors if fragment in m.text]
                self.assertTrue(match, [str(m) for m in errors])
                self.assertEqual((match[0].file.name, match[0].yaml_path), ("project.yaml", path))
                self.assertIsNotNone(match[0].line)

    def test_schema_errors(self):
        for frame in ({"id": 0x100, "dlc": 9}, {"id": 0x100, "data_mask": [256]}, {"id": 0x100, "foo": 1}, {}):
            with self.subTest(frame=frame):
                self.assertTrue(self.error_messages(self.sbc_board(), self.frame_project(frame)))

    def test_part_without_partial_networking(self):
        board = self.sbc_board()
        board["devices"][0]["part"] = "UJA1131HW/5V0"
        [m] = self.error_messages(board, self.frame_project({"id": 0x123}))
        self.assertEqual(m.yaml_path, "modules.SBC.can_wakeup_frame")
        self.assertIn("part UJA1131HW/5V0 of device SBC has no partial networking", m.text)

    def test_bus_not_waking_is_a_warning(self):
        config, messages = self.check(self.sbc_board(), self.frame_project({"id": 0x123}, ecu_state={"wakeup_can": "none"}))
        self.assertIsNotNone(config)
        [w] = [m for m in messages if m.severity == "warning"]
        self.assertEqual(w.yaml_path, "modules.SBC.can_wakeup_frame")
        self.assertIn("CAN bus VEH is not in modules.ecuState.wakeup_can (none)", w.text)

    def test_only_the_sbc_module_takes_it(self):
        project = self.frame_project({"id": 0x123})
        project["modules"]["ecuState"] = {"can_wakeup_frame": {"id": 0x123}}
        errors = self.error_messages(self.sbc_board(), project)
        self.assertTrue([m for m in errors if "module ecuState has no parameter can_wakeup_frame" in m.text],
                        [str(m) for m in errors])


# COM-23 (review question 5 of COM-22): the latency promise of the CAN system needs the RTE's CAN task below task can
class CanTaskOrderTest(Profiles):
    """Warning with YAML path in rte.yaml if the CAN task of the RTE does not run below the CAN system's task."""

    def check_ecu(self, ecu):
        project = self.project()
        project["project"]["rte"] = "rte.yaml"
        path = self.write(project=project)
        (path.parent / "rte.yaml").write_text(yaml.safe_dump(ecu, sort_keys=False), encoding="utf-8")
        return nxconfig.check(path, TEST_MCUDB)

    def test_rte_can_task_below_can_is_quiet(self):
        config, messages = self.check_ecu(TEST_ECU)                 # can.task fast (25), task can 40
        self.assertEqual(messages, [])
        self.assertIsNotNone(config)

    def test_rte_can_task_above_can_warns_at_its_priority(self):
        ecu = copy.deepcopy(TEST_ECU)
        ecu["can"]["task"] = "slow"
        ecu["tasks"][1]["priority"] = 45
        config, messages = self.check_ecu(ecu)
        self.assertIsNotNone(config)                                # a warning, the build goes on
        [m] = messages
        self.assertEqual((m.severity, m.file.name, m.yaml_path), ("warning", "rte.yaml", "tasks[1].priority"))
        self.assertIn("CAN task slow of the RTE (priority 45) does not run below task can of the CAN system "
                      "(priority 40)", m.text)
        self.assertIn("up to 100 ms instead of 50 ms", m.text)      # period of task slow
        self.assertIn("give it a priority below 40", m.text)
        self.assertIn("priority: 45", m.file.read_text(encoding="utf-8").splitlines()[m.line - 1])
        # same key as task can: not below either (tools/rtegen refuses the duplicate key as well)
        ecu["tasks"][1]["priority"] = 40
        _, messages = self.check_ecu(ecu)
        self.assertEqual(findings(messages), [("warning", "tasks[1].priority")])
        # a task above can that carries no CAN signals does not matter
        ecu["can"]["task"] = "fast"
        _, messages = self.check_ecu(ecu)
        self.assertEqual(messages, [])

    def test_only_task_without_can_task(self):
        ecu = copy.deepcopy(TEST_ECU)
        ecu["tasks"] = [ecu["tasks"][0]]
        del ecu["can"]["task"]
        ecu["tasks"][0]["priority"] = 41
        _, messages = self.check_ecu(ecu)
        self.assertEqual(findings(messages), [("warning", "tasks[0].priority")])
        self.assertIn("up to 10 ms instead of 5 ms", messages[0].text)
        # several tasks and no can.task, unknown can.task, no CAN signals: nothing to say here (tools/rtegen reports
        # the first two)
        for change in (lambda e: e["can"].pop("task"), lambda e: e["can"].update(task="none"), lambda e: e.pop("can")):
            ecu = copy.deepcopy(TEST_ECU)
            ecu["tasks"][0]["priority"] = 41
            ecu["tasks"][1]["priority"] = 42
            change(ecu)
            _, messages = self.check_ecu(ecu)
            self.assertEqual([m for m in messages if "does not run below" in m.text], [])

    def test_manifest_and_default_application_task(self):
        keys = checks.bsw_task_keys()                               # ecu/asyncCoreConfiguration/tasks.yaml
        self.assertEqual(keys.bsw["can"], 40)
        self.assertEqual(keys.default, ("swc", 30))
        self.assertEqual(checks.bsw_task_keys(self.tmp / "missing.yaml"), checks.TaskKeys({}, None))
        self.assertIsNone(checks.can_task_order_problem("swc", 30, "can", 40, 10))
        self.assertIn("one cycle later", checks.can_task_order_problem("swc", 50, "can", 40, None))
        # ECU description without tasks: the default application task of the manifest; an own manifest with a
        # default above task can is reported at can.task (or can: without the key)
        ecu = copy.deepcopy(TEST_ECU)
        ecu.pop("tasks")
        for t in ("fast", None):
            if t is None:
                ecu["can"].pop("task")
            config, messages = self.check_ecu(ecu)
            self.assertEqual([m for m in messages if "does not run below" in m.text], [])
            manifest = self.tmp / "tasks.yaml"
            manifest.write_text("tasks:\n  - { name: can, priority: 40 }\n"
                                "application_default: { name: app, priority: 50, stack_bytes: 1024 }\n",
                                encoding="utf-8")
            rep = nxconfig_config.Reporter()
            checks._can_task_order(config, rep, manifest)
            self.assertEqual(findings(rep.messages), [("warning", "can.task" if t else "can")])
            self.assertIn("CAN task app of the RTE (priority 50)", rep.messages[0].text)
            # a manifest without task can (another CAN system task name): nothing to compare
            manifest.write_text("tasks:\n  - { name: bsp, priority: 20 }\n", encoding="utf-8")
            rep = nxconfig_config.Reporter()
            checks._can_task_order(config, rep, manifest)
            self.assertEqual(rep.messages, [])


class ManifestCatalogueTest(Profiles):
    """DOC-14: manifest fields for the module catalogue - verification, verification_note and the kinds library and
    contract. Own profile: testboard_switches/testproject_switches (two BTS5030 on TESTMCU64) with manifests written
    by the tests next to the repository's libs/bsw and libs/bsp."""

    board, project = staticmethod(ModuleCheckTest.board), staticmethod(ModuleCheckTest.project)

    def write(self, board=None, project=None) -> Path:
        project = copy.deepcopy(SWITCH_PROJECT if project is None else project)
        project["project"]["board"] = "../../boards/testboard"          # directory written by Profiles.write
        return super().write(SWITCH_BOARD if board is None else board, project)

    LIBRARY = ("module: {name}\nkind: library\ndescription: test library\ncmake_target: {name}\n"
               "verification: mock\n")

    def module_dir(self, **manifests) -> Path:
        base = self.tmp / "catalogue"
        for name, text in manifests.items():
            (base / name).mkdir(parents=True, exist_ok=True)
            (base / name / "module.yaml").write_text(text, encoding="utf-8")
        return base

    def test_repository_manifests_state_their_verification(self):
        modules = nxconfig_config.load_modules()
        self.assertEqual(modules.messages, [])
        libs = {n: m for n, m in modules.manifests.items()
                if modules.files[n].is_relative_to(REPO / "libs")}
        self.assertEqual({n: m.verification for n, m in libs.items() if m.verification is None}, {})
        for name, m in libs.items():
            with self.subTest(module=name):
                # hardware needs the board evidence next to it; every note is one short sentence
                if m.verification == "hardware":
                    self.assertTrue(m.verification_note)
                if m.verification_note:
                    self.assertLessEqual(len(m.verification_note), 400)
        self.assertEqual(sorted(n for n, m in libs.items() if m.kind in ("library", "contract")),
                         ["autosarBase", "bspI2c", "bspPinEdge", "bspSampleGroup", "bspSpi", "consoleContext",
                          "e2e"])
        # the parked manifest carries valid fields and stays parked (nxconfig loads module.yaml only). Only these
        # keys are validated: the parked file as a whole does not fit the schema yet (connect.<name>.optional).
        pending = REPO / "libs/bsw/bccMc33771/module.yaml.pending"
        if pending.is_file():               # PLAT-62: the NDA driver is not in the exported open tree
            data = yaml.safe_load(pending.read_text(encoding="utf-8"))
            keys = ("module", "kind", "description", "verification", "verification_note")
            self.assertEqual(Manifest.model_validate({k: data[k] for k in keys}).verification, "mock")
        self.assertNotIn("bccMc33771", modules.manifests)

    def test_valid_fields(self):
        base = self.module_dir(
            libA=self.LIBRARY.format(name="libA"),
            conB="module: conB\nkind: contract\ncmake_target: conB\nverification: hardware\n"
                 "verification_note: Exercised on a board through one implementation.\n",
            svcC="module: svcC\nkind: service\nverification: none\n",
            svcD="module: svcD\nkind: service\n")                  # verification left out: not stated
        modules = nxconfig_config.load_modules((base,))
        self.assertEqual(modules.messages, [])
        self.assertEqual({n: (m.kind, m.verification) for n, m in modules.manifests.items()},
                         {"libA": ("library", "mock"), "conB": ("contract", "hardware"),
                          "svcC": ("service", "none"), "svcD": ("service", None)})
        self.assertEqual(modules.manifests["conB"].verification_note, "Exercised on a board through one implementation.")

    def test_invalid_fields_with_yaml_path(self):
        cases = [
            ("badLevel", "module: badLevel\nkind: service\nverification: board\n", "verification",
             "Input should be 'none', 'mock' or 'hardware'"),
            ("boolLevel", "module: boolLevel\nkind: service\nverification: true\n", "verification",
             "Input should be 'none', 'mock' or 'hardware'"),
            ("noteAlone", "module: noteAlone\nkind: service\nverification_note: Checked on a board.\n",
             "verification_note", "verification_note needs verification"),
            ("emptyNote", "module: emptyNote\nkind: service\nverification: mock\nverification_note: ''\n",
             "verification_note", "at least 1 character"),
            ("longNote", "module: longNote\nkind: service\nverification: mock\nverification_note: " + "x" * 401 + "\n",
             "verification_note", "at most 400 characters"),
            ("badKind", "module: badKind\nkind: helper\n", "kind", "'library' or 'contract'"),
            ("libParts", "module: libParts\nkind: library\nparts: [BTS5030*]\n", "parts",
             "a library is a catalogue entry only"),
            ("libRole", "module: libRole\nkind: library\nprovides: [can_phy]\n", "provides", "takes no provides"),
            ("libParams", "module: libParams\nkind: library\nparameters:\n  a: { type: int, min: 0, max: 1 }\n",
             "parameters", "takes no parameters"),
            ("conProject", "module: conProject\nkind: contract\nproject_parameters: true\n", "project_parameters",
             "a contract is a catalogue entry only"),
            ("conBus", "module: conBus\nkind: contract\nrequires: { spi_device: 1 }\n", "requires",
             "takes no requires"),
            ("conLife", "module: conLife\nkind: contract\nlifecycle: { level: 1, task: bsp }\nconsole: spi\n",
             "lifecycle", "takes no lifecycle"),
        ]
        for name, text, path, fragment in cases:
            with self.subTest(module=name):
                base = self.module_dir(**{name: text})
                modules = nxconfig_config.load_modules((base,))
                self.assertNotIn(name, modules.manifests)
                match = [m for m in modules.messages if m.yaml_path == path]
                self.assertTrue(match, [str(m) for m in modules.messages])
                self.assertIn(fragment, match[0].text)
                self.assertEqual(match[0].file, base / name / "module.yaml")
                self.assertEqual(match[0].line, text.splitlines().index(
                    next(line for line in text.splitlines() if line.startswith(path + ":"))) + 1)
                # the manifest is reported by nxconfig check of a project as well, with file and line
                _, messages = nxconfig.check(self.write(), TEST_MCUDB, (*nxconfig_config.MODULE_DIRS, base))
                self.assertIn((match[0].file, path, match[0].line),
                              [(m.file, m.yaml_path, m.line) for m in messages if m.severity == "error"])
                shutil.rmtree(base / name)
        # the second field of conLife is reported as well
        base = self.module_dir(conLife=cases[-1][1])
        self.assertEqual(sorted(m.yaml_path for m in nxconfig_config.load_modules((base,)).messages),
                         ["console", "lifecycle"])

    def test_library_and_contract_change_nothing_in_a_project(self):
        # same project with and without catalogue entries: no message, same devices, roles, modules, generated files
        from bswgen import modules as bswmodules
        base = self.module_dir(libA=self.LIBRARY.format(name="libA"),
                               conB="module: conB\nkind: contract\nverification: mock\n")
        results = []
        for dirs in (nxconfig_config.MODULE_DIRS, (*nxconfig_config.MODULE_DIRS, base)):
            config, messages = nxconfig.check(self.write(), TEST_MCUDB, dirs)
            self.assertEqual(messages, [])
            integration = config.integration
            modules = nxconfig_config.load_modules(dirs)
            results.append(((integration.devices, integration.can, integration.modules),
                            bswmodules.render_all(integration, modules, "project.yaml")))
        self.assertEqual(results[0], results[1])
        self.assertNotIn("libA", results[1][1]["integration/moduleConfiguration.h"])

    def test_library_and_contract_are_no_device_drivers(self):
        # a board device may not name a library as its module, a project entry may not give it values
        board = self.board()
        board["devices"][1]["module"] = "e2e"
        self.expect("module e2e is a library, not a device driver", board=board, level="board",
                    path="devices[1].module")
        project = self.project()
        project["modules"]["bspSpi"] = {"frame_bits": 16}
        error = self.expect("takes no parameters from the project", project=project, level="project",
                            path="modules.bspSpi")
        self.assertIn("module bspSpi (contract)", error.text)


def patch_limits(limits: dict):
    """ARCH-43: NAME_LIMITS of checks.py with some limits replaced, for the duration of a with block."""
    import unittest.mock
    return unittest.mock.patch.dict(checks.NAME_LIMITS, limits)


class NameLengthTest(Profiles):
    """ARCH-43: names of the configuration that end up in log lines (SYS-16 static_asserts) are checked by nxconfig
    against the limits of the code, with file, line and YAML path, before the build stops at the static_assert.

    Profile testboard_s32k_8mhz (S32K144, family s32k1xx: the family port defines the controller and gate driver
    limits) with a TMC6200 on an own SPI bus; the high-side switch labels on testboard_switches (family testfam,
    no family port: only the limits of libs/ apply). The limits are read from the real C++ files; the controller
    limit (all real controller names are short) is lowered through an own definition, like the targeted error of
    SYS-16.
    """

    LIMIT = 16                  # every name limit of the code today (CanFrameLog.h, CanSystem.cpp, Integration.cpp)
    GD = {"name": "GD", "part": "TMC6200-TA", "module": "gdTmc6200", "connect": {"drv_en": "LED", "fault": "KEY"}}
    SPI = {"name": "SPI_A", "instance": "LPSPI1", "sck": {"pin": "PTD0"}, "sin": {"pin": "PTD1"},
           "sout": {"pin": "PTD2"}, "pcs": {"pin": "PTD3", "index": 0}, "device": "GD"}

    def write(self, board=None, project=None) -> Path:
        project = copy.deepcopy(S32K_PROJECT if project is None else project)
        project["project"]["board"] = "../../boards/testboard"          # directory written by Profiles.write
        return super().write(S32K_BOARD if board is None else board, project)

    def s32k(self, bus: str = "VEH", gate_driver: str = "GD") -> tuple[dict, dict]:
        """Board and project with the used CAN bus called `bus` and a TMC6200 called `gate_driver`."""
        board, project = copy.deepcopy(S32K_BOARD), copy.deepcopy(S32K_PROJECT)
        board["can"][0]["name"] = bus
        board["devices"].append(dict(self.GD, name=gate_driver))
        board["spi"] = [dict(self.SPI, device=gate_driver)]
        project["use"]["can"] = [bus]
        project["use"]["spi"] = ["SPI_A"]
        project["can"] = {bus: project["can"]["VEH"], "DIAG": project["can"]["DIAG"]}
        project["interrupts"]["ranks"] = [{"source": "can", "name": bus, "rank": 3}]
        return board, project

    def name_errors(self, board, project, dirs=None, mcudb=nxconfig_config.MCUDB) -> list[nxconfig.Message]:
        args = () if dirs is None else (dirs,)
        config, messages = nxconfig.check(self.write(board, project), mcudb, *args)
        errors = [m for m in messages if m.severity == "error"]
        self.assertEqual(config is None, bool(errors))
        self.assertEqual([str(m) for m in errors if "the code takes at most" not in m.text], [])
        return errors

    def assert_finding(self, errors, level: str, path: str, *fragments: str):
        self.assertEqual([(m.file.name, m.yaml_path) for m in errors], [(f"{level}.yaml", path)],
                         [str(m) for m in errors])
        [message] = errors
        line = message.file.read_text(encoding="utf-8").splitlines()[message.line - 1]
        for fragment in fragments:
            self.assertIn(fragment, message.text + " | " + line, str(message))

    def test_limits_come_from_the_code(self):
        for family, kinds in (("s32k1xx", ["can_bus", "can_controller", "switch_label", "gate_driver_label"]),
                              ("stm32", ["can_bus", "switch_label"]), ("no_such_family", ["can_bus", "switch_label"])):
            with self.subTest(family=family):
                bounds, messages = checks.name_bounds(family)
                self.assertEqual(messages, [])
                self.assertEqual(sorted(bounds), sorted(kinds))
                for bound in bounds.values():
                    line = bound.file.read_text(encoding="utf-8").splitlines()[bound.line - 1]
                    short = bound.limit.constant.rsplit("::", 1)[-1]
                    self.assertRegex(line, rf"constexpr size_t {short} = {bound.chars}U;")
                    self.assertEqual(bound.chars, self.LIMIT)
        bounds, _ = checks.name_bounds("s32k1xx")
        self.assertEqual({k: b.file.name for k, b in bounds.items()},
                         {"can_bus": "CanFrameLog.h", "can_controller": "CanSystem.cpp",
                          "switch_label": "HighSideSwitchNames.h", "gate_driver_label": "Integration.cpp"})

    def test_constant_parser(self):
        cases = [
            ("static constexpr size_t N = 16U;", [(1, "16U")]),
            ("constexpr std::size_t const N{0x20u};", [(1, "0x20u")]),
            ("inline constexpr uint8_t N =\n    12;", [(1, "12")]),
            ("// constexpr size_t N = 3;\n/* constexpr size_t N = 4;\n */ constexpr size_t N = 5U;", [(3, "5U")]),
            ('char const* s = "constexpr size_t N = 6;"; constexpr size_t N_MORE = 7;', []),
            ("constexpr size_t N = OTHER + 1U;", [(1, "OTHER + 1U")]),
            ("size_t N = 16U; static_assert(len <= N);", []),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(checks.constant_definitions(text, "N"), expected)
        root = self.tmp / "code"
        root.mkdir()
        (root / "a.h").write_text("struct S { static constexpr size_t N = 0x10U; };\n", encoding="utf-8")
        self.assertEqual(checks.read_constant(root, "S::N"), (16, root / "a.h", 1))
        (root / "b.cpp").write_text("\n\nconstexpr size_t N = 16U;\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, r"S::N is defined 2 times .*\(a\.h:1, b\.cpp:3\)"):
            checks.read_constant(root, "S::N")
        (root / "b.cpp").write_text("constexpr size_t M = N + 1U;\n", encoding="utf-8")
        (root / "a.h").write_text("constexpr size_t N = sizeof(int);\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, r"N in a\.h:1 is 'sizeof\(int\)', not an integer literal"):
            checks.read_constant(root, "N")
        with self.assertRaisesRegex(ValueError, "no definition of X"):
            checks.read_constant(root, "X")

    def test_profile_and_names_of_exactly_the_limit(self):
        board, project = self.s32k()
        self.assertEqual(self.name_errors(board, project), [])
        board, project = self.s32k(bus="V" * self.LIMIT, gate_driver="G" * self.LIMIT)
        self.assertEqual(self.name_errors(board, project), [])
        config, _ = nxconfig.check(self.write(board, project), nxconfig_config.MCUDB)
        self.assertEqual([d.name for d in config.integration.instances("gdTmc6200")], ["G" * self.LIMIT])

    def test_can_bus_name(self):
        long = "VEHICLE_BUS_MAIN1"
        board, project = self.s32k(bus=long)
        self.assert_finding(self.name_errors(board, project), "board", "can[0].name",
                            f"name of CAN bus {long} has 17 characters, the code takes at most 16",
                            "CanFrameLog::BUS_NAME_CHARS in libs/bsw/canSystem/include/can/CanFrameLog.h:",
                            f"name: {long}")
        # a name that starts with a digit gets the prefix SIG_ in the code (bswgen identifier)
        board, project = self.s32k(bus="1" + "V" * (self.LIMIT - 1))
        self.assert_finding(self.name_errors(board, project), "board", "can[0].name",
                            f"(in the code SIG_1{'V' * (self.LIMIT - 1)}) has 20 characters")
        # an unused bus is not generated, so its name does not matter
        board, project = self.s32k()
        board["can"][1]["name"] = "DIAGNOSTIC_BUS_NR2"
        project["can"]["DIAGNOSTIC_BUS_NR2"] = project["can"].pop("DIAG")
        self.assertEqual(self.name_errors(board, project), [])

    def test_gate_driver_label(self):
        long = "MOTOR_GATE_DRIVER"
        board, project = self.s32k(gate_driver=long)
        self.assert_finding(self.name_errors(board, project), "board", "devices[2].name",
                            f"label of {long} (gdTmc6200, its device name) has 17 characters",
                            "GATE_DRIVER_LABEL_MAX_CHARS in "
                            "platforms/s32k1xx/port/main/src/integration/Integration.cpp:",
                            f"name: {long}")

    def test_can_controller_name(self):
        port = self.tmp / "port"
        port.mkdir()
        limit = checks.NameLimit("CAN controller name", "CAN_DEVICE_NAME_MAX_CHARS", str(port), required=False)
        board, project = self.s32k()
        for chars, expected in ((len("FlexCAN0"), []), (len("FlexCAN0") - 1, ["can[0].instance"])):
            with self.subTest(chars=chars), patch_limits({"can_controller": limit}):
                (port / "CanSystem.cpp").write_text(f"constexpr size_t CAN_DEVICE_NAME_MAX_CHARS = {chars}U;\n",
                                                    encoding="utf-8")
                errors = self.name_errors(board, project)
                self.assertEqual([m.yaml_path for m in errors], expected)
                if expected:
                    self.assert_finding(errors, "board", "can[0].instance",
                                        "controller name FlexCAN0 of CAN bus VEH has 8 characters, the code takes "
                                        "at most 7", "instance: FlexCAN0")

    def test_unreadable_limit_is_an_error(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        board, project = self.s32k()
        for required in (True, False):
            limit = checks.NameLimit("CAN bus name", "CanFrameLog::BUS_NAME_CHARS", str(empty), required)
            with self.subTest(required=required), patch_limits({"can_bus": limit}):
                config, messages = nxconfig.check(self.write(board, project), nxconfig_config.MCUDB)
                texts = [m.text for m in messages if m.severity == "error"]
                if required:
                    self.assertIsNone(config)
                    self.assertEqual(texts, ["longest CAN bus name unknown (tools/nxconfig/checks.py NAME_LIMITS): "
                                             f"no definition of CanFrameLog::BUS_NAME_CHARS in {empty}"])
                else:
                    self.assertEqual(texts, [])

    def test_switch_label(self):
        def switches(console: str) -> dict:
            project = copy.deepcopy(SWITCH_PROJECT)
            project["project"]["board"] = "../../boards/testboard"
            project["modules"]["LAMP_SW"]["console"] = console
            return project

        long = "lamp_front_right"                                            # 16 characters
        self.assertEqual(len(long), self.LIMIT)
        # the real manifest bounds the console group by its pattern already: a longer one stops there
        _, messages = nxconfig.check(self.write(SWITCH_BOARD, switches(long + "x")), TEST_MCUDB)
        self.assertEqual(findings(messages), [("error", "modules.LAMP_SW.console")])
        self.assertIn("does not match", messages[0].text)
        # a manifest that allows longer groups: the label (group in upper case) is checked against the library
        manifest = (REPO / "libs/bsw/highSideSwitch/module.yaml").read_text(encoding="utf-8")
        self.assertIn("{0,15}", manifest)
        base = self.tmp / "modules" / "highSideSwitch"
        base.mkdir(parents=True)
        (base / "module.yaml").write_text(manifest.replace("{0,15}", "{0,31}"), encoding="utf-8")
        # ARCH-66: the manifest names its binding trait, which nxconfig looks up in the module's include directory
        shutil.copytree(REPO / "libs/bsw/highSideSwitch/include", base / "include")
        dirs = (self.tmp / "modules",)
        self.assertEqual(self.name_errors(SWITCH_BOARD, switches(long), dirs, TEST_MCUDB), [])
        self.assert_finding(self.name_errors(SWITCH_BOARD, switches(long + "x"), dirs, TEST_MCUDB), "project",
                            "modules.LAMP_SW.console",
                            f"label {long.upper()}X of LAMP_SW (highSideSwitch, console group {long}x in upper "
                            "case) has 17 characters, the code takes at most 16",
                            "HighSideSwitchNames::LABEL_MAX_CHARS in libs/bsw/highSideSwitch/include/highSideSwitch/"
                            "HighSideSwitchNames.h:", f"console: {long}x")

    def test_real_projects_have_no_findings(self):
        for project in sorted((REPO / "projects").glob("*/project.yaml")):
            with self.subTest(project=project.parent.name):
                config, messages = nxconfig.check(project)
                self.assertIsNotNone(config, [str(m) for m in messages])
                self.assertEqual([str(m) for m in messages if "the code takes at most" in m.text], [])


# IO-31: own profile of the generator tests (tools/bswgen/test/profiles, S32K144, 8 MHz crystal, 64 MHz, half bridge
# BRIDGE on FTM1 at 15 kHz = 4267 ticks = 66671.9 ns, DC link shunt DC_LINK_I, unfitted SPARE_I)
SAMPLING_BOARD = yaml.safe_load(
    (REPO / "tools/bswgen/test/profiles/boards/testboard_adc_sampling/board.yaml").read_text(encoding="utf-8"))
SAMPLING_PROJECT = yaml.safe_load(
    (REPO / "tools/bswgen/test/profiles/projects/testproject_adc_sampling/project.yaml").read_text(encoding="utf-8"))


class AdcSamplingTest(Profiles):
    """IO-31: project adc_sampling.<channel> - averaging over one realized PWM period, messages with YAML path."""

    def write(self, board=None, project=None) -> Path:
        project = copy.deepcopy(SAMPLING_PROJECT if project is None else project)
        project["project"]["board"] = "../../boards/testboard"
        return super().write(SAMPLING_BOARD if board is None else board, project)

    def sampled(self, **entries):
        project = copy.deepcopy(SAMPLING_PROJECT)
        project["adc_sampling"] = {name: {"kind": "pwm_period_average", **values} for name, values in entries.items()}
        return project

    def messages(self, project):
        return nxconfig.check(self.write(project=project), nxconfig.MCUDB)

    def test_accepted_and_default(self):
        config, messages = self.messages(self.sampled(DC_LINK_I={"pwm_group": "BRIDGE", "samples": 8}))
        self.assertEqual(findings(messages), [])
        self.assertEqual(config.project.adc_sampling["DC_LINK_I"].samples, 8)
        config, messages = self.messages(SAMPLING_PROJECT)
        self.assertEqual(config.project.adc_sampling, {})          # left out: one conversion per read
        config, _ = nxconfig.check(DEMO_PROJECT)
        self.assertEqual(sorted(config.project.adc_sampling),   # IO-31: the demo averages the motor currents
                         ["ADC_CURRENT_U", "ADC_CURRENT_V", "ADC_CURRENT_W", "SENSE_TSCURRENT"])

    def test_messages_with_yaml_path(self):
        cases = [
            ({"NOPE": {"pwm_group": "BRIDGE", "samples": 8}}, "adc_sampling.NOPE",
             "adc_sampling NOPE: no board ADC channel the project uses (use.adc)"),
            ({"SPARE_I": {"pwm_group": "BRIDGE", "samples": 8}}, "adc_sampling.SPARE_I",
             "adc_sampling SPARE_I: the circuit of SPARE_I is not fitted on board testboard_adc_sampling "
             "(adc[].fitted: false)"),
            ({"DC_LINK_I": {"pwm_group": "FAN", "samples": 8}}, "adc_sampling.DC_LINK_I.pwm_group",
             "adc_sampling DC_LINK_I: PWM group FAN is not used by the project (pwm: BRIDGE), there is no period "
             "to average over"),
            # 66671.9 ns / 1741 ns (64 MHz profile: 48 MHz ADC clock, 32 MHz bus) -> at most 38
            ({"DC_LINK_I": {"pwm_group": "BRIDGE", "samples": 39}}, "adc_sampling.DC_LINK_I.samples",
             "adc_sampling DC_LINK_I: 39 conversions of 1741 ns do not fit into one period of BRIDGE (66672 ns at "
             "14998.828 Hz, at most 38)"),
        ]
        for entries, path, text in cases:
            with self.subTest(path=path):
                _, messages = self.messages(self.sampled(**entries))
                errors = [m for m in messages if m.severity == "error"]
                self.assertEqual([(m.yaml_path, m.text) for m in errors], [(path, text)])
                self.assertEqual(errors[0].file.name, "project.yaml")
                self.assertIsInstance(errors[0].line, int)

    def test_internal_channel_and_unused_group(self):
        project = self.sampled(BANDGAP={"pwm_group": "BRIDGE", "samples": 8})
        project["adc_internal"] = [{"name": "BANDGAP", "instance": "ADC0", "channel": "BANDGAP"}]
        _, messages = self.messages(project)
        self.assertIn("an internal channel", [m.text for m in messages if m.severity == "error"][0])
        project = self.sampled(DC_LINK_I={"pwm_group": "BRIDGE", "samples": 8})
        project.pop("pwm")                                          # the board has the bridge, the project not
        _, messages = self.messages(project)
        self.assertEqual(findings([m for m in messages if m.severity == "error"]),
                         [("error", "adc_sampling.DC_LINK_I.pwm_group")])

    def test_tight_spacing_is_a_warning(self):
        _, messages = self.messages(self.sampled(DC_LINK_I={"pwm_group": "BRIDGE", "samples": 30}))
        self.assertEqual(findings(messages), [("warning", "adc_sampling.DC_LINK_I.samples")])
        self.assertIn("at most 25 samples keep half a conversion free", messages[0].text)
        _, messages = self.messages(self.sampled(DC_LINK_I={"pwm_group": "BRIDGE", "samples": 25}))
        self.assertEqual(findings(messages), [])

    def test_schema(self):
        for values in ({"pwm_group": "BRIDGE", "samples": 1}, {"pwm_group": "BRIDGE", "samples": 65},
                       {"pwm_group": "BRIDGE", "samples": 8, "kind": "pdb_trigger"}, {"pwm_group": "BRIDGE"},
                       {"pwm_group": "BRIDGE", "samples": 8, "window_us": 50}):
            with self.subTest(values=values):
                project = self.sampled(DC_LINK_I={"pwm_group": "BRIDGE", "samples": 8})
                project["adc_sampling"]["DC_LINK_I"] = {"kind": "pwm_period_average", **values}
                _, messages = self.messages(project)
                self.assertTrue([m for m in messages if m.severity == "error"], values)


class SafetyRoleTest(Profiles):
    """TOOL-110: the pins of `safety` against what they do at run time (checks._safety_roles).

    safe_state_output is the indicator the safe state disables and re-muxes, locked_pin the pin whose
    configuration is locked, test_pin an unconnected pin configured as input with pull-down. Findings at
    safety.<key> of project.yaml; nxconfig must say before bswgen what bswgen would refuse."""

    def roles(self, board=None, project=None, mcudb=TEST_MCUDB) -> list[tuple[str, str, str]]:
        _, messages = self.check(board, project, mcudb)
        return [(m.severity, m.yaml_path, m.text) for m in messages if m.yaml_path.startswith("safety")]

    def test_the_synthetic_project_is_plausible(self):
        self.assertEqual(self.roles(), [])

    def test_indicator_and_locked_pin_are_two_outputs(self):
        project = self.project()
        project["safety"]["locked_pin"] = project["safety"]["safe_state_output"]
        (severity, path, text), = self.roles(project=project)
        self.assertEqual((severity, path), ("error", "safety.locked_pin"))
        self.assertIn("are both LED_RED", text)

    def test_test_pin_must_not_be_wired_on_the_board(self):
        """PA10 carries the input capture SPEED of the board, which the project does not use: no resource conflict,
        but not an unconnected pin either - the pull-down would load the net (F6: test_pin PTA6 was the ADC pin
        of the board, lost by F1)."""
        project = self.project()
        self.assertNotIn("SPEED", str(project["use"].get("input_capture")))
        project["safety"]["test_pin"] = "PA10"
        (severity, path, text), = self.roles(project=project)
        self.assertEqual((severity, path), ("error", "safety.test_pin"))
        self.assertIn("wired on board testboard (input_capture SPEED)", text)
        self.assertIn("unconnected", text)

    def with_rte(self, connections: str, **safety) -> list[tuple[str, str, str, int | None]]:
        """The synthetic project (indicator LED_RED, locked pin LED_GREEN) with an rte.yaml of `connections` and
        the keys `safety` added to its safety section: the messages at safety.* (severity, path, text, line)."""
        project = self.project()
        project["project"]["rte"] = "rte.yaml"
        project["safety"].update(safety)
        path = self.write(project=project)
        (path.parent / "rte.yaml").write_text(
            "schema_version: 2\ncomponents: []\n" + (f"connections:\n{connections}" if connections else ""),
            encoding="utf-8")
        _, messages = nxconfig.check(path, TEST_MCUDB)
        found = [(m.severity, m.yaml_path, m.text, m.line) for m in messages if m.yaml_path.startswith("safety")]
        for m in messages:
            if m.yaml_path.startswith("safety"):
                self.assertEqual(m.file.name, "project.yaml")
        return found

    def test_the_application_may_write_the_locked_pin(self):
        """The PCR lock freezes the configuration, not the level: an RTE connection to locked_pin is no finding."""
        self.assertEqual(self.with_rte("  Demo.Led1.Led1: dio.out.LED_GREEN\n"), [])

    def test_application_writes_the_indicator_warns(self):
        """SYS-87 (user decision 2026-09-25, variant A): a port that writes the indicator is a warning at
        safety.safe_state_output with the consequence - it floats in the safe state and the old level comes back
        when the safe state is left. A list of targets counts as well (the demo writes LED1 and a CAN signal)."""
        for connections in ("  Demo.Led2.Led2: dio.out.LED_RED\n",
                            "  Demo.Led2.Led2: [can.tx.Status.Led2, dio.out.LED_RED]\n"):
            (severity, path, text, line), = self.with_rte(connections)
            self.assertEqual((severity, path), ("warning", "safety.safe_state_output"))
            self.assertIsNotNone(line)
            self.assertIn("rte.yaml Demo.Led2.Led2 -> dio.out.LED_RED", text)
            self.assertIn("floats", text)
            self.assertIn("level the application wrote last", text)
            self.assertIn("AoU-30", text)
            self.assertIn("safety.safe_state_output_shared_with_application", text)

    def test_the_confirmation_silences_the_warning(self):
        self.assertEqual(self.with_rte("  Demo.Led2.Led2: dio.out.LED_RED\n",
                                       safe_state_output_shared_with_application="LED_RED is a status LED only"),
                         [])

    def test_a_confirmation_without_the_case_is_superfluous(self):
        """Without a port writing the indicator the key would only wait to silence a later, real finding."""
        for connections in ("  Demo.Led1.Led1: dio.out.LED_GREEN\n", ""):
            (severity, path, text, line), = self.with_rte(
                connections, safe_state_output_shared_with_application="LED_RED is a status LED only")
            self.assertEqual((severity, path), ("warning", "safety.safe_state_output_shared_with_application"))
            self.assertIsNotNone(line)
            self.assertIn("superfluous", text)
            self.assertIn("does not write LED_RED", text)

    def test_the_confirmation_needs_a_reason(self):
        """A text, not true: the confirmation says why (model.Safety)."""
        for value, expected in ((True, "reason as text"), ("  ", "must not be empty")):
            messages = self.with_rte("  Demo.Led2.Led2: dio.out.LED_RED\n",
                                     safe_state_output_shared_with_application=value)
            errors = [(p, t) for s, p, t, _ in messages if s == "error"]
            self.assertEqual([p for p, _ in errors], ["safety.safe_state_output_shared_with_application"])
            self.assertIn(expected, errors[0][1])

    def switches(self, **safety) -> list[tuple[str, str, str]]:
        """The switches profile (two high-side switches LAMP_SW/PUMP_SW, highSideSwitch: `in` is safe_off, `den`
        is not) with a safety section - every output of that board belongs to a driver."""
        project = copy.deepcopy(SWITCH_PROJECT)
        project["project"]["board"] = "../../boards/testboard"
        project["safety"] = safety
        return self.roles(copy.deepcopy(SWITCH_BOARD), project)

    def test_indicator_is_no_output_a_driver_owns(self):
        """F6 with drivers: PUMP_IN is the safe_off input of the pump switch - bswgen stops with 'is switched by
        the safe state ... but it is locked', nxconfig says it first at the YAML path. PUMP_DEN (diagnosis
        enable, no safe_off) belongs to the driver as well: the indicator would disable and re-mux it behind it."""
        (severity, path, text), = self.switches(safe_state_output="PUMP_IN", locked_pin="LAMP_DEN", test_pin="PC14")
        self.assertEqual((severity, path), ("error", "safety.safe_state_output"))
        self.assertIn("PUMP_IN is the safe_off output connect.in of device PUMP_SW (module highSideSwitch)", text)
        (severity, path, text), = self.switches(safe_state_output="PUMP_DEN", locked_pin="LAMP_DEN", test_pin="PC14")
        self.assertEqual((severity, path), ("error", "safety.safe_state_output"))
        self.assertIn("connect.den of device PUMP_SW", text)
        self.assertIn("driver mechanisms stay separate", text)

    def test_locked_pin_may_be_a_driver_output(self):
        """The lock freezes the configuration only, and a safe_off output is in the lock list anyway: nothing at
        safety.locked_pin (the finding left is the indicator LAMP_DEN, which the lamp switch owns)."""
        roles = self.switches(safe_state_output="LAMP_DEN", locked_pin="PUMP_IN", test_pin="PC14")
        self.assertEqual([(sev, path) for sev, path, _ in roles], [("error", "safety.safe_state_output")])


MINIMAL_PROJECT = REPO / "projects" / "s32k116evb_minimal" / "project.yaml"
MINIMAL_BOARD = REPO / "boards" / "s32k116evb"


class RteRequiredTest(unittest.TestCase):
    """TOOL-114 (user decision 2026-09-25, variant A): the RTE is required.

    The root CMakeLists.txt runs rtegen at every configure and aborts without project.rte; nxconfig says so first,
    with file and YAML path, for every project that is built (checks.rte_required, marker built_with_family_port).
    The profile is projects/s32k116evb_minimal in a temporary folder, its board the real board of the tree.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def project(self, rte: str | None = None, board: Path = MINIMAL_BOARD) -> Path:
        text = MINIMAL_PROJECT.read_text(encoding="utf-8")
        self.assertIn("  board: ../../boards/s32k116evb\n", text)
        self.assertIn("  rte: rte.yaml\n", text)
        text = text.replace("  board: ../../boards/s32k116evb\n", f"  board: {board.as_posix()}\n")
        text = text.replace("  rte: rte.yaml\n", "" if rte is None else f"  rte: {rte}\n")
        folder = self.tmp / "projects" / "minimal"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "project.yaml").write_text(text, encoding="utf-8")
        return folder / "project.yaml"

    @staticmethod
    def errors(messages):
        return [(m.file.name, m.yaml_path) for m in messages if m.severity == "error"]

    def test_a_built_project_without_rte_is_refused_with_file_and_path(self):
        file = self.project()
        config, messages = nxconfig.check(file)
        self.assertIsNone(config)
        self.assertEqual(self.errors(messages), [("project.yaml", "project.rte")], [str(m) for m in messages])
        text = next(m.text for m in messages if m.severity == "error")
        self.assertIn("RTE is required", text)
        self.assertIn("add `rte: rte.yaml`", text)             # what to do
        self.assertIn("nxconfig new", text)                     # or the wizard
        self.assertIsNotNone(next(m for m in messages if m.severity == "error").line)
        # the command line says the same (exit code 1, file:line (path))
        run = subprocess.run([sys.executable, str(HERE), "check", str(file)], capture_output=True, text=True,
                             encoding="utf-8")
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertRegex(run.stdout + run.stderr, r"project\.yaml:\d+ \(project\.rte\): error: project\.rte")

    def test_the_hint_of_the_message_is_enough(self):
        """The rte.yaml the message describes - one task, no component - passes nxconfig and rtegen."""
        _, messages = nxconfig.check(self.project())
        text = next(m.text for m in messages if m.severity == "error")
        tasks = re.search(r"tasks: (\[\{.*?\}\])", text).group(1)
        (self.tmp / "projects" / "minimal" / "rte.yaml").write_text(
            f"schema_version: 2\ntasks: {tasks}\ncomponents: []\n", encoding="utf-8")
        file = self.project(rte="rte.yaml")
        config, messages = nxconfig.check(file)
        self.assertIsNotNone(config, [str(m) for m in messages])
        self.assertEqual(rtegen_errors(file), [])

    def test_a_configuration_only_profile_is_not_refused_by_nxconfig_but_by_rtegen(self):
        """The marker of PLAT-95: a board outside every boards/ folder is never built, nxconfig leaves it alone.
        Not silently: rtegen.validate() - what the Studio step 'Konfiguration pruefen' asks - refuses it."""
        board = self.tmp / "testboards" / "s32k116evb"
        shutil.copytree(MINIMAL_BOARD, board)
        file = self.project(board=board)
        config, messages = nxconfig.check(file)
        self.assertIsNotNone(config, [str(m) for m in messages])
        self.assertFalse(checks.built_with_family_port(config))
        self.assertEqual(rtegen_errors(file), [("project.yaml", "project.rte")])

    def test_every_project_of_the_tree_has_an_ecu_description(self):
        projects = sorted((REPO / "projects").glob("*/project.yaml"))
        self.assertTrue(projects)
        for file in projects:
            with self.subTest(project=file.parent.name):
                rte = (yaml.safe_load(file.read_text(encoding="utf-8"))["project"] or {}).get("rte")
                self.assertTrue(rte, f"{file}: project.rte is not set")
                self.assertTrue((file.parent / rte).is_file(), f"{file}: {rte} does not exist")


def rtegen_errors(project: Path) -> list[tuple[str, str]]:
    """(file, YAML path) of the errors of `rtegen --validate` - rtegen.validate() on the command line, in a process
    of its own (tools/rtegen imports the module bswgen.py, which the package tools/bswgen shadows here)."""
    run = subprocess.run([sys.executable, str(HERE.parent / "rtegen" / "rtegen.py"), str(project), "--validate"],
                         capture_output=True, text=True, encoding="utf-8")
    found = re.findall(r"^rtegen: ([^:\s]+):?\d* \(([^)]*)\): error:", run.stdout + run.stderr, re.M)
    if run.returncode != 0 and not found:
        raise AssertionError(run.stdout + run.stderr)
    return found



class CanBusOffTest(Profiles):
    """COM-37: project values per CAN bus after bus off (can.<bus>.bus_off, libs/bsw/canSystem CanBusOffRecovery).

    Own profile testproject_s32k146_can (S32K146, two used buses POWERTRAIN and BODY, no SBC), not the eval board:
    each bus gets its own values; values that do not fit together are reported at their YAML path with a line.
    """

    def write(self, board=None, project=None) -> Path:
        project = copy.deepcopy(S32K146_CAN_PROJECT if project is None else project)
        project["project"]["board"] = "../../boards/testboard"
        return super().write(copy.deepcopy(S32K146_CAN_BOARD) if board is None else board, project)

    def check(self, board=None, project=None, mcudb=nxconfig.MCUDB):
        return nxconfig.check(self.write(board, project), mcudb)

    @staticmethod
    def with_bus_off(**buses):
        project = copy.deepcopy(S32K146_CAN_PROJECT)
        for bus, values in buses.items():
            project["can"][bus]["bus_off"] = values
        return project

    def test_left_out_means_the_defaults(self):
        config, messages = self.check()
        self.assertEqual(messages, [])
        self.assertIsNone(config.project.can["POWERTRAIN"].bus_off)
        defaults = nxconfig.model.CanBusOff()
        self.assertEqual((defaults.first_pause_ms, defaults.max_pause_ms, defaults.discard_after, defaults.quiet_ms,
                          defaults.summary_ms), (10, 1000, 3, 2000, 10000))

    def test_own_values_per_bus(self):
        config, messages = self.check(project=self.with_bus_off(
            POWERTRAIN={"max_pause_ms": 0, "discard_after": 0},
            BODY={"first_pause_ms": 20, "max_pause_ms": 200, "discard_after": 1, "quiet_ms": 500,
                  "summary_ms": 1000, "note": "own profile"}))
        self.assertEqual(messages, [])
        body, powertrain = config.project.can["BODY"].bus_off, config.project.can["POWERTRAIN"].bus_off
        self.assertEqual((body.first_pause_ms, body.max_pause_ms, body.discard_after, body.quiet_ms,
                          body.summary_ms), (20, 200, 1, 500, 1000))
        self.assertEqual((powertrain.max_pause_ms, powertrain.discard_after, powertrain.first_pause_ms), (0, 0, 10))

    def test_values_that_do_not_fit_together(self):
        cases = [
            ({"first_pause_ms": 0}, "error", "first_pause_ms", "a pause of 0 ms never doubles"),
            ({"first_pause_ms": 500, "max_pause_ms": 200}, "error", "first_pause_ms",
             "first_pause_ms 500 is above max_pause_ms 200"),
            ({"max_pause_ms": 0, "first_pause_ms": 20}, "warning", "first_pause_ms", "has no effect with max_pause_ms 0"),
            ({"max_pause_ms": 3000}, "warning", "quiet_ms", "quiet_ms 2000 is not longer than max_pause_ms 3000"),
            ({"max_pause_ms": 2000}, "warning", "quiet_ms", "quiet_ms 2000 is not longer than max_pause_ms 2000"),
        ]
        for values, severity, key, fragment in cases:
            with self.subTest(values=values):
                _, messages = self.check(project=self.with_bus_off(BODY=values))
                self.assertEqual(findings(messages), [(severity, f"can.BODY.bus_off.{key}")])
                self.assertIn(fragment, messages[0].text)
                self.assertTrue(messages[0].text.startswith("CAN bus BODY: "))
                self.assertEqual(messages[0].file.name, "project.yaml")
                self.assertIsInstance(messages[0].line, int)

    def test_automatic_recovery_without_first_pause_is_quiet(self):
        _, messages = self.check(project=self.with_bus_off(BODY={"max_pause_ms": 0}))
        self.assertEqual(messages, [])

    def test_schema_bounds_are_the_c_types(self):
        cases = [
            ({"max_pause_ms": 65536}, "less than or equal to 65535", "max_pause_ms"),
            ({"discard_after": 256}, "less than or equal to 255", "discard_after"),
            ({"quiet_ms": 0}, "greater than or equal to 1", "quiet_ms"),
            ({"summary_ms": 0}, "greater than or equal to 1", "summary_ms"),
            ({"first_pause_ms": -1}, "greater than or equal to 0", "first_pause_ms"),
            ({"pause_ms": 10}, "Extra inputs are not permitted", "pause_ms"),
            ({"quiet_ms": "2 s"}, "valid integer", "quiet_ms"),
        ]
        for values, fragment, key in cases:
            with self.subTest(values=values):
                self.expect(fragment, project=self.with_bus_off(BODY=values), level="project",
                            path=f"can.BODY.bus_off.{key}")

    def test_problems_function_matches_the_messages(self):
        bus_off = nxconfig.model.CanBusOff(first_pause_ms=0)
        self.assertEqual([(sev, key) for sev, key, _ in checks.can_bus_off_problems(bus_off)],
                         [("error", "first_pause_ms")])
        self.assertEqual(checks.can_bus_off_problems(nxconfig.model.CanBusOff()), [])


class LicenceManifestTest(Profiles):
    """PLAT-49: licence, distribution class and provenance in module.yaml (doc/architecture/lizenz-und-release.md
    section 5.2), checked with file, line and YAML path. Own profile: testboard_switches/testproject_switches with
    manifests written by the tests next to the repository's libs; plus the REUSE files of the tree."""

    module_dir = ManifestCatalogueTest.module_dir
    HEAD = "module: {name}\nkind: service\n"

    def write(self, board=None, project=None) -> Path:
        project = copy.deepcopy(SWITCH_PROJECT)
        project["project"]["board"] = "../../boards/testboard"          # directory written by Profiles.write
        return super().write(SWITCH_BOARD, project)

    def licence_messages(self, name: str, body: str) -> tuple[dict, list]:
        base = self.module_dir(**{name: self.HEAD.format(name=name) + body})
        modules = nxconfig_config.load_modules((base,))
        valid, messages = checks._manifests(modules)
        return valid, list(modules.messages) + messages

    def test_spdx_expression_syntax(self):
        from nxconfig.model import spdx_identifiers
        self.assertEqual(spdx_identifiers("Apache-2.0"), ["Apache-2.0"])
        self.assertEqual(spdx_identifiers("(MIT OR Apache-2.0) AND LicenseRef-neXtBSW-NDA"),
                         ["MIT", "Apache-2.0", "LicenseRef-neXtBSW-NDA"])
        self.assertEqual(spdx_identifiers("GPL-2.0-or-later WITH Classpath-exception-2.0"), ["GPL-2.0-or-later"])
        for bad in ("Apache 2.0", "MIT AND", "(MIT", "MIT)", "AND MIT", "MIT OR OR BSD-3-Clause", "Apache-2.0;"):
            with self.subTest(expression=bad):
                with self.assertRaises(ValueError):
                    spdx_identifiers(bad)

    def test_valid_combinations(self):
        cases = {
            "openDefault": "",                                            # left out: distribution.yaml decides
            "openStated": "license: Apache-2.0\ndistribution: open\nprovenance:\n"
                          "  - { doc: 'TMC6200 data sheet Rev. 1.08', access: public }\n",
            "ndaDriver": "license: LicenseRef-neXtBSW-NDA\ndistribution: nda\nprovenance:\n"
                         "  - { doc: 'Part data sheet Rev. 6.0', access: nda }\n"
                         "  - { doc: 'Programming guide Rev. 1.2', access: public }\n",
            "licensedDriver": "license: LicenseRef-neXtBSW-Commercial\ndistribution: licensed\nprovenance:\n"
                              "  - { doc: 'Customer specification 4711', access: customer }\n",
            "higherThanNeeded": "license: LicenseRef-neXtBSW-NDA\ndistribution: private\nprovenance:\n"
                                "  - { doc: 'Own measurements', access: own }\n",
            "privateApache": "license: Apache-2.0\ndistribution: private\n",
        }
        for name, body in cases.items():
            with self.subTest(module=name):
                valid, messages = self.licence_messages(name, body)
                self.assertEqual([str(m) for m in messages], [])
                self.assertIn(name, valid)
                shutil.rmtree(self.tmp / "catalogue" / name)
        valid, _ = self.licence_messages("ndaDriver2", cases["ndaDriver"])
        m = valid["ndaDriver2"]
        self.assertEqual((m.license, m.distribution, [p.access for p in m.provenance]),
                         ("LicenseRef-neXtBSW-NDA", "nda", ["nda", "public"]))

    def test_invalid_combinations_with_yaml_path(self):
        cases = [
            # (name, body, YAML path, fragment)
            ("badClass", "distribution: public\n", "distribution", "'open', 'licensed', 'nda', 'private' or"),
            ("badSpdx", "license: Apache 2.0\n", "license", "not an SPDX licence expression"),
            ("badAccess", "distribution: open\nprovenance:\n  - { doc: X, access: secret }\n", "provenance[0].access",
             "'own', 'public', 'customer' or 'nda'"),
            ("noDoc", "distribution: open\nprovenance:\n  - { access: public }\n", "provenance[0].doc",
             "Field required"),
            ("ndaAsOpen", "license: Apache-2.0\ndistribution: open\nprovenance:\n"
                          "  - { doc: 'Part data sheet Rev. 6.0', access: nda }\n", "distribution",
             "lower than provenance[0] needs ('Part data sheet Rev. 6.0', access nda: at least nda)"),
            ("customerAsOpen", "license: Apache-2.0\ndistribution: open\nprovenance:\n"
                               "  - { doc: 'Spec', access: customer }\n", "distribution", "at least licensed"),
            ("provenanceAlone", "provenance:\n  - { doc: 'Part data sheet Rev. 6.0', access: nda }\n", "provenance",
             "provenance needs distribution: state the release class the sources lead to (at least nda)"),
            ("refInOpen", "license: LicenseRef-neXtBSW-NDA\ndistribution: open\n", "license",
             "licence LicenseRef-neXtBSW-NDA needs distribution nda or higher (is open)"),
            ("refUnstated", "license: Apache-2.0 AND LicenseRef-neXtBSW-NDA\n", "license", "(is not stated)"),
            ("commercialAsNda", "license: LicenseRef-neXtBSW-NDA\ndistribution: licensed\n", "license",
             "needs distribution nda or higher (is licensed)"),
            ("unknownRef", "license: LicenseRef-Foo\ndistribution: private\n", "license", "unknown licence "
             "LicenseRef-Foo"),
            ("ndaApache", "license: Apache-2.0\ndistribution: nda\n", "license",
             "would let every recipient pass the files on"),
            ("ndaNoLicence", "distribution: nda\n", "distribution", "distribution nda needs license"),
            ("workstation", "license: LicenseRef-neXtBSW-Proprietary\ndistribution: workstation\n", "distribution",
             "never in a repository"),
        ]
        for name, body, path, fragment in cases:
            with self.subTest(module=name):
                valid, messages = self.licence_messages(name, body)
                self.assertNotIn(name, valid)
                errors = [m for m in messages if m.severity == "error"]
                match = [m for m in errors if m.yaml_path == path]
                self.assertTrue(match, [f"{m.yaml_path}: {m}" for m in errors])
                self.assertIn(fragment, match[0].text)
                self.assertEqual(match[0].file, self.tmp / "catalogue" / name / "module.yaml")
                lines = (self.HEAD.format(name=name) + body).splitlines()
                if "[" in path:                 # the entry of the list: line after the key provenance
                    expected = lines.index("provenance:") + 2
                else:
                    expected = lines.index(next(x for x in lines if x.startswith(path + ":"))) + 1
                self.assertEqual(match[0].line, expected)
                shutil.rmtree(self.tmp / "catalogue" / name)

    def test_unverified_access_is_a_warning(self):
        body = ("license: LicenseRef-neXtBSW-NDA\ndistribution: nda\nprovenance:\n"
                "  - { doc: 'Part data sheet Rev. 6.0', access: nda }\n"
                "  - { doc: 'Application note Rev. 2.1', access: public, verify: true }\n")
        valid, messages = self.licence_messages("unverified", body)
        self.assertIn("unverified", valid)                 # a warning does not take the manifest out
        self.assertEqual([(m.severity, m.yaml_path, m.line) for m in messages],
                         [("warning", "provenance[1].verify", 7)])
        self.assertIn("access of 'Application note Rev. 2.1' is not established yet", messages[0].text)

    def test_reported_by_project_check(self):
        # the manifest error stops nxconfig check of a project that sees the module folder, with file and line
        base = self.module_dir(ndaAsOpen=self.HEAD.format(name="ndaAsOpen")
                               + "distribution: open\nprovenance:\n  - { doc: 'Data sheet', access: nda }\n")
        _, messages = nxconfig.check(self.write(), TEST_MCUDB, (*nxconfig_config.MODULE_DIRS, base))
        self.assertIn((base / "ndaAsOpen" / "module.yaml", "distribution", 3),
                      [(m.file, m.yaml_path, m.line) for m in messages if m.severity == "error"])

    def test_repository_manifests(self):
        # every manifest of the tree passes the licence check; the NDA driver states its class in the parked file
        modules = nxconfig_config.load_modules()
        _, messages = checks._manifests(modules)
        self.assertEqual([str(m) for m in messages if m.yaml_path.split(".")[0].split("[")[0]
                          in ("license", "distribution", "provenance")], [])
        pending = REPO / "libs/bsw/bccMc33771/module.yaml.pending"
        if pending.is_file():               # PLAT-62: the NDA driver is not in the exported open tree
            data = yaml.safe_load(pending.read_text(encoding="utf-8"))
            keys = ("module", "kind", "license", "distribution", "provenance")
            m = Manifest.model_validate({k: data[k] for k in keys})
            self.assertEqual((m.license, m.distribution), ("LicenseRef-neXtBSW-NDA", "nda"))
            notes = []
            checks._licence_problems(m, lambda p, t: notes.append(("error", p, t)),
                                     lambda p, t: notes.append(("warning", p, t)))
            self.assertEqual([n[0] for n in notes], ["warning"])     # access to the programming guide still to be confirmed
            # every source file of the module carries the NDA marking in its own header
            for path in sorted((pending.parent).rglob("*")):
                if path.suffix in (".h", ".cpp") or path.name == "CMakeLists.txt":
                    with self.subTest(file=path.name):
                        self.assertIn("SPDX-License-Identifier: LicenseRef-neXtBSW-NDA",
                                      path.read_text(encoding="utf-8")[:300])

    def test_reuse_files_of_the_tree(self):
        # REUSE.toml names only licences whose text is under LICENSES/, every LicenseRef of the model has a text,
        # and the copyright holder is the one decided by the user (F13)
        reuse = REPO / "REUSE.toml"
        if not reuse.is_file():             # an exported tree without the REUSE files
            self.skipTest("no REUSE.toml in this tree")
        import tomllib
        from nxconfig.model import LICENSE_REF_CLASSES
        data = tomllib.loads(reuse.read_text(encoding="utf-8"))
        self.assertEqual(data["version"], 1)
        texts = {p.stem for p in (REPO / "LICENSES").glob("*.txt")}
        used = set()
        for annotation in data["annotations"]:
            self.assertIn(annotation.get("precedence", "closest"), ("closest", "aggregate", "override"))
            used.update(spdx_ids(annotation["SPDX-License-Identifier"]))
        self.assertEqual(sorted(used - texts), [])
        self.assertEqual(data["annotations"][0]["path"], "**")
        self.assertEqual(data["annotations"][0]["SPDX-License-Identifier"], "Apache-2.0")
        self.assertEqual(data["annotations"][0]["SPDX-FileCopyrightText"], "2026 Daniel Simon Reinhardt-Probul")
        present = [ref for ref in LICENSE_REF_CLASSES if ref in texts]
        # PLAT-114: the exported open tree carries neither the NDA module nor the Studio, and the export drops their
        # annotations and licence texts (tools/distcheck, REUSE.toml mode reuse_toml) - there they are absent
        if (REPO / "libs" / "bsw" / "bccMc33771").is_dir():
            self.assertIn("LicenseRef-neXtBSW-NDA", present)
        if (REPO / "tools" / "studio").is_dir():
            self.assertIn("LicenseRef-neXtBSW-Proprietary", present)


def spdx_ids(expression: str) -> list[str]:
    from nxconfig.model import spdx_identifiers
    return spdx_identifiers(expression)


if __name__ == "__main__":
    unittest.main()
