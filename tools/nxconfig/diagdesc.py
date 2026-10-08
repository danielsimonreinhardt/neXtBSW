"""Diagnostic description of a project (ARCH-51): data identifiers, routines, sessions, security levels and DTCs.

Schema: model.DiagDescription, file `projects/<project>/diag.yaml`, named by `project.diag` (decision of the
user 2026-09-21, like `rte.yaml`). This module checks what the schema cannot: that the entries fit each other,
that every identifier is unique and allowed, and that every reference points somewhere. Every finding carries
file, line and YAML path, so the Studio can show it at the key (Qt free, like the rest of tools/nxconfig).

Not here: the *addresses* of the diagnostic connection (CAN ids, ISO-TP, bootloader security access). They are
project values in `project.yaml diagnostics` and are checked in diag.py.

Nothing is generated here. COM-28 turns the description into UDS jobs, NVM-03 into the fault memory, TOOL-43
exports it as ODX/PDX. What this module derives (DiagDescriptionPlan) is what all three would otherwise derive
three times: the length of every identifier, the severity byte of every DTC, the DTC format identifier and the
status availability mask of the fault memory.

Family and board neutral: nothing here knows a derivative, a package or a CAN instance.

ISO 14229-1 (2026) is the source of the number ranges below. Only the numbers and clause references are used;
the wording is ours (the document is licensed and must not be reproduced):
  Annex C.1  data identifier ranges, the identification identifiers F180..F182 with their numberOfModules byte
  Annex D.1  DTC number ranges and the group identifiers (also of `dtc_groups`, NVM-09)
  Annex D.2  status bits; D.3 severity and class; D.4 DTCFormatIdentifier; D.8 extended data record numbers
  Annex F.1  routine identifier ranges
  Clause 9.2 session types, clause 9.4 security access levels
"""

from dataclasses import dataclass, field

from . import bswdtc
from .config import Config, Reporter
from .messages import format_path
from .model import TEXT_FILL_BYTE

DIAG_LEVEL = "diag"                                 # Reporter level of diag.yaml (config.DIAG_LEVEL)

# ----------------------------------------------------------------------------------------------- ISO ranges

# (first, last, kind, what). kind: "free" a project may use it, "reserved" it may not, "standard" the value has a
# fixed meaning in ISO 14229-1 and a project may only describe that meaning.
DID_RANGES = (
    (0x0000, 0x00FF, "reserved", "reserved by ISO 14229-1"),
    (0x0100, 0xA5FF, "free", "left to the vehicle manufacturer"),
    (0xA600, 0xA7FF, "reserved", "reserved for legislative requirements"),
    (0xA800, 0xACFF, "free", "left to the vehicle manufacturer"),
    (0xAD00, 0xAFFF, "reserved", "reserved for legislative requirements"),
    (0xB000, 0xB1FF, "free", "left to the vehicle manufacturer"),
    (0xB200, 0xBFFF, "reserved", "reserved for legislative requirements"),
    (0xC000, 0xC2FF, "free", "left to the vehicle manufacturer"),
    (0xC300, 0xCEFF, "reserved", "reserved for legislative requirements"),
    (0xCF00, 0xEFFF, "free", "left to the vehicle manufacturer"),
    (0xF000, 0xF00F, "reserved", "reserved for the network configuration of tractor and trailer"),
    (0xF010, 0xF0FF, "free", "left to the vehicle manufacturer"),
    (0xF100, 0xF17F, "free", "identification options of the vehicle manufacturer"),
    (0xF180, 0xF19F, "standard", "an identification identifier with a meaning fixed by ISO 14229-1"),
    (0xF1A0, 0xF1EF, "free", "identification options of the vehicle manufacturer"),
    (0xF1F0, 0xF1FF, "free", "identification options of the system supplier"),
    (0xF200, 0xF2FF, "reserved", "reserved for periodic identifiers (service 2A)"),
    (0xF300, 0xF3FF, "reserved", "reserved for dynamically defined identifiers (service 2C)"),
    (0xF400, 0xF5FF, "reserved", "reserved for regulated OBD"),
    (0xF600, 0xF6FF, "reserved", "reserved for regulated OBD monitors"),
    (0xF700, 0xF7FF, "reserved", "reserved for regulated OBD"),
    (0xF800, 0xF8FF, "reserved", "reserved for regulated OBD info types"),
    (0xF900, 0xF9FF, "reserved", "reserved for tachograph data"),
    (0xFA00, 0xFA18, "reserved", "reserved for airbag deployment and event data recorders"),
    (0xFA19, 0xFAFF, "reserved", "reserved for safety systems"),
    (0xFB00, 0xFCFF, "reserved", "reserved by ISO 14229-1"),
    (0xFD00, 0xFEFF, "free", "left to the system supplier"),
    (0xFF00, 0xFF01, "standard", "a value with a meaning fixed by ISO 14229-1 (UDS version, segmentation)"),
    (0xFF02, 0xFFFF, "reserved", "reserved by ISO 14229-1"),
)

ROUTINE_RANGES = (
    (0x0000, 0x00FF, "reserved", "reserved by ISO 14229-1"),
    (0x0100, 0x01FF, "reserved", "reserved for tachograph test results"),
    (0x0200, 0xDFFF, "free", "left to the vehicle manufacturer"),
    (0xE000, 0xE1FF, "reserved", "reserved for OBD test results"),
    (0xE200, 0xE201, "standard", "a routine with a meaning fixed by ISO 14229-1"),
    (0xE202, 0xE2FF, "reserved", "reserved for safety related systems"),
    (0xE300, 0xEFFF, "reserved", "reserved by ISO 14229-1"),
    (0xF000, 0xFEFF, "free", "left to the system supplier"),
    (0xFF00, 0xFF01, "standard", "a programming routine with a meaning fixed by ISO 14229-1"),
    (0xFF02, 0xFFFF, "reserved", "reserved by ISO 14229-1"),
)

