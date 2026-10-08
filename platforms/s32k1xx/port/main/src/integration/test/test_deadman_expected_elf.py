"""SYS-48: in the linked application the deadman check of the safety task gets the deadman's real configuration.

SYS-41 added the configuration re-check of the halt deadman (SM-06, safeSupervisionCheck/LpitDeadmanCheck.cpp) with a
weak default of safety::supervision::deadmanExpectedConfiguration() that returns nullptr - "no deadman, nothing to
check". The strong definition belongs next to the strong LPIT0_Ch1_IRQHandler (integration/DebugHaltGuardSystem.h).
Until it existed, the weak default won on the target and the deadman was never re-checked; no host test can see which
definition the linker picks, so this test looks at the image:

1. exactly one definition, global and strong (nm type T, not W/w/V) - the weak default is gone;
2. it returns the address of integration::LpitHaltDeadman::EXPECTED_CONFIGURATION (the literal it loads) and calls
   nothing;
3. that object lies in a read-only section (flash, not the RAM a wild write could reach) and holds, as 4 x 32 bit:
   channel 1, TVAL = LPIT clock x 10 ms - 1, the vector number of LPIT0_Ch1 and the level one above the syscall
   boundary. The expected values come from the generated files of the same build, not from the C++ code: the LPIT
   clock from bsp/clock/boardClock.h, the vector number from the derivative's vector list startup/nxIrqVectors.inc,
   the boundary from interrupts/interruptPriorities.h;
4. SafetyManager::checkSupervisionPeripherals() calls this definition.

A project without a power stage has no deadman: no strong LPIT0_Ch1_IRQHandler, the weak default must stay - then
the test checks exactly that (nullptr default kept, no EXPECTED_CONFIGURATION in the image).

Run: .venv/Scripts/python platforms/s32k1xx/port/main/src/integration/test/test_deadman_expected_elf.py
     (ELF: NX_APP_ELF=<app.ecu.elf> or the demo build of tools/test/build_dirs.py; arm-none-eabi-nm/objdump from PATH
     or NX_ARM_GCC_BIN)
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import struct
import subprocess
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[6]

GCC_PATH = os.pathsep.join(p for p in (os.environ.get("NX_ARM_GCC_BIN"), os.environ.get("PATH")) if p)
ARM_NM = shutil.which("arm-none-eabi-nm", path=GCC_PATH)
ARM_OBJDUMP = shutil.which("arm-none-eabi-objdump", path=GCC_PATH)

EXPECTED_FN = "_ZN6safety11supervision28deadmanExpectedConfigurationEv"
CONFIG = "_ZN11integration15LpitHaltDeadman22EXPECTED_CONFIGURATIONE"
CHECK_FN = "_ZN6safety13SafetyManager27checkSupervisionPeripheralsEv"
DEADMAN_VECTOR = "LPIT0_Ch1_IRQHandler"
TIMEOUT_MS = 10  # LpitHaltDeadman::TIMEOUT_MS, a constant of the mechanism

INSN = re.compile(r"^\s*([0-9a-f]+):\s+(?:[0-9a-f]{4}(?: [0-9a-f]{4})?|[0-9a-f]{8})\s+(\S+)\s*(.*)$")
LITERAL = re.compile(r"\[pc, #\d+\]\s*[;@]\s*\(([0-9a-f]+)")
BRANCH_TARGET = re.compile(r"^([0-9a-f]+)\b")
SHF_WRITE, SHF_ALLOC = 0x1, 0x2


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def application_elf() -> Path | None:
    env = os.environ.get("NX_APP_ELF")
    if env:
        return Path(env)
    build = _load("nx_build_dirs", REPO / "tools/test/build_dirs.py").demo_build_dir(REPO)
    elf = Path(build) / "ecu/application/RelWithDebInfo/app.ecu.elf"
    return elf if elf.is_file() else None


def build_dir_of(elf: Path) -> Path:
    """<build>/ecu/application/<config>/app.ecu.elf -> <build>."""
    return elf.parents[3]


class Elf:
    """Loaded sections of a 32-bit little-endian ELF (no pyelftools needed)."""

    def __init__(self, path: Path):
        self.data = path.read_bytes()
        assert self.data[:4] == b"\x7fELF" and self.data[4] == 1 and self.data[5] == 1, f"{path}: no ELF32 LE"
        shoff, = struct.unpack_from("<I", self.data, 0x20)
        shentsize, shnum = struct.unpack_from("<HH", self.data, 0x2E)
        self.sections = []
        for i in range(shnum):
            _, stype, flags, addr, offset, size = struct.unpack_from("<IIIIII", self.data, shoff + i * shentsize)
            if flags & SHF_ALLOC and stype != 8:  # loaded, not NOBITS
                self.sections.append((addr, size, offset, flags))

    def section(self, address: int, size: int):
        for addr, length, offset, flags in self.sections:
            if addr <= address and address + size <= addr + length:
                return offset + address - addr, flags
        return None

    def read(self, address: int, size: int) -> bytes | None:
        found = self.section(address, size)
        return None if found is None else self.data[found[0]:found[0] + size]


def nm_symbols(elf: Path) -> list[tuple[int, str, str]]:
    out = subprocess.run([ARM_NM, str(elf)], capture_output=True, text=True, check=True).stdout
    table = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3:
            table.append((int(parts[0], 16), parts[1], parts[2]))
    return table


def disassemble(elf: Path, function: str) -> list[tuple[int, str, str]]:
    out = subprocess.run([ARM_OBJDUMP, "-d", f"--disassemble={function}", str(elf)],
                         capture_output=True, text=True, check=True).stdout
    return [(int(m.group(1), 16), m.group(2), m.group(3)) for m in map(INSN.match, out.splitlines()) if m]


def generated_expectation(build: Path) -> dict[str, int]:
    """The deadman configuration as the generated files of the build define it (independent of the C++ code)."""
    board = build / "generated/board"
    clock = re.search(r"#define\s+NX_CLOCK_LPIT_HZ\s+(\d+)U?",
                      (board / "bsp/clock/boardClock.h").read_text(encoding="utf-8"))
    vector = re.search(rf"NX_IRQ_VECTOR\({DEADMAN_VECTOR}\)\s*/\*\s*(\d+)",
                       (board / "startup/nxIrqVectors.inc").read_text(encoding="utf-8"))
    boundary = re.search(r"#define\s+NX_IRQ_MAX_SYSCALL_LEVEL\s+(\d+)U?",
                         (board / "interrupts/interruptPriorities.h").read_text(encoding="utf-8"))
    assert clock and vector and boundary, f"{board}: LPIT clock, deadman vector or syscall boundary not found"
    hz, level = int(clock.group(1)), int(boundary.group(1))
    return {"channel": 1, "timerValue": hz // 1000 * TIMEOUT_MS - 1, "irq": int(vector.group(1)),
            "irqPriority": level - 1 if level > 0 else 0}


def findings(symbols: list[tuple[int, str, str]], function_body, read_config, expectation, checker_body) -> list[str]:
    """Everything that is wrong with the deadman expectation of an image with a deadman (empty = as it should be).

    function_body(name) -> instructions, read_config(address) -> (bytes or None, writable), both injectable so the
    rule itself can be tested against built-in faults."""
    found: list[str] = []
    definitions = [s for s in symbols if s[2] == EXPECTED_FN]
    if len(definitions) != 1:
        return [f"{EXPECTED_FN}: {len(definitions)} definitions in the image, expected exactly 1"]
    address, kind, _ = definitions[0]
    if kind != "T":
        found.append(f"deadmanExpectedConfiguration() is '{kind}', not a strong global definition (T): "
                     "the weak default of safeSupervisionCheck (nullptr) won - the deadman is not re-checked")
    configs = [s for s in symbols if s[2] == CONFIG]
    if len(configs) != 1:
        return found + [f"{CONFIG}: {len(configs)} objects in the image, expected exactly 1"]
    config_address = configs[0][0]
    body = function_body(EXPECTED_FN)
    literals = {int(m.group(1), 16) for _, _, ops in body for m in [LITERAL.search(ops)] if m}
    loaded = set()
    for literal in literals:
        data, _ = read_config(literal, 4)
        if data is not None:
            loaded.add(struct.unpack("<I", data)[0])
    if config_address not in loaded:
        found.append(f"deadmanExpectedConfiguration() does not return EXPECTED_CONFIGURATION "
                     f"(0x{config_address:08x}); literals {sorted(hex(v) for v in loaded)}")
    if any(op in ("bl", "blx") for _, op, _ in body):
        found.append("deadmanExpectedConfiguration() calls something - it only returns the constant")
    data, writable = read_config(config_address, 16)
    if data is None:
        found.append(f"EXPECTED_CONFIGURATION at 0x{config_address:08x} is in no loaded section")
    else:
        if writable:
            found.append("EXPECTED_CONFIGURATION lies in a writable section - it belongs in flash")
        values = dict(zip(("channel", "timerValue", "irq", "irqPriority"), struct.unpack("<IIII", data)))
        for key, want in expectation.items():
            if values[key] != want:
                found.append(f"EXPECTED_CONFIGURATION.{key} = {values[key]}, the generated files say {want}")
    calls = [ops for _, op, ops in checker_body if op in ("bl", "blx", "b.w", "b")]
    targets = {int(m.group(1), 16) for ops in calls for m in [BRANCH_TARGET.match(ops)] if m}
    if address not in targets:
        found.append("SafetyManager::checkSupervisionPeripherals() does not call the strong "
                     "deadmanExpectedConfiguration()")
    return found


@unittest.skipUnless(ARM_NM and ARM_OBJDUMP, "arm-none-eabi-nm/objdump not found (PATH or NX_ARM_GCC_BIN)")
class DeadmanExpectationInTheImage(unittest.TestCase):
    def setUp(self):
        self.elf_path = application_elf()
        if self.elf_path is None or not self.elf_path.is_file():
            self.skipTest("no application ELF (NX_APP_ELF or a demo build, tools/test/build_dirs.py)")
        self.elf = Elf(self.elf_path)
        self.symbols = nm_symbols(self.elf_path)
        self.has_deadman = any(s[2] == DEADMAN_VECTOR and s[1] == "T" for s in self.symbols)

    def read(self, address: int, size: int):
        found = self.elf.section(address, size)
        if found is None:
            return None, False
        return self.elf.data[found[0]:found[0] + size], bool(found[1] & SHF_WRITE)

    def image_findings(self) -> list[str]:
        return findings(self.symbols, lambda name: disassemble(self.elf_path, name), self.read,
                        generated_expectation(build_dir_of(self.elf_path)),
                        disassemble(self.elf_path, CHECK_FN))

    def test_strong_definition_wins_and_carries_the_generated_values(self):
        if not self.has_deadman:
            self.skipTest("project without a power stage: no deadman (see the next test)")
        self.assertEqual(self.image_findings(), [])

    def test_project_without_deadman_keeps_the_weak_default(self):
        if self.has_deadman:
            self.skipTest("project with a deadman (see the previous test)")
        self.assertFalse(any(s[2] == CONFIG for s in self.symbols))
        kinds = [s[1] for s in self.symbols if s[2] == EXPECTED_FN]
        self.assertIn(kinds, ([], ["W"]))

    def test_built_in_faults_are_found(self):
        """The rule on the real image with one fault each: weak default wins, wrong object, a value off by one,
        object in RAM, check not wired - each must be reported."""
        if not self.has_deadman:
            self.skipTest("project without a power stage")
        expectation = generated_expectation(build_dir_of(self.elf_path))
        body = lambda name: disassemble(self.elf_path, name)  # noqa: E731
        checker = disassemble(self.elf_path, CHECK_FN)
        weak = [(a, "W" if n == EXPECTED_FN else k, n) for a, k, n in self.symbols]
        self.assertTrue(findings(weak, body, self.read, expectation, checker))
        moved = [(a + 4 if n == CONFIG else a, k, n) for a, k, n in self.symbols]
        self.assertTrue(findings(moved, body, self.read, expectation, checker))
        for key in expectation:
            wrong = dict(expectation, **{key: expectation[key] + 1})
            self.assertTrue(findings(self.symbols, body, self.read, wrong, checker), key)
        in_ram = lambda address, size: (self.read(address, size)[0], True)  # noqa: E731
        self.assertTrue(findings(self.symbols, body, in_ram, expectation, checker))
        self.assertTrue(findings(self.symbols, body, self.read, expectation, []))


if __name__ == "__main__":
    unittest.main()
