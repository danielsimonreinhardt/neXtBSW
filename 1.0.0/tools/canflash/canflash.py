"""Flash the ECU application over CAN (UDS on ISO-TP) via the neXtBSW bootloader, update the bootloader."""

import argparse
import contextlib
import datetime
import os
import re
import struct
import sys
import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import can
import isotp
import udsoncan
from intelhex import IntelHex
from udsoncan.client import Client
from udsoncan.connections import PythonIsoTpConnection
from udsoncan.exceptions import NegativeResponseException

sys.path.insert(0, str(Path(__file__).resolve().parent))
import blkeys  # noqa: E402  (keys of the security access, BL-03)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from nxconfig import genlicense  # noqa: E402  (PLAT-120: licence line of generated files)
import flashguard  # noqa: E402  (configuration field and CSEc risk, NVM-15)
import powercycle  # noqa: E402  (optional power cycle while waiting, TOOL-51)

ROOT = Path(__file__).resolve().parents[2]
BL_CONFIG = ROOT / "bootloader" / "include" / "bl_config.h"
UPD_CONFIG = ROOT / "bootloader" / "updater" / "include" / "upd_config.h"
# generated memory layout (tools/bswgen/blgen.py) relative to a build directory, see find_layout()
LAYOUT_IN_BUILD = Path("generated") / "board" / "bootloader" / "bl_memory.h"
# generated diagnostic addresses (COM-11), written next to the memory layout
DIAG_HEADER_NAME = "bl_diag.h"
LAYOUT_KEYS = ("BL_FLASH_START", "BL_FLASH_SIZE", "BL_SECTOR_SIZE", "BL_PHRASE_SIZE", "BL_APP_START", "BL_APP_END",
               "BL_APP_RECORD_ADDR", "BL_RAM_START", "BL_RAM_END", "BL_BOOT_REQUEST_ADDR")
# diagnostic addresses of the project (bl_diag.h): the tester talks to bootloader and application with these
DIAG_KEYS = ("BL_CAN_ID_REQUEST_PHYS", "BL_CAN_ID_REQUEST_FUNC", "BL_CAN_ID_RESPONSE", "BL_ISOTP_PADDING",
             "BL_ISOTP_BLOCK_SIZE", "BL_ISOTP_STMIN_MS", "BL_ISOTP_N_CR_MS", "BL_ISOTP_N_BS_MS")
# board/derivative identity of the build (BL-02); 0 = not known, then only the memory layout is compared
TARGET_KEY = "BL_TARGET_ID"
# identification of the bootloader software, from the project (bl_diag.h BL_VERSION_PREFIX, COM-11)
PREFIX_KEY = "BL_VERSION_PREFIX"
# MCU family of the build or project (derivative family): selects the bootloader image rule (BL-08). blgen writes
# it into the generated bl_memory.h (BL-10); a project gives it by its derivative.
FAMILY_KEY = "BL_FAMILY"
# BL-10: board and derivative in plain text (generated bl_memory.h), what the bootloader answers for DID 0xF191
HARDWARE_KEY = "BL_HARDWARE_ID"
# generated bl_memory.h the selected layout was read from (key in the values of load_build(), not a macro)
LAYOUT_FILE_KEY = "layout_file"
# BL-03: key sector of the memory layout (bl_memory.h) and security access of the project (generated bl_security.h
# next to it): method, chip UID, name of the master key on the workstation
SECURITY_HEADER_NAME = "bl_security.h"
KEY_AREA_KEYS = ("BL_KEY_ADDR", "BL_KEY_AREA_SIZE")
SECURITY_KEYS = ("BL_SECURITY_METHOD", "BL_SECURITY_TEST_KEY_ALLOWED", "BL_UID_ADDR", "BL_UID_LEN")
KEY_NAME_KEY = "BL_SECURITY_KEY_NAME"
KEY_NAME_ENV = "NX_BL_KEY_NAME"         # TOOL-204: name of the master key without bl_security.h (or --key-name)

RID_ERASE_MEMORY = 0xFF00
RID_CHECK_MEMORY = 0xFF01
# BL-02 compatibility check. BL-24 moved it into the system supplier range of ISO 14229-1 Annex F.1
# (0xF000..0xFEFF); bootloaders up to 0.3.3 know only the former number, reserved by Annex F.1. The tester sends
# the one the connected bootloader knows (compat_routine_of), a bootloader since BL-24 answers both.
RID_CHECK_COMPAT = 0xF1A0
RID_CHECK_COMPAT_FORMER = 0xFF02
COMPAT_ROUTINE_SINCE = (0, 3, 4)       # first BL_VERSION_NUMBER with RID_CHECK_COMPAT
DID_BOOT_SOFTWARE_ID = 0xF180
DID_TARGET_COMPAT = 0xF1A0
DID_APPLICATION_SOFTWARE_ID = 0xF181   # answered by the application only (ecu/uds)
DID_ACTIVE_SESSION = 0xF186            # ActiveDiagnosticSession, answered by bootloader and application
# BL-04, ISO 14229-1: fingerprint of the programming session, software version and hardware number
DID_FINGERPRINT = 0xF184               # applicationSoftwareFingerprint, written before erasing and downloading
DID_ECU_SW_VERSION = 0xF189            # vehicleManufacturerECUSoftwareVersionNumber, ASCII
DID_ECU_HW_NUMBER = 0xF191             # vehicleManufacturerECUHardwareNumber, "<board>/<derivative>" (BL-10)
# BL-03: chip UID (ISO 14229-1 ECUSerialNumber) and state of the security access (format, method, key state,
# seed length, key length), both answered in every session by bootloaders from BL_SECURITY_HMAC_SINCE on
DID_ECU_SERIAL = 0xF18C
DID_SECURITY_INFO = 0xF1A1
KEY_STATES = {0: "no device key provisioned (key sector erased)",
              1: "device key provisioned",
              2: "the key sector holds no valid key record (half written, other method or damaged)"}
RECORD_MAGIC = 0x56505041
COMPAT_MAGIC = 0x43505041          # "APPC", compatibility block of the validity record (BL-02)
COMPAT_VERSION = 1
COMPAT_SIZE = 32                   # compatMagic .. compatCrc, see bootloader/src/core/app_image.h
FINGERPRINT_MAGIC = 0x46505041     # "APPF", fingerprint block behind the compatibility block (BL-10)
COMPAT_REASONS = {
    0x00: "compatible",
    0x01: "compatibility block damaged or of an unusable version",
    0x02: "built for another board or derivative",
    0x03: "other memory partitioning (flash, sectors, RAM, boot request)",
    0x04: "other application area (start, end, record address)",
    0x10: "no compatibility field (record written before BL-02)",
    0xFF: "no valid validity record",
}


# TOOL-54: an integer constant with every C suffix (u, l, ul, lu, ll, ull, llu in either case, ll/LL not mixed).
# Before, only "U", "L" and "UL" were read: '#define BL_TARGET_ID 0x12345678u' in a bl_memory.h passed with
# --layout did not match, the identity counted as 0 and the target check was skipped with only a note (board
# 2026-09-22, capture 109_tool53b_lowercase_u_accepted.log).
_INT_SUFFIX = r"(?:[uU](?:ll|LL|[lL])?|(?:ll|LL|[lL])[uU]?)?"
_NUMERIC_DEFINE = re.compile(r"#define\s+((?:BL|UPD|NX)_\w+)\s+(0[xX][0-9A-Fa-f]+|\d+)" + _INT_SUFFIX + r"(?![\w.])")
# every object-like macro definition of these prefixes with the first token of its value (finds unreadable ones)
_ANY_DEFINE = re.compile(r"^[ \t]*#[ \t]*define[ \t]+((?:BL|UPD|NX)_\w+)(?![\w(])[ \t]*([^\s/]*)", re.MULTILINE)


def _int_literal(text: str) -> int:
    return int(text, 16) if text[:2] in ("0x", "0X") else int(text, 10)


def load_config(*paths: Path) -> dict[str, int]:
    """Numeric macros of bl_config.h/upd_config.h or of a generated header (bl_memory.h, bl_diag.h)."""
    values = {}
    for path in paths or (BL_CONFIG, UPD_CONFIG):
        text = path.read_text()
        for name, value in _NUMERIC_DEFINE.findall(text):
            values[name] = _int_literal(value)
    return values


def unreadable_numbers(path: Path, keys) -> list[str]:
    """Macros of `keys` that `path` defines but load_config() cannot read as a number (TOOL-54), each as
    "<name> '<value>'". A typo like '0x1234567G' or '0x12345678uu' must not count as "not defined": for
    BL_TARGET_ID "not defined" means identity 0, and that switches the target check off."""
    path = Path(path)
    read = load_config(path)
    return [f"{name} '{value}'" for name, value in _ANY_DEFINE.findall(path.read_text())
            if name in keys and name not in read]


def load_strings(*paths: Path) -> dict[str, str]:
    """String macros of a header, e.g. BL_VERSION_PREFIX of the generated bl_diag.h."""
    texts = {}
    for path in paths:
        for name, value in re.findall(r'#define\s+((?:BL|UPD|NX)_\w+)\s+"([^"]*)"', Path(path).read_text()):
            texts[name] = value
    return texts


# UDS timing and updater values at import; memory layout and diagnostic addresses come from the build or the
# project (use_layout(), select_layout()) - they are generated per project since ARCH-08 and COM-11.
CFG = load_config()
CFG.setdefault(TARGET_KEY, 0)
TEXT: dict[str, str] = {}
# where the selected layout came from (LAYOUT_FILE_KEY), named when the layout lacks a generated value
SOURCE: dict[str, Path] = {}
# version number of the bootloader software; the prefix in front of it belongs to the project (bl_diag.h)
BL_VERSION_NUMBER = re.search(r'#define\s+BL_VERSION_NUMBER\s+"([^"]+)"', BL_CONFIG.read_text()).group(1)
# identification of this bootloader software before the rename to neXtBSW (PLAT-08, bl_config.h): bootloaders
# flashed before report it; image checks and version warnings accept it besides the project's prefix
BL_VERSION_PREFIX_FORMER = load_strings(BL_CONFIG)["BL_VERSION_PREFIX_FORMER"]
# layout of the fingerprint (BL-04), from bl_config.h - the bootloader and the tester have one source for it
FINGERPRINT_DATE_LEN = CFG["BL_FINGERPRINT_DATE_LEN"]
FINGERPRINT_TESTER_LEN = CFG["BL_FINGERPRINT_TESTER_LEN"]
FINGERPRINT_LEN = CFG["BL_FINGERPRINT_LEN"]
FINGERPRINT_BLOCK_SIZE = 8 + FINGERPRINT_LEN   # fpMagic .. fpCrc of the validity record (BL-10, app_image.h)
# Tester identification when none is given: a value of the workplace, not of the project (architecture
# section 4, like the CAN adapter or the debugger's serial number), therefore an environment variable.
TESTER_ID_ENV = "NX_TESTER_ID"
TESTER_ID_DEFAULT = "canflash"
# BL-03: seed and key of the HMAC security access (bl_config.h)
SEED_LEN = CFG["BL_SECURITY_SEED_LEN"]
KEY_LEN = CFG["BL_SECURITY_KEY_LEN"]
# Master key for this process instead of the key store (host tests: the insecure test key); see use_master_key()
MASTER_KEY: "blkeys.MasterKey | None" = None


def bl_version() -> str:
    """BL_VERSION_STRING of the selected project: identification prefix, space, version number."""
    prefix = TEXT.get(PREFIX_KEY)
    if prefix is None:
        raise ValueError(f"no bootloader identification ({PREFIX_KEY}): pass --layout "
                         f"<build>/generated/board/bootloader/bl_memory.h or --project projects/<project>/project.yaml")
    return f"{prefix} {BL_VERSION_NUMBER}"


def accepted_prefixes(prefix: str | None = None) -> list[str]:
    """Prefixes a bootloader of the selected project may report: the project's one and the former name (PLAT-08)."""
    own = TEXT.get(PREFIX_KEY) if prefix is None else prefix
    return [p for p in dict.fromkeys((own, BL_VERSION_PREFIX_FORMER)) if p is not None]


def target_id(board: str, derivative: str) -> int:
    """Board/derivative identity of the validity record: CRC-32 over "<board>/<derivative>" (BL-02).

    The generator computes the same value in tools/nxconfig/memory.py and writes it into the generated
    bl_memory.h as BL_TARGET_ID; test_blgen.py holds both implementations together. It is repeated here on
    purpose: importing the configuration model would cost the tester a second in the middle of a UDS session
    (P2 timeout, found by tools/canflash/test_bl_update_mutations.py)."""
    return zlib.crc32(f"{board}/{derivative}".encode("ascii"))


def layout_id(values: dict[str, int] | None = None) -> int:
    """Identity of a memory layout: CRC-32 over the ten LAYOUT_KEYS values as little-endian 32-bit words.
    app_layout_id() in bootloader/src/core/app_image.c computes exactly this from the generated macros."""
    v = CFG if values is None else values
    return zlib.crc32(struct.pack("<10I", *(v[k] for k in LAYOUT_KEYS)))


def _project_config(project: Path):
    sys.path.insert(0, str(ROOT / "tools" / "bswgen"))
    import blgen  # noqa: E402
    try:
        config = blgen.nxconfig.load(Path(project))
    except blgen.nxconfig.ConfigErrors as e:
        raise ValueError(f"{project}: {e}") from None
    if config.memory is None:
        raise ValueError(f"{project}: no memory partitioning (project memory)")
    return blgen, config


def layout_from_project(project: Path) -> dict[str, int]:
    """Memory layout of a project, calculated like the build does (nxconfig + blgen), without writing files."""
    blgen, config = _project_config(project)
    return blgen.memory_values(config.memory)


def target_from_project(project: Path) -> dict[str, int]:
    """Memory layout, board/derivative identity (BL-02) and diagnostic addresses (COM-11) of a project."""
    blgen, config = _project_config(project)
    values = blgen.memory_values(config.memory)
    values[TARGET_KEY] = target_id(config.board.board.name, config.derivative.derivative.name)
    if config.diagnostics is None:
        raise ValueError(f"{project}: no diagnostic addresses (project diagnostics)")
    values.update(blgen.diag_values(config.diagnostics))
    values[PREFIX_KEY] = config.diagnostics.version_prefix          # string, moved into TEXT by use_layout()
    values[FAMILY_KEY] = config.derivative.derivative.family        # string, moved into TEXT by use_layout()
    values[HARDWARE_KEY] = f"{config.board.board.name}/{config.derivative.derivative.name}"   # BL-10, as blgen
    values.update(blgen.security_values(config.diagnostics))        # BL-03
    values[KEY_NAME_KEY] = config.diagnostics.key_name              # string, moved into TEXT by use_diag()
    return values


def use_layout(values: dict) -> None:
    """Puts memory layout and diagnostic addresses into CFG/TEXT (in place, importers of CFG see it).

    Diagnostic addresses only if `values` carries them, so that a caller can switch the memory layout alone
    (the tests compare the records of two builds that way). The MCU family (BL-08) and the file the layout came
    from belong to the layout: a layout without them forgets those of the previous one."""
    missing = [k for k in LAYOUT_KEYS if k not in values]
    if missing:
        raise ValueError(f"memory layout lacks {', '.join(missing)}")
    if any(k in values for k in (*DIAG_KEYS, PREFIX_KEY)):
        use_diag(values)
    CFG.update({k: values[k] for k in LAYOUT_KEYS})
    CFG[TARGET_KEY] = values.get(TARGET_KEY, 0)
    for key in KEY_AREA_KEYS:                       # BL-03: layouts generated before have no key sector
        if key in values:
            CFG[key] = values[key]
        else:
            CFG.pop(key, None)
    SOURCE.pop(LAYOUT_FILE_KEY, None)
    for key in (FAMILY_KEY, HARDWARE_KEY):
        TEXT.pop(key, None)
        if values.get(key):
            TEXT[key] = str(values[key])
    if values.get(LAYOUT_FILE_KEY):
        SOURCE[LAYOUT_FILE_KEY] = Path(values[LAYOUT_FILE_KEY])


