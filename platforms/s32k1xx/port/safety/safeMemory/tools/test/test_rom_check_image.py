"""Tests of the post-link step of the ROM check (SM-14, SYS-38): tools/romCheckImage.py.

Board finding 2026-09-21 (session d3): __checksum_list_start (0x7DFC0) and __checksum_result were not in the image
(the build removed .checksum from the hex, no step computed the reference), the flash read 0xFF there. A changed
byte therefore stopped nothing. These tests show the build side of the fix:

- CRC: the fast CRC of the tool equals a register level model of the S32K1 CRC unit; the known vector has the same
  value as RomCheckHostTest.cpp (testKnownVector), where the real RomCheck.cpp runs against a C++ model of the unit.
- Image rules with synthetic hex content (no toolchain): valid list and matching result pass; gaps inside an area
  count as 0xFF; flash content outside every area, a missing list, 0 or too many areas, a list length that does not
  fit, unaligned areas and areas beyond the list stop the build; a changed byte in any area is a mismatch.
- The real linker template (port/main/linkerscript/application.dld.in) linked with arm-none-eabi-ld: inject writes the
  reference into the ELF and the hex; every changed byte of an area is a mismatch; the hex as the build made it before
  SYS-38 (objcopy --remove-section=.checksum) is refused like the board image; an ELF without list stops the build.
- SYS-91: the area with its own reference (calibration segment) is accepted outside the list and not part of the CRC,
  with its rules (aligned, not empty, inside the application flash, no overlap with an area or the list); the real
  template with a segment (both profiles) has five areas, and every byte of header and values changed leaves the
  reference valid while a code byte is still a mismatch.
- The demo build (tools/test/build_dirs.py): a copy of app.ecu.elf gets a valid list over every flash byte; once
  ecu/application uses nx_rom_check_image() (cmake/NxRomCheck.cmake) the app.ecu.hex of the build must carry it.

Run: .venv/Scripts/python -m unittest platforms/s32k1xx/port/safety/safeMemory/tools/test/test_rom_check_image.py
"""

from __future__ import annotations

import os
import random
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
REPO = HERE.parents[6]
sys.path.insert(0, str(TOOLS))
import romCheckImage as rc  # noqa: E402

LINKERSCRIPT_DIR = REPO / "platforms" / "s32k1xx" / "port" / "main" / "linkerscript"
HARD_FAULT_INCLUDE = REPO / "platforms" / "s32k1xx" / "hardFaultHandler" / "include"
APPLICATION_CMAKE = REPO / "ecu" / "application" / "CMakeLists.txt"
GCC_PATH = os.pathsep.join(p for p in (os.environ.get("NX_ARM_GCC_BIN"), os.environ.get("PATH")) if p)
ARM_GCC = shutil.which("arm-none-eabi-gcc", path=GCC_PATH)
ARM_LD = shutil.which("arm-none-eabi-ld", path=GCC_PATH)
ARM_OBJCOPY = shutil.which("arm-none-eabi-objcopy", path=GCC_PATH)
HAVE_ARM = bool(ARM_GCC and ARM_LD and ARM_OBJCOPY)


def hex_records(image: dict[int, bytes]) -> str:
    """Intel HEX text of {address: bytes} (16 bytes per record, extended linear address records)."""
    lines, upper = [], None
    for base in sorted(image):
        data = image[base]
        for offset in range(0, len(data), 16):
            address, chunk = base + offset, data[offset:offset + 16]
            if address >> 16 != upper:
                upper = address >> 16
                raw = bytes([2, 0, 0, 4]) + upper.to_bytes(2, "big")
                lines.append(":" + (raw + bytes([-sum(raw) & 0xFF])).hex().upper())
            raw = bytes([len(chunk)]) + (address & 0xFFFF).to_bytes(2, "big") + b"\x00" + chunk
            lines.append(":" + (raw + bytes([-sum(raw) & 0xFF])).hex().upper())
    lines.append(":00000001FF")
    return "\n".join(lines) + "\n"