# Annex D.1: the lowest numbers belong to legislation, the highest are group identifiers, not single DTCs.
DTC_RESERVED_RANGES = (
    (0x000000, 0x0000FF, "reserved for legislative requirements"),
    (0xFFFF00, 0xFFFFFF, "a group identifier, not a single DTC"),
)
# Annex D.1: groupOfDTC values of 14 (NVM-09). 0xFFFFFF stands for all DTCs; 0xFFFF00..0xFFFFFE are the functional
# groups, every other value a group of the vehicle manufacturer (or a single DTC number).
GROUP_ALL_DTCS = 0xFFFFFF
FUNCTIONAL_GROUPS = (0xFFFF00, 0xFFFFFE)

# Clause 9.2: session types a project must not use.
SESSION_RESERVED_RANGES = ((0x05, 0x3F, "reserved by ISO 14229-1"),)

# ARCH-68: upper limits of the AUTOSAR ECUC definitions (R25-11, Dcm and Dem) as *warnings*. The wire format allows
# more, so a project above them stays valid; the limits are the largest values a standard diagnostic module of
# that release accepts. Names of the parameters in tools/autosar_xcheck/map.yaml.
P2_WARN_MS = 1000                  # Dcm DcmDspSessionP2ServerMax, 1 s
P2_STAR_WARN_MS = 100_000          # Dcm DcmDspSessionP2StarServerMax, 100 s
TIME_DEBOUNCE_WARN_MS = 3_600_000  # Dem DemDebounceTimeFailedThreshold/...PassedThreshold, 3600 s
ROWS_WARN = 31                     # Dcm DcmDspSessionRow and DcmDspSecurityRow, at most 31 rows each

# Annex C.1: the record of these identifiers starts with the number of modules it describes (BL-22).
NUMBER_OF_MODULES_DIDS = {0xF180: "the boot software", 0xF181: "the application software",
                          0xF182: "the application data"}
# Annex C.1: identifiers whose length ISO 14229-1 fixes.
FIXED_LENGTH_DIDS = {0xF186: 1}

# Identifiers and routines the bootloader answers as part of its own protocol with tools/canflash
# (bootloader/src/core/uds.c). Their format is fixed in bl_config.h and read by the flash tool, so an
# application that describes them a second time would answer the same tester differently.
BOOTLOADER_DIDS = {
    0xF184: "the fingerprint of the last programming, written by the tester (BL-04)",
    0xF1A0: "the target and layout identity of the installed image (BL-02)",
    0xF1A1: "the format, method and key state of the security access (BL-03)",
}
BOOTLOADER_ROUTINES = {
    0xFF00: "eraseMemory of the bootloader",
    0xFF01: "checkProgrammingDependencies of the bootloader",
    0xF1A0: "the target compatibility check of the bootloader (BL-02, BL-24)",
    0xFF02: "the former identifier of the target compatibility check (bootloader before 0.3.4, BL-24)",
}

# Annex D.4: this description is the ISO 14229-1 DTC format. A server supports exactly one format, and 19 01
# reports this number.
DTC_FORMAT_ISO_14229_1 = 0x01
# Annex D.3: bits 7..5 of the severity byte; bit 0 is DTCClass_0 (unclassified), the only class a description
# without OBD reports (decision of the user 2026-09-21: no MIL, no OBD).
SEVERITY_BITS = {"none": 0x00, "maintenance_only": 0x20, "check_at_next_halt": 0x40, "check_immediately": 0x80}
DTC_CLASS_UNCLASSIFIED = 0x01
# Annex D.2: bit 7 is warningIndicatorRequested. Without a warning lamp there is no source for it, so the
# availability mask of 19 01 leaves it out and bits 0..6 stay.
STATUS_AVAILABILITY_NO_WARNING_INDICATOR = 0x7F

# Ranges of the integer types, for a constant that has to fit into its element.
INT_RANGES = {"uint8": (0, 0xFF), "uint16": (0, 0xFFFF), "uint32": (0, 0xFFFFFFFF),
              "int8": (-0x80, 0x7F), "int16": (-0x8000, 0x7FFF), "int32": (-0x80000000, 0x7FFFFFFF)}

# ISO 15765-2 carries at most 4095 bytes in one message; a 22 response spends 1 byte on the service and 2 on the
# identifier. A longer identifier cannot be answered at all on classic CAN.
ISOTP_MAX_PAYLOAD = 4095
RDBI_OVERHEAD = 3

# COM-30: seed and key of the method hmac-sha256 are cut from one HMAC-SHA256 output.
HMAC_SHA256_BYTES = 32


@dataclass(frozen=True)
class DtcPlan:
    """Derived values of one DTC; nothing here is configured."""

    name: str
    number: int
    severity_byte: int                              # Annex D.3: severity bits plus DTCClass_0
    snapshot_records: tuple[int, ...]
    extended_records: tuple[int, ...]


@dataclass(frozen=True)
class DiagDescriptionPlan:
    """Derived values of a checked diagnostic description, for COM-28, NVM-03 and TOOL-43."""

    file: str
    did_lengths: dict[str, int] = field(default_factory=dict)       # identifier name -> bytes on the wire
    dtcs: tuple[DtcPlan, ...] = ()
    dtc_format: int = DTC_FORMAT_ISO_14229_1
    status_availability_mask: int = STATUS_AVAILABILITY_NO_WARNING_INDICATOR
    sessions: dict[str, int] = field(default_factory=dict)          # session name -> session type
    security_levels: dict[str, int] = field(default_factory=dict)   # level name -> requestSeed sub-function
    # NVM-09: DTC group name -> names of the described DTCs in its range (ClearDiagnosticInformation, ODX export)
    dtc_groups: dict[str, tuple[str, ...]] = field(default_factory=dict)


