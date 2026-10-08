"""Minimal reader of the ODX/PDX export (TOOL-43): the round trip of the export without a foreign library.

Reads back what tools/diaggen/odxexport.py writes - a diagnostic layer container with one base variant - and
returns the values a tester works with as plain Python data (`summary`). `expected(description)` gives the same
data straight from the diagnostic description, so a test compares both: every session, security level, data
identifier (number, elements with coding, conversion, limits, unit and text table), routine (steps and their
parameters), DTC (number, display code, severity, snapshot and extended data records) and DTC group has to come
back identical.

The reader follows the wire content (service identifiers and constants of the requests, byte positions, coded
types and data objects of the parameters) and the references of the document; it does not read the YAML file
names. It is deliberately not a general ODX reader: variants, inheritance, protocol layers and communication
parameter subsets are not resolved (the export writes none of them).

Qt free and only the Python standard library.
"""

import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

SID_SESSION, SID_SECURITY, SID_READ, SID_WRITE, SID_ROUTINE = 0x10, 0x27, 0x22, 0x2E, 0x31
SID_READ_DTC, SID_CLEAR_DTC = 0x19, 0x14
ALL_DTCS = 0xFFFFFF
STEPS = {0x01: "start", 0x02: "stop", 0x03: "results"}
XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"
TEXT_TYPES = ("ascii", "bytes")


class OdxReadError(Exception):
    pass


# ------------------------------------------------------------------------------------------------ files


def load(path: Path) -> dict:
    """Summary of a .odx-d or a .pdx file."""
    path = Path(path)
    if path.suffix.lower() == ".pdx":
        return read_pdx(path)
    return summary(path.read_text(encoding="utf-8"))


def pdx_files(path: Path) -> dict[str, str]:
    """Catalog of a PDX package: file name -> MIME type, checked against the content of the zip."""
    with zipfile.ZipFile(path) as archive:
        index = ET.fromstring(archive.read("index.xml"))
        present = set(archive.namelist())
    if index.tag != "CATALOG":
        raise OdxReadError(f"index.xml: root element {index.tag}, expected CATALOG")
    files = {f.text: f.get("MIME-TYPE") for f in index.iter("FILE")}
    missing = [name for name in files if name not in present]
    if missing:
        raise OdxReadError(f"index.xml names files the package does not contain: {', '.join(missing)}")
    return files


def read_pdx(path: Path) -> dict:
    files = pdx_files(path)
    layers = [name for name in files if name.lower().endswith(".odx-d")]
    if len(layers) != 1:
        raise OdxReadError(f"expected one .odx-d in the package, found {len(layers)}")
    with zipfile.ZipFile(path) as archive:
        return summary(archive.read(layers[0]).decode("utf-8"))


# ------------------------------------------------------------------------------------------------ document


def _text(element, tag: str, default=None):
    child = element.find(tag) if element is not None else None
    return child.text if child is not None and child.text is not None else default


