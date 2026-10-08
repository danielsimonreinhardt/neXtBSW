"""Clock model: oscillator (board) + system clock (project) + clock limits (derivative) -> sources, PLL, dividers,
peripheral clocks and the derived communication timings (doc/architecture/README.md, section 4).

- Family neutral (all families): the oscillator of the board within the derivative's limits, the system clock not
  above the derivative's core maximum, CAN bit rate and UART baud rate given for every used bus (project can/uart).
- Family clock model (per family, registry MODELS): which source feeds the system clock, the resulting frequencies
  and, with the calculation kernels of timing.py, CAN bit timing and UART dividers. Findings come with a YAML path;
  register encodings are left to the generators (tools/bswgen).
- The functional clock of a peripheral: the derivative says which sources and asynchronous outputs its clock
  select offers and how fast that output may run (instances.<x>.clock sources/divider/max_hz, or select bus/core/
  none for a peripheral without a clock select), the project chooses one of them (peripheral_clocks.<x>, ARCH-18).
  Without a project entry the derivative's `default` applies; an instance the project uses that has neither is an
  error, not a silent gap (ARCH-09 B5, PLAT-22). clock.pcc holds gates the database does not model as instances.
- Dividers are derived, not configured (ARCH-22): the derivative names the maximum of every clock domain
  (clock.max_hz), stricter maxima while a given source feeds the system clock (clock.system_source_max_hz) and the
  maximum of every asynchronous output (clock.output_max_hz); the family model takes the smallest divider within.

Models: s32k1xx (SCG), stm32 (RCC, PLAT-14), rm4x (global clock module with named domains, PLAT-70; no generator
yet). A family without a model gets the neutral checks only; its generator does not exist yet either.
"""

from dataclasses import dataclass, field
from fractions import Fraction

from .config import Config, Reporter
from .messages import format_path as _fmt
from .model import FlashWaitBand
from .timing import (BaudLimits, BaudSetting, CanTiming, CanTimingLimits, PllLimits, PllSetting, baud_divider,
                     can_bit_timing, pll_preference, pll_settings, smallest_divider)

# CAN sample point deviation from the project's request (percentage points)
SAMPLE_POINT_WARNING_PP = Fraction(1)
SAMPLE_POINT_ERROR_PP = Fraction(5, 2)
# UART baud rate error (percent): above the warning limit a warning, above the error limit an error. Both ends of
# a link may deviate, an 8N1 receiver samples the stop bit 9.5 bits after the start edge (about 5 % in total).
BAUD_WARNING_PERCENT = Fraction(1)
BAUD_ERROR_PERCENT = Fraction(2)


@dataclass
class ClockPlan:
    """Result of a family clock model. Frequencies in Hz (Fraction, exact)."""

    family: str
    system_hz: int
    system_source: str                              # e.g. SPLL, FIRC, SOSC
    oscillator_hz: int | None
    pll: PllSetting | None                          # configured PLL (also when it does not feed the system clock)
    domains: dict[str, Fraction]                    # core, bus, slow, ...
    dividers: dict[str, int]                        # divider per domain
    outputs: dict[str, Fraction] = field(default_factory=dict)             # family clock outputs, e.g. FIRCDIV2
    sources: dict[str, Fraction] = field(default_factory=dict)             # running clock sources, undivided
    output_dividers: dict[str, int] = field(default_factory=dict)          # ARCH-22: divider of every output
    peripheral_source: dict[str, str] = field(default_factory=dict)        # instance/clock gate -> source
    peripheral_output: dict[str, str] = field(default_factory=dict)        # clock gate -> output, e.g. FIRCDIV2
    peripheral_hz: dict[str, Fraction] = field(default_factory=dict)       # instance -> functional clock
    # ARCH-18: who decided the clock of a gate - "project" (peripheral_clocks.<x>) or "derivative" (its default)
    peripheral_origin: dict[str, str] = field(default_factory=dict)
    can: dict[str, CanTiming] = field(default_factory=dict)                # used CAN bus, bootloader bus -> bit timing
    reset_core_hz: int | None = None                # core clock after reset, before any clock driver (bootloader)
    uart: dict[str, BaudSetting] = field(default_factory=dict)             # used UART -> divider
    pll_stages: tuple[int, ...] = ()                # PLAT-70: output divider per stage (rm4x OD, R), empty: one stage
    flash_wait_states: FlashWaitBand | None = None  # PLAT-70: band of the flash clock (rm4x FRDCNTL), None: none

    def counter_hz(self, timer_instance: str) -> Fraction:
        """Counter clock of a timer instance as the family PWM drivers use it."""
        return self.peripheral_hz.get(f"{timer_instance}.counter", self.domains["core"])


def _pct(x: Fraction) -> str:
    return f"{float(x * 100):.2f} %"


def _mhz(hz) -> str:
    return f"{float(Fraction(hz) / 1_000_000):g} MHz"


# ----------------------------------------------------------------------------------------------- neutral checks


def _limits(cfg: Config, rep: Reporter):
    clk = cfg.derivative.clock
    if clk is None:
        return
    pairs = []
    if clk.pll is not None:
        pairs = [(("clock", "pll", key), getattr(clk.pll, key))
                 for key in ("input_hz", "vco_hz", "input_divider", "multiplier")]
    if clk.oscillator is not None:
        pairs.append((("clock", "oscillator"), [clk.oscillator.min_hz, clk.oscillator.max_hz]))
    for path, (low, high) in pairs:
        if low > high:
            rep.error("derivative", path, f"minimum {low} is above maximum {high}")


def _neutral(cfg: Config, rep: Reporter) -> bool:
    """Checks every family gets. False if the clock model must not run."""
    ok = True
    clk, board, project = cfg.derivative.clock, cfg.board.board, cfg.project
    osc = board.clock.sosc_hz
    name = cfg.derivative.derivative.name
    if clk is not None:
        if osc is not None and clk.oscillator is None:
            rep.error("board", ("board", "clock", "sosc_hz"), f"{name} has no external oscillator input")
            ok = False
        elif osc is not None and not clk.oscillator.min_hz <= osc <= clk.oscillator.max_hz:
            rep.error("board", ("board", "clock", "sosc_hz"), f"oscillator {_mhz(osc)} is outside the range of "
                      f"{name} ({_mhz(clk.oscillator.min_hz)}..{_mhz(clk.oscillator.max_hz)})")
            ok = False
        core_max = clk.max_hz.get("core")
        if core_max is not None and project.clock.system_hz > core_max:
            rep.error("project", ("clock", "system_hz"), f"system clock {_mhz(project.clock.system_hz)} exceeds "
                      f"the maximum core clock of {name} ({_mhz(core_max)})")
            ok = False

    for kind, settings in (("can", project.can), ("uart", project.uart)):
        board_names = [b.name for b in getattr(cfg.board, kind)]
        used = [b.name for b in cfg.selected(kind)]
        if kind == "can" and project.bootloader is not None and project.bootloader.can not in used:
            used.append(project.bootloader.can)         # bit timing of the bootloader bus (memory.py checks it)
        for key in settings:
            if key not in board_names:
                rep.error("project", (kind, key), f"board {board.name} has no {kind} entry {key} "
                          f"(available: {', '.join(board_names) or '-'})")
                ok = False
            elif key not in used and kind != "can":
                rep.warning("project", (kind, key), f"{kind} {key} is not used (use.{kind}), setting ignored")
            # CAN: no warning for a bus the application does not use - its bit timing is generated anyway
            # (bootloader bus, build option BOOTLOADER_CAN_BUS, project variants; ARCH-07)
        what = "bitrate" if kind == "can" else "baudrate"
        for bus in used:
            if bus not in settings:
                rep.error("project", (kind,), f"{kind} {bus} is used (use.{kind}), but {kind}.{bus}.{what} is missing")
                ok = False
    return ok