def _hex16(value: int) -> str:
    return f"0x{value:04X}"


def _hex24(value: int) -> str:
    return f"0x{value:06X}"


def _range_of(value: int, ranges) -> tuple[str, str]:
    """(kind, reason) of a value in a range table; ("free", "") for a value no range covers."""
    for first, last, kind, what in ranges:
        if first <= value <= last:
            return kind, what
    return "free", ""


def _duplicates(rep: Reporter, entries, path, key: str, render=str, what: str = "") -> None:
    """Reports every entry whose `key` appeared before, at the YAML path of the later entry.

    `path` is the YAML path of the list, so the finding lands on the duplicate key itself and the Studio can
    jump there; the text names the entry that took the value first.
    """
    section = format_path(tuple(path))
    seen: dict[object, int] = {}
    for index, entry in enumerate(entries):
        value = getattr(entry, key)
        first = seen.get(value)
        if first is None:
            seen[value] = index
            continue
        named = f" ({entries[first].name})" if hasattr(entries[first], "name") else ""
        rep.error(DIAG_LEVEL, tuple(path) + (index, key),
                  f"{what or key} {render(value)} is already {section}[{first}]{named}: every entry of "
                  f"{section} needs its own {key}")


def _names(entries) -> dict[str, int]:
    return {e.name: i for i, e in enumerate(entries)}


def _reference(rep: Reporter, path, value: str, known: dict[str, int], section: str, what: str) -> bool:
    """Reports an unknown reference; True if the name exists."""
    if value in known:
        return True
    options = ", ".join(sorted(known)) if known else "none described"
    rep.error(DIAG_LEVEL, path, f"{what} {value} is not described in {section} (known: {options})")
    return False


# ----------------------------------------------------------------------------------------------- identifiers


def _check_element_names(rep: Reporter, rep_path, elements) -> None:
    seen: dict[str, int] = {}
    for index, element in enumerate(elements):
        first = seen.setdefault(element.name, index)
        if first != index:
            rep.error(DIAG_LEVEL, rep_path + (index, "name"),
                      f"element {element.name} is already element {first} of the same list: the names inside "
                      "one record have to differ (they become the parameter names of the export)")


def _check_constant(rep: Reporter, path, element) -> None:
    """A constant has to fit the element it fills: the length and the range check of a fixed value."""
    constant = element.source.constant
    if constant is None:
        return
    where = path + ("source", "constant")
    if isinstance(constant, str):
        if element.type != "ascii":
            rep.error(DIAG_LEVEL, where, f"the constant is a text but {element.name} has type {element.type}: "
                                         "use type ascii, or write the constant as a number")
        elif len(constant.encode("utf-8")) > element.width_bytes:
            rep.error(DIAG_LEVEL, where, f"the constant is {len(constant.encode('utf-8'))} bytes long but "
                                         f"{element.name} holds {element.width_bytes} (length_bytes)")
        return
    if element.type not in INT_RANGES:
        rep.error(DIAG_LEVEL, where, f"the constant is a number but {element.name} has type {element.type}: "
                                     "use an integer type, or write the constant as a text")
        return
    low, high = INT_RANGES[element.type]
    if not low <= constant <= high:
        rep.error(DIAG_LEVEL, where, f"the constant {constant} does not fit into {element.name} of type "
                                     f"{element.type} ({low}..{high})")


def _check_dids(rep: Reporter, desc, sessions: dict[str, int], security: dict[str, int]) -> dict[str, int]:
    """Checks the data identifiers; returns the derived length of each one by name."""
    _duplicates(rep, desc.data_identifiers, ("data_identifiers",), "name")
    _duplicates(rep, desc.data_identifiers, ("data_identifiers",), "identifier", _hex16, "data identifier")
    lengths: dict[str, int] = {}
    for index, did in enumerate(desc.data_identifiers):
        path = ("data_identifiers", index)
        lengths[did.name] = did.length_bytes
        kind, what = _range_of(did.identifier, DID_RANGES)
        if kind == "reserved":
            rep.error(DIAG_LEVEL, path + ("identifier",),
                      f"data identifier {_hex16(did.identifier)} is {what} (ISO 14229-1, Annex C.1): a project "
                      "describes identifiers of the manufacturer ranges (e.g. 0x0100..0xA5FF, 0xCF00..0xEFFF) "
                      "or of the supplier range 0xFD00..0xFEFF")
        elif kind == "standard" and did.identifier >= 0xFF00:
            # 0xF180..0xF19F are exactly the identification identifiers a project is meant to fill; 0xFF00 and
            # 0xFF01 describe the protocol itself and belong to the UDS stack, not to a described job.
            rep.error(DIAG_LEVEL, path + ("identifier",),
                      f"data identifier {_hex16(did.identifier)} is {what} and is answered by the UDS stack "
                      "itself, not by a described job")
        if did.identifier in BOOTLOADER_DIDS:
            rep.error(DIAG_LEVEL, path + ("identifier",),
                      f"data identifier {_hex16(did.identifier)} is {BOOTLOADER_DIDS[did.identifier]}: the "
                      "bootloader answers it and tools/canflash reads it in a fixed format "
                      "(bootloader/src/core/uds.c), so the application must not describe it a second time")
        if did.identifier in NUMBER_OF_MODULES_DIDS:
            first = did.data[0]
            if first.type != "uint8":
                rep.error(DIAG_LEVEL, path + ("data", 0, "type"),
                          f"identifier {_hex16(did.identifier)} describes "
                          f"{NUMBER_OF_MODULES_DIDS[did.identifier]}, so its record starts with the number of "
                          f"modules (ISO 14229-1, Annex C.1; BL-22): the first element has to be a uint8, "
                          f"not {first.type}")
        expected = FIXED_LENGTH_DIDS.get(did.identifier)
        if expected is not None and did.length_bytes != expected:
            rep.error(DIAG_LEVEL, path + ("data",),
                      f"identifier {_hex16(did.identifier)} has a length of {expected} byte fixed by ISO "
                      f"14229-1, the described data is {did.length_bytes} bytes")
        if did.length_bytes + RDBI_OVERHEAD > ISOTP_MAX_PAYLOAD:
            rep.error(DIAG_LEVEL, path + ("data",),
                      f"the described data is {did.length_bytes} bytes; with service and identifier the "
                      f"response is {did.length_bytes + RDBI_OVERHEAD} bytes and no longer fits into one "
                      f"ISO 15765-2 message ({ISOTP_MAX_PAYLOAD} bytes)")
        if not did.read and not did.write:
            rep.error(DIAG_LEVEL, path + ("read",),
                      f"identifier {did.name} can neither be read nor written: set read or write")
        _check_element_names(rep, path + ("data",), did.data)
        for number, element in enumerate(did.data):
            _check_constant(rep, path + ("data", number), element)
        for key in ("read_sessions", "write_sessions"):
            for number, name in enumerate(getattr(did, key) or ()):
                _reference(rep, path + (key, number), name, sessions, "sessions", "session")
        for key in ("read_security", "write_security"):
            name = getattr(did, key)
            if name is not None:
                _reference(rep, path + (key,), name, security, "security", "security level")
    return lengths


