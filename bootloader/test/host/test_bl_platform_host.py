"""Host tests of the bootloader SPI path and of the oscillator range of the family s32k1xx (BL-05).

Covered without hardware:
- family SPI HAL (bootloader/platforms/s32k1xx/bl_spi_s32k1xx.c) against a register model with the vendor
  register layout (bootloader/test/host/model): clock gate, pins, dividers, frame format, transfer, timeout
- hook sbcUja113x (bootloader/devices/sbcUja113x/sbc_uja113x.c) through that HAL against a simulated UJA113x:
  read sequence, Normal mode with V2 and CAN active, the stages of sbc_diag including every error case
- start-up of the SBC (BL-26): an SPI model that answers 0x00 to the first N frames, and 0x00/0xFF for ever as
  the fault case; plausibility of identification and mode; mutants of the hook that the scenarios must catch
- family neutrality of the hook: it must compile with the neutral include paths alone (no mcu/mcu.h, no
  generated board part) - and must not compile when a family header is needed
- oscillator range SCG_SOSCCFG.RANGE derived from the crystal (4/8/16/40 MHz, 32 kHz) instead of a fixed 3
  (ARCH-09 finding B7), including the crystals the derivative database rejects

The test profile is a board of its own (bootloader/test/host/model/bootloader/bl_board.h: LPSPI1, chip select 2,
16 MHz crystal, other pins), not the eval board (architecture section 11, question 7).

Run: .venv/Scripts/python -m unittest bootloader/test/host/test_bl_platform_host.py
(needs ziglang in the .venv, like tools/canflash/hostbuild.py)
"""

import ctypes
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
BOOTLOADER = REPO / "bootloader"
MODEL = HERE / "model"
VENDOR = REPO / "external" / "openbsw" / "platforms" / "s32k1xx" / "bsp" / "bspMcu" / "include" / "3rdparty" / "nxp"
SBC_INCLUDE = REPO / "libs" / "bsw" / "sbcUja113x" / "include"
REGISTERS_H = SBC_INCLUDE / "sbc" / "uja113xRegisters.h"
HAL_C = BOOTLOADER / "platforms" / "s32k1xx" / "hal_s32k1xx.c"
HOOK_C = BOOTLOADER / "devices" / "sbcUja113x" / "sbc_uja113x.c"

# byte offsets of the LPSPI registers ([RM] chapter LPSPI, same values as the vendor header)
LPSPI_CR, LPSPI_SR, LPSPI_CFGR1, LPSPI_CCR, LPSPI_FCR, LPSPI_TCR = 0x10, 0x14, 0x24, 0x40, 0x58, 0x60
# fields of sbc_diag_t in declaration order (bootloader/devices/sbcUja113x/sbc_uja113x.h)
DIAG_FIELDS = ("stage", "id", "mode", "mainStatus", "watchdogStatus", "regulator", "canControl",
               "transceiverStatus", "startupControl", "sbcConfig", "modeAfter", "regulatorAfter",
               "canControlAfter", "transceiverStatusAfter", "readyMs")
# values of the test board part (bootloader/test/host/model/bootloader/bl_board.h)
SPI_INSTANCE, SPI_PCS, SPI_PRESCALE, SPI_SCKDIV = 1, 2, 2, 12
PCC_LPSPI1_INDEX, PCC_PORTB_INDEX, PCC_PORTD_INDEX = 45, 74, 76
PCC_CGC = 1 << 30


def uja_defines() -> dict[str, int]:
    """Register numbers and bit masks from the driver header, so the test cannot drift from the device."""
    text = REGISTERS_H.read_text(encoding="utf-8")
    found = re.findall(r"#define\s+(UJA113X_\w+)\s+(0x[0-9A-Fa-f]+|\d+)U?\b", text)
    return {name: int(value, 0) for name, value in found}


UJA = uja_defines()


