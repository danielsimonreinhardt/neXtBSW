#!/usr/bin/env python3
"""Statement and branch coverage of the C/C++ host tests (TOOL-40, safety stage 2 of SYS-19).

The host tests are built with `ziglang` (kind `zig` in tests.yaml). The ziglang package ships clang but neither
the compiler-rt profile runtime nor `llvm-cov`/`llvm-profdata`, so LLVM's own coverage does not link and cannot be
read (measured 2026-09-21: `lld-link: undefined symbol: __llvm_profile_runtime` and `llvm_gcda_start_file`).
What does work is the gcov path:

    zig cc --coverage          -> clang writes gcov notes in GCC format (version B11*)
    tools/test/gcov_runtime.c  -> the missing llvm_gcda_* runtime, writes the .gcda counter files
    <prefix>gcov --json-format -> statement and branch counts per line, read here

The gcov reader is a tool the CI already pins: `arm-none-eabi-gcov` out of the ARM GCC of `tools/ci/versions.toml`
(gcov reads the notes of the host build; the target of the compiler does not matter). `--gcov` or `NX_GCOV`
name another one.

This tool builds every `zig` entry of the test list a second time - instrumented, without `-Werror` and with
`-O0` - runs it and aggregates the result per module. It is a report, not a gate: thresholds live in
`tools/test/coverage.yaml` and only turn the run red with `--enforce`.

    .venv/Scripts/python tools/test/coverage.py --jobs 4
    .venv/Scripts/python tools/test/coverage.py -k e2e --markdown build/coverage.md
    .venv/Scripts/python tools/test/coverage.py --json build/coverage.json --enforce
    .venv/Scripts/python <tree>/tools/test/coverage.py --workspace <ws>     # own code of a workspace (TOOL-217)

With `--workspace` (TOOL-217, customer-project.md gap N19) the measurement is about the customer's own code: only
the `zig` entries of `<ws>/ci/tests.yaml` are built, only files of the workspace are instrumented and counted (never
the pinned tree, not even as a submodule inside the workspace), modules are the manifest folders of the workspace and
the thresholds come from `<ws>/ci/coverage.yaml` (same format as tools/test/coverage.yaml).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import fnmatch
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

try:
    from . import run_tests as rt
    from . import workspace as ws_support
except ImportError:  # started as a file or with tools/test on sys.path
    import run_tests as rt  # type: ignore[no-redef]
    import workspace as ws_support  # type: ignore[no-redef]

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
RUNTIME = HERE / "gcov_runtime.c"
THRESHOLD_FILE = HERE / "coverage.yaml"

# Candidates for the gcov reader, in this order; the ARM GCC of the CI pinning is the one that always exists.
GCOV_CANDIDATES = ("arm-none-eabi-gcov", "gcov", "aarch64-none-elf-gcov", "aarch64-linux-gnu-gcov")

OK, UNDER, NO_RULE = "ok", "unter Schwelle", "ohne Schwelle"


# ---------------------------------------------------------------------------- configuration


@dataclass
class Thresholds:
    lines: float = 0.0
    branches: float = 0.0
    modules: dict[str, dict] = field(default_factory=dict)
    exclude: list[str] = field(default_factory=list)
    enforce: bool = False


def load_thresholds(path: Path = THRESHOLD_FILE) -> Thresholds:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    # every message names the path inside the YAML file (like tools/nxconfig), so the Studio can point at it
    if data.get("version") != 1:
        raise rt.ListError(f"{path}: version: {data.get('version')!r} is not supported (expected 1)")
    defaults = data.get("defaults") or {}
    modules = data.get("modules") or {}
    for name, rule in modules.items():
        if not isinstance(rule, dict) or "reason" not in rule:
            raise rt.ListError(f"{path}: modules.{name}: reason is missing - every threshold names why it is "
                               "that high")
        unknown = set(rule) - {"lines", "branches", "reason"}
        if unknown:
            raise rt.ListError(f"{path}: modules.{name}: unknown field(s) {', '.join(sorted(unknown))} "
                               "(allowed: lines, branches, reason)")
        for field_name in ("lines", "branches"):
            value = rule.get(field_name, 0)
            if not isinstance(value, (int, float)) or not 0 <= float(value) <= 100:
                raise rt.ListError(f"{path}: modules.{name}.{field_name}: {value!r} is not a percentage "
                                   "between 0 and 100")
    return Thresholds(lines=float(defaults.get("lines", 0)), branches=float(defaults.get("branches", 0)),
                      modules=modules, exclude=list(data.get("exclude") or []),
                      enforce=bool(data.get("enforce", False)))


# ---------------------------------------------------------------------------- modules


def module_directories(repo: Path = REPO) -> list[str]:
    """Repository paths of every directory with a module manifest, longest first (DOC-18 catalogue)."""
    found = {p for paths in rt.manifest_modules(repo).values() for p in paths}
    return sorted(found, key=len, reverse=True)


def workspace_module_directories(ws) -> list[str]:
    """Manifest folders of a workspace relative to its root, longest first (TOOL-217).

    Looked for below `search.modules` of the pin (else the whole workspace); folders of the tree, the build root and
    the environment are no modules of the customer."""
    foreign = [Path(f).resolve() for f in ws.foreign_dirs()]
    found: set[str] = set()
    for folder in ws.module_dirs():
        for paths in rt.manifest_modules_below(folder).values():
            for relative in paths:
                path = (Path(folder) / relative).resolve()
                if any(path == f or path.is_relative_to(f) for f in foreign) or not path.is_relative_to(ws.root):
                    continue
                found.add(path.relative_to(ws.root).as_posix() if path != ws.root else "")
    return sorted((f for f in found if f), key=len, reverse=True)


def module_of(relative: str, directories: list[str]) -> str:
    """The module a source file belongs to: its manifest directory, otherwise the component directory.

    Without a manifest the component is the directory in front of the first `src`, `include` or `test` part -
    `ecu/application/src/systems/RteSystem.cpp` becomes `ecu/application`. Such a module has no manifest yet; it
    still gets a line in the report so that nothing measured disappears."""
    for directory in directories:
        if relative == directory or relative.startswith(directory + "/"):
            return directory
    parts = relative.split("/")
    for index, part in enumerate(parts):
        if part in ("src", "include", "test") and index:
            return "/".join(parts[:index])
    return "/".join(parts[:-1])


# ---------------------------------------------------------------------------- measuring one entry


@dataclass
class EntryResult:
    name: str
    status: str            # "pass", "skip", "fail"
    reason: str = ""
    duration: float = 0.0
    gcda: list[Path] = field(default_factory=list)
    command: str = ""


def _strip_flags(flags: list[str]) -> list[str]:
    """Coverage build: no optimisation (line mapping) and no -Werror (warnings are the job of the normal run)."""
    return [f for f in flags if not f.startswith("-O") and f != "-Werror"]


def compile_runtime(runner: rt.Runner, out: Path) -> Path:
    """The gcov runtime once as an object file: it is C, the entries link with `zig c++ -std=c++NN`."""
    obj = out / "gcov_runtime.o"
    argv = [runner.python, "-m", "ziglang", "cc", "-O1", "-c", str(RUNTIME), "-o", str(obj)]
    proc = subprocess.run(argv, cwd=runner.repo, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    if proc.returncode != 0:
        raise rt.ListError(f"gcov_runtime.c laesst sich nicht uebersetzen: {proc.stdout}{proc.stderr}")
    return obj


def build_and_run(runner: rt.Runner, test: rt.Test, work: Path, gcov_dir: Path, runtime: Path,
                  run_id: int = 0, missing: dict[str, str] | None = None,
                  own: tuple[Path, list[Path]] | None = None) -> EntryResult:
    """Build one entry instrumented, run it and return its .gcda files.

    `run_id` goes into a define of its own: ziglang caches compilations, and on a cache hit clang does not run
    again - the object comes out of the cache, but the notes file (.gcno) next to it is never written
    ("cannot open notes file" in gcov). A key that differs per run keeps that from happening; inside one run
    equal keys are still reused, and their notes file exists.

    `own` (TOOL-217): (root, foreign folders) of a workspace run - only files below root and outside the foreign
    folders are instrumented. Without it everything of the tree except the submodule is."""
    started = time.monotonic()
    # the same preconditions as a normal run: a missing tool or a precondition from another group (the RTE of
    # the demo build, for instance) is a skip with a reason, never a red measurement
    reason = rt.skip_reason(test, missing or {}, runner)
    if reason:
        return EntryResult(test.name, "skip", reason, time.monotonic() - started)
    work.mkdir(parents=True, exist_ok=True)
    gcov_dir.mkdir(parents=True, exist_ok=True)
    spec = test.spec
    driver = spec.get("driver", "c++")
    head = [runner.python, "-m", "ziglang", driver]
    if spec.get("std"):
        head.append(f"-std={spec['std']}")
    link_head = list(head)
    head += _strip_flags(list(spec.get("flags", [])))
    head += [f"-I{runner.fill(i, work)}" for i in spec.get("include", [])]
    head += [f"-isystem{runner.fill(i, work)}" for i in spec.get("system_include", [])]
    head += [f"-D{d}" for d in spec.get("define", [])]
    head += ["-O0", "-g", f"-DNX_COVERAGE_RUN={run_id}"]
    env = runner.environment(test, work)

    objects: list[str] = []
    for index, source in enumerate(spec["sources"]):
        filled = Path(runner.fill(source, work))
        path = filled if filled.is_absolute() else (runner.cwd_of(test) / filled)
        if not path.is_file():
            return EntryResult(test.name, "skip", f"Quelle fehlt: {source}", time.monotonic() - started)
        obj = work / f"{index:02d}_{path.stem}.o"
        argv = [*head, "-c", str(path), "-o", str(obj)]
        stem = gcov_dir / f"{index:02d}_{path.stem}"
        instrument = _instrumented(path, runner.repo) if own is None else _instrumented_own(path, *own)
        if instrument:
            argv += ["--coverage", "-Xclang", f"-coverage-notes-file={stem}.gcno",
                     "-Xclang", f"-coverage-data-file={stem}.gcda"]
        code, out, timed = runner.spawn(argv, test, env, test.timeout)
        if code != 0 or timed:
            return EntryResult(test.name, "fail", f"Uebersetzen von {source} fehlgeschlagen: {out[-1500:]}",
                               time.monotonic() - started, command=subprocess.list2cmdline(argv))
        if instrument and not Path(f"{stem}.gcno").is_file():
            return EntryResult(test.name, "fail",
                               f"keine Notizdatei zu {source} (ziglang hat aus dem Cache geliefert)",
                               time.monotonic() - started, command=subprocess.list2cmdline(argv))
        objects.append(str(obj))

    exe = work / f"{test.name.replace('/', '_')}.exe"
    # no --coverage when linking: that would pull in clang's profile runtime, which ziglang does not ship -
    # gcov_runtime.c is exactly that missing piece
    link = [*link_head, *objects, str(runtime), "-o", str(exe)]
    code, out, timed = runner.spawn(link, test, env, test.timeout)
    if code != 0 or timed:
        return EntryResult(test.name, "fail", f"Binden fehlgeschlagen: {out[-1500:]}",
                           time.monotonic() - started, command=subprocess.list2cmdline(link))
    run_argv = [str(exe), *[runner.fill(a, work) for a in spec.get("args", [])]]
    code, out, timed = runner.spawn(run_argv, test, env, test.timeout)
    duration = time.monotonic() - started
    if code != 0 or timed:
        return EntryResult(test.name, "fail", f"Lauf fehlgeschlagen (Exit-Code {code}): {out[-1500:]}",
                           duration, command=subprocess.list2cmdline(run_argv))
    data = sorted(gcov_dir.glob("*.gcda"))
    if not data:
        return EntryResult(test.name, "fail", "keine .gcda geschrieben (gcov-Runtime nicht gebunden?)", duration)
    return EntryResult(test.name, "pass", "", duration, data, subprocess.list2cmdline(run_argv))


def _instrumented(path: Path, repo: Path) -> bool:
    """Everything of this repository is instrumented, the submodule is not - what is filtered out later is
    decided by the report, but OpenBSW sources are not even compiled twice."""
    try:
        relative = path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return False
    return not relative.startswith("external/")


def _instrumented_own(path: Path, root: Path, foreign: list[Path]) -> bool:
    """Workspace run (TOOL-217): only the customer's files - the tree is measured by the tree's own CI."""
    path = path.resolve()
    if not path.is_relative_to(root):
        return False
    return not any(path.is_relative_to(f) for f in foreign)


# ---------------------------------------------------------------------------- reading gcov


def find_gcov(explicit: str | None = None) -> str | None:
    for candidate in [explicit, os.environ.get("NX_GCOV"), *GCOV_CANDIDATES]:
        if not candidate:
            continue
        found = shutil.which(candidate, path=rt.gcc_path())
        if found:
            return found
    return None


def read_gcov(gcov: str, gcda: Path, repo: Path, foreign: list[Path] | None = None) -> tuple[dict, str]:
    """{repository path: {line: (executed, [branch taken, ...])}} out of one .gcda file.

    gcov names the source of a translation unit absolutely and the headers relative to the directory the
    compiler ran in - that is the repository, like every other command of tools/test. In a workspace run `repo`
    is the workspace and `foreign` its folders that are not the customer's code (TOOL-217)."""
    argv = [gcov, "--json-format", "--branch-probabilities", "--stdout", str(gcda)]
    proc = subprocess.run(argv, capture_output=True, text=True, cwd=repo, encoding="utf-8", errors="replace")
    if proc.returncode != 0 or not proc.stdout.strip():
        return {}, f"{gcda.name}: gcov endete mit {proc.returncode} ({(proc.stderr or '').strip()[:200]})"
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return {}, f"{gcda.name}: gcov lieferte kein JSON ({exc})"
    files: dict[str, dict[int, tuple[bool, list[bool]]]] = {}
    for entry in payload.get("files", []):
        name = entry.get("file", "")
        path = Path(name)
        if not path.is_absolute():
            path = repo / path
        try:
            resolved = path.resolve()
            relative = resolved.relative_to(repo.resolve()).as_posix()
        except (ValueError, OSError):
            continue
        if foreign and any(resolved.is_relative_to(f) for f in foreign):
            continue
        lines = files.setdefault(relative, {})
        for line in entry.get("lines", []):
            number = int(line.get("line_number", 0))
            if number <= 0:
                continue
            # gcov counts exception edges as branches; like gcovr they do not belong in a branch quota
            branches = [bool(b.get("count", 0)) for b in line.get("branches", []) if not b.get("throw")]
            executed, known = files[relative].get(number, (False, []))
            merged = [a or b for a, b in zip(known, branches)] + known[len(branches):] + branches[len(known):]
            lines[number] = (executed or bool(line.get("count", 0)), merged)
    return files, ""


