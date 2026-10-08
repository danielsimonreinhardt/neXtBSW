"""Tests of tools/bswgen/blgen.py (ARCH-08): memory layout and bootloader board part from derivative + project.

Profiles: the eval board demo (values of the bootloader flashed on the board, compared with the hand-written files
they replace: bl_config.h, bl_board.h, both linker scripts, application.dld.in), testproject_s32k142_bl (S32K142,
16 MHz crystal, no SBC, bootloader on FlexCAN1 with RX and TX on different ports, other partitioning) and
testproject_bootloader (family without bootloader HAL, code flash at 0x08000000). With arm-none-eabi-gcc (PATH or
NX_ARM_GCC_BIN) the generated headers are compiled against the NXP header and bootloader and updater are linked for
the S32K142 profile.

Run: .venv/Scripts/python -m unittest tools/bswgen/test_blgen.py
"""

import copy
import dataclasses
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "tools" / "canflash"))

import blgen  # noqa: E402
import bswgen  # noqa: E402

PROFILES = REPO / "tools" / "nxconfig" / "test" / "profiles"
# PLAT-62: the own full test profile (memory layout and diagnostic values of the eval board demo, board testboard_full)
# instead of the eval board project, which stays private (PLAT-46 F10) and is missing in the exported open tree. The
# statement "equal to the flashed bootloader" is about the eval board itself: PRIVATE_EVAL_PROJECT, skipped there.
EVAL_PROJECT = PROFILES / "projects" / "testproject_full" / "project.yaml"
PRIVATE_EVAL_PROJECT = REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml"
S32K142_PROJECT = PROFILES / "projects" / "testproject_s32k142_bl" / "project.yaml"
OTHER_PROJECT = PROFILES / "projects" / "testproject_bootloader" / "project.yaml"
TEST_MCUDB = REPO / "tools" / "nxconfig" / "test" / "mcudb"
GCC_PATH = os.pathsep.join(p for p in (os.environ.get("NX_ARM_GCC_BIN"), os.environ.get("PATH")) if p)
ARM_GCC = shutil.which("arm-none-eabi-gcc", path=GCC_PATH)

# bootloader/include/bl_config.h before ARCH-08 (memory part), the values of the flashed bootloader - except the
# application end: BL-03 takes the sector below the record (0x7E000) for the device key, so the application ends
# there. That is a change of the memory partitioning, made together with the bootloader update of BL-03.
EVAL_BL_CONFIG = {"BL_FLASH_SIZE": 0x80000, "BL_SECTOR_SIZE": 0x1000, "BL_PHRASE_SIZE": 8, "BL_APP_START": 0x8000,
                  "BL_APP_END": 0x7E000, "BL_APP_RECORD_ADDR": 0x7F000, "BL_RAM_START": 0x1FFF8000,
                  "BL_RAM_END": 0x20007000, "BL_BOOT_REQUEST_ADDR": 0x20006FF0}
EVAL_KEY_SECTOR = {"BL_KEY_ADDR": 0x7E000, "BL_KEY_AREA_SIZE": 0x1000}


def load(project: Path, mcudb: Path | None = None):
    return blgen.nxconfig.load(project, *([mcudb] if mcudb else []))


def defines(text: str) -> dict[str, int]:
    return {n: int(v, 0) for n, v in re.findall(r"#define\s+(\w+)\s+(0x[0-9A-Fa-f]+|\d+)U?L?\b", text)}


class MemoryFilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @unittest.skipUnless(PRIVATE_EVAL_PROJECT.is_file(),
                         "private eval board project not in this tree (exported open tree, PLAT-62)")
    def test_eval_board_values_equal_the_flashed_bootloader(self):
        self.check_layout(PRIVATE_EVAL_PROJECT, "evalboard_s32k144")

    def test_full_profile_has_the_layout_of_the_eval_board(self):
        """PLAT-62: the same memory values for the own full test profile (only the board name differs)."""
        self.check_layout(EVAL_PROJECT, "testboard_full")

    def check_layout(self, project, board):
        bswgen.generate(project, self.tmp)                  # bswgen writes the memory files for every project
        header = (self.tmp / blgen.MEMORY_HEADER).read_text(encoding="utf-8")
        values = defines(header)
        self.assertEqual({k: values[k] for k in EVAL_BL_CONFIG}, EVAL_BL_CONFIG)
        self.assertEqual(values["BL_FLASH_START"], 0)
        import canflash
        # BL-02/COM-11: the board/derivative identity is generated too (no fallback in bl_config.h any more)
        identity = blgen.nxmemory.target_id(board, "S32K144")
        self.assertEqual(values["BL_TARGET_ID"], identity)
        self.assertEqual(canflash.target_id(board, "S32K144"), identity)
        self.assertEqual({k: v for k, v in canflash.load_config(self.tmp / blgen.MEMORY_HEADER).items()},
                         dict(EVAL_BL_CONFIG, BL_FLASH_START=0, BL_TARGET_ID=identity, **EVAL_KEY_SECTOR))
        self.assertEqual(canflash.layout_from_project(project),
                         dict(EVAL_BL_CONFIG, BL_FLASH_START=0, **EVAL_KEY_SECTOR))
        # bootloader/src/target/linker.ld and updater linker.ld before ARCH-08
        ld = dict(re.findall(r"^(\w+)\s*=\s*(0x[0-9A-F]+);", (self.tmp / blgen.MEMORY_LINKER).read_text(), re.M))
        self.assertEqual({k: int(v, 16) for k, v in ld.items()},
                         {"BL_FLASH_ORIGIN": 0, "BL_AREA_SIZE": 0x8000, "BL_APP_ORIGIN": 0x8000, "BL_APP_LIMIT": 0x7E000,
                          "BL_RAM_ORIGIN": 0x1FFF8000, "BL_RAM_LENGTH": 0x8000, "BL_STACK_SIZE": 0x1000})
        # application.dld.in before ARCH-08: FLASH 0x8000..0x7EFFF, SRAM_L 0x1FFF8000..0x1FFFFFFF,
        # SRAM_U 0x20000000..0x20006FFF, NoInit 0x400, boot request 0x20006FF0
        app = defines((self.tmp / blgen.APP_LINKER_HEADER).read_text(encoding="utf-8"))
        self.assertEqual(app, {"NX_MEMORY_APP_FLASH_START": 0x8000, "NX_MEMORY_APP_FLASH_END": 0x7E000,
                               "NX_MEMORY_APP_RAM_COUNT": 2, "NX_MEMORY_APP_RAM0_START": 0x1FFF8000,
                               "NX_MEMORY_APP_RAM0_SIZE": 0x8000, "NX_MEMORY_APP_RAM1_START": 0x20000000,
                               "NX_MEMORY_APP_RAM1_SIZE": 0x7000, "NX_MEMORY_APP_NOINIT_SIZE": 0x400,
                               "NX_MEMORY_BOOT_REQUEST_ADDR": 0x20006FF0})

    def test_other_family_gets_the_memory_layout_but_no_board_part(self):
        blgen.generate(OTHER_PROJECT, self.tmp, board=False, mcudb=TEST_MCUDB)
        values = defines((self.tmp / blgen.MEMORY_HEADER).read_text(encoding="utf-8"))
        self.assertEqual((values["BL_FLASH_START"], values["BL_APP_START"], values["BL_APP_RECORD_ADDR"]),
                         (0x08000000, 0x08004000, 0x0803E000))
        self.assertIn("#define NX_MEMORY_APP_RAM_COUNT 1", (self.tmp / blgen.APP_LINKER_HEADER).read_text())
        with self.assertRaises(blgen.ConfigError) as ctx:
            blgen.generate(OTHER_PROJECT, self.tmp, mcudb=TEST_MCUDB)
        self.assertIn("no bootloader board generator for family testfam (bootloader/platforms/testfam, PLAT-16)",
                      str(ctx.exception))

    def test_family_and_hardware_identity_in_plain_text(self):
        """BL-10: bl_memory.h names the MCU family (canflash image rule, BL-08) and "<board>/<derivative>" (DID 0xF191);
        BL_TARGET_ID is the CRC-32 of exactly that string. Three profiles, two families."""
        import canflash
        cases = ((EVAL_PROJECT, None, "s32k1xx", "testboard_full/S32K144"),
                 (S32K142_PROJECT, None, "s32k1xx", "testboard_s32k142_bl/S32K142"),
                 (OTHER_PROJECT, TEST_MCUDB, "testfam", None))
        for project, mcudb, family, hardware in cases:
            with self.subTest(project.parent.name):
                gen = self.tmp / project.parent.name
                config = blgen.generate(project, gen, board=False, **({"mcudb": mcudb} if mcudb else {}))
                header = gen / blgen.MEMORY_HEADER
                strings = canflash.load_strings(header)
                expected = hardware or f"{config.board.board.name}/{config.derivative.derivative.name}"
                self.assertEqual(strings["BL_FAMILY"], family)
                self.assertEqual(strings["BL_FAMILY"], config.derivative.derivative.family)
                self.assertEqual(strings["BL_HARDWARE_ID"], expected)
                self.assertEqual(defines(header.read_text(encoding="utf-8"))["BL_TARGET_ID"],
                                 canflash.hardware_identity(expected))
                # canflash takes both along from a build (image rule, hardware number check)
                values = canflash.load_config(header)
                self.assertNotIn("BL_FAMILY", values, "string macros stay out of the numeric values")

    def test_project_without_memory_partitioning(self):
        project = yaml.safe_load(S32K142_PROJECT.read_text(encoding="utf-8"))
        del project["memory"], project["bootloader"]
        project["project"]["board"] = str((PROFILES / "boards" / "testboard_s32k142_bl").as_posix())
        path = self.tmp / "project.yaml"
        path.write_text(yaml.safe_dump(project), encoding="utf-8")
        bswgen.generate(path, self.tmp / "gen")
        self.assertFalse((self.tmp / "gen" / "bootloader").exists())
        self.assertFalse((self.tmp / "gen" / blgen.APP_LINKER_HEADER).exists())
        with self.assertRaises(blgen.ConfigError) as ctx:
            blgen.generate(path, self.tmp / "gen")
        self.assertIn("has no memory partitioning (memory)", str(ctx.exception))


