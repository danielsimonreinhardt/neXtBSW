"""SYS-117: the degraded reaction SD of the safe supervisor - project values, checks and what is derived from them.

doc/safety/partner-and-degraded.md part B (decisions B1-B6 of the user, 2026-09-29, each in its recommended variant):
an event the supervisor declares degradable may be mapped by the project to SD - its safe-output groups go to their
safe state, the event is reported (log, RTE signal `safety.degraded`, a DTC that samples it), the rest of the ECU keeps
running without a reset, and the groups stay degraded until the power cycle. Everything else keeps its fail-safe
reaction.

Configuration levels (architecture section 4): the groups and the mapping are project values (`safety.output_groups`,
`safety.degraded` of project.yaml). Which events may be degraded at all is a **fact of the supervisor's code** - the
closed list NX_SAFE_DEGRADABLE_EVENTS of ecu/safety/safeSupervisor/include/safeSupervisor/Degraded.h - read here, never
a project value: a mapping of any other event (every fault of the MCU itself) is refused with its YAML path. The event
numbers come from `enum class Event` of SafeSupervisor.h. Derived here, for integration/safeSupervisorConfiguration.h
(tools/bswgen, NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_*), only for a project with a mapping - every other project
gets not one derived value more, so its supervisor is compiled exactly as before:
- per group the bit of its entries in the safe outputs table (tools/bswgen collect_safe_outputs: the PWM groups first,
  in the order the project uses them - `Config.selected_pwm()`; the safe_off outputs follow, and since IO-45 an output
  of a device bound to a PWM group - board pwm[].gate_driver, the enable of its gate driver - joins that group);
- per mapping one word: event << 24 | device instance << 16 | bus id << 8 | groups (safeSupervisor/Degraded.h), and
  the groups of the whole bus if every device on it is mapped (a lost bus is a common cause, concept section 4.2).

Device instance (the byte of the device context word, safeSupervisor/DeviceReport.h: "assigned by the integration of
the project, unique per ECU"): the position of the device among the board devices the project integrates
(`Integration.devices`, board order) - the rule this task fixes for the device packages of SYS-28, which raise
DEVICE_FAULT. Bus id: the rule of tools/rtegen device_bus_id (SYS-29): SPI kind 1, index of the used SPI bus in board
order; a device on no bus or on several gets NO_BUS.

SYS-135 (safety review SYS-128/SYS-130 C1, decision of the safety responsible 2026-09-30): the report HW_SHUTOFF of a
hardware shut-off path (safeSupervisor/HwShutoff.h, not an event of SafeSupervisor::Event) is on a second closed list,
NX_SAFE_DEGRADABLE_REPORTS of Degraded.h, with the id its mapping carries in the event field. A mapping
`{event: HW_SHUTOFF, groups: [<group>]}` names exactly the group whose PWM group a hardware threshold of the project
switches off; derived: report id << 24 | timer instance number << 16 | NO_BUS << 8 | that group. Refused: another
number of groups, a group without a threshold, thresholds on more than one timer (the report record merges the paths),
a timer without an instance number. Without the mapping every report keeps S1 held (SYS-130).

Qt free; every finding names its YAML path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import REPO, Config, Reporter

SUPERVISOR_MODULE = "safeSupervisor"
DEGRADED_HEADER = Path("ecu/safety/safeSupervisor/include/safeSupervisor/Degraded.h")
SUPERVISOR_HEADER = Path("ecu/safety/safeSupervisor/include/safeSupervisor/SafeSupervisor.h")
PROJECT = "project"

BUS_KIND_SPI = 1          # safety::device::BusKind::SPI
BUS_INDEX_MAX = 31        # safety::device::BUS_INDEX_MAX
NO_BUS = 0xFF             # safety::device::NO_BUS
TABLE_BITS = 32           # entry mask of SafeState::enterDegradedState (uint32_t)
DEVICE_EVENTS = ("DEVICE_FAULT",)   # events whose mapping names a board device (`device`)
EVENT_FIELD_IMPLAUSIBLE = 0xFF      # SYS-135: the 8-bit event field of a mapping word; 0xFF is never a report id
HW_SHUTOFF = "HW_SHUTOFF"           # SYS-135: report of a hardware shut-off path (safeSupervisor/HwShutoff.h)


@dataclass(frozen=True)
class SupervisorFacts:
    """What the supervisor's code says: event numbers, the closed list, the limits."""

    events: dict[str, int]          # SafeSupervisor::Event -> number
    degradable: tuple[str, ...]     # NX_SAFE_DEGRADABLE_EVENTS
    max_groups: int
    max_mappings: int
    files: tuple[Path, ...]
    reports: dict[str, int] = field(default_factory=dict)   # SYS-135: NX_SAFE_DEGRADABLE_REPORTS, name -> id


