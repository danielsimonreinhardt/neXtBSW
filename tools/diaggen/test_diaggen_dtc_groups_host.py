"""NVM-09: the DTC groups of a description (`dtc_groups`) cleared with 14 by the application UDS stack, via udsoncan.

Chain under test: diag.yaml `dtc_groups` -> tools/diaggen diag/DiagDtcGroups.h (diagDtcGroups()) -> the table of
uds::app::ClearDiagnosticInformationService -> which DTCs of a real fault memory (dem::Manager) 14 clears. The stack
runs as the DLL of ecu/uds/test/host/test_app_uds_dtc.py (own profile dtc_profile/diag.yaml, diagnostic addresses of
projects/buildtest_s32k148_min, not the eval board). The profile has no groups of its own: this test adds three to a
copy of it, renders the header with tools/diaggen and hands exactly the generated rows to the DLL
(app_set_dtc_groups), so a wrong number, a swapped first/last or a lost row in the generator turns the test red.
In the product DiagSystem hands the same table to DiagStack (target build).

The checks of `dtc_groups` against ISO 14229-1 Annex D.1 (only clause numbers are cited) are tested in
tools/nxconfig/test_diagdesc.py (DtcGroups), the rendering alone in test_diaggen.py.

Run: .venv/Scripts/python -m unittest tools/diaggen/test_diaggen_dtc_groups_host.py
"""

import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
UDS_HOST = REPO / "ecu" / "uds" / "test" / "host"
sys.path.insert(0, str(UDS_HOST))
sys.path.insert(0, str(HERE))

import diaggen  # noqa: E402
import test_app_uds_dtc as dtc  # noqa: E402  (DLL with the fault memory, helpers of the NVM-04 test)

canflash = dtc.canflash
BATTERY, CLUTCH, AC, SPEED, SUPPLY = dtc.BATTERY, dtc.CLUTCH, dtc.AC, dtc.SPEED, dtc.SUPPLY
FAILED, INITIAL = dtc.FAILED, dtc.INITIAL

# groups of the test: a functional group (safety, Annex D.1) with SPEED alone, a manufacturer group with two DTCs
# (CLUTCH 0x080511, BATTERY 0x0A9B17) and one without a DTC of this server
DTC_GROUPS = [
    {"name": "Safety", "group": 0xFFFFD0, "first": SPEED, "last": SPEED},
    {"name": "Chassis", "group": 0x080000, "first": 0x080000, "last": 0x0FFFFF},
    {"name": "Empty", "group": 0x300000, "first": 0x300000, "last": 0x3FFFFF},
]
ROW = re.compile(r"\{0x([0-9A-F]{6})U, 0x([0-9A-F]{6})U, 0x([0-9A-F]{6})U\}, // (\w+)")


def generated_rows(groups) -> list[tuple[int, int, int]]:
    """diag/DiagDtcGroups.h of the NVM-04 profile with `groups`, parsed back into (group, first, last)."""
    tmp = Path(tempfile.mkdtemp(prefix="nx_dtcgroups_"))
    try:
        data = yaml.safe_load(dtc.PROFILE.read_text(encoding="utf-8"))
        data["dtc_groups"] = groups
        path = tmp / "diag.yaml"
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        header = diaggen.plan(diaggen.load_file(path)).dtc_groups_h
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    rows = ROW.findall(header)
    assert [r[3] for r in rows] == [g["name"] for g in groups], header
    return [(int(g, 16), int(f, 16), int(la, 16)) for g, f, la, _ in rows]


