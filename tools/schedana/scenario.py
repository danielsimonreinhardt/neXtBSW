"""Self contained analysis input (scenario file) and the role table.

A scenario describes a complete system - BSW tasks, application tasks with runnables, ISR groups and signals -
without ARXML, nxconfig or a board. It is the test profile of this package (review question 7), the export format
for the Studio and the way to analyse a system whose runtimes are estimates instead of measurements.

Every value has one place: the same keys as rte.yaml / tasks.yaml where they exist there, plus the timing values
(wcet_us, mean_us) that the configuration does not contain. Messages name the path of the entry.
"""

from pathlib import Path

import yaml

from .model import (APPLICATION, BSW, MODE_EVENTS, Access, IsrSpec, ModelError, ModeRunnableSpec,
                    ORDER_READ_INPUTS, ORDER_WRITE_OUTPUTS, RunnableSpec, SignalSpec, SystemModel, TaskSpec)

HERE = Path(__file__).resolve().parent
ROLES_FILE = HERE / "roles.yaml"

TASK_KEYS = {"name", "kind", "priority", "os_priority", "period_ms", "deadline_ms", "stack_bytes",
             "above_application", "roles", "overhead_us", "wcet_us", "mean_us", "blocking_us", "runnables",
             "mode_runnables"}
RUNNABLE_KEYS = {"name", "period_ms", "offset_ms", "wcet_us", "mean_us", "min_us", "reads", "writes"}
# RTE-17: a mode switch runnable with one event (RTE-08): entry/exit of a mode, transition from -> mode
MODE_RUNNABLE_KEYS = {"name", "event", "mode", "from", "wcet_us", "mean_us", "min_us", "reads", "writes"}
ISR_KEYS = {"name", "period_ms", "wcet_us", "mean_us"}
# A signal entry only declares the transfers the RTE itself does (readInputs/writeOutputs of the BSW and CAN
# values); the accesses of the runnables come from runnables[].reads/writes.
SIGNAL_KEYS = {"name", "label", "bsw", "consistency", "read_inputs_task", "write_outputs_task"}
SCENARIO_KEYS = {"schema_version", "name", "tick_us", "max_lock_us", "tasks", "isrs", "signals", "notes"}


def _keys(entry: dict, allowed: set, where: str) -> None:
    unknown = sorted(set(entry) - allowed)
    if unknown:
        raise ModelError(f"{where}.{unknown[0]}: unknown key (allowed: {', '.join(sorted(allowed))})")


def _num(value, where: str, minimum: float | None = None, allow_none: bool = True):
    if value is None:
        if allow_none:
            return None
        raise ModelError(f"{where}: value is required")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ModelError(f"{where}: expected a number, got {value!r}")
    if minimum is not None and value < minimum:
        raise ModelError(f"{where}: {value} is below the minimum {minimum}")
    return value


def _names(value, where: str) -> tuple:
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
        raise ModelError(f"{where}: expected a name or a list of names")
    return tuple(value)


def load_roles(path: Path | None = None) -> dict:
    """Role table: {'tasks': {name: (role, ...)}, 'isr_groups': {...}, 'source': <path>}."""
    file = Path(path) if path else ROLES_FILE
    try:
        data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    except OSError as e:
        raise ModelError(f"{file}: cannot be read ({e})") from None
    if not isinstance(data, dict):
        raise ModelError(f"{file}: expected a mapping")
    result = {"source": file.as_posix(), "tasks": {}, "isr_groups": {}}
    for section in ("tasks", "isr_groups"):
        for name, roles in (data.get(section) or {}).items():
            result[section][str(name)] = _names(roles, f"{file.name}: {section}.{name}")
    return result


def roles_of(name: str, roles: dict, section: str = "tasks") -> tuple:
    return roles.get(section, {}).get(name, ())


def load(path: Path, roles: dict | None = None) -> SystemModel:
    """Reads a scenario file into a SystemModel."""
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise ModelError(f"{path}: cannot be read ({e})") from None
    if not isinstance(data, dict):
        raise ModelError(f"{path}: expected a mapping")
    return from_dict(data, source=path.as_posix(), roles=roles)