class DiagFilesTest(unittest.TestCase):
    """COM-11: bootloader/bl_diag.h from the project's diagnostics section."""

    # bootloader/include/bl_config.h before COM-11 and ecu/uds/AppDiagConfig.h (hand written values of the
    # bootloader flashed on the eval board)
    EVAL_DIAG = {"BL_CAN_ID_REQUEST_PHYS": 0x7E0, "BL_CAN_ID_REQUEST_FUNC": 0x7DF, "BL_CAN_ID_RESPONSE": 0x7E8,
                 "BL_ISOTP_PADDING": 0xCC, "BL_ISOTP_BLOCK_SIZE": 0, "BL_ISOTP_STMIN_MS": 0,
                 "BL_ISOTP_N_CR_MS": 1000, "BL_ISOTP_N_BS_MS": 1000, "NX_DIAG_TESTER_ADDRESS": 0xF1,
                 "NX_DIAG_ECU_ADDRESS": 0x2A, "NX_DIAG_BL_SCAN_START": 0x410, "NX_DIAG_BL_SCAN_END": 0x8000}

    def test_eval_board_values_equal_the_hand_written_bl_config_h(self):
        header = blgen.diag_files(load(EVAL_PROJECT))[blgen.DIAG_HEADER]
        self.assertEqual(defines(header), self.EVAL_DIAG)
        self.assertIn('#define BL_VERSION_PREFIX "nextbsw-bl"', header)
        self.assertIn("on CAN bus CAN0", header)                     # rte.yaml uds.bus (ARCH-07)
        self.assertIn("the first 0x410 bytes of the code flash are skipped", header)
        import canflash
        self.assertEqual({k: v for k, v in canflash.load_config(*self.written(EVAL_PROJECT)).items()
                          if k in self.EVAL_DIAG}, self.EVAL_DIAG)
        self.assertEqual(canflash.load_strings(*self.written(EVAL_PROJECT))["BL_VERSION_PREFIX"], "nextbsw-bl")

    def test_updater_takes_the_identification_from_the_project(self):
        """PLAT-08 (from COM-11): UPD_VERSION_PREFIX is no hand value any more - it is the generated
        BL_VERSION_PREFIX, the former name comes from bl_config.h. UPD_STATUS_CAN_ID stays a hand value (the
        generator side, diag.py/blgen.py, was outside PLAT-08; diff proposal in the changelog)."""
        upd = (REPO / "bootloader/updater/include/upd_config.h").read_text(encoding="utf-8")
        self.assertRegex(upd, r'#define\s+UPD_VERSION_PREFIX\s+BL_VERSION_PREFIX " "')
        self.assertRegex(upd, r'#define\s+UPD_VERSION_PREFIX_FORMER\s+BL_VERSION_PREFIX_FORMER " "')
        self.assertNotRegex(upd, r'#define\s+UPD_VERSION_PREFIX\w*\s+"')
        bl = (REPO / "bootloader/include/bl_config.h").read_text(encoding="utf-8")
        self.assertIn('#define BL_VERSION_PREFIX_FORMER "S32K14x-BL"', bl)
        self.assertNotRegex(blgen.diag_files(load(EVAL_PROJECT))[blgen.DIAG_HEADER], r"#define\s+BL_VERSION_PREFIX_FORMER")

    def written(self, project: Path) -> list[Path]:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        blgen.write(tmp, blgen.memory_files(load(project)))
        return [tmp / blgen.DIAG_HEADER]

    def test_other_ids_and_project_search_area(self):
        """testproject_s32k142_bl: other ids, other ISO-TP values, scan_start from the project."""
        header = blgen.diag_files(load(S32K142_PROJECT))[blgen.DIAG_HEADER]
        self.assertEqual(defines(header), {
            "BL_CAN_ID_REQUEST_PHYS": 0x730, "BL_CAN_ID_REQUEST_FUNC": 0x7DE, "BL_CAN_ID_RESPONSE": 0x738,
            "BL_ISOTP_PADDING": 0x55, "BL_ISOTP_BLOCK_SIZE": 4, "BL_ISOTP_STMIN_MS": 2,
            "BL_ISOTP_N_CR_MS": 250, "BL_ISOTP_N_BS_MS": 250, "NX_DIAG_TESTER_ADDRESS": 0xF2,
            "NX_DIAG_ECU_ADDRESS": 0x11, "NX_DIAG_BL_SCAN_START": 0x500, "NX_DIAG_BL_SCAN_END": 0x6000})
        self.assertIn("project diagnostics.bootloader.scan_start", header)

    def test_other_family_without_reserved_flash_and_other_prefix(self):
        """testproject_bootloader (family testfam): search area from the first byte, own identification."""
        config = load(OTHER_PROJECT, TEST_MCUDB)
        header = blgen.diag_files(config)[blgen.DIAG_HEADER]
        values = defines(header)
        self.assertEqual((values["NX_DIAG_BL_SCAN_START"], values["NX_DIAG_BL_SCAN_END"]), (0x08000000, 0x08004000))
        self.assertEqual((values["BL_CAN_ID_REQUEST_PHYS"], values["BL_CAN_ID_RESPONSE"]), (0x611, 0x622))
        self.assertIn('#define BL_VERSION_PREFIX "nextbsw-bl"', header)
        self.assertNotIn("skipped", header)

    def test_project_without_memory_partitioning_gets_no_diagnostics_file(self):
        project = yaml.safe_load(S32K142_PROJECT.read_text(encoding="utf-8"))
        del project["memory"], project["bootloader"]
        project["project"]["board"] = str((PROFILES / "boards" / "testboard_s32k142_bl").as_posix())
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = tmp / "project.yaml"
        path.write_text(yaml.safe_dump(project), encoding="utf-8")
        self.assertEqual(blgen.diag_files(load(path)), {})