def derivative_clock(cfg: Config, name: str):
    """(clock entry of an instance or clock gate of the derivative, YAML path). (None, path) if there is none."""
    inst = cfg.derivative.instances.get(name)
    if inst is not None:
        return inst.clock, ("instances", name, "clock")
    if cfg.derivative.clock is not None and name in cfg.derivative.clock.pcc:
        return cfg.derivative.clock.pcc[name], ("clock", "pcc", name)
    return None, ("instances", name, "clock")


def peripheral_choices(cfg: Config, rep: Reporter) -> dict[str, tuple[str, int]]:
    """ARCH-18: the functional clocks the project chooses (peripheral_clocks) -> (source, divider).

    Family neutral: every choice has to name an instance (or a clock gate clock.pcc) of the derivative, a source
    the derivative's clock select offers and one of its asynchronous outputs. A rejected entry is left out, so
    the derivative's default applies and the following checks still see a complete clock plan. The limit of the
    output (max_hz) needs the frequencies and is checked in the family model.
    """
    choices: dict[str, tuple[str, int]] = {}
    if not cfg.project.peripheral_clocks:
        return choices
    used = used_instances(cfg)
    gates = cfg.derivative.clock.pcc if cfg.derivative.clock is not None else {}
    name = cfg.derivative.derivative.name

    def error(path, text):
        rep.error("project", ("peripheral_clocks",) + path, text)

    for instance, choice in cfg.project.peripheral_clocks.items():
        entry, where = derivative_clock(cfg, instance)
        if instance not in cfg.derivative.instances and instance not in gates:
            error((instance,), f"{name} has no peripheral instance {instance} (derivative database: instances, "
                               f"clock.pcc)")
            continue
        if entry is None:
            error((instance,), f"the derivative database gives {instance} no clock data ({_fmt(where)} with "
                               f"sources, divider and a source), so there is nothing to choose from")
            continue
        if entry.domain is not None:
            # PLAT-70: rm4x-like instance on a domain of the global clock module - no sources to list
            error((instance,), f"{instance} has no clock select of its own: it runs on the clock domain "
                               f"{entry.domain} ({_fmt(where)}.domain in {cfg.derivative_file.name}), whose "
                               f"frequency the family clock model derives from the system clock; there is nothing "
                               f"to choose")
            continue
        if entry.select is not None:
            error((instance,), f"{instance} has no clock select: its functional clock is {entry.select} "
                               f"({_fmt(where)}.select in {cfg.derivative_file.name})")
            continue
        if choice.select not in entry.sources:
            error((instance, "select"), f"{instance} cannot run from {choice.select}; {name} allows "
                                        f"{', '.join(entry.sources)} ({_fmt(where)}.sources)")
            continue
        divider = choice.divider if choice.divider is not None else entry.only_divider()
        if divider is None:
            error((instance,), f"{instance} needs divider (the asynchronous output of {choice.select}): {name} "
                               f"offers {', '.join(str(d) for d in entry.divider)} ({_fmt(where)}.divider)")
            continue
        if divider not in entry.divider:
            error((instance, "divider"), f"{instance} is not wired to the output {choice.select}DIV{divider}; "
                                         f"{name} offers "
                                         f"{', '.join(f'{choice.select}DIV{d}' for d in entry.divider)} "
                                         f"({_fmt(where)}.divider)")
            continue
        if instance in cfg.derivative.instances and instance not in used:
            rep.warning("project", ("peripheral_clocks", instance),
                        f"{instance} is not used by the project (use.*, pwm, adc_internal, bootloader), setting "
                        f"ignored")
            continue
        choices[instance] = (choice.select, divider)
    return choices


# ----------------------------------------------------------------------------------------------- family s32k1xx

S32K1_DIVCORE = range(1, 17)                        # SCG_RCCR DIVCORE/DIVBUS 4 bit, DIVSLOW 3 bit (S32K148_SCG.h)
S32K1_DIVBUS = range(1, 17)
S32K1_DIVSLOW = range(1, 9)
S32K1_SOSC_RANGE3_HZ = (8_000_000, 40_000_000)      # clockConfig.cpp soscConfig: RANGE(3) (high range), EREFS crystal
S32K1_SYSTEM_SOURCES = ("FIRC", "SOSC", "SIRC")     # preference on equal divider; SPLL only if none of them fits
S32K1_DOMAINS = ("core", "bus", "slow")             # SCG_RCCR DIVCORE/DIVBUS/DIVSLOW
# ARCH-22: asynchronous outputs DIV1/DIV2 of every source and their dividers SCG_<src>DIV[<src>DIV1/2]
# "÷ 1, 2, 4, 8, 16, 32, 64, or output disabled" (S32K1xx RM Rev. 13, 27.3 Table 27-1). The limits are facts of the derivative
# (clock.output_max_hz); the register code of a divider is left to the generator (tools/bswgen).
S32K1_ASYNC_OUTPUTS = (1, 2)
S32K1_ASYNC_DIVIDERS = (1, 2, 4, 8, 16, 32, 64)
# PCC clock gates the s32k1xx clock driver of OpenBSW writes at start-up, in the order of
# external/openbsw/platforms/s32k1xx/bsp/bspClock/src/clockConfig.cpp (enablePeripheralClocks, lines 105..190).
# This list carries no clock data: which clock a gate gets stands in the derivative (PLAT-22, instances.<x>.clock
# and clock.pcc.<gate>). It only says which CLOCK_SOURCE_<gate> macros the driver consumes and in which order the
# generator writes them. The driver guards every gate with #ifdef PCC_<gate>_INDEX.
# ARCH-18: a gate the derivative does not have gets no macro any more - the generated mcu/mcuDerivative.h undefines
# PCC_<gate>_INDEX of the absent peripherals, so the driver leaves those blocks out (no CLOCK_SOURCE_FTM4/FTM5/ENET
# for the S32K144 with the S32K148 header any more). tools/bswgen generates the PCC writes of the used instances.
S32K1_DRIVER_GATES = ("FTM0", "FTM1", "FTM2", "FTM3", "FTM4", "FTM5", "ADC0", "ADC1", "LPSPI0", "LPSPI1", "LPSPI2",
                      "LPIT", "LPTMR0", "FLEXIO", "LPI2C0", "LPUART0", "LPUART1", "LPUART2", "ENET")
# The driver writes these gates unconditionally: without their macro the port does not compile, so the derivative
# has to describe them. The others are guarded with #ifdef PCC_<gate>_INDEX and only needed where the device
# header defines the register (clockConfig.cpp lines 111..190).
S32K1_DRIVER_GATES_REQUIRED = frozenset(
    ("FTM0", "FTM1", "ADC0", "LPSPI0", "LPIT", "LPTMR0", "FLEXIO", "LPI2C0", "LPUART0", "LPUART1"))
# gate name in the driver -> instance name in the derivative where the spelling differs
S32K1_GATE_INSTANCES = {"FLEXIO": "FlexIO"}
# A clock source the board cannot provide (no oscillator, no PLL) is replaced by FIRC, like the driver does.
# Only for the derivative's default: a source the *project* chose and the board cannot provide is an error.
S32K1_FALLBACK_SOURCE = "FIRC"
# FlexCAN CTRL1 (classic CAN): PRESDIV 8 bit, PROPSEG/PSEG1/PSEG2 3 bit, RJW 2 bit; PSEG2 >= 2 tq (information
# processing time), 8..25 tq per bit (bsp/can/canBitTiming.h isValid)
FLEXCAN_LIMITS = CanTimingLimits(prescaler=(1, 256), prop_seg=(1, 8), phase_seg1=(1, 8), phase_seg2=(2, 8),
                                 sjw=(1, 4), quanta=(8, 25))
