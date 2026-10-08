"""Flash tools against total loss on devices with a security module (NVM-15).

Two ways to lose an s32k1xx part for good once its CSEc took a key space (PGMPART with key size != 0 - that alone
locks mass erase, with or without a loaded key; CLAUDE.md, decision csec-pgmpart-rueckweg 2026-09-29):

1. The flash configuration field 0x400..0x40F is erased (sector 0 erased, not yet programmed again) when a reset
   comes. The chip reads FSEC 0xFF = secured, the debugger has no access, and mass erase - the usual way back - is
   locked. So whoever erases sector 0 writes the field again, unsecured, in the same run before any reset.
2. A mass erase (Erase All Blocks, MDM-AP / J-Link unlock) is not a way back on such a part: it is refused by the
   chip, and a script that relies on it leaves the part half erased. The only way back is NVM-13: load the
   MASTER_ECU_KEY and run DBG_CHAL/DBG_AUTH, which erases the keys and undoes the partition.

This module holds the checks the tools share:

- `sector0_problems()`: an image that writes the first code flash sector must carry the complete configuration field
  with the reference value of the family (the same value upd_family.h and canflash's image rule accept).
- `csec_risk()`: can the device be CSEc-partitioned? From the project (`nvm.partition.key_slots`), the derivative
  (`eeprom_emulation.keys` of mcudb) and, if the caller read it, the partition code (s32k1xx SIM_FCFG1 DEPART).
- `mass_erase_commands()`: mass erase in a debugger command file or command line (J-Link, OpenOCD, pyOCD,
  STM32CubeProgrammer).
- `check_debug_flash()`: all of it for one debugger run; refuses with `GuardError`.

Reads project, board and derivative with PyYAML only (no tools/nxconfig), no Qt, no hardware.

    .venv/Scripts/python tools/canflash/flashguard.py --project projects/<project>/project.yaml \\
        --script <build>/bootloader/RelWithDebInfo/flash-bootloader.jlink --image bootloader.hex
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

import yaml

ROOT = Path(__file__).resolve().parents[2]
MCUDB = ROOT / "mcudb"

WAY_BACK = ("the only way back on a CSEc-partitioned part is NVM-13: load the MASTER_ECU_KEY and run "
            "DBG_CHAL/DBG_AUTH (erases the keys and undoes the partition) - only with the user on site")


class GuardError(ValueError):
    """A flash run that could leave a part secured or relies on a mass erase the part may refuse."""


# ---------------------------------------------------------------------- configuration field per family

@dataclass(frozen=True)
class ConfigFieldRule:
    family: str
    offset: int                 # from the start of the code flash
    reference: bytes

    @property
    def size(self) -> int:
        return len(self.reference)

    def problems(self, raw: bytes | None) -> list[str]:
        """Why `raw` is not the reference field (None: not in the image). s32k1xx: RM 36.4.1 field layout."""
        if raw is None:
            return ["no flash configuration field: after the erase of sector 0 it reads 0xFF, FSEC 0xFF secures "
                    "the chip at the next reset"]
        if raw == self.reference:
            return []
        fsec, fopt = raw[12], raw[13]
        reasons = []
        if fsec & 0x03 != 0x02:
            reasons.append(f"FSEC 0x{fsec:02X} secures the chip")
        if (fsec >> 4) & 0x03 == 0x02:
            reasons.append(f"FSEC 0x{fsec:02X} disables mass erase")
        if (fsec >> 6) & 0x03 == 0x02:
            reasons.append(f"FSEC 0x{fsec:02X} enables the backdoor key")
        if raw[8:12] != b"\xff" * 4 or raw[14:16] != b"\xff" * 2:
            reasons.append(f"FPROT {raw[8:12].hex()}/FEPROT 0x{raw[14]:02X}/FDPROT 0x{raw[15]:02X} protect flash")
        if fopt != self.reference[13]:
            reasons.append(f"FOPT 0x{fopt:02X} instead of 0x{self.reference[13]:02X}")
        return [f"flash configuration field {raw.hex()} is not the reference {self.reference.hex()}: "
                + ("; ".join(reasons) or "other bytes than the reference (backdoor key, FSEC bits)")]


# s32k1xx: backdoor key (8), FPROT3..0, FSEC 0xFE (unsecured, mass erase enabled, backdoor key disabled), FOPT 0x7F,
# FEPROT, FDPROT - bootloader/platforms/s32k1xx/startup.S and upd_family.h (fcfReference)
CONFIG_FIELDS = {
    "s32k1xx": ConfigFieldRule("s32k1xx", 0x400, bytes([0xFF] * 12 + [0xFE, 0x7F, 0xFF, 0xFF])),
}

# Partition code that means "not partitioned" (s32k1xx: SIM_FCFG1 DEPART 1111b, RM 36.1.2.4.2)
NOT_PARTITIONED = {"s32k1xx": 0xF}


def sector0_problems(image: Mapping[int, int], family: str | None, flash_start: int, first_sector_size: int
                     ) -> list[str]:
    """Problems of an image (address -> byte) that writes the first code flash sector: the configuration field of
    the family has to be complete and the reference. Empty for an image that leaves the sector alone and for a family
    without such a field."""
    rule = CONFIG_FIELDS.get(family or "")
    if rule is None:
        return []
    end = flash_start + first_sector_size
    if not any(flash_start <= a < end for a in image):
        return []
    address = flash_start + rule.offset
    present = [image.get(address + i) for i in range(rule.size)]
    if None in present and any(b is not None for b in present):
        return [f"sector 0 is written with only {sum(b is not None for b in present)} of {rule.size} bytes of the "
                f"flash configuration field at 0x{address:X}: the rest stays erased and may secure the chip"]
    return rule.problems(None if None in present else bytes(present))


def image_of_hex(path: Path) -> dict[int, int]:
    from intelhex import IntelHex

    ih = IntelHex(str(path))
    return {a: ih[a] for a in ih.addresses()}


# ---------------------------------------------------------------------- target of a project

@dataclass(frozen=True)
class Target:
    """What the guard needs from project, board and derivative."""

    project: Path | None
    derivative: str
    family: str | None
    flash_start: int
    first_sector_size: int
    key_choices: tuple[int, ...]        # key space sizes of the derivative (slots), (0,) or () without CSEc
    key_slots: int | None               # nvm.partition.key_slots of the project (None: not named)

    @property
    def csec_capable(self) -> bool:
        return any(k > 0 for k in self.key_choices)


def _yaml(path: Path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def target_of_project(project: Path, mcudb: Path = MCUDB) -> Target:
    project = Path(project)
    data = _yaml(project)
    board_ref = (data.get("project") or {}).get("board")
    if not board_ref:
        raise ValueError(f"{project}: project.board missing")
    board = (_yaml(project.parent / board_ref / "board.yaml").get("board") or {})
    mcu = board.get("mcu")
    found = sorted(Path(mcudb).glob(f"*/{mcu}.yaml")) if mcu else []
    if len(found) != 1:
        raise ValueError(f"{project}: derivative {mcu!r} of the board: {len(found)} files in mcudb")
    deriv = _yaml(found[0])
    flash_name = (data.get("memory") or {}).get("flash")
    flashes = [m for m in deriv.get("memory") or [] if m.get("kind") == "code_flash"
               and (flash_name is None or m.get("name") == flash_name)]
    if not flashes:
        raise ValueError(f"{found[0]}: no code flash {flash_name or ''}".rstrip())
    flash = flashes[0]
    sector = flash.get("sector_size") or ((flash.get("sectors") or [{}])[0].get("size"))
    if not sector:
        raise ValueError(f"{found[0]}: {flash.get('name')} has no sector size")
    keys = tuple(int(k.get("slots", 0)) for k in ((deriv.get("eeprom_emulation") or {}).get("keys") or []))
    partition = ((data.get("nvm") or {}).get("partition") or {})
    slots = partition.get("key_slots")
    info = deriv.get("derivative") or {}
    return Target(project, str(info.get("name", mcu)), info.get("family"), int(flash["start"]), int(sector), keys,
                  int(slots) if slots is not None else None)


# ---------------------------------------------------------------------- can the part be CSEc-partitioned?

@dataclass(frozen=True)
class CsecRisk:
    level: str          # "keys": project takes a key space; "partitioned": read partition code, key space unknown;
    reason: str         # "capable": derivative has CSEc, state unknown; "none": no CSEc or read unpartitioned

    @property
    def refuse_mass_erase(self) -> bool:
        return self.level in ("keys", "partitioned")

    @property
    def warn(self) -> bool:
        return self.level != "none"


def csec_risk(target: Target | None, depart: int | None = None) -> CsecRisk:
    """Whether the part may be CSEc-partitioned (mass erase locked). `depart`: the partition code read from the
    part (s32k1xx SIM_FCFG1 DEPART, e.g. `flexnvm show` of the application console), None if not read."""
    if target is None:
        return CsecRisk("capable", "no project given: the part may have a security module with a key space")
    name = target.derivative
    if not target.csec_capable:
        return CsecRisk("none", f"{name} has no key space of a security module (mcudb eeprom_emulation.keys)")
    if target.key_slots:
        return CsecRisk("keys", f"the project reserves {target.key_slots} CSEc key slots on {name} "
                                "(nvm.partition.key_slots): once partitioned, mass erase is locked")
    unpartitioned = NOT_PARTITIONED.get(target.family or "")
    if depart is not None and unpartitioned is not None:
        if depart != unpartitioned:
            return CsecRisk("partitioned", f"{name} reads partition code 0x{depart:X} (partitioned); whether a key "
                                           "space was taken cannot be read (no Read Resource on s32k1xx, NVM-10)")
        return CsecRisk("none", f"{name} reads partition code 0x{depart:X}: not partitioned")
    return CsecRisk("capable", f"{name} has a CSEc; its partition state was not read (key_slots of the project: "
                               f"{target.key_slots if target.key_slots is not None else 'not named'})")


# ---------------------------------------------------------------------- mass erase in debugger commands

# (pattern, what it does); matched per line, case-insensitive. Sector-wise writes (J-Link loadfile/loadbin,
# OpenOCD "flash write_image erase", pyOCD flash with its default sector erase, CubeProgrammer -w) are not listed.
MASS_ERASE = (
    (r"^\s*erase\s*$", "J-Link 'erase' without a range erases the whole chip"),
    (r"^\s*unlock\b", "J-Link 'unlock' is a mass erase through the MDM-AP"),
    (r"\bexec\s+\w*(unlock|eraseall|masserase)\w*", "J-Link exec command that unlocks or mass erases"),
    (r"\bmass_erase\b", "OpenOCD mass erase (kinetis mdm mass_erase, <driver> mass_erase)"),
    (r"\bmdm\s+mass_erase\b", "OpenOCD Kinetis MDM-AP mass erase"),
    (r"\bflash\s+erase_sector\s+\d+\s+0\s+last\b", "OpenOCD erase of the whole bank"),
    (r"(^|\s)erase\s+(--chip|-c\b)", "pyOCD chip erase"),
    (r"--erase[= ]chip\b|(^|\s)-e\s+chip\b", "pyOCD flash with chip erase"),
    (r"(^|\s)(-e|--erase)\s+all\b", "STM32CubeProgrammer full chip erase"),
)


_COMMENT = re.compile(r"(?:^|\s)(?:#|//).*$")
# J-Link "erase <start>,<end>" / "erase <start> <end>": erases the sectors of a range
_JLINK_RANGE_ERASE = re.compile(r"^\s*erase\s+(0x[0-9a-f]+|\d+)\s*[, ]\s*(0x[0-9a-f]+|\d+)", re.IGNORECASE)


def _code_lines(text: str):
    for number, line in enumerate(text.splitlines(), 1):
        yield number, line, _COMMENT.sub("", line)


def mass_erase_commands(text: str) -> list[tuple[int, str, str]]:
    """(line number, line, what it does) for every mass erase in a command file or command line."""
    hits = []
    for number, line, code in _code_lines(text):
        for pattern, what in MASS_ERASE:
            if re.search(pattern, code, re.IGNORECASE):
                hits.append((number, line.strip(), what))
                break
    return hits


def sector0_erase_commands(text: str, flash_start: int, first_sector_size: int) -> list[tuple[int, str]]:
    """(line number, line) of explicit range erases that take the first code flash sector along (J-Link
    `erase <start>,<end>`): nothing in the same command writes the configuration field back."""
    hits = []
    for number, line, code in _code_lines(text):
        m = _JLINK_RANGE_ERASE.match(code)
        if m:
            start, end = (int(v, 0) for v in m.groups())
            if start < flash_start + first_sector_size and end >= flash_start:
                hits.append((number, line.strip()))
    return hits


# ---------------------------------------------------------------------- one debugger run

def check_debug_flash(commands: str, images: Iterable[tuple[str, Mapping[int, int]]], target: Target | None,
                      depart: int | None = None, log=print) -> CsecRisk:
    """Checks one debugger run before it starts: `commands` (command file or command line), `images` the files it
    writes as (name, address -> byte). Raises GuardError; warnings go to `log`. Returns the CSEc risk."""
    risk = csec_risk(target, depart)
    refusals = []
    if target is not None:
        for name, image in images:
            refusals += [f"{name}: {p}" for p in sector0_problems(image, target.family, target.flash_start,
                                                                   target.first_sector_size)]
        if target.family in CONFIG_FIELDS:
            refusals += [f"line {n} '{line}' erases sector 0 on its own: the flash configuration field stays erased "
                         "until something writes it, a reset in between secures the chip - write sector 0 with an "
                         "image that carries the field (loadfile) instead"
                         for n, line in sector0_erase_commands(commands, target.flash_start,
                                                               target.first_sector_size)]
    erase = mass_erase_commands(commands)
    if erase:
        lines = "; ".join(f"line {n} '{line}': {what}" for n, line, what in erase)
        if risk.refuse_mass_erase:
            refusals.append(f"mass erase refused ({lines}): {risk.reason}; {WAY_BACK}")
        elif risk.warn:
            log(f"WARNING: mass erase ({lines}) on a part that may be CSEc-partitioned: {risk.reason}. If it is, "
                f"the chip refuses it; {WAY_BACK}")
    if refusals:
        raise GuardError("flash run refused (NVM-15):\n  " + "\n  ".join(refusals))
    return risk


def csec_notice(risk: CsecRisk) -> str | None:
    """Text for a run that erases sector 0 (bootloader update), None without a CSEc risk."""
    if not risk.warn:
        return None
    return ("CSEc: " + risk.reason + ".\n"
            "While sector 0 is erased the flash configuration field reads 0xFF; a reset or power loss in that window\n"
            "leaves the chip SECURED, and mass erase (J-Link unlock) is locked on a CSEc-partitioned part: the part\n"
            f"would be lost. Keep the supply stable and do not reset during the update; {WAY_BACK}.")


# ---------------------------------------------------------------------- command line

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="flashguard", description="Checks a debugger flash run against total loss "
                                     "on CSEc devices (NVM-15)")
    parser.add_argument("--project", type=Path, help="project.yaml (derivative, key slots)")
    parser.add_argument("--script", type=Path, action="append", default=[], help="debugger command file")
    parser.add_argument("--image", type=Path, action="append", default=[], help="Intel hex file the run writes")
    parser.add_argument("--depart", type=lambda v: int(v, 0), help="partition code read from the part "
                        "(s32k1xx SIM_FCFG1 DEPART, e.g. 0xF)")
    args = parser.parse_args(argv)
    try:
        target = target_of_project(args.project) if args.project else None
        commands = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in args.script)
        images = [(p.name, image_of_hex(p)) for p in args.image]
    except (OSError, ValueError) as e:
        print(f"flashguard: {e}", file=sys.stderr)
        return 2
    try:
        risk = check_debug_flash(commands, images, target, args.depart)
    except GuardError as e:
        print(f"flashguard: {e}", file=sys.stderr)
        return 1
    print(f"flashguard: ok ({risk.level}: {risk.reason})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
