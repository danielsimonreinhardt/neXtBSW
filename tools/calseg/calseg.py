# SPDX-License-Identifier: Apache-2.0
"""Calibration segment of an application image (SYS-89, stage K3 of doc/architecture/kalibrierung.md).

The linker template reserves the segment (memory.calibration of the project, placed by tools/nxconfig/memory.py):
32 bytes of header, then the values of all calibration parameters (rte::PARAMETERS). The header is the contract with
the start-up check of the application (ecu/calibration/include/calibration/CalibrationSegment.h):

    offset  0 magic "CALV"   4 format 1 (uint16)   6 header size 32 (uint16)   8 layout identifier
           12 dataset        16 length of the values   20 CRC-32 of the values   24 reserved 0xFFFFFFFF
           28 CRC-32 of bytes 0..27                                                (little endian, CRC of zlib)

Commands:

    calseg.py seal --elf app.ecu.elf --description RteCalibration.json --rte-config RteConfig.h
        after the link: in the development profile copy the values the build put into the RAM working page into the
        segment, then write the header into the ELF: layout identifier of RteCalibration.json, dataset identifier =
        the identifier of the values of a calibration dataset ("dataset": {"values": ...}, TOOL-145) or 0 for the
        defaults of <Swc>_data.m, length, CRCs. Stops (exit 1) if a parameter of rte::PARAMETERS lies outside the
        segment or its working page, or the sizes disagree.

    calseg.py hex --elf app.ecu.elf --rte-config RteConfig.h -o segment.hex [--dataset TARGET [--variant V]]
                                                                             [--values values.json]
        the sealed segment alone as Intel HEX. Without a dataset: the values of the build. With one: the stored
        values of a calibration dataset replace them and the header gets its identifier and new CRCs - without a
        new build, for any image with the same layout identifier. TARGET is what tools/calgen takes (project.yaml,
        rte.yaml, --variant as there); --values is the file `calgen values --json` writes.

    calseg.py show --elf app.ecu.elf [--hex segment.hex]
        header and check of the segment in the ELF (or in a segment hex) as the application would see it.

    calseg.py merge --elf app.ecu.elf --image app.ecu.hex [--segment segment.hex] [--flip OFFSET:BIT ...] -o out.hex
        SYS-91, test aid: the download image with its segment replaced by a segment hex (another dataset) and/or
        single bits flipped (a damaged header or value), to flash with canflash. Only segment bytes change; the ROM
        check list of the build does not cover them (area with own reference), so the start-up check of the
        application decides (on_invalid). Prints the status that check will give; exit 0 even for a damaged one.

The values of a dataset come from tools/calgen (stage K2): `calgen.values_of()` resolves and checks them with the
functions of the build and gives per parameter its A2L name and the **stored** values in memory order, plus the layout
identifier and the identifier of the values. This tool only places them at the addresses the ELF gives and writes
the header; it neither converts nor judges a value. The ELF and the RTE tables are read with tools/a2lgen.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "a2lgen"))
sys.path.insert(0, str(HERE.parent / "calgen"))

import a2lgen  # noqa: E402
from elf32 import Elf32, ElfError  # noqa: E402

MAGIC = 0x564C4143                  # "CALV"
FORMAT = 1
HEADER_SIZE = 32
RESERVED = 0xFFFFFFFF
DEFAULTS_DATASET = 0                # dataset identifier of a build without calibration dataset (<Swc>_data.m)
# symbols of the linker template (platforms/<family>/port/main/linkerscript/application.dld.in)
SEGMENT = "__nx_cal_segment"
VALUES = "__nx_cal_values"
VALUES_END = "__nx_cal_values_end"
RAM_START = "__nx_cal_ram_start"   # development profile only
RAM_END = "__nx_cal_ram_end"
PACK = {"BOOLEAN": "B", "UINT8": "B", "SINT8": "b", "UINT16": "H", "SINT16": "h", "UINT32": "I", "SINT32": "i",
        "FLOAT32": "f", "FLOAT64": "d"}


class CalsegError(Exception):
    """The segment cannot be sealed or written - the build must stop."""


@dataclass(frozen=True)
class Layout:
    """Where the segment of an image lies."""

    segment: int                    # header address
    values: int                     # values in flash (reference page)
    length: int
    ram: int | None                 # working page (development profile), None: production profile

    @property
    def production(self) -> bool:
        return self.ram is None

    def offset(self, address: int) -> int:
        """Offset of a parameter address in the values."""
        return address - (self.values if self.ram is None else self.ram)


@dataclass(frozen=True)
class Header:
    magic: int
    format: int
    header_size: int
    layout: int
    dataset: int
    length: int
    values_crc: int
    reserved: int
    header_crc: int


def crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def make_header(layout: int, dataset: int, values: bytes) -> bytes:
    head = struct.pack("<IHHIIIII", MAGIC, FORMAT, HEADER_SIZE, layout, dataset, len(values), crc32(values), RESERVED)
    return head + struct.pack("<I", crc32(head))


def read_header(data: bytes) -> Header:
    return Header(*struct.unpack_from("<IHHIIIIII", data, 0))


def check(segment: bytes, expected_layout: int, expected_length: int) -> str:
    """The start-up check of the application (CalibrationSegment.cpp check()) on a segment: status name."""
    if len(segment) < HEADER_SIZE:
        return "LENGTH"
    if segment[:HEADER_SIZE] == b"\xff" * HEADER_SIZE:
        return "ERASED"
    h = read_header(segment)
    if h.magic != MAGIC:
        return "MAGIC"
    if h.format != FORMAT or h.header_size != HEADER_SIZE:
        return "FORMAT"
    if crc32(segment[:28]) != h.header_crc:
        return "HEADER_CRC"
    if h.length != expected_length or h.length > len(segment) - HEADER_SIZE:
        return "LENGTH"
    if h.layout != expected_layout:
        return "LAYOUT"
    if crc32(segment[HEADER_SIZE:HEADER_SIZE + h.length]) != h.values_crc:
        return "VALUES_CRC"
    return "VALID"


def layout_of(elf: Elf32) -> Layout:
    missing = [s for s in (SEGMENT, VALUES, VALUES_END) if s not in elf.symbols]
    if missing:
        raise CalsegError(f"{elf.path.name}: symbols {', '.join(missing)} missing - the linker script has no "
                          "calibration segment (memory.calibration, application.dld.in)")
    segment, values, end = (elf.symbols[s].value for s in (SEGMENT, VALUES, VALUES_END))
    if values != segment + HEADER_SIZE or end < values:
        raise CalsegError(f"{elf.path.name}: values at 0x{values:08X}..0x{end:08X} do not follow the "
                          f"{HEADER_SIZE}-byte header at 0x{segment:08X}")
    ram = None
    if RAM_START in elf.symbols or RAM_END in elf.symbols:
        if RAM_START not in elf.symbols or RAM_END not in elf.symbols:
            raise CalsegError(f"{elf.path.name}: only one of {RAM_START}/{RAM_END}")
        ram = elf.symbols[RAM_START].value
        if elf.symbols[RAM_END].value - ram != end - values:
            raise CalsegError(f"{elf.path.name}: working page {elf.symbols[RAM_END].value - ram} bytes, flash "
                              f"segment {end - values} bytes - they must be equal")
    return Layout(segment, values, end - values, ram)


def parameters_of(elf_path: Path, rte_config: Path) -> list:
    try:
        return a2lgen.read_image(elf_path, a2lgen.load_signal_types(rte_config)).parameters
    except a2lgen.A2lError as exc:
        raise CalsegError(str(exc)) from exc


def check_parameters(layout: Layout, parameters: list, name: str) -> None:
    """Every parameter lies inside the values (production) or the working page (development)."""
    base = layout.values if layout.production else layout.ram
    where = "flash segment" if layout.production else "RAM working page"
    for p in parameters:
        size = a2lgen.TYPES[p.type][1] * p.count
        if not (base <= p.address and p.address + size <= base + layout.length):
            raise CalsegError(f"{name}: parameter {p.name} at 0x{p.address:08X} ({size} bytes) lies outside the "
                              f"{where} 0x{base:08X}..0x{base + layout.length:08X} - its storage "
                              "(.data.Rte_Prm_*/.bss.Rte_Prm_*, -fdata-sections) was not collected by the linker "
                              "template")


def write_bytes(elf_path: Path, address: int, payload: bytes) -> None:
    """Writes payload at address into the file content of the allocated section with contents that holds it."""
    elf = Elf32(elf_path)
    data = bytearray(elf.data)
    for s in elf.sections:
        if s.flags & 0x2 and s.kind != 8 and s.address <= address and address + len(payload) <= s.address + s.size:
            data[s.offset + address - s.address:s.offset + address - s.address + len(payload)] = payload
            elf_path.write_bytes(bytes(data))
            return
    raise CalsegError(f"{elf_path.name}: no section with contents holds 0x{address:08X}..0x"
                      f"{address + len(payload) - 1:08X} (calibration segment)")


def hex_id(value, what: str) -> int:
    try:
        number = int(str(value), 16)
    except ValueError:
        raise CalsegError(f"{what} {value!r} is no hex number") from None
    if not 0 <= number <= 0xFFFFFFFF:
        raise CalsegError(f"{what} {value!r} is no 32-bit number")
    return number


def description_ids(description: Path) -> tuple[int, int]:
    """(layout identifier, dataset identifier) of RteCalibration.json; dataset 0 without a calibration dataset."""
    try:
        content = json.loads(description.read_text(encoding="utf-8"))
        layout = content["layout"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CalsegError(f"{description}: no layout identifier ({exc})") from exc
    dataset = content.get("dataset")
    values = dataset.get("values") if isinstance(dataset, dict) else None
    return (hex_id(layout, f"{description.name}: layout"),
            DEFAULTS_DATASET if values is None else hex_id(values, f"{description.name}: dataset values"))


def seal(elf_path: Path, description: Path, rte_config: Path) -> tuple[Layout, bytes]:
    """Values (development: from the working page) and header into the flash segment of the ELF."""
    layout_id, dataset_id = description_ids(description)
    try:
        elf = Elf32(elf_path)
        layout = layout_of(elf)
        check_parameters(layout, parameters_of(elf_path, rte_config), elf_path.name)
        values = elf.read(layout.ram if not layout.production else layout.values, layout.length) \
            if layout.length else b""
    except ElfError as exc:
        raise CalsegError(str(exc)) from exc
    if not layout.production and layout.length:
        write_bytes(elf_path, layout.values, values)
    header = make_header(layout_id, dataset_id, values)
    write_bytes(elf_path, layout.segment, header)
    segment = Elf32(elf_path).read(layout.segment, HEADER_SIZE + layout.length)
    if check(segment, layout_id, layout.length) != "VALID":
        raise CalsegError(f"{elf_path.name}: sealed segment does not pass the start-up check "
                          f"({check(segment, layout_id, layout.length)})")
    return layout, segment


def dataset_values(target: Path, variant: str | None = None) -> dict:
    """calgen.values_of(): resolved and checked values of a calibration dataset (base or a variant)."""
    import calgen
    import caldata
    try:
        software = calgen.open_target(target)
        name = variant or caldata.BASE
        return calgen.values_of(software, name)
    except caldata.CalDataError as exc:
        raise CalsegError(f"calibration dataset of {target}: {exc}") from exc
    except calgen.CalgenError as exc:
        details = "; ".join(str(f) for f in getattr(exc, "findings", []) or [])
        raise CalsegError(f"calibration dataset of {target}: {exc}" + (f": {details}" if details else "")) from exc


def segment_image(elf_path: Path, rte_config: Path, dataset: dict | None = None) -> tuple[int, bytes]:
    """(address, bytes) of the sealed segment of the image; with the values of a calibration dataset (the result of
    calgen.values_of / `calgen values --json`) their stored values replace those of the build."""
    try:
        elf = Elf32(elf_path)
        layout = layout_of(elf)
        segment = elf.read(layout.segment, HEADER_SIZE + layout.length)
    except ElfError as exc:
        raise CalsegError(str(exc)) from exc
    header = read_header(segment)
    if check(segment, header.layout, layout.length) != "VALID":
        raise CalsegError(f"{elf_path.name}: the segment is not sealed ({check(segment, header.layout, layout.length)}"
                          "), run calseg seal first")
    if dataset is None:
        return layout.segment, segment
    layout_id = hex_id(dataset.get("layout"), "dataset: layout")
    if layout_id != header.layout:
        raise CalsegError(f"dataset: bound to layout {layout_id:08x}, the image has {header.layout:08x}")
    number = hex_id(dataset.get("values_id"), "dataset: values_id")
    components = dataset.get("components")
    if not isinstance(components, dict):
        raise CalsegError("dataset: components missing (output of calgen values --json expected)")
    given = {}
    for component in components.values():
        for entry in (component.values() if isinstance(component, dict) else []):
            if isinstance(entry, dict) and "name" in entry:
                given[entry["name"]] = entry.get("stored")
    parameters = parameters_of(elf_path, rte_config)
    names = {p.name for p in parameters}
    unknown = sorted(set(given) - names)
    if unknown:
        raise CalsegError(f"dataset: {', '.join(unknown)} is no parameter of the image")
    missing = sorted(names - set(given))
    if missing:
        raise CalsegError(f"dataset: incomplete, {', '.join(missing)} missing (a dataset names every parameter)")
    values = bytearray(segment[HEADER_SIZE:])
    order = "<" if elf.little_endian else ">"
    for p in parameters:
        stored = given[p.name]
        if not isinstance(stored, list) or len(stored) != p.count:
            raise CalsegError(f"dataset: {p.name} needs a list of {p.count} stored value(s)")
        _, size, low, high = a2lgen.TYPES[p.type]
        at = layout.offset(p.address)
        for i, value in enumerate(stored):
            integer = p.type not in ("FLOAT32", "FLOAT64")
            if isinstance(value, float) and integer and value.is_integer():
                value = int(value)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or (integer and not isinstance(value, int)):
                raise CalsegError(f"dataset: {p.name}[{i}] = {value!r} is no stored value of {p.type}")
            if not low <= value <= high:
                raise CalsegError(f"dataset: {p.name}[{i}] = {value} outside {p.type}")
            struct.pack_into(order + PACK[p.type], values, at + i * size, value)
    return layout.segment, make_header(header.layout, number, bytes(values)) + bytes(values)


def write_hex(address: int, data: bytes, path: Path) -> None:
    from intelhex import IntelHex
    ih = IntelHex()
    ih.frombytes(data, offset=address)
    path.parent.mkdir(parents=True, exist_ok=True)
    ih.write_hex_file(str(path))


def read_hex(path: Path, address: int, size: int) -> bytes:
    from intelhex import IntelHex
    ih = IntelHex(str(path))
    ih.padding = 0xFF
    return ih.tobinstr(start=address, size=size)


def parse_flip(text: str) -> tuple[int, int]:
    """OFFSET:BIT of --flip (offset from the start of the segment, decimal or 0x hex; bit 0..7)."""
    try:
        offset, bit = (int(part, 0) for part in text.split(":"))
    except ValueError:
        raise CalsegError(f"--flip {text!r}: expected OFFSET:BIT, e.g. 0x9:2") from None
    if not 0 <= bit <= 7 or offset < 0:
        raise CalsegError(f"--flip {text!r}: bit 0..7 and an offset >= 0")
    return offset, bit


def merge(elf_path: Path, image_hex: Path, output: Path, segment_hex: Path | None = None,
          flips: list[tuple[int, int]] | None = None) -> tuple[str, bytes]:
    """SYS-91: the download image of a build (app.ecu.hex) with its calibration segment replaced by a segment hex
    (another dataset, `calseg hex --dataset`) and/or single bits flipped (a damaged segment, for the board test of
    the start-up check). Only the bytes of the segment change: the ROM check list of the build does not cover them
    (romCheckImage.py, area with own reference), and canflash computes the validity record over what it downloads.
    Returns the status the start-up check of the application gives and the segment bytes."""
    from intelhex import IntelHex
    try:
        elf = Elf32(elf_path)
        layout = layout_of(elf)
        size = HEADER_SIZE + layout.length
        expected = read_header(elf.read(layout.segment, HEADER_SIZE)).layout    # what the image was sealed for
    except ElfError as exc:
        raise CalsegError(str(exc)) from exc
    ih = IntelHex(str(image_hex))
    ih.padding = 0xFF
    if not any(layout.segment <= a < layout.segment + size for a in ih.addresses()):
        raise CalsegError(f"{image_hex.name}: no calibration segment at 0x{layout.segment:08X} - not the download "
                          f"image of {elf_path.name}")
    segment = bytearray(read_hex(segment_hex, layout.segment, size) if segment_hex is not None
                        else ih.tobinstr(start=layout.segment, size=size))
    for offset, bit in flips or []:
        if offset >= size:
            raise CalsegError(f"--flip {offset}:{bit}: the segment has {size} bytes (header {HEADER_SIZE})")
        segment[offset] ^= 1 << bit
    for i, value in enumerate(segment):
        ih[layout.segment + i] = value
    output.parent.mkdir(parents=True, exist_ok=True)
    ih.write_hex_file(str(output))
    return check(bytes(segment), expected, layout.length), bytes(segment)


def describe(segment: bytes, layout: Layout | None = None) -> str:
    h = read_header(segment)
    where = "" if layout is None else (f" at 0x{layout.segment:08X}, "
                                       + ("production (values read from flash)" if layout.production
                                          else f"working page 0x{layout.ram:08X}"))
    return (f"layout {h.layout:08x}, dataset {h.dataset:08x}, {h.length} bytes, CRC 0x{h.values_crc:08X}{where}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_seal = sub.add_parser("seal", help="write values and header of the segment into the ELF")
    p_seal.add_argument("--elf", type=Path, required=True)
    p_seal.add_argument("--description", type=Path, required=True, help="RteCalibration.json (tools/rtegen)")
    p_seal.add_argument("--rte-config", type=Path, required=True, help="RteConfig.h (tools/rtegen)")
    p_hex = sub.add_parser("hex", help="the segment alone as Intel HEX")
    p_hex.add_argument("--elf", type=Path, required=True)
    p_hex.add_argument("--rte-config", type=Path, required=True)
    p_hex.add_argument("--dataset", type=Path, help="project.yaml or rte.yaml whose calibration dataset to take "
                                                    "(tools/calgen)")
    p_hex.add_argument("--variant", help="variant of the dataset (default: base)")
    p_hex.add_argument("--values", type=Path, help="file written by calgen values --json")
    p_hex.add_argument("-o", "--output", type=Path, required=True)
    p_merge = sub.add_parser("merge", help="SYS-91: download image with another or a damaged segment (test aid)")
    p_merge.add_argument("--elf", type=Path, required=True)
    p_merge.add_argument("--image", type=Path, required=True, help="app.ecu.hex of the build")
    p_merge.add_argument("--segment", type=Path, help="segment hex to put in (default: the one of the image)")
    p_merge.add_argument("--flip", action="append", default=[], metavar="OFFSET:BIT",
                         help="flip one bit, offset from the start of the segment (header: 0..31); repeatable")
    p_merge.add_argument("-o", "--output", type=Path, required=True)
    p_show = sub.add_parser("show", help="header and check of the segment")
    p_show.add_argument("--elf", type=Path, required=True)
    p_show.add_argument("--hex", type=Path, help="segment hex to check against the image instead of the ELF")
    args = parser.parse_args(argv)
    try:
        if args.command == "seal":
            layout, segment = seal(args.elf, args.description, args.rte_config)
            print(f"calseg: {args.elf.name}: sealed, {describe(segment, layout)}")
        elif args.command == "hex":
            dataset = None
            if args.dataset is not None and args.values is not None:
                raise CalsegError("give either --dataset or --values")
            if args.dataset is not None:
                dataset = dataset_values(args.dataset, args.variant)
            elif args.values is not None:
                try:
                    dataset = json.loads(args.values.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    raise CalsegError(f"{args.values}: {exc}") from exc
            address, data = segment_image(args.elf, args.rte_config, dataset)
            write_hex(address, data, args.output)
            print(f"calseg: {args.output.name}: 0x{address:08X}..0x{address + len(data) - 1:08X}, {describe(data)}")
        elif args.command == "merge":
            status, data = merge(args.elf, args.image, args.output, args.segment,
                                 [parse_flip(f) for f in args.flip])
            header = describe(data) if status != "MAGIC" else "no CALV header"
            print(f"calseg: {args.output.name}: segment {status} for the start-up check, {header}")
        else:
            elf = Elf32(args.elf)
            layout = layout_of(elf)
            segment = elf.read(layout.segment, HEADER_SIZE + layout.length) if args.hex is None else \
                read_hex(args.hex, layout.segment, HEADER_SIZE + layout.length)
            expected = read_header(elf.read(layout.segment, HEADER_SIZE)).layout     # what the image was sealed for
            status = check(segment, expected, layout.length)
            print(f"calseg: {status}, {describe(segment, layout)}")
            return 0 if status == "VALID" else 1
        return 0
    except (CalsegError, ElfError) as error:
        print(f"calseg: error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
