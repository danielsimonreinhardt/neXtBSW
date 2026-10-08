"""SYS-161 (safety review 2026-10-01, SR-151-1 b): in the family port no write of a digital output past
integration::safeoff::setCommandedOutput() - the only way a safe_off output may be written.

Background: SYS-151 holds every safe_off output (module.yaml connect.<name>.safe_off) on the level its driver
commands. It knows the commanded level only because the drivers write through setCommandedOutput()
(include/integration/SafeOffOutputs.h: the level is recorded first, then the pin). A direct bios::Output::set() on
such an output would be a write the hold takes back as "foreign" within 1 ms - or, worse, a driver path that skips
the record and whose switch-on the hold keeps fighting. SYS-150 closed the console path (`out set`, wrapped by
SafeOffOutputTester). This source test keeps both closed for every file of the family port, so that a new write
path cannot slip in unnoticed.

The rules (sources of platforms/s32k1xx without test folders and 3rdparty; comments and string literals ignored):

  W1  bios::Output::set/invert/release/init/setDynamicClient/clrDynamicClient (also as a function pointer) only in
      the body of setCommandedOutput() (SafeOffOutputs.h), and there only after Commanded::note(). Exception with
      reason (EXCEPTIONS): CellMonitorSystem.h, RESET of the MC33771C - no safe_off connection in its manifest
      (libs/bsw/bccMc33771/module.yaml[.pending]); the exception expires (test fails) as soon as the manifest
      declares any output safe_off.
  W2  no alias that would hide such a call: `using X = bios::Output`, `using bios::Output`, a namespace alias of
      bios, and no class derived from bios::Output except the name lookup helper of SafeOffOutputs.h (whose body
      calls nothing).
  W3  no bios::Io::setPin() in the family port (the pin write underneath the output manager).
  W4  bios::OutputTester only as the base of SafeOffOutputTester (its header include, the base class, the call of
      the base's executeCommand); the console group "out" of BspSystem is a SafeOffOutputTester.
  W5  the write paths of the drivers do use setCommandedOutput(): the binder of the generated module binding
      (FamilyBinders::set, ModuleBinders.h) and DRV_EN of the TMC6200 (BiosTmc6200Io::setDriverEnable). Every module
      manifest with a safe_off output either has a `binding:` (so it writes through FamilyBinders::set) or is listed
      in HAND_WRITTEN with its write path - a new safe_off module without either fails here.

Counter-check (Gegenprobe): BuiltInFaultTest puts each of the faults this test is meant to catch into a copy of the
sources in memory and expects a finding (old direct DRV_EN write, plain OutputTester in BspSystem, alias, Io::setPin,
second set in SafeOffOutputs.h, record after the pin, binder past the record, expired CellMonitor exception, new
safe_off module without write path, function pointer); comments with the forbidden calls stay without finding.

Run: .venv/Scripts/python platforms/s32k1xx/port/main/test/test_safe_off_write_paths.py
"""

from __future__ import annotations

import functools
import re
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[4]
FAMILY = REPO / "platforms/s32k1xx"
PORT = "platforms/s32k1xx/port/main"

SAFE_OFF_OUTPUTS_H = f"{PORT}/include/integration/SafeOffOutputs.h"
MODULE_BINDERS_H = f"{PORT}/src/integration/ModuleBinders.h"
GATE_DRIVER_H = f"{PORT}/include/systems/GateDriverSystem.h"
TESTER_H = f"{PORT}/include/systems/SafeOffOutputTester.h"
TESTER_CPP = f"{PORT}/src/systems/SafeOffOutputTester.cpp"
BSP_SYSTEM_H = f"{PORT}/include/systems/BspSystem.h"
BSP_SYSTEM_CPP = f"{PORT}/src/systems/BspSystem.cpp"
CELL_MONITOR_H = f"{PORT}/include/systems/CellMonitorSystem.h"
# PLAT-62: the NDA cell monitor (libs/bsw/bccMc33771, CellMonitorSystem.*) is not in the exported open tree - there the
# excepted write site and its manifest are missing, and the checks about them are skipped
NDA_CELL_MONITOR = (REPO / CELL_MONITOR_H).is_file()
nda_cell_monitor = unittest.skipUnless(
    NDA_CELL_MONITOR, "NDA cell monitor not in this tree (exported open tree, PLAT-62)")

# W1 exceptions: file -> (function the write is in, module whose manifest must not declare safe_off, reason)
EXCEPTIONS = {
    CELL_MONITOR_H: (
        "setReset",
        "bccMc33771",
        "RESET of the MC33771C cell controller: no safe_off connection in its manifest, the reset pin carries no "
        "load and is no output of the safe outputs table - not held by SYS-151, so no commanded level to record",
    ),
}

