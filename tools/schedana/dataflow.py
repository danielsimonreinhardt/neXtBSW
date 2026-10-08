"""Data flow analysis of the RTE buffers: order inside a cycle, age between tasks, rate transitions, cycles.

The RTE runs one task cycle as readInputs(task) -> due runnables in the configured order -> writeOutputs(task)
(RTE-02). Everything here follows from that:
  * inside one task a value written by an earlier runnable reaches a later runnable in the same cycle; the other
    way round the reader sees the value of the previous cycle (one cycle old),
  * between tasks the RTE copies at the task transition, so a value is published at the end of the writer cycle
    and taken over at the start of the reader cycle: worst case age = writer period + reader period,
  * a buffer that runnables of several tasks use is handed over atomically per access ("atomic"), so a cycle does
    not see a fixed snapshot of it,
  * implicit access (RTE-09): the runnable works on its own copy, loaded before and published after it. Order and
    age stay as above (the writer's value is published at the end of the writer runnable, still before a later
    runnable of the same cycle); an implicit reader of an "atomic" buffer keeps one value for its whole run.
"""

from .findings import ERROR, INFO, WARNING, Finding
from .model import ORDER_INIT, ORDER_MODE_SWITCH

MAX_CYCLE_LENGTH = 12
MAX_CYCLES_REPORTED = 20


def _period(model, task_name: str):
    task = model.task(task_name)
    return task.period_ms if task else None


def _entry_where(model, runnable: str | None) -> str:
    """YAML path of the runnable's entry in tasks[].runnables (rte.yaml or scenario), "" if unknown."""
    found = model.runnable(runnable) if runnable is not None else None
    return found.where if found is not None else ""


def _rate(model, access):
    """Rate at which this accessor touches the signal: the runnable period, else the task period."""
    if access.runnable is not None:
        runnable = model.runnable(access.runnable)
        if runnable is not None:
            return runnable.period_ms
        if access.order == ORDER_MODE_SWITCH:
            return None  # RTE-08: a mode switch runnable has no rate, it runs when the ECU mode changes
    return _period(model, access.task)


def analyse(model) -> dict:
    """Returns {"signals": [...], "cycles": [...], "findings": [...]}."""
    signals, findings = [], []
    for signal in model.signals:
        entry, signal_findings = _signal(model, signal)
        signals.append(entry)
        findings += signal_findings
    cycles = find_cycles(model)
    findings += cycle_findings(cycles)
    return {"signals": signals, "cycles": cycles, "findings": findings}


