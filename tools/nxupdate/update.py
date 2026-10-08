"""Core of the deliberate, traceable update workflow (TOOL-64, doc/architecture/customer-project.md gap N9,
section 5.5): moving a workspace's pin (`nextbsw.yaml`) from one neXtBSW release to another.

    1. generate every project of the workspace with the OLD pinned tree (no compile),
    2. move the pin: check the target tag's provenance (TOOL-65 rule, gap N10), then update it,
    3. migrate the configuration files that changed format (TOOL-66, gap N11),
    4. generate again with the NEW tree,
    5. report: the generated-file diff, which of it is field-relevant, the changelog excerpt between the tags,
       the `nxconfig check` messages that appeared or disappeared, and the skipped releases,
    6. optionally commit (never push) - nothing here runs without `--write`/`--commit` being asked for.

Why generation and migration are always run as a **subprocess against an explicit tree path**
------------------------------------------------------------------------------------------
neXtBSW's own rule (`doc/architecture/customer-project.md` section 5.4, N7) is that a tool must run with the
generator code of the release it is judging: the Studio bundles one version, `tools/ci` runs from the tree it
builds. This module holds to the same rule instead of importing `bswgen`/`rtegen`/`nxconfig migrate` as Python
modules of *this* tree: every generation and migration call is `python <tree>/tools/<...>` for whichever tree
(old or new) the step is about, exactly what `CMakeLists.txt` does at configure time
(`execute_process(... tools/bswgen/bswgen.py ...)`). A synthetic tree used in a host test only has to provide
those scripts at the same relative paths - it need not be a working neXtBSW checkout otherwise.

The one exception is the **tag check** (`pincheck.check_tree`): it is pure `git` fact-finding, deliberately
written to work on any tree without needing that tree's own code (see its module docstring), and it is *the*
gate this module already trusts for the release rule (TOOL-65). Calling it in-process (from this tree's copy)
is therefore the same rule, not a shortcut around it.

Why a dry run never touches the customer's submodule checkout
---------------------------------------------------------------
The default is a dry run that produces the report and changes nothing (project rule: "nichts committet ... ohne
ausdruecklichen Schalter"). To still generate the "after" side, a dry run adds a throwaway **`git worktree`** of
the pinned tree at the target tag (`add_worktree_at_tag`) instead of checking out the real submodule in place - a
worktree shares the pinned tree's object store and refs (so it sees the same `origin/master` the release rule
checks against), needs no network of its own and never touches the workspace or the submodule's own checkout.
Only `--write` moves the real submodule (`checkout_tree_at_tag`) and edits `nextbsw.yaml`.

Why "after" generation reads the workspace files as they are, before a migration is written
-----------------------------------------------------------------------------------------------
A format migration changes how a value is *written* (RTE-22: `components[].path` becomes relative to `rte.yaml`
instead of the repository root), not what is *generated* - the tree keeps reading the old format through a
documented fallback until the migration is applied, and RTE-22's own acceptance measurement was a byte-identical
generated image before and after. So generating "after" from the pre-migration files with the NEW tree produces
the same output a post-migration generation would - the migration step itself (run separately, see `run_migrate`)
is what proves and shows that notation change, and the migrated content is written only under `--write`. If a
future migration ever changes generated *output* (not just notation), that is caught the same way any other real
change is: as an entry in `diff_trees`, and this assumption is worth re-checking then (see README, open points).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))            # tools/ - nxconfig lives here as a sibling package

from nxconfig import pincheck                   # noqa: E402 - tag/provenance rule (TOOL-65, gap N10)
from nxconfig import version as nxversion       # noqa: E402
from nxconfig import migrate as nxmigrate       # noqa: E402 - only to collect project.yaml files (see list_projects)
from nxconfig import yamledit                   # noqa: E402 - editing nextbsw.yaml without losing its layout

PIN_FILE = "nextbsw.yaml"
UPDATE_DOC_DIR = ("doc", "nextbsw-updates")
GIT_TIMEOUT = 60
GEN_TIMEOUT = 300


class UpdateError(Exception):
    """The update cannot proceed at all (bad pin, unresolvable target, refused tag). Nothing was written."""


# ---------------------------------------------------------------------------- the target of --to (TOOL-232)


def _rctag():
    """tools/ci/rctag.py - release tags vX.Y.Z and release candidates vX.Y.Z-rcN - loaded from its file (standard
    library only; the tools/ci package itself is not imported)."""
    import importlib.util

    module = sys.modules.get("nx_rctag")
    if module is None:
        spec = importlib.util.spec_from_file_location("nx_rctag", HERE.parent / "ci" / "rctag.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return module


def target_of(text: str) -> tuple[str, str]:
    """`--to` -> (version written into the pin, git ref of the target tree).

    `1.5.0` or `v1.5.0` -> ("1.5.0", "v1.5.0"); a release candidate `v1.5.0-rc1` (or `1.5.0-rc1`) -> ("1.5.0",
    "v1.5.0-rc1"): the pin keeps MAJOR.MINOR.PATCH (it has to equal the tree's VERSION, ARCH-55), the tree is moved
    to the candidate tag (TOOL-232, Release R1 = v1.0.0-rc1). Anything else (v1.5.0-rc, v1.5.0-beta1) is refused."""
    tag = text if str(text).startswith("v") else f"v{text}"
    found = _rctag().parse(tag)
    if found is None:
        raise UpdateError(f"{text!r} is neither a version MAJOR.MINOR.PATCH nor a release tag vX.Y.Z or vX.Y.Z-rcN")
    return found[0], tag


def tag_version(tree: Path, tag: str) -> str | None:
    """The VERSION file of `tree` at `tag` (first line, stripped); None if the tag or the file is not there."""
    done = _git(["-C", str(tree), "show", f"{tag}:{nxversion.VERSION_FILE}"])
    if done.returncode != 0:
        return None
    lines = done.stdout.splitlines()
    return lines[0].strip() if lines else ""


def check_target_version(found: str | None, version: str, tag: str, where: str) -> None:
    """The version part of the target tag must be the VERSION of the tree it names (a candidate v1.0.1-rc1 on a
    tree with VERSION 1.0.0 is refused before anything moves)."""
    if found is None:
        raise UpdateError(f"{tag} not found in {where}, or the tree there has no {nxversion.VERSION_FILE} "
                          f"(git fetch --tags, or check the name)")
    if found != version:
        raise UpdateError(f"{tag} names version {version}, but {nxversion.VERSION_FILE} in {where} at {tag} is "
                          f"{found!r}; the pin would not match its tree")


# ---------------------------------------------------------------------------- small git helpers


def _git(args: list[str], cwd: Path | None = None, timeout: int = GIT_TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd) if cwd else None, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def git_lines(tree: Path, *args: str) -> list[str]:
    done = _git(["-C", str(tree), *args])
    return done.stdout.splitlines() if done.returncode == 0 else []


def _clear_readonly_and_retry(func, target, _exc):
    """Error handler for `rmtree`: git marks objects under `.git/objects` read-only, which plain `rmtree` cannot
    delete on Windows (`PermissionError`/`WinError 5`) - clear the bit and retry once. Same signature works for
    both `onexc` (Python 3.12+, an exception instance) and the older `onerror` (an `exc_info` tuple): neither is
    used here, only the position matters."""
    try:
        os.chmod(target, stat.S_IWRITE)
        func(target)
    except OSError:
        pass


def rmtree(path: Path) -> None:
    """`shutil.rmtree` that also clears up a scratch directory holding a git checkout or worktree on Windows."""
    try:
        shutil.rmtree(path, onexc=_clear_readonly_and_retry)         # Python 3.12+
    except TypeError:
        shutil.rmtree(path, onerror=_clear_readonly_and_retry)       # older Python


# ---------------------------------------------------------------------------- the pin


@dataclass
class Pin:
    """What this module needs from `nextbsw.yaml`: read leniently, the same way `pincheck` reads it."""

    file: Path
    version: str
    source: str
    path: str

    @property
    def tree(self) -> Path:
        return (self.file.parent / self.path).resolve()


def load_pin(workspace: Path) -> Pin:
    """The pin of `workspace` (a folder or a `nextbsw.yaml`). Raises `UpdateError` if it cannot be used."""
    file = Path(workspace).resolve()               # TOOL-215: a relative path broke relative_to() further down
    file = file / PIN_FILE if file.is_dir() else file
    if not file.is_file():
        raise UpdateError(f"{file} not found: this is not a workspace (no {PIN_FILE})")
    data, _, problem = pincheck.load_pin(file)
    if problem:
        raise UpdateError(problem)
    section = data.get("nextbsw") if isinstance(data.get("nextbsw"), dict) else {}
    version = section.get("version")
    path = section.get("path")
    source = section.get("source") if section.get("source") in ("submodule", "archive") else "submodule"
    if not isinstance(version, str) or not nxversion.is_version(version):
        raise UpdateError(f"{file}: nextbsw.version is missing or not MAJOR.MINOR.PATCH")
    if not isinstance(path, str) or not path:
        raise UpdateError(f"{file}: nextbsw.path is missing")
    return Pin(file, version, source, path)


def write_pin_version(pin: Pin, new_version: str) -> None:
    """Raises `nextbsw.version` in the pin file in place, keeping every comment and the author's layout."""
    doc, problem = yamledit.read(pin.file)
    if doc is None:
        raise UpdateError(f"{pin.file}: {problem}")
    doc.set(("nextbsw", "version"), new_version, f"nextbsw.version: {pin.version} -> {new_version}")
    pin.file.write_text(doc.result(), encoding="utf-8", newline="")


# ---------------------------------------------------------------------------- moving the pin


def add_worktree_at_tag(source_tree: Path, version: str, dest: Path) -> Path:
    """A throwaway `git worktree` of `source_tree` at `v<version>` - used for a dry run so the real submodule
    checkout is never touched. A worktree shares the source's object store and refs (including its
    `origin/master` remote-tracking ref, which the release rule needs), unlike a fresh clone of it - and it costs
    no network and no extra objects, whether `source_tree` was ever fetched from a real remote or not."""
    tag = nxversion.tag_of(version)
    dest.parent.mkdir(parents=True, exist_ok=True)
    done = _git(["-C", str(source_tree), "worktree", "add", "--quiet", "--detach", str(dest), tag],
               timeout=GIT_TIMEOUT)
    if done.returncode != 0:
        raise UpdateError(f"cannot materialise {tag} from {source_tree} into {dest}: {done.stderr.strip()}")
    return dest


def remove_worktree(source_tree: Path, dest: Path) -> None:
    """Undoes `add_worktree_at_tag`. Best effort: `dest` is about to be deleted by the caller either way; this
    only avoids leaving stale worktree administration behind in `source_tree/.git/worktrees/`."""
    try:
        done = _git(["-C", str(source_tree), "worktree", "remove", "--force", str(dest)], timeout=GIT_TIMEOUT)
        if done.returncode != 0:
            # git refuses to remove a worktree that holds submodules (populate_submodules, TOOL-215): delete the
            # folder and let git forget the administration of the vanished worktree
            if Path(dest).exists():
                rmtree(Path(dest))
            _git(["-C", str(source_tree), "worktree", "prune"], timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        pass


def populate_submodules(source_tree: Path, worktree: Path, *, offline: bool = False) -> list[str]:
    """Checks out the submodules of a fresh worktree (`external/openbsw`) - a worktree starts without them, and
    the generators need them (bswgen reads the derivative's device header from external/openbsw; DOC-36/TOOL-215).

    Only submodules the pinned tree itself has initialised are populated, each **cloned from that local checkout**
    (no network, objects shared by git's local clone) at the commit the target tag records. Only if that commit is
    not in the local checkout and the run is not `offline`, the submodule's configured URL is used instead.
    Returns the problems (empty: every submodule is there)."""
    names = {}
    for line in git_lines(worktree, "config", "-f", ".gitmodules", "--get-regexp", r"^submodule\..*\.path$"):
        key, _, path = line.partition(" ")
        names[path.strip()] = key[len("submodule."):-len(".path")]
    problems = []
    for line in git_lines(worktree, "submodule", "status"):
        if not line.startswith("-"):
            continue                                # already there (or not a plain uninitialised entry)
        parts = line[1:].split()
        if len(parts) < 2:
            continue
        path = parts[1]
        source = Path(source_tree) / path
        if not (source / ".git").exists():
            continue                                # not initialised in the pinned tree either: not needed there
        name = names.get(path, path)
        local = _git(["-C", str(worktree), "-c", "protocol.file.allow=always",
                      "-c", f"submodule.{name}.url={source.as_posix()}", "submodule", "update", "--init", "--quiet",
                      "--", path], timeout=GEN_TIMEOUT)
        if local.returncode == 0:
            continue
        if not offline:
            remote = _git(["-C", str(worktree), "submodule", "update", "--init", "--quiet", "--", path],
                          timeout=GEN_TIMEOUT)
            if remote.returncode == 0:
                continue
            problems.append(f"submodule {path} of the target tree could not be checked out: "
                            f"{remote.stderr.strip() or local.stderr.strip()}")
        else:
            problems.append(f"submodule {path} of the target tree could not be checked out from {source} "
                            f"(--offline): {local.stderr.strip()}")
    return problems


def checkout_tree_at_tag(tree: Path, version: str) -> None:
    """Really moves the pinned tree's own checkout to the tag - only ever called under `--write`."""
    tag = nxversion.tag_of(version)
    done = _git(["-C", str(tree), "checkout", "--quiet", tag], timeout=GIT_TIMEOUT)
    if done.returncode != 0:
        raise UpdateError(f"git checkout {tag} in {tree} failed: {done.stderr.strip()}")


def update_tree_submodules(tree: Path) -> list[str]:
    """After `checkout_tree_at_tag`: the submodules the customer has initialised follow the new gitlinks
    (`git submodule update --recursive`; a commit already present needs no network). Returns the problems."""
    try:
        done = _git(["-C", str(tree), "submodule", "update", "--recursive", "--quiet"], timeout=GEN_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        return [f"git submodule update in {tree} failed: {exc}"]
    return [] if done.returncode == 0 else [f"git submodule update in {tree} failed: {done.stderr.strip()}"]


def fetch_tags(tree: Path) -> None:
    """Best effort: makes a newly created tag visible. Never raises - `check_target` reports a missing tag."""
    try:
        _git(["-C", str(tree), "fetch", "--tags", "--quiet"], timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        pass


def check_target(tree: Path, version: str, *, source: str = "submodule", release: bool = False,
                 offline: bool = False) -> pincheck.PinStatus:
    """The release rule (TOOL-65, gap N10) applied to the *target* tree/version, before anything is moved."""
    if not offline and source == "submodule":
        fetch_tags(tree)
    return pincheck.check_tree(tree, version, release=release, source=source)


# ---------------------------------------------------------------------------- projects of the workspace


def list_projects(root: Path) -> list[Path]:
    """Every `project.yaml` of the workspace, in a stable order (reuses the collector `nxconfig migrate` uses -
    same rules for what is build output and what is a project, see TOOL-66)."""
    return [f for f, kind in nxmigrate.collect(root, root) if kind.name == "project"]


# ---------------------------------------------------------------------------- generation


@dataclass
class ProcResult:
    ok: bool
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""
    note: str = ""                              # e.g. "script not found: skipped"


def _run(python: str, script: Path, args: list[str], timeout: int = GEN_TIMEOUT) -> ProcResult:
    if not script.is_file():
        return ProcResult(True, note=f"{script} not found (generator missing in this tree): skipped")
    try:
        done = subprocess.run([python, str(script), *args], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return ProcResult(False, -1, "", str(exc), note=f"could not start {script}: {exc}")
    return ProcResult(done.returncode == 0, done.returncode, done.stdout, done.stderr)


# (subfolder under the project's generation output, relative script path from the tree root, extra CLI args)
GENERATORS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("board", ("tools", "bswgen", "bswgen.py")),
    ("rte", ("tools", "rtegen", "rtegen.py")),
)


@dataclass
class ProjectGeneration:
    project: Path
    out_dir: Path
    results: dict[str, ProcResult] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results.values())


def generate_project(python: str, tree: Path, project_file: Path, out_dir: Path) -> ProjectGeneration:
    """Runs bswgen and rtegen of `tree` on `project_file`, writing under `out_dir/board` and `out_dir/rte`."""
    results = {}
    for name, rel in GENERATORS:
        script = Path(tree, *rel)
        sub_out = out_dir / name
        sub_out.mkdir(parents=True, exist_ok=True)
        args = [str(project_file), str(sub_out)]
        if name == "rte":
            args += ["--repo", str(tree)]        # RTE-22: only the fallback for an ECU description of format 1
        results[name] = _run(python, script, args)
    return ProjectGeneration(project_file, out_dir, results)


@dataclass
class WorkspaceGeneration:
    tree: Path
    out_dir: Path
    projects: list[ProjectGeneration] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(p.ok for p in self.projects)

    @property
    def problems(self) -> list[str]:
        out = []
        for p in self.projects:
            for name, result in p.results.items():
                if result.note:
                    out.append(f"{p.project}: {name}: {result.note}")
                elif not result.ok:
                    out.append(f"{p.project}: {name}: exit {result.returncode}: "
                              f"{(result.stderr or result.stdout).strip()}")
        return out


def generate_workspace(python: str, tree: Path, root: Path, out_dir: Path) -> WorkspaceGeneration:
    """Generates every project of the workspace with `tree`, into `out_dir` (one subfolder per project, by its
    path relative to `root`) - configure/generation only, nothing is compiled."""
    out_dir.mkdir(parents=True, exist_ok=True)
    root = Path(root).resolve()                   # list_projects() returns resolved paths (TOOL-215)
    result = WorkspaceGeneration(tree, out_dir)
    for project in list_projects(root):
        rel = project.parent.relative_to(root)
        result.projects.append(generate_project(python, tree, project, out_dir / rel))
    return result


# ---------------------------------------------------------------------------- the "after" workspace (TOOL-215)
#
# Every tool of a tree checks the workspace it is pointed at (nxconfig/workspace.py, since TOOL-85/TOOL-87): the
# pin must name the tree the tool runs from and the version in that tree's VERSION file. The "after" side runs the
# TARGET tree's generators while the real workspace still pins the OLD tree (dry run: always; --write: until the
# pin is moved) - so on a real workspace both generators refused with "the workspace pins the tree ..., but this
# tool runs from ..." and every generated file showed up as "removed" (DOC-36, 2026-10-02). The stub generators of
# test_nxupdate.py never looked at the pin, which is why the unit tests did not see it.
#
# The "after" side therefore runs against a **staged copy** of the workspace: the workspace's own files (not the
# tree, not .git, not build output), with a pin that names the target version, and the target tree at the place
# the pin points to - so it is exactly the workspace the customer has after the update, and the target tree's
# tools accept it under their own, unchanged rules (no switch in any generator, which also keeps already released
# trees such as v0.2.0 usable as targets). Relative references from the workspace into the tree (for example
# rte.yaml `components[].path: ../../nextbsw/swc/...`) resolve into the target tree, as they will after the update.
#
# The target tree is placed there as a `git worktree` of the pinned tree at the target tag (dry run and --write:
# the real submodule checkout is never linked into the scratch folder), or as a directory link to an explicit
# `--tree`. Paths of the copy that end up in generated files or messages are mapped back to the real workspace
# (`StagedWorkspace.normalise`), so only real differences remain in the report.

COPY_SKIP_DIRS = {".git", ".hg", ".svn", ".venv", "venv", "__pycache__", "node_modules", ".nextbsw-backup"}


@dataclass
class StagedWorkspace:
    root: Path                                  # the copy of the workspace (its nextbsw.yaml pins the target)
    slot: Path                                  # where the copy's pin points: the target tree is placed here
    pin_path: str                               # nextbsw.path written into the copy's pin
    real_root: Path
    real_tree: Path
    target_tree: Path | None = None             # the real location behind `slot` if it is a link (--tree)

    def project(self, real_project: Path) -> Path:
        return self.root / Path(real_project).resolve().relative_to(self.real_root)

    def pairs(self) -> list[tuple[str, str]]:
        """(path in the staged copy, path in the real workspace), every spelling a tool may write, longest first."""
        mapping = [(self.root, self.real_root)]
        if not self.slot.is_relative_to(self.root):
            mapping.append((self.slot, self.real_tree))
        if self.target_tree is not None and self.target_tree != self.real_tree:
            mapping.append((self.target_tree, self.real_tree))
        out = []
        for staged, real in mapping:
            out.append((str(staged), str(real)))
            out.append((staged.as_posix(), real.as_posix()))
            if "\\" in str(staged):
                out.append((str(staged).replace("\\", "\\\\"), str(real).replace("\\", "\\\\")))
        return sorted(set(out), key=lambda p: len(p[0]), reverse=True)

    def normalise(self, text: str) -> str:
        for staged, real in self.pairs():
            text = text.replace(staged, real)
        return text


def after_layout(pin: Pin, dest: Path) -> StagedWorkspace:
    """Where the staged copy and the target tree go below `dest` - computed before anything is placed, so the
    dry-run worktree can be added at its final place. A relative `nextbsw.path` that leaves the workspace
    (`../nextbsw`) keeps its shape: the copy is nested deep enough that the slot still lies below `dest`."""
    dest = Path(dest).resolve()
    rel = Path(pin.path)
    if rel.is_absolute():
        root = dest / "ws"
        slot = dest / "tree"
        pin_path = os.path.relpath(slot, root).replace("\\", "/")
    else:
        ups = 0
        for part in Path(os.path.normpath(rel)).parts:
            if part != "..":
                break
            ups += 1
        root = dest.joinpath(*(["up"] * ups), "ws")
        slot = Path(os.path.normpath(root / rel))
        pin_path = pin.path
    return StagedWorkspace(root, slot, pin_path, pin.file.parent.resolve(), pin.tree)


def copy_workspace(pin: Pin, staged: StagedWorkspace, new_version: str) -> None:
    """Copies the workspace's own files into `staged.root` (not the pinned tree, not version control, not build
    output - the same pruning `nxconfig migrate` uses for build folders) and writes the copy's pin: the target
    version, and the tree at `staged.slot`."""
    real_tree = pin.tree
    staged_root = staged.root

    def ignore(folder: str, names: list[str]) -> set[str]:
        here = Path(folder)
        skipped = set()
        for name in names:
            path = here / name
            if name == ".git":                  # also a .git *file* (a nested checkout): never copy git admin
                skipped.add(name)
                continue
            if not path.is_dir():
                continue
            if name in COPY_SKIP_DIRS or nxmigrate.is_build_dir(path):
                skipped.add(name)
                continue
            resolved = path.resolve()
            # the pinned tree (also through a link), and the scratch folder if someone put it inside the workspace
            if resolved == real_tree or staged_root.is_relative_to(resolved):
                skipped.add(name)
        return skipped

    shutil.copytree(staged.real_root, staged.root, ignore=ignore, dirs_exist_ok=True)
    pin_copy = staged.root / pin.file.name
    doc, problem = yamledit.read(pin_copy)
    if doc is None:
        raise UpdateError(f"{pin.file}: {problem}")
    doc.set(("nextbsw", "version"), new_version, f"nxupdate staging: target {new_version}")
    if staged.pin_path != pin.path:
        doc.set(("nextbsw", "path"), staged.pin_path, "nxupdate staging: target tree")
    pin_copy.write_text(doc.result(), encoding="utf-8", newline="")


def link_dir(link: Path, target: Path) -> None:
    """`link` -> `target` as a directory junction (Windows, needs no privilege) or a symlink."""
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        try:
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
            return
        except (ImportError, AttributeError, OSError):
            done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True,
                                  text=True, errors="replace")
            if done.returncode == 0:
                return
            raise UpdateError(f"cannot link {link} to {target}: {done.stdout.strip()} {done.stderr.strip()}")
    os.symlink(target, link, target_is_directory=True)


def unlink_dir(link: Path) -> bool:
    """Removes a link made by `link_dir` without touching what it points to. True if nothing is left there."""
    try:
        if os.path.islink(link):
            os.unlink(link)
        elif getattr(os.path, "isjunction", lambda _p: False)(link):
            os.rmdir(link)
        elif os.path.lexists(link):
            return False                        # not a link: never delete through it
    except OSError:
        return False
    return not os.path.lexists(link)


# ---------------------------------------------------------------------------- diff of the generated files


@dataclass
class FileDiff:
    path: str                                   # relative to the generation root, forward slashes
    change: str                                  # "added" | "removed" | "changed"
    added: int = 0
    removed: int = 0
    diff: str = ""
    field_relevant: bool = False
    field_reason: str = ""


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def _files_of(root: Path) -> dict[str, Path]:
    if not root.is_dir():
        return {}
    return {p.relative_to(root).as_posix(): p for p in root.rglob("*") if p.is_file()}


def normalise_generated(out_dir: Path, staged: StagedWorkspace) -> int:
    """Rewrites the "after" generation in place (it lives in the scratch folder) so that it reads as if it had
    been generated in the real workspace: every path of the staged copy becomes the real one. A generator that
    lists its files with a SHA-256 (PLAT-79 identity, `*.sha256`) hashed the staged text, so in a second pass every
    occurrence of a rewritten file's old hash is replaced by the hash of its rewritten bytes - without knowing any
    manifest format. Returns the number of rewritten files."""
    import hashlib
    rehash: dict[str, str] = {}
    for path in (p for p in Path(out_dir).rglob("*") if p.is_file()):
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        new = staged.normalise(text)
        if new != text:
            data = new.encode("utf-8")
            path.write_bytes(data)
            rehash[hashlib.sha256(raw).hexdigest()] = hashlib.sha256(data).hexdigest()
    if rehash:
        pattern = re.compile("|".join(rehash))
        for path in (p for p in Path(out_dir).rglob("*") if p.is_file()):
            try:
                text = path.read_bytes().decode("utf-8")
            except UnicodeDecodeError:
                continue
            new = pattern.sub(lambda m: rehash[m.group(0)], text)
            if new != text:
                path.write_bytes(new.encode("utf-8"))
    return len(rehash)


def diff_trees(before: Path, after: Path) -> list[FileDiff]:
    """Every generated file that was added, removed or changed between two generation runs, with a unified diff."""
    before_files, after_files = _files_of(before), _files_of(after)
    out = []
    for rel in sorted(set(before_files) | set(after_files)):
        b, a = before_files.get(rel), after_files.get(rel)
        if b is None:
            text = _read_text(a)
            lines = list(text.splitlines(keepends=True)) if text is not None else ["(binary file)\n"]
            out.append(FileDiff(rel, "added", added=len(lines),
                                diff="".join(_udiff([], lines, rel))))
        elif a is None:
            text = _read_text(b)
            lines = list(text.splitlines(keepends=True)) if text is not None else ["(binary file)\n"]
            out.append(FileDiff(rel, "removed", removed=len(lines), diff="".join(_udiff(lines, [], rel))))
        else:
            text_b, text_a = _read_text(b), _read_text(a)
            if text_b == text_a:
                continue
            if text_b is None or text_a is None:
                out.append(FileDiff(rel, "changed", diff="(binary file differs)\n"))
                continue
            lines = list(_udiff(text_b.splitlines(keepends=True), text_a.splitlines(keepends=True), rel))
            added = sum(1 for line in lines if line.startswith("+") and not line.startswith("+++"))
            removed = sum(1 for line in lines if line.startswith("-") and not line.startswith("---"))
            out.append(FileDiff(rel, "changed", added, removed, "".join(lines)))
    for item in out:
        reason = field_relevance(item)
        if reason:
            item.field_relevant, item.field_reason = True, reason
    return out


def _udiff(before_lines, after_lines, path: str):
    import difflib
    return difflib.unified_diff(before_lines, after_lines, fromfile=path, tofile=path)


# ---------------------------------------------------------------------------- field-relevant changes
#
# doc/architecture/customer-project.md section 5.5: "the changes that decide whether an ECU in the field can take
# the new software without a bootloader update" - memory layout, bootloader identity, CAN IDs, diagnostic
# addresses, record formats. There is no registry of which generated file holds which of these across every
# board and project, so the rule here is a heuristic on the generated file's *path* and, for a changed file, on
# the *changed lines* of its diff - deliberately over-inclusive: a false positive costs one look in the report, a
# false negative could cost a bootloader update in the field. Documented in tools/nxupdate/README.md; extend the
# tuples below rather than special-casing a board or project name (the same rule everywhere, per CLAUDE.md).

FIELD_PATH_HINTS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"(memory|flash|linker|\.ld$|layout|partition)", re.I), "memory/flash layout"),
    (re.compile(r"(bootload|blupdate|\bbl[_-])", re.I), "bootloader identity"),
    (re.compile(r"(can[_-]?id|\.dbc$|signal)", re.I), "CAN IDs/signals"),
    (re.compile(r"(diag|\bdid\b|dtc|uds)", re.I), "diagnostic addresses"),
    (re.compile(r"(nvm|record|eeprom)", re.I), "record format / NVM layout"),
)
FIELD_CONTENT_HINTS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"\bCAN_ID_\w+|\bCanId\w*", re.I), "CAN ID value"),
    (re.compile(r"\bDID_\w+|\bDid\w*Addr", re.I), "diagnostic identifier"),
    (re.compile(r"FLASH_(ORIGIN|START|SIZE|ADDR)\w*|\bMEMORY\s*\{", re.I), "memory layout constant"),
    (re.compile(r"BOOTLOADER_(VERSION|ID|MAGIC)\w*", re.I), "bootloader identity constant"),
    (re.compile(r"\bNVM_(RECORD|BLOCK)_\w+", re.I), "NVM record layout"),
)


