# SPDX-License-Identifier: Apache-2.0
"""SYS-116: the safe states of a PWM group - freewheel and the active short - project values and checks.

doc/safety/partner-and-degraded.md part C (decisions C1-C6 of the user, 2026-09-29, each in its recommended variant).
Per used PWM group the project lists the safe states the application may select at run time (`pwm.<group>.safe_states`
out of `freewheel`, `active_short_low`, `active_short_high`); every switch-off path - the fault handlers, the halt
deadman, S1, the degraded reaction SD - applies the selection through the safe outputs table (tools/bswgen: the value
sets per entry, platforms/<family>/hardFaultHandler: the walks). Anything that makes the selection untrustworthy or
unavailable ends in freewheel.

Configuration levels (architecture section 4):
  derivative/family  whether the PWM timer can force the short with single stores - register knowledge of the
                     family's generator (SHORT_FAMILIES below: s32k1xx FTM software output control)
  board              pwm[].bridge_reset_state (what the bridge does while the MCU is in reset),
                     pwm[].high_side_hold (the high side can stay on - no bootstrap supply)
  project            pwm.<group>.safe_states, safe_state_test, safe_state_test_us

Code facts that gate the active short (read from the tree like the closed list of degraded.py; a project cannot
override them). The short is refused until both exist, because without them a short would be applied where the
concept forbids it or would not be held:
  - decision C4: every event that indicates a bridge fault forces freewheel - `NX_SAFE_BRIDGE_FAULT_CLASSES(X)` of
    the safe supervisor (ecu/safety/safeSupervisor/include/safeSupervisor/Degraded.h), the device fault classes the
    supervisor answers with freewheel before it walks the table;
  - decision C2: the gate-driver enable of a group in a short stays on, the halt deadman's guard and the group lock
    keep the short - `NX_SAFE_STATE_SHORT_HOLD` of the family port's integration
    (platforms/<family>/port/main/src/integration/SafeStateHold.h).
SYS-140 (C4) and SYS-139 (C2) define both. The facts are read more closely than "defined": the list of the supervisor
must name at least OUTPUT, SUPPLY and INTERNAL (a project cannot shorten it, and neither can a code change unnoticed),
and the hold is `NX_SAFE_STATE_SHORT_HOLD(X) X(<module>) ...` - the modules whose safe_off enable the family port keeps
on in the short; an enable of any other module bound to a group with a short is refused (its adapter would switch it
off in the first driver cycle of S1).

Derived for the safe supervisor (integration/safeSupervisorConfiguration.h, NX_MODULE_SAFESUPERVISOR_DERIVED_*), only
for a project in which a group allows a short - every other project gets not one value more:
  bridge_fault_devices       number of devices bound to a PWM group with a short (board pwm[].gate_driver)
  bridge_fault_device_<n>    device instance << 8 | selection groups (bit g = the g-th used PWM group), the words
                             safeSupervisor/Degraded.h (namespace bridge) reads; the instance is the one of degraded.py

Checks, each with its YAML path:
  - `freewheel` listed, no state twice                                                          error
  - the family's generator can force the short (SHORT_FAMILIES)                                  error
  - the group is complementary and every pair has a low-side channel                             error
  - `active_short_high` only with board `high_side_hold: true`                                   error
  - every safe_off output bound to the group (IO-45) belongs to that group only                  error
  - the code facts above exist                                                                   error
  - SYS-140: the enable bound to the group belongs to a module the family port keeps on           error
  - SYS-140: the project integrates the safe supervisor, at most 8 bound devices                  error
  - no gate driver bound to the group (an unbound enable would cut the short)                    warning
  - board `bridge_reset_state` not stated or freewheel (C3: freewheel during reset/restart)      warning
  - a short without `safe_state_test` (latent fault, C5)                                         warning
  - `safe_state_test` without a short, without `safe_state_test_us`                              error
  - `safe_state_test_us` without `safe_state_test`                                               warning

Qt free; every finding names its YAML path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import REPO, Config, Reporter

PROJECT, BOARD = "project", "board"

FREEWHEEL, SHORT_LOW, SHORT_HIGH = "freewheel", "active_short_low", "active_short_high"
SHORTS = (SHORT_LOW, SHORT_HIGH)
# Bit of a state in the allowed mask of the generated table (hardFaultHandler/safeStateSelection.h NX_SAFE_STATE_*)
STATE_BIT = {FREEWHEEL: 0, SHORT_LOW: 1, SHORT_HIGH: 2}

# Families whose generator builds the short with single stores of the safe outputs table (register knowledge):
# s32k1xx - FTM software output control SWOCTRL, CHnOC for every channel of the group and CHnOCV for the channels of
# one side, [RM] 47.5.15; it acts before dead-time insertion, output mask and polarity (tools/bswgen
# collect_safe_outputs), so the transition from PWM or freewheel to the short passes the timer's dead time.
SHORT_FAMILIES = {"s32k1xx": "FTM software output control (SWOCTRL), before dead-time insertion"}

BRIDGE_FAULT_HEADER = Path("ecu/safety/safeSupervisor/include/safeSupervisor/Degraded.h")
BRIDGE_FAULT_MACRO = "NX_SAFE_BRIDGE_FAULT_CLASSES"
SHORT_HOLD_HEADER = "platforms/{family}/port/main/src/integration/SafeStateHold.h"
SHORT_HOLD_MACRO = "NX_SAFE_STATE_SHORT_HOLD"
# SYS-140: the classes decision C4 names (partner-and-degraded.md 5.9); the supervisor's list must contain them all
BRIDGE_FAULT_CLASSES = ("OUTPUT", "SUPPLY", "INTERNAL")
# SYS-140: largest number of devices bound to a PWM group with a short (safeSupervisor/Degraded.h bridge::MAX_DEVICES)
BRIDGE_MAX_DEVICES = 8
BRIDGE_GROUP_BITS = 8               # selection groups of a derived device word (bits 7..0)
SUPERVISOR_MODULE = "safeSupervisor"


@dataclass(frozen=True)
class CodeFacts:
    """What the tree's code offers for the active short: missing facts as (file, macro, what it stands for).
    `hold_modules`: the modules of NX_SAFE_STATE_SHORT_HOLD(X) - None when not read (tests pass facts directly), then
    the enable of every module counts as kept."""

    missing: tuple[tuple[str, str, str], ...]
    files: tuple[Path, ...] = ()
    hold_modules: tuple[str, ...] | None = None


def _macro_list(text: str, macro: str) -> tuple[str, ...] | None:
    """The X(<name>) entries of `#define <macro>(X) ...` (continuation lines joined); None if it is not defined so."""
    joined = re.sub(r"\\\r?\n", " ", text)
    found = re.search(rf"^\s*#\s*define\s+{macro}\(X\)(.*)$", joined, re.M)
    if found is None:
        return None
    return tuple(re.findall(r"\bX\((\w+)\)", found.group(1)))


def read_facts(repo: Path, family: str) -> CodeFacts:
    """Reads the two code facts that gate the short (module docstring)."""
    missing, files = [], []
    hold_modules: tuple[str, ...] | None = ()
    checks = ((BRIDGE_FAULT_HEADER, BRIDGE_FAULT_MACRO,
               "decision C4: the safe supervisor forces freewheel for a bridge fault (DEVICE_FAULT of the classes "
               "OUTPUT, SUPPLY, INTERNAL, failed start test) before it walks the table"),
              (Path(SHORT_HOLD_HEADER.format(family=family)), SHORT_HOLD_MACRO,
               "decision C2: the family port keeps the gate-driver enable of a shorted group on in S1 and in the halt "
               "deadman's guard, and its PWM group lock keeps the short instead of rewriting freewheel"))
    for rel, macro, what in checks:
        path = Path(repo) / rel
        try:
            text = path.read_text(encoding="utf-8")
            files.append(path)
        except OSError:
            text = ""
        if not re.search(rf"^\s*#\s*define\s+{macro}\b", text, re.M):
            missing.append((rel.as_posix(), macro, what))
            continue
        listed = _macro_list(text, macro)
        if macro == BRIDGE_FAULT_MACRO:
            # SYS-140: defined is not enough - the list must name every class of decision C4
            lacking = [c for c in BRIDGE_FAULT_CLASSES if listed is None or c not in listed]
            if lacking:
                missing.append((rel.as_posix(), macro,
                                f"{what}; the list lacks {', '.join(lacking)} - it must name at least "
                                f"{', '.join(BRIDGE_FAULT_CLASSES)}"))
        else:
            # SYS-140: the modules whose bound enable the port keeps on; the plain form keeps none
            hold_modules = listed if listed is not None else ()
    return CodeFacts(tuple(missing), tuple(files), hold_modules)


@dataclass
class GroupPlan:
    """Safe states of one used PWM group, in the order of Config.selected_pwm() (= its selection word)."""

    name: str
    allowed: tuple[str, ...]            # always starts with freewheel
    test: bool
    test_us: int | None
    bound_outputs: tuple[str, ...] = ()  # dio outputs of the safe_off block bound to the group (IO-45)

    @property
    def allowed_mask(self) -> int:
        return sum(1 << STATE_BIT[s] for s in self.allowed)

    @property
    def shorts(self) -> tuple[str, ...]:
        return tuple(s for s in self.allowed if s in SHORTS)


@dataclass
class SafeStatePlan:
    groups: list[GroupPlan] = field(default_factory=list)

    @property
    def select(self) -> bool:
        """True if any group allows a short - only then does the table get the value sets (object code of every
        other project stays as it was)."""
        return any(g.shorts for g in self.groups)


def check(cfg: Config, rep: Reporter, repo: Path | None = None, facts: CodeFacts | None = None) -> SafeStatePlan:
    """Checks pwm.<group>.safe_states and the start test; returns the plan (freewheel only for every group without
    the key). `facts` replaces the code facts read from `repo` (tests)."""
    plan = SafeStatePlan()
    used = cfg.selected_pwm()
    if not used:
        return plan
    family = cfg.derivative.derivative.family
    bound = _bound_outputs(cfg)
    for grp, use in used:
        where = ("pwm", grp.name)
        states = list(use.safe_states)
        ok = True
        if states and FREEWHEEL not in states:
            rep.error(PROJECT, (*where, "safe_states"),
                      "freewheel must be listed: it is the state after a reset, the fallback for an invalid or "
                      "unavailable selection and the state the hardware fault input forces")
            ok = False
        doubles = sorted({s for s in states if states.count(s) > 1})
        if doubles:
            rep.error(PROJECT, (*where, "safe_states"), f"listed twice: {', '.join(doubles)}")
            ok = False
        shorts = [s for s in SHORTS if s in states]
        if shorts and not _short_possible(cfg, rep, grp, use, family, shorts, bound.get(grp.name, ()), repo, facts):
            ok = False
        _start_test(rep, grp, use, shorts)
        allowed = (FREEWHEEL, *shorts) if ok else (FREEWHEEL,)
        plan.groups.append(GroupPlan(grp.name, allowed, bool(use.safe_state_test and ok and shorts),
                                     use.safe_state_test_us, tuple(bound.get(grp.name, ()))))
    _derive_bridge(cfg, rep, plan)               # SYS-140: the supervisor's bridge devices
    return plan


def _bound_outputs(cfg: Config) -> dict[str, list[str]]:
    """IO-45: dio outputs of the safe_off block per PWM group they are bound to (board pwm[].gate_driver)."""
    from .degraded import safe_off_entries      # the same order and binding rule as the degraded walk
    result: dict[str, list[str]] = {}
    for entry in safe_off_entries(cfg):
        for group in entry.pwm:
            result.setdefault(group, []).append(entry.output)
    return result


def _short_possible(cfg: Config, rep: Reporter, grp, use, family: str, shorts: list[str], bound: tuple[str, ...],
                    repo: Path | None, facts: CodeFacts | None) -> bool:
    where = ("pwm", grp.name, "safe_states")
    ok = True
    if family not in SHORT_FAMILIES:
        rep.error(PROJECT, where, f"the generator of family {family} cannot force an active short with single stores "
                                  f"of the safe outputs table (families that can: {', '.join(SHORT_FAMILIES)}); "
                                  f"allowed here: freewheel only")
        return False
    if grp.mode != "complementary":
        rep.error(PROJECT, where, f"an active short needs a complementary group (board pwm {grp.name} mode "
                                  f"{grp.mode})")
        ok = False
    channels = {c.name for c in grp.channels}
    missing = [p.name for p in grp.pairs if p.low_side not in channels or p.high_side not in channels]
    if missing:
        rep.error(PROJECT, where, f"an active short needs a high-side and a low-side channel in every pair; "
                                  f"incomplete: {', '.join(missing)}")
        ok = False
    if SHORT_HIGH in shorts and not grp.high_side_hold:
        rep.error(PROJECT, where, f"active_short_high needs a high side that can stay on (board pwm {grp.name} "
                                  f"high_side_hold: true); a bootstrap-fed high side discharges and drifts into a "
                                  f"partial short")
        ok = False
    # IO-45: an enable bound to the group stays on in the short; it must not be the enable of anything else too
    from .degraded import safe_off_entries
    for entry in safe_off_entries(cfg):
        if grp.name in entry.pwm and (len(entry.pwm) > 1 or len(entry.users) > 1):
            rep.error(PROJECT, where, f"{entry.output} ({', '.join(entry.users)}) is left on in the active short of "
                                      f"{grp.name}, but it is also the enable of "
                                      f"{', '.join(p for p in entry.pwm if p != grp.name) or 'another connection'}: "
                                      f"an enable of a group with an active short belongs to that group alone")
            ok = False
    if grp.gate_driver is None:
        rep.warning(PROJECT, where, f"board pwm {grp.name} names no gate driver (gate_driver): a safe_off enable of "
                                    f"the bridge that is not bound to the group goes off in every switch-off path and "
                                    f"turns the short into freewheel")
    elif not bound:
        rep.warning(PROJECT, where, f"the gate driver {grp.gate_driver} of {grp.name} has no safe_off enable in the "
                                    f"project: nothing keeps the gates enabled but the gate driver itself")
    reset_state = grp.bridge_reset_state
    if reset_state is None or reset_state == FREEWHEEL:
        said = "not stated" if reset_state is None else "freewheel"
        rep.warning(PROJECT, where, f"the bridge's reset state is {said} (board pwm {grp.name} bridge_reset_state): "
                                    f"every reaction with a reset ends the short, the bridge freewheels during reset "
                                    f"and restart - confirm that this is tolerable for the item (decision C3, AoU-33)")
    if facts is None:
        facts = read_facts(Path(repo if repo is not None else getattr(cfg, "running_tree", None) or REPO), family)
        cfg.code_inputs.extend(f for f in facts.files if f not in cfg.code_inputs)
    for rel, macro, what in facts.missing:
        rep.error(PROJECT, where, f"the active short cannot be used in this tree yet: {rel} does not define {macro} "
                                  f"({what}; follow-up of SYS-116). Allowed until then: freewheel only")
        ok = False
    if facts.hold_modules is not None and not facts.missing:
        # SYS-140 (C2): only the family port's adapter of a listed module keeps its enable on in S1
        modules = {d.name: d.module for d in cfg.integration.devices}
        for entry in safe_off_entries(cfg):
            if grp.name not in entry.pwm:
                continue
            foreign = sorted({modules.get(u.split(".", 1)[0], "?") for u in entry.users} - set(facts.hold_modules))
            if foreign:
                rep.error(PROJECT, where, f"{entry.output} ({', '.join(entry.users)}) is the enable of {grp.name}, but "
                                          f"the family port keeps only the enable of "
                                          f"{', '.join(facts.hold_modules) or 'no module'} on in the active short "
                                          f"({SHORT_HOLD_MACRO}); the adapter of {', '.join(foreign)} would switch it "
                                          f"off in the first driver cycle of S1 and turn the short into freewheel")
                ok = False
    return ok


def _derive_bridge(cfg: Config, rep: Reporter, plan: SafeStatePlan):
    """SYS-140 (decision C4): the devices bound to a PWM group with a short, for the safe supervisor's removal of the
    short on a bridge fault (module docstring). Nothing for a project without a short."""
    if not plan.select:
        return
    from .degraded import instances              # the device instance of the context word (SYS-117 rule)
    shorted = [g for g in plan.groups if g.shorts]
    where = ("pwm", shorted[0].name, "safe_states")
    use = cfg.integration.modules.get(SUPERVISOR_MODULE)
    if use is None:
        rep.error(PROJECT, where, f"an active short needs the safe supervisor (module {SUPERVISOR_MODULE} of the "
                                  f"family port): it removes the short on a bridge fault before it walks the table "
                                  f"(decision C4); the project has none")
        return
    index = {g.name: k for k, g in enumerate(plan.groups)}
    instance = instances(cfg)
    words: list[tuple[int, str]] = []
    for device in cfg.integration.devices:
        group = getattr(device, "pwm", None)
        if group is None or group not in {g.name for g in shorted}:
            continue
        if index[group] >= BRIDGE_GROUP_BITS:
            rep.error(PROJECT, ("pwm", group, "safe_states"),
                      f"the safe supervisor names at most {BRIDGE_GROUP_BITS} PWM groups per bridge device; {group} "
                      f"is group {index[group]}")
            return
        words.append(((instance[device.name] << 8) | (1 << index[group]),
                      f"{device.name} (device instance {instance[device.name]}, board pwm {group}.gate_driver) -> "
                      f"selection group {index[group]} ({group}): a DEVICE_FAULT of the classes "
                      f"{', '.join(BRIDGE_FAULT_CLASSES)} removes its short"))
    if len(words) > BRIDGE_MAX_DEVICES:
        rep.error(PROJECT, where, f"at most {BRIDGE_MAX_DEVICES} devices may be bound to PWM groups with an active "
                                  f"short (safe supervisor, bridge::MAX_DEVICES); the project has {len(words)}")
        return
    use.derived["bridge_fault_devices"] = (len(words), "devices bound to a PWM group with an active short (SYS-140, "
                                                       "decision C4)")
    for n, (word, how) in enumerate(words):
        use.derived[f"bridge_fault_device_{n}"] = (word, how)


def _start_test(rep: Reporter, grp, use, shorts: list[str]):
    where = ("pwm", grp.name)
    if use.safe_state_test:
        if not shorts:
            rep.error(PROJECT, (*where, "safe_state_test"), "the start test builds an active short: list "
                                                            "active_short_low or active_short_high in safe_states")
        if use.safe_state_test_us is None:
            rep.error(PROJECT, (*where, "safe_state_test_us"), "the start test needs its duration "
                                                               "(safe_state_test_us)")
    elif shorts:
        rep.warning(PROJECT, (*where, "safe_state_test"),
                    "an active short that is never built is a latent fault (selection, forced pattern, gate driver, "
                    "low-side switches); enable the start test (safe_state_test, decision C5) or state why not")
        if use.safe_state_test_us is not None:
            rep.warning(PROJECT, (*where, "safe_state_test_us"), "has no effect without safe_state_test")
    elif use.safe_state_test_us is not None:
        rep.warning(PROJECT, (*where, "safe_state_test_us"), "has no effect without safe_state_test")