class CrcTest(unittest.TestCase):

    def test_known_vector(self):
        words = [0x04030201, 0x08070605, 0xDEADBEEF, 0x00000000, 0xFFFFFFFF]
        self.assertEqual(rc.crc_unit_model(words), 0x498CFAA3)
        self.assertEqual(rc.crc_image(struct.pack("<5I", *words)), 0x498CFAA3)

    def test_fast_crc_equals_the_register_model(self):
        generator = random.Random(38)
        for count in (1, 2, 3, 7, 64, 257):
            data = bytes(generator.randrange(256) for _ in range(4 * count))
            self.assertEqual(rc.crc_image(data), rc.crc_unit_model(list(struct.unpack(f"<{count}I", data))))

    def test_same_area_limit_as_romcheck(self):
        """The build must not accept a list RomCheck refuses (RomCheckList.h, romcheck::MAX_AREAS)."""
        header = (TOOLS.parent / "include" / "safeMemory" / "RomCheckList.h").read_text(encoding="utf-8")
        self.assertIn(f"constexpr uint32_t MAX_AREAS = {rc.MAX_AREAS}U;", header)

    def test_length_must_be_whole_words(self):
        with self.assertRaises(ValueError):
            rc.crc_image(b"\x00\x01\x02")


class ImageRulesTest(unittest.TestCase):
    """check_image() on synthetic images: flash 0x8000.., list at 0x9000."""

    FLASH, LIST = 0x8000, 0x9000

    def image(self, areas, content=None, count=None, words=None, result=None, extra=None):
        content = content if content is not None else {self.FLASH: bytes(range(256)) * 2}
        values = [len(areas) if count is None else count] + [v for area in areas for v in area]
        list_bytes = struct.pack(f"<{len(values)}I", *values)
        symbols = {rc.LIST_SYMBOL: self.LIST, rc.LIST_END_SYMBOL: self.LIST + 4 * (words or len(values)),
                   rc.RESULT_SYMBOL: self.LIST + 4 * (words or len(values)), rc.FLASH_START_SYMBOL: self.FLASH}
        records = dict(content)
        records[self.LIST] = list_bytes + (struct.pack("<I", result) if result is not None else b"")
        if extra:
            records.update(extra)
        path = Path(self.tmp.name) / "image.hex"
        path.write_text(hex_records(records), encoding="ascii")
        return rc.read_hex(path), symbols

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_list_and_result(self):
        image, symbols = self.image([(0x8000, 0x8100), (0x8100, 0x8200)])
        check = rc.check_image(image, symbols)
        expected = rc.crc_image(bytes(range(256)) * 2)
        self.assertEqual(check.crc, expected)
        image, symbols = self.image([(0x8000, 0x8100), (0x8100, 0x8200)], result=expected)
        self.assertEqual(rc.check_image(image, symbols).stored, expected)

    def test_gap_inside_an_area_counts_as_erased(self):
        content = {0x8000: b"\x11" * 16, 0x8020: b"\x22" * 16}
        image, symbols = self.image([(0x8000, 0x8030)], content=content)
        check = rc.check_image(image, symbols)
        self.assertEqual(check.gap_bytes, 16)
        self.assertEqual(check.crc, rc.crc_image(b"\x11" * 16 + b"\xff" * 16 + b"\x22" * 16))

    def test_content_outside_every_area_stops_the_build(self):
        with self.assertRaisesRegex(rc.RomCheckError, "outside every ROM check area, first at 0x00008100"):
            rc.check_image(*self.image([(0x8000, 0x8100)]))

    def test_missing_list_stops_the_build(self):
        """The board image: the hex has no list, the flash reads 0xFF at __checksum_list_start."""
        image, symbols = self.image([(0x8000, 0x8200)])
        path = Path(self.tmp.name) / "without.hex"
        path.write_text(hex_records({self.FLASH: bytes(range(256)) * 2}), encoding="ascii")
        with self.assertRaisesRegex(rc.RomCheckError, "not in the image"):
            rc.check_image(rc.read_hex(path), symbols)

    def test_implausible_lists_stop_the_build(self):
        cases = {
            "no area": dict(areas=[], count=0, words=1),
            "too many areas": dict(areas=[(0x8000, 0x8200)] * 17),
            "erased count": dict(areas=[(0x8000, 0x8200)], count=0xFFFFFFFF),
            "length": dict(areas=[(0x8000, 0x8200)], count=2, words=3),
            "unaligned start": dict(areas=[(0x8001, 0x8200)]),
            "unaligned end": dict(areas=[(0x8000, 0x81FE)]),
            "reversed": dict(areas=[(0x8200, 0x8000)]),
            "below the flash": dict(areas=[(0x7000, 0x8200)]),
            "beyond the list": dict(areas=[(0x8000, 0x9010)]),
        }
        for name, case in cases.items():
            with self.subTest(name), self.assertRaises(rc.RomCheckError):
                rc.check_image(*self.image(**case))

    def test_changed_byte_is_a_mismatch(self):
        image, symbols = self.image([(0x8000, 0x8200)])
        reference = rc.check_image(image, symbols).crc
        for address in (0x8000, 0x8001, 0x80FF, 0x81FF):
            original = image.read(address, address + 1)[0]
            image.set_byte(address, original ^ 0x01)
            self.assertNotEqual(rc.check_image(image, symbols).crc, reference, hex(address))
            image.set_byte(address, original)
        self.assertEqual(rc.check_image(image, symbols).crc, reference)

    # SYS-91: the area with its own reference (calibration segment) outside the list

    def own(self, symbols, start=0x8100, end=0x8200):
        return {**symbols, rc.OWN_START_SYMBOL: start, rc.OWN_END_SYMBOL: end}

    def test_own_area_is_accepted_outside_the_list_and_not_part_of_the_crc(self):
        image, symbols = self.image([(0x8000, 0x8100)])
        check = rc.check_image(image, self.own(symbols))
        self.assertEqual(check.own_area, (0x8100, 0x8200))
        self.assertEqual(check.crc, rc.crc_image(bytes(range(256))))
        self.assertIn("own reference 0x00008100..0x00008200", rc.describe(check))
        # another content there (another dataset) leaves the reference of the build as it is
        image.set_byte(0x8100, image.read(0x8100, 0x8101)[0] ^ 0xFF)
        self.assertEqual(rc.check_image(image, self.own(symbols)).crc, check.crc)
        # content outside the list and outside the own area still stops the build
        with self.assertRaisesRegex(rc.RomCheckError, "first at 0x00008180"):
            rc.check_image(image, self.own(symbols, 0x8100, 0x8180))

    def test_own_area_rules(self):
        image, symbols = self.image([(0x8000, 0x8100)])
        cases = {
            "overlaps an area": (0x80FC, 0x8200),
            "unaligned": (0x8102, 0x8200),
            "empty": (0x8100, 0x8100),
            "below the flash": (0x7F00, 0x7F80),
            "over the list": (0x8100, 0x9004),
        }
        for name, (start, end) in cases.items():
            with self.subTest(name), self.assertRaises(rc.RomCheckError):
                rc.check_image(image, self.own(symbols, start, end))
        half = dict(symbols)
        half[rc.OWN_START_SYMBOL] = 0x8100
        with self.assertRaisesRegex(rc.RomCheckError, "only one of"):
            rc.check_image(image, half)


