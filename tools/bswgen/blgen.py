"""Generates the memory layout and the board part of the bootloader (doc/architecture/README.md, section 7).

One source: derivative memory (mcudb) + project `memory` and `bootloader`, checked and calculated by
tools/nxconfig/memory.py, bit timing and clocks from the clock model (tools/nxconfig/clock.py).

Family neutral (every project with `memory`), written by bswgen.generate():
- bootloader/bl_memory.h    C macros of the bootloader core, updater, application (bl_config.h) and canflash
- bootloader/bl_memory.ld   linker symbols of the bootloader and updater linker scripts (INCLUDE)
- memory/memoryLayout.h     preprocessor values of the application linker script (no C suffixes)
- bootloader/bl_diag.h      diagnostic addresses (COM-11): UDS CAN ids, ISO-TP, bootloader identification and
                            the flash area the application searches, from project `diagnostics`
                            (tools/nxconfig/diag.py) and the transport of the bootloader core (BL-14);
                            read by bootloader, ecu/uds and canflash
- bootloader/bl_security.h  security access (BL-03): method, name of the master key on the workstation, chip UID of
                            the derivative; no key material

Family s32k1xx (projects with `bootloader`), written by the command line from bootloader/CMakeLists.txt, which
may replace the bus (build option BOOTLOADER_CAN_BUS):
- bootloader/bl_board.h     reset clock, CAN instance, pins, CTRL1, device hooks (bootloader/devices/<module>)
- bootloader/bl_board.cmake bus and device hook modules for the bootloader build
- bootloader/bl_sections.ld section statements that depend on the core (BL-30): code in the gap between an ARMv6-M
                            vector table and the flash configuration field, INCLUDEd by the family linker script

Generated is wiring and register values, the logic stays in bootloader/platforms/<family> and devices/<module>.
"""

import argparse
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import nxconfig  # noqa: E402
from nxconfig import genlicense  # noqa: E402  (PLAT-120: licence line of generated files)
from nxconfig import checks as nxchecks  # noqa: E402
from nxconfig import diag as nxdiag  # noqa: E402
from nxconfig import memory as nxmemory  # noqa: E402
from nxconfig.config import CORES, Reporter  # noqa: E402

MEMORY_HEADER = "bootloader/bl_memory.h"
MEMORY_LINKER = "bootloader/bl_memory.ld"
APP_LINKER_HEADER = "memory/memoryLayout.h"
DIAG_HEADER = "bootloader/bl_diag.h"
SECURITY_HEADER = "bootloader/bl_security.h"
BOARD_HEADER = "bootloader/bl_board.h"
BOARD_CMAKE = "bootloader/bl_board.cmake"
BOARD_SECTIONS = "bootloader/bl_sections.ld"

# Transports the bootloader core can be built with (BL-14); the generated bl_diag.h selects exactly one.
TRANSPORTS = ("can", "link")

S32K1_PIN_RE = re.compile(r"^PT([A-E])(\d{1,2})$")
S32K1_FLEXCAN_RE = re.compile(r"^FlexCAN(\d)$")
S32K1_LPSPI_RE = re.compile(r"^LPSPI(\d)$")
# LPSPI of a bootloader device hook: SCK at most this rate (conservative for any board wiring)
BOOTLOADER_SPI_MAX_HZ = 500_000
LPSPI_MIN_SCKDIV = 8            # SCK period of at least 10 prescaled clock periods (delays = one SCK period)


class ConfigError(Exception):
    pass


def _env() -> Environment:
    return Environment(loader=FileSystemLoader(HERE / "templates"), undefined=StrictUndefined,
                       keep_trailing_newline=True, trim_blocks=True, lstrip_blocks=True)


def source_label(config: "nxconfig.Config") -> str:
    """Where the generated file comes from: the project path, relative to its workspace resp. the tree (ARCH-57).

    A project of a workspace outside the neXtBSW tree (customer-project.md N4) is not below nxconfig.REPO; its
    label is then relative to the workspace root, so the generated header names the same path the customer sees.
    """
    roots = [nxconfig.REPO]
    workspace = getattr(config, "workspace", None)
    if workspace is not None:
        roots.insert(0, workspace.root)
    for root in roots:
        try:
            return f"{config.project_file.relative_to(root).as_posix()} (board {config.board.board.name})"
        except ValueError:
            continue
    return f"{config.project_file.name} (board {config.board.board.name})"


# ----------------------------------------------------------------------------------------------- memory layout


