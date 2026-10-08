"""SYS-24 (finding F4, PLAT-75 TF5): expected register list of SafeIo for a project, independent of bswgen.

The list (bsp/safeIo/safeIoConfiguration.h, NX_SAFE_IO_REGISTERS) names the registers the safety layer locks at start
and checks in every safety cycle. This module computes the part that follows from the configuration alone **without
importing bswgen or nxconfig** - YAML files only, register addresses from the reference manual - and reads the
generated header back to numbers, so that a test can compare both entry by entry:

- the locked pins and their order: the pins of the PWM groups the project uses (board order of groups and channels),
  the safe_off outputs (the specification of the safe outputs table, safe_outputs_expectation.py), then the role pin
  project.yaml safety.locked_pin; each pin once;
- the **pin function** of each of them from the derivative's pin multiplexing table in mcudb
  (`mcudb/<family>/<derivative>_<package>.pinmux.yaml`, signal FTMn_CHm resp. GPIO) - the independent check of the
  pin functions of the safe outputs that PLAT-75 (TF5) found missing; digital outputs carry no pin attribute;
- direction and input enable of their GPIO pins, the clock gates (the timer first, then the ports in the order of the
  locked pins), the static FTM words whose value follows from the board alone (PWM enable of the group's channels,
  system clock, no up-down counting, COMBINE/COMP/DTEN/SYNCEN per pair, polarity from the active levels, no
  inversion, BDMMODE 01, counter start 0);
- SYS-153: after the timer words, in every project, PMC_LVDSC1 (class SUPERVISION, 8-bit access): LVDRE on, LVDIE off.

Prescaler, period and dead time need the clock plan; for them the test compares the list with the driver
configuration the same generator run wrote (ftmPwmConfiguration.h) - the property that matters there is that SafeIo
checks exactly what ComplementaryPwm writes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

import safe_outputs_expectation as so

# Reference manual facts (S32K1xx RM Rev. 13): PORT PORTA..PORTE (12.5, 0x4004_9000 + 0x1000 * port), PCR n at 4 * n;
# GPIO PDDR 0x14, PIDR 0x18; FTM register offsets (47.4); PCC CGC bit 30; PCR MUX bits 10..8, LK bit 15, ISF bit 24.
RM_PORT_BASE = {port: 0x40049000 + 0x1000 * i for i, port in enumerate("ABCDE")}
RM_PDDR, RM_PIDR = 0x14, 0x18
RM_FTM_OFFSET = {"SC": 0x00, "CNT": 0x04, "MOD": 0x08, "CNTIN": 0x4C, "OUTMASK": 0x60, "COMBINE": 0x64,
                 "DEADTIME": 0x68, "POL": 0x70, "CONF": 0x84, "INVCTRL": 0x90, "SWOCTRL": 0x94,
                 "PAIR0DEADTIME": 0xA0, "PAIR1DEADTIME": 0xA8, "PAIR2DEADTIME": 0xB0, "PAIR3DEADTIME": 0xB8}
RM_PCR_MUX_SHIFT, RM_PCR_LK, RM_PCR_ISF = 8, 1 << 15, 1 << 24
PCR_MASK = 0x8743           # PS, PE, DSE, MUX, LK (PFE is not implemented on every pin)
GPIO_MUX = 1
# SYS-153: PMC (RM 41.7.1): base 0x4007_D000, LVDSC1 at offset 0, an 8-bit register; LVDIE bit 5, LVDRE bit 4. The
# family port switches LVDRE on and leaves LVDIE off in every start (SYS-125) - in every project, so always listed.
RM_PMC_BASE, RM_PMC_LVDSC1 = 0x4007D000, 0x0
RM_LVDSC1_LVDIE, RM_LVDSC1_LVDRE = 1 << 5, 1 << 4


@dataclass(frozen=True)
class Entry:
    cls: str
    address: int
    mask: int
    expected: int
    lock_bits: int = 0
    lock_clear: int = 0
    name: str = ""
    width: int = 32             # SYS-153: access width in bits

    @property
    def words(self) -> tuple[str, int, int, int, int, int, int]:
        return self.cls, self.address, self.mask, self.expected, self.lock_bits, self.lock_clear, self.width


def pinmux_of(board: dict) -> dict:
    """signals -> {pin: MUX} of the board's derivative and package (mcudb, read as YAML)."""
    mcu, package = board["board"]["mcu"], board["board"]["package"]
    found = sorted(so.REPO.glob(f"mcudb/*/{mcu}_{package}.pinmux.yaml"))
    if len(found) != 1:
        raise so.ExpectationError(f"{mcu} {package}: expected one pin multiplexing table, found {found}")
    return (yaml.safe_load(found[0].read_text(encoding="utf-8")) or {}).get("signals") or {}


