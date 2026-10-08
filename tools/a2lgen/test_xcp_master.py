"""TOOL-199: framing of XcpMaster.download()/write() - block mode (DOWNLOAD_NEXT) when the slave's
GET_COMM_MODE_INFO reports it and the data fits one block, several DOWNLOAD of their own SET_MTA otherwise -
against a scripted transport that only acknowledges every command (PID_RES), so what is checked is the commands
sent, not a real memory. ecu/xcp/test/test_xcp_host.py exercises the same code against the real slave engine of
ecu/xcp (which always reports block mode, MAX_BS 16), tools/studio/tests/test_livecal.py against the scripted
Studio slave (which does not report it, TOOL-11) - the case this module's fallback is for.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from xcp_master import DOWNLOAD, DOWNLOAD_NEXT, SET_MTA, XcpError, XcpMaster  # noqa: E402


class ScriptedTransport:
    """Acknowledges every command with PID_RES (0xFF): a stand-in for a slave that always agrees, so only the
    commands XcpMaster sends are under test here."""

    def __init__(self):
        self.sent: list[bytes] = []

    def send(self, data: bytes) -> None:
        self.sent.append(bytes(data))

    def receive(self, timeout: float) -> bytes:
        return b"\xFF"


def _master(max_bs: int = 1, max_cto: int = 8) -> tuple[XcpMaster, ScriptedTransport]:
    transport = ScriptedTransport()
    master = XcpMaster(transport)
    master.max_cto = max_cto
    master.max_bs = max_bs
    return master, transport


def _cmd(sent: list[bytes], code: int) -> list[bytes]:
    return [d for d in sent if d[0] == code]


class DownloadFramingTest(unittest.TestCase):
    def test_one_packet_is_a_single_download(self):
        master, transport = _master(max_bs=1)
        self.assertTrue(master.download(bytes([1, 2, 3, 4, 5, 6])))          # per = max_cto - 2 = 6
        self.assertEqual(transport.sent, [bytes([DOWNLOAD, 6, 1, 2, 3, 4, 5, 6])])
        self.assertTrue(master.write_is_atomic(6))

    def test_block_mode_when_the_slave_reports_it_and_it_fits(self):
        master, transport = _master(max_bs=16)                              # like ecu/xcp: GET_COMM_MODE_INFO
        data = bytes(range(1, 13))                                          # 12 bytes: DOWNLOAD(6) + DOWNLOAD_NEXT(6)
        self.assertTrue(master.download(data))
        self.assertEqual(transport.sent, [bytes([DOWNLOAD, 12]) + data[:6],
                                          bytes([DOWNLOAD_NEXT, 6]) + data[6:]])
        self.assertTrue(master.write_is_atomic(12))

    def test_more_than_one_block_returns_false_without_sending_anything(self):
        master, transport = _master(max_bs=16)                              # block capacity 96 bytes
        self.assertFalse(master.download(bytes(97)))
        self.assertEqual(transport.sent, [])                                # the caller decides what to do
        self.assertFalse(master.write_is_atomic(97))

    def test_without_block_mode_write_falls_back_to_several_downloads(self):
        """TOOL-199: max_bs 1 (no GET_COMM_MODE_INFO, e.g. the scripted Studio slave) - write() sends its own
        SET_MTA before each DOWNLOAD after the first (which reuses the MTA it just set); not atomic, XcpLink tells
        the Studio before it asks for confirmation (write_is_atomic())."""
        master, transport = _master(max_bs=1)
        data = bytes(range(1, 16))                                          # 15 bytes: chunks of 6, 6, 3
        self.assertFalse(master.write(0x1000, data))
        self.assertFalse(master.write_is_atomic(len(data)))
        sets = _cmd(transport.sent, SET_MTA)
        downloads = _cmd(transport.sent, DOWNLOAD)
        self.assertEqual(len(sets), 3)                                      # 0x1000, 0x1006, 0x100C
        self.assertEqual([int.from_bytes(s[4:8], "little") for s in sets], [0x1000, 0x1006, 0x100C])
        self.assertEqual([d[1] for d in downloads], [6, 6, 3])
        self.assertEqual(b"".join(d[2:2 + d[1]] for d in downloads), data)
        self.assertEqual(_cmd(transport.sent, DOWNLOAD_NEXT), [])           # no block mode: never used

    def test_more_than_one_block_falls_back_to_several_blocks(self):
        """A write bigger than even one full block (per * max_bs) - each block the slave gets is atomic, the
        write as a whole is not (TOOL-199); see ecu/xcp/test/test_xcp_host.py for the real slave (96 bytes)."""
        master, transport = _master(max_bs=16)
        self.assertFalse(master.write(0x3000, bytes(100), element_size=1))
        self.assertFalse(master.write_is_atomic(100))
        downloads = _cmd(transport.sent, DOWNLOAD)
        self.assertEqual([d[1] for d in downloads], [96, 4])                # one full block, then the rest
        self.assertEqual(len(_cmd(transport.sent, DOWNLOAD_NEXT)), 15)      # the first block used it, the rest not

    def test_element_size_keeps_each_download_a_whole_number_of_elements(self):
        master, transport = _master(max_bs=1)
        self.assertFalse(master.write(0x2000, bytes(range(20)), element_size=4))  # per=6 -> 4 bytes (1) a time
        downloads = _cmd(transport.sent, DOWNLOAD)
        self.assertEqual([d[1] for d in downloads], [4, 4, 4, 4, 4])
        self.assertEqual(b"".join(d[2:2 + d[1]] for d in downloads), bytes(range(20)))

    def test_element_bigger_than_one_packet_is_reported_not_silently_dropped(self):
        master, _transport = _master(max_bs=1)                              # per = 6, an 8 byte element never fits
        with self.assertRaises(XcpError):
            master.write(0x4000, bytes(16), element_size=8)


if __name__ == "__main__":
    unittest.main()