def memory_values(layout: "nxmemory.MemoryLayout") -> dict[str, int]:
    """Values of bootloader/bl_memory.h by macro name (canflash reads the same names from the header)."""
    return {
        "BL_FLASH_START": layout.flash_start,
        "BL_FLASH_SIZE": layout.flash_size,
        "BL_SECTOR_SIZE": layout.sector_size,
        "BL_PHRASE_SIZE": layout.program_size,
        "BL_APP_START": layout.app_start,
        "BL_APP_END": layout.app_end,
        "BL_APP_RECORD_ADDR": layout.record_address,
        "BL_RAM_START": layout.ram_start,
        "BL_RAM_END": layout.ram_end,
        "BL_BOOT_REQUEST_ADDR": layout.boot_request_address,
        # BL-03: key sector of the device key (not part of layoutId: the ten values above are)
        "BL_KEY_ADDR": layout.key_address,
        "BL_KEY_AREA_SIZE": layout.key_size,
    }


def hardware_id(board: str, derivative: str) -> str:
    """BL_HARDWARE_ID of bl_memory.h (BL-10): "<board>/<derivative>" in plain text, what the bootloader answers for
    DID 0xF191. BL_TARGET_ID is the CRC-32 of exactly this string (tools/nxconfig/memory.py target_id()); both names
    are schema names (letters, digits, underscore), so the string needs no escaping."""
    return f"{board}/{derivative}"


def memory_files(config: "nxconfig.Config", source: str | None = None) -> dict[str, str]:
    """Generated memory layout and diagnostic address files of a checked project; empty without `memory`."""
    layout = config.memory
    if layout is None:
        return {}
    source = source or source_label(config)
    env = _env()
    board, derivative = config.board.board.name, config.derivative.derivative.name
    ctx = {"source": source, "m": layout, "v": memory_values(layout), "flash": config.project.memory.flash,
           "target": {"id": nxmemory.target_id(board, derivative), "board": board, "derivative": derivative,
                      "hardware": hardware_id(board, derivative)},
           "family": config.derivative.derivative.family}
    files = {
        MEMORY_HEADER: env.get_template("bl_memory.h.j2").render(ctx),
        MEMORY_LINKER: env.get_template("bl_memory.ld.j2").render(ctx),
        APP_LINKER_HEADER: env.get_template("memoryLayout.h.j2").render(ctx),
    }
    files.update(diag_files(config, source))
    return genlicense.stamp_all(files)


# ----------------------------------------------------------------------------------------------- diagnostics


def diag_values(plan: "nxdiag.DiagPlan") -> dict[str, int]:
    """Values of bootloader/bl_diag.h by macro name (canflash reads the same names from the header)."""
    return {
        "BL_CAN_ID_REQUEST_PHYS": plan.request_id,
        "BL_CAN_ID_REQUEST_FUNC": plan.functional_id,
        "BL_CAN_ID_RESPONSE": plan.response_id,
        "BL_ISOTP_PADDING": plan.padding,
        "BL_ISOTP_BLOCK_SIZE": plan.block_size,
        "BL_ISOTP_STMIN_MS": plan.stmin_ms,
        "BL_ISOTP_N_CR_MS": plan.n_cr_ms,
        "BL_ISOTP_N_BS_MS": plan.n_bs_ms,
        "NX_DIAG_TESTER_ADDRESS": plan.tester_address,
        "NX_DIAG_ECU_ADDRESS": plan.ecu_address,
        "NX_DIAG_BL_SCAN_START": plan.scan_start,
        "NX_DIAG_BL_SCAN_END": plan.scan_end,
    }


def transport_block(transport: str) -> str:
    """Transport selection of the bootloader core in bootloader/bl_diag.h (BL-14).

    The core (bootloader/src/core/bl_transport.h) knows only the interface; exactly one implementation is
    compiled, and which one is a decision of the configuration, not of the code. `can` is ISO-TP on CAN
    (src/core/isotp.c) and what every project generates today; `link` is the inter-MCU link of a slave MCU
    (BL-15/BL-16, doc/architecture/multi-mcu-flash.md section 7.5), whose implementation follows there.

    Written here rather than in templates/bl_diag.h.j2 so that tools/canflash/hostbuild.py gets the block from
    the generator's own emitter, exactly like the rest of the header; moving it into the template changes
    nothing about the output."""
    if transport not in TRANSPORTS:
        raise ConfigError(f"unknown bootloader transport {transport!r} (known: {', '.join(TRANSPORTS)})")
    lines = [
        "",
        "/* Transport of the bootloader's UDS server (BL-14): the core compiles exactly one implementation of",
        " * bootloader/src/core/bl_transport.h, which is where the transports are named. This is the choice,",
        f" * and it is written nowhere else: {transport}",
        " *   BL_TRANSPORT_CAN   ISO 15765-2 on the CAN identifiers above (bootloader/src/core/isotp.c)",
        " *   BL_TRANSPORT_LINK  inter-MCU link of a slave MCU (BL-15/BL-16) */",
        f"#define BL_TRANSPORT BL_TRANSPORT_{transport.upper()}",
    ]
    return "\n".join(lines) + "\n"


