"""Reads the measured values of the target from console output.

Sources (console of the ECU application):
  * "rte runnables"  human readable: per task cycles, overruns, longest cycle; per runnable calls and runtime
  * "rte json"       one JSON object per line (ND-JSON) with minimum, mean and maximum runtime and the cycle
                     distance of every task, plus a header and an end marker to detect lost lines (RteSystem)
  * "stats cpu"      OpenBSW RuntimeMonitor: share, runs, average, minimum and maximum runtime per task and
                     per ISR group, and the measurement window
  * "stats stack"    "stack:task=<name>,size=<n>,used=<n>" per task

The input is a text (file, stdin or a string), never a serial port: opening the port is the job of the caller
(tools/studio hardware.py, or "studio console --port ... > file"). The parser is line based and every line is
self contained, so lost or garbled lines (IO-17: the console lost single characters) only drop that one value.
Dropped lines are counted and reported instead of being silently ignored.
"""

import json
import math
import re
from dataclasses import dataclass, field

# rte runnables (RteSystem.cpp)
RE_RTE_TASK = re.compile(
    r"^task (?P<name>\S+) \(context (?P<context>\d+)\): every (?P<period>\d+) ms, cycles (?P<cycles>\d+), "
    r"overruns (?P<overruns>\d+), max (?P<max>\d+) us\s*$")
RE_RTE_RUNNABLE = re.compile(
    r"^\s+(?P<name>\S+): every (?P<period>\d+) ms, offset (?P<offset>\d+) ms, calls (?P<calls>\d+), "
    r"last (?P<last>\d+) us, max (?P<max>\d+) us\s*$")
RE_RTE_INIT = re.compile(r"^init (?P<name>\S+): calls (?P<calls>\d+), last (?P<last>\d+) us\s*$")
# mode switch runnables (RTE-17, writeModeLine in RteConsole.h), one line per event
RE_RTE_MODE = re.compile(
    r"^mode (?P<name>\S+) \(task (?P<task>\S+)\): on (?P<event>entry|exit|transition) (?:(?P<from>\S+) -> )?"
    r"(?P<mode>\S+), calls (?P<calls>\d+), last (?P<last>\d+) us, max (?P<max>\d+) us\s*$")
# marker of a firmware built with the TOOL-09 overflow test (NX_RTE_LOAD_TEST_US)
RE_RTE_LOAD_TEST = re.compile(r"^load test build: runnable (?P<index>\d+) busy waits (?P<us>\d+) us\s*$")

# stats cpu (OpenBSW StatisticsWriter: name padded, then share, total, runs, avg, min, max)
RE_STATS_ROW = re.compile(
    r"^(?P<name>[A-Za-z_][A-Za-z0-9_-]*)\s+(?P<pct>\d+\.\d+) %\s+(?P<total>\d+) ms\s+(?P<runs>\d+)\s+"
    r"(?P<avg>\d+) us\s+(?P<min>\d+) us\s+(?P<max>\d+) us\s*$")
RE_STATS_HEADER = re.compile(r"^(?P<title>task|isr group)\s+%\s+total\s+runs\s+avg\s+min\s+max\s*$")
RE_STATS_WINDOW = re.compile(r"^measurement time:\s+(?P<us>\d+) us\s*$")
RE_STACK = re.compile(r"^stack:task=(?P<name>[^,]+),size=(?P<size>\d+),used=(?P<used>\d+)\s*$")

# Noise a console adds: the echo of the command, a prompt, the reset banner. Not an error, just not a value.
RE_NOISE = re.compile(r"^\s*(?:[>#$]\s*)?(?:rte|stats)(?: [a-z]+)*\s*$|^\s*[>#$]\s*$")
# TOOL-15 (board test 2026-09-16: 38 of 54 lines "not readable"): what else a real console answer contains and
# what is expected, not a loss -
#   * the acknowledgement " ok" the command interpreter prints after every answer (tools/boardtest ANSWER_END),
#   * log lines of the OpenBSW logger in between ("> 376064: RefApp: CONSOLE: INFO: Received console command"),
#   * the heading "### <command>" and "# ..." comment lines of a recorded capture (tools/boardtest --record).
RE_ACK = re.compile(r"^\s*ok\s*$")
RE_LOG = re.compile(r"^\s*>?\s*\d+: [A-Za-z0-9_]+: [A-Za-z0-9_]+: "
                    r"(?:DEBUG|INFO|WARN|WARNING|ERROR|CRITICAL|NONE): ")
