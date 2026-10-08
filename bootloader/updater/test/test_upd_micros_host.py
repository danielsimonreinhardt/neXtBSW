"""Host test of the updater's time measurement upd_micros() on the DWT cycle counter (PLAT-86).

bootloader/updater/platforms/s32k1xx/upd_micros.c with the ARMv7-M counter (cycle_counter/dwt/upd_cycle_counter.h)
against a DWT/CoreDebug register model (test/host/model_dwt). The test plays the hardware: CYCCNT counts only while
DEMCR.TRCENA and DWT_CTRL.CYCCNTENA are set; a debugger that closes its session clears TRCENA (J-Link debug de-init,
board finding PLAT-67, doc/hardware/debug-session-close.md) and the counter stands still, its reads are UNKNOWN.

Covered without hardware, per counter clock profile (48 MHz FIRC reset clock, 8 MHz, 80 MHz):
- start: TRCENA and CYCCNTENA set, CYCCNT 0, upd_micros() 0, no restart
- exact microseconds with the remainder carried over, over more than one period of the 32 bit counter; a window across
  the counter wraparound measures its real length (the former upd_cycle_count() / CYCLES_PER_US came out near 2^32 us)
- each kind of disconnect (TRCENA, TRCENA and CYCCNTENA, only CYCCNTENA, UNKNOWN value while off): the next call
  switches the counter on again, returns the previous value (no jump), counts one restart, then runs exactly again;
  a window across the disconnect is shorter by the time the counter stood still, never huge
- the ARMv6-M counter (LPIT) offers the same interface and never reports a stopped counter

Run: .venv/Scripts/python -m unittest bootloader/updater/test/test_upd_micros_host.py
(needs ziglang in the .venv, like test_upd_cycle_counter_host.py)
"""

import ctypes
import random
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
UPDATER = HERE.parent
BOOTLOADER = UPDATER.parent
PLATFORM = UPDATER / "platforms" / "s32k1xx"
COUNTERS = PLATFORM / "cycle_counter"

# ARMv7-M ARM DDI 0403E (values the test states itself instead of reading the code under test)
TRCENA = 1 << 24        # C1.6.5 DEMCR
CYCCNTENA = 1 << 0      # C1.8.7 DWT_CTRL
MASK32 = 0xFFFFFFFF

PROFILES = (48_000_000, 8_000_000, 80_000_000)


def build(out: Path, core_hz: int) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", "ziglang", "cc", "-target", "x86_64-windows-gnu", "-shared", "-O1", "-std=c99",
           "-Wall", "-Wextra", "-Werror", "-Wconversion", "-Wno-sign-conversion", f"-DUPD_TEST_CORE_HZ={core_hz}UL",
           f"-I{HERE / 'host' / 'model_dwt'}", f"-I{COUNTERS / 'dwt'}", f"-I{PLATFORM}",
           f"-I{UPDATER / 'include'}", f"-I{UPDATER / 'src' / 'core'}", f"-I{BOOTLOADER / 'include'}",
           f"-I{BOOTLOADER / 'src' / 'core'}",
           str(HERE / "host" / "upd_micros_host.c"), str(PLATFORM / "upd_micros.c"), "-o", str(out)]
    return subprocess.run(cmd, capture_output=True, text=True)