STUB_SOURCE = r"""
void Reset_Handler(void) {}
__attribute__((section(".isr_vector"), used)) void (*const vectors[8])(void) = {0, Reset_Handler};
__attribute__((section(".__startUpSection"), used)) const unsigned startup[2] = {0x11111111u, 0x22222222u};
__attribute__((used)) const char text[] = "rom check stub";
__attribute__((used)) int generalData = 0x5A5A5A5A;
__attribute__((section(".hot_data"), used)) int hotData = 0x3C3C3C3C;
__attribute__((section(".launchFromRAM"), used)) int ramFunction(int x) { return x * generalData + hotData; }
int main(void) { return ramFunction(text[0]); }
"""

LAYOUT = """#pragma once
#define NX_MEMORY_APP_FLASH_START 0x00008000
#define NX_MEMORY_APP_FLASH_END   0x0007F000
#define NX_MEMORY_APP_RAM_COUNT 2
#define NX_MEMORY_APP_RAM0_START 0x1FFF8000
#define NX_MEMORY_APP_RAM0_SIZE  0x00008000
#define NX_MEMORY_APP_RAM1_START 0x20000000
#define NX_MEMORY_APP_RAM1_SIZE  0x00007000
#define NX_MEMORY_APP_NOINIT_SIZE 0x00000400
#define NX_MEMORY_BOOT_REQUEST_ADDR 0x20006FF0
"""


