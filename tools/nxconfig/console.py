"""Console and logger of the application as a function of the project (PLAT-27).

project.yaml `console:` decides two things the build used to fix:

- `enabled` - whether the application has a console (command input on the terminal UART, the console commands of
  ecu/application) and a logger (ring buffer, formatter, output in the idle task). Without them every log call of
  the BSW finds no component mapping and does nothing (util::logger::Logger::isEnabled is false before
  Logger::init). The terminal UART stays initialised in every image: the start-up lines and the safety line of the
  safe supervisor (safety::safeConsoleWrite, "SAFETY RESET ...") are written to it synchronously and do not depend on
  console or logger.
- `logger_buffer_bytes` - the size of the logger ring buffer. The root CMakeLists.txt (PLAT-04 block) writes the
  generated logger/Config.h from NX_LOGGER_BUFFER_BYTES; this module hands the project value over (project.cmake).
  Without a value the default of the root CMakeLists.txt applies and the image is the one before PLAT-27.

Family and board neutral, no Qt; every finding carries file, YAML path and line (Reporter).
"""

from __future__ import annotations

from .config import Config, Reporter
from .model import LOGGER_BUFFER_DEFAULT_BYTES

SECTION = "console"


def enabled(cfg: Config) -> bool:
    """Console and logger in the image (default: yes)."""
    console = cfg.project.console
    return console is None or console.enabled


def check(cfg: Config, rep: Reporter) -> None:
    """Findings on project.yaml console (the schema checks type and bounds of the buffer size)."""
    console = cfg.project.console
    if console is None or console.logger_buffer_bytes is None:
        return
    size = console.logger_buffer_bytes
    if not console.enabled:
        rep.warning("project", (SECTION, "logger_buffer_bytes"),
                    f"logger_buffer_bytes {size} has no effect: console.enabled is false, the image has no logger")
        return
    layout = cfg.memory
    if layout is not None and layout.app_ram:
        total = sum(r.size for r in layout.app_ram)
        if size >= total:
            rep.error("project", (SECTION, "logger_buffer_bytes"),
                      f"logger ring buffer {size} B does not fit into the application RAM of {total} B "
                      f"({', '.join(r.region for r in layout.app_ram)})")
            return
    if size < LOGGER_BUFFER_DEFAULT_BYTES:
        rep.warning("project", (SECTION, "logger_buffer_bytes"),
                    f"logger ring buffer {size} B is below the default {LOGGER_BUFFER_DEFAULT_BYTES} B: the start-up "
                    f"may drop log lines before the console prints them (eval board 2026-09-18: 4096 B lost nine "
                    f"start-up lines); confirm on the board with the console command `logger lost` after a start")


def settings(cfg: Config) -> dict[str, str]:
    """CMake values of the build (project.cmake): NX_CONSOLE always, NX_LOGGER_BUFFER_BYTES only from the project.

    Without a project value NX_LOGGER_BUFFER_BYTES stays unset, so the root CMakeLists.txt keeps its default (and an
    explicit -DNX_LOGGER_BUFFER_BYTES of a test build still works); a project value is a normal CMake variable and
    wins over the cache entry.
    """
    on = enabled(cfg)
    values = {"NX_CONSOLE": "ON" if on else "OFF"}
    console = cfg.project.console
    if on and console is not None and console.logger_buffer_bytes is not None:
        values["NX_LOGGER_BUFFER_BYTES"] = str(console.logger_buffer_bytes)
    return values
