"""Worst-case stack per execution context from GCC `-fstack-usage` output and the call graph of the linked image (SYS-65).

Inputs
- the `.su` files the compiler writes next to every object (`-fstack-usage`, cmake/toolchains/NxArmNoneEabi-gcc.cmake):
  one line per function, `<file>:<line>:<col>:<name>\t<bytes>\t<qualifier>`; the name may contain line breaks
  (static initialisers), the qualifier is `static`, `dynamic` or `dynamic,bounded`;
- the disassembly of the linked image (`arm-none-eabi-objdump -d -C --no-show-raw-insn`): function starts, direct
  calls (`bl`), tail calls (`b`/`b.w`/`b.n` to the start of another function) and indirect calls (`blx rN`,
  `bx rN` with N != lr);
- a profile (YAML, see profiles/ and README.md) that names the execution contexts (tasks, interrupt levels), the
  stack each context owns, the targets of indirect calls the analysis must follow and the frames of functions
  without a `.su` entry (assembly, precompiled libraries).

Method (conservative by construction)
- A function's frame is the largest `.su` value of all entries with the same key (qualified name without return
  type, parameter list, template arguments and clone suffix): overloads, template instances and local functions of
  the same name in different files are merged upwards, never guessed.
- Depth of a function = its frame + the deepest callee. A tail call is counted like a call (the caller's frame is
  kept), which over-estimates by at most one frame per tail call.
- A context is `dispatch` segments plus `entries`: the frames of the dispatch code (task function, event loop,
  delegate stubs) are summed segment by segment over their direct call trees, and the deepest entry (the runnables
  and callbacks this context calls through function pointers) is added on top. That is an upper bound as long as
  every entry is reached by an indirect call inside the last dispatch segment.
- Everything the method cannot bound is reported, not dropped: functions without a frame (neither `.su` nor
  profile), indirect calls inside an entry's tree without a profile resolution, recursion, dynamic frames. A context
  with such a finding is marked `incomplete`; its number is then a lower bound.

Standard library plus PyYAML only; no Qt, no import from other tools.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# ----------------------------------------------------------------------------------------------------- names

_CLONE = re.compile(r"\s*\[clone [^\]]*\]")
_WITH = re.compile(r"\s*\[with [^\]]*\]$")
_GCC_SUFFIX = re.compile(r"\.(?:constprop|isra|part|cold|lto_priv)\.\d+")


def _strip_templates(text: str) -> str:
    """Remove every <...> group; `operator<`, `operator<<` and friends are kept as they are."""
    out = []
    depth = 0
    i = 0
    while i < len(text):
        if text.startswith("operator", i):
            m = re.match(r"operator\s*(<<=|>>=|<=>|<<|>>|<=|>=|<|>|->\*|->|\(\)|\[\]|[^\s(]+)", text[i:])
            if m and depth == 0:
                out.append(m.group(0))
                i += len(m.group(0))
                continue
        c = text[i]
        if c == "<":
            depth += 1
        elif c == ">" and depth > 0:
            depth -= 1
        elif depth == 0:
            out.append(c)
        i += 1
    return "".join(out)


def _split_params(name: str) -> str:
    """Name without the trailing parameter list and cv/ref qualifiers ('f(int) const' -> 'f')."""
    name = _WITH.sub("", name.strip())
    name = re.sub(r"\s*(const|volatile|&&|&|noexcept)\s*$", "", name)
    name = re.sub(r"\s*(const|volatile|&&|&|noexcept)\s*$", "", name)
    if not name.endswith(")"):
        return name
    depth = 0
    for i in range(len(name) - 1, -1, -1):
        if name[i] == ")":
            depth += 1
        elif name[i] == "(":
            depth -= 1
            if depth == 0:
                head = name[:i]
                # 'operator()' keeps its own parentheses
                if head.endswith("operator()") or head.endswith("operator"):
                    return head if head.endswith("operator()") else head + "()"
                return head
    return name


def key(name: str) -> str:
    """Key under which a `.su` name and an objdump symbol meet (see module docstring)."""
    name = " ".join(name.split())
    name = name.replace("(anonymous namespace)", "{anon}").replace("{anonymous}", "{anon}")
    name = _CLONE.sub("", name)
    name = _GCC_SUFFIX.sub("", name)
    for thunk in ("non-virtual thunk to ", "virtual thunk to "):
        if name.startswith(thunk):
            name = name[len(thunk):]
    name = _strip_templates(_split_params(name))
    # the return type ends at the last top-level blank before the qualified name; operators keep their blanks
    op = name.find("operator")
    if op >= 0:
        return name[name.rfind(" ", 0, op) + 1:]
    return name.split(" ")[-1].lstrip("*&")


# ------------------------------------------------------------------------------------------------- .su files

@dataclass
class Frame:
    name: str
    location: str
    size: int
    qualifier: str


def parse_su(text: str) -> list[Frame]:
    """Entries of one `.su` file; names with line breaks are joined with a blank."""
    frames = []
    # an entry ends with "\t<size>\t<qualifier>" at the end of a line; everything before belongs to its name
    pos = 0
    for m in re.finditer(r"\t(\d+)\t(static|dynamic,bounded|dynamic)[ \t]*(?:\r?\n|$)", text):
        head = text[pos:m.start()]
        pos = m.end()
        hm = re.match(r"^(.*?:\d+:\d+):(.*)$", head, re.S)
        if not hm:
            # Windows paths ("C:/...") have a colon more; take the last file:line:col before the name
            hm = re.match(r"^(.*:\d+:\d+):(.*)$", head, re.S)
        if not hm:
            raise ValueError(f"unreadable .su entry: {head!r}")
        frames.append(Frame(" ".join(hm.group(2).split()), hm.group(1), int(m.group(1)), m.group(2)))
    return frames


def load_su_dir(root: Path) -> list[Frame]:
    frames: list[Frame] = []
    for path in sorted(Path(root).rglob("*.su")):
        frames.extend(parse_su(path.read_text(encoding="utf-8", errors="replace")))
    return frames


# ------------------------------------------------------------------------------------------------ disassembly

@dataclass
class Function:
    name: str
    address: int
    calls: list[tuple[int, str]] = field(default_factory=list)        # (site, callee name)
    tail_calls: list[tuple[int, str]] = field(default_factory=list)
    indirect: list[tuple[int, str]] = field(default_factory=list)     # (site, instruction)
    sp_bytes: int = 0      # sum of all stack pointer decrements in the function (see sp_decrement)
    ops: list[str] = field(default_factory=list)      # mnemonics in order (SYS-97: trampolines of the vector table)
    words: list[int] = field(default_factory=list)    # literal pool words (`.word`) inside the function


_FUNC = re.compile(r"^([0-9a-f]+) <(.+)>:$")
_INSN = re.compile(r"^\s*([0-9a-f]+):\t(\S+)(?:\t(.*))?$")
_TARGET = re.compile(r"^([0-9a-f]+) <(.+?)(\+0x[0-9a-f]+)?>$")
_BRANCHES = {"b", "b.n", "b.w"}
_REG_RANGE = re.compile(r"([rsd])(\d+)(?:-[rsd](\d+))?")
_REG_ALIAS = {"sb": "r9", "sl": "r10", "fp": "r11", "ip": "r12", "sp": "r13", "lr": "r14", "pc": "r15"}


def _reglist_bytes(reglist: str) -> int:
    total = 0
    for item in reglist.strip("{} ").split(","):
        item = item.strip()
        item = _REG_ALIAS.get(item, item)
        m = _REG_RANGE.fullmatch(item)
        if not m:
            raise ValueError(f"register list not understood: {reglist!r}")
        count = 1 if m.group(3) is None else int(m.group(3)) - int(m.group(2)) + 1
        total += count * (8 if m.group(1) == "d" else 4)
    return total


def sp_decrement(op: str, args: str) -> int:
    """Bytes by which one Thumb-2 instruction moves the stack pointer down (0 if it does not).

    push/stmdb sp!/vpush/vstmdb sp! (register list), sub sp (immediate), str*/strd with pre-indexed negative offset
    on sp. A decrement by a register (alloca, VLA) cannot be bounded here and raises; the `.su` qualifier `dynamic`
    reports the same functions.
    """
    base = op.split(".")[0]
    if base in ("push", "vpush"):
        return _reglist_bytes(args)
    if base in ("stmdb", "vstmdb", "stmfd") and args.startswith("sp!"):
        return _reglist_bytes(args.split(",", 1)[1])
    if base in ("sub", "subw") and (args.startswith("sp, #") or args.startswith("sp, sp, #")):
        return int(args.rsplit("#", 1)[1], 0)
    if base in ("sub", "subw") and (args.startswith("sp, sp,") or args.startswith("sp, r")):
        raise ValueError(f"stack pointer moved by a register: {op} {args}")
    if base.startswith("str") and "[sp, #-" in args and args.endswith("!"):
        return int(args.split("#-", 1)[1].rstrip("]!"), 0)
    return 0


_LOAD = re.compile(r"^(r\d+|ip|lr), \[(r\d+|ip|lr)(?:, #(\d+))?\]$")
_VIRTUAL_WINDOW = 8     # instructions between the vptr load and the call


def _virtual_slot(recent: list[tuple[str, str]], reg: str) -> int | None:
    """Slot offset of a virtual call `ldr rV, [rObj]` ... `ldr rF, [rV, #off]` ... `blx rF` in the last instructions.

    Only this two-load shape counts as virtual: the function pointer is read from a table whose address was itself
    read from offset 0 of an object (the vptr). The result only labels the open site for the reviewer (which method
    of the interface is called); resolving it stays with the profile, because the slot alone does not say which
    class the object has - resolving over all vtables of the image merges unrelated classes into one large cycle.
    """
    for i in range(len(recent) - 1, -1, -1):
        op, args = recent[i]
        m = _LOAD.match(args) if op in ("ldr", "ldr.w") else None
        dest = args.split(",", 1)[0].strip()
        if dest != reg:
            continue
        if m is None:
            return None
        base, off = m.group(2), int(m.group(3) or 0)
        for j in range(i - 1, -1, -1):
            op2, args2 = recent[j]
            if args2.split(",", 1)[0].strip() != base:
                continue
            m2 = _LOAD.match(args2) if op2 in ("ldr", "ldr.w") else None
            return off if m2 is not None and int(m2.group(3) or 0) == 0 and m2.group(2) != base else None
        return None
    return None


def parse_objdump(text: str) -> dict[str, Function]:
    """Functions of `objdump -d -C --no-show-raw-insn`, keyed by their full (demangled) symbol name."""
    functions: dict[str, Function] = {}
    current: Function | None = None
    recent: list[tuple[str, str]] = []
    for line in text.splitlines():
        m = _FUNC.match(line)
        if m:
            name = m.group(2)
            current = functions.setdefault(name, Function(name, int(m.group(1), 16)))
            recent = []
            continue
        if current is None:
            continue
        m = _INSN.match(line)
        if not m:
            continue
        site = int(m.group(1), 16)
        op = m.group(2)
        args = (m.group(3) or "").split("\t;")[0].strip()
        current.ops.append(op)
        if op == ".word":
            try:
                current.words.append(int(args.split()[0], 16))
            except (ValueError, IndexError):
                pass
            continue
        current.sp_bytes += sp_decrement(op, args)
        if op == "bl":
            t = _TARGET.match(args)
            if t:
                current.calls.append((site, t.group(2)))
        elif op in _BRANCHES:
            t = _TARGET.match(args)
            if t and t.group(3) is None and t.group(2) != current.name:
                current.tail_calls.append((site, t.group(2)))
        elif op == "blx" or (op == "bx" and args != "lr"):
            t = _TARGET.match(args)
            if t:
                current.calls.append((site, t.group(2)))
            else:
                slot = _virtual_slot(recent, args)
                label = f"{op} {args}" if slot is None else f"{op} {args} (virtual, vtable offset {slot})"
                current.indirect.append((site, label))
        elif op in ("ldr", "ldr.w") and args.startswith("pc,") and "[sp]" not in args:
            # `ldr pc, [rT, rI, lsl #2]` is GCC's switch table (targets inside the function), not a call
            if not re.fullmatch(r"pc, \[r\d+, r\d+, lsl #2\]", args):
                current.indirect.append((site, f"{op} {args}"))
        elif op in ("mov", "mov.w") and args.startswith("pc,"):
            current.indirect.append((site, f"{op} {args}"))
        recent = (recent + [(op, args)])[-_VIRTUAL_WINDOW:]
    return functions


def objdump_tool() -> str | None:
    gcc_bin = os.environ.get("NX_ARM_GCC_BIN")
    if gcc_bin:
        for name in ("arm-none-eabi-objdump", "arm-none-eabi-objdump.exe"):
            candidate = Path(gcc_bin) / name
            if candidate.exists():
                return str(candidate)
    return shutil.which("arm-none-eabi-objdump")


def disassemble(elf: Path, objdump: str | None = None) -> str:
    tool = objdump or objdump_tool()
    if tool is None:
        raise FileNotFoundError("arm-none-eabi-objdump not found (PATH or NX_ARM_GCC_BIN)")
    return subprocess.run([tool, "-d", "-C", "--no-show-raw-insn", str(elf)], check=True, capture_output=True,
                          text=True, encoding="utf-8", errors="replace").stdout


_SECTION = re.compile(r"^Contents of section (\S+):$")
_DUMP_LINE = re.compile(r"^ ([0-9a-f]+) ((?:[0-9a-f]{2,8} ?){1,4})")


def parse_section_dump(text: str) -> dict[str, list[tuple[int, int]]]:
    """Words of `objdump -s` output per section: [(address, little-endian 32-bit word)] (SYS-97).

    Used for the vector table and for tables of function pointers (`.init_array`); a trailing partial word is
    dropped."""
    sections: dict[str, list[tuple[int, int]]] = {}
    current: list[tuple[int, int]] | None = None
    for line in text.splitlines():
        m = _SECTION.match(line)
        if m:
            current = sections.setdefault(m.group(1), [])
            continue
        m = _DUMP_LINE.match(line) if current is not None else None
        if not m:
            continue
        address = int(m.group(1), 16)
        data = bytes.fromhex("".join(m.group(2).split()))
        for off in range(0, len(data) - len(data) % 4, 4):
            current.append((address + off, int.from_bytes(data[off:off + 4], "little")))
    return sections


def dump_sections(elf: Path, names: list[str], objdump: str | None = None) -> dict[str, list[tuple[int, int]]]:
    """`objdump -s -j <name>` of the sections the profile needs; a section missing from the image stays absent."""
    if not names:
        return {}
    tool = objdump or objdump_tool()
    if tool is None:
        raise FileNotFoundError("arm-none-eabi-objdump not found (PATH or NX_ARM_GCC_BIN)")
    args = [tool, "-s"]
    for n in names:
        args += ["-j", n]
    out = subprocess.run(args + [str(elf)], check=True, capture_output=True, text=True, encoding="utf-8",
                         errors="replace").stdout
    return parse_section_dump(out)


# ------------------------------------------------------------------------- interrupt stack of the image (SYS-132)
# The size of the interrupt stack (MSP) and of its guard is a project value (memory.application.isr_stack_size /
# isr_stack_guard_size, generated memoryLayout.h) or the default of the family linker template. The image carries the
# value that was linked as linker symbols, so the check runs against the image instead of a number in the profile that
# could miss a project change.

IMAGE_STACK_SYMBOLS = ("__StackTop", "__StackLimit", "__StackGuardStart", "__StackGuardEnd")
_NM_LINE = re.compile(r"^([0-9a-fA-F]+)\s+[A-Za-z]\s+(\S+)\s*$")


def nm_tool() -> str | None:
    """arm-none-eabi-nm next to the objdump the analysis uses (same toolchain)."""
    objdump = objdump_tool()
    if objdump is None:
        return None
    candidate = Path(objdump).with_name(Path(objdump).name.replace("objdump", "nm"))
    return str(candidate) if candidate.exists() else shutil.which("arm-none-eabi-nm")


def parse_symbols(text: str) -> dict[str, int]:
    """Addresses of the interrupt stack symbols from `nm` output (other symbols are dropped)."""
    out = {}
    for line in text.splitlines():
        m = _NM_LINE.match(line)
        if m and m.group(2) in IMAGE_STACK_SYMBOLS:
            out[m.group(2)] = int(m.group(1), 16)
    return out


def image_symbols(elf: Path, nm: str | None = None) -> dict[str, int]:
    tool = nm or nm_tool()
    if tool is None:
        raise FileNotFoundError("arm-none-eabi-nm not found (PATH or NX_ARM_GCC_BIN)")
    return parse_symbols(subprocess.run([tool, str(elf)], check=True, capture_output=True, text=True,
                                        encoding="utf-8", errors="replace").stdout)


@dataclass(frozen=True)
class ImageStack:
    """Interrupt stack and guard as linked (bytes; None: the image has no such symbols)."""
    stack: int | None
    guard: int | None
    guard_below: bool               # the guard ends where the stack begins


def image_stack(symbols: dict[str, int]) -> ImageStack:
    stack = (symbols["__StackTop"] - symbols["__StackLimit"]
             if "__StackTop" in symbols and "__StackLimit" in symbols else None)
    guard = (symbols["__StackGuardEnd"] - symbols["__StackGuardStart"]
             if "__StackGuardEnd" in symbols and "__StackGuardStart" in symbols else None)
    below = guard is not None and stack is not None and symbols["__StackGuardEnd"] == symbols["__StackLimit"]
    return ImageStack(stack, guard, below)


def apply_image_stack(profile: "Profile", linked: ImageStack, source: str) -> list[str]:
    """The interrupt stack and guard of the image replace the profile values; the contexts on the interrupt stack
    get its size. Returns the notes for the report (a profile value that differs from the image)."""
    notes = []
    if linked.stack is not None:
        if profile.isr_stack_bytes is not None and profile.isr_stack_bytes != linked.stack:
            notes.append(f"interrupt stack {linked.stack} B linked, the profile says {profile.isr_stack_bytes} B "
                         "(project value memory.application.isr_stack_size?): the image counts")
        profile.isr_stack_bytes = linked.stack
        profile.isr_stack_source = source
        for c in profile.contexts:
            if c.on_isr_stack:
                c.stack_bytes = linked.stack
    if linked.guard is not None:
        if not linked.guard_below:
            # a guard that does not end at the stack limit does not catch an overflow of this stack
            raise ProfileError(f"{source}: the guard (__StackGuardStart..__StackGuardEnd) does not end at the "
                               "limit of the interrupt stack (__StackLimit)")
        if profile.isr_stack_guard_bytes is not None and profile.isr_stack_guard_bytes != linked.guard:
            notes.append(f"guard {linked.guard} B linked, the profile says {profile.isr_stack_guard_bytes} B "
                         "(project value memory.application.isr_stack_guard_size?): the image counts")
        profile.isr_stack_guard_bytes = linked.guard
    return notes


# ---------------------------------------------------------------------------------------------------- profile

class ProfileError(ValueError):
    pass


@dataclass
class Context:
    name: str
    kind: str                       # task | isr
    stack_bytes: int | None
    entries: list[str]
    dispatch: list[list[str]] = field(default_factory=list)   # segments; a segment lists alternatives
    context_bytes: int = 0
    level: int | None = None        # isr: NVIC level, lower = more urgent
    note: str = ""
    indirect: dict[str, list[str]] = field(default_factory=dict)   # resolutions valid in this context only
    # SYS-97: a task-kind context that runs on the interrupt stack itself (start-up before the scheduler); the
    # interrupt levels nest on top of it, so the interrupt stack must hold its depth plus the nested bound
    on_isr_stack: bool = False
    # SYS-97: the profile accepts this context's number as a lower bound, with the reason (report and --strict say
    # so); only for contexts whose overflow ends in a detected fault, never for a safety context
    lower_bound_accepted: str = ""
    # PLAT-107: the BSW task of ecu/configuration/taskStacks.yaml this context runs in. Its stack is then the value
    # of the build (generated tasks/BswTasks.h, NX_BSW_TASK_STACK_<TASK>) instead of a number in the profile, and its
    # worst case is checked against the floor of the task (min_stack_bytes) - the floor must hold it
    bsw_task: str = ""
    stack_source: str = ""          # where stack_bytes comes from (profile, or the generated header)


@dataclass
class VectorSpec:
    """SYS-97: which vectors of the image must be covered by an interrupt context (section `vectors:`)."""
    section: str
    skip: dict[int, str] = field(default_factory=dict)            # vector index -> reason (initial SP, ...)
    default_handlers: list[str] = field(default_factory=list)     # handlers of vectors nothing uses
    not_interrupts: dict[str, str] = field(default_factory=dict)  # handler -> reason (reset, covered elsewhere)
    # SYS-170: hooks of a generated interrupt wrapper (SAFE_ISR: pre hook, handler, post hook); a wrapper that calls
    # only these and a default handler is the wrapped form of an unused vector
    wrapper_hooks: list[str] = field(default_factory=list)


@dataclass
class Profile:
    name: str
    contexts: list[Context]
    frames: dict[str, int]
    frame_notes: dict[str, str]
    indirect: dict[str, list[str]]
    exception_frame_bytes: int = 0
    isr_stack_bytes: int | None = None
    ignore: list[str] = field(default_factory=list)
    safety_contexts: list[str] = field(default_factory=list)
    bounded_recursion: dict[str, int] = field(default_factory=dict)   # member -> times on the stack at most
    isr_stack_guard_bytes: int | None = None     # SYS-97: no-write guard directly below the interrupt stack
    vectors: VectorSpec | None = None            # SYS-97: vector table coverage
    isr_stack_source: str = "profile"            # SYS-132: where isr_stack_bytes comes from (profile or image)

    def table_sections(self) -> list[str]:
        """Sections named by `table:<section>` references (tables of function pointers, e.g. .init_array)."""
        refs = [t for v in self.indirect.values() for t in v]
        refs += [t for c in self.contexts for v in c.indirect.values() for t in v]
        return sorted({r[len(TABLE_PREFIX):] for r in refs if r.startswith(TABLE_PREFIX)})

    def sections(self) -> list[str]:
        """Every section the analysis reads from the image besides the disassembly."""
        names = set(self.table_sections())
        if self.vectors is not None:
            names.add(self.vectors.section)
        return sorted(names)


# a target reference `table:<section>` names every function whose address is stored in that section (SYS-97)
TABLE_PREFIX = "table:"


def _int(value, path):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProfileError(f"{path}: expected a non-negative integer, got {value!r}")
    return value


def _bounded(items) -> dict[str, int]:
    out = {}
    for i, item in enumerate(items):
        if isinstance(item, dict):
            if "function" not in item:
                raise ProfileError(f"bounded_recursion[{i}]: needs function")
            out[str(item["function"])] = _int(item.get("times", 1), f"bounded_recursion[{i}].times") or 1
        else:
            out[str(item)] = 1
    return out


def load_profile(path: Path) -> Profile:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ProfileError(f"{path}: top level must be a mapping")
    contexts = []
    for i, c in enumerate(data.get("contexts") or []):
        where = f"contexts[{i}]"
        if not isinstance(c, dict) or "name" not in c:
            raise ProfileError(f"{where}: needs a name")
        kind = c.get("kind", "task")
        if kind not in ("task", "isr"):
            raise ProfileError(f"{where}.kind: task or isr, got {kind!r}")
        entries = c.get("entries") or []
        if not entries:
            raise ProfileError(f"{where}.entries: at least one function")
        on_isr_stack = c.get("on_isr_stack", False)
        if not isinstance(on_isr_stack, bool):
            raise ProfileError(f"{where}.on_isr_stack: true or false, got {on_isr_stack!r}")
        if on_isr_stack and kind != "task":
            raise ProfileError(f"{where}.on_isr_stack: only a task-kind context runs below the interrupt levels")
        stack = c.get("stack_bytes")
        if stack is None and on_isr_stack:
            stack = data.get("isr_stack_bytes")
        contexts.append(Context(
            name=str(c["name"]), kind=kind,
            stack_bytes=None if stack is None else _int(stack, f"{where}.stack_bytes"),
            entries=[str(e) for e in entries],
            dispatch=[[str(a) for a in d] if isinstance(d, list) else [str(d)] for d in c.get("dispatch") or []],
            context_bytes=_int(c.get("context_bytes", 0), f"{where}.context_bytes"),
            level=None if c.get("level") is None else int(c["level"]), note=str(c.get("note", "")),
            indirect={str(k): [str(t) for t in (v or [])] for k, v in (c.get("indirect") or {}).items()},
            on_isr_stack=on_isr_stack, lower_bound_accepted=str(c.get("lower_bound_accepted") or "").strip(),
            bsw_task=_bsw_task(c.get("bsw_task"), kind, on_isr_stack, where),
            stack_source="" if stack is None else "profile"))
    names = [c.name for c in contexts]
    dup = {n for n in names if names.count(n) > 1}
    if dup:
        raise ProfileError(f"contexts: names used twice: {sorted(dup)}")
    for i, c in enumerate(contexts):
        if c.lower_bound_accepted and c.name in (data.get("safety_contexts") or []):
            raise ProfileError(f"contexts[{i}].lower_bound_accepted: a safety context must be complete")
        if c.lower_bound_accepted and c.kind == "isr":
            raise ProfileError(f"contexts[{i}].lower_bound_accepted: an interrupt level must be complete (it nests "
                               "on the interrupt stack of every other context)")
    frames, notes = {}, {}
    for k, v in (data.get("frames") or {}).items():
        if isinstance(v, dict):
            frames[str(k)] = _int(v.get("bytes"), f"frames.{k}.bytes")
            notes[str(k)] = str(v.get("source", ""))
        else:
            frames[str(k)] = _int(v, f"frames.{k}")
    indirect = {str(k): [str(t) for t in (v or [])] for k, v in (data.get("indirect") or {}).items()}
    safety = [str(s) for s in data.get("safety_contexts") or []]
    unknown = [s for s in safety if s not in names]
    if unknown:
        raise ProfileError(f"safety_contexts: not a context: {unknown}")
    guard = data.get("isr_stack_guard_bytes")
    return Profile(
        name=str(data.get("name", Path(path).stem)), contexts=contexts, frames=frames, frame_notes=notes,
        indirect=indirect, exception_frame_bytes=_int(data.get("exception_frame_bytes", 0), "exception_frame_bytes"),
        isr_stack_bytes=None if data.get("isr_stack_bytes") is None else _int(data["isr_stack_bytes"],
                                                                                 "isr_stack_bytes"),
        ignore=[str(i) for i in data.get("ignore") or []], safety_contexts=safety,
        bounded_recursion=_bounded(data.get("bounded_recursion") or []),
        isr_stack_guard_bytes=None if guard is None else _int(guard, "isr_stack_guard_bytes"),
        vectors=_vectors(data.get("vectors")))


def _bsw_task(value, kind: str, on_isr_stack: bool, where: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", value):
        raise ProfileError(f"{where}.bsw_task: a task name of taskStacks.yaml, got {value!r}")
    if kind != "task" or on_isr_stack:
        raise ProfileError(f"{where}.bsw_task: only a task-kind context on a task stack of its own")
    return value


# ------------------------------------------------------------------------------- BSW task stacks (PLAT-107)
# The task objects of the application take their stacks from project.yaml bsw_tasks (PLAT-26): tools/rtegen writes
# them to <build>/generated/rte/tasks/BswTasks.h, the defaults and floors are ecu/configuration/taskStacks.yaml. A
# context with `bsw_task` is measured against the stack of the build, and its worst case must fit below the floor of
# the task - the floor is what a project may set without further proof, so the analysis has to back it (decision
# PLAT-26/S1 of the safety review 2026-10-01).

BSW_TASKS_HEADER = Path("generated") / "rte" / "tasks" / "BswTasks.h"
TASK_STACKS_MANIFEST = Path("ecu") / "configuration" / "taskStacks.yaml"
_STACK_DEFINE = re.compile(r"^[ \t]*#define[ \t]+NX_BSW_TASK_STACK_([A-Z0-9_]+)[ \t]+(\d+)[uU]?\b", re.M)


def parse_bsw_tasks_header(text: str) -> dict[str, int]:
    """Stacks of the own task objects from tasks/BswTasks.h (name in lower case -> bytes); merged tasks are missing."""
    return {name.lower(): int(value) for name, value in _STACK_DEFINE.findall(text) if name != "BYTES"}


def apply_bsw_task_stacks(profile: Profile, stacks: dict[str, int], source: str) -> None:
    """The stack of every context with `bsw_task` from the build (BswTasks.h) instead of the profile."""
    for c in profile.contexts:
        if not c.bsw_task:
            continue
        if c.bsw_task not in stacks:
            raise ProfileError(f"{c.name}: bsw_task {c.bsw_task} has no stack of its own in {source} (merged into "
                               "another task, bsw_tasks.<task>.run_in, or no BSW task): analyse it as part of the "
                               "task it runs in")
        c.stack_bytes, c.stack_source = stacks[c.bsw_task], source


@dataclass
class TaskFloors:
    """min_stack_bytes per task of ecu/configuration/taskStacks.yaml (tasks without an entry take the default)."""
    path: Path
    default: int
    tasks: dict[str, int]

    def floor(self, task: str) -> int:
        return self.tasks.get(task, self.default)


def load_task_floors(path: Path) -> TaskFloors:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ProfileError(f"{path}: cannot be read ({exc})") from None

    def floor(entry, where):
        value = entry.get("min_stack_bytes") if isinstance(entry, dict) else None
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ProfileError(f"{path}: {where}.min_stack_bytes: expected a positive integer, got {value!r}")
        return value

    if not isinstance(data, dict) or not isinstance(data.get("tasks"), dict):
        raise ProfileError(f"{path}: expected a mapping with default and tasks")
    return TaskFloors(Path(path), floor(data.get("default"), "default"),
                      {str(k): floor(v, f"tasks.{k}") for k, v in data["tasks"].items()})


@dataclass
class FloorCheck:
    """Worst case of a context against the floor of its BSW task."""
    context: str
    task: str
    floor: int
    depth: int
    complete: bool
    stack_bytes: int | None
    stack_source: str

    @property
    def ok(self) -> bool:
        """False only if the floor is shown to be too small (a lower bound above it is proof enough)."""
        return self.depth <= self.floor

    @property
    def status(self) -> str:
        if not self.ok:
            return "BELOW THE WORST CASE"
        return "holds" if self.complete else "not shown (lower bound)"


def floor_checks(results: list[Result], floors: TaskFloors) -> list[FloorCheck]:
    return [FloorCheck(r.context.name, r.context.bsw_task, floors.floor(r.context.bsw_task), r.depth, r.complete,
                       r.context.stack_bytes, r.context.stack_source)
            for r in results if r.context.bsw_task]


def _vectors(v) -> VectorSpec | None:
    if v is None:
        return None
    if not isinstance(v, dict) or not v.get("section"):
        raise ProfileError("vectors: needs section (the vector table of the image)")
    skip = {}
    for k, reason in (v.get("skip") or {}).items():
        skip[_int(k, f"vectors.skip.{k}")] = str(reason)
    not_interrupts = v.get("not_interrupts") or {}
    if not isinstance(not_interrupts, dict):
        raise ProfileError("vectors.not_interrupts: a mapping handler -> reason")
    return VectorSpec(section=str(v["section"]), skip=skip,
                      default_handlers=[str(d) for d in v.get("default_handlers") or []],
                      not_interrupts={str(k): str(r) for k, r in not_interrupts.items()},
                      wrapper_hooks=[str(h) for h in v.get("wrapper_hooks") or []])


# --------------------------------------------------------------------------------------------------- analysis

@dataclass
class Finding:
    kind: str          # no-frame | unresolved-indirect | recursion | dynamic | unknown-entry
    function: str
    detail: str = ""


@dataclass
class Result:
    context: Context
    depth: int                         # bytes including context_bytes
    path: list[tuple[str, int]]        # deepest path: (function key, frame)
    findings: list[Finding]
    largest_frame: tuple[str, int] = ("", 0)   # SYS-97: largest single frame of every function the context reaches

    @property
    def complete(self) -> bool:
        return not self.findings

    @property
    def margin(self) -> int | None:
        return None if self.context.stack_bytes is None else self.context.stack_bytes - self.depth


@dataclass
class VectorRow:
    index: int
    address: int
    handler: str          # symbol at the address ("" if none)
    status: str           # context name, "default", "trampoline", "not an interrupt", "skipped", "empty", "UNCOVERED"
    detail: str = ""


@dataclass
class IsrStackBudget:
    """SYS-97: what the interrupt stack must hold - the start-up context below it plus every level nested."""
    nested: int                           # deepest handler per level plus one exception frame, summed
    below: Result | None                  # deepest on_isr_stack context (start-up), None if the profile has none
    total: int
    complete: bool
    uncovered_vectors: list[VectorRow] = field(default_factory=list)


@dataclass
class GuardCheck:
    """SYS-97: can a frame jump over the no-write guard below the interrupt stack?

    A write below the guard before the first write into it is possible only when the stack pointer moves by more
    than the guard without writing: worst case one frame that is reserved but not yet written (its function may
    write its locals later, from the bottom up) and then the next frame or an exception frame on top of that. The
    guard is sufficient when it holds the largest frame on the interrupt stack plus the larger of that frame and the
    exception frame. While a context on the interrupt stack is incomplete (an accepted lower bound included), the
    functions it may reach are not known: then the largest frame of the whole image counts."""
    guard: int
    largest: tuple[str, int]              # (function, frame) over every context on the interrupt stack
    required: int

    @property
    def ok(self) -> bool:
        return self.guard >= self.required


class Analysis:
    """Call graph on the exact (demangled) symbols of the image; frames looked up by symbol and by key.

    Frame of a symbol = the profile value if the profile names it (by symbol or key), else the larger of the sum of
    its stack pointer decrements and the largest `.su` value under its key.
    """

    def __init__(self, frames: list[Frame], functions: dict[str, Function], profile: Profile,
                 sections: dict[str, list[tuple[int, int]]] | None = None):
        self.profile = profile
        self.functions = functions
        self.sections = sections or {}
        self.at_address: dict[int, list[str]] = {}
        for name, fn in functions.items():
            self.at_address.setdefault(fn.address, []).append(name)
        self.su_frame: dict[str, int] = {}
        self.dynamic: dict[str, str] = {}
        for f in frames:
            k = key(f.name)
            self.su_frame[k] = max(self.su_frame.get(k, 0), f.size)
            if f.qualifier != "static":
                self.dynamic[k] = f.qualifier
        self.by_key: dict[str, list[str]] = {}
        for name in functions:
            self.by_key.setdefault(key(name), []).append(name)
        self.profile_frame: dict[str, int] = {}
        for ref, v in profile.frames.items():
            for sym in self.resolve(ref) or [ref]:
                self.profile_frame[sym] = v
        self.global_resolved = {sym: set(self._targets(v)) for k, v in profile.indirect.items()
                                for sym in self.resolve(k)}
        self.resolved = self.global_resolved
        self.ignored = {sym for i in profile.ignore for sym in (self.resolve(i) or [i])}
        self.bounded = {sym: n for b, n in profile.bounded_recursion.items() for sym in (self.resolve(b) or [b])}

    def resolve(self, ref: str) -> list[str]:
        """Symbols a profile reference names: the exact demangled symbol, else every symbol with its key."""
        if ref in self.functions:
            return [ref]
        return list(self.by_key.get(key(ref), []))

    def table_functions(self, section: str) -> list[str]:
        """Functions whose address (Thumb bit cleared) a table section holds; words that are no function start are
        not functions (0, -1, data). A section the image dump does not contain is an error, not an empty table."""
        if section not in self.sections:
            raise ProfileError(f"table:{section}: section not in the image dump (--elf, or --sections-dump)")
        out = []
        for _, word in self.sections[section]:
            out += self.at_address.get(word & ~1, [])
        return out

    def _targets(self, refs: list[str]) -> list[str]:
        out = []
        for r in refs:
            if r.startswith(TABLE_PREFIX):
                out += self.table_functions(r[len(TABLE_PREFIX):])
            else:
                out += self.resolve(r) or [r]
        return out

    def frame(self, sym: str) -> int | None:
        if sym in self.profile_frame:
            return self.profile_frame[sym]
        fn = self.functions.get(sym)
        su = self.su_frame.get(key(sym))
        if fn is None:
            return su
        return fn.sp_bytes if su is None else max(su, fn.sp_bytes)

    def frame_source(self, sym: str) -> str:
        """Where the frame of a symbol comes from: profile, .su, disassembly (larger than or without .su), none."""
        if sym in self.profile_frame:
            return "profile"
        fn = self.functions.get(sym)
        su = self.su_frame.get(key(sym))
        if su is not None and (fn is None or fn.sp_bytes <= su):
            return ".su"
        return "disassembly" if fn is not None else "none"

    def cross_check(self) -> tuple[int, int, int]:
        """(functions with both a .su value and a disassembled prologue, equal, disassembly larger)."""
        both = [n for n in self.functions if key(n) in self.su_frame]
        equal = sum(1 for n in both if self.functions[n].sp_bytes == self.su_frame[key(n)])
        larger = sum(1 for n in both if self.functions[n].sp_bytes > self.su_frame[key(n)])
        return len(both), equal, larger

    def _successors(self, sym: str, follow_indirect: bool, findings: dict) -> set[str]:
        fn = self.functions.get(sym)
        callees: set[str] = set()
        if fn is not None:
            callees.update(c for _, c in fn.calls)
            callees.update(c for _, c in fn.tail_calls)
        if follow_indirect:
            open_sites = []
            if fn is not None:
                open_sites += [f"0x{a:x}: {i}" for a, i in fn.indirect]
            if sym in self.resolved:
                callees |= self.resolved[sym]
            elif open_sites:
                findings.setdefault(("unresolved-indirect", sym), Finding(
                    "unresolved-indirect", sym, ", ".join(open_sites)))
        return callees - self.ignored

    def _frame_checked(self, sym: str, findings: dict) -> int:
        frame = self.frame(sym)
        if frame is None:
            findings.setdefault(("no-frame", sym), Finding(
                "no-frame", sym, "not disassembled, no .su entry, no profile frame"))
            frame = 0
        if key(sym) in self.dynamic and sym not in self.profile_frame:
            findings.setdefault(("dynamic", sym), Finding("dynamic", sym, self.dynamic[key(sym)]))
        return frame

    def _depths(self, roots: list[str], follow_indirect: bool, findings: dict) -> dict[str, tuple[int, list]]:
        """Deepest stack below every node reachable from the roots: (bytes, path).

        Strongly connected components (Tarjan, iterative) are recursion: reported, and bounded as if every member
        were on the stack once - a lower bound for real recursion.
        """
        succ: dict[str, list[str]] = {}
        index: dict[str, int] = {}
        low: dict[str, int] = {}
        on_stack: set[str] = set()
        stack: list[str] = []
        comp_of: dict[str, int] = {}
        comps: list[list[str]] = []
        counter = 0
        for root in roots:
            if root in index:
                continue
            work = [(root, 0)]
            while work:
                node, i = work.pop()
                if i == 0:
                    index[node] = low[node] = counter
                    counter += 1
                    stack.append(node)
                    on_stack.add(node)
                    succ[node] = sorted(self._successors(node, follow_indirect, findings))
                recurse = False
                children = succ[node]
                while i < len(children):
                    c = children[i]
                    i += 1
                    if c not in index:
                        work.append((node, i))
                        work.append((c, 0))
                        recurse = True
                        break
                    if c in on_stack:
                        low[node] = min(low[node], index[c])
                if recurse:
                    continue
                if low[node] == index[node]:
                    comp = []
                    while True:
                        w = stack.pop()
                        on_stack.discard(w)
                        comp_of[w] = len(comps)
                        comp.append(w)
                        if w == node:
                            break
                    comps.append(comp)
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
        # components are completed in reverse topological order: callees first
        depth: dict[str, tuple[int, list]] = {}
        for ci, comp in enumerate(comps):
            members = set(comp)
            recursive = len(comp) > 1 or comp[0] in succ[comp[0]]
            frames = {m: self._frame_checked(m, findings) for m in comp}
            best, best_path = 0, []
            for m in comp:
                for c in succ[m]:
                    if c not in members and depth[c][0] > best:
                        best, best_path = depth[c]
            if recursive:
                names = sorted(comp)
                if not members <= set(self.bounded):
                    findings.setdefault(("recursion", names[0]), Finding(
                        "recursion", names[0], f"cycle of {len(comp)} function(s): " + ", ".join(names[:6])
                        + (" ..." if len(names) > 6 else "")))
                # every member as often as the profile allows it on the stack (once if not named)
                total = sum(frames[m] * self.bounded.get(m, 1) for m in comp)
                entry = [(m, frames[m] * self.bounded.get(m, 1)) for m in names]
                for m in comp:
                    depth[m] = (total + best, entry + best_path)
            else:
                m = comp[0]
                depth[m] = (frames[m] + best, [(m, frames[m])] + best_path)
        return depth

    def context(self, ctx: Context) -> Result:
        self.resolved = {k: set(v) for k, v in self.global_resolved.items()}
        for ref, targets in ctx.indirect.items():
            for sym in self.resolve(ref):
                self.resolved.setdefault(sym, set()).update(self._targets(targets))
        findings: dict = {}
        reached: set[str] = set()
        # dispatch: a chain; each step's own frame stays on the stack below the next step, and each step's direct
        # subtree is a candidate for the deepest point on the way
        total, path = ctx.context_bytes, []
        prefix, prefix_path = ctx.context_bytes, []
        for seg in ctx.dispatch:
            syms = []
            for alt in seg:
                found = self.resolve(alt)
                if not found:
                    findings[("unknown-entry", alt)] = Finding("unknown-entry", alt, "dispatch function not in the image")
                syms += found
            if not syms:
                continue
            depth = self._depths(syms, False, findings)
            reached.update(depth)
            for sym in syms:
                if prefix + depth[sym][0] > total:
                    total, path = prefix + depth[sym][0], prefix_path + depth[sym][1]
            step = max(syms, key=lambda x: self._frame_checked(x, findings))
            prefix += self._frame_checked(step, findings)
            prefix_path = prefix_path + [(step, self._frame_checked(step, findings))]
        roots = []
        for e in ctx.entries:
            found = self.resolve(e)
            if not found:
                findings[("unknown-entry", e)] = Finding("unknown-entry", e, "entry not in the image")
            roots += found
        depth = self._depths(roots, True, findings)
        reached.update(depth)
        for sym in roots:
            if prefix + depth[sym][0] > total:
                total, path = prefix + depth[sym][0], prefix_path + depth[sym][1]
        largest = max(((s, self.frame(s) or 0) for s in reached), key=lambda sf: (sf[1], sf[0]), default=("", 0))
        return Result(ctx, total, path, list(findings.values()), largest)

    def isr_nesting(self, results: list[Result]) -> tuple[int, list[tuple[int | None, Result]]]:
        """Worst MSP use: per NVIC level the deepest handler plus one exception frame, summed over the levels
        (handlers of one level cannot preempt each other). Handlers without a level count as a level of their own."""
        per_level: dict = {}
        for r in results:
            if r.context.kind != "isr":
                continue
            lvl = r.context.level if r.context.level is not None else ("own", r.context.name)
            if lvl not in per_level or r.depth > per_level[lvl].depth:
                per_level[lvl] = r
        chosen = sorted(per_level.items(), key=lambda kv: (isinstance(kv[0], tuple), kv[0] if not isinstance(
            kv[0], tuple) else 0))
        total = sum(r.depth + self.profile.exception_frame_bytes for _, r in chosen)
        return total, [(lvl if not isinstance(lvl, tuple) else None, r) for lvl, r in chosen]

    # ------------------------------------------------------------------------------------ SYS-97: MSP budget

    def _is_trampoline_to(self, fn: Function, targets: set[int]) -> bool:
        """A weak vector stub that only loads a literal and branches to it (`ldr rX, [pc, #n]` / `bx rX`), or only
        tail-calls, and every literal and tail-call target is one of `targets` (the default handlers)."""
        if fn.calls or fn.indirect and not all(i.startswith("bx r") for _, i in fn.indirect):
            return False
        if any(op != ".word" and op.split(".")[0] not in ("ldr", "bx", "b", "nop") for op in fn.ops):
            return False
        branches = [w & ~1 for w in fn.words]
        branches += [self.functions[t].address for _, t in fn.tail_calls if t in self.functions]
        return bool(branches) and all(b in targets for b in branches)

    def _is_wrapper_of(self, fn: Function, defaults: set[int], hooks: set[int]) -> bool:
        """SYS-170: a generated interrupt wrapper of an unused vector - it calls (or tail-calls) only the wrapper hooks
        and a default handler, at least one default handler, and has no indirect call."""
        if fn.indirect or not defaults:
            return False
        targets = [self.functions[t].address for _, t in fn.calls + fn.tail_calls if t in self.functions]
        if len(targets) != len(fn.calls) + len(fn.tail_calls):
            return False
        return any(t in defaults for t in targets) and all(t in defaults or t in hooks for t in targets)

    def vector_rows(self, results: list[Result]) -> list[VectorRow]:
        """Every vector of the table with the context that covers it (profile `vectors:`). A vector whose handler
        is neither an entry of an interrupt context (or of the start-up context on the interrupt stack, for the
        reset vector), nor a default handler or a stub to one, nor a generated wrapper of one (SYS-170), nor a named
        non-interrupt, is UNCOVERED: its stack is not in the nested bound."""
        spec = self.profile.vectors
        if spec is None:
            return []
        if spec.section not in self.sections:
            raise ProfileError(f"vectors.section: {spec.section} not in the image dump (--elf, or --sections-dump)")
        covered: dict[int, str] = {}
        for r in results:
            if r.context.kind != "isr" and not r.context.on_isr_stack:
                continue
            for e in r.context.entries:
                for sym in self.resolve(e):
                    covered.setdefault(self.functions[sym].address, r.context.name)
        defaults = {self.functions[s].address for d in spec.default_handlers for s in self.resolve(d)}
        hooks = {self.functions[s].address for h in spec.wrapper_hooks for s in self.resolve(h)}
        not_irq = {self.functions[s].address: reason for h, reason in spec.not_interrupts.items()
                   for s in self.resolve(h)}
        table = self.sections[spec.section]
        base = table[0][0] if table else 0
        rows = []
        for addr_of_entry, word in table:
            index = (addr_of_entry - base) // 4
            target = word & ~1
            names = self.at_address.get(target, [])
            handler = names[0] if names else ""
            if index in spec.skip:
                rows.append(VectorRow(index, word, handler, "skipped", spec.skip[index]))
            elif word in (0, 0xFFFFFFFF):
                rows.append(VectorRow(index, word, handler, "empty"))
            elif target in covered:
                rows.append(VectorRow(index, word, handler, covered[target]))
            elif target in not_irq:
                rows.append(VectorRow(index, word, handler, "not an interrupt", not_irq[target]))
            elif target in defaults:
                rows.append(VectorRow(index, word, handler, "default"))
            elif names and self._is_trampoline_to(self.functions[handler], defaults):
                rows.append(VectorRow(index, word, handler, "trampoline", "branches to a default handler"))
            elif names and self._is_wrapper_of(self.functions[handler], defaults, hooks):
                rows.append(VectorRow(index, word, handler, "wrapper", "wrapper hooks around a default handler"))
            else:
                rows.append(VectorRow(index, word, handler, "UNCOVERED",
                                      "handler of no interrupt context (profile contexts, kind isr)"))
        return rows

    def isr_stack_budget(self, results: list[Result]) -> IsrStackBudget:
        nested, levels = self.isr_nesting(results)
        below = [r for r in results if r.context.on_isr_stack]
        deepest = max(below, key=lambda r: r.depth) if below else None
        uncovered = [row for row in self.vector_rows(results) if row.status == "UNCOVERED"]
        complete = (all(r.complete for _, r in levels)
                    and all(r.complete or r.context.lower_bound_accepted for r in below) and not uncovered)
        return IsrStackBudget(nested, deepest, nested + (deepest.depth if deepest else 0), complete, uncovered)

    def guard_check(self, results: list[Result]) -> GuardCheck | None:
        if self.profile.isr_stack_guard_bytes is None:
            return None
        on_msp = [r for r in results if r.context.kind == "isr" or r.context.on_isr_stack]
        candidates = [r.largest_frame for r in on_msp]
        if any(not r.complete for r in on_msp):
            # a context on the interrupt stack that is only a lower bound (accepted or not) may reach any function:
            # the largest frame of the whole image counts
            candidates += [(n, self.frame(n) or 0) for n in self.functions]
        largest = max(candidates, key=lambda sf: (sf[1], sf[0]), default=("", 0))
        required = largest[1] + max(largest[1], self.profile.exception_frame_bytes)
        return GuardCheck(self.profile.isr_stack_guard_bytes, largest, required)


def analyse(su_root: Path, disassembly: str, profile: Profile,
            sections: dict[str, list[tuple[int, int]]] | None = None) -> tuple[Analysis, list[Result]]:
    analysis = Analysis(load_su_dir(su_root), parse_objdump(disassembly), profile, sections)
    return analysis, [analysis.context(c) for c in profile.contexts]
