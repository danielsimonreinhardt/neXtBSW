"""Format migrations of a workspace (TOOL-66, doc/architecture/customer-project.md gap N11).

A customer works with one pinned neXtBSW version (`nextbsw.yaml`, ARCH-56). Moving the pin to a newer release may
raise the format of the configuration files: `project.yaml`, `board.yaml`, the derivative files of the workspace,
the ECU description `rte.yaml`, the diagnostic description `diag.yaml`, the component descriptions `swc.yaml` and
the pin itself. Every one of them carries its format version (`schema_version`, `swc.yaml`: `version`), so the
question "does this file fit this tree?" is answered by the file, not by a second version number in it
(customer-project.md section 3.1).

This module is the answer to "and who raises them?":

  - `KINDS` says, per file kind, which format version *this* tree reads - the same number as the `Literal` of the
    schema in `model.py` (a host test compares the two, so they cannot drift).
  - `STEPS` is the list of migration steps, each named by the version it produces (`rte:2` raises a file from 1
    to 2). A step carries a description, a check ("does this concern me?") and the change itself.
  - `plan()` collects the files of a workspace, works out the chain of steps per file and produces the diff;
    `apply()` writes it after a backup. `nxconfig migrate` is the command (dry run by default).
  - `finding()` is what `nxconfig check` uses when it opens a file: too old is reported with file, line, YAML path
    and the exact command that raises it; too new is an error saying that the tree is older than the files.

Nothing here reformats a file. The steps change lines through `yamledit.YamlFile`, so comments, alignment and the
customer's own structure stay byte for byte what they were (see `yamledit` for why a plain ruamel re-dump is not
good enough).


Adding a migration step
-----------------------

1. Raise the format in `model.py` (the `Literal` of `schema_version`) and `KINDS[<kind>].reads` to the new number.
2. Append a `Step` to `STEPS` below, named by the version it produces. `title` is the one line the report prints,
   `description` says what changes and why, `check` answers whether this document is concerned at all, and
   `change` makes the change on the `YamlFile` (`set`, `add`, `remove`, `rename`; `rewrite` only if the document
   has to be restructured - the report then says that the file was reformatted). The step never touches the
   version key itself; the runner raises it.
3. Migrate the files of this tree in the same change: `python tools/nxconfig migrate <tree> --write`.
4. Add a host test per step in `test_workspace.py` (class `Migrations`): a synthetic file of the old format with
   comments, the diff of the dry run, and the migrated file read by the new schema without a finding.
5. Name the step in the changelog entry of the release; the update report of a customer
   (customer-project.md section 5.5) lists it from `STEPS`.

A step whose `to_version` is higher than `KINDS[<kind>].reads` is *prepared*: `migrate --list` shows it, it never
runs on its own, and `--step <id>` runs it (that is how a step is written and tested before the release that
raises the format).
"""

import difflib
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import yamledit
from .messages import Message
from .model import (Board, Derivative, DiagDescription, EcuDescription, PinmuxTable, Project, WorkspacePin)

BACKUP_DIR = ".nextbsw-backup"                  # <workspace>/.nextbsw-backup/<timestamp>/<file>
PIN_FILE = "nextbsw.yaml"
# folders a workspace does not keep configuration in (and that are large): pruned while collecting
SKIP_DIRS = {".git", ".hg", ".svn", ".venv", "venv", "__pycache__", "node_modules", "external", ".claude",
             ".idea", ".vscode", BACKUP_DIR}
SKIP_PREFIX = ("build",)                        # build, build-debug, builddir ... - see is_build_dir


# ------------------------------------------------------------------ kinds


@dataclass(frozen=True)
class Kind:
    """One configuration format: how its files are recognised, which key carries the version, what this tree reads.

    `level` is the name `config.Reporter` uses for the file, so a finding of `finding()` lands in the right file.
    `optional` marks a format whose *first* version did not carry the key: a file without one is version 1
    (`rte.yaml`, which got the key with RTE-22; `swc.yaml`, which has none yet).
    """

    name: str
    file: str                                   # canonical file name ("" : recognised by its place, see kind_of)
    reads: int                                  # the format version this tree reads
    key: str = "schema_version"
    model: type | None = None                   # pydantic schema; None: the schema lives outside nxconfig
    optional: bool = False                      # a file without the key is version 1
    level: str = ""                             # config.Reporter level; "" : not read through config._read

    @property
    def title(self) -> str:
        return self.file or self.name


