"""Host tests of the fault memory services 19 and 14 (NVM-04) with udsoncan against the application UDS stack.

The stack of ecu/uds runs as DLL with a real fault memory (libs/bsw/dem, dem::Manager) whose tables tools/rtegen
generates from an own test description, dtc_profile/diag.yaml: five DTCs, both kinds of debouncing, confirmation
after one and after two operation cycles, two snapshot records of one DTC, extended data records of counters and of a
stored identifier. The diagnostic addresses are those of projects/buildtest_s32k148_min (0x6F0/0x6DF/0x6F8, filler
0xAA), not the eval board's. The test is the BSW monitor: it reports the results (app_dem_report), runs the cycles
of the fault memory (app_dem_main) and restarts the operation cycle (app_dem_restart_cycle).

The message examples of ISO 14229-1 (2026) 11.2.5 and 11.3.5 are rebuilt as own test vectors: same DTC numbers,
identifier, record numbers and data bytes, the DTC states produced by the fault memory itself. Where this fault
memory cannot be in the state an example assumes, the test says so at the vector (only clause numbers are cited, the
standard is licensed and lies in doc/confidential).

    .venv/Scripts/python ecu/uds/test/host/test_app_uds_dtc.py
"""

import ctypes
import os
import sys
import unittest
from pathlib import Path

import udsoncan
from udsoncan.exceptions import NegativeResponseException

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools" / "canflash"))
sys.path.insert(0, str(ROOT / "tools" / "test"))
import build_app_uds_host  # noqa: E402
import canflash  # noqa: E402
import hostbuild  # noqa: E402
from test_app_uds_host import APP_DLL, EVAL_PROJECT, OTHER_PROJECT, EcuBus, HostApp, app_dll  # noqa: E402
from test_bootloader_host import selected  # noqa: E402
from nxconfig import diagdesc  # noqa: E402  (derived DTC format and availability mask of the description)

PROFILE = HERE / "dtc_profile" / "diag.yaml"
DTC_DLL = Path(os.environ.get("APP_UDS_HOST_DLL_DTC", ROOT / "build" / "host" / "app_uds_host_dtc.dll"))

# dem::Result
PASSED, FAILED, PREPASSED, PREFAILED = 0, 1, 2, 3
# DTCs of the profile, in the order of its table
BATTERY, CLUTCH, AC, SPEED, SUPPLY = 0x0A9B17, 0x080511, 0x25221F, 0x123456, 0x789ABC
ALL_DTCS = (BATTERY, CLUTCH, AC, SPEED, SUPPLY)
INITIAL = 0x50            # status after a clear: testNotCompletedSinceLastClear, testNotCompletedThisOperationCycle
AVAILABILITY = 0x7F       # every status bit but warningIndicatorRequested (no warning lamp)
# groups of DTCs 14 clears besides all and single DTCs (Annex D.1): the functional safety group holds SPEED, a
# manufacturer group the range 0x0A0000..0x0AFFFF (BATTERY)
GROUPS = ((0xFFFFD0, SPEED, SPEED), (0x0A0000, 0x0A0000, 0x0AFFFF))


def dtc_dll() -> Path:
    """The DLL with the fault memory of the profile, rebuilt when a source changed (checksum, COM-09)."""
    sources = list(HERE.glob("*.cpp")) + list((ROOT / "ecu" / "uds").rglob("*.[ch]*"))
    sources += list((ROOT / "libs" / "bsw" / "dem" / "include").rglob("*.h"))
    sources += [PROFILE, OTHER_PROJECT, HERE / "build_app_uds_host.py", ROOT / "tools" / "rtegen" / "rtegen.py"]
    sources += list(OTHER_PROJECT.parent.glob("*.yaml")) + list((ROOT / "tools" / "diaggen").glob("*.py"))
    sources += build_app_uds_host.C_SOURCES + [ROOT / "bootloader/include/bl_config.h"]
    digest = hostbuild.checksum(sorted(set(sources)), extra=(str(OTHER_PROJECT), str(PROFILE), "dem"))
    hostbuild.build_if_changed(DTC_DLL, digest, lambda: build_app_uds_host.build(
        DTC_DLL, OTHER_PROJECT, diag=PROFILE, dem=True))
    return DTC_DLL


def dtc_bytes(number: int) -> bytes:
    return number.to_bytes(3, "big")


