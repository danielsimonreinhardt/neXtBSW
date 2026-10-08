"""Power stage of a complementary PWM group: minimum dead time and diagnostic values from generic facts (IO-24).

Decision of the user 2026-09-16: the influence of the switching transistors is described generically - the board
names facts of its stage (board.yaml pwm[].power_stage, model.PowerStage), no component is known here. Levels:

  board    pwm[].power_stage       switch, gate driver, gate resistors, short-circuit detection (facts, source/verify)
  project  pwm.<group>             deadtime_ns (decision), deadtime_margin_ns (decision)

Minimum dead time (doc/changelog.d/IO-24.md, section for doc/architecture):

  t_off     = (t_d(off) + t_f) * max(1, k_off)        switch still conducting after its turn-off command
  t_on      =  t_d(on)         * min(1, k_on)         the other switch starts conducting
  k_x       = (R_G,int + R_x,board) / (R_G,int + R_G,test)   gate resistance of the board against the data sheet test
  t_min     = max(0, t_off - t_on) + max(0, t_drv,off - t_drv,on) + margin

The resistance ratio is applied only in the direction that lengthens the dead time: a scaling law for a data sheet
time is an estimate, and an estimate must not shorten the required minimum. Without gate resistance values the times
of the data sheet are used as they are (warning).

Diagnostics derived from the same facts:

  short-circuit current   I = V_DS,threshold / R_DS(on)   (range from R_DS(on) at 25 degC and hot, R_DS(on) in uOhm)
  blanking                blanking >= (t_d(on) + t_r) * max(1, k_on), otherwise every turn-on may be taken as a short
  gate charge current     I_G = Q_G * f_PWM per switch (supply of the gate driver, bootstrap)

Where the threshold comes from (IO-35): a detector fixed by the circuit is a board fact (power_stage.short_circuit);
a detector whose threshold is a setting of the gate driver is a project decision, a parameter of the driver module
that its manifest marks with `power_stage` (vds_threshold[_high_side|_low_side]_mv, blanking_ns) - the instance is
the board device linked by pwm[].gate_driver. A parameter value <= 0 means the device keeps its own setting; then
nothing is derived. A threshold parameter means "trips at or below this drain-source voltage", so the derived
current is the highest current the detection lets through (typical detector level, spread of the driver not included).

What is reported:
- missing facts: warning with the YAML path of the missing group or value; nothing is assumed in their place,
- facts without `source` or with `verify: true`: warning, the result counts as unconfirmed,
- dead time of the project below the minimum: error if every fact used is confirmed, otherwise warning,
- no margin chosen by the project: warning.
"""

from dataclasses import dataclass
from fractions import Fraction
from math import ceil, floor

from .config import Config, Reporter

INT32_MAX = (1 << 31) - 1
SIDES = {"vds_threshold_mv": "both sides", "vds_threshold_high_side_mv": "high side",
         "vds_threshold_low_side_mv": "low side"}


@dataclass(frozen=True)
class DriverSetting:
    """A power stage parameter of the gate driver instance of a PWM group (module.yaml `power_stage`, IO-35)."""

    device: str                                     # board device (instance of the gate driver module)
    parameter: str                                  # module parameter, project path modules.<device>.<parameter>
    kind: str                                       # model.POWER_STAGE_PARAMETERS
    value: int                                      # project value (default filled in); <= 0: device keeps its own
    short_circuit_min_ma: int | None = None         # threshold at R_DS(on) hot (None: not derived)
    short_circuit_max_ma: int | None = None         # threshold at R_DS(on) 25 degC


@dataclass(frozen=True)
class PowerStagePlan:
    """Derived values of one used PWM group; a value is None when the facts for it are missing."""

    group: str
    deadtime_ns: int                                # project decision
    margin_ns: int | None                           # project decision, None: not chosen
    switch_term_ns: int | None                      # max(0, t_off - t_on)
    driver_term_ns: int | None                      # max(0, t_drv,off - t_drv,on)
    min_deadtime_ns: int | None                     # switch + driver + margin
    confirmed: bool                                 # every fact of the minimum has a source and no verify flag
    turn_on_ns: int | None                          # (t_d(on) + t_r) scaled, for the blanking check
    short_circuit_min_ma: int | None                # at R_DS(on) hot (None: no hot value)
    short_circuit_max_ma: int | None                # at R_DS(on) 25 degC
    gate_current_ua: int | None                     # Q_G * f_PWM per switch
    driver_settings: tuple[DriverSetting, ...] = ()  # power stage parameters of the linked gate driver (IO-35)
    r_ds_on_uohm: int | None = None                 # R_DS(on) at 25 degC used for the currents
    r_ds_on_hot_uohm: int | None = None             # R_DS(on) hot
    vds_threshold_mv: int | None = None             # board detector (power_stage.short_circuit), None: none