class LpspiDividerTest(unittest.TestCase):
    def test_rule(self):
        # (SOSCDIV2 Hz) -> (PRESCALE, SCKDIV, SCK Hz); 40 MHz is the setting verified on the eval board
        cases = {40_000_000: (3, 8, 500_000), 48_000_000: (3, 10, 500_000), 16_000_000: (1, 14, 500_000),
                 8_000_000: (0, 14, 500_000), 12_000_000: (1, 10, 500_000), 4_000_000: (0, 6, 500_000),
                 20_000_000: (2, 8, 500_000)}
        for clock, expected in cases.items():
            with self.subTest(clock=clock):
                d = blgen.lpspi_divider(clock, 500_000)
                self.assertEqual((d.prescale, d.sckdiv, round(d.sck_hz)), expected)
                self.assertLessEqual(d.sck_hz, 500_000)
        d = blgen.lpspi_divider(10_000_000, 500_000)              # 10 MHz / 2 / 10 = 500 kHz, / 1 / 20 too
        self.assertEqual((d.prescale, d.sckdiv), (1, 8))
        self.assertLessEqual(blgen.lpspi_divider(1_000_000, 500_000).sck_hz, 500_000)


class BoardFilesTest(unittest.TestCase):
    @unittest.skipUnless(PRIVATE_EVAL_PROJECT.is_file(),
                         "private eval board project not in this tree (exported open tree, PLAT-62)")
    def test_eval_board_equals_the_hand_written_bl_board_h(self):
        files = blgen.board_files(load(PRIVATE_EVAL_PROJECT))
        h = files[blgen.BOARD_HEADER]
        for line in [
            "#define BOARD_CAN           IP_FLEXCAN0",
            "#define BOARD_CAN_PCC_INDEX PCC_FlexCAN0_INDEX",
            "#define BOARD_CAN_CTRL1     0x04620006UL",           # PRESDIV 4, RJW 1, PSEG1 4, PSEG2 2, PROPSEG 6
            "#define BL_CORE_CLOCK_HZ 48000000UL",
            # can_init() and spi_init() before ARCH-08, same order
            "    IP_PCC->PCCn[PCC_PORTC_INDEX] |= PCC_PCCn_CGC_MASK;\n"
            "    IP_PORTC->PCR[2U] = PORT_PCR_MUX(3U); /* PTC2 FlexCAN0_RX */\n"
            "    IP_PORTC->PCR[3U] = PORT_PCR_MUX(3U); /* PTC3 FlexCAN0_TX */",
            "    IP_PCC->PCCn[PCC_PORTD_INDEX] |= PCC_PCCn_CGC_MASK;\n"
            "    IP_PCC->PCCn[PCC_PORTE_INDEX] |= PCC_PCCn_CGC_MASK;\n"
            "    IP_PORTD->PCR[0U] = PORT_PCR_MUX(3U); /* PTD0 LPSPI1_SCK */\n"
            "    IP_PORTD->PCR[1U] = PORT_PCR_MUX(3U); /* PTD1 LPSPI1_SIN */\n"
            "    IP_PORTE->PCR[0U] = PORT_PCR_MUX(5U); /* PTE0 LPSPI1_SOUT */\n"
            "    IP_PORTE->PCR[1U] = PORT_PCR_MUX(5U); /* PTE1 LPSPI1_PCS0 */",
            "#define BL_SBC_SPI IP_LPSPI1",
            "#define BL_SBC_SPI_PRESCALE 3U",
            "#define BL_SBC_SPI_SCKDIV 8U",
            "#define BL_SBC_SPI_PCS 0U",
            '#include "sbc_uja113x.h"',
            "    sbc_init(true); /* SBC: switched on before CAN",
            "    return sbc_watchdog_off();",
        ]:
            self.assertIn(line, h)
        self.assertEqual([x for x in files[blgen.BOARD_CMAKE].splitlines() if x.startswith("set(")],
                         ['set(NX_BL_CAN_BUS "CAN0")', "set(NX_BL_DEVICES sbcUja113x)"])

    @unittest.skipUnless(PRIVATE_EVAL_PROJECT.is_file(),
                         "private eval board project not in this tree (exported open tree, PLAT-62)")
    def test_other_bus_of_the_eval_board(self):
        """BOOTLOADER_CAN_BUS=CAN1: Diag-CAN (FlexCAN2, PTC16/PTC17), the SBC is only read (updater watchdog check)."""
        files = blgen.board_files(load(PRIVATE_EVAL_PROJECT), can="CAN1")
        h = files[blgen.BOARD_HEADER]
        self.assertIn("#define BOARD_CAN           IP_FLEXCAN2", h)
        self.assertIn("IP_PORTC->PCR[16U] = PORT_PCR_MUX(3U); /* PTC16 FlexCAN2_RX */", h)
        self.assertIn("IP_PORTC->PCR[17U] = PORT_PCR_MUX(3U); /* PTC17 FlexCAN2_TX */", h)
        self.assertIn("#define BOARD_CAN_CTRL1     0x04620006UL", h)             # can.CAN1 has the same values
        self.assertIn("sbc_init(false);", h)
        self.assertIn("CAN bus CAN1 from the build option BOOTLOADER_CAN_BUS", h)
        self.assertIn('set(NX_BL_CAN_BUS "CAN1")', files[blgen.BOARD_CMAKE])
        for bus, fragment in (("BODY", "has no CAN bus BODY"),):
            with self.assertRaises(blgen.ConfigError) as ctx:
                blgen.board_files(load(PRIVATE_EVAL_PROJECT), can=bus)
            self.assertIn(fragment, str(ctx.exception))

    def test_s32k142_without_sbc(self):
        files = blgen.board_files(load(S32K142_PROJECT))
        h = files[blgen.BOARD_HEADER]
        # 16 MHz / 250 kbit/s = 16 tq at prescaler 4, 75 %: 1 + 7 + 4 + 4, SJW 4
        ctrl1 = ((4 - 1) << 24) | ((4 - 1) << 22) | ((4 - 1) << 19) | ((4 - 1) << 16) | (7 - 1)
        self.assertIn(f"#define BOARD_CAN_CTRL1     0x{ctrl1:08X}UL", h)
        self.assertIn("#define BOARD_CAN           IP_FLEXCAN1", h)
        self.assertIn("    IP_PCC->PCCn[PCC_PORTA_INDEX] |= PCC_PCCn_CGC_MASK;\n"
                      "    IP_PCC->PCCn[PCC_PORTC_INDEX] |= PCC_PCCn_CGC_MASK;\n"
                      "    IP_PORTA->PCR[12U] = PORT_PCR_MUX(3U); /* PTA12 FlexCAN1_RX */\n"
                      "    IP_PORTC->PCR[7U] = PORT_PCR_MUX(3U); /* PTC7 FlexCAN1_TX */", h)
        self.assertIn("#define BL_OSC_HZ 16000000UL", h)
        self.assertNotIn("sbc", h)
        self.assertIn("static inline void bl_board_init(void)\n{\n}", h)
        self.assertIn("    return true;", h)
        self.assertIn("set(NX_BL_DEVICES )", files[blgen.BOARD_CMAKE])

    def test_generator_errors(self):
        config = load(EVAL_PROJECT)
        config.bootloader = dataclasses.replace(config.bootloader, hooks=config.bootloader.hooks + (
            blgen.nxmemory.DeviceHook("DIAG_PHY", "canPhyPassive", False),))
        with self.assertRaises(blgen.ConfigError) as ctx:
            blgen.board_files(config)
        self.assertIn("bootloader hook of module canPhyPassive (device DIAG_PHY) is not known", str(ctx.exception))
        config = load(S32K142_PROJECT)
        config.clock = dataclasses.replace(config.clock, oscillator_hz=None)
        with self.assertRaises(blgen.ConfigError) as ctx:
            blgen.board_files(config)
        self.assertIn("FlexCAN runs from SOSCDIV2", str(ctx.exception))
        # ARCH-22: the bootloader starts SOSC undivided; a divided oscillator output of the clock plan is an error
        config = load(S32K142_PROJECT)
        self.assertEqual({o: d for o, d in config.clock.output_dividers.items() if o.startswith("SOSC")},
                         {"SOSCDIV1": 1, "SOSCDIV2": 1})
        config.clock = dataclasses.replace(config.clock, output_dividers={**config.clock.output_dividers,
                                                                          "SOSCDIV2": 2})
        with self.assertRaises(blgen.ConfigError) as ctx:
            blgen.board_files(config)
        self.assertIn("divides SOSCDIV2 by 2", str(ctx.exception))
        self.assertIn("the bootloader runs SOSCDIV1/SOSCDIV2 undivided", str(ctx.exception))