# W5: modules with a safe_off output and no generated binding -> (file, function of the write path)
HAND_WRITTEN = {
    "gdTmc6200": (GATE_DRIVER_H, "setDriverEnable"),
}

WRITE_CALL = re.compile(r"\bOutput\s*::\s*(set|invert|release|init|setDynamicClient|clrDynamicClient)\b")
ALIAS = re.compile(
    r"\busing\s+\w+\s*=\s*(?:::)?\s*bios\s*::\s*Output\s*;"
    r"|\busing\s+(?:::)?\s*bios\s*::\s*Output\s*;"
    r"|\busing\s+namespace\s+(?:::)?\s*bios\s*;"
    r"|\bnamespace\s+\w+\s*=\s*(?:::)?\s*bios\s*;")
DERIVED = re.compile(r"(?:struct|class)\s+(\w+)\s*(?:final\s*)?:\s*(?:public\s+|private\s+|protected\s+)?"
                     r"(?:::)?\s*bios\s*::\s*Output\b(?!\w)")
IO_SET_PIN = re.compile(r"\bIo\s*::\s*setPin\b")
OUTPUT_TESTER = re.compile(r"\bOutputTester\b")
ALLOWED_TESTER_LINES = {
    (None, '#include "outputManager/OutputTester.h"'),
    (TESTER_H, "class SafeOffOutputTester : public ::bios::OutputTester"),
    (TESTER_CPP, "::bios::OutputTester::executeCommand(context, idx);"),
}


@functools.lru_cache(maxsize=None)
def strip(text: str) -> str:
    """Comments and string/char literals blanked, line structure kept (an #include keeps its file name)."""

    def blank(m: re.Match) -> str:
        token = m.group(0)
        if token.startswith("//"):
            return ""
        if token.startswith("/*"):
            return "\n" * token.count("\n")
        line_start = text.rfind("\n", 0, m.start()) + 1
        if text[line_start:m.start()].strip().startswith("#include"):
            return token
        return token[0] + " " * max(0, len(token) - 2) + token[0]

    return LEXEMES.sub(blank, text)


LEXEMES = re.compile(r"//[^\n]*|/\*.*?(?:\*/|\Z)|\"(?:\\.|[^\"\\\n])*\"?|'(?:\\.|[^'\\\n])*'?", re.S)