def _confirmed(facts) -> bool:
    return facts is not None and facts.source is not None and not facts.verify


def _ratio(stage, direction: str) -> Fraction | None:
    """Gate resistance of the board against the data sheet test circuit for one direction; None: unknown."""
    sw, gate = stage.switch, stage.gate
    if sw is None or gate is None or sw.test_gate_resistance_mohm is None:
        return None
    board = getattr(gate, f"turn_{direction}_resistance_mohm")
    if board is None:
        return None
    internal = sw.internal_gate_resistance_mohm or 0
    if internal + sw.test_gate_resistance_mohm == 0:
        return None
    return Fraction(internal + board, internal + sw.test_gate_resistance_mohm)


def _missing(facts, names) -> list[str]:
    return [n for n in names if facts is None or getattr(facts, n) is None]


def _group(cfg: Config, rep: Reporter, index: int, grp, use) -> PowerStagePlan:
    base = ("pwm", index, "power_stage")
    where = ("pwm", grp.name)
    stage = grp.power_stage
    if stage is None:
        rep.warning("board", ("pwm", index), f"PWM group {grp.name} has no power_stage: nothing about its switches "
                    f"and gate driver is confirmed, the minimum dead time is not derived and pwm.{grp.name}.deadtime_ns "
                    "of the project is not checked")
        return PowerStagePlan(grp.name, use.deadtime_ns, use.deadtime_margin_ns, None, None, None, False,
                              None, None, None, None)

    sw, drv = stage.switch, stage.driver
    switch_needed = ("turn_on_delay_ns", "turn_off_delay_ns", "fall_time_ns")
    driver_needed = ("turn_on_delay_ns", "turn_off_delay_ns")
    for key, facts, needed, what in (("switch", sw, switch_needed, "switching times of the transistor"),
                                     ("driver", drv, driver_needed, "propagation delays of the gate driver")):
        missing = _missing(facts, needed)
        if missing:
            path = base + (key,) if facts is not None else base
            rep.warning("board", path, f"PWM group {grp.name}: {what} unknown (power_stage.{key}: "
                        f"{', '.join(missing)}), the minimum dead time is not derived")

    k_on, k_off = _ratio(stage, "on"), _ratio(stage, "off")
    if stage.gate is None:
        rep.warning("board", base, f"PWM group {grp.name}: gate resistance of the board unknown (power_stage.gate), "
                    "the switching times of the data sheet are used as measured in its test circuit")
    elif sw is not None and (k_on is None or k_off is None):
        missing = _missing(stage.gate, ("turn_on_resistance_mohm", "turn_off_resistance_mohm"))
        if sw.test_gate_resistance_mohm is None:
            missing.append("switch.test_gate_resistance_mohm")
        rep.warning("board", base + ("gate",), f"PWM group {grp.name}: switching times not scaled to the gate "
                    f"resistance of the board ({', '.join(missing) or 'test and internal gate resistance are 0'})")

    switch_term = driver_term = minimum = None
    if not _missing(sw, switch_needed):
        t_off = Fraction(sw.turn_off_delay_ns + sw.fall_time_ns) * max(Fraction(1), k_off or Fraction(1))
        t_on = Fraction(sw.turn_on_delay_ns) * min(Fraction(1), k_on or Fraction(1))
        switch_term = max(0, ceil(t_off - t_on))
    if not _missing(drv, driver_needed):
        driver_term = max(0, drv.turn_off_delay_ns - drv.turn_on_delay_ns)
    used = [("switch", sw), ("driver", drv)] + ([("gate", stage.gate)] if k_on is not None or k_off is not None else [])
    confirmed = all(_confirmed(facts) for _, facts in used)
    if switch_term is not None and driver_term is not None:
        minimum = switch_term + driver_term + (use.deadtime_margin_ns or 0)
        unconfirmed = [key for key, facts in used if not _confirmed(facts)]
        if unconfirmed:
            rep.warning("board", base + (unconfirmed[0],), f"PWM group {grp.name}: the minimum dead time of "
                        f"{minimum} ns rests on unconfirmed facts (power_stage {', '.join(unconfirmed)}: "
                        "no source or verify: true)")
        if use.deadtime_margin_ns is None:
            rep.warning("project", where, f"pwm.{grp.name}: no deadtime_margin_ns chosen, the minimum dead time of "
                        f"{minimum} ns contains no margin for the spread of the switching times")
        if use.deadtime_ns < minimum:
            text = (f"pwm.{grp.name}.deadtime_ns {use.deadtime_ns} ns is below the minimum dead time of {minimum} ns "
                    f"the power stage needs (switch {switch_term} ns + driver {driver_term} ns + margin "
                    f"{use.deadtime_margin_ns or 0} ns): both switches of a half bridge may conduct at once")
            (rep.error if confirmed else rep.warning)("project", where + ("deadtime_ns",), text)

    turn_on = None
    if sw is not None and sw.turn_on_delay_ns is not None and sw.rise_time_ns is not None:
        turn_on = ceil(Fraction(sw.turn_on_delay_ns + sw.rise_time_ns) * max(Fraction(1), k_on or Fraction(1)))
    r_25 = sw.r_ds_on if sw is not None else None
    r_hot = sw.r_ds_on_hot if sw is not None else None
    sc_min = sc_max = None
    sc = stage.short_circuit
    if sc is not None and sc.vds_threshold_mv is not None:
        if not r_25:
            rep.warning("board", base + ("short_circuit", "vds_threshold_mv"), f"PWM group {grp.name}: the "
                        "short-circuit current is not derived, power_stage.switch.r_ds_on_uohm is unknown")
        else:
            sc_min, sc_max = _currents(sc.vds_threshold_mv, r_25, r_hot)
    if sc is not None and sc.blanking_ns is not None:
        _blanking(rep, "board", base + ("short_circuit", "blanking_ns"), grp.name, "short-circuit blanking",
                  sc.blanking_ns, turn_on)
    settings = tuple(_driver_settings(cfg, rep, grp, index, sc, r_25, r_hot, turn_on))
    gate_current = None
    if sw is not None and sw.gate_charge_nc is not None:
        gate_current = min(INT32_MAX, ceil(Fraction(sw.gate_charge_nc * use.frequency_hz, 1000)))   # nC * Hz = nA

    return PowerStagePlan(grp.name, use.deadtime_ns, use.deadtime_margin_ns, switch_term, driver_term, minimum,
                          confirmed and minimum is not None, turn_on, sc_min, sc_max, gate_current, settings,
                          r_25, r_hot, sc.vds_threshold_mv if sc is not None else None)


