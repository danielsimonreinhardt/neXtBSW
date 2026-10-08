"""Command line of the scheduling and data flow analysis (no Qt, no hardware access).

    python tools/schedana analyse projects/<project> [--measure console.txt] [--budget budget.yaml]
    python tools/schedana analyse --scenario tools/schedana/test/scenarios/overloaded.yaml
    python tools/schedana measure --input console.txt [--json]
    python tools/schedana scenario projects/<project> [-o model.yaml]
    python tools/schedana show projects/<project>

Measured values always come from a text (file or standard input), never from a serial port: opening the port is
the job of the caller, e.g. "python tools/studio console --port COM9 > console.txt" or the Studio.
"""

import argparse
import json
import sys
from pathlib import Path

from . import measure as measure_mod
from . import report as report_mod
from . import scenario as scenario_mod
from .model import ModelError

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_ERROR = 2


def _read(source: str) -> str:
    if source == "-":
        return sys.stdin.read()
    try:
        return Path(source).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise ModelError(f"{source}: cannot be read ({e})") from None


def _load_model(args):
    """Model from a scenario file or from a project (tools/rtegen configuration, read only)."""
    roles = scenario_mod.load_roles(args.roles) if args.roles else scenario_mod.load_roles()
    if args.scenario:
        return scenario_mod.load(args.scenario, roles=roles)
    if not args.project:
        raise ModelError("either a project or --scenario is required")
    from . import rteload  # imported here: only the project path needs nxconfig/rtegen
    return rteload.load(args.project, mcudb=args.mcudb, tasks_manifest=args.tasks, rte_file=args.rte,
                        roles=roles)


def _prepare(args):
    """Model, measured values and the remarks. A budget file wins over a measurement (deliberate assumption)."""
    model = _load_model(args)
    measurement, remarks = None, []
    if args.measure:
        measurement = measure_mod.parse(_read(args.measure), known_tasks=[t.name for t in model.tasks],
                                        known_isrs=[i.name for i in model.isrs])
        remarks += measure_mod.apply_to(model, measurement)
    if args.budget:
        from . import rteload
        remarks += rteload.apply_budget(model, rteload.load_budget(args.budget), Path(args.budget).name)
    return model, measurement, remarks


def cmd_analyse(args) -> int:
    model, measurement, remarks = _prepare(args)
    report = report_mod.analyse(model, measurement, remarks)
    if args.json is not None:
        text = report.to_json()
        if args.json == "-":
            print(text)
        else:
            Path(args.json).write_text(text + "\n", encoding="utf-8", newline="\n")
            print(f"JSON written to {args.json}")
    if args.json is None or args.text:
        sys.stdout.write(report.to_text(verbose=args.verbose))
    limit = {"none": None, "error": "error", "warning": "warning"}[args.fail_on]
    if limit == "error" and report.counts.get("error", 0):
        return EXIT_FINDINGS
    if limit == "warning" and (report.counts.get("error", 0) or report.counts.get("warning", 0)):
        return EXIT_FINDINGS
    return EXIT_OK


def cmd_measure(args) -> int:
    m = measure_mod.parse(_read(args.input))
    if args.json:
        print(json.dumps(m.to_dict(), indent=2, default=float))
        return EXIT_OK
    print(f"Quellen: {', '.join(m.sources) or '-'}")
    if m.window_us:
        print(f"Messfenster: {m.window_us / 1000.0:.1f} ms")
    for name, task in sorted(m.tasks.items()):
        stack = "-" if task.stack_size is None else f"{task.stack_used}/{task.stack_size}"
        print(f"task {name}: Zyklen {task.cycles}, Ueberlaeufe {task.overruns}, max {task.max_us}, "
              f"CPU {task.load_percent}, Stack {stack}")
    for name, r in sorted(m.runnables.items()):
        print(f"  runnable {name}: Aufrufe {r.calls}, min {r.min_us}, Mittel {r.mean_us}, max {r.max_us}, "
              f"Jitter {r.jitter_us}")
    for r in m.to_dict().get("modes", []):  # RTE-17
        print(f"  mode {r['name']} {r['label']} (task {r['task']}): Aufrufe {r.get('calls')}, "
              f"min {r.get('min_us')}, Mittel {r.get('mean_us')}, max {r.get('max_us')}")
    for name, isr in sorted(m.isrs.items()):
        print(f"isr {name}: Laeufe {isr.runs}, Rate {isr.rate_per_s}, max {isr.max_us}, CPU {isr.load_percent}")
    print(f"Zeilen: {m.lines_used} verwertet, {m.lines_ignored} unlesbar von {m.lines_total}")
    for problem in m.problems:
        print(f"! {problem}")
    return EXIT_OK


