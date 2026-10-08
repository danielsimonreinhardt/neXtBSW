"""ODX/PDX export of the diagnostic description (TOOL-43, decision diag-austausch 2026-09-21).

Writes what a diagnostic tester or a workshop system needs to talk to the ECU software of a project: one
diagnostic layer container (`.odx-d`, ODX model version 2.2.0) with one base variant, and on request a PDX
package (a zip with the `.odx-d` and a catalog `index.xml`).

Input: the project (project.yaml, checked by tools/nxconfig) and its diagnostic description (project.diag, ARCH-51;
without one tools/diaggen/default_diag.yaml as for the UDS jobs). The description must pass diaggen.check() - the
export describes what the generated jobs answer, never something they refuse.

What the layer contains, and where each value comes from (nothing is invented; a missing source is a message):

  sessions (10)         one service per session: request 10 <id>, response 50 <id> P2 P2*/10; the sessions are
                        the states of a state chart "Sessions", every service changes into its own state
  security (27)         per level a request seed service (27 <level>, response the seed, `seed_bytes`) and a send
                        key service (27 <level+1>, `key_bytes`); the levels are the states of a state chart
                        "SecurityAccess" besides "Locked", sending the key changes into the level
  data identifiers      per identifier a read service (22 <DID>) and, with `write`, a write service (2E <DID>);
  (22/2E)               the data is a structure of one data object per element: coded type (`type`,
                        `length_bytes`, `byte_order`), conversion (`factor`/`offset` linear, `values` as text
                        table), physical limits (`min`/`max`) and unit (`unit`)
  routines (31)         per step (start 01, stop 02, results 03) one service with its request and response
                        parameters, described like the elements of an identifier
  DTCs (19/14)          one DTC data object with every DTC (number, display code, text = `label` or the name,
                        level = severity); snapshot and extended data records as environment data per DTC, the
                        record number selects the record's content (19 04, 19 06); 19 02 by status mask, 14 for
                        all DTCs, per DTC group and per DTC
  preconditions         `read_sessions`, `write_sessions`, `sessions` and the security levels as precondition
                        states of the services
  groups                `group` of identifiers and routines as functional classes
  addresses             communication parameters of the base variant from project.yaml `diagnostics` (CAN ids,
                        ISO-TP block size, STmin, timeouts, filler byte), the bit rate of the diagnostic bus
                        (rte.yaml `uds.bus`, project `can.<bus>.bitrate`) and P2/P2* of the first session. They
                        name the usual parameters of the communication parameter subsets for ISO 15765-2
                        (transport) and ISO 15765-3 (UDS on CAN) the tester brings along; times in microseconds.

Delivery data that is no property of the ECU software (CHANGELOG ARCH-51, "keine Quelle"): revision label, date,
company and state of the document come from the caller (`Delivery`, command line options), never from diag.yaml.
A unit the table UNITS does not know is exported with its display name only and reported as a warning with the
YAML path of the element (no guessed SI dimension).

Not exported (open, see doc/changelog.d/TOOL-43.md): ECU variants and their patterns, audiences, the protocol layer
and the communication parameter subsets themselves, the bootloader (its own layer later).

Qt free. Command line: python -m tools.diaggen odx|pdx <project.yaml> -o <file>.
"""

import datetime
import io
import re
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import diaggen  # noqa: E402
import nxconfig  # noqa: E402
import nxconfig.config  # noqa: E402

MODEL_VERSION = "2.2.0"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
ET.register_namespace("xsi", XSI)
TOOL = "neXtBSW tools/diaggen (TOOL-43)"
ODX_D_MIME = "application/x-asam.odx.odx-d"

# Wire values of the services (ISO 14229-1 service identifiers and sub-functions)
SID_SESSION, SID_SECURITY, SID_READ, SID_WRITE, SID_ROUTINE = 0x10, 0x27, 0x22, 0x2E, 0x31
SID_READ_DTC, SID_CLEAR_DTC, SID_NEGATIVE = 0x19, 0x14, 0x7F
POSITIVE = 0x40
ROUTINE_STEPS = (("start", 0x01, "Start"), ("stop", 0x02, "Stop"), ("results", 0x03, "Results"))
REPORT_BY_STATUS_MASK, REPORT_SNAPSHOT, REPORT_EXTENDED = 0x02, 0x04, 0x06
ALL_DTCS = 0xFFFFFF
# severity of a DTC as LEVEL of the DTC (0: none; the higher, the more urgent)
SEVERITY_LEVEL = {"none": 0, "maintenance_only": 1, "check_at_next_halt": 2, "check_immediately": 3}
# the fault detection counter is signed (-128..127), the other counters of the fault memory are counts
SIGNED_COUNTERS = ("fault_detection_counter",)

# base data types of the coded values
CODED = {"uint8": ("A_UINT32", 8), "uint16": ("A_UINT32", 16), "uint32": ("A_UINT32", 32),
         "int8": ("A_INT32", 8), "int16": ("A_INT32", 16), "int32": ("A_INT32", 32), "float32": ("A_FLOAT32", 32)}

# Units the repository uses: display name -> (short name, SI factor, SI offset, exponents length, mass, time,
# current, temperature). SI value = factor * value + offset, e.g. 1 mV = 0.001 V. A unit without exponents is
# dimensionless (a count, a percentage).
UNITS = {
    "V": ("V", 1.0, 0.0, (2, 1, -3, -1, 0)), "mV": ("mV", 0.001, 0.0, (2, 1, -3, -1, 0)),
    "A": ("A", 1.0, 0.0, (0, 0, 0, 1, 0)), "mA": ("mA", 0.001, 0.0, (0, 0, 0, 1, 0)),
    "uA": ("uA", 1e-6, 0.0, (0, 0, 0, 1, 0)),
    "s": ("s", 1.0, 0.0, (0, 0, 1, 0, 0)), "ms": ("ms", 0.001, 0.0, (0, 0, 1, 0, 0)),
    "us": ("us", 1e-6, 0.0, (0, 0, 1, 0, 0)), "min": ("min", 60.0, 0.0, (0, 0, 1, 0, 0)),
    "h": ("h", 3600.0, 0.0, (0, 0, 1, 0, 0)),
    "Hz": ("Hz", 1.0, 0.0, (0, 0, -1, 0, 0)), "kHz": ("kHz", 1000.0, 0.0, (0, 0, -1, 0, 0)),
    "1/min": ("per_min", 1 / 60, 0.0, (0, 0, -1, 0, 0)), "rpm": ("rpm", 1 / 60, 0.0, (0, 0, -1, 0, 0)),
    "K": ("K", 1.0, 0.0, (0, 0, 0, 0, 1)), "degC": ("degC", 1.0, 273.15, (0, 0, 0, 0, 1)),
    "W": ("W", 1.0, 0.0, (2, 1, -3, 0, 0)), "mW": ("mW", 0.001, 0.0, (2, 1, -3, 0, 0)),
    "Ohm": ("Ohm", 1.0, 0.0, (2, 1, -3, -2, 0)), "mOhm": ("mOhm", 0.001, 0.0, (2, 1, -3, -2, 0)),
    "Ah": ("Ah", 3600.0, 0.0, (0, 0, 1, 1, 0)), "mAh": ("mAh", 3.6, 0.0, (0, 0, 1, 1, 0)),
    "Pa": ("Pa", 1.0, 0.0, (-1, 1, -2, 0, 0)), "kPa": ("kPa", 1000.0, 0.0, (-1, 1, -2, 0, 0)),
    "bar": ("bar", 100000.0, 0.0, (-1, 1, -2, 0, 0)),
    "m": ("m", 1.0, 0.0, (1, 0, 0, 0, 0)), "mm": ("mm", 0.001, 0.0, (1, 0, 0, 0, 0)),
    "km/h": ("km_per_h", 1 / 3.6, 0.0, (1, 0, -1, 0, 0)), "m/s": ("m_per_s", 1.0, 0.0, (1, 0, -1, 0, 0)),
    "Nm": ("Nm", 1.0, 0.0, (2, 1, -2, 0, 0)),
    "%": ("percent", 0.01, 0.0, (0, 0, 0, 0, 0)), "cycle": ("cycle", 1.0, 0.0, (0, 0, 0, 0, 0)),
    "count": ("count", 1.0, 0.0, (0, 0, 0, 0, 0)), "bit": ("bit", 1.0, 0.0, (0, 0, 0, 0, 0)),
    "byte": ("byte", 1.0, 0.0, (0, 0, 0, 0, 0)),
}
DIMENSION_TAGS = ("LENGTH-EXP", "MASS-EXP", "TIME-EXP", "CURRENT-EXP", "TEMPERATURE-EXP")
DIMENSION_KEYS = ("L", "M", "T", "I", "K")        # letters of the short names of the dimensions

