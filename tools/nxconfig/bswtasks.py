"""Stacks and merges of the BSW tasks as project values (PLAT-26).

The BSW tasks (ecu/asyncCoreConfiguration/tasks.yaml) and the OpenBSW idle and timer task are objects of
ecu/application/src/app/app.cpp. Their stacks were fixed there (6 x 2 144 B incl. task control block, safety 2 048 B,
timer 1 120 B - finding PLAT-12, the largest RAM item for derivatives with ~15 KB SRAM). Now:

- defaults, floors, granularity and the allowed merges per task: ecu/configuration/taskStacks.yaml (the task
  objects' own manifest; a project that decides nothing gets exactly these values - same image as before)
- project values: project.yaml bsw_tasks.<task> (stack_bytes, or run_in: the task has no FreeRTOS task of its own
  and runs in the named task)
- checks (with file, line and YAML path): known task, floor, granularity, merge allowed by the manifest and never
  for or into a task with above_application (safety), no chain of merges; a warning below the default (the value has
  to be confirmed at the board or by tools/stackuse) and for every merge (its consequence from the manifest); the sum
  of all task stacks plus interrupt stack, no-init area and RAM functions against the application RAM
- tools/rtegen takes the plan (resolve()) and generates tasks/BswTasks.h and the context aliases in async/Config.h

PLAT-107 (decisions of the user in the safety review 2026-10-01 on PLAT-26):
- S1: the floor (min_stack_bytes) is coupled to tools/stackuse - a profile context with `bsw_task` is measured
  against the stack of the build and its worst case must fit below the floor (tools/stackuse/cli.py); the demo
  profile does this for the safety task
- S2: a project with a power stage or a gate driver (a used PWM group - complementary half bridges -, or a used
  device whose module provides the role gate_driver) may only merge into the targets of `run_in_power_stage` of the
  manifest: background -> bsp is refused there until it is measured at the board (NvM writes would delay the
  deadman and the output monitoring of bsp)
- S3: a stack below the default needs a proof note (bsw_tasks.<task>.evidence: tools/stackuse or high-water mark);
  without one it is an error in an ASIL project (project.integrity not `qm`, left out = ASIL) and a warning in a QM
  project. The stack must hold the used bytes of its proof.

Family neutral, no Qt.
"""

from dataclasses import dataclass, field
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from .config import REPO, Config, Reporter

TASK_STACKS_NAMES = ("ecu", "configuration", "taskStacks.yaml")
TASKS_MANIFEST_NAMES = ("ecu", "asyncCoreConfiguration", "tasks.yaml")   # same file as checks.TASKS_MANIFEST_NAMES
OS_TASKS = ("idle", "timer")            # OpenBSW tasks every image has, not in the BSW task manifest
DEFAULT_MULTIPLE = 8                    # AAPCS: 8-byte stack alignment
SECTION = "bsw_tasks"
# Defaults of the application linker template (platforms/<family>/port/main/linkerscript) for the interrupt stack and
# its guard (SYS-97), for the RAM sum only; a project value of SYS-132 (memory.application.isr_stack_size,
# isr_stack_guard_size) replaces them as soon as the schema has it.
DEFAULT_ISR_STACK_SIZE = 0x1000
DEFAULT_ISR_STACK_GUARD_SIZE = 0x400


class StackManifestError(Exception):
    """ecu/configuration/taskStacks.yaml is missing or broken (a fault of the tree, not of the project)."""


def task_stacks_manifest(tree: Path | None = None) -> Path:
    """The stack manifest of the task objects of a tree (TOOL-85: of the tree the tools run as)."""
    return (REPO if tree is None else Path(tree)).joinpath(*TASK_STACKS_NAMES)