RE_CAPTURE_HEADER = re.compile(r"^\s*#(?:##)? ")


@dataclass
class RunnableMeasurement:
    name: str
    task: str | None = None
    period_ms: int | None = None
    offset_ms: int | None = None
    calls: int = 0
    last_us: float | None = None
    min_us: float | None = None
    mean_us: float | None = None
    max_us: float | None = None
    init: bool = False

    @property
    def jitter_us(self) -> float | None:
        if self.max_us is None or self.min_us is None:
            return None
        return self.max_us - self.min_us


@dataclass
class ModeMeasurement:
    """Statistics of one mode switch runnable and event (RTE-17): "mode ..." of rte runnables, {"t":"mode"} of
    rte json. calls 0: the switch did not happen in the measurement window, the runtimes say nothing."""
    name: str
    task: str
    event: str
    mode: str
    from_mode: str | None = None
    calls: int = 0
    last_us: float | None = None
    min_us: float | None = None
    mean_us: float | None = None
    max_us: float | None = None

    @property
    def key(self) -> tuple:
        return (self.task, self.name, self.event, self.from_mode, self.mode)

    @property
    def label(self) -> str:
        modes = f"{self.from_mode} -> {self.mode}" if self.event == "transition" else self.mode
        return f"on {self.event} {modes}"


@dataclass
class TaskMeasurement:
    name: str
    context: int | None = None
    period_ms: int | None = None
    cycles: int | None = None
    overruns: int | None = None
    # cycle runtime of the RTE task runner (rte runnables / rte json)
    min_us: float | None = None
    mean_us: float | None = None
    max_us: float | None = None
    # distance between two cycle starts (rte json): activation jitter
    min_delta_us: float | None = None
    max_delta_us: float | None = None
    # OpenBSW RuntimeMonitor (stats cpu)
    load_percent: float | None = None
    runs: int | None = None
    avg_us: float | None = None
    stats_min_us: float | None = None
    stats_max_us: float | None = None
    # stats stack
    stack_size: int | None = None
    stack_used: int | None = None

    @property
    def stack_free(self) -> int | None:
        if self.stack_size is None or self.stack_used is None:
            return None
        return self.stack_size - self.stack_used

    @property
    def stack_percent(self) -> float | None:
        if not self.stack_size or self.stack_used is None:
            return None
        return 100.0 * self.stack_used / self.stack_size

    @property
    def jitter_us(self) -> float | None:
        """Activation jitter: spread of the distance between two cycle starts."""
        if self.max_delta_us is None or self.min_delta_us is None:
            return None
        return self.max_delta_us - self.min_delta_us


@dataclass
class IsrMeasurement:
    name: str
    load_percent: float | None = None
    runs: int | None = None
    avg_us: float | None = None
    min_us: float | None = None
    max_us: float | None = None
    window_us: float | None = None

    @property
    def rate_per_s(self) -> float | None:
        if self.runs is None or not self.window_us:
            return None
        return self.runs * 1e6 / self.window_us

    @property
    def period_ms(self) -> float | None:
        rate = self.rate_per_s
        if not rate:
            return None
        return 1000.0 / rate


