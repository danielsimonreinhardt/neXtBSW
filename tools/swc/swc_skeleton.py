# SPDX-License-Identifier: Apache-2.0
"""Code skeleton and source check of a hand-written C component (RTE-20).

A component with `implementation: c` in its swc.yaml is written in C by hand. swc.yaml stays its master: rtegen reads
runnables, ports and calibration parameters from it and generates Rte_<Swc>.h with the same API Embedded Coder code
uses (Rte_Read_<Port>_<Element>, Rte_Write_<Port>_<Element>, Rte_Prm_CalPrm_<Name>). This tool

    create   writes src/<Swc>.c ONCE: one function per runnable (<Swc>_Init and <Swc>_<Runnable>) with commented
             example accesses to the ports and parameters the runnable uses. A component that already has a C source
             in src/ is never touched - the file belongs to the developer from then on.
    check    compares the runnable functions defined in src/*.c with swc.yaml: missing, static or with a wrong
             signature, and external `void f(void)` functions that are no runnable (renamed or removed in swc.yaml).
             It only reports (warnings with file, YAML path and line); it never rewrites a source.

No MATLAB, no Qt: neXtBSW Studio (TOOL-55) calls create(), check() and render() directly, rtegen calls check() and
shows its findings as warnings.

    python tools/swc/swc_skeleton.py create swc/<Swc>        src/<Swc>.c, exit code 1 on errors
    python tools/swc/swc_skeleton.py check  swc/<Swc>        findings, exit code 1 on errors (--strict: also warnings)

The source check reads C text, not a compiled program: comments, strings and preprocessor lines are ignored, function
definitions at file level are recognized by their form `<type> <name>(<parameters>) {`. Functions produced by macros
are not seen.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import swc_yaml  # noqa: E402
from nxconfig.messages import Message  # noqa: E402
from nxconfig import genlicense  # noqa: E402  (PLAT-120: licence line of generated files)

SOURCE_DIR = "src"
TOOL = "tools/swc/swc_skeleton.py"

# value written by the example Rte_Write of a port type
ZERO = {"boolean": "FALSE", "uint8": "0U", "uint16": "0U", "uint32": "0U", "sint8": "0", "sint16": "0",
        "sint32": "0", "float32": "0.0F", "float64": "0.0"}


def source_dir(folder: Path | str) -> Path:
    """Folder of the hand-written sources: <component>/src (built from there, like generated/src for Simulink)."""
    return Path(folder) / SOURCE_DIR


def source_file(folder: Path | str, name: str) -> Path:
    """The file the skeleton creates: <component>/src/<Swc>.c."""
    return source_dir(folder) / f"{name}.c"


def _folder_and_file(path: Path | str) -> tuple[Path, Path]:
    path = Path(path)
    if path.is_dir():
        return path, path / swc_yaml.FILE_NAME
    return path.parent, path


def _variable(name: str) -> str:
    """Local variable of an example access: the port name starting lower case (never a C keyword)."""
    var = name[0].lower() + name[1:]
    return var + "_" if var in swc_yaml.C_KEYWORDS else var


def _period(ms: int) -> str:
    return f"{ms // 1000} s" if ms % 1000 == 0 else f"{ms} ms"


def _examples(swc: swc_yaml.Swc, runnable: str) -> list[str]:
    """Example accesses of one runnable: every port and parameter swc.yaml assigns to it."""
    lines = []
    for p in swc.ports:
        if runnable not in p.runnables:
            continue
        if p.array:
            # RTE-25: an array port is read into and written from an array of its elements (AUTOSAR: element
            # pointer); the number is the resolved dimension of swc.yaml
            var = _variable(p.name)
            source = f"   ({p.dimension_source})" if p.dimension_source else ""
            lines.append(f"{p.type} {var}[{p.dimension}] = {{{ZERO[p.type]}}};{source}")
            lines.append(f"(void)Rte_{'Read' if p.direction == 'in' else 'Write'}_{p.name}_{p.name}({var});")
        elif p.direction == "in":
            var = _variable(p.name)
            lines += [f"{p.type} {var} = {ZERO[p.type]};",
                      f"(void)Rte_Read_{p.name}_{p.name}(&{var});"]
        else:
            lines.append(f"(void)Rte_Write_{p.name}_{p.name}({ZERO[p.type]});")
    for prm in swc.parameters:
        if runnable not in prm.runnables:
            continue
        var = _variable(prm.name)
        call = f"Rte_Prm_{swc_yaml.PARAMETER_PORT}_{prm.name}()"
        note = []
        if prm.dim > 1:
            note.append(f"{prm.dim} elements")
        if prm.lsb is not None:
            scale = f"stored * {prm.lsb!r}" + (f" + {prm.offset!r}" if prm.offset else "")
            note.append(f"stored value, physical = {scale}")
        tail = f"   ({', '.join(note)})" if note else ""
        if prm.dim > 1:
            lines.append(f"{prm.type} const* const {var} = {call};{tail}")
        else:
            lines.append(f"{prm.type} const {var} = {call};{tail}")
    return lines


def _uses(swc: swc_yaml.Swc, runnable: str) -> list[str]:
    def shown(p):
        return f"{p.name} ({p.type}{f'[{p.dimension}]' if p.array else ''})"
    reads = [shown(p) for p in swc.ports if p.direction == "in" and runnable in p.runnables]
    writes = [shown(p) for p in swc.ports if p.direction == "out" and runnable in p.runnables]
    params = [f"{p.name} ({p.type}{f'[{p.dim}]' if p.dim > 1 else ''})" for p in swc.parameters
              if runnable in p.runnables]
    out = []
    for what, items in (("Reads", reads), ("Writes", writes), ("Parameters", params)):
        line = f"{what}:"
        for i, item in enumerate(items):
            text = f" {item}{',' if i < len(items) - 1 else ''}"
            if len(line) + len(text) > 105:          # comment lines stay within 120 characters
                out.append(line)
                line = " " * len(what)
            line += text
        if items:
            out.append(line)
    return out


def render(swc: swc_yaml.Swc) -> str:
    """Text of src/<Swc>.c for a checked description (no file is touched; the Studio may show it as a preview)."""
    name = swc.name
    lines = [f"/* {name}{': ' + swc.description if swc.description else ''}",
             " *",
             f" * Hand-written software component (swc.yaml: implementation: c). Created once by {TOOL} from",
             " * swc.yaml; from now on this file is yours, the tool never overwrites it. swc.yaml stays the description",
             " * the RTE is generated from: after changing runnables there,",
             f" *     python {TOOL} check <component folder>",
             " * lists runnable functions that are missing here or no longer described.",
             f" * Rte_{name}.h (generated by tools/rtegen) declares the runnables and the port accesses. */",
             "",
             f'#include "Rte_{name}.h"',
             "",
             "/* Init runnable: called once at start-up, before the first periodic runnable. */",
             f"void {swc.init_runnable}(void)",
             "{",
             "}"]
    for r in swc.runnables:
        head = [f"Runnable {r.name}, every {_period(r.period_ms)} (task: rte.yaml tasks[].runnables {name}.{r.name})."]
        head += _uses(swc, r.name)
        lines += ["", "/* " + head[0]] + [" * " + h for h in head[1:]]
        lines[-1] += " */"
        lines += [f"void {swc.symbol_of(r.name)}(void)", "{"]
        examples = _examples(swc, r.name)
        if examples:
            lines.append("    /* Example accesses, replace them by the logic of the runnable:")
            lines += [f"     *     {e}" for e in examples]
            lines.append("     */")
        lines.append("}")
    return genlicense.stamp("\n".join(lines) + "\n", "skeleton.c")      # PLAT-120


@dataclass
class CreateResult:
    """Outcome of create(): the file, whether it was written now, why not, and the swc.yaml messages."""
    file: Path | None
    created: bool
    note: str = ""
    messages: list[Message] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(m.severity == "error" for m in self.messages)


def _load_c(yaml_file: Path) -> tuple[swc_yaml.Swc | None, list[Message]]:
    """swc.yaml of a C component, or None and the messages (errors of the description, wrong implementation)."""
    try:
        swc, messages = swc_yaml.load(yaml_file)
    except swc_yaml.SwcErrors as exc:
        return None, exc.messages
    if swc.implementation != "c":
        return None, messages + [Message("error", f"{swc.name} is implemented in {swc.implementation}, not in C "
                                                  f"(implementation: c in swc.yaml); its code comes from the model",
                                         yaml_file, ("implementation",))]
    return swc, messages


def create(folder: Path | str) -> CreateResult:
    """Writes src/<Swc>.c once. Never overwrites: nothing is written if src/ already holds a C source."""
    folder, yaml_file = _folder_and_file(folder)
    swc, messages = _load_c(yaml_file)
    if swc is None:
        return CreateResult(None, False, "swc.yaml has errors", messages)
    target = source_file(folder, swc.name)
    existing = sorted(source_dir(folder).glob("*.c"))
    if existing:
        return CreateResult(target, False, f"not created: {', '.join(f.name for f in existing)} already in "
                                           f"{SOURCE_DIR}/ (the skeleton is written only once)", messages)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("x", encoding="utf-8", newline="\n") as out:   # "x": fails instead of overwriting
            out.write(render(swc))
    except FileExistsError:
        return CreateResult(target, False, f"not created: {target.name} exists", messages)
    return CreateResult(target, True, f"created {target.as_posix()}", messages)


# ---------------------------------------------------------------------------------------------------- source check

@dataclass
class Function:
    """A function definition found at file level of a C source."""
    name: str
    file: Path
    line: int
    static: bool
    returns: str          # return type as written (normalized spaces)
    parameters: str       # parameter list as written (normalized spaces)

    @property
    def runnable_form(self) -> bool:
        return self.returns == "void" and self.parameters == "void"


_TOKEN = re.compile(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'', re.S)
_DEFINITION = re.compile(r"([A-Za-z_]\w*)\s*\(([^()]*)\)\s*\{")
_STORAGE = {"static", "extern", "inline", "__inline", "__inline__"}


def _blank(text: str) -> str:
    """Comments, strings and preprocessor lines replaced by spaces; newlines kept, so offsets keep their line."""
    def keep_lines(m: re.Match) -> str:
        return re.sub(r"[^\n]", " ", m.group(0))
    text = _TOKEN.sub(keep_lines, text)
    return re.sub(r"(?m)^[ \t]*#(?:[^\n]*\\\n)*[^\n]*", keep_lines, text)


def functions(file: Path) -> list[Function]:
    """Function definitions at file level (brace depth 0) of a C source."""
    text = _blank(Path(file).read_text(encoding="utf-8", errors="replace"))
    depth, start, found = 0, 0, []
    depths = []                      # brace depth at every offset where a definition may start
    for i, ch in enumerate(text):
        depths.append(depth)
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
    for m in _DEFINITION.finditer(text):
        if depths[m.start()] != 0:
            continue
        # the declaration specifiers: back to the previous ';', '}' or '{' at file level
        start = max(text.rfind(";", 0, m.start()), text.rfind("}", 0, m.start()), text.rfind("{", 0, m.start()))
        words = text[start + 1:m.start()].split()
        if not words or words[-1] in ("if", "while", "for", "switch", "return", "sizeof"):
            continue
        static = any(w in ("static",) for w in words)
        returns = " ".join(w for w in words if w not in _STORAGE)
        found.append(Function(m.group(1), Path(file), text.count("\n", 0, m.start()) + 1, static, returns,
                              " ".join(m.group(2).split())))
    return found


def check(folder: Path | str) -> list[Message]:
    """Runnable functions of src/*.c against swc.yaml; warnings, never a change to a file.

    Reported: a runnable without definition, defined static, defined twice or not as `void <name>(void)`; an
    external `void <name>(void)` that is no runnable of swc.yaml (probably renamed or removed there). Errors only
    when swc.yaml itself has errors or the component is no C component.
    """
    folder, yaml_file = _folder_and_file(folder)
    swc, messages = _load_c(yaml_file)
    if swc is None:
        return [m for m in messages if m.severity == "error"]
    tree = _tree(yaml_file)
    findings: list[Message] = []

    def warn(text: str, path: tuple = (), file: Path | None = None, line: int | None = None):
        if file is None:
            file, line = yaml_file, swc_yaml._line_of(tree, path) if tree is not None else None
        findings.append(Message("warning", text, file, path, line))

    sources = sorted(source_dir(folder).glob("*.c"))
    if not sources:
        warn(f"{swc.name}: no C source in {SOURCE_DIR}/, the RTE calls {len(swc.runnables) + 1} runnable "
             f"functions; create the skeleton with python {TOOL} create {folder.as_posix()}", ("name",))
        return findings
    defined: dict[str, list[Function]] = {}
    for src in sources:
        for f in functions(src):
            defined.setdefault(f.name, []).append(f)

    expected = [(swc.init_runnable, "init runnable", ("name",))]
    expected += [(swc.symbol_of(r.name), f"runnable {r.name}", ("runnables", i, "name"))
                 for i, r in enumerate(swc.runnables)]
    symbols = {symbol for symbol, _, _ in expected}
    for symbol, what, path in expected:
        found = defined.get(symbol, [])
        if not found:
            warn(f"{what}: function void {symbol}(void) is missing in {SOURCE_DIR}/*.c (the RTE calls it)", path)
            continue
        if len(found) > 1:
            where = ", ".join(f"{f.file.name}:{f.line}" for f in found)
            warn(f"{what}: {symbol} is defined {len(found)} times ({where})", path)
        for f in found:
            if f.static:
                warn(f"{what}: {symbol} is static; the RTE calls it from outside the file", (), f.file, f.line)
            if not f.runnable_form:
                warn(f"{what}: {symbol} must be defined as void {symbol}(void), not "
                     f"{f.returns} {symbol}({f.parameters})", (), f.file, f.line)
    for name, found in sorted(defined.items()):
        if name in symbols:
            continue
        for f in found:
            if not f.static and f.runnable_form:
                warn(f"function {name}(void) is no runnable of swc.yaml (renamed or removed there?) and is never "
                     f"called by the RTE; make it static if it is a helper", (), f.file, f.line)
    return findings


def _tree(yaml_file: Path):
    try:
        from ruamel.yaml import YAML
        return YAML(typ="rt").load(yaml_file.read_text(encoding="utf-8"))
    except Exception:   # noqa: BLE001 - only for line numbers; load() has reported a broken file already
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_create = sub.add_parser("create", help="write src/<Swc>.c once (never overwrites)")
    p_create.add_argument("folder", type=Path, help="component folder or its swc.yaml")
    p_check = sub.add_parser("check", help="compare the runnable functions of src/*.c with swc.yaml")
    p_check.add_argument("folder", type=Path, help="component folder or its swc.yaml")
    p_check.add_argument("--strict", action="store_true", help="exit code 1 on findings as well")
    args = parser.parse_args(argv)
    if args.command == "create":
        result = create(args.folder)
        for m in result.messages:
            print(m, file=sys.stderr)
        print(result.note)
        return 0 if result.ok else 1
    messages = check(args.folder)
    for m in messages:
        print(m, file=sys.stderr)
    errors = [m for m in messages if m.severity == "error"]
    if not messages:
        print(f"{args.folder.as_posix()}: runnable functions match swc.yaml")
    return 1 if errors or (args.strict and messages) else 0


if __name__ == "__main__":
    sys.exit(main())
