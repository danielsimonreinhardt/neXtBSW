# SPDX-License-Identifier: Apache-2.0
"""Field reference of pydantic models (DOC-03). No Qt, no file output: neXtBSW Studio can show the same data.

`document(model, file)` walks a model and returns one `Section` per object in the YAML tree (pre-order, fields in
declaration order), each with one `FieldDoc` per key: YAML path, type, unit, required/optional, default, allowed
values and description. Nothing is invented - where the schema says nothing, the value stays None and the renderer
shows a gap.

Where a description comes from, first match wins:
  1. `Field(description=...)`                                                    source "field"
  2. the comment of the field in the model source (trailing, or the comment
     block directly above the field)                                             source "comment"
  3. first paragraph of the docstring of the field's object type                 source "type"
Where a unit comes from:
  1. `Field(json_schema_extra={"unit": ...})`                                    source "field"
  2. the naming convention `<name>_<unit>` (UNIT_SUFFIXES, e.g. `system_hz`)      source "name"
A unit is only expected for numeric fields; a numeric field without one is reported as a gap.
"""

from __future__ import annotations

import ast
import inspect
import io
import re
import textwrap
import tokenize
import types
import typing
from dataclasses import dataclass, field
from functools import lru_cache

import annotated_types
from pydantic import BaseModel
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

# last `_`-separated token of a field name -> unit. The schema writes fixed units into names (ARCH section 4).
UNIT_SUFFIXES = {
    "hz": "Hz", "khz": "kHz", "mhz": "MHz",
    "ns": "ns", "us": "µs", "ms": "ms",
    "v": "V", "mv": "mV", "ma": "mA",
    "ohm": "Ω", "mohm": "mΩ", "uohm": "µΩ",
    "nc": "nC", "ddegc": "0.1 °C", "k": "K",
    "permille": "‰", "percent": "%",
    "bits": "bit", "bytes": "byte", "tq": "time quanta",
}

INT32_MIN, INT32_MAX = -(1 << 31), (1 << 31) - 1


# ----------------------------------------------------------------------------------------------- data


@dataclass
class TypeInfo:
    """Structured type of a field; the renderer turns it into text, the Studio may render it differently."""

    kind: str                                        # scalar, literal, model, list, dict, union
    name: str = ""                                   # scalar: integer/number/string/boolean/any; model: class name
    values: tuple = ()                               # literal: allowed values
    items: tuple["TypeInfo", ...] = ()               # list: (item,), dict: (key, value), union: members
    constraints: tuple[str, ...] = ()                # human-readable constraints on this level
    discriminator: str | None = None                 # union of objects told apart by this key
    model: type[BaseModel] | None = None             # model: the class
    section: str | None = None                       # model: path of the section documenting it (set by the walk)
    variant: str | None = None                       # model in a discriminated union: value of the discriminator

    def numeric(self) -> bool:
        """True if a number is (part of) the value, so a unit is expected. Dict keys do not count."""
        if self.kind == "scalar":
            return self.name in ("integer", "number")
        if self.kind == "dict":
            return self.items[1].numeric()
        if self.kind in ("list", "union"):
            return any(i.numeric() for i in self.items)
        return False

    def models(self) -> list["TypeInfo"]:
        """Object types reachable from this type (without descending into the objects)."""
        if self.kind == "model":
            return [self]
        if self.kind == "dict":
            return self.items[1].models()
        return [m for i in self.items for m in i.models()]


@dataclass
class FieldDoc:
    key: str                                         # YAML key (alias, e.g. `class`)
    path: str                                        # full YAML path, e.g. memory[].sector_size
    owner: str                                       # class that declares the field (for schema gap lists)
    type: TypeInfo
    required: bool
    default: str | None                              # YAML-like text; None if required
    description: str | None
    description_source: str | None                   # field, comment, type or None
    unit: str | None
    unit_source: str | None                          # field, name or None

    @property
    def numeric(self) -> bool:
        return self.type.numeric()

    @property
    def missing_description(self) -> bool:
        return self.description is None

    @property
    def missing_unit(self) -> bool:
        return self.numeric and self.unit is None