def run(argv: list) -> subprocess.CompletedProcess:
    return subprocess.run([str(a) for a in argv], capture_output=True, text=True)


@unittest.skipUnless(HAVE_ARM, "arm-none-eabi-gcc/ld/objcopy not found (PATH or NX_ARM_GCC_BIN)")
class LinkerTemplateImageTest(unittest.TestCase):
    """The real application linker template, linked with a stub that fills every kind of flash content."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmp.name)
        (tmp / "generated" / "memory").mkdir(parents=True)
        (tmp / "generated" / "memory" / "memoryLayout.h").write_text(LAYOUT, encoding="utf-8", newline="\n")
        pre = run([ARM_GCC, "-E", "-P", "-x", "c", "-I", LINKERSCRIPT_DIR, "-I", HARD_FAULT_INCLUDE, "-I",
                   tmp / "generated", LINKERSCRIPT_DIR / "application.dld.in", "-o", tmp / "application.dld"])
        assert pre.returncode == 0, pre.stderr
        (tmp / "stub.c").write_text(STUB_SOURCE, encoding="utf-8", newline="\n")
        cc = run([ARM_GCC, "-c", "-mcpu=cortex-m4", "-mthumb", "-O1", tmp / "stub.c", "-o", tmp / "stub.o"])
        assert cc.returncode == 0, cc.stderr
        link = run([ARM_LD, "-T", tmp / "application.dld", "-o", tmp / "linked.elf", tmp / "stub.o"])
        assert link.returncode == 0, link.stderr
        cls.linked = tmp / "linked.elf"

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.elf = Path(self.work.name) / "app.elf"
        self.hex = Path(self.work.name) / "app.hex"
        shutil.copyfile(self.linked, self.elf)

    def tearDown(self):
        self.work.cleanup()

    def inject(self) -> int:
        return rc.main(["inject", "--elf", str(self.elf), "--hex", str(self.hex), "--objcopy", ARM_OBJCOPY])

    def test_inject_writes_the_reference_into_elf_and_hex(self):
        self.assertEqual(self.inject(), 0)
        symbols = rc.symbols_of(self.elf)
        check = rc.check_image(rc.read_hex(self.hex), symbols)
        self.assertEqual(check.stored, check.crc)
        self.assertEqual(len(check.areas), 5)
        # the list follows the image: first word behind the last flash content, not at the end of the flash
        self.assertLess(symbols[rc.LIST_SYMBOL], 0x7F000 - 64)
        # the ELF carries the same reference (the hex is made from it, the debugger flashes the ELF)
        again = Path(self.work.name) / "again.hex"
        rc.objcopy_hex(ARM_OBJCOPY, self.elf, again)
        self.assertEqual(rc.check_image(rc.read_hex(again), symbols).stored, check.crc)
        self.assertEqual(rc.main(["check", "--elf", str(self.elf), "--hex", str(self.hex)]), 0)

    def test_every_kind_of_flash_content_is_checked(self):
        """IVT, startup section, code and read-only data, initialized hot data, RAM function and general data: a
        changed byte in each of them is a mismatch."""
        self.assertEqual(self.inject(), 0)
        symbols = rc.symbols_of(self.elf)
        image = rc.read_hex(self.hex)
        reference = rc.check_image(image, symbols).stored
        probes = {"vector table": 0x8004, "startup section": 0x8410}
        with open(self.elf, "rb") as handle:
            _, elf_symbols = rc._elf(handle.read())
        probes["code"] = elf_symbols["__ROM_CRC_START"]
        probes["data ROM (hot data, RAM functions)"] = elf_symbols["__DATA_ROM_START"]
        probes["data ROM (general data)"] = elf_symbols["__DATA_ROM_GENERAL"]
        for name, address in probes.items():
            with self.subTest(name):
                self.assertEqual(image.present(address, address + 1), 1, f"{name}: no content at 0x{address:X}")
                original = image.read(address, address + 1)[0]
                image.set_byte(address, original ^ 0x80)
                self.assertNotEqual(rc.check_image(image, symbols).crc, reference)
                image.set_byte(address, original)

    def test_changed_hex_fails_the_check(self):
        """What the board test does with canflash: one byte of the image changed after the build."""
        self.assertEqual(self.inject(), 0)
        lines = self.hex.read_text(encoding="ascii").splitlines()
        index = next(i for i, line in enumerate(lines) if line[7:9] == "00" and int(line[1:3], 16) >= 4)
        raw = bytearray(bytes.fromhex(lines[index][1:]))
        raw[4] ^= 0xFF
        raw[-1] = -sum(raw[:-1]) & 0xFF
        lines[index] = ":" + raw.hex().upper()
        changed = Path(self.work.name) / "changed.hex"
        changed.write_text("\n".join(lines) + "\n", encoding="ascii")
        self.assertEqual(rc.main(["check", "--elf", str(self.elf), "--hex", str(changed)]), 1)

    def test_hex_without_the_list_is_refused(self):
        """The build before SYS-38: objcopy --remove-section=.checksum, the flash reads 0xFF at the list."""
        self.assertEqual(self.inject(), 0)
        stripped = Path(self.work.name) / "stripped.hex"
        result = run([ARM_OBJCOPY, "-O", "ihex", "--remove-section=.checksum", self.elf, stripped])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(rc.main(["check", "--elf", str(self.elf), "--hex", str(stripped)]), 1)

    def test_elf_without_list_stops_the_build(self):
        tmp = Path(self.work.name)
        (tmp / "plain.ld").write_text("SECTIONS { .text 0x8000 : { *(.isr_vector) *(.text*) *(.rodata*) } }\n",
                                      encoding="utf-8")
        link = run([ARM_LD, "-T", tmp / "plain.ld", "-o", tmp / "plain.elf", Path(self.linked).with_name("stub.o")])
        self.assertEqual(link.returncode, 0, link.stderr)
        self.assertEqual(rc.main(["inject", "--elf", str(tmp / "plain.elf"), "--hex", str(tmp / "plain.hex"),
                                  "--objcopy", ARM_OBJCOPY]), 1)
        self.assertFalse((tmp / "plain.hex").exists())


CAL_LAYOUT = LAYOUT + """#define NX_MEMORY_CAL_START 0x00009000
#define NX_MEMORY_CAL_SIZE 0x00001000
#define NX_MEMORY_CAL_HEADER_SIZE 32
#define NX_MEMORY_CAL_IN_APP_FLASH 1
#define NX_MEMORY_CAL_PRODUCTION {production}
#define NX_MEMORY_CAL_ON_INVALID 0
"""
CAL_PARAMETERS = "unsigned short Rte_Prm_B_P_Y_data = 7U;\nshort Rte_Prm_C_P_Z_data[3] = {1, -2, 3};\n"


@unittest.skipUnless(HAVE_ARM, "arm-none-eabi-gcc/ld/objcopy not found (PATH or NX_ARM_GCC_BIN)")
class CalibrationSegmentImageTest(unittest.TestCase):
    """SYS-91: the template with a calibration segment (both profiles). Board 2026-09-28: one flipped bit in the CALV
    header held the ECU in the safe state through the ROM check - the segment was the sixth area of the list, with
    the reference of the build. Now the list has five areas, the segment is the area with its own reference, and any
    content there (another dataset, a damaged header) leaves the ROM check green: the start-up check of
    ecu/calibration decides."""

    def link(self, tmp: Path, production: bool) -> Path:
        (tmp / "generated" / "memory").mkdir(parents=True)
        (tmp / "generated" / "memory" / "memoryLayout.h").write_text(
            CAL_LAYOUT.format(production=1 if production else 0), encoding="utf-8", newline="\n")
        pre = run([ARM_GCC, "-E", "-P", "-x", "c", "-I", LINKERSCRIPT_DIR, "-I", HARD_FAULT_INCLUDE, "-I",
                   tmp / "generated", LINKERSCRIPT_DIR / "application.dld.in", "-o", tmp / "application.dld"])
        self.assertEqual(pre.returncode, 0, pre.stderr)
        (tmp / "stub.c").write_text(STUB_SOURCE + CAL_PARAMETERS, encoding="utf-8", newline="\n")
        cc = run([ARM_GCC, "-c", "-mcpu=cortex-m4", "-mthumb", "-O1", "-fdata-sections", tmp / "stub.c", "-o",
                  tmp / "stub.o"])
        self.assertEqual(cc.returncode, 0, cc.stderr)
        link = run([ARM_LD, "-T", tmp / "application.dld", "-o", tmp / "app.elf", tmp / "stub.o"])
        self.assertEqual(link.returncode, 0, link.stderr)
        return tmp / "app.elf"

    def test_segment_content_does_not_touch_the_rom_check(self):
        for production in (False, True):
            with self.subTest(production=production), tempfile.TemporaryDirectory() as name:
                tmp = Path(name)
                elf, hex_path = self.link(tmp, production), tmp / "app.hex"
                self.assertEqual(rc.main(["inject", "--elf", str(elf), "--hex", str(hex_path),
                                          "--objcopy", ARM_OBJCOPY]), 0)
                all_symbols = rc.symbols_of(elf)
                image = rc.read_hex(hex_path)
                check = rc.check_image(image, all_symbols)
                self.assertEqual(len(check.areas), 5)
                self.assertEqual(check.own_area, (0x9000, 0xA000))
                self.assertTrue(all(b <= 0x9000 or a >= 0xA000 for a, b in check.areas), check.areas)
                self.assertEqual(check.stored, check.crc)
                segment = all_symbols["__nx_cal_segment"]
                self.assertGreater(image.present(segment, segment + 32), 0, "the image carries the segment")
                # every byte of the header and of the values changed: the ROM check stays green
                for address in range(segment, all_symbols["__nx_cal_values_end"]):
                    image.set_byte(address, image.read(address, address + 1)[0] ^ 0x01)
                self.assertEqual(rc.check_image(image, all_symbols).crc, check.stored)
                # a byte of the code still is a mismatch
                code = all_symbols["__ROM_CRC_START"]
                image.set_byte(code, image.read(code, code + 1)[0] ^ 0x01)
                self.assertNotEqual(rc.check_image(image, all_symbols).crc, check.stored)


def demo_elf() -> Path | None:
    sys.path.insert(0, str(REPO / "tools" / "test"))
    import build_dirs  # noqa: E402
    found = sorted(build_dirs.demo_build_dir(REPO).glob("**/app.ecu.elf"), key=lambda p: p.stat().st_mtime)
    return found[-1] if found else None


@unittest.skipUnless(HAVE_ARM, "arm-none-eabi-objcopy not found (PATH or NX_ARM_GCC_BIN)")
class DemoBuildTest(unittest.TestCase):
    """The application of the demo project (tools/test -k target-demo builds it)."""

    def setUp(self):
        self.elf = demo_elf()
        if self.elf is None:
            self.skipTest("no demo build (tools/test -k target-demo, NX_BUILD_DIR)")

    def test_copy_of_the_demo_image_gets_a_valid_list(self):
        with tempfile.TemporaryDirectory() as name:
            elf, hex_path = Path(name) / "app.elf", Path(name) / "app.hex"
            shutil.copyfile(self.elf, elf)
            self.assertEqual(rc.main(["inject", "--elf", str(elf), "--hex", str(hex_path),
                                      "--objcopy", ARM_OBJCOPY]), 0)
            check = rc.check_image(rc.read_hex(hex_path), rc.symbols_of(elf))
            self.assertEqual(len(check.areas), 5)
            self.assertEqual(check.stored, check.crc)

    def test_build_hex_carries_list_and_reference(self):
        if "nx_rom_check_image" not in APPLICATION_CMAKE.read_text(encoding="utf-8"):
            self.skipTest("ecu/application does not use nx_rom_check_image() yet (SYS-38, patch for the coordinator)")
        hex_path = self.elf.with_suffix(".hex")
        self.assertEqual(rc.main(["check", "--elf", str(self.elf), "--hex", str(hex_path)]), 0)


if __name__ == "__main__":
    unittest.main()