@unittest.skipUnless(ARM_GCC, "arm-none-eabi-gcc not found (PATH, NX_ARM_GCC_BIN)")
class TargetCompileTest(unittest.TestCase):
    """Generated files compiled for the target: values against the NXP header, bootloader and updater linked."""

    OPENBSW = REPO / "external" / "openbsw"
    CFLAGS = ["-mcpu=cortex-m4", "-mfloat-abi=hard", "-mfpu=fpv4-sp-d16", "-mthumb", "-O2", "-std=gnu99",
              "-ffunction-sections", "-fdata-sections", "-ffreestanding", "-fno-builtin", "-fno-common",
              "-fshort-enums", "-Wall", "-Wextra", "-Wconversion", "-Wno-sign-conversion", "-Werror"]

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def generate(self, project: Path, name: str, can=None) -> Path:
        gen = self.tmp / name
        bswgen.generate(project, gen)                       # mcu/mcuDerivative.h and the memory layout
        blgen.write(gen, blgen.board_files(load(project), can))
        return gen

    def includes(self, gen: Path) -> list[str]:
        dirs = [REPO / "bootloader/include", REPO / "bootloader/src/core", REPO / "bootloader/platforms/s32k1xx",
                REPO / "bootloader/platforms/common",
                REPO / "bootloader/devices/sbcUja113x", REPO / "libs/bsw/sbcUja113x/include",
                REPO / "platforms/s32k1xx/bsp/bspMcu/include", self.OPENBSW / "platforms/s32k1xx/bsp/bspMcu/include",
                self.OPENBSW / "libs/3rdparty/cmsis", self.OPENBSW / "libs/3rdparty/cmsis/m-profile", gen,
                REPO / "bootloader/updater/include", REPO / "bootloader/updater/src/core"]
        return [f"-I{d}" for d in dirs]

    def gcc(self, *args):
        r = subprocess.run([ARM_GCC, *args], capture_output=True, text=True, cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr[-4000:])
        return r

    def family_objects(self, gen: Path, name: str) -> list[str]:
        """BL-34: the C++ unit of the family part (bootloader/platforms/s32k1xx/family.cmake), compiled like the CMake
        build does (g++, ISO C++17 without exceptions/RTTI, the family port's include directory for this unit only)
        and linked as C together with the C sources."""
        gxx = Path(ARM_GCC).with_name(Path(ARM_GCC).name.replace("gcc", "g++"))
        obj = self.tmp / f"{name}_bl_lvd_s32k1xx.o"
        flags = [f for f in self.CFLAGS if not f.startswith("-std=") and f not in ("-ffreestanding", "-fno-builtin")]
        r = subprocess.run([str(gxx), *flags, "-std=c++17", "-fno-exceptions", "-fno-rtti", "-fno-threadsafe-statics",
                            "-fno-use-cxa-atexit", "-Os", *self.includes(gen),
                            f"-I{REPO / 'platforms/s32k1xx/port/main/include'}", "-c",
                            str(REPO / "bootloader/platforms/s32k1xx/bl_lvd_s32k1xx.cpp"), "-o", str(obj)],
                           capture_output=True, text=True, cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr[-4000:])
        return [str(obj)]

    def check_values(self, gen: Path, asserts: str):
        source = self.tmp / f"{gen.name}_values.c"
        source.write_text('#include "bootloader/bl_board.h"\n#include "bl_config.h"\n' + asserts, encoding="utf-8")
        self.gcc(*self.CFLAGS, *self.includes(gen), "-fsyntax-only", str(source))

    @unittest.skipUnless(PRIVATE_EVAL_PROJECT.is_file(),
                         "private eval board project not in this tree (exported open tree, PLAT-62)")
    def test_eval_board_values_against_the_hand_written_expressions(self):
        gen = self.generate(PRIVATE_EVAL_PROJECT, "eval")
        self.check_values(gen, """
_Static_assert(BOARD_CAN_CTRL1 == (FLEXCAN_CTRL1_PRESDIV(4U) | FLEXCAN_CTRL1_RJW(1U) | FLEXCAN_CTRL1_PSEG1(4U)
               | FLEXCAN_CTRL1_PSEG2(2U) | FLEXCAN_CTRL1_PROPSEG(6U)), "CTRL1 of bl_board.h before ARCH-08");
_Static_assert(BOARD_CAN_PCC_INDEX == PCC_FlexCAN0_INDEX, "FlexCAN0");
_Static_assert(BL_SBC_SPI_PCC_INDEX == PCC_LPSPI1_INDEX && BL_SBC_SPI_PRESCALE == 3U && BL_SBC_SPI_SCKDIV == 8U, "SPI");
_Static_assert((BL_CORE_CLOCK_HZ / 1000UL) - 1UL == (48000000UL / 1000UL) - 1UL, "SysTick reload");
_Static_assert(BL_APP_START == 0x00008000UL && BL_APP_RECORD_ADDR == 0x0007F000UL, "layout");
_Static_assert(BL_BOOT_REQUEST_ADDR == 0x20006FF0UL && BL_RAM_END == 0x20007000UL, "RAM");
""")

    def test_s32k142_profile_links_bootloader_and_updater(self):
        gen = self.generate(S32K142_PROJECT, "s32k142")
        self.check_values(gen, """
_Static_assert(BOARD_CAN_CTRL1 == (FLEXCAN_CTRL1_PRESDIV(3U) | FLEXCAN_CTRL1_RJW(3U) | FLEXCAN_CTRL1_PSEG1(3U)
               | FLEXCAN_CTRL1_PSEG2(3U) | FLEXCAN_CTRL1_PROPSEG(6U)), "250 kbit/s from 16 MHz, 75 %");
_Static_assert(BOARD_CAN_PCC_INDEX == PCC_FlexCAN1_INDEX, "FlexCAN1");
_Static_assert(BL_APP_START == 0x6000UL && BL_APP_RECORD_ADDR == 0x3F800UL && BL_SECTOR_SIZE == 0x800UL, "flash");
""")
        link = [*self.CFLAGS, "-specs=nano.specs", "-specs=nosys.specs", "-static", "-Wl,--gc-sections",
                f"-L{gen / 'bootloader'}"]
        platform = REPO / "bootloader/platforms/s32k1xx"
        newlib = REPO / "platforms/common/newlib/nx_newlib_syscalls.c"   # own source like in the CMake build (PLAT-66)
        core = [REPO / "bootloader/src/core" / f
                for f in ("app_image.c", "bl_main.c", "crc32.c", "isotp.c", "seedkey.c", "sha256.c", "uds.c")]
        bl = self.tmp / "bootloader.elf"
        self.gcc(*link, *self.includes(gen), *map(str, core), str(platform / "hal_s32k1xx.c"), str(platform / "main.c"),
                 str(platform / "startup.S"), str(newlib), *self.family_objects(gen, "bl"),
                 "-T", str(platform / "linker.ld"), "-o", str(bl))
        symbols = self.symbols(bl)
        self.assertEqual(symbols["__isr_vector"], 0)
        self.assertEqual(symbols["__StackTop"], 0x20000000)            # end of SRAM_L of the S32K142
        self.assertNotIn("sbc_init", symbols)
        self.assertLessEqual(max(self.load_ranges(bl)), 0x6000)          # everything in the bootloader area

        # updater: embedded image checked and converted by canflash with the same generated layout
        hex_file = self.tmp / "bootloader.hex"
        subprocess.run([str(Path(ARM_GCC).with_name("arm-none-eabi-objcopy")), "-O", "ihex", str(bl), str(hex_file)],
                       check=True)
        blob = self.tmp / "upd_blob.c"
        r = subprocess.run([sys.executable, str(REPO / "tools/canflash/canflash.py"), "--layout",
                            str(gen / blgen.MEMORY_HEADER), "blimage", str(hex_file), "--c-out", str(blob)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        upd = REPO / "bootloader/updater"
        updater = self.tmp / "blupdater.elf"
        # cycle counter of the updater per core architecture (PLAT-28): the S32K142 is a Cortex-M4F (ARMv7-M, DWT),
        # selected like bootloader/updater/CMakeLists.txt does from NX_CORE_ARCHITECTURE
        counter = f"-I{upd / 'platforms/s32k1xx/cycle_counter/dwt'}"
        self.gcc(*link, *self.includes(gen), counter, str(upd / "src/core/bl_update.c"),
                 str(upd / "platforms/s32k1xx/upd_flash_s32k1xx.c"), str(upd / "platforms/s32k1xx/upd_main.c"),
                 str(upd / "platforms/s32k1xx/upd_micros.c"),  # time measurement, own file since PLAT-86
                 str(REPO / "bootloader/src/core/crc32.c"), str(platform / "hal_s32k1xx.c"), str(platform / "startup.S"),
                 str(blob), str(newlib), *self.family_objects(gen, "upd"), "-T", str(upd / "platforms/s32k1xx/linker.ld"), "-o", str(updater))
        symbols = self.symbols(updater)
        self.assertEqual(symbols["__isr_vector"], 0x6000)
        ranges = self.load_ranges(updater)
        self.assertGreaterEqual(min(ranges), 0x6000)
        self.assertLessEqual(max(ranges), 0x3F800)

    def symbols(self, elf: Path) -> dict[str, int]:
        nm = subprocess.run([str(Path(ARM_GCC).with_name("arm-none-eabi-nm")), str(elf)], capture_output=True,
                            text=True, check=True).stdout
        return {name: int(addr, 16) for addr, _, name in (line.split() for line in nm.splitlines() if len(line.split()) == 3)}

    def load_ranges(self, elf: Path) -> list[int]:
        """Start and end (load addresses) of every section with contents that is loaded into the flash."""
        out = subprocess.run([str(Path(ARM_GCC).with_name("arm-none-eabi-objdump")), "-h", str(elf)],
                             capture_output=True, text=True, check=True).stdout
        ranges = []
        pattern = r"^\s*\d+\s+(\S+)\s+([0-9a-f]+)\s+[0-9a-f]+\s+([0-9a-f]+)\s+[0-9a-f]+\s+\S+\n\s+(.*)$"
        for name, size, lma, flags in re.findall(pattern, out, re.M):
            if "LOAD" in flags and "CONTENTS" in flags and int(size, 16):
                ranges += [int(lma, 16), int(lma, 16) + int(size, 16)]
        self.assertTrue(ranges)
        return ranges


class SecurityFilesTest(unittest.TestCase):
    """BL-03: bootloader/bl_security.h (method, chip UID of the derivative, key name) and the key sector."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def variant(self, source: Path, change) -> Path:
        project = yaml.safe_load(source.read_text(encoding="utf-8"))
        project["project"]["board"] = str((source.parent / project["project"]["board"]).resolve().as_posix())
        rte = project["project"].get("rte")
        if rte:
            project["project"]["rte"] = str((source.parent / rte).resolve().as_posix())
        diag = project["project"].get("diag")
        if diag:
            project["project"]["diag"] = str((source.parent / diag).resolve().as_posix())
        change(project)
        path = self.tmp / f"p{len(list(self.tmp.iterdir()))}" / "project.yaml"
        path.parent.mkdir()
        path.write_text(yaml.safe_dump(project), encoding="utf-8")
        return path

    def test_eval_board_security_header(self):
        header = blgen.diag_files(load(EVAL_PROJECT))[blgen.SECURITY_HEADER]
        self.assertEqual(defines(header), {"BL_SECURITY_METHOD": 1, "BL_SECURITY_TEST_KEY_ALLOWED": 0,
                                           "BL_UID_ADDR": 0x40048054, "BL_UID_LEN": 16})
        self.assertIn('#define BL_SECURITY_KEY_NAME "testboard_full"', header)       # default: the board name
        self.assertIn('#define BL_SECURITY_METHOD_NAME "hmac-sha256"', header)
        self.assertNotRegex(header, r"[0-9a-fA-F]{32}")                              # no key material

    def test_other_family_and_derivative_take_their_own_uid(self):
        """The UID source is a derivative fact: 96 bit at 0x1FFF7A10 on STM32F446, 128 bit SIM on S32K142."""
        stm32 = load(REPO / "projects" / "nucleo_f446re_demo" / "project.yaml")
        if stm32.memory is not None:
            values = defines(blgen.diag_files(stm32)[blgen.SECURITY_HEADER])
            self.assertEqual((values["BL_UID_ADDR"], values["BL_UID_LEN"]), (0x1FFF7A10, 12))
        values = defines(blgen.diag_files(load(S32K142_PROJECT))[blgen.SECURITY_HEADER])
        self.assertEqual((values["BL_UID_ADDR"], values["BL_UID_LEN"]), (0x40048054, 16))
        values = defines(blgen.diag_files(load(OTHER_PROJECT, TEST_MCUDB))[blgen.SECURITY_HEADER])
        self.assertEqual((values["BL_UID_ADDR"], values["BL_UID_LEN"]), (0x1FFF0000, 12))

    def test_key_sector_below_the_record(self):
        for project, mcudb, key, record in ((EVAL_PROJECT, None, 0x7E000, 0x7F000),
                                            (S32K142_PROJECT, None, None, None),
                                            (OTHER_PROJECT, TEST_MCUDB, 0x0803C000, 0x0803E000)):
            with self.subTest(project.parent.name):
                layout = load(project, mcudb).memory
                values = blgen.memory_values(layout)
                sector = values["BL_APP_RECORD_ADDR"] - values["BL_KEY_ADDR"]
                self.assertEqual(values["BL_KEY_AREA_SIZE"], sector)
                self.assertEqual(values["BL_APP_END"], values["BL_KEY_ADDR"])
                self.assertGreater(sector, 0)
                if key is not None:
                    self.assertEqual((values["BL_KEY_ADDR"], values["BL_APP_RECORD_ADDR"]), (key, record))

    def test_named_key_and_method(self):
        path = self.variant(EVAL_PROJECT, lambda p: p["diagnostics"].setdefault("bootloader", {}).update(
            {"security": {"method": "hmac-sha256", "key": "fleet-2026"}}))
        header = blgen.diag_files(load(path))[blgen.SECURITY_HEADER]
        self.assertIn('#define BL_SECURITY_KEY_NAME "fleet-2026"', header)
        path = self.variant(EVAL_PROJECT, lambda p: p["diagnostics"].setdefault("bootloader", {}).update(
            {"security": {"method": "ed25519"}}))
        with self.assertRaises(blgen.nxconfig.ConfigErrors):
            load(path)                                   # not implemented yet: a schema error, not a silent default

    def test_insecure_test_key_needs_the_permission_of_the_project(self):
        """A target build with the repository's test key stops in the configuration check."""
        path = self.variant(EVAL_PROJECT, lambda p: p["diagnostics"].setdefault("bootloader", {}).update(
            {"security": {"key": "nextbsw-insecure-test"}}))
        with self.assertRaises(blgen.nxconfig.ConfigErrors) as ctx:
            load(path)
        self.assertIn("insecure test key", str(ctx.exception))
        self.assertIn("diagnostics.bootloader.security.key", str(ctx.exception).replace("(diagnostics", "diagnostics"))
        path = self.variant(EVAL_PROJECT, lambda p: p["diagnostics"].setdefault("bootloader", {}).update(
            {"security": {"key": "nextbsw-insecure-test", "allow_insecure_test_key": True}}))
        values = defines(blgen.diag_files(load(path))[blgen.SECURITY_HEADER])
        self.assertEqual(values["BL_SECURITY_TEST_KEY_ALLOWED"], 1)

    def test_derivative_without_uid_is_an_error(self):
        mcudb = self.tmp / "mcudb"
        shutil.copytree(REPO / "mcudb" / "s32k1xx", mcudb / "s32k1xx")
        derivative = mcudb / "s32k1xx" / "S32K144.yaml"
        text = derivative.read_text(encoding="utf-8")
        derivative.write_text(text[:text.index("\nuid:")] + "\n", encoding="utf-8")
        with self.assertRaises(blgen.nxconfig.ConfigErrors) as ctx:
            load(EVAL_PROJECT, mcudb)
        self.assertIn("has no chip UID", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
