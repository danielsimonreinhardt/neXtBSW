"""Puts the analyses together into one report, as text and as JSON.

analyse(model, measurement) is the single entry point of the package; the CLI and a frontend (tools/studio,
TOOL-10) only use this function and the returned Report.
"""

import json
from dataclasses import dataclass, field

from . import dataflow, priorities, scheduling
from .findings import ERROR, INFO, WARNING, Finding, counts, sort_findings

ASSUMPTIONS = [
    "A1 fixed priorities, preemptive, one core; every task has its own priority (OpenBSW context number)",
    "A2 ISR groups run above every task, modelled as periodic disturbance with their inter-arrival time; "
    "nesting inside a group is not modelled",
    "A3 an application task is analysed with its longest cycle (the cycle of the hyper period with the most due "
    "runnables, offsets included) - an upper bound for all its cycles",
    "A4 blocking = blocking_us of the task plus max_lock_us of the system, once per activation; "
    "priority inheritance of mutexes is not modelled",
    "A5 deadline = period unless deadline_ms says otherwise",
    "A6 runtimes are measured maxima or estimates, not a proven worst case execution time",
    "A7 all tasks start together (the RTE starts every task in run()), so the critical instant really happens; "
    "a task phase is not configurable, only the offset of a runnable inside its task",
]
# RTE-17: only in the report of a system with mode switch runnables (the other reports stay as they were)
ASSUMPTION_MODES = (
    "A8 mode switch runnables are event driven: the worst switch of a task (every runnable one change of the ECU "
    "mode runs) lies in the same cycle as its longest periodic cycle; a higher priority task disturbs with one "
    "switch per busy window; no part of the long term utilisation and of the simulation over the hyper period")


def implicit_access(model) -> dict:
    """RTE-09: per runnable with implicit access the RAM of its copies and the lock sections of its wrapper per call
    (at most one before and one after the runnable, none if every copy comes from a task copy). The time of the
    copies is part of the measured runtime of the runnable (rte runnables measures the wrapper)."""
    rows = [{"runnable": r.name, "task": r.task, "bytes": r.implicit_bytes, "locks": r.implicit_locks}
            for r in [*model.runnables(), *model.mode_runnables()] if r.implicit_bytes]
    seen, unique = set(), []
    for row in rows:  # a mode switch runnable with two events is one wrapper
        if (row["runnable"], row["task"]) not in seen:
            seen.add((row["runnable"], row["task"]))
            unique.append(row)
    return {"runnables": unique, "bytes": sum(r["bytes"] for r in unique)}


def assumptions_of(model) -> list:
    return ASSUMPTIONS + ([ASSUMPTION_MODES] if model.mode_runnables() else [])

STACK_WARN_PERCENT = 75.0
STACK_ERROR_PERCENT = 90.0


