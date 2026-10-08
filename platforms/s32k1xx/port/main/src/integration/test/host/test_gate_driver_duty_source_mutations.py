# SPDX-License-Identifier: Apache-2.0
"""Meaningfulness of the host test gate-driver-duty-source (IO-45): deliberately broken copies of
GateDriverDutySource.h must fail GateDriverDutySourceHostTest.cpp.

Each mutation is applied to a copy of the header in an include directory in front of the integration's own one; the
host test (unchanged) is compiled with zig c++ as the tools/test entry does and run. A mutant counts as detected if the
test exits non-zero or hangs; a mutant that does not compile is a fault of this script.

The mutations are the ways the source could hand the driver a wrong state:
  - the unit trap of IO-45 (the value in 0.01 % or in ticks instead of per mille),
  - a rounding that moves a phase into another state (a tiny on time as off, one tick short of full as full),
  - an unknown pair, another pair count or no period read as a state.

Run: .venv/Scripts/python platforms/s32k1xx/port/main/src/integration/test/host/test_gate_driver_duty_source_mutations.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
INTEGRATION = HERE.parents[1]
ROOT = HERE.parents[7]
HEADER = INTEGRATION / "GateDriverDutySource.h"
TEST = HERE / "GateDriverDutySourceHostTest.cpp"
DRIVER = ROOT / "libs" / "bsw" / "gdTmc6200"
RUN_LIMIT_S = 60

# name: [(old, new, occurrences)]; `old` must occur exactly `occurrences` times in the header
MUTATIONS = {
    "no conversion: the on time in ticks is handed over (unit trap)": [
        ("    duty.u = perMilleOfPeriod(ticks[0], periodTicks);", "    duty.u = static_cast<uint16_t>(ticks[0]);", 1),
    ],
    "per cent instead of per mille": [
        ("(static_cast<uint64_t>(onTicks) * ::gd::Tmc6200::DUTY_FULL) / periodTicks;",
         "(static_cast<uint64_t>(onTicks) * 100U) / periodTicks;", 1),
    ],
    "0.01 % instead of per mille (the console unit)": [
        ("(static_cast<uint64_t>(onTicks) * ::gd::Tmc6200::DUTY_FULL) / periodTicks;",
         "(static_cast<uint64_t>(onTicks) * 10000U) / periodTicks;", 1),
    ],
    "a tiny on time rounds to off": [
        ("    if (value < 1U)\n    {\n        return 1U;\n    }\n", "", 1),
    ],
    "one tick short of full rounds up to full": [
        ("(static_cast<uint64_t>(onTicks) * ::gd::Tmc6200::DUTY_FULL) / periodTicks;",
         "((static_cast<uint64_t>(onTicks) * ::gd::Tmc6200::DUTY_FULL) + periodTicks - 1U) / periodTicks;", 1),
    ],
    "rounded to nearest instead of down": [
        ("(static_cast<uint64_t>(onTicks) * ::gd::Tmc6200::DUTY_FULL) / periodTicks;",
         "((static_cast<uint64_t>(onTicks) * ::gd::Tmc6200::DUTY_FULL) + (periodTicks / 2U)) / periodTicks;", 1),
    ],
    "an on time beyond the period is not capped at full": [
        ("    if (onTicks >= periodTicks)\n    {\n        return ::gd::Tmc6200::DUTY_FULL;\n    }\n", "", 1),
    ],
    "an unknown pair is read as off": [
        ("        if (!group.highSideOnTicks(i, ticks[i]))\n        {\n            return false;\n        }\n",
         "        (void)group.highSideOnTicks(i, ticks[i]);\n", 1),
    ],
    "another number of pairs is accepted": [
        ("    if ((pairCount != 3U) || (periodTicks == 0U))", "    if ((pairCount > 3U) || (periodTicks == 0U))", 1),
    ],
    "no period is accepted": [
        ("    if ((pairCount != 3U) || (periodTicks == 0U))", "    if (pairCount != 3U)", 1),
    ],
    "phase w is phase v again": [
        ("    duty.w = perMilleOfPeriod(ticks[2], periodTicks);", "    duty.w = perMilleOfPeriod(ticks[1], periodTicks);",
         1),
    ],
}


def compile_and_run(header_dir: Path, workdir: Path, name: str):
    """Returns (compiled, passed, output)."""
    exe = workdir / f"{name}.exe"
    cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion",
           "-Wno-sign-conversion", "-Werror", f"-I{header_dir}", f"-I{INTEGRATION}", f"-I{DRIVER / 'include'}",
           str(DRIVER / "src" / "gd" / "Tmc6200.cpp"), str(TEST), "-o", str(exe)]
    build = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=600)
    if build.returncode != 0:
        return False, False, build.stdout + build.stderr
    try:
        run = subprocess.run([str(exe)], cwd=ROOT, capture_output=True, text=True, timeout=RUN_LIMIT_S)
    except subprocess.TimeoutExpired:
        return True, False, "hangs"
    return True, run.returncode == 0, run.stdout[-2000:]


class GateDriverDutySourceMutationTest(unittest.TestCase):
    def test_mutants_are_detected(self):
        work = Path(tempfile.mkdtemp(prefix="nx_gdsource_mut_"))
        try:
            original = work / "original"
            original.mkdir()
            shutil.copyfile(HEADER, original / HEADER.name)
            compiled, passed, output = compile_and_run(original, work, "original")
            self.assertTrue(compiled and passed, f"original does not pass:\n{output}")
            survivors, invalid = [], []
            for index, (name, changes) in enumerate(MUTATIONS.items()):
                text = HEADER.read_text(encoding="utf-8")
                for old, new, occurrences in changes:
                    self.assertEqual(text.count(old), occurrences, f"{name}: {old!r}")
                    text = text.replace(old, new)
                folder = work / f"m{index}"
                folder.mkdir()
                (folder / HEADER.name).write_text(text, encoding="utf-8")
                compiled, passed, output = compile_and_run(folder, work, f"m{index}")
                if not compiled:
                    invalid.append(name)
                    print(output[-1500:])
                elif passed:
                    survivors.append(name)
                state = "does not compile" if not compiled else ("SURVIVED" if passed else "detected")
                print(f"{state:>18}: {name}", flush=True)
            print(f"{len(MUTATIONS)} mutants, {len(survivors)} survived, {len(invalid)} did not compile")
            self.assertEqual(invalid, [], "mutants must compile")
            self.assertEqual(survivors, [], "mutants not detected by the host test")
        finally:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    unittest.main()
