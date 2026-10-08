# SPDX-License-Identifier: Apache-2.0
"""Unit tests of tools/csec (NVM-13): the host side of the CSEc way back to the factory state.

Only published vectors (sources named at each class):
  FIPS-197 Appendix B and C.1                AES-128
  NIST SP 800-38A F.2.1 CBC-AES128.Encrypt   AES-CBC
  RFC 4493 section 4 (= NIST SP 800-38B D.1) AES-CMAC
  HIS SHE Functional Specification v1.1, example of the memory update protocol (K1, K2, M1..M5): KDF and the
                                             memory update protocol together. The document is not on this machine;
                                             the values are entered as published. All seven 128/256-bit values
                                             matching is the evidence - a wrong digit in the transcription could
                                             not produce a match.
The debug authorisation has no published vector; it is built from the checked KDF and CMAC and tested by
construction. No device, no key of any real part.

Run: .venv/Scripts/python -m unittest tools/csec/test_she.py
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from tools.csec import cli, she  # noqa: E402

h = bytes.fromhex
NIST_KEY = h("2b7e151628aed2a6abf7158809cf4f3c")
NIST_MSG = h("6bc1bee22e409f96e93d7e117393172a" "ae2d8a571e03ac9c9eb76fac45af8e51"
             "30c81c46a35ce411e5fbc1191a0a52ef" "f69f2445df4f9b17ad2b417be66c3710")
DEVICE_SOURCE = ROOT / "platforms" / "s32k1xx" / "bsp" / "bspCsec" / "src" / "CsecRecovery.cpp"
DEVICE_TEST = ROOT / "platforms" / "s32k1xx" / "bsp" / "bspCsec" / "test" / "host" / "CsecRecoveryHostTest.cpp"


class AesTest(unittest.TestCase):
    """FIPS-197 Appendix C.1 (AES-128) and Appendix B (cipher example)."""

    def test_fips197_c1(self):
        self.assertEqual(she.aes128_encrypt(h("000102030405060708090a0b0c0d0e0f"),
                                            h("00112233445566778899aabbccddeeff")),
                         h("69c4e0d86a7b0430d8cdb78070b4c55a"))

    def test_fips197_appendix_b(self):
        self.assertEqual(she.aes128_encrypt(NIST_KEY, h("3243f6a8885a308d313198a2e0370734")),
                         h("3925841d02dc09fbdc118597196a0b32"))

    def test_sizes_refused(self):
        with self.assertRaises(ValueError):
            she.aes128_encrypt(NIST_KEY[:15], bytes(16))
        with self.assertRaises(ValueError):
            she.aes128_encrypt(NIST_KEY, bytes(15))


class CbcTest(unittest.TestCase):
    """NIST SP 800-38A F.2.1 CBC-AES128.Encrypt."""

    def test_sp800_38a_f21(self):
        self.assertEqual(she.cbc_encrypt(NIST_KEY, h("000102030405060708090a0b0c0d0e0f"), NIST_MSG),
                         h("7649abac8119b246cee98e9b12e9197d" "5086cb9b507219ee95db113a917678b2"
                           "73bed6b8e3c1743b7116e69e22229516" "3ff1caa1681fac09120eca307586e1a7"))

    def test_partial_block_refused(self):
        with self.assertRaises(ValueError):
            she.cbc_encrypt(NIST_KEY, bytes(16), bytes(17))


class CmacTest(unittest.TestCase):
    """RFC 4493 section 4, examples 1-4 (NIST SP 800-38B D.1)."""

    def test_rfc4493(self):
        for length, tag in ((0, "bb1d6929e95937287fa37d129b756746"), (16, "070a16b46b4d4144f79bdd9dd04a287c"),
                            (40, "dfa66747de9ae63030ca32611497c827"), (64, "51f0bebf7e3b9d92fc49741779363cfe")):
            with self.subTest(length=length):
                self.assertEqual(she.cmac(NIST_KEY, NIST_MSG[:length]), h(tag))


# HIS SHE Functional Specification v1.1, example of the memory update protocol: KEY_1 (ID 4) loaded with
# 0f0e..00, authorised by the MASTER_ECU_KEY (AuthID 1) 0001..0f, counter 1, no flags, UID 00..01
SHE_AUTH_KEY = h("000102030405060708090a0b0c0d0e0f")
SHE_NEW_KEY = h("0f0e0d0c0b0a09080706050403020100")
SHE_UID = h("000000000000000000000000000001")


class SheExampleTest(unittest.TestCase):
    """KDF and M1..M5 against the SHE example (source: see module docstring)."""

    def test_kdf_k1_k2(self):
        self.assertEqual(she.kdf(SHE_AUTH_KEY, she.KEY_UPDATE_ENC_C), h("118a46447a770d87828a69c222e2d17e"))
        self.assertEqual(she.kdf(SHE_AUTH_KEY, she.KEY_UPDATE_MAC_C), h("2ebb2a3da62dbd64b18ba6493e9fbe22"))

    def test_m1_to_m5(self):
        u = she.memory_update(SHE_NEW_KEY, 4, 1, SHE_AUTH_KEY, 1, SHE_UID)
        self.assertEqual(u.m1, h("00000000000000000000000000000141"))
        self.assertEqual(u.m2, h("2b111e2d93f486566bcbba1d7f7a9797" "c94643b050fc5d4d7de14cff682203c3"))
        self.assertEqual(u.m3, h("b9d745e5ace7d41860bc63c2b9f5bb46"))
        self.assertEqual(u.m4, h("00000000000000000000000000000141" "b472e8d8727d70d57295e74849a27917"))
        self.assertEqual(u.m5, h("820d8d95dc11b4668878160cb2a4e23e"))
        self.assertEqual(she.check_answer(u, u.m4, u.m5), [])

    def test_constants(self):
        # [AN5401] Appendix A and 4.5; DEBUG_KEY_C differs from KEY_UPDATE_ENC_C only in byte 1
        self.assertEqual(she.KEY_UPDATE_ENC_C.hex().upper(), "010153484500800000000000000000B0")
        self.assertEqual(she.KEY_UPDATE_MAC_C.hex().upper(), "010253484500800000000000000000B0")
        self.assertEqual(she.DEBUG_KEY_C.hex().upper(), "010353484500800000000000000000B0")

    def test_mp_is_miyaguchi_preneel(self):
        # by definition, two blocks: H1 = E_0(x1)^x1, H2 = E_H1(x2)^x2^H1
        x1, x2 = NIST_MSG[:16], NIST_MSG[16:32]
        h1 = bytes(a ^ b for a, b in zip(she.aes128_encrypt(bytes(16), x1), x1))
        h2 = bytes(a ^ b ^ c for a, b, c in zip(she.aes128_encrypt(h1, x2), x2, h1))
        self.assertEqual(she.mp_compress(x1 + x2), h2)
        with self.assertRaises(ValueError):
            she.mp_compress(bytes(17))


class MasterKeyLoadTest(unittest.TestCase):
    """The one load the recovery does: MASTER_ECU_KEY into its empty slot, authorised by itself (all ones)."""

    KEY = h("00112233445566778899aabbccddeeff")

    def test_first_load_is_the_she_protocol_with_the_empty_key(self):
        u = she.first_master_key_load(self.KEY)
        self.assertEqual(u, she.memory_update(self.KEY, 1, 1, b"\xff" * 16, 1, bytes(15)))
        self.assertEqual(u.m1, bytes(15) + b"\x11")
        uid = h("0102030405060708090a0b0c0d0e0f")
        self.assertEqual(she.first_master_key_load(self.KEY, uid).m1, uid + b"\x11")

    def test_block0_is_the_same_for_every_master_key(self):
        block0 = she.first_load_m2_block0()
        for key in (self.KEY, SHE_NEW_KEY, bytes(16), b"\xff" * 16):
            self.assertEqual(she.first_master_key_load(key).m2[:16], block0)
            self.assertEqual(she.first_master_key_load(key, SHE_UID).m2[:16], block0)

    def test_no_flag_can_be_asked_for(self):
        # WRITE_PROTECTION must not be settable at all: no parameter, the flag field is zero
        import inspect
        for fn in (she.memory_update, she.first_master_key_load, she.counter_block):
            names = [p.lower() for p in inspect.signature(fn).parameters]
            self.assertFalse([n for n in names if "flag" in n or "prot" in n or "fid" in n], fn.__name__)
        for counter in (1, 2, she.COUNTER_MAX):
            self.assertEqual(int.from_bytes(she.counter_block(counter), "big") & ((1 << 100) - 1), 0)
        code = re.sub(r'""".*?"""|#.*', "", (HERE / "she.py").read_text(encoding="utf-8"), flags=re.S)
        self.assertNotRegex(code, r"<<\s*99\s*\)\s*\+\s*new_key", "a flag bit in the M2 plaintext")
        self.assertNotIn("<< 95", code, "the flag field of M2 is written somewhere")

    def test_write_protected_m2_differs_in_block0(self):
        # what the device guard is for: the same load with WRITE_PROTECTION (FID bit 4) has another first block
        k1 = she.kdf(she.EMPTY_KEY, she.KEY_UPDATE_ENC_C)
        protected = she.aes128_encrypt(k1, ((1 << 100) | (0x10 << 95)).to_bytes(16, "big"))
        self.assertNotEqual(protected, she.first_load_m2_block0())

    def test_answer_check(self):
        u = she.first_master_key_load(self.KEY)
        self.assertEqual(she.check_answer(u, u.m4, u.m5), [])
        bad = bytearray(u.m5)
        bad[0] ^= 1
        self.assertTrue(she.check_answer(u, u.m4, bytes(bad)))
        with_uid = she.first_master_key_load(self.KEY, SHE_UID)
        self.assertIn("device UID", she.check_answer(u, with_uid.m4, with_uid.m5)[0])

    def test_arguments_checked(self):
        with self.assertRaises(ValueError):
            she.memory_update(self.KEY, 0, 1, she.EMPTY_KEY, 1)
        with self.assertRaises(ValueError):
            she.memory_update(self.KEY, 1, 16, she.EMPTY_KEY, 1)
        with self.assertRaises(ValueError):
            she.memory_update(self.KEY, 1, 1, she.EMPTY_KEY, 0)
        with self.assertRaises(ValueError):
            she.memory_update(self.KEY, 1, 1, she.EMPTY_KEY, 1 << 28)
        with self.assertRaises(ValueError):
            she.first_master_key_load(self.KEY, bytes(16))


class DebugAuthTest(unittest.TestCase):
    """AUTHORIZATION = CMAC_K(CHALLENGE | UID), K = KDF(MASTER_ECU_KEY, DEBUG_KEY_C) ([RM13] Table 36-99)."""

    def test_by_construction(self):
        key, challenge, uid = SHE_AUTH_KEY, h("00" * 15 + "01"), SHE_UID
        k = she.kdf(key, she.DEBUG_KEY_C)
        self.assertEqual(she.debug_key(key), k)
        self.assertEqual(she.debug_authorization(key, challenge, uid), she.cmac(k, challenge + uid))
        self.assertNotEqual(she.debug_key(key), she.kdf(key, she.KEY_UPDATE_ENC_C))
        # every input counts
        self.assertNotEqual(she.debug_authorization(key, challenge, uid),
                            she.debug_authorization(key, challenge, h("00" * 14 + "02")))
        self.assertNotEqual(she.debug_authorization(key, challenge, uid),
                            she.debug_authorization(key, h("00" * 15 + "02"), uid))
        self.assertNotEqual(she.debug_authorization(key, challenge, uid),
                            she.debug_authorization(SHE_NEW_KEY, challenge, uid))

    def test_sizes(self):
        with self.assertRaises(ValueError):
            she.debug_authorization(SHE_AUTH_KEY, bytes(15), SHE_UID)
        with self.assertRaises(ValueError):
            she.debug_authorization(SHE_AUTH_KEY, bytes(16), bytes(16))


class DeviceConstantTest(unittest.TestCase):
    """The device refuses every M2 whose first block is not the one of a first load without flags; its constant and
    the vectors of its host test must be what this module computes."""

    @staticmethod
    def c_array(text: str, name: str) -> bytes:
        m = re.search(name + r"\s*(?:\[[^\]]*\])?\s*(?:=\s*)?\{([^}]*)\}", text)
        assert m, name
        return bytes(int(v, 16) for v in re.findall(r"0x([0-9A-Fa-f]{2})U?", m.group(1)))

    def test_device_block0(self):
        self.assertEqual(self.c_array(DEVICE_SOURCE.read_text(encoding="utf-8"), "FIRST_LOAD_M2_BLOCK0"),
                         she.first_load_m2_block0())

    def test_host_test_vectors(self):
        text = DEVICE_TEST.read_text(encoding="utf-8")
        key = self.c_array(text, "TEST_MASTER_KEY")
        u = she.first_master_key_load(key)
        self.assertEqual(self.c_array(text, "TEST_M1"), u.m1)
        self.assertEqual(self.c_array(text, "TEST_M2"), u.m2)
        self.assertEqual(self.c_array(text, "TEST_M3"), u.m3)
        self.assertEqual(self.c_array(text, "TEST_M4"), u.m4)
        self.assertEqual(self.c_array(text, "TEST_M5"), u.m5)
        k1 = she.kdf(she.EMPTY_KEY, she.KEY_UPDATE_ENC_C)
        protected = she.cbc_encrypt(k1, bytes(16), ((1 << 100) | (0x10 << 95)).to_bytes(16, "big") + key)
        self.assertEqual(self.c_array(text, "TEST_M2_WRITE_PROTECTED"), protected)
        challenge, uid = self.c_array(text, "TEST_CHALLENGE"), self.c_array(text, "TEST_UID")
        self.assertEqual(self.c_array(text, "TEST_AUTH"), she.debug_authorization(key, challenge, uid))


class CliTest(unittest.TestCase):
    """python -m tools.csec: the key comes from a file outside the repository, never from the command line."""

    KEY = "00112233445566778899AABBCCDDEEFF"

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.key = self.dir / "dev.key"
        self.key.write_text(self.KEY + "\n", encoding="utf-8")

    def run_cli(self, *args: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = cli.main(list(args))
        return code, out.getvalue()

    def test_load_master(self):
        code, text = self.run_cli("load-master", "--key-file", str(self.key))
        self.assertEqual(code, 0, text)
        u = she.first_master_key_load(h(self.KEY))
        for value in (u.m1, u.m2, u.m3, u.m4, u.m5):
            self.assertIn(value.hex().upper(), text)
        self.assertIn(cli.LOAD_CONFIRM, text)
        self.assertNotIn(self.KEY, text, "the key itself is never printed")

    def test_console_lines_fit(self):
        # OpenBSW StdioConsoleInput holds a line in etl::string<128>; M1..M3 plus the word would be 166 characters
        u = she.first_master_key_load(h(self.KEY), SHE_UID)
        lines = cli.load_lines(u) + [cli.auth_line(bytes(16), bytes(16))]
        for line in lines:
            self.assertLessEqual(len(line), cli.CONSOLE_LINE_MAX, line)
        self.assertEqual(lines[3], f"csec loadkey {cli.LOAD_CONFIRM}")

    def test_check_load(self):
        u = she.first_master_key_load(h(self.KEY))
        code, text = self.run_cli("check-load", "--key-file", str(self.key), "--m4", u.m4.hex(), "--m5", u.m5.hex())
        self.assertEqual(code, 0, text)
        code, text = self.run_cli("check-load", "--key-file", str(self.key), "--m4", u.m4.hex(), "--m5", "00" * 16)
        self.assertEqual(code, 1, text)

    def test_debug_auth(self):
        challenge, uid = "11" * 16, "22" * 15
        code, text = self.run_cli("debug-auth", "--key-file", str(self.key), "--challenge", challenge, "--uid", uid)
        self.assertEqual(code, 0, text)
        auth = she.debug_authorization(h(self.KEY), h(challenge), h(uid))
        self.assertIn(auth.hex().upper(), text)
        self.assertIn(cli.auth_confirm(h(challenge)), text)
        self.assertEqual(cli.auth_confirm(h(challenge)), "DBGAUTH-11111111")

    def test_key_file_inside_the_repository_refused(self):
        inside = HERE / "test_key_inside_repo.key"
        try:
            inside.write_text(self.KEY, encoding="utf-8")
            code, text = self.run_cli("load-master", "--key-file", str(inside))
        finally:
            inside.unlink(missing_ok=True)
        self.assertEqual(code, 2)
        self.assertIn("repository", text)

    def test_bad_key_file(self):
        self.key.write_text("0011", encoding="utf-8")
        code, _ = self.run_cli("load-master", "--key-file", str(self.key))
        self.assertEqual(code, 2)
        code, _ = self.run_cli("load-master", "--key-file", str(self.dir / "missing.key"))
        self.assertEqual(code, 2)

    def test_key_from_the_key_store(self):
        """NVM-14: --key-name and --project read the key store of the workstation (NX_CSEC_KEY_DIR here)."""
        store = self.dir / "store"
        store.mkdir()
        (store / "dev-board.key").write_text("# NVM-14 key store format\n" + self.KEY + "\n", encoding="utf-8")
        (store / "testboard_full.key").write_text(self.KEY + "\n", encoding="utf-8")   # PLAT-62: test profile
        expected = she.first_master_key_load(h(self.KEY)).m1.hex().upper()
        with mock.patch.dict(os.environ, {"NX_CSEC_KEY_DIR": str(store)}):
            code, text = self.run_cli("load-master", "--key-name", "dev-board")
            self.assertEqual(code, 0, text)
            self.assertIn(expected, text)
            code, text = self.run_cli("load-master", "--project",
                                      str(HERE.parents[1] / "tools" / "nxconfig" / "test" / "profiles" / "projects"
                                          / "testproject_full"))
            self.assertEqual(code, 0, text)
            self.assertIn(expected, text)
            code, text = self.run_cli("load-master", "--key-name", "missing")
            self.assertEqual(code, 2)
            self.assertIn("csec-key new", text)
            with self.assertRaises(SystemExit):     # argparse: one source only
                self.run_cli("load-master", "--key-name", "dev-board", "--key-file", str(self.key))

    def test_public_test_value_refused(self):
        self.key.write_text("000102030405060708090a0b0c0d0e0f", encoding="utf-8")
        code, text = self.run_cli("load-master", "--key-file", str(self.key))
        self.assertEqual(code, 2)
        self.assertIn("public test value", text)

    def test_no_key_on_the_command_line(self):
        for sub in ("load-master", "check-load", "debug-auth"):
            with self.assertRaises(SystemExit):
                with contextlib.redirect_stderr(io.StringIO()):
                    cli.main([sub, "--key", self.KEY])


if __name__ == "__main__":
    unittest.main()
