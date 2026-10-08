"""Host tests of the bootloader security access (BL-03): HMAC-SHA256 seed/key with a device key per chip.

Four parts:
- SHA-256 and HMAC-SHA256 of the bootloader core (own implementation) against the NIST example vectors
  (FIPS 180-4 examples, CSRC "SHA_All.pdf"), RFC 4231 and Python's hashlib/hmac.
- The security access of the bootloader core as host DLL (simulated flash, CAN, chip UID, key sector, no-init RAM)
  for three memory layouts: eval board project, S32K116 (2-KB sectors, the 10-KB bootloader), STM32F446 (unequal
  sectors, record below the application): right key, wrong key, repetition, lockout (also across a reset), missing
  or damaged key record, other UID, random generator.
- The transition in canflash: a bootloader before BL-03 (S32K14x-BL 0.1.0 on the eval board) keeps XOR/rotl13.
- The key store of the workstation (tools/canflash/blkeys.py) and the provisioning tool (provision.py), without
  hardware: the J-Link script is written, never run.

    .venv/Scripts/python tools/canflash/test_bl_security_host.py
"""

import ctypes
import hashlib
import hmac
import os
import queue
import random
import shutil
import struct
import sys
import tempfile
import threading
import unittest
import zlib
from pathlib import Path
from unittest import mock

import udsoncan
from udsoncan.connections import QueueConnection
from udsoncan.exceptions import NegativeResponseException

sys.path.insert(0, str(Path(__file__).resolve().parent))
import blkeys  # noqa: E402
import canflash  # noqa: E402
import hostbuild  # noqa: E402
import provision  # noqa: E402
import test_bootloader_host as tbh  # noqa: E402  (module only: its TestCases are not collected here)

CFG = canflash.CFG
TEST_MASTER = blkeys.load_master_key(blkeys.TEST_KEY_NAME)
SA = 0x27


# ---------------------------------------------------------------------------------------------------- SHA-256


