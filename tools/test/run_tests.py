#!/usr/bin/env python3
"""One call for every host test of the repository (TOOL-04).

The test list is data, not guesswork: tools/test/tests.yaml names every test with its group, its command and a
comment. This script only executes it, collects the results and returns an exit code for CI. Everything that needs
target hardware is out of scope; the target group only configures and builds.

Run (from the repository root):
    .venv/Scripts/python tools/test                       # every group
    .venv/Scripts/python tools/test --group host          # one group
    .venv/Scripts/python tools/test --list                # what would run
    .venv/Scripts/python tools/test -k e2e --jobs 4       # selection, parallel (-k repeatable)
    .venv/Scripts/python tools/test --since --list        # entries the changes since origin/dev touch (TOOL-79)
    .venv/Scripts/python tools/test --slow --json run.json

Groups: python (unittest), pytest (tools/studio, offscreen), host (C/C++ host tests through ziglang),
build (host builds: bootloader DLL, STM32 build test), target (configure and build the target profiles).

A customer runs his own repository through the same command (TOOL-67, customer-project.md gap N12):

    .venv/Scripts/python <tree>/tools/test --workspace .    # his ci/tests.yaml plus the inherited tree entries

`tools/test/workspace.py` says what a workspace is, which entries of the tree carry over and why the tree is only
read. A run without --workspace is exactly what it was before - that run is the regression guard of the tree.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import fnmatch
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

try:
    from .build_dirs import DEMO_SUBDIR, ENV_DEMO_BUILD, default_build_root, fresh_args
    from . import workspace as ws_support
    from . import since as since_support
except ImportError:  # started as a directory or imported with tools/test on sys.path
    from build_dirs import DEMO_SUBDIR, ENV_DEMO_BUILD, default_build_root, fresh_args
    import workspace as ws_support
    import since as since_support

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
LIST_FILE = HERE / "tests.yaml"

PASS, FAIL, SKIP, TIMEOUT = "pass", "fail", "skip", "timeout"
RED = (FAIL, TIMEOUT)
LABEL = {PASS: "gruen", FAIL: "rot", SKIP: "uebersprungen", TIMEOUT: "Zeitueberschreitung"}
MARK = {PASS: "OK  ", FAIL: "FEHL", SKIP: "UEBS", TIMEOUT: "ZEIT"}
OUTPUT_TAIL = 8000  # characters of output kept for a failed test

TREE, WORKSPACE = "tree", "workspace"   # where an entry comes from (TOOL-67); see workspace.py


# ---------------------------------------------------------------------------- data model


@dataclass
class Test:
    name: str
    group: str
    kind: str
    comment: str = ""
    covers: list[str] = field(default_factory=list)
    modules: list[str] = field(default_factory=list)   # modules with a manifest this entry tests (DOC-18)
    # TOOL-79: files, directories or glob patterns the entry depends on without running them (tools/test --since)
    watch: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    needs_files: list[str] = field(default_factory=list)
    after: list[str] = field(default_factory=list)
    needs: list[str] = field(default_factory=list)     # entries that build what this one uses (TOOL-52)
    env: dict[str, str] = field(default_factory=dict)
    timeout: int = 900
    slow: bool = False
    parallel: bool = True
    # TOOL-67: an entry of the tree's list that is also meaningful in a customer workspace (workspace.py). It has
    # to run inside the tree and write nothing but its own {tmp}. Default: the entry stays in the tree.
    in_workspace: bool = False
    spec: dict = field(default_factory=dict)
    origin: str = TREE                                 # derived from the list it was read from, never from YAML


@dataclass
class Result:
    name: str
    group: str
    kind: str
    status: str
    origin: str = TREE
    duration: float = 0.0
    command: str = ""
    returncode: int | None = None
    reason: str = ""
    note: str = ""
    cases: int | None = None
    output: str = ""
    slow: bool = False


class ListError(Exception):
    """The test list itself is wrong (unknown kind, unknown group, duplicate name, unknown dependency)."""


# ---------------------------------------------------------------------------- test list


def load_list(path: Path = LIST_FILE, origin: str = TREE) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:       # TOOL-109: name the file - a workspace run reads two lists
        raise ListError(f"{path}: {exc}") from exc
    if not isinstance(data, dict) or "tests" not in data:
        raise ListError(f"{path}: no 'tests' section")
    if data.get("version") != 1:
        raise ListError(f"{path}: version {data.get('version')!r} is not supported (expected 1)")
    groups = list(data.get("groups") or [])
    defaults = data.get("defaults") or {}
    known_fields = set(Test.__dataclass_fields__) - {"spec", "origin"}
    tests: list[Test] = []
    for raw in data["tests"]:
        if "name" not in raw or "group" not in raw or "kind" not in raw:
            raise ListError(f"entry without name/group/kind: {raw!r}")
        merged = {**defaults.get(raw["kind"], {}), **raw}
        fields = {k: v for k, v in merged.items() if k in known_fields}
        spec = {k: v for k, v in merged.items() if k not in known_fields}
        test = Test(spec=spec, origin=origin, **fields)
        if not isinstance(test.in_workspace, bool):
            raise ListError(f"{test.name}: in_workspace must be true or false")
        if groups and test.group not in groups:
            raise ListError(f"{test.name}: unknown group {test.group!r} (known: {', '.join(groups)})")
        if test.kind not in KINDS:
            raise ListError(f"{test.name}: unknown kind {test.kind!r} (known: {', '.join(sorted(KINDS))})")
        if not test.comment.strip():
            raise ListError(f"{test.name}: comment is missing (every entry documents what it covers)")
        if not isinstance(test.modules, list) or not all(isinstance(m, str) and m.strip() for m in test.modules):
            raise ListError(f"{test.name}: modules must be a list of module names or module directories")
        if not isinstance(test.watch, list) or not all(isinstance(w, str) and w.strip() for w in test.watch):
            raise ListError(f"{test.name}: watch must be a list of files, directories or glob patterns")
        tests.append(test)
    names = [t.name for t in tests]
    doubles = sorted({n for n in names if names.count(n) > 1})
    if doubles:
        raise ListError(f"duplicate names: {', '.join(doubles)}")
    for test in tests:
        if not isinstance(test.needs, list):
            raise ListError(f"{test.name}: needs must be a list of entry names")
        unknown = [d for d in (*test.after, *test.needs) if d not in names]
        if unknown:
            raise ListError(f"{test.name}: unknown dependency {', '.join(unknown)}")
    data["tests"] = tests
    data["groups"] = groups or sorted({t.group for t in tests})
    data["file"] = path
    return data


def merge_lists(tree_data: dict, ws_data: dict | None) -> dict:
    """The list a workspace run executes (TOOL-67): the tree's `in_workspace` entries plus the workspace's own.

    `groups` is the union of both files. `tools` (the probes) are the workspace's own plus those of the tree that an
    inherited entry requires (TOOL-123); the workspace's probe of the same name wins.
    `inventory` comes from the **workspace** alone: the tree's test files are none of the customer's business
    (workspace.py). Without a workspace list only the inherited entries run and nothing is inventoried.

    A workspace may take further entries of the tree with a top-level `inherit: [<name>, ...]` in its own list -
    its own decision, named by the entry's name; an unknown name is an error of the list.
    """
    by_name = {t.name: t for t in tree_data["tests"]}
    wanted = [str(n) for n in ((ws_data or {}).get("inherit") or [])]
    unknown = [n for n in wanted if n not in by_name]
    if unknown:
        raise ListError(f"{ws_data['file']}: inherit nennt {', '.join(unknown)} - kein Eintrag von "
                        f"{tree_data['file']}")
    extra = set(wanted)
    inherited = [t for t in tree_data["tests"] if t.in_workspace or t.name in extra]
    own = list(ws_data["tests"]) if ws_data else []
    clash = sorted({t.name for t in own} & {t.name for t in inherited})
    if clash:
        raise ListError(f"{ws_data['file']}: {', '.join(clash)} heisst wie ein uebernommener Eintrag des Baums "
                        f"({tree_data['file']}) - anderen Namen waehlen")
    needed = {tool for t in inherited for tool in t.requires}
    merged = {
        "version": 1,
        "tests": inherited + own,
        "groups": sorted({*tree_data["groups"], *((ws_data or {}).get("groups") or [])}),
        # TOOL-123: of the tree's probes only those an inherited entry requires - the others (strictdoc, svd, ...)
        # are tools of the tree and only noise in a customer's report ("Fehlende Werkzeuge")
        "tools": {**{name: rule for name, rule in (tree_data.get("tools") or {}).items() if name in needed},
                  **((ws_data or {}).get("tools") or {})},
        "inventory": (ws_data or {}).get("inventory") if ws_data else None,
        "file": (ws_data or {}).get("file"),
        "inherited": [t.name for t in inherited],
    }
    names = [t.name for t in merged["tests"]]
    for test in merged["tests"]:
        unknown = [d for d in (*test.after, *test.needs) if d not in names]
        if unknown:
            raise ListError(f"{test.name}: unknown dependency {', '.join(unknown)} (an entry of a workspace may "
                            f"only depend on entries that run in a workspace, in_workspace)")
    return merged


# ---------------------------------------------------------------------------- module assignment (DOC-18)

# Where module manifests live: the places tools/nxconfig reads them from and tools/docgen catalogues -
# libs/<layer>/<module>, the family port templates and the board ports. A parked manifest counts as well.
MANIFEST_NAMES = ("module.yaml", "module.yaml.pending")
MANIFEST_ROOTS = ("libs/*", "platforms/*/port", "ecu/platforms/*")


def manifest_modules(repo: Path = REPO) -> dict[str, list[str]]:
    """{module name: [repository path of every module directory with that name]} of every manifest.

    The module name is the name of the directory holding the manifest, as in doc/reference/modules.md. Below
    libs/<layer> only the direct subdirectories are modules; in a port every directory with a manifest is."""
    found: dict[str, list[str]] = {}
    for pattern in MANIFEST_ROOTS:
        for root in sorted(d for d in repo.glob(pattern) if d.is_dir()):
            if pattern == "libs/*":
                dirs = {d for d in root.iterdir() if d.is_dir() and any((d / n).is_file() for n in MANIFEST_NAMES)}
            else:
                dirs = {p.parent for n in MANIFEST_NAMES for p in root.rglob(n)}
            for directory in dirs:
                found.setdefault(directory.name, []).append(directory.relative_to(repo).as_posix())
    return {name: sorted(paths) for name, paths in sorted(found.items())}


def manifest_modules_below(root: Path, skip_dirs=(".git", ".venv", "build", "__pycache__")) -> dict[str, list[str]]:
    """{module name: [path relative to `root`]} of every manifest anywhere below `root` (TOOL-67).

    A workspace names its module folders itself (`search.modules` of nextbsw.yaml); the layer convention
    `libs/<layer>/<module>` of the tree is the tree's, not the customer's. So here every directory with a
    manifest counts, at any depth."""
    root = Path(root)
    found: dict[str, list[str]] = {}
    for parent, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        if any(name in files for name in MANIFEST_NAMES):
            folder = Path(parent)
            found.setdefault(folder.name, []).append(folder.relative_to(root).as_posix())
    return {name: sorted(paths) for name, paths in sorted(found.items())}


def module_problems(data: dict, repo: Path = REPO, extra_roots=()) -> list[str]:
    """Entries whose `modules` name no module with a manifest, or a name several manifests carry.

    An entry names a module by its name (`capture`) or, where the name is not unique, by its directory relative
    to the repository (`platforms/<family>/port/.../capture`). `extra_roots` adds further roots - in a workspace
    run the module folders of the customer (TOOL-67), searched at any depth."""
    known = manifest_modules(repo)
    for root in extra_roots:
        for name, paths_of in manifest_modules_below(Path(root)).items():
            known.setdefault(name, []).extend(paths_of)
    paths = {p for ps in known.values() for p in ps}
    problems = []
    for test in data["tests"]:
        for module in test.modules:
            ref = module.strip().replace("\\", "/").rstrip("/")
            if "/" in ref:
                if ref not in paths:
                    problems.append(f"{test.name}: modules names {module!r}, but that directory has no "
                                    "module.yaml (or module.yaml.pending)")
            elif ref not in known:
                problems.append(f"{test.name}: modules names unknown module {module!r} (no module.yaml in a "
                                f"directory of that name under {', '.join(MANIFEST_ROOTS)})")
            elif len(known[ref]) > 1:
                problems.append(f"{test.name}: module name {module!r} is not unique ({', '.join(known[ref])}); "
                                "name the module directory instead")
    return problems


# ---------------------------------------------------------------------------- covers against args (TOOL-100)

DEFAULT_PATTERNS = ["test_*.py", "*HostTest.cpp"]   # inventory patterns of a list without its own

# Where the runner itself hands the test files to the tool, per kind. Only there can it check that a file named in
# `covers` is really executed. `command` is an arbitrary command line (the runner cannot know what it runs) and
# `cmake_preset` builds, it executes no test file - both stay unchecked, see tools/test/README.md.
HANDED_OVER = {"pytest": ("args",), "unittest": ("args",), "script": ("args",), "zig": ("sources",)}
PYTHON_RUNNERS = ("pytest", "unittest")
PLACEHOLDER_ROOTS = ("{repo}/", "{tree}/", "{workspace}/")


def _as_path(argument) -> str:
    """A file argument as it appears in `covers`: no root placeholder, no pytest node id, forward slashes."""
    text = str(argument).replace("\\", "/").split("::", 1)[0]
    for root in PLACEHOLDER_ROOTS:
        if text.startswith(root):
            text = text[len(root):]
    return text[2:] if text.startswith("./") else text


def covers_problems(data: dict, repo: Path = REPO, workspace: Path | None = None) -> list[str]:
    """Entries whose `covers` name a test file the entry never executes (TOOL-100).

    `covers` feeds the completeness check (inventory), `args`/`sources` are what really runs. A test file in
    `covers` that is missing from what the runner hands over stays green in the inventory and is never run - so
    every test file of `covers` (a name the inventory patterns match) must be handed over.

    The one named exception: a pytest/unittest entry can only hand over Python files. A C/C++ host test in its
    `covers` is compiled and started by the Python test itself (bswgen, rtegen, ecustate ...). It counts as
    executed only if one of the entry's Python files in `args` names it by its file name - so it is checked
    against the text, not waved through.

    Files in `covers` that are no test files (the module under test, a scenario, a header) are not inventoried
    and are not checked here: they cannot hide a test file from the inventory."""
    patterns = list(((data.get("inventory") or {}).get("patterns")) or DEFAULT_PATTERNS)
    problems = []
    for test in data["tests"]:
        fields = HANDED_OVER.get(test.kind)
        if not fields:
            continue
        handed = [_as_path(a) for f in fields for a in (test.spec.get(f) or []) if not str(a).startswith("-")]
        root = (workspace or repo) if test.origin == WORKSPACE else repo
        text = None
        for covered in test.covers:
            name = str(covered).replace("\\", "/")
            if not any(fnmatch.fnmatch(name.rsplit("/", 1)[-1], p) for p in patterns) or name in handed:
                continue
            where = "sources" if test.kind == "zig" else "args"
            if test.kind in PYTHON_RUNNERS and not name.endswith(".py"):
                if text is None:
                    text = "\n".join(_read(root / arg) for arg in handed if arg.endswith(".py"))
                if name.rsplit("/", 1)[-1] in text:
                    continue
                problems.append(f"{test.name}: covers nennt {name}, aber keine Python-Datei aus args nennt diese "
                                "Datei - sie wird nie ausgefuehrt (indirekt ausgefuehrte Hosttests muessen von "
                                "ihrem Python-Test namentlich uebersetzt werden)")
            else:
                problems.append(f"{test.name}: covers nennt {name}, aber {where} nicht - die Datei steht im "
                                f"Inventar und wird nie ausgefuehrt (in {where} aufnehmen oder aus covers nehmen)")
    return problems


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:          # a missing args file makes the entry red when it runs; here it names nothing
        return ""


# ---------------------------------------------------------------------------- tool probes


def gcc_path() -> str:
    """PATH with NX_ARM_GCC_BIN in front, like every other test of the repository."""
    return os.pathsep.join(p for p in (os.environ.get("NX_ARM_GCC_BIN"), os.environ.get("PATH")) if p)


def probe_tools(spec: dict, repo: Path = REPO, workspace: Path | None = None) -> dict[str, str]:
    """Returns {tool: reason} for every tool that is missing; an available tool is not in the result.

    `workspace` (TOOL-67): a probe of kind `path` is also looked for in the workspace, so a customer may require a
    file of his own repository without naming an absolute path."""
    roots = [Path(repo)] + ([Path(workspace)] if workspace else [])
    missing: dict[str, str] = {}
    for name, rule in (spec or {}).items():
        kind = rule.get("kind")
        hint = rule.get("hint", "")
        if kind == "python_module":
            module = rule.get("module", name)
            probe = subprocess.run([sys.executable, "-c", f"import {module}"], capture_output=True, text=True)
            if probe.returncode != 0:
                missing[name] = f"Python-Modul {module} fehlt in der .venv ({hint or 'pip install -r tools/requirements.txt'})"
        elif kind == "executable":
            if shutil.which(rule.get("name", name)) is None:
                missing[name] = f"{rule.get('name', name)} nicht im PATH" + (f" ({hint})" if hint else "")
        elif kind == "arm_gcc":
            if shutil.which(rule.get("name", "arm-none-eabi-gcc"), path=gcc_path()) is None:
                missing[name] = ("arm-none-eabi-gcc weder im PATH noch in NX_ARM_GCC_BIN"
                                 + (f" ({hint})" if hint else ""))
        elif kind == "path":
            if not any((root / rule["path"]).exists() for root in roots):
                missing[name] = f"{rule['path']} fehlt" + (f" ({hint})" if hint else "")
        elif kind == "env":
            if not os.environ.get(rule["name"]):
                missing[name] = (f"Umgebungsvariable {rule['name']} nicht gesetzt"
                                 + (f" ({hint})" if hint else ""))
        else:
            raise ListError(f"tool {name}: unknown probe kind {kind!r}")
    return missing


# ---------------------------------------------------------------------------- running


class Runner:
    def __init__(self, repo: Path, build_root: Path, python: str, keep: bool = False,
                 workspace: Path | None = None) -> None:
        self.repo = repo                       # the neXtBSW tree ({repo} and {tree})
        self.build_root = build_root
        self.python = python
        self.keep = keep
        # TOOL-67: the customer repository of a workspace run. Without it {workspace} is the tree itself, so the
        # same entry text works in both runs.
        self.workspace = workspace

    @property
    def workspace_root(self) -> Path:
        return self.workspace or self.repo

    def cwd_of(self, test: Test) -> Path:
        """Working directory: an entry of the workspace list runs in the workspace, one of the tree in the tree."""
        return self.workspace_root if test.origin == WORKSPACE else self.repo

    def placeholders(self, tmp: Path) -> dict[str, str]:
        return {"python": self.python, "repo": str(self.repo), "tree": str(self.repo),
                "workspace": str(self.workspace_root), "tmp": str(tmp), "build": str(self.build_root)}

    def fill(self, value: str, tmp: Path) -> str:
        return str(value).format(**self.placeholders(tmp))

    def spawn(self, argv: list[str], test: Test, env: dict[str, str], timeout: float) -> tuple[int, str, bool]:
        """Runs argv, returns (returncode, combined output, timed_out). Kills the process tree on Windows."""
        proc = subprocess.Popen(argv, cwd=self.cwd_of(test), env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace")
        try:
            out, _ = proc.communicate(timeout=timeout)
            return proc.returncode, out or "", False
        except subprocess.TimeoutExpired:
            if platform.system() == "Windows":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
            proc.kill()
            out, _ = proc.communicate()
            return proc.returncode, out or "", True

    def environment(self, test: Test, tmp: Path) -> dict[str, str]:
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        # PLAT-07: host tests that read the demo target build (build_dirs.demo_build_dir) take the one of this
        # run, also with --build-root; an explicit NX_BUILD_DIR (environment or entry) wins
        demo = self.build_root / DEMO_SUBDIR
        if not env.get(ENV_DEMO_BUILD) and (demo / "CMakeCache.txt").is_file():
            env[ENV_DEMO_BUILD] = str(demo)
        for key, value in test.env.items():
            env[key] = self.fill(value, tmp)
        return env

    def run(self, test: Test) -> Result:
        tmp = Path(tempfile.mkdtemp(prefix=f"nxtest_{test.name.replace('/', '_')}_"))
        started = time.monotonic()
        try:
            result = KINDS[test.kind](self, test, tmp)
        except Exception as exc:  # a broken entry must be red, never silently green
            result = Result(test.name, test.group, test.kind, FAIL, reason=f"Eintrag nicht ausführbar: {exc}")
        result.duration = time.monotonic() - started
        result.slow = test.slow
        if not (self.keep and result.status in RED):
            shutil.rmtree(tmp, ignore_errors=True)
        elif result.status in RED:
            result.output += f"\n[Arbeitsverzeichnis behalten: {tmp}]"
        return result


def _note(output: str) -> tuple[str, int | None]:
    """Short note and number of test cases from the output of unittest, pytest or a host test."""
    ran = re.search(r"^Ran (\d+) tests? in ([\d.]+)s", output, re.M)
    if ran:
        tail = re.search(r"^(OK(?: \(.*\))?|FAILED \(.*\))\s*$", output, re.M)
        return f"{ran.group(1)} Tests, {tail.group(1) if tail else '?'}", int(ran.group(1))
    py = re.search(r"(\d+) (?:passed|failed)[^\n]*", output)
    if py:
        return py.group(0).split(" in ")[0].strip(" ="), int(py.group(1))
    checks = re.search(r"(\d+) checks, (\d+) failures", output)
    if checks:
        return f"{checks.group(1)} Prüfungen, {checks.group(2)} Fehler", int(checks.group(1))
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return (lines[-1][:70] if lines else ""), None


def _finish(test: Test, argv: list[str], code: int, output: str, timed_out: bool, expect: str = "") -> Result:
    note, cases = _note(output)
    status = PASS
    reason = ""
    if timed_out:
        status, reason = TIMEOUT, f"Zeitüberschreitung nach {test.timeout} s"
    elif code != 0:
        status, reason = FAIL, f"Exit-Code {code}"
    elif expect and not re.search(expect, output):
        status, reason = FAIL, f"erwartete Ausgabe fehlt: {expect}"
    result = Result(test.name, test.group, test.kind, status, command=subprocess.list2cmdline(argv),
                    returncode=code, reason=reason, note=note, cases=cases)
    if status in RED:
        result.output = output[-OUTPUT_TAIL:]
    return result


# --- kinds -------------------------------------------------------------------


def _python_call(runner: Runner, test: Test, tmp: Path, head: list[str]) -> Result:
    argv = [runner.python, *head, *[runner.fill(a, tmp) for a in test.spec.get("args", [])]]
    code, out, timed = runner.spawn(argv, test, runner.environment(test, tmp), test.timeout)
    return _finish(test, argv, code, out, timed, test.spec.get("expect", ""))


def kind_unittest(runner: Runner, test: Test, tmp: Path) -> Result:
    return _python_call(runner, test, tmp, ["-m", "unittest"])


def kind_pytest(runner: Runner, test: Test, tmp: Path) -> Result:
    return _python_call(runner, test, tmp, ["-m", "pytest"])


def kind_script(runner: Runner, test: Test, tmp: Path) -> Result:
    return _python_call(runner, test, tmp, [])


def kind_command(runner: Runner, test: Test, tmp: Path) -> Result:
    argv = [runner.fill(a, tmp) for a in test.spec["argv"]]
    code, out, timed = runner.spawn(argv, test, runner.environment(test, tmp), test.timeout)
    return _finish(test, argv, code, out, timed, test.spec.get("expect", ""))


def kind_zig(runner: Runner, test: Test, tmp: Path) -> Result:
    """Compile a C/C++ host test with ziglang and run it (README.md and the file headers of the tests)."""
    exe = tmp / f"{test.name.replace('/', '_')}.exe"
    compile_argv = [runner.python, "-m", "ziglang", test.spec.get("driver", "c++")]
    if test.spec.get("std"):
        compile_argv.append(f"-std={test.spec['std']}")
    compile_argv += list(test.spec.get("flags", []))
    compile_argv += [f"-I{runner.fill(i, tmp)}" for i in test.spec.get("include", [])]
    compile_argv += [f"-isystem{runner.fill(i, tmp)}" for i in test.spec.get("system_include", [])]
    compile_argv += [f"-D{d}" for d in test.spec.get("define", [])]
    compile_argv += [runner.fill(s, tmp) for s in test.spec["sources"]]
    compile_argv += ["-o", str(exe)]
    env = runner.environment(test, tmp)
    code, out, timed = runner.spawn(compile_argv, test, env, test.timeout)
    if code != 0 or timed:
        result = _finish(test, compile_argv, code, out, timed)
        result.reason = f"Übersetzen fehlgeschlagen ({result.reason})"
        return result
    run_argv = [str(exe), *[runner.fill(a, tmp) for a in test.spec.get("args", [])]]
    code, run_out, timed = runner.spawn(run_argv, test, env, test.timeout)
    result = _finish(test, compile_argv + ["&&", exe.name], code, run_out, timed,
                     test.spec.get("expect", r"\d+ checks, 0 failures"))
    return result


def kind_cmake_preset(runner: Runner, test: Test, tmp: Path) -> Result:
    """Configure and build a CMake preset (target group); warning free is part of the result."""
    preset = test.spec["preset"]
    binary_dir = Path(runner.fill(test.spec["binary_dir"], tmp)) if test.spec.get("binary_dir") \
        else runner.build_root / preset
    env = runner.environment(test, tmp)
    env["PATH"] = gcc_path()
    env.setdefault("CC", "arm-none-eabi-gcc")
    env.setdefault("CXX", "arm-none-eabi-g++")
    # A cache whose compiler moved or vanished (NX_ARM_GCC_BIN changed, old installation removed) is configured
    # fresh instead of failing on the old path (VERIFY-0928)
    fresh, stale = fresh_args(binary_dir, env["PATH"])
    configure = ["cmake", "--preset", preset, *fresh, "-B", str(binary_dir),
                 f"-DPython3_EXECUTABLE={runner.python}"]
    code, out, timed = runner.spawn(configure, test, env, test.timeout)
    if stale:
        out = f"[neu konfiguriert: {stale}]\n" + out
    if code != 0 or timed:
        result = _finish(test, configure, code, out, timed)
        result.reason = f"Konfigurieren fehlgeschlagen ({result.reason})"
        return result
    config = test.spec.get("configuration", "RelWithDebInfo")
    build = ["cmake", "--build", str(binary_dir), "--config", config]
    if test.spec.get("clean_first"):
        build.append("--clean-first")
    code, build_out, timed = runner.spawn(build, test, env, test.timeout)
    result = _finish(test, build, code, build_out, timed)
    result.command = subprocess.list2cmdline(configure) + " && " + result.command
    targets = re.findall(r"^\[(\d+)/(\d+)\]", build_out, re.M)
    if result.status == PASS:
        result.note = f"{targets[-1][1]} Ziele" if targets else "Bau erfolgreich"
    warnings = [line for line in build_out.splitlines() if re.search(r"\bwarning:", line, re.I)]
    if warnings and result.status == PASS:
        result.status = FAIL
        result.reason = f"{len(warnings)} Warnung(en) im Bau-Log"
        result.output = "\n".join(warnings[:40])
    for name, pattern in (test.spec.get("expect_files") or {}).items():
        if result.status == PASS and not list(binary_dir.glob(pattern)):
            result.status, result.reason = FAIL, f"Erwartetes Erzeugnis fehlt: {name} ({pattern})"
    return result


KINDS = {"unittest": kind_unittest, "pytest": kind_pytest, "script": kind_script, "command": kind_command,
         "zig": kind_zig, "cmake_preset": kind_cmake_preset}


# ---------------------------------------------------------------------------- inventory


def inventory(data: dict, repo: Path = REPO) -> list[str]:
    """Test files that exist in the repository but are not in the list (so the list stays complete)."""
    rules = data.get("inventory") or {}
    patterns = rules.get("patterns") or DEFAULT_PATTERNS
    skip_dirs = set(rules.get("skip_dirs") or [])
    ignored = {entry["path"] if isinstance(entry, dict) else entry for entry in (rules.get("ignore") or [])}
    listed = {c for test in data["tests"] for c in test.covers} | ignored
    found: list[str] = []
    for folder, subdirs, files in os.walk(repo):
        subdirs[:] = [d for d in subdirs if d not in skip_dirs]  # prune: external/, build/, .venv/ are large
        for name in files:
            if not any(fnmatch.fnmatch(name, pattern) for pattern in patterns):
                continue
            rel = (Path(folder) / name).relative_to(repo).as_posix()
            if rel not in listed:
                found.append(rel)
    return sorted(found)


# ---------------------------------------------------------------------------- selection and scheduling


def name_matches(name: str, pattern: str) -> bool:
    """-k: a glob if the pattern has one, otherwise a substring (case ignored)."""
    if any(c in pattern for c in "*?["):
        return fnmatch.fnmatch(name, pattern)
    return pattern.lower() in name.lower()


def select(tests: list[Test], groups: list[str], pattern: str | list[str] | None, slow: bool,
           skip: list[str] | tuple = (), only: set[str] | None = None) -> list[Test]:
    """Entries of the run: group, pattern and --slow narrow the list, `skip` takes entries out by their exact
    name (TOOL-109: the Studio hands over the entries its user switched off). An entry that a remaining one
    `needs` still runs - with_needs adds it back.

    `pattern` may be several (-k repeated): an entry matching any of them is chosen (finding of the coordinator
    2026-09-28, before only the last -k counted). `only` (TOOL-79, --since) narrows to these names as well."""
    patterns = [pattern] if isinstance(pattern, str) else [p for p in (pattern or []) if p]
    chosen = []
    for test in tests:
        if groups and test.group not in groups:
            continue
        if test.name in skip:
            continue
        if patterns and not any(name_matches(test.name, p) for p in patterns):
            continue
        if only is not None and test.name not in only:
            continue
        if test.slow and not slow:
            continue
        chosen.append(test)
    return with_needs(chosen, tests)


def with_needs(chosen: list[Test], tests: list[Test]) -> list[Test]:
    """`chosen` plus every entry they `need`, transitively, whatever group, pattern or --slow say (TOOL-52).

    Finding 2026-09-22: `-k canflash-info-host` alone ran against an old bl_host.dll left in the build folder
    (reported BL 0.3.2 instead of 0.3.4) and was red; `after` only orders entries that are selected anyway. An
    entry that uses what another one builds names it under `needs`, and a single -k run builds it first."""
    by_name = {t.name: t for t in tests}
    names = {t.name for t in chosen}
    todo = list(chosen)
    while todo:
        for name in todo.pop().needs:
            if name not in names:
                names.add(name)
                todo.append(by_name[name])
    return [t for t in tests if t.name in names]


def blocked_by(test: Test, status: dict[str, str]) -> str:
    """Reason to skip `test` because an entry it needs did not end green (its output may be missing or stale)."""
    bad = [f"{name} {LABEL[status[name]]}" for name in test.needs if status.get(name, PASS) != PASS]
    return f"Vorbedingung nicht gruen: {', '.join(bad)}" if bad else ""


def skip_reason(test: Test, missing: dict[str, str], runner: Runner) -> str:
    for tool in test.requires:
        if tool in missing:
            return missing[tool]
    for needed in test.needs_files:
        filled = Path(runner.fill(needed, Path(".")))
        path = filled if filled.is_absolute() else runner.cwd_of(test) / filled
        if not path.exists():
            return f"Vorbedingung fehlt: {path} (erst die Gruppe erzeugen lassen, die sie baut)"
    return ""


def schedule(tests: list[Test], runner: Runner, missing: dict[str, str], jobs: int,
             report) -> list[Result]:
    """Runs the tests in waves: dependencies first, parallel-safe ones together."""
    results: list[Result] = []
    done: set[str] = set()
    status: dict[str, str] = {}
    pending = list(tests)
    selected = {t.name for t in tests}

    def start(test: Test) -> Result:
        reason = blocked_by(test, status)
        if reason:
            return Result(test.name, test.group, test.kind, SKIP, reason=reason, slow=test.slow)
        return run_one(test, runner, missing)

    def finish(test: Test, result: Result) -> None:
        result.origin = test.origin
        results.append(result)
        report(result)
        status[test.name] = result.status
        done.add(test.name)
        pending.remove(test)

    while pending:
        ready = [t for t in pending if all(d in done or d not in selected for d in (*t.after, *t.needs))]
        if not ready:  # a cycle in `after`/`needs` must never hang the run
            for test in pending:
                results.append(Result(test.name, test.group, test.kind, FAIL, origin=test.origin,
                                      reason=f"Abhängigkeit nicht auflösbar: {', '.join((*test.after, *test.needs))}"))
                report(results[-1])
            break
        for test in [t for t in ready if not t.parallel]:  # shared output paths, long builds: alone
            finish(test, start(test))
        wave = [t for t in ready if t.parallel]
        if wave:
            with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
                futures = {pool.submit(start, test): test for test in wave}
                for future in concurrent.futures.as_completed(futures):
                    finish(futures[future], future.result())
    return results


def run_one(test: Test, runner: Runner, missing: dict[str, str]) -> Result:
    reason = skip_reason(test, missing, runner)
    if reason:
        return Result(test.name, test.group, test.kind, SKIP, reason=reason, slow=test.slow)
    return runner.run(test)


# ---------------------------------------------------------------------------- reporting


def write_list_json(path: Path, tests: list[Test], workspace: Path | None, since: dict | None = None) -> None:
    """The list of --list as data (TOOL-109): what the Studio offers on its page "Host-Tests". Same selection
    as the printed table; `origin` says whether an entry comes from the workspace list or from the tree.
    With --since the mapping (TOOL-79) comes along under `since`."""
    payload = {
        "schema": "nxtest-list/1",
        "workspace": str(workspace) if workspace else None,
        "tests": [{"name": t.name, "group": t.group, "kind": t.kind, "origin": t.origin, "slow": t.slow,
                   "comment": " ".join(t.comment.split()), "requires": list(t.requires), "needs": list(t.needs)}
                  for t in tests],
    }
    if since is not None:
        payload["since"] = since
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def print_list(tests: list[Test]) -> None:
    width = max((len(t.name) for t in tests), default=4)
    print(f"{'Gruppe':<8} {'Name':<{width}} {'Art':<13} Kommentar")
    for test in sorted(tests, key=lambda t: (t.group, t.name)):
        flags = " [slow]" if test.slow else ""
        needs = f" [braucht: {', '.join(test.requires)}]" if test.requires else ""
        needs += f" [nach Eintrag: {', '.join(test.needs)}]" if test.needs else ""
        comment = " ".join(test.comment.split())
        print(f"{test.group:<8} {test.name:<{width}} {test.kind:<13} {comment}{needs}{flags}")
    print(f"\n{len(tests)} Einträge")


def summarise(results: list[Result], unlisted: list[str], total_duration: float,
              list_name: str = "tools/test/tests.yaml") -> None:
    print("\n" + "=" * 100)
    print(f"{'Gruppe':<8} {'gruen':>6} {'rot':>5} {'uebersprungen':>14} {'Rechenzeit':>11}   Testfaelle")
    groups = sorted({r.group for r in results})
    for group in groups:
        rows = [r for r in results if r.group == group]
        cases = sum(r.cases or 0 for r in rows)
        print(f"{group:<8} {sum(r.status == PASS for r in rows):>6} {sum(r.status in RED for r in rows):>5} "
              f"{sum(r.status == SKIP for r in rows):>14} {fmt(sum(r.duration for r in rows)):>11}   "
              f"{cases if cases else '-'}")
    green = sum(r.status == PASS for r in results)
    red = [r for r in results if r.status in RED]
    skipped = [r for r in results if r.status == SKIP]
    cases = sum(r.cases or 0 for r in results)
    print("-" * 100)
    print(f"{'gesamt':<8} {green:>6} {len(red):>5} {len(skipped):>14} "
          f"{fmt(sum(r.duration for r in results)):>11}   {cases}")
    print(f"Gesamtdauer (Wanduhr): {fmt(total_duration)}")
    if skipped:
        print("\nUebersprungen:")
        for r in skipped:
            print(f"  - {r.name} ({r.group}): {r.reason}")
    if red:
        print("\nROT:")
        for r in red:
            print(f"  - {r.name} ({r.group}): {r.reason}")
    if unlisted:
        print(f"\nNICHT IN {list_name} ({len(unlisted)}) - Liste ergaenzen oder unter inventory.ignore "
              "aufnehmen:")
        for rel in unlisted:
            print(f"  - {rel}")
    verdict = "FEHLER" if red or unlisted else ("OK" if green else "NICHTS GELAUFEN")
    print(f"\nErgebnis: {verdict} ({green} gruen, {len(red)} rot, {len(skipped)} uebersprungen, "
          f"{cases} Testfaelle, {fmt(total_duration)})")


def fmt(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{int(seconds // 60)}m{int(seconds % 60):02d}s"


def write_json(path: Path, results: list[Result], unlisted: list[str], total: float, started: float,
               groups: list[str], repo: Path = REPO, workspace: Path | None = None,
               since: dict | None = None) -> None:
    payload = {
        "schema": "nxtest/1",
        "started": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(started)),
        "duration_s": round(total, 3),
        "repo": str(repo),
        "workspace": str(workspace) if workspace else None,
        "groups_run": groups,
        "summary": {
            "pass": sum(r.status == PASS for r in results),
            "fail": sum(r.status == FAIL for r in results),
            "timeout": sum(r.status == TIMEOUT for r in results),
            "skip": sum(r.status == SKIP for r in results),
            "total": len(results),
            "cases": sum(r.cases or 0 for r in results),
        },
        "per_group": {g: {"pass": sum(r.status == PASS for r in results if r.group == g),
                          "fail": sum(r.status in RED for r in results if r.group == g),
                          "skip": sum(r.status == SKIP for r in results if r.group == g),
                          "duration_s": round(sum(r.duration for r in results if r.group == g), 3)}
                      for g in sorted({r.group for r in results})},
        "unlisted_test_files": unlisted,
        "tests": [{"name": r.name, "group": r.group, "kind": r.kind, "status": r.status, "origin": r.origin,
                   "duration_s": round(r.duration, 3), "returncode": r.returncode, "reason": r.reason,
                   "note": r.note, "cases": r.cases, "slow": r.slow, "command": r.command,
                   "output": r.output if r.status in RED else ""}
                  for r in results],
    }
    if since is not None:
        payload["since"] = since
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------- command line


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python tools/test", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--group", action="append", default=None,
                        help="python|pytest|host|build|target|all (repeatable, default all)")
    parser.add_argument("--list", action="store_true", help="only list the selected tests")
    parser.add_argument("-k", dest="pattern", action="append", default=None, metavar="PATTERN",
                        help="run only tests whose name matches (substring or glob); repeatable, an entry matching "
                             "any of them is chosen")
    parser.add_argument("--since", nargs="?", const=since_support.DEFAULT_REF, default=None, metavar="REF",
                        help="TOOL-79: only the entries the changes since REF touch (git diff against the merge "
                             f"base, working tree and untracked files included; default {since_support.DEFAULT_REF}"
                             "), plus every entry without a mapping; names the triggering file per entry and the "
                             "changed files no entry covers")
    parser.add_argument("--skip", action="append", default=[], metavar="NAME",
                        help="leave out the entry with exactly this name (repeatable; an entry another one needs "
                             "still runs; an unknown name is reported and ignored)")
    parser.add_argument("--jobs", "-j", type=int, default=1, help="run this many tests in parallel (default 1)")
    parser.add_argument("--json", type=Path,
                        help="write the result per test to this file (with --list: the selected entries)")
    parser.add_argument("--slow", action="store_true",
                        help="include the slow mutation runs (E2E about 7 min, bootloader about 4 min)")
    parser.add_argument("--timeout", type=int, default=None, help="override the timeout of every test (seconds)")
    parser.add_argument("--clean", action="store_true",
                        help="target builds from scratch (--clean-first); without it they are incremental")
    parser.add_argument("--build-root", type=Path, default=None,
                        help="directory for the target builds (default build/, in a worktree %%TEMP%%/s32k/...)")
    parser.add_argument("--no-inventory", action="store_true",
                        help="do not check the list against the test files in the repository")
    parser.add_argument("--keep", action="store_true", help="keep the working directory of failed tests")
    parser.add_argument("--tests", type=Path, default=LIST_FILE, help="other test list than tools/test/tests.yaml")
    parser.add_argument("--repo", type=Path, default=REPO, help="other repository root (for the own tests)")
    parser.add_argument("--workspace", type=Path, nargs="?", const=Path("."), default=None,
                        help="run against a customer workspace (TOOL-67): its ci/tests.yaml plus the entries of "
                             "the tree marked in_workspace; without a folder the workspace above the current one")
    return parser


def resolve_workspace(argument: Path, tree: Path) -> ws_support.Workspace:
    """The workspace of --workspace: the named folder, or the one above the current directory."""
    folder = Path(argument).resolve()
    if not (folder / ws_support.PIN_FILE).is_file():
        found = ws_support.find_workspace(folder)
        if found is None:
            raise ws_support.WorkspaceError(
                f"kein Workspace: ueber {folder} liegt kein {ws_support.PIN_FILE} (ARCH-56). Ordner angeben: "
                f"python tools/test --workspace <workspace>")
        folder = found
    return ws_support.open_workspace(folder, tree)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    workspace: ws_support.Workspace | None = None
    try:
        data = load_list(args.tests)
        if args.workspace is not None:
            workspace = resolve_workspace(args.workspace, args.repo.resolve())
            own = load_list(workspace.list_file, WORKSPACE) if workspace.list_file.is_file() else None
            data = merge_lists(data, own)
    except (ListError, yaml.YAMLError) as exc:
        print(f"Testliste fehlerhaft: {exc}", file=sys.stderr)
        return 2
    except ws_support.WorkspaceError as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 2
    problems = module_problems(data, args.repo.resolve(),
                               workspace.module_dirs() if workspace else [])
    if problems:
        print("Testliste fehlerhaft (Feld modules):\n  - " + "\n  - ".join(problems), file=sys.stderr)
        return 2
    problems = covers_problems(data, args.repo.resolve(), workspace.root if workspace else None)
    if problems:
        print("Testliste fehlerhaft (covers gegen args, TOOL-100):\n  - " + "\n  - ".join(problems),
              file=sys.stderr)
        return 2

    groups = [g for g in (args.group or ["all"])]
    if "all" in groups:
        groups = []
    unknown = [g for g in groups if g not in data["groups"]]
    if unknown:
        print(f"Unbekannte Gruppe(n): {', '.join(unknown)} (bekannt: {', '.join(data['groups'])}, all)",
              file=sys.stderr)
        return 2

    unknown = [name for name in args.skip if name not in {t.name for t in data["tests"]}]
    if unknown:
        print(f"Hinweis: --skip {', '.join(unknown)} - kein Eintrag dieses Namens in der Liste (ignoriert)")
    selection = None
    only = None
    if args.since is not None:
        if workspace is not None:
            print("FEHLER: --since gilt nur im Baum, nicht mit --workspace", file=sys.stderr)
            return 2
        repo_root = args.repo.resolve()
        try:
            base, changed = since_support.changed_files(repo_root, args.since)
        except since_support.SinceError as exc:
            print(f"FEHLER: --since {args.since}: {exc}", file=sys.stderr)
            return 2
        edited = since_support.edited_entries(repo_root, args.since, base, args.tests)
        try:
            list_rel = args.tests.resolve().relative_to(repo_root).as_posix()
        except ValueError:
            list_rel = ""
        selection = since_support.map_changes(data["tests"], changed, repo_root, args.since, base, edited, list_rel)
        only = selection.names
    tests = select(data["tests"], groups, args.pattern, args.slow, args.skip, only)
    if selection is not None:
        chosen = [t.name for t in tests]
        left_out = sorted(n for n in selection.triggers if n not in chosen)
        print("\n".join(since_support.report_lines(selection, chosen, left_out)) + "\n")
    if args.timeout:
        for test in tests:
            test.timeout = args.timeout
    if args.clean:
        for test in tests:
            if test.kind == "cmake_preset":
                test.spec["clean_first"] = True
    if args.list:
        print_list(tests)
        if args.json:
            write_list_json(args.json, tests, workspace.root if workspace else None,
                            since_support.as_json(selection) if selection else None)
            print(f"JSON: {args.json}")
        return 0
    if not tests:
        if selection is not None:          # TOOL-79: nothing touched is nothing to run, not a wrong call
            print("Kein Eintrag betroffen.")
            return 0
        print("Kein Test ausgewaehlt.", file=sys.stderr)
        return 2

    repo = args.repo.resolve()
    if workspace is None:
        build_root = (args.build_root or default_build_root(repo)).resolve()
    else:
        build_root = (args.build_root or workspace.build_root).resolve()
        # the pinned tree is a released baseline the workspace only reads (customer-project.md 5.1)
        refusal = ws_support.check_build_root(build_root, repo)
        if refusal:
            print(f"FEHLER: {refusal}", file=sys.stderr)
            return 2
    runner = Runner(repo, build_root, sys.executable, args.keep,
                    workspace.root if workspace else None)
    missing = probe_tools(data.get("tools"), repo, workspace.root if workspace else None)
    if workspace is not None:
        print(f"Workspace:  {workspace.root}")
        print(f"Testliste:  {workspace.list_file if workspace.list_file.is_file() else '(keine ci/tests.yaml)'}")
        print(f"Baum (nur lesend): {repo}")
        inherited = data.get("inherited") or []
        print(f"Uebernommen aus dem Baum ({len(inherited)}): {', '.join(inherited) or '-'}")
    else:
        print(f"Repository: {repo}")
    print(f"Python:     {sys.executable}")
    print(f"Bau-Wurzel: {runner.build_root}")
    if missing:
        print("Fehlende Werkzeuge (davon abhaengige Tests werden uebersprungen):")
        for tool, reason in sorted(missing.items()):
            print(f"  - {tool}: {reason}")
    needed = sorted({name for t in tests for name in t.needs})
    if needed:
        print(f"Vorbedingungen (needs), laufen zuerst mit: {', '.join(needed)}")
    print(f"{len(tests)} Test(s) in {len({t.group for t in tests})} Gruppe(n), {args.jobs} parallel\n")

    started = time.time()
    clock = time.monotonic()
    index = [0]

    def report(result: Result) -> None:
        index[0] += 1
        extra = result.note or result.reason
        print(f"[{index[0]:>2}/{len(tests)}] {MARK[result.status]} {result.group:<7} {result.name:<26} "
              f"{fmt(result.duration):>8}  {extra}", flush=True)

    results = schedule(tests, runner, missing, args.jobs, report)
    total = time.monotonic() - clock
    if args.no_inventory:
        unlisted = []
    elif workspace is None:
        unlisted = inventory(data, repo)
    elif data.get("inventory") is not None:
        # TOOL-67: the workspace's own rules over the workspace - never the test files of the tree
        unlisted = inventory(data, workspace.root)
    else:
        unlisted = []
        print(f"\nHinweis: keine {ws_support.CI_DIR}/{ws_support.LIST_NAME} im Workspace - die "
              "Vollstaendigkeitspruefung entfaellt (nur die uebernommenen Eintraege des Baums sind gelaufen).")

    for result in [r for r in results if r.status in RED]:
        print("\n" + "-" * 100)
        print(f"ROT: {result.name} ({result.group}) - {result.reason}")
        print(f"Befehl: {result.command}")
        if result.output:
            print(result.output)

    summarise(results, unlisted, total,
              f"{ws_support.CI_DIR}/{ws_support.LIST_NAME}" if workspace else "tools/test/tests.yaml")
    if selection is not None and selection.orphans_code():
        # repeated after the long run: the files without an entry are the finding (TOOL-79)
        print(f"\n--since {selection.ref}: geaenderte Dateien ohne Testeintrag (Code ohne Test): "
              + ", ".join(selection.orphans_code()))
    if args.json:
        write_json(args.json, results, unlisted, total, started, groups or data["groups"], repo,
                   workspace.root if workspace else None,
                   since_support.as_json(selection) if selection else None)
        print(f"JSON: {args.json}")
    return 1 if any(r.status in RED for r in results) or unlisted else 0


if __name__ == "__main__":
    sys.exit(main())
