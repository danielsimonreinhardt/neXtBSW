"""Minimal reader for 32 bit ELF files (executables of the ARM targets): symbols and initialised memory.

No dependency beyond the standard library. Little and big endian are both read (the byte order comes from the
file), so the same code serves a Cortex-M/Cortex-R image and the test images of the host tests.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

SHT_SYMTAB = 2
SHT_NOBITS = 8
SHF_ALLOC = 0x2


class ElfError(Exception):
    """Error with the path of the file in the message."""


@dataclass(frozen=True)
class Symbol:
    name: str
    value: int
    size: int
    kind: int      # STT_*: 1 object, 2 function
    binding: int   # STB_*: 0 local, 1 global, 2 weak
    section: int


@dataclass(frozen=True)
class Section:
    name: str
    kind: int
    flags: int
    address: int
    offset: int
    size: int


class Elf32:
    """An ELF32 file in memory: `symbols` by name, `read(address, n)` for initialised memory."""

    def __init__(self, path: Path):
        self.path = Path(path)
        try:
            self.data = self.path.read_bytes()
        except OSError as exc:
            raise ElfError(f"{self.path}: cannot read ({exc.strerror})") from exc
        ident = self.data[:16]
        if len(ident) < 16 or ident[:4] != b"\x7fELF":
            raise ElfError(f"{self.path}: not an ELF file")
        if ident[4] != 1:
            raise ElfError(f"{self.path}: ELF class {ident[4]}, only 32 bit (ELFCLASS32) images are supported")
        if ident[5] not in (1, 2):
            raise ElfError(f"{self.path}: unknown byte order (EI_DATA {ident[5]})")
        self.little_endian = ident[5] == 1
        self.order = "<" if self.little_endian else ">"
        (_type, self.machine, _version, _entry, _phoff, shoff, _flags, _ehsize, _phentsize, _phnum,
         shentsize, shnum, shstrndx) = struct.unpack_from(self.order + "HHIIIIIHHHHHH", self.data, 16)
        if shoff == 0 or shnum == 0:
            raise ElfError(f"{self.path}: no section headers (stripped image?)")
        raw = []
        for i in range(shnum):
            raw.append(struct.unpack_from(self.order + "IIIIIIIIII", self.data, shoff + i * shentsize))
        names = raw[shstrndx]
        self.sections: list[Section] = []
        for (name, kind, flags, address, offset, size, *_rest) in raw:
            self.sections.append(Section(self._string(names[4], name), kind, flags, address, offset, size))
        self.symbols: dict[str, Symbol] = {}
        for index, (_n, kind, _f, _a, offset, size, link, _i, _al, entsize) in enumerate(raw):
            if kind != SHT_SYMTAB:
                continue
            strtab = raw[link][4]
            for pos in range(offset, offset + size, entsize or 16):
                st_name, value, st_size, info, _other, shndx = struct.unpack_from(
                    self.order + "IIIBBH", self.data, pos)
                name = self._string(strtab, st_name)
                # a global symbol wins over a local one of the same name
                if name and (name not in self.symbols or (info >> 4) == 1):
                    self.symbols[name] = Symbol(name, value, st_size, info & 0xF, info >> 4, shndx)
        if not self.symbols:
            raise ElfError(f"{self.path}: no symbol table (stripped image?)")

    def _string(self, table_offset: int, index: int) -> str:
        start = table_offset + index
        end = self.data.find(b"\0", start)
        return self.data[start:end].decode("utf-8", errors="replace")

    def symbol(self, name: str, what: str) -> Symbol:
        try:
            return self.symbols[name]
        except KeyError:
            raise ElfError(f"{self.path}: symbol {name} ({what}) not found") from None

    def read(self, address: int, n: int) -> bytes:
        """n bytes of initialised memory (a section with file contents) at address."""
        for s in self.sections:
            if s.flags & SHF_ALLOC and s.kind != SHT_NOBITS and s.address <= address \
                    and address + n <= s.address + s.size:
                start = s.offset + address - s.address
                return self.data[start:start + n]
        raise ElfError(f"{self.path}: no initialised section contains 0x{address:08X}..0x{address + n - 1:08X}")

    def u8(self, address: int) -> int:
        return self.read(address, 1)[0]

    def u16(self, address: int) -> int:
        return struct.unpack(self.order + "H", self.read(address, 2))[0]

    def u32(self, address: int) -> int:
        return struct.unpack(self.order + "I", self.read(address, 4))[0]

    def i32(self, address: int) -> int:
        return struct.unpack(self.order + "i", self.read(address, 4))[0]

    def cstring(self, address: int, limit: int = 256) -> str:
        """NUL terminated string at address (at most `limit` bytes)."""
        for s in self.sections:
            if s.flags & SHF_ALLOC and s.kind != SHT_NOBITS and s.address <= address < s.address + s.size:
                start = s.offset + address - s.address
                end = self.data.find(b"\0", start, min(start + limit, s.offset + s.size))
                if end < 0:
                    raise ElfError(f"{self.path}: string at 0x{address:08X} is not terminated")
                return self.data[start:end].decode("utf-8", errors="replace")
        raise ElfError(f"{self.path}: no initialised section contains the string at 0x{address:08X}")
