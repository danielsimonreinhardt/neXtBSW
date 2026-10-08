"""Generates the diagnostic jobs of the application (ecu/uds) from the diagnostic description (COM-28).

Input:
  project (project.yaml, tools/nxconfig): names the diagnostic description (`project.diag`, schema DiagDescription
  of tools/nxconfig/model.py, checked by tools/nxconfig/diagdesc.py, ARCH-51). A project without one gets
  tools/diaggen/default_diag.yaml: the identifiers the application answered before COM-28.
Output (<out>/, read by ecu/uds):
  diag/DiagConfig.h      sizes of the tables and the session timing of DiagnosticSessionControl (UdsConfig.h)
  DiagDescription.cpp    the tables of uds/app/DiagDescription.h and one read, write, check or step function per
                         data identifier and routine
  diag_components.cmake  DIAG_USES_RTE: whether the jobs call the RTE (rte::diag, RteDiag.h of tools/rtegen)
  diag/DiagDtcGroups.h   NVM-09: diagDtcGroups(), the groups of `dtc_groups` ClearDiagnosticInformation (14) clears
                         besides all DTCs and single DTCs; systems/DiagSystem hands them to uds::app::DiagStack.
                         Header only, so the build of ecu/uds needs no further source file.
  diag/DiagPreprogramming.h  COM-43: the security levels CommunicationControl (28) and ControlDTCSetting (85)
                         need - none in a development build (the extended session suffices), in a product build
                         (--product, NX_PRODUCT_BUILD of SYS-74) every level of the description that can be unlocked
                         in the extended session; a product build of a description without one is refused
  with --rte-stub also RteDiag.h and DiagRteStub.cpp: the RTE functions as a value store for host tests

What a job does:
  22/2E  every element of an identifier comes from its `source`: a constant (encoded here), a provided port
         (through rte::diag::readDid_<Did>, written with rte::diag::writeDid_<Did>), a value of the basic
         software the RTE reads (COM-32, `bsw:` ecu.*, adc.*, bsw.* as in the rte.yaml connections, also through
         readDid_<Did>; NRC 0x22 while it cannot be read) or a value of the diagnostic stack itself
         (STACK_VALUES). A write checks every element (range `min`/`max`, text table `values`) before it changes
         anything. Snapshots of the fault memory read the same functions (ecu/uds DiagDidReader, NVM-03).
  31     one function per step: request parameters checked and handed to rte::diag::routine_<Routine>_<step>,
         response parameters taken from it. The sequence (NRC 0x24) is kept by the service in ecu/uds.
  27     the security levels become a table with the number of their method (COM-30, `security[].method`,
         numbers of tools/nxconfig/diag.py SECURITY_METHOD_IDS); ecu/uds serves hmac-sha256 with the device key of
         the bootloader's security access (uds/app/DeviceKeySecurity.h). A tester reads the chip UID it derives
         the device key from through an identifier with `source: {bsw: chip.uid}` (usually 0xF18C).

Restrictions, each reported with file, line and YAML path (not silently cut):
  - sessions: ecu/uds runs the OpenBSW session objects, i.e. 0x01 (default) and 0x03 (extended); 0x02 belongs to
    the bootloader. DiagnosticSessionControl answers one P2/P2* for all of them, so the sessions must agree.
  - `bsw:` names: the values of the diagnostic stack (STACK_VALUES) or, with a prefix of rteapi.RTE_BSW_PREFIXES,
    a value of the basic software through the RTE (COM-32): read only, a scalar, and the project needs an ECU
    description. Which of these names the project has (channels, services) and which types they fit is checked
    by tools/rtegen (diag_bsw_values), which knows the board and the BSW targets.
  - routine `call: {bsw: ...}`: no BSW routine service exists yet; `call: {runnable: ...}` names the runnable that
    serves the routine through its routine.* ports (checked by tools/rtegen).
  - `ascii`/`bytes` routine parameters and port sources: the RTE connects scalars only.

Every identifier answers exactly its described length (COM-33): a text of the stack (bootloader.version,
application.version, chip.uid; COM-44: hardware.number, nextbsw.version; PLAT-79: configuration.identity; PLAT-93: workspace.version) or a text
constant shorter than `length_bytes` is filled up with
nxconfig.model.TEXT_FILL_BYTE (NUL), a longer one is cut. Several identifiers in one 22 request stand back to back
without a length of their own, so a tester can only split the response by the description
(nxconfig.diagdesc.split_read_response) - a record whose length depends on the runtime value would make every
identifier behind it unreadable. Until COM-33 the texts kept their runtime length (as before COM-28).

Qt free; `validate()` returns messages with file, line and YAML path for the Studio.
"""

import argparse
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import nxconfig  # noqa: E402
import nxconfig.config  # noqa: E402
from nxconfig import genlicense  # noqa: E402  (PLAT-120: licence line of generated files)
from nxconfig import model as nxmodel  # noqa: E402
from nxconfig import identity as nxidentity  # noqa: E402  (PLAT-79: configuration identity, manifest)
from nxconfig.diag import SECURITY_METHOD_IDS  # noqa: E402  (COM-30: method numbers of the bootloader)
import rteapi  # noqa: E402

DEFAULT_DESCRIPTION = HERE / "default_diag.yaml"
GENERATOR = "tools/diaggen"

# session types ecu/uds can be in (uds/session/DiagSession.h of ecu/uds/configuration)
SUPPORTED_SESSIONS = {0x01: "default", 0x03: "extended"}
DEFAULT_SESSION = 0x01
MAX_SESSIONS = 8           # bits of a session mask
MAX_SECURITY_LEVELS = 8    # bits of a security mask
MAX_DIDS_PER_REQUEST_CAP = 32

INTEGER_TYPES = ("uint8", "uint16", "uint32", "int8", "int16", "int32")
TEXT_TYPES = ("ascii", "bytes")

