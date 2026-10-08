"""`canflash info` against the simulated ECU: application UDS stack (ecu/uds) and bootloader core as DLLs (TOOL-45).

Board finding 2026-09-21: with the application running, `info` showed nextbsw-bl 0.2.0 as "security access
XOR/rotl13 (bootloader before BL-03)" and "no software version (older than BL-04)". The application reports DID
0xF180 from the bootloader's flash but none of the bootloader's DIDs. Here the real application stack answers, so
the test sees what the board sees: who answers, no age notes for a running application, and `info --bootloader`
switching into the real bootloader, reading there and resetting back.

The simulated ECU (EcuBus, HostApp) comes from ecu/uds/test/host/test_app_uds_host.py; the DLLs as there
(APP_UDS_HOST_DLL, BL_HOST_DLL).

    .venv/Scripts/python -m unittest tools/canflash/test_info_host.py
"""

import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "ecu" / "uds" / "test" / "host"))
import canflash  # noqa: E402
import test_app_uds_host as app_host  # noqa: E402

AGE_NOTES = ("before BL-0", "older than BL-0", "XOR/rotl13")


class InfoAgainstTheSimulatedEcuTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app_host.ensure_dlls()
        cls.app = app_host.HostApp(app_host.APP_DLL)
        cls.bl = app_host.HostBootloader(app_host.BL_DLL)

    def setUp(self):
        self.app.power_on()
        self.bl.dll.host_erase_all()
        self.bl.dll.host_set_costs(20, 1)
        self.bus = app_host.EcuBus(self.app, self.bl)
        self.addCleanup(self.bus.shutdown)

    def tearDown(self):
        self.assertEqual(self.app.dll.app_assert_count(), 0, "ETL assert in the stack")

    def info(self, *argv) -> tuple[int, str, str]:
        args = canflash.build_parser().parse_args(["info", *argv])
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(canflash, "open_bus", return_value=contextlib.nullcontext(self.bus)), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = canflash.cmd_info(args)
        return code, out.getvalue(), err.getvalue()

    def test_running_application_is_named_without_age_notes(self):
        code, out, err = self.info()
        self.assertEqual(code, 0, err)
        self.assertTrue(out.startswith("application active: "), out)
        self.assertIn(f"bootloader in flash (reported by the application): {app_host.BL_VERSION}", out)
        self.assertIn("readable only in the bootloader", out)
        for note in AGE_NOTES:
            self.assertNotIn(note, out)
        self.assertFalse(self.bus.in_bootloader, "info without --bootloader must not leave the application")
        services = {data[1] for direction, _, data in self.bus.log if direction == "rx" and data[0] < 0x10}
        self.assertEqual(services, {0x22}, "read-only: nothing but ReadDataByIdentifier")

    def test_bootloader_switch_reads_the_real_bootloader_and_resets(self):
        code, out, err = self.info("--bootloader")
        self.assertEqual(code, 0, err + out)
        self.assertIn("application active: ", out)
        self.assertIn(f"bootloader active: {app_host.BL_VERSION}", out)
        self.assertIn(f"software version {canflash.BL_VERSION_NUMBER} (DID 0xF189)", out)
        self.assertIn(f"hardware number {canflash.TEXT[canflash.HARDWARE_KEY]} (DID 0xF191)", out)
        self.assertIn("security access hmac-sha256", out)
        self.assertIn("record format version", out)
        for note in AGE_NOTES:
            self.assertNotIn(note, out)
        self.assertIn("ECU reset", out)
        self.assertTrue(self.bus.in_bootloader)
        self.assertEqual(self.bl.dll.host_reset_requested(), 1, "the bootloader was reset back at the end")


if __name__ == "__main__":
    unittest.main(verbosity=2)
