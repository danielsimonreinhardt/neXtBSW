"""PLAT-77 (IO-32, TF5): expected safe outputs table of a project, independent of bswgen.

The table nxSafeOutputs (hardFaultHandler/safeOutputs.h) holds the register writes the fault handlers and the halt
deadman work through first. This module computes what it must contain for a project **without importing bswgen or
nxconfig** - only the YAML files are read, and every register address comes from the reference manual:

- `board.yaml` of the project's board: PWM groups (instance, channels), devices (module, connect), dio outputs (pin,
  active level), the derivative (`board.mcu`);
- `project.yaml`: which PWM groups (`pwm:`) and which dio outputs (`use.dio`) the project uses;
- the module manifests (`connect.<name>.safe_off`, libs/*/<module>/module.yaml);
- the derivative database `mcudb/<family>/<derivative>.yaml` (core and the FTM instances the derivative has).

Semantics (the specification of the table, IO-32/IO-37, not the generator's code): first every PWM group the project
uses, in board order: FTM software output control, SWOCTRL = the channel mask of the group (CHnOC set, CHnOCV 0,
[RM] 47.5.15); then every output a module connection declares safe_off, in board order of the devices and manifest
order of the connections, each output once: GPIO PCOR (active high) or PSOR (active low) with the pin's bit. Every
entry is gated by the PCC register of its peripheral with the CGC bit. An end entry of four zeros follows.

Shared by the image check platforms/s32k1xx/hardFaultHandler/test/test_safe_outputs_table.py (linked ELF) and
BswGenSafeOutputsHostTest (host build of the definition, tools/bswgen/test_bswgen.py), which uses it since SYS-49.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]

# Register facts of the S32K1 family from the reference manual (S32K1xx RM Rev. 13), not from the generator and not
# from the device headers (those are the second, independent source in the image check):
# FTM base addresses (47.4 memory map; FTM4..FTM7 only on S32K148), GPIO PTA..PTE (12.3, 0x400F_F000 + 0x40 * port),
# PCC (29.4, PCCn index of the peripheral slot), FTM SWOCTRL offset 0x94, GPIO PSOR 0x4 / PCOR 0x8, PCC CGC bit 30.
RM_FTM_BASE = {"FTM0": 0x40038000, "FTM1": 0x40039000, "FTM2": 0x4003A000, "FTM3": 0x40026000,
               "FTM4": 0x4006E000, "FTM5": 0x4006F000, "FTM6": 0x40070000, "FTM7": 0x40071000}
RM_GPIO_BASE = {port: 0x400FF000 + 0x40 * i for i, port in enumerate("ABCDE")}
RM_PCC_INDEX = {"FTM0": 56, "FTM1": 57, "FTM2": 58, "FTM3": 38, "FTM4": 110, "FTM5": 111, "FTM6": 112, "FTM7": 113,
                "PORTA": 73, "PORTB": 74, "PORTC": 75, "PORTD": 76, "PORTE": 77}
RM_PCC_BASE, RM_CGC = 0x40065000, 1 << 30
RM_SWOCTRL, RM_PSOR, RM_PCOR = 0x94, 0x4, 0x8

PIN = re.compile(r"^PT([A-E])(\d{1,2})$")


@dataclass(frozen=True)
class Entry:
    """One entry of nxSafeOutputs: the four words of NxSafeOutput and what it stands for."""

    address: int
    value: int
    gate: int
    gate_mask: int
    what: str = ""

    @property
    def words(self) -> tuple[int, int, int, int]:
        return self.address, self.value, self.gate, self.gate_mask

    def __str__(self) -> str:
        return (f"[0x{self.address:08X}] = 0x{self.value:X} if [0x{self.gate:08X}] & 0x{self.gate_mask:08X}"
                + (f" ({self.what})" if self.what else ""))


class ExpectationError(Exception):
    """The profile cannot be evaluated (unknown instance, pin, module); never a silent empty expectation."""


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def board_of(project_file: Path) -> tuple[dict, dict, Path]:
    project = load(project_file)
    board_dir = (project_file.parent / project["project"]["board"]).resolve()
    return project, load(board_dir / "board.yaml"), board_dir


def derivative_of(board: dict) -> dict:
    mcu = board["board"]["mcu"]
    found = sorted(REPO.glob(f"mcudb/*/{mcu}.yaml"))
    if len(found) != 1:
        raise ExpectationError(f"derivative {mcu}: expected one mcudb/<family>/{mcu}.yaml, found {found}")
    return load(found[0])


def manifest_of(module: str) -> dict:
    found = [*REPO.glob(f"libs/*/{module}/module.yaml"), *REPO.glob(f"platforms/*/port/**/{module}/module.yaml")]
    if not found:
        raise ExpectationError(f"no module.yaml of module {module}")
    return load(found[0])


def gate(peripheral: str) -> tuple[int, int]:
    if peripheral not in RM_PCC_INDEX:
        raise ExpectationError(f"no reference manual PCC index for {peripheral}")
    return RM_PCC_BASE + 4 * RM_PCC_INDEX[peripheral], RM_CGC


def expected(project_file: Path) -> list[Entry]:
    """The entries of nxSafeOutputs of the project, without the end entry."""
    project, board, _ = board_of(Path(project_file))
    derivative = derivative_of(board)
    instances = derivative.get("instances", {})
    use = project.get("use") or {}
    selected_pwm = project.get("pwm") or {}

    def used_output(name: str) -> bool:
        sel = use.get("dio", [])
        return sel == "all" or name in (sel or [])

    entries: list[Entry] = []
    for grp in board.get("pwm") or []:
        if grp["name"] not in selected_pwm:
            continue
        instance = grp["instance"]
        if instance not in RM_FTM_BASE or instance not in instances:
            raise ExpectationError(f"PWM group {grp['name']}: {instance} is no FTM of {board['board']['mcu']}")
        channels = [ch["channel"] for ch in grp["channels"]]
        limit = instances[instance].get("channels", 8)
        if any(not 0 <= ch < limit for ch in channels):
            raise ExpectationError(f"PWM group {grp['name']}: channel out of 0..{limit - 1} of {instance}")
        mask = sum(1 << ch for ch in channels)
        entries.append(Entry(RM_FTM_BASE[instance] + RM_SWOCTRL, mask, *gate(instance),
                             f"PWM group {grp['name']} on {instance}: SWOCTRL"))

    outputs = {o["name"]: o for o in (board.get("dio") or {}).get("outputs") or []}
    seen: set[str] = set()
    for dev in board.get("devices") or []:
        module = dev.get("module")
        if not module:
            continue
        for key, spec in (manifest_of(module).get("connect") or {}).items():
            if not (spec or {}).get("safe_off") or key not in (dev.get("connect") or {}):
                continue
            name = dev["connect"][key]
            if not used_output(name) or name in seen:
                continue
            seen.add(name)
            out = outputs.get(name)
            if out is None:
                raise ExpectationError(f"{dev['name']}.{key}: {name} is no dio output of the board")
            m = PIN.match(out["pin"])
            if m is None:
                raise ExpectationError(f"{name}: pin {out['pin']} is no GPIO pin PTxn")
            port, bit = m.group(1), int(m.group(2))
            if bit > 31:
                raise ExpectationError(f"{name}: pin {out['pin']} beyond bit 31")
            low = out.get("active", "high") == "low"
            entries.append(Entry(RM_GPIO_BASE[port] + (RM_PSOR if low else RM_PCOR), 1 << bit, *gate(f"PORT{port}"),
                                 f"{dev['name']}.{key} on {out['pin']}, active {'low: PSOR' if low else 'high: PCOR'}"))
    return entries


def core_of(project_file: Path) -> str:
    """Core of the project's derivative from mcudb (e.g. cortex-m4, cortex-m0plus)."""
    _, board, _ = board_of(Path(project_file))
    return derivative_of(board)["derivative"]["core"]


