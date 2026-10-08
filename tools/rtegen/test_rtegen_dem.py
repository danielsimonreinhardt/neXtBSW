"""NVM-03: the fault memory derived from the diagnostic description - generator, messages and the SIL test.

Own test profile test/dem (synthetic components of test/multitask, project buildtest_s32k148_min without SBC, BSW task
profile test/multitask/tasks.yaml), plus the demo project for its diag.yaml. Not the eval board.

  RteDemGenTest     RteDem.h, rte::demSample/demCondition/demCycleState, NvM blocks DemEvents/DemEntries, messages
  RteDemHostTest    test/dem/rte_dem_host_test.cpp: generated RTE + fault memory integration + NvM core over four
                    power cycles (virtual EEPROM in a file, and a port without memory)

Run: .venv/Scripts/python -m unittest tools/rtegen/test_rtegen_dem.py
"""

import contextlib
import copy
import io
import re
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

DEM_DIR = HERE / "test" / "dem"
MULTI = HERE / "test" / "multitask"
MULTI_TASKS = MULTI / "tasks.yaml"
PROJECT = rtegen.nxconfig.load(REPO / "projects" / "buildtest_s32k148_min" / "project.yaml")
# PLAT-62: the own full-featured test profile (synthetic testboard_full/testproject_full) instead of the eval board
# project, which stays private (PLAT-46 F10) - a test against it would be red in the exported open tree. What
# proves something about the eval board itself uses EVAL_DEMO_FILE and is skipped where it is missing.
DEMO_FILE = REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full" / "project.yaml"
EVAL_DEMO_FILE = REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml"
ECU = rtegen.with_resolved_paths(
    yaml.safe_load((DEM_DIR / "rte.yaml").read_text(encoding="utf-8")), DEM_DIR / "rte.yaml")
DIAG = yaml.safe_load((DEM_DIR / "diag.yaml").read_text(encoding="utf-8"))
DEM_TYPES = REPO / "libs" / "bsw" / "dem" / "include" / "dem" / "DemTypes.h"
SYSTEMS = REPO / "ecu" / "application" / "src" / "systems"
BSP_INCLUDE = REPO / "external" / "openbsw" / "libs" / "bsw" / "bsp" / "include"
ZIG = [sys.executable, "-m", "ziglang"]
CXX_FLAGS = ["-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion", "-Wno-sign-conversion", "-Werror"]


def description(data: dict, tmp: Path):
    path = tmp / "diag.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    desc, messages, _ = rtegen.nxconfig.config.read_diag(path)
    assert desc is not None, [str(m) for m in messages]
    return desc


class RteDemGenTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_with(self, ecu=None, diag=None, out="out"):
        path = self.tmp / "rte.yaml"
        path.write_text(yaml.safe_dump(ecu or ECU, sort_keys=False), encoding="utf-8")
        desc = description(diag or DIAG, self.tmp)
        with contextlib.redirect_stderr(io.StringIO()) as err:
            result = rtegen.generate(path, PROJECT, self.tmp / out, REPO, MULTI_TASKS, diag=desc)
        self.stderr = err.getvalue()
        return result

    def expect_error(self, fragment, ecu=None, diag=None):
        with self.assertRaises(rtegen.RteError) as ctx:
            self.run_with(ecu, diag)
        self.assertIn(fragment, str(ctx.exception))
        return str(ctx.exception)

    def ecu(self, drop=(), **connections):
        ecu = copy.deepcopy(ECU)
        for key in drop:
            ecu["connections"].pop(key)
        ecu["connections"].update(connections)
        return ecu

    def diag(self, change):
        data = copy.deepcopy(DIAG)
        change(data)
        return data

    def generated(self, name="RteConfig.cpp", out="out"):
        return (self.tmp / out / name).read_text(encoding="utf-8")

    # ---------------------------------------------------------------------------------------------- tables

    def test_sizes_are_the_ones_of_the_core(self):
        text = DEM_TYPES.read_text(encoding="utf-8")
        for name, value in (("NVM_EVENT_RECORD", rtegen.DEM_EVENT_RECORD), ("NVM_ENTRY_HEADER", rtegen.DEM_ENTRY_HEADER),
                            ("NVM_HEADER_BYTES", rtegen.DEM_NVM_HEADER)):
            self.assertRegex(text, rf"constexpr size_t {name}\s*= {value}U;")
        for name, value in rtegen.DEM_RESULT.items():
            self.assertRegex(text, rf"\b{name}\s*= 0?x?{value:X}U|\b{name}\s*= {value}U")
        for counter in rtegen.DEM_COUNTERS.values():
            self.assertIn(f"    {counter}", text)

    def test_tables_of_the_profile(self):
        self.run_with()
        header = self.generated("RteDem.h")
        self.assertIn('{"SpeedTooHigh", 0x100001U, 0x81U, 2U, 1U, 2U, 0U, 0U, 0x3U, ::dem::DebounceKind::COUNTER, '
                      '{4, -4, 1U, 1U, 0, 0}, {0U, 0U}, SNAPSHOTS_0, 1U, EXTENDED_0, 1U, nullptr}', header)
        # time debouncing, cycle Drive (index 1), no condition
        self.assertIn('{"CountStatus", 0x100002U, 0x01U, 3U, 1U, 0U, 1U, 1U, 0x0U, ::dem::DebounceKind::TIME, '
                      '{0, 0, 0U, 0U, 0, 0}, {25U, 20U}', header)
        self.assertIn('0U, nullptr, 0U, "can.busoff.CAN1"}', header)          # reported by the BSW
        self.assertIn("SNAPSHOT_0_0[] = {{0x0300U, 2U}, {0xFD10U, 4U}}", header)
        self.assertIn("{::dem::Counter::DID, 0xFD10U, 4U}", header)
        # 4 DTCs, 2 entries, replace oldest, 2 cycles (Power autostart), 2 conditions (Running initial), 10 ms, 10 B
        self.assertIn("CONFIG = {DTCS, 4U, 2U, true, 2U, 0x01U, 2U, 0x2U, 10U, 10U};", header)
        self.assertIn('CYCLE_NAMES[] = {"Power", "Drive"};', header)
        config_h = self.generated("RteConfig.h")
        self.assertIn("#define NX_RTE_HAS_DEM 1", config_h)
        self.assertIn("#define NX_RTE_HAS_NVM 1", config_h)          # the fault memory brings the NvM along
        self.assertIn("constexpr size_t DEM_EVENTS_BYTES      = 24U;", config_h)    # 4 + 4 * 5
        self.assertIn("constexpr size_t DEM_ENTRIES_BYTES     = 36U;", config_h)    # 4 + 2 * (6 + 10)
        config = self.generated()
        self.assertIn('{"DemEvents", 0x', config)
        self.assertRegex(config, r'\{"DemEntries", 0x[0-9A-F]{4}U, 36U, 64U, &nvmImage_DemEntries, '
                                 r'&nvmDefault_DemEntries, &nvmChanged\[1\], NvmWrite::CHANGE, 500U, '
                                 r'NvmIntegrity::QM, &nvmQuality\[1\]\}')
        self.assertIn("uint8_t nvmImage_DemEvents[24] = {};", config)
        self.assertNotIn("void nvmHandOver", config)                   # no block of a component: not generated

    def test_sources(self):
        self.run_with()
        config = self.generated()
        sample = config.split("uint8_t demSample(size_t const dtc)")[1].split("\n}\n")[0]
        self.assertIn("::async::LockType const lock;", sample)
        self.assertIn("return ((Rte_Buf_MotorSwc_Speed_Speed > 3000U)) ? 3U : 2U;", sample)   # limit
        self.assertIn("(Rte_Buf_AppSwc_SlowCount_SlowCount <= 3U) ? static_cast<uint8_t>(", sample)   # status
        self.assertNotIn("case 2U", sample)                                                   # reported
        condition = config.split("bool demCondition(size_t const condition)")[1].split("\n}\n")[0]
        # a required port reads the buffer of its source
        self.assertIn("return Rte_Buf_AppSwc_Led_Led != 0U;", condition)
        self.assertIn("::ecustate::rte::readState(state);\n            return state == 1U;", condition)
        cycle = config.split("uint8_t demCycleState(size_t const cycle)")[1].split("\n}\n")[0]
        self.assertIn("case 1U: // Drive: AppSwc.Led.Led", cycle)
        self.assertIn("void readState(uint8_t& state);", config)
        self.assertIn("time.failed_ms (CountStatus): 25 ms is not a multiple of fault_memory.cycle_ms 10; it "
                      "matures after 30 ms", self.stderr)
        # COM-46/NVM-08: the CAN system of the port reports bus-off now (dem/demBswReporters.h)
        self.assertNotIn("can.busoff.CAN1 is not reported", self.stderr)
        self.assertNotIn("rte.deadline is not", self.stderr)                  # reported by RteSystem (SYS-05)

    def test_layout_id_follows_the_table(self):
        self.run_with(out="a")
        ids_a = re.findall(r'\{"Dem\w+", (0x[0-9A-F]{4})U', self.generated(out="a"))

        def more(d):
            d["dtcs"][0]["snapshots"][0]["dids"] = ["MotorSpeed"]
        self.run_with(diag=self.diag(more), out="b")
        ids_b = re.findall(r'\{"Dem\w+", (0x[0-9A-F]{4})U', self.generated(out="b"))
        self.assertEqual(len(ids_a), 2)
        self.assertNotEqual(ids_a, ids_b)        # other entry size: the stored blocks are not misread

    def test_write_interval_zero_writes_at_the_run_down_only(self):
        self.run_with(diag=self.diag(lambda d: d["fault_memory"].update(write_interval_ms=0)))
        self.assertIn("NvmWrite::SHUTDOWN, 0U, NvmIntegrity::QM,", self.generated())

    def test_nothing_without_dtcs(self):
        def none(d):
            for key in ("dtcs", "fault_memory", "operation_cycles", "enable_conditions"):
                d.pop(key)
        self.run_with(ecu=self.ecu(drop=(), **{"AppSwc.Led.Led": "dio.out.LED1",
                                               "AppSwc.SlowCount.SlowCount": "rte"}), diag=self.diag(none))
        self.assertFalse((self.tmp / "out" / "RteDem.h").exists())
        self.assertNotIn("NX_RTE_HAS_DEM", self.generated("RteConfig.h"))
        self.assertNotIn("NX_RTE_HAS_NVM", self.generated("RteConfig.h"))
        self.assertNotIn("demSample", self.generated())

    # ---------------------------------------------------------------------------------------------- messages

    def test_event_link_must_be_the_one_of_the_description(self):
        self.expect_error("is only the same link as event.port",
                          ecu=self.ecu(**{"MotorSwc.Speed.Speed": ["rte", "dem.event.CountStatus"]}))
        self.expect_error("no DTC Missing in diag.yaml",
                          ecu=self.ecu(**{"MotorSwc.Speed.Speed": ["rte", "dem.event.Missing"]}))

    def test_cycles(self):
        self.expect_error("has autostart: true, it runs with the ECU",
                          ecu=self.ecu(**{"AppSwc.Led.Led": ["dio.out.LED1", "dem.cycle.Power"]}))
        self.expect_error("autostart: false needs a boolean provided port connected to dem.cycle.Drive",
                          ecu=self.ecu(**{"AppSwc.Led.Led": "dio.out.LED1"}))
        self.expect_error("is driven by a boolean port",
                          ecu=self.ecu(**{"AppSwc.Led.Led": "dio.out.LED1",
                                          "AppSwc.Setpoint.Setpoint": ["rte", "dem.cycle.Drive"]}))
        self.expect_error("expected dem.event.<Dtc> or dem.cycle.<Cycle>",
                          ecu=self.ecu(**{"AppSwc.Setpoint.Setpoint": ["rte", "dem.other.Drive"]}))

    def test_required_port_gets_nothing_from_the_fault_memory(self):
        self.expect_error("the fault memory reads provided ports only",
                          ecu=self.ecu(**{"MotorSwc.Setpoint.Setpoint": "dem.event.SpeedTooHigh"}))

    def test_result_needed_for_a_number(self):
        def plain(d):
            d["dtcs"][0]["event"] = {"port": "MotorSwc.Speed.Speed"}
        self.expect_error("say how the uint32 value of MotorSwc.Speed.Speed becomes a test result",
                          diag=self.diag(plain))

        def boolean_of_float(d):
            d["dtcs"][0]["event"] = {"port": "MotorSwc.Wide.Wide", "result": "boolean"}
        self.expect_error("the fault memory samples unscaled scalars", diag=self.diag(boolean_of_float))

    def test_limits_fit_the_type(self):
        def negative(d):
            d["dtcs"][0]["event"] = {"port": "MotorSwc.Speed.Speed", "failed_below": 0}
        self.expect_error("failed_below 0 can never be crossed by a uint32", diag=self.diag(negative))

        def outside(d):
            d["dtcs"][0]["event"] = {"port": "AppSwc.Setpoint.Setpoint", "failed_above": 70000}
        self.expect_error("failed_above 70000 is outside the range 0..65535", diag=self.diag(outside))

        def both(d):
            d["dtcs"][0]["event"] = {"port": "AppSwc.Setpoint.Setpoint", "failed_above": 900, "failed_below": 100}
        self.run_with(diag=self.diag(both))
        self.assertIn("((Rte_Buf_AppSwc_Setpoint_Setpoint > 900U) || (Rte_Buf_AppSwc_Setpoint_Setpoint < 100U))",
                      self.generated())

    def test_qualifier_needs_a_received_message(self):
        def qlf(d):
            d["dtcs"][0]["event"] = {"bsw": "can.qlf.PC_Command"}
        self.expect_error("the RTE receives no message PC_Command", diag=self.diag(qlf))

    def test_condition_sources(self):
        def unknown(d):
            d["enable_conditions"][1]["bsw"] = "power.ok"
        self.expect_error("bsw power.ok is not a condition the RTE knows", diag=self.diag(unknown))

        def mode(d):
            d["enable_conditions"][1]["bsw"] = "ecu.state.STANDBY"
        self.run_with(diag=self.diag(mode))
        self.assertIn("return state == 2U;", self.generated())

        def wrong(d):
            d["enable_conditions"][1]["bsw"] = "ecu.state.PARKED"
        self.expect_error("unknown ECU state PARKED", diag=self.diag(wrong))

    def test_reported_event_takes_no_result(self):
        def result(d):
            d["dtcs"][2]["event"]["result"] = "boolean"
        self.expect_error("applies to sampled sources only", diag=self.diag(result))

    def test_block_names_are_reserved(self):
        self.expect_error("DemEvents is the name of a block of the fault memory",
                          ecu=self.ecu(**{"AppSwc.Setpoint.Setpoint": ["rte", "nvm.DemEvents"]}))

    def test_validate_reports_the_same(self):
        """The Studio path (validate) sees the fault memory messages of the demo project."""
        messages = rtegen.validate(DEMO_FILE, REPO)
        self.assertFalse([m for m in messages if m.severity == "error"], [str(m) for m in messages])

    # ---------------------------------------------------------------------------------------------- demo project

    def test_demo_project(self):
        config = rtegen.nxconfig.load(DEMO_FILE)
        with contextlib.redirect_stderr(io.StringIO()):
            rtegen.generate(config.rte_file, config, self.tmp / "demo", REPO)
        cpp = self.generated(out="demo")
        self.assertIn("case 0U: // MotorNtcOpenCircuit: DemoSwc.MotorNtcFiltered_mV.MotorNtcFiltered_mV (limit)", cpp)
        self.assertIn("return ((Rte_Buf_DemoSwc_MotorNtcFiltered_mV_MotorNtcFiltered_mV > 4900U)) ? 3U : 2U;", cpp)
        # the port reads the qualifier of PC_Command: QLF_OK passed, QLF_DEBOUNCE and QLF_NOT_AVAILABLE (RTE-28)
        # no result, errors failed
        self.assertIn("return (Rte_Buf_CanQlf_PC_Command == 1U) ? 2U : (((Rte_Buf_CanQlf_PC_Command == 0U) || "
                      "(Rte_Buf_CanQlf_PC_Command == 64U)) ? 255U : 3U);", cpp)
        header = self.generated("RteDem.h", out="demo")
        self.assertIn('{"PcCommandTimeout", 0xC10016U, 0x01U, 1U, 1U, 40U, 0U, 0U, 0x1U, ::dem::DebounceKind::TIME, '
                      '{0, 0, 0U, 0U, 0, 0}, {500U, 500U}', header)
        # snapshot data of the demo: 0x0110 (2) + 0xF186 (1) + 0x0120 EcuConditions (11, COM-34) = 14 bytes; seven
        # DTCs since SYS-113 (the five causes of a safety reset of the standard description, reported by the BSW)
        # (+ NVM-30: NvmEcuStatisticsPlaceDefective = eight)
        self.assertIn("CONFIG = {DTCS, 8U, 16U, true, 1U, 0x01U, 1U, 0x0U, 10U, 14U};", header)
        self.assertIn('{"SafetyResetLoop", 0x1605FFU, 0x81U, 1U, 1U, 40U, 0U, 0U, 0x0U, ::dem::DebounceKind::COUNTER, '
                      '{1, -1, 1U, 1U, 0, 0}, {0U, 0U}, SNAPSHOTS_6, 1U, EXTENDED_6, 1U, "safety.reset_loop"}', header)