def from_dict(data: dict, source: str = "<scenario>", roles: dict | None = None) -> SystemModel:
    _keys(data, SCENARIO_KEYS, source)
    roles = roles if roles is not None else load_roles()
    model = SystemModel(
        name=str(data.get("name") or Path(source).stem),
        source=source,
        tick_us=int(_num(data.get("tick_us", 1000), f"{source}: tick_us", 1, allow_none=False)),
        max_lock_us=float(_num(data.get("max_lock_us", 0), f"{source}: max_lock_us", 0, allow_none=False)),
        roles_source=roles.get("source", ""),
    )
    for note in _names(data.get("notes"), f"{source}: notes"):
        model.notes.append(note)

    entries = data.get("tasks")
    if not isinstance(entries, list) or not entries:
        raise ModelError(f"{source}: tasks: expected a list of tasks")
    names = set()
    for i, entry in enumerate(entries):
        where = f"tasks[{i}]"
        if not isinstance(entry, dict):
            raise ModelError(f"{where}: expected a mapping with name, priority and period_ms")
        _keys(entry, TASK_KEYS, where)
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise ModelError(f"{where}.name: expected a task name")
        if name in names:
            raise ModelError(f"{where}.name: {name} is used twice")
        names.add(name)
        kind = entry.get("kind", APPLICATION)
        if kind not in (BSW, APPLICATION):
            raise ModelError(f"{where}.kind: expected {BSW} or {APPLICATION}, got {kind!r}")
        task = TaskSpec(
            name=name,
            kind=kind,
            priority=int(_num(entry.get("priority"), f"{where}.priority", 1, allow_none=False)),
            os_priority=int(_num(entry.get("os_priority", 0), f"{where}.os_priority", 0, allow_none=False)),
            period_ms=_num(entry.get("period_ms"), f"{where}.period_ms", 0),
            deadline_ms=_num(entry.get("deadline_ms"), f"{where}.deadline_ms", 0),
            stack_bytes=entry.get("stack_bytes"),
            above_application=bool(entry.get("above_application", False)),
            roles=_names(entry.get("roles"), f"{where}.roles") or roles_of(name, roles),
            overhead_us=float(_num(entry.get("overhead_us", 0), f"{where}.overhead_us", 0, allow_none=False)),
            wcet_us=_num(entry.get("wcet_us"), f"{where}.wcet_us", 0),
            mean_us=_num(entry.get("mean_us"), f"{where}.mean_us", 0),
            blocking_us=float(_num(entry.get("blocking_us", 0), f"{where}.blocking_us", 0, allow_none=False)),
            where=where,
        )
        for j, item in enumerate(entry.get("runnables") or []):
            rwhere = f"{where}.runnables[{j}]"
            if not isinstance(item, dict):
                raise ModelError(f"{rwhere}: expected a mapping with name and period_ms")
            _keys(item, RUNNABLE_KEYS, rwhere)
            rname = item.get("name")
            if not isinstance(rname, str) or not rname:
                raise ModelError(f"{rwhere}.name: expected a runnable name")
            period = _num(item.get("period_ms"), f"{rwhere}.period_ms", 1, allow_none=False)
            offset = _num(item.get("offset_ms", 0), f"{rwhere}.offset_ms", 0, allow_none=False)
            if task.period_ms:
                if period % task.period_ms:
                    raise ModelError(f"{rwhere}.period_ms: {period} ms is no multiple of the period "
                                     f"{task.period_ms} ms of task {task.name}")
                if offset % task.period_ms:
                    raise ModelError(f"{rwhere}.offset_ms: {offset} ms is no multiple of the period "
                                     f"{task.period_ms} ms of task {task.name}")
            if offset >= period:
                raise ModelError(f"{rwhere}.offset_ms: {offset} ms must be below the period {period} ms")
            task.runnables.append(RunnableSpec(
                name=rname, task=name, period_ms=int(period), offset_ms=int(offset), order=j,
                wcet_us=_num(item.get("wcet_us"), f"{rwhere}.wcet_us", 0),
                mean_us=_num(item.get("mean_us"), f"{rwhere}.mean_us", 0),
                min_us=_num(item.get("min_us"), f"{rwhere}.min_us", 0),
                reads=_names(item.get("reads"), f"{rwhere}.reads"),
                writes=_names(item.get("writes"), f"{rwhere}.writes"),
                where=rwhere,
            ))
        for j, item in enumerate(entry.get("mode_runnables") or []):
            task.mode_runnables.append(_mode_runnable(item, name, f"{where}.mode_runnables[{j}]"))
        if task.mode_runnables and kind != APPLICATION:
            raise ModelError(f"{where}.mode_runnables: only an application task runs mode switch runnables")
        model.tasks.append(task)

    for i, entry in enumerate(data.get("isrs") or []):
        where = f"isrs[{i}]"
        if not isinstance(entry, dict):
            raise ModelError(f"{where}: expected a mapping with name, period_ms and wcet_us")
        _keys(entry, ISR_KEYS, where)
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise ModelError(f"{where}.name: expected an ISR group name")
        model.isrs.append(IsrSpec(
            name=name,
            period_ms=_num(entry.get("period_ms"), f"{where}.period_ms", 0),
            wcet_us=_num(entry.get("wcet_us"), f"{where}.wcet_us", 0),
            mean_us=_num(entry.get("mean_us"), f"{where}.mean_us", 0),
            where=where,
        ))

    for i, entry in enumerate(data.get("signals") or []):
        where = f"signals[{i}]"
        if not isinstance(entry, dict):
            raise ModelError(f"{where}: expected a mapping with name")
        _keys(entry, SIGNAL_KEYS, where)
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise ModelError(f"{where}.name: expected a signal name")
        signal = SignalSpec(name=name, label=str(entry.get("label") or name), bsw=entry.get("bsw"),
                            consistency=str(entry.get("consistency") or "single_task"), where=where)
        for task_name in _names(entry.get("read_inputs_task"), f"{where}.read_inputs_task"):
            signal.accesses.append(Access(task_name, None, ORDER_READ_INPUTS, "w", where))
        for task_name in _names(entry.get("write_outputs_task"), f"{where}.write_outputs_task"):
            signal.accesses.append(Access(task_name, None, ORDER_WRITE_OUTPUTS, "r", where))
        model.signals.append(signal)

    return model.finish()