class _Document:
    def __init__(self, text: str):
        self.root = ET.fromstring(text)
        if self.root.tag != "ODX":
            raise OdxReadError(f"root element {self.root.tag}, expected ODX")
        variants = self.root.findall("./DIAG-LAYER-CONTAINER/BASE-VARIANTS/BASE-VARIANT")
        if len(variants) != 1:
            raise OdxReadError(f"expected one base variant, found {len(variants)}")
        self.layer = variants[0]
        self.ids = {e.get("ID"): e for e in self.root.iter() if e.get("ID") is not None}
        # state ID -> (semantic of its chart, name)
        self.states = {}
        for chart in self.layer.iter("STATE-CHART"):
            for state in chart.iter("STATE"):
                self.states[state.get("ID")] = (_text(chart, "SEMANTIC"), _text(state, "SHORT-NAME"))

    def target(self, element, tag: str):
        child = element.find(tag)
        if child is None:
            return None
        found = self.ids.get(child.get("ID-REF"))
        if found is None:
            raise OdxReadError(f"{tag} {child.get('ID-REF')} names no element of the document")
        return found

    @staticmethod
    def constants(message) -> dict[str, int]:
        return {_text(p, "SHORT-NAME"): int(_text(p, "CODED-VALUE")) for p in message.findall("./PARAMS/PARAM")
                if p.get(XSI_TYPE) == "CODED-CONST"}

    @staticmethod
    def coded_at(message, position: int) -> int | None:
        for p in message.findall("./PARAMS/PARAM"):
            if p.get(XSI_TYPE) == "CODED-CONST" and int(_text(p, "BYTE-POSITION", "-1")) == position:
                return int(_text(p, "CODED-VALUE"))
        return None

    def values(self, message) -> list:
        """(parameter, data object) of every VALUE parameter, in byte order."""
        found = [(p, self.target(p, "DOP-REF")) for p in message.findall("./PARAMS/PARAM")
                 if p.get(XSI_TYPE) == "VALUE"]
        return sorted(found, key=lambda pd: int(_text(pd[0], "BYTE-POSITION", "0")))

    def element(self, param, dop) -> tuple:
        """What a tester needs of one value: (name, type, width, byte order, factor, offset, unit, min, max,
        text table)."""
        coded = dop.find("DIAG-CODED-TYPE")
        base, bits = coded.get("BASE-DATA-TYPE"), int(_text(coded, "BIT-LENGTH"))
        kind = {"A_UINT32": f"uint{bits}", "A_INT32": f"int{bits}", "A_FLOAT32": "float32",
                "A_ASCIISTRING": "ascii", "A_BYTEFIELD": "bytes"}.get(base)
        if kind is None:
            raise OdxReadError(f"{_text(dop, 'SHORT-NAME')}: base data type {base} is not written by the export")
        order = "little" if coded.get("IS-HIGHLOW-BYTE-ORDER") == "false" else "big"
        compu = dop.find("COMPU-METHOD")
        category = _text(compu, "CATEGORY")
        factor, offset, table = 1.0, 0.0, None
        if category == "LINEAR":
            numerator = [float(v.text) for v in compu.findall(".//COMPU-NUMERATOR/V")]
            denominator = [float(v.text) for v in compu.findall(".//COMPU-DENOMINATOR/V")] or [1.0]
            offset, factor = numerator[0] / denominator[0], numerator[1] / denominator[0]
        elif category == "TEXTTABLE":
            table = tuple((int(_text(s, "LOWER-LIMIT")), _text(s, "COMPU-CONST/VT"))
                          for s in compu.findall(".//COMPU-SCALE"))
        unit = self.target(dop, "UNIT-REF")
        limits = [None, None]
        constraint = dop.find("PHYS-CONSTR")
        if constraint is not None:
            for i, tag in enumerate(("LOWER-LIMIT", "UPPER-LIMIT")):
                limit = constraint.find(tag)
                if limit is not None and limit.get("INTERVAL-TYPE") != "INFINITE" and limit.text is not None:
                    limits[i] = float(limit.text)
        return (_text(param, "SHORT-NAME"), kind, bits // 8, order, factor, offset,
                _text(unit, "DISPLAY-NAME") if unit is not None else None, limits[0], limits[1], table)

    def preconditions(self, service) -> tuple:
        sessions, security = [], []
        for reference in service.findall("./PRE-CONDITION-STATE-REFS/PRE-CONDITION-STATE-REF"):
            semantic, name = self.states[reference.get("ID-REF")]
            (sessions if semantic == "SESSION" else security).append(name)
        return tuple(sessions) or None, security[0] if security else None

    def transition_target(self, service) -> str | None:
        for reference in service.findall("./STATE-TRANSITION-REFS/STATE-TRANSITION-REF"):
            transition = self.ids[reference.get("ID-REF")]
            return transition.find("TARGET-SNREF").get("SHORT-NAME")
        return None

    @staticmethod
    def special(element) -> dict[str, str]:
        return {sd.get("SI"): sd.text for sd in element.findall("./SDGS/SDG/SD")}

    def funct_class(self, service) -> str | None:
        reference = service.find("./FUNCT-CLASS-REFS/FUNCT-CLASS-REF")
        return _text(self.ids[reference.get("ID-REF")], "SHORT-NAME") if reference is not None else None


