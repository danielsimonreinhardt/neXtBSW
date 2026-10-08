"""Memory partitioning and bootloader bus of a project (doc/architecture/README.md, sections 4 and 7).

Derivative (memory regions, sectors, program unit) + project (`memory`, `bootloader`) -> one MemoryLayout. The
generators (tools/bswgen/blgen.py) write from it the linker values of bootloader, updater and application and the
bootloader's C header, which tools/canflash reads as well. Family neutral, no Qt; findings with YAML path.

Conventions of the bootloader interface (they keep an already flashed bootloader compatible):
- bootloader from the start of the code flash, validity record in its last sector, the device key of the security
  access in the sector below (BL-03), application in between
- boot request word pair BOOT_REQUEST_FROM_END bytes below the end of the application RAM, inside the no-init area
- SYS-89: the calibration segment (memory.calibration) in the code flash lies directly behind the first sector of the
  application: the vector table stays at the start of the application, the bootloader keeps its view of the
  application area (the segment is part of it until BL-31 flashes it separately), and the download of an
  application stays as long as its image instead of stretching to the end of the flash
"""

import re
import zlib
from dataclasses import dataclass
from pathlib import Path

from .config import REPO, Config, Reporter, family_ports_dir

BOOT_REQUEST_FROM_END = 0x10        # boot request words at end - 0x10 .. end - 0x08 (bl_interface.h)
FAILED_START_RECORD_SIZE = 0x08     # PLAT-29: failed-start record of the SBC early start, below the boot request
HARD_FAULT_DUMP_LAYOUT = "hardFaultHandler/**/hardFaultDumpLayout.h"      # in platforms/<family>/, PLAT-20
RECORD_SIZE = 24                    # app_record_t, bootloader/src/core/app_image.h
CAL_HEADER_SIZE = 32                # SYS-89: CALV header of the calibration segment (ecu/calibration)
# Default of the application linker template for the RAM functions (platforms/<family>/port/main/linkerscript);
# only needed to report a project whose RAM is too small for it (PLAT-12).
DEFAULT_RAMFUNCTIONS_SIZE = 0x1000
# SYS-132: interrupt stack (multiple of the 8-byte stack alignment) and its guard (multiple of the MPU granularity;
# the family linker template asserts its own granularity, 32 bytes is the smallest of the supported MPUs)
ISR_STACK_ALIGNMENT = 8
ISR_STACK_GUARD_GRANULARITY = 32
DEVICE_HOOKS_NAMES = ("bootloader", "devices")      # bootloader/devices/<module>: bootloader hook of a device module
# BL-28: modules whose bootloader hook can serve the watchdog of the device (bootloader.sbc_watchdog: serve)
WATCHDOG_SERVING_HOOKS = frozenset({"sbcUja113x"})


def device_hooks(tree: Path | None = None) -> Path:
    """Bootloader hooks of a tree (TOOL-85: of the tree the tools run as, cfg.running_tree)."""
    return (REPO if tree is None else Path(tree)).joinpath(*DEVICE_HOOKS_NAMES)


@dataclass(frozen=True)
class RamArea:
    region: str
    start: int
    size: int

    @property
    def end(self) -> int:
        return self.start + self.size


@dataclass(frozen=True)
class CalibrationLayout:
    """SYS-89: place and behaviour of the calibration segment (memory.calibration); `end` is exclusive."""

    region: str
    start: int
    size: int
    sector_size: int
    in_application_flash: bool      # True: inside the application area of the code flash (bootloader's view)
    on_invalid: str                 # refuse | defaults | warn
    production: bool                # profile production: values read from flash, no RAM working page, no XCP

    @property
    def end(self) -> int:
        return self.start + self.size


