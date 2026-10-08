"""Checks of the UART channels of a project (ARCH-15): the UARTs the board port serves.

Every UART the project uses (use.uart) becomes an entry of the generated UART configuration
(bsp/uart/uartConfiguration.h): LPUART instance, baud rate register, receive interrupt and its NVIC level. The
board port builds Uart::Id, the configuration table, the receive buffers and the interrupt handlers from it, so
the board name of a UART must be usable as a C identifier without colliding with the fixed enumerators of
Uart::Id, and every used UART needs an interrupt rank. Family and board neutral, no Qt; every finding carries
file, YAML path and line.

These checks belong to tools/nxconfig/checks.py (one place for checks over entries and levels, so that the
Studio reports them without a generator run). checks.py is owned by another task at the moment (PLAT-22), so
the generators call check_uarts() directly - same arrangement as tools/nxconfig/can.py for ARCH-07.

Open proposal for the project schema (model.py, owned by PLAT-22): a console flag per used UART
(uart.<name>.console) so that a project with several UARTs can name the console instead of getting the first
used one. Until then check_uarts() warns as soon as the choice is not obvious.
"""

from .config import Config, Reporter

# Enumerators of bsp::Uart::Id that the board port defines itself (bspConfiguration/include/bsp/uart/UartConfig.h):
# INVALID closes the enumeration, TERMINAL is the alias of the console. A board UART of that name would clash.
RESERVED_UART_NAMES = ("TERMINAL", "INVALID")


def check_uarts(cfg: Config, rep: Reporter) -> None:
    """Reports what would keep a used UART from being served by the generated configuration."""
    uarts = cfg.selected("uart")
    index_of = {u.name: i for i, u in enumerate(cfg.board.uart)}
    ranked = {r.name for r in cfg.project.interrupts.ranks if r.source == "uart"}
    for uart in uarts:
        # The name of a UART becomes an enumerator of bsp::Uart::Id. Names are unique and C-usable already
        # (board schema Name, duplicate check of checks.py); only the fixed enumerators are left to check.
        if uart.name.upper() in RESERVED_UART_NAMES:
            rep.error("board", ("uart", index_of[uart.name], "name"),
                      f"UART name '{uart.name}' collides with the fixed enumerator bsp::Uart::Id::"
                      f"{uart.name.upper()} of the board port "
                      f"(reserved: {', '.join(RESERVED_UART_NAMES)})")
        # Every UART gets its receive interrupt enabled (UartRxBuffer, IO-17), so it needs an interrupt rank
        # (PLAT-21). Without one the build of the board port stops at the undefined level macro
        # NX_IRQ_LEVEL_UART_<uart>; the warning names the missing entry before that happens. Warning, not
        # error, because a project that only generates the configuration (test profiles) stays valid.
        if uart.name not in ranked:
            rep.warning("project", ("interrupts", "ranks"),
                        f"UART {uart.name} is used, but has no interrupt rank: the board port cannot set the "
                        f"level of its receive interrupt "
                        f"(interrupts.ranks: {{source: uart, name: {uart.name}, rank: <n>}})")
    # The console (stdin/stdout of the ECU, Uart::Id::TERMINAL) is the first used UART in board order. With one
    # used UART that is unambiguous; with several the project cannot say which one it means yet.
    if len(uarts) > 1:
        rep.warning("project", ("use", "uart"),
                    f"{len(uarts)} UARTs are used; the console (stdin/stdout) is the first one in board order, "
                    f"{uarts[0].name} on {uarts[0].instance}. The project cannot choose it yet (ARCH-15: a "
                    f"console flag per UART is proposed for the project schema)")
