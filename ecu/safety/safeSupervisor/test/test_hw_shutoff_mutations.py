# SPDX-License-Identifier: Apache-2.0
"""Meaningfulness of safe-supervisor-hw-shutoff (SYS-130): broken variants of the report of a hardware shut-off path
(src/safeSupervisor/HwShutoff.cpp) and of the refused return of the register check (SafeSupervisor.cpp with
NX_SAFE_SUPERVISOR_HW_SHUTOFF) must fail test/host/SafeSupervisorHwShutoffHostTest.cpp.

Each mutant is applied to a copy of the module (include, src, test); the host test is compiled with zig c++ exactly as
the tools/test entry safe-supervisor-hw-shutoff does, and run. A mutant counts as detected if the test exits non-zero
or hangs; a mutant that does not compile is a fault of this script.

SYS-135: further mutants of the degraded reaction of a mapped path (HwShutoff.cpp and
SafeSupervisor::degradeHwShutoff), and the unmutated test built once more without the degraded mapping
(-DNX_TEST_HW_SHUTOFF_WITHOUT_SD, the image of a project with a threshold and no mapping).

Run: .venv/Scripts/python -m unittest ecu/safety/safeSupervisor/test/test_hw_shutoff_mutations.py (about 4 min)
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODULE = HERE.parent
ROOT = HERE.parents[3]
HW = "src/safeSupervisor/HwShutoff.cpp"
SRC = "src/safeSupervisor/SafeSupervisor.cpp"
TEST = "test/host/SafeSupervisorHwShutoffHostTest.cpp"
FLAGS = ["-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion", "-Wno-sign-conversion", "-Werror",
         "-DPLATFORM_SUPPORT_IO", "-DPLATFORM_SUPPORT_WATCHDOG"]
RUN_LIMIT_S = 60

# (what, file, old, new): `old` must occur exactly once
MUTANTS = [
    ("known bits react again", HW, "if (plausible && ((bits & ~known) == 0U))", "if (plausible && false)"),
    ("no S1", HW, "    ::safety::SafeState::enterSafeState();\n#endif\n    return true;", "#endif\n    return true;"),
    ("copy not written", HW, "    reportedCopy       = all;\n", ""),
    ("copy not compared", HW, "if (value != reportedCopy)", "if (false)"),
    ("report without context ignored", HW, "(context != 0U) ? context : ALL;", "context;"),
    ("implausible record not rewritten as everything", HW, "uint32_t const all = known | bits;",
     "uint32_t const all = plausible ? (known | bits) : bits;"),
    ("implausible record not latched", HW, "return !plausible || (holds != heldCopy) || (holds != 0U);",
     "return (!plausible && false) || (holds != heldCopy) || (holds != 0U);"),
    ("S1 without the interrupt lock", HW, "    ::interrupts::SuspendResumeAllInterruptsScopedLock const lock;\n", ""),
    ("the register check leaves S1", SRC, "if (ok && ::safety::hwShutoff::latched())", "if (false)"),
    # SYS-135 (review SYS-128/SYS-130 C1): the degraded reaction of a mapped path
    ("S1 hold not recorded", HW, "    held     = all;\n    heldCopy = all;\n", ""),
    ("copy of the S1 hold not compared", HW, "(holds != heldCopy) || ", ""),
    ("a degraded report keeps the register check closed", HW,
     "return !plausible || (holds != heldCopy) || (holds != 0U);",
     "return !plausible || (holds != heldCopy) || (holds != 0U) || (context != 0U);"),
    ("SD for an implausible record", HW, "if (plausible && ::safety::SafeSupervisor::getInstance()",
     "if (::safety::SafeSupervisor::getInstance()"),
    ("SD never (S1 as before SYS-135)", HW, "if (plausible && ::safety::SafeSupervisor::getInstance()",
     "if (false && ::safety::SafeSupervisor::getInstance()"),
    ("SD also with findings of the path", SRC, "    if ((context & ~TIMER) != TRIPPED)",
     "    if ((context & ~TIMER & TRIPPED) == 0U)"),
    ("timer of the context ignored", SRC,
     "degradedGroupsOfId(degraded::REPORT_HW_SHUTOFF, context >> 24U, device::NO_BUS, false);",
     "degradedGroupsOfId(degraded::REPORT_HW_SHUTOFF, 3U, device::NO_BUS, false);"),
    ("SD degrades every group", SRC, "    enterDegraded(groups, degraded::REPORT_HW_SHUTOFF, context);",
     "    enterDegraded(DEGRADED_ALL_GROUPS, degraded::REPORT_HW_SHUTOFF, context);"),
    ("record names another event", SRC, "    enterDegraded(groups, degraded::REPORT_HW_SHUTOFF, context);",
     "    enterDegraded(groups, DEVICE_FAULT_ID, context);"),
]

# SYS-135: the unmutated test is also built as the image of a project with a threshold and no degraded mapping
WITHOUT_SD = ["-DNX_TEST_HW_SHUTOFF_WITHOUT_SD"]


def zig_available() -> bool:
    try:
        import ziglang  # noqa: F401
        return True
    except ImportError:
        return False


def build_and_run(module: Path, workdir: Path, name: str, extra: tuple = ()) -> str:
    """'compile' | 'fail' | 'hang' | 'ok'."""
    exe = workdir / f"{name}.exe"
    cmd = [sys.executable, "-m", "ziglang", "c++", *FLAGS, *extra, f"-I{module / 'include'}",
           f"-I{module / 'test' / 'host' / 'stubs'}",
           f"-isystem{ROOT / 'external/openbsw/libs/safety/safeMonitor/include'}", str(module / TEST), "-o", str(exe)]
    if subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=600).returncode != 0:
        return "compile"
    try:
        r = subprocess.run([str(exe)], cwd=ROOT, capture_output=True, text=True, timeout=RUN_LIMIT_S)
    except subprocess.TimeoutExpired:
        return "hang"
    return "ok" if r.returncode == 0 else "fail"


@unittest.skipUnless(zig_available(), "ziglang missing (requires: ziglang)")
@unittest.skipUnless((ROOT / "external/openbsw/libs").is_dir(), "external/openbsw missing (git submodule update)")
class MutationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def copy(self, name: str) -> Path:
        target = self.tmp / name
        for part in ("include", "src", "test/host"):
            shutil.copytree(MODULE / part, target / part)
        return target

    def test_unmutated_passes(self):
        self.assertEqual(build_and_run(self.copy("clean"), self.tmp, "clean"), "ok")

    def test_unmutated_passes_without_degraded_mapping(self):
        # SYS-135: the image of a project with a threshold and no mapping - every report holds S1 as before
        self.assertEqual(build_and_run(self.copy("clean_s1"), self.tmp, "clean_s1", tuple(WITHOUT_SD)), "ok")

    def test_mutants(self):
        for n, (what, rel, old, new) in enumerate(MUTANTS):
            with self.subTest(mutant=what):
                module = self.copy(f"m{n}")
                path = module / rel
                text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
                self.assertEqual(text.count(old), 1, f"mutation point of '{what}' not unique")
                path.write_text(text.replace(old, new), encoding="utf-8")
                self.assertIn(build_and_run(module, self.tmp, f"m{n}"), ("fail", "hang"),
                              f"mutant '{what}' survived")


if __name__ == "__main__":
    unittest.main()