def field_relevance(item: FileDiff) -> str:
    for pattern, reason in FIELD_PATH_HINTS:
        if pattern.search(item.path):
            return reason
    for pattern, reason in FIELD_CONTENT_HINTS:
        for line in item.diff.splitlines():
            if line[:1] in "+-" and not line[:3] in ("+++", "---") and pattern.search(line):
                return reason
    return ""


# ---------------------------------------------------------------------------- migration (TOOL-66, gap N11)


@dataclass
class MigrateRun:
    ran: bool
    returncode: int = 0
    output: str = ""


def run_migrate(python: str, tree: Path, root: Path, *, write: bool) -> MigrateRun:
    """`<tree>/tools/nxconfig migrate <root> [--write]` - the tree's own migration engine, so a future release's
    steps run with a future release's rules, not this one's."""
    script = Path(tree, "tools", "nxconfig")
    if not (script / "__main__.py").is_file():
        return MigrateRun(False, output=f"(no tools/nxconfig in {tree}: migration step skipped)")
    args = [python, str(script), "migrate", str(root)]
    if write:
        args.append("--write")
    try:
        done = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=GEN_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        return MigrateRun(True, -1, f"could not run migrate: {exc}")
    return MigrateRun(True, done.returncode, done.stdout + done.stderr)