def _check_routines(rep: Reporter, desc, sessions: dict[str, int], security: dict[str, int]) -> None:
    _duplicates(rep, desc.routines, ("routines",), "name")
    _duplicates(rep, desc.routines, ("routines",), "identifier", _hex16, "routine identifier")
    for index, routine in enumerate(desc.routines):
        path = ("routines", index)
        kind, what = _range_of(routine.identifier, ROUTINE_RANGES)
        if kind == "reserved":
            rep.error(DIAG_LEVEL, path + ("identifier",),
                      f"routine identifier {_hex16(routine.identifier)} is {what} (ISO 14229-1, Annex F.1): a "
                      "project describes routines of 0x0200..0xDFFF or of the supplier range 0xF000..0xFEFF")
        elif kind == "standard":
            rep.error(DIAG_LEVEL, path + ("identifier",),
                      f"routine identifier {_hex16(routine.identifier)} is {what} (ISO 14229-1, Annex F.1) and "
                      "must not be described as a routine of the application")
        if routine.identifier in BOOTLOADER_ROUTINES:
            rep.error(DIAG_LEVEL, path + ("identifier",),
                      f"routine identifier {_hex16(routine.identifier)} is "
                      f"{BOOTLOADER_ROUTINES[routine.identifier]} (bootloader/src/core/uds.c): a tester that "
                      "reaches the bootloader would run another routine under the same number")
        for step in ("start", "stop", "results"):
            value = getattr(routine, step)
            if value is None:
                continue
            _check_element_names(rep, path + (step, "request"), value.request)
            _check_element_names(rep, path + (step, "response"), value.response)
        for number, name in enumerate(routine.sessions or ()):
            _reference(rep, path + ("sessions", number), name, sessions, "sessions", "session")
        if routine.security is not None:
            _reference(rep, path + ("security",), routine.security, security, "security", "security level")


# ----------------------------------------------------------------------------------------------- fault memory


def _check_dtc_debounce(rep: Reporter, path, debounce) -> None:
    timed = debounce.time
    if timed is not None:
        for key in ("failed_ms", "passed_ms"):
            if getattr(timed, key) > TIME_DEBOUNCE_WARN_MS:
                rep.warning(DIAG_LEVEL, path + ("time", key),
                            f"{key} {getattr(timed, key)} is above {TIME_DEBOUNCE_WARN_MS} ms (one hour), the "
                            "largest time debouncing the AUTOSAR Dem definition accepts (R25-11, "
                            "DemDebounceTimeBasedClass): valid here, but not portable to a standard Dem")
    counter = debounce.counter
    if counter is None:
        return
    if counter.jump_up is not None and counter.jump_up >= counter.failed_threshold:
        rep.error(DIAG_LEVEL, path + ("counter", "jump_up"),
                  f"jump_up {counter.jump_up} reaches the failed_threshold {counter.failed_threshold}: the "
                  "first failed sample would mature the fault at once and the debouncing would have no effect")
    if counter.jump_down is not None and counter.jump_down <= counter.passed_threshold:
        rep.error(DIAG_LEVEL, path + ("counter", "jump_down"),
                  f"jump_down {counter.jump_down} reaches the passed_threshold {counter.passed_threshold}: the "
                  "first passed sample would heal the fault at once")


