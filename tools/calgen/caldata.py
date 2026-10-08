# SPDX-License-Identifier: Apache-2.0
"""Calibration dataset `nextbsw-cal 1`: read, write, resolve a variant, compare (TOOL-145, stage K2).

The store of calibration values of a project (doc/architecture/kalibrierung.md, decisions E2 a and E3 a): plain
YAML in Git, laid out so that a changed value is a changed line. No MATLAB, no Qt, and no knowledge of the software:
this module only knows the format. What a value must be (type, grid, limits, completeness, layout identifier) is
judged by tools/rtegen (apply_calibration_dataset) against the component descriptions - tools/calgen/calgen.py for
the command line, rtegen itself before it generates.

Layout of a dataset folder (normally projects/<p>/calibration/):

    dataset.yaml                    index: format, layout identifier, optional build_variant
    base/<Component>.cal.yaml       complete values, one file per component
    variants/<v>/<Component>.cal.yaml   delta: only values that differ from base

A component file:

    format: nextbsw-cal 1
    component: CalMapSwc
    values:
      MaxTorque_Nm:
        value: 250                  # physical value, as in <Swc>_data.m
        status: released            # draft | checked | released
        author: "A. Person"
        reason: "measured on the test bench"
      TorqueLimit_Nm:               # a map: one row per X breakpoint, one value per Y breakpoint
        value:
          - [120, 110, 90]
          - [150, 140, 120]

A variant entry replaces the whole base entry (value, status, author, reason); a variant cannot add a value that base
does not have. Files written by this module are regenerated in a fixed order; comments are not kept, except the row
comments of maps, which the writer produces itself. `patch_component` (TOOL-198) is the other way: it changes only
the lines of the changed entries of an existing file and keeps every comment.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

FORMAT = "nextbsw-cal 1"
STATUSES = ("draft", "checked", "released")
DATASET_DIR = "calibration"          # the folder next to project.yaml
INDEX_FILE = "dataset.yaml"
BASE = "base"
VARIANTS_DIR = "variants"
SUFFIX = ".cal.yaml"
ENTRY_KEYS = ("value", "status", "author", "reason")
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
VARIANT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
LAYOUT_RE = re.compile(r"^[0-9a-f]{8}$")


@dataclass
class Finding:
    """One message about a dataset, with file and line where there is one."""
    severity: str                    # error | warning
    text: str
    file: Path | None = None
    line: int | None = None

    def __str__(self) -> str:
        where = self.file.as_posix() if self.file is not None else ""
        if where and self.line:
            where += f":{self.line}"
        return f"{where}: {self.severity}: {self.text}" if where else f"{self.severity}: {self.text}"


class CalDataError(Exception):
    """The dataset cannot be read; carries every finding (not only the first)."""

    def __init__(self, findings: list[Finding]):
        self.findings = findings
        super().__init__("; ".join(str(f) for f in findings if f.severity == "error"))


@dataclass
class Entry:
    """Value of one calibration parameter with its status, author and reason."""
    value: object                    # number | text (value table) | list | list of rows (map)
    status: str = "draft"
    author: str = ""
    reason: str = ""
    file: Path | None = None
    line: int | None = None
    origin: str = ""                 # base or the variant it came from (resolve)

    @property
    def where(self) -> str:
        if self.file is None:
            return self.origin or "dataset"
        return f"{self.file.as_posix()}:{self.line}" if self.line else self.file.as_posix()

    def same_value(self, other: "Entry") -> bool:
        return normalise(self.value) == normalise(other.value)


@dataclass
class Part:
    """The base dataset, one variant (only its delta), or a resolved variant (base plus delta)."""
    name: str
    components: dict = field(default_factory=dict)      # component -> {element -> Entry}, in file order
    files: dict = field(default_factory=dict)           # component -> file it came from

    def entry(self, component: str, element: str) -> Entry | None:
        return self.components.get(component, {}).get(element)

    def count(self) -> int:
        return sum(len(v) for v in self.components.values())


@dataclass
class Dataset:
    root: Path
    layout: str = ""
    build_variant: str = ""
    base: Part = field(default_factory=lambda: Part(BASE))
    variants: dict = field(default_factory=dict)        # name -> Part (delta)

    @property
    def index_file(self) -> Path:
        return self.root / INDEX_FILE

    def names(self) -> list[str]:
        return [BASE, *self.variants]

    def part(self, name: str) -> Part:
        if name == BASE:
            return self.base
        if name not in self.variants:
            raise CalDataError([Finding("error", f"variant {name} does not exist (known: "
                                                 f"{', '.join(self.names())})", self.root)])
        return self.variants[name]

    def resolve(self, name: str = BASE) -> Part:
        """Complete values of base or of a variant: base overlaid with the delta of the variant."""
        delta = self.part(name)
        result = Part(name)
        for comp, entries in self.base.components.items():
            result.components[comp] = {e: _copy(entry, BASE) for e, entry in entries.items()}
            result.files[comp] = self.base.files.get(comp)
        if name != BASE:
            for comp, entries in delta.components.items():
                target = result.components.setdefault(comp, {})
                for element, entry in entries.items():
                    target[element] = _copy(entry, name)
        return result

    def files_of(self, name: str = BASE) -> list[Path]:
        """Index and component files that make up base or a variant (build inputs)."""
        files = [self.index_file, *self.base.files.values()]
        if name != BASE:
            files += list(self.part(name).files.values())
        return files


def _copy(entry: Entry, origin: str) -> Entry:
    return Entry(entry.value, entry.status, entry.author, entry.reason, entry.file, entry.line, entry.origin or origin)


def normalise(value):
    """Value with integral floats as int, so 250 and 250.0 compare equal (a DCM file writes either)."""
    if isinstance(value, list):
        return [normalise(v) for v in value]
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, float) and math.isfinite(value) and value.is_integer() and abs(value) < 1e15:
        return int(value)
    return value


# ---- reading -----------------------------------------------------------------------------------------------------

def _lines(text: str) -> dict:
    """Line (1-based) of every key of the mapping `values` of a component file, and of the top-level keys."""
    lines = {}
    try:
        node = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError:
        return lines
    if not isinstance(node, yaml.MappingNode):
        return lines
    for key, value in node.value:
        lines[(key.value,)] = key.start_mark.line + 1
        if key.value == "values" and isinstance(value, yaml.MappingNode):
            for k2, _v2 in value.value:
                lines[("values", k2.value)] = k2.start_mark.line + 1
    return lines


def _read_yaml(file: Path, findings: list) -> tuple[object, dict]:
    try:
        text = file.read_text(encoding="utf-8")
    except OSError as e:
        findings.append(Finding("error", f"cannot read: {e}", file))
        return None, {}
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        findings.append(Finding("error", f"no valid YAML: {getattr(e, 'problem', e)}", file,
                                mark.line + 1 if mark else None))
        return None, {}
    return data, _lines(text)


def _check_value(value, depth: int = 0) -> bool:
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return not isinstance(value, float) or math.isfinite(value)
    if isinstance(value, str):
        return depth < 2 and bool(value)
    if isinstance(value, list) and depth < 2 and value:
        return all(_check_value(v, depth + 1) for v in value)
    return False


def read_component_file(file: Path, part: Part, findings: list):
    data, lines = _read_yaml(file, findings)
    if data is None:
        if not findings or findings[-1].file != file:
            findings.append(Finding("error", "empty file", file))
        return
    if not isinstance(data, dict):
        findings.append(Finding("error", "a component file is a mapping (format, component, values)", file, 1))
        return
    for key in data:
        if key not in ("format", "component", "values"):
            findings.append(Finding("error", f"unknown key '{key}' (format, component, values)", file,
                                    lines.get((key,))))
    if data.get("format") != FORMAT:
        findings.append(Finding("error", f"format must be '{FORMAT}', not {data.get('format')!r}", file,
                                lines.get(("format",))))
        return
    component = data.get("component")
    expected = file.name[:-len(SUFFIX)]
    if component != expected:
        findings.append(Finding("error", f"component must be {expected} (the file name), not {component!r}", file,
                                lines.get(("component",))))
        return
    values = data.get("values") or {}
    if not isinstance(values, dict):
        findings.append(Finding("error", "values is a mapping: parameter -> {value, status, author, reason}", file,
                                lines.get(("values",))))
        return
    entries = {}
    for element, raw in values.items():
        line = lines.get(("values", element))
        if not isinstance(element, str) or not NAME_RE.fullmatch(element):
            findings.append(Finding("error", f"{element!r} is no parameter name", file, line))
            continue
        if not isinstance(raw, dict) or "value" not in raw:
            findings.append(Finding("error", f"{element}: needs value (and status, author, reason)", file, line))
            continue
        unknown = [k for k in raw if k not in ENTRY_KEYS]
        if unknown:
            findings.append(Finding("error", f"{element}: unknown key(s) {', '.join(map(str, unknown))} "
                                             f"({', '.join(ENTRY_KEYS)})", file, line))
            continue
        if not _check_value(raw["value"]):
            findings.append(Finding("error", f"{element}: value must be a number, a text of the value table, a list "
                                             f"or a list of rows, not {raw['value']!r}", file, line))
            continue
        status = raw.get("status", "draft")
        if status not in STATUSES:
            findings.append(Finding("error", f"{element}: status must be one of {', '.join(STATUSES)}, not "
                                             f"{status!r}", file, line))
            continue
        texts = {k: raw.get(k, "") for k in ("author", "reason")}
        bad = [k for k, v in texts.items() if v is not None and not isinstance(v, str)]
        if bad:
            findings.append(Finding("error", f"{element}: {', '.join(bad)} must be text", file, line))
            continue
        entries[element] = Entry(raw["value"], status, texts["author"] or "", texts["reason"] or "", file, line,
                                 part.name)
    part.components[component] = entries
    part.files[component] = file


def _read_part(folder: Path, name: str, findings: list) -> Part:
    part = Part(name)
    if not folder.is_dir():
        return part
    for file in sorted(folder.iterdir()):
        if file.is_file() and file.name.endswith(SUFFIX):
            read_component_file(file, part, findings)
        elif file.is_file():
            findings.append(Finding("warning", f"not a component file (*{SUFFIX}), ignored", file))
    return part


def load(root: Path) -> Dataset:
    """Reads a dataset folder; raises CalDataError with every format finding."""
    root = Path(root)
    findings: list[Finding] = []
    dataset = Dataset(root)
    index = root / INDEX_FILE
    if not index.is_file():
        raise CalDataError([Finding("error", f"no {INDEX_FILE} (format, layout): not a calibration dataset "
                                             f"(tools/calgen/calgen.py migrate creates one)", root)])
    data, lines = _read_yaml(index, findings)
    if isinstance(data, dict):
        for key in data:
            if key not in ("format", "layout", "build_variant"):
                findings.append(Finding("error", f"unknown key '{key}' (format, layout, build_variant)", index,
                                        lines.get((key,))))
        if data.get("format") != FORMAT:
            findings.append(Finding("error", f"format must be '{FORMAT}'", index, lines.get(("format",))))
        layout = str(data.get("layout") or "")
        if layout and not LAYOUT_RE.fullmatch(layout):
            findings.append(Finding("error", f"layout {layout!r} is no layout identifier (8 hex digits of "
                                             f"RteCalibration.json)", index, lines.get(("layout",))))
        dataset.layout = layout
        dataset.build_variant = str(data.get("build_variant") or "")
    elif not findings:
        findings.append(Finding("error", "the index is a mapping (format, layout, build_variant)", index, 1))
    dataset.base = _read_part(root / BASE, BASE, findings)
    variants = root / VARIANTS_DIR
    if variants.is_dir():
        for folder in sorted(p for p in variants.iterdir() if p.is_dir()):
            if not VARIANT_RE.fullmatch(folder.name) or folder.name == BASE:
                findings.append(Finding("error", f"{folder.name!r} is no variant name", folder))
                continue
            dataset.variants[folder.name] = _read_part(folder, folder.name, findings)
    for name, delta in dataset.variants.items():
        if any(f.severity == "error" for f in findings):
            break                        # an entry of base with a format error would look missing here
        for comp, entries in delta.components.items():
            for element, entry in entries.items():
                if dataset.base.entry(comp, element) is None:
                    findings.append(Finding("error", f"{comp}.{element}: a variant only changes values of base, "
                                                     f"and base has none", entry.file, entry.line))
    if dataset.build_variant and dataset.build_variant not in dataset.variants:
        findings.append(Finding("error", f"build_variant {dataset.build_variant} is no variant (known: "
                                         f"{', '.join(dataset.variants) or 'none'})", index,
                                lines.get(("build_variant",))))
    if any(f.severity == "error" for f in findings):
        raise CalDataError(findings)
    return dataset


# ---- writing -----------------------------------------------------------------------------------------------------

def format_number(v) -> str:
    v = normalise(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        text = repr(v)
        return text if any(c in text for c in ".eE") else text + ".0"
    return quote(str(v))


def quote(text: str) -> str:
    """A text as YAML (double-quoted JSON string, valid YAML)."""
    return json.dumps(text, ensure_ascii=False)


def render_value(value, indent: str, row_labels: list | None = None) -> str:
    """`value:` of an entry: scalar and list on one line, a map one row per line (a changed cell is one line)."""
    if isinstance(value, list) and value and isinstance(value[0], list):
        lines = [f"{indent}value:"]
        rows = [f"{indent}  - [{', '.join(format_number(v) for v in row)}]" for row in value]
        width = max(len(r) for r in rows)
        for i, text in enumerate(rows):
            if row_labels and i < len(row_labels) and row_labels[i]:
                text = f"{text.ljust(width)}    # {row_labels[i]}"
            lines.append(text)
        return "\n".join(lines)
    if isinstance(value, list):
        return f"{indent}value: [{', '.join(format_number(v) for v in value)}]"
    return f"{indent}value: {format_number(value)}"


def render_component(component: str, entries: dict, part: str, row_labels: dict | None = None) -> str:
    """Text of one component file; `row_labels`: element -> comment per map row (the X breakpoint)."""
    what = "base dataset" if part == BASE else f"variant {part} (delta on base)"
    out = [f"# Calibration values of {component} - {what}. Format {FORMAT}, tools/calgen (TOOL-145).",
           f"format: {FORMAT}", f"component: {component}"]
    if not entries:
        out.append("values: {}")
        return "\n".join(out) + "\n"
    out.append("values:")
    for element, entry in entries.items():
        out.append(f"  {element}:")
        out.append(render_value(entry.value, "    ", (row_labels or {}).get(element)))
        out.append(f"    status: {entry.status}")
        if entry.author:
            out.append(f"    author: {quote(entry.author)}")
        if entry.reason:
            out.append(f"    reason: {quote(entry.reason)}")
    return "\n".join(out) + "\n"


def _node_end(node) -> int:
    """Last line (0-based) of a node's own text: a block sequence ends with its last item, not at the next key."""
    if isinstance(node, yaml.SequenceNode) and not node.flow_style:
        return _node_end(node.value[-1]) if node.value else node.start_mark.line
    if isinstance(node, yaml.MappingNode) and not node.flow_style:
        return max((_node_end(v) for _k, v in node.value), default=node.start_mark.line)
    end = node.end_mark
    return end.line if end.column > 0 or end.line == node.start_mark.line else end.line - 1


