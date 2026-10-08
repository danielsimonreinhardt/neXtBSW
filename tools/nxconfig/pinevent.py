# SPDX-License-Identifier: Apache-2.0
"""Events on digital inputs (pin interrupts), IO-51; concept doc/architecture/pin-interrupts.md section 4.3.

The project enables events per board input (project.yaml `pin_events`, model.PinEvent); the derivative says which
pins can raise an interrupt and how their edge detectors reach the vectors (mcudb `pin_interrupts`,
model.DerivativePinInterrupts). Checks per event, all with YAML path:

  - the key is a digital input of the board (not an output), used by the project (use.dio)     error
  - the input has a rank (interrupts.ranks, source dio) - the priority of its vector           error
  - the derivative describes pin interrupts, and the pin can reach a vector                    error
  - the derivative's edge detectors can react to the physical edge (edge + board active level) error
    and, with debounce_us > 0, to both edges (level confirmation, IO-54)                       error
  - pins that end in the same vector have the same rank - Hall signals (module hall) included,
    they use the same vectors; pins that need the same line (stm32 EXTI n, s32k3xx EIRQ n)
    exclude each other                                                                          error
  - one consumer per pin: not a Hall signal as well (input capture and every other pin user
    are refused by the pin claim of checks._resources)                                        error
  - debounce_us shorter than the shortest spacing the rate limit allows (window / max_events)   error
  - wakeup: true is reserved until an MCU low-power mode exists                                 error
  - on a VIM core the vector is a request of the derivative's VIM (vim.requests)                error (derivative)

Value ranges (debounce, events, window, hold-off) are limits of the schema. check() returns the plan per event for
the generators (IO-54); info() gives the reader one line per event (nxconfig check).
"""

from dataclasses import dataclass

from .config import CORES, Config, Reporter
from .model import DerivativePinInterrupts, PinInterruptRoute

HALL_MODULE = "hall"                # module whose connected inputs use the pin interrupts of their vector (IO-07)
PHYSICAL_EDGE = {("activate", "high"): "rising", ("activate", "low"): "falling",
                 ("deactivate", "high"): "falling", ("deactivate", "low"): "rising"}


@dataclass(frozen=True)
class PinEventUse:
    """Checked event of one board input: what the generators need (IO-52..IO-54)."""

    input: str
    pin: str
    active: str                     # active level of the board input, high/low
    edge: str                       # project: activate/deactivate/both
    physical_edge: str              # rising/falling/both
    vector: str                     # vector (NVIC) or request (VIM) of the derivative
    line: str | None                # line of the derivative, None: the pin has its own edge detector
    rank: int
    debounce_us: int
    max_events: int
    window_ms: int
    rearm_ms: int
    shares: tuple[str, ...]         # other inputs (events and Hall signals) in the same vector

    @property
    def max_rate_per_s(self) -> float:
        return self.max_events * 1000 / self.window_ms


@dataclass
class _Consumer:
    input: str
    pin: str
    rank: int | None
    what: str                       # "pin event" or "hall <device>"
    routes: list[PinInterruptRoute]
    route: PinInterruptRoute | None = None


def _signal_pins(cfg: Config):
    from .checks import pinmux          # late: checks imports this module
    table = pinmux(cfg, Reporter())

    def pins(signal: str):
        return None if table is None else set(table.signals.get(signal, {}))
    return pins, table is not None