def merge(into: dict, other: dict) -> None:
    for name, lines in other.items():
        target = into.setdefault(name, {})
        for number, (executed, branches) in lines.items():
            known_executed, known = target.get(number, (False, []))
            merged = [a or b for a, b in zip(known, branches)] + known[len(branches):] + branches[len(known):]
            target[number] = (known_executed or executed, merged)


# ---------------------------------------------------------------------------- report


def excluded(relative: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(relative, pattern) for pattern in patterns)


def summarise(files: dict, thresholds: Thresholds, repo: Path = REPO,
              directories: list[str] | None = None) -> dict:
    directories = module_directories(repo) if directories is None else directories
    modules: dict[str, dict] = {}
    for relative, lines in sorted(files.items()):
        if excluded(relative, thresholds.exclude):
            continue
        name = module_of(relative, directories)
        module = modules.setdefault(name, {"module": name, "files": [], "lines_total": 0, "lines_covered": 0,
                                           "branches_total": 0, "branches_covered": 0})
        covered = sum(1 for executed, _ in lines.values() if executed)
        branches = [b for _, bs in lines.values() for b in bs]
        module["files"].append({"file": relative, "lines_total": len(lines), "lines_covered": covered,
                                "branches_total": len(branches), "branches_covered": sum(branches)})
        module["lines_total"] += len(lines)
        module["lines_covered"] += covered
        module["branches_total"] += len(branches)
        module["branches_covered"] += sum(branches)
    for name, module in modules.items():
        rule = thresholds.modules.get(name, {})
        module["line_percent"] = _percent(module["lines_covered"], module["lines_total"])
        module["branch_percent"] = _percent(module["branches_covered"], module["branches_total"])
        module["threshold_lines"] = float(rule.get("lines", thresholds.lines))
        module["threshold_branches"] = float(rule.get("branches", thresholds.branches))
        module["reason"] = rule.get("reason", "")
        module["own_threshold"] = name in thresholds.modules
        if not module["threshold_lines"] and not module["threshold_branches"]:
            module["verdict"] = NO_RULE
        elif (module["line_percent"] + 1e-9 < module["threshold_lines"]
              or module["branch_percent"] + 1e-9 < module["threshold_branches"]):
            module["verdict"] = UNDER
        else:
            module["verdict"] = OK
    total = {
        "lines_total": sum(m["lines_total"] for m in modules.values()),
        "lines_covered": sum(m["lines_covered"] for m in modules.values()),
        "branches_total": sum(m["branches_total"] for m in modules.values()),
        "branches_covered": sum(m["branches_covered"] for m in modules.values()),
    }
    total["line_percent"] = _percent(total["lines_covered"], total["lines_total"])
    total["branch_percent"] = _percent(total["branches_covered"], total["branches_total"])
    return {"modules": [modules[name] for name in sorted(modules)], "total": total}


