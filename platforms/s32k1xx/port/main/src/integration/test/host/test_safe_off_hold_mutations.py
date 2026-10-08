# SPDX-License-Identifier: Apache-2.0
"""SYS-150 / SYS-151: meaningfulness of the host tests safe-off-command-hold and safe-off-output-tester.

Both tests are compiled with zig c++ as their tools/test entries do and run. Each mutant is applied to a copy of the
family port directory platforms/s32k1xx/port/main (in the same place of a copied tree, so relative paths stay valid);
it counts as detected if the test it targets fails or hangs. A mutant that no longer compiles counts as survived: every
mutant is meant to compile and to be caught by the run.

Run: .venv/Scripts/python -m unittest platforms/s32k1xx/port/main/src/integration/test/host/test_safe_off_hold_mutations.py
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAIN = HERE.parents[3]  # platforms/s32k1xx/port/main
ROOT = HERE.parents[7]
OPENBSW = ROOT / "external/openbsw"
UTIL = OPENBSW / "libs/bsw/util"
RUN_LIMIT_S = 120

HOLD = "src/integration/SafeOffCommandHold.h"
PINS = "src/integration/SafeOffPins.h"
RECORD = "src/integration/SafeOffTakeBackRecord.h"   # SYS-196
BINDERS = "src/integration/ModuleBinders.h"
OUTPUTS = "include/integration/SafeOffOutputs.h"
TESTER = "src/systems/SafeOffOutputTester.cpp"
TESTER_H = "include/systems/SafeOffOutputTester.h"


def hold_command(main: Path, exe: Path) -> list[str]:
    return [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion",
            "-Wno-sign-conversion", "-Werror", f"-I{main / 'test/host/safeOffStubs'}", f"-I{main / 'src/integration'}",
            f"-I{main / 'include'}", f"-I{ROOT / 'platforms/s32k1xx/hardFaultHandler/include'}",
            str(main / "src/integration/test/host/SafeOffCommandHoldHostTest.cpp"), "-o", str(exe)]


def tester_command(main: Path, exe: Path) -> list[str]:
    util = [f"{UTIL}/src/util/{rel}" for rel in (
        "command/GroupCommand.cpp", "command/CommandContext.cpp", "string/ConstString.cpp",
        "format/SharedStringWriter.cpp", "format/StringWriter.cpp", "format/PrintfFormatter.cpp",
        "format/PrintfFormatScanner.cpp", "format/PrintfArgumentReader.cpp", "stream/NullOutputStream.cpp",
        "stream/SharedOutputStream.cpp", "stream/StringBufferOutputStream.cpp")]
    return [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
            "-fshort-enums", f"-I{main / 'test/host/safeOffStubs'}", f"-I{main / 'include'}",
            f"-isystem{UTIL / 'include'}", f"-isystem{OPENBSW / 'libs/3rdparty/etl/include'}",
            f"-isystem{OPENBSW / 'libs/bsp/bspOutputManager/include'}",
            str(main / "src/systems/test/host/SafeOffOutputTesterHostTest.cpp"), str(main / TESTER),
            str(OPENBSW / "libs/bsp/bspOutputManager/src/outputManager/OutputTester.cpp"), *util, "-o", str(exe)]


# (what, test, file, old, new): `old` must occur exactly once in `file`
MUTANTS = [
    # SYS-151: the hold
    ("the hold takes back a commanded level", "hold", HOLD,
     "if (_active(i) && !_commanded(i))", "if (_active(i))"),
    ("the hold never drives the output inactive", "hold", HOLD,
     "                    _switchOff(i);\n", ""),
    ("the hold works without the interrupt lock", "hold", HOLD,
     "                ::interrupts::SuspendResumeAllInterruptsScopedLock const lock;\n", ""),
    ("the hold checks only the first safe_off output", "hold", HOLD,
     "for (uint8_t i = 0U; i < _count; ++i)", "for (uint8_t i = 0U; i < 1U; ++i)"),
    # SYS-161: the count per safe_off output ("safeoff show")
    ("every take-back is counted for the first output", "hold", HOLD,
     "++_perOutput[i]; // SYS-161", "++_perOutput[0]; // SYS-161"),
    ("the take-backs are not counted per output", "hold", HOLD,
     "++_perOutput[i]; // SYS-161", "(void)_perOutput; // SYS-161"),
    ("the count per output is read from the wrong output", "hold", HOLD,
     "? _perOutput[index] : 0U;", "? _perOutput[_count - 1U - index] : 0U;"),
    ("the log is due in every poll", "hold", HOLD,
     "if ((_takeBacks == _loggedTakeBacks) || (_quietPolls < LOG_INTERVAL_POLLS))",
     "if (_takeBacks == _loggedTakeBacks)"),
    # SYS-194 (safety review BL38-P1, SYS147-B1 b): every take-back reported to the safe supervisor
    ("the take-backs are never reported", "hold", HOLD,
     "        reportDue(); // SYS-194", "        // SYS-194"),
    ("the report is due in every poll (no rate)", "hold", HOLD,
     "if ((_report == nullptr) || (_takeBacks == _reportedTotal) || (_reportQuietPolls < REPORT_INTERVAL_POLLS))",
     "if ((_report == nullptr) || (_takeBacks == _reportedTotal))"),
    ("only the first take-back is ever reported", "hold", HOLD,
     "            _reportQuietPolls = 0U;", "            _reportQuietPolls = 0U;\n            _report = nullptr;"),
    ("the first take-back waits for the interval", "hold", HOLD,
     "uint32_t _reportQuietPolls = REPORT_INTERVAL_POLLS;", "uint32_t _reportQuietPolls = 0U;"),
    ("a refused report counts as reported", "hold", HOLD,
     "if ((due != 0U) && _report(out))", "if ((due != 0U) && (_report(out) || true))"),
    ("a report names the wrong output", "hold", HOLD,
     "if ((due != 0U) && _report(out))", "if ((due != 0U) && _report(static_cast<uint8_t>(_count - 1U - out)))"),
    ("the reported count is not kept per output", "hold", HOLD,
     "                    _reported[out] = _perOutput[out];\n", ""),
    ("the report runs inside the interrupt lock", "hold", HOLD,
     "                    _switchOff(i);\n                    tookBack = true;\n",
     "                    _switchOff(i);\n                    tookBack = true;\n                    reportDue();\n"),
    ("without the arrays the report names the first output", "hold", HOLD,
     "else if (_report(_lastIndex))", "else if (_report(0U))"),
    # SYS-196 (decision SYS194-4 b): the record of the take-backs for the DTC of the fault memory
    ("a refused report repeated by the hold counts again", "hold", RECORD,
     "        if (now == _recorded[index])\n        {\n            return;",
     "        if (false)\n        {\n            return;"),
    ("an occurrence is never handed on", "hold", RECORD,
     "            _occurred();", "            (void)_occurred;"),
    ("the snapshot keeps the first output", "hold", RECORD,
     "        _lastOutput      = output;", "        _lastOutput      = (_occurrences == 0U) ? output : _lastOutput;"),
    ("the snapshot count is the one of the other output", "hold", RECORD,
     "? _takeBacks[_lastIndex] : 0U;", "? _takeBacks[_count - 1U - _lastIndex] : 0U;"),
    ("the recorded count is not kept", "hold", RECORD,
     "        _recorded[index] = now;\n", ""),
    # SYS-151: the pins from the generated table
    ("the pin level ignores the clock gate", "hold", PINS,
     "    if (!clocked(level.gateAddress, level.gateMask))\n    {\n        return false;\n    }\n", ""),
    ("the pin level reads the inactive level as active", "hold", PINS,
     "return (value & level.mask) != level.inactive;", "return (value & level.mask) == level.inactive;"),
    ("the store writes nothing", "hold", PINS,
     "*NX_SAFE_OUTPUT_REG(store.address) = store.value;", "*NX_SAFE_OUTPUT_REG(store.address) = 0U;"),
    ("the safe_off index is taken as table index (PWM group first)", "hold", PINS,
     "return static_cast<uint32_t>(::integration::safeoff::FIRST) + index;", "return index;"),
    # SYS-151: the commanded level and the write path
    ("the pin is written before the commanded level is recorded", "hold", OUTPUTS,
     "    Commanded::note(static_cast<uint32_t>(output), on);\n"
     "    return ::bios::Output::set(output, on ? 1U : 0U) == ::bsp::BSP_OK;\n",
     "    bool const ok = ::bios::Output::set(output, on ? 1U : 0U) == ::bsp::BSP_OK;\n"
     "    Commanded::note(static_cast<uint32_t>(output), on);\n    return ok;\n"),
    ("every output records into the first slot", "hold", OUTPUTS,
     "_active[static_cast<uint32_t>(index)] = on ? 1U : 0U;", "_active[0U] = on ? 1U : 0U;"),
    ("the binder writes the pin without the commanded level", "hold", BINDERS,
     "return ::integration::safeoff::setCommandedOutput(output, on);",
     "return ::bios::Output::set(output, on ? 1U : 0U) == ::bsp::BSP_OK;"),
    # (searching from entry 0 alone is an equivalent mutant: entry - FIRST of the PWM group is negative, so a PWM group
    # that shares its name with an output still does not count - testList checks FAN)
    ("the safe_off index is the table index", "hold", OUTPUTS,
     "            return entry - FIRST;\n", "            return entry;\n"),
    ("a PWM group that shares its name with an output counts as safe_off", "hold", OUTPUTS,
     "constexpr bool isSafeOff(uint32_t const outputId) { return indexOf(outputId) >= 0; }",
     "constexpr bool isSafeOff(uint32_t const outputId)\n{\n    return (indexOf(outputId) >= 0)\n"
     "           || (static_cast<uint32_t>(detail::Names::Ids::TABLE[0]) == outputId);\n}"),
    ("the safe_off list ends one entry early", "hold", OUTPUTS,
     "constexpr int32_t indexOf(uint32_t const outputId)\n{\n"
     "    for (int32_t entry = FIRST; entry < static_cast<int32_t>(NX_SAFE_OUTPUT_COUNT); ++entry)",
     "constexpr int32_t indexOf(uint32_t const outputId)\n{\n"
     "    for (int32_t entry = FIRST; entry < static_cast<int32_t>(NX_SAFE_OUTPUT_COUNT) - 1; ++entry)"),
    # SYS-150: the console wrapper
    ("out set of a safe_off output is passed on", "tester", TESTER,
     "if ((idx == SET_COMMAND) && ::integration::safeoff::isSafeOff(outputNo))", "if (false)"),
    ("only out set <id> 1 is refused", "tester", TESTER,
     "if ((idx == SET_COMMAND) && ::integration::safeoff::isSafeOff(outputNo))",
     "if ((idx == SET_COMMAND) && ::integration::safeoff::isSafeOff(outputNo) && (peek.scanIntToken<uint32_t>() != 0U))"),
    ("the refusal keeps the result OK", "tester", TESTER,
     "                    ::bios::Output::getName(static_cast<uint16_t>(outputNo)));\n"
     "                (void)context.check(false, ::util::command::ICommand::Result::BAD_VALUE);\n",
     "                    ::bios::Output::getName(static_cast<uint16_t>(outputNo)));\n"),
    ("the id is checked only after the one-byte cast (256 + n aliases)", "tester", TESTER,
     "if (outputNo >= static_cast<uint32_t>(::bios::Output::TOTAL_NUMBER_OF_OUTPUTS))",
     "if (static_cast<::bios::Output::OutputId>(outputNo) >= ::bios::Output::TOTAL_NUMBER_OF_OUTPUTS)"),
    ("the id is read with a decimal-only scanner", "tester", TESTER,
     "uint32_t const outputNo = peek.scanIntToken<uint32_t>();",
     "uint32_t outputNo = 0U;\n"
     "        ::util::string::ConstString const token = peek.scanToken();\n"
     "        for (size_t k = 0U; (k < token.length()) && (token[k] >= '0') && (token[k] <= '9'); ++k)\n"
     "        {\n            outputNo = (outputNo * 10U) + static_cast<uint32_t>(token[k] - '0');\n        }"),
    ("the wrapper consumes the id it peeked at", "tester", TESTER,
     "        ::util::command::CommandContext peek(context.getSuffix());\n",
     "        ::util::command::CommandContext& peek = context;\n"),
    ("the wrapper looks at the get command id for set", "tester", TESTER_H,
     "static constexpr uint8_t SET_COMMAND = 2U;", "static constexpr uint8_t SET_COMMAND = 3U;"),
]


def zig_available() -> bool:
    try:
        import ziglang  # noqa: F401
        return True
    except ImportError:
        return False


def build_and_run(main: Path, which: str, workdir: Path, name: str, werror: bool = True) -> tuple[str, str]:
    """('compile' | 'fail' | 'hang' | 'ok', output). Mutants build without -Werror (a field a mutant no longer
    uses is a warning, not a detection)."""
    exe = workdir / f"{name}.exe"
    cmd = hold_command(main, exe) if which == "hold" else tester_command(main, exe)
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


@unittest.skipUnless(zig_available() and (OPENBSW / "libs").is_dir(), "ziglang or the openbsw submodule missing")
class SafeOffHoldMutationsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="nx_safeoff_"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def copy(self, name: str) -> Path:
        tree = self.tmp / name
        target = tree / MAIN.relative_to(ROOT)
        shutil.copytree(MAIN, target, ignore=shutil.ignore_patterns("__pycache__", "*.py", "linkerscript"))
        return target

    def test_unmutated_passes(self):
        main = self.copy("clean")
        for which in ("hold", "tester"):
            with self.subTest(test=which):
                verdict, output = build_and_run(main, which, self.tmp, f"clean_{which}")
                self.assertEqual(verdict, "ok", output[-3000:])

    def test_mutants(self):
        for n, (what, which, rel, old, new) in enumerate(MUTANTS):
            with self.subTest(mutant=what):
                main = self.copy(f"m{n}")
                path = main / rel
                text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
                self.assertEqual(text.count(old), 1, f"mutation point of '{what}' not unique")
                path.write_text(text.replace(old, new), encoding="utf-8")
                verdict, output = build_and_run(main, which, self.tmp, f"m{n}", werror=False)
                print(f"  {verdict:8} {what}")
                self.assertIn(verdict, ("fail", "hang"),
                              f"mutant '{what}' survived ({verdict}): {output[-1500:]}")


if __name__ == "__main__":
    unittest.main()
