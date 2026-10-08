"""BL-33: no startup window after the resets of the watchdog fast test (SYS-149, safety review SR-149-2 a).

Board 2026-09-30 (SYS-149): after `safety wdg` and `safety mpu` RESET_b fell at 0, 228.7 and 455.5 ms - the error
reset and the two watchdog resets of the fast test (OpenBSW Watchdog::executeFastTest, TST 2 and 3) - and every one
of the three starts waited for the bootloader's startup window of 200 ms. The family hook
hal_reset_skips_startup_window() lets the two test resets pass; the core stays family neutral.

Covered without hardware:
- the rule of the family s32k1xx (bootloader/platforms/s32k1xx/bl_reset_s32k1xx.h) on the field masks of the vendor
  header, for every single reset source and every combination with WDOG, TST 0..3 and other WDOG_CS bits; the masks
  against the reference manual's literals
- reset sequences of the application (power-on, MPU fault, watchdog error with its interrupt, watchdog timeout in
  user mode, pin, lockup, a test that never ends): which start gets the window, and the 400 ms of SYS-149
- mutants of the rule (any WDOG bit, user mode, one byte only, wrong shift, always) - each must fail the table
- the core (bootloader/src/core/bl_main.c) on the simulated HAL of bl_host.c, three memory layouts: jump in the first
  poll when the hook says so; window as before when not; boot request, erased or corrupt application keep the
  bootloader and the hook is not asked with a boot request
- source rules: the core knows no register of the family, every HAL defines the hook, the family HAL feeds the rule
  RCM_SRS and WDOG_CS and its bl_system_init() keeps TST

Run: .venv/Scripts/python -m unittest bootloader/test/host/test_bl_startup_window_host.py
(needs ziglang in the .venv, like tools/canflash/hostbuild.py)
"""

import ctypes
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
BOOTLOADER = REPO / "bootloader"
FAMILY = BOOTLOADER / "platforms" / "s32k1xx"
RULE_H = FAMILY / "bl_reset_s32k1xx.h"
VENDOR = REPO / "external" / "openbsw" / "platforms" / "s32k1xx" / "bsp" / "bspMcu" / "include" / "3rdparty" / "nxp"

sys.path.insert(0, str(REPO / "tools" / "canflash"))
import canflash  # noqa: E402
import test_bootloader_host as tbh  # noqa: E402  (host DLL build and helpers of bootloader-host)

try:
    import ziglang  # noqa: F401

    HAVE_ZIG = True
except ImportError:  # pragma: no cover - the test skips without the host compiler
    HAVE_ZIG = False

CFG = canflash.CFG

# Reference manual literals (S32K1xx RM, RCM_SRS and WDOG_CS): WDOG is bit 5 of RCM_SRS, TST bits 4..3 of WDOG_CS
SRS_WDOG, SRS_POR, SRS_LVD, SRS_PIN, SRS_LOCKUP, SRS_SW = 0x20, 0x80, 0x02, 0x40, 0x200, 0x400
TST_MASK, TST_SHIFT = 0x18, 3
# WDOG_CS around the TST field: as the bootloader leaves it (0x2924), every other bit set, nothing
CS_OTHER_BITS = (0x0000, 0x2924, 0xFFFF & ~TST_MASK, 0x0080, 0x2100)

# board 2026-09-30, SYS-149: one start = startup window + ~28 ms (CRC of the application, clock, up to the test)
WINDOW_MS, START_MS = 200, 28

RULE_HARNESS = r"""
#include "S32K148_COMMON.h"
#include "S32K148_RCM.h"
#include "S32K148_WDOG.h"
#include "bl_reset_s32k1xx.h"
#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT __attribute__((visibility("default")))
#endif
EXPORT int rule(uint32_t srs, uint32_t cs) { return bl_s32k1xx_fast_test_reset(srs, cs) ? 1 : 0; }
EXPORT uint32_t field(int which)
{
    return (which == 0) ? RCM_SRS_WDOG_MASK : (which == 1) ? WDOG_CS_TST_MASK : WDOG_CS_TST_SHIFT;
}
"""

# built-in faults of the rule; each must turn the table red
MUTANTS = {
    "any WDOG bit (WDOG+SW of the watchdog interrupt passes)":
        ("(rcmSrs == RCM_SRS_WDOG_MASK)", "((rcmSrs & RCM_SRS_WDOG_MASK) != 0U)"),
    "user mode passes too (TST != 0)":
        ("((tst == BL_WDOG_TST_FAST_LOW) || (tst == BL_WDOG_TST_FAST_HIGH))", "(tst != 0U)"),
    "only the low byte reset passes":
        ("((tst == BL_WDOG_TST_FAST_LOW) || (tst == BL_WDOG_TST_FAST_HIGH))", "(tst == BL_WDOG_TST_FAST_LOW)"),
    "wrong field shift": (">> WDOG_CS_TST_SHIFT", ">> 4U"),
    "reset source or test step": (") && ((tst", ") || ((tst"),
    "always": ("return (rcmSrs ==", "return true || (rcmSrs =="),
}


