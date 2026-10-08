"""Generates a vendor device header (peripheral access layer) from a CMSIS-SVD file (PLAT-11).

The output follows the style of the NXP S32K1xx headers used by OpenBSW (S32K148.h): `<MOD>_Type` register layouts
with reserved gaps, `IP_<instance>_BASE` / `IP_<instance>` / `IP_<MOD>_BASE_PTRS`, `<MOD>_INSTANCE_COUNT`,
`<MOD>_<REG>_COUNT`, field macros `_MASK/_SHIFT/_WIDTH/(x)`, `IRQn_Type`, core configuration (`__NVIC_PRIO_BITS`,
`__MPU_PRESENT`, ...) and PCC index macros. Everything is written into one file.

SVD files of the same vendor differ in how they describe arrays (flat C0SC..C7SC, `%s` dims, clusters) and in some
names (interrupts). A family rules file (`mcudb/tools/device_header_rules/<family>.yaml`) maps both to the header
names the drivers use; registers not covered by a rule are folded automatically when they form a plain numbered array
(PCR0..PCR31 -> PCR[32]) or emitted one by one. Anything ambiguous (overlapping registers without a rule, instances
of one module with conflicting layouts, two names for one interrupt) stops the generator instead of guessing.

    svd2header.py generate --derivative S32K116 [--svd FILE] --out DIR   # writes DIR/<derivative>.h
    svd2header.py generate --derivative S32K116 --out DIR --check        # exit 1 if missing/outdated/other SVD
    svd2header.py compare  --derivative S32K148 --svd FILE --reference S32K148.h   # layout/macro diff
    svd2header.py symbols  --header FILE                                # driver symbols vs. header

The SVD location is workstation level: `--svd`, else `$NX_SVD_DIR/<svd file from the rules>`. The rules file records
the SHA-256 of the SVD and of the generated header, so `--check` proves the same result on every workstation.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace
from pathlib import Path

MCUDB = Path(__file__).resolve().parents[1]
REPO = MCUDB.parent
GENERATOR = "mcudb/tools/svd2header.py"
OPENBSW_S32K148 = "external/openbsw/platforms/s32k1xx/bsp/bspMcu/include/3rdparty/nxp/S32K148.h"


class SvdError(Exception):
    """Invalid or ambiguous input (SVD or rules)."""


# ----------------------------------------------------------------------------------------------- SVD model


@dataclass(frozen=True)
class Field:
    name: str
    offset: int
    width: int
    description: str = ""


@dataclass(frozen=True)
class Register:
    name: str
    offset: int
    size: int                   # bits
    access: str                 # read-only, write-only, read-write
    fields: tuple[Field, ...]
    description: str = ""
    alternate: bool = False     # SVD alternateGroup/alternateRegister: shares its address with another register


@dataclass
class Peripheral:
    name: str
    base: int
    group: str | None
    description: str
    registers: list[Register]
    interrupts: list[tuple[str, int, str]]


@dataclass
class Device:
    name: str
    version: str
    cpu: dict
    license_text: str
    peripherals: list[Peripheral]


def _text(node, tag, default=None):
    child = node.find(tag)
    return default if child is None or child.text is None else " ".join(child.text.split())


def _int(value: str) -> int:
    value = value.strip().lower()
    if value.startswith("#"):
        raise SvdError(f"binary SVD number {value!r} not supported")
    return int(value, 0)


def _dim_indices(node) -> list[str] | None:
    dim = _text(node, "dim")
    if dim is None:
        return None
    count = _int(dim)
    index = _text(node, "dimIndex")
    if index is None:
        return [str(i) for i in range(count)]
    m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", index)
    if m:
        values = [str(i) for i in range(int(m.group(1)), int(m.group(2)) + 1)]
    elif re.fullmatch(r"[A-Z]\s*-\s*[A-Z]", index):
        values = [chr(c) for c in range(ord(index[0]), ord(index[-1]) + 1)]
    else:
        values = [v.strip() for v in index.split(",")]
    if len(values) != count:
        raise SvdError(f"{_text(node, 'name')}: dimIndex {index!r} has {len(values)} entries, dim is {count}")
    return values


def _dim_name(name: str, index: str) -> str:
    return name.replace("[%s]", index).replace("%s", index)


def _fields(reg_node, reg_name: str, size: int) -> tuple[Field, ...]:
    result = []
    fields = reg_node.find("fields")
    for f in ([] if fields is None else fields.findall("field")):
        name = _text(f, "name")
        if _text(f, "bitOffset") is not None:
            offset, width = _int(_text(f, "bitOffset")), _int(_text(f, "bitWidth"))
        elif _text(f, "lsb") is not None:
            offset, width = _int(_text(f, "lsb")), _int(_text(f, "msb")) - _int(_text(f, "lsb")) + 1
        elif _text(f, "bitRange") is not None:
            msb, lsb = (int(v) for v in re.fullmatch(r"\[(\d+):(\d+)\]", _text(f, "bitRange")).groups())
            offset, width = lsb, msb - lsb + 1
        else:
            raise SvdError(f"{reg_name}.{name}: field without bit position")
        if width < 1 or offset + width > size:
            raise SvdError(f"{reg_name}.{name}: bits {offset}+{width} outside the {size}-bit register")
        indices = _dim_indices(f)
        for idx in indices or [None]:
            fname = name if idx is None else _dim_name(name, idx)
            step = 0 if idx is None else (indices.index(idx) * _int(_text(f, "dimIncrement", "0")))
            result.append(Field(fname, offset + step, width, _text(f, "description", "")))
    return tuple(sorted(result, key=lambda x: x.offset))


def _registers(parent, defaults: dict, prefix: str = "", base: int = 0) -> list[Register]:
    out = []
    for node in parent:
        if node.tag not in ("register", "cluster"):
            continue
        name = _text(node, "name")
        offset = base + _int(_text(node, "addressOffset"))
        indices = _dim_indices(node)
        step = _int(_text(node, "dimIncrement", "0")) if indices else 0
        local = dict(defaults)
        if _text(node, "size") is not None:
            local["size"] = _int(_text(node, "size"))
        if _text(node, "access") is not None:
            local["access"] = _text(node, "access")
        for i, idx in enumerate(indices or [None]):
            full = prefix + (name if idx is None else _dim_name(name, idx))
            if node.tag == "cluster":
                out += _registers(node, local, full + "_", offset + i * step)
                continue
            size = local.get("size", 32)
            out.append(Register(full, offset + i * step, size, local.get("access", "read-write"),
                                _fields(node, full, size), _text(node, "description", ""),
                                _text(node, "alternateGroup") is not None
                                or _text(node, "alternateRegister") is not None))
    return out


def load_svd(path: Path) -> Device:
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        raise SvdError(f"{path.name}: {e}") from None
    if root.tag != "device":
        raise SvdError(f"{path.name}: not a CMSIS-SVD device file")
    cpu_node = root.find("cpu")
    cpu = {}
    if cpu_node is not None:
        for tag in ("name", "revision", "mpuPresent", "fpuPresent", "vtorPresent", "nvicPrioBits",
                    "vendorSystickConfig"):
            if _text(cpu_node, tag) is not None:
                cpu[tag] = _text(cpu_node, tag)
    dev_defaults = {k: v for k, v in (("size", _text(root, "size")), ("access", _text(root, "access"))) if v}
    if "size" in dev_defaults:
        dev_defaults["size"] = _int(dev_defaults["size"])
    nodes = {_text(p, "name"): p for p in root.find("peripherals").findall("peripheral")}
    peripherals = []
    for name, node in nodes.items():
        src = node
        if node.get("derivedFrom"):
            if node.get("derivedFrom") not in nodes:
                raise SvdError(f"{name}: derivedFrom {node.get('derivedFrom')} not found")
            src = nodes[node.get("derivedFrom")]
        defaults = dict(dev_defaults)
        for n in (src, node):
            if _text(n, "size") is not None:
                defaults["size"] = _int(_text(n, "size"))
            if _text(n, "access") is not None:
                defaults["access"] = _text(n, "access")
        regs_node = node.find("registers") if node.find("registers") is not None else src.find("registers")
        registers = [] if regs_node is None else _registers(regs_node, defaults)
        interrupts = [(_text(i, "name"), _int(_text(i, "value")), _text(i, "description", ""))
                      for i in node.findall("interrupt")]
        peripherals.append(Peripheral(name, _int(_text(node, "baseAddress")),
                                      _text(node, "groupName") or _text(src, "groupName"),
                                      _text(node, "description") or _text(src, "description", ""),
                                      registers, interrupts))
    license_node = root.find("licenseText")
    license_text = "" if license_node is None or license_node.text is None else license_node.text
    license_text = "\n".join(line.rstrip() for line in license_text.replace("\\n", "\n").strip().splitlines())
    return Device(_text(root, "name"), _text(root, "version", ""), cpu, license_text, peripherals)


# ----------------------------------------------------------------------------------------------- rules


def load_rules(path: Path) -> dict:
    from ruamel.yaml import YAML

    data = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise SvdError(f"{path.name}: schema_version 1 expected")
    for key in data:
        if key not in ("schema_version", "family", "peripherals", "modules", "derivatives"):
            raise SvdError(f"{path.name}: unknown key {key!r}")
    return data


def _compile(pattern: str, where: str) -> re.Pattern:
    try:
        rx = re.compile(pattern)
    except re.error as e:
        raise SvdError(f"{where}: invalid match {pattern!r}: {e}") from None
    if rx.groups < 1:
        raise SvdError(f"{where}: match {pattern!r} needs a group for the index")
    return rx


def _index(m: re.Match, base: int, where: str) -> int:
    value = next((g for g in m.groups() if g is not None), None)
    if value is None:
        raise SvdError(f"{where}: {m.string} matched without an index")
    number = int(value) if value.isdigit() else (ord(value) - ord("A") if re.fullmatch(r"[A-Z]", value) else None)
    if number is None:
        raise SvdError(f"{where}: index {value!r} of {m.string} is neither a number nor a letter")
    return number - base


# ----------------------------------------------------------------------------------------------- layout model


@dataclass
class Member:
    """One element of a register layout: a register, an array, a cluster array or a union."""

    kind: str                   # register | array | cluster | union | struct
    name: str
    offset: int                 # bytes, relative to the enclosing layout
    size: int                   # bytes of one element
    count: int = 1
    access: str = "read-write"
    fields: tuple[Field, ...] = ()
    macro: str | None = None    # register part of the field macros; None: no macros
    description: str = ""
    children: list["Member"] = field(default_factory=list)
    count_macro: str | None = None


def _merge_fields(regs: list[Register], where: str) -> tuple[Field, ...]:
    merged: dict[str, Field] = {}
    for r in regs:
        for f in r.fields:
            known = merged.get(f.name)
            if known is not None and (known.offset, known.width) != (f.offset, f.width):
                raise SvdError(f"{where}: field {f.name} differs between elements ({r.name}: bit {f.offset}+{f.width}, "
                               f"elsewhere bit {known.offset}+{known.width})")
            merged.setdefault(f.name, f)
    return tuple(sorted(merged.values(), key=lambda f: f.offset))


def _access(regs: list[Register]) -> str:
    kinds = {r.access for r in regs}
    return kinds.pop() if len(kinds) == 1 else "read-write"


def _same_fields(regs: list[Register]) -> bool:
    first = [(f.name, f.offset, f.width) for f in regs[0].fields]
    return all([(f.name, f.offset, f.width) for f in r.fields] == first for r in regs[1:])


def _array_member(name, regs_by_index: dict[int, Register], stride, where, macro, count=None) -> Member:
    indices = sorted(regs_by_index)
    n = count if count is not None else len(indices)
    if indices != list(range(len(indices))) or (count is not None and len(indices) > count):
        raise SvdError(f"{where}: elements {indices} of {name} are not numbered 0..n-1")
    regs = [regs_by_index[i] for i in indices]
    size = regs[0].size
    if any(r.size != size for r in regs):
        raise SvdError(f"{where}: elements of {name} have different sizes")
    stride = stride if stride is not None else size // 8
    for i, r in zip(indices, regs):
        if r.offset != regs[0].offset + i * stride:
            raise SvdError(f"{where}: {r.name} at offset {r.offset:#x}, expected {regs[0].offset + i * stride:#x} "
                           f"(array {name}, step {stride:#x})")
    if stride != size // 8:
        raise SvdError(f"{where}: array {name} has step {stride:#x} but element size {size // 8:#x} (use a cluster)")
    return Member("array", name, regs[0].offset, size // 8, n, _access(regs), _merge_fields(regs, where), macro,
                  regs[0].description)


def _apply_rules(module: str, regs: list[Register], rules: list, extra: dict) -> tuple[list[Member], set[str]]:
    """Explicit layout rules of one module. Returns members and the names of the registers they consumed."""
    members: list[Member] = []
    used: set[str] = set()
    for n, rule in enumerate(rules):
        where = f"rules modules.{module}.layout[{n}]"
        base = int(rule.get("index_base", 0))
        if "skip" in rule:
            rx = re.compile(rule["skip"])
            used |= {r.name for r in regs if rx.fullmatch(r.name)}
            continue
        if "rename_all" in rule:
            rx = re.compile(rule["rename_all"])
            for i, r in enumerate(regs):
                if rx.fullmatch(r.name) and r.name not in used:
                    regs[i] = replace(r, name=rx.sub(rule["to"], r.name))
            continue
        if "rename" in rule:
            rx = re.compile(rule["match"])
            hits = [r for r in regs if rx.fullmatch(r.name) and r.name not in used]
            if len(hits) > 1:
                raise SvdError(f"{where}: {rule['match']} matches {len(hits)} registers")
            for r in hits:
                members.append(Member("register", rule["rename"], r.offset, r.size // 8, 1, r.access, r.fields,
                                      rule.get("macro", rule["rename"]), r.description))
                used.add(r.name)
            continue
        if "array" in rule or "sparse_array" in rule:
            name = rule.get("array") or rule["sparse_array"]
            rx = _compile(rule["match"], where)
            hits = {}
            for r in regs:
                m = rx.fullmatch(r.name)
                if m and r.name not in used:
                    if "sparse_array" in rule or rule.get("by_offset"):
                        hits[r.name] = r
                    else:
                        idx = _index(m, base, where)
                        if idx in hits:
                            raise SvdError(f"{where}: index {idx} of {name} twice ({hits[idx].name}, {r.name})")
                        hits[idx] = r
            if not hits:
                continue
            if rule.get("by_offset") and "array" in rule:
                hits = dict(enumerate(sorted(hits.values(), key=lambda r: r.offset)))
            used |= {r.name for r in hits.values()}
            macro = rule.get("macro", name)
            if "sparse_array" in rule:
                stride = int(rule.get("stride", 4))
                start = int(rule.get("start", 0))
                sizes = {r.size for r in hits.values()}
                if sizes != {stride * 8}:
                    raise SvdError(f"{where}: {name} elements must all be {stride * 8} bits")
                last = max(r.offset for r in hits.values())
                count = (last - start) // stride + 1
                regs_sorted = sorted(hits.values(), key=lambda r: r.offset)
                members.append(Member("array", name, start, stride, count, _access(regs_sorted),
                                      _merge_fields(regs_sorted, where), macro, regs_sorted[0].description))
                template = rule.get("index_macro")
                if template:
                    for r in regs_sorted:
                        if (r.offset - start) % stride:
                            raise SvdError(f"{where}: {r.name} is not on the {stride}-byte grid of {name}")
                        key = rx.fullmatch(r.name).group(1)
                        extra.setdefault("index_macros", []).append((template.format(key), (r.offset - start) // stride))
            else:
                members.append(_array_member(name, hits, rule.get("stride"), where, macro, rule.get("count")))
                members[-1].count_macro = rule.get("count_macro")
                if rule.get("element_macros"):
                    # field macros per element under the register's own name (ADC_SC1A_ADCH), as in the NXP headers
                    for r in sorted(hits.values(), key=lambda r: r.offset):
                        members.append(Member("macros_only", r.name, r.offset, r.size // 8, 1, r.access, r.fields,
                                              r.name, r.description))
            continue
        if "cluster" in rule:
            name = rule["cluster"]
            by_index: dict[int, dict[str, Register]] = {}
            # array members (array: true): group 1 is the cluster element, group 2 the array element (PDB CH[n].DLY[m])
            arrays: dict[int, dict[str, dict[int, Register]]] = {}
            specs = rule["members"]
            for spec in specs:
                swhere = f"{where}.{spec['name']}"
                if "element" in spec:
                    # fixed element of a cluster the SVD describes without an index (PDB PODLY -> POnDLY[0].PODLY)
                    rx = re.compile(spec["match"])
                else:
                    rx = _compile(spec["match"], swhere)
                if spec.get("array") and rx.groups < 2:
                    raise SvdError(f"{swhere}: array member needs groups for the cluster and the array element")
                for r in regs:
                    m = rx.fullmatch(r.name)
                    if m and r.name not in used:
                        if "element" in spec:
                            idx = int(spec["element"])
                        elif spec.get("array"):
                            idx = _index(re.fullmatch(r"(.+)", m.group(1)), base, swhere)
                            sub = _index(re.fullmatch(r"(.+)", m.group(2)), 0, swhere)
                            elems = arrays.setdefault(idx, {}).setdefault(spec["name"], {})
                            if sub in elems:
                                raise SvdError(f"{where}: {spec['name']}[{idx}][{sub}] twice")
                            elems[sub] = r
                            by_index.setdefault(idx, {}).setdefault(spec["name"], r)
                            continue
                        else:
                            idx = _index(m, base, where)
                        if spec["name"] in by_index.setdefault(idx, {}):
                            raise SvdError(f"{where}: {spec['name']}[{idx}] twice")
                        by_index[idx][spec["name"]] = r
            if not by_index:
                continue
            for idx, members_of in arrays.items():
                for spec_name, elems in members_of.items():
                    by_index[idx][spec_name] = min(elems.values(), key=lambda r: r.offset)
            indices = sorted(by_index)
            if indices != list(range(len(indices))):
                raise SvdError(f"{where}: cluster {name} elements {indices} are not numbered 0..n-1")
            first = by_index[0]
            start = min(r.offset for r in first.values())
            stride = rule.get("stride")
            if stride is None:
                if len(indices) < 2:
                    raise SvdError(f"{where}: cluster {name} has one element, give the stride")
                stride = min(r.offset for r in by_index[1].values()) - start
            children = []
            for spec in specs:
                elements = [by_index[i].get(spec["name"]) for i in indices]
                if any(e is None for e in elements):
                    missing = [i for i, e in zip(indices, elements) if e is None]
                    raise SvdError(f"{where}: cluster {name} member {spec['name']} missing in elements {missing}")
                if spec.get("array"):
                    # same array in every element: same element indices, same relative offsets
                    per = [_array_member(spec["name"], arrays[i][spec["name"]], None, f"{where}.{spec['name']}",
                                         spec.get("macro", spec["name"])) for i in indices]
                    rel = per[0].offset - start
                    for i, a in zip(indices, per):
                        if (a.offset, a.size, a.count) != (start + i * stride + rel, per[0].size, per[0].count):
                            raise SvdError(f"{where}: array {spec['name']}[{i}] does not fit cluster {name} (offset "
                                           f"{a.offset:#x}, expected {start + i * stride + rel:#x}, step {stride:#x})")
                    all_regs = [r for i in indices for r in arrays[i][spec["name"]].values()]
                    children.append(replace(per[0], offset=rel, access=_access(all_regs),
                                            fields=_merge_fields(all_regs, where)))
                    used |= {r.name for r in all_regs}
                    continue
                rel = elements[0].offset - start
                for i, e in zip(indices, elements):
                    if e.offset != start + i * stride + rel or e.size != elements[0].size:
                        raise SvdError(f"{where}: {e.name} does not fit cluster {name} (offset {e.offset:#x}, "
                                       f"expected {start + i * stride + rel:#x}, step {stride:#x})")
                children.append(Member("register", spec["name"], rel, elements[0].size // 8, 1, _access(elements),
                                       _merge_fields(elements, where), spec.get("macro", spec["name"]),
                                       elements[0].description))
                used |= {e.name for e in elements}
            children.sort(key=lambda c: c.offset)
            if max(c.offset + c.size * c.count for c in children) > stride:
                raise SvdError(f"{where}: members of cluster {name} do not fit the step {stride:#x}")
            members.append(Member("cluster", name, start, stride, int(rule.get("count", len(indices))),
                                  children=children, description=rule.get("description", "")))
            continue
        if "union" in rule:
            children = []
            for spec in rule["members"]:
                if "struct" in spec:
                    parts = []
                    for p in spec["members"]:
                        rx = re.compile(p["match"])
                        hits = [r for r in regs if rx.fullmatch(r.name) and r.name not in used]
                        if len(hits) > 1:
                            raise SvdError(f"{where}: {p['match']} matches {len(hits)} registers")
                        if hits:
                            r = hits[0]
                            used.add(r.name)
                            parts.append(Member("register", p["name"], r.offset, r.size // 8, 1, r.access, r.fields,
                                                p.get("macro", f"{rule['union']}_{spec['struct']}_{p['name']}"),
                                                r.description))
                    if parts:
                        start = min(p.offset for p in parts)
                        for p in parts:
                            p.offset -= start
                        children.append(Member("struct", spec["struct"], start, 0, children=parts))
                else:
                    rx = re.compile(spec["match"])
                    hits = [r for r in regs if rx.fullmatch(r.name) and r.name not in used]
                    if hits:
                        r = hits[0]
                        used.add(r.name)
                        children.append(Member("register", spec["name"], r.offset, r.size // 8, 1, r.access,
                                               r.fields, spec.get("macro", f"{rule['union']}_{spec['name']}"),
                                               r.description))
            if not children:
                continue
            start = min(c.offset for c in children)
            for c in children:
                c.offset -= start
                if c.kind == "struct":
                    c.size = max(p.offset + p.size for p in c.children)
            members.append(Member("union", rule["union"], start, max(c.offset + c.size for c in children),
                                  children=sorted(children, key=lambda c: c.offset)))
            continue
        raise SvdError(f"{where}: rule needs one of skip, rename, array, sparse_array, cluster, union")
    return members, used


NUMBERED = re.compile(r"^(.*\D)(\d+)$")


def _auto_arrays(regs: list[Register], where: str) -> tuple[list[Member], set[str]]:
    """Plain numbered arrays: NAME0..NAMEn-1, contiguous, same size and fields."""
    groups: dict[str, dict[int, Register]] = {}
    for r in regs:
        m = NUMBERED.match(r.name)
        if m and not r.alternate:
            groups.setdefault(m.group(1), {})[int(m.group(2))] = r
    members, used = [], set()
    for stem, by_index in groups.items():
        indices = sorted(by_index)
        if len(indices) < 2 or indices != list(range(len(indices))):
            continue
        elements = [by_index[i] for i in indices]
        size = elements[0].size
        if any(e.size != size or e.offset != elements[0].offset + i * size // 8 for i, e in enumerate(elements)):
            continue
        if not _same_fields(elements) or any(e.access != elements[0].access for e in elements):
            continue
        if any(r.name == stem for r in regs):
            continue
        members.append(Member("array", stem, elements[0].offset, size // 8, len(elements), elements[0].access,
                              elements[0].fields, stem, elements[0].description))
        used |= {e.name for e in elements}
    return members, used


@dataclass
class Module:
    name: str
    instances: list[tuple[str, int]]        # (instance name, base address)
    members: list[Member]
    description: str
    index_macros: list[tuple[str, int]]


def _natural(name: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name)]


def build_modules(device: Device, rules: dict, derivative: str) -> list[Module]:
    drules = (rules.get("derivatives") or {}).get(derivative) or {}
    prules = dict(rules.get("peripherals") or {})
    prules.update(drules.get("peripherals") or {})
    grouped: dict[str, list[tuple[str, Peripheral]]] = {}
    for p in device.peripherals:
        pr = prules.get(p.name) or {}
        if "skip" in pr:
            continue
        module = pr.get("module") or p.group or re.sub(r"_?\d+$", "", p.name)
        grouped.setdefault(module, []).append((pr.get("instance", p.name), p))
    modules = []
    mrules_all = rules.get("modules") or {}
    for module in sorted(grouped):
        where = f"{device.name} {module}"
        mrule = mrules_all.get(module) or {}
        strip = mrule.get("strip_prefix")
        # union of the registers of all instances (e.g. FTM1 has registers FTM0 lacks)
        union: dict[str, Register] = {}
        for inst, p in grouped[module]:
            for r in p.registers:
                if strip and r.name.startswith(strip):
                    r = replace(r, name=r.name[len(strip):])
                known = union.get(r.name)
                if known is None:
                    union[r.name] = r
                elif (known.offset, known.size) != (r.offset, r.size):
                    raise SvdError(f"{where}: register {r.name} at {r.offset:#x}/{r.size} in {p.name}, "
                                   f"at {known.offset:#x}/{known.size} in another instance")
                elif known.fields != r.fields and mrule.get("instance_fields") != "first":
                    union[r.name] = replace(known, fields=_merge_fields([known, r], f"{where} {r.name}"))
        regs = sorted(union.values(), key=lambda r: (r.offset, r.alternate, r.name))
        extra: dict = {}
        members, used = _apply_rules(module, regs, mrule.get("layout") or [], extra)
        rest = [r for r in regs if r.name not in used]
        auto, auto_used = _auto_arrays(rest, where) if mrule.get("auto_arrays", True) else ([], set())
        members += auto
        rest = [r for r in rest if r.name not in auto_used]
        alternates = mrule.get("alternates", "error")
        for r in rest:
            members.append(Member("register", r.name, r.offset, r.size // 8, 1, r.access, r.fields, r.name,
                                  r.description))
            members[-1].offset = r.offset
        members.sort(key=lambda m: (m.offset, m.kind != "union"))
        extra_macros = [m for m in members if m.kind == "macros_only"]
        members = [m for m in members if m.kind != "macros_only"]
        kept = []
        for m in members:
            end = kept[-1].offset + kept[-1].size * kept[-1].count if kept else 0
            if kept and m.offset < end:
                if alternates != "first":
                    raise SvdError(f"{where}: {m.name} at {m.offset:#x} overlaps {kept[-1].name} "
                                   f"({kept[-1].offset:#x}..{end:#x}); add a union/skip rule or alternates: first")
                extra_macros.append(m)  # field macros only
                continue
            kept.append(m)
        for m in extra_macros:
            m.kind = "macros_only"
        instances = sorted(((inst, p.base) for inst, p in grouped[module]), key=lambda x: _natural(x[0]))
        if len({i for i, _ in instances}) != len(instances):
            raise SvdError(f"{where}: instance names not unique")
        modules.append(Module(module, instances, kept + extra_macros, grouped[module][0][1].description,
                              extra.get("index_macros", [])))
    return modules


def interrupts(device: Device, rules: dict, derivative: str) -> list[tuple[str, int, str]]:
    drules = (rules.get("derivatives") or {}).get(derivative) or {}
    rename = drules.get("interrupts") or {}
    by_number: dict[int, tuple[str, str]] = {}
    seen_names = set()
    for p in device.peripherals:
        for name, number, desc in p.interrupts:
            name = rename.get(name, name)
            if name is None:
                continue
            known = by_number.get(number)
            if known and known[0] != name:
                raise SvdError(f"{device.name}: interrupt {number} named {known[0]} and {name}; "
                               f"rename one in derivatives.{derivative}.interrupts")
            by_number.setdefault(number, (name, desc))
    result = []
    for number in sorted(by_number):
        name, desc = by_number[number]
        if name in seen_names:
            raise SvdError(f"{device.name}: interrupt name {name} used for two numbers")
        seen_names.add(name)
        result.append((name, number, desc))
    unused = set(rename) - {n for p in device.peripherals for n, _, _ in p.interrupts}
    if unused:
        raise SvdError(f"derivatives.{derivative}.interrupts: {sorted(unused)} not in the SVD")
    return result


# ----------------------------------------------------------------------------------------------- emitter

CORE_EXCEPTIONS = {
    "CM0PLUS": [("NonMaskableInt", -14, "Non Maskable Interrupt"), ("HardFault", -13, "Cortex-M0+ Hard Fault"),
                ("SVCall", -5, "Cortex-M0+ SV Call"), ("PendSV", -2, "Cortex-M0+ Pend SV"),
                ("SysTick", -1, "Cortex-M0+ System Tick")],
    "CM4": [("NonMaskableInt", -14, "Non Maskable Interrupt"), ("HardFault", -13, "Cortex-M4 SV Hard Fault"),
            ("MemoryManagement", -12, "Cortex-M4 Memory Management"), ("BusFault", -11, "Cortex-M4 Bus Fault"),
            ("UsageFault", -10, "Cortex-M4 Usage Fault"), ("SVCall", -5, "Cortex-M4 SV Call"),
            ("DebugMonitor", -4, "Cortex-M4 Debug Monitor"), ("PendSV", -2, "Cortex-M4 Pend SV"),
            ("SysTick", -1, "Cortex-M4 System Tick")],
}
CTYPE = {1: "uint8_t", 2: "uint16_t", 4: "uint32_t"}
QUAL = {"read-only": "__I ", "write-only": "__O ", "read-write": "__IO", "writeOnce": "__IO",
        "read-writeOnce": "__IO"}


def _c_comment(text: str) -> str:
    return text.replace("*/", "* /").replace("/*", "/ *")


def _pad(left: str, column: int) -> str:
    return left + " " * max(1, column - len(left))


def _emit_members(members: list[Member], out: list[str], module: str, indent: str, counts: list[str],
                  size_limit: int | None = None):
    pos = 0
    reserved = 0
    for m in members:
        if m.kind == "macros_only":
            continue
        if m.offset > pos:
            out.append(f"{indent}uint8_t RESERVED_{reserved}[{m.offset - pos}];")
            reserved += 1
        desc = _c_comment(m.description)
        if m.kind == "register":
            if m.size not in CTYPE:
                raise SvdError(f"{module}.{m.name}: {m.size * 8}-bit register not supported")
            line = f"{indent}{QUAL.get(m.access, '__IO')} {CTYPE[m.size]} {m.name};"
            out.append(_pad(line, 51 + len(indent) - 2) + f"/**< {desc}, offset: {m.offset:#X} */".replace("0X", "0x"))
        elif m.kind == "array":
            macro = m.count_macro or f"{module}_{m.name}_COUNT"
            counts.append(macro + " " * max(1, 42 - len(macro)) + f"{m.count}u")
            line = f"{indent}{QUAL.get(m.access, '__IO')} {CTYPE[m.size]} {m.name}[{macro}];"
            out.append(_pad(line, 51 + len(indent) - 2) + f"/**< {desc}, array offset: {m.offset:#X}, array step: "
                       f"{m.size:#X} */".replace("0X", "0x"))
        elif m.kind in ("cluster", "union", "struct"):
            keyword = "union" if m.kind == "union" else "struct"
            out.append(_pad(f"{indent}{keyword} {{", 51 + len(indent) - 2) + f"/* offset: {m.offset:#X}".replace("0X", "0x")
                       + (f", array step: {m.size:#X} */".replace("0X", "0x") if m.kind == "cluster" else " */"))
            if m.kind == "union":
                for c in m.children:
                    sub = []
                    _emit_members([replace(c, offset=0)], sub, module, indent + "  ", counts)
                    out += sub
            else:
                _emit_members(m.children, out, module, indent + "  ", counts, m.size if m.kind == "cluster" else None)
            if m.kind == "cluster":
                macro = f"{module}_{m.name}_COUNT"
                counts.append(macro + " " * max(1, 42 - len(macro)) + f"{m.count}u")
                out.append(f"{indent}}} {m.name}[{macro}];")
            else:
                out.append(f"{indent}}} {m.name};")
        pos = m.offset + m.size * m.count
    if size_limit is not None and size_limit > pos:
        out.append(f"{indent}uint8_t RESERVED_{reserved}[{size_limit - pos}];")


def _field_macros(module: str, members: list[Member], out: list[str]):
    def registers(ms):
        for m in ms:
            if m.kind in ("register", "array", "macros_only") and m.macro:
                yield m
            yield from registers(m.children)

    seen = {}
    for reg in registers(members):
        if not reg.fields:
            continue
        ctype = CTYPE.get(reg.size, "uint32_t")
        out.append(f"/*! @name {reg.macro} - {_c_comment(reg.description)} */")
        out.append("/*! @{ */")
        for f in reg.fields:
            base = f"{module}_{reg.macro}_{f.name}"
            key = (f.offset, f.width)
            if base in seen:
                if seen[base] != key:
                    raise SvdError(f"{module}: field macro {base} defined twice with different bits")
                continue
            seen[base] = key
            mask = ((1 << f.width) - 1) << f.offset
            out.append("")
            out.append(_pad(f"#define {base}_MASK", 49) + f"({mask:#X}U)".replace("0X", "0x"))
            out.append(_pad(f"#define {base}_SHIFT", 49) + f"({f.offset}U)")
            out.append(_pad(f"#define {base}_WIDTH", 49) + f"({f.width}U)")
            out.append(_pad(f"#define {base}(x)", 49)
                       + f"((({ctype})((({ctype})(x)) << {base}_SHIFT)) & {base}_MASK)")
        out.append("/*! @} */")
        out.append("")


def render(device: Device, rules: dict, derivative: str, svd_name: str, svd_sha256: str,
           rules_name: str) -> str:
    drules = (rules.get("derivatives") or {}).get(derivative) or {}
    cpu = dict(device.cpu)
    cpu.update({k: str(v) for k, v in (drules.get("cpu") or {}).items()})
    core = cpu.get("name", "")
    if core not in CORE_EXCEPTIONS:
        raise SvdError(f"{device.name}: CPU {core!r} not supported (known: {sorted(CORE_EXCEPTIONS)})")
    modules = build_modules(device, rules, derivative)
    irqs = interrupts(device, rules, derivative)
    guard = f"{derivative}_H_"
    lic = "\n".join(("**     " + line).rstrip() for line in device.license_text.splitlines())
    o = [
        "/*",
        "** ###################################################################",
        f"**     Processor:           {derivative}",
        f"**     Generated by:        {GENERATOR} (neXtBSW PLAT-11), do not edit",
        f"**     Source SVD:          {svd_name} ({device.name} version {device.version})",
        f"**     Source SHA-256:      {svd_sha256}",
        f"**     Naming rules:        {rules_name}",
        "**",
        "**     Peripheral Access Layer. The register data below is taken from the SVD file, whose license",
        "**     notice follows:",
        "**",
        lic,
        "**",
        "** ###################################################################",
        "*/",
        "",
        f"#if !defined({guard})",
        f"#define {guard}",
        f"#define MCU_{derivative}",
        "",
        "#if (defined(MCU_ACTIVE))",
        f"  #error {derivative} memory map: There is already included another memory map. Only one memory map can be included.",
        "#endif",
        "#define MCU_ACTIVE",
        "",
        "#include <stdint.h>",
        "",
        "#ifndef __IO",
        "#ifdef __cplusplus",
        "  #define   __I     volatile             /*!< Defines 'read only' permissions */",
        "#else",
        "  #define   __I     volatile const       /*!< Defines 'read only' permissions */",
        "#endif",
        "#define     __O     volatile             /*!< Defines 'write only' permissions */",
        "#define     __IO    volatile             /*!< Defines 'read / write' permissions */",
        "#endif",
        "",
        "/* ----------------------------------------------------------------------------",
        "   -- Interrupt vector numbers",
        "   ---------------------------------------------------------------------------- */",
        "",
        f"#define NUMBER_OF_INT_VECTORS {16 + (max(n for _, n, _ in irqs) + 1 if irqs else 0)}"
        "                /**< Number of interrupts in the Vector table */",
        "",
        "typedef enum {",
        "  /* Auxiliary constants */",
        _pad("  NotAvail_IRQn", 31) + "= -128,             /**< Not available device specific interrupt */",
        "",
        "  /* Core interrupts */",
    ]
    entries = [(f"{n}_IRQn", v, d + " Interrupt") for n, v, d in CORE_EXCEPTIONS[core]]
    entries += [None]
    entries += [(f"{n}_IRQn", v, d) for n, v, d in irqs]
    last = max(i for i, e in enumerate(entries) if e is not None)
    for i, e in enumerate(entries):
        if e is None:
            o += ["", "  /* Device specific interrupts */"]
            continue
        value = f"= {e[1]}" + ("" if i == last else ",")
        o.append(_pad(f"  {e[0]}", 31) + _pad(value, 20) + f"/**< {_c_comment(e[2])} */")
    o += ["} IRQn_Type;", ""]

    def flag(key, default="0"):
        return "1" if cpu.get(key, default).lower() in ("true", "1") else "0"

    o += [
        "/* ----------------------------------------------------------------------------",
        f"   -- Core configuration ({core})",
        "   ---------------------------------------------------------------------------- */",
        "",
    ]
    if core == "CM0PLUS":
        o.append(_pad("#define __CM0PLUS_REV", 40) + "0x0000    /**< Core revision r0p0 */")
    o.append(_pad("#define __MPU_PRESENT", 40) + f"{flag('mpuPresent')}         /**< Defines if an MPU is present or not */")
    if "nvicPrioBits" not in cpu:
        raise SvdError(f"{device.name}: cpu.nvicPrioBits missing (SVD or rules derivatives.{derivative}.cpu)")
    o.append(_pad("#define __NVIC_PRIO_BITS", 40) + f"{int(cpu['nvicPrioBits'])}"
             "         /**< Number of priority bits implemented in the NVIC */")
    o.append(_pad("#define __Vendor_SysTickConfig", 40) + f"{flag('vendorSystickConfig')}"
             "         /**< Vendor specific implementation of SysTickConfig is defined */")
    if core == "CM0PLUS":
        o.append(_pad("#define __VTOR_PRESENT", 40) + f"{flag('vtorPresent', '1')}"
                 "         /**< Defines if VTOR is present or not */")
    o.append(_pad("#define __FPU_PRESENT", 40) + f"{flag('fpuPresent')}         /**< Defines if an FPU is present or not */")
    o.append("")

    for mod in modules:
        o += [
            "/* ----------------------------------------------------------------------------",
            f"   -- {mod.name} Peripheral Access Layer",
            "   ---------------------------------------------------------------------------- */",
            "",
        ]
        body, counts = [], []
        _emit_members(mod.members, body, mod.name, "  ", counts)
        if counts:
            o.append(f"/** {mod.name} - Size of Registers Arrays */")
            o += ["#define " + c for c in counts]
            o.append("")
        o.append(f"/** {mod.name} - Register Layout Typedef */")
        o.append("typedef struct {")
        o += body
        o.append(f"}} {mod.name}_Type, *{mod.name}_MemMapPtr;")
        o.append("")
        o.append(f"/** Number of instances of the {mod.name} module. */")
        o.append(_pad(f"#define {mod.name}_INSTANCE_COUNT", 49) + f"({len(mod.instances)}u)")
        o.append("")
        o.append(f"/* {mod.name} - Peripheral instance base addresses */")
        for inst, base in mod.instances:
            o.append(f"/** Peripheral {inst} base address */")
            o.append(_pad(f"#define IP_{inst}_BASE", 49) + f"({base:#010X}u)".replace("0X", "0x"))
            o.append(f"/** Peripheral {inst} base pointer */")
            o.append(_pad(f"#define IP_{inst}", 49) + f"(({mod.name}_Type *)IP_{inst}_BASE)")
        o.append(f"/** Array initializer of {mod.name} peripheral base addresses */")
        o.append(_pad(f"#define IP_{mod.name}_BASE_ADDRS", 49)
                 + "{ " + ", ".join(f"IP_{i}_BASE" for i, _ in mod.instances) + " }")
        o.append(f"/** Array initializer of {mod.name} peripheral base pointers */")
        o.append(_pad(f"#define IP_{mod.name}_BASE_PTRS", 49) + "{ " + ", ".join(f"IP_{i}" for i, _ in mod.instances) + " }")
        o.append("")
        o.append(f"/* {mod.name} Register Masks */")
        o.append("")
        _field_macros(mod.name, mod.members, o)
        for name, value in mod.index_macros:
            o.append(f"#define {name} {value}")
        if mod.index_macros:
            o.append("")
    o += [f"#endif  /* #if !defined({guard}) */", ""]
    text = "\n".join(o)
    return text.replace("0X", "0x") if False else text


# ------------------------------------------------------------------------------- interrupt list of the derivative
# PLAT-12: the startup template of a core architecture takes the external interrupt vectors from the derivative
# (platforms/<family>/startup/README.md, "Interrupt list startup/nxIrqVectors.inc"). The names are the ones of the
# device header (`<IRQ>_IRQn` -> handler `<IRQ>_IRQHandler`), so nothing but the derivative decides the table.

IRQ_SUFFIX = "_IRQn"
IRQ_HANDLER_SUFFIX = "_IRQHandler"


def external_irqs(irqs: dict[str, int]) -> dict[int, str]:
    """External interrupt numbers (>= 0) of a device header's IRQn_Type, by number -> name without _IRQn."""
    result: dict[int, str] = {}
    for name, number in sorted(irqs.items()):
        if number < 0 or not name.endswith(IRQ_SUFFIX):
            continue                    # core exceptions (negative) and NotAvail_IRQn (-128)
        short = name[: -len(IRQ_SUFFIX)]
        if number in result and result[number] != short:
            raise SvdError(f"interrupt {number} named {result[number]} and {short} in the device header")
        result[number] = short
    return result