@dataclass
class Measurement:
    """Everything one console dump contains, plus what was unreadable."""

    tasks: dict = field(default_factory=dict)       # name -> TaskMeasurement
    runnables: dict = field(default_factory=dict)   # name -> RunnableMeasurement
    isrs: dict = field(default_factory=dict)        # name -> IsrMeasurement
    window_us: float | None = None                  # measurement window of stats cpu
    up_us: float | None = None                      # system time at the moment of "rte json"
    lines_total: int = 0
    lines_used: int = 0
    lines_ignored: int = 0
    lines_noise: int = 0                            # echo, prompt, " ok", log lines: expected, not a loss
    ignored_examples: list = field(default_factory=list)
    problems: list = field(default_factory=list)
    sources: list = field(default_factory=list)     # which commands were recognised
    announced: dict = field(default_factory=dict)   # counts from the rte json header
    complete: bool | None = None                    # rte json: end marker seen and counts match
    load_test: dict = field(default_factory=dict)   # firmware with the TOOL-09 overflow test switch
    modes: dict = field(default_factory=dict)       # ModeMeasurement.key -> ModeMeasurement (RTE-17)

    def mode(self, name: str, task: str, event: str, mode: str, from_mode: str | None) -> ModeMeasurement:
        found = ModeMeasurement(name, task, event, mode, from_mode or None)
        return self.modes.setdefault(found.key, found)

    def task(self, name: str) -> TaskMeasurement:
        if name not in self.tasks:
            self.tasks[name] = TaskMeasurement(name)
        return self.tasks[name]

    def runnable(self, name: str) -> RunnableMeasurement:
        if name not in self.runnables:
            self.runnables[name] = RunnableMeasurement(name)
        return self.runnables[name]

    def isr(self, name: str) -> IsrMeasurement:
        if name not in self.isrs:
            self.isrs[name] = IsrMeasurement(name)
        return self.isrs[name]

    def to_dict(self) -> dict:
        def clean(obj):
            return {k: v for k, v in vars(obj).items() if v is not None}
        result = {
            "window_us": self.window_us,
            "up_us": self.up_us,
            "tasks": {n: dict(clean(t), stack_free=t.stack_free, jitter_us=t.jitter_us)
                      for n, t in sorted(self.tasks.items())},
            "runnables": {n: dict(clean(r), jitter_us=r.jitter_us) for n, r in sorted(self.runnables.items())},
            "isrs": {n: dict(clean(i), period_ms=i.period_ms, rate_per_s=i.rate_per_s)
                     for n, i in sorted(self.isrs.items())},
            "lines": {"total": self.lines_total, "used": self.lines_used, "ignored": self.lines_ignored,
                      "noise": self.lines_noise, "examples": self.ignored_examples[:5]},
            "sources": self.sources,
            "announced": self.announced,
            "complete": self.complete,
            "load_test": self.load_test,
            "problems": self.problems,
        }
        if self.modes:  # RTE-17: only if the firmware printed mode switch runnables
            items = list(result.items())
            at = [k for k, _ in items].index("runnables") + 1
            modes = [dict(clean(m), label=m.label) for _, m in sorted(self.modes.items(),
                                                                       key=lambda kv: tuple(x or "" for x in kv[0]))]
            result = dict(items[:at] + [("modes", modes)] + items[at:])
        return result


def _clean(line: str) -> str:
    """Removes the noise a serial console adds: CR, NUL, other control characters, trailing spaces."""
    return "".join(c for c in line if c == "\t" or c >= " ").replace("\t", " ").rstrip()


