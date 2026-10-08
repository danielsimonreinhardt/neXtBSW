"""Non-volatile memory of a project (NVM-07): partition of the emulated EEPROM and fixed places of NvM blocks.

Derivative (`eeprom_emulation`: codes of the one-time partition command, write times; `memory` regions of kind
`eeprom_ram` and `data_flash`) + project (`nvm.partition`, `nvm.blocks`) -> NvmPlan. Family neutral and free of Qt:
the codes are data of the derivative, the register encoding of the command (s32k1xx: PGMPART, FCCOB3..5) is the
family code's. Used by tools/bswgen (generated nvm/nvmPartition.h, the display before writing) and meant for
tools/rtegen (layout_blocks: fixed block addresses instead of the name order).

NVM-11: the key space of a security module (s32k1xx CSEc, FCCOB1) is a project value too (`nvm.partition.key_slots`,
sizes from mcudb `eeprom_emulation.keys`); it comes off the usable EEPROM. The display before writing names every split
of the derivative with its cost (data flash, backup, backup:FlexRAM, write endurance of the data sheet from
`eeprom_emulation.endurance`, usable bytes), so the user chooses instead of being given one.

The partition itself is never written by a tool or at start-up. The family port offers it as a console command of a
test build, behind an explicit confirmation that repeats the codes shown here (s32k1xx: "flexnvm partition").

NVM-18: a block is defined explicitly in `nvm.blocks.<Block>` - platform `type`, `length` (1 = scalar, more = vector)
and `integrity` (qm, asil_a..asil_d). The size of the block in the memory follows from the definition (block_bytes:
QM one value with CRC-16 in two places, ASIL two copies with CRC-32 and write counter, libs/bsw/nvm/include/nvm/
Nvm.h). This file checks the definitions and lays the defined blocks out (NvmPlan.layout, the EEPROM bar of the
Studio); tools/rtegen checks the connections nvm.<Block> against them (it knows the port types) and proposes a
definition for a connected block that has none (suggestion(), migration from before NVM-18).

NVM-12: the data flash - what the partition leaves of the partitionable flash (or the whole data flash region of a
derivative without partition) - holds the areas of `nvm.data_flash.areas` (name, size, purpose; calibration data
changed in the device, user 2026-09-28). This file checks that they fit and lays them out in whole sectors
(DataFlashPlan); tools/bswgen generates the table (nvm/nvmDataFlash.h) for dataflash::AreaService
(libs/bsw/nvmDataFlash). A calibration segment of `memory.calibration` in the same region keeps its place at the
start of the region and is left out.
"""

from dataclasses import dataclass, field

from .config import Config, Reporter

NVM = "nvm"
DEM_BLOCKS = ("DemEvents", "DemEntries")        # blocks of the fault memory (tools/rtegen DEM_BLOCKS, NVM-03)
# NVM-05: block of the operating time and reset statistics of the application (tools/rtegen ECU_STATS_BLOCK): created
# by its entry in nvm.blocks, fixed layout of 52 bytes (ecustats::Record), never connected in rte.yaml. Its write rate
# is the wear of the EEPROM: STATS_MIN_INTERVAL_MS is the shortest min_interval_ms the lifetime calculation of NVM-05
# allows (15 years of continuous operation stay below the data sheet write endurance).
ECU_STATS_BLOCK = "EcuStatistics"
ECU_STATS_BYTES = 52
STATS_MIN_INTERVAL_MS = 3600000
ALIGN = 4                                       # slots start on a 32-bit word (one EEPROM record per aligned word)

# NVM-18: block definitions. Sizes of the platform types (32 bit ARM EABI, D11; tools/rtegen PLATFORM_SIZES).
TYPE_BYTES = {"boolean": 1, "uint8": 1, "sint8": 1, "uint16": 2, "sint16": 2, "uint32": 4, "sint32": 4,
              "float32": 4, "float64": 8}
QM_OVERHEAD = 8          # id, size, sequence, CRC-16 (nvm::RECORD_OVERHEAD)
ASIL_OVERHEAD = 12       # id, size, write counter, CRC-32 (nvm::ASIL_OVERHEAD)
MAX_BLOCK_BYTES = 0x7FFF  # size field of the record is 16 bit (tools/rtegen NVM_MAX_BLOCK_BYTES)
INTEGRITY_DEFAULT = "qm"


def is_asil(integrity: str | None) -> bool:
    """True for asil_a .. asil_d: all ASIL levels use the two-copy mechanism of the NvM."""
    return (integrity or INTEGRITY_DEFAULT) != INTEGRITY_DEFAULT


def copy_bytes(size: int, integrity: str | None = None) -> int:
    """Bytes of one of the two places of a block with `size` data bytes (nvm::copyBytes, 4-byte aligned)."""
    overhead = ASIL_OVERHEAD if is_asil(integrity) else QM_OVERHEAD
    return (size + overhead + ALIGN - 1) & ~(ALIGN - 1)


def block_bytes(size: int, integrity: str | None = None) -> int:
    """Bytes a block takes in the memory: both places (nvm::blockBytes)."""
    return 2 * copy_bytes(size, integrity)


