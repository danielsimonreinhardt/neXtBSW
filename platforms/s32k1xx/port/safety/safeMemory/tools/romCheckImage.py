"""Post-link step of the ROM check (SM-14, SYS-38): writes the reference CRC into the application image.

RomCheck (src/safeMemory/RomCheck.cpp) runs the CRC unit of the S32K1 over the flash areas listed at
`__checksum_list_start` and compares the result with the word at `__checksum_result`. The linker script
(port/main/linkerscript/application.dld.in) places the list right behind the last flash content; the reference CRC
can only be computed after the link. This script does it and produces the hex the bootloader download uses, so the
CRC is in the image before `canflash record` computes the validity record over the same file:

    romCheckImage.py inject --elf app.ecu.elf --hex app.ecu.hex --objcopy arm-none-eabi-objcopy
    romCheckImage.py check  --elf app.ecu.elf --hex app.ecu.hex      # verification only, exit 1 on mismatch

`inject` refuses (exit code 1, the build fails) instead of producing an image that would stop the ECU at run time:
list symbols missing, list not in the image, number of areas or list length implausible, an area outside the
application flash or not word aligned, flash content outside every area (it would escape the check), and after
writing: the hex must contain the list and the matching result.

SYS-91: an area with its own reference. With a calibration segment (memory.calibration) the linker template defines
`__rom_check_own_area_start/_end` around its region and leaves it out of the list: its content may come from a dataset
generated without a build (tools/calseg hex --dataset), so the build reference cannot cover it; the application checks
it against the CRCs of its CALV header (ecu/calibration, at start-up and in every pass of RomCheck). Image content is
accepted outside the list only there; the area must be word aligned, inside the application flash and overlap no area
of the list and not the list itself - otherwise the build stops as before.

Gaps inside an area (alignment between output sections) are taken as 0xFF: canflash downloads the image from the
start of the application to its last byte with gaps filled with 0xFF (tools/canflash, load_app_image), a debug probe
erases whole sectors. The CRC matches the S32K1 CRC unit as RomCheck configures it (32 bit, polynomial 0x04C11DB7,
seed 0xFFFFFFFF, TOT=1, TOTR=2, FXOR=1, one 32-bit write per word): that is the CRC-32 of zlib over every word with
its bytes in big endian order (`crc_unit_model` is the register level model the tests compare against).

Standard library only (no pyelftools, which the OpenBSW script tools/safety/crcCheck.py needs and this repository
does not install).
"""

from __future__ import annotations

import argparse
import struct
import subprocess
import sys
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path

# Same limit as romcheck::MAX_AREAS in include/safeMemory/RomCheckList.h
MAX_AREAS = 16
LIST_SYMBOL = "__checksum_list_start"
LIST_END_SYMBOL = "__checksum_list_end"
RESULT_SYMBOL = "__checksum_result"
FLASH_START_SYMBOL = "__FLASH_START"
SYMBOLS = (LIST_SYMBOL, LIST_END_SYMBOL, RESULT_SYMBOL, FLASH_START_SYMBOL)
# SYS-91: optional, the area with its own reference (the calibration segment), outside the list
OWN_START_SYMBOL = "__rom_check_own_area_start"
OWN_END_SYMBOL = "__rom_check_own_area_end"


class RomCheckError(Exception):
    """The image cannot carry a valid ROM check - the build must stop."""


# ------------------------------------------------------------------------------------------------ CRC

def crc_image(data: bytes) -> int:
    """CRC of the S32K1 CRC unit over whole words of `data` (little endian in flash)."""
    if len(data) % 4:
        raise ValueError("CRC over a length that is no multiple of 4")
    swapped = bytearray(len(data))
    swapped[0::4], swapped[1::4], swapped[2::4], swapped[3::4] = data[3::4], data[2::4], data[1::4], data[0::4]
    return zlib.crc32(bytes(swapped)) & 0xFFFFFFFF


def _reflect(value: int, bits: int) -> int:
    result = 0
    for _ in range(bits):
        result = (result << 1) | (value & 1)
        value >>= 1
    return result