@dataclass(frozen=True)
class StackSpec:
    stack_bytes: int
    min_stack_bytes: int
    multiple_of: int = DEFAULT_MULTIPLE
    run_in: tuple[str, ...] = ()
    note: str = ""
    # PLAT-107 (S2): the merges allowed in a project with a power stage or gate driver; None: the same as run_in
    run_in_power_stage: tuple[str, ...] | None = None

    def merges(self, power_stage: bool) -> tuple[str, ...]:
        return self.run_in if not power_stage or self.run_in_power_stage is None else self.run_in_power_stage


@dataclass(frozen=True)
class StackManifest:
    path: Path
    default: StackSpec
    tasks: dict[str, StackSpec]

    def spec(self, name: str) -> StackSpec:
        return self.tasks.get(name, self.default)


def _spec(entry, where: str) -> StackSpec:
    if not isinstance(entry, dict):
        raise StackManifestError(f"{where}: expected a mapping")
    unknown = sorted(set(entry) - {"stack_bytes", "min_stack_bytes", "multiple_of", "run_in", "note",
                                   "run_in_power_stage"})
    if unknown:
        raise StackManifestError(f"{where}.{unknown[0]}: unknown key")
    values = {}
    for key in ("stack_bytes", "min_stack_bytes", "multiple_of"):
        value = entry.get(key, DEFAULT_MULTIPLE if key == "multiple_of" else None)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise StackManifestError(f"{where}.{key}: expected a positive integer, got {value!r}")
        values[key] = value
    run_in = entry.get("run_in", [])
    if not isinstance(run_in, list) or not all(isinstance(t, str) for t in run_in):
        raise StackManifestError(f"{where}.run_in: expected a list of task names")
    power_stage = entry.get("run_in_power_stage")
    if power_stage is not None:
        if not isinstance(power_stage, list) or not all(isinstance(t, str) for t in power_stage):
            raise StackManifestError(f"{where}.run_in_power_stage: expected a list of task names")
        if not set(power_stage) <= set(run_in):
            raise StackManifestError(f"{where}.run_in_power_stage: {sorted(set(power_stage) - set(run_in))[0]} is "
                                     f"not in run_in (only a narrower set of the allowed merges)")
        power_stage = tuple(power_stage)
    spec = StackSpec(values["stack_bytes"], values["min_stack_bytes"], values["multiple_of"], tuple(run_in),
                     str(entry.get("note", "")), power_stage)
    if spec.min_stack_bytes > spec.stack_bytes or spec.stack_bytes % spec.multiple_of \
            or spec.min_stack_bytes % spec.multiple_of:
        raise StackManifestError(f"{where}: stack_bytes {spec.stack_bytes} and min_stack_bytes "
                                 f"{spec.min_stack_bytes} must be multiples of {spec.multiple_of}, the floor not "
                                 f"above the default")
    return spec


def load_stack_manifest(path: Path) -> StackManifest:
    source = Path(path).as_posix()
    try:
        data = YAML(typ="safe").load(Path(path).read_text(encoding="utf-8"))
    except (OSError, YAMLError) as e:
        raise StackManifestError(f"{source}: cannot be read ({e})") from None
    if not isinstance(data, dict) or not isinstance(data.get("tasks"), dict):
        raise StackManifestError(f"{source}: expected a mapping with default and tasks")
    default = _spec(data.get("default"), f"{source}: default")
    tasks = {name: _spec(entry, f"{source}: tasks.{name}") for name, entry in data["tasks"].items()}
    for os_task in OS_TASKS:
        if os_task not in tasks:
            raise StackManifestError(f"{source}: tasks.{os_task}: missing (OpenBSW task of every image)")
    return StackManifest(Path(path), default, tasks)


@dataclass(frozen=True)
class BswTask:
    """One task object of app.cpp: its stack, or the task it runs in (run_in, then stack_bytes is 0)."""

    name: str
    stack_bytes: int
    default_bytes: int
    run_in: str | None = None
    os_task: bool = False               # idle or timer: no context of the BSW task manifest

    @property
    def own(self) -> bool:
        return self.run_in is None


