"""Builds the system model from the configuration of a project - read only, nothing is generated.

Sources (the same ones tools/rtegen uses, so the analysis sees exactly what the generated RTE does):
  projects/<project>/project.yaml   board and derivative via tools/nxconfig, names the ECU description
  projects/<project>/rte.yaml       application tasks, runnables with offset and order, connections, CAN
  ecu/asyncCoreConfiguration/tasks.yaml   BSW tasks, OS tick, ISR groups
  the components                    ARXML of a Simulink component, swc.yaml of a hand-written C component (TOOL-93)

The loader calls the loading functions of tools/rtegen (parse_component, build_tasks, build_rte, plan_tasks) and
writes nothing. A configuration error therefore produces the same message with the same YAML path as the
generator.

What the configuration does not contain: runtimes. The activation period and the runtime of the BSW tasks and of
the ISR groups, and the length of the interrupt locks, come from a measurement (measure.apply_to) or from a
budget file (--budget, see load_budget).
"""

import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
TOOLS = REPO / "tools"
for path in (str(TOOLS), str(TOOLS / "rtegen")):
    if path not in sys.path:
        sys.path.insert(0, path)

import nxconfig  # noqa: E402
import rtegen  # noqa: E402

from .model import (APPLICATION, BSW, Access, IsrSpec, ModelError, ModeRunnableSpec, ORDER_INIT,  # noqa: E402
                    ORDER_MODE_SWITCH, ORDER_READ_INPUTS, ORDER_WRITE_OUTPUTS, RunnableSpec, SignalSpec,
                    SystemModel, TaskSpec)

# SWC-MODE-SWITCH-EVENT activation (ARXML, rtegen) -> event name of the console and of the model (RTE-17)
MODE_EVENT_OF = {"ON-ENTRY": "entry", "ON-EXIT": "exit", "ON-TRANSITION": "transition"}
from .findings import WARNING, Finding  # noqa: E402
from .scenario import load_roles, roles_of  # noqa: E402

BUDGET_KEYS = {"schema_version", "max_lock_us", "tasks", "isrs", "notes"}
BUDGET_TASK_KEYS = {"period_ms", "deadline_ms", "wcet_us", "mean_us", "blocking_us", "roles", "overhead_us"}
BUDGET_ISR_KEYS = {"period_ms", "wcet_us", "mean_us"}


def project_file_of(path: Path) -> Path:
    path = Path(path).resolve()
    file = path / "project.yaml" if path.is_dir() else path
    if not file.is_file():
        raise ModelError(f"{path}: no project.yaml")
    return file


def load(project: Path, repo: Path = REPO, mcudb: Path | None = None, tasks_manifest: Path | None = None,
         rte_file: Path | None = None, roles: dict | None = None) -> SystemModel:
    """Reads a project into a SystemModel. rte_file/tasks_manifest override the files of the project (tests).

    TOOL-89 - `repo` is **the tree this load runs as**: the task manifest and the component paths are read from
    it, and it is handed to `nxconfig.load()` as `running_tree` (TOOL-85). Without that a project of a customer
    repository was refused by its own pin ("use the tools of the pinned tree") although the caller had just
    named the pinned tree - the Studio runs this analysis in-process (studio/core/analysis.py, TOOL-10), so it
    could not open the very projects it is meant for. The default is the tree this package lies in, so nothing
    changes for the command line: `mcudb_dir(REPO)` is `nxconfig.MCUDB`.
    """
    project_file = project_file_of(project)
    try:
        config = nxconfig.load(project_file, mcudb, running_tree=repo)
    except nxconfig.ConfigErrors as e:
        raise ModelError(str(e)) from None
    ecu_file = Path(rte_file) if rte_file else config.rte_file
    if ecu_file is None:
        raise ModelError(f"{project_file}: project.rte (ECU description) is not set")
    return load_rte(ecu_file, config, repo=repo, tasks_manifest=tasks_manifest, roles=roles)


