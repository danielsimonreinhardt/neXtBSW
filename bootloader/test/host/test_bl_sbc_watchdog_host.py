"""SBC watchdog in the bootloader, chosen per project (BL-28, user decision 2026-09-21 bl26-sbc-watchdog; BL-41,
safety review SBC watchdog 2026-10-06, SDMS0-1 c).

bootloader.sbc_watchdog in the project (tools/nxconfig/model.py):
- leave (default): takes effect only in software development mode (SDMS 1): the bootloader does not touch the SBC
  watchdog and enters Normal mode. Without development mode (SDMS 0) the watchdog runs from the SBC reset on, in
  Standby too (data sheet Table 7 with 7.2.1: WMC timeout, 128 ms; Table 8), so since BL-41 the bootloader serves it
  as with serve; bl_sbc_watchdog.reason tells why (SBC_WD_REASON_SDMS0). Before BL-41 the hook left the SBC as it was
  (stage 0xD0): SBC resets without valid application, a short window with one - that stays the updater build.
- serve: the hook sets the watchdog in Standby to a fixed setting (autonomous, 1024 ms), enters Normal mode and the
  core calls bl_device_service() in every poll and once more right before the jump; the application takes over.
- read only (sbc_init(false), the SBC is not in bootloader.init; BL-42, safety review bootloader 0.3.10 2026-10-07,
  BL41-2 b): with SDMS 0 the hook sets up and serves only the watchdog (SBC_WD_REASON_SDMS0_READ); it stays in
  Standby (an SBC found in Normal mode goes to Standby for the setting), V2 and CAN are not written. With SDMS 1 read
  only stays read only. The model can hold INTN1 low, so autonomous runs in Standby as timeout (Table 8).

Covered without hardware:
- hook sbcUja113x in its three builds (serve, leave = bootloader with BL_SBC_WATCHDOG_SERVICE, updater = no define)
  against the simulated UJA113x of bl_spi_host.c with its watchdog model (overflow, WMC/NWP change in Normal mode,
  trigger in a closed window, data sheet 7.2 with Table 8 and 7.3.3): SDMS 0 and 1, Forced Normal mode, an MCU-only
  reset with the window watchdog of an application still running, an SBC that does not take the setting, the
  hand-over period after the jump; no write to the MTPNV (0x71..0x75) in any scenario
- mutants of the serving code (serve build) and of the SDMS decision of leave (leave build) that the scenarios must
  catch
- core (bootloader/src/core/bl_main.c) built with BL_DEVICE_SERVICE against the simulated HAL of bl_host.c: the
  service runs in every poll and exactly once before the jump, never after it; without the define nothing is called
- generator: bl_board.cmake (NX_BL_WATCHDOG_SERVED_BY) and bl_board.h of the demo project with leave and serve,
  the per-source definitions in devices/sbcUja113x/bootloader.cmake

The SBC model is a board part of its own (bootloader/test/host/model: LPSPI1, chip select 2, identification 0x70),
not the eval board (architecture section 11, question 7). The board proof is only possible with SDMS 1 (eval
board, development mode); SDMS 0 stays a mock proof here until a board with a series SBC (SDMS 0) is at hand.

Run: .venv/Scripts/python -m unittest bootloader/test/host/test_bl_sbc_watchdog_host.py
(needs ziglang in the .venv, like tools/canflash/hostbuild.py)
"""

import ctypes
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_bl_platform_host as plat  # noqa: E402  (host build, register model and helpers of bl-platform-host)

REPO = plat.REPO
BOOTLOADER = plat.BOOTLOADER
UJA = plat.UJA
frame = plat.frame

WDC = UJA["UJA113X_REG_WATCHDOG_CONTROL"]
MODE = UJA["UJA113X_REG_MODE_CONTROL"]
STATUS = UJA["UJA113X_REG_WATCHDOG_STATUS"]
SDMS = UJA["UJA113X_SDMS_MASK"]
FNMS = UJA["UJA113X_FNMS_MASK"]
STANDBY, NORMAL = UJA["UJA113X_MC_STANDBY"], UJA["UJA113X_MC_NORMAL"]


def wdc(wmc: str, nwp_ms: int) -> int:
    return (UJA[f"UJA113X_WMC_{wmc.upper()}"] << UJA["UJA113X_WMC_SHIFT"]) | UJA[f"UJA113X_NWP_{nwp_ms}MS"]


FIXED = wdc("autonomous", 1024)        # the fixed setting of the serving hook (sbc_uja113x.c WATCHDOG_CONTROL)
PERIOD_MS, TRIGGER_MS = 1024, 256
# Register 0x00 after an SBC reset (data sheet Table 7 with 7.2.1): 128 ms, timeout without software development
# mode, autonomous with it.
RESET_DEFAULT = wdc("timeout", 128)
RESET_DEFAULT_SDMS = wdc("autonomous", 128)
OVERFLOWS, ILLEGAL, WINDOW, MAX_GAP = range(4)
LAST_TRIGGER, TRIGGERS, ERRORS, CONTROL_BEFORE, CONTROL_AFTER, SERVED, REASON = range(7)


def hook_defines() -> dict[str, int]:
    """SBC_WD_REASON_* of the hook header, so the test cannot drift from the hook."""
    text = (BOOTLOADER / "devices" / "sbcUja113x" / "sbc_uja113x.h").read_text(encoding="utf-8")
    return {n: int(v, 0) for n, v in re.findall(r"#define\s+(SBC_WD_REASON_\w+)\s+(\d+)U", text)}


REASONS = hook_defines()
REASON_NONE, REASON_PROJECT, REASON_SDMS0, REASON_SDMS0_READ = (
    REASONS[f"SBC_WD_REASON_{n}"] for n in ("NONE", "PROJECT", "SDMS0", "SDMS0_READ"))
SERVE_BUILD = ("BL_SBC_WATCHDOG_SERVE",)
LEAVE_BUILD = ("BL_SBC_WATCHDOG_SERVICE",)   # the bootloader with leave (bootloader.cmake, BL-41)
UPDATER_BUILD: tuple[str, ...] = ()          # the updater: no service


