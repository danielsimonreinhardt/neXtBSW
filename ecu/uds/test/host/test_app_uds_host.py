"""Host tests of the application UDS stack (ecu/uds) with udsoncan/can-isotp and the unchanged canflash tool.

The application stack (DiagStack + OpenBSW uds/docan/transport) runs as DLL (build_app_uds_host.py), the
bootloader core as bl_host.dll (README). After 10 02 the simulated ECU switches from the application to the
bootloader with boot request, so "canflash flash" works without --wait.

    .venv/Scripts/python ecu/uds/test/host/test_app_uds_host.py
"""

import ctypes
import json
import os
import struct
import sys
import time
import unittest
from pathlib import Path

import can
import udsoncan
from udsoncan.exceptions import NegativeResponseException

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT / "tools" / "canflash"))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools" / "test"))
sys.path.insert(0, str(ROOT / "tools"))
import nxconfig.config  # noqa: E402  (COM-33: lengths of the identifiers, split of a response)
from nxconfig import diagdesc  # noqa: E402
import blkeys  # noqa: E402  (COM-30: device key and key of the application levels)
import build_app_uds_host
import build_dirs  # noqa: E402
import did_equivalence  # noqa: E402  (COM-28: before/after sequence of the hand written identifiers)
import hostbuild  # noqa: E402
import canflash  # noqa: E402
from test_bootloader_host import HostBootloader, make_app_image, selected  # noqa: E402

CFG = canflash.CFG
# PLAT-62: the eval board demo where it exists (the prebuilt bootloader DLL is built for it), else the own full test
# profile with the same memory layout and diagnostic addresses - the eval board is private (PLAT-46 F10), and the
# importers of this module (tools/diaggen host tests) run in the exported open tree as well
EVAL_PROJECT = next(p for p in (ROOT / "projects" / "evalboard_s32k144_demo" / "project.yaml",
                                ROOT / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full"
                                / "project.yaml") if p.is_file())
# second build profile of the repository with **other** diagnostic addresses (COM-11): request 0x6F0,
# functional 0x6DF, response 0x6F8, filler 0xAA, flow control with block size 8 and STmin 1 ms
OTHER_PROJECT = ROOT / "projects" / "buildtest_s32k148_min" / "project.yaml"
BL_VERSION = canflash.bl_version()          # identification prefix of the project plus the version number
APP_DLL = Path(os.environ.get("APP_UDS_HOST_DLL", ROOT / "build" / "host" / "app_uds_host.dll"))
OTHER_APP_DLL = Path(os.environ.get("APP_UDS_HOST_DLL_OTHER", ROOT / "build" / "host" / "app_uds_host_other.dll"))
BL_DLL = Path(os.environ.get("BL_HOST_DLL", ROOT / "build" / "host" / "bl_host.dll"))

DID_APP_SOFTWARE_ID = 0xF181
DID_ACTIVE_SESSION = 0xF186
DID_RAM_TEST = 0xFD00
# COM-28: test profile of the generated jobs (own sessions, security levels, identifiers and routines)
PROFILE_DIAG = ROOT / "tools" / "diaggen" / "test" / "profile" / "diag.yaml"
PROFILE_APP_DLL = Path(os.environ.get("APP_UDS_HOST_DLL_PROFILE",
                                      ROOT / "build" / "host" / "app_uds_host_profile.dll"))


def description(path: Path):
    """The diagnostic description (model.DiagDescription) of a diag.yaml, as a tester would read it."""
    desc, messages, _ = nxconfig.config.read_diag(path)
    if desc is None:
        raise AssertionError([str(m) for m in messages])
    return desc


# COM-33: the eval board's description - F180/F181 answer numberOfModules plus a 32 byte text filled up with NUL
EVAL_DIAG = description(EVAL_PROJECT.parent / "diag.yaml")
EVAL_LENGTHS = {did.identifier: did.length_bytes for did in EVAL_DIAG.data_identifiers}


def padded(text: str, did: int) -> bytes:
    """The text of identification identifier `did` as the application answers it: filled up to its width."""
    return text.encode().ljust(EVAL_LENGTHS[did] - 1, b"\0")


def host_key(seed: bytes, level: int, key_bytes: int) -> bytes:
    """Key of the host build's test algorithm (AppUdsHost.cpp HostSecurityAlgorithm)."""
    return bytes(seed[i % len(seed)] ^ 0xA5 ^ level for i in range(key_bytes))
PHYS, FUNC, RESP = CFG["BL_CAN_ID_REQUEST_PHYS"], CFG["BL_CAN_ID_REQUEST_FUNC"], CFG["BL_CAN_ID_RESPONSE"]
PAD = CFG["BL_ISOTP_PADDING"]


class HostApp:
    def __init__(self, path: Path):
        self.dll = ctypes.CDLL(str(path))
        self.dll.app_millis.restype = ctypes.c_uint32
        self.dll.app_session.restype = ctypes.c_uint8
        self.dll.app_ram_test_data.restype = ctypes.POINTER(ctypes.c_uint8)
        self.dll.app_router_locked.restype = ctypes.c_uint32
        self.dll.app_router_failures.restype = ctypes.c_uint32
        self.dll.app_assert_count.restype = ctypes.c_uint32
        self.dll.app_communication_changes.restype = ctypes.c_uint32
        self.dll.app_dtc_changes.restype = ctypes.c_uint32
        self.dll.app_uds_bus_id.restype = ctypes.c_uint8
        self.dll.app_transceiver_bus_id.restype = ctypes.c_uint8
        # COM-28: RTE stand-in of the generated jobs (DiagRteStub.cpp) and state of 27/31
        self.dll.diag_stub_set.argtypes = [ctypes.c_char_p, ctypes.c_uint32]
        self.dll.diag_stub_get.argtypes = [ctypes.c_char_p]
        self.dll.diag_stub_get.restype = ctypes.c_uint32
        self.dll.diag_stub_writes.argtypes = [ctypes.c_char_p]
        self.dll.diag_stub_writes.restype = ctypes.c_uint32

    def stub_set(self, target: str, bits: int):
        """What a port of the RTE delivers to the diagnosis (raw bits of the value), by connection target."""
        self.dll.diag_stub_set(target.encode(), ctypes.c_uint32(bits & 0xFFFFFFFF))

    def stub_get(self, target: str) -> int:
        return self.dll.diag_stub_get(target.encode())

    def stub_writes(self, target: str) -> int:
        return self.dll.diag_stub_writes(target.encode())

    def power_on(self, bl_version: str | None = BL_VERSION):
        self.dll.app_power_on(bl_version.encode() if bl_version is not None else None)

    def poll_ms(self, ms: float):
        self.dll.app_poll_us(ctypes.c_uint32(int(ms * 1000)))

    def rx(self, cid: int, data: bytes) -> bool:
        buf = (ctypes.c_uint8 * 8).from_buffer_copy(data.ljust(8, b"\0"))
        return self.dll.app_can_rx(ctypes.c_uint32(cid), buf, ctypes.c_uint8(len(data))) == 1

    def tx(self):
        cid, length, buf = ctypes.c_uint32(), ctypes.c_uint8(), (ctypes.c_uint8 * 8)()
        if self.dll.app_can_tx(ctypes.byref(cid), buf, ctypes.byref(length)):
            return cid.value, bytes(buf[: length.value])
        return None


class EcuBus(can.BusABC):
    """python-can bus to the simulated ECU: application first, bootloader after its boot request.

    Simulated time follows real time. A frame the application receives while it is already resetting is
    lost (not acknowledged); the bootloader is started as soon as the application requested it.
    """

    def __init__(self, app: HostApp, bl: HostBootloader | None, **kwargs):
        super().__init__(channel="ecu", **kwargs)
        self.app, self.bl = app, bl
        self.in_bootloader = False
        self.last = time.perf_counter()
        self.log: list[tuple[str, int, bytes]] = []
        self.on_bus: list[tuple[int, bytes]] = []

    def _advance(self):
        now = time.perf_counter()
        if self.in_bootloader:
            # whole milliseconds only, the rest stays for the next call: a poll loop faster than 1 ms would
            # otherwise hand the bootloader 0 ms every time and its clock would fall behind real time (BL-25:
            # the bootloader now waits on its own clock for the gap between two response pending)
            ms = int((now - self.last) * 1e3)
            self.last += ms / 1e3
            self.bl.dll.host_poll(ctypes.c_uint32(ms))
        else:
            us = int((now - self.last) * 1e6)
            self.last += us / 1e6
            self.app.dll.app_poll_us(ctypes.c_uint32(us))
            self._check_handover()

    def _check_handover(self):
        if not self.in_bootloader and self.app.dll.app_boot_request() and self.bl is not None:
            # frames confirmed before the software reset are already on the bus
            while (frame := self.app.tx()) is not None:
                self.on_bus.append(frame)
            self.bl.dll.host_power_on(1)
            self.in_bootloader = True

    def send(self, msg, timeout=None):
        data = bytes(msg.data)
        self.log.append(("rx", msg.arbitration_id, data))
        self._advance()
        if self.in_bootloader:
            buf = (ctypes.c_uint8 * 8).from_buffer_copy(data.ljust(8, b"\0"))
            self.bl.dll.host_can_rx(ctypes.c_uint32(msg.arbitration_id), buf, ctypes.c_uint8(len(data)))
        else:
            self.app.rx(msg.arbitration_id, data)

    def _recv_internal(self, timeout):
        end = time.perf_counter() + (timeout if timeout is not None else 3600)
        cid, length, buf = ctypes.c_uint32(), ctypes.c_uint8(), (ctypes.c_uint8 * 8)()
        while True:
            self._advance()
            frame = None
            if self.on_bus:
                frame = self.on_bus.pop(0)
            elif self.in_bootloader:
                if self.bl.dll.host_can_tx(ctypes.byref(cid), buf, ctypes.byref(length)):
                    frame = (cid.value, bytes(buf[: length.value]))
            else:
                frame = self.app.tx()
            if frame:
                self.log.append(("tx", frame[0], frame[1]))
                return can.Message(arbitration_id=frame[0], data=frame[1], is_extended_id=False), False
            if time.perf_counter() >= end:
                return None, False
            time.sleep(0.0005)


def app_dll(out: Path, project: Path, diag: Path | None = None) -> Path:
    """Application UDS DLL for the diagnostic addresses of `project`, rebuilt when a source changed.

    Checksum instead of modification time (COM-09): a DLL that is newer than a source which changed back and
    forth or came from another branch is not rebuilt by a mtime check - such a stale DLL made this test red and
    looked like a time dependent failure (2026-09-16). COM-28: the diagnostic description (the project's yaml
    files or `diag`) and tools/diaggen belong to the sources."""
    sources = list(HERE.glob("*.cpp")) + list((ROOT / "ecu" / "uds").rglob("*.[ch]*")) + [project]
    sources += list(project.parent.glob("*.yaml")) + list((ROOT / "tools" / "diaggen").glob("*.py"))
    sources += list((ROOT / "tools" / "diaggen").glob("*.yaml")) + ([diag] if diag else [])
    # COM-30: SHA-256/HMAC and CRC-32 of the bootloader core, key record format of bl_config.h
    sources += build_app_uds_host.C_SOURCES + [ROOT / "bootloader/src/core/sha256.h",
                                              ROOT / "bootloader/src/core/crc32.h",
                                              ROOT / "bootloader/include/bl_config.h", HERE / "build_app_uds_host.py"]
    digest = hostbuild.checksum(sorted(set(sources)), extra=(str(project), str(diag)))
    hostbuild.build_if_changed(out, digest, lambda: build_app_uds_host.build(out, project, diag=diag))
    return out


def ensure_dlls():
    app_dll(APP_DLL, EVAL_PROJECT)
    if not BL_DLL.exists():
        raise unittest.SkipTest(f"{BL_DLL} not built (README, Bootloader host tests)")


class AppUdsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_dlls()
        cls.app = HostApp(APP_DLL)
        cls.bl = HostBootloader(BL_DLL)

    def setUp(self):
        self.app.power_on()
        self.bl.dll.host_erase_all()
        self.bl.dll.host_set_costs(20, 1)

    def tearDown(self):
        self.assertEqual(self.app.dll.app_assert_count(), 0, "ETL assert in the stack")

    def client(self, bus=None):
        bus = bus or EcuBus(self.app, self.bl)
        client = canflash.make_client(bus)
        client.config["data_identifiers"].update(
            {
                # BL-22: identification identifiers carry numberOfModules in front of the text
                DID_APP_SOFTWARE_ID: canflash._IdentificationRest(),
                DID_ACTIVE_SESSION: "B",
                DID_RAM_TEST: "4s",
            }
        )
        client.open()
        self.addCleanup(bus.shutdown)
        self.addCleanup(client.close)
        return client, bus

    # raw single frames, to check the frame level (padding, DLC, no response)
    def request_raw(self, cid: int, payload: bytes, wait_ms: float = 60.0) -> list[tuple[int, bytes]]:
        frame = bytes([len(payload)]) + payload
        self.assertTrue(self.app.rx(cid, frame.ljust(8, bytes([PAD]))))
        out = []
        for _ in range(int(wait_ms)):
            self.app.poll_ms(1)
            while (f := self.app.tx()) is not None:
                out.append(f)
        return out

    def test_bus_ids_of_the_diagnostic_stack(self):
        """ARCH-07: the stack runs on the bus of uds.bus, here the second one (test profile: DIAG, id 3).

        The profile of this host build (test/host/include/busid, uds/udsBusConfiguration.h) has two CAN buses
        and puts the diagnostic stack on the second one; all other tests of this file run on that bus.
        """
        self.assertEqual(self.app.dll.app_transceiver_bus_id(), 3)      # busid::DIAG of the test profile
        self.assertEqual(self.app.dll.app_uds_bus_id(), 3)              # the DoCAN layer uses the same bus
        # DiagSystem is replaced by AppUdsHost in this build (lifecycle, ICanSystem): its bus selection is
        # only checked here and by the target build
        source = (ROOT / "ecu/uds/src/systems/DiagSystem.cpp").read_text(encoding="utf-8")
        self.assertIn("getCanTransceiver(::busid::NX_UDS_BUS)", source)

    def test_read_identifiers(self):
        client, _ = self.client()
        boot = client.read_data_by_identifier_first(canflash.DID_BOOT_SOFTWARE_ID)
        app = client.read_data_by_identifier_first(DID_APP_SOFTWARE_ID)
        self.assertEqual(boot, BL_VERSION)
        self.assertEqual(app, "nextbsw-app host")
        # BL-22: both identification identifiers report one module (Annex C.1)
        self.assertEqual((boot.modules, app.modules), (1, 1))
        self.assertEqual(client.read_data_by_identifier_first(DID_ACTIVE_SESSION), (1,))
        self.assertEqual(self.app.dll.app_router_locked(), 0)

    def test_read_identifier_length_on_frame_level(self):
        """Exact bytes of the F180 answer (udsoncan tolerates extra bytes after a fixed-size codec).

        BL-22: numberOfModules = 1 sits between the identifier and the text, so the payload is one byte
        longer than the text and the text starts one byte later. COM-33: the text fills its 32 bytes of diag.yaml
        (NUL behind it), so the length is that of the description (3 + 33), not the runtime length of the text.

        Without an open client: its ISO-TP thread would answer the first frame with a flow control and the
        consecutive frames would arrive instead of the one frame this test looks at.
        """
        self.assertEqual(EVAL_LENGTHS[0xF180], 33)
        self.assertLess(len(BL_VERSION), 32)            # the text is shorter: the fill bytes are on the wire
        frames = self.request_raw(PHYS, bytes([0x22, 0xF1, 0x80]))
        self.assertEqual(
            frames,
            [(RESP, bytes([0x10, 3 + 33, 0x62, 0xF1, 0x80, 0x01]) + BL_VERSION[:2].encode())])

    def test_identification_identifiers_carry_the_number_of_modules(self):
        """BL-22, ISO 14229-1 Annex C.1: F180 and F181 answer numberOfModules first, then the text.

        On the frame level, so that the count byte is seen where it is - the codec of canflash reads both
        forms and would hide a missing byte.
        """
        for did, text in ((0xF180, BL_VERSION), (0xF181, "nextbsw-app host")):
            with self.subTest(hex(did)):
                # both answers need a first frame; without a flow control the next request would find the
                # transmitter still busy, so each one starts on a freshly powered ECU
                self.app.power_on()
                first = self.request_raw(PHYS, bytes([0x22, 0xF1, did & 0xFF]))[0][1]
                # first frame: 0x1<length>, 0x62, did, numberOfModules, then the text
                self.assertEqual(first[:6], bytes([0x10, 3 + EVAL_LENGTHS[did], 0x62, 0xF1, did & 0xFF, 0x01]))
                self.assertEqual(first[6:8], text[:2].encode())

    def test_canflash_reads_both_forms_of_an_identification(self):
        """BL-22 transition: the tester tells the two forms apart and reports which one it got.

        The devices in the field answer 0xF180 with the bare text; this application answers with the
        numberOfModules byte in front of it. Both must read back as the same identification.
        """
        client, _ = self.client()
        new_form = client.read_data_by_identifier_first(canflash.DID_BOOT_SOFTWARE_ID)
        self.assertEqual((str(new_form), new_form.modules, new_form.annex_c1), (BL_VERSION, 1, True))
        old_form = canflash.parse_identification(BL_VERSION.encode())
        self.assertEqual((str(old_form), old_form.modules, old_form.annex_c1), (BL_VERSION, None, False))
        self.assertEqual(new_form, old_form)
        self.assertIn("before BL-22", canflash.identification_note(old_form))
        self.assertEqual(canflash.identification_note(new_form), "")

    def test_bootloader_version_from_built_bootloader(self):
        """F180 from the real bootloader image (0x410..0x8000, as scanned on the target)."""
        # BOOTLOADER_HEX, else the demo build as tools/test finds it (NX_BUILD_DIR, tools/test/build_dirs.py)
        hex_path = Path(os.environ.get("BOOTLOADER_HEX") or build_dirs.bootloader_hex(ROOT))
        if not hex_path.exists():
            self.skipTest(f"{hex_path} not built")
        from intelhex import IntelHex

        area = IntelHex(str(hex_path)).tobinstr(start=0x410, end=CFG["BL_APP_START"] - 1)
        self.assertEqual(area.count(BL_VERSION.encode() + b"\0"), 1)
        buf = (ctypes.c_uint8 * len(area)).from_buffer_copy(area)
        self.app.dll.app_power_on_area(buf, ctypes.c_uint32(len(area)))
        client, _ = self.client()
        self.assertEqual(client.read_data_by_identifier_first(canflash.DID_BOOT_SOFTWARE_ID), BL_VERSION)

    def test_bootloader_version_with_former_prefix(self):
        """PLAT-08: a bootloader flashed before the rename reports "S32K14x-BL 0.1.0"; the application finds it."""
        former = f"{canflash.BL_VERSION_PREFIX_FORMER} 0.1.0"
        self.assertEqual(former, "S32K14x-BL 0.1.0")
        self.assertNotEqual(BL_VERSION.split(" ")[0], canflash.BL_VERSION_PREFIX_FORMER)
        self.app.power_on(bl_version=former)
        client, _ = self.client()
        self.assertEqual(client.read_data_by_identifier_first(canflash.DID_BOOT_SOFTWARE_ID), former)

    def test_bootloader_version_of_another_product_is_not_reported(self):
        """PLAT-08: only the project's prefix and the former name count - a foreign string gives NRC 0x22."""
        # frame level: one client per power-on would leave several ISO-TP threads on the bus
        # PLAT-51: case-sensitive - the PLAT-08 spelling NeXtBSW-BL and case variants of both prefixes are foreign
        for foreign in ("ACME-BL 0.1.0", "nextbsw-b 0.1.0", "S32K14x-BLX 0.1.0", "NeXtBSW-BL 0.1.0",
                        "NEXTBSW-BL 0.1.0", "s32k14x-bl 0.1.0"):
            with self.subTest(foreign):
                self.app.power_on(bl_version=foreign)
                self.assertEqual(self.request_raw(PHYS, bytes([0x22, 0xF1, 0x80])),
                                 [(RESP, bytes([0x03, 0x7F, 0x22, 0x22]).ljust(8, bytes([PAD])))])
        # evidence that the frame check sees an answer with a string: the project's identification
        self.app.power_on(bl_version=BL_VERSION)
        self.assertEqual(self.request_raw(PHYS, bytes([0x22, 0xF1, 0x80]))[0][1][:2], bytes([0x10, 3 + EVAL_LENGTHS[0xF180]]))

    def test_project_prefix_wins_over_the_former_one(self):
        """Both strings in the area (former one first): the project's identification is reported."""
        area = bytearray(b"\xff" * 0x200)
        former = f"{canflash.BL_VERSION_PREFIX_FORMER} 0.1.0".encode()
        area[0x20 : 0x20 + len(former) + 1] = former + b"\0"
        area[0x100 : 0x100 + len(BL_VERSION) + 1] = BL_VERSION.encode() + b"\0"
        buf = (ctypes.c_uint8 * len(area)).from_buffer_copy(area)
        self.app.dll.app_power_on_area(buf, ctypes.c_uint32(len(area)))
        client, _ = self.client()
        self.assertEqual(client.read_data_by_identifier_first(canflash.DID_BOOT_SOFTWARE_ID), BL_VERSION)

    def test_bootloader_version_missing(self):
        self.app.power_on(bl_version=None)
        client, _ = self.client()
        with self.assertRaises(NegativeResponseException) as ctx:
            client.read_data_by_identifier_first(canflash.DID_BOOT_SOFTWARE_ID)
        self.assertEqual(ctx.exception.response.code, 0x22)

    def test_response_frames_padded(self):
        frames = self.request_raw(PHYS, bytes([0x22, 0xF1, 0x86]))
        self.assertEqual(frames, [(RESP, bytes([0x04, 0x62, 0xF1, 0x86, 0x01, PAD, PAD, PAD]))])

    def test_multi_frame_response_and_request_flow_control(self):
        # F181 needs a first frame: the tester's flow control releases the consecutive frames
        frames = self.request_raw(PHYS, bytes([0x22, 0xF1, 0x81]))
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0][1][:2], bytes([0x10, 3 + EVAL_LENGTHS[0xF181]]))
        # segmented request: the ECU answers with flow control (CTS, BS 0, STmin 0)
        request = bytes([0x2E, 0xFD, 0x00]) + bytes(range(1, 11))
        self.assertTrue(self.app.rx(PHYS, bytes([0x10, len(request)]) + request[:6]))
        self.app.poll_ms(5)
        self.assertEqual(self.app.tx(), (RESP, bytes([0x30, 0x00, 0x00, PAD, PAD, PAD, PAD, PAD])))

    def test_unknown_identifier_and_service(self):
        frames = self.request_raw(PHYS, bytes([0x22, 0x12, 0x34]))
        self.assertEqual(frames, [(RESP, bytes([0x03, 0x7F, 0x22, 0x31, PAD, PAD, PAD, PAD]))])
        # a service the application does not have at all (ReadMemoryByAddress)
        frames = self.request_raw(PHYS, bytes([0x23, 0x11, 0x00, 0x10]))
        self.assertEqual(frames, [(RESP, bytes([0x03, 0x7F, 0x23, 0x11, PAD, PAD, PAD, PAD]))])
        # COM-28: SecurityAccess exists since diag.yaml has a level, but only in the extended session (7.7)
        frames = self.request_raw(PHYS, bytes([0x27, 0x01]))
        self.assertEqual(frames, [(RESP, bytes([0x03, 0x7F, 0x27, 0x7F, PAD, PAD, PAD, PAD]))])

    def unlock(self, level: int = 0x01, key_bytes: int = 4):
        """27 requestSeed / sendKey with the host build's test algorithm (frame level)."""
        seed = self.payloads(PHYS, bytes([0x27, level]))[0]
        self.assertEqual(seed[:2], bytes([0x67, level]))
        key = host_key(seed[2:], level, key_bytes)
        self.assertEqual(self.payloads(PHYS, bytes([0x27, level + 1]) + key), [bytes([0x67, level + 1])])

    def test_write_ram_identifier_only_in_extended_session(self):
        client, _ = self.client()
        with self.assertRaises(NegativeResponseException) as ctx:
            client.write_data_by_identifier(DID_RAM_TEST, b"\x01\x02\x03\x04")
        self.assertEqual(ctx.exception.response.code, 0x31)  # 10.7: not writable in this session
        self.assertEqual(bytes(self.app.dll.app_ram_test_data()[:4]), bytes(4))

        client.change_session(udsoncan.services.DiagnosticSessionControl.Session.extendedDiagnosticSession)
        self.assertEqual(client.read_data_by_identifier_first(DID_ACTIVE_SESSION), (3,))
        # COM-28: diag.yaml RamTestValue has write_security Application
        with self.assertRaises(NegativeResponseException) as ctx:
            client.write_data_by_identifier(DID_RAM_TEST, b"\x01\x02\x03\x04")
        self.assertEqual(ctx.exception.response.code, 0x33)
        # the host build's test algorithm (canflash's own unlock is the bootloader's)
        seed = client.request_seed(0x01).service_data.seed
        client.send_key(0x02, host_key(seed, 0x01, 4))
        client.write_data_by_identifier(DID_RAM_TEST, b"\x01\x02\x03\x04")
        self.assertEqual(bytes(self.app.dll.app_ram_test_data()[:4]), b"\x01\x02\x03\x04")
        self.assertEqual(client.read_data_by_identifier_first(DID_RAM_TEST), (b"\x01\x02\x03\x04",))

        # wrong length on the frame level; the client has to go first, its ISO-TP thread would take the answer
        # off the bus (closing twice is harmless, the cleanup does it again)
        client.close()
        frames = self.request_raw(PHYS, bytes([0x2E, 0xFD, 0x00, 0x05]))
        self.assertEqual(frames[0][1][1:4], bytes([0x7F, 0x2E, 0x13]))
        self.assertEqual(bytes(self.app.dll.app_ram_test_data()[:4]), b"\x01\x02\x03\x04")

    def test_session_timeout_and_tester_present(self):
        self.assertEqual(self.request_raw(PHYS, bytes([0x10, 0x03]))[0][1][:3], bytes([0x06, 0x50, 0x03]))
        for _ in range(4):  # 12 s with tester present every 3 s
            self.app.poll_ms(3000)
            self.assertEqual(self.request_raw(PHYS, bytes([0x3E, 0x80])), [])  # suppressed response
        self.assertEqual(self.app.dll.app_session(), 3)
        self.app.poll_ms(5100)
        self.assertEqual(self.app.dll.app_session(), 1)

    def test_functional_tester_present(self):
        self.assertEqual(self.request_raw(FUNC, bytes([0x3E, 0x80])), [])
        frames = self.request_raw(FUNC, bytes([0x3E, 0x00]))
        self.assertEqual(frames, [(RESP, bytes([0x02, 0x7E, 0x00, PAD, PAD, PAD, PAD, PAD]))])
        # unsupported identifier on a functional request: no negative response (ISO 14229-1)
        self.assertEqual(self.request_raw(FUNC, bytes([0x22, 0x12, 0x34])), [])

    # ------------------------------------------------------- BL-21: preprogramming step (ISO 14229-1 Table 501)

    def payloads(self, cid: int, payload: bytes) -> list[bytes]:
        """request_raw() reduced to the UDS payloads, without the ISO-TP length byte and the filler."""
        return [f[1][1 : 1 + f[1][0]] for f in self.request_raw(cid, payload)]

    def state(self) -> tuple[int, int, int]:
        """What CommunicationControl and ControlDTCSetting asked the ECU to switch (tx, rx, DTC updates)."""
        return (self.app.dll.app_messages_tx(), self.app.dll.app_messages_rx(),
                self.app.dll.app_dtc_updates())

    def test_preprogramming_step_of_iso_14229_1_is_answered(self):
        """Table 501: 10 03, 85 02 and 28 03 01, the sequence a conforming client runs before it flashes.

        Before BL-21 the application answered 0x28 and 0x85 with serviceNotSupported, so a client that
        follows 16.3.1.1 stopped here instead of moving on to 10 02."""
        self.assertEqual(self.state(), (1, 1, 1))
        self.assertEqual(self.payloads(PHYS, bytes([0x10, 0x03]))[0][:2], bytes([0x50, 0x03]))
        self.assertEqual(self.payloads(PHYS, bytes([0x85, 0x02])), [bytes([0xC5, 0x02])])
        self.assertEqual(self.payloads(PHYS, bytes([0x28, 0x03, 0x01])), [bytes([0x68, 0x03])])
        # unlike the boot software the application really switches: messages off, DTC updates frozen
        self.assertEqual(self.state(), (0, 0, 0))
        # and the handover to the bootloader still works from there
        self.assertEqual(self.payloads(PHYS, bytes([0x10, 0x02]))[0][:2], bytes([0x50, 0x02]))
        self.assertEqual(self.app.dll.app_boot_request(), 1)

    def test_communication_control_switches_the_application_messages(self):
        """9.5: each controlType reaches the ECU as the pair of switches it means."""
        self.assertEqual(self.payloads(PHYS, bytes([0x10, 0x03]))[0][:2], bytes([0x50, 0x03]))
        for sub, expected in ((0x03, (0, 0)), (0x01, (0, 1)), (0x02, (1, 0)), (0x00, (1, 1))):
            with self.subTest(controlType=hex(sub)):
                self.assertEqual(self.payloads(PHYS, bytes([0x28, sub, 0x01])), [bytes([0x68, sub])])
                self.assertEqual(self.state()[:2], expected)
        # "all message classes" (0x03) works like "normal" on an ECU without network management messages
        self.assertEqual(self.payloads(PHYS, bytes([0x28, 0x03, 0x03])), [bytes([0x68, 0x03])])
        self.assertEqual(self.state()[:2], (0, 0))
        # repeating the request is answered positively (9.5.1) but must not notify the ECU again
        before = self.app.dll.app_communication_changes()
        self.assertEqual(self.payloads(PHYS, bytes([0x28, 0x03, 0x03])), [bytes([0x68, 0x03])])
        self.assertEqual(self.app.dll.app_communication_changes(), before)
        # network management messages only: answered, but this ECU has none - the application keeps quiet
        self.assertEqual(self.payloads(PHYS, bytes([0x28, 0x00, 0x02])), [bytes([0x68, 0x00])])
        self.assertEqual(self.state()[:2], (0, 0), "an NM request must not re-enable application messages")

    def test_control_dtc_setting_freezes_the_status_updates(self):
        """9.8: on and off are answered and carried; a repeated request changes nothing."""
        self.assertEqual(self.payloads(PHYS, bytes([0x10, 0x03]))[0][:2], bytes([0x50, 0x03]))
        self.assertEqual(self.payloads(PHYS, bytes([0x85, 0x02])), [bytes([0xC5, 0x02])])
        self.assertEqual(self.app.dll.app_dtc_updates(), 0)
        before = self.app.dll.app_dtc_changes()
        self.assertEqual(self.payloads(PHYS, bytes([0x85, 0x02])), [bytes([0xC5, 0x02])])
        self.assertEqual(self.app.dll.app_dtc_changes(), before)
        self.assertEqual(self.payloads(PHYS, bytes([0x85, 0x01])), [bytes([0xC5, 0x01])])
        self.assertEqual(self.app.dll.app_dtc_updates(), 1)
        for sub in (0x00, 0x03, 0x40, 0x7F):
            with self.subTest(unsupported=hex(sub)):
                self.assertEqual(self.payloads(PHYS, bytes([0x85, sub])), [bytes([0x7F, 0x85, 0x12])])
        # a DTCSettingControlOptionRecord is not supported: its very presence is a length error
        self.assertEqual(self.payloads(PHYS, bytes([0x85, 0x02, 0xAA])), [bytes([0x7F, 0x85, 0x13])])

    def test_preprogramming_services_need_a_non_default_session(self):
        """Both are bound to the extendedDiagnosticSession the client opens in step (2) of 16.3.1.1."""
        self.assertEqual(self.app.dll.app_session(), 1)
        self.assertEqual(self.payloads(PHYS, bytes([0x28, 0x03, 0x01])), [bytes([0x7F, 0x28, 0x7F])])
        self.assertEqual(self.payloads(PHYS, bytes([0x85, 0x02])), [bytes([0x7F, 0x85, 0x7F])])
        self.assertEqual(self.state(), (1, 1, 1), "a refused request must not switch anything")

    def test_session_end_restores_communication_and_dtc_updates(self):
        """9.8.1: the frozen state belongs to the session - the S3 timeout has to end it too.

        Without this a client that walks away after step (7) would leave the ECU mute and blind on the
        bus until the next power cycle."""
        self.assertEqual(self.payloads(PHYS, bytes([0x10, 0x03]))[0][:2], bytes([0x50, 0x03]))
        self.assertEqual(self.payloads(PHYS, bytes([0x85, 0x02])), [bytes([0xC5, 0x02])])
        self.assertEqual(self.payloads(PHYS, bytes([0x28, 0x03, 0x01])), [bytes([0x68, 0x03])])
        self.assertEqual(self.state(), (0, 0, 0))
        self.app.poll_ms(5100)                       # S3 without a tester present
        self.assertEqual(self.app.dll.app_session(), 1)
        self.assertEqual(self.state(), (1, 1, 1))
        # the same on an explicit return to the default session (post-programming step, 16.3.1.3)
        self.assertEqual(self.payloads(PHYS, bytes([0x10, 0x03]))[0][:2], bytes([0x50, 0x03]))
        self.assertEqual(self.payloads(PHYS, bytes([0x28, 0x03, 0x01])), [bytes([0x68, 0x03])])
        self.assertEqual(self.state()[:2], (0, 0))
        self.assertEqual(self.payloads(PHYS, bytes([0x10, 0x01]))[0][:2], bytes([0x50, 0x01]))
        self.assertEqual(self.state(), (1, 1, 1))

    def test_preprogramming_step_works_functionally(self):
        """16.3.1.1: steps (5) and (7) may be addressed to every server with one request."""
        self.assertEqual(self.payloads(FUNC, bytes([0x10, 0x03]))[0][:2], bytes([0x50, 0x03]))
        self.assertEqual(self.payloads(FUNC, bytes([0x85, 0x02])), [bytes([0xC5, 0x02])])
        self.assertEqual(self.payloads(FUNC, bytes([0x28, 0x03, 0x01])), [bytes([0x68, 0x03])])
        self.assertEqual(self.state(), (0, 0, 0))
        # suppressPosRspMsgIndicationBit, the usual way of addressing a whole network
        self.assertEqual(self.payloads(FUNC, bytes([0x85, 0x81])), [])
        self.assertEqual(self.app.dll.app_dtc_updates(), 1)
        self.assertEqual(self.payloads(FUNC, bytes([0x28, 0x80, 0x01])), [])
        self.assertEqual(self.state()[:2], (1, 1))

    def test_ecu_reset(self):
        client, _ = self.client()
        client.ecu_reset(udsoncan.services.ECUReset.ResetType.hardReset)
        self.app.poll_ms(10)
        self.assertEqual(self.app.dll.app_reset_requested(), 1)
        self.assertEqual(self.app.dll.app_boot_request(), 0)

    def test_programming_session_sets_boot_request_after_response(self):
        self.assertTrue(self.app.rx(PHYS, bytes([0x02, 0x10, 0x02]).ljust(8, bytes([PAD]))))
        self.assertEqual(self.app.dll.app_boot_request(), 0, "boot request before the response was sent")
        frames = []
        for _ in range(20):
            self.app.poll_ms(1)
            while (f := self.app.tx()) is not None:
                frames.append(f)
        self.assertEqual(frames, [(RESP, bytes([0x06, 0x50, 0x02, 0x00, 0x32, 0x01, 0xF4, PAD]))])
        self.assertEqual(self.app.dll.app_boot_request(), 1)
        self.assertEqual(self.app.dll.app_reset_requested(), 0)

    def test_canflash_flash_without_wait(self):
        """canflash flash (without --wait) against the running application: 10 02 in the application,
        then security access, erase, download, check and reset in the bootloader."""
        old = make_app_image(6000, seed=3)
        self.bl.write(CFG["BL_APP_START"], old)
        self.bl.write(CFG["BL_APP_RECORD_ADDR"], canflash.app_record(old))

        data = make_app_image(20001, seed=11)
        client, bus = self.client()
        canflash.flash_application(client, data, log=lambda *a, **k: None)  # same calls as cmd_flash
        client.ecu_reset(udsoncan.services.ECUReset.ResetType.hardReset)
        time.sleep(0.05)
        self.bl.dll.host_poll(ctypes.c_uint32(0))

        self.assertTrue(bus.in_bootloader)
        self.assertEqual(bus.log[0], ("rx", PHYS, bytes([0x02, 0x10, 0x02]).ljust(8, bytes([PAD]))))
        self.assertEqual(bus.log[1][1:], (RESP, bytes([0x06, 0x50, 0x02, 0x00, 0x32, 0x01, 0xF4, PAD])))
        self.assertEqual(self.bl.dll.host_reset_requested(), 1)
        self.assertEqual(self.bl.read(CFG["BL_APP_START"], len(data)), data)
        self.assertEqual(self.bl.read(CFG["BL_APP_RECORD_ADDR"], 24), canflash.app_record(data))

        self.bl.dll.host_power_on(0)
        for _ in range(CFG["BL_STARTUP_WINDOW_MS"] + 10):
            self.bl.dll.host_poll(ctypes.c_uint32(1))
        self.assertEqual(self.bl.dll.host_jump_address(), CFG["BL_APP_START"])

    def test_canflash_runs_the_full_programming_sequence(self):
        """BL-21: canflash flash walks the whole programming phase #1 of ISO 14229-1 16.3.1.

        Preprogramming step against the running application (10 03, 85 02, 28 03), programming step in the
        bootloader (10 02, 27, 2E F184, 31 FF00, 34/36/37, 31 FF01) and post-programming step (11 01).
        Checked on the frames that really went over the bus, not on the tool's own log."""
        old = make_app_image(6000, seed=3)
        self.bl.write(CFG["BL_APP_START"], old)
        self.bl.write(CFG["BL_APP_RECORD_ADDR"], canflash.app_record(old))
        data = make_app_image(9000, seed=17)

        bus = EcuBus(self.app, self.bl)
        self.addCleanup(bus.shutdown)
        canflash.flash_over_bus(bus, data, log=lambda *a, **k: None)
        time.sleep(0.05)
        self.bl.dll.host_poll(ctypes.c_uint32(0))

        def service_of(frame: bytes):
            """(SID, first parameter byte) of a request frame, None for a continuation of one."""
            kind = frame[0] & 0xF0
            if kind == 0x00:                        # single frame: length byte, then the request
                body = frame[1 : 1 + frame[0]]
            elif kind == 0x10:                      # first frame of a segmented request (erase, download)
                body = frame[2:]
            else:
                return None                         # consecutive or flow control frame
            return (body[0], body[1] if len(body) > 1 else None) if body else None

        sids = [s for s in (service_of(f[2]) for f in bus.log if f[0] == "rx") if s is not None]
        for step, expected in (("10 03 extendedDiagnosticSession", (0x10, 0x03)),
                               ("85 02 ControlDTCSetting off", (0x85, 0x02)),
                               ("28 03 CommunicationControl disable", (0x28, 0x03)),
                               ("10 02 programmingSession", (0x10, 0x02)),
                               ("27 01 requestSeed", (0x27, 0x01)),
                               ("31 01 routineControl", (0x31, 0x01)),
                               ("37 requestTransferExit", (0x37, None)),
                               ("11 01 hardReset", (0x11, 0x01))):
            with self.subTest(step=step):
                self.assertIn(expected, sids)
        # order: the preprogramming step comes before the handover, the reset last
        self.assertLess(sids.index((0x85, 0x02)), sids.index((0x10, 0x02)))
        self.assertLess(sids.index((0x28, 0x03)), sids.index((0x10, 0x02)))
        self.assertEqual(sids[-1], (0x11, 0x01))
        # the preprogramming step reached the application, not the bootloader
        self.assertEqual(self.app.dll.app_dtc_updates(), 0)
        self.assertEqual((self.app.dll.app_messages_tx(), self.app.dll.app_messages_rx()), (0, 0))
        # and the programming step did its work
        self.assertEqual(self.bl.read(CFG["BL_APP_START"], len(data)), data)
        self.assertEqual(self.bl.read(CFG["BL_APP_RECORD_ADDR"], 24), canflash.app_record(data))
        self.assertEqual(self.bl.dll.host_reset_requested(), 1)

    def test_flash_survives_a_server_that_refuses_the_preprogramming_step(self):
        """A bootloader or application flashed before BL-21 answers 0x28/0x85 with serviceNotSupported.

        That means the network was not quiesced, not that the download must fail - canflash reports the
        refusal and flashes anyway, otherwise BL-21 would make every ECU in the field unflashable."""
        self.bl.dll.host_power_on(1)          # only the bootloader answers, the application is skipped
        bus = EcuBus(self.app, self.bl)
        bus.in_bootloader = True
        self.addCleanup(bus.shutdown)
        refused = canflash.pre_programming_step(bus, log=lambda *a, **k: None)
        self.assertEqual(refused, [], "the BL-21 bootloader accepts all three steps")

        # a server that knows none of them: every step is reported, nothing raises
        class DeafBus(can.BusABC):
            def __init__(self):
                super().__init__(channel="deaf")

            def send(self, msg, timeout=None):
                pass

            def _recv_internal(self, timeout):
                return None, False

        notes, made = [], canflash.make_client

        def impatient_client(bus):
            client = made(bus)
            client.set_config("p2_timeout", 0.05)   # nobody is going to answer, do not wait for it
            return client

        canflash.make_client = impatient_client
        deaf = DeafBus()
        try:
            refused = canflash.pre_programming_step(deaf, log=notes.append)
        finally:
            canflash.make_client = made
            deaf.shutdown()
        self.assertEqual(len(refused), 3, notes)
        self.assertTrue(all("not accepted" in n for n in notes), notes)

    def test_diagnostic_addresses_come_from_the_project(self):
        """COM-11: ids, filler byte, flow control and the version prefix of the eval board project."""
        self.assertEqual((PHYS, FUNC, RESP, PAD), (0x7E0, 0x7DF, 0x7E8, 0xCC))
        # version number from bl_config.h (BL-10: the tests keep no copy of it)
        self.assertEqual(BL_VERSION, f"nextbsw-bl {canflash.BL_VERSION_NUMBER}")
        # AppDiagConfig.h has no hand written value left: everything comes from the generated bl_diag.h
        config = (ROOT / "ecu/uds/include/uds/app/AppDiagConfig.h").read_text(encoding="utf-8")
        for name in ("BL_CAN_ID_REQUEST_PHYS", "BL_CAN_ID_REQUEST_FUNC", "BL_CAN_ID_RESPONSE", "BL_ISOTP_PADDING",
                     "BL_ISOTP_BLOCK_SIZE", "BL_ISOTP_STMIN_MS", "BL_ISOTP_N_CR_MS", "BL_ISOTP_N_BS_MS",
                     "NX_DIAG_TESTER_ADDRESS", "NX_DIAG_ECU_ADDRESS", "NX_DIAG_BL_SCAN_START",
                     "NX_DIAG_BL_SCAN_END", "BL_VERSION_PREFIX"):
            self.assertIn(name, config)
        self.assertNotRegex(config, r"=\s*0x[0-9A-Fa-f]+U")      # no numeric literal as a value

    def test_requests_on_other_can_ids_are_ignored(self):
        for foreign in (0x6F0, 0x6DF, 0x700, PHYS + 1, RESP):
            with self.subTest(id=hex(foreign)):
                self.assertEqual(self.request_raw(foreign, bytes([0x3E, 0x00])), [])

    def test_buffers_released_after_traffic(self):
        client, _ = self.client()
        for _ in range(20):
            client.read_data_by_identifier_first(DID_APP_SOFTWARE_ID)
            client.tester_present()
        self.request_raw(FUNC, bytes([0x3E, 0x00]))
        self.assertEqual(self.app.dll.app_router_locked(), 0)
        self.assertEqual(self.app.dll.app_router_failures(), 0)
        self.assertEqual(self.app.dll.app_shutdown(), 1)

    # ---------------------------------------------- COM-28: jobs generated from projects/<project>/diag.yaml

    def test_hand_written_identifiers_answer_the_same_bytes(self):
        """Before/after with udsoncan: the identifiers AppDataIdentifiers served by hand answer byte-equal.

        did_responses_before_com28.json was recorded on dev 19df315 with the hand written jobs
        (did_equivalence.py record). Every "same" row must match exactly; a "changed" row must give the answer
        the description or ISO 14229-1 demands; "setup" rows (27) did not exist before."""
        golden = json.loads(did_equivalence.GOLDEN.read_text(encoding="utf-8"))
        self.assertIn("19df315", golden["recorded_on"])
        client, _ = self.client()
        answers = did_equivalence.run(client, key_of=lambda seed: host_key(seed, 0x01, 4))
        same = changed = 0
        for (kind, text, why), row, answer in zip(did_equivalence.EXCHANGES, golden["rows"], answers):
            self.assertEqual((row["kind"], row["request"]), (kind, text), "golden file out of step")
            with self.subTest(request=text):
                if kind == "same":
                    self.assertEqual(answer, row["response"])
                    same += 1
                elif kind == "changed":
                    self.assertNotEqual(answer, row["response"])
                    self.assertEqual(answer, why[1], why[0])
                    changed += 1
                else:
                    self.assertTrue(answer.startswith("67"), answer)
        self.assertEqual((same, changed), (18, 5))       # COM-33: F180/F181 filled up to their described length

    def test_several_identifiers_in_one_request(self):
        """10.2: the response lists every supported identifier with its record, in the order requested."""
        self.app.stub_set("did.MotorNtcFiltered.motorNtcFiltered", 1234)
        client, _ = self.client()                       # segmented requests and responses: through udsoncan

        def send(payload: bytes) -> bytes:
            try:
                return bytes(client.send_request(udsoncan.Request.from_payload(payload)).original_payload)
            except NegativeResponseException as e:
                return bytes(e.response.original_payload)

        self.assertEqual(send(bytes([0x22, 0xF1, 0x86, 0x01, 0x10])),
                         bytes([0x62, 0xF1, 0x86, 0x01, 0x01, 0x10, 0x04, 0xD2]))
        self.assertEqual(send(bytes([0x22, 0xF1, 0x81, 0xF1, 0x86]))[:5], bytes([0x62, 0xF1, 0x81, 0x01, 0x6E]))
        # as many identifiers as the description can read (6 since 0xF18C, 7 since 0x0120 of COM-34, 8 since 0xF1F1 of PLAT-79,
        # 10 since 0xF1F2 of PLAT-93 and 0x0130 of NVM-05, 11 since 0x0140 of SYS-196, a
        # repeated one counts again) - and one more: incorrectMessageLength (maximum length check of 10.2)
        readable = sum(1 for did in EVAL_DIAG.data_identifiers if did.read)
        self.assertEqual(readable, 11)
        self.assertEqual(send(bytes([0x22]) + bytes([0xF1, 0x86]) * readable),
                         bytes([0x62]) + bytes([0xF1, 0x86, 1]) * readable)
        self.assertEqual(send(bytes([0x22]) + bytes([0xF1, 0x86]) * (readable + 1)), bytes([0x7F, 0x22, 0x13]))

    def test_several_identifiers_split_by_the_description(self):
        """COM-33 (board finding 2026-09-21, recording 24): F180, F181, F186, FD00 and 0110 in one request - the
        records stand back to back without a length, and the tester splits them by diag.yaml alone.

        The texts are shorter than their 32 bytes; with their runtime length (before COM-33) the split by the
        description ends at F181 already (checked at the end)."""
        self.app.stub_set("did.MotorNtcFiltered.motorNtcFiltered", 1674)
        client, _ = self.client()
        request = bytes([0x22, 0xF1, 0x80, 0xF1, 0x81, 0xF1, 0x86, 0xFD, 0x00, 0x01, 0x10])
        response = bytes(client.send_request(udsoncan.Request.from_payload(request)).original_payload)
        self.assertEqual(len(response), 1 + 5 * 2 + 33 + 33 + 1 + 4 + 2)
        records = diagdesc.split_read_response(EVAL_DIAG, response)
        self.assertEqual([r.identifier for r in records], [0xF180, 0xF181, 0xF186, 0xFD00, 0x0110])
        boot, app, session, ram, ntc = records
        self.assertEqual((boot.elements["numberOfModules"], boot.text("bootSoftwareIdentification")),
                         (b"\x01", BL_VERSION))
        self.assertEqual(boot.elements["bootSoftwareIdentification"], padded(BL_VERSION, 0xF180))
        self.assertEqual(app.text("applicationSoftwareIdentification"), "nextbsw-app host")
        self.assertEqual(app.elements["applicationSoftwareIdentification"], padded("nextbsw-app host", 0xF181))
        self.assertEqual(session.elements["activeSession"], b"\x01")
        self.assertEqual(len(ram.elements["ramTestValue"]), 4)     # value depends on earlier writes of the DLL
        self.assertEqual(ntc.elements["motorNtcFiltered"], (1674).to_bytes(2, "big"))
        # the same request answered with texts of their runtime length cannot be split by the description
        unpadded = response.replace(padded(BL_VERSION, 0xF180), BL_VERSION.encode()).replace(
            padded("nextbsw-app host", 0xF181), b"nextbsw-app host")
        with self.assertRaises(ValueError):
            diagdesc.split_read_response(EVAL_DIAG, unpadded)

    def test_identifier_from_a_port_of_the_rte(self):
        """0x0110 reads DemoSwc.MotorNtcFiltered_mV through rte::diag (here the host stand-in), big endian."""
        for value, wire in ((0, b"\x00\x00"), (5000, b"\x13\x88"), (0xFFFF, b"\xff\xff")):
            with self.subTest(value=value):
                self.app.stub_set("did.MotorNtcFiltered.motorNtcFiltered", value)
                self.assertEqual(self.payloads(PHYS, bytes([0x22, 0x01, 0x10])), [b"\x62\x01\x10" + wire])
        # read only: 2E is refused like an unknown identifier
        self.assertEqual(self.payloads(PHYS, bytes([0x10, 0x03]))[0][:2], bytes([0x50, 0x03]))
        self.assertEqual(self.payloads(PHYS, bytes([0x2E, 0x01, 0x10, 0x00, 0x01])), [bytes([0x7F, 0x2E, 0x31])])

    def test_the_fault_memory_reads_the_identifiers_like_22(self):
        """NVM-03: the snapshot identifiers of the fault memory come from the read functions of 22 (DiagDidReader,
        registered in dem::registry by the stack), exactly as long as described, without session check."""
        buf = (ctypes.c_uint8 * 32)()
        self.assertEqual(self.app.dll.app_snapshot_did(0xF186, buf, 1), 1)
        self.assertEqual(buf[0], 0x01)                                     # default session
        self.app.stub_set("did.MotorNtcFiltered.motorNtcFiltered", 5000)
        self.assertEqual(self.app.dll.app_snapshot_did(0x0110, buf, 2), 1)
        self.assertEqual(bytes(buf[:2]), bytes([0x13, 0x88]))       # the same bytes as 62 01 10
        self.assertEqual(self.app.dll.app_snapshot_did(0x0110, buf, 3), 0)  # other length than described
        self.assertEqual(self.app.dll.app_snapshot_did(0x1234, buf, 2), 0)  # not in the description
        self.assertEqual(self.payloads(PHYS, bytes([0x10, 0x03]))[0][:2], bytes([0x50, 0x03]))
        self.assertEqual(self.app.dll.app_snapshot_did(0xF186, buf, 1), 1)
        self.assertEqual(buf[0], 0x03)                                     # the session at the moment of the read

    def set_ecu_conditions(self):
        """Values of the basic software behind 0x0120 EcuConditions (COM-34), as the RTE would hand them over;
        returns a function that sends one request through udsoncan (segmented) and gives the response payload."""
        self.app.dll.diag_stub_available.argtypes = [ctypes.c_int]
        self.app.dll.diag_stub_available(1)
        self.addCleanup(self.app.dll.diag_stub_available, 1)
        for element, value in (("ecuState", 1), ("supply", 12345), ("supplyQlf", 1), ("resetReason", 2),
                                ("uptime", 0x01020304), ("tsVoltage", 2500)):
            self.app.stub_set(f"did.EcuConditions.{element}", value)
        client, _ = self.client()

        def send(payload: bytes) -> bytes:
            try:
                return bytes(client.send_request(udsoncan.Request.from_payload(payload)).original_payload)
            except NegativeResponseException as e:
                return bytes(e.response.original_payload)
        return send

    def test_identifier_from_values_of_the_basic_software(self):
        """COM-34: 0x0120 EcuConditions of the demo reads ecu.state, bsw.power.*, bsw.reset.reason, bsw.time.ms and
        adc.mv.SENSE_TSVOLTAGE through rte::diag (host stand-in) - big endian in the order of diag.yaml, readable
        in the default and the extended session, never writable, NRC 0x22 while a value cannot be read."""
        send = self.set_ecu_conditions()
        expected = bytes([0x62, 0x01, 0x20, 0x01, 0x30, 0x39, 0x01, 0x02, 0x01, 0x02, 0x03, 0x04, 0x09, 0xC4])
        self.assertEqual(EVAL_LENGTHS[0x0120], 11)
        self.assertEqual(send(bytes([0x22, 0x01, 0x20])), expected)
        record = diagdesc.split_read_response(EVAL_DIAG, expected)[0]
        self.assertEqual((record.identifier, record.elements["supply"], record.elements["uptime"]),
                         (0x0120, (12345).to_bytes(2, "big"), bytes([1, 2, 3, 4])))
        self.app.dll.diag_stub_available(0)                                # e.g. a failed ADC conversion
        self.assertEqual(send(bytes([0x22, 0x01, 0x20])), bytes([0x7F, 0x22, 0x22]))
        self.app.dll.diag_stub_available(1)
        self.assertEqual(send(bytes([0x10, 0x03]))[:2], bytes([0x50, 0x03]))
        self.assertEqual(send(bytes([0x22, 0x01, 0x20])), expected)
        self.assertEqual(send(bytes([0x2E, 0x01, 0x20]) + bytes(11)), bytes([0x7F, 0x2E, 0x31]))

    def test_ecu_conditions_split_next_to_the_session(self):
        """Board checklist COM-32 item 3 on the host: 22 01 20 F1 86 in one request, split by diag.yaml."""
        send = self.set_ecu_conditions()
        records = diagdesc.split_read_response(EVAL_DIAG, send(bytes([0x22, 0x01, 0x20, 0xF1, 0x86])))
        self.assertEqual([(r.identifier, len(r.data)) for r in records], [(0x0120, 11), (0xF186, 1)])
        self.assertEqual(records[0].elements["ecuState"], b"")
        self.assertEqual(records[1].elements["activeSession"], b"")

    def test_the_fault_memory_reads_the_bsw_identifier_like_22(self):
        """COM-34: 0x0120 is a snapshot identifier of 0x904001; the fault memory reads it through the job of 22
        and gets no record while a value cannot be read (the entry then keeps 0xFF, NVM-03)."""
        send = self.set_ecu_conditions()
        dtc = next(d for d in EVAL_DIAG.dtcs if d.number == 0x904001)
        self.assertIn("EcuConditions", dtc.snapshots[0].dids)
        buf = (ctypes.c_uint8 * 32)()
        self.assertEqual(self.app.dll.app_snapshot_did(0x0120, buf, 11), 1)
        self.assertEqual(bytes(buf[:11]), send(bytes([0x22, 0x01, 0x20]))[3:])
        self.assertEqual(self.app.dll.app_snapshot_did(0x0120, buf, 10), 0)      # other length than described
        self.app.dll.diag_stub_available(0)
        self.assertEqual(self.app.dll.app_snapshot_did(0x0120, buf, 11), 0)

    def test_session_timing_comes_from_the_description(self):
        """P2 50 ms and P2* 5000 ms of diag.yaml sessions in the 10 03 response (P2* in units of 10 ms)."""
        self.assertEqual(self.payloads(PHYS, bytes([0x10, 0x03])), [bytes([0x50, 0x03, 0x00, 0x32, 0x01, 0xF4])])
        config = (ROOT / "ecu/uds/configuration/include/uds/UdsConfig.h").read_text(encoding="utf-8")
        self.assertIn("DEFAULT_DIAG_RESPONSE_TIME        = NX_DIAG_P2_MS;", config)

    def test_security_access(self):
        """9.4 with the level Application of diag.yaml (seed and key 4 bytes, 3 attempts, 10 s delay)."""
        self.assertEqual(self.payloads(PHYS, bytes([0x10, 0x03]))[0][:2], bytes([0x50, 0x03]))
        # sendKey without a seed first, unknown level, level of a sendKey, wrong lengths
        self.assertEqual(self.payloads(PHYS, bytes([0x27, 0x02, 1, 2, 3, 4])), [bytes([0x7F, 0x27, 0x24])])
        self.assertEqual(self.payloads(PHYS, bytes([0x27, 0x03])), [bytes([0x7F, 0x27, 0x12])])
        self.assertEqual(self.payloads(PHYS, bytes([0x27, 0x00])), [bytes([0x7F, 0x27, 0x12])])
        self.assertEqual(self.payloads(PHYS, bytes([0x27, 0x01, 0x00])), [bytes([0x7F, 0x27, 0x13])])
        self.assertEqual(self.payloads(PHYS, bytes([0x27])), [bytes([0x7F, 0x27, 0x13])])
        # a key of the wrong length after a valid seed; the seed is used up by it
        seed = self.payloads(PHYS, bytes([0x27, 0x01]))[0]
        self.assertEqual(self.payloads(PHYS, bytes([0x27, 0x02, 1, 2, 3])), [bytes([0x7F, 0x27, 0x13])])
        key = host_key(seed[2:], 0x01, 4)
        self.assertEqual(self.payloads(PHYS, bytes([0x27, 0x02]) + key), [bytes([0x67, 0x02])])
        self.assertEqual(self.app.dll.app_security_unlocked(0), 1)
        # already unlocked: an all zero seed, and no key is expected
        self.assertEqual(self.payloads(PHYS, bytes([0x27, 0x01])), [bytes([0x67, 0x01, 0, 0, 0, 0])])
        self.assertEqual(self.payloads(PHYS, bytes([0x27, 0x02]) + key), [bytes([0x7F, 0x27, 0x24])])
        # a change of the session locks it again (9.2), also into the same session
        self.assertEqual(self.payloads(PHYS, bytes([0x10, 0x03]))[0][:2], bytes([0x50, 0x03]))
        self.assertEqual(self.app.dll.app_security_unlocked(0), 0)
        self.assertNotEqual(self.payloads(PHYS, bytes([0x27, 0x01]))[0][2:], bytes(4))

    def test_wrong_keys_start_the_delay(self):
        """attempts: 3 -> invalidKey, invalidKey, exceededNumberOfAttempts; then 0x37 until delay_ms (10 s)."""
        self.assertEqual(self.payloads(PHYS, bytes([0x10, 0x03]))[0][:2], bytes([0x50, 0x03]))
        for expected in (0x35, 0x35, 0x36):
            seed = self.payloads(PHYS, bytes([0x27, 0x01]))[0]
            self.assertEqual(seed[:2], bytes([0x67, 0x01]))
            wrong = bytes(b ^ 0xFF for b in host_key(seed[2:], 0x01, 4))
            self.assertEqual(self.payloads(PHYS, bytes([0x27, 0x02]) + wrong), [bytes([0x7F, 0x27, expected])])
        self.assertEqual(self.payloads(PHYS, bytes([0x27, 0x01])), [bytes([0x7F, 0x27, 0x37])])
        for _ in range(3):                           # keep the extended session alive while the delay runs
            self.app.poll_ms(3000)
            self.assertEqual(self.request_raw(PHYS, bytes([0x3E, 0x80])), [])
        self.assertEqual(self.payloads(PHYS, bytes([0x27, 0x01])), [bytes([0x7F, 0x27, 0x37])])
        self.app.poll_ms(1100)
        self.assertEqual(self.payloads(PHYS, bytes([0x27, 0x01]))[0][:2], bytes([0x67, 0x01]))

    def test_security_access_without_an_algorithm(self):
        """Without a key algorithm (none on the target yet) no level can be unlocked: 0x22 (9.4)."""
        self.app.dll.app_use_security_algorithm(0)
        self.addCleanup(self.app.dll.app_use_security_algorithm, 1)
        self.app.power_on()
        self.assertEqual(self.payloads(PHYS, bytes([0x10, 0x03]))[0][:2], bytes([0x50, 0x03]))
        self.assertEqual(self.payloads(PHYS, bytes([0x27, 0x01])), [bytes([0x7F, 0x27, 0x22])])
        self.assertEqual(self.payloads(PHYS, bytes([0x2E, 0xFD, 0x00, 1, 2, 3, 4])), [bytes([0x7F, 0x2E, 0x33])])

    def test_routine_control_sequence(self):
        """13.2 with LedSelfTest (0x0301): session, security, sub-function, length, range, sequence (0x24)."""
        start = bytes([0x31, 0x01, 0x03, 0x01])
        self.assertEqual(self.payloads(PHYS, start + b"\x05"), [bytes([0x7F, 0x31, 0x31])])   # default session
        self.assertEqual(self.payloads(PHYS, bytes([0x10, 0x03]))[0][:2], bytes([0x50, 0x03]))
        self.assertEqual(self.payloads(PHYS, start + b"\x05"), [bytes([0x7F, 0x31, 0x33])])   # locked
        self.unlock()
        self.assertEqual(self.payloads(PHYS, bytes([0x31, 0x01, 0x03])), [bytes([0x7F, 0x31, 0x13])])
        self.assertEqual(self.payloads(PHYS, bytes([0x31, 0x01, 0x12, 0x34, 0x05])), [bytes([0x7F, 0x31, 0x31])])
        self.assertEqual(self.payloads(PHYS, bytes([0x31, 0x04, 0x03, 0x01])), [bytes([0x7F, 0x31, 0x12])])
        self.assertEqual(self.payloads(PHYS, start), [bytes([0x7F, 0x31, 0x13])])            # request missing
        self.assertEqual(self.payloads(PHYS, start + b"\x00"), [bytes([0x7F, 0x31, 0x31])])  # below min 1
        self.assertEqual(self.payloads(PHYS, start + b"\x33"), [bytes([0x7F, 0x31, 0x31])])  # above max 50
        stop, results = bytes([0x31, 0x02, 0x03, 0x01]), bytes([0x31, 0x03, 0x03, 0x01])
        self.assertEqual(self.payloads(PHYS, stop), [bytes([0x7F, 0x31, 0x24])])
        self.assertEqual(self.payloads(PHYS, results), [bytes([0x7F, 0x31, 0x24])])
        # start: the request reaches the RTE, the response comes from it
        self.app.stub_set("routine.LedSelfTest.start.accepted", 1)
        self.assertEqual(self.payloads(PHYS, start + b"\x05"), [bytes([0x71, 0x01, 0x03, 0x01, 0x01])])
        self.assertEqual(self.app.stub_get("routine.LedSelfTest.start.durationCycles"), 5)
        self.assertEqual(self.app.stub_get("routine.LedSelfTest.start"), 1)
        self.assertEqual(self.app.dll.app_routine_running(0), 1)
        self.assertEqual(self.payloads(PHYS, start + b"\x05"), [bytes([0x7F, 0x31, 0x24])])  # still running
        self.app.stub_set("routine.LedSelfTest.results.state", 2)
        self.assertEqual(self.payloads(PHYS, results), [bytes([0x71, 0x03, 0x03, 0x01, 0x02])])
        self.assertEqual(self.payloads(PHYS, stop), [bytes([0x71, 0x02, 0x03, 0x01])])
        self.assertEqual(self.app.stub_get("routine.LedSelfTest.stop"), 1)
        self.assertEqual(self.payloads(PHYS, stop), [bytes([0x7F, 0x31, 0x24])])
        self.assertEqual(self.payloads(PHYS, results), [bytes([0x71, 0x03, 0x03, 0x01, 0x02])])  # still there
        # suppressPosRspMsgIndicationBit: started, but no positive response
        self.assertEqual(self.payloads(PHYS, bytes([0x31, 0x81, 0x03, 0x01, 0x07])), [])
        self.assertEqual(self.app.stub_get("routine.LedSelfTest.start.durationCycles"), 7)

    def test_session_change_stops_a_running_routine(self):
        """9.2: the routine was started under a security level that a session change locks - it is stopped."""
        self.assertEqual(self.payloads(PHYS, bytes([0x10, 0x03]))[0][:2], bytes([0x50, 0x03]))
        self.unlock()
        self.assertEqual(self.payloads(PHYS, bytes([0x31, 0x01, 0x03, 0x01, 0x05]))[0][:4],
                         bytes([0x71, 0x01, 0x03, 0x01]))
        stops = self.app.stub_get("routine.LedSelfTest.stop")
        self.app.poll_ms(5100)                          # S3 timeout: back to the default session
        self.assertEqual(self.app.dll.app_session(), 1)
        self.assertEqual(self.app.dll.app_routine_running(0), 0)
        self.assertEqual(self.app.stub_get("routine.LedSelfTest.stop"), stops + 1)


