# SPDX-License-Identifier: Apache-2.0
"""Meaningfulness of safe-supervisor-bridge-fault (SYS-140, decision C4): broken variants of the removal of the active
short on a bridge fault (SafeSupervisor.cpp with NX_MODULE_SAFESUPERVISOR_DERIVED_BRIDGE_FAULT_DEVICES, the class list
NX_SAFE_BRIDGE_FAULT_CLASSES of Degraded.h) must fail test/host/SafeSupervisorBridgeFaultHostTest.cpp.

Each mutant is applied to a copy of the module (include, src, test); the host test is compiled with zig c++ exactly as
the tools/test entry safe-supervisor-bridge-fault does, and run. A mutant counts as detected if the test exits non-zero
or hangs; a mutant that does not compile is a fault of this script.

Run: .venv/Scripts/python -m unittest ecu/safety/safeSupervisor/test/test_bridge_fault_mutations.py (about 2 min)
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
SRC = "src/safeSupervisor/SafeSupervisor.cpp"
DEGRADED = "include/safeSupervisor/Degraded.h"
TEST = "test/host/SafeSupervisorBridgeFaultHostTest.cpp"
FLAGS = ["-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion", "-Wno-sign-conversion", "-Werror",
         "-DPLATFORM_SUPPORT_IO", "-DPLATFORM_SUPPORT_WATCHDOG"]
RUN_LIMIT_S = 60

CALL = "            blockShortOnBridgeFault(word);\n"
RESET_REASON = '            reason = ResetLine{"DEVICE_FAULT", "word", ResetDetail::HEX, word};\n'

# (what, file, old, new): `old` must occur exactly once
MUTANTS = [
    ("no removal on a bridge fault", SRC, CALL, ""),
    ("removal only after the degraded walk and the hold (just before the reset)", SRC, [
        (CALL, ""), (RESET_REASON, CALL + RESET_REASON)]),
    ("OUTPUT is no bridge fault class", DEGRADED, "X(OUTPUT) X(SUPPLY) X(INTERNAL)", "X(SUPPLY) X(INTERNAL)"),
    ("SUPPLY is no bridge fault class", DEGRADED, "X(OUTPUT) X(SUPPLY) X(INTERNAL)", "X(OUTPUT) X(INTERNAL)"),
    ("INTERNAL is no bridge fault class", DEGRADED, "X(OUTPUT) X(SUPPLY) X(INTERNAL)", "X(OUTPUT) X(SUPPLY)"),
    ("IDENTITY taken as a bridge fault", DEGRADED, "X(OUTPUT) X(SUPPLY) X(INTERNAL)",
     "X(OUTPUT) X(SUPPLY) X(INTERNAL) X(IDENTITY)"),
    ("every class removes the short", SRC, "if (wellFormed && !isBridgeFaultClass(device::faultOf(word)))",
     "if (false)"),
    ("a malformed word removes nothing", SRC, ": bridge::ALL_GROUPS;", ": 0U;"),
    ("a malformed word is judged by its class", SRC, "bool const wellFormed = device::isWellFormed(word);",
     "bool const wellFormed = true;"),
    ("the device instance is not compared", SRC, "if (bridge::deviceInstance(device) == instance)",
     "if (bridge::deviceInstance(device) >= instance)"),
    ("the groups of the device are not taken", SRC, "            groups |= bridge::deviceGroups(device);",
     "            groups |= 1U;"),
    ("the wrong group is removed", SRC, "if ((groups & (1UL << group)) != 0U)", "if ((groups & (2UL << group)) != 0U)"),
    ("the family's group count is ignored", SRC, "(group < count) && (group < 32U)",
     "(group < (count + 8U)) && (group < 32U)"),
    ("removal outside the interrupt lock", SRC, CALL, None),
]


def zig_available() -> bool:
    try:
        import ziglang  # noqa: F401
        return True
    except ImportError:
        return False


def build_and_run(module: Path, workdir: Path, name: str) -> str:
    """'compile' | 'fail' | 'hang' | 'ok'."""
    exe = workdir / f"{name}.exe"
    cmd = [sys.executable, "-m", "ziglang", "c++", *FLAGS, f"-I{module / 'include'}",
           f"-I{module / 'test' / 'host' / 'stubs'}",
           f"-isystem{ROOT / 'external/openbsw/libs/safety/safeMonitor/include'}", str(module / TEST), "-o", str(exe)]
    if subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=600).returncode != 0:
        return "compile"
    try:
        r = subprocess.run([str(exe)], cwd=ROOT, capture_output=True, text=True, timeout=RUN_LIMIT_S)
    except subprocess.TimeoutExpired:
        return "hang"
    return "ok" if r.returncode == 0 else "fail"


def edits(entry) -> list[tuple[str, str]]:
    if len(entry) == 3:
        return entry[2]           # (what, file, [(old, new), ...])
    _, _, old, new = entry
    if new is None:
        # the call moved in front of the interrupt lock of handle(): the removal must run under it
        return [(old, ""), ("    ::interrupts::SuspendResumeAllInterruptsScopedLock const lock;\n    // SYS-59: the console",
                             "    blockShortOnBridgeFault(deviceFaultMonitor.getContext());\n"
                             "    ::interrupts::SuspendResumeAllInterruptsScopedLock const lock;\n    // SYS-59: the console")]
    return [(old, new)]


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

    def test_mutants(self):
        for n, entry in enumerate(MUTANTS):
            what, rel = entry[0], entry[1]
            with self.subTest(mutant=what):
                module = self.copy(f"m{n}")
                path = module / rel
                text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
                for old, new in edits(entry):
                    self.assertEqual(text.count(old), 1, f"mutation point of '{what}' not unique: {old!r}")
                    text = text.replace(old, new)
                path.write_text(text, encoding="utf-8")
                self.assertIn(build_and_run(module, self.tmp, f"m{n}"), ("fail", "hang"),
                              f"mutant '{what}' survived")


if __name__ == "__main__":
    unittest.main()