def crc_unit_model(words: list[int], polynomial: int = 0x04C11DB7, seed: int = 0xFFFFFFFF) -> int:
    """Register level model of the CRC unit ([RM] CRC chapter): CTRL TCRC=1 (32 bit), TOT=1 (bits in each byte
    transposed on write), TOTR=2 (bits and bytes transposed on read), FXOR=1 (read complemented); the seed is
    written with WAS=1, every data word with one 32-bit write, processed from bit 31 down."""
    crc = seed
    for word in words:
        transposed = 0
        for byte_index in range(4):
            byte = (word >> (8 * byte_index)) & 0xFF
            transposed |= _reflect(byte, 8) << (8 * byte_index)
        crc ^= transposed
        for _ in range(32):
            crc = ((crc << 1) ^ polynomial) & 0xFFFFFFFF if crc & 0x80000000 else (crc << 1) & 0xFFFFFFFF
    return _reflect(crc, 32) ^ 0xFFFFFFFF


# ------------------------------------------------------------------------------------------------ ELF (32 bit, LE)

@dataclass
class Section:
    name: str
    kind: int
    flags: int
    addr: int
    offset: int
    size: int


def _elf(data: bytes) -> tuple[list[Section], dict[str, int]]:
    if data[:4] != b"\x7fELF" or data[4] != 1 or data[5] != 1:
        raise RomCheckError("not a 32-bit little endian ELF file")
    shoff, = struct.unpack_from("<I", data, 0x20)
    shentsize, shnum, shstrndx = struct.unpack_from("<HHH", data, 0x2E)
    raw = [struct.unpack_from("<IIIIIIIIII", data, shoff + i * shentsize) for i in range(shnum)]
    names_offset = raw[shstrndx][4]

    def cstr(base: int, index: int) -> str:
        end = data.index(b"\0", base + index)
        return data[base + index:end].decode("ascii", "replace")

    sections = [Section(cstr(names_offset, r[0]), r[1], r[2], r[3], r[4], r[5]) for r in raw]
    symbols: dict[str, int] = {}
    for r in raw:
        if r[1] != 2:                   # SHT_SYMTAB
            continue
        strings = raw[r[6]][4]
        for pos in range(r[4], r[4] + r[5], 16):
            name, value = struct.unpack_from("<II", data, pos)
            if name:
                symbols.setdefault(cstr(strings, name), value)
    return sections, symbols


def symbols_of(elf: Path) -> dict[str, int]:
    _, symbols = _elf(elf.read_bytes())
    missing = [s for s in SYMBOLS if s not in symbols]
    if missing:
        raise RomCheckError(f"{elf.name}: symbols {', '.join(missing)} missing - the linker script defines no ROM "
                            "check list (.checksum, platforms/s32k1xx/port/main/linkerscript/application.dld.in)")
    return symbols


def write_word(elf: Path, address: int, value: int) -> None:
    """Writes `value` at `address` into the file content of the allocated section that holds it."""
    data = bytearray(elf.read_bytes())
    sections, _ = _elf(bytes(data))
    for s in sections:
        if s.kind == 1 and s.flags & 0x2 and s.addr <= address and address + 4 <= s.addr + s.size:   # PROGBITS, ALLOC
            struct.pack_into("<I", data, s.offset + address - s.addr, value)
            elf.write_bytes(bytes(data))
            return
    raise RomCheckError(f"{elf.name}: no section with content at 0x{address:08X} ({RESULT_SYMBOL})")


# ------------------------------------------------------------------------------------------------ Intel HEX

class Image:
    """Flash content between the lowest and the highest address of a hex: bytes (0xFF where the hex has none) and
    a mask of the bytes the hex does contain."""

    def __init__(self, records: list[tuple[int, bytes]]):
        if not records:
            raise RomCheckError("hex without data")
        self.low = min(a for a, _ in records)
        self.high = max(a + len(p) for a, p in records)
        self.data = bytearray(b"\xff") * (self.high - self.low)
        self.mask = bytearray(self.high - self.low)
        for address, payload in records:
            index = address - self.low
            self.data[index:index + len(payload)] = payload
            self.mask[index:index + len(payload)] = b"\x01" * len(payload)

    def present(self, start: int, end: int) -> int:
        """Number of bytes in [start, end) the hex contains."""
        a, b = max(start, self.low), min(end, self.high)
        return self.mask[a - self.low:b - self.low].count(1) if a < b else 0

    def read(self, start: int, end: int) -> bytes:
        """[start, end) as the flash holds it after the download: bytes the hex does not contain read 0xFF."""
        out = bytearray(b"\xff") * (end - start)
        a, b = max(start, self.low), min(end, self.high)
        if a < b:
            out[a - start:b - start] = self.data[a - self.low:b - self.low]
        return bytes(out)

    def word(self, address: int) -> int | None:
        if self.present(address, address + 4) != 4:
            return None
        return int.from_bytes(self.read(address, address + 4), "little")

    def set_byte(self, address: int, value: int) -> None:
        """For the tests: change one byte (mutation of the image)."""
        self.data[address - self.low] = value

    def outside(self, ranges: list[tuple[int, int]]) -> tuple[int, int | None]:
        """Number and first address of the bytes the hex contains outside every range."""
        covered = bytearray(len(self.mask))
        for start, end in ranges:
            a, b = max(start, self.low), min(end, self.high)
            if a < b:
                covered[a - self.low:b - self.low] = b"\x01" * (b - a)
        loose = int.from_bytes(self.mask, "little") & ~int.from_bytes(covered, "little")
        if not loose:
            return 0, None
        return bin(loose).count("1"), self.low + ((loose & -loose).bit_length() - 1) // 8