class DtcGroupsHostTest(unittest.TestCase):
    """14 with the groups diaggen generated, against the fault memory of the NVM-04 profile."""

    @classmethod
    def setUpClass(cls):
        cls.saved = dtc.selected()
        canflash.use_layout(canflash.target_from_project(dtc.OTHER_PROJECT))
        cls.phys, cls.func = canflash.CFG["BL_CAN_ID_REQUEST_PHYS"], canflash.CFG["BL_CAN_ID_REQUEST_FUNC"]
        cls.resp, cls.pad = canflash.CFG["BL_CAN_ID_RESPONSE"], canflash.CFG["BL_ISOTP_PADDING"]
        cls.app = dtc.FaultMemoryApp(dtc.dtc_dll())
        assert cls.app.dll.app_dem_present() == 1
        cls.rows = generated_rows(DTC_GROUPS)
        cls.app.set_groups(cls.rows)

    @classmethod
    def tearDownClass(cls):
        cls.app.set_groups(dtc.GROUPS)              # the NVM-04 groups again, in case the DLL is shared
        canflash.use_layout(cls.saved)

    # the fresh ECU, the udsoncan client and the helpers of the NVM-04 test
    setUp = dtc.FaultMemoryServicesTest.setUp
    tearDown = dtc.FaultMemoryServicesTest.tearDown
    client = dtc.FaultMemoryServicesTest.client
    close_client = dtc.FaultMemoryServicesTest.close_client
    send = dtc.FaultMemoryServicesTest.send
    request_raw = dtc.FaultMemoryServicesTest.request_raw
    statuses = dtc.FaultMemoryServicesTest.statuses
    store_speed_and_supply = dtc.FaultMemoryServicesTest.store_speed_and_supply

    def all_failed(self):
        """SPEED and SUPPLY stored, BATTERY, CLUTCH and AC failed in the running cycle."""
        self.store_speed_and_supply()
        self.app.cycle(BATTERY=FAILED, CLUTCH=FAILED, AC=FAILED)
        failed = self.statuses()
        self.assertNotIn(INITIAL, failed.values(), failed)
        return failed

    def test_the_generated_rows(self):
        self.assertEqual(self.rows, [(0xFFFFD0, SPEED, SPEED), (0x080000, 0x080000, 0x0FFFFF),
                                     (0x300000, 0x300000, 0x3FFFFF)])

    def test_a_manufacturer_group_clears_its_range_only(self):
        before = self.all_failed()
        self.assertTrue(self.client.clear_dtc(0x080000).positive)
        self.assertEqual(self.statuses(), {**before, CLUTCH: INITIAL, BATTERY: INITIAL})

    def test_a_functional_group_clears_its_dtc_and_the_stored_entry(self):
        before = self.all_failed()
        self.assertEqual(self.send(bytes([0x14, 0xFF, 0xFF, 0xD0])), bytes([0x54]))
        self.assertEqual(self.statuses(), {**before, SPEED: INITIAL})
        self.assertEqual(self.app.dll.app_dem_stored(SPEED), 0)
        self.assertEqual(self.app.dll.app_dem_stored(SUPPLY), 1)

    def test_a_group_without_a_dtc_of_this_server_is_positive(self):
        before = self.all_failed()
        self.assertEqual(self.send(bytes([0x14, 0x30, 0x00, 0x00])), bytes([0x54]))
        self.assertEqual(self.statuses(), before)

    def test_a_group_the_description_does_not_name(self):
        """The NVM-04 group 0x0A0000 is not in this description: NRC 0x31 physically, nothing cleared, and
        functionally no response (the server does not belong to the group)."""
        before = self.all_failed()
        self.assertEqual(self.send(bytes([0x14, 0x0A, 0x00, 0x00])), bytes([0x7F, 0x14, 0x31]))
        self.assertEqual(self.request_raw(self.func, bytes([0x14, 0x0A, 0x00, 0x00])), [])
        self.assertEqual(self.statuses(), before)

    def test_a_single_dtc_inside_a_group_stays_a_single_dtc(self):
        before = self.all_failed()
        self.assertTrue(self.client.clear_dtc(CLUTCH).positive)
        self.assertEqual(self.statuses(), {**before, CLUTCH: INITIAL})


if __name__ == "__main__":
    unittest.main()