@dataclass(frozen=True)
class MemoryLayout:
    """Addresses in bytes; every `end` is exclusive."""

    flash_region: str
    flash_start: int
    flash_size: int
    sector_size: int                # the one sector size of the flash region (bootloader core)
    program_size: int               # smallest programmable unit (phrase)
    bootloader_size: int
    record_address: int             # validity record, last sector
    bootloader_ram: RamArea
    stack_size: int
    app_ram: tuple[RamArea, ...]    # contiguous, address order
    noinit_size: int
    # PLAT-12: hot part of the application RAM as a project value; None: default of the linker template
    ramfunctions_size: int | None = None
    hot_ram_size: int | None = None
    # BL-03: key sector of the device key, the sector directly below the record sector. Outside the bootloader area
    # (the updater rewrites that whole area) and outside the application area (every download erases there), so
    # neither a bootloader update nor a download removes a provisioned key. None: no key sector.
    key_address: int | None = None
    # SYS-89: calibration segment; None: the project has none (memory.calibration left out)
    calibration: CalibrationLayout | None = None
    # SYS-132: interrupt stack (MSP) and its MPU guard as project values; None: default of the linker template
    isr_stack_size: int | None = None
    isr_stack_guard_size: int | None = None

    @property
    def flash_end(self) -> int:
        return self.flash_start + self.flash_size

    @property
    def app_start(self) -> int:
        return self.flash_start + self.bootloader_size

    @property
    def app_end(self) -> int:
        return self.key_address if self.key_address is not None else self.record_address

    @property
    def key_size(self) -> int:
        """Bytes of the key sector (one sector, 0 without key sector)."""
        return 0 if self.key_address is None else self.record_address - self.key_address

    @property
    def ram_start(self) -> int:
        return self.app_ram[0].start

    @property
    def ram_end(self) -> int:
        return self.app_ram[-1].end

    @property
    def noinit_start(self) -> int:
        return self.ram_end - self.noinit_size

    @property
    def boot_request_address(self) -> int:
        return self.ram_end - BOOT_REQUEST_FROM_END


@dataclass(frozen=True)
class DeviceHook:
    """Board device whose module has a bootloader hook (bootloader/devices/<module>)."""

    device: str
    module: str
    init: bool                      # switched on before CAN (bootloader.init); otherwise only read
    serve_watchdog: bool = False    # BL-28: the hook serves the watchdog of the device (bootloader.sbc_watchdog)


@dataclass(frozen=True)
class BootloaderPlan:
    can: str                        # board CAN bus
    hooks: tuple[DeviceHook, ...]
    sbc_watchdog: str = "leave"     # BL-28: bootloader.sbc_watchdog

    @property
    def watchdog_served_by(self) -> tuple[str, ...]:
        """Modules whose hook serves the watchdog of its device (generated into bl_board.cmake)."""
        return tuple(h.module for h in self.hooks if h.serve_watchdog)


def target_id(board: str, derivative: str) -> int:
    """Board/derivative identity of the validity record (BL-02): CRC-32 over "<board>/<derivative>".

    One source for the generated header (BL_TARGET_ID, written into the record and compared by the bootloader),
    for tools/canflash (which computes it for a project without a build directory) and for the host tests.
    """
    return zlib.crc32(f"{board}/{derivative}".encode("ascii"))


def _hex(value: int) -> str:
    return f"0x{value:X}"


