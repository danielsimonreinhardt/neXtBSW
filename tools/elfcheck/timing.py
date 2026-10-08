"""RTE timing and interrupt priorities of a linked image against the configuration (PLAT-82, tool-classification.md
proposal F, finding TF7, requirement SSR-081).

rtegen turns `rte.yaml` and the BSW task profile `ecu/asyncCoreConfiguration/tasks.yaml` into the task list, the
FreeRTOS priorities (context numbers of async/Config.h), the periods and offsets of the runnables - which SM-05 uses as
deadlines - and bswgen turns the project's interrupt ranks into NVIC levels. The tests of both generators check their
output with their own expectations. This check takes a second path:

Expectation, read here with PyYAML and xml.etree, without importing rtegen, bswgen or nxconfig:
- `project.yaml`: `project.rte`, `project.board` (-> derivative), `interrupts.max_syscall_level` and `interrupts.ranks`;
- the ECU description (`rte.yaml`): application tasks (name, period, priority key, runnables with offsets),
  `base_period_ms`, components and their paths;
- the component descriptions: of a Simulink component `<path>/generated/arxml/*.arxml` (runnables, their symbols,
  TIMING-EVENT periods, INIT-EVENTs and mode switch events), of a hand-written C component (RTE-20, PLAT-102) its
  `<path>/swc.yaml` with `implementation: c` (name, runnables with `period_ms`) - read by an own narrow reader, not
  by rtegen/swc_yaml: a difference between the two readers is exactly what this path is there to find;
- the BSW task profile (`tasks.yaml`): BSW tasks with their keys, OS tick, default application task;
- the derivative (`mcudb`): core and `nvic_priority_bits`.
The rules applied are the documented ones (tasks.yaml header, rte.yaml header, swc.yaml header of
tools/swc/swc_yaml.py, interruptPriorities.h / architecture section 4): all tasks sorted by key and numbered 1..N
(idle 0, timer N+1); a runnable's deadline is its period, a task cycle's deadline the task period; a C component has
its init runnable <Swc>_Init first, then its periodic runnables in file order with the C function <Swc>_<Runnable>;
NVIC level = max_syscall_level + rank.

Image (the thing under test), read with the own ELF reader (elffile.py) and DWARF reader (dwarf.py):
- the constant tables `rte::TASKS`, `rte::RUNNABLES` and `rte::MODE_RUNNABLES` (names, function pointers resolved to
  symbols, periods, offsets, task indices, tick counts), decoded with the layout of the structs of RteConfig.h
  (written here once more on purpose, AAPCS natural alignment, 32 bit little endian D11);
- the enumerators `TASK_<NAME>` and `ASYNC_CONFIG_TASK_COUNT` of async/Config.h from the debugging information of every
  compilation unit (the FreeRTOS priority of every task, BSW tasks included);
- the macro definitions of `.debug_macro` (-g3): interrupt ranks, syscall level, NVIC priority bits and OS tick as the
  image was compiled;
- optionally (`check_irq_probe`) the NVIC levels as the target compiler evaluates the generated headers
  (interruptPriorities.h with the platform's nxInterruptPriority.h), in a probe linked with the own reader.

Core logic without Qt (Studio requirement); messages name the image and the table, task, runnable or interrupt, and
expectation errors name the YAML path.
"""

from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from math import gcd
from pathlib import Path

import yaml

try:                                    # package import (tests, Studio) or script import (python tools/elfcheck)
    from . import check, dwarf, elffile
except ImportError:                     # pragma: no cover - run as a script
    import check  # type: ignore[no-redef]
    import dwarf  # type: ignore[no-redef]
    import elffile  # type: ignore[no-redef]

REPO = Path(__file__).resolve().parents[2]
TASKS_FILE = REPO / "ecu" / "asyncCoreConfiguration" / "tasks.yaml"
NX_INTERRUPT_INCLUDE = REPO / "platforms" / "common" / "interruptPriority" / "include"

# Symbols and struct layouts of the generated tables (tools/rtegen/templates/RteConfig.h.j2), written here on purpose:
# the check must not read them from the code it checks. Field kinds: p pointer, u8/u32 integers, z size_t.
TASKS_SYMBOL = "_ZN3rte5TASKSE"                         # rte::TASKS
RUNNABLES_SYMBOL = "_ZN3rte9RUNNABLESE"                 # rte::RUNNABLES
MODE_RUNNABLES_SYMBOL = "_ZN3rte14MODE_RUNNABLESE"      # rte::MODE_RUNNABLES
TASK_FIELDS = (("name", "p"), ("context", "u8"), ("periodMs", "u32"), ("firstRunnable", "z"),
               ("runnableCount", "z"), ("hyperTicks", "u32"))
RUNNABLE_FIELDS = (("name", "p"), ("function", "p"), ("periodMs", "u32"), ("offsetMs", "u32"), ("task", "u8"),
                   ("periodTicks", "u32"), ("offsetTicks", "u32"))
MODE_RUNNABLE_FIELDS = (("name", "p"), ("function", "p"), ("task", "u8"), ("activation", "u8"), ("mode", "p"),
                        ("fromMode", "p"))
NO_TASK = 0xFF
POINTER_SIZE = 4                                        # D11: 32 bit targets
TASK_COUNT_ENUMERATOR = "ASYNC_CONFIG_TASK_COUNT"

# Default RTOS syscall level per core when the project gives none (Cortex-M: level 0 stays unmaskable, ARMv6-M has
# no BASEPRI and masks everything: 0). Own table, like cores.py.
MAX_SYSCALL_DEFAULT = {"cortex-m0plus": 0, "cortex-m4f": 1, "cortex-m7f": 1, "cortex-m33": 1}
NVIC_CORES = set(MAX_SYSCALL_DEFAULT)
MCPU = {"cortex-m0plus": "cortex-m0plus", "cortex-m4f": "cortex-m4", "cortex-m7f": "cortex-m7",
        "cortex-m33": "cortex-m33"}

