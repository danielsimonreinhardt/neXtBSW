"""Host test of XCP on CAN (TOOL-01): the slave engine of ecu/xcp, built for the host (zig c++), talks over a
simulated CAN channel (stdin/stdout, one frame per line) with the master of tools/a2lgen/xcp_master.py, which
resolves names through an A2L written by tools/a2lgen for the same test profile.

Covered: connect, measure (SHORT_UPLOAD, UPLOAD), calibrate (DOWNLOAD, block mode) and read back, rejected
accesses (outside the regions, read-only memory, part of an element, too long, too large), the protocol states
(not connected, SYNCH, unknown command, sequence errors) and the consistency of a deferred write (applied in the
writer task only, as a whole, answered afterwards; dropped on timeout). TOOL-151: the EPK at ADDR_EPK is readable
(the EPK check of a calibration tool), writing it is refused, the bytes next to it stay unreachable.

Run: python ecu/xcp/test/test_xcp_host.py   (needs ziglang from the .venv)
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
XCP = HERE.parent
REPO = XCP.parents[1]
sys.path.insert(0, str(REPO / "tools" / "a2lgen"))

import a2lgen  # noqa: E402
from xcp_master import A2l, XcpError, XcpMaster, XcpTimeout, read_epk, read_value, write_value  # noqa: E402

ERR_CMD_SYNCH, ERR_CMD_BUSY, ERR_CMD_UNKNOWN = 0x00, 0x10, 0x20
ERR_OUT_OF_RANGE, ERR_WRITE_PROTECTED, ERR_ACCESS_DENIED, ERR_SEQUENCE = 0x22, 0x23, 0x24, 0x29

# the test profile of xcp_host_slave.cpp
SIGNALS = [a2lgen.Signal("Test.Counter", "", "UINT32", 0x20000000),
           a2lgen.Signal("Test.Temperature", "", "FLOAT32", 0x20000004),
           a2lgen.Signal("Test.Flag", "", "BOOLEAN", 0x20000008),
           a2lgen.Signal("Test.Energy", "", "FLOAT64", 0x20000010)]
PARAMETERS = [a2lgen.Parameter("Test.CalPrm.Alpha", "", "FLOAT32", 0x20000100, 1, (0, 1), 1),
              a2lgen.Parameter("Test.CalPrm.Table", "mV", "UINT16", 0x20000104, 4, (0, 1), 1),
              a2lgen.Parameter("Test.CalPrm.Gain", "", "FLOAT64", 0x20000110, 1, (0, 1), 1),
              a2lgen.Parameter("Test.CalPrm.Curve", "", "UINT8", 0x20000118, 100, (0, 1), 1)]
EPK, EPK_ADDRESS = "xcp_host_test neXtBSW 0.0.0 host", 0x00038BC0  # TOOL-151: xcp_host_slave.cpp, read only

_BUILD: Path | None = None


def build_slave() -> Path:
    global _BUILD
    if _BUILD is None:
        tmp = Path(tempfile.mkdtemp(prefix="xcp_host_"))
        exe = tmp / "xcp_host_slave.exe"
        cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
               "-Wconversion", f"-I{XCP / 'include'}", str(XCP / "src" / "xcp" / "XcpSlave.cpp"),
               str(HERE / "host" / "xcp_host_slave.cpp"), "-o", str(exe)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise AssertionError(r.stderr[-3000:])
        _BUILD = exe
    return _BUILD


class StdioChannel:
    """Simulated CAN channel: one frame per line to the host slave, its responses collected per line."""

    def __init__(self, deferred: bool = False):
        args = [str(build_slave())] + (["--deferred"] if deferred else [])
        self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        self.frames: list[bytes] = []

    def line(self, text: str) -> list[str]:
        self.proc.stdin.write(text + "\n")
        self.proc.stdin.flush()
        out = []
        while True:
            reply = self.proc.stdout.readline()
            if not reply:
                raise AssertionError(f"host slave ended after {text!r}")
            reply = reply.strip()
            if reply == "OK":
                return out
            if reply.startswith("TX "):
                self.frames.append(bytes.fromhex(reply[3:]))
            else:
                out.append(reply)

    # transport interface of XcpMaster
    def send(self, data: bytes) -> None:
        self.line("RX " + data.hex(" "))

    def receive(self, timeout: float) -> bytes | None:
        return self.frames.pop(0) if self.frames else None

    def dump(self, address: int, n: int) -> bytes:
        (mem,) = self.line(f"DUMP {address:x} {n}")
        return bytes.fromhex(mem[3:])

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.stdin.write("QUIT\n")
            self.proc.stdin.flush()
            self.proc.wait(timeout=10)
        self.proc.stdin.close()
        self.proc.stdout.close()


class XcpHostTestBase(unittest.TestCase):
    deferred = False

    @classmethod
    def setUpClass(cls):
        build_slave()
        xcp = a2lgen.XcpCan("CAN0", 0x7F0, 0x7F1, 500000, "test")
        image = a2lgen.Image(SIGNALS, PARAMETERS, True, epk=EPK, epk_address=EPK_ADDRESS)
        cls.a2l_text = a2lgen.render(image, xcp, "xcp_host_test")
        cls.a2l = A2l(cls.a2l_text)

    def setUp(self):
        self.channel = StdioChannel(self.deferred)
        self.master = XcpMaster(self.channel)

    def tearDown(self):
        self.channel.close()

    def assertXcpError(self, code: int, fn, *args):
        with self.assertRaises(XcpError) as ctx:
            fn(*args)
        self.assertEqual(ctx.exception.code, code, str(ctx.exception))


class XcpHostTest(XcpHostTestBase):
    def test_connect(self):
        info = self.master.connect()
        self.assertEqual(info["resource"], 0x01)          # CAL/PAG only
        self.assertEqual(info["comm_mode_basic"] & 0x07, 0)  # Intel byte order, byte granularity
        self.assertEqual((info["max_cto"], info["max_dto"], info["max_bs"]), (8, 8, 16))
        self.assertEqual(self.master.get_status()[0], 0xFF)
        self.assertEqual(self.master.synch(), ERR_CMD_SYNCH)

    def test_not_connected_is_silent(self):
        with self.assertRaises(XcpTimeout):
            self.master.get_status()
        self.master.connect()
        self.master.disconnect()
        with self.assertRaises(XcpTimeout):
            self.master.short_upload(0x20000000, 4)

    def test_measure(self):
        self.master.connect()
        self.assertEqual(read_value(self.master, self.a2l, "Test.Counter"), [0])
        self.assertEqual(read_value(self.master, self.a2l, "Test.Temperature"), [21.5])
        self.channel.line("STEP")
        self.channel.line("STEP")
        self.assertEqual(read_value(self.master, self.a2l, "Test.Counter"), [2])
        self.assertEqual(read_value(self.master, self.a2l, "Test.Temperature"), [22.5])
        self.assertEqual(read_value(self.master, self.a2l, "Test.Energy"), [0.5])  # 8 bytes in two uploads
        # UPLOAD continues at the MTA
        self.master.set_mta(0x20000000)
        self.assertEqual(struct.unpack("<I", self.master.upload(4))[0], 2)
        self.assertEqual(struct.unpack("<f", self.master.upload(4))[0], 22.5)

    def test_calibrate_and_read_back(self):
        self.master.connect()
        self.assertEqual(read_value(self.master, self.a2l, "Test.CalPrm.Alpha"), [0.125])
        write_value(self.master, self.a2l, "Test.CalPrm.Alpha", [1.0])
        self.assertEqual(read_value(self.master, self.a2l, "Test.CalPrm.Alpha"), [1.0])
        self.assertEqual(self.channel.dump(0x20000100, 4), struct.pack("<f", 1.0))
        # array of 8 bytes: DOWNLOAD (6) + DOWNLOAD_NEXT (2), applied as one block
        write_value(self.master, self.a2l, "Test.CalPrm.Table", [1, 2, 3, 65535])
        self.assertEqual(read_value(self.master, self.a2l, "Test.CalPrm.Table"), [1, 2, 3, 65535])
        # 64 bit parameter
        write_value(self.master, self.a2l, "Test.CalPrm.Gain", [-2.75])
        self.assertEqual(self.channel.dump(0x20000110, 8), struct.pack("<d", -2.75))
        # one element of an array
        self.master.write(0x20000106, struct.pack("<H", 222))
        self.assertEqual(read_value(self.master, self.a2l, "Test.CalPrm.Table"), [1, 222, 3, 65535])
        # a block of 96 bytes (16 packets) is the largest write
        self.master.write(0x20000118, bytes(range(96)))
        self.assertEqual(self.channel.dump(0x20000118, 96), bytes(range(96)))

    def test_rejected_accesses(self):
        self.master.connect()
        before = self.channel.dump(0x20000100, 4) + self.channel.dump(0x20000104, 8) + self.channel.dump(0x20000000, 4)
        # outside every region (gap between Table and Gain, and far away)
        self.assertXcpError(ERR_ACCESS_DENIED, self.master.write, 0x2000010C, b"\0\0\0\0")
        self.assertXcpError(ERR_ACCESS_DENIED, self.master.short_upload, 0x2000010C, 1)
        self.assertXcpError(ERR_ACCESS_DENIED, self.master.short_upload, 0x00000000, 4)
        self.assertXcpError(ERR_ACCESS_DENIED, self.master.write, 0x1FFFFFFC, b"\0\0\0\0")
        # a measurement is read only
        self.assertXcpError(ERR_WRITE_PROTECTED, self.master.write, 0x20000000, b"\1\0\0\0")
        # part of an element: wrong length, wrong offset
        self.assertXcpError(ERR_OUT_OF_RANGE, self.master.write, 0x20000100, b"\0\0")
        self.assertXcpError(ERR_OUT_OF_RANGE, self.master.write, 0x20000101, b"\0\0")
        self.assertXcpError(ERR_OUT_OF_RANGE, self.master.write, 0x20000104, b"\0\0\0")
        # across the end of a parameter (Alpha into Table) and of a signal
        self.assertXcpError(ERR_ACCESS_DENIED, self.master.write, 0x20000100, b"\0" * 6)
        self.assertXcpError(ERR_ACCESS_DENIED, self.master.short_upload, 0x20000006, 4)
        # a whole array larger than one block is refused, nothing written
        self.master.set_mta(0x20000118)
        self.assertXcpError(ERR_OUT_OF_RANGE, self.master.command, [0xF0, 100, 0, 0, 0, 0, 0, 0])
        self.assertXcpError(ERR_OUT_OF_RANGE, self.master.command, [0xF0, 0])  # nothing to write
        # UPLOAD longer than one response packet
        self.master.set_mta(0x20000118)
        self.assertXcpError(ERR_OUT_OF_RANGE, self.master.upload, 8)
        after = self.channel.dump(0x20000100, 4) + self.channel.dump(0x20000104, 8) + self.channel.dump(0x20000000, 4)
        self.assertEqual(before, after)

    def test_protocol_errors(self):
        self.master.connect()
        self.assertXcpError(ERR_CMD_UNKNOWN, self.master.command, [0xD0])
        self.assertXcpError(ERR_SEQUENCE, self.master.command, [0xEF, 2, 0, 0])  # DOWNLOAD_NEXT without block
        self.master.set_mta(0x20000104)
        self.master.command([0xF0, 8, 1, 0, 2, 0, 3, 0], expect_response=False)  # block started
        self.assertXcpError(ERR_SEQUENCE, self.master.command, [0xEF, 1, 4])     # 2 bytes remain, not 1
        self.assertEqual(read_value(self.master, self.a2l, "Test.CalPrm.Table"), [10, 20, 30, 40])
        self.assertXcpError(0x21, self.master.command, [0xF6, 0, 0])            # SET_MTA too short


    def test_epk_is_read_only(self):
        """TOOL-151: SHORT_UPLOAD at ADDR_EPK returns the EPK of the A2L; DOWNLOAD there is refused and changes
        nothing; one byte before or after the text, or a read across its end, is outside every region."""
        self.master.connect()
        self.assertEqual((self.a2l.epk, self.a2l.epk_address), (EPK, EPK_ADDRESS))
        self.assertEqual(read_epk(self.master, self.a2l), EPK)             # the check a calibration tool does
        self.assertEqual(self.master.short_upload(EPK_ADDRESS, 7), EPK[:7].encode())
        self.assertEqual(self.master.short_upload(EPK_ADDRESS + len(EPK) - 1, 1), EPK[-1:].encode())
        self.master.set_mta(EPK_ADDRESS + 7)                               # UPLOAD continues at the MTA
        self.assertEqual(self.master.upload(7), EPK[7:14].encode())
        # writing: the region is found but read only - the same bytes, one byte, the first packet of a block
        self.assertXcpError(ERR_WRITE_PROTECTED, self.master.write, EPK_ADDRESS, EPK[:6].encode())
        self.assertXcpError(ERR_WRITE_PROTECTED, self.master.write, EPK_ADDRESS + 3, b"X")
        self.master.set_mta(EPK_ADDRESS)
        self.assertXcpError(ERR_WRITE_PROTECTED, self.master.command, [0xF0, 8] + list(b"XXXXXX"))
        self.assertXcpError(ERR_SEQUENCE, self.master.command, [0xEF, 2] + list(b"XX"))  # no block was started
        self.assertEqual(self.master.get_status()[0], 0xFF)                # no write pending
        # just outside: the byte before, the terminating zero behind the text, a read across the end
        self.assertXcpError(ERR_ACCESS_DENIED, self.master.short_upload, EPK_ADDRESS - 1, 1)
        self.assertXcpError(ERR_ACCESS_DENIED, self.master.short_upload, EPK_ADDRESS + len(EPK), 1)
        self.assertXcpError(ERR_ACCESS_DENIED, self.master.short_upload, EPK_ADDRESS + len(EPK) - 2, 3)
        self.assertXcpError(ERR_ACCESS_DENIED, self.master.write, EPK_ADDRESS - 1, b"X")
        self.assertXcpError(ERR_ACCESS_DENIED, self.master.write, EPK_ADDRESS + len(EPK), b"X")
        self.assertEqual(self.channel.dump(EPK_ADDRESS, len(EPK) + 1), EPK.encode() + b"\0")
        self.assertEqual(read_epk(self.master, self.a2l), EPK)

    def test_implausible_write_is_refused_before_it_takes_effect(self):
        """SYS-90: the memory access refuses a value (the host slave's stand-in rule: Gain inside -100 .. 100);
        the master gets ERR_OUT_OF_RANGE, the memory and the MTA stay, the next plausible write goes through."""
        self.master.connect()
        old = self.channel.dump(0x20000110, 8)
        self.assertXcpError(ERR_OUT_OF_RANGE, self.master.write, 0x20000110, struct.pack("<d", 1000.0))
        self.assertEqual(self.channel.dump(0x20000110, 8), old)
        self.assertXcpError(ERR_OUT_OF_RANGE, self.master.write, 0x20000110, struct.pack("<d", float("nan")))
        self.assertEqual(self.channel.dump(0x20000110, 8), old)
        # a block (DOWNLOAD + DOWNLOAD_NEXT) is refused as a whole once it is complete; the MTA did not move
        self.master.set_mta(0x20000110)
        value = list(struct.pack("<d", -101.0))
        self.master.command([0xF0, 8] + value[:6], expect_response=False)
        self.assertXcpError(ERR_OUT_OF_RANGE, self.master.command, [0xEF, 2] + value[6:])
        self.assertEqual(self.master.get_status()[0], 0xFF)          # not busy: the refusal completed the write
        self.assertEqual(self.channel.dump(0x20000110, 8), old)
        self.master.download(struct.pack("<d", -3.0))                 # MTA still at Gain
        self.assertEqual(self.channel.dump(0x20000110, 8), struct.pack("<d", -3.0))
        write_value(self.master, self.a2l, "Test.CalPrm.Gain", [100.0])
        self.assertEqual(self.channel.dump(0x20000110, 8), struct.pack("<d", 100.0))


class XcpHostDeferredTest(XcpHostTestBase):
    """The write runs in the writer task (TICK), as XcpSystem does in the lowest RTE task."""

    deferred = True

    def test_write_applied_as_a_whole_in_the_writer_task(self):
        self.master.connect()
        old = self.channel.dump(0x20000110, 8)
        self.master.set_mta(0x20000110)
        self.master.command([0xF0, 8] + list(struct.pack("<d", 3.5)[:6]), expect_response=False)
        self.master.command([0xEF, 2] + list(struct.pack("<d", 3.5)[6:]), expect_response=False)
        # not yet written, not yet answered, and busy for other commands
        self.assertEqual(self.channel.frames, [])
        self.assertEqual(self.channel.dump(0x20000110, 8), old)
        self.assertXcpError(ERR_CMD_BUSY, self.master.get_status)
        self.channel.line("TICK")
        self.assertEqual(self.channel.frames.pop(0)[0], 0xFF)
        self.assertEqual(self.channel.dump(0x20000110, 8), struct.pack("<d", 3.5))
        self.assertEqual(self.master.get_status()[0], 0xFF)

    def test_write_dropped_on_timeout(self):
        self.master.connect()
        self.master.set_mta(0x20000100)
        self.master.command([0xF0, 4] + list(struct.pack("<f", 9.0)), expect_response=False)
        self.channel.line("EXPIRE")
        frame = self.channel.frames.pop(0)
        self.assertEqual(frame[:2], bytes([0xFE, ERR_CMD_BUSY]))
        self.channel.line("TICK")  # the writer task comes too late: nothing happens
        self.assertEqual(self.channel.frames, [])
        self.assertEqual(self.channel.dump(0x20000100, 4), struct.pack("<f", 0.125))


class A2lRoundTripTest(unittest.TestCase):
    """The master reads what a2lgen writes (names, addresses, types, conversion, IF_DATA)."""

    def test_round_trip(self):
        xcp = a2lgen.XcpCan("CAN1", 0x80001234, 0x80001235, 250000, "test")
        params = PARAMETERS + [a2lgen.Parameter("Test.CalPrm.Temp", "degC", "SINT16", 0x20000200, 1, (-400, 1), 10)]
        a2l = A2l(a2lgen.render(a2lgen.Image(SIGNALS, params, True), xcp, "rt"))
        self.assertEqual((a2l.master_id, a2l.slave_id, a2l.bitrate), (0x80001234, 0x80001235, 250000))
        temp = a2l.get("Test.CalPrm.Temp")
        self.assertEqual((temp.address, temp.datatype, temp.unit), (0x20000200, "SWORD", "degC"))
        # physical 25.0 degC = (-400 + stored) / 10 -> stored 650
        self.assertEqual(a2l.encode(temp, [25.0]), struct.pack("<h", 650))
        self.assertEqual(a2l.decode(temp, struct.pack("<h", 650)), [25.0])
        self.assertEqual(a2l.get("Test.CalPrm.Table").count, 4)


if __name__ == "__main__":
    try:
        unittest.main()
    finally:
        if _BUILD is not None:
            shutil.rmtree(_BUILD.parent, ignore_errors=True)
