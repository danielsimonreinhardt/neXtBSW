"""SYS-136 (safety review SYS-128/SYS-130 C7): the hardware shut-off path of a PWM group at the RTE and in the fault
memory.

The family port reports a hardware shut-off path to the safe supervisor (safety::hwShutoff::report, SYS-130; the
reaction S1 held until reset is the supervisor's). SYS-136 makes it visible:
  - RTE signals safety.hw_shutoff (uint32: the context word, boolean: anything reported) and safety.qlf.hw_shutoff
    (QLF_ERROR if the supervisor's record is implausible), read through safety::hwShutoff::read;
  - the BSW event safety.hw_shutoff: number +0x05FE of the neXtBSW range (tools/nxconfig/bswdtc.py), reported by
    RteSystem through dem/SafetyFaultReport.h (NX_DEM_SAFETY_HW_SHUTOFF of the generated dem/demBswReporters.h).

Own test profile test/dem (NVM-03: synthetic components of test/multitask, project buildtest_s32k148_min without SBC,
two tasks) - its checked-in files are read only; the connections and the DTC are added in memory. Not the eval board.

  HwShutoffGenTest     generated declaration and reads per task, types, refused connections, reporter header and
                       reporter table; a description without these targets generates not one line of it
  HwShutoffNumberTest  the DTC number in the numbering plan and its collisions (bsw_instance, event numbers)
  HwShutoffHostTest    test/dem/rte_dem_hw_shutoff_host_test.cpp: generated RTE (readInputs of both tasks) + fault
                       memory + SafetyFaultReport, with fault injection (a report, a second report, an implausible
                       record, a reset)
  HwShutoffMutation    the same host test against deliberately broken generated code and reporter - every fault found

Run: .venv/Scripts/python -m unittest tools/rtegen/test_rtegen_safety_hw_shutoff.py
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
sys.path.insert(0, str(REPO / "tools"))

import rtegen  # noqa: E402
from nxconfig import bswdtc, degraded  # noqa: E402
from test_rtegen_dem import (BSP_INCLUDE, CXX_FLAGS, DEM_DIR, DIAG, ECU, MULTI, MULTI_TASKS,  # noqa: E402
                             PROJECT, SYSTEMS, ZIG, description)

HOST_TEST = DEM_DIR / "rte_dem_hw_shutoff_host_test.cpp"
REPORTERS = rtegen.DEM_REPORTERS_FILE
REPORT_H = SYSTEMS / "dem" / "SafetyFaultReport.h"
SUPERVISOR = REPO / "ecu" / "safety" / "safeSupervisor"

# AppSwc.Speed (uint32, app task) reads the context word, MotorSwc.Enable (boolean, motor task) whether anything is
# reported, MotorSwc.Setpoint (uint16, motor task) the qualifier - two tasks, each with its own reading
CONNECTIONS = {"AppSwc.Speed.Speed": "safety.hw_shutoff", "MotorSwc.Enable.Enable": "safety.hw_shutoff",
               "MotorSwc.Setpoint.Setpoint": "safety.qlf.hw_shutoff"}


def hw_ecu(**connections) -> dict:
    ecu = copy.deepcopy(ECU)
    ecu["connections"].update(connections or CONNECTIONS)
    return ecu


def hw_diag() -> dict:
    """test/dem/diag.yaml plus the DTC of the hardware shut-off path (index 4)."""
    data = copy.deepcopy(DIAG)
    data["dtcs"].append({"number": 0x200010, "name": "HwShutoff", "operation_cycle": "Power",
                         "event": {"bsw": "safety.hw_shutoff"},
                         "debounce": {"counter": {"failed_threshold": 1, "passed_threshold": -1}}})
    return data


def generate(tmp: Path, ecu: dict, diag: dict, out: str = "out") -> str:
    path = tmp / "rte.yaml"
    path.write_text(yaml.safe_dump(ecu, sort_keys=False), encoding="utf-8")
    desc = description(diag, tmp)
    with contextlib.redirect_stderr(io.StringIO()) as err:
        rtegen.generate(path, PROJECT, tmp / out, REPO, MULTI_TASKS, diag=desc)
    return err.getvalue()


class HwShutoffGenTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def generated(self, name="RteConfig.cpp", out="out"):
        return (self.tmp / out / name).read_text(encoding="utf-8")

    def expect_error(self, fragment, **connections):
        with self.assertRaises(rtegen.RteError) as ctx:
            generate(self.tmp, hw_ecu(**connections), DIAG)
        self.assertIn(fragment, str(ctx.exception))

    def test_signals_per_task(self):
        err = generate(self.tmp, hw_ecu(), hw_diag())
        cpp = self.generated()
        self.assertEqual(cpp.count("bool read(uint32_t& context);"), 1)
        # one reading per task that reads it: app (word) and motor (boolean and qualifier)
        self.assertEqual(cpp.count("::safety::hwShutoff::read(hwShutoff)"), 2)
        self.assertIn("bool const hwShutoffPlausible = ::safety::hwShutoff::read(hwShutoff);", cpp)
        self.assertIn("(void)::safety::hwShutoff::read(hwShutoff); // implausible: the word reads 0xFFFFFFFF", cpp)
        self.assertIn("= static_cast<uint32>(hwShutoff);", cpp)
        self.assertIn("= (hwShutoff != 0U) ? 1U : 0U;", cpp)
        ok, error = rtegen.QLF["OK"], rtegen.QLF["ERROR"]
        self.assertIn(f"= static_cast<uint16>(hwShutoffPlausible ? {ok}U : {error}U);", cpp)
        # the degraded reader of SYS-117 is not pulled in by these targets
        self.assertNotIn("readGroups", cpp)
        header = self.generated(REPORTERS)
        self.assertIn("#define NX_DEM_SAFETY_HW_SHUTOFF 1", header)
        self.assertNotIn("#define NX_DEM_SAFETY_DEVICE_FAULT", header)
        self.assertNotIn("safety.hw_shutoff is not reported", err)          # a module reports it: no warning
        sample = cpp.split("uint8_t demSample(size_t const dtc)")[1].split("\n}\n")[0]
        self.assertNotIn("case 4U", sample)                                  # reported, not sampled
        self.assertIn('0U, nullptr, 0U, "safety.hw_shutoff"}', self.generated("RteDem.h"))

    def test_without_these_targets_nothing_changes(self):
        generate(self.tmp, copy.deepcopy(ECU), DIAG)
        cpp = self.generated()
        self.assertNotIn("hwShutoff", cpp)
        self.assertNotIn("HW_SHUTOFF", self.generated(REPORTERS).split("#pragma once")[1])

    def test_refused_connections(self):
        self.expect_error("type uint16 does not fit safety.hw_shutoff",
                          **{"MotorSwc.Setpoint.Setpoint": "safety.hw_shutoff"})
        self.expect_error("type boolean does not fit safety.qlf.hw_shutoff",
                          **{"MotorSwc.Enable.Enable": "safety.qlf.hw_shutoff"})
        self.expect_error("output port cannot use safety.hw_shutoff (the hardware shut-off report is read only)",
                          **{"AppSwc.Led.Led": "safety.hw_shutoff"})
        self.expect_error("unknown safety target", **{"AppSwc.Speed.Speed": "safety.hw_shutof"})

    def test_reporter_table_and_supervisor_signature(self):
        self.assertIn("RteSystem", rtegen.dem_bsw_reporter("safety.hw_shutoff"))
        source = (SUPERVISOR / "include" / "safeSupervisor" / "HwShutoff.h").read_text(encoding="utf-8")
        self.assertIn("bool read(uint32_t& context);", source)
        self.assertIn("namespace hwShutoff", source)
        report = REPORT_H.read_text(encoding="utf-8")
        self.assertIn('HW_SHUTOFF_EVENT[] = "safety.hw_shutoff";', report)
        application = (SYSTEMS / "RteSystem.cpp").read_text(encoding="utf-8")
        self.assertIn("#ifdef NX_DEM_SAFETY_HW_SHUTOFF", application)
        self.assertIn("::safety::hwShutoff::read(context)", application)


class HwShutoffNumberTest(unittest.TestCase):
    def test_fixed_sub_type_of_the_safety_block(self):
        self.assertEqual(bswdtc.offset_of("safety.hw_shutoff"), 0x05FE)
        self.assertEqual(bswdtc.derive("safety.hw_shutoff", 0x160000, 0x10000), 0x1605FE)
        self.assertEqual(bswdtc.display_code(0x1605FE), "P1605FE")
        self.assertEqual(bswdtc.offset_of("safety.reset_loop"), 0x05FF)            # unchanged
        self.assertEqual(bswdtc.derive("safety.device_fault", 0x160000, 0x10000, 0x13), 0x160513)
        with self.assertRaisesRegex(bswdtc.DerivationError, "leave bsw_instance out"):
            bswdtc.offset_of("safety.hw_shutoff", 0xFE)
        # the sub-type is taken: no cause may take it as its instance
        with self.assertRaisesRegex(bswdtc.DerivationError, "fixed sub-type"):
            bswdtc.offset_of("safety.device_fault", 0xFE)

    def test_no_collision_with_the_event_numbers(self):
        # the low byte of safety.<cause> is the number of SafeSupervisor::Event: they count from 0 and must stay below
        # the fixed sub-types at the top of the block (0xFE hardware shut-off, 0xFF reset loop; SYS-113: 0xFD MCU
        # watchdog, 0xFC fault exception, 0xFB other cause of a safety reset; SYS-196: 0xFA foreign safe_off level
        # taken back)
        events = list(degraded.read_facts(REPO).events)    # the enum of SafeSupervisor.h, in its order
        self.assertNotIn("HW_SHUTOFF", events)                                    # a report of its own, no event
        fixed = {low for _pattern, low in bswdtc.SOURCES["safety"].events if low is not None}
        self.assertEqual(fixed, {0xFA, 0xFB, 0xFC, 0xFD, 0xFE, 0xFF})
        self.assertLess(len(events), min(fixed))
        self.assertFalse(set(bswdtc.SAFETY_CAUSES.values()) & fixed)


def build_and_run(tmp: Path, mutate=None, mutate_report=None) -> subprocess.CompletedProcess:
    out = tmp / "gen"
    generate(tmp, hw_ecu(), hw_diag(), out="gen")
    if mutate is not None:
        cpp = out / "RteConfig.cpp"
        text = cpp.read_text(encoding="utf-8")
        original, replacement = mutate
        assert original in text, f"mutation does not apply any more: {original}"
        cpp.write_text(text.replace(original, replacement), encoding="utf-8")
    systems = SYSTEMS
    if mutate_report is not None:
        # a broken copy of the reporter header, found before the original (dem/SafetyFaultReport.h)
        systems = tmp / "systems"
        (systems / "dem").mkdir(parents=True)
        text = REPORT_H.read_text(encoding="utf-8")
        original, replacement = mutate_report
        assert original in text, f"mutation does not apply any more: {original}"
        (systems / "dem" / "SafetyFaultReport.h").write_text(text.replace(original, replacement), encoding="utf-8")
    swc = [MULTI / "MotorSwc/generated/src", MULTI / "AppSwc/generated/src"]
    includes = [out, out / "tasks", REPO / "libs/bsw/autosarBase/include", HERE / "test" / "can_host" / "stubs",
                HERE / "test" / "nvm" / "stubs", *swc, systems, SYSTEMS, SYSTEMS / "test" / "host",
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
    exe = tmp / "rte_dem_hw_shutoff_host_test.exe"
    r = subprocess.run(ZIG + ["c++", *CXX_FLAGS, *[f"-I{i}" for i in includes], "-isystem", str(BSP_INCLUDE),
                              str(out / "RteConfig.cpp"), str(HOST_TEST), *map(str, objects), "-o", str(exe)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return r
    return subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)


class HwShutoffHostTest(unittest.TestCase):
    def test_signals_and_dtc_with_fault_injection(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            result = build_and_run(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-3000:])
        self.assertIn(" 0 failures", result.stdout)
        self.assertGreaterEqual(int(result.stdout.strip().splitlines()[-1].split()[0]), 30)


class HwShutoffMutation(unittest.TestCase):
    MUTATIONS = [
        ("implausible record reads as plausible",
         "bool const hwShutoffPlausible = ::safety::hwShutoff::read(hwShutoff);",
         "bool const hwShutoffPlausible = ((void)::safety::hwShutoff::read(hwShutoff), true);"),
        ("boolean from bit 0 only", "= (hwShutoff != 0U) ? 1U : 0U;", "= ((hwShutoff & 1U) != 0U) ? 1U : 0U;"),
        ("word truncated to 16 bits", "= static_cast<uint32>(hwShutoff);",
         "= static_cast<uint32>(hwShutoff & 0xFFFFU);"),
    ]
    REPORT_MUTATIONS = [
        ("implausible record passes", "return (!plausible || (context != 0U)) ? ::dem::Result::FAILED",
         "return (plausible && (context != 0U)) ? ::dem::Result::FAILED"),
        ("only a full word fails", "return (!plausible || (context != 0U)) ? ::dem::Result::FAILED",
         "return (!plausible || (context == 0xFFFFFFFFU)) ? ::dem::Result::FAILED"),
    ]

    def run_mutations(self, mutations, key):
        for what, original, replacement in mutations:
            with self.subTest(fault=what):
                tmp = Path(tempfile.mkdtemp())
                try:
                    result = build_and_run(tmp, **{key: (original, replacement)})
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
                self.assertNotEqual(result.returncode, 0, f"fault not found: {what}\n{result.stdout[-2000:]}")
                # found by a check, not by a mutant that does not compile
                self.assertIn("FAIL ", result.stdout, f"{what}: {(result.stdout + result.stderr)[-2000:]}")

    def test_generated_code(self):
        self.run_mutations(self.MUTATIONS, "mutate")

    def test_reporter(self):
        self.run_mutations(self.REPORT_MUTATIONS, "mutate_report")


if __name__ == "__main__":
    unittest.main()
