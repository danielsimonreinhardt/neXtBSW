"""COM-43: CommunicationControl (28) and ControlDTCSetting (85) of the application in the development and the
product build (decision 2026-09-29, TOOL-131).

During development the extended session suffices; the product build (NX_PRODUCT_BUILD, SYS-74) serves both only
after security access. The stack of ecu/uds runs twice as DLL from the same own profile product_profile/diag.yaml
(one security level of the extended session, one DTC of a BSW monitor, real fault memory libs/bsw/dem): once as the
development build compiles it, once as the product build does (tools/diaggen --product, -DNX_PRODUCT_BUILD=1). The
diagnostic addresses are those of projects/buildtest_s32k148_min, not the eval board's.

Checked: NRC 0x33 without security access in the product build (the ECU switches nothing), both services after the
unlock, the lock again after a change of the session, the order session (0x7F) before security (0x33), the
development build unchanged; 85 off lets no report of a BSW monitor into the fault memory - not even after 85 on -,
and the session end and a new start switch DTC updates on again.

    .venv/Scripts/python ecu/uds/test/host/test_app_uds_product.py
"""

import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools" / "canflash"))
sys.path.insert(0, str(ROOT / "tools" / "test"))
import build_app_uds_host  # noqa: E402
import canflash  # noqa: E402
import hostbuild  # noqa: E402
from test_app_uds_dtc import FaultMemoryApp  # noqa: E402
from test_app_uds_host import OTHER_PROJECT, host_key  # noqa: E402
from test_bootloader_host import selected  # noqa: E402

PROFILE = HERE / "product_profile" / "diag.yaml"
PRODUCT_DLL = Path(os.environ.get("APP_UDS_HOST_DLL_PRODUCT", ROOT / "build" / "host" / "app_uds_host_product.dll"))
DEVELOPMENT_DLL = Path(os.environ.get("APP_UDS_HOST_DLL_PRODUCT_DEV",
                                      ROOT / "build" / "host" / "app_uds_host_product_dev.dll"))
MONITOR = 0x123456          # the DTC of the profile, reported by the test (BSW monitor)
PASSED, FAILED = 0, 1       # dem::Result
SECURITY_DENIED = 0x33


def profile_dll(out: Path, product: bool) -> Path:
    """The DLL of the profile for one build, rebuilt when a source changed (checksum, COM-09)."""
    sources = list(HERE.glob("*.cpp")) + list((ROOT / "ecu" / "uds").rglob("*.[ch]*"))
    sources += list((ROOT / "libs" / "bsw" / "dem" / "include").rglob("*.h"))
    sources += [PROFILE, OTHER_PROJECT, HERE / "build_app_uds_host.py", ROOT / "tools" / "rtegen" / "rtegen.py"]
    sources += list(OTHER_PROJECT.parent.glob("*.yaml")) + list((ROOT / "tools" / "diaggen").glob("*.py"))
    sources += build_app_uds_host.C_SOURCES + [ROOT / "bootloader/include/bl_config.h"]
    digest = hostbuild.checksum(sorted(set(sources)), extra=(str(OTHER_PROJECT), str(PROFILE), "dem",
                                                            "product" if product else "development"))
    hostbuild.build_if_changed(out, digest, lambda: build_app_uds_host.build(
        out, OTHER_PROJECT, diag=PROFILE, dem=True, product=product))
    return out