def _currents(threshold_mv: int, r_25: int, r_hot: int | None) -> tuple[int | None, int]:
    """(current at R_DS(on) hot, current at 25 degC) in mA: mV / uOhm = kA; the hot resistance gives the lower."""
    def current(r):
        return min(INT32_MAX, threshold_mv * 1_000_000 // r)
    return (current(r_hot) if r_hot else None), current(r_25)


def _blanking(rep: Reporter, level: str, path, group: str, what: str, blanking_ns: int, turn_on: int | None):
    if turn_on is None:
        rep.warning(level, path, f"PWM group {group}: {what} not checked, power_stage.switch needs "
                    "turn_on_delay_ns and rise_time_ns")
    elif blanking_ns < turn_on:
        rep.warning(level, path, f"PWM group {group}: {what} of {blanking_ns} ns ends before the switch has turned "
                    f"on ({turn_on} ns): every turn-on may be detected as a short circuit")


def _driver_settings(cfg: Config, rep: Reporter, grp, index: int, sc, r_25, r_hot, turn_on):
    """IO-35: power stage parameters of the gate driver instance(s) linked to the group (board pwm[].gate_driver)."""
    integration = getattr(cfg, "integration", None)
    for dev in (integration.devices if integration is not None else []):
        manifest = cfg.modules.manifests.get(dev.module) if dev.pwm == grp.name else None
        for pname, spec in (manifest.parameters.items() if manifest is not None else ()):
            value = dev.parameters.get(pname)
            if spec.power_stage is None or not isinstance(value, int) or isinstance(value, bool):
                continue
            path = ("modules", dev.name, pname)
            lo = hi = None
            if value > 0 and spec.power_stage == "blanking_ns":
                _blanking(rep, "project", path, grp.name, f"short-circuit blanking of {dev.name} ({pname})",
                          value, turn_on)
            elif value > 0:
                if sc is not None and sc.vds_threshold_mv is not None:
                    rep.warning("project", path, f"PWM group {grp.name}: short-circuit detection stated twice, as a "
                                f"fixed detector of the board (pwm[{index}].power_stage.short_circuit) and as a "
                                f"setting of {dev.name} ({pname}): both are derived, the lower current trips first")
                if not r_25:
                    rep.warning("project", path, f"PWM group {grp.name}: the short-circuit current of {dev.name} "
                                f"({pname} {value} mV) is not derived, the board states no R_DS(on) "
                                f"(pwm[{index}].power_stage.switch.r_ds_on_uohm)")
                else:
                    lo, hi = _currents(value, r_25, r_hot)
            yield DriverSetting(dev.name, pname, spec.power_stage, value, lo, hi)


def _unlinked(cfg: Config, rep: Reporter):
    """IO-35: a power stage setting of a gate driver that drives no PWM group of the project derives nothing."""
    integration = getattr(cfg, "integration", None)
    for dev in (integration.devices if integration is not None else []):
        manifest = cfg.modules.manifests.get(dev.module)
        if dev.pwm is not None or manifest is None:
            continue
        for pname, spec in manifest.parameters.items():
            value = dev.parameters.get(pname)
            if spec.power_stage is not None and isinstance(value, int) and not isinstance(value, bool) and value > 0:
                rep.warning("project", ("modules", dev.name, pname), f"{dev.name} drives no PWM group of the project "
                            f"(board pwm[].gate_driver): {pname} {value} {spec.unit} is set, but nothing about the "
                            "power stage is known, the short-circuit current is not derived")


def _amps(ma: int) -> str:
    return f"{ma / 1000:.1f} A"


def _range(lo: int | None, hi: int, plan: "PowerStagePlan") -> str:
    r25 = f"R_DS(on) {plan.r_ds_on_uohm} µΩ at 25 °C"
    if lo is None:
        return f"{_amps(hi)} ({r25}; no hot R_DS(on), the current at high temperature is lower)"
    return f"{_amps(lo)} .. {_amps(hi)} ({r25}, {plan.r_ds_on_hot_uohm} µΩ hot)"


def info(cfg: Config) -> list[str]:
    """Readable summary of the short-circuit detection per used PWM group (nxconfig check, not a message)."""
    lines = []
    for plan in getattr(cfg, "power_stages", {}).values():
        if plan.vds_threshold_mv is not None:
            lines.append(f"PWM group {plan.group}: board short-circuit detector at {plan.vds_threshold_mv} mV trips at "
                         + (_range(plan.short_circuit_min_ma, plan.short_circuit_max_ma, plan)
                            if plan.short_circuit_max_ma is not None else "an unknown current (no R_DS(on))"))
        for s in plan.driver_settings:
            head = f"PWM group {plan.group}: {s.device} {s.parameter}"
            if s.value <= 0:
                lines.append(f"{head} not set, the device keeps its own setting (nothing derived)")
            elif s.kind == "blanking_ns":
                lines.append(f"{head} {s.value} ns, the switch has turned on after "
                             + (f"{plan.turn_on_ns} ns" if plan.turn_on_ns is not None else "an unknown time"))
            elif s.short_circuit_max_ma is not None:
                lines.append(f"{head} {s.value} mV ({SIDES[s.kind]}): trips at or below "
                             + _range(s.short_circuit_min_ma, s.short_circuit_max_ma, plan))
            else:
                lines.append(f"{head} {s.value} mV ({SIDES[s.kind]}): current not derived (no R_DS(on))")
    return lines


def check(cfg: Config, rep: Reporter) -> dict[str, PowerStagePlan]:
    """Power stage plans of the PWM groups the project uses, by group name."""
    index = {grp.name: i for i, grp in enumerate(cfg.board.pwm)}
    plans = {grp.name: _group(cfg, rep, index[grp.name], grp, use) for grp, use in cfg.selected_pwm()}
    _unlinked(cfg, rep)
    return plans
