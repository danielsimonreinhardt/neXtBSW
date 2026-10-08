# SPDX-License-Identifier: Apache-2.0
"""Hardware thresholds (SYS-86): a board signal at a comparator input forces a PWM group off without software.

Concept: doc/safety/hardware-mechanisms.md section 2.2 (the comparator's output reaches a fault input of the PWM
timer; s32k1xx: CMP0 -> TRGMUX -> FTMx fault input). The three levels:

  derivative  comparators[]              DAC resolution, references, electrical limits, reachable fault inputs
  board       comparators.inputs[]       which signal is at which comparator input; `signal` names the board ADC
                                         entry whose circuit (scale or sensor) is in front of the pin
              comparators.supply         voltage of the supply reference (s32k1xx VDDA)
  project     hw_thresholds.<name>       threshold in the unit of the signal, direction, reference, PWM group,
                                         glitch filter

Derived here (the generator only turns it into register values): the pin voltage of the threshold through the
inverse of the signal's characteristic, the DAC level rounded towards the safe side (an `above` threshold trips at
or below the configured value, a `below` threshold at or above it), whether the comparator output must be inverted
(a falling characteristic or `below`), the fault input of the group's timer, the filter in timer clocks, the band
of the real threshold from the data sheet limits and the worst-case reaction time. Recovery is manual only: the
outputs stay forced until a reset (the latching of every software path, SM-40).

Checks, each with YAML path:
  - the comparator input exists on the board, its comparator in the derivative, the input number exists  error
  - the pin carries <instance>_IN<n> in the pin multiplexing table and is no debug pin (JTAG/SWD)       error
  - the pin has no other user of the project than an ADC channel (analog, shared)                         error
  - the signal is a fitted board ADC entry with a linear characteristic                                     error
  - the PWM group is used by the project, the comparator reaches a fault input of its timer                error
  - one threshold per comparator                                                                           error
  - the reference is offered by the derivative (and, for `supply`, stated by the board)                    error
  - the threshold lies inside the DAC range of the reference                                               error
  - the filter fits the timer's fault filter                                                               error
  - characteristic, supply rail, board input or threshold not confirmed (verify, no source)              warning
"""

from dataclasses import dataclass
from fractions import Fraction
from math import ceil, floor

from .config import Config, Reporter
from .model import DerivativeComparator

# Fault input path of the PWM timer per family (register knowledge, like checks.PWM_COUNTERS): largest filter value
# and the timer clocks between the edge at the fault input and the forced outputs. s32k1xx: [RM13] 47.4.3.19
# FLTCTRL[FFVAL] (4 bit, S. 1392) and Table 47-15 "FTM Fault Input Filter Delay" (S. 1476) with FLTPS = 0:
# 3 rising edges of the FTM input clock without filter, (4 + FFVAL) with filter.


@dataclass(frozen=True)
class FaultPath:
    filter_max_ticks: int
    ticks_unfiltered: int
    ticks_filtered_base: int

    def delay_ticks(self, filter_ticks: int) -> int:
        return self.ticks_unfiltered if filter_ticks == 0 else self.ticks_filtered_base + filter_ticks


FAULT_PATHS = {"s32k1xx": FaultPath(filter_max_ticks=15, ticks_unfiltered=3, ticks_filtered_base=4)}
DEBUG_SIGNAL_PREFIXES = ("JTAG_", "SWD_")


@dataclass(frozen=True)
class HwThresholdUse:
    """Checked hardware threshold: what the family generator needs, and what `nxconfig check` prints."""

    name: str
    input: str                      # board comparator input
    instance: str                   # comparator of the derivative (CMP0)
    input_index: int                # multiplexer input IN<n>
    pin: str
    signal: str                     # board ADC entry of the circuit
    unit: str                       # unit of the threshold
    trip: str                       # above/below of the signal
    pin_trip: str                   # above/below of the pin voltage (a falling characteristic swaps)
    threshold: int
    reference: str                  # supply/bandgap
    vin_mv: Fraction                # DAC reference voltage
    dac_bits: int
    dac_level: int                  # VOSEL: DAC output = vin * (level + 1) / 2^bits
    pin_mv: Fraction                # pin voltage of the configured threshold
    dac_mv: Fraction                # DAC output voltage = realised switching point at the pin
    realised: Fraction              # realised threshold in the unit of the signal
    band: tuple[Fraction, Fraction]  # realised threshold with offset, INL and reference tolerance (unit)
    supply_tolerance_open: bool     # reference `supply`: its tolerance is not part of the band
    pwm: str                        # PWM group
    timer: str                      # its timer instance
    fault_input: int                # fault input of the timer
    filter_ns: int
    filter_ticks: int               # timer clocks of the fault filter (0: none)
    timer_hz: Fraction | None       # timer input clock (None: no clock plan)
    reaction_max_ns: Fraction | None  # comparator + fault path, worst case (None: no clock plan)
    propagation_max_ns: int
    settling_max_us: int
    confirmed: bool                 # characteristic, rail, board input and project value all confirmed