@dataclass
class Section:
    path: str                                        # YAML path of the object, "" for the top level
    model: type[BaseModel]
    anchor: str = ""
    depth: int = 0                                   # nesting of objects, 0 for the top level
    variant: str | None = None                       # discriminated union member: `kind: linear`
    chain: tuple[str, ...] = ()                      # variants of this object and the objects above it
    doc: str | None = None                           # docstring of the model
    fields: list[FieldDoc] = field(default_factory=list)

    @property
    def title(self) -> str:
        return self.path or "(top level)"


# ----------------------------------------------------------------------------------------------- comments


@lru_cache(maxsize=None)
def _class_comments(cls: type) -> dict[str, tuple[str, ...]]:
    """Comments of the fields declared in the body of `cls`: the comment block above, then trailing comments."""
    try:
        lines, _ = inspect.getsourcelines(cls)
    except (OSError, TypeError):
        return {}
    source = textwrap.dedent("".join(lines))
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    comments: dict[int, str] = {}
    code_rows: set[int] = set()
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT:
            comments[tok.start[0]] = tok.string.lstrip("#").strip()
        elif tok.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT,
                              tokenize.ENDMARKER):
            for row in range(tok.start[0], tok.end[0] + 1):
                code_rows.add(row)
    classdef = next((n for n in tree.body if isinstance(n, ast.ClassDef)), None)
    if classdef is None:
        return {}
    result = {}
    for node in classdef.body:
        if not (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)):
            continue
        trailing = [comments[r] for r in range(node.lineno, node.end_lineno + 1) if r in comments and r in code_rows]
        above = []
        row = node.lineno - 1
        while row in comments and row not in code_rows:
            above.insert(0, comments[row])
            row -= 1
        result[node.target.id] = tuple(c for c in above + trailing if c)
    return result


def _declaring_class(model: type[BaseModel], name: str) -> type:
    for cls in model.__mro__:
        if name in cls.__dict__.get("__annotations__", {}):
            return cls
    return model


def _docstring(cls: type) -> str | None:
    doc = cls.__dict__.get("__doc__")
    if not doc:
        return None
    return inspect.cleandoc(doc)


def _first_paragraph(text: str) -> str:
    return " ".join(text.split("\n\n")[0].split())


# ----------------------------------------------------------------------------------------------- types


def _num(v) -> str:
    if isinstance(v, int) and not isinstance(v, bool):
        if v == INT32_MAX:
            return "2^31-1"
        if v == INT32_MIN:
            return "-2^31"
    return str(v)


def _constraints(meta, base: str) -> tuple[list[str], str | None]:
    """Readable constraints of pydantic/annotated_types metadata; returns (texts, discriminator)."""
    lo = hi = None
    lo_open = hi_open = False
    min_len = max_len = None
    texts: list[str] = []
    discriminator = None
    stack = list(meta)
    while stack:
        m = stack.pop(0)
        if isinstance(m, FieldInfo):
            if m.discriminator is not None:
                discriminator = m.discriminator if isinstance(m.discriminator, str) else None
            stack[0:0] = list(m.metadata)
            continue
        if isinstance(m, annotated_types.Gt):
            lo, lo_open = m.gt, True
        elif isinstance(m, annotated_types.Ge):
            lo, lo_open = m.ge, False
        elif isinstance(m, annotated_types.Lt):
            hi, hi_open = m.lt, True
        elif isinstance(m, annotated_types.Le):
            hi, hi_open = m.le, False
        elif isinstance(m, annotated_types.MinLen):
            min_len = m.min_length
        elif isinstance(m, annotated_types.MaxLen):
            max_len = m.max_length
        else:
            pattern = getattr(m, "pattern", None)
            if pattern is not None:
                texts.append(f"pattern `{pattern}`")
            if getattr(m, "allow_inf_nan", None) is False:
                texts.append("finite")
    bounds = []
    if lo is not None and hi is not None and not lo_open and not hi_open:
        bounds.append(f"{_num(lo)} to {_num(hi)}")
    else:
        if lo is not None:
            bounds.append(f"{'>' if lo_open else '≥'} {_num(lo)}")
        if hi is not None:
            bounds.append(f"{'<' if hi_open else '≤'} {_num(hi)}")
    def unit(n: int) -> str:
        return ("item" if base == "list" else "character") + ("" if n == 1 else "s")

    if min_len is not None or max_len is not None:
        if min_len == max_len:
            bounds.append(f"exactly {min_len} {unit(min_len)}")
        elif max_len is None:
            bounds.append(f"at least {min_len} {unit(min_len)}")
        elif min_len is None:
            bounds.append(f"at most {max_len} {unit(max_len)}")
        else:
            bounds.append(f"{min_len} to {max_len} {unit(max_len)}")
    return bounds + texts, discriminator