class Sha256Test(unittest.TestCase):
    """The bootloader's SHA-256/HMAC (bootloader/src/core/sha256.c) through the host DLL of the eval board."""

    @classmethod
    def setUpClass(cls):
        cls.dll = ctypes.CDLL(str(tbh.host_dll(tbh.EVAL_PROJECT)))

    def sha(self, data: bytes, chunk: int | None = None) -> str:
        out = (ctypes.c_uint8 * 32)()
        if chunk is None:
            self.dll.host_sha256(data, ctypes.c_uint32(len(data)), out)
        else:
            self.dll.host_sha256_chunked(data, ctypes.c_uint32(len(data)), ctypes.c_uint32(chunk), out)
        return bytes(out).hex()

    def hmac(self, key: bytes, data: bytes) -> str:
        out = (ctypes.c_uint8 * 32)()
        self.dll.host_hmac_sha256(key, ctypes.c_uint32(len(key)), data, ctypes.c_uint32(len(data)), out)
        return bytes(out).hex()

    def test_nist_examples(self):
        """FIPS 180-4 example messages (NIST CSRC, "SHA_All.pdf"): one block, two blocks, 896 bits, 10^6 x 'a'."""
        vectors = {
            b"abc": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            b"": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq":
                "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1",
            b"abcdefghbcdefghicdefghijdefghijkefghijklfghijklmghijklmnhijklmnoijklmnopjklmnopqklmnopqrlmnopqrsmnopqrst"
            b"nopqrstu": "cf5b16a778af8380036ce59e7b0492370b249b11e8f07a51afac45037afee9d1",
            b"a" * 1000000: "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0",
        }
        for message, digest in vectors.items():
            with self.subTest(length=len(message)):
                self.assertEqual(self.sha(message), digest)
                self.assertEqual(hashlib.sha256(message).hexdigest(), digest)     # the vector itself is right

    def test_every_length_around_the_block_boundaries(self):
        """0..200 bytes (padding into a second block at 56), fed at once and in pieces of 1, 7 and 64 bytes."""
        rnd = random.Random(3)
        for length in range(201):
            data = bytes(rnd.getrandbits(8) for _ in range(length))
            expected = hashlib.sha256(data).hexdigest()
            for chunk in (None, 1, 7, 64):
                with self.subTest(length=length, chunk=chunk):
                    self.assertEqual(self.sha(data, chunk), expected)

    def test_rfc_4231(self):
        """HMAC-SHA256 test cases 1, 2, 3, 4, 6 and 7 of RFC 4231 (6 and 7: key longer than a block)."""
        cases = [
            (b"\x0b" * 20, b"Hi There", "b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7"),
            (b"Jefe", b"what do ya want for nothing?",
             "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843"),
            (b"\xaa" * 20, b"\xdd" * 50, "773ea91e36800e46854db8ebd09181a72959098b3ef8c122d9635514ced565fe"),
            (bytes(range(1, 26)), b"\xcd" * 50, "82558a389a443c0ea4cc819899f2083a85f0faa3e578f8077a2e3ff46729665b"),
            (b"\xaa" * 131, b"Test Using Larger Than Block-Size Key - Hash Key First",
             "60e431591ee0b67f0d8a26aacbf5b77f8e0bc6213728c5140546040f0ee37f54"),
            (b"\xaa" * 131, b"This is a test using a larger than block-size key and a larger than block-size data. "
                            b"The key needs to be hashed before being used by the HMAC algorithm.",
             "9b09ffa71b942fcb27635fbcd5b0e944bfdc63644f0713938a7f51535c3a35e2"),
        ]
        for i, (key, data, mac) in enumerate(cases):
            with self.subTest(case=i):
                self.assertEqual(self.hmac(key, data), mac)
                self.assertEqual(hmac.new(key, data, hashlib.sha256).hexdigest(), mac)

    def test_hmac_against_python_for_key_and_message_lengths(self):
        rnd = random.Random(5)
        for key_len in (0, 1, 31, 32, 33, 63, 64, 65, 100):
            for length in (0, 16, 55, 56, 64, 119):
                key = bytes(rnd.getrandbits(8) for _ in range(key_len))
                data = bytes(rnd.getrandbits(8) for _ in range(length))
                with self.subTest(key=key_len, data=length):
                    self.assertEqual(self.hmac(key, data), hmac.new(key, data, hashlib.sha256).hexdigest())


# ---------------------------------------------------------------------------------------------------- the device