def _entry_lines(element: str, entry: Entry, key_indent: str, indent: str, labels: list | None) -> list[str]:
    out = [f"{key_indent}{element}:", *render_value(entry.value, indent, labels).split("\n"),
           f"{indent}status: {entry.status}"]
    if entry.author:
        out.append(f"{indent}author: {quote(entry.author)}")
    if entry.reason:
        out.append(f"{indent}reason: {quote(entry.reason)}")
    return out


def _same_entries(data, entries: dict) -> bool:
    """The values of a parsed component file are exactly `entries` (value, status, author, reason)."""
    values = (data.get("values") if isinstance(data, dict) else None) or {}
    if not isinstance(values, dict) or set(values) != set(entries):
        return False
    for element, entry in entries.items():
        raw = values[element]
        if not isinstance(raw, dict) or normalise(raw.get("value")) != normalise(entry.value):
            return False
        if raw.get("status", "draft") != entry.status or (raw.get("author") or "") != entry.author \
                or (raw.get("reason") or "") != entry.reason or set(raw) - set(ENTRY_KEYS):
            return False
    return True


def patch_component(text: str, component: str, entries: dict, row_labels: dict | None = None) -> str | None:
    """The component file `text` changed to `entries` (element -> Entry), keeping every line that stays - comments
    included (TOOL-198: a value taken over from the ECU must not wipe the notes of the calibration engineer).

    Only the keys of an entry that change are rewritten (a one-line key keeps its trailing comment; a map's rows are
    rewritten with the row comments of the writer); a new entry goes behind the entry in front of it in `entries`,
    a removed entry leaves with its lines. Returns None when the file is not in the plain block form of this writer
    or the result would not read back as `entries`; the caller then renders the file anew (render_component)."""
    try:
        node = yaml.compose(text, Loader=yaml.SafeLoader)
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    if not isinstance(node, yaml.MappingNode) or not isinstance(data, dict) or data.get("component") != component:
        return None
    found = next(((k, v) for k, v in node.value if k.value == "values"), None)
    if found is None or not isinstance(found[1], yaml.MappingNode) or found[1].flow_style or not found[1].value:
        return None
    values_key, values_node = found
    old = data.get("values") or {}
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    labels = row_labels or {}
    edits: list = []                                   # (first line, end line exclusive, order, new lines)
    spans: dict = {}                                   # element -> (key line, last line)
    key_indent, indent = "  ", "    "
    for position, (k, v) in enumerate(values_node.value):
        if not isinstance(v, yaml.MappingNode) or v.flow_style or not v.value:
            return None
        key_indent = " " * k.start_mark.column
        indent = " " * v.value[0][0].start_mark.column
        spans[k.value] = (k.start_mark.line, _node_end(v), {sk.value: (sk, sv) for sk, sv in v.value})
    order = 0

    def edit(first: int, end: int, new: list[str]) -> None:
        nonlocal order
        edits.append((first, end, order, [line + "\n" for line in new]))
        order += 1

    names = list(entries)
    for element, entry in entries.items():
        if element not in spans:                      # new: behind the entry in front of it that the file has
            before = [spans[e][1] for e in names[:names.index(element)] if e in spans]
            at = (max(before) if before else values_key.start_mark.line) + 1
            edit(at, at, _entry_lines(element, entry, key_indent, indent, labels.get(element)))
            continue
        raw = old.get(element) if isinstance(old.get(element), dict) else {}
        _first, _last, keys = spans[element]
        ind = " " * next(iter(keys.values()))[0].start_mark.column
        wanted = {"value": render_value(entry.value, ind, labels.get(element)).split("\n"),
                  "status": [f"{ind}status: {entry.status}"],
                  "author": [f"{ind}author: {quote(entry.author)}"] if entry.author else [],
                  "reason": [f"{ind}reason: {quote(entry.reason)}"] if entry.reason else []}
        same = {"value": normalise(raw.get("value")) == normalise(entry.value),
                "status": raw.get("status", "draft") == entry.status,
                "author": (raw.get("author") or "") == entry.author,
                "reason": (raw.get("reason") or "") == entry.reason}
        after = spans[element][0]                     # line behind which a missing key goes
        for key in ENTRY_KEYS:
            if key in keys:
                sk, sv = keys[key]
                first, last = sk.start_mark.line, _node_end(sv)
                if not same[key]:
                    new = wanted[key]
                    if first == last == sv.end_mark.line and len(new) == 1:
                        line = lines[first]           # one line: keep what follows the value (a comment)
                        edit(first, first + 1, [line[:sk.start_mark.column] + new[0][len(ind):] +
                                                line[sv.end_mark.column:].rstrip("\n")])
                    else:
                        edit(first, last + 1, new)
                after = last
            elif not same[key] and wanted[key]:
                edit(after + 1, after + 1, wanted[key])
    for element, (first, last, _keys) in spans.items():
        if element not in entries:
            edit(first, last + 1, [])
    for first, end, _order, new in sorted(edits, key=lambda e: (e[0], e[2]), reverse=True):
        lines[first:end] = new
    result = "".join(lines)
    try:
        if not _same_entries(yaml.safe_load(result), entries):
            return None
    except yaml.YAMLError:
        return None
    return result