def _percent(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 1) if whole else 0.0


def render_markdown(payload: dict) -> str:
    total = payload["total"]
    lines = ["## Testabdeckung der C/C++-Host-Tests (TOOL-40)", "",
             f"Gemessen mit `{payload['gcov']}` ueber {payload['entries']['pass']} von "
             f"{payload['entries']['total']} `zig`-Eintraegen, Dauer {payload['duration_s']:.0f} s. "
             f"Schwellen sind ein Vorschlag und werden "
             f"{'erzwungen' if payload['enforced'] else 'noch nicht erzwungen'}.", "",
             f"**Gesamt:** Anweisungen {total['line_percent']:.1f} % "
             f"({total['lines_covered']}/{total['lines_total']}), Zweige {total['branch_percent']:.1f} % "
             f"({total['branches_covered']}/{total['branches_total']})", "",
             "| Modul | Dateien | Anweisungen | Zweige | Schwelle A/Z | Urteil |", "|---|---|---|---|---|---|"]
    for module in payload["modules"]:
        threshold = (f"{module['threshold_lines']:.0f}/{module['threshold_branches']:.0f}"
                     if module["own_threshold"] else "(Vorgabe)")
        lines.append(f"| `{module['module']}` | {len(module['files'])} | "
                     f"{module['line_percent']:.1f} % ({module['lines_covered']}/{module['lines_total']}) | "
                     f"{module['branch_percent']:.1f} % ({module['branches_covered']}/{module['branches_total']}) | "
                     f"{threshold} | {module['verdict']} |")
    problems = [e for e in payload["entries"]["items"] if e["status"] != "pass"]
    if problems:
        lines += ["", "**Eintraege ohne Messwert:**", ""]
        lines += [f"- {e['name']} ({e['status']}): {e['reason'].splitlines()[0][:160]}" for e in problems]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------- command line


