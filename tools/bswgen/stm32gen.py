"""Generates the board configuration of the family **stm32** for a project (PLAT-14).

Counterpart of tools/bswgen/bswgen.py, which serves the family s32k1xx (its FAMILY check rejects everything else).
Nothing of that generator is rebuilt here: this module writes only the files the stm32 platform code needs, from
the same three configuration levels and the same checked clock model (tools/nxconfig, clock.py MODELS["stm32"]).

    mcu/derivativeFacts.h        core architecture/controller (ARCH-24), NVIC priority bits and MPU regions of the
                                 derivative (mcudb), for mcu/mcu.h
    mcu/mcuDerivative.h          include of the vendor device header named by the derivative (mcudb device_header)
    startup/nxIrqVectors.inc     external interrupt vectors from the IRQn_Type list of that device header
    bsp/clock/boardClock.h       frequencies and the RCC/FLASH register values of the clock driver
    bsp/can/bxCanConfiguration.h bit timing, pins and filter banks of the used bxCAN buses

Everything here is wiring and register encoding; the decisions (oscillator, system clock, bit rates) are checked
and calculated in tools/nxconfig. Findings of this module name the YAML key that has to change.

    python tools/bswgen/stm32gen.py projects/nucleo_f446re_demo/project.yaml build/generated/board
"""

import argparse
import re
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import nxconfig  # noqa: E402
from nxconfig import genlicense  # noqa: E402  (PLAT-120: licence line of generated files)
from nxconfig import can as nxcan  # noqa: E402
from nxconfig import checks as nxchecks  # noqa: E402
from nxconfig.config import CORES  # noqa: E402

FAMILY = "stm32"
# Where the device header named by mcudb (device_header.file) is looked for, in this order: the family module of
# this repository, then the OpenBSW platform of the family.
HEADER_DIRS = ("platforms/{family}/bsp/bspMcu/include", "external/openbsw/platforms/{family}/bsp/bspMcu/include")
PIN_RE = re.compile(r"^P([A-Z])(\d{1,2})$")
IRQ_RE = re.compile(r"^\s*(\w+)_IRQn\s*=\s*(\d+)", re.M)
IRQ_ENUM_RE = re.compile(r"typedef enum\b(.*?)\}\s*IRQn_Type;", re.S)
# RCC_CFGR HPRE/PPRE and RCC_PLLCFGR PLLP: the generated header uses the macro names of the device header, so the
# numbers are never written twice. The dividers are the ones the clock model may choose (clock.py STM32_HPRE/PPRE).
AHB_DIVIDERS = (1, 2, 4, 8, 16, 64, 128, 256, 512)
APB_DIVIDERS = (1, 2, 4, 8, 16)
# Flash wait states over HCLK for a supply of 2.7 V .. 3.6 V (RM0390 3.4.1 "Relation between CPU clock frequency
# and Flash memory read time", Annahme - das Reference Manual war nicht abrufbar, siehe mcudb/stm32/STM32F446RE.yaml).
# Lower supply voltages need more wait states; the board supply (board.supply_v) is checked against the range.
FLASH_WAIT_STATES_MHZ = (30, 60, 90, 120, 150, 180)
FLASH_SUPPLY_V = (2.7, 3.6)
# USB, SDIO and RNG need 48 MHz from the PLL Q output; the reset value of PLLR (F446: SAI) stays 2.
PLL_Q_TARGET_HZ = 48_000_000
PLL_R_DEFAULT = 2
# bxCAN filter banks of the peripheral, shared by CAN1 and CAN2 (platforms/stm32/bsp/bspBxCan)
FILTER_BANKS = 28
FILTER_BANKS_SINGLE = 14        # derivative without CAN2


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