def irq_vectors_inc(irqs: dict[str, int], source: str, handlers: dict[str, str] | None = None) -> str:
    """Text of startup/nxIrqVectors.inc: one line per external interrupt number, starting at 0.

    `handlers` overrides the handler symbol of single interrupts (names a derivative spells differently, e.g.
    FTFC_Fault_IRQHandler for the S32K14x FTFM_Fault; tools/bswgen FAMILY_IRQ_HANDLERS). Since SYS-170 the s32k1xx
    startups route every listed handler through its wrapper Safe_<handler> themselves.
    """
    external = external_irqs(irqs)
    handlers = handlers or {}
    lines = [f"/* Generated from {source} - do not edit. External interrupt vectors of the derivative,",
             " * one line per interrupt number from 0 (platforms/<family>/startup/README.md). */"]
    for number in range(max(external) + 1 if external else 0):
        short = external.get(number)
        if short is None:
            lines.append(f"NX_IRQ_RESERVED()                      /* {number} */")
            continue
        handler = handlers.get(short, short + IRQ_HANDLER_SUFFIX)
        lines.append(_pad(f"NX_IRQ_VECTOR({handler})", 39) + f"/* {number} {short} */")
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------------------------------- header parser

_TYPE_SIZE = {"uint8_t": 1, "uint16_t": 2, "uint32_t": 4}


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def read_header(path: Path, seen=None) -> str:
    """Header text with local #include "..." files inlined (NXP split headers)."""
    seen = set() if seen is None else seen
    if path in seen:
        return ""
    seen.add(path)
    text = path.read_text(encoding="utf-8", errors="replace")

    def inline(m):
        inc = path.parent / m.group(1)
        return read_header(inc, seen) if inc.is_file() else m.group(0)
    return re.sub(r'^#include\s+"([^"]+)"', inline, text, flags=re.M)