def _mux(signals: dict, signal: str, pin: str) -> int:
    value = (signals.get(signal) or {}).get(pin)
    if not isinstance(value, int):
        raise so.ExpectationError(f"pin multiplexing: no function {signal} on {pin}")
    return value


def _pin(name: str, pin: str) -> tuple[str, int]:
    m = so.PIN.match(pin)
    if m is None:
        raise so.ExpectationError(f"{name}: pin {pin} is no port pin PTxn")
    return m.group(1), int(m.group(2))


@dataclass(frozen=True)
class LockedPin:
    name: str
    pin: str
    mux: int
    output_gpio: bool


def locked_pins(project_file: Path) -> tuple[list[LockedPin], list[dict]]:
    """The pins of the list in order, and the PWM groups the project uses (board order)."""
    project, board, _ = so.board_of(Path(project_file))
    signals = pinmux_of(board)
    use = project.get("use") or {}
    selected_pwm = project.get("pwm") or {}
    outputs = {o["name"]: o for o in (board.get("dio") or {}).get("outputs") or []}
    pins: list[LockedPin] = []
    seen: set[str] = set()

    def add(name: str, pin: str, mux: int, gpio: bool):
        if name not in seen:
            seen.add(name)
            pins.append(LockedPin(name, pin, mux, gpio))

    groups = [g for g in board.get("pwm") or [] if g["name"] in selected_pwm]
    for grp in groups:
        for ch in grp["channels"]:
            add(ch["name"], ch["pin"], _mux(signals, f"{grp['instance']}_CH{ch['channel']}", ch["pin"]), False)
    # the safe_off outputs in the order of the safe outputs table (its specification, not the generator)
    for entry in so.expected(Path(project_file)):
        if "PWM group" in entry.what:
            continue
        name = entry.what.split(" on ")[0]
        dev, key = name.split(".")
        dev_entry = next(d for d in board.get("devices") or [] if d["name"] == dev)
        out = outputs[dev_entry["connect"][key]]
        add(out["name"], out["pin"], _mux(signals, "GPIO", out["pin"]), True)
    safety = project.get("safety") or {}
    if safety.get("locked_pin"):
        name = safety["locked_pin"]
        sel = use.get("dio", [])
        if name not in outputs or not (sel == "all" or name in (sel or [])):
            raise so.ExpectationError(f"safety.locked_pin {name} is no used dio output")
        add(name, outputs[name]["pin"], _mux(signals, "GPIO", outputs[name]["pin"]), True)
    return pins, groups


