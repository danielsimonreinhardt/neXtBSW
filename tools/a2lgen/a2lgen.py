"""A2L of an application built with the neXtBSW RTE (TOOL-01): what an XCP tool needs to measure and calibrate.

Everything is taken from the build, nothing is typed twice:
  ELF (app.ecu.elf)        addresses, and the RTE tables rte::SIGNALS / rte::PARAMETERS themselves (name, element
                           type, count, unit, conversion) - read from the initialised data of the image, so the A2L
                           describes exactly the binary it was made from
  RteConfig.h (generated)  order of rte::SignalType (the numbers stored in the tables)
  xcpCanConfiguration.h    bus and CAN identifiers of XCP on CAN (the header the target is compiled with)
  canBitTimingConfiguration.h (generated)  bit rate of that bus
  project.yaml (optional)  checks: XCP identifiers against the diagnostic identifiers and the DBC of the project;
                           TOOL-150: also unit/min/max of ports and measurements (swc.yaml of every component of
                           the ECU description) - rte::SIGNALS has no room for engineering limits in the image
                           (unlike rte::PARAMETERS), so this is the one place they can come from; without
                           --project, or where a component's swc.yaml has none, MEASUREMENT keeps the type range
                           and, for a port, no PHYS_UNIT - byte for byte as before TOOL-150

  RteCalibration.json (generated, next to RteConfig.h)  descriptions and value tables of the calibration objects
                           (RTE-26), checked against the image

Output: MEASUREMENT for every RTE signal (read only; an array port as one MEASUREMENT with MATRIX_DIM, RTE-25; a
measurement of a component, TOOL-143, with its description and PHYS_UNIT from the `bsw` text of its rows;
LOWER_LIMIT/UPPER_LIMIT of a port or a measurement are its swc.yaml min/max, TOOL-150, else the type range as
before), CHARACTERISTIC VALUE / VAL_BLK for every calibration parameter (conversion RAT_FUNC from physical =
(n0 + n1 * stored) / d), IF_DATA XCP with PROTOCOL_LAYER and XCP_ON_CAN. The AML (description of IF_DATA XCP) belongs to the calibration tool and is not generated; --aml adds an
/include line for it.
RTE-26 (calibration K1): CHARACTERISTIC CURVE / MAP with AXIS_DESCR (COM_AXIS, STD_AXIS), AXIS_PTS, record layouts
with axis parts, engineering limits with EXTENDED_LIMITS, value tables (COMPU_METHOD TAB_VERB + COMPU_VTAB),
descriptions, MOD_PAR with EPK/ADDR_EPK of the image (xcp::EPK) and one FUNCTION per component.
RTE-28: a signal qualifier (an unsigned MEASUREMENT whose element name ends in Qlf/Qualifier, the rule of rtegen
is_qualifier_port) gets the value table CM_QLF/VTAB_QLF of the QLF_* values of Rte_Qlf.h (rtegen QLF_VALUES, with
QLF_NOT_AVAILABLE), so the calibration tool shows QLF_NOT_AVAILABLE instead of 64.
RTE-29: a fixed point signal (row of rte::SIGNAL_SCALINGS, written by rtegen for port types with a linear compu method)
gets the COMPU_METHOD RAT_FUNC of its conversion (shared with the parameters of the same function), its unit as
PHYS_UNIT and the converted type range as limits. The table exists only in an image with such signals; RteConfig.h
(NX_RTE_HAS_SIGNAL_SCALING) and the image must agree. Without it the A2L is byte for byte what it was.

Core logic without Qt; every error names the file (and the YAML path) it comes from.

Usage:
  a2lgen.py app.ecu.elf -o app.ecu.a2l --rte-config RteConfig.h --xcp-config xcp/xcpCanConfiguration.h
            [--bit-timing bsp/can/canBitTimingConfiguration.h] [--project project.yaml] [--name NAME] [--aml FILE]
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "rtegen"))   # RTE-22: Pfadbasis einer ECU-Beschreibung

from elf32 import Elf32, ElfError  # noqa: E402
import rtegen  # noqa: E402
from nxconfig import can as nxcan  # noqa: E402  (tools/ is on the path through rtegen)
from nxconfig import genlicense  # noqa: E402  (PLAT-120: licence line of generated files)

REPO = HERE.parents[1]

# Layout of the RTE tables on a 32 bit target (ecu/xcp/src/systems/XcpSystem.cpp checks it with static_assert)
SIGNAL_SYMBOL = "_ZN3rte7SIGNALSE"        # rte::SIGNALS
PARAMETER_SYMBOL = "_ZN3rte10PARAMETERSE"  # rte::PARAMETERS
SIGNAL_LAYOUT = {"size": 16, "name": 0, "bsw": 4, "type": 8, "value": 12}
# RTE-29: rte::SIGNAL_SCALINGS (RteConfig.h.j2 struct SignalScaling), only in an image with fixed point signals
SCALING_SYMBOL = "_ZN3rte15SIGNAL_SCALINGSE"  # rte::SIGNAL_SCALINGS
SCALING_LAYOUT = {"size": 20, "signal": 0, "decimals": 2, "unit": 4, "numerator": 8, "denominator": 16}
SCALING_DEFINE = "NX_RTE_HAS_SIGNAL_SCALING"
PARAMETER_LAYOUT = {"size": 40, "name": 0, "unit": 4, "value": 8, "type": 12, "count": 14, "numerator": 16,
                    "denominator": 24,
                    # RTE-26: calibration definition (RteConfig.h.j2 struct Parameter)
                    "limits": 28, "axis": 32, "kind": 36, "safety": 37}

# rte::SignalType enumerator -> (A2L datatype, bytes, lowest stored value, highest stored value)
FLOAT32_MAX = 3.4028234663852886e38
FLOAT64_MAX = 1.7976931348623157e308
TYPES = {
    "BOOLEAN": ("UBYTE", 1, 0, 1),
    "UINT8": ("UBYTE", 1, 0, 0xFF),
    "SINT8": ("SBYTE", 1, -0x80, 0x7F),
    "UINT16": ("UWORD", 2, 0, 0xFFFF),
    "SINT16": ("SWORD", 2, -0x8000, 0x7FFF),
    "UINT32": ("ULONG", 4, 0, 0xFFFFFFFF),
    "SINT32": ("SLONG", 4, -0x80000000, 0x7FFFFFFF),
    "FLOAT32": ("FLOAT32_IEEE", 4, -FLOAT32_MAX, FLOAT32_MAX),
    "FLOAT64": ("FLOAT64_IEEE", 8, -FLOAT64_MAX, FLOAT64_MAX),
}

# XCP protocol values of the slave (ecu/xcp/include/xcp/XcpSlave.h)
MAX_CTO = 8
MAX_DTO = 8
MAX_BS = 16
T1_MS = 200  # command timeout: covers the hand-over of a write to the lowest RTE task (NX_XCP_WRITE_TIMEOUT_MS)
# TOOL-151: SET_CAL_PAGE/GET_CAL_PAGE are not announced. The slave answers them for page 0 only (the RAM values are the
# one page the ECU uses, no switching); announced, a calibration tool expects a switchable reference/working page and
# fails on SET_CAL_PAGE page 1 (ERR_PAGE_NOT_VALID). Without them and without SEGMENT/PAGE the A2L describes one page.
OPTIONAL_COMMANDS = ("GET_COMM_MODE_INFO", "GET_ID", "SET_MTA", "UPLOAD", "SHORT_UPLOAD", "DOWNLOAD",
                     "DOWNLOAD_NEXT", "DOWNLOAD_MAX")

IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_.\[\]]*")


class A2lError(Exception):
    """Error with file (and YAML path) in the message."""


@dataclass
class Signal:
    name: str
    bsw: str
    type: str
    address: int
    dim: int = 1                    # RTE-25: elements of an array (MATRIX_DIM), grouped rows of rte::SIGNALS
    # RTE-29: conversion of a fixed point row (rte::SIGNAL_SCALINGS): physical = (n0 + n1 * stored) / d
    numerator: tuple = (0, 1)
    denominator: int = 1
    unit: str = ""

    @property
    def scaled(self) -> bool:
        return (tuple(self.numerator), self.denominator) != ((0, 1), 1)


ELEMENT = re.compile(r"^(.*)\[(\d+)\]$")


def group_arrays(signals: list[Signal]) -> list[Signal]:
    """RTE-25: rows of rte::SIGNALS that are the elements of one array become one MEASUREMENT with MATRIX_DIM.

    The RTE lists an array element by element (Port[0], Port[1], ...; a record member Stat.Limits[0] as well). A
    run of rows with the indices 0..n-1, the same element type and BSW text, at consecutive addresses in the
    element size, is one array of n elements at the address of element 0. Only the innermost index is grouped (an
    array of named rows stays one MEASUREMENT per row). Anything else - a gap, another order, a different type -
    stays element by element, so the A2L never claims a layout the image does not have.
    """
    out: list[Signal] = []
    i = 0
    while i < len(signals):
        s = signals[i]
        m = ELEMENT.match(s.name)
        j = i + 1
        if m is not None and m.group(2) == "0":
            base, size = m.group(1), TYPES[s.type][1]
            while j < len(signals):
                t, n = signals[j], j - i
                if t.name != f"{base}[{n}]" or t.type != s.type or t.bsw != s.bsw or t.address != s.address + n * size \
                        or (t.numerator, t.denominator, t.unit) != (s.numerator, s.denominator, s.unit):   # RTE-29
                    break
                j += 1
        if j - i > 1:
            out.append(Signal(m.group(1), s.bsw, s.type, s.address, j - i, s.numerator, s.denominator, s.unit))
        else:
            out.append(s)
        i = j
    return out


MEASUREMENT_TEXT = re.compile(r"^measurement(?: \[([^\]]*)\])?(?: (.*))?$")


def measurement_texts(s: Signal) -> tuple[str, str]:
    """TOOL-143: (long identifier, unit) of a row of rte::SIGNALS. A measurement of a component carries
    "measurement [<unit>] <description>" in its bsw text (tools/rtegen Measurement.text); every other row keeps
    its connection text, or "RTE signal", and has no unit."""
    m = MEASUREMENT_TEXT.match(s.bsw or "")
    if m is None:
        return s.bsw or "RTE signal", ""
    return m.group(2) or f"measurement {s.name}", m.group(1) or ""


def _signal_key(name: str) -> str | None:
    """'Component.Name' of a rte::SIGNALS row name (TOOL-150), matching an entry of load_swc_limits(): a
    measurement's label is already 'Component.Name'; a port's is 'Component.Port.Element' (RTE-07: one data
    element, named like its port, so the array-grouped name is 'Component.Port.Port' too) - the first two dotted
    segments are the same key either way. None for a name with no dot (should not happen: every row belongs to a
    component)."""
    parts = name.split(".")
    return f"{parts[0]}.{parts[1]}" if len(parts) >= 2 else None


def load_swc_limits(project_path: Path, repo: Path = REPO) -> dict[str, tuple[str, float | int | None, float | int | None]]:
    """TOOL-150: (unit, min, max) of the ports and measurements of every component of the project's ECU description
    (rte.yaml next to project.yaml), keyed like _signal_key() ('Component.Name').

    Best effort and read straight from swc.yaml text, not through swc_yaml.check(): a2lgen already trusts the image
    for what is actually built (RTE-26 does the same for CHARACTERISTIC limits, stored there at build time); this
    only reads the engineering metadata next to it, for the ports and measurements swc.yaml has no room for in the
    image (rte::SIGNALS has no limits field, unlike rte::PARAMETERS). A project without `--project`, an ECU
    description a2lgen cannot resolve, or a component whose swc.yaml cannot be read contributes nothing - the A2L
    stays exactly what it always was (the type range, no PHYS_UNIT for a port): existing profiles without these
    fields are unaffected, byte for byte."""
    import yaml
    try:
        project = yaml.safe_load(Path(project_path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    rte_name = (project.get("project") or {}).get("rte")
    if not rte_name:
        return {}
    rte_path = Path(project_path).parent / rte_name
    try:
        ecu = yaml.safe_load(rte_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    found: dict[str, tuple[str, float | int | None, float | int | None]] = {}
    for comp in ecu.get("components") or []:
        if not isinstance(comp, dict) or not comp.get("name") or not comp.get("path"):
            continue
        try:
            swc_file = rtegen.path_of(ecu, rte_path, repo, str(comp["path"])) / rtegen.swc_yaml.FILE_NAME
            data = yaml.safe_load(swc_file.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(data, dict):
            continue
        for key in ("ports", "measurements"):
            for row in data.get(key) or []:
                if not isinstance(row, dict) or not isinstance(row.get("name"), str):
                    continue
                unit, lo, hi = row.get("unit"), row.get("min"), row.get("max")
                if unit or lo is not None or hi is not None:
                    found[f"{comp['name']}.{row['name']}"] = (str(unit or ""), lo, hi)
    return found


@dataclass
class Parameter:
    name: str
    unit: str
    type: str
    address: int
    count: int
    numerator: tuple[int, int]
    denominator: int
    # RTE-26 (calibration K1): from the image (rte::PARAMETERS) and RteCalibration.json
    kind: str = "PLAIN"             # PLAIN, CURVE, MAP, AXIS, OWN_AXIS (rte::ParameterKind)
    safety: str = "QM"              # rte::SafetyClass
    limits: tuple | None = None     # stored (min, max) of the element type, None: type range
    axis: tuple = (0xFFFF, 0xFFFF)  # rows of the axes (CURVE/MAP) or of the owner (OWN_AXIS)
    description: str = ""
    value_table: tuple = ()         # ((stored value, text), ...)


@dataclass
class XcpCan:
    bus: str
    master_id: int   # bit 31: extended
    slave_id: int
    bitrate: int | None = None
    source: str = ""


@dataclass
class Image:
    signals: list[Signal]
    parameters: list[Parameter]
    little_endian: bool
    notes: list[str] = field(default_factory=list)
    epk: str = ""                   # RTE-26: identity of the build (xcp::EPK) and its address
    epk_address: int | None = None
    layout: str = ""                # layout identifier of the calibration objects (RteCalibration.json)


# ---- inputs -----------------------------------------------------------------------------------------------------

def load_signal_types(path: Path) -> list[str]:
    """Enumerators of rte::SignalType in declaration order (their values are the numbers in the tables)."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise A2lError(f"{path}: cannot read ({exc.strerror})") from exc
    m = re.search(r"enum\s+class\s+SignalType\s*:\s*uint8_t\s*\{([^}]*)\}", text)
    if not m:
        raise A2lError(f"{path}: enum class SignalType not found (RteConfig.h of tools/rtegen expected)")
    names = [n.strip().split("=")[0].strip() for n in m.group(1).split(",")]
    names = [n for n in names if n]
    unknown = [n for n in names if n not in TYPES]
    if unknown:
        raise A2lError(f"{path}: SignalType {', '.join(unknown)} unknown to a2lgen (known: {', '.join(TYPES)})")
    return names