# Values the diagnostic stack itself provides (`source: {bsw: <name>}`), and how a job reaches them
STACK_VALUES = {
    "bootloader.version": {"types": TEXT_TYPES, "write": False, "text": "values.bootSoftwareId",
                           "what": "version text of the bootloader, found in its flash (NRC 0x22 without one)"},
    "application.version": {"types": TEXT_TYPES, "write": False, "text": "values.applicationSoftwareId",
                            "what": "version text of the application"},
    "uds.session": {"types": INTEGER_TYPES, "write": False, "expr": "values.activeSession",
                    "what": "session type of the active session"},
    "uds.ram_test": {"types": INTEGER_TYPES + ("float32",) + TEXT_TYPES, "write": True, "storage": True,
                     "what": "RAM value of the stack, as the tester wrote it (for write tests)"},
    # COM-30: the tester derives the device key of the security access from it (ECUSerialNumber 0xF18C, as the
    # bootloader answers it); its length is the derivative's (mcudb uid.size)
    "chip.uid": {"types": ("bytes",), "write": False, "text": "values.chipUid",
                 "what": "UID of the chip, 32-bit words in address order, each most significant byte first"},
    # COM-44: identification of the application (ecu/uds uds/app/ApplicationIdentification.h)
    "hardware.number": {"types": ("ascii",), "write": False, "text": "values.hardwareNumber",
                        "what": "hardware number \"<board>/<derivative>\" of the project (BL_HARDWARE_ID, the text "
                                "of 0xF191 of the bootloader)"},
    "nextbsw.version": {"types": ("ascii",), "write": False, "text": "values.nextbswVersion",
                        "what": "neXtBSW version of the tree and commit of the build (\"<VERSION> <git describe>\")"},
    # PLAT-79: SHA-256 over the configuration files of the project and the generators (tools/nxconfig/identity.py),
    # 64 hex digits; the same value the console shows ("info") and `nxconfig identity` prints
    "configuration.identity": {"types": ("ascii",), "write": False, "text": "values.configurationIdentity",
                               "what": "identity of configuration and generators of the image (SHA-256, 64 hex "
                                       "digits, nxconfig identity)"},
    # PLAT-93: git describe of the customer workspace (tools/diaggen/buildid.py, NX_DIAG_WORKSPACE_VERSION): "-" for
    # a project inside the neXtBSW tree, "nogit" for a workspace that is no repository
    "workspace.version": {"types": ("ascii",), "write": False, "text": "values.workspaceVersion",
                          "what": "commit of the customer workspace (git describe; \"-\" none, \"nogit\" no repository)"},
    # the record of 0xF1A0 of the bootloader field by field; version, the five 32-bit words and state in this order,
    # uint8/uint32 big endian, are its 22 bytes (checked against the bootloader by the host test)
    "compatibility.version": {"types": INTEGER_TYPES, "write": False, "expr": "values.compatibility.version",
                              "what": "version of the compatibility block (APP_COMPAT_VERSION)"},
    "compatibility.target": {"types": INTEGER_TYPES, "write": False, "expr": "values.compatibility.target",
                             "what": "target identity: CRC-32 of the hardware number (BL_TARGET_ID)"},
    "compatibility.layout": {"types": INTEGER_TYPES, "write": False, "expr": "values.compatibility.layout",
                             "what": "identity of the memory layout of the build (CRC-32 of its ten values)"},
    "compatibility.app_start": {"types": INTEGER_TYPES, "write": False, "expr": "values.compatibility.appStart",
                                "what": "start address of the application area (BL_APP_START)"},
    "compatibility.app_end": {"types": INTEGER_TYPES, "write": False, "expr": "values.compatibility.appEnd",
                              "what": "end address of the application area, exclusive (BL_APP_END)"},
    "compatibility.record_address": {"types": INTEGER_TYPES, "write": False,
                                     "expr": "values.compatibility.recordAddress",
                                     "what": "address of the validity record (BL_APP_RECORD_ADDR)"},
    "compatibility.state": {"types": INTEGER_TYPES, "write": False, "expr": "values.compatibility.state",
                            "what": "state of the installed validity record (0x00 OK, 0x01 format, 0x02 target, "
                                    "0x03 layout, 0x04 area, 0x10 no compatibility block, 0xFF no valid record)"},
}


class DiagGenError(Exception):
    def __init__(self, messages):
        super().__init__("\n".join(str(m) for m in messages))
        self.messages = messages


@dataclass
class Description:
    desc: object                 # model.DiagDescription
    file: Path
    tree: object
    uses_rte: bool = True        # the project has an ECU description (RTE)
    default: bool = False        # tools/diaggen/default_diag.yaml
    messages: list = field(default_factory=list)
    uid_bytes: int | None = None  # length of the chip UID (derivative uid.size), None: not known (description alone)
    product: bool = False        # COM-43: product build (NX_PRODUCT_BUILD, SYS-74): 28/85 need security access
    hardware_number: str | None = None  # COM-44: "<board>/<derivative>" of the project, None: description alone
    identity: object = None      # PLAT-79: nxconfig.identity.Identity of the project, None: description alone

    def error(self, path: tuple, text: str):
        self.message("error", path, text)

    def warning(self, path: tuple, text: str):
        self.message("warning", path, text)

    def message(self, severity: str, path: tuple, text: str):
        line = nxconfig.config.line_of(self.tree, path) if self.tree is not None else None
        self.messages.append(nxconfig.Message(severity, text, self.file, tuple(path), line))


# ------------------------------------------------------------------------------------------------ loading


def load_file(diag_file: Path, uses_rte: bool = True, default: bool = False) -> Description:
    desc, messages, tree = nxconfig.config.read_diag(Path(diag_file))
    if desc is None:
        raise DiagGenError(messages)
    return Description(desc, Path(diag_file), tree, uses_rte, default)


def load_project(project_file: Path, mcudb: Path | None = None, running_tree: Path | None = None) -> Description:
    """The description of a project, checked by tools/nxconfig (raises DiagGenError with its messages).

    `running_tree` as in validate() (TOOL-87): default None is the tree this package lies in.
    """
    try:
        config = nxconfig.load(Path(project_file).resolve(), mcudb or nxconfig.config.mcudb_dir(running_tree),
                               running_tree=running_tree)
    except nxconfig.ConfigErrors as e:
        raise DiagGenError(e.messages if hasattr(e, "messages") else [str(e)]) from None
    d = description_of(config)
    d.identity = nxidentity.of(config)          # PLAT-79: manifest of the generated files (write)
    return d


def description_of(config) -> Description:
    uses_rte = config.rte_file is not None
    if config.diag is None:
        d = load_file(DEFAULT_DESCRIPTION, uses_rte, default=True)
    else:
        source = config.sources.get(nxconfig.config.DIAG_LEVEL)
        d = Description(config.diag, config.diag_file, source.tree if source is not None else None, uses_rte)
    uid = getattr(config.derivative, "uid", None)
    d.uid_bytes = uid.size if uid is not None else None
    # COM-44: the hardware number the build answers - BL_HARDWARE_ID of tools/bswgen/blgen.py hardware_id()
    d.hardware_number = f"{config.board.board.name}/{config.derivative.derivative.name}"
    return d


# ------------------------------------------------------------------------------------------------ checks


@dataclass(frozen=True)
class SessionRow:
    name: str
    id: int
    p2_ms: int
    p2_star_ms: int