def _layout(cfg: Config, rep: Reporter) -> MemoryLayout | None:
    use = cfg.project.memory
    d = cfg.derivative
    name = d.derivative.name
    regions = {r.name: (i, r) for i, r in enumerate(d.memory)}
    errors = len(rep.messages)

    def region(path, key: str, kind: str):
        entry = regions.get(key)
        if entry is None or entry[1].kind != kind:
            options = [n for n, (_, r) in regions.items() if r.kind == kind]
            rep.error("project", path, f"{name} has no {kind} region {key} (available: {', '.join(options) or '-'})")
            return None, None
        return entry

    # --- flash: bootloader from the start, validity record in the last sector, application in between
    index, flash = region(("memory", "flash"), use.flash, "code_flash")
    sector_size = record = key_sector = None
    if flash is not None:
        table = flash.sector_table()
        sizes = sorted({size for _, size in table})
        if len(sizes) > 1:
            rep.error("project", ("memory", "flash"), f"{flash.name} of {name} has sectors of different sizes "
                      f"({', '.join(_hex(s) for s in sizes)}): the bootloader core needs one sector size (PLAT-16)")
        elif sizes:
            sector_size = sizes[0]
        if flash.program_size is None:
            rep.error("derivative", ("memory", index), f"{flash.name}: program_size (smallest programmable unit) is "
                      "missing, the bootloader needs it")
        if table:
            record = table[-1][0]
            # BL-03: the sector below the record holds the device key of the bootloader's security access
            key_sector = table[-2][0] if len(table) > 1 else record
            boundaries = [start - flash.start for start, _ in table] + [flash.size]
            size = use.bootloader.size
            if size not in boundaries:
                below = max(b for b in boundaries if b < size) if size > 0 else 0
                above = min((b for b in boundaries if b > size), default=None)
                rep.error("project", ("memory", "bootloader", "size"), f"bootloader size {_hex(size)} ends inside a "
                          f"sector of {flash.name} (nearest sector boundaries {_hex(below)}"
                          + (f", {_hex(above)})" if above is not None else ")"))
            elif flash.start + size >= key_sector:
                rep.error("project", ("memory", "bootloader", "size"), f"bootloader size {_hex(size)} leaves no "
                          f"application area in {flash.name} ({_hex(flash.size)} bytes, the last two sectors at "
                          f"{_hex(key_sector)} and {_hex(record)} hold the device key (BL-03) and the validity "
                          "record)")

    # --- RAM of the bootloader
    _, bl_region = region(("memory", "bootloader", "ram"), use.bootloader.ram, "ram")
    bl_ram = None
    if bl_region is not None:
        ram_size = use.bootloader.ram_size if use.bootloader.ram_size is not None else bl_region.size
        if ram_size > bl_region.size:
            rep.error("project", ("memory", "bootloader", "ram_size"), f"bootloader RAM {_hex(ram_size)} exceeds "
                      f"{bl_region.name} ({_hex(bl_region.size)} bytes)")
        elif use.bootloader.stack_size >= ram_size:
            rep.error("project", ("memory", "bootloader", "stack_size"), f"stack {_hex(use.bootloader.stack_size)} "
                      f"leaves no bootloader RAM ({_hex(ram_size)} bytes)")
        else:
            bl_ram = RamArea(bl_region.name, bl_region.start, ram_size)

    # --- RAM of the application: contiguous regions, no-init area at the end of the last one
    app_ram: list[RamArea] = []
    seen: set[str] = set()
    for i, key in enumerate(use.application.ram):
        path = ("memory", "application", "ram", i)
        if key in seen:
            rep.error("project", path, f"RAM region {key} listed twice")
            continue
        seen.add(key)
        _, r = region(path, key, "ram")
        if r is None:
            continue
        if app_ram and r.start != app_ram[-1].end:
            rep.error("project", path, f"application RAM must be contiguous in address order: {app_ram[-1].region} "
                      f"ends at {_hex(app_ram[-1].end)}, {key} starts at {_hex(r.start)} (the stack pointer checks "
                      "of bootloader and hard fault handler use one RAM range)")
        app_ram.append(RamArea(r.name, r.start, r.size))
    noinit = use.application.noinit_size
    if app_ram:
        last = app_ram[-1]
        if noinit > last.size:
            rep.error("project", ("memory", "application", "noinit_size"), f"no-init area {_hex(noinit)} exceeds "
                      f"{last.region} ({_hex(last.size)} bytes)")
        elif noinit < BOOT_REQUEST_FROM_END or noinit % 4:
            rep.error("project", ("memory", "application", "noinit_size"), f"no-init area {_hex(noinit)} must be a "
                      f"multiple of 4 and hold the boot request ({_hex(BOOT_REQUEST_FROM_END)} bytes at the end)")
        elif noinit < (needed := (minimum := noinit_minimum(cfg))[0]):
            # PLAT-29: before this the configuration accepted 0x100 and only the linker script refused it
            rep.error("project", ("memory", "application", "noinit_size"), f"no-init area {_hex(noinit)} is smaller "
                      f"than the minimum {_hex(needed)} of the family {cfg.derivative.derivative.family}: "
                      f"{minimum[1]} (the records of the modules in .noinit.* come on top - the memory "
                      "report of tools/test shows the used part per profile)")
        elif bl_ram is not None and bl_ram.start < last.end and last.end - noinit < bl_ram.end:
            rep.error("project", ("memory", "bootloader", "ram"), f"bootloader RAM {_hex(bl_ram.start)}.."
                      f"{_hex(bl_ram.end - 1)} overlaps the application no-init area {_hex(last.end - noinit)}.."
                      f"{_hex(last.end - 1)} (boot request, hard fault dump): reduce memory.bootloader.ram_size")

    # --- hot part of the application RAM (PLAT-12): RAM functions and hot data/bss at the start of the RAM
    ramfunctions = use.application.ramfunctions_size
    hot = use.application.hot_ram_size
    for key, value in (("ramfunctions_size", ramfunctions), ("hot_ram_size", hot)):
        if value is not None and value % 4:
            rep.error("project", ("memory", "application", key),
                      f"{key} {_hex(value)} must be a multiple of 4 (linker regions of the hot RAM)")
    if app_ram:
        total = sum(r.size for r in app_ram)
        default_ramfunctions = DEFAULT_RAMFUNCTIONS_SIZE
        effective_ramfunctions = ramfunctions if ramfunctions is not None else default_ramfunctions
        if hot is not None and hot <= effective_ramfunctions:
            rep.error("project", ("memory", "application", "hot_ram_size"), f"hot RAM {_hex(hot)} leaves no room for "
                      f"hot data behind the RAM functions ({_hex(effective_ramfunctions)} bytes, "
                      "memory.application.ramfunctions_size)")
        elif hot is not None and hot + noinit >= total:
            rep.error("project", ("memory", "application", "hot_ram_size"), f"hot RAM {_hex(hot)} and no-init area "
                      f"{_hex(noinit)} leave no room for data, heap and stack in {_hex(total)} bytes of "
                      "application RAM")
        elif hot is None and effective_ramfunctions + noinit >= total:
            rep.error("project", ("memory", "application", "ramfunctions_size"), f"RAM functions "
                      f"{_hex(effective_ramfunctions)} and no-init area {_hex(noinit)} leave no room for hot data, "
                      f"data, heap and stack in {_hex(total)} bytes of application RAM (memory.application: "
                      "hot_ram_size and ramfunctions_size)")

    # --- interrupt stack (MSP) and its guard (SYS-132, safety review SYS-97 S8): project value or template default
    isr_stack, isr_guard = _isr_stack(cfg, rep)

    if len(rep.messages) != errors and any(m.severity == "error" for m in rep.messages[errors:]):
        return None
    calibration = None
    if use.calibration is not None:
        calibration = _calibration(cfg, rep, flash, flash.start + use.bootloader.size, key_sector, sector_size)
        if calibration is None:
            return None
    return MemoryLayout(flash.name, flash.start, flash.size, sector_size, flash.program_size, use.bootloader.size,
                        record, bl_ram, use.bootloader.stack_size, tuple(app_ram), noinit, ramfunctions, hot,
                        key_sector, calibration, isr_stack, isr_guard)