def _define(text: str, name: str, path: Path) -> str:
    m = re.search(rf"^\s*#\s*define\s+{name}\s+(\S+)", text, re.MULTILINE)
    if not m:
        raise A2lError(f"{path}: #define {name} missing")
    return m.group(1)


def _number(token: str, name: str, path: Path) -> int:
    m = re.fullmatch(r"(0[xX][0-9A-Fa-f]+|\d+)[uUlL]*", token)
    if not m:
        raise A2lError(f"{path}: {name} is not a number: {token}")
    return int(m.group(1), 0)


def load_xcp_config(path: Path) -> XcpCan:
    """Bus and identifiers from xcp/xcpCanConfiguration.h (the header the target is compiled with)."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise A2lError(f"{path}: cannot read ({exc.strerror})") from exc
    bus = _define(text, "NX_XCP_BUS_NAME", path).strip('"')
    master = _number(_define(text, "NX_XCP_CAN_ID_MASTER", path), "NX_XCP_CAN_ID_MASTER", path)
    slave = _number(_define(text, "NX_XCP_CAN_ID_SLAVE", path), "NX_XCP_CAN_ID_SLAVE", path)
    for name, value in (("NX_XCP_CAN_ID_MASTER", master), ("NX_XCP_CAN_ID_SLAVE", slave)):
        raw = value & 0x7FFFFFFF
        if value & 0x80000000:
            if raw > 0x1FFFFFFF:
                raise A2lError(f"{path}: {name} 0x{value:X} exceeds the 29 bit range")
        elif raw > 0x7FF:
            raise A2lError(f"{path}: {name} 0x{value:X} exceeds the 11 bit range (set bit 31 for an extended id)")
    if master == slave:
        raise A2lError(f"{path}: NX_XCP_CAN_ID_MASTER and NX_XCP_CAN_ID_SLAVE are both 0x{master:X}")
    return XcpCan(bus, master, slave, None, str(path))


def load_bitrate(path: Path, bus: str) -> int:
    """Bit rate of the XCP bus from the generated bit timing header.

    `bus` is the board name of the bus (NX_XCP_BUS_NAME, e.g. "chassis"); the header names its constants after the
    C identifier of the bus (CHASSIS_BITRATE). Bus names are free since ARCH-61, so the name is never used as it
    is: the identifier comes from nxconfig.can.c_name, the rule of the generators (TOOL-112).
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise A2lError(f"{path}: cannot read ({exc.strerror})") from exc
    ident = nxcan.c_name(bus)
    m = re.search(rf"\b{re.escape(ident)}_BITRATE\s*=\s*(\d+)U?", text)
    if not m:
        raise A2lError(f"{path}: {ident}_BITRATE not found (bus {bus} of XCP has no bit timing in the project)")
    return int(m.group(1))