def block_end(text: str, start: int) -> int:
    """Offset after the brace block that opens at text[start] == '{' (end of text if unbalanced)."""
    depth = 0
    for j in range(start, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return j + 1
    return len(text)


def function_body(text: str, name: str) -> tuple[int, int] | None:
    """(start, end) offsets of the body of the first function definition `name(...) ... { ... }` in stripped text."""
    for m in re.finditer(rf"\b{re.escape(name)}\s*\(", text):
        depth, i = 0, m.end() - 1
        while i < len(text):  # skip the parameter list
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        rest = text[i + 1:]
        head = re.match(r"[^;{}]*\{", rest)
        if not head:
            continue  # a call or a declaration
        start = i + 1 + head.end() - 1
        return start, block_end(text, start)
    return None


def line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def scanned_sources() -> dict[str, str]:
    files = []
    for p in FAMILY.rglob("*"):
        if p.suffix not in (".h", ".hpp", ".cpp", ".c", ".inc") or not p.is_file():
            continue
        parts = p.relative_to(REPO).parts
        if "test" in parts or "3rdparty" in parts:
            continue
        files.append(p)
    return {p.relative_to(REPO).as_posix(): p.read_text(encoding="utf-8", errors="replace") for p in sorted(files)}


def module_manifests() -> dict[str, dict]:
    """module name -> manifest of every libs/**/module.yaml and parked module.yaml.pending."""
    found = {}
    for pattern in ("module.yaml", "module.yaml.pending"):
        for p in sorted((REPO / "libs").rglob(pattern)):
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            if isinstance(data, dict) and "module" in data:
                found[str(data["module"])] = data
    return found


def safe_off_connections(manifest: dict) -> list[str]:
    connect = manifest.get("connect") or {}
    return sorted(name for name, c in connect.items() if isinstance(c, dict) and c.get("safe_off"))


def violations(sources: dict[str, str], manifests: dict[str, dict]) -> list[str]:
    found: list[str] = []
    stripped = {name: strip(text) for name, text in sources.items()}

    for name, text in stripped.items():
        # W1: write calls of the output manager
        for m in WRITE_CALL.finditer(text):
            where = f"{name}:{line_of(text, m.start())}"
            if name == SAFE_OFF_OUTPUTS_H:
                body = function_body(text, "setCommandedOutput")
                if body is None or not (body[0] <= m.start() < body[1]):
                    found.append(f"{where}: W1 bios::Output::{m.group(1)} outside setCommandedOutput()")
                    continue
                note = text.find("Commanded::note(", body[0], body[1])
                if note < 0 or note > m.start():
                    found.append(f"{where}: W1 setCommandedOutput() writes the pin before (or without) "
                                 f"Commanded::note() - the hold would take the driver's own level back")
                continue
            exception = EXCEPTIONS.get(name)
            if exception is not None:
                function, module, _reason = exception
                body = function_body(text, function)
                manifest = manifests.get(module)
                if manifest is None:
                    found.append(f"{where}: W1 exception for {function}() names module {module}, whose manifest "
                                 f"is gone - check the exception")
                elif safe_off_connections(manifest):
                    found.append(f"{where}: W1 exception expired - module {module} now declares "
                                 f"{', '.join(safe_off_connections(manifest))} safe_off: write it through "
                                 f"integration::safeoff::setCommandedOutput()")
                elif body is None or not (body[0] <= m.start() < body[1]) or m.group(1) != "set":
                    found.append(f"{where}: W1 bios::Output::{m.group(1)} outside the excepted {function}()")
                continue
            found.append(f"{where}: W1 direct bios::Output::{m.group(1)} - write digital outputs through "
                         f"integration::safeoff::setCommandedOutput() (SYS-151/SYS-161)")
        # W2: aliases and derived classes
        for m in ALIAS.finditer(text):
            found.append(f"{name}:{line_of(text, m.start())}: W2 alias of bios::Output/bios hides output writes")
        for m in DERIVED.finditer(text):
            where = f"{name}:{line_of(text, m.start())}"
            if name != SAFE_OFF_OUTPUTS_H or m.group(1) != "Ids":
                found.append(f"{where}: W2 class {m.group(1)} derived from bios::Output (unqualified set() inside)")
                continue
            start = text.find("{", m.end())
            inner = text[start:block_end(text, start)] if start >= 0 else ""
            if re.search(r"\b(set|invert|release|init)\s*\(", inner):
                found.append(f"{where}: W2 the name lookup helper Ids calls an output function")
        # W3: pin writes underneath the output manager
        for m in IO_SET_PIN.finditer(text):
            found.append(f"{name}:{line_of(text, m.start())}: W3 bios::Io::setPin in the family port")
        # W4: the console tester
        for i, line in enumerate(text.splitlines(), 1):
            if OUTPUT_TESTER.search(line):
                s = " ".join(line.split())
                if (None, s) in ALLOWED_TESTER_LINES or (name, s) in ALLOWED_TESTER_LINES:
                    continue
                found.append(f"{name}:{i}: W4 bios::OutputTester used directly - the console group 'out' must be "
                             f"SafeOffOutputTester (SYS-150)")

    # W4: BspSystem registers the wrapper
    bsp_h, bsp_cpp = stripped.get(BSP_SYSTEM_H, ""), stripped.get(BSP_SYSTEM_CPP, "")
    if not re.search(r"^\s*SafeOffOutputTester\s+_outputTester\s*;", bsp_h, re.M):
        found.append(f"{BSP_SYSTEM_H}: W4 the member _outputTester is no SafeOffOutputTester")
    if not re.search(r"_asyncOutputTester\s*\(\s*_outputTester\s*,", bsp_cpp):
        found.append(f"{BSP_SYSTEM_CPP}: W4 _asyncOutputTester does not register _outputTester")

    # W5: the drivers' write paths
    def uses_commanded(file: str, function: str) -> bool:
        text = stripped.get(file, "")
        body = function_body(text, function)
        return body is not None and re.search(r"\bsetCommandedOutput\s*\(", text[body[0]:body[1]]) is not None

    if not uses_commanded(MODULE_BINDERS_H, "set"):
        found.append(f"{MODULE_BINDERS_H}: W5 FamilyBinders::set does not write through setCommandedOutput()")
    for module, manifest in sorted(manifests.items()):
        outputs = safe_off_connections(manifest)
        if not outputs or "binding" in manifest:
            continue
        path = HAND_WRITTEN.get(module)
        if path is None:
            found.append(f"libs/**/{module}/module.yaml: W5 safe_off output(s) {', '.join(outputs)} without a "
                         f"generated binding and without an entry in HAND_WRITTEN - name the write path and make it "
                         f"use setCommandedOutput()")
        elif not uses_commanded(*path):
            found.append(f"{path[0]}: W5 {path[1]}() of {module} does not write through setCommandedOutput()")
    return found


class WritePathTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources = scanned_sources()
        cls.manifests = module_manifests()

    def test_family_port_has_no_write_past_the_commanded_level(self):
        self.assertEqual(violations(self.sources, self.manifests), [])

    def test_scan_is_not_blind(self):
        # the scan sees the family port at all (a moved directory must not make the check pass empty)
        self.assertGreater(len(self.sources), 100)
        cell_monitor = (CELL_MONITOR_H,) if NDA_CELL_MONITOR else ()
        for name in (SAFE_OFF_OUTPUTS_H, MODULE_BINDERS_H, GATE_DRIVER_H, TESTER_H, TESTER_CPP, BSP_SYSTEM_H,
                     BSP_SYSTEM_CPP, *cell_monitor):
            self.assertIn(name, self.sources)
        # exactly the known write sites exist today: one in setCommandedOutput, one excepted
        hits = {name for name, text in self.sources.items() if WRITE_CALL.search(strip(text))}
        self.assertEqual(hits, {SAFE_OFF_OUTPUTS_H, *cell_monitor})
        # the manifests are read: the two safe_off modules of today
        with_safe_off = {m for m, data in self.manifests.items() if safe_off_connections(data)}
        self.assertEqual(with_safe_off, {"gdTmc6200", "highSideSwitch"})
        if NDA_CELL_MONITOR:
            self.assertIn("bccMc33771", self.manifests)
        self.assertIn("binding", self.manifests["highSideSwitch"])

    def test_strip_keeps_lines_and_includes(self):
        text = 'a; // Output::set(x)\n/* Output::set(\n */ b("Output::set(");\n#include "outputManager/OutputTester.h"\n'
        s = strip(text)
        self.assertEqual(s.count("\n"), text.count("\n"))
        self.assertIsNone(WRITE_CALL.search(s))
        self.assertIn('#include "outputManager/OutputTester.h"', s)


class BuiltInFaultTest(unittest.TestCase):
    """Gegenprobe: every fault the check is meant to catch, put into a copy of the sources in memory."""

    @classmethod
    def setUpClass(cls):
        cls.sources = scanned_sources()
        cls.manifests = module_manifests()

    def mutate(self, file: str, old: str, new: str, sources=None) -> list[str]:
        sources = dict(self.sources if sources is None else sources)
        self.assertIn(old, sources[file], f"{file}: the fault's anchor is gone - update the test")
        sources[file] = sources[file].replace(old, new, 1)
        return violations(sources, self.manifests)

    def assertFinds(self, found: list[str], rule: str):
        self.assertTrue(any(rule in f for f in found), f"{rule} not found in {found}")

    def test_drv_en_written_directly_again(self):
        # the TMC6200 DRV_EN as it was before SYS-151
        found = self.mutate(
            GATE_DRIVER_H,
            "return ::integration::safeoff::setCommandedOutput(_drvEn, on);",
            "return ::bios::Output::set(_drvEn, on ? 1U : 0U) == ::bsp::BSP_OK;")
        self.assertFinds(found, "W1 direct bios::Output::set")
        self.assertFinds(found, "W5 setDriverEnable() of gdTmc6200")

    def test_binder_past_the_record(self):
        found = self.mutate(
            MODULE_BINDERS_H,
            "return ::integration::safeoff::setCommandedOutput(output, on);",
            "return ::bios::Output::set(output, on ? 1U : 0U) == ::bsp::BSP_OK;")
        self.assertFinds(found, "W1 direct bios::Output::set")
        self.assertFinds(found, "W5 FamilyBinders::set")

    def test_plain_output_tester_in_bsp_system(self):
        found = self.mutate(BSP_SYSTEM_H, "SafeOffOutputTester _outputTester;", "bios::OutputTester _outputTester;")
        self.assertFinds(found, "W4 bios::OutputTester used directly")
        self.assertFinds(found, "W4 the member _outputTester")

    def test_output_tester_in_another_system(self):
        found = self.mutate(GATE_DRIVER_H, "namespace systems\n{", "namespace systems\n{\n::bios::OutputTester t;")
        self.assertFinds(found, "W4 bios::OutputTester used directly")

    def test_alias_and_derived_class(self):
        found = self.mutate(GATE_DRIVER_H, "namespace systems\n{",
                            "namespace systems\n{\nusing Out = ::bios::Output;\ninline void f() { Out::set(Out::LED1, 1U); }")
        self.assertFinds(found, "W2 alias")
        found = self.mutate(GATE_DRIVER_H, "namespace systems\n{",
                            "namespace systems\n{\nstruct Sneaky : ::bios::Output { void f() { set(LED1, 1U); } };")
        self.assertFinds(found, "W2 class Sneaky derived from bios::Output")
        found = self.mutate(SAFE_OFF_OUTPUTS_H, "static constexpr int32_t TABLE",
                            "static void f() { (void)set(static_cast<OutputId>(0), 1U); }\n        static constexpr int32_t TABLE")
        self.assertFinds(found, "W2 the name lookup helper Ids calls an output function")

    def test_function_pointer_and_other_writes(self):
        found = self.mutate(GATE_DRIVER_H, "namespace systems\n{",
                            "namespace systems\n{\nauto const p = &::bios::Output::set;")
        self.assertFinds(found, "W1 direct bios::Output::set")
        found = self.mutate(GATE_DRIVER_H, "namespace systems\n{",
                            "namespace systems\n{\ninline void f() { (void)::bios::Output::invert(::bios::Output::LED1); }")
        self.assertFinds(found, "W1 direct bios::Output::invert")
        found = self.mutate(GATE_DRIVER_H, "namespace systems\n{",
                            "namespace systems\n{\ninline void f() { (void)::bios::Io::setPin(3U, true); }")
        self.assertFinds(found, "W3 bios::Io::setPin")

    def test_second_set_in_safe_off_outputs(self):
        found = self.mutate(SAFE_OFF_OUTPUTS_H, "/** The level the drivers command",
                            "inline void raw() { (void)::bios::Output::set(::bios::Output::LED1, 1U); }\n"
                            "/** The level the drivers command")
        self.assertFinds(found, "W1 bios::Output::set outside setCommandedOutput()")

    def test_record_after_the_pin(self):
        old = ("    Commanded::note(static_cast<uint32_t>(output), on);\n"
               "    return ::bios::Output::set(output, on ? 1U : 0U) == ::bsp::BSP_OK;")
        new = ("    bool const ok = ::bios::Output::set(output, on ? 1U : 0U) == ::bsp::BSP_OK;\n"
               "    Commanded::note(static_cast<uint32_t>(output), on);\n    return ok;")
        sources = dict(self.sources)
        sources[SAFE_OFF_OUTPUTS_H] = sources[SAFE_OFF_OUTPUTS_H].replace("\r\n", "\n")
        found = self.mutate(SAFE_OFF_OUTPUTS_H, old, new, sources)
        self.assertFinds(found, "W1 setCommandedOutput() writes the pin before")
        found = self.mutate(SAFE_OFF_OUTPUTS_H, "    Commanded::note(static_cast<uint32_t>(output), on);\n", "",
                            sources)
        self.assertFinds(found, "W1 setCommandedOutput() writes the pin before (or without)")

    @nda_cell_monitor
    def test_cell_monitor_exception_expires(self):
        manifests = {k: dict(v) for k, v in self.manifests.items()}
        bcc = manifests["bccMc33771"]
        bcc["connect"] = {k: dict(v) for k, v in bcc["connect"].items()}
        bcc["connect"]["reset"]["safe_off"] = True
        found = violations(self.sources, manifests)
        self.assertFinds(found, "W1 exception expired - module bccMc33771")
        # and the module is a new safe_off module without binding and without write path
        self.assertFinds(found, "W5 safe_off output(s) reset")

    @nda_cell_monitor
    def test_cell_monitor_second_write_not_excepted(self):
        found = self.mutate(CELL_MONITOR_H, "    bool readFault(bool& active) override\n    {",
                            "    bool readFault(bool& active) override\n    {\n"
                            "        (void)::bios::Output::set(_reset, 1U);")
        self.assertFinds(found, "W1 bios::Output::set outside the excepted setReset()")

    def test_new_safe_off_module_needs_a_write_path(self):
        manifests = dict(self.manifests)
        manifests["pumpDriver"] = {"module": "pumpDriver", "connect": {"en": {"kind": "digital_output",
                                                                              "safe_off": True}}}
        found = violations(self.sources, manifests)
        self.assertFinds(found, "W5 safe_off output(s) en without a generated binding")
        manifests["pumpDriver"]["binding"] = {"trait": "x"}
        self.assertEqual(violations(self.sources, manifests), [])

    def test_comments_do_not_count(self):
        found = self.mutate(GATE_DRIVER_H, "namespace systems\n{",
                            "namespace systems\n{\n// ::bios::Output::set(x, 1U); bios::OutputTester t;\n"
                            "/* using Out = ::bios::Output; ::bios::Io::setPin(1U, true); */\n"
                            "char const* const s = \"::bios::Output::set(\";")
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
