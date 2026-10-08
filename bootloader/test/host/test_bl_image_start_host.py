"""BL-36/BL-38: the start decision of the bootloader (safety review 2026-10-02, SYS69-S10 a and SYS69-S11).

SYS69-S10 (code inspection INS-BLI-1, BLI-F01): app_image_valid() accepted a record without compatibility block
(APP_COMPAT_ABSENT, written before BL-02). The block is not covered by recordCrc, so one corrupted word compatMagic
turned an image of another target or memory layout into ABSENT and skipped the target/layout part of SM-51. Since
BL-36 only APP_COMPAT_OK starts; DID 0xF1A0 still reports 0x10 for such a record (diagnosis unchanged).

SYS69-S11 (BLI-F06): the start decision was one RAM word (appValid) computed in bl_init() and read again at the
jump, possibly minutes later. Since BL-36 bl_poll() checks again right before hal_jump_to_app(); if it fails, the
bootloader stays and remains reachable.

BL-38 (board 2026-10-02, bootloader 0.3.7): the second full CRC over the image cost ~160 ms per start after the
startup window (safety wdg 1328/1348 ms instead of 839/889, three starts). Since 0.3.8 app_image_valid() runs once
per start (bl_init(), inside the window as up to 0.3.6) and hands its computed CRC on; app_image_confirm() before the
jump re-reads record, compatibility block and entry and requires the record's size and CRC field to be the ones the
image CRC was computed for and matched. The bootloader issues no flash command between the two (erase and download
need the programming session, which latches the stay); a change of the image body alone without any flash command
of the bootloader is left to the flash ECC and the application's ROM check SM-14 (counter-check below, documented).
The count of CRC runs per start is measured on the host HAL (host_image_crc_runs()).

Covered without hardware, on the simulated HAL of bl_host.c, three memory layouts (eval board, other layout with
8-KB sectors and no SBC, S32K116 with 2-KB sectors):
- a record without compatibility block (24-byte record of milestone 2) is not started; DID 0xF1A0 reports 0x10
- a record of another target or layout with a corrupted compatMagic word is not started
- a record whose compatibility block is erased is not started
- a valid image still starts after the window (no change for correct images)
- record CRC, compatibility block, the record alone (another CRC field) or record and image together (another
  valid image) changed in the startup window: no jump, bootloader stays reachable over UDS (DID 0xF1A0 answers),
  and no later jump either
- image erased over UDS in the window (programming session): no jump, also after the session has ended
- an image whose CRC is wrong at power-on is not started; exactly one CRC run over the image per start
- fault injection (copy with appValid forced true, the corrupted RAM word of SYS69-S11): still no start of an image
  with a wrong CRC or a refused record
Mutants (copy of bootloader/, host DLL built from it): legacy acceptance back, re-check before the jump removed,
re-check result ignored, CRC field not compared at the jump, full CRC again at the jump (0.3.7), CRC of the start
check taken from the record - each must turn the scenarios red. An equivalent one is left out: without the
CRC at the start the confirmation still refuses a wrong image (it compares the computed CRC).
Source rule: in bl_main.c the jump is reached only through app_image_confirm() in the same branch.

Run: .venv/Scripts/python -m unittest bootloader/test/host/test_bl_image_start_host.py
(needs ziglang in the .venv, like tools/canflash/hostbuild.py)
"""

import ctypes
import re
import shutil
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
BOOTLOADER = REPO / "bootloader"

sys.path.insert(0, str(REPO / "tools" / "canflash"))
import canflash  # noqa: E402
import hostbuild  # noqa: E402
import test_bootloader_host as tbh  # noqa: E402  (host DLL build and helpers of bootloader-host)

try:
    import ziglang  # noqa: F401

    HAVE_ZIG = True
except ImportError:  # pragma: no cover - the test skips without the host compiler
    HAVE_ZIG = False

CFG = canflash.CFG
APP_COMPAT_ABSENT = 0x10

MAIN_C = "bootloader/src/core/bl_main.c"
IMAGE_C = "bootloader/src/core/app_image.c"

