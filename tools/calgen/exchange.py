# SPDX-License-Identifier: Apache-2.0
"""Exchange formats of a calibration dataset: DCM and CDFX (TOOL-145, stage K2).

The dataset in Git (caldata.py, format nextbsw-cal 1) is the store; these two formats are only the edge to
calibration tools. Both writers and readers here work on a neutral list of CalObject: one object per calibration
parameter as the A2L names it (<Component>.<Port>.<Element>), own breakpoints inside their curve or map. Mapping to and
from the dataset (element names, own axes, completeness) is tools/calgen/calgen.py.

Kept minimal on purpose - the objects neXtBSW has (value, array, curve, map, shared axis, value table) and nothing
else. Described in our own words:

DCM (text, the de-facto exchange format of calibration tools; version 2.0 is written)
    First statement `KONSERVIERUNG_FORMAT 2.0`. A line starting with `*` is a comment. Each object is a block from a
    keyword line to `END`:
      FESTWERT <name>                     scalar                  WERT <v>  or  TEXT "<text>"
      FESTWERTEBLOCK <name> <n>           array of n              WERT <v> ...
      KENNLINIE <name> <nx>               curve, own breakpoints  ST/X <x> ...   WERT <v> ...
      GRUPPENKENNLINIE <name> <nx>        curve on a shared axis  as KENNLINIE, the axis named in `*SSTX <axis>`
      KENNFELD <name> <nx> <ny>           map                     ST/X <x> ...  then per Y breakpoint:
                                                                  ST/Y <y>  and  WERT <nx values over X>
      GRUPPENKENNFELD <name> <nx> <ny>    map with shared axes    as KENNFELD, `*SSTX` / `*SSTY` name the axes
      STUETZSTELLENVERTEILUNG <name> <n>  shared axis             ST/X <x> ...
    Inside a block: LANGNAME "<text>", EINHEIT_W / EINHEIT_X / EINHEIT_Y "<unit>". A keyword may repeat over several
    lines; the numbers are then read one after the other. Values are physical. DCM has no place for status, author
    or reason: the writer puts them into a comment, the reader ignores it (an imported changed value is draft).

CDFX (XML, ASAM calibration data format 2.0)
    MSRSW / SW-SYSTEMS / SW-SYSTEM / SW-INSTANCE-SPEC / SW-INSTANCE-TREE holds one SW-INSTANCE per object:
    SHORT-NAME, LONG-NAME, CATEGORY (VALUE, VAL_BLK, CURVE, MAP, COM_AXIS), SW-FEATURE-REF (the component),
    SW-VALUE-CONT with UNIT-DISPLAY-NAME, SW-ARRAYSIZE and SW-VALUES-PHYS (V for a number, VT for a text of the
    value table, VG to group: a map has one VG per X breakpoint holding the values over Y), SW-AXIS-CONTS with one
    SW-AXIS-CONT per axis (STD_AXIS with its values, or COM_AXIS with SW-INSTANCE-REF to the shared axis), and
    SW-CS-HISTORY / CS-ENTRY with STATE, DATE, CSUS (who) and REMARK (why). The status maps onto STATE as
    draft -> prelimcalibrated, checked -> checked, released -> completed; the reader accepts these and our own words.

Neither format has been checked against a third-party tool yet (no CANape/INCA here); the round trip through both is
tested (tools/calgen/test_calgen.py).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

import caldata


class ExchangeError(Exception):
    """A DCM or CDFX file that cannot be read; the text names file and line (DCM) or object (CDFX)."""


@dataclass
class AxisPart:
    """One axis of a curve or map: own breakpoints (element <Name>_X/_Y) or a shared axis (its exchange name)."""
    element: str
    shared: str = ""                  # exchange name of the shared axis object; "" for own breakpoints
    unit: str = ""
    values: list = field(default_factory=list)


@dataclass
class CalObject:
    """One calibration object as the exchange formats see it (physical values, flat, a map row by row = per X)."""
    name: str                         # A2L name <Component>.<Port>.<Element>
    component: str
    element: str
    kind: str                         # value | array | curve | map | axis
    values: list = field(default_factory=list)
    shape: tuple = ()                 # () scalar, (n,), (nx, ny)
    unit: str = ""
    description: str = ""
    value_table: dict = field(default_factory=dict)   # stored value -> text
    axes: list = field(default_factory=list)          # AxisPart, X first
    status: str = "draft"
    author: str = ""
    reason: str = ""


@dataclass
class Imported:
    """One object read from a DCM or CDFX file, values already in our order (a map row by row per X)."""
    name: str
    kind: str
    values: list
    shape: tuple = ()
    axes: list = field(default_factory=list)          # per axis: list of breakpoints, or None (a reference)
    axis_refs: list = field(default_factory=list)     # per axis: name of a shared axis or ""
    status: str | None = None
    author: str | None = None
    reason: str | None = None
    where: str = ""


# ---- DCM ---------------------------------------------------------------------------------------------------------

DCM_PER_LINE = 6
DCM_KINDS = {"FESTWERT": "value", "FESTWERTEBLOCK": "array", "KENNLINIE": "curve", "GRUPPENKENNLINIE": "curve",
             "FESTKENNLINIE": "curve", "KENNFELD": "map", "GRUPPENKENNFELD": "map", "FESTKENNFELD": "map",
             "STUETZSTELLENVERTEILUNG": "axis"}
DCM_SKIPPED_BLOCKS = ("FUNKTIONEN", "VARIANTENKODIERUNG")
TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|(\S+)')


def _dcm_text(text: str) -> str:
    return '"' + text.replace('"', "'") + '"'


def _dcm_values(keyword: str, values: list, table: dict) -> list[str]:
    """Lines of one keyword with at most DCM_PER_LINE values each (TEXT for a value of the value table)."""
    texts = [table.get(int(v)) if table and isinstance(v, (int, float)) and float(v).is_integer() else None
             for v in values]
    if table and all(t is not None for t in texts):
        return [f"   TEXT {' '.join(_dcm_text(t) for t in texts[i:i + DCM_PER_LINE])}"
                for i in range(0, len(texts), DCM_PER_LINE)]
    return [f"   {keyword} {'   '.join(caldata.format_number(v) for v in values[i:i + DCM_PER_LINE])}"
            for i in range(0, len(values), DCM_PER_LINE)]


def write_dcm(objects: list[CalObject], header: list[str]) -> str:
    out = ["* DCM written by neXtBSW tools/calgen (TOOL-145)", *[f"* {h}" for h in header], "",
           "KONSERVIERUNG_FORMAT 2.0", ""]
    for o in objects:
        meta = "; ".join(x for x in (f"status {o.status}", o.author and f"author {o.author}",
                                     o.reason and f"reason {o.reason}") if x)
        out.append(f"* {meta}")
        common = [f"   LANGNAME {_dcm_text(o.description or o.name)}"]
        if o.kind == "axis":
            out += [f"STUETZSTELLENVERTEILUNG {o.name} {len(o.values)}", "*SST", *common,
                    f"   EINHEIT_X {_dcm_text(o.unit)}", *_dcm_values("ST/X", o.values, {}), "END", ""]
            continue
        if o.kind in ("value", "array"):
            head = f"FESTWERT {o.name}" if not o.shape else f"FESTWERTEBLOCK {o.name} {len(o.values)}"
            out += [head, *common, f"   EINHEIT_W {_dcm_text(o.unit)}", *_dcm_values("WERT", o.values, o.value_table),
                    "END", ""]
            continue
        group = any(a.shared for a in o.axes)
        refs = [f"*SST{letter} {a.shared}" for letter, a in zip("XY", o.axes) if a.shared]
        units = [f"   EINHEIT_{letter} {_dcm_text(a.unit)}" for letter, a in zip("XY", o.axes)]
        if o.kind == "curve":
            out += [f"{'GRUPPENKENNLINIE' if group else 'KENNLINIE'} {o.name} {len(o.values)}", *refs, *common, *units,
                    f"   EINHEIT_W {_dcm_text(o.unit)}", *_dcm_values("ST/X", o.axes[0].values, {}),
                    *_dcm_values("WERT", o.values, {}), "END", ""]
            continue
        nx, ny = o.shape
        out += [f"{'GRUPPENKENNFELD' if group else 'KENNFELD'} {o.name} {nx} {ny}", *refs, *common, *units,
                f"   EINHEIT_W {_dcm_text(o.unit)}", *_dcm_values("ST/X", o.axes[0].values, {})]
        for j in range(ny):
            out.append(f"   ST/Y {caldata.format_number(o.axes[1].values[j])}")
            out += _dcm_values("WERT", [o.values[i * ny + j] for i in range(nx)], {})
        out += ["END", ""]
    return "\n".join(out)


def _number(token: str, where: str):
    try:
        value = float(token)
    except ValueError:
        raise ExchangeError(f"{where}: '{token}' is no number") from None
    return int(value) if value.is_integer() and "." not in token and "e" not in token.lower() else value


def read_dcm(text: str, source: str = "DCM") -> tuple[list[Imported], list[str]]:
    """Objects of a DCM file and warnings (skipped blocks); raises ExchangeError for what it cannot read."""
    objects, warnings = [], []
    lines = text.splitlines()
    i, seen_format = 0, False
    while i < len(lines):
        number, line = i + 1, lines[i].strip()
        i += 1
        if not line or line.startswith("*"):
            continue
        words = line.split()
        keyword = words[0]
        where = f"{source}:{number}"
        if keyword == "KONSERVIERUNG_FORMAT":
            if len(words) < 2 or words[1] != "2.0":
                raise ExchangeError(f"{where}: only KONSERVIERUNG_FORMAT 2.0 is read")
            seen_format = True
            continue
        if keyword == "MODULKOPF":
            continue
        if keyword in DCM_SKIPPED_BLOCKS:
            warnings.append(f"{where}: block {keyword} skipped")
            while i < len(lines) and lines[i].strip() != "END":
                i += 1
            i += 1
            continue
        if keyword not in DCM_KINDS:
            raise ExchangeError(f"{where}: '{keyword}' is not understood (read are "
                                f"{', '.join(DCM_KINDS)})")
        if not seen_format:
            raise ExchangeError(f"{where}: KONSERVIERUNG_FORMAT 2.0 must come first")
        if len(words) < 2:
            raise ExchangeError(f"{where}: {keyword} without a name")
        if "@" in words:
            raise ExchangeError(f"{where}: two-dimensional {keyword} is not supported")
        name, dims = words[1], [_number(w, where) for w in words[2:]]
        body: dict[str, list] = {}
        y_rows: list[tuple] = []
        refs = {}
        while True:
            if i >= len(lines):
                raise ExchangeError(f"{where}: {keyword} {name} has no END")
            raw = lines[i].strip()
            i += 1
            if raw == "END":
                break
            if raw.startswith("*SSTX") or raw.startswith("*SSTY"):
                parts = raw.split()
                refs[raw[4]] = parts[1] if len(parts) > 1 else ""
                continue
            if not raw or raw.startswith("*"):
                continue
            tokens = [m.group(1) if m.group(1) is not None else m.group(2) for m in TOKEN.finditer(raw)]
            key, args = tokens[0], tokens[1:]
            here = f"{source}:{i}"
            if key in ("LANGNAME", "DISPLAYNAME", "FUNKTION", "VAR"):
                continue
            if key.startswith("EINHEIT_"):
                continue
            if key == "TEXT":
                values = [("text", a) for a in args]
            elif key in ("WERT", "ST/X", "ST/Y"):
                values = [_number(a, here) for a in args]
            else:
                raise ExchangeError(f"{here}: '{key}' is not understood in {keyword}")
            if key == "ST/Y":
                y_rows.append((values, []))
                continue
            if key in ("WERT", "TEXT") and y_rows:
                y_rows[-1][1].extend(values)
                continue
            body.setdefault("WERT" if key == "TEXT" else key, []).extend(values)
        kind = DCM_KINDS[keyword]
        obj = Imported(name, kind, [], where=where)
        values = [v[1] if isinstance(v, tuple) else v for v in body.get("WERT", [])]
        if kind == "value":
            if len(values) != 1:
                raise ExchangeError(f"{where}: FESTWERT {name} needs one value, has {len(values)}")
            obj.values = values
        elif kind == "array":
            if dims and dims[0] != len(values):
                raise ExchangeError(f"{where}: {keyword} {name} announces {dims[0]} values, has {len(values)}")
            obj.values, obj.shape = values, (len(values),)
        elif kind == "axis":
            obj.values = body.get("ST/X", [])
            obj.shape = (len(obj.values),)
        elif kind == "curve":
            xs = body.get("ST/X", [])
            if len(xs) != len(values) or (dims and dims[0] != len(values)):
                raise ExchangeError(f"{where}: {keyword} {name}: {len(xs)} breakpoints, {len(values)} values")
            obj.values, obj.shape = values, (len(values),)
            obj.axes, obj.axis_refs = [xs], [refs.get("X", "")]
        else:
            xs = body.get("ST/X", [])
            nx, ny = len(xs), len(y_rows)
            if dims and tuple(dims[:2]) != (nx, ny):
                raise ExchangeError(f"{where}: {keyword} {name} announces {dims[0]} x {dims[1]}, has {nx} x {ny}")
            if any(len(ys) != 1 or len(vs) != nx for ys, vs in y_rows):
                raise ExchangeError(f"{where}: {keyword} {name}: every ST/Y needs one breakpoint and {nx} values")
            columns = [[v[1] if isinstance(v, tuple) else v for v in vs] for _ys, vs in y_rows]
            obj.values = [columns[j][i] for i in range(nx) for j in range(ny)]
            obj.shape = (nx, ny)
            obj.axes = [xs, [ys[0] for ys, _vs in y_rows]]
            obj.axis_refs = [refs.get("X", ""), refs.get("Y", "")]
        objects.append(obj)
    if not seen_format:
        raise ExchangeError(f"{source}: no KONSERVIERUNG_FORMAT 2.0 - not a DCM file")
    return objects, warnings


# ---- CDFX --------------------------------------------------------------------------------------------------------

CDFX_DOCTYPE = ('<!DOCTYPE MSRSW PUBLIC "-//ASAM//DTD CALIBRATION DATA FORMAT:V2.0.0:LAI:IAI:XML:CDF200.XSD//EN" '
                '"cdf_v2.0.0.sl.dtd">')
CDFX_CATEGORY = {"value": "VALUE", "array": "VAL_BLK", "curve": "CURVE", "map": "MAP", "axis": "COM_AXIS"}
CDFX_KINDS = {v: k for k, v in CDFX_CATEGORY.items()} | {"BOOLEAN": "value", "CURVE_AXIS": "axis",
                                                         "RES_AXIS": "axis"}
STATE_OUT = {"draft": "prelimcalibrated", "checked": "checked", "released": "completed"}
STATE_IN = {"notedited": "draft", "prelimcalibrated": "draft", "calibrated": "draft", "checked": "checked",
            "completed": "released", **{s: s for s in caldata.STATUSES}}


def _v(value, table: dict) -> str:
    text = table.get(int(value)) if table and isinstance(value, (int, float)) and float(value).is_integer() else None
    return f"<VT>{escape(text)}</VT>" if text is not None else f"<V>{caldata.format_number(value)}</V>"


def write_cdfx(objects: list[CalObject], system: str, tree: str, date: str) -> str:
    out = ['<?xml version="1.0" encoding="UTF-8"?>', CDFX_DOCTYPE, "<MSRSW>",
           f"  <SHORT-NAME>{escape(tree)}</SHORT-NAME>", "  <CATEGORY>CDF20</CATEGORY>", "  <SW-SYSTEMS>",
           "    <SW-SYSTEM>", f"      <SHORT-NAME>{escape(system)}</SHORT-NAME>", "      <SW-INSTANCE-SPEC>",
           "        <SW-INSTANCE-TREE>", f"          <SHORT-NAME>{escape(tree)}</SHORT-NAME>",
           "          <CATEGORY>NO_VCD</CATEGORY>"]
    ind = " " * 10
    for o in objects:
        out += [f"{ind}<SW-INSTANCE>", f"{ind}  <SHORT-NAME>{escape(o.name)}</SHORT-NAME>"]
        if o.description:
            out.append(f"{ind}  <LONG-NAME>{escape(o.description)}</LONG-NAME>")
        out += [f"{ind}  <CATEGORY>{CDFX_CATEGORY[o.kind]}</CATEGORY>",
                f"{ind}  <SW-FEATURE-REF>{escape(o.component)}</SW-FEATURE-REF>", f"{ind}  <SW-VALUE-CONT>"]
        if o.unit:
            out.append(f"{ind}    <UNIT-DISPLAY-NAME>{escape(o.unit)}</UNIT-DISPLAY-NAME>")
        if o.shape:
            out.append(f"{ind}    <SW-ARRAYSIZE>{''.join(f'<V>{n}</V>' for n in o.shape)}</SW-ARRAYSIZE>")
        out.append(f"{ind}    <SW-VALUES-PHYS>")
        if o.kind == "map":
            nx, ny = o.shape
            for i in range(nx):
                row = "".join(_v(v, o.value_table) for v in o.values[i * ny:(i + 1) * ny])
                out.append(f"{ind}      <VG>{row}</VG>")
        else:
            out.append(f"{ind}      {''.join(_v(v, o.value_table) for v in o.values)}")
        out += [f"{ind}    </SW-VALUES-PHYS>", f"{ind}  </SW-VALUE-CONT>"]
        if o.axes:
            out.append(f"{ind}  <SW-AXIS-CONTS>")
            for a in o.axes:
                out.append(f"{ind}    <SW-AXIS-CONT>")
                if a.shared:
                    out += [f"{ind}      <CATEGORY>COM_AXIS</CATEGORY>",
                            f"{ind}      <SW-INSTANCE-REF>{escape(a.shared)}</SW-INSTANCE-REF>"]
                else:
                    out.append(f"{ind}      <CATEGORY>STD_AXIS</CATEGORY>")
                    if a.unit:
                        out.append(f"{ind}      <UNIT-DISPLAY-NAME>{escape(a.unit)}</UNIT-DISPLAY-NAME>")
                    out += [f"{ind}      <SW-ARRAYSIZE><V>{len(a.values)}</V></SW-ARRAYSIZE>",
                            f"{ind}      <SW-VALUES-PHYS>{''.join(_v(v, {}) for v in a.values)}</SW-VALUES-PHYS>"]
                out.append(f"{ind}    </SW-AXIS-CONT>")
            out.append(f"{ind}  </SW-AXIS-CONTS>")
        out += [f"{ind}  <SW-CS-HISTORY>", f"{ind}    <CS-ENTRY>",
                f"{ind}      <STATE>{STATE_OUT[o.status]}</STATE>", f"{ind}      <DATE>{escape(date)}</DATE>"]
        if o.author:
            out.append(f"{ind}      <CSUS>{escape(o.author)}</CSUS>")
        if o.reason:
            out.append(f"{ind}      <REMARK>{escape(o.reason)}</REMARK>")
        out += [f"{ind}    </CS-ENTRY>", f"{ind}  </SW-CS-HISTORY>", f"{ind}</SW-INSTANCE>"]
    out += ["        </SW-INSTANCE-TREE>", "      </SW-INSTANCE-SPEC>", "    </SW-SYSTEM>", "  </SW-SYSTEMS>",
            "</MSRSW>", ""]
    return "\n".join(out)


def _phys(parent, where: str) -> list:
    """Values under SW-VALUES-PHYS in document order (V as number, VT as ('text', t)); VG groups are flattened."""
    values = []
    node = parent.find("SW-VALUES-PHYS") if parent is not None else None
    if node is None:
        return values
    for el in node.iter():
        if el.tag == "V":
            values.append(_number((el.text or "").strip(), where))
        elif el.tag == "VT":
            values.append(("text", el.text or ""))
    return values


def read_cdfx(text: str, source: str = "CDFX") -> tuple[list[Imported], list[str]]:
    try:
        root = ET.fromstring(text.encode("utf-8"))
    except ET.ParseError as e:
        raise ExchangeError(f"{source}: no valid XML: {e}") from None
    if root.tag != "MSRSW":
        raise ExchangeError(f"{source}: root element is {root.tag}, a CDFX file has MSRSW")
    objects, warnings = [], []
    for inst in root.iter("SW-INSTANCE"):
        name = (inst.findtext("SHORT-NAME") or "").strip()
        where = f"{source}: {name or 'SW-INSTANCE without SHORT-NAME'}"
        category = (inst.findtext("CATEGORY") or "").strip()
        if category not in CDFX_KINDS:
            warnings.append(f"{where}: category {category or '-'} skipped")
            continue
        kind = CDFX_KINDS[category]
        cont = inst.find("SW-VALUE-CONT")
        values = [v[1] if isinstance(v, tuple) else v for v in _phys(cont, where)]
        axes, refs = [], []
        for ax in inst.findall("SW-AXIS-CONTS/SW-AXIS-CONT"):
            ref = (ax.findtext("SW-INSTANCE-REF") or "").strip()
            refs.append(ref)
            axes.append(None if ref else _phys(ax, where))
        if kind == "axis" and not values and axes and axes[0]:
            values, axes, refs = axes[0], [], []           # breakpoints given as the axis of the instance
        obj = Imported(name, kind, values, where=where, axes=axes, axis_refs=refs)
        sizes = [int(_number((v.text or "").strip(), where)) for v in inst.findall("SW-VALUE-CONT/SW-ARRAYSIZE/V")]
        if kind == "map":
            groups = cont.findall("SW-VALUES-PHYS/VG") if cont is not None else []
            nx = len(groups) or (sizes[0] if sizes else 0)
            ny = len(values) // nx if nx else 0
            if not nx or nx * ny != len(values) or len(axes) != 2:
                raise ExchangeError(f"{where}: a MAP needs one VG per X breakpoint and two axes")
            obj.shape = (nx, ny)
        elif kind in ("array", "curve", "axis"):
            obj.shape = (len(values),)
        elif len(values) != 1:
            raise ExchangeError(f"{where}: a VALUE has one value, not {len(values)}")
        entries = inst.findall("SW-CS-HISTORY/CS-ENTRY")
        if entries:
            last = entries[-1]
            state = (last.findtext("STATE") or "").strip()
            if state and state not in STATE_IN:
                warnings.append(f"{where}: state {state} unknown, taken as draft")
            obj.status = STATE_IN.get(state, "draft") if state else None
            obj.author = (last.findtext("CSUS") or "").strip() or None
            obj.reason = (last.findtext("REMARK") or "").strip() or None
        objects.append(obj)
    return objects, warnings