def build(out: Path, defines: tuple[str, ...] = (), includes: tuple[Path, ...] = (), sources=(),
          shared: bool = True, werror: bool = True) -> subprocess.CompletedProcess:
    """Builds the host DLL (or an object) with zig cc, like tools/canflash/hostbuild.py."""
    include_dirs = includes or (MODEL, BOOTLOADER / "include", BOOTLOADER / "src" / "core",
                               BOOTLOADER / "devices" / "sbcUja113x", BOOTLOADER / "platforms" / "s32k1xx",
                               SBC_INCLUDE, VENDOR)
    files = sources or (BOOTLOADER / "platforms" / "s32k1xx" / "bl_spi_s32k1xx.c",
                        BOOTLOADER / "devices" / "sbcUja113x" / "sbc_uja113x.c",
                        HERE / "bl_spi_host.c")
    cmd = [sys.executable, "-m", "ziglang", "cc", "-target", "x86_64-windows-gnu",
           *(["-shared"] if shared else ["-c"]), "-O1", "-std=c99", "-Wall", "-Wextra",
           *(["-Werror"] if werror else []), *(f"-D{d}" for d in defines),
           *(f"-I{d}" for d in include_dirs), *(str(f) for f in files), "-o", str(out)]
    out.parent.mkdir(parents=True, exist_ok=True)
    return subprocess.run(cmd, capture_output=True, text=True)


class BlSpiHost:
    """The built DLL with the register model behind it."""

    def __init__(self, path: Path):
        self.lib = ctypes.CDLL(str(path))
        for name in ("bl_test_reset", "bl_test_set_ms_per_poll", "bl_test_set_stuck_after", "bl_test_spi_init",
                     "bl_test_sbc_init", "bl_test_set_slave_reg", "bl_test_set_not_ready"):
            getattr(self.lib, name).restype = None
        for name in ("bl_test_slave_reg", "bl_test_frame_errors", "bl_test_tx_count", "bl_test_tx_word",
                     "bl_test_millis", "bl_test_lpspi_reg", "bl_test_pcc", "bl_test_pcr", "bl_test_spi_transfer",
                     "bl_test_diag", "bl_test_watchdog_off", "bl_test_sosc_range", "bl_test_sosccfg",
                     "bl_test_osc_hz"):
            getattr(self.lib, name).restype = ctypes.c_uint32
        self.lib.bl_test_spi_transfer.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
        self.reset()

    def reset(self):
        self.lib.bl_test_reset()

    def spi_init(self, bits=16, cpol=0, cpha=1):
        self.lib.bl_test_spi_init(bits, cpol, cpha)

    def transfer(self, tx) -> tuple[bool, int]:
        rx = ctypes.c_uint32(0)
        ok = self.lib.bl_test_spi_transfer(tx, ctypes.byref(rx))
        return bool(ok), rx.value

    def sbc_init(self, configure=True):
        self.lib.bl_test_sbc_init(1 if configure else 0)

    def reg(self, offset, instance=SPI_INSTANCE) -> int:
        return self.lib.bl_test_lpspi_reg(instance, offset)

    def diag(self) -> dict[str, int]:
        return {name: self.lib.bl_test_diag(i) for i, name in enumerate(DIAG_FIELDS)}

    def tx_words(self) -> list[int]:
        return [self.lib.bl_test_tx_word(i) for i in range(self.lib.bl_test_tx_count())]

    def close(self):
        """Windows keeps a loaded DLL locked: without this the temporary directory cannot be removed."""
        if sys.platform == "win32":
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.FreeLibrary.argtypes = [ctypes.c_void_p]
            kernel32.FreeLibrary(ctypes.c_void_p(self.lib._handle))


def without_comments(path: Path) -> str:
    """Source without C comments, so a sentence about a header is not taken for an include."""
    return re.sub(r"/\*.*?\*/|//[^\n]*", "", path.read_text(encoding="utf-8"), flags=re.S)


def frame(reg: int, data: int = 0, read: bool = True) -> int:
    """SPI frame of the UJA113x as the hook must build it."""
    return (reg << UJA["UJA113X_SPI_ADDRESS_SHIFT"]) | (UJA["UJA113X_SPI_RO_BIT"] if read else 0) | data