class SecurityAccessTest(unittest.TestCase):
    """Security access of the bootloader core, eval board layout (projects/evalboard_s32k144_demo)."""

    PROJECT, LAYOUT = tbh.EVAL_PROJECT, None

    @classmethod
    def setUpClass(cls):
        path = tbh.host_dll_for_layout(cls.LAYOUT) if cls.LAYOUT else tbh.host_dll(cls.PROJECT)
        cls.saved = tbh.selected()
        canflash.use_layout(canflash.load_build(path.parent / "gen" / "bootloader" / "bl_memory.h"))
        cls.uid_len = canflash.CFG["BL_UID_LEN"]
        cls.bl = tbh.HostBootloader(path)
        for name in ("host_uid_len", "host_random_calls"):
            getattr(cls.bl.dll, name).restype = ctypes.c_uint32

    @classmethod
    def tearDownClass(cls):
        canflash.use_layout(cls.saved)
        cls.bl.dll.host_provision_test_key()
        cls.bl.dll.host_set_random(0, 0)

    def setUp(self):
        self.bl.dll.host_erase_all()
        self.bl.dll.host_set_costs(20, 1)
        self.bl.dll.host_power_loss(ctypes.c_uint8(0))
        self.bl.dll.host_set_random(0, 0)
        self.set_uid(bytes(range(self.uid_len)))
        canflash.use_master_key(TEST_MASTER)

    # ------------------------------------------------------------------ helpers

    def set_uid(self, uid: bytes):
        self.uid = uid
        self.bl.dll.host_set_uid(uid, ctypes.c_uint32(len(uid)))
        self.bl.dll.host_provision_test_key()                 # key sector for this UID, like provision.py

    def set_record(self, record: bytes):
        self.bl.dll.host_set_key_record(record, ctypes.c_uint32(len(record)))

    def device_key(self, uid: bytes | None = None) -> bytes:
        return blkeys.device_key(TEST_MASTER.key, self.uid if uid is None else uid)

    def power_on(self, boot_request=True):
        with self.bl.lock:
            self.bl.dll.host_power_on(1 if boot_request else 0)

    def run_ms(self, ms: int):
        with self.bl.lock:
            for _ in range(ms):
                self.bl.dll.host_poll(ctypes.c_uint32(1))

    def wait_ms(self, client, ms: int):
        """Simulated time passes, TesterPresent every second keeps the programming session (S3 5 s)."""
        while ms > 0:
            step = min(ms, 1000)
            self.run_ms(step)
            client.tester_present()
            ms -= step

    def client(self):
        """A new tester; the one before is closed first, so no second ISO-TP receiver takes the answers."""
        self.close_client()
        bus = tbh.HostBus(self.bl)
        client = canflash.make_client(bus)
        client.open()
        self.opened = (client, bus)
        self.addCleanup(self.close_client)
        return client

    def close_client(self):
        opened = getattr(self, "opened", None)
        if opened is not None:
            self.opened = None
            opened[0].close()
            opened[1].shutdown()

    def raw(self, client, payload: bytes) -> bytes:
        """Request without udsoncan's interpretation; returns the response bytes (positive or 7F xx nrc)."""
        client.conn.send(payload)
        return bytes(client.conn.wait_frame(timeout=2, exception=True))

    def seed(self, client) -> bytes:
        rsp = self.raw(client, bytes([SA, 0x01]))
        self.assertEqual(rsp[:2], b"\x67\x01", rsp.hex())
        return rsp[2:]

    def send_key(self, client, key: bytes) -> bytes:
        return self.raw(client, bytes([SA, 0x02]) + key)

    def programming(self):
        self.power_on()
        client = self.client()
        client.change_session(2)
        return client

    # ------------------------------------------------------------------ identification

    def test_uid_and_security_state_are_readable_in_the_default_session(self):
        """DID 0xF18C = chip UID as the derivative describes it, DID 0xF1A1 = format, method, key state, lengths."""
        self.power_on()
        client = self.client()
        self.assertEqual(self.uid_len, self.bl.dll.host_uid_len())
        self.assertEqual(canflash.read_uid(client), self.uid)
        self.assertEqual(canflash.read_security_info(client),
                         {"version": 1, "method": blkeys.METHOD_HMAC_SHA256, "state": 1, "seed_len": 16,
                          "key_len": 32})

    # ------------------------------------------------------------------ seed and key

    def test_seed_of_16_bytes_and_hmac_key_unlock(self):
        client = self.programming()
        seed = self.seed(client)
        self.assertEqual(len(seed), canflash.SEED_LEN)
        self.assertEqual(self.send_key(client, blkeys.seed_key(self.device_key(), seed)), b"\x67\x02")
        # unlocked: an all-zero seed, as ISO 14229-1 wants it
        self.assertEqual(self.seed(client), bytes(16))

    def test_canflash_unlocks_and_flashes(self):
        """The tester reads UID and method itself and derives the device key from the master key."""
        self.power_on()
        client = self.client()
        data = tbh.make_app_image(5000, seed=9)
        canflash.flash_application(client, data, log=lambda *a, **k: None)
        self.assertEqual(client.nx_security.method, "hmac-sha256")
        self.assertEqual(client.nx_security.uid, self.uid)
        self.assertEqual(self.bl.read(CFG["BL_APP_START"], len(data)), data)

    def test_wrong_key_and_lockout_of_ten_seconds(self):
        client = self.programming()
        codes = []
        for _ in range(3):
            self.seed(client)
            codes.append(self.send_key(client, bytes(32)))
        self.assertEqual(codes, [b"\x7f\x27\x35", b"\x7f\x27\x35", b"\x7f\x27\x36"])
        self.assertEqual(self.raw(client, bytes([SA, 0x01])), b"\x7f\x27\x37")
        # 1 s margin: the host bus adds the real time of the test to the simulated clock
        self.wait_ms(client, CFG["BL_SECURITY_DELAY_MS"] - 1000)
        self.assertEqual(self.raw(client, bytes([SA, 0x01])), b"\x7f\x27\x37")
        self.wait_ms(client, 1100)
        seed = self.seed(client)
        self.assertEqual(self.send_key(client, blkeys.seed_key(self.device_key(), seed)), b"\x67\x02")

    def test_lockout_and_failed_attempts_survive_a_reset(self):
        """An ECU reset must not hand out new attempts: the counter lives in the no-init RAM."""
        client = self.programming()
        for _ in range(3):
            self.seed(client)
            self.send_key(client, bytes(32))
        client = self.programming()                           # reset (no power loss), still locked
        self.assertEqual(self.raw(client, bytes([SA, 0x01])), b"\x7f\x27\x37")
        self.wait_ms(client, CFG["BL_SECURITY_DELAY_MS"] + 100)
        for _ in range(2):
            self.seed(client)
            self.assertEqual(self.send_key(client, bytes(32)), b"\x7f\x27\x35")
        client = self.programming()
        self.seed(client)
        self.assertEqual(self.send_key(client, bytes(32)), b"\x7f\x27\x36")   # third attempt, across the reset

    def test_a_right_key_clears_the_attempts(self):
        client = self.programming()
        for _ in range(2):
            self.seed(client)
            self.send_key(client, bytes(32))
        seed = self.seed(client)
        self.assertEqual(self.send_key(client, blkeys.seed_key(self.device_key(), seed)), b"\x67\x02")
        client = self.programming()
        for expected in (b"\x7f\x27\x35", b"\x7f\x27\x35", b"\x7f\x27\x36"):
            self.seed(client)
            self.assertEqual(self.send_key(client, bytes(32)), expected)

    def test_seeds_do_not_repeat(self):
        """Over resets and power losses: every seed is new (counter and pool in the no-init RAM)."""
        seeds = set()
        for round_ in range(6):
            if round_ == 3:
                self.bl.dll.host_power_loss(ctypes.c_uint8(0xA5))
            client = self.programming()
            for _ in range(8):
                seeds.add(self.seed(client))
        self.assertEqual(len(seeds), 48)
        self.assertNotIn(bytes(16), seeds)

    def test_repeated_key_and_key_of_an_older_seed_are_refused(self):
        client = self.programming()
        first = self.seed(client)
        second = self.seed(client)                            # a new request replaces the challenge
        self.assertNotEqual(first, second)
        self.assertEqual(self.send_key(client, blkeys.seed_key(self.device_key(), first)), b"\x7f\x27\x35")
        # one key per seed: sending the right one after the attempt needs a new seed
        self.assertEqual(self.send_key(client, blkeys.seed_key(self.device_key(), second)), b"\x7f\x27\x24")
        seed = self.seed(client)
        key = blkeys.seed_key(self.device_key(), seed)
        self.assertEqual(self.send_key(client, key), b"\x67\x02")
        client = self.programming()                           # replay of a recorded exchange after a reset
        self.seed(client)
        self.assertEqual(self.send_key(client, key), b"\x7f\x27\x35")

    def test_every_byte_of_the_key_counts(self):
        """A key wrong in a single byte - first, middle or last - is refused (all 32 bytes are compared)."""
        for position in (0, 15, 16, 31):
            with self.subTest(position=position):
                # failed attempts survive a reset (no-init RAM): a power loss starts each position with three tries
                self.bl.dll.host_power_loss(ctypes.c_uint8(0))
                client = self.programming()
                seed = self.seed(client)
                key = bytearray(blkeys.seed_key(self.device_key(), seed))
                key[position] ^= 0x01
                self.assertEqual(self.send_key(client, bytes(key)), b"\x7f\x27\x35")

    def test_key_length(self):
        client = self.programming()
        self.seed(client)
        self.assertEqual(self.send_key(client, bytes(4)), b"\x7f\x27\x13")      # the XOR key of before BL-03
        self.assertEqual(self.send_key(client, bytes(33)), b"\x7f\x27\x13")

    def test_the_uid_decides_the_device_key(self):
        """The same image on another chip: the key of the first chip does not open it."""
        other = bytes((b * 7 + 3) & 0xFF for b in range(self.uid_len))
        self.set_uid(other)
        client = self.programming()
        seed = self.seed(client)
        self.assertEqual(self.send_key(client, blkeys.seed_key(self.device_key(bytes(range(self.uid_len))), seed)),
                         b"\x7f\x27\x35")
        seed = self.seed(client)
        self.assertEqual(self.send_key(client, blkeys.seed_key(self.device_key(other), seed)), b"\x67\x02")

    def test_other_master_key_is_refused(self):
        """A tester with another master key (another project) computes another device key."""
        canflash.use_master_key(blkeys.MasterKey("other", bytes(range(32)), "test", False))
        self.power_on()
        client = self.client()
        client.change_session(2)
        with self.assertRaises(NegativeResponseException) as ctx:
            client.unlock_security_access(1)
        self.assertEqual(ctx.exception.response.code, 0x35)

    def test_random_generator_of_the_family_is_used(self):
        self.bl.dll.host_set_random(1, 0x40)
        before = self.bl.dll.host_random_calls()
        client = self.programming()
        seed = self.seed(client)
        self.assertGreater(self.bl.dll.host_random_calls(), before)
        self.assertEqual(self.send_key(client, blkeys.seed_key(self.device_key(), seed)), b"\x67\x02")

    # ------------------------------------------------------------------ key record

    def test_without_device_key_the_security_access_is_refused_with_a_clear_message(self):
        self.set_record(b"\xff" * 48)
        client = self.programming()
        self.assertEqual(self.raw(client, bytes([SA, 0x01])), b"\x7f\x27\x22")
        self.assertEqual(canflash.read_security_info(client)["state"], 0)
        with self.assertRaises(RuntimeError) as ctx:
            canflash.flash_application(client, tbh.make_app_image(3000), log=lambda *a, **k: None)
        message = str(ctx.exception)
        self.assertIn("no device key provisioned", message)
        self.assertIn("provision", message)
        self.assertIn(self.uid.hex(), message)
        self.assertEqual(self.bl.read(CFG["BL_APP_START"], 16), b"\xff" * 16)      # nothing erased

    def test_damaged_or_foreign_record_counts_as_no_key(self):
        good = blkeys.key_record(self.device_key())
        broken = {
            "crc": good[:40] + bytes([good[40] ^ 1]) + good[41:],
            "method": good[:5] + b"\x02" + good[6:],
            "version": good[:4] + b"\x02" + good[5:],
            "length": good[:6] + b"\x10" + good[7:],
            "magic": b"NXKZ" + good[4:],
            "half written": good[:24] + b"\xff" * 24,
        }
        for name, record in broken.items():
            with self.subTest(name):
                self.set_record(record)
                client = self.programming()
                self.assertEqual(canflash.read_security_info(client)["state"], 2)
                self.assertEqual(self.raw(client, bytes([SA, 0x01])), b"\x7f\x27\x22")
        self.set_record(good)
        client = self.programming()
        seed = self.seed(client)
        self.assertEqual(self.send_key(client, blkeys.seed_key(self.device_key(), seed)), b"\x67\x02")

    def test_provisioning_writes_what_the_bootloader_reads(self):
        """provision.py and the bootloader agree on the key record (same bytes as the host's default record)."""
        out = (ctypes.c_uint8 * 48)()
        self.bl.dll.host_key_record_copy(out)
        self.assertEqual(bytes(out), blkeys.key_record(self.device_key()))

    def test_key_sector_is_outside_every_erase_and_download(self):
        """Neither an application download nor its erase reaches the key sector (it survives every flash)."""
        key = CFG["BL_KEY_ADDR"]
        self.assertTrue(key >= CFG["BL_APP_END"] or key + CFG["BL_KEY_AREA_SIZE"] <= CFG["BL_APP_START"])
        self.assertNotEqual(key, CFG["BL_APP_RECORD_ADDR"])
        client = self.programming()
        client.unlock_security_access(1)
        tbh.BootloaderTest.fingerprint(self, client)
        with self.assertRaises(NegativeResponseException) as ctx:
            client.routine_control(canflash.RID_ERASE_MEMORY, 1, bytes([0x44]) + struct.pack(">II", key, 0x10))
        self.assertEqual(ctx.exception.response.code, 0x31)
        with self.assertRaises(NegativeResponseException) as ctx:
            client.request_download(udsoncan.MemoryLocation(key, 16, 32, 32))
        self.assertEqual(ctx.exception.response.code, 0x70)


