"""Serialized ADC access: conversion time of one synchronous conversion and the bound of its polling loop
(module libs/bsp/bspAdcAccess, adc/AdcPollLimit.h, IO-14/IO-16/IO-19).

Nothing here is a value of a configuration level; everything is derived (section 4 "alles Abgeleitete berechnet der
Generator"):

  project  modules.bspAdcAccess    lock policy, lock timeout, safety factor, accepted interrupt suspension,
                                   sample phase, clock divider and hardware averaging of the converter
  derivat  instances.<ADC>         resolution_bits (compare phase) - manufacturer fact
  clock    clock.py ClockPlan      functional clock of the ADC instances, bus and core clock

- Family neutral: the split of a SAR conversion into sample + hold + compare + adder and the arithmetic of the
  polling bound (the same formulas the C++ header adc/AdcPollLimit.h implements, cross-checked by a static_assert
  against the generated numbers).
- Family model (registry MODELS): how many clocks the compare phase of a resolution takes, which dividers and
  averaging counts the converter has and which parts of the adder run in another clock domain. Register encodings
  stay in the generators (tools/bswgen).

The only check that can fail here is a comparison, not a computation: with the lock policy interrupt_lock a
conversion suspends the interrupts for its whole duration, so a conversion time above the interrupt suspension the
project accepts (max_interrupt_suspend_us) is reported with file, line and YAML path.
"""

from dataclasses import dataclass
from fractions import Fraction

from .config import Config, Reporter

MODULE = "bspAdcAccess"                             # module.yaml of the serialized access (project parameters)

# --- family s32k1xx (ADC12, reference manual "Conversion time"; also quoted in OpenBSW adc/Adc.h)
# Compare phase per resolution: CFG1[MODE] 8 bit 20, 10 bit 24, 12 bit 28 ADC clocks.
S32K1_COMPARE_CLOCKS = {8: 20, 10: 24, 12: 28}
S32K1_HOLD_CLOCKS = 1                               # hold phase of the sample/hold stage
S32K1_ADDER_ADC_CLOCKS = 5                          # single/first conversion adder, ADC clock domain
S32K1_ADDER_BUS_CLOCKS = 5                          # ... and its part in the bus clock domain
S32K1_CLOCK_DIVIDERS = (1, 2, 4, 8)                 # CFG1[ADIV]
S32K1_AVERAGES = (1, 4, 8, 16, 32)                  # SC3[AVGE] with AVGS