class Dwt:
    """The counting hardware around the DLL."""

    def __init__(self, lib, core_hz: int):
        self.lib = lib
        self.mhz = core_hz // 1_000_000
        self.cycles = 0          # exact time since start
        self.unknown = None      # value a read returns while TRCENA is 0 (None: the frozen value, as on the board)

    def counting(self) -> bool:
        return bool(self.lib.upd_test_demcr() & TRCENA) and bool(self.lib.upd_test_ctrl() & CYCCNTENA)

    def advance(self, cycles: int) -> None:
        if self.counting():
            self.lib.upd_test_set_cyccnt((self.lib.upd_test_cyccnt() + cycles) & MASK32)
        self.cycles += cycles

    def micros(self) -> int:
        if self.unknown is not None and not self.lib.upd_test_demcr() & TRCENA:
            self.lib.upd_test_set_cyccnt(self.unknown)   # UNKNOWN value while the DWT is off
        return self.lib.upd_test_micros()

    def expected(self) -> int:
        return (self.cycles // self.mhz) & MASK32


class UpdMicrosTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.libs = {}
        for hz in PROFILES:
            dll = Path(cls.tmp.name) / f"upd_micros_{hz}.dll"
            result = build(dll, hz)
            assert result.returncode == 0, result.stderr
            lib = ctypes.CDLL(str(dll))
            for name in ("upd_test_reset", "upd_test_start", "upd_test_set_demcr", "upd_test_set_ctrl",
                         "upd_test_set_cyccnt"):
                getattr(lib, name).restype = None
            for name in ("upd_test_reset", "upd_test_set_demcr", "upd_test_set_ctrl", "upd_test_set_cyccnt"):
                getattr(lib, name).argtypes = [ctypes.c_uint32]
            for name in ("upd_test_micros", "upd_test_restarts", "upd_test_demcr", "upd_test_ctrl",
                         "upd_test_cyccnt"):
                getattr(lib, name).restype = ctypes.c_uint32
            cls.libs[hz] = lib

    @classmethod
    def tearDownClass(cls):
        if sys.platform == "win32":
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.FreeLibrary.argtypes = [ctypes.c_void_p]
            for lib in cls.libs.values():
                kernel32.FreeLibrary(ctypes.c_void_p(lib._handle))
        cls.tmp.cleanup()

    def start(self, hz: int) -> Dwt:
        lib = self.libs[hz]
        lib.upd_test_reset(0x12345678)        # any value before the start, DWT off
        lib.upd_test_start()
        return Dwt(lib, hz)

    def test_start(self):
        for hz in PROFILES:
            with self.subTest(hz=hz):
                dwt = self.start(hz)
                self.assertTrue(dwt.lib.upd_test_demcr() & TRCENA)
                self.assertTrue(dwt.lib.upd_test_ctrl() & CYCCNTENA)
                self.assertEqual(dwt.lib.upd_test_cyccnt(), 0)
                self.assertEqual(dwt.micros(), 0)
                self.assertEqual(dwt.lib.upd_test_restarts(), 0)

    def test_exact_over_more_than_one_counter_period(self):
        rng = random.Random(4711)
        for hz in PROFILES:
            with self.subTest(hz=hz):
                dwt = self.start(hz)
                while dwt.cycles < 3 * (1 << 32):
                    step = rng.randrange(1, 20_000 * dwt.mhz)
                    if rng.random() < 0.001:
                        step = (1 << 32) - dwt.mhz - rng.randrange(1000)   # longest allowed gap
                    dwt.advance(step)
                    self.assertEqual(dwt.micros(), dwt.expected())
                self.assertEqual(dwt.lib.upd_test_restarts(), 0)

    def test_window_across_the_counter_wraparound(self):
        """A 10 ms flash window across the wrap of CYCCNT has its real length."""
        for hz in PROFILES:
            with self.subTest(hz=hz):
                dwt = self.start(hz)
                before_wrap = (1 << 32) - 5_000 * dwt.mhz                     # 5 ms before the wrap
                while dwt.cycles + 1_000_000 * dwt.mhz < before_wrap:
                    dwt.advance(1_000_000 * dwt.mhz)                          # one call per second
                    dwt.micros()
                dwt.advance(before_wrap - dwt.cycles)
                count0, t0 = dwt.lib.upd_test_cyccnt(), dwt.micros()
                dwt.advance(10_000 * dwt.mhz)
                count1, t1 = dwt.lib.upd_test_cyccnt(), dwt.micros()
                self.assertLess(count1, count0, "the window spans the wraparound")
                self.assertEqual((t1 - t0) & MASK32, 10_000)
                former = ((count1 // dwt.mhz) - (count0 // dwt.mhz)) & MASK32     # upd_cycle_count() / CYCLES_PER_US
                self.assertGreater(former, 1 << 31, "the former formula failed here")

    def test_debugger_disconnect(self):
        cases = {"TRCENA cleared": (True, False, None),
                 "TRCENA and CYCCNTENA cleared": (True, True, None),
                 "only CYCCNTENA cleared": (False, True, None),
                 "UNKNOWN reads while off": (True, False, 0xDEADBEEF)}
        rng = random.Random(7)
        for hz in PROFILES:
            for name, (trcena, cyccntena, unknown) in cases.items():
                with self.subTest(hz=hz, case=name):
                    dwt = self.start(hz)
                    for _ in range(100):
                        dwt.advance(rng.randrange(1, 2_000 * dwt.mhz))
                        last = dwt.micros()
                    self.assertEqual(last, dwt.expected())
                    t0 = last                                                  # a flash window starts
                    dwt.advance(300 * dwt.mhz)
                    if trcena:
                        dwt.lib.upd_test_set_demcr(dwt.lib.upd_test_demcr() & ~TRCENA & MASK32)
                    if cyccntena:
                        dwt.lib.upd_test_set_ctrl(dwt.lib.upd_test_ctrl() & ~CYCCNTENA & MASK32)
                    dwt.unknown = unknown
                    frozen = dwt.lib.upd_test_cyccnt()
                    dwt.advance(700 * dwt.mhz)
                    self.assertEqual(dwt.lib.upd_test_cyccnt(), frozen, "stands still")
                    self.assertEqual(dwt.micros(), last, "no jump at the restart")
                    self.assertTrue(dwt.counting())
                    self.assertEqual(dwt.lib.upd_test_restarts(), 1)
                    dwt.unknown = None
                    lost = dwt.expected() - last                               # 1 ms, up to the phase
                    self.assertIn(lost, (1000, 1001))
                    for _ in range(1000):
                        dwt.advance(rng.randrange(1, 2_000 * dwt.mhz))
                        self.assertIn(dwt.expected() - dwt.micros(), (lost, lost + 1))
                    window = (dwt.micros() - t0) & MASK32
                    self.assertLess(window, (dwt.cycles // dwt.mhz) - t0, "shorter by the time it stood still")
                    self.assertEqual(dwt.lib.upd_test_restarts(), 1)

    def test_restart_counter_starts_at_zero(self):
        dwt = self.start(PROFILES[0])
        dwt.lib.upd_test_set_demcr(0)
        dwt.micros()
        self.assertEqual(dwt.lib.upd_test_restarts(), 1)
        dwt = self.start(PROFILES[0])
        self.assertEqual(dwt.lib.upd_test_restarts(), 0)


class CounterInterfaceTest(unittest.TestCase):
    """Both counters offer the interface upd_micros.c uses; the LPIT one never reports a stopped counter."""

    def test_both_counters_offer_running_and_restart(self):
        for counter in ("dwt", "lpit"):
            text = (COUNTERS / counter / "upd_cycle_counter.h").read_text(encoding="utf-8")
            with self.subTest(counter=counter):
                self.assertRegex(text, r"static inline bool upd_cycle_counter_running\(void\)")
                self.assertRegex(text, r"static inline void upd_cycle_counter_restart\(void\)")
        lpit = (COUNTERS / "lpit" / "upd_cycle_counter.h").read_text(encoding="utf-8")
        self.assertRegex(lpit, r"upd_cycle_counter_running\(void\) \{ return true; \}")

    def test_upd_micros_checks_before_it_reads(self):
        code = re.sub(r"/\*.*?\*/", "", (PLATFORM / "upd_micros.c").read_text(encoding="utf-8"), flags=re.S)
        body = code.split("uint32_t upd_micros(void)", 1)[1]
        self.assertLess(body.index("upd_cycle_counter_running()"), body.index("upd_cycle_count()"))


if __name__ == "__main__":
    unittest.main()