# The formats of a workspace. `reads` must equal the Literal of schema_version in model.py (host test
# Migrations.test_registry_matches_the_schemas) - model.py stays the single place a format version is declared.
KINDS: dict[str, Kind] = {
    "project": Kind("project", "project.yaml", 1, model=Project, level="project"),
    "board": Kind("board", "board.yaml", 1, model=Board, level="board"),
    # mcudb/<family>/<derivative>.yaml of the workspace (nextbsw.yaml search.mcudb)
    "derivative": Kind("derivative", "", 1, model=Derivative, level="derivative"),
    "pinmux": Kind("pinmux", "", 1, model=PinmuxTable),
    # RTE-22: format 2 has the paths relative to the rte.yaml (step rte:2). The key was introduced with it, so a
    # file without one is the format of ARCH-34 - version 1, with its paths relative to the repository root.
    "rte": Kind("rte", "rte.yaml", 2, model=EcuDescription, optional=True, level="rte"),
    "diag": Kind("diag", "diag.yaml", 1, model=DiagDescription, level="diag"),
    # RTE-07: tools/swc owns this schema and calls the key `version` (tools/swc/swc_yaml.py SCHEMA_VERSION)
    "swc": Kind("swc", "swc.yaml", 1, key="version", optional=True),
    "workspace": Kind("workspace", PIN_FILE, 1, model=WorkspacePin, level="workspace"),
}
LEVELS = {k.level: k for k in KINDS.values() if k.level}


# ------------------------------------------------------------------ steps


@dataclass(frozen=True)
class Context:
    """What a step needs besides the document: where the file is, and what its relative paths were based on."""

    file: Path
    kind: Kind
    root: Path                                  # workspace root (without a pin: the tree) - base of repo paths
    tree: Path                                  # the neXtBSW tree the migration runs from


@dataclass(frozen=True)
class Step:
    """One format step, named by the version it produces: `<kind>:<to_version>`."""

    kind: str
    to_version: int
    title: str                                  # one line for the report
    description: str                            # what changes and why (migrate --list)
    change: Callable[[yamledit.YamlFile, Context], None]
    check: Callable[[yamledit.YamlFile, Context], bool] = lambda doc, ctx: True

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.to_version}"

    @property
    def prepared(self) -> bool:
        """True: the step produces a version this tree does not read yet - it never runs on its own."""
        return self.to_version > KINDS[self.kind].reads


def relative(target: Path, start: Path) -> str:
    """`target` as a path relative to the folder `start`, with forward slashes; absolute across drives."""
    try:
        return Path(os.path.relpath(target, start)).as_posix()
    except ValueError:
        return Path(target).as_posix()


def _rte2_concerns(doc: yamledit.YamlFile, ctx: Context) -> bool:
    """Only a description that names a path at all has something to rewrite."""
    components = doc.get("components", default=[]) or []
    return doc.has("can", "dbc") or any(isinstance(c, dict) and isinstance(c.get("path"), str) for c in components)


def _rte2_change(doc: yamledit.YamlFile, ctx: Context) -> None:
    """`components[].path` and `can.dbc` from repository-relative to relative to this rte.yaml (gap N3).

    The old base is the root of the workspace (in the tree: the tree itself) - that is what `rtegen --repo` used.
    """
    here = ctx.file.parent
    for i, component in enumerate(doc.get("components", default=[]) or []):
        old = component.get("path") if isinstance(component, dict) else None
        if not isinstance(old, str) or Path(old).is_absolute():
            continue
        new = relative((ctx.root / old).resolve(), here)
        if new != old:
            doc.set(("components", i, "path"), new,
                    f"components[{i}].path: {old} -> {new} (now relative to {ctx.file.name})")
    dbc = doc.get("can", "dbc")
    if isinstance(dbc, str) and not Path(dbc).is_absolute():
        new = relative((ctx.root / dbc).resolve(), here)
        if new != dbc:
            doc.set(("can", "dbc"), new, f"can.dbc: {dbc} -> {new} (now relative to {ctx.file.name})")


