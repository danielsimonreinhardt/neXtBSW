"""Formal check of an A2L as tools/a2lgen writes it (RTE-26): syntax, structure and references, without a tool.

Not a validator for A2L in general: it knows the blocks and keywords a2lgen uses (and a few common neighbours) and
refuses anything else, so a change of the generator that leaves this subset is noticed in the host test before a
calibration tool refuses the file. Checked:

  - tokens: strings, numbers (decimal, hex, float), identifiers; /begin and /end pairs with the same keyword
  - per block: the fixed parameters in number and kind, allowed keywords with their arguments, allowed sub-blocks
  - per object: lower limit <= upper limit, the limits inside EXTENDED_LIMITS, the number of AXIS_DESCR fits
    VALUE/VAL_BLK/CURVE/MAP, COM_AXIS names an AXIS_PTS_REF and STD_AXIS does not, the record layout has the axis
    parts a STD_AXIS needs (and none for COM_AXIS), RAT_FUNC has COEFFS, TAB_VERB a COMPU_TAB_REF with as many
    pairs as it announces
  - references: conversion methods, record layouts, value tables, axis points, the objects of a FUNCTION; names
    unique within their kind (CHARACTERISTIC, AXIS_PTS and MEASUREMENT share one name space)

Usage: python tools/a2lgen/a2lcheck.py app.ecu.a2l      (exit code 1 and one line per finding)
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

TOKEN = re.compile(r'\s+|/\*.*?\*/|//[^\n]*|"(?:[^"\\]|\\.)*"|/begin\b|/end\b|/include\b|[^\s"]+', re.S)
NUMBER = re.compile(r"^[+-]?(0[xX][0-9A-Fa-f]+|(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?)$")
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\[\]]*$")

DATATYPES = {"UBYTE", "SBYTE", "UWORD", "SWORD", "ULONG", "SLONG", "A_UINT64", "A_INT64", "FLOAT32_IEEE",
             "FLOAT64_IEEE"}
# argument kinds: s string, n number, i identifier, d datatype, or a set of allowed words
S, N, I, D = "s", "n", "i", "d"
INDEX_MODES = {"ROW_DIR", "COLUMN_DIR", "ALTERNATE_CURVES", "ALTERNATE_WITH_X", "ALTERNATE_WITH_Y"}
ADDR_TYPES = {"DIRECT", "PBYTE", "PWORD", "PLONG"}

# block keyword -> (fixed parameters, {keyword: arguments}, allowed sub-blocks)
BLOCKS = {
    "PROJECT": ([I, S], {}, {"HEADER", "MODULE"}),
    "HEADER": ([S], {"VERSION": [S], "PROJECT_NO": [I]}, set()),
    "MODULE": ([I, S], {}, {"MOD_PAR", "MOD_COMMON", "IF_DATA", "CHARACTERISTIC", "AXIS_PTS", "MEASUREMENT",
                            "COMPU_METHOD", "COMPU_VTAB", "RECORD_LAYOUT", "FUNCTION", "A2ML"}),
    "MOD_PAR": ([S], {"EPK": [S], "ADDR_EPK": [N], "VERSION": [S], "SUPPLIER": [S], "CUSTOMER": [S],
                      "ECU": [S], "CPU_TYPE": [S]}, {"MEMORY_SEGMENT"}),
    "MOD_COMMON": ([S], {"BYTE_ORDER": [{"MSB_FIRST", "MSB_LAST"}], "ALIGNMENT_BYTE": [N], "ALIGNMENT_WORD": [N],
                         "ALIGNMENT_LONG": [N], "ALIGNMENT_INT64": [N], "ALIGNMENT_FLOAT32_IEEE": [N],
                         "ALIGNMENT_FLOAT64_IEEE": [N], "DEPOSIT": [{"ABSOLUTE", "DIFFERENCE"}]}, set()),
    "COMPU_METHOD": ([I, S, {"IDENTICAL", "LINEAR", "RAT_FUNC", "TAB_INTP", "TAB_NOINTP", "TAB_VERB", "FORM"}, S, S],
                     {"COEFFS": [N] * 6, "COEFFS_LINEAR": [N, N], "COMPU_TAB_REF": [I], "REF_UNIT": [I]}, set()),
    # the value/text pairs after the fixed parameters are read apart (_check_block)
    "COMPU_VTAB": ([I, S, {"TAB_VERB"}, N], {"DEFAULT_VALUE": [S]}, set()),
    "RECORD_LAYOUT": ([I], {"FNC_VALUES": [N, D, INDEX_MODES, ADDR_TYPES],
                            "AXIS_PTS_X": [N, D, {"INDEX_INCR", "INDEX_DECR"}, ADDR_TYPES],
                            "AXIS_PTS_Y": [N, D, {"INDEX_INCR", "INDEX_DECR"}, ADDR_TYPES],
                            "FIX_NO_AXIS_PTS_X": [N], "FIX_NO_AXIS_PTS_Y": [N],
                            "NO_AXIS_PTS_X": [N, D], "NO_AXIS_PTS_Y": [N, D]}, set()),
    "CHARACTERISTIC": ([I, S, {"VALUE", "VAL_BLK", "CURVE", "MAP", "ASCII"}, N, I, N, I, N, N],
                       {"NUMBER": [N], "PHYS_UNIT": [S], "EXTENDED_LIMITS": [N, N], "FORMAT": [S],
                        "READ_ONLY": [], "MATRIX_DIM": [N], "DISPLAY_IDENTIFIER": [I]},
                       {"AXIS_DESCR", "IF_DATA"}),
    "AXIS_DESCR": ([{"STD_AXIS", "COM_AXIS", "FIX_AXIS", "RES_AXIS", "CURVE_AXIS"}, I, I, N, N, N],
                   {"AXIS_PTS_REF": [I], "PHYS_UNIT": [S], "EXTENDED_LIMITS": [N, N], "FORMAT": [S],
                    "READ_ONLY": []}, set()),
    "AXIS_PTS": ([I, S, N, I, I, N, I, N, N, N],
                 {"PHYS_UNIT": [S], "EXTENDED_LIMITS": [N, N], "FORMAT": [S], "READ_ONLY": [],
                  "DISPLAY_IDENTIFIER": [I]}, {"IF_DATA"}),
    "MEASUREMENT": ([I, S, D, I, N, N, N, N],
                    {"ECU_ADDRESS": [N], "MATRIX_DIM": [N], "ARRAY_SIZE": [N], "PHYS_UNIT": [S], "FORMAT": [S],
                     "BIT_MASK": [N], "READ_WRITE": [], "DISPLAY_IDENTIFIER": [I], "ECU_ADDRESS_EXTENSION": [N],
                     "BYTE_ORDER": [{"MSB_FIRST", "MSB_LAST"}], "DISCRETE": [], "MAX_REFRESH": [N, N],
                     "ERROR_MASK": [N], "SYMBOL_LINK": [S, N]}, {"IF_DATA"}),
    "FUNCTION": ([I, S], {"FUNCTION_VERSION": [S]},
                 {"DEF_CHARACTERISTIC", "REF_CHARACTERISTIC", "IN_MEASUREMENT", "OUT_MEASUREMENT",
                  "LOC_MEASUREMENT", "SUB_FUNCTION"}),
}
LISTS = {"DEF_CHARACTERISTIC", "REF_CHARACTERISTIC", "IN_MEASUREMENT", "OUT_MEASUREMENT", "LOC_MEASUREMENT",
         "SUB_FUNCTION"}
# MATRIX_DIM takes one to three numbers
VARIADIC = {"MATRIX_DIM": 3}


@dataclass
class Block:
    keyword: str
    line: int
    tokens: list = field(default_factory=list)       # (text, line) between /begin and /end, sub-blocks removed
    children: list = field(default_factory=list)
    params: list = field(default_factory=list)       # fixed parameters (text)
    keywords: dict = field(default_factory=dict)     # keyword -> [arguments] (last occurrence)
    extra: list = field(default_factory=list)        # tokens after the fixed parameters that are no keyword


def tokenize(text: str) -> list:
    tokens, line = [], 1
    for m in TOKEN.finditer(text):
        t = m.group(0)
        if not t.isspace() and not t.startswith("/*") and not t.startswith("//"):
            tokens.append((t, line))
        line += t.count("\n")
    return tokens


def _kind_ok(kind, token: str) -> bool:
    if isinstance(kind, set):
        return token in kind
    if kind == S:
        return token.startswith('"') and token.endswith('"') and len(token) >= 2
    if kind == N:
        return bool(NUMBER.match(token))
    if kind == D:
        return token in DATATYPES
    return bool(IDENT.match(token))


def _number(token: str) -> float:
    return float(int(token, 16)) if token.lower().lstrip("+-").startswith("0x") else float(token)


def parse(text: str) -> tuple[Block | None, list]:
    """(PROJECT block, findings) of an A2L text."""
    tokens = tokenize(text)
    findings = []
    if len(tokens) < 3 or tokens[0][0] != "ASAP2_VERSION" or not all(NUMBER.match(t) for t, _ in tokens[1:3]):
        return None, ["line 1: the file must start with ASAP2_VERSION <major> <minor>"]
    root = Block("<file>", 1)
    stack = [root]
    i = 3
    while i < len(tokens):
        t, line = tokens[i]
        if t == "/begin":
            if i + 1 >= len(tokens):
                findings.append(f"line {line}: /begin without keyword")
                break
            block = Block(tokens[i + 1][0], line)
            stack[-1].children.append(block)
            stack.append(block)
            i += 2
            continue
        if t == "/end":
            keyword = tokens[i + 1][0] if i + 1 < len(tokens) else ""
            if len(stack) == 1:
                findings.append(f"line {line}: /end {keyword} without /begin")
            elif stack[-1].keyword != keyword:
                findings.append(f"line {line}: /end {keyword} closes /begin {stack[-1].keyword} of line "
                                f"{stack[-1].line}")
                stack.pop()
            else:
                stack.pop()
            i += 2
            continue
        if t == "/include":
            i += 2
            continue
        stack[-1].tokens.append((t, line))
        i += 1
    for open_block in stack[1:]:
        findings.append(f"line {open_block.line}: /begin {open_block.keyword} is never closed")
    projects = [b for b in root.children if b.keyword == "PROJECT"]
    if len(projects) != 1 or root.tokens:
        findings.append("the file must hold exactly one PROJECT and nothing else after ASAP2_VERSION")
        return None, findings
    return projects[0], findings


def _check_block(block: Block, findings: list):
    if block.keyword == "IF_DATA" or block.keyword == "A2ML":
        return                                              # content described by the AML of the tool
    where = f"line {block.line}: {block.keyword}"
    if block.keyword in LISTS:
        for t, line in block.tokens:
            if not IDENT.match(t):
                findings.append(f"line {line}: {block.keyword}: '{t}' is no identifier")
        block.params = [t for t, _ in block.tokens]
        return
    spec = BLOCKS.get(block.keyword)
    if spec is None:
        findings.append(f"{where}: block unknown to this check")
        return
    fixed, keywords, subs = spec
    tokens = block.tokens
    if len(tokens) < len(fixed):
        findings.append(f"{where}: {len(tokens)} parameter(s), {len(fixed)} expected")
        return
    for n, kind in enumerate(fixed):
        if not _kind_ok(kind, tokens[n][0]):
            findings.append(f"line {tokens[n][1]}: {block.keyword} parameter {n + 1} '{tokens[n][0]}' is no "
                            f"{kind if isinstance(kind, str) else 'one of ' + ', '.join(sorted(kind))}")
    block.params = [t for t, _ in tokens[:len(fixed)]]
    i = len(fixed)
    if block.keyword == "COMPU_VTAB":
        pairs = int(_number(block.params[3])) if NUMBER.match(block.params[3]) else 0
        for _ in range(pairs):
            if i + 1 >= len(tokens) or not NUMBER.match(tokens[i][0]) or not _kind_ok(S, tokens[i + 1][0]):
                findings.append(f"{where} {block.params[0]}: {pairs} value/text pairs announced, fewer given")
                return
            block.extra.append((tokens[i][0], tokens[i + 1][0]))
            i += 2
    while i < len(tokens):
        t, line = tokens[i]
        args = keywords.get(t)
        if args is None:
            findings.append(f"line {line}: {block.keyword}: '{t}' is no keyword of this block")
            i += 1
            continue
        count = len(args)
        if t in VARIADIC:
            count = 0
            while count < VARIADIC[t] and i + 1 + count < len(tokens) and NUMBER.match(tokens[i + 1 + count][0]):
                count += 1
            args = [N] * max(count, 1)
        values = [x for x, _ in tokens[i + 1:i + 1 + count]]
        if len(values) < len(args) or not all(_kind_ok(k, v) for k, v in zip(args, values)):
            findings.append(f"line {line}: {block.keyword}: {t} needs {len(args)} argument(s) of kind "
                            f"{', '.join(k if isinstance(k, str) else '|'.join(sorted(k)) for k in args)}")
        block.keywords[t] = values
        i += 1 + count
    for child in block.children:
        if child.keyword not in subs:
            findings.append(f"line {child.line}: {child.keyword} is not allowed in {block.keyword}")
        _check_block(child, findings)


def _limits(block: Block, lower: int, findings: list, name: str):
    try:
        lo, hi = _number(block.params[lower]), _number(block.params[lower + 1])
    except (IndexError, ValueError):
        return
    if lo > hi:
        findings.append(f"line {block.line}: {name}: lower limit {lo:g} > upper limit {hi:g}")
    ext = block.keywords.get("EXTENDED_LIMITS")
    if ext and len(ext) == 2 and all(NUMBER.match(x) for x in ext):
        elo, ehi = _number(ext[0]), _number(ext[1])
        if not elo <= lo <= hi <= ehi:
            findings.append(f"line {block.line}: {name}: limits {lo:g} .. {hi:g} are not inside EXTENDED_LIMITS "
                            f"{elo:g} .. {ehi:g}")


def check_text(text: str) -> list:
    """Findings of an A2L text; [] = formally valid within the subset of a2lgen."""
    project, findings = parse(text)
    if project is None:
        return findings
    _check_block(project, findings)
    modules = [b for b in project.children if b.keyword == "MODULE"]
    if len(modules) != 1:
        return findings + ["the PROJECT must hold exactly one MODULE"]
    module = modules[0]
    by_kind: dict[str, dict] = {}
    objects: dict[str, Block] = {}
    for b in module.children:
        if not b.params:
            continue
        name = b.params[0]
        if b.keyword in ("CHARACTERISTIC", "AXIS_PTS", "MEASUREMENT"):
            if name in objects:
                findings.append(f"line {b.line}: {b.keyword} {name} is already defined (line {objects[name].line})")
            objects[name] = b
        elif b.keyword in ("COMPU_METHOD", "COMPU_VTAB", "RECORD_LAYOUT", "FUNCTION"):
            if name in by_kind.setdefault(b.keyword, {}):
                findings.append(f"line {b.line}: {b.keyword} {name} is already defined")
            by_kind[b.keyword][name] = b
    methods = by_kind.get("COMPU_METHOD", {})
    layouts = by_kind.get("RECORD_LAYOUT", {})
    vtabs = by_kind.get("COMPU_VTAB", {})

    def method(ref: str, line: int, what: str):
        if ref != "NO_COMPU_METHOD" and ref not in methods:
            findings.append(f"line {line}: {what}: conversion {ref} is not defined")

    for name, b in methods.items():
        kind = b.params[2] if len(b.params) > 2 else ""
        if kind == "RAT_FUNC" and "COEFFS" not in b.keywords:
            findings.append(f"line {b.line}: COMPU_METHOD {name}: RAT_FUNC needs COEFFS")
        if kind == "TAB_VERB":
            ref = (b.keywords.get("COMPU_TAB_REF") or [""])[0]
            if ref not in vtabs:
                findings.append(f"line {b.line}: COMPU_METHOD {name}: COMPU_TAB_REF {ref or '-'} is not defined")
    for name, b in vtabs.items():
        values = [v for v, _ in b.extra]
        if len(set(values)) != len(values):
            findings.append(f"line {b.line}: COMPU_VTAB {name}: a value is listed twice")
    for name, b in layouts.items():
        positions = [a[0] for k, a in b.keywords.items() if k in ("FNC_VALUES", "AXIS_PTS_X", "AXIS_PTS_Y")]
        if len(set(positions)) != len(positions):
            findings.append(f"line {b.line}: RECORD_LAYOUT {name}: two parts at one position")
    for name, b in objects.items():
        if len(b.params) < len(BLOCKS[b.keyword][0]):
            continue
        if b.keyword == "MEASUREMENT":
            method(b.params[3], b.line, f"MEASUREMENT {name}")
            _limits(b, 6, findings, name)
            if "ECU_ADDRESS" not in b.keywords:
                findings.append(f"line {b.line}: MEASUREMENT {name} has no ECU_ADDRESS")
            continue
        layout = layouts.get(b.params[4])
        if layout is None:
            findings.append(f"line {b.line}: {b.keyword} {name}: record layout {b.params[4]} is not defined")
        if b.keyword == "AXIS_PTS":
            method(b.params[6], b.line, f"AXIS_PTS {name}")
            _limits(b, 8, findings, name)
            if layout is not None and "AXIS_PTS_X" not in layout.keywords:
                findings.append(f"line {b.line}: AXIS_PTS {name}: record layout {layout.params[0]} has no AXIS_PTS_X")
            continue
        method(b.params[6], b.line, f"CHARACTERISTIC {name}")
        _limits(b, 7, findings, name)
        kind = b.params[2]
        axes = [c for c in b.children if c.keyword == "AXIS_DESCR"]
        wanted = {"VALUE": 0, "VAL_BLK": 0, "ASCII": 0, "CURVE": 1, "MAP": 2}.get(kind, 0)
        if len(axes) != wanted:
            findings.append(f"line {b.line}: CHARACTERISTIC {name} ({kind}) has {len(axes)} AXIS_DESCR, "
                            f"{wanted} expected")
        if kind == "VAL_BLK" and "NUMBER" not in b.keywords and "MATRIX_DIM" not in b.keywords:
            findings.append(f"line {b.line}: VAL_BLK {name} needs NUMBER or MATRIX_DIM")
        if layout is not None and "FNC_VALUES" not in layout.keywords:
            findings.append(f"line {b.line}: {name}: record layout {layout.params[0]} has no FNC_VALUES")
        for letter, axis in zip("XY", axes):
            if len(axis.params) < 6:
                continue
            attribute = axis.params[0]
            method(axis.params[2], axis.line, f"AXIS_DESCR of {name}")
            _limits(axis, 4, findings, f"axis {letter} of {name}")
            ref = (axis.keywords.get("AXIS_PTS_REF") or [None])[0]
            part = f"AXIS_PTS_{letter}"
            if attribute == "COM_AXIS":
                target = objects.get(ref) if ref else None
                if target is None or target.keyword != "AXIS_PTS":
                    findings.append(f"line {axis.line}: COM_AXIS of {name}: AXIS_PTS_REF {ref or '-'} is no AXIS_PTS")
                elif len(target.params) > 7 and target.params[7] != axis.params[3]:
                    findings.append(f"line {axis.line}: COM_AXIS of {name}: {axis.params[3]} points, "
                                    f"{ref} has {target.params[7]}")
                if layout is not None and part in layout.keywords:
                    findings.append(f"line {axis.line}: COM_AXIS of {name}, but its record layout stores {part}")
            elif attribute == "STD_AXIS":
                if ref:
                    findings.append(f"line {axis.line}: STD_AXIS of {name} must not have AXIS_PTS_REF")
                if layout is not None and part not in layout.keywords:
                    findings.append(f"line {axis.line}: STD_AXIS of {name}: record layout {layout.params[0]} has no "
                                    f"{part}")
                fix = layout.keywords.get(f"FIX_NO_AXIS_PTS_{letter}") if layout is not None else None
                if fix and fix[0] != axis.params[3]:
                    findings.append(f"line {axis.line}: STD_AXIS of {name}: {axis.params[3]} points, the record "
                                    f"layout fixes {fix[0]}")
    for name, b in by_kind.get("FUNCTION", {}).items():
        for child in b.children:
            for ref in child.params:
                target = objects.get(ref)
                wants = ("CHARACTERISTIC", "AXIS_PTS") if "CHARACTERISTIC" in child.keyword else ("MEASUREMENT",)
                if child.keyword == "SUB_FUNCTION":
                    if ref not in by_kind.get("FUNCTION", {}):
                        findings.append(f"line {child.line}: FUNCTION {name}: sub-function {ref} is not defined")
                elif target is None or target.keyword not in wants:
                    findings.append(f"line {child.line}: FUNCTION {name}: {ref} is no {' or '.join(wants)}")
    epk = [b for b in module.children if b.keyword == "MOD_PAR"]
    for b in epk:
        if ("EPK" in b.keywords) != ("ADDR_EPK" in b.keywords):
            findings.append(f"line {b.line}: MOD_PAR: EPK and ADDR_EPK go together")
    return findings


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(__doc__.split("\n\n")[0], file=sys.stderr)
        print("usage: a2lcheck.py <file.a2l>", file=sys.stderr)
        return 2
    findings = check_text(Path(args[0]).read_text(encoding="utf-8"))
    for f in findings:
        print(f"{args[0]}: {f}", file=sys.stderr)
    if not findings:
        print(f"{args[0]}: formally valid (subset of tools/a2lgen)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