SCALARS = {bool: "boolean", int: "integer", float: "number", str: "string"}


def type_info(tp, meta=()) -> TypeInfo:
    """TypeInfo of an annotation; `meta` are constraints pydantic already split off (FieldInfo.metadata)."""
    meta = list(meta)
    while typing.get_origin(tp) is typing.Annotated:
        args = typing.get_args(tp)
        tp, meta = args[0], list(args[1:]) + meta
    origin = typing.get_origin(tp)
    if origin in (typing.Union, types.UnionType):
        members = [a for a in typing.get_args(tp) if a is not type(None)]
        if len(members) == 1:                        # X | None: optional-ness is the default's business
            return type_info(members[0], meta)
        base = "union"
    elif origin is typing.Literal:
        base = "literal"
    elif origin in (list, tuple, set):
        base = "list"
    elif origin is dict:
        base = "dict"
    else:
        base = "other"
    texts, disc = _constraints(meta, base)
    if base == "union":
        return TypeInfo("union", items=tuple(type_info(a) for a in members), constraints=tuple(texts),
                        discriminator=disc)
    if base == "literal":
        return TypeInfo("literal", values=typing.get_args(tp), constraints=tuple(texts))
    if base == "list":
        args = typing.get_args(tp) or (typing.Any,)
        return TypeInfo("list", items=(type_info(args[0]),), constraints=tuple(texts))
    if base == "dict":
        k, v = (typing.get_args(tp) or (typing.Any, typing.Any))
        return TypeInfo("dict", items=(type_info(k), type_info(v)), constraints=tuple(texts))
    if isinstance(tp, type) and issubclass(tp, BaseModel):
        return TypeInfo("model", name=tp.__name__, model=tp, constraints=tuple(texts))
    return TypeInfo("scalar", name=SCALARS.get(tp, "any"), constraints=tuple(texts))


# ----------------------------------------------------------------------------------------------- defaults


