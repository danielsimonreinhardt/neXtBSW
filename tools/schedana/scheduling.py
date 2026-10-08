"""Scheduling analysis: utilisation, response times for fixed priorities, load over the hyper period, offsets.

Assumptions (they are part of every report, see report.py):
  A1 Fixed priorities, preemptive, one core. OpenBSW/FreeRTOS runs every task on its own priority (the context
     number), so no two tasks share a priority and there is no round robin.
  A2 ISR groups are above every task and are modelled as periodic disturbances with their minimum inter-arrival
     time. Their nesting is not modelled: a group is a single disturbance.
  A3 An application task is analysed with the longest of its cycles (worst_cycle_us): the cycle of the hyper period
     in which the most runnables are due, with the configured offsets. This is an upper bound for every cycle
     (pessimistic for multi rate tasks; the simulation over the hyper period shows the real cycles).
  A4 Blocking: the longest critical section that delays a task (interrupt locks of the BSW, IO-14/IO-16, and the
     RTE locks of the task copies) is added once per activation (blocking_us of the task plus max_lock_us of the
     system). Priority inheritance of the FreeRTOS mutex is not modelled.
  A5 Deadline = period unless deadline_ms says otherwise. Runnables inherit the deadline of their task.
  A6 Runtimes are the measured maxima (rte runnables / stats cpu) or the estimates of the scenario. They are not
     a proven WCET; a measurement only shows what happened during the measurement window.
  A7 All tasks start at the same time (the RTE starts every task in run() with scheduleAtFixedRate), so the
     critical instant of the response time analysis really happens. A task phase is not configurable today, only
     the offset of a runnable inside its task.
  A8 Mode switch runnables (RTE-08, only if the system has them): the worst switch of a task (all runnables one
     change of the ECU mode runs) lies in the same cycle as its longest periodic cycle, and a higher priority task
     disturbs with one switch per busy window - the ECU mode changes rarely compared to the task periods. They are
     event driven: no part of the long term utilisation and of the simulation over the hyper period (RTE-17).
"""

import heapq
from math import ceil, gcd

from .findings import ERROR, INFO, WARNING, Finding

MAX_JOBS = 40000             # guard for pathological hyper periods
MAX_ITERATIONS = 1000
MAX_WINDOWS_REPORTED = 256   # more windows would blow up the JSON report, the peaks stay
HYPER_CAP_MS = 60000         # above that the ISR rates are left out of the hyper period (measured rates)
ISR_PRIORITY_BASE = 1 << 16


def _us(period_ms) -> float:
    return float(period_ms) * 1000.0


def utilisation(model) -> dict:
    """Utilisation per task, per ISR group and in total."""
    tasks, isrs = [], []
    for task in model.tasks:
        load = task.utilisation()
        entry = {
            "task": task.name,
            "kind": task.kind,
            "context": task.os_priority,
            "priority": task.priority,
            "period_ms": task.period_ms,
            "worst_cycle_us": task.worst_cycle_us(),
            "utilisation": load,
            "percent": None if load is None else round(100.0 * load, 3),
            "where": task.where,
        }
        if task.mode_runnables:
            # RTE-17: event driven, not part of the long term utilisation (A8); the worst switch is its own value
            entry["mode_switch_us"] = task.mode_switch()["us"]
        tasks.append(entry)
    for isr in model.isrs:
        load = isr.utilisation()
        isrs.append({
            "isr_group": isr.name,
            "period_ms": isr.period_ms,
            "wcet_us": isr.wcet_us,
            "utilisation": load,
            "percent": None if load is None else round(100.0 * load, 3),
            "where": isr.where,
        })
    known = [e["utilisation"] for e in tasks + isrs if e["utilisation"] is not None]
    missing_tasks = [e["task"] for e in tasks if e["utilisation"] is None]
    missing_isrs = [e["isr_group"] for e in isrs if e["utilisation"] is None]
    # TOOL-15: a task and an ISR group may have the same name (OpenBSW: "can"), the name is listed once
    missing = list(dict.fromkeys(missing_tasks + missing_isrs))
    total = sum(known) if known else None
    return {"tasks": tasks, "isr_groups": isrs, "total": total,
            "total_percent": None if total is None else round(100.0 * total, 3),
            "without_runtime": missing,
            "without_runtime_by_kind": {"tasks": missing_tasks, "isr_groups": missing_isrs}}