def suggestion(name: str, native: str | None, length: int = 1, integrity: str = INTEGRITY_DEFAULT) -> str:
    """The definition proposed for block `name` from the type of its writing port (migration, NVM-18), as the flow
    mapping of project.yaml; None/'' native (a record): only the integrity."""
    parts = []
    if native:
        parts.append(f"type: {native}")
        if length > 1:
            parts.append(f"length: {length}")
    parts.append(f"integrity: {integrity}")
    return f"nvm.blocks.{name}: {{{', '.join(parts)}}}"


@dataclass(frozen=True)
class BlockDefinition:
    """NVM-18: one block as project.yaml defines it."""

    name: str
    type: str
    length: int
    integrity: str

    @property
    def size(self) -> int:
        """Data bytes of the block."""
        return TYPE_BYTES[self.type] * self.length

    @property
    def asil(self) -> bool:
        return is_asil(self.integrity)

    @property
    def bytes(self) -> int:
        """Bytes of the block in the memory (both places)."""
        return block_bytes(self.size, self.integrity)

    def describe(self) -> str:
        shape = self.type if self.length == 1 else f"{self.type}[{self.length}]"
        return f"{shape}, {self.integrity}"


def _kb(n: int) -> str:
    return f"{n // 1024} KB" if n % 1024 == 0 else f"{n} bytes"


def _writes(n: int) -> str:
    if n % 1_000_000 == 0:
        return f"{n // 1_000_000} M"
    if n % 100_000 == 0 and n >= 1_000_000:
        return f"{n / 1_000_000:.1f} M"
    return f"{n // 1000} K" if n % 1000 == 0 else str(n)


def endurance_writes(endurance: tuple[tuple[int, int], ...], backup_bytes: int, used_bytes: int) -> int | None:
    """NVM-11: guaranteed writes (data sheet) when `used_bytes` of the emulated EEPROM are written and
    `backup_bytes` back them up; None below the smallest ratio of the data sheet (it states no figure there).

    The ratio is the one of the EEPROM addresses in use, not of the partitioned size: a place never written creates
    no backup record (AN11983 section 2.1 and 7) - fewer bytes in use raise the ratio."""
    if used_bytes <= 0 or backup_bytes <= 0:
        return None
    met = [writes for ratio, writes in endurance if backup_bytes >= ratio * used_bytes]
    return max(met) if met else None


@dataclass(frozen=True)
class NvmOption:
    """NVM-11: one split the derivative offers, as the display before writing names it (cost and gain)."""

    partition_code: int
    data_flash_bytes: int
    backup_bytes: int
    eeprom_bytes: int           # emulated EEPROM of the partition (0 without backup)
    usable_bytes: int           # after the key space
    endurance_bytes: int | None  # most bytes in use for which the smallest ratio of the data sheet holds

    def describe(self, endurance: tuple[tuple[int, int], ...], chosen: bool) -> str:
        mark = "*" if chosen else " "
        head = (f"{mark} 0x{self.partition_code:X}: data flash {_kb(self.data_flash_bytes)}, "
                f"backup {_kb(self.backup_bytes)}")
        if self.backup_bytes == 0 or self.eeprom_bytes == 0:
            return head + ", no emulated EEPROM"
        ratio = self.backup_bytes / self.eeprom_bytes
        text = head + f", backup:FlexRAM {ratio:g}"
        if not endurance:
            return text + f", usable {self.usable_bytes} bytes, data sheet states no write endurance"
        at_full = endurance_writes(endurance, self.backup_bytes, self.usable_bytes)
        smallest = min(r for r, _ in endurance)
        if at_full is not None:
            return text + f", {_writes(at_full)} writes with all {self.usable_bytes} usable bytes"
        limit = self.endurance_bytes or 0
        first = endurance_writes(endurance, self.backup_bytes, limit) if limit else None
        return (text + f" (no data sheet figure below {smallest}), {_writes(first) if first else '?'} writes only "
                f"if at most {limit} of {self.usable_bytes} usable bytes are written")