# LPUART BAUD: OSR 5 bit (4..32 times oversampling, BOTHEDGE below 8), SBR 13 bit; reset value OSR = 16
LPUART_LIMITS = BaudLimits(oversampling=(4, 32), divider=(1, 8191), preferred_oversampling=16)


def _s32k1xx_limit_names(cfg: Config, rep: Reporter) -> bool:
    """ARCH-22: the names in clock.system_source_max_hz and clock.output_max_hz of an s32k1xx derivative.

    A misspelt key would silently leave a limit unused, so every key has to name a system clock source, a clock
    domain or an output of the family model, and a derivative that gives output limits gives them for every
    output of its sources (an output without a limit would stay undivided). False after an error.
    """
    clk = cfg.derivative.clock
    ok = True
    sources = ["FIRC", "SIRC"] + (["SOSC"] if clk.oscillator is not None else []) + (["SPLL"] if clk.pll else [])
    for src, limits in clk.system_source_max_hz.items():
        where = ("clock", "system_source_max_hz", src)
        if src not in sources:
            rep.error("derivative", where, f"{src} is no system clock source of the s32k1xx clock model for "
                                           f"{cfg.derivative.derivative.name} (available: {', '.join(sources)})")
            ok = False
            continue
        for dom in limits.max_hz:
            if dom not in S32K1_DOMAINS:
                rep.error("derivative", where + ("max_hz", dom), f"{dom} is no clock domain of the s32k1xx clock "
                                                                  f"model (available: {', '.join(S32K1_DOMAINS)})")
                ok = False
    if clk.output_max_hz is not None:
        where = ("clock", "output_max_hz", "max_hz")
        names = [f"{src}DIV{n}" for src in sources for n in S32K1_ASYNC_OUTPUTS]
        for name in clk.output_max_hz.max_hz:
            if name not in names:
                rep.error("derivative", where + (name,), f"{name} is no asynchronous clock output of "
                                                         f"{cfg.derivative.derivative.name} (available: "
                                                         f"{', '.join(names)})")
                ok = False
        missing = [name for name in names if name not in clk.output_max_hz.max_hz]
        if missing:
            rep.error("derivative", where, f"no limit for {', '.join(missing)}: the s32k1xx clock model would leave "
                                           f"these outputs undivided; give the maximum of every output")
            ok = False
    return ok


def _domain_limit(clk, source: str, dom: str):
    """(maximum, YAML path) of a clock domain while `source` feeds the system clock; (None, None) without limit."""
    limits = []
    if dom in clk.max_hz:
        limits.append((clk.max_hz[dom], ("clock", "max_hz", dom)))
    special = clk.system_source_max_hz.get(source)
    if special is not None and dom in special.max_hz:
        limits.append((special.max_hz[dom], ("clock", "system_source_max_hz", source, "max_hz", dom)))
    return min(limits, key=lambda item: item[0], default=(None, None))


def _pll_limits(clk) -> PllLimits:
    p = clk.pll
    return PllLimits(tuple(p.input_hz), tuple(p.vco_hz), tuple(p.input_divider), tuple(p.multiplier),
                     tuple(p.output_dividers))


def used_instances(cfg: Config) -> dict[str, tuple[str, tuple]]:
    """Peripheral instance -> (what uses it, YAML path in the project) for everything the project switches on.

    Family neutral: the board entries the project selects, its PWM groups, internal ADC channels and the CAN bus
    of the bootloader. Used by the clock models to demand a functional clock for exactly these instances.
    """
    used: dict[str, tuple[str, tuple]] = {}

    def add(instance: str, what: str, path: tuple):
        used.setdefault(instance, (what, path))

    for kind in ("spi", "can", "uart", "i2c"):
        for bus in cfg.selected(kind):
            add(bus.instance, f"{kind} {bus.name}", ("use", kind))
    for ch in cfg.selected("adc"):
        add(ch.instance, f"adc {ch.name}", ("use", "adc"))
    for i, ch in enumerate(cfg.project.adc_internal):
        add(ch.instance, f"adc_internal {ch.name}", ("adc_internal", i))
    for grp, _ in cfg.selected_pwm():
        add(grp.instance, f"pwm {grp.name}", ("pwm", grp.name))
    for ic in cfg.selected("input_capture"):
        add(ic.instance, f"input_capture {ic.name}", ("use", "input_capture"))
    if cfg.project.bootloader is not None:
        bus = next((b for b in cfg.board.can if b.name == cfg.project.bootloader.can), None)
        if bus is not None:
            add(bus.instance, f"bootloader bus {bus.name}", ("bootloader", "can"))
    return used


def _peripheral_clocks(cfg: Config, rep: Reporter, plan: ClockPlan, freqs: dict,
                       choices: dict[str, tuple[str, int]]) -> bool:
    """Functional clock of every peripheral. False after an error.

    The derivative offers the sources and outputs (PLAT-22), the project chooses (ARCH-18) and its default fills
    the gap for a project that chooses nothing. Order of the clock gates: first the gates the OpenBSW clock
    driver writes (S32K1_DRIVER_GATES, the order of the generated CLOCK_SOURCE macros), then the other instances
    of the derivative in the order of the database.
    """
    name = cfg.derivative.derivative.name
    used = used_instances(cfg)
    ok = True
    driver = [(gate, S32K1_GATE_INSTANCES.get(gate, gate)) for gate in S32K1_DRIVER_GATES]
    instances = {inst for _, inst in driver}
    order = driver + [(inst, inst) for inst in cfg.derivative.instances if inst not in instances]

    pcc_gates = cfg.derivative.clock.pcc if cfg.derivative.clock is not None else {}
    for gate, instance in order:
        entry, path = derivative_clock(cfg, instance)
        known = instance in cfg.derivative.instances or instance in pcc_gates
        choice = choices.get(instance)
        if entry is None or (entry.select is None and entry.default is None and choice is None):
            if entry is None:
                level = "derivative"
                missing = f"the derivative database gives it no clock data ({_fmt(path)})"
            else:
                # the derivative offers sources but no default: the project has to decide (PLAT-22, finding 1)
                level = "project"
                missing = (f"no functional clock is chosen: {name} offers "
                           f"{', '.join(f'{s}DIV{d}' for s in entry.sources for d in entry.divider)} "
                           f"({_fmt(path)}.sources in {cfg.derivative_file.name}) and names no default, so the "
                           f"project has to choose one (peripheral_clocks.{instance})")
            if instance in used:
                # the line of what uses the instance: that is where the reader sees the cause
                what, used_where = used[instance]
                rep.error(level, path if level == "derivative" else used_where,
                          f"{name}: {instance} is used by {what} ({_fmt(used_where)} in "
                          f"{cfg.project_file.name}), but {missing}")
                ok = False
            elif known and gate in S32K1_DRIVER_GATES:
                rep.error("derivative", path, f"{name}: the s32k1xx clock driver switches the clock of {gate} on "
                          f"and needs CLOCK_SOURCE_{gate}, but {missing}")
                ok = False
            elif gate in S32K1_DRIVER_GATES_REQUIRED:
                rep.error("derivative", ("instances",), f"{name}: the s32k1xx clock driver writes PCC_{gate} "
                          f"unconditionally (clockConfig.cpp) and needs CLOCK_SOURCE_{gate}, but the derivative "
                          f"database describes neither an instance {instance} nor a clock gate clock.pcc.{gate}")
                ok = False
            continue
        if entry.select == "none":
            continue                                   # clock selected in the peripheral (FlexCAN CTRL1.CLKSRC)
        if entry.select in ("bus", "core"):
            plan.peripheral_hz[instance] = plan.domains[entry.select]
            continue
        select, divider = choice if choice is not None else (entry.default.select, entry.default.divider)
        origin = "project" if choice is not None else "derivative"
        if select in freqs:
            source = select
        elif choice is not None:
            # the project chose a source this board cannot provide (no crystal, no PLL): its decision, its error
            rep.error("project", ("peripheral_clocks", instance, "select"),
                      f"{instance} cannot run from {select}: board {cfg.board.board.name} does not provide it "
                      f"(available: {', '.join(sorted(freqs))})")
            ok = False
            continue
        else:
            source = S32K1_FALLBACK_SOURCE             # like the driver: a missing source becomes FIRC
        output = f"{source}DIV{divider}"
        if output not in plan.outputs:
            rep.error("derivative", path, f"{name}: {gate} runs from {select}, which the s32k1xx clock model "
                                          f"does not provide (available: {', '.join(sorted(plan.outputs))})")
            ok = False
            continue
        hz = plan.outputs[output]
        if entry.max_hz is not None and hz > entry.max_hz:
            level, where = (("project", ("peripheral_clocks", instance)) if origin == "project"
                            else ("derivative", path + ("default",)))
            rep.error(level, where, f"{instance} would run at {_mhz(hz)} from {output}, more than the maximum "
                      f"functional clock of {_mhz(entry.max_hz)} that {name} allows ({_fmt(path)}.max_hz)"
                      + ("" if origin == "project" else "; choose another source (peripheral_clocks."
                         f"{instance}) or another system clock"))
            ok = False
            continue
        plan.peripheral_source[gate] = source
        plan.peripheral_output[gate] = output
        plan.peripheral_hz[instance] = hz
        plan.peripheral_origin[gate] = origin
    return ok