def diag_header(values: dict[str, int], prefix: str, source: str, bus: str | None = None,
                skipped: int = 0, skip_reason: str = "", transport: str = "can") -> str:
    """bootloader/bl_diag.h from diagnostic values; also used for the synthetic layouts of the host tests."""
    return genlicense.stamp(_env().get_template("bl_diag.h.j2").render(
        source=source, v=values, prefix=prefix, bus=bus, skipped=skipped,
        skip_reason=skip_reason) + transport_block(transport), "bl_diag.h")


def diag_files(config: "nxconfig.Config", source: str | None = None) -> dict[str, str]:
    """Generated diagnostic addresses and security access of a checked project; empty without `diagnostics` or
    `memory`."""
    plan = config.diagnostics
    if plan is None or config.memory is None:
        return {}
    skipped = plan.scan_start - config.memory.flash_start
    reason = ("project diagnostics.bootloader.scan_start" if plan.scan_start_from_project else
              f"reserved by the family {config.derivative.derivative.family}, tools/nxconfig/diag.py")
    source = source or source_label(config)
    uid = config.derivative.uid
    uid_source = f"mcudb {config.derivative.derivative.name} uid" + (f": {uid.source}" if uid and uid.source else "")
    # BL-14: the transport of the bootloader core. A project says it with bootloader.transport once ARCH-48 has
    # put it into the schema; until then every project is a CAN node, which is what the default stands for.
    transport = getattr(plan, "transport", None) or "can"
    return {DIAG_HEADER: diag_header(diag_values(plan), plan.version_prefix, source, plan.bus, skipped, reason,
                                     transport),
            SECURITY_HEADER: security_header(security_values(plan), plan.security_method, plan.key_name, source,
                                             uid_source)}


# ----------------------------------------------------------------------------------------------- security access


def security_values(plan: "nxdiag.DiagPlan") -> dict[str, int]:
    """Numeric values of bootloader/bl_security.h by macro name (BL-03; canflash reads the same names)."""
    if plan.uid_address is None:
        raise ConfigError("no chip UID of the derivative (mcudb uid), the security access needs it (BL-03)")
    return {
        "BL_SECURITY_METHOD": plan.security_method_id,
        "BL_SECURITY_TEST_KEY_ALLOWED": 1 if plan.allow_test_key else 0,
        "BL_UID_ADDR": plan.uid_address,
        "BL_UID_LEN": plan.uid_size,
    }


def security_header(values: dict[str, int], method: str, key_name: str, source: str, uid_source: str) -> str:
    """bootloader/bl_security.h; also used for the synthetic layouts of the host tests (tools/canflash/hostbuild.py)."""
    return genlicense.stamp(_env().get_template("bl_security.h.j2").render(
        source=source, v=values, method_name=method, key_name=key_name, uid_source=uid_source), "bl_security.h")


# ----------------------------------------------------------------------------------------------- board part s32k1xx


@dataclass
class PinMux:
    pin: str
    signal: str
    port: str
    number: int
    mux: int


@dataclass
class LpspiDivider:
    prescale: int                   # TCR[PRESCALE]: clock / 2^prescale
    sckdiv: int                     # CCR[SCKDIV]: prescaled clock / (sckdiv + 2)
    clock_hz: int

    @property
    def sck_hz(self) -> float:
        return self.clock_hz / (1 << self.prescale) / (self.sckdiv + 2)


def lpspi_divider(clock_hz: int, max_hz: int) -> LpspiDivider:
    """Coarsest prescaler whose SCK period (<= max_hz) still spans at least 10 prescaled clock periods; the delay fields
    use SCKDIV too, i.e. one SCK period. 40 MHz, 500 kHz: PRESCALE 3 (/8), SCKDIV 8 (board-verified bootloader)."""
    best = None
    for prescale in range(8):
        sckdiv = math.ceil(clock_hz / ((1 << prescale) * max_hz)) - 2
        if LPSPI_MIN_SCKDIV <= sckdiv <= 255:
            best = LpspiDivider(prescale, sckdiv, clock_hz)
    if best is None:                # slow clock: finest setting at or below max_hz
        best = LpspiDivider(0, min(max(math.ceil(clock_hz / max_hz) - 2, 0), 255), clock_hz)
    return best