class FaultMemoryApp(HostApp):
    """The host application with the fault memory of the profile."""

    def __init__(self, path: Path):
        super().__init__(path)
        for name in ("app_dem_report", "app_dem_status", "app_dem_fdc", "app_dem_stored"):
            getattr(self.dll, name).argtypes = [ctypes.c_uint32] + ([ctypes.c_uint8] if name == "app_dem_report"
                                                                     else [])
        self.dll.app_dem_main.argtypes = [ctypes.c_uint32]
        self.dll.app_dem_restart_cycle.argtypes = [ctypes.c_uint8]

    def cycle(self, **results):
        """One cycle of the fault memory after the given reports (name of a DTC constant -> dem::Result)."""
        for name, result in results.items():
            self.report(globals()[name], result)
        self.dll.app_dem_main(1)

    def report(self, number: int, result: int):
        assert self.dll.app_dem_report(number, result) == 0, hex(number)

    def main(self, cycles: int = 1):
        self.dll.app_dem_main(cycles)

    def restart(self):
        """End of the operation cycle and start of the next one (a power cycle of the fault memory)."""
        self.dll.app_dem_restart_cycle(0)

    def status(self, number: int) -> int:
        return self.dll.app_dem_status(number)

    def set_groups(self, groups):
        flat = (ctypes.c_uint32 * (3 * len(groups)))(*[v for g in groups for v in g])
        self.dll.app_set_dtc_groups(flat, len(groups))


class FaultMemoryServicesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saved = selected()
        canflash.use_layout(canflash.target_from_project(OTHER_PROJECT))
        cls.phys, cls.func = canflash.CFG["BL_CAN_ID_REQUEST_PHYS"], canflash.CFG["BL_CAN_ID_REQUEST_FUNC"]
        cls.resp, cls.pad = canflash.CFG["BL_CAN_ID_RESPONSE"], canflash.CFG["BL_ISOTP_PADDING"]
        cls.app = FaultMemoryApp(dtc_dll())
        assert cls.app.dll.app_dem_present() == 1
        cls.app.set_groups(GROUPS)

    @classmethod
    def tearDownClass(cls):
        canflash.use_layout(cls.saved)

    def setUp(self):
        # a fresh ECU: stack in the default session, fault memory cleared (empty NvM), cycle Ignition running
        self.app.power_on(canflash.bl_version())
        for element, value in (("coolantTemperature", 0xA6), ("throttlePosition", 0x66), ("engineSpeed", 0x0750),
                               ("manifoldPressure", 0x20)):
            self.app.stub_set(f"did.EnvironmentAtFailure.{element}", value)
        self.app.stub_set("did.SupplyVoltage.supply", 12000)
        self._client = None
        self.addCleanup(self.close_client)

    @property
    def client(self):
        """udsoncan client on the ECU, opened on first use. Its receive thread polls the DLL, so it is closed before a
        test sends raw frames (request_raw) and opened again by the next use."""
        if self._client is None:
            self._bus = EcuBus(self.app, None)
            self._client = canflash.make_client(self._bus)
            self._client.config["data_identifiers"].update({0x4711: ">BBHB", 0x4712: ">H", 0xF186: "B"})
            self._client.open()
        return self._client

    def close_client(self):
        if self._client is not None:
            self._client.close()
            self._bus.shutdown()
            self._client = None

    def tearDown(self):
        self.assertEqual(self.app.dll.app_assert_count(), 0, "ETL assert in the stack")

    def send(self, payload: bytes) -> bytes:
        try:
            return bytes(self.client.send_request(udsoncan.Request.from_payload(payload)).original_payload)
        except NegativeResponseException as e:
            return bytes(e.response.original_payload)

    def request_raw(self, cid: int, payload: bytes, wait_ms: float = 60.0) -> list[bytes]:
        """Single frame on `cid`, the payloads of the responses within `wait_ms`."""
        self.close_client()
        frame = bytes([len(payload)]) + payload
        self.assertTrue(self.app.rx(cid, frame.ljust(8, bytes([self.pad]))))
        out = []
        for _ in range(int(wait_ms)):
            self.app.poll_ms(1)
            while (f := self.app.tx()) is not None:
                self.assertEqual(f[0], self.resp)
                out.append(f[1][1:1 + f[1][0]])
        return out

    def statuses(self) -> dict[int, int]:
        return {n: self.app.status(n) for n in ALL_DTCS}

    # ------------------------------------------------------------ states of the examples, made by the fault memory

    def example_1_states(self):
        """11.3.5.2: CLUTCH 0x24 (pending, failed last cycle), BATTERY 0x26 (failed and passed this cycle, pending),
        AC 0x2F (confirmed after two cycles with a failure)."""
        self.app.cycle(CLUTCH=FAILED, AC=FAILED)
        self.app.restart()
        self.app.cycle(CLUTCH=PASSED, BATTERY=FAILED)       # AC fails again (its level stays): confirmed
        self.app.cycle(BATTERY=PASSED)
        self.assertEqual(self.statuses(), {BATTERY: 0x26, CLUTCH: 0x24, AC: 0x2F, SPEED: INITIAL, SUPPLY: INITIAL})

    def example_2_states(self):
        """11.3.5.3: BATTERY 0x24, AC 0x00 (tested, never failed), CLUTCH 0x2F."""
        self.app.cycle(BATTERY=FAILED, CLUTCH=FAILED, AC=PASSED)
        self.app.restart()
        self.app.cycle(BATTERY=PASSED)
        self.assertEqual(self.statuses(), {BATTERY: 0x24, CLUTCH: 0x2F, AC: 0x00, SPEED: INITIAL, SUPPLY: INITIAL})

    def example_3_states(self):
        """11.3.5.4: AC 0x24, BATTERY 0x00."""
        self.app.cycle(AC=FAILED, BATTERY=PASSED)
        self.app.restart()
        self.app.cycle(AC=PASSED)
        self.assertEqual(self.statuses(), {BATTERY: 0x00, CLUTCH: INITIAL, AC: 0x24, SPEED: INITIAL, SUPPLY: INITIAL})

    def store_speed_and_supply(self):
        """11.3.5.5: SPEED stored with its two snapshot records (confirmed in the second cycle with a failure),
        SUPPLY with one (confirmed at once); then SPEED passes in a new cycle."""
        self.app.cycle(SPEED=FAILED, SUPPLY=FAILED)
        self.app.restart()
        self.app.cycle()                                   # SPEED fails again: confirmed and stored
        self.assertEqual(self.app.dll.app_dem_stored(SPEED), 1)
        self.assertEqual(self.app.dll.app_dem_stored(SUPPLY), 1)
        # the stored values stay what they were at the storage
        self.app.stub_set("did.EnvironmentAtFailure.coolantTemperature", 0x11)
        self.app.stub_set("did.SupplyVoltage.supply", 9000)

    # ------------------------------------------------------------------------------------------ 19 01 / 02 / 0A

    def test_example_1_number_of_confirmed_dtcs(self):
        """11.3.5.2 with mask 08: one confirmed DTC. The availability mask of this server is 7F, not 2F."""
        self.example_1_states()
        self.assertEqual(self.send(bytes([0x19, 0x01, 0x08])), bytes([0x59, 0x01, AVAILABILITY, 0x01, 0x00, 0x01]))
        response = self.client.get_number_of_dtc_by_status_mask(0x08)
        self.assertEqual(response.service_data.dtc_count, 1)
        self.assertEqual(response.service_data.status_availability.get_byte_as_int(), AVAILABILITY)
        self.assertEqual(response.service_data.dtc_format, udsoncan.Dtc.Format.ISO14229_1)
        # the stack answers what tools/nxconfig derives for the description (TOOL-43 exports the same values)
        self.assertEqual((AVAILABILITY, udsoncan.Dtc.Format.ISO14229_1),
                         (diagdesc.STATUS_AVAILABILITY_NO_WARNING_INDICATOR, diagdesc.DTC_FORMAT_ISO_14229_1))
        # every DTC that has any bit of the mask counts; bit 7 is not supported and matches nothing
        self.assertEqual(self.send(bytes([0x19, 0x01, 0x04])), bytes([0x59, 0x01, AVAILABILITY, 0x01, 0x00, 0x03]))
        self.assertEqual(self.send(bytes([0x19, 0x01, 0x80]))[-2:], bytes([0x00, 0x00]))
        self.assertEqual(self.send(bytes([0x19, 0x01, 0xFF]))[-2:], bytes([0x00, 0x05]))

    def test_example_2_dtcs_by_status_mask(self):
        """11.3.5.3 with mask 84: the vector of the standard byte for byte (bit 7 of the mask is ignored)."""
        self.example_2_states()
        expected = bytes([0x59, 0x02, 0x7F, 0x0A, 0x9B, 0x17, 0x24, 0x08, 0x05, 0x11, 0x2F])
        self.assertEqual(self.send(bytes([0x19, 0x02, 0x84])), expected)
        dtcs = self.client.get_dtc_by_status_mask(0x84).service_data.dtcs
        self.assertEqual([(d.id, d.status.get_byte_as_int()) for d in dtcs], [(BATTERY, 0x24), (CLUTCH, 0x2F)])

    def test_example_3_no_matching_dtc(self):
        """11.3.5.4 with mask 01: no DTC fails now, the response ends after the availability mask."""
        self.example_3_states()
        self.assertEqual(self.send(bytes([0x19, 0x02, 0x01])), bytes([0x59, 0x02, 0x7F]))
        self.assertEqual(self.client.get_dtc_by_status_mask(0x01).service_data.dtcs, [])

    def test_supported_dtcs(self):
        """19 0A: every DTC of the table with its status, whatever the status."""
        self.example_1_states()
        expected = bytes([0x59, 0x0A, 0x7F]) + b"".join(
            dtc_bytes(n) + bytes([s]) for n, s in ((BATTERY, 0x26), (CLUTCH, 0x24), (AC, 0x2F), (SPEED, INITIAL),
                                                   (SUPPLY, INITIAL)))
        self.assertEqual(self.send(bytes([0x19, 0x0A])), expected)
        dtcs = self.client.get_supported_dtc().service_data.dtcs
        self.assertEqual([d.id for d in dtcs], list(ALL_DTCS))

    # ------------------------------------------------------------------------------------------ 19 03 / 04

    def test_example_4_snapshot_identification(self):
        """11.3.5.5: two records of 123456, one of 789ABC - the vector of the standard byte for byte. The records
        of one DTC come in ascending order although the description names record 02 first."""
        self.assertEqual(self.send(bytes([0x19, 0x03])), bytes([0x59, 0x03]))
        self.store_speed_and_supply()
        expected = bytes([0x59, 0x03, 0x12, 0x34, 0x56, 0x01, 0x12, 0x34, 0x56, 0x02, 0x78, 0x9A, 0xBC, 0x01])
        self.assertEqual(self.send(bytes([0x19, 0x03])), expected)
        dtcs = self.client.get_dtc_snapshot_identification().service_data.dtcs
        self.assertEqual([(d.id, d.snapshots) for d in dtcs], [(SPEED, [1, 2]), (SUPPLY, [1])])

    def test_example_5_snapshot_record_by_dtc_number(self):
        """11.3.5.6: record 02 of 123456 with identifier 4711 and the five bytes of the example. The status differs:
        this fault memory stores snapshots when a DTC is confirmed (D.2.1), so the DTC is 0x2C (confirmed, passed
        again) instead of the 0x24 of the example."""
        self.store_speed_and_supply()
        self.app.restart()
        self.app.cycle(SPEED=PASSED)
        expected = bytes([0x59, 0x04, 0x12, 0x34, 0x56, 0x2C, 0x02, 0x01, 0x47, 0x11, 0xA6, 0x66, 0x07, 0x50, 0x20])
        self.assertEqual(self.send(bytes([0x19, 0x04, 0x12, 0x34, 0x56, 0x02])), expected)
        # udsoncan decodes the record with the codec of 4711
        dtc = self.client.get_dtc_snapshot_by_dtc_number(SPEED, 2).service_data.dtcs[0]
        self.assertEqual((dtc.id, dtc.status.get_byte_as_int()), (SPEED, 0x2C))
        self.assertEqual([(s.record_number, s.did, s.data) for s in dtc.snapshots],
                         [(2, 0x4711, (0xA6, 0x66, 0x0750, 0x20))])

    def test_all_snapshot_records_in_ascending_order(self):
        """Record FF: every record once, ascending; record 01 holds two identifiers (supply, session at storage)."""
        self.store_speed_and_supply()
        record_1 = bytes([0x01, 0x02, 0x47, 0x12]) + (12000).to_bytes(2, "big") + bytes([0xF1, 0x86, 0x01])
        record_2 = bytes([0x02, 0x01, 0x47, 0x11, 0xA6, 0x66, 0x07, 0x50, 0x20])
        self.assertEqual(self.send(bytes([0x19, 0x04, 0x12, 0x34, 0x56, 0xFF])),
                         bytes([0x59, 0x04, 0x12, 0x34, 0x56, 0x2F]) + record_1 + record_2)
        dtc = self.client.get_dtc_snapshot_by_dtc_number(SPEED, 0xFF).service_data.dtcs[0]
        self.assertEqual([(s.record_number, s.did) for s in dtc.snapshots], [(1, 0x4712), (1, 0xF186), (2, 0x4711)])

    def test_snapshot_of_a_dtc_without_entry(self):
        """A known record of a DTC that is not stored: only the DTC and its status (11.3.1.5)."""
        self.app.cycle(SPEED=FAILED)                      # pending, not yet confirmed: no entry
        for record in (0x01, 0x02, 0xFF):
            with self.subTest(record=record):
                self.assertEqual(self.send(bytes([0x19, 0x04, 0x12, 0x34, 0x56, record])),
                                 bytes([0x59, 0x04, 0x12, 0x34, 0x56, 0x27]))
        # a DTC without snapshot records answers FF the same way
        self.assertEqual(self.send(bytes([0x19, 0x04]) + dtc_bytes(AC) + bytes([0xFF])),
                         bytes([0x59, 0x04]) + dtc_bytes(AC) + bytes([INITIAL]))

    def test_snapshot_parameters_out_of_range(self):
        """11.3.4: unknown DTC, group value as DTCMaskRecord, record the DTC does not have, record 00 - NRC 0x31."""
        self.store_speed_and_supply()
        for request in (bytes([0x19, 0x04, 0x12, 0x34, 0x57, 0x01]), bytes([0x19, 0x04, 0xFF, 0xFF, 0xFF, 0x01]),
                        bytes([0x19, 0x04, 0x12, 0x34, 0x56, 0x03]), bytes([0x19, 0x04, 0x12, 0x34, 0x56, 0x00]),
                        bytes([0x19, 0x04, 0x78, 0x9A, 0xBC, 0x02])):
            with self.subTest(request=request.hex()):
                self.assertEqual(self.send(request), bytes([0x7F, 0x19, 0x31]))

    # ------------------------------------------------------------------------------------------ 19 06 / 14

    def test_example_7_extended_data_by_dtc_number(self):
        """11.3.5.8: 123456 with status 24, record 05 = 17 and record 10 = 79 - the vector of the standard byte for
        byte. Record 05 counts the operation cycles since the first failure (23), record 10 is the fault
        detection counter (121, prefailed). Record 20 holds an identifier and is left out: the DTC is not stored."""
        self.app.cycle(SPEED=FAILED)                      # cycle 1: first failure
        for _ in range(21):                               # cycles 2..22 without failure
            self.app.restart()
            self.app.cycle(SPEED=PASSED)
        self.app.restart()
        self.app.cycle(SPEED=FAILED)                      # cycle 23: failed again, still one cycle short of confirmed
        self.app.restart()
        self.app.cycle(SPEED=PASSED)                      # cycle 24: passed, then eleven prefailed samples
        self.app.report(SPEED, PREFAILED)
        self.app.main(11)
        self.assertEqual((self.app.status(SPEED), self.app.dll.app_dem_fdc(SPEED)), (0x24, 121))
        expected = bytes([0x59, 0x06, 0x12, 0x34, 0x56, 0x24, 0x05, 0x17, 0x10, 0x79])
        self.assertEqual(self.send(bytes([0x19, 0x06, 0x12, 0x34, 0x56, 0xFF])), expected)
        # the prefailed DTC in 19 14 (0 < FDC < 7F)
        self.assertEqual(self.send(bytes([0x19, 0x14])), bytes([0x59, 0x14, 0x12, 0x34, 0x56, 0x79]))
        fdc = self.client.get_dtc_fault_counter().service_data.dtcs
        self.assertEqual([(d.id, d.fault_counter) for d in fdc], [(SPEED, 0x79)])
        # one record at a time, through udsoncan
        data = self.client.get_dtc_extended_data_by_dtc_number(SPEED, 0x10, data_size=1).service_data.dtcs[0]
        self.assertEqual([(e.record_number, e.raw_data) for e in data.extended_data], [(0x10, b"\x79")])
        # record 20 exists but has nothing to report yet
        self.assertEqual(self.send(bytes([0x19, 0x06, 0x12, 0x34, 0x56, 0x20])),
                         bytes([0x59, 0x06, 0x12, 0x34, 0x56, 0x24]))

    def test_extended_record_with_a_stored_identifier(self):
        """Record 20 of a stored DTC: occurrence counter now, supply voltage as stored."""
        self.store_speed_and_supply()
        self.assertEqual(self.send(bytes([0x19, 0x06, 0x12, 0x34, 0x56, 0x20])),
                         bytes([0x59, 0x06, 0x12, 0x34, 0x56, 0x2F, 0x20, 0x01]) + (12000).to_bytes(2, "big"))
        self.assertEqual(self.send(bytes([0x19, 0x06, 0x12, 0x34, 0x56, 0xFF])),
                         bytes([0x59, 0x06, 0x12, 0x34, 0x56, 0x2F, 0x05, 0x01, 0x10, 0x7F, 0x20, 0x01, 0x2E, 0xE0]))

    def test_extended_parameters_out_of_range(self):
        """FE (all OBD records) without OBD, a record the DTC does not have, an unknown DTC: NRC 0x31."""
        for request in (bytes([0x19, 0x06, 0x12, 0x34, 0x56, 0xFE]), bytes([0x19, 0x06, 0x12, 0x34, 0x56, 0x01]),
                        bytes([0x19, 0x06, 0x00, 0x00, 0x01, 0xFF]), bytes([0x19, 0x06, 0x25, 0x22, 0x1F, 0x01])):
            with self.subTest(request=request.hex()):
                self.assertEqual(self.send(request), bytes([0x7F, 0x19, 0x31]))
        # a DTC without extended data records answers FF with its status only
        self.assertEqual(self.send(bytes([0x19, 0x06]) + dtc_bytes(AC) + bytes([0xFF])),
                         bytes([0x59, 0x06]) + dtc_bytes(AC) + bytes([INITIAL]))

    def test_fault_detection_counter_list(self):
        """19 14 lists only prefailed DTCs: not failed ones (FDC 127) and not passed ones (FDC below 0)."""
        self.assertEqual(self.send(bytes([0x19, 0x14])), bytes([0x59, 0x14]))
        self.app.report(CLUTCH, PREFAILED)
        self.app.main(4)                                   # 4 of 10 samples
        self.app.cycle(SUPPLY=FAILED, AC=PREPASSED)       # CLUTCH 5 of 10: FDC 5 * 127 / 10 = 63; SUPPLY failed
                                                           # (127), AC below zero
        self.assertEqual(self.send(bytes([0x19, 0x14])), bytes([0x59, 0x14, 0x08, 0x05, 0x11, 63]))

    # ------------------------------------------------------------------------------------------ negative responses

    def test_sub_functions_outside_the_scope(self):
        """05 (the 11.3.5.7 example: no DTCStoredData), severity, first/most recent, permanent, user memory, OBD."""
        for subfunction in (0x00, 0x05, 0x07, 0x08, 0x09, 0x0B, 0x0C, 0x0D, 0x0E, 0x15, 0x16, 0x17, 0x18, 0x19,
                            0x1A, 0x42, 0x55, 0x56, 0x7F):
            with self.subTest(subfunction=hex(subfunction)):
                self.assertEqual(self.send(bytes([0x19, subfunction, 0x02])), bytes([0x7F, 0x19, 0x12]))

    def test_length_of_every_sub_function(self):
        """7.7: missing sub-function 0x13, then 0x12 before the exact length of a supported one (0x13)."""
        self.assertEqual(self.request_raw(self.phys, bytes([0x19])), [bytes([0x7F, 0x19, 0x13])])
        for request in (bytes([0x19, 0x01]), bytes([0x19, 0x02, 0x08, 0x00]), bytes([0x19, 0x03, 0x00]),
                        bytes([0x19, 0x04, 0x12, 0x34, 0x56]), bytes([0x19, 0x06, 0x12, 0x34, 0x56, 0xFF, 0x00]),
                        bytes([0x19, 0x0A, 0x00]), bytes([0x19, 0x14, 0xFF])):
            with self.subTest(request=request.hex()):
                self.assertEqual(self.send(request), bytes([0x7F, 0x19, 0x13]))
        self.assertEqual(self.send(bytes([0x19, 0x05])), bytes([0x7F, 0x19, 0x12]))

    def test_suppressed_positive_response_and_functional_requests(self):
        """Bit 7 of the sub-function suppresses the positive response; to a functional request 0x12 and 0x31 are
        not sent (7.5), a positive response is."""
        self.example_1_states()
        self.assertEqual(self.request_raw(self.phys, bytes([0x19, 0x81, 0x08])), [])
        self.assertEqual(self.request_raw(self.phys, bytes([0x19, 0x81])), [bytes([0x7F, 0x19, 0x13])])
        self.assertEqual(self.request_raw(self.func, bytes([0x19, 0x01, 0x08])),
                         [bytes([0x59, 0x01, 0x7F, 0x01, 0x00, 0x01])])
        self.assertEqual(self.request_raw(self.func, bytes([0x19, 0x05, 0x01])), [])
        self.assertEqual(self.request_raw(self.func, bytes([0x19, 0x04, 0x00, 0x00, 0x01, 0x01])), [])

    # ------------------------------------------------------------------------------------------ 14

    def test_clear_all_dtcs(self):
        """14 FFFFFF: status 0x50, snapshots and extended data gone, counters zero (11.2.1)."""
        self.store_speed_and_supply()
        self.assertEqual(self.send(bytes([0x14, 0xFF, 0xFF, 0xFF])), bytes([0x54]))
        self.assertEqual(set(self.statuses().values()), {INITIAL})
        self.assertEqual(self.send(bytes([0x19, 0x03])), bytes([0x59, 0x03]))
        self.assertEqual(self.send(bytes([0x19, 0x06, 0x12, 0x34, 0x56, 0x05])),
                         bytes([0x59, 0x06, 0x12, 0x34, 0x56, 0x50, 0x05, 0x00]))
        # udsoncan's default group is FFFFFF as well; a positive response is due with nothing stored
        self.assertTrue(self.client.clear_dtc().positive)

    def test_clear_one_dtc(self):
        """A single DTC number clears that DTC only."""
        self.example_2_states()
        self.assertTrue(self.client.clear_dtc(CLUTCH).positive)
        self.assertEqual(self.statuses(), {BATTERY: 0x24, CLUTCH: INITIAL, AC: 0x00, SPEED: INITIAL, SUPPLY: INITIAL})

    def test_clear_groups(self):
        """Annex D.1: the groups of the project (here the safety group FFFFD0 and a manufacturer group)."""
        self.store_speed_and_supply()
        self.app.cycle(BATTERY=FAILED)
        self.assertEqual(self.send(bytes([0x14, 0xFF, 0xFF, 0xD0])), bytes([0x54]))
        self.assertEqual((self.app.status(SPEED), self.app.status(SUPPLY)), (INITIAL, 0x2F))
        self.assertEqual(self.app.dll.app_dem_stored(SPEED), 0)
        self.assertEqual(self.send(bytes([0x14, 0x0A, 0x00, 0x00])), bytes([0x54]))
        self.assertEqual((self.app.status(BATTERY), self.app.status(SUPPLY)), (INITIAL, 0x2F))

    def test_example_11_2_5_emissions_group(self):
        """11.2.5 clears the emissions group FFFF33. This server has no emissions related DTCs and does not belong to
        that group: physically NRC 0x31, functionally no response (7.5), nothing is cleared."""
        self.example_1_states()
        before = self.statuses()
        self.assertEqual(self.send(bytes([0x14, 0xFF, 0xFF, 0x33])), bytes([0x7F, 0x14, 0x31]))
        self.assertEqual(self.request_raw(self.func, bytes([0x14, 0xFF, 0xFF, 0x33])), [])
        self.assertEqual(self.statuses(), before)
        # functionally to all groups: every server answers
        self.assertEqual(self.request_raw(self.func, bytes([0x14, 0xFF, 0xFF, 0xFF])), [bytes([0x54])])
        self.assertEqual(set(self.statuses().values()), {INITIAL})

    def test_clear_negative_responses(self):
        """11.2.4: length 0x13, unknown DTC or group and MemorySelection 0x31, no fault memory 0x22."""
        self.example_1_states()
        for request, nrc in ((bytes([0x14]), 0x13), (bytes([0x14, 0xFF, 0xFF]), 0x13),
                             (bytes([0x14, 0xFF, 0xFF, 0xFF, 0x00, 0x00]), 0x13),
                             (bytes([0x14, 0xFF, 0xFF, 0xFF, 0x00]), 0x31), (bytes([0x14, 0x12, 0x34, 0x57]), 0x31),
                             (bytes([0x14, 0x00, 0x00, 0x00]), 0x31), (bytes([0x14, 0xFF, 0xFF, 0x00]), 0x31)):
            with self.subTest(request=request.hex()):
                self.assertEqual(self.send(request), bytes([0x7F, 0x14, nrc]))
        self.assertEqual(self.app.status(AC), 0x2F)
        self.app.dll.app_dem_registered(0)
        try:
            self.assertEqual(self.send(bytes([0x14, 0xFF, 0xFF, 0xFF])), bytes([0x7F, 0x14, 0x22]))
            # 19 answers like a server without DTCs until the fault memory is there
            self.assertEqual(self.send(bytes([0x19, 0x01, 0xFF])), bytes([0x59, 0x01, 0x7F, 0x01, 0x00, 0x00]))
            self.assertEqual(self.send(bytes([0x19, 0x0A])), bytes([0x59, 0x0A, 0x7F]))
            self.assertEqual(self.send(bytes([0x19, 0x04, 0x25, 0x22, 0x1F, 0xFF])), bytes([0x7F, 0x19, 0x31]))
        finally:
            self.app.dll.app_dem_registered(1)
        self.assertEqual(self.app.status(AC), 0x2F)

    # ------------------------------------------------------------------------------------------ 85 with 19/14

    def test_control_dtc_setting_freezes_the_status_but_not_the_clear(self):
        """85 02 in the extended session: no status change (D.2.1), 14 still clears; 85 01 and the status moves."""
        self.assertEqual(self.send(bytes([0x10, 0x03]))[:2], bytes([0x50, 0x03]))
        self.assertEqual(self.send(bytes([0x85, 0x02])), bytes([0xC5, 0x02]))
        self.app.cycle(SUPPLY=FAILED)
        self.assertEqual(self.send(bytes([0x19, 0x02, 0x2F])), bytes([0x59, 0x02, 0x7F]))
        self.assertEqual(self.send(bytes([0x19, 0x01, 0x40])), bytes([0x59, 0x01, 0x7F, 0x01, 0x00, 0x05]))
        self.assertEqual(self.send(bytes([0x85, 0x01])), bytes([0xC5, 0x01]))
        # COM-43: what the monitor reported while 85 was off is not taken over - no DTC after 85 01 either
        self.app.main(3)
        self.assertEqual(self.send(bytes([0x19, 0x02, 0x2F])), bytes([0x59, 0x02, 0x7F]))
        self.app.cycle(SUPPLY=FAILED)
        self.assertEqual(self.send(bytes([0x19, 0x02, 0x08])), bytes([0x59, 0x02, 0x7F]) + dtc_bytes(SUPPLY) + b"\x2f")
        self.assertEqual(self.send(bytes([0x85, 0x02])), bytes([0xC5, 0x02]))
        self.assertEqual(self.send(bytes([0x14, 0xFF, 0xFF, 0xFF])), bytes([0x54]))
        self.assertEqual(self.app.status(SUPPLY), INITIAL)
        # 19 and 14 need no other session than the default one
        self.assertEqual(self.send(bytes([0x10, 0x01]))[:2], bytes([0x50, 0x01]))
        self.assertEqual(self.send(bytes([0x19, 0x01, 0x08]))[:2], bytes([0x59, 0x01]))


