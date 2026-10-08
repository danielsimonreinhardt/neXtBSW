# SPDX-License-Identifier: Apache-2.0
"""Static analysis of neXtBSW (PLAT-74).

    python -m tools.misra setup [--venv DIR]              # pinned cppcheck + clang-tidy (pip) into a venv of their own
    python -m tools.misra run [--compile-db F] [--out D]  # analyse, compare with baseline and deviations
    python -m tools.misra baseline [...]                  # analyse and rewrite baseline.yaml (inventory)
    python -m tools.misra baseline --only GLOB            # ... only the entries of matching files (SYS-62)
    python -m tools.misra check                           # rules.yaml, deviations.yaml, baseline.yaml only
    python -m tools.misra run --workspace <ws>            # own code of a customer workspace (TOOL-217, N19)
    python -m tools.misra baseline --workspace <ws>       # ... writes <ws>/ci/misra/baseline.yaml

With --workspace the workspace's own files count (tools/test/workspace.py): ci/misra/deviations.yaml,
ci/misra/baseline.yaml, optional ci/misra/defects.yaml and ci/misra/rules.yaml (else the tree's rule set). Only files
of the workspace are own code; the pinned tree, the build root and .venv are context, never judged. The compile
database defaults to the build root of the workspace plus `[static_analysis] compile_db` of ci/pipeline.toml.

Findings of a rule disapplied in rules.yaml (re-categorization plan, advisory only) are counted apart: neither new
nor baseline.

Exit codes of run: 0 no new finding, 1 new findings, 2 configuration/usage error, 3 all tools missing (skipped).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.misra import analysis as A
else:
    from . import analysis as A


def _workspace_module():
    """tools/test/workspace.py loaded from its file, as tools/ci does (what a workspace is lives there once)."""
    import importlib.util

    name = "nx_test_workspace"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, A.REPO / "tools" / "test" / "workspace.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module            # @dataclass looks its own module up in sys.modules
    spec.loader.exec_module(module)
    return module


def workspace_files(args: argparse.Namespace) -> dict:
    """TOOL-217 (gap N19): the configuration of a workspace run - {} without --workspace.

    Explicit --rules/--deviations/--baseline/--defects/--compile-db still win over the workspace's defaults.
    """
    if getattr(args, "workspace", None) is None:
        return {}
    support = _workspace_module()
    folder = args.workspace.resolve()
    if not (folder / support.PIN_FILE).is_file():
        found = support.find_workspace(folder)
        if found is None:
            raise A.MisraError(f"kein Workspace: ueber {folder} liegt kein {support.PIN_FILE} (ARCH-56)")
        folder = found
    try:
        ws = support.open_workspace(folder, A.REPO)
    except support.WorkspaceError as exc:
        raise A.MisraError(str(exc)) from None
    own_rules = ws.misra_dir / "rules.yaml"
    deviations = ws.misra_dir / "deviations.yaml"
    if args.deviations is None and not deviations.is_file():
        raise A.MisraError(f"{deviations} fehlt - Startpunkt: python -m tools.ci template misra-deviations "
                           f"--out {ws.root}")
    files = {
        "repo": ws.root,
        "copies_file": None,                     # OpenBSW copies are a matter of the tree, not of the customer
        "rules_file": own_rules if own_rules.is_file() else A.RULES_FILE,
        "deviations_file": deviations,
        "baseline_file": ws.misra_dir / "baseline.yaml",
        "defects_file": ws.misra_dir / "defects.yaml",
        "outside_dirs": tuple(ws.foreign_dirs()),
        "source_roots": ws.source_roots(),
    }
    if hasattr(args, "compile_db") and args.compile_db is None:
        compile_db = None
        if ws.pipeline_file.is_file():
            import tomllib

            try:
                pipeline = tomllib.loads(ws.pipeline_file.read_text(encoding="utf-8"))
            except tomllib.TOMLDecodeError as exc:
                raise A.MisraError(f"{ws.pipeline_file}: kein gueltiges TOML: {exc}") from None
            compile_db = (pipeline.get("static_analysis") or {}).get("compile_db")
        if not compile_db:
            raise A.MisraError(f"{ws.pipeline_file}: static_analysis.compile_db fehlt - oder --compile-db angeben")
        files["compile_db"] = ws.build_root / compile_db
    return files


def _opts(args: argparse.Namespace) -> A.RunOptions:
    ws = workspace_files(args)
    return A.RunOptions(
        compile_db=(args.compile_db or ws.get("compile_db") or A.default_compile_db()).resolve(),
        out=args.out.resolve(),
        tools=tuple(args.tool or A.TOOLS),
        tool_dir=args.tool_dir.resolve() if args.tool_dir else None,
        jobs=args.jobs,
        raw_dir=args.from_raw.resolve() if args.from_raw else None,
        repo=ws.get("repo", A.REPO),
        rules_file=args.rules or ws.get("rules_file", A.RULES_FILE),
        deviations_file=args.deviations or ws.get("deviations_file", A.DEVIATIONS_FILE),
        baseline_file=args.baseline or ws.get("baseline_file", A.BASELINE_FILE),
        defects_file=args.defects or ws.get("defects_file", A.DEFECTS_FILE),
        copies_file=ws.get("copies_file", A.COPIES_FILE),
        outside_dirs=ws.get("outside_dirs", ()),
        source_roots=ws.get("source_roots"),
    )


def _print_result(result: A.Result) -> None:
    inv = A.inventory(result)
    v = inv["own_by_verdict"]
    print(f"Werkzeuge: {result.tool_versions or '-'}")
    for tool, why in result.skipped_tools.items():
        print(f"UEBERSPRUNGEN {tool}: {why}")
    units = result.scope.get("units", {})
    print(f"Umfang: {units.get('own', 0)} eigene Einheiten, {units.get('copy', 0)} OpenBSW-Kopien")
    print(f"Eigener Code: neu {v.get('new', 0)}, Baseline {v.get('baseline', 0)}, "
          f"Abweichung {v.get('deviated', 0)}, Werkzeughinweis {v.get('notice', 0)}; "
          f"disapplied {inv['disapplied_total']} (getrennt); OpenBSW-Kopien {inv['copy_total']} (getrennt)")
    gated = result.scope.get("gated_copies") or []
    if gated:
        print(f"Geaenderte Kopien im Sicherheitspfad als eigener Code (SYS-62): {len(gated)} Dateien, "
              f"{result.scope.get('gated_units', 0)} Einheiten, {inv['gated_total']} Befunde")
    for m in result.messages:
        print(f"  {m}")
    if result.resolved:
        print(f"Behoben seit der Baseline: {sum(r['count'] for r in result.resolved)} "
              f"(python -m tools.misra baseline)")
    for f in result.new[:100]:
        print(f"NEU {f.file}:{f.line}:{f.column} [{f.rule}] {f.message}\n      {f.text}")
    if len(result.new) > 100:
        print(f"... und {len(result.new) - 100} weitere (report.md/result.json)")


def cmd_run(args: argparse.Namespace) -> int:
    opts = _opts(args)
    result, cfg = A.analyse(opts)
    A.write_reports(result, cfg["rules"], opts.out)
    _print_result(result)
    print(f"Bericht: {opts.out / 'report.md'}")
    code = A.exit_code(result, opts.tools)
    if code == A.EXIT_NEW:
        print(f"FEHLER: {len(result.new)} neue Befunde - weder in der Baseline noch in deviations.yaml")
    elif code == A.EXIT_SKIPPED:
        print("UEBERSPRUNGEN: kein Analysewerkzeug gefunden - nichts geprueft")
    return code


def cmd_baseline(args: argparse.Namespace) -> int:
    opts = _opts(args)
    result, cfg = A.analyse(opts)
    A.write_reports(result, cfg["rules"], opts.out)
    if len(result.skipped_tools) == len(A.TOOLS):
        print("FEHLER: kein Werkzeug gelaufen - Baseline bleibt unveraendert")
        return A.EXIT_SKIPPED
    data = A.build_baseline(result, cfg["baseline"], result.tool_versions, args.only)
    opts.baseline_file.parent.mkdir(parents=True, exist_ok=True)
    opts.baseline_file.write_text(A.dump_baseline(data), encoding="utf-8")
    _print_result(result)
    n = sum(i["count"] for items in data["entries"].values() for i in items)
    print(f"Baseline geschrieben: {opts.baseline_file} ({n} Befunde in {len(data['entries'])} Dateien)")
    return A.EXIT_OK


def cmd_check(args: argparse.Namespace) -> int:
    ws = workspace_files(args)
    rules_file = args.rules or ws.get("rules_file", A.RULES_FILE)
    deviations_file = args.deviations or ws.get("deviations_file", A.DEVIATIONS_FILE)
    baseline_file = args.baseline or ws.get("baseline_file", A.BASELINE_FILE)
    defects_file = args.defects or ws.get("defects_file", A.DEFECTS_FILE)
    rules = A.read_yaml(rules_file)
    messages = (A.validate_rules(rules, rules_file)
                + A.validate_deviations(A.read_yaml(deviations_file), deviations_file)
                + (A.validate_baseline(A.read_yaml(baseline_file), baseline_file, A.disapplied_rules(rules))
                   if baseline_file.is_file() else [])
                + (A.validate_defects(A.read_yaml(defects_file), defects_file) if defects_file.is_file() else []))
    for m in messages:
        print(m)
    errors = [m for m in messages if m.severity == "error"]
    print("OK" if not errors else f"{len(errors)} Fehler")
    return A.EXIT_CONFIG if errors else A.EXIT_OK


def cmd_setup(args: argparse.Namespace) -> int:
    venv = (args.venv or A.default_venv()).resolve()
    python = A.venv_bin(venv) / ("python.exe" if os.name == "nt" else "python")
    if not python.is_file():
        print(f"$ {sys.executable} -m venv {venv}")
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    argv = [str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(A.REQUIREMENTS_FILE)]
    print("$ " + subprocess.list2cmdline(argv))
    code = subprocess.run(argv).returncode
    if code != 0:
        return A.EXIT_CONFIG
    for tool in A.TOOLS:
        exe, _ = A.find_tool(tool, A.venv_bin(venv))
        print(f"{tool}: {exe} ({A.tool_version(exe) if exe else 'FEHLT'})")
    return A.EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.misra", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def files(p: argparse.ArgumentParser) -> None:
        # defaults None: the tree's files, or with --workspace the workspace's (TOOL-217)
        p.add_argument("--rules", type=Path, default=None, help="default tools/misra/rules.yaml")
        p.add_argument("--deviations", type=Path, default=None, help="default tools/misra/deviations.yaml")
        p.add_argument("--baseline", type=Path, default=None, help="default tools/misra/baseline.yaml")
        p.add_argument("--defects", type=Path, default=None, help="default tools/misra/defects.yaml")
        p.add_argument("--workspace", type=Path, nargs="?", const=Path("."), default=None,
                       help="own code of a customer workspace (TOOL-217, gap N19): its ci/misra/ files, only its "
                            "files are own code; without a folder the workspace above the current one")

    def analysis(p: argparse.ArgumentParser) -> None:
        files(p)
        p.add_argument("--compile-db", type=Path, default=None,
                       help="compile_commands.json of the target build (default: demo build, tools/test/build_dirs)")
        p.add_argument("--out", type=Path, default=A.temp_out(), help="reports and raw tool output")
        p.add_argument("--tool", action="append", choices=A.TOOLS, help="only this tool (repeatable)")
        p.add_argument("--tool-dir", type=Path, default=None,
                       help=f"directory with the executables (default {A.ENV_TOOLS}, the setup venv, PATH)")
        p.add_argument("--jobs", "-j", type=int, default=max(2, min(os.cpu_count() or 2, 8)))
        p.add_argument("--from-raw", type=Path, default=None,
                       help="evaluate cppcheck.xml/clang-tidy.txt of an earlier run instead of running the tools")

    run = sub.add_parser("run", help="analyse and compare with baseline and deviations")
    analysis(run)
    run.set_defaults(func=cmd_run)
    base = sub.add_parser("baseline", help="analyse and rewrite baseline.yaml")
    analysis(base)
    base.add_argument("--only", action="append", metavar="GLOB",
                      help="rewrite only the entries of matching files, keep all others (repeatable, SYS-62)")
    base.set_defaults(func=cmd_baseline)
    check = sub.add_parser("check", help="validate the configuration files")
    files(check)
    check.set_defaults(func=cmd_check)
    setup = sub.add_parser("setup", help="pip install the pinned tools into a venv of their own")
    setup.add_argument("--venv", type=Path, default=None, help=f"default {A.ENV_VENV} or <CI cache>/misra-venv")
    setup.set_defaults(func=cmd_setup)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except A.MisraError as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return A.EXIT_CONFIG


if __name__ == "__main__":
    sys.exit(main())