def provision(app: HostApp, master: bytes, uid: bytes, record: bytes | None = None) -> bytes:
    """COM-30: simulated key sector and chip UID of the next power on, as provision.py writes them; returns the
    device key. `record` replaces the record (damaged or foreign records)."""
    device = blkeys.device_key(master, uid)
    data = blkeys.key_record(device) if record is None else record
    app.dll.app_set_key_record((ctypes.c_uint8 * len(data)).from_buffer_copy(data), ctypes.c_uint32(len(data)))
    app.dll.app_set_uid((ctypes.c_uint8 * len(uid)).from_buffer_copy(uid), ctypes.c_uint32(len(uid)))
    return device


class AppUdsDeviceKeyTest(unittest.TestCase):
    """COM-30: the product key algorithm DeviceKeySecurity (method hmac-sha256) with the level Application of the
    demo description (seed and key 4 bytes): the device key of the bootloader's security access (BL-03) from the
    simulated key sector, the key computed by the tester (blkeys.app_key), the finding of COM-28 (2E FD00 not
    writable on the target) closed. Master key: the insecure test key of the repository."""

    UID = bytes(range(0x30, 0x40))

    @classmethod
    def setUpClass(cls):
        app_dll(APP_DLL, EVAL_PROJECT)
        cls.app = HostApp(APP_DLL)
        cls.master = blkeys.load_master_key(blkeys.TEST_KEY_NAME).key

    def setUp(self):
        self.assertEqual(self.app.dll.app_uid_length(), len(self.UID))       # S32K144: 16 bytes (mcudb uid)
        self.app.dll.app_use_security_algorithm(2)
        self.addCleanup(self.app.dll.app_use_security_algorithm, 1)
        self.device = provision(self.app, self.master, self.UID)
        self.app.power_on()

    def tearDown(self):
        self.assertEqual(self.app.dll.app_assert_count(), 0, "ETL assert in the stack")

    def send(self, payload: bytes) -> bytes:
        """One single frame request, the response payload (single frame)."""
        self.assertTrue(self.app.rx(PHYS, (bytes([len(payload)]) + payload).ljust(8, bytes([PAD]))))
        for _ in range(60):
            self.app.poll_ms(1)
            frame = self.app.tx()
            if frame is not None:
                self.assertEqual(frame[0], RESP)
                return frame[1][1:1 + frame[1][0]]
        self.fail(f"no response to {payload.hex()}")

    def seed(self) -> bytes:
        response = self.send(bytes([0x27, 0x01]))
        self.assertEqual(response[:2], bytes([0x67, 0x01]), response.hex())
        return response[2:]

    def test_unlock_and_write_the_ram_test_value(self):
        """The board proof of COM-30 on the host: 2E FD00 after unlocking (COM-28 finding: 0x22/0x33 before)."""
        self.assertEqual(self.app.dll.app_key_state(), 1)
        self.assertEqual(self.send(bytes([0x10, 0x03]))[:2], bytes([0x50, 0x03]))
        self.assertEqual(self.send(bytes([0x2E, 0xFD, 0x00, 1, 2, 3, 4])), bytes([0x7F, 0x2E, 0x33]))
        seed = self.seed()
        self.assertEqual(len(seed), 4)
        self.assertEqual(self.send(bytes([0x27, 0x02]) + blkeys.app_key(self.device, 0x01, seed, 4)),
                         bytes([0x67, 0x02]))
        self.assertEqual(self.send(bytes([0x2E, 0xFD, 0x00, 0x12, 0x34, 0x56, 0x78])), bytes([0x6E, 0xFD, 0x00]))
        self.assertEqual(self.send(bytes([0x22, 0xFD, 0x00])), bytes([0x62, 0xFD, 0x00, 0x12, 0x34, 0x56, 0x78]))

    def test_keys_that_do_not_unlock(self):
        """Every key but the right one is refused: other master key, other UID, the bootloader's calculation
        (HMAC over the seed alone), another level, the host test algorithm, the device key itself."""
        other_master = bytes(32)
        wrong = {
            "other master key": lambda s: blkeys.app_key(blkeys.device_key(other_master, self.UID), 0x01, s, 4),
            "other chip UID": lambda s: blkeys.app_key(blkeys.device_key(self.master, bytes(16)), 0x01, s, 4),
            "bootloader calculation": lambda s: blkeys.seed_key(self.device, s)[:4],
            "level 0x03": lambda s: blkeys.app_key(self.device, 0x03, s, 4),
            "host test algorithm": lambda s: host_key(s, 0x01, 4),
            "device key": lambda s: self.device[:4],
        }
        for name, key in wrong.items():
            with self.subTest(name):
                self.app.power_on()
                self.assertEqual(self.send(bytes([0x10, 0x03]))[:2], bytes([0x50, 0x03]))
                self.assertEqual(self.send(bytes([0x27, 0x02]) + key(self.seed())), bytes([0x7F, 0x27, 0x35]))
                self.assertEqual(self.send(bytes([0x2E, 0xFD, 0x00, 1, 2, 3, 4])), bytes([0x7F, 0x2E, 0x33]))

    def test_every_byte_of_the_key_counts(self):
        for position in range(4):
            with self.subTest(position=position):
                self.app.power_on()
                self.assertEqual(self.send(bytes([0x10, 0x03]))[:2], bytes([0x50, 0x03]))
                key = bytearray(blkeys.app_key(self.device, 0x01, self.seed(), 4))
                key[position] ^= 0x01
                self.assertEqual(self.send(bytes([0x27, 0x02]) + bytes(key)), bytes([0x7F, 0x27, 0x35]))

    def test_a_key_answers_its_own_seed_only(self):
        self.assertEqual(self.send(bytes([0x10, 0x03]))[:2], bytes([0x50, 0x03]))
        old = self.seed()
        new = self.seed()                           # a new seed replaces the old one
        self.assertNotEqual(old, new)
        self.assertEqual(self.send(bytes([0x27, 0x02]) + blkeys.app_key(self.device, 0x01, old, 4)),
                         bytes([0x7F, 0x27, 0x35]))

    def test_seeds_do_not_repeat(self):
        """64 seeds in one run and 16 first seeds after a power on each: all different and never all zero."""
        seeds = []
        self.assertEqual(self.send(bytes([0x10, 0x03]))[:2], bytes([0x50, 0x03]))
        for _ in range(64):
            seeds.append(self.seed())
            self.app.poll_ms(1)
        for _ in range(16):
            self.app.power_on()
            self.app.poll_ms(3)
            self.assertEqual(self.send(bytes([0x10, 0x03]))[:2], bytes([0x50, 0x03]))
            seeds.append(self.seed())
        self.assertEqual(len(set(seeds)), len(seeds))
        self.assertNotIn(bytes(4), seeds)

    def test_without_a_provisioned_key_nothing_unlocks(self):
        """Erased, damaged or foreign key records: NRC 0x22 on requestSeed, as the bootloader (state as 0xF1A1)."""
        good = blkeys.key_record(self.device)
        records = {
            "erased": (b"\xff" * 48, 0),
            "crc": (good[:40] + bytes([good[40] ^ 1]) + good[41:], 2),
            "key byte": (good[:8] + bytes([good[8] ^ 1]) + good[9:], 2),
            "magic": (b"NXKZ" + good[4:], 2),
            "version": (good[:4] + bytes([2]) + good[5:], 2),
            "other method": (blkeys.key_record(self.device, method=2), 2),
            "half written": (good[:24] + b"\xff" * 24, 2),
        }
        for name, (record, state) in records.items():
            with self.subTest(name):
                provision(self.app, self.master, self.UID, record)
                self.app.power_on()
                self.assertEqual(self.app.dll.app_key_state(), state)
                self.assertEqual(self.send(bytes([0x10, 0x03]))[:2], bytes([0x50, 0x03]))
                self.assertEqual(self.send(bytes([0x27, 0x01])), bytes([0x7F, 0x27, 0x22]))

    def test_canflash_unlocks_and_writes(self):
        """The tester side: canflash.unlock_application() with the level of the demo description; the UID comes
        from 0xF18C (chip.uid), which the coordinator added to the demo description after COM-30."""
        levels = canflash.app_security_levels(EVAL_PROJECT)
        self.assertEqual([(lv.name, lv.level, lv.seed_bytes, lv.key_bytes, lv.method, lv.session) for lv in levels],
                         [("Application", 0x01, 4, 4, "hmac-sha256", 0x03)])
        self.addCleanup(canflash.use_master_key, canflash.MASTER_KEY)
        canflash.use_master_key(blkeys.load_master_key(blkeys.TEST_KEY_NAME))
        bus = EcuBus(self.app, None)
        client = canflash.make_client(bus)
        client.open()
        self.addCleanup(bus.shutdown)
        self.addCleanup(client.close)
        self.assertIn(0xF18C, EVAL_LENGTHS)                     # chip.uid in the demo description
        self.assertEqual(canflash.unlock_application(client, levels[0], log=lambda *_: None), self.UID)
        self.assertEqual(canflash.unlock_application(client, levels[0], self.UID, log=lambda *_: None), self.UID)
        response = client.send_request(udsoncan.Request.from_payload(bytes([0x2E, 0xFD, 0x00, 0xCA, 0xFE, 0, 1])))
        self.assertEqual(bytes(response.original_payload), bytes([0x6E, 0xFD, 0x00]))
        self.assertEqual(bytes(self.app.dll.app_ram_test_data()[:4]), bytes([0xCA, 0xFE, 0, 1]))