def _missing_text(result: dict) -> str:
    """Names without a runtime, each once; a name that is a task and an ISR group says so (TOOL-15)."""
    by_kind = result.get("without_runtime_by_kind") or {}
    tasks, isrs = set(by_kind.get("tasks", ())), set(by_kind.get("isr_groups", ()))
    parts = []
    for name in result["without_runtime"]:
        if name in tasks and name in isrs:
            parts.append(f"{name} (task and ISR group)")
        elif name in isrs and name not in tasks:
            parts.append(f"{name} (ISR group)")
        else:
            parts.append(str(name))
    return ", ".join(parts)


def utilisation_findings(model, result: dict) -> list:
    findings = []
    total = result["total"]
    for entry in result["tasks"]:
        if entry["utilisation"] is not None and entry["utilisation"] > 1.0:
            findings.append(Finding(
                ERROR, "task_overloaded",
                f"task {entry['task']} needs {entry['percent']:.1f} % of the CPU on its own "
                f"({entry['worst_cycle_us']:.0f} us every {entry['period_ms']} ms): it cannot keep its period",
                entry["task"], entry["where"], {"utilisation": entry["utilisation"]}))
    if total is not None:
        if total > 1.0:
            findings.append(Finding(
                ERROR, "system_overloaded",
                f"the system needs {100.0 * total:.1f} % of the CPU (tasks and ISR groups together): "
                f"deadlines cannot be met at any priority assignment",
                "system", "", {"utilisation": total}))
        elif total > 0.8:
            findings.append(Finding(
                WARNING, "load_high",
                f"utilisation {100.0 * total:.1f} % leaves little reserve "
                f"(rule of thumb: 80 % for fixed priorities with mixed periods)",
                "system", "", {"utilisation": total}))
    if result["without_runtime"]:
        findings.append(Finding(
            INFO, "runtime_missing",
            "no runtime for " + _missing_text(result) +
            ": read measured values from the target (rte json, stats cpu) or put estimates into the scenario",
            "system", "", {"missing": result["without_runtime"],
                           "missing_by_kind": result["without_runtime_by_kind"]}))
    return findings


def response_times(model) -> dict:
    """Response time analysis for fixed priorities (Joseph/Pandya with blocking and ISR disturbance)."""
    entries = []
    isr_load = [(i, _us(i.period_ms), float(i.wcet_us))
                for i in model.isrs if i.period_ms and i.wcet_us is not None]
    for task in model.tasks:
        cost = task.worst_cycle_us()
        if cost is not None and task.mode_runnables:
            cost += task.mode_switch_us()  # RTE-17: a mode switch in the same cycle as the longest periodic one
        period = _us(task.period_ms) if task.period_ms else None
        deadline = _us(task.deadline) if task.deadline else None
        entry = {"task": task.name, "kind": task.kind, "context": task.os_priority,
                 "period_ms": task.period_ms, "deadline_ms": task.deadline,
                 "wcet_us": cost, "blocking_us": task.blocking_us + model.max_lock_us,
                 "response_us": None, "converged": None, "slack_us": None, "interference": [],
                 "where": task.where}
        if task.mode_runnables:
            entry["mode_switch_us"] = task.mode_switch()["us"]
        if cost is None or period is None:
            entry["reason"] = "no runtime or no period"
            entries.append(entry)
            continue
        higher = [(t, _us(t.period_ms), t.worst_cycle_us())
                  for t in model.higher_priority(task) if t.period_ms and t.worst_cycle_us() is not None]
        unknown = [t.name for t in model.higher_priority(task)
                   if not t.period_ms or t.worst_cycle_us() is None]
        # RTE-17 (A8): one mode switch of every higher priority task with mode switch runnables per busy window
        switches = [(t, t.mode_switch_us()) for t, _, _ in higher if t.mode_runnables and t.mode_switch_us()]
        blocking = task.blocking_us + model.max_lock_us
        response = cost + blocking
        limit = max(deadline or period, period) * 20.0
        converged = False
        for _ in range(MAX_ITERATIONS):
            value = cost + blocking + sum(us for _, us in switches)
            for _, t_period, t_cost in higher:
                value += ceil(response / t_period) * t_cost
            for _, i_period, i_cost in isr_load:
                value += ceil(response / i_period) * i_cost
            if abs(value - response) < 1e-9:
                converged = True
                break
            response = value
            if response > limit:
                break
        entry["response_us"] = response
        entry["converged"] = converged
        entry["unknown_higher"] = unknown
        entry["interference"] = (
            [{"source": t.name, "kind": "task", "jobs": ceil(response / p), "us": ceil(response / p) * c}
             for t, p, c in higher] +
            [{"source": i.name, "kind": "isr", "jobs": ceil(response / p), "us": ceil(response / p) * c}
             for i, p, c in isr_load] +
            [{"source": t.name, "kind": "mode_switch", "jobs": 1, "us": us} for t, us in switches])
        entry["interference"].sort(key=lambda e: -e["us"])
        if deadline is not None:
            entry["slack_us"] = deadline - response
        entries.append(entry)
    return {"tasks": entries, "assumption": "critical instant, all tasks released together (A7)"}


