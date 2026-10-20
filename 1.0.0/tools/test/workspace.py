"""Running tools/test and tools/ci against a customer workspace (TOOL-67, customer-project.md gap N12).

A workspace is a customer repository: the folder that holds `nextbsw.yaml` (ARCH-56,
`tools/nxconfig/workspace.py`). It owns its projects, boards, derivatives and components, and it pins one neXtBSW
tree. This module holds everything the two tools need to know about such a folder; the tools themselves keep their
behaviour inside the tree unchanged.

Where the workspace's own test list lives
-----------------------------------------
`<workspace>/ci/tests.yaml` and `<workspace>/ci/pipeline.toml` - the two names
`doc/architecture/customer-project.md` (gap N12) already names. Reasons to keep them:

  - one folder `ci/` holds everything the customer's pipeline decides, next to (not inside) the sources, so the
    workspace root stays the thin `CMakeLists.txt`, `CMakePresets.json`, `nextbsw.yaml` of ARCH-57;
  - the file format is **the tree's format** (`tools/test/tests.yaml`, version 1, same kinds and fields), so the
    customer reads one description and the tree's runner needs no second parser;
  - the pipeline file is the tree's format as well, so the customer states allowed skips, unbuilt presets and
    steps outside CI exactly as neXtBSW does.

Which entries of the tree carry over
------------------------------------
By default **none**. A tree entry runs in a workspace only if it carries `in_workspace: true`. The default is the
honest one: almost every entry of `tools/test/tests.yaml` builds or checks the tree's own projects, boards and
fixtures (the eval board among them) - re-running them in a customer's pipeline costs half an hour and can only
fail for reasons the customer must not be asked to fix. The release CI of the tree has run them before the tag.

What does carry over is the handful of entries that prove the **pinned tree's tools work in this environment**:
the customer's `.venv` is built from the tree's requirements, but on the customer's machine, with the customer's
Python. `in_workspace: true` marks those entries; every one of them runs inside the tree and writes only into its
own temporary directory.

Inventory
---------
In a workspace run the completeness check looks at the **workspace**, with the workspace's own `inventory`
patterns, and only the workspace list's `covers` count. A customer is never told that he failed to list the test
files of the neXtBSW tree - they are not his.

Reading only
------------
A workspace run never writes into the tree: the build root has to lie outside it (checked, see `check_build_root`),
entries of the workspace list run with the workspace as their working directory, and the entries inherited from the
tree are exactly those that write into their own `{tmp}` only.

Coverage and static analysis of the customer's own code (TOOL-217, gap N19)
--------------------------------------------------------------------------
The thresholds of `tools/test/coverage.yaml` and the baseline of `tools/misra` belong to the tree. A workspace keeps
its own next to its list, in the same formats:

  - `ci/coverage.yaml`          thresholds per module of the workspace (format of `tools/test/coverage.yaml`);
                                measured over the `zig` entries of `ci/tests.yaml` only, counted only for files of
                                the workspace (never the tree, even where the tree lies inside it as a submodule);
  - `ci/misra/deviations.yaml`  justified deviations (format of `tools/misra/deviations.yaml`, required);
  - `ci/misra/baseline.yaml`    known findings of the inventory, written by `tools.misra baseline --workspace`;
  - `ci/misra/defects.yaml`     optional, real defects among the findings;
  - `ci/misra/rules.yaml`       optional; without it the tree's rule set and re-categorization plan apply.

`ci/pipeline.toml` switches the two stages on (`[coverage]`, `[static_analysis]`) exactly as in the tree.

Standard library plus PyYAML - the same set `tools/test` already needs.
"""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PIN_FILE = "nextbsw.yaml"
CI_DIR = "ci"
LIST_NAME = "tests.yaml"
PIPELINE_NAME = "pipeline.toml"
BUILD_SUBDIR = "build/nxtest"          # build root of a workspace run, inside the workspace
VENV_DIR = ".venv"
COVERAGE_NAME = "coverage.yaml"        # TOOL-217 (N19): thresholds for the workspace's own code
MISRA_DIR = "misra"                    # TOOL-217 (N19): ci/misra/ - deviations, baseline, optional rules/defects


class WorkspaceError(Exception):
    """The folder is no workspace, or its pin cannot be read at all."""


# ---------------------------------------------------------------------------- discovery


def is_tree(folder: Path) -> bool:
    """Root of a neXtBSW tree - the rule of tools/nxconfig/workspace.py, without needing pydantic."""
    folder = Path(folder)
    return (folder / "VERSION").is_file() and (folder / "tools" / "nxconfig").is_dir()