def read_hex(path: Path) -> Image:
    """Flash content of an Intel HEX file (record types 00, 01, 02, 03, 04, 05)."""
    records: list[tuple[int, bytes]] = []
    base = 0
    for number, line in enumerate(path.read_text(encoding="ascii").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        if not line.startswith(":"):
            raise RomCheckError(f"{path.name}:{number}: no Intel HEX record")
        raw = bytes.fromhex(line[1:])
        if sum(raw) & 0xFF:
            raise RomCheckError(f"{path.name}:{number}: checksum of the record wrong")
        count, offset, kind, payload = raw[0], (raw[1] << 8) | raw[2], raw[3], raw[4:4 + raw[0]]
        if kind == 0:
            records.append((base + offset, bytes(payload[:count])))
        elif kind == 1:
            break
        elif kind == 2:
            base = int.from_bytes(payload, "big") << 4
        elif kind == 4:
            base = int.from_bytes(payload, "big") << 16
    return Image(records)


def objcopy_hex(objcopy: str, elf: Path, hex_path: Path) -> None:
    result = subprocess.run([objcopy, "-O", "ihex", str(elf), str(hex_path)], capture_output=True, text=True)
    if result.returncode:
        raise RomCheckError(f"{objcopy} -O ihex failed: {result.stderr.strip()}")


# ------------------------------------------------------------------------------------------------ list and CRC

@dataclass
class Check:
    areas: list[tuple[int, int]]
    crc: int
    stored: int | None
    list_start: int
    result_address: int
    gap_bytes: int
    own_area: tuple[int, int] | None = None     # SYS-91: checked against its own reference, not in the list

    @property
    def size(self) -> int:
        return sum(end - start for start, end in self.areas)


def read_list(image: Image, symbols: dict[str, int]) -> list[tuple[int, int]]:
    """The areas of the list as RomCheck sees them in flash, with RomCheck's plausibility rules (RomCheckList.h)."""
    start, end = symbols[LIST_SYMBOL], symbols[LIST_END_SYMBOL]
    lowest = symbols[FLASH_START_SYMBOL]
    words = (end - start) // 4
    values = [image.word(start + 4 * i) for i in range(words)]
    if not values or any(v is None for v in values):
        raise RomCheckError(f"ROM check list at 0x{start:08X} is not in the image (flash would read 0xFF there)")
    count = values[0]
    if count == 0 or count > MAX_AREAS:
        raise RomCheckError(f"ROM check list: {count} areas (1..{MAX_AREAS} allowed)")
    if words != 1 + 2 * count:
        raise RomCheckError(f"ROM check list: {words} words for {count} areas (expected {1 + 2 * count})")
    areas = [(values[1 + 2 * i], values[2 + 2 * i]) for i in range(count)]
    for index, (a, b) in enumerate(areas):
        if a % 4 or b % 4 or a > b or a < lowest or b > start:
            raise RomCheckError(f"ROM check area {index} 0x{a:08X}..0x{b:08X}: not word aligned or outside the "
                                f"application flash 0x{lowest:08X}..0x{start:08X}")
    return areas


def own_area(symbols: dict[str, int], areas: list[tuple[int, int]]) -> tuple[int, int] | None:
    """SYS-91: the area with its own reference (both symbols or none), with the rules that keep it apart from the
    list: word aligned, not empty, inside [application flash start, list), overlapping no area of the list."""
    have = [s in symbols for s in (OWN_START_SYMBOL, OWN_END_SYMBOL)]
    if not any(have):
        return None
    if not all(have):
        raise RomCheckError(f"only one of {OWN_START_SYMBOL}/{OWN_END_SYMBOL} defined")
    start, end = symbols[OWN_START_SYMBOL], symbols[OWN_END_SYMBOL]
    lowest, list_start = symbols[FLASH_START_SYMBOL], symbols[LIST_SYMBOL]
    if start % 4 or end % 4 or start >= end or start < lowest or end > list_start:
        raise RomCheckError(f"area with own reference 0x{start:08X}..0x{end:08X}: not word aligned, empty or outside "
                            f"the application flash 0x{lowest:08X}..0x{list_start:08X}")
    for index, (a, b) in enumerate(areas):
        if a < end and start < b:
            raise RomCheckError(f"ROM check area {index} 0x{a:08X}..0x{b:08X} overlaps the area with own reference "
                                f"0x{start:08X}..0x{end:08X} - its bytes would be tied to the build reference")
    return start, end


def check_image(image: Image, symbols: dict[str, int]) -> Check:
    areas = read_list(image, symbols)
    own = own_area(symbols, areas)
    list_start, result_address = symbols[LIST_SYMBOL], symbols[RESULT_SYMBOL]
    # Every flash byte of the image must be checked, except the list and the result themselves and (SYS-91) the area
    # with its own reference
    count, first = image.outside([*areas, (list_start, result_address + 4), *([own] if own else [])])
    if count:
        raise RomCheckError(f"{count} image bytes outside every ROM check area, first at 0x{first:08X} - an output "
                            "section the list in application.dld.in does not cover")
    data = b"".join(image.read(start, end) for start, end in areas)
    gaps = sum((end - start) - image.present(start, end) for start, end in areas)
    return Check(areas, crc_image(data), image.word(result_address), list_start, result_address, gaps, own)


def inject(elf: Path, hex_path: Path, objcopy: str) -> Check:
    symbols = symbols_of(elf)
    with tempfile.TemporaryDirectory() as tmp:
        plain = Path(tmp) / "plain.hex"
        objcopy_hex(objcopy, elf, plain)
        computed = check_image(read_hex(plain), symbols)
    write_word(elf, symbols[RESULT_SYMBOL], computed.crc)
    objcopy_hex(objcopy, elf, hex_path)
    final = check_image(read_hex(hex_path), symbols)
    if final.stored != final.crc or final.crc != computed.crc:
        raise RomCheckError(f"{hex_path.name}: ROM check result not in the hex (stored {final.stored}, "
                            f"computed 0x{final.crc:08X})")
    return final


def describe(check: Check) -> str:
    own = "" if check.own_area is None else (f", own reference 0x{check.own_area[0]:08X}..0x{check.own_area[1]:08X} "
                                             "(not in the list)")
    return (f"{len(check.areas)} areas, {check.size} bytes ({check.gap_bytes} gap bytes as 0xFF), "
            f"CRC 0x{check.crc:08X}, list at 0x{check.list_start:08X}, result at 0x{check.result_address:08X}{own}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_inject = sub.add_parser("inject", help="write the reference CRC into the ELF and produce the hex")
    p_inject.add_argument("--elf", type=Path, required=True)
    p_inject.add_argument("--hex", type=Path, required=True)
    p_inject.add_argument("--objcopy", default="arm-none-eabi-objcopy")
    p_check = sub.add_parser("check", help="verify that the hex carries a valid list and the matching CRC")
    p_check.add_argument("--elf", type=Path, required=True, help="for the symbol addresses")
    p_check.add_argument("--hex", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "inject":
            print(f"romcheck: {args.hex.name}: {describe(inject(args.elf, args.hex, args.objcopy))}")
            return 0
        check = check_image(read_hex(args.hex), symbols_of(args.elf))
        stored = "none" if check.stored is None else f"0x{check.stored:08X}"
        if check.stored != check.crc:
            print(f"romcheck: {args.hex.name}: MISMATCH, stored {stored}, {describe(check)}")
            return 1
        print(f"romcheck: {args.hex.name}: OK, {describe(check)}")
        return 0
    except RomCheckError as error:
        print(f"romcheck: error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