class S32K1Board:
    """Register values and pin setup of the bootloader board part (family s32k1xx)."""

    def __init__(self, config: "nxconfig.Config", plan: "nxmemory.BootloaderPlan"):
        self.config, self.plan = config, plan
        self.mux = nxchecks.pinmux(config)
        if self.mux is None or not self.mux.complete:
            raise ConfigError(f"{config.derivative.derivative.name} {config.board.board.package}: no complete pin "
                              "multiplexing table in mcudb (PORT_PCR.MUX of the bootloader pins)")
        clock = config.clock
        if clock is None or clock.reset_core_hz is None:
            raise ConfigError("no clock plan of the s32k1xx clock model")
        if clock.oscillator_hz is None:
            raise ConfigError(f"bootloader CAN {plan.can}: FlexCAN runs from SOSCDIV2, board "
                              f"{config.board.board.name} has no board.clock.sosc_hz")
        # ARCH-22: the bootloader starts SOSC with SOSCDIV1 = SOSCDIV2 = 1 (bootloader/platforms/s32k1xx/
        # hal_s32k1xx.c) and shares the CAN bit timing of the clock plan; a divided oscillator output would give
        # it another FlexCAN clock than calculated and break the derivative's output limit.
        divided = {o: d for o, d in clock.output_dividers.items() if o.startswith("SOSCDIV") and d != 1}
        if divided:
            raise ConfigError(f"s32k1xx bootloader: the clock model divides {', '.join(divided)} by "
                              f"{', '.join(str(d) for d in divided.values())} (oscillator "
                              f"{clock.oscillator_hz} Hz above clock.output_max_hz of {config.derivative.derivative.name}), "
                              f"but the bootloader runs SOSCDIV1/SOSCDIV2 undivided")
        if config.memory.flash_start != 0:
            raise ConfigError(f"s32k1xx bootloader: code flash must start at 0 (vector table, flash configuration "
                              f"field), {config.memory.flash_region} starts at 0x{config.memory.flash_start:X}")
        self.clock = clock

    def pin(self, pin: str, signal: str) -> PinMux:
        match = S32K1_PIN_RE.match(pin)
        selector = self.mux.selector(signal, pin)
        if match is None or not isinstance(selector, int):
            raise ConfigError(f"{signal} on {pin}: no PORT_PCR.MUX value in the pin multiplexing table")
        return PinMux(pin, signal, match.group(1), int(match.group(2)), selector)

    @staticmethod
    def pin_setup(pins: list[PinMux]) -> list[str]:
        """Clock gates of the ports (first use order), then the PCR writes in pin order."""
        ports = list(dict.fromkeys(p.port for p in pins))
        lines = [f"IP_PCC->PCCn[PCC_PORT{port}_INDEX] |= PCC_PCCn_CGC_MASK;" for port in ports]
        lines += [f"IP_PORT{p.port}->PCR[{p.number}U] = PORT_PCR_MUX({p.mux}U); /* {p.pin} {p.signal} */" for p in pins]
        return lines

    def can(self) -> dict:
        c = self.config
        bus = next(b for b in c.board.can if b.name == self.plan.can)
        match = S32K1_FLEXCAN_RE.match(bus.instance)
        if match is None:
            raise ConfigError(f"bootloader CAN {bus.name}: instance {bus.instance} is no FlexCAN")
        timing = self.clock.can.get(bus.name)
        use = c.project.can.get(bus.name)
        if timing is None or use is None:
            raise ConfigError(f"bootloader CAN {bus.name}: no bit timing (project can.{bus.name})")
        ctrl1 = (((timing.prescaler - 1) << 24) | ((timing.sjw - 1) << 22) | ((timing.phase_seg1 - 1) << 19)
                 | ((timing.phase_seg2 - 1) << 16) | (timing.prop_seg - 1))
        pins = [self.pin(bus.rx.pin, nxchecks.bus_signal(c, "can", bus, "rx")),
                self.pin(bus.tx.pin, nxchecks.bus_signal(c, "can", bus, "tx"))]
        return {"name": bus.name, "instance": bus.instance, "index": match.group(1), "phy": bus.phy or "-",
                "t": timing, "bitrate": use.bitrate, "ctrl1": ctrl1,
                "sample_point": f"{float(timing.sample_point * 100):g} %", "pins": pins,
                "pin_setup": self.pin_setup(pins)}

    def hook_sbc_uja113x(self, hook: "nxmemory.DeviceHook", used) -> dict:
        c = self.config
        spi_names = used.spi
        bus = next((b for b in c.board.spi if b.name in spi_names), None)
        if bus is None:
            raise ConfigError(f"bootloader hook {hook.device}: no used SPI bus with device {hook.device}")
        match = S32K1_LPSPI_RE.match(bus.instance)
        if match is None:
            raise ConfigError(f"bootloader hook {hook.device}: SPI instance {bus.instance} is no LPSPI")
        pins = [self.pin(getattr(bus, role).pin, nxchecks.bus_signal(c, "spi", bus, role))
                for role in ("sck", "sin", "sout", "pcs")]
        clock_hz = int(self.clock.outputs["SOSCDIV2"])
        div = lpspi_divider(clock_hz, BOOTLOADER_SPI_MAX_HZ)
        return {
            "header": "sbc_uja113x.h",
            "comment": f"{hook.device} ({hook.module}) on {bus.name} = {bus.instance}",
            "macros": [
                ("BL_SBC_SPI", f"IP_LPSPI{match.group(1)}"),
                ("BL_SBC_SPI_PCC_INDEX", f"PCC_LPSPI{match.group(1)}_INDEX"),
                ("BL_SBC_SPI_PCS", f"{bus.pcs.index}U"),
                ("BL_SBC_SPI_PRESCALE", f"{div.prescale}U"),
                ("BL_SBC_SPI_SCKDIV", f"{div.sckdiv}U"),
            ],
            "divider_comment": (f"SOSCDIV2 {clock_hz} Hz / {1 << div.prescale} (PRESCALE {div.prescale}) / "
                                f"(SCKDIV {div.sckdiv} + 2) = {div.sck_hz:g} Hz SCK, at most "
                                f"{BOOTLOADER_SPI_MAX_HZ} Hz; delays one SCK period"),
            "functions": [("bl_sbc_spi_pins", self.pin_setup(pins))],
            "init": f"sbc_init({'true' if hook.init else 'false'}); /* {hook.device}: "
                    + ("registers read only (watchdog state for the updater) */" if not hook.init
                       else "switched on before CAN (Normal mode, V2, CAN active), watchdog served by the "
                            "bootloader (BL-28) */" if hook.serve_watchdog
                       else "switched on before CAN (Normal mode, V2, CAN active) */"),
            "environment": "sbc_watchdog_off()",
        }

    HOOKS = {"sbcUja113x": "hook_sbc_uja113x"}

    def hooks(self) -> list[dict]:
        used = {d.name: d for d in self.config.integration.devices}
        result, modules = [], set()
        for hook in self.plan.hooks:
            method = self.HOOKS.get(hook.module)
            if method is None:
                raise ConfigError(f"bootloader hook of module {hook.module} (device {hook.device}) is not known to "
                                  "the s32k1xx board generator (tools/bswgen/blgen.py)")
            if hook.module in modules:
                raise ConfigError(f"bootloader hook {hook.module}: only one device per module is supported "
                                  f"({hook.device})")
            modules.add(hook.module)
            result.append(getattr(self, method)(hook, used[hook.device]))
        return result