def expected(srs: int, cs: int) -> bool:
    return srs == SRS_WDOG and ((cs & TST_MASK) >> TST_SHIFT) in (2, 3)


def zig_build(out: Path, sources, includes) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", "ziglang", "cc", "-target", "x86_64-windows-gnu", "-shared", "-O1", "-std=c99",
           "-Wall", "-Wextra", "-Wconversion", "-Werror", *(f"-I{d}" for d in includes), *map(str, sources),
           "-o", str(out)]
    return subprocess.run(cmd, capture_output=True, text=True)


def free(lib):
    """Windows keeps a loaded DLL locked: without this the temporary directory cannot be removed."""
    if sys.platform == "win32":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.FreeLibrary.argtypes = [ctypes.c_void_p]
        kernel32.FreeLibrary(ctypes.c_void_p(lib._handle))


class Rule:
    """bl_s32k1xx_fast_test_reset() of a header text, built as DLL."""

    def __init__(self, tmp: Path, name: str, header_text: str):
        folder = tmp / name
        folder.mkdir(parents=True)
        (folder / RULE_H.name).write_text(header_text, encoding="utf-8")
        harness = folder / "rule_harness.c"
        harness.write_text(RULE_HARNESS, encoding="utf-8")
        self.result = zig_build(folder / "rule.dll", [harness], [folder, VENDOR])
        self.lib = None
        if self.result.returncode == 0:
            self.lib = ctypes.CDLL(str(folder / "rule.dll"))
            self.lib.rule.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
            self.lib.field.restype = ctypes.c_uint32

    def __call__(self, srs: int, cs: int) -> bool:
        return bool(self.lib.rule(srs, cs))

    def table_errors(self) -> list[str]:
        """Every single reset source alone and together with WDOG, POR+WDOG, all four TST values, other CS bits."""
        sources = [1 << bit for bit in range(32)]
        srs_values = sorted({0, *sources, *(s | SRS_WDOG for s in sources), SRS_WDOG | SRS_POR, 0xFFFFFFFF})
        errors = []
        for srs in srs_values:
            for tst in range(4):
                for other in CS_OTHER_BITS:
                    cs = other | (tst << TST_SHIFT)
                    if self(srs, cs) != expected(srs, cs):
                        errors.append(f"SRS 0x{srs:X} CS 0x{cs:04X}: {self(srs, cs)}")
        return errors

    def close(self):
        if self.lib is not None:
            free(self.lib)


def start_sequence(rule, resets: list[tuple[str, int, int]]) -> list[tuple[str, bool, int]]:
    """(name, window skipped, time of the start in ms) for each reset of a sequence (SRS, WDOG_CS[TST])."""
    return [(name, rule(srs, tst << TST_SHIFT), START_MS + (0 if rule(srs, tst << TST_SHIFT) else WINDOW_MS))
            for name, srs, tst in resets]


# what the application does after each kind of reset: the reset itself, then the two resets of the fast test
FAST_TEST = [("fast test low byte", SRS_WDOG, 2), ("fast test high byte", SRS_WDOG, 3)]
PATHS = {
    "power-on": [("power-on", SRS_POR | SRS_LVD, 0)] + FAST_TEST,
    "safety mpu (fault handler, SW reset)": [("fault reset", SRS_SW, 1)] + FAST_TEST,
    "safety wdg (watchdog interrupt, then SW)": [("watchdog error reset", SRS_WDOG | SRS_SW, 1)] + FAST_TEST,
    "watchdog timeout in user mode without interrupt": [("watchdog reset", SRS_WDOG, 1)] + FAST_TEST,
    "pin reset (SBC)": [("pin reset", SRS_PIN, 1)] + FAST_TEST,
    "lockup": [("lockup reset", SRS_LOCKUP, 1)] + FAST_TEST,
}


def setUpModule():
    tbh.setUpModule()


def tearDownModule():
    tbh.tearDownModule()