def noinit_minimum(cfg: Config) -> tuple[int, str]:
    """PLAT-29: smallest no-init area the family linker template accepts and what it consists of (text for the
    message). Every family: the boot request word pair (BOOT_REQUEST_FROM_END bytes at the end); a family with a hard
    fault dump layout (platforms/<family>/hardFaultHandler, PLAT-20) adds the dump and the 8 byte failed-start record
    below the boot request. Read from the tree the tools run as (TOOL-85); the records of the modules in
    `.noinit.*` are not part of it (they depend on the features and are measured by the memory report)."""
    base = BOOT_REQUEST_FROM_END
    family = cfg.derivative.derivative.family
    folder = family_ports_dir(getattr(cfg, "running_tree", None)) / family
    for file in (sorted(folder.glob(HARD_FAULT_DUMP_LAYOUT)) if folder.is_dir() else ()):
        m = re.search(r"^#define\s+HARD_FAULT_DUMP_SIZE\s+(0[xX][0-9A-Fa-f]+|\d+)", file.read_text(
            encoding="utf-8", errors="replace"), re.M)
        if m:
            dump = (int(m.group(1), 0) + 3) & ~3
            total = dump + FAILED_START_RECORD_SIZE + BOOT_REQUEST_FROM_END
            return total, (f"hard fault dump {_hex(dump)} + failed-start record {_hex(FAILED_START_RECORD_SIZE)} + "
                           f"boot request {_hex(BOOT_REQUEST_FROM_END)}")
    return base, f"boot request {_hex(BOOT_REQUEST_FROM_END)}"