class AppUdsOtherAddressesTest(unittest.TestCase):
    """COM-11: the same stack built for a project with **other** diagnostic addresses.

    projects/buildtest_s32k148_min: request 0x6F0, functional 0x6DF, response 0x6F8, filler 0xAA, flow control
    with block size 8 and STmin 1 ms, tester 0xF3, ECU 0x31. Nothing in ecu/uds may assume 0x7E0/0x7E8 or 0xCC.
    """

    @classmethod
    def setUpClass(cls):
        cls.saved = selected()
        canflash.use_layout(canflash.target_from_project(OTHER_PROJECT))
        cls.phys = CFG["BL_CAN_ID_REQUEST_PHYS"]
        cls.func = CFG["BL_CAN_ID_REQUEST_FUNC"]
        cls.resp = CFG["BL_CAN_ID_RESPONSE"]
        cls.pad = CFG["BL_ISOTP_PADDING"]
        cls.version = canflash.bl_version()
        cls.app = HostApp(app_dll(OTHER_APP_DLL, OTHER_PROJECT))

    @classmethod
    def tearDownClass(cls):
        canflash.use_layout(cls.saved)

    def setUp(self):
        self.app.power_on(self.version)

    def tearDown(self):
        self.assertEqual(self.app.dll.app_assert_count(), 0, "ETL assert in the stack")

    def request_raw(self, cid: int, payload: bytes, wait_ms: float = 60.0) -> list[tuple[int, bytes]]:
        frame = bytes([len(payload)]) + payload
        self.assertTrue(self.app.rx(cid, frame.ljust(8, bytes([self.pad]))))
        out = []
        for _ in range(int(wait_ms)):
            self.app.poll_ms(1)
            while (f := self.app.tx()) is not None:
                out.append(f)
        return out

    def test_profile_differs_from_the_eval_board(self):
        self.assertEqual((self.phys, self.func, self.resp, self.pad), (0x6F0, 0x6DF, 0x6F8, 0xAA))
        self.assertNotEqual((self.phys, self.resp), (PHYS, RESP))
        self.assertEqual((CFG["BL_ISOTP_BLOCK_SIZE"], CFG["BL_ISOTP_STMIN_MS"]), (8, 1))

    def test_tester_present_on_the_project_ids_only(self):
        self.assertEqual(self.request_raw(self.phys, bytes([0x3E, 0x00])),
                         [(self.resp, bytes([0x02, 0x7E, 0x00]) + bytes([self.pad] * 5))])
        self.assertEqual(self.request_raw(self.func, bytes([0x3E, 0x00])),
                         [(self.resp, bytes([0x02, 0x7E, 0x00]) + bytes([self.pad] * 5))])
        # the addresses of the eval board reach nobody on this build
        for foreign in (PHYS, FUNC, RESP, self.resp):
            with self.subTest(id=hex(foreign)):
                self.assertEqual(self.request_raw(foreign, bytes([0x3E, 0x00])), [])

    def test_flow_control_announces_block_size_and_separation_time(self):
        request = bytes([0x2E, 0xFD, 0x00]) + bytes(range(1, 11))
        self.assertTrue(self.app.rx(self.phys, bytes([0x10, len(request)]) + request[:6]))
        self.app.poll_ms(5)
        self.assertEqual(self.app.tx(),
                         (self.resp, bytes([0x30, 0x08, 0x01]) + bytes([self.pad] * 5)))

    def test_bootloader_version_with_the_project_prefix(self):
        bus = EcuBus(self.app, None)
        client = canflash.make_client(bus)
        client.open()
        self.addCleanup(bus.shutdown)
        self.addCleanup(client.close)
        self.assertEqual(client.read_data_by_identifier_first(canflash.DID_BOOT_SOFTWARE_ID), self.version)
        # PLAT-08: own, shorter prefix of this profile - not the default nextbsw-bl
        self.assertEqual(self.version, f"NX148-BL {canflash.BL_VERSION_NUMBER}")
        # the eval board's identification is another product here
        self.app.power_on(BL_VERSION)
        with self.assertRaises(NegativeResponseException):
            client.read_data_by_identifier_first(canflash.DID_BOOT_SOFTWARE_ID)

    def test_session_control_and_boot_request(self):
        self.assertTrue(self.app.rx(self.phys, bytes([0x02, 0x10, 0x02]).ljust(8, bytes([self.pad]))))
        frames = []
        for _ in range(20):
            self.app.poll_ms(1)
            while (f := self.app.tx()) is not None:
                frames.append(f)
        self.assertEqual(frames, [(self.resp, bytes([0x06, 0x50, 0x02, 0x00, 0x32, 0x01, 0xF4, self.pad]))])
        self.assertEqual(self.app.dll.app_boot_request(), 1)

    def test_project_without_description_keeps_the_former_identifiers(self):
        """COM-28: this project names no diag.yaml, so tools/diaggen/default_diag.yaml applies - the identifiers
        of before COM-28, FD00 writable in the extended session without a security level, and no service 27."""
        def payloads(payload):
            return [f[1][1:1 + f[1][0]] for f in self.request_raw(self.phys, payload)]

        self.assertEqual(payloads(bytes([0x27, 0x01])), [bytes([0x7F, 0x27, 0x11])])
        self.assertEqual(payloads(bytes([0x22, 0xF1, 0x86])), [bytes([0x62, 0xF1, 0x86, 0x01])])
        self.assertEqual(payloads(bytes([0x10, 0x03]))[0][:2], bytes([0x50, 0x03]))
        self.assertEqual(payloads(bytes([0x2E, 0xFD, 0x00, 1, 2, 3, 4])), [bytes([0x6E, 0xFD, 0x00])])
        self.assertEqual(payloads(bytes([0x22, 0xFD, 0x00])), [bytes([0x62, 0xFD, 0x00, 1, 2, 3, 4])])
        self.assertEqual(payloads(bytes([0x31, 0x01, 0x03, 0x01])), [bytes([0x7F, 0x31, 0x31])])

    def test_deliberate_errors_are_noticed(self):
        """Significance of the tests above: the eval board values must make each of them fail."""
        with self.assertRaises(AssertionError):     # wrong request id -> no answer
            self.assertEqual(self.request_raw(PHYS, bytes([0x3E, 0x00])),
                             [(self.resp, bytes([0x02, 0x7E, 0x00]) + bytes([self.pad] * 5))])
        with self.assertRaises(AssertionError):     # eval board filler byte in the padding
            self.assertEqual(self.request_raw(self.phys, bytes([0x3E, 0x00])),
                             [(self.resp, bytes([0x02, 0x7E, 0x00]) + bytes([PAD] * 5))])
        with self.assertRaises(AssertionError):     # eval board response id
            self.assertEqual(self.request_raw(self.phys, bytes([0x3E, 0x00])),
                             [(RESP, bytes([0x02, 0x7E, 0x00]) + bytes([self.pad] * 5))])
        with self.assertRaises(AssertionError):     # flow control without block size and separation time
            request = bytes([0x2E, 0xFD, 0x00]) + bytes(range(1, 11))
            self.assertTrue(self.app.rx(self.phys, bytes([0x10, len(request)]) + request[:6]))
            self.app.poll_ms(5)
            self.assertEqual(self.app.tx(), (self.resp, bytes([0x30, 0x00, 0x00]) + bytes([self.pad] * 5)))


