# SPDX-License-Identifier: Apache-2.0
"""SYS-73 (SM-55): the RAM ECC latent-fault test on the linked application image.

Two things the host test cannot see:
1. Placement of the reserved words: nxEccLatentTestWordFirst in the hot bss at the start of the application RAM
   (on a derivative whose application RAM begins below 0x20000000: in SRAM_L, ECC channel 0 of the S32K14x),
   nxEccLatentTestWordLast in the no-init RAM at its end (SRAM_U), both outside the MPU protected .mpu_bss and 4 bytes
   long. A word in the wrong array would make the test fail at every start (ECC_TEST_NOT_COVERED) - found here at
   build time instead.
2. The injected read is register-only: in eimInjectedRead() the instructions from enabling the EIM channel to the
   barrier after disabling it touch neither the stack nor a literal, and the core masks interrupts first (Cortex-M4:
   FAULTMASK; Cortex-M0+: PRIMASK).

Run: .venv/Scripts/python -m unittest platforms/s32k1xx/port/safety/safeMemory/test/test_ecc_latent_test_elf.py
     (NX_APP_ELF=<app.ecu.elf> or a demo build found by tools/test/build_dirs.py; arm-none-eabi-nm/objdump from PATH
     or NX_ARM_GCC_BIN; skipped without them)
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[5]

GCC_PATH = os.pathsep.join(p for p in (os.environ.get("NX_ARM_GCC_BIN"), os.environ.get("PATH")) if p)
ARM_NM = shutil.which("arm-none-eabi-nm", path=GCC_PATH)
ARM_OBJDUMP = shutil.which("arm-none-eabi-objdump", path=GCC_PATH)

SRAM_U_START = 0x20000000
READ = "safety::safe_memory::eimInjectedRead("


def application_elf() -> Path | None:
    env = os.environ.get("NX_APP_ELF")
    if env:
        return Path(env)
    spec = importlib.util.spec_from_file_location("nx_build_dirs", REPO / "tools/test/build_dirs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    elf = Path(module.demo_build_dir(REPO)) / "ecu/application/RelWithDebInfo/app.ecu.elf"
    return elf if elf.is_file() else None


def symbols(elf: Path) -> dict[str, tuple[int, int]]:
    """Demangled symbol -> (address, size)."""
    out = subprocess.run([ARM_NM, "-C", "-S", str(elf)], capture_output=True, text=True, check=True).stdout
    result: dict[str, tuple[int, int]] = {}
    for line in out.splitlines():
        parts = line.split(maxsplit=3)
        if len(parts) == 4 and re.fullmatch(r"[0-9a-f]+", parts[1]):
            result[parts[3]] = (int(parts[0], 16), int(parts[1], 16))
        elif len(parts) == 3:
            result.setdefault(parts[2], (int(parts[0], 16), 0))
    return result


def instructions(disassembly: list[str]) -> list[str]:
    return [line.split("\t", 2)[-1].strip() for line in disassembly if re.match(r"\s*[0-9a-f]+:\t", line)]


def injection_window(disassembly: list[str]) -> list[str]:
    """Instructions from the store of the channel enable to the barrier after the read-back of the disable: the
    'str' right before 'movs rT, #1' up to the third 'dsb' after it. Empty if the sequence is not found."""
    ops = instructions(disassembly)
    for i, op in enumerate(ops):
        if re.fullmatch(r"movs\s+r\d+, #1", op) and i > 0 and ops[i - 1].startswith("str") \
                and i + 1 < len(ops) and ops[i + 1].startswith("str"):
            barriers = 0
            for j in range(i, len(ops)):
                if ops[j].startswith("dsb"):
                    barriers += 1
                    if barriers == 3:
                        return ops[i - 1:j + 1]
            return []
    return []


class InjectionWindowRule(unittest.TestCase):
    """The window rule on disassembly text (no tool needed)."""

    GOOD = """
   26b56:\t606b      \tstr\tr3, [r5, #4]
   26b58:\t2601      \tmovs\tr6, #1
   26b5a:\t602e      \tstr\tr6, [r5, #0]
   26b5c:\t682e      \tldr\tr6, [r5, #0]
   26b5e:\tf3bf 8f4f \tdsb\tsy
   26b62:\t6808      \tldr\tr0, [r1, #0]
   26b64:\tf3bf 8f4f \tdsb\tsy
   26b68:\t2600      \tmovs\tr6, #0
   26b6a:\t602e      \tstr\tr6, [r5, #0]
   26b6c:\t606e      \tstr\tr6, [r5, #4]
   26b6e:\t686e      \tldr\tr6, [r5, #4]
   26b70:\tf3bf 8f4f \tdsb\tsy
   26b74:\t6154      \tstr\tr4, [r2, #20]
""".splitlines()

    def test_window_is_found(self):
        window = injection_window(self.GOOD)
        self.assertEqual(len(window), 12)
        self.assertTrue(window[0].startswith("str"))
        self.assertTrue(window[-1].startswith("dsb"))

    def test_spill_in_the_window_is_seen(self):
        bad = [line.replace("ldr\tr0, [r1, #0]", "ldr\tr0, [sp, #4]") for line in self.GOOD]
        self.assertTrue(any("sp" in op for op in injection_window(bad)))


@unittest.skipUnless(ARM_NM and ARM_OBJDUMP, "arm-none-eabi-nm/objdump not found (PATH or NX_ARM_GCC_BIN)")
class ApplicationImageTest(unittest.TestCase):
    def setUp(self):
        elf = application_elf()
        if elf is None:
            self.skipTest("no application ELF (NX_APP_ELF or a demo build, tools/test/build_dirs.py)")
        self.elf = elf
        self.sym = symbols(elf)

    def address(self, name: str) -> int:
        self.assertIn(name, self.sym, f"{name} missing in {self.elf}")
        return self.sym[name][0]

    def test_reserved_words(self):
        first, first_size = self.sym.get("nxEccLatentTestWordFirst", (None, 0))
        last, last_size = self.sym.get("nxEccLatentTestWordLast", (None, 0))
        self.assertIsNotNone(first, "nxEccLatentTestWordFirst missing")
        self.assertIsNotNone(last, "nxEccLatentTestWordLast missing")
        self.assertEqual((first_size, last_size), (4, 4))
        ram_start = self.address("__hard_fault_ram_start")
        ram_end = self.address("__hard_fault_ram_end")
        # first word: hot bss, at the start of the RAM; in SRAM_L when the application RAM starts there
        self.assertTrue(self.address("__HOT_BSS_START") <= first < self.address("__HOT_BSS_END"), hex(first))
        if ram_start < SRAM_U_START:
            self.assertLess(first, SRAM_U_START, "first word not in SRAM_L")
        # last word: no-init RAM at the end of the RAM, in SRAM_U
        self.assertTrue(self.address("__NOINIT_DATA") <= last < ram_end, hex(last))
        self.assertGreaterEqual(last, SRAM_U_START, "last word not in SRAM_U")
        for word in (first, last):
            self.assertFalse(self.address("__MPU_BSS_START") <= word < self.address("__MPU_BSS_END"),
                             f"{word:#x} in the MPU protected RAM")
            self.assertEqual(word % 4, 0)

    def test_injected_read_is_register_only(self):
        start, size = self.sym.get(next((s for s in self.sym if s.startswith(READ)), ""), (None, 0))
        self.assertIsNotNone(start, "eimInjectedRead missing")
        out = subprocess.run([ARM_OBJDUMP, "-d", "-C", f"--start-address={start:#x}",
                              f"--stop-address={start + size:#x}", str(self.elf)],
                             capture_output=True, text=True, check=True).stdout
        lines = out.splitlines()
        window = injection_window(lines)
        self.assertEqual(len(window), 12, "\n".join(window) or out)
        for op in window:
            self.assertNotRegex(op, r"\bsp\b|\[pc|\bpush\b|\bpop\b|\bbl\b", f"RAM access in the window: {op}")
        ops = instructions(lines)
        start_of_window = next(i for i in range(len(ops)) if ops[i:i + len(window)] == window)
        before = "\n".join(ops[:start_of_window])
        self.assertRegex(before, r"msr\s+(FAULTMASK|PRIMASK)|cpsid", "interrupts not masked before the window")


if __name__ == "__main__":
    unittest.main()