@dataclass
class Report:
    model: object
    measurement: object = None
    scheduling: dict = field(default_factory=dict)
    priorities: dict = field(default_factory=dict)
    dataflow: dict = field(default_factory=dict)
    measured: dict = field(default_factory=dict)
    findings: list = field(default_factory=list)
    remarks: list = field(default_factory=list)

    # ------------------------------------------------------------------ summary

    @property
    def counts(self) -> dict:
        return counts(self.findings)

    @property
    def ok(self) -> bool:
        return self.counts.get(ERROR, 0) == 0

    def to_dict(self) -> dict:
        model = self.model
        result = {
            "schema": "schedana/1",
            "system": {
                "name": model.name,
                "source": model.source,
                "tick_us": model.tick_us,
                "max_lock_us": model.max_lock_us,
                "roles": model.roles_source,
                "tasks": len(model.tasks),
                "application_tasks": [t.name for t in model.application_tasks()],
                "isr_groups": [i.name for i in model.isrs],
                "signals": len(model.signals),
                "hyperperiod_ms": float(model.hyperperiod_ms()),
                "timing_complete": model.timing_complete(),
                "notes": list(model.notes),
            },
            "assumptions": assumptions_of(model),
            "measurement": self.measured,
            "utilisation": self.scheduling.get("utilisation", {}),
            "response_times": self.scheduling.get("response_times", {}),
            "hyperperiod": self.scheduling.get("hyperperiod", {}),
            "offset_suggestions": self.scheduling.get("offset_suggestions", []),
            "priorities": {k: v for k, v in self.priorities.items() if k != "findings"},
            "dataflow": {k: v for k, v in self.dataflow.items() if k != "findings"},
            "findings": [f.to_dict() for f in self.findings],
            "summary": {"errors": self.counts.get(ERROR, 0), "warnings": self.counts.get(WARNING, 0),
                        "infos": self.counts.get(INFO, 0), "ok": self.ok, "remarks": self.remarks},
        }
        if "mode_switches" in self.scheduling:
            # RTE-17: event driven load per task, after the offsets (a key only systems with modes have)
            items = list(result.items())
            at = [k for k, _ in items].index("offset_suggestions") + 1
            result = dict(items[:at] + [("mode_switches", self.scheduling["mode_switches"])] + items[at:])
        implicit = implicit_access(model)
        if implicit["runnables"]:
            # RTE-09: copies of the runnables with implicit access, after the data flow (only systems that have any)
            items = list(result.items())
            at = [k for k, _ in items].index("dataflow") + 1
            result = dict(items[:at] + [("implicit_access", implicit)] + items[at:])
        return result

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=False, default=float)

    # ------------------------------------------------------------------ text

    def to_text(self, verbose: bool = False) -> str:
        model = self.model
        out: list = []
        add = out.append
        add(f"Scheduling- und Datenflussanalyse: {model.name}")
        add(f"Quelle: {model.source}")
        if self.measured:
            add(f"Messdaten: {', '.join(self.measured.get('sources') or ['-'])}"
                + (f", Fenster {self.measured['window_us'] / 1000.0:.0f} ms" if self.measured.get("window_us")
                   else ""))
        add(f"Tasks: {len(model.tasks)} ({len(model.application_tasks())} Anwendung), "
            f"ISR-Gruppen: {len(model.isrs)}, Signale: {len(model.signals)}, "
            f"Hyperperiode: {float(model.hyperperiod_ms()):.0f} ms")
        add("")

        load = self.scheduling.get("utilisation", {})
        add("Auslastung je Task")
        add(f"  {'Task':<12}{'Art':<6}{'Kontext':>8}{'Periode':>10}{'C_max':>10}{'Last':>9}")
        for entry in load.get("tasks", []):
            add(f"  {entry['task']:<12}{entry['kind'][:3]:<6}{entry['context']:>8}"
                f"{_fmt(entry['period_ms'], 'ms'):>10}{_fmt(entry['worst_cycle_us'], 'us'):>10}"
                f"{_pct(entry['percent']):>9}")
        for entry in load.get("isr_groups", []):
            add(f"  {entry['isr_group']:<12}{'isr':<6}{'-':>8}{_fmt(entry['period_ms'], 'ms'):>10}"
                f"{_fmt(entry['wcet_us'], 'us'):>10}{_pct(entry['percent']):>9}")
        add(f"  {'gesamt':<12}{'':<6}{'':>8}{'':>10}{'':>10}{_pct(load.get('total_percent')):>9}")
        add("")

        response = self.scheduling.get("response_times", {})
        add("Antwortzeitanalyse (feste Prioritaeten, ISRs als Stoerer)")
        add(f"  {'Task':<12}{'C_max':>10}{'Block':>9}{'Antwort':>12}{'Frist':>12}{'Reserve':>12}")
        for entry in response.get("tasks", []):
            add(f"  {entry['task']:<12}{_fmt(entry['wcet_us'], 'us'):>10}"
                f"{_fmt(entry['blocking_us'], 'us'):>9}{_fmt(entry['response_us'], 'us'):>12}"
                f"{_fmt(None if entry['deadline_ms'] is None else float(entry['deadline_ms']) * 1000.0, 'us'):>12}"
                f"{_fmt(entry['slack_us'], 'us'):>12}"
                + ("" if entry.get("converged") is not False else "  (divergiert)"))
        add("")

        hyper = self.scheduling.get("hyperperiod", {})
        if hyper.get("peaks"):
            add(f"Lastspitzen ueber die Hyperperiode (Fenster {hyper['bucket_us'] / 1000.0:.1f} ms)")
            for peak in hyper["peaks"][:3]:
                share = ", ".join(f"{k} {v:.0f} us" for k, v in list(peak["by_task"].items())[:4])
                add(f"  bei {peak['start_us'] / 1000.0:8.1f} ms: {peak['demand_us']:7.0f} us "
                    f"({100.0 * peak['load']:5.1f} % des Fensters) - {share}")
            simulation = hyper.get("simulation") or {}
            if simulation.get("tasks"):
                add(f"  Simulation: Leerlauf {_pct(simulation.get('idle_percent'))}, "
                    f"laengste Antwortzeiten: " +
                    ", ".join(f"{name} {stats['max_response_us']:.0f} us"
                              for name, stats in sorted(simulation["tasks"].items())))
            add("")

        if self.scheduling.get("offset_suggestions"):
            add("Vorschlaege fuer Versatz (rte.yaml tasks[].runnables[].offset_ms)")
            for entry in self.scheduling["offset_suggestions"]:
                add(f"  Task {entry['task']}: Spitze {entry['peak_before_us']:.0f} us -> "
                    f"{entry['peak_after_us']:.0f} us")
                for name, offset in entry["offsets_ms"].items():
                    mark = " *" if name in entry["changed"] else ""
                    add(f"    {name}: offset_ms {offset}{mark}")
            add("")

        if self.scheduling.get("mode_switches"):
            add("Moduswechsel je Task (ereignisgesteuert, Mode-Switch-Runnables, A8)")
            for entry in self.scheduling["mode_switches"]:
                switch = entry["worst_switch"]
                old = "erster Zyklus" if switch["initial"] else (switch["from"] or "beliebig")
                add(f"  Task {entry['task']}: schlimmster Wechsel {old} -> {switch['to'] or 'beliebig'} "
                    f"{_fmt(switch['us'], 'us')}, mit dem laengsten periodischen Zyklus "
                    f"{_fmt(entry['worst_cycle_with_switch_us'], 'us')}")
                for r in entry["runnables"]:
                    mark = " *" if f"{r['name']} ({r['label']})" in switch["runnables"] else ""
                    add(f"    {r['name']} {r['label']}: C {_fmt(r['wcet_us'], 'us')}{mark}")
            add("  (* = im schlimmsten Wechsel)")
            add("")

        if self.measured:
            add("Messwerte vom Target")
            add(f"  {'Task':<12}{'Zyklen':>8}{'Ueberl.':>8}{'max':>11}{'Mittel':>11}{'Jitter':>10}"
                f"{'CPU':>9}{'Stack':>14}")
            for name, task in sorted(self.measured.get("tasks", {}).items()):
                stack = "-"
                if task.get("stack_size"):
                    stack = f"{task.get('stack_used', 0)}/{task['stack_size']}"
                add(f"  {name:<12}{_num(task.get('cycles')):>8}{_num(task.get('overruns')):>8}"
                    f"{_fmt(task.get('max_us') or task.get('stats_max_us'), 'us'):>11}"
                    f"{_fmt(task.get('mean_us') or task.get('avg_us'), 'us'):>11}"
                    f"{_fmt(task.get('jitter_us'), 'us'):>10}{_pct(task.get('load_percent')):>9}{stack:>14}")
            if self.measured.get("runnables"):
                add(f"  {'Runnable':<28}{'Aufrufe':>9}{'min':>8}{'Mittel':>8}{'max':>8}{'Jitter':>9}")
                for name, r in sorted(self.measured["runnables"].items()):
                    add(f"  {name:<28}{_num(r.get('calls')):>9}{_fmt(r.get('min_us'), 'us'):>8}"
                        f"{_fmt(r.get('mean_us') or r.get('last_us'), 'us'):>8}"
                        f"{_fmt(r.get('max_us'), 'us'):>8}{_fmt(r.get('jitter_us'), 'us'):>9}")
            if self.measured.get("modes"):
                add(f"  {'Mode-Switch-Runnable':<28}{'Aufrufe':>9}{'min':>8}{'Mittel':>8}{'max':>8}  Ereignis (Task)")
                for r in self.measured["modes"]:
                    add(f"  {r['name']:<28}{_num(r.get('calls')):>9}{_fmt(r.get('min_us'), 'us'):>8}"
                        f"{_fmt(r.get('mean_us') or r.get('last_us'), 'us'):>8}"
                        f"{_fmt(r.get('max_us'), 'us'):>8}  {r['label']} ({r['task']})")
            if self.measured.get("isrs"):
                add(f"  {'ISR-Gruppe':<12}{'Laeufe':>9}{'Rate':>12}{'max':>8}{'CPU':>8}")
                for name, isr in sorted(self.measured["isrs"].items()):
                    rate = isr.get("rate_per_s")
                    add(f"  {name:<12}{_num(isr.get('runs')):>9}"
                        f"{('-' if rate is None else f'{rate:.0f}/s'):>12}"
                        f"{_fmt(isr.get('max_us'), 'us'):>8}{_pct(isr.get('load_percent')):>8}")
            lines = self.measured.get("lines", {})
            add(f"  Zeilen: {lines.get('used', 0)} verwertet, {lines.get('ignored', 0)} unlesbar "
                f"von {lines.get('total', 0)}")
            for problem in self.measured.get("problems", []):
                add(f"  ! {problem}")
            add("")

        add("Prioritaeten (Reihenfolge nach Kontext)")
        for entry in self.priorities.get("order", []):
            roles = ",".join(entry["roles"]) or "-"
            add(f"  Kontext {entry['context']:>2}  {entry['task']:<12}{entry['kind'][:3]:<5}"
                f"Schluessel {entry['priority']:>3}  Rolle {roles}"
                + ("  (above_application)" if entry["above_application"] else ""))
        if self.priorities.get("roles_source"):
            add(f"  Rollenquelle: {self.priorities['roles_source']}")
        add("")

        add("Datenfluss")
        signals = self.dataflow.get("signals", [])
        add(f"  {len(signals)} Signale, "
            f"{sum(1 for s in signals if s['hand_overs'])} mit Taskuebergang, "
            f"{sum(1 for s in signals if s['stale_reads'])} mit Leser vor Schreiber, "
            f"{len(self.dataflow.get('cycles', []))} Zyklen")
        if verbose:
            for signal in signals:
                add(f"  {signal['label'] or signal['signal']} ({signal['consistency']}): "
                    f"Schreiber {', '.join(a['by'] for a in signal['writers']) or '-'} -> "
                    f"Leser {', '.join(a['by'] for a in signal['readers']) or '-'}")
        add("")

        implicit = implicit_access(model)
        if implicit["runnables"]:
            add("Implizite Zugriffe (Kopie je Runnable)")
            add(f"  {len(implicit['runnables'])} Runnables, {implicit['bytes']} Byte Kopien, "
                f"Lock-Abschnitte je Aufruf hoechstens {max(r['locks'] for r in implicit['runnables'])}")
            for entry in implicit["runnables"]:
                add(f"  {entry['runnable']:<32}{entry['task']:<12}{entry['bytes']:>6} Byte{entry['locks']:>4} Lock")
            add("")

        add(f"Befunde: {self.counts.get(ERROR, 0)} Fehler, {self.counts.get(WARNING, 0)} Warnungen, "
            f"{self.counts.get(INFO, 0)} Hinweise")
        for finding in self.findings:
            marker = {ERROR: "FEHLER ", WARNING: "WARNUNG", INFO: "HINWEIS"}.get(finding.severity, "       ")
            place = f" [{finding.where}]" if finding.where else ""
            add(f"  {marker} {finding.code}: {finding.message}{place}")
        if self.remarks:
            add("")
            add("Anmerkungen zur Zuordnung der Messwerte")
            for remark in self.remarks:
                add(f"  - {remark}")
        add("")
        add("Annahmen")
        for assumption in assumptions_of(model):
            add(f"  {assumption}")
        return "\n".join(out) + "\n"