def device_header_file(config: "nxconfig.Config", repo: Path = nxconfig.REPO) -> Path:
    """The vendor device header of the derivative on disk (mcudb device_header.file)."""
    header = config.derivative.device_header
    if header is None:
        raise ConfigError(f"{config.derivative.derivative.name}: the derivative has no device_header (mcudb)")
    family = config.derivative.derivative.family
    tried = [Path(repo) / d.format(family=family) / header.file for d in HEADER_DIRS]
    for path in tried:
        if path.is_file():
            return path
    raise ConfigError(f"{config.derivative.derivative.name}: device header {header.file} not found (looked in "
                      + ", ".join(str(p.parent) for p in tried) + ")")


def interrupt_vectors(header: Path) -> list[tuple[int, str | None]]:
    """(number, handler) of every external interrupt of a device header, gaps as None.

    Source is the IRQn_Type enumeration of the CMSIS device header; the handler names follow the ST convention
    <interrupt>_IRQHandler, the same names the OpenBSW startup files use.
    """
    text = header.read_text(encoding="utf-8", errors="replace")
    match = IRQ_ENUM_RE.search(text)
    if match is None:
        raise ConfigError(f"{header.name}: no IRQn_Type enumeration (external interrupt list of the derivative)")
    numbers: dict[int, str] = {}
    for name, value in IRQ_RE.findall(match.group(1)):
        number = int(value)
        if number < 0:
            continue                    # core exceptions (NonMaskableInt_IRQn = -14 ...) are not in the list
        if number in numbers:
            raise ConfigError(f"{header.name}: interrupt number {number} is used by {numbers[number]} and {name}")
        numbers[number] = name
    if not numbers:
        raise ConfigError(f"{header.name}: IRQn_Type has no external interrupt")
    return [(n, f"{numbers[n]}_IRQHandler" if n in numbers else None) for n in range(max(numbers) + 1)]


def _divider_macro(kind: str, divider: int, dividers: tuple[int, ...]) -> str:
    if divider not in dividers:
        raise ConfigError(f"{kind} divider {divider} is not one of {', '.join(str(d) for d in dividers)}")
    return f"RCC_CFGR_{kind}_DIV{divider}"


def flash_wait_states(hclk_hz: int, supply_v: float | None) -> int:
    """Flash wait states for the core clock, with the supply voltage range the table is valid for."""
    if supply_v is not None and not FLASH_SUPPLY_V[0] <= supply_v <= FLASH_SUPPLY_V[1]:
        raise ConfigError(f"board supply {supply_v} V is outside {FLASH_SUPPLY_V[0]}..{FLASH_SUPPLY_V[1]} V; the "
                          f"flash wait states of tools/bswgen/stm32gen.py (FLASH_WAIT_STATES_MHZ) only hold there "
                          f"(board.supply_v)")
    mhz = hclk_hz / 1_000_000
    for ws, limit in enumerate(FLASH_WAIT_STATES_MHZ):
        if mhz <= limit:
            return ws
    raise ConfigError(f"core clock {mhz:g} MHz is above the highest flash wait state entry "
                      f"({FLASH_WAIT_STATES_MHZ[-1]} MHz): clock.system_hz")