def _signal(model, signal) -> tuple:
    findings = []
    writers, readers = signal.writers(), signal.readers()
    entry = {
        "signal": signal.name,
        "label": signal.label,
        "bsw": signal.bsw,
        "consistency": signal.consistency,
        "tasks": signal.tasks(),
        "writers": [_accessor(a) for a in writers],
        "readers": [_accessor(a) for a in readers],
        "stale_reads": [],
        "hand_overs": [],
        "where": signal.where,
    }
    if not writers and not getattr(signal, "init_only", ""):
        # RTE-27: a port that reads only its init value is reported once, by the loader (unconnected_port)
        findings.append(Finding(
            WARNING, "signal_without_writer",
            f"signal {signal.label or signal.name} has no writer: every reader gets the initial value "
            f"({', '.join(a.label for a in readers) or 'no reader either'})",
            signal.name, signal.where))
    if not readers:
        findings.append(Finding(
            INFO, "signal_without_reader",
            f"signal {signal.label or signal.name} has no reader: it is written "
            f"({', '.join(a.label for a in writers)}) but nobody uses it",
            signal.name, signal.where))

    # ---- rate transitions (writer and reader touch the signal at different rates)
    for writer in writers:
        for reader in readers:
            if writer.order == ORDER_INIT or reader.order == ORDER_INIT:
                continue
            writer_rate, reader_rate = _rate(model, writer), _rate(model, reader)
            if writer_rate and reader_rate and writer_rate != reader_rate:
                findings += _rate_transition(
                    signal, {"from": writer.task, "to": reader.task, "by": writer.label, "for": reader.label},
                    writer_rate, reader_rate)

    # ---- order inside one task
    for reader in readers:
        for writer in writers:
            if reader.task != writer.task or reader.runnable is None or writer.runnable is None:
                continue
            if ORDER_INIT in (reader.order, writer.order):
                continue  # init runnables run once before the first cycle, an order inside a cycle has no meaning
            if ORDER_MODE_SWITCH in (reader.order, writer.order):
                continue  # RTE-08: a mode switch runnable always runs before the periodic ones, no list to swap
            if reader.order < writer.order:
                period = _period(model, reader.task)
                entry["stale_reads"].append({"reader": reader.runnable, "writer": writer.runnable,
                                             "task": reader.task, "age_ms": period})
                detail = {"reader": reader.runnable, "writer": writer.runnable, "age_ms": period}
                where = _entry_where(model, reader.runnable)
                if reader.where and reader.where != where:
                    detail["port"] = reader.where  # the port of a project (rteload) is no YAML path (TOOL-15)
                findings.append(Finding(
                    WARNING, "reader_before_writer",
                    f"{reader.runnable} reads {signal.label or signal.name} in task {reader.task} before "
                    f"{writer.runnable} writes it in the same cycle: the value is one cycle "
                    f"({period} ms) old. Swap the two entries in tasks[].runnables to get it in the same cycle",
                    signal.name, where or reader.where or signal.where, detail))

    # ---- hand over between tasks
    for writer in writers:
        for reader in readers:
            if writer.task == reader.task:
                continue
            writer_period = _period(model, writer.task)
            reader_period = _period(model, reader.task)
            age = None if not writer_period or not reader_period else writer_period + reader_period
            hand_over = {"from": writer.task, "to": reader.task, "by": writer.label, "for": reader.label,
                         "writer_period_ms": writer_period, "reader_period_ms": reader_period,
                         "worst_age_ms": age, "consistency": signal.consistency}
            entry["hand_overs"].append(hand_over)
            if signal.consistency == "atomic" and reader.implicit:
                # RTE-09: the reader works on its own copy, loaded under the lock before it runs
                findings.append(Finding(
                    INFO, "implicit_hand_over",
                    f"signal {signal.label or signal.name} is used by runnables of several tasks "
                    f"({', '.join(signal.tasks())}); {reader.label} reads it implicitly, so its value stays the "
                    f"same for the whole run of the runnable (copy taken under the lock at its start). Two "
                    f"runnables of one cycle may still see different values",
                    signal.name, signal.where, hand_over))
            elif signal.consistency == "atomic":
                findings.append(Finding(
                    WARNING, "atomic_hand_over",
                    f"signal {signal.label or signal.name} is used by runnables of several tasks "
                    f"({', '.join(signal.tasks())}): the RTE hands every single access over under a lock, so a "
                    f"cycle does not see one fixed value. Read it in one task and pass the result on if a "
                    f"stable value matters",
                    signal.name, signal.where, hand_over))
            elif age is not None:
                findings.append(Finding(
                    INFO, "cross_task_age",
                    f"signal {signal.label or signal.name} goes from task {writer.task} ({writer_period} ms) to "
                    f"task {reader.task} ({reader_period} ms): the reader sees a value that is up to "
                    f"{age} ms old (copy at the task transition)",
                    signal.name, signal.where, hand_over))
    return entry, findings


def _accessor(access) -> dict:
    entry = {"task": access.task, "by": access.label, "order": access.order}
    if access.implicit:
        entry["implicit"] = True  # RTE-09 (key only for implicit access: other reports stay as they were)
    return entry