def _isr_stack(cfg: Config, rep: Reporter) -> tuple[int | None, int | None]:
    """SYS-132: memory.application.isr_stack_size / isr_stack_guard_size checked (None: left out, the family linker
    template keeps its default). Alignment and MPU granularity are errors here and again in the linker template
    (#error), so the project shows them with the YAML path before a build.

    PLAT-112 (safety review group A 2026-10-01):
    - S132-4 a: a value the linker template of the family does not read is an error (it would stay without effect,
      the image keeps the template's own stack, e.g. family stm32 without such a template)
    - S132-1 b: a value below the template default needs a proof note (isr_stack_evidence /
      isr_stack_guard_evidence) - without one an error in an ASIL project, a warning in a QM project, as for the
      BSW task stacks (PLAT-107 S3); with one the stack (or guard) must hold the proven bytes
    - S132-2 a: an ASIL project that sets a value needs a stackuse profile of its own, so that tools/stackuse can
      check the linked value for this image (the template default is backed by the profile of the demo image)
    """
    from .bswtasks import DEFAULT_ISR_STACK_GUARD_SIZE, DEFAULT_ISR_STACK_SIZE, asil_project
    application = cfg.project.memory.application
    path = ("memory", "application")
    size, guard = application.isr_stack_size, application.isr_stack_guard_size
    evidence = application.isr_stack_evidence
    guard_evidence = application.isr_stack_guard_evidence
    if size is None and guard is None and evidence is None and guard_evidence is None:
        return None, None               # the template default, backed by the stackuse profile of the demo image
    size_ok = guard_ok = True
    if size is not None and size % ISR_STACK_ALIGNMENT:
        rep.error("project", path + ("isr_stack_size",), f"interrupt stack {_hex(size)} must be a multiple of "
                  f"{ISR_STACK_ALIGNMENT} (stack alignment of the procedure call standard)")
        size_ok = False
    if guard is not None and (guard % ISR_STACK_GUARD_GRANULARITY or guard < ISR_STACK_GUARD_GRANULARITY):
        rep.error("project", path + ("isr_stack_guard_size",), f"interrupt stack guard {_hex(guard)} must be a "
                  f"multiple of the MPU granularity of {ISR_STACK_GUARD_GRANULARITY} bytes")
        guard_ok = False
    ok = size_ok and guard_ok

    # S132-4 a: only a family whose application linker template reads the value
    family = cfg.derivative.derivative.family
    read = family_linker_macros(cfg) if size is not None or guard is not None else frozenset()
    unread = [(key, macro) for key, value, macro in (("isr_stack_size", size, ISR_STACK_MACRO),
                                                     ("isr_stack_guard_size", guard, ISR_STACK_GUARD_MACRO))
              if value is not None and macro not in read]
    for key, macro in unread:
        rep.error("project", path + (key,), f"not supported by the family {family}: its application linker template "
                  f"(platforms/{family}/{LINKER_TEMPLATE_GLOB}) does not read {macro}, the value would stay without "
                  "effect - leave it out (the family keeps the stack of its own linker script)")
    if unread:
        return None, None

    # a partial configuration (the memory overview of the Studio: no project section, no project file) does not
    # know the integrity: findings stay warnings there, the full check (nxconfig, live check) gives the errors
    whole = getattr(cfg.project, "project", None) is not None and getattr(cfg, "project_file", None) is not None
    asil = asil_project(cfg) if whole else False
    severity = rep.error if asil else rep.warning
    grade = "an ASIL project" if asil else "a QM project (project.integrity qm: only a warning)"
    if size is not None and size_ok and size < DEFAULT_ISR_STACK_SIZE and evidence is None:
        severity("project", path + ("isr_stack_size",), f"interrupt stack {_hex(size)} is below the default "
                 f"{_hex(DEFAULT_ISR_STACK_SIZE)} of the linker template without a proof note in {grade}: prove it "
                 "with tools/stackuse --strict for this image (start-up plus every interrupt level nested) or an "
                 "inject mspuse at the board and note it in memory.application.isr_stack_evidence, or keep the "
                 "default")
        ok = ok and not asil
    if guard is not None and guard_ok and guard < DEFAULT_ISR_STACK_GUARD_SIZE and guard_evidence is None:
        severity("project", path + ("isr_stack_guard_size",), f"interrupt stack guard {_hex(guard)} is below the "
                 f"default {_hex(DEFAULT_ISR_STACK_GUARD_SIZE)} of the linker template without a proof note in "
                 f"{grade}: it must hold the largest frame on the interrupt stack plus the larger of that frame and "
                 "an exception frame, or an overflow can jump over it - prove it with tools/stackuse --strict for "
                 "this image and note it in memory.application.isr_stack_guard_evidence, or keep the default")
        ok = ok and not asil
    if guard_evidence is not None and guard_evidence.method != "stackuse":
        rep.error("project", path + ("isr_stack_guard_evidence", "method"), f"the guard is proven only by "
                  f"tools/stackuse (method stackuse), not by {guard_evidence.method}: a measurement shows how deep "
                  "the stack went, not the largest frame that can jump over the guard")
        ok = False
    for key, value, default, proof, what in (
            ("isr_stack_evidence", size, DEFAULT_ISR_STACK_SIZE, evidence, "interrupt stack"),
            ("isr_stack_guard_evidence", guard, DEFAULT_ISR_STACK_GUARD_SIZE, guard_evidence,
             "interrupt stack guard")):
        effective = value if value is not None else default
        if proof is not None and proof.used_bytes > effective:
            origin = "project value" if value is not None else "default of the linker template"
            rep.error("project", path + (key, "used_bytes"), f"{what} {_hex(effective)} ({origin}) is smaller than "
                      f"the {proof.used_bytes} B its proof requires ({proof.method}: {proof.reference}): raise "
                      f"memory.application.{key.replace('_evidence', '_size')}")
            ok = False

    # S132-2 a: a project value of an ASIL project is checked by tools/stackuse against its own image
    if whole and asil and ok and (size is not None or guard is not None) and not stackuse_profiles(cfg):
        key = "isr_stack_size" if size is not None else "isr_stack_guard_size"
        rep.error("project", path + (key,), "an ASIL project that sets the interrupt stack needs a stackuse profile "
                  "of its own, so that tools/stackuse --strict checks the linked value for this image: add "
                  f"{' or '.join(_shown(p, cfg) for p in stackuse_profile_candidates(cfg))}, or keep the default "
                  "of the linker template")
        ok = False
    return (size, guard) if ok else (None, None)