def use_diag(values: dict) -> None:
    """Puts the diagnostic addresses of a build or project into CFG/TEXT (bl_diag.h, COM-11)."""
    missing = [k for k in (*DIAG_KEYS, PREFIX_KEY) if k not in values]
    if missing:
        raise ValueError(f"diagnostic addresses lack {', '.join(missing)} (generated bl_diag.h, project "
                         "diagnostics)")
    CFG.update({k: values[k] for k in DIAG_KEYS})
    TEXT[PREFIX_KEY] = str(values[PREFIX_KEY])
    # BL-03: security access of the project, if the values carry it (a build before BL-03 does not)
    TEXT.pop(KEY_NAME_KEY, None)                     # TOOL-204: no name of a layout used before
    if KEY_NAME_KEY in values:
        CFG.update({k: values[k] for k in SECURITY_KEYS if k in values})
        TEXT[KEY_NAME_KEY] = str(values[KEY_NAME_KEY])


def find_layout(*hints: Path) -> Path | None:
    """Generated bl_memory.h of the build a file belongs to: <build>/generated/board/bootloader/bl_memory.h in a
    parent directory of a hint (e.g. <build>/ecu/application/RelWithDebInfo/app.ecu.hex)."""
    for hint in hints:
        for parent in Path(hint).resolve().parents:
            if (parent / LAYOUT_IN_BUILD).is_file():
                return parent / LAYOUT_IN_BUILD
    return None


def load_build(layout: Path) -> dict:
    """Values of a generated bl_memory.h and of the bl_diag.h next to it (one build, one project)."""
    layout = Path(layout)
    diag = layout.parent / DIAG_HEADER_NAME
    if not diag.is_file():
        raise ValueError(f"{diag} is missing: the build was generated before COM-11 (no diagnostic addresses); "
                         "configure it again or pass --project projects/<project>/project.yaml")
    security = layout.parent / SECURITY_HEADER_NAME      # BL-03; a build before it has none
    bad = [f"{path}: {entry}"
           for path, keys in ((layout, (*LAYOUT_KEYS, TARGET_KEY, *KEY_AREA_KEYS)), (diag, DIAG_KEYS),
                              (security, SECURITY_KEYS))
           if path.is_file() for entry in unreadable_numbers(path, keys)]
    if bad:
        # TOOL-54: a defined but unreadable value is an error, not 0 (BL_TARGET_ID 0 = identity unknown)
        raise ValueError("cannot read as an integer constant: " + "; ".join(bad) + " (expected decimal or 0x "
                         "hex with an optional C suffix u, l, ul, ll, ull)")
    values: dict = load_config(layout, diag)
    values.update(load_strings(diag))
    if security.is_file():
        values.update(load_config(security))
        values.update(load_strings(security))
    strings = load_strings(layout)
    for key in (FAMILY_KEY, HARDWARE_KEY):
        if strings.get(key):
            values[key] = strings[key]
    values[LAYOUT_FILE_KEY] = layout
    return values


def select_layout(layout: str | None, project: str | None, *hints: Path, need_layout: bool = True) -> None:
    """--layout, --project, environment NX_BL_LAYOUT / NX_PROJECT, or the build directory of a hex file.

    Without `need_layout` (command "info") only the diagnostic addresses are needed, but they also come from
    the build or the project - no address of this tool is a constant any more (COM-11).
    """
    keys = (*LAYOUT_KEYS, *DIAG_KEYS) if need_layout else DIAG_KEYS
    if all(k in CFG for k in keys) and PREFIX_KEY in TEXT and not (layout or project):
        return
    layout = layout or os.environ.get("NX_BL_LAYOUT")
    project = project or os.environ.get("NX_PROJECT")
    if layout:
        use_layout(load_build(Path(layout)))
    elif project:
        use_layout(target_from_project(Path(project)))
    else:
        found = find_layout(*hints)
        if found is None:
            raise ValueError("no memory layout and no diagnostic addresses: pass --layout "
                             "<build>/generated/board/bootloader/bl_memory.h or "
                             "--project projects/<project>/project.yaml")
        use_layout(load_build(found))


# ---------------------------------------------------------------- several ECUs on one bus (TOOL-05)

@dataclass(frozen=True)
class Ecu:
    """One ECU as its project describes it: name and diagnostic addresses (project diagnostics, COM-11)."""

    name: str
    project: Path
    prefix: str
    diag: tuple                                   # (macro name, value) of bl_diag.h, see blgen.diag_values()

    def value(self, key: str) -> int:
        return dict(self.diag)[key]

    @property
    def request_id(self) -> int:
        return self.value("BL_CAN_ID_REQUEST_PHYS")

    @property
    def response_id(self) -> int:
        return self.value("BL_CAN_ID_RESPONSE")

    @property
    def functional_id(self) -> int:
        return self.value("BL_CAN_ID_REQUEST_FUNC")

    @property
    def padding(self) -> int:
        return self.value("BL_ISOTP_PADDING")


def project_files(sources) -> list[Path]:
    """project.yaml files of --projects: a project.yaml, a project directory, or a directory of projects."""
    found = []
    for source in sources:
        source = Path(source)
        if source.is_file():
            found.append(source)
        elif (source / "project.yaml").is_file():
            found.append(source / "project.yaml")
        elif source.is_dir():
            found.extend(sorted(source.glob("*/project.yaml")))
        else:
            raise ValueError(f"--projects {source}: neither a project.yaml nor a directory")
    return found


def ecus_from_projects(sources, log=None) -> list[Ecu]:
    """ECUs of the projects that have diagnostic addresses; projects without them (or broken ones) are skipped
    with a note, so that one unfinished project in projects/ does not stop a scan."""
    ecus = []
    for path in project_files(sources):
        try:
            blgen, config = _project_config(path)
        except ValueError as e:
            if log:
                log(f"  skipped {path}: {e}")
            continue
        if config.diagnostics is None:
            if log:
                log(f"  skipped {path}: no diagnostic addresses (project diagnostics)")
            continue
        values = blgen.diag_values(config.diagnostics)
        ecus.append(Ecu(config.project.project.name, path, config.diagnostics.version_prefix,
                        tuple(sorted(values.items()))))
    check_ecus(ecus)
    return ecus


def check_ecus(ecus: list[Ecu]) -> None:
    """Projects with the same request **and** response id are alternatives for one ECU (projects/ has two
    projects of the same board, demo and charge); they cannot be told apart on the bus, only by what is flashed.
    Sharing only one of the two ids is a conflict: the answers of one ECU would be taken for the other's."""
    for i, a in enumerate(ecus):
        for b in ecus[i + 1:]:
            if a.name == b.name:
                raise ValueError(f"ECU name {a.name} used by {a.project} and {b.project}")
            same = [text for key, text in (("request_id", "request"), ("response_id", "response"))
                    if getattr(a, key) == getattr(b, key)]
            if len(same) == 1:
                key = "request_id" if same[0] == "request" else "response_id"
                raise ValueError(f"ECUs {a.name} and {b.name} use the same {same[0]} id 0x{getattr(a, key):X} "
                                 f"but different other ids ({a.project}, {b.project}): they cannot share a bus")


def address_groups(ecus: list[Ecu]) -> list[list[Ecu]]:
    """ECUs grouped by (request id, response id): one group is one diagnostic node on the bus."""
    groups: dict[tuple[int, int], list[Ecu]] = {}
    for ecu in ecus:
        groups.setdefault((ecu.request_id, ecu.response_id), []).append(ecu)
    return list(groups.values())


def project_by_name(name: str, sources) -> Path:
    """project.yaml whose project.name is `name` (--ecu). Only the name is read, so that choosing one ECU does not
    cost a full configuration check of every project (one to two seconds each)."""
    import yaml

    names = {}
    for path in project_files(sources):
        try:
            entry = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("project") or {}
        except (yaml.YAMLError, OSError, AttributeError):
            continue
        if entry.get("name") == name:
            return path
        names[str(entry.get("name"))] = path
    raise ValueError(f"--ecu {name}: no project of this name (known: {', '.join(sorted(names)) or 'none'})")


TESTER_PRESENT = bytes([0x02, 0x3E, 0x00])
TESTER_PRESENT_OK = bytes([0x02, 0x7E, 0x00])


def scan_bus(bus: can.BusABC, ecus: list[Ecu], seconds: float = 0.3) -> tuple[list[Ecu], list[int]]:
    """TesterPresent (3E 00) to every configured ECU, physically, and once per functional id.

    Returns the configured ECUs that answered and the CAN ids of answers no configured ECU owns (an ECU of
    another project on the same functional id). Only single frames 02 7E 00 count, cyclic messages of the
    applications do not."""
    drain(bus)
    requests = [(e.request_id, e.padding) for e in ecus]
    requests += sorted({(e.functional_id, e.padding) for e in ecus})
    answered: set[int] = set()
    for can_id, padding in requests:
        bus.send(can.Message(arbitration_id=can_id, data=TESTER_PRESENT + bytes([padding] * 5), is_extended_id=False))
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        msg = bus.recv(0.01)
        if msg is not None and not msg.is_error_frame and bytes(msg.data[:3]) == TESTER_PRESENT_OK:
            answered.add(msg.arbitration_id)
    known = {e.response_id for e in ecus}
    return [e for e in ecus if e.response_id in answered], sorted(answered - known)


@contextlib.contextmanager
def addresses_of(ecu: Ecu):
    """CFG/TEXT switched to the diagnostic addresses of `ecu` for the duration of the block (make_client)."""
    saved_cfg, saved_text = dict(CFG), dict(TEXT)
    use_diag({**dict(ecu.diag), PREFIX_KEY: ecu.prefix})
    try:
        yield ecu
    finally:
        CFG.clear()
        CFG.update(saved_cfg)
        TEXT.clear()
        TEXT.update(saved_text)


def identify(bus: can.BusABC, ecu: Ecu) -> dict[str, str | None]:
    """What runs on an ECU that answered: bootloader identification (F180, from the bootloader or from the
    application's flash search) and application identification (F181, only an application answers it)."""
    with addresses_of(ecu), make_client(bus) as client:
        boot = read_boot_identification(client)
        app = read_application_identification(client)
    return {"boot": boot, "app": app}


def read_application_identification(client: Client) -> str | None:
    """DID 0xF181 of a running application, None if the bootloader answers (TOOL-45).

    Only the application knows the DID (ecu/uds); the bootloader answers requestOutOfRange. This is how canflash
    tells who is on the diagnostic addresses without changing the session: the application answers DID 0xF180 as
    well (with the identification found in the bootloader's flash), but none of the bootloader's own DIDs
    (0xF189, 0xF191, 0xF18C, 0xF1A1, 0xF1A0)."""
    client.set_config("data_identifiers", {**did_codecs(), DID_APPLICATION_SOFTWARE_ID: _IdentificationRest()})
    try:
        with quiet(client):
            return client.read_data_by_identifier_first(DID_APPLICATION_SOFTWARE_ID)
    except (NegativeResponseException, udsoncan.exceptions.TimeoutException):
        return None



def load_app_image(path: Path) -> bytes:
    """Returns the application bytes from APP_START, gaps filled with 0xFF, record sector excluded."""
    ih = IntelHex(str(path))
    start, end = CFG["BL_APP_START"], CFG["BL_APP_END"]
    addresses = [a for a in ih.addresses() if start <= a < end]
    if not addresses:
        raise ValueError(f"{path}: no data in application range 0x{start:X}..0x{end:X}")
    outside = [a for a in ih.addresses() if a < start]
    if outside:
        raise ValueError(f"{path}: data below application start (0x{min(outside):X}), wrong linker script?")
    # BL-02: silently dropping data above the area would flash a truncated image of a larger derivative
    above = [a for a in ih.addresses() if a >= end]
    if above:
        raise ValueError(f"{path}: data at 0x{max(above):X}, above the application area "
                         f"0x{start:X}..0x{end - 1:X} (image of another derivative or memory layout?)")
    ih.padding = 0xFF
    return ih.tobinstr(start=start, end=max(addresses))


def app_record(data: bytes) -> bytes:
    """Validity record of milestone 2 (24 bytes), unchanged so that a bootloader from before BL-02 accepts it."""
    start = CFG["BL_APP_START"]
    head = struct.pack("<IIII", RECORD_MAGIC, start, len(data), zlib.crc32(data))
    return head + struct.pack("<II", zlib.crc32(head), 0xFFFFFFFF)


def compat_block() -> bytes:
    """Compatibility block of the validity record (BL-02, 32 bytes): board/derivative and memory layout identity of
    the build. Older bootloaders read only the 24 bytes in front of it and ignore this."""
    head = struct.pack("<IIIIIII", COMPAT_MAGIC, COMPAT_VERSION, COMPAT_SIZE, CFG[TARGET_KEY], layout_id(),
                       CFG["BL_APP_END"], CFG["BL_APP_RECORD_ADDR"])
    return head + struct.pack("<I", zlib.crc32(head))


def app_record_full(data: bytes) -> bytes:
    """Validity record as the bootloader writes it since BL-02: 24 bytes as before plus the compatibility block.
    The fingerprint block behind it (BL-10) stays erased: a record made by the tool has no programming session."""
    return app_record(data) + compat_block()


def fingerprint_block(fingerprint: bytes) -> bytes:
    """Fingerprint block of the validity record (BL-10): what the bootloader writes behind the compatibility block
    when the programming session had a fingerprint (DID 0xF184)."""
    if len(fingerprint) != FINGERPRINT_LEN:
        raise ValueError(f"fingerprint of {len(fingerprint)} bytes, BL_FINGERPRINT_LEN is {FINGERPRINT_LEN}")
    head = struct.pack("<I", FINGERPRINT_MAGIC) + bytes(fingerprint)
    return head + struct.pack("<I", zlib.crc32(head))


def image_compat() -> dict[str, int]:
    """Target values the image in CFG's memory layout belongs to (routine 0xF1A0 or 0xFF02, DID 0xF1A0)."""
    return {"version": COMPAT_VERSION, "target": CFG[TARGET_KEY], "layout": layout_id(),
            "app_start": CFG["BL_APP_START"], "app_end": CFG["BL_APP_END"],
            "record_addr": CFG["BL_APP_RECORD_ADDR"]}


# ---------------------------------------------------------------- fingerprint of a programming session (BL-04)