# Communication parameters (subset, name) - the usual names of the ISO 15765-2 and -3 subsets
CP_TRANSPORT, CP_UDS, CP_CAN = "ISO_15765_2", "ISO_15765_3", "ISO_11898_2_DWCAN"


class OdxExportError(diaggen.DiagGenError):
    """The description cannot be exported; `messages` carry file, line and YAML path."""


@dataclass
class Delivery:
    """Data of the delivery, not of the ECU software: stands in the document header (ADMIN-DATA, COMPANY-DATA)."""

    revision: str = "1"
    date: datetime.date | None = None          # None: today
    company: str | None = None
    state: str = "draft"
    language: str | None = None                # e.g. "de-DE"; None: not stated

    @property
    def day(self) -> datetime.date:
        return self.date or datetime.date.today()


@dataclass
class OdxExport:
    name: str                                  # short name of the layer (the project)
    text: str                                  # the .odx-d document
    delivery: Delivery
    messages: list = field(default_factory=list)   # warnings with YAML path (nxconfig.Message)

    @property
    def file_name(self) -> str:
        return f"{self.name}.odx-d"


# ------------------------------------------------------------------------------------------------ helpers


def short_name(text: str) -> str:
    """A short name from any text: letters, digits and underscore, starting with a letter."""
    name = re.sub(r"[^A-Za-z0-9_]", "_", text).strip("_") or "X"
    return name if name[0].isalpha() else "N" + name


def number(value) -> str:
    """A number as the document writes it: integral values without a fraction, others as Python writes them
    (repr is the shortest text that reads back to the same float)."""
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, int):
        return str(value)
    value = float(value)
    return str(int(value)) if value.is_integer() and abs(value) < 2 ** 53 else repr(value)


def sub(parent, tag: str, text=None, **attributes) -> ET.Element:
    element = ET.SubElement(parent, tag, {k.replace("_", "-"): str(v) for k, v in attributes.items()
                                          if v is not None})
    if text is not None:
        element.text = str(text)
    return element


def names(parent, name: str, label: str | None = None, description: str | None = None) -> None:
    sub(parent, "SHORT-NAME", name)
    if label:
        sub(parent, "LONG-NAME", label)
    if description:
        desc = sub(parent, "DESC")
        sub(desc, "p", description)


def ref(parent, tag: str, target: str) -> ET.Element:
    element = sub(parent, tag)
    element.set("ID-REF", target)
    return element


def coded_type(parent, base: str, bits: int, big_endian: bool = True) -> ET.Element:
    element = sub(parent, "DIAG-CODED-TYPE")
    element.set("BASE-DATA-TYPE", base)
    if not big_endian:
        element.set("IS-HIGHLOW-BYTE-ORDER", "false")
    element.set(f"{{{XSI}}}type", "STANDARD-LENGTH-TYPE")
    sub(element, "BIT-LENGTH", bits)
    return element


def identical(parent) -> None:
    sub(sub(parent, "COMPU-METHOD"), "CATEGORY", "IDENTICAL")


