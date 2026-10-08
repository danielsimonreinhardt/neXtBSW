# SPDX-License-Identifier: Apache-2.0
"""Format strings for OpenBSW util::format must not use conversions it reads differently than C (IO-60).

util::format (Logger, StringWriter/SharedStringWriter of the console, PrintfFormatter) has its own printf scanner,
external/openbsw/libs/bsw/util/src/util/format/PrintfFormatScanner.cpp. Its scanParamLength() returns the same
size for 'l' and 'll': "%lu"/"%lx" are read as 64-bit values (PrintfArgumentReader: va_arg(uint64_t)). On the
32-bit target (Cortex-M/-R, D11) a long is 32 bit, the formatter takes two argument slots (8-byte aligned) and every
following argument shifts - COM-39 ("0xa5a5a5a5000007ff"), IO-54/IO-60 ("debounce 85899347920 us", "qlf <NULL>").
Host tests with the C library cannot see it. The scanner also knows no 'hh', 'z', 'j', 't', no floating point;
such a conversion is printed as text and its argument is not consumed - the same shift.

Rule: a format that reaches util::format uses %u/%d/%x with (u)int32_t (or %hu, %c, %s, %p, %llu with a real
64-bit value); no single 'l', no 'hh'/'z'/'j'/'t'/'L', no floating point, no PRI* macro (on arm-none-eabi
PRIu32 is "lu"). C library formats (printf/snprintf/vsnprintf of OpenBSW bspIo: the eyalroz printf, newlib) keep
"%lu" with unsigned long - that is correct there, and -Wformat demands it for uint32_t (unsigned long on the target).

What the tool finds, in every source outside external/ and test directories (tools/logcheck.iter_sources):
  - util::format sinks: Logger::debug/info/warn/error/critical (format = argument 2), Logger::log (argument 3),
    member calls .printf/.vprintf/->printf/.format (argument 1), and wrappers - a function with a
    "char const* <name>, ..." parameter whose body passes <name> as format to one of these (argument of <name>);
  - C library calls: printf, snprintf, vsnprintf, sprintf, fprintf, debug_printf ..., and functions declared with
    __attribute__((format(printf, n, m))) that are no util::format wrapper.
Every sink format (adjacent literals, conditional expressions of literals, PRI* macros) is scanned like
PrintfFormatScanner. Every other literal with a single-'l'/'hh'/'z'/'j'/'t' integer conversion or a PRI* macro that
goes to neither kind of call is an error too: nobody can tell where it ends up (a table of formats, a new wrapper) -
use %u/%x with uint32_t, or pass it to the C library directly.

    python tools/fmtcheck [--repo DIR] [--all]     findings (exit 1) / --all: every checked format with its sink
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.logcheck.logcheck import (  # noqa: E402  (shared C++ lexing of the logger check, SYS-16)
    SKIP_DIRS,
    SOURCE_SUFFIXES,
    _line_of,
    _skip_quoted,
    _split_conditional,
    _split_top,
    _unescape,
    strip_comments,
)

#: Logger methods and the index of their format argument
LOGGER_FORMAT_INDEX = {"debug": 1, "info": 1, "warn": 1, "error": 1, "critical": 1, "log": 2}
#: member functions of util::format::StringWriter / PrintfFormatter
MEMBER_SINKS = ("printf", "vprintf", "format")
#: C library printf family (OpenBSW bspIo routes them to the eyalroz printf, else newlib) and format index
LIBC_FORMAT_INDEX = {
    "printf": 0, "vprintf": 0, "debug_printf": 0, "puts": -1,
    "sprintf": 1, "vsprintf": 1, "fprintf": 1, "vfprintf": 1, "dprintf": 1,
    "snprintf": 2, "vsnprintf": 2, "printf_": 0, "snprintf_": 2, "vsnprintf_": 2, "fctprintf": 2,
}

_WRAPPER_RE = re.compile(
    r"\b([A-Za-z_]\w*)\s*\(([^;{}()]*?)\b(?:char\s+const|const\s+char)\s*\*\s*(?:const\s+)?(\w+)\s*,\s*\.\.\.\s*\)"
    r"[^;{}]*\{")
_FORMAT_ATTR_RE = re.compile(r"__attribute__\s*\(\(\s*(?:__)?format(?:__)?\s*\(\s*(?:__)?printf(?:__)?\s*,\s*(\d+)")
_PRI_RE = re.compile(r"\bPRI[diouxX](?:8|16|32|64|LEAST\d+|FAST\d+|MAX|PTR)\b")
#: integer conversion with a length util::format reads wrongly - for literals outside any known call (no ' ' flag:
#: "50 % load" is text)
_SUSPECT_RE = re.compile(r"%[-+#0]*(?:\*|\d+)?(?:\.(?:\*|\d+))?(?:l(?!l)|hh|z|j|t)[diouxXn]")

SINK = "util::format"
LIBC = "C library"
UNKNOWN = "unknown"


@dataclass
class Finding:
    file: str
    line: int
    sink: str            # SINK / LIBC / UNKNOWN
    via: str             # call name
    fmt: str | None      # evaluated format (None: not a literal)
    errors: list[str] = field(default_factory=list)


# ------------------------------------------------------------------------------------------------ scanner model

def format_errors(fmt: str) -> list[str]:
    """Conversions of a util::format format that the formatter reads differently than C (PrintfFormatScanner)."""
    errors = []
    i, n = 0, len(fmt)
    while i < n:
        if fmt[i] != "%":
            i += 1
            continue
        start = i
        i += 1
        while i < n and fmt[i] in "-+ #0":
            i += 1
        if i < n and fmt[i] == "*":
            i += 1
        else:
            while i < n and fmt[i].isdigit():
                i += 1
        if i < n and fmt[i] == ".":
            i += 1
            if i < n and fmt[i] == "*":
                i += 1
            else:
                while i < n and fmt[i].isdigit():
                    i += 1
        length = ""
        if fmt.startswith("ll", i):
            length = "ll"
        elif i < n and fmt[i] in "lhLzjtq":
            length = fmt[i] + ("h" if fmt.startswith("hh", i) else "")
        i += len(length)
        spec = fmt[i] if i < n else ""
        i += 1 if i < n else 0
        text = fmt[start:i]
        if spec == "%" and not length and text == "%%":
            continue
        if length == "l" and spec in "diouxX":
            errors.append(f"{text}: util::format reads 'l' as 64 bit (PrintfFormatScanner::scanParamLength), the "
                          "target passes 32 - use %u/%x with uint32_t")
        elif length in ("hh", "z", "j", "t", "L", "q"):
            errors.append(f"{text}: length '{length}' is not supported by util::format, the argument is not read "
                          "as C reads it")
        elif spec == "" or spec not in "cdiuoxXnpsS":
            errors.append(f"{text}: conversion '{spec or 'end of format'}' is not supported by util::format "
                          "(printed as text, the argument shifts the following ones)")
    return errors


# ------------------------------------------------------------------------------------------------ lexing

def literals(code: str) -> list[tuple[int, int]]:
    """(start, end) of every string literal of comment-free code; character literals skipped."""
    spans, i, n = [], 0, len(code)
    while i < n:
        c = code[i]
        if c == '"':
            j = _skip_quoted(code, i)
            spans.append((i, j))
            i = j
        elif c == "'":
            i = _skip_quoted(code, i)
        else:
            i += 1
    return spans


def call_arguments(code: str, open_paren: int) -> list[tuple[int, int]] | None:
    """(start, end) of the top-level arguments of the call whose '(' is at open_paren."""
    depth, i, start, args = 0, open_paren, open_paren + 1, []
    while i < len(code):
        c = code[i]
        if c in "\"'":
            i = _skip_quoted(code, i)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                if code[start:i].strip() or args:
                    args.append((start, i))
                return args
        elif c == "," and depth == 1:
            args.append((start, i))
            start = i + 1
        i += 1
    return None


_LIT_OR_MACRO_RE = re.compile(r'\s*(?:(?:u8)?"((?:[^"\\]|\\.)*)"|([A-Za-z_]\w*))', re.S)


def evaluate(expr: str) -> list[str] | None:
    """Values of a format argument: adjacent literals and PRI* macros (kept as marker), or a conditional
    expression of such; None if it is anything else (variable, other macro)."""
    expr = expr.strip()
    while expr.startswith("(") and expr.endswith(")") and _balanced(expr[1:-1]):
        expr = expr[1:-1].strip()
    top = _conditional(expr)
    if top is not None:
        left, right = evaluate(top[0]), evaluate(top[1])
        return None if left is None or right is None else left + right
    pos, parts, has_literal = 0, [], False
    while pos < len(expr):
        m = _LIT_OR_MACRO_RE.match(expr, pos)
        if not m:
            return None
        if m.group(2) is not None:
            # a macro between literals ("UDS on " NX_UDS_BUS_NAME " ..."): PRI* is judged, others are text
            parts.append("{" + m.group(2) + "}")
        else:
            has_literal = True
            parts.append(_unescape(m.group(1)))
        pos = m.end()
        while pos < len(expr) and expr[pos].isspace():
            pos += 1
    return ["".join(parts)] if has_literal else None


def _balanced(expr: str) -> bool:
    depth, i = 0, 0
    while i < len(expr):
        c = expr[i]
        if c in "\"'":
            i = _skip_quoted(expr, i)
            continue
        depth += {"(": 1, ")": -1}.get(c, 0)
        if depth < 0:
            return False
        i += 1
    return depth == 0


def _conditional(expr: str) -> tuple[str, str] | None:
    """The two branches of a top-level "cond ? a : b", else None (nested conditionals and '::' kept)."""
    top = _split_top(expr, "?")
    return None if top is None else _split_conditional(top[1])


# ------------------------------------------------------------------------------------------------ sinks

@dataclass
class Sinks:
    """Functions whose format argument goes to util::format (wrappers) or to the C library."""
    wrappers: dict[str, int] = field(default_factory=dict)   # name -> format index
    libc: dict[str, int] = field(default_factory=lambda: dict(LIBC_FORMAT_INDEX))


def _body(code: str, open_brace: int) -> str:
    depth, i = 0, open_brace
    while i < len(code):
        c = code[i]
        if c in "\"'":
            i = _skip_quoted(code, i)
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return code[open_brace:i + 1]
        i += 1
    return code[open_brace:]


def _classify(name: str, member: str, sinks: Sinks) -> tuple[str, int] | None:
    """(sink kind, format index) of a call, None if it is no printf-like call."""
    if member == "::" and name in LOGGER_FORMAT_INDEX:
        return SINK, LOGGER_FORMAT_INDEX[name]
    if member in (".", "->") and name in MEMBER_SINKS:
        return SINK, 0
    if not member or member == "::":
        if name in sinks.wrappers:
            return SINK, sinks.wrappers[name]
        if name in sinks.libc:
            return LIBC, sinks.libc[name]
    return None


def _calls(code: str, sinks: Sinks):
    """(name, member operator, start, (kind, format index), argument spans) of every printf-like call."""
    names = set(LOGGER_FORMAT_INDEX) | set(MEMBER_SINKS) | set(sinks.wrappers) | set(sinks.libc)
    call_re = re.compile(r"\b(" + "|".join(sorted(map(re.escape, names))) + r")\s*\(")
    for m in call_re.finditer(code):
        before = code[max(0, m.start() - 64):m.start()].rstrip()
        member = next((op for op in (".", "->", "::") if before.endswith(op)), "")
        if member == "::" and not re.search(r"\bLogger\s*::$", before):
            # only Logger::info etc. is a qualified sink; std::printf, ns::wrapper() are plain function calls
            member = ""
        cls = _classify(m.group(1), member, sinks)
        if cls is None or cls[1] < 0:
            continue
        args = call_arguments(code, m.end() - 1)
        if args is None or cls[1] >= len(args):
            continue  # a declaration or a call without format
        yield m.group(1), member, m.start(1), cls, args


def find_wrappers(sources: list[tuple[str, str]], sinks: Sinks) -> None:
    """Adds every function that forwards its "char const* fmt, ..." parameter to a sink (to a fixpoint)."""
    changed = True
    while changed:
        changed = False
        for _, code in sources:
            for m in _WRAPPER_RE.finditer(code):
                name, before, param = m.group(1), m.group(2), m.group(3)
                if name in sinks.wrappers or name in ("if", "while", "for", "switch", "return"):
                    continue
                index = before.count(",")  # parameters before the format
                body = _body(code, m.end() - 1)
                kind = None
                for _, _, _, cls, args in _calls(body, sinks):
                    s, e = args[cls[1]]
                    if body[s:e].strip() == param:
                        kind = SINK if cls[0] == SINK else (kind or LIBC)
                if kind == SINK:
                    sinks.wrappers[name] = index
                    sinks.libc.pop(name, None)
                    changed = True
                elif kind == LIBC and name not in sinks.libc:
                    sinks.libc[name] = index
                    changed = True
            # functions declared with the printf format attribute are checked by the compiler with C semantics
            for m in _FORMAT_ATTR_RE.finditer(code):
                decl = re.search(r"\b([A-Za-z_]\w*)\s*\(", code[m.end():])
                if decl and decl.group(1) not in sinks.wrappers and decl.group(1) not in sinks.libc:
                    sinks.libc[decl.group(1)] = int(m.group(1)) - 1
                    changed = True


def check_source(code: str, file: str, sinks: Sinks) -> list[Finding]:
    findings: list[Finding] = []
    covered: set[int] = set()   # literal starts inside a classified format argument
    for name, member, start, (kind, index), args in _calls(code, sinks):
        s, e = args[index]
        expr = code[s:e]
        for ls, _ in literals(expr):
            covered.add(s + ls)
        if kind == LIBC:
            values = evaluate(expr)
            if values is not None and any(_suspect(v) for v in values):
                findings.append(Finding(file, _line_of(code, start), LIBC, name, " | ".join(values)))
            continue
        values = evaluate(expr)
        f = Finding(file, _line_of(code, start), SINK, _call_text(name, member), None if values is None
                    else " | ".join(values))
        if values is None:
            if '"' in expr and _SUSPECT_RE.search(expr):
                f.errors.append("format is not a plain literal but contains a suspect conversion")
            findings.append(f)
            continue
        for v in values:
            for pri in re.findall(r"\{(\w+)\}", v):
                if _PRI_RE.fullmatch(pri):
                    f.errors.append(f"{pri}: PRI* macros expand to C lengths ('lu' for 32 bit on arm-none-eabi) - "
                                    "use %u/%x with uint32_t")
            # a PRI* macro stands for its conversion letter (reported above), any other macro for plain text
            f.errors.extend(format_errors(re.sub(r"\{(\w+)\}", _macro_text, v)))
        findings.append(f)
    # literals nobody classified: a suspect conversion there has an unknown destination
    spans = literals(code)
    k = 0
    while k < len(spans):
        group = [spans[k]]
        while k + 1 < len(spans) and _joined(code, group[-1][1], spans[k + 1][0]):
            k += 1
            group.append(spans[k])
        k += 1
        start, end = group[0][0], group[-1][1]
        if any(s in covered for s, _ in group):
            continue
        values = evaluate(code[start:end])
        if values is None:
            continue
        value = values[0]
        if _suspect(value):
            if code[:start].rstrip().endswith("#include"):
                continue
            f = Finding(file, _line_of(code, start), UNKNOWN, "-", value)
            f.errors.append("suspect conversion in a literal that goes to no known printf - use %u/%x with "
                            "uint32_t or pass it to the C library directly")
            findings.append(f)
    return findings


def _macro_text(m: re.Match) -> str:
    return m.group(1)[3] if _PRI_RE.fullmatch(m.group(1)) else ""


def _suspect(value: str) -> bool:
    """A single-'l'/'hh'/'z'/'j'/'t' integer conversion or a PRI* macro in an evaluated literal."""
    return _SUSPECT_RE.search(value) is not None or any(_PRI_RE.fullmatch(m) for m in re.findall(r"\{(\w+)\}", value))


def _joined(code: str, end: int, next_start: int) -> bool:
    """Only whitespace or PRI* macros between two literals (adjacent literal concatenation)."""
    between = code[end:next_start]
    return re.fullmatch(r"(?:\s|\bPRI\w+\b)*", between) is not None


def _call_text(name: str, member: str) -> str:
    return ("Logger::" if member == "::" else member if member in (".", "->") else "") + name


def iter_sources(repo: Path):
    """The sources of tools/logcheck (same directories skipped), without walking into skipped trees."""
    for root, dirs, files in os.walk(repo):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            path = Path(root) / name
            if path.suffix in SOURCE_SUFFIXES:
                yield path, path.relative_to(repo).as_posix()


def scan_repo(repo: Path = REPO) -> list[Finding]:
    sources = []
    for path, rel in iter_sources(repo):
        sources.append((rel, strip_comments(path.read_text(encoding="utf-8", errors="replace"))))
    sinks = Sinks()
    find_wrappers(sources, sinks)
    findings = []
    for rel, code in sources:
        findings.extend(check_source(code, rel, sinks))
    return findings


def short(text: str | None, width: int = 80) -> str:
    if text is None:
        return "(not a literal)"
    text = text.replace("\r", "\\r").replace("\n", "\\n")
    return text if len(text) <= width else text[: width - 3] + "..."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python tools/fmtcheck", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", type=Path, default=REPO)
    parser.add_argument("--all", action="store_true", help="list every checked format, not only the findings")
    args = parser.parse_args(argv)
    findings = scan_repo(args.repo.resolve())
    errors = [f for f in findings if f.errors]
    if args.all:
        for f in sorted(findings, key=lambda f: (f.file, f.line)):
            mark = "FEHLER" if f.errors else "ok"
            print(f"{mark:6} {f.sink:12} {f.file}:{f.line}  {f.via}  \"{short(f.fmt)}\"")
    for f in errors:
        for e in f.errors:
            print(f"{f.file}:{f.line}: {e}")
    sinks = sum(1 for f in findings if f.sink == SINK)
    libc = sum(1 for f in findings if f.sink == LIBC)
    print(f"fmtcheck: {sinks} util::format formats, {libc} C library formats with 'l', "
          f"{len(errors)} with findings")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