# built-in faults; each must turn at least one scenario red
MUTANTS = {
    "legacy record accepted again (before BL-36)": [
        (IMAGE_C, "    if (app_record_compat(record) != APP_COMPAT_OK)",
         "    app_compat_result_t const compat = app_record_compat(record);\n"
         "    if ((compat != APP_COMPAT_OK) && (compat != APP_COMPAT_ABSENT))"),
    ],
    "no image check before the jump (before BL-36)": [
        (MAIN_C, "        if (!app_image_confirm(&checkedImage))", "        if (false)"),
    ],
    "CRC field of the record not compared at the jump (BL-38)": [
        (IMAGE_C, " && (record.crc == checked->crc);", ";"),
    ],
    "full CRC over the image again at the jump (0.3.7)": [
        (MAIN_C, "        if (!app_image_confirm(&checkedImage))", "        if (!app_image_valid(&checkedImage))"),
    ],
    # without the CRC at the start alone the confirmation still refuses (it compares the computed CRC): equivalent;
    # the CRC taken from the record instead of computed over the flash is the fault that would start any image
    "CRC of the start check taken from the record instead of the flash": [
        (IMAGE_C, "    checked->crc  = crc32_update(0U, hal_flash_ptr(record.start), record.size);",
         "    checked->crc  = record.crc;"),
    ],
    "result of the check before the jump ignored": [
        (MAIN_C, "            stayInBootloader = true;\n        }\n        else\n        {",
         "            stayInBootloader = true;\n        }\n        {"),
    ],
}

# Fault injection, not a fault of the code: the start decision of bl_init() is corrupted in RAM (SYS69-S11). Every
# scenario must stay safe - the check before the jump does not trust appValid.
APP_VALID_CORRUPTED = [
    (MAIN_C, "    appValid               = app_image_valid(&checkedImage);",
     "    (void)app_image_valid(&checkedImage);\n    appValid = true;"),
]


def free(lib):
    """Windows keeps a loaded DLL locked: without this the temporary directory cannot be removed."""
    if sys.platform == "win32":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.FreeLibrary.argtypes = [ctypes.c_void_p]
        kernel32.FreeLibrary(ctypes.c_void_p(lib._handle))


def legacy_record(data: bytes) -> bytes:
    """Record of milestone 2: 24 bytes, compatibility and fingerprint block erased."""
    return canflash.app_record(data)


def foreign_record_with_corrupted_magic(data: bytes) -> bytes:
    """Record of an image for another target whose compatMagic word is corrupted (BLI-F01)."""
    record = bytearray(tbh.record_with(data, target=canflash.image_compat()["target"] ^ 0x5A5A5A5A))
    record[24:28] = struct.pack("<I", canflash.COMPAT_MAGIC ^ 0x00000100)
    return bytes(record)


def foreign_layout_with_corrupted_magic(data: bytes) -> bytes:
    record = bytearray(tbh.record_with(data, layout=canflash.layout_id() ^ 0xA5A5A5A5))
    record[24:28] = b"\xff\xff\xff\xff"
    return bytes(record)


def erased_compat_block(data: bytes) -> bytes:
    return canflash.app_record(data) + b"\xff" * 32


def wrong_image_crc(data: bytes) -> bytes:
    """Complete, consistent record (record CRC, compatibility block) whose CRC field does not match the image."""
    head = struct.pack("<IIII", canflash.RECORD_MAGIC, CFG["BL_APP_START"], len(data), zlib.crc32(data) ^ 0x00010000)
    return head + struct.pack("<II", zlib.crc32(head), 0xFFFFFFFF) + canflash.compat_block()


