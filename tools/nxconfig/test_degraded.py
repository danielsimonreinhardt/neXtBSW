"""SYS-117: tools/nxconfig/degraded.py - safe-output groups and degraded mappings (doc/safety/partner-and-degraded.md
part B): the closed list read from the supervisor's code, every refusal with its YAML path, the derived values and
their agreement with tools/bswgen (table order, generated header) and tools/rtegen (bus ids); the demo and every other
project without a mapping get no derived value more.

Run: .venv/Scripts/python -m unittest tools/nxconfig/test_degraded.py
"""

import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(REPO / "tools" / "bswgen"))
sys.path.insert(0, str(REPO / "tools" / "rtegen"))

import nxconfig  # noqa: E402
from nxconfig import degraded  # noqa: E402
from nxconfig.checks import DeviceUse, ModuleUse  # noqa: E402
from nxconfig.config import Reporter  # noqa: E402
from nxconfig.model import Safety  # noqa: E402

# PLAT-62: the own full-featured test profile (synthetic testboard_full/testproject_full) instead of the eval board
# project, which stays private (PLAT-46 F10) - a test against it would be red in the exported open tree. What
# proves something about the eval board itself uses EVAL_DEMO_PROJECT and is skipped where it is missing.
DEMO_PROJECT = REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full" / "project.yaml"
EVAL_DEMO_PROJECT = REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml"
DEVICE_FAULT = 0x13


def findings(messages, severity=None):
    return [(m.severity, m.yaml_path, m.text) for m in messages if severity is None or m.severity == severity]


