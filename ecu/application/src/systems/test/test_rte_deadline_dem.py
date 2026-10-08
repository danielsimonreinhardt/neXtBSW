"""COM-47: SIL test of the BSW event rte.deadline with its own test profile (host/stubs, not the eval board).

  RteDeadlineDemHostTest      host/RteDeadlineDemHostTest.cpp (zig c++, -Werror -Wconversion, NX_DEM_RTE_DEADLINE):
                              the real RteSystem.cpp + dem/DeadlineReport.h + dem::Manager of libs/bsw/dem - a missed
                              deadline fails the DTC, a deadline met again passes it after the debouncing, a clear
                              stays cleared (board 2026-09-30: 0x2F again after 14 FF FF FF until 11 01)
  RteDeadlineDemMutationTest  the same test with deliberate faults in RteSystem.cpp / DeadlineReport.h, among them the
                              behaviour before COM-47 (no PASSED while running) - each must fail

Run: .venv/Scripts/python -m unittest ecu/application/src/systems/test/test_rte_deadline_dem.py
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SYSTEMS = HERE.parent
REPO = SYSTEMS.parents[3]
HOST = HERE / "host"
TEST = HOST / "RteDeadlineDemHostTest.cpp"
APP_INCLUDE = REPO / "ecu" / "application" / "include"
DEM_INCLUDE = REPO / "libs" / "bsw" / "dem" / "include"
ZIG = [sys.executable, "-m", "ziglang"]
CXX_FLAGS = ["-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion", "-Wno-sign-conversion", "-Werror",
             "-DNX_DEM_RTE_DEADLINE=1"]


def build_and_run(systems: Path, tmp: Path) -> subprocess.CompletedProcess:
    """Builds the test with RteSystem.cpp and the headers of \\p systems (the real one or a mutated copy)."""
    exe = tmp / "rte_deadline_dem.exe"
    includes = [APP_INCLUDE, systems, HOST / "stubs", DEM_INCLUDE]
    built = subprocess.run(ZIG + ["c++", *CXX_FLAGS, *[f"-I{i}" for i in includes], str(TEST),
                                  str(systems / "RteSystem.cpp"), "-o", str(exe)],
                           capture_output=True, text=True)
    if built.returncode != 0:
        return built
    return subprocess.run([str(exe)], capture_output=True, text=True, timeout=120)


def last_counts(stdout: str) -> tuple[int, int]:
    words = stdout.strip().splitlines()[-1].replace(",", "").split()
    return int(words[-4]), int(words[-2])


class RteDeadlineDemHostTest(unittest.TestCase):
    def test_level_follows_the_deadlines(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            result = build_and_run(SYSTEMS, tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print("\n" + result.stdout.strip(), file=sys.stderr)
        checks, failures = last_counts(result.stdout)
        self.assertEqual(failures, 0)
        self.assertGreaterEqual(checks, 25)


# (file, original, mutated, what the mutant does)
MUTANTS = [
    ("dem/DeadlineReport.h",
     "(state.checked ? ::dem::Result::PREPASSED : ::dem::Result::NONE)", "::dem::Result::NONE",
     "never PASSED while running - the behaviour before COM-47 (board 2026-09-30)"),
    ("RteSystem.cpp",
     "deadlineLevels.taskFailing[_index] = duration > deadlineUs;",
     "deadlineLevels.taskFailing[_index] = deadlineLevels.taskFailing[_index] || (duration > deadlineUs);",
     "a task cycle that met its deadline again does not clear its level"),
    ("RteSystem.cpp",
     "deadlineLevels.runnableFailing[index] = duration > deadlineUs;", "(void)duration;",
     "the level of a runnable is not kept until its next call"),
    ("dem/DeadlineReport.h", "(state.failing || newViolation)", "(state.failing || (newViolation && false))",
     "a violation that is over before the fault memory samples is lost"),
    ("dem/DeadlineReport.h", "? ::dem::Result::PREPASSED", "? ::dem::Result::PASSED",
     "PASSED past the debouncing of the DTC"),
    ("dem/DeadlineReport.h", "memory->report(static_cast<uint16_t>(_dtc), level);",
     "static ::dem::Result last = ::dem::Result::NONE; if (level != last) { "
     "memory->report(static_cast<uint16_t>(_dtc), level); last = level; }",
     "reports only on a change (dem::EventReporter): the level of before 85 off stays"),
]


class RteDeadlineDemMutationTest(unittest.TestCase):
    def test_mutants_fail(self):
        for file, original, mutated, what in MUTANTS:
            with self.subTest(what):
                tmp = Path(tempfile.mkdtemp())
                try:
                    copy = tmp / "systems"
                    shutil.copytree(SYSTEMS, copy, ignore=shutil.ignore_patterns("test"))
                    target = copy / file
                    text = target.read_text(encoding="utf-8")
                    self.assertEqual(text.count(original), 1, f"{file}: {original!r} not found once")
                    target.write_text(text.replace(original, mutated), encoding="utf-8")
                    result = build_and_run(copy, tmp)
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
                self.assertEqual(result.returncode != 0 and "FAIL" in result.stdout, True,
                                 f"mutant survived ({what}): {result.stdout[-2000:]}{result.stderr[-2000:]}")


if __name__ == "__main__":
    unittest.main()
