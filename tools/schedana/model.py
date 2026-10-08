"""System model of the scheduling analysis: tasks, runnables, ISR groups and signals without any tool dependency.

Every analysis in this package works on a SystemModel only. Loaders fill it from
  * the configuration of a project (rte.yaml + ecu/asyncCoreConfiguration/tasks.yaml, see rteload.py) or
  * a self contained scenario file (scenario.py), used by the host tests and by anybody without ARXML.

The model is plain data (no Qt, no ruamel, no nxconfig) so that the Studio (tools/studio, architecture section 10,
review question 8) and a later web frontend can build, keep and display it.
"""

from dataclasses import dataclass, field
from fractions import Fraction
from functools import reduce
from math import gcd

BSW = "bsw"
APPLICATION = "application"

# Position of an access inside one task cycle. The RTE runs readInputs(task) -> due runnables -> writeOutputs(task),
# so the BSW/CAN transfers are the first and the last accessor of a cycle (RTE-02). The init runnables run once
# before every cycle, at the lifecycle init.
ORDER_INIT = -2
ORDER_READ_INPUTS = -1
ORDER_WRITE_OUTPUTS = 1 << 20
# Mode switch runnables (RTE-08) run at the end of readInputs(task), before the periodic runnables of the cycle.
# Their accesses carry the runnable name, the transfers of readInputs none, so the same position does not mix up.
ORDER_MODE_SWITCH = ORDER_READ_INPUTS

# Mode switch events (RTE-08) in the order the RTE runs them in one switch: exit of the old mode, transition,
# entry of the new mode. The initial mode of the ECU mode manager: its entry runs in the first cycle of a task.
MODE_EVENTS = ("exit", "transition", "entry")
MODE_INITIAL = "STARTUP"

# Semantic roles used by the priority check (priorities.py). They are configuration, never derived from a task name
# inside an analysis: roles.yaml holds the defaults, --roles overrides them.
ROLE_SAFETY = "safety"
ROLE_COMMUNICATION = "communication"


class ModelError(Exception):
    """Invalid analysis input. The message starts with the path of the offending entry."""


@dataclass(frozen=True)
class Access:
    """One accessor of a signal inside one task cycle."""

    task: str
    runnable: str | None  # None: transfer done by the RTE itself (readInputs/writeOutputs, CAN pack/unpack)
    order: int
    mode: str             # "r" or "w"
    where: str = ""
    implicit: bool = False  # RTE-09: Rte_IRead/Rte_IWrite on a copy of the runnable (stable for its whole run)

    @property
    def label(self) -> str:
        if self.runnable is not None:
            return self.runnable
        name = "readInputs" if self.order == ORDER_READ_INPUTS else "writeOutputs"
        target = f" {self.where}" if self.where and "." in self.where else ""
        return f"{name}({self.task}){target}"


@dataclass
class RunnableSpec:
    name: str
    task: str
    period_ms: int
    offset_ms: int = 0
    order: int = 0                     # call order inside the task
    wcet_us: float | None = None       # worst case execution time (measurement or estimate)
    mean_us: float | None = None
    min_us: float | None = None
    reads: tuple = ()
    writes: tuple = ()
    where: str = ""
    implicit_bytes: int = 0            # RTE-09: RAM of its implicit copies (one per implicitly accessed element)
    implicit_locks: int = 0            # RTE-09: lock sections of its wrapper per call (0..2: before / after)

    @property
    def jitter_us(self) -> float | None:
        if self.wcet_us is None or self.min_us is None:
            return None
        return self.wcet_us - self.min_us


@dataclass
class ModeRunnableSpec:
    """A mode switch runnable (RTE-08) with one of its events: runs at the cycle start of its task when the ECU mode
    the task sees changes - not periodic, event driven load of the task (RTE-17). A runnable with two events is two
    entries, as in rte::MODE_RUNNABLES."""

    name: str
    task: str
    event: str                         # MODE_EVENTS: "entry", "exit" or "transition"
    mode: str                          # ECU mode (upper case); transition: the mode it enters
    from_mode: str | None = None       # transition: the mode it leaves
    wcet_us: float | None = None
    mean_us: float | None = None
    min_us: float | None = None
    reads: tuple = ()
    writes: tuple = ()
    where: str = ""
    implicit_bytes: int = 0            # RTE-09, as RunnableSpec
    implicit_locks: int = 0

    @property
    def key(self) -> tuple:
        return (self.task, self.name, self.event, self.from_mode, self.mode)

    @property
    def label(self) -> str:
        """The event as the console prints it: "on entry RUN", "on transition STANDBY -> RUN"."""
        modes = f"{self.from_mode} -> {self.mode}" if self.event == "transition" else self.mode
        return f"on {self.event} {modes}"


