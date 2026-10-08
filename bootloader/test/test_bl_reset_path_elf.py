"""BL-33 / BL-34: the reset path of the linked bootloader (family s32k1xx).

The host tests (bootloader/test/host/test_bl_startup_window_host.py, test_bl_lvd_host.py) prove the rule and the
unit; only the image shows what the target runs and when. This test reads bootloader.elf of the demo build:

BL-34 (low-voltage detect reset, SYS-125):
1. Reset_Handler calls bl_system_init() first, before the RAM is initialized;
2. bl_system_init() writes the watchdog and ends with the call of bl_lvd_reset_enable();
3. bl_lvd_reset_enable() stores the byte 0x10 to PMC_LVDSC1 (0x4007D000) - exactly one store to the PMC, a byte
   store, no call; the same check as the application's (platforms/s32k1xx/port/main/test/
   test_low_voltage_detect_reset_elf.py, whose tracker is reused);
4. nothing of the C++ runtime is linked (the unit is C++ only to use the port's header).

BL-33 (startup window after the watchdog fast test, SYS-149):
5. bl_init() asks hal_reset_skips_startup_window() after hal_boot_request_consume();
6. the hook reads RCM (0x4007F000, SRS at +8) and WDOG (0x40052000, CS at +0) and stores nothing.

Run: .venv/Scripts/python -m unittest bootloader/test/test_bl_reset_path_elf.py
     (ELF: BL_ELF=<bootloader.elf> or the demo build of tools/test/build_dirs.py; arm-none-eabi-objdump/nm from PATH
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
REPO = HERE.parents[1]

GCC_PATH = os.pathsep.join(p for p in (os.environ.get("NX_ARM_GCC_BIN"), os.environ.get("PATH")) if p)
ARM_OBJDUMP = shutil.which("arm-none-eabi-objdump", path=GCC_PATH)
ARM_NM = shutil.which("arm-none-eabi-nm", path=GCC_PATH)

RCM_BASE, WDOG_BASE = 0x4007F000, 0x40052000
RCM_SRS_OFFSET = 8


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


APP_ELF_TEST = _load("nx_lvd_elf", REPO / "platforms/s32k1xx/port/main/test/test_low_voltage_detect_reset_elf.py")


def bootloader_elf() -> Path | None:
    env = os.environ.get("BL_ELF")
    if env:
        return Path(env)
    build = _load("nx_build_dirs", REPO / "tools/test/build_dirs.py").demo_build_dir(REPO)
    elf = Path(build) / "bootloader/RelWithDebInfo/bootloader.elf"
    return elf if elf.is_file() else None


def disassemble(elf: Path, symbol: str) -> list[str]:
    out = subprocess.run([ARM_OBJDUMP, "-d", f"--disassemble={symbol}", str(elf)], capture_output=True, text=True,
                         check=True).stdout
    lines = out.splitlines()
    start = next((i for i, line in enumerate(lines) if f"<{symbol}>:" in line), None)
    if start is None:
        return []
    body = []
    for line in lines[start + 1:]:
        if not line.strip() or line.startswith("Disassembly of section"):
            break
        body.append(line)
    return body


def calls(lines: list[str]) -> list[str]:
    """Targets of bl/blx/b.w <symbol> in order (a tail call is a b.w to another function)."""
    found = []
    for line in lines:
        m = re.search(r"\t(bl|blx|b\.w|b)\s+[0-9a-f]+ <([A-Za-z_]\w*)>", line)
        if m:
            found.append(m.group(2))
    return found


def literals(lines: list[str]) -> list[int]:
    return [int(m.group(3), 16) for m in (APP_ELF_TEST.WORD.match(line) for line in lines) if m]


@unittest.skipIf(ARM_OBJDUMP is None, "arm-none-eabi-objdump not found (PATH or NX_ARM_GCC_BIN)")
class ResetPathElfTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.elf = bootloader_elf()
        if cls.elf is None or not cls.elf.is_file():
            raise unittest.SkipTest("bootloader ELF not built (BL_ELF or tools/test -k target-demo)")

    def test_reset_handler_calls_system_init_before_the_ram_init(self):
        lines = disassemble(self.elf, "Reset_Handler")
        self.assertTrue(lines, "Reset_Handler not found")
        self.assertEqual(calls(lines)[0], "bl_system_init")
        # the labels copy_data/zero_bss end the symbol: the pointer loads of the RAM init follow the call
        first_call = next(i for i, line in enumerate(lines) if "<bl_system_init>" in line)
        first_load = next(i for i, line in enumerate(lines) if re.search(r"\tldr", line))
        self.assertLess(first_call, first_load, "bl_system_init runs before .data/.bss are written")

    def test_system_init_ends_with_the_lvd_reset(self):
        lines = disassemble(self.elf, "bl_system_init")
        self.assertTrue(lines, "bl_system_init not found")
        self.assertEqual(calls(lines), ["bl_lvd_reset_enable"], "one call, the LVD reset")
        self.assertIn(WDOG_BASE, literals(lines), "the watchdog is written first")
        code = [line for line in lines if APP_ELF_TEST.INSN.match(line) and ".word" not in line]
        self.assertIn("<bl_lvd_reset_enable>", code[-1], "the LVD reset is the last step")

    def test_lvd_reset_stores_lvdre(self):
        lines = disassemble(self.elf, "bl_lvd_reset_enable")
        self.assertTrue(lines, "bl_lvd_reset_enable not found")
        stores, called = APP_ELF_TEST.pmc_stores(lines)
        self.assertEqual(called, [], "no call")
        self.assertEqual(len(stores), 1, f"exactly one store to the PMC, found {stores}")
        _, mnemonic, target, value = stores[0]
        self.assertEqual(target, APP_ELF_TEST.PMC_LVDSC1)
        self.assertEqual(mnemonic, "strb", "PMC_LVDSC1 is an 8-bit register")
        self.assertEqual(value, APP_ELF_TEST.LVDSC1_VALUE, "LVDRE on, LVDIE off, LVDACK not written")

    @unittest.skipIf(ARM_NM is None, "arm-none-eabi-nm not found")
    def test_no_cxx_runtime(self):
        out = subprocess.run([ARM_NM, str(self.elf)], capture_output=True, text=True, check=True).stdout
        names = [line.split()[-1] for line in out.splitlines() if line.strip()]
        cxx = [n for n in names if n.startswith(("_Z", "__cxa", "__gxx", "_ZTV", "__aeabi_atexit"))]
        self.assertEqual(cxx, [], "the C++ unit must not pull in the C++ runtime")

    def test_init_asks_the_hook_after_the_boot_request(self):
        order = calls(disassemble(self.elf, "bl_init"))
        self.assertIn("hal_reset_skips_startup_window", order)
        self.assertLess(order.index("hal_boot_request_consume"), order.index("hal_reset_skips_startup_window"))

    def test_hook_reads_rcm_srs_and_wdog_cs(self):
        lines = disassemble(self.elf, "hal_reset_skips_startup_window")
        self.assertTrue(lines, "hal_reset_skips_startup_window not found")
        self.assertIn(RCM_BASE, literals(lines))
        self.assertIn(WDOG_BASE, literals(lines))
        self.assertTrue(any(re.search(rf"\tldr\s+r\d+, \[r\d+, #{RCM_SRS_OFFSET}\]", line) for line in lines),
                        "RCM_SRS is read")
        self.assertFalse(any(re.search(r"\tstr", line) for line in lines), "the hook writes nothing")
        self.assertEqual(calls(lines), [])


if __name__ == "__main__":
    unittest.main()