class SecurityAccessS32K116Test(SecurityAccessTest):
    """Smallest derivative of the focus family (2-KB sectors, 128-bit SIM UID): bootloader/test/layouts/s32k116_2k."""

    PROJECT, LAYOUT = None, "s32k116_2k"


class SecurityAccessStm32F446Test(SecurityAccessTest):
    """Other family: 96-bit UID, unequal sectors, record below the application, key sector above the record."""

    PROJECT, LAYOUT = None, "stm32f446_unequal"

    def test_uid_length_of_the_family(self):
        self.assertEqual(self.uid_len, 12)
        self.assertEqual(CFG["BL_KEY_ADDR"], 0x08008000)


# ---------------------------------------------------------------------------------------------------- transition


class FakeBootloader:
    """A bootloader over a QueueConnection, answered by `handle(request) -> response`."""

    def __init__(self, handle):
        self.conn = QueueConnection("fake")
        self.handle = handle
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        while not self.stop.is_set():
            try:
                request = bytes(self.conn.touserqueue.get(timeout=0.01))
            except queue.Empty:
                continue
            self.conn.fromuserqueue.put(self.handle(request))

    def __enter__(self):
        self.thread.start()
        self.client = canflash.NxClient(self.conn, config={"data_identifiers": canflash.did_codecs(),
                                                           "request_timeout": 2})
        self.client.set_config("security_algo", lambda level, seed, params: self.client.security_key(seed))
        self.client.open()
        return self.client

    def __exit__(self, *exc):
        self.client.close()
        self.stop.set()
        self.thread.join()