def _check_dtcs(rep: Reporter, desc, did_lengths: dict[str, int]) -> tuple[DtcPlan, ...]:
    if not desc.dtcs:
        return ()
    cycles = _names(desc.operation_cycles)
    conditions = _names(desc.enable_conditions)
    dids = {name: name for name in did_lengths}
    memory = desc.fault_memory
    if memory is None:
        rep.error(DIAG_LEVEL, ("dtcs",), f"{len(desc.dtcs)} DTCs are described but there is no fault_memory "
                                         "section: the size and the overflow behaviour of the memory are part "
                                         "of the description")
    if not cycles:
        rep.error(DIAG_LEVEL, ("dtcs",), "DTCs are described but operation_cycles is empty: the status bits, "
                                         "the confirmation and the aging of a DTC count in an operation cycle "
                                         "(ISO 14229-1, D.2.1)")
    _duplicates(rep, desc.dtcs, ("dtcs",), "name")
    _duplicates(rep, desc.dtcs, ("dtcs",), "number", _hex24, "DTC number")
    plans = []
    for index, dtc in enumerate(desc.dtcs):
        path = ("dtcs", index)
        for first, last, what in DTC_RESERVED_RANGES:
            if first <= dtc.number <= last:
                rep.error(DIAG_LEVEL, path + ("number",),
                          f"DTC number {_hex24(dtc.number)} is {what} (ISO 14229-1, Annex D.1)")
        _check_dtc_debounce(rep, path + ("debounce",), dtc.debounce)
        _reference(rep, path + ("operation_cycle",), dtc.operation_cycle, cycles, "operation_cycles",
                   "operation cycle")
        if dtc.aging is not None and dtc.aging.cycle is not None:
            _reference(rep, path + ("aging", "cycle"), dtc.aging.cycle, cycles, "operation_cycles",
                       "operation cycle")
        for number, name in enumerate(dtc.enable_conditions):
            _reference(rep, path + ("enable_conditions", number), name, conditions, "enable_conditions",
                       "enable condition")
        _duplicates(rep, dtc.snapshots, path + ("snapshots",), "record_number", hex, "snapshot record")
        _duplicates(rep, dtc.extended_data, path + ("extended_data",), "record_number", hex,
                    "extended data record")
        for number, snapshot in enumerate(dtc.snapshots):
            seen: set[str] = set()
            for position, name in enumerate(snapshot.dids):
                _reference(rep, path + ("snapshots", number, "dids", position), name, dids, "data_identifiers",
                           "data identifier")
                if name in seen:
                    rep.error(DIAG_LEVEL, path + ("snapshots", number, "dids", position),
                              f"data identifier {name} stands twice in the same snapshot record")
                seen.add(name)
        for number, record in enumerate(dtc.extended_data):
            for position, item in enumerate(record.data):
                if item.did is not None:
                    _reference(rep, path + ("extended_data", number, "data", position, "did"), item.did, dids,
                               "data_identifiers", "data identifier")
            _check_element_names(rep, path + ("extended_data", number, "data"), record.data)
        if memory is not None:
            if len(dtc.snapshots) > memory.snapshot_records_max:
                rep.error(DIAG_LEVEL, path + ("snapshots",),
                          f"{len(dtc.snapshots)} snapshot records are described but the fault memory stores "
                          f"{memory.snapshot_records_max} per DTC (fault_memory.snapshot_records_max)")
            if len(dtc.extended_data) > memory.extended_records_max:
                rep.error(DIAG_LEVEL, path + ("extended_data",),
                          f"{len(dtc.extended_data)} extended data records are described but the fault memory "
                          f"stores {memory.extended_records_max} per DTC "
                          "(fault_memory.extended_records_max)")
        plans.append(DtcPlan(dtc.name, dtc.number,
                             SEVERITY_BITS[dtc.severity] | DTC_CLASS_UNCLASSIFIED,
                             tuple(s.record_number for s in dtc.snapshots),
                             tuple(r.record_number for r in dtc.extended_data)))
    if memory is not None:
        if memory.aging_cycle is not None:
            _reference(rep, ("fault_memory", "aging_cycle"), memory.aging_cycle, cycles, "operation_cycles",
                       "operation cycle")
        if len(desc.dtcs) > memory.max_entries:
            rep.warning(DIAG_LEVEL, ("fault_memory", "max_entries"),
                        f"{len(desc.dtcs)} DTCs are described but the memory holds {memory.max_entries} at the "
                        f"same time: with more than {memory.max_entries} mature faults the memory overflows "
                        f"and {memory.overflow} decides which entry is lost")
    return tuple(plans)



def _check_dtc_groups(rep: Reporter, desc) -> dict[str, tuple[str, ...]]:
    """NVM-09: the groups 14 clears besides all DTCs and single DTCs (Annex D.1); group name -> member DTCs."""
    groups = desc.dtc_groups
    if not groups:
        return {}
    _duplicates(rep, groups, ("dtc_groups",), "name")
    _duplicates(rep, groups, ("dtc_groups",), "group", _hex24, "group identifier")
    numbers = {dtc.number: dtc.name for dtc in desc.dtcs}
    members: dict[str, tuple[str, ...]] = {}
    for index, entry in enumerate(groups):
        path = ("dtc_groups", index)
        if entry.group == GROUP_ALL_DTCS:
            rep.error(DIAG_LEVEL, path + ("group",),
                      f"group identifier {_hex24(entry.group)} stands for all DTCs (ISO 14229-1, Annex D.1); 14 "
                      "clears them without an entry")
        elif entry.group in numbers:
            rep.error(DIAG_LEVEL, path + ("group",),
                      f"group identifier {_hex24(entry.group)} is the number of DTC {numbers[entry.group]}: 14 "
                      "takes the value as that single DTC (Annex D.1), the group could never be cleared")
        elif entry.group <= 0x0000FF:
            rep.error(DIAG_LEVEL, path + ("group",),
                      f"group identifier {_hex24(entry.group)} lies in the range reserved for legislative "
                      "requirements (ISO 14229-1, Annex D.1)")
        if entry.first > entry.last:
            rep.error(DIAG_LEVEL, path + ("last",),
                      f"last {_hex24(entry.last)} is below first {_hex24(entry.first)}: the group would hold no DTC")
            continue
        if entry.last >= FUNCTIONAL_GROUPS[0]:
            rep.error(DIAG_LEVEL, path + ("last",),
                      f"last {_hex24(entry.last)} reaches the group identifiers {_hex24(FUNCTIONAL_GROUPS[0])}.."
                      f"{_hex24(GROUP_ALL_DTCS)} (ISO 14229-1, Annex D.1), which are no DTC numbers")
        inside = tuple(dtc.name for dtc in desc.dtcs if entry.first <= dtc.number <= entry.last)
        if not inside:
            rep.warning(DIAG_LEVEL, path,
                        f"group {entry.name} ({_hex24(entry.first)}..{_hex24(entry.last)}) holds none of the "
                        "described DTCs: 14 with it answers positively and clears nothing")
        members[entry.name] = inside
    return members


