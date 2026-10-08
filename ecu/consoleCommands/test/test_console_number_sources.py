# SPDX-License-Identifier: Apache-2.0
"""SYS-163 (safety review group A 2026-10-01, SR-110-2 a): the number arguments of "lc level" and "dem setting" in the
sources.

"lc level" is run by the host test console-number (ConsoleNumberHostTest.cpp). "dem setting" sits in
ecu/application/src/systems/RteSystem.cpp next to the whole RTE and fault memory of the project, which a host test
cannot build on its own; this test checks its source instead:

  N1  the case of the command reads its number with consolecommands::scanUnsigned and the limit of the command
      (dem setting: 1U; lc level: the level count of the lifecycle manager), returns when it fails or when the line
      does not end there (checkEol), and only after both acts (setDtcSetting / transitionToLevel);
  N2  no scanIntToken in these cases;
  N3  no scanIntToken with a type narrower than 32 bit in ecu/consoleCommands and ecu/application/src/systems (such a
      type wraps 256 to 0 without any error - the finding of PLAT-110).

Built-in faults (in memory) must be found: no checkEol, the old uint8_t scanner, the action before the check, a wrong
limit, a narrow scanIntToken elsewhere. Comments and string literals do not count.

Run: .venv/Scripts/python -m unittest ecu/consoleCommands/test/test_console_number_sources.py
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RTE_SYSTEM = "ecu/application/src/systems/RteSystem.cpp"
LIFECYCLE = "ecu/consoleCommands/src/lifecycle/console/LifecycleControlCommand.cpp"
SCANNED_DIRS = ("ecu/consoleCommands", "ecu/application/src/systems")

# (file, case label, limit argument, action)
COMMANDS = (
    (RTE_SYSTEM, "DEM_ID_SETTING", "1U", "setDtcSetting("),
    (LIFECYCLE, "GO_TO_LEVEL", "_lifecycleManager.getLevelCount()", "transitionToLevel("),
)

NARROW = re.compile(r"scanIntToken\s*<\s*(?:::)?(?:std::)?(u?int(?:8|16)_t|(?:unsigned\s+)?(?:char|short)|bool)\s*>")


def strip(code: str) -> str:
    """C++ source without comments and string/char literals (newlines kept)."""
    out, i, n = [], 0, len(code)
    while i < n:
        c = code[i]
        if code.startswith("//", i):
            j = code.find("\n", i)
            i = n if j < 0 else j
        elif code.startswith("/*", i):
            j = code.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("\n" * code.count("\n", i, j))
            i = j
        elif c in "\"'":
            j = i + 1
            while j < n and code[j] != c:
                j += 2 if code[j] == "\\" else 1
            out.append(c + c)
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def case_body(code: str, label: str) -> str | None:
    """Text of `case <label>:` up to the next case/default label of the same switch (or its end)."""
    m = re.search(rf"\bcase\s+{label}\s*:", code)
    if m is None:
        return None
    end = re.compile(r"\b(?:case\s+\w+|default)\s*:")
    nxt = end.search(code, m.end())
    return code[m.end():nxt.start() if nxt else len(code)]


def check_command(code: str, label: str, limit: str, action: str) -> list[str]:
    body = case_body(strip(code), label)
    if body is None:
        return [f"case {label}: not found"]
    errors = []
    flat = re.sub(r"\s+", " ", body)
    scan = re.search(r"::consolecommands::scanUnsigned\( ?context, ?" + re.escape(limit) + r", ?\w+ ?\)", flat)
    eol = flat.find("context.checkEol()")
    act = flat.find(action)
    ret = flat.find("return;")
    if scan is None:
        errors.append(f"case {label}: number not read with consolecommands::scanUnsigned(context, {limit}, ...)")
    if eol < 0:
        errors.append(f"case {label}: end of line not checked (checkEol)")
    if act < 0:
        errors.append(f"case {label}: {action} not found")
    if scan is not None and eol >= 0 and act >= 0:
        cond = re.search(r"if \( ?!" + re.escape(scan.group(0)) + r" ?\|\| ?!context\.checkEol\(\) ?\) ?\{ ?return; ?\}",
                         flat)
        if cond is None or not (cond.end() <= act):
            errors.append(f"case {label}: {action} not behind 'if (!scanUnsigned(...) || !context.checkEol()) "
                          "{ return; }'")
    if ret < 0:
        errors.append(f"case {label}: no return without action")
    if "scanIntToken" in body:
        errors.append(f"case {label}: scanIntToken (wraps around without error)")
    return errors


def check_narrow(files: dict[str, str]) -> list[str]:
    errors = []
    for rel, code in sorted(files.items()):
        for number, line in enumerate(strip(code).splitlines(), 1):
            m = NARROW.search(line)
            if m:
                errors.append(f"{rel}:{number}: scanIntToken<{m.group(1)}> wraps around (use consolecommands::"
                              "scanUnsigned)")
    return errors


def source_files() -> dict[str, str]:
    files = {}
    for d in SCANNED_DIRS:
        for path in sorted((ROOT / d).rglob("*")):
            if path.suffix in (".cpp", ".h") and "test" not in path.relative_to(ROOT / d).parts:
                files[path.relative_to(ROOT).as_posix()] = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return files


class ConsoleNumberSourcesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = source_files()

    def test_commands(self):
        for rel, label, limit, action in COMMANDS:
            with self.subTest(command=label):
                self.assertIn(rel, self.files)
                self.assertEqual(check_command(self.files[rel], label, limit, action), [])

    def test_no_narrow_scanner(self):
        self.assertGreater(len(self.files), 5, "no sources found (fault of this test)")
        self.assertEqual(check_narrow(self.files), [])

    def test_comments_and_strings_do_not_count(self):
        code = '// scanIntToken<uint8_t>()\n/* scanIntToken<uint16_t> */\nchar const* t = "scanIntToken<uint8_t>";\n'
        self.assertEqual(check_narrow({"x.cpp": code}), [])

    def test_built_in_faults_are_detected(self):
        rte = self.files[RTE_SYSTEM]
        lc = self.files[LIFECYCLE]
        mutants = {
            "dem setting without checkEol": (RTE_SYSTEM, rte, "DEM_ID_SETTING", "1U", "setDtcSetting(",
                                             "if (!::consolecommands::scanUnsigned(context, 1U, on) || !context.checkEol())",
                                             "if (!::consolecommands::scanUnsigned(context, 1U, on))"),
            "dem setting with limit 255": (RTE_SYSTEM, rte, "DEM_ID_SETTING", "1U", "setDtcSetting(",
                                           "scanUnsigned(context, 1U, on)", "scanUnsigned(context, 255U, on)"),
            "dem setting with the old uint8_t scanner": (
                RTE_SYSTEM, rte, "DEM_ID_SETTING", "1U", "setDtcSetting(",
                "uint32_t on = 0U;\n            if (!::consolecommands::scanUnsigned(context, 1U, on) || "
                "!context.checkEol())",
                "uint8_t const on = context.scanIntToken<uint8_t>();\n            if (!context.checkEol() || "
                "!context.check(on <= 1U))"),
            "dem setting acts before the check": (
                RTE_SYSTEM, rte, "DEM_ID_SETTING", "1U", "setDtcSetting(",
                "            uint32_t on = 0U;\n",
                "            uint32_t on = 0U;\n            memory.setDtcSetting(false);\n"),
            "lc level without checkEol": (LIFECYCLE, lc, "GO_TO_LEVEL", "_lifecycleManager.getLevelCount()",
                                          "transitionToLevel(", "\n                || !context.checkEol())", ")"),
            "lc level with the old scanner": (
                LIFECYCLE, lc, "GO_TO_LEVEL", "_lifecycleManager.getLevelCount()", "transitionToLevel(",
                "            uint32_t level = 0U;\n",
                "            uint32_t level = context.scanIntToken<uint8_t>();\n"),
        }
        for name, (rel, text, label, limit, action, old, new) in mutants.items():
            with self.subTest(mutant=name):
                self.assertEqual(text.count(old), 1, f"mutant '{name}' does not apply (fault of this test)")
                mutated = text.replace(old, new)
                files = dict(self.files)
                files[rel] = mutated
                found = check_command(mutated, label, limit, action) + check_narrow(files)
                self.assertNotEqual(found, [], name)
        narrow = dict(self.files)
        narrow["ecu/application/src/systems/NewCommand.cpp"] = "uint16_t const n = context.scanIntToken<uint16_t>();\n"
        self.assertNotEqual(check_narrow(narrow), [], "narrow scanner in a new file")


if __name__ == "__main__":
    unittest.main()