def yaml_value(value) -> str:
    """Default value as it would be written in YAML."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return '"' + value.replace('"', '\\"') + '"'
    if isinstance(value, BaseModel):
        return "{}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(yaml_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k}: {yaml_value(v)}" for k, v in value.items()) + "}"
    return str(value)


def _default(info: FieldInfo) -> str | None:
    if info.is_required():
        return None
    if info.default is not PydanticUndefined:
        return yaml_value(info.default)
    if info.default_factory is not None:
        return yaml_value(info.default_factory())
    return None


# ----------------------------------------------------------------------------------------------- walk


def unit_from_name(key: str) -> str | None:
    return UNIT_SUFFIXES.get(key.lower().rsplit("_", 1)[-1])


def _field_doc(model: type[BaseModel], name: str, info: FieldInfo, parent: str) -> FieldDoc:
    key = info.alias or name
    owner = _declaring_class(model, name)
    tinfo = type_info(info.annotation, info.metadata)
    description, dsource = info.description, "field" if info.description else None
    if description is None:
        comments = _class_comments(owner).get(name, ())
        if comments:
            description, dsource = " ".join(comments), "comment"
    if description is None:
        objects = tinfo.models()
        if len(objects) == 1 and _docstring(objects[0].model):
            description, dsource = _first_paragraph(_docstring(objects[0].model)), "type"
    unit, usource = None, None
    extra = info.json_schema_extra if isinstance(info.json_schema_extra, dict) else {}
    if extra.get("unit"):
        unit, usource = str(extra["unit"]), "field"
    elif tinfo.numeric() and unit_from_name(key):
        unit, usource = unit_from_name(key), "name"
    return FieldDoc(key=key, path=f"{parent}.{key}" if parent else key, owner=f"{owner.__name__}.{name}",
                    type=tinfo, required=info.is_required(), default=_default(info), description=description,
                    description_source=dsource, unit=unit, unit_source=usource)


def _child_path(fdoc: FieldDoc, t: TypeInfo, suffix: str = "") -> list[tuple[str, TypeInfo]]:
    """(path, model TypeInfo) of the objects below a field: list -> `[]`, dict -> `.<key>`."""
    if t.kind == "model":
        return [(fdoc.path + suffix, t)]
    if t.kind == "list":
        return _child_path(fdoc, t.items[0], suffix + "[]")
    if t.kind == "dict":
        return _child_path(fdoc, t.items[1], suffix + ".<key>")
    out = []
    for member in t.items:
        out += _child_path(fdoc, member, suffix)
    return out


def _variant(model: type[BaseModel], discriminator: str | None) -> str | None:
    if discriminator is None or discriminator not in model.model_fields:
        return None
    values = typing.get_args(model.model_fields[discriminator].annotation)
    return f"{discriminator}: {values[0]}" if len(values) == 1 else None


def _discriminators(t: TypeInfo) -> str | None:
    if t.kind == "union" and t.discriminator:
        return t.discriminator
    for i in t.items:
        d = _discriminators(i)
        if d:
            return d
    return None


def document(model: type[BaseModel], root: str = "", prefix: str = "") -> list[Section]:
    """All sections of `model` in pre-order; object types in field types are linked to their section.

    `prefix` keeps anchors unique when several models are documented on one page.
    """
    sections: list[Section] = []

    def walk(m: type[BaseModel], path: str, variant: str | None, seen: tuple, chain: tuple):
        # chain: variants of this object and its parents, so that equal paths below variants get own anchors
        section = Section(path=path, model=m, anchor=anchor(path, "-".join(chain) or None, prefix),
                          depth=len(seen) - 1, variant=variant, chain=chain, doc=_docstring(m))
        sections.append(section)
        children = []
        for name, info in m.model_fields.items():
            fdoc = _field_doc(m, name, info, path)
            section.fields.append(fdoc)
            disc = _discriminators(fdoc.type)
            for child_path, t in _child_path(fdoc, fdoc.type):
                t.variant = _variant(t.model, disc)
                child_chain = chain + ((t.variant,) if t.variant else ())
                t.section = anchor(child_path, "-".join(child_chain) or None, prefix)
                children.append((t.model, child_path, t.variant, child_chain))
        for child, child_path, child_variant, child_chain in children:
            if child in seen:                         # recursive schema: link only
                continue
            walk(child, child_path, child_variant, seen + (child,), child_chain)

    walk(model, root, None, (model,), ())
    return sections


def anchor(path: str, variant: str | None = None, prefix: str = "") -> str:
    """Stable HTML anchor of a section."""
    text = prefix + (path or "top-level") + (f"-{variant}" if variant else "")
    text = text.replace("[]", "-list").replace("<key>", "key")
    return "f-" + re.sub(r"[^a-z0-9_]+", "-", text.lower()).strip("-")