class FactsTest(unittest.TestCase):
    """The closed list and the event numbers are read from the code, not written down twice."""

    def test_closed_list_and_numbers(self):
        facts = degraded.read_facts(REPO)
        self.assertEqual(facts.degradable, ("DEVICE_FAULT",))
        self.assertEqual(facts.events["DEVICE_FAULT"], DEVICE_FAULT)
        self.assertEqual(facts.events["SAFETY_MANAGER_SEQUENCE_DEVIATION"], 0)
        self.assertEqual(facts.events["ETL_ASSERT"], len(facts.events) - 1)
        self.assertEqual((facts.max_groups, facts.max_mappings), (8, 16))
        # every MCU-internal fault of the concept's "fail-safe only" rows is known and not degradable
        for name in ("ROM_CHECK_FAILURE", "INTEGRITY_EVENT_DMA_ECC_ERROR", "MPU_CONFIGURATION_ERROR",
                     "WATCHDOG_STARTUP_CHECK_FAILURE", "SAFE_WATCHDOG_SERVICE_DEVIATION", "STACK_OVERFLOW",
                     "INTERNAL_SUPPLIES_ERROR", "ADC_REFERENCE_ERROR", "SAFETY_CYCLE_PERIOD_VIOLATION",
                     "CHECK_REGISTER_ERROR", "LOCK_REGISTER_ERROR", "RESET_LOOP_LIMIT", "RTE_TIMING_VIOLATION"):
            self.assertIn(name, facts.events)
            self.assertNotIn(name, facts.degradable)

    def test_unreadable_code_is_a_problem(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            for rel in (degraded.DEGRADED_HEADER, degraded.SUPERVISOR_HEADER):
                (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(REPO / rel, tmp / rel)
            text = (tmp / degraded.DEGRADED_HEADER).read_text(encoding="utf-8")
            (tmp / degraded.DEGRADED_HEADER).write_text(
                text.replace("X(DEVICE_FAULT)", "X(DEVICE_FAULT) X(NO_SUCH_EVENT)"), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "NO_SUCH_EVENT"):
                degraded.read_facts(tmp)
            (tmp / degraded.DEGRADED_HEADER).write_text("// nothing", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "NX_SAFE_DEGRADABLE_EVENTS"):
                degraded.read_facts(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def fake_config(safety: dict, pwm=(("MOT1", "FTM0"), ("MOT2", "FTM3"), ("PUMP", "FTM4")),
                devices=(("GD_A", ["SPI0"]), ("GD_B", ["SPI0"]), ("GD_C", ["SPI1"]), ("SENSOR", ["SPI1"]),
                         ("SWITCH", [])),
                fault_memory=True, supervisor=True, safe_off=(), bound=None, outputs=None):
    """A configuration as checks.py leaves it for degraded.check(): used PWM groups, integrated devices, modules.
    IO-45: `bound` names the PWM group a device drives (board pwm[].gate_driver -> DeviceUse.pwm), `outputs` the dio
    output of a device's safe_off connection (default EN for every one)."""
    groups = [SimpleNamespace(name=n, instance=i) for n, i in pwm]
    module = SimpleNamespace(connect={"drv_en": SimpleNamespace(safe_off=True)})
    bound, outputs = bound or {}, outputs or {}
    integration = SimpleNamespace(
        devices=[DeviceUse(n, "part", "gd" if n in safe_off else "other", spi,
                           [], connect={"drv_en": outputs.get(n, "EN")} if n in safe_off else {}, pwm=bound.get(n))
                 for n, spi in devices],
        spi=["SPI0", "SPI1"],
        modules={"safeSupervisor": ModuleUse("safeSupervisor")} if supervisor else {})
    return SimpleNamespace(
        project=SimpleNamespace(safety=Safety.model_validate(
            {"safe_state_output": "LED1", "locked_pin": "LED2", "test_pin": "PTE14", **safety})),
        selected_pwm=lambda: [(g, None) for g in groups],
        integration=integration,
        modules=SimpleNamespace(manifests={"gd": module}),
        diag=SimpleNamespace(fault_memory=object()) if fault_memory else None,
        rte=None, running_tree=REPO, code_inputs=[])


GROUPS = [{"name": "DRIVE_A", "pwm": "MOT1"}, {"name": "DRIVE_B", "pwm": "MOT2"}, {"name": "AUX", "pwm": "PUMP"}]


def run(safety: dict, **kwargs):
    cfg, rep = fake_config(safety, **kwargs), Reporter()
    plan = degraded.check(cfg, rep)
    return cfg, rep, plan


class CheckTest(unittest.TestCase):
    """Every rule of the concept's check tables (sections 4.2 and 4.3) with its YAML path."""

    def errors(self, safety: dict, **kwargs):
        cfg, rep, plan = run(safety, **kwargs)
        self.assertIsNone(plan)
        self.assertNotIn("degraded_groups", cfg.integration.modules.get("safeSupervisor", ModuleUse("x")).derived)
        return [(path, text) for severity, path, text in findings(rep.messages) if severity == "error"]

    def test_valid_mapping_derives_the_supervisor_values(self):
        cfg, rep, plan = run({"output_groups": GROUPS, "degraded": [
            {"event": "DEVICE_FAULT", "device": "GD_A", "groups": ["DRIVE_A"]},
            {"event": "DEVICE_FAULT", "device": "GD_B", "groups": ["DRIVE_B"]},
            {"event": "DEVICE_FAULT", "device": "GD_C", "groups": ["AUX", "DRIVE_B"]},
            {"event": "DEVICE_FAULT", "device": "SWITCH", "groups": ["AUX"]}]})
        self.assertEqual(findings(rep.messages, "error"), [])
        derived = {k: v for k, (v, _how) in cfg.integration.modules["safeSupervisor"].derived.items()}
        self.assertEqual(derived["degraded_groups"], 3)
        self.assertEqual([derived[f"degraded_group_{k}_entries"] for k in range(3)], [0x1, 0x2, 0x4])
        self.assertEqual(derived["degraded_mappings"], 4)
        # event << 24 | instance << 16 | bus << 8 | groups; SPI0 = 0x20, SPI1 = 0x21, no bus 0xFF
        self.assertEqual(derived["degraded_mapping_0"], 0x13002001)
        self.assertEqual(derived["degraded_mapping_1"], 0x13012002)
        self.assertEqual(derived["degraded_mapping_2"], 0x13022106)
        self.assertEqual(derived["degraded_mapping_3"], 0x1304FF04)
        self.assertEqual(derived["degraded_mapping_0_bus_groups"], 0x3)   # GD_A and GD_B: all of SPI0 mapped
        self.assertEqual(derived["degraded_mapping_1_bus_groups"], 0x3)
        self.assertEqual(derived["degraded_mapping_2_bus_groups"], 0x0)   # SENSOR on SPI1 is not mapped
        self.assertEqual(derived["degraded_mapping_3_bus_groups"], 0x0)   # no bus
        warnings = [(p, t) for s, p, t in findings(rep.messages) if s == "warning"]
        self.assertIn("safety.degraded[2].device", [p for p, _ in warnings])
        self.assertTrue(any("SENSOR" in t and "stays fail-safe" in t for _, t in warnings))
        self.assertEqual([g[2] for g in plan.groups], [0, 1, 2])
        self.assertTrue(any(f.name == "Degraded.h" for f in cfg.code_inputs))

    def test_mcu_internal_events_are_refused(self):
        for event in ("ROM_CHECK_FAILURE", "INTEGRITY_EVENT_DMA_ECC_ERROR", "WATCHDOG_STARTUP_CHECK_FAILURE",
                      "SAFE_WATCHDOG_SERVICE_DEVIATION", "MPU_CONFIGURATION_ERROR", "STACK_OVERFLOW",
                      "INTERNAL_SUPPLIES_ERROR", "SAFETY_CYCLE_PERIOD_VIOLATION", "RESET_LOOP_LIMIT",
                      "DEVICE_REPORT", "RTE_TIMING_VIOLATION"):
            errors = self.errors({"output_groups": GROUPS, "degraded": [{"event": event, "groups": ["DRIVE_A"]}]})
            self.assertEqual([p for p, _ in errors], ["safety.degraded[0].event"], event)
            self.assertIn("fail-safe only", errors[0][1])
        errors = self.errors({"output_groups": GROUPS, "degraded": [{"event": "NO_SUCH", "groups": ["DRIVE_A"]}]})
        self.assertEqual(errors, [("safety.degraded[0].event", "NO_SUCH is no event of the safe supervisor")])

    def test_mapping_rules(self):
        cases = [
            ({"event": "DEVICE_FAULT", "groups": ["DRIVE_A"]}, "safety.degraded[0]", "needs `device`"),
            ({"event": "DEVICE_FAULT", "device": "NOPE", "groups": ["DRIVE_A"]}, "safety.degraded[0].device",
             "no board device"),
            ({"event": "DEVICE_FAULT", "device": "GD_A", "groups": []}, "safety.degraded[0].groups", "no group"),
            ({"event": "DEVICE_FAULT", "device": "GD_A", "groups": ["NOPE"]}, "safety.degraded[0].groups",
             "unknown safe-output group NOPE"),
            ({"event": "DEVICE_FAULT", "device": "GD_A", "groups": ["DRIVE_A", "DRIVE_A"]},
             "safety.degraded[0].groups", "twice"),
        ]
        for entry, path, text in cases:
            errors = self.errors({"output_groups": GROUPS, "degraded": [entry]})
            self.assertEqual([p for p, _ in errors], [path], entry)
            self.assertIn(text, errors[0][1])
        errors = self.errors({"output_groups": GROUPS, "degraded": [
            {"event": "DEVICE_FAULT", "device": "GD_A", "groups": ["DRIVE_A"]},
            {"event": "DEVICE_FAULT", "device": "GD_A", "groups": ["DRIVE_B"]}]})
        self.assertEqual([p for p, _ in errors], ["safety.degraded[1].device"])
        many = [{"event": "DEVICE_FAULT", "device": "GD_A", "groups": ["DRIVE_A"]}] * 17
        self.assertIn("safety.degraded", [p for p, _ in self.errors({"output_groups": GROUPS, "degraded": many})])

    def test_group_rules(self):
        mapping = [{"event": "DEVICE_FAULT", "device": "GD_A", "groups": ["DRIVE_A"]}]
        cases = [
            ([{"name": "DRIVE_A", "pwm": "MOT1"}, {"name": "DRIVE_A", "pwm": "MOT2"}, {"name": "AUX", "pwm": "PUMP"}],
             "safety.output_groups[1].name", "listed twice"),
            ([{"name": "DRIVE_A", "pwm": "NOPE"}, {"name": "DRIVE_B", "pwm": "MOT2"}, {"name": "AUX", "pwm": "PUMP"}],
             "safety.output_groups[0].pwm", "no PWM group the project uses"),
            ([{"name": "DRIVE_A", "pwm": "MOT1"}, {"name": "DRIVE_B", "pwm": "MOT1"}, {"name": "AUX", "pwm": "PUMP"}],
             "safety.output_groups[1].pwm", "already in safe-output group DRIVE_A"),
            ([{"name": "DRIVE_A", "pwm": "MOT1"}, {"name": "DRIVE_B", "pwm": "MOT2"}], "safety.output_groups",
             "missing: PUMP"),
        ]
        for groups, path, text in cases:
            errors = self.errors({"output_groups": groups, "degraded": mapping})
            self.assertIn(path, [p for p, _ in errors], groups)
            self.assertTrue(any(text in t for _, t in errors), errors)
        # two groups on one timer are not independent (concept section 4.3)
        errors = self.errors({"output_groups": GROUPS, "degraded": mapping},
                             pwm=(("MOT1", "FTM0"), ("MOT2", "FTM0"), ("PUMP", "FTM4")))
        self.assertEqual([p for p, _ in errors], ["safety.output_groups[1].pwm"])
        self.assertIn("not independent", errors[0][1])
        nine = [{"name": f"G{i}", "pwm": f"P{i}"} for i in range(9)]
        errors = self.errors({"output_groups": nine, "degraded": mapping},
                             pwm=tuple((f"P{i}", f"FTM{i}") for i in range(9)))
        self.assertIn("safety.output_groups", [p for p, _ in errors])

    def test_prerequisites(self):
        mapping = [{"event": "DEVICE_FAULT", "device": "GD_A", "groups": ["DRIVE_A"]}]
        self.assertEqual([p for p, _ in self.errors({"degraded": mapping})], ["safety.degraded"])
        self.assertEqual([p for p, _ in self.errors({"output_groups": GROUPS, "degraded": mapping},
                                                    fault_memory=False)], ["safety.degraded"])
        self.assertEqual([p for p, _ in self.errors({"output_groups": GROUPS, "degraded": mapping},
                                                    supervisor=False)], ["safety.degraded"])

    def test_warnings(self):
        # groups without a mapping: no effect, nothing derived
        cfg, rep, plan = run({"output_groups": GROUPS})
        self.assertIsNone(plan)
        self.assertEqual([(s, p) for s, p, _ in findings(rep.messages)], [("warning", "safety.output_groups")])
        self.assertEqual(cfg.integration.modules["safeSupervisor"].derived, {})
        # a mapping of every group: S1 without reset at once
        cfg, rep, plan = run({"output_groups": GROUPS, "degraded": [
            {"event": "DEVICE_FAULT", "device": "SWITCH", "groups": ["DRIVE_A", "DRIVE_B", "AUX"]}]})
        self.assertIsNotNone(plan)
        self.assertIn(("warning", "safety.degraded[0].groups"), [(s, p) for s, p, _ in findings(rep.messages)])
        # a safe_off output of a device that drives no PWM group belongs to no group (IO-45: only a bound one joins)
        cfg, rep, plan = run({"output_groups": GROUPS, "degraded": [
            {"event": "DEVICE_FAULT", "device": "SWITCH", "groups": ["AUX"]}]}, safe_off=("GD_A",))
        self.assertTrue(any(p == "safety.output_groups" and "GD_A.drv_en" in t and "S1 only" in t
                            for s, p, t in findings(rep.messages, "warning")))
        derived = {k: v for k, (v, _how) in cfg.integration.modules["safeSupervisor"].derived.items()}
        self.assertEqual([derived[f"degraded_group_{k}_entries"] for k in range(3)], [0x1, 0x2, 0x4])

    def test_bound_enable_joins_the_group_of_its_pwm_group(self):
        """IO-45: the enable of a gate driver bound to a PWM group (board pwm[].gate_driver) is in that group."""
        mapping = [{"event": "DEVICE_FAULT", "device": "GD_A", "groups": ["DRIVE_A"]}]
        # GD_A drives MOT1, GD_C drives PUMP; their enables are entries 3 and 4 (after the three PWM groups)
        cfg, rep, plan = run({"output_groups": GROUPS, "degraded": mapping}, safe_off=("GD_A", "GD_C"),
                             bound={"GD_A": "MOT1", "GD_C": "PUMP"}, outputs={"GD_A": "EN_A", "GD_C": "EN_C"})
        self.assertEqual(findings(rep.messages, "error"), [])
        # no enable left outside a group (the only warning is the one of the unmapped GD_B on the bus of GD_A)
        self.assertEqual([p for s, p, _ in findings(rep.messages) if s == "warning"], ["safety.degraded[0].device"])
        derived = {k: v for k, (v, how) in cfg.integration.modules["safeSupervisor"].derived.items()}
        self.assertEqual([derived[f"degraded_group_{k}_entries"] for k in range(3)], [0x1 | 0x8, 0x2, 0x4 | 0x10])
        how = cfg.integration.modules["safeSupervisor"].derived["degraded_group_0_entries"][1]
        self.assertIn("GD_A.drv_en", how)
        self.assertEqual([(e.index, e.output, e.pwm) for e in degraded.safe_off_entries(cfg)],
                         [(3, "EN_A", ("MOT1",)), (4, "EN_C", ("PUMP",))])
        # a device without a PWM group keeps its enable out of every group, with the warning
        cfg, rep, plan = run({"output_groups": GROUPS, "degraded": mapping}, safe_off=("GD_A", "GD_C"),
                             bound={"GD_A": "MOT1"}, outputs={"GD_A": "EN_A", "GD_C": "EN_C"})
        derived = {k: v for k, (v, _how) in cfg.integration.modules["safeSupervisor"].derived.items()}
        self.assertEqual([derived[f"degraded_group_{k}_entries"] for k in range(3)], [0x1 | 0x8, 0x2, 0x4])
        warnings = [(p, t) for s, p, t in findings(rep.messages)
                    if s == "warning" and p == "safety.output_groups"]
        self.assertEqual(len(warnings), 1)
        self.assertIn("GD_C.drv_en", warnings[0][1])
        self.assertNotIn("GD_A.drv_en", warnings[0][1])
        # one output as the enable of two gate drivers in two different groups: in two groups, refused
        errors = self.errors({"output_groups": GROUPS, "degraded": mapping}, safe_off=("GD_A", "GD_B"),
                             bound={"GD_A": "MOT1", "GD_B": "MOT2"}, outputs={"GD_A": "EN", "GD_B": "EN"})
        self.assertEqual([p for p, _ in errors], ["safety.output_groups"])
        self.assertIn("DRIVE_A and DRIVE_B", errors[0][1])
        # ... in the same group it is one entry of that group
        cfg, rep, plan = run({"output_groups": [{"name": "DRIVE_A", "pwm": "MOT1"}, {"name": "DRIVE_B", "pwm": "MOT2"},
                                                {"name": "AUX", "pwm": "PUMP"}], "degraded": mapping},
                             safe_off=("GD_A", "GD_B"), bound={"GD_A": "MOT1", "GD_B": "MOT1"},
                             outputs={"GD_A": "EN", "GD_B": "EN"})
        self.assertEqual(findings(rep.messages, "error"), [])
        derived = {k: v for k, (v, _how) in cfg.integration.modules["safeSupervisor"].derived.items()}
        self.assertEqual(derived["degraded_group_0_entries"], 0x1 | 0x8)

    def test_without_safety_entries_nothing_happens(self):
        cfg, rep, plan = run({})
        self.assertIsNone(plan)
        self.assertEqual(rep.messages, [])
        self.assertEqual(cfg.integration.modules["safeSupervisor"].derived, {})


class DemoTest(unittest.TestCase):
    """The real demo project: unchanged without a mapping, and with one on a copy - agreement with bswgen and rtegen."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def demo_copy(self, safety: dict) -> Path:
        project = yaml.safe_load(DEMO_PROJECT.read_text(encoding="utf-8"))
        folder = DEMO_PROJECT.parent
        project["project"]["board"] = (REPO / "tools" / "nxconfig" / "test" / "profiles" / "boards" / "testboard_full").as_posix()
        project["project"]["rte"] = (folder / project["project"]["rte"]).as_posix()
        project["project"]["diag"] = (folder / project["project"]["diag"]).as_posix()
        project["safety"].update(safety)
        path = self.tmp / "demo" / "project.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(project, sort_keys=False, allow_unicode=True), encoding="utf-8")
        return path

    def test_demo_without_mapping_derives_nothing(self):
        config, messages = nxconfig.check(DEMO_PROJECT)
        self.assertEqual([m for m in messages if m.severity != "info"], [])
        derived = config.integration.modules["safeSupervisor"].derived
        self.assertEqual([k for k in derived if k.startswith("degraded")], [])

    def test_demo_copy_with_mapping(self):
        path = self.demo_copy({"output_groups": [{"name": "DRIVE", "pwm": "MOT1"}],
                               "degraded": [{"event": "DEVICE_FAULT", "device": "MOT1_GD", "groups": ["DRIVE"]}]})
        config, messages = nxconfig.check(path)
        self.assertEqual([m for m in messages if m.severity == "error"], [])
        warnings = {m.yaml_path for m in messages if m.severity == "warning"}
        # one group only: every group; the high-side switch KL30B_SW.in drives no PWM group and belongs to no group,
        # (IO-45: the enable MOT1_GD.drv_en of the gate driver of MOT1 does); the demo's rte does not read
        # safety.degraded
        self.assertEqual(warnings, {"safety.degraded[0].groups", "safety.output_groups", "safety.degraded"})
        loose = [m.text for m in messages if m.severity == "warning" and m.yaml_path == "safety.output_groups"]
        self.assertTrue(any("KL30B_SW.in" in t for t in loose), loose)
        self.assertFalse(any("MOT1_GD.drv_en" in t for t in loose), loose)
        use = config.integration.modules["safeSupervisor"]
        derived = {k: v for k, (v, _how) in use.derived.items()}
        import rtegen                       # noqa: E402  (the bus id rule of SYS-29)
        bus = rtegen.device_bus_ids(config)["MOT1_GD"]
        instance = [d.name for d in config.integration.devices].index("MOT1_GD")
        self.assertEqual(derived["degraded_mapping_0"], (DEVICE_FAULT << 24) | (instance << 16) | (bus << 8) | 0x1)
        # IO-45: entry 0 the PWM group MOT1, entry 1 its gate driver's enable MOT1_GD_DRV_EN
        self.assertEqual(derived["degraded_group_0_entries"], 0x3)
        # the generated header of the module carries the values, one macro each
        from bswgen import modules as bswmodules  # noqa: E402
        header = bswmodules.render_parameter_header(config.integration, config.modules, "safeSupervisor", "test")
        self.assertIn("#define NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_GROUPS 1U", header)
        self.assertIn("#define NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_GROUP_0_ENTRIES 3U", header)
        # IO-45: the entries are the ones of the table tools/bswgen generates for this project, entry for entry
        import bswgen                       # noqa: E402
        out = self.tmp / "gen"
        bswgen.generate(path, out)
        table = (out / "bsp/safeOutputs/safeOutputsConfiguration.h").read_text(encoding="utf-8")
        # SYS-30 appends the read-back table NX_SAFE_OUTPUT_READBACKS with the same X( entries: only the first table
        table = table.split("#define NX_SAFE_OUTPUTS(X)", 1)[1].split("#define NX_SAFE_OUTPUT_READBACKS", 1)[0]
        names = re.findall(r"^    X\((\w+),", table, re.M)
        self.assertEqual(names, ["MOT1", "MOT1_GD_DRV_EN", "EN_SW_KL30B"])
        self.assertEqual([(e.index, e.output) for e in degraded.safe_off_entries(config)],
                         [(1, "MOT1_GD_DRV_EN"), (2, "EN_SW_KL30B")])
        self.assertEqual([names[i] for i in range(len(names)) if (derived["degraded_group_0_entries"] >> i) & 1],
                         ["MOT1", "MOT1_GD_DRV_EN"])
        self.assertIn(f"#define NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPING_0 {derived['degraded_mapping_0']}U",
                      header)
        # MOT1_GD is the only device on its SPI bus (the SBC has its own): a lost bus degrades just its group
        self.assertEqual(derived["degraded_mapping_0_bus_groups"], 0x1)
        self.assertIn("#define NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPING_0_BUS_GROUPS 1U", header)

    def test_table_order_is_the_one_of_bswgen(self):
        """The entry bit of a group is its PWM group's place in the safe outputs table of tools/bswgen."""
        config, _ = nxconfig.check(DEMO_PROJECT)
        order = [grp.name for grp, _ in config.selected_pwm()]
        source = (REPO / "tools/bswgen/bswgen.py").read_text(encoding="utf-8")
        start = source.index("    def collect_safe_outputs(self):")
        body = source[start:source.index("\n    def ", start + 10)]
        # the table starts with the PWM groups in selected_pwm() order, before any safe_off output
        self.assertLess(body.index("for grp, _ in self.config.selected_pwm():"), body.index("for dev in"))
        self.assertEqual(order, ["MOT1"])


if __name__ == "__main__":
    unittest.main()
