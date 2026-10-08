"""Checks of the communication channels of a project (ARCH-07): the CAN buses the application serves.

Every CAN bus the project uses (use.can) becomes a bus id, a transceiver object, a console entry and a pair of
interrupt handlers of the generated configuration (busid/busIdConfiguration.h, bsp/can/canDeviceConfiguration.h).
That only works if the names of the buses are usable as C identifiers, are unique and do not collide with the
other bus ids of the ECU, and if there are not more buses than the interfaces can carry. Family and board
neutral, no Qt; every finding carries file, YAML path and line.

These checks belong to tools/nxconfig/checks.py (one place for checks over entries and levels, so that the
Studio reports them without a generator run). checks.py is owned by another task at the moment, so the
generators call check_buses() directly - see doc/changelog.d/ARCH-07.md.
"""

import re

from .config import Config, Reporter

# Bus ids of ecu/configuration (busid/BusId.h) that are not CAN buses: the names must stay free
RESERVED_BUS_NAMES = ("SELFDIAG", "ETH_0", "ETH_1", "LAST_BUS", "CAN_BUS_COUNT")
# Wake-up bits of the ECU state management (libs/bsw/ecuState EcuState.h, wakeup::canBus): bit 8 + bus index
MAX_CAN_BUSES = 8


def c_name(name: str) -> str:
    """C identifier of a bus name, same rule as tools/bswgen/modules.py identifier()."""
    ident = re.sub(r"\W", "_", name.upper())
    return f"SIG_{ident}" if ident[0].isdigit() else ident


def check_buses(cfg: Config, rep: Reporter) -> None:
    """Reports what would keep a used CAN bus from being served by the generated configuration."""
    buses = cfg.selected("can")
    index_of = {bus.name: i for i, bus in enumerate(b for b in cfg.board.can)}
    if len(buses) > MAX_CAN_BUSES:
        rep.error("project", ("use", "can"), f"{len(buses)} CAN buses are used, at most {MAX_CAN_BUSES} are "
                  f"supported (wake-up bit per bus, libs/bsw/ecuState)")
    ranked = {r.name for r in cfg.project.interrupts.ranks if r.source == "can"}
    for bus in buses:
        # The name of a bus becomes its bus id (busid::<name>), so the ids of the other channels are taken.
        # Bus names are unique and C-usable already (board schema Name, duplicate check of checks.py).
        ident = c_name(bus.name)
        if ident in RESERVED_BUS_NAMES:
            rep.error("board", ("can", index_of[bus.name], "name"),
                      f"CAN bus name '{bus.name}' collides with the bus id busid::{ident} of the ECU "
                      f"(reserved: {', '.join(RESERVED_BUS_NAMES)})")
        # Every bus gets its message buffer interrupts enabled (CanSystem), so it needs an interrupt rank
        # (PLAT-21). Without one the build of the board port stops at the undefined level macro
        # NX_IRQ_LEVEL_CAN_<bus>; the warning names the missing entry before that happens. Warning, not
        # error, because a project that only generates the configuration (test profiles) stays valid.
        if bus.name not in ranked:
            rep.warning("project", ("interrupts", "ranks"),
                        f"CAN bus {bus.name} is used, but has no interrupt rank: the board port cannot set "
                        f"the level of its message buffer interrupts "
                        f"(interrupts.ranks: {{source: can, name: {bus.name}, rank: <n>}})")