# PLAT-112 (S132-4 a): the macros of the generated memoryLayout.h a family linker template has to read for the
# project value to take effect, and where the application linker templates of a family are (platform rule: per
# family, OpenBSW layout; today platforms/s32k1xx/port/main/linkerscript/application.dld.in)
ISR_STACK_MACRO = "NX_MEMORY_APP_ISR_STACK_SIZE"
ISR_STACK_GUARD_MACRO = "NX_MEMORY_APP_ISR_STACK_GUARD_SIZE"
LINKER_TEMPLATE_GLOB = "port/**/linkerscript/*"


def family_linker_macros(cfg: Config) -> frozenset[str]:
    """Interrupt stack macros the application linker templates of the project's family read (empty: none, or the
    family has no such template). Read from the tree the tools run as (TOOL-85)."""
    from .config import family_ports_dir
    folder = family_ports_dir(getattr(cfg, "running_tree", None)) / cfg.derivative.derivative.family
    found: set[str] = set()
    for file in (sorted(folder.glob(LINKER_TEMPLATE_GLOB)) if folder.is_dir() else ()):
        if file.is_file():
            text = file.read_text(encoding="utf-8", errors="replace")
            found.update(m for m in (ISR_STACK_MACRO, ISR_STACK_GUARD_MACRO) if m in text)
    return frozenset(found)


# PLAT-112 (S132-2 a): stackuse profile of a project - in the project folder, or among the profiles of the tree the
# tools run as, named after the project (like tools/stackuse/profiles/evalboard_s32k144_demo.yaml)
STACKUSE_PROFILE_IN_PROJECT = "stackuse.yaml"
STACKUSE_PROFILES = ("tools", "stackuse", "profiles")


def stackuse_profile_candidates(cfg: Config) -> tuple[Path, ...]:
    """Where a stackuse profile of the project is looked for, in this order."""
    return (cfg.project_file.parent / STACKUSE_PROFILE_IN_PROJECT,
            Path(cfg.running_tree).joinpath(*STACKUSE_PROFILES, f"{cfg.project.project.name}.yaml"))


def stackuse_profiles(cfg: Config) -> tuple[Path, ...]:
    """The stackuse profiles of the project that exist (empty: none)."""
    return tuple(p for p in stackuse_profile_candidates(cfg) if p.is_file())


def _shown(path: Path, cfg: Config) -> str:
    """A candidate path for a message: relative to the tree the tools run as, else as it is."""
    try:
        return Path(path).resolve().relative_to(Path(cfg.running_tree).resolve()).as_posix()
    except ValueError:
        return Path(path).as_posix()