def _fmt(value, unit: str) -> str:
    """Number with unit, without losing a fraction that matters (a 0.5 ms ISR period must not read as 0 ms)."""
    if value is None:
        return "-"
    value = float(value)
    if value and abs(value) < 10.0 and abs(value - round(value)) > 1e-9:
        return f"{value:.3g} {unit}"
    return f"{value:.0f} {unit}"


def _pct(value) -> str:
    return "-" if value is None else f"{float(value):.2f} %"


def _num(value) -> str:
    return "-" if value is None else str(value)


def measurement_findings(model, measurement) -> list:
    """Checks the measured values against the configuration (overruns, stack reserve, jitter, plausibility)."""
    findings = []
    if measurement is None:
        return findings
    for task in model.tasks:
        found = measurement.tasks.get(task.name)
        if found is None:
            continue
        if found.overruns:
            findings.append(Finding(
                ERROR, "measured_overruns",
                f"task {task.name} reports {found.overruns} overruns in {found.cycles} cycles: a cycle took "
                f"longer than the task period of {found.period_ms or task.period_ms} ms",
                task.name, task.where, {"overruns": found.overruns, "cycles": found.cycles}))
        percent = found.stack_percent
        if percent is not None:
            severity = ERROR if percent >= STACK_ERROR_PERCENT else (
                WARNING if percent >= STACK_WARN_PERCENT else None)
            if severity:
                findings.append(Finding(
                    severity, "stack_reserve_low",
                    f"task {task.name} uses {found.stack_used} of {found.stack_size} byte stack "
                    f"({percent:.0f} %), only {found.stack_free} byte reserve",
                    task.name, task.where, {"used": found.stack_used, "size": found.stack_size}))
        if found.max_us is not None and (found.period_ms or task.period_ms):
            period_us = float(found.period_ms or task.period_ms) * 1000.0
            if found.max_us > 0.7 * period_us:
                findings.append(Finding(
                    WARNING, "cycle_close_to_period",
                    f"the longest measured cycle of task {task.name} is {found.max_us:.0f} us, "
                    f"{100.0 * found.max_us / period_us:.0f} % of its period",
                    task.name, task.where, {"max_us": found.max_us, "period_us": period_us}))
        if found.jitter_us is not None and (found.period_ms or task.period_ms):
            period_us = float(found.period_ms or task.period_ms) * 1000.0
            if found.jitter_us > 0.2 * period_us:
                findings.append(Finding(
                    WARNING, "activation_jitter",
                    f"the start of task {task.name} moves by {found.jitter_us:.0f} us "
                    f"({100.0 * found.jitter_us / period_us:.0f} % of its period) between two cycles: it is "
                    f"delayed by higher priority work",
                    task.name, task.where, {"jitter_us": found.jitter_us}))
    for task in model.application_tasks():
        for r in task.runnables:
            found = measurement.runnables.get(r.name)
            if found is None or found.calls is None or not found.calls:
                continue
            if found.period_ms is not None and found.period_ms != r.period_ms:
                findings.append(Finding(
                    WARNING, "measured_period_differs",
                    f"runnable {r.name} runs every {found.period_ms} ms on the target, the configuration says "
                    f"{r.period_ms} ms: the image on the board is not the analysed configuration",
                    r.name, r.where, {"measured_ms": found.period_ms, "configured_ms": r.period_ms}))
            if found.jitter_us is not None and found.max_us and found.min_us is not None \
                    and found.min_us > 0 and found.max_us > 3.0 * found.min_us:
                findings.append(Finding(
                    INFO, "runtime_jitter",
                    f"the runtime of {r.name} varies between {found.min_us:.0f} us and {found.max_us:.0f} us "
                    f"(jitter {found.jitter_us:.0f} us): data dependent branches or a preemption inside the "
                    f"measurement",
                    r.name, r.where, {"min_us": found.min_us, "max_us": found.max_us}))
    # cross check of the analysis against the measurement: the idle share says what the CPU really did
    idle = measurement.tasks.get("idle")
    analysed = model.utilisation()
    if idle is not None and idle.load_percent is not None and analysed is not None:
        measured = 100.0 - idle.load_percent
        below = 100.0 * analysed < measured - 0.01
        findings.append(Finding(
            WARNING if below else INFO, "load_measured_vs_analysed",
            f"the target used {measured:.2f} % of the CPU during the measurement window "
            f"(idle {idle.load_percent:.2f} %), the analysis expects at most {100.0 * analysed:.2f} % "
            f"(maximum runtimes, A6): "
            + ("the analysis is BELOW the measurement, so a runtime or an activation rate is missing "
               "(a task or an ISR group without values?)" if below else "the analysis is on the safe side"),
            "system", "", {"measured_percent": measured, "analysed_percent": 100.0 * analysed}))
    if measurement.load_test:
        findings.append(Finding(
            WARNING, "load_test_build",
            f"the firmware was built with the TOOL-09 overflow test: runnable "
            f"{measurement.load_test['runnable_index']} busy waits "
            f"{float(measurement.load_test['us']):.0f} us. The measured values belong to that build, not to the "
            f"normal one",
            "measurement", "", measurement.load_test))
    for problem in measurement.problems:
        findings.append(Finding(INFO, "measurement_incomplete", f"measurement: {problem}", "measurement", ""))
    if measurement.complete is False:
        findings.append(Finding(
            WARNING, "measurement_lines_lost",
            "the machine readable output (rte json) is incomplete: repeat the command, the analysis uses only "
            "the values that arrived",
            "measurement", ""))
    return findings


def analyse(model, measurement=None, remarks=None) -> Report:
    """Runs every analysis on the model and returns the report."""
    scheduling_result = scheduling.scheduling_analysis(model)
    priority_result = priorities.analyse(model, scheduling_result.get("response_times"))
    dataflow_result = dataflow.analyse(model)
    findings = (scheduling_result["findings"] + priority_result["findings"] + dataflow_result["findings"]
                + measurement_findings(model, measurement) + list(getattr(model, "findings", [])))
    return Report(
        model=model,
        measurement=measurement,
        scheduling=scheduling_result,
        priorities=priority_result,
        dataflow=dataflow_result,
        measured=measurement.to_dict() if measurement is not None else {},
        findings=sort_findings(findings),
        remarks=list(remarks or []),
    )