def read_facts(repo: Path = REPO) -> SupervisorFacts:
    """Reads the closed list and the event numbers from the supervisor's headers; raises ValueError."""
    degraded_file, supervisor_file = Path(repo) / DEGRADED_HEADER, Path(repo) / SUPERVISOR_HEADER
    try:
        degraded_text = degraded_file.read_text(encoding="utf-8")
        supervisor_text = supervisor_file.read_text(encoding="utf-8")
    except OSError as e:
        raise ValueError(f"cannot read the safe supervisor's code: {e}") from e
    listed = re.search(r"^#define NX_SAFE_DEGRADABLE_EVENTS\(X\)\s+(.*)$", degraded_text, re.M)
    if listed is None:
        raise ValueError(f"{DEGRADED_HEADER.as_posix()}: NX_SAFE_DEGRADABLE_EVENTS(X) not found")
    degradable = tuple(re.findall(r"X\((\w+)\)", listed.group(1)))
    limits = {}
    for name in ("MAX_GROUPS", "MAX_MAPPINGS"):
        found = re.search(rf"constexpr uint32_t {name} = (\d+)U;", degraded_text)
        if found is None:
            raise ValueError(f"{DEGRADED_HEADER.as_posix()}: {name} not found")
        limits[name] = int(found.group(1))
    body = re.search(r"enum class Event : uint8_t\s*\{(.*?)\};", supervisor_text, re.S)
    if body is None:
        raise ValueError(f"{SUPERVISOR_HEADER.as_posix()}: enum class Event not found")
    text = re.sub(r"/\*.*?\*/", "", body.group(1), flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    names = [n.strip() for n in text.split(",") if n.strip()]
    if not all(re.fullmatch(r"[A-Z][A-Z0-9_]*", n) for n in names):
        raise ValueError(f"{SUPERVISOR_HEADER.as_posix()}: enum class Event has entries with explicit values or "
                         f"unexpected text; the numbering cannot be read")
    events = {n: i for i, n in enumerate(names)}
    unknown = [n for n in degradable if n not in events]
    if unknown:
        raise ValueError(f"{DEGRADED_HEADER.as_posix()}: NX_SAFE_DEGRADABLE_EVENTS names {', '.join(unknown)}, which "
                         f"is no event of SafeSupervisor::Event")
    # SYS-135: the reports outside SafeSupervisor::Event (HW_SHUTOFF) with the id of their mapping's event field
    listed = re.search(r"^#define NX_SAFE_DEGRADABLE_REPORTS\(X\)\s+(.*)$", degraded_text, re.M)
    if listed is None:
        raise ValueError(f"{DEGRADED_HEADER.as_posix()}: NX_SAFE_DEGRADABLE_REPORTS(X) not found")
    reports = {name: int(number, 0)
               for name, number in re.findall(r"X\((\w+),\s*(0x[0-9A-Fa-f]+|\d+)\)", listed.group(1))}
    wrong = [n for n, i in reports.items() if n in events or not len(events) <= i < EVENT_FIELD_IMPLAUSIBLE]
    if wrong:
        raise ValueError(f"{DEGRADED_HEADER.as_posix()}: NX_SAFE_DEGRADABLE_REPORTS names {', '.join(wrong)} with an "
                         f"event name or an id outside {len(events)}..{EVENT_FIELD_IMPLAUSIBLE - 1}")
    return SupervisorFacts(events, degradable, limits["MAX_GROUPS"], limits["MAX_MAPPINGS"],
                           (degraded_file, supervisor_file), reports)


def bus_ids(cfg: Config) -> dict[str, int]:
    """Bus id of every board device the project integrates (the rule of tools/rtegen device_bus_ids, SYS-29)."""
    spi = list(cfg.integration.spi)
    ids = {}
    for device in cfg.integration.devices:
        used = list(device.spi or [])
        bus = NO_BUS
        if len(used) == 1 and used[0] in spi and spi.index(used[0]) <= BUS_INDEX_MAX:
            bus = (BUS_KIND_SPI << 5) | spi.index(used[0])
        ids[device.name] = bus
    return ids


def instances(cfg: Config) -> dict[str, int]:
    """Device instance of every board device the project integrates: its position among them (board order)."""
    return {d.name: i for i, d in enumerate(cfg.integration.devices)}


def safe_off_outputs(cfg: Config) -> list[str]:
    """`device.connection` of every safe_off output of the project (the second block of the safe outputs table)."""
    found = []
    for device in cfg.integration.devices:
        manifest = cfg.modules.manifests.get(device.module)
        if manifest is None:
            continue
        for key, spec in manifest.connect.items():
            if spec.safe_off and key in device.connect:
                found.append(f"{device.name}.{key}")
    return found


@dataclass(frozen=True)
class SafeOffEntry:
    """IO-45: one entry of the second block of the safe outputs table, as tools/bswgen collect_safe_outputs builds it."""

    index: int                  # entry index in the whole table (the PWM groups come first)
    output: str                 # dio output (board name)
    users: tuple[str, ...]      # every `device.connection` that declares it safe_off
    pwm: tuple[str, ...]        # PWM groups of the project the devices of `users` are bound to (board pwm[].gate_driver)


def safe_off_entries(cfg: Config) -> list[SafeOffEntry]:
    """IO-45: the safe_off block of the safe outputs table in the order of tools/bswgen collect_safe_outputs - every
    integrated device in board order, its manifest connections in manifest order, an output named twice only once -
    with the PWM group each entry is bound to through its device (board pwm[].gate_driver, `DeviceUse.pwm`).
    test_degraded.py compares the order with the table bswgen generates."""
    first = len(cfg.selected_pwm())
    order: list[str] = []
    users: dict[str, list[str]] = {}
    pwm: dict[str, list[str]] = {}
    for device in cfg.integration.devices:
        manifest = cfg.modules.manifests.get(device.module)
        if manifest is None:
            continue
        for key, spec in manifest.connect.items():
            if not spec.safe_off or key not in device.connect:
                continue
            output = device.connect[key]
            if output not in users:
                order.append(output)
                users[output], pwm[output] = [], []
            users[output].append(f"{device.name}.{key}")
            group = getattr(device, "pwm", None)
            if group is not None and group not in pwm[output]:
                pwm[output].append(group)
    return [SafeOffEntry(first + i, name, tuple(users[name]), tuple(pwm[name])) for i, name in enumerate(order)]


@dataclass
class DegradedPlan:
    groups: list[tuple]                         # (group, PWM group, its entry, entry mask of the group, enables)
    mappings: list[tuple[int, int, str]]        # (mapping word, bus groups, how it was derived)


def check(cfg: Config, rep: Reporter, repo: Path | None = None) -> DegradedPlan | None:
    """Checks safety.output_groups and safety.degraded; derives the values of the supervisor for a project with a
    mapping (None otherwise, and nothing is derived)."""
    safety = cfg.project.safety
    if safety is None or (not safety.output_groups and not safety.degraded):
        return None
    repo = Path(repo if repo is not None else cfg.running_tree)
    try:
        facts = read_facts(repo)
    except ValueError as e:
        rep.error(PROJECT, ("safety", "degraded"), f"degraded reaction (SYS-117) cannot be checked: {e}")
        return None
    cfg.code_inputs.extend(f for f in facts.files if f not in cfg.code_inputs)
    errors = len([m for m in rep.messages if m.severity == "error"])

    groups = _groups(cfg, rep, facts)
    if not safety.degraded:
        rep.warning(PROJECT, ("safety", "output_groups"),
                    "safe-output groups without a degraded mapping (safety.degraded) have no effect: every event "
                    "keeps its fail-safe reaction")
        return None
    if not safety.output_groups:
        rep.error(PROJECT, ("safety", "degraded"),
                  "a degraded mapping needs safe-output groups: list them under safety.output_groups (a PWM group "
                  "each)")
        return None
    if cfg.integration.modules.get(SUPERVISOR_MODULE) is None:
        rep.error(PROJECT, ("safety", "degraded"),
                  f"a degraded mapping needs the safe supervisor (module {SUPERVISOR_MODULE} of the family port), "
                  f"which decides the reaction; the project has none")
        return None
    if cfg.diag is None or cfg.diag.fault_memory is None:
        rep.error(PROJECT, ("safety", "degraded"),
                  "a degraded mapping needs the error memory: the degraded groups are reported through it "
                  "(diag.yaml fault_memory, a DTC that samples a port connected to safety.degraded)")
    mappings = _mappings(cfg, rep, facts, groups)
    _rte_use(cfg, rep)
    if len([m for m in rep.messages if m.severity == "error"]) > errors or groups is None or mappings is None:
        return None

    use = cfg.integration.modules[SUPERVISOR_MODULE]
    use.derived["degraded_groups"] = (len(groups), "number of safety.output_groups")
    for k, (name, pwm, entry, mask, enables) in enumerate(groups):
        how = (f"safety.output_groups[{k}] {name}: PWM group {pwm} = entry {entry} of the safe outputs table "
               f"(tools/bswgen: the used PWM groups first, in board order)")
        if enables:
            how += (f"; IO-45: its enables {', '.join(enables)} (board pwm[].gate_driver) = entries "
                    f"{', '.join(str(i) for i in range(TABLE_BITS) if (mask >> i) & 1 and i != entry)}")
        use.derived[f"degraded_group_{k}_entries"] = (mask, how)
    use.derived["degraded_mappings"] = (len(mappings), "number of safety.degraded")
    for n, (word, bus_groups, how) in enumerate(mappings):
        use.derived[f"degraded_mapping_{n}"] = (word, how)
        use.derived[f"degraded_mapping_{n}_bus_groups"] = (
            bus_groups, "groups of every device on the same bus if all of them are mapped, else 0 (a fault on the "
                        "lost bus stays fail-safe)")
    return DegradedPlan(groups, mappings)


def _groups(cfg: Config, rep: Reporter, facts: SupervisorFacts) -> list[tuple] | None:
    """(group, PWM group, entry of the PWM group, entry mask of the group, bound enables) per safety.output_groups
    entry; None after an error. IO-45: the mask holds the PWM group's entry and the entries of the safe_off outputs of
    the devices bound to that PWM group (board pwm[].gate_driver)."""
    safety = cfg.project.safety
    used = [grp for grp, _ in cfg.selected_pwm()]
    index = {grp.name: i for i, grp in enumerate(used)}
    instance = {grp.name: grp.instance for grp in used}
    result, ok = [], True
    names: dict[str, int] = {}
    owner: dict[str, int] = {}
    timers: dict[str, int] = {}
    extra: dict[int, int] = {}              # IO-45: output group -> entry bits of its bound enables
    bound: dict[int, list[str]] = {}        # IO-45: output group -> `device.connection` of those enables
    if len(safety.output_groups) > facts.max_groups:
        rep.error(PROJECT, ("safety", "output_groups"),
                  f"{len(safety.output_groups)} safe-output groups, the safe supervisor holds at most "
                  f"{facts.max_groups} (safeSupervisor/Degraded.h MAX_GROUPS)")
        ok = False
    for i, group in enumerate(safety.output_groups):
        where = ("safety", "output_groups", i)
        if group.name in names:
            rep.error(PROJECT, (*where, "name"), f"group {group.name} is listed twice (also safety.output_groups"
                                                 f"[{names[group.name]}])")
            ok = False
            continue
        names[group.name] = i
        if group.pwm not in index:
            known = ", ".join(index) or "none"
            rep.error(PROJECT, (*where, "pwm"), f"{group.pwm} is no PWM group the project uses (used: {known})")
            ok = False
            continue
        if group.pwm in owner:
            rep.error(PROJECT, (*where, "pwm"), f"PWM group {group.pwm} is already in safe-output group "
                                                f"{safety.output_groups[owner[group.pwm]].name}: an output belongs to "
                                                f"one group at most")
            ok = False
            continue
        owner[group.pwm] = i
        timer = instance[group.pwm]
        if timer in timers:
            other = safety.output_groups[timers[timer]]
            rep.error(PROJECT, (*where, "pwm"),
                      f"PWM group {group.pwm} runs on {timer} like {other.pwm} of group {other.name}: groups on one "
                      f"timer are not independent - forcing one group rewrites the output control of the other "
                      f"(concept section 4.3); put them into one group or on different timers")
            ok = False
            continue
        timers[timer] = i
        if index[group.pwm] >= TABLE_BITS:
            rep.error(PROJECT, (*where, "pwm"), f"PWM group {group.pwm} is entry {index[group.pwm]} of the safe "
                                                f"outputs table, the degraded walk reaches entries 0..{TABLE_BITS - 1}")
            ok = False
            continue
        result.append((group.name, group.pwm, index[group.pwm]))
    if safety.output_groups:
        missing = [name for name in index if name not in owner]
        if missing:
            rep.error(PROJECT, ("safety", "output_groups"),
                      f"every PWM group the project uses belongs to exactly one safe-output group; missing: "
                      f"{', '.join(missing)}")
            ok = False
        # IO-45: a safe_off output of a device bound to a PWM group (board pwm[].gate_driver - the enable of its
        # gate driver) joins the safe-output group of that PWM group (concept section 4.3, decision C2 (a)): the
        # degraded walk forces it together with the channels, and its driver is locked with the group
        # (integration: the enable lock of the gate driver). An output bound to PWM groups of two safe-output groups
        # would belong to two groups and is refused; an output bound to none goes off with S1 only.
        loose = []
        for entry in safe_off_entries(cfg):
            groups = sorted({owner[p] for p in entry.pwm if p in owner})
            if len(groups) > 1:
                names = " and ".join(safety.output_groups[g].name for g in groups)
                rep.error(PROJECT, ("safety", "output_groups"),
                          f"{entry.output} ({', '.join(entry.users)}) is the enable of PWM groups in the safe-output "
                          f"groups {names}: an output belongs to one group at most")
                ok = False
            elif not groups:
                loose += entry.users
            elif entry.index >= TABLE_BITS:
                rep.error(PROJECT, ("safety", "output_groups", groups[0]),
                          f"{entry.output} ({', '.join(entry.users)}) is entry {entry.index} of the safe outputs "
                          f"table, the degraded walk reaches entries 0..{TABLE_BITS - 1}")
                ok = False
            else:
                extra[groups[0]] = extra.get(groups[0], 0) | (1 << entry.index)
                bound[groups[0]] = bound.get(groups[0], []) + list(entry.users)
        if loose:
            rep.warning(PROJECT, ("safety", "output_groups"),
                        f"the safe_off outputs {', '.join(loose)} belong to no safe-output group (their device drives "
                        f"no PWM group of the project, board pwm[].gate_driver): the degraded reaction does not "
                        f"switch them, they go off with S1 only")
    if not ok:
        return None
    return [(name, pwm, entry, (1 << entry) | extra.get(owner[pwm], 0), tuple(bound.get(owner[pwm], ())))
            for name, pwm, entry in result]


def _mappings(cfg: Config, rep: Reporter, facts: SupervisorFacts, groups) -> list[tuple[int, int, str]] | None:
    """(word, bus groups, how) per safety.degraded entry; None after an error."""
    safety = cfg.project.safety
    # the names as listed (first place of each), so that a faulty group is reported once, at the group
    bit: dict[str, int] = {}
    for group in safety.output_groups:
        bit.setdefault(group.name, len(bit))
    all_groups = (1 << len(groups)) - 1 if groups else 0
    ids, buses = instances(cfg), bus_ids(cfg)
    rows, ok = [], True
    seen: dict[tuple[str, str | None], int] = {}
    if len(safety.degraded) > facts.max_mappings:
        rep.error(PROJECT, ("safety", "degraded"),
                  f"{len(safety.degraded)} degraded mappings, the safe supervisor holds at most {facts.max_mappings} "
                  f"(safeSupervisor/Degraded.h MAX_MAPPINGS)")
        ok = False
    for n, entry in enumerate(safety.degraded):
        where = ("safety", "degraded", n)
        if entry.event not in facts.events and entry.event not in facts.reports:
            rep.error(PROJECT, (*where, "event"), f"{entry.event} is no event of the safe supervisor")
            ok = False
            continue
        if entry.event not in facts.degradable and entry.event not in facts.reports:
            closed = ", ".join((*facts.degradable, *facts.reports))
            rep.error(PROJECT, (*where, "event"),
                      f"{entry.event} is fail-safe only and cannot be degraded: the safe supervisor degrades only the "
                      f"events of its closed list ({closed}; safeSupervisor/Degraded.h). Faults of "
                      f"the MCU itself - memory, image, program flow, watchdog, clock, supply, kernel - put the "
                      f"supervision of the remaining function in doubt (partner-and-degraded.md section 4.2)")
            ok = False
            continue
        device = None
        if entry.event in DEVICE_EVENTS:
            if entry.device is None:
                rep.error(PROJECT, where, f"{entry.event} needs `device`: the board device whose faults are degraded")
                ok = False
                continue
            if entry.device not in ids:
                known = ", ".join(ids) or "none"
                rep.error(PROJECT, (*where, "device"),
                          f"{entry.device} is no board device the project integrates (integrated: {known})")
                ok = False
                continue
            if ids[entry.device] > 0xFF:
                rep.error(PROJECT, (*where, "device"), f"{entry.device} is device instance {ids[entry.device]}, the "
                                                       f"context word of a device report holds 0..255")
                ok = False
                continue
            device = entry.device
        elif entry.device is not None:
            rep.error(PROJECT, (*where, "device"), f"{entry.event} carries no device; leave `device` out")
            ok = False
            continue
        key = (entry.event, device)
        if key in seen:
            rep.error(PROJECT, (*where, "device") if device else (*where, "event"),
                      f"{entry.event}{' of ' + device if device else ''} is already mapped by safety.degraded"
                      f"[{seen[key]}]")
            ok = False
            continue
        seen[key] = n
        if not entry.groups:
            rep.error(PROJECT, (*where, "groups"), f"{entry.event} degrades no group: name at least one safe-output "
                                                   f"group (without one the event would switch nothing off)")
            ok = False
            continue
        unknown = [g for g in entry.groups if g not in bit]
        if unknown:
            known = ", ".join(bit) or "none"
            rep.error(PROJECT, (*where, "groups"), f"unknown safe-output group {', '.join(unknown)} "
                                                   f"(safety.output_groups: {known})")
            ok = False
            continue
        if len(set(entry.groups)) != len(entry.groups):
            rep.error(PROJECT, (*where, "groups"), "a group is listed twice")
            ok = False
            continue
        mask = sum(1 << bit[g] for g in entry.groups)
        if entry.event in facts.reports:
            # SYS-135: HW_SHUTOFF - the report, not the project, says which path tripped; the mapping names its group
            path = _hw_shutoff_path(cfg, rep, where, entry.groups)
            if path is None:
                ok = False
                continue
            timer, pwm, names = path
            report = facts.reports[entry.event]
            how = (f"safety.degraded[{n}]: report {entry.event} ({report}) << 24 | timer {timer} (PWM group {pwm}, "
                   f"hardware threshold {', '.join(names)}) << 16 | no bus 0x{NO_BUS:02X} << 8 | groups "
                   f"{entry.groups[0]} 0x{mask:02X}")
            if groups and mask == all_groups:
                rep.warning(PROJECT, (*where, "groups"),
                            f"{entry.event} degrades every safe-output group: a trip of the path becomes S1 without "
                            f"a reset at once (nothing is left to continue), as without the mapping")
            rows.append([n, entry.event, None, NO_BUS, mask, (report << 24) | (timer << 16) | (NO_BUS << 8) | mask,
                         how])
            continue
        if groups and mask == all_groups:
            rep.warning(PROJECT, (*where, "groups"),
                        f"{entry.event}{' of ' + device if device else ''} degrades every safe-output group: the "
                        f"reaction becomes S1 without a reset at once (nothing is left to continue), instead of "
                        f"the fail-safe reaction with its reset")
        event = facts.events[entry.event]
        instance = ids[device] if device else 0
        bus = buses[device] if device else NO_BUS
        how = (f"safety.degraded[{n}]: event {entry.event} ({event}) << 24 | "
               + (f"device {device} instance {instance} << 16 | bus 0x{bus:02X} << 8 | " if device else "")
               + f"groups {'+'.join(entry.groups)} 0x{mask:02X}")
        rows.append([n, entry.event, device, bus, mask, (event << 24) | (instance << 16) | (bus << 8) | mask, how])
    if not ok:
        return None
    result = []
    for n, event, device, bus, mask, word, how in rows:
        bus_groups = 0
        if device is not None and bus != NO_BUS:
            on_bus = [d for d, b in buses.items() if b == bus]
            mapped = {r[2]: r[4] for r in rows if r[1] == event and r[2] is not None}
            unmapped = [d for d in on_bus if d not in mapped]
            if unmapped:
                rep.warning(PROJECT, ("safety", "degraded", n, "device"),
                            f"a fault of {device} while its bus is lost stays fail-safe: the devices "
                            f"{', '.join(unmapped)} on the same bus are not mapped, and a lost bus is a common cause "
                            f"of all its devices")
            else:
                for d in on_bus:
                    bus_groups |= mapped[d]
        result.append((word, bus_groups, how))
    return result


def _hw_shutoff_path(cfg: Config, rep: Reporter, where: tuple, groups: list[str]) -> tuple | None:
    """SYS-135 (safety review SYS-128/SYS-130 C1): (timer index, PWM group, names of its hardware thresholds) of the
    hardware shut-off path whose safe-output group a HW_SHUTOFF mapping names; None after an error with its YAML path.

    The mapping carries the timer as instance: the family's report names the timer of the tripped path in bits 31..24
    of its context word (s32k1xx hwThreshold::timerOf: the FTM number, the digits of the timer instance). Exactly one
    group - the one of that path's PWM group, whose outputs the path forces in hardware: SD degrades only it."""
    if len(groups) != 1:
        rep.error(PROJECT, (*where, "groups"),
                  f"{HW_SHUTOFF} degrades only the group of the tripped path: name exactly that one group (the "
                  f"group of the PWM group its hardware threshold switches off), not {len(groups)}")
        return None
    pwm = next((g.pwm for g in cfg.project.safety.output_groups if g.name == groups[0]), None)
    thresholds = getattr(cfg.project, "hw_thresholds", None) or {}
    instance = {grp.name: grp.instance for grp, _ in cfg.selected_pwm()}
    names = [name for name, t in thresholds.items() if t.pwm == pwm]
    if not names:
        rep.error(PROJECT, (*where, "groups"),
                  f"group {groups[0]} (PWM group {pwm}) has no hardware shut-off path: no entry of hw_thresholds "
                  f"switches {pwm} off, so no {HW_SHUTOFF} report can name it")
        return None
    timers = sorted({instance[t.pwm] for t in thresholds.values() if t.pwm in instance})
    if len(timers) > 1:
        rep.error(PROJECT, (*where, "event"),
                  f"{HW_SHUTOFF} can be degraded only while every hardware threshold of the project acts on one timer "
                  f"(here: {', '.join(timers)}): the safe supervisor keeps the reports of all paths in one word "
                  f"(safeSupervisor/HwShutoff.h), so the trip of a second path may add no new bit and stay without "
                  f"its degraded reaction")
        return None
    number = re.search(r"(\d+)$", instance.get(pwm, ""))
    if number is None or int(number.group(1)) > 0xFF:
        rep.error(PROJECT, (*where, "groups"),
                  f"the timer {instance.get(pwm)} of PWM group {pwm} carries no instance number 0..255, which the "
                  f"report of its hardware shut-off path names")
        return None
    return int(number.group(1)), pwm, names


def _rte_use(cfg: Config, rep: Reporter):
    """Warning when no port reads the degraded groups: the application would not learn that a group is off."""
    rte = cfg.rte
    if rte is None:
        return
    targets = []
    for value in rte.connections.values():
        targets += value if isinstance(value, list) else [value]
    if not any(str(t).startswith(("safety.degraded", "safety.qlf.degraded")) for t in targets):
        rep.warning(PROJECT, ("safety", "degraded"),
                    "no port of the ECU description reads safety.degraded: the application does not learn which "
                    "safe-output groups are degraded, and no DTC can sample it (connect a required port to "
                    "safety.degraded or safety.degraded.<GROUP>)")


__all__ = ["HW_SHUTOFF", "SupervisorFacts", "DegradedPlan", "SafeOffEntry", "read_facts", "check", "bus_ids", "instances",
           "safe_off_outputs", "safe_off_entries"]