# ---------------------------------------------------------------------------- nxconfig check messages new/gone


def check_messages(python: str, tree: Path, project: Path) -> set[str] | None:
    """The messages `<tree>/tools/nxconfig check <project> --json` reports, as one string per message. None if
    the tool could not be run at all (older/synthetic tree without it) - a soft failure, this section of the
    report is best-effort."""
    script = Path(tree, "tools", "nxconfig")
    if not (script / "__main__.py").is_file():
        return None
    try:
        done = subprocess.run([python, str(script), "check", str(project), "--json"], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=GEN_TIMEOUT)
        messages = json.loads(done.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None
    return {f"{m.get('file') or ''}:{m.get('line') or ''} ({m.get('path') or ''}): {m.get('severity')}: "
            f"{m.get('text')}" for m in messages}


# ---------------------------------------------------------------------------- changelog and skipped releases


def skipped_releases(tree: Path, old_tag: str, new_tag: str) -> list[str]:
    """Release tags strictly between `old_tag` and `new_tag` (reachable from the new one, not from the old one) -
    the releases a customer skips by moving straight to `new_tag`."""
    out = git_lines(tree, "tag", "--merged", new_tag, "--no-merged", old_tag)
    versions = sorted((v for v in (nxversion.version_of_tag(t) for t in out) if v is not None),
                      key=nxversion.parse)
    return [nxversion.tag_of(v) for v in versions if nxversion.tag_of(v) != new_tag]


def changelog_excerpt(tree: Path, old_tag: str, new_tag: str, path: str = "CHANGELOG.md",
                      max_lines: int = 400) -> str:
    """Lines added to `CHANGELOG.md` between the two tags - a mechanical excerpt, not a summary."""
    lines = git_lines(tree, "diff", "--unified=0", old_tag, new_tag, "--", path)
    added = [line[1:] for line in lines if line.startswith("+") and not line.startswith("+++")]
    if not added:
        return f"(no lines added to {path} between {old_tag} and {new_tag}, or the file does not exist)"
    if len(added) > max_lines:
        added = added[:max_lines] + [f"... ({len(added) - max_lines} more line(s), see the tree's own history)"]
    return "\n".join(added)


# ---------------------------------------------------------------------------- the whole run


@dataclass
class UpdateResult:
    workspace: Path
    old_version: str
    new_version: str
    old_tree: Path
    after_tree: Path
    pin_status: pincheck.PinStatus
    file_diffs: list[FileDiff]
    report_text: str
    migrate_after: MigrateRun
    messages_new: dict[str, set[str]]
    messages_gone: dict[str, set[str]]
    skipped: list[str]
    changelog: str
    before_problems: list[str]
    after_problems: list[str]
    written: bool
    committed: bool
    report_file: Path | None

    @property
    def ok(self) -> bool:
        """True: the target tag passed the release rule (TOOL-65) and generation raised no error on either side.
        A `False` result still carries a full report - it says what stopped a `--write` run from happening."""
        return self.pin_status.usable and not self.before_problems and not self.after_problems

    @property
    def field_relevant(self) -> list[FileDiff]:
        return [d for d in self.file_diffs if d.field_relevant]


def run(workspace: Path, new_version: str, *, tree: Path | None = None, write: bool = False, commit: bool = False,
       release: bool = False, offline: bool = False, python: str | None = None,
       scratch: Path | None = None, keep_scratch: bool = False) -> UpdateResult:
    """The update workflow of section 5.5, steps 1-5 always, step 6 (commit) only with `commit=True` (which
    implies `write=True`). Nothing is written to the workspace unless `write` is true; nothing is committed
    unless `commit` is true. `tree`, given explicitly, is used as the target tree as-is (the `source: archive`
    fallback of customer-project.md 4.4: this module does not download an archive) - without it, the target is
    the pinned tree's own git history, at `v<new_version>` (`source: submodule`). `new_version` may also name a
    release candidate `vX.Y.Z-rcN` (TOOL-232, `target_of`): the tree goes to that tag, the pin gets `X.Y.Z`.
    """
    python = python or sys.executable
    write = write or commit
    pin = load_pin(workspace)
    root = pin.file.parent
    if not pin.tree.is_dir():
        raise UpdateError(f"pinned tree {pin.tree} not found ({pin.path}); "
                          f"git submodule update --init --recursive first")
    new_version, new_tag = target_of(new_version)
    old_tag = nxversion.tag_of(pin.version)
    ref = new_tag[len(nxversion.TAG_PREFIX):]      # "1.5.0" or "1.5.0-rc1": the helpers below name the tag v + ref
    if tree is not None:
        try:
            found = nxversion.read_version(Path(tree))
        except nxversion.VersionError:
            found = None
        check_target_version(found, new_version, new_tag, str(tree))

    own_scratch = scratch is None
    scratch = (Path(scratch) if scratch is not None else Path(tempfile.mkdtemp(prefix="nxupdate-"))).resolve()
    worktree_of: Path | None = None                # set if a worktree was added at staged.slot - cleaned up below
    linked: Path | None = None                     # set if staged.slot is a link to --tree - removed below
    after_tree: Path | None = None
    staged = after_layout(pin, scratch / "after-ws")
    setup_problems: list[str] = []                 # placing the target tree (submodules), reported as "after"
    try:
        before_dir, after_dir = scratch / "before", scratch / "after"

        # 1. generate every project with the OLD pinned tree (as it stands: no compile), and collect its
        #    `nxconfig check` messages now - with --write the tree is moved below (TOOL-215).
        before_gen = generate_workspace(python, pin.tree, root, before_dir)
        before_messages = {project: check_messages(python, pin.tree, project) for project in list_projects(root)}

        # 2. move the pin: the target tree first, checked against the release rule (TOOL-65) - nothing about the
        #    workspace changes yet, whatever the finding. The tree the "after" generation runs from is placed at
        #    the slot of the staged workspace copy (TOOL-215, see "the after workspace" above).
        if tree is not None:
            # customer-project.md 4.4, "source: archive stays the documented fallback": nxupdate does not
            # download or unpack an archive itself - a tree materialised by other means is used as it is.
            after_tree, source = Path(tree).resolve(), "archive"
            link_dir(staged.slot, after_tree)
            linked = staged.slot
            staged.target_tree = after_tree
        else:
            source = pin.source
            if source != "submodule":
                raise UpdateError(f"{pin.file}: source: {source} is not moved automatically by nxupdate (no "
                                  "download here); unpack the new release archive yourself and pass --tree "
                                  "<path>, or move the pin by hand")
            if not offline:
                fetch_tags(pin.tree)
            check_target_version(tag_version(pin.tree, new_tag), new_version, new_tag, str(pin.tree))
            if write:
                checkout_tree_at_tag(pin.tree, ref)
                setup_problems += update_tree_submodules(pin.tree)
            # dry run and --write alike: a worktree at the tag in the staged copy - the real submodule checkout
            # is never linked into the scratch folder. A dry run checks the release rule on that worktree.
            add_worktree_at_tag(pin.tree, ref, staged.slot)
            worktree_of = pin.tree
            setup_problems += populate_submodules(pin.tree, staged.slot, offline=offline)
            after_tree = pin.tree if write else staged.slot

        pin_status = check_target(after_tree, ref, source=source, release=release, offline=True)
        # ^ offline here: fetch already happened above (or was skipped on purpose); check_target must not repeat
        #   a network call on the freshly made clone/checkout, whose remote is a local path anyway.

        # 3. migrate (dry run unless write): the NEW tree's own migration engine and rules.
        migrate_after = run_migrate(python, after_tree, root, write=write)

        # 4. generate again with the NEW tree, in the staged copy of the workspace (taken after the migration,
        #    so with --write it holds the migrated files; a dry run reads them as found - see module docstring).
        copy_workspace(pin, staged, new_version)
        after_gen = generate_workspace(python, staged.slot, staged.root, after_dir)

        # 5. report material.
        normalise_generated(after_dir, staged)
        migrate_after.output = staged.normalise(migrate_after.output)
        file_diffs = diff_trees(before_dir, after_dir)
        messages_new, messages_gone = {}, {}
        for project in list_projects(root):
            rel = str(project.parent.relative_to(root))
            before_msgs = before_messages.get(project)
            after_msgs = check_messages(python, staged.slot, staged.project(project))
            if before_msgs is not None and after_msgs is not None:
                after_msgs = {staged.normalise(m) for m in after_msgs}
                new, gone = after_msgs - before_msgs, before_msgs - after_msgs
                if new:
                    messages_new[rel] = new
                if gone:
                    messages_gone[rel] = gone
        skipped = skipped_releases(after_tree, old_tag, new_tag) if _is_git(after_tree) else []
        changelog = changelog_excerpt(after_tree, old_tag, new_tag) if _is_git(after_tree) \
            else "(target tree is no git checkout: no changelog history available)"

        written = False
        if write and pin_status.usable:
            write_pin_version(pin, new_version)
            written = True

        # the report is written to doc/nextbsw-updates/ *before* the commit, so the commit below picks it up
        # together with the pin and the migration (customer-project.md 5.5, step 6: "one change").
        after_problems = [staged.normalise(p) for p in setup_problems + after_gen.problems]
        report_text = _render_report(
            workspace=root, old_version=pin.version, new_version=new_version, old_tag=old_tag, new_tag=new_tag,
            pin_status=pin_status, file_diffs=file_diffs, migrate_after=migrate_after,
            messages_new=messages_new, messages_gone=messages_gone, skipped=skipped, changelog=changelog,
            before_problems=before_gen.problems, after_problems=after_problems,
            written=written, committed=commit and written, write_requested=write)
        report_file = _write_report_file(root, old_tag, new_tag, report_text) if written else None

        committed = False
        if commit and written:
            committed = _commit(root, pin, new_version, old_tag, new_tag)

        return UpdateResult(root, pin.version, new_version, pin.tree, after_tree, pin_status, file_diffs,
                            report_text, migrate_after, messages_new, messages_gone, skipped, changelog,
                            before_gen.problems, after_problems, written, committed, report_file)
    finally:
        safe_to_delete = True
        if linked is not None:
            # the link goes first and on its own: a scratch folder still linking to a tree is never deleted
            safe_to_delete = unlink_dir(linked)
        if worktree_of is not None and not keep_scratch:
            remove_worktree(worktree_of, staged.slot)
        if own_scratch and not keep_scratch and safe_to_delete:
            try:
                rmtree(scratch)
            except OSError:
                pass                             # best effort: a leftover scratch folder is a nuisance, not a bug


def _is_git(tree: Path) -> bool:
    return _git(["-C", str(tree), "rev-parse", "--git-dir"]).returncode == 0


def _commit(root: Path, pin: Pin, new_version: str, old_tag: str, new_tag: str) -> bool:
    """`git add` the pin, the submodule gitlink, whatever the migration changed, and the report; one commit, no
    push (the project rule: agents and this tool never push)."""
    paths = [str(pin.file), pin.path]
    report_dir = root.joinpath(*UPDATE_DOC_DIR)
    if report_dir.is_dir():
        paths.append(str(report_dir))
    done = _git(["-C", str(root), "add", "--", *paths])
    if done.returncode != 0:
        return False
    # TOOL-215: the configuration files `migrate --write` changed (the collector it uses itself); only files the
    # repository already tracks (`add -u`) - a configuration file the customer has not committed yet stays theirs
    config = [str(f) for f, _kind in nxmigrate.collect(root, root) if f != pin.file]
    if config:
        done = _git(["-C", str(root), "add", "-u", "--", *config])
        if done.returncode != 0:
            return False
    message = f"nextbsw: update {old_tag} -> {new_tag}\n\nSee doc/{'/'.join(UPDATE_DOC_DIR[1:])}/{old_tag}-{new_tag}.md"
    done = _git(["-C", str(root), "commit", "--quiet", "-m", message])
    return done.returncode == 0


# ---------------------------------------------------------------------------- report text


def _render_report(*, workspace: Path, old_version: str, new_version: str, old_tag: str, new_tag: str,
                   pin_status: pincheck.PinStatus, file_diffs: list[FileDiff], migrate_after: MigrateRun,
                   messages_new: dict, messages_gone: dict, skipped: list[str], changelog: str,
                   before_problems: list[str], after_problems: list[str], written: bool, committed: bool,
                   write_requested: bool = False) -> str:
    lines = [f"# neXtBSW update {old_tag} -> {new_tag}", "",
            f"Workspace: `{workspace}`  ", f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}  ",
            f"Mode: {'written' if written else 'dry run (nothing changed)'}"
            + (", committed" if committed else ""), ""]

    lines += ["## Pin check (TOOL-65, gap N10)", "", pin_status.headline, ""]
    for finding in pin_status.findings:
        if finding.severity != "ok":
            lines.append(f"- **{finding.severity}** [{finding.code}] {finding}")
    if write_requested and not written:
        lines.append("- **--write was given, but nextbsw.yaml was NOT changed** because the target tag failed "
                     "the release rule above. If `source: submodule`, the pinned tree's checkout may already sit "
                     "on the new commit (git already moved it there to be able to check it) while nextbsw.yaml "
                     "still names the old version - `nxconfig pin`/`nxconfig check` will flag that mismatch until "
                     "it is resolved by hand (checkout the old tag again, or fix the tag and re-run).")
    lines.append("")

    if skipped:
        lines += ["## Releases skipped", "", *(f"- {t}" for t in skipped), ""]

    lines += ["## Changelog excerpt (raw diff of CHANGELOG.md between the tags)", "", "```", changelog, "```", ""]

    lines += ["## Migration (TOOL-66, gap N11)", "", "```", migrate_after.output.strip() or "(no output)", "```", ""]

    if before_problems or after_problems:
        lines += ["## Generation problems", ""]
        lines += [f"- before: {p}" for p in before_problems]
        lines += [f"- after: {p}" for p in after_problems]
        lines.append("")

    lines += ["## nxconfig check: messages that appeared or disappeared", ""]
    if not messages_new and not messages_gone:
        lines.append("(no change, or the comparison could not run - see generation problems above)")
    for project, msgs in sorted(messages_new.items()):
        lines += [f"- {project}: **new**:"] + [f"  - {m}" for m in sorted(msgs)]
    for project, msgs in sorted(messages_gone.items()):
        lines += [f"- {project}: **gone**:"] + [f"  - {m}" for m in sorted(msgs)]
    lines.append("")

    lines += ["## Generated-file diff", ""]
    added = sum(1 for d in file_diffs if d.change == "added")
    removed = sum(1 for d in file_diffs if d.change == "removed")
    changed = sum(1 for d in file_diffs if d.change == "changed")
    lines.append(f"{len(file_diffs)} file(s) differ ({added} added, {removed} removed, {changed} changed); "
                f"{len(_field(file_diffs))} of them field-relevant (see below).")
    lines.append("")
    lines.append("| File | Change | +/- | Field-relevant |")
    lines.append("|---|---|---|---|")
    for d in file_diffs:
        mark = f"yes: {d.field_reason}" if d.field_relevant else ""
        lines.append(f"| {d.path} | {d.change} | +{d.added}/-{d.removed} | {mark} |")
    lines.append("")
    lines.append("ELF size change: not measured (this command generates only, it does not compile; run "
                "`tools/ci run --workspace <dir>` for a full build).")
    lines.append("")

    field = _field(file_diffs)
    lines += ["## Field-relevant changes (section 5.5: decide whether the field can take this without a "
             "bootloader update)", ""]
    if not field:
        lines.append("(none found by the heuristic below)")
    for d in field:
        lines += [f"### {d.path} ({d.field_reason})", "", "```diff", d.diff.rstrip("\n"), "```", ""]

    lines += ["## Full unified diff of every changed generated file", ""]
    for d in file_diffs:
        lines += [f"### {d.path}", "", "```diff", d.diff.rstrip("\n") or "(no textual diff, binary)", "```", ""]

    return "\n".join(lines) + "\n"


def _field(file_diffs: list[FileDiff]) -> list[FileDiff]:
    return [d for d in file_diffs if d.field_relevant]


def _write_report_file(root: Path, old_tag: str, new_tag: str, text: str) -> Path:
    out_dir = root.joinpath(*UPDATE_DOC_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{old_tag}-{new_tag}.md"
    out.write_text(text, encoding="utf-8", newline="")
    return out


def render_summary(result: UpdateResult) -> str:
    """The short text `nxupdate` prints to the console; the full report is the markdown file/text."""
    lines = [f"nxupdate: {result.old_version} -> {result.new_version} ({result.workspace})",
            result.pin_status.headline,
            f"generated files: {len(result.file_diffs)} differ, {len(result.field_relevant)} field-relevant"]
    if result.before_problems:
        lines.append(f"before-generation problems: {len(result.before_problems)}")
    if result.after_problems:
        lines.append(f"after-generation problems: {len(result.after_problems)}")
    lines.append("written: yes" if result.written else "written: no (dry run)")
    if result.written:
        lines.append("committed: yes" if result.committed else "committed: no")
        if result.report_file:
            lines.append(f"report: {result.report_file}")
    return "\n".join(lines)
