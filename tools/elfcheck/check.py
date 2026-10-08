"""Linked images against the derivative map and the core (PLAT-80, tool-classification.md proposal D, finding TF6).

One generated memory layout feeds the bootloader, the application linker script, the updater and canflash: the
consumers agree by construction, so a layout that is wrong for the derivative is wrong everywhere at once and every
consumer test passes. This check takes its expectation from elsewhere:

- the derivative database (`mcudb/<family>/<derivative>.yaml`): code flash, RAM regions, sector geometry, program
  size, core and FPU;
- the project file for the few values the user sets (which flash, bootloader size and RAM, application RAM regions,
  board name) - read directly with PyYAML, not through tools/nxconfig;
- the linked images themselves: sections, program headers, symbols, build attributes, and the ten layout values the
  bootloader was compiled with (`layoutValues` of bootloader/src/core/app_image.c, read out of the image, i.e. the
  thing under test, not the generated header).

It imports nothing from tools/nxconfig, tools/bswgen or tools/canflash; the ELF and hex readers are its own
(elffile.py, ihex.py). Core logic without Qt (Studio requirement); every message names the file and the section,
symbol or record it is about.
"""

from __future__ import annotations

import re
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import yaml

try:                                    # package import (tests, Studio) or script import (python tools/elfcheck)
    from . import cores, elffile, ihex
except ImportError:                     # pragma: no cover - run as a script
    import cores  # type: ignore[no-redef]
    import elffile  # type: ignore[no-redef]
    import ihex  # type: ignore[no-redef]

REPO = Path(__file__).resolve().parents[2]
MCUDB = REPO / "mcudb"

# Conventions of the bootloader core (bootloader/src/core/app_image.h, bl_interface.h, doc/README.md section 2),
# written here once more on purpose: the check must not read them from the code it checks.
LAYOUT_SYMBOL = "layoutValues"          # ten uint32: flash start, size, sector, phrase, app start, app end,
LAYOUT_WORDS = 10                       # record address, RAM start, RAM end, boot request address
RECORD_MAGIC = b"APPV"
COMPAT_MAGIC = b"APPC"
COMPAT_VERSION = 1
COMPAT_SIZE = 32
RECORD_V1_SIZE = 24
BOOT_REQUEST_SIZE = 8                   # magic and its inversion
BLOB_MAGIC = b"BLUP"                    # updater: header (magic, size, CRC, header CRC) + bootloader bytes
BLOB_SYMBOL = "upd_blob"
# ROM check list of SM-14 (platforms/s32k1xx/port/main/linkerscript/application.dld.in)
ROM_LIST_START, ROM_LIST_END, ROM_RESULT = "__checksum_list_start", "__checksum_list_end", "__checksum_result"
# SYS-91: the area with its own reference (calibration segment), outside the list
ROM_OWN_START, ROM_OWN_END = "__rom_check_own_area_start", "__rom_check_own_area_end"
NOINIT_MARK = "noinit"                  # sections a startup code does not clear
# DOC-28 (NXCPP-05, DE5): symbols of a heap allocator no linked image may define - newlib (nano) and libnosys, and
# every operator new / new[] overload (Itanium names _Znw* / _Zna*, size_t j on 32 bit, m on 64 bit). Placement new
# (_ZnwjPv) allocates nothing and stays allowed. operator delete itself is allowed: the platform defines it
# (platforms/s32k1xx/port/main/src/cxxRuntime/NoHeapOperatorDelete.cpp), libstdc++'s version shows up as `free`.
HEAP_SYMBOLS = frozenset((
    "malloc", "_malloc_r", "calloc", "_calloc_r", "realloc", "_realloc_r", "reallocf", "_reallocf_r",
    "free", "_free_r", "memalign", "_memalign_r", "aligned_alloc", "posix_memalign", "valloc", "pvalloc",
    "_sbrk", "_sbrk_r", "sbrk",
))
HEAP_NEW = re.compile(r"_Zn[wa][jm]")
HEAP_PLACEMENT_NEW = re.compile(r"_Zn[wa][jm]Pv$")
STT_FUNC = 2                            # ELF symbol type of a function (Thumb bit in its value)
# SYS-170 (SYS69-S1, finding SFP-F01): the interrupt frame of the safety shell. In an application that has it
# (SafePreIsrHook defined), every vector from SysTick (15) on must point at a wrapper whose first call is
# SafePreIsrHook (closes the MPU gate of the protected RAM) and that calls SafePostIsrHook, or at DefaultISR
# (reserved numbers, numbers behind the derivative's last vector). Vectors 1..14 (reset, NMI, faults, SVC, PendSV)
# are not interrupt frames that return to the safety cycle with the gate restored and are not checked here.
ISR_PRE_HOOK = "SafePreIsrHook"
ISR_POST_HOOK = "SafePostIsrHook"
ISR_FIRST_CHECKED = 15
ISR_DEFAULT = "DefaultISR"
# The vectors of the safety shell that are no hook wrapper on purpose (IO-32): the naked watchdog path into
# customHardFaultHandler, which switches the outputs off first and never returns, and the halt deadman of the debug
# halt guard (outputs off before any call; its vector name is an alias of the handler).
ISR_UNWRAPPED_SAFETY = frozenset(("Safe_WDOG_EWM_IRQHandler", "Safe_LPIT0_Ch1_IRQHandler"))
# SYS-170 (review point SYS170-S4, option a): no floating-point instruction in anything a wrapper reaches. On a core
# with FPU the exception entry only reserves the FP part of the frame on the interrupted task's stack (lazy
# stacking); the first FP instruction of the handler writes it there. If that task is the safety task, the pre hook
# has just made its stack read-only - the deferred write would be a bus fault. VFP/Advanced SIMD in Thumb-2:
# first halfword 1110 11xx xxxx xxxx, coprocessor field (second halfword, bits 11..8) 1010 or 1011.
ISR_FPU_LIMIT = 4096                    # functions followed at most (an image is far below)
# Not followed: the end of an unexpected interrupt. DefaultISR (OpenBSW vIllegalISR: a debug_printf line, then an
# endless loop) never returns to the interrupted code; a bus fault from a lazy FP save there only turns the hang until
# the watchdog into the fault reaction (outputs off, dump, reset).
ISR_FPU_STOP = frozenset(("DefaultISR",))


@dataclass(frozen=True)
class ConfigField:
    """NVM-15: flash configuration field a family reads out of reset from the first code flash sector. Erased or
    secured, the chip comes up secured after the next reset; on a part whose security module took a key space
    (s32k1xx CSEc after PGMPART with key size != 0) mass erase is locked as well, and the part is lost. Written
    here once more on purpose (bootloader/platforms/s32k1xx/startup.S, upd_family.h and tools/canflash hold it too):
    the check must not read it from the code it checks."""

    family: str
    offset: int                         # from the start of the code flash
    reference: bytes                    # the only field the updater of the family accepts


# s32k1xx: 0x400..0x40F = backdoor key (8), FPROT3..0, FSEC, FOPT, FEPROT, FDPROT. FSEC 0xFE: SEC 10 = unsecured,
# FSLACC 11, MEEN 11 = mass erase enabled, KEYEN 11 = backdoor key disabled; FOPT 0x7F; nothing protected.
CONFIG_FIELDS = {
    "s32k1xx": ConfigField("s32k1xx", 0x400, bytes([0xFF] * 12 + [0xFE, 0x7F, 0xFF, 0xFF])),
}


# ---------------------------------------------------------------------- findings