def sessions_of(desc) -> list[SessionRow]:
    """Sessions of the description; without any, the sessions the stack knows (schema: "served in every session
    the UDS stack knows"), with the schema's default timing."""
    if desc.sessions:
        return [SessionRow(s.name, s.id, s.p2_ms, s.p2_star_ms) for s in desc.sessions]
    p2 = nxmodel.DiagSession.model_fields["p2_ms"].default
    p2_star = nxmodel.DiagSession.model_fields["p2_star_ms"].default
    return [SessionRow(name.capitalize(), sid, p2, p2_star) for sid, name in SUPPORTED_SESSIONS.items()]


def session_mask(d: Description, names, without_default: bool = False) -> int:
    """Bit mask over sessions_of(); `names` None: every session (without the default session if asked)."""
    sessions = sessions_of(d.desc)
    if names is None:
        return sum(1 << i for i, s in enumerate(sessions) if not (without_default and s.id == DEFAULT_SESSION))
    index = {s.name: i for i, s in enumerate(sessions)}
    return sum(1 << index[n] for n in names if n in index)


def security_mask(d: Description, name) -> int:
    if name is None:
        return 0
    index = {s.name: i for i, s in enumerate(d.desc.security)}
    return 1 << index[name] if name in index else 0


EXTENDED_SESSION = 0x03    # the session of CommunicationControl and ControlDTCSetting (OpenBSW session mask)


def preprogramming_security_mask(d: Description) -> int:
    """COM-43: security mask CommunicationControl (28) and ControlDTCSetting (85) need (bit i: security[i]).

    Decision of 2026-09-29 (TOOL-131): during development the extended session suffices (0), a product build
    (SYS-74) needs security access - any level of the description that can be unlocked in the extended session,
    the only session both services are served in. Derived, no value of its own in diag.yaml."""
    if not d.product:
        return 0
    sessions = sessions_of(d.desc)
    extended = next((1 << i for i, s in enumerate(sessions) if s.id == EXTENDED_SESSION), 0)
    return sum(1 << i for i, level in enumerate(d.desc.security)
               if session_mask(d, level.sessions, without_default=True) & extended)


def check(d: Description) -> None:
    desc = d.desc
    if d.product and preprogramming_security_mask(d) == 0:
        # COM-43: without such a level a product image could never be quiesced before flashing
        d.error(("security",), "a product build (NX_PRODUCT_BUILD, SYS-74) serves CommunicationControl (28) and "
                               "ControlDTCSetting (85) only after security access: the description needs a security "
                               "level that can be unlocked in the extended session (0x03)")
    if len(desc.sessions) > MAX_SESSIONS:
        d.error(("sessions",), f"at most {MAX_SESSIONS} sessions (bits of the session masks of ecu/uds)")
    for i, s in enumerate(desc.sessions):
        if s.id not in SUPPORTED_SESSIONS:
            d.error(("sessions", i, "id"),
                    f"session type 0x{s.id:02X}: the application's UDS stack (ecu/uds, OpenBSW session objects) "
                    f"enters only 0x01 (default) and 0x03 (extended); 0x02 belongs to the bootloader")
        if s.p2_star_ms % 10:
            d.error(("sessions", i, "p2_star_ms"), f"p2_star_ms {s.p2_star_ms} is sent in units of 10 ms "
                                                   "(ISO 14229-2), use a multiple of 10")
        if s.p2_ms > 0xFFFF or s.p2_star_ms // 10 > 0xFFFF:
            d.error(("sessions", i), "P2 or P2*/10 does not fit the two bytes of the session response")
    if desc.sessions:
        first = desc.sessions[0]
        for i, s in enumerate(desc.sessions[1:], 1):
            if (s.p2_ms, s.p2_star_ms) != (first.p2_ms, first.p2_star_ms):
                d.error(("sessions", i, "p2_ms"),
                        f"P2/P2* {s.p2_ms}/{s.p2_star_ms} ms differs from {first.name} "
                        f"({first.p2_ms}/{first.p2_star_ms} ms): DiagnosticSessionControl of ecu/uds answers one "
                        "pair for every application session")
    if len(desc.security) > MAX_SECURITY_LEVELS:
        d.error(("security",), f"at most {MAX_SECURITY_LEVELS} security levels (bits of the security masks)")
    used_storage = None
    for i, did in enumerate(desc.data_identifiers):
        for j, e in enumerate(did.data):
            path = ("data_identifiers", i, "data", j, "source")
            if e.source.port is not None:
                if not d.uses_rte:
                    d.error(path + ("port",), "a port source needs an ECU description (project.rte), the project "
                                              "has none")
                if e.type not in rteapi.SCALAR_TYPES:
                    d.error(path + ("port",), f"type {e.type}: a port source carries a scalar (the RTE connects no "
                                              "arrays to the diagnosis yet)")
            if rteapi.is_rte_bsw(e.source.bsw):
                # COM-32: a value of the basic software, read by the RTE (names and types: tools/rtegen)
                if not d.uses_rte:
                    d.error(path + ("bsw",), f"{e.source.bsw} is read through the RTE, the project has no ECU "
                                             "description (project.rte)")
                if e.type not in rteapi.SCALAR_TYPES:
                    d.error(("data_identifiers", i, "data", j, "type"),
                            f"{e.source.bsw} is a value of the basic software, a scalar; type {e.type} cannot carry "
                            "it")
                if did.write:
                    d.error(("data_identifiers", i, "write"),
                            f"{did.name} is writable, but {e.name} comes from {e.source.bsw}, a value of the basic "
                            "software the diagnosis only reads")
                continue
            if e.source.bsw is not None:
                spec = STACK_VALUES.get(e.source.bsw)
                if spec is None:
                    d.error(path + ("bsw",),
                            f"{e.source.bsw} is neither a value of the diagnostic stack ({', '.join(STACK_VALUES)}) "
                            f"nor a value of the basic software read through the RTE "
                            f"({', '.join(p + '.*' for p in rteapi.RTE_BSW_PREFIXES)}, as in the rte.yaml "
                            "connections)")
                    continue
                if e.type not in spec["types"]:
                    d.error(("data_identifiers", i, "data", j, "type"),
                            f"{e.source.bsw} ({spec['what']}) cannot be {e.type} "
                            f"(one of {', '.join(spec['types'])})")
                if did.write and not spec["write"]:
                    d.error(("data_identifiers", i, "write"),
                            f"{did.name} is writable, but {e.name} comes from {e.source.bsw}, which only the stack "
                            "sets")
                if e.source.bsw == "chip.uid" and d.uid_bytes is not None and e.width_bytes != d.uid_bytes:
                    d.error(("data_identifiers", i, "data", j, "length_bytes"),
                            f"the chip UID of the derivative has {d.uid_bytes} bytes (mcudb uid.size), "
                            f"{e.name} {e.width_bytes}")
                # COM-44: a longer hardware number would be cut - it is an identity, so say so
                if (e.source.bsw == "hardware.number" and d.hardware_number is not None
                        and len(d.hardware_number) > e.width_bytes):
                    d.warning(("data_identifiers", i, "data", j, "length_bytes"),
                              f"the hardware number \"{d.hardware_number}\" of the project has "
                              f"{len(d.hardware_number)} characters, {e.name} answers {e.width_bytes}: the text is cut, "
                              f"raise length_bytes")
                if spec.get("storage"):
                    if used_storage is not None:
                        d.error(path + ("bsw",), f"{e.source.bsw} backs one element only, it already backs "
                                                 f"{used_storage}")
                    used_storage = f"{did.name}.{e.name}"
    for i, routine in enumerate(desc.routines):
        if not d.uses_rte:
            d.error(("routines", i), "a routine is served through the RTE, the project has no ECU description "
                                     "(project.rte)")
        for step in rteapi.STEPS:
            value = getattr(routine, step)
            if value is None:
                continue
            if value.call.bsw is not None:
                d.error(("routines", i, step, "call", "bsw"),
                        f"{value.call.bsw}: there is no BSW routine service yet; name the runnable that serves "
                        "the routine (call: {runnable: Component.Runnable}) and connect its routine.* ports")
            for kind in ("request", "response"):
                for k, p in enumerate(getattr(value, kind)):
                    if p.type not in rteapi.SCALAR_TYPES:
                        d.error(("routines", i, step, kind, k, "type"),
                                f"type {p.type}: routine parameters are scalars (the RTE connects no arrays)")