def response_findings(model, result: dict) -> list:
    findings = []
    for entry in result["tasks"]:
        name, where = entry["task"], entry["where"]
        if entry["response_us"] is None:
            continue
        if not entry["converged"]:
            findings.append(Finding(
                ERROR, "response_diverges",
                f"the response time of task {name} does not converge: the higher priority tasks and ISR groups "
                f"use up the whole CPU, the task never finishes in time",
                name, where, {"interference": entry["interference"][:5]}))
            continue
        deadline = entry["deadline_ms"]
        if entry["slack_us"] is not None and entry["slack_us"] < 0:
            worst = entry["interference"][0]["source"] if entry["interference"] else "-"
            findings.append(Finding(
                ERROR, "deadline_missed",
                f"task {name} answers after at most {entry['response_us']:.0f} us but its deadline is "
                f"{_us(deadline):.0f} us ({entry['slack_us']:.0f} us missing); largest share: {worst}",
                name, where, {"response_us": entry["response_us"], "slack_us": entry["slack_us"],
                              "interference": entry["interference"][:5]}))
        elif entry["slack_us"] is not None and deadline and entry["slack_us"] < 0.2 * _us(deadline):
            findings.append(Finding(
                WARNING, "deadline_tight",
                f"task {name} keeps its deadline with only {entry['slack_us']:.0f} us reserve "
                f"({100.0 * entry['slack_us'] / _us(deadline):.0f} % of the deadline)",
                name, where, {"response_us": entry["response_us"], "slack_us": entry["slack_us"]}))
        if entry.get("unknown_higher"):
            findings.append(Finding(
                INFO, "interference_unknown",
                f"the response time of task {name} ignores {', '.join(entry['unknown_higher'])}: "
                f"no runtime or no period known for these higher priority tasks",
                name, where, {"unknown": entry["unknown_higher"]}))
    return findings


# --------------------------------------------------------------------------------------------- hyper period

def _bucket_us(model, hyper_us: float) -> float:
    periods = [int(_us(t.period_ms)) for t in model.tasks if t.period_ms]
    periods += [int(_us(i.period_ms)) for i in model.isrs if i.period_ms]
    step = float(gcd(*periods)) if len(periods) > 1 else (float(periods[0]) if periods else 1000.0)
    while hyper_us / step > 2000:
        step *= 2
    return step


