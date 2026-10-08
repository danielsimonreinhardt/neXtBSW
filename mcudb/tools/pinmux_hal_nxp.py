"""Generates the pin multiplexing tables of the derivative database from Zephyr hal_nxp pinctrl headers.

Input:  mcudb/<family>/sources/hal_nxp/<part>-pinctrl.h (copied unchanged from Zephyr hal_nxp dts/nxp/s32/pinctrl,
        Copyright NXP, SPDX-License-Identifier: Apache-2.0; the licence notice is carried into every generated file,
        the licence text lies next to the headers)
Output: mcudb/<family>/<part>.pinmux.yaml, one table per header (derivative + package)

Table format (schema PinmuxTable in tools/nxconfig/model.py): signal -> pin -> selector. Signal names are normalised
to the instance names of the derivative database: CAN0_RX -> FlexCAN0_RX, FXIO_D0 -> FlexIO_D0,
EMIOS_1_CH4_H -> EMIOS1_CH4 (the letter is the eMIOS channel type).

Two header formats are understood:
- KINETIS_MUX(port, pin, mux) (S32K1): selector = PORT_PCR.MUX (ALT number); a plain pin name (PTA0) is GPIO.
- NXP_SIUL2_PINMUX(mscr_siul2, imcr_siul2, mscr, mscr_sss, imcr, imcr_sss) (S32K3): selector = named values
  {mscr, mscr_sss} for outputs and {mscr, imcr, imcr_sss} for inputs (Zephyr binding nxp-siul2-pinctrl.h);
  the _O/_I entries of one signal on one pin are merged. SIUL2 instance numbers are added as mscr_siul2/imcr_siul2
  only when not 0.
Delta headers (#include of a base part and #undef of absent signals) are resolved. Entries without a pin name
(internal TRGMUX routes) or whose macro name does not match their arguments (pin port/number, MSCR index) are not
guessed: they are left out and listed in the header of the generated file.

    python mcudb/tools/pinmux_hal_nxp.py           # (re)generate
    python mcudb/tools/pinmux_hal_nxp.py --check   # exit 1 if a generated file is missing or outdated
"""

import argparse
import re
import sys
from pathlib import Path

MCUDB = Path(__file__).resolve().parents[1]

KINETIS_RE = re.compile(r"^#define\s+(\w+)\s+KINETIS_MUX\('([A-Z])',\s*(\d+),\s*(\d+)\)", re.M)
SIUL2_RE = re.compile(r"^#define\s+(\w+)\s+NXP_SIUL2_PINMUX\(\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+)\)",
                      re.M)
UNDEF_RE = re.compile(r"^#undef\s+(\w+)", re.M)
INCLUDE_RE = re.compile(r'^#include\s+"([^"]+)"', re.M)
PART_RE = re.compile(r"for\s+(\w+)/signal_configuration\.xml")
LICENSE_RE = re.compile(r"(Copyright [^\n*]+)(?:\s*\*)*\s*(SPDX-License-Identifier: [^\n*]+)")
KINETIS_NAME_RE = re.compile(r"^(?:(\w+)_)?(PT([A-Z])(\d+))$")
SIUL2_NAME_RE = re.compile(r"^(PT([A-Z])(\d+))_(\w+?)(?:_([IO]))?$")
RENAME = ((re.compile(r"^CAN(\d+)_"), r"FlexCAN\1_"), (re.compile(r"^FXIO_"), "FlexIO_"),
          (re.compile(r"^EMIOS_(\d+)_CH(\d+)_[A-Z]$"), r"EMIOS\1_CH\2"))
SELECTOR = {"kinetis": "PORT_PCR.MUX", "siul2": "SIUL2_MSCR/IMCR"}
KEY_ORDER = ("mscr_siul2", "mscr", "mscr_sss", "imcr_siul2", "imcr", "imcr_sss")


class SourceError(Exception):
    pass


def _statements(file: Path, seen=()):
    """Yields ('define', format, name, args) and ('undef', name) in preprocessing order, includes resolved."""
    if file in seen:
        raise SourceError(f"{file.name}: recursive include")
    if not file.is_file():
        raise SourceError(f"{seen[-1].name if seen else file.name}: included file {file.name} not found")
    text = file.read_text(encoding="utf-8")
    events = [(m.start(), "include", m) for m in INCLUDE_RE.finditer(text)]
    events += [(m.start(), "kinetis", m) for m in KINETIS_RE.finditer(text)]
    events += [(m.start(), "siul2", m) for m in SIUL2_RE.finditer(text)]
    events += [(m.start(), "undef", m) for m in UNDEF_RE.finditer(text)]
    for _, kind, m in sorted(events, key=lambda e: e[0]):
        if kind == "include":
            yield from _statements(file.parent / m.group(1), seen + (file,))
        elif kind == "undef":
            yield "undef", m.group(1)
        elif kind == "kinetis":
            yield "define", kind, m.group(1), (m.group(2), int(m.group(3)), int(m.group(4)))
        else:
            yield "define", kind, m.group(1), tuple(int(g) for g in m.groups()[1:])


def _rename(signal: str) -> str:
    for pattern, repl in RENAME:
        signal = pattern.sub(repl, signal)
    return signal


def _kinetis(name: str, args) -> tuple[str, str, int] | str:
    """(signal, pin, selector) or the reason why the entry is inconsistent."""
    port, number, mux = args
    m = KINETIS_NAME_RE.match(name)
    if not m or m.group(3) != port or int(m.group(4)) != number:
        return f"{name} does not match KINETIS_MUX('{port}',{number},{mux})"
    return _rename(m.group(1) or "GPIO"), m.group(2), mux