class SpiHalTest(unittest.TestCase):
    """Family SPI HAL against the register model."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        dll = Path(cls.tmp.name) / "bl_spi_host.dll"
        result = build(dll)
        assert result.returncode == 0, result.stderr
        cls.host = BlSpiHost(dll)

    @classmethod
    def tearDownClass(cls):
        cls.host.close()
        cls.tmp.cleanup()

    def setUp(self):
        self.host.reset()

    def test_init_writes_the_wiring_of_the_board_part(self):
        self.host.spi_init()
        # clock gate of the port with the peripheral clock source of the board part (SOSCDIV2 = PCS 1)
        self.assertEqual(self.host.lib.bl_test_pcc(PCC_LPSPI1_INDEX), PCC_CGC | (1 << 24))
        # pins of the board part, with their port clock gates
        self.assertEqual(self.host.lib.bl_test_pcc(PCC_PORTB_INDEX), PCC_CGC)
        self.assertEqual(self.host.lib.bl_test_pcc(PCC_PORTD_INDEX), PCC_CGC)
        self.assertEqual(self.host.lib.bl_test_pcr(1, 14), 3 << 8)  # PTB14 MUX 3
        self.assertEqual(self.host.lib.bl_test_pcr(3, 3), 3 << 8)   # PTD3 MUX 3
        # master, no stall, module and debug enable
        self.assertEqual(self.host.reg(LPSPI_CFGR1), 0x9)
        self.assertEqual(self.host.reg(LPSPI_CR), 0x9)
        self.assertEqual(self.host.reg(LPSPI_FCR), 0x0)
        # dividers of the clock model in all four fields (SCKDIV, DBT, PCSSCK, SCKPCS): delays one SCK period
        self.assertEqual(self.host.reg(LPSPI_CCR), (SPI_SCKDIV << 24) | (SPI_SCKDIV << 16) | (SPI_SCKDIV << 8)
                         | SPI_SCKDIV)
        # frame of the device: 16 bit, CPOL 0, CPHA 1, prescaler and chip select of the board part
        self.assertEqual(self.host.reg(LPSPI_TCR), (1 << 30) | (SPI_PRESCALE << 27) | (SPI_PCS << 24) | 15)

    def test_other_instance_untouched(self):
        self.host.spi_init()
        for instance in (0, 2):
            self.assertEqual(self.host.reg(LPSPI_CR, instance), 0, "the HAL wrote to a foreign LPSPI")

    def test_frame_format_follows_the_caller(self):
        self.host.spi_init(bits=8, cpol=1, cpha=0)
        self.assertEqual(self.host.reg(LPSPI_TCR), (1 << 31) | (SPI_PRESCALE << 27) | (SPI_PCS << 24) | 7)
        self.host.spi_init(bits=32, cpol=0, cpha=0)
        self.assertEqual(self.host.reg(LPSPI_TCR) & 0xFFF, 31)

    def test_transfer_sends_and_receives(self):
        self.host.spi_init()
        self.host.lib.bl_test_set_slave_reg(0x07, 0xA3)
        ok, rx = self.host.transfer(frame(0x07))
        self.assertTrue(ok)
        self.assertEqual(rx & 0xFF, 0xA3)
        self.assertEqual(self.host.tx_words(), [frame(0x07)])
        self.assertEqual(self.host.lib.bl_test_frame_errors(), 0)

    def test_transfer_times_out_without_a_running_port(self):
        """Without bl_spi_init the module is disabled: no status flag, so the transfer must not hang."""
        ok, _ = self.host.transfer(frame(0x01))
        self.assertFalse(ok)
        self.assertLessEqual(self.host.lib.bl_test_millis(), 12, "the timeout must be the 10 ms of the HAL")

    def test_transfer_times_out_when_the_port_stops(self):
        self.host.spi_init()
        self.host.lib.bl_test_set_stuck_after(0)
        ok, _ = self.host.transfer(frame(0x01))
        self.assertFalse(ok)

    def test_wrong_frame_format_is_not_answered(self):
        """Mutation check of the model: a frame that is not the UJA113x format gets no answer."""
        self.host.spi_init(bits=8, cpol=0, cpha=1)
        ok, _ = self.host.transfer(frame(0x01))
        self.assertFalse(ok)
        self.assertEqual(self.host.lib.bl_test_frame_errors(), 1)


class SbcHookBase(unittest.TestCase):
    """Built hook with a simulated UJA113x that is ready and plausible (no tests of its own)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        dll = Path(cls.tmp.name) / "bl_hook_host.dll"
        result = build(dll)
        assert result.returncode == 0, result.stderr
        cls.host = BlSpiHost(dll)

    @classmethod
    def tearDownClass(cls):
        cls.host.close()
        cls.tmp.cleanup()

    # Identification of the simulated SBC: a code of the series that is not the one of the eval board (0x51), so the
    # hook cannot pass by knowing that board (architecture section 11, question 7).
    SBC_ID = 0x70

    def setUp(self):
        self.host.reset()
        self.software_development_mode(True)
        self.host.lib.bl_test_set_slave_reg(UJA["UJA113X_REG_IDENTIFICATION_0"], self.SBC_ID)
        self.host.lib.bl_test_set_slave_reg(UJA["UJA113X_REG_MODE_CONTROL"], UJA["UJA113X_MC_STANDBY"])

    def software_development_mode(self, on: bool):
        value = UJA["UJA113X_SDMS_MASK"] if on else 0
        self.host.lib.bl_test_set_slave_reg(UJA["UJA113X_REG_WATCHDOG_STATUS"], value)