def _rate_transition(signal, where: dict, writer_period, reader_period) -> list:
    """Writer and reader touch the signal at different rates: values are dropped, repeated or beating."""
    name = signal.label or signal.name
    detail = dict(where, writer_period_ms=writer_period, reader_period_ms=reader_period)
    written = f"{where['by']} writes every {writer_period} ms"
    read = f"{where['for']} reads every {reader_period} ms"
    faster, slower = min(writer_period, reader_period), max(writer_period, reader_period)
    if slower % faster:
        return [Finding(
            WARNING, "rate_transition_not_harmonic",
            f"rate transition of {name} is not harmonic: {written}, {read} ({slower} ms is no multiple of "
            f"{faster} ms). The age of the value changes from cycle to cycle (beat) and which value is seen "
            f"is not repeatable",
            signal.name, signal.where, detail)]
    if reader_period > writer_period:
        every = int(reader_period // writer_period)
        return [Finding(
            INFO, "rate_transition_down",
            f"rate transition of {name}: {written}, {read} - {every - 1} of {every} values are never read "
            f"(the RTE keeps the last one, it does not filter)",
            signal.name, signal.where, detail)]
    return [Finding(
        INFO, "rate_transition_up",
        f"rate transition of {name}: {written}, {read} - the reader sees the same value "
        f"{int(writer_period // reader_period)} times",
        signal.name, signal.where, detail)]


# --------------------------------------------------------------------------------------------- cycles

def build_graph(model) -> dict:
    """Edges writer -> reader over the runnables, with the delay in task cycles.

    delay 0: same task, writer before reader (the value arrives in the same cycle)
    delay 1: same task, writer after reader (the reader sees the previous cycle)
    delay 1 + : between tasks (copy at the task transition, one writer cycle plus one reader cycle)
    """
    edges: dict = {}
    for signal in model.signals:
        for writer in signal.writers():
            if writer.runnable is None:
                continue
            for reader in signal.readers():
                if reader.runnable is None or reader.runnable == writer.runnable:
                    continue
                if writer.task == reader.task:
                    delay = 0 if writer.order < reader.order else 1
                    delay_ms = 0 if delay == 0 else (_period(model, writer.task) or 0)
                else:
                    delay = 1
                    delay_ms = (_period(model, writer.task) or 0) + (_period(model, reader.task) or 0)
                key = (writer.runnable, reader.runnable)
                current = edges.get(key)
                if current is None or delay < current["delay"]:
                    edges[key] = {"signal": signal.name, "delay": delay, "delay_ms": delay_ms,
                                  "from_task": writer.task, "to_task": reader.task}
    graph: dict = {}
    for (src, dst), data in edges.items():
        graph.setdefault(src, []).append((dst, data))
        graph.setdefault(dst, [])
    return graph


def find_cycles(model) -> list:
    """Elementary cycles of the data flow graph (bounded depth), each with its total delay."""
    graph = build_graph(model)
    found, seen = [], set()

    def walk(start, node, path, edges_on_path):
        if len(path) > MAX_CYCLE_LENGTH or len(found) >= MAX_CYCLES_REPORTED:
            return
        for nxt, data in graph.get(node, []):
            if nxt == start:
                ring = path + [nxt]
                key = frozenset(path)
                if key in seen:
                    continue
                seen.add(key)
                used = edges_on_path + [data]
                found.append({
                    "runnables": ring,
                    "signals": [e["signal"] for e in used],
                    "delay_cycles": sum(e["delay"] for e in used),
                    "delay_ms": sum(e["delay_ms"] for e in used),
                    "tasks": sorted({e["from_task"] for e in used} | {e["to_task"] for e in used}),
                })
            elif nxt not in path and nxt > start:
                walk(start, nxt, path + [nxt], edges_on_path + [data])

    for node in sorted(graph):
        walk(node, node, [node], [])
    return found


def cycle_findings(cycles: list) -> list:
    findings = []
    for ring in cycles:
        chain = " -> ".join(ring["runnables"])
        if ring["delay_cycles"] == 0:
            findings.append(Finding(
                ERROR, "dataflow_cycle_without_delay",
                f"data flow cycle without delay: {chain} (signals {', '.join(ring['signals'])}). "
                f"Inside one task cycle this cannot be resolved, one of the values must come from the "
                f"previous cycle",
                ring["runnables"][0], "", ring))
        else:
            findings.append(Finding(
                INFO, "dataflow_cycle",
                f"closed loop {chain} over {', '.join(ring['signals'])}: the loop runs with a delay of "
                f"{ring['delay_ms']} ms ({ring['delay_cycles']} task cycle(s)) - intended for a controller, "
                f"a surprise otherwise",
                ring["runnables"][0], "", ring))
    return findings
