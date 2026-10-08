#!/usr/bin/env python3
"""Provider-neutral CI pipeline of neXtBSW (TOOL-03).

Everything a CI job does lives here; a CI provider only checks out the repository (with the submodule), provides
the Python interpreter from tools/ci/.python-version and calls two commands:

    python -m tools.ci setup --python-packages     # pinned tools into the cache, pinned Python packages
    python -m tools.ci run --strict --out ci-out   # check tools, tools/test (all groups), artifacts, report

The same two commands run a local pipeline on a workstation. Other commands:

    python -m tools.ci check [--toolchain local]   # versions against tools/ci/versions.toml, preset coverage
    python -m tools.ci lint                        # actionlint over .github/workflows (pinned download)
    python -m tools.ci summary ci-out              # print the Markdown report of a finished run
    python -m tools.ci preflight [--since v0.2.0]  # PF-1..PF-5 before the release CI (TOOL-209)

A customer runs his own repository through the same pipeline (TOOL-67, customer-project.md gap N12):

    python -m tools.ci run --workspace <dir>       # pin, his ci/tests.yaml, his presets, his artefacts
    python -m tools.ci run --workspace <dir> --release   # plus: the pinned tree must be a released baseline
    python -m tools.ci template gitlab --out .     # the CI file of his provider, with submodule and credentials

Then `ci/pipeline.toml` of the workspace decides instead of `tools/ci/pipeline.toml`, the presets of his
`CMakePresets.json` are the ones that need a decision, and the first stage compares the version of the tree with
his `nextbsw.yaml` (ARCH-55/56). The tree is only read: the build root lies in the workspace, and a run without
--workspace is unchanged - that run is the regression guard of the tree.

`--release` adds the strictness a handed-over image needs (TOOL-65, customer-project.md gap N10): the tree that is
built has to stand on the **annotated** tag `v<version>` reachable from `master`, has to be clean, and every
overlay has to carry the same version. Without the flag a development pin - a tree with no release tag, the state
every customer starts in - is only a warning, so daily work stays green.

The standard library is enough to start (setup runs before any package is installed); PyYAML is imported only
where tools/test/tests.yaml is read.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
VERSIONS_FILE = HERE / "versions.toml"
PIPELINE_FILE = HERE / "pipeline.toml"
PYTHON_VERSION_FILE = HERE / ".python-version"
CONSTRAINTS_FILE = HERE / "constraints.txt"
REQUIREMENTS_FILE = REPO / "tools" / "requirements.txt"
# TOOL-63 (gap N8): the core list above holds what generators, checks and CI need; PySide6 and its companions are
# only needed by tools/studio and live in their own file, so a workspace that builds and checks stays free of Qt.
# Both are pinned by the same constraints.txt; `python -m tools.setup` builds a workspace environment from them.
STUDIO_REQUIREMENTS = REPO / "tools" / "requirements-studio.txt"
MISRA_REQUIREMENTS = REPO / "tools" / "misra" / "requirements.txt"   # cppcheck + clang-tidy (PLAT-74)
# documentation website (DOC-71): MkDocs and companions, every package pinned exactly in the file itself; installed by
# `setup --python-packages` so `docgen-site` is not skipped in a --strict run (TOOL-232)
SITE_REQUIREMENTS = REPO / "tools" / "requirements-site.txt"
# StrictDoc for the requirements (DOC-21): its own venv in the tool cache, pinned by its own constraints file (the
# workstation .venv holds a pypdf that StrictDoc pins differently); tools/trace finds it through NX_STRICTDOC_VENV
DOCS_REQUIREMENTS = REPO / "tools" / "requirements-docs.txt"
DOCS_CONSTRAINTS = HERE / "constraints-docs.txt"
MARKER = ".nx-ci-ok"
EXE = ".exe" if os.name == "nt" else ""
TEMPLATE_DIR = HERE / "templates"          # CI files a customer copies into his workspace (TOOL-67)

OK, WARN, FAIL, SKIP = "ok", "warn", "fail", "skip"


class CiError(Exception):
    """Configuration or download problem that stops the pipeline."""


# ---------------------------------------------------------------------------- configuration


def load_toml(path: Path) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def host_os() -> str:
    system = platform.system()
    if system == "Windows":
        return "windows"
    if system == "Linux":
        return "linux"
    raise CiError(f"operating system {system} has no pinned tools in {VERSIONS_FILE.name}")


def pinned_python() -> str:
    return PYTHON_VERSION_FILE.read_text(encoding="utf-8").strip()


def read_constraints(path: Path = CONSTRAINTS_FILE) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        match = re.fullmatch(r"([A-Za-z0-9._-]+)==([^\s;]+)", line)
        if not match:
            raise CiError(f"{path.name}: only exact pins name==version are allowed, got {line!r}")
        pins[canonical(match.group(1))] = match.group(2)
    return pins


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def default_cache(env: dict[str, str] | None = None) -> Path:
    """The tool cache. `env` is the environment to read it from (TOOL-124: the Studio asks with the environment it
    gives its child processes); None reads this process's."""
    env = os.environ if env is None else env
    if env.get("NX_CI_CACHE"):
        return Path(env["NX_CI_CACHE"])
    if os.name == "nt":
        return Path(env.get("LOCALAPPDATA", Path.home())) / "nextbsw" / "ci-cache"
    return Path(env.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "nextbsw" / "ci-cache"


# ---------------------------------------------------------------------------- pinned tools


@dataclass
class Tool:
    name: str
    version: str
    probe: list[str]
    bin: str
    url: str
    sha256: str
    optional: bool = False

    @property
    def archive(self) -> str:
        return self.url.rsplit("/", 1)[-1]


def pinned_tools(versions: dict, os_name: str | None = None) -> dict[str, Tool]:
    os_name = os_name or host_os()
    tools: dict[str, Tool] = {}
    for name, spec in versions.get("tools", {}).items():
        target = spec.get(os_name)
        if not target:
            if spec.get("optional"):
                continue
            raise CiError(f"{VERSIONS_FILE.name}: tool {name} has no download for {os_name}")
        for key in ("version", "probe", "bin"):
            if key not in spec:
                raise CiError(f"{VERSIONS_FILE.name}: tool {name} misses {key!r}")
        if not re.fullmatch(r"[0-9a-f]{64}", target.get("sha256", "")):
            raise CiError(f"{VERSIONS_FILE.name}: tool {name} ({os_name}) needs a lower-case sha256")
        tools[name] = Tool(name, spec["version"], list(spec["probe"]), spec["bin"], target["url"],
                           target["sha256"], bool(spec.get("optional")))
    return tools


def tool_dir(cache: Path, tool: Tool) -> Path:
    return cache / f"{tool.name}-{tool.version}"


def installed_bin(cache: Path, tool: Tool) -> Path | None:
    """bin directory of an installed pinned tool, None if it is not (completely) installed."""
    root = tool_dir(cache, tool)
    marker = root / MARKER
    if not marker.is_file():
        return None
    lines = marker.read_text(encoding="utf-8").splitlines()
    if len(lines) < 2 or lines[0].strip() != tool.sha256:
        return None
    found = root / lines[1].strip()
    return found if (found / (tool.probe[0] + EXE)).is_file() else None


def locate_bin(root: Path, tool: Tool) -> Path | None:
    """Directory of the probe executable in a freshly extracted archive (shortest match)."""
    exe = tool.probe[0] + EXE
    candidates = [c.parent for c in root.glob(f"**/{exe}") if tool.bin == "." or c.parent.name == tool.bin]
    return min(candidates, key=lambda p: len(p.parts)) if candidates else None


def download(url: str, target: Path, sha256: str, log=print, attempts: int = 3) -> None:
    """Streams url into target and checks the SHA-256; a wrong checksum is never retried silently."""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    for attempt in range(1, attempts + 1):
        digest = hashlib.sha256()
        size = 0
        started = time.monotonic()
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "nextbsw-ci"})
            with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as out:
                while chunk := response.read(1 << 20):
                    out.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
        except OSError as exc:
            partial.unlink(missing_ok=True)
            if attempt == attempts:
                raise CiError(f"download of {url} failed: {exc}") from exc
            log(f"  Download fehlgeschlagen ({exc}), Versuch {attempt + 1} von {attempts}")
            time.sleep(5 * attempt)
            continue
        if digest.hexdigest() != sha256:
            partial.unlink(missing_ok=True)
            raise CiError(f"SHA-256 of {url} is {digest.hexdigest()}, {VERSIONS_FILE.name} expects {sha256}")
        partial.replace(target)
        log(f"  {target.name}: {size / 1e6:.1f} MB in {time.monotonic() - started:.1f} s, SHA-256 geprueft")
        return


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def extract(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                path = destination / info.filename
                zf.extract(info, destination)
                mode = (info.external_attr >> 16) & 0o777
                if mode and os.name != "nt" and not info.is_dir():
                    path.chmod(mode)
        if os.name != "nt":  # zip archives without Unix modes (ninja): make the probe executable
            for path in destination.rglob("*"):
                if path.is_file() and not path.suffix:
                    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    elif archive.name.endswith((".tar.gz", ".tgz", ".tar.xz")):
        with tarfile.open(archive) as tf:
            tf.extractall(destination, filter="tar")
    else:
        raise CiError(f"unknown archive type: {archive.name}")


def install(cache: Path, tool: Tool, log=print) -> Path:
    found = installed_bin(cache, tool)
    if found:
        log(f"  {tool.name} {tool.version}: im Cache ({found})")
        return found
    root = tool_dir(cache, tool)
    log(f"  {tool.name} {tool.version}: lade {tool.archive}")
    shutil.rmtree(root, ignore_errors=True)
    downloads = cache / "downloads"
    archive = downloads / tool.archive
    if archive.is_file() and file_sha256(archive) == tool.sha256:
        log(f"  {archive.name}: bereits geladen, SHA-256 geprueft")
    else:
        download(tool.url, archive, tool.sha256, log)
    staging = Path(tempfile.mkdtemp(prefix=f"{tool.name}-", dir=cache))
    try:
        extract(archive, staging)
        staging.replace(root)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    archive.unlink(missing_ok=True)  # the extracted tree is what the cache keeps
    found = locate_bin(root, tool)
    if not found:
        raise CiError(f"{tool.name}: {tool.probe[0]}{EXE} not found under {root} after extracting")
    # the marker is written last: an interrupted installation is never taken for a complete one
    (root / MARKER).write_text(f"{tool.sha256}\n{found.relative_to(root).as_posix()}\n", encoding="utf-8")
    return found


def probe_version(argv: list[str], env: dict[str, str]) -> tuple[str | None, str]:
    """Runs the version probe, returns (version, first output line)."""
    exe = shutil.which(argv[0], path=env.get("PATH"))
    if not exe:
        return None, f"{argv[0]} nicht gefunden"
    try:
        out = subprocess.run([exe, *argv[1:]], capture_output=True, text=True, timeout=60, env=env).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"{exe}: {exc}"
    first = next((line.strip() for line in out.splitlines() if line.strip()), "")
    match = re.search(r"\d+\.\d+(?:\.\d+)?", first)
    return (match.group(0) if match else None), f"{first} ({exe})"


# ---------------------------------------------------------------------------- environment


def tool_environment(toolchain: str, cache: Path, versions: dict, base: dict[str, str] | None = None,
                     log=print, install_missing: bool = False) -> dict[str, str]:
    """Environment for tools/test: pinned bin directories in front of PATH and as NX_ARM_GCC_BIN."""
    env = dict(base if base is not None else os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    docs = strictdoc_venv(cache)
    if not env.get("NX_STRICTDOC_VENV") and venv_python(docs).is_file():
        env["NX_STRICTDOC_VENV"] = str(docs)   # tools/test probes it (entries trace-sdoc, requirements-html)
    if toolchain == "local":
        if env.get("NX_ARM_GCC_BIN"):  # as tools/test and the presets do
            env["PATH"] = os.pathsep.join([env["NX_ARM_GCC_BIN"], env.get("PATH", "")])
        return env
    front: list[str] = []
    for tool in pinned_tools(versions).values():
        if tool.optional:
            continue
        found = install(cache, tool, log) if install_missing else installed_bin(cache, tool)
        if not found:
            raise CiError(f"{tool.name} {tool.version} is not in the cache {cache}: "
                          "run `python -m tools.ci setup` first (or --toolchain local)")
        front.append(str(found))
        if tool.name == "arm_gcc":
            env["NX_ARM_GCC_BIN"] = str(found)  # tools/test and the presets put it in front of PATH
    env["PATH"] = os.pathsep.join([*front, env.get("PATH", "")])
    return env


# ---------------------------------------------------------------------------- checks


@dataclass
class Finding:
    area: str
    status: str
    text: str


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    def add(self, area: str, status: str, text: str) -> None:
        self.findings.append(Finding(area, status, text))

    @property
    def failed(self) -> bool:
        return any(f.status == FAIL for f in self.findings)


def check_versions(env: dict[str, str], versions: dict, strict: bool, report: Report) -> None:
    mismatch = FAIL if strict else WARN
    for tool in pinned_tools(versions).values():
        if tool.optional:
            continue
        found, detail = probe_version(tool.probe, env)
        if found is None:
            report.add("Werkzeug", mismatch, f"{tool.name}: {detail}, festgelegt {tool.version}")
        elif found != tool.version:
            report.add("Werkzeug", mismatch, f"{tool.name} {found} statt {tool.version}: {detail}")
        else:
            report.add("Werkzeug", OK, f"{tool.name} {found}: {detail}")
    want = pinned_python()
    have = platform.python_version()
    if have != want:
        report.add("Python", mismatch, f"Python {have} statt {want} ({sys.executable})")
    else:
        report.add("Python", OK, f"Python {have} ({sys.executable})")
    check_packages(read_constraints(), mismatch, report)
    check_docs_packages(env, mismatch, report)
    check_environment(report)


_setup_module_cache = None


def setup_module():
    """tools/setup/setup_env.py loaded from its file (it does the same with this module for the pins)."""
    global _setup_module_cache
    if _setup_module_cache is None:
        import importlib.util

        spec = importlib.util.spec_from_file_location("nx_setup_env", REPO / "tools" / "setup" / "setup_env.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module      # @dataclass looks its own module up in sys.modules
        spec.loader.exec_module(module)
        _setup_module_cache = module
    return _setup_module_cache


def check_environment(report: Report) -> None:
    """TOOL-63: warn when the `.venv` this runs in was built from another state of the tree.

    A workspace environment made by `python -m tools.setup` carries a marker with the tree and a hash over its
    requirements and constraints; after a pin move the hash no longer fits (customer-project.md 5.3). An
    environment installed by hand has no marker and is passed over in silence. Never red: the packages themselves
    are checked above, this only names the way to bring the environment back in line.
    """
    setup = setup_module()
    venv = Path(sys.executable).resolve().parents[1]
    marker, problem = setup.read_marker(venv)
    if problem:
        report.add("Umgebung", WARN, f"{venv}: {problem} ({setup.hint(setup.BROKEN, venv.parent)})")
        return
    if marker is None:
        return
    profile = marker.get("profile", setup.CORE)
    if Path(marker.get("tree", "")) != REPO:
        report.add("Umgebung", WARN, f"{venv} gehoert zum Baum {marker.get('tree')}, nicht zu {REPO} "
                                     f"({setup.hint(setup.OTHER_TREE, venv.parent, profile)})")
    elif marker.get("fingerprint") != setup.fingerprint(REPO, profile):
        repair = setup.hint(setup.STALE, venv.parent, profile)
        report.add("Umgebung", WARN, f"{venv} ist veraltet: requirements/constraints haben sich seit "
                                     f"{marker.get('created', '?')} geaendert ({repair})")
    else:
        report.add("Umgebung", OK, f"{venv} passt zu diesem Baum (Profil {profile}, neXtBSW "
                                   f"{marker.get('nextbsw_version') or '?'})")


_workspace_module_cache = None


def workspace_module():
    """tools/test/workspace.py loaded from its file (TOOL-67).

    What a workspace is, where its list and its pipeline live and how its pin is checked is written down once, in
    `tools/test/workspace.py`; both tools read it from there instead of repeating the rules.
    """
    global _workspace_module_cache
    if _workspace_module_cache is None:
        import importlib.util

        path = REPO / "tools" / "test" / "workspace.py"
        spec = importlib.util.spec_from_file_location("nx_test_workspace", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module      # @dataclass looks its own module up in sys.modules
        spec.loader.exec_module(module)
        _workspace_module_cache = module
    return _workspace_module_cache


@dataclass
class Target:
    """What a pipeline run works on: the tree itself, or a customer workspace (TOOL-67, gap N12).

    Everything that differs between the two runs is a field here, so the stages stay one piece of code. `ws` is
    the loaded workspace (`tools/test/workspace.Workspace`) or None for a run in the tree.
    """

    root: Path                       # the repository whose pipeline this is (tree or workspace); holds its presets
    tests_file: Path                 # the tree's test list - the workspace's own list is found by tools/test
    pipeline_file: Path
    build_root: Path
    out: Path
    ws: object | None = None

    @property
    def is_workspace(self) -> bool:
        return self.ws is not None

    @property
    def label(self) -> str:
        return f"Workspace {self.root}" if self.is_workspace else f"neXtBSW-Baum {self.root}"

    def entries(self) -> list[dict]:
        """The raw entries a run executes: the tree's list, plus the workspace's own list if there is one.

        Used to decide the preset coverage and to collect artefacts - both have to see the customer's target
        entries, not the tree's.
        """
        tests = load_test_list(self.tests_file)
        if not self.is_workspace:
            return tests
        own, wanted = [], set()
        if self.ws.list_file.is_file():
            import yaml

            data = yaml.safe_load(self.ws.list_file.read_text(encoding="utf-8")) or {}
            own = list(data.get("tests") or [])
            wanted = {str(n) for n in (data.get("inherit") or [])}      # entries the workspace asked for by name
        return own + [t for t in tests if t.get("in_workspace") or t.get("name") in wanted]


def resolve_target(args: argparse.Namespace) -> Target:
    """The Target of this call. Without --workspace everything is exactly what it was before (TOOL-03)."""
    default_out = getattr(args, "out", None)
    if getattr(args, "workspace", None) is None:
        return Target(REPO, args.tests, PIPELINE_FILE,
                      (getattr(args, "build_root", None) or default_build_root()).resolve(),
                      (default_out or REPO / "build" / "ci-out").resolve())
    support = workspace_module()
    folder = Path(args.workspace).resolve()
    if not (folder / support.PIN_FILE).is_file():
        found = support.find_workspace(folder)
        if found is None:
            raise CiError(f"kein Workspace: ueber {folder} liegt kein {support.PIN_FILE} (ARCH-56)")
        folder = found
    try:
        ws = support.open_workspace(folder, REPO)
    except support.WorkspaceError as exc:
        raise CiError(str(exc)) from None
    build_root = (getattr(args, "build_root", None) or ws.build_root).resolve()
    refusal = support.check_build_root(build_root, REPO)
    if refusal:
        raise CiError(refusal)
    pipeline = ws.pipeline_file if ws.pipeline_file.is_file() else PIPELINE_FILE
    return Target(ws.root, args.tests, pipeline, build_root,
                  (default_out or ws.root / "build" / "ci-out").resolve(), ws)


_pincheck_module_cache = None


def pincheck_module():
    """tools/nxconfig/pincheck.py loaded from its file (TOOL-65, gap N10).

    The rule "which tree may a release be built from" is written down once, in `tools/nxconfig`, so the CI, the
    Studio status line and the Studio git page give the same verdict with the same words. Loaded like
    `version.py`: the nxconfig package would pull in pydantic, and this file needs none.
    """
    global _pincheck_module_cache
    if _pincheck_module_cache is None:
        import importlib.util

        path = REPO / "tools" / "nxconfig" / "pincheck.py"
        spec = importlib.util.spec_from_file_location("nx_pincheck", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module      # @dataclass looks its own module up in sys.modules
        spec.loader.exec_module(module)
        _pincheck_module_cache = module
    return _pincheck_module_cache


def check_pin(target: Target, report: Report, release: bool = False) -> None:
    """ARCH-55/56 and TOOL-65 (gap N10): the workspace's nextbsw.yaml against the tree this run uses.

    Two halves, and they answer different questions:

      - **identity** (`tools/test/workspace.pin_findings`): is this the tree the pin names, and is it the version
        the pin names? A pipeline that builds a customer image with another tree produces an image nobody can
        reproduce - red in every run.
      - **provenance** (`tools/nxconfig/pincheck`): is that tree a released baseline - the annotated tag
        `v<version>`, reachable from `master`, clean, overlays of the same version?

    The provenance findings are **reported in every run**, with the rule's own words, so a customer sees from
    the first pipeline what his pin looks like. They only **decide** the run with `--release`; without the flag
    the worst they can be is a warning. The reason is that a workspace run always builds with the tree it runs
    from (that is what the identity half enforces), so "the pinned tree is dirty" is the same sentence as "the
    neXtBSW working copy this pipeline runs in has uncommitted changes" - true, and worth saying, but not a
    reason to stop a pipeline that is not producing a release. `--release` is the point at which an image leaves
    the house and has to be reproducible from a tag; there the rule's severities count, and `nxconfig pin`
    prints them unfiltered at any time.

    Every finding names file, line and YAML path of `nextbsw.yaml` and what to do about it.
    """
    if not target.is_workspace:
        return
    for finding in workspace_module().pin_findings(target.ws):
        report.add("Pin", OK if finding.status == "ok" else FAIL, finding.text)
    pincheck = pincheck_module()
    status = pincheck.check(target.ws.pin_file, target.ws.pin or None, release=release)
    level = {pincheck.ERROR: FAIL if release else WARN, pincheck.WARNING: WARN, pincheck.OK: OK}
    for finding in status.findings:
        report.add("Pin", level[finding.severity], str(finding))


def check_workspace_environment(target: Target, report: Report) -> None:
    """The `.venv` of the workspace against the requirements of the pinned tree (TOOL-63)."""
    if not target.is_workspace:
        return
    setup = setup_module()
    status = setup.inspect(target.root, REPO)
    if status.ok:
        report.add("Umgebung", OK, status.text)
    else:
        report.add("Umgebung", WARN, f"{status.text} ({setup.hint(status.state, target.root)})")


def _version_module():
    """tools/nxconfig/version.py loaded on its own: the nxconfig package needs pydantic, setup runs without it."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("nx_version", REPO / "tools" / "nxconfig" / "version.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rctag_module():
    """tools/ci/rctag.py loaded from its file (TOOL-232): release tags vX.Y.Z and release candidates vX.Y.Z-rcN.

    Loaded by path, so `python tools/ci` (no package) and `python -m tools.ci` find the same module."""
    module = sys.modules.get("nx_rctag")
    if module is None:
        import importlib.util

        spec = importlib.util.spec_from_file_location("nx_rctag", HERE / "rctag.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return module


def tags_at_head(repo: Path) -> list[str] | None:
    """Every tag on the checked-out commit; None if `repo` is no git checkout (or git is missing)."""
    try:
        done = subprocess.run(["git", "-C", str(repo), "tag", "--points-at", "HEAD"], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return sorted(done.stdout.split()) if done.returncode == 0 else None


def check_release_version(repo: Path, report: Report, tags: list[str] | None = None) -> None:
    """ARCH-55/SYS-68: on a release (a tag vX.Y.Z on the checked-out commit) VERSION must equal the tag.

    TOOL-232: a release candidate vX.Y.Z-rcN counts the same; its version part must equal VERSION (Release R1 =
    v1.0.0-rc1 on VERSION 1.0.0). A tag that starts like a release tag (`v` + digit) but has neither form
    (v1.0.0-rc, v1.0.0-beta1) is red as well: cm-plan.md 3.2 reserves that prefix for release tags.
    Without such a tag only a note. `tags` (all tags on HEAD) replaces the query of git (tests)."""
    nxversion = _version_module()
    rctag = rctag_module()
    try:
        version = nxversion.read_version(repo)
    except nxversion.VersionError as exc:
        report.add("Version", FAIL, str(exc))
        return
    if tags is None:
        tags = tags_at_head(repo)
    if tags is None:
        report.add("Version", OK, f"VERSION {version}; kein git-Checkout, Release-Tag nicht pruefbar")
        return
    release_like = [t for t in tags if rctag.looks_like_release(t)]
    if not release_like:
        report.add("Version", OK, f"VERSION {version}; kein Release-Tag vX.Y.Z auf diesem Commit (Entwicklungsstand, "
                                  f"{nxversion.git_describe(repo) or '?'})")
        return
    wrong = [p for p in (rctag.problem(t, version) for t in release_like) if p]
    if wrong:
        report.add("Version", FAIL, "; ".join(wrong))
        return
    tag = rctag.pick(release_like, version)
    kind = " (Release-Kandidat)" if rctag.is_candidate(tag) else ""
    report.add("Version", OK, f"VERSION {version} == Release-Tag {tag}{kind}")


def release_tag_of_tree(repo: Path, version: str, tags: list[str] | None = None) -> str:
    """The release tag a release run of the tree checks (TOOL-232): `v<VERSION>` if HEAD carries it, else the newest
    release candidate `v<VERSION>-rcN` on HEAD, else `v<VERSION>` (whose absence the rule then reports)."""
    if tags is None:
        tags = tags_at_head(repo) or []
    return rctag_module().pick(tags, version) or f"v{version}"


def check_release_tree(repo: Path, report: Report, release: bool = False, tags: list[str] | None = None) -> None:
    """TOOL-65 (gap N10) for a run inside the tree: is this tree itself a released baseline?

    Only with `--release`. A normal run of the tree is the regression guard of a development branch - asking it
    for a tag would make every working day red. A release run asks the same question the pin check asks of a
    customer's submodule: annotated tag `v<VERSION>`, reachable from `master`, clean. TOOL-232: a release candidate
    `v<VERSION>-rcN` on HEAD is judged by the same rule in place of `v<VERSION>` (`pincheck.check_tree` names the tag
    `v` + the version it is given, so it is given `X.Y.Z-rcN`). `tags` replaces the query of git (tests).
    """
    if not release:
        return
    pincheck = pincheck_module()
    nxversion = _version_module()
    try:
        version = nxversion.read_version(repo)
    except nxversion.VersionError:
        version = ""                         # check_tree reports the broken VERSION itself (tree-version)
    tag = release_tag_of_tree(repo, version, tags) if version else ""
    status = pincheck.check_tree(repo, tag[1:] if rctag_module().is_candidate(tag) else "", release=True)
    level = {pincheck.ERROR: FAIL, pincheck.WARNING: WARN, pincheck.OK: OK}
    for finding in status.findings:
        report.add("Release", level[finding.severity], str(finding))


# ---------------------------------------------------------------------------- provenance of a report (SYS-67)


def provenance(repo: Path) -> dict:
    """Which state of `repo` a run judged (SYS-67, cm-plan.md gap G1).

    Full commit, tree hash, `git describe`, the OpenBSW gitlink, VERSION and the changes of the working tree
    (tracked and untracked; ignored files such as build/ do not count, exactly like `git status`). An archived
    report is bound to a release tag through `tree`: the tagged commit carries the same tree, and the run was clean
    (`tools/ci/release.py: report_binding`). Outside a git checkout every git field is None."""

    def git(*args: str) -> str | None:
        try:
            done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=120)
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout.strip() if done.returncode == 0 else None

    try:
        version = (repo / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        version = None
    commit = git("rev-parse", "--verify", "--quiet", "HEAD^{commit}")
    if not commit:
        return {"commit": None, "tree": None, "describe": None, "openbsw": None, "version": version,
                "dirty": None, "changes": [], "changes_total": 0}
    status = git("status", "--porcelain")
    changes = status.splitlines() if status else []
    return {
        "commit": commit,
        "tree": git("rev-parse", "HEAD^{tree}"),
        "describe": git("describe", "--tags", "--always", "--dirty", "--abbrev=12"),
        "openbsw": git("rev-parse", "--verify", "--quiet", "HEAD:external/openbsw"),
        "version": version,
        "dirty": None if status is None else bool(changes),
        "changes": changes[:20],
        "changes_total": len(changes),
    }


def judge_provenance(start: dict, end: dict, strict: bool, report: Report) -> None:
    """A report is evidence for one tree only (SYS-67): name a run that cannot be bound to a commit, a dirty tree,
    and a tree that changed while the run took its hours. A moved HEAD mixes two states in one report - red with
    --strict; new local changes during the run (a test writing into the tree) are a warning."""
    if not start.get("commit"):
        report.add("Herkunft", WARN, "kein git-Checkout: der Bericht ist an keinen Commit gebunden")
        return
    if start.get("dirty"):
        report.add("Herkunft", WARN, f"Arbeitsbaum beim Start geaendert ({start['changes_total']} Pfade, z. B. "
                                     f"{start['changes'][0].strip()}): der Bericht ist an keinen Tag bindbar")
    if (end.get("commit"), end.get("tree")) != (start.get("commit"), start.get("tree")):
        report.add("Herkunft", FAIL if strict else WARN,
                   f"HEAD hat sich waehrend des Laufs bewegt ({str(start.get('commit'))[:12]} -> "
                   f"{str(end.get('commit'))[:12]}): der Bericht mischt zwei Staende")
    elif end.get("changes") != start.get("changes"):
        new = [c.strip() for c in end.get("changes", []) if c not in start.get("changes", [])]
        report.add("Herkunft", WARN, f"Arbeitsbaum waehrend des Laufs geaendert ({end.get('changes_total', 0)} "
                                     f"Pfade{', neu z. B. ' + new[0] if new else ''}): ein Test schreibt in den Baum "
                                     f"oder jemand arbeitete darin")


def check_docs_packages(env: dict[str, str], mismatch: str, report: Report) -> None:
    """The StrictDoc venv (DOC-21) against tools/ci/constraints-docs.txt."""
    venv = env.get("NX_STRICTDOC_VENV")
    python = venv_python(Path(venv)) if venv else None
    if python is None or not python.is_file():
        report.add("StrictDoc", mismatch, "StrictDoc-venv fehlt (python -m tools.ci setup --tool strictdoc, DOC-21)")
        return
    probe = subprocess.run([str(python), "-c", "import importlib.metadata as m, json; print(json.dumps("
                            "{d.metadata['Name']: d.version for d in m.distributions() if d.metadata['Name']}))"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
    if probe.returncode != 0:
        report.add("StrictDoc", mismatch, f"StrictDoc-venv nicht lesbar: {probe.stderr.strip()[-200:]}")
        return
    installed = {canonical(k): v for k, v in json.loads(probe.stdout).items()}
    pins = read_constraints(DOCS_CONSTRAINTS)
    wrong = [f"{name} fehlt" if name not in installed else f"{name} {installed[name]} statt {version}"
             for name, version in sorted(pins.items()) if installed.get(name) != version]
    wrong += [f"{name} steht nicht in {DOCS_CONSTRAINTS.name}" for name in sorted(set(installed) - set(pins))]
    if wrong:
        report.add("StrictDoc", mismatch, "; ".join(wrong))
    else:
        report.add("StrictDoc", OK, f"strictdoc {installed.get('strictdoc')} und {len(pins) - 1} weitere Pakete "
                                    f"wie {DOCS_CONSTRAINTS.name} ({venv})")


def _package_problems(names: set[str], installed: dict[str, str], pins: dict[str, str]) -> list[str]:
    wrong = []
    for name in sorted(names):
        if name not in installed:
            wrong.append(f"{name} fehlt")
        elif name in pins and installed[name] != pins[name]:
            wrong.append(f"{name} {installed[name]} statt {pins[name]}")
        elif name not in pins:
            wrong.append(f"{name} steht nicht in {CONSTRAINTS_FILE.name}")
    return wrong


def check_packages(pins: dict[str, str], mismatch: str, report: Report) -> None:
    """Core packages and Studio packages separately (TOOL-63).

    The core list must fit - without it nothing generates, checks or builds. The Studio packages are reported on
    their own: a machine that only builds and checks may leave them out (then "uebersprungen", never green by
    accident), but a half-installed or wrongly pinned Qt is a finding like any other.
    """
    from importlib import metadata

    installed = {canonical(d.metadata["Name"]): d.version for d in metadata.distributions() if d.metadata["Name"]}
    required = requirement_names()
    studio = requirement_names(STUDIO_REQUIREMENTS)
    wrong = _package_problems(required, installed, pins)
    for name, version in sorted(pins.items()):
        if name not in required | studio and name in installed and installed[name] != version:
            wrong.append(f"{name} {installed[name]} statt {version}")
    if wrong:
        report.add("Python-Pakete", mismatch, "; ".join(wrong))
    else:
        report.add("Python-Pakete", OK, f"{len(required)} Kernpakete aus {REQUIREMENTS_FILE.name} wie "
                                        f"{CONSTRAINTS_FILE.name}")
    studio_wrong = _package_problems(studio, installed, pins)
    if not studio & set(installed):
        report.add("Studio-Pakete", SKIP, f"{len(studio)} Pakete aus {STUDIO_REQUIREMENTS.name} nicht installiert "
                                          f"(nur fuer tools/studio noetig; python -m tools.setup venv . --studio)")
    elif studio_wrong:
        report.add("Studio-Pakete", mismatch, "; ".join(studio_wrong))
    else:
        report.add("Studio-Pakete", OK, f"{len(studio)} Pakete aus {STUDIO_REQUIREMENTS.name} wie "
                                        f"{CONSTRAINTS_FILE.name}")


def requirement_names(path: Path = REQUIREMENTS_FILE) -> set[str]:
    names = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            names.add(canonical(re.split(r"[<>=~!\[; ]", line, 1)[0]))
    return names


def configure_presets(presets_file: Path, seen: set[Path] | None = None) -> list[dict]:
    """configurePresets of a preset file and of the files it includes (ARCH-57: the base presets of neXtBSW live
    in cmake/NxPresets.json so a workspace outside the tree can include the same file)."""
    presets_file = Path(presets_file).resolve()
    seen = set() if seen is None else seen
    if presets_file in seen or not presets_file.is_file():
        return []
    seen.add(presets_file)
    data = json.loads(presets_file.read_text(encoding="utf-8"))
    found = [p for p in data.get("configurePresets", []) if isinstance(p, dict) and "name" in p]
    for include in data.get("include", []):
        found += configure_presets(presets_file.parent / include, seen)
    return found


def visible_projects(presets_file: Path, worktree_bases: list[str]) -> dict[str, list[str]]:
    """{build profile: [visible preset names]} without presets that inherit from a worktree base.

    A profile is a project (NX_PROJECT) together with the effective cache variables of the preset. Presets of the
    same project with the same variables are aliases; a preset with a cache variable of its own (e.g. NX_ETHERNET,
    BUILD_TARGET_RTOS, PLAT-71) is a profile of its own, shown as "<project> [VAR=value, ...]" with the variables
    that differ from the preset that sets NX_PROJECT. Without this, a dropped build test of such a variant would go
    unnoticed because another preset of the same project is still built."""
    presets = {p["name"]: p for p in configure_presets(presets_file)}

    def parents(name: str) -> list[str]:
        value = presets.get(name, {}).get("inherits", [])
        return [value] if isinstance(value, str) else list(value)

    def chain(name: str) -> list[str]:
        result = [name]
        for parent in parents(name):
            result += chain(parent)
        return result

    def plain(value: object) -> str:
        value = value.get("value") if isinstance(value, dict) else value
        return value if isinstance(value, str) else json.dumps(value)

    def variables(name: str) -> dict[str, str]:
        # CMake: the preset's own value wins, then the parents in the order of "inherits"
        merged: dict[str, str] = {}
        for parent in reversed(parents(name)):
            merged.update(variables(parent))
        merged.update({k: plain(v) for k, v in presets.get(name, {}).get("cacheVariables", {}).items()})
        return merged

    def project_root(name: str) -> str | None:
        for link in chain(name):
            if "NX_PROJECT" in presets.get(link, {}).get("cacheVariables", {}):
                return link
        return None

    profiles: dict[str, list[str]] = {}
    keys: dict[str, tuple] = {}
    for name, preset in presets.items():
        if preset.get("hidden") or set(chain(name)[1:]) & set(worktree_bases):
            continue
        root = project_root(name)
        if root is None:
            continue
        own = variables(name)
        base = variables(root)
        diff = sorted((k, v) for k, v in own.items() if k != "NX_PROJECT" and base.get(k) != v)
        label = own["NX_PROJECT"] + (f" [{', '.join(f'{k}={v}' for k, v in diff)}]" if diff else "")
        key = tuple(sorted(own.items()))
        if label in keys and keys[label] != key:      # same label, other variables: name them all
            label = own["NX_PROJECT"] + f" [{', '.join(f'{k}={v}' for k, v in key if k != 'NX_PROJECT')}]"
        keys.setdefault(label, key)
        profiles.setdefault(label, []).append(name)
    return profiles


def check_presets(tests: list[dict], pipeline: dict, repo: Path, report: Report) -> list[dict]:
    """Every visible build profile is built by tools/test or excluded with a reason in pipeline.toml."""
    section = pipeline.get("presets", {})
    projects = visible_projects(repo / "CMakePresets.json", section.get("worktree_bases", []))
    built = {t["preset"] for t in tests if t.get("kind") == "cmake_preset"}
    not_built = section.get("not_built", {})
    skipped: list[dict] = []
    for proj, names in sorted(projects.items()):
        covered = sorted(set(names) & built)
        excluded = sorted(set(names) & set(not_built))
        short = proj.replace("${sourceDir}/", "")
        if covered:
            report.add("Profile", OK, f"{short}: gebaut ({', '.join(covered)})")
        elif excluded:
            entry = not_built[excluded[0]]
            note = entry["reason"]
            env_name = entry.get("requires_env")
            if env_name and os.environ.get(env_name):
                note += f" - {env_name} ist hier gesetzt: Target-Eintrag in tools/test/tests.yaml ergaenzen"
            report.add("Profile", SKIP, f"{short} ({excluded[0]}): {note}")
            skipped.append({"name": excluded[0], "reason": note})
        else:
            report.add("Profile", FAIL, f"{short} ({', '.join(names)}): Profil ohne CI-Entscheidung - "
                                        "Target-Eintrag in tools/test/tests.yaml oder presets.not_built in "
                                        "tools/ci/pipeline.toml")
    for name in sorted(set(not_built) - {n for names in projects.values() for n in names}):
        report.add("Profile", FAIL, f"pipeline.toml nennt unbekanntes Preset {name}")
    return skipped


def load_test_list(path: Path) -> list[dict]:
    import yaml  # installed by `setup --python-packages`; tools/test needs it as well

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return list(data.get("tests") or [])


# ---------------------------------------------------------------------------- commands


def cmd_setup(args: argparse.Namespace) -> int:
    versions = load_toml(VERSIONS_FILE)
    cache = args.cache.resolve()
    print(f"Werkzeug-Cache: {cache}")
    names = args.tool or [n for n, t in pinned_tools(versions).items() if not t.optional] + ["misra", "strictdoc"]
    tools = pinned_tools(versions)
    for name in names:
        if name == "misra":
            install_misra_tools(cache)
            continue
        if name == "strictdoc":
            install_strictdoc(cache)
            continue
        if name not in tools:
            raise CiError(f"unknown tool {name} (known: {', '.join(tools)}, misra, strictdoc)")
        install(cache, tools[name])
    if args.python_packages:
        pip = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
        pins = read_constraints()
        if "pip" in pins:
            run_logged([*pip, f"pip=={pins['pip']}"])
        # which lists: python_package_files (core, Studio unless --core-only, website). A workspace uses
        # python -m tools.setup instead.
        run_logged([*pip, *python_package_files(args.core_only), "-c", str(CONSTRAINTS_FILE)])
    return 0


def python_package_files(core_only: bool = False) -> list[str]:
    """The `-r` arguments of `setup --python-packages`.

    TOOL-63: CI runs the Studio tests, so the core and the Studio list are installed by default; --core-only is for a
    machine that only builds and checks (no Qt). TOOL-232: the documentation website packages (DOC-71,
    tools/requirements-site.txt, every one pinned exactly in the file itself) are installed in both cases - without
    them `docgen-site` is skipped, and under `run --strict` a skipped entry is red."""
    files = ["-r", str(REQUIREMENTS_FILE)]
    if not core_only:
        files += ["-r", str(STUDIO_REQUIREMENTS)]
    return files + ["-r", str(SITE_REQUIREMENTS)]


def misra_venv(cache: Path) -> Path:
    """venv of the static analysis tools in the tool cache (pip wheels, no installer, PLAT-74)."""
    return cache / "misra-venv"


def install_misra_tools(cache: Path) -> Path:
    """cppcheck (with the MISRA add-on) and clang-tidy at the versions of tools/misra/requirements.txt."""
    venv = misra_venv(cache)
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        run_logged([sys.executable, "-m", "venv", str(venv)])
    run_logged([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(MISRA_REQUIREMENTS)])
    return venv


def strictdoc_venv(cache: Path) -> Path:
    """venv of StrictDoc in the tool cache (DOC-21); never the .venv of the repository or the Studio build."""
    return cache / "strictdoc-venv"


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def install_strictdoc(cache: Path) -> Path:
    """strictdoc and its dependencies at the versions of tools/ci/constraints-docs.txt."""
    venv = strictdoc_venv(cache)
    python = venv_python(venv)
    if not python.is_file():
        run_logged([sys.executable, "-m", "venv", str(venv)])
    pip = [str(python), "-m", "pip", "install", "--disable-pip-version-check"]
    pins = read_constraints(DOCS_CONSTRAINTS)
    if "pip" in pins:
        run_logged([*pip, f"pip=={pins['pip']}"])
    run_logged([*pip, "-r", str(DOCS_REQUIREMENTS), "-c", str(DOCS_CONSTRAINTS)])
    return venv


def run_logged(argv: list[str], env: dict[str, str] | None = None, cwd: Path = REPO) -> None:
    print("$ " + subprocess.list2cmdline(argv), flush=True)
    code = subprocess.run(argv, env=env, cwd=cwd).returncode
    if code != 0:
        raise CiError(f"command failed with exit code {code}: {subprocess.list2cmdline(argv)}")


def print_findings(report: Report) -> None:
    mark = {OK: "OK  ", WARN: "WARN", FAIL: "FEHL", SKIP: "UEBS"}
    for f in report.findings:
        print(f"  {mark[f.status]} {f.area:<14} {f.text}")


def cmd_check(args: argparse.Namespace) -> int:
    report = Report()
    target = resolve_target(args)
    versions = load_toml(VERSIONS_FILE)
    pipeline = load_toml(target.pipeline_file)
    env = tool_environment(args.toolchain, args.cache.resolve(), versions)
    print(target.label)
    check_pin(target, report, release=args.release)
    check_versions(env, versions, args.strict, report)
    check_workspace_environment(target, report)
    check_release_version(REPO, report)
    check_release_tree(REPO, report, args.release)
    check_presets(target.entries(), pipeline, target.root, report)
    print_findings(report)
    return 1 if report.failed else 0


def default_build_root() -> Path:
    sys.path.insert(0, str(REPO))
    from tools.test.run_tests import default_build_root as test_default

    return test_default(REPO) / "ci"


def cmd_run(args: argparse.Namespace) -> int:
    if args.strict and (args.no_inventory or args.k or args.group):
        raise CiError("--strict runs the complete list: no --no-inventory, -k or --group")
    started = time.time()
    target = resolve_target(args)
    out = target.out
    out.mkdir(parents=True, exist_ok=True)
    versions = load_toml(VERSIONS_FILE)
    pipeline = load_toml(target.pipeline_file)
    cache = args.cache.resolve()
    build_root = target.build_root
    stages: list[dict] = []
    report = Report()
    print(f"{target.label}\nAblauf: {target.pipeline_file}\nBau-Wurzel: {build_root}\nBericht: {out}")
    # SYS-67 (cm-plan G1): the state the report is about, taken before anything is built
    origin = {"tree": provenance(REPO)}
    if target.is_workspace:
        origin["workspace"] = provenance(target.root)
    print(f"Stand: {origin['tree']['describe'] or 'kein git-Checkout'} (Commit {origin['tree']['commit'] or '-'})")
    args.origin = origin

    def stage(name: str, func) -> object:
        print(f"\n=== {name} ===", flush=True)
        clock = time.monotonic()
        try:
            result = func()
            status = OK
        except CiError as exc:
            report.add(name, FAIL, str(exc))
            print(f"FEHLER: {exc}", flush=True)
            result, status = None, FAIL
        stages.append({"name": name, "status": status, "duration_s": round(time.monotonic() - clock, 1)})
        return result

    env = stage("Werkzeuge bereitstellen", lambda: tool_environment(
        args.toolchain, cache, versions, log=print, install_missing=not args.no_install))
    if env is None:
        return finish(out, args, report, stages, started, None, [], build_root, target=target)

    def checks() -> list[dict]:
        check_pin(target, report, release=args.release)
        check_versions(env, versions, args.strict, report)
        check_workspace_environment(target, report)
        check_release_version(REPO, report)
        check_release_tree(REPO, report, args.release)
        skipped = check_presets(target.entries(), pipeline, target.root, report)
        print_findings(report)
        return skipped

    skipped_presets = stage("Pin, Werkzeuge und Profile pruefen" if target.is_workspace
                            else "Werkzeuge und Profile pruefen", checks) or []

    json_path = out / "tests.json"
    log_path = out / "tests.log"

    def tests() -> dict:
        argv = [sys.executable, "-m", "tools.test", "--jobs", str(args.jobs), "--json", str(json_path),
                "--build-root", str(build_root), "--tests", str(args.tests)]
        if target.is_workspace:
            argv += ["--workspace", str(target.root)]
        for group in args.group or pipeline.get("run", {}).get("groups", ["all"]):
            argv += ["--group", group]
        if pipeline.get("run", {}).get("clean", True) and not args.incremental:
            argv.append("--clean")
        if args.slow:
            argv.append("--slow")
        if args.k:
            argv += ["-k", args.k]
        if args.no_inventory:
            argv.append("--no-inventory")
        if "--clean" in argv:
            # --clean-first keeps the CMake cache and with it the compiler of an earlier run (other toolchain):
            # a clean run starts from an empty build root
            wipe_build_root(build_root)
        print("$ " + subprocess.list2cmdline(argv), flush=True)
        json_path.unlink(missing_ok=True)
        with log_path.open("w", encoding="utf-8", buffering=1) as log:  # line buffered: can be followed live
            proc = subprocess.Popen(argv, cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="replace")
            for line in proc.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()
                log.write(line)
            code = proc.wait()
        if not json_path.is_file():
            raise CiError(f"tools/test ended with exit code {code} without a result file (see {log_path.name})")
        result = json.loads(json_path.read_text(encoding="utf-8"))
        result["exit_code"] = code
        return result

    test_result = stage("Tests und Target-Builds (tools/test)", tests)
    if test_result:
        judge_tests(test_result, pipeline, args, report)

    coverage = None
    # TOOL-217 (customer-project.md gap N19): in a workspace run both stages measure the customer's own code against
    # the workspace's own thresholds (ci/coverage.yaml) and baseline (ci/misra/) - never the tree's. A stage that does
    # not run is named with its reason, never left out silently and never green.
    ws_problem = workspace_stage_problem(target, "coverage", pipeline, args) if target.is_workspace else None
    if ws_problem:
        report.add("Abdeckung", *ws_problem)
    elif coverage_wanted(pipeline, args):
        coverage = stage("Testabdeckung (tools/test/coverage.py)",
                         lambda: measure_coverage(pipeline, args, out, build_root, env,
                                                  target.root if target.is_workspace else None))
        judge_coverage(coverage, pipeline, report, target.pipeline_file)

    static = None
    ws_problem = workspace_stage_problem(target, "static_analysis", pipeline, args) if target.is_workspace else None
    if ws_problem:
        report.add("Statik", *ws_problem)
    elif static_analysis_wanted(pipeline, args):
        static = stage("Statische Analyse (tools/misra)",
                       lambda: run_static_analysis(pipeline, args, out, build_root, env, cache,
                                                   target.root if target.is_workspace else None))
        judge_static_analysis(static, pipeline, args, report)

    artifacts = stage("Artefakte sammeln", lambda: collect_artifacts(
        target.entries(), pipeline, build_root, out / "artifacts", env, target.root)) or []
    return finish(out, args, report, stages, started, test_result, artifacts, build_root, skipped_presets,
                  pipeline, coverage, static, target)


# ---------------------------------------------------------------------------- workspace: own code (TOOL-217)


def workspace_stage_problem(target: Target, section: str, pipeline: dict,
                            args: argparse.Namespace) -> tuple[str, str] | None:
    """Why the coverage or static analysis stage of a workspace run does not run - (status, text) - or None.

    Gap N19 of customer-project.md: the stages measure the customer's own code with the workspace's own files
    (tools/test/workspace.py). A stage switched off in ci/pipeline.toml is named as not run (SKIP); a stage switched
    on without its files is red - the customer asked for a gate he has not configured.
    """
    ws = target.ws
    where = _display(target.pipeline_file, target.root)
    wanted = coverage_wanted(pipeline, args) if section == "coverage" else static_analysis_wanted(pipeline, args)
    if not wanted:
        if not pipeline.get(section, {}).get("enabled", False):
            return SKIP, (f"im Workspace-Lauf ausgeschaltet ({where}, [{section}] enabled); eigener Code wird "
                          f"nicht {'gemessen' if section == 'coverage' else 'analysiert'} - Vorlage: "
                          f"python -m tools.ci template {'coverage' if section == 'coverage' else 'misra-deviations'}")
        if (args.no_coverage if section == "coverage" else args.no_static_analysis):
            return SKIP, f"abgewaehlt (--no-{'coverage' if section == 'coverage' else 'static-analysis'})"
        return SKIP, "Teillauf (-k/--group): nur ein Lauf ueber die ganze Liste sagt etwas"
    if section == "coverage":
        if not ws.coverage_file.is_file():
            return FAIL, (f"{_display(ws.coverage_file, target.root)} fehlt, {where} schaltet [coverage] ein - "
                          f"python -m tools.ci template coverage --out {ws.root}")
        return None
    deviations = ws.misra_dir / "deviations.yaml"
    if not deviations.is_file():
        return FAIL, (f"{_display(deviations, target.root)} fehlt, {where} schaltet [static_analysis] ein - "
                      f"python -m tools.ci template misra-deviations --out {ws.root}")
    if not pipeline.get("static_analysis", {}).get("compile_db"):
        return FAIL, (f"{where}: static_analysis.compile_db fehlt - die Compile-Datenbank des eigenen "
                      f"Target-Builds relativ zur Bau-Wurzel, z. B. \"main/compile_commands.json\"")
    return None


def _display(path: Path, root: Path) -> str:
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------- static analysis (PLAT-74)


def static_analysis_wanted(pipeline: dict, args: argparse.Namespace) -> bool:
    """Runs over the target build of the whole list; a trial run with -k/--group may not have built it."""
    if args.no_static_analysis or not pipeline.get("static_analysis", {}).get("enabled", False):
        return False
    return not (args.k or args.group)


def run_static_analysis(pipeline: dict, args: argparse.Namespace, out: Path, build_root: Path,
                        env: dict[str, str], cache: Path, workspace: Path | None = None) -> dict:
    """`python -m tools.misra run` over the compile database of the target build; returns its result.json.

    `workspace` (TOOL-217): analyse the customer's own code with the workspace's deviations and baseline."""
    settings = pipeline.get("static_analysis", {})
    compile_db = build_root / settings.get("compile_db", "demo/compile_commands.json")
    if not compile_db.is_file():
        return {"error": f"Compile-Datenbank fehlt: {compile_db} (Target-Build nicht gelaufen?)"}
    target = out / "misra"
    env = {**env}
    env.setdefault("NX_MISRA_VENV", str(misra_venv(cache)))
    argv = [sys.executable, "-m", "tools.misra", "run", "--compile-db", str(compile_db), "--out", str(target),
            "--jobs", str(args.jobs)]
    if workspace is not None:
        argv += ["--workspace", str(workspace)]
    print("$ " + subprocess.list2cmdline(argv), flush=True)
    (target / "result.json").unlink(missing_ok=True)
    proc = subprocess.run(argv, cwd=REPO, env=env, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    target.mkdir(parents=True, exist_ok=True)
    (out / "misra.log").write_text((proc.stdout or "") + (proc.stderr or ""), encoding="utf-8")
    print((proc.stdout or "")[-4000:] + (proc.stderr or "")[-2000:], flush=True)
    if not (target / "result.json").is_file():
        raise CiError(f"tools/misra endete mit Exit-Code {proc.returncode} ohne Ergebnis (siehe misra.log)")
    payload = json.loads((target / "result.json").read_text(encoding="utf-8"))
    payload["exit_code"] = proc.returncode
    return payload


def judge_static_analysis(static: dict | None, pipeline: dict, args: argparse.Namespace, report: Report) -> None:
    settings = pipeline.get("static_analysis", {})
    area = "Statik"
    if static is None:
        report.add(area, FAIL, "Analyse ohne Ergebnis (siehe misra.log)")
        return
    if "error" in static:
        report.add(area, FAIL if args.strict else WARN, static["error"])
        return
    # a missing tool is never green: skipped with a hint, red in a strict run (CI installs them in setup)
    for tool, why in static.get("skipped_tools", {}).items():
        hard = args.strict and settings.get("strict_tools", True)
        report.add(area, FAIL if hard else SKIP, f"{tool} nicht gelaufen: {why}")
    new = static.get("new", [])
    if new:
        first = "; ".join(f"{f['file']}:{f['line']} {f['rule']}" for f in new[:5])
        report.add(area, FAIL if settings.get("enforce", True) else WARN,
                   f"{len(new)} neue Befunde weder in der Baseline noch in der Abweichungsliste: {first}"
                   + (" ..." if len(new) > 5 else ""))
    resolved = sum(r["count"] for r in static.get("resolved", []))
    if resolved:
        report.add(area, WARN, f"{resolved} Befunde der Baseline behoben - `python -m tools.misra baseline` "
                               f"verschaerft sie")
    for message in static.get("messages", []):
        if message["severity"] == "warning":
            report.add(area, WARN, f"{message['where']}: {message['text']}")
    if static.get("exit_code") not in (0, 1, 3, None) and not report.failed:
        report.add(area, FAIL, f"tools/misra endete mit Exit-Code {static['exit_code']}")


# ---------------------------------------------------------------------------- coverage (TOOL-40)


def coverage_wanted(pipeline: dict, args: argparse.Namespace) -> bool:
    """Coverage runs over the whole list; a trial run with -k/--group measures nothing meaningful."""
    if args.no_coverage or not pipeline.get("coverage", {}).get("enabled", False):
        return False
    return not (args.k or args.group)


def measure_coverage(pipeline: dict, args: argparse.Namespace, out: Path, build_root: Path,
                     env: dict[str, str], workspace: Path | None = None) -> dict:
    """Runs tools/test/coverage.py and returns its JSON (plus how the run itself ended).

    `workspace` (TOOL-217): the zig entries of the workspace list over the customer's own code, thresholds from
    its ci/coverage.yaml."""
    settings = pipeline.get("coverage", {})
    json_path = out / "coverage.json"
    log_path = out / "coverage.log"
    argv = [sys.executable, str(REPO / "tools" / "test" / "coverage.py"), "--jobs", str(args.jobs),
            "--json", str(json_path), "--markdown", str(out / "coverage.md"),
            "--build-root", str(build_root), "--tests", str(args.tests)]
    if settings.get("enforce", False):
        argv.append("--enforce")
    if workspace is not None:
        argv += ["--workspace", str(workspace)]
    print("$ " + subprocess.list2cmdline(argv), flush=True)
    json_path.unlink(missing_ok=True)
    proc = subprocess.run(argv, cwd=REPO, env=env, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    log_path.write_text((proc.stdout or "") + (proc.stderr or ""), encoding="utf-8")
    print((proc.stdout or "")[-4000:], flush=True)
    if not json_path.is_file():
        raise CiError(f"coverage.py endete mit Exit-Code {proc.returncode} ohne Ergebnisdatei "
                      f"(siehe {log_path.name})")
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    payload["exit_code"] = proc.returncode
    return payload


def judge_coverage(coverage: dict | None, pipeline: dict, report: Report, pipeline_file: Path | None = None) -> None:
    settings = pipeline.get("coverage", {})
    hard = bool(settings.get("strict", False))
    if coverage is None:
        report.add("Abdeckung", FAIL if hard else WARN, "Messlauf ohne Ergebnis (siehe coverage.log)")
        return
    entries = coverage.get("entries", {})
    for item in entries.get("items", []):
        if item["status"] == "fail":
            report.add("Abdeckung", FAIL if hard else WARN,
                       f"{item['name']}: {item['reason'].splitlines()[0][:160]}")
        elif item["status"] == "skip":
            report.add("Abdeckung", SKIP, f"{item['name']}: {item['reason'].splitlines()[0][:160]}")
    under = [m for m in coverage.get("modules", []) if m["verdict"] == "unter Schwelle"]
    for module in under:
        report.add("Abdeckung", FAIL if coverage.get("enforced") else WARN,
                   f"{module['module']}: Anweisungen {module['line_percent']:.1f} % "
                   f"(Schwelle {module['threshold_lines']:.0f}), Zweige {module['branch_percent']:.1f} % "
                   f"(Schwelle {module['threshold_branches']:.0f})")
    if not coverage.get("enforced"):
        where = "tools/ci/pipeline.toml" if pipeline_file in (None, PIPELINE_FILE) else str(pipeline_file)
        report.add("Abdeckung", SKIP, f"Schwellen werden berichtet, nicht erzwungen ({where}, [coverage] enforce)")


def wipe_build_root(build_root: Path, repo: Path = REPO) -> None:
    build_root = build_root.resolve()
    repo = repo.resolve()
    if build_root == repo or build_root in repo.parents or build_root.parent == build_root:
        raise CiError(f"refusing to delete build root {build_root} (repository or one of its parents)")
    if build_root.exists():
        print(f"Bau-Wurzel wird geleert: {build_root}", flush=True)
        def writable(func, path, _exc):  # read-only files (git objects of generated trees) on Windows
            os.chmod(path, stat.S_IWRITE)
            func(path)

        shutil.rmtree(build_root, onexc=writable)
        if build_root.exists():
            raise CiError(f"build root {build_root} could not be deleted")


def judge_tests(result: dict, pipeline: dict, args: argparse.Namespace, report: Report) -> None:
    allowed = pipeline.get("allowed_skips", {})
    for test in result["tests"]:
        if test["status"] in ("fail", "timeout"):
            report.add("Test", FAIL, f"{test['name']} ({test['group']}): {test['reason']}")
        elif test["status"] == "skip":
            if test["name"] in allowed:
                report.add("Test", SKIP, f"{test['name']}: {allowed[test['name']]} ({test['reason']})")
            else:
                report.add("Test", FAIL if args.strict else WARN,
                           f"{test['name']} unerwartet uebersprungen: {test['reason']}")
    for path in result.get("unlisted_test_files", []):
        report.add("Test", FAIL, f"{path} steht nicht in tools/test/tests.yaml")
    if result.get("exit_code") not in (0, None) and not report.failed:
        report.add("Test", FAIL, f"tools/test endete mit Exit-Code {result['exit_code']}")
    summary = result["summary"]
    if summary["total"] == 0:
        report.add("Test", FAIL, "kein Test gelaufen")


def collect_artifacts(tests: list[dict], pipeline: dict, build_root: Path, target: Path,
                      env: dict[str, str], root: Path = REPO) -> list[dict]:
    shutil.rmtree(target, ignore_errors=True)
    patterns = pipeline.get("artifacts", {}).get("patterns", ["**/*.elf"])
    size_tool = shutil.which("arm-none-eabi-size", path=env.get("PATH"))
    collected: list[dict] = []
    for test in tests:
        if test.get("kind") != "cmake_preset":
            continue
        binary_dir = Path(str(test.get("binary_dir", "{build}/" + test["preset"])).format(
            build=build_root, repo=REPO, tree=REPO, workspace=root, tmp=build_root, python=sys.executable))
        if not binary_dir.is_dir():
            continue
        for pattern in patterns:
            for path in sorted(binary_dir.glob(pattern)):
                if not path.is_file():
                    continue
                dest = target / test["name"] / path.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)
                entry = {"profile": test["name"], "file": dest.relative_to(target.parent).as_posix(),
                         "bytes": path.stat().st_size}
                if path.suffix == ".elf" and size_tool:
                    sizes = subprocess.run([size_tool, str(path)], capture_output=True, text=True, env=env).stdout
                    row = sizes.splitlines()[1].split() if len(sizes.splitlines()) > 1 else []
                    if len(row) >= 3:
                        entry.update(text=int(row[0]), data=int(row[1]), bss=int(row[2]))
                collected.append(entry)
                print(f"  {entry['file']} ({entry['bytes']} Bytes)")
    if not collected:
        print("  keine Target-Erzeugnisse gefunden")
    return collected


def finish(out: Path, args: argparse.Namespace, report: Report, stages: list[dict], started: float,
           test_result: dict | None, artifacts: list[dict], build_root: Path,
           skipped_presets: list[dict] | None = None, pipeline: dict | None = None,
           coverage: dict | None = None, static: dict | None = None, target: "Target | None" = None) -> int:
    origin = getattr(args, "origin", None)
    if origin:
        # SYS-67: the same question at the end - a run of hours must not end on another tree than it began on
        end = provenance(REPO)
        judge_provenance(origin["tree"], end, bool(getattr(args, "strict", False)), report)
        origin = {**origin, "tree": {**origin["tree"], "end": {k: end[k] for k in ("commit", "tree", "dirty",
                                                                                   "changes_total")}}}
    payload = {
        "schema": "nxci/1",
        "started": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(started)),
        "duration_s": round(time.time() - started, 1),
        "verdict": "fail" if report.failed else "pass",
        # TOOL-67: which repository this run judged, and with which tree
        "workspace": str(target.root) if target is not None and target.is_workspace else None,
        "tree": str(REPO),
        "pin": ((target.ws.pinned_version if target is not None and target.is_workspace else None)),
        # SYS-67 (cm-plan G1): commit, tree hash, describe, OpenBSW gitlink, VERSION, dirty - at start, plus the
        # state at the end; `workspace` likewise for a customer repository
        "provenance": (origin or {}).get("tree"),
        "workspace_provenance": (origin or {}).get("workspace"),
        "toolchain": args.toolchain,
        "strict": args.strict,
        "slow": bool(getattr(args, "slow", False)),
        "release": bool(getattr(args, "release", False)),
        "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "python": sys.executable,
        "build_root": str(build_root),
        "stages": stages,
        "findings": [f.__dict__ for f in report.findings],
        "tests": test_result["summary"] if test_result else None,
        "per_group": test_result["per_group"] if test_result else None,
        "artifacts": artifacts,
        "skipped_presets": skipped_presets or [],
        "outside": (pipeline or {}).get("outside", []),
        "coverage": coverage,
        # without the full finding list (result.json of tools/misra lies next to it under misra/)
        "static_analysis": ({k: v for k, v in static.items() if k != "findings"} if static else None),
    }
    (out / "ci-result.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    markdown = render_summary(payload, test_result)
    (out / "summary.md").write_text(markdown, encoding="utf-8")
    print("\n" + markdown)
    return 1 if report.failed else 0


def render_summary(payload: dict, test_result: dict | None = None) -> str:
    verdict = "gruen" if payload["verdict"] == "pass" else "ROT"
    title = f"Workspace {payload['workspace']}" if payload.get("workspace") else "neXtBSW CI"
    lines = [f"## {title}: {verdict}", ""]
    if payload.get("workspace"):
        lines += [f"neXtBSW {payload.get('pin') or '?'} aus `{payload.get('tree')}` (Pin `nextbsw.yaml`)", ""]
    origin = payload.get("provenance")
    if origin and origin.get("commit"):
        state = "sauber" if origin.get("dirty") is False else f"GEAENDERT ({origin.get('changes_total', 0)} Pfade)"
        lines += [f"Stand `{origin.get('describe')}`: Commit `{origin['commit']}`, Baum `{origin.get('tree')}`, "
                  f"OpenBSW `{origin.get('openbsw') or '-'}`, VERSION {origin.get('version') or '?'}, {state}", ""]
    elif origin is not None:
        lines += ["Stand: kein git-Checkout, der Bericht ist an keinen Commit gebunden", ""]
    lines += [f"{payload['platform']}, Werkzeuge `{payload['toolchain']}`, "
              f"{'streng' if payload['strict'] else 'nicht streng'}"
              f"{', mit Mutationslaeufen (--slow)' if payload.get('slow') else ''}, "
              f"Dauer {payload['duration_s']:.0f} s", "",
              "| Stufe | Ergebnis | Dauer |", "|---|---|---|"]
    lines += [f"| {s['name']} | {s['status']} | {s['duration_s']:.0f} s |" for s in payload["stages"]]
    if payload.get("tests"):
        t = payload["tests"]
        lines += ["", f"**Tests:** {t['pass']} gruen, {t['fail'] + t['timeout']} rot, {t['skip']} uebersprungen, "
                      f"{t['cases']} Testfaelle", "", "| Gruppe | gruen | rot | uebersprungen | Dauer |",
                  "|---|---|---|---|---|"]
        for group, g in sorted((payload.get("per_group") or {}).items()):
            lines.append(f"| {group} | {g['pass']} | {g['fail']} | {g['skip']} | {g['duration_s']:.0f} s |")
    coverage = payload.get("coverage")
    if coverage:
        total = coverage["total"]
        lines += ["", f"**Testabdeckung (C/C++-Host-Tests, TOOL-40):** Anweisungen "
                      f"{total['line_percent']:.1f} % ({total['lines_covered']}/{total['lines_total']}), Zweige "
                      f"{total['branch_percent']:.1f} % ({total['branches_covered']}/{total['branches_total']}), "
                      f"{coverage['entries']['pass']}/{coverage['entries']['total']} Eintraege gemessen, "
                      f"{'Schwellen erzwungen' if coverage.get('enforced') else 'Schwellen nur berichtet'}", "",
                  "| Modul | Anweisungen | Zweige | Schwelle A/Z | Urteil |", "|---|---|---|---|---|"]
        for module in coverage["modules"]:
            threshold = (f"{module['threshold_lines']:.0f}/{module['threshold_branches']:.0f}"
                         if module["own_threshold"] else "(Vorgabe)")
            lines.append(f"| `{module['module']}` | {module['line_percent']:.1f} % | "
                         f"{module['branch_percent']:.1f} % | {threshold} | {module['verdict']} |")
    static = payload.get("static_analysis")
    if static and "inventory" in static:
        inv = static["inventory"]
        verdicts = inv.get("own_by_verdict", {})
        versions = ", ".join(f"{k} {v}" for k, v in sorted(static.get("tool_versions", {}).items())) or "-"
        units = (static.get("scope") or {}).get("units", {})
        lines += ["", f"**Statische Analyse (PLAT-74, {versions}):** {units.get('own', 0)} eigene Einheiten - "
                      f"neu {verdicts.get('new', 0)}, Baseline {verdicts.get('baseline', 0)}, Abweichung "
                      f"{verdicts.get('deviated', 0)}, Werkzeughinweis {verdicts.get('notice', 0)}; "
                      f"OpenBSW-Kopien getrennt: {inv.get('copy_total', 0)}; echte Fehler (defects.yaml): "
                      f"{len(static.get('defects', []))}"]
    groups = {FAIL: "Rot", WARN: "Warnungen", SKIP: "Uebersprungen"}
    for status, title in groups.items():
        items = [f for f in payload["findings"] if f["status"] == status]
        if items:
            lines += ["", f"**{title}:**", ""] + [f"- {f['area']}: {f['text']}" for f in items]
    if payload.get("outside"):
        lines += ["", "**Ausserhalb der CI (nicht gelaufen):**", ""]
        lines += [f"- {o['name']}: {o['reason']}" for o in payload["outside"]]
    if payload.get("artifacts"):
        lines += ["", "| Erzeugnis | Bytes | text | data | bss |", "|---|---|---|---|---|"]
        for a in payload["artifacts"]:
            lines.append(f"| {a['file']} | {a['bytes']} | {a.get('text', '')} | {a.get('data', '')} | "
                         f"{a.get('bss', '')} |")
    return "\n".join(lines) + "\n"


def cmd_summary(args: argparse.Namespace) -> int:
    path = args.out / "summary.md"
    if not path.is_file():
        print(f"## neXtBSW CI: kein Ergebnis\n\n{path} fehlt - der Lauf brach vor dem Bericht ab.")
        return 1
    sys.stdout.write(path.read_text(encoding="utf-8"))
    return 0


# ---------------------------------------------------------------------------- templates for a workspace (TOOL-67)

# name -> (file in tools/ci/templates, where it belongs in the workspace, what it is for)
TEMPLATES = {
    "github": ("workspace-github.yml", ".github/workflows/ci.yml",
               "GitHub Actions: Checkout mit Submodul, Python aus dem Pin, setup und run"),
    "gitlab": ("workspace-gitlab.yml", ".gitlab-ci.yml",
               "GitLab CI: Submodul-Strategie, Werkzeug-Cache, setup und run"),
    "tests": ("workspace-tests.yaml", "ci/tests.yaml",
              "Startpunkt der eigenen Testliste des Workspace (Format wie tools/test/tests.yaml)"),
    "pipeline": ("workspace-pipeline.toml", "ci/pipeline.toml",
                 "Startpunkt des eigenen Ablaufs (Format wie tools/ci/pipeline.toml)"),
    # TOOL-217 (gap N19): coverage and static analysis of the customer's own code
    "coverage": ("workspace-coverage.yaml", "ci/coverage.yaml",
                 "Abdeckungsschwellen fuer eigenen Code (Format wie tools/test/coverage.yaml)"),
    "misra-deviations": ("workspace-misra-deviations.yaml", "ci/misra/deviations.yaml",
                         "Abweichungsliste der Statik fuer eigenen Code (Format wie tools/misra)"),
}


def template_text(name: str) -> str:
    if name not in TEMPLATES:
        raise CiError(f"unbekannte Vorlage {name} (bekannt: {', '.join(TEMPLATES)})")
    path = TEMPLATE_DIR / TEMPLATES[name][0]
    if not path.is_file():
        raise CiError(f"Vorlage fehlt: {path}")
    from tools.nxconfig import genlicense               # PLAT-120: the copy in the workspace carries the licence line
    return genlicense.stamp(path.read_text(encoding="utf-8"), TEMPLATES[name][1])


def cmd_template(args: argparse.Namespace) -> int:
    """Print a CI file for a workspace, or write it where it belongs (never over an existing file)."""
    if args.list or not args.name:
        print("Vorlagen fuer einen Workspace (python -m tools.ci template <name> --out <workspace>):")
        for name, (_, place, purpose) in TEMPLATES.items():
            print(f"  {name:<9} -> {place:<26} {purpose}")
        return 0
    text = template_text(args.name)
    if args.out is None:
        sys.stdout.write(text)
        return 0
    destination = args.out.resolve() / TEMPLATES[args.name][1]
    if destination.exists() and not args.force:
        raise CiError(f"{destination} gibt es schon (--force ueberschreibt)")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")
    print(f"{destination} geschrieben")
    return 0


def cmd_release(args: argparse.Namespace) -> int:
    """Release publication of a tagged baseline (TOOL-69, customer-project.md gap N15): plan the annotated tag,
    archive the CI report, extract release notes from CHANGELOG.md, export per distcheck profile - all local,
    nothing pushed. See tools/ci/release.py for what each piece does and why."""
    from tools.ci import release

    nxversion = _version_module()
    version = args.version or nxversion.read_version(REPO)
    profiles = [p.strip() for p in args.profile.split(",") if p.strip()]
    # TOOL-232: the tag is `--tag` (a release candidate vX.Y.Z-rcN of the version), else the release tag of the
    # version that HEAD already carries (the candidate tag set in step 6 of cm-plan.md 5.2), else v<version>
    try:
        explicit = getattr(args, "tag", None)
        tag = release.release_tag(version, explicit or release_tag_of_tree(REPO, version))
    except release.ReleaseError as exc:
        raise CiError(str(exc)) from exc
    if tag != nxversion.tag_of(version):
        print(f"Release-Tag: {tag} ({'--tag' if explicit else 'auf HEAD'})")
    out = (args.out or (REPO / "build" / "release" / tag)).resolve()
    try:
        plan = release.build_release_plan(REPO, version, out_dir=out, profiles=profiles,
                                          report_dir=args.report_dir, archive_root=args.archive_root,
                                          archive=args.archive, allow_findings=args.allow_findings,
                                          tag_message=args.tag_message, tag=tag)
    except release.ReleaseError as exc:
        raise CiError(str(exc)) from exc
    json_path, md_path = release.write_plan_files(plan, out)
    print(md_path.read_text(encoding="utf-8"))
    print(f"Plan geschrieben: {json_path}")
    blocked = [r for r in plan.profiles if not r["exported"]]
    if blocked and args.strict:
        raise CiError("Export blockiert (distcheck-Befunde): "
                      + ", ".join(f"{r['profile']} ({r['errors']} Fehler)" for r in blocked))
    # SYS-67: with --strict the archived report has to be evidence of exactly this tree (verification-plan.md 7)
    binding = plan.ci_report_binding
    if args.strict and (binding is None or not binding["bound"]):
        reasons = binding["problems"] if binding else [plan.ci_report_note or "kein CI-Bericht"]
        raise CiError("CI-Bericht nicht an diesen Baum gebunden: " + "; ".join(reasons))
    return 0


def cmd_preflight(args: argparse.Namespace) -> int:
    """Pre-flight before the release CI (TOOL-209, verification-plan.md 8.2, T-VP3): PF-1 .. PF-5 in order and a
    short report. The logic lives in tools/ci/preflight.py; this only prepares tools and environment."""
    from tools.ci import preflight

    versions = load_toml(VERSIONS_FILE)
    cache = args.cache.resolve()
    state = provenance(REPO)
    since = args.since or preflight.previous_release(REPO)
    work = (args.work_dir or preflight.default_work_dir(REPO, state.get("commit"))).resolve()
    env = tool_environment(args.toolchain, cache, versions, log=print, install_missing=not args.no_install)
    ctx = preflight.Context(
        repo=REPO, work=work, since=since, jobs=args.jobs, slow=args.slow, toolchain=args.toolchain, cache=cache,
        release=args.release, commit=args.commit, allow_dirty=args.allow_dirty, clean=args.clean,
        timeout_factor=args.timeout_factor, env=env, tests_file=args.tests, only=args.only or [],
        skip=args.skip or [], fail_fast=args.fail_fast,
        misra_env={"NX_MISRA_VENV": env.get("NX_MISRA_VENV") or str(misra_venv(cache))})
    print(f"Vorlauf: seit {since or '?'}, Arbeitsordner {work}")
    run = preflight.Preflight(ctx, provenance=provenance)
    run.execute()
    json_path, md_path = run.write_report()
    print("\n" + md_path.read_text(encoding="utf-8"))
    print(f"Bericht: {md_path} (JSON {json_path})")
    return 0 if run.green else 1


def cmd_lint(args: argparse.Namespace) -> int:
    versions = load_toml(VERSIONS_FILE)
    tool = pinned_tools(versions)["actionlint"]
    found = install(args.cache.resolve(), tool)
    workflows = sorted((REPO / ".github" / "workflows").glob("*.y*ml"))
    if not workflows:
        print("keine Workflows unter .github/workflows")
        return 1
    argv = [str(found / ("actionlint" + EXE)), "-no-color", *[str(w) for w in workflows]]
    print("$ " + subprocess.list2cmdline(argv), flush=True)
    code = subprocess.run(argv, cwd=REPO).returncode
    print("actionlint: keine Befunde" if code == 0 else f"actionlint: Befunde (Exit-Code {code})")
    return code


# ---------------------------------------------------------------------------- command line


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.ci", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cache", type=Path, default=default_cache(),
                        help="tool cache (default NX_CI_CACHE, else a per-user cache directory)")
    sub = parser.add_subparsers(dest="command", required=True)

    setup = sub.add_parser("setup", help="download the pinned tools (and Python packages)")
    setup.add_argument("--tool", action="append", help="only this tool (repeatable; also misra, strictdoc)")
    setup.add_argument("--python-packages", action="store_true",
                       help="pip install tools/requirements.txt and tools/requirements-studio.txt with "
                            "tools/ci/constraints.txt into this Python")
    setup.add_argument("--core-only", action="store_true",
                       help="with --python-packages: core packages only, no PySide6 (then the Studio tests are "
                            "skipped)")
    setup.set_defaults(func=cmd_setup)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--toolchain", choices=["pinned", "local"], default="pinned",
                       help="pinned: tools from versions.toml (default); local: PATH/NX_ARM_GCC_BIN of this machine")
        p.add_argument("--strict", action="store_true",
                       help="version deviations and unexpected skips are errors (CI)")
        p.add_argument("--tests", type=Path, default=REPO / "tools" / "test" / "tests.yaml",
                       help="test list passed to tools/test")
        p.add_argument("--release", action="store_true",
                       help="release strictness of the pin (TOOL-65, gap N10): the tree that is built must "
                            "stand on the annotated tag v<version> (or a release candidate v<version>-rcN, "
                            "TOOL-232) reachable from master and be clean; a development pin (no tag) is an "
                            "error instead of a warning")
        p.add_argument("--workspace", type=Path, nargs="?", const=Path("."), default=None,
                       help="run the pipeline of a customer workspace (TOOL-67): its ci/pipeline.toml, its "
                            "ci/tests.yaml, its presets, plus the pin check; without a folder the workspace "
                            "above the current one")

    check = sub.add_parser("check", help="compare tools and packages with the pins, check profile coverage")
    common(check)
    check.set_defaults(func=cmd_check)

    run = sub.add_parser("run", help="the whole pipeline")
    common(run)
    run.add_argument("--out", type=Path, default=None,
                     help="report and artifacts (default build/ci-out of the tree or of the workspace)")
    run.add_argument("--build-root", type=Path, default=None, help="build directory (default <tools/test>/ci)")
    run.add_argument("--jobs", "-j", type=int, default=max(2, min(os.cpu_count() or 2, 8)))
    run.add_argument("--group", action="append", help="tools/test group instead of pipeline.toml run.groups")
    run.add_argument("-k", help="tools/test selection by name (local trials)")
    run.add_argument("--slow", action="store_true", help="include the mutation runs")
    run.add_argument("--incremental", action="store_true", help="no --clean for the target builds")
    run.add_argument("--no-install", action="store_true", help="fail instead of downloading missing pinned tools")
    run.add_argument("--no-coverage", action="store_true",
                     help="skip the coverage run (TOOL-40) even if pipeline.toml asks for it")
    run.add_argument("--no-static-analysis", action="store_true",
                     help="skip the static analysis (PLAT-74) even if pipeline.toml asks for it")
    run.add_argument("--no-inventory", action="store_true",
                     help="tools/test without the completeness check (own tests with invented lists only)")
    run.set_defaults(func=cmd_run)

    summary = sub.add_parser("summary", help="print the Markdown report of a run (for a CI job summary)")
    summary.add_argument("out", type=Path)
    summary.set_defaults(func=cmd_summary)

    release = sub.add_parser("release", help="release publication of a tagged baseline (TOOL-69, gap N15)")
    release.add_argument("--version", default=None, help="release version MAJOR.MINOR.PATCH (default: VERSION)")
    release.add_argument("--tag", default=None,
                         help="release tag when it is not v<version>: a release candidate vX.Y.Z-rcN of the "
                              "version (TOOL-232; default: the release tag of the version on HEAD, else "
                              "v<version>); names the tag plan, the archive and ci-out-<tag>.zip")
    release.add_argument("--out", type=Path, default=None,
                         help="where the plan and local artefacts are written (default build/release/<tag>)")
    release.add_argument("--report-dir", type=Path, default=REPO / "build" / "ci-out",
                         help="a finished CI report (python -m tools.ci run --out <dir>) to archive under the "
                              "release tag; missing is only noted, not an error")
    release.add_argument("--archive-root", type=Path, default=None,
                         help="where CI reports are archived, as a copy plus ci-out-<tag>.zip with its SHA-256 "
                              "(default NX_RELEASE_ARCHIVE, else a per-user cache directory; SYS-67, "
                              "doc/safety/verification-plan.md section 7)")
    release.add_argument("--profile", default="open",
                         help="comma-separated distcheck profiles to export (default open)")
    release.add_argument("--allow-findings", action="store_true",
                         help="export a profile despite distcheck errors (trial runs only, like distcheck itself)")
    release.add_argument("--archive", action="store_true",
                         help="also zip each exported profile with its SHA-256 (variant 3, customer-project.md 4.4)")
    release.add_argument("--tag-message", default=None, help="message of the planned annotated tag")
    release.add_argument("--strict", action="store_true",
                         help="a profile blocked by distcheck findings, or a CI report not bound to this tree "
                              "(SYS-67: verdict, --strict, clean, same tree hash), fails the command")
    release.set_defaults(func=cmd_release)

    preflight = sub.add_parser("preflight", help="pre-flight before the release CI: PF-1..PF-5 of "
                                                 "doc/safety/verification-plan.md 8.2 (TOOL-209)")
    preflight.add_argument("--toolchain", choices=["pinned", "local"], default="pinned",
                           help="pinned: tools from versions.toml (default); local: PATH/NX_ARM_GCC_BIN")
    preflight.add_argument("--tests", type=Path, default=REPO / "tools" / "test" / "tests.yaml",
                           help="test list passed to tools/test")
    preflight.add_argument("--release", action="store_true",
                           help="PF-2 checks with --release (annotated tag v<version> on master, clean tree)")
    preflight.add_argument("--since", default=None, metavar="REF",
                           help="baseline of the increment (default: the newest release tag before HEAD)")
    preflight.add_argument("--jobs", "-j", type=int, default=4)
    preflight.add_argument("--slow", action="store_true", help="include the mutation runs (safety release)")
    preflight.add_argument("--work-dir", type=Path, default=None,
                           help="build root and report below this folder, outside the tree "
                                "(default <temp>/nx-preflight/<commit>)")
    preflight.add_argument("--commit", default=None, help="expected candidate commit (prefix) - PF-1 checks HEAD")
    preflight.add_argument("--allow-dirty", action="store_true",
                           help="trial run: a dirty tree or a build/ folder in it is only noted")
    preflight.add_argument("--clean", action="store_true", help="--clean for the target builds of PF-3")
    preflight.add_argument("--timeout-factor", type=float, default=1.0,
                           help="scale every step limit (loaded machine)")
    preflight.add_argument("--only", action="append", metavar="PF-N", help="only this step (repeatable)")
    preflight.add_argument("--skip", action="append", metavar="PF-N", help="leave this step out (repeatable)")
    preflight.add_argument("--fail-fast", action="store_true", help="stop after the first red step")
    preflight.add_argument("--no-install", action="store_true", help="fail instead of downloading missing tools")
    preflight.set_defaults(func=cmd_preflight)

    lint = sub.add_parser("lint", help="actionlint over .github/workflows")
    lint.set_defaults(func=cmd_lint)

    template = sub.add_parser("template", help="CI files for a customer workspace (TOOL-67)")
    template.add_argument("name", nargs="?", choices=sorted(TEMPLATES), help="which template (none: list them)")
    template.add_argument("--out", type=Path, default=None,
                          help="workspace the file is written into; without it the template is printed")
    template.add_argument("--force", action="store_true", help="overwrite an existing file")
    template.add_argument("--list", action="store_true", help="only list the templates")
    template.set_defaults(func=cmd_template)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except CiError as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