def pll_context(plan) -> dict | None:
    """PLLCFGR fields of the clock plan (None if the project runs without the PLL)."""
    if plan.pll is None:
        return None
    p = plan.pll
    if p.output_divider % 2 or not 2 <= p.output_divider <= 8:
        raise ConfigError(f"PLLP {p.output_divider} is not 2, 4, 6 or 8")
    q = max(2, -(-int(p.vco_hz) // PLL_Q_TARGET_HZ))     # smallest Q with VCO/Q <= 48 MHz
    if q > 15:
        raise ConfigError(f"PLLQ {q} is above 15 (VCO {float(p.vco_hz)/1e6:g} MHz): choose another system clock")
    return {
        "m": p.input_divider, "n": p.multiplier, "p": p.output_divider, "p_code": p.output_divider // 2 - 1,
        "q": q, "q_hz": int(p.vco_hz) // q, "q_exact": int(p.vco_hz) % q == 0 and int(p.vco_hz) // q ==
        PLL_Q_TARGET_HZ, "r": PLL_R_DEFAULT, "input_hz": int(p.input_hz), "vco_hz": int(p.vco_hz),
        "output_hz": int(p.output_hz), "source": plan.peripheral_source.get("PLL", "HSI"),
    }


def clock_context(config: "nxconfig.Config") -> dict:
    plan = config.clock
    if plan is None or plan.family != FAMILY:
        raise ConfigError(f"no clock plan of the stm32 clock model for {config.project.project.name}")
    hclk = int(plan.domains["core"])
    ws = flash_wait_states(hclk, config.board.board.supply_v)
    used = sorted((name, int(hz)) for name, hz in plan.peripheral_hz.items() if "." not in name)
    counters = sorted((name.split(".")[0], int(hz)) for name, hz in plan.peripheral_hz.items()
                      if name.endswith(".counter"))
    return {
        "sysclk_hz": int(plan.system_hz), "hclk_hz": hclk, "pclk1_hz": int(plan.domains["apb1"]),
        "pclk2_hz": int(plan.domains["apb2"]), "hsi_hz": int(plan.outputs["HSI"]),
        "hse_hz": int(plan.outputs["HSE"]) if "HSE" in plan.outputs else None,
        "reset_hz": plan.reset_core_hz, "source": plan.system_source,
        "use_hse": "HSE" in plan.outputs, "hpre": _divider_macro("HPRE", plan.dividers["core"], AHB_DIVIDERS),
        "ppre1": _divider_macro("PPRE1", plan.dividers["apb1"], APB_DIVIDERS),
        "ppre2": _divider_macro("PPRE2", plan.dividers["apb2"], APB_DIVIDERS),
        "ahb_divider": plan.dividers["core"], "apb1_divider": plan.dividers["apb1"],
        "apb2_divider": plan.dividers["apb2"],
        "apb1_max_hz": config.derivative.clock.max_hz.get("apb1"),
        "apb2_max_hz": config.derivative.clock.max_hz.get("apb2"),
        "wait_states": ws, "pll": pll_context(plan), "peripherals": used, "counters": counters,
        "bus_of": {name: plan.peripheral_source.get(name, "") for name, _ in used},
    }


def _pin(pin: str) -> tuple[str, int]:
    match = PIN_RE.match(pin)
    if match is None:
        raise ConfigError(f"pin {pin} is not a GPIO pin of the family stm32 (P<port><number>)")
    return match.group(1), int(match.group(2))


def can_context(config: "nxconfig.Config") -> dict:
    """Bit timing, pins and filter banks of the used bxCAN buses."""
    plan = config.clock
    mux = nxchecks.pinmux(config)
    if mux is None or not mux.complete:
        raise ConfigError(f"{config.derivative.derivative.name} {config.board.board.package}: no complete pin "
                          "multiplexing table in mcudb (alternate function of the CAN pins)")
    single = "CAN2" not in config.derivative.instances
    banks = FILTER_BANKS_SINGLE if single else FILTER_BANKS
    buses = []
    for bus in config.selected("can"):
        match = re.fullmatch(r"CAN(\d)", bus.instance)
        if match is None:
            raise ConfigError(f"CAN {bus.name}: instance {bus.instance} is no bxCAN instance (CAN1, CAN2, ...)")
        timing = plan.can.get(bus.name)
        if timing is None:
            raise ConfigError(f"CAN {bus.name}: no bit timing (project can.{bus.name})")
        pins = []
        for role in ("rx", "tx"):
            entry = getattr(bus, role)
            signal = nxchecks.bus_signal(config, "can", bus, role)
            selector = mux.selector(signal, entry.pin)
            if not isinstance(selector, int):
                raise ConfigError(f"{signal} on {entry.pin}: no alternate function in the pin multiplexing table")
            port, number = _pin(entry.pin)
            pins.append({"role": role, "pin": entry.pin, "port": port, "number": number, "af": selector,
                         "signal": signal})
        use = config.project.can[bus.name]
        buses.append({"name": bus.name, "ident": nxcan.c_name(bus.name), "instance": bus.instance,
                      "index": int(match.group(1)), "bitrate": use.bitrate, "t": timing,
                      "quanta": timing.quanta, "ts1": timing.prop_seg + timing.phase_seg1,
                      "btr": _btr(timing), "sample_point": f"{float(timing.sample_point * 100):g} %",
                      "requested": f"{float(use.sample_point_percent):g} %",
                      "sample_point_bp": int(timing.sample_point * 10000), "pins": pins,
                      "phy": bus.phy or "-", "verify": use.verify})
    # Filter banks: the block belongs to CAN1 and is split at CAN2SB. Every used bus of an instance gets an equal
    # share of its side; a derivative without CAN2 has one side with 14 banks.
    masters = [b for b in buses if b["index"] == 1]
    slaves = [b for b in buses if b["index"] == 2]
    if slaves and single:
        raise ConfigError(f"{config.derivative.derivative.name} has no CAN2, but a bus uses it")
    split = banks // 2 if slaves else banks
    _assign(masters, 0, split)
    _assign(slaves, split, banks - split)
    others = [b for b in buses if b["index"] > 2]
    if others:
        raise ConfigError(f"CAN{others[0]['index']} has its own filter block; the generator only knows the "
                          f"master/slave pair CAN1/CAN2 (bus {others[0]['name']})")
    return {"buses": buses, "split": split, "banks": banks, "single": single}


def _assign(buses: list[dict], first: int, count: int):
    if not buses:
        return
    share = count // len(buses)
    for i, bus in enumerate(buses):
        bus["filter_bank"] = first + i * share
        bus["filter_count"] = share


def _btr(t) -> int:
    """CAN_BTR of a bit timing (same encoding as bxCanBtr() in can/bxCanBitTiming.h, checked there)."""
    ts1 = t.prop_seg + t.phase_seg1
    return ((t.sjw - 1) << 24) | ((t.phase_seg2 - 1) << 20) | ((ts1 - 1) << 16) | (t.prescaler - 1)


def derivative_facts(config: "nxconfig.Config", source: str) -> str:
    """mcu/derivativeFacts.h: the numbers the family code needs from the derivative database (ARCH-16).

    Same macro names as the s32k1xx generator, minus NX_MPU_CESR_NRGD: that is the register encoding of the NXP
    system MPU. The stm32 family uses the MPU of the Arm core, which reports its regions in MPU_TYPE.
    """
    d = config.derivative
    info = d.derivative
    core = CORES.get(info.core)
    if core is None:
        raise ConfigError(f"{info.name}: unknown core {info.core} (tools/nxconfig config.CORES)")
    lines = [
        f"// Generated by tools/bswgen/stm32gen.py from {source} - do not edit.",
        "// Hardware facts of the derivative (mcudb <family>/<derivative>.yaml) that the family code needs as",
        "// numbers. Nothing here is a board or a project value; the evidence of every value is the `source` of",
        "// its entry in the derivative database.",
        "",
        "#pragma once",
        "",
        # ARCH-24: same macro names as tools/bswgen/bswgen.py core_context (contract of nxInterruptPriority.h)
        f"// Core of {info.name} ({info.core}, mcudb derivative.core -> tools/nxconfig config.CORES). Code that differs",
        "// between core profiles selects on these macros, never on __ARM_ARCH (7 for Cortex-M4F and Cortex-R4F).",
        f"#define NX_CORE_ARCHITECTURE_{core.architecture.upper()} 1",
        f"#define NX_CORE_INTERRUPT_CONTROLLER_{core.interrupt_controller.upper()} 1",
        "",
        f"// NVIC priority bits {info.name} implements: levels 0 (highest) .. {(1 << info.nvic_priority_bits) - 1}"
        if info.nvic_priority_bits is not None else "// the derivative database gives no nvic_priority_bits",
    ]
    if info.nvic_priority_bits is not None:
        lines += ["// (mcudb derivative.nvic_priority_bits; interrupts/nxInterruptPriority.h compares the value of",
                  "// the device header against it and stops the build on a mismatch)",
                  f"#define NX_NVIC_PRIORITY_BITS {info.nvic_priority_bits}"]
    lines.append("")
    if d.mpu is not None:
        lines += [f"// Region descriptors of the Arm core MPU of {info.name} (mcudb mpu.regions)",
                  f"#define NX_MPU_REGIONS {d.mpu.regions}"]
    else:
        lines += [f"// {info.name} describes no MPU in the derivative database (mcudb mpu): code that needs it",
                  "// stops with an #error instead of assuming a region count"]
    return "\n".join(lines) + "\n"


def device_header_include(config: "nxconfig.Config", source: str) -> str:
    """mcu/mcuDerivative.h: the vendor device header the family mcu.h includes."""
    d = config.derivative
    header = d.device_header
    return "\n".join([
        f"// Generated by tools/bswgen/stm32gen.py from {source} - do not edit.",
        f"// Device header of {d.derivative.name}: {header.file} (written for {header.describes}).",
        "",
        "#pragma once",
        "",
        f'#include "{header.file}"',
    ]) + "\n"


def irq_vectors(vectors: list[tuple[int, str | None]], derivative: str, header: Path, source: str) -> str:
    """startup/nxIrqVectors.inc: one entry per external interrupt number, for the startup template."""
    lines = [
        f"/* Generated by tools/bswgen/stm32gen.py from {source} - do not edit.",
        f" * External interrupts of {derivative}, from the IRQn_Type list of {header.name}",
        f" * ({len(vectors)} vectors, handler names as in the ST startup files). Used twice by the startup",
        " * template platforms/stm32/startup/armv7m/startUp.S: as vector table and as weak handler list. */",
    ]
    for number, handler in vectors:
        lines.append(f"NX_IRQ_VECTOR({handler}) /* {number} */" if handler else f"NX_IRQ_RESERVED() /* {number} */")
    return "\n".join(lines) + "\n"


def files(config: "nxconfig.Config", source: str | None = None) -> dict[str, str]:
    """All generated files of the family stm32 for a checked project."""
    family = config.derivative.derivative.family
    if family != FAMILY:
        raise ConfigError(f"{config.derivative.derivative.name}: family {family} is not supported by stm32gen "
                          f"(only {FAMILY}; the family s32k1xx has tools/bswgen/bswgen.py)")
    source = source or source_label(config)
    header = device_header_file(config)
    env = _env()
    ctx = {"source": source, "derivative": config.derivative.derivative.name, "board": config.board.board.name,
           "c": clock_context(config), "can": can_context(config)}
    return genlicense.stamp_all({
        "mcu/derivativeFacts.h": derivative_facts(config, source),
        "mcu/mcuDerivative.h": device_header_include(config, source),
        "startup/nxIrqVectors.inc": irq_vectors(interrupt_vectors(header), ctx["derivative"], header, source),
        "bsp/clock/boardClock.h": env.get_template("stm32BoardClock.h.j2").render(ctx),
        "bsp/can/bxCanConfiguration.h": env.get_template("stm32BxCanConfiguration.h.j2").render(ctx),
    })


def write(out_dir: Path, contents: dict[str, str]):
    contents = genlicense.stamp_all(contents)
    for rel, content in contents.items():
        target = Path(out_dir) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or target.read_text(encoding="utf-8") != content:
            target.write_text(content, encoding="utf-8", newline="\n")


def generate(project_file: Path, out_dir: Path, mcudb: Path = nxconfig.MCUDB) -> "nxconfig.Config":
    try:
        config = nxconfig.load(project_file, mcudb)
    except nxconfig.ConfigErrors as e:
        raise ConfigError(str(e)) from None
    write(out_dir, files(config))
    return config


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("project", type=Path, help="projects/<project>/project.yaml")
    parser.add_argument("out", type=Path, help="output directory for the generated headers")
    parser.add_argument("--mcudb", type=Path, default=nxconfig.MCUDB, help="derivative database (tests)")
    args = parser.parse_args(argv)
    try:
        generate(args.project, args.out, args.mcudb)
    except ConfigError as e:
        print(f"stm32gen: error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
