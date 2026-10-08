"""COM-28: the RTE functions of the diagnostic jobs (did.* and routine.* targets, rte::diag, RteDiag.h).

Own test profile test/diag (synthetic components of test/multitask, project buildtest_s32k148_min without SBC,
BSW task profile test/multitask/tasks.yaml), plus the demo project for the link of its diag.yaml.

Run: .venv/Scripts/python -m unittest tools/rtegen/test_rtegen_diag.py
"""

import contextlib
import copy
import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))

import rtegen  # noqa: E402
import rteapi  # noqa: E402  (tools/diaggen, put on the path by rtegen)

DIAG_DIR = HERE / "test" / "diag"
MULTI = HERE / "test" / "multitask"
MULTI_TASKS = MULTI / "tasks.yaml"
PROJECT_TWO_BUSES = rtegen.nxconfig.load(REPO / "projects" / "buildtest_s32k148_min" / "project.yaml")
# PLAT-62: the own full-featured test profile (synthetic testboard_full/testproject_full) instead of the eval board
# project, which stays private (PLAT-46 F10) - a test against it would be red in the exported open tree. What
# proves something about the eval board itself uses EVAL_DEMO_FILE and is skipped where it is missing.
DEMO_FILE = REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full" / "project.yaml"
EVAL_DEMO_FILE = REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml"
ECU = rtegen.with_resolved_paths(
    yaml.safe_load((DIAG_DIR / "rte.yaml").read_text(encoding="utf-8")), DIAG_DIR / "rte.yaml")
DIAG = yaml.safe_load((DIAG_DIR / "diag.yaml").read_text(encoding="utf-8"))


def description(data: dict, tmp: Path):
    path = tmp / "diag.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    desc, messages, _ = rtegen.nxconfig.config.read_diag(path)
    assert desc is not None, messages
    return desc


class RteDiagTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_with(self, ecu=None, diag=None, out="out"):
        path = self.tmp / "rte.yaml"
        path.write_text(yaml.safe_dump(ecu or ECU, sort_keys=False), encoding="utf-8")
        desc = description(diag or DIAG, self.tmp)
        with contextlib.redirect_stderr(io.StringIO()) as err:
            result = rtegen.generate(path, PROJECT_TWO_BUSES, self.tmp / out, REPO, MULTI_TASKS, diag=desc)
        self.stderr = err.getvalue()
        return result

    def expect_error(self, fragment, ecu=None, diag=None):
        with self.assertRaises(rtegen.RteError) as ctx:
            self.run_with(ecu, diag)
        self.assertIn(fragment, str(ctx.exception))
        return str(ctx.exception)

    def ecu(self, **connections):
        ecu = copy.deepcopy(ECU)
        ecu["connections"].update(connections)
        return ecu

    def diag(self, change):
        data = copy.deepcopy(DIAG)
        change(data)
        return data

    def generated(self, name="RteConfig.cpp", out="out"):
        return (self.tmp / out / name).read_text(encoding="utf-8")

    def test_functions_of_the_profile(self):
        _, buffers, _ = self.run_with()
        header = self.generated("RteDiag.h")
        for signature in ("bool readDid_MotorSpeed(uint16_t& speed);",
                          "bool readDid_MotorSetpoint(uint16_t& setpoint);",
                          "void writeDid_MotorSetpoint(uint16_t setpoint);",
                          "void routine_SpinUp_start(uint8_t enable);",
                          "void routine_SpinUp_results(int32_t& speed);"):
            self.assertIn(signature, header)
        config = self.generated()
        self.assertIn('#include "RteDiag.h"', config)
        # clamped when the port is wider than the element
        self.assertIn("speed = ((Rte_Buf_MotorSwc_Speed_Speed > 65535U) ? static_cast<uint16_t>(65535) : "
                      "static_cast<uint16_t>(Rte_Buf_MotorSwc_Speed_Speed));", config)
        # the counter and the boolean request port
        self.assertIn("Rte_Buf_AppSwc_Speed_Speed = static_cast<uint32>(Rte_Buf_AppSwc_Speed_Speed + 1U);", config)
        self.assertIn("Rte_Buf_MotorSwc_Enable_Enable = static_cast<boolean>((enable != 0) ? 1U : 0U);", config)
        # the diag link is visible in the buffer (console "rte signals")
        self.assertEqual(buffers["MotorSwc.Setpoint.Setpoint"].links, ["did.MotorSetpoint"])
        self.assertIn("did.MotorSetpoint", buffers["AppSwc.Setpoint.Setpoint"].links)
        self.assertEqual(self.stderr.count("diag.yaml"), 0, self.stderr)

    def test_header_is_the_one_diaggen_writes(self):
        """rtegen and tools/diaggen render RteDiag.h with the same module: identical text for one description."""
        self.run_with()
        desc = description(DIAG, self.tmp)
        self.assertEqual(self.generated("RteDiag.h"), rteapi.render_header(desc, "rte.yaml", "tools/rtegen"))
        self.assertEqual([f.name for f in rteapi.functions(desc)],
                         ["readDid_MotorSpeed", "readDid_MotorSetpoint", "writeDid_MotorSetpoint",
                          "routine_SpinUp_start", "routine_SpinUp_results"])

    def test_without_description_no_functions(self):
        path = self.tmp / "rte.yaml"
        ecu = copy.deepcopy(ECU)
        for key in ("MotorSwc.Setpoint.Setpoint", "MotorSwc.Enable.Enable", "AppSwc.Speed.Speed"):
            ecu["connections"][key] = "rte.init"
        ecu["connections"]["MotorSwc.Speed.Speed"] = "rte"
        ecu["connections"]["AppSwc.Setpoint.Setpoint"] = "rte"
        path.write_text(yaml.safe_dump(ecu), encoding="utf-8")
        rtegen.generate(path, PROJECT_TWO_BUSES, self.tmp / "out", REPO, MULTI_TASKS, diag=None)
        self.assertNotIn("namespace diag", self.generated())
        self.assertNotIn("RteDiag.h", self.generated())
        self.assertNotIn("void ", self.generated("RteDiag.h"))

    def test_target_without_description(self):
        path = self.tmp / "rte.yaml"
        path.write_text(yaml.safe_dump(ECU), encoding="utf-8")
        with self.assertRaises(rtegen.RteError) as ctx:
            rtegen.generate(path, PROJECT_TWO_BUSES, self.tmp / "out", REPO, MULTI_TASKS, diag=None)
        self.assertIn("the project has no diagnostic description", str(ctx.exception))

    def test_connection_errors(self):
        cases = [
            (dict(**{"MotorSwc.Setpoint.Setpoint": "did.Unknown"}), "expected did.<Did>[.<element>]"),
            (dict(**{"MotorSwc.Setpoint.Setpoint": "did.MotorSetpoint.nope"}), "has no element nope"),
            (dict(**{"MotorSwc.Setpoint.Setpoint": "did.MotorSpeed"}), "is not written through the RTE"),
            (dict(**{"MotorSwc.Speed.Speed": ["rte", "did.MotorSetpoint"]}),
             "a provided port delivers an identifier only as its source.port"),
            (dict(**{"AppSwc.Speed.Speed": "routine.SpinUp.stop"}), "expected routine.<Routine>.<step>"),
            (dict(**{"MotorSwc.Enable.Enable": "routine.SpinUp.start"}), "the request counter of a step"),
            (dict(**{"MotorSwc.Enable.Enable": "routine.SpinUp.start.nope"}), "has no parameter nope (enable)"),
            (dict(**{"MotorSwc.Enable.Enable": "routine.SpinUp.results.speed"}),
             "speed is a response parameter, a provided port delivers it"),
            (dict(**{"AppSwc.Setpoint.Setpoint": ["rte", "routine.SpinUp.results.speed"]}),
             "is already delivered by MotorSwc.Speed.Speed"),
            (dict(**{"AppSwc.Setpoint.Setpoint": ["rte", "routine.SpinUp.start.enable"]}),
             "enable is a request parameter, a required port receives it"),
        ]
        for connections, fragment in cases:
            with self.subTest(fragment):
                self.expect_error(fragment, ecu=self.ecu(**connections))

    def test_description_errors(self):
        def source(ref):
            return lambda d: d["data_identifiers"][0]["data"][0]["source"].update(port=ref)

        self.expect_error("MotorSwc has no port element Nope.Nope", diag=self.diag(source("MotorSwc.Nope.Nope")))
        self.expect_error("unknown component Other", diag=self.diag(source("Other.Speed.Speed")))
        self.expect_error("is a required port; the source of a data identifier is a provided port",
                          diag=self.diag(source("MotorSwc.Setpoint.Setpoint")))
        self.expect_error("float64 has no type in the diagnostic description",
                          diag=self.diag(source("MotorSwc.Wide.Wide")))

        def runnable(d):
            d["routines"][0]["start"]["call"]["runnable"] = "MotorSwc.RunNever"
        text = self.expect_error("MotorSwc.RunNever is not a runnable of the ECU description", diag=self.diag(runnable))
        self.assertIn("Run1ms", text)

    def test_value_range_of_a_written_port(self):
        """A value the diagnosis lets through must fit the port; min/max or values of diag.yaml narrow it."""
        def wide(d):
            d["routines"][0]["start"]["request"][0] = {"name": "enable", "type": "uint8"}
        self.expect_error("enable takes 0..255, port type boolean holds 0..1", diag=self.diag(wide))

        def narrowed(d):
            d["routines"][0]["start"]["request"][0] = {"name": "enable", "type": "uint8", "min": 0, "max": 1}
        self.run_with(diag=self.diag(narrowed))

        def signed(d):
            d["data_identifiers"][1]["data"][0]["type"] = "int16"
        self.expect_error("setpoint takes -32768..32767, port type uint16 holds 0..65535", diag=self.diag(signed))

        def floating(d):
            d["data_identifiers"][1]["data"][0]["type"] = "float32"
        self.expect_error("the float32 element setpoint does not fit port type uint16", diag=self.diag(floating))

    def test_unconnected_parameters_are_warnings(self):
        ecu = self.ecu(**{"MotorSwc.Enable.Enable": "rte.init", "MotorSwc.Speed.Speed": "rte"})
        self.run_with(ecu=ecu)
        self.assertIn("nothing receives routine.SpinUp.start.enable", self.stderr)
        self.assertIn("routine.SpinUp.results.speed has no providing port", self.stderr)
        config = self.generated()
        self.assertIn("(void)enable; // routine.SpinUp.start.enable: nothing receives it", config)
        self.assertIn("speed = static_cast<int32_t>(0); // routine.SpinUp.results.speed: not connected", config)

    def test_demo_project_serves_its_description(self):
        """The demo's diag.yaml: 0x0110 from DemoSwc.MotorNtcFiltered_mV (uint32 -> uint16), LedSelfTest with
        its steps; the routine ports are not connected in the demo, which is reported as warnings."""
        with contextlib.redirect_stderr(io.StringIO()) as err:
            rtegen.generate_project(DEMO_FILE, self.tmp / "demo", REPO)
        header = self.generated("RteDiag.h", "demo")
        self.assertIn("bool readDid_MotorNtcFiltered(uint16_t& motorNtcFiltered);", header)
        self.assertIn("void routine_LedSelfTest_start(uint8_t durationCycles, uint8_t& accepted);", header)
        self.assertIn("Rte_Buf_DemoSwc_MotorNtcFiltered_mV_MotorNtcFiltered_mV > 65535U",
                      self.generated("RteConfig.cpp", "demo"))
        self.assertIn("nothing receives routine.LedSelfTest.start.durationCycles", err.getvalue())

    def test_another_ecu_description_gets_no_diag(self):
        """diag_of: the project's description belongs to the project's own ECU description only."""
        config = rtegen.nxconfig.load(DEMO_FILE)
        self.assertIsNotNone(rtegen.diag_of(config, config.rte_file))
        self.assertIsNone(rtegen.diag_of(config, self.tmp / "rte.yaml"))
        self.assertIsNone(rtegen.diag_of(PROJECT_TWO_BUSES, PROJECT_TWO_BUSES.rte_file))

    def test_validate_reports_diag_findings(self):
        """The Studio check (validate) runs the same resolution: the demo reports the three routine warnings."""
        messages = rtegen.validate(DEMO_FILE, REPO)
        texts = [m.text for m in messages if "LedSelfTest" in m.text]
        self.assertEqual(len(texts), 3, texts)
        self.assertTrue(all(m.severity == "warning" for m in messages if "LedSelfTest" in m.text))