def parse(text: str, known_tasks=(), known_isrs=()) -> Measurement:
    """Parses any mix of rte runnables, rte json, stats cpu and stats stack output.

    known_tasks/known_isrs (names from the model) help when the table header of "stats cpu" was lost: a row is
    assigned by its name instead of by the section it stands in.
    """
    m = Measurement()
    known_tasks, known_isrs = set(known_tasks), set(known_isrs)
    section = None          # "task" or "isr" inside a stats cpu table
    current_task = None     # task of the indented runnable lines of "rte runnables"
    json_lines = 0

    for raw in text.splitlines():
        line = _clean(raw)
        # the console writes one space in front of every output line (" task swc ...", " ok"); the formats in
        # RteSystem.cpp and OpenBSW start at column 0 (TOOL-05 finding with the recorded board answers)
        if line.startswith(" ") and not line.startswith("  "):
            line = line[1:]
        if not line:
            section = None if section == "isr" else section
            continue
        m.lines_total += 1
        stripped = line.strip()

        if RE_NOISE.match(line) or RE_ACK.match(line) or RE_LOG.match(line) or RE_CAPTURE_HEADER.match(line):
            m.lines_noise += 1
            continue

        if stripped.startswith("{") and stripped.endswith("}"):
            if _json_line(m, stripped):
                m.lines_used += 1
                json_lines += 1
            else:
                _ignore(m, line)
            continue

        match = RE_RTE_TASK.match(line)
        if match:
            task = m.task(match["name"])
            task.context = int(match["context"])
            task.period_ms = int(match["period"])
            task.cycles = int(match["cycles"])
            task.overruns = int(match["overruns"])
            task.max_us = float(match["max"])
            current_task = task.name
            _source(m, "rte runnables")
            m.lines_used += 1
            continue

        match = RE_RTE_RUNNABLE.match(line)
        if match:
            r = m.runnable(match["name"])
            r.task = current_task
            r.period_ms = int(match["period"])
            r.offset_ms = int(match["offset"])
            r.calls = int(match["calls"])
            r.last_us = float(match["last"])
            r.max_us = float(match["max"])
            m.lines_used += 1
            continue

        match = RE_RTE_LOAD_TEST.match(line)
        if match:
            m.load_test = {"runnable_index": int(match["index"]), "us": int(match["us"])}
            _source(m, "rte runnables")
            m.lines_used += 1
            continue

        match = RE_RTE_INIT.match(line)
        if match:
            r = m.runnable(match["name"])
            r.init = True
            r.calls = int(match["calls"])
            r.last_us = float(match["last"])
            if r.max_us is None:
                r.max_us = r.last_us
            m.lines_used += 1
            continue

        match = RE_RTE_MODE.match(line)
        if match:
            r = m.mode(match["name"], match["task"], match["event"], match["mode"], match["from"])
            r.calls = int(match["calls"])
            r.last_us = float(match["last"])
            r.max_us = float(match["max"])
            _source(m, "rte runnables")
            m.lines_used += 1
            continue

        match = RE_STACK.match(line)
        if match:
            size, used = int(match["size"]), int(match["used"])
            _source(m, "stats stack")
            if used > size:  # a garbled number: a task cannot use more stack than it has
                m.problems.append(f"stats stack: {match['name']} reports used {used} > size {size}, "
                                  f"line dropped")
                _ignore(m, line)
                continue
            task = m.task(match["name"])
            task.stack_size = size
            task.stack_used = used
            m.lines_used += 1
            continue

        match = RE_STATS_HEADER.match(line)
        if match:
            section = "task" if match["title"] == "task" else "isr"
            _source(m, "stats cpu")
            m.lines_used += 1
            continue

        match = RE_STATS_WINDOW.match(line)
        if match:
            m.window_us = float(match["us"])
            for isr in m.isrs.values():
                isr.window_us = m.window_us
            m.lines_used += 1
            continue

        if set(line) <= {"-", " "}:  # separator line of the table
            m.lines_used += 1
            continue

        match = RE_STATS_ROW.match(line)
        if match:
            name = match["name"]
            where = section
            if where is None:  # header lost: decide by name
                where = "isr" if name in known_isrs and name not in known_tasks else "task"
                if name not in known_isrs and name not in known_tasks:
                    m.problems.append(f"stats cpu: row {name!r} without table header, counted as a task")
            if where == "isr":
                isr = m.isr(name)
                isr.load_percent = float(match["pct"])
                isr.runs = int(match["runs"])
                isr.avg_us = float(match["avg"])
                isr.min_us = float(match["min"])
                isr.max_us = float(match["max"])
                isr.window_us = m.window_us
            else:
                task = m.task(name)
                task.load_percent = float(match["pct"])
                task.runs = int(match["runs"])
                task.avg_us = float(match["avg"])
                task.stats_min_us = float(match["min"])
                task.stats_max_us = float(match["max"])
            _source(m, "stats cpu")
            m.lines_used += 1
            continue

        _ignore(m, line)

    if json_lines:
        _source(m, "rte json")
        expected = m.announced
        if expected:
            tasks = len([t for t in m.tasks.values() if t.cycles is not None])
            runnables = len([r for r in m.runnables.values() if not r.init])
            missing = []
            if expected.get("tasks") is not None and tasks < expected["tasks"]:
                missing.append(f"{expected['tasks'] - tasks} of {expected['tasks']} task lines")
            if expected.get("periodic") is not None and runnables < expected["periodic"]:
                missing.append(f"{expected['periodic'] - runnables} of {expected['periodic']} runnable lines")
            if expected.get("modes") is not None and len(m.modes) < expected["modes"]:
                missing.append(f"{expected['modes'] - len(m.modes)} of {expected['modes']} mode lines")
            if missing:
                m.problems.append("rte json: lost " + ", ".join(missing))
            m.complete = not missing and m.announced.get("end_seen", False)
        else:
            m.problems.append("rte json: header line lost, cannot tell whether lines are missing")
            m.complete = False
    if m.lines_ignored:
        m.problems.append(f"{m.lines_ignored} of {m.lines_total} lines were not readable and are ignored "
                          f"(example: {m.ignored_examples[0]!r})")
    return m


