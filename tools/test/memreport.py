"""Memory report of the application image per build profile (PLAT-29).

The measuring link of PLAT-12 as code. A profile whose application does not fit its flash or RAM regions does not
link, and a failed link leaves no image to measure. Before PLAT-29 the numbers of such a profile (S32K116) were
measured by hand once (`--noinhibit-exec`, regions enlarged) and stood in the changelog. This module repeats the
last link step of a built profile on its own, next to the real image, so that a profile that overflows still gets a
complete report:

  * the link command and the response file come from the build files of CMake (`ninja -t commands`, the build
    statement of the target), not from a copy that could drift,
  * `--noinhibit-exec` keeps the image although a region overflows, `--print-memory-usage` gives the budget per
    linker region, exactly as the linker counts it (same alignment and fill as the error message "overflowed by"),
  * the map file gives the biggest items per library or object file, the symbol table the biggest symbols.

The budget of a profile is the size of its linker regions (derivative + project: memory.application, bootloader area,
hot RAM, no-init area); `memory_budget.yaml` adds a reserve that must stay free per region. Parsing and checking
work on text and run without a toolchain (test_memory_report.py); only the measuring link needs the build directory
of the profile and arm-none-eabi-gcc. Standard library only (PyYAML for the table in the test).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

UNITS = {"B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3}
TOP = 8                                 # items per list of the report


# ---------------------------------------------------------------------------- linker regions and their usage


@dataclass(frozen=True)
class Region:
    """One linker region: `size` is the budget, `used` what the linker placed in it (None: not measured)."""

    name: str
    origin: int
    size: int
    attrs: str
    used: int | None = None

    @property
    def kind(self) -> str:
        """`ram` for a writable region, otherwise `flash` (the attributes of the MEMORY command, `rw` / `xr`)."""
        return "ram" if "w" in self.attrs.lower() else "flash"

    @property
    def free(self) -> int | None:
        return None if self.used is None else self.size - self.used

    @property
    def over(self) -> int:
        """Bytes beyond the region (0: fits)."""
        return 0 if self.used is None else max(0, self.used - self.size)


def parse_map_regions(map_text: str) -> list[Region]:
    """Regions of the block `Memory Configuration` of a map file (origin, length, attributes)."""
    regions: list[Region] = []
    inside = False
    for line in map_text.splitlines():
        if line.startswith("Memory Configuration"):
            inside = True
            continue
        if inside and line.startswith("Linker script and memory map"):
            break
        m = re.match(r"^(\S+)\s+0x([0-9a-fA-F]+)\s+0x([0-9a-fA-F]+)(?:\s+(\w+))?\s*$", line) if inside else None
        if m and m.group(1) != "*default*":
            regions.append(Region(m.group(1), int(m.group(2), 16), int(m.group(3), 16), m.group(4) or ""))
    return regions


def parse_memory_usage(text: str) -> dict[str, int]:
    """`--print-memory-usage` of ld: {region: used bytes}. ld prints a size in the largest unit that divides it
    exactly (B, KB, MB, GB), so the bytes are exact."""
    used: dict[str, int] = {}
    for line in text.splitlines():
        m = re.match(r"^\s*(\S+):\s+(\d+)\s+(B|KB|MB|GB)\s+(\d+)\s+(B|KB|MB|GB)\s+[\d.]+%", line)
        if m:
            used[m.group(1)] = int(m.group(2)) * UNITS[m.group(3)]
    return used


def with_usage(regions: list[Region], used: dict[str, int]) -> list[Region]:
    return [Region(r.name, r.origin, r.size, r.attrs, used.get(r.name)) for r in regions]


def check_budget(regions: list[Region], min_free: dict[str, int] | None = None) -> list[str]:
    """Findings of a profile (empty: within budget). A region over its size and a region with less than its
    reserve (`min_free`, bytes per region name) left are findings; a region without a measured value is one too,
    so that a missing line of the linker output cannot pass as 'fits'."""
    min_free = min_free or {}
    findings: list[str] = []
    for r in regions:
        if r.used is None:
            findings.append(f"{r.name}: no usage reported by the linker")
        elif r.over:
            findings.append(f"{r.name} ({r.kind}): {r.used} of {r.size} bytes used, {r.over} bytes too much")
        elif r.free < min_free.get(r.name, 0):
            findings.append(f"{r.name} ({r.kind}): {r.free} bytes free, at least {min_free[r.name]} must stay free "
                            f"(tools/test/memory_budget.yaml)")
    unknown = sorted(set(min_free) - {r.name for r in regions})
    findings += [f"{name}: reserve in memory_budget.yaml names no linker region" for name in unknown]
    return findings


# ---------------------------------------------------------------------------- sections, map file, symbols


@dataclass(frozen=True)
class Kind:
    """Where the bytes of an output section end up: in the flash image, in the RAM, or both (initialised data)."""

    flash: bool
    ram: bool


def parse_sections(readelf_text: str) -> dict[str, Kind]:
    """`readelf -SW`: allocated output sections. NOBITS only takes RAM, writable PROGBITS takes RAM and the flash
    image, the rest takes flash. Sections that are not allocated (debug information) are left out."""
    kinds: dict[str, Kind] = {}
    for line in readelf_text.splitlines():
        m = re.match(r"^\s*\[\s*\d+\]\s+(\S+)\s+(\S+)\s+[0-9a-fA-F]+\s+[0-9a-fA-F]+\s+[0-9a-fA-F]+\s+[0-9a-fA-F]+\s+"
                     r"([A-Z]*)\s+\d+\s+\d+\s+\d+", line)
        if not m or "A" not in m.group(3):
            continue
        name, kind, flags = m.groups()
        kinds[name] = Kind(flash=kind != "NOBITS", ram=kind == "NOBITS" or "W" in flags)
    return kinds


def group_of(file: str) -> str:
    """Library or object file of a map entry: `libfoo.a(bar.obj)` -> `libfoo.a`, a path -> the file name."""
    file = file.strip()
    if "(" in file and file.endswith(")"):
        file = file[:file.index("(")]
    name = re.split(r"[\\/]", file)[-1]
    return name[:-4] if name.endswith(".obj") else name


def parse_map_inputs(map_text: str, kinds: dict[str, Kind]) -> dict[str, list[int]]:
    """{library or object: [flash bytes, RAM bytes]} from the input sections of a map file. The output section an
    input section belongs to decides flash and RAM (`kinds`); initialised data counts in both."""
    totals: dict[str, list[int]] = {}
    started = False
    output = None
    pending = None                      # name of an input section whose numbers are on the next line
    for line in map_text.splitlines():
        if not started:
            started = line.startswith("Linker script and memory map")
            continue
        head = re.match(r"^(\.\S+)(?:\s+0x[0-9a-fA-F]+\s+0x[0-9a-fA-F]+(?:\s+load address\s+0x[0-9a-fA-F]+)?)?\s*$",
                        line)
        if head:
            output = head.group(1)
            pending = None
            continue
        if line and not line[0].isspace():
            output = None if line.startswith("/DISCARD/") or not line.startswith(".") else output
            continue
        if output is None or output not in kinds:
            continue
        wrapped = re.match(r"^\s+0x[0-9a-fA-F]+\s+0x([0-9a-fA-F]+)\s+(\S.*)$", line)
        entry = re.match(r"^ (\.\S+)\s+0x[0-9a-fA-F]+\s+0x([0-9a-fA-F]+)\s+(\S.*)$", line)
        if entry:
            size, file = int(entry.group(2), 16), entry.group(3)
        elif pending and wrapped:
            size, file = int(wrapped.group(1), 16), wrapped.group(2)
        else:
            m = re.match(r"^ (\.\S+)\s*$", line)
            pending = m.group(1) if m else None
            continue
        pending = None
        if size == 0:
            continue
        kind = kinds[output]
        entry_total = totals.setdefault(group_of(file), [0, 0])
        entry_total[0] += size if kind.flash else 0
        entry_total[1] += size if kind.ram else 0
    return totals


@dataclass(frozen=True)
class Symbol:
    name: str
    size: int
    flash: bool
    ram: bool


def parse_symbols(nm_text: str) -> list[Symbol]:
    """`nm -S -C`: symbols with a size. Code, read-only data and weak symbols take flash, `b`/`B` takes RAM,
    `d`/`D` (initialised data) both."""
    symbols = []
    for line in nm_text.splitlines():
        m = re.match(r"^[0-9a-fA-F]+\s+([0-9a-fA-F]+)\s+(\w)\s+(.+)$", line)
        if not m or int(m.group(1), 16) == 0:
            continue
        kind = m.group(2)
        symbols.append(Symbol(m.group(3).strip(), int(m.group(1), 16),
                              flash=kind in "tTrRwWvViIdD", ram=kind in "bBsSdD"))
    return symbols


def biggest(totals: dict[str, list[int]], column: int, top: int = TOP) -> list[tuple[str, int]]:
    ranked = sorted(((name, v[column]) for name, v in totals.items() if v[column] > 0), key=lambda e: (-e[1], e[0]))
    return ranked[:top]


def biggest_symbols(symbols: list[Symbol], ram: bool, top: int = TOP) -> list[tuple[str, int]]:
    chosen = [s for s in symbols if (s.ram if ram else s.flash)]
    return [(s.name, s.size) for s in sorted(chosen, key=lambda s: (-s.size, s.name))[:top]]


# ---------------------------------------------------------------------------- report


@dataclass
class Report:
    profile: str
    regions: list[Region]
    inputs: dict[str, list[int]] = field(default_factory=dict)
    symbols: list[Symbol] = field(default_factory=list)
    linked: bool = True                 # the real link of the profile went through (False: only the measuring link)

    def text(self, top: int = TOP) -> str:
        lines = [f"Speicherbericht {self.profile}" + ("" if self.linked else "  (Abbild linkt NICHT, Mess-Link)"),
                 f"  {'Region':<24} {'Art':<6} {'Adresse':>10} {'Budget':>9} {'belegt':>9} {'frei':>9}"]
        for r in self.regions:
            used = "-" if r.used is None else str(r.used)
            free = "-" if r.free is None else (str(r.free) if r.free >= 0 else f"{r.free} (UEBER)")
            lines.append(f"  {r.name:<24} {r.kind:<6} {r.origin:>#10x} {r.size:>9} {used:>9} {free:>9}")
        for title, rows in (("Flash nach Bibliothek/Objekt", biggest(self.inputs, 0, top)),
                            ("RAM nach Bibliothek/Objekt", biggest(self.inputs, 1, top)),
                            ("Flash nach Symbol", biggest_symbols(self.symbols, False, top)),
                            ("RAM nach Symbol", biggest_symbols(self.symbols, True, top))):
            if rows:
                lines.append(f"  groesste Posten, {title}:")
                lines += [f"    {size:>8}  {name}" for name, size in rows]
        return "\n".join(lines)

    def as_dict(self, top: int = TOP) -> dict:
        return {
            "profile": self.profile, "linked": self.linked,
            "regions": [{"name": r.name, "kind": r.kind, "origin": r.origin, "size": r.size, "used": r.used,
                         "free": r.free} for r in self.regions],
            "flash_by_library": biggest(self.inputs, 0, top), "ram_by_library": biggest(self.inputs, 1, top),
            "flash_by_symbol": biggest_symbols(self.symbols, False, top),
            "ram_by_symbol": biggest_symbols(self.symbols, True, top),
        }


# ---------------------------------------------------------------------------- measuring link


class MeasureError(RuntimeError):
    pass


def _unescape(text: str) -> str:
    return re.sub(r"\$(.)", r"\1", text)


def response_file_content(build_dir: Path, config: str, target: str) -> str:
    """Content of the response file CMake gives the linker (`rspfile_content = $in $LINK_PATH $LINK_LIBRARIES`),
    rebuilt from the build statement of `target` - ninja writes the file only while it links and deletes it after."""
    candidates = [build_dir / "CMakeFiles" / f"impl-{config}.ninja", build_dir / f"build-{config}.ninja"]
    for file in candidates:
        if not file.is_file():
            continue
        lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
        for i, line in enumerate(lines):
            if not line.startswith(f"build {target}:"):
                continue
            statement = line.split(":", 1)[1].replace("$ ", "\0").replace("$:", "\1").replace("$$", "\2")
            explicit = statement.split("|")[0].split()[1:]               # first word is the rule
            variables: dict[str, str] = {}
            for follow in lines[i + 1:]:
                if not follow.startswith("  "):
                    break
                name, _, value = follow.strip().partition(" = ")
                variables[name] = value
            parts = [" ".join(w.replace("\0", " ").replace("\1", ":").replace("\2", "$") for w in explicit),
                     variables.get("LINK_PATH", ""), variables.get("LINK_LIBRARIES", "")]
            return _unescape(" ".join(p for p in parts if p))
    raise MeasureError(f"no build statement for {target} in {build_dir}")


def link_command(build_dir: Path, config: str, target: str, ninja: str, env: dict[str, str]) -> str:
    """The command of the link step of `target`, as ninja runs it (without the steps before and after it)."""
    out = subprocess.run([ninja, "-f", f"build-{config}.ninja", "-t", "commands", target], cwd=build_dir,
                         capture_output=True, text=True, env=env)
    if out.returncode != 0:
        raise MeasureError(f"ninja -t commands {target}: {(out.stdout + out.stderr)[-400:]}")
    for segment in " && ".join(out.stdout.strip().splitlines()).split(" && "):
        if " -T " in segment and re.search(r"@\S+\.rsp", segment):
            return segment.strip().rstrip('"')
    raise MeasureError(f"no link command in the commands of {target}")


@dataclass
class MeasureResult:
    elf: Path
    map: Path
    returncode: int
    log: str
    usage: dict[str, int]


def measuring_link(build_dir: Path, out_dir: Path, *, config: str = "RelWithDebInfo",
                   target: str = "ecu/application/RelWithDebInfo/app.ecu.elf", gcc_path: str | None = None,
                   shrink: tuple[str, int] | None = None) -> MeasureResult:
    """Links the objects of the profile once more into `out_dir`, with `--noinhibit-exec` and
    `--print-memory-usage`. The image of the profile is not touched. `shrink` = (region, length) is the negative
    control: the same link with one region of the linker script made smaller, to show that an overflow turns up."""
    env = dict(os.environ)
    if gcc_path is not None:
        env["PATH"] = gcc_path
    ninja = shutil.which("ninja", path=env.get("PATH"))
    if ninja is None:
        raise MeasureError("ninja not found")
    out_dir.mkdir(parents=True, exist_ok=True)
    command = link_command(build_dir, config, target, ninja, env)
    rsp = out_dir / "measure.rsp"
    rsp.write_text(response_file_content(build_dir, config, target), encoding="utf-8")
    elf, map_file = out_dir / "measure.elf", out_dir / "measure.map"
    command = re.sub(r"@\S+\.rsp", lambda _: f'@"{rsp}"', command)
    command = re.sub(r"-Wl,-Map,\S+", lambda _: f'-Wl,-Map,"{map_file}"', command)
    command = re.sub(r" -o \S+", lambda _: f' -o "{elf}"', command)
    if shrink is not None:
        script = re.search(r"-T\s+(\S+)", command)
        if script is None:
            raise MeasureError("no linker script in the link command")
        text = Path(script.group(1)).read_text(encoding="utf-8")
        name, length = shrink
        small, count = re.subn(rf"(\b{re.escape(name)}\s*\(\w+\)\s*:\s*ORIGIN\s*=\s*[^,\n]+,\s*LENGTH\s*=\s*)[^\n]+",
                               lambda m: f"{m.group(1)}{length:#x}", text)
        if count != 1:
            raise MeasureError(f"region {name} not found in the linker script")
        (out_dir / "shrunk.ld").write_text(small, encoding="utf-8")
        command = command.replace(script.group(1), f'"{out_dir / "shrunk.ld"}"')
    command += " -Wl,--noinhibit-exec -Wl,--print-memory-usage"
    for stale in (elf, map_file):
        stale.unlink(missing_ok=True)
    done = subprocess.run(command, shell=True, cwd=build_dir, capture_output=True, text=True, env=env)
    log = done.stdout + done.stderr
    if not elf.is_file() or not map_file.is_file():
        raise MeasureError(f"measuring link produced no image (exit code {done.returncode}):\n{log[-3000:]}")
    return MeasureResult(elf, map_file, done.returncode, log, parse_memory_usage(log))


def measure(profile: str, build_dir: Path, out_dir: Path, *, gcc_path: str | None = None, linked: bool = True,
            shrink: tuple[str, int] | None = None, **kwargs) -> Report:
    """Measuring link of the profile and the whole report (regions, biggest items)."""
    result = measuring_link(build_dir, out_dir, gcc_path=gcc_path, shrink=shrink, **kwargs)
    map_text = result.map.read_text(encoding="utf-8", errors="replace")
    regions = with_usage(parse_map_regions(map_text), result.usage)
    tools = _binutils(gcc_path)
    readelf = subprocess.run([tools["readelf"], "-SW", str(result.elf)], capture_output=True, text=True, check=True)
    nm = subprocess.run([tools["nm"], "-S", "-C", str(result.elf)], capture_output=True, text=True, check=True)
    return Report(profile, regions, parse_map_inputs(map_text, parse_sections(readelf.stdout)),
                  parse_symbols(nm.stdout), linked)


def _binutils(gcc_path: str | None) -> dict[str, str]:
    gcc = shutil.which("arm-none-eabi-gcc", path=gcc_path)
    if gcc is None:
        raise MeasureError("arm-none-eabi-gcc not found")
    return {name: str(Path(gcc).with_name(Path(gcc).name.replace("gcc", name))) for name in ("readelf", "nm")}