class Scenarios:
    """The scenarios on one host bootloader; True = the expected (safe) behaviour."""

    def __init__(self, bl: tbh.HostBootloader):
        self.bl = bl

    def reset(self):
        self.bl.dll.host_erase_all()
        self.bl.dll.host_set_costs(20, 1)
        self.bl.dll.host_power_loss(ctypes.c_uint8(0))
        self.bl.dll.host_provision_test_key()

    def install(self, data: bytes, record: bytes | None = None):
        self.bl.write(CFG["BL_APP_START"], data)
        self.bl.write(CFG["BL_APP_RECORD_ADDR"], canflash.app_record_full(data) if record is None else record)

    def run_ms(self, ms: int):
        for _ in range(ms):
            with self.bl.lock:
                self.bl.dll.host_poll(ctypes.c_uint32(1))

    def power_on(self):
        with self.bl.lock:
            self.bl.dll.host_power_on(0)

    def jumped(self) -> bool:
        return self.bl.dll.host_jump_address() == CFG["BL_APP_START"]

    def starts(self, record_of) -> bool:
        self.reset()
        data = tbh.make_app_image(3000)
        self.install(data, record_of(data) if record_of is not None else None)
        self.power_on()
        self.run_ms(CFG["BL_STARTUP_WINDOW_MS"] + 10)
        return self.jumped()

    def crc_runs_of_a_start(self) -> int:
        """CRC-32 runs over the image from power-on to the jump of a valid image (BL-38: 1, 0.3.7: 2)."""
        self.reset()
        self.install(tbh.make_app_image(3000))
        self.power_on()
        self.run_ms(CFG["BL_STARTUP_WINDOW_MS"] + 10)
        return self.bl.dll.host_image_crc_runs() if self.jumped() else -1

    def starts_after_change_in_window(self, change) -> bool:
        """Valid image at bl_init(), changed 50 ms later (inside the startup window)."""
        self.reset()
        data = tbh.make_app_image(3000)
        self.install(data)
        self.power_on()
        self.run_ms(50)
        assert not self.jumped()
        change(self, data)
        self.run_ms(CFG["BL_STARTUP_WINDOW_MS"] + 1000)
        return self.jumped()

    @staticmethod
    def flip_image_byte(s, data):
        s.bl.write(CFG["BL_APP_START"] + 1000, bytes([data[1000] ^ 0x01]))

    @staticmethod
    def flip_record_crc(s, data):
        address = CFG["BL_APP_RECORD_ADDR"] + 12            # crc field of the record
        s.bl.write(address, bytes([s.bl.read(address, 1)[0] ^ 0x80]))

    @staticmethod
    def erase_compat_block(s, data):
        s.bl.write(CFG["BL_APP_RECORD_ADDR"] + 24, b"\xff" * 32)

    @staticmethod
    def replace_image_and_record(s, data):
        """Another valid image with its own complete record - consistent in itself, but not the image checked."""
        other = tbh.make_app_image(3000, seed=7)
        s.bl.write(CFG["BL_APP_START"], other)
        s.bl.write(CFG["BL_APP_RECORD_ADDR"], canflash.app_record_full(other))

    @staticmethod
    def replace_record_only(s, data):
        """A complete, consistent record with another CRC field over the unchanged image."""
        s.bl.write(CFG["BL_APP_RECORD_ADDR"], wrong_image_crc(data))

    def results(self) -> dict[str, bool]:
        return {
            "valid image starts": self.starts(None),
            "legacy record not started": not self.starts(legacy_record),
            "foreign target, corrupted magic not started": not self.starts(foreign_record_with_corrupted_magic),
            "foreign layout, erased magic not started": not self.starts(foreign_layout_with_corrupted_magic),
            "erased compatibility block not started": not self.starts(erased_compat_block),
            "wrong image CRC at power-on not started": not self.starts(wrong_image_crc),
            "record CRC changed in the window: no jump": not self.starts_after_change_in_window(self.flip_record_crc),
            "compat block erased in the window: no jump":
                not self.starts_after_change_in_window(self.erase_compat_block),
            "record with another CRC field in the window: no jump":
                not self.starts_after_change_in_window(self.replace_record_only),
            "image and record replaced in the window: no jump":
                not self.starts_after_change_in_window(self.replace_image_and_record),
            "one CRC run over the image per start (BL-38)": self.crc_runs_of_a_start() == 1,
        }