def _calibration(cfg: Config, rep: Reporter, flash, app_start: int, app_end: int,
                 app_sector: int) -> CalibrationLayout | None:
    """SYS-89: the calibration segment of memory.calibration, placed and checked against the derivative.

    Code flash (the region of memory.flash): directly behind the first sector of the application. That sector keeps
    the vector table, the flash configuration field and the start-up code where the bootloader expects them; the
    segment follows sector aligned, so it can be erased on its own (BL-31), and the application code continues
    behind it. Data flash: from the start of the region, inside the part the partition leaves as data flash
    (nvm.partition.data_flash_bytes); refused until the bootloader can write it (BL-31, NVM-12).
    """
    use = cfg.project.memory.calibration
    where = ("memory", "calibration")
    errors = len(rep.messages)
    name = cfg.derivative.derivative.name
    region = next((r for r in cfg.derivative.memory if r.name == use.region), None)
    if region is None or region.kind not in ("code_flash", "data_flash"):
        options = [r.name for r in cfg.derivative.memory if r.name == flash.name or r.kind == "data_flash"]
        rep.error("project", where + ("region",), f"{name} has no flash region {use.region} for the calibration "
                  f"segment (possible: {', '.join(options) or '-'})")
        return None
    if region.kind == "code_flash" and region.name != flash.name:
        rep.error("project", where + ("region",), f"calibration segment in {region.name}: a code flash region must "
                  f"be the one of the application (memory.flash: {flash.name})")
        return None
    if use.on_invalid == "defaults" and use.profile == "production":
        rep.error("project", where + ("on_invalid",), "on_invalid defaults needs the RAM working page of the "
                  "development profile: in the production profile the application reads the values directly from "
                  "the segment and has no second copy to fall back to (use refuse or warn)")
    sizes = sorted({size for _, size in region.sector_table()})
    if len(sizes) != 1:
        rep.error("derivative", ("memory",), f"{region.name} of {name} has no uniform sector size, the calibration "
                  "segment needs one")
        return None
    sector = sizes[0]
    if use.size % sector:
        rep.error("project", where + ("size",), f"calibration segment {_hex(use.size)} is no multiple of the "
                  f"sector size {_hex(sector)} of {region.name} (whole sectors, so that it can be erased on its own)")
    elif use.size <= CAL_HEADER_SIZE:
        rep.error("project", where + ("size",), f"calibration segment {_hex(use.size)} leaves no room behind its "
                  f"{CAL_HEADER_SIZE}-byte header")
    if region.kind == "code_flash":
        start = app_start + app_sector
        if start + use.size >= app_end:
            rep.error("project", where + ("size",), f"calibration segment {_hex(start)}.."
                      f"{_hex(start + use.size - 1)} leaves no application code in {region.name} (application "
                      f"area {_hex(app_start)}..{_hex(app_end - 1)}, the segment follows its first sector)")
    else:
        start = region.start
        partition = cfg.project.nvm.partition if cfg.project.nvm is not None else None
        if partition is None:
            rep.error("project", where + ("region",), f"calibration segment in the data flash {region.name}: "
                      "nvm.partition must say how much of it stays data flash (the rest backs up the emulated "
                      "EEPROM)")
        elif use.size > partition.data_flash_bytes:
            rep.error("project", where + ("size",), f"calibration segment {_hex(use.size)} does not fit into the "
                      f"{_hex(partition.data_flash_bytes)} bytes of data flash of nvm.partition")
        if not any(m.severity == "error" for m in rep.messages[errors:]):
            rep.error("project", where + ("region",), f"calibration segment in the data flash {region.name}: the "
                      "bootloader and canflash cannot write the data flash yet (BL-31, NVM-12), so an application "
                      f"with it could not be downloaded; use {flash.name} until then")
    if any(m.severity == "error" for m in rep.messages[errors:]):
        return None
    return CalibrationLayout(region.name, start, use.size, sector, region.kind == "code_flash", use.on_invalid,
                             use.profile == "production")


