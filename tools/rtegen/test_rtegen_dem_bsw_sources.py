"""NVM-08 (with COM-46 and IO-59): the BSW sources of the fault memory in tools/rtegen - the supply sampled from the BSW
service bsw.power (bsw.power.undervoltage/overvoltage, condition bsw.power.ok), bus-off per CAN bus and the degraded
reaction of the safe supervisor reported by the platform/integration (generated dem/demBswReporters.h).

Own test profile test/dem (NVM-03, project buildtest_s32k148_min without SBC, buses CAN0/CAN1) - its checked-in files are
read only; the extra DTCs are added in memory. Not the eval board.

  BswSourcesGenTest     generated code and header, messages with the YAML path of diag.yaml, a project without these
                        DTCs generates exactly what it generated before (empty reporter header, no readPower)
  BswSourcesHostTest    test/dem/rte_dem_bsw_sources_host_test.cpp: generated RTE + fault memory integration +
                        CanBusOffReport per generated bus + SafetyFaultReport, with fault injection
  BswSourcesMutation    the same host test against deliberately broken generated code - every fault must be found

Run: .venv/Scripts/python -m unittest tools/rtegen/test_rtegen_dem_bsw_sources.py
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
from test_rtegen_dem import (BSP_INCLUDE, CXX_FLAGS, DEM_DIR, DIAG, ECU, MULTI, MULTI_TASKS, PROJECT, SYSTEMS,  # noqa: E402
                             ZIG, description)

HOST_TEST = DEM_DIR / "rte_dem_bsw_sources_host_test.cpp"
REPORTERS = rtegen.DEM_REPORTERS_FILE


def sources_diag() -> dict:
    """test/dem/diag.yaml plus the supply DTCs, the condition bsw.power.ok and the degraded reaction."""
    data = copy.deepcopy(DIAG)
    data["enable_conditions"].append({"name": "SupplyOk", "bsw": "bsw.power.ok"})
    data["dtcs"] += [
        {"number": 0x200001, "name": "SupplyUnder", "operation_cycle": "Power",
         "event": {"bsw": "bsw.power.undervoltage"}, "debounce": {"time": {"failed_ms": 50, "passed_ms": 30}}},
        {"number": 0x200002, "name": "SupplyOver", "operation_cycle": "Power",
         "event": {"bsw": "bsw.power.overvoltage", "failed_above": 15000},
         "debounce": {"time": {"failed_ms": 50, "passed_ms": 30}}},
        {"number": 0x200003, "name": "SupplyUnderMv", "operation_cycle": "Power",
         "event": {"bsw": "bsw.power.undervoltage", "failed_below": 10500},
         "debounce": {"counter": {"failed_threshold": 1, "passed_threshold": -1}}},
        {"number": 0x200004, "name": "SafetyDegraded", "operation_cycle": "Power",
         "event": {"bsw": "safety.device_fault"},
         "debounce": {"counter": {"failed_threshold": 1, "passed_threshold": -1}}},
    ]
    return data


def generate(tmp: Path, diag: dict, out: str = "out", project=PROJECT) -> str:
    path = tmp / "rte.yaml"
    path.write_text(yaml.safe_dump(ECU, sort_keys=False), encoding="utf-8")
    desc = description(diag, tmp)
    with contextlib.redirect_stderr(io.StringIO()) as err:
        rtegen.generate(path, project, tmp / out, REPO, MULTI_TASKS, diag=desc)
    return err.getvalue()


class BswSourcesGenTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def generated(self, name="RteConfig.cpp", out="out"):
        return (self.tmp / out / name).read_text(encoding="utf-8")

    def expect_error(self, change, *fragments):
        data = sources_diag()
        change(data)
        with self.assertRaises(rtegen.RteError) as ctx:
            generate(self.tmp, data)
        for fragment in fragments:
            self.assertIn(fragment, str(ctx.exception))

    def test_reporters_and_samples(self):
        err = generate(self.tmp, sources_diag())
        header = self.generated(REPORTERS)
        self.assertIn('#define NX_DEM_CAN_BUSOFF(X) \\\n    X(CAN1, "can.busoff.CAN1")\n', header)
        self.assertIn("#define NX_DEM_SAFETY_DEVICE_FAULT 1", header)
        # reported by a module of neXtBSW now: no warning any more (COM-46, NVM-08); the supply is sampled
        for name in ("can.busoff.CAN1", "safety.device_fault", "bsw.power."):
            self.assertNotIn(f"{name} is not reported", err)
        cpp = self.generated()
        self.assertIn("void readPower(uint8_t& power_state, uint16_t& power_mv, uint8_t& power_health);", cpp)
        sample = cpp.split("uint8_t demSample(size_t const dtc)")[1].split("\n}\n")[0]
        self.assertIn("case 4U: // SupplyUnder: bsw.power.undervoltage (state of the supply provider)", sample)
        self.assertIn("if (power_state == 2U)", sample)
        self.assertIn("case 5U: // SupplyOver: bsw.power.overvoltage (supply voltage against 15000 mV)", sample)
        self.assertIn("return (power_mv > 15000U) ? 3U : 2U;", sample)
        self.assertIn("return (power_mv < 10500U) ? 3U : 2U;", sample)
        self.assertNotIn("case 7U", sample)                                    # safety.device_fault: reported
        condition = cpp.split("bool demCondition(size_t const condition)")[1].split("\n}\n")[0]
        self.assertIn("case 2U: // SupplyOk: bsw.power.ok (bsw.power)", condition)
        self.assertIn("return (power_health == 1U) && (power_state == 1U);", condition)
        self.assertIn('0U, nullptr, 0U, "safety.device_fault"}', self.generated("RteDem.h"))

    def test_without_these_dtcs_nothing_changes(self):
        # the profile of NVM-03 without supply and safety DTCs and without its bus-off DTC: an empty reporter header and
        # no hand-over of bsw.power - the code of such a project is what it was before (COM-47: nor its rte.deadline DTC)
        data = copy.deepcopy(DIAG)
        data["dtcs"] = [d for d in data["dtcs"] if d["name"] not in ("BusOff", "Deadline")]
        generate(self.tmp, data)
        header = self.generated(REPORTERS)
        self.assertNotIn("#define", header)
        self.assertIn("#pragma once", header)
        self.assertNotIn("readPower", self.generated())
        # without any DTC the header is written as well (the platform includes it unconditionally)
        none = copy.deepcopy(DIAG)
        for key in ("dtcs", "fault_memory", "operation_cycles", "enable_conditions"):
            none.pop(key)
        ecu = copy.deepcopy(ECU)
        ecu["connections"].update({"AppSwc.Led.Led": "dio.out.LED1", "AppSwc.SlowCount.SlowCount": "rte"})
        path = self.tmp / "rte2.yaml"
        path.write_text(yaml.safe_dump(ecu, sort_keys=False), encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()):
            rtegen.generate(path, PROJECT, self.tmp / "none", REPO, MULTI_TASKS, diag=description(none, self.tmp))
        self.assertNotIn("#define", self.generated(REPORTERS, out="none"))

    def test_wrong_direction_and_result(self):
        def under_above(d):
            d["dtcs"][4]["event"]["failed_above"] = 9000
        self.expect_error(under_above, "diag.yaml dtcs[4].event (SupplyUnder)", "give failed_below (mV), not "
                                                                                 "failed_above")

        def over_below(d):
            d["dtcs"][5]["event"] = {"bsw": "bsw.power.overvoltage", "failed_below": 9000}
        self.expect_error(over_below, "dtcs[5].event (SupplyOver)", "give failed_above")

        def result(d):
            d["dtcs"][4]["event"]["result"] = "boolean"
        self.expect_error(result, "dtcs[4].event (SupplyUnder)", "result boolean does not apply")

    def test_other_power_names(self):
        def value(d):
            d["dtcs"][4]["event"] = {"bsw": "bsw.power.supply_mv"}
        self.expect_error(value, "dtcs[4].event (SupplyUnder)", "bsw.power.supply_mv is no event of the supply")

        def condition(d):
            d["enable_conditions"][2]["bsw"] = "bsw.power.state"
        self.expect_error(condition, "enable_conditions[2] (SupplyOk)", "bsw.power.ok")

    def test_bus_off_names_a_used_bus(self):
        def unknown(d):
            d["dtcs"][2]["event"] = {"bsw": "can.busoff.CAN7"}
        self.expect_error(unknown, "dtcs[2].event (BusOff)", "CAN7 is no CAN bus the project uses (CAN0, CAN1)")

        def twice(d):
            d["dtcs"][6] = {**d["dtcs"][2], "number": 0x200003, "name": "BusOffAgain"}
        self.expect_error(twice, "dtcs[6].event (BusOffAgain)", "already the event of another DTC")

    def test_two_buses_in_the_order_of_the_description(self):
        data = sources_diag()
        data["dtcs"].append({"number": 0x200005, "name": "BusOff0", "operation_cycle": "Power",
                             "event": {"bsw": "can.busoff.CAN0"},
                             "debounce": {"counter": {"failed_threshold": 1, "passed_threshold": -1}}})
        generate(self.tmp, data)
        self.assertIn('    X(CAN1, "can.busoff.CAN1") \\\n    X(CAN0, "can.busoff.CAN0")\n',
                      self.generated(REPORTERS))

    def test_reporters_table(self):
        self.assertIsNotNone(rtegen.dem_bsw_reporter("can.busoff.CAN1"))
        self.assertIsNotNone(rtegen.dem_bsw_reporter("safety.device_fault"))
        self.assertIsNone(rtegen.dem_bsw_reporter("safety.rom_check_failure"))     # SYS-113: after the next start
        self.assertIsNone(rtegen.dem_bsw_reporter("bsw.power.undervoltage"))       # sampled, not reported
        self.assertEqual(set(rtegen.DEM_SUPPLY_EVENTS), {"bsw.power.undervoltage", "bsw.power.overvoltage"})

    def test_validate_reports_the_same(self):
        # TOOL-113: the Studio check runs build_model() - the refusals above come from there, not from rendering
        self.assertNotIn("_dem_supply_sample", rtegen.RENDER_STEPS)
        self.assertIn("render_dem_reporters", rtegen.RENDER_STEPS)


def build_and_run(tmp: Path, mutate=None) -> subprocess.CompletedProcess:
    out = tmp / "gen"
    desc = description(sources_diag(), tmp)
    with contextlib.redirect_stderr(io.StringIO()):
        rtegen.generate(DEM_DIR / "rte.yaml", PROJECT, out, REPO, MULTI_TASKS, diag=desc)
    if mutate is not None:
        cpp = out / "RteConfig.cpp"
        text = cpp.read_text(encoding="utf-8")
        original, replacement = mutate
        assert original in text, f"mutation does not apply any more: {original}"
        cpp.write_text(text.replace(original, replacement), encoding="utf-8")
    swc = [MULTI / "MotorSwc/generated/src", MULTI / "AppSwc/generated/src"]
    includes = [out, out / "tasks", REPO / "libs/bsw/autosarBase/include", HERE / "test" / "can_host" / "stubs",
                HERE / "test" / "nvm" / "stubs", *swc, SYSTEMS, SYSTEMS / "test" / "host",
                REPO / "libs/bsw/dem/include", REPO / "libs/bsw/nvm/include", REPO / "libs/bsw/canSystem/include"]
    objects = []
    for src in (out / "Rte.c", swc[0] / "MotorSwc.c", swc[1] / "AppSwc.c"):
        obj = tmp / (src.stem + ".o")
        r = subprocess.run(ZIG + ["cc", "-std=c99", "-O1", "-Wall", "-Wextra", "-Werror",
                                  *[f"-I{i}" for i in includes], "-c", str(src), "-o", str(obj)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            return r
        objects.append(obj)
    exe = tmp / "rte_dem_bsw_sources_host_test.exe"
    r = subprocess.run(ZIG + ["c++", *CXX_FLAGS, *[f"-I{i}" for i in includes], "-isystem", str(BSP_INCLUDE),
                              str(out / "RteConfig.cpp"), str(HOST_TEST), *map(str, objects), "-o", str(exe)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return r
    return subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)


class BswSourcesHostTest(unittest.TestCase):
    def test_sources_with_fault_injection(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            result = build_and_run(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-3000:])
        self.assertIn(" 0 failures", result.stdout)
        self.assertGreaterEqual(int(result.stdout.strip().splitlines()[-1].split()[0]), 56)


class BswSourcesMutation(unittest.TestCase):
    MUTATIONS = [
        ("a provider without a reading counts", "if (power_health != 1U)", "if (false)"),
        ("undervoltage limit inclusive", "return (power_mv < 10500U)", "return (power_mv <= 10500U)"),
        ("no voltage measured is a result", "if (power_mv == 0U)", "if (false)"),
        ("overvoltage state as undervoltage", "if (power_state == 2U)", "if (power_state == 3U)"),
        ("condition ignores the health", "return (power_health == 1U) && (power_state == 1U);",
         "return (power_state == 1U);"),
        ("supply failure not debounced", "return (power_mv > 15000U) ? 3U : 2U;", "return (power_mv > 15000U) ? 1U : 2U;"),
    ]

    def test_every_fault_is_found(self):
        for what, original, replacement in self.MUTATIONS:
            with self.subTest(fault=what):
                tmp = Path(tempfile.mkdtemp())
                try:
                    result = build_and_run(tmp, (original, replacement))
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
                self.assertNotEqual(result.returncode, 0, f"fault not found: {what}\n{result.stdout[-2000:]}")


if __name__ == "__main__":
    unittest.main()