class SbcHookTest(SbcHookBase):
    """Hook sbcUja113x through the family SPI HAL against the simulated UJA113x."""

    def test_read_only_reads_every_register_once(self):
        self.host.sbc_init(configure=False)
        diag = self.host.diag()
        self.assertEqual(diag["stage"], 1)
        expected = [frame(UJA[f"UJA113X_REG_{name}"]) for name in
                    ("IDENTIFICATION_0", "MODE_CONTROL", "MAIN_STATUS", "WATCHDOG_STATUS", "REGULATOR_CONTROL",
                     "CAN_CONTROL", "TRANSCEIVER_STATUS", "STARTUP_CONTROL", "SBC_CONFIG_CONTROL")]
        self.assertEqual(self.host.tx_words(), expected)
        self.assertEqual(diag["id"], self.host.lib.bl_test_slave_reg(UJA["UJA113X_REG_IDENTIFICATION_0"]))
        self.assertEqual(diag["mode"], self.host.lib.bl_test_slave_reg(UJA["UJA113X_REG_MODE_CONTROL"]))
        self.assertEqual(self.host.lib.bl_test_frame_errors(), 0)
        self.assertTrue(self.host.lib.bl_test_watchdog_off())

    def test_configure_switches_v2_and_can_on_and_enters_normal_mode(self):
        regulator_before = 0x55 & ~UJA["UJA113X_V2C_MASK"]
        can_before = 0x88 & ~UJA["UJA113X_CMC_MASK"]
        self.host.lib.bl_test_set_slave_reg(UJA["UJA113X_REG_REGULATOR_CONTROL"], regulator_before)
        self.host.lib.bl_test_set_slave_reg(UJA["UJA113X_REG_CAN_CONTROL"], can_before)
        self.host.sbc_init(configure=True)

        diag = self.host.diag()
        self.assertEqual(diag["stage"], 2)
        expected_regulator = regulator_before | (UJA["UJA113X_V2C_ON_NORMAL"] << UJA["UJA113X_V2C_SHIFT"])
        expected_can = can_before | UJA["UJA113X_CMC_ACTIVE"]
        # written to the device (the slave model keeps what it was given)
        self.assertEqual(self.host.lib.bl_test_slave_reg(UJA["UJA113X_REG_REGULATOR_CONTROL"]), expected_regulator)
        self.assertEqual(self.host.lib.bl_test_slave_reg(UJA["UJA113X_REG_CAN_CONTROL"]), expected_can)
        self.assertEqual(self.host.lib.bl_test_slave_reg(UJA["UJA113X_REG_MODE_CONTROL"]), UJA["UJA113X_MC_NORMAL"])
        # and read back afterwards
        self.assertEqual(diag["regulatorAfter"], expected_regulator)
        self.assertEqual(diag["canControlAfter"], expected_can)
        self.assertEqual(diag["modeAfter"], UJA["UJA113X_MC_NORMAL"])
        # the three writes are frames without the read-only bit
        writes = [w for w in self.host.tx_words() if (w & UJA["UJA113X_SPI_RO_BIT"]) == 0]
        self.assertEqual(writes, [frame(UJA["UJA113X_REG_REGULATOR_CONTROL"], expected_regulator, read=False),
                                  frame(UJA["UJA113X_REG_CAN_CONTROL"], expected_can, read=False),
                                  frame(UJA["UJA113X_REG_MODE_CONTROL"], UJA["UJA113X_MC_NORMAL"], read=False)])
        self.assertEqual(self.host.lib.bl_test_frame_errors(), 0)

    def test_normal_mode_delay_is_waited_out(self):
        self.host.sbc_init(configure=True)
        self.assertGreaterEqual(self.host.lib.bl_test_millis(), 20, "the 20 ms after Normal mode are missing")

    def test_without_software_development_mode_nothing_is_written(self):
        """The SBC watchdog would start with Normal mode: the hook without service (the updater build; the
        bootloader serves it since BL-41, test_bl_sbc_watchdog_host.py) must stop at stage 0xD0."""
        self.software_development_mode(False)
        self.host.sbc_init(configure=True)
        self.assertEqual(self.host.diag()["stage"], 0xD0)
        self.assertEqual([w for w in self.host.tx_words() if (w & UJA["UJA113X_SPI_RO_BIT"]) == 0], [])
        self.assertFalse(self.host.lib.bl_test_watchdog_off())

    def test_spi_error_while_reading(self):
        self.host.lib.bl_test_set_stuck_after(0)
        self.host.sbc_init(configure=True)
        self.assertEqual(self.host.diag()["stage"], 0xE1)
        self.assertFalse(self.host.lib.bl_test_watchdog_off())

    def test_spi_error_while_writing(self):
        self.host.lib.bl_test_set_stuck_after(9)  # the nine reads work, the first write does not
        self.host.sbc_init(configure=True)
        self.assertEqual(self.host.diag()["stage"], 0xE2)

    def test_spi_error_while_reading_back(self):
        self.host.lib.bl_test_set_stuck_after(12)  # nine reads and three writes work, the read back does not
        self.host.sbc_init(configure=True)
        self.assertEqual(self.host.diag()["stage"], 0xE3)


