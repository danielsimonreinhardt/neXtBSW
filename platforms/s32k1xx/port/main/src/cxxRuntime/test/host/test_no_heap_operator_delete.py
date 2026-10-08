# SPDX-License-Identifier: Apache-2.0
"""DOC-28 (dev-environment.md 4.6 P5 (a), finding DE5): the platform's own operator delete never returns.

Compiles the real NoHeapOperatorDelete.cpp with NoHeapOperatorDeleteHostTest.cpp (stubs/ for the safe supervisor,
the reset and the loop) with zig c++ and runs every case of the host test; then the same with broken copies of the
unit (mutants), which must turn at least one case red. Like GCC on the target, the host build uses sized
deallocation (-fsized-deallocation), so the deleting destructor calls operator delete(void*, std::size_t).

Run: .venv/Scripts/python platforms/s32k1xx/port/main/src/cxxRuntime/test/host/test_no_heap_operator_delete.py
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
UNIT = HERE.parents[1] / "NoHeapOperatorDelete.cpp"
TEST = HERE / "NoHeapOperatorDeleteHostTest.cpp"
STUBS = HERE / "stubs"

# case: (expected exit code, expected output lines)
CASES = {
    "plain": (3, ["REPORT"]),
    "sized": (3, ["REPORT"]),
    "destructor": (3, ["REPORT"]),
    "fallback": (5, ["REPORT", "RESET", "LOOP"]),
}

SIZED_BODY = """    (void)pointer;
    (void)size;
    heapReleaseFailed(reinterpret_cast<uintptr_t>(__builtin_return_address(0)));
}"""
# both definitions: from the comment above the plain form to the end of the unit
_SOURCE = UNIT.read_text(encoding="utf-8")
DEFINITIONS = _SOURCE[_SOURCE.index("// noinline: __builtin_return_address(0)"):]

# name: (old, new) in a copy of NoHeapOperatorDelete.cpp; `old` must occur exactly once
MUTATIONS = {
    "no own operator delete (the state before DOC-28: the library's versions call free and return)":
        (DEFINITIONS, 'extern "C" void keepHelper(uintptr_t const a) { heapReleaseFailed(a); }\n'),
    "the sized form returns without a reaction": (SIZED_BODY, "    (void)pointer;\n    (void)size;\n}"),
    "the plain form returns without a reaction":
        ("    (void)pointer;\n    heapReleaseFailed(reinterpret_cast<uintptr_t>(__builtin_return_address(0)));\n}\n\n",
         "    (void)pointer;\n}\n\n"),
    "no reset after a reaction that came back":
        ("    softwareSystemReset();\n    etl::infinite_loop();\n", "    etl::infinite_loop();\n"),
    "call site 0 instead of the return address":
        ("reportEtlAssert(static_cast<uint32_t>(returnAddress))",
         "reportEtlAssert(0U * static_cast<uint32_t>(returnAddress))"),
}


def zig_available() -> bool:
    try:
        return subprocess.run([sys.executable, "-m", "ziglang", "version"], capture_output=True,
                              timeout=120).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def build(unit: Path, exe: Path) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-fsized-deallocation", "-Wall", "-Wextra",
           "-Wconversion", "-Wno-sign-conversion", "-Werror", f"-I{STUBS}", str(unit), str(TEST), "-o", str(exe)]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=600)


def run_cases(exe: Path) -> dict[str, tuple[int, list[str]]]:
    results = {}
    for case in CASES:
        run = subprocess.run([str(exe), case], capture_output=True, text=True, errors="replace", timeout=60)
        results[case] = (run.returncode, run.stdout.split())
    return results


def failures(results: dict[str, tuple[int, list[str]]]) -> list[str]:
    return [f"{case}: exit {code} {out}, expected exit {CASES[case][0]} {CASES[case][1]}"
            for case, (code, out) in results.items() if (code, out) != CASES[case]]


@unittest.skipUnless(zig_available(), "ziglang not installed (.venv)")
class NoHeapOperatorDeleteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="doc28_delete_"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_every_case_ends_in_the_reaction(self):
        exe = self.tmp / "original.exe"
        result = build(UNIT, exe)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(failures(run_cases(exe)), [])

    def test_mutants_are_detected(self):
        source = UNIT.read_text(encoding="utf-8")
        for index, (name, (old, new)) in enumerate(MUTATIONS.items()):
            with self.subTest(mutant=name):
                self.assertEqual(source.count(old), 1, f"mutation does not fit the unit any more: {name}")
                unit = self.tmp / f"m{index}" / UNIT.name
                unit.parent.mkdir()
                unit.write_text(source.replace(old, new), encoding="utf-8")
                exe = unit.with_suffix(".exe")
                result = build(unit, exe)
                self.assertEqual(result.returncode, 0, f"mutant does not compile (fault of this test): "
                                                       f"{result.stderr}")
                self.assertNotEqual(failures(run_cases(exe)), [], f"mutant not detected: {name}")


if __name__ == "__main__":
    unittest.main()