def _s32k1xx(cfg: Config, rep: Reporter, choices: dict[str, tuple[str, int]]) -> ClockPlan | None:
    clk = cfg.derivative.clock
    name = cfg.derivative.derivative.name
    if clk is None:
        rep.error("derivative", ("derivative", "name"), f"{name}: no clock data (clock) in the derivative database, "
                  "needed by the s32k1xx clock model")
        return None
    missing = [s for s in ("FIRC", "SIRC") if s not in clk.internal]
    if missing:
        rep.error("derivative", ("clock", "internal"), f"s32k1xx clock model needs internal clocks {', '.join(missing)}")
        return None
    if not _s32k1xx_limit_names(cfg, rep):
        return None
    target = cfg.project.clock.system_hz
    osc = cfg.board.board.clock.sosc_hz
    if osc is not None and not S32K1_SOSC_RANGE3_HZ[0] <= osc <= S32K1_SOSC_RANGE3_HZ[1]:
        rep.error("board", ("board", "clock", "sosc_hz"), f"oscillator {_mhz(osc)}: the s32k1xx clock driver "
                  f"configures SOSC for a crystal in the high range ({_mhz(S32K1_SOSC_RANGE3_HZ[0])}.."
                  f"{_mhz(S32K1_SOSC_RANGE3_HZ[1])})")
        return None
    base = {"FIRC": clk.internal["FIRC"], "SIRC": clk.internal["SIRC"]}
    if osc is not None:
        base["SOSC"] = osc

    # PLL: started by the driver whenever an oscillator is fitted and the derivative has one
    settings = list(pll_settings(osc, _pll_limits(clk))) if (osc is not None and clk.pll is not None) else []
    if osc is not None and clk.pll is not None and not settings:
        rep.error("board", ("board", "clock", "sosc_hz"), f"no PLL setting of {name} fits oscillator {_mhz(osc)} "
                  f"(reference {_mhz(clk.pll.input_hz[0])}..{_mhz(clk.pll.input_hz[1])} after divider "
                  f"{clk.pll.input_divider[0]}..{clk.pll.input_divider[1]})")
        return None

    source, divcore, pll = None, None, None
    for d in S32K1_DIVCORE:
        found = [s for s in S32K1_SYSTEM_SOURCES if s in base and Fraction(base[s], d) == target]
        if found:
            source, divcore = found[0], d
            break
    if source is None:
        candidates = sorted(((pll_preference(s), d, s) for s in settings for d in S32K1_DIVCORE
                             if s.output_hz / d == target), key=lambda c: (c[0], c[1]))
        if candidates:
            _, divcore, pll = candidates[0]
            source = "SPLL"
    if source is None:
        reachable = {Fraction(f, d) for f in base.values() for d in S32K1_DIVCORE}
        reachable |= {s.output_hz / d for s in settings for d in S32K1_DIVCORE}
        core_max = clk.max_hz.get("core", max(reachable))
        options = sorted((f for f in reachable if f <= core_max and f.denominator == 1), reverse=True)[:8]
        rep.error("project", ("clock", "system_hz"), f"system clock {_mhz(target)} cannot be derived exactly from "
                  f"FIRC, SIRC" + (f", SOSC {_mhz(osc)} or the PLL" if osc is not None else " (no oscillator)")
                  + f" with DIVCORE 1..16 (possible e.g.: {', '.join(_mhz(f) for f in options)})")
        return None
    if pll is None and settings:
        pll = min(settings, key=pll_preference)

    core = Fraction(target)
    # ARCH-22: a limit of the derivative that applies only with this system clock source (s32k14x: the core limit
    # is checked family neutral above, a stricter one of the source here)
    limit, where = _domain_limit(clk, source, "core")
    if limit is not None and core > limit:
        rep.error("project", ("clock", "system_hz"), f"system clock {_mhz(target)} from {source} exceeds the maximum "
                  f"core clock of {name} with {source} as system clock ({_mhz(limit)}, {_fmt(where)} in "
                  f"{cfg.derivative_file.name})")
        return None
    domains, dividers = {"core": core}, {"core": divcore}
    for dom, divs in (("bus", S32K1_DIVBUS), ("slow", S32K1_DIVSLOW)):
        # ARCH-22: the smallest divider within the strictest limit, e.g. BUS_CLK 40 MHz with the SPLL as system clock
        limit, where = _domain_limit(clk, source, dom)
        d = 1 if limit is None else smallest_divider(core, limit, divs)
        if d is None:
            rep.error("project", ("clock", "system_hz"), f"system clock {_mhz(target)} from {source}: {dom} clock "
                      f"cannot be divided below {_mhz(limit)} ({_fmt(where)} in {cfg.derivative_file.name}; divider "
                      f"{divs.start}..{divs.stop - 1})")
            return None
        domains[dom], dividers[dom] = core / d, d
    plan = ClockPlan("s32k1xx", target, source, osc, pll, domains, dividers)

    # asynchronous outputs DIV1/DIV2 of every running source (ARCH-22): the smallest divider that keeps the output
    # within the limit of the derivative (clock.output_max_hz); without limits the outputs stay undivided.
    freqs = dict(base)
    if pll is not None:
        freqs["SPLL"] = pll.output_hz
    limits = clk.output_max_hz.max_hz if clk.output_max_hz is not None else {}
    for src, f in freqs.items():
        plan.sources[src] = Fraction(f)
        for n in S32K1_ASYNC_OUTPUTS:
            output = f"{src}DIV{n}"
            limit = limits.get(output)
            d = 1 if limit is None else smallest_divider(f, limit, S32K1_ASYNC_DIVIDERS)
            if d is None:
                rep.error("derivative", ("clock", "output_max_hz", "max_hz", output),
                          f"{name}: {output} cannot be divided from {src} {_mhz(f)} to at most {_mhz(limit)} "
                          f"(dividers {', '.join(str(x) for x in S32K1_ASYNC_DIVIDERS)})")
                return None
            plan.output_dividers[output] = d
            plan.outputs[output] = Fraction(f) / d
    if not _peripheral_clocks(cfg, rep, plan, freqs, choices):
        return None
    for inst, data in cfg.derivative.instances.items():
        if data.class_ == "timer":
            plan.peripheral_hz[f"{inst}.counter"] = core        # FTM SC.CLKS = 01: system clock
        if data.class_ == "can":
            if osc is not None:
                plan.peripheral_hz[inst] = plan.outputs["SOSCDIV2"]  # CTRL1.CLKSRC = 0: oscillator clock SOSCDIV2

    plan.reset_core_hz = clk.internal["FIRC"]           # SCG reset state: FIRC with DIVCORE 1 (bootloader clock)
    buses = cfg.selected("can")
    # Bit timing for every board CAN bus the project gives values for, not only for the ones the application
    # uses (ARCH-07): the bootloader bus (bootloader.can or the build option BOOTLOADER_CAN_BUS) and buses a
    # variant of the project switches on need it as well. The application buses keep their order (bus ids).
    names = [b.name for b in buses]
    buses += [b for b in cfg.board.can if b.name not in names and b.name in cfg.project.can]
    for bus in buses:
        use = cfg.project.can[bus.name]
        where = ("can", bus.name)
        clock_hz = plan.peripheral_hz.get(bus.instance)
        if clock_hz is None:
            rep.error("project", where, f"CAN {bus.name} ({bus.instance}): FlexCAN runs from the oscillator clock "
                      f"SOSCDIV2 (CTRL1.CLKSRC = 0), board {cfg.board.board.name} has no board.clock.sosc_hz")
            continue
        _can_timing(plan, rep, bus.name, int(clock_hz), use, FLEXCAN_LIMITS)
    for bus in cfg.selected("uart"):
        _uart_divider(plan, rep, bus.name, bus.instance, cfg.project.uart[bus.name].baudrate, LPUART_LIMITS)
    return plan