def _silent_pinmux(cfg: Config):
    from .checks import pinmux          # late: checks imports this module
    return pinmux(cfg, Reporter())


def _claims(cfg: Config):
    from .checks import _resources      # late: checks imports this module
    return _resources(cfg, Reporter())


def _characteristic(cfg: Config, index: int, ch):
    """(a, b, d, vref, max_raw, unit, confirmed) of a linear board ADC entry: value = (a * raw + b) / d, else None."""
    from . import sensor
    channel = sensor._Channel(cfg, Reporter(), index, ch)
    if channel.vref is None or channel.max_raw is None:
        return None
    result = channel.coefficients()
    if result is None:
        return None
    kind, unit, a, b, c, d, *_rest, confirmed = result
    if c != 0 or a == 0:
        return None
    if d < 0:
        a, b, d = -a, -b, -d
    return a, b, d, channel.vref, channel.max_raw, unit, confirmed


def _rail_mv(cfg: Config, ref) -> tuple[Fraction | None, bool]:
    if ref.mv is not None:
        return Fraction(ref.mv), True
    rail = next((r for r in cfg.board.rails if r.name == ref.rail), None)
    if rail is None:
        return None, False
    return Fraction(rail.mv * ref.ratio_permille, 1000), rail.source is not None and not rail.verify