def header_layout(text: str) -> dict:
    """Structural view of an NXP-style header: macros, register layouts (flattened member paths), interrupts."""
    code = _strip_comments(text)
    macros = {}
    for m in re.finditer(r"^[ \t]*#define[ \t]+(\w+(?:\(x\))?)[ \t]*([^\n]*)$", code, re.M):
        macros[m.group(1)] = " ".join(m.group(2).split())
    counts = {}
    for name, value in macros.items():
        v = re.fullmatch(r"\(?\s*(0x[0-9A-Fa-f]+|\d+)[uU]?\s*\)?", value)
        if v:
            counts[name] = int(v.group(1), 0)
    types = {}
    for m in re.finditer(r"typedef\s+struct\s*\{", code):
        depth, i = 1, m.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(code[i], 0)
            i += 1
        tail = re.match(r"\s*(\w+)", code[i:])
        body = code[m.end():i - 1]
        flat: list[tuple[str, int, int]] = []
        _parse_struct(body, 0, "", flat, counts)
        types[tail.group(1)] = flat
    irqs = {m.group(1): int(m.group(2)) for m in re.finditer(r"(\w+_IRQn)\s*=\s*(-?\d+)", code)}
    for m in re.finditer(r"\}\s*(\w+);", code):           # typedef enum { ... } IRQn_Type;
        types.setdefault(m.group(1), [])
    return {"macros": macros, "types": types, "irqs": irqs}