@dataclass(frozen=True)
class BswTaskPlan:
    tasks: tuple[BswTask, ...]          # idle, timer, then the BSW tasks in manifest order
    manifest: Path = field(compare=False, default=None)

    def task(self, name: str) -> BswTask:
        return next(t for t in self.tasks if t.name == name)

    @property
    def stack_bytes(self) -> int:
        """Sum of the stacks of all own task objects (BSW, idle, timer)."""
        return sum(t.stack_bytes for t in self.tasks)


def resolve(bsw: list[tuple[str, bool]], stacks: StackManifest, values: dict, error, warning, *,
            asil: bool = False, power_stage: str | None = None) -> BswTaskPlan:
    """The plan of the task objects from the BSW tasks of the task manifest ((name, above_application) in manifest
    order), the stack manifest and the project values (project.yaml bsw_tasks, name -> BswTaskUse).

    `error(path, text)` / `warning(path, text)` get the YAML path below project.yaml (e.g. ("bsw_tasks", "can",
    "stack_bytes")). Every error is reported; a task with an error keeps its default in the returned plan.

    PLAT-107: `asil` - a stack below the default without proof note (evidence) is an error instead of a warning;
    `power_stage` - what makes the project one with a power stage or gate driver (text for the message): only the
    merges of run_in_power_stage are allowed. The defaults keep the PLAT-26 behaviour (tools/rtegen).
    """
    names = [name for name, _ in bsw]
    above = {name for name, a in bsw if a}
    known = list(OS_TASKS) + names
    for name in values:
        if name not in known:
            error((SECTION, name), f"{name} is no BSW task (known: {', '.join(known)}; the application tasks take "
                                   f"their stack from the ECU description, rte.yaml tasks[].stack_bytes)")
    merged = {name: use.run_in for name, use in values.items() if name in names and use.run_in is not None}
    tasks = []
    for name in known:
        spec = stacks.spec(name)
        use = values.get(name)
        run_in = None
        stack = spec.stack_bytes
        if use is not None and use.run_in is not None:
            where = (SECTION, name, "run_in")
            target = use.run_in
            if name in OS_TASKS or name in above:
                error(where, f"task {name} cannot run in another task"
                             f"{' (above_application: it keeps its own task and priority)' if name in above else ' (OpenBSW task)'}")
            elif target not in names:
                error(where, f"{target} is no BSW task (known: {', '.join(names)})")
            elif target in above:
                error(where, f"no task may run in {target}: it keeps its own task and the highest priority "
                             f"(above_application)")
            elif target not in spec.run_in:
                allowed = ", ".join(spec.run_in) if spec.run_in else "none"
                error(where, f"task {name} may not run in {target} (allowed by {stacks.path.name}: {allowed})")
            elif power_stage is not None and target not in spec.merges(True):
                allowed = ", ".join(spec.merges(True)) or "none"
                error(where, f"task {name} may not run in {target} in a project with a power stage or gate driver "
                             f"({power_stage}): {spec.note or 'its work would delay the modules of ' + target}; "
                             f"refused until measured at the board (safety review 2026-10-01, PLAT-26/S2; allowed "
                             f"by {stacks.path.name} run_in_power_stage: {allowed})")
            elif target in merged:
                error(where, f"{target} runs in {merged[target]} itself (bsw_tasks.{target}.run_in); name that task")
            else:
                run_in, stack = target, 0
                consequence = f": {spec.note}" if spec.note else ""
                warning(where, f"task {name} has no task of its own and runs in {target}{consequence}; the stack "
                               f"of {target} has to hold the deepest path of both (confirm at the board, console "
                               f"'stats', or with tools/stackuse)")
        elif use is not None and use.stack_bytes is not None:
            where = (SECTION, name, "stack_bytes")
            value = use.stack_bytes
            if value < spec.min_stack_bytes:
                error(where, f"stack of task {name} {value} B is below the minimum {spec.min_stack_bytes} B "
                             f"({stacks.path.name})")
            elif value % spec.multiple_of:
                reason = " (MPU region of its own)" if spec.multiple_of > DEFAULT_MULTIPLE else " (stack alignment)"
                error(where, f"stack of task {name} {value} B is no multiple of {spec.multiple_of}{reason}")
            elif value < spec.stack_bytes and use.evidence is None and asil:
                # PLAT-107 (S3): the floor is a plausibility limit, no proof - an ASIL project notes the proof
                error(where, f"stack of task {name} {value} B is below the default {spec.stack_bytes} B without a "
                             f"proof note in an ASIL project: add bsw_tasks.{name}.evidence (worst case of "
                             f"tools/stackuse for this image, or the high-water mark at the board, console "
                             f"'stats'), or keep the default (project.integrity qm: only a warning)")
            else:
                stack = value
                if value < spec.stack_bytes and use.evidence is None:
                    warning(where, f"stack of task {name} {value} B is below the default {spec.stack_bytes} B: "
                                   f"confirm it with the high-water mark at the board (console 'stats') or with "
                                   f"tools/stackuse for this image and note it in bsw_tasks.{name}.evidence")
        if use is not None and use.evidence is not None and run_in is None:
            # PLAT-107: the stack the task gets (the default if the project set none or its value was refused)
            # must hold what its proof says it uses
            used = use.evidence.used_bytes
            if used > stack:
                error((SECTION, name, "evidence", "used_bytes"),
                      f"task {name} uses {used} B according to its proof ({use.evidence.method}: "
                      f"{use.evidence.reference}), more than its stack of {stack} B: raise "
                      f"bsw_tasks.{name}.stack_bytes")
        tasks.append(BswTask(name, stack, spec.stack_bytes, run_in, name in OS_TASKS))
    return BswTaskPlan(tuple(tasks), stacks.path)