# ------------------------------------------------------------------------------------------------ C++ code


def c_ident(name: str) -> str:
    return name


def _float_bits(value: float) -> int:
    return struct.unpack(">I", struct.pack(">f", value))[0]


def encode_constant(element) -> list[int]:
    """Bytes of a constant element on the wire."""
    width = element.width_bytes
    value = element.source.constant
    if element.type in TEXT_TYPES:
        data = value.encode("ascii") if isinstance(value, str) else bytes([value])
        return list(data.ljust(width, bytes([nxmodel.TEXT_FILL_BYTE])))   # a shorter text is filled up (COM-33)
    if element.type == "float32":
        raw = _float_bits(float(value))
    else:
        raw = int(value) & ((1 << (8 * width)) - 1)
    data = raw.to_bytes(width, "big")
    return list(data if element.byte_order == "big" else data[::-1])


def hex_list(data) -> str:
    return ", ".join(f"0x{b:02X}U" for b in data)


def put_expr(element, expr: str) -> str:
    """Statement appending `expr` (C value of the element's type) with the element's width and byte order."""
    width = element.width_bytes
    big = "true" if element.byte_order == "big" else "false"
    if element.type == "float32":
        return f"if (!putFloat(response, {expr}, {big})) {{ return DiagReturnCode::ISO_RESPONSE_TOO_LONG; }}"
    return (f"if (!putUnsigned(response, static_cast<uint32_t>({expr}), {width}U, {big})) "
            f"{{ return DiagReturnCode::ISO_RESPONSE_TOO_LONG; }}")


def get_expr(element, data: str, offset: int) -> str:
    """C expression decoding the element at `offset` of `data` into its C type."""
    ctype = rteapi.C_TYPES[element.type]
    big = "true" if element.byte_order == "big" else "false"
    if element.type == "float32":
        return f"getFloat(&{data}[{offset}U], {big})"
    raw = f"getUnsigned(&{data}[{offset}U], {element.width_bytes}U, {big})"
    if element.type.startswith("int"):
        bits = 8 * element.width_bytes
        return f"static_cast<{ctype}>(signExtend({raw}, {bits}U))"
    return f"static_cast<{ctype}>({raw})"


def range_check(element, var: str) -> list[str]:
    """Statements refusing a decoded value outside the described range (NRC 0x31)."""
    if getattr(element, "values", None):
        allowed = " && ".join(f"({var} != {c_literal(element, v.value)})" for v in element.values)
        return [f"if ({allowed}) {{ return DiagReturnCode::ISO_REQUEST_OUT_OF_RANGE; }}"]
    limits = rteapi.coded_range(element)
    if limits is None:
        return []
    lo, hi = limits
    conds = []
    if element.type in rteapi.TYPE_RANGE:
        tlo, thi = rteapi.TYPE_RANGE[element.type]
        if lo > tlo:
            conds.append(f"({var} < {c_literal(element, lo)})")
        if hi < thi:
            conds.append(f"({var} > {c_literal(element, hi)})")
    else:
        conds += [f"({var} < {lo!r}F)", f"({var} > {hi!r}F)"]
    return [f"if ({' || '.join(conds)}) {{ return DiagReturnCode::ISO_REQUEST_OUT_OF_RANGE; }}"] if conds else []


def c_literal(element, value) -> str:
    if element.type == "float32":
        return f"{float(value)!r}F"
    value = int(value)
    if element.type.startswith("int"):
        if value == -(1 << 31):
            return "(-2147483647 - 1)"
        return f"{value}"
    return f"{value}U"


@dataclass
class Generated:
    config_h: str
    source_cpp: str
    cmake: str
    rte_header: str
    rte_stub: str
    dtc_groups_h: str = ""          # NVM-09: diag/DiagDtcGroups.h
    preprogramming_h: str = ""      # COM-43: diag/DiagPreprogramming.h


