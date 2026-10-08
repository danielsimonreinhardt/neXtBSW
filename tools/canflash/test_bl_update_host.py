"""Host tests of the bootloader updater (bootloader/updater): flash model with power loss at every flash
operation, image checks in C and in canflash.py, status frames and confirmation of `canflash blupdate`.

The DLL is built with Zig/Clang on every run (UPD_HOST_DLL: use a prebuilt one, UPD_SOURCE: other source tree).
"""

import ctypes
import io
import os
import random
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zlib
from pathlib import Path

import can

sys.path.insert(0, str(Path(__file__).resolve().parent))
import canflash  # noqa: E402
import hostbuild  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "test"))
import build_dirs  # noqa: E402
# The updater is family s32k1xx code (flash configuration field): tested with the eval board memory layout (test data)
# PLAT-62: the eval board demo where it exists (reference of the prebuilt bootloader DLL), else the own full test
# profile with the same memory layout - the eval board is private (PLAT-46 F10) and missing in the exported open tree
PRIVATE_EVAL_PROJECT = ROOT / "projects" / "evalboard_s32k144_demo" / "project.yaml"
EVAL_PROJECT = (PRIVATE_EVAL_PROJECT if PRIVATE_EVAL_PROJECT.is_file()
                else ROOT / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full" / "project.yaml")
CFG = canflash.CFG
canflash.use_layout(canflash.target_from_project(EVAL_PROJECT))
SECTOR = CFG["BL_SECTOR_SIZE"]
PHRASE = CFG["BL_PHRASE_SIZE"]
AREA = CFG["BL_APP_START"]
APP = CFG["BL_APP_START"]
RECORD = CFG["BL_APP_RECORD_ADDR"]
CUT_NONE = 0xFFFFFFFF

OK, ALREADY = 0x00, 0x01
ERR_HEADER, ERR_SIZE, ERR_CRC, ERR_FCF, ERR_VECTORS, ERR_COMPAT, ERR_STUB, ERR_ENV = range(0x10, 0x18)
ERR_FLASH_STUB, ERR_FLASH_BODY, ERR_FLASH_FINAL = 0x20, 0x21, 0x22
STEP_START, STEP_CHECKED, STEP_STUB, STEP_BODY, STEP_FINAL, STEP_RECORD, STEP_DONE, STEP_ERROR = (
    1, 2, 3, 4, 5, 6, 7, 0x0E)


def build_dll(out: Path, source: Path, werror: bool = True, test_layout: str | None = None) -> None:
    bl = source / "bootloader"
    gen = Path(out).parent / f"{Path(out).stem}_gen"
    # PLAT-16: test_layout builds the updater core for a synthetic s32k1xx layout (other sector size)
    layout = hostbuild.write_test_layout(test_layout, gen) if test_layout \
        else hostbuild.generate_layout(EVAL_PROJECT, gen)
    # family part of the updater (BL-08): upd_family.h and upd_family_check_image.inc of the layout's family
    platform, _ = hostbuild.family_sources(source, hostbuild.layout_family(layout))
    cmd = [sys.executable, "-m", "ziglang", "cc", "-target", "x86_64-windows-gnu", "-shared", "-O1", "-std=c99",
           "-Wall", "-Wextra", *(["-Werror"] if werror else []), "-Wconversion", "-Wno-sign-conversion",
           f"-I{bl / 'include'}", f"-I{bl / 'src' / 'core'}", f"-I{layout}", f"-I{bl / 'updater' / 'include'}",
           f"-I{bl / 'updater' / 'src' / 'core'}", f"-I{platform}",
           str(bl / "src" / "core" / "crc32.c"), str(bl / "updater" / "src" / "core" / "bl_update.c"),
           str(bl / "updater" / "test" / "host" / "upd_host.c"), "-o", str(out)]
    subprocess.run(cmd, check=True)


class Stats(ctypes.Structure):
    _fields_ = [("start", ctypes.c_uint8), ("retries", ctypes.c_uint8), ("stubUs", ctypes.c_uint32),
                ("finalUs", ctypes.c_uint32), ("securedUs", ctypes.c_uint32)]


_DLL = None


def load_dll() -> ctypes.CDLL:
    global _DLL
    if _DLL is not None:
        return _DLL
    if os.environ.get("UPD_HOST_DLL"):
        path = Path(os.environ["UPD_HOST_DLL"])
    else:
        path = Path(tempfile.gettempdir()) / f"upd_host_{os.getpid()}.dll"
        build_dll(path, Path(os.environ.get("UPD_SOURCE", ROOT)))
    dll = ctypes.CDLL(str(path))
    dll.host_flash.restype = ctypes.POINTER(ctypes.c_uint8)
    for name in ("host_ops", "host_op", "host_guard_violations", "host_reports", "host_report", "host_corrupt_count"):
        getattr(dll, name).restype = ctypes.c_uint32
    _DLL = dll
    return dll


def pad(data: bytes, size: int = AREA) -> bytes:
    return data + b"\xff" * (size - len(data))


def make_bl_image(size: int, seed: int, version: bytes = b"nextbsw-bl 0.2.0") -> bytearray:
    rnd = random.Random(seed)
    size -= size % PHRASE
    data = bytearray(rnd.getrandbits(8) for _ in range(size))
    struct.pack_into("<IIII", data, 0, 0x20000000, 0x2089, 0x1835, 0x1835)
    data[0x400:0x410] = canflash.FCF_REFERENCE
    data[0x1F00 : 0x1F00 + len(version) + 1] = version + b"\0"
    return data


def blob_of(image: bytes) -> bytes:
    return canflash.bootloader_blob(bytes(image))


def make_raw_blob(image: bytes, size: int | None = None, crc: int | None = None, magic: int = canflash.BLOB_MAGIC) -> bytes:
    head = struct.pack("<III", magic, len(image) if size is None else size, zlib.crc32(image) if crc is None else crc)
    return head + struct.pack("<I", zlib.crc32(head)) + bytes(image)


class UpdaterModel(unittest.TestCase):
    OLD = make_bl_image(20000, seed=1, version=b"S32K14x-BL 0.1.0")
    NEW = make_bl_image(12000, seed=2)
    UPDATER = bytes(bytearray(struct.pack("<IIII", 0x20000000, APP + 0x1355, APP + 0x1001, APP + 0x1001))
                    + bytes(random.Random(3).getrandbits(8) for _ in range(0x3000 - 16)))

    @classmethod
    def setUpClass(cls):
        cls.dll = load_dll()

    def setUp(self):
        self.guard_before = self.dll.host_guard_violations()
        self.dll.host_corrupt(CUT_NONE, 0)
        self.install(self.OLD)

    def tearDown(self):
        self.assertEqual(self.dll.host_guard_violations(), self.guard_before, "flash access outside the allowed areas")

    # --- flash model helpers
    def flash(self):
        return (ctypes.c_uint8 * 0x80000).from_address(ctypes.addressof(self.dll.host_flash().contents))

    def write(self, address, data):
        ctypes.memmove(ctypes.addressof(self.flash()) + address, bytes(data), len(data))

    def read(self, address, size):
        return ctypes.string_at(ctypes.addressof(self.flash()) + address, size)

    def install(self, bootloader: bytes):
        self.dll.host_erase_all()
        self.write(0, bootloader)
        self.write(APP, self.UPDATER)
        self.write(RECORD, canflash.app_record(self.UPDATER))

    @property
    def stub(self) -> bytes:
        return pad(self.UPDATER[:0x400] + canflash.FCF_REFERENCE, SECTOR)

    def record_valid(self) -> bool:
        return self.read(RECORD, 24) == canflash.app_record(self.UPDATER) and self.read(APP, len(self.UPDATER)) == self.UPDATER

    def run_updater(self, blob=None, env=True, force=False, cut=CUT_NONE, during=False, seed=1):
        blob = blob_of(self.NEW) if blob is None else blob
        stats = Stats()
        self.dll.host_arm(ctypes.c_uint32(cut), 1 if during else 0, ctypes.c_uint32(seed))
        result = self.dll.host_run(blob, len(blob), 1 if env else 0, 1 if force else 0, ctypes.byref(stats))
        return result, stats

    def ops(self):
        kind = ctypes.c_uint8()
        result = []
        for i in range(self.dll.host_ops()):
            address = self.dll.host_op(i, ctypes.byref(kind))
            result.append((chr(kind.value), address))
        return result

    def reports(self):
        return [((r >> 8) & 0xFF, r & 0xFF) for r in (self.dll.host_report(i) for i in range(self.dll.host_reports()))]

    def boot(self):
        """What the MCU starts after a reset: 'old'/'new' bootloader, 'updater' (stub) or 'brick'."""
        area = self.read(0, AREA)
        fsec = area[0x40C]
        self.assertNotEqual((fsec >> 4) & 3, 2, "mass erase disabled")
        secured = fsec & 3 != 2
        if area == pad(bytes(self.NEW)):
            return "new", secured
        if area == pad(bytes(self.OLD)):
            return "old", secured
        if area[:8] == self.stub[:8]:
            return "updater", secured
        return "brick", secured

    def resets_until_stable(self, blob=None):
        """Resets and lets bootloader/stub start the updater until nothing starts it any more."""
        for _ in range(6):
            kind, secured = self.boot()
            if kind == "brick" or (kind in ("old", "new") and not self.record_valid()):
                return kind, secured
            if kind == "updater":
                self.assertFalse(secured, "updater started with an erased FCF")
            self.run_updater(blob)
        self.fail("updater restarted endlessly")

    @staticmethod
    def brick_expected(ops, cut, during):
        """Unbootable iff sector 0 was erased (fully or partially) and its reset vector not programmed since."""
        done = ops[:cut] if cut != CUT_NONE else ops
        partial = ops[cut] if cut != CUT_NONE and during and cut < len(ops) else None
        erased = programmed = False
        for op in done:
            if op == ("E", 0):
                erased, programmed = True, False
            elif op == ("P", 0) and erased:
                programmed = True
        if partial == ("E", 0):
            return True
        if partial == ("P", 0) and erased:
            return True
        return erased and not programmed

    @staticmethod
    def secured_expected(ops, cut, during):
        """FCF erased: sector 0 erased and phrase 0x408 (FSEC/FOPT) not programmed since.
        None: undetermined (power loss while erasing sector 0 or programming FSEC leaves FSEC 0xFE or 0xFF)."""
        done = ops[:cut]
        partial = ops[cut] if during and cut < len(ops) else None
        erased = programmed = False
        for op in done:
            if op == ("E", 0):
                erased, programmed = True, False
            elif op == ("P", 0x408) and erased:
                programmed = True
        if partial in (("E", 0), ("P", 0x408)):
            return None
        return erased and not programmed


class UpdateFlowTest(UpdaterModel):
    def test_update_replaces_bootloader_and_invalidates_record(self):
        result, stats = self.run_updater()
        self.assertEqual(result, OK)
        self.assertEqual(self.read(0, AREA), pad(bytes(self.NEW)))
        self.assertEqual(self.read(RECORD, SECTOR), b"\xff" * SECTOR)
        self.assertEqual(self.read(APP, len(self.UPDATER)), self.UPDATER)
        self.assertEqual(self.reports(), [(STEP_START, 0), (STEP_CHECKED, 0), (STEP_STUB, 0)]
                         + [(STEP_BODY, s) for s in range(1, 8)] + [(STEP_FINAL, 0), (STEP_RECORD, 0), (STEP_DONE, OK)])
        ops = self.ops()
        erases = [a for k, a in ops if k == "E"]
        self.assertEqual(erases, [0] + [s * SECTOR for s in range(1, 8)] + [0, RECORD])
        self.assertEqual((stats.start, stats.retries), (0, 0))
        self.assertEqual(self.boot(), ("new", False))

    def test_write_order_in_sector_0(self):
        self.run_updater()
        ops = self.ops()
        e0, e1 = [i for i, op in enumerate(ops) if op == ("E", 0)]
        stub = [a for k, a in ops[e0 + 1 : ops.index(("E", SECTOR))]]
        final = [a for k, a in ops[e1 + 1 : ops.index(("E", RECORD))]]
        # stub: FCF (0x400 is erased already), reset vector, rest
        self.assertEqual(stub[:2], [0x408, 0x000])
        # new bootloader: FCF, rest ascending, reset vector last
        self.assertEqual(final[0], 0x408)
        self.assertEqual(final[-1], 0x000)
        self.assertEqual(final[1:-1], sorted(final[1:-1]))
        stats = self.run_updater(force=True)[1]
        self.assertEqual(stats.stubUs, 20000 + 2 * 100)
        self.assertEqual(stats.securedUs, 20000 + 100)

    def test_bootloader_larger_than_before_and_real_image(self):
        # BL_HEX, else the demo build as tools/test finds it (NX_BUILD_DIR, tools/test/build_dirs.py)
        hex_path = Path(os.environ.get("BL_HEX") or build_dirs.bootloader_hex(ROOT))
        if not hex_path.exists():
            self.skipTest(f"{hex_path} not built")
        image = canflash.load_bootloader_image(hex_path)
        self.assertTrue(canflash.check_bootloader_image(image).startswith(canflash.bl_version().split(".")[0] + "."))
        self.assertEqual(self.dll.host_check(blob_of(image), len(blob_of(image))), OK)
        self.NEW = image
        result, _ = self.run_updater(blob_of(image))
        self.assertEqual(result, OK)
        self.assertEqual(self.read(0, AREA), pad(image))

    def test_already_installed_writes_nothing_but_the_record(self):
        self.install(self.NEW)
        result, stats = self.run_updater()
        self.assertEqual((result, stats.start), (ALREADY, 2))
        self.assertEqual(self.ops(), [("E", RECORD)])
        self.assertEqual(self.boot(), ("new", False))
        self.assertFalse(self.record_valid())

    def test_same_sector_0_different_body_still_uses_stub(self):
        # e.g. only code behind sector 0 changed: writing sectors 1..7 under the old sector 0 would be unsafe
        old = bytearray(self.NEW)
        old[0x2100] ^= 0xFF
        self.OLD = old
        self.install(old)
        result, stats = self.run_updater()
        self.assertEqual((result, stats.start), (OK, 0))
        self.assertEqual([op for op in self.ops() if op[0] == "E"][:2], [("E", 0), ("E", SECTOR)])
        self.assertEqual(self.read(0, AREA), pad(bytes(self.NEW)))

    def test_force_rewrites_identical_bootloader(self):
        self.install(self.NEW)
        result, _ = self.run_updater(force=True)
        self.assertEqual(result, OK)
        self.assertGreater(len(self.ops()), 1000)
        self.assertEqual(self.read(0, AREA), pad(bytes(self.NEW)))

    def test_environment_not_ok_writes_nothing(self):
        result, _ = self.run_updater(env=False)
        self.assertEqual(result, ERR_ENV)
        self.assertEqual(self.ops(), [("E", RECORD)])
        self.assertEqual(self.boot(), ("old", False))
        self.assertFalse(self.record_valid())

    def test_implausible_updater_vectors_write_nothing(self):
        self.UPDATER = struct.pack("<II", 0x20000000, 0x9354) + self.UPDATER[8:]  # reset vector without Thumb bit
        self.install(self.OLD)
        result, _ = self.run_updater()
        self.assertEqual(result, ERR_STUB)
        self.assertEqual(self.ops(), [("E", RECORD)])

    def test_verify_error_is_retried(self):
        self.dll.host_corrupt(0x1008, 1)
        result, stats = self.run_updater()
        self.assertEqual((result, stats.retries, self.dll.host_corrupt_count()), (OK, 1, 1))
        self.assertEqual(self.read(0, AREA), pad(bytes(self.NEW)))

    def test_persistent_body_error_keeps_stub_and_resumes(self):
        self.dll.host_corrupt(0x1008, 0)
        result, stats = self.run_updater()
        self.assertEqual((result, stats.retries), (ERR_FLASH_BODY, 2))
        self.assertEqual(self.boot(), ("updater", False))
        self.assertTrue(self.record_valid())
        self.dll.host_corrupt(CUT_NONE, 0)
        result, stats = self.run_updater()
        self.assertEqual((result, stats.start), (OK, 1))
        self.assertEqual(self.boot(), ("new", False))

    def test_persistent_final_error_keeps_record(self):
        self.dll.host_corrupt(0x0800, 0)
        result, _ = self.run_updater()
        self.assertEqual(result, ERR_FLASH_FINAL)
        self.assertTrue(self.record_valid())

    def test_interrupted_stub_is_completed_without_new_erase(self):
        self.run_updater()
        ops = self.ops()
        p0 = ops.index(("P", 0))
        self.install(self.OLD)
        self.run_updater(cut=p0 + 5)
        self.assertEqual(self.boot(), ("updater", False))
        result, stats = self.run_updater()
        self.assertEqual((result, stats.start), (OK, 1))
        self.assertEqual([op for op in self.ops() if op == ("E", 0)], [("E", 0)], "stub sector erased again")


class PowerLossTest(UpdaterModel):
    def check_cut(self, clean_ops, cut, during):
        self.install(self.OLD)
        self.run_updater(cut=cut, during=during, seed=cut + 7)
        kind, secured = self.resets_until_stable()
        self.assertEqual(kind == "brick", self.brick_expected(clean_ops, cut, during), f"cut {cut} during={during}: {kind}")
        if kind == "brick":
            expected = self.secured_expected(clean_ops, cut, during)
            if expected is not None:
                self.assertEqual(secured, expected, f"cut {cut} during={during}")
        else:
            self.assertEqual(kind, "new")
            self.assertEqual(self.read(0, AREA), pad(bytes(self.NEW)))
        return kind, secured

    def test_power_loss_before_and_during_every_flash_operation(self):
        self.run_updater()
        clean = self.ops()
        e0, e1 = [i for i, op in enumerate(clean) if op == ("E", 0)]
        p0 = clean.index(("P", 0))
        p1 = len(clean) - 1 - clean[::-1].index(("P", 0))
        bricks = {False: [], True: []}
        secured = {False: [], True: []}
        for during in (False, True):
            for cut in range(len(clean) + 1):
                kind, sec = self.check_cut(clean, cut, during)
                if kind == "brick":
                    bricks[during].append(cut)
                    if sec:
                        secured[during].append(cut)
        # exact windows: sector 0 erased until its reset vector is programmed
        self.assertEqual(bricks[False], list(range(e0 + 1, p0 + 1)) + list(range(e1 + 1, p1 + 1)))
        self.assertEqual(bricks[True], list(range(e0, p0 + 1)) + list(range(e1, p1 + 1)))
        self.assertEqual(p0 - e0, 2, "stub window: erase, FCF phrase, reset vector")
        print(f"\n  {len(clean)} flash operations; unbootable after cut before op: stub {p0 - e0}, "
              f"final sector 0 {p1 - e1} (of which FCF erased: {len(secured[False])} in total)")

    def test_random_double_power_loss(self):
        self.run_updater()
        total = len(self.ops())
        rnd = random.Random(42)
        outcomes = {"new": 0, "brick": 0}
        for n in range(150):
            self.install(self.OLD)
            first = rnd.randrange(total)
            self.run_updater(cut=first, during=rnd.random() < 0.5, seed=n)
            kind, _ = self.boot()
            if kind == "brick" or (kind == "new" and not self.record_valid()):
                outcomes[kind] += 1
                continue
            second, during = rnd.randrange(total), rnd.random() < 0.5
            self.run_updater(cut=second, during=during, seed=n + 1000)
            ops = self.ops()
            kind, _ = self.resets_until_stable()
            self.assertEqual(kind == "brick", self.brick_expected(ops, second, during), f"{first}/{second}: {kind}")
            if kind != "brick":
                self.assertEqual(kind, "new")
            outcomes[kind] += 1
        self.assertGreater(outcomes["new"], 50)
        self.assertGreater(outcomes["brick"], 20)


class ImageCheckTest(UpdaterModel):
    def assert_rejected(self, image: bytes, code: int, blob: bytes | None = None, python=True):
        blob = blob_of(image) if blob is None else blob
        self.install(self.OLD)
        if python:
            with self.assertRaises(ValueError):
                canflash.check_bootloader_image(bytes(image))
        self.assertEqual(self.dll.host_check(blob, len(blob)), code)
        result, _ = self.run_updater(blob)
        self.assertEqual(result, code)
        self.assertEqual(self.ops(), [("E", RECORD)], "no write into the bootloader area")
        self.assertEqual(self.boot(), ("old", False))
        self.assertFalse(self.record_valid())

    def modified(self, offset: int, value: bytes) -> bytearray:
        image = bytearray(self.NEW)
        image[offset : offset + len(value)] = value
        return image

    def test_valid_image_accepted(self):
        self.assertEqual(canflash.check_bootloader_image(bytes(self.NEW)), "nextbsw-bl 0.2.0")
        blob = blob_of(self.NEW)
        self.assertEqual(self.dll.host_check(blob, len(blob)), OK)

    def test_fcf_must_not_secure_or_lock(self):
        for fsec in (0xFF, 0xFC, 0xFD, 0xEE, 0xDE, 0xBE, 0x7E, 0x00):  # secured, mass erase off, backdoor on
            with self.subTest(fsec=hex(fsec)):
                self.assert_rejected(self.modified(0x40C, bytes([fsec])), ERR_FCF)
        with self.assertRaisesRegex(ValueError, "disables mass erase"):
            canflash.check_bootloader_image(bytes(self.modified(0x40C, b"\xee")))
        with self.assertRaisesRegex(ValueError, "secures the chip"):
            canflash.check_bootloader_image(bytes(self.modified(0x40C, b"\xff")))

    def test_fcf_other_fields(self):
        for offset, value in ((0x40D, b"\xff"), (0x408, b"\x00"), (0x40B, b"\xfe"), (0x400, b"\x12"), (0x40E, b"\x00")):
            with self.subTest(offset=hex(offset)):
                self.assert_rejected(self.modified(offset, value), ERR_FCF)

    def test_size_limits(self):
        big = make_bl_image(AREA + PHRASE, seed=5)
        self.assert_rejected(big, ERR_SIZE)
        self.assert_rejected(bytes(self.NEW[:0x408]), ERR_SIZE)
        self.assert_rejected(self.NEW, ERR_SIZE, make_raw_blob(self.NEW, size=len(self.NEW) + 8), python=False)

    def test_header_and_crc(self):
        self.assert_rejected(self.NEW, ERR_HEADER, make_raw_blob(self.NEW, magic=0x50554C43), python=False)
        blob = bytearray(blob_of(self.NEW))
        blob[13] ^= 1
        self.assert_rejected(self.NEW, ERR_HEADER, bytes(blob), python=False)
        self.assert_rejected(self.NEW, ERR_CRC, make_raw_blob(self.NEW, crc=zlib.crc32(self.NEW) ^ 1), python=False)
        self.assert_rejected(self.NEW, ERR_HEADER, b"BLUP", python=False)

    def test_vectors(self):
        cases = {
            "sp outside ram": struct.pack("<I", 0x00010000),
            "sp at ram start": struct.pack("<I", CFG["BL_RAM_START"]),
            "reset without thumb bit": struct.pack("<II", 0x20000000, 0x2088),
            "reset in vector table": struct.pack("<II", 0x20000000, 0x0101),
            "reset behind image": struct.pack("<II", 0x20000000, len(self.NEW) + 1),
            "hardfault erased": struct.pack("<IIII", 0x20000000, 0x2089, 0x1835, 0xFFFFFFFF),
        }
        for name, value in cases.items():
            with self.subTest(name):
                self.assert_rejected(self.modified(0, value), ERR_VECTORS)

    def test_version_compatibility(self):
        self.assert_rejected(self.modified(0x1F00, b"nextbsw-bl 1.0.0"), ERR_COMPAT)
        self.assert_rejected(self.modified(0x1F00, b"XXXXXXXXXXXXXXXX"), ERR_COMPAT)
        accepted = self.modified(0x1F00, b"nextbsw-bl 0.9.9")
        self.assertEqual(self.dll.host_check(blob_of(accepted), len(blob_of(accepted))), OK)

    def test_identification_of_the_project_and_the_former_name(self):
        """PLAT-08 transition rule: C updater and canflash accept an image with the project's identification
        (nextbsw-bl) and one with the former name (S32K14x-BL, e.g. the bootloader flashed on the eval board, to
        install it again), and reject any other prefix - same length, one character off, or a longer one."""
        # the number is BL_VERSION_NUMBER of bl_config.h (BL-10: no copy of it in the tests)
        self.assertEqual(canflash.bl_version(), f"nextbsw-bl {canflash.BL_VERSION_NUMBER}")
        for version in (b"nextbsw-bl 0.1.0", b"S32K14x-BL 0.1.0", b"S32K14x-BL 0.7.3"):
            with self.subTest(accepted=version):
                image = make_bl_image(12000, seed=2, version=version)
                self.assertEqual(canflash.check_bootloader_image(bytes(image)), version.decode())
                self.assertEqual(self.dll.host_check(blob_of(image), len(blob_of(image))), OK)
        for version in (b"ACME-BL 0.1.0\0\0\0", b"nextbsw-bx 0.1.0", b"S32K14x-B 0.1.0\0", b"S32K14x-BL 1.0.0",
                        b"Xnextbsw-bl0.1.0", b"nextbsw-bl-2 0.1",
                        # PLAT-51: compared case-sensitively - the PLAT-08 spelling and case variants are foreign
                        b"NeXtBSW-BL 0.1.0", b"NEXTBSW-BL 0.1.0", b"s32k14x-bl 0.1.0", b"S32K14X-BL 0.1.0"):
            with self.subTest(rejected=version):
                self.assert_rejected(self.modified(0x1F00, version), ERR_COMPAT)

    def test_prefix_at_the_end_of_the_image_is_not_read_past(self):
        """The former prefix may be longer or shorter than the project's; the search stops at the image end."""
        image = self.modified(len(self.NEW) - 12, b"S32K14x-BL 0")     # "<major>." cut off by the image end
        image[0x1F00:0x1F10] = b"XXXXXXXXXXXXXXXX"
        self.assert_rejected(image, ERR_COMPAT)
        image[len(image) - 13 : len(image)] = b"S32K14x-BL 0."
        self.assertEqual(self.dll.host_check(blob_of(image), len(blob_of(image))), OK)

    def test_resumed_updater_with_bad_image_touches_nothing(self):
        self.run_updater()
        p0 = self.ops().index(("P", 0))
        self.install(self.OLD)
        self.run_updater(cut=p0 + 1)
        bad = blob_of(self.modified(0x40C, b"\xff"))
        result, stats = self.run_updater(bad)
        self.assertEqual((result, stats.start), (ERR_FCF, 1))
        self.assertEqual(self.ops(), [])

    def test_hex_loader_and_blob_extraction(self):
        from intelhex import IntelHex

        with tempfile.TemporaryDirectory() as tmp:
            ih = IntelHex()
            ih.frombytes(bytes(self.NEW), offset=0)
            ih.write_hex_file(f"{tmp}/bl.hex")
            self.assertEqual(canflash.load_bootloader_image(Path(f"{tmp}/bl.hex")), bytes(self.NEW))
            ih.frombytes(b"\x00" * 8, offset=AREA)
            ih.write_hex_file(f"{tmp}/bad.hex")
            with self.assertRaises(ValueError):
                canflash.load_bootloader_image(Path(f"{tmp}/bad.hex"))
        updater = self.UPDATER[:0x500] + struct.pack("<I", canflash.BLOB_MAGIC) + blob_of(self.NEW) + b"\xff" * 4
        self.assertEqual(canflash.extract_bootloader_image(updater), bytes(self.NEW))
        with self.assertRaises(ValueError):
            canflash.extract_bootloader_image(self.UPDATER)


class UpdaterSectorTableTest(unittest.TestCase):
    """PLAT-16: the updater walks the bootloader area with the sector table instead of BL_SECTOR_SIZE.

    Built for the synthetic S32K116 layout (bootloader/test/layouts/s32k116_2k.yaml): 128 KB flash with **2-KB**
    sectors, bootloader area 0x2000, record in the last sector 0x1F800. The same code that writes eight 4-KB
    sectors on the eval board must write four 2-KB sectors here - with a fixed sector size it would have erased
    0x0, 0x1000 only (and read past the buffer)."""

    FLASH_SIZE = 0x20000
    AREA = 0x2000                       # bootloader area = BL_APP_START of this layout
    APP = 0x2000
    RECORD = 0x1F800
    RECORD_SIZE = 0x800
    SECTORS = [0x0000, 0x0800, 0x1000, 0x1800]

    @classmethod
    def setUpClass(cls):
        path = Path(tempfile.gettempdir()) / f"upd_host_2k_{os.getpid()}.dll"
        build_dll(path, Path(os.environ.get("UPD_SOURCE", ROOT)), test_layout="s32k116_2k")
        cls.dll = ctypes.CDLL(str(path))
        cls.dll.host_flash.restype = ctypes.POINTER(ctypes.c_uint8)
        for name in ("host_ops", "host_op", "host_guard_violations", "host_reports", "host_report"):
            getattr(cls.dll, name).restype = ctypes.c_uint32
        cls.image = cls.make_image(0x1800, seed=21)
        cls.updater = cls.make_updater()

    @staticmethod
    def make_image(size: int, seed: int, version: bytes = b"nextbsw-bl 0.2.0") -> bytes:
        """Bootloader image for a 0x2000 area: vectors inside the image, FCF at 0x400, version string behind it."""
        rnd = random.Random(seed)
        data = bytearray(rnd.getrandbits(8) for _ in range(size - size % PHRASE))
        struct.pack_into("<IIII", data, 0, 0x20000000, 0x0A89, 0x0835, 0x0835)
        data[0x400:0x410] = canflash.FCF_REFERENCE
        data[0x1000 : 0x1000 + len(version) + 1] = version + b"\0"
        return bytes(data)

    @classmethod
    def make_updater(cls) -> bytes:
        """The running updater at APP: its first 0x400 bytes become the stub's vector table."""
        head = struct.pack("<IIII", 0x20000000, cls.APP + 0x555, cls.APP + 0x501, cls.APP + 0x501)
        return head + bytes(random.Random(22).getrandbits(8) for _ in range(0x1000 - len(head)))

    def setUp(self):
        self.dll.host_erase_all()
        self.dll.host_corrupt(ctypes.c_uint32(CUT_NONE), 0)
        self.write(0, self.image + b"\xff" * (self.AREA - len(self.image)))
        self.write(self.APP, self.updater)
        self.write(self.RECORD, b"\x5a" * 24)      # any record: the updater only has to erase its sector
        self.guard_before = self.dll.host_guard_violations()

    def tearDown(self):
        self.assertEqual(self.dll.host_guard_violations(), self.guard_before,
                         "flash access outside the bootloader area and the record sector")

    def flash(self):
        return (ctypes.c_uint8 * self.FLASH_SIZE).from_address(ctypes.addressof(self.dll.host_flash().contents))

    def write(self, address, data):
        ctypes.memmove(ctypes.addressof(self.flash()) + address, bytes(data), len(data))

    def read(self, address, size) -> bytes:
        return ctypes.string_at(ctypes.addressof(self.flash()) + address, size)

    def run_updater(self, blob: bytes):
        stats = Stats()
        self.dll.host_arm(ctypes.c_uint32(CUT_NONE), 0, ctypes.c_uint32(1))
        result = self.dll.host_run(blob, len(blob), 1, 1, ctypes.byref(stats))
        kind = ctypes.c_uint8()
        ops = []
        for i in range(self.dll.host_ops()):
            address = self.dll.host_op(i, ctypes.byref(kind))
            ops.append((chr(kind.value), address))
        return result, ops

    def test_update_erases_every_2kb_sector_of_the_area(self):
        new = self.make_image(0x1400, seed=23)
        result, ops = self.run_updater(make_raw_blob(new))
        self.assertEqual(result, OK)
        self.assertEqual([a for k, a in ops if k == "E"],
                         [*self.SECTORS, 0x0000, self.RECORD],
                         "stub, every further sector of the area, sector 0 again, then the record sector")
        self.assertEqual(self.read(0, self.AREA), new + b"\xff" * (self.AREA - len(new)))
        self.assertEqual(self.read(self.RECORD, self.RECORD_SIZE), b"\xff" * self.RECORD_SIZE)
        # programmed phrases only in the sectors that hold image bytes (an erased phrase needs no programming)
        programmed = sorted({a - (a % 0x800) for k, a in ops if k == "P"})
        self.assertEqual(programmed, [s for s in self.SECTORS if s < len(new)])

    def test_reports_one_body_step_per_sector(self):
        new = self.make_image(0x1400, seed=24)
        self.run_updater(make_raw_blob(new))
        reports = [(r >> 8) & 0xFF for r in (self.dll.host_report(i) for i in range(self.dll.host_reports()))]
        details = [r & 0xFF for r in (self.dll.host_report(i) for i in range(self.dll.host_reports()))
                   if ((r >> 8) & 0xFF) == STEP_BODY]
        self.assertEqual(details, [1, 2, 3], "one body report per sector behind sector 0")
        self.assertIn(STEP_DONE, reports)


class BlupdateToolTest(unittest.TestCase):
    def test_confirmation_requires_update(self):
        self.assertTrue(canflash.confirm_update("nextbsw-bl 0.2.0", ask=lambda prompt: "UPDATE", out=lambda *a: None))
        for answer in ("", "y", "yes", "update"):
            self.assertFalse(canflash.confirm_update("nextbsw-bl 0.2.0", ask=lambda prompt, a=answer: a, out=lambda *a: None))

    def test_watch_updater_status_frames(self):
        with can.Bus(interface="virtual", channel="upd", receive_own_messages=False) as rx, \
                can.Bus(interface="virtual", channel="upd") as tx:
            frames = [bytes([STEP_START, 0, 0, 0, 0, 0, 0, 0]), bytes([STEP_STUB, 0, 0x00, 0xCA, 0, 0, 0x00, 0xC9]),
                      bytes([STEP_DONE, OK, 0x00, 0xCA, 0x14, 0x46, 0x00, 0xC9])]

            def sender():
                tx.send(can.Message(arbitration_id=0x7E8, data=b"\x06\x50\x02\x00\x32\x01\xf4\xcc", is_extended_id=False))
                for f in frames:
                    time.sleep(0.02)
                    tx.send(can.Message(arbitration_id=CFG["UPD_STATUS_CAN_ID"], data=f, is_extended_id=False))

            thread = threading.Thread(target=sender)
            thread.start()
            lines = []
            status = canflash.watch_updater(rx, 2.0, log=lines.append)
            thread.join()
        self.assertEqual(status, (OK, (20.2, 519.0, 20.1)))
        self.assertEqual(len(lines), 3)


def strip_c_comments(text: str) -> str:
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", text, flags=re.S))


class FamilyRuleTest(unittest.TestCase):
    """BL-08: bootloader core, updater core and canflash know no image start of their own - the MCU family decides
    (bootloader/platforms/<family>, canflash IMAGE_RULES), chosen from the generated layout (BL_FAMILY, written by
    blgen since BL-10) or the project."""

    def setUp(self):
        saved = dict(canflash.CFG), dict(canflash.TEXT), dict(canflash.SOURCE)

        def restore():
            for current, old in zip((canflash.CFG, canflash.TEXT, canflash.SOURCE), saved):
                current.clear()
                current.update(old)

        self.addCleanup(restore)
        self.tmp = Path(tempfile.mkdtemp(prefix="bl08_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.eval_layout = canflash.load_build(hostbuild.generate_layout(EVAL_PROJECT, self.tmp / "eval")
                                               / "bootloader" / "bl_memory.h")

    def build_tree(self, family: str | None) -> Path:
        """<build>/generated/board/bootloader/bl_memory.h of the eval board as blgen writes it (BL-10), with BL_FAMILY
        set to `family`, or without BL_FAMILY (a layout generated before BL-10). The generated/nxconfig/project.cmake
        of the build names s32k1xx in both cases: canflash must not fall back to it any more."""
        build = self.tmp / f"build_{family}"
        target = build / canflash.LAYOUT_IN_BUILD
        target.parent.mkdir(parents=True)
        source = Path(self.eval_layout[canflash.LAYOUT_FILE_KEY])
        text = source.read_text(encoding="utf-8")
        self.assertIn('#define BL_FAMILY "s32k1xx"', text, "blgen writes the family (BL-10)")
        replacement = f'#define BL_FAMILY "{family}"\n' if family else ""
        target.write_text(re.sub(r'#define BL_FAMILY "\w+"\n', replacement, text), encoding="utf-8")
        shutil.copy(source.with_name(canflash.DIAG_HEADER_NAME), target.with_name(canflash.DIAG_HEADER_NAME))
        settings = build / "generated" / "nxconfig" / "project.cmake"
        settings.parent.mkdir(parents=True)
        settings.write_text('set(NX_PROJECT_NAME "x")\nset(NX_FAMILY "s32k1xx")\n', encoding="utf-8")
        return target

    def test_family_of_the_project(self):
        values = canflash.target_from_project(EVAL_PROJECT)
        self.assertEqual(values[canflash.FAMILY_KEY], "s32k1xx")
        canflash.use_layout(values)
        self.assertEqual(canflash.image_rule().family, "s32k1xx")
        self.assertEqual(canflash.image_rule().header_size, 0x410)

    def test_family_of_the_generated_header(self):
        """blgen writes BL_FAMILY (BL-10), the synthetic host layouts the same text; load_build() takes it along."""
        self.assertEqual(self.eval_layout[canflash.FAMILY_KEY], "s32k1xx")
        for name, family in (("s32k116_2k", "s32k1xx"), ("stm32f446_unequal", "stm32")):
            if not (ROOT / "bootloader" / "test" / "layouts" / f"{name}.yaml").is_file():
                continue                            # TOOL-244: layout of the private family stm32, not exported
            with self.subTest(name):
                gen = hostbuild.write_test_layout(name, self.tmp / name)
                self.assertEqual(hostbuild.layout_family(gen), family)
                canflash.use_layout(canflash.load_build(gen / "bootloader" / "bl_memory.h"))
                self.assertEqual(canflash.selected_family(), family)

    def test_family_of_a_build(self):
        canflash.use_layout(canflash.load_build(self.build_tree("s32k1xx")))
        self.assertEqual(canflash.selected_family(), "s32k1xx")

    def test_layout_without_family_is_an_error(self):
        """A layout generated before BL-10 names no family: no guess from project.cmake or the ported families any
        more, but the advice to configure the build again."""
        canflash.use_layout(canflash.load_build(self.build_tree(None)))
        with self.assertRaisesRegex(ValueError, "names no MCU family.*generated before BL-10, configure the build"):
            canflash.selected_family()
        canflash.use_layout({k: v for k, v in canflash.target_from_project(EVAL_PROJECT).items()
                             if k != canflash.FAMILY_KEY})
        with self.assertRaisesRegex(ValueError, "no MCU family"):
            canflash.selected_family()

    def test_family_without_updater_port_is_refused(self):
        """An STM32 build must not be checked with the S32K1 rule (FCF at 0x400) - there is no updater port yet."""
        image = bytes(make_bl_image(12000, seed=3))
        canflash.use_layout(canflash.target_from_project(EVAL_PROJECT))
        self.assertEqual(canflash.check_bootloader_image(image), "nextbsw-bl 0.2.0")
        for values in ({**canflash.target_from_project(EVAL_PROJECT), canflash.FAMILY_KEY: "stm32"},
                       canflash.load_build(self.build_tree("stm32"))):
            with self.subTest(values.get(canflash.FAMILY_KEY, "build")):
                canflash.use_layout({**values, canflash.PREFIX_KEY: "nextbsw-bl"})
                with self.assertRaisesRegex(ValueError, "no bootloader image rule for the family stm32"):
                    canflash.check_bootloader_image(image)

    def test_blimage_of_a_build_without_note(self):
        """canflash blimage as the updater build calls it: same output as before BL-08, nothing on stderr."""
        from contextlib import redirect_stderr, redirect_stdout
        from intelhex import IntelHex

        image = make_bl_image(12000, seed=4, version=b"S32K14x-BL 0.1.0")
        hex_file = self.tmp / "bootloader.hex"
        ih = IntelHex()
        ih.frombytes(bytes(image), offset=0)
        ih.write_hex_file(str(hex_file))
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            result = canflash.main(["--layout", str(self.build_tree("s32k1xx")), "blimage", str(hex_file),
                                    "--bin-out", str(self.tmp / "blob.bin")])
        self.assertEqual(result, 0, err.getvalue())
        self.assertEqual(out.getvalue(), f"S32K14x-BL 0.1.0, {len(image)} bytes, CRC-32 0x{zlib.crc32(image):08X}\n")
        self.assertEqual(err.getvalue(), "")

    def test_cores_know_no_image_start(self):
        """No FCF, no vector table layout, no Thumb bit in the code of the cores (comments aside)."""
        forbidden = re.compile(r"FCF|fcf|0x0*400|0x0*410|~1UL|VTOR|__set_MSP|\bSCB\b|\bNVIC\b")
        cores = [*(ROOT / "bootloader" / "src" / "core").glob("*.[ch]"),
                 *(ROOT / "bootloader" / "updater" / "src" / "core").glob("*.[ch]"),
                 *(ROOT / "bootloader" / "updater" / "include").glob("*.h")]
        self.assertGreater(len(cores), 10)
        for path in cores:
            with self.subTest(path.name):
                self.assertEqual(forbidden.findall(strip_c_comments(path.read_text(encoding="utf-8"))), [])

    def test_every_family_brings_its_hooks(self):
        platforms = ROOT / "bootloader" / "platforms"
        families = sorted(p.name for p in platforms.iterdir() if (p / f"hal_{p.name}.c").is_file())
        self.assertIn("s32k1xx", families)
        if (platforms / "stm32").is_dir():          # TOOL-244: private family, missing in the exported open tree
            self.assertIn("stm32", families)
        for family in families:
            with self.subTest(family):
                self.assertTrue((platforms / family / "bl_family_image.inc").is_file())
        ported = sorted(p.name for p in (ROOT / "bootloader" / "updater" / "platforms").iterdir() if p.is_dir())
        self.assertEqual(sorted(canflash.IMAGE_RULES), ported, "tool rule and updater port per family")
        for family in ported:
            with self.subTest(updater=family):
                self.assertTrue((platforms / family / "upd_family.h").is_file())
                self.assertTrue((platforms / family / "upd_family_check_image.inc").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