def legacy_bootloader(identification: bytes):
    """The security access of the bootloaders before BL-03: 4-byte seed, XOR/rotl13 key, no DID 0xF1A1/0xF18C."""
    state = {"seed": 0x1234ABCD, "unlocked": False}

    def handle(request: bytes) -> bytes:
        if request == b"\x22\xf1\x80":
            return b"\x62\xf1\x80" + identification
        if request == b"\x27\x01":
            return b"\x67\x01" + struct.pack(">I", state["seed"])
        if request[:2] == b"\x27\x02":
            if request[2:] == struct.pack(">I", blkeys.legacy_key(state["seed"])):
                state["unlocked"] = True
                return b"\x67\x02"
            return b"\x7f\x27\x35"
        return bytes([0x7F, request[0], 0x31 if request[0] == 0x22 else 0x11])

    return handle, state


class TransitionTest(unittest.TestCase):
    def test_flashed_eval_board_bootloader_keeps_xor(self):
        """The bootloader on the eval board reports S32K14x-BL 0.1.0: canflash answers with XOR/rotl13."""
        handle, state = legacy_bootloader(b"S32K14x-BL 0.1.0")
        with FakeBootloader(handle) as client:
            client.unlock_security_access(1)
        self.assertTrue(state["unlocked"])
        self.assertEqual(client.nx_security.method, blkeys.LEGACY_METHOD)

    def test_bootloader_without_did_f1a1_keeps_xor(self):
        """A build of the renamed software before BL-03 (nextbsw-bl 0.1.0) does not know DID 0xF1A1."""
        handle, state = legacy_bootloader(b"nextbsw-bl 0.1.0")
        with FakeBootloader(handle) as client:
            client.unlock_security_access(1)
        self.assertTrue(state["unlocked"])

    def test_legacy_key_is_the_old_formula(self):
        self.assertEqual(blkeys.legacy_key(0x00000001), ((0x00000001 ^ 0x5EC0B007) << 13 | (0x00000001 ^ 0x5EC0B007)
                                                          >> 19) & 0xFFFFFFFF)

    def test_seed_length_that_does_not_fit_the_method_is_an_error(self):
        with self.assertRaises(RuntimeError):
            canflash.SecurityAccess(blkeys.LEGACY_METHOD).key(bytes(16))
        with self.assertRaises(RuntimeError):
            canflash.SecurityAccess("hmac-sha256", bytes(32)).key(bytes(4))

    def test_method_by_identification(self):
        self.assertEqual(canflash.security_method_of("S32K14x-BL 0.1.0"), blkeys.LEGACY_METHOD)
        self.assertIsNone(canflash.security_method_of("nextbsw-bl 0.1.0"))
        self.assertIsNone(canflash.security_method_of(None))

    def test_bl_security_constant_is_gone_from_the_bootloader(self):
        """distcheck rule R7: no key constant in bl_config.h or anywhere in the bootloader any more."""
        for path in (tbh.ROOT / "bootloader").rglob("*"):
            if path.suffix in (".c", ".h", ".inc", ".S", ".ld"):
                self.assertNotIn("BL_SECURITY_CONSTANT", path.read_text(encoding="utf-8", errors="replace"), path)
                self.assertNotIn("5EC0B007", path.read_text(encoding="utf-8", errors="replace").upper(), path)


