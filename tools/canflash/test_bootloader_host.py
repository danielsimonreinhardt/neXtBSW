"""End-to-end tests: canflash.py against the bootloader core built for the host (simulated flash/CAN).

The bootloader DLL is built with Zig/Clang for the memory layout generated from a project (tools/canflash/hostbuild.py,
ARCH-08): the eval board demo project and the synthetic profile testproject_bootloader (code flash at 0x08000000 with
8-KB sectors, one RAM region, no SBC, other CAN bus). BL_HOST_DLL: use a prebuilt DLL for the eval board layout.
"""

import ctypes
import datetime
import os
import random
import shutil
import struct
import sys
import tempfile
import threading
import time
import unittest
import zlib
from dataclasses import replace
from pathlib import Path

import can
import udsoncan
from can.interfaces.vector.exceptions import VectorOperationError
from udsoncan.exceptions import NegativeResponseException

sys.path.insert(0, str(Path(__file__).resolve().parent))
import blkeys  # noqa: E402
import canflash  # noqa: E402
import hostbuild  # noqa: E402

# BL-03: the host DLL's key sector is provisioned with the insecure test key (tools/canflash/host/bl_host_security.c),
# so the tester uses it too - also for importers of this module (ecu/uds/test/host/test_app_uds_host.py)
canflash.use_master_key(blkeys.load_master_key(blkeys.TEST_KEY_NAME))

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "test"))
try:
    import build_dirs  # noqa: E402  (where the demo target build is, PLAT-07)
except ImportError:  # copy of the sources without tools/test (mutation test): only NX_BUILD_DIR counts
    build_dirs = None
CFG = canflash.CFG
# PLAT-62: the eval board demo stays the reference layout where it exists - the prebuilt bootloader DLL of the group
# build (bl-host-dll) and the importers of this module (bootloader/test/host, ecu/uds/test/host) are built for it. The
# eval board is private (PLAT-46 F10): in the exported open tree the own full test profile takes its place (same memory
# layout and diagnostic addresses, board testboard_full), so these tests run there instead of failing.
FULL_PROJECT = ROOT / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full" / "project.yaml"
PRIVATE_EVAL_PROJECT = ROOT / "projects" / "evalboard_s32k144_demo" / "project.yaml"
EVAL_PROJECT = PRIVATE_EVAL_PROJECT if PRIVATE_EVAL_PROJECT.is_file() else FULL_PROJECT
EVAL_BOARD_NAME = "evalboard_s32k144" if EVAL_PROJECT == PRIVATE_EVAL_PROJECT else "testboard_full"
TEST_MCUDB = ROOT / "tools" / "nxconfig" / "test" / "mcudb"
OTHER_PROJECT = ROOT / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_bootloader" / "project.yaml"
# second build profile of the repository (ARCH-09): S32K148, 1.5 MB flash, no hardware
S32K148_PROJECT = ROOT / "projects" / "buildtest_s32k148_min" / "project.yaml"
DLL_PATH = Path(os.environ["BL_HOST_DLL"]) if os.environ.get("BL_HOST_DLL") else None
# memory layout and diagnostic addresses of the eval board for importers of CFG
# (ecu/uds/test/host/test_app_uds_host.py); NX_BL_LAYOUT: generated bl_memory.h (and the bl_diag.h next to it)
# instead, for copies of this file without the configuration (mutation test)
EVAL_LAYOUT = (canflash.load_build(Path(os.environ["NX_BL_LAYOUT"])) if os.environ.get("NX_BL_LAYOUT")
               else canflash.target_from_project(EVAL_PROJECT))
if not all(k in CFG for k in canflash.LAYOUT_KEYS):
    canflash.use_layout(EVAL_LAYOUT)


def selected() -> dict:
    """Memory layout, identity and diagnostic addresses currently in CFG/TEXT (saved and restored by the tests)."""
    values = {k: CFG[k] for k in (*canflash.LAYOUT_KEYS, *canflash.DIAG_KEYS, canflash.TARGET_KEY)}
    values[canflash.PREFIX_KEY] = canflash.TEXT[canflash.PREFIX_KEY]
    # BL-10: family and hardware identity belong to the layout; use_layout() drops them when they are missing
    values.update({k: canflash.TEXT[k] for k in (canflash.FAMILY_KEY, canflash.HARDWARE_KEY) if k in canflash.TEXT})
    return values


_BUILT: dict[str, Path] = {}
_BUILD_DIR = Path(tempfile.mkdtemp(prefix="bl_host_"))


def host_dll(project: Path, mcudb: Path | None = None) -> Path:
    """Bootloader host DLL for the memory layout of `project`, built once per test run."""
    key = str(project)
    if key not in _BUILT:
        name = project.parent.name
        layout = hostbuild.generate_layout(project, _BUILD_DIR / name / "gen", mcudb)
        _BUILT[key] = hostbuild.build_bl_host(_BUILD_DIR / name / "bl_host.dll", layout)
    return _BUILT[key]


def host_dll_for_layout(name: str) -> Path:
    """Bootloader host DLL for a synthetic layout bootloader/test/layouts/<name>.yaml (PLAT-16)."""
    key = f"layout:{name}"
    if key not in _BUILT:
        layout = hostbuild.write_test_layout(name, _BUILD_DIR / name / "gen")
        _BUILT[key] = hostbuild.build_bl_host(_BUILD_DIR / name / "bl_host.dll", layout)
    return _BUILT[key]


# Tester P2 of the host tests (BL-10). The simulated ECU follows real time, so the 1 s P2 of canflash measured the
# load of the machine: with parallel target builds or test runs, 1-4 cases of the S32K148 and STM32F446 classes timed
# out (BL-03), single runs never did. The bootloader's own timing (response pending before every sector, P2*) is
# checked on its simulated clock (test_response_pending_covers_long_sector_erases), not by this timeout; tests
# that need a short P2 set it themselves (test_old_sequence_loses_security_access).
HOST_P2_TIMEOUT = 10.0
_MAKE_CLIENT = canflash.make_client


def patient_client(bus: can.BusABC):
    """canflash.make_client() with HOST_P2_TIMEOUT - also for clients canflash opens itself (flash_over_bus)."""
    client = _MAKE_CLIENT(bus)
    client.set_config("p2_timeout", HOST_P2_TIMEOUT)
    return client


def setUpModule():
    canflash.make_client = patient_client


def tearDownModule():
    canflash.make_client = _MAKE_CLIENT
    shutil.rmtree(_BUILD_DIR, ignore_errors=True)


class HostBootloader:
    def __init__(self, path: Path):
        self.dll = ctypes.CDLL(str(path))
        self.dll.host_flash.restype = ctypes.POINTER(ctypes.c_uint8)
        self.dll.host_flash_size.restype = ctypes.c_uint32
        self.dll.host_millis.restype = ctypes.c_uint32
        self.dll.host_jump_address.restype = ctypes.c_uint32
        for name in ("host_sector_count", "host_erase_log"):
            getattr(self.dll, name).restype = ctypes.c_uint32
        self.start = 0
        if hasattr(self.dll, "host_flash_start"):          # flash array index 0 = BL_FLASH_START
            self.dll.host_flash_start.restype = ctypes.c_uint32
            self.start = self.dll.host_flash_start()
        # BL-25: send time of a popped frame on the bootloader's clock (a flash step delays the pop, not the send)
        self.tx_time = self.dll.host_millis
        if hasattr(self.dll, "host_last_tx_ms"):
            self.dll.host_last_tx_ms.restype = ctypes.c_uint32
            self.tx_time = self.dll.host_last_tx_ms
        self.lock = threading.RLock()
        # direction, CAN id, data, simulated time in ms (PLAT-16: the response pending timing is checked on the
        # bootloader's own clock, because an erase costs simulated, not real, milliseconds)
        self.log: list[tuple[str, int, bytes, int]] = []

    def flash(self) -> ctypes.Array:
        size = self.dll.host_flash_size()
        return (ctypes.c_uint8 * size).from_address(ctypes.addressof(self.dll.host_flash().contents))

    def write(self, address: int, data: bytes):
        ctypes.memmove(ctypes.addressof(self.flash()) + address - self.start, data, len(data))

    def read(self, address: int, size: int) -> bytes:
        return ctypes.string_at(ctypes.addressof(self.flash()) + address - self.start, size)

    # ------------------------------------------------------------------ PLAT-16: sector table and erase log

    def sectors(self) -> list[tuple[int, int]]:
        """(start, size) of every sector of the code flash, from the table the core uses."""
        out = []
        start, size = ctypes.c_uint32(), ctypes.c_uint32()
        for index in range(self.dll.host_sector_count()):
            assert self.dll.host_sector_by_index(ctypes.c_uint32(index), ctypes.byref(start), ctypes.byref(size))
            out.append((start.value, size.value))
        return out

    def sector_at(self, address: int) -> tuple[int, int, int] | None:
        start, size, index = ctypes.c_uint32(), ctypes.c_uint32(), ctypes.c_uint32()
        if not self.dll.host_sector_at(ctypes.c_uint32(address), ctypes.byref(start), ctypes.byref(size),
                                      ctypes.byref(index)):
            return None
        return start.value, size.value, index.value

    def erased(self) -> list[tuple[int, int]]:
        """Sectors erased since the last host_erase_all/host_clear_erase_log, in order."""
        buf = (ctypes.c_uint32 * 1024)()
        total = self.dll.host_erase_log(buf, ctypes.c_uint32(512))
        return [(buf[2 * i], buf[2 * i + 1]) for i in range(min(total, 512))]


class HostBus(can.BusABC):
    """python-can bus whose other end is the host-built bootloader. Simulated time follows real time."""

    def __init__(self, bl: HostBootloader, **kwargs):
        super().__init__(channel="host", **kwargs)
        self.bl = bl
        self.last = time.perf_counter()

    def _advance(self):
        now = time.perf_counter()
        ms = int((now - self.last) * 1000)
        if ms:
            self.last += ms / 1000
        self.bl.dll.host_poll(ctypes.c_uint32(ms))

    def send(self, msg, timeout=None):
        with self.bl.lock:
            data = bytes(msg.data)
            self.bl.log.append(("rx", msg.arbitration_id, data, self.bl.dll.host_millis()))
            buf = (ctypes.c_uint8 * 8).from_buffer_copy(data.ljust(8, b"\0"))
            self.bl.dll.host_can_rx(ctypes.c_uint32(msg.arbitration_id), buf, ctypes.c_uint8(len(data)))
            self._advance()

    def _recv_internal(self, timeout):
        end = time.perf_counter() + (timeout if timeout is not None else 3600)
        cid, length, buf = ctypes.c_uint32(), ctypes.c_uint8(), (ctypes.c_uint8 * 8)()
        while True:
            with self.bl.lock:
                self._advance()
                if self.bl.dll.host_can_tx(ctypes.byref(cid), buf, ctypes.byref(length)):
                    data = bytes(buf[: length.value])
                    self.bl.log.append(("tx", cid.value, data, self.bl.tx_time()))
                    return can.Message(arbitration_id=cid.value, data=data, is_extended_id=False), False
            if time.perf_counter() >= end:
                return None, False
            time.sleep(0.0005)

    def shutdown(self):
        super().shutdown()


def make_app_image(size: int, seed: int = 1) -> bytes:
    rnd = random.Random(seed)
    start = CFG["BL_APP_START"]
    sp = 0x20003000 if CFG["BL_RAM_START"] < 0x20003000 <= CFG["BL_RAM_END"] else CFG["BL_RAM_END"]
    vectors = struct.pack("<II", sp, start + 0x411)
    return vectors + bytes(rnd.getrandbits(8) for _ in range(size - len(vectors)))


COMPAT_FIELDS = ("magic", "version", "size", "target", "layout", "app_end", "record_addr")


def record_with(data: bytes, crc: int | None = None, **fields: int) -> bytes:
    """Validity record of `data` whose compatibility block has single fields replaced: record of an image built for
    another board, derivative or memory layout (BL-02)."""
    values = list(struct.unpack("<7I", canflash.compat_block()[:28]))
    for name, value in fields.items():
        values[COMPAT_FIELDS.index(name)] = value
    head = struct.pack("<7I", *values)
    return canflash.app_record(data) + head + struct.pack("<I", zlib.crc32(head) if crc is None else crc)


def routine_requests(log) -> list[int]:
    """Identifiers of the RoutineControl startRoutine requests in a bootloader log, in order (BL-24).

    Read from the single or first frame the bootloader received, i.e. what went over the bus - not what the
    tester's code intended to send."""
    rids = []
    for direction, cid, data, _ in log:
        if direction != "rx" or cid != CFG["BL_CAN_ID_REQUEST_PHYS"]:
            continue
        kind = data[0] & 0xF0
        payload = data[1 : 1 + data[0]] if kind == 0x00 else data[2:] if kind == 0x10 else b""
        if payload[:2] == b"\x31\x01" and len(payload) >= 4:
            rids.append(int.from_bytes(payload[2:4], "big"))
    return rids


def compat_record(**fields: int) -> bytes:
    """Request record of the compatibility routine for this build, single values replaced (BL-02)."""
    image = {**canflash.image_compat(), **fields}
    return bytes([image["version"]]) + struct.pack(">IIIII", image["target"], image["layout"], image["app_start"],
                                                   image["app_end"], image["record_addr"])