def _source(m: Measurement, name: str) -> None:
    if name not in m.sources:
        m.sources.append(name)


def _ignore(m: Measurement, line: str) -> None:
    m.lines_ignored += 1
    if len(m.ignored_examples) < 5:
        m.ignored_examples.append(line[:80])


def _json_line(m: Measurement, line: str) -> bool:
    """One ND-JSON line of "rte json". Returns False if it is not usable (garbled, unknown type)."""
    try:
        obj = json.loads(line)
    except ValueError:
        return False
    if not isinstance(obj, dict):
        return False
    kind = obj.get("t")

    def num(key):
        value = obj.get(key)
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    def integer(key):
        value = obj.get(key)
        return int(value) if isinstance(value, int) and not isinstance(value, bool) else None

    if kind == "rte":
        m.announced.update({k: v for k, v in
                            (("tasks", integer("tasks")), ("runnables", integer("runnables")),
                             ("periodic", integer("periodic")), ("modes", integer("modes"))) if v is not None})
        m.up_us = num("up_us")
        if num("load_test_us") is not None:
            m.load_test = {"runnable_index": integer("load_test_index") or 0, "us": num("load_test_us")}
        return True
    if kind == "end":
        m.announced["end_seen"] = True
        return True
    name = obj.get("name")
    if not isinstance(name, str) or not name:
        return False
    if kind == "task":
        task = m.task(name)
        task.context = integer("context") if integer("context") is not None else task.context
        task.period_ms = integer("period_ms") if integer("period_ms") is not None else task.period_ms
        task.cycles = integer("cycles") if integer("cycles") is not None else task.cycles
        task.overruns = integer("overruns") if integer("overruns") is not None else task.overruns
        for attr, key in (("min_us", "min_us"), ("mean_us", "mean_us"), ("max_us", "max_us"),
                          ("min_delta_us", "min_delta_us"), ("max_delta_us", "max_delta_us")):
            value = num(key)
            if value is not None:
                setattr(task, attr, value)
        return True
    if kind == "mode":
        task, event, mode, from_mode = obj.get("task"), obj.get("event"), obj.get("mode"), obj.get("from")
        if not isinstance(task, str) or event not in ("entry", "exit", "transition") or not isinstance(mode, str) \
                or not mode or not isinstance(from_mode, str) or (event == "transition") != bool(from_mode):
            return False
        r = m.mode(name, task, event, mode, from_mode)
        if integer("calls") is not None:
            r.calls = integer("calls")
        for attr in ("last_us", "min_us", "mean_us", "max_us"):
            value = num(attr)
            if value is not None:
                setattr(r, attr, value)
        return True
    if kind in ("runnable", "init"):
        r = m.runnable(name)
        r.init = kind == "init"
        task = obj.get("task")
        r.task = task if isinstance(task, str) else r.task
        for attr, key in (("period_ms", "period_ms"), ("offset_ms", "offset_ms"), ("calls", "calls")):
            value = integer(key)
            if value is not None:
                setattr(r, attr, value)
        for attr in ("last_us", "min_us", "mean_us", "max_us"):
            value = num(attr)
            if value is not None:
                setattr(r, attr, value)
        return True
    return False