# The migration steps, oldest first. Each one is named by the version it produces; see the module docstring for
# how a new one is entered here.
STEPS: list[Step] = [
    Step("rte", 2,
         "ECU description: paths relative to rte.yaml",
         "`components[].path` and `can.dbc` were relative to the root of the repository (`rtegen --repo`). In a "
         "customer workspace that root is not the neXtBSW tree, so the paths become relative to the rte.yaml "
         "itself, like `project.board` is relative to `project.yaml` (customer-project.md gap N3). Files that "
         "name no path only get the `schema_version` key.",
         _rte2_change, _rte2_concerns),
]


def steps_of(kind: str) -> list[Step]:
    return sorted((s for s in STEPS if s.kind == kind), key=lambda s: s.to_version)


def chain(kind: Kind, version: int, extra: tuple[str, ...] = ()) -> list[Step]:
    """The steps that raise a file of `version` to what this tree reads, plus the prepared steps named in `extra`."""
    wanted = [s for s in steps_of(kind.name) if version < s.to_version <= kind.reads]
    wanted += [s for s in steps_of(kind.name) if s.id in extra and s not in wanted and s.to_version > version]
    return sorted(wanted, key=lambda s: s.to_version)


def step_ids() -> list[str]:
    return [s.id for s in STEPS]


# ------------------------------------------------------------------ the version of a file


def version_of(doc: yamledit.YamlFile, kind: Kind) -> int | None:
    """The format version a file declares. None: the key is missing (and the format requires it) or is no number."""
    value = doc.get(kind.key)
    if value is None:
        return 1 if kind.optional else None
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def command(root: Path, tree: Path, steps: list[Step]) -> str:
    """The exact command that raises a file, for a finding of `nxconfig check`."""
    prepared = [s.id for s in steps if s.prepared]
    extra = "".join(f" --step {i}" for i in prepared)
    return f"python {(Path(tree) / 'tools' / 'nxconfig').as_posix()} migrate {Path(root).as_posix()}{extra} --write"


def root_for(path: Path) -> Path:
    """The root a file's relative paths are based on: the workspace above it, else its neXtBSW tree or repository.

    The walk is the one of workspace.find_pin_file (customer-project.md section 3.1), but it also stops - and
    answers - where there is no pin: in the neXtBSW repository itself the root is the tree (development case).
    """
    path = Path(path).resolve()
    folder = path if path.is_dir() else path.parent
    for candidate in (folder, *folder.parents):
        if (candidate / PIN_FILE).is_file() or _is_tree(candidate) or (candidate / ".git").exists():
            return candidate
    return folder


def finding(level: str, file: Path, doc, tree: Path) -> Message | None:
    """The finding `nxconfig check` reports when it opens a file whose format does not fit this tree.

    Too old: which steps raise it and the exact command (file, line and YAML path come from the Reporter).
    Too new: an error saying that the tree is older than the files - no tool can guess a format it does not know.
    Returns None for a file this tree reads (and for one whose version key pydantic will complain about anyway).
    """
    kind = LEVELS.get(level)
    if kind is None or doc is None:
        return None
    value = doc.get(kind.key) if isinstance(doc, dict) else None
    if value is None and kind.optional:
        value = 1
    if value is None or isinstance(value, bool) or not isinstance(value, int) or value == kind.reads:
        return None                             # missing or malformed: the schema reports it with its own message
    path = (kind.key,)
    if value > kind.reads:
        return Message("error", f"{kind.title} is format {kind.key} {value}, this neXtBSW reads {kind.reads}: the "
                                f"tree ({Path(tree).as_posix()}) is older than the files of the workspace. Check "
                                f"out the neXtBSW the workspace was written with, or move the pin forward "
                                f"({PIN_FILE} nextbsw.version) - a format can only be raised, never lowered.",
                       Path(file), path)
    steps = chain(kind, value)
    how = ", ".join(s.id for s in steps) or "no step is registered for it"
    return Message("error", f"{kind.title} is format {kind.key} {value}, this neXtBSW reads {kind.reads}: run the "
                            f"migration ({how}) with `{command(root_for(file), tree, steps)}`; it shows the change "
                            f"first and writes a backup to {BACKUP_DIR}/ before it edits anything.",
                   Path(file), path)