def _check_bsw_range(rep: Reporter, desc) -> None:
    """TOOL-178: the neXtBSW range (bsw_dtc_range) against the plan and against the DTCs of the project.

    The range itself (multiple of 0x100, not in the legislated or group ranges) and the derived numbers are checked
    by the schema. Here: a range too small for the plan, a project DTC inside the range (its number could collide
    with a BSW DTC of a later neXtBSW release or of a follow-up source) and an explicit number of a BSW source that
    is not the one of the plan."""
    span = desc.bsw_dtc_range
    base, last = span.base, span.base + span.size - 1
    if span.size < bswdtc.PLAN_SIZE:
        lost = [s for s in bswdtc.PLAN if s.offset >= span.size]
        rep.warning(DIAG_LEVEL, ("bsw_dtc_range", "size"),
                    f"size 0x{span.size:X} is below the numbering plan (0x{bswdtc.PLAN_SIZE:X}): "
                    f"{', '.join(f'{s.key} (+0x{s.block:02X}xx)' for s in lost)} get no number in this range")
    for index, dtc in enumerate(desc.dtcs):
        if dtc.number_derived or not base <= dtc.number <= last:
            continue
        path = ("dtcs", index, "number")
        if dtc.number == base:
            rep.error(DIAG_LEVEL, path, f"DTC number {_hex24(dtc.number)} is the group identifier of the neXtBSW "
                                        f"range (bsw_dtc_range.base), never a DTC")
            continue
        if dtc.event.bsw is None:
            rep.warning(DIAG_LEVEL, path,
                        f"DTC {dtc.name} ({_hex24(dtc.number)}) is a DTC of the project inside the neXtBSW range "
                        f"{_hex24(base)}..{_hex24(last)} (bsw_dtc_range) but no BSW source reports it: number "
                        "project DTCs outside the range, the basic software numbers its own DTCs there")
            continue
        planned = bswdtc.planned(dtc.event.bsw, base, span.size, dtc.bsw_instance)
        if planned is not None and planned != dtc.number:
            rep.warning(DIAG_LEVEL, path,
                        f"DTC {dtc.name} of {dtc.event.bsw} has {_hex24(dtc.number)}, the numbering plan gives "
                        f"{_hex24(planned)}: leave the number out so it follows bsw_dtc_range")


def _check_bsw_instances(cfg: Config, rep: Reporter, desc) -> None:
    """COM-46/NVM-08: `bsw_instance` of a BSW DTC with instances is a project fact, never a free number - the index of
    the bus among the used CAN buses (can.busoff.<BUS>, the order of the bus ids: board order of the used buses) and the
    number of the cause of the safe state (safety.<cause>, bswdtc.SAFETY_CAUSES). A bus-off DTC names a used bus."""
    try:
        used = [b.name for b in cfg.selected("can")]
    except (AttributeError, TypeError):
        used = None                                 # no board or project loaded: nothing to compare with
    for index, dtc in enumerate(desc.dtcs):
        event = dtc.event.bsw or ""
        if event.startswith(bswdtc.CAN_BUSOFF_PREFIX) and used is not None:
            bus = event[len(bswdtc.CAN_BUSOFF_PREFIX):]
            if bus not in used:
                rep.error(DIAG_LEVEL, ("dtcs", index, "event", "bsw"),
                          f"DTC {dtc.name}: {event} names no CAN bus the project uses ({', '.join(used) or 'none'}, "
                          "project.yaml use.can)")
            elif dtc.bsw_instance is not None and dtc.bsw_instance != used.index(bus):
                rep.error(DIAG_LEVEL, ("dtcs", index, "bsw_instance"),
                          f"DTC {dtc.name}: bus {bus} has the index {used.index(bus)} among the used CAN buses "
                          f"({', '.join(used)}, the order of their bus ids), not {dtc.bsw_instance}")
        elif event.startswith(bswdtc.SAFETY_PREFIX):
            cause = bswdtc.SAFETY_CAUSES.get(event[len(bswdtc.SAFETY_PREFIX):])
            if cause is not None and dtc.bsw_instance is not None and dtc.bsw_instance != cause:
                rep.error(DIAG_LEVEL, ("dtcs", index, "bsw_instance"),
                          f"DTC {dtc.name}: the cause {event} has the number 0x{cause:02X} (SafeSupervisor::Event), "
                          f"not 0x{dtc.bsw_instance:02X}")


#: SYS-196: values of the hold's take-backs a data identifier reads (tools/rtegen BSW_STATUS "safeoff")
SAFE_OFF_STATUS_PREFIX = "bsw.safeoff."


