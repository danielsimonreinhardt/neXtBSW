"""S3server of the bootloader around long requests and long jobs (BL-29, NRC 0x24 of VER-07).

The session layer timing of ISO 14229-2 (doc/confidential, section on session timing, table of the tS3_Server start
and stop conditions) stops S3 in the server when a request starts - with the first frame of a segmented request -
and starts it again only when the final response has been transmitted (a 0x78 does not count), or when the action
is complete if no response is sent, or when the reception of a segmented request ends in an error. Before BL-29
the bootloader counted S3 from the moment the last request was complete and let it run on during the reception of
the next one and during a long flash job. A block that took longer than S3 to arrive - STmin 1 ms on a Windows
tester with gaps of up to 80 ms per frame, 147 frames per 1026-byte block - came in after the session had already
fallen back to default: the download was closed and TransferData got NRC 0x24.

Driven frame by frame on the bootloader's simulated clock (no real time, no tester library), so the timing is
exact and the tests are independent of the machine's load. Run for three memory layouts, because S3 is core
logic and must not depend on the flash (eval board S32K144, testproject_bootloader with 8-KB sectors at
0x08000000 on another CAN bus, S32K116 with 2-KB sectors).

Run: .venv/Scripts/python -m unittest bootloader/test/host/test_bl_s3_host.py
"""

import ctypes
import struct
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools" / "canflash"))
import canflash  # noqa: E402
import test_bootloader_host as tbh  # noqa: E402  (host DLL build and the simulated bus of bootloader-host)

try:
    import ziglang  # noqa: F401

    HAVE_ZIG = True
except ImportError:  # pragma: no cover - the test skips without the host compiler
    HAVE_ZIG = False

CFG = canflash.CFG
SID_TRANSFER_DATA = 0x36


def setUpModule():
    tbh.setUpModule()


def tearDownModule():
    tbh.tearDownModule()


