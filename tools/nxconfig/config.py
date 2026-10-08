"""Loads a project with its board and derivative, keeps YAML positions for the messages."""

from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError

from .messages import ConfigErrors, Message, Path_
from .model import Board, Derivative, DiagDescription, EcuDescription, Manifest, Project

REPO = Path(__file__).resolve().parents[2]

# TOOL-85: every folder below is a folder *of a neXtBSW tree*, not of "the" tree. Which tree that is, is a
# question of the caller: the command line runs as the tree it lies in (REPO, the default everywhere), the
# Studio switches the tree per opened project (TOOL-61) and nextbsw/ in a customer repository is a second
# checkout of the same tree. So the places below are derived from a `tree` argument, and `check(running_tree=)`
# hands it down. Binding them to REPO at import time was the reason the Studio had to move the module variable
# for the duration of a check (studio/core/tree.py as_tool_of, TOOL-60) - a global that two threads share.
MCUDB_NAME = "mcudb"
# <dir>/**/<module>/module.yaml: device drivers and services in libs/bsw, MCU drivers and contracts in libs/bsp
MODULE_DIR_NAMES = (("libs", "bsw"), ("libs", "bsp"))
# Ports may carry modules with a manifest (doc/architecture/README.md, section 3, rule 2, e.g. the safety
# supervision until ARCH-05/SYS-06 moves it). Two places are read, both selected by the project alone, so nothing
# here names a board or a family (port_module_dirs):
#   platforms/<family>/port   family port template (PLAT-23), family of the project's derivative
#   ecu/platforms/<board>     board port: board peculiarities and justified exceptions
FAMILY_PORTS_NAME = "platforms"
FAMILY_PORT_NAME = "port"
BOARD_PORTS_NAMES = ("ecu", "platforms")
MANIFEST = "module.yaml"


def tree_of(tree: Path | None = None) -> Path:
    """The neXtBSW tree an operation works with: the given one, else the tree this package lies in."""
    return REPO if tree is None else Path(tree)


def mcudb_dir(tree: Path | None = None) -> Path:
    """Derivative database of a tree (a workspace adds its own through `search.mcudb`, ARCH-56)."""
    return tree_of(tree) / MCUDB_NAME


def module_dirs_of(tree: Path | None = None) -> tuple[Path, ...]:
    """Module folders of a tree, in the order they are searched."""
    root = tree_of(tree)
    return tuple(root.joinpath(*parts) for parts in MODULE_DIR_NAMES)


def family_ports_dir(tree: Path | None = None) -> Path:
    """`platforms/` of a tree: the family port templates (PLAT-23)."""
    return tree_of(tree) / FAMILY_PORTS_NAME


def board_ports_dir(tree: Path | None = None) -> Path:
    """`ecu/platforms/` of a tree: the board ports."""
    return tree_of(tree).joinpath(*BOARD_PORTS_NAMES)


# The tree this package lies in, as the constants other tools import them (tools/bswgen, tools/docgen).
MCUDB = mcudb_dir()
MODULE_DIRS = module_dirs_of()

LEVELS = ("project", "board", "derivative")
RTE_LEVEL = "rte"                                   # ECU description of the project (project.rte, ARCH-34)
DIAG_LEVEL = "diag"                                 # diagnostic description of the project (project.diag, ARCH-51)