class PreprogrammingBase(unittest.TestCase):
    PRODUCT = False

    @classmethod
    def setUpClass(cls):
        cls.saved = selected()
        canflash.use_layout(canflash.target_from_project(OTHER_PROJECT))
        cls.phys, cls.func = canflash.CFG["BL_CAN_ID_REQUEST_PHYS"], canflash.CFG["BL_CAN_ID_REQUEST_FUNC"]
        cls.resp, cls.pad = canflash.CFG["BL_CAN_ID_RESPONSE"], canflash.CFG["BL_ISOTP_PADDING"]
        cls.app = FaultMemoryApp(profile_dll(PRODUCT_DLL if cls.PRODUCT else DEVELOPMENT_DLL, cls.PRODUCT))
        assert cls.app.dll.app_dem_present() == 1

    @classmethod
    def tearDownClass(cls):
        canflash.use_layout(cls.saved)

    def setUp(self):
        self.app.power_on(canflash.bl_version())

    def tearDown(self):
        self.assertEqual(self.app.dll.app_assert_count(), 0, "ETL assert in the stack")

    def payloads(self, payload: bytes, functional: bool = False) -> list[bytes]:
        """Single frame request, the UDS payloads of the responses within 60 ms."""
        frame = (bytes([len(payload)]) + payload).ljust(8, bytes([self.pad]))
        self.assertTrue(self.app.rx(self.func if functional else self.phys, frame))
        out = []
        for _ in range(60):
            self.app.poll_ms(1)
            while (f := self.app.tx()) is not None:
                self.assertEqual(f[0], self.resp)
                out.append(f[1][1:1 + f[1][0]])
        return out

    def one(self, payload: bytes, functional: bool = False) -> bytes:
        answers = self.payloads(payload, functional)
        self.assertEqual(len(answers), 1, answers)
        return answers[0]

    def state(self) -> tuple[int, int, int]:
        """What 28 and 85 asked the ECU to switch: application messages tx, rx, DTC updates."""
        return (self.app.dll.app_messages_tx(), self.app.dll.app_messages_rx(), self.app.dll.app_dtc_updates())

    def extended(self):
        self.assertEqual(self.one(bytes([0x10, 0x03]))[:2], bytes([0x50, 0x03]))

    def unlock(self):
        seed = self.one(bytes([0x27, 0x01]))
        self.assertEqual(seed[:2], bytes([0x67, 0x01]))
        key = host_key(seed[2:], 0x01, 4)
        self.assertEqual(self.one(bytes([0x27, 0x02]) + key), bytes([0x67, 0x02]))

    def quiesce(self):
        """Preprogramming step (16.3.1.1 steps (5) and (7)): 85 off, then 28 disableRxAndTx."""
        self.assertEqual(self.one(bytes([0x85, 0x02])), bytes([0xC5, 0x02]))
        self.assertEqual(self.one(bytes([0x28, 0x03, 0x01])), bytes([0x68, 0x03]))
        self.assertEqual(self.state(), (0, 0, 0))