def _siul2(name: str, args) -> tuple[str, str, dict[str, int]] | str:
    mscr_siul2, imcr_siul2, mscr, mscr_sss, imcr, imcr_sss = args
    m = SIUL2_NAME_RE.match(name)
    call = f"NXP_SIUL2_PINMUX({', '.join(map(str, args))})"
    if not m:
        return f"{name}: no pin name in {call}"
    expected = (ord(m.group(2)) - ord("A")) * 32 + int(m.group(3))
    if mscr != expected:
        return f"{name}: MSCR index {mscr} in {call}, expected {expected} for {m.group(1)}"
    output, inp = mscr_sss != 0, imcr != 0 or imcr_sss != 0
    if m.group(5) == "O" and inp or m.group(5) == "I" and output or not (output or inp):
        return f"{name}: direction does not match {call}"
    selector = {"mscr": mscr}
    if output:
        selector["mscr_sss"] = mscr_sss
    if inp:
        selector.update(imcr=imcr, imcr_sss=imcr_sss)
    if mscr_siul2:
        selector["mscr_siul2"] = mscr_siul2
    if imcr_siul2:
        selector["imcr_siul2"] = imcr_siul2
    return _rename(m.group(4)), m.group(1), selector


def parse(file: Path) -> tuple[str, dict, list[str]]:
    """(format, signal -> pin -> selector, excluded entries) of one hal_nxp header."""
    macros: dict[str, tuple] = {}
    formats = set()
    for st in _statements(file):
        if st[0] == "undef":
            macros.pop(st[1], None)
        else:
            macros[st[2]] = (st[1], st[3])
    excluded, table = [], {}
    for name, (fmt, args) in macros.items():
        formats.add(fmt)
        entry = (_kinetis if fmt == "kinetis" else _siul2)(name, args)
        if isinstance(entry, str):
            excluded.append(entry)
            continue
        signal, pin, selector = entry
        pins = table.setdefault(signal, {})
        if pin not in pins:
            pins[pin] = selector
        elif isinstance(selector, dict) and isinstance(pins[pin], dict) and \
                all(pins[pin].get(k, v) == v for k, v in selector.items()):
            pins[pin] = {**pins[pin], **selector}
        elif pins[pin] != selector:
            raise SourceError(f"{file.name}: {signal} on {pin} with selectors {pins[pin]} and {selector}")
    if len(formats) != 1:
        raise SourceError(f"{file.name}: expected one macro format, found {sorted(formats) or 'none'}")
    return formats.pop(), table, sorted(excluded)


def _natural(text: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", text)]


def _selector(value) -> str:
    if isinstance(value, int):
        return str(value)
    return "{" + ", ".join(f"{k}: {value[k]}" for k in sorted(value, key=KEY_ORDER.index)) + "}"


def render(file: Path) -> tuple[str, str]:
    """(part, YAML text) for one hal_nxp header; the part is the file name without -pinctrl.h."""
    part = file.name.removesuffix("-pinctrl.h")
    text = file.read_text(encoding="utf-8")
    named = PART_RE.search(text)
    if named and named.group(1) != part:
        raise SourceError(f"{file.name}: header describes {named.group(1)}")
    lic = LICENSE_RE.search(text)
    if not lic:
        raise SourceError(f"{file.name}: licence notice not found")
    fmt, table, excluded = parse(file)
    source = file.relative_to(MCUDB).as_posix()
    lines = [
        f"# Pin-Multiplexing {part} (Derivat-Datenbank, nur lesen). Schema: tools/nxconfig/model.py (PinmuxTable).",
        f"# Erzeugt von mcudb/tools/pinmux_hal_nxp.py aus mcudb/{source} - nicht von Hand ändern.",
        "# Quelle: Zephyr hal_nxp (dts/nxp/s32/pinctrl), Herkunft und Stand: README.md im Quellordner,",
        f"# {lic.group(1).strip()}, {lic.group(2).strip()}",
    ]
    if excluded:
        lines.append(f"# Nicht übernommen ({len(excluded)} Einträge der Quelle ohne Pin oder mit widersprüchlichen Werten):")
        lines += [f"#   {e}" for e in excluded]
    lines += [
        "schema_version: 1",
        f"part: {part}",
        f"selector: {SELECTOR[fmt]}",
        f"source: {source}",
        "signals:",
    ]
    for signal in sorted(table, key=lambda s: (s != "GPIO", _natural(s))):
        pins = sorted(table[signal].items(), key=lambda kv: _natural(kv[0]))
        entries = [f"{pin}: {_selector(sel)}" for pin, sel in pins]
        if len(entries) <= 8 and fmt == "kinetis":
            lines.append(f"  {signal}: {{{', '.join(entries)}}}")
        else:
            lines.append(f"  {signal}:")
            lines += [f"    {e}" for e in entries]
    return part, "\n".join(lines) + "\n"


def sources() -> list[Path]:
    return sorted(MCUDB.glob("*/sources/hal_nxp/*-pinctrl.h"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="only compare, do not write")
    args = parser.parse_args(argv)
    stale = []
    for src in sources():
        part, text = render(src)
        target = src.parents[2] / f"{part}.pinmux.yaml"
        current = target.read_text(encoding="utf-8") if target.exists() else None
        if current == text:
            continue
        if args.check:
            stale.append(target.relative_to(MCUDB).as_posix())
        else:
            target.write_text(text, encoding="utf-8", newline="\n")
            print(f"written {target.relative_to(MCUDB).as_posix()} ({text.count(chr(10))} lines)")
    if stale:
        print(f"outdated: {', '.join(stale)} (run mcudb/tools/pinmux_hal_nxp.py)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