# ------------------------------------------------------------------ collecting the files of a workspace


def mcudb_dirs(root: Path) -> list[Path]:
    """Folders that hold derivative descriptions: `search.mcudb` of the pin and the conventional `mcudb/`.

    The pin is read leniently (ruamel only, no schema): during an update the pin itself may be of an old format,
    and a migration must still be able to find the files.
    """
    root = Path(root)
    dirs, pin = [], root / PIN_FILE
    if pin.is_file():
        doc, _ = yamledit.read(pin)
        entries = doc.get("search", "mcudb", default=[]) if doc is not None else []
        dirs += [(root / e).resolve() for e in (entries or []) if isinstance(e, str)]
    default = (root / "mcudb").resolve()
    return dirs if default in dirs else dirs + [default]


def _is_tree(folder: Path) -> bool:
    from . import workspace as nxworkspace
    return nxworkspace.is_tree(folder)


def is_build_dir(folder: Path) -> bool:
    """True: a build output folder the walk may prune.

    The name alone cannot decide it. `projects/buildtest_s32k148_min` is a project of this tree, and a customer may
    call a folder `build_variants` just as well - pruning it by its prefix silently left its `rte.yaml` behind
    (found in RTE-22, where that project kept format 1 while every other one was migrated). So a folder that holds
    a configuration file of a known format is never build output, whatever it is called.
    """
    folder = Path(folder)
    if not folder.name.startswith(SKIP_PREFIX):
        return False
    return not any((folder / kind.file).is_file() for kind in KINDS.values() if kind.file)


def kind_of(file: Path, mcudb: list[Path]) -> Kind | None:
    """The format of a file, by its name and its place. None: not a configuration file of a known format."""
    file = Path(file)
    name = file.name
    if name.endswith(".pinmux.yaml"):
        return KINDS["pinmux"]
    for kind in ("project", "board", "rte", "diag", "swc", "workspace"):
        if name == KINDS[kind].file:
            return KINDS[kind]
    if name.endswith(".yaml") and any(file.parent.parent.resolve() == d for d in mcudb):
        return KINDS["derivative"]              # <search.mcudb>/<family>/<derivative>.yaml
    return None


def collect(folder: Path, root: Path | None = None) -> list[tuple[Path, Kind]]:
    """The configuration files below `folder` with their format, in a stable order.

    Walks the folder, prunes what is not configuration (`.git`, build folders, and every neXtBSW tree below it:
    the tree brings its own files and is migrated with its own release). Files a project names under another name
    (`project.rte`, `project.diag`) are picked up from the project. `root` is the workspace the derivative search
    paths are read from; without it, `folder` itself.
    """
    folder_root = Path(folder).resolve()
    mcudb = mcudb_dirs(root if root is not None else folder_root)
    found: dict[Path, Kind] = {}
    for folder, subdirs, files in os.walk(folder_root):
        here = Path(folder)
        subdirs[:] = sorted(d for d in subdirs
                            if d not in SKIP_DIRS and not is_build_dir(here / d)
                            and not _is_tree(here / d))
        for name in sorted(files):
            if not name.endswith((".yaml", ".yml")):
                continue
            kind = kind_of(here / name, mcudb)
            if kind is not None:
                found[(here / name).resolve()] = kind
    for file, kind in list(found.items()):
        if kind.name != "project":
            continue
        doc, _ = yamledit.read(file)
        if doc is None:
            continue
        for key, other in (("rte", KINDS["rte"]), ("diag", KINDS["diag"])):
            named = doc.get("project", key)
            if isinstance(named, str):
                target = (file.parent / named).resolve()
                if target.is_file():
                    found.setdefault(target, other)
    return sorted(found.items(), key=lambda item: item[0].as_posix())