def _parse_struct(body: str, base: int, prefix: str, flat: list, counts: dict, union=False) -> int:
    """Appends (path, offset, element size) for every register element; returns the size in bytes."""
    pos = 0
    size = 0
    i = 0
    tokens = body
    while i < len(tokens):
        m = re.compile(r"\s*(struct|union)\s*\{").match(tokens, i)
        if m:
            depth, j = 1, m.end()
            while depth:
                depth += {"{": 1, "}": -1}.get(tokens[j], 0)
                j += 1
            inner = tokens[m.end():j - 1]
            tail = re.compile(r"\s*(\w+)\s*(?:\[\s*(\w+)\s*\])?\s*;").match(tokens, j)
            name, count = tail.group(1), tail.group(2)
            n = 1 if count is None else (int(count) if count.isdigit() else counts.get(count, -1))
            elem_flat: list = []
            elem = _parse_struct(inner, 0, "", elem_flat, counts, m.group(1) == "union")
            for k in range(max(n, 1)):
                for path, off, sz in elem_flat:
                    flat.append((f"{prefix}{name}{'' if count is None else f'[{k}]'}.{path}",
                                 base + pos + k * elem + off, sz))
            advance = elem * max(n, 1)
            if union:
                size = max(size, advance)
            else:
                pos += advance
            i = tail.end()
            continue
        m = re.compile(r"\s*(?:__IO|__I|__O)?\s*(uint8_t|uint16_t|uint32_t)\s+(\w+)"
                       r"\s*(?:\[\s*(\w+)\s*\])?\s*;").match(tokens, i)
        if m:
            sz = _TYPE_SIZE[m.group(1)]
            count = m.group(3)
            n = 1 if count is None else (int(count) if count.isdigit() else counts.get(count, -1))
            if not m.group(2).startswith("RESERVED"):
                for k in range(max(n, 1)):
                    flat.append((f"{prefix}{m.group(2)}{'' if count is None else f'[{k}]'}", base + pos + k * sz, sz))
            if union:
                size = max(size, sz * max(n, 1))
            else:
                pos += sz * max(n, 1)
            i = m.end()
            continue
        if tokens[i:].strip() == "":
            break
        raise SvdError(f"cannot parse struct member near: {tokens[i:i + 60]!r}")
    return size if union else pos