@dataclass(frozen=True)
class Finding:
    level: str                          # "error" or "warning"
    code: str                           # stable identifier for tests and filters
    file: str                           # the image, record or derivative file concerned
    where: str                          # "section .text", "symbol layoutValues", "record", "attributes", ...
    message: str

    def __str__(self) -> str:
        return f"{self.level}: {self.file}: {self.where}: {self.message} [{self.code}]"


class Findings(list):
    def error(self, code: str, file, where: str, message: str) -> None:
        self.append(Finding("error", code, str(file), where, message))

    def warning(self, code: str, file, where: str, message: str) -> None:
        self.append(Finding("warning", code, str(file), where, message))

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self if f.level == "error"]


def hx(value: int) -> str:
    return f"0x{value:08X}"


def rng(start: int, end: int) -> str:
    return f"[{hx(start)}, {hx(end)})"


# ---------------------------------------------------------------------- expectation

@dataclass(frozen=True)
class Region:
    name: str
    kind: str
    start: int
    size: int
    sector_size: int | None = None
    sectors: tuple[tuple[int, int], ...] = ()       # (size, count) from the region start
    program_size: int | None = None

    @property
    def end(self) -> int:
        return self.start + self.size

    def contains(self, start: int, end: int) -> bool:
        return self.start <= start and end <= self.end

    def boundaries(self) -> list[int]:
        """Sector boundaries from the region start to its end (inclusive)."""
        if self.sector_size:
            return list(range(self.start, self.end + 1, self.sector_size))
        result, address = [self.start], self.start
        for size, count in self.sectors:
            for _ in range(count):
                address += size
                result.append(address)
        return result

    def uniform_sector_size(self) -> int:
        if self.sector_size:
            return self.sector_size
        sizes = {size for size, _ in self.sectors}
        return sizes.pop() if len(sizes) == 1 else 0


@dataclass
class Expectation:
    derivative: str
    derivative_file: Path
    core: str
    fpu: str | None
    regions: list[Region]
    flash: Region
    board: str | None = None
    project_file: Path | None = None
    bootloader_size: int | None = None
    bootloader_ram: tuple[str, int, int] | None = None          # (region name, start, end)
    app_ram: list[tuple[str, int, int]] = field(default_factory=list)
    family: str | None = None                                   # derivative.family (NVM-15: configuration field)

    def region_of(self, start: int, end: int) -> Region | None:
        return next((r for r in self.regions if r.contains(start, end)), None)

    def ram_spans(self) -> list[tuple[int, int]]:
        """RAM regions of the derivative, adjacent ones merged (S32K1: SRAM_L and SRAM_U are one block)."""
        spans: list[list[int]] = []
        for r in sorted((r for r in self.regions if r.kind == "ram"), key=lambda r: r.start):
            if spans and spans[-1][1] == r.start:
                spans[-1][1] = r.end
            else:
                spans.append([r.start, r.end])
        return [(a, b) for a, b in spans]

    def in_ram(self, start: int, end: int) -> bool:
        return any(a <= start and end <= b for a, b in self.ram_spans())

    @property
    def target(self) -> str | None:
        return f"{self.board}/{self.derivative}" if self.board else None


def _int(value, what: str, file: Path) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{file}: {what}: integer expected, got {value!r}")
    return value


def load_derivative(path: Path) -> tuple[dict, list[Region]]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    derivative = (data or {}).get("derivative") or {}
    regions = []
    for i, entry in enumerate((data or {}).get("memory") or []):
        where = f"memory[{i}]"
        sectors = tuple((_int(s["size"], f"{where}.sectors.size", path), _int(s["count"], f"{where}.sectors.count", path))
                        for s in entry.get("sectors") or [])
        regions.append(Region(str(entry["name"]), str(entry["kind"]), _int(entry["start"], f"{where}.start", path),
                              _int(entry["size"], f"{where}.size", path), entry.get("sector_size"), sectors,
                              entry.get("program_size")))
    if not derivative.get("name") or not derivative.get("core"):
        raise ValueError(f"{path}: derivative.name and derivative.core are needed")
    return derivative, regions


def workspace_mcudb(project: Path) -> tuple[Path, ...]:
    """search.mcudb of the nextbsw.yaml above a project (ARCH-56), searched before the tree's mcudb - the same
    walk-up as tools/nxconfig/workspace.find_pin_file (stops at a neXtBSW tree or a git root). A derivative of a
    customer workspace (target-new-board: NBMCU) is found there, not in the tree."""
    folder = Path(project).resolve().parent
    for candidate in (folder, *folder.parents):
        pin = candidate / "nextbsw.yaml"
        if pin.is_file():
            data = yaml.safe_load(pin.read_text(encoding="utf-8")) or {}
            entries = ((data.get("search") or {}).get("mcudb")) or []
            return tuple((candidate / str(e)).resolve() for e in entries)
        if (candidate / "VERSION").is_file() and (candidate / "tools" / "nxconfig").is_dir():
            return ()
        if (candidate / ".git").exists():
            return ()
    return ()


def find_derivative_file(name: str, mcudb: Path = MCUDB, first: tuple[Path, ...] = ()) -> Path:
    """The derivative file mcudb/<family>/<name>.yaml - in the workspace's search paths `first`, else in `mcudb`."""
    for base in first:
        found = sorted(Path(base).glob(f"*/{name}.yaml"))
        if found:
            return found[0]
    found = sorted(Path(mcudb).glob(f"*/{name}.yaml"))
    if len(found) != 1:
        raise ValueError(f"derivative {name}: {len(found)} files mcudb/*/{name}.yaml")
    return found[0]


def load_expectation(project: Path | None = None, derivative_file: Path | None = None, flash: str | None = None,
                     board: str | None = None, mcudb: Path = MCUDB) -> Expectation:
    """Expectation from the derivative database plus the user values of the project (if a project is given)."""
    proj: dict = {}
    if project is not None:
        project = Path(project)
        proj = yaml.safe_load(project.read_text(encoding="utf-8")) or {}
        board_ref = (proj.get("project") or {}).get("board")
        if board_ref:
            board_file = (project.parent / board_ref / "board.yaml").resolve()
            board_data = (yaml.safe_load(board_file.read_text(encoding="utf-8")) or {}).get("board") or {}
            board = board or board_data.get("name")
            if derivative_file is None and board_data.get("mcu"):
                derivative_file = find_derivative_file(str(board_data["mcu"]), mcudb,
                                                       workspace_mcudb(project))
    if derivative_file is None:
        raise ValueError("no derivative: give a project with a board or the derivative file of mcudb")
    derivative, regions = load_derivative(Path(derivative_file))
    memory = proj.get("memory") or {}
    flash = flash or memory.get("flash")
    code_flashes = [r for r in regions if r.kind == "code_flash"]
    if flash is None:
        if len(code_flashes) != 1:
            raise ValueError(f"{derivative_file}: {len(code_flashes)} code flash regions, name the one used")
        flash_region = code_flashes[0]
    else:
        flash_region = next((r for r in code_flashes if r.name == flash), None)
        if flash_region is None:
            raise ValueError(f"{derivative_file}: no code flash region '{flash}'")
    exp = Expectation(str(derivative["name"]), Path(derivative_file), str(derivative["core"]), derivative.get("fpu"),
                      regions, flash_region, board, project, family=derivative.get("family"))
    by_name = {r.name: r for r in regions}

    def region(name: str, where: str) -> Region:
        if name not in by_name:
            raise ValueError(f"{project}: {where}: no region '{name}' in {derivative_file}")
        return by_name[name]

    bl = memory.get("bootloader") or {}
    if "size" in bl:
        exp.bootloader_size = _int(bl["size"], "memory.bootloader.size", project)
    if bl.get("ram"):
        r = region(bl["ram"], "memory.bootloader.ram")
        size = bl.get("ram_size", r.size)
        exp.bootloader_ram = (r.name, r.start, r.start + _int(size, "memory.bootloader.ram_size", project))
    app = memory.get("application") or {}
    for name in app.get("ram") or []:
        r = region(name, "memory.application.ram")
        exp.app_ram.append((r.name, r.start, r.end))
    return exp