def workspace_root(path: Path) -> tuple[Path, list[tuple[Path, Kind]]]:
    """(root, files) for what the user named: a workspace, a folder, a pin file or a single configuration file."""
    path = Path(path).resolve()
    if path.is_dir():
        # a folder inside a workspace (migrate projects/<p>) migrates only that folder, but the base of the
        # relative paths stays the workspace - otherwise a step would rewrite them against the wrong root
        root = root_for(path)
        return root, collect(path, root)
    if path.name == PIN_FILE:
        return path.parent, collect(path.parent)
    root = root_for(path)
    kind = kind_of(path, mcudb_dirs(root))
    if kind is None:
        return root, []
    return root, [(path, kind)]


# ------------------------------------------------------------------ plan and apply


@dataclass
class FilePlan:
    """What would happen to one file."""

    file: Path
    kind: Kind
    version: int | None = None
    steps: list[Step] = field(default_factory=list)
    changes: list[tuple[str, list[str]]] = field(default_factory=list)   # (step id, the lines it changed)
    diff: str = ""
    new_text: str | None = None
    problem: str | None = None                  # unreadable, or newer than this tree reads
    reformatted: bool = False

    @property
    def to_version(self) -> int:
        return self.steps[-1].to_version if self.steps else (self.version or 0)

    @property
    def migrates(self) -> bool:
        return self.new_text is not None and bool(self.steps)


@dataclass
class Plan:
    root: Path
    tree: Path
    files: list[FilePlan] = field(default_factory=list)
    selected: tuple[str, ...] = ()              # --step: prepared steps the user asked for

    @property
    def todo(self) -> list[FilePlan]:
        return [f for f in self.files if f.migrates]

    @property
    def problems(self) -> list[FilePlan]:
        return [f for f in self.files if f.problem]

    @property
    def current(self) -> list[FilePlan]:
        return [f for f in self.files if not f.migrates and not f.problem]


def plan_file(file: Path, kind: Kind, root: Path, tree: Path, selected: tuple[str, ...] = ()) -> FilePlan:
    """The steps for one file and the diff they produce. Nothing is written."""
    result = FilePlan(Path(file), kind)
    doc, problem = yamledit.read(file)
    if doc is None:
        result.problem = problem
        return result
    if not isinstance(doc.tree, dict):
        result.problem = f"expected a mapping at the top level of {Path(file).name}"
        return result
    result.version = version_of(doc, kind)
    if result.version is None:
        result.problem = (f"{kind.key} is missing or is no whole number; the schema of {kind.title} reports it "
                          f"(nxconfig check) - a migration cannot guess the format")
        return result
    # A prepared step named with --step raises files beyond what this tree reads on purpose (that is how a step is
    # tested before its release); only a version nobody asked for means the tree is older than the files.
    allowed = max([kind.reads] + [s.to_version for s in steps_of(kind.name) if s.id in selected])
    if result.version > allowed:
        result.problem = (f"{kind.key} {result.version} is newer than this neXtBSW reads ({kind.reads}): the tree "
                          f"{Path(tree).as_posix()} is older than this file")
        return result
    result.steps = chain(kind, result.version, selected)
    if not result.steps:
        return result
    ctx = Context(Path(file).resolve(), kind, Path(root).resolve(), Path(tree).resolve())
    for step in result.steps:
        before = len(doc.edits)
        try:
            if step.check(doc, ctx):
                step.change(doc, ctx)
        except yamledit.EditError as e:
            result.problem = f"step {step.id} cannot be applied: {e}"
            return result
        notes = [e.note for e in doc.edits[before:]]
        result.changes.append((step.id, notes or ["(this file has nothing the step changes)"]))
    # The version key is raised once, by the runner: a step never has to remember it, and a chain of steps leaves
    # one line changed instead of one per step.
    target = result.steps[-1].to_version
    present = doc.has(kind.key)
    note = f"{kind.key}: {result.version} -> {target}" if present else f"{kind.key}: added, {target}"
    try:
        (doc.set if present else doc.add)((kind.key,), target, note)
    except yamledit.EditError as e:
        result.problem = f"{kind.key} cannot be raised to {target}: {e}"
        return result
    result.changes[-1][1].append(note)
    result.reformatted = doc.reformatted
    result.new_text = doc.result()
    result.diff = "".join(difflib.unified_diff(
        doc.text.splitlines(keepends=True), result.new_text.splitlines(keepends=True),
        fromfile=f"{Path(file).name} ({kind.key} {result.version})",
        tofile=f"{Path(file).name} ({kind.key} {result.to_version})"))
    return result


