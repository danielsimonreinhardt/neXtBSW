"""Host test of the updater's time base without DWT (PLAT-28).

ARMv6-M (Cortex-M0+, e.g. S32K116) has no DWT cycle counter. The family port s32k1xx of the updater counts LPIT0
channel 0 on FIRCDIV2 instead (bootloader/updater/platforms/s32k1xx/cycle_counter/lpit/upd_cycle_counter.h); which
counter a build uses, bootloader/updater/CMakeLists.txt takes from the core architecture of the derivative
(NX_CORE_ARCHITECTURE, tools/nxconfig).

Covered without hardware:
- the LPIT counter against a register model with the vendor register layout (test/host/model): FIRCDIV2 divide by 1
  (FIRCDIV1 untouched), PCC clock source FIRCDIV2 with the PCS lock of the PCC (a source can only change with the gate
  off), LPIT clock gate and MCR[M_CEN] before the channel registers, channel 0 free running from 0xFFFFFFFF, no other
  channel, PCC gate or SCG register touched
- the reading counts up and wraps at 2^32, so upd_micros() keeps the unsigned arithmetic of the DWT counter
- the selection in CMake: ARMv7-M keeps DWT, ARMv6-M gets the LPIT, anything else stops the configuration; neither
  header names a derivative, and the ARMv6-M header does not use DWT/CoreDebug

Run: .venv/Scripts/python -m unittest bootloader/updater/test/test_upd_cycle_counter_host.py
(needs ziglang in the .venv, like bootloader/test/host/test_bl_platform_host.py)
"""

import ctypes
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
UPDATER = HERE.parent
REPO = HERE.parents[2]
VENDOR = REPO / "external" / "openbsw" / "platforms" / "s32k1xx" / "bsp" / "bspMcu" / "include" / "3rdparty" / "nxp"
COUNTERS = UPDATER / "platforms" / "s32k1xx" / "cycle_counter"
LPIT_HEADER = COUNTERS / "lpit" / "upd_cycle_counter.h"
DWT_HEADER = COUNTERS / "dwt" / "upd_cycle_counter.h"

# [RM] S32K1xx Series Reference Manual Rev. 14 (values the test states itself instead of reading the code under test)
FIRCDIV2_SHIFT, FIRCDIV_FIELD = 8, 0x7          # 28.3.15 SCG_FIRCDIV: FIRCDIV2 bits 10-8, FIRCDIV1 bits 2-0
DIVIDE_BY_1 = 1                                  # 001 divide by 1
PCC_CGC, PCC_PCS_SHIFT = 1 << 30, 24             # 29.6 PCC register: CGC bit 30, PCS bits 26-24
PCS_FIRCDIV2 = 3                                 # 27.6 p. 572: LPIT FIRCDIV2_CLK 011
PCS_SOSCDIV2 = 1
M_CEN = 1                                        # 48.4.1.2 MCR[M_CEN]
T_EN, MODE_MASK = 1, 0xC                         # 48.4.1.x TCTRLn: T_EN bit 0, MODE bits 3-2 (00 periodic counter)
SCG_FIRCDIV_WORD = 0x304 // 4


def build(out: Path, header_dir: Path = LPIT_HEADER.parent) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", "ziglang", "cc", "-target", "x86_64-windows-gnu", "-shared", "-O1", "-std=c99",
           "-Wall", "-Wextra", "-Werror", f"-I{HERE / 'host' / 'model'}", f"-I{header_dir}", f"-I{VENDOR}",
           str(HERE / "host" / "upd_cycle_counter_host.c"), "-o", str(out)]
    return subprocess.run(cmd, capture_output=True, text=True)


def without_comments(path: Path) -> str:
    return re.sub(r"/\*.*?\*/|//[^\n]*", "", path.read_text(encoding="utf-8"), flags=re.S)


class LpitCycleCounterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        dll = Path(cls.tmp.name) / "upd_cycle_counter_host.dll"
        result = build(dll)
        assert result.returncode == 0, result.stderr
        cls.lib = ctypes.CDLL(str(dll))
        for name in ("upd_test_reset", "upd_test_start", "upd_test_set_cval"):
            getattr(cls.lib, name).restype = None
        cls.lib.upd_test_reset.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        cls.lib.upd_test_set_cval.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        for name in ("upd_test_count", "upd_test_fircdiv", "upd_test_scg_word", "upd_test_scg_words", "upd_test_pcc",
                     "upd_test_pcc_words", "upd_test_pcc_lpit_index", "upd_test_lpit_mcr", "upd_test_lpit_tval",
                     "upd_test_lpit_tctrl", "upd_test_lpit_channels", "upd_test_lpit_without_mcen",
                     "upd_test_lpit_without_gate"):
            getattr(cls.lib, name).restype = ctypes.c_uint32
        for name in ("upd_test_scg_word", "upd_test_pcc", "upd_test_lpit_tval", "upd_test_lpit_tctrl"):
            getattr(cls.lib, name).argtypes = [ctypes.c_uint32]

    @classmethod
    def tearDownClass(cls):
        if sys.platform == "win32":
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.FreeLibrary.argtypes = [ctypes.c_void_p]
            kernel32.FreeLibrary(ctypes.c_void_p(cls.lib._handle))
        cls.tmp.cleanup()

    def start(self, fircdiv=0, pcc_lpit=0):
        self.lib.upd_test_reset(fircdiv, pcc_lpit)
        self.lib.upd_test_start()

    def test_lpit_index_is_the_one_of_the_manual(self):
        self.assertEqual(self.lib.upd_test_pcc_lpit_index(), 0xDC // 4)   # [RM] 29.5: PCC_LPIT at offset DCh

    def test_clock_of_the_counter_is_firc_divided_by_1(self):
        self.start()
        fircdiv = self.lib.upd_test_fircdiv()
        self.assertEqual((fircdiv >> FIRCDIV2_SHIFT) & FIRCDIV_FIELD, DIVIDE_BY_1)
        self.assertEqual(fircdiv & FIRCDIV_FIELD, 0, "FIRCDIV1 belongs to someone else")
        self.assertEqual(self.lib.upd_test_pcc(self.lib.upd_test_pcc_lpit_index()),
                         PCC_CGC | (PCS_FIRCDIV2 << PCC_PCS_SHIFT))

    def test_other_dividers_of_firc_are_kept(self):
        self.start(fircdiv=0x5)                                              # FIRCDIV1 = divide by 16
        self.assertEqual(self.lib.upd_test_fircdiv(), (DIVIDE_BY_1 << FIRCDIV2_SHIFT) | 0x5)

    def test_clock_source_changes_with_the_gate_off(self):
        """A gate left on with another source (e.g. by a debugger session) must be switched off first."""
        self.start(pcc_lpit=PCC_CGC | (PCS_SOSCDIV2 << PCC_PCS_SHIFT))
        self.assertEqual(self.lib.upd_test_pcc(self.lib.upd_test_pcc_lpit_index()),
                         PCC_CGC | (PCS_FIRCDIV2 << PCC_PCS_SHIFT))

    def test_lpit_is_enabled_before_its_channels_are_used(self):
        self.start()
        self.assertEqual(self.lib.upd_test_lpit_without_gate(), 0, "LPIT accessed without PCC clock gate")
        self.assertEqual(self.lib.upd_test_lpit_without_mcen(), 1, "only the MCR write may happen without M_CEN")
        self.assertEqual(self.lib.upd_test_lpit_mcr() & M_CEN, M_CEN)

    def test_channel_0_runs_free_from_the_maximum(self):
        self.start()
        self.assertEqual(self.lib.upd_test_lpit_tval(0), 0xFFFFFFFF)
        tctrl = self.lib.upd_test_lpit_tctrl(0)
        self.assertEqual(tctrl & T_EN, T_EN)
        self.assertEqual(tctrl & MODE_MASK, 0, "periodic counter mode")
        self.assertEqual(tctrl & ~(T_EN | MODE_MASK), 0, "no trigger, no chaining")
        for channel in range(1, self.lib.upd_test_lpit_channels()):
            with self.subTest(channel=channel):
                self.assertEqual(self.lib.upd_test_lpit_tctrl(channel), 0)
                self.assertEqual(self.lib.upd_test_lpit_tval(channel), 0)

    def test_nothing_else_is_touched(self):
        self.start()
        lpit = self.lib.upd_test_pcc_lpit_index()
        for index in range(self.lib.upd_test_pcc_words()):
            if index != lpit:
                self.assertEqual(self.lib.upd_test_pcc(index), 0, f"PCC word {index}")
        for index in range(self.lib.upd_test_scg_words()):
            if index != SCG_FIRCDIV_WORD:
                self.assertEqual(self.lib.upd_test_scg_word(index), 0, f"SCG word {index}")

    def test_count_goes_up_and_wraps_at_2_pow_32(self):
        self.start()
        self.lib.upd_test_set_cval(0, 0xFFFFFFFF)
        self.assertEqual(self.lib.upd_test_count(), 0)
        self.lib.upd_test_set_cval(0, 0xFFFFFFFF - 48_000_000)               # one second at 48 MHz
        self.assertEqual(self.lib.upd_test_count(), 48_000_000)
        before = 0xFFFFFFFF - 10                                            # 10 ticks before the reload
        self.lib.upd_test_set_cval(0, 10)
        t0 = self.lib.upd_test_count()
        self.assertEqual(t0, before)
        self.lib.upd_test_set_cval(0, 0xFFFFFFFF - 5)                       # reloaded, 16 ticks later
        self.assertEqual((self.lib.upd_test_count() - t0) & 0xFFFFFFFF, 16)


class CounterSelectionTest(unittest.TestCase):
    """The counter follows the core architecture, not a derivative or a board."""

    def test_cmake_maps_the_architectures(self):
        text = (UPDATER / "CMakeLists.txt").read_text(encoding="utf-8")
        mapping = dict(re.findall(r"(armv\d+m)=(\w+)", text.split("set(_UPD_CYCLE_COUNTERS", 1)[1].split(")", 1)[0]))
        self.assertEqual(mapping, {"armv7m": "dwt", "armv6m": "lpit"})
        self.assertIn("nx_core_architecture(", text)
        self.assertRegex(text, r"FATAL_ERROR \"blupdater: no cycle counter")
        for counter in mapping.values():
            self.assertTrue((COUNTERS / counter / "upd_cycle_counter.h").is_file(), counter)

    def test_armv6m_counter_does_not_use_the_core_debug_units(self):
        code = without_comments(LPIT_HEADER)
        for name in ("DWT", "CoreDebug", "CYCCNT", "DEMCR"):
            self.assertNotIn(name, code)

    def test_counters_name_no_derivative(self):
        for header in (LPIT_HEADER, DWT_HEADER, UPDATER / "platforms" / "s32k1xx" / "upd_main.c",
                       UPDATER / "platforms" / "s32k1xx" / "upd_micros.c"):
            with self.subTest(file=header.name):
                self.assertIsNone(re.search(r"S32K1\d\d|__ARM_ARCH|__CORTEX_M", without_comments(header)))

    def test_main_uses_only_the_counter_interface(self):
        # PLAT-86: the time measurement moved from upd_main.c to upd_micros.c (host test test_upd_micros_host.py)
        main = without_comments(UPDATER / "platforms" / "s32k1xx" / "upd_main.c")
        code = without_comments(UPDATER / "platforms" / "s32k1xx" / "upd_micros.c")
        for name in ("DWT", "CoreDebug", "LPIT"):
            self.assertNotIn(name, main)
            self.assertNotIn(name, code)
        self.assertIn("upd_micros_start()", main)
        self.assertIn("upd_cycle_counter_start()", code)
        self.assertIn("upd_cycle_count()", code)


if __name__ == "__main__":
    unittest.main()