@unittest.skipUnless(HAVE_ZIG, "ziglang not installed (host compiler)")
class ImageStartTest(unittest.TestCase):
    """Eval board memory layout (projects/evalboard_s32k144_demo, else the full test profile)."""

    PROJECT, MCUDB, LAYOUT = tbh.EVAL_PROJECT, None, None

    setUpClass = classmethod(tbh.BootloaderTest.setUpClass.__func__)
    tearDownClass = classmethod(tbh.BootloaderTest.tearDownClass.__func__)
    setUp = tbh.BootloaderTest.setUp
    power_on = tbh.BootloaderTest.power_on
    run_ms = tbh.BootloaderTest.run_ms
    client = tbh.BootloaderTest.client
    fingerprint = tbh.BootloaderTest.fingerprint
    programming_client = tbh.BootloaderTest.programming_client
    install_app = tbh.BootloaderTest.install_app
    jumps_after_startup_window = tbh.BootloaderTest.jumps_after_startup_window
    target_compat = tbh.BootloaderTest.target_compat

    def test_scenarios(self):
        for name, ok in Scenarios(self.bl).results().items():
            with self.subTest(name):
                self.assertTrue(ok)

    def test_legacy_record_is_reported_but_not_started(self):
        data = tbh.make_app_image(3000)
        self.install_app(data, legacy_record(data))
        self.assertFalse(self.jumps_after_startup_window())
        self.assertEqual(self.target_compat()["record"], APP_COMPAT_ABSENT)

    def test_corrupted_magic_of_a_foreign_record_reads_as_absent_and_is_not_started(self):
        data = tbh.make_app_image(3000)
        self.install_app(data, foreign_record_with_corrupted_magic(data))
        self.assertFalse(self.jumps_after_startup_window())
        self.assertEqual(self.target_compat()["record"], APP_COMPAT_ABSENT)

    def test_bootloader_stays_reachable_after_a_failed_check_before_the_jump(self):
        data = tbh.make_app_image(3000)
        self.install_app(data)
        self.power_on()
        self.run_ms(50)
        Scenarios.replace_record_only(Scenarios(self.bl), data)
        self.run_ms(CFG["BL_STARTUP_WINDOW_MS"] + 100)
        self.assertEqual(self.bl.dll.host_jump_address(), 0)
        target = canflash.read_target_compat(self.client())
        self.assertIsNotNone(target, "DID 0xF1A0 must answer: the bootloader stays reachable")
        self.assertEqual(target["record"], 0x00, "record and compatibility block valid, only the CRC field changed")
        self.run_ms(2000)
        self.assertEqual(self.bl.dll.host_jump_address(), 0, "no later jump without a new check at the next start")

    def test_restored_record_starts_at_the_next_start(self):
        """The failed check is not latched in flash: after a reset the image is checked again from scratch."""
        data = tbh.make_app_image(3000)
        self.install_app(data)
        self.power_on()
        self.run_ms(50)
        Scenarios.replace_record_only(Scenarios(self.bl), data)
        self.run_ms(CFG["BL_STARTUP_WINDOW_MS"] + 10)
        self.assertEqual(self.bl.dll.host_jump_address(), 0)
        self.bl.write(CFG["BL_APP_RECORD_ADDR"], canflash.app_record_full(data))
        self.assertTrue(self.jumps_after_startup_window())

    def test_image_erased_over_uds_in_the_window_is_not_started(self):
        """The only flash write path of the bootloader: programming session, which latches the stay (BL-38)."""
        data = tbh.make_app_image(3000)
        self.install_app(data)
        self.power_on()
        client = self.programming_client()
        client.routine_control(canflash.RID_ERASE_MEMORY, 1,
                               bytes([0x44]) + struct.pack(">II", CFG["BL_APP_START"], len(data)))
        self.assertNotEqual(self.bl.erased(), [])
        client.change_session(1)
        self.run_ms(CFG["BL_STARTUP_WINDOW_MS"] + CFG["BL_S3_SERVER_MS"] + 100)
        self.assertEqual(self.bl.dll.host_jump_address(), 0)

    def test_extended_session_writes_no_flash(self):
        """Counter-part of the write-path argument: the extended session (the one that can hold the jump for
        minutes) refuses erase and download, so nothing changes between the check and the jump."""
        tbh.BootloaderTest.test_extended_session_grants_no_programming_rights(self)

    def test_one_crc_over_the_image_per_start(self):
        """BL-38: the restart time of 0.3.7 grew by one CRC per start; the host HAL counts the CRC runs."""
        scenarios = Scenarios(self.bl)
        self.assertEqual(scenarios.crc_runs_of_a_start(), 1)
        # also after the extended session held the jump (the case of SYS69-S11): still one CRC per start
        scenarios.reset()
        scenarios.install(tbh.make_app_image(3000))
        scenarios.power_on()
        client = self.client()
        client.change_session(3)
        self.run_ms(CFG["BL_STARTUP_WINDOW_MS"] + CFG["BL_S3_SERVER_MS"] + 100)
        self.assertTrue(scenarios.jumped())
        self.assertEqual(self.bl.dll.host_image_crc_runs(), 1)

    def test_image_changed_without_flash_command_is_left_to_the_application(self):
        """Documented limit (BL-38, safety review): an image byte changed between bl_init() and the jump without
        any flash command of the bootloader (no such path exists; a bit error is the flash ECC's) is not looked at
        again before the jump - the application's ROM check SM-14 at its start sees it. Up to 0.3.7 the second CRC
        caught it at the price of ~160 ms per start."""
        scenarios = Scenarios(self.bl)
        data = tbh.make_app_image(3000)
        self.assertTrue(scenarios.starts_after_change_in_window(lambda s, d: Scenarios.flip_image_byte(s, data)))


