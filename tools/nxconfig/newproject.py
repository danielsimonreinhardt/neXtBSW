"""New project (TOOL-59 part a): an empty project for a board, or a copy of an existing project.

Core function without Qt, used by the command line (`nxconfig new`) and neXtBSW Studio ("Neues Projekt ...").
The flow follows doc/architecture/customer-project.md section 6 (development case 6.4): nothing is written before
create(); the plan shows every file; the project is written to a staging folder next to the target and moved into
place in one step, so an error leaves nothing half-written; afterwards `nxconfig check` runs on the result.

Why here and not in the Studio: the project format belongs to tools/nxconfig (schema, checks, board and derivative
lookup), the command line and CI need it without PySide6, and the workspace case of TOOL-60 (own repository with
a pinned neXtBSW, customer-project.md N2) extends the nxconfig lookup as well. The Studio adds only the location
step (studio/core/newproject.py) and the dialog.

Board neutrality: an empty project is derived from the board and its derivative only - core clock, memory
partitioning, bootloader bus, console and interrupt ranks come from the board and mcudb facts and are confirmed by
nxconfig, never copied from an example project.

TOOL-110 (finding F6 of the TOOL-75 run): the `safety` section is NOT derived. Until v_0.16.7 the wizard wrote the
first two outputs of the board and the first GPIO pin the board file does not describe into it - on a pump
controller that was `safe_state_output: PUMP_EN`, `locked_pin: FAULT_LED`, `test_pin: PTA6`, with no word. What
the three keys do at run time (platforms/s32k1xx/port/safety/safeIo, bswgen collect()/collect_safe_io()):
    safe_state_output   entering the safe state DISABLES the pin (PCR MUX 0, the pin floats), leaving it muxes the
                        pin back to GPIO - with the level the application last wrote, so an actuator on it comes
                        back on without an enable of its driver
    locked_pin          its pin control register is locked at start (PCR LK) and checked every safety cycle with
                        direction and clock gate; the level stays writable, any reconfiguration is refused by the
                        hardware and a changed direction enters the safe state
    test_pin            configured as GPIO input with pull-down and checked every safety cycle; the console
                        (`safety dst`/`est`) disables it and switches it back to leave the safe state again
Which output is an indicator and which pin is really unconnected is a fact of the circuit that no board file
states, so the wizard asks: `plan_empty(safety=None)` writes the section commented out, with the candidates of the
board and what each key does, and says in the notes that the family port needs it; `safety=SafetyChoice(...)` is
the explicit choice of the user (Studio page "Sicherheit", command line --safe-state-output/--locked-pin/--test-pin).
Only the board check of the board wizard (newboard.check_board) plans with SAFETY_PROBE - in a scratch folder
that is deleted afterwards, never in a project of the user.

TOOL-131 (user test 2026-09-25, scope decided 2026-09-29): `with_diag` writes the standard diagnostic description of
neXtBSW (tools/diaggen/standard_diag.yaml) instead of an empty diag.yaml - sessions, the application security level,
the identification and status identifiers and the DTCs the basic software raises itself, with numbers derived from
the neXtBSW range (diag.yaml bsw_dtc_range, TOOL-178). Two parts follow the project, both derived and never guessed: the supply voltage in 0x0120 only if a
used board device provides the role power_supply (module.yaml `provides`), the calibration DTCs only with a
calibration segment (`calibration=True` writes memory.calibration: one sector of the code flash, refuse,
development). A board without a CAN bus has no diagnostic connection: it keeps the empty description.

TOOL-181: an existing board.yaml can be taken over as the template of a NEW board (plan_board_copy /
create_board_copy at the end of this module): copied under a new name, sources and verify markers kept, relative
paths kept valid or named.
"""

import keyword
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from . import bswdtc
from . import config as nxcfg
from . import genlicense
from . import nvm as nxnvm
from .messages import Message

PROJECT_FILE = "project.yaml"
RTE_FILE = "rte.yaml"
DIAG_FILE = "diag.yaml"
# TOOL-131: standard diagnostic description of the neXtBSW tree (template rules in its header)
STANDARD_DIAG = Path("tools") / "diaggen" / "standard_diag.yaml"
STANDARD_DIAG_FEATURES = ("supply", "calibration")
# COM-46: blocks of the template written once per entry (`# [each <kind>]`): can_bus - once per used CAN bus, the
# example bus of the template (STANDARD_DIAG_EXAMPLE_BUS, index 0) replaced by the name and index of the bus
STANDARD_DIAG_EACH = ("can_bus",)
STANDARD_DIAG_EXAMPLE_BUS = "CAN0"
STANDARD_DIAG_NOTES_END = "# ---- end of template notes ----"
# Default neXtBSW DTC range (decision 2026-09-29; a project value since TOOL-178: diag.yaml bsw_dtc_range, plan in
# bswdtc.py and in the header of STANDARD_DIAG). A project numbers its own DTCs outside it; the base is the group
# identifier, never a DTC.
NXBSW_DTC_FIRST = bswdtc.BSW_DTC_BASE
NXBSW_DTC_LAST = bswdtc.BSW_DTC_BASE + bswdtc.BSW_DTC_SIZE - 1
NXBSW_DTC_BLOCKS = {s.key: (bswdtc.BSW_DTC_BASE >> 8) + s.block for s in bswdtc.PLAN if s.events}
POWER_SUPPLY_ROLE = "power_supply"
TASKS_MANIFEST = Path("ecu") / "asyncCoreConfiguration" / "tasks.yaml"

TEMPLATES = ("empty", "copy")

# TOOL-106 (F5): which signals of the board an EMPTY project switches on in `use`.
#   minimal  the first CAN bus (bootloader, diagnostics), the first UART (console), the SPI bus of its transceiver
#            (TOOL-110: and the two outputs of the safety section, only if the user chose them); every other
#            signal of the board is named in the notes, so nothing is left out silently
#   all      every bus, every digital input and output, every ADC and input capture channel of the board, with a
#            bit rate, a baud rate and an interrupt rank for every bus that needs one. For a board that was just
#            described signal by signal (the board wizard) this is what the user means; the Studio offers it and
#            the command line has --all-signals.
# PWM groups are switched on in neither case: `pwm.<group>` needs frequency and dead time, and a guessed dead time
# on a half bridge destroys hardware - that is a decision of the project, the notes say where it goes.
SIGNAL_CHOICES = ("minimal", "all")
USE_KINDS = ("can", "uart", "spi", "i2c", "adc", "input_capture")          # board lists that `use.<kind>` selects

# Project name: the build directory (<build root>/<name>), NX_PROJECT_NAME and generated identifiers use it, so it
# follows the C identifier rule and the folder rule at once; model.Name ([A-Za-z0-9_]+) is the looser schema rule.
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,47}$")
C_KEYWORDS = frozenset({
    "auto", "break", "case", "char", "const", "continue", "default", "do", "double", "else", "enum", "extern",
    "float", "for", "goto", "if", "inline", "int", "long", "register", "restrict", "return", "short", "signed",
    "sizeof", "static", "struct", "switch", "typedef", "union", "unsigned", "void", "volatile", "while", "bool",
    "true", "false", "class", "namespace", "template", "new", "delete", "this", "private", "public", "protected",
    "virtual", "operator", "friend", "using", "try", "catch", "throw"})
WINDOWS_DEVICE_NAMES = frozenset({"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
                                  *(f"lpt{i}" for i in range(1, 10))})

# Copy: never taken over (build output, tool caches). Top level only: `build` and `generated` of the project
# itself; a component's generated/ folder (ARXML, code of Embedded Coder) is a source and is copied.
SKIP_TOP = frozenset({"build", "generated", "out"})
SKIP_ANYWHERE = frozenset({".git", "__pycache__", "slprj", ".vs", ".vscode", ".idea"})
SKIP_SUFFIXES = (".pyc", ".slxc", ".asv", ".autosave", ".bak", ".tmp")
SKIP_DIR_SUFFIXES = ("_ert_rtw", "_grt_rtw")
TEXT_SUFFIXES = (".yaml", ".yml", ".md", ".txt")

# Empty project, derived values (every one is checked by nxconfig afterwards)
COMMON_CLOCKS_HZ = tuple(mhz * 1_000_000 for mhz in (480, 400, 300, 240, 216, 200, 180, 168, 160, 150, 120, 112,
                                                     100, 96, 90, 84, 80, 72, 64, 60, 48, 40, 32, 24, 16, 12, 8))
BOOTLOADER_FLASH = 0x8000           # flash of the bootloader on a derivative with >= 256 KB code flash
BOOTLOADER_FLASH_MIN = 0x2800       # smaller flash: an eighth of it, at least this (ARMv6-M bootloader ~8.6 KB)
BOOTLOADER_RAM = 0x8000             # at most this much RAM for the bootloader
HOT_RAM_MIN = 0x2000                # hot part of the linker template: a RAM chain does not start with a smaller region
SMALL_RAM = 0x8000                  # below: small no-init area and a small hot part (values of PLAT-12)
CAN_BITRATE = 500_000
UART_BAUDRATE = 115_200
DIAG_REQUEST_ID = 0x7E0             # ISO 15765-4 physical request/response pair of the first ECU, a common default
DIAG_RESPONSE_ID = 0x7E8
# Test pin candidates of the safety section: never a pin that can carry debug, reset, oscillator or boot functions
# (switching such a pin to a GPIO input would cut off the debugger or the crystal)
# Contract of a family port template (platforms/<family>/port) that nxconfig does not check yet: internal ADC
# channels the port reads by name (project name, internal channel of the derivative). s32k1xx: SafeSystem.cpp reads
# Ai_BANDGAP and Ai_ADCINTERNAL0 (reference and internal supply monitor); without them the port does not compile.
# Finding of TOOL-59: this belongs into the manifest of the port module and a check in checks.py (like
# diag.FAMILY_SCAN_OFFSET it is a family fact, kept here in one table until then).
FAMILY_PORT_ADC_INTERNAL = {"s32k1xx": (("BANDGAP", "BANDGAP"), ("ADCINTERNAL0", "INTERNAL0"))}
SPECIAL_PIN_RE = re.compile(r"JTAG|SWD|SWCLK|SWDIO|SWO|SYS_J|TRACE|RESET|NMI|EXTAL|XTAL|OSC|BOOT|TCK|TMS|TDI|TDO",
                            re.IGNORECASE)


class NewProjectError(Exception):
    """Invalid request (name, folder, board, source): nothing was written."""

    def __init__(self, problems: list[str]):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


# TOOL-110: the pins of the safety section are chosen by the user, never by the wizard (module docstring).
SAFETY_KEYS = ("safe_state_output", "locked_pin", "test_pin")
# What each key does at run time - one text for the commented section, the command line help and the Studio page.
SAFETY_EFFECTS = {
    "safe_state_output": "digital output of the board used as INDICATOR of the safe state: entering the safe state "
                         "disables the pin (it floats), leaving it switches it back to GPIO with the level the "
                         "application last wrote. Never an actuator (enable of a pump, motor, heater, relay): it "
                         "would float in the safe state and come back on when the safe state is left. Take an "
                         "output the application does not write: if an rte.yaml dio.out connection writes it, "
                         "nxconfig warns until safety.safe_state_output_shared_with_application gives the reason "
                         "(SYS-87, AoU-30); one a device driver owns is refused",
    "locked_pin": "digital output whose pin configuration is locked at start (PCR lock) and checked every safety "
                  "cycle; its level stays writable, but any reconfiguration is refused by the hardware and a "
                  "changed direction enters the safe state. Not the same output as safe_state_output",
    "test_pin": "pin that is UNCONNECTED on the circuit: configured as GPIO input with pull-down and checked every "
                "safety cycle (the console command `safety dst`/`est` switches it to enter/leave the safe state). "
                "A pin the board file does not describe is not necessarily unconnected - check the schematic",
}
# Planning mode of newboard.check_board only: derive a section so the board can be proven in a scratch project.
SAFETY_PROBE = "probe"