# ----------------------------------------------------------------------------------------------- shared steps


def _can_timing(plan: ClockPlan, rep: Reporter, bus: str, clock_hz: int, use, limits: CanTimingLimits):
    where = ("can", bus)
    wanted = Fraction(str(use.sample_point_percent)) / 100
    timing, problem = can_bit_timing(clock_hz, use.bitrate, wanted, limits, use.sjw_tq, use.prop_seg_tq)
    if timing is None:
        exact = "cannot be derived exactly" in problem
        rep.error("project", where + (("bitrate",) if exact else ()), f"CAN {bus}: {problem}")
        return
    deviation = abs(timing.sample_point - wanted) * 100
    text = (f"CAN {bus}: sample point {_pct(timing.sample_point)} instead of {_pct(wanted)} "
            f"({timing.quanta} tq at prescaler {timing.prescaler}, clock {_mhz(clock_hz)})")
    if deviation > SAMPLE_POINT_ERROR_PP:
        rep.error("project", where + ("sample_point_percent",), text)
        return
    if deviation > SAMPLE_POINT_WARNING_PP:
        rep.warning("project", where + ("sample_point_percent",), text)
    plan.can[bus] = timing


def _uart_divider(plan: ClockPlan, rep: Reporter, bus: str, instance: str, baudrate: int, limits: BaudLimits):
    where = ("uart", bus, "baudrate")
    clock_hz = plan.peripheral_hz.get(instance)
    if clock_hz is None:
        rep.error("project", where, f"UART {bus}: no functional clock known for {instance}")
        return
    setting = baud_divider(int(clock_hz), baudrate, limits)
    if setting is None:
        rep.error("project", where, f"UART {bus}: {baudrate} Bd is out of range for {instance} at {_mhz(clock_hz)}")
        return
    error = abs(setting.error(baudrate)) * 100
    text = (f"UART {bus}: {float(setting.baudrate):.1f} Bd instead of {baudrate} Bd ({float(error):.2f} % error, "
            f"{instance} clock {_mhz(clock_hz)})")
    if error > BAUD_ERROR_PERCENT:
        rep.error("project", where, text + f", more than {float(BAUD_ERROR_PERCENT):g} %")
        return
    if error > BAUD_WARNING_PERCENT:
        rep.warning("project", where, text)
    plan.uart[bus] = setting


# ----------------------------------------------------------------------------------------------- family stm32
# PLAT-14: HSI/HSE -> (PLL) -> SYSCLK -> AHB (HCLK, core) -> APB1/APB2. Only one model, added next to the s32k1xx
# one; nothing of the s32k1xx model is touched.
#
# What is family knowledge and stands here:
# - the divider sets of the RCC (HPRE, PPRE1, PPRE2) and the system clock sources,
# - which peripheral instance hangs on which APB bus (STM32F4_APB, checked against RCC_APB1ENR_<x>EN /
#   RCC_APB2ENR_<x>EN of the device header in platforms/stm32/test/test_stm32f446.py),
# - the doubling of the timer counter clock when the APB prescaler is not 1 (RCC_DCKCFGR.TIMPRE = 0),
# - the register limits of bxCAN and of the USART baud rate divider.
# What comes from the configuration: oscillator (board), system clock (project), PLL and bus limits (derivative
# clock.max_hz: core, apb1, apb2 - the APB1 limit of the F446 is 45 MHz, not the 48 MHz the F413 port uses).
#
# Open (finding of PLAT-14): the functional clock of an instance comes from the configuration since PLAT-22/ARCH-18
# (derivative instances.<x>.clock, project peripheral_clocks.<x>), but model.ClockSource only knows the s32k1xx
# sources and model.ClockDomain only "bus"/"core"/"none" - there is no way to say "APB1" or "APB2". Until the schema
# has named clock domains per family, the bus of an instance stands in the table below and the derivative entries of
# mcudb/stm32 carry no clock. The table is the only stm32 place where an instance name appears.
STM32_HPRE = (1, 2, 4, 8, 16, 64, 128, 256, 512)    # RCC_CFGR HPRE (SYSCLK -> HCLK)
STM32_PPRE = (1, 2, 4, 8, 16)                       # RCC_CFGR PPRE1/PPRE2 (HCLK -> PCLK1/PCLK2)
STM32_SYSTEM_SOURCES = ("HSE", "HSI")               # RCC_CFGR SW = 1 / 0; the PLL (SW = 2) is used if neither fits
STM32_PLL_SOURCES = ("HSE", "HSI")                  # RCC_PLLCFGR PLLSRC
# Instance -> APB bus (RM0390 RCC_APB1ENR/RCC_APB2ENR; every entry is checked against the device header)
STM32F4_APB = {
    "TIM2": "apb1", "TIM3": "apb1", "TIM4": "apb1", "TIM5": "apb1", "TIM6": "apb1", "TIM7": "apb1",
    "TIM12": "apb1", "TIM13": "apb1", "TIM14": "apb1", "SPI2": "apb1", "SPI3": "apb1", "USART2": "apb1",
    "USART3": "apb1", "UART4": "apb1", "UART5": "apb1", "I2C1": "apb1", "I2C2": "apb1", "I2C3": "apb1",
    "FMPI2C1": "apb1", "CAN1": "apb1", "CAN2": "apb1", "CAN3": "apb1", "SPDIFRX": "apb1", "DAC": "apb1",
    "TIM1": "apb2", "TIM8": "apb2", "TIM9": "apb2", "TIM10": "apb2", "TIM11": "apb2", "USART1": "apb2",
    "USART6": "apb2", "ADC1": "apb2", "ADC2": "apb2", "ADC3": "apb2", "SDIO": "apb2", "SPI1": "apb2",
    "SPI4": "apb2", "SAI1": "apb2", "SAI2": "apb2", "QUADSPI": "ahb3",
}
# bxCAN CAN_BTR: BRP 10 bit (1..1024), TS1 4 bit (1..16), TS2 3 bit (1..8), SJW 2 bit (1..4). bxCAN has no separate
# propagation segment: TS1 is propagation + phase 1. The kernel of timing.py always spends one tq on the
# propagation segment, so phase 1 goes up to 15 and TS1 = 1 + phase1 stays within its 16 tq (the generator writes
# TS1 = prop_seg + phase_seg1 - 1 into the register, tools/bswgen/stm32gen.py).
BXCAN_LIMITS = CanTimingLimits(prescaler=(1, 1024), prop_seg=(1, 1), phase_seg1=(1, 15), phase_seg2=(1, 8),
                               sjw=(1, 4), quanta=(3, 25))