def check(cfg: Config, rep: Reporter) -> list[HwThresholdUse]:
    """Hardware thresholds of the project; entries with errors are left out (the messages say why)."""
    wanted = cfg.project.hw_thresholds
    if not wanted:
        return []
    board_cmp = cfg.board.comparators
    inputs = {inp.name: (i, inp) for i, inp in enumerate(board_cmp.inputs)} if board_cmp is not None else {}
    comparators: dict[str, DerivativeComparator] = {c.name: c for c in cfg.derivative.comparators}
    adc = {ch.name: (i, ch) for i, ch in enumerate(cfg.board.adc)}
    used_pwm = {grp.name: grp for grp, _ in cfg.selected_pwm()}
    derivative = cfg.derivative.derivative.name
    family = cfg.derivative.derivative.family
    table = _silent_pinmux(cfg)
    claims = None
    per_comparator: dict[str, str] = {}
    result = []
    for name, th in wanted.items():
        base = ("hw_thresholds", name)

        def error(path, text, _base=base, _name=name):
            rep.error("project", _base + tuple(path), f"hardware threshold {_name}: {text}")

        def board_error(path, text, _name=name):
            rep.error("board", ("comparators",) + tuple(path), f"hardware threshold {_name}: {text}")

        if th.input not in inputs:
            error(("input",), f"board {cfg.board.board.name} has no comparator input {th.input} (comparators.inputs: "
                              f"{', '.join(inputs) or '-'})")
            continue
        i, inp = inputs[th.input]
        bpath = ("inputs", i)
        comparator = comparators.get(inp.instance)
        if comparator is None:
            board_error(bpath + ("instance",), f"derivative {derivative} describes no comparator {inp.instance} "
                                               f"(mcudb comparators: {', '.join(comparators) or '-'})")
            continue
        if inp.input >= comparator.inputs:
            board_error(bpath + ("input",), f"{inp.instance} has the inputs IN0 .. IN{comparator.inputs - 1}, not "
                                            f"IN{inp.input}")
            continue
        signal_name = f"{inp.instance}_IN{inp.input}"
        if table is None:
            board_error(bpath + ("pin",), f"package {cfg.board.board.package} of {derivative} has no pin "
                                          f"multiplexing table; the pin of {signal_name} cannot be checked")
            continue
        if inp.pin not in table.signals.get(signal_name, {}):
            pins = ", ".join(table.signals.get(signal_name, {})) or "none in this package"
            board_error(bpath + ("pin",), f"pin {inp.pin} is no {signal_name} (pin multiplexing of "
                                          f"{cfg.board.board.package}: {pins})")
            continue
        debug = sorted(s for s, pins in table.signals.items()
                       if s.startswith(DEBUG_SIGNAL_PREFIXES) and inp.pin in pins)
        if debug:
            board_error(bpath + ("pin",), f"pin {inp.pin} is a debug interface pin ({', '.join(debug)}); the family "
                                          f"code does not take a debug pin away from the probe")
            continue
        if inp.instance in per_comparator:
            error(("input",), f"comparator {inp.instance} already serves hardware threshold "
                              f"{per_comparator[inp.instance]}; one comparator has one DAC and one output")
            continue
        per_comparator[inp.instance] = name

        if claims is None:
            claims = _claims(cfg)
        claim = claims.get(f"pin {inp.pin}")
        if claim is not None and not claim.owner.startswith("adc "):
            error(("input",), f"pin {inp.pin} of comparator input {inp.name} is used by {claim.owner}; a comparator "
                              f"input shares its pin only with an ADC channel")
            continue

        if inp.signal not in adc:
            board_error(bpath + ("signal",), f"board has no ADC entry {inp.signal} (adc[].name); the signal names the "
                                             f"circuit in front of the comparator pin")
            continue
        ai, ach = adc[inp.signal]
        if not ach.fitted:
            board_error(bpath + ("signal",), f"ADC entry {inp.signal} marks its circuit as not fitted (fitted: "
                                             f"false): the pin carries no quantity")
            continue
        char = _characteristic(cfg, ai, ach)
        if char is None:
            board_error(bpath + ("signal",), f"ADC entry {inp.signal} has no linear characteristic (a pull-up or a "
                                             f"characteristic with an error, see adc[{ai}]); a threshold needs the "
                                             f"inverse of a linear one")
            continue
        a, b, d, vref, max_raw, unit, signal_confirmed = char

        grp = used_pwm.get(th.pwm)
        if grp is None:
            error(("pwm",), f"PWM group {th.pwm} is not used by the project (pwm: {', '.join(used_pwm) or '-'})")
            continue
        routes = comparator.fault_inputs.get(grp.instance, [])
        if not routes:
            error(("pwm",), f"the output of {inp.instance} reaches no fault input of {grp.instance}, the timer of "
                            f"PWM group {grp.name} (mcudb comparators[{inp.instance}].fault_inputs: "
                            f"{', '.join(comparator.fault_inputs) or '-'})")
            continue
        path = FAULT_PATHS.get(family)
        if path is None:
            error(("pwm",), f"the family {family} has no fault input model (hwthreshold.FAULT_PATHS)")
            continue

        # reference of the DAC
        supply_open = False
        if th.reference == "supply":
            if comparator.supply_reference is None:
                error(("reference",), f"{inp.instance} of {derivative} offers no supply reference")
                continue
            if board_cmp.supply is None:
                board_error(("supply",), f"hardware threshold {name} uses the supply reference: the board must state "
                                         f"the voltage at {comparator.supply_reference} (comparators.supply)")
                continue
            vin, rail_confirmed = _rail_mv(cfg, board_cmp.supply)
            if vin is None:
                board_error(("supply", "rail"), f"unknown rail {board_cmp.supply.rail} (board rails: "
                                                f"{', '.join(r.name for r in cfg.board.rails) or 'none'})")
                continue
            if not rail_confirmed:
                rep.warning("board", ("comparators", "supply"),
                            f"hardware threshold {name}: the supply reference ({comparator.supply_reference}) is not "
                            f"confirmed (rail without source or verify: true); the threshold moves with it")
            supply_open = True
            ref_low = ref_high = Fraction(1)
        else:
            if comparator.bandgap_mv is None:
                error(("reference",), f"{inp.instance} of {derivative} offers no bandgap reference")
                continue
            vin = Fraction(comparator.bandgap_mv)
            ref_low = Fraction(comparator.bandgap_min_mv, comparator.bandgap_mv)
            ref_high = Fraction(comparator.bandgap_max_mv, comparator.bandgap_mv)

        # threshold -> pin voltage: value = (a * raw + b) / d with raw = U * max_raw / vref
        slope = Fraction(a * max_raw, d * vref)            # unit per mV at the pin
        offset = Fraction(b, d)

        def value(mv: Fraction) -> Fraction:
            return slope * mv + offset

        pin_mv = (Fraction(th.threshold) - offset) / slope
        pin_trip = th.trip if slope > 0 else ("below" if th.trip == "above" else "above")
        levels = 1 << comparator.dac_bits
        lsb = vin / levels
        if pin_mv > vin or pin_mv <= 0:
            error(("threshold",), f"{th.threshold} {unit} is {float(pin_mv):.1f} mV at pin {inp.pin}, outside the range "
                                  f"of the {th.reference} reference ({float(lsb):.2f} .. {float(vin):.0f} mV); choose "
                                  f"the other reference or another threshold")
            continue
        steps = pin_mv / lsb
        level = (floor(steps) if pin_trip == "above" else ceil(steps)) - 1
        if level < 0 or level >= levels:
            error(("threshold",), f"{th.threshold} {unit} is {float(pin_mv):.2f} mV at pin {inp.pin}, below the lowest "
                                  f"DAC level {float(lsb):.2f} mV of the {th.reference} reference")
            continue
        dac_mv = lsb * (level + 1)

        # band of the real switching point: comparator offset, DAC non-linearity, reference tolerance (bandgap)
        spread = Fraction(comparator.offset_max_mv) + lsb * Fraction(comparator.dac_inl_max_millilsb, 1000)
        low_mv, high_mv = dac_mv * ref_low - spread, dac_mv * ref_high + spread
        band = tuple(sorted((value(low_mv), value(high_mv))))

        # fault filter of the timer
        timer_hz = cfg.clock.counter_hz(grp.instance) if cfg.clock is not None else None
        filter_ticks = 0
        if th.filter_ns > 0:
            if timer_hz is None:
                error(("filter_ns",), f"the timer clock of {grp.instance} is unknown (no clock plan), the filter "
                                      f"cannot be derived")
                continue
            filter_ticks = ceil(Fraction(th.filter_ns) * timer_hz / 10**9)
            if filter_ticks > path.filter_max_ticks:
                most = floor(Fraction(path.filter_max_ticks * 10**9) / timer_hz)
                error(("filter_ns",), f"{th.filter_ns} ns are {filter_ticks} clocks of {grp.instance} "
                                      f"({float(timer_hz) / 1e6:g} MHz); its fault filter holds at most "
                                      f"{path.filter_max_ticks} (at most {most} ns)")
                continue
        reaction = None
        if timer_hz is not None:
            reaction = comparator.propagation_max_ns + Fraction(path.delay_ticks(filter_ticks) * 10**9) / timer_hz

        confirmed = signal_confirmed and not inp.verify and not th.verify
        if not signal_confirmed:
            rep.warning("board", ("adc", ai), f"hardware threshold {name}: the characteristic of {inp.signal} is not "
                                              f"confirmed (verify: true or no source); the threshold rests on it")
        if inp.verify:
            rep.warning("board", ("comparators",) + bpath, f"hardware threshold {name}: comparator input {inp.name} "
                                                           f"is marked verify: true")
        result.append(HwThresholdUse(
            name, inp.name, inp.instance, inp.input, inp.pin, inp.signal, unit, th.trip, pin_trip, th.threshold,
            th.reference, vin, comparator.dac_bits, level, pin_mv, dac_mv, value(dac_mv), band, supply_open, grp.name,
            grp.instance, routes[0], th.filter_ns, filter_ticks, timer_hz, reaction, comparator.propagation_max_ns,
            comparator.settling_max_us, confirmed))
    return result


def info(cfg: Config) -> list[str]:
    """One readable line per hardware threshold (nxconfig check, not a message)."""
    lines = []
    for t in getattr(cfg, "hw_thresholds", None) or []:
        reaction = f"{float(t.reaction_max_ns):.0f} ns" if t.reaction_max_ns is not None else "unknown (no clock plan)"
        tol = " plus the tolerance of the supply" if t.supply_tolerance_open else ""
        lines.append(f"hardware threshold {t.name}: {t.signal} {t.trip} {t.threshold} {t.unit} -> {t.instance} "
                     f"IN{t.input_index} ({t.pin}), DAC level {t.dac_level} of {1 << t.dac_bits} on the "
                     f"{t.reference} reference ({float(t.vin_mv):g} mV): switches at {float(t.dac_mv):.1f} mV = "
                     f"{float(t.realised):.0f} {t.unit} (band {float(t.band[0]):.0f} .. {float(t.band[1]):.0f} "
                     f"{t.unit}{tol}); forces PWM group {t.pwm} ({t.timer} fault input {t.fault_input}) inactive, "
                     f"latched until reset; reaction at most {reaction}")
    return lines
