# SPDX-License-Identifier: Apache-2.0
"""Meaningfulness of the gate test (SYS-42, SM-11 MPU state check on entry and exit): broken variants must fail it.

Each mutation is applied to copies of safeLifecycle/src, safeMemory/src and safeMemory/include; the host test
(SafetyGateHostTest.cpp, unchanged) is compiled against the copies with zig c++ exactly as the tools/test entry
safety-gate does, and run. A mutant counts as detected if the test exits non-zero or hangs; a mutant that does not
compile is a fault of this script.
Run: .venv/Scripts/python platforms/s32k1xx/port/safety/safeLifecycle/test/host/test_safety_gate_mutations.py (1 min)
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIFECYCLE = HERE.parents[1]
SAFETY = HERE.parents[2]
ROOT = HERE.parents[6]
MEMORY = SAFETY / "safeMemory"
RUN_LIMIT_S = 60

MANAGER = "safeLifecycle/src/safeLifecycle/SafetyManager.cpp"
PROTECTION = "safeMemory/src/safeMemory/MemoryProtection.cpp"
UNLOCK = "safeMemory/include/safeMemory/ProtectedRamScopedUnlock.h"

# name: [(file, old, new, occurrences), ...]; `old` must occur exactly `occurrences` times
MUTATIONS = {
    "entry state read after the unlock": [
        (MANAGER,
         "    bool const safeRamLockStatusOnEnter = MemoryProtection::fusaGateIsLocked();\n"
         "    ProtectedRamScopedUnlock const protectedRamUnlock;\n",
         "    ProtectedRamScopedUnlock const protectedRamUnlock;\n"
         "    bool const safeRamLockStatusOnEnter = MemoryProtection::fusaGateIsLocked();\n", 1),
    ],
    "entry check dropped": [
        (MANAGER, "supervisor.mpuStatusCheckOnEnterMonitor.check(safeRamLockStatusOnEnter);",
         "(void)safeRamLockStatusOnEnter;", 1),
    ],
    "exit check dropped": [
        (MANAGER, "supervisor.mpuStatusCheckOnLeaveMonitor.check(safeRamLockStatusOnLeave);",
         "(void)safeRamLockStatusOnLeave;", 1),
    ],
    "exit check does not read the MPU": [
        (MANAGER, "bool const safeRamLockStatusOnLeave = MemoryProtection::fusaGateIsLocked();",
         "bool const safeRamLockStatusOnLeave = !safeRamLockStatusOnEnter;", 1),
    ],
    "gate state: everything but full access counts as locked": [
        (PROTECTION, "return (lockWord == REGION_LOCKED_READ_ACCESS);", "return (lockWord != REGION_FULL_ACCESS);", 1),
    ],
    "unlock does not open the gate": [
        (PROTECTION,
         "    bool const gateLocked = fusaGateIsLocked();\n"
         "    mpu::setWordInDescriptor<FUSA_REGION, RegionDescriptor_AccessControl, REGION_FULL_ACCESS>();\n",
         "    bool const gateLocked = fusaGateIsLocked();\n", 1),
    ],
    "restore always closes": [
        (PROTECTION, "    if (gateLocked)\n", "    if (gateLocked || true)\n", 1),
    ],
    "restore always opens": [
        (PROTECTION, "    if (gateLocked)\n", "    if (gateLocked && false)\n", 1),
    ],
    "scoped unlock without restore": [
        (UNLOCK,
         "        ::interrupts::SuspendResumeAllInterruptsScopedLock const lock;\n"
         "        MemoryProtection::fusaGateRestoreLock(_oldLock);\n",
         "        ::interrupts::SuspendResumeAllInterruptsScopedLock const lock;\n", 1),
    ],
}


def compile_and_run(tree: Path, workdir: Path, name: str):
    """Returns (compiled, passed, output). `tree` holds safeLifecycle/src and safeMemory/{src,include}."""
    exe = workdir / f"{name}.exe"
    cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion",
           "-Wno-sign-conversion", "-Wno-shorten-64-to-32", "-Werror",
           f"-I{HERE / 'gateStubs'}",
           f"-I{tree / 'safeMemory/include'}",
           f"-I{HERE / 'stubs'}",
           f"-I{LIFECYCLE / 'include'}",
           f"-I{ROOT / 'ecu/safety/safeSupervisor/include'}",
           f"-I{ROOT / 'ecu/safety/safeSupervisor/test/host/stubs'}",
           f"-isystem{ROOT / 'external/openbsw/libs/safety/safeMonitor/include'}",
           str(HERE / "SafetyGateHostTest.cpp"), str(tree / MANAGER), str(tree / PROTECTION),
           str(ROOT / "ecu/safety/safeWatchdog/src/safeWatchdog/CyclePeriodCheck.cpp"), "-o", str(exe)]
    build = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=600)
    if build.returncode != 0:
        return False, False, build.stdout + build.stderr
    try:
        run = subprocess.run([str(exe)], cwd=ROOT, capture_output=True, text=True, timeout=RUN_LIMIT_S)
    except subprocess.TimeoutExpired:
        return True, False, "hangs"
    return True, run.returncode == 0, run.stdout[-2000:]


def copy_tree(tree: Path):
    shutil.copytree(LIFECYCLE / "src", tree / "safeLifecycle/src")
    shutil.copytree(MEMORY / "src", tree / "safeMemory/src")
    shutil.copytree(MEMORY / "include", tree / "safeMemory/include")


def apply(tree: Path, changes):
    for rel, old, new, occurrences in changes:
        path = tree / rel
        text = path.read_text(encoding="utf-8")
        found = text.count(old)
        if found != occurrences:
            raise AssertionError(f"{rel}: {old!r} found {found} times, expected {occurrences}")
        path.write_text(text.replace(old, new), encoding="utf-8")


class SafetyGateMutationTest(unittest.TestCase):
    def test_mutants_are_detected(self):
        work = Path(tempfile.mkdtemp(prefix="nx_safety_gate_mut_"))
        try:
            original = work / "original"
            copy_tree(original)
            compiled, passed, output = compile_and_run(original, work, "original")
            self.assertTrue(compiled and passed, f"original does not pass:\n{output}")
            survivors, invalid = [], []
            for index, (name, changes) in enumerate(MUTATIONS.items()):
                tree = work / f"m{index}"
                copy_tree(tree)
                apply(tree, changes)
                compiled, passed, output = compile_and_run(tree, work, f"m{index}")
                if not compiled:
                    invalid.append(name)
                    state = "does not compile"
                elif passed:
                    survivors.append(name)
                    state = "SURVIVED"
                else:
                    state = "detected" + (" (hangs)" if output == "hangs" else "")
                print(f"{state:>18}: {name}", flush=True)
                if not compiled:
                    print(output[-1500:])
            print(f"{len(MUTATIONS)} mutants, {len(survivors)} survived, {len(invalid)} did not compile")
            self.assertEqual(invalid, [], "mutants must compile")
            self.assertEqual(survivors, [], "mutants not detected by the host test")
        finally:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    unittest.main()
