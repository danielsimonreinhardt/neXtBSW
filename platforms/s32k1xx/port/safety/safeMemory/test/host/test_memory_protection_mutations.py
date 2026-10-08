# SPDX-License-Identifier: Apache-2.0
"""Meaningfulness of the MPU region check host test (SYS-171, safety review check point SYS69-S2, findings SFP-F02
and SFP-F04): broken variants of MemoryProtection.cpp must fail MemoryProtectionHostTest.cpp.

Each mutation is applied to a copy of safeMemory/src and safeMemory/include; the host test (unchanged, with its stubs
mpStubs/) is compiled against the copy with zig c++ like the tools/test entry safe-memory-mpu-regions, and run. A
mutant counts as detected if the test exits non-zero or hangs; a mutant that does not compile is a fault of this
script.
Run: .venv/Scripts/python platforms/s32k1xx/port/safety/safeMemory/test/host/test_memory_protection_mutations.py
(about 1 min)
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

PROTECTION = "src/safeMemory/MemoryProtection.cpp"

CYCLIC_COMPARE = "return ((current_lock_word & WORD2_FIELDS) == (expected_lock_word & WORD2_FIELDS));"
REGION0_COMPARE = "return ((word2 & WORD2_FIELDS) == (REGION0_ACCESS & WORD2_FIELDS));"
REGION0_CALL = "    if (!checkDebuggerRegion())\n"
REGION0_WRITE = "mpu::setWordInDescriptor<0U, RegionDescriptor_AccessControl, REGION0_ACCESS>();"

# name: [(file, old, new, occurrences), ...]; `old` must occur exactly `occurrences` times
MUTATIONS = {
    # the code before SYS-171: descriptor 0 never read, WORD2 compared with the fields of M0 .. M2 only
    "state before SYS-171": [
        (PROTECTION, REGION0_CALL, "    if (false && !checkDebuggerRegion())\n", 1),
        (PROTECTION, CYCLIC_COMPARE,
         "return ((current_lock_word & 0x0001FFFFU) == (expected_lock_word & 0x0001FFFFU));", 1),
    ],
    "descriptor 0 not checked": [
        (PROTECTION, REGION0_CALL, "    if (false && !checkDebuggerRegion())\n", 1),
    ],
    "descriptor 0 compared in its M0 fields only": [
        (PROTECTION, REGION0_COMPARE, "return ((word2 & 0x3FU) == (REGION0_ACCESS & 0x3FU));", 1),
    ],
    "descriptor 0 compared with the former mask": [
        (PROTECTION, REGION0_COMPARE, "return ((word2 & 0x0001FFFFU) == (REGION0_ACCESS & 0x0001FFFFU));", 1),
    ],
    "descriptor 0 compared without its M0 fields": [
        (PROTECTION, REGION0_COMPARE, "return ((word2 & WORD2_FIELDS & ~0x3FU) == (REGION0_ACCESS & WORD2_FIELDS & ~0x3FU));", 1),
    ],
    "descriptor 0 reported as region 1": [
        (PROTECTION, "    {\n        failed_region = 0U;\n        return false;\n    }\n",
         "    {\n        failed_region = 1U;\n        return false;\n    }\n", 1),
    ],
    "descriptor 0 checked after the others": [
        (PROTECTION, REGION0_CALL + "    {\n        failed_region = 0U;\n        return false;\n    }\n", "", 1),
        (PROTECTION, "    for (uint8_t i = numOfMemoryProtectionConfigurationRamRegions + 1; i < MAX_MPU_REGIONS; ++i)\n",
         REGION0_CALL + "    {\n        failed_region = 0U;\n        return false;\n    }\n"
         "    for (uint8_t i = numOfMemoryProtectionConfigurationRamRegions + 1; i < MAX_MPU_REGIONS; ++i)\n", 1),
    ],
    "cyclic comparison with the former mask": [
        (PROTECTION, CYCLIC_COMPARE,
         "return ((current_lock_word & 0x0001FFFFU) == (expected_lock_word & 0x0001FFFFU));", 1),
    ],
    # the enables of the bus masters 4..7 are not implemented on the S32K1 (RM 15.1.2): comparing them is a false
    # alarm in every cycle where the silicon reads them as 0
    "cyclic comparison includes the unimplemented M4..M7 bits": [
        (PROTECTION, CYCLIC_COMPARE,
         "return ((current_lock_word & (WORD2_FIELDS | 0xFF000000U)) == (expected_lock_word & (WORD2_FIELDS | 0xFF000000U)));",
         1),
    ],
    "descriptor 0 compared including the unimplemented M4..M7 bits": [
        (PROTECTION, REGION0_COMPARE,
         "return ((word2 & (WORD2_FIELDS | 0xFF000000U)) == (REGION0_ACCESS & (WORD2_FIELDS | 0xFF000000U)));", 1),
    ],
    "cyclic comparison without M1PE": [
        (PROTECTION, CYCLIC_COMPARE,
         "return ((current_lock_word & WORD2_FIELDS & ~0x800U) == (expected_lock_word & WORD2_FIELDS & ~0x800U));", 1),
    ],
    "cyclic comparison without the M3 fields": [
        (PROTECTION, CYCLIC_COMPARE,
         "return ((current_lock_word & WORD2_FIELDS & ~0x7C0000U) == (expected_lock_word & WORD2_FIELDS & ~0x7C0000U));",
         1),
    ],
    "core gets access through descriptor 0": [
        (PROTECTION, REGION0_WRITE,
         "mpu::setWordInDescriptor<0U, RegionDescriptor_AccessControl, (REGION0_ACCESS | 0x6U)>();", 1),
    ],
    "descriptor 0 not written": [
        (PROTECTION, REGION0_WRITE, "", 1),
    ],
    # M3SM 00b instead of 11b: the same right, but StaticBsp's RGDAAC0 write changes the word afterwards
    "descriptor 0 written so that StaticBsp changes it": [
        (PROTECTION, REGION0_WRITE,
         "mpu::setWordInDescriptor<0U, RegionDescriptor_AccessControl, (REGION0_ACCESS & ~0x600000U)>();", 1),
    ],
}


def compile_and_run(tree: Path, workdir: Path, name: str):
    """Returns (compiled, passed, output). `tree` holds a copy of safeMemory/src and safeMemory/include."""
    exe = workdir / f"{name}.exe"
    cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion",
           "-Wno-sign-conversion", "-Wno-shorten-64-to-32", "-Werror",
           f"-I{HERE / 'mpStubs'}",
           f"-I{tree / 'include'}",
           str(HERE / "MemoryProtectionHostTest.cpp"), str(tree / PROTECTION), "-o", str(exe)]
    build = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=600)
    if build.returncode != 0:
        return False, False, build.stdout + build.stderr
    try:
        run = subprocess.run([str(exe)], cwd=ROOT, capture_output=True, text=True, timeout=RUN_LIMIT_S)
    except subprocess.TimeoutExpired:
        return True, False, "hangs"
    return True, run.returncode == 0, run.stdout[-2000:]


def copy_tree(tree: Path):
    shutil.copytree(MEMORY / "src", tree / "src")
    shutil.copytree(MEMORY / "include", tree / "include")


def apply(tree: Path, changes):
    for rel, old, new, occurrences in changes:
        path = tree / rel
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        found = text.count(old)
        if found != occurrences:
            raise AssertionError(f"{rel}: {old!r} found {found} times, expected {occurrences}")
        path.write_text(text.replace(old, new), encoding="utf-8")


class MemoryProtectionMutationTest(unittest.TestCase):
    def test_mutants_are_detected(self):
        work = Path(tempfile.mkdtemp(prefix="nx_mpu_regions_mut_"))
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
