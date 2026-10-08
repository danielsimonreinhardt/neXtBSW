"""COM-44: identification identifiers of the application - F191 hardware number, F195 neXtBSW version and build
(PLAT-79: F1F1 configuration identity),
the compatibility record of F1A0 - against the bootloader, which answers the same values (decision 2026-09-29).

The application stack of ecu/uds runs as DLL from the own profile identification_profile/diag.yaml with the memory
layout of projects/evalboard_s32k144_demo, the bootloader as bl_host.dll of the same layout (group bl-host-dll). For
each validity record (valid, erased, without compatibility block, damaged, of another target, layout, area or
version) the record is written into the bootloader's flash and handed to the application as its record at
BL_APP_RECORD_ADDR; both must answer the same 22 bytes and the same state.

    .venv/Scripts/python ecu/uds/test/host/test_app_uds_identification.py
"""

import ctypes
import os
import struct
import sys
import unittest
from pathlib import Path

import udsoncan
from udsoncan.exceptions import NegativeResponseException

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools" / "canflash"))
sys.path.insert(0, str(ROOT / "tools" / "bswgen"))
import canflash  # noqa: E402
from test_app_uds_host import BL_DLL, EVAL_PROJECT, EcuBus, HostApp, app_dll  # noqa: E402
from test_bootloader_host import HostBootloader, make_app_image, record_with, selected  # noqa: E402

PROFILE = HERE / "identification_profile" / "diag.yaml"
IDENT_DLL = Path(os.environ.get("APP_UDS_HOST_DLL_IDENT", ROOT / "build" / "host" / "app_uds_host_ident.dll"))
DID_HARDWARE, DID_NEXTBSW, DID_COMPAT_APP, DID_COMPAT_BL = 0xF191, 0xF195, 0xFD10, 0xF1A0
DID_CONFIGURATION = 0xF1F1                      # PLAT-79
DID_WORKSPACE = 0xF1F2                          # PLAT-93
NO_RECORD, OK, FORMAT, TARGET, LAYOUT, AREA, ABSENT = 0xFF, 0x00, 0x01, 0x02, 0x03, 0x04, 0x10


def hardware_number() -> str:
    """BL_HARDWARE_ID of the eval board (tools/bswgen/blgen.py hardware_id, BL-10)."""
    import blgen  # noqa: E402
    from nxconfig import config as nxconfig_config  # noqa: E402,F401
    import nxconfig  # noqa: E402

    config = nxconfig.load(EVAL_PROJECT)
    return blgen.hardware_id(config.board.board.name, config.derivative.derivative.name)


class IdentificationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not BL_DLL.exists():
            raise unittest.SkipTest(f"{BL_DLL} not built (group bl-host-dll)")
        cls.saved = selected()
        canflash.use_layout(canflash.target_from_project(EVAL_PROJECT))
        cls.app = HostApp(app_dll(IDENT_DLL, EVAL_PROJECT, PROFILE))
        cls.app.dll.app_set_nextbsw_version.argtypes = [ctypes.c_char_p]
        cls.app.dll.app_set_configuration_identity.argtypes = [ctypes.c_char_p]
        cls.app.dll.app_set_workspace_version.argtypes = [ctypes.c_char_p]
        cls.app.dll.app_set_application_record.argtypes = [ctypes.c_char_p, ctypes.c_uint32]
        cls.app.dll.app_application_record_bytes.restype = ctypes.c_uint32
        cls.bl = HostBootloader(BL_DLL)

    @classmethod
    def tearDownClass(cls):
        canflash.use_layout(cls.saved)

    def setUp(self):
        self.app.dll.app_set_nextbsw_version(b"0.1.0 v0.1.0-12-g46d7f048")
        self.app.dll.app_set_application_record(None, self.app.dll.app_application_record_bytes())
        self.app.power_on()
        self.bl.dll.host_erase_all()

    def tearDown(self):
        self.assertEqual(self.app.dll.app_assert_count(), 0, "ETL assert in the stack")

    def read(self, bus, did: int) -> bytes:
        client = canflash.make_client(bus)
        client.open()
        try:
            response = client.send_request(udsoncan.Request.from_payload(bytes([0x22, did >> 8, did & 0xFF])))
            return bytes(response.original_payload)
        except NegativeResponseException as e:
            return bytes(e.response.original_payload)
        finally:
            client.close()
            bus.shutdown()

    def read_app(self, did: int) -> bytes:
        return self.read(EcuBus(self.app, None), did)

    def read_bootloader(self, did: int) -> bytes:
        self.bl.dll.host_power_on(1)
        bus = EcuBus(self.app, self.bl)
        bus.in_bootloader = True
        return self.read(bus, did)

    def with_record(self, record: bytes | None):
        """`record` at BL_APP_RECORD_ADDR of the bootloader's flash and as the application's validity record."""
        size = self.app.dll.app_application_record_bytes()
        stored = (record or b"").ljust(size, b"\xff")[:size]
        self.bl.dll.host_erase_all()
        self.bl.write(canflash.CFG["BL_APP_RECORD_ADDR"], stored)
        self.app.dll.app_set_application_record(stored, size)
        self.app.power_on()

    # ------------------------------------------------------------------------------------------------- F191

    def test_hardware_number_is_the_bootloaders(self):
        text = hardware_number()
        self.assertEqual(text, "evalboard_s32k144/S32K144")
        app = self.read_app(DID_HARDWARE)
        self.assertEqual(app[:3], bytes([0x62, 0xF1, 0x91]))
        self.assertEqual(app[3:], text.encode().ljust(32, b"\0"))       # filled up to its 32 bytes (COM-33)
        bootloader = self.read_bootloader(DID_HARDWARE)
        self.assertEqual(bootloader[3:], text.encode())                   # the bootloader answers the bare text
        self.assertEqual(app[3:].rstrip(b"\0"), bootloader[3:])

    # ------------------------------------------------------------------------------------------------- F195

    def test_nextbsw_version(self):
        self.assertEqual(self.read_app(DID_NEXTBSW), bytes([0x62, 0xF1, 0x95])
                         + b"0.1.0 v0.1.0-12-g46d7f048".ljust(48, b"\0"))
        self.app.dll.app_set_nextbsw_version(None)
        self.app.power_on()
        self.assertEqual(self.read_app(DID_NEXTBSW), bytes([0x7F, 0x22, 0x22]))   # not known: conditionsNotCorrect

    # ------------------------------------------------------------------------------------------------- F1F1

    def test_configuration_identity(self):
        """PLAT-79: the 64 hex digits of the image (NX_IDENTITY_CONFIGURATION) exactly, unknown: NRC 0x22."""
        identity = "6bdd52f5986f70c631383470f892a8719da4fd38ddcbfa633344fab3b0bb5248"
        self.app.dll.app_set_configuration_identity(identity.encode())
        self.app.power_on()
        self.assertEqual(self.read_app(DID_CONFIGURATION), bytes([0x62, 0xF1, 0xF1]) + identity.encode())
        self.app.dll.app_set_configuration_identity(None)
        self.app.power_on()
        self.assertEqual(self.read_app(DID_CONFIGURATION), bytes([0x7F, 0x22, 0x22]))

    # ------------------------------------------------------------------------------------------------- F1F2

    def test_workspace_version(self):
        """PLAT-93: commit of the customer workspace (NX_DIAG_WORKSPACE_VERSION) in its 48 bytes, "-" for no workspace,
        "nogit" for a workspace without repository, unknown: NRC 0x22. Independent of F1F1 and F195."""
        for text in ("hp-v2.1-7-g00ff11a-dirty+1a2b3c4d", "-", "nogit"):
            with self.subTest(text=text):
                self.app.dll.app_set_workspace_version(text.encode())
                self.app.power_on()
                self.assertEqual(self.read_app(DID_WORKSPACE),
                                 bytes([0x62, 0xF1, 0xF2]) + text.encode().ljust(48, b"\0"))
        self.app.dll.app_set_workspace_version(b"-")
        self.app.power_on()
        self.assertEqual(self.read_app(DID_NEXTBSW), bytes([0x62, 0xF1, 0x95])
                         + b"0.1.0 v0.1.0-12-g46d7f048".ljust(48, b"\0"))          # the tree's commit is not touched
        self.app.dll.app_set_workspace_version(None)
        self.app.power_on()
        self.assertEqual(self.read_app(DID_WORKSPACE), bytes([0x7F, 0x22, 0x22]))
        self.app.dll.app_set_workspace_version(b"-")

    # ------------------------------------------------------------------------------------------------- F1A0

    def test_compatibility_record_is_the_bootloaders(self):
        data = make_app_image(4096)
        valid = record_with(data)
        records = {
            "erased": (None, NO_RECORD),
            "valid": (valid, OK),
            "without compatibility block (before BL-02)": (canflash.app_record(data), ABSENT),
            "other target": (record_with(data, target=0x12345678), TARGET),
            "other layout": (record_with(data, layout=0x0BADF00D), LAYOUT),
            "other application end": (record_with(data, app_end=canflash.CFG["BL_APP_END"] - 0x1000), AREA),
            "other version": (record_with(data, version=2), FORMAT),
            "damaged compatibility block": (record_with(data, crc=0), FORMAT),
            "damaged record": (valid[:16] + b"\0\0\0\0" + valid[20:], NO_RECORD),
            "other start": (struct.pack("<I", canflash.RECORD_MAGIC) + struct.pack("<I", 0) + valid[8:], NO_RECORD),
        }
        for name, (record, state) in records.items():
            with self.subTest(record=name):
                self.with_record(record)
                bootloader = self.read_bootloader(DID_COMPAT_BL)
                app = self.read_app(DID_COMPAT_APP)
                self.assertEqual(bootloader[:3], bytes([0x62, 0xF1, 0xA0]))
                self.assertEqual(app[:3], bytes([0x62, 0xFD, 0x10]))
                self.assertEqual(len(app), 3 + 22)
                self.assertEqual(app[3:], bootloader[3:])
                self.assertEqual(app[-1], state)
        # the values of the build in the record: version 1, target, layout and area of the eval board
        target = canflash.image_compat()
        self.assertEqual(app[3:24], bytes([target["version"]]) + struct.pack(
            ">IIIII", target["target"], target["layout"], target["app_start"], target["app_end"],
            target["record_addr"]))

    def test_record_not_readable(self):
        """Without a record span (the DiagSystem of a family that cannot read it) the state is NO_RECORD."""
        self.app.dll.app_set_application_record(None, 0)
        self.app.power_on()
        self.assertEqual(self.read_app(DID_COMPAT_APP)[-1], NO_RECORD)


if __name__ == "__main__":
    unittest.main(verbosity=2)