# ---------------------------------------------------------------------- the layout inside the bootloader

@dataclass(frozen=True)
class Layout:
    flash_start: int
    flash_size: int
    sector_size: int
    phrase_size: int
    app_start: int
    app_end: int
    record_addr: int
    ram_start: int
    ram_end: int
    boot_request: int
    raw: bytes

    @property
    def layout_id(self) -> int:
        return zlib.crc32(self.raw)


def read_layout(img: elffile.ElfImage, out: Findings) -> Layout | None:
    sym = img.symbol(LAYOUT_SYMBOL)
    where = f"symbol {LAYOUT_SYMBOL}"
    if sym is None:
        out.error("layout-missing", img.path, where, "not in the image: the layout the bootloader was built with "
                  "cannot be read (bootloader/src/core/app_image.c)")
        return None
    raw = img.read(sym.value, 4 * LAYOUT_WORDS)
    if sym.size != 4 * LAYOUT_WORDS or raw is None:
        out.error("layout-missing", img.path, where, f"{sym.size} bytes at {hx(sym.value)}, expected "
                  f"{4 * LAYOUT_WORDS} bytes of flash content")
        return None
    return Layout(*struct.unpack("<10I", raw), raw=raw)


# ---------------------------------------------------------------------- single checks

def check_core(img: elffile.ElfImage, exp: Expectation, out: Findings) -> None:
    where = "section .ARM.attributes"
    want, reason = cores.expected(exp.core, exp.fpu)
    if want is None:
        out.error("core-unknown", exp.derivative_file, "derivative.core", reason)
        return
    if not img.attributes:
        out.error("attributes-missing", img.path, where, "no build attributes: core and FP ABI cannot be checked")
        return
    arch = img.attribute(elffile.TAG_CPU_ARCH)
    if arch not in want.cpu_arch:
        out.error("core-arch", img.path, where,
                  f"Tag_CPU_arch {cores.ARCH_NAMES.get(arch, arch)}, the derivative {exp.derivative} ({exp.core}) "
                  f"needs {'/'.join(cores.ARCH_NAMES[a] for a in sorted(want.cpu_arch))}")
    profile = img.attribute(elffile.TAG_CPU_ARCH_PROFILE)
    profile = chr(profile) if isinstance(profile, int) and profile else "none"
    if profile != want.profile:
        out.error("core-profile", img.path, where,
                  f"Tag_CPU_arch_profile '{profile}', {exp.core} is profile '{want.profile}'")
    fp_arch = img.attribute(elffile.TAG_FP_ARCH) or 0
    if fp_arch != want.fp_arch:
        out.error("fp-arch", img.path, where,
                  f"Tag_FP_arch {cores.FP_ARCH_NAMES.get(fp_arch, fp_arch)}, {exp.core} "
                  f"(FPU {exp.fpu or 'default'}) has {cores.FP_ARCH_NAMES.get(want.fp_arch, want.fp_arch)}")
    hardfp = img.attribute(elffile.TAG_ABI_HARDFP_USE) or 0
    if want.fp_arch and hardfp not in want.hardfp_use:
        out.error("fp-precision", img.path, where,
                  f"Tag_ABI_HardFP_use {hardfp}, the FPU of {exp.core} allows {sorted(want.hardfp_use)} "
                  "(1 = single precision only)")
    vfp_args = img.attribute(elffile.TAG_ABI_VFP_ARGS) or 0
    if vfp_args != want.vfp_args:
        abi = {0: "base AAPCS (soft/softfp)", 1: "VFP registers (hard)"}
        out.error("fp-abi", img.path, where,
                  f"Tag_ABI_VFP_args {abi.get(vfp_args, vfp_args)}, expected {abi[want.vfp_args]} for "
                  f"{exp.core}{' with FPU' if want.fp_arch else ' without FPU'}")


def _ranges_overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def content_ranges(img: elffile.ElfImage) -> list[tuple[int, int, str]]:
    """Load address ranges of the sections that put bytes into flash, in address order."""
    return sorted((img.lma(s), img.lma(s) + s.size, s.name) for s in img.sections if s.has_content)


def check_sections(img: elffile.ElfImage, exp: Expectation, out: Findings, area: tuple[int, int] | None,
                   area_name: str, ram: list[tuple[str, int, int]] | None) -> None:
    """Every allocated section inside the derivative's memory; load addresses in the code flash and in the image's
    area; run-time RAM inside the RAM the project gives this image; no two sections on the same bytes."""
    flash = exp.flash
    run_ranges: list[tuple[int, int, str]] = []
    for sec in img.sections:
        if not sec.alloc or sec.size == 0:
            continue
        where = f"section {sec.name}"
        start, end = sec.addr, sec.addr + sec.size
        region = exp.region_of(start, end)
        if region is None:
            out.error("section-outside-memory", img.path, where,
                      f"run address {rng(start, end)} lies in no memory region of {exp.derivative}")
        elif region.kind not in ("code_flash", "ram"):
            out.error("section-region-kind", img.path, where,
                      f"run address {rng(start, end)} in {region.name} ({region.kind}): code, data and stacks "
                      "belong into code flash or RAM")
        if region is not None and region.kind == "ram":
            run_ranges.append((start, end, sec.name))
            if ram is not None and not any(a <= start and end <= b for _, a, b in ram):
                names = ", ".join(n for n, _, _ in ram)
                out.error("section-outside-ram", img.path, where,
                          f"{rng(start, end)} outside the RAM the project gives this image ({names})")
        if sec.has_content:
            lma = img.lma(sec)
            lma_end = lma + sec.size
            if not flash.contains(lma, lma_end):
                out.error("section-outside-flash", img.path, where,
                          f"load address {rng(lma, lma_end)} outside the code flash {flash.name} "
                          f"{rng(flash.start, flash.end)} of {exp.derivative}")
            elif area is not None and not (area[0] <= lma and lma_end <= area[1]):
                out.error("section-outside-area", img.path, where,
                          f"load address {rng(lma, lma_end)} outside the {area_name} {rng(*area)}")
    content = content_ranges(img)
    for ranges, what in ((content, "load"), (sorted(run_ranges), "RAM")):
        for (a0, a1, an), (b0, b1, bn) in zip(ranges, ranges[1:]):
            if b0 < a1:
                out.error("overlap", img.path, f"section {bn}",
                          f"{what} addresses {rng(b0, b1)} overlap section {an} {rng(a0, a1)}")
    check_segments(img, out, area, area_name)