def _check_safe_off_take_back(cfg: Config, rep: Reporter, desc) -> None:
    """SYS-196: the DTC of a foreign safe_off level taken back (safety.safe_off_take_back) and the values bsw.safeoff.*
    exist only where the hold of the commanded level (SYS-151) runs - a project with at least one safe_off output of an
    integrated device (degraded.safe_off_entries, the second block of the safe outputs table). Without one the hold is
    not built in: the DTC would never mature and the identifier would always read "none" - an error, not a silent
    DTC (decision SYS194-4 b: only a project with the hold gets the DTC)."""
    from . import degraded                      # lazy: only needed by this check

    try:
        outputs = degraded.safe_off_entries(cfg)
    except (AttributeError, TypeError):
        return                                  # no board or project loaded: nothing to compare with
    if outputs:
        return
    why = ("the project has no safe_off output (no integrated device whose module declares one, module.yaml connect "
           "safe_off), so the hold of the commanded level that takes back foreign levels (SYS-151) is not built in")
    for index, dtc in enumerate(desc.dtcs):
        if dtc.event.bsw == bswdtc.SAFE_OFF_TAKE_BACK:
            rep.error(DIAG_LEVEL, ("dtcs", index, "event", "bsw"),
                      f"DTC {dtc.name}: {dtc.event.bsw} would never be reported - {why}; remove the DTC")
    for index, did in enumerate(desc.data_identifiers):
        for number, element in enumerate(did.data):
            name = element.source.bsw
            if name is not None and str(name).startswith(SAFE_OFF_STATUS_PREFIX):
                rep.error(DIAG_LEVEL, ("data_identifiers", index, "data", number, "source", "bsw"),
                          f"identifier {did.name}: {name} has no value here - {why}; remove the element")


# ----------------------------------------------------------------------------------------------- references


def _check_sessions(rep: Reporter, desc) -> dict[str, int]:
    _duplicates(rep, desc.sessions, ("sessions",), "name")
    _duplicates(rep, desc.sessions, ("sessions",), "id", hex, "session type")
    if len(desc.sessions) > ROWS_WARN:
        rep.warning(DIAG_LEVEL, ("sessions",),
                    f"{len(desc.sessions)} sessions are described, the AUTOSAR Dcm definition accepts at most "
                    f"{ROWS_WARN} (R25-11, DcmDspSessionRow): valid here, but not portable to a standard Dcm")
    for index, session in enumerate(desc.sessions):
        if session.p2_ms > P2_WARN_MS:
            rep.warning(DIAG_LEVEL, ("sessions", index, "p2_ms"),
                        f"p2_ms {session.p2_ms} is above {P2_WARN_MS} ms, the largest P2 the AUTOSAR Dcm "
                        "definition accepts (R25-11, DcmDspSessionP2ServerMax): valid here, but not portable")
        if session.p2_star_ms > P2_STAR_WARN_MS:
            rep.warning(DIAG_LEVEL, ("sessions", index, "p2_star_ms"),
                        f"p2_star_ms {session.p2_star_ms} is above {P2_STAR_WARN_MS} ms, the largest P2* the "
                        "AUTOSAR Dcm definition accepts (R25-11, DcmDspSessionP2StarServerMax): valid here, but "
                        "not portable")
        for first, last, what in SESSION_RESERVED_RANGES:
            if first <= session.id <= last:
                rep.error(DIAG_LEVEL, ("sessions", index, "id"),
                          f"session type 0x{session.id:02X} is {what} (ISO 14229-1, clause 9.2): 0x01..0x04 are "
                          "the standard sessions, 0x40..0x5F are left to the manufacturer and 0x60..0x7E to "
                          "the system supplier")
    return _names(desc.sessions)


def _check_security(cfg: Config, rep: Reporter, desc, sessions: dict[str, int]) -> dict[str, int]:
    _duplicates(rep, desc.security, ("security",), "name")
    _duplicates(rep, desc.security, ("security",), "level", hex, "security level")
    if len(desc.security) > ROWS_WARN:
        rep.warning(DIAG_LEVEL, ("security",),
                    f"{len(desc.security)} security levels are described, the AUTOSAR Dcm definition accepts at "
                    f"most {ROWS_WARN} (R25-11, DcmDspSecurityRow): valid here, but not portable to a standard Dcm")
    for index, level in enumerate(desc.security):
        for number, name in enumerate(level.sessions or ()):
            _reference(rep, ("security", index, "sessions", number), name, sessions, "sessions", "session")
        _check_security_method(cfg, rep, index, level)
        if level.key_bytes < level.seed_bytes:
            rep.warning(DIAG_LEVEL, ("security", index, "key_bytes"),
                        f"the key is {level.key_bytes} bytes and the seed {level.seed_bytes}: a key shorter "
                        "than the seed throws away entropy of the challenge")
    return _names(desc.security)


def _check_security_method(cfg: Config, rep: Reporter, index: int, level) -> None:
    """COM-30: what the method of a level needs. hmac-sha256 answers with the device key of the bootloader's
    security access (BL-03), so the project needs that key: a memory partitioning with its key sector and a
    bootloader security access with the same method."""
    if level.method != "hmac-sha256":
        return
    for field_name, value in (("seed_bytes", level.seed_bytes), ("key_bytes", level.key_bytes)):
        if value > HMAC_SHA256_BYTES:
            rep.error(DIAG_LEVEL, ("security", index, field_name),
                      f"{field_name} {value}: method hmac-sha256 makes seed and key from one HMAC-SHA256, at most "
                      f"{HMAC_SHA256_BYTES} bytes")
    project = cfg.project
    if project is None:
        return
    if project.memory is None or project.diagnostics is None:
        rep.error(DIAG_LEVEL, ("security", index, "method"),
                  f"level {level.name} uses hmac-sha256, which answers with the device key of the bootloader's "
                  "security access (BL-03, key sector of the memory partitioning); the project has no "
                  f"{'memory partitioning (memory)' if project.memory is None else 'diagnostics section'}")
    elif project.diagnostics.bootloader.security.method != level.method:
        rep.error(DIAG_LEVEL, ("security", index, "method"),
                  f"level {level.name} uses {level.method}, the bootloader's security access "
                  f"{project.diagnostics.bootloader.security.method}: the device key in the key sector belongs to "
                  "the bootloader's method (project.yaml diagnostics.bootloader.security.method)")


