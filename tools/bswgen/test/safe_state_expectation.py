"""SYS-116: independent expectation of the extended safe outputs table (nxSafeOutputsSelect) and of the safe state
groups of a project - read from project.yaml, board.yaml and the module manifests with the rules of
doc/safety/partner-and-degraded.md part C, not from the generator.

Builds on safe_outputs_expectation.expected() (the four words of every entry, register addresses from the reference
manual) and adds per entry:
- the group: k (1-based, board order of the used PWM groups) for the software output control entry of PWM group k and
  for every safe_off output of the device the board names as that group's gate driver (`pwm[].gate_driver`); 0 else;
- the clock gate of the group's timer (PCC of its FTM, CGC) for an entry of a group, 0/0 otherwise;
- the value in active short low / high: for the timer entry CHnOC of every channel (bits 7..0) plus CHnOCV (bits
  15..8) of the low-side resp. high-side channels ([RM] 47.5.15); for a bound enable 0 (left as it is); the freewheel
  value where the group does not allow that short or the entry belongs to no group.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import safe_outputs_expectation as base

STATE_BIT = {"freewheel": 0, "active_short_low": 1, "active_short_high": 2}


@dataclass(frozen=True)
class SelectEntry:
    words: tuple[int, int, int, int, int, int, int, int, int]
    what: str


@dataclass(frozen=True)
class Group:
    name: str
    allowed: int
    test: int
    test_us: int


def expected(project_file: Path) -> tuple[list[SelectEntry], list[Group]] | None:
    """None if no used PWM group allows a short (the project keeps the plain table)."""
    project, board, _ = base.board_of(Path(project_file))
    used = [g for g in board.get("pwm") or [] if g["name"] in (project.get("pwm") or {})]
    uses = {g["name"]: project["pwm"][g["name"]] for g in used}
    if not any(s != "freewheel" for u in uses.values() for s in u.get("safe_states") or []):
        return None
    plain = base.expected(project_file)
    groups = []
    for g in used:
        states = uses[g["name"]].get("safe_states") or ["freewheel"]
        test = bool(uses[g["name"]].get("safe_state_test"))
        groups.append(Group(g["name"], sum(1 << STATE_BIT[s] for s in states), int(test),
                            uses[g["name"]].get("safe_state_test_us", 0) if test else 0))
    entries: list[SelectEntry] = []
    for k, g in enumerate(used, start=1):
        e = plain[k - 1]
        states = uses[g["name"]].get("safe_states") or []
        channel = {c["name"]: c["channel"] for c in g["channels"]}
        low = sum(1 << channel[p["low_side"]] for p in g["pairs"])
        high = sum(1 << channel[p["high_side"]] for p in g["pairs"])
        entries.append(SelectEntry((*e.words, k, e.gate, e.gate_mask,
                                    e.value | (low << 8) if "active_short_low" in states else e.value,
                                    e.value | (high << 8) if "active_short_high" in states else e.value), e.what))
    # safe_off block: which device each output belongs to, in the order of the plain expectation
    driver_of = {g["gate_driver"]: (k, g) for k, g in enumerate(used, start=1) if g.get("gate_driver")}
    owners = []
    seen = set()
    outputs_used = project.get("use", {}).get("dio")
    for dev in board.get("devices") or []:
        module = dev.get("module")
        if not module:
            continue
        for key, spec in (base.manifest_of(module).get("connect") or {}).items():
            if not (spec or {}).get("safe_off") or key not in (dev.get("connect") or {}):
                continue
            name = dev["connect"][key]
            if (outputs_used != "all" and name not in (outputs_used or [])) or name in seen:
                continue
            seen.add(name)
            owners.append(dev["name"])
    for e, owner in zip(plain[len(used):], owners, strict=True):
        if owner in driver_of:
            k, g = driver_of[owner]
            states = uses[g["name"]].get("safe_states") or []
            timer_gate = plain[k - 1].gate, plain[k - 1].gate_mask
            entries.append(SelectEntry((*e.words, k, *timer_gate, 0 if "active_short_low" in states else e.value,
                                        0 if "active_short_high" in states else e.value), e.what))
        else:
            entries.append(SelectEntry((*e.words, 0, 0, 0, e.value, e.value), e.what))
    return entries, groups