# USART BRR of the F4 is a fixed point divider: with OVER8 = 0 the baud rate is PCLK / BRR with BRR = 16 * USARTDIV
# (mantissa 12 bit, fraction 4 bit), i.e. an integer divider of 16..65535. Oversampling by 8 (OVER8 = 1) has a
# different fraction encoding and is not used.
USART_LIMITS = BaudLimits(oversampling=(1, 1), divider=(16, 65535), preferred_oversampling=1)
STM32_TIMER_APB_FACTOR = 2                          # RCC_DCKCFGR.TIMPRE = 0: timer clock = 2 x PCLK if PPRE != 1


def _stm32(cfg: Config, rep: Reporter, choices: dict[str, tuple[str, int]]) -> ClockPlan | None:
    clk = cfg.derivative.clock
    name = cfg.derivative.derivative.name
    if clk is None:
        rep.error("derivative", ("derivative", "name"), f"{name}: no clock data (clock) in the derivative database, "
                  "needed by the stm32 clock model")
        return None
    if "HSI" not in clk.internal:
        rep.error("derivative", ("clock", "internal"), "stm32 clock model needs the internal clock HSI "
                  "(source after reset)")
        return None
    target = cfg.project.clock.system_hz
    osc = cfg.board.board.clock.sosc_hz
    base = {"HSI": clk.internal["HSI"]}
    if osc is not None:
        base["HSE"] = osc

    # SYSCLK: an oscillator or HSI directly (no PLL), otherwise the PLL. HCLK = SYSCLK: the AHB prescaler stays 1
    # so that the core runs at the project's system clock; APB1/APB2 are divided down to their limits.
    source, pll = None, None
    for candidate in STM32_SYSTEM_SOURCES:
        if base.get(candidate) == target:
            source = candidate
            break
    if source is None:
        if clk.pll is None:
            rep.error("derivative", ("clock", "pll"), f"{name}: no PLL in the derivative database, system clock "
                      f"{_mhz(target)} is neither HSI nor the oscillator")
            return None
        for reference in STM32_PLL_SOURCES:
            if reference not in base:
                continue
            settings = [s for s in pll_settings(base[reference], _pll_limits(clk)) if s.output_hz == target]
            if settings:
                pll = min(settings, key=pll_preference)
                source = "PLL"
                plan_reference = reference
                break
        if source is None:
            reachable = {s.output_hz for r in STM32_PLL_SOURCES if r in base
                         for s in pll_settings(base[r], _pll_limits(clk))}
            core_max = clk.max_hz.get("core", max(reachable) if reachable else target)
            options = sorted((f for f in reachable if f <= core_max and f.denominator == 1), reverse=True)[:8]
            rep.error("project", ("clock", "system_hz"), f"system clock {_mhz(target)} cannot be derived exactly "
                      f"from HSI {_mhz(base['HSI'])}"
                      + (f", the oscillator {_mhz(osc)}" if osc is not None else " (no oscillator)")
                      + f" or the PLL of {name} (possible e.g.: {', '.join(_mhz(f) for f in options) or '-'})")
            return None
    else:
        plan_reference = source

    core = Fraction(target)
    domains = {"core": core, "ahb": core}
    dividers = {"core": 1, "ahb": 1}
    for dom in ("apb1", "apb2"):
        limit = clk.max_hz.get(dom)
        if limit is None:
            rep.error("derivative", ("clock", "max_hz"), f"{name}: the stm32 clock model needs the maximum of the "
                      f"{dom.upper()} bus (clock.max_hz.{dom}); most peripherals are clocked from it")
            return None
        d = smallest_divider(core, limit, STM32_PPRE)
        if d is None:
            rep.error("project", ("clock", "system_hz"), f"system clock {_mhz(target)}: {dom.upper()} cannot be "
                      f"divided below its maximum {_mhz(limit)} (prescaler "
                      f"{', '.join(str(x) for x in STM32_PPRE)})")
            return None
        domains[dom], dividers[dom] = core / d, d

    plan = ClockPlan("stm32", target, source, osc, pll, domains, dividers)
    plan.outputs = {"SYSCLK": core, "HCLK": core, "PCLK1": domains["apb1"], "PCLK2": domains["apb2"],
                    **{f"{s}": Fraction(f) for s, f in base.items()}}
    plan.reset_core_hz = clk.internal["HSI"]            # RCC reset state: HSI, HPRE/PPRE 1 (bootloader time base)
    if pll is not None:
        plan.peripheral_source["PLL"] = plan_reference

    # Functional clock of every instance the project uses; the bus comes from the family table.
    used = used_instances(cfg)
    for instance, (what, where) in used.items():
        bus = STM32F4_APB.get(instance)
        if bus is None:
            rep.error("derivative", ("instances", instance), f"{name}: {instance} is used by {what} "
                      f"({_fmt(where)} in {cfg.project_file.name}), but the stm32 clock model does not know which "
                      f"bus clocks it (tools/nxconfig/clock.py STM32F4_APB)")
            return None
        if bus not in domains:
            rep.error("derivative", ("instances", instance), f"{name}: {instance} is clocked from {bus.upper()}, "
                      f"which the stm32 clock model does not provide (available: "
                      f"{', '.join(sorted(domains))})")
            return None
        plan.peripheral_source[instance] = bus.upper()
        plan.peripheral_hz[instance] = domains[bus]
    for instance, data in cfg.derivative.instances.items():
        bus = STM32F4_APB.get(instance)
        if data.class_ != "timer" or bus not in ("apb1", "apb2"):
            continue
        # RCC_DCKCFGR.TIMPRE = 0 (reset value): the timer counter runs at twice PCLK unless the APB prescaler is 1
        factor = 1 if dividers[bus] == 1 else STM32_TIMER_APB_FACTOR
        plan.peripheral_hz[f"{instance}.counter"] = domains[bus] * factor

    buses = cfg.selected("can")
    names = [b.name for b in buses]
    buses += [b for b in cfg.board.can if b.name not in names and b.name in cfg.project.can]
    for bus in buses:
        use = cfg.project.can[bus.name]
        clock_hz = plan.peripheral_hz.get(bus.instance) or domains.get("apb1")
        # bxCAN is clocked from PCLK1 only; a bus the application does not use has no entry in peripheral_hz
        _can_timing(plan, rep, bus.name, int(clock_hz), use, BXCAN_LIMITS)
    for bus in cfg.selected("uart"):
        _uart_divider(plan, rep, bus.name, bus.instance, cfg.project.uart[bus.name].baudrate, USART_LIMITS)
    return plan