# ----------------------------------------------------------------------------------------------- board part stm32


STM32_CAN_RE = re.compile(r"^CAN(\d)$")
STM32_PIN_RE = re.compile(r"^P([A-Z])(\d{1,2})$")
# Filter banks the bootloader uses on its bus (bootloader/platforms/stm32/hal_stm32.c: physical and functional
# request identifier, one mask filter each).
STM32_BOOTLOADER_FILTER_BANKS = 2


class Stm32Board:
    """Register values and pin setup of the bootloader board part (family stm32, PLAT-14).

    The two differences to the family s32k1xx, both in bootloader/platforms/stm32/hal_stm32.c:

    - **Clock.** bxCAN is clocked from PCLK1 only; there is no way to feed the oscillator directly into the CAN
      protocol engine as FlexCAN does with SOSCDIV2. An accurate bit rate therefore needs an accurate system
      clock, so the bootloader switches SYSCLK to the oscillator (HSE, without the PLL) and the bit timing is
      calculated for that clock - not for the PCLK1 of the application, which runs from the PLL. Without an
      oscillator on the board the bootloader would have to use HSI (+-1 %), which is out of the CAN tolerance:
      that is an error with the YAML path of the board entry, not a silent fall-back.
    - **Filter banks.** They all live in CAN1 and are split at CAN1->FMR.CAN2SB, so a bootloader on CAN2 needs
      the banks of the master and the split (same knowledge as platforms/stm32/bsp/bspBxCan of the application).
    """

    def __init__(self, config: "nxconfig.Config", plan: "nxmemory.BootloaderPlan"):
        import stm32gen                                 # flash wait states of the family, one place

        self.config, self.plan, self.stm32gen = config, plan, stm32gen
        clock = config.clock
        if clock is None or clock.family != "stm32":
            raise ConfigError("no clock plan of the stm32 clock model")
        if clock.oscillator_hz is None:
            raise ConfigError(f"bootloader CAN {plan.can}: bxCAN runs from PCLK1, so the bootloader switches the "
                              f"system clock to the oscillator - board {config.board.board.name} has no "
                              f"board.clock.sosc_hz (the internal HSI is not accurate enough for CAN)")
        self.clock = clock
        self.core_hz = int(clock.oscillator_hz)         # SYSCLK = PCLK1 = oscillator, no PLL, no prescaler
        self.mux = nxchecks.pinmux(config)
        if self.mux is None or not self.mux.complete:
            raise ConfigError(f"{config.derivative.derivative.name} {config.board.board.package}: no complete pin "
                              "multiplexing table in mcudb (alternate function of the bootloader CAN pins)")
        if config.memory.flash_start == 0:
            raise ConfigError(f"stm32 bootloader: the code flash starts at 0x{config.memory.flash_start:X}; the "
                              f"family expects it at the vector table base of the derivative (0x08000000)")

    def pins(self, bus) -> list[dict]:
        out = []
        for role in ("rx", "tx"):
            entry = getattr(bus, role)
            signal = nxchecks.bus_signal(self.config, "can", bus, role)
            selector = self.mux.selector(signal, entry.pin)
            match = STM32_PIN_RE.match(entry.pin)
            if match is None or not isinstance(selector, int):
                raise ConfigError(f"{signal} on {entry.pin}: no alternate function in the pin multiplexing table")
            out.append({"pin": entry.pin, "signal": signal, "port": match.group(1),
                        "index": ord(match.group(1)) - ord("A"), "number": int(match.group(2)), "af": selector})
        return out

    def timing(self, bus):
        """Bit timing of the bootloader bus at the oscillator clock (the application's PCLK1 is another clock)."""
        from nxconfig import clock as nxclock
        from nxconfig.timing import can_bit_timing
        from fractions import Fraction

        use = self.config.project.can.get(bus.name)
        if use is None:
            raise ConfigError(f"bootloader CAN {bus.name}: no bit rate (project can.{bus.name})")
        wanted = Fraction(str(use.sample_point_percent)) / 100
        timing, problem = can_bit_timing(self.core_hz, use.bitrate, wanted, nxclock.BXCAN_LIMITS, use.sjw_tq,
                                         use.prop_seg_tq)
        if timing is None:
            raise ConfigError(f"bootloader CAN {bus.name} at the oscillator clock {self.core_hz} Hz: {problem} "
                              f"(project can.{bus.name})")
        deviation = abs(timing.sample_point - wanted) * 100
        if deviation > nxclock.SAMPLE_POINT_ERROR_PP:
            raise ConfigError(f"bootloader CAN {bus.name}: sample point "
                              f"{float(timing.sample_point * 100):g} % instead of "
                              f"{float(use.sample_point_percent):g} % at the oscillator clock "
                              f"{self.core_hz} Hz (project can.{bus.name}.sample_point_percent)")
        return timing, use

    def can(self) -> dict:
        c = self.config
        bus = next(b for b in c.board.can if b.name == self.plan.can)
        match = STM32_CAN_RE.match(bus.instance)
        if match is None:
            raise ConfigError(f"bootloader CAN {bus.name}: instance {bus.instance} is no bxCAN instance")
        index = int(match.group(1))
        if index > 2:
            raise ConfigError(f"bootloader CAN {bus.name}: {bus.instance} has its own filter block; the stm32 "
                              f"board generator only knows the master/slave pair CAN1/CAN2")
        if index == 2 and "CAN2" not in c.derivative.instances:
            raise ConfigError(f"{c.derivative.derivative.name} has no CAN2 (bootloader bus {bus.name})")
        timing, use = self.timing(bus)
        # The banks of the bootloader: CAN1 takes the first ones, CAN2 the first ones above the split. The split
        # is the same value the application uses (half the block when both instances are used).
        banks = self.stm32gen.FILTER_BANKS if "CAN2" in c.derivative.instances else \
            self.stm32gen.FILTER_BANKS_SINGLE
        split = banks // 2 if "CAN2" in c.derivative.instances else banks
        bank = split if index == 2 else 0
        if bank + STM32_BOOTLOADER_FILTER_BANKS > banks:
            raise ConfigError(f"bootloader CAN {bus.name}: no room for {STM32_BOOTLOADER_FILTER_BANKS} filter "
                              f"banks from bank {bank} of {banks}")
        # CAN2 is the slave of CAN1: without CAN1EN neither its registers nor the filter block work (the pin data
        # of the derivative says so: ClockEnableMode="__HAL_RCC_CAN2_CLK_ENABLE; __HAL_RCC_CAN1_CLK_ENABLE")
        bits = ["RCC_APB1ENR_CAN1EN"] if index == 1 else ["RCC_APB1ENR_CAN2EN", "RCC_APB1ENR_CAN1EN"]
        clock_bits = bits[0] if len(bits) == 1 else "(" + " | ".join(bits) + ")"
        return {"name": bus.name, "instance": bus.instance, "index": index, "phy": bus.phy or "-",
                "t": timing, "bitrate": use.bitrate, "btr": _stm32_btr(timing), "ts1": timing.prop_seg
                + timing.phase_seg1, "sample_point": f"{float(timing.sample_point * 100):g} %",
                "clock_bits": clock_bits, "filter_bank": bank, "filter_split": split, "filter_banks": banks,
                "pins": self.pins(bus)}

    def hooks(self) -> list[dict]:
        if self.plan.hooks:
            hook = self.plan.hooks[0]
            raise ConfigError(f"bootloader hook of module {hook.module} (device {hook.device}) is not known to "
                              f"the stm32 board generator (tools/bswgen/blgen.py); the family has no device hook "
                              f"yet (bootloader/devices/<module> with an stm32 implementation)")
        return []