def find_workspace(start: Path) -> Path | None:
    """The folder with nextbsw.yaml above `start`, by the walk-up rules of tools/nxconfig/workspace.py.

    The walk stops at a neXtBSW tree (the project belongs to that tree) and at the root of a git repository
    without a pin (a repository without nextbsw.yaml is no workspace).
    """
    start = Path(start).resolve()
    folder = start if start.is_dir() else start.parent
    for candidate in (folder, *folder.parents):
        if (candidate / PIN_FILE).is_file():
            return candidate
        if is_tree(candidate) or (candidate / ".git").exists():
            return None
    return None


def version_module(tree: Path):
    """tools/nxconfig/version.py of `tree`, loaded from its file (the package would need pydantic).

    The version rules live in one place (ARCH-55); tools/ci loads the same file the same way.
    """
    path = Path(tree) / "tools" / "nxconfig" / "version.py"
    if not path.is_file():
        raise WorkspaceError(f"{tree} is no neXtBSW tree: {path} is missing")
    spec = importlib.util.spec_from_file_location("nx_version_for_workspace", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------- the workspace


@dataclass
class Workspace:
    """A customer repository as the two tools see it. `tree` is the neXtBSW tree this run uses."""

    root: Path
    tree: Path
    pin: dict = field(default_factory=dict)      # raw nextbsw.yaml (checked by tools/nxconfig, not here)
    pin_error: str = ""                          # the pin file could not be parsed at all

    @property
    def pin_file(self) -> Path:
        return self.root / PIN_FILE

    @property
    def list_file(self) -> Path:
        return self.root / CI_DIR / LIST_NAME

    @property
    def pipeline_file(self) -> Path:
        return self.root / CI_DIR / PIPELINE_NAME

    @property
    def coverage_file(self) -> Path:
        """Coverage thresholds of the workspace's own code (TOOL-217, gap N19)."""
        return self.root / CI_DIR / COVERAGE_NAME

    @property
    def misra_dir(self) -> Path:
        """Deviations, baseline and optional rules of the static analysis of the workspace (TOOL-217, gap N19)."""
        return self.root / CI_DIR / MISRA_DIR

    def foreign_dirs(self) -> list[Path]:
        """Folders inside the workspace that are not the customer's code: the pinned tree, if it lies inside (a
        submodule `nextbsw/`), the build root and the environment. Coverage and static analysis never count them
        (TOOL-217)."""
        found = []
        for folder in (self.tree, self.build_root, self.venv):
            folder = Path(folder).resolve()
            if folder != self.root and folder.is_relative_to(self.root):
                found.append(folder)
        return found

    def source_roots(self) -> list[str]:
        """`search.modules` of the pin relative to the workspace - where own sources not compiled by any build are
        looked for (static analysis, "uncovered"). Empty without the field: the whole workspace is not walked."""
        entries = ((self.pin.get("search") or {}).get("modules") or [])
        out = []
        for entry in entries:
            folder = (self.root / str(entry)).resolve()
            if folder.is_dir() and folder != self.root and folder.is_relative_to(self.root):
                out.append(folder.relative_to(self.root).as_posix())
        return out

    @property
    def presets_file(self) -> Path:
        return self.root / "CMakePresets.json"

    @property
    def build_root(self) -> Path:
        return self.root / BUILD_SUBDIR

    @property
    def venv(self) -> Path:
        return self.root / VENV_DIR

    @property
    def pinned_version(self) -> str | None:
        value = (self.pin.get("nextbsw") or {}).get("version")
        return str(value) if value is not None else None

    def module_dirs(self) -> list[Path]:
        """Folders that may hold the customer's module manifests: `search.modules` of the pin, else the root.

        Only used to check the field `modules` of his test list - `tools/nxconfig` reads the same paths to find
        the modules themselves (ARCH-56).
        """
        entries = ((self.pin.get("search") or {}).get("modules") or [])
        found = [(self.root / str(e)).resolve() for e in entries]
        found = [f for f in found if f.is_dir()]
        return found or [self.root]

    @property
    def pinned_path(self) -> str | None:
        value = (self.pin.get("nextbsw") or {}).get("path")
        return str(value) if value is not None else None


def open_workspace(folder: Path, tree: Path) -> Workspace:
    """Reads `<folder>/nextbsw.yaml`. WorkspaceError if the folder is no workspace at all.

    A pin that parses but is wrong is **not** an error here - `pin_findings` names every deviation with its
    reason, so a report can show them all instead of stopping at the first.
    """
    folder = Path(folder).resolve()
    if not folder.is_dir():
        raise WorkspaceError(f"{folder} is no folder")
    pin_file = folder / PIN_FILE
    if not pin_file.is_file():
        raise WorkspaceError(f"{folder} is no workspace: {PIN_FILE} is missing "
                             f"(a customer repository holds the pin at its root, ARCH-56)")
    try:
        data = yaml.safe_load(pin_file.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return Workspace(folder, Path(tree).resolve(), {}, f"{pin_file}: {exc}")
    if not isinstance(data, dict):
        return Workspace(folder, Path(tree).resolve(), {}, f"{pin_file}: no mapping at the top level")
    return Workspace(folder, Path(tree).resolve(), data)


# ---------------------------------------------------------------------------- the pin (ARCH-55/ARCH-56)


@dataclass
class PinFinding:
    status: str      # "ok" | "fail"
    text: str


def pin_findings(ws: Workspace) -> list[PinFinding]:
    """Checks `nextbsw.yaml` against the tree this run uses: schema, path, version.

    This is the part of the pin a **pipeline** has to see (customer-project.md 5.3): the version of the tree the
    workspace is built with must be the version the workspace pinned, and the path must point at exactly that
    tree. Everything else about the pin (search paths, profile, overlays) is checked by `tools/nxconfig` when the
    configuration is read - this tool does not repeat it.
    """
    if ws.pin_error:
        return [PinFinding("fail", f"{PIN_FILE} nicht lesbar: {ws.pin_error}")]
    found: list[PinFinding] = []
    schema = ws.pin.get("schema_version")
    if schema != 1:
        found.append(PinFinding("fail", f"{PIN_FILE}: schema_version {schema!r} statt 1"))
    pinned_path = ws.pinned_path
    if not pinned_path:
        found.append(PinFinding("fail", f"{PIN_FILE}: nextbsw.path fehlt"))
    else:
        target = (ws.root / pinned_path).resolve()
        if not _same(target, ws.tree):
            found.append(PinFinding("fail", f"{PIN_FILE}: nextbsw.path zeigt auf {target}, dieser Lauf benutzt "
                                            f"aber den Baum {ws.tree} - Werkzeuge des gepinnten Baums benutzen"))
    pinned_version = ws.pinned_version
    try:
        tree_version = version_module(ws.tree).read_version(ws.tree)
    except Exception as exc:                                    # VersionError or a tree without VERSION
        found.append(PinFinding("fail", f"Version des Baums {ws.tree} nicht lesbar: {exc}"))
        tree_version = None
    if not pinned_version:
        found.append(PinFinding("fail", f"{PIN_FILE}: nextbsw.version fehlt"))
    elif tree_version is not None and pinned_version != tree_version:
        found.append(PinFinding("fail", f"{PIN_FILE} pinnt neXtBSW {pinned_version}, der Baum {ws.tree} ist aber "
                                        f"{tree_version} - Tag v{pinned_version} auschecken oder den Pin bewusst "
                                        f"nachziehen"))
    if not found:
        found.append(PinFinding("ok", f"{PIN_FILE} pinnt neXtBSW {pinned_version}, der Baum {ws.tree} ist "
                                      f"{tree_version}"))
    return found


def _same(a: Path, b: Path) -> bool:
    try:
        return Path(a).resolve() == Path(b).resolve() or Path(a).samefile(b)
    except OSError:
        return False


# ---------------------------------------------------------------------------- reading only


def check_build_root(build_root: Path, tree: Path) -> str:
    """Reason why `build_root` must not be used in a workspace run, "" if it is fine.

    The tree is a released baseline the customer only reads (customer-project.md 5.1). A build directory inside it
    would write generated code, object files and CMake caches into it, and the next pin move would find a dirty
    submodule.
    """
    build_root = Path(build_root).resolve()
    tree = Path(tree).resolve()
    if build_root == tree or build_root.is_relative_to(tree):
        return (f"Bau-Wurzel {build_root} liegt im neXtBSW-Baum {tree}; ein Workspace-Lauf liest den Baum nur "
                f"(--build-root auf einen Ordner im Workspace setzen)")
    return ""


def snapshot(folder: Path, skip_dirs=(".git", ".venv", "build", "__pycache__")) -> dict[str, tuple[int, int]]:
    """{relative path: (size, mtime_ns)} of every file below `folder` - proof that a run wrote nothing.

    Used by the host test of this module; kept here so tools/ci can use the same proof.
    """
    folder = Path(folder).resolve()
    found: dict[str, tuple[int, int]] = {}
    for parent, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for name in files:
            path = Path(parent) / name
            try:
                info = path.stat()
            except OSError:
                continue
            found[path.relative_to(folder).as_posix()] = (info.st_size, info.st_mtime_ns)
    return found