def cmd_scenario(args) -> int:
    import yaml
    model, _, _ = _prepare(args)
    text = yaml.safe_dump(scenario_mod.to_dict(model), sort_keys=False, allow_unicode=True)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8", newline="\n")
        print(f"scenario written to {args.out}")
    else:
        sys.stdout.write(text)
    return EXIT_OK


def cmd_show(args) -> int:
    model, _, _ = _prepare(args)
    print(f"{model.name} ({model.source}), OS-Tick {model.tick_us} us, "
          f"Hyperperiode {float(model.hyperperiod_ms()):.0f} ms")
    for task in model.tasks:
        print(f"  Kontext {task.os_priority:>2} {task.name:<12} {task.kind:<12} Schluessel {task.priority:>3} "
              f"Periode {task.period_ms} ms Stack {task.stack_bytes} Rollen {','.join(task.roles) or '-'}")
        for r in task.runnables:
            print(f"      {r.order}. {r.name}: {r.period_ms} ms, Versatz {r.offset_ms} ms, "
                  f"C {r.wcet_us} us [{r.where}]")
        for r in task.mode_runnables:  # RTE-17
            print(f"      Moduswechsel {r.name}: {r.label}, C {r.wcet_us} us [{r.where}]")
    for isr in model.isrs:
        print(f"  ISR-Gruppe {isr.name}: Periode {isr.period_ms} ms, C {isr.wcet_us} us")
    for signal in model.signals:
        writers = ", ".join(a.label for a in signal.writers()) or "-"
        readers = ", ".join(a.label for a in signal.readers()) or "-"
        print(f"  Signal {signal.label} ({signal.consistency}): {writers} -> {readers}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="schedana", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("project", nargs="?", type=Path,
                       help="projects/<project> or projects/<project>/project.yaml")
        p.add_argument("--scenario", type=Path, help="self contained analysis input instead of a project")
        p.add_argument("--rte", type=Path, help="ECU description instead of the one named by the project")
        p.add_argument("--tasks", type=Path, help="BSW task manifest instead of ecu/asyncCoreConfiguration")
        p.add_argument("--mcudb", type=Path, help="derivative database (tools/nxconfig)")
        p.add_argument("--roles", type=Path, help="role table instead of tools/schedana/roles.yaml")
        p.add_argument("--budget", type=Path, help="periods/runtimes of BSW tasks, ISR groups, interrupt locks")
        p.add_argument("--measure", help="console output with measured values (file or - for standard input)")

    analyse = sub.add_parser("analyse", help="full report (load, response times, priorities, data flow)")
    common(analyse)
    analyse.add_argument("--json", nargs="?", const="-", help="report as JSON (file or - for standard output)")
    analyse.add_argument("--text", action="store_true", help="text report even when --json is given")
    analyse.add_argument("--verbose", action="store_true", help="list every signal in the text report")
    analyse.add_argument("--fail-on", choices=("none", "error", "warning"), default="error",
                         help="exit code 1 from this severity on (default: error)")
    analyse.set_defaults(func=cmd_analyse)

    show = sub.add_parser("show", help="print the loaded model")
    common(show)
    show.set_defaults(func=cmd_show)

    export = sub.add_parser("scenario", help="export the model as a scenario file")
    common(export)
    export.add_argument("-o", "--out", type=Path, help="output file (default: standard output)")
    export.set_defaults(func=cmd_scenario)

    parse = sub.add_parser("measure", help="parse console output only")
    parse.add_argument("--input", required=True, help="file with the console output, - for standard input")
    parse.add_argument("--json", action="store_true", help="output as JSON")
    parse.set_defaults(func=cmd_measure)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ModelError as e:
        print(f"schedana: error: {e}", file=sys.stderr)
        return EXIT_ERROR
    except OSError as e:
        print(f"schedana: error: {e}", file=sys.stderr)
        return EXIT_ERROR