def _norm_macro(value: str) -> str:
    v = value.replace(" ", "")
    v = re.sub(r"(0x[0-9A-Fa-f]+|\d+)[uU]", lambda m: str(int(m.group(1), 0)), v)
    return v


def compare_layouts(generated: dict, reference: dict, modules: set[str] | None = None,
                    symbols: set[str] | None = None) -> list[str]:
    """Differences between two header layouts, reference-based. modules limits types/macros to these prefixes."""
    diffs = []

    def wanted(name):
        if symbols is not None:
            return name in symbols
        return modules is None or name.split("_")[0] in modules or name.startswith("IP_") and \
            re.sub(r"^IP_([A-Za-z]+).*", r"\1", name) in modules
    for tname, flat in reference["types"].items():
        if not wanted(tname) and not (modules and tname[:-5] in modules):
            continue
        gen = generated["types"].get(tname)
        if gen is None:
            diffs.append(f"type {tname}: missing")
            continue
        g = {p: (o, s) for p, o, s in gen}
        for path, off, sz in flat:
            if path not in g:
                diffs.append(f"type {tname}: member {path} missing")
            elif g[path] != (off, sz):
                diffs.append(f"type {tname}: member {path} at {g[path][0]:#x}/{g[path][1]}, reference {off:#x}/{sz}")
    for name, value in reference["macros"].items():
        if not wanted(name):
            continue
        if name.endswith("(x)"):
            continue
        if name not in generated["macros"]:
            diffs.append(f"macro {name}: missing")
        elif _norm_macro(generated["macros"][name]) != _norm_macro(value):
            diffs.append(f"macro {name}: {generated['macros'][name]} != reference {value}")
    for name, value in reference["irqs"].items():
        if symbols is None and modules is not None:
            continue                          # module filter says nothing about interrupts
        if symbols is not None and name not in symbols:
            continue
        if name not in generated["irqs"]:
            diffs.append(f"irq {name}: missing")
        elif generated["irqs"][name] != value:
            diffs.append(f"irq {name}: {generated['irqs'][name]} != reference {value}")
    return diffs