def check_segments(img: elffile.ElfImage, out: Findings, area: tuple[int, int] | None, area_name: str) -> None:
    """A PT_LOAD segment that carries file bytes of no section (typically the ELF header in front of the vector
    table) is harmless for objcopy and the hex file, but a loader that follows the program headers would write
    those bytes too - outside the image's area that is someone else's flash."""
    if area is None:
        return
    for seg in img.segments:
        if seg.type != elffile.PT_LOAD or not seg.filesz:
            continue
        covered = sorted((s.offset, s.offset + s.size) for s in img.sections
                         if s.has_content and img.segment_of(s) == seg)
        first = covered[0][0] if covered else seg.offset + seg.filesz
        extra = first - seg.offset
        if extra > 0 and seg.paddr < area[0]:
            out.warning("segment-extra-bytes", img.path, f"program header {seg.index}",
                        f"PT_LOAD loads {extra:#x} bytes of no section (ELF header, padding) at {hx(seg.paddr)}, "
                        f"outside the {area_name} {rng(*area)}: flash the hex file, not the ELF")


def check_vectors(img: elffile.ElfImage, exp: Expectation, out: Findings, start: int,
                  ram: list[tuple[int, int]], what: str) -> None:
    """Cortex-M: vector table at the start of the image, aligned for VTOR, stack pointer in RAM, reset Thumb."""
    want, _ = cores.expected(exp.core, exp.fpu)
    if want is None or want.profile != "M":
        return
    content = content_ranges(img)
    if not content:
        out.error("image-empty", img.path, "sections", f"{what}: no section with flash content")
        return
    first = content[0]
    where = f"section {first[2]}"
    if first[0] != start:
        out.error("vector-table", img.path, where,
                  f"{what} starts at {hx(first[0])}, the vector table belongs to the start of its area {hx(start)}")
        return
    size = first[1] - first[0]
    align = max(128, 1 << (max(size, 1) - 1).bit_length())
    if start % align:
        out.error("vector-alignment", img.path, where,
                  f"vector table of {size:#x} bytes at {hx(start)}: VTOR needs an alignment of {align:#x}")
    flash = img.flash_bytes()
    words = bytes(flash.get(start + i, 0xFF) for i in range(8))
    sp, reset = struct.unpack("<II", words)
    if not any(a <= sp <= b and sp % 4 == 0 for a, b in ram):
        out.error("initial-sp", img.path, where,
                  f"initial stack pointer {hx(sp)} not inside the RAM of the {what} "
                  f"({', '.join(rng(a, b) for a, b in ram)}) or not word aligned")
    target = reset & ~1
    if not reset & 1 or not any(a <= target < b for a, b, _ in content):
        out.error("reset-vector", img.path, where,
                  f"reset vector {hx(reset)} is not a Thumb address inside the {what}")


def config_field_problems(field: ConfigField, raw: bytes | None) -> list[tuple[str, str]]:
    """(code, text) for every way `raw` (None: not in the image) differs from the reference of the family."""
    if raw is None:
        return [("fcf-missing", "no flash configuration field: the erased bytes read FSEC 0xFF and the chip comes up "
                 "secured after the next reset")]
    problems = []
    fsec, fopt = raw[12], raw[13]
    if fsec & 0x03 != 0x02:
        problems.append(("fcf-secured", f"FSEC 0x{fsec:02X}: SEC {fsec & 3:02b} secures the chip (only 10 is "
                         "unsecured)"))
    if (fsec >> 4) & 0x03 == 0x02:
        problems.append(("fcf-mass-erase", f"FSEC 0x{fsec:02X}: MEEN 10 disables mass erase"))
    if (fsec >> 6) & 0x03 == 0x02:
        problems.append(("fcf-backdoor", f"FSEC 0x{fsec:02X}: KEYEN 10 enables the backdoor key"))
    if raw[8:12] != b"\xff" * 4 or raw[14:16] != b"\xff" * 2:
        problems.append(("fcf-protection", f"FPROT {raw[8:12].hex()}, FEPROT 0x{raw[14]:02X}, FDPROT "
                         f"0x{raw[15]:02X}: protected flash (the updater could not rewrite the bootloader)"))
    if fopt != field.reference[13]:
        problems.append(("fcf-value", f"FOPT 0x{fopt:02X} instead of 0x{field.reference[13]:02X}"))
    if not problems and raw != field.reference:
        problems.append(("fcf-value", f"{raw.hex()} is not the reference {field.reference.hex()} (backdoor key or "
                         "FSEC bits the updater does not accept)"))
    return problems


def check_config_field(image: dict[int, int], exp: Expectation, out: Findings, file, what: str) -> None:
    """NVM-15: an image that puts bytes into the first sector of the code flash is the one that erases it when it is
    flashed (J-Link loadfile, the bootloader updater); it has to carry the complete flash configuration field of the
    family with the reference value, else the next reset leaves the chip secured."""
    field = CONFIG_FIELDS.get(exp.family or "")
    if field is None:
        return
    bounds = exp.flash.boundaries()
    first = (exp.flash.start, bounds[1] if len(bounds) > 1 else exp.flash.end)
    if not any(first[0] <= a < first[1] for a in image):
        return
    address = exp.flash.start + field.offset
    present = [image.get(address + i) for i in range(len(field.reference))]
    where = f"flash configuration field {rng(address, address + len(field.reference))}"
    if None in present and any(b is not None for b in present):
        out.error("fcf-missing", file, where, f"{what} writes sector 0 with only "
                  f"{sum(b is not None for b in present)} of {len(present)} bytes of the field: the rest stays "
                  "erased, FSEC may read 0xFF (secured) after the next reset")
        return
    raw = None if None in present else bytes(present)
    for code, text in config_field_problems(field, raw):
        out.error(code, file, where, f"{what} writes sector 0: {text}")


def check_layout(bl: elffile.ElfImage, layout: Layout, exp: Expectation, out: Findings) -> tuple[int, int]:
    """The ten layout values the bootloader was built with against the derivative. Returns the bootloader area."""
    where = f"symbol {LAYOUT_SYMBOL}"
    f = exp.flash
    if (layout.flash_start, layout.flash_size) != (f.start, f.size):
        out.error("layout-flash", bl.path, where,
                  f"flash {rng(layout.flash_start, layout.flash_start + layout.flash_size)}, {exp.derivative} "
                  f"{f.name} is {rng(f.start, f.end)}")
    uniform = f.uniform_sector_size()
    if layout.sector_size != uniform:
        out.error("layout-sector-size", bl.path, where,
                  f"sector size {layout.sector_size:#x}, {exp.derivative} {f.name} has "
                  f"{f'{uniform:#x}' if uniform else 'sectors of different sizes (value 0)'}")
    if f.program_size is not None and layout.phrase_size != f.program_size:
        out.error("layout-phrase", bl.path, where,
                  f"program unit {layout.phrase_size} bytes, {exp.derivative} {f.name} programs {f.program_size}")
    bounds = f.boundaries()
    for name, value in (("application start", layout.app_start), ("application end", layout.app_end),
                        ("validity record", layout.record_addr)):
        if value not in bounds:
            out.error("sector-alignment", bl.path, where,
                      f"{name} {hx(value)} is not on a sector boundary of {exp.derivative} {f.name}")
    if not (f.start < layout.app_start < layout.app_end <= f.end):
        out.error("layout-application", bl.path, where,
                  f"application area {rng(layout.app_start, layout.app_end)} not inside {f.name} {rng(f.start, f.end)}")
    rec = (layout.record_addr, _sector_end(bounds, layout.record_addr))
    if not f.contains(*rec):
        out.error("record-position", bl.path, where, f"validity record sector {rng(*rec)} outside {f.name}")
    if _ranges_overlap(rec, (layout.app_start, layout.app_end)):
        out.error("record-position", bl.path, where,
                  f"validity record sector {rng(*rec)} inside the application area "
                  f"{rng(layout.app_start, layout.app_end)}: an application erase would take it along")
    bl_end = f.start + exp.bootloader_size if exp.bootloader_size else min(layout.app_start, layout.record_addr)
    bl_area = (f.start, bl_end)
    if exp.bootloader_size and bl_end not in bounds:
        out.error("sector-alignment", exp.project_file, "memory.bootloader.size",
                  f"bootloader area {rng(*bl_area)} does not end on a sector boundary of {f.name}")
    if _ranges_overlap(rec, bl_area):
        out.error("record-position", bl.path, where,
                  f"validity record sector {rng(*rec)} inside the bootloader area {rng(*bl_area)}")
    if _ranges_overlap(bl_area, (layout.app_start, layout.app_end)):
        out.error("overlap", bl.path, where, f"application area {rng(layout.app_start, layout.app_end)} overlaps "
                  f"the bootloader area {rng(*bl_area)}")
    if not exp.in_ram(layout.ram_start, layout.ram_end) or layout.ram_start >= layout.ram_end:
        out.error("layout-ram", bl.path, where,
                  f"RAM {rng(layout.ram_start, layout.ram_end)} is not one block of RAM of {exp.derivative}")
    br = (layout.boot_request, layout.boot_request + BOOT_REQUEST_SIZE)
    if layout.boot_request % 4 or not (layout.ram_start <= br[0] and br[1] <= layout.ram_end):
        out.error("boot-request", bl.path, where,
                  f"boot request {rng(*br)} not word aligned inside the RAM {rng(layout.ram_start, layout.ram_end)}")
    if exp.app_ram and not any(a <= br[0] and br[1] <= b for _, a, b in exp.app_ram):
        out.error("boot-request", bl.path, where,
                  f"boot request {rng(*br)} outside the application RAM of the project "
                  f"({', '.join(n for n, _, _ in exp.app_ram)}): the application could not write it")
    return bl_area


