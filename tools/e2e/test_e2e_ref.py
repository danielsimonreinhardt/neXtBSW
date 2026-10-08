"""Tests of the Python E2E reference model against published AUTOSAR values (COM-10).

Sources (R22-11, www.autosar.org):
  CRC:  AUTOSAR_SWS_CRCLibrary Tables 7.1-7.4 (check values, result tables)
  P01:  AUTOSAR_PRS_E2EProtocol 6.5.5, Tables 6.18-6.27
  P02:  AUTOSAR_PRS_E2EProtocol 6.6.5, Tables 6.34-6.36
  P11:  AUTOSAR_PRS_E2EProtocol 6.12.5, Tables 6.86-6.92
Run: .venv/Scripts/python -m unittest tools/e2e/test_e2e_ref.py
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import e2e_ref as e  # noqa: E402

CRC8_TABLE_7_2 = [("00 00 00 00", 0x59), ("F2 01 83", 0x37), ("0F AA 00 55", 0x79), ("00 FF 55 11", 0xB8),
                  ("33 22 55 AA BB CC DD EE FF", 0xCB), ("92 6B 55", 0x8C), ("FF FF FF FF", 0x74)]
CRC8H2F_TABLE_7_4 = [("00 00 00 00", 0x12), ("F2 01 83", 0xC2), ("0F AA 00 55", 0xC6), ("00 FF 55 11", 0x77),
                     ("33 22 55 AA BB CC DD EE FF", 0x11), ("92 6B 55", 0x33), ("FF FF FF FF", 0x6C)]

# (mode, counter) -> byte 0 and byte 1 of the protected all-zero 8-byte message, Tables 6.20-6.27
P01_EXAMPLES = {(e.DATAID_BOTH, 0): (0xCC, 0x00), (e.DATAID_BOTH, 1): (0x91, 0x01),
                (e.DATAID_ALT, 0): (0xCE, 0x00), (e.DATAID_ALT, 1): (0x02, 0x01),
                (e.DATAID_LOW, 0): (0xCE, 0x00), (e.DATAID_LOW, 1): (0x93, 0x01),
                (e.DATAID_NIBBLE, 0): (0x2A, 0x10), (e.DATAID_NIBBLE, 1): (0x77, 0x11)}

# counter -> CRC byte, Table 6.36 (DataIDList 0x01..0x10, counter starts with 1)
P02_EXAMPLES = [(1, 0x1B), (2, 0x98), (3, 0x31), (4, 0x0D), (5, 0x18), (6, 0x9B), (7, 0x65), (8, 0x08), (9, 0x1D),
                (10, 0x9E), (11, 0x37), (12, 0x0B), (13, 0x1E), (14, 0x9D), (15, 0xCD), (0, 0x0E)]


class CrcTest(unittest.TestCase):
    def test_check_values(self):
        self.assertEqual(e.crc8_sae_j1850(b"123456789"), 0x4B)
        self.assertEqual(e.crc8_h2f(b"123456789"), 0xDF)

    def test_result_tables(self):
        for text, crc in CRC8_TABLE_7_2:
            self.assertEqual(e.crc8_sae_j1850(bytes.fromhex(text)), crc, text)
        for text, crc in CRC8H2F_TABLE_7_4:
            self.assertEqual(e.crc8_h2f(bytes.fromhex(text)), crc, text)

    def test_magic_check(self):
        # Tables 7.1/7.3 and Example 7.2: CRC over data plus its own CRC, XORed with the XOR value, is the magic check
        for text, _ in CRC8_TABLE_7_2:
            data = bytes.fromhex(text)
            self.assertEqual(e.crc8_sae_j1850(data + bytes([e.crc8_sae_j1850(data)])) ^ 0xFF, 0xC4)
            self.assertEqual(e.crc8_h2f(data + bytes([e.crc8_h2f(data)])) ^ 0xFF, 0x42)
        self.assertEqual(e.crc8_sae_j1850(bytes(4) + b"\x59"), 0x3B)  # Example 7.2 intermediate value


class SpecExampleTest(unittest.TestCase):
    def test_p01(self):
        for (mode, counter), expected in P01_EXAMPLES.items():
            data = bytearray(8)
            rc, nxt = e.p01_protect(e.P01Config(data_id_mode=mode), counter, data)
            self.assertEqual(rc, e.E_OK)
            self.assertEqual(nxt, counter + 1)
            self.assertEqual(bytes(data), bytes(expected) + bytes(6), (mode, counter))

    def test_p02(self):
        cfg, counter = e.P02Config(), 0
        for expected_counter, crc in P02_EXAMPLES:
            data = bytearray(8)
            rc, counter = e.p02_protect(cfg, counter, data)
            self.assertEqual((rc, counter), (e.E_OK, expected_counter))
            self.assertEqual(bytes(data), bytes([crc, expected_counter]) + bytes(6))

    def test_p11(self):
        for mode, counter in ((e.DATAID_BOTH, 0), (e.DATAID_BOTH, 1), (e.DATAID_NIBBLE, 0), (e.DATAID_NIBBLE, 1)):
            data = bytearray(8)
            rc, _ = e.p11_protect(e.P11Config(data_id_mode=mode), counter, data)
            self.assertEqual(rc, e.E_OK)
            self.assertEqual(bytes(data), bytes(P01_EXAMPLES[(mode, counter)]) + bytes(6), (mode, counter))

    def test_p11_offset_64(self):
        # Table 6.92: CRCOffset 64, CounterOffset 72, DataIDNibbleOffset 76, 16 bytes
        cfg = e.P11Config(crc_offset=64, counter_offset=72, data_id_nibble_offset=76, data_id_mode=e.DATAID_NIBBLE,
                          data_length=128)
        data = bytearray(16)
        self.assertEqual(e.p11_protect(cfg, 0, data)[0], e.E_OK)
        self.assertEqual(bytes(data), bytes(8) + bytes([0x7D, 0x10]) + bytes(6))


class BehaviourTest(unittest.TestCase):
    """Sanity of the model's check side (the detailed comparison against C is test_e2e_host.py)."""

    def test_p11_sequence(self):
        cfg, st, counter = e.P11Config(max_delta_counter=2), e.P11CheckState(), 0
        seen = []
        for step in range(40):
            data = bytearray(8)
            _, counter = e.p11_protect(cfg, counter, data)
            if step in (5, 20, 21):
                continue                     # lost messages
            e.p11_check(cfg, st, data)
            seen.append(st.status)
            if step == 30:
                e.p11_check(cfg, st, data)   # repetition
                seen.append(st.status)
        self.assertEqual(seen.count(e.P11STATUS_OKSOMELOST), 1)
        self.assertEqual(seen.count(e.P11STATUS_WRONGSEQUENCE), 1)
        self.assertEqual(seen.count(e.P11STATUS_REPEATED), 1)
        self.assertEqual(e.p11_check(cfg, st, None), e.E_OK)
        self.assertEqual(st.status, e.P11STATUS_NONEWDATA)

    def test_p01_first_data_and_wrong_crc(self):
        cfg, st = e.P01Config(), e.P01CheckState()
        data = bytearray(8)
        e.p01_protect(cfg, 7, data)
        self.assertEqual(e.p01_check(cfg, st, data), e.E_OK)
        self.assertEqual(st.status, e.P01STATUS_INITIAL)
        data[3] ^= 0x10
        e.p01_check(cfg, st, data)
        self.assertEqual(st.status, e.P01STATUS_WRONGCRC)
        self.assertEqual(e.legacy_map_status(e.E_OK, st.status, True), e.P_ERROR)

    def test_p01_nibble_mismatch(self):
        cfg, st = e.P01Config(data_id_mode=e.DATAID_NIBBLE), e.P01CheckState()
        data = bytearray(8)
        e.p01_protect(cfg, 0, data)
        other = e.P01Config(data_id_mode=e.DATAID_NIBBLE, data_id=0x223)
        e.p01_check(other, st, data)
        self.assertEqual(st.status, e.P01STATUS_WRONGCRC)


if __name__ == "__main__":
    unittest.main(verbosity=2)