def load_rte(ecu_file: Path, config, repo: Path = REPO, tasks_manifest: Path | None = None,
             roles: dict | None = None) -> SystemModel:
    """The part without nxconfig.load: an ECU description plus an already loaded project configuration."""
    roles = roles if roles is not None else load_roles()
    ecu_file = Path(ecu_file)
    try:
        ecu = yaml.safe_load(ecu_file.read_text(encoding="utf-8"))
    except OSError as e:
        raise ModelError(f"{ecu_file}: cannot be read ({e})") from None
    if not isinstance(ecu, dict):
        raise ModelError(f"{ecu_file}: expected a mapping")

    try:
        components = {}
        for entry in ecu.get("components", []):
            swc_dir = rtegen.path_of(ecu, ecu_file, Path(repo), entry["path"])
            comp = _component(entry["name"], swc_dir)       # ARXML or, RTE-20, swc.yaml of a C component
            components[comp.name] = comp
        if not components:
            raise ModelError("no components configured")
        # CAN buses of the project (ARCH-07: a bus name is its bus id, load_can needs the list)
        can = rtegen.load_can(ecu, Path(repo), rtegen.can_buses(config), ecu_file)
        manifest = rtegen.load_task_manifest(Path(tasks_manifest) if tasks_manifest
                                             else Path(repo) / rtegen.TASKS_MANIFEST)
        app_tasks, all_tasks = rtegen.build_tasks(ecu, components, manifest, can)
        buffers, port_buffer = rtegen.build_rte(ecu, components, rtegen.board_signals(config), can,
                                                rtegen.wakeup_sources(config))
        if can is not None and not (can.rx or can.tx):
            can = None
        port_access, sleep_task = rtegen.plan_tasks(components, buffers, port_buffer, app_tasks, can)
        # RTE-09: copies of the runnables with implicit access (memory, lock sections of their wrapper)
        implicit = {ir.name: ir for ir in rtegen.plan_implicit(components, buffers, port_buffer, port_access)}
        # RTE-27: required ports without connection read their init value - a warning, the text of the generator
        unconnected = rtegen.init_value_warnings(components, buffers)
        # RTE-23: disabled components and runnables, and what reads a constant because of them - a warning each
        disabled = rtegen.disabled_warnings(components, buffers, port_buffer)
    except rtegen.RteError as e:
        raise ModelError(str(e)) from None

    model = SystemModel(name=ecu_file.parent.name, source=ecu_file.as_posix(), tick_us=manifest["tick_us"],
                        roles_source=roles.get("source", ""))
    model.notes.append(f"BSW tasks and ISR groups from {manifest['path'].as_posix()}")
    for text in unconnected:
        model.findings.append(Finding(WARNING, "unconnected_port", text, target=text.split()[2], where="connections"))
    for text in disabled:
        where = text.split(":", 1)[0]
        if where.startswith(("required port", "connection")):
            # the port (reading or disabled provided one) at the connections of rte.yaml
            target, where = where.split()[2 if where.startswith("required") else 1], "connections"
        else:
            target = text.split()[2]                   # the component or runnable at its switch
        model.findings.append(Finding(WARNING, "disabled_part", text, target=target, where=where))

    prefix = Path(repo).as_posix() + "/"
    by_name = {}
    for task in all_tasks:
        spec = TaskSpec(
            name=task.name,
            kind=BSW if task.bsw else APPLICATION,
            priority=task.priority,
            os_priority=task.os_priority,
            period_ms=task.period_ms or None,
            stack_bytes=task.stack_bytes or None,
            above_application=task.above_application,
            roles=roles_of(task.name, roles),
            where=task.where.replace(prefix, ""),  # tasks.yaml paths are absolute in rtegen messages
        )
        by_name[task.name] = spec
        model.tasks.append(spec)

    for task in app_tasks:
        spec = by_name[task.name]
        for order, (comp, runnable) in enumerate(task.runnables):
            spec.runnables.append(RunnableSpec(
                name=f"{comp.name}.{runnable.name}",
                task=task.name,
                period_ms=runnable.period_ms,
                offset_ms=runnable.offset_ms,
                order=order,
                where=f"{task.where}.runnables[{order}]",
                **_implicit_of(implicit.get(f"{comp.name}.{runnable.name}")),
            ))
        # RTE-08/RTE-17: mode switch runnables, one entry per event (as rte::MODE_RUNNABLES of the generated RTE)
        for row in rtegen.mode_runnable_table([task]):
            spec.mode_runnables.append(ModeRunnableSpec(
                name=row.name,
                task=task.name,
                event=MODE_EVENT_OF[row.event.activation],
                mode=row.mode,
                from_mode=row.from_mode,
                where=_runnable_where(ecu, task, row.name),
                **_implicit_of(implicit.get(row.name)),
            ))

    for group in manifest["isr_groups"]:
        model.isrs.append(IsrSpec(name=group, where=f"{manifest['path'].name}: isr_groups"))

    _signals(model, buffers, port_buffer, port_access, can, sleep_task)
    _fill_accesses(model, components, port_buffer, app_tasks)
    return model.finish()