def check(cfg: Config, rep: Reporter) -> list[PinEventUse]:
    """Pin events of the project; entries with errors are left out."""
    events = cfg.project.pin_events
    if not events:
        return []
    inputs = {io.name: io for io in cfg.board.dio.inputs}
    outputs = {io.name for io in cfg.board.dio.outputs}
    used = {io.name for io in cfg.selected("dio.inputs")}
    ranks = {r.name: r.rank for r in cfg.project.interrupts.ranks if r.source == "dio"}
    facts: DerivativePinInterrupts | None = cfg.derivative.pin_interrupts
    derivative = cfg.derivative.derivative.name
    signal_pins, have_table = _signal_pins(cfg)

    hall: dict[str, str] = {}
    integration = cfg.integration
    for dev in (integration.instances(HALL_MODULE) if integration is not None else []):
        for name in dev.connect.values():
            hall[name] = dev.name

    candidates: list[tuple[str, _Consumer]] = []
    reported: set[str] = set()
    for name, event in events.items():
        base = ("pin_events", name)
        if name not in inputs:
            if name in outputs:
                rep.error("project", base, f"pin event {name}: {name} is a digital output of board "
                          f"{cfg.board.board.name}; events exist only on digital inputs (dio.inputs)")
            else:
                rep.error("project", base, f"pin event {name}: board {cfg.board.board.name} has no digital input "
                          f"{name} (available: {', '.join(inputs) or '-'})")
            continue
        io = inputs[name]
        if name not in used:
            rep.error("project", base, f"pin event {name}: the project does not use the input (use.dio)")
        if event.wakeup:
            rep.error("project", base + ("wakeup",),
                      f"pin event {name}: wakeup is reserved for an MCU low-power mode, which neXtBSW does not have "
                      f"yet - the ECU sleeps through its supply and wakes through the SBC's local wake inputs "
                      f"(ecu.wakeup_local) or CAN; remove wakeup or set it to false (concept pin-interrupts 3.5)")
        if event.debounce_us * event.max_events >= event.window_ms * 1000:
            rep.error("project", base + ("debounce_us",),
                      f"pin event {name}: debounce_us {event.debounce_us} must be shorter than the shortest spacing "
                      f"the rate limit allows ({event.window_ms} ms / {event.max_events} events = "
                      f"{event.window_ms * 1000 / event.max_events:g} us); otherwise the debounce and not the rate "
                      f"limit bounds the events")
        if name in hall:
            rep.error("project", base, f"pin event {name}: {name} ({io.pin}) is a signal of the Hall sensor "
                      f"{hall[name]} (module {HALL_MODULE}); a pin has one consumer of its interrupt")
            continue
        if name not in ranks:
            rep.error("project", base, f"pin event {name}: the input has no interrupt rank; add "
                      f"{{source: dio, name: {name}, rank: <n>}} to interrupts.ranks (the rank is the priority of "
                      f"the pin interrupt vector)")
        if facts is None:
            rep.error("project", base, f"pin event {name}: derivative {derivative} does not describe which pins can "
                      f"raise an interrupt (mcudb pin_interrupts)")
            continue
        physical = "both" if event.edge == "both" else PHYSICAL_EDGE[(event.edge, io.active)]
        if physical not in facts.edges:
            rep.error("project", base + ("edge",),
                      f"pin event {name}: edge {event.edge} of an active-{io.active} input is a {physical} edge, "
                      f"which the edge detectors of {derivative} cannot detect (edges: {', '.join(facts.edges)})")
        elif event.debounce_us > 0 and "both" not in facts.edges:
            # IO-54: the debounce confirms the level after the lock-out from the last change inside it, so the edge
            # detector has to report every change (pinEvent detectorEdges(): both edges while debouncing)
            rep.error("project", base + ("debounce_us",),
                      f"pin event {name}: debounce_us {event.debounce_us} needs an edge detector that reports both "
                      f"edges (the level after the lock-out is confirmed from the last change inside it), which "
                      f"{derivative} cannot (edges: {', '.join(facts.edges)}); set debounce_us to 0")
        routes = facts.routes(io.pin, signal_pins)
        if not routes:
            hint = "" if have_table else f"; the package {cfg.board.board.package} has no pin multiplexing table"
            rep.error("project", base, f"pin event {name}: pin {io.pin} of {derivative} cannot raise an interrupt "
                      f"(mcudb pin_interrupts{hint})")
            continue
        _vim_requests(cfg, rep, routes, reported)
        candidates.append((name, _Consumer(name, io.pin, ranks.get(name), "pin event", routes)))

    if facts is None or not candidates:
        return []
    consumers = [c for _, c in candidates]
    # Hall signals occupy their vectors first: their port and rank are fixed by the Hall driver (bswgen)
    halls = []
    for name, device in hall.items():
        io = inputs.get(name)
        if io is None or name in events:
            continue
        routes = facts.routes(io.pin, signal_pins)
        if routes and name in ranks:
            halls.append(_Consumer(name, io.pin, ranks[name], f"hall {device}", routes))
    failed = _assign(rep, halls + sorted(consumers, key=lambda c: len(c.routes)), derivative)

    result = []
    for name, consumer in candidates:
        if consumer.route is None or name in failed or consumer.rank is None:
            continue
        event, io = events[name], inputs[name]
        shares = tuple(c.input for c in halls + consumers
                       if c is not consumer and c.route is not None and c.route.vector == consumer.route.vector)
        result.append(PinEventUse(
            name, io.pin, io.active, event.edge,
            "both" if event.edge == "both" else PHYSICAL_EDGE[(event.edge, io.active)],
            consumer.route.vector, consumer.route.line, consumer.rank, event.debounce_us, event.max_events,
            event.window_ms, event.rearm_ms, shares))
    return result