class SbcStartupTest(SbcHookBase):
    """BL-26: after power-on the SBC answers 0x00 until it is ready (board 2026-09-21, VER-07, bl_sbc_diag
    D0 00 .. 00). The hook must read again until the answer is plausible, and must not write anything to an SBC that
    never answers plausibly."""

    READ_FRAMES = 9          # registers of one read pass (read_all)
    READY_TIMEOUT_MS = 100   # start-up window of the hook (sbc_uja113x.c READY_TIMEOUT_MS)

    # identification codes of the series (sbc/uja113xRegisters.h, register 0x7E)
    ID_CODES = {0x00, 0x01, 0x10, 0x11, 0x40, 0x41, 0x50, 0x51, 0x60, 0x61, 0x70, 0x71}

    def writes(self) -> list[int]:
        return [w for w in self.host.tx_words() if (w & UJA["UJA113X_SPI_RO_BIT"]) == 0]

    def test_zero_answers_after_power_on_are_read_again(self):
        """SPI model: the first N transfers answer 0x00, then the SBC is ready - Normal mode and CAN on."""
        for n in (1, 2, self.READ_FRAMES - 1, self.READ_FRAMES, self.READ_FRAMES + 3, 2 * self.READ_FRAMES):
            with self.subTest(not_ready_frames=n):
                self.setUp()
                self.host.lib.bl_test_set_not_ready(n, 0x00)
                self.host.sbc_init(configure=True)
                diag = self.host.diag()
                self.assertEqual(diag["stage"], 2)
                # The SBC gets ready inside a pass. Is the mode (second frame) already read from a ready SBC, the
                # pass is plausible even if the identification was still 0x00 - itself a code of the series. Every
                # value a decision or a write depends on comes later in the pass and is therefore always valid.
                self.assertEqual(diag["id"], 0x00 if n % self.READ_FRAMES == 1 else self.SBC_ID)
                self.assertEqual(diag["watchdogStatus"], UJA["UJA113X_SDMS_MASK"])
                self.assertEqual(self.host.lib.bl_test_slave_reg(UJA["UJA113X_REG_MODE_CONTROL"]),
                                 UJA["UJA113X_MC_NORMAL"], "the SBC did not take Normal mode")
                self.assertEqual(diag["modeAfter"], UJA["UJA113X_MC_NORMAL"])
                self.assertEqual(len(self.writes()), 3)
                passes = n // self.READ_FRAMES + (2 if n % self.READ_FRAMES >= 2 else 1)
                reads = self.host.lib.bl_test_tx_count() - 3 - 4  # without the writes and the read back
                self.assertEqual(reads, passes * self.READ_FRAMES)
                self.assertGreater(diag["readyMs"], 0)
                self.assertLess(diag["readyMs"], self.READY_TIMEOUT_MS)

    def test_board_finding_2026_09_21_is_repaired(self):
        """Registers of the eval board after the J-Link reset (42_ver07_summary.txt), preceded by one pass of 0x00
        as after power-on: the hook now reaches stage 2 instead of 0xD0. Only the finding is the eval board here."""
        values = {"IDENTIFICATION_0": 0x51, "MODE_CONTROL": 0x04, "MAIN_STATUS": 0x20, "WATCHDOG_STATUS": 0x04,
                  "REGULATOR_CONTROL": 0x00, "CAN_CONTROL": 0x04, "TRANSCEIVER_STATUS": 0x48,
                  "STARTUP_CONTROL": 0x00, "SBC_CONFIG_CONTROL": 0x06}
        for name, value in values.items():
            self.host.lib.bl_test_set_slave_reg(UJA[f"UJA113X_REG_{name}"], value)
        self.host.lib.bl_test_set_not_ready(self.READ_FRAMES, 0x00)
        self.host.sbc_init(configure=True)
        diag = self.host.diag()
        self.assertEqual(diag["stage"], 2)
        self.assertEqual([diag["id"], diag["mode"], diag["watchdogStatus"]], [0x51, 0x04, 0x04])
        self.assertEqual(diag["canControlAfter"] & UJA["UJA113X_CMC_MASK"], UJA["UJA113X_CMC_ACTIVE"])

    def test_permanent_zero_or_ones_writes_nothing(self):
        """Dead or missing SBC (data line low or high): stage 0xE4 after the window, no command, updater refuses."""
        for answer in (0x00, 0xFF):
            for configure in (True, False):
                with self.subTest(answer=answer, configure=configure):
                    self.setUp()
                    self.host.lib.bl_test_set_not_ready(0xFFFFFFFF, answer)
                    self.host.sbc_init(configure=configure)
                    diag = self.host.diag()
                    self.assertEqual(diag["stage"], 0xE4)
                    self.assertEqual(self.writes(), [])
                    self.assertFalse(self.host.lib.bl_test_watchdog_off())
                    self.assertGreaterEqual(diag["readyMs"], self.READY_TIMEOUT_MS)
                    # the window ends after the pass that crossed it, not later
                    self.assertLess(self.host.lib.bl_test_millis(), 2 * self.READY_TIMEOUT_MS)
                    self.assertEqual(self.host.lib.bl_test_tx_count() % self.READ_FRAMES, 0)

    def test_a_ready_sbc_is_read_once(self):
        """No delay for a ready SBC (software reset, application already ran): one pass, as before BL-26."""
        self.host.sbc_init(configure=False)
        self.assertEqual(self.host.diag()["stage"], 1)
        self.assertEqual(self.host.lib.bl_test_tx_count(), self.READ_FRAMES)

    def test_every_identification_code_of_the_series_and_nothing_else(self):
        for code in range(256):
            with self.subTest(identification=hex(code)):
                self.setUp()
                self.host.lib.bl_test_set_slave_reg(UJA["UJA113X_REG_IDENTIFICATION_0"], code)
                self.host.sbc_init(configure=False)
                self.assertEqual(self.host.diag()["stage"], 1 if code in self.ID_CODES else 0xE4)

    def test_mode_must_be_one_the_sbc_can_have_with_the_mcu_supplied(self):
        """0x00 is an identification code, so the mode tells a real 0x00 from an SBC that is not ready."""
        for mode in range(8):
            with self.subTest(mode=mode):
                self.setUp()
                self.host.lib.bl_test_set_slave_reg(UJA["UJA113X_REG_IDENTIFICATION_0"], 0x00)
                self.host.lib.bl_test_set_slave_reg(UJA["UJA113X_REG_MODE_CONTROL"], 0xF8 | mode)
                self.host.sbc_init(configure=False)
                ok = mode in (UJA["UJA113X_MC_STANDBY"], UJA["UJA113X_MC_NORMAL"])
                self.assertEqual(self.host.diag()["stage"], 1 if ok else 0xE4)

    def test_hook_writes_no_mtpnv_register(self):
        """Board rule: the MTPNV (0x71..0x75) is never written - no frame without the read-only bit there."""
        for answer_frames in (0, 5, 0xFFFFFFFF):
            with self.subTest(not_ready_frames=answer_frames):
                self.setUp()
                self.host.lib.bl_test_set_not_ready(answer_frames, 0x00)
                self.host.sbc_init(configure=True)
                for word in self.writes():
                    self.assertNotIn((word >> UJA["UJA113X_SPI_ADDRESS_SHIFT"]) & 0x7F, range(0x71, 0x76))

    def test_source_names_the_start_up_time_and_its_source(self):
        text = HOOK_C.read_text(encoding="utf-8")
        self.assertRegex(text, r"#define READY_TIMEOUT_MS %dUL" % self.READY_TIMEOUT_MS)
        self.assertIn("UJA113x data sheet/app hints", text)