@dataclass(frozen=True)
class NvmPartitionPlan:
    """The partition the project wants, with the codes of the derivative's command."""

    eeprom_bytes: int
    eeprom_code: int
    data_flash_bytes: int
    backup_bytes: int
    partition_code: int
    load_on_reset: bool
    write_limit_us: int | None      # time limit of one write to the emulated EEPROM (None: not derivable)
    partition_typ_ms: int | None    # typical duration of the partition command (data sheet), None: not stated
    derivative: str
    verify: bool                    # the code table is marked verify: true in mcudb
    source: str
    # NVM-11: key space of the security module (s32k1xx CSEc, FCCOB1). key_code None: the derivative has a key space
    # but the project names none - the plan cannot be written (the command would fix the space for good).
    key_code: int | None = 0
    key_slots: int = 0
    key_system_slots: int = 0
    key_bytes: int = 0
    key_choices: tuple[int, ...] = ()           # slot counts the derivative offers (empty: no key space)
    sfe_code: int = 0                           # FCCOB2, always 0 (verify-only not offered)
    endurance: tuple[tuple[int, int], ...] = ()  # (ratio, writes) of the data sheet
    options: tuple[NvmOption, ...] = ()          # every split of the derivative, for the display

    @property
    def load_code(self) -> int:
        """Value of the "load during reset" byte of the s32k1xx command: 0 = load (opposite of the IFR bit EEERST)."""
        return 0 if self.load_on_reset else 1

    @property
    def writable(self) -> bool:
        """False while the key space of a derivative that has one is not named (nvm.partition.key_slots)."""
        return self.key_code is not None

    @property
    def usable_bytes(self) -> int:
        """Emulated EEPROM the NvM may use: the partitioned size less the key space at its end."""
        return max(self.eeprom_bytes - self.key_bytes, 0)

    @property
    def endurance_bytes(self) -> int | None:
        """Most EEPROM bytes in use for which the smallest ratio of the data sheet still holds (backup / ratio, at
        most the usable bytes); None without endurance figures or without backup."""
        if not self.endurance or self.backup_bytes == 0:
            return None
        return min(self.backup_bytes // min(r for r, _ in self.endurance), self.usable_bytes)

    @property
    def confirm_word(self) -> str:
        """Word the user types to write the partition: it repeats the command bytes, so the values shown are the
        values confirmed (console "flexnvm partition <word>"). NVM-11: FCCOB1..FCCOB5 in command order,
        "PGMPART-<key>-<sfe>-<load>-<size>-<partition>"; without a named key space the old three-code word, which
        the console command refuses."""
        if self.key_code is None:
            return f"PGMPART-{self.eeprom_code:X}-{self.partition_code:X}-{self.load_code}"
        return (f"PGMPART-{self.key_code:X}-{self.sfe_code:X}-{self.load_code}-{self.eeprom_code:X}-"
                f"{self.partition_code:X}")

    def usage_findings(self, used_bytes: int) -> list[str]:
        """NVM-11: what the NvM layout (`used_bytes` of emulated EEPROM written) costs: beyond the usable bytes it
        does not fit, beyond endurance_bytes the data sheet states no write endurance. Empty: fine."""
        found = []
        if used_bytes > self.usable_bytes:
            found.append(f"{used_bytes} bytes of NvM blocks exceed the {self.usable_bytes} usable bytes of emulated "
                         f"EEPROM ({self.eeprom_bytes} less {self.key_bytes} of key space)")
        elif self.endurance_bytes is not None and used_bytes > self.endurance_bytes:
            smallest = min(r for r, _ in self.endurance)
            found.append(f"{used_bytes} bytes of NvM blocks written: backup {_kb(self.backup_bytes)} is less than "
                         f"{smallest} times that, the data sheet states no write endurance there (at most "
                         f"{self.endurance_bytes} bytes keep {_writes(endurance_writes(self.endurance, self.backup_bytes, self.endurance_bytes) or 0)} writes)")
        return found

    def describe(self) -> list[str]:
        """Plain text shown before the partition is written (bswgen --nvm-partition, console "flexnvm show")."""
        lines = [
            f"derivative {self.derivative}: emulated EEPROM {self.eeprom_bytes} bytes (size code 0x{self.eeprom_code:X}),",
            f"data flash {self.data_flash_bytes // 1024} KB, EEPROM backup {self.backup_bytes // 1024} KB "
            f"(partition code 0x{self.partition_code:X} = {self.partition_code:04b}b),",
            f"load on reset: {'yes' if self.load_on_reset else 'no'} (command byte {self.load_code})",
        ]
        if self.key_code is None:
            lines.append(f"key space: NOT NAMED - {self.derivative} reserves 0, "
                         f"{', '.join(str(s) for s in self.key_choices if s)} key slots; name nvm.partition.key_slots")
            lines.append("NOT WRITABLE: the console command refuses a word without key space (it would fix the "
                         "key space at 0 for good)")
        else:
            key = (f"key space: {self.key_slots} slots ({self.key_slots - self.key_system_slots} for user keys), "
                   f"{self.key_bytes} bytes off the end of the EEPROM" if self.key_slots else "key space: none")
            lines.append(f"{key}; verify-only (SFE) off")
            lines.append(f"command bytes FCCOB0..5: 0x80 0x{self.key_code:02X} 0x{self.sfe_code:02X} "
                         f"0x{self.load_code:02X} 0x{self.eeprom_code:02X} 0x{self.partition_code:02X}")
        lines.append(f"usable EEPROM: {self.usable_bytes} bytes ({self.eeprom_bytes} less {self.key_bytes} of key "
                     f"space)")
        if self.endurance_bytes is not None:
            smallest = min(r for r, _ in self.endurance)
            writes = endurance_writes(self.endurance, self.backup_bytes, self.endurance_bytes)
            lines.append(f"write endurance {_writes(writes or 0)} (data sheet, backup:EEPROM in use >= {smallest}) "
                         f"only while at most {self.endurance_bytes} bytes are written "
                         f"({_kb(self.backup_bytes)} / {smallest}); below {smallest} the data sheet states none")
        if self.options:
            lines.append(f"splits of {self.derivative} (* = this project):")
            lines += [o.describe(self.endurance, o.partition_code == self.partition_code) for o in self.options]
        if self.write_limit_us is not None:
            lines.append(f"time limit of one EEPROM write: {self.write_limit_us} us")
        if self.partition_typ_ms is not None:
            lines.append(f"partition command takes about {self.partition_typ_ms} ms (typical, data sheet)")
        if self.key_slots:
            lines.append("IRREVERSIBLE: with key space the reference manual refuses mass erase once keys are "
                         "allocated ([RM13] 36.5.11.14 note, Tables 36-56/36-60); back only by DBG_CHAL/DBG_AUTH. "
                         "Never give a key WRITE_PROTECTION.")
        else:
            lines.append("IRREVERSIBLE: only a mass erase of the chip undoes the partition.")
        if self.verify:
            lines.append("code table marked verify: true - confirm it in the original reference manual first.")
        return lines


@dataclass
class NvmPlan:
    partition: NvmPartitionPlan | None
    usable_bytes: int                       # emulated EEPROM the blocks may use (partition or window), 0: unknown
    addresses: dict[str, int] = field(default_factory=dict)     # fixed block addresses
    writes: dict[str, tuple[str, int]] = field(default_factory=dict)   # block -> (policy, min_interval_ms)
    # NVM-18: blocks with a definition (type, length, integrity) and the integrity of every entry
    definitions: dict[str, BlockDefinition] = field(default_factory=dict)
    integrity: dict[str, str] = field(default_factory=dict)
    # NVM-18: addresses of the defined blocks, laid out like tools/rtegen (fixed ones kept, the others in name
    # order into the gaps) - without the blocks whose size is not known here (fault memory, blocks without type)
    layout: dict[str, int] = field(default_factory=dict)
    # NVM-12: areas of the data flash (None: nvm.data_flash left out, or it has errors)
    data_flash: "DataFlashPlan | None" = None


@dataclass(frozen=True)
class DataFlashArea:
    """NVM-12: one laid-out area of the data flash; offset from the start of the region."""

    name: str
    offset: int
    size: int
    purpose: str


@dataclass(frozen=True)
class DataFlashPlan:
    """NVM-12: the data flash the project uses and its areas (generated nvm/nvmDataFlash.h)."""

    region: str                 # mcudb memory region of kind data_flash
    start: int                  # its address; offset 0 of the areas
    usable_bytes: int           # what the partition leaves as data flash (the whole region without partition)
    sector_size: int
    partition_code: int | None  # code of the partition the areas rely on (s32k1xx DEPART), None without partition
    reserved_bytes: int         # calibration segment of memory.calibration at the start of the region
    areas: tuple[DataFlashArea, ...]

    @property
    def used_bytes(self) -> int:
        return sum(a.size for a in self.areas)

    @property
    def free_bytes(self) -> int:
        return self.usable_bytes - self.reserved_bytes - self.used_bytes


def _window(cfg: Config):
    return next((r for r in cfg.derivative.memory if r.kind == "eeprom_ram"), None)


def _data_flash(cfg: Config):
    return next((r for r in cfg.derivative.memory if r.kind == "data_flash"), None)


def write_limit_us(emulation, backup_bytes: int) -> int | None:
    """Time limit of one write: the longest write of the backup size (the next larger entry if the size has none),
    times the factor for the first writes after a reset. None without timing."""
    timing = emulation.timing if emulation is not None else None
    if timing is None or backup_bytes == 0:
        return None
    larger = sorted((b for b in timing.backups if b.backup_bytes >= backup_bytes), key=lambda b: b.backup_bytes)
    entry = larger[0] if larger else max(timing.backups, key=lambda b: b.backup_bytes)
    return entry.write_max_us * timing.first_write_factor


def write_limit_of(cfg: Config) -> int | None:
    """Time limit of one EEPROM write of the project: the one of its partition, without a partition the one of the
    largest backup of the derivative (the chip may be partitioned by someone else), None without timing."""
    plan = cfg.nvm
    if plan is not None and plan.partition is not None and plan.partition.write_limit_us is not None:
        return plan.partition.write_limit_us
    emulation = cfg.derivative.eeprom_emulation
    if emulation is None or emulation.timing is None:
        return None
    return write_limit_us(emulation, max(b.backup_bytes for b in emulation.timing.backups))


def partition_typ_ms(emulation, backup_bytes: int) -> int | None:
    timing = emulation.timing if emulation is not None else None
    if timing is None:
        return None
    return next((b.partition_typ_ms for b in timing.backups if b.backup_bytes == backup_bytes), None)


def _partition(cfg: Config, rep: Reporter) -> NvmPartitionPlan | None:
    p = cfg.project.nvm.partition
    where = (NVM, "partition")
    emulation = cfg.derivative.eeprom_emulation
    name = cfg.derivative.derivative.name
    if emulation is None:
        rep.error("project", where, f"{name} describes no partitionable emulated EEPROM (mcudb eeprom_emulation); "
                                    f"remove nvm.partition")
        return None
    size = next((s for s in emulation.sizes if s.eeprom_bytes == p.eeprom_bytes), None)
    if size is None:
        possible = ", ".join(str(s.eeprom_bytes) for s in emulation.sizes)
        rep.error("project", where + ("eeprom_bytes",),
                  f"{p.eeprom_bytes} bytes of emulated EEPROM are not possible on {name} (possible: {possible})")
        return None
    candidates = [c for c in emulation.partitions if c.data_flash_bytes == p.data_flash_bytes]
    if not candidates:
        possible = ", ".join(sorted({f"{c.data_flash_bytes // 1024} KB" for c in emulation.partitions}))
        rep.error("project", where + ("data_flash_bytes",),
                  f"{p.data_flash_bytes} bytes of data flash are not a split of {name} (possible: {possible})")
        return None
    if p.partition_code is not None:
        chosen = next((c for c in candidates if c.code == p.partition_code), None)
        if chosen is None:
            codes = ", ".join(f"0x{c.code:X}" for c in candidates)
            rep.error("project", where + ("partition_code",),
                      f"partition code 0x{p.partition_code:X} does not give {p.data_flash_bytes // 1024} KB of data "
                      f"flash on {name} (codes for it: {codes})")
            return None
    elif len(candidates) > 1:
        codes = ", ".join(f"0x{c.code:X}" for c in candidates)
        rep.error("project", where + ("partition_code",),
                  f"{name} has several codes for {p.data_flash_bytes // 1024} KB of data flash ({codes}); name the "
                  f"one to program in nvm.partition.partition_code")
        return None
    else:
        chosen = candidates[0]
    if size.eeprom_bytes > 0 and chosen.backup_bytes == 0:
        rep.error("project", where, f"{p.eeprom_bytes} bytes of emulated EEPROM need an EEPROM backup, but "
                                    f"{p.data_flash_bytes // 1024} KB of data flash leave none (the command would "
                                    f"be refused)")
        return None
    if size.eeprom_bytes == 0 and chosen.backup_bytes > 0:
        rep.error("project", where, f"an EEPROM backup of {chosen.backup_bytes // 1024} KB needs emulated EEPROM, "
                                    f"but eeprom_bytes is 0 (the command would be refused)")
        return None
    window = _window(cfg)
    if size.eeprom_bytes > 0 and (window is None or size.eeprom_bytes > window.size):
        have = f"{window.name} has {window.size}" if window is not None else "it has no eeprom_ram region"
        rep.error("project", where + ("eeprom_bytes",),
                  f"{p.eeprom_bytes} bytes of emulated EEPROM do not fit the RAM window of {name} ({have})")
        return None
    flash = _data_flash(cfg)
    if flash is not None and chosen.data_flash_bytes + chosen.backup_bytes != flash.size:
        rep.error("project", where + ("data_flash_bytes",),
                  f"partition code 0x{chosen.code:X} of {name} splits {chosen.data_flash_bytes + chosen.backup_bytes} "
                  f"bytes, the data flash region {flash.name} has {flash.size} (mcudb eeprom_emulation inconsistent)")
        return None
    if not p.load_on_reset and size.eeprom_bytes > 0:
        rep.warning("project", where + ("load_on_reset",),
                    "without load on reset the emulated EEPROM is not ready after a reset; the start-up launches no "
                    "flash command (SETRAM), so the NvM would run on its defaults")
    keys = _key_space(cfg, rep, size, chosen)
    if keys is False:
        return None
    endurance = tuple(sorted((e.ratio, e.writes) for e in emulation.endurance))
    key_bytes = keys.eeprom_bytes if keys is not None else 0
    largest = max(s.eeprom_bytes for s in emulation.sizes)
    options = []
    for c in sorted(emulation.partitions, key=lambda c: c.code):
        eeprom = largest if c.backup_bytes else 0
        usable = max(eeprom - key_bytes, 0) if eeprom else 0
        limit = min(c.backup_bytes // endurance[0][0], usable) if endurance and eeprom else None
        options.append(NvmOption(c.code, c.data_flash_bytes, c.backup_bytes, eeprom, usable, limit))
    return NvmPartitionPlan(size.eeprom_bytes, size.code, chosen.data_flash_bytes, chosen.backup_bytes, chosen.code,
                            p.load_on_reset, write_limit_us(emulation, chosen.backup_bytes),
                            partition_typ_ms(emulation, chosen.backup_bytes), name, emulation.verify,
                            emulation.source or "",
                            key_code=keys.code if keys is not None else (None if emulation.keys else 0),
                            key_slots=keys.slots if keys is not None else 0,
                            key_system_slots=keys.system_slots if keys is not None and keys.slots else 0,
                            key_bytes=key_bytes,
                            key_choices=tuple(k.slots for k in emulation.keys),
                            endurance=endurance, options=tuple(options))


def _key_space(cfg: Config, rep: Reporter, size, chosen):
    """NVM-11: the key space entry of the derivative the project names (nvm.partition.key_slots); None if not named
    (warning where the derivative has a key space), False after an error."""
    p = cfg.project.nvm.partition
    where = (NVM, "partition", "key_slots")
    emulation = cfg.derivative.eeprom_emulation
    name = cfg.derivative.derivative.name
    if p.key_verify_only:
        rep.error("project", (NVM, "partition", "key_verify_only"),
                  "verify-only keys (security flag extension, s32k1xx SFE) are not offered yet: the partition command "
                  "writes the flag off (NVM-11)")
        return False
    if p.key_slots is None:
        # no warning (yet): the demo project names no key space until the coordinator adds it, and its checks are
        # kept free of warnings; the display says NOT WRITABLE and the console command refuses the old word
        return None
    if not emulation.keys:
        if p.key_slots == 0:
            return None
        rep.error("project", where, f"{name} has no key space for a security module (mcudb eeprom_emulation.keys); "
                                    f"key_slots must be 0 or left out")
        return False
    keys = next((k for k in emulation.keys if k.slots == p.key_slots), None)
    if keys is None:
        rep.error("project", where, f"{p.key_slots} key slots are not possible on {name} (possible: "
                                    f"{', '.join(str(k.slots) for k in emulation.keys)})")
        return False
    if keys.slots == 0:
        return keys
    # [RM13] 36.5.11.14 Table 36-69: FCCOB1 > 0 is refused without EEPROM backup and with FCCOB3[0] = 1
    if chosen.backup_bytes == 0:
        rep.error("project", where, f"a key space needs an EEPROM backup, but {chosen.data_flash_bytes // 1024} KB "
                                    f"of data flash leave none (the command would be refused)")
        return False
    if not p.load_on_reset:
        rep.error("project", where, "a key space needs load_on_reset: true (the command refuses key space with the "
                                    "FlexRAM not loaded during reset)")
        return False
    largest = max(s.eeprom_bytes for s in emulation.sizes)
    if size.eeprom_bytes != largest:
        rep.error("project", where, f"a key space needs the full emulated EEPROM of {name} ({largest} bytes, the "
                                    f"security module fixes the size), eeprom_bytes is {size.eeprom_bytes}")
        return False
    if keys.eeprom_bytes >= size.eeprom_bytes:
        rep.error("project", where, f"{keys.slots} key slots take {keys.eeprom_bytes} bytes, the emulated EEPROM "
                                    f"has {size.eeprom_bytes}")
        return False
    return keys


def _rte_blocks(cfg: Config) -> set[str] | None:
    """Block names of the ECU description (targets nvm.<Block>), None if there is none."""
    if cfg.rte is None:
        return None
    names = set()
    for targets in cfg.rte.connections.values():
        for target in targets if isinstance(targets, list) else [targets]:
            text = str(target)
            if text.startswith(NVM + "."):
                names.add(text[len(NVM) + 1:])
    return names


def check(cfg: Config, rep: Reporter) -> NvmPlan | None:
    use = cfg.project.nvm
    if use is None:
        return None
    partition = _partition(cfg, rep) if use.partition is not None else None
    window = _window(cfg)
    # NVM-11: the key space comes off the end of the emulated EEPROM
    usable = partition.usable_bytes if partition is not None else (window.size if window is not None else 0)
    plan = NvmPlan(partition, usable)
    endurance_limit = partition.endurance_bytes if partition is not None else None
    known = _rte_blocks(cfg)
    for name, block in use.blocks.items():
        where = (NVM, "blocks", name)
        _definition(plan, name, block, rep)
        if known is not None and name not in known and name not in DEM_BLOCKS and name != ECU_STATS_BLOCK:
            what = "the block is defined, but" if block.type is not None else "the entry has no effect:"
            rep.warning("project", where, f"{what} no connection of the ECU description writes nvm.{name}; connect "
                                          f"a provided port to nvm.{name}")
        if block.address is not None:
            if block.address % ALIGN:
                rep.error("project", where + ("address",), f"address 0x{block.address:X} is not a multiple of {ALIGN}")
            elif usable and block.address >= usable:
                rep.error("project", where + ("address",),
                          f"address 0x{block.address:X} lies outside the {usable} bytes of emulated EEPROM")
            else:
                plan.addresses[name] = block.address
                if endurance_limit is not None and block.address >= endurance_limit:
                    rep.warning("project", where + ("address",),
                                f"address 0x{block.address:X} lies beyond the {endurance_limit} bytes for which the "
                                f"data sheet write endurance holds (EEPROM backup / "
                                f"{min(r for r, _ in partition.endurance)})")
        if block.min_interval_ms is not None and block.write != "change":
            rep.error("project", where + ("min_interval_ms",), "min_interval_ms only applies with write: change")
        if block.write is not None:
            plan.writes[name] = (block.write.upper(), block.min_interval_ms or 0)
        if name == ECU_STATS_BLOCK:
            _stats_rate(block, rep)
    _layout(plan, rep)
    if use.data_flash is not None:
        plan.data_flash = _data_flash_plan(cfg, rep, partition)
    return plan


def _data_flash_plan(cfg: Config, rep: Reporter, partition: NvmPartitionPlan | None) -> DataFlashPlan | None:
    """NVM-12: checks nvm.data_flash against the data flash of the derivative and the partition and lays the areas
    out (fixed ones where the project puts them, the others in name order into the first gap, whole sectors)."""
    use = cfg.project.nvm
    where = (NVM, "data_flash")
    name = cfg.derivative.derivative.name
    errors = len(rep.messages)
    region = _data_flash(cfg)
    if region is None:
        rep.error("project", where, f"{name} has no data flash (mcudb memory of kind data_flash); remove "
                                    f"nvm.data_flash")
        return None
    sizes = sorted({size for _, size in region.sector_table()})
    if len(sizes) != 1:
        rep.error("derivative", ("memory",), f"data flash {region.name} of {name} has no uniform sector size, the "
                                             f"areas of nvm.data_flash need one")
        return None
    sector = sizes[0]
    code = None
    if cfg.derivative.eeprom_emulation is not None:
        if use.partition is None:
            rep.error("project", where, f"the data flash of {name} is what the partition leaves of {region.name}: "
                                        f"nvm.partition must say how much (data_flash_bytes)")
            return None
        if partition is None:
            return None                         # the partition has errors of its own
        usable, code = partition.data_flash_bytes, partition.partition_code
        if usable == 0:
            rep.error("project", where, f"nvm.partition leaves no data flash (data_flash_bytes 0, partition code "
                                        f"0x{code:X}); choose a split with data flash or remove nvm.data_flash")
            return None
    else:
        usable = region.size
    reserved = 0
    calibration = cfg.project.memory.calibration if cfg.project.memory is not None else None
    if calibration is not None and calibration.region == region.name:
        reserved = calibration.size
    areas = use.data_flash.areas
    if not areas:
        rep.error("project", where + ("areas",), "name at least one area (name, size, purpose) or remove "
                                                 "nvm.data_flash")
        return None
    for area_name, area in areas.items():
        at = where + ("areas", area_name)
        if area.size % sector:
            rep.error("project", at + ("size",), f"{area.size} bytes are no multiple of the sector size {sector} of "
                                                 f"{region.name} (whole sectors, so that the area can be erased on "
                                                 f"its own)")
        if area.offset is not None and area.offset % sector:
            rep.error("project", at + ("offset",), f"offset 0x{area.offset:X} is not on a sector of {region.name} "
                                                   f"(sector size 0x{sector:X})")
    if any(m.severity == "error" for m in rep.messages[errors:]):
        return None
    total = sum(a.size for a in areas.values())
    what = (f"the {usable} bytes of data flash of nvm.partition (partition code 0x{code:X})" if code is not None
            else f"the {usable} bytes of {region.name}")
    if reserved:
        what += f", less {reserved} of the calibration segment (memory.calibration)"
    if reserved + total > usable:
        rep.error("project", where + ("areas",),
                  f"the areas need {total} bytes, they do not fit into {what}: {reserved + total - usable} bytes "
                  f"too many; make areas smaller or choose a split with more data flash")
        return None
    fixed = {n: a.offset for n, a in areas.items() if a.offset is not None}
    try:
        offsets = layout_areas([(n, areas[n].size) for n in sorted(areas)], fixed, usable, sector, reserved)
    except ValueError as e:
        head, _, reason = str(e).partition(": ")
        area_name = head.rpartition(".")[2]
        at = where + ("areas", area_name, "offset") if area_name in fixed else where + ("areas",)
        rep.error("project", at, reason)
        return None
    laid = tuple(DataFlashArea(n, offsets[n], areas[n].size, areas[n].purpose)
                 for n in sorted(areas, key=lambda n: (offsets[n], n)))
    return DataFlashPlan(region.name, region.start, usable, sector, code, reserved, laid)


def layout_areas(sizes: list[tuple[str, int]], fixed: dict[str, int], usable: int, sector: int,
                 reserved: int = 0) -> dict[str, int]:
    """NVM-12: offsets of the data flash areas - fixed ones where the project puts them, the others in the given
    order into the first gap that holds them, on sector boundaries, behind `reserved` bytes at the start. Raises
    ValueError "nvm.data_flash.areas.<name>: <reason>" (overlap, outside, no room)."""
    size_of = dict(sizes)
    taken: list[tuple[int, int, str]] = [(0, reserved, "the calibration segment")] if reserved else []
    for name, offset in sorted(fixed.items(), key=lambda item: item[1]):
        end = offset + size_of[name]
        if offset % sector:
            raise ValueError(f"nvm.data_flash.areas.{name}: offset 0x{offset:X} is not on a sector (0x{sector:X})")
        if end > usable:
            raise ValueError(f"nvm.data_flash.areas.{name}: 0x{offset:X}..0x{end - 1:X} lies outside the {usable} "
                             f"bytes of data flash")
        for start, stop, other in taken:
            if offset < stop and start < end:
                raise ValueError(f"nvm.data_flash.areas.{name}: 0x{offset:X}..0x{end - 1:X} overlaps {other} "
                                 f"(0x{start:X}..0x{stop - 1:X})")
        taken.append((offset, end, name))
    result = dict(fixed)
    for name, size in sizes:
        if name in fixed:
            continue
        offset = 0
        for start, stop, _ in sorted(taken):
            if _up(offset, sector) + size <= start:
                break
            offset = max(offset, stop)
        offset = _up(offset, sector)
        if offset + size > usable:
            raise ValueError(f"nvm.data_flash.areas: no room for {name} ({size} bytes) in the {usable} bytes of data "
                             f"flash (the fixed areas leave gaps too small)")
        taken.append((offset, offset + size, name))
        result[name] = offset
    return result


def _up(value: int, unit: int) -> int:
    return (value + unit - 1) // unit * unit


def _definition(plan: NvmPlan, name: str, block, rep: Reporter) -> None:
    """NVM-18: checks the definition of one block and records it in the plan."""
    where = (NVM, "blocks", name)
    plan.integrity[name] = block.integrity or INTEGRITY_DEFAULT
    if name in DEM_BLOCKS:
        for key in ("type", "length"):
            if getattr(block, key) is not None:
                rep.error("project", where + (key,),
                          f"{name} is a block of the fault memory: its layout comes from the diagnostic description "
                          f"(diag.yaml fault_memory); remove {key}")
        return
    if name == ECU_STATS_BLOCK:
        # NVM-05: layout of the application (ecustats::Record), the size is known here for the layout and the Studio
        for key in ("type", "length"):
            if getattr(block, key) is not None:
                rep.error("project", where + (key,),
                          f"{name} is the block of the operating time and reset statistics (NVM-05): its layout of "
                          f"{ECU_STATS_BYTES} bytes comes from the application; remove {key}")
        plan.definitions[name] = BlockDefinition(name, "uint8", ECU_STATS_BYTES, block.integrity or INTEGRITY_DEFAULT)
        return
    if block.type is None:
        if block.length is not None:
            rep.error("project", where + ("length",), "length needs a type (the platform type of one element)")
        return
    definition = BlockDefinition(name, block.type, block.length or 1, block.integrity or INTEGRITY_DEFAULT)
    if definition.size > MAX_BLOCK_BYTES:
        rep.error("project", where + ("length",),
                  f"{definition.length} x {definition.type} are {definition.size} bytes, a block holds at most "
                  f"{MAX_BLOCK_BYTES}")
        return
    plan.definitions[name] = definition


def _stats_rate(block, rep: Reporter) -> None:
    """NVM-05: the write rate of the statistics block against the lifetime of the EEPROM (warnings, the project may
    decide otherwise for a short-lived device)."""
    where = (NVM, "blocks", ECU_STATS_BLOCK)
    if block.write != "change":
        rep.warning("project", where + ("write",),
                    f"{ECU_STATS_BLOCK} is written only at the run-down without write: change - the counts of a start "
                    f"that ends in a reset or a power cut are lost; recommended: write: change, min_interval_ms: "
                    f"{STATS_MIN_INTERVAL_MS}")
    elif (block.min_interval_ms or 0) < STATS_MIN_INTERVAL_MS:
        rep.warning("project", where + ("min_interval_ms",),
                    f"min_interval_ms {block.min_interval_ms or 0} is below {STATS_MIN_INTERVAL_MS} (1 h): in 15 years "
                    f"of continuous operation the operating time alone writes the block more often than the data "
                    f"sheet write endurance of the emulated EEPROM allows (NVM-05)")


def _layout(plan: NvmPlan, rep: Reporter) -> None:
    """NVM-18: lays the defined blocks out like tools/rtegen and checks the fixed places among them."""
    if not plan.definitions:
        return
    sizes = [(n, d.bytes) for n, d in sorted(plan.definitions.items())]
    fixed = {n: a for n, a in plan.addresses.items() if n in plan.definitions}
    try:
        plan.layout = layout_blocks(sizes, fixed, plan.usable_bytes)
    except ValueError as e:
        head, _, reason = str(e).partition(": ")
        name = head.rpartition(".")[2]
        where = (NVM, "blocks", name, "address") if name in fixed else (NVM, "blocks")
        rep.error("project", where, f"{reason} (sizes from the definitions in nvm.blocks)")
        return
    end = max(plan.layout[n] + d.bytes for n, d in plan.definitions.items())
    if plan.partition is not None:
        for finding in plan.partition.usage_findings(end):
            report = rep.error if "exceed" in finding else rep.warning
            report("project", (NVM, "blocks"), finding + " (the defined blocks alone)")


def _align(address: int) -> int:
    return (address + ALIGN - 1) & ~(ALIGN - 1)


def layout_blocks(sizes: list[tuple[str, int]], fixed: dict[str, int], usable: int) -> dict[str, int]:
    """Addresses of the blocks: fixed ones where the project puts them, the others in the given order into the first
    gap that holds them. `sizes`: (name, bytes of both slots) in layout order. Raises ValueError with the reason
    (overlap, outside, no room) - the caller reports it with the YAML path nvm.blocks.<name>."""
    size_of = dict(sizes)
    for name in fixed:
        if name not in size_of:
            raise ValueError(f"nvm.blocks.{name}: no such block")
    taken: list[tuple[int, int, str]] = []
    for name, address in sorted(fixed.items(), key=lambda item: item[1]):
        end = address + size_of[name]
        if address % ALIGN:
            raise ValueError(f"nvm.blocks.{name}: address 0x{address:X} is not a multiple of {ALIGN}")
        if usable and end > usable:
            raise ValueError(f"nvm.blocks.{name}: 0x{address:X}..0x{end - 1:X} lies outside the {usable} bytes")
        for start, stop, other in taken:
            if address < stop and start < end:
                raise ValueError(f"nvm.blocks.{name}: 0x{address:X}..0x{end - 1:X} overlaps {other} "
                                 f"(0x{start:X}..0x{stop - 1:X})")
        taken.append((address, end, name))
    result = dict(fixed)
    for name, size in sizes:
        if name in fixed:
            continue
        address = 0
        for start, stop, _ in sorted(taken):
            if _align(address) + size <= start:
                break
            address = max(address, stop)
        address = _align(address)
        if usable and address + size > usable:
            raise ValueError(f"nvm.blocks: no room for {name} ({size} bytes) in the {usable} bytes of emulated EEPROM")
        taken.append((address, address + size, name))
        result[name] = address
    return result