def _sector_end(bounds: list[int], address: int) -> int:
    later = [b for b in bounds if b > address]
    return later[0] if later else address + 1


def check_boot_request_free(img: elffile.ElfImage, layout: Layout, out: Findings, what: str) -> None:
    """No section that a start-up code initialises may cover the boot request (it would be lost on the way)."""
    br = (layout.boot_request, layout.boot_request + BOOT_REQUEST_SIZE)
    for sec in img.sections:
        if sec.alloc and sec.size and NOINIT_MARK not in sec.name \
                and _ranges_overlap((sec.addr, sec.addr + sec.size), br):
            out.error("boot-request", img.path, f"section {sec.name}",
                      f"{rng(sec.addr, sec.addr + sec.size)} covers the boot request {rng(*br)}: the {what} "
                      "initialises it at start-up")


def check_cross_overlap(images: list[tuple[str, elffile.ElfImage]], out: Findings) -> None:
    """Images that are in flash at the same time must not share a byte (updater and application alternate)."""
    for i, (na, a) in enumerate(images):
        for nb, b in images[i + 1:]:
            if {na, nb} == {"application", "updater"}:
                continue
            for a0, a1, an in content_ranges(a):
                for b0, b1, bn in content_ranges(b):
                    if _ranges_overlap((a0, a1), (b0, b1)):
                        out.error("overlap", b.path, f"section {bn}",
                                  f"{nb} load addresses {rng(b0, b1)} overlap {na} section {an} {rng(a0, a1)} "
                                  f"({a.path.name})")


def heap_symbol(name: str) -> str | None:
    """DOC-28: what `name` is, if it is a part of a heap allocator (else None)."""
    if name in HEAP_SYMBOLS:
        return "C library allocator"
    if HEAP_NEW.match(name) and not HEAP_PLACEMENT_NEW.match(name):
        return "operator new"
    return None


def check_heap(img: elffile.ElfImage, out: Findings, what: str) -> None:
    """DOC-28 (dev-environment.md 4.6 P5 (b), rule NXCPP-05, finding DE5): no allocator in the linked image.

    The source scan only sees calls in our code; the image also shows what a library pulls in behind them (before
    DOC-28 the deleting destructors linked newlib's malloc/free/_sbrk through libstdc++'s operator delete). Defined
    symbols only - a linked image resolves every reference it keeps."""
    seen = set()
    for sym in img.symbols:
        if sym.shndx == 0 or sym.name in seen:
            continue
        kind = heap_symbol(sym.name)
        if kind is None:
            continue
        seen.add(sym.name)
        address = sym.value & ~1 if sym.type == STT_FUNC else sym.value     # without the Thumb bit
        out.error("heap-symbol", img.path, f"symbol {sym.name}",
                  f"{what} contains a heap allocator ({kind}, {sym.size} bytes at {hx(address)}): neXtBSW has no "
                  "dynamic memory (NXCPP-05); find the reference with the linker map (cross reference / 'archive "
                  "member included') - an own operator delete of the platform must be on the link line")


def thumb_calls(code: bytes, address: int) -> list[tuple[int, str]]:
    """Targets of the 32-bit BL and B.W (encoding T4) in a Thumb function body, in order, as (target, "bl"|"b.w").

    Walks the instruction stream from the start (16-bit and 32-bit encodings by their first halfword); enough for the
    small wrapper functions this is used for (no literal pool in front of the calls)."""
    calls: list[tuple[int, str]] = []
    pos = 0
    while pos + 2 <= len(code):
        h1 = struct.unpack_from("<H", code, pos)[0]
        if (h1 >> 11) not in (0x1D, 0x1E, 0x1F):
            pos += 2
            continue
        if pos + 4 > len(code):
            break
        h2 = struct.unpack_from("<H", code, pos + 2)[0]
        if (h1 >> 11) == 0x1E and (h2 & 0xC000) == 0xC000 and h2 & 0x1000:
            kind = "bl"
        elif (h1 >> 11) == 0x1E and (h2 & 0xD000) == 0x9000:
            kind = "b.w"
        else:
            kind = None
        if kind is not None:
            s = (h1 >> 10) & 1
            i1 = 1 ^ (((h2 >> 13) & 1) ^ s)
            i2 = 1 ^ (((h2 >> 11) & 1) ^ s)
            imm = (s << 24) | (i1 << 23) | (i2 << 22) | ((h1 & 0x3FF) << 12) | ((h2 & 0x7FF) << 1)
            if s:
                imm -= 1 << 25
            calls.append(((address + pos + 4 + imm) & 0xFFFFFFFF, kind))
        pos += 4
    return calls


def data_ranges(img: elffile.ElfImage) -> list[tuple[int, int]]:
    """Address ranges of data inside code ($d up to the next $t/$a mapping symbol or the end of its section)."""
    marks = sorted((s.value, s.name[:2]) for s in img.symbols
                   if s.shndx != 0 and s.name[:2] in ("$d", "$t", "$a") and (len(s.name) == 2 or s.name[2] == "."))
    ranges = []
    for i, (address, kind) in enumerate(marks):
        if kind != "$d":
            continue
        sec = next((s for s in img.sections if s.has_content and s.addr <= address < s.addr + s.size), None)
        end = sec.addr + sec.size if sec else address
        nxt = next((a for a, _ in marks[i + 1:] if a > address), end)
        ranges.append((address, min(nxt, end)))
    return ranges