def _stm32_btr(t) -> int:
    """CAN_BTR: SJW, TS2, TS1 and BRP, each minus one (same encoding as can/bxCanBitTiming.h bxCanBtr())."""
    ts1 = t.prop_seg + t.phase_seg1
    return ((t.sjw - 1) << 24) | ((t.phase_seg2 - 1) << 20) | ((ts1 - 1) << 16) | (t.prescaler - 1)


FAMILIES = {"s32k1xx": S32K1Board, "stm32": Stm32Board}
# Template of the board header per family
BOARD_TEMPLATES = {"s32k1xx": "bl_board_s32k1xx.h.j2", "stm32": "bl_board_stm32.h.j2"}

# s32k1xx (BL-30): the flash configuration field sits at 0x400, the bootloader's vector table in front of it has the
# architectural maximum of the core (bootloader/platforms/s32k1xx/startup.S). ARMv7-M: 16 + 240 entries fill the
# first KB. ARMv6-M: 16 + 32 entries, 832 bytes up to the FCF take code. What does not fit there goes on to .text
# (-Wl,--enable-non-contiguous-regions, bootloader/CMakeLists.txt). Startup and family HAL stay out: the reset and
# fault handlers must lie behind the FCF, the updater (upd_family_check_image.inc) and tools/canflash reject a
# bootloader image otherwise; linker.ld asserts it.
S32K1_VECTOR_GAP_ARCHITECTURES = ("armv6m",)
S32K1_VECTOR_GAP = """  .text_gap :
  {
    *(EXCLUDE_FILE(*startup.S.* *hal_s32k1xx.c.*) .text.*)
  } > VECTORS
"""