def select(tests: list[rt.Test], pattern: str | None) -> list[rt.Test]:
    chosen = [t for t in tests if t.kind == "zig"]
    if pattern:
        chosen = [t for t in chosen if fnmatch.fnmatch(t.name, f"*{pattern}*")]
    return chosen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python tools/test/coverage.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-k", help="only entries whose name contains this")
    parser.add_argument("--jobs", "-j", type=int, default=max(2, min((os.cpu_count() or 2) // 2, 4)))
    parser.add_argument("--tests", type=Path, default=rt.LIST_FILE, help="test list (default tools/test/tests.yaml)")
    parser.add_argument("--thresholds", type=Path, default=THRESHOLD_FILE)
    parser.add_argument("--out", type=Path, default=None, help="working directory (default a temporary one)")
    parser.add_argument("--json", type=Path, default=None, help="write the result as JSON")
    parser.add_argument("--markdown", type=Path, default=None, help="write the report as Markdown")
    parser.add_argument("--gcov", default=None, help="gcov to read the counters with (default arm-none-eabi-gcov)")
    parser.add_argument("--enforce", action="store_true", help="a module below its threshold is an error")
    parser.add_argument("--keep", action="store_true", help="keep the working directory")
    parser.add_argument("--build-root", type=Path, default=None)
    parser.add_argument("--workspace", type=Path, nargs="?", const=Path("."), default=None,
                        help="own code of a customer workspace (TOOL-217, gap N19): zig entries of its "
                             "ci/tests.yaml, thresholds of its ci/coverage.yaml, only its files counted")
    args = parser.parse_args(argv)

    started = time.time()
    ws = None
    if args.workspace is not None:
        try:
            ws = rt.resolve_workspace(args.workspace, REPO)
        except ws_support.WorkspaceError as exc:
            print(f"FEHLER: {exc}", file=sys.stderr)
            return 2
        if not ws.list_file.is_file():
            print(f"FEHLER: {ws.list_file} fehlt - ohne eigene Testliste gibt es nichts zu messen", file=sys.stderr)
            return 2
        threshold_file = args.thresholds if args.thresholds != THRESHOLD_FILE else ws.coverage_file
        if not threshold_file.is_file():
            print(f"FEHLER: {threshold_file} fehlt - Startpunkt: python -m tools.ci template coverage "
                  f"--out {ws.root}", file=sys.stderr)
            return 2
        args.thresholds = threshold_file
    try:
        thresholds = load_thresholds(args.thresholds)
        # a workspace measures its own entries only: the inherited ones of the tree test the tree
        data = rt.load_list(ws.list_file, rt.WORKSPACE) if ws is not None else rt.load_list(args.tests)
    except rt.ListError as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 2
    chosen = select(data["tests"], args.k)
    if not chosen and args.k:
        print(f"kein zig-Eintrag passt auf -k {args.k}", file=sys.stderr)
        return 2
    gcov = find_gcov(args.gcov)
    if not gcov:
        print(f"FEHLER: kein gcov gefunden ({', '.join(GCOV_CANDIDATES)}; NX_ARM_GCC_BIN oder --gcov setzen)",
              file=sys.stderr)
        return 2
    tools = data.get("tools") or {}
    missing = rt.probe_tools(tools)
    if "ziglang" in missing:
        print(f"FEHLER: {missing['ziglang']}", file=sys.stderr)
        return 2

    out = (args.out or Path(os.environ.get("TEMP", "/tmp")) / "nx-coverage").resolve()
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    if ws is not None:
        build_root = (args.build_root or ws.build_root).resolve()
        runner = rt.Runner(REPO, build_root, sys.executable, workspace=ws.root)
        root, foreign = ws.root, [Path(f).resolve() for f in ws.foreign_dirs()]
        own = (root, foreign)
    else:
        build_root = (args.build_root or rt.default_build_root(REPO)).resolve()
        runner = rt.Runner(REPO, build_root, sys.executable)
        root, foreign, own = REPO, [], None
    print(f"Abdeckung: {len(chosen)} Eintraege, gcov {gcov}, {args.jobs} parallel", flush=True)
    runtime = compile_runtime(runner, out)
    run_id = int(started)

    results: dict[str, EntryResult] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(build_and_run, runner, test, out / "work" / test.name,
                               out / "gcov" / test.name, runtime, run_id, missing, own): test
                   for test in chosen}
        for future in concurrent.futures.as_completed(futures):
            test = futures[future]
            result = future.result()
            results[test.name] = result
            mark = {"pass": "OK  ", "skip": "UEBS", "fail": "FEHL"}[result.status]
            print(f"  {mark} {test.name:<34} {result.duration:5.1f} s "
                  f"{result.reason.splitlines()[0][:80] if result.reason else ''}", flush=True)

    files: dict = {}
    notes: list[str] = []
    for test in chosen:
        result = results[test.name]
        for gcda in result.gcda:
            part, note = read_gcov(gcov, gcda, root, foreign)
            if note:
                notes.append(note)
            merge(files, part)

    payload = summarise(files, thresholds, root,
                        workspace_module_directories(ws) if ws is not None else None)
    payload.update({
        "schema": "nxcov/1",
        "workspace": str(ws.root) if ws is not None else None,       # TOOL-217
        "thresholds_file": str(args.thresholds),
        "gcov": Path(gcov).name,
        "duration_s": round(time.time() - started, 1),
        "enforced": bool(args.enforce or thresholds.enforce),
        "notes": notes,
        "entries": {
            "total": len(chosen),
            "pass": sum(1 for r in results.values() if r.status == "pass"),
            "fail": sum(1 for r in results.values() if r.status == "fail"),
            "skip": sum(1 for r in results.values() if r.status == "skip"),
            "items": [{"name": t.name, "status": results[t.name].status, "reason": results[t.name].reason,
                       "duration_s": round(results[t.name].duration, 1)} for t in chosen],
        },
    })
    markdown = render_markdown(payload)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(markdown, encoding="utf-8")
    print("\n" + markdown)
    for note in notes:
        print(f"Hinweis: {note}")
    if not args.keep:
        shutil.rmtree(out, ignore_errors=True)
    else:
        print(f"[Arbeitsverzeichnis behalten: {out}]")

    if payload["entries"]["fail"]:
        return 1
    if payload["enforced"] and any(m["verdict"] == UNDER for m in payload["modules"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