class SbcStartupMutationTest(unittest.TestCase):
    """Meaningfulness of SbcStartupTest (BL-26): every deliberately broken hook must fail at least one scenario."""

    MUTATIONS = {
        "no plausibility check": ("ready   = plausible(d);", "ready   = plausible(d) || true;"),
        "no second read": ("while (!ready && (elapsed < READY_TIMEOUT_MS))",
                           "while (!ready && (elapsed == 0U) && (d->readyMs == 0U))"),
        "identification not checked": ("if (((id & ~ID_CODE_BITS) != 0U) || ", "if ("),
        "mode not checked": ("return ((MODES_WITH_MCU >> mc) & 1U) != 0U;", "return mc < 8U;"),
        "window of 10 ms": ("#define READY_TIMEOUT_MS 100UL", "#define READY_TIMEOUT_MS 10UL"),
        "SBC that never answered is configured": ("    if (!ready)\n", "    if (false)\n"),
    }

    @staticmethod
    def scenarios_pass(host: BlSpiHost) -> bool:
        def run(not_ready=0, answer=0x00, sbc_id=0x70, mode=None, configure=True) -> dict[str, int]:
            host.reset()
            host.lib.bl_test_set_slave_reg(UJA["UJA113X_REG_WATCHDOG_STATUS"], UJA["UJA113X_SDMS_MASK"])
            host.lib.bl_test_set_slave_reg(UJA["UJA113X_REG_IDENTIFICATION_0"], sbc_id)
            host.lib.bl_test_set_slave_reg(UJA["UJA113X_REG_MODE_CONTROL"],
                                           UJA["UJA113X_MC_STANDBY"] if mode is None else mode)
            host.lib.bl_test_set_not_ready(not_ready, answer)
            host.sbc_init(configure=configure)
            return host.diag()

        writes = lambda: [w for w in host.tx_words() if (w & UJA["UJA113X_SPI_RO_BIT"]) == 0]  # noqa: E731
        checks = [
            run(not_ready=9)["stage"] == 2,
            run(not_ready=27)["stage"] == 2,  # fourth pass, 84 ms after the first (28 ms per pass on the model clock)
            run(not_ready=0xFFFFFFFF)["stage"] == 0xE4 and writes() == [],
            run(not_ready=0xFFFFFFFF, answer=0xFF)["stage"] == 0xE4 and writes() == [],
            run(sbc_id=0x8E)["stage"] == 0xE4,
            run(sbc_id=0x00, mode=0)["stage"] == 0xE4,
            run(sbc_id=0x00)["stage"] == 2,
        ]
        return all(checks)

    def build_variant(self, tmp: Path, name: str, old: str = "", new: str = "") -> BlSpiHost:
        text = HOOK_C.read_text(encoding="utf-8")
        if old:
            self.assertEqual(text.count(old), 1, f"mutation {name!r} does not match the hook any more")
            text = text.replace(old, new)
        hook = tmp / name.replace(" ", "_") / "sbc_uja113x.c"
        hook.parent.mkdir(parents=True)
        hook.write_text(text, encoding="utf-8")
        dll = hook.parent / "hook.dll"
        result = build(dll, sources=(BOOTLOADER / "platforms" / "s32k1xx" / "bl_spi_s32k1xx.c", hook,
                                     HERE / "bl_spi_host.c"))
        self.assertEqual(result.returncode, 0, f"{name}: {result.stderr}")
        return BlSpiHost(dll)

    def test_unmodified_hook_passes_and_every_mutation_fails(self):
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