def summary(text: str) -> dict:
    """The values of an exported document (see module description)."""
    doc = _Document(text)
    result = {"name": _text(doc.layer, "SHORT-NAME"), "sessions": {}, "security": {}, "dids": {}, "routines": {},
              "dtcs": {}, "dtc_groups": {},
              "comparams": {r.get("ID-REF"): _text(r, "SIMPLE-VALUE")
                            for r in doc.layer.findall("./COMPARAM-REFS/COMPARAM-REF")}}
    seeds, keys = {}, {}
    for service in doc.layer.findall("./DIAG-COMMS/DIAG-SERVICE"):
        request = doc.target(service, "REQUEST-REF")
        response = doc.target(service.find("POS-RESPONSE-REFS"), "POS-RESPONSE-REF")
        sid = doc.coded_at(request, 0)
        label = _text(service, "LONG-NAME")
        sessions, security = doc.preconditions(service)
        if sid == SID_SESSION:
            special = doc.special(service)
            result["sessions"][doc.transition_target(service)] = (
                doc.coded_at(request, 1), int(special["p2_ms"]), int(special["p2_star_ms"]))
        elif sid == SID_SECURITY:
            level = doc.coded_at(request, 1)
            if level % 2:
                seed = doc.values(response)[0][1]
                seeds[level] = (int(_text(seed.find("DIAG-CODED-TYPE"), "BIT-LENGTH")) // 8, doc.special(service))
            else:
                key = doc.values(request)[0][1]
                keys[level - 1] = (doc.transition_target(service),
                                   int(_text(key.find("DIAG-CODED-TYPE"), "BIT-LENGTH")) // 8)
        elif sid in (SID_READ, SID_WRITE):
            identifier = doc.constants(request)["DataIdentifier"]
            message = response if sid == SID_READ else request
            param, structure = doc.values(message)[0]
            entry = result["dids"].setdefault(identifier, {
                "name": _text(structure, "SHORT-NAME"), "label": label, "group": doc.funct_class(service),
                "read": False, "write": False, "read_sessions": None, "write_sessions": None, "read_security": None,
                "write_security": None,
                "elements": [doc.element(p, dop) for p, dop in doc.values(structure)]})
            kind = "read" if sid == SID_READ else "write"
            entry[kind] = True
            entry[f"{kind}_sessions"], entry[f"{kind}_security"] = sessions, security
        elif sid == SID_ROUTINE:
            constants = doc.constants(request)
            identifier = constants["RoutineIdentifier"]
            routine = result["routines"].setdefault(identifier, {"name": None, "label": label,
                                                                 "group": doc.funct_class(service),
                                                                 "sessions": sessions, "security": security,
                                                                 "steps": {}})
            step = STEPS[constants["RoutineControlType"]]
            if step == "start":
                routine["name"] = _text(service, "SHORT-NAME").rsplit("_", 1)[0]
            routine["steps"][step] = {kind: [doc.element(p, dop) for p, dop in doc.values(message)]
                                      for kind, message in (("request", request), ("response", response))}
        elif sid == SID_CLEAR_DTC:
            group = doc.constants(request).get("GroupOfDTC")
            special = doc.special(service)
            if group is not None and group != ALL_DTCS:
                result["dtc_groups"][special["group"]] = (group, int(special["first"]), int(special["last"]))
    for level, (seed_bytes, special) in seeds.items():
        name, key_bytes = keys[level]
        result["security"][name] = (level, seed_bytes, key_bytes, special["method"], int(special["attempts"]),
                                    int(special["delay_ms"]))
    _fault_memory(doc, result)
    return result


def _fault_memory(doc: _Document, result: dict) -> None:
    dictionary = doc.layer.find("DIAG-DATA-DICTIONARY-SPEC")
    for dtc in dictionary.findall("./DTC-DOPS/DTC-DOP/DTCS/DTC"):
        special = doc.special(dtc)
        result["dtcs"][int(_text(dtc, "TROUBLE-CODE"))] = {
            "name": _text(dtc, "SHORT-NAME"), "text": _text(dtc, "TEXT"),
            "display": _text(dtc, "DISPLAY-TROUBLE-CODE"), "level": int(_text(dtc, "LEVEL", "0")),
            "severity": special.get("severity"), "priority": int(special.get("priority", "1")),
            "group": special.get("group"), "snapshots": {}, "extended": {}}
    for description in dictionary.findall("./ENV-DATA-DESCS/ENV-DATA-DESC"):
        kind = "snapshots" if _text(description, "SHORT-NAME") == "SnapshotRecords" else "extended"
        for reference in description.findall("./ENV-DATA-REFS/ENV-DATA-REF"):
            env = doc.ids[reference.get("ID-REF")]
            records = {}
            for _param, mux in doc.values(env):
                # a list of records (end of PDU field) of a structure that holds the multiplexer
                if mux.tag == "END-OF-PDU-FIELD":
                    mux = doc.values(doc.target(mux, "BASIC-STRUCTURE-REF"))[0][1]
                if mux.tag != "MUX":
                    raise OdxReadError(f"{_text(env, 'SHORT-NAME')}: expected the records as a multiplexer")
                for case in mux.findall("./CASES/CASE"):
                    structure = doc.target(case, "STRUCTURE-REF")
                    if kind == "snapshots":
                        content = tuple(_text(dop, "SHORT-NAME") for _p, dop in doc.values(structure))
                    else:
                        content = tuple(
                            (_text(p, "SHORT-NAME"),
                             p.get("SEMANTIC")[4:] if p.get("SEMANTIC", "").startswith("DID.") else None,
                             None if p.get("SEMANTIC", "").startswith("DID.") else p.get("SEMANTIC"))
                            for p, _dop in doc.values(structure))
                    records[int(_text(case, "LOWER-LIMIT"))] = content
            for value in env.findall("./DTC-VALUES/DTC-VALUE"):
                result["dtcs"][int(value.text)][kind].update(records)


# ------------------------------------------------------------------------------------------------ expectation


def _parameter(p) -> tuple:
    text = p.type in TEXT_TYPES
    return (p.name, p.type, p.width_bytes, "big" if text else p.byte_order, 1.0 if text else float(p.factor),
            0.0 if text else float(p.offset), None if text else p.unit,
            None if p.min is None else float(p.min), None if p.max is None else float(p.max),
            None if p.values is None else tuple((v.value, v.text) for v in p.values))


def _names(values) -> tuple | None:
    return tuple(values) if values else None


def expected(description) -> dict:
    """The summary the export of `description` (a diaggen.Description or a model.DiagDescription) must give,
    straight from the description. Communication parameters and the layer name are not part of it."""
    import diaggen

    desc = getattr(description, "desc", description)
    result = {"sessions": {r.name: (r.id, r.p2_ms, r.p2_star_ms) for r in diaggen.sessions_of(desc)},
              "security": {s.name: (s.level, s.seed_bytes, s.key_bytes, s.method, s.attempts, s.delay_ms)
                           for s in desc.security},
              "dids": {}, "routines": {}, "dtcs": {},
              "dtc_groups": {g.name: (g.group, g.first, g.last) for g in desc.dtc_groups}}
    for did in desc.data_identifiers:
        if not (did.read or did.write):
            continue
        result["dids"][did.identifier] = {
            "name": did.name, "label": did.label, "group": did.group, "read": did.read, "write": did.write,
            "read_sessions": _names(did.read_sessions) if did.read else None,
            "write_sessions": _names(did.write_sessions) if did.write else None,
            "read_security": did.read_security if did.read else None,
            "write_security": did.write_security if did.write else None,
            "elements": [_parameter(e) for e in did.data]}
    for routine in desc.routines:
        steps = {}
        for step in STEPS.values():
            value = getattr(routine, step)
            if value is not None:
                steps[step] = {"request": [_parameter(p) for p in value.request],
                               "response": [_parameter(p) for p in value.response]}
        result["routines"][routine.identifier] = {"name": routine.name, "label": routine.label,
                                                  "group": routine.group, "sessions": _names(routine.sessions),
                                                  "security": routine.security, "steps": steps}
    levels = {"none": 0, "maintenance_only": 1, "check_at_next_halt": 2, "check_immediately": 3}
    for dtc in desc.dtcs:
        result["dtcs"][dtc.number] = {
            "name": dtc.name, "text": dtc.label or dtc.name, "display": dtc.display_code,
            "level": levels[dtc.severity], "severity": dtc.severity, "priority": dtc.priority, "group": dtc.group,
            "snapshots": {s.record_number: tuple(s.dids) for s in dtc.snapshots},
            "extended": {x.record_number: tuple((i.name, i.did, i.counter) for i in x.data)
                         for x in dtc.extended_data}}
    return result


def comparable(summary_: dict) -> dict:
    """A summary without what `expected` does not know (layer name, communication parameters)."""
    return {k: v for k, v in summary_.items() if k not in ("name", "comparams")}