def _assign(rep: Reporter, consumers: list[_Consumer], derivative: str) -> set[str]:
    """Gives each consumer a route: a line serves one pin, a vector has one rank. Returns the inputs that got none."""
    line_owner: dict[str, _Consumer] = {}
    vector_rank: dict[str, _Consumer] = {}
    failed: set[str] = set()
    for c in consumers:
        blocked_line, blocked_rank = None, []
        for route in c.routes:
            if route.line is not None and route.line in line_owner:
                blocked_line = blocked_line or route
                continue
            owner = vector_rank.get(route.vector)
            if owner is not None and c.rank is not None and owner.rank != c.rank:
                if route.vector not in blocked_rank:
                    blocked_rank.append(route.vector)
                continue
            c.route = route
            if route.line is not None:
                line_owner[route.line] = c
            if c.rank is not None:
                vector_rank.setdefault(route.vector, c)
            break
        if c.route is not None or c.what != "pin event":
            continue
        failed.add(c.input)
        path = ("pin_events", c.input)
        if blocked_rank:
            taken = []
            for vector in blocked_rank:
                owner = vector_rank[vector]
                members = [x.input for x in consumers if x.route is not None and x.route.vector == vector]
                taken.append(f"vector {vector} of {derivative} already serves {', '.join(members)} with rank "
                             f"{owner.rank} ({owner.what})")
            alone = len(blocked_rank) == 1
            rep.error("project", path,
                      f"pin event {c.input}: {c.input} ({c.pin}) has interrupt rank {c.rank}, but "
                      f"{'; '.join(taken)}; one vector has one priority: give "
                      + (f"all inputs of {blocked_rank[0]} the same rank" if alone else
                         f"the inputs at most {len(blocked_rank)} different ranks") + " (interrupts.ranks)")
        else:
            owner = line_owner[blocked_line.line]
            rep.error("project", path,
                      f"pin event {c.input}: {c.input} ({c.pin}) needs line {blocked_line.line} of {derivative}, "
                      f"which {owner.input} ({owner.pin}, {owner.what}) already uses; pins that share a line exclude "
                      f"each other, move one of the inputs to another pin number")
    return failed


def _vim_requests(cfg: Config, rep: Reporter, routes: list[PinInterruptRoute], reported: set[str]):
    """On a VIM core the vector is a request the family generator moves to the channel of its rank."""
    core = CORES.get(cfg.derivative.derivative.core)
    vim = cfg.derivative.vim
    if core is None or core.interrupt_controller != "vim" or vim is None:
        return
    for route in routes:
        if route.vector not in vim.requests and route.vector not in reported:
            reported.add(route.vector)
            rep.error("derivative", ("pin_interrupts",),
                      f"pin interrupt vector {route.vector} is no request of the VIM of "
                      f"{cfg.derivative.derivative.name} (vim.requests)")


def info(cfg: Config) -> list[str]:
    """One readable line per pin event (nxconfig check, not a message)."""
    lines = []
    for ev in getattr(cfg, "pin_events", None) or []:
        edge = ev.edge if ev.edge == "both" else f"{ev.edge} = {ev.physical_edge} (active {ev.active})"
        route = f"vector {ev.vector}" + (f" via line {ev.line}" if ev.line else "")
        shared = f", shared with {', '.join(ev.shares)}" if ev.shares else ""
        debounce = f"debounce {ev.debounce_us} us" if ev.debounce_us else "no debounce"
        lines.append(f"pin event {ev.input} ({ev.pin}): edge {edge}, {route}{shared}, rank {ev.rank}, {debounce}, "
                     f"at most {ev.max_events} per {ev.window_ms} ms ({ev.max_rate_per_s:g}/s), re-armed "
                     f"{ev.rearm_ms} ms after the limit trips")
    return lines