# ----------------------------------------------------------------------------------------------- symbol usage

USAGE_DIRS = ("external/openbsw/platforms/s32k1xx", "platforms/s32k1xx", "ecu/platforms/evalboard_s32k144", "bootloader")
USAGE_SKIP = re.compile(r"/3rdparty/(nxp|freertos|threadx)|/test/|/mock/")
# OpenBSW mcu.h remaps short names to the IP_ instances
REMAP = {"LMEM", "WDOG", "LPUART0", "LPUART1", "LPIT0", "ADC0", "FTFC", "FTM0", "FTM1", "FTM2", "FTM3", "FTM4",
         "SIM", "RCM", "SCG", "PCC", "ERM", "CRC", "GPIO_BASE_PTRS", "PORT_BASE_PTRS"}


def used_symbols(reference_text: str, root: Path = REPO) -> dict[str, list[str]]:
    """Symbols of a reference header (OpenBSW S32K148.h) that driver and board sources use, with the files."""
    code = _strip_comments(reference_text)
    defined = set(re.findall(r"#define\s+(\w+)", code)) | set(re.findall(r"\}\s*(\w+_Type)\b", code)) \
        | set(re.findall(r"(\w+_IRQn)\s*=", code))
    members = set(re.findall(r"uint(?:8|16|32)_t\s+(\w+)\s*[\[;]", code)) | set(re.findall(r"\}\s*(\w+)\s*[\[;]", code))
    members = {m for m in members if not m.startswith("RESERVED") and not m.endswith("_Type")}
    used: dict[str, set[str]] = {}
    for d in USAGE_DIRS:
        for f in sorted((root / d).rglob("*")):
            rel = f.relative_to(root).as_posix()
            if f.suffix not in {".c", ".cpp", ".h", ".hpp", ".S", ".s"} or USAGE_SKIP.search("/" + rel):
                continue
            text = _strip_comments(f.read_text(encoding="utf-8", errors="replace"))
            for tok in set(re.findall(r"\b[A-Za-z_]\w*\b", text)):
                if tok in defined or tok in REMAP:
                    used.setdefault(tok, set()).add(rel)
            for tok in set(re.findall(r"(?:->|\.)\s*([A-Za-z_]\w*)", text)):
                if tok in members:
                    used.setdefault("." + tok, set()).add(rel)
    return {k: sorted(v) for k, v in sorted(used.items())}