def _check_rte_references(cfg: Config, rep: Reporter, desc) -> None:
    """Every `port:` and `runnable:` names a component of the ECU description (rte.yaml).

    Only the component is checked here. Whether the port, the element or the runnable exists and which type it
    has stands in the component's ARXML and is checked by tools/rtegen when it generates the jobs (COM-28).
    """
    ecu = cfg.rte
    if ecu is None:
        return                                      # no ECU description: reported at project.rte by checks.py
    components = {c.name for c in ecu.components}

    def component(path, reference: str, what: str) -> None:
        name = reference.split(".")[0]
        if name not in components:
            rep.error(DIAG_LEVEL, path, f"{what} {reference} names component {name}, which the ECU description "
                                        f"does not run ({cfg.rte_file.name} components: "
                                        f"{', '.join(sorted(components)) if components else 'none'})")

    for index, did in enumerate(desc.data_identifiers):
        for number, element in enumerate(did.data):
            if element.source.port is not None:
                component(("data_identifiers", index, "data", number, "source", "port"),
                          element.source.port, "the source")
    for index, routine in enumerate(desc.routines):
        for step in ("start", "stop", "results"):
            value = getattr(routine, step)
            if value is not None and value.call.runnable is not None:
                component(("routines", index, step, "call", "runnable"), value.call.runnable, "the call")
    for index, condition in enumerate(desc.enable_conditions):
        if condition.port is not None:
            component(("enable_conditions", index, "port"), condition.port, "the condition")
    for index, dtc in enumerate(desc.dtcs):
        if dtc.event.port is not None:
            component(("dtcs", index, "event", "port"), dtc.event.port, "the event")


# ----------------------------------------------------------------------------------------------- entry point


def check(cfg: Config, rep: Reporter) -> DiagDescriptionPlan | None:
    """Checks the diagnostic description of a project; None without one or after errors."""
    desc = cfg.diag
    if desc is None:
        return None
    errors = len(rep.messages)
    _duplicates(rep, desc.operation_cycles, ("operation_cycles",), "name")
    _duplicates(rep, desc.enable_conditions, ("enable_conditions",), "name")
    sessions = _check_sessions(rep, desc)
    security = _check_security(cfg, rep, desc, sessions)
    did_lengths = _check_dids(rep, desc, sessions, security)
    _check_routines(rep, desc, sessions, security)
    dtcs = _check_dtcs(rep, desc, did_lengths)
    dtc_groups = _check_dtc_groups(rep, desc)
    _check_bsw_range(rep, desc)
    _check_bsw_instances(cfg, rep, desc)            # COM-46/NVM-08
    _check_safe_off_take_back(cfg, rep, desc)       # SYS-196
    _check_rte_references(cfg, rep, desc)
    if any(m.severity == "error" for m in rep.messages[errors:]):
        return None
    return DiagDescriptionPlan(cfg.diag_file.as_posix() if cfg.diag_file else "", did_lengths, dtcs,
                               DTC_FORMAT_ISO_14229_1, STATUS_AVAILABILITY_NO_WARNING_INDICATOR,
                               dict(sorted((s.name, s.id) for s in desc.sessions)),
                               dict(sorted((s.name, s.level) for s in desc.security)), dtc_groups)


# ----------------------------------------------------------------------------------------------- tester side


@dataclass(frozen=True)
class DidRecord:
    """One identifier of a ReadDataByIdentifier response, cut out by the description (split_read_response)."""

    identifier: int
    name: str
    data: bytes                                     # the whole data record, `length_bytes` of the identifier
    elements: dict[str, bytes]                      # element name -> its bytes, in the order of `data`

    def text(self, element: str) -> str:
        """An `ascii` element without its fill bytes (COM-33: filled up with TEXT_FILL_BYTE)."""
        return self.elements[element].rstrip(bytes([TEXT_FILL_BYTE])).decode("ascii", "replace")


def split_read_response(desc, payload: bytes) -> list[DidRecord]:
    """Splits a positive ReadDataByIdentifier response (0x62, then identifier and data record, repeated) by the
    description `desc` (model.DiagDescription).

    The records stand back to back without a length of their own, so only the length of each identifier
    (`DataIdentifier.length_bytes`, fixed on the wire since COM-33) says where the next identifier begins. Raises
    ValueError for a response that does not fit the description (unknown identifier, cut off record).
    For testers (host tests, the Studio's tester, canflash), Qt free."""
    data = bytes(payload)
    if not data or data[0] != 0x62:
        raise ValueError(f"not a positive ReadDataByIdentifier response: {data[:1].hex() or 'empty'}")
    by_id = {did.identifier: did for did in desc.data_identifiers}
    records, pos = [], 1
    while pos < len(data):
        if pos + 2 > len(data):
            raise ValueError(f"byte {pos}: identifier cut off")
        identifier = int.from_bytes(data[pos:pos + 2], "big")
        did = by_id.get(identifier)
        if did is None:
            raise ValueError(f"byte {pos}: identifier {_hex16(identifier)} is not in the description, the rest of "
                             "the response cannot be split")
        start, end = pos + 2, pos + 2 + did.length_bytes
        if end > len(data):
            raise ValueError(f"byte {start}: {did.name} needs {did.length_bytes} bytes, {len(data) - start} are left")
        elements, offset = {}, start
        for element in did.data:
            elements[element.name] = data[offset:offset + element.width_bytes]
            offset += element.width_bytes
        records.append(DidRecord(identifier, did.name, data[start:end], elements))
        pos = end
    return records
