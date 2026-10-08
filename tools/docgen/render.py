# SPDX-License-Identifier: Apache-2.0
"""Markdown rendering of the field reference (DOC-03). Deterministic: same sections, byte-identical text."""

from __future__ import annotations

import re

from .schema import FieldDoc, Section, TypeInfo

MISSING_DESCRIPTION = "_missing in schema_"
MISSING_UNIT = "_missing_"
HEADER = ("Field", "Type", "Unit", "Required", "Default", "Allowed values", "Description")


def cell(text: str) -> str:
    """Text that is safe inside a Markdown table cell."""
    return " ".join(text.split()).replace("|", "\\|")


def prose(text: str) -> str:
    """Schema text as Markdown: `<` outside code spans is escaped, so `<part>.yaml` is not taken for HTML."""
    parts = text.split("`")
    if len(parts) % 2 == 0:                          # unbalanced backticks: treat everything as text
        return text.replace("<", "&lt;")
    return "`".join(p if i % 2 else p.replace("<", "&lt;") for i, p in enumerate(parts))


def paragraphs(doc: str) -> list[str]:
    """Docstring as Markdown lines: paragraphs joined to one line, `- ` items kept as a list."""
    out = []
    for paragraph in doc.split("\n\n"):
        items: list[str] = []
        for line in paragraph.splitlines():
            line = line.strip()
            if line.startswith("- ") or not items:
                items.append(line)
            else:
                items[-1] += " " + line
        out += [prose(" ".join(i.split())) for i in items]
        out.append("")
    return out


def _value(v) -> str:
    if isinstance(v, bool):
        return "`true`" if v else "`false`"
    if isinstance(v, str):
        return f"`{v}`"
    return f"`{v!r}`"


def type_text(t: TypeInfo) -> str:
    if t.kind == "scalar":
        return t.name
    if t.kind == "literal":
        if all(isinstance(v, str) for v in t.values):
            return "string"
        if all(isinstance(v, int) and not isinstance(v, bool) for v in t.values):
            return "integer"
        return "value"
    if t.kind == "model":
        label = f"object ({t.variant})" if t.variant else "object"
        return f"[{label}](#{t.section})" if t.section else label
    if t.kind == "list":
        return f"list of {type_text(t.items[0])}"
    if t.kind == "dict":
        return f"map of {type_text(t.items[0])} to {type_text(t.items[1])}"
    members = " or ".join(type_text(m) for m in t.items)
    if t.discriminator:
        return f"one of {members}, selected by `{t.discriminator}`"
    return members


def allowed(t: TypeInfo, prefix: str = "") -> list[str]:
    """Constraints and enumerations, one entry per level of the type (`item:`, `key:`, `value:` below)."""
    own = list(t.constraints)
    if t.kind == "literal":
        own.append(", ".join(_value(v) for v in t.values))
    out = [prefix + ", ".join(own)] if own else []
    if t.kind == "list":
        out += allowed(t.items[0], prefix + "item: ")
    elif t.kind == "dict":
        out += allowed(t.items[0], prefix + "key: ")
        out += allowed(t.items[1], prefix + "value: ")
    elif t.kind == "union":
        for m in t.items:
            out += allowed(m, prefix)
    return out


REPEAT_WORDS = 15                                    # a description this long, repeated on a page, is written once


def row(f: FieldDoc, after_r1: bool = False, seen: dict | None = None, anchor: str = "") -> str:
    unit = f.unit if f.unit else (MISSING_UNIT if f.numeric else "")
    description = prose(f.description) if f.description is not None else MISSING_DESCRIPTION
    if seen is not None and f.description is not None and len(f.description.split()) >= REPEAT_WORDS:
        first = seen.setdefault(f.description, (f.path, anchor))     # DOC-76: the same text again becomes a pointer
        if first != (f.path, anchor):
            description = f"Same as [`{first[0]}`](#{first[1]})."
    cells = (
        f"`{f.path}`",
        type_text(f.type),
        unit,
        "yes" if f.required else "no",
        f"`{f.default}`" if f.default is not None else "",
        "; ".join(allowed(f.type)),
        ("**After R1.** " if after_r1 else "") + description,
    )
    return "| " + " | ".join(cell(c) for c in cells) + " |"


def title_of(s: Section) -> str:
    title = f"`{s.title}`" if s.path else s.title
    if s.chain:
        title += f" ({', '.join(s.chain)})"
    return title


def section(s: Section, level: int = 3, after_r1=lambda path: False, seen: dict | None = None) -> list[str]:
    lines = [f'<a id="{s.anchor}"></a>', "", "#" * level + " " + title_of(s), ""]
    if s.doc:
        lines += paragraphs(s.doc)
    lines.append("| " + " | ".join(HEADER) + " |")
    lines.append("|" + "---|" * len(HEADER))
    lines += [row(f, after_r1(f.path), seen, s.anchor) for f in s.fields]
    lines.append("")
    return lines


def _plain_type(f: FieldDoc) -> str:
    return re.sub(r"\(#f-[^)]*\)", "", type_text(f.type))


def signature(group: list[Section]) -> tuple:
    """What a section and everything below it says, independent of where it sits (paths relative, anchors dropped).
    Two objects with the same signature have the same table; the second one is written as a pointer (DOC-76)."""
    top = group[0].path
    return tuple((s.path[len(top):], s.variant, s.doc,
                  tuple((f.path[len(top):], _plain_type(f), f.unit, f.required, f.default, tuple(allowed(f.type)),
                         f.description) for f in s.fields)) for s in group)


def stub_same(s: Section, first: Section, below: list[Section]) -> list[str]:
    """Pointer to an identical table; the anchors of the objects below stay valid."""
    lines = [f'<a id="{s.anchor}"></a>', "", "### " + title_of(s), "",
             f"Same keys as {title_of(first)} ([table](#{first.anchor})); the paths start with `{s.path}` instead "
             f"of `{first.path}`.", ""]
    lines += [f'<a id="{x.anchor}"></a>' for x in below]
    return lines + [""] if below else lines


def stub_after_r1(s: Section, below: list[Section]) -> list[str]:
    """Short entry of an object that belongs to the power stage or motor control (after R1, DOC-76)."""
    keys = ", ".join(f"`{f.key}`" for f in s.fields)
    lines = [f'<a id="{s.anchor}"></a>', "", "### " + title_of(s) + " (after R1)", "",
             "After R1: part of the schema and checked by `tools/nxconfig`, but not of the first release; the table "
             f"is written when the object is. Keys: {keys}.", ""]
    lines += [f'<a id="{x.anchor}"></a>' for x in below]
    return lines + [""] if below else lines


def contents(sections: list[Section]) -> list[str]:
    """Nested list of links to the sections, indented by depth of the YAML path."""
    lines = []
    for s in sections:
        label = f"`{s.title}`" if s.path else s.title
        if s.chain:
            label += f" ({', '.join(s.chain)})"
        lines.append("    " * s.depth + f"- [{label}](#{s.anchor})")
    return lines