class Layer:
    """Builds the base variant; every object gets an ID below the layer's prefix."""

    def __init__(self, name: str):
        self.name = name
        self.dops: list[ET.Element] = []
        self.dtc_dops: list[ET.Element] = []
        self.env_data_descs: list[ET.Element] = []
        self.env_datas: list[ET.Element] = []
        self.structures: list[ET.Element] = []
        self.end_of_pdu_fields: list[ET.Element] = []
        self.muxs: list[ET.Element] = []
        self.units: dict[str, ET.Element] = {}
        self.dimensions: dict[tuple, ET.Element] = {}
        self.services: list[ET.Element] = []
        self.requests: list[ET.Element] = []
        self.pos_responses: list[ET.Element] = []
        self.neg_responses: list[ET.Element] = []
        self.funct_classes: dict[str, str] = {}
        self.negative_response: str | None = None     # ID of the negative response every service refers to
        self._ids: set[str] = set()

    def id(self, kind: str, *parts: str) -> str:
        identifier = ".".join((self.name, kind) + tuple(short_name(p) for p in parts))
        if identifier in self._ids:
            # two names of different kinds meet in one ODX name (e.g. routine Read with step Start and the
            # identifier Start: both Read_Start) - rename one of them
            raise OdxExportError([nxconfig.Message(
                "error", f"the ODX name {identifier} would stand twice in the export: rename one of the entries "
                         "whose names form it")])
        self._ids.add(identifier)
        return identifier

    def element(self, tag: str, kind: str, *parts: str, name: str | None = None, label=None, description=None,
                **attributes) -> ET.Element:
        element = ET.Element(tag)
        element.set("ID", self.id(kind, *parts))
        for key, value in attributes.items():
            if value is not None:
                element.set(key.replace("_", "-"), str(value))
        names(element, name or short_name(parts[-1]), label, description)
        return element

    # -------------------------------------------------------------- data objects

    def unit(self, display: str) -> tuple[str, bool]:
        """(ID, known) of the unit with this display name. A unit the table UNITS does not know gets its display
        name only, without SI factor and dimension (the caller reports it)."""
        spec = UNITS.get(display)
        if display not in self.units:
            if spec is None:
                short = "u_" + short_name(display)
                unit = self.element("UNIT", "UNIT", short, name=short)
                sub(unit, "DISPLAY-NAME", display)
            else:
                short, factor, offset, exponents = spec
                unit = self.element("UNIT", "UNIT", short, name=short)
                sub(unit, "DISPLAY-NAME", display)
                sub(unit, "FACTOR-SI-TO-UNIT", number(factor))
                sub(unit, "OFFSET-SI-TO-UNIT", number(offset))
                ref(unit, "PHYSICAL-DIMENSION-REF", self.dimension(exponents))
            self.units[display] = unit
        return self.units[display].get("ID"), spec is not None

    def dimension(self, exponents: tuple) -> str:
        if exponents not in self.dimensions:
            key = "_".join(f"{k}{e}".replace("-", "m") for k, e in zip(DIMENSION_KEYS, exponents) if e) or "none"
            dimension = self.element("PHYSICAL-DIMENSION", "DIM", key, name=short_name("dim_" + key))
            for tag, exponent in zip(DIMENSION_TAGS, exponents):
                if exponent:
                    sub(dimension, tag, exponent)
            self.dimensions[exponents] = dimension
        return self.dimensions[exponents].get("ID")

    def value_dop(self, parameter, *parts: str) -> tuple[str, str | None]:
        """Data object of one element or routine parameter (model.DiagParameter): (ID, display name of a unit
        the table does not know)."""
        dop = self.element("DATA-OBJECT-PROP", "DOP", *parts, name=short_name("_".join(parts)),
                           label=parameter.label, description=parameter.description)
        unknown_unit = None
        text_type = parameter.type in ("ascii", "bytes")
        linear = parameter.factor != 1.0 or parameter.offset != 0.0
        compu = sub(dop, "COMPU-METHOD")
        if parameter.values is not None:
            sub(compu, "CATEGORY", "TEXTTABLE")
            scales = sub(sub(compu, "COMPU-INTERNAL-TO-PHYS"), "COMPU-SCALES")
            for entry in parameter.values:
                scale = sub(scales, "COMPU-SCALE")
                sub(scale, "LOWER-LIMIT", entry.value, INTERVAL_TYPE="CLOSED")
                sub(scale, "UPPER-LIMIT", entry.value, INTERVAL_TYPE="CLOSED")
                sub(sub(scale, "COMPU-CONST"), "VT", entry.text)
        elif linear:
            sub(compu, "CATEGORY", "LINEAR")
            scale = sub(sub(sub(compu, "COMPU-INTERNAL-TO-PHYS"), "COMPU-SCALES"), "COMPU-SCALE")
            coeffs = sub(scale, "COMPU-RATIONAL-COEFFS")
            numerator = sub(coeffs, "COMPU-NUMERATOR")
            sub(numerator, "V", number(parameter.offset))
            sub(numerator, "V", number(parameter.factor))
            sub(sub(coeffs, "COMPU-DENOMINATOR"), "V", "1")
        else:
            sub(compu, "CATEGORY", "IDENTICAL")
        if parameter.type == "ascii":
            coded_type(dop, "A_ASCIISTRING", 8 * parameter.width_bytes)
            physical = "A_UNICODE2STRING"
        elif parameter.type == "bytes":
            coded_type(dop, "A_BYTEFIELD", 8 * parameter.width_bytes)
            physical = "A_BYTEFIELD"
        else:
            base, bits = CODED[parameter.type]
            coded_type(dop, base, bits, parameter.byte_order == "big")
            physical = ("A_UNICODE2STRING" if parameter.values is not None else "A_FLOAT64" if linear else base)
        sub(dop, "PHYSICAL-TYPE", BASE_DATA_TYPE=physical)
        if parameter.unit and not text_type:
            unit, known = self.unit(parameter.unit)
            ref(dop, "UNIT-REF", unit)
            if not known:
                unknown_unit = parameter.unit
        if parameter.min is not None or parameter.max is not None:
            constraint = sub(dop, "PHYS-CONSTR")
            if parameter.min is not None:
                sub(constraint, "LOWER-LIMIT", number(parameter.min), INTERVAL_TYPE="CLOSED")
            else:
                sub(constraint, "LOWER-LIMIT", INTERVAL_TYPE="INFINITE")
            if parameter.max is not None:
                sub(constraint, "UPPER-LIMIT", number(parameter.max), INTERVAL_TYPE="CLOSED")
            else:
                sub(constraint, "UPPER-LIMIT", INTERVAL_TYPE="INFINITE")
        self.dops.append(dop)
        return dop.get("ID"), unknown_unit

    def plain_dop(self, name: str, base: str, bits: int, unit: str | None = None, factor: float = 1.0,
                  label: str | None = None) -> str:
        dop = self.element("DATA-OBJECT-PROP", "DOP", name, name=name, label=label)
        if factor != 1.0:
            compu = sub(dop, "COMPU-METHOD")
            sub(compu, "CATEGORY", "LINEAR")
            coeffs = sub(sub(sub(sub(compu, "COMPU-INTERNAL-TO-PHYS"), "COMPU-SCALES"), "COMPU-SCALE"),
                         "COMPU-RATIONAL-COEFFS")
            numerator = sub(coeffs, "COMPU-NUMERATOR")
            sub(numerator, "V", "0")
            sub(numerator, "V", number(factor))
            sub(sub(coeffs, "COMPU-DENOMINATOR"), "V", "1")
        else:
            identical(dop)
        coded_type(dop, base, bits)
        sub(dop, "PHYSICAL-TYPE", BASE_DATA_TYPE=base if base != "A_BYTEFIELD" else "A_BYTEFIELD")
        if unit is not None:
            ref(dop, "UNIT-REF", self.unit(unit)[0])
        self.dops.append(dop)
        return dop.get("ID")

    # -------------------------------------------------------------- parameters

    @staticmethod
    def params(parent) -> ET.Element:
        return sub(parent, "PARAMS")

    @staticmethod
    def coded_const(params, name: str, position: int, value: int, bits: int, semantic: str | None = None,
                    base: str = "A_UINT32") -> int:
        param = sub(params, "PARAM", SEMANTIC=semantic)
        param.set(f"{{{XSI}}}type", "CODED-CONST")
        sub(param, "SHORT-NAME", name)
        sub(param, "BYTE-POSITION", position)
        sub(param, "CODED-VALUE", value)
        coded_type(param, base, bits)
        return position + bits // 8

    @staticmethod
    def value(params, name: str, position: int, dop: str, label: str | None = None,
              semantic: str | None = None) -> None:
        param = sub(params, "PARAM", SEMANTIC=semantic)
        param.set(f"{{{XSI}}}type", "VALUE")
        names(param, name, label)
        sub(param, "BYTE-POSITION", position)
        ref(param, "DOP-REF", dop)

    def message(self, tag: str, kind: str, name: str) -> tuple[ET.Element, ET.Element]:
        message = self.element(tag, kind, name, name=name)
        {"REQUEST": self.requests, "POS-RESPONSE": self.pos_responses,
         "NEG-RESPONSE": self.neg_responses}[tag].append(message)
        return message, self.params(message)

    def service(self, name: str, request: ET.Element, response: ET.Element, *, semantic: str, label=None,
                description=None, preconditions=(), transitions=(), funct_class: str | None = None,
                sdgs: dict | None = None) -> ET.Element:
        service = self.element("DIAG-SERVICE", "DS", name, name=name, label=label, description=description,
                               SEMANTIC=semantic)
        if sdgs:
            groups = sub(service, "SDGS")
            group = sub(groups, "SDG", SI="neXtBSW")
            for key, value in sdgs.items():
                sub(group, "SD", value, SI=key)
        if funct_class is not None:
            ref(sub(service, "FUNCT-CLASS-REFS"), "FUNCT-CLASS-REF", self.funct_class(funct_class))
        if preconditions:
            block = sub(service, "PRE-CONDITION-STATE-REFS")
            for state in preconditions:
                ref(block, "PRE-CONDITION-STATE-REF", state)
        if transitions:
            block = sub(service, "STATE-TRANSITION-REFS")
            for transition in transitions:
                ref(block, "STATE-TRANSITION-REF", transition)
        ref(service, "REQUEST-REF", request.get("ID"))
        ref(sub(service, "POS-RESPONSE-REFS"), "POS-RESPONSE-REF", response.get("ID"))
        ref(sub(service, "NEG-RESPONSE-REFS"), "NEG-RESPONSE-REF", self.negative_response)
        self.services.append(service)
        return service

    def funct_class(self, group: str) -> str:
        if group not in self.funct_classes:
            self.funct_classes[group] = self.id("FC", group)
        return self.funct_classes[group]