class WithoutFaultMemoryTest(unittest.TestCase):
    """The eval board's DLL has no fault memory in dem::registry: 19 answers like a server without DTCs, 14 with
    conditionsNotCorrect (the product starts the fault memory after the NvM, the stack answers before)."""

    @classmethod
    def setUpClass(cls):
        cls.app = HostApp(app_dll(APP_DLL, EVAL_PROJECT))

    def setUp(self):
        self.app.power_on()

    def payloads(self, payload: bytes) -> list[bytes]:
        pad = canflash.CFG["BL_ISOTP_PADDING"]
        frame = (bytes([len(payload)]) + payload).ljust(8, bytes([pad]))
        self.assertTrue(self.app.rx(canflash.CFG["BL_CAN_ID_REQUEST_PHYS"], frame))
        out = []
        for _ in range(60):
            self.app.poll_ms(1)
            while (f := self.app.tx()) is not None:
                out.append(f[1][1:1 + f[1][0]])
        return out

    def test_services_answer_without_a_fault_memory(self):
        self.assertEqual(self.app.dll.app_dem_present(), 0)
        self.assertEqual(self.payloads(bytes([0x19, 0x01, 0xFF])), [bytes([0x59, 0x01, 0x7F, 0x01, 0x00, 0x00])])
        self.assertEqual(self.payloads(bytes([0x19, 0x02, 0xFF])), [bytes([0x59, 0x02, 0x7F])])
        self.assertEqual(self.payloads(bytes([0x19, 0x14])), [bytes([0x59, 0x14])])
        self.assertEqual(self.payloads(bytes([0x19, 0x06, 0x90, 0x40, 0x01, 0xFF])), [bytes([0x7F, 0x19, 0x31])])
        self.assertEqual(self.payloads(bytes([0x14, 0xFF, 0xFF, 0xFF])), [bytes([0x7F, 0x14, 0x22])])


if __name__ == "__main__":
    unittest.main(verbosity=2)
