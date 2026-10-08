"""I2C buses of a project (IO-28): rate and time limit per used bus, and on a FlexIO the resources of the bus driver.

Every I2C bus the project uses (use.i2c) gets a plan the generator renders into bsp/i2c/i2cConfiguration.h:

- rate and time limit from the project (`i2c.<bus>.baudrate_hz`, `timeout_ms`). A used bus without an entry runs at
  DEFAULT_BAUDRATE_HZ (I2C standard mode, which every I2C device supports); no finding, info() names the default;
- on a FlexIO instance: the SCL divider of the family driver from the functional clock of the clock plan, the
  FlexIO data pins from the board (`i2c[].sda/scl.flexio_pin`) and two shifters and two timers per bus, handed out
  in board order. Several buses may share one FlexIO as long as its shifters and timers last.

Which FlexIO resources a family has and how its driver counts is family knowledge and lives in FLEXIO_FAMILIES
(s32k1xx: platforms/s32k1xx/bsp/bspFlexioI2c, reference manual Rev. 13 chapter 54). A family without an entry
gets no FlexIO plan; an I2C bus on an instance its port has no driver for is reported by checks._i2c_drivers
(TOOL-120). Family and board neutral otherwise, no Qt; every finding carries file, YAML path and line.
"""

from dataclasses import dataclass
from fractions import Fraction

from .config import Config, Reporter

DEFAULT_BAUDRATE_HZ = 100_000          # I2C standard mode
MAX_BAUDRATE_HZ = 1_000_000            # I2C Fast-mode Plus; faster modes are not I2C bus modes a master drives here


@dataclass(frozen=True)
class FlexioI2cFamily:
    """FlexIO of a family and the numbers of its I2C master driver (the C++ constants in parentheses)."""

    driver: str                  # for messages
    shifters: int                # shifters per FlexIO (PARAM[SHIFTER]); two per bus
    timers: int                  # timers per FlexIO (PARAM[TIMER]); SCL timer and the shift timer after it
    timer_chain: int             # the shift timer (SCL timer + 1) is no multiple of it (TIMERS_PER_ENABLE_CHAIN)
    min_compare: int             # TIMCMP[7:0] at the fastest rate, f / ((n + 1) * 2) (MIN_BAUD_COMPARE)
    max_compare: int             # TIMCMP[7:0] at the slowest rate (MAX_BAUD_COMPARE)
    max_bytes: int               # data bytes per call of write()/read() (MAX_BYTES_PER_TRANSFER)


# s32k1xx: reference manual Rev. 13, 54.3.1.3 PARAM (4 shifters, 4 timers on every S32K1xx), 54.3.1.20.2 (timer N-1
# reserved for N divisible by 4), 54.5.5 (f/6 and the 8-bit edge counter), constants of
# platforms/s32k1xx/bsp/bspFlexioI2c/include/i2c/FlexioI2cTiming.h. The driver checks the resources against PARAM of
# the chip again at init().
FLEXIO_FAMILIES = {
    "s32k1xx": FlexioI2cFamily("the FlexIO I2C master (platforms/s32k1xx/bsp/bspFlexioI2c)",
                               shifters=4, timers=4, timer_chain=4, min_compare=2, max_compare=0xFF, max_bytes=13),
}


@dataclass
class I2cBusPlan:
    name: str
    instance: str
    baudrate_hz: int             # requested upper limit of SCL
    timeout_ms: int
    defaulted: bool              # no i2c.<bus> entry in the project: DEFAULT_BAUDRATE_HZ and the default time limit
    verify: bool
    # FlexIO only (None elsewhere)
    clock_hz: int | None = None  # functional clock of the FlexIO
    compare: int | None = None   # TIMCMP[7:0] of the SCL timer
    scl_hz: int | None = None    # nominal SCL frequency, at or below baudrate_hz
    sda_pin: int | None = None
    scl_pin: int | None = None
    tx_shifter: int | None = None
    rx_shifter: int | None = None
    scl_timer: int | None = None
    max_bytes: int | None = None

    @property
    def timeout_us(self) -> int:
        return self.timeout_ms * 1000