def asil_project(cfg: Config, rep: Reporter | None = None) -> bool:
    """PLAT-107 (S3): an ASIL project - project.integrity not `qm`; left out counts as ASIL (the safety part of
    neXtBSW is in every image). `qm` together with an NvM block of ASIL integrity is a contradiction (error)."""
    integrity = cfg.project.project.integrity
    if integrity != "qm":
        return True
    nvm = cfg.project.nvm
    blocks = [name for name, b in (nvm.blocks.items() if nvm is not None else [])
              if (b.integrity or "qm") != "qm"]
    if blocks and rep is not None:
        rep.error("project", ("project", "integrity"), f"project integrity qm, but the NvM block {blocks[0]} has the "
                                                       f"integrity {nvm.blocks[blocks[0]].integrity}: a project with "
                                                       f"an ASIL block is an ASIL project")
    return bool(blocks)


def power_stage_project(cfg: Config) -> str | None:
    """PLAT-107 (S2): what makes the project one with a power stage or a gate driver (None: nothing) - a used PWM
    group (complementary half bridges) or a used device whose module provides the role gate_driver."""
    groups = [grp.name for grp, _ in cfg.selected_pwm()]
    if groups:
        return f"PWM group {', '.join(groups)}"
    integration = cfg.integration
    for dev in getattr(integration, "devices", None) or []:
        manifest = cfg.modules.manifests.get(dev.module)
        if manifest is not None and "gate_driver" in (manifest.provides or []):
            return f"gate driver {dev.name}"
    return None


def _bsw_tasks_of(manifest: Path) -> list[tuple[str, bool]] | None:
    """(name, above_application) of the BSW task manifest; None if it cannot be read (tools/rtegen reports it)."""
    try:
        data = YAML(typ="safe").load(Path(manifest).read_text(encoding="utf-8"))
    except (OSError, YAMLError):
        return None
    if not isinstance(data, dict):
        return None
    return [(t["name"], bool(t.get("above_application", False))) for t in data.get("tasks") or []
            if isinstance(t, dict) and isinstance(t.get("name"), str)]


