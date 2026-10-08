"""SYS-74: the product check of tools/elfcheck (product.py) and the product build switch, without a build.

ProductUnitTest
  - a console text counts only in the bytes that go into the ECU (allocated sections with content), not in the
    debug information; symbols count with their mangled names, AdcSafetyCommand (safeadc stays) is not "safety";
  - the counter check names a marker without a hit;
  - the inventory is complete: every test-build option of a family port (option(NX_*_COMMAND ... OFF)) is refused
    by cmake/NxProductBuild.cmake (NX_TEST_BUILD_OPTIONS) and has a marker in product.MARKERS, and the other way round;
  - NX_PRODUCT_BUILD is OFF by default (option of the root CMakeLists.txt), the definition reaches every target
    (module included before the first add_subdirectory) and the product build refuses the test options (FATAL_ERROR);
  - the development-only code is guarded by NX_PRODUCT_BUILD where the inventory says (consoleCommands, SafetySystem,
    SbcSystem, Integration.cpp);
  - every check fails on a built-in fault (option missing from the list, marker missing, include after the first
    subdirectory, default ON, guard removed).
The linked images are checked by tools/elfcheck/test/product_build.py (tools/test -k target-product).

Run: .venv/Scripts/python tools/elfcheck/test/test_product.py
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1]
REPO = TOOL.parents[1]
sys.path.insert(0, str(TOOL))

import elffile  # noqa: E402
import product  # noqa: E402

PRODUCT_CMAKE = REPO / "cmake" / "NxProductBuild.cmake"
ROOT_CMAKE = REPO / "CMakeLists.txt"

# Where the product build must leave development code out: file -> texts that must sit inside an
# "#ifndef NX_PRODUCT_BUILD" / "!defined(NX_PRODUCT_BUILD)" block
GUARDED = {
    "ecu/consoleCommands/src/lifecycle/console/LifecycleControlCommand.cpp":
        ('"reboot the system"', '"forces an assert"', "case ID_REBOOT:", "case ID_ASSERT:"),
    "platforms/s32k1xx/port/main/src/systems/SbcSystem.cpp":
        ('"failstart"', "case ID_FAILSTART:", "failed starts still armed",
         # SYS-119: sbc write, sbc pn
         '"write register (param: <address> <value>)', "case ID_WRITE:", "sbc.writeRegister(",
         '"arm: write CAN wake-up frame', "case ID_PN:", "sbc.armPartialNetworking()"),
    "platforms/s32k1xx/port/main/src/integration/Integration.cpp": ("failStartIfArmed",),
    "ecu/application/include/systems/SafetySystem.h": ("SafetyCommand _safetyCommand",),
    "ecu/application/src/systems/SafetySystem.cpp": ("_asyncCommandWrapperForSafetyCommand(",),
    "ecu/application/src/systems/RteSystem.cpp": ('"inject"', "nvmInject(context, out);", "NVM_ID_INJECT,",
                                                  # SYS-119: dem clear, cycle, setting
                                                  '"[<dtc>] clears every DTC', "case DEM_ID_CLEAR:",
                                                  "case DEM_ID_CYCLE:", "case DEM_ID_SETTING:",
                                                  "memory.clear(group)", "memory.setDtcSetting("),
    # SYS-162 (SR-110-1 c): console "pwm"
    "platforms/s32k1xx/port/main/include/systems/BspSystem.h":
        ('#include "systems/CheckedOutputPwmTester.h"', "CheckedOutputPwmTester _outputPwmTester;",
         "::console::AsyncCommandWrapper _asyncOutputPwmTester;"),
    "platforms/s32k1xx/port/main/src/systems/BspSystem.cpp":
        (", _outputPwmTester()", ", _asyncOutputPwmTester(_outputPwmTester"),
    # SYS-119 (CX-9, safety plan N7): service commands that write past the function owning the target
    "platforms/s32k1xx/port/main/src/systems/GateDriverSystem.cpp":
        ('"write GCONF/GSTAT/SHORT_CONF/DRV_CONF', "case ID_WRITE:", "drv.writeRegister("),
    "platforms/s32k1xx/port/main/src/systems/CellMonitorSystem.cpp":
        ('"write a fault status register', "case ID_WRITE:", "drv.writeRegister("),
    "platforms/s32k1xx/port/main/src/systems/CanSystem.cpp":
        ('"send a standard frame if the transceiver is ready', "case ID_SEND:", "result = bus->send(frame);"),
    "platforms/s32k1xx/port/bspConfiguration/src/bsp/i2c/I2cCommand.cpp":
        ('"write the bytes with STOP', '"write the bytes, repeated START', "case ID_READ:", "case ID_WRITE:",
         "void readCommand(", "void writeCommand(", "master->write(", "writeRead(*master"),
    "platforms/s32k1xx/port/main/src/systems/SafeOffOutputTester.cpp":
        ("refused: safe_off output, switched by its driver only",),
    # SYS-119 review points S1, S2 (S4: SbcSystem.cpp below)
    "libs/bsw/highSideSwitch/src/highSideSwitch/HighSideSwitchSystem.cpp":
        ('"switch on, load supply (board rule)"', '"clear a latched overcurrent/diagnosis fault', "case ID_ON:",
         "case ID_RESET:", "console::Command::ON"),
}
# SYS-119 review points S2, S4: files already in GUARDED get more texts
GUARDED["platforms/s32k1xx/port/main/src/systems/GateDriverSystem.cpp"] += (
    '"clear latched faults and GSTAT flags', "case ID_CLEAR:")
GUARDED["platforms/s32k1xx/port/main/src/systems/CellMonitorSystem.cpp"] += (
    '"clear latched faults and device fault registers', "case ID_CLEAR:", "drv.clearFaults()")
GUARDED["platforms/s32k1xx/port/main/src/systems/SbcSystem.cpp"] += (
    '"enter SBC Sleep, MCU supply off', "case ID_SLEEP:", "system._sleepRequested = true;")

# SYS-119: code that exists only in the product image (#ifdef NX_PRODUCT_BUILD): "out" without "set"
PRODUCT_ONLY = {
    "platforms/s32k1xx/port/main/src/systems/SafeOffOutputTester.cpp":
        ('DEFINE_COMMAND_GROUP_GET_INFO_BEGIN(SafeOffOutputTester, "out"', "if (idx == SET_COMMAND)"),
    "platforms/s32k1xx/port/main/include/systems/SafeOffOutputTester.h": ("DECLARE_COMMAND_GROUP_GET_INFO",),
}

# SYS-162: sources of development-only commands that CMake compiles only without NX_PRODUCT_BUILD
CMAKE_GUARDED = {
    "platforms/s32k1xx/port/main/CMakeLists.txt": "src/systems/CheckedOutputPwmTester.cpp",
}


def _strip_cmake_comments(text: str) -> str:
    return "\n".join(re.sub(r'#(?![^"]*"[^"]*$).*', "", line) for line in text.splitlines())


# ------------------------------------------------------------------------------------------ checks (text -> errors)


def port_test_options() -> set[str]:
    """option(NX_*_COMMAND ...) of every family port."""
    found = set()
    for cmake in REPO.glob("platforms/*/port/main/CMakeLists.txt"):
        found |= set(re.findall(r"option\(\s*(NX_\w+_COMMAND)\b", _strip_cmake_comments(cmake.read_text("utf-8"))))
    return found


def refused_options(text: str) -> set[str]:
    m = re.search(r"set\(\s*NX_TEST_BUILD_OPTIONS\s+([^)]*)\)", _strip_cmake_comments(text))
    return set(m.group(1).split()) if m else set()


def check_inventory(port_options: set[str], cmake_text: str, markers=product.MARKERS) -> list[str]:
    errors = []
    refused = refused_options(cmake_text)
    marked = {m.option for m in markers if m.option}
    for option in sorted(port_options - refused):
        errors.append(f"{option}: test-build option not refused by NxProductBuild.cmake (NX_TEST_BUILD_OPTIONS)")
    for option in sorted(port_options - marked):
        errors.append(f"{option}: no marker in tools/elfcheck/product.py")
    for option in sorted((refused | marked) - port_options):
        errors.append(f"{option}: named for the product build, but no family port declares it")
    return errors


def check_product_cmake(text: str) -> list[str]:
    errors = []
    code = _strip_cmake_comments(text)
    block = code[code.find("if (NX_PRODUCT_BUILD)"):]
    if "if (NX_PRODUCT_BUILD)" not in code or "add_compile_definitions(NX_PRODUCT_BUILD=1)" not in block:
        errors.append("the product build does not define NX_PRODUCT_BUILD=1 for every target")
    if not re.search(r"foreach\s*\(\s*_nx_option\s+IN\s+LISTS\s+NX_TEST_BUILD_OPTIONS\s*\)", block) \
            or "FATAL_ERROR" not in block:
        errors.append("the product build does not refuse the test-build options")
    return errors


def check_root_include(text: str) -> list[str]:
    """Root CMakeLists.txt: option OFF by default (tools/docgen reads options there), module before any subdirectory."""
    code = _strip_cmake_comments(text)
    option = re.search(r'option\(\s*NX_PRODUCT_BUILD\s+"[^"]*"\s+(\w+)\s*\)', code)
    include = code.find('include("${NX_ROOT}/cmake/NxProductBuild.cmake")')
    errors = []
    if option is None or option.group(1) != "OFF":
        errors.append("option(NX_PRODUCT_BUILD ... OFF) missing in the root CMakeLists.txt or not OFF by default")
    elif include >= 0 and option.start() > include:
        errors.append("option(NX_PRODUCT_BUILD) declared after the include of cmake/NxProductBuild.cmake")
    if include < 0:
        return errors + ["the root CMakeLists.txt does not include cmake/NxProductBuild.cmake"]
    first = code.find("add_subdirectory(")
    if 0 <= first < include:
        errors.append("cmake/NxProductBuild.cmake included after the first add_subdirectory (definition misses "
                      "targets)")
    return errors


def _guarded_lines(text: str) -> set[int]:
    """Line numbers inside a block that is compiled only without NX_PRODUCT_BUILD."""
    inside, stack = set(), []
    for number, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if s.startswith("#if"):
            stack.append(bool(re.match(r"#ifndef\s+NX_PRODUCT_BUILD\b", s)) or "!defined(NX_PRODUCT_BUILD)" in s)
        elif s.startswith("#else") or s.startswith("#elif"):
            if stack:
                stack[-1] = False
        elif s.startswith("#endif"):
            if stack:
                stack.pop()
        elif any(stack):
            inside.add(number)
    return inside


def _product_lines(text: str) -> set[int]:
    """SYS-119: line numbers inside a block that is compiled only with NX_PRODUCT_BUILD (#ifdef / #if defined)."""
    inside, stack = set(), []
    for number, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if s.startswith("#if"):
            stack.append(bool(re.match(r"#ifdef\s+NX_PRODUCT_BUILD\b", s))
                         or bool(re.match(r"#if\s+defined\s*\(\s*NX_PRODUCT_BUILD\s*\)\s*(//.*)?$", s)))
        elif s.startswith("#else") or s.startswith("#elif"):
            if stack:
                stack[-1] = False
        elif s.startswith("#endif"):
            if stack:
                stack.pop()
        elif any(stack):
            inside.add(number)
    return inside


def check_product_only(files: dict[str, str], expected: dict[str, tuple[str, ...]] | None = None) -> list[str]:
    errors = []
    for rel, texts in (PRODUCT_ONLY if expected is None else expected).items():
        text = files.get(rel)
        if text is None:
            errors.append(f"{rel}: missing")
            continue
        inside = _product_lines(text)
        lines = text.splitlines()
        for needle in texts:
            numbers = [i for i, line in enumerate(lines, 1) if needle in line]
            if not numbers:
                errors.append(f"{rel}: {needle} not found (fault of this test or code moved)")
            for number in numbers:
                if number not in inside:
                    errors.append(f"{rel}:{number}: {needle} outside an #ifdef NX_PRODUCT_BUILD block")
    return errors


def check_guards(files: dict[str, str]) -> list[str]:
    errors = []
    for rel, texts in GUARDED.items():
        text = files.get(rel)
        if text is None:
            errors.append(f"{rel}: missing")
            continue
        guarded = _guarded_lines(text)
        lines = text.splitlines()
        for needle in texts:
            numbers = [i for i, line in enumerate(lines, 1) if needle in line]
            if not numbers:
                errors.append(f"{rel}: {needle} not found (fault of this test or code moved)")
            for number in numbers:
                if number not in guarded:
                    errors.append(f"{rel}:{number}: {needle} outside the NX_PRODUCT_BUILD guard")
    return errors


def check_consolecommands_cmake(text: str) -> list[str]:
    code = _strip_cmake_comments(text)
    hits = [m.start() for m in re.finditer(r"SafetyCommand\.cpp", code)]
    if len(hits) != 1:
        return [f"SafetyCommand.cpp named {len(hits)} times in ecu/consoleCommands/CMakeLists.txt (expected once)"]
    start = code.rfind("if (NOT NX_PRODUCT_BUILD)", 0, hits[0])
    if start < 0 or "endif" in code[start:hits[0]]:
        return ["SafetyCommand.cpp outside the if (NOT NX_PRODUCT_BUILD) block"]
    return []


def check_cmake_guard(text: str, source: str) -> list[str]:
    """SYS-162: `source` is named once, inside an if (NOT NX_PRODUCT_BUILD) block."""
    code = _strip_cmake_comments(text)
    hits = [m.start() for m in re.finditer(re.escape(source), code)]
    if len(hits) != 1:
        return [f"{source} named {len(hits)} times (expected once)"]
    start = code.rfind("if (NOT NX_PRODUCT_BUILD)", 0, hits[0])
    if start < 0 or "endif" in code[start:hits[0]]:
        return [f"{source} outside the if (NOT NX_PRODUCT_BUILD) block"]
    return []


def _real_guard_files() -> dict[str, str]:
    return {rel: (REPO / rel).read_text(encoding="utf-8") for rel in GUARDED if (REPO / rel).is_file()}


# PLAT-62: guarded files of the NDA cell monitor (libs/bsw/bccMc33771, CellMonitorSystem.*) are not in the exported
# open tree - there they are no finding ("missing") but simply not part of the tree
NDA_FILES = {"platforms/s32k1xx/port/main/src/systems/CellMonitorSystem.cpp",
             "platforms/s32k1xx/port/main/include/systems/CellMonitorSystem.h"}


def _absent_nda(findings: list[str]) -> list[str]:
    return [f for f in findings if not (f.endswith(": missing") and f.split(":")[0] in NDA_FILES
                                        and not (REPO / f.split(":")[0]).is_file())]


def _image(sections: list[tuple[str, int, int, bytes]], symbols: list[str]) -> elffile.ElfImage:
    """A made-up image: (name, type, flags, bytes) per section, defined symbols."""
    data, parts = b"", []
    for index, (name, sh_type, flags, content) in enumerate(sections, 1):
        parts.append(elffile.Section(index, name, sh_type, flags, 0x1000 * index, len(data), len(content), 0))
        data += content
    img = elffile.ElfImage(Path("made-up.elf"), data, 0, sections=parts)
    img.symbols = [elffile.Symbol(name, 0, 0, 2, 1, 1) for name in symbols]
    return img


SHT_PROGBITS, SHF_ALLOC = 1, 2


class ProductUnitTest(unittest.TestCase):
    def test_texts_only_in_the_image_bytes(self):
        img = _image([(".rodata", SHT_PROGBITS, SHF_ALLOC, b"x\0forces an assert\0"),
                      (".debug_str", SHT_PROGBITS, 0, b"Commands to test safety features\0ID_FAILSTART\0")], [])
        hits = product.find(*product.image_parts(img))
        self.assertEqual([(h.marker.command, h.kind) for h in hits], [("lc udef|pabt|dabt|assert", "text")])

    def test_symbols_by_mangled_name(self):
        img = _image([(".text", SHT_PROGBITS, SHF_ALLOC, b"\0")],
                     ["_ZTVN6safety13SafetyCommandE", "_ZN16AdcSafetyCommand14executeCommandERN4util7commandEh",
                      "_ZN7systems13SbcEarlyStart13armFailStartsEh"])
        hits = product.find(*product.image_parts(img))
        self.assertEqual(sorted(h.marker.command for h in hits), ["safety mpu|wdg|prt|dst|est", "sbc failstart"])

    def test_counter_check_names_a_marker_without_hit(self):
        hits = product.find([], b"Trigger a hard fault")
        absent = product.missing(hits)
        self.assertEqual(len(absent), len(product.MARKERS) - 1)
        self.assertNotIn("hardfault udf|bus|sp", [m.command for m in absent])

    def test_every_marker_has_something_to_find(self):
        for marker in product.MARKERS:
            with self.subTest(marker=marker.command):
                self.assertTrue(marker.symbols or marker.texts)
                self.assertTrue(marker.why)

    def test_board_proof_markers(self):
        """SYS-173/SYS-172: "shutdownhang" applies to every image, "shortlock" only where the selection of the active
        short exists (its group: a log line of the start test of the short) - the demo has none, so the counter check skips it
        there and keeps it in an image of an active-short project."""
        commands = {m.command: m for m in product.MARKERS}
        hang = commands["shutdownhang arm|disarm|show"]
        lock = commands["shortlock show|reset|select|block"]
        self.assertEqual((hang.option, hang.group), ("NX_SHUTDOWN_HANG_COMMAND", None))
        self.assertEqual(lock.option, "NX_SHORT_LOCK_MEASURE_COMMAND")
        self.assertIn(hang, product.applicable(b""))
        self.assertNotIn(lock, product.applicable(b""))
        short_image = b"safe state: PWM group %s short removed until power-on\0"
        self.assertIn(lock, product.applicable(short_image))
        self.assertIn(lock, product.missing(product.find([], short_image), product.applicable(short_image)))
        hits = product.find(["_ZN12_GLOBAL__N_118ShutdownHangSystem8shutdownEv"],
                            short_image + b"SYS-172 lock time of the safe state selection (test build only)\0")
        self.assertEqual(sorted({h.marker.command for h in hits}), sorted([hang.command, lock.command]))

    def test_inventory_complete(self):
        options = port_test_options()
        self.assertIn("NX_SAFETY_FAULT_INJECTION_COMMAND", options, "no port option found (fault of this test)")
        self.assertEqual(check_inventory(options, PRODUCT_CMAKE.read_text(encoding="utf-8")), [])

    def test_product_switch(self):
        self.assertEqual(check_product_cmake(PRODUCT_CMAKE.read_text(encoding="utf-8")), [])
        self.assertEqual(check_root_include(ROOT_CMAKE.read_text(encoding="utf-8")), [])
        self.assertEqual(check_consolecommands_cmake(
            (REPO / "ecu/consoleCommands/CMakeLists.txt").read_text(encoding="utf-8")), [])
        for rel, source in CMAKE_GUARDED.items():
            with self.subTest(cmake=rel):
                self.assertEqual(check_cmake_guard((REPO / rel).read_text(encoding="utf-8"), source), [])

    def test_pwm_not_in_the_product(self):
        """SYS-162 (SR-110-1 c): console "pwm" is a development command - marker without option, guarded in
        BspSystem and in CMake; each of the three fails on a built-in fault."""
        marker = next((m for m in product.MARKERS if m.command.startswith("pwm ")), None)
        self.assertIsNotNone(marker, "no marker for the console pwm")
        self.assertIsNone(marker.option, "pwm is in every development build, not behind a test option")
        img = _image([(".rodata", SHT_PROGBITS, SHF_ALLOC, b"channel[0-9,0xFF=all] duty[0=off|10000=100%]\0")],
                     ["_ZN7systems22CheckedOutputPwmTester14executeCommandERN4util7command14CommandContextEh",
                      "_ZTVN4bios15OutputPwmTesterE"])
        hits = [h for h in product.find(*product.image_parts(img)) if h.marker is marker]
        self.assertEqual(sorted(h.kind for h in hits), ["symbol", "symbol", "text"])
        clean = _image([(".rodata", SHT_PROGBITS, SHF_ALLOC, b"Set PWM\0")], ["_ZN4bios9OutputPwm7setDutyEttb"])
        self.assertEqual(product.find(*product.image_parts(clean)), [],
                         "OutputPwm itself (the interface of the drivers) is no finding")
        rel = "platforms/s32k1xx/port/main/CMakeLists.txt"
        cmake = (REPO / rel).read_text(encoding="utf-8").replace("\r\n", "\n")
        guard = "if (NOT NX_PRODUCT_BUILD)\n    target_sources(main PRIVATE src/systems/CheckedOutputPwmTester"
        mutant = cmake.replace(guard, guard.replace("NOT NX_PRODUCT_BUILD", "TRUE"))
        self.assertNotEqual(mutant, cmake, "mutant does not apply (fault of this test)")
        self.assertNotEqual(check_cmake_guard(mutant, CMAKE_GUARDED[rel]), [])
        files = _real_guard_files()
        for rel, old in (("platforms/s32k1xx/port/main/include/systems/BspSystem.h",
                          "#ifndef NX_PRODUCT_BUILD\n    /* Console \"pwm\""),
                         ("platforms/s32k1xx/port/main/include/systems/BspSystem.h",
                          "#ifndef NX_PRODUCT_BUILD // SYS-162\n    ::console::AsyncCommandWrapper _asyncOutputPwmTester"),
                         ("platforms/s32k1xx/port/main/src/systems/BspSystem.cpp",
                          "#ifndef NX_PRODUCT_BUILD // SYS-162\n, _asyncOutputPwmTester")):
            with self.subTest(mutant=old[:50]):
                text = files[rel].replace("\r\n", "\n")
                unguarded = dict(files)
                unguarded[rel] = text.replace(old, old.replace("#ifndef NX_PRODUCT_BUILD", "#if 1", 1), 1)
                self.assertNotEqual(unguarded[rel], text, "mutant does not apply (fault of this test)")
                self.assertNotEqual(check_guards(unguarded), [])

    def test_development_code_guarded(self):
        self.assertEqual(_absent_nda(check_guards(_real_guard_files())), [])

    def test_service_commands_not_in_the_product(self):
        """SYS-119 (CX-9, N7): the service write commands have markers without test option, each with the help text of
        its console group; the counter check skips a marker whose group is not in the image (module not in the
        project) and keeps it when the group is there; guards in the sources, "out" product table under #ifdef."""
        commands = {m.command: m for m in product.MARKERS}
        for name in ("sbc write", "sbc pn", "<gd> write", "<cmu> write", "can send", "i2c rd|wr", "out set",
                     "dem clear|cycle|setting"):
            with self.subTest(command=name):
                self.assertIn(name, commands)
                self.assertIsNone(commands[name].option)
                self.assertIsNotNone(commands[name].group)
        cmu = commands["<cmu> write"]
        without_cmu = b"Trigger a hard fault\0UJA113x system basis chip\0"
        self.assertNotIn(cmu, product.applicable(without_cmu))
        self.assertIn(commands["sbc write"], product.applicable(without_cmu))
        self.assertIn(commands["safety mpu|wdg|prt|dst|est"], product.applicable(b""), "marker without group")
        with_cmu = without_cmu + b"state, faults, balancing, counters\0"
        hits = product.find([], with_cmu)
        self.assertIn(cmu, product.missing(hits, product.applicable(with_cmu)),
                      "group in the image, command missing: the counter check must name it")
        img = _image([(".rodata", SHT_PROGBITS, SHF_ALLOC,
                       b"send a standard frame if the transceiver is ready (param: [<bus>] <id>)\0"
                       b"%d : %s -> refused: safe_off output, switched by its driver only\r\n\0"),
                      (".debug_str", SHT_PROGBITS, 0, b"[<dtc>] clears every DTC\0")], [])
        self.assertEqual(sorted(h.marker.command for h in product.find(*product.image_parts(img))),
                         ["can send", "out set"])
        files = {rel: (REPO / rel).read_text(encoding="utf-8") for rel in PRODUCT_ONLY}
        self.assertEqual(check_product_only(files), [])
        rel = "platforms/s32k1xx/port/main/src/systems/SafeOffOutputTester.cpp"
        text = files[rel].replace("\r\n", "\n")
        mutant = dict(files)
        mutant[rel] = text.replace("#ifdef NX_PRODUCT_BUILD\n    if (idx == SET_COMMAND)", "#if 1\n    if (idx == "
                                   "SET_COMMAND)", 1)
        self.assertNotEqual(mutant[rel], text, "mutant does not apply (fault of this test)")
        self.assertNotEqual(check_product_only(mutant), [])
        guards = _real_guard_files()
        for rel, old in (("platforms/s32k1xx/port/main/src/systems/CanSystem.cpp",
                          "#ifndef NX_PRODUCT_BUILD // SYS-119\n        case ID_SEND:"),
                         ("ecu/application/src/systems/RteSystem.cpp",
                          "#ifndef NX_PRODUCT_BUILD // SYS-119\n        case DEM_ID_CLEAR:"),
                         ("platforms/s32k1xx/port/main/src/systems/SbcSystem.cpp",
                          "#ifndef NX_PRODUCT_BUILD // SYS-119\n        case ID_WRITE:")):
            with self.subTest(mutant=f"{rel}: {old[-20:]}"):
                text = guards[rel].replace("\r\n", "\n")
                unguarded = dict(guards)
                unguarded[rel] = text.replace(old, old.replace("#ifndef NX_PRODUCT_BUILD", "#if 1", 1), 1)
                self.assertNotEqual(unguarded[rel], text, "mutant does not apply (fault of this test)")
                self.assertNotEqual(check_guards(unguarded), [])

    def test_built_in_faults_are_detected(self):
        cmake = PRODUCT_CMAKE.read_text(encoding="utf-8")
        options = port_test_options()
        mutants = {
            "option missing from the list": cmake.replace("    NX_GD_ENABLE_COMMAND\n", "", 1),
            "unknown option in the list": cmake.replace("    NX_GD_ENABLE_COMMAND\n",
                                                        "    NX_GD_ENABLE_COMMAND\n    NX_OLD_COMMAND\n", 1),
        }
        for name, text in mutants.items():
            with self.subTest(mutant=name):
                self.assertNotEqual(text, cmake, f"mutant '{name}' does not apply (fault of this test)")
                self.assertNotEqual(check_inventory(options, text), [], name)
        # every marker of one option missing (NVM-17: one option may bring several commands)
        last = product.MARKERS[-1].option
        self.assertIsNotNone(last, "the last marker has a test-build option (fault of this test)")
        self.assertNotEqual(check_inventory(options, cmake, tuple(m for m in product.MARKERS if m.option != last)),
                            [], "marker missing")
        self.assertNotEqual(check_inventory(options | {"NX_NEW_TEST_COMMAND"}, cmake), [], "new port option")
        for name, text in {"no definition": cmake.replace("add_compile_definitions(NX_PRODUCT_BUILD=1)", ""),
                           "no refusal": cmake.replace("FATAL_ERROR", "STATUS")}.items():
            with self.subTest(mutant=name):
                self.assertNotEqual(text, cmake, f"mutant '{name}' does not apply (fault of this test)")
                self.assertNotEqual(check_product_cmake(text), [], name)
        root = ROOT_CMAKE.read_text(encoding="utf-8")
        include = 'include("${NX_ROOT}/cmake/NxProductBuild.cmake")'
        late = root.replace(include, "") + "\n" + include + "\n"
        self.assertNotEqual(check_root_include(late), [])
        self.assertNotEqual(check_root_include(root.replace(include, "")), [])
        default_on = root.replace('(SYS-74)" OFF)', '(SYS-74)" ON)')
        self.assertNotEqual(default_on, root, "mutant 'default ON' does not apply (fault of this test)")
        self.assertNotEqual(check_root_include(default_on), [])
        files = _real_guard_files()
        rel = "platforms/s32k1xx/port/main/src/systems/SbcSystem.cpp"
        unguarded = dict(files)
        unguarded[rel] = files[rel].replace("#ifndef NX_PRODUCT_BUILD // SYS-74: fault injection", "#if 1 //", 1)
        self.assertNotEqual(unguarded[rel], files[rel], "mutant does not apply (fault of this test)")
        self.assertNotEqual(check_guards(unguarded), [])
        cc = (REPO / "ecu/consoleCommands/CMakeLists.txt").read_text(encoding="utf-8")
        self.assertNotEqual(check_consolecommands_cmake(cc.replace("if (NOT NX_PRODUCT_BUILD)", "if (TRUE)")), [])


if __name__ == "__main__":
    unittest.main()
