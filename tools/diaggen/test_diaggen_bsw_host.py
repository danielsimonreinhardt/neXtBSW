"""COM-32: identifiers with values of the basic software, served by the application UDS stack against udsoncan.

The stack of ecu/uds runs as DLL (ecu/uds/test/host/build_app_uds_host.py) with the jobs tools/diaggen generates
from the own profile test/bsw/diag.yaml and the diagnostic addresses of projects/buildtest_s32k148_min (S32K148
without SBC) - not the eval board. The host build has no RTE: DiagRteStub.cpp stands in for the rte::diag
functions, so the test sets what the RTE would read from ecu.state, adc.*, bsw.* and checks what the tester gets:
the coding of every element on the wire, several identifiers in one request (split by the description alone,
COM-33), NRC 0x22 while a value of the basic software cannot be read, and the same bytes for a snapshot of the
fault memory (dem::IDidReader, NVM-03). How the real RTE reads the values is tools/rtegen/test_rtegen_diag.py
(RteDiagBswHostTest).

Run: .venv/Scripts/python -m unittest tools/diaggen/test_diaggen_bsw_host.py
"""

import ctypes
import os
import struct
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
UDS_HOST = REPO / "ecu" / "uds" / "test" / "host"
sys.path.insert(0, str(UDS_HOST))

import test_app_uds_host as uds_host  # noqa: E402  (HostApp, EcuBus, app_dll, canflash of the application tests)
import udsoncan  # noqa: E402
from udsoncan.exceptions import NegativeResponseException  # noqa: E402

canflash = uds_host.canflash
diagdesc = uds_host.diagdesc
BSW_PROFILE = HERE / "test" / "bsw" / "diag.yaml"
BSW_APP_DLL = Path(os.environ.get("APP_UDS_HOST_DLL_BSW", REPO / "build" / "host" / "app_uds_host_bsw.dll"))


class DiagBswHostTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saved = uds_host.selected()
        canflash.use_layout(canflash.target_from_project(uds_host.OTHER_PROJECT))
        cls.app = uds_host.HostApp(uds_host.app_dll(BSW_APP_DLL, uds_host.OTHER_PROJECT, BSW_PROFILE))
        cls.app.dll.diag_stub_available.argtypes = [ctypes.c_int]
        cls.app.dll.app_snapshot_did.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint8), ctypes.c_uint32]
        cls.desc = uds_host.description(BSW_PROFILE)

    @classmethod
    def tearDownClass(cls):
        canflash.use_layout(cls.saved)

    def setUp(self):
        self.app.power_on(canflash.bl_version())
        self.app.dll.diag_stub_available(1)

    def tearDown(self):
        self.app.dll.diag_stub_available(1)
        self.assertEqual(self.app.dll.app_assert_count(), 0, "ETL assert in the stack")

    def send(self, payload: bytes) -> bytes:
        """One request through udsoncan (segmented as needed), the response payload (positive or negative)."""
        if not hasattr(self, "_client"):
            bus = uds_host.EcuBus(self.app, None)
            self._client = canflash.make_client(bus)
            self._client.open()
            self.addCleanup(bus.shutdown)
            self.addCleanup(self._client.close)
            self.addCleanup(lambda: delattr(self, "_client"))
        try:
            return bytes(self._client.send_request(udsoncan.Request.from_payload(payload)).original_payload)
        except NegativeResponseException as e:
            return bytes(e.response.original_payload)

    def snapshot(self, did: int, length: int) -> tuple[int, bytes]:
        buf = (ctypes.c_uint8 * length)()
        result = self.app.dll.app_snapshot_did(did, buf, length)
        return result, bytes(buf)

    def set_values(self):
        stub = self.app.stub_set
        stub("did.EcuStatus.state", 1)                 # RUN
        stub("did.EcuStatus.wakeup", 0x00000201)       # POWER and CAN1
        stub("did.EcuStatus.canWake", 1)
        stub("did.Supply.voltage", 12000)              # mV of bsw.power.supply_mv
        stub("did.Supply.quality", 1)                  # QLF_OK
        stub("did.Supply.uptime", 0x01020304)
        stub("did.Poti.voltage", 3300)
        stub("did.Poti.value", -5)
        stub("did.Poti.quality", 32)                   # QLF_ERROR_RANGE
        stub("did.Poti.speed", 1000)
        stub("did.Bandgap.voltage", struct.unpack("<I", struct.pack("<f", 1200.0))[0])

    def test_ecu_state_and_start_reasons(self):
        self.set_values()
        self.assertEqual(self.send(bytes([0x22, 0x04, 0x00])),
                         bytes([0x62, 0x04, 0x00, 0x01, 0x00, 0x00, 0x02, 0x01, 0x01]))

    def test_services_with_byte_order(self):
        self.set_values()
        self.assertEqual(self.send(bytes([0x22, 0x04, 0x01])),
                         bytes([0x62, 0x04, 0x01, 0x2E, 0xE0, 0x01, 0x04, 0x03, 0x02, 0x01]))

    def test_mixed_sources_in_the_order_of_the_description(self):
        """constant, adc.mv, adc.phys (int32, negative), adc.qlf, a port and the session of the stack."""
        self.set_values()
        self.assertEqual(self.send(bytes([0x22, 0x04, 0x02])),
                         bytes([0x62, 0x04, 0x02, 0x07, 0x0C, 0xE4, 0xFF, 0xFF, 0xFF, 0xFB, 0x20, 0x03, 0xE8, 0x01]))

    def test_float_value(self):
        self.set_values()
        self.assertEqual(self.send(bytes([0x22, 0x04, 0x03])), bytes([0x62, 0x04, 0x03, 0x44, 0x96, 0x00, 0x00]))

    def test_several_identifiers_split_by_the_description(self):
        self.set_values()
        response = self.send(bytes([0x22, 0x04, 0x02, 0x04, 0x00, 0x04, 0x03, 0x04, 0x01]))
        records = diagdesc.split_read_response(self.desc, response)
        self.assertEqual([(r.identifier, len(r.data)) for r in records],
                         [(0x0402, 11), (0x0400, 6), (0x0403, 4), (0x0401, 7)])
        poti, status, _, supply = records
        self.assertEqual(poti.elements["value"], bytes([0xFF, 0xFF, 0xFF, 0xFB]))
        self.assertEqual(status.elements["wakeup"], bytes([0x00, 0x00, 0x02, 0x01]))
        self.assertEqual(supply.elements["uptime"], bytes([0x04, 0x03, 0x02, 0x01]))

    def test_value_that_cannot_be_read_is_conditions_not_correct(self):
        """A failed conversion (the RTE reports false) is NRC 0x22, not a made up value."""
        self.set_values()
        self.app.dll.diag_stub_available(0)
        self.assertEqual(self.send(bytes([0x22, 0x04, 0x02])), bytes([0x7F, 0x22, 0x22]))
        self.app.dll.diag_stub_available(1)
        self.assertEqual(self.send(bytes([0x22, 0x04, 0x02]))[:3], bytes([0x62, 0x04, 0x02]))

    def test_snapshot_reads_the_same_bytes(self):
        """The fault memory reads a snapshot identifier through the same job as service 22 (NVM-03)."""
        self.set_values()
        response = self.send(bytes([0x22, 0x04, 0x02]))
        self.assertEqual(self.snapshot(0x0402, 11), (1, response[3:]))
        self.app.dll.diag_stub_available(0)
        self.assertEqual(self.snapshot(0x0402, 11)[0], 0)        # refused: the fault memory stores 0xFF

    def test_bsw_identifier_is_not_writable(self):
        self.assertEqual(self.send(bytes([0x10, 0x03]))[:2], bytes([0x50, 0x03]))
        self.assertEqual(self.send(bytes([0x2E, 0x04, 0x00, 0x01, 0, 0, 0, 0, 0])), bytes([0x7F, 0x2E, 0x31]))
        # the port identifier next to them is written as before
        self.assertEqual(self.send(bytes([0x2E, 0x04, 0x04, 0x01, 0x02])), bytes([0x6E, 0x04, 0x04]))
        self.assertEqual(self.app.stub_get("did.Setpoint.setpoint"), 0x0102)


if __name__ == "__main__":
    unittest.main()