def generate_code(d: Description) -> Generated:
    desc = d.desc
    source = d.file.name
    fns = {f.name: f for f in rteapi.functions(desc)}
    body: list[str] = []
    did_rows, routine_rows, level_rows = [], [], []
    storage = None                                          # (did, element) of uds.ram_test

    for did in desc.data_identifiers:
        ident = c_ident(did.name)
        read_len = did.length_bytes
        # --- read
        read_fn = "nullptr"
        if did.read:
            read_fn = f"read_{ident}"
            lines = [f"// 0x{did.identifier:04X} {did.name}: read ({read_len} bytes)",
                     f"DiagReturnCode::Type {read_fn}(DiagValues const& values, PositiveResponse& response)", "{"]
            uses_values = False
            rte_fn = fns.get(f"readDid_{did.name}")
            if rte_fn is not None:
                for p in rte_fn.params:
                    lines.append(f"    {p.ctype} {p.name} = {'0.0F' if p.diag_type == 'float32' else '0'};")
                # COM-32: false while a value of the basic software cannot be read (e.g. a failed conversion)
                lines.append(f"    if (!::rte::diag::{rte_fn.name}({', '.join(p.name for p in rte_fn.params)})) "
                             "{ return DiagReturnCode::ISO_CONDITIONS_NOT_CORRECT; }")
            for e in did.data:
                src = e.source
                if src.constant is not None:
                    lines.append(f"    static uint8_t const {e.name}_bytes[] = {{{hex_list(encode_constant(e))}}};")
                    lines.append(f"    if (response.appendData({e.name}_bytes, sizeof({e.name}_bytes)) "
                                 f"!= sizeof({e.name}_bytes)) {{ return DiagReturnCode::ISO_RESPONSE_TOO_LONG; }}")
                elif src.port is not None or rteapi.is_rte_bsw(src.bsw):
                    lines.append("    " + put_expr(e, e.name))
                else:
                    spec = STACK_VALUES[src.bsw]
                    if "text" in spec:
                        uses_values = True
                        text = spec["text"]
                        fill = f"0x{nxmodel.TEXT_FILL_BYTE:02X}U"
                        lines += [f"    // {src.bsw}: {spec['what']}; always {e.width_bytes} bytes, a shorter value "
                                  f"filled up with {fill} (COM-33)",
                                  f"    if ({text}.empty()) {{ return DiagReturnCode::ISO_CONDITIONS_NOT_CORRECT; }}",
                                  f"    if (response.getAvailableDataLength() < {e.width_bytes}U) "
                                  "{ return DiagReturnCode::ISO_RESPONSE_TOO_LONG; }",
                                  f"    size_t const {e.name}_length = ({text}.size() < {e.width_bytes}U) "
                                  f"? {text}.size() : {e.width_bytes}U;",
                                  f"    (void)response.appendData({text}.data(), {e.name}_length);",
                                  f"    for (size_t i = {e.name}_length; i < {e.width_bytes}U; ++i) "
                                  f"{{ (void)response.appendUint8({fill}); }}"]
                    elif "expr" in spec:
                        uses_values = True
                        lines.append("    " + put_expr(e, spec["expr"]))
                    else:
                        storage = (did, e)
                        lines.append(f"    if (response.appendData(ramTest, sizeof(ramTest)) != sizeof(ramTest)) "
                                     "{ return DiagReturnCode::ISO_RESPONSE_TOO_LONG; }")
            if not uses_values:
                lines.insert(3, "    (void)values;")
            lines += ["    return DiagReturnCode::OK;", "}", ""]
            body += lines
        else:
            for e in did.data:
                if e.source.bsw is not None and STACK_VALUES.get(e.source.bsw, {}).get("storage"):
                    storage = (did, e)
        # --- write
        write_fn = "nullptr"
        if did.write:
            write_fn = f"write_{ident}"
            lines = [f"// 0x{did.identifier:04X} {did.name}: write ({read_len} bytes, length checked by the service)",
                     f"DiagReturnCode::Type {write_fn}(uint8_t const data[], uint16_t const /* length */)", "{"]
            offset = 0
            apply = []
            rte_fn = fns.get(f"writeDid_{did.name}")
            for e in did.data:
                if e.source.port is not None:
                    lines.append(f"    {rteapi.C_TYPES[e.type]} const {e.name} = {get_expr(e, 'data', offset)};")
                    lines += ["    " + s for s in range_check(e, e.name)]
                elif e.source.bsw is not None and not rteapi.is_rte_bsw(e.source.bsw):
                    storage = (did, e)
                    apply.append(f"    (void)::memcpy(ramTest, &data[{offset}U], sizeof(ramTest));")
                offset += e.width_bytes
            if rte_fn is not None:
                apply.append(f"    ::rte::diag::{rte_fn.name}({', '.join(p.name for p in rte_fn.params)});")
            lines += ["    // every value is checked before anything is written"] + apply
            lines += ["    return DiagReturnCode::OK;", "}", ""]
            body += lines
        did_rows.append(
            f"    {{0x{did.identifier:04X}U, {read_len}U, {read_len if did.write else 0}U, "
            f"0x{session_mask(d, did.read_sessions) if did.read else 0:02X}U, "
            f"0x{session_mask(d, did.write_sessions) if did.write else 0:02X}U, "
            f"0x{security_mask(d, did.read_security):02X}U, 0x{security_mask(d, did.write_security):02X}U, "
            f"{read_fn}, {write_fn}}}, // {did.name}")

    request_max = response_max = 0
    for routine in desc.routines:
        steps = []
        for step in rteapi.STEPS:
            value = getattr(routine, step)
            if value is None:
                steps.append("{0U, nullptr, nullptr}")
                continue
            fn = fns[f"routine_{routine.name}_{step}"]
            req_len = sum(p.width_bytes for p in value.request)
            resp_len = sum(p.width_bytes for p in value.response)
            request_max, response_max = max(request_max, req_len), max(response_max, resp_len)
            call = f" ({value.call.runnable})" if value.call.runnable else ""
            decode, checks, offset = [], [], 0
            for p in value.request:
                decode.append(f"    {rteapi.C_TYPES[p.type]} const {p.name} = {get_expr(p, 'request', offset)};")
                checks += ["    " + s for s in range_check(p, p.name)]
                offset += p.width_bytes
            check_fn = "nullptr"
            if checks:
                check_fn = f"check_{routine.name}_{step}"
                body += [f"// 0x{routine.identifier:04X} {routine.name}: range of the {step} request",
                         f"DiagReturnCode::Type {check_fn}(uint8_t const request[], uint16_t const /* length */)",
                         "{"] + decode + checks + ["    return DiagReturnCode::OK;", "}", ""]
            run_fn = f"run_{routine.name}_{step}"
            lines = [f"// 0x{routine.identifier:04X} {routine.name}: {step}{call}",
                     f"DiagReturnCode::Type {run_fn}(uint8_t const request[], uint16_t const /* length */, "
                     "PositiveResponse& response)", "{"]
            if not value.request:
                lines.append("    (void)request;")
            if not value.response:
                lines.append("    (void)response;")
            lines += decode
            for p in value.response:
                lines.append(f"    {rteapi.C_TYPES[p.type]} {p.name} = {'0.0F' if p.type == 'float32' else '0'};")
            lines.append(f"    ::rte::diag::{fn.name}({', '.join(p.name for p in fn.params)});")
            for p in value.response:
                lines.append("    " + put_expr(p, p.name))
            lines += ["    return DiagReturnCode::OK;", "}", ""]
            body += lines
            steps.append(f"{{{req_len}U, {check_fn}, {run_fn}}}")
        routine_rows.append(
            f"    {{0x{routine.identifier:04X}U, 0x{session_mask(d, routine.sessions):02X}U, "
            f"0x{security_mask(d, routine.security):02X}U, {{{', '.join(steps)}}}}}, // {routine.name}")

    for level in desc.security:
        level_rows.append(
            f"    {{0x{level.level:02X}U, {level.seed_bytes}U, {level.key_bytes}U, {level.attempts}U, "
            f"{level.delay_ms}U, 0x{session_mask(d, level.sessions, without_default=True):02X}U, "
            f"{SECURITY_METHOD_IDS[level.method]}U}}, // {level.name} ({level.method})")

    readable = sum(1 for did in desc.data_identifiers if did.read)
    max_dids = max(1, min(readable, MAX_DIDS_PER_REQUEST_CAP))
    session_rows = sessions_of(desc)
    p2, p2_star = session_rows[0].p2_ms, session_rows[0].p2_star_ms
    seed_max = max([lv.seed_bytes for lv in desc.security] or [1])
    key_max = max([lv.key_bytes for lv in desc.security] or [1])
    uses_rte = bool(fns)

    config_h = "\n".join([
        f"// Generated by {GENERATOR} from {source} - do not edit.",
        "",
        "#pragma once",
        "",
        "#include <cstddef>",
        "",
        "// Session timing answered by DiagnosticSessionControl (ecu/uds/configuration UdsConfig.h): P2 in ms, P2* in",
        "// units of 10 ms, the same for every application session of the description.",
        f"#define NX_DIAG_P2_MS {p2}U",
        f"#define NX_DIAG_P2_STAR_10MS {p2_star // 10}U",
        "",
        "// Sizes of the tables and buffers of uds/app/DiagDescriptionServices.h (arrays: at least 1)",
        f"constexpr size_t DIAG_DID_COUNT                    = {len(desc.data_identifiers)}U;",
        f"constexpr size_t DIAG_ROUTINE_COUNT                = {len(desc.routines)}U;",
        f"constexpr size_t DIAG_ROUTINE_COUNT_ARRAY          = {max(1, len(desc.routines))}U;",
        f"constexpr size_t DIAG_SECURITY_LEVEL_COUNT         = {len(desc.security)}U;",
        f"constexpr size_t DIAG_SECURITY_LEVEL_COUNT_ARRAY   = {max(1, len(desc.security))}U;",
        f"constexpr size_t DIAG_SEED_BYTES_ARRAY             = {seed_max}U;",
        f"constexpr size_t DIAG_KEY_BYTES_ARRAY              = {key_max}U;",
        f"constexpr size_t DIAG_MAX_DIDS_PER_REQUEST         = {max_dids}U; // readable identifiers",
        f"constexpr size_t DIAG_ROUTINE_REQUEST_BYTES_ARRAY  = {max(1, request_max)}U;",
        f"constexpr size_t DIAG_ROUTINE_RESPONSE_BYTES_ARRAY = {max(1, response_max)}U;",
        ""])

    head = [
        f"// Generated by {GENERATOR} from {source} - do not edit.",
        "//",
        "// Diagnostic jobs of the description (COM-28): tables of uds/app/DiagDescription.h, served by",
        "// uds/app/DiagDescriptionServices.h. Values from components go through the RTE (rte::diag, RteDiag.h).",
        "",
        "#include \"uds/app/DiagDescription.h\"",
        "",
        "#include <diag/DiagConfig.h>",
    ]
    if uses_rte:
        head.append("#include <RteDiag.h>")
    head += ["", "#include <cstring>", "", "namespace uds", "{", "namespace app", "{", "namespace", "{"]
    helpers = [
        "// big or little endian, `width` bytes of `value`",
        "[[maybe_unused]] bool putUnsigned(PositiveResponse& response, uint32_t const value, uint8_t const width, "
        "bool const big)",
        "{",
        "    if (response.getAvailableDataLength() < width)",
        "    {",
        "        return false;",
        "    }",
        "    for (uint8_t i = 0U; i < width; ++i)",
        "    {",
        "        uint8_t const shift = static_cast<uint8_t>(8U * (big ? (width - 1U - i) : i));",
        "        (void)response.appendUint8(static_cast<uint8_t>((value >> shift) & 0xFFU));",
        "    }",
        "    return true;",
        "}",
        "",
        "[[maybe_unused]] bool putFloat(PositiveResponse& response, float const value, bool const big)",
        "{",
        "    uint32_t bits = 0U;",
        "    (void)::memcpy(&bits, &value, sizeof(bits));",
        "    return putUnsigned(response, bits, 4U, big);",
        "}",
        "",
        "[[maybe_unused]] uint32_t getUnsigned(uint8_t const data[], uint8_t const width, bool const big)",
        "{",
        "    uint32_t value = 0U;",
        "    for (uint8_t i = 0U; i < width; ++i)",
        "    {",
        "        uint8_t const byte = big ? data[i] : data[width - 1U - i];",
        "        value              = (value << 8U) | byte;",
        "    }",
        "    return value;",
        "}",
        "",
        "[[maybe_unused]] int32_t signExtend(uint32_t const value, uint8_t const bits)",
        "{",
        "    uint32_t const sign = 1UL << (bits - 1U);",
        "    uint32_t const mask = (bits >= 32U) ? 0xFFFFFFFFUL : ((1UL << bits) - 1U);",
        "    uint32_t const v    = value & mask;",
        "    return ((v & sign) != 0U) ? static_cast<int32_t>(v | ~mask) : static_cast<int32_t>(v);",
        "}",
        "",
        "[[maybe_unused]] float getFloat(uint8_t const data[], bool const big)",
        "{",
        "    uint32_t const bits = getUnsigned(data, 4U, big);",
        "    float value         = 0.0F;",
        "    (void)::memcpy(&value, &bits, sizeof(value));",
        "    return value;",
        "}",
        "",
    ]
    if storage is not None:
        did, e = storage
        helpers += [f"// uds.ram_test of {did.name}.{e.name}: the bytes the tester wrote",
                    f"uint8_t ramTest[{e.width_bytes}] = {{}};", ""]
    tables = ["", "DidEntry const DIDS[] = {"] + did_rows + ["};"] if did_rows else []
    tables += ["", "RoutineEntry const ROUTINES[] = {"] + routine_rows + ["};"] if routine_rows else []
    tables += ["", "SecurityLevel const SECURITY_LEVELS[] = {"] + level_rows + ["};"] if level_rows else []
    sessions = ", ".join(f"0x{s.id:02X}U" for s in session_rows)
    tables += ["", f"uint8_t const SESSIONS[] = {{{sessions}}}; // bit i of a session mask: SESSIONS[i]", ""]
    tail = ["} // namespace", "",
            "DiagDescriptionTables const& diagDescription()", "{",
            "    static DiagDescriptionTables const tables = {",
            "        ::etl::span<uint8_t const>(SESSIONS),",
            f"        {'::etl::span<DidEntry const>(DIDS)' if did_rows else '{}'},",
            f"        {'::etl::span<RoutineEntry const>(ROUTINES)' if routine_rows else '{}'},",
            f"        {'::etl::span<SecurityLevel const>(SECURITY_LEVELS)' if level_rows else '{}'},",
            f"        {max_dids}U}};",
            "    return tables;", "}", "",
            "::etl::span<uint8_t> diagRamTest()", "{",
            "    return ::etl::span<uint8_t>(ramTest);" if storage is not None else "    return {};",
            "}", "", "} // namespace app", "} // namespace uds", ""]
    source_cpp = "\n".join(head + helpers + body + tables + tail)
    cmake = (f"# Generated by {GENERATOR} from {source} - do not edit.\n"
             f"set(DIAG_USES_RTE {'TRUE' if uses_rte else 'FALSE'})\n")
    return Generated(config_h, source_cpp, cmake, rteapi.render_header(desc, source, GENERATOR),
                     render_rte_stub(desc, source), render_dtc_groups(desc, source),
                     render_preprogramming(d, source))


