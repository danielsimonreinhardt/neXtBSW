"""Small reader for linked ARM ELF32 images (PLAT-80): sections, program headers, symbols, build attributes.

Standard library only and on purpose independent of pyelftools and binutils, so that the image check does not
share a reader with any tool of the build. Only what the check needs: ELF32, little endian, EM_ARM, and the
`aeabi` subsection of `.ARM.attributes` (ARM IHI 0044 "ELF for the Arm Architecture" and IHI 0045 "Addenda to the
ABI for the Arm Architecture", section "Build attributes").

The load address (LMA) of a section is the address objcopy and the hex file use: the physical address of the
PT_LOAD segment that holds the section plus the offset of the section inside the segment.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

EM_ARM = 40
ET_EXEC = 2
PT_LOAD = 1
SHT_PROGBITS = 1
SHT_SYMTAB = 2
SHT_NOBITS = 8
SHT_ARM_ATTRIBUTES = 0x70000003
SHF_WRITE = 0x1
SHF_ALLOC = 0x2
SHF_EXECINSTR = 0x4

# Build attribute tags whose value is a NUL-terminated string (IHI 0045): 4, 5, 67 and, above 32, every odd tag;
# 32 (Tag_compatibility) is a ULEB128 followed by a string.
_STRING_TAGS = {4, 5, 65, 67}

TAG_CPU_NAME = 5
TAG_CPU_ARCH = 6
TAG_CPU_ARCH_PROFILE = 7
TAG_THUMB_ISA_USE = 9
TAG_FP_ARCH = 10
TAG_ABI_HARDFP_USE = 27
TAG_ABI_VFP_ARGS = 28


class ElfError(ValueError):
    """The file is not an image this reader handles (the message names the file)."""


@dataclass(frozen=True)
class Section:
    index: int
    name: str
    type: int
    flags: int
    addr: int
    offset: int
    size: int
    header_offset: int          # file offset of the section header (for tests that build mutants)

    @property
    def alloc(self) -> bool:
        return bool(self.flags & SHF_ALLOC)

    @property
    def has_content(self) -> bool:
        """Occupies bytes in the image (flash): allocated, not NOBITS, not empty."""
        return self.alloc and self.type != SHT_NOBITS and self.size > 0


@dataclass(frozen=True)
class Segment:
    index: int
    type: int
    offset: int
    vaddr: int
    paddr: int
    filesz: int
    memsz: int
    flags: int
    header_offset: int


@dataclass(frozen=True)
class Symbol:
    name: str
    value: int
    size: int
    type: int
    bind: int
    shndx: int


@dataclass(frozen=True)
class Attribute:
    tag: int
    value: int | str
    offset: int                 # file offset of the value (for tests that build mutants)


@dataclass
class ElfImage:
    path: Path
    data: bytes
    entry: int
    sections: list[Section] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    attributes: dict[int, Attribute] = field(default_factory=dict)

    # ------------------------------------------------------------------ lookup

    def section(self, name: str) -> Section | None:
        return next((s for s in self.sections if s.name == name), None)

    def symbol(self, name: str) -> Symbol | None:
        """A defined symbol of that name (the global one if there are several)."""
        found = [s for s in self.symbols if s.name == name and s.shndx != 0]
        found.sort(key=lambda s: s.bind != 1)
        return found[0] if found else None

    def attribute(self, tag: int) -> int | str | None:
        entry = self.attributes.get(tag)
        return None if entry is None else entry.value

    def segment_of(self, section: Section) -> Segment | None:
        """PT_LOAD segment holding the section: by file offset for sections with content, by address otherwise."""
        for seg in self.segments:
            if seg.type != PT_LOAD:
                continue
            if section.type != SHT_NOBITS:
                if seg.filesz and seg.offset <= section.offset and section.offset + section.size <= seg.offset + seg.filesz \
                        and seg.vaddr <= section.addr < seg.vaddr + max(seg.memsz, 1):
                    return seg
            elif seg.vaddr <= section.addr and section.addr + section.size <= seg.vaddr + seg.memsz:
                return seg
        return None

    def lma(self, section: Section) -> int:
        seg = self.segment_of(section)
        return section.addr if seg is None else seg.paddr + (section.addr - seg.vaddr)

    def section_bytes(self, section: Section) -> bytes:
        if section.type == SHT_NOBITS:
            return b""
        return self.data[section.offset:section.offset + section.size]

    def read(self, address: int, size: int) -> bytes | None:
        """Bytes at a run-time address (VMA) from the section holding it, None if no section holds all of them."""
        for sec in self.sections:
            if sec.has_content and sec.addr <= address and address + size <= sec.addr + sec.size:
                start = sec.offset + address - sec.addr
                return self.data[start:start + size]
        return None

    def flash_bytes(self) -> dict[int, int]:
        """Image content by load address, as objcopy writes the hex file: every allocated section with content."""
        image: dict[int, int] = {}
        for sec in self.sections:
            if sec.has_content:
                base = self.lma(sec)
                for i, b in enumerate(self.section_bytes(sec)):
                    image[base + i] = b
        return image


# ---------------------------------------------------------------------- parsing

def _uleb(data: bytes, pos: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        if pos >= len(data):
            raise ElfError("truncated ULEB128 in .ARM.attributes")
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return value, pos


def _cstr(data: bytes, pos: int) -> tuple[str, int]:
    end = data.find(b"\0", pos)
    if end < 0:
        raise ElfError("unterminated string in .ARM.attributes")
    return data[pos:end].decode("ascii", "replace"), end + 1


def parse_attributes(data: bytes, base: int = 0) -> dict[int, Attribute]:
    """File-scope attributes (Tag_File) of the `aeabi` vendor subsection; `base` is the file offset of `data`."""
    if not data or data[0] != ord("A"):
        raise ElfError("the .ARM.attributes section does not start with format version 'A'")
    result: dict[int, Attribute] = {}
    pos = 1
    while pos + 4 <= len(data):
        length = struct.unpack_from("<I", data, pos)[0]
        end = pos + length
        if length < 5 or end > len(data):
            raise ElfError("bad subsection length in .ARM.attributes")
        vendor, sub = _cstr(data, pos + 4)
        if vendor == "aeabi":
            while sub < end:
                tag = data[sub]
                size = struct.unpack_from("<I", data, sub + 1)[0]
                sub_end = sub + size
                if size < 5 or sub_end > end:
                    raise ElfError("bad attribute block length in .ARM.attributes")
                p = sub + 5
                if tag == 1:                        # Tag_File: the whole image
                    while p < sub_end:
                        attr, p = _uleb(data, p)
                        at = p
                        if attr == 32:              # Tag_compatibility: flag, then vendor name
                            flag, p = _uleb(data, p)
                            _, p = _cstr(data, p)
                            value: int | str = flag
                        elif attr in _STRING_TAGS or (attr > 32 and attr % 2 == 1):
                            value, p = _cstr(data, p)
                        else:
                            value, p = _uleb(data, p)
                        result[attr] = Attribute(attr, value, base + at)
                sub = sub_end
        pos = end
    return result


def load(path: str | Path) -> ElfImage:
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as e:
        raise ElfError(f"{path}: cannot read ({e.strerror})") from None
    if data[:4] != b"\x7fELF":
        raise ElfError(f"{path}: not an ELF file")
    if data[4] != 1:
        raise ElfError(f"{path}: not ELF32 (class {data[4]}) - the derivatives are 32 bit (D11)")
    if data[5] != 1:
        raise ElfError(f"{path}: not little endian (D11)")
    (e_type, e_machine, _, e_entry, e_phoff, e_shoff, _, _, e_phentsize, e_phnum, e_shentsize, e_shnum,
     e_shstrndx) = struct.unpack_from("<HHIIIIIHHHHHH", data, 16)
    if e_machine != EM_ARM:
        raise ElfError(f"{path}: machine {e_machine}, not ARM ({EM_ARM})")
    if e_type != ET_EXEC:
        raise ElfError(f"{path}: not a linked executable (e_type {e_type})")
    image = ElfImage(path, data, e_entry)

    raw = []
    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        raw.append((off, struct.unpack_from("<IIIIIIIIII", data, off)))
    names = raw[e_shstrndx][1] if e_shstrndx < len(raw) else None

    def name_at(table_offset: int, index: int) -> str:
        end = data.find(b"\0", table_offset + index)
        return data[table_offset + index:end].decode("ascii", "replace")

    for i, (off, (sh_name, sh_type, sh_flags, sh_addr, sh_offset, sh_size, *_)) in enumerate(raw):
        name = name_at(names[4], sh_name) if names else ""
        image.sections.append(Section(i, name, sh_type, sh_flags, sh_addr, sh_offset, sh_size, off))

    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        p_type, p_offset, p_vaddr, p_paddr, p_filesz, p_memsz, p_flags, _ = struct.unpack_from("<IIIIIIII", data, off)
        image.segments.append(Segment(i, p_type, p_offset, p_vaddr, p_paddr, p_filesz, p_memsz, p_flags, off))

    for sec in image.sections:
        if sec.type == SHT_SYMTAB:
            strtab = raw[struct.unpack_from("<I", data, sec.header_offset + 24)[0]][1]
            for pos in range(sec.offset, sec.offset + sec.size, 16):
                st_name, st_value, st_size, st_info, _, st_shndx = struct.unpack_from("<IIIBBH", data, pos)
                if st_name:
                    image.symbols.append(Symbol(name_at(strtab[4], st_name), st_value, st_size, st_info & 0xF,
                                                st_info >> 4, st_shndx))
        elif sec.type == SHT_ARM_ATTRIBUTES:
            image.attributes = parse_attributes(image.section_bytes(sec), sec.offset)
    return image
