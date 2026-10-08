"""SYS-51: the fault injection commands ("inject deadman", "inject wdogint") exist only in a test build.

The console group "inject" falsifies the LPIT deadman configuration or clears WDOG_CS[INT] while the application runs
and ends in a safety reset - a board test tool, never part of the standard (series) build. Same guard style as the
other test-build commands (NX_FLEXNVM_PARTITION_COMMAND in test_flexnvm.py, NX_GD_ENABLE_COMMAND, the hardfault
command):

SourceGuardTest (no build needed)
  - the CMake option NX_SAFETY_FAULT_INJECTION_COMMAND exists with default OFF;
  - SafetyFaultInjectionCommand.cpp is named exactly once in the CMake files, inside the if-block of that option, and
    reaches the image only through the object library of that block;
  - no other source than the command (and host tests) includes SafetyFaultInjection.h or names the command;
  - nothing switches the option on for a standard build (presets, CMake files, board ports, projects, tests.yaml);
  - the console reference generator (tools/docgen) sees the group only with that build option, and in no project's
    standard build (skipped without external/openbsw);
  - every check fails on a built-in fault (option ON, source outside the if-block, an include from Integration.cpp,
    a preset with the option ON).
DemoImageTest (after target-demo; skipped without the ELF)
  - the linked demo image has no symbol of the command and none of its console texts.

Run: .venv/Scripts/python platforms/s32k1xx/port/main/src/integration/test/test_safety_fault_injection_guard.py
     (ELF: NX_APP_ELF=<app.ecu.elf> or the demo build of tools/test/build_dirs.py; arm-none-eabi-nm from PATH or
     NX_ARM_GCC_BIN)
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[6]
PORT = REPO / "platforms/s32k1xx/port/main"
PORT_CMAKE = PORT / "CMakeLists.txt"

OPTION = "NX_SAFETY_FAULT_INJECTION_COMMAND"
COMMAND_SOURCE = "src/integration/SafetyFaultInjectionCommand.cpp"
COMMAND_REL = "platforms/s32k1xx/port/main/" + COMMAND_SOURCE
HEADER = "SafetyFaultInjection.h"
GROUP = "inject"
# Texts of the command that must not be in a standard image (strings of SafetyFaultInjectionCommand.cpp)
IMAGE_TEXTS = (b"inject deadman", b"inject wdogint", b"Safety fault injection")
SKIP_PARTS = {"external", "build", ".git", ".claude", ".venv", "node_modules"}

GCC_PATH = os.pathsep.join(p for p in (os.environ.get("NX_ARM_GCC_BIN"), os.environ.get("PATH")) if p)
ARM_NM = shutil.which("arm-none-eabi-nm", path=GCC_PATH)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _files(suffixes: tuple[str, ...], names: tuple[str, ...] = ()):
    for top in ("boards", "bootloader", "cmake", "ecu", "libs", "platforms", "projects", "swc", "tools"):
        base = REPO / top
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            rel = path.relative_to(REPO)
            if SKIP_PARTS.intersection(rel.parts) or not path.is_file():
                continue
            if path.suffix in suffixes or path.name in names:
                yield rel.as_posix(), path


def _strip_cmake_comments(text: str) -> str:
    return "\n".join(re.sub(r'#(?![^"]*"[^"]*$).*', "", line) for line in text.splitlines())


def _strip_cpp_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


# ------------------------------------------------------------------------------------------ checks (text -> errors)


def check_port_cmake(text: str) -> list[str]:
    """The option with default OFF, the source only inside its if-block, linked only through that block."""
    errors = []
    code = _strip_cmake_comments(text)
    if not re.search(rf'option\(\s*{OPTION}\s+"[^"]*"\s+OFF\s*\)', code):
        errors.append(f"option({OPTION} ... OFF) missing or not OFF by default")
    hits = [m.start() for m in re.finditer(re.escape(COMMAND_SOURCE), code)]
    if len(hits) != 1:
        errors.append(f"{COMMAND_SOURCE} named {len(hits)} times in the port CMakeLists.txt (expected once)")
        return errors
    start = code.rfind(f"if ({OPTION})", 0, hits[0])
    if start < 0 or "endif" in code[start:hits[0]]:
        errors.append(f"{COMMAND_SOURCE} outside the if ({OPTION}) block")
        return errors
    end = code.find("endif", hits[0])
    block = code[start:end]
    if not re.search(r"add_library\(\s*nxSafetyFaultInjection\s+OBJECT\s", block):
        errors.append("the command is not an object library of the option block")
    if "$<TARGET_OBJECTS:nxSafetyFaultInjection>" not in block:
        errors.append("the objects of the command are not linked inside the option block")
    outside = code[:start] + code[end:]
    if "nxSafetyFaultInjection" in outside:
        errors.append("nxSafetyFaultInjection used outside the option block")
    return errors


def check_other_cmake(files: dict[str, str]) -> list[str]:
    """No other CMake file names the command source or its object library."""
    errors = []
    for rel, text in files.items():
        code = _strip_cmake_comments(text)
        if "SafetyFaultInjectionCommand" in code or "nxSafetyFaultInjection" in code:
            errors.append(f"{rel}: names the fault injection command")
    return errors


def check_sources(files: dict[str, str]) -> list[str]:
    """Only the command includes the injection header; nothing else names the command."""
    errors = []
    for rel, text in files.items():
        if rel == COMMAND_REL or "/test/" in f"/{rel}":
            continue
        code = _strip_cpp_comments(text)
        if re.search(rf'#\s*include\s*[<"][^>"]*{re.escape(HEADER)}[>"]', code):
            errors.append(f"{rel}: includes {HEADER} (only the test-build command may)")
        if "SafetyFaultInjectionCommand" in code:
            errors.append(f"{rel}: names the fault injection command")
    return errors


def check_switched_on(files: dict[str, str]) -> list[str]:
    """No preset, CMake file, board port, project or test entry switches the option on."""
    errors = []
    pattern = re.compile(
        rf"{OPTION}\s*(?:=|\"\s*:\s*)\s*\"?(?:ON|TRUE|1|YES)\b|set\s*\(\s*{OPTION}\s+(?:ON|TRUE|1|YES)\b",
        re.IGNORECASE)
    for rel, text in files.items():
        if pattern.search(text):
            errors.append(f"{rel}: switches {OPTION} on")
    return errors


def _real_cmake_files() -> dict[str, str]:
    out = {rel: p.read_text(encoding="utf-8", errors="replace")
           for rel, p in _files((".cmake",), ("CMakeLists.txt",))}
    root = REPO / "CMakeLists.txt"
    out["CMakeLists.txt"] = root.read_text(encoding="utf-8", errors="replace")
    return out


def _real_sources() -> dict[str, str]:
    return {rel: p.read_text(encoding="utf-8", errors="replace") for rel, p in _files((".cpp", ".h", ".hpp", ".c"))}


def _real_switch_files() -> dict[str, str]:
    out = _real_cmake_files()
    out["CMakePresets.json"] = (REPO / "CMakePresets.json").read_text(encoding="utf-8")
    for rel, p in _files((".yaml", ".json")):
        # SYS-75: a board test suite names the test image the person at the board builds; it builds nothing itself
        if rel.startswith("tools/boardtest/suites/"):
            continue
        out[rel] = p.read_text(encoding="utf-8", errors="replace")
    return out


class SourceGuardTest(unittest.TestCase):
    def test_option_off_and_source_only_in_its_block(self):
        self.assertEqual(check_port_cmake(PORT_CMAKE.read_text(encoding="utf-8")), [])

    def test_no_other_cmake_file_builds_the_command(self):
        files = {rel: t for rel, t in _real_cmake_files().items() if rel != PORT_CMAKE.relative_to(REPO).as_posix()}
        self.assertEqual(check_other_cmake(files), [])

    def test_only_the_command_uses_the_injection(self):
        sources = _real_sources()
        self.assertIn(COMMAND_REL, sources)
        self.assertEqual(check_sources(sources), [])

    def test_nothing_switches_the_option_on(self):
        self.assertEqual(check_switched_on(_real_switch_files()), [])

    def test_console_reference_sees_it_only_with_the_option(self):
        if not (REPO / "external/openbsw/libs").is_dir():
            self.skipTest("external/openbsw not checked out (tools/docgen needs the OpenBSW commands)")
        sys.path.insert(0, str(REPO))
        try:
            from tools.docgen import console  # noqa: E402
        finally:
            sys.path.remove(str(REPO))
        doc = console.collect()
        groups = [g for g in doc.groups if g.name == GROUP]
        self.assertEqual(len(groups), 1, "console group 'inject' not found by tools/docgen")
        group = groups[0]
        self.assertEqual(group.source, COMMAND_REL)
        self.assertIn(("option", OPTION, False), {(c.kind, c.name, c.negated) for c in group.conds})
        self.assertEqual(doc.options[OPTION][0], "OFF")
        # SYS-97: mspuse and mspoverflow (board checks of the interrupt stack)
        self.assertEqual(sorted(c.name for c in group.commands), ["deadman", "mspoverflow", "mspuse", "wdogint"])
        for project in doc.projects:
            self.assertIsNot(console.availability(group.conds, project, doc), True,
                             f"{project.name}: 'inject' would be in the standard build")

    def test_built_in_faults_are_detected(self):
        cmake = PORT_CMAKE.read_text(encoding="utf-8")
        mutants = {
            "option ON": cmake.replace(f'OFF)\nif ({OPTION})', f'ON)\nif ({OPTION})'),
            "source before the block": cmake.replace(
                "target_include_directories(main PUBLIC include)",
                f"target_sources(main PRIVATE {COMMAND_SOURCE})\ntarget_include_directories(main PUBLIC include)", 1),
            "objects linked outside": cmake.replace(
                "target_include_directories(main PUBLIC include)",
                "target_link_libraries(main INTERFACE nxSafetyFaultInjection)\n"
                "target_include_directories(main PUBLIC include)", 1),
        }
        for name, text in mutants.items():
            with self.subTest(mutant=name):
                self.assertNotEqual(text, cmake, f"mutant '{name}' does not apply (fault of this test)")
                self.assertNotEqual(check_port_cmake(text), [], name)
        self.assertNotEqual(check_sources({"platforms/s32k1xx/port/main/src/integration/Integration.cpp":
                                           '#include "SafetyFaultInjection.h"\n'}), [])
        self.assertNotEqual(check_sources({"ecu/application/src/app/app.cpp":
                                           "SafetyFaultInjectionCommand x;\n"}), [])
        self.assertNotEqual(check_other_cmake({"ecu/application/CMakeLists.txt":
                                               "target_sources(app PRIVATE SafetyFaultInjectionCommand.cpp)"}), [])
        for text in (f'"cacheVariables": {{ "{OPTION}": "ON" }}', f"-D{OPTION}=ON", f"set({OPTION} ON)"):
            with self.subTest(switch=text):
                self.assertNotEqual(check_switched_on({"CMakePresets.json": text}), [])
        self.assertEqual(check_switched_on({"x": f"-D{OPTION}=OFF"}), [])


def application_elf() -> Path | None:
    env = os.environ.get("NX_APP_ELF")
    if env:
        return Path(env)
    build = _load("nx_build_dirs", REPO / "tools/test/build_dirs.py").demo_build_dir(REPO)
    elf = Path(build) / "ecu/application/RelWithDebInfo/app.ecu.elf"
    return elf if elf.is_file() else None


class DemoImageTest(unittest.TestCase):
    def setUp(self):
        self.elf = application_elf()
        if self.elf is None or not self.elf.is_file():
            self.skipTest("demo image not built (tools/test -k target-demo)")

    def test_no_symbol_of_the_command(self):
        if ARM_NM is None:
            self.skipTest("arm-none-eabi-nm not found (PATH or NX_ARM_GCC_BIN)")
        out = subprocess.run([ARM_NM, "-C", str(self.elf)], capture_output=True, text=True, check=True).stdout
        self.assertIn("deadmanExpectedConfiguration", out, "not the image with the deadman (fault of this test)")
        hits = [line for line in out.splitlines()
                if re.search(r"SafetyFaultInjection|faultinjection::|clearWatchdogInterrupt|injectDeadmanFault", line)]
        self.assertEqual(hits, [])

    def test_no_text_of_the_command(self):
        data = self.elf.read_bytes()
        # assertTrue/False instead of assertIn: the failure message must not print the whole image
        self.assertTrue(b"Commands to test safety features" in data, "console texts not found (fault of this test)")
        found = [text.decode() for text in IMAGE_TEXTS if text in data]
        self.assertEqual(found, [], "console texts of the fault injection in the image")


if __name__ == "__main__":
    unittest.main()
