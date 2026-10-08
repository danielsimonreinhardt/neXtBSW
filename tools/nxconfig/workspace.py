"""Workspace pin nextbsw.yaml and its discovery (ARCH-56, doc/architecture/customer-project.md gap N2).

A workspace is a customer repository: the folder that holds nextbsw.yaml. The file names exactly one neXtBSW tree
(version, source, path, profile) and the workspace's own search paths (search.mcudb, search.modules, search.boards).
It is not a fourth configuration level - it carries no value of the ECU.

Discovery walks up from the folder of project.yaml, like git finds .git. The walk stops at the first folder that

  - holds nextbsw.yaml                 -> that is the workspace,
  - is the root of a neXtBSW tree      -> the project belongs to that tree (no pin: the running tree),
  - is the root of a git repository    -> a repository without nextbsw.yaml is no workspace (running tree).

Without a pin every tool uses the tree it runs from; the projects under projects/ of the neXtBSW repository keep
working unchanged (customer-project.md section 6.4). With a pin, its search paths come before the tree's own folders
and a name found in two places is an error with both paths. All messages carry file, line and YAML path of
nextbsw.yaml (Reporter level "workspace").

What this module checks is the *identity* of the pin: the right folder, the right version. Whether the pinned tree
is a released baseline - annotated tag v<version>, reachable from master, clean, overlays of the same version - is
the release strictness of gap N10 and lives in `pincheck.py`, with its own entry point: it costs git calls and is
only asked for by `tools/ci run|check --release`, the Studio status line and the Studio git page, not by every
`nxconfig check`.
"""

from dataclasses import dataclass
from pathlib import Path

from . import version as nxversion
from .model import WorkspacePin

PIN_FILE = "nextbsw.yaml"
WORKSPACE_LEVEL = "workspace"
BOARD_FILE = "board.yaml"


def is_tree(folder: Path) -> bool:
    """True for the root of a neXtBSW tree (a checkout or unpacked release): VERSION and tools/nxconfig."""
    folder = Path(folder)
    return nxversion.version_file(folder).is_file() and (folder / "tools" / "nxconfig").is_dir()


def find_pin_file(start: Path) -> Path | None:
    """nextbsw.yaml above `start` (a project.yaml or a folder) by the walk-up rules of this module, else None."""
    start = Path(start).resolve()
    folder = start if start.is_dir() else start.parent
    for candidate in (folder, *folder.parents):
        pin = candidate / PIN_FILE
        if pin.is_file():
            return pin
        if is_tree(candidate) or (candidate / ".git").exists():
            return None
    return None


@dataclass
class Workspace:
    """A loaded and checked nextbsw.yaml. Search paths are absolute, in the order they are searched."""

    file: Path                                  # nextbsw.yaml
    pin: WorkspacePin
    tree: Path                                  # the pinned neXtBSW tree (resolved)
    mcudb: tuple[Path, ...] = ()                # search.mcudb, before the tree's mcudb
    modules: tuple[Path, ...] = ()              # search.modules, before the tree's libs/bsw, libs/bsp
    boards: tuple[Path, ...] = ()               # search.boards, before the tree's boards

    @property
    def root(self) -> Path:
        return self.file.parent

    @property
    def version(self) -> str:
        return self.pin.nextbsw.version

    def board_dirs(self) -> tuple[Path, ...]:
        """Folders boards are offered from: the workspace's first, then the tree's boards/."""
        return self.boards + (self.tree / "boards",)


def _same(a: Path, b: Path) -> bool:
    try:
        return Path(a).resolve() == Path(b).resolve() or Path(a).samefile(b)
    except OSError:
        return False


