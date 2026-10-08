# SPDX-License-Identifier: Apache-2.0
"""SYS-139: the hold of an active short in the family port - ShortHoldHostTest.cpp and its meaningfulness.

The host test is compiled with zig c++ exactly as the tools/test entry short-hold does (NX_SAFE_OUTPUT_SELECT, the
table of hardFaultHandler/test/host/selectStubs, the real SafeOutputSelection.cpp, SafeState.cpp, SafeStateHold.h,
DebugHaltGuard.h, FtmPwmInhibit.h, ShortHold.h, Tmc6200PowerStageOutput.h and gd::Tmc6200) and run. Each mutant is
applied to a copy of the integration directory; it counts as detected if the test fails or hangs.

Run: .venv/Scripts/python -m unittest platforms/s32k1xx/port/main/src/integration/test/host/test_short_hold_mutations.py
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
TEST = "test/host/ShortHoldHostTest.cpp"
FLAGS = ["-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion", "-Wno-sign-conversion", "-Werror",
         "-DNX_SAFE_OUTPUT_SELECT=1"]
RUN_LIMIT_S = 60

ADAPTER = "Tmc6200PowerStageOutput.h"
SHORT = "ShortHold.h"
INHIBIT = "FtmPwmInhibit.h"

# (what, file, old, new): `old` must occur exactly once
MUTANTS = [
    ("the adapter switches the enable off after a halt anyway", ADAPTER,
     "        if (!kept()) // SYS-139: DRV_EN of a group in an active short stays on\n        {\n"
     "            (void)_driver->disable();\n        }\n", "        (void)_driver->disable();\n"),
    ("the adapter reports a kept driver as unlocked", ADAPTER,
     "(_driver->updateEnableLock() || kept())", "_driver->updateEnableLock()"),
    ("the lock ignores the short (S1 switches the enable off)", SHORT,
     "return baseLocked && !keepEnable(driverEnabled);", "return baseLocked && (driverEnabled || true);"),
    ("an enable that was off is let on in S1", SHORT,
     "return driverEnabled && groupInShort();", "return (driverEnabled || true) && groupInShort();"),
    ("the short is taken from the word without the timer clock", SHORT,
     "inline bool inShort(NxSafeOutputSelect const& entry) { return nxSafeOutputSelectedValue(&entry) != entry.value; }",
     "inline bool inShort(NxSafeOutputSelect const& entry) { return nxSafeStateSelection[entry.group - 1U] != 0U; }"),
    ("the inhibit writes freewheel over the short", INHIBIT,
     "    void inhibit() override { ::bios::ftmPwm::ftm().SWOCTRL = forcedValue(); }",
     "    void inhibit() override { ::bios::ftmPwm::ftm().SWOCTRL = ::bios::ftmPwm::CHANNEL_MASK; }"),
    ("the group lock writes freewheel over the short", INHIBIT,
     "::bios::ftmPwm::groupLocked() ? forcedValue() : 0U;",
     "::bios::ftmPwm::groupLocked() ? ::bios::ftmPwm::CHANNEL_MASK : 0U;"),
    ("the output control is taken from any register", SHORT,
     "(NX_SAFE_OUTPUT_REG(entry.address) == reg)", "(reg != nullptr)"),
    ("the hardware path removes the short of the wrong group", SHORT,
     "constexpr uint32_t PWM_GROUP = 0U;", "constexpr uint32_t PWM_GROUP = 1U;"),
]


def zig_available() -> bool:
    try:
        import ziglang  # noqa: F401
        return True
    except ImportError:
        return False


def build_and_run(integration: Path, workdir: Path, name: str) -> tuple[str, str]:
    """('compile' | 'fail' | 'hang' | 'ok', output)."""
    exe = workdir / f"{name}.exe"
    includes = [integration / "test/host/shortHoldStubs", integration,
                ROOT / "platforms/s32k1xx/hardFaultHandler/test/host/selectStubs",
                ROOT / "platforms/s32k1xx/hardFaultHandler/include", ROOT / "platforms/s32k1xx/port/safety/safeIo/include",
                ROOT / "platforms/s32k1xx/port/safety/safeIo/test/host/stubs", ROOT / "libs/bsw/gdTmc6200/include"]
    # the test includes the handler library and safeIo relative to its own place: compile it where it lies in the copy
    cmd = [sys.executable, "-m", "ziglang", "c++", *FLAGS, *[f"-I{i}" for i in includes],
           str(ROOT / "libs/bsw/gdTmc6200/src/gd/Tmc6200.cpp"), str(integration / TEST), "-o", str(exe)]
    built = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=600)
    if built.returncode != 0:
        return "compile", built.stdout + built.stderr
    try:
        r = subprocess.run([str(exe)], cwd=ROOT, capture_output=True, text=True, timeout=RUN_LIMIT_S)
    except subprocess.TimeoutExpired:
        return "hang", ""
    return ("ok" if r.returncode == 0 else "fail"), r.stdout


@unittest.skipUnless(zig_available(), "ziglang missing (requires: ziglang)")
class ShortHoldTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="nx_shorthold_"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def copy(self, name: str) -> Path:
        """A copy of the integration directory in the same place of a copied tree (relative includes of the test)."""
        tree = self.tmp / name
        target = tree / INTEGRATION.relative_to(ROOT)
        shutil.copytree(INTEGRATION, target, ignore=shutil.ignore_patterns("__pycache__", "*.py"))
        for rel in ("platforms/s32k1xx/hardFaultHandler/src", "platforms/s32k1xx/port/safety/safeIo/src"):
            shutil.copytree(ROOT / rel, tree / rel)
        return target

    def test_unmutated_passes(self):
        verdict, output = build_and_run(self.copy("clean"), self.tmp, "clean")
        self.assertEqual(verdict, "ok", output[-3000:])

    def test_mutants(self):
        for n, (what, rel, old, new) in enumerate(MUTANTS):
            with self.subTest(mutant=what):
                integration = self.copy(f"m{n}")
                path = integration / rel
                text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
                self.assertEqual(text.count(old), 1, f"mutation point of '{what}' not unique")
                path.write_text(text.replace(old, new), encoding="utf-8")
                verdict, output = build_and_run(integration, self.tmp, f"m{n}")
                self.assertIn(verdict, ("fail", "hang"), f"mutant '{what}' survived ({verdict}): {output[-1500:]}")


if __name__ == "__main__":
    unittest.main()