def flexio_baud(family: FlexioI2cFamily, clock_hz: int, requested_hz: int) -> tuple[int, int] | None:
    """(TIMCMP[7:0], SCL Hz) of the fastest rate at or below `requested_hz`, None if none exists.

    Same arithmetic as flexio::calculateBaud(): SCL = f / ((compare + 1) * 2), compare rounded up so that the rate
    never exceeds the request, at least min_compare. A request below the slowest rate is refused, not rounded up:
    an I2C bus must never run faster than its slowest device.
    """
    if clock_hz <= 0 or requested_hz <= 0:
        return None
    half = max(-(-clock_hz // (2 * requested_hz)), family.min_compare + 1)
    if half > family.max_compare + 1:
        return None
    return half - 1, clock_hz // (2 * half)


def min_timeout_us(max_bytes: int, scl_hz: int) -> int:
    """Longest transfer of the driver: address byte, max_bytes data bytes and the STOP/repeated START word, nine
    SCL periods each (the rule of FlexioI2cConfig.timeoutUs), rounded up to whole microseconds."""
    return -(-(max_bytes + 2) * 9 * 1_000_000 // scl_hz)


def check(cfg: Config, rep: Reporter) -> list[I2cBusPlan]:
    """Plans of the used I2C buses in board order; findings go to `rep`."""
    board_names = [b.name for b in cfg.board.i2c]
    used = cfg.selected("i2c")
    used_names = [b.name for b in used]
    for key in cfg.project.i2c:
        if key not in board_names:
            rep.error("project", ("i2c", key), f"board {cfg.board.board.name} has no i2c entry {key} "
                      f"(available: {', '.join(board_names) or '-'})")
        elif key not in used_names:
            rep.warning("project", ("i2c", key), f"i2c {key} is not used (use.i2c), setting ignored")

    plans = []
    for bus in used:
        use = cfg.project.i2c.get(bus.name)
        if use is None:
            # No finding: standard mode is the rate every I2C device supports. info() says it, so the reader
            # sees that the rate is a default and not a value of the project.
            plan = I2cBusPlan(bus.name, bus.instance, DEFAULT_BAUDRATE_HZ, 20, True, False)
        else:
            plan = I2cBusPlan(bus.name, bus.instance, use.baudrate_hz, use.timeout_ms, False, use.verify)
        if plan.baudrate_hz > MAX_BAUDRATE_HZ:
            rep.error("project", ("i2c", bus.name, "baudrate_hz"), f"I2C {bus.name}: {plan.baudrate_hz} Hz is above "
                      f"{MAX_BAUDRATE_HZ} Hz, the fastest I2C bus mode (Fast-mode Plus)")
            continue
        plans.append(plan)
    _flexio(cfg, rep, plans)
    return plans


def _flexio(cfg: Config, rep: Reporter, plans: list[I2cBusPlan]):
    """Divider, time limit and resources of the buses on a FlexIO of a family in FLEXIO_FAMILIES."""
    d = cfg.derivative
    family = FLEXIO_FAMILIES.get(d.derivative.family)
    if family is None:
        return
    board = {b.name: (i, b) for i, b in enumerate(cfg.board.i2c)}
    selection = cfg.project.use.i2c
    taken: dict[str, list[str]] = {}                   # FlexIO instance -> buses on it, in board order
    for plan in plans:
        inst = d.instances.get(plan.instance)
        if inst is None or inst.class_ != "flexio":
            continue
        index, bus = board[plan.name]
        where = ("i2c",) if plan.defaulted else ("i2c", plan.name, "baudrate_hz")
        clock_hz = cfg.clock.peripheral_hz.get(plan.instance) if cfg.clock is not None else None
        if clock_hz is None:
            rep.error("project", where, f"I2C {plan.name}: no functional clock known for {plan.instance} "
                      f"(clock plan, peripheral_clocks.{plan.instance})")
            continue
        clock_hz = int(Fraction(clock_hz))
        baud = flexio_baud(family, clock_hz, plan.baudrate_hz)
        if baud is None:
            fastest = clock_hz // (2 * (family.min_compare + 1))
            slowest = clock_hz // (2 * (family.max_compare + 1))
            rep.error("project", where, f"I2C {plan.name}: {plan.baudrate_hz} Hz is below the slowest SCL rate of "
                      f"{family.driver} at {plan.instance} clock {clock_hz} Hz ({slowest}..{fastest} Hz); "
                      f"a faster FlexIO clock is not the way out, a slower one is (peripheral_clocks.{plan.instance})")
            continue
        plan.clock_hz, (plan.compare, plan.scl_hz) = clock_hz, baud
        plan.max_bytes = family.max_bytes
        needed = min_timeout_us(family.max_bytes, plan.scl_hz)
        if plan.timeout_us < needed:
            rep.error("project", ("i2c", plan.name, "timeout_ms") if not plan.defaulted else ("i2c",),
                      f"I2C {plan.name}: time limit {plan.timeout_ms} ms is shorter than the longest transfer of "
                      f"{family.driver} at {plan.scl_hz} Hz ({needed} us for {family.max_bytes} data bytes)")
            continue
        plan.sda_pin, plan.scl_pin = bus.sda.flexio_pin, bus.scl.flexio_pin
        clash = next(((role, other) for role, pin in (("sda", plan.sda_pin), ("scl", plan.scl_pin))
                      for other in plans if other is not plan and other.instance == plan.instance
                      and other.scl_timer is not None and pin in (other.sda_pin, other.scl_pin)), None)
        if clash is not None:
            role, other = clash
            rep.error("board", ("i2c", index, role, "flexio_pin"), f"I2C {plan.name}: {plan.instance} data pin "
                      f"D{getattr(bus, role).flexio_pin} is used by I2C {other.name} as well")
            continue
        slot = len(taken.setdefault(plan.instance, []))
        resources = _resources(family, slot)
        if resources is None:
            path = ("use", "i2c") if selection == "all" else ("use", "i2c", list(selection).index(plan.name))
            rep.error("project", path, f"I2C {plan.name}: {plan.instance} has no shifters and timers left - "
                      f"{family.driver} needs 2 shifters and 2 timers per bus, {plan.instance} has "
                      f"{family.shifters} and {family.timers}, already used by "
                      f"{', '.join(taken[plan.instance])}; put the bus on another instance (board.yaml "
                      f"i2c[{index}].instance) or leave it out of use.i2c")
            continue
        taken[plan.instance].append(plan.name)
        plan.tx_shifter, plan.rx_shifter, plan.scl_timer = resources


def _resources(family: FlexioI2cFamily, slot: int) -> tuple[int, int, int] | None:
    """(transmit shifter, receive shifter, SCL timer) of the `slot`-th bus on one FlexIO, None if it does not fit.

    Shifters in pairs; the SCL timer is the first timer whose follower (the shift timer) is free of the reserved
    enable chain, the timers of earlier slots skipped.
    """
    if 2 * slot + 1 >= family.shifters:
        return None
    timers = [t for t in range(family.timers - 1) if (t + 1) % family.timer_chain != 0]
    free, used = [], set()
    for t in timers:
        if t not in used and t + 1 not in used:
            free.append(t)
            used.update((t, t + 1))
    if slot >= len(free):
        return None
    return 2 * slot, 2 * slot + 1, free[slot]


def info(cfg: Config) -> list[str]:
    """One line per used I2C bus for the reader of `nxconfig check` (no finding, not counted, not in --json)."""
    lines = []
    for p in cfg.i2c_buses:
        rate = (f"at most {p.baudrate_hz} Hz (no i2c.{p.name} in the project: standard mode)" if p.defaulted
                else f"at most {p.baudrate_hz} Hz" + (" (verify: true)" if p.verify else ""))
        if p.scl_timer is None:
            lines.append(f"I2C {p.name} on {p.instance}: {rate}, time limit {p.timeout_ms} ms")
            continue
        lines.append(f"I2C {p.name} on {p.instance}: {rate} -> SCL {p.scl_hz} Hz ({p.clock_hz} Hz / "
                     f"(({p.compare} + 1) * 2)), time limit {p.timeout_ms} ms, at most {p.max_bytes} bytes per "
                     f"transfer, shifters {p.tx_shifter}/{p.rx_shifter}, timers {p.scl_timer}/{p.scl_timer + 1}")
    return lines