# ------------------------------------------------------------------------------------------------ export


class _Export:
    def __init__(self, d: diaggen.Description, config, name: str, delivery: Delivery):
        self.d = d
        self.desc = d.desc
        self.config = config
        self.delivery = delivery
        self.layer = Layer(name)
        self.messages: list = []
        self.session_states: dict[str, str] = {}
        self.session_transitions: dict[str, list[str]] = {}
        self.security_states: dict[str, str] = {}
        self.security_transitions: dict[str, str] = {}
        self.did_structures: dict[str, tuple[str, int]] = {}      # name -> (structure ID, bytes)
        self.charts: list[ET.Element] = []
        self.comparams: list[tuple[str, str, str]] = []            # (subset, name, value)

    def warn(self, path: tuple, text: str) -> None:
        line = nxconfig.config.line_of(self.d.tree, path) if self.d.tree is not None else None
        self.messages.append(nxconfig.Message("warning", text, self.d.file, tuple(path), line))

    def unit_warning(self, unknown: str | None, path: tuple) -> None:
        if unknown is not None:
            self.warn(path, f"unit {unknown}: no SI dimension known to the ODX export (tools/diaggen/odxexport.py "
                            f"UNITS: {', '.join(UNITS)}); exported with its display name only, the tester cannot "
                            "convert it")

    # -------------------------------------------------------------- building blocks

    def run(self) -> str:
        layer = self.layer
        layer.negative_response = self.negative_response()
        self.sessions()
        self.security()
        self.data_identifiers()
        self.routines()
        self.fault_memory()
        self.communication_parameters()
        return self.document()

    def negative_response(self) -> str:
        layer = self.layer
        nrc = layer.plain_dop("NegativeResponseCode", "A_UINT32", 8, label="Negative response code")
        message, params = layer.message("NEG-RESPONSE", "NR", "NR_General")
        layer.coded_const(params, "SID_NR", 0, SID_NEGATIVE, 8, "SERVICE-ID")
        param = sub(params, "PARAM", SEMANTIC="SERVICEIDRQ")
        param.set(f"{{{XSI}}}type", "MATCHING-REQUEST-PARAM")
        sub(param, "SHORT-NAME", "SIDRQ_NR")
        sub(param, "BYTE-POSITION", 1)
        sub(param, "REQUEST-BYTE-POS", 0)
        sub(param, "BYTE-LENGTH", 1)
        layer.value(params, "NRC", 2, nrc, semantic="DATA")
        return message.get("ID")

    def session_refs(self, sessions) -> list[str]:
        return [self.session_states[n] for n in (sessions or []) if n in self.session_states]

    def security_refs(self, level) -> list[str]:
        return [self.security_states[level]] if level in self.security_states else []

    def sessions(self) -> None:
        layer = self.layer
        rows = diaggen.sessions_of(self.desc)
        chart = layer.element("STATE-CHART", "SC", "Sessions", name="Sessions", label="Diagnostic sessions")
        sub(chart, "SEMANTIC", "SESSION")
        transitions = sub(chart, "STATE-TRANSITIONS")
        start = next((r for r in rows if r.id == diaggen.DEFAULT_SESSION), rows[0])
        for target in rows:
            self.session_transitions[target.name] = []
            for source in rows:
                if source is target:
                    continue
                transition = layer.element("STATE-TRANSITION", "ST", f"{source.name}_to_{target.name}")
                sub(transition, "SOURCE-SNREF", SHORT_NAME=source.name)
                sub(transition, "TARGET-SNREF", SHORT_NAME=target.name)
                transitions.append(transition)
                self.session_transitions[target.name].append(transition.get("ID"))
        sub(chart, "START-STATE-SNREF", SHORT_NAME=start.name)
        states = sub(chart, "STATES")
        by_name = {s.name: s for s in self.desc.sessions}
        for row in rows:
            session = by_name.get(row.name)
            state = layer.element("STATE", "STATE", "Session", row.name, name=row.name,
                                  label=session.label if session is not None else None)
            states.append(state)
            self.session_states[row.name] = state.get("ID")
        self.charts.append(chart)
        p2 = layer.plain_dop("P2Server", "A_UINT32", 16, unit="ms", label="P2 server max")
        p2_star = layer.plain_dop("P2StarServer", "A_UINT32", 16, unit="ms", factor=10.0,
                                  label="P2* server max (10 ms resolution)")
        for row in rows:
            session = by_name.get(row.name)
            name = f"Session_{row.name}"
            request, params = layer.message("REQUEST", "RQ", name)
            position = layer.coded_const(params, "SID_RQ", 0, SID_SESSION, 8, "SERVICE-ID")
            layer.coded_const(params, "SessionType", position, row.id, 8, "SUBFUNCTION")
            response, params = layer.message("POS-RESPONSE", "PR", name)
            position = layer.coded_const(params, "SID_PR", 0, SID_SESSION + POSITIVE, 8, "SERVICE-ID")
            position = layer.coded_const(params, "SessionType", position, row.id, 8, "SUBFUNCTION")
            layer.value(params, "P2Server", position, p2)
            layer.value(params, "P2StarServer", position + 2, p2_star)
            layer.service(name, request, response, semantic="SESSION",
                          label=session.label if session is not None else None,
                          description=session.description if session is not None else None,
                          transitions=self.session_transitions[row.name],
                          sdgs={"p2_ms": row.p2_ms, "p2_star_ms": row.p2_star_ms})

    def security(self) -> None:
        if not self.desc.security:
            return
        layer = self.layer
        chart = layer.element("STATE-CHART", "SC", "SecurityAccess", name="SecurityAccess",
                              label="Security access")
        sub(chart, "SEMANTIC", "SECURITY")
        transitions = sub(chart, "STATE-TRANSITIONS")
        for level in self.desc.security:
            transition = layer.element("STATE-TRANSITION", "ST", f"Locked_to_{level.name}")
            sub(transition, "SOURCE-SNREF", SHORT_NAME="Locked")
            sub(transition, "TARGET-SNREF", SHORT_NAME=level.name)
            transitions.append(transition)
            self.security_transitions[level.name] = transition.get("ID")
        sub(chart, "START-STATE-SNREF", SHORT_NAME="Locked")
        states = sub(chart, "STATES")
        states.append(layer.element("STATE", "STATE", "Security", "Locked", name="Locked"))
        for level in self.desc.security:
            state = layer.element("STATE", "STATE", "Security", level.name, name=level.name, label=level.label)
            states.append(state)
            self.security_states[level.name] = state.get("ID")
        self.charts.append(chart)
        rows = diaggen.sessions_of(self.desc)
        for i, level in enumerate(self.desc.security):
            if level.sessions is None:
                allowed = [r.name for r in rows if r.id != diaggen.DEFAULT_SESSION]
            else:
                allowed = list(level.sessions)
            preconditions = self.session_refs(allowed)
            sdgs = {"method": level.method, "attempts": level.attempts, "delay_ms": level.delay_ms}
            seed = layer.plain_dop(f"Seed_{level.name}", "A_BYTEFIELD", 8 * level.seed_bytes)
            key = layer.plain_dop(f"Key_{level.name}", "A_BYTEFIELD", 8 * level.key_bytes)
            name = f"RequestSeed_{level.name}"
            request, params = layer.message("REQUEST", "RQ", name)
            position = layer.coded_const(params, "SID_RQ", 0, SID_SECURITY, 8, "SERVICE-ID")
            layer.coded_const(params, "SecurityAccessType", position, level.level, 8, "SUBFUNCTION")
            response, params = layer.message("POS-RESPONSE", "PR", name)
            position = layer.coded_const(params, "SID_PR", 0, SID_SECURITY + POSITIVE, 8, "SERVICE-ID")
            position = layer.coded_const(params, "SecurityAccessType", position, level.level, 8, "SUBFUNCTION")
            layer.value(params, "Seed", position, seed)
            layer.service(name, request, response, semantic="SECURITY", label=level.label,
                          description=level.description, preconditions=preconditions, sdgs=sdgs)
            name = f"SendKey_{level.name}"
            request, params = layer.message("REQUEST", "RQ", name)
            position = layer.coded_const(params, "SID_RQ", 0, SID_SECURITY, 8, "SERVICE-ID")
            position = layer.coded_const(params, "SecurityAccessType", position, level.level + 1, 8, "SUBFUNCTION")
            layer.value(params, "Key", position, key)
            response, params = layer.message("POS-RESPONSE", "PR", name)
            position = layer.coded_const(params, "SID_PR", 0, SID_SECURITY + POSITIVE, 8, "SERVICE-ID")
            layer.coded_const(params, "SecurityAccessType", position, level.level + 1, 8, "SUBFUNCTION")
            layer.service(name, request, response, semantic="SECURITY", label=level.label,
                          preconditions=preconditions, transitions=[self.security_transitions[level.name]])

    def structure(self, did, index: int) -> tuple[str, int]:
        """Structure of the data of one identifier (one value per element), (ID, bytes)."""
        if did.name in self.did_structures:
            return self.did_structures[did.name]
        layer = self.layer
        structure = layer.element("STRUCTURE", "STRUCT", did.name, name=did.name, label=did.label)
        sub(structure, "BYTE-SIZE", did.length_bytes)
        params = layer.params(structure)
        position = 0
        for j, element in enumerate(did.data):
            dop, unknown = layer.value_dop(element, did.name, element.name)
            self.unit_warning(unknown, ("data_identifiers", index, "data", j, "unit"))
            layer.value(params, element.name, position, dop, label=element.label)
            position += element.width_bytes
        layer.structures.append(structure)
        self.did_structures[did.name] = (structure.get("ID"), did.length_bytes)
        return self.did_structures[did.name]

    def data_identifiers(self) -> None:
        layer = self.layer
        for i, did in enumerate(self.desc.data_identifiers):
            structure, _length = self.structure(did, i)
            if did.read:
                name = f"Read_{did.name}"
                request, params = layer.message("REQUEST", "RQ", name)
                position = layer.coded_const(params, "SID_RQ", 0, SID_READ, 8, "SERVICE-ID")
                layer.coded_const(params, "DataIdentifier", position, did.identifier, 16, "ID")
                response, params = layer.message("POS-RESPONSE", "PR", name)
                position = layer.coded_const(params, "SID_PR", 0, SID_READ + POSITIVE, 8, "SERVICE-ID")
                position = layer.coded_const(params, "DataIdentifier", position, did.identifier, 16, "ID")
                layer.value(params, did.name, position, structure, semantic="DATA")
                layer.service(name, request, response, semantic="DATA-READ", label=did.label,
                              description=did.description, funct_class=did.group,
                              preconditions=self.session_refs(did.read_sessions) + self.security_refs(
                                  did.read_security))
            if did.write:
                name = f"Write_{did.name}"
                request, params = layer.message("REQUEST", "RQ", name)
                position = layer.coded_const(params, "SID_RQ", 0, SID_WRITE, 8, "SERVICE-ID")
                position = layer.coded_const(params, "DataIdentifier", position, did.identifier, 16, "ID")
                layer.value(params, did.name, position, structure, semantic="DATA")
                response, params = layer.message("POS-RESPONSE", "PR", name)
                position = layer.coded_const(params, "SID_PR", 0, SID_WRITE + POSITIVE, 8, "SERVICE-ID")
                layer.coded_const(params, "DataIdentifier", position, did.identifier, 16, "ID")
                layer.service(name, request, response, semantic="DATA-WRITE", label=did.label,
                              description=did.description, funct_class=did.group,
                              preconditions=self.session_refs(did.write_sessions) + self.security_refs(
                                  did.write_security))

    def routines(self) -> None:
        layer = self.layer
        for i, routine in enumerate(self.desc.routines):
            preconditions = self.session_refs(routine.sessions) + self.security_refs(routine.security)
            for step, subfunction, title in ROUTINE_STEPS:
                value = getattr(routine, step)
                if value is None:
                    continue
                name = f"{routine.name}_{title}"
                request, params = layer.message("REQUEST", "RQ", name)
                position = layer.coded_const(params, "SID_RQ", 0, SID_ROUTINE, 8, "SERVICE-ID")
                position = layer.coded_const(params, "RoutineControlType", position, subfunction, 8, "SUBFUNCTION")
                position = layer.coded_const(params, "RoutineIdentifier", position, routine.identifier, 16, "ID")
                for k, parameter in enumerate(value.request):
                    dop, unknown = layer.value_dop(parameter, routine.name, step, "request", parameter.name)
                    self.unit_warning(unknown, ("routines", i, step, "request", k, "unit"))
                    layer.value(params, parameter.name, position, dop, label=parameter.label, semantic="DATA")
                    position += parameter.width_bytes
                response, params = layer.message("POS-RESPONSE", "PR", name)
                position = layer.coded_const(params, "SID_PR", 0, SID_ROUTINE + POSITIVE, 8, "SERVICE-ID")
                position = layer.coded_const(params, "RoutineControlType", position, subfunction, 8, "SUBFUNCTION")
                position = layer.coded_const(params, "RoutineIdentifier", position, routine.identifier, 16, "ID")
                for k, parameter in enumerate(value.response):
                    dop, unknown = layer.value_dop(parameter, routine.name, step, "response", parameter.name)
                    self.unit_warning(unknown, ("routines", i, step, "response", k, "unit"))
                    layer.value(params, parameter.name, position, dop, label=parameter.label, semantic="DATA")
                    position += parameter.width_bytes
                layer.service(name, request, response, semantic="ROUTINE", label=routine.label,
                              description=routine.description if step == "start" else None,
                              funct_class=routine.group, preconditions=preconditions)

    # -------------------------------------------------------------- fault memory

    def fault_memory(self) -> None:
        desc = self.desc
        if not desc.dtcs:
            return
        layer = self.layer
        dtc_dop = layer.element("DTC-DOP", "DOP", "DTC", name="DTC", label="Diagnostic trouble code")
        coded_type(dtc_dop, "A_UINT32", 24)
        sub(dtc_dop, "PHYSICAL-TYPE", BASE_DATA_TYPE="A_UINT32", DISPLAY_RADIX="HEX")
        identical(dtc_dop)
        dtcs = sub(dtc_dop, "DTCS")
        for dtc in desc.dtcs:
            entry = sub(dtcs, "DTC")
            entry.set("ID", layer.id("DTC", dtc.name))
            sub(entry, "SHORT-NAME", dtc.name)
            sub(entry, "TROUBLE-CODE", dtc.number)
            if dtc.display_code:
                sub(entry, "DISPLAY-TROUBLE-CODE", dtc.display_code)
            sub(entry, "TEXT", dtc.label or dtc.name)
            sub(entry, "LEVEL", SEVERITY_LEVEL[dtc.severity])
            special = {"severity": dtc.severity, "priority": dtc.priority, "group": dtc.group,
                       "description": dtc.description}
            group = sub(sub(entry, "SDGS"), "SDG", SI="neXtBSW")
            for key, value in special.items():
                if value is not None:
                    sub(group, "SD", value, SI=key)
        layer.dtc_dops.append(dtc_dop)
        dtc_ref = dtc_dop.get("ID")
        status = layer.plain_dop("DTCStatus", "A_UINT32", 8, label="Status of DTC")
        mask = layer.plain_dop("DTCStatusMask", "A_UINT32", 8, label="DTC status mask")
        record = layer.plain_dop("DTCRecordNumber", "A_UINT32", 8, label="Record number")
        counter = layer.plain_dop("FaultMemoryCounter", "A_UINT32", 8, label="Counter of the fault memory")
        signed_counter = layer.plain_dop("FaultDetectionCounter", "A_INT32", 8, label="Fault detection counter")

        # 19 02: DTC and status of every DTC matching the mask, up to the end of the response
        pair = layer.element("STRUCTURE", "STRUCT", "DTCAndStatus", name="DTCAndStatus")
        params = layer.params(pair)
        layer.value(params, "DTC", 0, dtc_ref)
        layer.value(params, "DTCStatus", 3, status)
        layer.structures.append(pair)
        records = layer.element("END-OF-PDU-FIELD", "EOPF", "DTCAndStatusRecords", name="DTCAndStatusRecords")
        ref(records, "BASIC-STRUCTURE-REF", pair.get("ID"))
        layer.end_of_pdu_fields.append(records)
        name = "ReadDTC_ByStatusMask"
        request, params = layer.message("REQUEST", "RQ", name)
        position = layer.coded_const(params, "SID_RQ", 0, SID_READ_DTC, 8, "SERVICE-ID")
        position = layer.coded_const(params, "ReportType", position, REPORT_BY_STATUS_MASK, 8, "SUBFUNCTION")
        layer.value(params, "DTCStatusMask", position, mask)
        response, params = layer.message("POS-RESPONSE", "PR", name)
        position = layer.coded_const(params, "SID_PR", 0, SID_READ_DTC + POSITIVE, 8, "SERVICE-ID")
        position = layer.coded_const(params, "ReportType", position, REPORT_BY_STATUS_MASK, 8, "SUBFUNCTION")
        layer.value(params, "DTCStatusAvailabilityMask", position, mask)
        layer.value(params, "DTCAndStatusRecords", position + 1, records.get("ID"), semantic="DATA")
        layer.service(name, request, response, semantic="FAULTREAD", label="Report DTC by status mask")

        # snapshot and extended data records: environment data per DTC, the record number selects the content
        # (a multiplexer per DTC, one case per record - a tester picks the environment data by the DTC alone)
        snapshot_datas, extended_datas = [], []
        for dtc in desc.dtcs:
            cases = []
            for snapshot in dtc.snapshots:
                case = layer.element("STRUCTURE", "STRUCT", dtc.name, f"Snapshot{snapshot.record_number:02X}",
                                     name=f"{dtc.name}_Snapshot{snapshot.record_number:02X}",
                                     description=snapshot.description)
                params = layer.params(case)
                position = layer.coded_const(params, "NumberOfIdentifiers", 0, len(snapshot.dids), 8)
                for did_name in snapshot.dids:
                    index, did = self.did_by_name(did_name)
                    structure, length = self.structure(did, index)
                    position = layer.coded_const(params, f"Id_{did.name}", position, did.identifier, 16, "ID")
                    layer.value(params, did.name, position, structure)
                    position += length
                layer.structures.append(case)
                cases.append((snapshot, case))
            if cases:
                snapshot_datas.append(self.records(dtc, "Snapshot", "DTCSnapshotRecord", cases, record))
            cases = []
            for extended in dtc.extended_data:
                case = layer.element("STRUCTURE", "STRUCT", dtc.name, f"Extended{extended.record_number:02X}",
                                     name=f"{dtc.name}_Extended{extended.record_number:02X}",
                                     description=extended.description)
                params = layer.params(case)
                position = 0
                for item in extended.data:
                    if item.counter is not None:
                        dop = signed_counter if item.counter in SIGNED_COUNTERS else counter
                        layer.value(params, item.name, position, dop, label=item.label, semantic=item.counter)
                        position += 1
                    else:
                        index, did = self.did_by_name(item.did)
                        structure, length = self.structure(did, index)
                        layer.value(params, item.name, position, structure, label=item.label,
                                    semantic=f"DID.{did.name}")
                        position += length
                layer.structures.append(case)
                cases.append((extended, case))
            if cases:
                extended_datas.append(self.records(dtc, "Extended", "DTCExtDataRecord", cases, record))
        for key, subfunction, datas, title in (
                ("Snapshot", REPORT_SNAPSHOT, snapshot_datas, "Report DTC snapshot record by DTC number"),
                ("ExtendedData", REPORT_EXTENDED, extended_datas, "Report DTC extended data record by DTC number")):
            if not datas:
                continue
            desc_element = layer.element("ENV-DATA-DESC", "EDD", key, name=f"{key}Records")
            sub(desc_element, "PARAM-SNREF", SHORT_NAME="DTC")
            block = sub(desc_element, "ENV-DATA-REFS")
            for env in datas:
                ref(block, "ENV-DATA-REF", env)
            layer.env_data_descs.append(desc_element)
            name = f"ReadDTC_{key}"
            request, params = layer.message("REQUEST", "RQ", name)
            position = layer.coded_const(params, "SID_RQ", 0, SID_READ_DTC, 8, "SERVICE-ID")
            position = layer.coded_const(params, "ReportType", position, subfunction, 8, "SUBFUNCTION")
            layer.value(params, "DTC", position, dtc_ref)
            layer.value(params, "RecordNumber", position + 3, record)
            response, params = layer.message("POS-RESPONSE", "PR", name)
            position = layer.coded_const(params, "SID_PR", 0, SID_READ_DTC + POSITIVE, 8, "SERVICE-ID")
            position = layer.coded_const(params, "ReportType", position, subfunction, 8, "SUBFUNCTION")
            layer.value(params, "DTC", position, dtc_ref)
            layer.value(params, "DTCStatus", position + 3, status)
            layer.value(params, "Record", position + 4, desc_element.get("ID"), semantic="DATA")
            layer.service(name, request, response, semantic="FAULTREAD", label=title)

        # 14: all DTCs, every group of dtc_groups, a single DTC
        clears = [("ClearDTC_All", ALL_DTCS, "Clear all DTCs", None)]
        clears += [(f"ClearDTC_Group_{g.name}", g.group, g.label, g) for g in desc.dtc_groups]
        for name, value, label, group in clears:
            request, params = layer.message("REQUEST", "RQ", name)
            position = layer.coded_const(params, "SID_RQ", 0, SID_CLEAR_DTC, 8, "SERVICE-ID")
            layer.coded_const(params, "GroupOfDTC", position, value, 24)
            response, params = layer.message("POS-RESPONSE", "PR", name)
            layer.coded_const(params, "SID_PR", 0, SID_CLEAR_DTC + POSITIVE, 8, "SERVICE-ID")
            sdgs = None if group is None else {"group": group.name, "first": group.first, "last": group.last}
            layer.service(name, request, response, semantic="FAULTCLEAR", label=label,
                          description=None if group is None else group.description, sdgs=sdgs)
        name = "ClearDTC_Single"
        request, params = layer.message("REQUEST", "RQ", name)
        position = layer.coded_const(params, "SID_RQ", 0, SID_CLEAR_DTC, 8, "SERVICE-ID")
        layer.value(params, "DTC", position, dtc_ref)
        response, params = layer.message("POS-RESPONSE", "PR", name)
        layer.coded_const(params, "SID_PR", 0, SID_CLEAR_DTC + POSITIVE, 8, "SERVICE-ID")
        layer.service(name, request, response, semantic="FAULTCLEAR", label="Clear a single DTC")

    def records(self, dtc, kind: str, param: str, cases: list, record_dop: str) -> str:
        """Environment data of one DTC: its record number (1 byte) selects the case of a multiplexer, one case
        per record of `kind`. Returns the ID of the environment data."""
        layer = self.layer
        mux = layer.element("MUX", "MUX", dtc.name, kind, name=f"{dtc.name}_{kind}Records")
        sub(mux, "BYTE-POSITION", 1)
        key = sub(mux, "SWITCH-KEY")
        sub(key, "BYTE-POSITION", 0)
        ref(key, "DATA-OBJECT-PROP-REF", record_dop)
        block = sub(mux, "CASES")
        for entry, structure in cases:
            case = sub(block, "CASE")
            names(case, f"Record{entry.record_number:02X}", entry.label)
            ref(case, "STRUCTURE-REF", structure.get("ID"))
            sub(case, "LOWER-LIMIT", entry.record_number, INTERVAL_TYPE="CLOSED")
            sub(case, "UPPER-LIMIT", entry.record_number, INTERVAL_TYPE="CLOSED")
        layer.muxs.append(mux)
        # one record or, when the tester asks for all of them (record number FF), several back to back
        item = layer.element("STRUCTURE", "STRUCT", dtc.name, f"{kind}Record", name=f"{dtc.name}_{kind}Record")
        layer.value(layer.params(item), param, 0, mux.get("ID"))
        layer.structures.append(item)
        field_ = layer.element("END-OF-PDU-FIELD", "EOPF", dtc.name, kind, name=f"{dtc.name}_{kind}List")
        ref(field_, "BASIC-STRUCTURE-REF", item.get("ID"))
        layer.end_of_pdu_fields.append(field_)
        env = layer.element("ENV-DATA", "ENV", dtc.name, kind, name=f"{dtc.name}_{kind}")
        layer.value(layer.params(env), f"{param}s", 0, field_.get("ID"))
        sub(sub(env, "DTC-VALUES"), "DTC-VALUE", dtc.number)
        layer.env_datas.append(env)
        return env.get("ID")

    def did_by_name(self, name: str):
        for index, did in enumerate(self.desc.data_identifiers):
            if did.name == name:
                return index, did
        raise OdxExportError([f"identifier {name} is not described"])   # nxconfig checks the names before

    # -------------------------------------------------------------- addresses

    def communication_parameters(self) -> None:
        project = self.config.project if self.config is not None else None
        diagnostics = getattr(project, "diagnostics", None)
        if diagnostics is None:
            if self.config is not None:
                self.messages.append(nxconfig.Message(
                    "warning", "the project has no diagnostics section (CAN identifiers, ISO-TP): the ODX export "
                               "has no communication parameters", self.config.project_file, ("diagnostics",),
                    nxconfig.config.line_of(self._project_tree(), ("diagnostics",))))
            return
        isotp = diagnostics.isotp
        rows = diaggen.sessions_of(self.desc)
        self.comparams += [
            (CP_TRANSPORT, "CP_CanPhysReqId", number(diagnostics.request_id)),
            (CP_TRANSPORT, "CP_CanRespUSDTId", number(diagnostics.response_id)),
            (CP_TRANSPORT, "CP_CanFuncReqId", number(diagnostics.functional_id)),
            (CP_TRANSPORT, "CP_BlockSize", number(isotp.block_size)),
            (CP_TRANSPORT, "CP_STmin", number(isotp.stmin_ms * 1000)),
            (CP_TRANSPORT, "CP_Cr", number(isotp.n_cr_ms * 1000)),
            (CP_TRANSPORT, "CP_Bs", number(isotp.n_bs_ms * 1000)),
            (CP_TRANSPORT, "CP_CanFillerByte", number(isotp.padding)),
            (CP_TRANSPORT, "CP_CanFillerByteHandling", "1"),
            (CP_UDS, "CP_P2Max", number(rows[0].p2_ms * 1000)),
            (CP_UDS, "CP_P2Star", number(rows[0].p2_star_ms * 1000)),
        ]
        bitrate = self._bitrate()
        if bitrate is not None:
            self.comparams.append((CP_CAN, "CP_Baudrate", number(bitrate)))

    def _project_tree(self):
        source = self.config.sources.get("project")
        return source.tree if source is not None else None

    def _bitrate(self) -> int | None:
        rte = getattr(self.config, "rte", None)
        bus = None
        if rte is not None:
            uds = getattr(rte, "uds", None)
            can = getattr(rte, "can", None)
            bus = uds.bus if uds is not None else getattr(can, "bus", None)
        can_use = getattr(self.config.project, "can", {}) or {}
        if bus is None and len(can_use) == 1:
            bus = next(iter(can_use))
        use = can_use.get(bus) if bus is not None else None
        return getattr(use, "bitrate", None)

    # -------------------------------------------------------------- document

    def document(self) -> str:
        layer, delivery = self.layer, self.delivery
        root = ET.Element("ODX", {"MODEL-VERSION": MODEL_VERSION})
        root.set(f"{{{XSI}}}noNamespaceSchemaLocation", "odx.xsd")
        container = sub(root, "DIAG-LAYER-CONTAINER")
        container.set("ID", f"{layer.name}.DLC")
        names(container, layer.name, f"Diagnostic description of {layer.name}")
        admin = sub(container, "ADMIN-DATA")
        if delivery.language:
            sub(admin, "LANGUAGE", delivery.language)
        revision = sub(sub(admin, "DOC-REVISIONS"), "DOC-REVISION")
        sub(revision, "REVISION-LABEL", delivery.revision)
        sub(revision, "STATE", delivery.state)
        sub(revision, "DATE", f"{delivery.day.isoformat()}T00:00:00")
        sub(revision, "TOOL", TOOL)
        if delivery.company:
            company = sub(sub(container, "COMPANY-DATAS"), "COMPANY-DATA")
            company.set("ID", f"{layer.name}.CD.{short_name(delivery.company)}")
            names(company, short_name(delivery.company), delivery.company)
        variant = sub(sub(container, "BASE-VARIANTS"), "BASE-VARIANT")
        variant.set("ID", f"{layer.name}.BV")
        names(variant, layer.name, f"ECU software {layer.name}",
              f"Exported by {TOOL} from {self.d.file.name}.")
        if layer.funct_classes:
            classes = sub(variant, "FUNCT-CLASSS")
            for group, identifier in layer.funct_classes.items():
                element = sub(classes, "FUNCT-CLASS")
                element.set("ID", identifier)
                sub(element, "SHORT-NAME", short_name(group))
        dictionary = sub(variant, "DIAG-DATA-DICTIONARY-SPEC")
        for tag, items in (("DTC-DOPS", layer.dtc_dops), ("ENV-DATA-DESCS", layer.env_data_descs),
                           ("DATA-OBJECT-PROPS", layer.dops), ("STRUCTURES", layer.structures),
                           ("END-OF-PDU-FIELDS", layer.end_of_pdu_fields), ("MUXS", layer.muxs),
                           ("ENV-DATAS", layer.env_datas)):
            if items:
                sub(dictionary, tag).extend(items)
        if layer.units:
            spec = sub(dictionary, "UNIT-SPEC")
            sub(spec, "UNITS").extend(layer.units.values())
            sub(spec, "PHYSICAL-DIMENSIONS").extend(layer.dimensions.values())
        sub(variant, "DIAG-COMMS").extend(layer.services)
        sub(variant, "REQUESTS").extend(layer.requests)
        sub(variant, "POS-RESPONSES").extend(layer.pos_responses)
        sub(variant, "NEG-RESPONSES").extend(layer.neg_responses)
        sub(variant, "STATE-CHARTS").extend(self.charts)
        if self.comparams:
            block = sub(variant, "COMPARAM-REFS")
            for subset, name, value in self.comparams:
                element = sub(block, "COMPARAM-REF", ID_REF=f"{subset}.{name}", DOCREF=subset,
                              DOCTYPE="COMPARAM-SUBSET")
                sub(element, "SIMPLE-VALUE", value)
        ET.indent(root, space="  ")
        return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def layer_name(config, d: diaggen.Description) -> str:
    project = getattr(config, "project", None)
    name = getattr(getattr(project, "project", None), "name", None) if project is not None else None
    return short_name(name or d.file.stem)


