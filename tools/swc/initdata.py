# SPDX-License-Identifier: Apache-2.0
"""Static reading of the init data of a component, without MATLAB (RTE-25): symbolic port dimensions.

An imported model often sizes a port with a variable of its old project, e.g. PortDimensions
`hvscmc.number_cell_voltages`. The component carries that data as <Swc>_init.m next to its model and, for an import,
in init_data/ (tools/studio/core/initgen.py). swc.yaml may name such an expression as the dimension of a port; the
generated code always has a number, and this module is where the number comes from - documented and checkable,
never in C code.

What is evaluated (and nothing else - MATLAB is not emulated):
  * the files <folder>/<Swc>_init.m and every *.m under <folder>/init_data/ (sorted), scripts only;
  * plain assignments `name = expr;` and `a.b.c = expr;` at the top level of a script (not inside if/for/while/
    switch/try/function) - an assignment inside such a block makes the name ambiguous;
  * `expr`: whole or decimal numbers, other assigned names, + - * / ^ and parentheses, the numeric casts
    double/single/int8..uint32 and Simulink.Parameter(value) (also as `x = Simulink.Parameter; x.Value = 96;`).
A name assigned twice with different values, read from a .mat file, a data dictionary or computed by a function is
not resolved: the caller gets None and the reason, and swc.yaml then has to carry the number (`dimension: 96`,
`dimension_source: hvscmc.number_cell_voltages`). For a Simulink component rtegen compares the dimension with the
ARXML Embedded Coder wrote, which MATLAB itself resolved.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

INIT_SUFFIX = "_init.m"
DATA_DIR = "init_data"
MAX_BYTES = 1_000_000               # a larger script is no hand-written init file; not read
EXPRESSION = re.compile(r"^[A-Za-z0-9_.+\-*/^() \t]+$")
NAME = re.compile(r"[A-Za-z]\w*(?:\s*\.\s*[A-Za-z]\w*)*")
BLOCK_OPEN = re.compile(r"^(if|for|parfor|while|switch|try|spmd|function|classdef)\b")
BLOCK_END = re.compile(r"^end\b")
ASSIGN = re.compile(r"^([A-Za-z]\w*(?:\s*\.\s*[A-Za-z]\w*)*)\s*=(?!=)\s*(.+)$")
CASTS = {"double", "single", "int8", "uint8", "int16", "uint16", "int32", "uint32", "Simulink.Parameter"}


@dataclass
class Assignment:
    value: str                      # right-hand side as written
    file: Path
    line: int
    top_level: bool


@dataclass
class Resolution:
    value: int | None               # None: not resolved without MATLAB
    why: str                        # where the value comes from, or why it does not
    file: Path | None = None
    line: int | None = None


def init_files(folder: Path | str, name: str) -> list[Path]:
    """The scripts that set up the data of component `name`: <Swc>_init.m, then init_data/**/*.m (sorted)."""
    folder = Path(folder)
    files = [folder / f"{name}{INIT_SUFFIX}"]
    data = folder / DATA_DIR
    if data.is_dir():
        files += sorted(p for p in data.rglob("*.m") if p.is_file())
    return [f for f in files if f.is_file()]


def _strip_comment(line: str) -> str:
    """The code of a MATLAB line without its % comment (a % inside a quoted text stays)."""
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"":
            quote = ch
        elif ch == "'" and (i == 0 or not (line[i - 1].isalnum() or line[i - 1] in "_.)]}'")):
            quote = ch            # a transpose after a name is no string
        elif ch == "%":
            return line[:i]
    return line


def _statements(text: str):
    """(line, code, depth) of the statements of a script; ... joins lines, ; and , separate statements."""
    depth, pending, first, block_comment = 0, "", 0, 0
    for number, raw in enumerate(text.splitlines(), 1):
        if raw.strip() == "%{":                           # block comment %{ ... %} (may nest)
            block_comment += 1
            continue
        if block_comment:
            block_comment -= raw.strip() == "%}"
            continue
        code = _strip_comment(raw).rstrip()
        if code.endswith("..."):
            pending += code[:-3] + " "
            first = first or number
            continue
        code = pending + code
        line = first or number
        pending, first = "", 0
        for part in re.split(r"[;,](?![^()\[\]{}]*[)\]}])", code):
            part = part.strip()
            if not part:
                continue
            if BLOCK_END.match(part):
                depth = max(0, depth - 1)
                continue
            yield line, part, depth
            if BLOCK_OPEN.match(part):
                if part.split()[0] in ("function", "classdef"):
                    return                    # a function file: nothing of it runs as a script
                depth += 1


def _norm(name: str) -> str:
    return re.sub(r"\s+", "", name)


def assignments(folder: Path | str, name: str) -> dict[str, list[Assignment]]:
    """Every assignment of the init scripts of component `name`, by the assigned name (a.b.c)."""
    found: dict[str, list[Assignment]] = {}
    for file in init_files(folder, name):
        try:
            if file.stat().st_size > MAX_BYTES:
                continue
            text = file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line, code, depth in _statements(text):
            m = ASSIGN.match(code)
            if m:
                found.setdefault(_norm(m.group(1)), []).append(Assignment(m.group(2).strip(), file, line, depth == 0))
    return found


class _Unresolved(Exception):
    pass


def _evaluate(expression: str, known: dict[str, list[Assignment]],
              seen: tuple[str, ...]) -> tuple[float, Assignment | None]:
    """Value of `expression`; the assignment the (outermost) name comes from, for the message."""
    text = expression.strip()
    if not EXPRESSION.match(text):
        raise _Unresolved(f"'{text}' is no plain arithmetic of numbers and names")
    names: dict[str, str] = {}

    def name_of(m: re.Match) -> str:
        key = f"_n{len(names)}"
        names[key] = _norm(m.group(0))
        return key
    code = NAME.sub(name_of, text).replace("^", "**")
    try:
        tree = ast.parse(code, mode="eval")
    except SyntaxError:
        raise _Unresolved(f"'{text}' cannot be read") from None
    origin: list[Assignment] = []

    def value(node) -> float:
        if isinstance(node, ast.Expression):
            return value(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            v = value(node.operand)
            return v if isinstance(node.op, ast.UAdd) else -v
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)):
            a, b = value(node.left), value(node.right)
            if isinstance(node.op, ast.Div) and b == 0:
                raise _Unresolved(f"'{text}' divides by zero")
            if isinstance(node.op, ast.Pow) and (abs(b) > 64 or abs(a) > 2 ** 32):
                raise _Unresolved(f"'{text}' is out of range")
            if isinstance(node.op, ast.Add):
                return a + b
            if isinstance(node.op, ast.Sub):
                return a - b
            if isinstance(node.op, ast.Mult):
                return a * b
            return a / b if isinstance(node.op, ast.Div) else a ** b
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and len(node.args) == 1 \
                and not node.keywords and names.get(node.func.id) in CASTS:
            return value(node.args[0])
        if isinstance(node, ast.Name) and node.id in names:
            v, where = _lookup(names[node.id], known, seen)
            if where is not None:
                origin.append(where)
            return v
        raise _Unresolved(f"'{text}' contains something other than numbers, names and + - * / ^")
    result = value(tree)
    return result, (origin[0] if len(origin) == 1 else None)


def _lookup(name: str, known: dict[str, list[Assignment]], seen: tuple[str, ...]) -> tuple[float, Assignment | None]:
    if name in seen:
        raise _Unresolved(f"{name} refers to itself")
    entries = known.get(name)
    bare = all(e.value.replace(" ", "") in ("Simulink.Parameter", "Simulink.Parameter()") for e in entries or [])
    if bare and f"{name}.Value" in known:
        name = f"{name}.Value"                    # x = Simulink.Parameter; x.Value = 96;
        entries = known[name]
    if not entries:
        raise _Unresolved(f"{name} is not assigned in the init scripts (a .mat file, a data dictionary or another "
                          f"script may set it - only MATLAB knows then)")
    if any(not e.top_level for e in entries):
        e = next(e for e in entries if not e.top_level)
        raise _Unresolved(f"{name} is assigned inside a block ({e.file.name}:{e.line}), its value depends on the run")
    values = []
    for e in entries:
        if e.value.replace(" ", "") in ("Simulink.Parameter", "Simulink.Parameter()"):
            continue
        values.append((_evaluate(e.value, known, seen + (name,))[0], e))
    if not values:
        raise _Unresolved(f"{name} has no value in the init scripts")
    if len({v for v, _ in values}) > 1:
        where = ", ".join(f"{e.file.name}:{e.line}" for _, e in values)
        raise _Unresolved(f"{name} is assigned different values ({where})")
    return values[-1][0], values[-1][1]


# ---- TOOL-143: Simulink.Signal objects of the init data (measurement marks), read without MATLAB

SIGNAL_CLASS = re.compile(r"^(?:Simulink|mpt|AUTOSAR4?|[A-Za-z]\w*)\.Signal\s*(?:\(\s*\))?$")
TEXT = re.compile(r"""^(?:'((?:[^']|'')*)'|"((?:[^"]|"")*)")$""")
SIGNAL_PROPERTIES = {"CoderInfo.StorageClass": "storage_class", "StorageClass": "storage_class",
                     "CoderInfo.CustomStorageClass": "custom_storage_class", "DataType": "data_type",
                     "Dimensions": "dimensions", "Unit": "unit", "DocUnits": "unit", "Description": "description"}