# ---------------------------------------------------------------------------------------------------- key store


class KeyStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="blkeys_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        env = {k: v for k, v in os.environ.items() if k not in (blkeys.KEY_ENV, blkeys.KEY_FILE_ENV)}
        env[blkeys.KEY_DIR_ENV] = str(self.tmp)
        patcher = mock.patch.dict("os.environ", env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_default_store_is_localappdata(self):
        with mock.patch.dict("os.environ", {"LOCALAPPDATA": str(self.tmp / "lad")}):
            os.environ.pop(blkeys.KEY_DIR_ENV)
            self.assertEqual(blkeys.key_dir(), self.tmp / "lad" / "neXtBSW" / "keys")

    def test_keygen_then_load(self):
        path = blkeys.create_master_key("project_a")
        self.assertEqual(path, self.tmp / "project_a.key")
        key = blkeys.load_master_key("project_a")
        self.assertEqual(len(key.key), 32)
        self.assertFalse(key.insecure)
        self.assertNotIn(key.key.hex(), repr(key))
        with self.assertRaises(blkeys.MasterKeyError):
            blkeys.create_master_key("project_a")                # never overwritten
        self.assertEqual(blkeys.load_master_key("project_a").key, key.key)
        with self.assertRaises(blkeys.MasterKeyError):
            blkeys.create_master_key(blkeys.TEST_KEY_NAME)

    def test_missing_key_names_where_it_was_looked_for(self):
        with self.assertRaises(blkeys.MasterKeyError) as ctx:
            blkeys.load_master_key("nowhere")
        text = str(ctx.exception)
        for part in ("nowhere.key", blkeys.KEY_ENV, blkeys.KEY_FILE_ENV, "keygen"):
            self.assertIn(part, text)

    def test_environment_variables_for_ci_and_studio(self):
        key = bytes(range(32))
        with mock.patch.dict("os.environ", {blkeys.KEY_ENV: key.hex()}):
            loaded = blkeys.load_master_key("whatever")
            self.assertEqual((loaded.key, loaded.source), (key, f"environment variable {blkeys.KEY_ENV}"))
        file = self.tmp / "ci.key"
        file.write_text("# comment\n" + key.hex().upper() + "\n", encoding="utf-8")
        with mock.patch.dict("os.environ", {blkeys.KEY_FILE_ENV: str(file)}):
            self.assertEqual(blkeys.load_master_key("whatever").key, key)
        with mock.patch.dict("os.environ", {blkeys.KEY_ENV: "abcd"}):
            with self.assertRaises(blkeys.MasterKeyError):
                blkeys.load_master_key("whatever")

    def test_test_key_is_marked_insecure_and_documented(self):
        self.assertTrue(TEST_MASTER.insecure)
        self.assertEqual(TEST_MASTER.key, hashlib.sha256(
            b"neXtBSW insecure test key - never provision a real device with it").digest())
        self.assertIn("INSECURE", blkeys.TEST_KEY_FILE.read_text(encoding="utf-8"))

    def test_device_key_and_record(self):
        master, uid = bytes(32), bytes(range(16))
        device = blkeys.device_key(master, uid)
        self.assertEqual(device, hmac.new(master, uid, hashlib.sha256).digest())
        record = blkeys.key_record(device)
        self.assertEqual(len(record), 48)
        self.assertEqual(record[:8], b"NXKY\x01\x01\x20\xff")
        self.assertEqual(struct.unpack("<I", record[40:44])[0], zlib.crc32(record[:40]))


# ---------------------------------------------------------------------------------------------------- provisioning


class ProvisionTest(unittest.TestCase):
    """provision.py without hardware: record, hex file and J-Link script are written, nothing is run."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="provision_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.env = mock.patch.dict("os.environ", {blkeys.KEY_DIR_ENV: str(self.tmp / "keys")})
        self.env.start()
        self.addCleanup(self.env.stop)
        for var in (blkeys.KEY_ENV, blkeys.KEY_FILE_ENV):
            os.environ.pop(var, None)

    def test_eval_board_plan_with_a_key_of_the_store(self):
        blkeys.create_master_key(tbh.EVAL_BOARD_NAME)
        uid = bytes(range(16))
        plan = provision.plan_for_project(tbh.EVAL_PROJECT, uid)
        self.assertEqual(plan.key_address, 0x7E000)
        self.assertEqual(plan.record, blkeys.key_record(blkeys.device_key(
            blkeys.load_master_key(tbh.EVAL_BOARD_NAME).key, uid)))
        files = provision.write_files(plan, self.tmp / "out")
        script = files["script"].read_text(encoding="utf-8")
        self.assertIn("device S32K144", script)
        self.assertIn("speed 1000", script)
        self.assertIn("connect", script)
        self.assertIn(f'loadfile "{files["hex"].as_posix()}"', script)
        self.assertNotIn("erase\n", script)                  # never a mass erase: it would take the bootloader
        from intelhex import IntelHex
        ih = IntelHex(str(files["hex"]))
        self.assertEqual((ih.minaddr(), ih.maxaddr()), (0x7E000, 0x7E000 + 47))
        self.assertEqual(ih.tobinstr(start=0x7E000, size=48), plan.record)
        self.assertNotIn(plan.record[8:40].hex(), script)

    def test_insecure_test_key_is_refused_unless_the_project_allows_it(self):
        with self.assertRaises(provision.ProvisionError) as ctx:
            provision.check_key(blkeys.load_master_key(blkeys.TEST_KEY_NAME), allowed=False)
        self.assertIn("insecure", str(ctx.exception))
        provision.check_key(blkeys.load_master_key(blkeys.TEST_KEY_NAME), allowed=True)
        with mock.patch.dict("os.environ", {blkeys.KEY_FILE_ENV: str(blkeys.TEST_KEY_FILE)}):
            with self.assertRaises(provision.ProvisionError):  # also through NX_BL_KEY_FILE
                provision.check_key(blkeys.load_master_key(tbh.EVAL_BOARD_NAME), allowed=False)

    def test_uid_length_must_fit_the_derivative(self):
        blkeys.create_master_key(tbh.EVAL_BOARD_NAME)
        with self.assertRaises(provision.ProvisionError):
            provision.plan_for_project(tbh.EVAL_PROJECT, bytes(12))

    def test_uid_from_jlink_output(self):
        """mem32 output of J-Link Commander: words in address order, most significant byte first."""
        text = ("J-Link>mem32 0x40048054, 4\n"
                "40048054 = 00112233 44556677 8899AABB CCDDEEFF \n"
                "J-Link>exit\n")
        self.assertEqual(provision.uid_from_jlink(text, 0x40048054, 16),
                         bytes.fromhex("00112233445566778899aabbccddeeff"))
        with self.assertRaises(provision.ProvisionError):
            provision.uid_from_jlink("Cannot connect to target.\n", 0x40048054, 16)


if __name__ == "__main__":
    unittest.main(verbosity=2)