# ----------------------------------------------------------------------------------------------- family rm4x
# PLAT-70: global clock module (GCM) of TI Hercules RM4x. One source (OSCIN, HFLPO or PLL1 from OSCIN) feeds GCLK and
# HCLK at the project's system clock; every other domain is divided from its parent as the derivative describes it
# (clock.domain_dividers: VCLK = HCLK / 1..16, VCLK2 an integer multiple of VCLK, VCLKA1 = VCLK, RTICLK = VCLK ...),
# each with the smallest divider within clock.max_hz.<domain>. A peripheral instance runs on the domain it names
# (instances.<x>.clock.domain); there is no clock select per instance.
#
# What is family knowledge and stands here: which domains run at the system clock, which sources can feed it and in
# which preference, that PLL1 is fed by OSCIN, the register limits of DCAN and SCI/LIN. Everything else - source
# numbers, divider sets, domain maxima, PLL limits and stages, the oscillator monitor window, flash wait states - is a
# fact of the derivative. Not modelled (no board or project field, no driver): EXTCLKIN1/2 and PLL2 as sources, a
# domain on a source other than its parent (other_source), frequency modulation of PLL1.
RM4X_SYSTEM_DOMAINS = ("GCLK", "HCLK")              # [DS] Table 6-13 p73: GCLK always the same frequency as HCLK
RM4X_SYSTEM_SOURCES = ("OSCIN", "HFLPO")            # used directly if equal to the system clock, in this order
RM4X_PLL = "PLL1"                                   # otherwise PLL1 from OSCIN ([DS] Figure 6-6 p72)
RM4X_OSCILLATOR = "OSCIN"
RM4X_LIMP_SOURCE = "HFLPO"                          # [DS] 6.7 p78: OSCIN domains switch to HFLPO on OSC FAIL
# DCAN BTR: BRP with BRPE 1..1024, TSEG1 (prop + phase 1) 2..16, TSEG2 1..8, SJW 1..4, 8..25 tq per bit ([TRM] 26.3.2
# p1334, 26.17.4 Table 26-10 p1370). Like bxCAN there is no separate propagation segment: prop_seg stays 1 tq and
# the generator writes TSEG1 = prop_seg + phase_seg1 - 1.
DCAN_LIMITS = CanTimingLimits(prescaler=(1, 1024), prop_seg=(1, 1), phase_seg1=(1, 15), phase_seg2=(1, 8),
                              sjw=(1, 4), quanta=(8, 25))
# SCI and SCI/LIN asynchronous mode: baud = VCLK / (16 * (P + 1)), 24-bit prescaler P; P = 0 gives VCLK / 32 like
# P = 1, so the divider P + 1 starts at 2 ([TRM] 28.13.12 Table 28-23 p1587 equations 52/54, 29.7.11 Table 29-16
# p1641 equations 59/61). The fractional divider M of the SCI/LIN module is not used (the SCI module has none).
SCI_LIMITS = BaudLimits(oversampling=(16, 16), divider=(2, 1 << 24), preferred_oversampling=16)