def _ceil_div(a: int, b: int) -> int:
    return -(-a // b)


def _saturate(value: int) -> int:
    return min(value, 0xFFFFFFFF)


@dataclass(frozen=True)
class AdcAccessPlan:
    """Derived timing of one synchronous conversion and the values the generators need."""

    family: str
    instances: tuple[str, ...]                      # ADC instances the bound covers, all at the same clock
    used: bool                                      # False: the project uses no ADC channel (bound unused)
    policy: str                                     # lock_policy of the project
    lock_timeout_ms: int
    safety_factor: int
    max_suspend_us: int
    resolution_bits: int
    sample_clocks: int
    hold_clocks: int
    compare_clocks: int
    adder_clocks: int
    adder_bus_clocks: int
    averages: int
    clock_divider: int
    adc_clock_hz: int                               # converter clock after the divider
    bus_clock_hz: int
    core_clock_hz: int
    conversion_clocks: int                          # ADC clock domain, averaging included
    conversion_ns: int                              # plus the adder part of the bus clock domain
    poll_cycles: int                                # iterations of the polling core for one conversion
    poll_limit: int                                 # poll_cycles * safety_factor

    @property
    def rtos_mutex(self) -> bool:
        return self.policy == "rtos_mutex"

    @property
    def suspend_ns(self) -> int:
        """Interrupt suspension one conversion causes: the whole conversion with interrupt_lock, and with the
        RTOS mutex the exclusive path of a caller that cannot wait (its own conversion plus the restart of the
        conversion it interrupted)."""
        return self.conversion_ns if self.policy == "interrupt_lock" else 2 * self.conversion_ns


def _clocks_to_ns(clocks: int, clock_hz: int) -> int:
    return _saturate(_ceil_div(clocks * 1000000000, clock_hz)) if clock_hz else 0xFFFFFFFF


def _poll_cycles(clocks: int, clock_hz: int, poll_hz: int) -> int:
    """Iterations of a loop at poll_hz that certainly cover `clocks` clocks at clock_hz (adc/AdcPollLimit.h)."""
    if clock_hz == 0:
        return 0xFFFFFFFF
    return max(1, _saturate(_ceil_div(clocks * poll_hz, clock_hz)))


def _instances(cfg: Config) -> tuple[list[str], bool]:
    """(ADC instances of the derivative the bound covers, whether the project uses any), in derivative order.

    A project without an ADC channel still compiles the access module, so the bound is derived for the
    instances of the derivative; nothing converts, so its value does not matter.
    """
    all_adc = [name for name, inst in cfg.derivative.instances.items() if inst.class_ == "adc"]
    used = {ch.instance for ch in cfg.selected("adc")} | {ch.instance for ch in cfg.project.adc_internal}
    selected = [name for name in all_adc if name in used]
    return (selected, True) if selected else (all_adc, False)


def _s32k1xx(cfg: Config, rep: Reporter, values: dict) -> AdcAccessPlan | None:
    plan, name = cfg.clock, cfg.derivative.derivative.name
    instances, used = _instances(cfg)
    if not instances:
        return None                                 # derivative without an ADC: no conversion to bound
    divider, averages = int(values["clock_divider"]), int(values["averages"])
    if divider not in S32K1_CLOCK_DIVIDERS:
        rep.error("project", ("modules", MODULE, "clock_divider"), f"clock_divider {divider}: the s32k1xx converter "
                  f"divides by {', '.join(str(d) for d in S32K1_CLOCK_DIVIDERS)} (CFG1[ADIV])")
        return None
    if averages not in S32K1_AVERAGES:
        rep.error("project", ("modules", MODULE, "averages"), f"averages {averages}: the s32k1xx converter averages "
                  f"over {', '.join(str(a) for a in S32K1_AVERAGES)} conversions (SC3[AVGE], 1: off)")
        return None

    bits = {inst: cfg.derivative.instances[inst].resolution_bits for inst in instances}
    if len(set(bits.values())) > 1:
        rep.error("derivative", ("instances", instances[0], "resolution_bits"),
                  f"{name}: the used ADC instances have different resolutions ("
                  + ", ".join(f"{i} {b} bit" for i, b in bits.items()) + "), one bound cannot cover them")
        return None
    resolution = next(iter(bits.values()))
    compare = S32K1_COMPARE_CLOCKS.get(resolution)
    if compare is None:
        rep.error("derivative", ("instances", instances[0], "resolution_bits"),
                  f"{name}: no compare phase known for {resolution} bit (s32k1xx: "
                  f"{', '.join(f'{b} bit' for b in S32K1_COMPARE_CLOCKS)})")
        return None

    if plan is None:
        return None                                 # no clock plan: the clock model reported it
    functional = {inst: plan.peripheral_hz.get(inst) for inst in instances}
    missing = [inst for inst, hz in functional.items() if hz is None]
    if missing:
        rep.error("derivative", ("instances", missing[0]),
                  f"{name}: no functional clock known for {', '.join(missing)} (clock model)")
        return None
    clocks = {inst: Fraction(hz, divider) for inst, hz in functional.items()}
    if len(set(clocks.values())) > 1:
        rep.error("project", ("modules", MODULE),
                  f"the used ADC instances run at different clocks ("
                  + ", ".join(f"{i} {float(c) / 1e6:g} MHz" for i, c in clocks.items())
                  + "): one polling bound cannot cover them")
        return None
    adc_hz = int(next(iter(clocks.values())))
    bus_hz, core_hz = int(plan.domains["bus"]), int(plan.domains["core"])

    sample = int(values["sample_clocks"])
    conversion_clocks = (sample + S32K1_HOLD_CLOCKS + compare + S32K1_ADDER_ADC_CLOCKS) * averages
    adder_bus = S32K1_ADDER_BUS_CLOCKS * averages
    conversion_ns = _clocks_to_ns(conversion_clocks, adc_hz) + _clocks_to_ns(adder_bus, bus_hz)
    poll_cycles = _poll_cycles(conversion_clocks, adc_hz, core_hz) + _poll_cycles(adder_bus, bus_hz, core_hz)
    factor = int(values["poll_safety_factor"])
    return AdcAccessPlan("s32k1xx", tuple(instances), used, str(values["lock_policy"]),
                         int(values["lock_timeout_ms"]), factor, int(values["max_interrupt_suspend_us"]),
                         resolution, sample, S32K1_HOLD_CLOCKS, compare, S32K1_ADDER_ADC_CLOCKS,
                         S32K1_ADDER_BUS_CLOCKS, averages, divider, adc_hz, bus_hz, core_hz, conversion_clocks,
                         conversion_ns, poll_cycles, _saturate(max(1, poll_cycles * factor)))


MODELS = {"s32k1xx": _s32k1xx}


def _suspension(plan: AdcAccessPlan, rep: Reporter):
    """The interrupt suspension of one conversion against what the project accepts."""
    limit_ns = plan.max_suspend_us * 1000
    if plan.suspend_ns <= limit_ns:
        return
    what = (f"a conversion of {plan.conversion_ns} ns suspends the interrupts for its whole duration"
            if not plan.rtos_mutex else
            f"a caller that cannot wait (ISR, interrupts suspended, before the scheduler) converts in an "
            f"exclusive section and restarts the conversion it interrupted, {plan.suspend_ns} ns for two "
            f"conversions of {plan.conversion_ns} ns")
    advice = ("use lock_policy rtos_mutex (then only callers that cannot wait suspend the interrupts), a shorter "
              "sample_clocks or no averaging" if not plan.rtos_mutex else
              "shorten sample_clocks, switch the averaging off or accept the suspension")
    rep.warning("project", ("modules", MODULE, "max_interrupt_suspend_us"),
                f"ADC lock policy {plan.policy}: {what}, more than the {plan.max_suspend_us} us "
                f"(max_interrupt_suspend_us) this project accepts; {advice}")


def check(cfg: Config, rep: Reporter) -> AdcAccessPlan | None:
    """Derived ADC access timing of a project; None without a family model, without ADC or after errors."""
    integration = cfg.integration
    values = integration.parameters(MODULE) if integration is not None else {}
    if not values:
        return None                                 # module not part of this build (no manifest, or faulty)
    model = MODELS.get(cfg.derivative.derivative.family)
    if model is None:
        return None
    before = sum(m.severity == "error" for m in rep.messages)
    plan = model(cfg, rep, values)
    if plan is None or sum(m.severity == "error" for m in rep.messages) != before:
        return None
    if plan.used:
        _suspension(plan, rep)
    return plan
