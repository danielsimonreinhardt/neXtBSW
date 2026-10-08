# SPDX-License-Identifier: Apache-2.0
"""Meaningfulness of the ROM check host test (SYS-44: re-check period, known-answer test of the CRC unit, suspension
during flash writes): broken variants of RomCheck.cpp and RomCheckFlashWrites.cpp must fail it.

Each mutation is applied to a copy of safeMemory/src and safeMemory/include; the host test (RomCheckHostTest.cpp,
unchanged, with its stubs) is compiled against the copy with zig c++ like the tools/test entry rom-check, and run. A
mutant counts as detected if the test exits non-zero or hangs; a mutant that does not compile is a fault of this
script.
Run: .venv/Scripts/python platforms/s32k1xx/port/safety/safeMemory/test/host/test_rom_check_mutations.py (about 1 min)
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
MEMORY = HERE.parents[1]
ROOT = HERE.parents[6]
RUN_LIMIT_S = 60

CHECK = "src/safeMemory/RomCheck.cpp"
WRITES = "src/romCheckFlashWrites/RomCheckFlashWrites.cpp"

# name: [(file, old, new, occurrences), ...]; `old` must occur exactly `occurrences` times
MUTATIONS = {
    "known-answer test ignored": [
        (CHECK, "if (answer != KNOWN_ANSWER)", "if ((answer != KNOWN_ANSWER) && false)", 1),
    ],
    "known answer compared with the reference of the image": [
        (CHECK, "if (answer != KNOWN_ANSWER)", "if (answer == image.reference)", 1),
    ],
    "open flash write not checked before the pass": [
        (CHECK, "if (before.writing() || flashCommandRunning())", "if (flashCommandRunning())", 1),
    ],
    "write during the pass not checked": [
        (CHECK, "if ((after.begun != before.begun) || flashCommandRunning())",
         "if (((after.begun != before.begun) && false) || flashCommandRunning())", 1),
    ],
    "running flash command ignored": [
        (CHECK, "return (FTFC->FSTAT & FTFC_FSTAT_CCIF_MASK) == 0U;", "return (FTFC->FSTAT & 0U) != 0U;", 1),
    ],
    "deadline one period too early": [
        (CHECK, "((nowMs - _postponedSinceMs) > periodMs)", "((nowMs - _postponedSinceMs) >= periodMs)", 1),
    ],
    "no deadline": [
        (CHECK, "bool const overdue = _postponed &&", "bool const overdue = false && _postponed &&", 1),
    ],
    "deadline restarts at every discarded pass": [
        (CHECK, "    if (!_postponed)\n    {\n", "    if (!_postponed || discarded)\n    {\n", 1),
    ],
    "period one millisecond too late": [
        (CHECK, "((nowMs - _lastPassMs) < periodMs)", "((nowMs - _lastPassMs) <= periodMs)", 1),
    ],
    "period 0 repeats": [
        (CHECK, "(_result != Result::ROMCHECK_OK) || (periodMs == 0U) ||", "(_result != Result::ROMCHECK_OK) ||", 1),
    ],
    "time of the pass not kept": [
        (CHECK, "    _lastPassMs        = nowMs;\n", "    _lastPassMs        = 0U * nowMs;\n", 1),
    ],
    "postponement not ended by a pass": [
        (CHECK, "    _postponed         = false;\n    _result            = result;\n",
         "    _result            = result;\n", 1),
    ],
    "safety shell in every round while waiting": [
        (CHECK, "if (_postponed && !discarded)", "if (_postponed && !discarded && false)", 1),
    ],
    "failure of a later pass not reported": [
        (CHECK, "    if (result != Result::ROMCHECK_OK)\n    {\n        // Mismatch",
         "    if ((result != Result::ROMCHECK_OK) && (_statistics.passes < 2U))\n    {\n        // Mismatch", 1),
    ],
    "checks before init()": [
        (CHECK, "bool RomCheck::_armed                             = false;",
         "bool RomCheck::_armed                             = true;", 1),
    ],
    "indicator counts without the lock": [
        (WRITES, "    ::interrupts::SuspendResumeAllInterruptsScopedLock const lock;\n    _begun", "    _begun", 1),
    ],
    "indicator end() does not count": [
        (WRITES, "    _ended = _ended + 1U;", "    _ended = _ended + 0U;", 1),
    ],
    # SYS-91: the area with its own reference (calibration segment)
    "own area never checked": [
        (CHECK, "|| (ownAreaCheck == nullptr))", "|| true)", 1),
    ],
    "own area deviation ignored": [
        (CHECK, "    if (!own.ok)\n", "    if (!own.ok && false)\n", 1),
    ],
    "own area checked before a failing list": [
        (CHECK, "if ((pass.result != RomCheck::Result::ROMCHECK_OK) || (ownAreaCheck == nullptr))",
         "if (ownAreaCheck == nullptr)", 1),
    ],
    "own area check registered without the safety shell": [
        (CHECK, "    SafetyShell const safetyShell;\n    _ownAreaCheck = check;", "    _ownAreaCheck = check;", 1),
    ],
}


def compile_and_run(tree: Path, workdir: Path, name: str):
    """Returns (compiled, passed, output). `tree` holds a copy of safeMemory/src, safeMemory/include and the test."""
    exe = workdir / f"{name}.exe"
    cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
           f"-I{HERE / 'romCheckStubs'}",
           f"-I{tree / 'include'}",
           str(tree / "test/host/RomCheckHostTest.cpp"), "-o", str(exe)]
    build = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=600)
    if build.returncode != 0:
        return False, False, build.stdout + build.stderr
    try:
        run = subprocess.run([str(exe)], cwd=ROOT, capture_output=True, text=True, timeout=RUN_LIMIT_S)
    except subprocess.TimeoutExpired:
        return True, False, "hangs"
    return True, run.returncode == 0, run.stdout[-2000:]


def copy_tree(tree: Path):
    # the test includes ../../src/...: keep the layout of the module
    shutil.copytree(MEMORY / "src", tree / "src")
    shutil.copytree(MEMORY / "include", tree / "include")
    (tree / "test/host").mkdir(parents=True)
    shutil.copy2(HERE / "RomCheckHostTest.cpp", tree / "test/host/RomCheckHostTest.cpp")


def apply(tree: Path, changes):
    for rel, old, new, occurrences in changes:
        path = tree / rel
        text = path.read_text(encoding="utf-8")
        found = text.count(old)
        if found != occurrences:
            raise AssertionError(f"{rel}: {old!r} found {found} times, expected {occurrences}")
        path.write_text(text.replace(old, new), encoding="utf-8")


class RomCheckMutationTest(unittest.TestCase):
    def test_mutants_are_detected(self):
        work = Path(tempfile.mkdtemp(prefix="nx_rom_check_mut_"))
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
