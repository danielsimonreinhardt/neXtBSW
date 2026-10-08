"""SYS-125: in the linked application boardInit() switches the low-voltage detect reset on.

The host test (low-voltage-detect-reset) proves the function on a register model; no host test can see whether the
target calls it, with which register and when. This test reads the image:

1. boardInit() stores the byte 0x10 (LVDRE on, LVDIE off, no LVDACK) to PMC_LVDSC1 (0x4007D000, [RM13] 41.7.1.1),
   as a byte store (the register is 8 bits wide);
2. that store is the only store to the PMC in boardInit(), and it comes before the first call after the watchdog is
   disabled (i.e. before hardfault::prepareDump() and the clock set-up - the earliest point after the watchdog);
3. the reset handler calls boardInit() (startUp.S), so every start runs it before main().

Run: .venv/Scripts/python platforms/s32k1xx/port/main/test/test_low_voltage_detect_reset_elf.py
     (ELF: NX_APP_ELF=<app.ecu.elf> or the demo build of tools/test/build_dirs.py; arm-none-eabi-objdump from PATH
     or NX_ARM_GCC_BIN)
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
REPO = HERE.parents[4]

GCC_PATH = os.pathsep.join(p for p in (os.environ.get("NX_ARM_GCC_BIN"), os.environ.get("PATH")) if p)
ARM_OBJDUMP = shutil.which("arm-none-eabi-objdump", path=GCC_PATH)

PMC_LVDSC1 = 0x4007D000
PMC_END = 0x4007E000
LVDSC1_VALUE = 0x10

INSN = re.compile(r"^\s*([0-9a-f]+):\s+(?:[0-9a-f]{4}(?: [0-9a-f]{4})?|[0-9a-f]{8})\s+(\S+)\s*(.*)$")
WORD = re.compile(r"^\s*([0-9a-f]+):\s+([0-9a-f]{8})\s+\.word\s+0x([0-9a-f]+)")
LITERAL = re.compile(r"^(r\d+|ip|lr), \[pc, #\d+\]\s*[;@]\s*\(([0-9a-f]+)")
MOV_IMM = re.compile(r"^(r\d+|ip|lr), #(\d+)")
STORE = re.compile(r"^(r\d+|ip|lr), \[(r\d+|ip|lr)(?:, #(\d+))?\]")


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


def disassemble(elf: Path, symbol: str) -> list[str]:
    out = subprocess.run([ARM_OBJDUMP, "-d", f"--disassemble={symbol}", str(elf)], capture_output=True, text=True,
                         check=True).stdout
    return out.splitlines()


def pmc_stores(lines: list[str]):
    """(address, mnemonic, target, value or None) of every store into the PMC, plus the addresses of the calls.

    Tracks registers loaded from the literal pool and small immediates in straight-line order - enough for the
    compiler's code of a single register write; a value it cannot follow is None (and fails the test)."""
    words = {}
    for line in lines:
        m = WORD.match(line)
        if m:
            words[int(m.group(1), 16)] = int(m.group(3), 16)
    regs: dict[str, int | None] = {}
    stores, calls = [], []
    for line in lines:
        m = INSN.match(line)
        if not m or m.group(2) == ".word":
            continue
        addr, mnemonic, operands = int(m.group(1), 16), m.group(2), m.group(3)
        base = mnemonic.split(".")[0]
        if base in ("bl", "blx"):
            calls.append(addr)
            regs = {r: v for r, v in regs.items() if r not in ("r0", "r1", "r2", "r3", "ip", "lr")}
            continue
        lit = LITERAL.match(operands)
        if base == "ldr" and lit:
            regs[lit.group(1)] = words.get(int(lit.group(2), 16))
            continue
        imm = MOV_IMM.match(operands)
        if base in ("mov", "movs", "movw") and imm:
            regs[imm.group(1)] = int(imm.group(2))
            continue
        store = STORE.match(operands)
        if base.startswith("str") and store:
            target = regs.get(store.group(2))
            if target is not None:
                target += int(store.group(3) or 0)
                if PMC_LVDSC1 <= target < PMC_END:
                    stores.append((addr, base, target, regs.get(store.group(1))))
            continue
        # any other instruction that writes a register invalidates what we knew about it
        first = operands.split(",")[0].strip()
        if first in regs:
            regs[first] = None
    return stores, calls


@unittest.skipIf(ARM_OBJDUMP is None, "arm-none-eabi-objdump not found (PATH or NX_ARM_GCC_BIN)")
class LowVoltageDetectResetElfTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.elf = application_elf()
        if cls.elf is None or not cls.elf.is_file():
            raise unittest.SkipTest("application ELF not built (NX_APP_ELF or tools/test -k target-demo)")

    def test_board_init_writes_lvdsc1(self):
        lines = disassemble(self.elf, "boardInit")
        self.assertTrue(any("<boardInit>:" in line for line in lines), "boardInit not found in the image")
        stores, calls = pmc_stores(lines)
        self.assertEqual(len(stores), 1, f"expected exactly one store to the PMC in boardInit, found {stores}")
        addr, mnemonic, target, value = stores[0]
        self.assertEqual(target, PMC_LVDSC1, "the store goes to PMC_LVDSC1")
        self.assertEqual(mnemonic, "strb", "PMC_LVDSC1 is an 8-bit register")
        self.assertEqual(value, LVDSC1_VALUE, "LVDRE on, LVDIE off, LVDACK not written")
        self.assertGreaterEqual(len(calls), 2, "boardInit calls the watchdog and the dump preparation")
        self.assertLess(calls[0], addr, "the watchdog is disabled first")
        self.assertLess(addr, calls[1], "the LVD reset is on before the next call (dump, clock)")

    def test_reset_handler_calls_board_init(self):
        lines = disassemble(self.elf, "Reset_Handler")
        self.assertTrue(any(re.search(r"\bbl\w*\s+[0-9a-f]+ <boardInit>", line) for line in lines),
                        "Reset_Handler does not call boardInit")

    def test_parser_detects_breakage(self):
        """The tracker must see a wrong value, a word store and a missing store."""
        good = ["   10:\t4b01      \tldr\tr3, [pc, #4]\t; (18 <boardInit+0x8>)",
                "   12:\t2210      \tmovs\tr2, #16",
                "   14:\t701a      \tstrb\tr2, [r3, #0]",
                "   18:\t4007d000 \t.word\t0x4007d000"]
        stores, _ = pmc_stores(good)
        self.assertEqual(stores, [(0x14, "strb", PMC_LVDSC1, 16)])
        stores, _ = pmc_stores([good[0], good[1].replace("#16", "#48"), good[2], good[3]])
        self.assertEqual(stores[0][3], 48)
        stores, _ = pmc_stores([good[0], good[1], good[2].replace("strb", "str"), good[3]])
        self.assertEqual(stores[0][1], "str")
        stores, _ = pmc_stores([good[0], good[1], good[3]])
        self.assertEqual(stores, [])


if __name__ == "__main__":
    unittest.main()