def _plain(node):
    """ruamel round-trip nodes -> plain Python types (pydantic strict mode rejects the subclasses)."""
    if isinstance(node, dict):
        return {str(k): _plain(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_plain(v) for v in node]
    if isinstance(node, bool):
        return bool(node)
    if isinstance(node, int):
        return int(node)
    if isinstance(node, float):
        return float(node)
    if isinstance(node, str):
        return str(node)
    return node


def line_of(tree, path: Path_) -> int | None:
    """1-based line of the deepest existing element of `path` in a round-trip YAML tree."""
    line, node = None, tree
    for part in path:
        try:
            if isinstance(node, CommentedMap) and part in node:
                line, node = node.lc.key(part)[0] + 1, node[part]
            elif isinstance(node, CommentedSeq) and isinstance(part, int) and 0 <= part < len(node):
                line, node = node.lc.item(part)[0] + 1, node[part]
            else:
                break
        except (AttributeError, KeyError, TypeError):
            break
    return line


@dataclass
class Source:
    file: Path
    tree: object = None


@dataclass
class Reporter:
    sources: dict[str, Source] = field(default_factory=dict)
    messages: list[Message] = field(default_factory=list)

    def add(self, severity: str, level: str, path: Path_, text: str):
        src = self.sources.get(level)
        file = src.file if src else None
        line = line_of(src.tree, path) if src and src.tree is not None else None
        self.messages.append(Message(severity, text, file, tuple(path), line))

    def error(self, level: str, path: Path_, text: str):
        self.add("error", level, path, text)

    def warning(self, level: str, path: Path_, text: str):
        self.add("warning", level, path, text)

    @property
    def has_errors(self) -> bool:
        return any(m.severity == "error" for m in self.messages)


@dataclass
class ModuleSet:
    """Module manifests by module name, with their files and YAML trees (messages with line numbers)."""

    manifests: dict[str, Manifest] = field(default_factory=dict)
    files: dict[str, Path] = field(default_factory=dict)
    trees: dict[str, object] = field(default_factory=dict)
    messages: list[Message] = field(default_factory=list)

    def message(self, severity: str, module: str, path: Path_, text: str) -> Message:
        return Message(severity, text, self.files.get(module), tuple(path), line_of(self.trees.get(module), path))

    @property
    def has_errors(self) -> bool:
        return any(m.severity == "error" for m in self.messages)


def port_module_dirs(board_name: str, family: str, ports: Path | None = None,
                     family_ports: Path | None = None, tree: Path | None = None) -> tuple[Path, ...]:
    """Module directories of the port of a project: family port template first, then the board port.

    PLAT-23: the handwritten port is a family template (platforms/<family>/port), the board port
    (ecu/platforms/<board>) only holds board peculiarities. Directories that do not exist are left out, so a
    board without peculiarities and a family without a template both work.

    TOOL-85: both folders belong to a tree - `tree` says which one (default: the tree this package lies in).
    `ports` and `family_ports` name them one by one, for tests with their own port folders.
    """
    dirs = (Path(family_ports if family_ports is not None else family_ports_dir(tree)) / family / FAMILY_PORT_NAME,
            Path(ports if ports is not None else board_ports_dir(tree)) / board_name)
    return tuple(d for d in dirs if d.is_dir())


def load_modules(dirs=None) -> ModuleSet:
    """Reads <dir>/**/<module>/module.yaml of every directory in `dirs`. Never raises for manifest problems.

    Only the structure is checked here; the consistency of parameters and constraints is checked in checks.py.
    """
    dirs = MODULE_DIRS if dirs is None else dirs
    result = ModuleSet()

    def error(text: str, file: Path, path=(), tree=None):
        result.messages.append(Message("error", text, file, tuple(path),
                                       line_of(tree, tuple(path)) if tree is not None else None))

    for base in dirs:
        for file in sorted(Path(base).glob(f"**/{MANIFEST}")):
            try:
                tree = YAML(typ="rt").load(file.read_text(encoding="utf-8"))
            except (OSError, YAMLError) as e:
                error(f"cannot read module manifest: {e}", file)
                continue
            if not isinstance(tree, dict):
                error("expected a mapping at the top level of the module manifest", file, (), tree)
                continue
            try:
                manifest = Manifest.model_validate(_plain(tree))
            except ValidationError as e:
                for err in e.errors():
                    loc = tuple(p for p in err["loc"] if not (isinstance(p, str) and ("[" in p or p in _UNION_TAGS)))
                    error(f"{err['msg']}", file, loc, tree)
                continue
            if manifest.module != file.parent.name:
                error(f"module {manifest.module} must be in a directory of the same name (is {file.parent.name})",
                      file, ("module",), tree)
                continue
            if manifest.module in result.manifests:
                error(f"module {manifest.module} is also described in {result.files[manifest.module]}",
                      file, ("module",), tree)
                continue
            result.manifests[manifest.module] = manifest
            result.files[manifest.module] = file
            result.trees[manifest.module] = tree
    return result


@dataclass
class Config:
    project_file: Path
    board_file: Path
    derivative_file: Path
    project: Project
    board: Board
    derivative: Derivative
    modules: ModuleSet = field(default_factory=ModuleSet)
    sources: dict[str, Source] = field(default_factory=dict)   # YAML trees of the levels (line numbers)
    integration: object = None                  # checks.Integration: module instances and roles of the project
    clock: object = None                        # clock.ClockPlan of the family clock model (None: no model)
    adc_access: object = None                   # adc.AdcAccessPlan (None: no family ADC model or no ADC used)
    memory: object = None                       # memory.MemoryLayout (None: project without memory partitioning)
    bootloader: object = None                   # memory.BootloaderPlan (None: project without bootloader section)
    nvm: object = None                          # nvm.NvmPlan (None: project without nvm section, NVM-07)
    diagnostics: object = None                  # diag.DiagPlan (None: project without diagnostics section)
    power_stages: dict = field(default_factory=dict)    # powerstage.PowerStagePlan by used PWM group (IO-24)
    adc_sensors: dict = field(default_factory=dict)     # sensor.SensorPlan by used board ADC channel (IO-18)
    sensed_levels: list = field(default_factory=list)   # sensed.SensedLevel of module parameters with sense: (IO-21)
    i2c_buses: list = field(default_factory=list)       # i2c.I2cBusPlan of the used I2C buses (IO-28)
    pin_events: list = field(default_factory=list)      # pinevent.PinEventUse of project pin_events (IO-51)
    rte: EcuDescription | None = None           # ECU description (project.rte, ARCH-34); None: none or not found
    # ARCH-51: diagnostic description (project.diag); None: none or not found
    diag: DiagDescription | None = None
    diag_description: object = None             # diagdesc.DiagDescriptionPlan (None: no description or errors)
    safety_timing: object = None                # checks.SafetyTiming (None: no safety task module, SYS-25)
    safe_states: object = None                  # safestate.SafeStatePlan of the used PWM groups (SYS-116)
    # SYS-25: code files the checks read constants from (checks.read_constant) - configure inputs like the YAML files
    code_inputs: list[Path] = field(default_factory=list)
    # ARCH-56: workspace pin nextbsw.yaml above the project (workspace.Workspace); None: the running tree
    workspace: object = None
    # ARCH-58: derived feature switches of the build (features.Switch by upper-case name); None: not derived yet
    features: dict | None = None
    hw_thresholds: list = field(default_factory=list)   # hwthreshold.HwThresholdUse of project hw_thresholds (SYS-86)
    sample_groups: list = field(default_factory=list)   # samplegroup.SampleGroupUse of project sample_groups (IO-62)
    # TOOL-85: the neXtBSW tree the tools ran as while this configuration was loaded - the tree the workspace pin
    # was measured against, and the tree the port folders, code constants and bootloader hooks come from. The
    # command line runs as the tree it lies in (REPO); the Studio switches the tree per opened project (TOOL-61).
    running_tree: Path = REPO
    bsw_tasks: object = None                    # bswtasks.BswTaskPlan (None: not checked or no manifest, PLAT-26)

    @property
    def rte_file(self) -> Path | None:
        rte = self.project.project.rte
        return (self.project_file.parent / rte).resolve() if rte else None

    @property
    def diag_file(self) -> Path | None:
        diag = self.project.project.diag
        return (self.project_file.parent / diag).resolve() if diag else None

    def selected(self, kind: str) -> list:
        """Board entries of `kind` the project uses, in board order. kind 'dio.inputs'/'dio.outputs' for DIO."""
        if kind.startswith("dio."):
            entries, selection = getattr(self.board.dio, kind[4:]), self.project.use.dio
        else:
            entries, selection = getattr(self.board, kind), getattr(self.project.use, kind)
        if selection == "all":
            return list(entries)
        wanted = set(selection)
        return [e for e in entries if e.name in wanted]

    def selected_pwm(self) -> list[tuple]:
        """(board group, project parameters) of the PWM groups the project uses, in board order."""
        return [(g, self.project.pwm[g.name]) for g in self.board.pwm if g.name in self.project.pwm]


def _read(reporter: Reporter, level: str, file: Path, model: type[BaseModel], running_tree: Path | None = None):
    """`running_tree` (TOOL-85): the tree whose formats the file is measured against (migrate.finding)."""
    reporter.sources[level] = Source(file)
    try:
        tree = YAML(typ="rt").load(file.read_text(encoding="utf-8"))
    except OSError as e:
        reporter.error(level, (), f"cannot read {file}: {e.strerror or e}")
        return None
    except YAMLError as e:
        reporter.error(level, (), f"invalid YAML: {e}")
        return None
    reporter.sources[level].tree = tree
    if not isinstance(tree, dict):
        reporter.error(level, (), f"expected a mapping at the top level of {file.name}")
        return None
    # TOOL-66: a file whose format does not fit this tree is reported here, before the schema complains about
    # fields that only exist in the other format: too old with the migration command that raises it, too new with
    # the statement that the tree is older than the files.
    from . import migrate
    problem = migrate.finding(level, file, tree, tree_of(running_tree))
    if problem is not None:
        reporter.add(problem.severity, level, problem.path, problem.text)
        return None
    try:
        return model.model_validate(_plain(tree))
    except ValidationError as e:
        for err in e.errors():
            loc = tuple(p for p in err["loc"] if not (isinstance(p, str) and ("[" in p or p in _UNION_TAGS)))
            reporter.error(level, loc, f"{err['msg']} (input: {_short(err.get('input'))})")
        return None


# pydantic inserts union member tags into error locations, e.g. ('use', 'dio', "literal['all']")
_UNION_TAGS = {"int", "float", "str", "bool", "none", "constrained-str", "constrained-int", "constrained-float"}


def _short(value) -> str:
    text = repr(value)
    return text if len(text) <= 60 else text[:57] + "..."


def read_rte(file: Path) -> tuple[EcuDescription | None, list[Message], object]:
    """Reads and validates an ECU description (rte.yaml) on its own: (model or None, messages, YAML tree).

    The messages carry file, line and YAML path. check() does the same for the file the project names; tools/rtegen
    uses this for an ECU description given next to a loaded project.
    """
    reporter = Reporter()
    ecu = _read(reporter, RTE_LEVEL, Path(file), EcuDescription)
    return ecu, reporter.messages, reporter.sources[RTE_LEVEL].tree


def read_diag(file: Path) -> tuple[DiagDescription | None, list[Message], object]:
    """Reads and validates a diagnostic description (diag.yaml) on its own: (model or None, messages, tree).

    The messages carry file, line and YAML path. check() does the same for the file the project names; the
    Studio and later generators (COM-28, NVM-03, TOOL-43) use this for a file given on its own.
    """
    reporter = Reporter()
    desc = _read(reporter, DIAG_LEVEL, Path(file), DiagDescription)
    return desc, reporter.messages, reporter.sources[DIAG_LEVEL].tree


def find_derivative(name: str, mcudb: Path | None = None) -> Path | None:
    matches = sorted(Path(mcudb if mcudb is not None else MCUDB).glob(f"*/{name}.yaml"))
    return matches[0] if matches else None


def check(project_file: Path, mcudb: Path | None = None, module_dirs=None,
          port_requirements: bool = True, running_tree: Path | None = None
          ) -> tuple[Config | None, list[Message]]:
    """Loads and checks a project. Never raises for configuration problems; the Studio uses this.

    ARCH-56: a workspace pin nextbsw.yaml above the project (workspace.find_pin_file) adds its search paths before
    `mcudb` and `module_dirs` (the tree's own folders); without one nothing changes.

    TOOL-85 - `running_tree`: **which neXtBSW tree this call runs as**. Default (None) is the tree this package
    lies in, so the command line and every generator keep behaving as before: a pin that names another tree is
    refused ("use the tools of the pinned tree", customer-project.md section 3.1), because a generator must not
    silently work with a foreign tree.

    A caller that switches trees on purpose says so with this parameter, and nothing else changes for it:
    `nextbsw/` in a customer repository is a second checkout of the same tree, and the Studio is exactly the tool
    that picks the tree per opened project (TOOL-61). It then passes `running_tree=<pinned tree>` and the pin is
    measured against that tree - instead of moving the module variable `REPO` under a lock for the duration of a
    check (the former studio/core/tree.py `as_tool_of`, TOOL-60).

    A caller that names `running_tree` and neither `mcudb` nor `module_dirs` gets **that tree's** derivative
    database and module folders; without `running_tree` they stay the folders of the tree this package lies in
    (`MCUDB`, `MODULE_DIRS`), so no call that exists today reads from anywhere else than before. The family and
    board port folders and the BSW task manifest follow the running tree in both cases (`checks.run`).
    """
    from . import checks
    from . import workspace as nxworkspace

    project_file = Path(project_file).resolve()
    named = running_tree is not None
    running_tree = tree_of(running_tree).resolve()
    mcudb = ((mcudb_dir(running_tree) if named else MCUDB) if mcudb is None else Path(mcudb))
    module_dirs = ((module_dirs_of(running_tree) if named else MODULE_DIRS)
                   if module_dirs is None else tuple(module_dirs))
    reporter = Reporter()
    pinned, ws = nxworkspace.discover(project_file, reporter, running_tree)
    if pinned and ws is None:
        return None, reporter.messages
    project = _read(reporter, "project", project_file, Project, running_tree)
    if project is None:
        return None, reporter.messages

    board_path = (project_file.parent / project.project.board).resolve()
    board_file = board_path / "board.yaml" if board_path.is_dir() else board_path
    board = _read(reporter, "board", board_file, Board, running_tree)
    if board is None:
        if not board_file.exists():
            reporter.messages[-1] = Message("error", f"board {project.project.board} not found ({board_file})",
                                            project_file, ("project", "board"),
                                            line_of(reporter.sources["project"].tree, ("project", "board")))
        return None, reporter.messages
    if ws is not None and not _unique_board(ws, board, board_file, reporter):
        return None, reporter.messages

    mcudbs = (ws.mcudb if ws else ()) + (mcudb,)
    derivative_file = _derivative_file(board.board.mcu, mcudbs, reporter)
    if derivative_file is None:
        return None, reporter.messages
    derivative = _read(reporter, "derivative", derivative_file, Derivative, running_tree)
    if derivative is None:
        return None, reporter.messages

    # Modules of the workspace (search.modules, ARCH-56) and of the repository plus those of the port of this
    # project (port_module_dirs): family template and board port. A module name found twice is an error of
    # load_modules with both files.
    ports = port_module_dirs(board.board.name, derivative.derivative.family, tree=running_tree)
    dirs = (ws.modules if ws else ()) + tuple(module_dirs) + ports
    config = Config(project_file, board_file, derivative_file, project, board, derivative,
                    load_modules(dirs), reporter.sources, workspace=ws, running_tree=running_tree)
    # ARCH-34: the ECU description belongs to the project level; its schema is checked before the build like the
    # three files above. A missing file is reported by checks.py at project.rte.
    if config.rte_file is not None and config.rte_file.is_file():
        config.rte = _read(reporter, RTE_LEVEL, config.rte_file, EcuDescription, running_tree)
    # ARCH-51: the diagnostic description belongs to the project level as well; a missing file is reported by
    # checks.py at project.diag.
    if config.diag_file is not None and config.diag_file.is_file():
        config.diag = _read(reporter, DIAG_LEVEL, config.diag_file, DiagDescription, running_tree)
    checks.run(config, reporter, port_requirements_check=port_requirements)
    return (None if reporter.has_errors else config), reporter.messages


def _derivative_file(name: str, mcudbs: tuple[Path, ...], reporter: Reporter) -> Path | None:
    """The derivative description `name` from the first of `mcudbs` (workspace search.mcudb, then the tree's).

    A derivative in two databases is an error with both paths, at the workspace entry that repeats it.
    """
    from . import workspace as nxworkspace

    found = nxworkspace.find_derivative_files(name, mcudbs)
    if not found:
        where = mcudbs[-1] if len(mcudbs) == 1 else ", ".join(str(d) for d in mcudbs)
        reporter.error("board", ("board", "mcu"), f"no derivative description {name}.yaml in {where}")
        return None
    if len(found) > 1:
        (first, a), (_, b) = found[0], found[1]
        reporter.error(nxworkspace.WORKSPACE_LEVEL, ("search", "mcudb", first),
                       f"derivative {name} is described twice: {a} and {b}; a workspace derivative must not "
                       "repeat one of another database (rename it or drop the copy)")
        return None
    return found[0][1]


def _unique_board(ws, board: Board, board_file: Path, reporter: Reporter) -> bool:
    """False (error with both paths) if another board folder of the workspace (search.boards) or the tree
    (boards/) has the name of the project's board: the name selects the board port (port_module_dirs)."""
    from . import workspace as nxworkspace

    name = board.board.name
    own = Path(board_file).resolve().parent
    others = [f for _, f in nxworkspace.board_folders(name, ws.board_dirs()) if f.resolve() != own]
    if not others:
        return True
    reporter.error("board", ("board", "name"),
                   f"board name {name} is used twice: {own} and {others[0]}; board names must be unique in the "
                   f"workspace and the tree (the name selects the board port; {nxworkspace.PIN_FILE} search.boards)")
    return False


def load(project_file: Path, mcudb: Path | None = None, module_dirs=None,
         running_tree: Path | None = None) -> Config:
    """Like check(), but raises ConfigErrors if there is an error. For generators."""
    config, messages = check(project_file, mcudb, module_dirs, running_tree=running_tree)
    if config is None:
        raise ConfigErrors(messages)
    return config


# ----------------------------------------------------------------------------------------------- cores


INTERRUPT_CONTROLLERS = ("nvic", "vim")


@dataclass(frozen=True)
class Core:
    """Facts of one core (doc/architecture/plattformen.md, section 4): compiler options, architecture profile and
    interrupt controller model (ARCH-23).

    nvic: interrupt priority = NVIC level, the RTOS masks up to a syscall level (BASEPRI; ARMv6-M: everything).
    vim:  interrupt priority = VIM channel number, the RTOS boundary is IRQ (may call the RTOS) against FIQ (never);
          there are no priority bits and no syscall level. The channel count is a derivative fact (mcudb vim).
    """

    mcpu: str
    fpus: dict[str, str | None]                     # derivative.fpu -> -mfpu (None: no FPU, soft float ABI)
    architecture: str                               # armv6m, armv7m, armv8m, armv7r: selects the platform code
    interrupt_controller: str = "nvic"              # nvic | vim: selects the interrupt checks (checks.py)
    nvic_priority_bits: tuple[int, int] | None = None   # nvic only: implementable NVIC priority bits [min, max]
    max_syscall_default: int | None = None          # nvic only: RTOS syscall level of the architecture

    def __post_init__(self):
        if self.interrupt_controller not in INTERRUPT_CONTROLLERS:
            raise ValueError(f"core {self.mcpu}: unknown interrupt controller {self.interrupt_controller}")
        nvic = self.interrupt_controller == "nvic"
        if nvic != (self.nvic_priority_bits is not None) or nvic != (self.max_syscall_default is not None):
            raise ValueError(f"core {self.mcpu}: nvic_priority_bits and max_syscall_default belong to NVIC cores "
                             "and only to them")


# derivative.core -> facts. PLAT-21: the only place that knows the cores. The toolchain file uses NX_MCPU/NX_MFPU,
# the platform code NX_CORE_ARCHITECTURE (platforms/common/cmake/NxCoreArchitecture.cmake), the configuration checks
# interrupt_controller, nvic_priority_bits (checks.NVIC_PRIORITY_BITS) and max_syscall_default.
#
# ARMv6-M fixes 2 priority bits (bits [7:6]), ARMv7-M and ARMv8-M Mainline implement 3 to 8. ARMv6-M has no BASEPRI:
# the FreeRTOS port masks every interrupt (PRIMASK), so the syscall level must be 0. On the other architectures the
# default leaves only level 0 unmaskable; a project may raise it for further zero latency interrupts.
#
# ARCH-23: Cortex-R4F is ARMv7-R with a VFPv3-D16 FPU (double precision, 16 D registers) [ARM]. The Cortex-R4 has
# no interrupt controller of its own (no BASEPRI; IRQ and FIQ masked through the CPSR I and F bits), the SoC brings
# it. The derivatives of the target picture (family rm4x, architecture D13) use the TI VIM, so the entry names vim;
# a Cortex-R4F with another controller (for example a GIC) needs its own entry. Until the platform code for armv7r
# exists (ARCH-24/25, PLAT-35), FreeRTOS port, fault handler and updater stop with a message for this core.
CORES: dict[str, Core] = {
    "cortex-m0plus": Core("cortex-m0plus", {"none": None}, "armv6m", "nvic", (2, 2), 0),
    "cortex-m4f": Core("cortex-m4", {"sp": "fpv4-sp-d16"}, "armv7m", "nvic", (3, 8), 1),
    "cortex-m7f": Core("cortex-m7", {"sp": "fpv5-sp-d16", "dp": "fpv5-d16"}, "armv7m", "nvic", (3, 8), 1),
    "cortex-m33": Core("cortex-m33", {"sp": "fpv5-sp-d16", "none": None}, "armv8m", "nvic", (3, 8), 1),
    "cortex-r4f": Core("cortex-r4f", {"dp": "vfpv3-d16"}, "armv7r", "vim"),
}


def core_fpu(derivative) -> str | None:
    """FPU variant of a derivative (model.DerivativeInfo): the given one or the core's only variant.

    None for an unknown core or when the core has several variants and none is given (checks.py reports both).
    """
    core = CORES.get(derivative.core)
    if core is None:
        return None
    if derivative.fpu is not None:
        return derivative.fpu if derivative.fpu in core.fpus else None
    return next(iter(core.fpus)) if len(core.fpus) == 1 else None


def core_flags(derivative) -> dict[str, str]:
    """NX_FPU, NX_MCPU, NX_MFLOAT_ABI, NX_MFPU of a checked derivative (model.DerivativeInfo)."""
    core, fpu = CORES[derivative.core], core_fpu(derivative)
    mfpu = core.fpus[fpu]
    return {"NX_FPU": fpu, "NX_MCPU": core.mcpu, "NX_MFLOAT_ABI": "soft" if mfpu is None else "hard",
            "NX_MFPU": mfpu or ""}


# ----------------------------------------------------------------------------------------------- build settings

# board.debug.interface -> NX_DEBUG_INTERFACE (spelling of the J-Link "si" command)
DEBUG_INTERFACES = {"JTAG": "JTAG", "SWD": "SWD", "CJTAG": "cJTAG"}
JLINK_INTERFACES = DEBUG_INTERFACES                 # name before PLAT-18
DEFAULT_DEBUG_SPEED_KHZ = 1000                      # without board.debug.speed_khz: safe for every probe and board


@dataclass(frozen=True)
class ProbeKind:
    """A kind of debug probe and the flash tools that drive it (PLAT-18).

    The board names its probe (board.debug.probe), this table selects the implementations; which of them is
    installed and where is workstation level (root CMakeLists.txt: NX_FLASH_TOOL and the tool path variables).
    """
    prefixes: tuple[str, ...]                       # board.debug.probe, lower case without " -_", starts with one
    interfaces: tuple[str, ...]                     # NX_DEBUG_INTERFACE values the probe drives
    tools: tuple[str, ...]                          # flash tools in order of preference (NX_FLASH_TOOLS)
    openocd_interface: str = ""                     # OpenOCD adapter configuration
    openocd_transports: dict[str, str] = field(default_factory=dict)   # NX_DEBUG_INTERFACE -> OpenOCD transport


PROBES = {
    "jlink": ProbeKind(("jlink",), ("JTAG", "SWD", "cJTAG"), ("jlink",)),
    "stlink": ProbeKind(("stlink",), ("SWD", "JTAG"), ("stm32cubeprogrammer", "openocd"),
                        "interface/stlink.cfg", {"SWD": "hla_swd", "JTAG": "hla_jtag"}),
    # OpenSDA only with CMSIS-DAP (DAPLink) firmware; the PEmicro firmware of new boards speaks neither pyOCD nor
    # OpenOCD (projects/README.md, section flash targets)
    "cmsis-dap": ProbeKind(("cmsisdap", "daplink", "opensda"), ("SWD", "JTAG"), ("pyocd", "openocd"),
                           "interface/cmsis-dap.cfg", {"SWD": "swd", "JTAG": "jtag"}),
    # TI XDS110: flashing with the UniFlash command line (dslite), debugging with OpenOCD (decision 2026-09-17)
    "xds110": ProbeKind(("xds110",), ("JTAG", "cJTAG", "SWD"), ("dslite",)),
}
# a board.debug section without probe: J-Link, as before PLAT-18
DEFAULT_PROBE = "jlink"


def probe_kind(probe: str | None) -> str | None:
    """Key of PROBES for a board.debug.probe text ("J-Link EDU Mini", "ST-LINK/V2-1", "OpenSDA"), None if unknown."""
    if probe is None:
        return DEFAULT_PROBE
    plain = "".join(c for c in probe.lower() if c not in " -_")
    return next((kind for kind, p in PROBES.items() if plain.startswith(p.prefixes)), None)


def build_settings(config: Config) -> tuple[dict[str, str | list[str]], list[Message]]:
    """Values the build selects from the project (CMake: NX_*): board, derivative, family, core, paths, debug.

    Nothing here is a board constant; everything follows from the three levels. Returns the settings and
    warnings (with YAML path) for debug settings no flash target can use.
    """
    messages: list[Message] = []
    board, derivative = config.board.board, config.derivative.derivative
    rte, diag = config.rte_file, config.diag_file
    settings: dict[str, str | list[str]] = {
        "NX_PROJECT_NAME": config.project.project.name,
        "NX_PROJECT_FILE": config.project_file.as_posix(),
        "NX_PROJECT_DIR": config.project_file.parent.as_posix(),
        "NX_BOARD": board.name,
        "NX_BOARD_FILE": config.board_file.as_posix(),
        "NX_BOARD_DIR": config.board_file.parent.as_posix(),
        "NX_DERIVATIVE": derivative.name,
        "NX_DERIVATIVE_FILE": config.derivative_file.as_posix(),
        "NX_FAMILY": derivative.family,
        "NX_CORE": derivative.core,
        # PLAT-21: architecture profile of the core for the platform code (FreeRTOS port, fault handler, startup)
        "NX_CORE_ARCHITECTURE": CORES[derivative.core].architecture,
        **core_flags(derivative),
        "NX_PACKAGE": board.package,
        # PLAT-12: derivative facts the build itself needs (not only the generated code): features the platform
        # switches on (mcudb derivative.features, e.g. lmem_cache for the OpenBSW cache driver) and whether the
        # derivative has a PLL at all (the s32k1xx clock driver of OpenBSW does not compile without SPLL).
        "NX_FEATURES": list(derivative.features),
        # a derivative without clock description (synthetic test profiles, other families) says nothing about a
        # PLL: only a described clock tree without PLL switches it off
        "NX_CLOCK_PLL": "OFF" if config.derivative.clock is not None and config.derivative.clock.pll is None else "ON",
        "NX_RTE_FILE": rte.as_posix() if rte else "",
        "NX_DIAG_FILE": diag.as_posix() if diag else "",
        "NX_DEBUG_INTERFACE": "",
        "NX_DEBUG_PROBE": "",
        "NX_DEBUG_SPEED_KHZ": str(DEFAULT_DEBUG_SPEED_KHZ),
        "NX_JLINK_DEVICE": "",
        # PLAT-18: flash targets per probe (root CMakeLists.txt, section flash targets)
        "NX_DEBUG_PROBE_KIND": "",
        "NX_FLASH_TOOLS": [],
        "NX_PYOCD_TARGET": "",
        "NX_OPENOCD_TARGET": "",
        "NX_OPENOCD_INTERFACE": "",
        "NX_OPENOCD_TRANSPORT": "",
    }
    # ARCH-58 (customer-project.md N5): the feature switches of the build, derived from project, board and the
    # port instead of hand-written in ecu/platforms/<board>/Options.cmake. The root CMakeLists.txt sets
    # PLATFORM_SUPPORT_<name> from NX_FEATURE_<name>; a board port may still override one as an exception.
    from . import features as _features
    if config.features is None:                     # build_settings without a checks.run before it
        config.features = _features.derive(config, repo=config.running_tree)
    settings.update(_features.settings_of(config.features))
    # PLAT-27: console and logger selectable, size of the logger ring buffer from the project (console.py)
    from . import console as _console
    settings.update(_console.settings(config))
    debug = board.debug
    if debug is not None:
        settings["NX_DEBUG_PROBE"] = debug.probe or ""
        if debug.speed_khz is not None:
            settings["NX_DEBUG_SPEED_KHZ"] = str(debug.speed_khz)
        _debug_settings(config, debug, settings, messages)
    inputs = [config.project_file, config.board_file, config.derivative_file] + ([rte] if rte else [])
    inputs += [diag] if diag else []
    # SYS-25: manifests of the port modules whose parameters the project sets (their defaults reach the generated
    # headers; the root CMakeLists.txt globs the manifests in libs/bsw, not those of the ports) and the code files
    # constants were read from
    used = getattr(config.integration, "modules", None) or {}
    manifests = [config.modules.files[m] for m in sorted(used) if m in config.modules.files]
    inputs += [f for f in manifests if not _is_below(f, config.running_tree / "libs")]
    inputs += [f for f in config.code_inputs if f not in inputs]
    inputs += referenced_derivative_files(config)
    if config.workspace is not None:
        inputs.append(config.workspace.file)        # ARCH-56: the pin decides the search paths
    settings["NX_CONFIG_FILES"] = [p.as_posix() for p in inputs]
    return settings, messages


def _is_below(file: Path, directory: Path) -> bool:
    return Path(file).resolve().is_relative_to(Path(directory).resolve())


def _debug_settings(config: Config, debug, settings: dict, messages: list[Message]) -> None:
    """Probe kind, flash tool candidates and the target names of the tools (PLAT-18).

    Without a usable probe and interface NX_FLASH_TOOLS stays empty; the flash targets then say why when they run.
    """
    derivative = config.derivative.derivative
    interface = DEBUG_INTERFACES.get(debug.interface.upper())
    if interface is None:
        messages.append(_board_message(config, ("board", "debug", "interface"),
                                       f"debug interface {debug.interface} is not supported by the flash targets "
                                       f"({', '.join(DEBUG_INTERFACES.values())}), no flash targets"))
        return
    settings["NX_DEBUG_INTERFACE"] = interface
    kind = probe_kind(debug.probe)
    if kind is None:
        known = ", ".join(f"{k} ({'/'.join(p.tools)})" for k, p in PROBES.items())
        messages.append(_board_message(config, ("board", "debug", "probe"),
                                       f"debug probe {debug.probe} is unknown to the flash targets (known: {known}), "
                                       "no flash targets"))
        return
    probe = PROBES[kind]
    if interface not in probe.interfaces:
        messages.append(_board_message(config, ("board", "debug", "interface"),
                                       f"probe {debug.probe} ({kind}) does not drive {interface} "
                                       f"({', '.join(probe.interfaces)}), no flash targets"))
        return
    settings["NX_DEBUG_PROBE_KIND"] = kind
    settings["NX_FLASH_TOOLS"] = list(probe.tools)
    if kind == "jlink":
        # J-Link device names follow the derivative unless it names another one
        settings["NX_JLINK_DEVICE"] = derivative.jlink_device or derivative.name
    if "pyocd" in probe.tools:
        # pyOCD target types are lower case derivative names unless the derivative names another one (CMSIS pack)
        settings["NX_PYOCD_TARGET"] = derivative.pyocd_target or derivative.name.lower()
    if "openocd" in probe.tools:
        # no default: OpenOCD target scripts are per family and not derived from the name (the flash target reports
        # a missing derivative.openocd_target)
        settings["NX_OPENOCD_TARGET"] = derivative.openocd_target or ""
        settings["NX_OPENOCD_INTERFACE"] = probe.openocd_interface
        settings["NX_OPENOCD_TRANSPORT"] = probe.openocd_transports[interface]


def referenced_derivative_files(config: Config) -> list[Path]:
    """mcudb files the derivative refers to and the checks read: pin multiplexing table of the board's package,
    derivative the device header was written for (configure inputs of the build)."""
    from . import checks
    files = []
    ref = config.derivative.pinmux.get(config.board.board.package)
    if ref is not None and ref.file is not None and checks.pinmux(config) is not None:
        files.append(config.derivative_file.parent / ref.file)
    described = checks.described_derivative(config)
    if described is not None and described is not config.derivative:
        files.append(config.derivative_file.parent / f"{config.derivative.device_header.describes}.yaml")
    return files


def _board_message(config: Config, path: Path_, text: str) -> Message:
    try:
        tree = YAML(typ="rt").load(config.board_file.read_text(encoding="utf-8"))
    except (OSError, YAMLError):
        tree = None
    return Message("warning", text, config.board_file, path, line_of(tree, path) if tree is not None else None)


def _cmake_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace(";", "\\;") + '"'


def render_cmake(settings: dict[str, str | list[str]], source: str) -> str:
    """CMake include file with one set() per setting; lists become CMake lists."""
    lines = [f"# Generated by tools/nxconfig from {source} - do not edit.", ""]
    for name, value in settings.items():
        if isinstance(value, list):
            lines.append(f"set({name}\n" + "".join(f"    {_cmake_quote(v)}\n" for v in value) + ")")
        else:
            lines.append(f"set({name} {_cmake_quote(value)})")
    return "\n".join(lines) + "\n"


def write_if_changed(path: Path, content: str) -> bool:
    path = Path(path)
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    return True