def _job_sources(model, hyper_us: float) -> list:
    """One generator per task and ISR group, each yielding its activations in release order."""
    def task_jobs(task):
        period = _us(task.period_ms)
        ticks = task.hyper_ticks()
        with_runnables = task.is_application and task.runnables
        for k in range(int(hyper_us // period)):
            cost = task.cycle_us(k % ticks) if with_runnables else task.wcet_us
            if cost is None:
                continue
            due = [r.name for r in task.due(k % ticks)] if with_runnables else []
            yield {"release_us": k * period, "cost_us": float(cost), "priority": task.os_priority,
                   "name": task.name, "kind": task.kind, "runnables": due,
                   "deadline_us": k * period + _us(task.deadline or task.period_ms)}

    def isr_jobs(index, isr):
        period = _us(isr.period_ms)
        for k in range(int(hyper_us // period)):
            yield {"release_us": k * period, "cost_us": float(isr.wcet_us),
                   "priority": ISR_PRIORITY_BASE + index, "name": isr.name, "kind": "isr",
                   "runnables": [], "deadline_us": k * period + period}

    sources = [task_jobs(t) for t in model.tasks if t.period_ms]
    sources += [isr_jobs(i, isr) for i, isr in enumerate(model.isrs) if isr.period_ms and isr.wcet_us is not None]
    return sources


def activation_count(model, hyper_us: float) -> int:
    """Number of activations of all tasks and ISR groups with a period over `hyper_us` (upper bound: a task
    without a runtime has none)."""
    count = sum(int(hyper_us // _us(t.period_ms)) for t in model.tasks if t.period_ms)
    count += sum(int(hyper_us // _us(i.period_ms)) for i in model.isrs if i.period_ms and i.wcet_us is not None)
    return count


def jobs_within(model, hyper_us: float, max_jobs: int | None = None) -> tuple:
    """All activations over the hyper period, always sorted by release time (then highest priority first), and
    the horizon they cover.

    Returns (jobs, horizon_us). If there are more than `max_jobs` (default MAX_JOBS) activations, the list stops
    at a release instant: it holds every activation released before horizon_us and none after it, so a simulation
    of the list is exact up to horizon_us (from the critical instant, A7). horizon_us == hyper_us means complete.
    TOOL-34: before, the list was cut per task and returned unsorted - later tasks were missing and simulate()
    saw releases from the past.
    """
    limit = MAX_JOBS if max_jobs is None else max_jobs
    jobs: list = []
    horizon = hyper_us
    # heapq.merge keeps the order of the sources for equal keys: the same order as a stable sort of the old list
    for job in heapq.merge(*_job_sources(model, hyper_us), key=lambda j: (j["release_us"], -j["priority"])):
        if len(jobs) >= limit:
            # stop at a release instant: an instant is either complete or not in the list at all
            horizon = job["release_us"]
            while jobs and jobs[-1]["release_us"] >= horizon:
                jobs.pop()
            break
        jobs.append(job)
    return jobs, horizon


def jobs_of(model, hyper_us: float) -> list:
    """All activations over the hyper period: (release_us, cost_us, priority, name, kind, detail).

    Always sorted by release time (simulate() relies on it). At most MAX_JOBS activations: a longer list stops at
    a release instant, see jobs_within() for the horizon it covers (TOOL-34).
    """
    return jobs_within(model, hyper_us)[0]


def hyperperiod(model) -> dict:
    """Load over the hyper period: demand per window, peaks and the simulated response time per activation."""
    hyper = model.hyperperiod_ms()
    capped = False
    if float(hyper) > HYPER_CAP_MS:  # unrelated periods (e.g. a measured ISR rate): analyse the task pattern
        hyper = model.hyperperiod_ms(include_isrs=False)
        capped = True
    hyper_us = float(hyper) * 1000.0
    result = {"hyperperiod_ms": float(hyper), "truncated": False, "capped": capped, "windows": [], "peaks": [],
              "simulation": {}, "bucket_us": None}
    if not hyper_us:
        return result
    jobs, horizon_us = jobs_within(model, hyper_us)
    if horizon_us < hyper_us:
        # TOOL-34: more than MAX_JOBS activations - load windows and simulation cover [0, horizon) completely,
        # and the report says how much of the hyper period that is (finding hyperperiod_truncated)
        result["truncated"] = True
        result["analysed_ms"] = horizon_us / 1000.0
        result["activations"] = activation_count(model, hyper_us)
        result["activations_analysed"] = len(jobs)
    step = _bucket_us(model, horizon_us)
    result["bucket_us"] = step
    buckets = int(ceil(horizon_us / step)) or 1
    demand = [0.0] * buckets
    contributors: list = [dict() for _ in range(buckets)]
    for job in jobs:
        index = min(buckets - 1, int(job["release_us"] // step))
        demand[index] += job["cost_us"]
        contributors[index][job["name"]] = contributors[index].get(job["name"], 0.0) + job["cost_us"]
    windows = [{"start_us": i * step, "demand_us": round(demand[i], 3),
                "load": round(demand[i] / step, 4)} for i in range(buckets)]
    if len(windows) <= MAX_WINDOWS_REPORTED:
        result["windows"] = windows
    else:  # a long hyper period would blow up the report; the peaks below keep the interesting part
        del result["windows"]
        result["windows_omitted"] = len(windows)
    order = sorted(range(buckets), key=lambda i: -demand[i])
    result["peaks"] = [{"start_us": i * step, "demand_us": round(demand[i], 3),
                        "load": round(demand[i] / step, 4),
                        "by_task": dict(sorted(contributors[i].items(), key=lambda kv: -kv[1]))}
                       for i in order[:5]]
    result["simulation"] = simulate(jobs, horizon_us)
    return result


def simulate(jobs: list, horizon_us: float, trace: bool = False) -> dict:
    """Preemptive fixed priority simulation of one hyper period (no blocking, see A4).

    trace=True (TOOL-15) additionally returns "pieces": every execution slice in time order as
    {"job", "start_us", "end_us", "from_us"}. "job" is the activation (a copy of the entry of `jobs` plus "number",
    the count of that name from 1, and "late", set once it finished after its deadline); "from_us" is the part of
    the activation's cost that was already done when the slice starts (a preempted activation has several slices).
    This is what a timeline draws (tools/studio); the aggregates are the same with and without trace.
    """
    ready: list = []
    pending = list(jobs)
    # the event loop below takes the releases in list order; a list from elsewhere (not jobs_of) that is not in
    # release order would release jobs late and report false deadline misses (TOOL-34), so it is sorted here
    if any(a["release_us"] > b["release_us"] for a, b in zip(pending, pending[1:])):
        pending.sort(key=lambda j: (j["release_us"], -j["priority"]))
    index = 0
    now = 0.0
    per_name: dict = {}
    counters: dict = {}
    pieces: list = []
    idle_us = 0.0
    guard = 0
    while (index < len(pending) or ready) and guard < 10 * (len(pending) + 10):
        guard += 1
        while index < len(pending) and pending[index]["release_us"] <= now:
            job = dict(pending[index])
            job["remaining"] = job["cost_us"]
            if trace:
                counters[job["name"]] = counters.get(job["name"], 0) + 1
                job["number"] = counters[job["name"]]
                job["done_us"] = 0.0
                job["late"] = False
            ready.append(job)
            index += 1
        if not ready:
            nxt = pending[index]["release_us"]
            idle_us += nxt - now
            now = nxt
            continue
        ready.sort(key=lambda j: (-j["priority"], j["release_us"]))
        job = ready[0]
        next_release = pending[index]["release_us"] if index < len(pending) else float("inf")
        run_until = min(now + job["remaining"], next_release)
        if trace and run_until > now:
            pieces.append({"job": job, "start_us": now, "end_us": run_until, "from_us": job["done_us"]})
            job["done_us"] += run_until - now
        job["remaining"] -= run_until - now
        now = run_until
        if job["remaining"] <= 1e-9:
            ready.pop(0)
            stats = per_name.setdefault(job["name"], {"kind": job["kind"], "jobs": 0, "max_response_us": 0.0,
                                                      "min_response_us": None, "misses": 0, "busy_us": 0.0})
            response = now - job["release_us"]
            stats["jobs"] += 1
            stats["busy_us"] += job["cost_us"]
            stats["max_response_us"] = max(stats["max_response_us"], response)
            stats["min_response_us"] = response if stats["min_response_us"] is None \
                else min(stats["min_response_us"], response)
            if now > job["deadline_us"] + 1e-9:
                stats["misses"] += 1
                if trace:
                    job["late"] = True
    busy = sum(s["busy_us"] for s in per_name.values())
    result = {"horizon_us": horizon_us, "tasks": per_name, "busy_us": round(busy, 3),
              "idle_percent": round(100.0 * max(0.0, horizon_us - busy) / horizon_us, 3) if horizon_us else None,
              "unfinished": len(ready)}
    if trace:  # not part of the report: thousands of slices, and the job dicts are shared between them
        result["pieces"] = pieces
    return result


def hyperperiod_findings(model, result: dict) -> list:
    findings = []
    simulation = result.get("simulation") or {}
    for name, stats in sorted(simulation.get("tasks", {}).items()):
        if stats["misses"]:
            findings.append(Finding(
                ERROR, "simulated_deadline_missed",
                f"over the hyper period ({result['hyperperiod_ms']:.0f} ms) {stats['misses']} of {stats['jobs']} "
                f"activations of {name} finish after their deadline "
                f"(longest response time {stats['max_response_us']:.0f} us)",
                name, "", {"misses": stats["misses"], "jobs": stats["jobs"]}))
    for peak in result.get("peaks", [])[:1]:
        if peak["load"] > 1.0:
            share = ", ".join(f"{k} {v:.0f} us" for k, v in list(peak["by_task"].items())[:4])
            findings.append(Finding(
                WARNING, "load_peak",
                f"load peak at {peak['start_us'] / 1000.0:.1f} ms: {peak['demand_us']:.0f} us of work are "
                f"released within {result['bucket_us'] / 1000.0:.1f} ms ({100.0 * peak['load']:.0f} % of the "
                f"window); largest share: {share}",
                "system", "", peak))
    if result.get("capped"):
        findings.append(Finding(
            INFO, "hyperperiod_capped",
            f"the activation periods have no common pattern below {HYPER_CAP_MS / 1000.0:.0f} s (typical for a "
            f"measured ISR rate): the load over the hyper period was analysed for the task periods "
            f"({result['hyperperiod_ms']:.0f} ms) with the ISR groups left out of the pattern",
            "system", ""))
    if result.get("truncated"):
        findings.append(Finding(
            INFO, "hyperperiod_truncated",
            f"the hyper period ({result['hyperperiod_ms']:.0f} ms) has {result['activations']} activations, more "
            f"than the limit of {MAX_JOBS}: load windows and simulation cover only the first "
            f"{result['analysed_ms']:.1f} ms ({result['activations_analysed']} activations of all tasks and ISR "
            f"groups, from the critical instant A7). Later cycles of the hyper period are not simulated; the "
            f"response time analysis covers all of them",
            "system", "", {"hyperperiod_ms": result["hyperperiod_ms"], "analysed_ms": result["analysed_ms"],
                           "activations": result["activations"],
                           "activations_analysed": result["activations_analysed"], "limit": MAX_JOBS}))
    return findings


# --------------------------------------------------------------------------------------------- offsets

def offset_suggestions(model) -> list:
    """Distributes the runnables of a task over its cycles so that the longest cycle gets shorter.

    Only offsets are suggested (rte.yaml tasks[].runnables[].offset_ms); the phase of a task is not configurable
    (A7). A suggestion keeps the call order and the period of every runnable.
    """
    suggestions = []
    for task in model.tasks:
        if not task.is_application or not task.runnables or not task.period_ms:
            continue
        if any(r.wcet_us is None for r in task.runnables):
            continue
        ticks = task.hyper_ticks()
        if ticks <= 1:
            continue
        before = [task.cycle_us(t) or 0.0 for t in range(ticks)]
        load = [task.overhead_us] * ticks
        chosen = {}
        for r in sorted(task.runnables, key=lambda r: (-float(r.wcet_us), r.order)):
            every = max(1, int(r.period_ms // task.period_ms))
            best, best_peak = None, None
            for start in range(every):
                if start * task.period_ms >= r.period_ms:
                    continue
                trial = list(load)
                for t in range(start, ticks, every):
                    trial[t] += float(r.wcet_us)
                peak = (max(trial), sum(t * t for t in trial))
                if best_peak is None or peak < best_peak:
                    best, best_peak = start, peak
            if best is None:
                best = 0
            chosen[r.name] = best * task.period_ms
            for t in range(best, ticks, every):
                load[t] += float(r.wcet_us)
        if max(load) < max(before) - 1e-9:
            changed = {name: offset for name, offset in chosen.items()
                       if offset != next(r.offset_ms for r in task.runnables if r.name == name)}
            suggestions.append({
                "task": task.name,
                "where": task.where,
                "peak_before_us": round(max(before), 3),
                "peak_after_us": round(max(load), 3),
                "offsets_ms": chosen,
                "changed": changed,
                "cycles_before_us": [round(v, 3) for v in before],
                "cycles_after_us": [round(v, 3) for v in load],
            })
    return suggestions


def offset_findings(model, suggestions: list) -> list:
    findings = []
    for entry in suggestions:
        changed = ", ".join(f"{name} -> offset_ms {offset}" for name, offset in entry["changed"].items())
        findings.append(Finding(
            INFO, "offset_suggestion",
            f"task {entry['task']}: the longest cycle drops from {entry['peak_before_us']:.0f} us to "
            f"{entry['peak_after_us']:.0f} us if the runnables are spread over the cycles ({changed})",
            entry["task"], entry["where"], entry))
    return findings


# --------------------------------------------------------------------------------------------- mode switches

def mode_switches(model) -> list:
    """Event driven load per task (RTE-17): the mode switch runnables (RTE-08) and the worst switch of the task.

    The RTE runs them at the cycle start of their task, before the periodic runnables, when the ECU mode the task
    sees has changed. Worst case: all runnables of one switch in the same cycle as the longest periodic cycle
    (response time of the task); a higher priority task disturbs with one switch per busy window (A8). They have
    no period, so they are not part of the long term utilisation and not of the simulation over the hyper period.
    """
    result = []
    for task in model.tasks:
        switch = task.mode_switch()
        if switch is None:
            continue
        periodic = task.worst_cycle_us()
        result.append({
            "task": task.name,
            "where": task.where,
            "runnables": [{"name": r.name, "event": r.event, "mode": r.mode, "from": r.from_mode,
                           "label": r.label, "wcet_us": r.wcet_us, "mean_us": r.mean_us, "where": r.where}
                          for r in task.mode_runnables],
            "worst_switch": switch,
            "worst_cycle_us": periodic,
            "worst_cycle_with_switch_us": None if periodic is None or switch["us"] is None
            else periodic + switch["us"],
        })
    return result


def _switch_name(switch: dict) -> str:
    old = "first cycle" if switch["initial"] else (switch["from"] or "any mode")
    return f"{old} -> {switch['to'] or 'any mode'}"


def mode_switch_findings(model, switches: list) -> list:
    findings = []
    for entry in switches:
        switch = entry["worst_switch"]
        if switch["us"] is not None:
            findings.append(Finding(
                INFO, "mode_switch_load",
                f"task {entry['task']}: a change of the ECU mode runs up to {switch['us']:.0f} us of mode switch "
                f"runnables in one cycle ({_switch_name(switch)}: {', '.join(switch['runnables']) or '-'}); the "
                f"response time counts it in the same cycle as the longest periodic one",
                entry["task"], entry["where"], {"mode_switch_us": switch["us"], "runnables": switch["runnables"]}))
        elif entry["worst_cycle_us"] is not None:
            findings.append(Finding(
                INFO, "mode_runtime_missing",
                f"task {entry['task']}: no runtime for the mode switch runnables {', '.join(switch['missing'])}; "
                f"the response time is without a mode switch. Switch the ECU mode during the measurement "
                f"(rte json counts every mode switch runnable) or put estimates into the scenario",
                entry["task"], entry["where"], {"missing": switch["missing"]}))
    return findings


def scheduling_analysis(model) -> dict:
    """Everything of this module in one dictionary, plus the findings."""
    load = utilisation(model)
    response = response_times(model)
    hyper = hyperperiod(model)
    offsets = offset_suggestions(model)
    switches = mode_switches(model)
    findings = (utilisation_findings(model, load) + response_findings(model, response)
                + hyperperiod_findings(model, hyper) + offset_findings(model, offsets)
                + mode_switch_findings(model, switches))
    result = {"utilisation": load, "response_times": response, "hyperperiod": hyper,
              "offset_suggestions": offsets, "findings": findings}
    if switches:  # RTE-17: only with mode switch runnables, the report of other systems stays as it was
        result["mode_switches"] = switches
    return result