# ----------------------------------------------------------------------------------------------- command line


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rules_path(family: str) -> Path:
    return MCUDB / "tools" / "device_header_rules" / f"{family}.yaml"


def generate(derivative: str, rules_file: Path, svd: Path) -> tuple[str, str]:
    rules = load_rules(rules_file)
    drules = (rules.get("derivatives") or {}).get(derivative)
    if drules is None:
        raise SvdError(f"{rules_file.name}: no entry derivatives.{derivative}")
    sha = _sha256(svd)
    expected = (drules.get("svd") or {}).get("sha256")
    if expected and expected != sha:
        raise SvdError(f"{svd}: SHA-256 {sha} differs from {expected} recorded in {rules_file.name} "
                       f"(other SVD version; update the rules only after reviewing the differences)")
    device = load_svd(svd)
    text = render(device, rules, derivative, (drules.get("svd") or {}).get("file", svd.name), sha,
                  rules_file.relative_to(REPO).as_posix() if rules_file.is_relative_to(REPO) else rules_file.name)
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()


def _svd_for(args, rules_file: Path) -> Path:
    if args.svd:
        return Path(args.svd)
    rules = load_rules(rules_file)
    name = ((rules.get("derivatives") or {}).get(args.derivative, {}).get("svd") or {}).get("file")
    folder = os.environ.get("NX_SVD_DIR")
    if not name or not folder:
        raise SvdError("no SVD: give --svd or set NX_SVD_DIR (workstation level, see "
                       f"mcudb/{rules.get('family', '<family>')}/sources/*/README.md)")
    return Path(folder) / name


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("generate", "compare"):
        p = sub.add_parser(name)
        p.add_argument("--derivative", required=True)
        p.add_argument("--family", default="s32k1xx")
        p.add_argument("--rules", help="rules file (default mcudb/tools/device_header_rules/<family>.yaml)")
        p.add_argument("--svd", help="SVD file (default $NX_SVD_DIR/<file from the rules>)")
        if name == "generate":
            p.add_argument("--out", required=True, help="output directory, writes <derivative>.h")
            p.add_argument("--check", action="store_true", help="only compare with --out and the recorded SHA-256")
        else:
            p.add_argument("--reference", required=True, help="reference header (NXP split headers are inlined)")
            p.add_argument("--modules", help="comma-separated module names to compare (default: all)")
            p.add_argument("--used-only", action="store_true", help="only symbols used by drivers (symbols command)")
    p = sub.add_parser("symbols")
    p.add_argument("--reference", default=str(REPO / OPENBSW_S32K148))
    p.add_argument("--header", help="generated header to check the used symbols against")
    # PLAT-12: external interrupt vectors of a derivative for the startup template (startup/nxIrqVectors.inc)
    p = sub.add_parser("irqvectors")
    p.add_argument("--derivative", help="derivative of the rules file (generates from the SVD)")
    p.add_argument("--family", default="s32k1xx")
    p.add_argument("--rules", help="rules file (default mcudb/tools/device_header_rules/<family>.yaml)")
    p.add_argument("--svd", help="SVD file (default $NX_SVD_DIR/<file from the rules>)")
    p.add_argument("--header", help="device header to read IRQn_Type from instead of generating it from the SVD")
    p.add_argument("--out", help="output file (default: standard output)")
    args = parser.parse_args(argv)
    try:
        if args.command == "irqvectors":
            if args.header:
                source, irqs = args.header, header_layout(read_header(Path(args.header)))["irqs"]
            elif args.derivative:
                rules_file = Path(args.rules) if args.rules else rules_path(args.family)
                svd = _svd_for(args, rules_file)
                if not svd.is_file():
                    raise SvdError(f"SVD {svd} not found (workstation level, see "
                                   f"mcudb/{args.family}/sources/*/README.md)")
                text, _ = generate(args.derivative, rules_file, svd)
                source, irqs = f"{args.derivative} ({svd.name})", header_layout(text)["irqs"]
            else:
                raise SvdError("irqvectors needs --derivative (from the SVD) or --header (from a device header)")
            out = irq_vectors_inc(irqs, source)
            if args.out:
                Path(args.out).parent.mkdir(parents=True, exist_ok=True)
                Path(args.out).write_text(out, encoding="utf-8", newline="\n")
                print(f"{args.out} ({len(external_irqs(irqs))} external interrupts)")
            else:
                sys.stdout.write(out)
            return 0
        if args.command == "symbols":
            used = used_symbols(read_header(Path(args.reference)))
            gen = header_layout(read_header(Path(args.header))) if args.header else None
            members = set()
            if gen:
                for flat in gen["types"].values():
                    for path, _, _ in flat:
                        members |= {re.sub(r"\[\d+\]", "", part) for part in path.split(".")}
            missing = 0
            for sym, files in used.items():
                if gen is None:
                    status = ""
                elif sym.startswith("."):
                    status = "ok" if sym[1:] in members else "MISSING"
                elif sym in REMAP:
                    status = "remap (mcu.h)"
                else:
                    status = "ok" if (sym in gen["macros"] or sym + "(x)" in gen["macros"] or sym in gen["irqs"]
                                      or sym in gen["types"]) else "MISSING"
                missing += status == "MISSING"
                print(f"{sym}\t{status}\t{len(files)}\t{files[0]}")
            print(f"# {len(used)} symbols, {missing} missing", file=sys.stderr)
            return 0
        rules_file = Path(args.rules) if args.rules else rules_path(args.family)
        svd = _svd_for(args, rules_file)
        if not svd.is_file():
            raise SvdError(f"SVD {svd} not found (workstation level, see mcudb/{args.family}/sources/*/README.md)")
        if args.command == "compare":
            rules = load_rules(rules_file)
            text = render(load_svd(svd), rules, args.derivative, svd.name, _sha256(svd), rules_file.name)
            ref = header_layout(read_header(Path(args.reference)))
            mods = set(args.modules.split(",")) if args.modules else None
            symbols = None
            if args.used_only:
                symbols = set(used_symbols(read_header(Path(args.reference))))
            diffs = compare_layouts(header_layout(text), ref, mods, symbols)
            for d in diffs:
                print(d)
            print(f"# {len(diffs)} differences", file=sys.stderr)
            return 1 if diffs else 0
        text, digest = generate(args.derivative, rules_file, svd)
        rules = load_rules(rules_file)
        recorded = ((rules["derivatives"][args.derivative].get("header") or {}).get("sha256"))
        target = Path(args.out) / f"{args.derivative}.h"
        if args.check:
            problems = []
            if recorded and recorded != digest:
                problems.append(f"generated header SHA-256 {digest} differs from {recorded} in {rules_file.name}")
            if not target.is_file():
                problems.append(f"{target} missing")
            elif target.read_text(encoding="utf-8") != text:
                problems.append(f"{target} outdated")
            for p in problems:
                print(p, file=sys.stderr)
            return 1 if problems else 0
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
        print(f"{target} ({len(text)} bytes, SHA-256 {digest})")
        if recorded and recorded != digest:
            print(f"warning: SHA-256 differs from {recorded} recorded in {rules_file.name}", file=sys.stderr)
            return 1
        return 0
    except SvdError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
