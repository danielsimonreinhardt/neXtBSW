# SPDX-License-Identifier: Apache-2.0
"""Meaningfulness of the host test of the RAM ECC latent-fault test (SYS-73, SM-55): broken variants of
EccLatentTest.cpp must fail it.

Each mutation is applied to a copy of safeMemory/src and safeMemory/include; the host test (EccLatentTestHostTest.cpp,
unchanged, with its stubs) is compiled against the copy with zig c++ like the tools/test entry safe-memory-ecc-latent
(S32K14x layout: two channels, double-bit case), and run. A mutant counts as detected if the test exits non-zero or
hangs; a mutant that does not compile is a fault of this script. The register-only injected read of the target
(EccLatentTestTarget.cpp) is not host code; it is checked on the board.
Run: .venv/Scripts/python platforms/s32k1xx/port/safety/safeMemory/test/host/test_ecc_latent_test_mutations.py
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

TEST = "src/safeMemory/EccLatentTest.cpp"

# name: [(file, old, new, occurrences), ...]; `old` must occur exactly `occurrences` times
MUTATIONS = {
    "single-bit: value not checked": [
        (TEST, "if (corrected != PATTERN)", "if (false && (corrected != PATTERN))", 1),
    ],
    "single-bit: correction record not checked": [
        (TEST, "if ((record & singleFlagOf(channel)) == 0U)", "if (false && ((record & singleFlagOf(channel)) == 0U))",
         1),
    ],
    "single-bit: non-correctable record ignored": [
        (TEST, "        failures |= ECC_TEST_SINGLE_UNCORRECTABLE;", "", 1),
    ],
    "double-bit: record not checked": [
        (TEST, "        failures |= ECC_TEST_DOUBLE_NOT_DETECTED;", "", 1),
    ],
    "address never checked": [
        (TEST, "if (address != addressOf(word))", "if (false && (address != addressOf(word)))", 2),
    ],
    "double-bit case injects one bit": [
        (TEST, "constexpr uint32_t DOUBLE_MASK = 0x00000081U;", "constexpr uint32_t DOUBLE_MASK = 0x00000080U;", 1),
    ],
    "single-bit case injects two bits": [
        (TEST, "constexpr uint32_t SINGLE_MASK = 0x00010000U;", "constexpr uint32_t SINGLE_MASK = 0x00018000U;", 1),
    ],
    "EIM never checked after the reads": [
        (TEST, "        failures |= ECC_TEST_EIM_LEFT_ON;\n", "        (void)failures;\n", 2),
    ],
    "EIM not switched off after a fault": [
        (TEST, "    IP_EIM->EIMCR  = 0U;\n    IP_EIM->EICHEN = 0U;\n", "", 1),
    ],
    "record not cleared": [
        (TEST, "        ERM->SR0 = flags;", "        (void)flags;", 1),
    ],
    "record cleared for every channel": [
        (TEST, "        ERM->SR0 = flags;", "        ERM->SR0 = 0xFFFFFFFFU;", 1),
    ],
    "interrupt notification not masked": [
        (TEST, "= interruptEnables & ~interruptEnablesOf(channel);",
         "= interruptEnables & ~(0U * interruptEnablesOf(channel));", 1),
    ],
    "interrupt notification not restored": [
        (TEST, "    ERM->CR0 = interruptEnables;\n    return failures;", "    return failures;", 1),
    ],
    "record before the test ignored": [
        (TEST, "    if ((ERM->SR0 & flags) != 0U)\n    {\n        return ECC_TEST_PRECONDITION;",
         "    if (false)\n    {\n        return ECC_TEST_PRECONDITION;", 1),
    ],
    "enabled EIM before the test ignored": [
        (TEST, "    if (!eimOff())\n    {\n        switchEimOff();\n        return ECC_TEST_PRECONDITION;",
         "    if (false)\n    {\n        switchEimOff();\n        return ECC_TEST_PRECONDITION;", 1),
    ],
    "pattern not written": [
        (TEST, "    *word                           = PATTERN;\n", "", 1),
    ],
    "plain read afterwards not checked": [
        (TEST, "if (plain != PATTERN)", "if (false && (plain != PATTERN))", 1),
    ],
    "record after the test not checked": [
        (TEST, "        failures |= ECC_TEST_RECORD_NOT_CLEARED;", "", 1),
    ],
    "descriptor not reset": [
        (TEST, "    IP_EIM->EICHDn[channel].WORD1 = 0U;\n    IP_EIM->EICHDn[channel].WORD0 = 0U;\n",
         "    IP_EIM->EICHDn[channel].WORD0 = 0U;\n", 1),
    ],
    "channel without a word accepted": [
        (TEST, "(word == nullptr) ? ECC_TEST_NOT_COVERED", "(word == nullptr) ? 0U", 1),
    ],
    "failure of a channel ignored": [
        (TEST, "        if (failures != 0U)\n        {\n            result.context",
         "        if (false)\n        {\n            result.context", 1),
    ],
    "channels swapped": [
        (TEST, "return (address < SRAM_U_START) ? 0U : 1U;", "return (address < SRAM_U_START) ? 1U : 0U;", 1),
    ],
    "channel boundary one word off": [
        (TEST, "return (address < SRAM_U_START) ? 0U : 1U;", "return (address <= SRAM_U_START) ? 0U : 1U;", 1),
    ],
    "context without the channel": [
        (TEST, "| ((channel & 0xFU) << 24U)", "| ((channel & 0U) << 24U)", 1),
    ],
    "context without the latent-test source bit": [
        (TEST, "return ECC_SOURCE_LATENT_TEST | ", "return ", 1),
    ],
}


def compile_and_run(tree: Path, workdir: Path, name: str):
    """Returns (compiled, passed, output). `tree` holds a copy of safeMemory/src and safeMemory/include."""
    exe = workdir / f"{name}.exe"
    cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion",
           "-Wno-sign-conversion", "-Werror",
           f"-I{HERE / 'eccLatentStubs'}",
           f"-I{tree / 'include'}",
           str(HERE / "EccLatentTestHostTest.cpp"),
           str(tree / "src/safeMemory/EccLatentTest.cpp"),
           str(tree / "src/safeMemory/IntegrityEccHandler.cpp"),
           "-o", str(exe)]
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
        text = path.read_text(encoding="utf-8")
        found = text.count(old)
        if found != occurrences:
            raise AssertionError(f"{rel}: {old!r} found {found} times, expected {occurrences}")
        path.write_text(text.replace(old, new), encoding="utf-8")


class EccLatentTestMutationTest(unittest.TestCase):
    def test_mutants_are_detected(self):
        work = Path(tempfile.mkdtemp(prefix="nx_ecc_latent_mut_"))
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