def _stage_split(vco_hz: Fraction, total: int, pll) -> tuple[int, ...] | None:
    """Output divider `total` split over the stages of the PLL (rm4x OD, then R) with the limit between the first
    and the second stage; the smallest first stage wins. (total,) for a PLL with one stage, None if no split fits."""
    stages = pll.output_divider_stages
    if not stages:
        return (total,)

    def split(rest: int, index: int) -> tuple[int, ...] | None:
        low, high = stages[index]
        if index == len(stages) - 1:
            return (rest,) if low <= rest <= high else None
        for d in range(low, high + 1):
            if rest % d:
                continue
            if index == 0 and pll.post_divider_input_max_hz is not None and vco_hz / d > pll.post_divider_input_max_hz:
                continue
            tail = split(rest // d, index + 1)
            if tail is not None:
                return (d,) + tail
        return None

    return split(total, 0)


def _rm4x_pll(osc: int, pll, target: int, exact: bool = True):
    """(setting, stages) of PLL1 from the oscillator with output `target`; exact=False: the output dividers next to
    it (below and above) instead, for the suggestions of an error message."""
    outputs = set(pll.output_dividers)
    for div in range(pll.input_divider[0], pll.input_divider[1] + 1):
        pfd = Fraction(osc, div)
        if not pll.input_hz[0] <= pfd <= pll.input_hz[1]:
            continue
        for mult in range(pll.multiplier[0], pll.multiplier[1] + 1):
            vco = pfd * mult
            if not pll.vco_hz[0] <= vco <= pll.vco_hz[1]:
                continue
            ratio = vco / target
            if exact:
                candidates = {int(ratio)} if ratio.denominator == 1 else set()
            else:
                candidates = {int(ratio), int(ratio) + 1}
            for out in sorted(candidates & outputs):
                stages = _stage_split(vco, out, pll)
                if stages is not None:
                    yield PllSetting(div, mult, out, pfd, vco, vco / out), stages


def _rm4x_domain_order(domains: dict) -> list[str]:
    """Domains of clock.domain_dividers with parents and multiple_of first; a cycle is reported by checks.py."""
    order: list[str] = []

    def visit(name: str, path: tuple):
        if name in order or name not in domains or name in path:
            return
        entry = domains[name]
        for dep in (entry.parent, entry.multiple_of):
            if dep is not None:
                visit(dep, path + (name,))
        order.append(name)

    for name in domains:
        visit(name, ())
    return order


def _rm4x(cfg: Config, rep: Reporter, choices: dict[str, tuple[str, int]]) -> ClockPlan | None:
    clk = cfg.derivative.clock
    name = cfg.derivative.derivative.name
    file = cfg.derivative_file.name
    if clk is None:
        rep.error("derivative", ("derivative", "name"), f"{name}: no clock data (clock) in the derivative database, "
                  "needed by the rm4x clock model")
        return None
    if clk.source_numbers is None:
        rep.error("derivative", ("clock",), f"{name}: the rm4x clock model needs the numbers of the clock sources "
                  "(clock.source_numbers): the generator selects the source of GCLK/HCLK by number")
        return None
    numbers = clk.source_numbers.numbers
    target = cfg.project.clock.system_hz
    osc = cfg.board.board.clock.sosc_hz

    for dom in RM4X_SYSTEM_DOMAINS:
        limit = clk.max_hz.get(dom)
        if limit is not None and target > limit:
            rep.error("project", ("clock", "system_hz"), f"system clock {_mhz(target)} exceeds the maximum {dom} of "
                      f"{name} ({_mhz(limit)}, clock.max_hz.{dom} in {file}); {dom} runs at the system clock")
            return None

    monitor = clk.oscillator_monitor
    if osc is not None and monitor is not None and monitor.reference in clk.internal:
        ref = clk.internal[monitor.reference]
        low, high = Fraction(ref, monitor.factor), Fraction(ref * monitor.factor)
        if not low < osc < high:
            rep.error("board", ("board", "clock", "sosc_hz"), f"oscillator {_mhz(osc)} is outside the window of the "
                      f"clock monitor of {name} ({monitor.reference} {_mhz(ref)} / {monitor.factor} < f < "
                      f"{monitor.reference} * {monitor.factor}: {_mhz(low)}..{_mhz(high)}, clock.oscillator_monitor "
                      f"in {file}): the monitor would report an oscillator failure")
            return None

    base = {src: clk.internal[src] for src in RM4X_SYSTEM_SOURCES if src in clk.internal}
    if osc is not None:
        base[RM4X_OSCILLATOR] = osc
    source = next((src for src in RM4X_SYSTEM_SOURCES if base.get(src) == target), None)
    pll, stages = None, ()
    if source is None:
        if osc is None or clk.pll is None:
            rep.error("project", ("clock", "system_hz"), f"system clock {_mhz(target)} cannot be derived: "
                      + ("board " + cfg.board.board.name + " has no oscillator (board.clock.sosc_hz) for the PLL"
                         if osc is None else f"{name} has no PLL (clock.pll)")
                      + f", and no source runs at it directly (available: "
                      + f"{', '.join(f'{s} {_mhz(f)}' for s, f in base.items()) or '-'})")
            return None
        found = sorted(((pll_preference(s), s.output_divider, s, st) for s, st in _rm4x_pll(osc, clk.pll, target)),
                       key=lambda c: (c[0], c[1]))
        if not found:
            limits = [clk.max_hz[dom] for dom in RM4X_SYSTEM_DOMAINS if dom in clk.max_hz]
            near = {s.output_hz for s, _ in _rm4x_pll(osc, clk.pll, target, exact=False)
                    if s.output_hz.denominator == 1 and all(s.output_hz <= limit for limit in limits)}
            options = sorted(sorted(near, key=lambda f: abs(f - target))[:4])
            rep.error("project", ("clock", "system_hz"), f"system clock {target} Hz cannot be derived exactly "
                      f"from the oscillator {_mhz(osc)} with PLL1 of {name} (NR {clk.pll.input_divider[0]}.."
                      f"{clk.pll.input_divider[1]}, NF {clk.pll.multiplier[0]}..{clk.pll.multiplier[1]}, output "
                      f"divider stages and limits of clock.pll in {file}; nearest: "
                      f"{', '.join(f'{int(f)} Hz' for f in options) or '-'})")
            return None
        _, _, pll, stages = found[0]
        source = RM4X_PLL
    if source not in numbers:
        rep.error("derivative", ("clock", "source_numbers", "numbers"), f"{name}: the system clock source {source} "
                  f"has no number (available: {', '.join(numbers)})")
        return None

    core = Fraction(target)
    domains = {"core": core, **{dom: core for dom in RM4X_SYSTEM_DOMAINS}}
    dividers = {dom: 1 for dom in RM4X_SYSTEM_DOMAINS}
    sources = {src: Fraction(f) for src, f in base.items()}
    if pll is not None:
        sources[RM4X_PLL] = pll.output_hz
    for dom in _rm4x_domain_order(clk.domain_dividers):
        entry = clk.domain_dividers[dom]
        where = ("clock", "domain_dividers", dom)
        parent = domains.get(entry.parent, sources.get(entry.parent))
        if parent is None:
            rep.error("derivative", where + ("parent",), f"{name}: {dom} is divided from {entry.parent}, which the "
                      f"rm4x clock model does not provide (running: "
                      f"{', '.join(list(domains)[1:] + list(sources))})")
            return None
        limit = clk.max_hz.get(dom)
        other = domains.get(entry.multiple_of) if entry.multiple_of is not None else None
        chosen = next((d for d in entry.dividers
                       if (limit is None or parent / d <= limit) and (other is None or (parent / d / other)
                                                                        .denominator == 1)), None)
        if chosen is None:
            rules = ([f"to at most {_mhz(limit)}"] if limit is not None else []) + (
                [f"to an integer multiple of {entry.multiple_of} ({_mhz(other)})"] if other is not None else [])
            rep.error("project", ("clock", "system_hz"), f"system clock {_mhz(target)}: {dom} cannot be divided from "
                      f"{entry.parent} {_mhz(parent)} {' and '.join(rules)} (clock.max_hz.{dom}, dividers "
                      f"{entry.dividers[0]}..{entry.dividers[-1]} in clock.domain_dividers.{dom} of {file})")
            return None
        domains[dom], dividers[dom] = parent / chosen, chosen

    plan = ClockPlan("rm4x", target, source, osc, pll, domains, dividers)
    plan.sources = sources
    plan.pll_stages = stages
    plan.reset_core_hz = osc if osc is not None else clk.internal.get(RM4X_LIMP_SOURCE)  # GCLK source after reset

    fws = clk.flash_wait_states
    if fws is not None:
        hz = domains.get(fws.clock)
        band = next((b for b in fws.bands if hz is not None and hz <= b.max_hz), None)
        if hz is None:
            rep.error("derivative", ("clock", "flash_wait_states", "clock"), f"{name}: flash clock {fws.clock} is "
                      f"not set up by the rm4x clock model (available: {', '.join(domains)})")
            return None
        if band is None:
            rep.error("project", ("clock", "system_hz"), f"system clock {_mhz(target)}: {fws.clock} {_mhz(hz)} is "
                      f"above the last flash wait state band of {name} ({_mhz(fws.bands[-1].max_hz)}, "
                      f"clock.flash_wait_states in {file})")
            return None
        plan.flash_wait_states = band

    # Functional clock of the instances the project uses and of every CAN bus with bit timing (bootloader bus,
    # variants, ARCH-07). Only these are read: a derivative value is "used" when the project needs it (ARCH-37).
    used = used_instances(cfg)
    buses = cfg.selected("can")
    names = [b.name for b in buses]
    buses += [b for b in cfg.board.can if b.name not in names and b.name in cfg.project.can]
    needed = dict(used)
    for bus in buses:
        needed.setdefault(bus.instance, (f"can {bus.name}", ("can", bus.name)))
    ok = True
    for instance, (what, used_where) in needed.items():
        inst = cfg.derivative.instances.get(instance)
        if inst is None:
            continue                                        # checks.py reported the unknown instance
        entry = inst.clock
        path = ("instances", instance, "clock")
        if entry is None or entry.domain is None:
            rep.error("derivative", path, f"{name}: {instance} is used by {what} ({_fmt(used_where)} in "
                      f"{cfg.project_file.name}), but the rm4x clock model needs its clock domain ({_fmt(path)}"
                      f".domain in {file})")
            ok = False
            continue
        hz = domains.get(entry.domain)
        if hz is None:
            rep.error("derivative", path + ("domain",), f"{name}: {instance} (used by {what}) runs on {entry.domain}, "
                      f"which the rm4x clock model does not set up (no entry clock.domain_dividers.{entry.domain})")
            ok = False
            continue
        plan.peripheral_source[instance] = entry.domain
        plan.peripheral_hz[instance] = hz
        if inst.class_ == "timer":
            plan.peripheral_hz[f"{instance}.counter"] = hz
    if not ok:
        return None

    for bus in buses:
        clock_hz = plan.peripheral_hz[bus.instance]
        if clock_hz.denominator != 1:
            rep.error("project", ("can", bus.name), f"CAN {bus.name} ({bus.instance}): the DCAN clock "
                      f"{float(clock_hz):.1f} Hz is no whole number of Hz; choose another system clock")
            continue
        _can_timing(plan, rep, bus.name, int(clock_hz), cfg.project.can[bus.name], DCAN_LIMITS)
    for bus in cfg.selected("uart"):
        _uart_divider(plan, rep, bus.name, bus.instance, cfg.project.uart[bus.name].baudrate, SCI_LIMITS)
    return plan


MODELS = {"s32k1xx": _s32k1xx, "stm32": _stm32, "rm4x": _rm4x}


def check(cfg: Config, rep: Reporter) -> ClockPlan | None:
    """Clock checks of a project; the plan of the family model, None without model or after errors."""
    _limits(cfg, rep)
    if not _neutral(cfg, rep):
        return None
    before = sum(m.severity == "error" for m in rep.messages)
    choices = peripheral_choices(cfg, rep)              # ARCH-18, family neutral (also without a clock model)
    model = MODELS.get(cfg.derivative.derivative.family)
    if model is None:
        return None
    plan = model(cfg, rep, choices)
    after = sum(m.severity == "error" for m in rep.messages)
    return plan if after == before else None