def fpu_instructions(code: bytes, address: int, data: list[tuple[int, int]]) -> list[int]:
    """Addresses of VFP/Advanced SIMD instructions in a Thumb function body (data ranges skipped)."""
    found = []
    pos = 0
    while pos + 2 <= len(code):
        here = address + pos
        skip = next((b for a, b in data if a <= here < b), None)
        if skip is not None:
            pos = skip - address
            continue
        h1 = struct.unpack_from("<H", code, pos)[0]
        if (h1 >> 11) not in (0x1D, 0x1E, 0x1F):
            pos += 2
            continue
        if pos + 4 > len(code):
            break
        h2 = struct.unpack_from("<H", code, pos + 2)[0]
        if (h1 & 0xEC00) == 0xEC00 and (h1 >> 13) == 0x7 and (h2 & 0x0E00) == 0x0A00:
            found.append(here)
        pos += 4
    return found


def check_isr_fpu(img: elffile.ElfImage, out: Findings, what: str, roots: dict[str, str]) -> int:
    """SYS170-S4 a: no function reachable (BL/B.W) from a hook-wrapped handler holds a VFP instruction.

    `roots`: handler name -> the wrapper that calls it. Calls through a register (blx rN, function pointers) cannot
    be followed from the code alone; what is reached that way is not checked (stated in the rule)."""
    funcs = {}
    for s in img.symbols:
        if s.type == STT_FUNC and s.shndx != 0 and s.size:
            funcs.setdefault(s.value & ~1, (s.name, s.size))
    data = data_ranges(img)
    seen: dict[int, str] = {}
    queue = []
    for handler, wrapper in roots.items():
        sym = img.symbol(handler)
        if sym is not None:
            queue.append((sym.value & ~1, wrapper))
    reported = set()
    while queue and len(seen) < ISR_FPU_LIMIT:
        address, wrapper = queue.pop()
        if address in seen or address not in funcs:
            continue
        seen[address] = wrapper
        name, size = funcs[address]
        if name in ISR_FPU_STOP:
            continue
        code = img.read(address, size)
        if code is None:
            continue
        hits = fpu_instructions(code, address, data)
        if hits and name not in reported:
            reported.add(name)
            out.error("isr-fpu", img.path, f"function {name}",
                      f"{what}: {name} (reached from the interrupt wrapper {wrapper}) holds a floating-point "
                      f"instruction at {hx(hits[0])}: with lazy FP stacking its first FP instruction writes the "
                      "interrupted task's FP context onto that task's stack, which the pre hook made read-only for "
                      "the safety task - a bus fault (SYS170-S4); keep floating point out of interrupt handlers")
        for target, _ in thumb_calls(code, address):
            if target in funcs and target not in seen:
                queue.append((target, wrapper))
    return len(seen)


def isr_wrapper_problem(img: elffile.ElfImage, name: str, functions: dict[int, str]) -> str | None:
    """Why the function `name` is no interrupt wrapper of the safety shell (None if it is one)."""
    sym = img.symbol(name)
    code = img.read(sym.value & ~1, sym.size) if sym is not None and sym.size else None
    if code is None:
        return "its code is not in the image"
    calls = [functions.get(target, hex(target)) for target, _ in thumb_calls(code, sym.value & ~1)]
    if not calls or calls[0] != ISR_PRE_HOOK:
        return f"its first call is {calls[0] if calls else 'none'}, not {ISR_PRE_HOOK}"
    if ISR_POST_HOOK not in calls[1:]:
        return f"it never calls {ISR_POST_HOOK}"
    return None