def has_signal_scaling(path: Path) -> bool:
    """RTE-29: RteConfig.h declares rte::SIGNAL_SCALINGS (the ECU has fixed point signals)."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise A2lError(f"{path}: cannot read ({exc.strerror})") from exc
    return re.search(rf"^\s*#\s*define\s+{SCALING_DEFINE}\b", text, re.MULTILINE) is not None


def read_signal_scalings(elf, elf_path: Path, signals: list[Signal], declared: bool | None) -> None:
    """RTE-29: conversion and unit of the fixed point rows from rte::SIGNAL_SCALINGS, applied to `signals`.

    `declared`: what RteConfig.h says (None: not checked). A header with the table and an image without it means the
    linker removed it (nothing reads it) or header and image belong to different builds - the A2L would show stored
    values as physical ones, so that is an error, as the reverse is."""
    table = elf.symbols.get(SCALING_SYMBOL)
    if table is None:
        if declared:
            raise A2lError(f"{elf_path}: rte::SIGNAL_SCALINGS missing although RteConfig.h defines {SCALING_DEFINE} "
                           f"(removed by the linker, or RteConfig.h of another build)")
        return
    if declared is False:
        raise A2lError(f"{elf_path}: rte::SIGNAL_SCALINGS in the image, but RteConfig.h has no {SCALING_DEFINE} "
                       f"(RteConfig.h of another build?)")
    if table.size % SCALING_LAYOUT["size"]:
        raise A2lError(f"{elf_path}: rte::SIGNAL_SCALINGS has {table.size} bytes, not a multiple of "
                       f"{SCALING_LAYOUT['size']} (layout of rte::SignalScaling changed? see SCALING_LAYOUT)")
    previous = -1
    for i in range(table.size // SCALING_LAYOUT["size"]):
        base = table.value + i * SCALING_LAYOUT["size"]
        row = elf.u16(base + SCALING_LAYOUT["signal"])
        if row <= previous or row >= len(signals):
            raise A2lError(f"{elf_path}: rte::SIGNAL_SCALINGS[{i}] names row {row} of rte::SIGNALS "
                           f"({len(signals)} rows, ascending expected)")
        previous = row
        s = signals[row]
        s.numerator = (elf.i32(base + SCALING_LAYOUT["numerator"]), elf.i32(base + SCALING_LAYOUT["numerator"] + 4))
        s.denominator = elf.i32(base + SCALING_LAYOUT["denominator"])
        unit_ptr = elf.u32(base + SCALING_LAYOUT["unit"])
        s.unit = elf.cstring(unit_ptr) if unit_ptr else ""
        if s.numerator[1] == 0 or s.denominator == 0:
            raise A2lError(f"{elf_path}: signal {s.name}: conversion ({s.numerator[0]} + {s.numerator[1]} * stored) "
                           f"/ {s.denominator} is not invertible")
        if s.type.startswith("FLOAT"):
            raise A2lError(f"{elf_path}: signal {s.name}: a {s.type} row with a fixed point conversion")


def read_image(elf_path: Path, types: list[str], scaling_declared: bool | None = None) -> Image:
    """rte::SIGNALS (RTE-29: with rte::SIGNAL_SCALINGS) and rte::PARAMETERS as stored in the image."""
    try:
        elf = Elf32(elf_path)
        signals = []
        table = elf.symbol(SIGNAL_SYMBOL, "rte::SIGNALS")
        if table.size % SIGNAL_LAYOUT["size"]:
            raise A2lError(f"{elf_path}: rte::SIGNALS has {table.size} bytes, not a multiple of "
                           f"{SIGNAL_LAYOUT['size']} (layout of rte::Signal changed? see SIGNAL_LAYOUT)")
        for i in range(table.size // SIGNAL_LAYOUT["size"]):
            base = table.value + i * SIGNAL_LAYOUT["size"]
            name = elf.cstring(elf.u32(base + SIGNAL_LAYOUT["name"]))
            bsw_ptr = elf.u32(base + SIGNAL_LAYOUT["bsw"])
            signals.append(Signal(name, elf.cstring(bsw_ptr) if bsw_ptr else "",
                                  _type(types, elf.u8(base + SIGNAL_LAYOUT["type"]), elf_path, name),
                                  elf.u32(base + SIGNAL_LAYOUT["value"])))
        read_signal_scalings(elf, elf_path, signals, scaling_declared)            # RTE-29
        parameters = []
        table = elf.symbols.get(PARAMETER_SYMBOL)
        if table is not None:
            if table.size % PARAMETER_LAYOUT["size"]:
                raise A2lError(f"{elf_path}: rte::PARAMETERS has {table.size} bytes, not a multiple of "
                               f"{PARAMETER_LAYOUT['size']} (layout of rte::Parameter changed? see PARAMETER_LAYOUT)")
            for i in range(table.size // PARAMETER_LAYOUT["size"]):
                base = table.value + i * PARAMETER_LAYOUT["size"]
                name = elf.cstring(elf.u32(base + PARAMETER_LAYOUT["name"]))
                p = Parameter(
                    name=name,
                    unit=elf.cstring(elf.u32(base + PARAMETER_LAYOUT["unit"])),
                    type=_type(types, elf.u8(base + PARAMETER_LAYOUT["type"]), elf_path, name),
                    address=elf.u32(base + PARAMETER_LAYOUT["value"]),
                    count=elf.u16(base + PARAMETER_LAYOUT["count"]),
                    numerator=(elf.i32(base + PARAMETER_LAYOUT["numerator"]),
                               elf.i32(base + PARAMETER_LAYOUT["numerator"] + 4)),
                    denominator=elf.i32(base + PARAMETER_LAYOUT["denominator"]))
                read_calibration_fields(elf, base, p, elf.little_endian)          # RTE-26
                if p.count == 0:
                    raise A2lError(f"{elf_path}: parameter {name} has no elements")
                if p.numerator[1] == 0 or p.denominator == 0:
                    raise A2lError(f"{elf_path}: parameter {name}: conversion ({p.numerator[0]} + "
                                   f"{p.numerator[1]} * stored) / {p.denominator} is not invertible")
                parameters.append(p)
        epk, epk_address = read_epk(elf)                                          # RTE-26
        return Image(signals, parameters, elf.little_endian, epk=epk, epk_address=epk_address)
    except ElfError as exc:
        raise A2lError(str(exc)) from exc


def _type(types: list[str], value: int, path: Path, name: str) -> str:
    if value >= len(types):
        raise A2lError(f"{path}: {name}: element type {value} outside rte::SignalType ({len(types)} values)")
    return types[value]


# ---- RTE-26: calibration objects (stage K1 of doc/architecture/kalibrierung.md) ----------------------------------
#
# The image says what an object is (rte::PARAMETERS: kind, axis rows, stored limits, safety class); the texts that
# are not needed at run time (description, value table) come from RteCalibration.json next to RteConfig.h, which
# a2lgen checks against the image. A2L objects:
#   PLAIN (value/array)   CHARACTERISTIC VALUE / VAL_BLK
#   CURVE, MAP            CHARACTERISTIC CURVE / MAP with one AXIS_DESCR per axis: COM_AXIS + AXIS_PTS_REF for a
#                         shared axis, STD_AXIS for own breakpoints (stored in the same record in front of the
#                         values; RECORD_LAYOUT with FIX_NO_AXIS_PTS_X/Y, AXIS_PTS_X/Y, FNC_VALUES); a map keeps
#                         one block per X breakpoint with the values over Y, Y running fastest - in A2L terms the
#                         X axis runs across the columns, so that is column by column: FNC_VALUES COLUMN_DIR in a
#                         record layout of its own (RL_MAP_...; TOOL-196, a2lgen wrote ROW_DIR before)
#   AXIS                  AXIS_PTS
#   OWN_AXIS              no object of its own, part of the record of its curve/map
# Engineering limits (swc.yaml min/max, stored in the image) are the limits of the object; the type range becomes
# EXTENDED_LIMITS. A value table is a COMPU_METHOD TAB_VERB with a COMPU_VTAB. One FUNCTION per component lists
# its calibration objects.

KIND_NAMES = ("PLAIN", "CURVE", "MAP", "AXIS", "OWN_AXIS")
SAFETY_NAMES = ("QM", "ASIL_A", "ASIL_B", "ASIL_C", "ASIL_D")
NO_AXIS = 0xFFFF
EPK_SYMBOL = "_ZN3xcp3EPKE"               # xcp::EPK (ecu/xcp/src/systems/XcpEpk.cpp)
CALIBRATION_FILE = "RteCalibration.json"   # tools/rtegen, next to RteConfig.h
CALIBRATION_FORMAT = "nextbsw-rte-calibration 1"
STRUCT_CODES = {"BOOLEAN": "B", "UINT8": "B", "SINT8": "b", "UINT16": "H", "SINT16": "h", "UINT32": "I",
                "SINT32": "i", "FLOAT32": "f", "FLOAT64": "d"}


def read_stored(elf, address: int, kind: str, count: int, little_endian: bool) -> list:
    """`count` stored values of rte::SignalType `kind` at `address` of the image."""
    import struct
    size = TYPES[kind][1]
    data = elf.read(address, size * count)
    return list(struct.unpack(("<" if little_endian else ">") + STRUCT_CODES[kind] * count, data))


def read_calibration_fields(elf, base: int, p: "Parameter", little_endian: bool):
    """RTE-26 part of one rte::PARAMETERS row: stored limits, axis rows, kind, safety class."""
    limits = elf.u32(base + PARAMETER_LAYOUT["limits"])
    p.limits = tuple(read_stored(elf, limits, p.type, 2, little_endian)) if limits else None
    p.axis = (elf.u16(base + PARAMETER_LAYOUT["axis"]), elf.u16(base + PARAMETER_LAYOUT["axis"] + 2))
    kind, safety = elf.u8(base + PARAMETER_LAYOUT["kind"]), elf.u8(base + PARAMETER_LAYOUT["safety"])
    if kind >= len(KIND_NAMES) or safety >= len(SAFETY_NAMES):
        raise A2lError(f"{elf.path}: parameter {p.name}: kind {kind} / safety class {safety} unknown to a2lgen "
                       f"(rte::ParameterKind / rte::SafetyClass changed?)")
    p.kind, p.safety = KIND_NAMES[kind], SAFETY_NAMES[safety]


def read_epk(elf) -> tuple[str, int | None]:
    """EPK text and address from the image (xcp::EPK); ("", None) for an image without it."""
    symbol = elf.symbols.get(EPK_SYMBOL)
    if symbol is None:
        return "", None
    text = elf.cstring(symbol.value)
    if not text or any(not 0x20 <= ord(ch) < 0x7F or ch in '"\\' for ch in text):
        raise A2lError(f"{elf.path}: xcp::EPK is no printable text ({text!r})")
    return text, symbol.value


def load_calibration(rte_config: Path) -> dict | None:
    """RteCalibration.json next to RteConfig.h (tools/rtegen); None if the generate is older than RTE-26."""
    import json
    path = Path(rte_config).parent / CALIBRATION_FILE
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise A2lError(f"{path}: cannot read ({exc})") from exc
    if data.get("format") != CALIBRATION_FORMAT:
        raise A2lError(f"{path}: format {data.get('format')!r}, expected {CALIBRATION_FORMAT!r}")
    data["path"] = str(path)
    return data


# kind of the image row -> kinds RteCalibration.json may give for it
_JSON_KINDS = {"PLAIN": ("value", "array"), "CURVE": ("curve",), "MAP": ("map",), "AXIS": ("axis",),
               "OWN_AXIS": ("own_axis",)}


def apply_calibration(image: "Image", calibration: dict | None, elf_path: Path):
    """Checks the image against RteCalibration.json and takes description and value table from it.

    Both must name the same objects with the same kind, and the axis rows of the image must fit (a curve has one
    axis, a map two, each of kind AXIS or OWN_AXIS; own breakpoints point back at their owner). a2lgen never
    describes an object the image does not have.
    """
    params = image.parameters
    for i, p in enumerate(params):
        rows = [r for r in p.axis if r != NO_AXIS]
        want = {"CURVE": 1, "MAP": 2, "OWN_AXIS": 1}.get(p.kind, 0)
        if len(rows) != want or any(r >= len(params) for r in rows):
            raise A2lError(f"{elf_path}: parameter {p.name} ({p.kind}) has axis rows {list(p.axis)}, expected {want}")
        if p.kind in ("CURVE", "MAP"):
            for r in rows:
                if params[r].kind not in ("AXIS", "OWN_AXIS"):
                    raise A2lError(f"{elf_path}: axis of {p.name} is {params[r].name} of kind {params[r].kind}")
                if params[r].kind == "OWN_AXIS" and params[r].axis[0] != i:
                    raise A2lError(f"{elf_path}: own breakpoints {params[r].name} do not belong to {p.name}")
            count = 1
            for r in rows:
                count *= params[r].count
            if count != p.count:
                raise A2lError(f"{elf_path}: {p.name} has {p.count} values, its axes give {count}")
    if calibration is None:
        if any(p.kind != "PLAIN" or p.limits is not None or p.safety != "QM" for p in params):
            image.notes.append(f"{CALIBRATION_FILE} missing next to RteConfig.h: no descriptions and value tables")
        return
    image.layout = calibration.get("layout", "")
    described = calibration.get("parameters") or {}
    path = calibration["path"]
    names = {p.name for p in params}
    extra = sorted(set(described) - names)
    if extra:
        raise A2lError(f"{path}: {', '.join(extra)} not in the image {elf_path} (generate and build do not belong "
                       f"together)")
    for p in params:
        info = described.get(p.name)
        if info is None:
            raise A2lError(f"{path}: parameter {p.name} of the image {elf_path} is missing")
        if info.get("kind") not in _JSON_KINDS[p.kind]:
            raise A2lError(f"{path}: {p.name} is {info.get('kind')}, the image says {p.kind}")
        p.description = str(info.get("description") or "")
        p.value_table = tuple((int(k), str(v)) for k, v in info.get("value_table") or ())
        if p.value_table and (p.type.startswith("FLOAT") or (p.numerator, p.denominator) != ((0, 1), 1)):
            raise A2lError(f"{path}: {p.name}: a value table needs an unscaled integer type")


def _phys(p: "Parameter", stored) -> float | int:
    n0, n1 = p.numerator
    value = (n0 + n1 * stored) / p.denominator
    if TYPES[p.type][0].startswith("FLOAT"):
        return float(stored)
    return int(value) if value == int(value) else value


def engineering_limits(p: "Parameter") -> tuple:
    """Physical (lower, upper) of the object: swc.yaml min/max as stored in the image, else the type range."""
    if p.limits is None:
        return physical_limits(p)
    a, b = _phys(p, p.limits[0]), _phys(p, p.limits[1])
    return (min(a, b), max(a, b))


def vtab_name(p: "Parameter") -> str:
    return "VTAB_" + p.name.replace(".", "_")


def compu_of(p: "Parameter") -> str:
    """COMPU_METHOD of an object: its value table, or the rational function of its conversion."""
    return "CM_TAB_" + p.name.replace(".", "_") if p.value_table else compu_name(p)


def _align(offset: int, kind: str) -> int:
    size = TYPES[kind][1]            # MOD_COMMON ALIGNMENT_*: natural alignment of the ARM EABI
    return -(-offset // size) * size


def record_parts(p: "Parameter", params: list) -> list:
    """Record of a curve/map with own breakpoints: [(part, parameter)] in memory order (X, Y, values)."""
    own = [params[r] for r in p.axis if r != NO_AXIS and params[r].kind == "OWN_AXIS"]
    letters = {id(params[r]): "XY"[i] for i, r in enumerate(r for r in p.axis if r != NO_AXIS)}
    return [(letters[id(a)], a) for a in own] + [("V", p)]


def record_address(p: "Parameter", params: list, elf_path) -> int:
    """Start of the record of a curve/map (its first own axis); checks that the parts lie as the layout says."""
    parts = record_parts(p, params)
    start = parts[0][1].address
    offset = 0
    for letter, part in parts:
        offset = _align(offset, part.type)
        if part.address != start + offset:
            raise A2lError(f"{elf_path}: {part.name} lies at 0x{part.address:08X}, the record of {p.name} expects "
                           f"0x{start + offset:08X} (own breakpoints must be stored in front of the values)")
        offset += TYPES[part.type][1] * part.count
    return start


def record_layout_name(p: "Parameter", params: list) -> str:
    """Record layout of an object: RL_<type> (values only), RL_AXIS_<type>, or RL_STD_... for a record; a map has
    layouts of its own (RL_MAP_<type>, RL_MAP_<parts>) because its values are stored column by column (TOOL-196)."""
    dtype = TYPES[p.type][0]
    if p.kind == "AXIS":
        return f"RL_AXIS_{dtype}"
    parts = record_parts(p, params) if p.kind in ("CURVE", "MAP") else [("V", p)]
    prefix = "RL_MAP_" if p.kind == "MAP" else "RL_"
    if len(parts) == 1:
        return f"{prefix}{dtype}"
    return (prefix if p.kind == "MAP" else "RL_STD_") +         "_".join(f"{letter}{part.count}{TYPES[part.type][0]}" for letter, part in parts)


def index_mode(p: "Parameter") -> str:
    """FNC_VALUES index mode: a map lies as one block per X breakpoint (the values over Y, Y running fastest; the
    order of <Swc>_data.m, the RTE and Simulink's column-major matrix ny x nx). In the A2L view the X axis runs
    across the columns and Y down the rows, so that is COLUMN_DIR; ROW_DIR would mean X running fastest (TOOL-196,
    cross-checked with the free readers objutils/asamint and calibrationReader, test_map_index_mode.py). For values,
    arrays and curves (one dimension) both modes give the same order; they keep ROW_DIR."""
    return "COLUMN_DIR" if p.kind == "MAP" else "ROW_DIR"


def record_layout_lines(name: str, p: "Parameter", params: list) -> list:
    dtype = TYPES[p.type][0]
    if p.kind == "AXIS":
        return [f"    /begin RECORD_LAYOUT {name}", f"      AXIS_PTS_X 1 {dtype} INDEX_INCR DIRECT",
                "    /end RECORD_LAYOUT"]
    lines = [f"    /begin RECORD_LAYOUT {name}"]
    for position, (letter, part) in enumerate(record_parts(p, params), start=1):
        if letter == "V":
            lines.append(f"      FNC_VALUES {position} {TYPES[part.type][0]} {index_mode(p)} DIRECT")
        else:
            lines += [f"      FIX_NO_AXIS_PTS_{letter} {part.count}",
                      f"      AXIS_PTS_{letter} {position} {TYPES[part.type][0]} INDEX_INCR DIRECT"]
    return lines + ["    /end RECORD_LAYOUT"]


def calibration_definitions(image: "Image") -> list:
    """COMPU_METHOD/COMPU_VTAB of value tables and the record layouts of axes and records (RTE-26 objects only)."""
    out = []
    params = image.parameters
    for p in params:
        if p.value_table:
            out += [f"    /begin COMPU_METHOD {compu_of(p)} {_quote('value table of ' + p.name)}",
                    f"      TAB_VERB {_quote('%.0')} {_quote('')}",
                    f"      COMPU_TAB_REF {vtab_name(p)}",
                    "    /end COMPU_METHOD",
                    f"    /begin COMPU_VTAB {vtab_name(p)} {_quote(p.name)} TAB_VERB {len(p.value_table)}"]
            out += [f"      {value} {_quote(text_)}" for value, text_ in p.value_table]
            out.append("    /end COMPU_VTAB")
    layouts: dict[str, Parameter] = {}
    for p in params:
        if p.kind in ("AXIS", "CURVE", "MAP"):
            name = record_layout_name(p, params)
            if not name.startswith("RL_") or name == f"RL_{TYPES[p.type][0]}":
                continue                     # RL_<type>: written with the RTE-03 layouts
            layouts.setdefault(name, p)
    for name, p in layouts.items():
        out += record_layout_lines(name, p, params)
    return out


def _limits_lines(p: "Parameter", indent: str) -> list:
    """PHYS_UNIT and, with engineering limits, EXTENDED_LIMITS (the type range)."""
    lines = [f"{indent}PHYS_UNIT {_quote(p.unit)}"] if p.unit else []
    if p.limits is not None:
        low, high = physical_limits(p)
        lines.append(f"{indent}EXTENDED_LIMITS {_num(low)} {_num(high)}")
    return lines


def axis_descr_lines(axis: "Parameter") -> list:
    """AXIS_DESCR of one axis of a curve/map: COM_AXIS with AXIS_PTS_REF, or STD_AXIS for own breakpoints."""
    low, high = engineering_limits(axis)
    attribute = "STD_AXIS" if axis.kind == "OWN_AXIS" else "COM_AXIS"
    lines = [f"      /begin AXIS_DESCR {attribute} NO_INPUT_QUANTITY {compu_name(axis)} {axis.count} {_num(low)} "
             f"{_num(high)}"]
    if attribute == "COM_AXIS":
        lines.append(f"        AXIS_PTS_REF {axis.name}")
    lines += _limits_lines(axis, "        ")
    return lines + ["      /end AXIS_DESCR"]


def characteristic_lines(p: "Parameter", image: "Image") -> list:
    """A2L object of one row of rte::PARAMETERS; an RTE-03 parameter gets exactly the lines of TOOL-01."""
    params = image.parameters
    dtype = TYPES[p.type][0]
    if p.kind == "OWN_AXIS":
        return []                        # part of the record of its curve/map
    low, high = engineering_limits(p)
    text_ = p.description or ("RTE calibration parameter " + p.name)
    if p.kind == "AXIS":
        lines = [f"    /begin AXIS_PTS {_ident(p.name, 'axis')} {_quote(p.description or 'RTE axis ' + p.name)}",
                 f"      0x{p.address:08X} NO_INPUT_QUANTITY {record_layout_name(p, params)} 0 {compu_name(p)} "
                 f"{p.count} {_num(low)} {_num(high)}"]
        return lines + _limits_lines(p, "      ") + ["    /end AXIS_PTS"]
    if p.kind in ("CURVE", "MAP"):
        address = record_address(p, params, "image")
        lines = [f"    /begin CHARACTERISTIC {_ident(p.name, 'parameter')} {_quote(text_)}",
                 f"      {p.kind} 0x{address:08X} {record_layout_name(p, params)} 0 {compu_of(p)} {_num(low)} "
                 f"{_num(high)}"]
        for r in (r for r in p.axis if r != NO_AXIS):
            lines += axis_descr_lines(params[r])
        return lines + _limits_lines(p, "      ") + ["    /end CHARACTERISTIC"]
    kind = "VALUE" if p.count == 1 else "VAL_BLK"
    lines = [f"    /begin CHARACTERISTIC {_ident(p.name, 'parameter')} {_quote(text_)}",
             f"      {kind} 0x{p.address:08X} RL_{dtype} 0 {compu_of(p)} {_num(low)} {_num(high)}"]
    if p.count > 1:
        lines.append(f"      NUMBER {p.count}")
    return lines + _limits_lines(p, "      ") + ["    /end CHARACTERISTIC"]


def function_lines(image: "Image") -> list:
    """One FUNCTION per component with its calibration objects (DEF_CHARACTERISTIC: CHARACTERISTIC, AXIS_PTS)."""
    groups: dict[str, list] = {}
    for p in image.parameters:
        if p.kind != "OWN_AXIS":
            groups.setdefault(p.name.split(".")[0], []).append(p.name)
    out = []
    for component, names in groups.items():
        out += [f"    /begin FUNCTION {_ident(component, 'component')} {_quote('calibration objects of ' + component)}",
                "      /begin DEF_CHARACTERISTIC"]
        out += [f"        {name}" for name in names]
        out += ["      /end DEF_CHARACTERISTIC", "    /end FUNCTION"]
    return out


def mod_par_lines(image: "Image") -> list:
    """MOD_PAR with EPK and ADDR_EPK of the image (xcp::EPK), empty without it."""
    if not image.epk:
        return []
    return [f"    /begin MOD_PAR {_quote('neXtBSW application')}",
            f"      EPK {_quote(image.epk)}",
            f"      ADDR_EPK 0x{image.epk_address:08X}",
            "    /end MOD_PAR"]


# ---- checks against the project -----------------------------------------------------------------------------------

def check_project(xcp: XcpCan, project_path: Path, repo: Path = REPO) -> list[str]:
    """Identifier collisions of XCP with the diagnostics and the DBC of the project. Returns warnings."""
    import yaml  # PyYAML, as nxconfig

    try:
        project = yaml.safe_load(Path(project_path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise A2lError(f"{project_path}: cannot read ({exc})") from exc
    used_raw = (project.get("use") or {}).get("can") or []
    # "all": every CAN bus of the board - the build only gets that far with a bus the board has (busid::<bus>)
    used = [] if isinstance(used_raw, str) else [str(b) for b in used_raw]
    if isinstance(used_raw, str) and used_raw != "all":
        used = [used_raw]
    if used and xcp.bus not in used:
        raise A2lError(f"{project_path}: use.can: XCP bus {xcp.bus} ({xcp.source}) is not a CAN bus of the project "
                       f"({', '.join(used)})")
    ecu, rte_path = {}, None
    rte_name = (project.get("project") or {}).get("rte")
    if rte_name:
        rte_path = Path(project_path).parent / rte_name
        try:
            ecu = yaml.safe_load(rte_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise A2lError(f"{rte_path}: cannot read ({exc})") from exc
    can = ecu.get("can") or {}
    can_bus = can.get("bus") or (used[0] if used else None)
    uds_bus = (ecu.get("uds") or {}).get("bus") or can_bus
    taken: list[tuple[int, str]] = []
    diag = project.get("diagnostics") or {}
    if uds_bus == xcp.bus:
        for key in ("request_id", "functional_id", "response_id"):
            if key in diag:
                taken.append((int(diag[key]), f"{project_path}: diagnostics.{key}"))
    warnings = []
    if can.get("dbc") and can_bus == xcp.bus:
        dbc = rtegen.path_of(ecu, rte_path, repo, can["dbc"])
        try:
            import cantools
            db = cantools.database.load_file(str(dbc), strict=False)
            for msg in db.messages:
                ident = msg.frame_id | (0x80000000 if msg.is_extended_frame else 0)
                taken.append((ident, f"{dbc}: message {msg.name}"))
        except Exception as exc:  # noqa: BLE001 - any parse problem is reported, the A2L is still written
            warnings.append(f"{dbc}: DBC not read, XCP identifiers not checked against it ({exc})")
    for ident, what in taken:
        for name, value in (("master", xcp.master_id), ("slave", xcp.slave_id)):
            if ident == value:
                raise A2lError(f"{xcp.source}: XCP {name} id 0x{value:X} on {xcp.bus} is already used by {what}")
    return warnings


# ---- output --------------------------------------------------------------------------------------------------------

def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _num(value: float | int) -> str:
    if isinstance(value, int):
        return str(value)
    return repr(float(value)) if abs(value) < 1e15 else f"{value:.9E}"


def _ident(name: str, what: str) -> str:
    if not IDENT.fullmatch(name):
        raise A2lError(f"{what} {name!r} is not a valid A2L identifier")
    return name


QLF_COMPU = "CM_QLF"
QLF_VTAB = "VTAB_QLF"
QLF_SIGNAL_TYPES = ("UINT8", "UINT16", "UINT32")


def is_qualifier_signal(s: Signal) -> bool:
    """RTE-28: a row of rte::SIGNALS that carries a qualifier (QLF_* of Rte_Qlf.h): unsigned, and its last name part
    (the element, index removed) ends in Qlf or Qualifier - the naming rtegen.is_qualifier_port checks, and the
    names of the BSW qualifiers (<Message>.Qlf, nvm.<Block>.Qlf)."""
    last = re.sub(r"\[\d+\]$", "", s.name).split(".")[-1].lower()
    return s.type in QLF_SIGNAL_TYPES and last.endswith(rtegen.QLF_PORT_SUFFIXES)


def qualifier_definitions() -> list:
    """RTE-28: COMPU_METHOD TAB_VERB with the COMPU_VTAB of all QLF_* values (the one list of rtegen)."""
    out = [f"    /begin COMPU_METHOD {QLF_COMPU} {_quote('signal qualifier QLF_* of Rte_Qlf.h')}",
           f"      TAB_VERB {_quote('%.0')} {_quote('')}",
           f"      COMPU_TAB_REF {QLF_VTAB}",
           "    /end COMPU_METHOD",
           f"    /begin COMPU_VTAB {QLF_VTAB} {_quote('QLF_* of Rte_Qlf.h')} TAB_VERB {len(rtegen.QLF_VALUES)}"]
    out += [f"      {value} {_quote('QLF_' + name)}" for name, value, _ in rtegen.QLF_VALUES]
    out.append("    /end COMPU_VTAB")
    return out


def physical_limits(p: Parameter) -> tuple[float | int, float | int]:
    _dtype, _size, low, high = TYPES[p.type]
    n0, n1 = p.numerator
    d = p.denominator
    a = (n0 + n1 * low) / d
    b = (n0 + n1 * high) / d
    lo, hi = min(a, b), max(a, b)
    if TYPES[p.type][0].startswith("FLOAT"):
        return lo, hi
    return (int(lo), int(hi)) if lo == int(lo) and hi == int(hi) else (lo, hi)


def compu_name(p: Parameter) -> str:
    n0, n1 = p.numerator
    if (n0, n1, p.denominator) == (0, 1, 1):
        return "CM_IDENTITY"
    part = lambda v: f"M{-v}" if v < 0 else str(v)  # noqa: E731
    return f"CM_RAT_{part(n0)}_{part(n1)}_{part(p.denominator)}"


def render(image: Image, xcp: XcpCan, name: str, aml: Path | None = None, source: str = "",
           limits: dict | None = None) -> str:
    name = _ident(name, "project name")
    order = "MSB_LAST" if image.little_endian else "MSB_FIRST"
    out = [
        "ASAP2_VERSION 1 61",
        f"/* Generated by tools/a2lgen{(' from ' + source) if source else ''} - do not edit. */",
        f"/begin PROJECT {name} {_quote('neXtBSW application')}",
        "  /begin HEADER " + _quote("") + " VERSION " + _quote("TOOL-01") + " /end HEADER",
        f"  /begin MODULE {name} {_quote('RTE signals and calibration parameters, XCP on CAN')}",
    ]
    if aml is not None:
        out.append(f"    /include {_quote(str(aml).replace(chr(92), '/'))}")
    out += mod_par_lines(image)                                                   # RTE-26: EPK
    out += [
        "    /begin MOD_COMMON " + _quote(""),
        f"      BYTE_ORDER {order}",
        "      ALIGNMENT_BYTE 1",
        "      ALIGNMENT_WORD 2",
        "      ALIGNMENT_LONG 4",
        "      ALIGNMENT_FLOAT32_IEEE 4",
        "      ALIGNMENT_FLOAT64_IEEE 8",
        "    /end MOD_COMMON",
        "",
        "    /begin IF_DATA XCP",
        "      /begin PROTOCOL_LAYER",
        "        0x0100",
        "        " + " ".join([str(T1_MS)] * 7) + "  /* T1..T7 [ms] */",
        f"        {MAX_CTO} {MAX_DTO}  /* MAX_CTO MAX_DTO */",
        f"        BYTE_ORDER_{order}",
        "        ADDRESS_GRANULARITY_BYTE",
    ]
    out += [f"        OPTIONAL_CMD {cmd}" for cmd in OPTIONAL_COMMANDS]
    out += [
        f"        COMMUNICATION_MODE_SUPPORTED BLOCK MASTER {MAX_BS} 0",
        "      /end PROTOCOL_LAYER",
        "      /begin XCP_ON_CAN",
        "        0x0100",
        f"        CAN_ID_MASTER 0x{xcp.master_id:X}",
        f"        CAN_ID_SLAVE 0x{xcp.slave_id:X}",
    ]
    if xcp.bitrate:
        out.append(f"        BAUDRATE {xcp.bitrate}")
    out += [
        "        MAX_DLC_REQUIRED",
        "      /end XCP_ON_CAN",
        "    /end IF_DATA",
        "",
    ]

    # conversions (one per distinct rational function) and record layouts (one per element type)
    methods: dict[str, Parameter] = {"CM_IDENTITY": Parameter("", "", "UINT8", 0, 1, (0, 1), 1)}
    for p in image.parameters:
        methods.setdefault(compu_name(p), p)
    measured = group_arrays(image.signals)
    for s in measured:
        if s.scaled:
            methods.setdefault(compu_name(s), s)              # RTE-29: fixed point signal, same rational function
    for cm, p in methods.items():
        n0, n1 = p.numerator
        d = p.denominator
        # A2L RAT_FUNC gives the stored value from the physical one: stored = (d * phys - n0) / n1
        out += [
            f"    /begin COMPU_METHOD {cm} {_quote(f'phys = ({n0} + {n1} * stored) / {d}')}",
            f"      RAT_FUNC {_quote('%12.6')} {_quote('')}",
            f"      COEFFS 0 {d} {-n0} 0 0 {n1}",
            "    /end COMPU_METHOD",
        ]
    for kind in sorted({p.type for p in image.parameters}):
        dtype = TYPES[kind][0]
        out += [f"    /begin RECORD_LAYOUT RL_{dtype}", f"      FNC_VALUES 1 {dtype} ROW_DIR DIRECT",
                "    /end RECORD_LAYOUT"]
    out += calibration_definitions(image)                     # RTE-26: value tables, axis and record layouts
    if any(is_qualifier_signal(s) for s in measured):
        out += qualifier_definitions()                        # RTE-28: QLF_* as text, with QLF_NOT_AVAILABLE
    out.append("")

    for p in image.parameters:
        out += characteristic_lines(p, image)                 # RTE-26: VALUE/VAL_BLK as before, CURVE, MAP, AXIS_PTS
    for s in measured:
        dtype, _size, low, high = TYPES[s.type]
        long_id, text_unit = measurement_texts(s)           # TOOL-143: description and unit of a measurement
        # TOOL-150: unit/min/max of a port or measurement from swc.yaml (load_swc_limits, --project) - the bsw text
        # unit of a measurement (above) still wins if both happen to be given, so a component without --project
        # behaves exactly as before; a port never has a text unit, so it always takes the swc.yaml one
        info = (limits or {}).get(_signal_key(s.name))
        # RTE-29: the unit of a fixed point type (image) comes before the swc.yaml one, like its conversion
        unit = text_unit or s.unit or (info[0] if info else "")
        if s.scaled:
            low, high = physical_limits(s)                  # RTE-29: the stored range, converted
            compu = compu_name(s)
        else:
            compu = QLF_COMPU if is_qualifier_signal(s) else "CM_IDENTITY"
        if info is not None and info[1] is not None and info[2] is not None:
            low, high = info[1], info[2]                    # engineering limits (physical), else the type range
        out += [
            f"    /begin MEASUREMENT {_ident(s.name, 'signal')} {_quote(long_id)}",
            f"      {dtype} {compu} 0 0 {_num(low)} {_num(high)}",
            f"      ECU_ADDRESS 0x{s.address:08X}",
        ]
        if s.dim > 1:
            out.append(f"      MATRIX_DIM {s.dim}")      # RTE-25: array port, one MEASUREMENT (ASAP2 1.61)
        if unit:
            out.append(f"      PHYS_UNIT {_quote(unit)}")
        out.append("    /end MEASUREMENT")
    out += function_lines(image)                                                  # RTE-26: FUNCTION per component
    out += ["  /end MODULE", "/end PROJECT", ""]
    return "\n".join(out)


def generate(elf: Path, output: Path, rte_config: Path, xcp_config: Path, bit_timing: Path | None = None,
             project: Path | None = None, name: str = "nextbsw_app", aml: Path | None = None) -> list[str]:
    """Writes the A2L; returns warnings. Raises A2lError."""
    types = load_signal_types(rte_config)
    xcp = load_xcp_config(xcp_config)
    if bit_timing is not None:
        xcp.bitrate = load_bitrate(bit_timing, xcp.bus)
    warnings = check_project(xcp, project) if project is not None else []
    limits = load_swc_limits(project) if project is not None else {}              # TOOL-150
    image = read_image(elf, types, has_signal_scaling(rte_config))               # RTE-29
    apply_calibration(image, load_calibration(rte_config), Path(elf))             # RTE-26
    names = {}
    measured = group_arrays(image.signals)          # RTE-25: an array is one MEASUREMENT under its port name
    for kind, item in ([("signal", s) for s in image.signals] + [("signal", s) for s in measured if s.dim > 1]
                       + [("parameter", p) for p in image.parameters]):
        if item.name in names:
            raise A2lError(f"{elf}: {kind} {item.name} appears twice (also as {names[item.name]})")
        names[item.name] = kind
    text = genlicense.stamp(render(image, xcp, name, aml, Path(elf).name, limits), "out.a2l")
    try:
        Path(output).write_text(text, encoding="utf-8")
    except OSError as exc:
        raise A2lError(f"{output}: cannot write ({exc.strerror})") from exc
    characteristics = sum(1 for p in image.parameters if p.kind not in ("AXIS", "OWN_AXIS"))
    axes = sum(1 for p in image.parameters if p.kind == "AXIS")
    return warnings + image.notes + [
        f"{output}: {len(measured)} MEASUREMENT, {characteristics} CHARACTERISTIC"
        + (f", {axes} AXIS_PTS" if axes else "") + (f", EPK {image.epk}" if image.epk else ", no EPK in the image")
        + f", XCP on {xcp.bus} 0x{xcp.master_id:X}/0x{xcp.slave_id:X}"
        + (f" at {xcp.bitrate} bit/s" if xcp.bitrate else "")]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("elf", type=Path)
    ap.add_argument("-o", "--output", type=Path, required=True)
    ap.add_argument("--rte-config", type=Path, required=True, help="generated RteConfig.h")
    ap.add_argument("--xcp-config", type=Path, required=True, help="xcp/xcpCanConfiguration.h")
    ap.add_argument("--bit-timing", type=Path, help="generated bsp/can/canBitTimingConfiguration.h")
    ap.add_argument("--project", type=Path, help="project.yaml: identifier checks")
    ap.add_argument("--name", default="nextbsw_app")
    ap.add_argument("--aml", type=Path, help="AML file of the calibration tool, written as /include")
    args = ap.parse_args(argv)
    try:
        for line in generate(args.elf, args.output, args.rte_config, args.xcp_config, args.bit_timing,
                             args.project, args.name, args.aml):
            print(f"a2lgen: {line}")
    except A2lError as exc:
        print(f"a2lgen: error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