def apply_to(model, m: Measurement) -> list:
    """Fills the runtimes of a model from a measurement. Returns the list of remarks (names that do not match).

    Runnables and application tasks take their runtime from "rte runnables"/"rte json", BSW tasks and ISR groups
    from "stats cpu" (maximum as worst case, average as mean, run count as rate).
    """
    remarks = []
    for task in model.tasks:
        found = m.tasks.get(task.name)
        if task.is_application and task.runnables:
            for r in task.runnables:
                key = r.name if r.name in m.runnables else _match_runnable(r.name, m.runnables)
                measured = m.runnables.get(key) if key else None
                if measured is None:
                    remarks.append(f"no measurement for runnable {r.name}")
                    continue
                r.wcet_us = measured.max_us if measured.max_us is not None else r.wcet_us
                r.mean_us = measured.mean_us if measured.mean_us is not None else measured.last_us
                r.min_us = measured.min_us if measured.min_us is not None else r.min_us
            # the cycle overhead is what the task runner needs beyond its runnables
            if found is not None and found.max_us is not None:
                runnable_max = sum(float(r.wcet_us) for r in task.runnables if r.wcet_us is not None)
                task.overhead_us = max(0.0, found.max_us - runnable_max) if runnable_max else task.overhead_us
        else:
            if found is None or found.stats_max_us is None:
                if found is None or found.load_percent is None:
                    remarks.append(f"no measurement for task {task.name}")
                continue
            task.wcet_us = found.stats_max_us
            task.mean_us = found.avg_us
            if not task.period_ms and found.runs and m.window_us:
                measured = m.window_us / found.runs / 1000.0
                task.period_ms, how = _on_tick_grid(measured, model.tick_us, found.runs)
                remarks.append(f"task {task.name}: activation period {measured:.5f} ms derived from "
                               f"{found.runs} runs in the measurement window, used as "
                               f"{task.period_ms} ms ({how})")
                continue
        # TOOL-34: a configured period stays; the measurement only brings runtime and jitter. A rate that does
        # not fit the configured period is worth a remark (other image on the board, event driven task).
        if task.period_ms and found is not None and found.runs and m.window_us:
            measured = m.window_us / found.runs / 1000.0
            if abs(measured - float(task.period_ms)) > _tolerance(found.runs) * float(task.period_ms):
                remarks.append(f"task {task.name}: {found.runs} runs in the measurement window are one activation "
                               f"every {measured:.3f} ms, the configured period is {task.period_ms} ms; the "
                               f"configured period is used")
    for r in model.mode_runnables():  # RTE-17
        measured = m.modes.get(r.key) or next(
            (x for x in m.modes.values() if (x.name, x.event, x.from_mode, x.mode) == r.key[1:]), None)
        if measured is None:
            if m.modes or "rte json" in m.sources or "rte runnables" in m.sources:
                remarks.append(f"no measurement for mode switch runnable {r.name} {r.label}")
            continue
        if measured.task != r.task:
            remarks.append(f"mode switch runnable {r.name} {r.label} runs in task {measured.task} on the target, "
                           f"the configuration says {r.task}")
        if not measured.calls:
            remarks.append(f"mode switch runnable {r.name} {r.label} did not run during the measurement window: "
                           f"no runtime (switch the ECU mode while measuring)")
            continue
        r.wcet_us = measured.max_us if measured.max_us is not None else r.wcet_us
        r.mean_us = measured.mean_us if measured.mean_us is not None else measured.last_us
        r.min_us = measured.min_us if measured.min_us is not None else r.min_us
    for isr in model.isrs:
        found = m.isrs.get(isr.name)
        if found is None:
            remarks.append(f"no measurement for ISR group {isr.name}")
            continue
        isr.wcet_us = found.max_us if found.max_us is not None else isr.wcet_us
        isr.mean_us = found.avg_us if found.avg_us is not None else isr.mean_us
        if not found.runs and m.window_us:
            # the group did not run during the measurement window: no load, but also no evidence of its runtime
            isr.wcet_us = 0.0
            # TOOL-34: the window itself (999.995 ms) is no period; on the tick grid it keeps the hyper period small
            isr.period_ms, _ = _on_tick_grid(m.window_us / 1000.0, model.tick_us, None)
            remarks.append(f"ISR group {isr.name} did not run during the measurement window, counted with "
                           f"0 us - it can still disturb, give it a budget (--budget) if it matters")
        elif found.period_ms:
            isr.period_ms, how = _on_tick_grid(found.period_ms, model.tick_us, found.runs)
            remarks.append(f"ISR group {isr.name}: {found.runs} runs in the measurement window are "
                           f"{found.period_ms:.5f} ms between two runs, used as {isr.period_ms} ms ({how})")
    known = {t.name for t in model.tasks} | {i.name for i in model.isrs}
    for name in sorted(set(m.tasks) | set(m.isrs)):
        if name not in known and name not in ("idle", "timer"):
            remarks.append(f"measured task/ISR group {name} is not part of the configuration")
    return remarks