class AppUdsProfileTest(unittest.TestCase):
    """COM-28: the generated jobs with the test profile tools/diaggen/test/profile/diag.yaml, not the eval board.

    Diagnostic addresses of projects/buildtest_s32k148_min; sessions with P2 40 ms / P2* 2000 ms, the levels
    Workshop (0x03, seed 2, key 3, 2 attempts) and Factory (0x11, seed 4, key 4), identifiers with little endian,
    signed, float and text elements, a text table and a range on writes, a routine without stop step.
    """

    @classmethod
    def setUpClass(cls):
        cls.saved = selected()
        canflash.use_layout(canflash.target_from_project(OTHER_PROJECT))
        cls.phys, cls.resp, cls.pad = CFG["BL_CAN_ID_REQUEST_PHYS"], CFG["BL_CAN_ID_RESPONSE"], CFG["BL_ISOTP_PADDING"]
        cls.app = HostApp(app_dll(PROFILE_APP_DLL, OTHER_PROJECT, PROFILE_DIAG))

    @classmethod
    def tearDownClass(cls):
        canflash.use_layout(cls.saved)

    def setUp(self):
        self.app.power_on(canflash.bl_version())

    def tearDown(self):
        self.assertEqual(self.app.dll.app_assert_count(), 0, "ETL assert in the stack")

    def send(self, payload: bytes) -> bytes:
        """One request through udsoncan (segmented as needed), the response payload (positive or negative)."""
        if not hasattr(self, "_client"):
            bus = EcuBus(self.app, None)
            self._client = canflash.make_client(bus)
            self._client.open()
            self.addCleanup(bus.shutdown)
            self.addCleanup(self._client.close)
            self.addCleanup(lambda: delattr(self, "_client"))
        try:
            return bytes(self._client.send_request(udsoncan.Request.from_payload(payload)).original_payload)
        except NegativeResponseException as e:
            return bytes(e.response.original_payload)

    def unlock(self, level: int, key_bytes: int):
        seed = self.send(bytes([0x27, level]))
        self.assertEqual(seed[:2], bytes([0x67, level]))
        self.assertEqual(self.send(bytes([0x27, level + 1]) + host_key(seed[2:], level, key_bytes)),
                         bytes([0x67, level + 1]))

    def test_session_timing_of_the_profile(self):
        self.assertEqual(self.send(bytes([0x10, 0x03])), bytes([0x50, 0x03, 0x00, 0x28, 0x00, 0xC8]))

    def test_mixed_sources_and_byte_orders(self):
        """0x0200: constant uint16 little endian, int16 little endian and float32 from ports, session byte."""
        self.app.stub_set("did.MotorState.current", -2)
        self.app.stub_set("did.MotorState.temperature", struct.unpack("<I", struct.pack("<f", 21.5))[0])
        self.assertEqual(self.send(bytes([0x22, 0x02, 0x00])),
                         bytes([0x62, 0x02, 0x00, 0x34, 0x12, 0xFE, 0xFF, 0x41, 0xAC, 0x00, 0x00, 0x01]))
        # a text constant is filled up with NUL to its width
        self.assertEqual(self.send(bytes([0x22, 0xF1, 0xF0])), bytes([0x62, 0xF1, 0xF0]) + b"NX\0\0")

    def test_texts_of_the_stack_split_by_the_profile(self):
        """COM-33 with other widths than the eval board: F180 (24 byte text), 0x0203 (20 byte text in front of the
        session byte), the 4 byte constant F1F0 and the 9 byte 0x0200 in one request, split by the profile alone."""
        desc = description(PROFILE_DIAG)
        version = canflash.bl_version()
        response = self.send(bytes([0x22, 0xF1, 0x80, 0x02, 0x03, 0xF1, 0xF0, 0x02, 0x00]))
        records = diagdesc.split_read_response(desc, response)
        self.assertEqual([(r.identifier, len(r.data)) for r in records],
                         [(0xF180, 25), (0x0203, 21), (0xF1F0, 4), (0x0200, 9)])
        boot, build, code, _ = records
        self.assertEqual(boot.elements["bootSoftwareIdentification"], version.encode().ljust(24, b"\0"))
        self.assertEqual(boot.text("bootSoftwareIdentification"), version)
        self.assertEqual(build.elements["application"], b"nextbsw-app host".ljust(20, b"\0"))
        self.assertEqual(build.elements["session"], b"\x01")          # behind the text, at a fixed offset
        self.assertEqual(code.text("code"), "NX")
        # canflash reads the filled text like the bare one (it strips the NUL behind the text)
        self.assertEqual(canflash.parse_identification(boot.data), version)

    def test_read_sessions_and_security_of_an_identifier(self):
        self.assertEqual(self.send(bytes([0x22, 0x02, 0x02])), bytes([0x7F, 0x22, 0x31]))    # Service only
        # an identifier not readable in this session is left out of a request with several
        self.assertEqual(self.send(bytes([0x22, 0x02, 0x02, 0xF1, 0xF0])), bytes([0x62, 0xF1, 0xF0]) + b"NX\0\0")
        self.assertEqual(self.send(bytes([0x22, 0x02, 0x01])), bytes([0x7F, 0x22, 0x33]))    # Factory needed
        self.assertEqual(self.send(bytes([0x27, 0x11])), bytes([0x7F, 0x27, 0x7F]))          # no level in Base
        self.assertEqual(self.send(bytes([0x10, 0x03]))[:2], bytes([0x50, 0x03]))
        self.assertEqual(self.send(bytes([0x22, 0x02, 0x02])), bytes([0x62, 0x02, 0x02, 0x07]))
        self.unlock(0x11, 4)
        self.app.stub_set("did.Setpoint.setpoint", -300)
        self.app.stub_set("did.Setpoint.mode", 3)
        self.assertEqual(self.send(bytes([0x22, 0x02, 0x01])), bytes([0x62, 0x02, 0x01, 0xFE, 0xD4, 0x03]))

    def test_write_checks_security_range_and_text_table(self):
        self.assertEqual(self.send(bytes([0x10, 0x03]))[:2], bytes([0x50, 0x03]))
        write = bytes([0x2E, 0x02, 0x01])
        self.assertEqual(self.send(write + bytes([0xFE, 0x0C, 0x01])), bytes([0x7F, 0x2E, 0x33]))
        self.unlock(0x11, 4)                            # the wrong level: only one is unlocked at a time
        self.assertEqual(self.send(write + bytes([0xFE, 0x0C, 0x01])), bytes([0x7F, 0x2E, 0x33]))
        self.unlock(0x03, 3)
        self.assertEqual(self.send(bytes([0x22, 0x02, 0x01])), bytes([0x7F, 0x22, 0x33]))    # Factory locked now
        self.assertEqual(self.send(write + bytes([0xFE, 0x0C])), bytes([0x7F, 0x2E, 0x13]))  # one byte short
        self.assertEqual(self.send(write + bytes([0x02, 0x58, 0x01])), bytes([0x7F, 0x2E, 0x31]))  # 600 > 500
        self.assertEqual(self.send(write + bytes([0xFE, 0x0B, 0x01])), bytes([0x7F, 0x2E, 0x31]))  # -501
        self.assertEqual(self.send(write + bytes([0xFE, 0x0C, 0x02])), bytes([0x7F, 0x2E, 0x31]))  # mode 2
        self.assertEqual(self.app.stub_writes("did.Setpoint.setpoint"), 0, "a refused write changed a port")
        self.assertEqual(self.send(write + bytes([0xFE, 0x0C, 0x03])), bytes([0x6E, 0x02, 0x01]))
        self.assertEqual((self.app.stub_get("did.Setpoint.setpoint"), self.app.stub_get("did.Setpoint.mode")),
                         (0xFE0C, 3))
        self.assertEqual(self.app.stub_writes("did.Setpoint.setpoint"), 1)
        # not writable in the default session
        self.assertEqual(self.send(bytes([0x10, 0x01]))[:2], bytes([0x50, 0x01]))
        self.assertEqual(self.send(write + bytes([0x00, 0x00, 0x00])), bytes([0x7F, 0x2E, 0x31]))

    def test_restartable_routine_without_stop(self):
        start = bytes([0x31, 0x01, 0x04, 0x00])
        results = bytes([0x31, 0x03, 0x04, 0x00])
        self.assertEqual(self.send(start + bytes([0x64, 0x00])), bytes([0x7F, 0x31, 0x31]))  # Service only
        self.assertEqual(self.send(bytes([0x10, 0x03]))[:2], bytes([0x50, 0x03]))
        self.unlock(0x03, 3)
        self.assertEqual(self.send(results), bytes([0x7F, 0x31, 0x24]))                     # never started
        self.assertEqual(self.send(bytes([0x31, 0x02, 0x04, 0x00])), bytes([0x7F, 0x31, 0x12]))  # no stop step
        self.assertEqual(self.send(start + bytes([0x05, 0x00])), bytes([0x7F, 0x31, 0x31]))  # 5 < min 10
        self.app.stub_set("routine.Calibrate.start.status", -1)
        for count in (1, 2):                            # no stop step: may be started again at any time
            self.assertEqual(self.send(start + bytes([0x64, 0x00])), bytes([0x71, 0x01, 0x04, 0x00, 0xFF]))
            self.assertEqual(self.app.stub_get("routine.Calibrate.start"), count)
        self.assertEqual(self.app.stub_get("routine.Calibrate.start.target"), 100)          # little endian
        self.app.stub_set("routine.Calibrate.results.offset", -70000)
        self.assertEqual(self.send(results), bytes([0x71, 0x03, 0x04, 0x00, 0xFF, 0xFE, 0xEE, 0x90]))

    def test_routine_in_every_session_without_security(self):
        self.assertEqual(self.send(bytes([0x31, 0x01, 0x04, 0x01])), bytes([0x71, 0x01, 0x04, 0x01]))
        self.assertEqual(self.app.stub_get("routine.Flush.start"), 1)
        self.assertEqual(self.send(bytes([0x31, 0x03, 0x04, 0x01])), bytes([0x7F, 0x31, 0x12]))
        self.assertEqual(self.send(bytes([0x31, 0x01, 0x04, 0x01, 0x00])), bytes([0x7F, 0x31, 0x13]))

    def test_attempts_and_delay_of_a_level(self):
        """Workshop: 2 attempts, 1000 ms delay."""
        self.assertEqual(self.send(bytes([0x10, 0x03]))[:2], bytes([0x50, 0x03]))
        for expected in (0x35, 0x36):
            seed = self.send(bytes([0x27, 0x03]))
            wrong = bytes(b ^ 0x0F for b in host_key(seed[2:], 0x03, 3))
            self.assertEqual(self.send(bytes([0x27, 0x04]) + wrong), bytes([0x7F, 0x27, expected]))
        self.assertEqual(self.send(bytes([0x27, 0x03])), bytes([0x7F, 0x27, 0x37]))
        self.assertEqual(self.send(bytes([0x27, 0x11]))[:2], bytes([0x67, 0x11]))   # other level: own counter
        time.sleep(1.1)                                  # simulated time follows real time on the EcuBus
        self.unlock(0x03, 3)