def s32k1xx_sections(config: "nxconfig.Config", source: str) -> str:
    """bootloader/bl_sections.ld of the family s32k1xx: the gap section for a core with a short vector table."""
    core = config.derivative.derivative.core
    architecture = CORES[core].architecture
    head = [f"/* Generated by tools/bswgen/blgen.py from {source} - do not edit.",
            " * Section statements of bootloader/platforms/s32k1xx/linker.ld that depend on the core (BL-30),",
            f" * core {core} ({architecture}): "]
    if architecture in S32K1_VECTOR_GAP_ARCHITECTURES:
        head[-1] += "vector table 16 + 32 entries, code between it and the FCF at 0x400. */"
        return "\n".join(head) + "\n\n" + S32K1_VECTOR_GAP
    head[-1] += "the vector table fills the first KB up to the FCF, no gap. */"
    return "\n".join(head) + "\n"


def board_files(config: "nxconfig.Config", can: str | None = None, source: str | None = None) -> dict[str, str]:
    """bootloader/bl_board.h and bl_board.cmake of a checked project; `can` replaces bootloader.can."""
    if config.project.bootloader is None or config.memory is None:
        raise ConfigError(f"project {config.project.project.name} has no bootloader section (bootloader, memory)")
    family = config.derivative.derivative.family
    if family not in FAMILIES:
        raise ConfigError(f"{config.derivative.derivative.name}: no bootloader board generator for family {family} "
                          f"(bootloader/platforms/{family}, PLAT-16)")
    plan = config.bootloader
    if can is not None and can != plan.can:
        rep = Reporter(sources=dict(config.sources))
        plan = nxmemory.bootloader_plan(config, rep, can)
        errors = [str(m) for m in rep.messages if m.severity == "error"]
        if plan is None or errors:
            raise ConfigError(f"BOOTLOADER_CAN_BUS {can}: " + "; ".join(errors))
    board = FAMILIES[family](config, plan)
    ctx = {
        "source": source or source_label(config),
        # Core clock of the bootloader: the family decides whether it keeps the reset clock (s32k1xx: FIRC) or
        # switches to the oscillator (stm32: bxCAN runs from PCLK1, see Stm32Board)
        "core_hz": getattr(board, "core_hz", board.clock.reset_core_hz),
        "reset_hz": board.clock.reset_core_hz,
        "osc_hz": board.clock.oscillator_hz,
        "can": board.can(),
        "hooks": board.hooks(),
        "overridden": can is not None and can != config.project.bootloader.can,
    }
    cmake = "\n".join([
        f"# Generated by tools/bswgen/blgen.py from {ctx['source']} - do not edit.",
        "",
        f'set(NX_BL_CAN_BUS "{plan.can}")',
        f"set(NX_BL_DEVICES {' '.join(h.module for h in plan.hooks)})",
        # BL-28: bootloader.sbc_watchdog: serve - modules whose hook serves the watchdog of its device; not written
        # with leave, so that file and build stay as before
        *([f"set(NX_BL_WATCHDOG_SERVED_BY {' '.join(plan.watchdog_served_by)})"] if plan.watchdog_served_by else []),
    ]) + "\n"
    if family == "stm32":
        # Flash wait states for the clock the bootloader runs at (one table, tools/bswgen/stm32gen.py)
        ctx["flash_latency"] = board.stm32gen.flash_wait_states(board.core_hz, config.board.board.supply_v)
    files = {BOARD_HEADER: _env().get_template(BOARD_TEMPLATES[family]).render(ctx), BOARD_CMAKE: cmake}
    if family == "s32k1xx":
        files[BOARD_SECTIONS] = s32k1xx_sections(config, ctx["source"])
    return genlicense.stamp_all(files)


