"""COM-46/NVM-08: host test of CAN bus-off as a source of the fault memory (libs/bsw/canSystem/include/can/
CanBusOffReport.h) and of the reporting side of a BSW monitor (libs/bsw/dem/include/dem/EventReporter.h), with its own
test profile (two buses VEH/BODY in the real fault memory core, a third bus without a DTC).

  CanBusOffReportHostTest   test/host/CanBusOffReportHostTest.cpp (zig c++, -Werror -Wconversion)
  CanBusOffReportMutation   the same test against both headers with deliberate faults - every one must fail it

Run: .venv/Scripts/python -m unittest libs/bsw/canSystem/test/test_can_bus_off_report.py
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
CAN_INCLUDE = HERE.parent / "include"
DEM_INCLUDE = REPO / "libs" / "bsw" / "dem" / "include"
REPORT = CAN_INCLUDE / "can" / "CanBusOffReport.h"
REPORTER = DEM_INCLUDE / "dem" / "EventReporter.h"
TEST = HERE / "host" / "CanBusOffReportHostTest.cpp"
ZIG = [sys.executable, "-m", "ziglang"]
CXX_FLAGS = ["-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion", "-Wno-sign-conversion", "-Werror"]


def build_and_run(can_include: Path, dem_include: Path, tmp: Path) -> subprocess.CompletedProcess:
    exe = tmp / "can_bus_off_report_test.exe"
    built = subprocess.run(ZIG + ["c++", *CXX_FLAGS, f"-I{can_include}", f"-I{dem_include}", str(TEST), "-o", str(exe)],
                           capture_output=True, text=True)
    if built.returncode != 0:
        return built
    return subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)


class CanBusOffReportHostTest(unittest.TestCase):
    def test_host(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            result = build_and_run(CAN_INCLUDE, DEM_INCLUDE, tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(" 0 failures", result.stdout)
        checks = int(result.stdout.strip().splitlines()[-1].split()[0])
        self.assertGreaterEqual(checks, 60)


class CanBusOffReportMutation(unittest.TestCase):
    # (what, file, original, replacement)
    MUTATIONS = [
        ("bus-off reported as passed", REPORT, ": (state == CanBusState::BUS_OFF) ? ::dem::Result::FAILED",
         ": (state == CanBusState::BUS_OFF) ? ::dem::Result::PASSED"),
        ("unknown state reported as passed", REPORT, "return (state == CanBusState::UNKNOWN)   ? ::dem::Result::NONE",
         "return (state == CanBusState::UNKNOWN)   ? ::dem::Result::PASSED"),
        ("latched bus-off ignored", REPORT, "bool const missed = enteredBusOff &&", "bool const missed = false &&"),
        ("level reported only on a change (COM-48: level of before 85 off stays)", REPORTER,
         "if (level != Result::NONE) // COM-48", "if ((level != Result::NONE) && (level != _reported)) // COM-48"),
        ("failure before the fault memory lost", REPORTER,
         "_failedPending = _failedPending || failedSinceLast || isFailed(level);",
         "_failedPending = failedSinceLast;"),
        ("missed failure not reported", REPORTER, "        if (missed)\n", "        if (false && missed)\n"),
        ("a new fault memory is not looked up", REPORTER, "if (memory != _memory)", "if (_memory == nullptr)"),
        ("report without a DTC of the name", REPORTER, "        if (_dtc < 0)\n        {\n            return;",
         "        if (_dtc < -1)\n        {\n            return;"),
        ("failure since the last call not reported to a new memory", REPORTER, "            _reported = Result::NONE;\n", "\n"),
    ]

    def test_every_fault_is_found(self):
        for what, file, original, replacement in self.MUTATIONS:
            with self.subTest(fault=what):
                tmp = Path(tempfile.mkdtemp())
                try:
                    can_inc, dem_inc = tmp / "can_include", tmp / "dem_include"
                    shutil.copytree(CAN_INCLUDE, can_inc)
                    shutil.copytree(DEM_INCLUDE, dem_inc)
                    target = (can_inc if file == REPORT else dem_inc) / file.parent.name / file.name
                    text = target.read_text(encoding="utf-8")
                    self.assertIn(original, text, "mutation does not apply any more")
                    target.write_text(text.replace(original, replacement, 1), encoding="utf-8")
                    result = build_and_run(can_inc, dem_inc, tmp)
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
                self.assertNotEqual(result.returncode, 0, f"fault not found: {what}\n{result.stdout}")


if __name__ == "__main__":
    unittest.main()