def _bcd(value: int) -> int:
    if not 0 <= value <= 99:
        raise ValueError(f"{value} does not fit into one BCD byte")
    return ((value // 10) << 4) | (value % 10)


def _from_bcd(byte: int) -> int:
    high, low = byte >> 4, byte & 0x0F
    if high > 9 or low > 9:
        raise ValueError(f"0x{byte:02X} is not BCD")
    return high * 10 + low


def tester_identification(tester: str | bytes | None = None) -> bytes:
    """The tester's part of the fingerprint: `tester`, the environment variable NX_TESTER_ID or 'canflash'.

    Padded with zero bytes to BL_FINGERPRINT_TESTER_LEN. A longer identification is an error instead of a
    silent truncation - the fingerprint is supposed to say who programmed, not a part of it."""
    if tester is None:
        tester = os.environ.get(TESTER_ID_ENV) or TESTER_ID_DEFAULT
    raw = tester if isinstance(tester, bytes) else str(tester).encode("ascii", "replace")
    if len(raw) > FINGERPRINT_TESTER_LEN:
        raise ValueError(f"tester identification {tester!r} is longer than {FINGERPRINT_TESTER_LEN} bytes")
    return raw.ljust(FINGERPRINT_TESTER_LEN, b"\0")


def fingerprint_bytes(tester: str | bytes | None = None, date: datetime.date | None = None) -> bytes:
    """The BL_FINGERPRINT_LEN bytes of DID 0xF184: programming date in BCD, then the tester identification."""
    day = date or datetime.date.today()
    header = bytes([_bcd(day.year // 100), _bcd(day.year % 100), _bcd(day.month), _bcd(day.day)])
    if len(header) != FINGERPRINT_DATE_LEN:
        raise ValueError(f"BL_FINGERPRINT_DATE_LEN is {FINGERPRINT_DATE_LEN}, the date is {len(header)} bytes: "
                         "bl_config.h and this encoding have drifted apart")
    return header + tester_identification(tester)


def parse_fingerprint(payload: bytes) -> dict:
    """{date, tester, raw} of a fingerprint read back; `date` is None if its BCD bytes are not a date."""
    if len(payload) != FINGERPRINT_LEN:
        raise RuntimeError(f"DID 0x{DID_FINGERPRINT:04X}: {len(payload)} instead of {FINGERPRINT_LEN} bytes")
    try:
        date = datetime.date(_from_bcd(payload[0]) * 100 + _from_bcd(payload[1]),
                             _from_bcd(payload[2]), _from_bcd(payload[3]))
    except ValueError:
        date = None
    tester = bytes(payload[FINGERPRINT_DATE_LEN:]).rstrip(b"\0")
    return {"date": date, "tester": tester.decode("ascii", "replace"), "raw": bytes(payload)}


def format_fingerprint(value: dict) -> str:
    return f"{value['date'].isoformat() if value['date'] else 'no valid date'} by '{value['tester']}'"


def isotp_params() -> dict:
    """Tester side of the ISO-TP connection, from the project's diagnostic addresses (bl_diag.h)."""
    return {
        "tx_padding": CFG["BL_ISOTP_PADDING"],
        "tx_data_min_length": 8,
        # STmin and block size the tester asks the ECU for; the ECU announces its own values in its flow control
        "stmin": CFG["BL_ISOTP_STMIN_MS"],
        "blocksize": CFG["BL_ISOTP_BLOCK_SIZE"],
        "rx_flowcontrol_timeout": CFG["BL_ISOTP_N_BS_MS"],
        "rx_consecutive_frame_timeout": CFG["BL_ISOTP_N_CR_MS"],
        # TOOL-48: send() returns only when the last consecutive frame is out, so the client's P2 starts at the end
        # of the request (ISO 14229-2: P2client runs from the request's confirmation), not when sending begins
        "blocking_send": True,
    }


# TOOL-48: upper bound for sending one request, only against a stalled ISO-TP thread - flow control that stays away
# (N_Bs) and a broken transmission end the send themselves. Per frame: the longest STmin (127 ms) plus scheduling
# gaps of the PC; never below SEND_TIMEOUT_MIN_S.
SEND_TIMEOUT_PER_FRAME_S = 0.25
SEND_TIMEOUT_MIN_S = 30.0


def send_timeout(length: int) -> float:
    """Longest time the tester waits for a request of `length` bytes to be sent completely (TOOL-48)."""
    frames = 1 if length <= 7 else 1 + -(-(length - 6) // 7)
    return max(SEND_TIMEOUT_MIN_S, frames * SEND_TIMEOUT_PER_FRAME_S)


class NxIsoTpConnection(PythonIsoTpConnection):
    """ISO-TP connection of canflash: every request is sent completely before the client starts its P2 (TOOL-48).

    udsoncan starts P2 right after conn.send(). With a non-blocking send that was the moment the first frame was
    queued, so a long request on a slow link (large STmin of the ECU, scheduling gaps of the PC) used up P2 before
    its last frame was out (board 2026-09-21: STmin 1 ms, gaps of 1-80 ms under Windows, a 1026-byte block took more
    than 1 s). A transmission the stack gives up (no flow control within N_Bs, overflow) ends as a TimeoutException
    of udsoncan like a missing response, so every command reports it the same way."""

    def specific_send(self, payload: bytes, timeout: float | None = None) -> None:
        try:
            super().specific_send(payload, send_timeout(len(payload)) if timeout is None else timeout)
        except isotp.errors.BlockingSendFailure as e:
            self.empty_txqueue()
            raise udsoncan.exceptions.TimeoutException(
                f"request of {len(payload)} bytes not sent completely - no flow control from the ECU within "
                f"N_Bs or transmission aborted ({type(e).__name__})") from e


class _AsciiRest(udsoncan.DidCodec):
    """Reads the rest of the response as ASCII (software version number, DID 0xF189)."""

    def decode(self, payload):
        return bytes(payload).decode("ascii", "replace").rstrip("\0")

    def __len__(self):
        raise udsoncan.DidCodec.ReadAllRemainingData


class Identification(str):
    """Text of an identification identifier (0xF180, 0xF181), plus which form the server answered in.

    It is a str, so everything that only wants the text (split_boot_version(), boot_version_warnings(), the
    comparison after a bootloader update) works unchanged. ``modules`` is the numberOfModules byte of
    ISO 14229-1 Annex C.1, or None for the form before BL-22 (bare text, every device flashed until then)."""

    modules: "int | None"

    def __new__(cls, text: str, modules: "int | None" = None):
        self = super().__new__(cls, text)
        self.modules = modules
        return self

    @property
    def annex_c1(self) -> bool:
        return self.modules is not None


# BL-22: the highest numberOfModules byte that is told apart from a text reliably (see parse_identification)
MAX_NUMBER_OF_MODULES = 0x1F


def parse_identification(payload: bytes) -> Identification:
    """Text of an identification identifier, in either form (BL-22 transition).

    Annex C.1 puts numberOfModules in front of the modules; until BL-22 this boot software and this application
    answered with the bare text, and every device in the field still does. The two forms are told apart by the
    first byte: an identification text starts with the first character of its prefix, which is printable
    (>= 0x20) - "nextbsw-bl ...", "S32K14x-BL ...", "nextbsw-app ...". A numberOfModules byte is a small count;
    1 for every neXtBSW identifier, and up to MAX_NUMBER_OF_MODULES it can never be mistaken for a character.
    A server that reported 32 modules or more would be read as the old form - it would also have to send them,
    which no form of this protocol does."""
    data = bytes(payload)
    if data and 1 <= data[0] <= MAX_NUMBER_OF_MODULES:
        return Identification(data[1:].decode("ascii", "replace").rstrip("\0"), data[0])
    return Identification(data.decode("ascii", "replace").rstrip("\0"))


class _IdentificationRest(udsoncan.DidCodec):
    """Reads an identification identifier (DID 0xF180/0xF181) in both forms, see parse_identification()."""

    def decode(self, payload):
        return parse_identification(payload)

    def __len__(self):
        raise udsoncan.DidCodec.ReadAllRemainingData


class _RawRest(udsoncan.DidCodec):
    """Reads the rest of the response unchanged (target compatibility, DID 0xF1A0)."""

    def decode(self, payload):
        return bytes(payload)

    def __len__(self):
        raise udsoncan.DidCodec.ReadAllRemainingData


class _FixedRaw(udsoncan.DidCodec):
    """A fixed number of raw bytes, read and written (fingerprint, DID 0xF184)."""

    def __init__(self, length: int):
        self.length = length

    def encode(self, *did_value):
        data = bytes(did_value[0])
        if len(data) != self.length:
            raise ValueError(f"DID 0x{DID_FINGERPRINT:04X} takes exactly {self.length} bytes, not {len(data)}")
        return data

    def decode(self, payload):
        return bytes(payload)

    def __len__(self):
        return self.length


def did_codecs() -> dict:
    return {DID_BOOT_SOFTWARE_ID: _IdentificationRest(), DID_TARGET_COMPAT: _RawRest(),
            DID_ECU_SW_VERSION: _AsciiRest(), DID_ECU_HW_NUMBER: _RawRest(),
            DID_FINGERPRINT: _FixedRaw(FINGERPRINT_LEN), DID_ECU_SERIAL: _RawRest(), DID_SECURITY_INFO: _RawRest(),
            DID_ACTIVE_SESSION: _FixedRaw(1)}


# ---------------------------------------------------------------- security access (BL-03)


def use_master_key(key: "blkeys.MasterKey | None") -> None:
    """Master key for this process instead of the key store of the workstation (None: key store again)."""
    global MASTER_KEY
    MASTER_KEY = key


def security_method_of(identification: str | None) -> str | None:
    """Method of the security access by the bootloader identification (DID 0xF180): XOR/rotl13 (LEGACY_METHOD) for
    the former prefix BL_VERSION_PREFIX_FORMER (the bootloaders flashed before PLAT-08 and BL-03), None otherwise -
    then DID 0xF1A1 decides (a bootloader that does not know it is older than BL-03 as well)."""
    parts = split_boot_version(identification) if identification else None
    if parts is not None and parts[0] == BL_VERSION_PREFIX_FORMER:
        return blkeys.LEGACY_METHOD
    return None


def read_uid(client: Client) -> bytes | None:
    """DID 0xF18C: chip UID (BL-03), None for a bootloader before BL-03."""
    payload = _read_optional(client, DID_ECU_SERIAL)
    return None if payload is None else bytes(payload)


def read_security_info(client: Client) -> dict | None:
    """DID 0xF1A1 (BL-03): method, key state, seed and key length; None for a bootloader before BL-03."""
    payload = _read_optional(client, DID_SECURITY_INFO)
    if payload is None:
        return None
    if len(payload) < 5:
        raise RuntimeError(f"DID 0x{DID_SECURITY_INFO:04X}: {len(payload)} instead of 5 bytes")
    return {"version": payload[0], "method": payload[1], "state": payload[2], "seed_len": payload[3],
            "key_len": payload[4]}


def security_state_text(info: dict | None, uid: bytes | None = None) -> str:
    if info is None:
        return "security access XOR/rotl13 (bootloader before BL-03)"
    method = blkeys.METHODS.get(info["method"], f"unknown method {info['method']}")
    text = f"security access {method}, {KEY_STATES.get(info['state'], hex(info['state']))}"
    return text + (f", chip UID {uid.hex()}" if uid else "")


def missing_key_name_text() -> str:
    """TOOL-204: which file carries the name of the master key, where it is missing and how to go on without it."""
    layout = SOURCE.get(LAYOUT_FILE_KEY)
    where = (f"{Path(layout).parent / SECURITY_HEADER_NAME} is missing next to {Path(layout).name}" if layout
             else f"the name of the master key is unknown ({SECURITY_HEADER_NAME} of the build or the project)")
    return (f"{where}: the security access of this bootloader needs the name of the master key "
            f"({KEY_NAME_KEY}). {SECURITY_HEADER_NAME} is generated together with bl_memory.h and bl_diag.h "
            "(tools/bswgen/blgen.py, build directory <build>/generated/board/bootloader); copy it to the folder "
            f"of --layout, or pass --project projects/<project>/project.yaml, or name the key with --key-name "
            f"<name> (environment {KEY_NAME_ENV}), e.g. the project's diagnostics.bootloader.security.key")


def master_key() -> "blkeys.MasterKey":
    """The master key of the selected project (bl_security.h BL_SECURITY_KEY_NAME) or the one of use_master_key()."""
    if MASTER_KEY is not None:
        return MASTER_KEY
    name = TEXT.get(KEY_NAME_KEY)
    if name is None:
        raise ValueError(missing_key_name_text())
    return blkeys.load_master_key(name)


@dataclass
class SecurityAccess:
    """How to answer the seed of the connected bootloader: method, and for HMAC-SHA256 the device key."""

    method: str
    device_key: bytes | None = None
    uid: bytes | None = None

    def key(self, seed: bytes) -> bytes:
        if self.method == blkeys.LEGACY_METHOD:
            if len(seed) != 4:
                raise RuntimeError(f"seed of {len(seed)} bytes from a bootloader identified as older than BL-03 "
                                   f"(expected 4): identification and security access do not fit together")
            return struct.pack(">I", blkeys.legacy_key(struct.unpack(">I", seed)[0]))
        if len(seed) != SEED_LEN:
            raise RuntimeError(f"seed of {len(seed)} bytes, the {self.method} security access has {SEED_LEN}")
        return blkeys.seed_key(self.device_key, seed)


def prepare_security(client: Client) -> SecurityAccess:
    """Method and key of the connected bootloader, before its seed is requested (BL-03 transition).

    A bootloader before BL-03 (former identification S32K14x-BL, as on the eval board, or no DID 0xF1A1) keeps its
    XOR/rotl13. A newer one reports method and key state in DID 0xF1A1: without a provisioned device key the security
    access is refused with a message that says so, instead of a bare NRC 0x22."""
    identification = read_boot_identification(client)
    if security_method_of(identification) == blkeys.LEGACY_METHOD:
        return SecurityAccess(blkeys.LEGACY_METHOD)
    info = read_security_info(client)
    if info is None:                                  # 0x31: the bootloader does not know DID 0xF1A1
        return SecurityAccess(blkeys.LEGACY_METHOD)
    method = blkeys.METHODS.get(info["method"])
    if method is None:
        raise RuntimeError(f"the bootloader uses security access method {info['method']}, this canflash knows "
                           f"{', '.join(f'{k} {v}' for k, v in blkeys.METHODS.items())}")
    uid = read_uid(client)
    if info["state"] != 1:
        raise RuntimeError(f"Security access refused by the target: {KEY_STATES.get(info['state'], info['state'])}"
                           + (f", chip UID {uid.hex()}" if uid else "") + ". Provision the device key once over the "
                           "debugger (tools/canflash/provision.py); flashing over CAN works after that.")
    if not uid:
        raise RuntimeError(f"the bootloader does not report its chip UID (DID 0x{DID_ECU_SERIAL:04X})")
    return SecurityAccess(method, blkeys.device_key(master_key().key, uid), uid)


class NxClient(Client):
    """udsoncan client that finds out how to answer the seed right before the security access (BL-03): by the
    identification of the bootloader, and for HMAC-SHA256 with the device key from master key and chip UID."""

    nx_security: SecurityAccess | None = None

    def unlock_security_access(self, level, seed_params=bytes()):
        self.nx_security = prepare_security(self)
        return super().unlock_security_access(level, seed_params)

    def security_key(self, seed: bytes) -> bytes:
        if self.nx_security is None:
            self.nx_security = prepare_security(self)
        return self.nx_security.key(bytes(seed))


# ---------------------------------------------------------------- security access of the application (COM-30)


@dataclass(frozen=True)
class AppLevel:
    """One security level of the application, as the project's diagnostic description (diag.yaml) gives it."""

    name: str
    level: int                  # requestSeed sub-function
    seed_bytes: int
    key_bytes: int
    method: str
    session: int                # session type the tester opens before the seed (first of the level's sessions)


def app_security_levels(project: Path) -> list[AppLevel]:
    """Security levels of the application from the project's diagnostic description, loaded like the build does
    (tools/diaggen). Called before the bus is opened: loading the configuration takes about a second."""
    sys.path.insert(0, str(ROOT / "tools" / "diaggen"))
    import diaggen  # noqa: E402
    try:
        d = diaggen.load_project(Path(project))
    except diaggen.DiagGenError as e:
        raise ValueError(f"{project}: {e}") from None
    sessions = diaggen.sessions_of(d.desc)
    by_name = {s.name: s.id for s in sessions}
    levels = []
    for lv in d.desc.security:
        names = lv.sessions if lv.sessions is not None else [s.name for s in sessions
                                                             if s.id != diaggen.DEFAULT_SESSION]
        session = next((by_name[n] for n in names if n in by_name), 0x03)
        levels.append(AppLevel(lv.name, lv.level, lv.seed_bytes, lv.key_bytes, lv.method, session))
    return levels


def pick_level(levels: list[AppLevel], wanted: str | None) -> AppLevel:
    """The level named by `wanted` (name or requestSeed sub-function, e.g. 0x01), the first one without."""
    if not levels:
        raise ValueError("the application's diagnostic description has no security level (diag.yaml security)")
    if wanted is None:
        return levels[0]
    for lv in levels:
        if lv.name == wanted or (wanted[:1].isdigit() and lv.level == int(wanted, 0)):
            return lv
    raise ValueError(f"no security level {wanted} in the description (known: "
                     f"{', '.join(f'{lv.name} 0x{lv.level:02X}' for lv in levels)})")


def unlock_application(client: Client, level: AppLevel, uid: bytes | None = None, log=print) -> bytes:
    """Unlocks `level` of the running application (27, COM-30) and returns the chip UID it used.

    Session of the level (10), chip UID from the identifier with `chip.uid` (0xF18C) unless `uid` is given, device
    key = HMAC-SHA256(master key, UID) as for the bootloader (BL-03), seed, key = blkeys.app_key(). An all zero
    seed means the level is unlocked already."""
    if level.method != "hmac-sha256":
        raise ValueError(f"security level {level.name} uses {level.method}, this canflash knows hmac-sha256")
    client.change_session(level.session)
    if uid is None:
        payload = _read_optional(client, DID_ECU_SERIAL)
        if not payload:
            raise RuntimeError(f"the application does not report its chip UID (DID 0x{DID_ECU_SERIAL:04X}; its "
                               "diag.yaml needs an identifier with source {bsw: chip.uid}). Pass --uid <hex>: "
                               "'canflash info --bootloader' shows it")
        uid = bytes(payload)
    device = blkeys.device_key(master_key().key, uid)
    seed = bytes(client.request_seed(level.level).service_data.seed)
    if not any(seed):
        log(f"security level {level.name} (0x{level.level:02X}) is unlocked already")
        return uid
    if len(seed) != level.seed_bytes:
        raise RuntimeError(f"seed of {len(seed)} bytes, security level {level.name} of the description has "
                           f"{level.seed_bytes}: target and project do not fit together")
    client.send_key(level.level + 1, blkeys.app_key(device, level.level, seed, level.key_bytes))
    log(f"security level {level.name} (0x{level.level:02X}) unlocked, chip UID {uid.hex()}")
    return uid


def parse_did_write(text: str) -> tuple[int, bytes]:
    """"FD00=01020304" -> (0xFD00, bytes)."""
    did, _, data = text.partition("=")
    try:
        return int(did, 16), bytes.fromhex(data)
    except ValueError:
        raise ValueError(f"--write {text}: expected <DID hex>=<data hex>, e.g. FD00=12345678") from None


def cmd_unlock(args) -> int:
    """Unlocks a security level of the running application, then writes and reads identifiers (COM-30)."""
    project = args.project or os.environ.get("NX_PROJECT") or getattr(args, "timing_project", None)
    if project is None:
        raise ValueError("unlock needs the project's diagnostic description: pass --project "
                         "projects/<project>/project.yaml")
    level = pick_level(app_security_levels(Path(project)), args.level)
    uid = bytes.fromhex(args.uid) if args.uid else None
    writes = [parse_did_write(w) for w in args.write]
    reads = [int(r, 16) for r in args.read]
    with open_bus(args) as bus, make_client(bus) as client:
        unlock_application(client, level, uid)
        for did, data in writes:
            request = bytes([0x2E]) + did.to_bytes(2, "big") + data
            response = client.send_request(udsoncan.Request.from_payload(request))
            print(f"2E {did:04X} {data.hex()} -> {bytes(response.original_payload).hex(' ')}")
        for did in reads:
            request = bytes([0x22]) + did.to_bytes(2, "big")
            response = client.send_request(udsoncan.Request.from_payload(request))
            print(f"22 {did:04X} -> {bytes(response.original_payload).hex(' ')}")
    return 0


def make_client(bus: can.BusABC) -> Client:
    address = isotp.Address(
        isotp.AddressingMode.Normal_11bits,
        txid=CFG["BL_CAN_ID_REQUEST_PHYS"],
        rxid=CFG["BL_CAN_ID_RESPONSE"],
    )
    stack = isotp.CanStack(bus, address=address, params=isotp_params())
    config = dict(udsoncan.configs.default_client_config)
    config.update(
        {
            "use_server_timing": False,  # server P2 of 50 ms is too tight for a PC + USB adapter
            "p2_timeout": 1.0,
            "p2_star_timeout": CFG["BL_P2STAR_SERVER_MS"] / 1000 + 1.0,
            "request_timeout": 30.0,
            "data_identifiers": did_codecs(),
            "exception_on_unexpected_response": True,
        }
    )
    client = NxClient(NxIsoTpConnection(stack), config=config)
    # BL-03: method and key of the connected bootloader, found out by unlock_security_access()
    client.set_config("security_algo", lambda level, seed, params: client.security_key(seed))
    return client


def send_frame(bus: can.BusABC, msg: can.Message) -> bool:
    """Sends one frame; False instead of an exception when the adapter refuses it (TOOL-51).

    With nobody on the bus who acknowledges (board without power, ECU holding a safe state with its CAN off) the
    controller repeats the first frame forever and the adapter's transmit queue fills up; the Vector driver then
    refuses every further frame with XL_ERR_QUEUE_IS_FULL (board 2026-09-22, --wait ended with a traceback). The
    queue is emptied, so the next request goes out again - the frames in it are outdated anyway. An adapter
    without flush_tx_buffer() just keeps its queue.

    Only after a refusal, never ahead: python-can's Vector backend empties the queue by sending a high-priority
    frame (ID 0, no data) - harmless on a bus where nobody listens, but not something to put on a live bus."""
    try:
        bus.send(msg)
        return True
    except can.CanError:
        with contextlib.suppress(Exception):   # not every interface implements it (BusABC raises)
            bus.flush_tx_buffer()
        return False


def _session_request(bus: can.BusABC, interval: float) -> bool:
    """Sends 10 02 as a single frame and returns whether 50 02 arrived within `interval` seconds.

    A refused send (send_frame()) still waits `interval`: an earlier request may be answered meanwhile, and the
    request rate stays the same while nobody acknowledges."""
    request = bytes([0x02, 0x10, 0x02]) + bytes([CFG["BL_ISOTP_PADDING"]] * 5)
    if not send_frame(bus, can.Message(arbitration_id=CFG["BL_CAN_ID_REQUEST_PHYS"], data=request,
                                       is_extended_id=False)):
        REFUSED_SENDS[0] += 1
    deadline = time.monotonic() + interval
    while time.monotonic() < deadline:
        msg = bus.recv(0.005)
        if msg and msg.arbitration_id == CFG["BL_CAN_ID_RESPONSE"] and msg.data[:3] == bytes([0x06, 0x50, 0x02]):
            return True
    return False


REFUSED_SENDS = [0]   # sends refused by the adapter since the start of the process (log of the waiting, tests)


def drain(bus: can.BusABC, quiet: float = 0.0, limit: float = 0.3) -> int:
    """Throws away frames that are still on the bus and returns their number.

    `quiet` 0 takes only what is already buffered (non blocking), a larger value also frames that arrive
    within that time after the last one. `limit` bounds the whole call, so the cyclic messages of a running
    application cannot hold it.

    Needed around the repeated 10 02 of enter_bootloader(): every one of those requests may be answered, and
    an answer that arrives later than `interval` stays in the receive queue. The ISO-TP stack of the client
    that follows would take such a leftover 50 02 as the response to its **next** request; with
    exception_on_unexpected_response that ends the flash run. The board finding "27 01 without answer after
    the application's 50 02" has exactly this shape."""
    end = time.monotonic() + limit
    count = 0
    while (time.monotonic() < end) and (bus.recv(quiet) is not None):
        count += 1
    return count


def enter_bootloader(bus: can.BusABC, seconds: float = 3.0, interval: float = 0.025, confirmations: int = 5) -> bool:
    """Brings a running application or the bootloader into the bootloader's programming session.

    The application answers 10 02 like the bootloader and then resets into it; the bootloader needs about
    80 ms before it listens, and requests sent meanwhile are lost (board finding: 27 01 right after the
    application's 50 02). So 10 02 is repeated until an answer follows an unanswered request (bootloader
    after the reset), or `confirmations` requests in a row were answered (the bootloader was already
    active: the application resets after its first answer)."""
    drain(bus)  # answers of an earlier run must not count as answers of this one
    end = time.monotonic() + seconds
    seen_answer = silent_after_answer = False
    answered_in_row = 0
    while time.monotonic() < end:
        if _session_request(bus, interval):
            if silent_after_answer:
                return True
            seen_answer = True
            answered_in_row += 1
            if answered_in_row >= confirmations:
                return True
        else:
            silent_after_answer = seen_answer
            answered_in_row = 0
    return False


WAIT_INTERVAL_S = 0.01   # 10 02 every 10 ms: the bootloader's startup window gets several requests (TOOL-51)


def wait_for_bootloader(bus: can.BusABC, seconds: float, power: "powercycle.PowerSwitch | None" = None,
                        off_s: float = powercycle.DEFAULT_OFF_S, log=None, interval: float = WAIT_INTERVAL_S) -> bool:
    """Sends DiagnosticSessionControl(programming) repeatedly until the bootloader answers, at most `seconds`.

    TOOL-51: nobody acknowledging (board off, ECU in a held safe state) is the normal case while waiting for a
    power cycle; refused sends do not end the waiting (send_frame()). With `power` the supply is switched off and
    on in a thread of its own while the requests go on, so the bootloader is caught in its startup window.
    Requests still queued when the ECU starts are delivered then; their extra answers are drained by
    reach_bootloader()."""
    refused_before = REFUSED_SENDS[0]
    cycle = None
    if power is not None:
        cycle = powercycle.PowerCycle(power, off_s, log=log or (lambda *a, **k: None))
        cycle.start()
    end = time.monotonic() + seconds
    reached = False
    try:
        while time.monotonic() < end:
            if _session_request(bus, interval):
                reached = True
                break
    finally:
        if cycle is not None:
            cycle.join()   # the supply is on again before anything else happens (also after Ctrl-C)
    refused = REFUSED_SENDS[0] - refused_before
    if refused and log is not None:
        log(f"  {refused} request(s) refused by the adapter while waiting - nobody acknowledged, "
            "transmit queue emptied")
    return reached


@contextlib.contextmanager
def quiet(client: Client):
    """Silences the client's own log while a negative response is an expected answer.

    udsoncan logs every negative response as a warning before it raises (standard_error_management), and without
    a logging configuration Python prints that to stderr - twice for read_data_by_identifier_first, because the
    call is wrapped twice. For a question like "does this bootloader know DID 0xF1A0" that is noise (TOOL-05)."""
    logger = client.logger
    before = logger.disabled
    logger.disabled = True
    try:
        yield client
    finally:
        logger.disabled = before


def read_target_compat(client: Client) -> dict[str, int] | None:
    """Target values of DID 0xF1A0 (BL-02) or None for a bootloader that does not know the DID."""
    try:
        with quiet(client):
            payload = client.read_data_by_identifier_first(DID_TARGET_COMPAT)
    except NegativeResponseException as e:
        if e.response.code == 0x31:  # requestOutOfRange: bootloader from before BL-02
            return None
        raise
    if len(payload) < 22:
        raise RuntimeError(f"DID 0x{DID_TARGET_COMPAT:04X}: {len(payload)} instead of 22 bytes")
    version = payload[0]
    target, layout, app_start, app_end, record_addr = struct.unpack(">IIIII", payload[1:21])
    return {"version": version, "target": target, "layout": layout, "app_start": app_start, "app_end": app_end,
            "record_addr": record_addr, "record": payload[21]}


def _read_optional(client: Client, did: int):
    """Payload of `did`, or None if the target does not know it (0x31) or refuses it in this session (0x7F).

    Everything a bootloader before BL-04 does not have is read this way, so an older target stays usable."""
    client.set_config("data_identifiers", did_codecs())
    try:
        with quiet(client):
            return client.read_data_by_identifier_first(did)
    except NegativeResponseException as e:
        if e.response.code in (0x31, 0x11, 0x7F, 0x22):
            return None
        raise


def read_software_version(client: Client) -> str | None:
    """DID 0xF189: version number of the bootloader software without the project's prefix (BL-04)."""
    return _read_optional(client, DID_ECU_SW_VERSION)


def read_hardware_number(client: Client) -> str | None:
    """DID 0xF191: board and derivative of the target as "<board>/<derivative>" (BL_HARDWARE_ID, BL-10).

    A build between BL-04 and BL-10 answered the four bytes of BL_TARGET_ID instead (never flashed to a board, but
    possible in a build directory); that is shown as "0x<identity>"."""
    payload = _read_optional(client, DID_ECU_HW_NUMBER)
    if payload is None:
        return None
    if payload and all(0x20 <= b < 0x7F for b in payload):
        return payload.decode("ascii")
    if len(payload) == 4:
        return f"0x{struct.unpack('>I', payload)[0]:08X}"
    raise RuntimeError(f"DID 0x{DID_ECU_HW_NUMBER:04X}: neither text nor a 4-byte identity ({payload.hex()})")


def hardware_identity(hardware: str) -> int:
    """BL_TARGET_ID that belongs to a hardware number of DID 0xF191 (the CRC-32 of the text, see target_id())."""
    if re.fullmatch(r"0x[0-9A-F]{8}", hardware):
        return int(hardware, 16)
    return zlib.crc32(hardware.encode("ascii"))


def read_fingerprint(client: Client) -> dict | None:
    """DID 0xF184 of the running programming session, or None if none was written (or the DID is unknown)."""
    payload = _read_optional(client, DID_FINGERPRINT)
    return None if payload is None else parse_fingerprint(payload)


def write_fingerprint(client: Client, tester: str | bytes | None = None, date: datetime.date | None = None,
                      log=print) -> bytes | None:
    """Writes DID 0xF184 (tester identification and date) before erasing and downloading (BL-04).

    The bootloader refuses every erase and every download without it. A bootloader from before BL-04 does not
    know the DID; then only a note is logged, exactly like the transition of BL-02, so a board with the older
    bootloader stays flashable. Needs the programming session and security access."""
    data = fingerprint_bytes(tester, date)
    client.set_config("data_identifiers", did_codecs())
    try:
        with quiet(client):
            client.write_data_by_identifier(DID_FINGERPRINT, data)
    except NegativeResponseException as e:
        if e.response.code in (0x31, 0x11):
            log("Note: this bootloader is older than BL-04 (no fingerprint, DID 0xF184). The download is not "
                "documented on the target.")
            return None
        raise
    log(f"Fingerprint: {format_fingerprint(parse_fingerprint(data))}")
    return data


def compat_differences(image: dict[str, int], target: dict[str, int]) -> list[str]:
    """Readable differences between the image's build values and the target's (empty = they match)."""
    diff = []
    if image["version"] != target["version"]:
        diff.append(f"record format version {image['version']} against {target['version']} on the target")
    if image["target"] and target["target"] and image["target"] != target["target"]:
        diff.append(f"board/derivative 0x{image['target']:08X} against 0x{target['target']:08X} on the target")
    for key, name in (("app_start", "application start"), ("app_end", "application end"),
                      ("record_addr", "validity record")):
        if image[key] != target[key]:
            diff.append(f"{name} 0x{image[key]:08X} against 0x{target[key]:08X} on the target")
    if not diff and image["layout"] != target["layout"]:
        diff.append(f"memory layout 0x{image['layout']:08X} against 0x{target['layout']:08X} on the target "
                    "(flash size, sector or phrase size, RAM range, boot request address)")
    return diff


def split_boot_version(identification: str) -> tuple[str, str] | None:
    """(prefix, version number) of a BL_VERSION_STRING like "nextbsw-bl 0.1.0", None if it has not that form."""
    match = re.fullmatch(r"(\S.*?) (\d+)\.(\d+)\.(\d+)", identification.strip())
    if match is None:
        return None
    return match.group(1), ".".join(match.group(2, 3, 4))


def boot_version_warnings(identification: str, prefix: str | None = None, number: str = BL_VERSION_NUMBER) -> list[str]:
    """Differences between the bootloader on the target (DID 0xF180) and what the tester expects (TOOL-05).

    Warnings, not errors: the prefix belongs to the project, and a newer minor version is compatible by
    definition. The former name of the software (BL_VERSION_PREFIX_FORMER, S32K14x-BL before PLAT-08) passes
    without a warning, so boards with a bootloader flashed before the rename keep working. Board, derivative and memory layout - the facts that decide whether an image may run - are
    checked by DID 0xF1A0 and routine 0xF1A0 (0xFF02 before BL-24) and stay errors."""
    expected_prefix = TEXT.get(PREFIX_KEY) if prefix is None else prefix
    parts = split_boot_version(identification)
    if parts is None:
        return [f"Warning: the bootloader identification {identification!r} is not '<prefix> <major>.<minor>.<patch>'"
                " - the target may not be a bootloader of this project."]
    warnings = []
    if expected_prefix is not None and parts[0] not in accepted_prefixes(expected_prefix):
        warnings.append(f"Warning: the bootloader identifies itself as '{parts[0]}', the project expects "
                        f"'{expected_prefix}' (diagnostics.bootloader.version_prefix) - another product on these "
                        "diagnostic addresses, or a bootloader from before a rename of the prefix.")
    if parts[1].split(".")[0] != number.split(".")[0]:
        warnings.append(f"Warning: bootloader version {parts[1]} has another major version than this tool "
                        f"({number}); services and records may differ.")
    return warnings


def identification_note(identification: str | None) -> str:
    """What to add behind an identification that came in the form before BL-22, empty for the Annex C.1 form.

    Not a warning: a device in the field answers like this and is flashed like any other. It tells the user
    which of the two forms a target speaks, which is the whole point of the transition."""
    if isinstance(identification, Identification) and not identification.annex_c1:
        return "  (without numberOfModules: identification of a build before BL-22)"
    return ""


def read_boot_identification(client: Client) -> str | None:
    """DID 0xF180 of the bootloader (or of the application, which reports the bootloader found in its flash)."""
    client.set_config("data_identifiers", did_codecs())
    try:
        with quiet(client):
            return client.read_data_by_identifier_first(DID_BOOT_SOFTWARE_ID)
    except NegativeResponseException:
        return None


def check_boot_software(client: Client, log=print) -> str | None:
    """Logs the bootloader identification and the warnings of boot_version_warnings(); returns the identification."""
    identification = read_boot_identification(client)
    if identification is None:
        log("Warning: the target does not report a bootloader identification (DID 0xF180).")
        return None
    log(f"Bootloader: {identification}{identification_note(identification)}")
    for warning in boot_version_warnings(identification):
        log(warning)
    return identification


def compat_routine_of(identification: str | None) -> int | None:
    """Routine identifier of the compatibility check for the bootloader that reported `identification` (BL-24).

    Decided by the version number alone, like the method of the security access: the prefix belongs to the
    project, the number to the bootloader software, so "S32K14x-BL 0.1.0", "NX148-BL 0.3.3" and "nextbsw-bl 0.3.1"
    all get RID_CHECK_COMPAT_FORMER and every bootloader since COMPAT_ROUTINE_SINCE the new one. None if the
    identification does not name a version (missing DID 0xF180, another product): then the tester tries the new
    routine first and falls back on the former one (send_compat_routine)."""
    parts = split_boot_version(identification) if identification is not None else None
    if parts is None:
        return None
    version = tuple(int(part) for part in parts[1].split("."))
    return RID_CHECK_COMPAT if version >= COMPAT_ROUTINE_SINCE else RID_CHECK_COMPAT_FORMER


def send_compat_routine(client: Client, record: bytes, identification: str | None = None):
    """Starts the compatibility check with the routine identifier the bootloader knows; returns (rid, response).

    Without a usable identification the new identifier goes first; a bootloader from before BL-24 answers it with
    requestOutOfRange (0x31) before looking at anything else, and the former one is sent instead."""
    rid = compat_routine_of(identification)
    if rid is not None:
        return rid, client.routine_control(rid, 1, record)
    try:
        with quiet(client):
            return RID_CHECK_COMPAT, client.routine_control(RID_CHECK_COMPAT, 1, record)
    except NegativeResponseException as e:
        if e.response.code != 0x31:
            raise
    return RID_CHECK_COMPAT_FORMER, client.routine_control(RID_CHECK_COMPAT_FORMER, 1, record)


class ImageRejected(RuntimeError):
    """The image does not belong to the target; raised by check_compatibility() before anything is erased."""


# TOOL-53 (3): loud on purpose - the option is a test aid for the bootloader's own check, never for flashing
SKIP_LOCAL_CHECK_WARNING = (
    "WARNING: --skip-local-compat-check is a TEST AID. canflash does not refuse an image that does not match the "
    "target itself but hands it to the bootloader's compatibility check (routine 0xF1A0), so that the bootloader's "
    "refusal can be shown on the target. An image the local check found different is never flashed.")


def check_compatibility(client: Client, log=print, identification: str | None = None,
                        local: bool = True) -> dict[str, int] | None:
    """Compares the image's build with the target and lets the bootloader confirm it, before anything is erased.

    Raises ImageRejected with the differences named. A bootloader from before BL-02 knows neither the DID nor the
    routine; then only a note is logged (its own range checks still apply). `identification` (DID 0xF180) selects
    the routine identifier (BL-24, compat_routine_of).

    `local=False` (--skip-local-compat-check, TOOL-53): differences found here are logged as a warning and the
    image is declared to the bootloader anyway, so that its refusal can be verified on the target (BL-24 c). If
    the bootloader accepts such an image, the run still stops before the erase."""
    image = image_compat()
    target = read_target_compat(client)
    if target is None:
        # Known gap of the transition: such a bootloader accepts any image that fits into its application area.
        log("Note: this bootloader is older than BL-02 (no DID 0xF1A0). Board, derivative and memory layout of the "
            "image cannot be checked on the target - make sure the hex belongs to this board:\n"
            f"  image: board/derivative 0x{image['target']:08X}, memory layout 0x{image['layout']:08X}, "
            f"application 0x{image['app_start']:08X}..0x{image['app_end'] - 1:08X}")
        return None
    diff = compat_differences(image, target)
    if diff and local:
        raise ImageRejected("Image and target do not match, nothing was erased or written:\n  - "
                            + "\n  - ".join(diff)
                            + "\nUse the hex file and the memory layout of the right project (--project/--layout).")
    if diff:
        log("WARNING (--skip-local-compat-check): image and target do not match, declared to the bootloader "
            "anyway:\n  - " + "\n  - ".join(diff))
    # the bootloader has the last word and blocks erasing and downloading after a mismatch it found itself
    rid, status = send_compat_routine(client, bytes([image["version"]])
                                      + struct.pack(">IIIII", image["target"], image["layout"], image["app_start"],
                                                    image["app_end"], image["record_addr"]), identification)
    reason = status.service_data.routine_status_record[0]
    if reason != 0x00:
        raise ImageRejected(f"Bootloader rejects the image: {COMPAT_REASONS.get(reason, hex(reason))} "
                            f"(routine 0x{rid:04X} status 0x{reason:02X}); nothing was erased.")
    if diff:
        raise ImageRejected(f"Bootloader ACCEPTED an image that does not match the target (routine 0x{rid:04X} "
                            "status 0x00) - a finding against the bootloader's check. Nothing was erased: "
                            "--skip-local-compat-check never flashes such an image.")
    if target["target"] == 0 or image["target"] == 0:
        log("Note: no board/derivative identity (BL_TARGET_ID 0 on the target or in the build); only the memory "
            "layout was compared.")
    return target


PROGRESS_MODES = ("auto", "line", "steps", "off")


def progress_mode(mode: str = "auto", stream=None) -> str:
    """"auto": a running line on a terminal, whole lines every 10 % otherwise (pipe, CMake log, Studio)."""
    if mode not in PROGRESS_MODES:
        raise ValueError(f"progress mode {mode!r} unknown ({', '.join(PROGRESS_MODES)})")
    if mode != "auto":
        return mode
    stream = sys.stdout if stream is None else stream
    try:
        return "line" if stream.isatty() else "steps"
    except (AttributeError, ValueError):     # replaced or closed stream
        return "steps"


class Progress:
    """Download progress (TOOL-05).

    "line" rewrites one line with a carriage return - only a terminal shows that as a running counter; a CMake
    log or a pipe keeps every update, which made flash-can print hundreds of "1024/143452 bytes" into one line.
    "steps" therefore writes a whole line each time the next `step` percent is reached (at most 100/step + 1
    lines), "off" nothing. The transfer rate follows in every mode except "off"."""

    def __init__(self, total: int, log=print, mode: str = "steps", step: int = 10):
        if mode not in PROGRESS_MODES or mode == "auto":
            raise ValueError(f"progress mode {mode!r}: resolve 'auto' with progress_mode() first")
        self.total, self.log, self.mode, self.step = total, log, mode, step
        self.next_percent = step
        self.t0 = time.monotonic()
        self.lines = 0

    def update(self, done: int) -> None:
        percent = 100 * done // self.total if self.total else 100
        text = f"  {done:7d}/{self.total} bytes ({percent:3d} %)"
        if self.mode == "line":
            self.log("\r" + text, end="", flush=True)
            self.lines = 1
        elif self.mode == "steps" and percent >= self.next_percent:
            self.log(text)
            self.lines += 1
            self.next_percent = (percent // self.step + 1) * self.step

    def finish(self) -> None:
        if self.mode == "off":
            return
        if self.mode == "line":
            self.log("")
        elapsed = max(time.monotonic() - self.t0, 1e-3)
        self.log(f"  {self.total / elapsed / 1024:.1f} KB/s")


SESSION_NAMES = {0x01: "default", 0x02: "programming", 0x03: "extended"}


def download_lost(client: Client, block: int, pause: float) -> str:
    """What a TransferData with NRC 0x24 (requestSequenceError) means: the bootloader has no download open any
    more (TOOL-48, board 2026-09-21: aborted at 10 % with STmin 1 ms). The active session and the time since the
    previous block tell the causes apart: back in the default session = S3 expired or the ECU restarted; still in
    the programming session = the download was closed by the bootloader itself."""
    try:
        raw = _read_optional(client, DID_ACTIVE_SESSION)
    except (NegativeResponseException, udsoncan.exceptions.TimeoutException):
        raw = None
    session = int.from_bytes(raw, "big") if raw else None
    s3 = CFG.get("BL_S3_SERVER_MS")
    text = (f"TransferData block {block}: the bootloader has no download open any more (NRC 0x24), "
            f"{pause:.2f} s after the previous block's answer")
    if session is None:
        text += "; active session unknown (DID 0xF186 not answered)"
    else:
        text += f"; active session now 0x{session:02X} ({SESSION_NAMES.get(session, 'other')})"
    if session == 0x01:
        cause = f"S3 of {s3 / 1000:g} s expired" if s3 and pause * 1000 > s3 else "S3 expired or the ECU restarted"
        text += f" - it left the programming session ({cause})"
    return text + ". Flash again."


def leave_bootloader(client: Client, reset: bool = True) -> str:
    """After a rejected image (TOOL-53): hardReset, so that the ECU does not stay in the bootloader; says what happened.

    Board finding 2026-09-22: canflash refused an image of another board before the erase, but the ECU stayed in
    the bootloader (F181 answered 0x31) until 11 01 came. Nothing was erased, so the reset starts the installed
    application again - the bootloader itself decides whether it is valid."""
    if not reset:
        return ("The ECU stays in the bootloader (--stay-in-bootloader); reset it (11 01 or power cycle) to start "
                "the installed application again.")
    try:
        with quiet(client):
            client.ecu_reset(udsoncan.services.ECUReset.ResetType.hardReset)
    except UDS_ERRORS as e:
        return (f"The ECU did not accept the reset (11 01: {describe_error(e)}); it stays in the bootloader until "
                "it is reset or power cycled.")
    return ("ECU reset (11 01): the bootloader starts the installed application again if it is valid, "
            "otherwise it stays in the bootloader.")


def flash_application(client: Client, data: bytes, log=print, check: bool = True, progress: str = "auto",
                      tester: str | bytes | None = None, local_check: bool = True,
                      reset_on_reject: bool = True) -> None:
    start = CFG["BL_APP_START"]
    size = len(data)
    location = udsoncan.MemoryLocation(start, size, address_format=32, memorysize_format=32)

    client.change_session(udsoncan.services.DiagnosticSessionControl.Session.programmingSession)
    client.unlock_security_access(1)
    if check:
        identification = check_boot_software(client, log)
        try:
            check_compatibility(client, log, identification, local=local_check)
        except ImageRejected as e:
            raise ImageRejected(f"{e}\n{leave_bootloader(client, reset_on_reject)}") from None
    # BL-04: who programs and when, before anything is erased - the bootloader insists on it
    write_fingerprint(client, tester, log=log)

    log(f"Erasing 0x{start:08X} .. 0x{start + size:08X}")
    client.routine_control(RID_ERASE_MEMORY, 1, bytes([0x44]) + struct.pack(">II", start, size))

    max_len = client.request_download(location).service_data.max_length
    chunk = max_len - 2
    chunk -= chunk % CFG["BL_PHRASE_SIZE"]
    # a running line only where print() reaches a terminal; a caller's own log function gets whole lines
    shown = Progress(size, log, progress_mode(progress) if log is print else
                     ("steps" if progress in ("auto", "line") else progress))
    answered = time.monotonic()
    for index, offset in enumerate(range(0, size, chunk)):
        try:
            client.transfer_data((index + 1) & 0xFF, data[offset : offset + chunk])
        except NegativeResponseException as e:
            if e.response.code != 0x24:
                raise
            raise RuntimeError(download_lost(client, index + 1, time.monotonic() - answered)) from e
        answered = time.monotonic()
        shown.update(min(offset + chunk, size))
    shown.finish()
    client.request_transfer_exit()

    response = client.routine_control(RID_CHECK_MEMORY, 1, struct.pack(">III", start, size, zlib.crc32(data)))
    status = response.service_data.routine_status_record
    if status != b"\x00":
        raise RuntimeError(f"CRC check failed on target (status {status.hex()})")
    log(f"Check OK (CRC-32 0x{zlib.crc32(data):08X})")


@dataclass(frozen=True)
class BusTiming:
    """Bit timing the tester uses: bitrate and sample point of the project's CAN bus, quanta of the adapter."""

    bitrate: int
    sample_point_percent: float | None = None      # None: driver default
    sjw: int | None = None
    quanta: int = 16

    def segments(self) -> dict[str, int]:
        """Vector XL segments for the sample point: 1 + tseg1 + tseg2 = quanta (empty without a sample point)."""
        if self.sample_point_percent is None:
            return {}
        tseg2 = round(self.quanta * (100.0 - self.sample_point_percent) / 100.0)
        if not 1 <= tseg2 <= self.quanta - 2:
            raise ValueError(f"sample point {self.sample_point_percent:g} % cannot be set with {self.quanta} time "
                             "quanta (--quanta)")
        sjw = tseg2 if self.sjw is None else min(self.sjw, tseg2)
        return {"sjw_abr": sjw, "tseg1_abr": self.quanta - 1 - tseg2, "tseg2_abr": tseg2}


def timing_from_project(project: Path, bus: str | None = None) -> BusTiming:
    """Bitrate, sample point and SJW of a CAN bus of the project (project can.<bus>); without `bus` the
    bootloader's bus (bootloader.can). tools/boardtest uses the same for the application's bus.

    Only the project level is read and the entry checked with the model's CanUse (defaults included): a full
    configuration check costs seconds before every flash-can, and the bus entry needs nothing of board or
    derivative."""
    import yaml

    sys.path.insert(0, str(ROOT / "tools"))
    from nxconfig.model import CanUse

    try:
        values = yaml.safe_load(Path(project).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        raise ValueError(f"{project}: {e}") from None
    what = f"bus {bus}" if bus else "the bootloader bus (bootloader.can)"
    bus = bus or (values.get("bootloader") or {}).get("can")
    entry = (values.get("can") or {}).get(bus) if bus else None
    if entry is None:
        raise ValueError(f"{project}: no bit timing for {what} (can.<bus>)")
    try:
        use = CanUse.model_validate(entry)
    except Exception as e:  # pydantic.ValidationError, reported like the other project errors
        raise ValueError(f"{project}: can.{bus}: {e}") from None
    return BusTiming(use.bitrate, float(use.sample_point_percent), use.sjw_tq)


def project_of_build(layout: Path) -> Path | None:
    """project.yaml of the build a generated bl_memory.h belongs to (NX_PROJECT in the CMake cache), or None."""
    root = Path(layout).resolve().parents[len(LAYOUT_IN_BUILD.parts) - 1]
    cache = root / "CMakeCache.txt"
    if not cache.is_file():
        return None
    text = cache.read_text(errors="replace")
    project = re.search(r"^NX_PROJECT:\w+=(.+)$", text, re.MULTILINE)
    source = re.search(r"^CMAKE_HOME_DIRECTORY:\w+=(.+)$", text, re.MULTILINE)
    if project is None:
        return None
    path = Path(project.group(1).strip())
    if not path.is_absolute():
        path = (Path(source.group(1).strip()) if source else ROOT) / path
    if path.is_dir():
        path = path / "project.yaml"
    return path if path.is_file() else None


def resolve_timing(args) -> BusTiming:
    """Command line first, then the project (--project, --ecu, NX_PROJECT or the CMake cache of the hex file's
    build), otherwise the bitrate alone with the driver's default segments."""
    project = getattr(args, "timing_project", None)
    base = None
    if project is not None:
        try:
            base = timing_from_project(Path(project))
        except ValueError as e:
            print(f"canflash: {e}; driver default bit timing", file=sys.stderr)
    bitrate = args.bitrate or (base.bitrate if base else 500000)
    sample_point = args.sample_point if args.sample_point is not None else (
        base.sample_point_percent if base else None)
    sjw = args.sjw if args.sjw is not None else (base.sjw if base else None)
    return BusTiming(bitrate, sample_point, sjw, args.quanta)


def open_bus(args) -> can.BusABC:
    timing = resolve_timing(args)
    return bus_for(args.interface, args.channel, timing, args.app_name)


def bus_for(interface: str, channel, timing: BusTiming, app_name: str | None = None) -> can.BusABC:
    """python-can bus with the project's bit timing; segments only for adapters that take them (vector)."""
    if interface == "vector":
        # same bitrate and sample point as the ECU's bus (project can.<bus>), quanta of the adapter
        return can.Bus(interface="vector", channel=channel, bitrate=timing.bitrate, app_name=app_name,
                       **timing.segments())
    return can.Bus(interface=interface, channel=channel, bitrate=timing.bitrate)


def reach_bootloader(bus: can.BusABC, wait: float, log=print, power: "powercycle.PowerSwitch | None" = None,
                     off_s: float = powercycle.DEFAULT_OFF_S) -> bool:
    """--wait: the tester resets the board; otherwise 10 02 switches a running application (or the bootloader).

    Without --wait this is what the CMake target flash-can uses (COM-09): nobody has to type "lc bootloader"
    on the console first. `power` (--power-cycle, TOOL-51) switches the supply off and on while waiting."""
    if wait > 0:
        if power is None:
            log(f"Waiting up to {wait:.0f} s for the bootloader (reset or power-cycle the board) ...")
        else:
            log(f"Waiting up to {wait:.0f} s for the bootloader, power cycle over {power.name} ...")
        reached = wait_for_bootloader(bus, wait, power, off_s, log)
    else:
        reached = enter_bootloader(bus)
    if reached:
        # Answers of the repeated requests, including one that arrived after its interval had passed: they
        # must not reach the ISO-TP client as the response to its next request (see drain()).
        left = drain(bus, quiet=0.03)
        if left != 0:
            log(f"Bootloader reached; {left} leftover frame(s) discarded before the first request.")
    return reached


def pre_programming_step(bus: can.BusABC, log=print) -> list[str]:
    """Preprogramming step of programming phase #1 (ISO 14229-1 16.3.1.1), steps (2), (5) and (7).

    Before anything is flashed, the client puts every server into a non-defaultSession, stops the DTC
    status bit updating and silences the non-diagnostic communication, so the bus is quiet during the
    download and no server records faults about the ones that went away. The vehicle manufacturer chooses
    the parameters of step (7); this project disables reception and transmission of the normal (that is,
    application) communication messages of the node the request reaches.

    Addressed physically, because canflash flashes one ECU at a time and wants to see the answers. The
    standard also allows the functional form for these steps.

    Best effort on purpose: a bootloader or application from before BL-21 answers serviceNotSupported, and
    that must not stop a flash run - it only means the network was not quiesced. The refusals are returned
    so a caller (and the host test) can tell "step done" from "step refused"."""
    refused: list[str] = []
    drain(bus)
    with make_client(bus) as client:
        steps = (
            ("10 03 extendedDiagnosticSession",
             lambda: client.change_session(
                 udsoncan.services.DiagnosticSessionControl.Session.extendedDiagnosticSession)),
            ("85 02 ControlDTCSetting(off)",
             lambda: client.control_dtc_setting(udsoncan.services.ControlDTCSetting.SettingType.off)),
            ("28 03 CommunicationControl(disableRxAndTx, normal)",
             lambda: client.communication_control(
                 udsoncan.services.CommunicationControl.ControlType.disableRxAndTx,
                 udsoncan.CommunicationType(subnet=0, normal_msg=True))),
        )
        for name, call in steps:
            try:
                with quiet(client):
                    call()
            except (NegativeResponseException, udsoncan.exceptions.TimeoutException) as e:
                refused.append(name)
                log(f"  preprogramming step: {name} not accepted ({type(e).__name__}), continuing")
    if not refused:
        log("Network prepared for programming (10 03, 85 02, 28 03).")
    return refused


def post_programming_step(client: Client) -> None:
    """Post-programming step of programming phase #1 (ISO 14229-1 16.3.1.3).

    A hardReset ends the programming session, starts the freshly downloaded application and, with it,
    returns the server to the defaultSession - which re-enables the communication and the DTC status
    updates that the preprogramming step switched off."""
    client.ecu_reset(udsoncan.services.ECUReset.ResetType.hardReset)


def flash_over_bus(bus: can.BusABC, data: bytes, wait: float = 0.0, log=print, progress: str = "auto",
                   tester: str | bytes | None = None, prepare: bool = True,
                   power: "powercycle.PowerSwitch | None" = None, off_s: float = powercycle.DEFAULT_OFF_S,
                   local_check: bool = True, reset_on_reject: bool = True) -> None:
    # ISO 14229-1 16.3.1.1: quiesce the network while the application still runs, before 10 02 hands the
    # ECU over to the bootloader
    if prepare:
        pre_programming_step(bus, log)
    if not reach_bootloader(bus, wait, log, power, off_s):
        raise RuntimeError("No answer from the bootloader.")
    with make_client(bus) as client:
        flash_application(client, data, log, progress=progress, tester=tester, local_check=local_check,
                          reset_on_reject=reset_on_reject)
        post_programming_step(client)


def cmd_flash(args) -> int:
    data = load_app_image(Path(args.hexfile))
    local_check = not getattr(args, "skip_local_compat_check", False)
    if not local_check:
        print(SKIP_LOCAL_CHECK_WARNING, file=sys.stderr)
    with open_bus(args) as bus:
        try:
            flash_over_bus(bus, data, args.wait, progress=args.progress,
                           tester=getattr(args, "tester", None),
                           prepare=not getattr(args, "no_prepare", False), **power_kwargs(args),
                           local_check=local_check,
                           reset_on_reject=not getattr(args, "stay_in_bootloader", False))
        except RuntimeError as e:
            print(e, file=sys.stderr)
            return 1
    print("Done, ECU reset.")
    return 0


def power_kwargs(args) -> dict:
    """power/off_s for reach_bootloader() from the command line (prepare() sets args.power, TOOL-51)."""
    return {"power": getattr(args, "power", None),
            "off_s": getattr(args, "power_off_time", powercycle.DEFAULT_OFF_S)}


def cmd_scan(args) -> int:
    """Which ECUs of the projects answer on the bus, and what runs on them (bootloader or application)."""
    ecus = ecus_from_projects(args.projects or [ROOT / "projects"], log=print)
    if not ecus:
        print("No project with diagnostic addresses.", file=sys.stderr)
        return 1
    groups = address_groups(ecus)
    print("Configured diagnostic nodes (request/response id: projects):")
    for group in groups:
        print(f"  0x{group[0].request_id:X}/0x{group[0].response_id:X}: " + ", ".join(e.name for e in group))
    if getattr(args, "timing_project", None) is None:
        args.timing_project = ecus[0].project        # ECUs on one bus share its bitrate
    with open_bus(args) as bus:
        found, unknown = scan_bus(bus, [g[0] for g in groups], args.scan_time)
        for first in found:
            names = " | ".join(e.name for g in groups if g[0] is first for e in g)
            what = identify(bus, first)
            state = f"application '{what['app']}'" if what["app"] else "bootloader active"
            print(f"  answer on 0x{first.response_id:X} ({names}): {state}, bootloader '{what['boot'] or '?'}'")
            if what["boot"]:
                for warning in boot_version_warnings(what["boot"], first.prefix):
                    print(f"    {warning}")
    for can_id in unknown:
        print(f"  unknown ECU: TesterPresent answer on 0x{can_id:X} (no project in --projects owns this id)")
    if not found and not unknown:
        print("No ECU answered.", file=sys.stderr)
        return 1
    return 0


def print_target_compat(target: dict[str, int], out=print) -> None:
    out(f"  record format version {target['version']}, board/derivative 0x{target['target']:08X}, "
        f"memory layout 0x{target['layout']:08X}")
    out(f"  application 0x{target['app_start']:08X}..0x{target['app_end'] - 1:08X}, "
        f"validity record 0x{target['record_addr']:08X}")
    out(f"  installed application: {COMPAT_REASONS.get(target['record'], hex(target['record']))}")


APP_ACTIVE_NOTE = ("  application active - the bootloader's own identification (software version, hardware number, "
                   "security access, target compatibility) is readable only in the bootloader; "
                   "'info --bootloader' switches into it (10 02) and reads it there")


def info_report(client: Client, out=print) -> str | None:
    """What `canflash info` shows, on a client that is already connected; returns the application identification
    (DID 0xF181) if an application answered, None for the bootloader (TOOL-45).

    First who answers: a running application reports the bootloader identification found in its flash (DID 0xF180)
    but none of the bootloader's DIDs. Reading those anyway turned "not the bootloader" into "a bootloader older than
    BL-03/BL-04" (board finding 2026-09-21: nextbsw-bl 0.2.0 shown as XOR/rotl13 without software version). So with
    an application on the line only its identification and the bootloader identification from its flash are shown,
    plus a note how to read the rest; the age notes remain for a bootloader that answers itself."""
    app = read_application_identification(client)
    identification = read_boot_identification(client)
    if app is not None:
        out(f"application active: {app}{identification_note(app)}")
        if identification is None:
            out("  the application reports no bootloader identification (DID 0xF180)")
        else:
            out(f"  bootloader in flash (reported by the application): {identification}"
                f"{identification_note(identification)}")
            for warning in boot_version_warnings(identification):
                out(warning)
        out(APP_ACTIVE_NOTE)
        return app
    if identification is None:
        raise RuntimeError(f"the target answers neither DID 0x{DID_APPLICATION_SOFTWARE_ID:04X} nor "
                           f"0x{DID_BOOT_SOFTWARE_ID:04X}")
    out(f"bootloader active: {identification}{identification_note(identification)}")
    for warning in boot_version_warnings(identification):
        out(warning)
    # BL-04: identification without a session change, so "info" stays a read-only look at the target
    print_identification(read_software_version(client), read_hardware_number(client), out=out)
    # BL-03: method, key state and chip UID; whether this workstation has the project's master key
    info = read_security_info(client)
    out(f"  {security_state_text(info, read_uid(client) if info is not None else None)}")
    if info is not None:
        out(f"  {master_key_status()}")
    target = read_target_compat(client)
    if target is None:
        out("  no target compatibility (bootloader older than BL-02)")
    else:
        print_target_compat(target, out=out)
    return None


def cmd_info(args) -> int:
    """Read-only look at the target by default (no session change). --wait waits for the bootloader after a reset;
    --bootloader switches a running application into the bootloader (10 02), reads there and resets the ECU back
    into the application afterwards (TOOL-45)."""
    with open_bus(args) as bus:
        if args.wait > 0:
            # reach_bootloader() also drains the leftover 50 02 of the repeated requests, see drain()
            if not reach_bootloader(bus, args.wait, **power_kwargs(args)):
                print("No answer from the bootloader.", file=sys.stderr)
                return 1
        try:
            with make_client(bus) as client:
                app = info_report(client)
            if app is None or not getattr(args, "bootloader", False):
                return 0
            print("Switching into the bootloader (10 02) ...")
            if not reach_bootloader(bus, 0.0):
                print("No answer from the bootloader.", file=sys.stderr)
                return 1
            with make_client(bus) as client:
                info_report(client)
                # the application was running before: give it back (a hard reset starts it again)
                client.ecu_reset(udsoncan.services.ECUReset.ResetType.hardReset)
            print("ECU reset, the application starts again.")
        except RuntimeError as e:
            print(e, file=sys.stderr)
            return 1
    return 0


def master_key_status() -> str:
    """Whether the master key of the selected project is available here (name and source, never the key)."""
    try:
        key = master_key()
    except ValueError as e:
        return f"master key: {e}"
    return f"master key '{key.name}' from {key.source}" + (" (INSECURE test key)" if key.insecure else "")


def print_identification(version: str | None, hardware: str | None, out=print) -> None:
    """DIDs 0xF189 and 0xF191 of the target (BL-04); both missing means a bootloader from before BL-04."""
    if version is None and hardware is None:
        out("  no software version and hardware number (bootloader older than BL-04)")
        return
    out(f"  software version {version or '?'} (DID 0xF189), hardware number {hardware or '?'} (DID 0xF191)")
    own = CFG.get(TARGET_KEY, 0)
    if hardware is not None and own and hardware_identity(hardware) != own:
        name = TEXT.get(HARDWARE_KEY) or f"0x{own:08X}"
        out(f"  Warning: the selected project belongs to hardware {name}, the target reports {hardware} - another "
            "board or derivative.")


def cmd_fingerprint(args) -> int:
    """Reads the fingerprint of the programming session, after writing one with --write (BL-04)."""
    with open_bus(args) as bus:
        if not reach_bootloader(bus, args.wait, **power_kwargs(args)):
            print("No answer from the bootloader.", file=sys.stderr)
            return 1
        with make_client(bus) as client:
            print_identification(read_software_version(client), read_hardware_number(client))
            client.change_session(udsoncan.services.DiagnosticSessionControl.Session.programmingSession)
            client.unlock_security_access(1)
            if args.write:
                if write_fingerprint(client, args.tester) is None:
                    return 1
            value = read_fingerprint(client)
    if value is None:
        print("No fingerprint in this programming session (nothing written yet, or a bootloader before BL-04).")
        return 1
    print(f"Fingerprint: {format_fingerprint(value)}  [{value['raw'].hex()}]")
    return 0


def cmd_compat(args) -> int:
    """Compares a hex file (or just the layout of the build) with the target, without writing anything."""
    image = image_compat()
    print(f"Image: board/derivative 0x{image['target']:08X}, memory layout 0x{image['layout']:08X}, "
          f"application 0x{image['app_start']:08X}..0x{image['app_end'] - 1:08X}")
    if args.hexfile:
        data = load_app_image(Path(args.hexfile))
        print(f"  {args.hexfile}: {len(data)} bytes, CRC-32 0x{zlib.crc32(data):08X}")
    with open_bus(args) as bus:
        if not reach_bootloader(bus, args.wait, **power_kwargs(args)):
            print("No answer from the bootloader.", file=sys.stderr)
            return 1
        with make_client(bus) as client:
            identification = client.read_data_by_identifier_first(DID_BOOT_SOFTWARE_ID)
            print(f"Target: {identification}{identification_note(identification)}")
            for warning in boot_version_warnings(identification):
                print(warning)
            target = read_target_compat(client)
            if target is None:
                print("  no target compatibility (bootloader older than BL-02): cannot be checked on the target")
                return 1
            print_target_compat(target)
            diff = compat_differences(image, target)
    if diff:
        print("Not compatible:\n  - " + "\n  - ".join(diff), file=sys.stderr)
        return 1
    print("Compatible.")
    return 0


def cmd_record(args) -> int:
    data = load_app_image(Path(args.hexfile))
    ih = IntelHex()
    ih.frombytes(app_record_full(data), offset=CFG["BL_APP_RECORD_ADDR"])
    ih.write_hex_file(args.output)
    return 0


# ---------------------------------------------------------------- bootloader update

# Family s32k1xx (bootloader/platforms/s32k1xx/upd_family.h): Flash Configuration Field behind the vector table,
# unsecured (FSEC 0xFE: SEC 10, mass erase enabled, backdoor key disabled), no FPROT, FOPT 0x7F
FCF_ADDR = flashguard.CONFIG_FIELDS["s32k1xx"].offset
FCF_REFERENCE = flashguard.CONFIG_FIELDS["s32k1xx"].reference
BLOB_MAGIC = 0x50554C42  # "BLUP"
UPD_STEPS = {1: "start", 2: "image checked", 3: "stub written", 4: "sector written", 5: "sector 0 written",
             6: "record invalidated", 7: "done", 0x0E: "error"}
UPD_RESULTS = {0x00: "OK", 0x01: "already installed", 0x10: "blob header", 0x11: "image size", 0x12: "image CRC",
               0x13: "flash configuration field", 0x14: "vector table", 0x15: "incompatible version",
               0x16: "updater vector table", 0x17: "SBC watchdog not confirmed off", 0x20: "flash error (stub)",
               0x21: "flash error (sector)", 0x22: "flash error (sector 0)", 0x23: "flash error (record)",
               0x24: "verify error"}


def load_bootloader_image(path: Path) -> bytes:
    """Returns the bootloader bytes from the flash start, gaps filled with 0xFF, padded to whole phrases."""
    ih = IntelHex(str(path))
    addresses = ih.addresses()
    start, end = CFG["BL_FLASH_START"], CFG["BL_APP_START"]
    if not addresses:
        raise ValueError(f"{path}: empty")
    if max(addresses) >= end:
        raise ValueError(f"{path}: data at 0x{max(addresses):X}, outside the bootloader area 0x{start:X}..0x{end - 1:X}")
    if min(addresses) != start:
        raise ValueError(f"{path}: does not start at 0x{start:X} (vector table)")
    ih.padding = 0xFF
    data = ih.tobinstr(start=start, end=max(addresses))
    return data + b"\xff" * (-len(data) % CFG["BL_PHRASE_SIZE"])


@dataclass(frozen=True)
class ImageRule:
    """What a bootloader image of a family has at its start (BL-08): the tool's copy of the updater's family part
    (bootloader/platforms/<family>/upd_family.h and upd_family_check_image.inc), so that tool and updater accept the
    same images. `header_size` is UPD_IMAGE_HEADER_SIZE: smallest image, the version string is searched behind it;
    `check` raises ValueError for the start of an image."""

    family: str
    header_size: int
    check: Callable[[bytes], None]


def _check_start_s32k1xx(data: bytes) -> None:
    """Family s32k1xx: reference Flash Configuration Field, Cortex-M vector table (stack pointer in the application
    RAM, reset/NMI/HardFault Thumb addresses inside the image behind the FCF)."""
    size = len(data)
    fcf = data[FCF_ADDR : FCF_ADDR + 16]
    if fcf != FCF_REFERENCE:
        fsec, fopt = fcf[12], fcf[13]
        reasons = []
        if fsec & 0x03 != 0x02:
            reasons.append(f"FSEC 0x{fsec:02X} secures the chip")
        if (fsec >> 4) & 0x03 == 0x02:
            reasons.append(f"FSEC 0x{fsec:02X} disables mass erase (chip unrecoverable)")
        if (fsec >> 6) & 0x03 == 0x02:
            reasons.append(f"FSEC 0x{fsec:02X} enables the backdoor key")
        if fcf[8:12] != b"\xff\xff\xff\xff":
            reasons.append(f"FPROT {fcf[8:12].hex()} protects sectors")
        if fopt != 0x7F:
            reasons.append(f"FOPT 0x{fopt:02X} instead of 0x7F")
        raise ValueError(f"Flash Configuration Field {fcf.hex()} rejected: " + ("; ".join(reasons) or "not the reference value"))
    sp, *vectors = struct.unpack_from("<IIII", data)
    if not CFG["BL_RAM_START"] < sp <= CFG["BL_RAM_END"]:
        raise ValueError(f"initial stack pointer 0x{sp:08X} outside RAM")
    for name, v in zip(("reset", "NMI", "HardFault"), vectors):
        if not v & 1 or not FCF_ADDR + 16 <= v & ~1 < size:
            raise ValueError(f"{name} vector 0x{v:08X} not a Thumb address inside the image")


# Families with an updater port (bootloader/updater/platforms/<family>). A family without a rule here has no
# bootloader update over CAN; the check says so instead of applying the rule of another family.
IMAGE_RULES = {
    "s32k1xx": ImageRule("s32k1xx", FCF_ADDR + 16, _check_start_s32k1xx),
}


def selected_family() -> str:
    """MCU family of the selected layout (BL-08): BL_FAMILY of the generated bl_memory.h (blgen, BL-10) or the
    derivative of the project. No guess: a layout without it was generated before BL-10 and is generated again by
    configuring its build."""
    family = TEXT.get(FAMILY_KEY)
    if family:
        return family
    layout = SOURCE.get(LAYOUT_FILE_KEY)
    if layout is not None:
        raise ValueError(f"{layout} names no MCU family ({FAMILY_KEY}): generated before BL-10, configure the build "
                         "again (cmake --preset ...) or pass --project projects/<project>/project.yaml")
    raise ValueError("no MCU family for the bootloader image check: pass --layout "
                     "<build>/generated/board/bootloader/bl_memory.h or --project projects/<project>/project.yaml")


def image_rule(family: str | None = None) -> ImageRule:
    family = family or selected_family()
    rule = IMAGE_RULES.get(family)
    if rule is None:
        raise ValueError(f"no bootloader image rule for the family {family}: the bootloader update over CAN needs an "
                         f"updater port (bootloader/updater/platforms/{family}) and its rule in "
                         "tools/canflash/canflash.py IMAGE_RULES (BL-08)")
    return rule


def check_bootloader_image(data: bytes) -> str:
    """Same checks as upd_check_blob() in the updater: size, start of the image by the rule of the selected MCU family
    (IMAGE_RULES; s32k1xx: flash configuration field and vector table), version. Returns the version string, raises
    ValueError."""
    rule = image_rule()
    size = len(data)
    area = CFG["BL_APP_START"] - CFG["BL_FLASH_START"]
    if size > area or size < rule.header_size:
        raise ValueError(f"image size {size} outside 0x{rule.header_size:X}..0x{area:X}")
    rule.check(data)
    # "<prefix> <major>." with the project's prefix or the former name (PLAT-08); first occurrence, as the updater
    bl_version()                        # raises without a selected project
    major = BL_VERSION_NUMBER.split(".")[0]
    needles = [f"{prefix} {major}.".encode() for prefix in accepted_prefixes()]
    found = [pos for pos in (data.find(needle, rule.header_size) for needle in needles) if pos >= 0]
    if not found:
        names = " or ".join(f"'{needle.decode()}'" for needle in needles)
        raise ValueError(f"no version string {names} in the image (incompatible bootloader)")
    pos = min(found)
    end = data.find(b"\0", pos)
    return data[pos : end if end >= 0 else pos + 32].decode("ascii", "replace")


def bootloader_blob(data: bytes) -> bytes:
    head = struct.pack("<III", BLOB_MAGIC, len(data), zlib.crc32(data))
    return head + struct.pack("<I", zlib.crc32(head)) + data


def find_bootloader_blob(app: bytes) -> tuple[int, int]:
    """Offset and length (header included) of the embedded blob in an updater application image."""
    pos = app.find(struct.pack("<I", BLOB_MAGIC))
    while pos >= 0:
        head = app[pos : pos + 16]
        if len(head) == 16:
            _, size, crc, head_crc = struct.unpack("<IIII", head)
            data = app[pos + 16 : pos + 16 + size]
            if head_crc == zlib.crc32(head[:12]) and len(data) == size and zlib.crc32(data) == crc:
                return pos, 16 + size
        pos = app.find(struct.pack("<I", BLOB_MAGIC), pos + 1)
    raise ValueError("no bootloader blob found (not an updater image?)")


def extract_bootloader_image(app: bytes) -> bytes:
    """Finds the embedded blob in an updater application image and returns the bootloader bytes."""
    pos, length = find_bootloader_blob(app)
    return app[pos + 16 : pos + length]


def check_updater_stub_field(updater: bytes, family: str | None = None) -> None:
    """NVM-15: the updater erases sector 0 twice (stub, then the new bootloader). Its stub has to carry the family's
    configuration field, programmed right after the erase (UPD_STUB_EARLY_PHRASES, upd_family.h), or a reset while
    the stub is sector 0 leaves the chip secured. The field of the stub is a constant of the updater (fcfReference):
    it has to be in the image outside the embedded bootloader. Raises ValueError. The write order itself is tested
    against the updater core (test_bl_update_host.py), not here."""
    rule = flashguard.CONFIG_FIELDS.get(family or selected_family())
    if rule is None:
        return
    pos, length = find_bootloader_blob(updater)
    outside = updater[:pos] + b"\0" * length + updater[pos + length :]
    if rule.reference not in outside:
        raise ValueError(f"the updater carries no flash configuration field {rule.reference.hex()} for its stub: it "
                         "would leave sector 0 without the field after erasing it, and a reset in that state secures "
                         "the chip (NVM-15) - refused, nothing was sent")


def cmd_blimage(args) -> int:
    data = load_bootloader_image(Path(args.hexfile))
    try:
        version = check_bootloader_image(data)
    except ValueError as e:
        print(f"{args.hexfile}: {e}", file=sys.stderr)
        return 1
    blob = bootloader_blob(data)
    if args.c_out:
        rows = ",\n".join("    " + ", ".join(f"0x{b:02X}" for b in blob[i : i + 16]) for i in range(0, len(blob), 16))
        Path(args.c_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.c_out).write_text(genlicense.stamp(
            f"/* Generated by tools/canflash/canflash.py blimage from {Path(args.hexfile).name}, do not edit.\n"
            f" * {version}, {len(data)} bytes, CRC-32 0x{zlib.crc32(data):08X} */\n\n"
            "#include <stdint.h>\n\n"
            "extern uint32_t const upd_blob_size;\n"
            "extern uint8_t const upd_blob[];\n\n"
            f"uint32_t const upd_blob_size = {len(blob)}UL;\n"
            f"uint8_t const upd_blob[{len(blob)}] = {{\n{rows}}};\n", "upd_blob.c"))
    if args.bin_out:
        Path(args.bin_out).write_bytes(blob)
    print(f"{version}, {len(data)} bytes, CRC-32 0x{zlib.crc32(data):08X}")
    return 0


def watch_updater(bus: can.BusABC, seconds: float, log=print):
    """Follows the updater's status frames. Returns (result, stub/final/secured window in ms) or None."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        msg = bus.recv(0.1)
        if not msg or msg.arbitration_id != CFG["UPD_STATUS_CAN_ID"] or len(msg.data) != 8:
            continue
        step, detail = msg.data[0], msg.data[1]
        windows = tuple(v / 10 for v in struct.unpack(">HHH", bytes(msg.data[2:8])))
        log(f"  updater: {UPD_STEPS.get(step, hex(step))} ({detail})")
        if step in (7, 0x0E):
            return detail, windows
    return None


def confirm_update(version: str, ask=input, out=print, csec: str | None = None) -> bool:
    out(
        f"This replaces the bootloader with {version}.\n"
        "A power loss while sector 0 is rewritten (twice, milliseconds each) leaves a board that can only be\n"
        "recovered via J-Link (flash-bootloader). The application must be flashed again afterwards."
    )
    if csec:
        out(csec)
    try:
        return ask("Type UPDATE to continue: ").strip() == "UPDATE"
    except EOFError:                # input closed while asking: no confirmation (TOOL-53)
        out("")
        return False


def interactive(stream=None) -> bool:
    """True if somebody can type the confirmation: standard input is a terminal (TOOL-53)."""
    stream = sys.stdin if stream is None else stream
    try:
        return stream is not None and stream.isatty()
    except (AttributeError, ValueError):   # closed or replaced stream
        return False


NOT_INTERACTIVE = ("canflash blupdate: needs --yes when not interactive (no terminal to type the confirmation); "
                   "nothing was written.")


def read_boot_version(client: Client) -> str:
    client.set_config("data_identifiers", did_codecs())
    return client.read_data_by_identifier_first(DID_BOOT_SOFTWARE_ID)


# Exit code of `blupdate --app` when the bootloader was replaced but the application could not be flashed (BL-25)
APP_NOT_FLASHED = 5
# What the UDS conversation can end with (udsoncan has no common base class)
UDS_ERRORS = (udsoncan.exceptions.TimeoutException, udsoncan.exceptions.NegativeResponseException,
              udsoncan.exceptions.InvalidResponseException, udsoncan.exceptions.UnexpectedResponseException)


def describe_error(e: Exception) -> str:
    """One line for an error of the UDS conversation, without a traceback (BL-25)."""
    if isinstance(e, udsoncan.exceptions.TimeoutException):
        return f"no response in time ({e})"
    return str(e) or type(e).__name__


def bootloader_update(bus: can.BusABC, updater: bytes, version: str, wait: float = 0.0, timeout: float = 30.0,
                      app: bytes | None = None, log=print, progress: str = "auto",
                      power: "powercycle.PowerSwitch | None" = None, off_s: float = powercycle.DEFAULT_OFF_S,
                      follow: can.BusABC | None = None) -> int:
    """`follow` (BL-39, --follow-channel): bus of the updater and of the new bootloader when the update moves the
    bootloader to another CAN bus of the ECU (build option BOOTLOADER_CAN_BUS). The updater is flashed over `bus`
    by the installed bootloader, but reports on the bus it was built for - on `bus` alone its status was never
    seen (board 2026-10-02, VER-04: "No status from the updater" with a Diag-CAN updater flashed over CAN0)."""
    if not reach_bootloader(bus, wait, log, power, off_s):
        log("No answer from the bootloader.")
        return 1
    with make_client(bus) as client:
        log(f"Installed: {read_boot_version(client)}")
        flash_application(client, updater, log, progress=progress)
        client.ecu_reset(udsoncan.services.ECUReset.ResetType.hardReset)
    if follow is not None:
        bus = follow
        log("Updater flashed, ECU reset. Waiting for the updater on the follow bus ...")
    else:
        log("Updater flashed, ECU reset. Waiting for the updater ...")
    status = watch_updater(bus, timeout, log)
    if status is None:
        log("No status from the updater.")
        return 3
    result, (stub_ms, final_ms, secured_ms) = status
    log(f"Updater result: {UPD_RESULTS.get(result, hex(result))}; unbootable windows: stub {stub_ms} ms, "
        f"sector 0 {final_ms} ms; FCF erased at most {secured_ms} ms")
    if result not in (0x00, 0x01):
        return 3
    if not wait_for_bootloader(bus, 5.0):
        log("New bootloader does not answer.")
        return 3
    drain(bus, quiet=0.03)  # leftover 50 02 of the repeated requests, see drain()
    with make_client(bus) as client:
        installed = read_boot_version(client)
        info = read_security_info(client)
        uid = read_uid(client) if info is not None else None
    log(f"Now installed: {installed}")
    if installed != version:
        return 3
    if info is not None:
        log(f"  {security_state_text(info, uid)}")
    if info is not None and info["state"] != 1:
        # BL-03: a new bootloader on a device that was never provisioned: nothing can be flashed over CAN yet
        log("The new bootloader has no device key yet: provision it over the debugger (tools/canflash/provision.py), "
            "then flash the application." + (" The application was not flashed." if app is not None else ""))
        return 4 if app is not None else 0
    if app is not None:
        # BL-25: the bootloader update is done at this point, whatever happens to the application. A failure here
        # ended in a traceback before, which read like a failed update (board 2026-09-21); say what state the
        # ECU is in and what to do next instead.
        try:
            flash_over_bus(bus, app, 0.0, log, progress)
        except (RuntimeError, ValueError, can.CanError, *UDS_ERRORS) as e:
            log(f"Bootloader updated to {installed}, application NOT flashed: {describe_error(e)}")
            log("The ECU stays in the new bootloader; flash the application again with 'canflash flash <app.hex>'.")
            return APP_NOT_FLASHED
        log("Application flashed, ECU reset.")
    return 0


def blupdate_csec_notice(project) -> str | None:
    """NVM-15: warning for a bootloader update on a part that may be CSEc-partitioned (project key slots or a
    derivative with CSEc), None otherwise."""
    target = None
    if project is not None:
        try:
            target = flashguard.target_of_project(Path(project))
        except (OSError, ValueError) as e:
            print(f"canflash: {e}; CSEc state of the part unknown", file=sys.stderr)
    return flashguard.csec_notice(flashguard.csec_risk(target))


def cmd_blupdate(args) -> int:
    updater = load_app_image(Path(args.hexfile))
    try:
        image = extract_bootloader_image(updater)
        version = check_bootloader_image(image)
        check_updater_stub_field(updater)
    except ValueError as e:
        print(f"{args.hexfile}: {e}", file=sys.stderr)
        return 1
    print(f"Embedded bootloader: {version}, {len(image)} bytes, CRC-32 0x{zlib.crc32(image):08X}")
    csec = blupdate_csec_notice(getattr(args, "timing_project", None))
    if csec and args.yes:
        print(csec)
    app = load_app_image(Path(args.app)) if args.app else None
    if not args.yes and not interactive():
        # TOOL-53 (1): from a pipe, CMake or Studio the prompt ended in an EOFError traceback (board 2026-09-22)
        print(NOT_INTERACTIVE, file=sys.stderr)
        return 1
    if not args.yes and not confirm_update(version, csec=csec):
        print("Aborted.")
        return 1
    with open_bus(args) as bus:
        follow = None
        try:
            if getattr(args, "follow_channel", None) is not None and args.follow_channel != args.channel:
                timing = resolve_timing(args)
                if getattr(args, "follow_bitrate", None):
                    timing = BusTiming(args.follow_bitrate, None, None, args.quanta)
                follow = bus_for(args.interface, args.follow_channel, timing, args.app_name)
            return bootloader_update(bus, updater, version, args.wait, args.timeout, app, progress=args.progress,
                                     follow=follow, **power_kwargs(args))
        except RuntimeError as e:
            print(e, file=sys.stderr)
            return 1
        finally:
            if follow is not None:
                follow.shutdown()


def cmd_keygen(args) -> int:
    """New random master key for the security access (BL-03) in the key store of this workstation."""
    path = blkeys.create_master_key(args.name, Path(args.dir) if args.dir else None)
    print(f"Master key '{args.name}' written to {path}.\n"
          "Keep a copy outside this PC (password manager, sealed medium): every device provisioned with it needs it "
          "for each flash over CAN. Never commit it.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def bus_args(p, ecu=True):
        p.add_argument("--interface", default="vector")
        p.add_argument("--channel", default=0, type=int)
        p.add_argument("--bitrate", default=None, type=int,
                       help="bit/s (default: can.<bus> of the project's bootloader bus, otherwise 500000)")
        p.add_argument("--sample-point", default=None, type=float,
                       help="sample point in %% for adapters with segment settings (vector; default: project)")
        p.add_argument("--sjw", default=None, type=int, help="synchronisation jump width in tq (default: project)")
        p.add_argument("--quanta", default=16, type=int, help="time quanta per bit of the adapter (default 16)")
        p.add_argument("--app-name", default=None, help="Vector application name (default: global channel index)")
        p.add_argument("--wait", default=0.0, type=float, help="seconds to wait for the bootloader after reset")
        # TOOL-51: optional power cycle while waiting; without these options nothing is switched
        p.add_argument("--power-cycle", default=None, metavar="DEVICE",
                       help="with --wait: switch this LabControl power supply off and on (e.g. psu:COM6; address "
                            f"and token from {powercycle.LABCONTROL_URL_ENV}/{powercycle.LABCONTROL_TOKEN_ENV})")
        p.add_argument("--power-on-current", default=None, type=float, metavar="A",
                       help="with --power-cycle: switch on with this current limit (PSU_CURR) instead of "
                            "PSU_OUT_ON (0.1 A on an HCS-34xx)")
        p.add_argument("--power-off-cmd", default=None, metavar="CMD",
                       help="with --wait: shell command that switches the ECU off (together with --power-on-cmd)")
        p.add_argument("--power-on-cmd", default=None, metavar="CMD", help="shell command that switches it on")
        p.add_argument("--power-off-time", default=powercycle.DEFAULT_OFF_S, type=float, metavar="S",
                       help=f"seconds without power (default {powercycle.DEFAULT_OFF_S})")
        p.add_argument("--projects", action="append", default=None,
                       help="project.yaml or directory of projects the ECUs come from (repeatable; default: "
                            "projects/ of the repository)")
        if ecu:
            p.add_argument("--ecu", default=None,
                           help="target ECU by project name when several ECUs share the bus (see 'scan'); "
                                "takes the diagnostic addresses and the memory layout of that project")
        p.add_argument("--progress", default="auto", choices=PROGRESS_MODES,
                       help="download progress: auto (running line on a terminal, a line every 10 %% "
                            "otherwise), line, steps, off")

    def tester_arg(p):
        p.add_argument("--tester", default=None,
                       help=f"tester identification of the fingerprint, at most {FINGERPRINT_TESTER_LEN} "
                            f"characters (default: {TESTER_ID_ENV} of the workplace, otherwise "
                            f"'{TESTER_ID_DEFAULT}')")

    p = sub.add_parser("flash", help="erase, download, check and reset")
    p.add_argument("hexfile")
    p.add_argument("--no-prepare", action="store_true",
                   help="skip the preprogramming step of ISO 14229-1 16.3.1.1 (10 03, 85 02, 28 03)")
    p.add_argument("--stay-in-bootloader", action="store_true",
                   help="after a rejected image leave the ECU in the bootloader instead of resetting it (11 01)")
    p.add_argument("--skip-local-compat-check", action="store_true",
                   help="TEST AID, prints a warning: hand an image that does not match the target to the "
                        "bootloader's check (routine 0xF1A0) instead of refusing it here, to verify the bootloader's "
                        "refusal; such an image is never flashed")
    bus_args(p)
    tester_arg(p)
    p.set_defaults(func=cmd_flash)

    p = sub.add_parser("info", help="read the bootloader version, software version and hardware number")
    bus_args(p)
    p.add_argument("--bootloader", action="store_true",
                   help="if the application answers: switch into the bootloader (10 02), read its identification "
                        "there and reset the ECU back into the application (default: read-only, no session change)")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("fingerprint", help="read the fingerprint of the programming session, with --write set it")
    bus_args(p)
    tester_arg(p)
    p.add_argument("--write", action="store_true", help="write a fingerprint (date of today) before reading")
    p.set_defaults(func=cmd_fingerprint)

    p = sub.add_parser("scan", help="which ECUs of the projects answer on the bus (TesterPresent), and what runs")
    bus_args(p, ecu=False)
    p.add_argument("--scan-time", default=0.3, type=float, help="seconds to collect answers")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("compat", help="compare board, derivative, memory layout and addresses with the target")
    p.add_argument("hexfile", nargs="?", help="application hex (optional; only the layout is compared without it)")
    bus_args(p)
    p.set_defaults(func=cmd_compat)

    p = sub.add_parser("record", help="write the validity record of a hex file for J-Link flashing")
    p.add_argument("hexfile")
    p.add_argument("output")
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("blimage", help="check a bootloader hex and write the blob embedded into the updater")
    p.add_argument("hexfile")
    p.add_argument("--c-out", help="C source with upd_blob[]")
    p.add_argument("--bin-out", help="raw blob")
    p.set_defaults(func=cmd_blimage)

    p = sub.add_parser("keygen", help="create a master key of the security access in the key store (BL-03)")
    p.add_argument("name", help="key name as the project gives it (diagnostics.bootloader.security.key)")
    p.add_argument("--dir", default=None,
                   help=f"directory instead of the key store ({blkeys.KEY_DIR_ENV} or %%LOCALAPPDATA%%/neXtBSW/keys)")
    p.set_defaults(func=cmd_keygen)

    p = sub.add_parser("unlock", help="unlock a security level of the running application (27, COM-30), then "
                                      "write and read identifiers")
    bus_args(p)
    p.add_argument("--level", default=None,
                   help="name or requestSeed sub-function of the level (default: the first of the description)")
    p.add_argument("--uid", default=None,
                   help="chip UID as hex, if the application's description has no identifier with chip.uid "
                        "('canflash info --bootloader' shows it)")
    p.add_argument("--write", action="append", default=[], metavar="DID=HEX",
                   help="WriteDataByIdentifier after unlocking, e.g. FD00=12345678 (repeatable)")
    p.add_argument("--read", action="append", default=[], metavar="DID",
                   help="ReadDataByIdentifier after the writes, e.g. FD00 (repeatable)")
    p.set_defaults(func=cmd_unlock)

    p = sub.add_parser("blupdate", help="replace the bootloader: flash the updater application and follow it")
    p.add_argument("hexfile", help="updater hex (blupdater.hex)")
    p.add_argument("--app", help="application hex to flash after the update")
    p.add_argument("--yes", action="store_true", help="skip the confirmation")
    p.add_argument("--timeout", default=30.0, type=float, help="seconds to wait for the updater result")
    p.add_argument("--follow-channel", default=None, type=int,
                   help="channel on which the updater reports and the new bootloader answers, when the updater "
                        "was built for another CAN bus of the ECU (BOOTLOADER_CAN_BUS, BL-39); default: --channel")
    p.add_argument("--follow-bitrate", default=None, type=int,
                   help="bit/s of the follow channel (default: the timing of --channel)")
    bus_args(p)
    p.set_defaults(func=cmd_blupdate)

    for p in [parser] + list(sub.choices.values()):
        top = p is parser
        p.add_argument("--layout", default=None if top else argparse.SUPPRESS,
                       help="generated bl_memory.h of the build (default: found from the hex file's build directory)")
        p.add_argument("--project", default=None if top else argparse.SUPPRESS,
                       help="project.yaml: memory layout calculated from the project instead")
        p.add_argument("--key-name", default=None if top else argparse.SUPPRESS,
                       help="name of the master key of the security access, for a --layout without bl_security.h "
                            f"(default: BL_SECURITY_KEY_NAME of bl_security.h; environment {KEY_NAME_ENV})")

    return parser


def prepare(args) -> None:
    """Chooses the target (--ecu, --layout, --project, environment, build of the hex file) and remembers the
    project the bus timing comes from."""
    hints = [Path(getattr(args, a)) for a in ("hexfile", "app") if getattr(args, a, None)]
    ecu = getattr(args, "ecu", None)
    if ecu:
        if args.layout or args.project:
            raise ValueError("--ecu chooses the project itself; leave out --layout and --project")
        args.project = str(project_by_name(ecu, args.projects or [ROOT / "projects"]))
    if args.command == "scan":
        args.timing_project = args.project or os.environ.get("NX_PROJECT")
        return
    if args.command == "keygen":                    # works on the workstation only, no target
        return
    # "info" only talks to the target and needs the diagnostic addresses, every other command the
    # memory layout as well (COM-11: both are generated per project, nothing is a tool constant)
    select_layout(args.layout, args.project, *hints, need_layout=args.command not in ("info", "unlock"))
    if getattr(args, "key_name", None):              # TOOL-204: explicit name wins, the environment only fills in
        TEXT[KEY_NAME_KEY] = args.key_name
    elif os.environ.get(KEY_NAME_ENV) and KEY_NAME_KEY not in TEXT:
        TEXT[KEY_NAME_KEY] = os.environ[KEY_NAME_ENV]
    build = find_layout(*hints) if ecu else None
    if build is not None:
        # a hex file of another ECU would only be refused by the compatibility check after the switch into the
        # bootloader - and that switch would already reset the wrong ECU's application
        built = load_build(build)
        other = [k for k in DIAG_KEYS if built.get(k) != CFG[k]]
        if other:
            raise ValueError(f"{hints[0]} was built with other diagnostic addresses than ECU {ecu} "
                             f"({', '.join(other)}): wrong hex file or wrong --ecu")
    if hasattr(args, "wait"):
        args.power = powercycle.switch_from_args(args)   # TOOL-51, None without a power option
    if hasattr(args, "interface"):
        project = args.project or os.environ.get("NX_PROJECT")
        if project is None:
            layout = args.layout or os.environ.get("NX_BL_LAYOUT")
            layout = Path(layout) if layout else find_layout(*hints)
            project = project_of_build(layout) if layout is not None else None
        args.timing_project = project


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        prepare(args)
        return args.func(args)
    except (ValueError, RuntimeError) as e:
        print(f"canflash: {e}", file=sys.stderr)
        return 1
    except NegativeResponseException as e:
        print(f"Negative response: {e}", file=sys.stderr)
        return 2
    except udsoncan.exceptions.TimeoutException as e:
        # BL-25: a target that stops answering is an outcome of flashing, not a program error - no traceback
        print(f"canflash: {describe_error(e)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