class NeutralityTest(unittest.TestCase):
    """The hook must not need family code any more (BL-05), and the HAL must not name the device."""

    def test_hook_compiles_without_any_family_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = build(Path(tmp) / "hook.o", shared=False,
                           includes=(BOOTLOADER / "include", BOOTLOADER / "src" / "core",
                                     BOOTLOADER / "devices" / "sbcUja113x", SBC_INCLUDE),
                           sources=(HOOK_C,))
            self.assertEqual(result.returncode, 0,
                             f"the hook still needs a family header (mcu/mcu.h, bl_board.h):\n{result.stderr}")

    def test_hook_source_names_no_family_register(self):
        code = without_comments(HOOK_C)
        for pattern in ("LPSPI", "IP_PCC", "IP_PORT", "bl_board.h", "mcu/mcu.h", "BL_SBC_SPI"):
            self.assertNotIn(pattern, code, f"{pattern} is family code and belongs into bootloader/platforms")

    def test_hook_compile_fails_without_the_spi_interface(self):
        """Mutation check: without bootloader/include the hook cannot compile - the include path is load-bearing."""
        with tempfile.TemporaryDirectory() as tmp:
            result = build(Path(tmp) / "hook.o", shared=False,
                           includes=(BOOTLOADER / "src" / "core", BOOTLOADER / "devices" / "sbcUja113x",
                                     SBC_INCLUDE),
                           sources=(HOOK_C,))
            self.assertNotEqual(result.returncode, 0)