TICK_TOLERANCE = 0.01   # relative deviation from a tick multiple that is still measurement noise (TOOL-34)


def _tolerance(runs: int | None) -> float:
    """Relative uncertainty of a period derived from `runs` runs in a window: the window cuts the activations at
    both ends, so the count is only known to +-1 (100 runs: 1 %). At least TICK_TOLERANCE."""
    return max(TICK_TOLERANCE, 1.0 / runs) if runs else TICK_TOLERANCE


def _on_tick_grid(period_ms: float, tick_us: int, runs: int | None) -> tuple:
    """Puts a measured inter-arrival time on the OS tick grid. Returns (period_ms, how) - how for the remark.

    A cyclic task is scheduled on the tick grid, so its configured period is a multiple of the tick; the measured
    rate (runs per measurement window) only says which multiple. TOOL-34: 100 runs in 999 995 us are 9.99995 ms
    and mean the 10 ms task - rounding that down to 9 ms (TOOL-09) made the hyper period 99.9 s and let the
    simulation release the task 11 % too often. So the nearest multiple is taken when the deviation is within the
    measurement uncertainty (_tolerance: the count is known to +-1, at least 1 %). A rate that is clearly off the
    grid (sporadic ISR, event driven task) is rounded down as before, so its disturbance is not underestimated,
    and a rate faster than one tick keeps its measured value (rounded down to 1 us) instead of becoming one tick.
    """
    tick_ms = max(tick_us, 1) / 1000.0
    if period_ms < tick_ms * (1.0 - _tolerance(runs)):
        return math.floor(period_ms * 1000.0) / 1000.0, "faster than the OS tick, kept as measured"
    nearest = max(1, round(period_ms / tick_ms)) * tick_ms
    if abs(nearest - period_ms) <= _tolerance(runs) * period_ms:
        return round(nearest, 6), "the nearest multiple of the OS tick, within the measurement uncertainty"
    ticks = max(1, int(period_ms / tick_ms))
    return round(ticks * tick_ms, 6), ("not close to a tick multiple: rounded down to the OS tick grid, so the "
                                       "disturbance is not underestimated")


def _match_runnable(name: str, measured: dict) -> str | None:
    """The console prints "Component.Runnable", the configuration may use the bare runnable name (and vice versa)."""
    for key in measured:
        if key == name or key.endswith(f".{name}") or name.endswith(f".{key}"):
            return key
    return None