@dataclass(frozen=True)
class SafetyChoice:
    """The pins of the safety section as the user chose them (TOOL-110)."""

    safe_state_output: str
    locked_pin: str
    test_pin: str


@dataclass
class SafetyCandidates:
    """What the board offers for the safety section, and whether the family port needs the section at all."""

    board: str
    outputs: list[tuple[str, str]] = field(default_factory=list)    # (name, pin) of every digital output
    test_pins: list[str] = field(default_factory=list)              # GPIO pins the board file does not describe
    required: bool = False                                          # the family port reads the section
    port: str = ""                                                  # its manifest (for the texts), if required

    def problems(self, choice: SafetyChoice) -> list[str]:
        """Why `choice` cannot be written for this board (empty: it can)."""
        names = [name for name, _ in self.outputs]
        problems = []
        for key in ("safe_state_output", "locked_pin"):
            value = getattr(choice, key)
            if value not in names:
                problems.append(f"safety.{key}: '{value}' is no digital output of board {self.board} "
                                f"(outputs: {', '.join(names) or '-'})")
        if choice.safe_state_output == choice.locked_pin:
            problems.append(f"safety: safe_state_output and locked_pin are both {choice.locked_pin} - the safe state "
                            "switches the indicator's pin, which a locked pin refuses (bswgen collect_safe_io)")
        if choice.test_pin not in self.test_pins:
            problems.append(f"safety.test_pin: '{choice.test_pin}' is no free GPIO pin of board {self.board} (free: "
                            f"GPIO pins the board file does not describe, without debug/reset/oscillator/boot "
                            f"function: {', '.join(self.test_pins[:12]) or '-'}"
                            f"{' ...' if len(self.test_pins) > 12 else ''})")
        return problems


@dataclass(frozen=True)
class BoardInfo:
    name: str
    file: Path
    mcu: str
    description: str = ""
    # ARCH-58: the family of the board has a port template (platforms/<family>/port): the project can be built.
    # Until then the marker was the hand-written ecu/platforms/<board>/Options.cmake of the board; since the
    # feature switches are derived, that file is optional and says nothing about buildability any more.
    port: bool = False
    problem: str = ""                       # why the board cannot be read (empty: fine)

    @property
    def buildable(self) -> bool:
        return self.port and not self.problem


@dataclass(frozen=True)
class ProjectInfo:
    name: str
    file: Path
    board: str = ""
    description: str = ""


@dataclass
class PlannedFile:
    path: str                               # relative to the target folder, posix
    text: str | None = None                 # new or rewritten content
    source: Path | None = None              # copied unchanged
    note: str = ""


@dataclass
class Plan:
    """What create() will write. Nothing exists on disk yet."""

    repo: Path
    name: str
    target: Path
    template: str                           # empty | copy
    board: BoardInfo | None = None
    source: ProjectInfo | None = None
    files: list[PlannedFile] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)      # derived values, rewritten paths, limits
    mcudb: Path = nxcfg.MCUDB
    module_dirs: tuple = nxcfg.MODULE_DIRS
    # TOOL-126: what the check of the whole planned file set found BEFORE anything is written (empty project only).
    # `expected_errors` are the errors nxconfig will report right after create() - the Studio says them on the
    # summary page instead of finishing a project that turns out broken. `safety_required` is the manifest of the
    # family port when it reads the safety section and the plan has none: the wizard must not create that project.
    expected_errors: list[Message] = field(default_factory=list)
    safety_required: str = ""

    @property
    def project_file(self) -> Path:
        return self.target / PROJECT_FILE

    def tree(self) -> list[str]:
        return sorted(f.path for f in self.files)


@dataclass
class Result:
    project_file: Path
    messages: list[Message]
    plan: Plan

    @property
    def errors(self) -> list[Message]:
        return [m for m in self.messages if m.severity == "error"]


# ----------------------------------------------------------------------------------------------- lists


def _load_yaml(file: Path):
    try:
        return YAML(typ="safe").load(file.read_text(encoding="utf-8"))
    except (OSError, YAMLError):
        return None


def _tree_of(repo: Path, workspace=None) -> Path:
    """The neXtBSW tree behind `repo`: the pinned tree of a workspace (repo is then the workspace root), else repo."""
    return Path(workspace.tree) if workspace is not None else Path(repo)


def _mcudb_for(mcu: str, mcudb: Path, workspace=None) -> Path:
    """TOOL-216: the derivative database that describes `mcu` - a workspace's own (search.mcudb) before `mcudb`."""
    if workspace is not None and mcu:
        for folder in workspace.mcudb:
            if nxcfg.find_derivative(mcu, folder) is not None:
                return Path(folder)
    return Path(mcudb)


def _board_dirs(repo: Path, workspace=None) -> tuple[Path, ...]:
    """Folders boards are offered from: those of the workspace (search.boards, then the tree), else repo/boards."""
    return tuple(workspace.board_dirs()) if workspace is not None else (Path(repo) / "boards",)


def _project_dirs(repo: Path, workspace=None) -> tuple[Path, ...]:
    """Folders projects are offered from: projects/ of the workspace, then those of its tree; else repo/projects."""
    if workspace is None:
        return (Path(repo) / "projects",)
    return (Path(workspace.root) / "projects", Path(workspace.tree) / "projects")


def _modules_for(module_dirs, workspace=None) -> tuple:
    """TOOL-216: module folders for a scratch project of the planning, which has no nextbsw.yaml above it: the
    workspace's search.modules before the tree's."""
    return (tuple(workspace.modules) if workspace is not None else ()) + tuple(module_dirs)


def workspace_arguments(workspace) -> dict:
    """TOOL-216: the keyword arguments of plan()/plan_empty()/plan_copy()/safety_candidates()/list_boards() that make
    them work in the workspace `workspace` (workspace.Workspace): repo is the workspace root (projects go to
    <root>/projects, project.board stays relative below it), boards and projects are looked up in the workspace
    before its tree, derivative database and modules of the workspace's search paths are used for the planning (the
    tool runs as the pinned tree, so its own mcudb and module folders are the tree's)."""
    return {"repo": Path(workspace.root), "workspace": workspace}


def _buildable(mcu: str, repo: Path, mcudb: Path) -> bool:
    """ARCH-58: a board is buildable when the family of its derivative has a port template.

    The family is the folder of the derivative in `mcudb` - reading the derivative file itself for a list is not
    worth it, and a derivative that is in no family folder has no family port either.
    """
    if not mcu:
        return False
    from . import features
    family = next((f.parent.name for f in sorted(Path(mcudb).glob(f"*/{mcu}.yaml"))), "")
    return bool(family) and features.family_port_template(family, repo).is_file()


def list_boards(repo: Path = nxcfg.REPO, mcudb: Path = nxcfg.MCUDB, workspace=None) -> list[BoardInfo]:
    """Boards of the tree (boards/<board>/board.yaml), with the information whether the tree can build them.

    TOOL-216: with `workspace` (see workspace_arguments) the boards of its search.boards come first, then those of
    its tree; a board folder named in two places is listed once, from the first."""
    result = []
    seen = set()
    tree = _tree_of(repo, workspace)
    for file in [f for base in _board_dirs(repo, workspace) for f in sorted(Path(base).glob("*/board.yaml"))]:
        if workspace is not None:
            if file.parent.name in seen:
                continue
            seen.add(file.parent.name)
        data = _load_yaml(file)
        board = data.get("board") if isinstance(data, dict) else None
        if not isinstance(board, dict) or not board.get("name"):
            result.append(BoardInfo(file.parent.name, file, "", problem="board.yaml cannot be read"))
            continue
        name = str(board["name"])
        result.append(BoardInfo(name, file, str(board.get("mcu", "")), str(board.get("description") or ""),
                                _buildable(str(board.get("mcu", "")), tree,
                                           _mcudb_for(str(board.get("mcu", "")), mcudb, workspace))))
    return result


def list_projects(repo: Path = nxcfg.REPO, workspace=None) -> list[ProjectInfo]:
    """Projects of the tree (projects/<project>/project.yaml) as sources of a copy (TOOL-216: in a workspace those of
    the workspace first, then those of its tree)."""
    result = []
    for file in [f for base in _project_dirs(repo, workspace) for f in sorted(Path(base).glob("*/project.yaml"))]:
        data = _load_yaml(file)
        section = data.get("project") if isinstance(data, dict) else None
        if not isinstance(section, dict):
            continue
        board = Path(str(section.get("board", ""))).name
        result.append(ProjectInfo(str(section.get("name") or file.parent.name), file, board,
                                  str(section.get("description") or "")))
    return result


def _project_names(repo: Path, workspace=None) -> dict[str, Path]:
    return {p.name.lower(): p.file for p in reversed(list_projects(repo, workspace))}


# ----------------------------------------------------------------------------------------------- checks


def check_name(name: str, repo: Path = nxcfg.REPO, workspace=None) -> list[str]:
    """Problems of a project name (empty list: fine)."""
    problems = []
    if not NAME_RE.fullmatch(name or ""):
        problems.append(f"project name {name!r}: a letter first, then letters, digits or '_' (at most 48 characters; "
                        "C identifier and folder name)")
        return problems
    if name.lower() in C_KEYWORDS or keyword.iskeyword(name):
        problems.append(f"project name {name!r} is a reserved word")
    if name.lower() in WINDOWS_DEVICE_NAMES:
        problems.append(f"project name {name!r} is a reserved device name on Windows")
    other = _project_names(repo, workspace).get(name.lower())
    if other is not None:
        problems.append(f"project name {name!r} is already used by {other} (the build directory "
                        "<build root>/<name> would be shared)")
    return problems


def default_target(name: str, repo: Path = nxcfg.REPO) -> Path:
    return Path(repo) / "projects" / name


def check_target(target: Path, repo: Path = nxcfg.REPO) -> list[str]:
    """Problems of the target folder: must be new or empty, not inside build output or the OpenBSW submodule."""
    target = Path(target).absolute()
    problems = []
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        problems.append(f"folder {target} exists and is not empty")
    repo = Path(repo).resolve()
    for part in ("build", "external", ".git", "boards", "mcudb"):
        if _is_below(target, repo / part):
            problems.append(f"folder {target} lies in {part}/ of the repository - choose projects/<name> or a folder "
                            "outside of it")
    return problems


def _is_below(path: Path, directory: Path) -> bool:
    try:
        Path(os.path.abspath(path)).relative_to(os.path.abspath(directory))
        return True
    except ValueError:
        return False


is_below = _is_below


# RTE-22: from this format of the ECU description its paths are relative to the rte.yaml, not to the repository
_FILE_RELATIVE_PATHS = 2


