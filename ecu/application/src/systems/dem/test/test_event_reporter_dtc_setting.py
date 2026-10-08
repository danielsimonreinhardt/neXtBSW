"""COM-48: host SIL test of the reporters of the BSW events against ControlDTCSetting (85): the real fault memory core
(libs/bsw/dem Dem.h), dem::EventReporter, can/CanBusOffReport.h (COM-46) and dem/SafetyFaultReport.h (NVM-08
safety.device_fault, SYS-136 safety.hw_shutoff) with its own test profile (two buses, both safety DTCs, a sampled DTC).

  EventReporterDtcSettingHostTest   test/host/EventReporterDtcSettingHostTest.cpp (zig c++, -Werror -Wconversion)
  EventReporterDtcSettingMutation   the same test with deliberate faults - every one must fail it, among them the
                                    behaviour before COM-48 (report only on a change)

Run: .venv/Scripts/python -m unittest ecu/application/src/systems/dem/test/test_event_reporter_dtc_setting.py
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SYSTEMS = HERE.parents[1]
REPO = HERE.parents[5]
DEM_INCLUDE = REPO / "libs" / "bsw" / "dem" / "include"
CAN_INCLUDE = REPO / "libs" / "bsw" / "canSystem" / "include"
REPORTER = DEM_INCLUDE / "dem" / "EventReporter.h"
SAFETY = SYSTEMS / "dem" / "SafetyFaultReport.h"
TEST = HERE / "host" / "EventReporterDtcSettingHostTest.cpp"
ZIG = [sys.executable, "-m", "ziglang"]
CXX_FLAGS = ["-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion", "-Wno-sign-conversion", "-Werror"]


def build_and_run(dem_include: Path, systems: Path, tmp: Path) -> subprocess.CompletedProcess:
    exe = tmp / "event_reporter_dtc_setting_test.exe"
    built = subprocess.run(ZIG + ["c++", *CXX_FLAGS, f"-I{CAN_INCLUDE}", f"-I{dem_include}", f"-I{systems}",
                                  str(TEST), "-o", str(exe)], capture_output=True, text=True)
    if built.returncode != 0:
        return built
    return subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)


class EventReporterDtcSettingHostTest(unittest.TestCase):
    def test_host(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            result = build_and_run(DEM_INCLUDE, SYSTEMS, tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(" 0 failures", result.stdout)
        checks = int(result.stdout.strip().splitlines()[-1].split()[0])
        self.assertGreaterEqual(checks, 45)


class EventReporterDtcSettingMutation(unittest.TestCase):
    # (what, file, original, replacement)
    MUTATIONS = [
        ("reports only on a change (before COM-48)", REPORTER, "if (level != Result::NONE) // COM-48",
         "if ((level != Result::NONE) && (level != _reported)) // COM-48"),
        ("device fault passes", SAFETY, "return (!plausible || (groups != 0U)) ? ::dem::Result::FAILED",
         "return (!plausible && (groups != 0U)) ? ::dem::Result::FAILED"),
        ("hardware shut-off passes", SAFETY, "return (!plausible || (context != 0U)) ? ::dem::Result::FAILED",
         "return (plausible && (context == 0U)) ? ::dem::Result::FAILED"),
    ]

    def test_every_fault_is_found(self):
        for what, file, original, replacement in self.MUTATIONS:
            with self.subTest(fault=what):
                tmp = Path(tempfile.mkdtemp())
                try:
                    dem_inc, systems = tmp / "dem_include", tmp / "systems"
                    shutil.copytree(DEM_INCLUDE, dem_inc)
                    (systems / "dem").mkdir(parents=True)
                    shutil.copy2(SAFETY, systems / "dem" / SAFETY.name)
                    target = (systems if file == SAFETY else dem_inc) / "dem" / file.name
                    text = target.read_text(encoding="utf-8")
                    self.assertIn(original, text, "mutation does not apply any more")
                    target.write_text(text.replace(original, replacement, 1), encoding="utf-8")
                    result = build_and_run(dem_inc, systems, tmp)
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
                self.assertNotEqual(result.returncode, 0, f"fault not found: {what}\n{result.stdout}")


if __name__ == "__main__":
    unittest.main()
