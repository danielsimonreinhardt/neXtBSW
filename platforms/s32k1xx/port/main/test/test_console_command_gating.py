"""PLAT-106: the console commands of the systems exist only in an image with console (NX_CONSOLE).

project.yaml console.enabled false (PLAT-27) sets NX_CONSOLE 0. A constructed ::console::AsyncCommandWrapper (or
SyncCommandWrapper) registers its command with the console during static initialization, so the command, its
texts and the console framework are linked although no console could ever run them (S32K116: about 10 KB flash
for the commands of the family port alone). Every wrapper of the family port's systems, of the high-side switch
module and the "nvm"/"dem"/"cal" commands of RteSystem.cpp must therefore be inside a preprocessor condition on
NX_CONSOLE - or inside a test build option that the family port's CMakeLists.txt switches off without console.

SourceGuardTest (no build needed)
  - every wrapper declaration, member initialisation and typed_storage create() in the scanned sources is inside
    a condition naming NX_CONSOLE or such a test build option;
  - the family port's CMakeLists.txt defines NX_CONSOLE for target main (on without a project value) and switches
    the test build options with console commands off without console; the high-side switch module defines it
    for its system target;
  - the check fails on built-in faults (an ungated member, an ungated global wrapper, an ungated create(), a test
    build option missing in the CMake list).

Run: .venv/Scripts/python platforms/s32k1xx/port/main/test/test_console_command_gating.py
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[4]
PORT = REPO / "platforms/s32k1xx/port/main"
PORT_CMAKE = PORT / "CMakeLists.txt"
HSS = REPO / "libs/bsw/highSideSwitch"
HSS_CMAKE = HSS / "CMakeLists.txt"

# Directories scanned completely (without test folders) and single files
SCAN_DIRS = (PORT / "include", PORT / "src", HSS / "include", HSS / "src")
SCAN_FILES = (REPO / "ecu/application/src/systems/RteSystem.cpp",)
# Files of which only the typed_storage create() calls are checked: the member wrapper of RteSystem ("rte") and the
# other systems of ecu/application are declared in ecu/application/include/systems (outside PLAT-106, open item)
CREATE_ONLY = {"ecu/application/src/systems/RteSystem.cpp"}

# A whole source that is compiled only with a test build option (CMake), and that option
OPTION_SOURCES = {"SafetyFaultInjectionCommand.cpp": "NX_SAFETY_FAULT_INJECTION_COMMAND",
                  # SYS-173 / SYS-172: board proofs of the direct reset and of the lock time of the selection
                  "ShutdownHangCommand.cpp": "NX_SHUTDOWN_HANG_COMMAND",
                  "ShortLockMeasureCommand.cpp": "NX_SHORT_LOCK_MEASURE_COMMAND"}

WRAPPER = r"(?:::)?console::(?:Async|Sync)CommandWrapper"


def forced_off_options(cmake_text: str) -> set[str]:
    """Test build options the family port switches off without console (foreach list of PLAT-106)."""
    m = re.search(r"foreach\s*\(\s*_nx_option\s+([^)]*)\)", cmake_text)
    return set(m.group(1).split()) if m else set()


def _strip_comment(line: str) -> str:
    return line.split("//", 1)[0]


def _active_conditions(lines: list[str]) -> list[list[str]]:
    """For every line the stack of the preprocessor conditions it is in (#else keeps the condition text)."""
    stack: list[str] = []
    out = []
    for line in lines:
        s = line.strip()
        if re.match(r"#\s*(if|ifdef|ifndef)\b", s):
            out.append(list(stack))
            stack.append(s)
            continue
        if re.match(r"#\s*endif\b", s):
            out.append(list(stack))
            if stack:
                stack.pop()
            continue
        out.append(list(stack))
    return out


def violations(sources: dict[str, str], accepted: set[str]) -> list[str]:
    """Wrapper uses outside a condition on one of the accepted macros, as 'file:line: text'."""
    accepted_re = re.compile(r"\b(" + "|".join(sorted(re.escape(a) for a in accepted)) + r")\b")
    members: set[str] = set()
    storages: set[str] = set()
    for text in sources.values():
        for m in re.finditer(rf"^\s*{WRAPPER}\s+(\w+)\s*;", text, re.M):
            members.add(m.group(1))
        for m in re.finditer(rf"typed_storage<\s*{WRAPPER}\s*>\s+(\w+)\s*;", text):
            storages.add(m.group(1))
    found = []
    for name, text in sources.items():
        option = OPTION_SOURCES.get(Path(name).name)
        if option is not None and option in accepted:
            continue
        lines = text.splitlines()
        conds = _active_conditions(lines)
        for i, raw in enumerate(lines):
            line = _strip_comment(raw)
            if not line.strip() or line.lstrip().startswith(("#", "*", "/*")):
                continue
            create = any(re.search(rf"\b{re.escape(s)}\s*\.\s*create\s*\(", line) for s in storages)
            hit = create or (
                name not in CREATE_ONLY
                and (
                    (re.search(rf"\b{WRAPPER}\b", line) and not re.search(r"typed_storage<", line))
                    or any(re.search(rf"^\s*,\s*{re.escape(m)}\s*\(", line) for m in members)
                )
            )
            if hit and not any(accepted_re.search(c) for c in conds[i]):
                found.append(f"{name}:{i + 1}: {raw.strip()}")
    return found


def scanned_sources() -> dict[str, str]:
    files = [p for d in SCAN_DIRS for p in d.rglob("*") if p.suffix in (".h", ".hpp", ".cpp")]
    files = [p for p in files if "test" not in p.relative_to(REPO).parts]
    files += list(SCAN_FILES)
    return {p.relative_to(REPO).as_posix(): p.read_text(encoding="utf-8") for p in sorted(files)}


class SourceGuardTest(unittest.TestCase):
    def setUp(self):
        self.cmake = PORT_CMAKE.read_text(encoding="utf-8")
        self.accepted = {"NX_CONSOLE"} | forced_off_options(self.cmake)
        self.sources = scanned_sources()

    def test_every_wrapper_is_gated(self):
        self.assertGreater(len(self.sources), 20)
        self.assertEqual(violations(self.sources, self.accepted), [])

    def test_scanned_sources_contain_wrappers(self):
        # the scan sees the systems at all (a renamed directory must not make the check pass empty)
        joined = "\n".join(self.sources.values())
        for name in ("_asyncCommand", "_asyncFlexNvmCommand", "asyncI2cCommand", "nvmAsyncCommand"):
            self.assertIn(name, joined)

    def test_port_cmake_defines_nx_console(self):
        self.assertRegex(self.cmake, r"if \(NOT DEFINED NX_CONSOLE OR NX_CONSOLE\)\s*\n\s*target_compile_definitions"
                                     r"\(main PRIVATE NX_CONSOLE=1\)")
        self.assertIn("target_compile_definitions(main PRIVATE NX_CONSOLE=0)", self.cmake)
        # every test build option with a console command is switched off without console
        options = set(re.findall(r"\boption\s*\(\s*(NX_\w+_COMMAND)\b", self.cmake))
        self.assertTrue(options)
        self.assertEqual(options - forced_off_options(self.cmake), set())
        # ... before the first of them is declared (a normal variable then shadows the cache option)
        self.assertLess(self.cmake.index("foreach (_nx_option"), self.cmake.index("option("))

    def test_high_side_switch_defines_nx_console(self):
        text = HSS_CMAKE.read_text(encoding="utf-8")
        self.assertIn("target_compile_definitions(highSideSwitchSystem PUBLIC NX_CONSOLE=1)", text)
        self.assertIn("target_compile_definitions(highSideSwitchSystem PUBLIC NX_CONSOLE=0)", text)

    # ---- built-in faults: the check must find them

    def test_fault_ungated_member(self):
        name = "platforms/s32k1xx/port/main/include/systems/CanSystem.h"
        text = self.sources[name]
        broken = re.sub(r"#if !defined\(NX_CONSOLE\) \|\| NX_CONSOLE\n(\s*// PLAT-106[^\n]*\n)", r"\1", text, count=1)
        self.assertNotEqual(broken, text)
        found = violations({**self.sources, name: broken}, self.accepted)
        self.assertTrue(any("CanSystem.h" in f and "AsyncCommandWrapper" in f for f in found), found)

    def test_fault_ungated_member_initialisation(self):
        name = "platforms/s32k1xx/port/main/src/systems/SbcSystem.cpp"
        text = self.sources[name]
        broken = text.replace("#if !defined(NX_CONSOLE) || NX_CONSOLE\n, _command(*this)", ", _command(*this)", 1)
        self.assertNotEqual(broken, text)
        found = violations({**self.sources, name: broken}, self.accepted)
        self.assertTrue(any("SbcSystem.cpp" in f and "_asyncCommand(" in f for f in found), found)

    def test_fault_ungated_global_wrapper(self):
        name = "platforms/s32k1xx/port/main/src/integration/Integration.cpp"
        broken = self.sources[name] + "\n::console::AsyncCommandWrapper extraWrapper(extraCommand, 0U);\n"
        found = violations({**self.sources, name: broken}, self.accepted)
        self.assertTrue(any("extraWrapper" in f for f in found), found)

    def test_fault_ungated_create(self):
        name = "ecu/application/src/systems/RteSystem.cpp"
        text = self.sources[name]
        broken = text.replace("#if !defined(NX_CONSOLE) || NX_CONSOLE\n#ifdef NX_RTE_HAS_NVM", "#ifdef NX_RTE_HAS_NVM", 1)
        self.assertNotEqual(broken, text)
        found = violations({**self.sources, name: broken}, self.accepted)
        self.assertTrue(any("nvmAsyncCommand.create" in f for f in found), found)

    def test_fault_option_not_switched_off(self):
        accepted = self.accepted - {"NX_SAFETY_FAULT_INJECTION_COMMAND", "NX_DFLASH_COMMAND",
                                    "NX_SHUTDOWN_HANG_COMMAND", "NX_SHORT_LOCK_MEASURE_COMMAND"}
        found = violations(self.sources, accepted)
        self.assertTrue(any("SafetyFaultInjectionCommand.cpp" in f for f in found), found)
        self.assertTrue(any("DataFlashSystem" in f for f in found), found)
        self.assertTrue(any("ShutdownHangCommand.cpp" in f for f in found), found)
        self.assertTrue(any("ShortLockMeasureCommand.cpp" in f for f in found), found)

    def test_board_proof_options_switched_off_without_console(self):
        # SYS-173/SYS-172: both options bring console commands only - without console they are off (foreach list)
        forced = forced_off_options(self.cmake)
        for option in ("NX_SHUTDOWN_HANG_COMMAND", "NX_SHORT_LOCK_MEASURE_COMMAND"):
            self.assertIn(option, forced)
            self.assertRegex(self.cmake, rf'option\({option}\s+"[^"]*"\s+OFF\)')
        mutant = self.cmake.replace(" NX_SHUTDOWN_HANG_COMMAND", "", 1)
        self.assertNotEqual(mutant, self.cmake, "mutant does not apply (fault of this test)")
        found = violations(self.sources, {"NX_CONSOLE"} | forced_off_options(mutant))
        self.assertTrue(any("ShutdownHangCommand.cpp" in f for f in found), found)


if __name__ == "__main__":
    unittest.main()
