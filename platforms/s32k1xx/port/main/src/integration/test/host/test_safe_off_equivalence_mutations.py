# SPDX-License-Identifier: Apache-2.0
"""ARCH-66: meaningfulness of SafeOffEquivalenceHostTest.cpp - the generated binding of the high-side switch behaves
like the hand-written adapter it replaces (safety review ARCH-52 B2/B5).

The host test is compiled with zig c++ as the tools/test entry safe-off-equivalence does and run. Each mutant changes
the generic side - the framework adapter integration::SafeOffOutput (ModuleBinding.h) or the access struct of the
module (highSideSwitch/HighSideSwitchSafeOff.h) - in a copy of its directory; the reference copy inside the test stays
as it is. A mutant counts as detected if the test fails or hangs.

Run: .venv/Scripts/python -m unittest platforms/s32k1xx/port/main/src/integration/test/host/test_safe_off_equivalence_mutations.py
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
INTEGRATION = HERE.parents[1]
ROOT = HERE.parents[7]
HSS = ROOT / "libs/bsw/highSideSwitch"
TEST = "test/host/SafeOffEquivalenceHostTest.cpp"
FLAGS = ["-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion", "-Wno-sign-conversion", "-Werror"]
RUN_LIMIT_S = 60

FRAMEWORK = "integration/ModuleBinding.h"
ACCESS = "highSideSwitch/include/highSideSwitch/HighSideSwitchSafeOff.h"

# (what, file, old, new): `old` must occur exactly once
MUTANTS = [
    ("off does not switch the driver off", ACCESS,
     "static void off(Owner& owner) { (void)channelOf(owner).requestOff(); }",
     "static void off(Owner& owner) { (void)owner; }"),
    ("an explicit enable is taken from the current state, not the switch-on counter", ACCESS,
     "return channelOf(owner).getSwitch().getCounters().switchOns;",
     "return channelOf(owner).getSwitch().isEnableOutput() ? 1U : 0U;"),
    ("the lock of the safe state is not handed to the driver", ACCESS,
     "        channelOf(owner).setSwitchOnLock(lock);\n", "        (void)owner;\n        (void)lock;\n"),
    ("holdForSafeState does not evaluate the lock", ACCESS,
     "static bool updateLock(Owner& owner) { return channelOf(owner).updateSwitchOnLock(); }",
     "static bool updateLock(Owner& owner) { return (void)owner, false; }"),
    ("the adapter forgets the switch-on count of the halt", FRAMEWORK,
     "        _enablesAtSwitchOff = Access::enables(*_system);\n", ""),
    ("the adapter releases without a halt before", FRAMEWORK,
     "return _switchedOff && (_system != nullptr) && (Access::enables(*_system) != _enablesAtSwitchOff);",
     "return (_system != nullptr) && (Access::enables(*_system) != _enablesAtSwitchOff);"),
    ("the adapter does not switch off after a halt", FRAMEWORK,
     "        Access::off(*_system);\n", ""),
    ("the adapter reports held without asking the driver", FRAMEWORK,
     "bool holdForSafeState() override { return (_system != nullptr) && Access::updateLock(*_system); }",
     "bool holdForSafeState() override { return _system != nullptr; }"),
]


def zig_available() -> bool:
    try:
        import ziglang  # noqa: F401
        return True
    except ImportError:
        return False


def build_and_run(tree: Path, name: str) -> tuple[str, str]:
    """('compile' | 'fail' | 'hang' | 'ok', output) for the copies in `tree`."""
    exe = tree / f"{name}.exe"
    includes = [tree / "integration", tree / "highSideSwitch/include"]
    cmd = [sys.executable, "-m", "ziglang", "c++", *FLAGS, *[f"-I{i}" for i in includes],
           str(HSS / "src/highSideSwitch/HighSideSwitch.cpp"), str(HSS / "src/highSideSwitch/HighSideSwitchChannel.cpp"),
           str(INTEGRATION / TEST), "-o", str(exe)]
    built = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=600)
    if built.returncode != 0:
        return "compile", built.stdout + built.stderr
    try:
        r = subprocess.run([str(exe)], cwd=ROOT, capture_output=True, text=True, timeout=RUN_LIMIT_S)
    except subprocess.TimeoutExpired:
        return "hang", ""
    return ("ok" if r.returncode == 0 else "fail"), r.stdout


@unittest.skipUnless(zig_available(), "ziglang missing (requires: ziglang)")
class SafeOffEquivalenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="nx_safeoff_"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def copy(self, name: str) -> Path:
        tree = self.tmp / name
        shutil.copytree(INTEGRATION, tree / "integration", ignore=shutil.ignore_patterns("__pycache__", "test"))
        shutil.copytree(HSS / "include", tree / "highSideSwitch/include")
        return tree

    def test_unmutated_passes(self):
        verdict, output = build_and_run(self.copy("clean"), "clean")
        self.assertEqual(verdict, "ok", output[-3000:])

    def test_mutants(self):
        for n, (what, rel, old, new) in enumerate(MUTANTS):
            with self.subTest(mutant=what):
                tree = self.copy(f"m{n}")
                path = tree / rel
                text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
                self.assertEqual(text.count(old), 1, f"mutation point of '{what}' not unique")
                path.write_text(text.replace(old, new), encoding="utf-8")
                verdict, output = build_and_run(tree, f"m{n}")
                self.assertIn(verdict, ("fail", "hang"), f"mutant '{what}' survived ({verdict}): {output[-1500:]}")


if __name__ == "__main__":
    unittest.main()