def _schema_version(document) -> int:
    """Format an ECU description declares; a document without the key is format 1 (paths relative to the repo)."""
    value = document.get("schema_version") if isinstance(document, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else 1


def _rel(path: Path, start: Path) -> str:
    """Relative posix path, absolute if there is none (another drive)."""
    try:
        return Path(os.path.relpath(os.path.abspath(path), os.path.abspath(start))).as_posix()
    except ValueError:
        return Path(os.path.abspath(path)).as_posix()


def _board_ref(board_dir: Path, target: Path, repo: Path) -> str:
    """project.board: relative inside the repository (projects/<p> -> ../../boards/<b>), absolute for a project
    outside of it (a relative path through the whole file system breaks as soon as either moves)."""
    if _is_below(target, repo) and _is_below(board_dir, repo):
        return _rel(board_dir, target)
    return Path(os.path.abspath(board_dir)).as_posix()


def _repo_rel(path: Path, repo: Path) -> str:
    """Path relative to the repository root, the way comments and build hints name a project folder."""
    return _rel(path, repo)


def _validate(name: str, target: Path, repo: Path, workspace=None) -> None:
    problems = check_name(name, repo, workspace) + check_target(target, repo)
    if problems:
        raise NewProjectError(problems)


def _find_board(board: str | Path | BoardInfo, repo: Path, mcudb: Path = nxcfg.MCUDB, workspace=None) -> BoardInfo:
    if isinstance(board, BoardInfo):
        return board
    boards = list_boards(repo, mcudb, workspace)
    text = str(board)
    for info in boards:
        if text in (info.name, str(info.file), str(info.file.parent)) or \
                Path(text).resolve() in (info.file.resolve(), info.file.parent.resolve()):
            return info
    where = ", ".join(str(d) for d in _board_dirs(repo, workspace))
    raise NewProjectError([f"board {text} not found in {where} "
                           f"(available: {', '.join(b.name for b in boards) or '-'})"])


def _find_project(source: str | Path | ProjectInfo, repo: Path, workspace=None) -> ProjectInfo:
    if isinstance(source, ProjectInfo):
        return source
    text = str(source)
    for info in list_projects(repo, workspace):
        if text == info.name or Path(text).resolve() in (info.file.resolve(), info.file.parent.resolve()):
            return info
    path = Path(text)
    file = path / PROJECT_FILE if path.is_dir() else path
    if file.is_file():
        data = _load_yaml(file)
        section = data.get("project") if isinstance(data, dict) else None
        if isinstance(section, dict):
            return ProjectInfo(str(section.get("name") or file.parent.name), file.resolve(),
                               Path(str(section.get("board", ""))).name, str(section.get("description") or ""))
    raise NewProjectError([f"project {text} not found (a folder with {PROJECT_FILE} or the name of a project in "
                           f"{', '.join(str(d) for d in _project_dirs(repo, workspace))})"])


# ----------------------------------------------------------------------------------------------- empty project


def _hex(value: int) -> str:
    return f"0x{value:X}"


def _quote(text: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./\-][A-Za-z0-9_./\- ]*", text) and not text.endswith(" ") \
            and text.lower() not in ("yes", "no", "true", "false", "null", "on", "off", "~"):
        return text
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _clock_candidates(derivative_file: Path) -> list[int]:
    data = _load_yaml(derivative_file) or {}
    clock = data.get("clock") if isinstance(data, dict) else None
    clock = clock if isinstance(clock, dict) else {}
    max_hz = (clock.get("max_hz") or {}).get("core") if isinstance(clock.get("max_hz"), dict) else None
    internal = [v for v in (clock.get("internal") or {}).values() if isinstance(v, int) and v >= 1_000_000] \
        if isinstance(clock.get("internal"), dict) else []
    if isinstance(max_hz, int) and max_hz > 0:
        values = [max_hz] + [c for c in COMMON_CLOCKS_HZ if c < max_hz]
    else:
        # no clock limits in the derivative: the fastest internal oscillator is reachable without PLL
        values = sorted(internal, reverse=True) + [c for c in COMMON_CLOCKS_HZ if c <= 48_000_000]
    values += sorted(internal, reverse=True)
    return list(dict.fromkeys(values))


def _check_text(folder: Path, files: dict[str, str], mcudb: Path, module_dirs,
                port_requirements: bool = False) -> tuple:
    """PLAT-95/TOOL-59: the probes on the way to a project are incomplete on purpose - the requirements of
    the family port are checked once at the end, on the whole file set, not on every intermediate step.
    Without that, a probe with nothing but the clock reports the sections the wizard is about to write and
    hides the finding the probe was looking for."""
    folder.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        (folder / rel).write_text(text, encoding="utf-8", newline="\n")
    return nxcfg.check(folder / PROJECT_FILE, mcudb, module_dirs,
                       port_requirements=port_requirements)


def _board_pins(tree) -> set[str]:
    """Every `pin:` value of the board file (buses, DIO, ADC, PWM, capture ...)."""
    pins = set()

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "pin" and isinstance(value, str):
                    pins.add(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
    walk(tree)
    return pins


def free_test_pins(signals: dict, taken: set[str]) -> list[str]:
    """GPIO pins of the package that the board does not describe and that carry no debug, reset, oscillator or
    boot function (SPECIAL_PIN_RE), in the order of the pin table. Candidates only (TOOL-110): whether a pin is
    unconnected on the circuit is not in any file."""
    special = {pin for signal, pins in signals.items() if SPECIAL_PIN_RE.search(signal) for pin in pins}
    return [p for p in (signals.get("GPIO") or {}) if p not in taken and p not in special]


def free_test_pin(signals: dict, taken: set[str]) -> str | None:
    """First of free_test_pins(); None if there is none."""
    return next(iter(free_test_pins(signals, taken)), None)


def _port_needs_safety(cfg, info: BoardInfo) -> str:
    """The manifest of the family port if it reads the safety section and the board is built with it; else ''."""
    if not info.port:
        return ""
    try:
        from . import checks
        file = checks.port_manifest_file(cfg.derivative.derivative.family)
        requirements, _messages = checks.read_port_manifest(file, cfg.derivative.derivative.family)
    except Exception:                               # noqa: BLE001 - unreadable manifest: nxconfig reports it
        return ""
    if requirements is None:
        return ""
    if any(entry.get("key") == "safety" for entry in requirements.entries("project")):
        return requirements.where
    return ""


def _safety_candidates(cfg, info: BoardInfo) -> SafetyCandidates:
    mux = None
    try:
        from . import checks
        mux = checks.pinmux(cfg)
    except Exception:                               # noqa: BLE001 - no pin table: no test pin candidates
        mux = None
    port = _port_needs_safety(cfg, info)
    return SafetyCandidates(
        info.name, [(o.name, o.pin) for o in cfg.board.dio.outputs],
        free_test_pins(mux.signals if mux else {}, _board_pins(nxcfg._plain(cfg.sources["board"].tree))),
        bool(port), port)


def _ram_chain(regions: list) -> list:
    """Longest contiguous chain of RAM regions (address order) that does not start with a region smaller than the
    hot part of the linker template; ties: the first one."""
    regions = sorted(regions, key=lambda r: r.start)
    chains, chain = [], []
    for r in regions:
        if chain and r.start == chain[-1].start + chain[-1].size:
            chain.append(r)
        else:
            if chain:
                chains.append(chain)
            chain = [r]
    if chain:
        chains.append(chain)
    best = []
    for c in chains:
        while len(c) > 1 and c[0].size < HOT_RAM_MIN:
            c = c[1:]
        if sum(r.size for r in c) > sum(r.size for r in best):
            best = c
    return best


def _memory_section(cfg, notes: list[str], calibration: bool = False) -> str:
    d = cfg.derivative
    flash = next((r for r in d.memory if r.kind == "code_flash"), None)
    if flash is None:
        notes.append(f"no memory partitioning: {d.derivative.name} describes no code_flash region")
        return ""
    table = flash.sector_table()
    sizes = sorted({size for _, size in table})
    if len(sizes) != 1 or flash.program_size is None:
        why = "sectors of different sizes (PLAT-16)" if len(sizes) != 1 else "no program_size"
        notes.append(f"no memory partitioning: {flash.name} of {d.derivative.name} has {why}; without it neither "
                     "bootloader nor application image can be linked")
        return ""
    sector = sizes[0]
    want = BOOTLOADER_FLASH if flash.size >= 0x40000 else max(flash.size // 8, BOOTLOADER_FLASH_MIN)
    bl_size = -(-want // sector) * sector
    chain = _ram_chain([r for r in d.memory if r.kind == "ram"])
    if not chain:
        notes.append(f"no memory partitioning: {d.derivative.name} describes no RAM region")
        return ""
    total = sum(r.size for r in chain)
    noinit = 0x400 if total >= SMALL_RAM else 0x200
    first = chain[0]
    room = first.size - (noinit if len(chain) == 1 else 0)
    bl_ram = min(BOOTLOADER_RAM, room) // 0x100 * 0x100
    stack = 0x1000 if bl_ram >= 0x4000 else 0x800
    ram = ", ".join(r.name for r in chain)
    small = ""
    if total < SMALL_RAM:
        small = ", ramfunctions_size: 0x400, hot_ram_size: 0x600"
    notes.append(f"memory: bootloader {_hex(bl_size)} of {flash.name} (sector {_hex(sector)}), bootloader RAM "
                 f"{_hex(bl_ram)} of {first.name}, application RAM {ram} ({_hex(total)} bytes)")
    cal = ""
    if calibration:
        # TOOL-131: one sector behind the first application sector (memory.py places it); refuse is what a safety
        # relevant parameter requires later on, development keeps the XCP working page
        cal = (f"  # Calibration segment (SYS-89): one sector for the header and the values; grow it with the "
               "parameters.\n"
               f"  calibration: {{ region: {flash.name}, size: {_hex(sector)}, on_invalid: refuse, "
               "profile: development }\n")
        notes.append(f"calibration segment: {_hex(sector)} (one sector) of {flash.name}, on_invalid refuse, profile "
                     "development - the size has to hold 32 bytes of header plus all calibration parameters")
    return (
        "# Memory partitioning (projects/README.md, Speicheraufteilung): derived from the derivative by the wizard -\n"
        "# the bootloader flash has to hold the bootloader image of this core, check the size after the first build.\n"
        "memory:\n"
        f"  flash: {flash.name}\n"
        f"  bootloader: {{ size: {_hex(bl_size)}, ram: {first.name}, ram_size: {_hex(bl_ram)}, "
        f"stack_size: {_hex(stack)} }}\n"
        f"  application: {{ ram: [{ram}], noinit_size: {_hex(noinit)}{small} }}\n" + cal + "\n")


def _wrap_comment(text: str, indent: str = "#   ", width: int = 116) -> list[str]:
    lines, line = [], indent
    for word in text.split():
        if len(line) + len(word) + 1 > width and line.strip("# "):
            lines.append(line.rstrip() + "\n")
            line = indent
        line += word + " "
    if line.strip("# "):
        lines.append(line.rstrip() + "\n")
    return lines


def _safety_note(offer: SafetyCandidates) -> str:
    """Note of a plan without safety section (TOOL-110): not chosen, and what that means for the build."""
    text = ("safety pins NOT chosen - the section stands commented out in project.yaml with what each key does "
            "(choose them: Studio page 'Sicherheit', or nxconfig new --safe-state-output/--locked-pin/--test-pin)")
    if offer.required:
        text += (f"; the family port needs the section ({offer.port}): nxconfig reports an error at project.yaml "
                 "safety and the project does not build until it is set")
    if len(offer.outputs) < 2:
        text += (f"; board {offer.board} has {len(offer.outputs)} digital output(s) - safe_state_output and "
                 "locked_pin need two different ones")
    if not offer.test_pins:
        text += f"; the package of board {offer.board} has no free GPIO pin for test_pin"
    return text


def _safety_section(offer: SafetyCandidates, chosen: SafetyChoice | None, probe: bool = False) -> str:
    """The safety section of project.yaml: the user's choice, or - not chosen - commented out with the candidates
    and the run-time effect of each key, so the file itself says why it is empty (TOOL-110)."""
    lines = ["# Safety section (platforms/<family>/port/safety, SafeIo and SafeState). What each pin does at run "
             "time:\n"]
    for key in SAFETY_KEYS:
        lines += _wrap_comment(f"{key}: {SAFETY_EFFECTS[key]}.")
    if chosen is not None:
        who = ("derived for the board check only (scratch project)" if probe
               else "chosen by the user when the project was created, not by the wizard")
        return ("".join(lines) + f"# {who}.\n"
                f"safety:\n  safe_state_output: {chosen.safe_state_output}\n  locked_pin: {chosen.locked_pin}\n"
                f"  test_pin: {chosen.test_pin}\n")
    lines.append("# NOT chosen (TOOL-110): which output is an indicator and which pin is unconnected is a fact of "
                 "the circuit,\n# not of the board file, so the wizard does not pick them."
                 + (f" The family port needs this section\n# ({offer.port}): until it is set, nxconfig reports an "
                    "error here and the project does not build." if offer.required else "") + "\n")
    lines += _wrap_comment("Digital outputs of the board: " + (", ".join(f"{n} ({p})" for n, p in offer.outputs)
                                                               or "none") + ".", indent="# ")
    free = offer.test_pins[:16]
    more = len(offer.test_pins) - len(free)
    lines += _wrap_comment("GPIO pins the board file does not describe (test_pin candidates, check the schematic): "
                           + (", ".join(free) if free else "none") + (f" and {more} more" if more > 0 else "")
                           + ".", indent="# ")
    lines.append("# Add both outputs to use.dio as well.\n"
                 "# safety:\n#   safe_state_output: <indicator output>\n#   locked_pin: <another output>\n"
                 "#   test_pin: <unconnected pin>\n")
    return "".join(lines)


def _derivative_file(board: BoardInfo, mcudb: Path) -> Path:
    derivative_file = nxcfg.find_derivative(board.mcu, mcudb)
    if derivative_file is None:
        raise NewProjectError([f"board {board.name}: no derivative description {board.mcu}.yaml in {mcudb}"])
    return derivative_file


def _probe_clock(name: str, board: BoardInfo, derivative_file: Path, folder: Path, mcudb: Path, module_dirs):
    """Step 1 of an empty project: the highest core clock the derivative allows that the family clock model can
    reach with this board. Returns (config of the probe, clock); raises NewProjectError."""
    board_abs = board.file.parent.resolve().as_posix()
    cfg, last = None, []
    for hz in _clock_candidates(derivative_file):
        head = (f"schema_version: 1\nproject:\n  name: {name}\n  board: {board_abs}\n"
                f"clock:\n  system_hz: {hz}\n")
        cfg, last = _check_text(folder, {PROJECT_FILE: head}, mcudb, module_dirs)
        if cfg is not None:
            return cfg, hz
        if any(m.path[:1] != ("clock",) for m in last if m.severity == "error"):
            break                               # board or derivative problem: no clock helps
    raise NewProjectError([f"board {board.name}: no core clock passes the checks"]
                          + [str(m) for m in last if m.severity == "error"])


def safety_candidates(board, repo: Path = nxcfg.REPO, mcudb: Path = nxcfg.MCUDB,
                      module_dirs=nxcfg.MODULE_DIRS, workspace=None) -> SafetyCandidates:
    """TOOL-110: what `board` (name, folder or BoardInfo) offers for the safety section - every digital output
    and every free GPIO pin - and whether its family port needs the section. For the Studio page "Sicherheit"
    and the command line; nothing is chosen here. Raises NewProjectError."""
    info = _find_board(board, Path(repo).resolve(), Path(mcudb), workspace)
    if info.problem:
        raise NewProjectError([f"board {info.name}: {info.problem}"])
    mcudb = _mcudb_for(info.mcu, mcudb, workspace)
    module_dirs = _modules_for(module_dirs, workspace)
    scratch = Path(tempfile.mkdtemp(prefix="nxsafety-"))
    try:
        cfg, _hz = _probe_clock("safety", info, _derivative_file(info, Path(mcudb)), scratch / "clock",
                                Path(mcudb), tuple(module_dirs))
        return _safety_candidates(cfg, info)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def standard_diag_template(repo: Path = nxcfg.REPO) -> Path:
    """TOOL-131: the standard diagnostic description of the tree the project is created in, else of the running
    tree (a customer workspace pins its neXtBSW; a test tree may have none)."""
    for tree in (Path(repo), nxcfg.REPO):
        file = tree / STANDARD_DIAG
        if file.is_file():
            return file
    raise NewProjectError([f"no standard diagnostic description {STANDARD_DIAG.as_posix()} in {Path(repo)} or "
                           f"{nxcfg.REPO}"])


def standard_diag(name: str, features=(), repo: Path = nxcfg.REPO, uid_bytes: int | None = None,
                  nvm_blocks: dict | None = None, can_buses=()) -> str:
    """TOOL-131: diag.yaml of a new project from the standard description: the lines between `# [if <feature>]`
    and `# [end <feature>]` stay only for the given features, the template notes become the project header.
    `uid_bytes`: size of the chip UID of the derivative (mcudb uid.size) - the length of the element with source
    chip.uid (0xF18C) is a derivative fact (S32K1 16, STM32 12, RM4x 8 bytes); None keeps the template's.

    `nvm_blocks` (NVM-16/NVM-20): NvM blocks already defined for the project (project.yaml `nvm.blocks`) as
    `{name: asil}` - each gets its `dtcs[]` entries (bswdtc.nvm_dtc_yaml, bsw_instance from bswdtc.block_index over
    this same mapping) inserted before `dtc_groups` right away, the same entries the Studio dialog "Neuer
    NvM-Block" offers to add to an existing diag.yaml. An empty project defines no NvM block yet (they are usually
    added afterwards, on the page "Projekt-Konfiguration"), so this is normally empty here; a caller that already
    knows some - a project template, a future wizard step - does not have to add them again by hand.

    `can_buses` (COM-46): board names of the CAN buses the project uses, in the order of their bus ids (board order
    of the used buses, bswgen NX_CAN_BUSES) - each gets the bus-off DTC of the `# [each can_bus]` block with
    bsw_instance = its position; none given: no bus-off DTC."""
    features = set(features)
    can_buses = list(can_buses)
    bad = [b for b in can_buses if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", str(b))]
    if bad or len(set(can_buses)) != len(can_buses) or len(can_buses) > 0x100:
        raise NewProjectError([f"CAN buses {', '.join(map(str, can_buses))}: distinct board names (letter, then "
                               "letters, digits, _), at most 256"])
    unknown = features - set(STANDARD_DIAG_FEATURES)
    if unknown:
        raise NewProjectError([f"unknown diagnostic feature(s) {', '.join(sorted(unknown))} (known: "
                               f"{', '.join(STANDARD_DIAG_FEATURES)})"])
    template = standard_diag_template(repo)
    lines = template.read_text(encoding="utf-8").splitlines(keepends=True)
    marker = next((i for i, line in enumerate(lines) if line.rstrip() == STANDARD_DIAG_NOTES_END), None)
    if marker is None:
        raise NewProjectError([f"{template}: the line {STANDARD_DIAG_NOTES_END!r} is missing"])
    out, open_ = [], []
    each: list[str] | None = None                      # COM-46: lines of an open `# [each can_bus]` block
    for number, line in enumerate(lines[marker + 1:], marker + 2):
        found = re.fullmatch(r"# \[(if|end|each) ([a-z_]+)\]\s*", line)
        if found:
            kind, feature = found.groups()
            if kind == "each" or (kind == "end" and feature in STANDARD_DIAG_EACH):
                if feature not in STANDARD_DIAG_EACH:
                    raise NewProjectError([f"{template}:{number}: unknown block {feature!r}"])
                if kind == "each":
                    if each is not None:
                        raise NewProjectError([f"{template}:{number}: [each {feature}] inside another [each]"])
                    each = []
                    continue
                if each is None:
                    raise NewProjectError([f"{template}:{number}: [end {feature}] without [each {feature}]"])
                if all(f in features for f in open_):
                    out += _each_can_bus(each, can_buses)
                each = None
                continue
            if feature not in STANDARD_DIAG_FEATURES:
                raise NewProjectError([f"{template}:{number}: unknown feature {feature!r}"])
            if each is not None:
                raise NewProjectError([f"{template}:{number}: [{kind} {feature}] inside an [each] block"])
            if kind == "if":
                open_.append(feature)
            elif not open_ or open_.pop() != feature:
                raise NewProjectError([f"{template}:{number}: [end {feature}] without [if {feature}]"])
            continue
        if each is not None:
            each.append(line)
        elif all(f in features for f in open_):
            out.append(line)
    if open_:
        raise NewProjectError([f"{template}: [if {open_[-1]}] is never closed"])
    if each is not None:
        raise NewProjectError([f"{template}: [each] is never closed"])
    if uid_bytes is not None:
        uid = re.compile(r"length_bytes: \d+(?=, source: \{bsw: chip\.uid\})")
        if sum(len(uid.findall(line)) for line in out) != 1:
            raise NewProjectError([f"{template}: no single element `length_bytes: <n>, source: {{bsw: chip.uid}}`"])
        out = [uid.sub(f"length_bytes: {uid_bytes}", line) for line in out]
    chosen = [f for f in STANDARD_DIAG_FEATURES if f in features]
    head = (f"# Diagnostic description (ARCH-51) of project {name}: the standard diagnostics of neXtBSW (TOOL-131),\n"
            f"# written by the new-project wizard from {STANDARD_DIAG.as_posix()} "
            f"({', '.join(chosen) if chosen else 'no optional part'}).\n"
            "# The project owns this file: add its own identifiers, routines and DTCs. bsw_dtc_range (default "
            f"0x{NXBSW_DTC_FIRST:06X}..0x{NXBSW_DTC_LAST:06X})\n"
            "# is the neXtBSW range: the BSW DTCs derive their numbers from it (plan in tools/nxconfig/bswdtc.py) - "
            "number project DTCs outside it.\n"
            "# Schema: tools/nxconfig/model.py (DiagDescription). Services 10/3E/11/28 are served by the UDS stack.\n")
    text = head + "".join(out)
    if nvm_blocks:
        text = _insert_nvm_dtcs(text, nvm_blocks, template)
    return text


def _each_can_bus(block: list[str], can_buses: list[str]) -> list[str]:
    """COM-46: the `# [each can_bus]` block once per bus - the example bus of the template becomes the bus, its
    `bsw_instance: 0` the index of the bus (bswdtc numbering plan +0x03nn)."""
    # the example bus as a word of its own, or glued to the DTC name CanBusOff<BUS>
    example = re.compile(rf"(?:(?<![A-Za-z0-9_])|(?<=CanBusOff)){STANDARD_DIAG_EXAMPLE_BUS}(?![A-Za-z0-9_])")
    instance = re.compile(r"^(\s*bsw_instance: )0(\s*)$")
    out = []
    for index, bus in enumerate(can_buses):
        for line in block:
            line = example.sub(lambda _m, bus=bus: bus, line)
            out.append(instance.sub(lambda m, index=index: f"{m.group(1)}{index}{m.group(2)}", line))
    return out


def _insert_nvm_dtcs(text: str, nvm_blocks: dict, template: Path) -> str:
    """`standard_diag()`: splices the `dtcs[]` entries of `nvm_blocks` (NVM-20) into `text` right before
    `dtc_groups:` (and its explaining comment, which stays attached to it) - the same spot the template's own
    header tells a caller who adds them by hand (STANDARD_DIAG header, "NvM block DTCs")."""
    names = list(nvm_blocks)
    pieces = [bswdtc.nvm_dtc_yaml(block, bswdtc.block_index(block, names), bool(asil))
             for block, asil in nvm_blocks.items()]
    lines = text.splitlines(keepends=True)
    marker = next((i for i, line in enumerate(lines) if line.rstrip() == "dtc_groups:"), None)
    if marker is None:
        raise NewProjectError([f"{template}: no dtc_groups: to insert the NvM block DTCs before"])
    start = marker
    while start > 0 and lines[start - 1].lstrip().startswith("#"):
        start -= 1
    while start > 0 and lines[start - 1].strip() == "":
        start -= 1
    insertion = ("\n  # NVM-16/NVM-20: defects of the project's own NvM blocks (tools/nxconfig/bswdtc.nvm_dtc_yaml).\n"
                + "\n".join(pieces) + "\n")
    return "".join(lines[:start]) + insertion + "".join(lines[start:])


def supply_providers(cfg, use: dict) -> list[str]:
    """TOOL-131: board devices the selection `use` brings into the build whose module provides the role
    power_supply (module.yaml `provides`) - only they publish a measured supply voltage on bsw.power.*.

    A device is used as nxconfig's integration uses it: on a used SPI bus, or as transceiver of a used CAN bus. Its
    module is the one the board names, else the manifest whose `parts` match - the role comes from the manifest,
    never from a part name. Works on the probe configuration, before the project checks clean (a new project
    usually still lacks its safety pins, and nxconfig.check() gives no configuration then)."""
    manifests = cfg.modules.manifests
    spi, can = set(use.get("spi", [])), set(use.get("can", []))
    used = ({b.device for b in cfg.board.spi if b.name in spi and b.device}
            | {b.phy for b in cfg.board.can if b.name in can and b.phy})
    found = []
    for dev in cfg.board.devices:
        if dev.name not in used:
            continue
        module = dev.module or next((n for n, m in manifests.items()
                                     if any(fnmatchcase(dev.part, p) for p in m.parts)), None)
        if module in manifests and POWER_SUPPLY_ROLE in manifests[module].provides:
            found.append(dev.name)
    return found


def _empty_files(name: str, target: Path, board: BoardInfo, repo: Path, with_diag: bool, description: str,
                 mcudb: Path, module_dirs, notes: list[str], signals: str = "minimal",
                 safety: "SafetyChoice | str | None" = None, report: dict | None = None,
                 calibration: bool = False) -> dict[str, str]:
    """project.yaml, rte.yaml (and diag.yaml) of an empty project; values checked in a scratch folder.

    `signals` (TOOL-106): "minimal" or "all" - see SIGNAL_CHOICES. `safety` (TOOL-110): None, the user's
    SafetyChoice, or SAFETY_PROBE for the board check - see the module docstring. `report` (TOOL-126), if given,
    receives "errors" (errors of the final check of the whole file set) and "safety_required" (the family port's
    manifest if it needs the section and none was chosen, else "")."""
    if signals not in SIGNAL_CHOICES:
        raise NewProjectError([f"unknown signal choice {signals!r} (one of {', '.join(SIGNAL_CHOICES)})"])
    if not (safety is None or safety == SAFETY_PROBE or isinstance(safety, SafetyChoice)):
        raise NewProjectError([f"unknown safety choice {safety!r} (None or a SafetyChoice)"])
    derivative_file = _derivative_file(board, mcudb)
    scratch = Path(tempfile.mkdtemp(prefix="nxnew-"))
    try:
        board_abs = board.file.parent.resolve().as_posix()
        cfg, clock_hz = _probe_clock(name, board, derivative_file, scratch / "clock", mcudb, module_dirs)
        candidates = _clock_candidates(derivative_file)
        candidates = candidates[candidates.index(clock_hz):]

        b = cfg.board
        sections = []
        # 2. communication: the first CAN bus (bootloader, UDS) and the first UART (console)
        can = b.can[0] if b.can else None
        uart = b.uart[0] if b.uart else None
        spi = next((s for s in b.spi if can is not None and s.device and s.device == can.phy), None)
        use = {}
        if can is not None:
            use["can"] = [can.name]
        if uart is not None:
            use["uart"] = [uart.name]
        if spi is not None:
            use["spi"] = [spi.name]
            notes.append(f"SPI bus {spi.name} is used: the transceiver {can.phy} of {can.name} sits on it")
        outputs = [o.name for o in b.dio.outputs]
        # TOOL-110: the safety section is the user's choice (module docstring); only the board check probes one
        offer = _safety_candidates(cfg, board)
        chosen = None
        if isinstance(safety, SafetyChoice):
            problems = offer.problems(safety)
            if problems:
                raise NewProjectError(problems)
            chosen = safety
            notes.append(f"safety section as chosen: safe_state_output {chosen.safe_state_output}, locked_pin "
                         f"{chosen.locked_pin}, test_pin {chosen.test_pin} - check on the schematic that the "
                         f"indicator drives no actuator and that {chosen.test_pin} is unconnected. Leave "
                         f"{chosen.safe_state_output} to the safe state: once a component writes it (rte.yaml "
                         f"dio.out.{chosen.safe_state_output}), nxconfig warns at safety.safe_state_output until "
                         "safety.safe_state_output_shared_with_application: \"<reason>\" confirms it (SYS-87)")
        elif safety == SAFETY_PROBE and len(offer.outputs) > 1 and offer.test_pins:
            chosen = SafetyChoice(offer.outputs[0][0], offer.outputs[1][0], offer.test_pins[0])
        if chosen is not None:
            use["dio"] = [chosen.safe_state_output, chosen.locked_pin]
        else:
            notes.append(_safety_note(offer))
        if report is not None:
            report["safety_required"] = offer.port if offer.required and chosen is None else ""
        # minimal: no digital input - bswgen generates a correct configuration without one (PLAT-94), and the test
        # pin of the safety section is a pin of its own (safety.test_pin), not a board input.
        # TOOL-106: "all" switches on every signal of the board; either way the notes name what stays off.
        buses = {"can": [can] if can is not None else [], "uart": [uart] if uart is not None else []}
        if signals == "all":
            for kind in USE_KINDS:
                listed = [e.name for e in getattr(b, kind)]
                if listed:
                    use[kind] = listed
            dio_names = list(dict.fromkeys(list(use.get("dio", [])) + outputs + [i.name for i in b.dio.inputs]))
            if dio_names:
                use["dio"] = dio_names
            use = {k: use[k] for k in (*USE_KINDS, "dio") if k in use}      # the order of the schema
            buses = {"can": list(b.can), "uart": list(b.uart)}
        left_out = [f"{kind} {', '.join(missing)}" for kind, missing in (
            (kind, [e.name for e in getattr(b, kind) if e.name not in use.get(kind, [])]) for kind in USE_KINDS)
            if missing]
        dio_off = [e.name for e in (*b.dio.outputs, *b.dio.inputs) if e.name not in use.get("dio", [])]
        if dio_off:
            left_out.append(f"dio {', '.join(dio_off)}")
        if left_out:
            notes.append("not used by this project (use): " + "; ".join(left_out) + " - switch them on in use.<kind> "
                         "(Studio: page Projekt-Konfiguration, button 'Eintrag hinzufuegen')")
        if b.pwm:
            notes.append(f"PWM group(s) {', '.join(g.name for g in b.pwm)} not used: pwm.<group> needs frequency_hz and "
                         "deadtime_ns, a decision of the project (a guessed dead time can destroy a half bridge)")

        def head(hz: int, board_ref: str) -> list[str]:
            return [f"# Project {name}: created empty for board {board.name} by the new-project wizard (TOOL-59).\n"
                    "# Schema: tools/nxconfig/model.py (Project); levels: doc/architecture/README.md section 4.\n"
                    "# Every value below was derived from the board and its derivative and checked by nxconfig; "
                    "adapt\n# them to the ECU (projects/README.md).\n"
                    "schema_version: 1\n\n"
                    "project:\n"
                    f"  name: {name}\n"
                    f"  board: {_quote(board_ref)}\n"
                    f"  rte: {RTE_FILE}\n"
                    + (f"  diag: {DIAG_FILE}\n" if with_diag else "")
                    + f"  description: {_quote(description or f'Empty project on {board.name}')}\n\n"
                    "clock:\n"
                    f"  system_hz: {hz}\n\n"]
        memory = _memory_section(cfg, notes, calibration) if can is not None else ""
        if can is None:
            notes.append("no CAN bus on the board: no bootloader, no memory partitioning, no diagnostic addresses")
        if calibration and "calibration:" not in memory:
            notes.append("no calibration segment: the project has no memory partitioning (memory.calibration lies "
                         "in the code flash of the application)")
        if memory:
            sections.append(memory)
            sections.append(
                "# Bootloader and diagnostic addresses (COM-11): the first CAN bus of the board.\n"
                f"bootloader:\n  can: {can.name}\n\n"
                f"diagnostics:\n  request_id: {_hex(DIAG_REQUEST_ID)}\n  response_id: {_hex(DIAG_RESPONSE_ID)}\n\n")
        if use:
            sections.append("use:\n" + "".join(f"  {k}: [{', '.join(v)}]\n" for k, v in use.items()) + "\n")
        if buses["can"]:
            sections.append("can:\n" + "".join(f"  {bus.name}: {{ bitrate: {CAN_BITRATE} }}\n"
                                               for bus in buses["can"]) + "\n")
        if buses["uart"]:
            sections.append("uart:\n" + "".join(f"  {bus.name}: {{ baudrate: {UART_BAUDRATE} }}\n"
                                                for bus in buses["uart"]) + "\n")
        ranks = []
        priorities = cfg.derivative.derivative.nvic_priority_bits is not None or cfg.derivative.vim is not None
        if not priorities:
            notes.append(f"no interrupts section: {cfg.derivative.derivative.name} describes no interrupt priorities "
                         "(nvic_priority_bits or vim)")
        else:
            for bus in buses["can"]:
                ranks.append(f"    - {{ source: can, name: {bus.name}, rank: {len(ranks)} }}\n")
            for bus in buses["uart"]:
                ranks.append(f"    - {{ source: uart, name: {bus.name}, rank: {len(ranks)} }}\n")
        if ranks and priorities:
            sections.append("# Interrupt priorities as ranks (PLAT-21): CAN before the console.\n"
                            "interrupts:\n  ranks:\n" + "".join(ranks) + "\n")
        internal = []
        family_adc = FAMILY_PORT_ADC_INTERNAL.get(cfg.derivative.derivative.family, ())
        if family_adc and b.board.adc_reference_mv is None:
            notes.append(f"no adc_internal: board {b.board.name} gives no board.adc_reference_mv, but the family port "
                         f"{cfg.derivative.derivative.family} reads internal ADC channels (it will not compile)")
            family_adc = ()
        for label, channel in family_adc:
            instance = next((n for n, x in cfg.derivative.instances.items()
                             if x.class_ == "adc" and channel in (x.internal_channels or [])), None)
            if instance is None:
                notes.append(f"family port {cfg.derivative.derivative.family} reads the internal ADC channel {label}, "
                             f"but {cfg.derivative.derivative.name} has no ADC with {channel}")
            else:
                internal.append(f"  - {{ name: {label}, instance: {instance}, channel: {channel} }}\n")
        if internal:
            sections.append(f"# Internal ADC channels the family port {cfg.derivative.derivative.family} reads "
                            "(reference and supply monitor of the safety task).\n"
                            "adc_internal:\n" + "".join(internal) + "\n")
        sections.append(_safety_section(offer, chosen, safety == SAFETY_PROBE))

        def project_text(hz: int, board_ref: str) -> str:
            return "".join(head(hz, board_ref) + sections).rstrip("\n") + "\n"

        files = {RTE_FILE: _empty_rte(name, repo)}
        # TOOL-131: the standard diagnostics need the diagnostic connection (the first CAN bus, `diagnostics`)
        standard = with_diag and bool(memory)
        diag_features = {"calibration"} if "calibration:" in memory else set()
        supply = supply_providers(cfg, use) if standard else []
        if supply:
            diag_features.add("supply")
        uid = getattr(cfg.derivative, "uid", None)
        uid_bytes = uid.size if uid is not None else None
        if standard and uid_bytes is None:
            notes.append(f"{cfg.derivative.derivative.name} states no chip UID (mcudb uid): 0xF18C and the security "
                         "access of the application need one, nxconfig reports it")
        if standard:
            # NVM-20: an empty project defines no NvM block yet (project.yaml nvm.blocks stays empty here - blocks
            # are usually added afterwards, page "Projekt-Konfiguration"); the probe's nvm.blocks is read anyway so
            # a caller that already defines some (a future wizard step, nxconfig.new with a project text that
            # already has them) gets their DTCs right away instead of adding them by hand later.
            defined = (cfg.project.nvm.blocks if cfg.project.nvm is not None else None) or {}
            nvm_blocks = {block: nxnvm.is_asil(entry.integrity) for block, entry in defined.items()}
            # COM-46: a bus-off DTC per used CAN bus, in the order of their bus ids (board order)
            used_can = set(use.get("can", []))
            can_buses = [bus.name for bus in b.can if bus.name in used_can]
            files[DIAG_FILE] = standard_diag(name, diag_features, repo, uid_bytes, nvm_blocks, can_buses)
        elif with_diag:
            notes.append("diag.yaml without jobs: the project has no diagnostic connection (no CAN bus or no memory "
                         "partitioning), the standard diagnostics of neXtBSW need one")
            files[DIAG_FILE] = (f"# Diagnostic description (ARCH-51) of project {name}: no jobs yet. Schema: "
                                "tools/nxconfig/model.py (DiagDescription).\nschema_version: 1\n")
        # 3. the whole project at the clock found above; bit timing and baud rates follow from the clock, so a
        # lower clock is tried while only they (or the clock) fail
        by_hz = {}
        for hz in candidates:
            _cfg, found = _check_text(scratch / "full", {**files, PROJECT_FILE: project_text(hz, board_abs)},
                                      mcudb, module_dirs, port_requirements=True)
            by_hz[hz] = found
            timing = [m for m in found if m.severity == "error" and m.path[:1] in (("clock",), ("can",), ("uart",))]
            if not timing:
                clock_hz = hz
                break
        if standard:
            chosen = [f for f in STANDARD_DIAG_FEATURES if f in diag_features]
            notes.append("diag.yaml: standard diagnostics of neXtBSW (TOOL-131) - sessions 01/03, security level "
                         "Application, F180/F181/F186/F18C, 0x0120 EcuConditions, DTC RteDeadline"
                         + (f", bus-off DTC of CAN {', '.join(can_buses)} (COM-46)" if can_buses else "")
                         + (" with " + " and ".join(chosen) if chosen else "")
                         + (f" (supply voltage from {', '.join(supply)}, role power_supply)" if supply else
                            "; no supply voltage in 0x0120: no used board device provides the role power_supply"))
        if report is not None:
            # TOOL-126: until now these findings were thrown away here and came back only after create()
            report["errors"] = [m for m in by_hz.get(clock_hz, []) if m.severity == "error"]
        notes.insert(0, f"core clock {clock_hz / 1e6:g} MHz (highest value of {cfg.derivative.derivative.name} the "
                        "clock model reaches with this board and its buses)")
        files[PROJECT_FILE] = project_text(clock_hz, _board_ref(board.file.parent, target, repo))
        return files
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _empty_rte(name: str, repo: Path) -> str:
    """rte.yaml without a component: one task (application_default). TOOL-114: every project the wizard creates
    has an ECU description - the empty one and the copy of a source without project.rte."""
    return (f"# ECU description (tools/rtegen) of project {name}, created empty (TOOL-59): no software "
            "component yet.\n"
            "# Format: swc/README.md and projects/README.md. Add components in neXtBSW Studio (step "
            "Komponenten).\n"
            "# RTE-22: components[].path and can.dbc are relative to this file.\n"
            f"schema_version: {_FILE_RELATIVE_PATHS}\n"
            + _default_tasks(repo)
            + "components: []\n")


def _default_tasks(repo: Path) -> str:
    """One application task with name, priority and stack of the BSW task manifest (application_default).

    TOOL-108: written in BLOCK style, not as `- { name: swc, ... }`. The task is the one entry of rte.yaml that
    grows while the project is edited - its `runnables:` list gets an entry for every runnable, each with its
    own comment and offset. A one line flow mapping holds all of that in one ever longer line, and the Studio
    keeps comments per runnable only in a block list (yamldoc.set_sequence, TOOL-15). The flow spelling of
    projects created before is still edited correctly (yamldoc, TOOL-108); this only decides how NEW ones look.
    """
    for manifest in (Path(repo) / TASKS_MANIFEST, nxcfg.REPO / TASKS_MANIFEST):
        data = _load_yaml(manifest)
        default = data.get("application_default") if isinstance(data, dict) else None
        if isinstance(default, dict) and default.get("name"):
            return ("# One application task with the defaults of ecu/asyncCoreConfiguration/tasks.yaml "
                    "(application_default).\n"
                    "# Without a runnables: list every runnable runs in it; the Studio writes the list as soon as\n"
                    "# a runnable is assigned or a second task is added.\n"
                    "tasks:\n"
                    f"  - name: {default['name']}\n"
                    "    period_ms: 10\n"
                    f"    priority: {default.get('priority', 30)}\n"
                    f"    stack_bytes: {default.get('stack_bytes', 2048)}\n")
    return ""


def plan_empty(name: str, board, target: Path | None = None, repo: Path = nxcfg.REPO, with_diag: bool = False,
               description: str = "", mcudb: Path = nxcfg.MCUDB, module_dirs=nxcfg.MODULE_DIRS,
               signals: str = "minimal", safety: "SafetyChoice | str | None" = None,
               calibration: bool = False, workspace=None) -> Plan:
    """Plan of an empty project for `board` (name, folder or BoardInfo). Raises NewProjectError.

    `workspace` (TOOL-216, see workspace_arguments): `board` is also looked up in the boards of the workspace.

    `signals` (TOOL-106): "minimal" or "all" signals of the board in `use` - see SIGNAL_CHOICES.
    `safety` (TOOL-110): None - the section stays commented out and the notes say so; a SafetyChoice - the pins the
    user chose, checked against the board (NewProjectError otherwise). SAFETY_PROBE is for newboard.check_board.
    `with_diag` (TOOL-131): the standard diagnostics of neXtBSW; `calibration`: a calibration segment
    (memory.calibration) and its DTCs in diag.yaml - see the module docstring."""
    repo = Path(repo).resolve()
    target = Path(target).absolute() if target else default_target(name, repo)
    _validate(name, target, repo, workspace)
    info = _find_board(board, repo, Path(mcudb), workspace)
    if info.problem:
        raise NewProjectError([f"board {info.name}: {info.problem}"])
    # the plan keeps the databases and modules of the tree: nxconfig check adds the search paths of the workspace
    # itself; only the scratch projects of the planning (no pin there) need them named
    plan = Plan(repo, name, target, "empty", board=info, mcudb=Path(mcudb), module_dirs=tuple(module_dirs))
    probe_mcudb = _mcudb_for(info.mcu, mcudb, workspace)
    probe_modules = _modules_for(module_dirs, workspace)
    if not info.port:
        plan.notes.append(f"the family of board {info.name} has no port template platforms/<family>/port: the "
                          "project can be checked and generated, but not built (PLAT-23; the feature switches "
                          "themselves are derived since ARCH-58)")
    report: dict = {}
    files = _empty_files(name, target, info, repo, with_diag, description, probe_mcudb, probe_modules,
                         plan.notes, signals, safety, report, calibration)
    plan.files = [PlannedFile(rel, text) for rel, text in files.items()]
    plan.expected_errors = list(report.get("errors", []))
    plan.safety_required = report.get("safety_required", "")
    return plan


# ----------------------------------------------------------------------------------------------- copy


def _skipped(rel: Path) -> bool:
    parts = rel.parts
    if parts and parts[0] in SKIP_TOP and len(parts) > 1:
        return True
    if any(p in SKIP_ANYWHERE or p.endswith(SKIP_DIR_SUFFIXES) for p in parts[:-1]):
        return True
    return rel.name in SKIP_ANYWHERE or rel.suffix.lower() in SKIP_SUFFIXES


def _value_span(line: str, col: int) -> tuple[int, int]:
    """Span of the scalar token starting at `col` (plain, single or double quoted)."""
    if col < len(line) and line[col] in "'\"":
        quote = line[col]
        i = col + 1
        while i < len(line):
            if line[i] == quote:
                if quote == "'" and i + 1 < len(line) and line[i + 1] == "'":
                    i += 2
                    continue
                if quote == '"' and line[i - 1] == "\\":
                    i += 1
                    continue
                return col, i + 1
            i += 1
        return col, len(line)
    end = len(line)
    for stop in (" #", ",", "}", "]"):
        pos = line.find(stop, col)
        if pos != -1:
            end = min(end, pos)
    return col, len(line[:end].rstrip())


def _set_scalars(text: str, edits: list[tuple[tuple, str]]) -> str:
    """Replaces the scalar values at the YAML paths of `edits` in the text, keeping comments and layout."""
    tree = YAML(typ="rt").load(text)
    lines = text.split("\n")
    spans = []
    for path, value in edits:
        node = tree
        for key in path[:-1]:
            node = node[key]
        line, col = node.lc.value(path[-1]) if hasattr(node.lc, "value") and isinstance(path[-1], str) \
            else node.lc.item(path[-1])
        spans.append((line, col, value))
    for line, col, value in sorted(spans, reverse=True):
        start, end = _value_span(lines[line], col)
        lines[line] = lines[line][:start] + _quote(value) + lines[line][end:]
    return "\n".join(lines)


def _insert_after(text: str, key_line: int, line: str) -> str:
    lines = text.split("\n")
    lines.insert(key_line + 1, line)
    return "\n".join(lines)


def plan_copy(name: str, source, target: Path | None = None, repo: Path = nxcfg.REPO, description: str = "",
              mcudb: Path = nxcfg.MCUDB, module_dirs=nxcfg.MODULE_DIRS, workspace=None) -> Plan:
    """Plan of a copy of `source` (project name, folder or project.yaml). Raises NewProjectError.

    `workspace` (TOOL-216, see workspace_arguments): the source is also looked up in the projects of the workspace,
    and project.board is written relative to the new project whenever board and project lie below the workspace
    root (an absolute path only for a project outside of it)."""
    repo = Path(repo).resolve()
    target = Path(target).absolute() if target else default_target(name, repo)
    _validate(name, target, repo, workspace)
    info = _find_project(source, repo, workspace)
    src_dir = info.file.parent.resolve()
    # references in comments and format 1 paths are relative to the root the source lives in (its tree)
    src_root = repo
    if workspace is not None and _is_below(src_dir, Path(workspace.tree).resolve()):
        src_root = Path(workspace.tree).resolve()
    if _is_below(target, src_dir):
        raise NewProjectError([f"folder {target} lies inside the source project {src_dir}"])
    plan = Plan(repo, name, target, "copy", source=info, mcudb=Path(mcudb), module_dirs=tuple(module_dirs))

    old_prefix = _repo_rel(src_dir, src_root).rstrip("/") + "/"
    new_prefix = _repo_rel(target, repo).rstrip("/") + "/"

    def comment_paths(text: str) -> str:
        # references to the source folder (comments, check/build hints) follow the copy
        return text.replace(old_prefix, new_prefix) if old_prefix != new_prefix else text

    files: dict[str, PlannedFile] = {}
    skipped = 0
    for path in sorted(src_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(src_dir)
        if _skipped(rel):
            skipped += 1
            continue
        key = rel.as_posix()
        if path.suffix.lower() in TEXT_SUFFIXES:
            try:
                files[key] = PlannedFile(key, comment_paths(path.read_text(encoding="utf-8")), note="")
                continue
            except UnicodeDecodeError:
                pass
        files[key] = PlannedFile(key, source=path)
    if skipped:
        plan.notes.append(f"{skipped} file(s) of build output and tool caches not copied (top level build/, "
                          "generated/; __pycache__, slprj, *.slxc, ...)")

    # project.yaml: name, board, ECU and diagnostic description
    project = files.get(PROJECT_FILE)
    if project is None or project.text is None:
        raise NewProjectError([f"{info.file} cannot be read"])
    data = YAML(typ="safe").load(project.text) or {}
    section = data.get("project") or {}
    edits = [(("project", "name"), name)]
    board_path = (src_dir / str(section.get("board", ""))).resolve()
    new_board = _board_ref(board_path, target, repo)
    edits.append((("project", "board"), new_board))
    plan.notes.append(f"project.board: {section.get('board')} -> {new_board}")
    for key in ("rte", "diag"):
        value = section.get(key)
        if not value:
            continue
        file = (src_dir / str(value)).resolve()
        if not _is_below(file, src_dir):          # outside the source folder: keep pointing to the same file
            new = _rel(file, target)
            edits.append((("project", key), new))
            plan.notes.append(f"project.{key}: {value} -> {new} (file outside the source project, not copied)")
    if description:
        if "description" in section:
            edits.append((("project", "description"), description))
    text = _set_scalars(project.text, edits)
    # TOOL-114: the RTE is required - a source without project.rte (a project written before TOOL-114, a
    # configuration-only profile) gets an rte.yaml without components, as an empty project does. The wizard never
    # creates a project without an ECU description.
    if not section.get("rte"):
        added = next(key for key in (RTE_FILE, *(f"rte_{i}.yaml" for i in range(1, 100)))
                     if key not in files and not (src_dir / key).exists())
        files[added] = PlannedFile(added, _empty_rte(name, repo), note="added: the source has no project.rte")
        tree = YAML(typ="rt").load(text)
        line = tree["project"].lc.value("board")[0]
        indent = re.match(r"\s*", text.split("\n")[line]).group(0)
        text = _insert_after(text, line, f"{indent}rte: {added}  # TOOL-114: added by the wizard, the RTE is required")
        plan.notes.append(f"project.rte: the source has none - {added} added with one task and no component "
                          "(the RTE is required, TOOL-114)")
    if description and "description" not in section:
        tree = YAML(typ="rt").load(text)
        line = tree["project"].lc.value("name")[0]
        indent = re.match(r"\s*", text.split("\n")[line]).group(0)
        text = _insert_after(text, line, f"{indent}description: {_quote(description)}")
    project.text = text
    project.note = "name, board and descriptions rewritten"

    # rte.yaml: project-specific components and DBC move with the copy, reusable ones (swc/) stay referenced
    rte_value = section.get("rte")
    rte_key = None
    if rte_value:
        rte_file = (src_dir / str(rte_value)).resolve()
        if _is_below(rte_file, src_dir):
            rte_key = rte_file.relative_to(src_dir).as_posix()
    if rte_key and rte_key in files and files[rte_key].text is not None:
        rte = files[rte_key]
        original = YAML(typ="safe").load((src_dir / rte_key).read_text(encoding="utf-8")) or {}
        # RTE-22: a component or DBC copied with the project moves to the same place below the target; one outside
        # the source project stays where it is, but its path still has to be written from the new folder - with
        # paths relative to the rte.yaml it is no longer the same text. The copy keeps the format of its source, so
        # both sides use the same kind of base: format 2 the rte.yaml's folder, format 1 the repository root.
        file_relative = _schema_version(original) >= _FILE_RELATIVE_PATHS
        old_base = (src_dir / rte_key).resolve().parent if file_relative else src_root
        new_base = (target / rte_key).parent if file_relative else repo

        def _moved(old: str) -> tuple[str, bool]:
            if Path(old).is_absolute():         # an absolute reference means "exactly this place": keep it
                return str(old), False
            where = (old_base / str(old)).resolve()
            copied = _is_below(where, src_dir)
            new_place = (target / where.relative_to(src_dir)) if copied else where
            return _rel(new_place, new_base), copied

        edits = []
        for i, comp in enumerate(original.get("components") or []):
            if not isinstance(comp, dict) or not comp.get("path"):
                continue
            new, copied = _moved(comp["path"])
            what = "copied" if copied else "stays referenced"
            if new != comp["path"]:
                edits.append((("components", i, "path"), new))
                plan.notes.append(f"component {comp.get('name')}: {what}, path {comp['path']} -> {new}")
            else:
                plan.notes.append(f"component {comp.get('name')}: {what} ({comp['path']})")
        can = original.get("can")
        if isinstance(can, dict) and can.get("dbc"):
            new, copied = _moved(can["dbc"])
            if new != can["dbc"]:
                edits.append((("can", "dbc"), new))
                plan.notes.append(f"can.dbc: {can['dbc']} -> {new}")
        if edits:
            rte.text = _set_scalars(rte.text, edits)
            rte.note = "paths of copied components and DBC rewritten"
    plan.notes.append("diagnostic addresses, CAN ids and XCP ids are those of the source project - change them if both "
                      "ECUs share a bus")
    plan.notes.append("comments of the source keep their wording (e.g. the CMake preset of the source); the copy builds "
                      "without a preset of its own (Studio step Bauen, or -DNX_PROJECT)")
    plan.files = list(files.values())
    return plan


# ----------------------------------------------------------------------------------------------- create


def plan(name: str, template: str, board=None, source=None, target: Path | None = None, repo: Path = nxcfg.REPO,
         with_diag: bool = False, description: str = "", mcudb: Path = nxcfg.MCUDB,
         module_dirs=nxcfg.MODULE_DIRS, signals: str = "minimal", safety: "SafetyChoice | None" = None,
         calibration: bool = False, workspace=None) -> Plan:
    if template == "empty":
        if board is None:
            raise NewProjectError(["an empty project needs a board"])
        return plan_empty(name, board, target, repo, with_diag, description, mcudb, module_dirs, signals, safety,
                          calibration, workspace)
    if template == "copy":
        if source is None:
            raise NewProjectError(["a copy needs a source project"])
        return plan_copy(name, source, target, repo, description, mcudb, module_dirs, workspace)
    raise NewProjectError([f"unknown template {template!r} (one of {', '.join(TEMPLATES)})"])


def create(plan: Plan) -> Result:
    """Writes the plan: staging folder next to the target, one rename into place, then `nxconfig check`.

    Raises NewProjectError (or OSError) with nothing left behind if the target became unusable or writing fails.
    Configuration errors of the result are no failure: they come back as messages with YAML path.
    """
    target = plan.target
    problems = check_target(target, plan.repo)
    if problems:
        raise NewProjectError(problems)
    created_parents = []
    parent = target.parent
    while not parent.exists():
        created_parents.append(parent)
        parent = parent.parent
    staging = target.parent / f".{target.name}.nxnew-{uuid.uuid4().hex[:8]}"
    try:
        for folder in reversed(created_parents):
            folder.mkdir()
        staging.mkdir()
        for f in plan.files:
            out = staging / f.path
            out.parent.mkdir(parents=True, exist_ok=True)
            if f.text is not None:
                # PLAT-120: files of a new project come from templates (generator output exception); a copy of a
                # project keeps the text of its source
                text = genlicense.stamp_if_known(f.text, f.path) if plan.template == "empty" else f.text
                out.write_text(text, encoding="utf-8", newline="\n")
            else:
                shutil.copy2(f.source, out)
        if target.exists():
            target.rmdir()                          # empty (checked above)
        os.replace(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        for folder in created_parents:              # innermost first
            try:
                folder.rmdir()
            except OSError:
                pass
        raise
    _config, messages = nxcfg.check(plan.project_file, plan.mcudb, plan.module_dirs)
    return Result(plan.project_file, messages, plan)


# ----------------------------------------------------------------------------------------------- board from a template
# TOOL-181 (user test 2026-09-29, y7dy6ef7): a new ECU is often the next revision or a variant of hardware that is
# already described. Instead of the board wizard (signal by signal from an empty draft) the project wizard can take
# an existing board.yaml - of the tree, of the boards/ folder of a workspace, or any file - as a TEMPLATE: it is
# copied under a new name into boards/<new> of the workspace the project goes to and is then an ordinary board of
# that workspace, edited on the board page like any other.
#
# What the copy keeps and what it changes:
#   kept      every value, every comment with its source (datasheet paths, measurements), every `verify: true` -
#             the facts of the template are not confirmed for the new hardware, so copying confirms nothing
#   changed   board.name (and the description, if one is given), a header that names the template, and the
#             folder references boards/<old>/ -> boards/<new>/
#   paths     every relative path the text names is resolved: one that stays valid from the new place is left
#             alone, one relative to the board folder (./, ../) is rewritten so it points to the same file, and one
#             that cannot be reached from the new place is NAMED in the notes - never silently broken
#   not taken the README of the template (it describes the template; a new one says where the board comes from),
#             other files of the template folder, the board port ecu/platforms/<old> (a justified exception of the
#             template, not of the new board) - the notes say so. Nothing a path points to is ever copied (the
#             schematics and datasheets below doc/ stay where they are).
# Afterwards the board is proven like a board of the board wizard: newboard.check_board plans an empty project with
# every signal on it in a scratch folder and returns the findings of nxconfig check.

BOARD_FILE_NAME = "board.yaml"
BOARD_README_NAME = "README.md"
# a relative path in the text of a board file: segments joined by "/", not part of a URL or of an absolute path
_PATH_TOKEN_RE = re.compile(r"(?<![\w:/.\\-])((?:\.\.?/)*[A-Za-z0-9_][A-Za-z0-9_.\-]*"
                            r"(?:/[A-Za-z0-9_.\-]*[A-Za-z0-9_\-])+)")
_HAS_SUFFIX_RE = re.compile(r"\.[A-Za-z0-9]{1,5}$")


@dataclass
class BoardCopy:
    """What create_board_copy() will write: boards/<name>/board.yaml and README.md. Nothing exists yet."""

    source: Path                            # board.yaml of the template
    template_name: str                      # board.name of the template
    name: str                               # board.name of the copy
    root: Path                              # workspace root: the copy goes to <root>/boards/<name>
    target: Path                            # <root>/boards/<name>
    text: str = ""                          # the new board.yaml
    readme: str = ""                        # the new README.md
    mcu: str = ""
    notes: list[str] = field(default_factory=list)                  # paths and what is not taken over
    rewritten: list[tuple[str, str]] = field(default_factory=list)  # (old, new) folder relative paths
    unresolved: list[str] = field(default_factory=list)             # paths not reachable from the new place

    # the Studio's Location.leftovers()/discard() treat a copy like a board of the board wizard: no board port
    port_file = None

    @property
    def board_file(self) -> Path:
        return self.target / BOARD_FILE_NAME

    def tree(self) -> list[str]:
        rel = _rel(self.target, self.root)
        return [f"{rel}/{BOARD_FILE_NAME}", f"{rel}/{BOARD_README_NAME}"]


@dataclass
class BoardCopyResult:
    board_file: Path
    messages: list[Message]
    plan: BoardCopy

    @property
    def errors(self) -> list[Message]:
        return [m for m in self.messages if m.severity == "error"]


def board_template_file(source) -> Path:
    """The board.yaml of a template given as file or folder; raises NewProjectError."""
    path = Path(source)
    file = path / BOARD_FILE_NAME if path.is_dir() else path
    if not file.is_file():
        raise NewProjectError([f"board template {source}: no {BOARD_FILE_NAME} there"])
    return file.resolve()


def _template_root(board_dir: Path) -> Path:
    """The root the template's own relative paths are written against: the folder above boards/, else its folder."""
    return board_dir.parent.parent if board_dir.parent.name == "boards" else board_dir


def _exists_from(token: str, bases) -> Path | None:
    for base in bases:
        if base is None:
            continue
        try:
            candidate = Path(base) / token
            if candidate.exists():
                return candidate.resolve()
        except (OSError, ValueError):
            continue
    return None


def _board_readme(name: str, template: str, source: Path, mcu: str, notes: int) -> str:
    """README of the copy (published documentation is English, CLAUDE.md)."""
    return (f"# {name}\n\n"
            f"Board description of `{name}` ({mcu or 'derivative: see board.yaml'}), created by the new-project "
            f"wizard of neXtBSW Studio from the template board `{template}` (`{source.as_posix()}`, TOOL-181).\n\n"
            "Every fact in [`board.yaml`](board.yaml) - pins, crystal, fitted parts, supply - is **inherited from the "
            "template** and not confirmed for this hardware. The source comments and the `verify: true` markers of "
            "the template were kept as they were; check each entry against the schematic of this board, change what "
            "differs, and remove `verify: true` only where the schematic or a measurement confirms the value.\n"
            + (f"\nThe wizard reported {notes} note(s) on paths and files of the template; the log of the Studio "
               "lists them.\n" if notes else ""))


def plan_board_copy(source, name: str, root: Path, tree: Path | None = None, description: str = "",
                    target: Path | None = None) -> BoardCopy:
    """Plan of a copy of the board file `source` (file or folder) as board `name` into <root>/boards/<name>.

    `tree` is the neXtBSW tree the workspace uses (a path that exists only there is named as such). Raises
    NewProjectError; writes nothing. The rules of the name are the caller's (newboard.check_name)."""
    file = board_template_file(source)
    root = Path(root).resolve()
    tree = Path(tree).resolve() if tree else None
    target = Path(target).absolute() if target else root / "boards" / name
    try:
        text = file.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        raise NewProjectError([f"board template {file}: cannot be read ({e})"])
    try:
        data = YAML(typ="safe").load(text)
    except YAMLError as e:
        raise NewProjectError([f"board template {file}: no valid YAML ({str(e).splitlines()[0]})"])
    section = data.get("board") if isinstance(data, dict) else None
    if not isinstance(section, dict) or not section.get("name"):
        raise NewProjectError([f"board template {file}: no board.name - not a board file of nxconfig"])
    old = str(section["name"])
    if name.lower() == old.lower():
        raise NewProjectError([f"board name {name!r} is the name of the template - the copy needs a new name"])
    if _is_below(target, file.parent) or _is_below(file.parent, target):
        raise NewProjectError([f"folder {target} and the template folder {file.parent} overlap"])
    plan = BoardCopy(file, old, name, root, target, mcu=str(section.get("mcu") or ""))

    # 1. name (and description), keeping every comment and the layout
    edits = [(("board", "name"), name)]
    if description and "description" in section:
        edits.append((("board", "description"), description))
    text = _set_scalars(text, edits)
    if description and "description" not in section:
        line = YAML(typ="rt").load(text)["board"].lc.value("name")[0]
        indent = re.match(r"\s*", text.split("\n")[line]).group(0)
        text = _insert_after(text, line, f"{indent}description: {_quote(description)}")

    # 2. relative paths: valid from the new place, rewritten (relative to the board folder) or named
    old_dir, new_dir = file.parent, target
    old_bases, new_bases = [old_dir, _template_root(old_dir), tree], [new_dir, root]
    decided: dict[str, str] = {}
    own_prefix = f"boards/{old}/"
    in_tree, missing = [], []
    for match in _PATH_TOKEN_RE.finditer(text):
        token = match.group(1)
        if token in decided:
            continue
        decided[token] = token
        if token.startswith(own_prefix):
            decided[token] = f"boards/{name}/" + token[len(own_prefix):]
            continue
        if _exists_from(token, new_bases) is not None:
            continue                                        # valid from the new place as written
        found = _exists_from(token, old_bases)
        if found is not None and token.startswith(("./", "../")) and (old_dir / token).exists():
            new = _rel((old_dir / token).resolve(), new_dir)
            decided[token] = new
            plan.rewritten.append((token, new))
            plan.notes.append(f"path {token} -> {new} (relative to the board folder; the same file)")
        elif found is not None and tree is not None and _exists_from(token, [tree]) is not None:
            in_tree.append(token)                           # a file of the neXtBSW tree (schema, module, datasheet)
        elif found is not None:
            plan.unresolved.append(token)
            plan.notes.append(f"path {token}: not reachable from the new board folder or the workspace root - it "
                              f"lies next to the template ({found.as_posix()}); kept as written, adapt the reference "
                              "or put the file next to the new board")
        elif _HAS_SUFFIX_RE.search(token):
            missing.append(token)
    if in_tree:
        plan.unresolved += in_tree
        plan.notes.append(f"{len(in_tree)} path(s) name files of the neXtBSW tree {tree.as_posix()} and are not "
                          "reachable from the workspace root (in a workspace the tree is the submodule nextbsw/); "
                          "kept as written: " + ", ".join(in_tree))
    if missing:
        plan.unresolved += missing
        plan.notes.append(f"{len(missing)} path(s) not found already from the template; kept as written: "
                          + ", ".join(missing))
    text = _PATH_TOKEN_RE.sub(lambda m: decided.get(m.group(1), m.group(1)), text)

    # 3. a header naming the template; the template's own header follows unchanged (its sources stay)
    head = (f"# Board {name}: created from the template {old} ({file.as_posix()}) by the new-project wizard "
            "(TOOL-181).\n"
            "# Every fact below is inherited from the template and not confirmed for this hardware: check it against "
            "the\n# schematic of THIS board. Sources and `verify: true` of the template are kept as they were.\n"
            + (f"# Header of the template {old}:\n" if text.startswith("#") else ""))
    plan.text = head + text + ("" if text.endswith("\n") else "\n")

    # 4. what is not taken over - said, not dropped
    others = sorted(p.name for p in old_dir.iterdir() if p.name != BOARD_FILE_NAME)
    if BOARD_README_NAME in others:
        others.remove(BOARD_README_NAME)
        plan.notes.append(f"{BOARD_README_NAME} of the template not taken over (it describes {old}); the new one "
                          "names the template")
    if others:
        plan.notes.append(f"not copied from the template folder: {', '.join(others)}")
    for base in dict.fromkeys(b for b in (_template_root(old_dir), tree) if b is not None):
        port = Path(base) / "ecu" / "platforms" / old / "Options.cmake"
        if port.is_file():
            active = [line for line in port.read_text(encoding="utf-8", errors="replace").splitlines()
                      if line.strip() and not line.lstrip().startswith("#")]
            plan.notes.append(f"board port ecu/platforms/{old}/Options.cmake of the template not taken over"
                              + (f": it holds {len(active)} active line(s), an exception of {old} - add "
                                 f"ecu/platforms/{name}/Options.cmake only if the new board needs the same"
                                 if active else " (comments only; a board builds without one since ARCH-58)"))
            break
    plan.readme = _board_readme(name, old, file, plan.mcu, len(plan.notes))
    return plan


def create_board_copy(plan: BoardCopy, mcudb: Path = nxcfg.MCUDB, module_dirs=nxcfg.MODULE_DIRS
                      ) -> BoardCopyResult:
    """Writes the copy (staging folder, one move into place) and proves it with newboard.check_board.

    All or nothing, like the board wizard. Findings of the check are no failure - they come back as messages with
    file, line and YAML path."""
    from . import newboard                  # newboard imports this module

    target = plan.target
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise NewProjectError([f"folder {target} exists and is not empty"])
    staging = target.parent / f".{target.name}.nxcopy-{uuid.uuid4().hex[:8]}"
    try:
        staging.mkdir(parents=True)
        (staging / BOARD_FILE_NAME).write_text(plan.text, encoding="utf-8", newline="\n")
        (staging / BOARD_README_NAME).write_text(plan.readme, encoding="utf-8", newline="\n")
        if target.exists():
            target.rmdir()                  # empty (checked above)
        os.replace(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    messages = newboard.check_board(plan.board_file, plan.root, mcudb, module_dirs)
    return BoardCopyResult(plan.board_file, messages, plan)
