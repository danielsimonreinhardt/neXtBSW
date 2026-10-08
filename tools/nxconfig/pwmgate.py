# SPDX-License-Identifier: Apache-2.0
"""SYS-95 (finding DFA-4 (a) of doc/safety/dfa.md, DF-47): a PWM group whose power stage only the PWM timer itself
switches off.

The safe outputs table de-energises a PWM group in every switch-off path (S1, the fault handlers, the halt deadman,
the degraded reaction) by writing the timer's software output control. The register check (SYS-24, class CLOCK_GATE of
SM-22) detects a PWM timer whose clock gate was switched off and raises S1 - but with the gate off the timer's registers
do not take the write, and its pins stay where they froze: the fault disables exactly the part of the reaction it
triggers. The reaction still de-energises the power stage if a `safe_off` output on another peripheral (the enable of
the gate driver on a GPIO port) is bound to the group (board `pwm[].gate_driver`, IO-45); without one the stage-1
argument for that group rests on the integrator's hardware alone (AoU-07: the power stage has its own enable or a
defined state when its inputs freeze).

Check, with its YAML path:
  - a used PWM group without a `safe_off` output bound to it                                     warning

A warning, not an error: a board may de-energise the bridge by other means the configuration does not know (a gate
driver with an input watchdog, an external enable from the SBC). The message names what to do and the assumption
the project then relies on. Whether the walk of the safe outputs table should open the clock gate before it writes
the timer is decision point (b) of DFA-4 for SYS-30 (the reference manual has to say whether an FTM output follows
the software output control right after the gate is opened); this check does not depend on it.

Configuration levels: nothing new - the binding is a board fact (pwm[].gate_driver), `safe_off` a fact of the module
manifest, the use of the group and of the device the project's. Qt free; every finding names its YAML path.
"""

from __future__ import annotations

from .config import Config, Reporter

PROJECT = "project"


def unprotected_groups(cfg: Config) -> list[str]:
    """Used PWM groups of the project that no `safe_off` output is bound to (degraded.safe_off_entries, IO-45)."""
    from .degraded import safe_off_entries      # the same binding rule as the safe outputs table and the walk
    bound = {group for entry in safe_off_entries(cfg) for group in entry.pwm}
    return [grp.name for grp, _ in cfg.selected_pwm() if grp.name not in bound]


def check(cfg: Config, rep: Reporter) -> list[str]:
    """Warns for every used PWM group without a bound `safe_off` output; returns their names."""
    if cfg.integration is None:
        return []
    groups = unprotected_groups(cfg)
    for name in groups:
        grp = next(g for g, _ in cfg.selected_pwm() if g.name == name)
        how = (f"board pwm {name} names no gate driver (gate_driver)" if grp.gate_driver is None
               else f"its gate driver {grp.gate_driver} has no safe_off output in the project")
        rep.warning(PROJECT, ("pwm", name),
                    f"no safe_off output on another peripheral de-energises the power stage of {name} ({how}): a "
                    f"switched-off clock gate of {grp.instance} freezes its pins and also disables the PWM part of "
                    f"the reaction it triggers (DFA-4, doc/safety/dfa.md) - bind the enable of the bridge to the group "
                    f"(board pwm[].gate_driver and a safe_off connection of its module) or state that the power stage "
                    f"has its own enable or a defined state when its inputs freeze (AoU-07 of the safety manual)")
    return groups


__all__ = ["check", "unprotected_groups"]