def render_index(layout: str, build_variant: str = "") -> str:
    out = [f"# Calibration dataset, format {FORMAT} (tools/calgen, TOOL-145). base/ holds every value, variants/<v>/",
           "# only what differs. layout binds the values to the software (RteCalibration.json of the build).",
           f"format: {FORMAT}", f"layout: {quote(layout)}"]      # quoted: 8 hex digits may read as a number
    if build_variant:
        out.append(f"build_variant: {build_variant}")
    return "\n".join(out) + "\n"


def write_text(file: Path, text: str) -> bool:
    """Writes only when the text changed; returns True when it did."""
    file.parent.mkdir(parents=True, exist_ok=True)
    if file.is_file() and file.read_text(encoding="utf-8") == text:
        return False
    file.write_text(text, encoding="utf-8", newline="\n")
    return True


def part_folder(root: Path, name: str) -> Path:
    return root / BASE if name == BASE else root / VARIANTS_DIR / name


def write_part(root: Path, part: Part, row_labels: dict | None = None) -> list[Path]:
    """Writes the component files of a part (an empty delta component removes its file); returns changed files.

    `row_labels`: component -> element -> comment per map row."""
    folder = part_folder(root, part.name)
    changed = []
    for component, entries in part.components.items():
        file = folder / f"{component}{SUFFIX}"
        if not entries and part.name != BASE:
            if file.is_file():
                file.unlink()
                changed.append(file)
            continue
        if write_text(file, render_component(component, entries, part.name, (row_labels or {}).get(component))):
            changed.append(file)
    return changed


