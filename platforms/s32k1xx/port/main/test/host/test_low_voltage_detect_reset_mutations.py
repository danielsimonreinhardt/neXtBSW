# SPDX-License-Identifier: Apache-2.0
"""Meaningfulness of low-voltage-detect-reset (SYS-125): broken variants of pmc/LowVoltageDetectReset.h must fail it.

Each mutation is applied to a copy of platforms/s32k1xx/port/main/include/pmc; the host test
(LowVoltageDetectResetHostTest.cpp, unchanged) is compiled against the copy with zig c++ exactly as the tools/test
entry low-voltage-detect-reset does, and run. A mutant counts as detected if the test exits non-zero or hangs; a
mutant that does not compile is a fault of this script.
Run: .venv/Scripts/python platforms/s32k1xx/port/main/test/host/test_low_voltage_detect_reset_mutations.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
PMC = ROOT / "platforms/s32k1xx/port/main/include/pmc"
DEVICE = ROOT / "platforms/s32k1xx/bsp/bspMcu/include/3rdparty/nxp/rtd"
TEST = HERE / "LowVoltageDetectResetHostTest.cpp"
HEADER = "pmc/LowVoltageDetectReset.h"
RUN_LIMIT_S = 60

WRITE = "    lvdsc1 = LVDSC1_START_VALUE;\n"
RULE = (
    "    return (static_cast<uint32_t>(lvdsc1)\n"
    "            & (static_cast<uint32_t>(LVDSC1_LVDRE) | static_cast<uint32_t>(LVDSC1_LVDIE)))\n"
    "           == static_cast<uint32_t>(LVDSC1_LVDRE);\n"
)

# name: [(file, old, new, occurrences), ...]; `old` must occur exactly `occurrences` times
MUTATIONS = {
    "no write (the port before SYS-125)": [
        (HEADER, WRITE, "    (void)lvdsc1;\n", 1),
    ],
    "reset and interrupt on (the vector is DefaultISR)": [
        (HEADER, WRITE, "    lvdsc1 = static_cast<uint8_t>(LVDSC1_LVDRE | LVDSC1_LVDIE);\n", 1),
    ],
    "the flag acknowledged with every start": [
        (HEADER, WRITE, "    lvdsc1 = static_cast<uint8_t>(LVDSC1_LVDRE | LVDSC1_LVDACK);\n", 1),
    ],
    "read-modify-write keeps a stray interrupt enable": [
        (HEADER, WRITE, "    lvdsc1 = static_cast<uint8_t>(static_cast<uint8_t>(lvdsc1) | LVDSC1_LVDRE);\n", 1),
    ],
    "the start value switches the reset off": [
        (HEADER, "constexpr uint8_t LVDSC1_START_VALUE = LVDSC1_LVDRE;",
         "constexpr uint8_t LVDSC1_START_VALUE = 0U;", 1),
    ],
    "a reserved bit written instead of LVDRE": [
        (HEADER, "constexpr uint8_t LVDSC1_START_VALUE = LVDSC1_LVDRE;",
         "constexpr uint8_t LVDSC1_START_VALUE = 0x08U;", 1),
    ],
    "the read-back rule ignores the interrupt enable": [
        (HEADER, RULE, "    return (lvdsc1 & LVDSC1_LVDRE) == LVDSC1_LVDRE;\n", 1),
    ],
    "the read-back rule refuses a set flag": [
        (HEADER, RULE, "    return lvdsc1 == LVDSC1_LVDRE;\n", 1),
    ],
    "the read-back rule accepts anything": [
        (HEADER, RULE, "    return (static_cast<uint32_t>(lvdsc1) & 0x100U) == 0U;\n", 1),
    ],
}


def compile_and_run(include: Path, workdir: Path, name: str):
    """Returns (compiled, passed, output). `include` holds the copy of the pmc folder as include/pmc."""
    exe = workdir / f"{name}.exe"
    cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion",
           "-Werror", f"-I{include}", f"-I{DEVICE}", str(TEST), "-o", str(exe)]
    build = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=600)
    if build.returncode != 0:
        return False, False, build.stdout + build.stderr
    try:
        run = subprocess.run([str(exe)], cwd=ROOT, capture_output=True, text=True, timeout=RUN_LIMIT_S)
    except subprocess.TimeoutExpired:
        return True, False, "hangs"
    return True, run.returncode == 0, run.stdout[-2000:]


def copy_tree(include: Path):
    shutil.copytree(PMC, include / "pmc")


def apply(include: Path, changes):
    for rel, old, new, occurrences in changes:
        path = include / rel
        text = path.read_text(encoding="utf-8")
        found = text.count(old)
        if found != occurrences:
            raise AssertionError(f"{rel}: {old!r} found {found} times, expected {occurrences}")
        path.write_text(text.replace(old, new), encoding="utf-8")


class LowVoltageDetectResetMutationTest(unittest.TestCase):
    def test_mutants_are_detected(self):
        work = Path(tempfile.mkdtemp(prefix="nx_sys125_mut_"))
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