@unittest.skipUnless(HAVE_ZIG, "ziglang not installed (host compiler)")
class FastTestRuleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="bl33_")
        cls.tmp = Path(cls._tmp.name)
        cls.rule = Rule(cls.tmp, "real", RULE_H.read_text(encoding="utf-8"))
        assert cls.rule.lib is not None, cls.rule.result.stderr[-3000:]

    @classmethod
    def tearDownClass(cls):
        cls.rule.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_vendor_fields_are_those_of_the_reference_manual(self):
        self.assertEqual(self.rule.lib.field(0), SRS_WDOG)
        self.assertEqual(self.rule.lib.field(1), TST_MASK)
        self.assertEqual(self.rule.lib.field(2), TST_SHIFT)

    def test_table(self):
        self.assertEqual(self.rule.table_errors(), [])

    def test_only_the_two_test_resets(self):
        for tst in range(4):
            with self.subTest(tst=tst):
                self.assertEqual(self.rule(SRS_WDOG, tst << TST_SHIFT), tst in (2, 3))
        # the watchdog error of `safety wdg` (WDOG+SW, board 0x420) and a power-on keep the window in every step
        for srs in (SRS_WDOG | SRS_SW, SRS_POR, SRS_POR | SRS_LVD, SRS_WDOG | SRS_POR, SRS_LVD, 0):
            for tst in range(4):
                with self.subTest(srs=hex(srs), tst=tst):
                    self.assertFalse(self.rule(srs, tst << TST_SHIFT))

    def test_every_path_keeps_the_window_of_its_first_start_and_saves_400_ms(self):
        for path, resets in PATHS.items():
            with self.subTest(path=path):
                starts = start_sequence(self.rule, resets)
                self.assertEqual([skipped for _, skipped, _ in starts], [False, True, True], starts)
                before = len(resets) * (START_MS + WINDOW_MS)
                after = sum(ms for _, _, ms in starts)
                self.assertEqual(before - after, 2 * WINDOW_MS)

    def test_a_test_that_never_ends_is_left_by_a_power_cycle(self):
        """An application that resets in its test for good passes without window - until a power-on clears TST."""
        endless = [("fast test low byte", SRS_WDOG, 2)] * 5
        self.assertTrue(all(skipped for _, skipped, _ in start_sequence(self.rule, endless)))
        self.assertFalse(self.rule(SRS_POR, 0), "power-on clears TST and keeps the window: the tester's way in")

    def test_board_sequence_of_sys_149(self):
        """SYS-149 (O1): first CAN frame at 787 ms after three starts of 228 ms each; with BL-33 400 ms earlier."""
        starts = start_sequence(self.rule, PATHS["safety wdg (watchdog interrupt, then SW)"])
        measured_first_frame_ms = 787
        predicted = measured_first_frame_ms - sum(WINDOW_MS for _, skipped, _ in starts if skipped)
        self.assertEqual(predicted, 387)

    def test_mutants_are_caught(self):
        source = RULE_H.read_text(encoding="utf-8")
        for name, (old, new) in MUTANTS.items():
            with self.subTest(mutant=name):
                self.assertEqual(source.count(old), 1, f"mutant '{name}' does not apply any more")
                mutant = Rule(self.tmp, re.sub(r"\W+", "_", name), source.replace(old, new))
                try:
                    if mutant.lib is None:
                        continue  # a mutant that does not even compile is caught as well
                    self.assertNotEqual(mutant.table_errors(), [], f"mutant '{name}' not detected")
                finally:
                    mutant.close()


@unittest.skipUnless(HAVE_ZIG, "ziglang not installed (host compiler)")
class CoreWindowTest(unittest.TestCase):
    """The core on the simulated HAL: eval board memory layout (projects/evalboard_s32k144_demo)."""

    PROJECT, MCUDB, LAYOUT = tbh.EVAL_PROJECT, None, None

    setUpClass = classmethod(tbh.BootloaderTest.setUpClass.__func__)
    tearDownClass = classmethod(tbh.BootloaderTest.tearDownClass.__func__)
    setUp = tbh.BootloaderTest.setUp
    run_ms = tbh.BootloaderTest.run_ms
    install_app = tbh.BootloaderTest.install_app

    def start(self, boot_request: bool, skips: bool):
        self.bl.dll.host_skip_queries.restype = ctypes.c_uint32
        with self.bl.lock:
            self.bl.dll.host_power_on_after_reset(1 if boot_request else 0, 1 if skips else 0)

    def jumped(self) -> bool:
        return self.bl.dll.host_jump_address() == CFG["BL_APP_START"]

    def test_jumps_in_the_first_poll_after_a_reset_the_family_lets_pass(self):
        self.install_app(tbh.make_app_image(5000))
        self.start(boot_request=False, skips=True)
        self.assertEqual(self.bl.dll.host_skip_queries(), 1)
        self.run_ms(1)
        self.assertTrue(self.jumped(), "no window: the valid application is started at once")

    def test_window_as_before_otherwise(self):
        self.install_app(tbh.make_app_image(5000))
        self.start(boot_request=False, skips=False)
        self.assertEqual(self.bl.dll.host_skip_queries(), 1)
        self.run_ms(CFG["BL_STARTUP_WINDOW_MS"] - 5)
        self.assertFalse(self.jumped())
        self.run_ms(10)
        self.assertTrue(self.jumped())

    def test_power_on_of_the_host_keeps_the_window(self):
        """host_power_on() is a power-on: the hook answers false (as s32k1xx does with RCM_SRS[POR])."""
        self.install_app(tbh.make_app_image(5000))
        with self.bl.lock:
            self.bl.dll.host_power_on(0)
        self.run_ms(CFG["BL_STARTUP_WINDOW_MS"] - 5)
        self.assertFalse(self.jumped())

    def test_boot_request_wins_and_the_hook_is_not_asked(self):
        self.install_app(tbh.make_app_image(5000))
        self.start(boot_request=True, skips=True)
        self.assertEqual(self.bl.dll.host_skip_queries(), 0)
        self.run_ms(1000)
        self.assertFalse(self.jumped())

    def test_no_application_no_jump(self):
        self.start(boot_request=False, skips=True)  # flash erased by setUp
        self.run_ms(1000)
        self.assertEqual(self.bl.dll.host_jump_address(), 0)

    def test_corrupt_application_no_jump(self):
        data = tbh.make_app_image(5000)
        self.install_app(data)
        self.bl.write(CFG["BL_APP_START"] + 100, bytes([data[100] ^ 0xFF]))
        self.start(boot_request=False, skips=True)
        self.run_ms(1000)
        self.assertEqual(self.bl.dll.host_jump_address(), 0)