@unittest.skipUnless(HAVE_ZIG, "ziglang not installed (host compiler)")
class ImageStartOtherLayoutTest(ImageStartTest):
    """testproject_bootloader: 8-KB sectors at 0x08000000, no SBC, other CAN bus and ids."""

    PROJECT, MCUDB, LAYOUT = tbh.OTHER_PROJECT, tbh.TEST_MCUDB, None


@unittest.skipUnless(HAVE_ZIG, "ziglang not installed (host compiler)")
class ImageStartS32K116Test(ImageStartTest):
    """Smallest derivative of the focus family, 2-KB sectors (synthetic layout s32k116_2k)."""

    PROJECT, MCUDB, LAYOUT = None, None, "s32k116_2k"


def setUpModule():
    tbh.setUpModule()


def tearDownModule():
    tbh.tearDownModule()


@unittest.skipUnless(HAVE_ZIG, "ziglang not installed (host compiler)")
class MutantTest(unittest.TestCase):
    """Each mutant of app_image.c / bl_main.c, built as host DLL (eval board layout), fails a scenario."""

    @classmethod
    def setUpClass(cls):
        cls.layout = tbh.selected()
        cls._tmp = tempfile.TemporaryDirectory(prefix="bl36_")
        cls.tmp = Path(cls._tmp.name)
        cls.gen = hostbuild.generate_layout(tbh.EVAL_PROJECT, cls.tmp / "gen")
        canflash.use_layout(canflash.load_build(cls.gen / "bootloader" / "bl_memory.h"))

    @classmethod
    def tearDownClass(cls):
        canflash.use_layout(cls.layout)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def build(self, name: str, edits) -> tbh.HostBootloader:
        root = self.tmp / re.sub(r"\W+", "_", name)
        for sub in ("include", "src/core", "platforms", "test/host"):
            shutil.copytree(BOOTLOADER / sub, root / "bootloader" / sub, ignore=shutil.ignore_patterns("__pycache__"))
        for file, old, new in edits:
            path = root / file
            text = path.read_text(encoding="utf-8")
            self.assertEqual(text.count(old), 1, f"mutant '{name}' does not apply any more: {old!r}")
            path.write_text(text.replace(old, new), encoding="utf-8")
        dll = hostbuild.build_bl_host(root / "bl_host.dll", self.gen, root, werror=False)
        return tbh.HostBootloader(dll)

    def test_unmodified_copy_passes(self):
        bl = self.build("unmodified", [])
        try:
            results = Scenarios(bl).results()
        finally:
            free(bl.dll)
        self.assertTrue(all(results.values()), results)

    def test_corrupted_start_decision_starts_nothing_unchecked(self):
        """SYS69-S11: appValid forced true after bl_init() (a corrupted RAM word). The check before the jump still
        refuses every image the start check refused - it compares the CRC computed at the start with the record."""
        bl = self.build("appValid corrupted", APP_VALID_CORRUPTED)
        try:
            results = Scenarios(bl).results()
        finally:
            free(bl.dll)
        self.assertTrue(all(results.values()), results)

    def test_mutants_are_caught(self):
        for name, edits in MUTANTS.items():
            with self.subTest(mutant=name):
                bl = self.build(name, edits)
                try:
                    results = Scenarios(bl).results()
                finally:
                    free(bl.dll)
                failed = [k for k, ok in results.items() if not ok]
                print(f"\n  {name}: {failed}", end="")
                self.assertNotEqual(failed, [], f"mutant '{name}' not detected")
                self.assertTrue(results["valid image starts"], f"mutant '{name}' broke the good case instead")