AR_NS = "{http://autosar.org/schema/r4.0}"

# Description of a software component (tools/swc/swc_yaml.py header, RTE-20): written here once more on purpose.
SWC_FILE = "swc.yaml"
IMPLEMENTATIONS = ("simulink", "c")


class TimingConfigError(ValueError):
    """The configuration cannot give an expectation (the message names the file and the YAML path)."""


# ---------------------------------------------------------------------- expectation

@dataclass
class ExpRunnable:
    key: str                            # Component.Runnable
    symbol: str                         # C entry point (ARXML SYMBOL)
    period_ms: int                      # 0: init or mode switch runnable
    kind: str                           # "periodic", "init", "mode"
    offset_ms: int = 0
    task: str | None = None             # application task name


@dataclass
class ExpTask:
    name: str
    key: int                            # ordering key of tasks.yaml / rte.yaml
    bsw: bool
    where: str
    period_ms: int = 0                  # application tasks
    index: int = -1                     # row in rte::TASKS (application tasks in configuration order)
    priority: int = 0                   # FreeRTOS priority = context number
    runnables: list[ExpRunnable] = field(default_factory=list)
    modes: list[ExpRunnable] = field(default_factory=list)
    above: bool = False                 # BSW task no application task may exceed (above_application)

    @property
    def hyper_ticks(self) -> int:
        result = 1
        for r in self.runnables:
            ticks = r.period_ms // self.period_ms
            result = result * ticks // gcd(result, ticks)
        return result


@dataclass(frozen=True)
class ExpIrq:
    source: str
    name: str
    rank: int
    level: int                          # NVIC level (Cortex-M)
    ident: str                          # <SOURCE>_<NAME> of the macros NX_IRQ_RANK_* / NX_IRQ_LEVEL_*


@dataclass
class TimingExpectation:
    project_file: Path | None
    rte_file: Path | None
    tasks_file: Path
    tick_us: int
    tasks: list[ExpTask]                # every task in FreeRTOS priority order (BSW and application)
    app: list[ExpTask]                  # application tasks in configuration order (= rows of rte::TASKS)
    init: list[ExpRunnable]
    core: str | None = None
    nvic_bits: int | None = None
    max_syscall: int | None = None
    irqs: list[ExpIrq] = field(default_factory=list)

    @property
    def runnables(self) -> list[ExpRunnable]:
        """Rows of rte::RUNNABLES: periodic runnables per task in call order, then the init runnables."""
        return [r for t in self.app for r in t.runnables] + self.init

    @property
    def task_count(self) -> int:
        """ASYNC_CONFIG_TASK_COUNT: idle 0, tasks 1..N, the timer task gets this number."""
        return len(self.tasks) + 1


def _load_yaml(path: Path) -> dict:
    try:
        return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except OSError as e:
        raise TimingConfigError(f"{path}: cannot read ({e.strerror})") from None