def render_preprogramming(d: Description, source: str) -> str:
    """COM-43: diag/DiagPreprogramming.h - which build the tables were generated for and the security mask
    CommunicationControl (28) and ControlDTCSetting (85) need (uds/app/AppProgrammingPreconditions.h)."""
    mask = preprogramming_security_mask(d)
    names = [level.name for i, level in enumerate(d.desc.security) if mask & (1 << i)]
    return "\n".join([
        f"// Generated by {GENERATOR} from {source} - do not edit.",
        "//",
        "// COM-43 (decision 2026-09-29): CommunicationControl (28) and ControlDTCSetting (85) are served in the extended",
        "// session. During development that suffices; a product build (NX_PRODUCT_BUILD, SYS-74) needs security access",
        "// as well - any level of the description that can be unlocked in the extended session (bit i: security[i]).",
        "",
        "#pragma once",
        "",
        "#include <cstdint>",
        "",
        f"#define NX_DIAG_PRODUCT_BUILD {1 if d.product else 0}",
        "",
        "namespace uds",
        "{",
        "namespace app",
        "{",
        f"constexpr uint8_t DIAG_PREPROGRAMMING_SECURITY = 0x{mask:02X}U; // "
        + (", ".join(names) if names else "no security access (development build)"),
        "} // namespace app",
        "} // namespace uds",
        ""])


