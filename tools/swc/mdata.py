# SPDX-License-Identifier: Apache-2.0
"""Values of a component data file: swc/<Swc>/<Swc>_data.m (RTE-03).

The data file is the one place where the calibration values of a software component are written. The Simulink
model describes the parameters (name, type, dimension, scaling) as ParameterReceiver ports, it never carries a
number; MATLAB runs the file into the model workspace (tools/swc/swc_load_data.m) and tools/rtegen reads the same
file to fill the parameter storage of the RTE. Reading it here, in plain Python without MATLAB and without Qt,
keeps the firmware build free of MATLAB and lets the Studio (TOOL-08) show the same values.

Understood is the assignment subset the old MBDT data files use
(<mbdt-projects>/01_SimulinkEvalProjects/S32K144_basic_application_data.m):

    name = 3;                       whole numbers and floating point, with sign and exponent
    group.name = 0.5;               struct fields (the name is then "group.name")
    table = [1 2 3];                row vector, separated by spaces or commas
    mask  = [0, 1, 2];
    map = [1 2 3; 4 5 6];           matrix, rows separated by ';' (RTE-26: a map, one row per X breakpoint);
                                    the values are kept row by row, Entry.shape gives (rows, columns)
    flag = true;                    true/false become 1/0
    id = hex2dec('6F1');            hex2dec / bin2dec with a literal text
    reg = bin2dec('00000110');
    alias = group.name;             reference to a name assigned earlier
    x = 1 + ...                     line continuation
        2;                          (the joined line still has to be one of the forms above)

Everything else (function calls such as run('Other.m'), clear, expressions) is remembered as unsupported and
ignored: an old data file stays readable, and only a parameter that really points at such a value is an error
with file, line and the text that was not understood. Statements after a % comment are dropped, a % inside
'text' is not a comment.

    python tools/swc/mdata.py swc/DemoSwc/DemoSwc_data.m            lists the values it understood
    python tools/swc/mdata.py swc/DemoSwc/DemoSwc_data.m --json     name -> list of numbers, for MATLAB
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

NAME = r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*"
ASSIGNMENT = re.compile(rf"^\s*({NAME})\s*=\s*(.*)$", re.DOTALL)
NUMBER = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")
DEC_CALL = re.compile(r"^(hex2dec|bin2dec)\s*\(\s*'([0-9A-Fa-f]+)'\s*\)$")
REFERENCE = re.compile(rf"^{NAME}$")


class MDataError(Exception):
    """A value was asked for that the data file does not hold or that was not understood."""


@dataclass
class Entry:
    """One assignment of the data file."""
    name: str
    line: int
    values: list | None = None   # numbers of the value, None: not understood
    text: str = ""               # right hand side as written (for messages)
    shape: tuple | None = None   # RTE-26: (rows, columns) of a bracketed value, None for a plain number


@dataclass
class MData:
    file: Path
    entries: dict = field(default_factory=dict)   # name -> Entry, in file order

    @property
    def names(self) -> list:
        """Names with a value this module understood."""
        return [name for name, e in self.entries.items() if e.values is not None]

    def get(self, name: str, where: str = "") -> list:
        """Values of one name; raises MDataError with file, line and reason when it cannot be used."""
        prefix = f"{where}: " if where else ""
        entry = self.entries.get(name)
        if entry is None:
            known = ", ".join(self.names[:8]) or "nothing"
            raise MDataError(f"{prefix}{name} is not set in {self.file.as_posix()} (it knows: {known})")
        if entry.values is None:
            raise MDataError(f"{prefix}{self.file.as_posix()}:{entry.line}: {name} = {entry.text} is not a value "
                             f"this reader understands (numbers, vectors, hex2dec/bin2dec, true/false, "
                             f"a name assigned earlier)")
        return list(entry.values)

    def line_of(self, name: str) -> int | None:
        entry = self.entries.get(name)
        return entry.line if entry is not None else None


def _strip_comment(line: str) -> str:
    """Removes a % comment; a % inside 'text' does not start one."""
    out, quoted = [], False
    for i, ch in enumerate(line):
        if ch == "'" and (quoted or i == 0 or not (line[i - 1].isalnum() or line[i - 1] in ")]}_.'")):
            quoted = not quoted          # a leading ' starts text, a ' after a value is the transpose operator
        elif ch == "%" and not quoted:
            break
        out.append(ch)
    return "".join(out)


def _statements(text: str):
    """Yields (statement, line number) with continuations joined and comments removed."""
    pending, first = "", 0
    for number, raw in enumerate(text.splitlines(), start=1):
        line = _strip_comment(raw).rstrip()
        if not pending:
            first = number
        if line.endswith("..."):
            pending += line[:-3]
            continue
        pending += line
        statement = pending.strip()
        pending = ""
        # several statements may share a line; ',' only separates outside brackets and text
        for part in _split_statements(statement):
            if part:
                yield part, first


def _split_statements(statement: str):
    parts, depth, quoted, current = [], 0, False, ""
    for i, ch in enumerate(statement):
        if ch == "'" and (quoted or i == 0 or not (statement[i - 1].isalnum() or statement[i - 1] in ")]}_.'")):
            quoted = not quoted
        if not quoted:
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            elif ch in ";," and depth == 0:
                parts.append(current.strip())
                current = ""
                continue
        current += ch
    parts.append(current.strip())
    return parts


def _vector(text: str) -> list | None:
    """Numbers of a row vector [1 2 3] or, row by row, of a matrix [1 2; 3 4]; None when it is neither."""
    inner = text[1:-1].strip()
    if not inner:
        return []
    if ";" in inner:
        shape = _matrix_shape(text)      # RTE-26: a map (Kennfeld), rows of equal length
        if shape is None:
            return None
        return [v for row in inner.split(";") if row.strip() for v in _vector("[" + row + "]")]
    values = []
    for item in re.split(r"[\s,]+", inner):
        if not NUMBER.match(item):
            return None
        values.append(_number(item))
    return values


def _matrix_shape(text: str) -> tuple | None:
    """(rows, columns) of a bracketed value [1 2; 3 4] (RTE-26); a row vector is (1, n), None if not rectangular."""
    text = text.strip()
    if not (text.startswith("[") and text.endswith("]")):
        return None
    rows = [row for row in text[1:-1].split(";") if row.strip()]
    if not rows:
        return (0, 0)
    counts = []
    for row in rows:
        items = [item for item in re.split(r"[\s,]+", row.strip()) if item]
        if not all(NUMBER.match(item) for item in items):
            return None
        counts.append(len(items))
    return (len(rows), counts[0]) if len(set(counts)) == 1 else None


def _number(text: str):
    value = float(text)
    return int(value) if value.is_integer() and "." not in text and "e" not in text.lower() else value


def _value(text: str, data: "MData"):
    """Numbers of one right hand side; None when this reader does not understand it."""
    text = text.strip()
    if not text:
        return None
    if NUMBER.match(text):
        return [_number(text)]
    if text in ("true", "false"):
        return [1 if text == "true" else 0]
    if text.startswith("[") and text.endswith("]"):
        return _vector(text)
    call = DEC_CALL.match(text)
    if call is not None:
        return [int(call.group(2), 16 if call.group(1) == "hex2dec" else 2)]
    negated = text[1:].strip() if text.startswith("-") else None
    if negated is not None and REFERENCE.match(negated):
        values = data.entries.get(negated)
        return [-v for v in values.values] if values is not None and values.values is not None else None
    if REFERENCE.match(text):
        entry = data.entries.get(text)
        return list(entry.values) if entry is not None and entry.values is not None else None
    return None


def parse(text: str, file: Path) -> MData:
    data = MData(Path(file))
    for statement, line in _statements(text):
        match = ASSIGNMENT.match(statement)
        if match is None:
            continue                     # clear, run('...'), a call: not an assignment, nothing to read
        name, rhs = match.group(1), match.group(2).strip()
        entry = Entry(name, line, _value(rhs, data), rhs)
        if entry.values is not None:
            known = data.entries.get(rhs)
            entry.shape = known.shape if known is not None else _matrix_shape(rhs)   # RTE-26
        data.entries[name] = entry
    return data


def load(file: Path | str) -> MData:
    file = Path(file)
    try:
        text = file.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise MDataError(f"cannot read the data file: {exc}") from exc
    return parse(text, file)


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("file", type=Path, help="<Swc>_data.m")
    parser.add_argument("--json", action="store_true", help="name -> list of numbers (for MATLAB)")
    parser.add_argument("-o", "--output", type=Path, help="write the JSON here instead of stdout")
    args = parser.parse_args(argv)
    try:
        data = load(args.file)
    except MDataError as exc:
        print(f"mdata: error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        text = json.dumps({name: data.entries[name].values for name in data.names}, indent=2) + "\n"
        if args.output:
            args.output.write_text(text, encoding="utf-8", newline="\n")
        else:
            sys.stdout.write(text)
        return 0
    for name, entry in data.entries.items():
        if entry.values is None:
            print(f"{args.file}:{entry.line}: {name} = {entry.text}  (not understood)", file=sys.stderr)
        else:
            print(f"{name} = {entry.values}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