class AppUdsDeviceKeyProfileTest(unittest.TestCase):
    """COM-30 with the test profile: two hmac-sha256 levels of other lengths (Workshop 0x03 seed 2 / key 3,
    Factory 0x11 seed 4 / key 4), the chip UID from 0xF18C (`chip.uid`), diagnostic addresses of
    buildtest_s32k148_min. canflash reads the UID itself; a key of one level does not unlock the other."""

    UID = bytes.fromhex("0123456789abcdeffedcba9876543210")

    @classmethod
    def setUpClass(cls):
        cls.saved = selected()
        canflash.use_layout(canflash.target_from_project(OTHER_PROJECT))
        cls.app = HostApp(app_dll(PROFILE_APP_DLL, OTHER_PROJECT, PROFILE_DIAG))
        cls.master = blkeys.load_master_key(blkeys.TEST_KEY_NAME)
        # the levels as canflash takes them from a description (here the profile on its own)
        sys.path.insert(0, str(ROOT / "tools" / "diaggen"))
        import diaggen  # noqa: E402
        desc = diaggen.load_file(PROFILE_DIAG).desc
        cls.levels = {lv.name: canflash.AppLevel(lv.name, lv.level, lv.seed_bytes, lv.key_bytes, lv.method, 0x03)
                      for lv in desc.security}

    @classmethod
    def tearDownClass(cls):
        canflash.use_layout(cls.saved)

    def setUp(self):
        self.app.dll.app_use_security_algorithm(2)
        self.addCleanup(self.app.dll.app_use_security_algorithm, 1)
        self.device = provision(self.app, self.master.key, self.UID)
        self.app.power_on(canflash.bl_version())
        self.addCleanup(canflash.use_master_key, canflash.MASTER_KEY)
        canflash.use_master_key(self.master)
        bus = EcuBus(self.app, None)
        self.client = canflash.make_client(bus)
        self.client.open()
        self.addCleanup(bus.shutdown)
        self.addCleanup(self.client.close)

    def tearDown(self):
        self.assertEqual(self.app.dll.app_assert_count(), 0, "ETL assert in the stack")

    def send(self, payload: bytes) -> bytes:
        try:
            return bytes(self.client.send_request(udsoncan.Request.from_payload(payload)).original_payload)
        except NegativeResponseException as e:
            return bytes(e.response.original_payload)

    def test_chip_uid_in_every_session(self):
        self.assertEqual(self.send(bytes([0x22, 0xF1, 0x8C])), bytes([0x62, 0xF1, 0x8C]) + self.UID)

    def test_both_levels_unlock_with_their_own_lengths(self):
        quiet = dict(log=lambda *_: None)
        # the DLL and its RTE stand-in are shared with AppUdsProfileTest: nothing here writes a port. Unlocked
        # Workshop shows by the range check that follows the security check (0x31 instead of 0x33, 600 > 500).
        write = bytes([0x2E, 0x02, 0x01, 0x02, 0x58, 0x01])
        self.assertEqual(self.send(bytes([0x10, 0x03]))[:2], bytes([0x50, 0x03]))
        self.assertEqual(self.send(write), bytes([0x7F, 0x2E, 0x33]))
        self.assertEqual(canflash.unlock_application(self.client, self.levels["Workshop"], **quiet), self.UID)
        self.assertEqual(self.send(write), bytes([0x7F, 0x2E, 0x31]))
        self.assertEqual(self.send(bytes([0x22, 0x02, 0x01])), bytes([0x7F, 0x22, 0x33]))
        self.assertEqual(canflash.unlock_application(self.client, self.levels["Factory"], **quiet), self.UID)
        self.assertEqual(self.send(bytes([0x22, 0x02, 0x01]))[:3], bytes([0x62, 0x02, 0x01]))

    def test_the_key_of_one_level_does_not_unlock_another(self):
        self.assertEqual(self.send(bytes([0x10, 0x03]))[:2], bytes([0x50, 0x03]))
        seed = self.send(bytes([0x27, 0x11]))[2:]
        self.assertEqual(len(seed), 4)
        self.assertEqual(self.send(bytes([0x27, 0x12]) + blkeys.app_key(self.device, 0x03, seed, 4)),
                         bytes([0x7F, 0x27, 0x36]))      # attempts: 1 in the profile
        seed = self.send(bytes([0x27, 0x03]))[2:]
        self.assertEqual(len(seed), 2)
        self.assertEqual(self.send(bytes([0x27, 0x04]) + blkeys.app_key(self.device, 0x03, seed, 3)),
                         bytes([0x67, 0x04]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