def _int(value, file: Path, where: str, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TimingConfigError(f"{file}: {where}: integer expected, got {value!r}")
    if minimum is not None and value < minimum:
        raise TimingConfigError(f"{file}: {where}: {value} is below {minimum}")
    return value


def _period_ms(text: str | None, where: str) -> int:
    try:
        ms = Decimal((text or "").strip()) * 1000
    except InvalidOperation:
        raise TimingConfigError(f"{where}: PERIOD '{text}' is not a number") from None
    if ms <= 0 or ms != ms.to_integral_value():
        raise TimingConfigError(f"{where}: PERIOD {text} s is not a whole number of milliseconds")
    return int(ms)


def load_component(name: str, folder: Path) -> list[ExpRunnable]:
    """Runnables of component `name` in the ARXML files of `folder`, in the order of RUNNABLES."""
    files = sorted(Path(folder).glob("*.arxml"))
    if not files:
        raise TimingConfigError(f"{folder}: no ARXML files for component {name}")
    for f in files:
        root = ET.parse(f).getroot()
        for swc in root.iter():
            if not swc.tag.endswith("SW-COMPONENT-TYPE") or swc.findtext(f"{AR_NS}SHORT-NAME") != name:
                continue
            behavior = swc.find(f"{AR_NS}INTERNAL-BEHAVIORS/{AR_NS}SWC-INTERNAL-BEHAVIOR")
            if behavior is None:
                raise TimingConfigError(f"{f}: component {name} has no SWC-INTERNAL-BEHAVIOR")
            periods: dict[str, int] = {}
            inits: set[str] = set()
            modes: set[str] = set()
            events = behavior.find(f"{AR_NS}EVENTS")
            for ev in list(events) if events is not None else []:
                ref = ev.findtext(f"{AR_NS}START-ON-EVENT-REF") or ""
                target = ref.strip().rsplit("/", 1)[-1]
                kind = ev.tag.replace(AR_NS, "")
                if kind == "TIMING-EVENT":
                    periods[target] = _period_ms(ev.findtext(f"{AR_NS}PERIOD"), f"{f}: {name}.{target}")
                elif kind == "INIT-EVENT":
                    inits.add(target)
                elif kind == "SWC-MODE-SWITCH-EVENT":
                    modes.add(target)
            result = []
            runnables = behavior.find(f"{AR_NS}RUNNABLES")
            for r in list(runnables) if runnables is not None else []:
                rname = r.findtext(f"{AR_NS}SHORT-NAME")
                symbol = (r.findtext(f"{AR_NS}SYMBOL") or rname).strip()
                key = f"{name}.{rname}"
                if rname in modes:
                    result.append(ExpRunnable(key, symbol, 0, "mode"))
                elif rname in periods:
                    result.append(ExpRunnable(key, symbol, periods[rname], "periodic"))
                elif rname in inits or rname == f"{name}_Init":
                    result.append(ExpRunnable(key, symbol, 0, "init"))
                else:
                    raise TimingConfigError(f"{f}: {key}: runnable without timing, init or mode switch event")
            return result
    raise TimingConfigError(f"{folder}: component {name} not found in {', '.join(p.name for p in files)}")


def component_implementation(folder: Path) -> str:
    """'c' when `folder`/swc.yaml says `implementation: c` (RTE-20), else 'simulink' (also without swc.yaml)."""
    file = Path(folder) / SWC_FILE
    if not file.is_file():
        return "simulink"
    data = _load_yaml(file)
    value = data.get("implementation", "simulink") if isinstance(data, dict) else "simulink"
    if value not in IMPLEMENTATIONS:
        raise TimingConfigError(f"{file}: implementation: '{value}' is none of {', '.join(IMPLEMENTATIONS)}")
    return value


def load_c_component(name: str, folder: Path) -> list[ExpRunnable]:
    """Runnables of the hand-written C component `name` from `folder`/swc.yaml (RTE-20), in the order of RUNNABLES.

    Own narrow reader (PLAT-102): only name, runnables and their periods, nothing of ports or parameters, and no import
    of rtegen or swc_yaml - the requirement (SSR-081 (b)) asks for a parser that uses no generator code. The rules
    are the documented ones of the swc.yaml header: the init runnable <Swc>_Init (C symbol of the same name) comes
    first, then one periodic runnable per entry with the C function <Swc>_<Runnable>.
    """
    file = Path(folder) / SWC_FILE
    data = _load_yaml(file)
    if not isinstance(data, dict):
        raise TimingConfigError(f"{file}: mapping with name and runnables expected")
    if data.get("name") != name:
        raise TimingConfigError(f"{file}: name: describes component {data.get('name')!r}, the ECU description "
                                f"expects {name}")
    entries = data.get("runnables")
    if not isinstance(entries, list) or not entries:
        raise TimingConfigError(f"{file}: runnables: at least one runnable with name and period_ms is needed")
    init = f"{name}_Init"
    result = [ExpRunnable(f"{name}.{init}", init, 0, "init")]
    seen: set[str] = {init}
    for i, entry in enumerate(entries):
        where = f"runnables[{i}]"
        rname = entry.get("name") if isinstance(entry, dict) else None
        if not isinstance(rname, str) or not re.fullmatch(r"[A-Za-z_]\w*", rname):
            raise TimingConfigError(f"{file}: {where}.name: C identifier expected, got {rname!r}")
        if rname in seen:
            raise TimingConfigError(f"{file}: {where}.name: runnable {rname} is named twice")
        seen.add(rname)
        period = _int(entry.get("period_ms"), file, f"{where}.period_ms", 1)
        result.append(ExpRunnable(f"{name}.{rname}", f"{name}_{rname}", period, "periodic"))
    return result


def load_any_component(name: str, folder: Path) -> list[ExpRunnable]:
    """Runnables of a component of the ECU description: swc.yaml of a C component or ARXML of a Simulink one."""
    if component_implementation(folder) == "c":
        return load_c_component(name, folder)
    return load_component(name, Path(folder) / "generated" / "arxml")


def _runnable_item(item, rte: Path, where: str) -> tuple[str, int]:
    if isinstance(item, str):
        return item, 0
    if isinstance(item, dict) and isinstance(item.get("runnable"), str):
        return item["runnable"], _int(item.get("offset_ms", 0), rte, f"{where}.offset_ms", 0)
    raise TimingConfigError(f"{rte}: {where}: expected Component.Runnable or {{runnable: ..., offset_ms: ...}}")


def _component_dir(ecu, rte_file, repo, path: str) -> Path:
    """Folder of a component of the ECU description (RTE-22), read here again instead of asking rtegen.

    Format 2 bases `components[].path` on the folder of the rte.yaml, format 1 on the repository root. The rule is
    the documented one (rte.yaml header); this file deliberately takes the second path and imports no generator.
    """
    version = ecu.get("schema_version") if isinstance(ecu, dict) else None
    relative_to_file = isinstance(version, int) and not isinstance(version, bool) and version >= 2
    base = Path(rte_file).resolve().parent if relative_to_file else Path(repo)
    return Path(os.path.normpath(base / path))


def load_tasks(rte_file: Path, tasks_file: Path = TASKS_FILE, repo: Path = REPO) -> TimingExpectation:
    """Task list, priorities, periods and runnables expected from the ECU description and the BSW task profile."""
    manifest = _load_yaml(tasks_file)
    tick_us = _int(manifest.get("tick_us"), tasks_file, "tick_us", 1)
    max_priorities = _int(manifest.get("max_priorities"), tasks_file, "max_priorities", 3)
    bsw = []
    for i, entry in enumerate(manifest.get("tasks") or []):
        where = f"tasks[{i}]"
        bsw.append(ExpTask(str(entry.get("name")), _int(entry.get("priority"), tasks_file, f"{where}.priority", 1),
                           True, f"{tasks_file}: {where}", above=entry.get("above_application", False) is True))
    default = manifest.get("application_default") or {}

    ecu = _load_yaml(rte_file)
    components: dict[str, ExpRunnable] = {}
    order: list[str] = []
    for i, comp in enumerate(ecu.get("components") or []):
        name, path = comp.get("name"), comp.get("path")
        if not name or not path:
            raise TimingConfigError(f"{rte_file}: components[{i}]: name and path are needed")
        for r in load_any_component(str(name), _component_dir(ecu, rte_file, repo, str(path))):
            components[r.key] = r
            order.append(r.key)
    periodic = [k for k in order if components[k].kind == "periodic"]

    given = ecu.get("tasks")
    app: list[ExpTask] = []
    if given is None:
        periods = [components[k].period_ms for k in periodic]
        base = ecu.get("base_period_ms")
        period = _int(base, rte_file, "base_period_ms", 1) if base is not None else (
            _gcd_all(periods) if periods else 10)
        given = [{"name": default.get("name"), "period_ms": period, "priority": default.get("priority")}]
        where_prefix = f"{tasks_file}: application_default"
    else:
        where_prefix = None
    for i, entry in enumerate(given):
        where = where_prefix or f"{rte_file}: tasks[{i}]"
        task = ExpTask(str(entry.get("name")), _int(entry.get("priority"), rte_file, f"tasks[{i}].priority", 1),
                       False, where, _int(entry.get("period_ms"), rte_file, f"tasks[{i}].period_ms", 1), i)
        if (task.period_ms * 1000) % tick_us:
            raise TimingConfigError(f"{where}.period_ms: {task.period_ms} ms is no multiple of the OS tick "
                                    f"{tick_us} us ({tasks_file})")
        app.append(task)

    names: dict[str, ExpTask] = {}
    keys: dict[int, ExpTask] = {}
    for task in bsw + app:
        if task.name in names:
            raise TimingConfigError(f"{task.where}.name: {task.name} is also {names[task.name].where}")
        if task.key in keys:
            raise TimingConfigError(f"{task.where}.priority: key {task.key} is also used by {keys[task.key].name} "
                                    f"({keys[task.key].where}); every task needs its own FreeRTOS priority")
        names[task.name], keys[task.key] = task, task
    for task in app:
        for b in bsw:
            if b.above and task.key > b.key:
                raise TimingConfigError(f"{task.where}.priority: {task.key} is above BSW task {b.name} ({b.key}), "
                                        f"which must stay the highest")
    ordered = sorted(bsw + app, key=lambda t: t.key)
    if len(ordered) + 2 > max_priorities:
        raise TimingConfigError(f"{tasks_file}: max_priorities {max_priorities}: {len(ordered)} tasks plus idle and "
                                f"timer do not fit")
    for number, task in enumerate(ordered, start=1):
        task.priority = number

    assigned: dict[str, str] = {}
    listed = any(isinstance(e, dict) and "runnables" in e for e in given)
    for task, entry in zip(app, given):
        for j, item in enumerate(entry.get("runnables") or []):
            where = f"{rte_file}: tasks[{task.index}].runnables[{j}]"
            key, offset = _runnable_item(item, rte_file, f"tasks[{task.index}].runnables[{j}]")
            if key not in components:
                raise TimingConfigError(f"{where}: unknown runnable {key}")
            if key in assigned:
                raise TimingConfigError(f"{where}: {key} is already in task {assigned[key]}")
            _assign(task, components[key], offset, where)
            assigned[key] = task.name
    if not listed and len(app) == 1:
        for key in order:
            if components[key].kind in ("periodic", "mode"):
                _assign(app[0], components[key], 0, f"{app[0].where}")
                assigned[key] = app[0].name
    missing = [k for k in order if components[k].kind in ("periodic", "mode") and k not in assigned]
    if missing:
        raise TimingConfigError(f"{rte_file}: tasks: runnables {', '.join(missing)} are in no task")
    init = [components[k] for k in order if components[k].kind == "init"]
    return TimingExpectation(None, Path(rte_file), Path(tasks_file), tick_us, ordered, app, init)


def _gcd_all(values: list[int]) -> int:
    result = 0
    for v in values:
        result = gcd(result, v)
    return result


def _assign(task: ExpTask, r: ExpRunnable, offset: int, where: str) -> None:
    if r.kind == "init":
        raise TimingConfigError(f"{where}: {r.key} is an init runnable and belongs to no task")
    r.task = task.name
    if r.kind == "mode":
        if offset:
            raise TimingConfigError(f"{where}.offset_ms: {r.key} is started by a mode switch and has no offset")
        task.modes.append(r)
        return
    if r.period_ms % task.period_ms:
        raise TimingConfigError(f"{where}: period {r.period_ms} ms of {r.key} is no multiple of the period "
                                f"{task.period_ms} ms of task {task.name}")
    if offset % task.period_ms or offset >= r.period_ms:
        raise TimingConfigError(f"{where}.offset_ms: {offset} ms is no multiple of {task.period_ms} ms or not below "
                                f"the period {r.period_ms} ms")
    r.offset_ms = offset
    task.runnables.append(r)


def load_interrupts(exp: TimingExpectation, project: dict, project_file: Path, derivative_file: Path) -> None:
    """Interrupt ranks of the project and the NVIC facts of the derivative (Cortex-M); a VIM core has no levels."""
    deriv = (_load_yaml(derivative_file).get("derivative") or {})
    exp.core = str(deriv.get("core"))
    interrupts = project.get("interrupts") or {}
    if exp.core not in NVIC_CORES:
        return                                  # Cortex-R/VIM: channels, not levels (limit of this check)
    exp.nvic_bits = _int(deriv.get("nvic_priority_bits"), derivative_file, "derivative.nvic_priority_bits", 2)
    level = interrupts.get("max_syscall_level")
    exp.max_syscall = MAX_SYSCALL_DEFAULT[exp.core] if level is None else _int(
        level, project_file, "interrupts.max_syscall_level", 0)
    levels = 1 << exp.nvic_bits
    if exp.max_syscall >= levels:
        raise TimingConfigError(f"{project_file}: interrupts.max_syscall_level: {exp.max_syscall} is no level of "
                                f"{exp.nvic_bits} priority bits")
    for i, r in enumerate(interrupts.get("ranks") or []):
        where = f"interrupts.ranks[{i}]"
        rank = _int(r.get("rank"), project_file, f"{where}.rank", 0)
        if exp.max_syscall + rank >= levels:
            raise TimingConfigError(f"{project_file}: {where}.rank: {rank} gives level {exp.max_syscall + rank}, "
                                    f"beyond the {levels} levels of {exp.nvic_bits} priority bits")
        source, name = str(r.get("source")), str(r.get("name"))
        ident = f"{source.upper()}_{re.sub(r'[^A-Z0-9_]', '_', name.upper())}"
        exp.irqs.append(ExpIrq(source, name, rank, exp.max_syscall + rank, ident))


def load_expectation(project: Path, tasks_file: Path = TASKS_FILE, rte_file: Path | None = None,
                     derivative_file: Path | None = None, repo: Path = REPO) -> TimingExpectation:
    """Expectation of a project: its ECU description (or `rte_file`), the BSW task profile and its interrupt ranks."""
    project = Path(project)
    proj = _load_yaml(project)
    p = proj.get("project") or {}
    if rte_file is None:
        if not p.get("rte"):
            raise TimingConfigError(f"{project}: project.rte (ECU description) is not set")
        rte_file = (project.parent / str(p["rte"])).resolve()
    exp = load_tasks(Path(rte_file), tasks_file, repo)
    exp.project_file = project
    derivative_file = derivative_file or _derivative_of(project, p)
    if derivative_file is not None:
        load_interrupts(exp, proj, project, Path(derivative_file))
    return exp


def interrupt_expectation(project: Path, derivative_file: Path | None = None) -> TimingExpectation:
    """Only the interrupt ranks of a project (for a project without ECU description, or the probe alone)."""
    project = Path(project)
    proj = _load_yaml(project)
    exp = TimingExpectation(project, None, TASKS_FILE, 0, [], [], [])
    derivative_file = derivative_file or _derivative_of(project, proj.get("project") or {})
    if derivative_file is None:
        raise TimingConfigError(f"{project}: project.board: no board with a derivative (board.mcu)")
    load_interrupts(exp, proj, project, Path(derivative_file))
    return exp


def _derivative_of(project: Path, p: dict) -> Path | None:
    if not p.get("board"):
        return None
    board = (_load_yaml((project.parent / str(p["board"]) / "board.yaml").resolve()).get("board") or {})
    return check.find_derivative_file(str(board["mcu"])) if board.get("mcu") else None


# ---------------------------------------------------------------------- the image

def struct_layout(fields: tuple[tuple[str, str], ...]) -> tuple[dict[str, tuple[int, str]], int]:
    """Offsets of a C struct with natural alignment (AAPCS, 32 bit): name -> (offset, kind), and the size."""
    sizes = {"p": POINTER_SIZE, "z": POINTER_SIZE, "u8": 1, "u32": 4}
    offset, align, result = 0, 1, {}
    for name, kind in fields:
        size = sizes[kind]
        offset = (offset + size - 1) // size * size
        result[name] = (offset, kind)
        offset += size
        align = max(align, size)
    return result, (offset + align - 1) // align * align


def read_table(img: elffile.ElfImage, symbol: str, fields, out: check.Findings, what: str) -> list[dict] | None:
    sym = img.symbol(symbol)
    if sym is None:
        return None
    layout, size = struct_layout(fields)
    if sym.size % size:
        out.error("rte-table-size", img.path, f"symbol {what}",
                  f"{sym.size} bytes are no multiple of the {size} byte entry of the table")
        return []
    raw = img.read(sym.value, sym.size)
    if raw is None:
        out.error("rte-table-content", img.path, f"symbol {what}", "the table has no content in the image")
        return []
    rows = []
    for i in range(sym.size // size):
        row = {}
        for name, (off, kind) in layout.items():
            fmt = {"p": "<I", "z": "<I", "u8": "<B", "u32": "<I"}[kind]
            row[name] = struct.unpack_from(fmt, raw, i * size + off)[0]
        rows.append(row)
    return rows


def c_string(img: elffile.ElfImage, address: int, limit: int = 256) -> str | None:
    for sec in img.sections:
        if sec.has_content and sec.addr <= address < sec.addr + sec.size:
            start = sec.offset + address - sec.addr
            end = img.data.find(b"\0", start, min(start + limit, sec.offset + sec.size))
            return None if end < 0 else img.data[start:end].decode("utf-8", "replace")
    return None


def function_names(img: elffile.ElfImage) -> dict[int, list[str]]:
    result: dict[int, list[str]] = {}
    for s in img.symbols:
        if s.type == 2 and s.shndx != 0:                # STT_FUNC
            result.setdefault(s.value & ~1, []).append(s.name)
    return result


def _function_matches(names: list[str], r: ExpRunnable) -> bool:
    component, runnable = r.key.split(".", 1)
    wrapper = f"Rte_Implicit_{component}_{runnable}"    # RTE-09: runnables with implicit access
    return any(n == r.symbol or wrapper in n for n in names)


@dataclass
class ImageTiming:
    tasks: list[dict] | None
    runnables: list[dict] | None
    modes: list[dict] | None
    contexts: list[dict[str, int]]      # TASK_* enumerators per compilation unit that has them
    macros: dict[str, set[str]]


def read_image(img: elffile.ElfImage, out: check.Findings) -> ImageTiming:
    tasks = read_table(img, TASKS_SYMBOL, TASK_FIELDS, out, "rte::TASKS")
    runnables = read_table(img, RUNNABLES_SYMBOL, RUNNABLE_FIELDS, out, "rte::RUNNABLES")
    modes = read_table(img, MODE_RUNNABLES_SYMBOL, MODE_RUNNABLE_FIELDS, out, "rte::MODE_RUNNABLES")
    funcs = function_names(img)
    for rows in (tasks, runnables, modes):
        for row in rows or []:
            row["name"] = c_string(img, row["name"])
            if "function" in row:
                row["functions"] = funcs.get(row["function"] & ~1, [])
    contexts = [dict(e.values) for e in dwarf.enumerations(img) if TASK_COUNT_ENUMERATOR in e.values]
    return ImageTiming(tasks, runnables, modes, contexts, dwarf.macros(img))


def _macro_int(texts: set[str]) -> int | None:
    if len(texts) != 1:
        return None
    m = re.fullmatch(r"\(?\s*(\d+)[uUlL]*\s*\)?", next(iter(texts)))
    return int(m.group(1)) if m else None


def check_tables(exp: TimingExpectation, image: ImageTiming, file, out: check.Findings) -> None:
    """rte::TASKS, rte::RUNNABLES and rte::MODE_RUNNABLES against the expected tasks and runnables (SM-05)."""
    if image.tasks is None or image.runnables is None:
        out.error("rte-missing", file, "symbol rte::TASKS/rte::RUNNABLES",
                  "the image has no RTE tables, but the project has an ECU description")
        return
    if len(image.tasks) != len(exp.app):
        out.error("task-count", file, "symbol rte::TASKS",
                  f"{len(image.tasks)} application tasks, the configuration has {len(exp.app)} "
                  f"({', '.join(t.name for t in exp.app)})")
    first = 0
    for task, row in zip(exp.app, image.tasks):
        where = f"rte::TASKS[{task.index}] ({task.name})"
        if row["name"] != task.name:
            out.error("task-name", file, where, f"name '{row['name']}', expected '{task.name}' ({task.where})")
        if row["context"] != task.priority:
            out.error("task-priority", file, where, f"context (FreeRTOS priority) {row['context']}, expected "
                      f"{task.priority} from key {task.key} among {_order(exp)}")
        if row["periodMs"] != task.period_ms:
            out.error("task-period", file, where, f"period {row['periodMs']} ms (deadline of a cycle), expected "
                      f"{task.period_ms} ms ({task.where}.period_ms)")
        if (row["firstRunnable"], row["runnableCount"]) != (first, len(task.runnables)):
            out.error("task-runnables", file, where, f"runnables [{row['firstRunnable']}, +{row['runnableCount']}), "
                      f"expected [{first}, +{len(task.runnables)})")
        if row["hyperTicks"] != task.hyper_ticks:
            out.error("task-hyper", file, where, f"cycle counter wraps at {row['hyperTicks']}, expected "
                      f"{task.hyper_ticks} (least common multiple of the runnable periods in cycles)")
        first += len(task.runnables)

    expected = exp.runnables
    if len(image.runnables) != len(expected):
        out.error("runnable-count", file, "symbol rte::RUNNABLES",
                  f"{len(image.runnables)} rows, expected {len(expected)} ({', '.join(r.key for r in expected)})")
    index = {t.name: t.index for t in exp.app}
    periods = {t.name: t.period_ms for t in exp.app}
    for i, (r, row) in enumerate(zip(expected, image.runnables)):
        where = f"rte::RUNNABLES[{i}] ({r.key})"
        if row["name"] != r.key:
            out.error("runnable-name", file, where, f"name '{row['name']}', expected '{r.key}' (call order of "
                      f"{exp.rte_file}, then the init runnables)")
        if not _function_matches(row["functions"], r):
            out.error("runnable-function", file, where, f"calls {', '.join(row['functions']) or hex(row['function'])},"
                      f" expected {r.symbol} (ARXML SYMBOL)")
        if r.kind == "init":
            want = (0, 0, NO_TASK, 0, 0)
        else:
            tp = periods[r.task]
            want = (r.period_ms, r.offset_ms, index[r.task], r.period_ms // tp, r.offset_ms // tp)
        have = (row["periodMs"], row["offsetMs"], row["task"], row["periodTicks"], row["offsetTicks"])
        if have[0] != want[0]:
            out.error("runnable-period", file, where, f"period {have[0]} ms (SM-05 deadline), expected {want[0]} ms"
                      + ("" if r.kind == "init" else " (ARXML TIMING-EVENT)"))
        if have[1] != want[1]:
            out.error("runnable-offset", file, where, f"offset {have[1]} ms, expected {want[1]} ms")
        if have[2] != want[2]:
            out.error("runnable-task", file, where, f"task index {have[2]}, expected {want[2]}"
                      + (f" ({r.task})" if r.task else " (init runnable: no task)"))
        if have[3:] != want[3:]:
            out.error("runnable-ticks", file, where, f"period/offset {have[3]}/{have[4]} task cycles, expected "
                      f"{want[3]}/{want[4]}")

    modes = [(r.key, index[r.task]) for t in exp.app for r in t.modes]
    have_modes = sorted({(row["name"], row["task"]) for row in image.modes or []})
    if sorted(set(modes)) != have_modes:
        out.error("mode-runnables", file, "symbol rte::MODE_RUNNABLES",
                  f"mode switch runnables {have_modes}, expected {sorted(set(modes))}")


def _order(exp: TimingExpectation) -> str:
    return ", ".join(f"{t.name} {t.key}" for t in exp.tasks)


def check_contexts(exp: TimingExpectation, image: ImageTiming, file, out: check.Findings) -> None:
    """Every task's context number (TASK_<NAME> of async/Config.h, as compiled) against the key order."""
    if not image.contexts:
        out.warning("task-context-unknown", file, "debug information",
                    "no enumerators of async/Config.h (build without debugging information): the priorities of the "
                    "BSW tasks are not checked, only those of the application tasks in rte::TASKS")
        return
    first = image.contexts[0]
    for other in image.contexts[1:]:
        if other != first:
            out.error("task-context-units", file, "debug information",
                      f"the compilation units disagree on async/Config.h: {sorted(first.items())} and "
                      f"{sorted(other.items())} (object compiled with another configuration)")
            break
    names = {f"TASK_{t.name.upper()}": t for t in exp.tasks}
    have = {k: v for k, v in first.items() if k.startswith("TASK_")}
    for name in sorted(set(have) - set(names)):
        out.error("task-list", file, f"enumerator {name}", f"task in the image, not in the configuration "
                  f"({exp.tasks_file}, {exp.rte_file})")
    for name, task in names.items():
        if name not in have:
            out.error("task-list", file, f"enumerator {name}", f"task {task.name} of the configuration "
                      f"({task.where}) is not in the image")
        elif have[name] != task.priority:
            out.error("task-priority", file, f"enumerator {name}", f"FreeRTOS priority {have[name]}, expected "
                      f"{task.priority} from key {task.key} among {_order(exp)}")
    if first.get(TASK_COUNT_ENUMERATOR) != exp.task_count:
        out.error("task-count", file, f"enumerator {TASK_COUNT_ENUMERATOR}",
                  f"{first.get(TASK_COUNT_ENUMERATOR)}, expected {exp.task_count} (idle, {len(exp.tasks)} tasks; "
                  f"the timer task gets this number)")


def check_macros(exp: TimingExpectation, image: ImageTiming, file, out: check.Findings,
                 interrupts: bool = True) -> None:
    """OS tick, interrupt ranks, syscall level and NVIC bits as the image was compiled (.debug_macro).

    `interrupts=False` for an image that does not contain the board port (host build of the RTE tables alone).
    """
    m = image.macros
    if not m:
        out.warning("macros-unknown", file, "debug information", "no .debug_macro (build without -g3): tick, "
                    "interrupt ranks and NVIC bits of the image are not checked")
        return
    tick = _macro_int(m.get("ASYNC_CONFIG_TICK_IN_US", set()))
    if exp.rte_file is not None and tick != exp.tick_us:
        out.error("os-tick", file, "macro ASYNC_CONFIG_TICK_IN_US",
                  f"{sorted(m.get('ASYNC_CONFIG_TICK_IN_US', set())) or 'missing'}, expected {exp.tick_us} us "
                  f"({exp.tasks_file} tick_us)")
    if exp.nvic_bits is None or not interrupts:
        return
    for macro, want, source in (("NX_NVIC_PRIORITY_BITS", exp.nvic_bits, "mcudb nvic_priority_bits"),
                                ("NX_IRQ_MAX_SYSCALL_LEVEL", exp.max_syscall, "interrupts.max_syscall_level")):
        have = _macro_int(m.get(macro, set()))
        if have != want:
            out.error("irq-" + ("bits" if "BITS" in macro else "syscall"), file, f"macro {macro}",
                      f"{sorted(m.get(macro, set())) or 'missing'}, expected {want} ({source})")
    ranks = {k[len("NX_IRQ_RANK_"):]: v for k, v in m.items() if k.startswith("NX_IRQ_RANK_")}
    for irq in exp.irqs:
        have = _macro_int(ranks.pop(irq.ident, set()))
        if have != irq.rank:
            out.error("irq-rank", file, f"macro NX_IRQ_RANK_{irq.ident}",
                      f"{'missing' if have is None else have}, expected rank {irq.rank} of {irq.source} {irq.name} "
                      f"(project interrupts.ranks)")
    for ident in sorted(ranks):
        out.error("irq-rank", file, f"macro NX_IRQ_RANK_{ident}", "rank in the image, not in the project")


def check_image(exp: TimingExpectation, app: Path, out: check.Findings | None = None,
                interrupts: bool = True) -> check.Findings:
    """The RTE tables, the task contexts and the compiled macros of one linked image."""
    out = out if out is not None else check.Findings()
    try:
        img = elffile.load(app)
    except elffile.ElfError as e:
        out.error("image-unreadable", app, "file", str(e))
        return out
    try:
        image = read_image(img, out)
    except dwarf.DwarfError as e:
        out.error("debug-unreadable", app, "debug information", str(e))
        return out
    if exp.rte_file is not None:                # a project without ECU description: interrupts only
        check_tables(exp, image, app, out)
        check_contexts(exp, image, app, out)
    check_macros(exp, image, app, out, interrupts)
    return out


# ---------------------------------------------------------------------- NVIC levels through the target compiler

PROBE_PREFIX = "nx_probe_"


def arm_gcc() -> str | None:
    path = os.pathsep.join(p for p in (os.environ.get("NX_ARM_GCC_BIN"), os.environ.get("PATH")) if p)
    return shutil.which("arm-none-eabi-gcc", path=path)


def check_irq_probe(exp: TimingExpectation, generated: Path, out: check.Findings | None = None,
                    gcc: str | None = None) -> check.Findings:
    """NVIC level of every rank as the target compiler evaluates the generated headers.

    `generated` is the folder holding `interrupts/interruptPriorities.h` and `mcu/derivativeFacts.h` (bswgen output,
    `<build>/generated/board`). The probe defines one constant per expected interrupt and the syscall level from the
    macros NX_IRQ_LEVEL_<SOURCE>_<NAME>, is compiled for the derivative's core and linked, and the own ELF reader
    reads the values back.
    """
    out = out if out is not None else check.Findings()
    header = Path(generated) / "interrupts" / "interruptPriorities.h"
    if exp.nvic_bits is None:
        return out
    if not header.is_file():
        out.error("irq-header-missing", header, "file", "generated interrupt priorities not found")
        return out
    gcc = gcc or arm_gcc()
    if gcc is None:
        out.warning("irq-probe-skipped", header, "file", "arm-none-eabi-gcc not found (PATH, NX_ARM_GCC_BIN)")
        return out
    defined = set(re.findall(r"^\s*#\s*define\s+NX_IRQ_LEVEL_(\w+)", header.read_text(encoding="utf-8"), re.M))
    for ident in sorted(defined - {i.ident for i in exp.irqs}):
        out.error("irq-level-extra", header, f"macro NX_IRQ_LEVEL_{ident}", "interrupt level not in the project")
    lines = ['#include "interrupts/interruptPriorities.h"',
             f"const unsigned {PROBE_PREFIX}max_syscall = NX_IRQ_MAX_SYSCALL_LEVEL;",
             f"const unsigned {PROBE_PREFIX}bits = NX_NVIC_PRIORITY_BITS;"]
    for irq in exp.irqs:
        if irq.ident not in defined:
            out.error("irq-level-missing", header, f"macro NX_IRQ_LEVEL_{irq.ident}",
                      f"no level for {irq.source} {irq.name} of the project")
            continue
        lines.append(f"const unsigned {PROBE_PREFIX}level_{irq.ident} = NX_IRQ_LEVEL_{irq.ident};")
    lines.append("void _start(void) { for (;;) { } }")
    with tempfile.TemporaryDirectory(prefix="nx_irq_probe_") as tmp:
        src, elf = Path(tmp) / "probe.c", Path(tmp) / "probe.elf"
        src.write_text("\n".join(lines) + "\n", encoding="utf-8")
        cmd = [gcc, f"-mcpu={MCPU[exp.core]}", "-mthumb", "-std=c99", "-O0", "-nostdlib", "-nostartfiles",
               "-Wl,--no-gc-sections", "-Wl,-e,_start", f"-I{generated}", f"-I{NX_INTERRUPT_INCLUDE}",
               str(src), "-o", str(elf)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            out.error("irq-probe-build", header, "file", "the generated interrupt priorities do not compile for "
                      f"{exp.core}: {r.stderr.strip()[-600:]}")
            return out
        img = elffile.load(elf)
        values = {}
        for sym in img.symbols:
            if sym.name.startswith(PROBE_PREFIX) and sym.size == 4:
                values[sym.name[len(PROBE_PREFIX):]] = struct.unpack("<I", img.read(sym.value, 4))[0]
    for key, want, source in (("bits", exp.nvic_bits, "mcudb nvic_priority_bits"),
                              ("max_syscall", exp.max_syscall, "interrupts.max_syscall_level")):
        if values.get(key) != want:
            out.error("irq-" + ("bits" if key == "bits" else "syscall"), header, key,
                      f"{values.get(key)}, expected {want} ({source})")
    for irq in exp.irqs:
        key = f"level_{irq.ident}"
        if key in values and values[key] != irq.level:
            out.error("irq-level", header, f"macro NX_IRQ_LEVEL_{irq.ident}",
                      f"NVIC level {values[key]}, expected {irq.level} = max_syscall_level {exp.max_syscall} + "
                      f"rank {irq.rank} of {irq.source} {irq.name}")
    return out


def check_build(exp: TimingExpectation, build: Path, out: check.Findings | None = None) -> check.Findings:
    """The application of a CMake build directory and its generated interrupt priorities."""
    out = out if out is not None else check.Findings()
    images = check.find_build_images(Path(build))
    if "app" not in images:
        out.error("image-missing", build, "build", "no app.ecu.elf in the build directory")
    else:
        check_image(exp, images["app"], out)
    check_irq_probe(exp, Path(build) / "generated" / "board", out)
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(prog="elfcheck timing", description="RTE timing and interrupt priorities of a "
                                     "linked image against the configuration (tool-classification.md proposal F)")
    parser.add_argument("--project", type=Path, required=True, help="project.yaml")
    parser.add_argument("--build", type=Path, help="CMake build directory (app.ecu.elf, generated/board)")
    parser.add_argument("--app", type=Path, help="application ELF")
    parser.add_argument("--generated", type=Path, help="folder with interrupts/interruptPriorities.h (bswgen output)")
    parser.add_argument("--rte", type=Path, help="ECU description (default: project.rte)")
    parser.add_argument("--tasks", type=Path, default=TASKS_FILE, help="BSW task profile (default: %(default)s)")
    parser.add_argument("--strict", action="store_true", help="warnings count as errors")
    args = parser.parse_args(argv)
    try:
        has_rte = args.rte is not None or bool((_load_yaml(args.project).get("project") or {}).get("rte"))
        exp = load_expectation(args.project, args.tasks, args.rte) if has_rte else interrupt_expectation(args.project)
    except (TimingConfigError, OSError, ValueError) as e:
        print(f"elfcheck timing: {e}", file=sys.stderr)
        return 2
    out = check.Findings()
    if args.build:
        check_build(exp, args.build, out)
    if args.app:
        check_image(exp, args.app, out)
    if args.generated:
        check_irq_probe(exp, args.generated, out)
    if not (args.build or args.app or args.generated):
        print("elfcheck timing: nothing to check (--build, --app, --generated)", file=sys.stderr)
        return 2
    print(f"elfcheck timing: {exp.rte_file or 'no ECU description'} with {exp.tasks_file}: "
          f"{', '.join(f'{t.name}={t.priority}' for t in exp.tasks)}"
          + (f"; NVIC {exp.nvic_bits} bits, syscall level {exp.max_syscall}, "
             f"{', '.join(f'{i.ident}={i.level}' for i in exp.irqs)}" if exp.nvic_bits is not None else ""))
    for finding in out:
        print(finding)
    errors = len(out.errors) + (len(out) - len(out.errors) if args.strict else 0)
    print(f"elfcheck timing: {len(out.errors)} errors, {len(out) - len(out.errors)} warnings")
    return 1 if errors else 0
