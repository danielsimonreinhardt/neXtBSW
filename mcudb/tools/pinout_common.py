"""Shared part of the package pinout generators (TOOL-162): file format, pin kinds, checks against the pin table.

A pinout file `mcudb/<family>/pinout/<part>.yaml` belongs to the pin multiplexing table `mcudb/<family>/<part>.pinmux.yaml`
of the same package (same part name) and names every terminal of the package (pin number or ball) with what is bonded
to it. It lives in the subfolder `pinout/` so that the tools that read every `mcudb/<family>/*.yaml` as a derivative
never see it:

    schema_version: 1
    part: S32K144_LQFP64
    outline: {shape: quad, pins: 64}                 # quad: LQFP/QFP/QFN, pin 1 top left, counted anticlockwise
    outline: {shape: grid, rows: [A, ...], columns: 10}   # grid: BGA seen from the top, ball = row letter + column
    source: "<document, revision, table>"
    pins:
      1: PTD1                                       # a pad of the pin table (same name as in <part>.pinmux.yaml)
      7: {name: VDD, kind: power}                   # a terminal without pin multiplexing: kind power, ground,
                                                    # reset, boot, test or nc

The pad names are the pin names of the pin table, so a pinout and its table must agree (check_against_pinmux). The
drawing (where a terminal sits) follows from `outline` alone and is done by the user of the file (Studio), the file
only holds the facts of the data sheet.
"""

from __future__ import annotations

import re
from pathlib import Path

MCUDB = Path(__file__).resolve().parents[1]
KINDS = ("power", "ground", "reset", "boot", "test", "nc")
GROUND_RE = re.compile(r"^(VSS|VREFL|GND|ADREFLO|VSSAD|KELVIN_GND)", re.IGNORECASE)
POWER_RE = re.compile(r"^(VDD|VCC|VREFH|VBAT|VCAP|ADREFHI|VREF\+)", re.IGNORECASE)


class SourceError(Exception):
    pass


def pinout_file(pinmux_file: Path) -> Path:
    """mcudb/<family>/pinout/<part>.yaml of the pin table mcudb/<family>/<part>.pinmux.yaml."""
    name = Path(pinmux_file).name
    if not name.endswith(".pinmux.yaml"):
        raise SourceError(f"{name}: not a pin table")
    return Path(pinmux_file).parent / "pinout" / (name[: -len(".pinmux.yaml")] + ".yaml")


def natural(text) -> list:
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", str(text))]


def supply_kind(name: str) -> str:
    """power or ground for a supply terminal name ("VDDA/VREFH" -> power, "VREFL/VSS" -> ground)."""
    parts = name.split("/")
    if any(POWER_RE.match(p) for p in parts):
        return "power"
    if all(GROUND_RE.match(p) for p in parts):
        return "ground"
    raise SourceError(f"{name}: not a known supply name")


def quad(count: int) -> dict:
    return {"shape": "quad", "pins": count}


def grid(balls) -> dict:
    """Outline of a ball grid from the ball names found (rows as letters, columns as numbers)."""
    rows: set = set()
    columns = 0
    for ball in balls:
        m = re.fullmatch(r"([A-Z]+)(\d+)", str(ball))
        if not m:
            raise SourceError(f"ball name {ball}")
        rows.add(m.group(1))
        columns = max(columns, int(m.group(2)))
    return {"shape": "grid", "rows": sorted(rows, key=lambda r: (len(r), r)), "columns": columns}


def check_complete(part: str, outline: dict, pins: dict) -> None:
    """Every terminal of the outline has exactly one entry (quad: 1..n); raises SourceError otherwise."""
    if outline["shape"] == "quad":
        expected = set(range(1, outline["pins"] + 1))
        found = set(pins)
        if found != expected:
            raise SourceError(f"{part}: pins missing {sorted(expected - found)[:10]}, extra {sorted(found - expected)[:10]}")


def _key(value) -> str:
    return str(value)


def _entry(value) -> str:
    if isinstance(value, str):
        return value
    return "{name: " + _quote(value["name"]) + ", kind: " + value["kind"] + "}"


def _quote(text: str) -> str:
    return text if re.fullmatch(r"[A-Za-z0-9_/+\-]+", text) and not text[0] in "-+" else '"' + text + '"'


def render(part: str, outline: dict, source: str, pins: dict, header: list[str]) -> str:
    """YAML text of one pinout file; `header` are the comment lines (origin, licence, generator)."""
    check_complete(part, outline, pins)
    for number, value in pins.items():
        if not isinstance(value, str) and value.get("kind") not in KINDS:
            raise SourceError(f"{part} pin {number}: kind {value.get('kind')!r}")
    if outline["shape"] == "quad":
        shape = f"{{shape: quad, pins: {outline['pins']}}}"
    else:
        shape = f"{{shape: grid, rows: [{', '.join(outline['rows'])}], columns: {outline['columns']}}}"
    lines = [f"# {line}" if line else "#" for line in header] + [
        "schema_version: 1",
        f"part: {part}",
        f"outline: {shape}",
        f'source: "{source}"',
        "pins:",
    ]
    for number in sorted(pins, key=natural):
        lines.append(f"  {_key(number)}: {_entry(pins[number])}")
    return "\n".join(lines) + "\n"


def pads_of_pinmux(file: Path) -> set[str]:
    """Pin names of a generated pin table (keys under every signal)."""
    from ruamel.yaml import YAML
    data = YAML(typ="safe").load(file.read_text(encoding="utf-8"))
    return {str(pin) for pins in (data.get("signals") or {}).values() for pin in pins}


def check_against_pinmux(part: str, pins: dict, pinmux_file: Path) -> list[str]:
    """Differences between the pads of a pinout and the pins of its table (empty list: they agree)."""
    pads = {v for v in pins.values() if isinstance(v, str)}
    table = pads_of_pinmux(pinmux_file)
    out = []
    if table - pads:
        out.append(f"{part}: pins of {pinmux_file.name} without a terminal: {', '.join(sorted(table - pads, key=natural))}")
    if pads - table:
        out.append(f"{part}: pads without an entry in {pinmux_file.name}: {', '.join(sorted(pads - table, key=natural))}")
    return out


def write_or_check(targets: dict[Path, str], check: bool) -> int:
    """Write every target whose text changed (or, with check, list them); returns the exit code."""
    stale = []
    for target, text in targets.items():
        current = target.read_text(encoding="utf-8") if target.exists() else None
        if current == text:
            continue
        stale.append(target)
        if not check:
            target.parent.mkdir(exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="\n")
    for target in stale:
        print(("outdated: " if check else "written: ") + target.relative_to(MCUDB).as_posix())
    return 1 if (check and stale) else 0