class Host(plat.BlSpiHost):
    def __init__(self, path: Path):
        super().__init__(path)
        for name in ("bl_test_wd_model", "bl_test_wd_lock", "bl_test_idle", "bl_test_wd_intn_low",
                     "bl_test_slave_write"):
            getattr(self.lib, name).restype = None
        self.lib.bl_test_wd_events.restype = ctypes.c_uint32
        self.serving = hasattr(self.lib, "bl_test_service")
        if self.serving:
            self.lib.bl_test_service.restype = None
            self.lib.bl_test_wd_diag.restype = ctypes.c_uint32

    def events(self) -> list[int]:
        return [self.lib.bl_test_wd_events(i) for i in range(4)]

    def wd(self, which: int) -> int:
        return self.lib.bl_test_wd_diag(which)

    def writes(self) -> list[int]:
        return [w for w in self.tx_words() if (w & UJA["UJA113X_SPI_RO_BIT"]) == 0]

    def written_registers(self) -> list[int]:
        return [(w >> UJA["UJA113X_SPI_ADDRESS_SHIFT"]) & 0x7F for w in self.writes()]

    def run_bootloader(self, ms: int):
        """Polls of the bootloader, one per millisecond of the model clock (bl_poll calls the service first)."""
        for _ in range(ms):
            self.lib.bl_test_service(0)

    def jump(self):
        self.lib.bl_test_service(1)


def power_on_sbc(host: Host, sdms: bool, mode: int = STANDBY, control: int | None = None, status_extra: int = 0,
                 kick_ago_ms: int = 0, intn_low: bool = False):
    """SBC of the test board part: identification 0x70, Standby after power-on, watchdog model on; `intn_low`: an
    interrupt is pending (INTN1 low), so autonomous runs in Standby as timeout without development mode (Table 8)."""
    if control is None:
        control = RESET_DEFAULT_SDMS if sdms else RESET_DEFAULT
    host.reset()
    host.lib.bl_test_set_slave_reg(UJA["UJA113X_REG_IDENTIFICATION_0"], 0x70)
    host.lib.bl_test_set_slave_reg(MODE, mode)
    host.lib.bl_test_set_slave_reg(STATUS, (SDMS if sdms else 0) | status_extra)
    host.lib.bl_test_set_slave_reg(WDC, control)
    host.lib.bl_test_wd_model(kick_ago_ms)
    host.lib.bl_test_wd_intn_low(1 if intn_low else 0)


class HookBuild(unittest.TestCase):
    """The hook built like bootloader.cmake does: serve (BL_SBC_WATCHDOG_SERVE), leave (BL_SBC_WATCHDOG_SERVICE) or
    as the updater (no define)."""

    DEFINES: tuple[str, ...] = UPDATER_BUILD

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        dll = Path(cls.tmp.name) / "bl_wd_host.dll"
        result = plat.build(dll, defines=cls.DEFINES)
        assert result.returncode == 0, result.stderr
        cls.host = Host(dll)

    @classmethod
    def tearDownClass(cls):
        cls.host.close()
        cls.tmp.cleanup()

    def tearDown(self):
        # board rule: the MTPNV (0x71..0x75) is never written, whatever the scenario did
        for reg in self.host.written_registers():
            self.assertNotIn(reg, range(0x71, 0x76), "the hook wrote to the MTPNV")