def load(file: Path, reporter, running_tree: Path) -> Workspace | None:
    """Reads and checks nextbsw.yaml; findings go to `reporter` (level "workspace"). None if there was an error.

    Checked: the schema; the tree at nextbsw.path exists, is a neXtBSW tree and is the tree this tool runs from;
    nextbsw.version equals the tree's VERSION; every search path exists and lies outside the tree (the tree's own
    folders are searched anyway).
    """
    from .config import _read

    file = Path(file).resolve()
    errors = len([m for m in reporter.messages if m.severity == "error"])
    pin = _read(reporter, WORKSPACE_LEVEL, file, WorkspacePin)
    if pin is None:
        return None
    root = file.parent
    tree = (root / pin.nextbsw.path).resolve()
    path = ("nextbsw", "path")
    if not tree.is_dir():
        hint = ("git submodule update --init --recursive" if pin.nextbsw.source == "submodule"
                else "unpack the release archive there")
        reporter.error(WORKSPACE_LEVEL, path, f"neXtBSW tree {pin.nextbsw.path} not found ({tree}); {hint}")
    elif not is_tree(tree):
        reporter.error(WORKSPACE_LEVEL, path, f"{tree} is no neXtBSW tree (needs {nxversion.VERSION_FILE} and "
                                              "tools/nxconfig)")
    else:
        try:
            tree_version = nxversion.read_version(tree)
        except nxversion.VersionError as e:
            reporter.error(WORKSPACE_LEVEL, path, str(e))
        else:
            if tree_version != pin.nextbsw.version:
                reporter.error(WORKSPACE_LEVEL, ("nextbsw", "version"),
                               f"pins neXtBSW {pin.nextbsw.version}, but the tree {pin.nextbsw.path} is "
                               f"{tree_version} ({nxversion.VERSION_FILE}); check out the tag "
                               f"{nxversion.tag_of(pin.nextbsw.version)} or move the pin as a deliberate update")
        if not _same(tree, running_tree):
            reporter.error(WORKSPACE_LEVEL, path,
                           f"the workspace pins the tree {tree}, but this tool runs from {Path(running_tree)}; "
                           f"use the tools of the pinned tree ({tree / 'tools'})")

    def search(key: str) -> tuple[Path, ...]:
        found = []
        for i, entry in enumerate(getattr(pin.search, key)):
            folder = (root / entry).resolve()
            if not folder.is_dir():
                reporter.error(WORKSPACE_LEVEL, ("search", key, i), f"search path {entry} not found ({folder})")
            elif folder.is_relative_to(tree):
                reporter.error(WORKSPACE_LEVEL, ("search", key, i),
                               f"search path {entry} lies inside the neXtBSW tree ({folder}); the tree's own "
                               "folders are searched anyway, name only the workspace's extensions")
            elif folder in found:
                reporter.error(WORKSPACE_LEVEL, ("search", key, i), f"search path {entry} is named twice")
            else:
                found.append(folder)
        return tuple(found)

    workspace = Workspace(file, pin, tree, search("mcudb"), search("modules"), search("boards"))
    if len([m for m in reporter.messages if m.severity == "error"]) > errors:
        return None
    return workspace


def discover(start: Path, reporter, running_tree: Path) -> tuple[bool, Workspace | None]:
    """(pin found, workspace). (False, None): no nextbsw.yaml - use the running tree. (True, None): the pin has
    errors, they are in `reporter`."""
    file = find_pin_file(start)
    if file is None:
        return False, None
    return True, load(file, reporter, running_tree)


def find_derivative_files(name: str, dirs) -> list[tuple[int, Path]]:
    """(index of the directory in `dirs`, file) of every directory that describes the derivative `name` - the first
    file per directory, in the order of `dirs`."""
    found = []
    for i, base in enumerate(dirs):
        matches = sorted(Path(base).glob(f"*/{name}.yaml"))
        if matches:
            found.append((i, matches[0]))
    return found


def board_folders(name: str, dirs) -> list[tuple[int, Path]]:
    """(index of the directory in `dirs`, folder) of every board folder `name` with a board.yaml."""
    return [(i, Path(base) / name) for i, base in enumerate(dirs) if (Path(base) / name / BOARD_FILE).is_file()]