def export_description(d: diaggen.Description, config=None, delivery: Delivery | None = None,
                       name: str | None = None) -> OdxExport:
    """ODX document of a loaded description (diaggen.Description). Raises OdxExportError with the messages of
    diaggen.check() when the description has errors; warnings stay in the result."""
    diaggen.check(d)
    errors = [m for m in d.messages if m.severity == "error"]
    if errors:
        raise OdxExportError(d.messages)
    delivery = delivery or Delivery()
    job = _Export(d, config, name or layer_name(config, d), delivery)
    text = job.run()
    return OdxExport(job.layer.name, text, delivery, [m for m in d.messages if m.severity != "error"] + job.messages)


def export_project(project_file: Path, mcudb: Path | None = None, running_tree: Path | None = None,
                   delivery: Delivery | None = None) -> OdxExport:
    """ODX document of a project (checked by tools/nxconfig first; `running_tree` as in diaggen.validate())."""
    try:
        config = nxconfig.load(Path(project_file).resolve(), mcudb or nxconfig.config.mcudb_dir(running_tree),
                               running_tree=running_tree)
    except nxconfig.ConfigErrors as e:
        raise OdxExportError(e.messages if hasattr(e, "messages") else [str(e)]) from None
    return export_description(diaggen.description_of(config), config, delivery)