def render_dtc_groups(desc, source: str) -> str:
    """NVM-09: diag/DiagDtcGroups.h, the table of `dtc_groups` (ISO 14229-1, Annex D.1) as uds::app::DtcGroup.

    Checked by tools/nxconfig (diagdesc._check_dtc_groups: not 0xFFFFFF, not a DTC number, first <= last below the
    group identifiers); the service in ecu/uds takes 0xFFFFFF and a described DTC number before the table."""
    rows = [f"        {{0x{g.group:06X}U, 0x{g.first:06X}U, 0x{g.last:06X}U}}, // {g.name}"
            for g in desc.dtc_groups]
    lines = [f"// Generated by {GENERATOR} from {source} - do not edit.",
             "//",
             "// Groups of DTCs ClearDiagnosticInformation (14) clears besides all DTCs (0xFFFFFF) and a single DTC",
             "// (NVM-09, ISO 14229-1 Annex D.1): `dtc_groups` of the description, {group, first, last}.",
             "// systems/DiagSystem hands them to uds::app::DiagStack.",
             "",
             "#pragma once",
             "",
             "#include \"uds/app/FaultMemoryServices.h\"",
             "",
             "#include <etl/span.h>",
             "",
             "#include <cstddef>",
             "",
             "namespace uds",
             "{",
             "namespace app",
             "{",
             f"constexpr size_t DIAG_DTC_GROUP_COUNT = {len(rows)}U;",
             "",
             "inline ::etl::span<DtcGroup const> diagDtcGroups()",
             "{"]
    if rows:
        lines += ["    static DtcGroup const GROUPS[] = {"] + rows
        lines += ["    };", "    return ::etl::span<DtcGroup const>(GROUPS);"]
    else:
        lines.append("    return {};")
    lines += ["}", "", "} // namespace app", "} // namespace uds", ""]
    return "\n".join(lines)