class ServeTest(HookBuild):
    """bootloader.sbc_watchdog: serve."""

    DEFINES = SERVE_BUILD

    def test_sdms0_power_on_is_served_while_the_bootloader_runs(self):
        """No software development mode: Normal mode starts the watchdog - the bootloader serves it, CAN is on."""
        h = self.host
        power_on_sbc(h, sdms=False)
        h.sbc_init(configure=True)
        diag = h.diag()
        self.assertEqual(diag["stage"], 2)
        self.assertEqual(h.lib.bl_test_slave_reg(MODE), NORMAL)
        self.assertEqual(diag["canControlAfter"] & UJA["UJA113X_CMC_MASK"], UJA["UJA113X_CMC_ACTIVE"])
        self.assertEqual(h.lib.bl_test_slave_reg(WDC), FIXED)
        self.assertEqual((h.wd(CONTROL_BEFORE), h.wd(CONTROL_AFTER), h.wd(SERVED)), (RESET_DEFAULT, FIXED, 1))
        self.assertEqual(h.wd(REASON), REASON_PROJECT)
        # already in Standby: no mode write before the setting, which is written twice and read back
        self.assertEqual(h.written_registers()[:2], [WDC, WDC])
        h.run_bootloader(10_000)
        overflows, illegal, window, max_gap = h.events()
        self.assertEqual((overflows, illegal, window), (0, 0, 0))
        self.assertLessEqual(max_gap, TRIGGER_MS + 1)
        self.assertGreaterEqual(h.wd(TRIGGERS), 10_000 // (TRIGGER_MS + 1))
        # twice per trigger (data sheet 7.2.5 case B), always the same value: a trigger, never a change
        triggers = [w for w in h.writes() if (w >> UJA["UJA113X_SPI_ADDRESS_SHIFT"]) == WDC]
        self.assertEqual(set(triggers), {frame(WDC, FIXED, read=False)})

    def test_jump_hands_a_whole_period_to_the_application(self):
        """Assumption of use: after the jump the application takes the watchdog over within 1024 ms."""
        h = self.host
        power_on_sbc(h, sdms=False)
        h.sbc_init(configure=True)
        h.run_bootloader(600)
        while (h.lib.bl_test_millis() - h.wd(LAST_TRIGGER)) < TRIGGER_MS - 5:
            h.run_bootloader(1)  # just before the next periodic trigger is due
        triggers = h.wd(TRIGGERS)
        h.jump()
        self.assertEqual(h.wd(TRIGGERS), triggers + 1, "no trigger right before the jump")
        h.lib.bl_test_idle(PERIOD_MS - 2)
        self.assertEqual(h.events()[OVERFLOWS], 0)
        h.lib.bl_test_idle(60)  # the application did not take over in time
        self.assertEqual(h.events()[OVERFLOWS], 1)

    def test_mcu_only_reset_with_the_window_watchdog_of_the_application(self):
        """SBC still in Normal mode with a window watchdog of unknown phase: Standby first, then the setting."""
        h = self.host
        power_on_sbc(h, sdms=False, mode=NORMAL, control=wdc("window", 64), kick_ago_ms=10)
        h.sbc_init(configure=True)
        self.assertEqual(h.diag()["stage"], 2)
        self.assertEqual(h.writes()[0], frame(MODE, STANDBY, read=False), "register 0x00 written in Normal mode")
        self.assertEqual(h.written_registers()[1:3], [WDC, WDC])
        self.assertEqual(h.events()[:3], [0, 0, 0])
        self.assertEqual(h.lib.bl_test_slave_reg(WDC), FIXED)
        h.run_bootloader(3000)
        self.assertEqual(h.events()[:3], [0, 0, 0])

    def test_development_mode_autonomous_is_left_alone(self):
        """SDMS 1 and autonomous (eval board): off in every mode - not a single write to register 0x00, as with
        leave; the bootloader behaves as before BL-28 (the board proof with serve)."""
        h = self.host
        power_on_sbc(h, sdms=True)
        h.sbc_init(configure=True)
        self.assertEqual(h.diag()["stage"], 2)
        h.run_bootloader(2000)
        h.jump()
        self.assertNotIn(WDC, h.written_registers())
        self.assertEqual((h.wd(SERVED), h.wd(REASON)), (0, REASON_PROJECT))
        self.assertEqual(len(h.writes()), 3)  # regulator, CAN, Normal mode - the writes of leave
        self.assertEqual(h.events()[:3], [0, 0, 0])

    def test_development_mode_with_a_timeout_watchdog_of_the_application(self):
        """SDMS 1, but the application had set timeout: it runs in Normal mode, so it is set to autonomous (off)."""
        h = self.host
        power_on_sbc(h, sdms=True, mode=NORMAL, control=wdc("timeout", 256), kick_ago_ms=20)
        h.sbc_init(configure=True)
        self.assertEqual(h.diag()["stage"], 2)
        self.assertEqual(h.writes()[0], frame(MODE, STANDBY, read=False))
        self.assertEqual((h.lib.bl_test_slave_reg(WDC), h.wd(SERVED)), (FIXED, 0))
        h.run_bootloader(5000)
        h.jump()
        self.assertEqual(h.events()[:3], [0, 0, 0])
        self.assertEqual(h.written_registers().count(WDC), 2, "an autonomous watchdog in development mode is off")

    def test_forced_normal_mode_needs_nothing(self):
        h = self.host
        power_on_sbc(h, sdms=False, status_extra=FNMS)
        h.sbc_init(configure=True)
        h.run_bootloader(500)
        self.assertEqual(h.diag()["stage"], 2)
        self.assertNotIn(WDC, h.written_registers())
        self.assertEqual(h.wd(SERVED), 0)

    def test_setting_not_taken_stops_before_normal_mode(self):
        """The SBC does not take the setting: no Normal mode (the watchdog would run with an unknown period)."""
        h = self.host
        power_on_sbc(h, sdms=False)
        h.lib.bl_test_wd_lock(1)
        h.sbc_init(configure=True)
        self.assertEqual(h.diag()["stage"], 0xE5)
        self.assertNotIn(frame(MODE, NORMAL, read=False), h.writes())
        self.assertEqual(h.wd(SERVED), 0)
        h.lib.bl_test_idle(5000)
        # PLAT-117: the reset default (timeout, data sheet Table 7) runs in Standby too (Table 8) - the SBC resets
        self.assertGreater(h.events()[OVERFLOWS], 0)
        self.assertFalse(h.lib.bl_test_watchdog_off())

    def test_sbc_that_never_answers_gets_nothing(self):
        h = self.host
        power_on_sbc(h, sdms=False)
        h.lib.bl_test_set_not_ready(0xFFFFFFFF, 0x00)
        h.sbc_init(configure=True)
        h.run_bootloader(500)
        self.assertEqual((h.diag()["stage"], h.writes(), h.wd(SERVED)), (0xE4, [], 0))

    def test_read_only_hook_serves_only_the_watchdog(self):
        """configure false (the SBC is not the transceiver of the bootloader bus; nxconfig refuses serve for this
        case, bootloader.sbc_watchdog): as with leave since BL-42 - with SDMS 0 only the watchdog is set up and
        served, nothing else is written (ReadOnlyTest)."""
        h = self.host
        power_on_sbc(h, sdms=False)
        h.sbc_init(configure=False)
        h.run_bootloader(500)
        self.assertEqual((h.diag()["stage"], h.wd(SERVED), h.wd(REASON)), (1, 1, REASON_SDMS0_READ))
        self.assertEqual(set(h.written_registers()), {WDC})
        self.assertEqual(h.lib.bl_test_slave_reg(MODE), STANDBY)

    def test_updater_still_refuses_without_development_mode(self):
        """The updater (bl_board_environment_ok) needs the watchdog off; serving does not change that."""
        h = self.host
        power_on_sbc(h, sdms=False)
        h.sbc_init(configure=True)
        self.assertFalse(h.lib.bl_test_watchdog_off())


def serve_until_just_before_a_trigger(h: Host, ms: int = 600):
    """The bootloader runs `ms`, then on until the next periodic trigger is just due (worst case for the jump)."""
    h.run_bootloader(ms)
    while (h.lib.bl_test_millis() - h.wd(LAST_TRIGGER)) < TRIGGER_MS - 5:
        h.run_bootloader(1)


class LeaveTest(HookBuild):
    """bootloader.sbc_watchdog: leave (default), the bootloader build (BL_SBC_WATCHDOG_SERVICE, BL-41)."""

    DEFINES = LEAVE_BUILD

    def test_service_in_this_build(self):
        self.assertTrue(self.host.serving, "the bootloader build must contain bl_device_service() (BL-41)")

    def test_sdms0_is_served_like_serve(self):
        """BL-41: SDMS 0 with the reset default of the data sheet (WMC timeout 128 ms, Table 7, running in Standby,
        Table 8). Before BL-41 nobody served it: SBC resets (WDI first, then a reset with WDI pending, 7.2.3), the
        model counted overflows here. Now: the fixed setting in Standby, Normal mode with CAN, no reset over
        seconds."""
        h = self.host
        power_on_sbc(h, sdms=False)
        h.sbc_init(configure=True)
        diag = h.diag()
        self.assertEqual(diag["stage"], 2)
        self.assertEqual(h.lib.bl_test_slave_reg(MODE), NORMAL)
        self.assertEqual(diag["canControlAfter"] & UJA["UJA113X_CMC_MASK"], UJA["UJA113X_CMC_ACTIVE"])
        self.assertEqual((h.wd(CONTROL_BEFORE), h.wd(CONTROL_AFTER), h.wd(SERVED), h.wd(REASON)),
                         (RESET_DEFAULT, FIXED, 1, REASON_SDMS0))
        self.assertEqual(h.written_registers()[:2], [WDC, WDC])  # already in Standby: setting first, twice
        h.run_bootloader(10_000)
        overflows, illegal, window, max_gap = h.events()
        self.assertEqual((overflows, illegal, window), (0, 0, 0))
        self.assertLessEqual(max_gap, TRIGGER_MS + 1)
        self.assertGreaterEqual(h.wd(TRIGGERS), 10_000 // (TRIGGER_MS + 1))
        triggers = [w for w in h.writes() if (w >> UJA["UJA113X_SPI_ADDRESS_SHIFT"]) == WDC]
        self.assertEqual(set(triggers), {frame(WDC, FIXED, read=False)})

    def test_sdms0_jump_hands_a_whole_period_to_the_application(self):
        """BL-41 with an application: the last trigger right before the jump, then 1024 ms for the application
        (AoU-34) instead of what was left of 128 ms since the SBC reset."""
        h = self.host
        power_on_sbc(h, sdms=False)
        h.sbc_init(configure=True)
        serve_until_just_before_a_trigger(h)
        triggers = h.wd(TRIGGERS)
        h.jump()
        self.assertEqual(h.wd(TRIGGERS), triggers + 1, "no trigger right before the jump")
        h.lib.bl_test_idle(PERIOD_MS - 2)
        self.assertEqual(h.events()[OVERFLOWS], 0)
        h.lib.bl_test_idle(60)  # the application did not take over in time
        self.assertEqual(h.events()[OVERFLOWS], 1)

    def test_sdms0_mcu_only_reset_with_the_window_watchdog_of_the_application(self):
        h = self.host
        power_on_sbc(h, sdms=False, mode=NORMAL, control=wdc("window", 64), kick_ago_ms=10)
        h.sbc_init(configure=True)
        self.assertEqual((h.diag()["stage"], h.wd(REASON)), (2, REASON_SDMS0))
        self.assertEqual(h.writes()[0], frame(MODE, STANDBY, read=False), "register 0x00 written in Normal mode")
        h.run_bootloader(3000)
        self.assertEqual(h.events()[:3], [0, 0, 0])

    def test_sdms0_setting_not_taken_stops_before_normal_mode(self):
        h = self.host
        power_on_sbc(h, sdms=False)
        h.lib.bl_test_wd_lock(1)
        h.sbc_init(configure=True)
        self.assertEqual((h.diag()["stage"], h.wd(SERVED), h.wd(REASON)), (0xE5, 0, REASON_SDMS0))
        self.assertNotIn(frame(MODE, NORMAL, read=False), h.writes())
        # fault case as with serve: an SBC that does not take a write is not served further - it resets
        h.lib.bl_test_idle(5000)
        self.assertGreater(h.events()[OVERFLOWS], 0)

    def test_sdms0_forced_normal_mode_needs_nothing(self):
        h = self.host
        power_on_sbc(h, sdms=False, status_extra=FNMS)
        h.sbc_init(configure=True)
        h.run_bootloader(500)
        self.assertEqual(h.diag()["stage"], 2)
        self.assertNotIn(WDC, h.written_registers())
        self.assertEqual(h.wd(SERVED), 0)

    def test_sdms1_enters_normal_mode_without_touching_the_watchdog(self):
        """leave with software development mode: unchanged by BL-41."""
        h = self.host
        power_on_sbc(h, sdms=True)
        h.sbc_init(configure=True)
        self.assertEqual(h.diag()["stage"], 2)
        h.run_bootloader(5000)
        h.jump()
        self.assertNotIn(WDC, h.written_registers())
        self.assertEqual(len(h.writes()), 3)  # regulator, CAN, Normal mode
        self.assertEqual((h.wd(SERVED), h.wd(REASON), h.wd(TRIGGERS)), (0, REASON_NONE, 0))
        self.assertEqual(h.events()[:3], [0, 0, 0])

    def test_sdms1_mcu_only_reset_leaves_the_watchdog_of_the_application(self):
        """leave with SDMS 1 after an MCU-only reset: a timeout watchdog the application had set is not touched
        (as before BL-41; with serve it would be set to autonomous, i.e. off)."""
        h = self.host
        power_on_sbc(h, sdms=True, mode=NORMAL, control=wdc("timeout", 256), kick_ago_ms=20)
        h.sbc_init(configure=True)
        self.assertEqual((h.diag()["stage"], h.wd(REASON)), (2, REASON_NONE))
        self.assertNotIn(WDC, h.written_registers())
        self.assertEqual(h.lib.bl_test_slave_reg(WDC), wdc("timeout", 256))

    def test_updater_still_refuses_without_development_mode(self):
        h = self.host
        power_on_sbc(h, sdms=False)
        h.sbc_init(configure=True)
        self.assertFalse(h.lib.bl_test_watchdog_off())


REGULATOR, CAN_CONTROL = UJA["UJA113X_REG_REGULATOR_CONTROL"], UJA["UJA113X_REG_CAN_CONTROL"]


class ReadOnlyTest(HookBuild):
    """BL-42 (safety review bootloader 0.3.10 2026-10-07, BL41-2 b): the SBC is only read (sbc_init(false), not in
    bootloader.init) in the bootloader build. With SDMS 0 the hook sets up only the watchdog (in Standby) and serves
    it; V2, CAN and the mode stay as found (an SBC in Normal mode goes to Standby for the setting). With SDMS 1 it
    stays read only."""

    DEFINES = LEAVE_BUILD

    def assert_watchdog_only(self, h: Host):
        self.assertEqual(set(h.written_registers()) - {MODE}, {WDC})
        for w in h.writes():
            if (w >> UJA["UJA113X_SPI_ADDRESS_SHIFT"]) == MODE:
                self.assertEqual(w, frame(MODE, STANDBY, read=False), "read only must never enter Normal mode")
        self.assertEqual(h.lib.bl_test_slave_reg(MODE), STANDBY)
        # regulator and CAN control untouched (the model starts every register with its own value)
        self.assertEqual((h.lib.bl_test_slave_reg(REGULATOR), h.lib.bl_test_slave_reg(CAN_CONTROL)),
                         (0x10 + REGULATOR, 0x10 + CAN_CONTROL))

    def test_sdms0_power_on_no_reset_over_seconds(self):
        """SBC reset default (timeout, 128 ms, running in Standby - before BL-42 nobody served it here, see
        UpdaterTest): fixed setting in Standby, no mode write, served for 10 s without an overflow."""
        for intn_low in (False, True):
            with self.subTest(intn_low=intn_low):
                h = self.host
                power_on_sbc(h, sdms=False, intn_low=intn_low)
                h.sbc_init(configure=False)
                self.assertEqual(h.diag()["stage"], 1)
                self.assertEqual((h.wd(CONTROL_BEFORE), h.wd(CONTROL_AFTER), h.wd(SERVED), h.wd(REASON)),
                                 (RESET_DEFAULT, FIXED, 1, REASON_SDMS0_READ))
                self.assertEqual(h.written_registers(), [WDC, WDC])  # already in Standby: setting only, twice
                h.run_bootloader(10_000)
                overflows, illegal, window, max_gap = h.events()
                self.assertEqual((overflows, illegal, window), (0, 0, 0))
                # with INTN1 low it runs in Standby from the setting on: the first gap holds the read back after the
                # setting (lastTriggerMs is taken after it, a few model milliseconds) - far below the 1024 ms
                self.assertLessEqual(max_gap, TRIGGER_MS + 16)
                self.assertGreaterEqual(h.wd(TRIGGERS), 10_000 // (TRIGGER_MS + 1))
                self.assert_watchdog_only(h)
                triggers = [w for w in h.writes() if (w >> UJA["UJA113X_SPI_ADDRESS_SHIFT"]) == WDC]
                self.assertEqual(set(triggers), {frame(WDC, FIXED, read=False)})

    def test_sdms0_jump_hands_a_whole_period_to_the_application_intn_low(self):
        """Interrupt pending: autonomous runs in Standby as timeout (1024 ms) - the last trigger right before the
        jump gives the application the whole period."""
        h = self.host
        power_on_sbc(h, sdms=False, intn_low=True)
        h.sbc_init(configure=False)
        serve_until_just_before_a_trigger(h)
        triggers = h.wd(TRIGGERS)
        h.jump()
        self.assertEqual(h.wd(TRIGGERS), triggers + 1, "no trigger right before the jump")
        h.lib.bl_test_idle(PERIOD_MS - 2)
        self.assertEqual(h.events()[OVERFLOWS], 0)
        h.lib.bl_test_idle(60)  # the application did not take over in time
        self.assertEqual(h.events()[OVERFLOWS], 1)

    def test_sdms0_jump_hands_a_whole_period_to_the_application_in_normal_mode(self):
        """INTN1 high: off in Standby; the application's Normal mode starts the fixed setting (1024 ms, not the
        128 ms of the reset default) with a whole period."""
        h = self.host
        power_on_sbc(h, sdms=False)
        h.sbc_init(configure=False)
        serve_until_just_before_a_trigger(h)
        h.jump()
        h.lib.bl_test_idle(3000)  # start-up of the application in Standby
        self.assertEqual(h.events()[OVERFLOWS], 0)
        h.lib.bl_test_slave_write(MODE, NORMAL)
        h.lib.bl_test_idle(PERIOD_MS - 2)
        self.assertEqual(h.events()[:3], [0, 0, 0])
        h.lib.bl_test_idle(60)
        self.assertEqual(h.events()[OVERFLOWS], 1)

    def test_sdms0_mcu_only_reset_with_the_window_watchdog_of_the_application(self):
        """SBC still in Normal mode (MCU-only reset), window watchdog of unknown phase: the setting needs Standby
        (data sheet 7.2, 7.3.3), so Standby first - and the SBC stays there, no Normal mode, V2/CAN not written."""
        h = self.host
        power_on_sbc(h, sdms=False, mode=NORMAL, control=wdc("window", 64), kick_ago_ms=10)
        h.sbc_init(configure=False)
        self.assertEqual((h.diag()["stage"], h.wd(SERVED), h.wd(REASON)), (1, 1, REASON_SDMS0_READ))
        self.assertEqual(h.writes()[0], frame(MODE, STANDBY, read=False), "register 0x00 written in Normal mode")
        self.assertEqual(h.written_registers()[1:3], [WDC, WDC])
        h.run_bootloader(3000)
        self.assertEqual(h.events()[:3], [0, 0, 0])
        self.assert_watchdog_only(h)

    def test_sdms0_setting_not_taken(self):
        h = self.host
        power_on_sbc(h, sdms=False)
        h.lib.bl_test_wd_lock(1)
        h.sbc_init(configure=False)
        self.assertEqual((h.diag()["stage"], h.wd(SERVED), h.wd(REASON)), (0xE5, 0, REASON_SDMS0_READ))
        self.assert_watchdog_only(h)

    def test_sdms0_forced_normal_mode_needs_nothing(self):
        h = self.host
        power_on_sbc(h, sdms=False, status_extra=FNMS)
        h.sbc_init(configure=False)
        h.run_bootloader(500)
        self.assertEqual((h.diag()["stage"], h.writes(), h.wd(SERVED)), (1, [], 0))

    def test_sdms1_stays_read_only(self):
        """Software development mode: read only as before BL-42 - also after an MCU-only reset with a timeout
        watchdog of the application (that is not this hook's business with leave)."""
        for mode, control in ((STANDBY, None), (NORMAL, wdc("timeout", 256))):
            with self.subTest(mode=mode):
                h = self.host
                power_on_sbc(h, sdms=True, mode=mode, control=control, kick_ago_ms=20)
                h.sbc_init(configure=False)
                h.run_bootloader(500)
                h.jump()
                self.assertEqual((h.diag()["stage"], h.writes()), (1, []))
                self.assertEqual((h.wd(SERVED), h.wd(REASON), h.wd(TRIGGERS)), (0, REASON_NONE, 0))


class UpdaterTest(HookBuild):
    """The updater build (no define): leave as before BL-41 - it reads, stops at 0xD0 and refuses (AoU-34)."""

    DEFINES = UPDATER_BUILD

    def test_no_service_in_this_build(self):
        self.assertFalse(self.host.serving, "the updater build must not contain bl_device_service()")

    def test_read_only_sdms0_reads_only(self):
        """Read only stays read only in the updater (it refuses with SDMS 0). This is also the hazard BL-42 removes
        from the bootloader: the reset default runs in Standby and nobody serves it - the SBC resets."""
        h = self.host
        power_on_sbc(h, sdms=False)
        h.sbc_init(configure=False)
        self.assertEqual((h.diag()["stage"], h.writes()), (1, []))
        self.assertFalse(h.lib.bl_test_watchdog_off())
        h.lib.bl_test_idle(5000)
        self.assertGreater(h.events()[OVERFLOWS], 0)

    def test_sdms0_leaves_the_sbc_in_standby(self):
        """SDMS 0: no write at all, the SBC stays in Standby; the updater refuses (bl_board_environment_ok)."""
        h = self.host
        power_on_sbc(h, sdms=False)
        h.sbc_init(configure=True)
        self.assertEqual((h.diag()["stage"], h.writes()), (0xD0, []))
        self.assertFalse(h.lib.bl_test_watchdog_off())
        self.assertEqual(h.lib.bl_test_slave_reg(MODE), STANDBY)

    def test_sdms1_enters_normal_mode_without_touching_the_watchdog(self):
        h = self.host
        power_on_sbc(h, sdms=True)
        h.sbc_init(configure=True)
        self.assertEqual(h.diag()["stage"], 2)
        self.assertNotIn(WDC, h.written_registers())
        self.assertTrue(h.lib.bl_test_watchdog_off())
        h.lib.bl_test_idle(5000)
        self.assertEqual(h.events()[:3], [0, 0, 0])


class MutationBase(unittest.TestCase):
    """Builds deliberately broken hooks; subclasses name the build, the mutations and the scenarios."""

    DEFINES: tuple[str, ...] = ()
    MUTATIONS: dict[str, tuple[str, str]] = {}

    @staticmethod
    def scenarios_pass(h: Host) -> bool:
        raise NotImplementedError

    def build_variant(self, tmp: Path, name: str, old: str = "", new: str = "") -> Host:
        text = plat.HOOK_C.read_text(encoding="utf-8")
        if old:
            self.assertEqual(text.count(old), 1, f"mutation {name!r} does not match the hook any more")
            text = text.replace(old, new)
        hook = tmp / re.sub(r"\W+", "_", name) / "sbc_uja113x.c"
        hook.parent.mkdir(parents=True)
        hook.write_text(text, encoding="utf-8")
        dll = hook.parent / "hook.dll"
        result = plat.build(dll, defines=self.DEFINES,
                            sources=(BOOTLOADER / "platforms" / "s32k1xx" / "bl_spi_s32k1xx.c", hook,
                                     HERE / "bl_spi_host.c"))
        self.assertEqual(result.returncode, 0, f"{name}: {result.stderr}")
        return Host(dll)

    def check_mutations(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            host = self.build_variant(Path(tmp), "unmodified")
            try:
                self.assertTrue(self.scenarios_pass(host))
            finally:
                host.close()
            for name, (old, new) in self.MUTATIONS.items():
                with self.subTest(mutation=name):
                    host = self.build_variant(Path(tmp), name, old, new)
                    try:
                        self.assertFalse(self.scenarios_pass(host), f"mutation {name!r} was not detected")
                    finally:
                        host.close()


class LeaveMutationTest(MutationBase):
    """Meaningfulness of LeaveTest (BL-41): every deliberately broken SDMS decision of leave must fail a scenario."""

    DEFINES = LEAVE_BUILD
    MUTATIONS = {
        "SDMS query inverted": ("!= 0U) ? SBC_WD_REASON_NONE : SBC_WD_REASON_SDMS0;",
                                "!= 0U) ? SBC_WD_REASON_SDMS0 : SBC_WD_REASON_NONE;"),
        "no service with SDMS 0": ("!= 0U) ? SBC_WD_REASON_NONE : SBC_WD_REASON_SDMS0;",
                                   "!= 0U) ? SBC_WD_REASON_NONE : SBC_WD_REASON_NONE;"),
        "served with SDMS 1 too": ("!= 0U) ? SBC_WD_REASON_NONE : SBC_WD_REASON_SDMS0;",
                                   "!= 0U) ? SBC_WD_REASON_SDMS0 : SBC_WD_REASON_SDMS0;"),
        "setup result ignored": ("if ((reason != SBC_WD_REASON_NONE) && !watchdog_setup(d))",
                                 "if ((reason != SBC_WD_REASON_NONE) && !watchdog_setup(d) && false)"),
        "no trigger before the jump": ("(!beforeJump && ((nowMs", "((void)beforeJump, ((nowMs"),
    }

    @staticmethod
    def scenarios_pass(h: Host) -> bool:
        checks = []
        power_on_sbc(h, sdms=False)
        h.sbc_init(configure=True)
        h.run_bootloader(3000)
        checks.append(h.diag()["stage"] == 2 and h.events()[:3] == [0, 0, 0] and h.wd(REASON) == REASON_SDMS0)
        serve_until_just_before_a_trigger(h, 3 * TRIGGER_MS)
        h.jump()
        h.lib.bl_test_idle(PERIOD_MS - 2)
        checks.append(h.events()[OVERFLOWS] == 0)
        power_on_sbc(h, sdms=True)
        h.sbc_init(configure=True)
        h.run_bootloader(500)
        checks.append(h.diag()["stage"] == 2 and WDC not in h.written_registers() and len(h.writes()) == 3
                      and h.wd(REASON) == REASON_NONE)
        # SDMS 1 after an MCU-only reset with a timeout watchdog of the application: leave does not touch it
        power_on_sbc(h, sdms=True, mode=NORMAL, control=wdc("timeout", 256), kick_ago_ms=20)
        h.sbc_init(configure=True)
        checks.append(WDC not in h.written_registers())
        power_on_sbc(h, sdms=False)
        h.lib.bl_test_wd_lock(1)
        h.sbc_init(configure=True)
        checks.append(h.diag()["stage"] == 0xE5 and frame(MODE, NORMAL, read=False) not in h.writes())
        return all(checks)

    def test_unmodified_hook_passes_and_every_mutation_fails(self):
        self.check_mutations()


class ReadOnlyMutationTest(MutationBase):
    """Meaningfulness of ReadOnlyTest (BL-42): every deliberately broken read-only path must fail a scenario."""

    DEFINES = LEAVE_BUILD
    MUTATIONS = {
        "read only: SDMS query inverted": ("(!developmentMode) ? SBC_WD_REASON_SDMS0_READ : SBC_WD_REASON_NONE",
                                           "(developmentMode) ? SBC_WD_REASON_SDMS0_READ : SBC_WD_REASON_NONE"),
        "read only: no service with SDMS 0": ("(!developmentMode) ? SBC_WD_REASON_SDMS0_READ : SBC_WD_REASON_NONE",
                                              "(!developmentMode) ? SBC_WD_REASON_NONE : SBC_WD_REASON_NONE"),
        "read only: served with SDMS 1 too": ("(!developmentMode) ? SBC_WD_REASON_SDMS0_READ : SBC_WD_REASON_NONE",
                                              "(!developmentMode) ? SBC_WD_REASON_SDMS0_READ : SBC_WD_REASON_SDMS0_READ"),
        "read only: setup result ignored": ("if ((readReason != SBC_WD_REASON_NONE) && !watchdog_setup(d))",
                                            "if ((readReason != SBC_WD_REASON_NONE) && !watchdog_setup(d) && false)"),
        "read only: falls through to V2, CAN and Normal mode": (
            "return; /* BL-42: read only - no V2, no CAN, no Normal mode */", "/* falls through */"),
        "no Standby before the setting": ("ok = sbc_write(UJA113X_REG_MODE_CONTROL, UJA113X_MC_STANDBY);", "ok = true;"),
        "not served at all": ("    if ((w->served == 0U) || ", "    if (true || "),
        "no trigger before the jump": ("(!beforeJump && ((nowMs", "((void)beforeJump, ((nowMs"),
    }

    @staticmethod
    def scenarios_pass(h: Host) -> bool:
        def watchdog_only() -> bool:
            modes = [w for w in h.writes() if (w >> UJA["UJA113X_SPI_ADDRESS_SHIFT"]) == MODE]
            return (set(h.written_registers()) - {MODE} == {WDC} and h.lib.bl_test_slave_reg(MODE) == STANDBY
                    and all(w == frame(MODE, STANDBY, read=False) for w in modes))

        checks = []
        power_on_sbc(h, sdms=False, intn_low=True)
        h.sbc_init(configure=False)
        h.run_bootloader(3000)
        checks.append(h.diag()["stage"] == 1 and h.events()[:3] == [0, 0, 0] and h.wd(REASON) == REASON_SDMS0_READ
                      and watchdog_only())
        serve_until_just_before_a_trigger(h, 3 * TRIGGER_MS)
        h.jump()
        h.lib.bl_test_idle(PERIOD_MS - 2)
        checks.append(h.events()[OVERFLOWS] == 0)
        power_on_sbc(h, sdms=False, mode=NORMAL, control=wdc("window", 64), kick_ago_ms=10)
        h.sbc_init(configure=False)
        h.run_bootloader(500)
        checks.append(h.events()[:3] == [0, 0, 0] and watchdog_only())
        for mode, control in ((STANDBY, None), (NORMAL, wdc("timeout", 256))):
            power_on_sbc(h, sdms=True, mode=mode, control=control, kick_ago_ms=20)
            h.sbc_init(configure=False)
            h.run_bootloader(200)
            checks.append(h.writes() == [] and h.wd(REASON) == REASON_NONE)
        power_on_sbc(h, sdms=False)
        h.lib.bl_test_wd_lock(1)
        h.sbc_init(configure=False)
        checks.append(h.diag()["stage"] == 0xE5)
        return all(checks)

    def test_unmodified_hook_passes_and_every_mutation_fails(self):
        self.check_mutations()


class ServeMutationTest(MutationBase):
    """Meaningfulness of ServeTest: every deliberately broken serving hook must fail at least one scenario."""

    DEFINES = SERVE_BUILD
    MUTATIONS = {
        "trigger only after a whole period": ("#define WATCHDOG_TRIGGER_MS (WATCHDOG_PERIOD_MS / 4UL)",
                                              "#define WATCHDOG_TRIGGER_MS (WATCHDOG_PERIOD_MS + 8UL)"),
        "no trigger before the jump": ("(!beforeJump && ((nowMs", "((void)beforeJump, ((nowMs"),
        "short period kept": ("| UJA113X_NWP_1024MS))", "| UJA113X_NWP_128MS))"),
        "no Standby before the setting": ("ok = sbc_write(UJA113X_REG_MODE_CONTROL, UJA113X_MC_STANDBY);", "ok = true;"),
        "setting in development mode": ("    return (status & UJA113X_SDMS_MASK) == 0U;", "    return true;"),
        "served in development mode": ("w->served = (uint8_t)(((d->watchdogStatus & UJA113X_SDMS_MASK) == 0U) ? 1U : 0U);",
                                       "w->served = 1U;"),
        "no read back": ("|| ((w->controlAfter & WATCHDOG_MASK) != WATCHDOG_CONTROL)", ""),
        "not served at all": ("    if ((w->served == 0U) || ", "    if (true || "),
    }

    @staticmethod
    def scenarios_pass(h: Host) -> bool:
        checks = []
        power_on_sbc(h, sdms=False)
        h.sbc_init(configure=True)
        h.run_bootloader(3000)
        checks.append(h.diag()["stage"] == 2 and h.events()[:3] == [0, 0, 0])
        h.run_bootloader(3 * TRIGGER_MS)  # somewhere in the trigger cycle
        while (h.lib.bl_test_millis() - h.wd(LAST_TRIGGER)) < TRIGGER_MS - 5:
            h.run_bootloader(1)
        h.jump()
        h.lib.bl_test_idle(PERIOD_MS - 2)
        checks.append(h.events()[OVERFLOWS] == 0)
        power_on_sbc(h, sdms=False, mode=NORMAL, control=wdc("window", 64), kick_ago_ms=10)
        h.sbc_init(configure=True)
        h.run_bootloader(500)
        checks.append(h.events()[:3] == [0, 0, 0])
        power_on_sbc(h, sdms=True)
        h.sbc_init(configure=True)
        h.run_bootloader(500)
        checks.append(WDC not in h.written_registers())
        power_on_sbc(h, sdms=True, mode=NORMAL, control=wdc("timeout", 256), kick_ago_ms=20)
        h.sbc_init(configure=True)
        h.run_bootloader(1000)
        checks.append(h.written_registers().count(WDC) == 2 and h.events()[:3] == [0, 0, 0])
        power_on_sbc(h, sdms=False)
        h.lib.bl_test_wd_lock(1)
        h.sbc_init(configure=True)
        checks.append(h.diag()["stage"] == 0xE5)
        return all(checks)

    def test_unmodified_hook_passes_and_every_mutation_fails(self):
        self.check_mutations()


def _canflash_helpers():
    sys.path.insert(0, str(REPO / "tools" / "canflash"))
    import canflash  # noqa: E402
    import hostbuild  # noqa: E402
    import test_bootloader_host as tbh  # noqa: E402
    return canflash, hostbuild, tbh


try:
    import ziglang  # noqa: F401

    HAVE_ZIG = True
except ImportError:  # pragma: no cover
    HAVE_ZIG = False


@unittest.skipUnless(HAVE_ZIG, "ziglang not installed (host compiler)")
class CoreServiceTest(unittest.TestCase):
    """bl_poll() with BL_DEVICE_SERVICE (bootloader/src/core/bl_main.c) on the simulated HAL of bl_host.c, memory
    layout of the synthetic profile testproject_bootloader (not the eval board)."""

    @classmethod
    def setUpClass(cls):
        canflash, hostbuild, tbh = _canflash_helpers()
        cls.canflash, cls.tbh = canflash, tbh
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tree = Path(cls.tmp.name) / "tree"
        # a copy of the bootloader sources with the definition bootloader.cmake gives bl_main.c for serve; the
        # host part bl_host.c gets it too and records the calls instead of serving a device
        for sub in ("include", "src/core", "platforms", "test/host"):
            shutil.copytree(BOOTLOADER / sub, tree / "bootloader" / sub)
        for name in ("src/core/bl_main.c", "test/host/bl_host.c"):
            path = tree / "bootloader" / name
            path.write_text("#define BL_DEVICE_SERVICE 1\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
        layout = hostbuild.generate_layout(tbh.OTHER_PROJECT, Path(cls.tmp.name) / "gen", tbh.TEST_MCUDB)
        dll = hostbuild.build_bl_host(Path(cls.tmp.name) / "bl_host.dll", layout, source=tree, rebuild=True)
        cls.saved_layout = tbh.selected()
        canflash.use_layout(canflash.load_build(layout / "bootloader" / "bl_memory.h"))
        cls.bl = tbh.HostBootloader(dll)
        for name in ("host_service_calls", "host_service_before_jump", "host_service_after_jump",
                     "host_service_last_ms"):
            getattr(cls.bl.dll, name).restype = ctypes.c_uint32

    @classmethod
    def tearDownClass(cls):
        cls.canflash.use_layout(cls.saved_layout)
        if sys.platform == "win32":
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.FreeLibrary.argtypes = [ctypes.c_void_p]
            kernel32.FreeLibrary(ctypes.c_void_p(cls.bl.dll._handle))
        cls.tmp.cleanup()

    def setUp(self):
        self.bl.dll.host_erase_all()
        self.bl.dll.host_set_costs(20, 1)
        self.bl.dll.host_power_loss(ctypes.c_uint8(0))
        self.bl.dll.host_provision_test_key()

    def run_ms(self, ms):
        for _ in range(ms):
            self.bl.dll.host_poll(ctypes.c_uint32(1))

    def counts(self) -> tuple[int, int, int]:
        d = self.bl.dll
        return d.host_service_calls(), d.host_service_before_jump(), d.host_service_after_jump()

    def test_every_poll_serves_without_valid_application(self):
        self.bl.dll.host_power_on(0)
        self.run_ms(700)
        self.assertEqual(self.bl.dll.host_jump_address(), 0)
        self.assertEqual(self.counts(), (700, 0, 0))
        self.assertEqual(self.bl.dll.host_service_last_ms(), self.bl.dll.host_millis())

    def test_exactly_one_service_right_before_the_jump(self):
        cfg = self.canflash.CFG
        data = self.tbh.make_app_image(3000)
        self.bl.write(cfg["BL_APP_START"], data)
        self.bl.write(cfg["BL_APP_RECORD_ADDR"], self.canflash.app_record_full(data))
        self.bl.dll.host_power_on(0)
        self.run_ms(cfg["BL_STARTUP_WINDOW_MS"] + 50)
        self.assertEqual(self.bl.dll.host_jump_address(), cfg["BL_APP_START"])
        calls, before_jump, after_jump = self.counts()
        self.assertEqual((before_jump, after_jump), (1, 0))
        # one call per poll (the jump comes in poll BL_STARTUP_WINDOW_MS), plus the one before it; none after it
        self.assertEqual(calls, cfg["BL_STARTUP_WINDOW_MS"] + 1)

    def test_core_calls_the_service_only_with_the_definition(self):
        """Without the definition (a bootloader without a device hook that needs a service) the core compiles no
        call; bootloader.cmake of sbcUja113x defines it for every bootloader with that hook (BL-41)."""
        code = plat.without_comments(BOOTLOADER / "src" / "core" / "bl_main.c")
        calls = [m.start() for m in re.finditer(r"bl_device_service\(", code)]
        self.assertEqual(len(calls), 2)
        for pos in calls:
            before = code[:pos]
            self.assertGreater(before.rfind("#if defined(BL_DEVICE_SERVICE)"), before.rfind("#endif"))


class GeneratorTest(unittest.TestCase):
    """blgen: bl_board.cmake names the serving modules, bootloader.cmake turns them into per-source definitions."""

    @staticmethod
    def board_files(serve: bool) -> dict[str, str]:
        sys.path.insert(0, str(REPO / "tools" / "bswgen"))
        import blgen  # noqa: E402
        import nxconfig  # noqa: E402

        # PLAT-62: the own full test profile (SBC on CAN0 like the eval board) - the eval board project is private
        src = REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full"
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project.yaml"
            for f in (p for p in src.iterdir() if p.is_file()):   # an empty local swc/ folder is no input
                shutil.copy(f, Path(tmp) / f.name)
            text = project.read_text(encoding="utf-8").replace(
                "board: ../../boards/testboard_full",
                "board: " + (REPO / "tools/nxconfig/test/profiles/boards/testboard_full").as_posix(), 1)
            if serve:
                text = text.replace("bootloader:\n  can: CAN0\n", "bootloader:\n  can: CAN0\n  sbc_watchdog: serve\n", 1)
                assert "sbc_watchdog: serve" in text
            project.write_text(text, encoding="utf-8")
            config, messages = nxconfig.check(project)
            assert config is not None, [str(m) for m in messages]
            return blgen.board_files(config, source="test")

    def test_leave_and_serve(self):
        leave, serve = self.board_files(False), self.board_files(True)
        self.assertNotIn("NX_BL_WATCHDOG_SERVED_BY", leave["bootloader/bl_board.cmake"])  # as before BL-28
        self.assertIn("set(NX_BL_WATCHDOG_SERVED_BY sbcUja113x)", serve["bootloader/bl_board.cmake"])
        self.assertIn("sbc_init(true); /* SBC: switched on before CAN (Normal mode, V2, CAN active) */",
                      leave["bootloader/bl_board.h"])
        self.assertIn("watchdog served by the bootloader (BL-28)", serve["bootloader/bl_board.h"])
        # nothing else in the board part changes: bus, pins, SPI port
        strip = lambda t: re.sub(r"sbc_init\(true\);.*", "", t)  # noqa: E731
        self.assertEqual(strip(leave["bootloader/bl_board.h"]), strip(serve["bootloader/bl_board.h"]))

    def test_bootloader_cmake_defines_per_source(self):
        text = (BOOTLOADER / "devices" / "sbcUja113x" / "bootloader.cmake").read_text(encoding="utf-8")
        code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        condition = 'if ("sbcUja113x" IN_LIST NX_BL_WATCHDOG_SERVED_BY)'
        self.assertIn(condition, code)
        always, served = code.split(condition, 1)
        # source properties of the bootloader directory, not add_compile_definitions (would reach the updater)
        self.assertNotIn("add_compile_definitions", text)
        # BL-41: service and core call in every bootloader with the hook, before (outside) the serve condition
        self.assertRegex(always, r'set_property\(SOURCE "\$\{CMAKE_CURRENT_LIST_DIR\}/sbc_uja113x.c"[^)]*'
                                 r'BL_SBC_WATCHDOG_SERVICE\)')
        self.assertRegex(always, r'set_property\(SOURCE "\$\{CMAKE_CURRENT_SOURCE_DIR\}/src/core/bl_main.c"[^)]*'
                                 r'BL_DEVICE_SERVICE\)')
        self.assertNotIn("BL_SBC_WATCHDOG_SERVE)", always)
        self.assertRegex(served, r'set_property\(SOURCE "\$\{CMAKE_CURRENT_LIST_DIR\}/sbc_uja113x.c"[^)]*'
                                 r'BL_SBC_WATCHDOG_SERVE\)')


if __name__ == "__main__":
    unittest.main(verbosity=2)