class RteDiagHostTest(unittest.TestCase):
    """Compiles the generated RTE of test/diag with the synthetic components and runs rte_diag_host_test.cpp."""

    def test_diag_runtime(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            out = tmp / "gen"
            desc = description(DIAG, tmp)
            with contextlib.redirect_stderr(io.StringIO()):
                rtegen.generate(DIAG_DIR / "rte.yaml", PROJECT_TWO_BUSES, out, REPO, MULTI_TASKS, diag=desc)
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
            exe = tmp / "rte_diag_host_test.exe"
            r = subprocess.run(zig + ["c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion", "-Werror",
                                      *includes, str(out / "RteConfig.cpp"), str(DIAG_DIR / "rte_diag_host_test.cpp"),
                                      *objects, "-o", str(exe)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            r = subprocess.run([str(exe)], capture_output=True, text=True)
            print("\n" + r.stdout.strip(), file=sys.stderr)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:])
            self.assertIn(", 0 failures", r.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


DIAG_BSW_DIR = HERE / "test" / "diag_bsw"
DIAG_BSW = yaml.safe_load((DIAG_BSW_DIR / "diag.yaml").read_text(encoding="utf-8"))
PROJECT_TWO_BUSES_FILE = REPO / "projects" / "buildtest_s32k148_min" / "project.yaml"


class RteDiagBswTest(unittest.TestCase):
    """COM-32: values of the basic software (ecu.*, adc.*, bsw.*) as the source of a data identifier.

    Own profile test/diag_bsw/diag.yaml with the ECU description of test/diag and the project
    buildtest_s32k148_min (S32K148 without SBC, ADC channel POTI with sensor plan, internal input BANDGAP, CAN0/CAN1).
    """

    setUp, tearDown = RteDiagTest.setUp, RteDiagTest.tearDown
    run_with, expect_error, generated = RteDiagTest.run_with, RteDiagTest.expect_error, RteDiagTest.generated

    def with_bsw(self, change=None):
        data = copy.deepcopy(DIAG_BSW)
        if change is not None:
            change(data)
        return data

    def element(self, did: int, element: int, **values):
        def change(d):
            d["data_identifiers"][did]["data"][element].update(values)
        return self.with_bsw(change)

    def test_functions_read_the_bsw(self):
        self.run_with(diag=self.with_bsw())
        header = self.generated("RteDiag.h")
        for signature in ("bool readDid_EcuStatus(uint8_t& state, uint32_t& wakeup, uint8_t& canWake);",
                          "bool readDid_Poti(uint16_t& voltage, uint16_t& raw, int16_t& value, uint8_t& quality);",
                          "bool readDid_Supply(uint8_t& state, uint8_t& ok, uint16_t& voltage, uint8_t& quality, "
                          "uint8_t& reason, uint8_t& watchdog, uint32_t& uptime);",
                          "bool readDid_MotorStatus(uint16_t& speed, uint8_t& state);"):
            self.assertIn(signature, header)
        config = self.generated()
        # declarations of the hand-overs and the sensor conversion, only because the description needs them
        self.assertIn("void readWakeupSources(uint32_t& sources);", config)
        self.assertIn("void readPower(uint8_t& power_state, uint16_t& power_mv, uint8_t& power_health);", config)
        self.assertIn("#include <bsp/adc/analogSensorConfiguration.h>", config)
        # the values of the rte.yaml targets, converted to the element type
        self.assertIn("canWake = static_cast<uint8_t>(((bsw_ecu_wakeup & 0x00000200U) != 0U) ? 1U : 0U); "
                      "// ecu.wakeup.CAN1", config)
        self.assertIn("voltage = ((bsw_adc_mv_POTI > 65535U) ? static_cast<uint16_t>(65535) : "
                      "static_cast<uint16_t>(bsw_adc_mv_POTI)); // adc.mv.POTI", config)
        self.assertIn("value = ((bsw_sensor_POTI_value > 32767) ? static_cast<int16_t>(32767) : "
                      "((bsw_sensor_POTI_value < -32768) ? static_cast<int16_t>(-32768) : "
                      "static_cast<int16_t>(bsw_sensor_POTI_value))); // adc.phys.POTI", config)
        self.assertIn("bandgap = static_cast<float>(bsw_adc_mv_BANDGAP); // adc.mv.BANDGAP", config)
        # one reading per service, like the task cycle
        supply = config.split("bool readDid_Supply(")[1].split("\n}\n")[0]
        self.assertEqual(supply.count("::bswservice::rte::readPower("), 1)
        self.assertNotIn("LockType", supply)
        # port and BSW value in one identifier: the BSW value first, then the lock for the port
        status = config.split("bool readDid_MotorStatus(")[1].split("\n}\n")[0]
        self.assertLess(status.index("::ecustate::rte::readState(bsw_ecu_state);"),
                        status.index("::async::LockType const lock;"))
        self.assertLess(status.index("::async::LockType const lock;"), status.index("Rte_Buf_MotorSwc_Speed_Speed"))
        self.assertEqual(self.stderr.count("diag.yaml"), 0, self.stderr)

    def test_header_is_the_one_diaggen_writes(self):
        self.run_with(diag=self.with_bsw())
        desc = description(self.with_bsw(), self.tmp)
        self.assertEqual(self.generated("RteDiag.h"), rteapi.render_header(desc, "rte.yaml", "tools/rtegen"))

    def test_bsw_names_are_checked_like_rte_targets(self):
        cases = [
            ((3, 0, {"source": {"bsw": "adc.mv.NOPE"}}),
             "data_identifiers[3].data[0].source.bsw (Poti.voltage): NOPE is no adc.mv channel of the project "
             "(known: ADCINTERNAL0, BANDGAP, POTI)"),
            ((3, 0, {"source": {"bsw": "adc.mv.poti"}}), "poti is no adc.mv channel"),
            ((3, 2, {"source": {"bsw": "adc.phys.BANDGAP"}}), "BANDGAP is an internal ADC channel"),
            ((3, 0, {"source": {"bsw": "adc.volt.POTI"}}), "expected adc.mv|adc.raw|adc.phys|adc.qlf.<CHANNEL>"),
            ((2, 2, {"source": {"bsw": "ecu.wakeup.CAN7"}}), "unknown wake-up source CAN7"),
            ((2, 0, {"source": {"bsw": "ecu.sleep_request"}}), "is no ECU state value the diagnosis can read"),
            ((5, 1, {"source": {"bsw": "bsw.power.nope"}}), "unknown value nope of the BSW service power"),
            ((5, 1, {"source": {"bsw": "bsw.clock.ms"}}), "expected bsw.<service>.<value>"),
            ((2, 1, {"type": "uint16"}),
             "data_identifiers[2].data[1].type (EcuStatus.wakeup): ecu.wakeup (reasons of this start, bit mask"),
            ((2, 0, {"type": "float32"}), "ecu.state (state of the ECU state machine"),
            ((2, 2, {"name": "bsw_can"}), "element names starting with bsw_ are reserved"),
        ]
        for (did, element, values), fragment in cases:
            with self.subTest(fragment):
                self.expect_error(fragment, diag=self.element(did, element, **values))

    def test_bsw_value_is_not_written(self):
        def writable(d):
            d["data_identifiers"][2]["write"] = True
        self.expect_error("data_identifiers[2].write (EcuStatus): state comes from ecu.state",
                          diag=self.with_bsw(writable))

    def test_unfitted_channel_is_a_warning(self):
        """A channel whose circuit is not fitted reads an open pin: warned like an rte.yaml connection."""
        signals = rtegen.board_signals(PROJECT_TWO_BUSES)
        poti = copy.deepcopy(DIAG_BSW["data_identifiers"][3])
        poti["data"] = poti["data"][:2]                               # adc.mv and adc.raw: the pin itself
        only_bsw = {"schema_version": 1, "data_identifiers": [poti]}
        unfitted = dict(signals, **{"adc.unfitted": {"POTI"}})
        plan = rtegen.plan_diag(description(only_bsw, self.tmp), {}, {}, unfitted,
                                rtegen.wakeup_sources(PROJECT_TWO_BUSES))
        self.assertEqual(len([w for w in plan.warnings if "POTI is not fitted" in w]), 2, plan.warnings)
        # the physical value of an unfitted circuit does not exist: an error, as for an rte.yaml connection
        with self.assertRaises(rtegen.RteError) as ctx:
            rtegen.plan_diag(description({"schema_version": 1, "data_identifiers": [DIAG_BSW["data_identifiers"][3]]},
                                         self.tmp), {}, {}, unfitted, {})
        self.assertIn("the circuit of POTI is not fitted on the board", str(ctx.exception))

    def test_values_of_the_project(self):
        """diag_bsw_values: what the Studio can offer as `source.bsw`, in the shape of diaggen.STACK_VALUES."""
        values = rtegen.diag_bsw_values(PROJECT_TWO_BUSES)
        for name in ("ecu.state", "ecu.wakeup", "ecu.wakeup.CAN1", "adc.mv.POTI", "adc.mv.BANDGAP", "adc.raw.POTI",
                     "adc.phys.POTI", "adc.qlf.POTI", "bsw.power.supply_mv", "bsw.reset.reason", "bsw.time.us"):
            self.assertIn(name, values)
        self.assertNotIn("adc.phys.BANDGAP", values)          # internal input: no physical value
        self.assertEqual(values["ecu.wakeup"]["types"], ("uint32",))
        self.assertIn("float32", values["adc.mv.POTI"]["types"])
        self.assertNotIn("float32", values["bsw.power.qlf"]["types"])
        self.assertTrue(all(rteapi.is_rte_bsw(name) for name in values))
        # the eval board offers its own channels, not those of this profile
        demo = rtegen.diag_bsw_values(rtegen.nxconfig.load(DEMO_FILE))
        self.assertIn("adc.phys.12VBAT_CURRENT", demo)
        self.assertNotIn("adc.mv.POTI", demo)

    def test_findings_point_into_diag_yaml(self):
        """validate(): a finding of the description carries diag.yaml, its YAML path and line (Studio)."""
        path = DIAG_BSW_DIR / "diag.yaml"
        _, _, tree = rtegen.nxconfig.config.read_diag(path)
        text = "diag.yaml data_identifiers[3].data[2].source.bsw (Poti.value): BANDGAP is an internal ADC channel"
        m = rtegen.rte_message(text, self.tmp / "rte.yaml", None, diag=(path, tree))
        self.assertEqual(m.path, ("data_identifiers", 3, "data", 2, "source", "bsw"))
        self.assertEqual(Path(m.file), path)
        lines = path.read_text(encoding="utf-8").splitlines()
        self.assertIn("adc.phys.POTI", lines[m.line - 1])
        # without a description the message stays with the ECU description, as before
        self.assertEqual(Path(rtegen.rte_message(text, self.tmp / "rte.yaml").file), self.tmp / "rte.yaml")

    def test_snapshot_of_a_bsw_identifier(self):
        """NVM-03 with COM-32: a snapshot record may name an identifier with BSW values; the fault memory reads
        it through the same job as service 22 (ecu/uds DiagDidReader), so the RTE only has to serve it."""
        dem_dir = HERE / "test" / "dem"
        ecu = rtegen.with_resolved_paths(                    # RTE-22: rendered from a scratch folder
            yaml.safe_load((dem_dir / "rte.yaml").read_text(encoding="utf-8")), dem_dir / "rte.yaml")
        data = yaml.safe_load((dem_dir / "diag.yaml").read_text(encoding="utf-8"))
        data["data_identifiers"].append({"identifier": 0x0320, "name": "Conditions", "data": [
            {"name": "state", "type": "uint8", "source": {"bsw": "ecu.state"}},
            {"name": "supply", "type": "uint16", "source": {"bsw": "bsw.power.supply_mv"}},
            {"name": "poti", "type": "uint16", "source": {"bsw": "adc.mv.POTI"}}]})
        data["dtcs"][0]["snapshots"][0]["dids"].append("Conditions")
        self.run_with(ecu=ecu, diag=data)
        self.assertIn("{0x0320U, 5U}", self.generated("RteDem.h"))
        self.assertIn("bool readDid_Conditions(uint8_t& state, uint16_t& supply, uint16_t& poti);",
                      self.generated("RteDiag.h"))


class RteDiagBswHostTest(unittest.TestCase):
    """COM-32: compiles the generated RTE of test/diag_bsw with the sensor header bswgen generates for
    buildtest_s32k148_min and runs test/diag_bsw/rte_diag_bsw_host_test.cpp; then built-in faults."""

    def test_bsw_runtime(self):
        sys.path.insert(0, str(REPO / "tools" / "bswgen"))
        import bswgen  # noqa: E402

        tmp = Path(tempfile.mkdtemp())
        try:
            out, board = tmp / "gen", tmp / "board"
            desc = description(DIAG_BSW, tmp)
            with contextlib.redirect_stderr(io.StringIO()):
                rtegen.generate(DIAG_DIR / "rte.yaml", PROJECT_TWO_BUSES, out, REPO, MULTI_TASKS, diag=desc)
                bswgen.generate(PROJECT_TWO_BUSES_FILE, board)
            zig = [sys.executable, "-m", "ziglang"]
            includes = [f"-I{out}", f"-I{out / 'tasks'}", f"-I{REPO / 'libs/bsw/autosarBase/include'}",
                        f"-I{HERE / 'test' / 'can_host' / 'stubs'}", f"-I{MULTI / 'MotorSwc/generated/src'}",
                        f"-I{MULTI / 'AppSwc/generated/src'}", f"-I{board}"]
            objects = []
            for src in (out / "Rte.c", MULTI / "MotorSwc/generated/src/MotorSwc.c",
                        MULTI / "AppSwc/generated/src/AppSwc.c"):
                obj = tmp / (src.stem + ".o")
                r = subprocess.run(zig + ["cc", "-std=c99", "-O1", "-Wall", "-Wextra", "-Werror", *includes, "-c",
                                          str(src), "-o", str(obj)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr[-3000:])
                objects.append(str(obj))

            def build_and_run(config_cpp: Path, name: str):
                exe = tmp / f"{name}.exe"
                r = subprocess.run(zig + ["c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion", "-Werror",
                                          *includes, str(config_cpp), str(DIAG_BSW_DIR / "rte_diag_bsw_host_test.cpp"),
                                          *objects, "-o", str(exe)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr[-3000:])
                return subprocess.run([str(exe)], capture_output=True, text=True)

            r = build_and_run(out / "RteConfig.cpp", "rte_diag_bsw_host_test")
            print("\n" + r.stdout.strip(), file=sys.stderr)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:])
            self.assertIn(", 0 failures", r.stdout)

            # built-in faults: the test must notice each of them
            config = (out / "RteConfig.cpp").read_text(encoding="utf-8")
            lock = "    ::async::LockType const lock;\n"
            read_state = ("    uint8_t bsw_ecu_state = 0U;\n    ::ecustate::rte::readState(bsw_ecu_state);\n"
                          "    state = static_cast<uint8_t>(bsw_ecu_state); // ecu.state\n")
            mutants = {
                "wrong wake-up bit": ("(bsw_ecu_wakeup & 0x00000200U)", "(bsw_ecu_wakeup & 0x00000100U)"),
                "failed conversion answered as a value": (
                    "    if (::bios::AnalogInputScale::get(::bios::AnalogInput::Ai_BANDGAP, bsw_adc_mv_BANDGAP) "
                    "!= ::bsp::BSP_OK)\n    {\n        bsw_available = false;",
                    "    if (::bios::AnalogInputScale::get(::bios::AnalogInput::Ai_BANDGAP, bsw_adc_mv_BANDGAP) "
                    "!= ::bsp::BSP_OK)\n    {\n        bsw_available = true;"),
                "BSW value read under the RTE lock": (read_state + lock, lock + read_state),
                "not clamped": ("(bsw_adc_mv_POTI > 65535U) ? static_cast<uint16_t>(65535)",
                                "(bsw_adc_mv_POTI > 65535U) ? static_cast<uint16_t>(bsw_adc_mv_POTI)"),
                "health ignored": ("((bsw_power_health == 2U) ? QLF_ERROR", "((bsw_power_health == 2U) ? QLF_OK"),
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


if __name__ == "__main__":
    unittest.main()