def render_rte_stub(desc, source: str) -> str:
    """Host test stand-in for the RTE functions: every value is a slot the test sets and reads by target name."""
    fns = rteapi.functions(desc)
    slots = []
    for fn in fns:
        slots += [p.target for p in fn.params]
        if fn.counter:
            slots.append(fn.counter)
    slots = list(dict.fromkeys(slots))
    index = {name: i for i, name in enumerate(slots)}
    lines = [f"// Generated by {GENERATOR} from {source} for host tests - do not edit.",
             "//",
             "// The RTE functions of RteDiag.h as a value store: a test sets what a provided port would deliver and",
             "// reads what the diagnosis handed to a required port (diag_stub_set/get by connection target).",
             "",
             "#include \"RteDiag.h\"",
             "",
             "#include <cstring>",
             "",
             "#ifdef _WIN32",
             "#define DIAG_STUB_EXPORT __declspec(dllexport)",
             "#else",
             "#define DIAG_STUB_EXPORT __attribute__((visibility(\"default\")))",
             "#endif",
             "",
             "namespace",
             "{",
             f"char const* const NAMES[] = {{{', '.join(chr(34) + s + chr(34) for s in slots) or 'nullptr'}}};",
             f"uint32_t values[{max(1, len(slots))}] = {{}}; // raw bits of the value",
             f"uint32_t writes[{max(1, len(slots))}] = {{}}; // how often the diagnosis wrote the slot",
             f"constexpr unsigned SLOT_COUNT = {len(slots)}U;",
             "bool available = true; // what every read reports (false: a BSW value cannot be read, COM-32)",
             "",
             "template<typename T>",
             "T get(unsigned const slot)",
             "{",
             "    T value;",
             "    if (sizeof(T) == 4U)",
             "    {",
             "        (void)::memcpy(&value, &values[slot], sizeof(value));",
             "    }",
             "    else",
             "    {",
             "        value = static_cast<T>(values[slot]);",
             "    }",
             "    return value;",
             "}",
             "",
             "template<typename T>",
             "void put(unsigned const slot, T const value)",
             "{",
             "    uint32_t bits = 0U;",
             "    if (sizeof(T) == 4U)",
             "    {",
             "        (void)::memcpy(&bits, &value, sizeof(bits));",
             "    }",
             "    else",
             "    {",
             "        bits = static_cast<uint32_t>(value) & ((1UL << (8U * sizeof(T))) - 1U);",
             "    }",
             "    values[slot] = bits;",
             "    ++writes[slot];",
             "}",
             "",
             "int find(char const* const name)",
             "{",
             "    for (unsigned i = 0U; i < SLOT_COUNT; ++i)",
             "    {",
             "        if (::strcmp(NAMES[i], name) == 0)",
             "        {",
             "            return static_cast<int>(i);",
             "        }",
             "    }",
             "    return -1;",
             "}",
             "} // namespace",
             "",
             "namespace rte",
             "{",
             "namespace diag",
             "{"]
    for fn in fns:
        lines += [fn.signature(), "{"]
        for p in fn.params:
            slot = index[p.target]
            if p.direction == "out":
                lines.append(f"    {p.name} = get<{p.ctype}>({slot}U); // {p.target}")
            else:
                lines.append(f"    put<{p.ctype}>({slot}U, {p.name}); // {p.target}")
        if fn.counter:
            slot = index[fn.counter]
            lines.append(f"    ++values[{slot}U]; // {fn.counter}")
            lines.append(f"    ++writes[{slot}U];")
        if fn.returns == "bool":
            lines.append("    return available;")
        lines += ["}", ""]
    lines += ["} // namespace diag", "} // namespace rte", "",
              "extern \"C\"",
              "{",
              "DIAG_STUB_EXPORT int diag_stub_set(char const* const name, uint32_t const bits)",
              "{",
              "    int const slot = find(name);",
              "    if (slot >= 0)",
              "    {",
              "        values[slot] = bits;",
              "    }",
              "    return slot;",
              "}",
              "",
              "DIAG_STUB_EXPORT uint32_t diag_stub_get(char const* const name)",
              "{",
              "    int const slot = find(name);",
              "    return (slot >= 0) ? values[slot] : 0xDEADBEEFU;",
              "}",
              "",
              "DIAG_STUB_EXPORT uint32_t diag_stub_writes(char const* const name)",
              "{",
              "    int const slot = find(name);",
              "    return (slot >= 0) ? writes[slot] : 0U;",
              "}",
              "",
              "// COM-32: 0 lets every read report a value of the basic software as not readable (NRC 0x22)",
              "DIAG_STUB_EXPORT void diag_stub_available(int const yes)",
              "{",
              "    available = (yes != 0);",
              "}",
              "}", ""]
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------------ entry points


def plan(d: Description) -> Generated:
    check(d)
    if any(m.severity == "error" for m in d.messages):
        raise DiagGenError(d.messages)
    return generate_code(d)


def write(generated: Generated, out_dir: Path, rte_stub: bool = False, identity=None) -> None:
    """Writes the generated files (only those whose content changes); with `identity` (PLAT-79,
    nxconfig.identity.Identity of the project) also identity/diaggen.sha256, the manifest of this run."""
    files = {"diag/DiagConfig.h": generated.config_h, "DiagDescription.cpp": generated.source_cpp,
             "diag_components.cmake": generated.cmake, "diag/DiagDtcGroups.h": generated.dtc_groups_h,
             "diag/DiagPreprogramming.h": generated.preprogramming_h}
    if rte_stub:
        files["RteDiag.h"] = generated.rte_header
        files["DiagRteStub.cpp"] = generated.rte_stub
    files = genlicense.stamp_all(files)                                  # PLAT-120, before the manifest hashes them
    if identity is not None:
        files[nxidentity.manifest_path("diaggen")] = genlicense.stamp(
            identity.manifest("diaggen", files), nxidentity.manifest_path("diaggen"))
    for name, content in files.items():
        target = Path(out_dir) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or target.read_text(encoding="utf-8") != content:
            target.write_text(content, encoding="utf-8", newline="\n")


def validate(project_file: Path, mcudb: Path | None = None, running_tree: Path | None = None,
             product: bool = False) -> list:
    """Every finding of a generator run, as messages with file, line and YAML path; writes nothing (Studio).

    `product` (COM-43): check for a product build (NX_PRODUCT_BUILD, SYS-74) - 28/85 need a security level of the
    extended session then. Default False, the development build every call made before COM-43.

    TOOL-87 - `running_tree`: which neXtBSW tree this call runs as, handed down to `nxconfig.check()`
    (TOOL-85 describes the rule). Default None keeps the behaviour of every call that exists today: the tree
    this package lies in, so a workspace pin that names another tree stays refused on the command line. The
    Studio switches the tree per opened project (TOOL-61) and says so here; without this parameter it had to
    move the module variable `nxconfig.config.REPO` under a lock (studio/core/tree.py `as_tool_of`, TOOL-60).

    Without `mcudb` the derivative database is the one of the running tree - which is `nxconfig.MCUDB` exactly
    when `running_tree` is not given, so nothing changes for the callers of today.
    """
    config, messages = nxconfig.check(Path(project_file).resolve(),
                                      mcudb or nxconfig.config.mcudb_dir(running_tree),
                                      running_tree=running_tree)
    messages = list(messages)
    if config is None:
        return messages
    try:
        d = description_of(config)
    except DiagGenError as e:
        return messages + list(e.messages)
    d.product = product
    check(d)
    return messages + d.messages


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("project", type=Path, help="project (projects/<project>/project.yaml)")
    parser.add_argument("out", type=Path, nargs="?", help="output directory")
    parser.add_argument("--diag", type=Path, help="diagnostic description on its own instead of project.diag "
                                                  "(test profiles)")
    parser.add_argument("--rte-stub", action="store_true", help="also write RteDiag.h and DiagRteStub.cpp "
                                                               "(host tests without RTE)")
    parser.add_argument("--validate", action="store_true", help="only check, write nothing")
    parser.add_argument("--product", action="store_true", help="product build (NX_PRODUCT_BUILD, SYS-74): 28 and 85 "
                                                              "need security access (COM-43)")
    args = parser.parse_args()
    try:
        if args.validate:
            messages = validate(args.project, product=args.product)
            for m in messages:
                print(f"diaggen: {m}", file=sys.stderr)
            sys.exit(1 if any(m.severity == "error" for m in messages) else 0)
        if args.out is None:
            parser.error("the output directory is required unless --validate is given")
        d = load_file(args.diag) if args.diag else load_project(args.project)
        d.product = args.product
        write(plan(d), args.out, args.rte_stub, identity=d.identity)
    except DiagGenError as e:
        for m in e.messages:
            print(f"diaggen: {m}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