@unittest.skipUnless(HAVE_ZIG, "ziglang not installed (host compiler)")
class S3AroundLongRequestsTest(unittest.TestCase):
    """Eval board memory layout (projects/evalboard_s32k144_demo)."""

    PROJECT, MCUDB, LAYOUT = tbh.EVAL_PROJECT, None, None

    # the helpers of bootloader-host, without its test cases
    setUpClass = classmethod(tbh.BootloaderTest.setUpClass.__func__)
    tearDownClass = classmethod(tbh.BootloaderTest.tearDownClass.__func__)
    setUp = tbh.BootloaderTest.setUp
    power_on = tbh.BootloaderTest.power_on
    run_ms = tbh.BootloaderTest.run_ms
    client = tbh.BootloaderTest.client
    fingerprint = tbh.BootloaderTest.fingerprint
    programming_client = tbh.BootloaderTest.programming_client
    raw = tbh.BootloaderTest.raw

    # ------------------------------------------------------------------ frame level

    def frame_in(self, data: bytes):
        pad = CFG["BL_ISOTP_PADDING"]
        with self.bl.lock:
            buf = (ctypes.c_uint8 * 8).from_buffer_copy(data.ljust(8, bytes([pad])))
            self.bl.dll.host_can_rx(ctypes.c_uint32(CFG["BL_CAN_ID_REQUEST_PHYS"]), buf, ctypes.c_uint8(8))

    def frames_out(self) -> list[bytes]:
        out, cid, length, buf = [], ctypes.c_uint32(), ctypes.c_uint8(), (ctypes.c_uint8 * 8)()
        with self.bl.lock:
            while self.bl.dll.host_can_tx(ctypes.byref(cid), buf, ctypes.byref(length)):
                self.assertEqual(cid.value, CFG["BL_CAN_ID_RESPONSE"])
                out.append(bytes(buf[: length.value]))
        return out

    def send_slowly(self, payload: bytes, gap_ms: int, frames: int | None = None) -> list[bytes]:
        """A segmented request, `gap_ms` between two frames (below N_Cr, so ISO-TP accepts it); `frames`: stop
        after that many frames (a tester that gives up). Returns the frames the bootloader sent meanwhile."""
        self.assertLess(gap_ms, CFG["BL_ISOTP_N_CR_MS"])
        chunks = [bytes([0x10 | (len(payload) >> 8), len(payload) & 0xFF]) + payload[:6]]
        chunks += [bytes([0x20 | (sn & 0x0F)]) + payload[pos:pos + 7]
                   for sn, pos in enumerate(range(6, len(payload), 7), start=1)]
        out = []
        sent = chunks[:frames]
        for index, chunk in enumerate(sent):
            self.frame_in(chunk)
            self.run_ms(gap_ms if index + 1 < len(sent) else 1)    # no gap behind the last frame
            out += self.frames_out()
        return out

    def responses(self, frames: list[bytes]) -> list[bytes]:
        """UDS payloads of single frame responses; flow control frames are left out."""
        return [f[1:1 + f[0]] for f in frames if (f[0] >> 4) == 0]

    def session(self) -> int:
        """Active session from DID 0xF186 - the request restarts S3 like any other."""
        rsp = self.raw(bytes([0x22, 0xF1, 0x86]))
        self.assertEqual(rsp[0][:3], bytes([0x62, 0xF1, 0x86]))
        return rsp[0][3]

    def open_download(self, size: int) -> None:
        """Programming session, security access, fingerprint, erase and RequestDownload through canflash; the
        client is closed afterwards so that from here on only the test talks to the bootloader."""
        self.power_on()
        client = self.programming_client()
        start = CFG["BL_APP_START"]
        client.routine_control(canflash.RID_ERASE_MEMORY, 1, bytes([0x44]) + struct.pack(">II", start, size))
        client.request_download(tbh.udsoncan.MemoryLocation(start, size, 32, 32))
        client.close()
        self.frames_out()

    def slow_block_gap(self, payload_len: int) -> int:
        """Gap per frame that makes the reception of `payload_len` bytes last longer than S3 by 20 %."""
        frames = 1 + (payload_len - 6 + 6) // 7
        return -(-CFG["BL_S3_SERVER_MS"] * 12 // 10 // frames)

    # ------------------------------------------------------------------ cases

    def test_block_received_longer_than_s3_is_programmed(self):
        """The VER-07 case: a TransferData block of maximum length whose frames take 1.2 x S3 to arrive (147
        frames, gaps far below N_Cr). Before BL-29: 7F 36 24, the session had fallen back to default during the
        reception."""
        block = 1024
        self.open_download(2 * block)
        payload = bytes([SID_TRANSFER_DATA, 0x01]) + bytes(range(256)) * (block // 256)
        gap = self.slow_block_gap(len(payload))
        start = self.bl.dll.host_millis()
        out = self.send_slowly(payload, gap)
        self.run_ms(50)
        out += self.frames_out()
        self.assertGreater(self.bl.dll.host_millis() - start, CFG["BL_S3_SERVER_MS"])
        final = [r for r in self.responses(out) if r[:1] != b"\x7f" or r[2:3] != b"\x78"]
        self.assertEqual(final, [bytes([0x76, 0x01])], "block accepted, no NRC 0x24")
        self.assertEqual(self.bl.read(CFG["BL_APP_START"], block), payload[2:])
        self.assertEqual(self.session(), 0x02)

    def test_s3_starts_with_the_final_response_and_still_expires(self):
        """After the slow block S3 runs from the response on: S3 - 50 ms later the session is still open, and a
        tester that stays silent for longer than S3 still loses it (the fix must not keep a session alive)."""
        self.open_download(1024)
        payload = bytes([SID_TRANSFER_DATA, 0x01]) + bytes(64)
        self.send_slowly(payload, self.slow_block_gap(len(payload)))
        self.run_ms(20)
        self.frames_out()
        self.run_ms(CFG["BL_S3_SERVER_MS"] - 50)
        self.assertEqual(self.session(), 0x02)
        self.run_ms(CFG["BL_S3_SERVER_MS"] + 50)
        self.assertEqual(self.session(), 0x01)

    def test_aborted_reception_restarts_s3(self):
        """A segmented request that ends in an N_Cr timeout restarts S3 at the error, not at the last complete
        request: here the error comes S3 + ~0.8 s after the last request, and the session is still open."""
        self.power_on(boot_request=True)            # programming session, as after 10 02 in the application
        self.run_ms(20)
        self.assertEqual(self.session(), 0x02)
        self.run_ms(CFG["BL_S3_SERVER_MS"] - 200)
        self.send_slowly(bytes([SID_TRANSFER_DATA, 0x01]) + bytes(100), gap_ms=10, frames=5)
        self.run_ms(CFG["BL_ISOTP_N_CR_MS"] + 50)   # the tester is gone in the middle of the message
        self.assertEqual(self.session(), 0x02)
        self.run_ms(CFG["BL_S3_SERVER_MS"] + 50)
        self.assertEqual(self.session(), 0x01)

    def test_s3_starts_after_a_flash_job_longer_than_s3(self):
        """An erase that takes longer than S3 (STM32F4 128-KB sectors, or the whole S32K148 application area at the
        measured 42 ms per 4-KB sector: about 16 s) is announced with 0x78, which does not restart S3. S3 starts
        with the final response. Before BL-29 the session fell back right after the positive erase response, and
        the RequestDownload that follows was refused."""
        self.power_on()
        client = self.programming_client()
        client.close()
        self.frames_out()
        sector = self.bl.sector_at(CFG["BL_APP_START"])[1]
        per_sector = -(-CFG["BL_S3_SERVER_MS"] * 12 // 10 // 2)        # two sectors: 1.2 x S3
        self.bl.dll.host_set_costs(per_sector, 1)
        start = CFG["BL_APP_START"]
        erase = bytes([0x31, 0x01, 0xFF, 0x00, 0x44]) + struct.pack(">II", start, 2 * sector)
        before = self.bl.dll.host_millis()
        out = self.send_slowly(erase, gap_ms=1)
        for _ in range(4 * CFG["BL_S3_SERVER_MS"] // 100):
            self.run_ms(100)
            out += self.frames_out()
            if any(r[:1] == b"\x71" for r in self.responses(out)):
                break
        self.assertIn(bytes([0x71, 0x01, 0xFF, 0x00]), [r[:4] for r in self.responses(out)])
        self.assertGreater(self.bl.dll.host_millis() - before, CFG["BL_S3_SERVER_MS"])
        self.assertEqual(self.session(), 0x02, "the session outlives a job longer than S3")


@unittest.skipUnless(HAVE_ZIG, "ziglang not installed (host compiler)")
class S3OtherLayoutTest(S3AroundLongRequestsTest):
    """testproject_bootloader: 8-KB sectors at 0x08000000, no SBC, other CAN bus and ids."""

    PROJECT, MCUDB, LAYOUT = tbh.OTHER_PROJECT, tbh.TEST_MCUDB, None


@unittest.skipUnless(HAVE_ZIG, "ziglang not installed (host compiler)")
class S3S32K116Test(S3AroundLongRequestsTest):
    """Smallest derivative of the focus family, 2-KB sectors (synthetic layout s32k116_2k)."""

    PROJECT, MCUDB, LAYOUT = None, None, "s32k116_2k"


if __name__ == "__main__":
    unittest.main()