def code(path: Path) -> str:
    """Source without C comments."""
    return re.sub(r"/\*.*?\*/|//[^\n]*", "", path.read_text(encoding="utf-8"), flags=re.S)


class SourceRuleTest(unittest.TestCase):
    def test_jump_only_behind_a_fresh_image_check(self):
        core = code(REPO / MAIN_C)
        self.assertEqual(len(re.findall(r"\bhal_jump_to_app\s*\(", core)), 1)
        # BL-38: the full check (CRC over the image) once per start in bl_init(), the confirmation at the jump
        self.assertEqual(len(re.findall(r"\bapp_image_valid\s*\(", core)), 1, "once, in bl_init()")
        self.assertEqual(len(re.findall(r"\bapp_image_confirm\s*\(", core)), 1, "once, at the jump")
        init = re.search(r"void bl_init\(void\)\s*\{(.*?)\n\}", core, re.S).group(1)
        self.assertIn("app_image_valid(&checkedImage)", init)
        poll = re.search(r"void bl_poll\(void\)\s*\{(.*)\n\}", core, re.S).group(1)
        check = poll.index("if (!app_image_confirm(&checkedImage))")
        jump = poll.index("hal_jump_to_app(")
        self.assertLess(check, jump)
        between = poll[check:jump]
        self.assertIn("else", between, "the jump is the else branch of the fresh check")
        # between check and jump only the hand-over of the devices (BL_DEVICE_SERVICE) - no flash access
        self.assertEqual(re.findall(r"\b(\w+)\s*\(", between.split("else", 1)[1]),
                         ["defined", "bl_device_service", "hal_millis"])

    def test_only_a_checked_compatibility_block_starts(self):
        image = code(REPO / IMAGE_C)
        body = re.search(r"static bool record_startable\(app_record_t\* record\)\s*\{(.*?)\n\}", image, re.S).group(1)
        self.assertIn("app_record_compat(record) != APP_COMPAT_OK", body)
        self.assertNotIn("APP_COMPAT_ABSENT", body)
        # both checks of a start go through it
        for name in ("app_image_valid", "app_image_confirm"):
            fn = re.search(rf"bool {name}\([^)]*\)\s*\{{(.*?)\n\}}", image, re.S).group(1)
            self.assertIn("record_startable(&record)", fn, name)

    def test_one_crc_over_the_image_and_the_confirmation_compares_its_result(self):
        image = code(REPO / IMAGE_C)
        crc_over_image = re.findall(r"crc32_update\(0U, hal_flash_ptr\(record\.start\), record\.size\)", image)
        self.assertEqual(len(crc_over_image), 1)
        confirm = re.search(r"bool app_image_confirm\([^)]*\)\s*\{(.*?)\n\}", image, re.S).group(1)
        self.assertNotIn("crc32_update", confirm, "no CRC over the image at the jump (BL-38)")
        self.assertIn("record.crc == checked->crc", confirm)
        self.assertIn("record.size == checked->size", confirm)

    def test_flash_is_written_only_by_jobs_of_the_programming_session(self):
        """The write-path argument of BL-38: the core writes flash only in uds.c, from the jobs that the
        programming session starts; bl_main.c latches the stay as soon as that session is active."""
        core = REPO / "bootloader" / "src" / "core"
        writers = {f.name for f in core.glob("*.c")
                   if re.search(r"\bhal_flash_(erase_sector|program)\s*\(", code(f))}
        self.assertEqual(writers, {"uds.c"})
        main = code(REPO / MAIN_C)
        self.assertRegex(main, r"if \(uds\.session == UDS_SESSION_PROGRAMMING\)\s*\{\s*stayInBootloader = true;")


if __name__ == "__main__":
    unittest.main()