def _component(name: str, swc_dir: Path):
    """Component model of one entry of the ECU description, from where the generator reads it (TOOL-93).

    A Simulink component from the ARXML of Embedded Coder, a hand-written C component (RTE-20, `implementation: c`)
    from its swc.yaml - the same Component model either way (rtegen.parse_c_component), so runnables, periods and
    the ports each runnable reads and writes are what the generated RTE has. Before TOOL-93 this read the ARXML
    only, and a project with a C component ended in "no ARXML files in ..." although it was generated and built.
    The calibration values (<Swc>_data.m) are not loaded: the schedule does not depend on them.
    """
    if rtegen.component_implementation(swc_dir) == "c":
        comp, _warnings = rtegen.parse_c_component(name, swc_dir)     # warnings belong to generator and check
    else:
        comp = rtegen.parse_component(name, swc_dir / "generated" / "arxml")
    comp.safety = rtegen.component_safety(swc_dir)   # RTE-27: an unconnected port of it stays an error
    return comp


def _implicit_of(ir) -> dict:
    """RTE-09: memory of the implicit copies of a runnable and the lock sections of its wrapper per call (one before
    it if a copy comes from a published value, one after it if such a copy is written; never one per copy)."""
    if ir is None:
        return {}
    return {"implicit_bytes": ir.byte_size, "implicit_locks": int(ir.lock_before) + int(ir.lock_after)}


def _runnable_where(ecu: dict, task, key: str) -> str:
    """YAML path of a runnable in tasks[].runnables; the task itself if the list is left out (one task)."""
    tasks = ecu.get("tasks") if isinstance(ecu.get("tasks"), list) else []
    for i, entry in enumerate(tasks):
        if not isinstance(entry, dict) or entry.get("name") != task.name:
            continue
        for j, item in enumerate(entry.get("runnables") or []):
            name = item.get("runnable") if isinstance(item, dict) else item
            if name == key:
                return f"tasks[{i}].runnables[{j}]"
        return f"tasks[{i}]"
    return task.where


def _signals(model, buffers, port_buffer, port_access, can, sleep_task) -> None:
    """One SignalSpec per RTE buffer, with the transfers the RTE itself does (readInputs/writeOutputs, CAN)."""
    locked_buffers = set()
    for full, (_, locked) in port_access.items():
        if locked:
            key = port_buffer.get(full)
            if key:
                locked_buffers.add(key)
    for key, buffer in buffers.items():
        consistency = "atomic" if key in locked_buffers else ("task_copy" if buffer.shared else "single_task")
        signal = SignalSpec(name=key, label=buffer.label or key, bsw=buffer.connection,
                            consistency=consistency, where="connections")
        target = f"{buffer.bsw}.{buffer.bsw_label}" if buffer.bsw_signal else (buffer.bsw or "connections")
        if buffer.bsw in rtegen.IO_INPUT_KINDS and buffer.io_task is not None:
            signal.accesses.append(Access(buffer.io_task.name, None, ORDER_READ_INPUTS, "w", target))
        if buffer.bsw in rtegen.IO_OUTPUT_KINDS and buffer.io_task is not None:
            signal.accesses.append(Access(buffer.io_task.name, None, ORDER_WRITE_OUTPUTS, "r", target))
        if buffer.sleep_request and sleep_task is not None:
            signal.accesses.append(Access(sleep_task.name, None, ORDER_WRITE_OUTPUTS, "r", "ecu.sleep_request"))
        signal.shared = buffer.shared
        # RTE-23: the buffer of a disabled provided port is written by nobody on purpose, like an init-only one
        signal.init_only = buffer.init_only or (rtegen.INIT_DISABLED if buffer.disabled_by else "")
        model.signals.append(signal)
    if can is not None and can.task is not None:
        by_name = {s.name: s for s in model.signals}
        key_of = {id(buffer): key for key, buffer in buffers.items()}
        for message in can.rx.values():
            # COM-03: the qualifier of a received message is written by the RTE itself (can.task), whether a
            # component reads it or not - otherwise it would look like a signal without a writer
            if message.qlf_buffer is None:
                continue
            signal = by_name.get(key_of.get(id(message.qlf_buffer)))
            if signal is not None:
                access = Access(can.task.name, None, ORDER_READ_INPUTS, "w",
                                f"can.qlf.{message.message.name}")
                if access not in signal.accesses:
                    signal.accesses.append(access)
        for direction, order, mode in (("rx", ORDER_READ_INPUTS, "w"), ("tx", ORDER_WRITE_OUTPUTS, "r")):
            for message in getattr(can, direction).values():
                for link in message.links:
                    signal = by_name.get(key_of.get(id(link.buffer)))
                    if signal is None:
                        continue
                    # several signals of one message share the transfer: one access per message, not per signal
                    access = Access(can.task.name, None, order, mode,
                                    f"can.{direction}.{message.message.name}")
                    if access not in signal.accesses:
                        signal.accesses.append(access)