@dataclass
class SignalObject:
    """A Simulink.Signal object an init script creates (TOOL-143), with the properties it sets there."""
    name: str
    file: Path
    line: int
    storage_class: str = "Auto"
    custom_storage_class: str = ""
    data_type: str = "auto"
    dimensions: str = "-1"
    unit: str = ""
    description: str = ""

    @property
    def effective_storage_class(self) -> str:
        """The storage class that counts: the custom one if StorageClass says 'Custom'."""
        if self.storage_class == "Custom" and self.custom_storage_class:
            return self.custom_storage_class
        return self.storage_class


def _text_value(value: str) -> str | None:
    m = TEXT.match(value.strip())
    if not m:
        return None
    return m.group(1).replace("''", "'") if m.group(1) is not None else m.group(2).replace('""', '"')


def signal_objects(folder: Path | str, name: str) -> dict[str, SignalObject]:
    """Simulink.Signal objects (also of a package: mpt.Signal, ...) the init scripts of component `name` create at
    their top level, by name, with the properties set by plain assignments (`x.CoderInfo.StorageClass =
    'ExportedGlobal'`, `x.DataType = 'single'`, `x.Dimensions = 8`, `x.Unit`, `x.Description`). An object created
    or changed inside a block, or created twice, is left out - only MATLAB knows it then (swc_ports 'report')."""
    known = assignments(folder, name)
    found: dict[str, SignalObject] = {}
    for var, entries in known.items():
        if "." in var or not any(SIGNAL_CLASS.match(e.value) for e in entries):
            continue
        if len(entries) != 1 or not entries[0].top_level:
            continue
        found[var] = SignalObject(var, entries[0].file, entries[0].line)
    for var, entries in known.items():
        head, _, prop = var.partition(".")
        if head not in found or prop not in SIGNAL_PROPERTIES:
            continue
        if any(not e.top_level for e in entries):
            del found[head]
            continue
        value = entries[-1].value
        key = SIGNAL_PROPERTIES[prop]
        if key == "dimensions":
            text = value.strip()
        else:
            text = _text_value(value)
            if text is None:
                continue
        setattr(found[head], key, text)
    return found


def resolve(expression: str, folder: Path | str, name: str) -> Resolution:
    """Whole number >= 1 an expression of the init data of component `name` in `folder` stands for, or why not."""
    known = assignments(folder, name)
    files = init_files(folder, name)
    if not files:
        return Resolution(None, f"there is no {name}{INIT_SUFFIX} and no {DATA_DIR}/ next to swc.yaml")
    try:
        value, where = _evaluate(expression, known, ())
    except (_Unresolved, RecursionError) as exc:
        return Resolution(None, str(exc) if isinstance(exc, _Unresolved) else f"'{expression}' nests too deep")
    if float(value) != int(value) or int(value) < 1:
        return Resolution(None, f"'{expression}' is {value}, not a whole number >= 1")
    if where is None:
        return Resolution(int(value), f"'{expression}' = {int(value)}")
    return Resolution(int(value), f"'{expression}' = {int(value)} ({where.file.name}:{where.line})", where.file,
                      where.line)