class BootloaderTest(unittest.TestCase):
    """Eval board memory layout (projects/evalboard_s32k144_demo, else the full test profile - PLAT-62)."""

    PROJECT, MCUDB = EVAL_PROJECT, None
    LAYOUT = None                      # synthetic layout bootloader/test/layouts/<name>.yaml instead of a project

    @classmethod
    def setUpClass(cls):
        use_prebuilt = DLL_PATH is not None and cls.LAYOUT is None and cls.PROJECT == EVAL_PROJECT
        if use_prebuilt and not DLL_PATH.exists():
            raise unittest.SkipTest(f"{DLL_PATH} not built")
        if cls.LAYOUT is not None:
            path = host_dll_for_layout(cls.LAYOUT)
        else:
            path = DLL_PATH if use_prebuilt else host_dll(cls.PROJECT, cls.MCUDB)
        cls.layout = selected()
        canflash.use_layout(canflash.load_build(path.parent / "gen" / "bootloader" / "bl_memory.h")
                            if not use_prebuilt else EVAL_LAYOUT)
        cls.bl = HostBootloader(path)

    @classmethod
    def tearDownClass(cls):
        canflash.use_layout(cls.layout)

    def setUp(self):
        self.bl.dll.host_erase_all()
        self.bl.dll.host_set_costs(20, 1)
        self.bl.log.clear()
        # BL-03: every test starts like after a power-on (no lockout of an earlier test left in the no-init RAM),
        # with the device provisioned with the insecure test key
        self.bl.dll.host_power_loss(ctypes.c_uint8(0))
        self.bl.dll.host_provision_test_key()

    def power_on(self, boot_request=False):
        # the lock keeps the reset and the simulated time apart from the ISO-TP receive thread of an open client
        with self.bl.lock:
            self.bl.dll.host_power_on(1 if boot_request else 0)

    def run_ms(self, ms):
        for _ in range(ms):
            with self.bl.lock:
                self.bl.dll.host_poll(ctypes.c_uint32(1))

    def client(self):
        bus = HostBus(self.bl)
        client = canflash.make_client(bus)
        client.open()
        self.addCleanup(bus.shutdown)
        self.addCleanup(client.close)
        return client

    def fingerprint(self, client, tester: str = "hosttest", date=None):
        """BL-04: the fingerprint the bootloader demands before every erase and every download."""
        return canflash.write_fingerprint(client, tester, date, log=lambda *a, **k: None)

    def install_app(self, data: bytes, record: bytes | None = None):
        self.bl.write(CFG["BL_APP_START"], data)
        self.bl.write(CFG["BL_APP_RECORD_ADDR"], canflash.app_record_full(data) if record is None else record)

    def jumps_after_startup_window(self) -> bool:
        self.power_on()
        self.run_ms(CFG["BL_STARTUP_WINDOW_MS"] + 10)
        return self.bl.dll.host_jump_address() == CFG["BL_APP_START"]

    def target_compat(self) -> dict:
        """DID 0xF1A0 of a freshly started bootloader; the boot request keeps it from jumping into a valid
        application. Client and bus are closed again so that no second tester listens on the same DLL."""
        self.power_on(boot_request=True)
        bus = HostBus(self.bl)
        client = canflash.make_client(bus)
        client.open()
        try:
            target = canflash.read_target_compat(client)
        finally:
            client.close()
            bus.shutdown()
        self.assertIsNotNone(target, "the host bootloader must answer DID 0xF1A0")
        return target

    def test_empty_flash_stays_in_bootloader(self):
        self.power_on()
        self.run_ms(1000)
        self.assertEqual(self.bl.dll.host_jump_address(), 0)
        client = self.client()
        # identification prefix from the project (COM-11), version number from bl_config.h
        self.assertEqual(client.read_data_by_identifier_first(canflash.DID_BOOT_SOFTWARE_ID), canflash.bl_version())

    def test_diagnostic_addresses_come_from_the_project(self):
        """COM-11: the DLL was built with the generated bl_diag.h of its project (ids, filler, identification)."""
        self.power_on()
        prefix = canflash.TEXT[canflash.PREFIX_KEY]
        self.assertEqual(canflash.bl_version(), f"{prefix} {canflash.BL_VERSION_NUMBER}")
        phys, func, resp = (CFG["BL_CAN_ID_REQUEST_PHYS"], CFG["BL_CAN_ID_REQUEST_FUNC"], CFG["BL_CAN_ID_RESPONSE"])
        self.assertEqual(len({phys, func, resp}), 3)
        pad = CFG["BL_ISOTP_PADDING"]

        def request(cid: int, payload: bytes) -> list[tuple[int, bytes]]:
            frame = bytes([len(payload)]) + payload
            with self.bl.lock:
                buf = (ctypes.c_uint8 * 8).from_buffer_copy(frame.ljust(8, bytes([pad])))
                self.bl.dll.host_can_rx(ctypes.c_uint32(cid), buf, ctypes.c_uint8(8))
            self.run_ms(20)
            out, cid_out, length, buf = [], ctypes.c_uint32(), ctypes.c_uint8(), (ctypes.c_uint8 * 8)()
            while self.bl.dll.host_can_tx(ctypes.byref(cid_out), buf, ctypes.byref(length)):
                out.append((cid_out.value, bytes(buf[: length.value])))
            return out

        # 3E 00 on the physical id: answered on the project's response id, padded with the project's filler
        self.assertEqual(request(phys, bytes([0x3E, 0x00])),
                         [(resp, bytes([0x02, 0x7E, 0x00]) + bytes([pad] * 5))])
        # functional id: same answer; any other id is not ours and stays unanswered
        self.assertEqual(request(func, bytes([0x3E, 0x00]))[0][0], resp)
        for foreign in (0x7E0, 0x7DF, 0x611, 0x6DF, 0x100):
            if foreign not in (phys, func):
                with self.subTest(id=hex(foreign)):
                    self.assertEqual(request(foreign, bytes([0x3E, 0x00])), [])
        # flow control of a segmented request announces the project's block size and separation time
        self.assertEqual(request(phys, b""), [])            # empty frame, no state left behind
        with self.bl.lock:
            first = bytes([0x10, 0x0A, 0x22, 0xF1, 0x80, 0x00, 0x00, 0x00])
            buf = (ctypes.c_uint8 * 8).from_buffer_copy(first)
            self.bl.dll.host_can_rx(ctypes.c_uint32(phys), buf, ctypes.c_uint8(8))
        self.run_ms(5)
        cid_out, length, buf = ctypes.c_uint32(), ctypes.c_uint8(), (ctypes.c_uint8 * 8)()
        self.assertTrue(self.bl.dll.host_can_tx(ctypes.byref(cid_out), buf, ctypes.byref(length)))
        self.assertEqual((cid_out.value, bytes(buf[:3])),
                         (resp, bytes([0x30, CFG["BL_ISOTP_BLOCK_SIZE"], CFG["BL_ISOTP_STMIN_MS"]])))
        self.assertEqual(bytes(buf[3:8]), bytes([pad] * 5))

    def test_boot_software_identification_carries_the_number_of_modules(self):
        """BL-22, ISO 14229-1 Annex C.1: the boot software answers DID 0xF180 with numberOfModules first.

        On the frame level, because the codec of canflash reads both forms and would hide a missing byte.
        The text itself is unchanged - the byte is added to the response, never stored in flash, which is
        what keeps the application's search (COM-11), the updater and 'canflash blimage' working.
        """
        self.power_on()
        phys, resp = CFG["BL_CAN_ID_REQUEST_PHYS"], CFG["BL_CAN_ID_RESPONSE"]
        version = canflash.bl_version().encode("ascii")
        with self.bl.lock:
            frame = bytes([0x03, 0x22, 0xF1, 0x80]).ljust(8, bytes([CFG["BL_ISOTP_PADDING"]]))
            buf = (ctypes.c_uint8 * 8).from_buffer_copy(frame)
            self.bl.dll.host_can_rx(ctypes.c_uint32(phys), buf, ctypes.c_uint8(8))
        self.run_ms(20)
        cid_out, length, buf = ctypes.c_uint32(), ctypes.c_uint8(), (ctypes.c_uint8 * 8)()
        self.assertTrue(self.bl.dll.host_can_tx(ctypes.byref(cid_out), buf, ctypes.byref(length)))
        first = bytes(buf[: length.value])
        # first frame: 0x1<length>, 0x62, F1, 80, numberOfModules = 1, then the identification text
        self.assertEqual(cid_out.value, resp)
        self.assertEqual(first[:6], bytes([0x10, 4 + len(version), 0x62, 0xF1, 0x80, 0x01]))
        self.assertEqual(first[6:8], version[:2])
        # and the whole answer through the tester: the same text as before BL-22, marked as the new form
        bus = HostBus(self.bl)
        self.addCleanup(bus.shutdown)
        with canflash.make_client(bus) as client:
            identification = canflash.read_boot_identification(client)
        self.assertEqual(str(identification), canflash.bl_version())
        self.assertEqual(identification.modules, 1)

    def test_valid_app_is_started_after_startup_window(self):
        self.install_app(make_app_image(5000))
        self.power_on()
        self.run_ms(CFG["BL_STARTUP_WINDOW_MS"] - 5)
        self.assertEqual(self.bl.dll.host_jump_address(), 0)
        self.run_ms(10)
        self.assertEqual(self.bl.dll.host_jump_address(), CFG["BL_APP_START"])

    def test_corrupt_app_is_not_started(self):
        data = make_app_image(5000)
        self.install_app(data)
        self.bl.write(CFG["BL_APP_START"] + 100, bytes([data[100] ^ 0xFF]))
        self.power_on()
        self.run_ms(1000)
        self.assertEqual(self.bl.dll.host_jump_address(), 0)

    def implausible_starts(self, good: bytes) -> dict[str, bytes]:
        """Images with a valid CRC whose start the Cortex-M families refuse (hal_app_entry_plausible(), BL-08)."""
        start = CFG["BL_APP_START"]
        return {
            "stack pointer at the RAM start": struct.pack("<I", CFG["BL_RAM_START"]) + good[4:],
            "stack pointer above the RAM": struct.pack("<I", CFG["BL_RAM_END"] + 4) + good[4:],
            "reset handler without Thumb bit": good[:4] + struct.pack("<I", start + 0x410) + good[8:],
            "reset handler behind the image": good[:4] + struct.pack("<I", start + len(good) + 1) + good[8:],
        }

    def test_application_start_refused_by_the_family_is_not_started(self):
        """BL-08: valid record and CRC are not enough - the family hook checks the start of the application."""
        good = make_app_image(5000)
        for name, data in self.implausible_starts(good).items():
            with self.subTest(name):
                self.bl.dll.host_erase_all()
                self.install_app(data)
                self.assertFalse(self.jumps_after_startup_window())
        self.bl.dll.host_erase_all()
        self.install_app(struct.pack("<I", CFG["BL_RAM_END"]) + good[4:])   # full descending: the end is allowed
        self.assertTrue(self.jumps_after_startup_window())

    def test_check_memory_refuses_an_application_start_of_another_kind(self):
        """BL-08: RID 0xFF01 writes no record for an image the family hook refuses, even with the right CRC."""
        data = self.implausible_starts(make_app_image(9000))["reset handler without Thumb bit"]
        self.power_on()
        client = self.programming_client()
        client.routine_control(canflash.RID_ERASE_MEMORY, 1, bytes([0x44]) + struct.pack(">II", CFG["BL_APP_START"], len(data)))
        client.request_download(udsoncan.MemoryLocation(CFG["BL_APP_START"], len(data), 32, 32))
        for i, off in enumerate(range(0, len(data), 1024)):
            client.transfer_data(i + 1, data[off : off + 1024])
        client.request_transfer_exit()
        rsp = client.routine_control(
            canflash.RID_CHECK_MEMORY, 1, struct.pack(">III", CFG["BL_APP_START"], len(data), zlib.crc32(data))
        )
        self.assertEqual(rsp.service_data.routine_status_record, b"\x01")
        self.assertEqual(self.bl.read(CFG["BL_APP_RECORD_ADDR"], 4), b"\xff\xff\xff\xff")

    def test_tester_in_startup_window_keeps_bootloader(self):
        self.install_app(make_app_image(5000))
        self.power_on()
        bus = HostBus(self.bl)
        self.addCleanup(bus.shutdown)
        self.assertTrue(canflash.wait_for_bootloader(bus, 1.0))
        self.run_ms(1000)
        self.assertEqual(self.bl.dll.host_jump_address(), 0)

    def test_boot_request_enters_programming_session(self):
        self.install_app(make_app_image(5000))
        self.power_on(boot_request=True)
        self.run_ms(500)
        self.assertEqual(self.bl.dll.host_jump_address(), 0)
        client = self.client()
        client.unlock_security_access(1)

    def test_full_flash_cycle_then_app_starts(self):
        self.power_on()
        data = make_app_image(70001, seed=7)
        self.bl.dll.host_set_costs(40, 1)
        client = self.client()
        canflash.flash_application(client, data, log=lambda *a, **k: None)
        client.ecu_reset(udsoncan.services.ECUReset.ResetType.hardReset)
        time.sleep(0.05)
        self.bl.dll.host_poll(ctypes.c_uint32(0))
        self.assertEqual(self.bl.dll.host_reset_requested(), 1)
        self.assertEqual(self.bl.read(CFG["BL_APP_START"], len(data)), data)
        expected = canflash.app_record_full(data)
        self.assertEqual(self.bl.read(CFG["BL_APP_RECORD_ADDR"], len(expected)), expected)
        pending = [f for d, i, f, t in self.bl.log if d == "tx" and f[1:4] == bytes([0x7F, 0x31, 0x78])]
        self.assertTrue(pending, "erase of many sectors must use response pending")

        self.power_on()
        self.run_ms(CFG["BL_STARTUP_WINDOW_MS"] + 10)
        self.assertEqual(self.bl.dll.host_jump_address(), CFG["BL_APP_START"])

    # ---------------------------------------------- BL-25: maximum block length over the real transport path

    def async_tick(self):
        """Clock model of a target (bl_host.c, BL-25): the 1-ms tick falls between two readings of one poll."""
        self.bl.dll.host_set_async_tick(1)
        self.addCleanup(self.bl.dll.host_set_async_tick, 0)

    def test_download_with_maximum_block_length_and_asynchronous_tick(self):
        """The board finding of BL-25: canflash sends every block with the maximum length the bootloader announces
        (1026 bytes, 147 CAN frames), through ISO-TP (isotp.c), the transport interface and the UDS core. With a
        tick that can fall inside a poll, the ISO-TP receiver before BL-25 dropped such a message without a word:
        the frame was stamped with a newer reading than the one its timer compared against, and the unsigned
        difference looked like an expired N_Cr."""
        self.power_on()
        self.async_tick()
        data = make_app_image(12 * 1024 + 123, seed=25)
        client = self.client()
        canflash.flash_application(client, data, log=lambda *a, **k: None)
        self.assertEqual(self.bl.read(CFG["BL_APP_START"], len(data)), data)

        announced = [f for d, i, f, t in self.bl.log if d == "tx" and f[1:3] == bytes([0x74, 0x20])]
        self.assertEqual(len(announced), 1)
        max_len = (announced[0][3] << 8) | announced[0][4]
        blocks = [((f[0] & 0x0F) << 8) | f[1] for d, i, f, t in self.bl.log
                  if d == "rx" and (f[0] >> 4) == 1 and f[2] == 0x36]
        self.assertGreater(len(blocks), 2, "the image has to need several blocks")
        self.assertEqual(set(blocks[:-1]), {max_len}, "every block but the last one uses the announced length")
        self.assertEqual(sum(n - 2 for n in blocks), len(data))

    def send_segmented(self, message: bytes) -> list[bytes]:
        """One request as ISO-TP first frame and consecutive frames, one poll after every frame (each with its
        own asynchronous tick); returns the frames the bootloader sent meanwhile."""
        pad = CFG["BL_ISOTP_PADDING"]
        cid = ctypes.c_uint32(CFG["BL_CAN_ID_REQUEST_PHYS"])
        frames = [bytes([0x10 | (len(message) >> 8), len(message) & 0xFF]) + message[:6]]
        rest = message[6:]
        for sn in range(1, 1 + (len(rest) + 6) // 7):
            frames.append((bytes([0x20 | (sn & 0x0F)]) + rest[(sn - 1) * 7 : sn * 7]).ljust(8, bytes([pad])))
        sent = []
        for frame in frames:
            with self.bl.lock:
                self.bl.dll.host_can_rx(cid, (ctypes.c_uint8 * 8).from_buffer_copy(frame), ctypes.c_uint8(8))
                self.bl.dll.host_poll(ctypes.c_uint32(0))
            sent.extend(self.tx_frames())
        return sent

    def tx_frames(self) -> list[bytes]:
        out, cid, length, buf = [], ctypes.c_uint32(), ctypes.c_uint8(), (ctypes.c_uint8 * 8)()
        while self.bl.dll.host_can_tx(ctypes.byref(cid), buf, ctypes.byref(length)):
            out.append(bytes(buf[: length.value]))
        return out

    def test_transfer_data_of_maximum_length_frame_by_frame(self):
        """Below canflash: one TransferData of the announced maximum length, frame by frame, with the tick at
        every reading position of a poll in turn. It must be answered - first 0x78, then 0x76."""
        self.power_on()
        client = self.programming_client()
        location = udsoncan.MemoryLocation(CFG["BL_APP_START"], 4096, 32, 32)
        client.routine_control(canflash.RID_ERASE_MEMORY, 1,
                               bytes([0x44]) + struct.pack(">II", CFG["BL_APP_START"], 4096))
        max_len = client.request_download(location).service_data.max_length
        client.close()                                  # its receive thread would take the answers away
        self.async_tick()
        block = bytes(random.Random(25).getrandbits(8) for _ in range(max_len - 2))
        sent = self.send_segmented(bytes([0x36, 0x01]) + block)
        self.run_ms(50)
        sent += self.tx_frames()
        # clear to send after the first frame and, with a block size, after every block - never wait or overflow
        flow_control = bytes([0x30, CFG["BL_ISOTP_BLOCK_SIZE"], CFG["BL_ISOTP_STMIN_MS"]])
        self.assertEqual(sent[0], flow_control.ljust(8, bytes([CFG["BL_ISOTP_PADDING"]])))
        self.assertEqual({f for f in sent if (f[0] >> 4) == 3}, {sent[0]})
        answers = [f[1 : 1 + f[0]] for f in sent if (f[0] >> 4) == 0]
        self.assertEqual(answers, [bytes([0x7F, 0x36, 0x78]), bytes([0x76, 0x01])],
                         "TransferData of the maximum length has to be answered")
        self.assertEqual(self.bl.read(CFG["BL_APP_START"], len(block)), block)

    def test_wrong_crc_does_not_validate(self):
        self.power_on()
        data = make_app_image(9000)
        client = self.programming_client()
        client.routine_control(canflash.RID_ERASE_MEMORY, 1, bytes([0x44]) + struct.pack(">II", CFG["BL_APP_START"], len(data)))
        client.request_download(udsoncan.MemoryLocation(CFG["BL_APP_START"], len(data), 32, 32))
        for i, off in enumerate(range(0, len(data), 1024)):
            client.transfer_data(i + 1, data[off : off + 1024])
        client.request_transfer_exit()
        rsp = client.routine_control(
            canflash.RID_CHECK_MEMORY, 1, struct.pack(">III", CFG["BL_APP_START"], len(data), zlib.crc32(data) ^ 1)
        )
        self.assertEqual(rsp.service_data.routine_status_record, b"\x01")
        self.assertEqual(self.bl.read(CFG["BL_APP_RECORD_ADDR"], 4), b"\xff\xff\xff\xff")

    def test_security_required_and_lockout(self):
        self.power_on()
        client = self.client()
        client.change_session(2)
        with self.assertRaises(NegativeResponseException) as ctx:
            client.request_download(udsoncan.MemoryLocation(CFG["BL_APP_START"], 16, 32, 32))
        self.assertEqual(ctx.exception.response.code, 0x33)

        client.set_config("security_algo", lambda level, seed, params: bytes(canflash.KEY_LEN))
        codes = []
        for _ in range(4):
            try:
                client.unlock_security_access(1)
            except NegativeResponseException as e:
                codes.append(e.response.code)
        self.assertEqual(codes, [0x35, 0x35, 0x36, 0x37])

    def test_wrong_block_sequence_and_range_checks(self):
        self.power_on()
        client = self.programming_client()
        with self.assertRaises(NegativeResponseException) as ctx:
            client.routine_control(canflash.RID_ERASE_MEMORY, 1, bytes([0x44]) + struct.pack(">II", 0x0, 0x1000))
        self.assertEqual(ctx.exception.response.code, 0x31)
        with self.assertRaises(NegativeResponseException) as ctx:
            client.request_download(udsoncan.MemoryLocation(CFG["BL_APP_END"], 16, 32, 32))
        self.assertEqual(ctx.exception.response.code, 0x70)
        client.request_download(udsoncan.MemoryLocation(CFG["BL_APP_START"], 64, 32, 32))
        with self.assertRaises(NegativeResponseException) as ctx:
            client.transfer_data(2, bytes(16))
        self.assertEqual(ctx.exception.response.code, 0x73)

    def test_programming_failure_reports_nrc_72(self):
        self.power_on()
        client = self.programming_client()
        client.routine_control(canflash.RID_ERASE_MEMORY, 1, bytes([0x44]) + struct.pack(">II", CFG["BL_APP_START"], 64))
        client.request_download(udsoncan.MemoryLocation(CFG["BL_APP_START"], 64, 32, 32))
        self.bl.dll.host_fail_next_program()
        with self.assertRaises(NegativeResponseException) as ctx:
            client.transfer_data(1, bytes(64))
        self.assertEqual(ctx.exception.response.code, 0x72)

    def test_session_timeout_returns_to_default(self):
        self.power_on()
        client = self.client()
        client.change_session(2)
        client.unlock_security_access(1)
        self.run_ms(CFG["BL_S3_SERVER_MS"] + 50)
        with self.assertRaises(NegativeResponseException) as ctx:
            client.request_download(udsoncan.MemoryLocation(CFG["BL_APP_START"], 16, 32, 32))
        self.assertEqual(ctx.exception.response.code, 0x7F)

    def flash_interrupted(self, between_blocks) -> str:
        """flash_application() with `between_blocks` run before the third TransferData; returns its error."""
        self.power_on()
        client = self.client()
        blocks, transfer = [], client.transfer_data

        def transfer_data(*args, **kwargs):
            blocks.append(args[0])
            if len(blocks) == 3:
                between_blocks()
            return transfer(*args, **kwargs)
        client.transfer_data = transfer_data
        with self.assertRaises(RuntimeError) as ctx:
            canflash.flash_application(client, make_app_image(5000, seed=5), log=lambda *a, **k: None)
        return str(ctx.exception)

    def test_nrc_0x24_after_s3_is_explained(self):
        """TOOL-48 (board 2026-09-21, VER-07: aborted at 10 % with NRC 0x24 on 36): the bootloader answers a
        TransferData without an open download with 0x24. When S3 ran out between two blocks, canflash says so -
        block, pause, the session the bootloader is in now - instead of a bare negative response."""
        text = self.flash_interrupted(lambda: self.run_ms(CFG["BL_S3_SERVER_MS"] + 50))
        self.assertIn("TransferData block 3", text)
        self.assertIn("NRC 0x24", text)
        self.assertIn("active session now 0x01 (default)", text)
        self.assertIn("left the programming session", text)

    def test_nrc_0x24_after_a_restart_is_explained(self):
        """The same after a restart of the ECU in the middle of the download (reset, not power loss)."""
        text = self.flash_interrupted(lambda: self.power_on())
        self.assertIn("NRC 0x24", text)
        self.assertIn("S3 expired or the ECU restarted", text)

    # ------------------------------------------------------------ BL-21: preprogramming step (ISO 14229-1 16.3.1.1)

    def raw(self, payload: bytes, functional: bool = False, ms: int = 20) -> list[bytes]:
        """One single frame request, the answers as UDS payloads (ISO-TP length byte and filler removed).

        Deliberately below udsoncan: the cases here are malformed requests and suppressed responses, which a
        client library either rejects itself or turns into a timeout."""
        pad = CFG["BL_ISOTP_PADDING"]
        cid = CFG["BL_CAN_ID_REQUEST_FUNC"] if functional else CFG["BL_CAN_ID_REQUEST_PHYS"]
        with self.bl.lock:
            frame = (bytes([len(payload)]) + payload).ljust(8, bytes([pad]))
            self.bl.dll.host_can_rx(ctypes.c_uint32(cid), (ctypes.c_uint8 * 8).from_buffer_copy(frame),
                                    ctypes.c_uint8(8))
        self.run_ms(ms)
        out, cid_out, length, buf = [], ctypes.c_uint32(), ctypes.c_uint8(), (ctypes.c_uint8 * 8)()
        while self.bl.dll.host_can_tx(ctypes.byref(cid_out), buf, ctypes.byref(length)):
            data = bytes(buf[: length.value])
            self.assertEqual(cid_out.value, CFG["BL_CAN_ID_RESPONSE"])
            out.append(data[1 : 1 + data[0]])
        return out

    def test_preprogramming_step_of_iso_14229_1_is_answered(self):
        """Table 501: the boot software has to answer 10 03, 85 02 and 28 (disable) positively.

        Before BL-21 the bootloader knew none of the three, so a conforming tester stopped in the
        preprogramming step - before it ever reached 10 02."""
        self.power_on()
        self.assertEqual(self.raw(bytes([0x10, 0x03]))[0][:2], bytes([0x50, 0x03]))
        # DID 0xF186 reports the session the tester just opened
        self.assertEqual(self.raw(bytes([0x22, 0xF1, 0x86])), [bytes([0x62, 0xF1, 0x86, 0x03])])
        self.assertEqual(self.raw(bytes([0x85, 0x02])), [bytes([0xC5, 0x02])])
        self.assertEqual(self.raw(bytes([0x28, 0x03, 0x01])), [bytes([0x68, 0x03])])
        # and the programming step still follows from there
        self.assertEqual(self.raw(bytes([0x10, 0x02]))[0][:2], bytes([0x50, 0x02]))

    def test_communication_control_subfunctions_and_negative_responses(self):
        """9.5: controlType 0x00..0x03 for every message class of Annex B.1, everything else refused."""
        self.power_on()
        for sub in (0x00, 0x01, 0x02, 0x03):
            for comm in (0x01, 0x02, 0x03, 0xF1, 0xF3):  # subnet 0 (this node) and 0xF (network of the request)
                with self.subTest(sub=hex(sub), comm=hex(comm)):
                    self.assertEqual(self.raw(bytes([0x28, sub, comm])), [bytes([0x68, sub])])
        # suppressPosRspMsgIndicationBit: accepted, but silent
        self.assertEqual(self.raw(bytes([0x28, 0x83, 0x01])), [])
        # 0x04/0x05 address the master of a sub-network, 0x40 is manufacturer specific: neither is us
        for sub in (0x04, 0x05, 0x06, 0x40, 0x60, 0x7F):
            with self.subTest(unsupported=hex(sub)):
                self.assertEqual(self.raw(bytes([0x28, sub, 0x01, 0x00, 0x01])), [bytes([0x7F, 0x28, 0x12])])
        for short in (bytes([0x28]), bytes([0x28, 0x03]), bytes([0x28, 0x03, 0x01, 0x00])):
            with self.subTest(length=short.hex()):
                self.assertEqual(self.raw(short), [bytes([0x7F, 0x28, 0x13])])
        # communicationType: 0 and the reserved bits 2/3 are no message class, 0x1..0xE no sub-network of ours
        for comm in (0x00, 0x04, 0x08, 0x0C, 0x10, 0x31, 0xE1):
            with self.subTest(communicationType=hex(comm)):
                self.assertEqual(self.raw(bytes([0x28, 0x03, comm])), [bytes([0x7F, 0x28, 0x31])])

    def test_control_dtc_setting_subfunctions_and_negative_responses(self):
        """9.8: on and off are answered, the option record has nothing to refer to without a fault memory."""
        self.power_on()
        self.assertEqual(self.raw(bytes([0x85, 0x01])), [bytes([0xC5, 0x01])])
        self.assertEqual(self.raw(bytes([0x85, 0x02])), [bytes([0xC5, 0x02])])
        self.assertEqual(self.raw(bytes([0x85, 0x82])), [])       # suppressPosRspMsgIndicationBit
        for sub in (0x00, 0x03, 0x3F, 0x40, 0x7F):
            with self.subTest(unsupported=hex(sub)):
                self.assertEqual(self.raw(bytes([0x85, sub])), [bytes([0x7F, 0x85, 0x12])])
        self.assertEqual(self.raw(bytes([0x85])), [bytes([0x7F, 0x85, 0x13])])
        self.assertEqual(self.raw(bytes([0x85, 0x02, 0xAA])), [bytes([0x7F, 0x85, 0x31])])

    def test_preprogramming_step_works_functionally(self):
        """16.3.1.1: the client may address steps (5) and (7) functionally to every server at once.

        A positive response is still sent; the negative responses the standard lets a server keep to itself
        on a functional request (0x11, 0x12, 0x31) stay silent, so one refusing server cannot drown the bus."""
        self.power_on()
        self.assertEqual(self.raw(bytes([0x10, 0x03]), functional=True)[0][:2], bytes([0x50, 0x03]))
        self.assertEqual(self.raw(bytes([0x85, 0x02]), functional=True), [bytes([0xC5, 0x02])])
        self.assertEqual(self.raw(bytes([0x28, 0x03, 0x01]), functional=True), [bytes([0x68, 0x03])])
        self.assertEqual(self.raw(bytes([0x28, 0x04, 0x01, 0x00, 0x01]), functional=True), [])   # 0x12 withheld
        self.assertEqual(self.raw(bytes([0x85, 0x03]), functional=True), [])                     # 0x12 withheld
        self.assertEqual(self.raw(bytes([0x28, 0x03, 0x00]), functional=True), [])               # 0x31 withheld
        # a wrong length is answered even functionally (0x13 is not in the withheld set)
        self.assertEqual(self.raw(bytes([0x85]), functional=True), [bytes([0x7F, 0x85, 0x13])])

    def test_extended_session_grants_no_programming_rights(self):
        """The new session of Table 501 must not become a second way into the flash (16.4.1.3).

        Everything that reads or writes programming data stays bound to the programming session and answers
        serviceNotSupportedInActiveSession (0x7F), exactly as it did before BL-21 in the default session."""
        self.power_on()
        client = self.client()
        client.change_session(3)
        self.assertEqual(client.read_data_by_identifier_first(canflash.DID_ACTIVE_SESSION), b"\x03")
        start, size = CFG["BL_APP_START"], 4096
        refused = {
            "securityAccess": lambda: client.unlock_security_access(1),
            "writeFingerprint": lambda: canflash.write_fingerprint(client, "hosttest", log=lambda *a, **k: None),
            "readFingerprint": lambda: client.read_data_by_identifier_first(canflash.DID_FINGERPRINT),
            "eraseMemory": lambda: client.routine_control(
                canflash.RID_ERASE_MEMORY, 1, bytes([0x44]) + struct.pack(">II", start, size)),
            "requestDownload": lambda: client.request_download(
                udsoncan.MemoryLocation(start, 64, address_format=32, memorysize_format=32)),
        }
        for name, call in refused.items():
            with self.subTest(service=name), self.assertRaises(NegativeResponseException) as ctx:
                call()
            self.assertEqual(ctx.exception.response.code, 0x7F, name)
        # and the application is untouched: nothing was erased on the way
        self.assertEqual(self.bl.erased(), [])

    def test_extended_session_holds_the_jump_until_s3_expires(self):
        """A tester in the preprogramming step must not lose the server to the application mid-sequence."""
        self.install_app(make_app_image(5000))
        self.assertTrue(self.jumps_after_startup_window())   # without a session the jump happens as before
        self.power_on()
        self.assertEqual(self.raw(bytes([0x10, 0x03]))[0][:2], bytes([0x50, 0x03]))
        self.run_ms(CFG["BL_STARTUP_WINDOW_MS"] + 50)
        self.assertEqual(self.bl.dll.host_jump_address(), 0, "the extended session has to hold the bootloader")
        self.run_ms(CFG["BL_S3_SERVER_MS"] + 50)             # tester gone: S3 releases the session
        self.assertEqual(self.bl.dll.host_jump_address(), CFG["BL_APP_START"])

    def test_flash_real_application_hex(self):
        # build directory of the eval board demo: NX_BUILD_DIR, else tools/test's {build}/demo or the preset's
        # binaryDir (tools/test/build_dirs.py, PLAT-07)
        if build_dirs is not None:
            build_dir = build_dirs.demo_build_dir(ROOT)
        elif os.environ.get("NX_BUILD_DIR"):
            build_dir = Path(os.environ["NX_BUILD_DIR"])
        else:
            self.skipTest("no build directory (NX_BUILD_DIR) in a copy without tools/test")
        build = build_dir / "ecu" / "application" / "RelWithDebInfo"
        app_hex, record_hex = build / "app.ecu.hex", build / "app.ecu.record.hex"
        if self.LAYOUT is not None or self.PROJECT != EVAL_PROJECT:
            self.skipTest("the application is built for the eval board layout only")
        if not app_hex.exists():
            self.skipTest("application not built")
        self.assertEqual(canflash.find_layout(app_hex), build_dir / canflash.LAYOUT_IN_BUILD)
        data = canflash.load_app_image(app_hex)
        self.power_on()
        client = self.client()
        canflash.flash_application(client, data, log=lambda *a, **k: None)

        from intelhex import IntelHex

        built = IntelHex(str(record_hex)).tobinstr(start=CFG["BL_APP_RECORD_ADDR"], size=56)
        # the 24 bytes a bootloader from before BL-02 reads must stay identical to the post-build record
        self.assertEqual(self.bl.read(CFG["BL_APP_RECORD_ADDR"], 24), built[:24])
        self.assertEqual(built[:24], canflash.app_record(data))
        # the compatibility block of the post-build record belongs to the build's own generated layout (since
        # COM-11 the target build knows its BL_TARGET_ID as well, so both identities agree)
        own = selected()
        try:
            canflash.use_layout(canflash.load_build(build_dir / canflash.LAYOUT_IN_BUILD))
            self.assertEqual(built[24:], canflash.compat_block())
        finally:
            canflash.use_layout(own)
        self.assertEqual(self.bl.read(CFG["BL_APP_RECORD_ADDR"] + 24, 32), canflash.compat_block())
        self.assertTrue(self.jumps_after_startup_window())

    def test_record_tool_matches_target_record(self):
        data = make_app_image(1234)
        record = canflash.app_record_full(data)
        self.assertEqual(len(record), 56)
        magic, start, size, crc, rcrc, reserved = struct.unpack("<IIIIII", record[:24])
        self.assertEqual((magic, start, size, crc), (canflash.RECORD_MAGIC, CFG["BL_APP_START"], 1234, zlib.crc32(data)))
        self.assertEqual((rcrc, reserved), (zlib.crc32(record[:16]), 0xFFFFFFFF))
        self.install_app(data)
        self.assertTrue(self.jumps_after_startup_window())

    # ------------------------------------------------------------------ PLAT-16: sector table instead of a mask

    def app_sectors(self) -> list[tuple[int, int]]:
        """(start, size) of the sectors of the application area, from the table the core itself uses."""
        return [(s, n) for s, n in self.bl.sectors() if CFG["BL_APP_START"] <= s < CFG["BL_APP_END"]]

    def record_sector(self) -> tuple[int, int]:
        return self.bl.sector_at(CFG["BL_APP_RECORD_ADDR"])[:2]

    def programming_client(self):
        client = self.client()
        client.change_session(2)
        client.unlock_security_access(1)
        self.fingerprint(client)
        return client

    def test_sector_table_covers_the_flash_region(self):
        """Self check of the generated table (bl_sector_map_ok) and the invariants the core relies on."""
        self.assertEqual(self.bl.dll.host_sector_map_ok(), 1)
        sectors = self.bl.sectors()
        self.assertEqual(sectors[0][0], CFG["BL_FLASH_START"])
        self.assertEqual(sectors[-1][0] + sectors[-1][1], CFG["BL_FLASH_START"] + CFG["BL_FLASH_SIZE"])
        for (start, size), (following, _) in zip(sectors, sectors[1:]):
            self.assertEqual(start + size, following, "sectors without gap or overlap, in address order")
        if CFG["BL_SECTOR_SIZE"]:
            self.assertEqual({size for _, size in sectors}, {CFG["BL_SECTOR_SIZE"]},
                             "BL_SECTOR_SIZE is the one sector size, or 0 for unequal sectors")
        for name in ("BL_APP_START", "BL_APP_RECORD_ADDR"):
            self.assertEqual(self.bl.sector_at(CFG[name])[0], CFG[name], f"{name} on a sector boundary")
        # the record sector must not lie inside the application area: erasing the application would take it with it
        start, size = self.record_sector()
        self.assertTrue(start >= CFG["BL_APP_END"] or (start + size) <= CFG["BL_APP_START"],
                        f"record sector 0x{start:X}+0x{size:X} inside the application area")

    def test_erase_follows_the_sector_table(self):
        """The core steps from sector to sector with the size from the table, not with a fixed sector size."""
        self.power_on()
        client = self.programming_client()
        sectors = self.app_sectors()
        tail = sectors[-3:]
        start = tail[0][0] + 1              # inside the sector: the range rounds down to its start
        self.bl.dll.host_clear_erase_log()
        client.routine_control(canflash.RID_ERASE_MEMORY, 1,
                               bytes([0x44]) + struct.pack(">II", start, CFG["BL_APP_END"] - start))
        self.assertEqual(self.bl.erased(), [self.record_sector()] + tail)

    def test_erase_of_one_byte_erases_exactly_its_sector(self):
        """A one byte range erases exactly the sector it lies in - with the mask this was always BL_SECTOR_SIZE."""
        self.power_on()
        client = self.programming_client()
        sectors = self.app_sectors()
        for start, size in (sectors[0], sectors[-1]):
            with self.subTest(sector=hex(start)):
                self.bl.dll.host_clear_erase_log()
                client.routine_control(canflash.RID_ERASE_MEMORY, 1,
                                       bytes([0x44]) + struct.pack(">II", start + 1, 1))
                self.assertEqual(self.bl.erased(), [self.record_sector(), (start, size)])

    def test_bootloader_area_and_record_sector_are_protected(self):
        """Neither the bootloader area nor the record sector can be reached with an erase or a download."""
        self.power_on()
        client = self.programming_client()
        before = self.bl.read(CFG["BL_FLASH_START"], 64)
        boot_sectors = [(s, n) for s, n in self.bl.sectors() if s < CFG["BL_APP_START"]]
        for start, size in (boot_sectors[0], boot_sectors[-1]):
            with self.subTest(start=hex(start)):
                with self.assertRaises(NegativeResponseException) as ctx:
                    client.routine_control(canflash.RID_ERASE_MEMORY, 1,
                                           bytes([0x44]) + struct.pack(">II", start, size))
                self.assertEqual(ctx.exception.response.code, 0x31)
        with self.assertRaises(NegativeResponseException) as ctx:   # nothing may reach beyond the application area
            client.request_download(udsoncan.MemoryLocation(CFG["BL_APP_END"] - 8, 16, 32, 32))
        self.assertEqual(ctx.exception.response.code, 0x70)
        self.assertEqual(self.bl.read(CFG["BL_FLASH_START"], 64), before)
        # the whole application area in one download is accepted
        client.request_download(udsoncan.MemoryLocation(CFG["BL_APP_START"], CFG["BL_APP_END"] - CFG["BL_APP_START"],
                                                        32, 32))

    # 0.3 x P2*: shortest pause between two 0x78 of one request (ISO 14229-2:2021 clause 9.2, Table 4, note b;
    # BL_PENDING_MIN_GAP_MS of bl_config.h is derived the same way)
    PENDING_MIN_GAP_MS = CFG["BL_P2STAR_SERVER_MS"] * 3 // 10

    def pending_times(self, sid: int) -> list[int]:
        return [t for d, i, f, t in self.bl.log if d == "tx" and f[1:4] == bytes([0x7F, sid, 0x78])]

    def assert_pending_spacing(self, sid: int):
        """BL-25: every 0x78 of one request at least 0.3 x P2* after the previous one, no pause beyond P2*."""
        pending = self.pending_times(sid)
        self.assertTrue(pending, "a flash job starts with a response pending")
        spacing = [b - a for a, b in zip(pending, pending[1:])]
        self.assertTrue(all(s >= self.PENDING_MIN_GAP_MS for s in spacing),
                        f"0x78 every {spacing} ms, at least {self.PENDING_MIN_GAP_MS} ms apart")
        times = [t for d, i, f, t in self.bl.log if d == "tx"]
        gaps = [b - a for a, b in zip(times, times[1:])]
        self.assertLess(max(gaps, default=0), CFG["BL_P2STAR_SERVER_MS"],
                        f"a pause of {max(gaps)} ms exceeds P2* = {CFG['BL_P2STAR_SERVER_MS']} ms, gaps {gaps}")

    def erase_application_area(self, sector_ms: int, size: int | None = None) -> tuple[int, int, list[int]]:
        """Erases the application area (the whole one, or `size` bytes from its start) at `sector_ms` per sector;
        checks the spacing of the 0x78 and returns the number of sectors, the job's duration and the times of its
        0x78 (ms)."""
        self.power_on()
        client = self.programming_client()
        self.bl.dll.host_set_costs(ctypes.c_uint32(sector_ms), ctypes.c_uint32(1))
        self.addCleanup(self.bl.dll.host_set_costs, ctypes.c_uint32(20), ctypes.c_uint32(1))
        start, whole = CFG["BL_APP_START"], CFG["BL_APP_END"] - CFG["BL_APP_START"]
        size = whole if size is None else min(size, whole)
        # the sectors of the range, plus the sector of the validity record if it lies outside
        sectors = sum(1 for a, n in self.app_sectors() if a < start + size) + 1
        self.bl.log.clear()
        client.routine_control(canflash.RID_ERASE_MEMORY, 1, bytes([0x44]) + struct.pack(">II", start, size))
        self.assert_pending_spacing(0x31)
        # the simulated clock also runs with real time between the polls, so the job may take longer than
        # sector_ms per sector; what counts is one 0x78 at the start and at most one per 0.3 x P2* of the job's
        # real duration
        times = [t for d, i, f, t in self.bl.log if d == "tx"]
        pending = self.pending_times(0x31)
        allowed = 1 + (times[-1] - times[0]) // self.PENDING_MIN_GAP_MS
        self.assertLessEqual(len(pending), allowed,
                             f"{sectors} sectors of {sector_ms} ms in {times[-1] - times[0]} ms")
        if sectors >= 8:  # layouts with many small sectors: the bound must really be below one 0x78 per sector
            self.assertLess(allowed, sectors // 4, "the job has to be short against the number of its sectors")
        return sectors, times[-1] - times[0], pending

    def test_fast_sector_erases_get_one_response_pending(self):
        """The board finding of BL-25: the core before BL-25 answered every sector with its own 0x78 - 43 frames
        for 41 sectors within about 0.18 s, one every 4 ms (bl_transfer_fail.asc; the application area had been
        erased by the attempts before, TOOL-48). At 4 ms per sector the whole application area fits into
        0.3 x P2* here, so the job start's 0x78 is the only one."""
        self.erase_application_area(4)

    # the application of the board run (TOOL-48): canflash erased 0x00008000 .. 0x00031E90
    BOARD_IMAGE_SIZE = 0x31E90 - 0x8000

    def test_measured_sector_erase_time_of_the_s32k144(self):
        """TOOL-48: the S32K144 erased the sectors of a programmed application of 171664 bytes in 1.73 s, about
        42 ms per 4-KB sector (board 2026-09-21 abends, bootloader 0.3.0), and sent 2 x 7F 31 78, at 0 and at
        1.73 s - the job outlasted 0.3 x P2*, a second one is what ISO 14229-2 allows. The host model with the
        measured cost and the board's image: spacing and bound hold in every layout, never one per sector."""
        sectors, took, pending = self.erase_application_area(42, self.BOARD_IMAGE_SIZE)
        self.assertGreaterEqual(took, sectors * 42, "the modelled erase time has to be in the job")
        if self.LAYOUT is None and self.PROJECT == EVAL_PROJECT:
            # the board's case: 42 sectors of the image and the record's, about 1.8 s > 0.3 x P2* = 1.5 s
            self.assertEqual(len(pending), 2, f"{sectors} sectors in {took} ms, 0x78 at {pending}")

    def test_measured_sector_erase_time_whole_application_area(self):
        """TOOL-48: the whole application area at the measured 42 ms per sector (S32K148: 375 sectors, about
        16 s) - several 0x78, each at least 0.3 x P2* after the previous one and none more than P2* apart."""
        sectors, took, pending = self.erase_application_area(42)
        self.assertGreaterEqual(took, sectors * 42, "the modelled erase time has to be in the job")

    def test_response_pending_covers_long_sector_erases(self):
        """One sector erase may use up most of P2* (STM32F4: 128 KB, seconds): the core predicts the next erase
        from the cost per KB it measured and from the size of the sector, and sends 0x78 in time before it - but
        not more often than 0.3 x P2* (BL-25; before, PLAT-16 sent one before every sector)."""
        self.power_on()
        client = self.programming_client()
        # 10 ms + 32 ms/KB: a 4-KB sector costs 138 ms, a 128-KB sector 4106 ms - just inside P2* = 5000 ms
        self.bl.dll.host_set_erase_cost(ctypes.c_uint32(10), ctypes.c_uint32(32))
        self.addCleanup(self.bl.dll.host_set_costs, ctypes.c_uint32(20), ctypes.c_uint32(1))
        tail = self.app_sectors()[-3:]
        start = tail[0][0]
        self.bl.log.clear()
        self.bl.dll.host_clear_erase_log()
        client.routine_control(canflash.RID_ERASE_MEMORY, 1,
                               bytes([0x44]) + struct.pack(">II", start, CFG["BL_APP_END"] - start))
        erased = self.bl.erased()
        self.assertEqual(erased, [self.record_sector()] + tail)
        self.assert_pending_spacing(0x31)

    # ------------------------------------------------------------------ BL-02: compatibility application <-> target

    def test_compatibility_block_of_the_record(self):
        """Format and values of the compatibility block; the first 24 bytes stay the record of milestone 2."""
        data = make_app_image(1234)
        block = canflash.compat_block()
        magic, version, size, target, layout, app_end, record_addr = struct.unpack("<7I", block[:28])
        self.assertEqual((magic, version, size), (canflash.COMPAT_MAGIC, canflash.COMPAT_VERSION, 32))
        self.assertEqual(len(block), 32)
        self.assertEqual(struct.unpack("<I", block[28:])[0], zlib.crc32(block[:28]))
        self.assertEqual((app_end, record_addr), (CFG["BL_APP_END"], CFG["BL_APP_RECORD_ADDR"]))
        self.assertEqual(layout, canflash.layout_id())
        self.assertEqual(canflash.app_record_full(data)[:24], canflash.app_record(data))
        self.assertEqual(target, CFG[canflash.TARGET_KEY])
        self.assertNotEqual(target, 0, "the build must know its board/derivative (generated BL_TARGET_ID)")

    def test_identity_of_generated_header_and_project_agree(self):
        """The BL_TARGET_ID of the generated layout header is the identity of board and derivative of the project."""
        if self.LAYOUT is not None:
            self.skipTest("synthetic layout without a project profile (PLAT-16)")
        if not self.PROJECT.exists():
            self.skipTest("source copy without the project files (mutation test)")
        self.assertEqual(CFG[canflash.TARGET_KEY], canflash.target_id(*self.names()))
        if self.MCUDB is None:      # the synthetic profile needs its own derivative database
            self.assertEqual(canflash.target_from_project(self.PROJECT)[canflash.TARGET_KEY], CFG[canflash.TARGET_KEY])

    def test_target_compatibility_did(self):
        """DID 0xF1A0 reports the target's identity and the state of the installed application."""
        target = self.target_compat()
        image = canflash.image_compat()
        self.assertEqual({k: target[k] for k in image}, image)
        self.assertEqual(target["record"], 0xFF)            # empty flash: no validity record
        self.assertEqual(canflash.compat_differences(image, target), [])

        self.install_app(make_app_image(3000))
        self.assertEqual(self.target_compat()["record"], 0x00)

    def test_record_of_another_board_is_not_started(self):
        data = make_app_image(3000)
        self.install_app(data, record_with(data, target=canflash.target_id("otherboard", "OTHERMCU")))
        self.assertFalse(self.jumps_after_startup_window())
        self.assertEqual(self.target_compat()["record"], 0x02)

    def test_record_with_other_addresses_is_not_started(self):
        data = make_app_image(3000)
        # a different address, layout independent (BL_SECTOR_SIZE is 0 for a flash with unequal sectors, PLAT-16)
        for field, value in (("app_end", CFG["BL_APP_END"] - 0x1000),
                             ("record_addr", CFG["BL_APP_RECORD_ADDR"] - 0x1000)):
            with self.subTest(field=field):
                self.bl.dll.host_erase_all()
                self.install_app(data, record_with(data, **{field: value}))
                self.assertFalse(self.jumps_after_startup_window())
                self.assertEqual(self.target_compat()["record"], 0x04)

    def test_record_with_other_memory_layout_is_not_started(self):
        data = make_app_image(3000)
        self.install_app(data, record_with(data, layout=canflash.layout_id() ^ 0xA5A5A5A5))
        self.assertFalse(self.jumps_after_startup_window())
        self.assertEqual(self.target_compat()["record"], 0x03)

    def test_damaged_compatibility_block_is_not_started(self):
        data = make_app_image(3000)
        for name, record in (("CRC", record_with(data, crc=0xDEADBEEF)),
                             ("version", record_with(data, version=2)),
                             ("size", record_with(data, size=40))):
            with self.subTest(name):
                self.bl.dll.host_erase_all()
                self.install_app(data, record)
                self.assertFalse(self.jumps_after_startup_window())
                self.assertEqual(self.target_compat()["record"], 0x01)

    def test_record_without_compatibility_block_is_not_started(self):
        """BL-36 (SYS69-S10): the 24-byte record of an application flashed before BL-02 is reported (0x10) but no
        longer started - without the block the target and layout check of SM-51 would be skipped."""
        data = make_app_image(3000)
        self.install_app(data, canflash.app_record(data))
        self.assertEqual(self.bl.read(CFG["BL_APP_RECORD_ADDR"] + 24, 8), b"\xff" * 8)
        self.assertFalse(self.jumps_after_startup_window())
        self.assertEqual(self.target_compat()["record"], 0x10)

    def test_mismatching_image_is_rejected_before_erasing(self):
        """canflash checks the image against the target and aborts before the first erase."""
        data = make_app_image(9000, seed=3)
        self.install_app(data)
        # BL-10: with the boot request, not in the 200 ms startup window - under load the first request came after
        # it, the bootloader had started the installed application and the test ended in a timeout
        self.power_on(boot_request=True)
        client = self.client()
        with self.foreign_build():
            with self.assertRaises(RuntimeError) as ctx:
                canflash.flash_application(client, make_app_image(5000, seed=4), log=lambda *a, **k: None)
        self.assertIn("do not match", str(ctx.exception))
        self.assertIn("board/derivative", str(ctx.exception))
        # nothing erased or written: the installed application still starts
        self.assertEqual(self.bl.read(CFG["BL_APP_START"], len(data)), data)
        self.assertTrue(self.jumps_after_startup_window())

    # -------------------------------------------- TOOL-53: after a rejected image, and the bootloader's own refusal

    def reset_seen(self) -> bool:
        """The bootloader asked for a reset (hal_reset); 11 01 is answered first, the reset follows."""
        time.sleep(0.05)
        with self.bl.lock:
            self.bl.dll.host_poll(ctypes.c_uint32(0))
        return self.bl.dll.host_reset_requested() == 1

    def rejected(self, **kwargs) -> tuple[str, list[str], bytes]:
        """Flash an image of another board onto a target with an installed application; (message, log, image)."""
        data = make_app_image(9000, seed=53)
        self.install_app(data)
        self.power_on(boot_request=True)
        client = self.client()
        lines: list[str] = []
        with self.foreign_build():
            with self.assertRaises(canflash.ImageRejected) as ctx:
                canflash.flash_application(client, make_app_image(5000, seed=54),
                                           log=lambda *a, **k: lines.append(" ".join(map(str, a))), **kwargs)
        self.assertNotIn(canflash.RID_ERASE_MEMORY, routine_requests(self.bl.log))
        return str(ctx.exception), lines, data

    def test_rejected_image_resets_the_ecu(self):
        """Board 2026-09-22: after the refusal the ECU stayed in the bootloader (F181 -> 0x31) until 11 01 came."""
        message, _, data = self.rejected()
        self.assertIn("do not match", message)
        self.assertIn("ECU reset (11 01)", message)
        self.assertTrue(self.reset_seen(), "canflash must reset the ECU after a rejected image")
        self.assertEqual(self.bl.read(CFG["BL_APP_START"], len(data)), data)
        self.assertTrue(self.jumps_after_startup_window())

    def test_rejected_image_stays_in_the_bootloader_on_request(self):
        message, _, _ = self.rejected(reset_on_reject=False)
        self.assertIn("stays in the bootloader (--stay-in-bootloader)", message)
        self.assertFalse(self.reset_seen())
        self.assertEqual(self.bl.dll.host_jump_address(), 0)

    def test_bootloader_itself_rejects_without_the_local_check(self):
        """BL-24 c on the target: --skip-local-compat-check hands the foreign image to routine 0xF1A0, and the
        bootloader's own refusal (status 0x02, board/derivative) ends the run before the erase."""
        message, lines, data = self.rejected(local_check=False)
        self.assertIn("Bootloader rejects the image", message)
        self.assertIn(f"routine 0x{canflash.RID_CHECK_COMPAT:04X} status 0x02", message)
        self.assertIn("ECU reset (11 01)", message)
        self.assertTrue(any("WARNING (--skip-local-compat-check)" in line and "board/derivative" in line
                            for line in lines), lines)
        self.assertIn(canflash.RID_CHECK_COMPAT, routine_requests(self.bl.log))
        self.assertTrue(self.reset_seen())
        self.assertEqual(self.bl.read(CFG["BL_APP_START"], len(data)), data)

    def test_without_the_local_check_a_matching_image_is_flashed(self):
        """The option changes nothing for an image of this target."""
        self.power_on()
        data = make_app_image(3000, seed=55)
        canflash.flash_application(self.client(), data, log=lambda *a, **k: None, local_check=False)
        self.assertEqual(self.bl.read(CFG["BL_APP_START"], len(data)), data)

    def test_bootloader_blocks_programming_after_a_declared_mismatch(self):
        """Even a tester that ignores the answer of routine 0xF1A0 (0xFF02 before BL-24) cannot erase or
        download."""
        self.power_on()
        client = self.programming_client()
        image = canflash.image_compat()
        wrong = bytes([image["version"]]) + struct.pack(">IIIII", image["target"] ^ 0xFF, image["layout"],
                                                        image["app_start"], image["app_end"], image["record_addr"])
        rsp = client.routine_control(canflash.RID_CHECK_COMPAT, 1, wrong)
        self.assertEqual(rsp.service_data.routine_status_record, b"\x02")
        for name, call in (("erase", lambda: client.routine_control(
                                canflash.RID_ERASE_MEMORY, 1,
                                bytes([0x44]) + struct.pack(">II", CFG["BL_APP_START"], 64))),
                           ("download", lambda: client.request_download(
                                udsoncan.MemoryLocation(CFG["BL_APP_START"], 64, 32, 32)))):
            with self.subTest(name):
                with self.assertRaises(NegativeResponseException) as ctx:
                    call()
                self.assertEqual(ctx.exception.response.code, 0x22)
        # a matching declaration releases programming again
        ok = bytes([image["version"]]) + struct.pack(">IIIII", image["target"], image["layout"], image["app_start"],
                                                     image["app_end"], image["record_addr"])
        self.assertEqual(client.routine_control(canflash.RID_CHECK_COMPAT, 1, ok)
                         .service_data.routine_status_record, b"\x00")
        client.request_download(udsoncan.MemoryLocation(CFG["BL_APP_START"], 64, 32, 32))

    # -------------------------------------------- BL-24: compatibility check in the system supplier range

    def declared_mismatch_blocks_until_released(self, declare: int, release: int):
        """Mismatch declared with routine `declare`: erase refused; released with routine `release`."""
        self.power_on()
        client = self.programming_client()
        rsp = client.routine_control(declare, 1, compat_record(target=canflash.image_compat()["target"] ^ 0xFF))
        # the answer repeats the identifier of the request
        self.assertEqual(rsp.get_payload(), bytes([0x71, 0x01, declare >> 8, declare & 0xFF, 0x02]))
        with self.assertRaises(NegativeResponseException) as ctx:
            client.routine_control(canflash.RID_ERASE_MEMORY, 1,
                                   bytes([0x44]) + struct.pack(">II", CFG["BL_APP_START"], 64))
        self.assertEqual(ctx.exception.response.code, 0x22)
        rsp = client.routine_control(release, 1, compat_record())
        self.assertEqual(rsp.get_payload(), bytes([0x71, 0x01, release >> 8, release & 0xFF, 0x00]))
        client.request_download(udsoncan.MemoryLocation(CFG["BL_APP_START"], 64, 32, 32))

    def test_compatibility_routine_in_the_supplier_range(self):
        """BL-24: the check is routine 0xF1A0 (ISO 14229-1 Annex F.1 leaves 0xF000..0xFEFF to the system supplier)
        and blocks programming after a mismatch it found, as 0xFF02 did."""
        self.assertEqual(canflash.RID_CHECK_COMPAT, 0xF1A0)
        self.assertTrue(0xF000 <= canflash.RID_CHECK_COMPAT <= 0xFEFF)
        self.declared_mismatch_blocks_until_released(canflash.RID_CHECK_COMPAT, canflash.RID_CHECK_COMPAT)

    def test_former_compatibility_routine_is_still_answered(self):
        """BL-24 transition: a tester from before BL-24 sends 0xFF02 and keeps flashing. Both identifiers are one
        check with one state - declared with the former, released with the new one."""
        self.declared_mismatch_blocks_until_released(canflash.RID_CHECK_COMPAT_FORMER, canflash.RID_CHECK_COMPAT)

    def test_mismatch_declared_with_the_new_routine_is_released_by_the_former(self):
        self.declared_mismatch_blocks_until_released(canflash.RID_CHECK_COMPAT, canflash.RID_CHECK_COMPAT_FORMER)

    def test_canflash_declares_with_the_new_routine(self):
        """This bootloader reports BL_VERSION_NUMBER in DID 0xF180: canflash sends 0xF1A0 and never 0xFF02."""
        self.power_on()
        data = make_app_image(3000, seed=24)
        canflash.flash_application(self.client(), data, log=lambda *a, **k: None)
        self.assertEqual(self.bl.read(CFG["BL_APP_START"], len(data)), data)
        rids = routine_requests(self.bl.log)
        self.assertIn(canflash.RID_CHECK_COMPAT, rids)
        self.assertNotIn(canflash.RID_CHECK_COMPAT_FORMER, rids)
        self.assertLess(rids.index(canflash.RID_CHECK_COMPAT), rids.index(canflash.RID_ERASE_MEMORY))

    def test_hex_outside_the_application_area_is_refused(self):
        """A hex file of a larger derivative must not be flashed truncated (silently cut before BL-02)."""
        from intelhex import IntelHex

        data = make_app_image(2048)
        ih = IntelHex()
        ih.frombytes(data, offset=CFG["BL_APP_START"])
        path = Path(_BUILD_DIR) / f"above_{type(self).__name__}.hex"
        ih.write_hex_file(str(path))
        self.assertEqual(canflash.load_app_image(path), data)

        ih.frombytes(b"\x01\x02\x03\x04", offset=CFG["BL_APP_END"])
        ih.write_hex_file(str(path))
        with self.assertRaises(ValueError) as ctx:
            canflash.load_app_image(path)
        self.assertIn("above the application area", str(ctx.exception))

    def test_unknown_routine_and_wrong_length(self):
        self.power_on()
        client = self.client()
        client.change_session(2)
        client.unlock_security_access(1)
        with self.assertRaises(NegativeResponseException) as ctx:
            client.routine_control(0xFF03, 1, b"")
        self.assertEqual(ctx.exception.response.code, 0x31)
        for rid in (canflash.RID_CHECK_COMPAT, canflash.RID_CHECK_COMPAT_FORMER):
            with self.subTest(rid=hex(rid)), self.assertRaises(NegativeResponseException) as ctx:
                client.routine_control(rid, 1, bytes(10))
            self.assertEqual(ctx.exception.response.code, 0x13)
        # the neighbours of both identifiers are no routines of the bootloader (BL-24)
        for rid in (0xF19F, 0xF1A1, 0xF102):
            with self.subTest(rid=hex(rid)), self.assertRaises(NegativeResponseException) as ctx:
                client.routine_control(rid, 1, compat_record())
            self.assertEqual(ctx.exception.response.code, 0x31)

    # ----------------------------------------------------------------- BL-04: identification and fingerprint

    def test_identification_dids_come_from_one_source(self):
        """DID 0xF189 is BL_VERSION_NUMBER of bl_config.h, DID 0xF191 the BL_HARDWARE_ID "<board>/<derivative>" of
        the generated layout (BL-10), whose CRC-32 is BL_TARGET_ID.

        Both are identification, not programming data: they answer in the default session, without security
        access, so `canflash info` can read them without touching the ECU."""
        self.power_on()
        client = self.client()
        self.assertEqual(canflash.read_software_version(client), canflash.BL_VERSION_NUMBER)
        hardware = canflash.read_hardware_number(client)
        self.assertEqual(hardware, canflash.TEXT[canflash.HARDWARE_KEY], "the generated layout names the same")
        self.assertEqual(canflash.hardware_identity(hardware), CFG[canflash.TARGET_KEY])
        if self.LAYOUT is not None:
            target = hostbuild.load_test_layout(self.LAYOUT)["target"]
            self.assertEqual(hardware, f"{target['board']}/{target['derivative']}")
        elif self.PROJECT.exists():
            self.assertEqual(hardware, "/".join(self.names()))
        # no literal anywhere: DID 0xF180 is prefix + this very number
        self.assertTrue(canflash.bl_version().endswith(" " + canflash.read_software_version(client)))

    def test_fingerprint_is_written_and_read_back(self):
        """DID 0xF184: what the tester wrote comes back unchanged - also after the download (RAM, BL-04)."""
        self.power_on()
        client = self.client()
        client.change_session(2)
        client.unlock_security_access(1)
        written = self.fingerprint(client, "tester-7", datetime.date(2026, 9, 17))
        self.assertEqual(len(written), canflash.FINGERPRINT_LEN)
        self.assertEqual(written[:4], bytes([0x20, 0x26, 0x09, 0x17]))    # BCD date
        self.assertEqual(canflash.read_fingerprint(client)["raw"], written)

        data = make_app_image(3000)
        canflash.flash_application(client, data, log=lambda *a, **k: None, tester="tester-7")
        value = canflash.read_fingerprint(client)
        self.assertEqual(value["tester"], "tester-7")
        self.assertEqual(value["date"], datetime.date.today())
        self.assertEqual(self.bl.read(CFG["BL_APP_START"], len(data)), data)

    def test_download_without_fingerprint_is_refused(self):
        """Erasing and downloading without DID 0xF184 answer 0x22, and nothing is erased (BL-04)."""
        data = make_app_image(3000)
        self.install_app(data)
        self.power_on(boot_request=True)
        client = self.client()
        client.change_session(2)
        client.unlock_security_access(1)
        for name, call in (("erase", lambda: client.routine_control(
                                canflash.RID_ERASE_MEMORY, 1,
                                bytes([0x44]) + struct.pack(">II", CFG["BL_APP_START"], 64))),
                           ("download", lambda: client.request_download(
                                udsoncan.MemoryLocation(CFG["BL_APP_START"], 64, 32, 32)))):
            with self.subTest(name):
                with self.assertRaises(NegativeResponseException) as ctx:
                    call()
                self.assertEqual(ctx.exception.response.code, 0x22)
        self.assertEqual(self.bl.read(CFG["BL_APP_START"], len(data)), data, "nothing may be erased")
        # with the fingerprint the same tester may program
        self.fingerprint(client)
        client.request_download(udsoncan.MemoryLocation(CFG["BL_APP_START"], 64, 32, 32))

    def test_fingerprint_of_the_wrong_length_is_refused(self):
        """A short or long fingerprint is a length error - half an entry must not be written (BL-04)."""
        self.power_on()
        client = self.client()
        client.change_session(2)
        client.unlock_security_access(1)
        for length in (canflash.FINGERPRINT_LEN - 1, canflash.FINGERPRINT_LEN + 1, 0):
            with self.subTest(length=length):
                client.set_config("data_identifiers", {canflash.DID_FINGERPRINT: canflash._FixedRaw(length)})
                with self.assertRaises(NegativeResponseException) as ctx:
                    client.write_data_by_identifier(canflash.DID_FINGERPRINT, bytes(length))
                self.assertEqual(ctx.exception.response.code, 0x13)
        client.set_config("data_identifiers", canflash.did_codecs())
        self.assertIsNone(canflash.read_fingerprint(client), "no fingerprint may have been stored")

    def test_unknown_data_identifier_is_refused(self):
        """An unknown DID answers 0x31, read as well as written (BL-04)."""
        self.power_on()
        client = self.client()
        client.change_session(2)
        client.unlock_security_access(1)
        client.set_config("data_identifiers",
                          {**canflash.did_codecs(), 0xF1FE: canflash._FixedRaw(canflash.FINGERPRINT_LEN)})
        with self.assertRaises(NegativeResponseException) as ctx:
            client.read_data_by_identifier_first(0xF1FE)
        self.assertEqual(ctx.exception.response.code, 0x31)
        with self.assertRaises(NegativeResponseException) as ctx:
            client.write_data_by_identifier(0xF1FE, bytes(canflash.FINGERPRINT_LEN))
        self.assertEqual(ctx.exception.response.code, 0x31)

    def test_fingerprint_needs_the_programming_session_and_security(self):
        """Reading and writing DID 0xF184 outside the programming session: 0x7F, without security: 0x33."""
        self.power_on()
        client = self.client()
        client.change_session(2)
        client.unlock_security_access(1)
        self.fingerprint(client)

        client.change_session(1)
        for name, call in (("read", lambda: client.read_data_by_identifier_first(canflash.DID_FINGERPRINT)),
                           ("write", lambda: client.write_data_by_identifier(canflash.DID_FINGERPRINT,
                                                                            canflash.fingerprint_bytes("x")))):
            with self.subTest(name):
                with self.assertRaises(NegativeResponseException) as ctx:
                    call()
                self.assertEqual(ctx.exception.response.code, 0x7F)
        # the session change dropped the fingerprint with the security access (BL-04)
        client.change_session(2)
        with self.assertRaises(NegativeResponseException) as ctx:
            client.write_data_by_identifier(canflash.DID_FINGERPRINT, canflash.fingerprint_bytes("x"))
        self.assertEqual(ctx.exception.response.code, 0x33)
        client.unlock_security_access(1)
        with self.assertRaises(NegativeResponseException) as ctx:
            client.read_data_by_identifier_first(canflash.DID_FINGERPRINT)
        self.assertEqual(ctx.exception.response.code, 0x22, "no fingerprint after the session change")

    # ----------------------------------------------------------------- BL-10: fingerprint in the validity record

    def test_fingerprint_survives_the_reset_in_the_record(self):
        """Routine 0xFF01 writes the session's fingerprint into the record behind the compatibility block; after the
        reset DID 0xF184 reports it from there (BL-04 kept it in RAM only)."""
        self.power_on()
        client = self.client()
        data = make_app_image(3000, seed=21)
        canflash.flash_application(client, data, log=lambda *a, **k: None, tester="tester-10")
        written = canflash.read_fingerprint(client)["raw"]
        record = self.bl.read(CFG["BL_APP_RECORD_ADDR"], 56 + canflash.FINGERPRINT_BLOCK_SIZE)
        self.assertEqual(record[:56], canflash.app_record_full(data), "the first 56 bytes stay as before BL-10")
        self.assertEqual(record[56:], canflash.fingerprint_block(written))
        client.close()                          # one tester at a time on the DLL, see target_compat()
        self.assertTrue(self.jumps_after_startup_window(), "the longer record keeps the application startable")

        self.power_on(boot_request=True)
        client = self.client()
        client.change_session(2)
        value = canflash.read_fingerprint(client)
        self.assertIsNotNone(value, "the fingerprint must survive the reset")
        self.assertEqual((value["raw"], value["tester"]), (written, "tester-10"))
        # a fingerprint written in the running session takes precedence over the installed one
        client.unlock_security_access(1)
        other = self.fingerprint(client, "tester-11")
        self.assertEqual(canflash.read_fingerprint(client)["raw"], other)

    def test_record_without_fingerprint_block(self):
        """A record written before BL-10 (erased fingerprint block) or with a damaged block has no fingerprint, and
        the application stays startable either way."""
        data = make_app_image(3000, seed=22)
        block = bytearray(canflash.fingerprint_block(canflash.fingerprint_bytes("x")))
        block[6] ^= 0x01                                                   # CRC no longer matches
        for name, tail in (("before BL-10", b""), ("damaged block", bytes(block))):
            with self.subTest(name):
                self.bl.dll.host_erase_all()
                self.install_app(data, canflash.app_record_full(data) + tail)
                self.assertTrue(self.jumps_after_startup_window())
                self.power_on(boot_request=True)
                client = self.client()
                client.change_session(2)
                self.assertIsNone(canflash.read_fingerprint(client))
                client.close()

    def names(self) -> tuple[str, str]:
        """Board and derivative name of the test's project."""
        sys.path.insert(0, str(ROOT / "tools" / "bswgen"))
        import blgen  # noqa: E402
        config = blgen.nxconfig.load(self.PROJECT, self.MCUDB or blgen.nxconfig.MCUDB)
        return config.board.board.name, config.derivative.derivative.name

    def foreign_build(self):
        """Context manager: CFG claims an image of another board (same layout)."""
        test = self

        class Foreign:
            def __enter__(self):
                self.saved = CFG[canflash.TARGET_KEY]
                CFG[canflash.TARGET_KEY] = canflash.target_id("otherboard", "OTHERMCU")
                return self

            def __exit__(self, *args):
                CFG[canflash.TARGET_KEY] = self.saved
                return False

        test.assertNotEqual(CFG[canflash.TARGET_KEY], 0)
        return Foreign()


class BootloaderOtherLayoutTest(BootloaderTest):
    """All bootloader tests again with the generated layout of testproject_bootloader (ARCH-08): code flash at
    0x08000000 with 8-KB sectors, 16-KB bootloader, one RAM region, no SBC, bootloader on the second CAN bus."""

    PROJECT, MCUDB = OTHER_PROJECT, TEST_MCUDB

    def test_layout_of_the_profile(self):
        self.assertEqual({k: hex(CFG[k]) for k in ("BL_FLASH_START", "BL_SECTOR_SIZE", "BL_APP_START", "BL_APP_END",
                                                   "BL_APP_RECORD_ADDR", "BL_RAM_START", "BL_RAM_END")},
                         {"BL_FLASH_START": "0x8000000", "BL_SECTOR_SIZE": "0x2000", "BL_APP_START": "0x8004000",
                          "BL_APP_END": "0x803c000", "BL_APP_RECORD_ADDR": "0x803e000", "BL_RAM_START": "0x20000000",
                          "BL_RAM_END": "0x20010000"})
        self.assertEqual(self.bl.start, 0x08000000)


class BootloaderS32K148Test(BootloaderTest):
    """All bootloader tests again for the second build profile of the repository (ARCH-09, no hardware):
    S32K148, 1.5 MB flash with 4-KB sectors, bootloader 0x0..0x7FFF like the eval board, other board."""

    PROJECT, MCUDB = S32K148_PROJECT, None

    def test_layout_of_the_profile(self):
        self.assertEqual({k: hex(CFG[k]) for k in ("BL_FLASH_SIZE", "BL_APP_START", "BL_APP_END",
                                                   "BL_APP_RECORD_ADDR")},
                         {"BL_FLASH_SIZE": "0x180000", "BL_APP_START": "0x8000", "BL_APP_END": "0x17e000",
                          "BL_APP_RECORD_ADDR": "0x17f000"})
        self.assertEqual(self.names(), ("buildtest_s32k148", "S32K148"))

    def test_image_of_the_eval_board_is_rejected(self):
        """The eval board application (other board, other application end) must not reach this target."""
        own = selected()
        self.power_on()
        client = self.client()
        try:
            canflash.use_layout(EVAL_LAYOUT)
            self.assertNotEqual(canflash.layout_id(), canflash.layout_id(own))
            with self.assertRaises(RuntimeError) as ctx:
                canflash.check_compatibility(client, log=lambda *a, **k: None)
        finally:
            canflash.use_layout(own)
        message = str(ctx.exception)
        self.assertIn("board/derivative", message)
        self.assertIn("application end 0x0007E000", message)
        self.assertIn("nothing was erased", message)

    def test_record_of_the_eval_board_is_not_started(self):
        """An eval board hex plus its record flashed by J-Link: the bootloader refuses to start it."""
        own = selected()
        data = make_app_image(3000)
        try:
            canflash.use_layout(EVAL_LAYOUT)
            record = canflash.app_record(data) + canflash.compat_block()
        finally:
            canflash.use_layout(own)
        self.install_app(data, record)
        self.assertFalse(self.jumps_after_startup_window())
        # board/derivative is reported first; the application end 0x7F000 differs as well
        self.assertEqual(self.target_compat()["record"], 0x02)
        self.assertEqual(struct.unpack_from("<I", record, 24 + 20)[0], EVAL_LAYOUT["BL_APP_END"])


class BootloaderS32K116Test(BootloaderTest):
    """All bootloader tests again for the smallest derivative of the focus family (PLAT-16, PLAT-11): S32K116,
    128 KB flash with 2-KB sectors, 15 KB RAM. Synthetic layout (no board profile yet, PLAT-13):
    bootloader/test/layouts/s32k116_2k.yaml."""

    PROJECT, MCUDB, LAYOUT = None, None, "s32k116_2k"

    def test_layout_of_the_profile(self):
        self.assertEqual({k: hex(CFG[k]) for k in ("BL_FLASH_START", "BL_FLASH_SIZE", "BL_SECTOR_SIZE",
                                                   "BL_APP_START", "BL_APP_END", "BL_APP_RECORD_ADDR")},
                         {"BL_FLASH_START": "0x0", "BL_FLASH_SIZE": "0x20000", "BL_SECTOR_SIZE": "0x800",
                          "BL_APP_START": "0x2000", "BL_APP_END": "0x1f000", "BL_APP_RECORD_ADDR": "0x1f800"})
        self.assertEqual(self.bl.dll.host_sector_count(), 64)
        self.assertEqual({size for _, size in self.bl.sectors()}, {0x800})


class BootloaderStm32F446Test(BootloaderTest):
    """All bootloader tests again for a flash with UNEQUAL sectors - the case BL_SECTOR_SIZE could not describe
    (PLAT-16): STM32F446RE, 4 x 16 KB + 64 KB + 3 x 128 KB at 0x08000000, validity record in sector 1, i.e.
    *below* the application area. Synthetic layout bootloader/test/layouts/stm32f446_unequal.yaml."""

    PROJECT, MCUDB, LAYOUT = None, None, "stm32f446_unequal"

    def test_layout_of_the_profile(self):
        self.assertEqual({k: hex(CFG[k]) for k in ("BL_FLASH_START", "BL_FLASH_SIZE", "BL_SECTOR_SIZE",
                                                   "BL_PHRASE_SIZE", "BL_APP_START", "BL_APP_END",
                                                   "BL_APP_RECORD_ADDR")},
                         {"BL_FLASH_START": "0x8000000", "BL_FLASH_SIZE": "0x80000", "BL_SECTOR_SIZE": "0x0",
                          "BL_PHRASE_SIZE": "0x4", "BL_APP_START": "0x800c000", "BL_APP_END": "0x8080000",
                          "BL_APP_RECORD_ADDR": "0x8004000"})
        self.assertEqual(self.bl.start, 0x08000000)

    def test_sector_table_of_the_derivative(self):
        """The table is the sector map of RM0390: 4 x 16 KB, 1 x 64 KB, 3 x 128 KB, indices 0..7."""
        self.assertEqual(self.bl.sectors(),
                         [(0x08000000, 0x4000), (0x08004000, 0x4000), (0x08008000, 0x4000), (0x0800C000, 0x4000),
                          (0x08010000, 0x10000),
                          (0x08020000, 0x20000), (0x08040000, 0x20000), (0x08060000, 0x20000)])
        # the sector index is what a STM32 HAL writes into FLASH_CR.SNB
        for address, expected in ((0x08000000, 0), (0x0800FFFF, 3), (0x08010000, 4), (0x0801FFFF, 4),
                                  (0x08020000, 5), (0x0807FFFF, 7)):
            self.assertEqual(self.bl.sector_at(address)[2], expected, hex(address))
        self.assertIsNone(self.bl.sector_at(0x08080000), "one byte behind the region")
        self.assertIsNone(self.bl.sector_at(0x07FFFFFF))

    def test_record_below_the_application_area(self):
        """The record is in sector 1 (16 KB), not in the last sector (which would cost 128 KB here)."""
        start, size = self.record_sector()
        self.assertEqual((start, size), (0x08004000, 0x4000))
        self.assertLess(start, CFG["BL_APP_START"])
        self.assertEqual(CFG["BL_APP_END"], CFG["BL_FLASH_START"] + CFG["BL_FLASH_SIZE"])
        # the application really reaches the end of the flash, and the record still survives and is written
        data = make_app_image(9000, seed=11)
        self.power_on()
        client = self.client()
        canflash.flash_application(client, data, log=lambda *a, **k: None)
        self.assertEqual(self.bl.read(CFG["BL_APP_RECORD_ADDR"], 56), canflash.app_record_full(data))
        self.power_on()
        self.run_ms(CFG["BL_STARTUP_WINDOW_MS"] + 10)
        self.assertEqual(self.bl.dll.host_jump_address(), CFG["BL_APP_START"])

    def test_erase_of_the_whole_application_uses_the_unequal_sectors(self):
        """Erasing 0x0800C000..0x0807FFFF must erase 1 x 16 KB, 1 x 64 KB and 3 x 128 KB - 5 commands, not 29.
        BL-03: sector 2 (0x08008000) is the key sector next to the record and is never erased."""
        self.power_on()
        client = self.programming_client()
        self.bl.dll.host_clear_erase_log()
        start = CFG["BL_APP_START"]
        client.routine_control(canflash.RID_ERASE_MEMORY, 1,
                               bytes([0x44]) + struct.pack(">II", start, CFG["BL_APP_END"] - start))
        self.assertEqual(self.bl.erased(),
                         [(0x08004000, 0x4000),                                   # record sector first
                          (0x0800C000, 0x4000), (0x08010000, 0x10000),
                          (0x08020000, 0x20000), (0x08040000, 0x20000), (0x08060000, 0x20000)])
        self.assertEqual(CFG["BL_KEY_ADDR"], 0x08008000)
        self.assertEqual(self.bl.read(start, 16), b"\xff" * 16)


class AppThenBootloaderBus(HostBus):
    """A running application in front of the host bootloader: it answers 10 02 like COM-04 (50 02 00 32 01 F4),
    resets and the bootloader listens only after `startup_s` (board: 78 ms). Frames meanwhile are lost.

    It also answers the preprogramming step of ISO 14229-1 16.3.1.1 (BL-21), which canflash sends before
    the handover - like the real application of ecu/uds does. Everything else it hears is lost, which is
    what the reset gap of the board does to a request."""

    APP_RESPONSE = bytes([0x06, 0x50, 0x02, 0x00, 0x32, 0x01, 0xF4, 0xCC])
    # request prefix -> response, for the requests the application answers before the handover
    PRE_PROGRAMMING = {
        b"\x02\x10\x03": bytes([0x06, 0x50, 0x03, 0x00, 0x32, 0x01, 0xF4]),   # extendedDiagnosticSession
        b"\x02\x85\x02": bytes([0x02, 0xC5, 0x02]),                           # ControlDTCSetting(off)
        b"\x03\x28\x03": bytes([0x02, 0x68, 0x03]),                           # CommunicationControl(disable)
    }

    def __init__(self, bl: HostBootloader, test: "BootloaderTest", startup_s: float = 0.078, **kwargs):
        super().__init__(bl, **kwargs)
        self.test = test
        self.startup_s = startup_s
        self.phase = "app"
        self.reset_at = 0.0
        self.pending: list[bytes] = []
        self.lost: list[bytes] = []

    def _update(self):
        if self.phase == "resetting" and time.perf_counter() >= self.reset_at + self.startup_s:
            with self.bl.lock:
                self.test.power_on(boot_request=True)
                self.last = time.perf_counter()
            self.phase = "bootloader"

    def send(self, msg, timeout=None):
        self._update()
        if self.phase == "bootloader":
            return super().send(msg, timeout)
        data = bytes(msg.data)
        ours = msg.arbitration_id == CFG["BL_CAN_ID_REQUEST_PHYS"]
        if self.phase == "app" and ours and data[:3] == b"\x02\x10\x02":
            self.pending.append(self.APP_RESPONSE)
            self.phase = "resetting"
            self.reset_at = time.perf_counter() + 0.002
        elif self.phase == "app" and ours and data[:3] in self.PRE_PROGRAMMING:
            pad = bytes([CFG["BL_ISOTP_PADDING"]])
            self.pending.append(self.PRE_PROGRAMMING[data[:3]].ljust(8, pad))
        else:
            self.lost.append(data)

    def _recv_internal(self, timeout):
        if self.pending:
            return can.Message(arbitration_id=CFG["BL_CAN_ID_RESPONSE"], data=self.pending.pop(0), is_extended_id=False), False
        self._update()
        if self.phase == "bootloader":
            return super()._recv_internal(timeout)
        time.sleep(min(timeout if timeout is not None else 0.005, 0.005))
        self._update()
        return None, False


class DuplicateAnswerBus(AppThenBootloaderBus):
    """Every 50 02 appears twice on the bus: the second copy is the answer to a request that enter_bootloader
    had already given up on. That is what a real bus does when an answer needs longer than `interval`
    (board: the bootloader answered 78 ms after the application's 50 02) - the copy is left over for whoever
    reads the bus next (COM-09, canflash.drain())."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.extra: list[can.Message] = []

    def _recv_internal(self, timeout):
        if self.extra:
            return self.extra.pop(0), False
        msg, filtered = super()._recv_internal(timeout)
        if msg is not None and msg.arbitration_id == CFG["BL_CAN_ID_RESPONSE"] \
                and bytes(msg.data)[:3] == b"\x06\x50\x02":
            self.extra.append(can.Message(arbitration_id=msg.arbitration_id, data=bytes(msg.data),
                                          is_extended_id=False))
        return msg, filtered


class EnterBootloaderTest(unittest.TestCase):
    """COM-09 finding (board, VER-board-welle2): `canflash flash` from the running application lost 27 01."""

    PROJECT, MCUDB, LAYOUT = EVAL_PROJECT, None, None
    setUpClass = classmethod(BootloaderTest.setUpClass.__func__)
    tearDownClass = classmethod(BootloaderTest.tearDownClass.__func__)
    setUp = BootloaderTest.setUp
    power_on = BootloaderTest.power_on
    install_app = BootloaderTest.install_app

    def app_bus(self, startup_s: float = 0.078, cls=AppThenBootloaderBus):
        self.bl.dll.host_erase_all()  # the host bootloader is powered on at the simulated reset
        bus = cls(self.bl, self, startup_s=startup_s)
        self.addCleanup(bus.shutdown)
        return bus

    def flash_patiently(self, bus, data):
        """Same sequence as canflash.flash_over_bus, but without simulated flash cost and with a generous P2.

        The simulated ECU follows real time, so with 1 ms per programmed phrase and the default P2 of 1 s the
        load of a parallel test run decides whether the test passes. These tests are about **reaching** the
        bootloader without --wait, not about erase and program times (those are BootloaderTest's job), so
        neither belongs in their result - COM-09 removes tests that depend on the clock of the machine."""
        self.bl.dll.host_set_costs(0, 0)
        self.assertTrue(canflash.reach_bootloader(bus, 0.0, log=lambda *a, **k: None))
        with canflash.make_client(bus) as client:
            client.set_config("p2_timeout", 10.0)
            client.set_config("request_timeout", 60.0)
            canflash.flash_application(client, data, log=lambda *a, **k: None)
            client.ecu_reset(udsoncan.services.ECUReset.ResetType.hardReset)

    def test_flash_from_running_application_without_wait(self):
        bus = self.app_bus()
        data = make_app_image(9000, seed=11)
        canflash.flash_over_bus(bus, data, wait=0.0, log=lambda *a, **k: None)
        self.assertEqual(self.bl.read(CFG["BL_APP_START"], len(data)), data)
        self.assertEqual(self.bl.read(CFG["BL_APP_RECORD_ADDR"], 24), canflash.app_record(data))
        self.assertTrue(bus.lost, "at least one 10 02 must have been sent into the reset gap")
        self.assertTrue(all(f[:3] == b"\x02\x10\x02" for f in bus.lost), "only 10 02 may be lost")

    def test_old_sequence_loses_security_access(self):
        # BL-10: a reset gap of 1 s instead of the board's 78 ms. With 78 ms the next request had to fall into the gap
        # in real time; under the load of a parallel run the client sent it late, the bootloader already answered
        # and the expected timeout did not come. 1 s is longer than P2 and the request timeout below.
        bus = self.app_bus(startup_s=1.0)
        with canflash.make_client(bus) as client:
            client.set_config("p2_timeout", 0.3)
            client.set_config("request_timeout", 0.5)
            client.change_session(2)
            with self.assertRaises(udsoncan.exceptions.TimeoutException):
                client.unlock_security_access(1)
        # BL-03: the security access starts with reading the identification (22 F1 80) to choose the method, so
        # that is the request the resetting ECU loses now; before BL-03 it was 27 01 itself
        self.assertTrue(any(f[1:3] == b"\x27\x01" or f[1:4] == b"\x22\xf1\x80" for f in bus.lost))

    def test_enter_bootloader_when_bootloader_already_active(self):
        self.power_on()
        bus = HostBus(self.bl)
        self.addCleanup(bus.shutdown)
        t0 = time.monotonic()
        self.assertTrue(canflash.enter_bootloader(bus, seconds=2.0))
        self.assertLess(time.monotonic() - t0, 1.0)

    def test_enter_bootloader_without_ecu(self):
        bus = can.Bus(interface="virtual", channel="nobody")
        self.addCleanup(bus.shutdown)
        self.assertFalse(canflash.enter_bootloader(bus, seconds=0.3))

    def test_flash_without_wait_repeated_with_different_reset_gaps(self):
        """COM-09: robustness over several runs. The gap between the application's 50 02 and the listening
        bootloader is not a constant on the board (measured 78 ms), so it is varied here."""
        for startup_s in (0.0, 0.03, 0.078, 0.2):
            with self.subTest(startup_ms=int(startup_s * 1000)):
                bus = self.app_bus(startup_s)
                data = make_app_image(2048, seed=int(startup_s * 1000) + 1)
                self.flash_patiently(bus, data)
                self.assertEqual(self.bl.read(CFG["BL_APP_START"], len(data)), data)
                self.assertEqual(self.bl.read(CFG["BL_APP_RECORD_ADDR"], 24), canflash.app_record(data))

    def test_flash_without_wait_with_duplicated_answers(self):
        """Answers that arrive after their request was given up on must not reach the UDS client."""
        bus = self.app_bus(cls=DuplicateAnswerBus)
        data = make_app_image(2048, seed=23)
        self.flash_patiently(bus, data)
        self.assertEqual(self.bl.read(CFG["BL_APP_START"], len(data)), data)

    def test_bus_is_empty_when_the_bootloader_is_reached(self):
        """reach_bootloader() hands an empty bus to the client: no leftover 50 02 of the repeated requests."""
        bus = self.app_bus(cls=DuplicateAnswerBus)
        self.assertTrue(canflash.reach_bootloader(bus, wait=0.0, log=lambda *a, **k: None))
        self.assertIsNone(bus.recv(0.05), "a leftover answer was left on the bus")

    def test_drain_removes_buffered_frames_and_stops(self):
        bus = can.Bus(interface="virtual", channel="drain")
        other = can.Bus(interface="virtual", channel="drain")
        self.addCleanup(bus.shutdown)
        self.addCleanup(other.shutdown)
        self.assertEqual(canflash.drain(bus), 0)
        for index in range(5):
            other.send(can.Message(arbitration_id=0x100 + index, data=b"\x01", is_extended_id=False))
        time.sleep(0.02)
        self.assertEqual(canflash.drain(bus), 5)
        self.assertEqual(canflash.drain(bus), 0)
        # a bus that never goes quiet must not hold the call: the limit ends it
        t0 = time.monotonic()
        self.assertEqual(canflash.drain(bus, quiet=0.05, limit=0.1), 0)
        self.assertLess(time.monotonic() - t0, 0.5)


class HeldEcuBus(HostBus):
    """TOOL-51: the host bootloader behind an ECU that does not take part in the bus until a power cycle - the
    application holds its safe state (board 2026-09-22: ROM_CHECK_FAILURE, S1 held) or the board has no power.

    Nobody acknowledges: the frames stay in the adapter's transmit queue, a full queue refuses every further frame
    with the Vector driver's XL_ERR_QUEUE_IS_FULL, flush_tx_buffer() empties it. The power switch of the test
    (`switch`) powers the host bootloader on; the frames still queued then are delivered to it, as the controller
    repeats them until someone acknowledges."""

    QUEUE = 4

    def __init__(self, bl: HostBootloader, test: "EnterBootloaderTest", **kwargs):
        super().__init__(bl, **kwargs)
        self.test = test
        self.powered = False
        self.queued: list[can.Message] = []
        self.refused = 0
        bus = self

        class Switch(canflash.powercycle.PowerSwitch):
            name = "host switch"
            calls: list[str] = []

            def off(self):
                self.calls.append("off")
                bus.powered = False

            def on(self):
                self.calls.append("on")
                bus.power_on()
        self.switch = Switch()

    def power_on(self):
        with self.bl.lock:
            self.test.power_on()
            self.last = time.perf_counter()
            queued, self.queued = self.queued, []
            self.powered = True
            for msg in queued:
                HostBus.send(self, msg)

    def send(self, msg, timeout=None):
        if self.powered:
            return super().send(msg, timeout)
        if len(self.queued) >= self.QUEUE:
            self.refused += 1
            raise VectorOperationError(11, "XL_ERR_QUEUE_IS_FULL", "xlCanTransmit")
        self.queued.append(msg)

    def flush_tx_buffer(self):
        self.queued.clear()

    def _recv_internal(self, timeout):
        if not self.powered:
            time.sleep(min(timeout if timeout is not None else 0.005, 0.005))
            return None, False
        return super()._recv_internal(timeout)


class HeldEcuTest(unittest.TestCase):
    """TOOL-51, board 2026-09-22 (e2, captures 22-24): the way back from a held safe state is `canflash flash --wait`
    and a power cycle. Before TOOL-51 the first refused send (XL_ERR_QUEUE_IS_FULL) ended the run with a traceback;
    the board was rescued by a script that caught the bootloader in its startup window. Here canflash itself does
    that, with the power switch as the optional hook, and flashes in the same run."""

    PROJECT, MCUDB, LAYOUT = EVAL_PROJECT, None, None
    setUpClass = classmethod(BootloaderTest.setUpClass.__func__)
    tearDownClass = classmethod(BootloaderTest.tearDownClass.__func__)
    setUp = BootloaderTest.setUp
    power_on = BootloaderTest.power_on
    install_app = BootloaderTest.install_app

    def held_bus(self) -> HeldEcuBus:
        self.bl.dll.host_set_costs(0, 0)   # about reaching the bootloader, not about flash times (see COM-09)
        bus = HeldEcuBus(self.bl, self)
        self.addCleanup(bus.shutdown)
        return bus

    def test_flash_with_wait_and_power_cycle(self):
        """Whole run like on the board: preprogramming without an answer, waiting with refused sends, power cycle,
        the bootloader caught in its startup window, the new application flashed in the same process."""
        self.install_app(make_app_image(3000, seed=51))
        bus = self.held_bus()
        data = make_app_image(4096, seed=52)
        lines = []
        canflash.flash_over_bus(bus, data, wait=10.0, log=lambda *a, **k: lines.append(" ".join(map(str, a))),
                                progress="off", power=bus.switch, off_s=0.2)
        self.assertEqual(bus.switch.calls, ["off", "on"])
        self.assertGreater(bus.refused, 0, "the transmit queue must have been full while nobody acknowledged")
        self.assertEqual(self.bl.read(CFG["BL_APP_START"], len(data)), data)
        self.assertEqual(self.bl.read(CFG["BL_APP_RECORD_ADDR"], 24), canflash.app_record(data))
        self.assertTrue(any("refused by the adapter" in line for line in lines), lines)

    def test_without_power_cycle_the_wait_ends_cleanly(self):
        """Nobody switches: after --wait the run ends with "No answer from the bootloader", not a traceback."""
        bus = self.held_bus()
        with self.assertRaisesRegex(RuntimeError, "No answer from the bootloader"):
            canflash.flash_over_bus(bus, make_app_image(1024), wait=0.5, log=lambda *a, **k: None, prepare=False)
        self.assertGreater(bus.refused, 0)


class OldBootloaderBus(HostBus):
    """The host bootloader as a bootloader from before BL-02: DID 0xF1A0 is unknown (NRC requestOutOfRange)."""

    def __init__(self, bl: HostBootloader, **kwargs):
        super().__init__(bl, **kwargs)
        self.answers: list[bytes] = []

    def send(self, msg, timeout=None):
        data = bytes(msg.data)
        if msg.arbitration_id == CFG["BL_CAN_ID_REQUEST_PHYS"] and data[:4] == b"\x03\x22\xF1\xA0":
            self.answers.append(bytes([0x03, 0x7F, 0x22, 0x31]).ljust(8, bytes([CFG["BL_ISOTP_PADDING"]])))
            return
        super().send(msg, timeout)

    def _recv_internal(self, timeout):
        if self.answers:
            return can.Message(arbitration_id=CFG["BL_CAN_ID_RESPONSE"], data=self.answers.pop(0),
                               is_extended_id=False), False
        return super()._recv_internal(timeout)


class PreBl22BootloaderBus(HostBus):
    """The host bootloader as one flashed before BL-22: DID 0xF180 answers the bare identification text,
    without the numberOfModules byte of ISO 14229-1 Annex C.1. Every other service is the real bootloader.

    The answer is built here as ISO-TP frames, because dropping one byte out of a segmented response means
    segmenting it again: first frame, then the consecutive frames once the tester's flow control arrives.
    This is what every device in the field sends, and the tester has to keep flashing it (BL-22).
    """

    REQUEST_F180 = bytes([0x03, 0x22, 0xF1, 0x80])
    RESPONSE_F180 = bytes([0x62, 0xF1, 0x80])

    def __init__(self, bl: HostBootloader, text: str | None = None, modules: bytes = b"", **kwargs):
        super().__init__(bl, **kwargs)
        self.text = (canflash.bl_version() if text is None else text).encode("ascii")
        self.modules = modules          # b"\x01": the Annex C.1 form (BL-22), for PreBl24BootloaderBus
        self.answers: list[bytes] = []
        self._consecutive: list[bytes] = []

    def _frames(self) -> tuple[list[bytes], list[bytes]]:
        pad = bytes([CFG["BL_ISOTP_PADDING"]])
        payload = self.RESPONSE_F180 + self.modules + self.text
        if len(payload) <= 7:
            return [(bytes([len(payload)]) + payload).ljust(8, pad)], []
        first = (bytes([0x10, len(payload)]) + payload[:6]).ljust(8, pad)
        rest = payload[6:]
        consecutive = [(bytes([0x20 | ((i + 1) & 0x0F)]) + rest[i * 7 : (i + 1) * 7]).ljust(8, pad)
                       for i in range((len(rest) + 6) // 7)]
        return [first], consecutive

    def send(self, msg, timeout=None):
        data = bytes(msg.data)
        if msg.arbitration_id == CFG["BL_CAN_ID_REQUEST_PHYS"]:
            if data[:4] == self.REQUEST_F180:
                first, self._consecutive = self._frames()
                self.answers.extend(first)
                return
            if self._consecutive and (data[0] & 0xF0) == 0x30:   # flow control of the tester
                self.answers.extend(self._consecutive)
                self._consecutive = []
                return
        super().send(msg, timeout)

    def _recv_internal(self, timeout):
        if self.answers:
            return can.Message(arbitration_id=CFG["BL_CAN_ID_RESPONSE"], data=self.answers.pop(0),
                               is_extended_id=False), False
        return super()._recv_internal(timeout)


class PreBl24BootloaderBus(PreBl22BootloaderBus):
    """The host bootloader as one from before BL-24 (0.3.1 on the eval board, 2026-09-22): it knows the
    compatibility check only as routine 0xFF02 and answers 0xF1A0 with requestOutOfRange.

    DID 0xF180 reports `text` in the Annex C.1 form (these bootloaders have BL-22), or nothing at all
    (`identification=False`: requestOutOfRange, a target that does not tell its version). A request for 0xF1A0 is
    handed to the real bootloader as the unknown routine 0xFF03, so the refusal - after the whole segmented request,
    before any check - comes from the bootloader itself, like from the one in the field."""

    UNKNOWN_ROUTINE = b"\xFF\x03"

    def __init__(self, bl: HostBootloader, text: str = "", identification: bool = True, **kwargs):
        super().__init__(bl, text=text or f"{canflash.TEXT[canflash.PREFIX_KEY]} 0.3.1", modules=b"\x01", **kwargs)
        self.identification = identification

    def send(self, msg, timeout=None):
        data = bytes(msg.data)
        if msg.arbitration_id == CFG["BL_CAN_ID_REQUEST_PHYS"]:
            if not self.identification and data[:4] == self.REQUEST_F180:
                self.answers.append(bytes([0x03, 0x7F, 0x22, 0x31]).ljust(8, bytes([CFG["BL_ISOTP_PADDING"]])))
                return
            kind = data[0] & 0xF0
            at = 3 if kind == 0x00 else 4 if kind == 0x10 else None      # routine identifier in the frame
            if at is not None and data[at - 2 : at] == b"\x31\x01" and data[at : at + 2] == b"\xF1\xA0":
                msg = can.Message(arbitration_id=msg.arbitration_id, is_extended_id=False,
                                  data=data[:at] + self.UNKNOWN_ROUTINE + data[at + 2 :])
        super().send(msg, timeout)


def collect_log():
    lines: list[str] = []

    def log(*args, end="\n", flush=False):
        lines.append(" ".join(str(a) for a in args) + end)
    return lines, log


class CanflashComfortTest(unittest.TestCase):
    """TOOL-05 against the host bootloader: progress in a log, version check without udsoncan noise."""

    PROJECT, MCUDB, LAYOUT = EVAL_PROJECT, None, None
    setUpClass = classmethod(BootloaderTest.setUpClass.__func__)
    tearDownClass = classmethod(BootloaderTest.tearDownClass.__func__)
    setUp = BootloaderTest.setUp
    power_on = BootloaderTest.power_on

    def flash(self, bus, data):
        """flash_over_bus with a generous P2 and no simulated flash cost (see EnterBootloaderTest.flash_patiently)."""
        self.bl.dll.host_set_costs(0, 0)
        lines, log = collect_log()
        self.assertTrue(canflash.reach_bootloader(bus, 0.0, log))
        with canflash.make_client(bus) as client:
            client.set_config("p2_timeout", 10.0)
            client.set_config("request_timeout", 60.0)
            canflash.flash_application(client, data, log)
        return lines

    def test_old_bootloader_leaves_one_note_and_no_exception_text(self):
        """Board finding: two '[NegativeResponseException] ... RequestOutOfRange (0x31)' per flash."""
        self.power_on(boot_request=True)
        bus = OldBootloaderBus(self.bl)
        self.addCleanup(bus.shutdown)
        data = make_app_image(20000, seed=31)
        with self.assertNoLogs("UdsClient", "WARNING"):
            lines = self.flash(bus, data)
        text = "".join(lines)
        self.assertEqual(text.count("older than BL-02"), 1, text)
        self.assertNotIn("NegativeResponse", text)
        self.assertNotIn("RequestOutOfRange", text)
        self.assertEqual(self.bl.read(CFG["BL_APP_START"], len(data)), data)

    def test_an_ecu_from_before_bl_22_is_read_and_flashed(self):
        """BL-22, hard requirement of the transition: a device whose bootloader answers DID 0xF180 without
        the numberOfModules byte stays readable and flashable with this tester.

        It is the case of every ECU in the field on the day BL-22 is merged. The tester reads the same
        identification out of both forms, gives no warning, and the image lands in flash unchanged."""
        self.power_on(boot_request=True)
        bus = PreBl22BootloaderBus(self.bl)
        self.addCleanup(bus.shutdown)
        data = make_app_image(20000, seed=22)
        lines = self.flash(bus, data)
        text = "".join(lines)
        self.assertIn(f"Bootloader: {canflash.bl_version()}", text)
        self.assertIn("before BL-22", text)
        self.assertFalse([line for line in lines if line.startswith("Warning")], lines)
        self.assertEqual(self.bl.read(CFG["BL_APP_START"], len(data)), data)

    def flash_pre_bl_24(self, bus, seed: int) -> str:
        """Flashes over a PreBl24BootloaderBus without any udsoncan noise; returns the log."""
        self.power_on(boot_request=True)
        self.addCleanup(bus.shutdown)
        data = make_app_image(20000, seed=seed)
        with self.assertNoLogs("UdsClient", "WARNING"):
            lines = self.flash(bus, data)
        text = "".join(lines)
        self.assertNotIn("NegativeResponse", text)
        self.assertNotIn("RequestOutOfRange", text)
        self.assertEqual(self.bl.read(CFG["BL_APP_START"], len(data)), data)
        return text

    def test_an_ecu_from_before_bl_24_is_flashed_with_the_former_routine(self):
        """BL-24, hard requirement of the transition: a bootloader 0.3.1 (as on the eval board) knows only 0xFF02.
        canflash picks the routine by the version in DID 0xF180, sends 0xFF02 and never 0xF1A0 - no failed attempt,
        no note, the image lands in flash unchanged. The bus refuses 0xF1A0, so a wrong choice fails here."""
        text = self.flash_pre_bl_24(PreBl24BootloaderBus(self.bl), seed=241)
        self.assertIn(f"Bootloader: {canflash.TEXT[canflash.PREFIX_KEY]} 0.3.1\n", text)
        self.assertNotIn("Warning", text)
        rids = routine_requests(self.bl.log)
        self.assertIn(canflash.RID_CHECK_COMPAT_FORMER, rids)
        self.assertNotIn(canflash.RID_CHECK_COMPAT, rids)
        self.assertNotIn(0xFF03, rids)

    def test_the_last_version_before_bl_24_gets_the_former_routine(self):
        """The boundary: 0.3.3 is the last bootloader that knows only 0xFF02."""
        self.flash_pre_bl_24(PreBl24BootloaderBus(self.bl, text=f"{canflash.TEXT[canflash.PREFIX_KEY]} 0.3.3"),
                             seed=242)
        rids = routine_requests(self.bl.log)
        self.assertIn(canflash.RID_CHECK_COMPAT_FORMER, rids)
        self.assertNotIn(0xFF03, rids)

    def test_without_identification_the_former_routine_follows_a_refusal(self):
        """A target that does not name its version (no DID 0xF180): canflash tries 0xF1A0 first, and after
        requestOutOfRange 0xFF02 - quietly, the refusal is the expected answer of an older bootloader."""
        text = self.flash_pre_bl_24(PreBl24BootloaderBus(self.bl, identification=False), seed=243)
        self.assertIn("DID 0xF180", text)                      # the known warning of check_boot_software
        rids = routine_requests(self.bl.log)                    # 0xFF03: 0xF1A0 as the bus hands it on
        self.assertEqual([r for r in rids if r != canflash.RID_ERASE_MEMORY and r != canflash.RID_CHECK_MEMORY],
                         [0xFF03, canflash.RID_CHECK_COMPAT_FORMER])

    def test_the_two_identification_forms_read_back_the_same(self):
        """BL-22: the same device, once answering in each form - one identification, two notes.

        The note is the only difference the user sees; everything the tester decides from the
        identification (prefix, major version, method of the security access) is the same."""
        self.power_on(boot_request=True)
        old_bus = PreBl22BootloaderBus(self.bl)
        self.addCleanup(old_bus.shutdown)
        with canflash.make_client(old_bus) as client:
            old = canflash.read_boot_identification(client)
        new_bus = HostBus(self.bl)
        self.addCleanup(new_bus.shutdown)
        with canflash.make_client(new_bus) as client:
            new = canflash.read_boot_identification(client)
        self.assertEqual((str(old), str(new)), (canflash.bl_version(), canflash.bl_version()))
        self.assertEqual((old.modules, new.modules), (None, 1))
        self.assertEqual((old.annex_c1, new.annex_c1), (False, True))
        self.assertEqual(canflash.boot_version_warnings(old), canflash.boot_version_warnings(new))
        self.assertEqual(canflash.security_method_of(old), canflash.security_method_of(new))
        self.assertIn("before BL-22", canflash.identification_note(old))
        self.assertEqual(canflash.identification_note(new), "")

    def test_progress_of_a_logged_flash_is_a_few_lines(self):
        """A log function (Studio, bootloader_update) gets whole lines every 10 %, never a carriage return."""
        self.power_on(boot_request=True)
        bus = HostBus(self.bl)
        self.addCleanup(bus.shutdown)
        data = make_app_image(60000, seed=5)
        lines = self.flash(bus, data)
        counters = [line for line in lines if "bytes (" in line]
        self.assertEqual(len(counters), 10, lines)
        self.assertFalse(any("\r" in line for line in lines))
        self.assertIn("(100 %)", counters[-1])
        self.assertIn(f"Bootloader: {canflash.bl_version()}\n", lines)
        self.assertFalse([line for line in lines if line.startswith("Warning")])
        self.assertEqual(self.bl.read(CFG["BL_APP_START"], len(data)), data)

    def test_bootloader_of_another_prefix_warns_but_flashes(self):
        """The user flashes deliberately: another identification is a warning, not an abort (PLAT-08 rename)."""
        self.power_on(boot_request=True)
        bus = HostBus(self.bl)
        self.addCleanup(bus.shutdown)
        data = make_app_image(4096, seed=9)
        prefix = canflash.TEXT[canflash.PREFIX_KEY]
        canflash.TEXT[canflash.PREFIX_KEY] = "ACME-BL"             # neither this prefix nor the former name
        try:
            lines = self.flash(bus, data)
        finally:
            canflash.TEXT[canflash.PREFIX_KEY] = prefix
        warnings = [line for line in lines if line.startswith("Warning")]
        self.assertEqual(len(warnings), 1, lines)
        self.assertIn(f"'{prefix}'", warnings[0])
        self.assertEqual(self.bl.read(CFG["BL_APP_START"], len(data)), data)


class MultiHostBus(can.BusABC):
    """One CAN bus with several host bootloaders (each its own DLL): every frame reaches every ECU."""

    def __init__(self, bootloaders: list[HostBootloader], **kwargs):
        super().__init__(channel="multi", **kwargs)
        self.buses = [HostBus(bl) for bl in bootloaders]

    def send(self, msg, timeout=None):
        for bus in self.buses:
            bus.send(msg, timeout)

    def _recv_internal(self, timeout):
        end = time.perf_counter() + (timeout if timeout is not None else 3600)
        while True:
            for bus in self.buses:
                msg, _ = bus._recv_internal(0)
                if msg is not None:
                    return msg, False
            if time.perf_counter() >= end:
                return None, False
            time.sleep(0.0005)

    def shutdown(self):
        for bus in self.buses:
            bus.shutdown()
        super().shutdown()


class MultiEcuTest(unittest.TestCase):
    """TOOL-05: two ECUs with the diagnostic addresses of their projects share one bus - the eval board demo
    (0x7E0/0x7E8) and the S32K148 build profile (0x6F0/0x6F8). Scan finds both, flashing reaches only the one
    chosen by its project."""

    @classmethod
    def setUpClass(cls):
        cls.saved = selected()
        cls.projects = {"eval": EVAL_PROJECT, "k148": S32K148_PROJECT}
        cls.dlls = {name: host_dll(project) for name, project in cls.projects.items()}
        cls.bls = {name: HostBootloader(path) for name, path in cls.dlls.items()}
        cls.layouts = {name: canflash.load_build(path.parent / "gen" / "bootloader" / "bl_memory.h")
                       for name, path in cls.dlls.items()}
        cls.ecus = {e.project: e for e in canflash.ecus_from_projects(list(cls.projects.values()))}

    @classmethod
    def tearDownClass(cls):
        canflash.use_layout(cls.saved)

    def setUp(self):
        for bl in self.bls.values():
            bl.dll.host_erase_all()
            bl.dll.host_set_costs(0, 0)
            with bl.lock:
                bl.dll.host_power_on(1)          # boot request: both stay in their bootloader
            bl.log.clear()
        self.bus = MultiHostBus(list(self.bls.values()))
        self.addCleanup(self.bus.shutdown)

    def ecu(self, name) -> canflash.Ecu:
        return self.ecus[self.projects[name]]

    def test_scan_finds_both_ecus_and_what_runs_on_them(self):
        found, unknown = canflash.scan_bus(self.bus, [self.ecu("eval"), self.ecu("k148")])
        self.assertEqual([e.name for e in found], [EVAL_PROJECT.parent.name, "buildtest_s32k148_min"])
        self.assertEqual(unknown, [])
        for name in ("eval", "k148"):
            with self.subTest(name):
                what = canflash.identify(self.bus, self.ecu(name))
                self.assertEqual(what, {"boot": f"{self.ecu(name).prefix} {canflash.BL_VERSION_NUMBER}",
                                        "app": None})

    def test_scan_reports_an_ecu_no_project_owns(self):
        """Only the eval ECU is configured; the S32K148 answers the functional TesterPresent of a configured
        ECU with the same functional id - here faked by giving the configured ECU the S32K148's functional id."""
        k148 = self.ecu("k148")
        diag = dict(self.ecu("eval").diag)
        diag["BL_CAN_ID_REQUEST_FUNC"] = k148.functional_id
        only_eval = replace(self.ecu("eval"), diag=tuple(sorted(diag.items())))
        found, unknown = canflash.scan_bus(self.bus, [only_eval])
        self.assertEqual([e.name for e in found], [EVAL_PROJECT.parent.name])
        self.assertEqual(unknown, [k148.response_id])

    def test_flash_reaches_only_the_chosen_ecu(self):
        data = make_app_image(8192, seed=77)
        for target, other in (("k148", "eval"), ("eval", "k148")):
            with self.subTest(target=target):
                self.setUp()
                canflash.use_layout(self.layouts[target])
                lines, log = collect_log()
                self.assertTrue(canflash.reach_bootloader(self.bus, 0.0, log))
                with canflash.make_client(self.bus) as client:
                    client.set_config("p2_timeout", 10.0)
                    client.set_config("request_timeout", 60.0)
                    canflash.flash_application(client, data, log)
                start = CFG["BL_APP_START"]
                self.assertEqual(self.bls[target].read(start, len(data)), data)
                self.assertEqual(self.bls[other].read(self.layouts[other]["BL_APP_START"], 64), b"\xff" * 64)
                self.assertFalse([entry for entry in self.bls[other].log if entry[0] == "tx"],
                                 f"{other} answered a request meant for {target}")
                self.assertFalse([line for line in lines if line.startswith("Warning")], lines)

    def test_ecu_option_refuses_the_hex_of_another_ecu(self):
        """--ecu with a hex file whose build has other diagnostic addresses stops before the bus is opened."""
        tmp = Path(tempfile.mkdtemp(prefix="canflash_ecu_"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        generated = tmp / canflash.LAYOUT_IN_BUILD
        generated.parent.mkdir(parents=True)
        source = self.dlls["k148"].parent / "gen" / "bootloader"
        for name in ("bl_memory.h", canflash.DIAG_HEADER_NAME):
            shutil.copy(source / name, generated.parent / name)
        hexfile = tmp / "app.hex"
        hexfile.write_text(":00000001FF\n", encoding="ascii")
        self.addCleanup(canflash.use_layout, selected())
        parser = canflash.build_parser()
        args = parser.parse_args(["flash", str(hexfile), "--ecu", EVAL_PROJECT.parent.name,
                                  "--projects", str(EVAL_PROJECT)])
        with self.assertRaises(ValueError) as ctx:
            canflash.prepare(args)
        self.assertIn("other diagnostic addresses", str(ctx.exception))
        args = parser.parse_args(["flash", str(hexfile), "--ecu", "buildtest_s32k148_min",
                                  "--projects", str(S32K148_PROJECT)])
        canflash.prepare(args)
        self.assertEqual(Path(args.project), S32K148_PROJECT)
        self.assertEqual(CFG["BL_CAN_ID_REQUEST_PHYS"], self.ecu("k148").request_id)
        self.assertEqual(Path(args.timing_project), S32K148_PROJECT)


class HostBuildChecksumTest(unittest.TestCase):
    """COM-09: host libraries are built against the checksum of their sources, not against modification times.

    The failure this replaces: an app_uds_host.dll that was **newer** than every source but built from other
    content made the test app-uds-host red and looked like a time dependent failure (COM-11 finding)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="hostbuild_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.source = self.tmp / "a.c"
        self.source.write_text("int a(void) { return 1; }\n", encoding="utf-8")
        self.out = self.tmp / "lib.dll"
        self.builds = 0

    def build(self):
        self.builds += 1
        self.out.write_text(self.source.read_text(encoding="utf-8"), encoding="utf-8")

    def run_build(self, extra=("-O1",)) -> bool:
        return hostbuild.build_if_changed(self.out, hostbuild.checksum([self.source], extra), self.build)

    def test_builds_once_and_reuses_afterwards(self):
        self.assertTrue(self.run_build())
        self.assertFalse(self.run_build())
        self.assertEqual(self.builds, 1)

    def test_changed_source_rebuilds_even_if_the_library_is_newer(self):
        self.run_build()
        os.utime(self.out, (time.time() + 3600, time.time() + 3600))  # library from the future
        self.source.write_text("int a(void) { return 2; }\n", encoding="utf-8")
        self.assertTrue(self.run_build(), "the mtime trap: newer library, other content")
        self.assertEqual(self.builds, 2)

    def test_same_size_and_same_mtime_but_other_content_rebuilds(self):
        self.run_build()
        stat = self.source.stat()
        self.source.write_text("int a(void) { return 7; }\n", encoding="utf-8")
        os.utime(self.source, (stat.st_atime, stat.st_mtime))
        self.assertTrue(self.run_build())

    def test_changed_compiler_arguments_rebuild(self):
        self.run_build()
        self.assertTrue(self.run_build(extra=("-O2",)))
        self.assertFalse(self.run_build(extra=("-O2",)))

    def test_missing_library_rebuilds_although_the_stamp_is_there(self):
        self.run_build()
        self.out.unlink()
        self.assertTrue(self.run_build())

    def test_a_failed_build_is_repeated(self):
        def fail():
            self.builds += 1
            raise RuntimeError("compiler failed")

        digest = hostbuild.checksum([self.source])
        with self.assertRaises(RuntimeError):
            hostbuild.build_if_changed(self.out, digest, fail)
        self.assertFalse(hostbuild.stamp_file(self.out).is_file())
        self.assertTrue(hostbuild.build_if_changed(self.out, digest, self.build))

    def test_checksum_covers_a_file_that_appears(self):
        added = self.tmp / "b.h"
        before = hostbuild.checksum([self.source, added])
        added.write_text("#define B 1\n", encoding="utf-8")
        self.assertNotEqual(before, hostbuild.checksum([self.source, added]))

    def test_checksum_does_not_depend_on_the_order(self):
        other = self.tmp / "b.c"
        other.write_text("int b(void) { return 2; }\n", encoding="utf-8")
        self.assertEqual(hostbuild.checksum([self.source, other]), hostbuild.checksum([other, self.source]))

    def test_checksum_of_two_files_with_swapped_content_differs(self):
        """Significance: the checksum is not a sum over the contents."""
        other = self.tmp / "b.c"
        other.write_text("int b(void) { return 2; }\n", encoding="utf-8")
        first = hostbuild.checksum([self.source, other])
        self.source.write_text("int b(void) { return 2; }\n", encoding="utf-8")
        other.write_text("int a(void) { return 1; }\n", encoding="utf-8")
        self.assertNotEqual(first, hostbuild.checksum([self.source, other]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
