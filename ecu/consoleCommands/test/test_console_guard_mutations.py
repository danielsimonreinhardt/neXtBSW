# SPDX-License-Identifier: Apache-2.0
"""SYS-162 / SYS-163 (safety review group A 2026-10-01, SR-110-1 b and SR-110-2 a): meaningfulness of the host tests
checked-pwm-tester (both test profiles) and console-number.

The tests are compiled with zig c++ as their tools/test entries do and run. Each mutant is applied to a copy of the
sources (platforms/s32k1xx/port/main and ecu/consoleCommands, same relative places in a copied tree); it counts as
detected if one of the runs it targets fails or hangs. A mutant that no longer compiles counts as survived: every
mutant is meant to compile and to be caught by the run. Mutants build without -Werror.

Run: .venv/Scripts/python -m unittest ecu/consoleCommands/test/test_console_guard_mutations.py
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OPENBSW = ROOT / "external/openbsw"
UTIL = OPENBSW / "libs/bsw/util"
RUN_LIMIT_S = 120

MAIN = "platforms/s32k1xx/port/main"
CONSOLE = "ecu/consoleCommands"
TESTER = f"{MAIN}/src/systems/CheckedOutputPwmTester.cpp"
NUMBER = f"{CONSOLE}/include/consoleCommands/CheckedNumber.h"
LEVEL = f"{CONSOLE}/src/lifecycle/console/LifecycleControlCommand.cpp"

UTIL_SOURCES = [f"{UTIL}/src/util/{rel}" for rel in (
    "command/GroupCommand.cpp", "command/CommandContext.cpp", "string/ConstString.cpp",
    "format/SharedStringWriter.cpp", "format/StringWriter.cpp", "format/PrintfFormatter.cpp",
    "format/PrintfFormatScanner.cpp", "format/PrintfArgumentReader.cpp", "stream/NullOutputStream.cpp",
    "stream/SharedOutputStream.cpp", "stream/StringBufferOutputStream.cpp")]


def pwm_command(tree: Path, stubs: str, exe: Path) -> list[str]:
    main = tree / MAIN
    return [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
            "-fshort-enums", f"-I{main / 'test/host' / stubs}", f"-I{main / 'include'}",
            f"-isystem{UTIL / 'include'}", f"-isystem{OPENBSW / 'libs/3rdparty/etl/include'}",
            f"-isystem{OPENBSW / 'libs/bsp/bspOutputPwm/include'}",
            str(main / "src/systems/test/host/CheckedOutputPwmTesterHostTest.cpp"), str(tree / TESTER),
            str(OPENBSW / "libs/bsp/bspOutputPwm/src/outputPwm/OutputPwmTester.cpp"), *UTIL_SOURCES, "-o", str(exe)]


def number_command(tree: Path, exe: Path) -> list[str]:
    console = tree / CONSOLE
    return [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
            f"-I{console / 'test/host/lcStubs'}", f"-I{console / 'include'}",
            f"-isystem{UTIL / 'include'}", f"-isystem{OPENBSW / 'libs/bsw/lifecycle/include'}",
            f"-isystem{OPENBSW / 'libs/3rdparty/etl/include'}",
            str(console / "test/host/ConsoleNumberHostTest.cpp"), str(tree / LEVEL), *UTIL_SOURCES, "-o", str(exe)]


# (what, test, file, old, new): `old` must occur exactly once in `file`; test "pwm" runs both profiles
MUTANTS = [
    # SYS-162: console pwm
    ("the PWM groups of the safe outputs table are not refused", "pwm", TESTER,
     "if (all ? isSafetyPwmChannel(FIRST_GENERATED_CHANNEL) : isSafetyPwmChannel(channel))", "if (false)"),
    ("pwm set 0xFF (every channel) is not refused", "pwm", TESTER,
     "if (all ? isSafetyPwmChannel(FIRST_GENERATED_CHANNEL) : isSafetyPwmChannel(channel))",
     "if (!all && isSafetyPwmChannel(channel))"),
    ("pwm set 0xFF is refused without a PWM group as well", "pwm", TESTER,
     "if (all ? isSafetyPwmChannel(FIRST_GENERATED_CHANNEL) : isSafetyPwmChannel(channel))",
     "if (all || isSafetyPwmChannel(channel))"),
    ("pwm setPeriod is not refused", "pwm", TESTER,
     "if (all ? isSafetyPwmChannel(FIRST_GENERATED_CHANNEL) : isSafetyPwmChannel(channel))",
     "if ((idx == SET_COMMAND) && (all ? isSafetyPwmChannel(FIRST_GENERATED_CHANNEL) : isSafetyPwmChannel(channel)))"),
    ("the refusal keeps the result OK", "pwm", TESTER,
     "channel)));\n            (void)context.check(false, ::util::command::ICommand::Result::BAD_VALUE);\n",
     "channel)));\n"),
    ("the refusal passes the line on to OpenBSW", "pwm", TESTER,
     "            return;\n        }\n    }\n    ::bios::OutputPwmTester::executeCommand",
     "        }\n    }\n    ::bios::OutputPwmTester::executeCommand"),
    ("the first channel of the group is not refused", "pwm", TESTER,
     "(channel >= FIRST_GENERATED_CHANNEL)", "(channel > FIRST_GENERATED_CHANNEL)"),
    ("the static dummy channel is refused", "pwm", TESTER,
     "(channel >= FIRST_GENERATED_CHANNEL)", "(channel >= 0U)"),
    ("the last channel of the group is not refused", "pwm", TESTER,
     "(channel < END_OF_CHANNELS)", "(channel + 1U < END_OF_CHANNELS)"),
    ("the PWM groups are counted wrongly", "pwm", TESTER,
     "return (SAFE_PWM_GROUP_COUNT > 0) && (channel", "return (SAFE_PWM_GROUP_COUNT > 1) && (channel"),
    # SYS-163: the number without wrap-around
    ("no limit (wrap-around as before)", "number", NUMBER,
     "if (above || (d > max) || (number > ((max - d) / base)))", "if (false)"),
    ("the limit lets one more through", "number", NUMBER,
     "(number > ((max - d) / base))", "(number > ((max - d) / base) + 1U)"),
    ("a digit above the limit is accepted", "number", NUMBER,
     "if (above || (d > max) || (number > ((max - d) / base)))",
     "if (above || (number > ((max - d) / base)))"),
    ("a negative number is accepted", "number", NUMBER,
     "if (above || (negative && (number != 0U)))", "if (above)"),
    ("characters after the number are accepted", "number", NUMBER,
     "if ((pos == first) || (pos != length))", "if (pos == first)"),
    ("an empty number is accepted", "number", NUMBER,
     "if ((pos == first) || (pos != length))", "if (pos != length)"),
    ("no hex", "number", NUMBER,
     "        base = 16U;\n        pos  = 2U;", "        base = 10U;\n        pos  = 2U;"),
    ("no octal", "number", NUMBER,
     "base = 8U; // the leading 0", "base = 10U; // the leading 0"),
    ("the value is written although it is refused", "number", NUMBER,
     "    if (above || (negative && (number != 0U)))\n",
     "    value = number;\n    if (above || (negative && (number != 0U)))\n"),
    ("a refused number reports BAD_TOKEN instead of BAD_VALUE", "number", NUMBER,
     "return context.check(false, ::util::command::ICommand::Result::BAD_VALUE);",
     "return context.check(false, ::util::command::ICommand::Result::BAD_TOKEN);"),
    ("lc level without checkEol", "number", LEVEL,
     "\n                || !context.checkEol())", ")"),
    ("lc level with a wrong limit", "number", LEVEL,
     "scanUnsigned(context, _lifecycleManager.getLevelCount(), level)", "scanUnsigned(context, 255U, level)"),
    ("lc level ignores the result of the scan", "number", LEVEL,
     "            if (!::consolecommands::scanUnsigned(context, _lifecycleManager.getLevelCount(), level)\n"
     "                || !context.checkEol())\n            {\n                return;\n            }\n",
     "            (void)::consolecommands::scanUnsigned(context, _lifecycleManager.getLevelCount(), level);\n"),
]


def zig_available() -> bool:
    try:
        import ziglang  # noqa: F401
        return True
    except ImportError:
        return False


def build_and_run(cmd: list[str], exe: Path, werror: bool) -> tuple[str, str]:
    """('compile' | 'fail' | 'hang' | 'ok', output)."""
    if not werror:
        cmd = [a for a in cmd if a != "-Werror"]
    built = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=900)
    if built.returncode != 0:
        return "compile", built.stdout + built.stderr
    try:
        r = subprocess.run([str(exe)], cwd=ROOT, capture_output=True, text=True, timeout=RUN_LIMIT_S)
    except subprocess.TimeoutExpired:
        return "hang", ""
    return ("ok" if r.returncode == 0 else "fail"), r.stdout


def run_test(tree: Path, which: str, workdir: Path, name: str, werror: bool = True) -> tuple[str, str]:
    """Verdict of all runs of `which`: the first that does not pass, else ok."""
    if which == "pwm":
        runs = [(pwm_command(tree, stubs, workdir / f"{name}_{stubs}.exe"), workdir / f"{name}_{stubs}.exe")
                for stubs in ("pwmTesterStubs", "pwmTesterStubsNoGroup")]
    else:
        runs = [(number_command(tree, workdir / f"{name}.exe"), workdir / f"{name}.exe")]
    for cmd, exe in runs:
        verdict, output = build_and_run(cmd, exe, werror)
        if verdict != "ok":
            return verdict, output
    return "ok", ""


@unittest.skipUnless(zig_available() and (OPENBSW / "libs").is_dir(), "ziglang or the openbsw submodule missing")
class ConsoleGuardMutationsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="nx_consoleguard_"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def copy(self, name: str) -> Path:
        tree = self.tmp / name
        for rel in (MAIN, CONSOLE):
            shutil.copytree(ROOT / rel, tree / rel, ignore=shutil.ignore_patterns("__pycache__", "*.py", "linkerscript"))
        return tree

    def test_unmutated_passes(self):
        tree = self.copy("clean")
        for which in ("pwm", "number"):
            with self.subTest(test=which):
                verdict, output = run_test(tree, which, self.tmp, f"clean_{which}")
                self.assertEqual(verdict, "ok", output[-3000:])

    def test_mutants(self):
        for n, (what, which, rel, old, new) in enumerate(MUTANTS):
            with self.subTest(mutant=what):
                tree = self.copy(f"m{n}")
                path = tree / rel
                text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
                self.assertEqual(text.count(old), 1, f"mutation point of '{what}' not unique")
                path.write_text(text.replace(old, new), encoding="utf-8")
                verdict, output = run_test(tree, which, self.tmp, f"m{n}", werror=False)
                print(f"  {verdict:8} {what}")
                self.assertIn(verdict, ("fail", "hang"), f"mutant '{what}' survived ({verdict}): {output[-1500:]}")


if __name__ == "__main__":
    unittest.main()
