"""NVM-03: host test of the fault memory core (libs/bsw/dem/include/dem/Dem.h) with its own test profile.

  DemHostTest        test/host/DemHostTest.cpp (zig c++, -Werror -Wconversion): status byte, debouncing, confirmation,
                     aging, enable conditions, ControlDTCSetting, snapshots, extended data, overflow, persistence
  DemMutationTest    the same test against Dem.h with deliberate faults - every one of them must make it fail

Run: .venv/Scripts/python -m unittest libs/bsw/dem/test/test_dem.py
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODULE = HERE.parent
INCLUDE = MODULE / "include"
CORE = INCLUDE / "dem" / "Dem.h"
TEST = HERE / "host" / "DemHostTest.cpp"
ZIG = [sys.executable, "-m", "ziglang"]
CXX_FLAGS = ["-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion", "-Wno-sign-conversion", "-Werror"]


def build_and_run(include: Path, tmp: Path) -> subprocess.CompletedProcess:
    exe = tmp / "dem_host_test.exe"
    built = subprocess.run(ZIG + ["c++", *CXX_FLAGS, f"-I{include}", str(TEST), "-o", str(exe)],
                           capture_output=True, text=True)
    if built.returncode != 0:
        return built
    return subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)


class DemHostTest(unittest.TestCase):
    def test_core(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            result = build_and_run(INCLUDE, tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(" 0 failures", result.stdout)
        checks = int(result.stdout.strip().splitlines()[-1].split()[0])
        self.assertGreaterEqual(checks, 170)


class DemMutationTest(unittest.TestCase):
    MUTATIONS = [
        ("no jump over zero (D.6)", "if (counter < c.jumpUp)", "if (false && counter < c.jumpUp)"),
        ("no jump down", "if (counter > c.jumpDown)", "if (false && counter > c.jumpDown)"),
        ("pending not cleared at the cycle end",
         "s.status = clearBits8(s.status, status::PENDING_DTC);", "(void)0;"),
        ("DTC setting off ignored", "if ((sample == Result::NONE) || !_dtcSetting ||",
         "if ((sample == Result::NONE) ||"),
        ("enable conditions ignored", "|| ((_conditions & dtc.conditions) != dtc.conditions)", ""),
        ("aging keeps the confirmed bit",
         "s.status       = clearBits8(s.status, status::CONFIRMED_DTC);\n                        s.failedCycles = 0U;",
         "s.failedCycles = 0U;"),
        ("cycle start does not set bit 6",
         "clearBits8(s.status, status::TEST_FAILED_THIS_OPERATION_CYCLE),\n"
         "                status::TEST_NOT_COMPLETED_THIS_OPERATION_CYCLE);",
         "clearBits8(s.status, status::TEST_FAILED_THIS_OPERATION_CYCLE),\n                0U);"),
        ("occurrence on every matured failure", "if ((s.status & status::TEST_FAILED) == 0U)\n            {\n                s.occurrence",
         "if (true)\n            {\n                s.occurrence"),
        ("failed cycles counted per failure, not per cycle",
         "if (firstThisCycle)\n            {\n                s.failedCycles",
         "if (true)\n            {\n                s.failedCycles"),
        ("newest entry displaced instead of the oldest", "(_entries[e].stamp < _entries[victim].stamp)",
         "(_entries[e].stamp > _entries[victim].stamp)"),
        ("more important entry displaced", "if (other.priority < dtc.priority)", "if (false)"),
        ("clear keeps the status", "s.status                  = status::INITIAL;", "(void)0;"),
        ("load accepts another format", "&& (events[1] == NVM_FORMAT_EVENTS)", ""),
        ("time debouncing counts the first sample", "s.direction = dir; // a new direction starts at zero",
         "s.direction = dir; s.elapsedMs = _config.cycleMs; if (false) // a new direction starts at zero"),
        ("snapshot read under the lock", "        IDidReader* const reader = (_reader",
         "        Lock const outer;\n        IDidReader* const reader = (_reader"),
        ("a short failure of a BSW event is lost", "if (s.latchFailed && (result != Result::FAILED)",
         "if (false && s.latchFailed && (result != Result::FAILED)"),
        ("a full memory is retried every cycle", "return newFailure && ", "return (matured == Result::FAILED) && "),
        # COM-43: a report while ControlDTCSetting is off would become a DTC after 85 on
        ("reports taken while the DTC setting is off", "if (!_dtcSetting)\n        {\n            return; // COM-43",
         "if (false)\n        {\n            return; // COM-43"),
    ]

    def test_mutations_are_found(self):
        source = CORE.read_text(encoding="utf-8")
        for name, old, new in self.MUTATIONS:
            with self.subTest(mutation=name):
                self.assertEqual(source.count(old), 1, f"mutation text not found once: {old}")
                tmp = Path(tempfile.mkdtemp())
                try:
                    shutil.copytree(INCLUDE, tmp / "include")
                    (tmp / "include" / "dem" / "Dem.h").write_text(source.replace(old, new), encoding="utf-8")
                    result = build_and_run(tmp / "include", tmp)
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
                self.assertNotEqual(result.returncode, 0, f"mutation '{name}' was not detected:\n{result.stdout}")


if __name__ == "__main__":
    unittest.main()