def plan(path: Path, tree: Path, selected: tuple[str, ...] = ()) -> Plan:
    """What `migrate` would do with the workspace (or the single file) at `path`."""
    root, files = workspace_root(path)
    result = Plan(root, Path(tree).resolve(), selected=tuple(selected))
    for file, kind in files:
        result.files.append(plan_file(file, kind, root, tree, tuple(selected)))
    return result


def backup_folder(root: Path, when: float | None = None) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(when if when is not None else time.time()))
    return Path(root) / BACKUP_DIR / stamp


def apply(result: Plan, backup: Path | None = None) -> tuple[Path | None, list[Path]]:
    """Writes the plan: first a copy of every file that changes, then the new contents. (backup folder, files).

    The backup is written before the first file is touched, so an interrupted run leaves a complete copy of the
    old state. Nothing is written for a plan without a change.
    """
    todo = result.todo
    if not todo:
        return None, []
    folder = Path(backup) if backup is not None else backup_folder(result.root)
    for item in todo:
        target = folder / relative(item.file, result.root)
        if target.resolve().is_relative_to(folder.resolve()):
            target.parent.mkdir(parents=True, exist_ok=True)
        else:                                   # a file outside the workspace: keep it flat, keep its name
            target = folder / item.file.name
            folder.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item.file, target)
    written = []
    for item in todo:
        item.file.write_text(item.new_text, encoding="utf-8", newline="")
        written.append(item.file)
    return folder, written


# ------------------------------------------------------------------ report


def report(result: Plan, write: bool, backup: Path | None = None, diff: bool = True) -> list[str]:
    """The lines `nxconfig migrate` prints: per file its steps, what they change and the diff."""
    lines = [f"migrate: {result.root.as_posix()} (neXtBSW tree {result.tree.as_posix()})"
             + ("" if write else " - dry run, nothing is written")]
    for item in result.files:
        if item.problem:
            lines.append(f"  {relative(item.file, result.root):<44} {item.kind.name:<11} error: {item.problem}")
        elif item.migrates:
            lines.append(f"  {relative(item.file, result.root):<44} {item.kind.name:<11} "
                         f"{item.kind.key} {item.version} -> {item.to_version}")
            for step_id, notes in item.changes:
                step = next(s for s in STEPS if s.id == step_id)
                lines.append(f"      {step_id}  {step.title}")
                lines += [f"        - {note}" for note in notes]
            if item.reformatted:
                lines.append("        ! this step rewrites the whole file; its layout may change")
            if diff:
                lines += ["        " + line.rstrip("\n") for line in item.diff.splitlines()]
    lines.append(f"  {len(result.todo)} file(s) to migrate, {sum(len(f.steps) for f in result.todo)} step(s), "
                 f"{len(result.current)} already current, {len(result.problems)} with a problem")
    if result.todo and not write:
        lines.append(f"  run again with --write to apply; a copy of every file goes to {BACKUP_DIR}/<timestamp> "
                     "first")
    elif result.todo and write:
        lines.append(f"  written; the old files are in {(backup or backup_folder(result.root)).as_posix()}")
    return lines


def describe_registry() -> list[str]:
    """`migrate --list`: the formats this tree reads and every registered step."""
    lines = ["formats of this neXtBSW tree:"]
    for kind in KINDS.values():
        lines.append(f"  {kind.name:<11} {kind.title:<16} {kind.key} {kind.reads}"
                     + ("  (a file without the key is version 1)" if kind.optional else ""))
    lines.append("migration steps:")
    for step in STEPS:
        state = f"prepared (this tree reads {KINDS[step.kind].reads})" if step.prepared else "runs automatically"
        lines.append(f"  {step.id:<11} {step.title}  [{state}]")
        lines.append(f"              {step.description}")
    if not STEPS:
        lines.append("  (none: no format has been raised yet)")
    return lines