def bootloader_plan(cfg: Config, rep: Reporter, can: str | None = None,
                    hooks_dir: Path | None = None) -> BootloaderPlan | None:
    """Bus and device hooks of the bootloader; `can` replaces bootloader.can (build option BOOTLOADER_CAN_BUS).

    TOOL-85: like the code constants of `checks.run`, the hooks are **C sources of the tree**, so they stay with
    the tree this package lies in until the Studio's fixtures are complete trees (TOOL-73).
    """
    hooks_dir = device_hooks() if hooks_dir is None else hooks_dir
    use = cfg.project.bootloader
    if can is not None:
        use = use.model_copy(update={"can": can})
    buses = {b.name: b for b in cfg.board.can}
    errors = len(rep.messages)
    bus = buses.get(use.can)
    if bus is None:
        rep.error("project", ("bootloader", "can"), f"board {cfg.board.board.name} has no CAN bus {use.can} "
                  f"(available: {', '.join(buses) or '-'})")
    elif use.can not in cfg.project.can:
        rep.error("project", ("bootloader", "can"), f"CAN {use.can} of the bootloader needs its bit rate: "
                  f"can.{use.can}.bitrate is missing")

    used = {dev.name: dev for dev in cfg.integration.devices}
    with_hook = {n: dev for n, dev in used.items() if (hooks_dir / dev.module).is_dir()}
    if use.init is None:
        init = []
        if bus is not None and bus.phy is not None:
            phy = next((dev for dev in cfg.board.devices if dev.name == bus.phy), None)
            if bus.phy in with_hook:
                init = [bus.phy]
            elif bus.phy not in used and phy is not None and any(
                    (hooks_dir / m).is_dir() for m in {phy.module} | _modules_of_part(cfg, phy.part) if m):
                rep.error("project", ("bootloader", "can"), f"transceiver {bus.phy} of {use.can} needs its bootloader "
                          f"hook, but the project does not use device {bus.phy} (use its buses)")
    else:
        init = []
        for i, dev_name in enumerate(use.init):
            if dev_name not in with_hook:
                what = (f"device {dev_name} ({used[dev_name].module}) has no bootloader hook "
                        f"({hooks_dir.name}/{used[dev_name].module})") if dev_name in used else \
                    f"device {dev_name} is not used by the project"
                rep.error("project", ("bootloader", "init", i), what + f" (devices with a hook: "
                          f"{', '.join(with_hook) or '-'})")
            elif dev_name in init:
                rep.warning("project", ("bootloader", "init", i), f"{dev_name} listed twice")
            else:
                init.append(dev_name)
    serving = _watchdog_serving(use.sbc_watchdog, with_hook, init, rep)
    if any(m.severity == "error" for m in rep.messages[errors:]):
        return None
    hooks = tuple(DeviceHook(dev.name, dev.module, dev.name in init, dev.name in serving)
                  for dev in with_hook.values())
    return BootloaderPlan(use.can, hooks, use.sbc_watchdog)


def _watchdog_serving(mode: str, with_hook: dict, init: list[str], rep: Reporter) -> set[str]:
    """BL-28: devices whose watchdog the bootloader serves. `serve` needs a device switched on by the bootloader
    (bootloader.init or the transceiver default) whose hook can serve a watchdog; `leave` serves none."""
    if mode != "serve":
        return set()
    capable = [n for n, dev in with_hook.items() if dev.module in WATCHDOG_SERVING_HOOKS]
    serving = {n for n in capable if n in init}
    if not capable:
        rep.error("project", ("bootloader", "sbc_watchdog"), "serve needs a used device whose bootloader hook serves "
                  f"an SBC watchdog (modules: {', '.join(sorted(WATCHDOG_SERVING_HOOKS))}); a board without such an "
                  "SBC keeps leave")
    elif not serving:
        rep.error("project", ("bootloader", "sbc_watchdog"), f"serve needs the SBC switched on by the bootloader: "
                  f"{', '.join(capable)} is only read (add it to bootloader.init)")
    elif len(serving) > 1:
        rep.error("project", ("bootloader", "sbc_watchdog"), "serve with more than one SBC switched on by the "
                  f"bootloader ({', '.join(sorted(serving))}) is not supported")
    return serving


def _modules_of_part(cfg: Config, part: str) -> set[str]:
    from fnmatch import fnmatchcase
    return {n for n, m in cfg.modules.manifests.items() if any(fnmatchcase(part, p) for p in m.parts)}


def check(cfg: Config, rep: Reporter, hooks_dir: Path | None = None):
    """(MemoryLayout, BootloaderPlan) of a project, each None if the project has no such section or after errors."""
    if cfg.project.memory is None:
        if cfg.project.bootloader is not None:
            rep.error("project", ("bootloader",), "the bootloader needs the memory partitioning (memory)")
        return None, None
    layout = _layout(cfg, rep)
    plan = bootloader_plan(cfg, rep, None, hooks_dir) if cfg.project.bootloader is not None else None
    return layout, plan