def expected(project_file: Path) -> list[Entry]:
    """Every entry of the list whose value follows from the configuration without the clock plan; the FTM words SC
    (prescaler), MOD and PAIRnDEADTIME with their clock-independent part only (see compare_pwm_timer)."""
    pins, groups = locked_pins(project_file)
    entries: list[Entry] = []
    gates: list[str] = []
    ports: dict[str, list[int]] = {}
    for p in pins:
        port, number = _pin(p.name, p.pin)
        entries.append(Entry("PIN_FUNCTION", RM_PORT_BASE[port] + 4 * number, PCR_MASK,
                             (p.mux << RM_PCR_MUX_SHIFT) | RM_PCR_LK, RM_PCR_LK, RM_PCR_ISF, p.name))
        if p.output_gpio:
            ports.setdefault(port, []).append(number)
        if f"PORT{port}" not in gates:
            gates.append(f"PORT{port}")
    for port in sorted(ports):
        bits = sum(1 << n for n in ports[port])
        entries.append(Entry("GPIO_DIRECTION", so.RM_GPIO_BASE[port] + RM_PDDR, bits, bits, name=f"PT{port}_PDDR"))
        entries.append(Entry("GPIO_DIRECTION", so.RM_GPIO_BASE[port] + RM_PIDR, bits, 0, name=f"PT{port}_PIDR"))
    if len(groups) > 1:
        raise so.ExpectationError("more than one PWM group: the driver supports one group per project")
    gates = [g["instance"] for g in groups] + gates
    for g in gates:
        address, cgc = so.gate(g)
        entries.append(Entry("CLOCK_GATE", address, cgc, cgc, name=f"PCC_{g}"))
    for grp in groups:
        base = so.RM_FTM_BASE[grp["instance"]]
        channels = {ch["name"]: ch["channel"] for ch in grp["channels"]}
        mask = sum(1 << c for c in channels.values())
        pairs = sorted(channels[p["high_side"]] // 2 for p in grp["pairs"])
        pol = 0
        for p in grp["pairs"]:
            if grp["active"]["high_side"] == "low":
                pol |= 1 << channels[p["high_side"]]
            if grp["active"]["low_side"] == "low":
                pol |= 1 << channels[p["low_side"]]
        words = [("SC", 0x00FF003F, (mask << 16) | (1 << 3)),   # PS compared separately
                 ("MOD", 0xFFFF, None),
                 ("CNTIN", 0xFFFF, 0),
                 ("COMBINE", 0x77777777, sum(0x33 << (8 * k) for k in pairs)),
                 ("POL", 0xFF, pol),
                 ("INVCTRL", 0x0F, 0),
                 ("CONF", 0xC0, 0x40)]
        words += [(f"PAIR{k}DEADTIME", 0x000F00FF, None) for k in pairs]
        for reg, m, value in words:
            entries.append(Entry("PWM_TIMER", base + RM_FTM_OFFSET[reg], m, -1 if value is None else value,
                                 name=f"{grp['name']}_{reg}"))
    # SYS-153: low-voltage detect reset of the family port, a byte register read with an 8-bit access
    entries.append(Entry("SUPERVISION", RM_PMC_BASE + RM_PMC_LVDSC1, RM_LVDSC1_LVDIE | RM_LVDSC1_LVDRE,
                         RM_LVDSC1_LVDRE, name="PMC_LVDSC1", width=8))
    return entries


# ---- the generated header, back to numbers -------------------------------------------------------------------

_ENTRY = re.compile(r"X\((\w+), (\w+), (.+?), (0x[0-9A-F]+)UL, (0x[0-9A-F]+)UL, "
                    r"(0x[0-9A-F]+)UL, (0x[0-9A-F]+)UL, (\d+)U\)")
_PCC_INDEX = {"FTM0": 56, "FTM1": 57, "FTM2": 58, "FTM3": 38, "FTM4": 110, "FTM5": 111, "FTM6": 112, "FTM7": 113,
              "PORTA": 73, "PORTB": 74, "PORTC": 75, "PORTD": 76, "PORTE": 77}
_OFFSETS = {("PORT_Type", "PCR"): 0, ("GPIO_Type", "PDDR"): RM_PDDR, ("GPIO_Type", "PIDR"): RM_PIDR,
            ("PCC_Type", "PCCn"): 0, **{("FTM_Type", r): o for r, o in RM_FTM_OFFSET.items()},
            ("PMC_Type", "LVDSC1"): RM_PMC_LVDSC1}


def address_value(expression: str) -> int:
    """The address expression of an entry (device header symbols) evaluated with the reference manual facts."""
    def symbol(m: re.Match) -> str:
        name = m.group(0)
        if (b := re.fullmatch(r"IP_PORT([A-E])_BASE", name)) is not None:
            return str(RM_PORT_BASE[b.group(1)])
        if (b := re.fullmatch(r"IP_PT([A-E])_BASE", name)) is not None:
            return str(so.RM_GPIO_BASE[b.group(1)])
        if (b := re.fullmatch(r"IP_(FTM\d)_BASE", name)) is not None:
            return str(so.RM_FTM_BASE[b.group(1)])
        if name == "IP_PCC_BASE":
            return str(so.RM_PCC_BASE)
        if name == "IP_PMC_BASE":
            return str(RM_PMC_BASE)
        if (b := re.fullmatch(r"PCC_(\w+)_INDEX", name)) is not None:
            return str(_PCC_INDEX[b.group(1)])
        raise so.ExpectationError(f"unknown symbol {name} in address {expression}")

    text = re.sub(r"offsetof\((\w+), (\w+)\)", lambda m: str(_OFFSETS[(m.group(1), m.group(2))]), expression)
    text = re.sub(r"\b(\d+)U?L?\b", r"\1", text)
    text = re.sub(r"\b[A-Za-z_]\w*\b", symbol, text)
    if not re.fullmatch(r"[\d +*]+", text):
        raise so.ExpectationError(f"address {expression} is no sum of products")
    return eval(text, {"__builtins__": {}})  # noqa: S307 - digits, + and * only (checked above)


def parse(header: str) -> list[Entry]:
    count = re.search(r"#define NX_SAFE_IO_REGISTER_COUNT (\d+)", header)
    entries = [Entry(m.group(1), address_value(m.group(3)), int(m.group(4), 16), int(m.group(5), 16),
                     int(m.group(6), 16), int(m.group(7), 16), m.group(2), int(m.group(8)))
               for m in _ENTRY.finditer(header)]
    if count is None or int(count.group(1)) != len(entries):
        raise so.ExpectationError(f"NX_SAFE_IO_REGISTER_COUNT {count and count.group(1)} != {len(entries)} entries")
    return entries


def driver_values(ftm_header: str) -> dict[str, int]:
    """PRESCALER_SHIFT, PERIOD_TICKS, DEADTIME_DTPS, DEADTIME_TICKS of ftmPwmConfiguration.h (the driver's values)."""
    values = {}
    for key in ("PRESCALER_SHIFT", "PERIOD_TICKS", "DEADTIME_DTPS", "DEADTIME_TICKS"):
        m = re.search(rf"constexpr uint32_t {key}\s*=\s*(\d+)U;", ftm_header)
        if m is None:
            raise so.ExpectationError(f"ftmPwmConfiguration.h: {key} missing")
        values[key] = int(m.group(1))
    return values


def compare(actual: list[Entry], expect: list[Entry], driver: dict[str, int] | None) -> list[str]:
    """Findings per entry: class, address, mask, expected value (with the driver's prescaler, period and dead time
    for the clock-dependent FTM words), lock bits, bits cleared by the lock; missing and unexpected entries."""
    findings = []
    for index in range(max(len(actual), len(expect))):
        have = actual[index] if index < len(actual) else None
        want = expect[index] if index < len(expect) else None
        if have is None:
            findings.append(f"entry {index}: missing, expected {want}")
            continue
        if want is None:
            findings.append(f"entry {index}: unexpected {have}")
            continue
        value = want.expected
        if want.cls == "PWM_TIMER" and driver is not None:
            if want.name.endswith("_SC"):
                value |= driver["PRESCALER_SHIFT"]
            elif want.name.endswith("_MOD"):
                value = driver["PERIOD_TICKS"] - 1
            elif want.name.endswith("DEADTIME"):
                value = (driver["DEADTIME_DTPS"] << 6) | driver["DEADTIME_TICKS"]
        for field, got, exp in zip(("class", "address", "mask", "expected", "lock bits", "lock clear", "width"),
                                   have.words, (want.cls, want.address, want.mask, value, want.lock_bits,
                                                want.lock_clear, want.width)):
            if got != exp:
                findings.append(f"entry {index} ({want.name}): {field} {got!r}, expected {exp!r}")
    return findings