class SoscRangeTest(unittest.TestCase):
    """SCG_SOSCCFG from the crystal of the board instead of a fixed high range (ARCH-09 finding B7)."""

    RANGES = {4_000_000: 2, 7_999_999: 2, 8_000_000: 3, 16_000_000: 3, 40_000_000: 3, 32_768: 1, 1_000_000: 2}

    def build_with_crystal(self, hz: int, tmp: Path) -> subprocess.CompletedProcess:
        return build(tmp / f"sosc_{hz}.dll", defines=(f"BL_OSC_HZ={hz}UL",))

    def test_range_follows_the_crystal(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            for hz, expected in self.RANGES.items():
                with self.subTest(crystal=hz):
                    dll = Path(tmp) / f"sosc_{hz}.dll"
                    result = build(dll, defines=(f"BL_OSC_HZ={hz}UL",))
                    self.assertEqual(result.returncode, 0, result.stderr)
                    host = BlSpiHost(dll)
                    try:
                        self.assertEqual(host.lib.bl_test_osc_hz(), hz)
                        self.assertEqual(host.lib.bl_test_sosc_range(), expected)
                        # SOSCCFG: RANGE in bits 5:4, EREFS (crystal) set, HGO (high gain) clear
                        self.assertEqual(host.lib.bl_test_sosccfg(), (expected << 4) | 0x4)
                    finally:
                        host.close()

    def test_crystal_outside_every_range_does_not_compile(self):
        """Mutation check: a value the ranges do not cover must stop the build, not pick a wrong range."""
        with tempfile.TemporaryDirectory() as tmp:
            for hz in (0, 100, 48_000_000, 50_000):
                with self.subTest(crystal=hz):
                    result = build(Path(tmp) / f"bad_{hz}.dll", defines=(f"BL_OSC_HZ={hz}UL",))
                    self.assertNotEqual(result.returncode, 0, f"{hz} Hz was accepted")
                    self.assertIn("outside the SOSC ranges", result.stderr)

    def test_hal_uses_the_derived_range(self):
        """Guard against a relapse: no fixed RANGE(3) in the family HAL any more (ARCH-09 B7)."""
        text = HAL_C.read_text(encoding="utf-8")
        self.assertIn("SCG_SOSCCFG_RANGE(BL_SOSC_RANGE)", text)
        self.assertNotIn("SCG_SOSCCFG_RANGE(3", text)
        self.assertIn('#include "bl_sosc_s32k1xx.h"', text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