# ------------------------------------------------------------------------------------------------ files


def index_xml(export: OdxExport) -> str:
    """Catalog of a PDX package: one block with the .odx-d."""
    root = ET.Element("CATALOG", {"F-DTD-VERSION": f"ODX-{MODEL_VERSION}"})
    root.set(f"{{{XSI}}}noNamespaceSchemaLocation", "odx-cc.xsd")
    sub(root, "SHORT-NAME", export.name)
    block = sub(sub(root, "ABLOCKS"), "ABLOCK", UPD="UNCHANGED")
    sub(block, "SHORT-NAME", export.name)
    files = sub(block, "FILES")
    sub(files, "FILE", export.file_name, CREATION_DATE=f"{export.delivery.day.isoformat()}T00:00:00",
        MIME_TYPE=ODX_D_MIME)
    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def pdx_bytes(export: OdxExport) -> bytes:
    """PDX package: zip with index.xml and the .odx-d, reproducible (fixed time stamp = delivery date)."""
    stamp = export.delivery.day.timetuple()[:3] + (0, 0, 0)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in (("index.xml", index_xml(export)), (export.file_name, export.text)):
            info = zipfile.ZipInfo(name, date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, text.encode("utf-8"))
    return buffer.getvalue()


def write(export: OdxExport, path: Path, kind: str | None = None) -> Path:
    """Writes the export as .odx-d or .pdx (kind None: from the suffix of `path`); returns the path written.

    Written through a temporary file next to the target, so a failed write never leaves half a file."""
    path = Path(path)
    kind = kind or ("pdx" if path.suffix.lower() == ".pdx" else "odx")
    data = pdx_bytes(export) if kind == "pdx" else export.text.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)
    return path


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m tools.diaggen odx|pdx",
                                     description="ODX/PDX export of the diagnostic description (TOOL-43)")
    parser.add_argument("kind", choices=("odx", "pdx"), help="odx: one .odx-d, pdx: package with index.xml")
    parser.add_argument("project", type=Path, help="project (projects/<project>/project.yaml or its folder)")
    parser.add_argument("-o", "--output", type=Path, help="file to write; default <layer>.odx-d / .pdx in the "
                                                         "current directory")
    parser.add_argument("--revision", default="1", help="revision label of the document (default 1)")
    parser.add_argument("--date", help="date of the revision, YYYY-MM-DD (default today)")
    parser.add_argument("--company", help="company of the delivery (COMPANY-DATA)")
    parser.add_argument("--state", default="draft", help="state of the revision (default draft)")
    parser.add_argument("--language", help="language of the texts, e.g. de-DE")
    args = parser.parse_args(argv)
    project = args.project / "project.yaml" if args.project.is_dir() else args.project
    try:
        day = datetime.date.fromisoformat(args.date) if args.date else None
    except ValueError:
        parser.error(f"--date {args.date}: expected YYYY-MM-DD")
    delivery = Delivery(args.revision, day, args.company, args.state, args.language)
    try:
        export = export_project(project, delivery=delivery)
    except diaggen.DiagGenError as e:
        for m in e.messages:
            print(f"diaggen odx: {m}", file=sys.stderr)
        return 1
    for m in export.messages:
        print(f"diaggen odx: {m}", file=sys.stderr)
    target = args.output or Path(f"{export.name}.{'pdx' if args.kind == 'pdx' else 'odx-d'}")
    written = write(export, target, args.kind)
    print(f"diaggen: {args.kind.upper()} written: {written}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
