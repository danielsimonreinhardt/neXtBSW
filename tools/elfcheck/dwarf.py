"""Minimal DWARF reader for the timing check (PLAT-82): enumerators and macro definitions of a linked image.

Standard library only and independent of binutils and pyelftools, like elffile.py. It reads just enough of the
debugging information (DWARF 2 to 5, "DWARF Debugging Information Format" version 5, sections 7.5 and 6.3) to answer
two questions about the image as it was compiled:

- `enumerations(img)`: the enumerators of every enumeration type at file or namespace scope, per compilation unit
  (for the task contexts `TASK_<NAME>` of the generated async/Config.h, which only exist as enumerators);
- `macros(img)`: every `#define` recorded in `.debug_macro` (GCC with -g3), name -> set of definitions (for the
  interrupt ranks, the RTOS syscall level, the NVIC priority bits and the OS tick the image was compiled with).

Scope on purpose: DIEs below functions, classes and other non-namespace scopes are skipped with DW_AT_sibling where
the compiler gives one; only DW_TAG_compile_unit, DW_TAG_partial_unit and DW_TAG_namespace are descended into.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass

try:
    from . import elffile
except ImportError:                     # pragma: no cover - run as a script
    import elffile  # type: ignore[no-redef]

TAG_ENUMERATION_TYPE = 0x04
TAG_ENUMERATOR = 0x28
TAG_COMPILE_UNIT = 0x11
TAG_PARTIAL_UNIT = 0x3C
TAG_NAMESPACE = 0x39
AT_SIBLING = 0x01
AT_NAME = 0x03
AT_CONST_VALUE = 0x1C
AT_STR_OFFSETS_BASE = 0x72

_DESCEND = {TAG_COMPILE_UNIT, TAG_PARTIAL_UNIT, TAG_NAMESPACE}


class DwarfError(ValueError):
    """Debugging information this reader cannot decode (the message names the section and offset)."""


def _uleb(data: bytes, pos: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return value, pos


def _sleb(data: bytes, pos: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            if byte & 0x40:
                value -= 1 << shift
            return value, pos


def _cstr(data: bytes, pos: int) -> tuple[str, int]:
    end = data.find(b"\0", pos)
    if end < 0:
        raise DwarfError(f"unterminated string at offset {pos}")
    return data[pos:end].decode("utf-8", "replace"), end + 1


def _section(img: elffile.ElfImage, name: str) -> bytes:
    sec = img.section(name)
    return b"" if sec is None else img.section_bytes(sec)


# ---------------------------------------------------------------------- .debug_abbrev

def _abbrevs(data: bytes, offset: int) -> dict[int, tuple[int, bool, list[tuple[int, int, int]]]]:
    """Abbreviation table at `offset`: code -> (tag, has children, [(attribute, form, implicit const)])."""
    table = {}
    pos = offset
    while True:
        code, pos = _uleb(data, pos)
        if code == 0:
            return table
        tag, pos = _uleb(data, pos)
        children = data[pos] == 1
        pos += 1
        specs = []
        while True:
            attr, pos = _uleb(data, pos)
            form, pos = _uleb(data, pos)
            implicit = 0
            if form == 0x21:                    # DW_FORM_implicit_const: the value is in the abbreviation
                implicit, pos = _sleb(data, pos)
            if attr == 0 and form == 0:
                break
            specs.append((attr, form, implicit))
        table[code] = (tag, children, specs)


# ---------------------------------------------------------------------- .debug_info

@dataclass(frozen=True)
class Enumeration:
    unit: int                           # offset of the compilation unit in .debug_info
    values: dict[str, int]              # enumerator name -> value, in declaration order


class _Unit:
    def __init__(self, info: bytes, strs: bytes, line_strs: bytes, str_offsets: bytes, offset_size: int,
                 address_size: int, version: int, start: int):
        self.info, self.strs, self.line_strs, self.str_offsets = info, strs, line_strs, str_offsets
        self.osz, self.asz, self.version, self.start = offset_size, address_size, version, start
        self.str_offsets_base: int | None = None

    def string(self, form: int, raw) -> str | None:
        if form == 0x08:
            return raw
        if form == 0x0E:
            return _cstr(self.strs, raw)[0]
        if form == 0x1F:
            return _cstr(self.line_strs, raw)[0]
        if form in (0x1A, 0x25, 0x26, 0x27, 0x28):          # strx: index into .debug_str_offsets
            if self.str_offsets_base is None:
                return None
            at = self.str_offsets_base + raw * self.osz
            off = struct.unpack_from("<Q" if self.osz == 8 else "<I", self.str_offsets, at)[0]
            return _cstr(self.strs, off)[0]
        return None

    def attribute(self, form: int, pos: int, implicit: int):
        """(value, next position) of one attribute value; strings stay raw (offset or index) until string()."""
        d = self.info
        if form == 0x0B or form == 0x11 or form == 0x0C or form == 0x25 or form == 0x29:     # 1 byte
            return d[pos], pos + 1
        if form == 0x05 or form == 0x12 or form == 0x26 or form == 0x2A:                   # 2 bytes
            return struct.unpack_from("<H", d, pos)[0], pos + 2
        if form == 0x27 or form == 0x2B:                                                     # 3 bytes
            return d[pos] | d[pos + 1] << 8 | d[pos + 2] << 16, pos + 3
        if form == 0x06 or form == 0x13 or form == 0x1C or form == 0x28 or form == 0x2C:     # 4 bytes
            return struct.unpack_from("<I", d, pos)[0], pos + 4
        if form == 0x07 or form == 0x14 or form == 0x20 or form == 0x24:                   # 8 bytes
            return struct.unpack_from("<Q", d, pos)[0], pos + 8
        if form in (0x0E, 0x17, 0x1D, 0x1F) or (form == 0x10 and self.version >= 3):      # offset size
            fmt = "<Q" if self.osz == 8 else "<I"
            return struct.unpack_from(fmt, d, pos)[0], pos + self.osz
        if form == 0x01 or (form == 0x10 and self.version < 3):                            # address size
            fmt = {1: "<B", 2: "<H", 4: "<I", 8: "<Q"}[self.asz]
            return struct.unpack_from(fmt, d, pos)[0], pos + self.asz
        if form in (0x0F, 0x15, 0x1A, 0x1B, 0x22, 0x23):                                   # ULEB128
            return _uleb(d, pos)
        if form == 0x0D:
            return _sleb(d, pos)
        if form == 0x08:
            return _cstr(d, pos)
        if form == 0x19:                                                                     # flag_present
            return 1, pos
        if form == 0x21:                                                                     # implicit_const
            return implicit, pos
        if form == 0x1E:
            return None, pos + 16
        if form in (0x09, 0x18):                                                             # block, exprloc
            size, pos = _uleb(d, pos)
            return None, pos + size
        if form == 0x0A:
            return None, pos + 1 + d[pos]
        if form == 0x03:
            return None, pos + 2 + struct.unpack_from("<H", d, pos)[0]
        if form == 0x04:
            return None, pos + 4 + struct.unpack_from("<I", d, pos)[0]
        if form == 0x16:                                                                     # indirect
            real, pos = _uleb(d, pos)
            return self.attribute(real, pos, implicit)
        raise DwarfError(f".debug_info: unknown attribute form 0x{form:02x} at offset {pos}")


def _enumerations_uncached(img: elffile.ElfImage) -> list[Enumeration]:
    info = _section(img, ".debug_info")
    abbrev = _section(img, ".debug_abbrev")
    strs, line_strs = _section(img, ".debug_str"), _section(img, ".debug_line_str")
    str_offsets = _section(img, ".debug_str_offsets")
    result: list[Enumeration] = []
    tables: dict[int, dict] = {}
    pos = 0
    while pos + 11 <= len(info):
        start = pos
        length = struct.unpack_from("<I", info, pos)[0]
        osz = 4
        pos += 4
        if length == 0xFFFFFFFF:
            length = struct.unpack_from("<Q", info, pos)[0]
            osz = 8
            pos += 8
        end = pos + length
        version = struct.unpack_from("<H", info, pos)[0]
        pos += 2
        if version >= 5:
            unit_type, asz = info[pos], info[pos + 1]
            pos += 2
            abbrev_off = struct.unpack_from("<Q" if osz == 8 else "<I", info, pos)[0]
            pos += osz
            if unit_type in (4, 5):             # skeleton, split compile: dwo id
                pos += 8
            elif unit_type in (2, 6):           # type units: signature and type offset
                pos += 8 + osz
        elif version >= 2:
            abbrev_off = struct.unpack_from("<Q" if osz == 8 else "<I", info, pos)[0]
            pos += osz
            asz = info[pos]
            pos += 1
        else:
            raise DwarfError(f".debug_info: unit at offset {start} has version {version}")
        if abbrev_off not in tables:
            tables[abbrev_off] = _abbrevs(abbrev, abbrev_off)
        table = tables[abbrev_off]
        unit = _Unit(info, strs, line_strs, str_offsets, osz, asz, version, start)
        # stack of (tag, collected enumerators or None) per open DIE with children
        stack: list[tuple[int, dict | None]] = []
        while pos < end:
            code, pos = _uleb(info, pos)
            if code == 0:
                if stack:
                    tag, values = stack.pop()
                    if values:
                        result.append(Enumeration(start, values))
                continue
            try:
                tag, children, specs = table[code]
            except KeyError:
                raise DwarfError(f".debug_info: unknown abbreviation {code} at offset {pos}") from None
            name = const = sibling = None
            name_form = 0
            for attr, form, implicit in specs:
                value, pos = unit.attribute(form, pos, implicit)
                if attr == AT_NAME:
                    name, name_form = value, form
                elif attr == AT_CONST_VALUE:
                    const = value
                elif attr == AT_SIBLING:
                    sibling = value
                elif attr == AT_STR_OFFSETS_BASE:
                    unit.str_offsets_base = value
            parent = stack[-1] if stack else None
            if tag == TAG_ENUMERATOR and parent is not None and parent[1] is not None and const is not None:
                text = unit.string(name_form, name)
                if text is not None:
                    parent[1][text] = const
            if not children:
                continue
            in_scope = parent is None or parent[0] in _DESCEND
            if tag == TAG_ENUMERATION_TYPE and in_scope:
                stack.append((tag, {}))
            elif tag in _DESCEND and in_scope:
                stack.append((tag, None))
            elif sibling is not None and 0 < sibling and start + sibling > pos:
                pos = start + sibling       # skip the children (function bodies, class members)
            else:
                stack.append((tag, None))
        pos = end
    return result


_CACHE: dict[bytes, list[Enumeration]] = {}


def enumerations(img: elffile.ElfImage) -> list[Enumeration]:
    """Enumeration types at file or namespace scope of every unit (empty without .debug_info)."""
    key = hashlib.sha256(_section(img, ".debug_info") + b"|" + _section(img, ".debug_abbrev")
                         + b"|" + _section(img, ".debug_str")).digest()
    if key not in _CACHE:
        _CACHE[key] = _enumerations_uncached(img)
    return _CACHE[key]


# ---------------------------------------------------------------------- .debug_macro

def macros(img: elffile.ElfImage) -> dict[str, set[str]]:
    """Every macro definition of `.debug_macro`: name -> set of replacement texts ("" for an empty one).

    Function-like macros are keyed by their name without the parameter list. Empty without the section (no -g3).
    """
    data = _section(img, ".debug_macro")
    strs = _section(img, ".debug_str")
    result: dict[str, set[str]] = {}

    def define(text: str) -> None:
        head, _, body = text.partition(" ")
        name = head.split("(", 1)[0]
        if "(" in head and not head.endswith(")"):         # parameter list with blanks: "F(a, b) body"
            rest = text[text.index("(") :]
            body = rest[rest.index(")") + 1 :]
        result.setdefault(name, set()).add(body.strip())

    pos = 0
    while pos + 3 <= len(data):
        start = pos
        version = struct.unpack_from("<H", data, pos)[0]
        flags = data[pos + 2]
        pos += 3
        if version not in (4, 5):
            raise DwarfError(f".debug_macro: unit at offset {start} has version {version}")
        osz = 8 if flags & 1 else 4
        if flags & 2:
            pos += osz                              # debug_line_offset
        operands: dict[int, list[int]] = {}
        if flags & 4:
            count = data[pos]
            pos += 1
            for _ in range(count):
                opcode = data[pos]
                n, pos = _uleb(data, pos + 1)
                operands[opcode] = list(data[pos:pos + n])
                pos += n
        fmt = "<Q" if osz == 8 else "<I"
        while True:
            op = data[pos]
            pos += 1
            if op == 0:
                break
            if op in (0x01, 0x02):                  # define, undef: line, string
                _, pos = _uleb(data, pos)
                text, pos = _cstr(data, pos)
                if op == 0x01:
                    define(text)
            elif op in (0x05, 0x06):                # define_strp, undef_strp: line, offset into .debug_str
                _, pos = _uleb(data, pos)
                off = struct.unpack_from(fmt, data, pos)[0]
                pos += osz
                if op == 0x05:
                    define(_cstr(strs, off)[0])
            elif op == 0x03:                        # start_file: line, file
                _, pos = _uleb(data, pos)
                _, pos = _uleb(data, pos)
            elif op == 0x04:                        # end_file
                pass
            elif op in (0x07, 0x0A):                # import, import_sup: offset
                pos += osz
            elif op in (0x08, 0x09):                # define_sup, undef_sup: line, offset (other file)
                _, pos = _uleb(data, pos)
                pos += osz
            elif op in (0x0B, 0x0C):                # define_strx, undef_strx: line, index (no str_offsets here)
                _, pos = _uleb(data, pos)
                _, pos = _uleb(data, pos)
            elif op in operands:                    # vendor opcode described in the header
                for form in operands[op]:
                    if form in (0x0F, 0x15, 0x1A, 0x1B):
                        _, pos = _uleb(data, pos)
                    elif form == 0x0D:
                        _, pos = _sleb(data, pos)
                    elif form == 0x08:
                        _, pos = _cstr(data, pos)
                    elif form in (0x0E, 0x17, 0x1F):
                        pos += osz
                    elif form in (0x0B, 0x05, 0x06, 0x07):
                        pos += {0x0B: 1, 0x05: 2, 0x06: 4, 0x07: 8}[form]
                    else:
                        raise DwarfError(f".debug_macro: operand form 0x{form:02x} of opcode 0x{op:02x}")
            else:
                raise DwarfError(f".debug_macro: unknown opcode 0x{op:02x} at offset {pos - 1}")
    return result
