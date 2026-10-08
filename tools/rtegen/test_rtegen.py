"""Unit tests for tools/rtegen against the checked-in DemoSwc ARXML.

Run: .venv/Scripts/python -m unittest tools/rtegen/test_rtegen.py
"""

import ast
import contextlib
import copy
import inspect
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import types
import unittest
from unittest import mock
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))

import rtegen  # noqa: E402

# PLAT-62: the own full-featured test profile (synthetic board and project, demo component, demo CAN matrix) instead
# of the eval board project - the eval board stays private (PLAT-46 F10), a test against it would be red in the
# exported open tree. Tests that prove something about the eval board itself use EVAL_PROJECT_FILE and skip there.
PROFILES = REPO / "tools" / "nxconfig" / "test" / "profiles"
PROJECT_FILE = PROFILES / "projects" / "testproject_full" / "project.yaml"
BOARD_DIR = PROFILES / "boards" / "testboard_full"
PROJECT = rtegen.nxconfig.load(PROJECT_FILE)  # connection targets: board signals the test profile uses
EVAL_PROJECT_FILE = REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml"
eval_board = unittest.skipUnless(EVAL_PROJECT_FILE.is_file(),
                                 "private eval board project not in this tree (exported open tree, PLAT-62)")
# build-test profile with two CAN buses and no SBC (ARCH-09), used for the multi-bus checks of ARCH-07
PROJECT_TWO_BUSES = rtegen.nxconfig.load(REPO / "projects" / "buildtest_s32k148_min" / "project.yaml")
ECU_FILE = PROJECT_FILE.parent / "rte.yaml"   # ECU description of the demo project (project.rte)
# RTE-22: the tests render these descriptions into a scratch folder, where a path relative to the rte.yaml would
# point next to the copy. with_resolved_paths() pins them to the components and the DBC they were written for.
ECU = rtegen.with_resolved_paths(yaml.safe_load(ECU_FILE.read_text(encoding="utf-8")), ECU_FILE)


class RteGenTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_with(self, ecu):
        path = self.tmp / "rte.yaml"
        path.write_text(yaml.safe_dump(ecu), encoding="utf-8")
        return rtegen.generate(path, PROJECT, self.tmp / "out", REPO)

    def expect_error(self, ecu, fragment):
        with self.assertRaises(rtegen.RteError) as ctx:
            self.run_with(ecu)
        self.assertIn(fragment, str(ctx.exception))

    def test_demo_component_is_parsed(self):
        comp = rtegen.parse_component("DemoSwc", REPO / "swc" / "DemoSwc" / "generated" / "arxml")
        self.assertEqual({r.name: r.period_ms for r in comp.runnables},
                         {"DemoSwc_Init": None, "Run10ms": 10, "Run100ms": 100})
        self.assertEqual(comp.ports["Button1.Button1"].direction, "in")
        self.assertEqual(comp.ports["Button1.Button1"].type.native, "boolean")
        self.assertEqual(comp.ports["MotorNtcFiltered_mV.MotorNtcFiltered_mV"].type.native, "uint32")

    def test_generation_output(self):
        components, buffers, tasks = self.run_with(ECU)
        self.assertEqual([(t.name, t.period_ms) for t in tasks], [("swc", 10)])
        # 5 ports + monitored PC_Command signals (3 values, CRC, counter) + the qualifier of PC_Command
        self.assertEqual(len(buffers), 11)
        out = self.tmp / "out"
        for name in ("Rte_Type.h", "Rte_DemoSwc.h", "Rte_Internal.h", "Rte.c", "RteConfig.h", "RteConfig.cpp",
                     "rte_components.cmake"):
            self.assertTrue((out / name).exists(), name)
        config = (out / "RteConfig.cpp").read_text(encoding="utf-8")
        self.assertIn("::bios::DigitalInput::BUTTON1", config)
        self.assertIn("::bios::AnalogInput::Ai_MOTOR_NTC", config)
        self.assertIn("::bios::Output::LED2", config)
        self.assertIn('{"DemoSwc.Run100ms", &Run100ms, 100U, 0U, 0U, 10U, 0U}', config)
        header = (out / "Rte_DemoSwc.h").read_text(encoding="utf-8")
        self.assertIn("#define Rte_Read_Button1_Button1 Rte_Read_DemoSwc_Button1_Button1", header)

    def test_generation_is_stable(self):
        self.run_with(ECU)
        first = {p.name: p.read_text(encoding="utf-8") for p in (self.tmp / "out").rglob("*") if p.is_file()}
        self.run_with(ECU)
        second = {p.name: p.read_text(encoding="utf-8") for p in (self.tmp / "out").rglob("*") if p.is_file()}
        self.assertIn("Config.h", first)
        self.assertEqual(first, second)

    def test_unconnected_required_port(self):
        """RTE-27: an unconnected required port is a warning, no error - it reads its init value, const buffer."""
        ecu = copy.deepcopy(ECU)
        del ecu["connections"]["DemoSwc.Button1.Button1"]
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            _components, buffers, _tasks = self.run_with(ecu)
        self.assertIn("rtegen: warning: required port DemoSwc.Button1.Button1 is not connected: it reads its init "
                      "value 0 from a constant buffer", stderr.getvalue())
        b = buffers["DemoSwc.Button1.Button1"]
        self.assertEqual((b.init_only, b.init, b.constant, b.links), (rtegen.INIT_UNCONNECTED, "0", True, []))
        self.assertIn("boolean const Rte_Buf_DemoSwc_Button1_Button1 = 0;",
                      (self.tmp / "out" / "Rte.c").read_text(encoding="utf-8"))
        self.assertIn("extern boolean const Rte_Buf_DemoSwc_Button1_Button1;",
                      (self.tmp / "out" / "Rte_Internal.h").read_text(encoding="utf-8"))

    def test_unknown_board_signal(self):
        ecu = copy.deepcopy(ECU)
        ecu["connections"]["DemoSwc.Button1.Button1"] = "dio.in.BUTTON9"
        self.expect_error(ecu, "BUTTON9 is not a dio.in signal used by the project")

    def test_board_signal_not_used_by_the_project(self):
        project = yaml.safe_load(PROJECT_FILE.read_text(encoding="utf-8"))
        project["project"]["board"] = BOARD_DIR.as_posix()
        project["project"]["rte"] = ECU_FILE.as_posix()
        project["project"].pop("diag", None)   # relative to the demo folder (ARCH-51); test_rtegen_diag.py
        project["use"]["dio"] = ["BUTTON2", "LED1", "LED2"]   # BUTTON1 exists on the board, but is not used
        path = self.tmp / "project.yaml"
        path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        config = rtegen.nxconfig.load(path)
        self.assertNotIn("BUTTON1", rtegen.board_signals(config)["dio.in"])
        with self.assertRaises(rtegen.RteError) as ctx:
            rtegen.generate(ECU_FILE, config, self.tmp / "out", REPO)
        self.assertIn("BUTTON1 is not a dio.in signal used by the project", str(ctx.exception))

    def test_demo_project_keeps_ecu_description_and_dbc(self):
        # ARCH-04: rte.yaml and DBC belong to the project. RTE-22: the paths inside rte.yaml are relative to the
        # rte.yaml itself, so the DBC is simply its neighbour and no repository root is needed to find either.
        self.assertEqual(PROJECT.rte_file, ECU_FILE)
        written = yaml.safe_load(ECU_FILE.read_text(encoding="utf-8"))       # as the file spells it
        base = rtegen.paths_base(written, ECU_FILE, REPO)
        self.assertEqual(base, ECU_FILE.parent, "format 2 is based on the folder of the rte.yaml")
        dbc = base / written["can"]["dbc"]
        self.assertTrue(dbc.is_file(), dbc)
        self.assertEqual(dbc.parent, PROJECT_FILE.parent, "the DBC is the neighbour of the description")
        for entry in written["components"]:
            self.assertTrue((base / entry["path"] / "generated" / "arxml").is_dir(), entry["path"])

    def test_generate_project(self):
        components, _, tasks = rtegen.generate_project(PROJECT_FILE, self.tmp / "out", REPO)
        self.assertEqual(list(components), ["DemoSwc"])
        self.assertEqual(tasks[0].period_ms, 10)
        project = yaml.safe_load(PROJECT_FILE.read_text(encoding="utf-8"))
        project["project"]["board"] = BOARD_DIR.as_posix()
        project["project"].pop("rte")
        project["project"].pop("diag", None)   # relative to the demo folder (ARCH-51)
        path = self.tmp / "project.yaml"
        path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        with self.assertRaises(rtegen.RteError) as ctx:
            rtegen.generate_project(path, self.tmp / "out", REPO)
        self.assertIn("project.rte (ECU description) is not set", str(ctx.exception))

    def test_direction_mismatch(self):
        ecu = copy.deepcopy(ECU)
        ecu["connections"]["DemoSwc.Led1.Led1"] = "dio.in.BUTTON1"
        self.expect_error(ecu, "output port cannot use dio.in")

    def test_type_mismatch(self):
        ecu = copy.deepcopy(ECU)
        ecu["connections"]["DemoSwc.MotorNtc_mV.MotorNtc_mV"] = "dio.in.BUTTON1"
        self.expect_error(ecu, "type uint32 does not fit dio.in")

    def test_required_port_cannot_stay_in_rte(self):
        ecu = copy.deepcopy(ECU)
        ecu["connections"]["DemoSwc.Button1.Button1"] = "rte"
        self.expect_error(ecu, "only provided ports can stay inside the RTE")
        self.expect_error(ecu, "a required port without source: rte.init")

    def test_required_port_keeps_init_value(self):
        # COM-13: a required port without source in this project (rte.init) keeps its init value
        ecu = copy.deepcopy(ECU)
        ecu["connections"]["DemoSwc.Button1.Button1"] = "rte.init"
        self.run_with(ecu)
        text = "\n".join(p.read_text(encoding="utf-8") for p in (self.tmp / "out").rglob("*") if p.is_file())
        self.assertIn("DemoSwc.Button1.Button1", text)

    def test_provided_port_cannot_keep_init_value(self):
        ecu = copy.deepcopy(ECU)
        ecu["connections"]["DemoSwc.Led1.Led1"] = "rte.init"
        self.expect_error(ecu, "only required ports can keep their init value")

    def test_unknown_port(self):
        ecu = copy.deepcopy(ECU)
        ecu["connections"]["DemoSwc.Nope.Nope"] = "rte"
        self.expect_error(ecu, "unknown component port")

    def test_base_period_must_divide_periods(self):
        # ECU description without tasks: one task swc with base_period_ms (format before RTE-02)
        ecu = copy.deepcopy(ECU)
        del ecu["tasks"], ecu["can"]["task"]
        ecu["base_period_ms"] = 30
        self.expect_error(ecu, "base_period_ms: period 10 ms of DemoSwc.Run10ms is no multiple of the period 30 ms "
                               "of task swc")
        ecu["tasks"] = ECU["tasks"]
        self.expect_error(ecu, "base_period_ms: not allowed together with tasks:")    # schema (ARCH-34)

    def test_swc_to_swc_connection(self):
        # A required port fed by a provided port of the same component shares the sender's buffer.
        ecu = copy.deepcopy(ECU)
        ecu["connections"]["DemoSwc.Button1.Button1"] = "DemoSwc.Led2.Led2"
        _, buffers, _ = self.run_with(ecu)
        self.assertNotIn("DemoSwc.Button1.Button1", buffers)
        rte_c = (self.tmp / "out" / "Rte.c").read_text(encoding="utf-8")
        self.assertIn("*data = (boolean)Rte_Buf_DemoSwc_Led2_Led2;", rte_c)

    def test_provided_port_with_several_targets(self):
        ecu = copy.deepcopy(ECU)
        ecu["connections"]["DemoSwc.Led1.Led1"] = ["dio.out.LED1", "dio.out.LED2"]
        self.expect_error(ecu, "already connected to dio.out.LED1")
        ecu["connections"]["DemoSwc.Led1.Led1"] = []
        self.expect_error(ecu, "connections.DemoSwc.Led1.Led1: List should have at least 1 item")   # schema
        ecu["connections"]["DemoSwc.Button1.Button1"] = ["dio.in.BUTTON1"]
        ecu["connections"]["DemoSwc.Led1.Led1"] = "dio.out.LED1"
        self.expect_error(ecu, "a required port has exactly one source")

    def test_model_hash_check(self):
        swc = self.tmp / "swc"
        (swc / "generated").mkdir(parents=True)
        (swc / "X.slx").write_bytes(b"model v1")
        (swc / "generated" / "model.sha256").write_text(
            "a4d0c0d1ea1e53a7fbd7d5a8b5dbcad61b1ceb97cd36e1f5a39d1a0c1b5e8f9d  X.slx\n", encoding="ascii")
        self.assertFalse(rtegen.model_is_current(swc, "X"))
        import hashlib
        (swc / "generated" / "model.sha256").write_text(hashlib.sha256(b"model v1").hexdigest() + "  X.slx\n",
                                                        encoding="ascii")
        self.assertTrue(rtegen.model_is_current(swc, "X"))
        self.assertIsNone(rtegen.model_is_current(self.tmp / "missing", "X"))

    def test_model_hash_names_every_input(self):
        """TOOL-169: _init.m, init_data/ and libraries are inputs, too; the warning names the file."""
        import hashlib
        swc = self.tmp / "swc" / "X"
        (swc / "generated").mkdir(parents=True)
        (swc / "init_data").mkdir()
        lib = self.tmp / "swc" / "template" / "Lib.slx"
        lib.parent.mkdir(parents=True)
        files = {"X.slx": b"model", "X_init.m": b"a = 1;\n", "init_data/b.m": b"b = 1;\n", "X_data.m": b"p = 1;\n"}
        for rel, data in files.items():
            (swc / rel).write_bytes(data)
        lib.write_bytes(b"lib")
        record = [f"{hashlib.sha256(files[r]).hexdigest()}  {r}" for r in ("X.slx", "X_init.m", "init_data/b.m")]
        record += [f"{hashlib.sha256(b'lib').hexdigest()}  ../template/Lib.slx", "# nx-model-inputs 2"]
        (swc / "generated" / "model.sha256").write_text("\n".join(record) + "\n", encoding="ascii")
        self.assertTrue(rtegen.model_is_current(swc, "X"))
        (swc / "X_data.m").write_bytes(b"p = 2;\n")             # values the RTE reads, no input of the code
        self.assertTrue(rtegen.model_is_current(swc, "X"))
        (swc / "X_init.m").write_bytes(b"a = 2;\n")
        self.assertFalse(rtegen.model_is_current(swc, "X"))
        self.assertEqual(rtegen.model_warning("X", rtegen.model_changes(swc, "X")),
                         "components: X_init.m differs from the model the generated code was built from, "
                         "run tools/swc/swc_build.m")
        lib.unlink()
        (swc / "init_data" / "c.m").write_bytes(b"c = 1;\n")
        (swc / "X_init.m").write_bytes(b"a = 1;\n")
        self.assertEqual(rtegen.model_warning("X", rtegen.model_changes(swc, "X")),
                         "components: X: init_data/c.m is new since the generated code was built; "
                         "../template/Lib.slx is missing (input of the generated code), run tools/swc/swc_build.m")


NO_CAN = {k: v for k, v in ECU.items() if k not in ("can", "uds")}
NO_CAN["connections"] = {k: ([t for t in v if not t.startswith("can.")] if isinstance(v, list) else v)
                         for k, v in ECU["connections"].items()}
NO_CAN["connections"] = {k: (v[0] if isinstance(v, list) and len(v) == 1 else (v or "rte"))
                         for k, v in NO_CAN["connections"].items()}
# the qualifier port of DemoSwc (COM-13) has no source without CAN: it keeps its init value
NO_CAN["connections"]["DemoSwc.PcCommandQlf.PcCommandQlf"] = "rte.init"

CAN_DBC = """VERSION ""
NS_ :
BS_:
BU_: EVB PC
BO_ 256 Tx: 8 EVB
 SG_ Flag : 0|1@1+ (1,0) [0|1] "" PC
 SG_ Wide : 8|16@1+ (1,0) [0|65535] "" PC
 SG_ Neg : 24|8@1- (1,0) [-128|-1] "" PC
 SG_ Pair : 32|2@1+ (1,5) [5|8] "" PC
BO_ 257 NoCycle: 8 EVB
 SG_ S : 0|8@1+ (1,0) [0|0] "" PC
BO_ 512 Rx: 8 PC
 SG_ Small : 0|8@1+ (1,0) [0|200] "" EVB
 SG_ Big : 8|16@1- (1,0) [-1000|1000] "" EVB
 SG_ Frac : 24|8@1+ (0.5,0) [0|100] "" EVB
 SG_ Other : 32|8@1+ (1,0) [0|0] "" PC
BO_ 513 Bad: 8 PC
 SG_ S : 0|8@1+ (1,0) [0|300] "" EVB
BA_DEF_ BO_  "GenMsgCycleTime" INT 0 65535;
BA_DEF_DEF_  "GenMsgCycleTime" 0;
BA_ "GenMsgCycleTime" BO_ 256 30;
BA_ "GenMsgCycleTime" BO_ 512 100;
"""


CAPTURE_PORT = "DemoSwc.MotorNtc_mV.MotorNtc_mV"   # uint32 required port, read by DemoSwc.Run100ms


class RteCaptureTest(unittest.TestCase):
    """IO-03: capture.<Channel>.<value> - period, frequency, duty cycle and quality of a PWM input."""

    setUp, tearDown = RteGenTest.setUp, RteGenTest.tearDown
    run_with, expect_error = RteGenTest.run_with, RteGenTest.expect_error

    def ecu(self, **connections):
        ecu = copy.deepcopy(NO_CAN)
        ecu["connections"].update(connections)
        return ecu

    def generated(self, name="RteConfig.cpp"):
        return (self.tmp / "out" / name).read_text(encoding="utf-8")

    def test_board_channels_are_targets(self):
        self.assertEqual(rtegen.board_signals(PROJECT)["capture"], {"PWM_IN"})
        # the channel the demo project does not use (PILOT_DUTY shares FTM0 with the motor PWM) is no target
        self.assertNotIn("PILOT_DUTY", rtegen.board_signals(PROJECT)["capture"])

    def test_value_is_read_once_per_cycle(self):
        _, buffers, _ = self.run_with(self.ecu(**{CAPTURE_PORT: "capture.PWM_IN.freq_mhz"}))
        b = buffers[CAPTURE_PORT]
        self.assertEqual((b.bsw, b.bsw_signal), ("capture.freq_mhz", "PWM_IN"))
        config = self.generated()
        self.assertIn("#include <bsp/capture/InputCapture.h>", config)
        self.assertEqual(config.count("::bios::InputCapture::update();"), 1)
        self.assertIn("::bios::InputCapture::Measurement const capture_PWM_IN\n"
                      "            = ::bios::InputCapture::get(::bios::capture::Ci_PWM_IN);", config)
        self.assertIn("Rte_Buf_DemoSwc_MotorNtc_mV_MotorNtc_mV = static_cast<uint32>("
                      "capture_PWM_IN.frequency_mhz);", config)
        self.assertIn("::bios::InputCapture::init();", config)   # the RTE is the only user of the measurement
        self.assertIn('"capture.PWM_IN.freq_mhz"', config)       # console "rte signals"

    def test_every_value_of_a_channel(self):
        fields = {"freq_mhz": "frequency_mhz", "period_us": "period_us", "duty_permille": "duty_permille",
                  "qlf": "quality"}
        for value, field in fields.items():
            with self.subTest(value=value):
                self.run_with(self.ecu(**{CAPTURE_PORT: f"capture.PWM_IN.{value}"}))
                self.assertIn(f"static_cast<uint32>(capture_PWM_IN.{field});", self.generated())

    def test_the_same_channel_feeds_several_ports(self):
        """Two ports read the measurement; one update and one get are enough for all of them."""
        ecu = self.ecu(**{CAPTURE_PORT: "capture.PWM_IN.duty_permille",
                          "DemoSwc.Button1.Button1": "dio.in.BUTTON1"})
        self.run_with(ecu)
        config = self.generated()
        self.assertEqual(config.count("::bios::InputCapture::get("), 1)

    def test_without_a_capture_connection_no_code(self):
        self.run_with(NO_CAN)
        config = self.generated()
        for absent in ("InputCapture", "bsp/capture"):
            self.assertNotIn(absent, config)

    def test_connection_errors(self):
        cases = [
            ({CAPTURE_PORT: "capture.PWM_IN"}, "expected capture.<Channel>.<value> with value one of freq_mhz"),
            ({CAPTURE_PORT: "capture.PWM_IN.duty"}, "unknown capture value duty (known: freq_mhz, period_us"),
            ({CAPTURE_PORT: "capture.PILOT_DUTY.freq_mhz"},
             "PILOT_DUTY is no input_capture channel of the project (use.input_capture: PWM_IN)"),   # RTE-18
            ({"DemoSwc.Button1.Button1": "capture.PWM_IN.freq_mhz"},
             "type boolean does not fit capture.PWM_IN.freq_mhz (uint32, milli-Hertz (Hz * 1000))"),
            ({PWM_PORT: "capture.PWM_IN.freq_mhz"},
             "output port cannot use capture.PWM_IN.freq_mhz (a measured value is an input)"),
        ]
        for connections, fragment in cases:
            with self.subTest(connections=connections):
                self.expect_error(self.ecu(**connections), fragment)

    def test_quality_is_the_signal_qualifier_of_com_03(self):
        """A component compares QLF_OK whether a value comes from CAN (COM-03) or from a capture input."""
        driver = (REPO / "platforms/s32k1xx/port/bspConfiguration/include/bsp/capture/InputCapture.h").read_text(
            encoding="utf-8")
        values = {name: int(value) for name, value in re.findall(r"QUALITY_(\w+)\s*=\s*(\d+)U;", driver)}
        for qlf, quality in rtegen.CAPTURE_QUALITY.items():
            self.assertEqual(values[quality[len("QUALITY_"):]], rtegen.QLF[qlf], f"{quality} != QLF_{qlf}")
        self.assertEqual(set(values), {q[len("QUALITY_"):] for q in rtegen.CAPTURE_QUALITY.values()})

    def test_missing_qualifier_is_a_warning(self):
        ecu = self.ecu(**{CAPTURE_PORT: "capture.PWM_IN.freq_mhz"})
        _, buffers, _ = self.run_with(ecu)
        self.assertEqual(rtegen.capture_warnings(buffers),
                         ["connections: capture.PWM_IN.qlf is not connected, so a component cannot tell a "
                          "measured value from a held one (missing signal, value out of range): connect it to a "
                          "required port"])
        _, with_qlf, _ = self.run_with(self.ecu(**{CAPTURE_PORT: "capture.PWM_IN.qlf"}))
        self.assertEqual(rtegen.capture_warnings(with_qlf), [])
        self.assertEqual(rtegen.capture_warnings({}), [])

    def test_values_of_one_channel_are_read_in_one_task(self):
        """One update serves every channel, so two tasks must not poll the measurement against each other."""
        ecu = copy.deepcopy(MULTI_ECU)
        ecu["connections"]["AppSwc.Speed.Speed"] = "capture.PWM_IN.freq_mhz"        # task app
        path = self.tmp / "rte.yaml"
        path.write_text(yaml.safe_dump(ecu, sort_keys=False), encoding="utf-8")
        rtegen.generate(path, PROJECT, self.tmp / "out", REPO, MULTI_TASKS)
        config = self.generated()
        self.assertIn("// ---- task app", config.split("::bios::InputCapture::update();")[0])
        ecu["connections"]["MotorSwc.Setpoint.Setpoint"] = "capture.PWM_IN.duty_permille"  # task motor
        path.write_text(yaml.safe_dump(ecu, sort_keys=False), encoding="utf-8")
        with self.assertRaises(rtegen.RteError) as ctx:
            rtegen.generate(path, PROJECT, self.tmp / "out", REPO, MULTI_TASKS)
        self.assertIn("the capture values are read in several tasks (app, motor)", str(ctx.exception))


def empty_rte(folder: Path) -> Path:
    """TOOL-114: the RTE is required - a copy of a project on a real board of boards/ is built, so nxconfig refuses
    it without project.rte. Tests that hand rtegen their own ECU description give the copy the smallest one."""
    folder.mkdir(parents=True, exist_ok=True)
    file = folder / "empty_rte.yaml"
    file.write_text("schema_version: 2\ntasks: [{ name: swc, period_ms: 10, priority: 30, stack_bytes: 2048 }]\n"
                    "components: []\n", encoding="utf-8")
    return file


def hall_project(tmp: Path):
    """The demo project with its hall device HALL1 switched on (IO-07), loaded from a copy in `tmp`."""
    doc = yaml.safe_load(PROJECT_FILE.read_text(encoding="utf-8"))
    doc["project"]["board"] = BOARD_DIR.as_posix()
    doc["project"]["rte"] = empty_rte(tmp / "hall_project").as_posix()      # TOOL-114
    doc["project"].pop("diag", None)   # relative to the demo folder (ARCH-51)
    doc["modules"]["HALL1"] = {"arrangement": "deg120", "pole_pairs": 4, "min_speed_rpm": 60,
                               "max_speed_rpm": 6000}
    doc["interrupts"]["ranks"] += [{"source": "dio", "name": n, "rank": 1} for n in ("HALL_UX", "HALL_V", "HALL_WY")]
    path = tmp / "hall_project" / "project.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return rtegen.nxconfig.load(path)


class RteHallTest(unittest.TestCase):
    """IO-07: hall.<Sensor>.<value> - state, sector, periods, speed, direction and quality of a Hall sensor set."""

    setUp, tearDown = RteGenTest.setUp, RteGenTest.tearDown

    def ecu(self, **connections):
        ecu = copy.deepcopy(NO_CAN)
        ecu["connections"].update(connections)
        return ecu

    def run_with(self, ecu, project=None):
        path = self.tmp / "rte.yaml"
        path.write_text(yaml.safe_dump(ecu), encoding="utf-8")
        return rtegen.generate(path, project or hall_project(self.tmp), self.tmp / "out", REPO)

    def expect_error(self, ecu, fragment, project=None):
        with self.assertRaises(rtegen.RteError) as ctx:
            self.run_with(ecu, project)
        self.assertIn(fragment, str(ctx.exception))

    def generated(self):
        return (self.tmp / "out" / "RteConfig.cpp").read_text(encoding="utf-8")

    def test_sensors_are_the_instantiated_devices(self):
        # the demo project describes HALL1 on the board but does not use it: no target
        self.assertEqual(rtegen.board_signals(PROJECT)["hall"], set())
        self.assertEqual(rtegen.board_signals(hall_project(self.tmp))["hall"], {"HALL1"})

    def test_reading_and_init(self):
        _, buffers, _ = self.run_with(self.ecu(**{CAPTURE_PORT: "hall.HALL1.speed_rpm"}))
        b = buffers[CAPTURE_PORT]
        self.assertEqual((b.bsw, b.bsw_signal), ("hall.speed_rpm", "HALL1"))
        config = self.generated()
        self.assertIn("#include <hall/HallSensors.h>", config)
        self.assertIn("::bios::HallSensors::Reading const hall_HALL1\n"
                      "            = ::bios::HallSensors::get(::bios::hall::Hi_HALL1);", config)
        self.assertIn("Rte_Buf_DemoSwc_MotorNtc_mV_MotorNtc_mV = static_cast<uint32>(hall_HALL1.speed_rpm);",
                      config)
        self.assertIn("::bsp::startPinInterrupts();", config)   # pulls the port object with the handlers
        self.assertIn("#include <bsp/pinIrq/PinIrqConfig.h>", config)
        self.assertNotIn("cyclePinEvents", config)                # no pin events in this project
        self.assertNotIn("update()", config.split("HallSensors")[1][:200])   # no polling cycle

    def test_every_value_of_a_sensor(self):
        fields = {"state": "state", "sector": "sector", "period_us": "period_us", "mech_period_us": "mech_period_us",
                  "speed_rpm": "speed_rpm", "qlf": "quality"}
        for value, field in fields.items():
            with self.subTest(value=value):
                self.run_with(self.ecu(**{CAPTURE_PORT: f"hall.HALL1.{value}"}))
                self.assertIn(f"static_cast<uint32>(hall_HALL1.{field});", self.generated())

    def test_without_a_hall_connection_no_code(self):
        self.run_with(NO_CAN)
        for absent in ("HallSensors", "hall/"):
            self.assertNotIn(absent, self.generated())

    def test_connection_errors(self):
        cases = [
            ({CAPTURE_PORT: "hall.HALL1"}, "expected hall.<Sensor>.<value> with value one of state"),
            ({CAPTURE_PORT: "hall.HALL1.rpm"}, "unknown hall value rpm (known: state, sector"),
            ({CAPTURE_PORT: "hall.HALL2.speed_rpm"}, "HALL2 is no hall sensor of the project"),
            ({CAPTURE_PORT: "hall.HALL1.direction"}, "type uint32 does not fit hall.HALL1.direction (sint16, sint32, "
                                                     "sint8"),
            ({PWM_PORT: "hall.HALL1.speed_rpm"}, "output port cannot use hall.HALL1.speed_rpm"),
        ]
        for connections, fragment in cases:
            with self.subTest(connections=connections):
                self.expect_error(self.ecu(**connections), fragment)
        # the demo project does not instantiate HALL1: not a target there
        self.expect_error(self.ecu(**{CAPTURE_PORT: "hall.HALL1.speed_rpm"}),
                          "HALL1 is no hall sensor of the project", PROJECT)

    def test_quality_is_the_signal_qualifier_of_com_03(self):
        driver = (REPO / "libs/bsw/hallDecoder/include/hallDecoder/HallDecoder.h").read_text(encoding="utf-8")
        values = {name: int(value) for name, value in re.findall(r"QUALITY_(\w+)\s*=\s*(\d+)U;", driver)}
        for qlf, quality in rtegen.HALL_QUALITY.items():
            self.assertEqual(values[quality[len("QUALITY_"):]], rtegen.QLF[qlf], f"{quality} != QLF_{qlf}")
        self.assertEqual(set(values), {q[len("QUALITY_"):] for q in rtegen.HALL_QUALITY.values()})

    def test_missing_qualifier_is_a_warning(self):
        _, buffers, _ = self.run_with(self.ecu(**{CAPTURE_PORT: "hall.HALL1.speed_rpm"}))
        self.assertEqual(len(rtegen.hall_warnings(buffers)), 1)
        self.assertIn("hall.HALL1.qlf is not connected", rtegen.hall_warnings(buffers)[0])
        _, with_qlf, _ = self.run_with(self.ecu(**{CAPTURE_PORT: "hall.HALL1.qlf"}))
        self.assertEqual(rtegen.hall_warnings(with_qlf), [])

    def test_values_may_be_read_in_several_tasks(self):
        """Unlike the polled capture: get() needs no cycle and locks itself, so two tasks may read one sensor."""
        ecu = copy.deepcopy(MULTI_ECU)
        ecu["connections"]["AppSwc.Speed.Speed"] = "hall.HALL1.speed_rpm"          # task app
        ecu["connections"]["MotorSwc.Setpoint.Setpoint"] = "hall.HALL1.qlf"        # task motor
        path = self.tmp / "rte.yaml"
        path.write_text(yaml.safe_dump(ecu, sort_keys=False), encoding="utf-8")
        rtegen.generate(path, hall_project(self.tmp), self.tmp / "out", REPO, MULTI_TASKS)
        config = self.generated()
        self.assertEqual(config.count("::bios::HallSensors::get(::bios::hall::Hi_HALL1)"), 2)
        self.assertEqual(config.count("::bsp::startPinInterrupts();"), 1)


def pin_event_project(tmp: Path, events: dict | None = None, hall: bool = False):
    """IO-54: copy of the demo project with pin events on its buttons (active low, PTA6/PTA7, vector PORTA)."""
    doc = yaml.safe_load(PROJECT_FILE.read_text(encoding="utf-8"))
    doc["project"]["board"] = BOARD_DIR.as_posix()
    doc["project"]["rte"] = empty_rte(tmp / "pin_event_project").as_posix()      # TOOL-114
    doc["project"].pop("diag", None)   # relative to the demo folder (ARCH-51)
    (doc.get("safety") or {}).pop("safe_state_output_shared_with_application", None)
    doc["pin_events"] = events if events is not None else {
        "BUTTON1": {"edge": "activate", "debounce_us": 2000, "max_events": 20, "window_ms": 100, "rearm_ms": 500},
        "BUTTON2": {"edge": "both", "debounce_us": 0, "max_events": 50, "window_ms": 100, "rearm_ms": 200}}
    doc["interrupts"]["ranks"] += [{"source": "dio", "name": n, "rank": 2} for n in doc["pin_events"]]
    if hall:
        doc["modules"]["HALL1"] = {"arrangement": "deg120", "pole_pairs": 4, "min_speed_rpm": 60,
                                   "max_speed_rpm": 6000}
        doc["interrupts"]["ranks"] += [{"source": "dio", "name": n, "rank": 2}
                                       for n in ("HALL_UX", "HALL_V", "HALL_WY")]
    path = tmp / "pin_event_project" / "project.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return rtegen.nxconfig.load(path)


class RtePinEventTest(unittest.TestCase):
    """IO-54: pinevent.<Input>.count/.time_us/.qlf - events on digital inputs (libs/bsp/bspPinEvent)."""

    setUp, tearDown = RteGenTest.setUp, RteGenTest.tearDown

    def ecu(self, **connections):
        ecu = copy.deepcopy(NO_CAN)
        ecu["connections"].update(connections)
        return ecu

    def run_with(self, ecu, project=None):
        path = self.tmp / "rte.yaml"
        path.write_text(yaml.safe_dump(ecu), encoding="utf-8")
        return rtegen.generate(path, project or pin_event_project(self.tmp), self.tmp / "out", REPO)

    def expect_error(self, ecu, fragment, project=None):
        with self.assertRaises(rtegen.RteError) as ctx:
            self.run_with(ecu, project)
        self.assertIn(fragment, str(ctx.exception))

    def generated(self):
        return (self.tmp / "out" / "RteConfig.cpp").read_text(encoding="utf-8")

    def test_inputs_are_the_pin_events_of_the_project(self):
        self.assertEqual(rtegen.board_signals(PROJECT)["pinevent"], set())
        self.assertEqual(rtegen.board_signals(pin_event_project(self.tmp))["pinevent"], {"BUTTON1", "BUTTON2"})

    def test_reading_start_and_cycle(self):
        _, buffers, _ = self.run_with(self.ecu(**{CAPTURE_PORT: "pinevent.BUTTON1.count",
                                                  QLF_PORT: "pinevent.BUTTON1.qlf"}))
        b = buffers[CAPTURE_PORT]
        self.assertEqual((b.bsw, b.bsw_signal, b.bsw_name), ("pinevent.count", "BUTTON1", "BUTTON1"))
        config = self.generated()
        self.assertIn("#include <bsp/pinIrq/PinIrqConfig.h>", config)
        self.assertNotIn("HallSensors", config)
        # one read per input and cycle; the buffers are only written when the copy is consistent (else: hold)
        self.assertEqual(config.count("::bsp::pinEvents().read(::pinevent::project::Pe_BUTTON1, pinevent_BUTTON1)"),
                         1)
        block = config.split("if (::bsp::pinEvents().read(::pinevent::project::Pe_BUTTON1, pinevent_BUTTON1))")[1]
        block = block.split("}")[0]
        self.assertIn("Rte_Buf_DemoSwc_MotorNtc_mV_MotorNtc_mV = static_cast<uint32>(pinevent_BUTTON1.count);",
                      block)
        self.assertIn("Rte_Buf_DemoSwc_PcCommandQlf_PcCommandQlf = static_cast<uint8>(pinevent_BUTTON1.quality);",
                      block)
        # the RTE starts the pin interrupts once and cycles the events in the task that reads them
        self.assertEqual(config.count("::bsp::startPinInterrupts();"), 1)
        self.assertEqual(config.count("::bsp::cyclePinEvents();"), 1)
        self.assertLess(config.index("::bsp::cyclePinEvents();"), config.index("::bsp::pinEvents().read("))

    def test_every_value(self):
        fields = {"count": ("count", CAPTURE_PORT, "uint32"), "time_us": ("timeUs", CAPTURE_PORT, "uint32"),
                  "qlf": ("quality", QLF_PORT, "uint8")}
        for value, (field_name, port, ctype) in fields.items():
            with self.subTest(value=value):
                self.run_with(self.ecu(**{port: f"pinevent.BUTTON2.{value}"}))
                self.assertIn(f"static_cast<{ctype}>(pinevent_BUTTON2.{field_name});", self.generated())

    def test_events_without_a_connection_are_started_and_cycled(self):
        """The project decides the events: counted (console "pinevent") even if no component reads them."""
        self.run_with(NO_CAN)
        config = self.generated()
        self.assertEqual(config.count("::bsp::startPinInterrupts();"), 1)
        self.assertEqual(config.count("::bsp::cyclePinEvents();"), 1)
        self.assertIn("// ---- task swc", config.split("::bsp::cyclePinEvents();")[0])
        self.assertNotIn("pinEvents().read", config)

    def test_without_pin_events_no_code(self):
        self.run_with(NO_CAN, PROJECT)
        config = self.generated()
        for absent in ("pinIrq", "PinEvent", "pinEvent", "startPinInterrupts", "cyclePinEvents"):
            self.assertNotIn(absent, config)

    def test_with_hall_the_start_serves_both(self):
        self.run_with(self.ecu(**{CAPTURE_PORT: "hall.HALL1.speed_rpm", QLF_PORT: "pinevent.BUTTON1.qlf"}),
                      pin_event_project(self.tmp, hall=True))
        config = self.generated()
        self.assertEqual(config.count("::bsp::startPinInterrupts();"), 1)
        self.assertEqual(config.count("#include <bsp/pinIrq/PinIrqConfig.h>"), 1)
        self.assertIn("#include <hall/HallSensors.h>", config)

    def test_connection_errors(self):
        cases = [
            ({CAPTURE_PORT: "pinevent.BUTTON1"}, "expected pinevent.<Input>.<value> with value one of count"),
            ({CAPTURE_PORT: "pinevent.BUTTON1.edges"}, "unknown pinevent value edges (known: count, time_us, qlf)"),
            ({CAPTURE_PORT: "pinevent.HALL_UX.count"},
             "HALL_UX is no pin event of the project (project.yaml pin_events: BUTTON1, BUTTON2)"),
            ({BUTTON_PORT: "pinevent.BUTTON1.count"},
             "type boolean does not fit pinevent.BUTTON1.count (uint32, accepted edges"),
            ({PWM_PORT: "pinevent.BUTTON1.count"}, "output port cannot use pinevent.BUTTON1.count"),
        ]
        for connections, fragment in cases:
            with self.subTest(connections=connections):
                self.expect_error(self.ecu(**connections), fragment)
        # the demo project has no pin events: not a target there
        self.expect_error(self.ecu(**{CAPTURE_PORT: "pinevent.BUTTON1.count"}),
                          "BUTTON1 is no pin event of the project (project.yaml pin_events: none)", PROJECT)

    def test_quality_is_the_signal_qualifier_of_com_03(self):
        """QUALITY_* of the module against QLF_* of the RTE, both ways (the same requirement as hall and capture)."""
        module = (REPO / "libs/bsp/bspPinEvent/include/pinEvent/PinEvent.h").read_text(encoding="utf-8")
        values = {name: int(value) for name, value in re.findall(r"QUALITY_(\w+)\s*=\s*(\d+)U;", module)}
        for qlf, quality in rtegen.PINEVENT_QUALITY.items():
            self.assertEqual(values[quality[len("QUALITY_"):]], rtegen.QLF[qlf], f"{quality} != QLF_{qlf}")
        self.assertEqual(set(values), {q[len("QUALITY_"):] for q in rtegen.PINEVENT_QUALITY.values()})

    def test_missing_qualifier_is_a_warning(self):
        _, buffers, _ = self.run_with(self.ecu(**{CAPTURE_PORT: "pinevent.BUTTON1.count"}))
        (warning,) = rtegen.pinevent_warnings(buffers)
        self.assertIn("pinevent.BUTTON1.qlf is not connected", warning)
        _, with_qlf, _ = self.run_with(self.ecu(**{QLF_PORT: "pinevent.BUTTON1.qlf"}))
        self.assertEqual(rtegen.pinevent_warnings(with_qlf), [])

    def test_values_may_be_read_in_several_tasks_and_cycle_once(self):
        """read() is lock free and consistent: two tasks may read one input; the cycle runs in one task only."""
        ecu = copy.deepcopy(MULTI_ECU)
        ecu["connections"]["AppSwc.Speed.Speed"] = "pinevent.BUTTON1.count"          # task app
        ecu["connections"]["MotorSwc.Setpoint.Setpoint"] = "pinevent.BUTTON1.qlf"    # task motor
        path = self.tmp / "rte.yaml"
        path.write_text(yaml.safe_dump(ecu, sort_keys=False), encoding="utf-8")
        rtegen.generate(path, pin_event_project(self.tmp), self.tmp / "out", REPO, MULTI_TASKS)
        config = self.generated()
        self.assertEqual(config.count("::bsp::pinEvents().read(::pinevent::project::Pe_BUTTON1"), 2)
        self.assertEqual(config.count("::bsp::cyclePinEvents();"), 1)
        self.assertEqual(config.count("::bsp::startPinInterrupts();"), 1)


BUTTON_PORT = "DemoSwc.Button1.Button1"              # boolean required port
QLF_PORT = "DemoSwc.PcCommandQlf.PcCommandQlf"         # uint8 required port


class RteGateDriverTest(unittest.TestCase):
    """IO-25: gd.<Device>.faults/enabled/qlf - status of a TMC6200 gate driver, inputs only (no switch-on target)."""

    setUp, tearDown = RteGenTest.setUp, RteGenTest.tearDown
    ecu = RteHallTest.ecu

    def run_with(self, ecu, project=None, tasks=None):
        path = self.tmp / "rte.yaml"
        path.write_text(yaml.safe_dump(ecu, sort_keys=False), encoding="utf-8")
        return rtegen.generate(path, project or PROJECT, self.tmp / "out", REPO, tasks)

    def expect_error(self, ecu, fragment, project=None):
        with self.assertRaises(rtegen.RteError) as ctx:
            self.run_with(ecu, project)
        self.assertIn(fragment, str(ctx.exception))

    def generated(self):
        return (self.tmp / "out" / "RteConfig.cpp").read_text(encoding="utf-8")

    def test_devices_are_the_instantiated_gate_drivers(self):
        self.assertEqual(rtegen.board_signals(PROJECT)["gd"], {"MOT1_GD"})
        self.assertEqual(rtegen.board_signals(PROJECT_TWO_BUSES)["gd"], set())   # S32K148 profile: no gate driver

    def test_reading_of_all_values(self):
        _, buffers, _ = self.run_with(self.ecu(**{CAPTURE_PORT: "gd.MOT1_GD.faults", BUTTON_PORT: "gd.MOT1_GD.enabled",
                                                  QLF_PORT: "gd.MOT1_GD.qlf"}))
        self.assertEqual((buffers[CAPTURE_PORT].bsw, buffers[CAPTURE_PORT].bsw_signal), ("gd.faults", "MOT1_GD"))
        self.assertEqual(rtegen.gd_warnings(buffers), [])
        config = self.generated()
        self.assertIn("void readGateDriver_MOT1_GD(uint16_t& faults, bool& enabled, uint8_t& health);", config)
        self.assertEqual(config.count("::integration::rte::readGateDriver_MOT1_GD("), 1)   # one reading per cycle
        self.assertIn("uint8_t const gd_MOT1_GD_health_qlf = (gd_MOT1_GD_health == 1U)   ? QLF_OK\n"
                      "                                                 : (gd_MOT1_GD_health == 2U) ? QLF_ERROR\n"
                      "                                                                                  : QLF_DEBOUNCE;",
                      config)
        # SYS-29: the supervisor's verdict on the bus of the device (MOT1_GD_SPI, second used SPI bus) on top
        self.assertIn("uint8_t const gd_MOT1_GD_quality\n"
                      "            = ::safety::device::rte::busLost(0x21U) ? QLF_ERROR : gd_MOT1_GD_health_qlf;", config)
        self.assertEqual(config.count("bool busLost(uint8_t bus);"), 1)
        self.assertIn("Rte_Buf_DemoSwc_MotorNtc_mV_MotorNtc_mV = static_cast<uint32>(gd_MOT1_GD_faults);", config)
        self.assertIn("Rte_Buf_DemoSwc_Button1_Button1 = gd_MOT1_GD_enabled ? 1U : 0U;", config)
        self.assertIn("Rte_Buf_DemoSwc_PcCommandQlf_PcCommandQlf = static_cast<uint8>(gd_MOT1_GD_quality);", config)
        # the RTE only reads: no call into the driver that could switch DRV_EN
        for absent in ("enable(", "Tmc6200", "GateDriverSystem", "DRV_EN,", "::bios::Output::MOT1_GD"):
            self.assertNotIn(absent, config)

    def test_health_codes_match_the_integration(self):
        integration = (REPO / "platforms/s32k1xx/port/main/src/integration/Integration.cpp").read_text(encoding="utf-8")
        self.assertIn("void readGateDriver_##device(uint16_t& faults, bool& enabled, uint8_t& health)", integration)
        codes = dict(re.findall(r"State::(\w+)\)\s*\?\s*uint8_t\{(\d)U\}", integration))
        self.assertEqual(codes, {"FAILED": "2", "UNINITIALIZED": "0"})
        self.assertIn(": uint8_t{1U};", integration)
        self.assertEqual(rtegen.GD_HEALTH, {0: "QLF_DEBOUNCE", 1: "QLF_OK", 2: "QLF_ERROR"})

    def test_without_a_gate_driver_connection_no_code(self):
        self.run_with(NO_CAN)
        self.assertNotIn("readGateDriver", self.generated())
        self.assertNotIn("busLost", self.generated())

    # --- SYS-29 (gap G5, finding F5): qualifier path of device values with the supervisor's bus verdict

    def test_bus_ids_follow_the_used_buses_in_board_order(self):
        integration = PROJECT.integration
        self.assertEqual(integration.spi, ["SBC_SPI", "MOT1_GD_SPI"])
        self.assertEqual(rtegen.device_bus_ids(PROJECT), {"SBC": 0x20, "MOT1_GD": 0x21, "DIAG_PHY": 0xFF,
                                                               "KL30B_SW": 0xFF})     # DIAG_PHY: CAN1 (COM-05)
        # a board with CAN transceivers but no SPI device: no device has a bus verdict
        self.assertEqual(set(rtegen.device_bus_ids(PROJECT_TWO_BUSES).values()), {rtegen.DEVICE_NO_BUS})
        self.assertEqual(rtegen.device_bus_ids(None), {})
        self.assertEqual(rtegen.device_bus_id("spi", 0), 0x20)
        self.assertEqual(rtegen.device_bus_id("can", 31), 0x9F)
        self.assertEqual(rtegen.device_bus_id("spi", 32), rtegen.DEVICE_NO_BUS)
        self.assertEqual(rtegen.device_bus_id("usb", 0), rtegen.DEVICE_NO_BUS)

    def test_bus_encoding_matches_the_supervisor(self):
        header = (REPO / "ecu/safety/safeSupervisor/include/safeSupervisor/DeviceReport.h").read_text(encoding="utf-8")
        kinds = {name.lower(): int(value) for name, value in re.findall(r"^\s+(\w+)\s*=\s*(\d+)U,?", header, re.M)
                 if name in ("SPI", "I2C", "UART", "CAN", "OTHER")}
        self.assertEqual(kinds, rtegen.DEVICE_BUS_KINDS)
        self.assertIn("constexpr uint8_t NO_BUS = 0xFFU;", header)
        self.assertIn(f"constexpr uint8_t BUS_INDEX_MAX = {rtegen.DEVICE_BUS_INDEX_MAX}U;", header)
        self.assertIn("(static_cast<uint32_t>(kind) << 5U) | index", header)
        reader = (REPO / "ecu/safety/safeSupervisor/src/safeSupervisor/DeviceReportRte.cpp").read_text(encoding="utf-8")
        self.assertIn("namespace safety\n{\nnamespace device\n{\nnamespace rte\n{\nbool busLost(uint8_t const bus)", reader)

    def test_device_without_a_single_bus_keeps_the_driver_health(self):
        with mock.patch.object(rtegen, "device_bus_ids", return_value={"MOT1_GD": rtegen.DEVICE_NO_BUS}):
            self.run_with(self.ecu(**{QLF_PORT: "gd.MOT1_GD.qlf"}))
        config = self.generated()
        self.assertIn("uint8_t const gd_MOT1_GD_quality = gd_MOT1_GD_health_qlf;", config)
        self.assertNotIn("busLost", config)

    def test_connection_errors(self):
        cases = [
            ({CAPTURE_PORT: "gd.MOT1_GD"}, "expected gd.<Device>.<value> with value one of faults, enabled, qlf"),
            ({BUTTON_PORT: "gd.MOT1_GD.enable"}, "unknown gd value enable (known: faults, enabled, qlf; there is no "
                                                 "value that switches a gate driver on)"),
            ({CAPTURE_PORT: "gd.MOT2_GD.faults"}, "MOT2_GD is no gate driver of the project"),
            ({CAPTURE_PORT: "gd.MOT1_GD.enabled"}, "type uint32 does not fit gd.MOT1_GD.enabled (boolean"),
            ({BUTTON_PORT: "gd.MOT1_GD.faults"}, "type boolean does not fit gd.MOT1_GD.faults (uint16, uint32"),
            ({PWM_PORT: "gd.MOT1_GD.enabled"}, "output port cannot use gd.MOT1_GD.enabled (the gate driver status is "
                                               "an input; the RTE does not switch a gate driver)"),
            # the pins of a module instance are no RTE outputs: DRV_EN only through the driver
            ({"DemoSwc.Led1.Led1": "dio.out.MOT1_GD_DRV_EN"},
             "MOT1_GD_DRV_EN is driven by the module instance MOT1_GD (gdTmc6200, drv_en), the RTE must not write it"),
            ({"DemoSwc.Led1.Led1": "dio.out.EN_SW_KL30B"},
             "EN_SW_KL30B is driven by the module instance KL30B_SW (highSideSwitch, in)"),
        ]
        for connections, fragment in cases:
            with self.subTest(connections=connections):
                self.expect_error(self.ecu(**connections), fragment)
        self.assertNotIn("MOT1_GD_DRV_EN", rtegen.board_signals(PROJECT)["dio.out"])
        self.assertIn("LED1", rtegen.board_signals(PROJECT)["dio.out"])
        self.expect_error(self.ecu(**{CAPTURE_PORT: "gd.MOT1_GD.faults"}), "MOT1_GD is no gate driver of the project "
                          "(board devices with module gdTmc6200 on a used SPI bus: none)", PROJECT_TWO_BUSES)

    def test_missing_qualifier_is_a_warning(self):
        _, buffers, _ = self.run_with(self.ecu(**{CAPTURE_PORT: "gd.MOT1_GD.faults"}))
        self.assertEqual(len(rtegen.gd_warnings(buffers)), 1)
        self.assertIn("gd.MOT1_GD.qlf is not connected", rtegen.gd_warnings(buffers)[0])

    def test_values_may_be_read_in_several_tasks(self):
        ecu = copy.deepcopy(MULTI_ECU)
        ecu["connections"]["AppSwc.Speed.Speed"] = "gd.MOT1_GD.faults"          # task app
        ecu["connections"]["MotorSwc.Setpoint.Setpoint"] = "gd.MOT1_GD.qlf"      # task motor
        self.run_with(ecu, tasks=MULTI_TASKS)
        config = self.generated()
        self.assertEqual(config.count("::integration::rte::readGateDriver_MOT1_GD("), 2)
        self.assertEqual(config.count("void readGateDriver_MOT1_GD("), 1)


class RteCaptureHostTest(unittest.TestCase):
    """Compiles the generated RTE with a capture target against a stub driver and runs test/capture_host."""

    def test_capture_runtime(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            ecu = copy.deepcopy(NO_CAN)
            ecu["connections"][CAPTURE_PORT] = "capture.PWM_IN.freq_mhz"
            ecu_file = tmp / "rte.yaml"
            ecu_file.write_text(yaml.safe_dump(ecu), encoding="utf-8")
            out = tmp / "gen"
            rtegen.generate(ecu_file, PROJECT, out, REPO)
            host = HERE / "test"
            zig = [sys.executable, "-m", "ziglang"]
            includes = [f"-I{out}", f"-I{out / 'tasks'}", f"-I{REPO / 'libs/bsw/autosarBase/include'}",
                        f"-I{host / 'capture_host' / 'stubs'}", f"-I{host / 'pwm_host' / 'stubs'}",
                        f"-I{host / 'can_host' / 'stubs'}", f"-I{REPO / 'swc/DemoSwc/generated/src'}"]
            objects = []
            for src in (out / "Rte.c", REPO / "swc/DemoSwc/generated/src/DemoSwc.c"):
                obj = tmp / (src.stem + ".o")
                r = subprocess.run(zig + ["cc", "-std=c99", "-O1", "-Wall", "-Wextra", "-Werror", *includes, "-c",
                                          str(src), "-o", str(obj)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr[-3000:])
                objects.append(str(obj))
            exe = tmp / "rte_capture_host_test.exe"
            r = subprocess.run(zig + ["c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror", *includes,
                                      str(out / "RteConfig.cpp"),
                                      str(host / "capture_host" / "rte_capture_host_test.cpp"), *objects,
                                      "-o", str(exe)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            r = subprocess.run([str(exe)], capture_output=True, text=True)
            print("\n" + r.stdout.strip(), file=sys.stderr)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def xcp_of(out: Path) -> tuple[str, int, int] | None:
    """(bus, master id, slave id) of a generated xcp/xcpCanConfiguration.h, None for NX_XCP_MISSING."""
    text = (out / "xcp" / "xcpCanConfiguration.h").read_text(encoding="utf-8")
    if "#define NX_XCP_MISSING 1" in text:
        return None
    values = dict(re.findall(r"^#define (NX_XCP_\w+) +(\S+)", text, re.M))
    return (values["NX_XCP_BUS"], int(values["NX_XCP_CAN_ID_MASTER"].rstrip("UL"), 16),
            int(values["NX_XCP_CAN_ID_SLAVE"].rstrip("UL"), 16))


def rx_filters_of(out: Path) -> list[tuple[str, int, int, int, bool]]:
    """(bus, buffer, id, mask, extended) of every entry of a generated can/canRxFilterConfiguration.h."""
    text = (out / "can" / "canRxFilterConfiguration.h").read_text(encoding="utf-8")
    entries = re.findall(r"X\((\w+), (\d+)U, (0x[0-9A-F]+)UL, (0x[0-9A-F]+)UL, (true|false)\)", text)
    return [(bus, int(buffer), int(ident, 16), int(mask, 16), ext == "true") for bus, buffer, ident, mask, ext in entries]


def command_id_in_filter(xcp: tuple[str, int, int], filters) -> bool:
    """The rule of ecu/xcp XcpSystem.cpp commandIdInReceiveFilter(): a filter of the XCP bus accepts the id."""
    bus, master, _ = xcp
    extended, raw = bool(master & 0x80000000), master & 0x1FFFFFFF
    return any(b == bus and e == extended and (raw & mask) == (ident & mask) for b, _, ident, mask, e in filters)


class RteCanTest(unittest.TestCase):
    """can.rx / can.tx connections: DBC lookup, direction, type and value range checks."""

    setUp, tearDown = RteGenTest.setUp, RteGenTest.tearDown
    run_with, expect_error = RteGenTest.run_with, RteGenTest.expect_error

    def can_ecu(self, **connections):
        dbc = self.tmp / "t.dbc"
        dbc.write_text(CAN_DBC, encoding="utf-8")
        ecu = copy.deepcopy(NO_CAN)
        ecu["can"] = {"dbc": str(dbc), "node": "EVB"}  # absolute path: repo / abs == abs
        ecu["connections"].update(connections)
        return ecu

    def test_bus_and_uds_bus_come_from_the_project(self):
        """ARCH-07: the buses are the ones the project uses, the UDS bus is configurable."""
        self.run_with(ECU)
        out = self.tmp / "out"
        self.assertIn("getCanTransceiver(::busid::CAN0)", (out / "RteConfig.cpp").read_text(encoding="utf-8"))
        uds = (out / "uds" / "udsBusConfiguration.h").read_text(encoding="utf-8")
        self.assertIn("#define NX_UDS_BUS      CAN0 // ::busid::CAN0\n", uds)
        self.assertIn('#define NX_UDS_BUS_NAME "CAN0"\n', uds)
        # two buses (build-test profile): signals and diagnostics can run on either of them
        buses = rtegen.can_buses(PROJECT_TWO_BUSES)
        self.assertEqual(buses, ["CAN0", "CAN1"])
        ecu = self.can_ecu()
        ecu["can"]["bus"] = "CAN1"
        can = rtegen.load_can(ecu, REPO, buses)
        self.assertEqual((can.bus, can.bus_ident), ("CAN1", "CAN1"))
        self.assertEqual(rtegen.load_uds(ecu, can, buses), "CAN1")              # default: the signal bus
        ecu["uds"] = {"bus": "CAN0"}
        self.assertEqual(rtegen.load_uds(ecu, can, buses), "CAN0")              # diagnostics on the other bus
        self.assertEqual(rtegen.load_uds({}, None, buses), "CAN0")              # no CAN section: the first bus
        self.assertIn("#define NX_UDS_BUS      CAN1", rtegen.render_uds_bus_config("CAN1", "rte.yaml"))
        self.assertIn("#define NX_UDS_BUS_MISSING 1", rtegen.render_uds_bus_config(None, "rte.yaml"))
        # the ECU description of the two-bus profile (no DBC): diagnostics on its second bus
        two_file = REPO / "projects/buildtest_s32k148_min/rte.yaml"
        two = rtegen.with_resolved_paths(yaml.safe_load(two_file.read_text(encoding="utf-8")), two_file)
        two["uds"] = {"bus": "CAN1"}
        path = self.tmp / "two.yaml"
        path.write_text(yaml.safe_dump(two), encoding="utf-8")
        rtegen.generate(path, PROJECT_TWO_BUSES, self.tmp / "two", REPO)
        uds = (self.tmp / "two" / "uds" / "udsBusConfiguration.h").read_text(encoding="utf-8")
        self.assertIn("#define NX_UDS_BUS      CAN1 // ::busid::CAN1\n", uds)

    def test_rx_filter_of_the_demo_project(self):
        """COM-08: received DBC messages, diagnostic request ids and the XCP command id (TOOL-26), repeated over all
        14 standard buffers."""
        self.run_with(ECU)
        header = (self.tmp / "out" / "can" / "canRxFilterConfiguration.h").read_text(encoding="utf-8")
        self.assertIn("#define NX_CAN_RX_FILTER_BUFFERS_STD_CAN0 14U\n", header)     # COM-26: per bus
        self.assertIn("#define NX_CAN_RX_FILTER_BUFFERS_EXT_CAN0 2U\n", header)
        self.assertIn("X(CAN0, 0U, 0x210UL, 0x7FFUL, false) /* PC_Command */ \\\n", header)
        self.assertIn("X(CAN0, 1U, 0x7DFUL, 0x7FFUL, false) /* diagnostics.functional_id */ \\\n", header)
        self.assertIn("X(CAN0, 2U, 0x7E0UL, 0x7FFUL, false) /* diagnostics.request_id */ \\\n", header)
        self.assertIn("X(CAN0, 3U, 0x7F0UL, 0x7FFUL, false) /* xcp.master_id */ \\\n", header)   # TOOL-26
        self.assertIn("X(CAN0, 13U, 0x7DFUL, 0x7FFUL, false) /* repeats buffer 1: diagnostics.functional_id */\n",
                      header)
        self.assertIn("// CAN0: 4 standard filters for 4 ids in 14 buffers, no extended id\n", header)   # COM-36
        self.assertEqual(len(re.findall(r"X\(CAN0, \d+U", header)), 14)
        self.assertNotIn("true)", header)                                   # no extended id, buffers 14/15 off
        # without CAN signals only the diagnostic ids remain
        self.run_with(NO_CAN)
        header = (self.tmp / "out" / "can" / "canRxFilterConfiguration.h").read_text(encoding="utf-8")
        self.assertNotIn("0x210UL", header)
        self.assertIn("0x7E0UL", header)

    def test_rx_filter_per_bus(self):
        """Diagnostics on the second bus of the two-bus profile: its ids go there, the first bus receives nothing."""
        buses = rtegen.can_buses(PROJECT_TWO_BUSES)
        diag = type("Diag", (), {"request_id": 0x6F1, "functional_id": 0x6F0})()
        config = type("Config", (), {"diagnostics": diag})()
        ids = rtegen.can_rx_ids(None, "CAN1", config, buses)
        self.assertEqual(ids, {"CAN0": [], "CAN1": [(0x6F1, False, "diagnostics.request_id"),
                                                     (0x6F0, False, "diagnostics.functional_id")]})
        header = rtegen.render_can_rx_filter_config({b: rtegen.can_rx_filters(i) for b, i in ids.items()}, "rte.yaml")
        self.assertIn("X(CAN1, 0U, 0x6F0UL, 0x7FFUL, false)", header)
        self.assertNotIn("X(CAN0", header)
        self.assertIn("#define NX_CAN_RX_FILTERS(X)\n", rtegen.render_can_rx_filter_config({"CAN0": []}, "r"))
        self.assertEqual(rtegen.can_rx_ids(None, None, None, buses), {"CAN0": [], "CAN1": []})

    def test_rx_filter_split_per_instance(self):
        """COM-26: a bus on a FlexCAN instance with 16 message buffers has 6 standard and 2 extended receive buffers."""
        # the two-bus profile (S32K148: FlexCAN0 and FlexCAN2 with 32 buffers each)
        self.assertEqual(rtegen.can_rx_splits(PROJECT_TWO_BUSES, rtegen.can_buses(PROJECT_TWO_BUSES)),
                         {"CAN0": (14, 2), "CAN1": (14, 2)})
        # the same buses on an S32K144 (FlexCAN2 with 16 buffers, mcudb), without loading a project nxconfig rejects
        instances = dict(PROJECT_TWO_BUSES.derivative.instances)
        instances["FlexCAN2"] = instances["FlexCAN2"].model_copy(update={"message_buffers": 16})
        config = type("Config", (), {"integration": PROJECT_TWO_BUSES.integration,
                                     "derivative": type("D", (), {"instances": instances})()})()
        buses = rtegen.can_buses(PROJECT_TWO_BUSES)
        splits = rtegen.can_rx_splits(config, buses)
        self.assertEqual(splits, {"CAN0": (14, 2), "CAN1": (6, 2)})
        diag = type("Diag", (), {"request_id": 0x7E0, "functional_id": 0x7DF})()
        ids = rtegen.can_rx_ids(None, "CAN1", type("C", (), {"diagnostics": diag})(), buses)
        ids["CAN1"].append((0x18DA00F1, True, "xcp.master_id"))
        filters = {b: rtegen.can_rx_filters(i, *splits[b]) for b, i in ids.items()}
        self.assertEqual([f.buffer for f in filters["CAN1"]], [0, 1, 2, 3, 4, 5, 6, 7])
        self.assertEqual([f.extended for f in filters["CAN1"]], [False] * 6 + [True] * 2)
        header = rtegen.render_can_rx_filter_config(filters, "rte.yaml", splits)
        self.assertIn("#define NX_CAN_RX_FILTER_BUFFERS_STD_CAN0 14U\n#define NX_CAN_RX_FILTER_BUFFERS_EXT_CAN0 2U\n"
                      "#define NX_CAN_RX_FILTER_BUFFERS_STD_CAN1 6U\n#define NX_CAN_RX_FILTER_BUFFERS_EXT_CAN1 2U\n",
                      header)
        self.assertIn("X(CAN1, 7U, 0x18DA00F1UL, 0x1FFFFFFFUL, true)", header)
        self.assertNotIn("X(CAN1, 8U", header)                              # buffer 8 transmits on this instance
        # stub configurations without instance data keep the split of 32 buffers
        self.assertEqual(rtegen.can_rx_splits(None, ["CAN0"]), {"CAN0": (14, 2)})

    def test_rx_filter_repeats_are_named_uds_on_can1(self):
        """COM-36, board 2026-09-22 round 3: with uds.bus CAN1 the table of CAN1 held 0x7DF/0x7E0/0x7F0 twice.

        Not a second registration of UDS or XCP: three ids, each once in can_rx_ids, repeated over the six standard
        buffers of a 16-buffer instance (queue, COM-08). The repeats name the buffer they repeat; the extended
        buffers 6/7 get the 29-bit XCP command id.
        """
        buses = rtegen.can_buses(PROJECT_TWO_BUSES)
        diag = type("Diag", (), {"request_id": 0x7E0, "functional_id": 0x7DF})()
        config = type("C", (), {"diagnostics": diag})()
        ids = rtegen.can_rx_ids(None, "CAN1", config, buses, rtegen.XcpCan("CAN1", 0x7F0, 0x7F1))
        self.assertEqual(sorted(v for v, _, _ in ids["CAN1"]), [0x7DF, 0x7E0, 0x7F0])      # each id once
        filters = rtegen.can_rx_filters(ids["CAN1"], 6, 2)
        self.assertEqual([(f.buffer, f.id, f.repeats) for f in filters],
                         [(0, 0x7DF, None), (1, 0x7E0, None), (2, 0x7F0, None),
                          (3, 0x7DF, 0), (4, 0x7E0, 1), (5, 0x7F0, 2)])
        header = rtegen.render_can_rx_filter_config({"CAN1": filters}, "rte.yaml", {"CAN1": (6, 2)})
        self.assertIn("// CAN1: 3 standard filters for 3 ids in 6 buffers, no extended id\n", header)
        self.assertIn("X(CAN1, 3U, 0x7DFUL, 0x7FFUL, false) /* repeats buffer 0: diagnostics.functional_id */", header)
        self.assertIn("X(CAN1, 2U, 0x7F0UL, 0x7FFUL, false) /* xcp.master_id */", header)
        # 29-bit XCP command id: standard ids repeat three times, the extended one fills buffers 6 and 7
        ids = rtegen.can_rx_ids(None, "CAN1", config, buses, rtegen.XcpCan("CAN1", 0x98DA00F1, 0x98DAF100))
        filters = rtegen.can_rx_filters(ids["CAN1"], 6, 2)
        self.assertEqual([(f.buffer, f.id, f.extended, f.repeats) for f in filters],
                         [(0, 0x7DF, False, None), (1, 0x7E0, False, None), (2, 0x7DF, False, 0),
                          (3, 0x7E0, False, 1), (4, 0x7DF, False, 0), (5, 0x7E0, False, 1),
                          (6, 0x18DA00F1, True, None), (7, 0x18DA00F1, True, 6)])
        header = rtegen.render_can_rx_filter_config({"CAN1": filters}, "rte.yaml", {"CAN1": (6, 2)})
        self.assertIn("// CAN1: 2 standard filters for 2 ids in 6 buffers, 1 extended filter for 1 id in 2 buffers\n",
                      header)
        self.assertIn("X(CAN1, 7U, 0x18DA00F1UL, 0x1FFFFFFFUL, true) /* repeats buffer 6: xcp.master_id */\n", header)
        # more ids than buffers: merged filters count every id they cover and do not repeat
        many = [(0x100 + i * 0x11, False, f"M{i}") for i in range(8)]
        filters = rtegen.can_rx_filters(many, 6, 2)
        self.assertEqual([f.repeats for f in filters], [None] * 6)
        self.assertIn("// CAN1: 6 standard filters for 8 ids in 6 buffers, no extended id",
                      rtegen.render_can_rx_filter_config({"CAN1": filters}, "r", {"CAN1": (6, 2)}))
        # a merge may leave fewer filters than buffers (0x100..0x103 in one filter): the rest repeats again
        filters = rtegen.can_rx_filters([(0x100 + i, False, f"M{i}") for i in range(8)], 6, 2)
        self.assertEqual([f.repeats for f in filters], [None] * 5 + [0])
        self.assertIn("// CAN1: 5 standard filters for 8 ids in 6 buffers, no extended id",
                      rtegen.render_can_rx_filter_config({"CAN1": filters}, "r", {"CAN1": (6, 2)}))

    def test_rx_filter_refuses_one_id_for_two_receivers(self):
        """COM-36: one id twice on a bus (XCP command id = diagnostic request id) is an error, not one filter."""
        buses = rtegen.can_buses(PROJECT_TWO_BUSES)
        diag = type("Diag", (), {"request_id": 0x7E0, "functional_id": 0x7DF})()
        config = type("C", (), {"diagnostics": diag})()
        with self.assertRaisesRegex(rtegen.RteError, r"receive filter of CAN1: xcp.master_id and "
                                                     r"diagnostics.request_id are both the 11-bit id 0x7E0"):
            rtegen.can_rx_ids(None, "CAN1", config, buses, rtegen.XcpCan("CAN1", 0x7E0, 0x7E8))
        # the same number as a 29-bit id is another identifier
        ids = rtegen.can_rx_ids(None, "CAN1", config, buses, rtegen.XcpCan("CAN1", 0x800007E0, 0x7E8))
        self.assertIn((0x7E0, True, "xcp.master_id"), ids["CAN1"])
        # on the other bus it is no collision either
        ids = rtegen.can_rx_ids(None, "CAN1", config, buses, rtegen.XcpCan("CAN0", 0x7E0, 0x7E8))
        self.assertEqual(ids["CAN0"], [(0x7E0, False, "xcp.master_id")])

    def test_xcp_bus_ids_and_rx_filter(self):
        """TOOL-26: project.yaml xcp: -> xcp/xcpCanConfiguration.h and the command id in the receive filter."""
        buses = rtegen.can_buses(PROJECT_TWO_BUSES)
        use = type("Xcp", (), {"bus": None, "master_id": 0x6A0, "slave_id": 0x6A1})()
        config = type("Config", (), {"project": type("P", (), {"xcp": use})(), "diagnostics": None})()
        xcp = rtegen.load_xcp(config, buses, "CAN1")                        # default: the diagnostic bus
        self.assertEqual(xcp, rtegen.XcpCan("CAN1", 0x6A0, 0x6A1))
        use.bus = "CAN0"
        self.assertEqual(rtegen.load_xcp(config, buses, "CAN1").bus, "CAN0")
        use.bus = "CAN7"
        with self.assertRaisesRegex(rtegen.RteError, r"xcp.bus: bus CAN7 is not a CAN bus of the project"):
            rtegen.load_xcp(config, buses, "CAN1")
        use.bus, use.master_id = None, 0x98DA00F1                           # 29-bit id on the diagnostic bus
        xcp = rtegen.load_xcp(config, buses, "CAN1")
        ids = rtegen.can_rx_ids(None, "CAN1", config, buses, xcp)
        self.assertEqual(ids, {"CAN0": [], "CAN1": [(0x18DA00F1, True, "xcp.master_id")]})
        filters = rtegen.can_rx_filters(ids["CAN1"])
        self.assertEqual([(f.buffer, f.id, f.extended) for f in filters], [(14, 0x18DA00F1, True),
                                                                          (15, 0x18DA00F1, True)])
        header = rtegen.render_xcp_config(xcp, "rte.yaml")
        self.assertIn("#define NX_XCP_BUS           CAN1 // ::busid::CAN1\n", header)
        self.assertIn('#define NX_XCP_BUS_NAME      "CAN1"\n', header)
        self.assertIn("#define NX_XCP_CAN_ID_MASTER 0x98DA00F1UL", header)
        self.assertIn("#define NX_XCP_CAN_ID_SLAVE  0x6A1UL", header)
        self.assertNotIn("NX_XCP_MISSING", header)
        self.assertIn("#define NX_XCP_MISSING 1", rtegen.render_xcp_config(None, "rte.yaml"))
        self.assertIsNone(rtegen.load_xcp(type("C", (), {"project": type("P", (), {"xcp": None})()})(), buses, None))
        self.assertIsNone(rtegen.load_xcp(None, buses, "CAN0"))
        with self.assertRaisesRegex(rtegen.RteError, "xcp: no bus"):
            rtegen.load_xcp(config, [], None)

    def test_xcp_on_the_second_bus_of_an_own_profile(self):
        """TOOL-26, own profile (two-bus build test, not the eval board): XCP with 29-bit ids on CAN1, UDS on CAN0.

        The whole generate: header, filter of CAN1 with the extended command id in buffers 14/15, nothing of XCP
        on CAN0, and the check of ecu/xcp (static_assert) repeated here on the generated text.
        """
        project = yaml.safe_load((REPO / "projects/buildtest_s32k148_min/project.yaml").read_text(encoding="utf-8"))
        project["project"]["board"] = (REPO / "boards" / "buildtest_s32k148").as_posix()
        project["project"]["rte"] = (REPO / "projects/buildtest_s32k148_min/rte.yaml").as_posix()
        project["xcp"] = {"bus": "CAN1", "master_id": 0x98DA00F1, "slave_id": 0x98DAF100}
        path = self.tmp / "project.yaml"
        path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        config = rtegen.nxconfig.load(path)
        out = self.tmp / "xcp2"
        rtegen.generate(REPO / "projects/buildtest_s32k148_min/rte.yaml", config, out, REPO)
        xcp = xcp_of(out)
        self.assertEqual(xcp, ("CAN1", 0x98DA00F1, 0x98DAF100))
        filters = rx_filters_of(out)
        self.assertEqual([f for f in filters if f[0] == "CAN1"],
                         [("CAN1", 14, 0x18DA00F1, 0x1FFFFFFF, True), ("CAN1", 15, 0x18DA00F1, 0x1FFFFFFF, True)])
        self.assertFalse(any(f[4] for f in filters if f[0] == "CAN0"))       # UDS on CAN0: standard ids only
        self.assertTrue(command_id_in_filter(xcp, filters))

    def test_xcp_command_id_is_in_the_rx_filter_of_every_project(self):
        """TOOL-26: board 2026-09-18 was deaf to XCP (command 0x7F0 not in the COM-08 filter of CAN0).

        For every project under projects/ with an xcp: section, the generated filter of the XCP bus must accept
        the command id - the same rule ecu/xcp checks with static_assert. Checked against itself: the filter of
        the demo project without the XCP id (the state of the TOOL-01 transition) does not accept 0x7F0.
        """
        checked = []
        # PLAT-62: the own test profile is checked with the projects, so the check runs in the open tree as well
        for project_file in [*sorted((REPO / "projects").glob("*/project.yaml")), PROJECT_FILE]:
            config = rtegen.nxconfig.load(project_file)
            if config.project.xcp is None or config.rte_file is None:
                continue
            out = self.tmp / project_file.parent.name
            rtegen.generate(config.rte_file, config, out, REPO)
            xcp = xcp_of(out)
            self.assertTrue(command_id_in_filter(xcp, rx_filters_of(out)),
                            f"{project_file.parent.name}: XCP command id 0x{xcp[1]:X} not in the filter of {xcp[0]}")
            checked.append(project_file.parent.name)
        self.assertIn("testproject_full", checked)
        if EVAL_PROJECT_FILE.is_file():                       # private tree: the eval board project as well
            self.assertIn("evalboard_s32k144_demo", checked)
        demo = self.tmp / "testproject_full"
        self.assertEqual(xcp_of(demo), ("CAN0", 0x7F0, 0x7F1))
        # mutation: the demo filter without the XCP section (the state of the TOOL-01 transition) fails the check
        self.assertFalse(command_id_in_filter(xcp_of(demo), rx_filters_of(self.demo_without_xcp())))

    def demo_without_xcp(self) -> Path:
        """Generate of the demo project with its xcp: section removed."""
        project = yaml.safe_load(PROJECT_FILE.read_text(encoding="utf-8"))
        project["project"]["board"] = BOARD_DIR.as_posix()
        project["project"]["rte"] = ECU_FILE.as_posix()
        project["project"].pop("diag", None)   # relative to the demo folder (ARCH-51)
        project.pop("xcp")
        path = self.tmp / "noxcp.yaml"
        path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        rtegen.generate(ECU_FILE, rtegen.nxconfig.load(path), self.tmp / "noxcp", REPO)
        return self.tmp / "noxcp"

    def test_project_without_xcp_gets_the_missing_marker(self):
        """rtegen writes the header for every project (GeneratedIncludesTest, PLAT-25); without xcp: no slave."""
        out = self.demo_without_xcp()
        header = (out / "xcp" / "xcpCanConfiguration.h").read_text(encoding="utf-8")
        self.assertIn("#define NX_XCP_MISSING 1\n", header)
        self.assertNotIn("NX_XCP_CAN_ID_MASTER", header)
        self.assertIsNone(xcp_of(out))
        self.assertNotIn("0x7F0UL", (out / "can" / "canRxFilterConfiguration.h").read_text(encoding="utf-8"))

    def test_rx_filter_grouping(self):
        """More identifiers than buffers: masks, every id still accepted, as few foreign ids as possible."""
        def accepts(f, value):
            return (value & f.mask) == (f.id & f.mask)

        # 16 ids of one block and 6 single ones into 14 buffers: the block becomes one filter without foreign ids
        ids = [(0x100 + i, False, f"m{i}") for i in range(16)] + [(0x300 + 2 * i, False, f"s{i}") for i in range(6)]
        filters = rtegen.can_rx_filters(ids)
        self.assertEqual([f.buffer for f in filters], list(range(14)))
        self.assertEqual(filters[0].id, 0x100)
        self.assertEqual(filters[0].mask, 0x7F0)
        for value, _, _ in ids:
            self.assertTrue(any(accepts(f, value) for f in filters), hex(value))
        distinct = {(f.id, f.mask) for f in filters}
        foreign = {v for v in range(0x800) if any((v & m) == (i & m) for i, m in distinct)} - {v for v, _, _ in ids}
        self.assertEqual(foreign, set())

        # scattered ids: all accepted, still at most 14 distinct filters, the queue repeats them
        scattered = [((i * 0x2F5) % 0x800, False, f"x{i}") for i in range(40)]
        filters = rtegen.can_rx_filters(scattered)
        self.assertEqual(len(filters), 14)
        for value, _, _ in scattered:
            self.assertTrue(any(accepts(f, value) for f in filters), hex(value))
        accepted = {v for v in range(0x800) if any(accepts(f, v) for f in filters)}
        self.assertLess(len(accepted), 0x800)                               # not simply "accept all"

        # extended ids only in buffers 14 and 15, standard ones never there
        mixed = [(0x18FF0001, True, "e1"), (0x18FF0002, True, "e2"), (0x18FF0104, True, "e3"), (0x123, False, "s")]
        filters = rtegen.can_rx_filters(mixed)
        ext = [f for f in filters if f.extended]
        self.assertEqual([f.buffer for f in ext], [14, 15])
        self.assertTrue(all(f.buffer < 14 for f in filters if not f.extended))
        for value, extended, _ in mixed:
            self.assertTrue(any(accepts(f, value) for f in filters if f.extended == extended), hex(value))
        self.assertTrue(all((f.id | f.mask) <= 0x1FFFFFFF for f in ext))
        # duplicates count once, a type without ids gets no buffer
        self.assertEqual({(f.id, f.mask) for f in rtegen.can_rx_filters([(5, False, "a"), (5, False, "b")])},
                         {(5, 0x7FF)})
        self.assertEqual(rtegen.can_rx_filters([]), [])

    def test_demo_configuration(self):
        _, buffers, _ = self.run_with(ECU)
        out = self.tmp / "out"
        config = (out / "RteConfig.cpp").read_text(encoding="utf-8")
        self.assertTrue((out / "CanDb.c").exists())
        self.assertIn('"dio.out.LED1, can.tx.EVB_Status.Led1"', config)
        self.assertIn("{\"PC_Command\", CANDB_PC_COMMAND_ID, 500U", config)
        self.assertIn("if ((canTick % 10U) == 0U)", config)
        self.assertIn("static_cast<float32>(CanDb_PC_Command_Setpoint_mV_ToDouble(msg.Setpoint_mV))", config)
        self.assertEqual(buffers["can.rx.PC_Command.Setpoint_mV"].init, "2500.0F")
        self.assertIn("CanDb.c", (out / "rte_components.cmake").read_text(encoding="utf-8"))

    def test_without_can_no_can_code(self):
        self.run_with(NO_CAN)
        out = self.tmp / "out"
        self.assertNotIn("CanDb", (out / "RteConfig.cpp").read_text(encoding="utf-8"))
        self.assertIn("set(RTE_CAN_SOURCES\n)", (out / "rte_components.cmake").read_text(encoding="utf-8"))

    def test_tx_and_rx_accepted(self):
        ecu = self.can_ecu(**{
            "DemoSwc.Led1.Led1": ["dio.out.LED1", "can.tx.Tx.Flag"],
            "DemoSwc.MotorNtcFiltered_mV.MotorNtcFiltered_mV": ["can.tx.Tx.Wide"],
            "DemoSwc.MotorNtc_mV.MotorNtc_mV": "can.rx.Rx.Small",
        })
        self.run_with(ecu)
        config = (self.tmp / "out" / "RteConfig.cpp").read_text(encoding="utf-8")
        self.assertIn("msg.Flag = CanDb_Tx_Flag_FromInt((Rte_Buf_DemoSwc_Led1_Led1 != 0U) ? 1 : 0);", config)
        self.assertIn("if ((canTick % 3U) == 0U)", config)
        self.assertIn("Rte_Buf_CanRx_Rx_Big = static_cast<sint32>", config)  # unconnected rx signal kept
        self.assertNotIn("Rx_Other", config)  # not received by EVB

    def test_can_errors(self):
        cases = [
            ({"DemoSwc.MotorNtc_mV.MotorNtc_mV": "can.rx.Rx.Big"}, "signal range [-1000|1000] does not fit type uint32"),
            ({"DemoSwc.MotorNtc_mV.MotorNtc_mV": "can.rx.Rx.Frac"}, "is not integer, use a float32/float64 port"),
            ({"DemoSwc.Button1.Button1": "can.rx.Rx.Small"}, "signal range [0|200] does not fit type boolean"),
            ({"DemoSwc.MotorNtc_mV.MotorNtc_mV": "can.rx.Rx.Other"}, "node EVB is no receiver of Rx.Other"),
            ({"DemoSwc.MotorNtc_mV.MotorNtc_mV": "can.rx.Rx.Nope"}, "message Rx has no signal Nope"),
            ({"DemoSwc.MotorNtc_mV.MotorNtc_mV": "can.rx.Nope.X"}, "no message Nope"),
            ({"DemoSwc.MotorNtc_mV.MotorNtc_mV": "can.rx.Bad.S"}, "does not fit 8 bit unsigned"),
            ({"DemoSwc.MotorNtc_mV.MotorNtc_mV": "can.rx.Rx"}, "expected can.rx.<Message>.<Signal>"),
            ({"DemoSwc.MotorNtc_mV.MotorNtc_mV": "can.tx.Tx.Wide"}, "input port cannot use can.tx"),
            ({"DemoSwc.Led1.Led1": "can.rx.Rx.Small"}, "output port cannot use can.rx"),
            ({"DemoSwc.MotorNtcFiltered_mV.MotorNtcFiltered_mV": "can.tx.Rx.Small"}, "node EVB is no sender of Rx"),
            ({"DemoSwc.MotorNtcFiltered_mV.MotorNtcFiltered_mV": "can.tx.NoCycle.S"},
             "has no GenMsgCycleTime (t.dbc): a cyclic message needs one"),   # COM-38: or Spontaneous
            ({"DemoSwc.MotorNtcFiltered_mV.MotorNtcFiltered_mV": "can.tx.Tx.Neg"}, "[-128|-1] and type uint32"),
            ({"DemoSwc.Led1.Led1": "can.tx.Tx.Pair"}, "[5|8] cannot represent boolean 0 and 1"),
            ({"DemoSwc.Led2.Led2": "can.tx.Tx.Neg"}, "cannot represent boolean 0 and 1"),
            ({"DemoSwc.Led1.Led1": "can.tx.Tx.Wide", "DemoSwc.Led2.Led2": "can.tx.Tx.Wide"},
             "signal Tx.Wide is already sent from another port"),
        ]
        for connections, fragment in cases:
            with self.subTest(connections=connections):
                ecu = self.can_ecu(**connections)
                if fragment is None:
                    with self.assertRaises(rtegen.RteError) as ctx:  # only the 25 ms cycle may fail here
                        self.run_with(ecu)
                    self.assertIn("cycle time 25 ms is no multiple of the base 10 ms", str(ctx.exception))
                else:
                    self.expect_error(ecu, fragment)

    def test_can_section_errors(self):
        ecu = self.can_ecu()
        ecu["can"]["node"] = "XYZ"
        self.expect_error(ecu, "node XYZ is not defined")
        ecu = self.can_ecu()
        ecu["can"]["bus"] = "CAN_1"
        self.expect_error(ecu, "can.bus: bus CAN_1 is not a CAN bus of the project (use.can: CAN0, CAN1)")
        ecu = self.can_ecu()
        ecu["uds"] = {"bus": "CAN2"}                    # the demo uses CAN0 and CAN1 (COM-05)
        self.expect_error(ecu, "uds.bus: bus CAN2 is not a CAN bus of the project (use.can: CAN0, CAN1)")
        ecu = self.can_ecu()
        ecu["uds"] = {"bus": "CAN0", "request": 1}
        self.expect_error(ecu, "uds.request: Extra inputs are not permitted")          # schema (ARCH-34)
        ecu = self.can_ecu()
        ecu["can"]["monitor"] = ["Tx"]
        self.expect_error(ecu, "node EVB receives no signal of Tx")
        ecu = copy.deepcopy(NO_CAN)
        ecu["connections"]["DemoSwc.MotorNtc_mV.MotorNtc_mV"] = "can.rx.Rx.Small"
        self.expect_error(ecu, "no DBC configured")

    def test_rx_timeout_rounds_up(self):
        dbc = CAN_DBC.replace('BA_ "GenMsgCycleTime" BO_ 512 100;', 'BA_ "GenMsgCycleTime" BO_ 512 33;')
        ecu = self.can_ecu(**{"DemoSwc.MotorNtc_mV.MotorNtc_mV": "can.rx.Rx.Small"})
        (self.tmp / "t.dbc").write_text(dbc, encoding="utf-8")
        self.run_with(ecu)
        config = (self.tmp / "out" / "RteConfig.cpp").read_text(encoding="utf-8")
        self.assertIn('{"Rx", CANDB_RX_ID, 100U,', config)  # 3 x 33 ms = 99 ms -> 10 base periods


PWM_PORT = "DemoSwc.MotorNtcFiltered_mV.MotorNtcFiltered_mV"  # uint32 provided port


class RtePwmTest(unittest.TestCase):
    """pwm.<Pair> connections: duty of a complementary PWM pair the project uses."""

    setUp, tearDown = RteGenTest.setUp, RteGenTest.tearDown
    run_with, expect_error = RteGenTest.run_with, RteGenTest.expect_error

    def ecu(self, **connections):
        ecu = copy.deepcopy(NO_CAN)
        ecu["connections"].update(connections)
        return ecu

    def test_board_pairs_are_targets(self):
        self.assertEqual(rtegen.board_signals(PROJECT)["pwm"], {"MOT1_U", "MOT1_V", "MOT1_W"})

    def test_pwm_accepted(self):
        _, buffers, _ = self.run_with(self.ecu(**{PWM_PORT: "pwm.MOT1_V"}))
        self.assertEqual((buffers[PWM_PORT].bsw, buffers[PWM_PORT].bsw_signal), ("pwm", "MOT1_V"))
        config = (self.tmp / "out" / "RteConfig.cpp").read_text(encoding="utf-8")
        self.assertIn("#include <outputPwm/OutputPwm.h>", config)
        self.assertIn("static uint32_t written = 0U;", config)
        self.assertIn("::bios::OutputPwm::setDuty(::bios::OutputPwm::MOT1_V, duty, false)", config)
        self.assertIn("(Rte_Buf_DemoSwc_MotorNtcFiltered_mV_MotorNtcFiltered_mV > 10000U) ? 10000U", config)
        self.assertIn('"pwm.MOT1_V"', config)

    def test_pwm_with_can_target(self):
        # COM-02 fan-out: one pwm target next to CAN signals
        ecu = copy.deepcopy(ECU)
        ecu["connections"][PWM_PORT] = ecu["connections"][PWM_PORT] + ["pwm.MOT1_U"]
        self.run_with(ecu)
        config = (self.tmp / "out" / "RteConfig.cpp").read_text(encoding="utf-8")
        self.assertIn("OutputPwm::MOT1_U", config)
        self.assertIn("writeCan();", config)

    def test_without_pwm_no_include(self):
        self.run_with(NO_CAN)
        self.assertNotIn("OutputPwm", (self.tmp / "out" / "RteConfig.cpp").read_text(encoding="utf-8"))

    def test_pwm_errors(self):
        cases = [
            ({PWM_PORT: "pwm.MOT2_U"}, "MOT2_U is not a pwm signal used by the project"),
            ({PWM_PORT: "pwm.MOT1_U_PWM_HS"}, "MOT1_U_PWM_HS is not a pwm signal used by the project"),  # channel, not pair
            ({"DemoSwc.Led1.Led1": "pwm.MOT1_U"}, "type boolean does not fit pwm (uint16, uint32)"),
            ({"DemoSwc.MotorNtc_mV.MotorNtc_mV": "pwm.MOT1_U"}, "input port cannot use pwm"),
            ({PWM_PORT: ["pwm.MOT1_U", "pwm.MOT1_V"]}, "already connected to pwm.MOT1_U"),
            ({PWM_PORT: "pwm.MOT1_U", "DemoSwc.Led1.Led1": ["dio.out.LED1"]}, None),
        ]
        for connections, fragment in cases:
            with self.subTest(connections=connections):
                if fragment is None:
                    self.run_with(self.ecu(**connections))
                else:
                    self.expect_error(self.ecu(**connections), fragment)

    def test_pair_driven_by_two_ports(self):
        # no second uint provided port in DemoSwc: fake one by retyping Led2 in a copy of the ARXML
        arxml = self.tmp / "swc" / "generated" / "arxml"
        arxml.mkdir(parents=True)
        for f in (REPO / "swc" / "DemoSwc" / "generated" / "arxml").glob("*.arxml"):
            text = f.read_text(encoding="utf-8")
            if f.name == "DemoSwc_interface.arxml":
                head, sep, tail = text.partition("<SHORT-NAME>Led2</SHORT-NAME>")
                tail = tail.replace("/DataTypes/boolean<", "/DataTypes/uint32<", 1)
                text = head + sep + tail
            (arxml / f.name).write_text(text, encoding="utf-8")
        # the copy of a component carries its calibration values (RTE-03)
        shutil.copy(REPO / "swc" / "DemoSwc" / "DemoSwc_data.m", self.tmp / "swc" / "DemoSwc_data.m")
        ecu = self.ecu(**{PWM_PORT: "pwm.MOT1_W", "DemoSwc.Led2.Led2": "pwm.MOT1_W"})
        ecu["components"] = [{"name": "DemoSwc", "path": str(self.tmp / "swc")}]
        self.expect_error(ecu, "pwm.MOT1_W is already driven by DemoSwc.Led2.Led2")


class RtePwmHostTest(unittest.TestCase):
    """Compiles the generated RTE with a pwm target against stubs and runs test/pwm_host."""

    def test_pwm_runtime(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            ecu = copy.deepcopy(NO_CAN)
            ecu["connections"][PWM_PORT] = "pwm.MOT1_V"
            ecu_file = tmp / "rte.yaml"
            ecu_file.write_text(yaml.safe_dump(ecu), encoding="utf-8")
            out = tmp / "gen"
            rtegen.generate(ecu_file, PROJECT, out, REPO)
            host = HERE / "test"
            zig = [sys.executable, "-m", "ziglang"]
            includes = [f"-I{out}", f"-I{out / 'tasks'}", f"-I{REPO / 'libs/bsw/autosarBase/include'}", f"-I{host / 'pwm_host' / 'stubs'}",
                        f"-I{host / 'can_host' / 'stubs'}", f"-I{REPO / 'swc/DemoSwc/generated/src'}"]
            objects = []
            for src in (out / "Rte.c", REPO / "swc/DemoSwc/generated/src/DemoSwc.c"):
                obj = tmp / (src.stem + ".o")
                r = subprocess.run(zig + ["cc", "-std=c99", "-O1", "-Wall", "-Wextra", "-Werror", *includes, "-c",
                                          str(src), "-o", str(obj)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr[-3000:])
                objects.append(str(obj))
            exe = tmp / "rte_pwm_host_test.exe"
            r = subprocess.run(zig + ["c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror", *includes,
                                      str(out / "RteConfig.cpp"), str(host / "pwm_host" / "rte_pwm_host_test.cpp"),
                                      *objects, "-o", str(exe)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            r = subprocess.run([str(exe)], capture_output=True, text=True)
            print("\n" + r.stdout.strip(), file=sys.stderr)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class RteCanHostTest(unittest.TestCase):
    """Compiles the generated RTE for the demo project rte.yaml with OpenBSW stubs and runs test/can_host."""

    def test_can_runtime(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            ecu_file = tmp / "rte.yaml"
            ecu_file.write_text(yaml.safe_dump(ECU), encoding="utf-8")
            out = tmp / "gen"
            rtegen.generate(ecu_file, PROJECT, out, REPO)
            build, run = build_can_host(tmp, out, CAN_HOST / "rte_can_host_test.cpp")
            self.assertEqual(build.returncode, 0, build.stderr[-4000:])
            print("\n" + run.stdout.strip(), file=sys.stderr)
            self.assertEqual(run.returncode, 0, run.stdout[-4000:])
            self.assertIn(", 0 failures", run.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


QLF_DBC = """VERSION ""
NS_ :
BS_:
BU_: EVB PC
BO_ 256 Tx: 8 EVB
 SG_ Flag : 0|1@1+ (1,0) [0|1] "" PC
 SG_ Crc : 8|8@1+ (1,0) [0|255] "" PC
 SG_ Counter : 16|4@1+ (1,0) [0|15] "" PC
 SG_ Nibble : 20|4@1+ (1,0) [0|15] "" PC
BO_ 257 Short: 1 EVB
 SG_ Byte : 0|8@1+ (1,0) [0|255] "" PC
BO_ 512 Rx: 8 PC
 SG_ Small : 0|8@1+ (1,0) [0|200] "" EVB
 SG_ Crc : 8|8@1+ (1,0) [0|255] "" EVB
 SG_ Counter : 16|4@1+ (1,0) [0|15] "" EVB
 SG_ Nibble : 20|4@1+ (1,0) [0|15] "" EVB
 SG_ CrcM : 39|8@0+ (1,0) [0|255] "" EVB
 SG_ CounterM : 47|4@0+ (1,0) [0|15] "" EVB
 SG_ Other : 40|4@1+ (1,0) [0|15] "" PC
BO_ 513 Bad: 8 PC
 SG_ Wide : 0|16@1+ (1,0) [0|65535] "" EVB
 SG_ Signed : 16|8@1- (1,0) [-128|127] "" EVB
 SG_ Scaled : 24|8@1+ (2,0) [0|510] "" EVB
 SG_ Mid : 33|4@1+ (1,0) [0|15] "" EVB
 SG_ Cross : 44|8@1+ (1,0) [0|255] "" EVB
 SG_ Plain : 56|8@1+ (1,0) [0|255] "" EVB
BA_DEF_ BO_  "GenMsgCycleTime" INT 0 65535;
BA_DEF_DEF_  "GenMsgCycleTime" 0;
BA_ "GenMsgCycleTime" BO_ 256 30;
BA_ "GenMsgCycleTime" BO_ 257 30;
BA_ "GenMsgCycleTime" BO_ 512 100;
BA_ "GenMsgCycleTime" BO_ 513 100;
"""

GOOD_E2E = {"profile": "P11", "data_id": 0x123, "data_id_mode": "BOTH", "crc": "Crc", "counter": "Counter"}


class RteQualifierTest(unittest.TestCase):
    """COM-03: can.qlf.<Message> connections, section can.qualifiers and the E2E configuration checks."""

    setUp, tearDown = RteGenTest.setUp, RteGenTest.tearDown
    run_with, expect_error = RteGenTest.run_with, RteGenTest.expect_error

    def ecu(self, qualifiers=None, **connections):
        dbc = self.tmp / "q.dbc"
        dbc.write_text(QLF_DBC, encoding="utf-8")
        ecu = copy.deepcopy(NO_CAN)
        ecu["can"] = {"dbc": str(dbc), "node": "EVB", "monitor": ["Rx"]}
        if qualifiers is not None:
            ecu["can"]["qualifiers"] = qualifiers
        ecu["connections"].update(connections)
        return ecu

    def generated(self, name="RteConfig.cpp"):
        return (self.tmp / "out" / name).read_text(encoding="utf-8")

    def test_constants_are_generated_for_c_and_matlab(self):
        self.run_with(NO_CAN)  # also without CAN: the models need the constants
        header, matlab = self.generated("Rte_Qlf.h"), self.generated("Rte_Qlf.m")
        for name, value in (("DEBOUNCE", 0), ("OK", 1), ("ERROR_ALIVE", 2), ("ERROR_CRC", 4),
                            ("ERROR_TIMEOUT", 8), ("ERROR", 16), ("ERROR_RANGE", 32), ("NOT_AVAILABLE", 64)):
            self.assertIn(f"#define QLF_{name} ", re.sub(r" +", " ", header))
            self.assertRegex(header, rf"#define QLF_{name}\s+\(\(Rte_QlfType\){value}u\)")
            self.assertRegex(matlab, rf"QLF_{name}\s+= uint8\({value}\);")
        self.assertIn('#include "Rte_Qlf.h"', self.generated("Rte_Type.h"))
        self.assertEqual(rtegen.QLF, {"DEBOUNCE": 0, "OK": 1, "ERROR_ALIVE": 2, "ERROR_CRC": 4,
                                      "ERROR_TIMEOUT": 8, "ERROR": 16, "ERROR_RANGE": 32,
                                      "NOT_AVAILABLE": 64})   # RTE-28: 64 is fixed (NVM-19 relies on it)
        self.assertIn("set(RTE_USES_E2E FALSE)", self.generated("rte_components.cmake"))

    def test_qualifier_buffer_for_every_received_message(self):
        _, buffers, _ = self.run_with(self.ecu())
        b = buffers["can.qlf.Rx"]
        self.assertEqual((b.name, b.ctype, b.init, b.bsw), ("Rte_Buf_CanQlf_Rx", "uint8", "0U", "can.qlf"))
        config = self.generated()
        self.assertIn('"Rx.Qlf", "can.qlf.Rx"', config)              # console "rte signals"
        self.assertIn("Rte_Buf_CanQlf_Rx = CAN_RX_STATUS[0].qlf;", config)
        self.assertIn('{"Rx", CANDB_RX_ID, 300U, 0U,', config)       # timeout 3 x cycle, no debouncing
        self.assertNotIn("E2E_", config)                             # no e2e section: no E2E code

    def test_connection_and_e2e_accepted(self):
        ecu = self.ecu({"Rx": {"timeout_ms": 250, "debounce_ms": 50, "on_error": "init", "e2e": GOOD_E2E},
                        "Tx": {"e2e": dict(GOOD_E2E, profile="P01", data_id_mode="NIBBLE",
                                           data_id_nibble="Nibble")}},
                       **{"DemoSwc.MotorNtc_mV.MotorNtc_mV": "can.qlf.Rx",
                          "DemoSwc.Led1.Led1": "can.tx.Tx.Flag"})
        _, buffers, _ = self.run_with(ecu)
        self.assertEqual(buffers["can.qlf.Rx"].links, ["can.qlf.Rx"])
        config = self.generated()
        self.assertIn('{"Rx", CANDB_RX_ID, 250U, 50U,', config)
        self.assertIn("QLF_DEBOUNCE, 0U, false, false, true},", config)   # on_error: init
        self.assertIn("E2E_P11ConfigType const Rx_E2E_CONFIG = {/* CounterOffset */ 16U, /* CRCOffset */ 8U, "
                      "/* DataID */ 0x0123U, /* DataIDNibbleOffset */ 0U, "
                      "/* DataIDMode */ E2E_P11_DATAID_BOTH, /* DataLength */ 64U, "
                      "/* MaxDeltaCounter */ 1U};", config)
        self.assertIn("E2E_P01ConfigType const Tx_E2E_CONFIG = {/* CounterOffset */ 16U, /* CRCOffset */ 8U, "
                      "/* DataID */ 0x0123U, /* DataIDNibbleOffset */ 20U, "
                      "/* DataIDMode */ E2E_P01_DATAID_NIBBLE, /* DataLength */ 64U, "
                      "/* MaxDeltaCounterInit */ 1U, /* MaxNoNewOrRepeatedData */ 14U, "
                      "/* SyncCounterInit */ 1U};", config)
        self.assertIn("#include <e2e/E2E_P01.h>", config)
        self.assertIn("#include <e2e/E2E_P11.h>", config)
        self.assertIn("(void)E2E_P11CheckInit(&Rx_E2E_CHECK);", config)
        self.assertIn("(void)E2E_P01ProtectInit(&Tx_E2E_PROTECT);", config)
        self.assertIn("E2E_P01Protect(", config)
        self.assertIn("set(RTE_USES_E2E TRUE)", self.generated("rte_components.cmake"))
        rte_c = self.generated("Rte.c")
        self.assertIn("*data = (uint32)Rte_Buf_CanQlf_Rx;", rte_c)     # the port reads the qualifier buffer

    def test_motorola_offsets(self):
        """A Motorola signal names its most significant bit: byte 4 and the high nibble of byte 5."""
        ecu = self.ecu({"Rx": {"e2e": dict(GOOD_E2E, crc="CrcM", counter="CounterM")}})
        self.run_with(ecu)
        self.assertIn("/* CounterOffset */ 44U, /* CRCOffset */ 32U,", self.generated())

    def test_p02_configuration(self):
        ids = list(range(1, 17))
        ecu = self.ecu({"Rx": {"e2e": {"profile": "P02", "data_id_list": ids, "crc": "Crc",
                                       "counter": "Counter"}}})
        self.expect_error(ecu, "profile P02 has a fixed header, the crc must be at bit offset 0 (byte 0), "
                               "Crc is at 8")
        ecu = self.ecu({"Bad": {"e2e": {"profile": "P02", "data_id_list": ids, "crc": "Plain",
                                        "counter": "Mid"}}},
                       **{"DemoSwc.MotorNtc_mV.MotorNtc_mV": "can.qlf.Bad"})
        self.expect_error(ecu, "signal Mid is not nibble aligned (bit 1 of byte 4)")

    def test_connection_errors(self):
        cases = [
            ({"DemoSwc.Led1.Led1": "can.qlf.Rx"}, "output port cannot use can.qlf"),
            ({"DemoSwc.Button1.Button1": "can.qlf.Rx"}, "type boolean does not fit can.qlf "
                                                        "(uint16, uint32, uint8)"),
            ({"DemoSwc.MotorNtc_mV.MotorNtc_mV": "can.qlf.Nope"}, "no message Nope"),
            ({"DemoSwc.MotorNtc_mV.MotorNtc_mV": "can.qlf.Tx"}, "node EVB receives no signal of Tx"),
            ({"DemoSwc.MotorNtc_mV.MotorNtc_mV": "can.qlf.Rx.Small"}, "expected can.qlf.<Message>"),
        ]
        for connections, fragment in cases:
            with self.subTest(connections=connections):
                self.expect_error(self.ecu(None, **connections), fragment)
        ecu = copy.deepcopy(NO_CAN)
        ecu["connections"]["DemoSwc.MotorNtc_mV.MotorNtc_mV"] = "can.qlf.Rx"
        self.expect_error(ecu, "no DBC configured")

    def test_section_errors(self):
        cases = [
            # schema (tools/nxconfig/model.py QualifierUse, ARCH-34)
            ({"Rx": {"timeout": 10}}, "can.qualifiers.Rx.timeout: Extra inputs are not permitted"),
            ({"Rx": {"on_error": "keep"}}, "can.qualifiers.Rx.on_error: Input should be 'hold' or 'init'"),
            ({"Rx": {"timeout_ms": -1}}, "can.qualifiers.Rx.timeout_ms: Input should be greater than or equal to 0"),
            ({"Rx": {"debounce_ms": "20"}}, "can.qualifiers.Rx.debounce_ms: Input should be a valid integer"),
            ({"Rx": 5}, "can.qualifiers.Rx: Input should be a valid dictionary"),
            # rtegen: DBC and direction of the message
            ({"Nope": {}}, "can.qualifiers.Nope: q.dbc: no message Nope"),
            ({"Short": {}}, "can.qualifiers.Short: message Short is neither sent nor received by the RTE"),
            ({"Tx": {"timeout_ms": 100, "e2e": GOOD_E2E}},
             "can.qualifiers.Tx.timeout_ms: Tx is sent by this ECU, a sent message only takes e2e"),
            ({"Tx": {"on_error": "init", "e2e": GOOD_E2E}}, "can.qualifiers.Tx.on_error: Tx is sent"),
            ({"Tx": {}}, "can.qualifiers.Tx: Tx is sent by this ECU, only e2e (E2E protection) applies"),
        ]
        for qualifiers, fragment in cases:
            with self.subTest(qualifiers=qualifiers):
                ecu = self.ecu(qualifiers, **{"DemoSwc.Led1.Led1": "can.tx.Tx.Flag"})
                self.expect_error(ecu, fragment)
        ecu = self.ecu(None, **{"DemoSwc.Led1.Led1": "can.tx.Tx.Flag"})
        ecu["can"]["qualifiers"] = [1]
        self.expect_error(ecu, "can.qualifiers: Input should be a valid dictionary")
        ecu = self.ecu(None, **{"DemoSwc.Led1.Led1": "can.tx.Tx.Flag"})
        ecu["can"]["qualifier"] = {}   # typo in the section name
        self.expect_error(ecu, "can.qualifier: Extra inputs are not permitted")

    def test_e2e_errors(self):
        cases = [
            ({"profile": "P99", "crc": "Crc", "counter": "Counter"},
             "can.qualifiers.Rx.e2e.profile: Input should be 'P01', 'P02' or 'P11'"),        # schema
            ({"profile": "P11", "crc": "Crc"}, "can.qualifiers.Rx.e2e.counter: Field required"),
            ({"profile": "P11", "crc": "", "counter": "Counter"},
             "can.qualifiers.Rx.e2e.crc: String should have at least 1 character"),
            (dict(GOOD_E2E, data_id_mode="SOME"), "e2e.data_id_mode: Input should be 'BOTH', 'ALT', 'LOW' or "
                                                  "'NIBBLE'"),
            (dict(GOOD_E2E, data_id=0x10000), "e2e.data_id: 0x10000 does not fit the 16 bit data id of P11"),
            (dict(GOOD_E2E, data_id_mode="ALT"), "e2e.data_id_mode: ALT is not a data id mode of P11 "
                                                 "(BOTH, NIBBLE)"),
            (dict(GOOD_E2E, data_id_mode="NIBBLE"), "e2e.data_id_nibble: mode NIBBLE sends the high nibble"),
            (dict(GOOD_E2E, data_id_mode="NIBBLE", data_id=0x1234, data_id_nibble="Nibble"),
             "e2e.data_id: mode NIBBLE allows at most 12 bit (0x1234 given)"),
            (dict(GOOD_E2E, data_id_nibble="Nibble"), "e2e.data_id_nibble: only mode NIBBLE sends a data id "
                                                      "nibble (mode BOTH)"),
            (dict(GOOD_E2E, max_delta_counter=15), "e2e.max_delta_counter: 15 is above the counter range of "
                                                   "P11 (0..14)"),
            (dict(GOOD_E2E, data_id_list=[0] * 16), "e2e.data_id_list: only profile P02 uses a data id list"),
            ({"profile": "P02", "crc": "Crc", "counter": "Counter", "data_id": 1},
             "e2e.data_id: profile P02 uses data_id_list"),
            ({"profile": "P02", "crc": "Crc", "counter": "Counter", "data_id_list": [0] * 15},
             "e2e.data_id_list: profile P02 needs exactly 16 data ids"),
            ({"profile": "P02", "crc": "Crc", "counter": "Counter", "data_id_list": [256] + [0] * 15},
             "e2e.data_id_list[0]: 256 does not fit the 8 bit data id of P02"),
            (dict(GOOD_E2E, crc="Nope"), "can.qualifiers.Rx.e2e: message Rx has no signal Nope"),
            (dict(GOOD_E2E, crc="Nibble"), "signal Nibble is 4 bit wide, the E2E crc needs 8 bit"),
            (dict(GOOD_E2E, crc="CrcM", counter="Crc"), "signal Crc is 8 bit wide, the E2E counter needs "
                                                        "4 bit"),
            (dict(GOOD_E2E, counter="Crc"), "crc, counter and data_id_nibble must name different DBC signals"),
            (dict(GOOD_E2E, counter="Other"), "node EVB is no receiver of Rx.Other, it cannot check the "
                                              "E2E counter"),
        ]
        for e2e, fragment in cases:
            with self.subTest(e2e=e2e):
                self.expect_error(self.ecu({"Rx": {"e2e": e2e}}), fragment)
        bad = [
            ({"crc": "Signed", "counter": "Mid"}, "signal Signed must be unsigned with factor 1 and offset 0"),
            ({"crc": "Scaled", "counter": "Mid"}, "signal Scaled must be unsigned with factor 1 and offset 0"),
            ({"crc": "Wide", "counter": "Mid"}, "signal Wide is 16 bit wide, the E2E crc needs 8 bit"),
            ({"crc": "Cross", "counter": "Mid"}, "signal Cross crosses a byte boundary, the E2E crc must lie "
                                                 "in one byte"),
            ({"crc": "Plain", "counter": "Mid"}, "signal Mid is not nibble aligned (bit 1 of byte 4)"),
        ]
        for fields, fragment in bad:
            with self.subTest(fields=fields):
                ecu = self.ecu({"Bad": {"e2e": dict(GOOD_E2E, **fields)}},
                               **{"DemoSwc.MotorNtc_mV.MotorNtc_mV": "can.qlf.Bad"})
                self.expect_error(ecu, fragment)

    def test_crc_and_counter_must_not_be_connected(self):
        for target, fragment in (("can.rx.Rx.Counter", "Rx.Counter carries the E2E counter and must not be "
                                                       "connected to a port"),
                                 ("can.rx.Rx.Crc", "Rx.Crc carries the E2E crc and must not be connected")):
            with self.subTest(target=target):
                ecu = self.ecu({"Rx": {"e2e": GOOD_E2E}},
                               **{"DemoSwc.MotorNtc_mV.MotorNtc_mV": target})
                self.expect_error(ecu, fragment)
        ecu = self.ecu({"Tx": {"e2e": GOOD_E2E}},
                       **{"DemoSwc.Led1.Led1": "can.tx.Tx.Flag",
                          "DemoSwc.MotorNtcFiltered_mV.MotorNtcFiltered_mV": "can.tx.Tx.Crc"})
        self.expect_error(ecu, "Tx.Crc carries the E2E crc and must not be connected to a port")

    def test_message_too_short_for_e2e(self):
        ecu = self.ecu({"Short": {"e2e": dict(GOOD_E2E, crc="Byte", counter="Byte")}},
                       **{"DemoSwc.Led1.Led1": "can.tx.Short.Byte"})
        self.expect_error(ecu, "message Short is 1 byte long, profile P11 needs 2..32 byte")


CAN_HOST = HERE / "test" / "can_host"
E2E_SOURCES = ["E2E.c", "E2E_P01.c", "E2E_P02.c", "E2E_P11.c"]


def build_can_host(tmp, out, test_file, extra_includes=()):
    """Compiles the generated RTE with the OpenBSW stubs, the E2E library and one host test; runs it."""
    zig = [sys.executable, "-m", "ziglang"]
    includes = [f"-I{out}", f"-I{out / 'tasks'}", f"-I{REPO / 'libs/bsw/autosarBase/include'}",
                f"-I{CAN_HOST / 'stubs'}", f"-I{REPO / 'swc/DemoSwc/generated/src'}",
                f"-I{REPO / 'libs/bsw/e2e/include'}", *extra_includes]
    objects = []
    sources = [out / "CanDb.c", out / "Rte.c", REPO / "swc/DemoSwc/generated/src/DemoSwc.c"]
    sources += [REPO / "libs/bsw/e2e/src/e2e" / name for name in E2E_SOURCES]
    for src in sources:
        obj = tmp / (src.stem + ".o")
        r = subprocess.run(zig + ["cc", "-std=c99", "-O1", "-Wall", "-Wextra", "-Wconversion", "-Werror",
                                  *includes, "-c", str(src), "-o", str(obj)], capture_output=True, text=True)
        if r.returncode != 0:
            return r, None
        objects.append(str(obj))
    exe = tmp / (test_file.stem + ".exe")
    r = subprocess.run(zig + ["c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
                              "-DPLATFORM_SUPPORT_CAN=1", *includes, str(out / "RteConfig.cpp"),
                              str(test_file), *objects, "-o", str(exe)], capture_output=True, text=True)
    if r.returncode != 0:
        return r, None
    return r, subprocess.run([str(exe)], capture_output=True, text=True)


class RteCommControlHostTest(unittest.TestCase):
    """COM-29: CommunicationControl (UDS 0x28) reaches the generated COM.

    The state belongs to the communication layer: the RTE keeps it and offers rte::setApplicationTxEnabled(),
    a diagnostic stack writes it (direction Dcm -> Com). The generate therefore names no diagnostic module at
    all, and the host test builds with the same profile as every other CAN host test - no UDS, no stub.
    """

    def generate_demo(self, tmp):
        ecu_file = tmp / "rte.yaml"
        ecu_file.write_text(yaml.safe_dump(ECU), encoding="utf-8")
        out = tmp / "gen"
        rtegen.generate(ecu_file, PROJECT, out, REPO)
        return out

    def test_communication_control_mutes_the_application_messages(self):
        """Default "send" without any diagnostic stack, silence after the setter says no, and the messages
        back in their old phase with the next E2E counter."""
        tmp = Path(tempfile.mkdtemp())
        try:
            out = self.generate_demo(tmp)
            build, run = build_can_host(tmp, out, CAN_HOST / "rte_comm_control_host_test.cpp")
            self.assertEqual(build.returncode, 0, build.stderr[-4000:])
            print("\n" + run.stdout.strip(), file=sys.stderr)
            self.assertEqual(run.returncode, 0, run.stdout[-4000:])
            self.assertIn(", 0 failures", run.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_the_rte_does_not_depend_on_the_diagnostic_stack(self):
        """The generate keeps the state itself: no diagnostic header, no build switch, and the default is
        "send" - a project without any diagnostic stack behaves as before."""
        tmp = Path(tempfile.mkdtemp())
        try:
            out = self.generate_demo(tmp)
            config = (out / "RteConfig.cpp").read_text(encoding="utf-8")
            header = (out / "RteConfig.h").read_text(encoding="utf-8")
            self.assertNotIn("DiagSystem", config)
            self.assertNotIn("PLATFORM_SUPPORT_UDS", config)
            self.assertIn("bool applicationTxEnabled = true;", config)
            self.assertIn("&& applicationTxEnabled)", config)
            self.assertIn("void setApplicationTxEnabled(bool const enabled) "
                          "{ applicationTxEnabled = enabled; }", config)
            self.assertIn("void setApplicationTxEnabled(bool enabled);", header)
            cmake = (out / "rte_components.cmake").read_text(encoding="utf-8")
            self.assertIn("set(RTE_APPLICATION_TX_GATE TRUE)", cmake)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_a_project_without_sent_messages_is_unchanged(self):
        """Only the sent application messages are gated: an ECU description that receives but does not send
        gets neither the state nor the setter, so its generate stays byte for byte the old one. The generator
        says so in rte_components.cmake, and ecu/uds then compiles no call at all."""
        tmp = Path(tempfile.mkdtemp())
        try:
            ecu = copy.deepcopy(ECU)
            ecu["can"]["qualifiers"].pop("EVB_Status", None)   # qualifier of a message nobody sends
            ecu["connections"] = {}
            for port, target in ECU["connections"].items():
                targets = target if isinstance(target, list) else [target]
                kept = [t for t in targets if not str(t).startswith("can.tx.")]
                ecu["connections"][port] = (kept[0] if len(kept) == 1 else kept) if kept else "rte"
            ecu_file = tmp / "rte.yaml"
            ecu_file.write_text(yaml.safe_dump(ecu), encoding="utf-8")
            out = tmp / "gen"
            rtegen.generate(ecu_file, PROJECT, out, REPO)
            config = (out / "RteConfig.cpp").read_text(encoding="utf-8")
            self.assertNotIn("applicationTxEnabled", config)
            self.assertNotIn("setApplicationTxEnabled", (out / "RteConfig.h").read_text(encoding="utf-8"))
            self.assertIn("    if ((canTransceiver != nullptr) && (canTransceiver->getState() "
                          "== ::can::ICanTransceiver::State::OPEN))\n", config)
            self.assertIn("set(RTE_APPLICATION_TX_GATE FALSE)",
                          (out / "rte_components.cmake").read_text(encoding="utf-8"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_the_diagnostic_stack_writes_the_state(self):
        """The other half of COM-29 lives in ecu/uds: CommunicationControl hands every change to the RTE,
        and the call is compiled only when the generator reports a gate. If that wiring is removed, the
        target would silently keep sending - this test fails instead."""
        diag = (REPO / "ecu" / "uds" / "src" / "systems" / "DiagSystem.cpp").read_text(encoding="utf-8")
        self.assertIn("::rte::setApplicationTxEnabled(txEnabled);", diag)
        self.assertIn("#ifdef NX_RTE_APPLICATION_TX_GATE", diag)
        cmake = (REPO / "ecu" / "uds" / "CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn("RTE_APPLICATION_TX_GATE", cmake)
        self.assertIn("NX_RTE_APPLICATION_TX_GATE=1", cmake)


class RteQualifierHostTest(unittest.TestCase):
    """COM-03: compiles the generated RTE for test/can_host/qualifier_rte.yaml (one received message per E2E
    profile, one without E2E, one sent message with E2E) and runs rte_qualifier_host_test.cpp."""

    def test_qualifier_runtime(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            out = tmp / "gen"
            rtegen.generate(CAN_HOST / "qualifier_rte.yaml", PROJECT, out, REPO)
            build, run = build_can_host(tmp, out, CAN_HOST / "rte_qualifier_host_test.cpp")
            self.assertEqual(build.returncode, 0, build.stderr[-4000:])
            print("\n" + run.stdout.strip(), file=sys.stderr)
            self.assertEqual(run.returncode, 0, run.stdout[-4000:])
            self.assertIn(", 0 failures", run.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_qualifier_stalled_counter(self):
        """COM-14: a stalled E2E counter must not heal the qualifier (board capture 2026-09-16: one QLF_OK
        cycle with the tenth frame of the stalled counter); every counter value before the stall, all profiles."""
        tmp = Path(tempfile.mkdtemp())
        try:
            out = tmp / "gen"
            rtegen.generate(CAN_HOST / "qualifier_rte.yaml", PROJECT, out, REPO)
            build, run = build_can_host(tmp, out, CAN_HOST / "rte_qualifier_stall_host_test.cpp")
            self.assertEqual(build.returncode, 0, build.stderr[-4000:])
            print("\n" + run.stdout.strip(), file=sys.stderr)
            self.assertEqual(run.returncode, 0, run.stdout[-4000:])
            self.assertIn(", 0 failures", run.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def event_dbc(send_type=None, cycle=None, delay=None, enum=("Cyclic", "Spontaneous", "CyclicAndSpontaneous",
                                                             "IfActive", "OnWrite")) -> str:
    """COM-38: one sent message Tx (Flag, E2E fields) with the given GenMsgSendType, cycle and minimum gap."""
    lines = ['VERSION ""', "NS_ :", "BS_:", "BU_: EVB PC",
             "BO_ 256 Tx: 8 EVB",
             ' SG_ Flag : 0|1@1+ (1,0) [0|1] "" PC',
             ' SG_ Crc : 8|8@1+ (1,0) [0|255] "" PC',
             ' SG_ Counter : 16|4@1+ (1,0) [0|15] "" PC',
             'BA_DEF_ BO_  "GenMsgCycleTime" INT 0 65535;',
             'BA_DEF_ BO_  "GenMsgDelayTime" INT 0 65535;',
             'BA_DEF_ BO_  "GenMsgSendType" ENUM  ' + ",".join(f'"{v}"' for v in enum) + ";",
             'BA_DEF_DEF_  "GenMsgCycleTime" 0;',
             'BA_DEF_DEF_  "GenMsgDelayTime" 0;',
             f'BA_DEF_DEF_  "GenMsgSendType" "{enum[0]}";']
    if send_type is not None:
        lines.append(f'BA_ "GenMsgSendType" BO_ 256 {enum.index(send_type)};')
    if cycle is not None:
        lines.append(f'BA_ "GenMsgCycleTime" BO_ 256 {cycle};')
    if delay is not None:
        lines.append(f'BA_ "GenMsgDelayTime" BO_ 256 {delay};')
    return "\n".join(lines) + "\n"


class RteEventSendTest(unittest.TestCase):
    """COM-38: GenMsgSendType and GenMsgDelayTime of the DBC - what rtegen accepts, rejects and generates."""

    setUp, tearDown = RteGenTest.setUp, RteGenTest.tearDown
    run_with, expect_error = RteGenTest.run_with, RteGenTest.expect_error

    def ecu(self, dbc_text, e2e=None):
        dbc = self.tmp / "evt.dbc"
        dbc.write_text(dbc_text, encoding="utf-8")
        ecu = copy.deepcopy(NO_CAN)
        ecu["can"] = {"dbc": str(dbc), "node": "EVB"}
        if e2e is not None:
            ecu["can"]["qualifiers"] = {"Tx": {"e2e": e2e}}
        ecu["connections"]["DemoSwc.Led1.Led1"] = "can.tx.Tx.Flag"
        return ecu

    def generated(self):
        return (self.tmp / "out" / "RteConfig.cpp").read_text(encoding="utf-8")

    def test_send_modes_of_the_dbc(self):
        path = self.tmp / "modes.dbc"
        path.write_text(event_dbc("Spontaneous", delay=30), encoding="utf-8")
        msg = rtegen.dbcgen.load(path).message("Tx")
        self.assertEqual((msg.send_type, msg.send_mode, msg.delay_ms), ("Spontaneous", "event", 30))
        for text, mode in (("Cyclic", "cyclic"), ("", "cyclic"), ("NoMsgSendType", "cyclic"),
                           ("spontaneous", "event"), ("OnChange", "event"), ("SpontaneousWithDelay", "event"),
                           ("CyclicAndSpontaneous", "mixed"), ("CyclicAndOnChange", "mixed"),
                           ("IfActive", None), ("OnWrite", None), ("CyclicIfActive", None)):
            msg.send_type = text
            self.assertEqual(msg.send_mode, mode, text)
        path.write_text(event_dbc(), encoding="utf-8")                  # default of the ENUM: Cyclic
        msg = rtegen.dbcgen.load(path).message("Tx")
        self.assertEqual((msg.send_type, msg.send_mode, msg.delay_ms), ("Cyclic", "cyclic", 0))

    def test_event_message_is_sent_on_change(self):
        e2e = {"profile": "P01", "data_id": 0x123, "crc": "Crc", "counter": "Counter"}
        self.run_with(self.ecu(event_dbc("Spontaneous", delay=30), e2e))
        config = self.generated()
        self.assertIn('{"Tx", CANDB_TX_ID, 0U, 0U, 0U, 0U},', config)             # no cycle
        self.assertIn("// Tx: GenMsgSendType Spontaneous - on change, minimum gap 30 ms (COM-38)", config)
        self.assertIn("if ((changed || event.cyclicPending) && (event.sinceLast >= 3U))", config)
        self.assertIn("    txEventInit();\n", config)
        self.assertIn("#include <cstring>", config)
        self.assertNotIn("canTick % 0U", config)
        # E2E only on a send: the protect call sits inside the send branch
        branch = config[config.index("event.sinceLast >= 3U"):config.index("++CAN_TX_STATUS[0].sent;")]
        self.assertIn("E2E_P01Protect(", branch)

    def test_mixed_message_keeps_its_cycle(self):
        self.run_with(self.ecu(event_dbc("CyclicAndSpontaneous", cycle=50, delay=20)))
        config = self.generated()
        self.assertIn('{"Tx", CANDB_TX_ID, 50U, 0U, 0U, 0U},', config)
        self.assertIn("on change and every 50 ms, minimum gap 20 ms", config)
        self.assertIn("if ((canTick % 5U) == 0U)\n            {\n                event.cyclicPending = true;", config)

    def test_without_minimum_gap_no_comparison_with_zero(self):
        """-Wtype-limits of the target build: 'sinceLast >= 0U' would always be true."""
        self.run_with(self.ecu(event_dbc("Spontaneous")))
        config = self.generated()
        self.assertIn("            if (changed || event.cyclicPending)\n", config)
        self.assertNotIn(">= 0U", config)

    def test_cyclic_message_is_unchanged(self):
        """Default send type Cyclic: the code of before COM-38, no event state at all."""
        self.run_with(self.ecu(event_dbc(cycle=100, delay=30)))
        config = self.generated()
        self.assertIn("        if ((canTick % 10U) == 0U)\n        {\n            CanDb_Tx_t msg;", config)
        self.assertNotIn("TxEvent", config)
        # NVM-05: the demo project stores its ECU statistics, the NvM blocks need <cstring>; no event state does
        self.assertTrue("#include <cstring>" not in config or "nvmImage_" in config)

    def test_rejected_combinations(self):
        cases = [
            (event_dbc("IfActive", cycle=100), "has GenMsgSendType IfActive (evt.dbc), which the RTE does not send; "
                                               "supported: Cyclic, Spontaneous, CyclicAndSpontaneous"),
            (event_dbc("OnWrite"), "has GenMsgSendType OnWrite"),
            (event_dbc("Cyclic"), "message Tx has no GenMsgCycleTime (evt.dbc): a cyclic message needs one"),
            (event_dbc("CyclicAndSpontaneous", delay=20), "(cyclic and on change) but no GenMsgCycleTime"),
            (event_dbc("Spontaneous", cycle=100), "(on change only) and GenMsgCycleTime 100 ms (evt.dbc): set the "
                                                  "cycle time to 0 or use CyclicAndSpontaneous"),
            (event_dbc("CyclicAndSpontaneous", cycle=50, delay=60),
             "GenMsgDelayTime 60 ms is longer than GenMsgCycleTime 50 ms (evt.dbc)"),
            (event_dbc("Spontaneous", delay=15),
             "GenMsgDelayTime 15 ms of Tx (evt.dbc) is no multiple of the period 10 ms of the CAN task swc"),
        ]
        for dbc_text, fragment in cases:
            with self.subTest(fragment=fragment):
                with self.assertRaises(rtegen.RteError) as ctx:
                    self.run_with(self.ecu(dbc_text))
                text = str(ctx.exception)
                self.assertIn(fragment, text)
                # the place is the connection: file rte.yaml, YAML path connections.<port>
                self.assertTrue(text.startswith("connection DemoSwc.Led1.Led1 -> can.tx.Tx.Flag: "), text)
                message = rtegen.rte_message(text, self.tmp / "rte.yaml")
                self.assertEqual(message.path, ("connections", "DemoSwc.Led1.Led1"))


class RteEventHostTest(unittest.TestCase):
    """COM-38: compiles the generated RTE for test/can_host/event_rte.yaml (event, mixed, event without E2E and
    cyclic messages) and runs rte_event_host_test.cpp; then checks every E2E frame it printed against the
    reference model tools/e2e/e2e_ref.py: correct CRC, counter one step on per frame, event or cyclic."""

    def test_event_runtime(self):
        sys.path.insert(0, str(REPO / "tools" / "e2e"))
        import e2e_ref
        tmp = Path(tempfile.mkdtemp())
        try:
            out = tmp / "gen"
            rtegen.generate(CAN_HOST / "event_rte.yaml", PROJECT, out, REPO)
            build, run = build_can_host(tmp, out, CAN_HOST / "rte_event_host_test.cpp")
            self.assertEqual(build.returncode, 0, build.stderr[-4000:])
            print("\n" + run.stdout.strip()[-3000:], file=sys.stderr)
            self.assertEqual(run.returncode, 0, run.stdout[-4000:])
            self.assertIn(", 0 failures", run.stdout)
            frames = {}
            for line in run.stdout.splitlines():
                if line.startswith("FRAME "):
                    _, tick, can_id, *data = line.split()
                    frames.setdefault(int(can_id, 16), []).append((int(tick), bytes(int(b, 16) for b in data)))
            self.assertEqual([t for t, _ in frames[0x300]], [1, 4, 20])
            self.assertEqual([t for t, _ in frames[0x301]], [0, 5, 7, 9, 11, 15, 20, 25])
            p01 = e2e_ref.P01Config(counter_offset=16, crc_offset=8, data_id=0x0321,
                                    data_id_mode=e2e_ref.DATAID_BOTH, data_length=64)
            counter = 0                                          # E2E_P01ProtectInit: counter 0, then +1
            for tick, data in frames[0x300]:
                expected = bytearray(data)
                rc, counter_next = e2e_ref.p01_protect(p01, counter, expected)
                self.assertEqual((rc, bytes(expected)), (e2e_ref.E_OK, data), f"TxEvtP01 at cycle {tick}")
                counter = counter_next
            p02 = e2e_ref.P02Config(data_length=64, data_id_list=list(range(0x21, 0x31)))
            counter = 0                                          # P02 increments first: first frame 1
            for tick, data in frames[0x301]:
                expected = bytearray(data)
                rc, counter = e2e_ref.p02_protect(p02, counter, expected)
                self.assertEqual((rc, bytes(expected)), (e2e_ref.E_OK, data), f"TxMixP02 at cycle {tick}")
            self.assertEqual([d[1] & 0x0F for _, d in frames[0x301]], [1, 2, 3, 4, 5, 6, 7, 8])
            self.assertEqual([d[2] & 0x0F for _, d in frames[0x300]], [0, 1, 2])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


ECU_STATE_CONNECTIONS = {
    "DemoSwc.Button1.Button1": "ecu.wakeup.CAN0",
    "DemoSwc.MotorNtc_mV.MotorNtc_mV": "ecu.state",
    "DemoSwc.Led1.Led1": ["dio.out.LED1", "ecu.sleep_request"],
    "DemoSwc.Led2.Led2": ["dio.out.LED2", "ecu.sleep_request"],
}


class RteEcuStateTest(unittest.TestCase):
    """SYS-01: ecu.state / ecu.wakeup / ecu.wakeup.<source> / ecu.sleep_request and the section ecu_state:."""

    setUp, tearDown = RteGenTest.setUp, RteGenTest.tearDown
    run_with, expect_error = RteGenTest.run_with, RteGenTest.expect_error

    def ecu(self, **connections):
        ecu = copy.deepcopy(NO_CAN)
        ecu["connections"].update(connections)
        return ecu

    def generated(self, name="RteConfig.cpp"):
        return (self.tmp / "out" / name).read_text(encoding="utf-8")

    def test_wakeup_sources_of_the_demo_project(self):
        sources = rtegen.wakeup_sources(PROJECT)
        self.assertEqual(sources["POWER"], 0x1)
        self.assertEqual(sources["UNKNOWN"], 0x80)
        self.assertEqual(sources["CAN0"], 0x100)     # NX_CAN_CAN0_INDEX 0
        self.assertEqual(sources["LOCAL7"], 0x800000)
        self.assertEqual(sources["CAN1"], 0x200)     # COM-05: the demo uses the diagnostic CAN too (index 1)
        self.assertNotIn("CAN0", rtegen.wakeup_sources(None))
        # two buses: bit per bus in the order of the bus ids (ARCH-07)
        two = rtegen.wakeup_sources(PROJECT_TWO_BUSES)
        self.assertEqual((two["CAN0"], two["CAN1"]), (0x100, 0x200))

    def test_bits_match_the_module_header(self):
        header = (REPO / "libs/bsw/ecuState/include/ecuState/EcuState.h").read_text(encoding="utf-8")
        for name, bit in rtegen.WAKEUP_BITS.items():
            self.assertRegex(header, rf"constexpr uint32_t {name}\s*= 1UL << {bit}U;")
        self.assertIn(f"CAN_BUS_SHIFT = {rtegen.WAKEUP_CAN_SHIFT}U;", header)
        self.assertIn(f"LOCAL_SHIFT   = {rtegen.WAKEUP_LOCAL_SHIFT}U;", header)
        self.assertIn(f"CAN_BUS_COUNT = {rtegen.WAKEUP_GROUP_SIZE}U;", header)
        states = re.findall(r"^\s*(\w+)\s*= (\d)U,", header, re.M)
        # SYS-02 appended RUNDOWN; the values 0..4 of SYS-01 are the RTE interface and stay put
        self.assertEqual(states, [("STARTUP", "0"), ("RUN", "1"), ("STANDBY", "2"), ("SHUTDOWN", "3"),
                                  ("SLEEP", "4"), ("RUNDOWN", "5")])

    def test_without_ecu_targets_no_ecu_code(self):
        self.run_with(NO_CAN)
        self.assertNotIn("ecustate", self.generated())
        self.assertIn("#define NX_ECU_STATE_PARAM_MIN_RUN_MS 5000U // min_run_ms (default)",
                      self.generated("EcuStateConfig.h"))

    def test_targets_accepted(self):
        _, buffers, _ = self.run_with(self.ecu(**ECU_STATE_CONNECTIONS))
        config = self.generated()
        self.assertEqual((buffers["DemoSwc.Button1.Button1"].bsw, buffers["DemoSwc.Button1.Button1"].bsw_signal),
                         ("ecu.wakeup.", "0x00000100U"))
        self.assertIn("void readState(uint8_t& state);", config)
        self.assertIn("void readWakeupSources(uint32_t& sources);", config)
        self.assertIn("void writeSleepRequest(bool request);", config)
        self.assertIn("Rte_Buf_DemoSwc_MotorNtc_mV_MotorNtc_mV = static_cast<uint32>(state);", config)
        self.assertIn("Rte_Buf_DemoSwc_Button1_Button1 = ((wakeup & 0x00000100U) != 0U) ? 1U : 0U;", config)
        self.assertIn("::ecustate::rte::writeSleepRequest(\n        (Rte_Buf_DemoSwc_Led1_Led1 != 0U)\n"
                      "        && (Rte_Buf_DemoSwc_Led2_Led2 != 0U));", config)
        self.assertIn('"dio.out.LED1, ecu.sleep_request"', config)
        self.assertIn('"ecu.wakeup.CAN0"', config)

    def test_wakeup_mask_and_single_request(self):
        self.run_with(self.ecu(**{"DemoSwc.MotorNtc_mV.MotorNtc_mV": "ecu.wakeup", "DemoSwc.Led2.Led2": "ecu.sleep_request"}))
        config = self.generated()
        self.assertIn("Rte_Buf_DemoSwc_MotorNtc_mV_MotorNtc_mV = wakeup;", config)
        self.assertNotIn("readState", config)
        self.assertIn("::ecustate::rte::writeSleepRequest(\n        (Rte_Buf_DemoSwc_Led2_Led2 != 0U));", config)

    def test_target_errors(self):
        cases = [
            ({"DemoSwc.Button1.Button1": "ecu.wakeup.CAN2"}, "unknown wake-up source CAN2"),
            ({"DemoSwc.Button1.Button1": "ecu.phase"}, "unknown ECU state target"),
            # RTE-08: ecu.mode exists, but only for mode ports
            ({"DemoSwc.Button1.Button1": "ecu.mode"}, "ecu.mode connects a mode port"),
            ({"DemoSwc.Button1.Button1": "ecu.state"}, "type boolean does not fit ecu.state (uint16, uint32, uint8)"),
            ({"DemoSwc.MotorNtc_mV.MotorNtc_mV": "ecu.wakeup.POWER"}, "type uint32 does not fit ecu.wakeup.POWER"),
            ({"DemoSwc.Button1.Button1": "ecu.wakeup"}, "type boolean does not fit ecu.wakeup (uint32)"),
            ({"DemoSwc.Button1.Button1": "ecu.sleep_request"}, "input port cannot use ecu.sleep_request"),
            ({"DemoSwc.Led1.Led1": "ecu.state"}, "output port cannot use ecu.state"),
            ({"DemoSwc.MotorNtcFiltered_mV.MotorNtcFiltered_mV": "ecu.sleep_request"},
             "type uint32 does not fit ecu.sleep_request (boolean)"),
            ({"DemoSwc.Led1.Led1": ["ecu.sleep_request", "ecu.sleep_request"]}, "already connected to ecu.sleep_request"),
        ]
        for connections, fragment in cases:
            with self.subTest(connections=connections):
                self.expect_error(self.ecu(**connections), fragment)

    def project_file(self, values=None, base=PROJECT_FILE, rte=None):
        """Copy of the project `base` with modules.ecuState = values (SYS-08: the parameters are project values)."""
        doc = yaml.safe_load(base.read_text(encoding="utf-8"))
        doc["project"]["board"] = (base.parent / doc["project"]["board"]).resolve().as_posix()
        doc["project"].pop("diag", None)   # relative to the demo folder (ARCH-51)
        # TOOL-114: the RTE is required - without an own one the copy gets the smallest description
        doc["project"]["rte"] = (rte if rte is not None else empty_rte(self.tmp / "ecu_state_project")).as_posix()
        if values is not None:
            doc.setdefault("modules", {})["ecuState"] = values
        path = self.tmp / "ecu_state_project" / "project.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        return path

    def run_project(self, values, base=PROJECT_FILE, ecu=None):
        """Generates the RTE of `ecu` (default: the demo ECU without CAN) for the project with modules.ecuState."""
        path = self.tmp / "rte.yaml"
        path.write_text(yaml.safe_dump(self.ecu() if ecu is None else ecu), encoding="utf-8")
        config = rtegen.nxconfig.load(self.project_file(values, base))
        return rtegen.generate(path, config, self.tmp / "out", REPO)

    def project_errors(self, values, base=PROJECT_FILE):
        config, messages = rtegen.nxconfig.check(self.project_file(values, base))
        self.assertIsNone(config, "project with a faulty modules.ecuState must not load")
        return [m for m in messages if m.severity == "error"]

    def test_step_error_continue_is_refused_with_an_nvm_step(self):
        """SYS-174 (safety review 2026-10-02, SYS69-S5, ECS-F04): no step_error continue past the NvM step."""
        with self.assertRaises(rtegen.RteError) as ctx:
            self.run_project({"step_error": "continue"})          # test profile: data flash areas and NvM blocks
        text = str(ctx.exception)
        self.assertIn("modules.ecuState.step_error: continue is not allowed", text)
        self.assertIn("(NvM blocks EcuStatistics)", text)
        self.assertIn("use restart", text)
        # data flash areas alone make an NvM step as well (the gate of NVM-23)
        self.assertEqual(rtegen.ecu_state_nvm_step(None, PROJECT), "data flash areas CalMain, CalBackup, Test")
        self.run_project({"step_error": "restart"})                # restart is fine
        # a project without NvM step (no data flash areas, no NvM blocks) keeps continue
        two_buses = REPO / "projects" / "buildtest_s32k148_min" / "project.yaml"
        self.assertIsNone(yaml.safe_load(two_buses.read_text(encoding="utf-8")).get("nvm"))
        two_rte = two_buses.parent / "rte.yaml"
        self.run_project({"step_error": "continue"}, two_buses,
                         rtegen.with_resolved_paths(yaml.safe_load(two_rte.read_text(encoding="utf-8")), two_rte))
        self.assertIn("#define NX_ECU_STATE_PARAM_STEP_ERROR CONTINUE", self.generated("EcuStateConfig.h"))
        # the reason names the NvM blocks when there are some, without data flash
        plan = rtegen.NvmPlan(blocks=[types.SimpleNamespace(name="EcuStatistics")], layout_bytes=0, slot_max=0,
                              window_start=0, window_size=0, window_name="")
        self.assertEqual(rtegen.ecu_state_nvm_step(plan, None), "NvM blocks EcuStatistics")
        self.assertIsNone(rtegen.ecu_state_nvm_step(None, None))

    def test_parameters(self):
        self.run_project({"post_run_ms": 750, "wakeup_during_shutdown": "sleep", "verify": True})
        header = self.generated("EcuStateConfig.h")
        self.assertIn("#define NX_ECU_STATE_PARAM_POST_RUN_MS 750U // post_run_ms\n", header)
        self.assertIn("#define NX_ECU_STATE_PARAM_WAKEUP_DURING_SHUTDOWN SLEEP // wakeup_during_shutdown\n", header)
        self.assertIn("#define NX_ECU_STATE_PARAM_AFTER_WATCHDOG_RESET RUN // after_watchdog_reset (default)", header)
        self.assertIn("#define NX_ECU_STATE_VERIFY 1", header)
        self.assertIn("parameter values marked verify: not confirmed", header)
        self.assertIn("project.yaml modules.ecuState", header)

    def test_parameter_errors(self):
        """SYS-08: tools/nxconfig checks the project values, every message with project.yaml, line and YAML path."""
        cases = [
            ({"post_run": 1}, "post_run", "module ecuState has no parameter post_run"),
            ({"post_run_ms": -1}, "post_run_ms", "post_run_ms: -1 is out of range 0..3600000 ms"),
            ({"sleep_timeout_ms": 100}, "sleep_timeout_ms", "sleep_timeout_ms: 100 is out of range 101..3600000 ms"),
            ({"min_run_ms": True}, "min_run_ms", "min_run_ms: expected an integer"),
            ({"wakeup_during_shutdown": "abort"}, "wakeup_during_shutdown", "'abort' does not match"),
            ({"after_watchdog_reset": "Run"}, "after_watchdog_reset", "'Run' does not match"),
            ({"verify": "yes"}, "verify", "verify must be true or false"),
        ]
        for values, name, fragment in cases:
            with self.subTest(values=values):
                match = [m for m in self.project_errors(values) if fragment in m.text]
                self.assertTrue(match, fragment)
                self.assertEqual((match[0].file.name, match[0].yaml_path), ("project.yaml", f"modules.ecuState.{name}"))
                self.assertIsNotNone(match[0].line)
        errors = self.project_errors([1])
        self.assertTrue(any(m.yaml_path.startswith("modules.ecuState") for m in errors), [str(m) for m in errors])

    def test_section_in_the_ecu_description_is_refused(self):
        """SYS-08: the values stand in the project only; the old place is an error that names the new one."""
        ecu = self.ecu()
        ecu["ecu_state"] = {"post_run_ms": 750}
        self.expect_error(ecu, "ecu_state: the parameters of the ECU state management are project values, "
                               "move them to project.yaml modules.ecuState")
        ecu_file = self.tmp / "refused" / "rte.yaml"
        ecu_file.parent.mkdir(parents=True, exist_ok=True)
        ecu_file.write_text(yaml.safe_dump(ecu, sort_keys=False), encoding="utf-8")
        messages = rtegen.validate(self.project_file(rte=ecu_file), REPO)
        match = [m for m in messages if "move them to project.yaml modules.ecuState" in m.text]
        self.assertTrue(match, [str(m) for m in messages])
        self.assertEqual((match[0].file.name, match[0].yaml_path), ("rte.yaml", "ecu_state"))
        self.assertIsNotNone(match[0].line)

    def test_validate_reports_project_values(self):
        messages = rtegen.validate(self.project_file({"post_run_ms": -1}, rte=ECU_FILE), REPO)
        match = [m for m in messages if "post_run_ms: -1 is out of range" in m.text]
        self.assertTrue(match, [str(m) for m in messages])
        self.assertEqual((match[0].file.name, match[0].yaml_path), ("project.yaml", "modules.ecuState.post_run_ms"))
        self.assertIsNotNone(match[0].line)

    def test_defaults_without_project(self):
        values, verify = rtegen.load_ecu_state({}, None)
        manifest = rtegen.ecu_state_manifest(None)
        self.assertEqual(values, {n: s.default for n, s in manifest.parameters.items()})
        self.assertFalse(verify)

    def test_manifest_matches_the_module(self):
        # parameter order = EcuStateConfig fields in ECU_STATE_CONFIG(), limits = isValidEcuStateConfig
        manifest = rtegen.ecu_state_manifest(PROJECT)
        integration = (REPO / "libs/bsw/ecuState/include/ecuState/EcuStateIntegration.h").read_text(encoding="utf-8")
        body = integration.split("#define ECU_STATE_CONFIG()")[1]
        used = re.findall(r"NX_ECU_STATE_PARAM_(\w+)", body)
        # SYS-02: wakeup_can and wakeup_local become one mask instead of a value of their own
        self.assertEqual(used, [name.upper() for name in manifest.parameters
                                if name not in rtegen.WAKEUP_LIST_PARAMETERS])
        self.assertIn("NX_ECU_STATE_WAKEUP_SOURCES", body)
        for name in rtegen.WAKEUP_LIST_PARAMETERS:
            self.assertIn(name, manifest.parameters)
        header = (REPO / "libs/bsw/ecuState/include/ecuState/EcuState.h").read_text(encoding="utf-8")
        max_ms = int(re.search(r"MAX_TIME_MS = (\d+)U", header).group(1))
        delay = int(re.search(r"POWER_DOWN_DELAY_MS = (\d+)U", header).group(1))
        times = {n: s for n, s in manifest.parameters.items() if s.type == "int"}
        self.assertTrue(all(s.max == max_ms for s in times.values()))
        self.assertEqual(times["min_run_ms"].min, 0)
        self.assertEqual(times["post_run_ms"].min, 0)
        self.assertIn(f"(c.shutdownTimeoutMs >= {times['shutdown_timeout_ms'].min}U)", header)
        self.assertEqual(times["sleep_timeout_ms"].min, delay + 1)
        self.assertIn("(c.sleepTimeoutMs > POWER_DOWN_DELAY_MS)", header)
        for name in ("wakeup_during_shutdown", "after_watchdog_reset", "step_error"):
            for option in manifest.parameters[name].pattern.split("|"):
                self.assertRegex(header, rf"\n    {option.upper()},")
        # SYS-02: one step limit per run-down step of the module
        self.assertIn("(c.stepTimeoutMs[0] >= 10U)", header)
        self.assertEqual(times["nvm_timeout_ms"].min, 10)
        self.assertEqual(times["can_stop_timeout_ms"].min, 10)
        steps = (REPO / "libs/bsw/ecuState/include/ecuState/IShutdownStep.h").read_text(encoding="utf-8")
        # SYS-174: the third step SAFE_OUTPUTS has no project value - its limit is SAFE_OUTPUTS_STEP_TIMEOUT_MS
        self.assertEqual(len(re.findall(r"^\s*(\w+)\s*= \dU,", steps, re.M)), 3)
        self.assertIn("SHUTDOWN_STEP_COUNT = 3U", header)
        self.assertNotIn("safe_outputs_timeout_ms", manifest.parameters)
        self.assertRegex(header, r"SAFE_OUTPUTS_STEP_TIMEOUT_MS = \d+U;")

    def test_wakeup_selection(self):
        """SYS-02: wakeup_can / wakeup_local / wakeup_cyclic_ms become one mask in EcuStateConfig.h."""
        cases = [
            # the comment resolves "all" to the names, so the header says which sources are really armed
            ({}, 0x00000300, "wakeup_can CAN0,CAN1, wakeup_local none"),           # default: every bus (COM-05)
            ({"wakeup_can": "none"}, 0x00000000, "wakeup_can none, wakeup_local none"),
            ({"wakeup_can": "CAN0"}, 0x00000100, "wakeup_can CAN0, wakeup_local none"),
            # IO-33: only inputs the board wires as wake_input (eval board: HVIO2..4 = LOCAL1..3)
            ({"wakeup_local": "LOCAL1,LOCAL3"}, 0x000A0300, "wakeup_can CAN0,CAN1, wakeup_local LOCAL1,LOCAL3"),
            ({"wakeup_local": "all"}, 0x00FF0300, None),
            ({"wakeup_can": "none", "wakeup_cyclic_ms": 1000},
             0x00000010, "wakeup_can none, wakeup_local none, cyclic 1000 ms"),     # TIMER bit 4
        ]
        for values, mask, comment in cases:
            with self.subTest(values=values):
                self.run_project(values)
                header = self.generated("EcuStateConfig.h")
                self.assertIn(f"#define NX_ECU_STATE_WAKEUP_SOURCES 0x{mask:08X}U", header)
                if comment is not None:
                    self.assertIn(f"0x{mask:08X}U // {comment}", header)
        # the names stay readable in the generated header, but as text: the mask is what the code uses
        self.run_project({"wakeup_can": "CAN0", "wakeup_local": "LOCAL2"})
        header = self.generated("EcuStateConfig.h")
        self.assertIn('#define NX_ECU_STATE_PARAM_WAKEUP_CAN "CAN0" // wakeup_can\n', header)
        self.assertIn('#define NX_ECU_STATE_PARAM_WAKEUP_LOCAL "LOCAL2" // wakeup_local\n', header)
        # two buses: one bit per bus (ARCH-07)
        mask, resolved = rtegen.ecu_state_wakeup(
            {"wakeup_can": "CAN1", "wakeup_local": "none", "wakeup_cyclic_ms": 0}, PROJECT_TWO_BUSES)
        self.assertEqual((mask, resolved["wakeup_can"]), (0x200, "CAN1"))
        mask, _ = rtegen.ecu_state_wakeup(
            {"wakeup_can": "all", "wakeup_local": "none", "wakeup_cyclic_ms": 0}, PROJECT_TWO_BUSES)
        self.assertEqual(mask, 0x300)

    def test_wakeup_selection_errors(self):
        """A source that does not exist on this board is an error with file, line and YAML path (SYS-02, SYS-08)."""
        two_buses = REPO / "projects" / "buildtest_s32k148_min" / "project.yaml"
        cases = [
            ({"wakeup_can": "CAN7"}, PROJECT_FILE,
             "wakeup_can: CAN7 is no CAN bus of the project (use.can: CAN0, CAN1)"),
            ({"wakeup_can": "CAN0,CAN2"}, two_buses,
             "wakeup_can: CAN2 is no CAN bus of the project (use.can: CAN0, CAN1)"),
            ({"wakeup_can": "CAN0,CAN0"}, PROJECT_FILE, "wakeup_can: a CAN bus is named twice (CAN0,CAN0)"),
            ({"wakeup_can": "can0"}, PROJECT_FILE, "wakeup_can: can0 is no CAN bus of the project"),
            ({"wakeup_local": "LOCAL8"}, PROJECT_FILE, "wakeup_local: 'LOCAL8' does not match"),
            ({"wakeup_local": "LOCAL1,LOCAL1"}, PROJECT_FILE, "wakeup_local: a name is named twice (LOCAL1,LOCAL1)"),
            ({"wakeup_cyclic_ms": -1}, PROJECT_FILE, "wakeup_cyclic_ms: -1 is out of range 0..3600000 ms"),
            ({"step_error": "abort"}, PROJECT_FILE, "step_error: 'abort' does not match"),
            ({"nvm_timeout_ms": 9}, PROJECT_FILE, "nvm_timeout_ms: 9 is out of range 10..3600000 ms"),
            ({"can_stop_timeout_ms": 0}, PROJECT_FILE, "can_stop_timeout_ms: 0 is out of range 10..3600000 ms"),
        ]
        for values, base, fragment in cases:
            with self.subTest(values=values):
                match = [m for m in self.project_errors(values, base) if fragment in m.text]
                self.assertTrue(match, fragment)
                self.assertEqual((match[0].file.name, match[0].yaml_path),
                                 ("project.yaml", f"modules.ecuState.{next(iter(values))}"))
                self.assertIsNotNone(match[0].line)
        # both buses of the two-bus profile are accepted, one bit per bus (ARCH-07)
        two_rte = two_buses.parent / "rte.yaml"
        self.run_project({"wakeup_can": "CAN1,CAN0"}, two_buses,       # RTE-22: rendered from a scratch folder
                         rtegen.with_resolved_paths(yaml.safe_load(two_rte.read_text(encoding="utf-8")), two_rte))
        self.assertIn("#define NX_ECU_STATE_WAKEUP_SOURCES 0x00000300U", self.generated("EcuStateConfig.h"))


class RteEcuStateHostTest(unittest.TestCase):
    """Compiles the generated RTE with ECU state targets and the real hand-over (EcuStateRte.cpp), runs it."""

    def test_ecu_state_runtime(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            ecu = copy.deepcopy(NO_CAN)
            ecu["connections"].update(ECU_STATE_CONNECTIONS)
            ecu_file = tmp / "rte.yaml"
            ecu_file.write_text(yaml.safe_dump(ecu), encoding="utf-8")
            out = tmp / "gen"
            rtegen.generate(ecu_file, PROJECT, out, REPO)
            host = HERE / "test"
            zig = [sys.executable, "-m", "ziglang"]
            includes = [f"-I{out}", f"-I{out / 'tasks'}", f"-I{REPO / 'libs/bsw/autosarBase/include'}", f"-I{host / 'can_host' / 'stubs'}",
                        f"-I{REPO / 'swc/DemoSwc/generated/src'}", f"-I{REPO / 'libs/bsw/ecuState/include'}"]
            objects = []
            for src in (out / "Rte.c", REPO / "swc/DemoSwc/generated/src/DemoSwc.c"):
                obj = tmp / (src.stem + ".o")
                r = subprocess.run(zig + ["cc", "-std=c99", "-O1", "-Wall", "-Wextra", "-Werror", *includes, "-c",
                                          str(src), "-o", str(obj)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr[-3000:])
                objects.append(str(obj))
            exe = tmp / "rte_ecu_state_host_test.exe"
            r = subprocess.run(zig + ["c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion", "-Werror",
                                      *includes, str(out / "RteConfig.cpp"),
                                      str(REPO / "libs/bsw/ecuState/src/ecuState/EcuStateRte.cpp"),
                                      str(host / "ecu_state_host" / "rte_ecu_state_host_test.cpp"), *objects,
                                      "-o", str(exe)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            r = subprocess.run([str(exe)], capture_output=True, text=True)
            print("\n" + r.stdout.strip(), file=sys.stderr)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:])
            self.assertIn(", 0 failures", r.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


MULTI = HERE / "test" / "multitask"
MULTI_ECU = rtegen.with_resolved_paths(
    yaml.safe_load((MULTI / "rte.yaml").read_text(encoding="utf-8")), MULTI / "rte.yaml")
MULTI_TASKS = MULTI / "tasks.yaml"  # own BSW task profile, not ecu/asyncCoreConfiguration/tasks.yaml


class RteMultiTaskTest(unittest.TestCase):
    """RTE-02: application tasks, runnable assignment with offset and order, priorities between BSW tasks, data
    consistency plan; two synthetic components (test/multitask) and an own BSW task profile."""

    setUp, tearDown = RteGenTest.setUp, RteGenTest.tearDown

    def run_multi(self, ecu, manifest=MULTI_TASKS):
        path = self.tmp / "rte.yaml"
        path.write_text(yaml.safe_dump(ecu, sort_keys=False), encoding="utf-8")
        return rtegen.generate(path, PROJECT, self.tmp / "out", REPO, manifest)

    def expect(self, ecu, fragment, manifest=MULTI_TASKS):
        with self.assertRaises(rtegen.RteError) as ctx:
            self.run_multi(ecu, manifest)
        self.assertIn(fragment, str(ctx.exception))

    def generated(self, name):
        return (self.tmp / "out" / name).read_text(encoding="utf-8")

    def test_access_points_are_parsed(self):
        comp = rtegen.parse_component("AppSwc", MULTI / "AppSwc" / "generated" / "arxml")
        runnables = {r.name: r for r in comp.runnables}
        self.assertTrue(comp.access_points)
        self.assertEqual(runnables["Run10ms"].accesses,
                         {"Speed.Speed", "Wide.Wide", "Setpoint.Setpoint", "Led.Led"})
        self.assertEqual(runnables["Run50ms"].accesses, {"Speed.Speed", "SlowCount.SlowCount"})
        self.assertEqual(runnables["AppSwc_Init"].accesses, {"Setpoint.Setpoint"})
        demo = rtegen.parse_component("DemoSwc", REPO / "swc" / "DemoSwc" / "generated" / "arxml")
        self.assertEqual({r.name: r.accesses for r in demo.runnables}["Run100ms"],
                         {"MotorNtc_mV.MotorNtc_mV", "MotorNtcFiltered_mV.MotorNtcFiltered_mV"})

    def test_without_access_points_every_runnable_uses_every_port(self):
        folder = self.tmp / "arxml"
        folder.mkdir()
        text = (MULTI / "AppSwc" / "generated" / "arxml" / "AppSwc.arxml").read_text(encoding="utf-8")
        text = re.sub(r"\s*<DATA-(RECEIVE-POINT-BY-ARGUMENTS|SEND-POINTS)>.*?</DATA-\1>", "", text, flags=re.S)
        (folder / "AppSwc.arxml").write_text(text, encoding="utf-8")
        comp = rtegen.parse_component("AppSwc", folder)
        self.assertFalse(comp.access_points)
        self.assertEqual({r.name: len(r.accesses) for r in comp.runnables},
                         {"AppSwc_Init": 0, "Run10ms": 5, "Run50ms": 5})

    def test_tasks_and_priorities(self):
        _, _, tasks = self.run_multi(MULTI_ECU)
        self.assertEqual([(t.name, t.period_ms, t.os_priority, t.hyper_ticks) for t in tasks],
                         [("motor", 1, 3, 1), ("app", 10, 2, 5)])
        config = self.generated("tasks/async/Config.h")
        enum = re.findall(r"^\s+(TASK_\w+)(?: = 1)?, /\* priority (\d+), key (\d+)", config, re.M)
        self.assertEqual(enum, [("TASK_COMM", "1", "20"), ("TASK_APP", "2", "25"), ("TASK_MOTOR", "3", "35"),
                                ("TASK_IO", "4", "40"), ("TASK_GUARD", "5", "90")])
        self.assertIn("#define ASYNC_CONFIG_TICK_IN_US (500U)", config)
        self.assertIn('#define ASYNC_CONFIG_ISR_GROUP_NAMES "timer", "comm", "adc"', config)
        self.assertIn("    X(motor, TASK_MOTOR, 1024U) \\\n    X(app, TASK_APP, 2048U)\n", self.generated("RteTasks.h"))
        rte = self.generated("RteConfig.cpp")
        self.assertIn('{"AppSwc.Run50ms", &Run50ms, 50U, 20U, 1U, 5U, 2U},', rte)
        self.assertLess(rte.index('"MotorSwc.RunDiag10ms"'), rte.index('"AppSwc.Run10ms"'))
        self.assertIn('{"motor", TASK_MOTOR, 1U, 0U, 1U, 1U},\n    {"app", TASK_APP, 10U, 1U, 3U, 5U},', rte)

    def test_demo_task_list_is_unchanged(self):
        # the demo project keeps the task order and priorities of the hand-written Config.h before RTE-02
        rtegen.generate(ECU_FILE, PROJECT, self.tmp / "out", REPO)
        enum = re.findall(r"^\s+(TASK_\w+)", self.generated("tasks/async/Config.h"), re.M)
        self.assertEqual(enum, ["TASK_BACKGROUND", "TASK_BSP", "TASK_SWC", "TASK_CAN", "TASK_SYSADMIN", "TASK_SAFETY"])
        self.assertIn("X(swc, TASK_SWC, 2048U)", self.generated("RteTasks.h"))
        self.assertNotIn("Rte_Cpy_", self.generated("Rte.c"))  # one task: no copies, no locks
        # COM-28: the rte::diag functions behind the task code lock by design (called from the diagnostic task)
        task_code = self.generated("RteConfig.cpp").split("// ---- task swc")[1].split("// ---- COM-28")[0]
        self.assertNotIn("LockType", task_code)

    def test_consistency_plan(self):
        _, buffers, _ = self.run_multi(MULTI_ECU)
        speed = buffers["MotorSwc.Speed.Speed"]
        self.assertTrue(speed.shared)
        self.assertEqual(speed.copies, {"motor": "w", "app": "r"})
        setpoint = buffers["AppSwc.Setpoint.Setpoint"]
        self.assertEqual(setpoint.copies, {"app": "w"})  # MotorSwc reads it from two tasks: no copy
        self.assertFalse(buffers["MotorSwc.Enable.Enable"].shared)
        self.assertFalse(buffers["AppSwc.Led.Led"].shared)
        self.assertEqual(buffers["AppSwc.Led.Led"].io_task.name, "app")
        self.assertEqual(buffers["MotorSwc.Enable.Enable"].io_task.name, "motor")
        rte_c = self.generated("Rte.c")
        self.assertIn("*data = (uint32)Rte_Cpy_app_MotorSwc_Speed_Speed;", rte_c)
        self.assertIn("Rte_Cpy_motor_MotorSwc_Speed_Speed = (uint32)data;", rte_c)
        self.assertIn("/* Rte_Read_MotorSwc_Setpoint_Setpoint: RteConfig.cpp (used by several tasks) */", rte_c)
        config = self.generated("RteConfig.cpp")
        self.assertIn("Std_ReturnType Rte_Read_MotorSwc_Setpoint_Setpoint(uint16* data)\n{\n"
                      "    ::async::LockType const lock;", config)
        app_in = config.split("void readInputs_app()")[1].split("void writeOutputs_app()")[0]
        self.assertIn("Rte_Cpy_app_MotorSwc_Speed_Speed = Rte_Buf_MotorSwc_Speed_Speed;", app_in)
        motor_out = config.split("void writeOutputs_motor()")[1].split("} // namespace")[0]
        self.assertIn("Rte_Buf_MotorSwc_Wide_Wide = Rte_Cpy_motor_MotorSwc_Wide_Wide;", motor_out)
        self.assertIn("::bios::Output::LED1", config.split("void writeOutputs_app()")[1])

    def test_can_task(self):
        dbc = self.tmp / "t.dbc"
        dbc.write_text(CAN_DBC, encoding="utf-8")
        ecu = copy.deepcopy(MULTI_ECU)
        ecu["can"] = {"dbc": str(dbc), "node": "EVB"}
        ecu["connections"]["AppSwc.SlowCount.SlowCount"] = "can.tx.Tx.Wide"
        self.expect(ecu, "can.task: required with several application tasks (motor, app)")
        ecu["can"]["task"] = "nope"
        self.expect(ecu, "can.task: nope is no application task (tasks: motor, app)")
        ecu["can"]["task"] = "app"
        dbc.write_text(CAN_DBC.replace('BO_ 256 30;', 'BO_ 256 25;'), encoding="utf-8")
        self.expect(ecu, "cycle time 25 ms is no multiple of the period 10 ms of the CAN task app (can.task)")
        dbc.write_text(CAN_DBC, encoding="utf-8")
        ecu["can"]["task"] = "motor"
        _, buffers, _ = self.run_multi(ecu)
        self.assertEqual(buffers["AppSwc.SlowCount.SlowCount"].copies, {"app": "w", "motor": "r"})
        config = self.generated("RteConfig.cpp")
        self.assertIn("CanDb_Tx_Wide_FromInt(static_cast<int64_t>(Rte_Cpy_motor_AppSwc_SlowCount_SlowCount))", config)
        self.assertIn("if ((canTick % 30U) == 0U)", config)
        self.assertIn("writeCan();", config.split("void writeOutputs_motor()")[1].split("} // namespace")[0])

    def test_task_errors(self):
        def with_task(index, **changes):
            ecu = copy.deepcopy(MULTI_ECU)
            ecu["tasks"][index].update(changes)
            return ecu

        cases = [
            (with_task(0, priority=25), "tasks[1].priority: 25 is already used by task motor (tasks[0])"),
            (with_task(1, priority=40), "tasks[1].priority: 40 is already used by BSW task io"),
            (with_task(0, priority=95), "tasks[0].priority: 95 is above BSW task guard (90)"),
            (with_task(0, priority=0), "tasks[0].priority: Input should be greater than 0"),      # schema
            (with_task(0, name="io"), "tasks[0].name: io is already used by BSW task"),
            (with_task(1, name="motor"), "tasks[1].name: motor is already used by task (tasks[0])"),
            (with_task(0, name="timer"), "tasks[0].name: task name timer is reserved"),            # schema
            (with_task(0, name="Motor_1"), "tasks[0].name: String should match pattern '^[a-z][a-z0-9]*$'"),
            (with_task(0, period_ms=0), "tasks[0].period_ms: Input should be greater than 0"),
            (with_task(0, period_ms="1"), "tasks[0].period_ms: Input should be a valid integer"),
            (with_task(0, stack_bytes=100), "tasks[0].stack_bytes: 100 is below the minimum 256"),  # manifest
            (with_task(0, prio=3), "tasks[0].prio: Extra inputs are not permitted"),
            (with_task(0, period_ms=3), "tasks[0].runnables[0]: period 1 ms of MotorSwc.Run1ms is no multiple of "
                                        "the period 3 ms of task motor"),
            (with_task(1, runnables=["MotorSwc.RunDiag10ms", "AppSwc.Run10ms",
                                     {"runnable": "AppSwc.Run50ms", "offset_ms": 25}]),
             "tasks[1].runnables[2].offset_ms: 25 ms is no multiple of the period 10 ms of task app"),
            (with_task(1, runnables=["MotorSwc.RunDiag10ms", "AppSwc.Run10ms",
                                     {"runnable": "AppSwc.Run50ms", "offset_ms": 50}]),
             "tasks[1].runnables[2].offset_ms: 50 ms must be below the period 50 ms of AppSwc.Run50ms"),
            (with_task(1, runnables=["MotorSwc.RunDiag10ms", "AppSwc.Run10ms", "AppSwc.Run50ms", "AppSwc.Run10ms"]),
             "tasks[1].runnables[3]: AppSwc.Run10ms is already assigned to task app"),
            (with_task(1, runnables=["MotorSwc.RunDiag10ms", "AppSwc.Run10ms"]),
             "tasks: runnable AppSwc.Run50ms is not assigned to a task"),
            (with_task(1, runnables=["MotorSwc.RunDiag10ms", "AppSwc.Run10ms", "AppSwc.Run50ms", "AppSwc.AppSwc_Init"]),
             "tasks[1].runnables[3]: AppSwc.AppSwc_Init is an init runnable"),
            (with_task(1, runnables=["MotorSwc.RunDiag10ms", "AppSwc.Run10ms", "AppSwc.Run5ms"]),
             "tasks[1].runnables[2]: unknown runnable AppSwc.Run5ms"),
            (with_task(1, runnables=["AppSwc.Run10ms", {"runnable": "AppSwc.Run50ms", "offset": 20}]),
             "tasks[1].runnables[1].offset: Extra inputs are not permitted"),
            (with_task(1, runnables=["AppSwc.Run10ms", 5]),
             "tasks[1].runnables[1]: Input should be a valid dictionary or instance of RunnableRef"),
        ]
        for ecu, fragment in cases:
            with self.subTest(fragment=fragment):
                self.expect(ecu, fragment)
        ecu = copy.deepcopy(MULTI_ECU)
        ecu["tasks"] = []
        self.expect(ecu, "tasks: List should have at least 1 item")                            # schema
        ecu = copy.deepcopy(MULTI_ECU)
        del ecu["tasks"]  # without tasks: default task of the profile with the gcd of the periods
        _, _, tasks = self.run_multi(ecu)
        self.assertEqual([(t.name, t.period_ms, t.priority, t.stack_bytes) for t in tasks], [("app", 1, 30, 1024)])

    def test_manifest_limits_and_errors(self):
        manifest = yaml.safe_load(MULTI_TASKS.read_text(encoding="utf-8"))
        path = self.tmp / "tasks.yaml"

        def expect_manifest(data, fragment, ecu=MULTI_ECU):
            path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
            self.expect(ecu, fragment, path)

        data = copy.deepcopy(manifest)
        data["max_priorities"] = 6  # 5 tasks + idle + timer = 7
        expect_manifest(data, "tasks: 5 tasks plus idle and timer need more than 6 FreeRTOS priorities")
        data = copy.deepcopy(manifest)
        data["tick_us"] = 3000
        expect_manifest(data, "tasks[0].period_ms: 1 ms is no multiple of the OS tick 3000 us")
        data = copy.deepcopy(manifest)
        data["tasks"].append({"name": "io", "priority": 70})
        expect_manifest(data, "tasks[3]: task name or priority used twice")
        data = copy.deepcopy(manifest)
        data["tasks"][0]["above_application"] = "yes"
        expect_manifest(data, "tasks[0].above_application: expected true or false")
        data = copy.deepcopy(manifest)
        data["isr_groups"] = ["ok", "Not OK"]
        expect_manifest(data, "isr_groups[1]: 'Not OK' is no valid name")
        data = copy.deepcopy(manifest)
        del data["application_default"]
        expect_manifest(data, "application_default: expected name, priority and stack_bytes")

    def test_repository_manifest(self):
        manifest = rtegen.load_task_manifest(REPO / rtegen.TASKS_MANIFEST)
        self.assertEqual([t["name"] for t in manifest["tasks"]], ["background", "bsp", "can", "sysadmin", "safety"])
        self.assertEqual(manifest["isr_groups"], ["test", "can"])
        # the BSW task objects in app.cpp / the board integration must match the manifest
        app = (REPO / "ecu/application/src/app/app.cpp").read_text(encoding="utf-8")
        for task in manifest["tasks"]:
            self.assertRegex(app, rf"(Task|TaskStack)<TASK_{task['name'].upper()}\b")


class RteMultiTaskHostTest(unittest.TestCase):
    """RTE-02: compiles the generated RTE for test/multitask (two components, two tasks) with the synthetic components
    and runs test/multitask/rte_multitask_host_test.cpp."""

    def test_multitask_runtime(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            out = tmp / "gen"
            rtegen.generate(MULTI / "rte.yaml", PROJECT, out, REPO, MULTI_TASKS)
            zig = [sys.executable, "-m", "ziglang"]
            includes = [f"-I{out}", f"-I{out / 'tasks'}", f"-I{REPO / 'libs/bsw/autosarBase/include'}",
                        f"-I{HERE / 'test' / 'can_host' / 'stubs'}", f"-I{MULTI / 'MotorSwc/generated/src'}",
                        f"-I{MULTI / 'AppSwc/generated/src'}"]
            objects = []
            for src in (out / "Rte.c", MULTI / "MotorSwc/generated/src/MotorSwc.c",
                        MULTI / "AppSwc/generated/src/AppSwc.c"):
                obj = tmp / (src.stem + ".o")
                r = subprocess.run(zig + ["cc", "-std=c99", "-O1", "-Wall", "-Wextra", "-Werror", *includes, "-c",
                                          str(src), "-o", str(obj)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr[-3000:])
                objects.append(str(obj))
            exe = tmp / "rte_multitask_host_test.exe"
            r = subprocess.run(zig + ["c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror", *includes,
                                      str(out / "RteConfig.cpp"), str(MULTI / "rte_multitask_host_test.cpp"),
                                      *objects, "-o", str(exe)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            r = subprocess.run([str(exe)], capture_output=True, text=True)
            print("\n" + r.stdout.strip(), file=sys.stderr)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:])
            self.assertIn(", 0 failures", r.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


BSW_SERVICES_DIR = HERE / "test" / "bsw_services"
BSW_SERVICES_ECU = rtegen.with_resolved_paths(
    yaml.safe_load((BSW_SERVICES_DIR / "rte.yaml").read_text(encoding="utf-8")),
    BSW_SERVICES_DIR / "rte.yaml")
BSW_SERVICE_HEADER = REPO / "ecu" / "rte" / "services" / "include" / "bswService" / "BswServiceRte.h"


class RteBswServiceTest(unittest.TestCase):
    """RTE-05: BSW service ports bsw.power.*, bsw.reset.*, bsw.time.* with the test profile test/bsw_services
    (project buildtest_s32k148_min without SBC, BSW task profile test/multitask/tasks.yaml)."""

    setUp, tearDown = RteGenTest.setUp, RteGenTest.tearDown

    def run_with(self, ecu, project=PROJECT_TWO_BUSES, out="out"):
        path = self.tmp / "rte.yaml"
        path.write_text(yaml.safe_dump(ecu), encoding="utf-8")
        return rtegen.generate(path, project, self.tmp / out, REPO, MULTI_TASKS)

    def expect_error(self, ecu, fragment):
        with self.assertRaises(rtegen.RteError) as ctx:
            self.run_with(ecu)
        self.assertIn(fragment, str(ctx.exception))

    def ecu(self, **connections):
        ecu = copy.deepcopy(BSW_SERVICES_ECU)
        ecu["connections"].update(connections)
        return ecu

    def generated(self, out="out"):
        return (self.tmp / out / "RteConfig.cpp").read_text(encoding="utf-8")

    def task_body(self, task, out="out"):
        config = self.generated(out)
        return config.split(f"void readInputs_{task}()")[1].split("void writeOutputs_")[0]

    def test_targets_of_the_test_profile(self):
        _, buffers, tasks = self.run_with(BSW_SERVICES_ECU)
        self.assertEqual([t.name for t in tasks], ["motor", "app"])
        kinds = {key.split(".")[1]: (b.bsw, b.bsw_signal, b.io_task.name) for key, b in buffers.items() if b.bsw}
        self.assertEqual(kinds["PowerState"], ("bsw.power.state", "power", "app"))
        self.assertEqual(kinds["ResetQlf"], ("bsw.reset.qlf", "reset", "app"))
        self.assertEqual(kinds["FastUs"], ("bsw.time.us", "time", "motor"))
        self.assertEqual(kinds["FastReason"], ("bsw.reset.reason", "reset", "motor"))
        # every port is read in one task: no task copies, no lock
        self.assertFalse(any(b.shared for b in buffers.values()))
        self.assertEqual(rtegen.bsw_service_warnings(buffers), [])
        config = self.generated()
        self.assertIn("void readPower(uint8_t& power_state, uint16_t& power_mv, uint8_t& power_health);", config)
        self.assertIn("void readReset(uint8_t& reset_reason, uint32_t& reset_sources, uint8_t& reset_health);", config)
        self.assertIn("void readTime(uint32_t& time_ms, uint32_t& time_us);", config)
        app, motor = self.task_body("app"), self.task_body("motor")
        # one call per service and task, only the services the task reads
        for name in ("readPower", "readReset", "readTime"):
            self.assertEqual(app.count(f"::bswservice::rte::{name}("), 1, name)
        self.assertNotIn("readPower", motor)
        self.assertEqual(motor.count("::bswservice::rte::readReset("), 1)
        self.assertEqual(motor.count("::bswservice::rte::readTime("), 1)
        self.assertIn("Rte_Buf_ServiceSwc_ResetReason_ResetReason = static_cast<uint16>(reset_reason);", app)
        self.assertIn("Rte_Buf_ServiceSwc_PowerOk_PowerOk = ((power_health == 1U) && (power_state == 1U)) ? 1U : 0U;",
                      app)
        self.assertIn("Rte_Buf_ServiceSwc_ResetWatchdog_ResetWatchdog = ((reset_sources & 0x00000004U) != 0U) ? 1U : 0U;",
                      app)
        self.assertIn("Rte_Buf_ServiceSwc_ResetPowerOn_ResetPowerOn = ((reset_sources & 0x00000001U) != 0U) ? 1U : 0U;",
                      app)
        self.assertIn("Rte_Buf_ServiceSwc_ResetQlf_ResetQlf = static_cast<uint32>(((reset_health == 1U) ? QLF_OK : "
                      "((reset_health == 2U) ? QLF_ERROR : QLF_DEBOUNCE)));", app)
        self.assertIn("Rte_Buf_ServiceSwc_FastUs_FastUs = static_cast<uint32>(time_us);", motor)
        # the signal table names the service target (console "rte signals")
        self.assertIn('{"ServiceSwc.SupplyMv.SupplyMv", "bsw.power.supply_mv", SignalType::UINT16,', config)

    def test_code_does_not_depend_on_the_board(self):
        """The same connections give the same service code on a board with SBC and a board without SBC."""
        self.run_with(BSW_SERVICES_ECU, PROJECT_TWO_BUSES, "no_sbc")
        self.run_with(BSW_SERVICES_ECU, PROJECT, "sbc")
        for task in ("app", "motor"):
            self.assertEqual(self.task_body(task, "no_sbc"), self.task_body(task, "sbc"))

    def test_without_bsw_targets_no_service_code(self):
        ecu = self.ecu()
        ecu["connections"] = {k: ("rte" if k.startswith("ServiceSwc.Count") else "rte.init")
                              for k in ecu["connections"]}
        self.run_with(ecu)
        self.assertNotIn("bswservice", self.generated())

    def test_single_value_reads_its_service_only(self):
        ecu = self.ecu()
        ecu["connections"] = {k: ("rte" if k.startswith("ServiceSwc.Count") else "rte.init")
                              for k in ecu["connections"]}
        ecu["connections"]["ServiceSwc.TimeMs.TimeMs"] = "bsw.time.ms"
        self.run_with(ecu)
        config = self.generated()
        self.assertIn("void readTime(", config)
        self.assertNotIn("readPower", config)
        self.assertNotIn("readReset", config)
        self.assertIn("Rte_Buf_ServiceSwc_TimeMs_TimeMs = static_cast<uint32>(time_ms);", self.task_body("app"))

    def test_target_errors(self):
        cases = [
            ({"ServiceSwc.PowerState.PowerState": "bsw.sbc.state"},
             "unknown BSW service sbc (known: power, reset, time; targets are named by service, not by the device"),
            ({"ServiceSwc.PowerState.PowerState": "bsw.power.mode"},
             "unknown value mode of the BSW service power (known: state, ok, supply_mv, qlf)"),
            ({"ServiceSwc.PowerState.PowerState": "bsw.power"}, "expected bsw.<service>.<value>"),
            ({"ServiceSwc.PowerState.PowerState": "bsw.power.state.x"}, "expected bsw.<service>.<value>"),
            ({"ServiceSwc.Count.Count": "bsw.time.ms"},
             "output port cannot use bsw.time.ms (BSW service ports are inputs)"),
            ({"ServiceSwc.Count.Count": ["rte", "bsw.time.ms"]}, "output port cannot use bsw.time.ms"),
            ({"ServiceSwc.PowerOk.PowerOk": "bsw.time.ms"}, "type boolean does not fit bsw.time.ms (uint32,"),
            ({"ServiceSwc.PowerState.PowerState": "bsw.power.supply_mv"},
             "type uint8 does not fit bsw.power.supply_mv (uint16, uint32, supply voltage in mV"),
            ({"ServiceSwc.TimeMs.TimeMs": "bsw.reset.watchdog"}, "type uint32 does not fit bsw.reset.watchdog (boolean"),
            ({"ServiceSwc.SupplyMv.SupplyMv": "bsw.time.us"}, "type uint16 does not fit bsw.time.us (uint32"),
        ]
        for connections, fragment in cases:
            with self.subTest(connections=connections):
                self.expect_error(self.ecu(**connections), fragment)

    def test_message_has_the_yaml_path(self):
        text = "connection ServiceSwc.PowerState.PowerState -> bsw.sbc.state: unknown BSW service sbc"
        ecu_file = self.tmp / "rte.yaml"
        ecu_file.write_text(yaml.safe_dump(self.ecu(**{"ServiceSwc.PowerState.PowerState": "bsw.sbc.state"}),
                                           sort_keys=False), encoding="utf-8")
        _, _, tree = rtegen.nxconfig.config.read_rte(ecu_file)
        message = rtegen.rte_message(text, ecu_file, tree)
        self.assertEqual(message.yaml_path, "connections.ServiceSwc.PowerState.PowerState")
        self.assertIsNotNone(message.line)

    def test_warning_without_qualifier(self):
        ecu = self.ecu(**{"ServiceSwc.PowerQlf.PowerQlf": "rte.init", "ServiceSwc.ResetQlf.ResetQlf": "rte.init"})
        _, buffers, _ = self.run_with(ecu)
        warnings = rtegen.bsw_service_warnings(buffers)
        self.assertEqual(len(warnings), 2, warnings)
        self.assertTrue(warnings[0].startswith("connections: bsw.power.qlf is not connected"), warnings)
        self.assertTrue(warnings[1].startswith("connections: bsw.reset.qlf is not connected"), warnings)
        # time has no qualifier: nothing to warn about
        _, buffers, _ = self.run_with(self.ecu(**{"ServiceSwc.PowerQlf.PowerQlf": "rte.init"}))
        self.assertEqual([w.split(" is ")[0] for w in rtegen.bsw_service_warnings(buffers)],
                         ["connections: bsw.power.qlf"])

    def test_values_match_the_hand_over(self):
        """Numeric values of the enumerations, health values and the read signatures of BswServiceRte.h."""
        header = BSW_SERVICE_HEADER.read_text(encoding="utf-8")

        def enum(name):
            body = header.split(f"enum class {name} : uint8_t")[1].split("};")[0]
            return {n: int(v) for n, v in re.findall(r"^\s*(\w+)\s*= (\d+)U,", body, re.M)}

        self.assertEqual(enum("State"), rtegen.BSW_POWER_STATES)
        self.assertEqual(enum("Reason"), rtegen.BSW_RESET_REASONS)
        qualifier = {"PENDING": "QLF_DEBOUNCE", "OK": "QLF_OK", "FAILED": "QLF_ERROR"}
        self.assertEqual({v: qualifier[n] for n, v in enum("Health").items()}, rtegen.BSW_SERVICE_HEALTH)
        for spec in rtegen.BSW_SERVICES.values():
            params = ", ".join(f"{ctype}& \\w+" for ctype, _ in spec["locals"])
            self.assertRegex(header, rf"void {spec['read']}\({params}\);")
        # the units in the generator name the literals of the header
        for name, value in rtegen.BSW_POWER_STATES.items():
            self.assertIn(f"{value} {name}", rtegen.BSW_SERVICES["power"]["values"]["state"]["unit"])
        for name, value in rtegen.BSW_RESET_REASONS.items():
            self.assertIn(f"{value} {name}", rtegen.BSW_SERVICES["reset"]["values"]["reason"]["unit"])
        # the qualifier names exist (Rte_Qlf.h, COM-03)
        self.assertTrue(set(rtegen.BSW_SERVICE_HEALTH.values()) <= {f"QLF_{n}" for n in rtegen.QLF})

    def test_hand_over_is_part_of_the_rte_build(self):
        cmake = (REPO / "ecu" / "rte" / "CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn("add_library(bswServices services/src/bswService/BswServiceRte.cpp)", cmake)
        self.assertIn("target_link_libraries(rte PRIVATE bswServices)", cmake)


class RteBswServiceHostTest(unittest.TestCase):
    """RTE-05: compiles the generated RTE of test/bsw_services with the real hand-overs (BswServiceRte.cpp,
    EcuStateRte.cpp) and a test system timer, runs test/bsw_services/rte_bsw_services_host_test.cpp."""

    def test_bsw_service_runtime(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            out = tmp / "gen"
            rtegen.generate(BSW_SERVICES_DIR / "rte.yaml", PROJECT_TWO_BUSES, out, REPO, MULTI_TASKS)
            zig = [sys.executable, "-m", "ziglang"]
            swc = BSW_SERVICES_DIR / "ServiceSwc/generated/src"
            includes = [f"-I{out}", f"-I{out / 'tasks'}", f"-I{REPO / 'libs/bsw/autosarBase/include'}",
                        f"-I{HERE / 'test' / 'can_host' / 'stubs'}", f"-I{BSW_SERVICES_DIR / 'stubs'}", f"-I{swc}",
                        f"-I{REPO / 'libs/bsw/ecuState/include'}", f"-I{BSW_SERVICE_HEADER.parents[1]}"]
            objects = []
            for src in (out / "Rte.c", swc / "ServiceSwc.c"):
                obj = tmp / (src.stem + ".o")
                r = subprocess.run(zig + ["cc", "-std=c99", "-O1", "-Wall", "-Wextra", "-Werror", *includes, "-c",
                                          str(src), "-o", str(obj)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr[-3000:])
                objects.append(str(obj))
            exe = tmp / "rte_bsw_services_host_test.exe"
            r = subprocess.run(zig + ["c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion", "-Werror",
                                      *includes, str(out / "RteConfig.cpp"),
                                      str(REPO / "ecu/rte/services/src/bswService/BswServiceRte.cpp"),
                                      str(REPO / "libs/bsw/ecuState/src/ecuState/EcuStateRte.cpp"),
                                      str(BSW_SERVICES_DIR / "rte_bsw_services_host_test.cpp"), *objects,
                                      "-o", str(exe)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            r = subprocess.run([str(exe)], capture_output=True, text=True)
            print("\n" + r.stdout.strip(), file=sys.stderr)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:])
            self.assertIn(", 0 failures", r.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


MODES = HERE / "test" / "modes"
MODES_ECU = rtegen.with_resolved_paths(
    yaml.safe_load((MODES / "rte.yaml").read_text(encoding="utf-8")), MODES / "rte.yaml")


class RteModeTest(unittest.TestCase):
    """RTE-08: mode ports on the ECU mode manager ecu.mode and mode switch runnables, test profile test/modes (two
    components, two tasks, project buildtest_s32k148_min without SBC, BSW task profile test/multitask/tasks.yaml)."""

    setUp, tearDown = RteGenTest.setUp, RteGenTest.tearDown

    def run_with(self, ecu, out="out"):
        path = self.tmp / "rte.yaml"
        path.write_text(yaml.safe_dump(ecu, sort_keys=False), encoding="utf-8")
        return rtegen.generate(path, PROJECT_TWO_BUSES, self.tmp / out, REPO, MULTI_TASKS)

    def expect_error(self, ecu, fragment):
        with self.assertRaises(rtegen.RteError) as ctx:
            self.run_with(ecu)
        self.assertIn(fragment, str(ctx.exception))
        return str(ctx.exception)

    def generated(self, name, out="out"):
        return (self.tmp / out / name).read_text(encoding="utf-8")

    def mutated(self, component: str, old: str, new: str) -> dict:
        """ECU description of the test profile with a changed copy of a component's ARXML."""
        target = self.tmp / component
        if not target.exists():
            shutil.copytree(MODES / component, target)
        arxml = target / "generated" / "arxml" / f"{component}.arxml"
        text = arxml.read_text(encoding="utf-8")
        self.assertIn(old, text)
        arxml.write_text(text.replace(old, new, 1), encoding="utf-8")
        ecu = copy.deepcopy(MODES_ECU)
        for entry in ecu["components"]:
            if entry["name"] == component:
                entry["path"] = target.as_posix()
        return ecu

    def test_ports_groups_and_events_are_parsed(self):
        comp = rtegen.parse_component("ModeSwc", MODES / "ModeSwc" / "generated" / "arxml")
        self.assertEqual(list(comp.mode_ports), ["EcuMode.EcuMode", "SlowMode.EcuMode", "AnyMode.EcuMode"])
        self.assertNotIn("EcuMode.EcuMode", comp.ports)          # no sender-receiver buffer
        group = comp.mode_ports["EcuMode.EcuMode"].decl
        self.assertEqual((group.name, group.transition, group.initial, group.ctype), ("EcuModes", 255, "STARTUP",
                                                                                       "uint8"))
        self.assertEqual(group.modes, {"RUN": 10, "STARTUP": 5, "STANDBY": 20, "RUNDOWN": 30, "SHUTDOWN": 40,
                                       "SLEEP": 50})
        runnables = {r.name: r for r in comp.runnables}
        self.assertEqual([(e.activation, e.port, e.modes) for e in runnables["OnStandbyToRun"].mode_events],
                         [("ON-TRANSITION", "EcuMode.EcuMode", ("STANDBY", "RUN"))])
        self.assertEqual([(e.activation, e.modes) for e in runnables["OnRunExit"].mode_events],
                         [("ON-EXIT", ("RUN",))])
        self.assertIsNone(runnables["OnRunEntry"].period_ms)
        self.assertFalse(runnables["OnRunEntry"].init)
        self.assertEqual(runnables["OnRunEntry"].accesses, {"EntryCount.EntryCount"})
        self.assertEqual(runnables["Run1ms"].mode_accesses, {"EcuMode.EcuMode", "AnyMode.EcuMode"})
        # ALPHABETIC_ORDER (no category): values in alphabetical order, transition = number of modes; no access
        # points: every runnable reads every mode port
        alpha = rtegen.parse_component("AlphaSwc", MODES / "AlphaSwc" / "generated" / "arxml")
        decl = alpha.mode_ports["Mode.Mode"].decl
        self.assertEqual(decl.modes, {"Startup": 5, "Run": 0, "Standby": 4, "Rundown": 1, "Shutdown": 2, "Sleep": 3})
        self.assertEqual(decl.transition, 6)
        self.assertFalse(alpha.mode_access_points)
        self.assertEqual({r.name: r.mode_accesses for r in alpha.runnables},
                         {"Run10ms": {"Mode.Mode"}, "OnStandbyExit": {"Mode.Mode"}})

    def test_ecu_modes_match_the_module_header(self):
        header = (REPO / "libs/bsw/ecuState/include/ecuState/EcuState.h").read_text(encoding="utf-8")
        states = {name: int(value) for name, value in re.findall(r"^\s*(\w+)\s*= (\d)U,", header, re.M)}
        self.assertEqual(states, rtegen.ECU_MODES)
        rte = (REPO / "libs/bsw/ecuState/src/ecuState/EcuStateRte.cpp").read_text(encoding="utf-8")
        self.assertIn(f"state{{static_cast<uint8_t>(::ecustate::State::{rtegen.ECU_MODE_INITIAL})}}", rte)

    def test_generated_code(self):
        components, buffers, tasks = self.run_with(MODES_ECU)
        fast, slow = tasks
        self.assertEqual([r.name for _, r in fast.mode_runnables], ["OnStandbyToRun"])
        self.assertEqual([r.name for _, r in slow.mode_runnables],
                         ["OnStartupEntry", "OnRunEntry", "OnRunExit", "OnStandbyExit"])
        self.assertEqual([r.name for _, r in slow.runnables], ["Run10ms", "Run10ms"])   # periodic only
        # a value written by a mode switch runnable in slow and read in fast gets task copies (RTE-02 plan)
        self.assertEqual(buffers["ModeSwc.EntryCount.EntryCount"].copies, {"fast": "r", "slow": "w"})
        types = self.generated("Rte_Type.h")
        self.assertIn("typedef uint8 Rte_ModeType_EcuModes;", types)
        self.assertIn("#define RTE_MODE_EcuModes_RUN ((Rte_ModeType_EcuModes)10U)", types)
        self.assertIn("#define RTE_TRANSITION_AlphaModes ((Rte_ModeType_AlphaModes)6U)", types)
        header = self.generated("Rte_ModeSwc.h")
        self.assertIn("#define Rte_Mode_SlowMode_EcuMode Rte_Mode_ModeSwc_SlowMode_EcuMode", header)
        self.assertIn("extern Rte_ModeType_EcuModes Rte_Mode_ModeSwc_AnyMode_EcuMode(void);", header)
        rte_c = self.generated("Rte.c")
        self.assertIn("uint8 Rte_EcuMode_fast = 0u;", rte_c)
        self.assertIn("uint8 const mode = Rte_EcuMode_fast;", rte_c.split("Rte_Mode_ModeSwc_EcuMode_EcuMode(void)")[1])
        self.assertIn("uint8 const mode = Rte_EcuMode_slow;", rte_c.split("Rte_Mode_AlphaSwc_Mode_Mode(void)")[1])
        self.assertNotIn("Rte_ModeType_EcuModes Rte_Mode_ModeSwc_AnyMode_EcuMode(void)", rte_c)
        config = self.generated("RteConfig.cpp")
        self.assertIn("void readState(uint8_t& state);", config)
        self.assertIn("Rte_ModeType_EcuModes Rte_Mode_ModeSwc_AnyMode_EcuMode(void)", config)
        # mode switch runnables are no rows of rte::RUNNABLES (RteSystem would call them as init runnables)
        table = config.split("Runnable const RUNNABLES[] = {")[1].split("};")[0]
        self.assertNotIn("OnRunEntry", table)
        slow_code = config.split("void readInputs_slow()")[1].split("void writeOutputs_slow()")[0]
        exit_at, entry_at = slow_code.index("switch (previous) // on exit"), slow_code.index("switch (mode) // on entry")
        self.assertLess(exit_at, entry_at)
        self.assertLess(slow_code.index("runModeRunnable(3U); // ModeSwc.OnRunExit"),
                        slow_code.index("runModeRunnable(4U); // AlphaSwc.OnStandbyExit"))
        fast_code = config.split("void readInputs_fast()")[1].split("void writeOutputs_fast()")[0]
        self.assertIn("if ((previous == 2U) && (mode == 1U)) // STANDBY -> RUN", fast_code)
        self.assertNotIn("modeStarted", fast_code)      # no entry runnable of the initial mode in this task
        self.assertIn("static bool modeStarted = false;", slow_code)

    def test_without_mode_ports_nothing_changes(self):
        self.run_with(MULTI_ECU)
        for name in ("Rte.c", "Rte_Type.h", "Rte_Internal.h", "RteConfig.cpp", "Rte_AppSwc.h"):
            text = self.generated(name)
            self.assertNotIn("Rte_Mode", text)
            self.assertNotIn("Rte_EcuMode", text)
            self.assertNotIn("RTE-08", text)
        self.assertNotIn("ecustate", self.generated("RteConfig.cpp"))
        for name in ("RteConfig.h", "RteConfig.cpp"):   # RTE-17: no table without mode switch runnables
            self.assertNotIn("MODE_RUNNABLE", self.generated(name))
            self.assertNotIn("ModeRunnable", self.generated(name))

    def test_mode_runnable_table(self):
        """RTE-17: rte::MODE_RUNNABLES next to rte::RUNNABLES - one row per mode switch event, grouped by task, with
        activation and ECU mode; the mode switch calls each row through runModeRunnable(row)."""
        _, _, tasks = self.run_with(MODES_ECU)
        rows = rtegen.mode_runnable_table(tasks)
        self.assertEqual([(r.index, r.task.name, r.name, r.activation, r.mode, r.from_mode) for r in rows], [
            (0, "fast", "ModeSwc.OnStandbyToRun", "ON_TRANSITION", "RUN", "STANDBY"),
            (1, "slow", "ModeSwc.OnStartupEntry", "ON_ENTRY", "STARTUP", None),
            (2, "slow", "ModeSwc.OnRunEntry", "ON_ENTRY", "RUN", None),
            (3, "slow", "ModeSwc.OnRunExit", "ON_EXIT", "RUN", None),
            (4, "slow", "AlphaSwc.OnStandbyExit", "ON_EXIT", "STANDBY", None),   # "Standby" in the component
        ])
        header = self.generated("RteConfig.h")
        self.assertIn("#define NX_RTE_HAS_MODE_RUNNABLES 1", header)
        self.assertIn("constexpr size_t MODE_RUNNABLE_COUNT = 5U;", header)
        self.assertIn("extern ModeRunnable const MODE_RUNNABLES[];", header)
        self.assertIn("void setModeRunnableCall(ModeRunnableCall call);", header)
        config = self.generated("RteConfig.cpp")
        table = config.split("ModeRunnable const MODE_RUNNABLES[] = {")[1].split("};")[0]
        self.assertIn('{"AlphaSwc.OnStandbyExit", &AlphaSwc_OnStandbyExit, 1U, ModeActivation::ON_EXIT, "STANDBY", '
                      'nullptr},', table)
        self.assertEqual(table.count("ModeActivation::"), 5)
        # the periodic table is unchanged: no mode switch runnable, PERIODIC_RUNNABLE_COUNT without them
        self.assertNotIn("OnRunEntry", config.split("Runnable const RUNNABLES[] = {")[1].split("};")[0])
        self.assertIn("constexpr size_t PERIODIC_RUNNABLE_COUNT = 3U;", header)
        # every call of a mode switch runnable goes through its row; no direct call is left
        for row in rows:
            self.assertNotIn(f"{row.runnable.symbol}();", config)
        slow_code = config.split("void readInputs_slow()")[1].split("void writeOutputs_slow()")[0]
        self.assertEqual(slow_code.count("runModeRunnable(1U); // ModeSwc.OnStartupEntry"), 2)  # initial + switch
        self.assertIn("runModeRunnable(2U); // ModeSwc.OnRunEntry", slow_code)
        fast_code = config.split("void readInputs_fast()")[1].split("void writeOutputs_fast()")[0]
        self.assertIn("runModeRunnable(0U); // ModeSwc.OnStandbyToRun", fast_code)

    def test_a_runnable_with_two_events_has_two_rows(self):
        """RTE-17: a runnable started by two events (exit RUN and entry STANDBY) runs once per event, so it has one
        row per event - each with its own statistics in the console."""
        second = ("<SWC-MODE-SWITCH-EVENT><SHORT-NAME>Event_OnRunExit2</SHORT-NAME>"
                  "<START-ON-EVENT-REF DEST=\"RUNNABLE-ENTITY\">/Components/ModeSwc/ModeSwc_IB/OnRunExit"
                  "</START-ON-EVENT-REF><ACTIVATION>ON-ENTRY</ACTIVATION><MODE-IREFS><MODE-IREF>"
                  "<CONTEXT-PORT-REF DEST=\"R-PORT-PROTOTYPE\">/Components/ModeSwc/SlowMode</CONTEXT-PORT-REF>"
                  "<CONTEXT-MODE-DECLARATION-GROUP-PROTOTYPE-REF DEST=\"MODE-DECLARATION-GROUP-PROTOTYPE\">"
                  "/Interfaces/EcuModeIf/EcuMode</CONTEXT-MODE-DECLARATION-GROUP-PROTOTYPE-REF>"
                  "<TARGET-MODE-DECLARATION-REF DEST=\"MODE-DECLARATION\">/ModeDeclarationGroups/EcuModes/STANDBY"
                  "</TARGET-MODE-DECLARATION-REF></MODE-IREF></MODE-IREFS></SWC-MODE-SWITCH-EVENT></EVENTS>")
        _, _, tasks = self.run_with(self.mutated("ModeSwc", "</EVENTS>", second))
        rows = [(r.index, r.name, r.activation, r.mode) for r in rtegen.mode_runnable_table(tasks)
                if r.name == "ModeSwc.OnRunExit"]
        self.assertEqual(rows, [(3, "ModeSwc.OnRunExit", "ON_EXIT", "RUN"), (4, "ModeSwc.OnRunExit", "ON_ENTRY",
                                                                              "STANDBY")])
        config = self.generated("RteConfig.cpp")
        self.assertIn("constexpr size_t MODE_RUNNABLE_COUNT = 6U;", self.generated("RteConfig.h"))
        slow_code = config.split("void readInputs_slow()")[1].split("void writeOutputs_slow()")[0]
        exit_at = slow_code.index("runModeRunnable(3U); // ModeSwc.OnRunExit")
        entry_at = slow_code.index("runModeRunnable(4U); // ModeSwc.OnRunExit")
        self.assertLess(exit_at, entry_at)

    def test_mode_ports_without_mode_switch_runnables_have_no_table(self):
        ecu = copy.deepcopy(MODES_ECU)
        ecu["tasks"][0]["runnables"] = ["ModeSwc.Run1ms"]
        ecu["tasks"][1]["runnables"] = ["ModeSwc.Run10ms", "AlphaSwc.Run10ms"]
        stripped = ecu
        for component in ("ModeSwc", "AlphaSwc"):
            target = self.tmp / component
            shutil.copytree(MODES / component, target)
            arxml = target / "generated" / "arxml" / f"{component}.arxml"
            text = arxml.read_text(encoding="utf-8")
            # the former mode switch runnables become init runnables (a runnable needs an event)
            text = re.sub(r"<SWC-MODE-SWITCH-EVENT>\s*(<SHORT-NAME>.*?</SHORT-NAME>)\s*"
                          r"(<START-ON-EVENT-REF.*?</START-ON-EVENT-REF>).*?</SWC-MODE-SWITCH-EVENT>",
                          r"<INIT-EVENT>\1\2</INIT-EVENT>", text, flags=re.S)
            self.assertNotIn("SWC-MODE-SWITCH-EVENT", text)
            arxml.write_text(text, encoding="utf-8")
            for entry in stripped["components"]:
                if entry["name"] == component:
                    entry["path"] = target.as_posix()
        self.run_with(stripped)
        self.assertIn("Rte_EcuMode_fast", self.generated("RteConfig.cpp"))     # the mode ports are still there
        self.assertNotIn("MODE_RUNNABLE", self.generated("RteConfig.h"))
        self.assertNotIn("runModeRunnable", self.generated("RteConfig.cpp"))

    def test_one_task_takes_the_mode_switch_runnables_without_list(self):
        ecu = copy.deepcopy(MODES_ECU)
        ecu["tasks"] = [{"name": "app", "period_ms": 1, "priority": 25, "stack_bytes": 2048}]
        _, _, tasks = self.run_with(ecu)
        self.assertEqual(len(tasks[0].mode_runnables), 5)
        config = self.generated("RteConfig.cpp")
        self.assertIn('{"ModeSwc.OnStandbyToRun", &ModeSwc_OnStandbyToRun, 0U, ModeActivation::ON_TRANSITION, "RUN", '
                      '"STANDBY"},', config)
        self.assertIn("// ModeSwc.OnStandbyToRun", config.split("void readInputs_app()")[1])
        # every mode port is read in the one task: all return its view, none reads the mode manager directly
        self.assertNotIn("Rte_ModeType_EcuModes Rte_Mode_", self.generated("RteConfig.cpp"))

    def test_connection_errors(self):
        ecu = copy.deepcopy(MODES_ECU)
        ecu["connections"]["ModeSwc.EcuMode.EcuMode"] = "ecu.state"
        self.expect_error(ecu, "connection ModeSwc.EcuMode.EcuMode: a mode port is connected to a mode manager, "
                               "the ECU has one: ecu.mode (got ecu.state)")
        ecu["connections"]["ModeSwc.EcuMode.EcuMode"] = ["ecu.mode"]
        self.expect_error(ecu, "the ECU has one: ecu.mode")
        ecu = copy.deepcopy(MODES_ECU)
        del ecu["connections"]["AlphaSwc.Mode.Mode"]
        self.expect_error(ecu, "required port AlphaSwc.Mode.Mode is not connected (a mode port: connect it to "
                               "ecu.mode)")
        ecu = copy.deepcopy(MODES_ECU)
        ecu["connections"]["ModeSwc.EntryCountIn.EntryCountIn"] = "ecu.mode"
        self.expect_error(ecu, "ecu.mode connects a mode port (MODE-SWITCH-INTERFACE, Rte_Mode); a sender-receiver "
                               "port reads the state number from ecu.state")

    def test_task_errors(self):
        ecu = copy.deepcopy(MODES_ECU)
        ecu["tasks"][1]["runnables"].remove("ModeSwc.OnRunExit")
        self.expect_error(ecu, "tasks: runnable ModeSwc.OnRunExit is not assigned to a task")
        ecu = copy.deepcopy(MODES_ECU)
        ecu["tasks"][1]["runnables"][3] = {"runnable": "ModeSwc.OnRunEntry", "offset_ms": 10}
        self.expect_error(ecu, "tasks[1].runnables[3].offset_ms: ModeSwc.OnRunEntry is started by a mode switch, "
                               "not by a period; it has no offset")
        ecu = copy.deepcopy(MODES_ECU)
        ecu["tasks"][0]["runnables"].append("ModeSwc.OnRunExit")
        self.expect_error(ecu, "ModeSwc.OnRunExit is already assigned to task fast")

    def test_mode_group_errors(self):
        missing = self.mutated("AlphaSwc", "<MODE-DECLARATION>\n                            <SHORT-NAME>Rundown"
                                           "</SHORT-NAME>\n                        </MODE-DECLARATION>", "")
        self.expect_error(missing, "connection AlphaSwc.Mode.Mode -> ecu.mode: the mode declaration group "
                                   "AlphaModes must have exactly the modes of ecu.mode (STARTUP, RUN, STANDBY, "
                                   "SHUTDOWN, SLEEP, RUNDOWN, case does not matter): missing RUNDOWN")
        shutil.rmtree(self.tmp / "AlphaSwc")
        extra = self.mutated("AlphaSwc", "<SHORT-NAME>Rundown</SHORT-NAME>", "<SHORT-NAME>Limp</SHORT-NAME>")
        self.expect_error(extra, "missing RUNDOWN; unknown Limp")
        shutil.rmtree(self.tmp / "AlphaSwc")
        case = self.mutated("AlphaSwc", "<SHORT-NAME>Rundown</SHORT-NAME>", "<SHORT-NAME>RUN</SHORT-NAME>")
        self.expect_error(case, "modes Run and RUN of the mode declaration group AlphaModes differ only in case")
        shutil.rmtree(self.tmp / "AlphaSwc")
        no_transition = self.mutated("ModeSwc", "<ON-TRANSITION-VALUE>255</ON-TRANSITION-VALUE>", "")
        self.expect_error(no_transition, "ModeSwc.EcuMode: mode declaration group EcuModes (EXPLICIT_ORDER) needs "
                                         "an ON-TRANSITION-VALUE")
        shutil.rmtree(self.tmp / "ModeSwc")
        twice = self.mutated("ModeSwc", "<VALUE>20</VALUE>", "<VALUE>10</VALUE>")
        self.expect_error(twice, "mode values and ON-TRANSITION-VALUE must all differ")
        shutil.rmtree(self.tmp / "ModeSwc")
        no_value = self.mutated("ModeSwc", "<VALUE>20</VALUE>", "")
        self.expect_error(no_value, "(EXPLICIT_ORDER): mode STANDBY has no VALUE")
        shutil.rmtree(self.tmp / "ModeSwc")
        # same group name, other content in two components: one set of RTE_MODE_ macros is not possible
        clash = self.mutated("AlphaSwc", "<SHORT-NAME>AlphaModes</SHORT-NAME>", "<SHORT-NAME>EcuModes</SHORT-NAME>")
        for old in ("/AlphaModes</TYPE-TREF>", "/AlphaModes/Standby<", "/AlphaModes/Startup<"):
            arxml = self.tmp / "AlphaSwc" / "generated" / "arxml" / "AlphaSwc.arxml"
            arxml.write_text(arxml.read_text(encoding="utf-8").replace(old, old.replace("Alpha", "Ecu")),
                             encoding="utf-8")
        self.expect_error(clash, "the mode declaration group EcuModes differs from the one of")

    def test_component_errors(self):
        cases = [
            ("<ACTIVATION>ON-EXIT</ACTIVATION>", "<ACTIVATION>ON-EXIT</ACTIVATION><DISABLED-MODE-IREFS/>",
             "ModeSwc.OnRunExit: SWC-MODE-SWITCH-EVENT Event_OnRunExit has DISABLED-MODE-IREFS; mode disabling is "
             "not supported"),
            ("<ACTIVATION>ON-EXIT</ACTIVATION>", "<ACTIVATION>ON-DELAYED</ACTIVATION>",
             "activation ON-DELAYED is not supported (ON-EXIT, ON-TRANSITION, ON-ENTRY)"),
            ("/ModeDeclarationGroups/EcuModes/STARTUP</TARGET", "/ModeDeclarationGroups/EcuModes/BOOT</TARGET",
             "ModeSwc.OnStartupEntry: mode BOOT is not in the mode declaration group EcuModes"),
            ("<R-PORT-PROTOTYPE>\n                            <SHORT-NAME>AnyMode</SHORT-NAME>\n"
             "                            <REQUIRED-INTERFACE-TREF DEST=\"MODE-SWITCH-INTERFACE\">/Interfaces/EcuModeIf"
             "</REQUIRED-INTERFACE-TREF>\n                        </R-PORT-PROTOTYPE>",
             "<P-PORT-PROTOTYPE><SHORT-NAME>AnyMode</SHORT-NAME><PROVIDED-INTERFACE-TREF DEST=\"MODE-SWITCH-INTERFACE\">"
             "/Interfaces/EcuModeIf</PROVIDED-INTERFACE-TREF></P-PORT-PROTOTYPE>",
             "ModeSwc.AnyMode: a provided mode port (mode manager in a component, Rte_Switch) is not supported"),
            ("</EVENTS>",
             "<TIMING-EVENT><SHORT-NAME>Event_Exit10ms</SHORT-NAME><START-ON-EVENT-REF DEST=\"RUNNABLE-ENTITY\">"
             "/Components/ModeSwc/ModeSwc_IB/OnRunExit</START-ON-EVENT-REF><PERIOD>0.01</PERIOD></TIMING-EVENT>"
             "</EVENTS>",
             "ModeSwc.OnRunExit: a runnable started by a mode switch event and by a timing event is not supported"),
            ("<CONTEXT-R-PORT-REF DEST=\"R-PORT-PROTOTYPE\">/Components/ModeSwc/SlowMode</CONTEXT-R-PORT-REF>",
             "<CONTEXT-R-PORT-REF DEST=\"R-PORT-PROTOTYPE\">/Components/ModeSwc/LostMode</CONTEXT-R-PORT-REF>",
             "ModeSwc.Run10ms: mode access point to unknown mode port LostMode.EcuMode"),
        ]
        for old, new, fragment in cases:
            with self.subTest(fragment=fragment):
                ecu = self.mutated("ModeSwc", old, new)
                self.expect_error(ecu, fragment)
                shutil.rmtree(self.tmp / "ModeSwc")
        ecu = self.mutated("ModeSwc", "<ACTIVATION>ON-TRANSITION</ACTIVATION>", "<ACTIVATION>ON-ENTRY</ACTIVATION>")
        self.expect_error(ecu, "(ON-ENTRY) needs 1 MODE-IREF, has 2")

    def test_warning_for_modes_the_tasks_do_not_see(self):
        comp = rtegen.parse_component("ModeSwc", MODES / "ModeSwc" / "generated" / "arxml")
        self.assertEqual(rtegen.mode_warnings({"ModeSwc": comp}), [])
        ecu = self.mutated("ModeSwc", "/ModeDeclarationGroups/EcuModes/STARTUP</TARGET",
                           "/ModeDeclarationGroups/EcuModes/SHUTDOWN</TARGET")
        comp = rtegen.parse_component("ModeSwc", self.tmp / "ModeSwc" / "generated" / "arxml")
        warnings = rtegen.mode_warnings({"ModeSwc": comp})
        self.assertEqual(len(warnings), 1)
        self.assertTrue(warnings[0].startswith("components: ModeSwc.OnStartupEntry runs ON-ENTRY SHUTDOWN, but the "
                                               "application tasks stop"), warnings[0])
        self.run_with(ecu)                                # a warning, not an error
        self.assertIn("case 3U: // SHUTDOWN", self.generated("RteConfig.cpp"))

    def test_messages_carry_the_yaml_path(self):
        ecu = copy.deepcopy(MODES_ECU)
        ecu["connections"]["ModeSwc.EcuMode.EcuMode"] = "ecu.state"
        text = self.expect_error(ecu, "a mode port")
        path = self.tmp / "rte.yaml"
        message = rtegen.rte_message(text, path, rtegen.nxconfig.config.read_rte(path)[2])
        self.assertEqual(message.yaml_path, "connections.ModeSwc.EcuMode.EcuMode")
        self.assertIsNotNone(message.line)


class RteModeHostTest(unittest.TestCase):
    """RTE-08: compiles the generated RTE of test/modes with the synthetic components and the real hand-over of the
    ECU state (EcuStateRte.cpp), runs test/modes/rte_modes_host_test.cpp."""

    def test_mode_runtime(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            out = tmp / "gen"
            rtegen.generate(MODES / "rte.yaml", PROJECT_TWO_BUSES, out, REPO, MULTI_TASKS)
            zig = [sys.executable, "-m", "ziglang"]
            includes = [f"-I{out}", f"-I{out / 'tasks'}", f"-I{REPO / 'libs/bsw/autosarBase/include'}",
                        f"-I{HERE / 'test' / 'can_host' / 'stubs'}", f"-I{REPO / 'libs/bsw/ecuState/include'}",
                        f"-I{MODES / 'ModeSwc/generated/src'}", f"-I{MODES / 'AlphaSwc/generated/src'}",
                        f"-I{REPO / 'ecu/application/src/systems'}"]  # RteConsole.h (RTE-17)
            objects = []
            for src in (out / "Rte.c", MODES / "ModeSwc/generated/src/ModeSwc.c",
                        MODES / "AlphaSwc/generated/src/AlphaSwc.c"):
                obj = tmp / (src.stem + ".o")
                r = subprocess.run(zig + ["cc", "-std=c99", "-O1", "-Wall", "-Wextra", "-Wconversion", "-Werror",
                                          *includes, "-c", str(src), "-o", str(obj)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr[-3000:])
                objects.append(str(obj))
            exe = tmp / "rte_modes_host_test.exe"
            r = subprocess.run(zig + ["c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion", "-Werror",
                                      *includes, str(out / "RteConfig.cpp"),
                                      str(REPO / "libs/bsw/ecuState/src/ecuState/EcuStateRte.cpp"),
                                      str(MODES / "rte_modes_host_test.cpp"), *objects, "-o", str(exe)],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            r = subprocess.run([str(exe)], capture_output=True, text=True)
            print("\n" + r.stdout.strip(), file=sys.stderr)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:])
            self.assertIn(", 0 failures", r.stdout)
            self.check_console_lines_with_schedana(r.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def check_console_lines_with_schedana(self, stdout: str):
        """RTE-17: the lines RteConsole.h printed for the mode switch runnables of the profile, read by the parser
        of tools/schedana - the contract between the firmware and the analysis, with the generated table."""
        if str(REPO / "tools") not in sys.path:
            sys.path.insert(0, str(REPO / "tools"))
        from schedana import measure
        text = stdout.split("--- rte runnables (modes) ---\n")[1].split("--- rte json (modes) ---\n")[0]
        json_text = stdout.split("--- rte json (modes) ---\n")[1].split("--- end ---")[0]
        for lines, source in ((text, "rte runnables"), (json_text, "rte json")):
            m = measure.parse(lines)
            self.assertEqual(m.lines_ignored, 0, lines)
            self.assertEqual(m.sources, [source])
            self.assertEqual(len(m.modes), 5, lines)
            transition = m.modes[("fast", "ModeSwc.OnStandbyToRun", "transition", "STANDBY", "RUN")]
            self.assertEqual((transition.calls, transition.max_us), (1, 6.0))
            entry = m.modes[("slow", "ModeSwc.OnRunEntry", "entry", None, "RUN")]
            self.assertEqual((entry.calls, entry.max_us), (3, 26.0))


SENSOR = HERE / "test" / "sensor_host"
SENSOR_ECU = rtegen.with_resolved_paths(
    yaml.safe_load((SENSOR / "rte.yaml").read_text(encoding="utf-8")), SENSOR / "rte.yaml")


class RteSensorTest(unittest.TestCase):
    """IO-18: adc.phys.<NAME> (sint32) and adc.qlf.<NAME> - signed physical value and quality of a board sensor."""

    setUp, tearDown = RteGenTest.setUp, RteGenTest.tearDown

    def run_with(self, ecu, project=None):
        path = self.tmp / "rte.yaml"
        path.write_text(yaml.safe_dump(ecu, sort_keys=False), encoding="utf-8")
        return rtegen.generate(path, project or PROJECT, self.tmp / "out", REPO)

    def expect_error(self, ecu, fragment):
        with self.assertRaises(rtegen.RteError) as ctx:
            self.run_with(ecu)
        self.assertIn(fragment, str(ctx.exception))

    def ecu(self, **connections):
        ecu = copy.deepcopy(SENSOR_ECU)
        ecu["connections"].update(connections)
        return ecu

    def generated(self):
        return (self.tmp / "out" / "RteConfig.cpp").read_text(encoding="utf-8")

    def test_targets_are_the_channels_with_a_plan(self):
        signals = rtegen.board_signals(PROJECT)
        # RTE-11: board names as board.yaml spells them (12VBAT_CURRENT, C identifier SIG_12VBAT_CURRENT)
        expected = {"SENSE_12VGATE", "12VBAT_CURRENT", "SENSE_TSVOLTAGE", "SENSE_TSCURRENT", "CURRENTSENSE_KL30B",
                    "PILOT_ANALOG", "PROXY_ANALOG", "ADC_CURRENT_U", "ADC_CURRENT_V", "ADC_CURRENT_W", "BEMF_U",
                    "BEMF_V", "BEMF_W"}
        self.assertEqual(signals["adc.phys"], expected)
        self.assertEqual(signals["adc.qlf"], expected)
        # not fitted on the test board (like the NTC pull-up of the eval board): raw pin only
        self.assertEqual(signals["adc.unfitted"], {"MOTOR_NTC"})
        self.assertIn("MOTOR_NTC", signals["adc.mv"])

    def test_one_conversion_per_channel(self):
        _, buffers, _ = self.run_with(SENSOR_ECU)
        b = buffers["SensorSwc.TsCurrent.TsCurrent"]
        self.assertEqual((b.bsw, b.bsw_signal, b.ctype), ("adc.phys", "SENSE_TSCURRENT", "sint32"))
        config = self.generated()
        self.assertIn("#include <bsp/adc/analogSensorConfiguration.h>", config)
        self.assertEqual(config.count("::bios::AnalogInput::Ai_SENSE_TSCURRENT"), 1)
        self.assertIn("sensor_SENSE_TSCURRENT_value = nxAdcPhys_SENSE_TSCURRENT(sensor_SENSE_TSCURRENT_raw, "
                      "&sensor_SENSE_TSCURRENT_quality);", config)
        self.assertIn("Rte_Buf_SensorSwc_TsCurrent_TsCurrent = static_cast<sint32>(sensor_SENSE_TSCURRENT_value);",
                      config)
        # only the quality of the switch is connected: no value variable (no unused variable on the target)
        self.assertIn("(void)nxAdcPhys_CURRENTSENSE_KL30B(", config)
        self.assertNotIn("sensor_CURRENTSENSE_KL30B_value", config)
        self.assertIn('"adc.phys.SENSE_TSCURRENT"', config)          # console "rte signals"
        self.assertEqual(rtegen.adc_warnings(buffers, rtegen.board_signals(PROJECT)), [])

    def test_without_a_sensor_connection_no_code(self):
        self.run_with(NO_CAN)
        for absent in ("analogSensorConfiguration", "nxAdcPhys_"):
            self.assertNotIn(absent, self.generated())

    def test_connection_errors(self):
        cases = [
            ({"SensorSwc.TsCurrent.TsCurrent": "adc.phys.MOTOR_NTC"},
             "the circuit of MOTOR_NTC is not fitted on the board (board.yaml adc fitted: false)"),
            ({"SensorSwc.TsCurrentQlf.TsCurrentQlf": "adc.qlf.MOTOR_NTC"}, "the circuit of MOTOR_NTC is not fitted"),
            ({"SensorSwc.TsCurrent.TsCurrent": "adc.phys.NOPE"}, "NOPE is not a adc.phys signal used by the project"),
            ({"SensorSwc.TsCurrentQlf.TsCurrentQlf": "adc.phys.SENSE_TSCURRENT"},
             "type uint8 does not fit adc.phys (sint32)"),
            ({"SensorSwc.TsCurrent.TsCurrent": "adc.qlf.SENSE_TSCURRENT"},
             "type sint32 does not fit adc.qlf (uint16, uint32, uint8)"),
            ({"SensorSwc.TsCurrent.TsCurrent": "adc.mv.SENSE_TSCURRENT"}, "type sint32 does not fit adc.mv (uint32)"),
        ]
        for connections, fragment in cases:
            with self.subTest(connections=connections):
                self.expect_error(self.ecu(**connections), fragment)

    def test_warnings(self):
        ecu = self.ecu()
        del ecu["connections"]["SensorSwc.ProxyQlf.ProxyQlf"]
        ecu["connections"]["SensorSwc.ProxyQlf.ProxyQlf"] = "rte.init"
        _, buffers, _ = self.run_with(ecu)
        signals = rtegen.board_signals(PROJECT)
        self.assertEqual(rtegen.adc_warnings(buffers, signals),
                         ["connections: adc.qlf.PROXY_ANALOG is not connected, so a component cannot tell a physical "
                          "value from one at the end of the converter or outside the sensor range: connect it to a "
                          "required port"])
        # the test profile reads the open MOTOR_NTC pin (pull-up not fitted)
        _, demo, _ = RteGenTest.run_with(self, NO_CAN)
        self.assertEqual(rtegen.adc_warnings(demo, signals),
                         ["connections: the circuit of ADC channel MOTOR_NTC is not fitted on the board (board.yaml adc "
                          "fitted: false), adc.mv/adc.raw.MOTOR_NTC read an open pin"])

    def test_quality_codes_are_the_signal_qualifier(self):
        """The generated conversion reports QLF_OK / QLF_ERROR_RANGE (doc/architecture/signal-qualifier.md)."""
        for qlf, quality in rtegen.ADC_SENSOR_QUALITY.items():
            self.assertEqual(getattr(rtegen.nxconfig.sensor, quality), rtegen.QLF[qlf], f"{quality} != QLF_{qlf}")

    def test_host_test_values_match_the_reference(self):
        """The numbers rte_sensor_host_test.cpp expects are those of the Python reference (tools/nxconfig/sensor.py)."""
        plans, ev = PROJECT.adc_sensors, rtegen.nxconfig.sensor.evaluate
        ok, rng = rtegen.QLF["OK"], rtegen.QLF["ERROR_RANGE"]
        # PLAT-62: the test profile testproject_full (testboard_full), not the eval board
        cases = {"SENSE_TSCURRENT": {2046: (-37, ok), 2048: (12, ok), 8: (-49805, rng), 4094: (49976, ok)},
                 "PROXY_ANALOG": {3000: (1660, ok), 2999: (1658, ok), 4095: (6030100, rng), 0: (0, rng)},
                 "CURRENTSENSE_KL30B": {1000: (2442, ok), 1: (2, ok), 4095: (10000, rng), 0: (0, rng)}}
        for name, raws in cases.items():
            for raw, expected in raws.items():
                self.assertEqual(ev(plans[name], raw), expected, (name, raw))


class RteSensorHostTest(unittest.TestCase):
    """IO-18: compiles the generated RTE of test/sensor_host with the board header bswgen generates for the test
    profile (PLAT-62) and runs test/sensor_host/rte_sensor_host_test.cpp."""

    def test_sensor_runtime(self):
        sys.path.insert(0, str(REPO / "tools" / "bswgen"))
        import bswgen  # noqa: E402

        tmp = Path(tempfile.mkdtemp())
        try:
            out, board = tmp / "gen", tmp / "board"
            rtegen.generate(SENSOR / "rte.yaml", PROJECT, out, REPO)
            bswgen.generate(PROJECT_FILE, board)
            zig = [sys.executable, "-m", "ziglang"]
            includes = [f"-I{out}", f"-I{out / 'tasks'}", f"-I{REPO / 'libs/bsw/autosarBase/include'}",
                        f"-I{HERE / 'test' / 'can_host' / 'stubs'}", f"-I{SENSOR / 'SensorSwc/generated/src'}",
                        f"-I{board}"]
            objects = []
            for src in (out / "Rte.c", SENSOR / "SensorSwc/generated/src/SensorSwc.c"):
                obj = tmp / (src.stem + ".o")
                r = subprocess.run(zig + ["cc", "-std=c99", "-O1", "-Wall", "-Wextra", "-Werror", *includes, "-c",
                                          str(src), "-o", str(obj)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr[-3000:])
                objects.append(str(obj))

            def build_and_run(config_cpp: Path, name: str):
                exe = tmp / f"{name}.exe"
                r = subprocess.run(zig + ["c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror", *includes,
                                          str(config_cpp), str(SENSOR / "rte_sensor_host_test.cpp"),
                                          *objects, "-o", str(exe)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr[-3000:])
                return subprocess.run([str(exe)], capture_output=True, text=True)

            r = build_and_run(out / "RteConfig.cpp", "rte_sensor_host_test")
            print("\n" + r.stdout.strip(), file=sys.stderr)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:])
            self.assertIn(", 0 failures", r.stdout)

            # built-in faults: the test must notice each of them
            config = (out / "RteConfig.cpp").read_text(encoding="utf-8")
            mutants = {
                "failed conversion reported as OK": ("uint8_t sensor_SENSE_TSCURRENT_quality = QLF_ERROR;",
                                                     "uint8_t sensor_SENSE_TSCURRENT_quality = QLF_OK;"),
                "value overwritten after a failed conversion": (
                    "        if (sensor_PROXY_ANALOG_valid)\n        {\n            Rte_Buf_SensorSwc_Proxy_Proxy",
                    "        if (true)\n        {\n            Rte_Buf_SensorSwc_Proxy_Proxy"),
                "wrong channel converted": ("Ai_PROXY_ANALOG, sensor_PROXY_ANALOG_raw",
                                            "Ai_CURRENTSENSE_KL30B, sensor_PROXY_ANALOG_raw"),
            }
            for index, (what, (old, new)) in enumerate(mutants.items()):
                with self.subTest(mutant=what):
                    self.assertEqual(config.count(old), 1, what)
                    mutant = out / f"RteConfig_mutant{index}.cpp"
                    mutant.write_text(config.replace(old, new), encoding="utf-8")
                    r = build_and_run(mutant, f"mutant{index}")
                    self.assertNotEqual(r.returncode, 0, f"mutant not detected: {what}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


PARAM = HERE / "test" / "param"
PARAM_ECU = rtegen.with_resolved_paths(
    yaml.safe_load((PARAM / "rte.yaml").read_text(encoding="utf-8")), PARAM / "rte.yaml")
# Old MBDT project data file of the user; the test runs only if NX_MBDT_DATA points to it.
MBDT_DATA = Path(os.environ.get("NX_MBDT_DATA", "mbdt_data_not_set.m"))


class RteParamTest(unittest.TestCase):
    """RTE-03: calibration parameters (ParameterReceiver ports) with values from <Swc>_data.m, own test component
    CalSwc (test/param) in the ARXML shape of Embedded Coder R2024a."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        # private copy of the component: tests change its data file; repo root of the run is self.tmp
        shutil.copytree(PARAM / "CalSwc", self.tmp / "CalSwc")
        self.data = self.tmp / "CalSwc" / "CalSwc_data.m"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_param(self, ecu=None):
        ecu = copy.deepcopy(ecu or PARAM_ECU)
        ecu["components"][0]["path"] = "CalSwc"
        path = self.tmp / "rte.yaml"
        path.write_text(yaml.safe_dump(ecu, sort_keys=False), encoding="utf-8")
        return rtegen.generate(path, PROJECT, self.tmp / "out", self.tmp, REPO / rtegen.TASKS_MANIFEST)

    def expect(self, fragment, ecu=None):
        with self.assertRaises(rtegen.RteError) as ctx:
            self.run_param(ecu)
        self.assertIn(fragment, str(ctx.exception))
        return str(ctx.exception)

    def set_value(self, name, text):
        lines = self.data.read_text(encoding="utf-8").splitlines()
        lines = [f"{name} = {text};" if re.match(rf"^{name}\s*=", line) else line for line in lines]
        self.data.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def generated(self, name):
        return (self.tmp / "out" / name).read_text(encoding="utf-8")

    def test_parameters_are_parsed(self):
        comp = rtegen.parse_component("CalSwc", PARAM / "CalSwc" / "generated" / "arxml")
        p = comp.parameters
        self.assertEqual(list(p), ["CalPrm.Gain", "CalPrm.Table", "CalPrm.Factor", "CalPrm.Temp", "CalPrm.Alpha",
                                   "CalPrm.Enable"])
        self.assertNotIn("CalPrm.Gain", comp.ports)          # no sender-receiver port, no connection needed
        self.assertEqual((p["CalPrm.Gain"].type.native, p["CalPrm.Gain"].dim), ("uint16", 1))
        self.assertEqual((p["CalPrm.Table"].type.name, p["CalPrm.Table"].dim), ("uint16", 4))
        self.assertEqual((p["CalPrm.Factor"].type.name, p["CalPrm.Factor"].type.native), ("UFIX16_SP001", "uint16"))
        self.assertEqual((p["CalPrm.Factor"].numerator, p["CalPrm.Factor"].denominator), ((0, 1), 1000))
        # numerator [-40, 0.1] / 1 -> integer coefficients (-400 + 1 * raw) / 10
        self.assertEqual((p["CalPrm.Temp"].numerator, p["CalPrm.Temp"].denominator, p["CalPrm.Temp"].unit),
                         ((-400, 1), 10, "degC"))
        self.assertEqual(p["CalPrm.Factor"].unit, "")          # NoUnit
        self.assertEqual(p["CalPrm.Gain"].runnables, {"Run10ms"})
        self.assertEqual(p["CalPrm.Alpha"].runnables, set())    # no access point in the ARXML
        self.assertEqual(p["CalPrm.Gain"].access, "READ-WRITE")

    def test_values_scalar_vector_fixed_point(self):
        components, _, _ = self.run_param()
        p = components["CalSwc"].parameters
        self.assertEqual(p["CalPrm.Gain"].raw, [3])           # via cal.gain = hex2dec('3')
        self.assertEqual(p["CalPrm.Table"].raw, [10, 20, 30, 40])   # continuation line
        self.assertEqual((p["CalPrm.Factor"].phys, p["CalPrm.Factor"].raw), ([1.5], [1500]))
        self.assertEqual((p["CalPrm.Temp"].phys, p["CalPrm.Temp"].raw), ([25], [650]))
        self.assertEqual(p["CalPrm.Alpha"].raw, [0.125])
        self.assertEqual(p["CalPrm.Enable"].raw, [1])

    def test_generated_storage_and_access(self):
        self.run_param()
        rte_c = self.generated("Rte.c")
        # RAM, not const: a calibration tool may write it (TOOL-01)
        self.assertIn("uint16 Rte_Prm_CalSwc_CalPrm_Gain_data = 3U;", rte_c)
        self.assertIn("uint16 Rte_Prm_CalSwc_CalPrm_Table_data[4] = { 10U, 20U, 30U, 40U };", rte_c)
        self.assertIn("UFIX16_SP001 Rte_Prm_CalSwc_CalPrm_Factor_data = 1500U; /* CalSwc.CalPrm.Factor = 1.5 */", rte_c)
        self.assertIn("SFIX16_S01_BN40 Rte_Prm_CalSwc_CalPrm_Temp_data = 650; /* CalSwc.CalPrm.Temp = 25 degC */",
                      rte_c)
        self.assertIn("float32 Rte_Prm_CalSwc_CalPrm_Alpha_data = 0.125F;", rte_c)
        self.assertIn("boolean Rte_Prm_CalSwc_CalPrm_Enable_data = 1U;", rte_c)
        self.assertNotRegex(rte_c, r"(?m)^.*\bconst\b.*Rte_Prm_\w+_data")
        self.assertIn("uint16* Rte_Prm_CalSwc_CalPrm_Table(void)", rte_c)
        header = self.generated("Rte_CalSwc.h")
        self.assertIn("#define Rte_Prm_CalPrm_Gain Rte_Prm_CalSwc_CalPrm_Gain", header)
        self.assertIn("extern uint16* Rte_Prm_CalSwc_CalPrm_Table(void); /* 4 elements */", header)
        self.assertIn("extern UFIX16_SP001 Rte_Prm_CalSwc_CalPrm_Factor(void);", header)
        types = self.generated("Rte_Type.h")
        self.assertIn("typedef uint16 UFIX16_SP001;", types)
        self.assertIn("typedef sint16 SFIX16_S01_BN40;", types)
        self.assertIn("extern uint16 Rte_Prm_CalSwc_CalPrm_Table_data[4];", self.generated("Rte_Internal.h"))
        config = self.generated("RteConfig.cpp")
        self.assertIn('{"CalSwc.CalPrm.Temp", "degC", &Rte_Prm_CalSwc_CalPrm_Temp_data, SignalType::SINT16, 1U, '
                      '{ -400, 1 }, 10},', config)
        self.assertIn('{"CalSwc.CalPrm.Table", "", Rte_Prm_CalSwc_CalPrm_Table_data, SignalType::UINT16, 4U, '
                      '{ 0, 1 }, 1},', config)
        self.assertIn("constexpr size_t PARAMETER_COUNT = 6U;", self.generated("RteConfig.h"))

    def test_value_is_taken_from_the_data_file_not_the_arxml(self):
        # the ARXML init value of Gain is 99 (what the model saw at its last build); the data file wins
        self.set_value("Gain", "7")
        components, _, _ = self.run_param()
        self.assertEqual(components["CalSwc"].parameters["CalPrm.Gain"].raw, [7])

    def test_missing_value(self):
        self.set_value("Temp", "25")
        text = self.data.read_text(encoding="utf-8").replace("Temp = 25;", "% Temp removed")
        self.data.write_text(text, encoding="utf-8")
        message = self.expect("CalSwc.CalPrm.Temp: Temp is not set in")
        self.assertIn("CalSwc/CalSwc_data.m", message)

    def test_missing_data_file(self):
        self.data.unlink()
        self.expect("no data file CalSwc/CalSwc_data.m")

    def test_vector_length_must_match(self):
        self.set_value("Table", "[1 2 3]")
        self.expect("CalSwc.CalPrm.Table: CalSwc/CalSwc_data.m:8: Table has 3 value(s), the model expects 4 values")
        self.set_value("Gain", "[1 2]")
        self.set_value("Table", "[1 2 3 4]")
        self.expect("Gain has 2 value(s), the model expects a scalar")

    def test_integer_without_scaling_must_be_whole(self):
        self.set_value("Gain", "2.5")
        self.expect("2.5 is no whole number, but Gain is uint16 without scaling")

    def test_fixed_point_rounds_and_checks_the_range(self):
        self.set_value("Factor", "0.0016")                       # 1.6 steps -> 2
        components, _, _ = self.run_param()
        self.assertEqual(components["CalSwc"].parameters["CalPrm.Factor"].raw, [2])
        self.set_value("Factor", "65.536")                       # 65536 steps: one above uint16
        self.expect("65.536 does not fit UFIX16_SP001 (uint16 0 .. 65535) (stored value 65536)")
        self.set_value("Factor", "1")
        self.set_value("Temp", "-3300")                          # (-3300 + 40) * 10 = -32600 fits sint16
        components, _, _ = self.run_param()
        self.assertEqual(components["CalSwc"].parameters["CalPrm.Temp"].raw, [-32600])
        self.set_value("Temp", "-3400")
        self.expect("-3400 does not fit SFIX16_S01_BN40 (sint16")

    def test_integer_range(self):
        self.set_value("Gain", "-1")
        self.expect("-1 does not fit uint16 (uint16 0 .. 65535)")

    def test_unsupported_expression(self):
        self.set_value("Gain", "cal.gain * 2")
        self.expect("CalSwc/CalSwc_data.m:7: Gain = cal.gain * 2 is not a value this reader understands")

    def test_parameter_is_not_connected(self):
        ecu = copy.deepcopy(PARAM_ECU)
        ecu["connections"]["CalSwc.CalPrm.Gain"] = "rte"
        self.expect("CalPrm.Gain is a calibration parameter; it is not connected", ecu)

    def test_reference_to_an_unknown_name(self):
        self.set_value("Alpha", "x_undefined")
        comp = rtegen.parse_component("CalSwc", self.tmp / "CalSwc" / "generated" / "arxml")
        with self.assertRaises(rtegen.RteError) as ctx:
            rtegen.load_parameter_values(comp, self.tmp / "CalSwc", self.tmp)
        self.assertIn("Alpha = x_undefined is not a value this reader understands", str(ctx.exception))

    def test_component_without_parameters_needs_no_data_file(self):
        comp = rtegen.parse_component("MotorSwc", MULTI / "MotorSwc" / "generated" / "arxml")
        self.assertEqual(comp.parameters, {})
        rtegen.load_parameter_values(comp, self.tmp / "nowhere", self.tmp)   # no error

    def test_demo_component_reads_its_data_file(self):
        comp = rtegen.parse_component("DemoSwc", REPO / "swc" / "DemoSwc" / "generated" / "arxml")
        rtegen.load_parameter_values(comp, REPO / "swc" / "DemoSwc", REPO)
        alpha = comp.parameters["CalPrm.FilterAlpha"]
        self.assertEqual((alpha.type.native, alpha.dim, alpha.raw, alpha.runnables), ("float32", 1, [0.125],
                                                                                        {"Run100ms"}))
        with self.assertRaises(rtegen.RteError) as ctx:
            rtegen.load_parameter_values(comp, self.tmp / "nowhere", self.tmp)
        self.assertIn("1 calibration parameter(s) (FilterAlpha) but no data file", str(ctx.exception))

    def test_unused_parameter_without_type(self):
        arxml = self.tmp / "CalSwc" / "generated" / "arxml" / "CalSwc.arxml"
        text = arxml.read_text(encoding="utf-8")
        text = text.replace('<TYPE-TREF DEST="IMPLEMENTATION-DATA-TYPE">/DataTypes/boolean</TYPE-TREF>\n'
                            '                        </PARAMETER-DATA-PROTOTYPE>', '</PARAMETER-DATA-PROTOTYPE>')
        arxml.write_text(text, encoding="utf-8")
        with self.assertRaises(rtegen.RteError) as ctx:
            rtegen.parse_component("CalSwc", arxml.parent)
        self.assertIn("CalSwc.CalPrm.Enable: parameter without type", str(ctx.exception))

    def test_c_literals(self):
        self.assertEqual(rtegen.c_literal("float32", 1), "1.0F")
        self.assertEqual(rtegen.c_literal("float32", 1e-7), "1e-07F")
        self.assertEqual(rtegen.c_literal("float64", 0.5), "0.5")
        self.assertEqual(rtegen.c_literal("sint32", -(2**31)), "(-2147483647 - 1)")
        self.assertEqual(rtegen.c_literal("sint8", -5), "-5")
        self.assertEqual(rtegen.c_literal("uint32", 2**32 - 1), "4294967295U")


class MDataTest(unittest.TestCase):
    """RTE-03: reader of the MATLAB data files (tools/swc/mdata.py), without MATLAB."""

    def parse(self, text):
        return rtegen.mdata.parse(text, Path("x_data.m"))

    def test_forms(self):
        d = self.parse("a = 3;\nb = -1.5e-3; % comment 'x'\ng.c = [1, 2 3];\nh = hex2dec('6F1');\n"
                       "k = bin2dec('0110'); t = true; f = false;\nr = g.c;\nn = -a;\ne = [];\n"
                       "long = [1 ...\n  2];\n")
        self.assertEqual(d.get("a"), [3])
        self.assertEqual(d.get("b"), [-0.0015])
        self.assertEqual(d.get("g.c"), [1, 2, 3])
        self.assertEqual(d.get("h"), [0x6F1])
        self.assertEqual((d.get("k"), d.get("t"), d.get("f")), ([6], [1], [0]))
        self.assertEqual(d.get("r"), [1, 2, 3])
        self.assertEqual(d.get("n"), [-3])
        self.assertEqual(d.get("e"), [])
        self.assertEqual(d.get("long"), [1, 2])
        self.assertEqual(d.line_of("long"), 9)     # first line of the continued statement

    def test_percent_in_text_is_no_comment(self):
        d = self.parse("s = '50%'; v = 2;\n")
        self.assertIsNone(d.entries["s"].values)
        self.assertEqual(d.get("v"), [2])

    def test_unsupported_and_missing(self):
        # RTE-26: a matrix is a map (row by row); only a ragged one is not understood
        d = self.parse("clear;\nrun('Other.m');\nm = [1 2; 3];\nx = a + 1;\nmap = [1 2; 3 4];\n")
        self.assertEqual((d.get("map"), d.entries["map"].shape), ([1, 2, 3, 4], (2, 2)))
        with self.assertRaises(rtegen.mdata.MDataError) as ctx:
            d.get("m")
        self.assertIn("x_data.m:3: m = [1 2; 3] is not a value", str(ctx.exception))
        with self.assertRaises(rtegen.mdata.MDataError):
            d.get("x")
        with self.assertRaises(rtegen.mdata.MDataError) as ctx:
            d.get("y", "Swc.P.y")
        self.assertIn("Swc.P.y: y is not set in x_data.m", str(ctx.exception))

    def test_later_assignment_wins(self):
        d = self.parse("a = 1;\na = 2;\n")
        self.assertEqual(d.get("a"), [2])

    @unittest.skipUnless(MBDT_DATA.is_file(), "old MBDT project data: set NX_MBDT_DATA to run")
    def test_old_mbdt_data_file(self):
        d = rtegen.mdata.load(MBDT_DATA)
        self.assertEqual(d.get("SYSCONST.QLF_OK"), [1])
        self.assertEqual(d.get("dcm.uds_tester_address"), [0x6F1])
        self.assertEqual(d.get("hvsmeas.meascurrentts_safetyoffsetnegative"), [-0.4])
        self.assertEqual(len(d.get("hvscellmod.cell_ocv_table")), 27)
        self.assertEqual(d.get("sbc_param.reg_config_control"), [0b110])
        self.assertEqual([n for n, e in d.entries.items() if e.values is None], [])


class RteParamHostTest(unittest.TestCase):
    """RTE-03: compiles the generated RTE of test/param with the synthetic component CalSwc and runs
    test/param/rte_param_host_test.cpp (start values, parameter table, write through the table address)."""

    def test_param_runtime(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            out = tmp / "gen"
            rtegen.generate(PARAM / "rte.yaml", PROJECT, out, REPO)
            zig = [sys.executable, "-m", "ziglang"]
            includes = [f"-I{out}", f"-I{out / 'tasks'}", f"-I{REPO / 'libs/bsw/autosarBase/include'}",
                        f"-I{HERE / 'test' / 'can_host' / 'stubs'}", f"-I{PARAM / 'CalSwc/generated/src'}"]
            objects = []
            for src in (out / "Rte.c", PARAM / "CalSwc/generated/src/CalSwc.c"):
                obj = tmp / (src.stem + ".o")
                r = subprocess.run(zig + ["cc", "-std=c99", "-O1", "-Wall", "-Wextra", "-Werror", *includes, "-c",
                                          str(src), "-o", str(obj)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr[-3000:])
                objects.append(str(obj))
            exe = tmp / "rte_param_host_test.exe"
            r = subprocess.run(zig + ["c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror", *includes,
                                      str(out / "RteConfig.cpp"), str(PARAM / "rte_param_host_test.cpp"),
                                      *objects, "-o", str(exe)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            r = subprocess.run([str(exe)], capture_output=True, text=True)
            print("\n" + r.stdout.strip(), file=sys.stderr)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:])
            self.assertIn(", 0 failures", r.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


TYPES = HERE / "test" / "types"
TYPES_ECU = rtegen.with_resolved_paths(
    yaml.safe_load((TYPES / "rte.yaml").read_text(encoding="utf-8")), TYPES / "rte.yaml")
# RTE-04 runs on the S32K148 build-test profile (two CAN buses, no SBC), not on the Eval Board: the data types do
# not depend on the board; the enumeration -> BSW check uses the demo project for its ecu.state target.
TYPES_PROJECT = PROJECT_TWO_BUSES


class RteTypesTest(unittest.TestCase):
    """RTE-04: arrays, records (Simulink buses), enumerations and fixed point at sender-receiver ports. Components:
    the Simulink test model TypeSwc (code and ARXML from Embedded Coder R2024a) and the synthetic receiver
    TypeSink (test/types); BSW task profile test/multitask/tasks.yaml."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        # private copies: some tests change the ARXML; repo root of the run is self.tmp
        for name in ("TypeSwc", "TypeSink"):
            shutil.copytree(TYPES / name / "generated", self.tmp / name / "generated")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ecu(self, **connections):
        ecu = copy.deepcopy(TYPES_ECU)
        for comp in ecu["components"]:
            comp["path"] = comp["name"]
        ecu["connections"].update(connections)
        return ecu

    def run_types(self, ecu=None, project=TYPES_PROJECT):
        path = self.tmp / "rte.yaml"
        path.write_text(yaml.safe_dump(ecu or self.ecu(), sort_keys=False), encoding="utf-8")
        return rtegen.generate(path, project, self.tmp / "out", self.tmp, MULTI_TASKS)

    def expect(self, fragment, ecu=None, project=TYPES_PROJECT):
        with self.assertRaises(rtegen.RteError) as ctx:
            self.run_types(ecu, project)
        self.assertIn(fragment, str(ctx.exception))
        return str(ctx.exception)

    def generated(self, name):
        return (self.tmp / "out" / name).read_text(encoding="utf-8")

    def arxml(self, comp, file):
        return self.tmp / comp / "generated" / "arxml" / file

    def edit(self, comp, file, old, new, count=1):
        path = self.arxml(comp, file)
        text = path.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), count, old)
        path.write_text(text.replace(old, new), encoding="utf-8")

    def edit_re(self, comp, file, pattern, repl, count=1):
        path = self.arxml(comp, file)
        text, n = re.subn(pattern, repl, path.read_text(encoding="utf-8"), flags=re.S)
        self.assertEqual(n, count, pattern)
        path.write_text(text, encoding="utf-8")

    def reset(self):
        """Fresh copies of the components (a subtest changed them)."""
        self.tearDown()
        self.setUp()

    def test_types_are_parsed(self):
        comp = rtegen.parse_component("TypeSwc", TYPES / "TypeSwc" / "generated" / "arxml")
        samples, cmd, mode, temp = (comp.ports[f"{n}.{n}"].type for n in ("Samples", "Cmd", "ModeIn", "Temp"))
        self.assertEqual((samples.category, samples.name, samples.size, samples.element.native),
                         ("ARRAY", "rt_Array_uint16_4", 4, "uint16"))
        self.assertEqual((cmd.category, [n for n, _ in cmd.fields]), ("STRUCTURE", ["Mode", "Speed", "Limits", "Enable"]))
        fields = dict(cmd.fields)
        # the application record carries the scaling and the literals of its members
        self.assertEqual((fields["Speed"].name, fields["Speed"].native, fields["Speed"].numerator,
                          fields["Speed"].denominator), ("TypeSwc_Speed", "sint16", (0, 1), 16))
        self.assertEqual(fields["Mode"].literals, mode.literals)
        self.assertEqual((fields["Limits"].category, fields["Limits"].size), ("ARRAY", 2))
        self.assertEqual(mode.literals, (("TypeSwc_Mode_Off", 0), ("TypeSwc_Mode_Run", 1), ("TypeSwc_Mode_Fault", 3)))
        self.assertEqual((mode.native, mode.scaled), ("uint8", False))
        self.assertEqual((temp.name, temp.native, temp.numerator, temp.denominator), ("UFIX16_SP1_BN40", "uint16",
                                                                                      (-400, 1), 10))
        stat = comp.ports["Stat.Stat"].type
        self.assertEqual(dict(stat.fields)["Echo"].signature(), cmd.signature())   # nested record
        # init values: physical 0 degC of the fixed-point type is the stored 400; records and arrays in C syntax
        self.assertEqual(comp.ports["Temp.Temp"].init, "400")
        self.assertEqual(comp.ports["Cmd.Cmd"].init, "{0, 0, {0, 0}, 0}")
        self.assertEqual(comp.ports["Samples.Samples"].init, "{0, 0, 0, 0}")
        self.assertEqual([leaf for leaf, _ in stat.leaves()][-5:],
                         [".Echo.Mode", ".Echo.Speed", ".Echo.Limits[0]", ".Echo.Limits[1]", ".Echo.Enable"])

    def test_init_value_forms(self):
        sink = rtegen.parse_component("TypeSink", TYPES / "TypeSink" / "generated" / "arxml")
        self.assertEqual(sink.ports["Check.Check"].init, "7")             # NUMERICAL, implementation type
        self.assertEqual(sink.ports["LastMode.LastMode"].init, "1")       # TEXT: literal TypeSwc_Mode_Run
        self.assertEqual(sink.ports["LastTemp.LastTemp"].init, "655")     # APPLICATION: 25.5 degC
        self.assertEqual(sink.ports["Peak.Peak"].init, "0")               # none: zero

    def test_rte_type_header(self):
        self.run_types()
        header = self.generated("Rte_Type.h")
        at = {t: header.index(t) for t in ("typedef uint8 rt_Array_uint8_2[2];", "typedef sint16 TypeSwc_Speed;",
                                           "typedef uint8 TypeSwc_Mode;", "} TypeSwc_CmdBus;", "} TypeSwc_StatBus;")}
        for used in ("typedef uint8 rt_Array_uint8_2[2];", "typedef sint16 TypeSwc_Speed;",
                     "typedef uint8 TypeSwc_Mode;"):
            self.assertLess(at[used], at["} TypeSwc_CmdBus;"], "every type after the types it uses")
        self.assertLess(at["} TypeSwc_CmdBus;"], at["} TypeSwc_StatBus;"])
        self.assertIn("typedef uint16 rt_Array_uint16_4[4];", header)
        self.assertIn("typedef uint16 UFIX16_SP1_BN40;", header)
        self.assertIn("#ifndef TypeSwc_Mode_Fault\n#define TypeSwc_Mode_Fault ((TypeSwc_Mode)3U)\n#endif", header)
        self.assertIn("#ifndef DEFINED_TYPEDEF_FOR_TypeSwc_CmdBus_", header)
        self.assertIn("    rt_Array_uint8_2 Limits;\n    boolean Enable;\n} TypeSwc_CmdBus;", header)
        self.assertEqual(header.count("typedef uint8 TypeSwc_Mode;"), 1, "one definition for both components")

    def test_api_signatures(self):
        """Argument forms of Embedded Coder R2024a (stub Rte_TypeSwc.h): arrays by element pointer, records by
        pointer (write: const), scalars as before."""
        self.run_types()
        header = self.generated("Rte_TypeSwc.h")
        for decl in ("Rte_Read_TypeSwc_Samples_Samples(uint16* data)", "Rte_Read_TypeSwc_Cmd_Cmd(TypeSwc_CmdBus* data)",
                     "Rte_Read_TypeSwc_ModeIn_ModeIn(TypeSwc_Mode* data)",
                     "Rte_Read_TypeSwc_Temp_Temp(UFIX16_SP1_BN40* data)",
                     "Rte_Write_TypeSwc_Scaled_Scaled(const uint16* data)",
                     "Rte_Write_TypeSwc_Stat_Stat(const TypeSwc_StatBus* data)",
                     "Rte_Write_TypeSwc_ModeOut_ModeOut(TypeSwc_Mode data)"):
            self.assertIn(f"extern Std_ReturnType {decl};", header)
        rte_c = self.generated("Rte.c")
        self.assertIn("#include <string.h>", rte_c)
        self.assertIn("(void)memcpy(data, Rte_Cpy_fast_TypeSwc_Scaled_Scaled, sizeof(Rte_Cpy_fast_TypeSwc_Scaled_Scaled));",
                      rte_c)
        self.assertIn("    Rte_Cpy_fast_TypeSwc_Stat_Stat = *data;", rte_c)
        self.assertIn("    *data = Rte_Buf_TypeSwc_Cmd_Cmd;", rte_c)
        self.assertIn("TypeSwc_StatBus Rte_Buf_TypeSwc_Stat_Stat = {0, 0, 0, {0, 0, {0, 0}, 0}};", rte_c)
        self.assertIn("uint16 Rte_Buf_TypeSwc_Temp_Temp = 400;", rte_c)

    def test_task_handover_of_records_and_arrays(self):
        _, buffers, tasks = self.run_types()
        stat, scaled = buffers["TypeSwc.Stat.Stat"], buffers["TypeSwc.Scaled.Scaled"]
        self.assertEqual(stat.copies, {"fast": "w", "slow": "r"})
        self.assertEqual(scaled.copies, {"fast": "w"})
        config = self.generated("RteConfig.cpp")
        self.assertIn("#include <cstring>", config)
        # array: memcpy, record: assignment, both under the lock of the task boundary
        self.assertIn("(void)std::memcpy(&Rte_Buf_TypeSwc_Scaled_Scaled, &Rte_Cpy_fast_TypeSwc_Scaled_Scaled, "
                      "sizeof(Rte_Buf_TypeSwc_Scaled_Scaled));", config)
        self.assertIn("Rte_Cpy_slow_TypeSwc_Stat_Stat = Rte_Buf_TypeSwc_Stat_Stat;", config)
        # TypeSink.Scaled is read in fast and slow: published value under the lock
        self.assertIn("Std_ReturnType Rte_Read_TypeSink_Scaled_Scaled(uint16* data)\n{\n    ::async::LockType const lock;"
                      "\n    (void)std::memcpy(data, &Rte_Buf_TypeSwc_Scaled_Scaled, sizeof(Rte_Buf_TypeSwc_Scaled_Scaled));",
                      config)

    def test_signal_table_lists_elements(self):
        self.run_types()
        config = self.generated("RteConfig.cpp")
        self.assertIn('{"TypeSwc.Stat.Stat.Echo.Limits[1]", nullptr, SignalType::UINT8, '
                      '&Rte_Buf_TypeSwc_Stat_Stat.Echo.Limits[1]},', config)
        self.assertIn('{"TypeSwc.Cmd.Cmd.Speed", "rte.init", SignalType::SINT16, &Rte_Buf_TypeSwc_Cmd_Cmd.Speed},',
                      config)
        self.assertIn('{"TypeSwc.ModeOut.ModeOut", nullptr, SignalType::UINT8, &Rte_Buf_TypeSwc_ModeOut_ModeOut},',
                      config)
        self.assertNotIn('"TypeSwc.Stat.Stat"', config)

    def test_connection_type_checks(self):
        cases = [
            ({"TypeSink.Scaled.Scaled": "TypeSwc.Stat.Stat"},
             "type rt_Array_uint16_4 does not match TypeSwc_StatBus of the provided port (type: array rt_Array_uint16_4 "
             "vs structure TypeSwc_StatBus)"),
            ({"TypeSwc.Temp.Temp": "TypeSink.Peak.Peak"},
             "type UFIX16_SP1_BN40 does not match uint16 of the provided port (type: UFIX16_SP1_BN40 (uint16) "
             "[physical = (-400 + 1 * stored) / 10] vs uint16)"),
            ({"TypeSwc.ModeIn.ModeIn": "TypeSink.Peak.Peak"}, "type uint8 != uint16"),
            ({"TypeSwc.Temp.Temp": "TypeSink.LastTemp.LastTemp"}, None),     # same fixed-point type
            ({"TypeSwc.ModeIn.ModeIn": "TypeSink.LastMode.LastMode"}, None),  # same enumeration
        ]
        for connections, fragment in cases:
            with self.subTest(connections=connections):
                if fragment is None:
                    self.run_types(self.ecu(**connections))
                else:
                    self.expect(fragment, self.ecu(**connections))

    def test_enumeration_without_literals_does_not_match(self):
        # the enumeration of a provided port without literals (plain uint8) is not the enumeration TypeSwc_Mode
        self.edit("TypeSink", "TypeSink.arxml", "/DataTypes/ApplDataTypes/TypeSwc_Mode</TYPE-TREF>",
                  "/DataTypes/uint8</TYPE-TREF>")
        self.edit("TypeSink", "TypeSink.arxml", '<TYPE-TREF DEST="APPLICATION-PRIMITIVE-DATA-TYPE">/DataTypes/uint8',
                  '<TYPE-TREF DEST="IMPLEMENTATION-DATA-TYPE">/DataTypes/uint8')
        self.edit("TypeSink", "TypeSink.arxml", "<TEXT-VALUE-SPECIFICATION><VALUE>TypeSwc_Mode_Run</VALUE>"
                  "</TEXT-VALUE-SPECIFICATION>", "<NUMERICAL-VALUE-SPECIFICATION><VALUE>1</VALUE>"
                  "</NUMERICAL-VALUE-SPECIFICATION>")
        self.expect("(type: TypeSwc_Mode (uint8) [enumeration TypeSwc_Mode_Off=0, TypeSwc_Mode_Run=1, "
                    "TypeSwc_Mode_Fault=3] vs uint8)", self.ecu(**{"TypeSwc.ModeIn.ModeIn": "TypeSink.LastMode.LastMode"}))

    def test_record_members_must_match(self):
        # a receiver whose record has another member layout under the same name cannot be generated at all
        self.edit("TypeSink", "TypeSink_datatype.arxml", "<SHORT-NAME>Max</SHORT-NAME>", "<SHORT-NAME>Peak</SHORT-NAME>",
                  count=2)
        text = self.expect("components: type TypeSwc_StatBus is defined differently by TypeSwc.Stat.Stat")
        self.assertIn("TypeSink.Stat.Stat (TypeSwc_StatBus (record Sum, Peak, Mode, Echo))", text)

    def test_array_size_must_match(self):
        self.edit("TypeSink", "TypeSink_datatype.arxml", "<ARRAY-SIZE>4</ARRAY-SIZE>", "<ARRAY-SIZE>5</ARRAY-SIZE>")
        self.expect("components: type rt_Array_uint16_4 is defined differently by TypeSwc.Samples.Samples")

    def test_composite_and_scaled_ports_stay_between_components(self):
        project = PROJECT   # eval board: has the targets adc.raw.* and ecu.state
        adc = sorted(rtegen.board_signals(PROJECT)["adc.raw"])[0]
        cases = [
            ({"TypeSwc.Samples.Samples": f"adc.raw.{adc}"}, "rt_Array_uint16_4 (uint16[4]) is an array, BSW and CAN "
                                                            "targets take unscaled scalars only"),
            ({"TypeSwc.Cmd.Cmd": "ecu.state"}, "(record Mode, Speed, Limits, Enable) is a record"),
            ({"TypeSwc.Temp.Temp": f"adc.raw.{adc}"}, "UFIX16_SP1_BN40 (uint16) [physical = (-400 + 1 * stored) / 10] "
                                                      "is fixed point"),
            ({"TypeSwc.Stat.Stat": ["rte", "ecu.sleep_request"]}, "is a record"),
        ]
        for connections, fragment in cases:
            with self.subTest(connections=connections):
                self.expect(fragment, self.ecu(**connections), project)
        # an enumeration is an integer: allowed where its platform type fits
        self.run_types(self.ecu(**{"TypeSwc.ModeIn.ModeIn": "ecu.state"}), project)
        self.assertIn("Rte_Buf_TypeSwc_ModeIn_ModeIn = static_cast<uint8>(state);", self.generated("RteConfig.cpp"))

    def test_init_value_errors(self):
        cases = [
            ("TypeSink.arxml", "<VALUE>TypeSwc_Mode_Run</VALUE>", "<VALUE>TypeSwc_Mode_Stop</VALUE>",
             "TypeSink.LastMode.LastMode: init value: init value TypeSwc_Mode_Stop is no literal of TypeSwc_Mode "
             "(literals: TypeSwc_Mode_Off, TypeSwc_Mode_Run, TypeSwc_Mode_Fault)"),
            ("TypeSink.arxml", "<V>25.5</V>", "<V>6600</V>",
             "TypeSink.LastTemp.LastTemp: init value: init value 6600 does not fit UFIX16_SP1_BN40"),
            ("TypeSink.arxml", "<V>25.5</V>", "<V>-40.04</V>", None),   # rounds to the nearest step: stored 0
            ("TypeSink.arxml", "<VALUE>7</VALUE>", "<VALUE>7.5</VALUE>",
             "TypeSink.Check.Check: init value: init value 15/2 is no whole number, but uint32 is uint32"),
            ("TypeSink.arxml", "<VALUE>7</VALUE>", "<VALUE>seven</VALUE>", "'seven' is no number"),
        ]
        for file, old, new, fragment in cases:
            with self.subTest(new=new):
                self.reset()
                self.edit("TypeSink", file, old, new)
                if fragment is None:
                    _, buffers, _ = self.run_types()
                    self.assertEqual(buffers["TypeSink.LastTemp.LastTemp"].init, "0")
                else:
                    self.expect(fragment)

    def test_record_and_array_init_shapes(self):
        # an array init value with a missing element (Samples and Scaled lose their 4th element)
        self.edit_re("TypeSwc", "TypeSwc_component.arxml",
                     r"<NUMERICAL-VALUE-SPECIFICATION>\s*<SHORT-LABEL>DefaultInitValue_rt_Array_uint16_4_rt_Array_"
                     r"uint16_4_4</SHORT-LABEL>\s*<VALUE>0</VALUE>\s*</NUMERICAL-VALUE-SPECIFICATION>", "", count=2)
        self.expect("TypeSwc.Samples.Samples: init value: init value has 3 element(s), rt_Array_uint16_4 "
                    "(uint16[4]) has 4")
        # a record init value without its boolean member (command record of Cmd and of Stat.Echo)
        self.reset()
        self.edit_re("TypeSwc", "TypeSwc_component.arxml",
                     r"<APPLICATION-VALUE-SPECIFICATION>\s*<SHORT-LABEL>DefaultInitValue_boolean</SHORT-LABEL>"
                     r"(?:(?!</APPLICATION-VALUE-SPECIFICATION>).)*</APPLICATION-VALUE-SPECIFICATION>", "", count=2)
        self.expect("TypeSwc.Cmd.Cmd: init value: init value has 3 field(s), TypeSwc_CmdBus (record Mode, Speed, "
                    "Limits, Enable) has 4")

    def test_unsupported_types_are_named(self):
        cases = [
            (r"<ARRAY-SIZE-SEMANTICS>FIXED-SIZE</ARRAY-SIZE-SEMANTICS>",
             "<ARRAY-SIZE-SEMANTICS>VARIABLE-SIZE</ARRAY-SIZE-SEMANTICS>", 3,
             "only fixed-size arrays are supported"),
            (r"<CATEGORY>STRUCTURE</CATEGORY>(\s*<SUB-ELEMENTS>)", r"<CATEGORY>UNION</CATEGORY>\1", 2,
             "category UNION is not supported (VALUE, TYPE_REFERENCE, ARRAY, STRUCTURE)"),
            (r"(<LOWER-LIMIT[^>]*>)3(</LOWER-LIMIT>)", r"\g<1>2\2", 1,
             "every COMPU-SCALE needs one value (LOWER-LIMIT = UPPER-LIMIT) and a literal"),
            (r"<VT>TypeSwc_Mode_Run</VT>", "<VT>Mode Run</VT>", 1,
             "enumeration literal 'Mode Run' of COMPU_TypeSwc_Mode is no C identifier"),
            (r"(<SHORT-NAME>COMPU_UFIX16_SP1_BN40</SHORT-NAME>\s*<CATEGORY>)LINEAR", r"\1SCALE_LINEAR", 1,
             "compu method COMPU_UFIX16_SP1_BN40 of category SCALE_LINEAR is not supported"),
        ]
        for pattern, repl, count, fragment in cases:
            with self.subTest(fragment=fragment):
                self.reset()
                self.edit_re("TypeSwc", "TypeSwc_datatype.arxml", pattern, repl, count)
                self.expect(fragment)

    def test_literal_values_of_two_enumerations_must_agree(self):
        types = [rtegen.DataType("/A", "A", "uint8", literals=(("MODE_ON", 1),)),
                 rtegen.DataType("/B", "B", "uint8", literals=(("MODE_ON", 2),))]
        with self.assertRaises(rtegen.RteError) as ctx:
            rtegen.render_type_definitions(types)
        self.assertIn("enumeration literal MODE_ON is 1 in A and 2 in B", str(ctx.exception))

    def test_signal_scalings_of_fixed_point_rows(self):
        """RTE-29: rte::SIGNAL_SCALINGS lists the fixed point rows of rte::SIGNALS with the rational conversion of
        their type, unit (from the compu method of the ARXML) and the decimals of the console."""
        unit = ('<UNIT UUID="00000000-0000-0000-0000-000000000029"><SHORT-NAME>degC</SHORT-NAME>'
                '<DISPLAY-NAME>degC</DISPLAY-NAME><FACTOR-SI-TO-UNIT>1</FACTOR-SI-TO-UNIT>'
                '<OFFSET-SI-TO-UNIT>0</OFFSET-SI-TO-UNIT></UNIT>')
        self.edit_re("TypeSwc", "TypeSwc_datatype.arxml",
                     r"(<SHORT-NAME>COMPU_UFIX16_SP1_BN40</SHORT-NAME>\s*<CATEGORY>LINEAR</CATEGORY>\s*"
                     r"<UNIT-REF DEST=\"UNIT\">)/DataTypes/Units/NoUnit", r"\1/DataTypes/Units/degC")
        self.edit_re("TypeSwc", "TypeSwc_datatype.arxml", r"(<SHORT-NAME>Units</SHORT-NAME>\s*<ELEMENTS>)",
                     r"\1" + unit)
        self.run_types()
        header, config = self.generated("RteConfig.h"), self.generated("RteConfig.cpp")
        self.assertIn("#define NX_RTE_HAS_SIGNAL_SCALING 1", header)
        self.assertIn("constexpr size_t SIGNAL_SCALING_COUNT = 5U;", header)
        rows = re.findall(r'^\s*\{"([^"]+)", ', config.split("Signal const SIGNALS[] = {")[1].split("};")[0], re.M)
        scalings = re.findall(r'^\s*\{(\d+)U, (\d+)U, "([^"]*)", \{(-?\d+), (-?\d+)\}, (\d+)\}, // (\S+)$', config,
                              re.M)
        self.assertEqual([(rows[int(r)], label) for r, *_, label in scalings], [(label, label) for *_, label in scalings])
        self.assertEqual([(label, int(dec), unit_, int(n0), int(n1), int(d))
                          for _r, dec, unit_, n0, n1, d, label in scalings], [
            ("TypeSwc.Stat.Stat.Echo.Speed", 4, "", 0, 1, 16),         # record member, 1/16 rpm: 0.0625 exactly
            ("TypeSwc.TempOut.TempOut", 1, "degC", -400, 1, 10),        # bias: (-400 + stored) / 10
            ("TypeSink.LastTemp.LastTemp", 1, "", -400, 1, 10),         # TypeSink's own compu method (NoUnit)
            ("TypeSwc.Cmd.Cmd.Speed", 4, "", 0, 1, 16),
            ("TypeSwc.Temp.Temp", 1, "degC", -400, 1, 10),
        ])
        # an enumeration, a boolean or a plain integer row has no entry
        self.assertNotIn("TypeSwc.ModeOut.ModeOut\n", config.split("SIGNAL_SCALINGS[] = {")[1])

    def test_without_fixed_point_no_scaling_table(self):
        """RTE-29: an ECU without fixed point signals keeps rte::Signal and its tables as before (same image)."""
        rtegen.generate(MULTI / "rte.yaml", PROJECT_TWO_BUSES, self.tmp / "plain", REPO, MULTI_TASKS)
        for name in ("RteConfig.h", "RteConfig.cpp"):
            text = (self.tmp / "plain" / name).read_text(encoding="utf-8")
            self.assertNotIn("SIGNAL_SCALING", text, name)
            self.assertNotIn("SignalScaling", text, name)

    def test_scaling_decimals(self):
        cases = [((0, 1), 16, 4), ((-400, 1), 10, 1), ((0, 5), 1, 0), ((0, 1), 1000, 3), ((1, 1), 4, 2),
                 ((0, 1), 3, 2),        # not a finite decimal: the step 0.33 with one digit more than its first
                 ((0, 1), 7000000, 6),  # capped
                 ((0, 3), 7, 2)]
        for numerator, d, expected in cases:
            with self.subTest(numerator=numerator, d=d):
                self.assertEqual(rtegen.scaling_decimals(numerator, d), expected)

    def test_scaling_beyond_64_bit_is_refused(self):
        """The console computes (n0 + n1 * stored) * 10^decimals in int64_t: fewer decimals where needed, an error
        when not even the value itself fits."""
        def buffer(native, numerator, d):
            t = rtegen.DataType("/T", "T", native, numerator=numerator, denominator=d)
            return rtegen.Buffer("Rte_Buf_A_P_P", native, "0", "A.P.P", dtype=t)
        rows = rtegen.signal_scalings([buffer("sint32", (0, 1), 1000)], [])
        self.assertEqual((rows[0].signal, rows[0].decimals), (0, 3))
        rows = rtegen.signal_scalings([buffer("uint32", (0, 2**31 - 1), 3)], [])
        self.assertEqual(rows[0].decimals, 0)          # 2^31 * 2^32 / 3: no decimal fits
        with self.assertRaises(rtegen.RteError) as ctx:
            rtegen.signal_scalings([buffer("uint32", (-(2**31), -(2**31)), 1)], [])
        self.assertIn("A.P.P: scaling physical = (-2147483648 + -2147483648 * stored) / 1", str(ctx.exception))

    def test_validate_reports_type_errors_with_yaml_path(self):
        """TOOL-07/Prüffrage 8: the Studio check reports type problems of connections with file, line and path."""
        ecu = self.ecu(**{"TypeSwc.Temp.Temp": "TypeSink.Peak.Peak"})
        for comp in ecu["components"]:
            comp["path"] = (self.tmp / comp["name"]).as_posix()
        ecu_file = self.tmp / "validate" / "rte.yaml"
        ecu_file.parent.mkdir(parents=True, exist_ok=True)
        ecu_file.write_text(yaml.safe_dump(ecu, sort_keys=False), encoding="utf-8")
        base = REPO / "projects" / "buildtest_s32k148_min" / "project.yaml"
        doc = yaml.safe_load(base.read_text(encoding="utf-8"))
        doc["project"]["board"] = (base.parent / doc["project"]["board"]).resolve().as_posix()
        doc["project"]["rte"] = ecu_file.as_posix()
        project = self.tmp / "validate" / "project.yaml"
        project.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        messages = rtegen.validate(project, REPO, tasks_manifest=MULTI_TASKS)
        match = [m for m in messages if "does not match uint16 of the provided port" in m.text]
        self.assertTrue(match, [str(m) for m in messages])
        self.assertEqual((match[0].severity, match[0].file.name, match[0].yaml_path),
                         ("error", "rte.yaml", "connections.TypeSwc.Temp.Temp"))
        self.assertIsNotNone(match[0].line)
        # a conflict between the components points at the components section
        self.edit("TypeSink", "TypeSink_datatype.arxml", "<ARRAY-SIZE>4</ARRAY-SIZE>", "<ARRAY-SIZE>5</ARRAY-SIZE>")
        messages = rtegen.validate(project, REPO, tasks_manifest=MULTI_TASKS)
        match = [m for m in messages if "is defined differently" in m.text]
        self.assertTrue(match, [str(m) for m in messages])
        self.assertEqual(match[0].yaml_path, "components")
        self.assertIsNotNone(match[0].line)


class RteTypesHostTest(unittest.TestCase):
    """RTE-04 SIL: compiles the generated RTE of test/types with the Embedded Coder code of the Simulink test model
    TypeSwc and the synthetic receiver TypeSink, runs test/types/rte_types_host_test.cpp."""

    def test_types_runtime(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            out = tmp / "gen"
            rtegen.generate(TYPES / "rte.yaml", TYPES_PROJECT, out, REPO, MULTI_TASKS)
            zig = [sys.executable, "-m", "ziglang"]
            includes = [f"-I{out}", f"-I{out / 'tasks'}", f"-I{REPO / 'libs/bsw/autosarBase/include'}",
                        f"-I{HERE / 'test' / 'can_host' / 'stubs'}", f"-I{TYPES / 'TypeSwc/generated/src'}",
                        f"-I{TYPES / 'TypeSink/generated/src'}"]
            objects = []
            for src in (out / "Rte.c", TYPES / "TypeSwc/generated/src/TypeSwc.c",
                        TYPES / "TypeSink/generated/src/TypeSink.c"):
                obj = tmp / (src.stem + ".o")
                r = subprocess.run(zig + ["cc", "-std=c99", "-O1", "-Wall", "-Wextra", "-Werror", *includes, "-c",
                                          str(src), "-o", str(obj)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr[-3000:])
                objects.append(str(obj))
            exe = tmp / "rte_types_host_test.exe"
            r = subprocess.run(zig + ["c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror", *includes,
                                      str(out / "RteConfig.cpp"), str(TYPES / "rte_types_host_test.cpp"),
                                      *objects, "-o", str(exe)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            r = subprocess.run([str(exe)], capture_output=True, text=True)
            print("\n" + r.stdout.strip(), file=sys.stderr)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:])
            self.assertIn(", 0 failures", r.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# every ECU description in the repository: projects, test profiles of rtegen and of neXtBSW Studio
ALL_ECU_FILES = (sorted(REPO.glob("projects/*/rte.yaml")) + sorted(HERE.glob("test/*/*rte.yaml"))
                 + sorted(REPO.glob("tools/studio/tests/profiles/rte_*.yaml")))


class RteSchemaTest(unittest.TestCase):
    """ARCH-34: rtegen reads the ECU description through the schema EcuDescription of tools/nxconfig."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_every_ecu_description_fits_the_schema(self):
        self.assertGreaterEqual(len(ALL_ECU_FILES), 11)
        for file in ALL_ECU_FILES:
            with self.subTest(file=file.relative_to(REPO).as_posix()):
                ecu, messages, _ = rtegen.nxconfig.config.read_rte(file)
                self.assertIsNotNone(ecu, [str(m) for m in messages])
                self.assertEqual(messages, [])

    def test_schema_values_match_the_generator(self):
        import typing
        model = rtegen.nxmodel
        self.assertEqual(typing.get_args(model.E2eUse.model_fields["profile"].annotation), tuple(rtegen.E2E_PROFILES))
        modes = {m for spec in rtegen.E2E_PROFILES.values() for m in spec["modes"]}
        literal = model.E2eUse.model_fields["data_id_mode"].annotation
        self.assertEqual(set(typing.get_args(typing.get_args(literal)[0])), modes)
        self.assertEqual(typing.get_args(model.QualifierUse.model_fields["on_error"].annotation), rtegen.ON_ERROR)
        self.assertEqual(rtegen.TASK_NAME_RE.pattern, r"^[a-z][a-z0-9]*$")   # manifest tasks: same rule
        self.assertEqual(rtegen.RESERVED_TASK_NAMES, {"idle", "timer"})
        # RTE-22: schema_version is the first field of the description now, so it also starts a YAML path
        self.assertEqual(rtegen.ECU_SECTIONS, ("schema_version", "tasks", "base_period_ms", "components", "can",
                                               "uds", "connections", "ecu_state"))

    def test_all_schema_findings_at_once_with_yaml_path(self):
        ecu = copy.deepcopy(MULTI_ECU)
        ecu["tasks"][0]["prio"] = 3
        ecu["tasks"][1]["period_ms"] = "10"
        ecu["connections"]["AppSwc.Led.Led"] = []
        path = self.tmp / "rte.yaml"
        path.write_text(yaml.safe_dump(ecu, sort_keys=False), encoding="utf-8")
        with self.assertRaises(rtegen.RteError) as ctx:
            rtegen.generate(path, PROJECT, self.tmp / "out", REPO, MULTI_TASKS)
        lines = str(ctx.exception).splitlines()
        self.assertEqual([line.split(":", 1)[0] for line in lines],
                         ["tasks[0].prio", "tasks[1].period_ms", "connections.AppSwc.Led.Led"])
        self.assertFalse((self.tmp / "out").exists())            # nothing written

    def test_nxconfig_check_reports_the_ecu_description(self):
        ecu = copy.deepcopy(ECU)
        ecu["tasks"][0]["name"] = "Swc_1"
        ecu["can"]["qualifiers"]["PC_Command"]["on_error"] = "keep"
        ecu_file = self.tmp / "rte.yaml"
        ecu_file.write_text(yaml.safe_dump(ecu, sort_keys=False), encoding="utf-8")
        doc = yaml.safe_load(PROJECT_FILE.read_text(encoding="utf-8"))
        doc["project"]["board"] = (PROJECT_FILE.parent / doc["project"]["board"]).resolve().as_posix()
        doc["project"]["rte"] = ecu_file.as_posix()
        doc["project"].pop("diag", None)   # relative to the demo folder (ARCH-51)
        project = self.tmp / "project.yaml"
        project.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        config, messages = rtegen.nxconfig.check(project)
        self.assertIsNone(config)
        found = [(m.file.name, m.yaml_path, m.line) for m in messages if m.severity == "error"]
        tree = ecu_file.read_text(encoding="utf-8").splitlines()
        self.assertEqual([(f, p) for f, p, _ in found],
                         [("rte.yaml", "tasks[0].name"), ("rte.yaml", "can.qualifiers.PC_Command.on_error")])
        for _, path, line in found:
            self.assertIn(path.rsplit(".", 1)[-1] + ":", tree[line - 1])   # the line of the key itself
        # rtegen.validate (neXtBSW Studio) reports the same messages and stops before the generator
        self.assertEqual([m for m in rtegen.validate(project, REPO) if m.severity == "error"],
                         [m for m in messages if m.severity == "error"])
        # the checked-in project loads its ECU description as model
        self.assertIsInstance(PROJECT.rte, rtegen.nxmodel.EcuDescription)
        self.assertEqual([c.name for c in PROJECT.rte.components], ["DemoSwc"])

    def test_mapping_input_checks_only_the_sections_used(self):
        """tools/schedana, tools/boardtest and the Studio pass the plain mapping of an rte.yaml."""
        dbc = self.tmp / "can.dbc"
        dbc.write_text(CAN_DBC, encoding="utf-8")
        ecu = {"tasks": [{"name": "Bad_Name"}], "can": {"dbc": dbc.as_posix(), "node": "EVB"}}
        can = rtegen.load_can(ecu, REPO, ["CAN0"])                # an unfinished task list does not matter here
        self.assertEqual((can.node, can.bus), ("EVB", "CAN0"))
        with self.assertRaisesRegex(rtegen.RteError, r"^uds\.bus: Field required"):
            rtegen.load_uds({"uds": {}}, None, ["CAN0"])
        with self.assertRaisesRegex(rtegen.RteError, r"^tasks\[0\]\.name: String should match pattern"):
            rtegen.build_tasks(ecu, {}, rtegen.load_task_manifest(MULTI_TASKS), None)
        with self.assertRaisesRegex(rtegen.RteError, "expected a mapping"):
            rtegen.load_uds([], None, ["CAN0"])


BOARD_NAMES = HERE / "test" / "board_names"


def board_names_project(tmp: Path, rte: Path | None = None):
    """Copy of the RTE-11 test profile (test/board_names) in `tmp`, optionally with another rte.yaml; loaded."""
    doc = yaml.safe_load((BOARD_NAMES / "project.yaml").read_text(encoding="utf-8"))
    doc["project"]["board"] = (BOARD_NAMES / "board").as_posix()
    doc["project"]["rte"] = (rte or BOARD_NAMES / "rte.yaml").as_posix()
    path = tmp / "board_names_project" / "project.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return path


class RteBoardNamesTest(unittest.TestCase):
    """RTE-11: board targets are written as the board profile spells them (leading digit, lower case), the internal
    converter inputs of project.yaml adc_internal are adc.mv/adc.raw targets; the C identifiers stay internal."""

    setUp, tearDown = RteGenTest.setUp, RteGenTest.tearDown
    ECU = rtegen.with_resolved_paths(
        yaml.safe_load((BOARD_NAMES / "rte.yaml").read_text(encoding="utf-8")), BOARD_NAMES / "rte.yaml")

    def config(self):
        return rtegen.nxconfig.load(BOARD_NAMES / "project.yaml")

    def run_with(self, ecu):
        path = self.tmp / "rte.yaml"
        path.write_text(yaml.safe_dump(ecu, sort_keys=False), encoding="utf-8")
        return rtegen.generate(path, self.config(), self.tmp / "out", REPO)

    def ecu(self, **connections):
        ecu = copy.deepcopy(self.ECU)
        ecu["connections"].update(connections)
        return ecu

    def expect_error(self, ecu, fragment):
        with self.assertRaises(rtegen.RteError) as ctx:
            self.run_with(ecu)
        self.assertIn(fragment, str(ctx.exception))
        return str(ctx.exception)

    def test_targets_are_the_board_names(self):
        signals = rtegen.board_signals(self.config())
        board_adc = {"12VBAT_CURRENT", "vbat_sense", "5V_SPARE"}
        internal = {"1V_BANDGAP", "supply_int"}
        self.assertEqual(signals["adc.mv"], board_adc | internal)
        self.assertEqual(signals["adc.raw"], board_adc | internal)
        self.assertEqual(signals["adc.internal"], internal)
        # sensor plans only for fitted board channels: an internal input has no circuit, 5V_SPARE is not fitted
        self.assertEqual(signals["adc.phys"], {"12VBAT_CURRENT", "vbat_sense"})
        self.assertEqual(signals["adc.qlf"], {"12VBAT_CURRENT", "vbat_sense"})
        self.assertEqual(signals["adc.unfitted"], {"5V_SPARE"})
        self.assertEqual(signals["dio.in"], {"button_a"})
        self.assertEqual(signals["dio.out"], {"3V3_EN", "led_status"})
        self.assertFalse(any(name.startswith("SIG_") for kind in ("adc.mv", "dio.out") for name in signals[kind]))

    def test_generated_identifiers_are_those_of_the_board_headers(self):
        """The RTE uses the C identifiers bswgen generates for the same names (SIG_ for a leading digit, upper case);
        the console table shows the names of the ECU description."""
        sys.path.insert(0, str(REPO / "tools" / "bswgen"))
        import bswgen  # noqa: E402

        _, buffers, _ = self.run_with(self.ECU)
        b = buffers["SensorSwc.TsCurrent.TsCurrent"]
        self.assertEqual((b.bsw, b.bsw_signal, b.bsw_name), ("adc.phys", "SIG_12VBAT_CURRENT", "12VBAT_CURRENT"))
        b = buffers["DemoSwc.MotorNtc_mV.MotorNtc_mV"]
        self.assertEqual((b.bsw, b.bsw_signal, b.bsw_name), ("adc.mv", "SIG_1V_BANDGAP", "1V_BANDGAP"))
        config = (self.tmp / "out" / "RteConfig.cpp").read_text(encoding="utf-8")
        self.assertIn("::bios::AnalogInputScale::get(::bios::AnalogInput::Ai_SIG_1V_BANDGAP, value)", config)
        self.assertIn("nxAdcPhys_SIG_12VBAT_CURRENT(sensor_SIG_12VBAT_CURRENT_raw, ", config)
        self.assertIn("::bios::Output::set(::bios::Output::SIG_3V3_EN, ", config)
        self.assertIn("::bios::DigitalInput::get(::bios::DigitalInput::BUTTON_A, ", config)
        for label in ("adc.mv.1V_BANDGAP", "adc.phys.12VBAT_CURRENT", "adc.qlf.vbat_sense", "dio.out.3V3_EN",
                      "dio.in.button_a"):
            self.assertIn(f'"{label}"', config)

        board = self.tmp / "board"
        bswgen.generate(board_names_project(self.tmp), board)
        headers = "\n".join(p.read_text(encoding="utf-8") for p in board.rglob("*.h"))
        declared = set(re.findall(r"\b(\w+),", headers)) | set(re.findall(r"nxAdcPhys_(\w+)\(", headers))
        used = (re.findall(r"::bios::AnalogInput::(Ai_\w+)", config) + re.findall(r"::bios::Output::(\w+),", config)
                + re.findall(r"::bios::DigitalInput::(\w+),", config) + re.findall(r"nxAdcPhys_(\w+)\(", config))
        self.assertTrue(used)
        for ident in used:
            self.assertIn(ident, declared)
        for ident in re.findall(r"\b(?:Ai_|nxAdcPhys_|Output::|DigitalInput::)(\w+)", config):
            self.assertRegex(ident, r"^[A-Za-z_]\w*$")

    def test_raw_value_of_an_internal_input(self):
        self.run_with(self.ecu(**{"DemoSwc.MotorNtc_mV.MotorNtc_mV": "adc.raw.supply_int"}))
        config = (self.tmp / "out" / "RteConfig.cpp").read_text(encoding="utf-8")
        self.assertIn("::bios::AnalogInput::get(::bios::AnalogInput::Ai_SUPPLY_INT, value)", config)

    def test_c_spelling_is_refused_with_the_board_name(self):
        cases = [
            ({"DemoSwc.MotorNtc_mV.MotorNtc_mV": "adc.mv.SIG_12VBAT_CURRENT"},
             "SIG_12VBAT_CURRENT is not a adc.mv signal used by the project; write adc.mv.12VBAT_CURRENT as in the "
             "board profile (the C identifier SIG_12VBAT_CURRENT is internal)"),
            ({"DemoSwc.MotorNtc_mV.MotorNtc_mV": "adc.mv.SIG_1V_BANDGAP"}, "write adc.mv.1V_BANDGAP as in"),
            ({"DemoSwc.Led2.Led2": "dio.out.LED_STATUS"}, "write dio.out.led_status as in the board profile"),
            ({"SensorSwc.Proxy.Proxy": "adc.phys.VBAT_SENSE"}, "write adc.phys.vbat_sense as in the board profile"),
        ]
        for connections, fragment in cases:
            with self.subTest(connections=connections):
                self.expect_error(self.ecu(**connections), fragment)

    def test_internal_input_has_no_physical_value(self):
        for kind in ("adc.phys", "adc.qlf"):
            port = "SensorSwc.Proxy.Proxy" if kind == "adc.phys" else "SensorSwc.ProxyQlf.ProxyQlf"
            with self.subTest(kind=kind):
                self.expect_error(self.ecu(**{port: f"{kind}.1V_BANDGAP"}),
                                  f"1V_BANDGAP is an internal ADC channel (project.yaml adc_internal) without circuit "
                                  f"or sensor plan, there is no physical value; use adc.mv.1V_BANDGAP")

    def test_unknown_name(self):
        text = self.expect_error(self.ecu(**{"DemoSwc.MotorNtc_mV.MotorNtc_mV": "adc.mv.BANDGAP"}),
                                 "connection DemoSwc.MotorNtc_mV.MotorNtc_mV -> adc.mv.BANDGAP: BANDGAP is not a "
                                 "adc.mv signal used by the project")
        self.assertNotIn("write", text)

    def test_unfitted_channel_warns_with_the_board_name(self):
        _, buffers, _ = self.run_with(self.ecu(**{"DemoSwc.MotorNtc_mV.MotorNtc_mV": "adc.mv.5V_SPARE"}))
        warnings = rtegen.adc_warnings(buffers, rtegen.board_signals(self.config()))
        self.assertEqual(warnings, ["connections: the circuit of ADC channel 5V_SPARE is not fitted on the board "
                                    "(board.yaml adc fitted: false), adc.mv/adc.raw.5V_SPARE read an open pin"])

    def test_two_names_with_one_c_identifier(self):
        with self.assertRaises(rtegen.RteError) as ctx:
            rtegen.resolve_board_name({"12V", "SIG_12V"}, "12V", "connection A.B.C -> adc.mv.12V", "adc.mv")
        self.assertIn("connection A.B.C -> adc.mv.12V: board names 12V and SIG_12V give the same C identifier "
                      "SIG_12V", str(ctx.exception))
        self.assertEqual(rtegen.resolve_board_name({"12V", "VBAT"}, "12V", "x", "adc.mv"), "SIG_12V")

    def test_validate_reports_the_yaml_path(self):
        """Prüffrage 8: the message of an unknown name carries rte.yaml, line and the path of the connection."""
        ecu_file = self.tmp / "validate" / "rte.yaml"
        ecu_file.parent.mkdir(parents=True, exist_ok=True)
        ecu_file.write_text(yaml.safe_dump(self.ecu(**{"DemoSwc.Led1.Led1": "dio.out.SIG_3V3_EN"}), sort_keys=False),
                            encoding="utf-8")
        messages = rtegen.validate(board_names_project(self.tmp, ecu_file), REPO)
        match = [m for m in messages if "write dio.out.3V3_EN as in the board profile" in m.text]
        self.assertTrue(match, [str(m) for m in messages])
        self.assertEqual((match[0].severity, match[0].file.name, match[0].yaml_path),
                         ("error", "rte.yaml", "connections.DemoSwc.Led1.Led1"))
        self.assertIsNotNone(match[0].line)
        # the checked-in description of the profile is clean
        self.assertEqual([str(m) for m in rtegen.validate(board_names_project(self.tmp), REPO)
                          if m.severity == "error"], [])


IMPLICIT = HERE / "test" / "implicit"
IMPLICIT_ECU = rtegen.with_resolved_paths(
    yaml.safe_load((IMPLICIT / "rte.yaml").read_text(encoding="utf-8")), IMPLICIT / "rte.yaml")


class RteImplicitTest(unittest.TestCase):
    """RTE-09: implicit access (DATA-READ-ACCESSS / DATA-WRITE-ACCESSS -> Rte_IRead, Rte_IWrite, Rte_IWriteRef) with a
    copy per runnable; test profile test/implicit (ProducerSwc, ConsumerSwc in two tasks, BSW task profile
    test/multitask/tasks.yaml)."""

    setUp, tearDown = RteGenTest.setUp, RteGenTest.tearDown

    def run_with(self, ecu, out="out"):
        path = self.tmp / "rte.yaml"
        path.write_text(yaml.safe_dump(ecu, sort_keys=False), encoding="utf-8")
        return rtegen.generate(path, PROJECT, self.tmp / out, REPO, MULTI_TASKS)

    def generated(self, name, out="out"):
        return (self.tmp / out / name).read_text(encoding="utf-8")

    def mutated(self, fixture: Path, component: str, ecu: dict, *changes) -> dict:
        """ECU description with a changed copy of a component's ARXML; changes are (old, new), each replaced once."""
        target = self.tmp / component
        if not target.exists():
            shutil.copytree(fixture / component, target)
        arxml = target / "generated" / "arxml" / f"{component}.arxml"
        text = arxml.read_text(encoding="utf-8")
        for old, new in changes:
            self.assertIn(old, text)
            text = text.replace(old, new, 1)
        arxml.write_text(text, encoding="utf-8")
        ecu = copy.deepcopy(ecu)
        for entry in ecu["components"]:
            if entry["name"] == component:
                entry["path"] = target.as_posix()
        return ecu

    def test_access_points_are_parsed(self):
        comp = rtegen.parse_component("ConsumerSwc", IMPLICIT / "ConsumerSwc" / "generated" / "arxml")
        runnables = {r.name: r for r in comp.runnables}
        self.assertEqual(runnables["RunFast1ms"].implicit, {"CountImp.Count": "r"})
        self.assertEqual(runnables["RunFast1ms"].explicit, {"CountExp.Count"})
        self.assertEqual(runnables["RunFast1ms"].accesses, {"CountImp.Count", "CountExp.Count"})  # task plan
        self.assertEqual(runnables["RunImp10ms"].implicit, {"CountImp.Count": "r", "Samples.Samples": "r",
                                                            "Pair.Pair": "r", "Seen.Seen": "w"})
        self.assertEqual(runnables["RunExp10ms"].implicit, {})
        producer = rtegen.parse_component("ProducerSwc", IMPLICIT / "ProducerSwc" / "generated" / "arxml")
        self.assertEqual({r.name: r.implicit for r in producer.runnables},
                         {"ProducerSwc_Init": {"Count.Count": "w"},
                          "Run1ms": {"Count.Count": "w", "Samples.Samples": "w", "Pair.Pair": "w"}})
        # explicit components are unchanged: no implicit access
        demo = rtegen.parse_component("DemoSwc", REPO / "swc" / "DemoSwc" / "generated" / "arxml")
        self.assertFalse(any(r.implicit for r in demo.runnables))

    def test_sizes_of_the_copies(self):
        components, buffers, tasks = self.run_with(IMPLICIT_ECU)
        pair = components["ProducerSwc"].ports["Pair.Pair"].type
        samples = components["ProducerSwc"].ports["Samples.Samples"].type
        self.assertEqual((pair.byte_size, pair.alignment, samples.byte_size, samples.alignment), (8, 4, 6, 2))
        demo = rtegen.parse_component("TypeSwc", TYPES / "TypeSwc" / "generated" / "arxml")
        cmd = next(p.type for p in demo.ports.values() if p.type.name == "TypeSwc_CmdBus")
        # uint8 Mode, sint16 Speed, uint8 Limits[2], boolean Enable: 1 + pad 1 + 2 + 2 + 1 + pad 1 = 8
        self.assertEqual(cmd.byte_size, 8)

    def test_generated_api(self):
        self.run_with(IMPLICIT_ECU)
        consumer = self.generated("Rte_ConsumerSwc.h")
        self.assertIn("#define Rte_IRead_RunImp10ms_CountImp_Count Rte_IRead_ConsumerSwc_RunImp10ms_CountImp_Count\n"
                      "extern uint32 Rte_IRead_ConsumerSwc_RunImp10ms_CountImp_Count(void);", consumer)
        self.assertIn("extern const uint16* Rte_IRead_ConsumerSwc_RunImp10ms_Samples_Samples(void);", consumer)
        self.assertIn("extern const ImpPair* Rte_IRead_ConsumerSwc_RunImp10ms_Pair_Pair(void);", consumer)
        self.assertIn("extern void Rte_IWrite_ConsumerSwc_RunImp10ms_Seen_Seen(uint32 data);", consumer)
        self.assertIn("#define Rte_Read_CountExp_Count Rte_Read_ConsumerSwc_CountExp_Count", consumer)  # explicit
        self.assertNotIn("Rte_IRead_RunExp10ms", consumer)
        producer = self.generated("Rte_ProducerSwc.h")
        self.assertIn("extern void Rte_IWrite_ProducerSwc_Run1ms_Samples_Samples(const uint16* data);", producer)
        self.assertIn("#define Rte_IWriteRef_Run1ms_Samples_Samples Rte_IWriteRef_ProducerSwc_Run1ms_Samples_Samples\n"
                      "extern uint16* Rte_IWriteRef_ProducerSwc_Run1ms_Samples_Samples(void);", producer)
        self.assertIn("extern ImpPair* Rte_IWriteRef_ProducerSwc_Run1ms_Pair_Pair(void);", producer)
        self.assertIn("extern void Rte_IWrite_ProducerSwc_Run1ms_Pair_Pair(const ImpPair* data);", producer)
        rte_c = self.generated("Rte.c")
        self.assertIn("ImpPair Rte_Imp_ConsumerSwc_RunImp10ms_Pair_Pair = {0, 0};", rte_c)
        self.assertIn("    return &Rte_Imp_ConsumerSwc_RunImp10ms_Pair_Pair;", rte_c)
        self.assertIn("uint16* Rte_IWriteRef_ProducerSwc_Run1ms_Samples_Samples(void)\n{\n"
                      "    return Rte_Imp_ProducerSwc_Run1ms_Samples_Samples;", rte_c)
        self.assertIn("extern uint32 Rte_Imp_ConsumerSwc_RunImp10ms_Seen_Seen; /* ConsumerSwc.RunImp10ms: Seen.Seen "
                      "(Rte_IWrite) */", self.generated("Rte_Internal.h"))

    def test_copy_plan_and_locks(self):
        """One copy per runnable and element, exchanged with what explicit access uses; at most one lock section per
        side, none for a task copy (no double locking)."""
        self.run_with(IMPLICIT_ECU)
        config = self.generated("RteConfig.cpp")
        self.assertIn('{"ProducerSwc.Run1ms", &Rte_Implicit_ProducerSwc_Run1ms, 1U,', config)
        self.assertIn('{"ConsumerSwc.RunExp10ms", &RunExp10ms, 10U,', config)            # explicit only
        self.assertIn('{"ProducerSwc.ProducerSwc_Init", &Rte_Implicit_ProducerSwc_ProducerSwc_Init, 0U, 0U, NO_TASK',
                      config)

        def wrapper(name):
            return config.split(f"void Rte_Implicit_{name}()\n{{\n")[1].split("\n}\n")[0]

        run1ms = wrapper("ProducerSwc_Run1ms")        # task copy of fast: no lock at all
        self.assertNotIn("LockType", run1ms)
        self.assertIn("Rte_Imp_ProducerSwc_Run1ms_Pair_Pair = Rte_Cpy_fast_ProducerSwc_Pair_Pair;", run1ms)
        self.assertLess(run1ms.index("Rte_Imp_ProducerSwc_Run1ms_Count_Count = "), run1ms.index("Run1ms();"))
        self.assertLess(run1ms.index("Run1ms();"), run1ms.index("Rte_Cpy_fast_ProducerSwc_Count_Count = "))
        imp = wrapper("ConsumerSwc_RunImp10ms")        # one lock for CountImp (several tasks), the rest without
        self.assertEqual(imp.count("::async::LockType const lock;"), 1)
        locked = imp.split("::async::LockType const lock;")[1].split("}")[0]
        self.assertIn("Rte_Imp_ConsumerSwc_RunImp10ms_CountImp_Count = static_cast<uint32>("
                      "Rte_Buf_ProducerSwc_Count_Count);", locked)
        self.assertNotIn("Samples", locked)
        self.assertIn("(void)std::memcpy(&Rte_Imp_ConsumerSwc_RunImp10ms_Samples_Samples, "
                      "&Rte_Cpy_slow_ProducerSwc_Samples_Samples", imp)
        self.assertLess(imp.index("}"), imp.index("RunImp10ms();"))   # the runnable never runs under the lock
        self.assertTrue(imp.rstrip().endswith("Rte_Buf_ConsumerSwc_Seen_Seen = static_cast<uint32>("
                                              "Rte_Imp_ConsumerSwc_RunImp10ms_Seen_Seen);"))
        # the task plan of RTE-02 is unchanged by the implicit copies
        _, buffers, _ = self.run_with(IMPLICIT_ECU, "again")
        self.assertEqual(buffers["ProducerSwc.Count.Count"].copies, {"fast": "w"})
        self.assertEqual(buffers["ProducerSwc.Samples.Samples"].copies, {"fast": "w", "slow": "r"})
        self.assertFalse(buffers["ConsumerSwc.Seen.Seen"].shared)
        plan = {ir.name: ir for ir in rtegen.plan_implicit(*self._planned())}
        self.assertEqual({n: (ir.byte_size, ir.lock_before, ir.lock_after) for n, ir in plan.items()},
                         {"ProducerSwc.ProducerSwc_Init": (4, False, False), "ProducerSwc.Run1ms": (18, False, False),
                          "ConsumerSwc.RunFast1ms": (4, True, False), "ConsumerSwc.RunImp10ms": (22, True, False)})

    def _planned(self):
        components = {e["name"]: rtegen.parse_component(e["name"], IMPLICIT / e["path"] / "generated" / "arxml")
                      for e in IMPLICIT_ECU["components"]}
        manifest = rtegen.load_task_manifest(MULTI_TASKS)
        path = self.tmp / "plan.yaml"
        path.write_text(yaml.safe_dump(IMPLICIT_ECU, sort_keys=False), encoding="utf-8")
        ecu = rtegen.load_ecu(path)
        app, _ = rtegen.build_tasks(ecu, components, manifest, None)
        buffers, port_buffer = rtegen.build_rte(ecu, components, rtegen.board_signals(PROJECT))
        port_access, _ = rtegen.plan_tasks(components, buffers, port_buffer, app, None)
        return components, buffers, port_buffer, port_access

    def test_explicit_projects_are_unchanged(self):
        """Without implicit access points nothing of RTE-09 appears in the generated code."""
        rtegen.generate(ECU_FILE, PROJECT, self.tmp / "demo", REPO)
        for name in ("Rte.c", "Rte_Internal.h", "RteConfig.cpp", "Rte_DemoSwc.h"):
            text = self.generated(name, "demo")
            for marker in ("Rte_Imp_", "Rte_Implicit_", "Rte_IRead", "Rte_IWrite", "RTE-09"):
                self.assertNotIn(marker, text, f"{name}: {marker}")

    def test_errors(self):
        cases = [
            # the implicit write of Seen turned into a read: a read access point on a provided port
            (IMPLICIT, "ConsumerSwc", [("<DATA-WRITE-ACCESSS>", "<DATA-READ-ACCESSS>"),
                                       ("</DATA-WRITE-ACCESSS>", "</DATA-READ-ACCESSS>")],
             "ConsumerSwc.RunImp10ms: DATA-READ-ACCESSS of Seen.Seen: implicit read access needs a required port, "
             "Seen.Seen is a provided port"),
            # the implicit write pointed at the required port CountImp
            (IMPLICIT, "ConsumerSwc", [("/Components/ConsumerSwc/Seen</PORT-PROTOTYPE-REF>",
                                        "/Components/ConsumerSwc/CountImp</PORT-PROTOTYPE-REF>"),
                                       ("/Interfaces/Seen/Seen</TARGET-DATA-PROTOTYPE-REF>",
                                        "/Interfaces/Count/Count</TARGET-DATA-PROTOTYPE-REF>")],
             "ConsumerSwc.RunImp10ms: DATA-WRITE-ACCESSS of CountImp.Count: implicit write access needs a provided "
             "port, CountImp.Count is a required port"),
            (IMPLICIT, "ConsumerSwc", [("<DATA-RECEIVE-POINT-BY-ARGUMENTS>", "<DATA-RECEIVE-POINT-BY-VALUES>"),
                                       ("</DATA-RECEIVE-POINT-BY-ARGUMENTS>", "</DATA-RECEIVE-POINT-BY-VALUES>")],
             "ConsumerSwc.RunFast1ms: DATA-RECEIVE-POINT-BY-VALUES (Rte_DRead) is not supported"),
            (IMPLICIT, "ProducerSwc", [('DEST="P-PORT-PROTOTYPE">/Components/ProducerSwc/Count<',
                                        'DEST="P-PORT-PROTOTYPE">/Components/ProducerSwc/Nope<')],
             "ProducerSwc.ProducerSwc_Init: access point to unknown port element Nope.Count"),
        ]
        for fixture, component, changes, fragment in cases:
            with self.subTest(fragment=fragment):
                shutil.rmtree(self.tmp / component, ignore_errors=True)
                ecu = self.mutated(fixture, component, IMPLICIT_ECU, *changes)
                with self.assertRaises(rtegen.RteError) as ctx:
                    self.run_with(ecu)
                self.assertIn(fragment, str(ctx.exception))

    def test_warning_for_implicit_writes_from_two_tasks(self):
        # RunFast1ms (task fast) writes Seen implicitly as well as RunImp10ms (task slow)
        block = ('<DATA-WRITE-ACCESSS>\n'
                 '                                        <VARIABLE-ACCESS>\n'
                 '                                            <SHORT-NAME>IWrite_Seen_Seen</SHORT-NAME>\n'
                 '                                            <ACCESSED-VARIABLE>\n'
                 '                                                <AUTOSAR-VARIABLE-IREF>\n'
                 '                                                    <PORT-PROTOTYPE-REF DEST="P-PORT-PROTOTYPE">'
                 '/Components/ConsumerSwc/Seen</PORT-PROTOTYPE-REF>\n'
                 '                                                    <TARGET-DATA-PROTOTYPE-REF '
                 'DEST="VARIABLE-DATA-PROTOTYPE">/Interfaces/Seen/Seen</TARGET-DATA-PROTOTYPE-REF>\n'
                 '                                                </AUTOSAR-VARIABLE-IREF>\n'
                 '                                            </ACCESSED-VARIABLE>\n'
                 '                                        </VARIABLE-ACCESS>\n'
                 '                                    </DATA-WRITE-ACCESSS>\n'
                 '                                    <SYMBOL>RunFast1ms</SYMBOL>')
        ecu = self.mutated(IMPLICIT, "ConsumerSwc", IMPLICIT_ECU, ("<SYMBOL>RunFast1ms</SYMBOL>", block))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.run_with(ecu)
        self.assertIn("rtegen: warning: ConsumerSwc.RunFast1ms: Seen.Seen: implicit write of a port whose runnables "
                      "run in several tasks", err.getvalue())
        self.assertIn("ConsumerSwc.RunImp10ms: Seen.Seen: implicit write", err.getvalue())
        config = self.generated("RteConfig.cpp")
        fast = config.split("void Rte_Implicit_ConsumerSwc_RunFast1ms()\n{\n")[1].split("\n}\n")[0]
        # before: CountImp and the start value of Seen in one lock section; after: Seen in one lock section
        self.assertEqual(fast.count("::async::LockType const lock;"), 2)
        self.assertLess(fast.index("RunFast1ms();"), fast.rindex("::async::LockType const lock;"))
        # the checked-in profile gives no such warning
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.run_with(IMPLICIT_ECU, "clean")
        self.assertNotIn("implicit write", err.getvalue())

    def test_mode_switch_runnable_with_implicit_access(self):
        # RTE-08 profile: OnRunEntry writes EntryCount implicitly instead of explicitly
        ecu = self.mutated(MODES, "ModeSwc", MODES_ECU, ("<DATA-SEND-POINTS>", "<DATA-WRITE-ACCESSS>"),
                           ("</DATA-SEND-POINTS>", "</DATA-WRITE-ACCESSS>"))
        path = self.tmp / "rte.yaml"
        path.write_text(yaml.safe_dump(ecu, sort_keys=False), encoding="utf-8")
        rtegen.generate(path, PROJECT_TWO_BUSES, self.tmp / "out", REPO, MULTI_TASKS)
        config = self.generated("RteConfig.cpp")
        self.assertIn('{"ModeSwc.OnRunEntry", &Rte_Implicit_ModeSwc_OnRunEntry,', config)
        self.assertIn("void Rte_Implicit_ModeSwc_OnRunEntry()", config)
        self.assertIn("#define Rte_IWrite_OnRunEntry_EntryCount_EntryCount", self.generated("Rte_ModeSwc.h"))


class RteImplicitHostTest(unittest.TestCase):
    """RTE-09: compiles the generated RTE for test/implicit with the synthetic components and runs
    test/implicit/rte_implicit_host_test.cpp (implicit reads stable while a higher priority task writes, explicit
    reads as the counter-check)."""

    def test_implicit_runtime(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            out = tmp / "gen"
            rtegen.generate(IMPLICIT / "rte.yaml", PROJECT, out, REPO, MULTI_TASKS)
            zig = [sys.executable, "-m", "ziglang"]
            includes = [f"-I{out}", f"-I{out / 'tasks'}", f"-I{REPO / 'libs/bsw/autosarBase/include'}",
                        f"-I{HERE / 'test' / 'can_host' / 'stubs'}", f"-I{IMPLICIT / 'ProducerSwc/generated/src'}",
                        f"-I{IMPLICIT / 'ConsumerSwc/generated/src'}"]
            objects = []
            for src in (out / "Rte.c", IMPLICIT / "ProducerSwc/generated/src/ProducerSwc.c",
                        IMPLICIT / "ConsumerSwc/generated/src/ConsumerSwc.c"):
                obj = tmp / (src.stem + ".o")
                r = subprocess.run(zig + ["cc", "-std=c99", "-O1", "-Wall", "-Wextra", "-Wconversion", "-Werror",
                                          *includes, "-c", str(src), "-o", str(obj)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr[-3000:])
                objects.append(str(obj))
            exe = tmp / "rte_implicit_host_test.exe"
            r = subprocess.run(zig + ["c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion", "-Werror",
                                      *includes, str(out / "RteConfig.cpp"), str(IMPLICIT / "rte_implicit_host_test.cpp"),
                                      *objects, "-o", str(exe)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            r = subprocess.run([str(exe)], capture_output=True, text=True)
            print("\n" + r.stdout.strip(), file=sys.stderr)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:])
            self.assertIn(", 0 failures", r.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


UNCONNECTED = HERE / "test" / "unconnected"
UNCONNECTED_ECU = rtegen.with_resolved_paths(
    yaml.safe_load((UNCONNECTED / "rte.yaml").read_text(encoding="utf-8")), UNCONNECTED / "rte.yaml")
CALIB = HERE / "test" / "calib"


class RteUnconnectedPortTest(unittest.TestCase):
    """RTE-27 (Studio test 2026-09-28): a required port without connection no longer stops the configuration. It
    reads a defined init value from a constant buffer of its own, a qualifier port QLF_NOT_AVAILABLE (64, RTE-28;
    RTE-27 had QLF_ERROR_TIMEOUT); the generator
    warns with file and YAML path. Ports of a safety-relevant component stay errors, mode ports as well
    (RteModesTest: "a mode port: connect it to ecu.mode")."""

    setUp, tearDown = RteGenTest.setUp, RteGenTest.tearDown

    def generate(self, ecu: dict, project=PROJECT, tasks=MULTI_TASKS):
        path = self.tmp / "rte.yaml"
        path.write_text(yaml.safe_dump(ecu), encoding="utf-8")
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = rtegen.generate(path, project, self.tmp / "out", REPO, tasks)
        return result, stderr.getvalue().splitlines()

    def test_profile_warns_once_per_port(self):
        (_components, buffers, _tasks), printed = self.generate(UNCONNECTED_ECU)
        unconnected = sorted(k for k, b in buffers.items() if b.init_only == rtegen.INIT_UNCONNECTED)
        self.assertEqual(unconnected, ["ConsumerSwc.CountExp.Count", "ConsumerSwc.CountImp.Count",
                                       "ConsumerSwc.Pair.Pair", "ConsumerSwc.Samples.Samples",
                                       "InitSinkSwc.LevelQlf.LevelQlf", "InitSinkSwc.Level_mV.Level_mV"])
        warned = [line for line in printed if "is not connected" in line]
        self.assertEqual(len(warned), 6, printed)
        self.assertIn("rtegen: warning: required port ConsumerSwc.Samples.Samples is not connected: it reads its init "
                      "value {0, 0, 0} from a constant buffer; connect it to its source, or to rte.init to keep the "
                      "init value deliberately", warned)
        self.assertIn("rtegen: warning: required port InitSinkSwc.LevelQlf.LevelQlf is not connected: it reads "
                      "QLF_NOT_AVAILABLE (64, no source) from a constant buffer, so the component treats the value it "
                      "qualifies as unavailable; connect it to its source (e.g. can.qlf.<Message>)", warned)
        self.assertEqual(buffers["InitSinkSwc.LevelQlf.LevelQlf"].init, "64U")
        self.assertEqual((rtegen.QLF_NO_SOURCE, rtegen.QLF[rtegen.QLF_NO_SOURCE]), ("NOT_AVAILABLE", 64))

    def test_qualifier_ports(self):
        """Only an unscaled QLF_TYPES scalar named ...Qlf/...Qualifier is a qualifier port."""
        sink = rtegen.parse_c_component("InitSinkSwc", UNCONNECTED / "InitSinkSwc")[0].ports
        self.assertTrue(rtegen.is_qualifier_port(sink["LevelQlf.LevelQlf"]))
        self.assertFalse(rtegen.is_qualifier_port(sink["Level_mV.Level_mV"]))     # no qualifier name
        demo = rtegen.parse_component("DemoSwc", REPO / "swc" / "DemoSwc" / "generated" / "arxml").ports
        self.assertTrue(rtegen.is_qualifier_port(demo["PcCommandQlf.PcCommandQlf"]))
        self.assertFalse(rtegen.is_qualifier_port(demo["Button1.Button1"]))        # boolean
        consumer = rtegen.parse_component("ConsumerSwc", IMPLICIT / "ConsumerSwc" / "generated" / "arxml").ports
        self.assertFalse(rtegen.is_qualifier_port(consumer["Pair.Pair"]))          # a record

    def test_demo_qualifier_reads_not_available(self):
        ecu = copy.deepcopy(ECU)
        del ecu["connections"]["DemoSwc.PcCommandQlf.PcCommandQlf"]
        (_components, buffers, _tasks), printed = self.generate(ecu, tasks=None)
        self.assertEqual(buffers["DemoSwc.PcCommandQlf.PcCommandQlf"].init, "64U")
        self.assertIn("uint8 const Rte_Buf_DemoSwc_PcCommandQlf_PcCommandQlf = 64U;",
                      (self.tmp / "out" / "Rte.c").read_text(encoding="utf-8"))
        # rte.init stays what it was: the init value of the port (0 = QLF_DEBOUNCE), no warning, no const
        ecu["connections"]["DemoSwc.PcCommandQlf.PcCommandQlf"] = "rte.init"
        (_components, buffers, _tasks), printed = self.generate(ecu, tasks=None)
        b = buffers["DemoSwc.PcCommandQlf.PcCommandQlf"]
        self.assertEqual((b.init, b.init_only, b.constant), ("0", None, False))
        self.assertFalse([line for line in printed if "is not connected:" in line], printed)

    def test_safety_component_stays_an_error(self):
        """CalMapSwc has asil_b parameters: an unconnected input is refused, rte.init (documented) is allowed."""
        self.assertEqual(rtegen.component_safety(CALIB / "CalMapSwc"), "asil_b")
        self.assertEqual(rtegen.component_safety(UNCONNECTED / "InitSinkSwc"), "qm")
        self.assertEqual(rtegen.component_safety(IMPLICIT / "ConsumerSwc"), "qm")          # no swc.yaml
        ecu = rtegen.with_resolved_paths(yaml.safe_load((CALIB / "rte.yaml").read_text(encoding="utf-8")),
                                         CALIB / "rte.yaml")
        self.generate(ecu, project=PROJECT_TWO_BUSES, tasks=None)                          # rte.init: fine
        del ecu["connections"]["CalMapSwc.Speed_rpm.Speed_rpm"]
        with self.assertRaises(rtegen.RteError) as ctx:
            self.generate(ecu, project=PROJECT_TWO_BUSES, tasks=None)
        self.assertIn("required port CalMapSwc.Speed_rpm.Speed_rpm is not connected: CalMapSwc is safety relevant "
                      "(swc.yaml safety asil_b)", str(ctx.exception))


class RteUnconnectedHostTest(unittest.TestCase):
    """RTE-27: compiles the generated RTE of test/unconnected (ConsumerSwc with the ARXML init values 7 and 5 on a
    copy, InitSinkSwc) and runs test/unconnected/rte_unconnected_host_test.cpp: the runnables run with the init
    values, explicit, implicit and locked reads alike, the qualifier reads QLF_NOT_AVAILABLE (RTE-28), the buffers are
    const."""

    def test_unconnected_runtime(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            consumer = tmp / "ConsumerSwc"
            shutil.copytree(IMPLICIT / "ConsumerSwc", consumer)
            arxml = consumer / "generated" / "arxml" / "ConsumerSwc.arxml"
            text = arxml.read_text(encoding="utf-8")
            # CountImp and CountExp (the first two init values) 7, the three elements of Samples 5
            for new in ("7", "7", "5", "5", "5"):
                self.assertIn("<VALUE>0</VALUE>", text)
                text = text.replace("<VALUE>0</VALUE>", f"<VALUE>{new}</VALUE>", 1)
            arxml.write_text(text, encoding="utf-8")
            ecu = copy.deepcopy(UNCONNECTED_ECU)
            next(c for c in ecu["components"] if c["name"] == "ConsumerSwc")["path"] = consumer.as_posix()
            ecu_file = tmp / "rte.yaml"
            ecu_file.write_text(yaml.safe_dump(ecu), encoding="utf-8")
            out = tmp / "gen"
            with contextlib.redirect_stderr(io.StringIO()):
                rtegen.generate(ecu_file, PROJECT, out, REPO, MULTI_TASKS)
            zig = [sys.executable, "-m", "ziglang"]
            includes = [f"-I{out}", f"-I{out / 'tasks'}", f"-I{REPO / 'libs/bsw/autosarBase/include'}",
                        f"-I{HERE / 'test' / 'can_host' / 'stubs'}", f"-I{consumer / 'generated/src'}"]
            objects = []
            for src, extra in ((out / "Rte.c", []), (consumer / "generated/src/ConsumerSwc.c", []),
                               (UNCONNECTED / "InitSinkSwc/src/InitSinkSwc.c", ["-Wsign-conversion", "-pedantic"])):
                obj = tmp / (src.stem + ".o")
                r = subprocess.run(zig + ["cc", "-std=c99", "-O1", "-Wall", "-Wextra", "-Wconversion", *extra,
                                          "-Werror", *includes, "-c", str(src), "-o", str(obj)],
                                   capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr[-3000:])
                objects.append(str(obj))
            exe = tmp / "rte_unconnected_host_test.exe"
            r = subprocess.run(zig + ["c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion", "-Werror",
                                      *includes, str(out / "RteConfig.cpp"),
                                      str(UNCONNECTED / "rte_unconnected_host_test.cpp"), *objects, "-o", str(exe)],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            r = subprocess.run([str(exe)], capture_output=True, text=True)
            print("\n" + r.stdout.strip(), file=sys.stderr)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:])
            self.assertIn(", 0 failures", r.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


SYSTEMS = REPO / "ecu" / "application" / "src" / "systems"


class RteRunnableCountTest(unittest.TestCase):
    """RTE-19 (finding BEF-001 of PLAT-74): RUNNABLE_COUNT is a compile-time constant of RteConfig.h, the generated
    RteConfig.cpp checks the table against it and RteSystem.cpp checks it against its arrays (MAX_RUNNABLES,
    TIMING_RUNNABLES). A table with a row too many or too few - generator error or RteConfig.cpp edited by hand -
    and a count above the arrays are translation errors. Own test profile test/multitask (two tasks, two init
    runnables), not the eval board."""

    INIT_ROW = re.compile(r'^    \{"[^"]+", &\w+, 0U, 0U, NO_TASK, 0U, 0U\},\r?\n', re.M)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.out = self.tmp / "gen"
        rtegen.generate(MULTI / "rte.yaml", PROJECT, self.out, REPO, MULTI_TASKS)
        self.zig = [sys.executable, "-m", "ziglang"]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def compile_config(self, source: str):
        path = self.tmp / "RteConfig.cpp"
        path.write_text(source, encoding="utf-8")
        includes = [f"-I{self.out}", f"-I{self.out / 'tasks'}", f"-I{REPO / 'libs/bsw/autosarBase/include'}",
                    f"-I{HERE / 'test' / 'can_host' / 'stubs'}", f"-I{MULTI / 'MotorSwc/generated/src'}",
                    f"-I{MULTI / 'AppSwc/generated/src'}"]
        return subprocess.run(self.zig + ["c++", "-std=c++17", "-c", "-Wall", "-Wextra", "-Werror",
                                          *includes, str(path), "-o", str(self.tmp / "RteConfig.o")], capture_output=True, text=True)

    def test_count_is_a_compile_time_constant(self):
        header = (self.out / "RteConfig.h").read_text(encoding="utf-8")
        config = (self.out / "RteConfig.cpp").read_text(encoding="utf-8")
        self.assertIn("constexpr size_t RUNNABLE_COUNT = RUNNABLE_TABLE_SIZE;", header)
        self.assertNotIn("extern size_t const RUNNABLE_COUNT", header)
        self.assertNotIn("size_t const RUNNABLE_COUNT", config)
        self.assertIn('static_assert(sizeof(RUNNABLES) / sizeof(RUNNABLES[0]) == RUNNABLE_COUNT, "runnable table");',
                      config)

    def test_table_must_match_the_count(self):
        config = (self.out / "RteConfig.cpp").read_text(encoding="utf-8")
        rows = self.INIT_ROW.findall(config)
        self.assertEqual(len(rows), 2, "test profile test/multitask has two init runnables")
        r = self.compile_config(config)
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        # one row too few and one row too many: both refused at compile time with the message of the assertion
        for broken in (config.replace(rows[0], "", 1), config.replace(rows[0], rows[0] + rows[0], 1)):
            self.assertNotEqual(broken, config)
            r = self.compile_config(broken)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("runnable table", r.stderr)

    def compile_rte_system(self, *defines):
        # same includes and flags as the host test rte-deadline (tools/test/tests.yaml), against its stub profile
        includes = [f"-I{REPO / 'ecu/application/include'}", f"-I{SYSTEMS}", f"-I{SYSTEMS / 'test/host/stubs'}"]
        return subprocess.run(self.zig + ["c++", "-std=c++17", "-c", "-Wall", "-Wextra", "-Wconversion",
                                          "-Wno-sign-conversion", "-Werror", "-DNX_RTE_SAFE_SUPERVISOR=1", *defines,
                                          *includes, str(SYSTEMS / "RteSystem.cpp"), "-o", str(self.tmp / "RteSystem.o")],
                              capture_output=True, text=True)

    def test_rte_system_refuses_a_count_above_its_arrays(self):
        r = self.compile_rte_system()
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        r = self.compile_rte_system("-DNX_TEST_RUNNABLE_COUNT_EXCESS=1U")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("rte::RUNNABLE_COUNT exceeds RteSystem::MAX_RUNNABLES", r.stderr)
        self.assertIn("rte::RUNNABLE_COUNT exceeds TIMING_RUNNABLES", r.stderr)


BUILDTEST_FILE = REPO / "projects" / "buildtest_s32k148_min" / "project.yaml"
NVM = HERE / "test" / "nvm"


def _calls(function) -> set[str]:
    """Functions of rtegen (or of a module it imports, as 'module.name') that the source of `function` calls."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Name) and (f.id == "print" or callable(getattr(rtegen, f.id, None))):
            found.add(f.id)
        elif (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
              and inspect.ismodule(getattr(rtegen, f.value.id, None))):
            found.add(f"{f.value.id}.{f.attr}")
    return found


def _reach(names) -> set[str]:
    """Every function of the module rtegen itself reached from `names`, following calls by name."""
    seen, todo = set(), list(names)
    while todo:
        name = todo.pop()
        obj = getattr(rtegen, name, None)
        if name in seen or not inspect.isfunction(obj) or obj.__module__ != rtegen.__name__:
            continue
        seen.add(name)
        todo += [n for n in _calls(obj) if "." not in n]
    return seen


def _refuses(name: str) -> bool:
    """The function raises RteError (or a subclass) itself."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(getattr(rtegen, name))))
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and isinstance(node.exc.func, ast.Name):
            cls = getattr(rtegen, node.exc.func.id, None)
            if isinstance(cls, type) and issubclass(cls, rtegen.RteError):
                return True
    return False


class RteValidateDriftTest(unittest.TestCase):
    """TOOL-113: rtegen.validate() (Studio "Konfiguration prüfen", `studio validate --like-generate`) reports what
    rtegen.generate() refuses or warns - per faulty profile the same text, with file and YAML path - and generate()
    cannot gain a check validate() lacks.

    Own profiles on the build test project buildtest_s32k148_min (two CAN buses, XCP on CAN1, no SBC) with the
    test descriptions of test/nvm, test/types, test/modes and test/can_host, not the eval board.
    """

    setUp, tearDown = RteGenTest.setUp, RteGenTest.tearDown

    def profile(self, name: str, ecu: dict, **sections) -> Path:
        """project.yaml of the build test project with `ecu` as its rte.yaml; `sections` replace top-level keys."""
        folder = self.tmp / name
        folder.mkdir(parents=True)
        ecu_file = folder / "rte.yaml"
        ecu_file.write_text(yaml.safe_dump(ecu, sort_keys=False), encoding="utf-8")
        doc = yaml.safe_load(BUILDTEST_FILE.read_text(encoding="utf-8"))
        doc["project"]["board"] = (BUILDTEST_FILE.parent / doc["project"]["board"]).resolve().as_posix()
        doc["project"]["rte"] = ecu_file.as_posix()
        doc.update(sections)
        project = folder / "project.yaml"
        project.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        return project

    def both(self, project: Path):
        """(errors, warnings) of generate() as CMake runs it, and the messages of validate() beyond nxconfig's."""
        _config, checked = rtegen.nxconfig.check(project)
        validated = rtegen.validate(project, REPO, tasks_manifest=MULTI_TASKS)
        self.assertEqual(validated[:len(checked)], list(checked))    # nxconfig first, unchanged
        errors, stderr = [], io.StringIO()
        with contextlib.redirect_stderr(stderr):
            try:
                config = rtegen.nxconfig.load(project)
                rtegen.generate(config.rte_file, config, project.parent / "out", REPO, MULTI_TASKS)
            except rtegen.nxconfig.ConfigErrors as e:
                errors = [m.text for m in e.messages if m.severity == "error"]
            except rtegen.RteError as e:
                errors = [str(e)]
        return errors, stderr.getvalue().splitlines(), validated

    def assert_same(self, project: Path, error=None, warning=None):
        """generate() and validate() report the same; `error`/`warning`: (file name, YAML path, text fragment)."""
        errors, printed, validated = self.both(project)
        self.assertEqual(errors, [m.text for m in validated if m.severity == "error"])
        warned = [m for m in validated[len(rtegen.nxconfig.check(project)[1]):] if m.severity == "warning"]
        self.assertEqual(len(printed), len(warned), (printed, [str(m) for m in warned]))
        for line, m in zip(printed, warned):
            self.assertIn(line, (f"rtegen: warning: {m.text}", f"rtegen: {m}"))
        for expected, found in ((error, [m for m in validated if m.severity == "error"]), (warning, warned)):
            if expected is None:
                continue
            match = [m for m in found if expected[2] in m.text]
            self.assertTrue(match, [str(m) for m in validated])
            self.assertEqual((match[0].file.name, match[0].yaml_path), expected[:2])
            self.assertIsNotNone(match[0].line)
        if error is None:
            self.assertEqual(errors, [])

    def test_validate_matches_generate(self):
        # 1. the structure: generate() is build_model() plus rendering. A new check in generate() - a call of any
        #    other function of rtegen or of a module it imports - fails here: it belongs into build_model(), where
        #    validate() runs it too, or (rendering only, refuses nothing) into rtegen.RENDER_STEPS.
        with self.subTest("structure: generate() = load_ecu + build_model + RENDER_STEPS, validate() = build_model"):
            called = _calls(rtegen.generate)
            self.assertEqual(called - {"load_ecu", "build_model"} - getattr(rtegen, "RENDER_STEPS", set()), set(),
                             "generate() calls a step validate() does not run: move it into build_model()")
            self.assertNotIn("print", called, "a warning of generate() goes through build_model(warn=...)")
            self.assertIn("build_model", _calls(rtegen.validate))
            # a render step (or what it calls) that refuses a configuration: build_model() must reach it first
            model = _reach(["build_model"])
            late = sorted(name for name in _reach(n for n in rtegen.RENDER_STEPS if "." not in n)
                          if _refuses(name) and name not in model)
            self.assertEqual(late, [], "a render step raises RteError that build_model() never checks")
            self.assertIn("ecu_state_wakeup", model)     # was only reached through render_ecu_state_config

        # 2. per faulty profile: the same text, file and YAML path
        nvm = rtegen.with_resolved_paths(yaml.safe_load((NVM / "rte.yaml").read_text(encoding="utf-8")),
                                         NVM / "rte.yaml")
        with self.subTest("nvm_warnings: a block nobody reads (was missing in validate)"):
            ecu = copy.deepcopy(nvm)
            ecu["connections"]["NvmSwc.StoredCount.StoredCount"] = "rte.init"
            self.assert_same(self.profile("nvm_unread", ecu), warning=(
                "rte.yaml", "connections",
                "nvm.BootCount is written by NvmSwc.Count.Count but no required port reads it"))
        with self.subTest("components: the model differs from the generated code (text differed)"):
            copied = self.tmp / "TypeSwc"
            shutil.copytree(TYPES / "TypeSwc", copied)
            (copied / "generated" / "model.sha256").write_text("0" * 64 + "  TypeSwc.slx\n", encoding="ascii")
            ecu = copy.deepcopy(TYPES_ECU)
            next(c for c in ecu["components"] if c["name"] == "TypeSwc")["path"] = copied.as_posix()
            self.assert_same(self.profile("slx_changed", ecu), warning=(
                "rte.yaml", "components", "components: TypeSwc.slx differs from the model"))
        with self.subTest("load_xcp: xcp.bus no CAN bus of the project (nxconfig refuses it for both)"):
            self.assert_same(self.profile("xcp_bus", TYPES_ECU, xcp={"bus": "CAN7", "master_id": 0x7E0,
                                                                     "slave_id": 0x7E8}),
                             error=("project.yaml", "xcp.bus", "bus CAN7 is not a CAN bus of the project"))
        with self.subTest("ecu_state_wakeup: a wake-up bus that does not exist (was checked while rendering)"):
            self.assert_same(self.profile("wakeup", TYPES_ECU, modules={"ecuState": {"wakeup_can": "CAN7"}}),
                             error=("project.yaml", "modules.ecuState.wakeup_can", "CAN7"))
        with self.subTest("model phase: a connection of two types"):
            ecu = copy.deepcopy(TYPES_ECU)
            ecu["connections"]["TypeSwc.Temp.Temp"] = "TypeSink.Peak.Peak"
            self.assert_same(self.profile("types", ecu), error=(
                "rte.yaml", "connections.TypeSwc.Temp.Temp", "does not match uint16 of the provided port"))
        with self.subTest("mode_runnable_table: mode switch runnables (refuses nothing, same warnings)"):
            self.assert_same(self.profile("modes", MODES_ECU))
        with self.subTest("init_value_warnings: required ports without connection (RTE-27)"):
            self.assert_same(self.profile("unconnected", UNCONNECTED_ECU), warning=(
                "rte.yaml", "connections.InitSinkSwc.LevelQlf.LevelQlf", "reads QLF_NOT_AVAILABLE"))
        with self.subTest("can_rx_ids/can_rx_filters: CAN signals, diagnostics and XCP on two buses"):
            can = CAN_HOST / "qualifier_rte.yaml"
            self.assert_same(self.profile("can", rtegen.with_resolved_paths(
                yaml.safe_load(can.read_text(encoding="utf-8")), can)))

    def test_receive_buffer_split_refusal_is_a_message(self):
        """can_rx_splits: bswgen's ConfigError (odd or too few message buffers of an instance nxconfig does not
        judge, e.g. another family) was no RteError - validate() let it through and the Studio check failed."""
        from types import SimpleNamespace
        config = SimpleNamespace(
            integration=SimpleNamespace(can=[SimpleNamespace(name="CAN0", instance="FlexCAN0")]),
            derivative=SimpleNamespace(instances={"FlexCAN0": SimpleNamespace(message_buffers=6)}))
        with self.assertRaises(rtegen.RteError) as ctx:
            rtegen.can_rx_splits(config, ["CAN0"])
        self.assertIn("CAN CAN0: FlexCAN0 has 6 message buffers", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