@unittest.skipUnless(HAVE_ZIG, "ziglang not installed (host compiler)")
class CoreWindowOtherLayoutTest(CoreWindowTest):
    """testproject_bootloader: 8-KB sectors at 0x08000000, no SBC, other CAN bus and ids."""

    PROJECT, MCUDB, LAYOUT = tbh.OTHER_PROJECT, tbh.TEST_MCUDB, None


@unittest.skipUnless(HAVE_ZIG, "ziglang not installed (host compiler)")
class CoreWindowS32K116Test(CoreWindowTest):
    """Smallest derivative of the focus family, 2-KB sectors (synthetic layout s32k116_2k)."""

    PROJECT, MCUDB, LAYOUT = None, None, "s32k116_2k"


def code(path: Path) -> str:
    """Source without C/C++ comments."""
    return re.sub(r"/\*.*?\*/|//[^\n]*", "", path.read_text(encoding="utf-8"), flags=re.S)


class SourceRuleTest(unittest.TestCase):
    """Family neutrality and wiring, readable without a compiler."""

    HOOK = "hal_reset_skips_startup_window"

    def test_core_knows_no_family_register(self):
        core = code(BOOTLOADER / "src" / "core" / "bl_main.c")
        for word in ("RCM", "WDOG", "SRS", "TST", "IP_", "s32k"):
            self.assertNotIn(word, core, f"bl_main.c must stay family neutral ({word})")
        self.assertEqual(len(re.findall(rf"\b{self.HOOK}\s*\(", core)), 1)
        self.assertIn("BL_STARTUP_WINDOW_MS", core, "the window itself stays")
        self.assertRegex(core, rf"!bootRequest\s*&&\s*{self.HOOK}\(\)", "a boot request is decided first")

    def test_every_hal_defines_the_hook(self):
        hals = [*sorted((BOOTLOADER / "platforms").glob("*/hal_*.c")), HERE / "bl_host.c",
                HERE / "bl_transport_link_host.c"]
        # s32k1xx and stm32 plus the two host stand-ins; TOOL-244: stm32 is private, missing in the exported tree
        self.assertGreaterEqual(len(hals), 4 if (BOOTLOADER / "platforms" / "stm32").is_dir() else 3)
        for hal in hals:
            with self.subTest(hal=hal.name):
                self.assertRegex(code(hal), rf"\bbool\s+{self.HOOK}\s*\(\s*void\s*\)\s*\{{")

    def test_family_hal_feeds_the_rule(self):
        hal = code(FAMILY / "hal_s32k1xx.c")
        self.assertRegex(hal, rf"{self.HOOK}\(void\)\s*\{{\s*return\s+bl_s32k1xx_fast_test_reset\(IP_RCM->SRS,\s*"
                              r"IP_WDOG->CS\);\s*\}")
        # TST must survive the bootloader: bl_system_init() writes it back unchanged
        init = re.search(r"void bl_system_init\(void\)\s*\{(.*?)\n\}", hal, re.S).group(1)
        self.assertRegex(init, r"IP_WDOG->CS\s*=\s*\(IP_WDOG->CS & WDOG_CS_TST_MASK\)\s*\|")
        self.assertIsNone(re.search(r"\bstatic\b", init))

    def test_other_families_say_no(self):
        stm32 = code(BOOTLOADER / "platforms" / "stm32" / "hal_stm32.c")
        self.assertRegex(stm32, rf"{self.HOOK}\(void\)\s*\{{\s*return false;\s*\}}")


if __name__ == "__main__":
    unittest.main()