def check_isr_wrappers(img: elffile.ElfImage, out: Findings, what: str) -> int:
    """SYS-170 (SYS69-S1, SFP-F01): every interrupt vector of an application with the safety shell runs through a
    wrapper that closes the MPU gate of the protected RAM first; returns the number of vectors checked.

    Independent of the generator: the vector table is read from the image (`__isr_vector`, its size), the wrapper is
    recognised by its code (first call SafePreIsrHook, later SafePostIsrHook), not by its name. An image without
    SafePreIsrHook has no safety shell and is not checked (bootloader, updater, small test applications)."""
    if img.symbol(ISR_PRE_HOOK) is None:
        return 0
    table = img.symbol("__isr_vector")
    if table is None:
        out.error("isr-vector-table", img.path, "symbol __isr_vector", f"{what}: no vector table symbol")
        return 0
    size = table.size or 0
    if not size:
        sec = next((s for s in img.sections if s.has_content and s.addr <= table.value < s.addr + s.size), None)
        size = 0 if sec is None else sec.addr + sec.size - table.value
    raw = img.read(table.value, size) or b""
    words = struct.unpack(f"<{len(raw) // 4}I", raw[:len(raw) // 4 * 4])
    functions = {s.value & ~1: s.name for s in img.symbols
                 if s.type == STT_FUNC and s.shndx != 0 and s.bind == 1}
    for s in img.symbols:                      # local functions only where no global one has the address
        if s.type == STT_FUNC and s.shndx != 0:
            functions.setdefault(s.value & ~1, s.name)
    exempt = {s.value & ~1 for s in img.symbols
              if s.type == STT_FUNC and s.shndx != 0 and s.name in ISR_UNWRAPPED_SAFETY}
    checked = 0
    verdicts: dict[str, str | None] = {}
    roots: dict[str, str] = {}
    for index in range(ISR_FIRST_CHECKED, len(words)):
        word = words[index]
        if word in (0, 0xFFFFFFFF):              # reserved (ARMv6-M) and the S32K14x TRIM word at the end
            continue
        checked += 1
        name = functions.get(word & ~1)
        where = f"vector {index}" + ("" if index < 16 else f" (IRQ {index - 16})")
        if name is None:
            out.error("isr-unwrapped", img.path, where, f"{what}: vector {hx(word)} is no function of the image")
            continue
        if name == ISR_DEFAULT or (word & ~1) in exempt:
            continue
        if name not in verdicts:
            verdicts[name] = isr_wrapper_problem(img, name, functions)
            if verdicts[name] is None:
                sym = img.symbol(name)
                calls = thumb_calls(img.read(sym.value & ~1, sym.size), sym.value & ~1)
                for target, _ in calls:
                    callee = functions.get(target)
                    if callee not in (None, ISR_PRE_HOOK, ISR_POST_HOOK):
                        roots.setdefault(callee, name)
        if verdicts[name] is not None:
            out.error("isr-unwrapped", img.path, where,
                      f"{what}: {name} is no interrupt wrapper of the safety shell - {verdicts[name]}; an interrupt "
                      "that preempts the safety cycle would run with write access to the protected RAM "
                      "(SAFE_ISR, safeLifecycle/IsrWrapper.cpp, startup/nxIrqVectors.inc)")
    roots.update({ISR_PRE_HOOK: ISR_PRE_HOOK, ISR_POST_HOOK: ISR_POST_HOOK})
    check_isr_fpu(img, out, what, roots)
    return checked


def check_rom_list(img: elffile.ElfImage, layout: Layout | None, out: Findings) -> None:
    """ROM check list of SM-14: directly behind the last flash content, inside the application area, its areas
    cover every byte of the image except the list and the reference CRC - and (SYS-91) the area with its own
    reference, the calibration segment, which no area may overlap."""
    syms = {n: img.symbol(n) for n in (ROM_LIST_START, ROM_LIST_END, ROM_RESULT)}
    missing = [n for n, s in syms.items() if s is None]
    if missing:
        out.error("rom-list-missing", img.path, "symbol " + ", ".join(missing),
                  "no ROM check list (SM-14): the image cannot be checked at start-up")
        return
    start, end, result = (syms[n].value for n in (ROM_LIST_START, ROM_LIST_END, ROM_RESULT))
    where = f"symbol {ROM_LIST_START}"
    raw = img.read(start, end - start) if end > start else None
    if raw is None or len(raw) < 4 or start % 4:
        out.error("rom-list", img.path, where, f"list {rng(start, end)} not readable as word aligned flash content")
        return
    count = struct.unpack_from("<I", raw)[0]
    if len(raw) != 4 + 8 * count or result != end:
        out.error("rom-list", img.path, where,
                  f"{count} areas need {4 + 8 * count} bytes, the list has {len(raw)}; result at {hx(result)}")
        return
    areas = [struct.unpack_from("<II", raw, 4 + 8 * i) for i in range(count)]
    list_range = (start, result + 4)
    others = [(a, b, n) for a, b, n in content_ranges(img) if not _ranges_overlap((a, b), list_range)]
    last = max(b for _, b, _ in others) if others else start
    if start != (last + 3) & ~3:
        out.error("rom-list-position", img.path, where,
                  f"list at {hx(start)}, the last flash content ends at {hx(last)}: the list belongs directly "
                  "behind it (SYS-38)")
    if layout is not None and not (layout.app_start <= start and list_range[1] <= layout.app_end):
        out.error("rom-list-position", img.path, where,
                  f"list and result {rng(*list_range)} outside the application area "
                  f"{rng(layout.app_start, layout.app_end)}")
    for i, (a, b) in enumerate(areas):
        if a > b or (layout is not None and a != b and not (layout.app_start <= a and b <= layout.app_end)):
            out.error("rom-list-area", img.path, where,
                      f"area {i} {rng(a, b)} empty-reversed or outside the application area")
        if _ranges_overlap((a, b), list_range):
            out.error("rom-list-area", img.path, where, f"area {i} {rng(a, b)} covers the list itself")
    live = sorted((a, b) for a, b in areas if a < b)
    for (a0, a1), (b0, b1) in zip(live, live[1:]):
        if b0 < a1:
            out.error("rom-list-area", img.path, where, f"areas {rng(a0, a1)} and {rng(b0, b1)} overlap")
    own_syms = [img.symbol(n) for n in (ROM_OWN_START, ROM_OWN_END)]
    exempt = []
    if any(own_syms):
        own_where = f"symbol {ROM_OWN_START}"
        if not all(own_syms):
            out.error("rom-own-area", img.path, own_where, f"only one of {ROM_OWN_START}/{ROM_OWN_END}")
        else:
            o0, o1 = (s.value for s in own_syms)
            if o0 % 4 or o1 % 4 or o0 >= o1 or o1 > start or \
                    (layout is not None and not (layout.app_start <= o0 and o1 <= layout.app_end)):
                out.error("rom-own-area", img.path, own_where,
                          f"area with own reference {rng(o0, o1)} not word aligned, empty or outside the application "
                          "area before the list")
            for a, b in live:
                if _ranges_overlap((a, b), (o0, o1)):
                    out.error("rom-own-area", img.path, own_where,
                              f"ROM check area {rng(a, b)} overlaps the area with own reference {rng(o0, o1)} "
                              "(calibration segment, SYS-91): a dataset flashed on its own would fail SM-14")
            exempt.append((o0, o1))
    for a, b, name in others:
        address = a
        for s, e in sorted(live + exempt):
            if s <= address < e:
                address = e
        if address < b:
            out.error("rom-list-coverage", img.path, f"section {name}",
                      f"flash bytes from {hx(address)} (section {rng(a, b)}) are in no area of the ROM check list")


def image_from_hex_or_elf(img: elffile.ElfImage, out: Findings) -> tuple[dict[int, int], Path | None]:
    """The bytes that go to the target: the hex file next to the ELF if there is one (post-link steps write into
    it), checked against the ELF, else the ELF's own sections."""
    hex_path = img.path.with_suffix(".hex")
    elf_bytes = img.flash_bytes()
    if not hex_path.is_file():
        return elf_bytes, None
    try:
        hex_bytes = ihex.load(hex_path)
    except (OSError, ihex.HexError) as e:
        out.error("hex-unreadable", hex_path, "file", str(e))
        return elf_bytes, None
    result = img.symbol(ROM_RESULT)
    patched = set(range(result.value, result.value + 4)) if result else set()
    extra = sorted(set(hex_bytes) - set(elf_bytes))
    if extra:
        out.error("hex-differs", hex_path, "file", f"bytes at {hx(extra[0])} (and {len(extra) - 1} more) that no "
                  f"section of {img.path.name} holds")
    lost = sorted(set(elf_bytes) - set(hex_bytes))
    if lost:
        out.error("hex-differs", hex_path, "file", f"no bytes at {hx(lost[0])} (and {len(lost) - 1} more) where "
                  f"{img.path.name} has content")
    changed = sorted(a for a in set(elf_bytes) & set(hex_bytes) if elf_bytes[a] != hex_bytes[a] and a not in patched)
    if changed:
        out.error("hex-differs", hex_path, "file",
                  f"byte at {hx(changed[0])} differs from {img.path.name} ({len(changed)} bytes; only the ROM check "
                  "result may be written after the link)")
    return hex_bytes, hex_path


def check_record(record: Path, image: dict[int, int], layout: Layout, exp: Expectation, out: Findings,
                 what: str) -> None:
    """Validity record of an image where the bootloader looks for it, matching the image and the layout."""
    if not record.is_file():
        out.error("record-missing", record, "file", f"no validity record for the {what}: the bootloader would not "
                  "start it")
        return
    try:
        data = ihex.load(record)
    except (OSError, ihex.HexError) as e:
        out.error("record-unreadable", record, "file", str(e))
        return
    blocks = ihex.blocks(data)
    if len(blocks) != 1:
        out.error("record-position", record, "record", f"{len(blocks)} blocks, one record expected")
        return
    address, raw = blocks[0]
    if address != layout.record_addr:
        out.error("record-position", record, "record",
                  f"at {hx(address)}, the bootloader reads the validity record at {hx(layout.record_addr)}")
    if len(raw) < RECORD_V1_SIZE or len(raw) % max(layout.phrase_size, 1):
        out.error("record-format", record, "record", f"{len(raw)} bytes, not whole flash phrases of "
                  f"{layout.phrase_size} bytes or shorter than {RECORD_V1_SIZE}")
        return
    magic, start, size, crc, record_crc, reserved = struct.unpack_from("<4sIIIII", raw)
    if magic != RECORD_MAGIC:
        out.error("record-format", record, "record", f"magic {magic!r}, expected {RECORD_MAGIC!r}")
    if record_crc != zlib.crc32(raw[:16]) or reserved != 0xFFFFFFFF:
        out.error("record-format", record, "record", "header CRC or reserved word wrong")
    addresses = [a for a in image if layout.app_start <= a < layout.app_end]
    if start != layout.app_start:
        out.error("record-image", record, "record",
                  f"image start {hx(start)}, the application area starts at {hx(layout.app_start)}")
    if addresses:
        body = bytes(image.get(a, 0xFF) for a in range(layout.app_start, max(addresses) + 1))
        if size != len(body) or crc != zlib.crc32(body):
            out.error("record-image", record, "record",
                      f"size {size:#x}/CRC {crc:#010x} do not match the {what} image "
                      f"({len(body):#x} bytes, CRC {zlib.crc32(body):#010x})")
    if len(raw) >= RECORD_V1_SIZE + COMPAT_SIZE and raw[24:28] != b"\xff" * 4:
        cmagic, version, csize, target, layout_id, app_end, record_addr, ccrc = struct.unpack_from("<4s7I", raw, 24)
        if cmagic != COMPAT_MAGIC or version != COMPAT_VERSION or csize != COMPAT_SIZE \
                or ccrc != zlib.crc32(raw[24:24 + COMPAT_SIZE - 4]):
            out.error("record-format", record, "record compatibility block", "magic, version, size or CRC wrong")
            return
        if layout_id != layout.layout_id:
            out.error("record-layout", record, "record compatibility block",
                      f"layout identity {layout_id:#010x}, the bootloader image has {layout.layout_id:#010x}")
        if (app_end, record_addr) != (layout.app_end, layout.record_addr):
            out.error("record-layout", record, "record compatibility block",
                      f"application end {hx(app_end)}/record {hx(record_addr)}, the bootloader has "
                      f"{hx(layout.app_end)}/{hx(layout.record_addr)}")
        if exp.target and target != zlib.crc32(exp.target.encode("ascii")):
            out.error("record-target", record, "record compatibility block",
                      f"target identity {target:#010x} is not CRC-32 of '{exp.target}'")
    else:
        out.warning("record-compat-missing", record, "record", "no compatibility block (record before BL-02)")


def check_updater_blob(upd: elffile.ElfImage, bl: elffile.ElfImage, layout: Layout, bl_area: tuple[int, int],
                       out: Findings) -> None:
    """The bootloader the updater carries is this bootloader image and fits its area."""
    sym = upd.symbol(BLOB_SYMBOL)
    where = f"symbol {BLOB_SYMBOL}"
    if sym is None:
        out.error("blob-missing", upd.path, where, "no embedded bootloader")
        return
    raw = upd.read(sym.value, sym.size)
    if raw is None or len(raw) < 16 or raw[:4] != BLOB_MAGIC:
        out.error("blob-format", upd.path, where, "not a bootloader blob (magic, size)")
        return
    _, size, crc, head_crc = struct.unpack_from("<4sIII", raw)
    body = raw[16:16 + size]
    if head_crc != zlib.crc32(raw[:12]) or len(body) != size or crc != zlib.crc32(body):
        out.error("blob-format", upd.path, where, "header or image CRC wrong")
        return
    if bl_area[0] + size > bl_area[1]:
        out.error("blob-size", upd.path, where,
                  f"embedded bootloader of {size:#x} bytes does not fit the bootloader area {rng(*bl_area)}")
    image = bl.flash_bytes()
    if image:
        top = max(image) + 1
        expected = bytes(image.get(a, 0xFF) for a in range(layout.flash_start, top))
        expected += b"\xff" * (-len(expected) % max(layout.phrase_size, 1))
        if body != expected:
            out.error("blob-differs", upd.path, where,
                      f"embedded bootloader ({size:#x} bytes, CRC {crc:#010x}) is not the bootloader image "
                      f"{bl.path.name} ({len(expected):#x} bytes, CRC {zlib.crc32(expected):#010x})")


# ---------------------------------------------------------------------- the whole check

def _load(path: Path | None, out: Findings) -> elffile.ElfImage | None:
    if path is None:
        return None
    try:
        return elffile.load(path)
    except elffile.ElfError as e:
        out.error("elf-unreadable", path, "file", str(e))
        return None


def check_image_config_field(img: elffile.ElfImage, exp: Expectation, out: Findings, what: str) -> None:
    """NVM-15: the configuration field in the ELF and in the hex file next to it (the file J-Link writes)."""
    check_config_field(img.flash_bytes(), exp, out, img.path, what)
    hex_path = img.path.with_suffix(".hex")
    if hex_path.is_file():
        try:
            data = ihex.load(hex_path)
        except (OSError, ihex.HexError):
            return                      # the hex of an application is reported by image_from_hex_or_elf
        check_config_field(data, exp, out, hex_path, what)


def check_config_field_file(path: Path, exp: Expectation) -> Findings:
    """NVM-15: only the configuration field of one image (ELF or Intel hex), for the post-build step of an image
    that writes sector 0 (bootloader/CMakeLists.txt)."""
    out = Findings()
    path = Path(path)
    if path.suffix.lower() == ".hex":
        try:
            check_config_field(ihex.load(path), exp, out, path, path.stem)
        except (OSError, ihex.HexError) as e:
            out.error("hex-unreadable", path, "file", str(e))
        return out
    img = _load(path, out)
    if img is not None:                 # the ELF alone: its hex is named on the command line where it matters
        check_config_field(img.flash_bytes(), exp, out, img.path, path.stem)
    return out


def record_of(elf: Path) -> Path:
    return elf.with_name(elf.stem + ".record.hex")


def check_images(exp: Expectation, bootloader: Path | None = None, app: Path | None = None,
                 updater: Path | None = None, records: bool = True, rom_list: bool = True) -> Findings:
    """Checks the given images against the expectation; validity records are `<stem>.record.hex` next to the ELF."""
    out = Findings()
    bl, ap, up = _load(bootloader, out), _load(app, out), _load(updater, out)
    if out.errors:
        return out
    layout = read_layout(bl, out) if bl else None
    bl_area = None
    all_ram = [(a, b) for a, b in exp.ram_spans()]
    app_ram = [(n, a, b) for n, a, b in exp.app_ram] or None
    if bl is not None:
        check_core(bl, exp, out)
        if layout is not None:
            bl_area = check_layout(bl, layout, exp, out)
        bl_ram = [exp.bootloader_ram] if exp.bootloader_ram else None
        check_sections(bl, exp, out, bl_area, "bootloader area", bl_ram)
        check_vectors(bl, exp, out, exp.flash.start, [(a, b) for _, a, b in bl_ram] if bl_ram else all_ram,
                      "bootloader")
        check_image_config_field(bl, exp, out, "bootloader")
        check_heap(bl, out, "bootloader")
        if layout is not None:
            check_boot_request_free(bl, layout, out, "bootloader")
    elif ap is not None or up is not None:
        out.warning("no-bootloader", "-", "input", "no bootloader image: application area, validity record and "
                    "overlap with the bootloader are not checked")
    area = (layout.app_start, layout.app_end) if layout else None
    for img, what in ((ap, "application"), (up, "updater")):
        if img is None:
            continue
        check_core(img, exp, out)
        check_sections(img, exp, out, area, "application area", app_ram)
        start = layout.app_start if layout else (content_ranges(img) or [(exp.flash.start,)])[0][0]
        check_vectors(img, exp, out, start, [(a, b) for _, a, b in app_ram] if app_ram else all_ram, what)
        check_image_config_field(img, exp, out, what)
        check_heap(img, out, what)
        if layout is not None:
            check_boot_request_free(img, layout, out, what)
        if what == "application":
            check_isr_wrappers(img, out, what)
        if what == "application" and rom_list:
            check_rom_list(img, layout, out)
        if layout is not None and records:
            image, _ = image_from_hex_or_elf(img, out)
            check_record(record_of(img.path), image, layout, exp, out, what)
    if up is not None and bl is not None and layout is not None and bl_area is not None:
        check_updater_blob(up, bl, layout, bl_area, out)
    check_cross_overlap([(n, i) for n, i in (("bootloader", bl), ("application", ap), ("updater", up)) if i], out)
    return out


BUILD_IMAGES = {"bootloader": "bootloader.elf", "app": "app.ecu.elf", "updater": "blupdater.elf"}


def find_build_images(build: Path) -> dict[str, Path]:
    """The images of a CMake build directory (the same names tools/test expects)."""
    found = {}
    for key, name in BUILD_IMAGES.items():
        hits = sorted(Path(build).rglob(name))
        hits = [h for h in hits if "CMakeFiles" not in h.parts]
        if hits:
            found[key] = hits[0]
    return found