def _fill_accesses(model, components, port_buffer, app_tasks) -> None:
    """Adds the accesses of the runnables (ARXML access points) to the signals."""
    by_name = {s.name: s for s in model.signals}
    order_of = {r.name: r.order for r in model.runnables()}
    for comp in components.values():
        for runnable in comp.runnables:
            name = f"{comp.name}.{runnable.name}"
            if runnable.period_ms:
                task_name = runnable.task.name if runnable.task is not None else None
                order = order_of.get(name, 0)
            elif runnable.mode_events:
                # RTE-08: a mode switch runnable runs in its task at the cycle start, not at the lifecycle init
                task_name = runnable.task.name if runnable.task is not None else None
                order = ORDER_MODE_SWITCH
            else:
                # init runnables run once at the lifecycle init, in the context of the first application task
                task_name = app_tasks[0].name if app_tasks else None
                order = ORDER_INIT
            if task_name is None:
                continue
            for key in sorted(runnable.accesses):
                signal = by_name.get(port_buffer.get(f"{comp.name}.{key}"))
                if signal is None:
                    continue
                mode = "w" if comp.ports[key].direction == "out" else "r"
                # RTE-09: implicit only if the runnable has no explicit access point to the element as well
                implicit = key in runnable.implicit and key not in runnable.explicit
                access = Access(task_name, name, order, mode, f"{comp.name}.{key}", implicit)
                if access not in signal.accesses:
                    signal.accesses.append(access)


# --------------------------------------------------------------------------------------------- budget

def load_budget(path: Path) -> dict:
    """Periods and runtimes the configuration does not contain (BSW tasks, ISR groups, interrupt locks)."""
    file = Path(path)
    try:
        data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    except OSError as e:
        raise ModelError(f"{file}: cannot be read ({e})") from None
    if not isinstance(data, dict):
        raise ModelError(f"{file}: expected a mapping")
    unknown = sorted(set(data) - BUDGET_KEYS)
    if unknown:
        raise ModelError(f"{file.name}: {unknown[0]}: unknown key (allowed: {', '.join(sorted(BUDGET_KEYS))})")
    for section, allowed in (("tasks", BUDGET_TASK_KEYS), ("isrs", BUDGET_ISR_KEYS)):
        for name, entry in (data.get(section) or {}).items():
            if not isinstance(entry, dict):
                raise ModelError(f"{file.name}: {section}.{name}: expected a mapping")
            bad = sorted(set(entry) - allowed)
            if bad:
                raise ModelError(f"{file.name}: {section}.{name}.{bad[0]}: unknown key "
                                 f"(allowed: {', '.join(sorted(allowed))})")
    return data


def apply_budget(model: SystemModel, budget: dict, source: str = "budget") -> list:
    """Puts the values of a budget file into the model. Returns remarks about names that do not exist."""
    remarks = []
    if "max_lock_us" in budget:
        model.max_lock_us = float(budget["max_lock_us"])
    for name, entry in (budget.get("tasks") or {}).items():
        task = model.task(name)
        if task is None:
            remarks.append(f"{source}: tasks.{name} is not a task of this system")
            continue
        for key, value in entry.items():
            if key == "roles":
                task.roles = tuple(value)
            else:
                setattr(task, key, value)
    for name, entry in (budget.get("isrs") or {}).items():
        isr = next((i for i in model.isrs if i.name == name), None)
        if isr is None:
            isr = IsrSpec(name=name, where=f"{source}: isrs.{name}")
            model.isrs.append(isr)
            remarks.append(f"{source}: isrs.{name} is not an ISR group of the configuration, added anyway")
        for key, value in entry.items():
            setattr(isr, key, value)
    for note in budget.get("notes") or []:
        model.notes.append(str(note))
    return remarks