class ProductBuildTest(PreprogrammingBase):
    PRODUCT = True

    def test_generated_for_the_product_build(self):
        header = (PRODUCT_DLL.parent / f"{PRODUCT_DLL.stem}_diag" / "diag" / "DiagPreprogramming.h").read_text(
            encoding="utf-8")
        self.assertIn("#define NX_DIAG_PRODUCT_BUILD 1", header)
        self.assertIn("DIAG_PREPROGRAMMING_SECURITY = 0x01U; // Application", header)

    def test_security_access_is_needed(self):
        """Extended session alone: NRC 0x33 for both, nothing switched."""
        self.extended()
        self.assertEqual(self.one(bytes([0x85, 0x02])), bytes([0x7F, 0x85, SECURITY_DENIED]))
        self.assertEqual(self.one(bytes([0x28, 0x03, 0x01])), bytes([0x7F, 0x28, SECURITY_DENIED]))
        self.assertEqual(self.one(bytes([0x85, 0x01])), bytes([0x7F, 0x85, SECURITY_DENIED]))
        self.assertEqual(self.state(), (1, 1, 1), "a refused request must not switch anything")
        # the suppressPosRspMsgIndicationBit does not suppress a negative response (functional too)
        self.assertEqual(self.one(bytes([0x85, 0x82]), functional=True), bytes([0x7F, 0x85, SECURITY_DENIED]))
        self.assertEqual(self.state(), (1, 1, 1))

    def test_after_the_unlock_both_are_served(self):
        self.extended()
        self.unlock()
        self.quiesce()
        # and the handover to the bootloader still works from there
        self.assertEqual(self.one(bytes([0x10, 0x02]))[:2], bytes([0x50, 0x02]))
        self.assertEqual(self.app.dll.app_boot_request(), 1)

    def test_a_session_change_locks_again(self):
        """9.2: the level is locked by every change of the session - and the session end restores both states."""
        self.extended()
        self.unlock()
        self.quiesce()
        self.assertEqual(self.one(bytes([0x10, 0x01]))[:2], bytes([0x50, 0x01]))
        self.assertEqual(self.state(), (1, 1, 1))
        self.extended()
        self.assertEqual(self.one(bytes([0x85, 0x02])), bytes([0x7F, 0x85, SECURITY_DENIED]))
        self.assertEqual(self.one(bytes([0x28, 0x03, 0x01])), bytes([0x7F, 0x28, SECURITY_DENIED]))
        self.assertEqual(self.state(), (1, 1, 1))

    def test_session_before_security(self):
        """The general order of the checks: a service not served in the default session is 0x7F, not 0x33."""
        self.assertEqual(self.one(bytes([0x85, 0x02])), bytes([0x7F, 0x85, 0x7F]))
        self.assertEqual(self.one(bytes([0x28, 0x03, 0x01])), bytes([0x7F, 0x28, 0x7F]))

    def test_sub_function_and_length_after_the_unlock(self):
        self.extended()
        self.unlock()
        self.assertEqual(self.one(bytes([0x85, 0x03])), bytes([0x7F, 0x85, 0x12]))
        self.assertEqual(self.one(bytes([0x28, 0x03])), bytes([0x7F, 0x28, 0x13]))
        self.assertEqual(self.state(), (1, 1, 1))

    def test_no_dtc_from_the_preprogramming_step(self):
        """85 off: what a BSW monitor reports then is not taken over, also not after 85 on; a report after 85 on is."""
        self.extended()
        self.unlock()
        self.quiesce()
        self.app.report(MONITOR, FAILED)
        self.app.main(3)
        self.assertEqual(self.app.status(MONITOR) & 0x09, 0)
        self.assertEqual(self.one(bytes([0x85, 0x01])), bytes([0xC5, 0x01]))
        self.app.main(3)
        self.assertEqual(self.one(bytes([0x19, 0x02, 0x09])), bytes([0x59, 0x02, 0x7F]))
        self.app.report(MONITOR, FAILED)
        self.app.main(1)
        self.assertEqual(self.one(bytes([0x19, 0x02, 0x09])), bytes([0x59, 0x02, 0x7F, 0x12, 0x34, 0x56, 0x2F]))

    def test_a_new_start_updates_dtcs_again(self):
        """A reset in the middle of the preprogramming step (the tester walks away): the next start takes reports."""
        self.extended()
        self.unlock()
        self.quiesce()
        self.app.power_on(canflash.bl_version())
        self.assertEqual(self.state(), (1, 1, 1))
        self.app.report(MONITOR, FAILED)
        self.app.main(1)
        self.assertEqual(self.app.status(MONITOR) & 0x09, 0x09)


class DevelopmentBuildTest(PreprogrammingBase):
    PRODUCT = False

    def test_generated_for_the_development_build(self):
        header = (DEVELOPMENT_DLL.parent / f"{DEVELOPMENT_DLL.stem}_diag" / "diag" / "DiagPreprogramming.h").read_text(
            encoding="utf-8")
        self.assertIn("#define NX_DIAG_PRODUCT_BUILD 0", header)
        self.assertIn("DIAG_PREPROGRAMMING_SECURITY = 0x00U;", header)

    def test_extended_session_suffices(self):
        self.extended()
        self.quiesce()
        self.assertEqual(self.one(bytes([0x10, 0x01]))[:2], bytes([0x50, 0x01]))
        self.assertEqual(self.state(), (1, 1, 1))

    def test_no_dtc_from_the_preprogramming_step(self):
        self.extended()
        self.quiesce()
        self.app.report(MONITOR, FAILED)
        self.app.main(2)
        # the session end switches 85 on again, the report of the off time stays out
        self.assertEqual(self.one(bytes([0x10, 0x01]))[:2], bytes([0x50, 0x01]))
        self.assertEqual(self.app.dll.app_dtc_updates(), 1)
        self.app.main(2)
        self.assertEqual(self.app.status(MONITOR) & 0x09, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