def compare(actual: list[tuple[int, int, int, int]], expect: list[Entry]) -> list[str]:
    """Findings of an actual table (without the end entry) against the expectation, entry by entry: address, value,
    gate register, gate mask and position. Empty when the table is right."""
    findings = []
    for index in range(max(len(actual), len(expect))):
        have = actual[index] if index < len(actual) else None
        want = expect[index] if index < len(expect) else None
        if have is None:
            findings.append(f"entry {index}: missing, expected {want}")
            continue
        if want is None:
            findings.append(f"entry {index}: unexpected [0x{have[0]:08X}] = 0x{have[1]:X} "
                            f"if [0x{have[2]:08X}] & 0x{have[3]:08X}")
            continue
        for field, got, exp in zip(("address", "value", "gate register", "gate mask"), have, want.words):
            if got != exp:
                findings.append(f"entry {index} ({want.what}): {field} 0x{got:08X}, expected 0x{exp:08X}")
    return findings


# SYS-30 (gap G1): the read-back of every entry, from the same specification. GPIO PDIR 0x10 ([RM] 12.3: the pin
# level, which sees a pin held by something else), FTM SWOCTRL itself with CHnOC and CHnOCV of the group's channels.
RM_PDIR = 0x10


def expected_readbacks(project_file: Path) -> list[tuple[int, int, int, int, int]]:
    """(read-back register, mask, expected, gate register, gate mask) per entry of nxSafeOutputs, in its order."""
    rows = []
    for e in expected(project_file):
        if (e.address & 0xFFF) == RM_SWOCTRL and e.address - RM_SWOCTRL in RM_FTM_BASE.values():
            rows.append((e.address, e.value | (e.value << 8), e.value, e.gate, e.gate_mask))
        else:
            port_base = e.address & ~0x3F
            active_low = (e.address - port_base) == RM_PSOR
            rows.append((port_base + RM_PDIR, e.value, e.value if active_low else 0, e.gate, e.gate_mask))
    return rows
