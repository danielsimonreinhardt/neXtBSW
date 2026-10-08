"""Generates the package pinouts of S32K1xx derivatives from the reference manual attachments (TOOL-162).

Source: S32K1xx Series Reference Manual, Rev. 13, 04/2020 (public document of NXP), its attached spreadsheets
`<derivative>_IO_Signal_Description_Input_Multiplexing.xlsx`: sheet "Pinout" (pad -> pin number/ball per package),
sheet "Supplies" (VDD, VDDA, VREFH, VREFL, VSS -> pin numbers/balls per package) and one drawing sheet per package
(cross-check: the name printed next to every pin number must match). The zephyr pin tables (hal_nxp) have no pin
numbers, so the reference manual is the only public source of the geometry.

The manual is not part of the repository (the licence of NXP documents does not allow redistribution): every
workstation keeps it in doc/hardware/s32k1xx/S32K-RM.pdf (or in the file named by NX_S32K1_RM). Only the facts read
from it (which terminal carries which pad or supply) go into mcudb, with the source named in every file. The
spreadsheets are read straight out of the PDF (embedded files, zip streams) with the standard library only; the
revision is identified by SHA-256, a different file is an error because the files cite exactly this revision.

Output: mcudb/s32k1xx/pinout/<part>.yaml for every pin table <part>.pinmux.yaml of the family
(format: mcudb/tools/pinout_common.py).

    python mcudb/tools/pinout_nxp_rm.py           # (re)generate
    python mcudb/tools/pinout_nxp_rm.py --check   # exit 1 if a generated file is missing or outdated
                                                  # (exit 3: reference manual not on this workstation)
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pinout_common as pc  # noqa: E402

REPO = pc.MCUDB.parent
FAMILY = pc.MCUDB / "s32k1xx"
DEFAULT_RM = REPO / "doc" / "hardware" / "s32k1xx" / "S32K-RM.pdf"
RM_CITE = "S32K1xx Series Reference Manual Rev. 13 (04/2020)"
RM_SHA256 = "4339261ebd1e69bc142eab0f905fc4f03bdee94df5066bf199056d3e6c518c85"
TITLE = re.compile(r"^(S32K\w+) IO_Signal_Description_Input_Multiplexing$")
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}


class Missing(Exception):
    """The reference manual is not on this workstation."""


def rm_file() -> Path:
    return Path(os.environ.get("NX_S32K1_RM") or DEFAULT_RM)


# ------------------------------------------------------------------------------------------------ reading the PDF

def attachments(pdf: bytes) -> list[bytes]:
    """Every zip stream (embedded xlsx) of the PDF, decompressed."""
    out = []
    for m in re.finditer(rb"stream\r?\n", pdf):
        start = m.end()
        if pdf[m.start() - 3:m.start()] == b"end":
            continue
        try:
            head = zlib.decompressobj().decompress(pdf[start:start + 4096], 8)
        except zlib.error:
            continue
        if head[:4] != b"PK\x03\x04":
            continue
        end = pdf.find(b"endstream", start)
        out.append(zlib.decompressobj().decompress(pdf[start:end]))
    return out


def read_book(data: bytes) -> dict[str, list[tuple[int, dict[str, str]]]]:
    """sheet name -> [(row number, {column letter: text})] of an xlsx file."""
    z = zipfile.ZipFile(io.BytesIO(data))
    strings = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS):
            strings.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
    book = ET.fromstring(z.read("xl/workbook.xml"))
    rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
    out = {}
    for sheet in book.find("m:sheets", NS):
        target = rels[sheet.get(f"{{{NS['r']}}}id")].lstrip("/")
        xml = ET.fromstring(z.read(target if target.startswith("xl/") else "xl/" + target))
        rows = []
        for row in xml.iter(f"{{{NS['m']}}}row"):
            cells = {}
            for c in row.findall("m:c", NS):
                column = re.match("[A-Z]+", c.get("r")).group()
                v = c.find("m:v", NS)
                if v is not None:
                    text = strings[int(v.text)] if c.get("t") == "s" else (v.text or "")
                else:
                    inline = c.find("m:is", NS)
                    text = "".join(t.text or "" for t in inline.iter(f"{{{NS['m']}}}t")) if inline is not None else ""
                cells[column] = text.strip()
            rows.append((int(row.get("r")), cells))
        out[sheet.get("name")] = rows
    return out


def books(pdf_file: Path) -> dict[str, dict]:
    """derivative -> workbook of its IO signal description."""
    if not pdf_file.is_file():
        raise Missing(f"{pdf_file} not found (reference manual on workstation level, see the module doc)")
    data = pdf_file.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != RM_SHA256:
        raise pc.SourceError(f"{pdf_file.name}: SHA-256 {digest} is not {RM_CITE} ({RM_SHA256})")
    out = {}
    for blob in attachments(data):
        book = read_book(blob)
        title = next((cells.get("A", "") for _, cells in book.get("Title", [])), "")
        m = TITLE.match(title)
        if m:
            out[m.group(1)] = book
    return out


# ------------------------------------------------------------------------------------------------ the tables

def package_token(text: str, derivative: str = "") -> str:
    """'S32K144_64 LQFP' / 'S32K144_64lqfp' / 'LQFP64' -> '64lqfp'; any BGA -> 'bga'; 'QFN32' -> '32qfn'."""
    t = text.replace(derivative + "_", "").replace(" ", "").lower()
    if "bga" in t:
        return "bga"
    m = re.fullmatch(r"(?:(\d+)([a-z]+)|([a-z]+)(\d+))", t)
    if not m:
        raise pc.SourceError(f"package name {text!r}")
    return f"{m.group(1)}{m.group(2)}" if m.group(1) else f"{m.group(4)}{m.group(3)}"


def _columns(header: dict[str, str], derivative: str) -> dict[str, str]:
    """package token -> column letter of a header row (columns that name a package of the derivative)."""
    out = {}
    for column, text in header.items():
        if text.startswith(derivative + "_"):
            out[package_token(text, derivative)] = column
    return out


def _terminal(value: str):
    return int(value) if value.isdigit() else value


def pads(book: dict, derivative: str, token: str) -> dict:
    """terminal -> pad or supply name from sheet Pinout."""
    rows = book["Pinout"]
    header = rows[0][1]
    column = _columns(header, derivative).get(token)
    name_column = next(c for c, t in header.items() if t == "Pin Name")
    if column is None:
        raise pc.SourceError(f"{derivative}: sheet Pinout has no column for {token}")
    out = {}
    for _, cells in rows[1:]:
        terminal, pad = cells.get(column, ""), cells.get(name_column, "")
        if not terminal or not pad:                 # last row: pin count without name, notes
            continue
        key = _terminal(terminal)
        if key in out:
            raise pc.SourceError(f"{derivative} {token}: terminal {terminal} twice in sheet Pinout")
        out[key] = pad
    return out


def supplies(book: dict, derivative: str, token: str) -> dict:
    """terminal -> supply name ("VDDA/VREFH" when the sheet names one terminal twice) from sheet Supplies."""
    rows = book["Supplies"]
    column = _columns(rows[0][1], derivative).get(token)
    if column is None:
        raise pc.SourceError(f"{derivative}: sheet Supplies has no column for {token}")
    out: dict = {}
    name = ""
    for _, cells in rows[1:]:
        name = cells.get("A") or name
        terminal = cells.get(column, "")
        if not terminal:
            continue
        key = _terminal(terminal)
        names = out.setdefault(key, [])
        if name not in names:
            names.append(name)
    return {k: "/".join(v) for k, v in out.items()}


def drawing(book: dict, derivative: str, token: str) -> dict:
    """terminal -> set of names printed next to it on the package drawing sheet (empty dict: no drawing)."""
    sheet = next((n for n in book if n.startswith(derivative + "_") and package_token(n, derivative) == token), None)
    if sheet is None:
        return {}
    grid = {(r, _col(c)): t for r, cells in book[sheet] for c, t in cells.items() if t}
    out: dict = {}
    if token == "bga":
        columns = {c: t for (r, c), t in grid.items() if t.isdigit() and r == min(r for r, _ in grid if r > 3)}
        rows = {r: t for (r, c), t in grid.items() if re.fullmatch(r"[A-Z]", t) and c == min(c for _, c in grid)}
        for (r, c), t in grid.items():
            if r in rows and c in columns:
                out.setdefault(f"{rows[r]}{columns[c]}", set()).add(t)
        return out
    for (r, c), t in grid.items():
        if not t.isdigit():
            continue
        for dr, dc in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            neighbour = grid.get((r + dr, c + dc))
            if neighbour and not neighbour.isdigit():
                out.setdefault(int(t), set()).add(neighbour)
    return out


def _col(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n


def pinout(book: dict, derivative: str, token: str) -> tuple[dict, dict, list[str]]:
    """(outline, pins, findings) of one package."""
    pins: dict = {}
    supply_names: dict = {}
    for terminal, name in pads(book, derivative, token).items():
        if pc.POWER_RE.match(name) or pc.GROUND_RE.match(name):     # sheet Pinout lists the supplies as well
            supply_names.setdefault(terminal, []).append(name)
        else:
            pins[terminal] = name
    for terminal, name in supplies(book, derivative, token).items():
        if terminal in pins:
            raise pc.SourceError(f"{derivative} {token}: terminal {terminal} is pad {pins[terminal]} and supply {name}")
        names = supply_names.setdefault(terminal, [])
        names += [n for n in name.split("/") if n not in names]     # sheet Supplies adds shared terminals (VDDA/VREFH)
    for terminal, names in supply_names.items():
        pins[terminal] = {"name": "/".join(names), "kind": pc.supply_kind("/".join(names))}
    findings = []
    drawn = drawing(book, derivative, token)
    for terminal, value in pins.items():
        name = value if isinstance(value, str) else value["name"]
        seen = drawn.get(terminal)
        if drawn and not (seen and any(part in " ".join(seen).replace("/", " ").split() for part in name.split("/"))):
            findings.append(f"{derivative} {token}: terminal {terminal} is {name} in Pinout/Supplies, "
                            f"the drawing shows {sorted(seen or ['nothing'])}")
    if token == "bga":
        outline = pc.grid(pins)
        # balls the tables do not name (not bonded / reserved) are shown as nc if the drawing names them
        for ball, names in drawn.items():
            if ball not in pins:
                label = "/".join(sorted(names))
                findings.append(f"{derivative} {token}: ball {ball} only on the drawing ({label}) - kind nc")
                pins[ball] = {"name": label, "kind": "nc"}
    else:
        count = int(re.match(r"\d+", token).group())
        outline = pc.quad(count)
    return outline, pins, findings


def tables() -> list[tuple[str, Path]]:
    """(derivative, pin table) of every pin table of the family, from the derivative files (pinmux: file:)."""
    from ruamel.yaml import YAML
    out = []
    for file in sorted(FAMILY.glob("*.yaml")):
        if file.name.endswith(".pinmux.yaml"):
            continue
        data = YAML(typ="safe").load(file.read_text(encoding="utf-8"))
        for ref in ((data or {}).get("pinmux") or {}).values():
            if isinstance(ref, dict) and ref.get("file"):
                out.append((file.stem, FAMILY / ref["file"]))
    return out


def targets(pdf_file: Path | None = None) -> tuple[dict[Path, str], list[str]]:
    found = books(pdf_file or rm_file())
    out: dict[Path, str] = {}
    findings: list[str] = []
    for derivative, table in tables():
        part = table.name[: -len(".pinmux.yaml")]
        token = package_token(part.split("_", 1)[1])
        book = found.get(derivative)
        if book is None:
            raise pc.SourceError(f"{RM_CITE}: no IO signal description of {derivative}")
        outline, pins, notes = pinout(book, derivative, token)
        findings += notes
        findings += pc.check_against_pinmux(part, pins, table)
        header = [
            f"Gehäuse-Pinbelegung {part} (Derivat-Datenbank, nur lesen). Format: mcudb/tools/pinout_common.py.",
            f"Erzeugt von mcudb/tools/pinout_nxp_rm.py aus {RM_CITE}, Anhang",
            f"{derivative}_IO_Signal_Description_Input_Multiplexing.xlsx (Blätter Pinout, Supplies; Gegenprobe am "
            "Gehäuse-Blatt) - nicht von Hand ändern.",
            "Das Handbuch liegt auf Arbeitsplatzebene (doc/hardware/s32k1xx/S32K-RM.pdf), nicht im Repository.",
        ] + [f"Befund: {n}" for n in notes]
        source = (f"[RM] {RM_CITE}, {derivative}_IO_Signal_Description_Input_Multiplexing.xlsx, "
                  f"sheets Pinout and Supplies, column {token}")
        out[pc.pinout_file(table)] = pc.render(part, outline, source, pins, header)
    return out, findings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="only compare, do not write")
    parser.add_argument("--rm", type=Path, help="reference manual PDF (default: NX_S32K1_RM or doc/hardware/...)")
    args = parser.parse_args(argv)
    try:
        texts, findings = targets(args.rm)
    except Missing as e:
        print(f"skipped: {e}", file=sys.stderr)
        return 3
    except pc.SourceError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    for f in findings:
        print(f"finding: {f}")
    return pc.write_or_check(texts, args.check)


if __name__ == "__main__":
    sys.exit(main())