def _application_stacks(cfg: Config, manifest: Path) -> int:
    """Stacks of the application tasks: rte.yaml tasks[].stack_bytes, else the default task of the manifest."""
    if cfg.rte is not None and cfg.rte.tasks:
        return sum(t.stack_bytes for t in cfg.rte.tasks)
    try:
        data = YAML(typ="safe").load(Path(manifest).read_text(encoding="utf-8"))
        value = data["application_default"]["stack_bytes"]
    except (OSError, YAMLError, KeyError, TypeError):
        return 0
    return value if isinstance(value, int) and cfg.rte is not None else 0


def _ram_sum(cfg: Config, rep: Reporter, plan: BswTaskPlan, manifest: Path):
    """All task stacks plus interrupt stack, its guard, no-init area and RAM functions against the application RAM."""
    layout = cfg.memory
    if layout is None:
        return                                          # no memory partitioning: nothing to compare against
    from .memory import DEFAULT_RAMFUNCTIONS_SIZE
    application = cfg.project.memory.application
    isr = getattr(application, "isr_stack_size", None) or DEFAULT_ISR_STACK_SIZE
    guard = getattr(application, "isr_stack_guard_size", None) or DEFAULT_ISR_STACK_GUARD_SIZE
    ramfunctions = layout.ramfunctions_size if layout.ramfunctions_size is not None else DEFAULT_RAMFUNCTIONS_SIZE
    app = _application_stacks(cfg, manifest)
    total = sum(r.size for r in layout.app_ram)
    need = plan.stack_bytes + app + isr + guard + layout.noinit_size + ramfunctions
    if need > total:
        # An error once the project decides its stacks (bsw_tasks); without that section a warning, so a build
        # profile that is known not to link yet (s32k116evb_minimal, PLAT-12: bootloader and updater are built from
        # it) still configures - the linker refuses the application image either way.
        decided = bool(cfg.project.bsw_tasks)
        report = rep.error if decided else rep.warning
        path = (SECTION,) if decided else ("memory", "application", "ram")
        report("project", path,
               f"task stacks {plan.stack_bytes} B (BSW, idle, timer) + {app} B (application tasks) + interrupt "
               f"stack {isr} B and guard {guard} B + no-init area {layout.noinit_size} B + RAM functions "
               f"{ramfunctions} B = {need} B exceed the application RAM of {total} B "
               f"({', '.join(r.region for r in layout.app_ram)}); reduce bsw_tasks.<task>.stack_bytes, merge a "
               f"task (run_in) or the application task stacks (rte.yaml tasks[].stack_bytes)")


def check(cfg: Config, rep: Reporter, tasks_manifest: Path | None = None,
          stacks_manifest: Path | None = None) -> BswTaskPlan | None:
    """The plan of the task objects of the project, or None if a manifest cannot be read (reported)."""
    tree = cfg.running_tree
    tasks_manifest = tasks_manifest or (Path(tree) if tree is not None else REPO).joinpath(*TASKS_MANIFEST_NAMES)
    stacks_manifest = stacks_manifest or task_stacks_manifest(tree)
    bsw = _bsw_tasks_of(tasks_manifest)
    if bsw is None:
        return None                                     # tools/rtegen reports the broken task manifest
    if not Path(stacks_manifest).is_file() and not cfg.project.bsw_tasks:
        return None                                     # a tree without the manifest (older pin): nothing to decide
    try:
        stacks = load_stack_manifest(stacks_manifest)
    except StackManifestError as e:
        rep.error("project", (SECTION,) if cfg.project.bsw_tasks else (), str(e))
        return None
    for f in (stacks_manifest, tasks_manifest):
        if f not in cfg.code_inputs:
            cfg.code_inputs.append(f)                   # a changed manifest has to reach the configure step
    plan = resolve(bsw, stacks, cfg.project.bsw_tasks,
                   lambda path, text: rep.error("project", path, text),
                   lambda path, text: rep.warning("project", path, text),
                   asil=asil_project(cfg, rep), power_stage=power_stage_project(cfg))
    _ram_sum(cfg, rep, plan, tasks_manifest)
    return plan