@dataclass
class TaskSpec:
    """A task of the system. Application tasks carry runnables, BSW tasks only their timing budget."""

    name: str
    kind: str = APPLICATION
    priority: int = 0                  # ordering key of tasks.yaml / rte.yaml
    os_priority: int = 0               # context number = FreeRTOS priority (OpenBSW asyncFreeRtos)
    period_ms: float | None = None     # None: no periodic activation known (event driven BSW task)
    deadline_ms: float | None = None   # None: implicit deadline = period
    stack_bytes: int | None = None
    above_application: bool = False
    roles: tuple = ()
    overhead_us: float = 0.0           # RTE overhead per cycle (readInputs/writeOutputs, dispatch)
    wcet_us: float | None = None       # BSW tasks: per activation. Application tasks: derived from the runnables.
    mean_us: float | None = None
    blocking_us: float = 0.0           # longest critical section that blocks this task (interrupt lock, RTE lock)
    runnables: list = field(default_factory=list)
    where: str = ""
    mode_runnables: list = field(default_factory=list)  # ModeRunnableSpec, in call order inside one group (RTE-08)

    @property
    def is_application(self) -> bool:
        return self.kind == APPLICATION

    def mode_switch(self) -> dict | None:
        """Worst mode switch of this task (RTE-17): the largest sum of the mode switch runnables one change of the
        ECU mode runs in one cycle - every exit of the old mode, every transition old -> new, every entry of the new
        mode; in the first cycle also the entry of the initial mode (the RTE enters STARTUP, then switches on).

        Any pair of modes can follow each other in the task's view (a mode shorter than a cycle is skipped).
        Returns None without mode switch runnables, else {"us", "from", "to", "runnables", "missing"}; "us" is None
        if a runtime is missing (then "runnables" is the switch with the most runnables)."""
        if not self.mode_runnables:
            return None
        other = "*"  # any mode no event names
        modes = sorted({m for r in self.mode_runnables for m in (r.mode, r.from_mode) if m}) + [other]
        missing = [r for r in self.mode_runnables if r.wcet_us is None]

        def runs(old, new, initial=False):
            chosen = []
            if initial:
                chosen += [r for r in self.mode_runnables if r.event == "entry" and r.mode == MODE_INITIAL]
                if new == MODE_INITIAL:
                    return chosen
                old = MODE_INITIAL
            chosen += [r for r in self.mode_runnables if r.event == "exit" and r.mode == old]
            chosen += [r for r in self.mode_runnables
                       if r.event == "transition" and r.from_mode == old and r.mode == new]
            chosen += [r for r in self.mode_runnables if r.event == "entry" and r.mode == new]
            return chosen

        candidates = [(old, new, runs(old, new)) for old in modes for new in modes if old != new]
        candidates += [(None, new, runs(None, new, initial=True)) for new in modes]

        def cost(entry):
            chosen = entry[2]
            if any(r.wcet_us is None for r in chosen):
                return (float("-inf"), len(chosen))
            return (sum(float(r.wcet_us) for r in chosen), len(chosen))

        def size(entry):
            return len(entry[2])

        best = max(candidates, key=size if missing else cost)
        old, new, chosen = best
        return {
            "us": None if missing else sum(float(r.wcet_us) for r in chosen),
            "from": old if old != other else None,  # None: first cycle (initial mode) or a mode without events
            "to": new if new != other else None,
            "initial": old is None,
            "runnables": [f"{r.name} ({r.label})" for r in chosen],
            "missing": [f"{r.name} ({r.label})" for r in missing],
        }

    def mode_switch_us(self) -> float:
        """Runtime of the worst mode switch, 0 without mode switch runnables or with a missing runtime."""
        switch = self.mode_switch()
        return float(switch["us"]) if switch and switch["us"] is not None else 0.0

    @property
    def deadline(self) -> float | None:
        return self.deadline_ms if self.deadline_ms is not None else self.period_ms

    def hyper_ticks(self) -> int:
        """Number of task cycles until the runnable pattern repeats (period of the dispatch table)."""
        if not self.runnables or not self.period_ms:
            return 1
        ticks = [max(1, int(r.period_ms // self.period_ms)) for r in self.runnables]
        return reduce(lambda a, b: a * b // gcd(a, b), ticks, 1)

    def due(self, tick: int) -> list:
        """Runnables of cycle number tick (rte::isDue: tick % ticks == offset ticks)."""
        result = []
        for r in self.runnables:
            if not self.period_ms:
                continue
            every = max(1, int(r.period_ms // self.period_ms))
            start = int(r.offset_ms // self.period_ms)
            if every and (tick % every) == (start % every):
                result.append(r)
        return result

    def cycle_us(self, tick: int) -> float | None:
        """Execution demand of cycle number tick. None if a runtime is missing."""
        due = self.due(tick)
        if any(r.wcet_us is None for r in due):
            return None
        return self.overhead_us + sum(float(r.wcet_us) for r in due)

    def worst_cycle_us(self) -> float | None:
        """Longest cycle over the hyper period of the task (the C used by the response time analysis)."""
        if not self.is_application or not self.runnables:
            return self.wcet_us
        values = [self.cycle_us(t) for t in range(self.hyper_ticks())]
        if any(v is None for v in values):
            return None
        return max(values) if values else self.overhead_us

    def utilisation(self) -> float | None:
        """C/T, runnable by runnable (exact for multi rate tasks, unlike worst_cycle_us/period)."""
        if not self.period_ms:
            return None
        if not self.is_application or not self.runnables:
            return None if self.wcet_us is None else float(self.wcet_us) / (self.period_ms * 1000.0)
        if any(r.wcet_us is None for r in self.runnables):
            return None
        load = self.overhead_us / (self.period_ms * 1000.0)
        for r in self.runnables:
            load += float(r.wcet_us) / (r.period_ms * 1000.0)
        return load


@dataclass
class IsrSpec:
    """An ISR group (ecu/asyncCoreConfiguration/tasks.yaml isr_groups) as a disturbance of every task."""

    name: str
    period_ms: float | None = None     # minimum inter-arrival time
    wcet_us: float | None = None
    mean_us: float | None = None
    where: str = ""

    def utilisation(self) -> float | None:
        if not self.period_ms or self.wcet_us is None:
            return None
        return float(self.wcet_us) / (self.period_ms * 1000.0)


@dataclass
class SignalSpec:
    """An RTE buffer with all its accessors (runnables and the BSW/CAN transfers of the RTE)."""

    name: str
    label: str = ""
    bsw: str | None = None             # BSW connection, e.g. dio.in.BUTTON1
    accesses: list = field(default_factory=list)
    shared: bool = False               # written/read in more than one task: copies at the task transition
    consistency: str = "single_task"   # single_task | task_copy | atomic
    where: str = ""
    init_only: str = ""                # RTE-27: only the init value, nobody writes it (rtegen Buffer.init_only)

    def writers(self) -> list:
        return [a for a in self.accesses if a.mode == "w"]

    def readers(self) -> list:
        return [a for a in self.accesses if a.mode == "r"]

    def tasks(self) -> list:
        return sorted({a.task for a in self.accesses})


@dataclass
class SystemModel:
    """Everything the analyses need. tasks holds BSW and application tasks, ordered by priority after finish()."""

    name: str = ""
    source: str = ""
    tick_us: int = 1000
    tasks: list = field(default_factory=list)
    isrs: list = field(default_factory=list)
    signals: list = field(default_factory=list)
    max_lock_us: float = 0.0           # longest interrupt lock of the BSW (IO-14/IO-16), blocks every task
    roles_source: str = ""
    notes: list = field(default_factory=list)
    findings: list = field(default_factory=list)   # RTE-27: findings of the configuration (rteload), in every report

    # ------------------------------------------------------------------ lookup

    def task(self, name: str) -> TaskSpec | None:
        return next((t for t in self.tasks if t.name == name), None)

    def application_tasks(self) -> list:
        return [t for t in self.tasks if t.is_application]

    def runnables(self) -> list:
        return [r for t in self.tasks for r in t.runnables]

    def runnable(self, name: str) -> RunnableSpec | None:
        return next((r for r in self.runnables() if r.name == name), None)

    def mode_runnables(self) -> list:
        """Mode switch runnables of all tasks (RTE-08), one entry per event."""
        return [r for t in self.tasks for r in t.mode_runnables]

    def signal(self, name: str) -> SignalSpec | None:
        return next((s for s in self.signals if s.name == name), None)

    def higher_priority(self, task: TaskSpec) -> list:
        """Tasks that can preempt this one (OpenBSW: context number = FreeRTOS priority, no equal priorities)."""
        return [t for t in self.tasks if t.os_priority > task.os_priority]

    # ------------------------------------------------------------------ derived

    def hyperperiod_ms(self, include_isrs: bool = True) -> Fraction:
        """Least common multiple of all activation periods (tasks, runnables and, if asked, ISR groups)."""
        periods = [Fraction(t.period_ms).limit_denominator(1000) for t in self.tasks if t.period_ms]
        periods += [Fraction(r.period_ms) for r in self.runnables()]
        if include_isrs:
            periods += [Fraction(i.period_ms).limit_denominator(1000) for i in self.isrs if i.period_ms]
        if not periods:
            return Fraction(0)
        return reduce(_lcm_fraction, periods)

    def utilisation(self) -> float | None:
        parts = [t.utilisation() for t in self.tasks] + [i.utilisation() for i in self.isrs]
        if any(p is None for p in parts):
            return None
        return sum(parts)  # type: ignore[arg-type]

    def timing_complete(self) -> bool:
        """True if every task, runnable and ISR group has a runtime (otherwise only structural checks are possible)."""
        for t in self.tasks:
            if t.is_application and t.runnables:
                if any(r.wcet_us is None for r in t.runnables):
                    return False
            elif t.wcet_us is None:
                return False
            if any(r.wcet_us is None for r in t.mode_runnables):
                return False
        return all(i.wcet_us is not None and i.period_ms for i in self.isrs)

    # ------------------------------------------------------------------ building

    def finish(self) -> "SystemModel":
        """Sorts tasks by their ordering key, numbers the contexts like rtegen and fills the derived fields."""
        self.tasks.sort(key=lambda t: t.priority)
        for number, task in enumerate(self.tasks, start=1):
            if not task.os_priority:
                task.os_priority = number
            for order, r in enumerate(task.runnables):
                r.order = order
                r.task = task.name
            for r in task.mode_runnables:
                r.task = task.name
        self._collect_signals()
        return self

    def _collect_signals(self) -> None:
        """Adds the accesses of the runnables to the signals; creates signals that only appear in reads/writes."""
        known = {s.name: s for s in self.signals}
        for task in self.tasks:
            for r in task.runnables:
                for mode, names in (("r", r.reads), ("w", r.writes)):
                    for name in names:
                        signal = known.get(name)
                        if signal is None:
                            signal = SignalSpec(name=name, label=name)
                            known[name] = signal
                            self.signals.append(signal)
                        access = Access(task.name, r.name, r.order, mode, r.where)
                        if access not in signal.accesses:
                            signal.accesses.append(access)
            for r in task.mode_runnables:  # RTE-08: at the cycle start, before the periodic runnables
                for mode, names in (("r", r.reads), ("w", r.writes)):
                    for name in names:
                        signal = known.get(name)
                        if signal is None:
                            signal = SignalSpec(name=name, label=name)
                            known[name] = signal
                            self.signals.append(signal)
                        access = Access(task.name, r.name, ORDER_MODE_SWITCH, mode, r.where)
                        if access not in signal.accesses:
                            signal.accesses.append(access)
        for signal in self.signals:
            tasks = {a.task for a in signal.accesses}
            runnable_tasks = {a.task for a in signal.accesses if a.runnable is not None}
            if len(tasks) > 1:
                signal.shared = True
                if signal.consistency == "single_task":
                    signal.consistency = "task_copy"
            elif len(runnable_tasks) <= 1 and not signal.shared:
                signal.consistency = "single_task"
            signal.accesses.sort(key=lambda a: (a.task, a.order, a.mode))


def _lcm_int(a: int, b: int) -> int:
    return abs(a * b) // gcd(a, b) if a and b else 0


def _lcm_fraction(a: Fraction, b: Fraction) -> Fraction:
    """Least common multiple of two rational periods (ms may be fractional for ISR inter-arrival times)."""
    return Fraction(_lcm_int(a.numerator * b.denominator, b.numerator * a.denominator),
                    a.denominator * b.denominator)