def write(out_dir: Path, contents: dict[str, str]):
    contents = genlicense.stamp_all(contents)
    for rel, content in contents.items():
        target = Path(out_dir) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or target.read_text(encoding="utf-8") != content:
            target.write_text(content, encoding="utf-8", newline="\n")


def generate(project_file: Path, out_dir: Path, can: str | None = None, board: bool = True,
             mcudb: Path = nxconfig.MCUDB) -> "nxconfig.Config":
    try:
        config = nxconfig.load(project_file, mcudb)
    except nxconfig.ConfigErrors as e:
        raise ConfigError(str(e)) from None
    if config.memory is None:
        raise ConfigError(f"project {config.project.project.name} has no memory partitioning (memory)")
    contents = memory_files(config)
    if board:
        contents.update(board_files(config, can))
    write(out_dir, contents)
    return config


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("project", type=Path, help="projects/<project>/project.yaml")
    parser.add_argument("out", type=Path, help="output directory (build/generated/board)")
    parser.add_argument("--can", default=None, help="board CAN bus instead of the project's bootloader.can")
    parser.add_argument("--memory-only", action="store_true", help="only the memory layout (any family)")
    args = parser.parse_args()
    try:
        generate(args.project, args.out, args.can or None, board=not args.memory_only)
    except ConfigError as e:
        print(f"blgen: error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