class RteDemHostTest(unittest.TestCase):
    """Generated RTE of test/dem with the fault memory and the NvM core over four power cycles."""

    def test_power_cycles(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            out = tmp / "gen"
            desc = description(DIAG, tmp)
            with contextlib.redirect_stderr(io.StringIO()):
                rtegen.generate(DEM_DIR / "rte.yaml", PROJECT, out, REPO, MULTI_TASKS, diag=desc)
            swc = [MULTI / "MotorSwc/generated/src", MULTI / "AppSwc/generated/src"]
            includes = [out, out / "tasks", REPO / "libs/bsw/autosarBase/include", HERE / "test" / "can_host" / "stubs",
                        HERE / "test" / "nvm" / "stubs", *swc, SYSTEMS, SYSTEMS / "test" / "host",
                        REPO / "libs/bsw/dem/include", REPO / "libs/bsw/nvm/include"]
            objects = []
            for src in (out / "Rte.c", swc[0] / "MotorSwc.c", swc[1] / "AppSwc.c"):
                obj = tmp / (src.stem + ".o")
                r = subprocess.run(ZIG + ["cc", "-std=c99", "-O1", "-Wall", "-Wextra", "-Werror",
                                          *[f"-I{i}" for i in includes], "-c", str(src), "-o", str(obj)],
                                   capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr[-3000:])
                objects.append(obj)
            exe = tmp / "rte_dem_host_test.exe"
            r = subprocess.run(ZIG + ["c++", *CXX_FLAGS, *[f"-I{i}" for i in includes], "-isystem", str(BSP_INCLUDE),
                                      str(out / "RteConfig.cpp"), str(DEM_DIR / "rte_dem_host_test.cpp"),
                                      *map(str, objects), "-o", str(exe)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            image = tmp / "eeprom.bin"
            outputs = []
            for run, arg in (("1", str(image)), ("2", str(image)), ("3", str(image)), ("none", "-")):
                result = subprocess.run([str(exe), run, arg], capture_output=True, text=True, timeout=60)
                outputs.append(result.stdout.strip())
                self.assertEqual(result.returncode, 0, result.stdout[-3000:])
                self.assertIn(" 0 failures", result.stdout)
            print("\n" + "\n".join(outputs), file=sys.stderr)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