def _mode_runnable(item, task: str, where: str) -> ModeRunnableSpec:
    """One mode switch runnable of a scenario (RTE-17): name, event entry/exit/transition, mode, from."""
    if not isinstance(item, dict):
        raise ModelError(f"{where}: expected a mapping with name, event and mode")
    _keys(item, MODE_RUNNABLE_KEYS, where)
    name = item.get("name")
    if not isinstance(name, str) or not name:
        raise ModelError(f"{where}.name: expected a runnable name")
    event = item.get("event")
    if event not in MODE_EVENTS:
        raise ModelError(f"{where}.event: expected {', '.join(MODE_EVENTS)}, got {event!r}")
    mode = item.get("mode")
    if not isinstance(mode, str) or not mode:
        raise ModelError(f"{where}.mode: expected an ECU mode (e.g. RUN)")
    from_mode = item.get("from")
    if event == "transition":
        if not isinstance(from_mode, str) or not from_mode:
            raise ModelError(f"{where}.from: a transition needs the mode it leaves")
        if from_mode.upper() == mode.upper():
            raise ModelError(f"{where}.from: a transition from {mode} to itself never happens")
    elif from_mode is not None:
        raise ModelError(f"{where}.from: only a transition has a mode it leaves")
    return ModeRunnableSpec(
        name=name, task=task, event=event, mode=mode.upper(),
        from_mode=from_mode.upper() if from_mode else None,
        wcet_us=_num(item.get("wcet_us"), f"{where}.wcet_us", 0),
        mean_us=_num(item.get("mean_us"), f"{where}.mean_us", 0),
        min_us=_num(item.get("min_us"), f"{where}.min_us", 0),
        reads=_names(item.get("reads"), f"{where}.reads"),
        writes=_names(item.get("writes"), f"{where}.writes"),
        where=where,
    )


def to_dict(model: SystemModel) -> dict:
    """Exports a model as a scenario (round trip; used to hand a project model to the Studio or to a test)."""
    data = {"schema_version": 1, "name": model.name, "tick_us": model.tick_us, "tasks": []}
    if model.max_lock_us:
        data["max_lock_us"] = model.max_lock_us
    for task in model.tasks:
        entry = {"name": task.name, "kind": task.kind, "priority": task.priority,
                 "os_priority": task.os_priority}
        for key in ("period_ms", "deadline_ms", "stack_bytes", "wcet_us", "mean_us"):
            value = getattr(task, key)
            if value is not None:
                entry[key] = value
        if task.above_application:
            entry["above_application"] = True
        if task.roles:
            entry["roles"] = list(task.roles)
        if task.overhead_us:
            entry["overhead_us"] = task.overhead_us
        if task.blocking_us:
            entry["blocking_us"] = task.blocking_us
        if task.runnables:
            entry["runnables"] = []
            for r in task.runnables:
                item = {"name": r.name, "period_ms": r.period_ms}
                if r.offset_ms:
                    item["offset_ms"] = r.offset_ms
                for key in ("wcet_us", "mean_us", "min_us"):
                    if getattr(r, key) is not None:
                        item[key] = getattr(r, key)
                if r.reads:
                    item["reads"] = list(r.reads)
                if r.writes:
                    item["writes"] = list(r.writes)
                entry["runnables"].append(item)
        if task.mode_runnables:  # RTE-17
            entry["mode_runnables"] = []
            for r in task.mode_runnables:
                item = {"name": r.name, "event": r.event, "mode": r.mode}
                if r.from_mode:
                    item["from"] = r.from_mode
                for key in ("wcet_us", "mean_us", "min_us"):
                    if getattr(r, key) is not None:
                        item[key] = getattr(r, key)
                if r.reads:
                    item["reads"] = list(r.reads)
                if r.writes:
                    item["writes"] = list(r.writes)
                entry["mode_runnables"].append(item)
        data["tasks"].append(entry)
    if model.isrs:
        data["isrs"] = [{k: v for k, v in
                         (("name", i.name), ("period_ms", i.period_ms), ("wcet_us", i.wcet_us),
                          ("mean_us", i.mean_us)) if v is not None}
                        for i in model.isrs]
    signals = []
    for s in model.signals:
        entry = {"name": s.name, "consistency": s.consistency}
        if s.label and s.label != s.name:
            entry["label"] = s.label
        if s.bsw:
            entry["bsw"] = s.bsw
        read_inputs = [a.task for a in s.accesses if a.runnable is None and a.order == ORDER_READ_INPUTS]
        write_outputs = [a.task for a in s.accesses if a.runnable is None and a.order == ORDER_WRITE_OUTPUTS]
        if read_inputs:
            entry["read_inputs_task"] = read_inputs
        if write_outputs:
            entry["write_outputs_task"] = write_outputs
        signals.append(entry)
    if signals:
        data["signals"] = signals
    if model.notes:
        data["notes"] = list(model.notes)
    return data
