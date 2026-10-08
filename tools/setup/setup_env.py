#!/usr/bin/env python3
"""Python environment of a workspace (TOOL-63, doc/architecture/customer-project.md 5.3, gap N8).

A customer works in their own repository (the workspace) with neXtBSW as a submodule on a fixed tag. The tools,
the generators and the build of that workspace run with **the workspace's own** `.venv`, built from the
requirements and the constraints of exactly that pinned tree. This module is the one command that creates it:

    python -m tools.setup venv                 # workspace found by walking up from the current folder
    python -m tools.setup venv <workspace>     # core packages only - no Qt, no Studio
    python -m tools.setup venv <workspace> --studio     # plus tools/requirements-studio.txt
    python -m tools.setup check <workspace>    # report only, writes nothing

Why `tools/setup` and not `tools/ci`: `tools/ci` is the pipeline of *this* tree (checkout, toolchain cache, tests,
report); it runs after an environment exists. Creating the environment of *another* repository is the first step a
customer takes, before any pipeline, and the command must stay readable for someone who does not run CI at all.
Both live side by side and share their pins: the pin parser and the exact versions come from `tools/ci`.

The command is repeatable. It reads the state of `<workspace>/.venv` first and only then decides:

    missing       no `.venv`                                  -> create, install, write the marker
    current       marker matches the tree, packages complete   -> nothing is run at all
    stale         the tree's requirements changed              -> install over it, marker rewritten
    incomplete    marker fits, but packages are missing/wrong  -> install the difference
    broken        `.venv` without an interpreter               -> named, not overwritten (--recreate)
    unmarked      an environment this command did not create   -> named (--adopt after a check, or --recreate)
    other tree    the marker names a different neXtBSW tree    -> named (--recreate)

The marker `<workspace>/.venv/nextbsw-venv.json` carries the tree, its VERSION and a hash over the requirement and
constraint files it was built from; it is written **last**, so an interrupted installation is never taken for a
complete one (same rule as the tool cache of `tools/ci`). A stale marker is what other tools warn about after a
pin move: `python -m tools.ci check` reports it, and the Studio's tool page points back at this command.

Only the standard library is imported - the environment this creates is the one that holds the packages.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]

PIN_FILE = "nextbsw.yaml"          # the workspace pin (ARCH-56, tools/nxconfig/workspace.py)
VENV_DIR = ".venv"
MARKER_NAME = "nextbsw-venv.json"
MARKER_SCHEMA = 1
CORE = "core"
STUDIO = "studio"

# States of an environment, in the order the check decides them
MISSING, BROKEN, UNMARKED, OTHER_TREE, STALE, INCOMPLETE, CURRENT = (
    "missing", "broken", "unmarked", "other_tree", "stale", "incomplete", "current")


class SetupError(Exception):
    """A problem that stops the command (bad arguments, a failed pip run)."""


# ---------------------------------------------------------------------------- pins (shared with tools/ci)

_ci_module = None


def ci():
    """tools/ci/ci.py loaded from its file.

    Not `import tools.ci`: this command runs before anything is installed and must not depend on the `tools`
    namespace package being importable. Only the pin parser and the canonical package name are used - the exact
    versions stay in tools/ci/constraints.txt, the one place that holds them (tools/ci/README.md).
    """
    global _ci_module
    if _ci_module is None:
        import importlib.util

        path = REPO / "tools" / "ci" / "ci.py"
        spec = importlib.util.spec_from_file_location("nx_ci_pins", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module      # @dataclass in ci.py looks its own module up in sys.modules
        spec.loader.exec_module(module)
        _ci_module = module
    return _ci_module


def requirement_files(tree: Path, profile: str) -> list[Path]:
    """The files the environment of `profile` is built from, in the order pip gets them."""
    files = [tree / "tools" / "requirements.txt"]
    if profile == STUDIO:
        files.append(tree / "tools" / "requirements-studio.txt")
    return files


def constraints_file(tree: Path) -> Path:
    return tree / "tools" / "ci" / "constraints.txt"


def python_pin_file(tree: Path) -> Path:
    return tree / "tools" / "ci" / ".python-version"


def fingerprint(tree: Path, profile: str) -> str:
    """sha256 over profile, requirements, constraints and the pinned interpreter version.

    Every value that decides what lands in the environment is part of it - a changed pin of the tree therefore
    makes the marker stale, which is exactly the warning customer-project.md 5.3 asks for.
    """
    digest = hashlib.sha256()
    digest.update(profile.encode("utf-8"))
    for path in [*requirement_files(tree, profile), constraints_file(tree), python_pin_file(tree)]:
        digest.update(b"\0" + path.name.encode("utf-8") + b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError as error:
            raise SetupError(f"{path} ist nicht lesbar: {error}") from error
    return "sha256:" + digest.hexdigest()


def tree_version(tree: Path) -> str:
    """The VERSION file at the tree root (ARCH-55/N1); "" if the tree does not carry one."""
    try:
        return (tree / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def required_packages(tree: Path, profile: str) -> set[str]:
    names: set[str] = set()
    for path in requirement_files(tree, profile):
        names |= ci().requirement_names(path)
    return names


# ---------------------------------------------------------------------------- workspace and venv


def is_tree(folder: Path) -> bool:
    """Root of a neXtBSW tree - same rule as tools/nxconfig/workspace.py, without needing pydantic."""
    folder = Path(folder)
    return (folder / "VERSION").is_file() and (folder / "tools" / "nxconfig").is_dir()


def find_workspace(start: Path) -> Path | None:
    """The folder with nextbsw.yaml above `start`, by the walk-up rules of tools/nxconfig/workspace.py.

    The walk stops at a neXtBSW tree (the project belongs to that tree, no workspace) and at the root of a git
    repository without a pin (a repository without nextbsw.yaml is no workspace).
    """
    start = Path(start).resolve()
    folder = start if start.is_dir() else start.parent
    for candidate in (folder, *folder.parents):
        if (candidate / PIN_FILE).is_file():
            return candidate
        if is_tree(candidate) or (candidate / ".git").exists():
            return None
    return None


def venv_dir(workspace: Path) -> Path:
    return Path(workspace) / VENV_DIR


def venv_python(venv: Path) -> Path:
    return Path(venv) / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def marker_file(venv: Path) -> Path:
    return Path(venv) / MARKER_NAME


def read_marker(venv: Path) -> tuple[dict | None, str]:
    """(marker, problem). `problem` is filled when the file exists but cannot be used."""
    path = marker_file(venv)
    if not path.is_file():
        return None, ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return None, f"{path.name} ist nicht lesbar ({error})"
    if not isinstance(data, dict) or data.get("schema") != MARKER_SCHEMA:
        schema = data.get("schema") if isinstance(data, dict) else "?"
        return None, f"{path.name} hat ein unbekanntes Format (schema {schema})"
    return data, ""


def write_marker(venv: Path, tree: Path, profile: str, python_version: str) -> dict:
    data = {
        "schema": MARKER_SCHEMA,
        "tool": "tools/setup",
        "tree": str(Path(tree).resolve()).replace("\\", "/"),
        "nextbsw_version": tree_version(tree),
        "profile": profile,
        "python": python_version,
        "fingerprint": fingerprint(tree, profile),
        "files": [str(p.relative_to(tree)).replace("\\", "/")
                  for p in [*requirement_files(tree, profile), constraints_file(tree), python_pin_file(tree)]],
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    marker_file(venv).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return data


# ---------------------------------------------------------------------------- state of an environment


@dataclass
class Status:
    workspace: Path
    tree: Path
    profile: str
    venv: Path
    state: str
    text: str                                   # one sentence, German, for the console and the Studio
    python: Path | None = None
    marker: dict | None = None
    problems: list[str] = field(default_factory=list)   # packages that are missing or at the wrong version

    @property
    def ok(self) -> bool:
        return self.state == CURRENT

    @property
    def fixable(self) -> bool:
        """True when `venv` may install without being told to overwrite anything."""
        return self.state in (MISSING, STALE, INCOMPLETE)


def installed_packages(python: Path | str, runner=None) -> dict[str, str]:
    """distribution -> version of another interpreter, asked in one call ({} when it cannot be started)."""
    code = ("import json, importlib.metadata as m;"
            "print(json.dumps({d.metadata['Name'].lower(): d.version for d in m.distributions()"
            " if d.metadata['Name']}))")
    runner = runner or _probe
    try:
        out = runner([str(python), "-c", code])
        return {ci().canonical(k): v for k, v in json.loads(out).items()} if out else {}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


def _probe(argv: list[str]) -> str:
    done = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    return done.stdout if done.returncode == 0 else ""


def package_problems(installed: dict[str, str], required: set[str], pins: dict[str, str]) -> list[str]:
    """What is missing or sits at the wrong version - the message a customer can act on."""
    problems = []
    for name in sorted(required):
        if name not in installed:
            problems.append(f"{name} fehlt")
        elif name in pins and installed[name] != pins[name]:
            problems.append(f"{name} {installed[name]} statt {pins[name]}")
    return problems


def inspect(workspace: Path, tree: Path = REPO, profile: str = CORE, probe=None) -> Status:
    """Read the state of `<workspace>/.venv` without changing anything."""
    workspace, tree = Path(workspace).resolve(), Path(tree).resolve()
    venv = venv_dir(workspace)
    here = fingerprint(tree, profile)

    def status(state: str, text: str, **kw) -> Status:
        return Status(workspace, tree, profile, venv, state, text, **kw)

    if not venv.exists():
        return status(MISSING, f"kein {VENV_DIR} in {workspace}")
    python = venv_python(venv)
    if not python.is_file():
        return status(BROKEN, f"{venv} hat keinen Interpreter ({python.name} fehlt) - unvollstaendige oder "
                              f"abgebrochene Installation")
    marker, problem = read_marker(venv)
    if problem:
        return status(BROKEN, f"{venv}: {problem}", python=python)
    if marker is None:
        return status(UNMARKED, f"{venv} stammt nicht aus diesem Befehl (kein {MARKER_NAME})", python=python)
    if Path(marker.get("tree", "")) != tree:
        return status(OTHER_TREE, f"{venv} gehoert zum Baum {marker.get('tree')}, nicht zu {tree}",
                      python=python, marker=marker)
    if marker.get("profile") != profile:
        return status(STALE, f"{venv} wurde mit Profil {marker.get('profile')} angelegt, verlangt ist {profile}",
                      python=python, marker=marker)
    if marker.get("fingerprint") != here:
        return status(STALE, f"{venv} ist veraltet: requirements/constraints von neXtBSW {tree_version(tree)} "
                             f"haben sich seit dem Anlegen ({marker.get('created', '?')}) geaendert",
                      python=python, marker=marker)

    pins = ci().read_constraints(constraints_file(tree))
    problems = package_problems(installed_packages(python, probe), required_packages(tree, profile), pins)
    if problems:
        return status(INCOMPLETE, f"{venv} ist unvollstaendig: {'; '.join(problems)}",
                      python=python, marker=marker, problems=problems)
    return status(CURRENT, f"{venv} ist aktuell (neXtBSW {marker.get('nextbsw_version') or '?'}, "
                           f"Profil {profile}, Python {marker.get('python', '?')})",
                  python=python, marker=marker)


def hint(state: str, workspace: Path | str = "<workspace>", profile: str = CORE) -> str:
    """The command that repairs `state` - one line, so every tool names the same way out."""
    base = f"python -m tools.setup venv {workspace}" + (" --studio" if profile == STUDIO else "")
    if state in (MISSING, STALE, INCOMPLETE):
        return base
    if state == UNMARKED:
        return f"{base} --adopt (uebernehmen) oder {base} --recreate (neu anlegen)"
    if state in (BROKEN, OTHER_TREE):
        return f"{base} --recreate"
    return ""


# ---------------------------------------------------------------------------- creating and repairing


def create_venv(venv: Path, base_python: str, runner) -> None:
    runner([base_python, "-m", "venv", str(venv)])


def install(python: Path, tree: Path, profile: str, runner) -> None:
    """pip install of the requirement files against the constraints of the same tree (no network pin is guessed)."""
    constraints = constraints_file(tree)
    pip = [str(python), "-m", "pip", "install", "--disable-pip-version-check"]
    pins = ci().read_constraints(constraints)
    if "pip" in pins:
        runner([*pip, f"pip=={pins['pip']}"])          # pip itself is pinned, like in tools/ci setup
    args = []
    for path in requirement_files(tree, profile):
        args += ["-r", str(path)]
    runner([*pip, *args, "-c", str(constraints)])


def run_logged(argv: list[str]) -> None:
    print("$ " + subprocess.list2cmdline(argv), flush=True)
    code = subprocess.run(argv).returncode
    if code != 0:
        raise SetupError(f"{argv[0]} endete mit {code}")


def remove_tree(path: Path) -> None:
    """Delete a folder including the read-only files a venv leaves behind on Windows."""
    import shutil
    import stat

    def writable(func, target, _error):
        os.chmod(target, stat.S_IWRITE)
        func(target)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=writable)
    else:
        shutil.rmtree(path, onerror=writable)


def ensure(workspace: Path, tree: Path = REPO, profile: str = CORE, *, recreate: bool = False,
           adopt: bool = False, base_python: str | None = None, runner=None, probe=None,
           out=print) -> tuple[int, Status]:
    """Bring `<workspace>/.venv` into the state the pinned tree asks for. Returns (exit code, status after)."""
    runner = runner or run_logged
    base_python = base_python or sys.executable
    status = inspect(workspace, tree, profile, probe=probe)
    out(f"Workspace: {status.workspace}")
    out(f"neXtBSW:   {status.tree} (VERSION {tree_version(status.tree) or '?'})")
    out(f"Umgebung:  {status.text}")

    if status.state == CURRENT and not recreate:
        out("Nichts zu tun.")
        return 0, status

    if recreate and status.venv.exists():
        out(f"{status.venv} wird geloescht (--recreate).")
        remove_tree(status.venv)
        status = inspect(workspace, tree, profile, probe=probe)

    if status.state in (BROKEN, OTHER_TREE):
        out(f"Abbruch: {status.text}")
        out(f"         {hint(status.state, status.workspace, profile)}")
        return 1, status
    if status.state == UNMARKED and not adopt:
        out(f"Abbruch: {status.text}")
        out(f"         {hint(status.state, status.workspace, profile)}")
        return 1, status

    if status.state == MISSING:
        create_venv(status.venv, base_python, runner)
    python = venv_python(status.venv)
    if not Path(python).is_file():
        raise SetupError(f"{python} fehlt nach dem Anlegen von {status.venv}")

    pins = ci().read_constraints(constraints_file(status.tree))
    required = required_packages(status.tree, profile)
    if status.state == UNMARKED and adopt:
        # adopt: never install over an environment somebody else built - check it and only then take it over
        problems = package_problems(installed_packages(python, probe), required, pins)
        if problems:
            out(f"Abbruch: {status.venv} passt nicht zu {constraints_file(status.tree)}: {'; '.join(problems)}")
            out(f"         {hint(BROKEN, status.workspace, profile)}")
            return 1, status
    else:
        install(python, status.tree, profile, runner)

    problems = package_problems(installed_packages(python, probe), required, pins)
    if problems:
        out("Abbruch: nach der Installation fehlen oder passen nicht: " + "; ".join(problems))
        return 1, inspect(workspace, tree, profile, probe=probe)

    version = _python_version(python, probe)
    write_marker(status.venv, status.tree, profile, version)
    after = inspect(workspace, tree, profile, probe=probe)
    out(f"Fertig: {after.text}")
    out(f"Interpreter: {python}")
    return (0 if after.ok else 1), after


def _python_version(python: Path, probe=None) -> str:
    runner = probe or _probe
    try:
        out = runner([str(python), "-c", "import platform;print(platform.python_version())"])
    except (OSError, subprocess.SubprocessError):
        return ""
    return (out or "").strip()


# ---------------------------------------------------------------------------- command line


def resolve_workspace(argument: Path | None) -> Path:
    if argument is not None:
        folder = Path(argument).resolve()
        if not folder.is_dir():
            raise SetupError(f"{folder} ist kein Ordner")
        return folder
    found = find_workspace(Path.cwd())
    if found is None:
        raise SetupError(f"kein Workspace gefunden: ueber {Path.cwd()} liegt kein {PIN_FILE}. Ordner angeben: "
                         f"python -m tools.setup venv <workspace>")
    return found


def profile_of(args: argparse.Namespace) -> str:
    return STUDIO if getattr(args, "studio", False) else CORE


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.setup", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    make = sub.add_parser("venv", help="create or update the .venv of a workspace")
    make.add_argument("workspace", nargs="?", type=Path,
                      help=f"folder with {PIN_FILE}; without it the folders above the current one are searched")
    make.add_argument("--studio", action="store_true",
                      help="also install tools/requirements-studio.txt (PySide6 and friends)")
    make.add_argument("--recreate", action="store_true", help="delete an existing .venv and build it again")
    make.add_argument("--adopt", action="store_true",
                      help="take over an existing .venv: check it against the constraints, then write the marker")
    make.add_argument("--python", dest="base_python", default=None,
                      help="interpreter the .venv is created with (default: the running one)")

    look = sub.add_parser("check", help="report the state of the .venv, write nothing")
    look.add_argument("workspace", nargs="?", type=Path)
    look.add_argument("--studio", action="store_true")

    args = parser.parse_args(argv)
    try:
        workspace = resolve_workspace(args.workspace)
        profile = profile_of(args)
        if args.command == "check":
            status = inspect(workspace, REPO, profile)
            print(f"Workspace: {status.workspace}")
            print(f"neXtBSW:   {status.tree} (VERSION {tree_version(status.tree) or '?'})")
            print(f"Umgebung:  {status.text}")
            if not status.ok:
                print(f"           {hint(status.state, status.workspace, profile)}")
            return 0 if status.ok else 1
        code, _ = ensure(workspace, REPO, profile, recreate=args.recreate, adopt=args.adopt,
                         base_python=args.base_python)
        return code
    except SetupError as error:
        print(f"Fehler: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