def write_index(root: Path, layout: str, build_variant: str = "") -> bool:
    return write_text(root / INDEX_FILE, render_index(layout, build_variant))


# ---- comparing ---------------------------------------------------------------------------------------------------

@dataclass
class Change:
    component: str
    element: str
    what: str                        # added | removed | value | status | author | reason
    old: object = None
    new: object = None
    cells: list = field(default_factory=list)   # value: (index text, old, new) per changed cell

    def __str__(self) -> str:
        name = f"{self.component}.{self.element}"
        if self.what == "added":
            return f"+ {name}: {_show(self.new)}"
        if self.what == "removed":
            return f"- {name}: {_show(self.old)}"
        if self.what == "value" and self.cells:
            shown = ", ".join(f"{i}: {_show(a)} -> {_show(b)}" for i, a, b in self.cells[:8])
            more = f" (+{len(self.cells) - 8} more)" if len(self.cells) > 8 else ""
            return f"~ {name}: {shown}{more}"
        return f"~ {name} {self.what}: {_show(self.old)} -> {_show(self.new)}"


def _show(value) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(_show(v) for v in value) + "]"
    return format_number(value) if not isinstance(value, str) else value


def _cells(a, b, prefix="") -> list | None:
    """Changed cells of two values of the same shape, None when the shapes differ."""
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return None
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            sub = _cells(x, y, f"{prefix}[{i + 1}]")
            if sub is None:
                return None
            out += sub
        return out
    if isinstance(a, list) or isinstance(b, list):
        return None
    return [] if a == b else [(prefix or "value", a, b)]


def diff(a: Part, b: Part) -> list[Change]:
    """What changes from a to b: values (cell by cell where the shape stays), status, author, reason."""
    changes = []
    for comp in list(dict.fromkeys([*a.components, *b.components])):
        ea, eb = a.components.get(comp, {}), b.components.get(comp, {})
        for element in list(dict.fromkeys([*ea, *eb])):
            x, y = ea.get(element), eb.get(element)
            if x is None:
                changes.append(Change(comp, element, "added", new=y.value))
                continue
            if y is None:
                changes.append(Change(comp, element, "removed", old=x.value))
                continue
            va, vb = normalise(x.value), normalise(y.value)
            if va != vb:
                cells = _cells(va, vb)
                changes.append(Change(comp, element, "value", va, vb, cells or []))
            for key in ("status", "author", "reason"):
                if getattr(x, key) != getattr(y, key):
                    changes.append(Change(comp, element, key, getattr(x, key), getattr(y, key)))
    return changes
