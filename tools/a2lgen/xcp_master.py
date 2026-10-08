"""Small XCP-on-CAN master for the A2L of tools/a2lgen (TOOL-01): connect, measure by polling, calibrate.

Enough for the board check and the host test of ecu/xcp; not a replacement for a calibration tool. It knows
the A2L that a2lgen writes (MEASUREMENT, CHARACTERISTIC VALUE/VAL_BLK, COMPU_METHOD RAT_FUNC, IF_DATA XCP_ON_CAN;
RTE-26: CURVE/MAP - the values, own breakpoints as <Name>_X/<Name>_Y -, AXIS_PTS, value tables as stored numbers,
LOWER/UPPER_LIMIT, EPK), not A2L in general. Byte order of the data from the A2L (MOD_COMMON BYTE_ORDER), of the protocol from CONNECT.

TOOL-199: a DOWNLOAD longer than one CAN frame uses block mode (DOWNLOAD_NEXT) if CONNECT/GET_COMM_MODE_INFO of
the slave reports it and it fits one block - applied by the ECU as one change, see XcpMaster.download(). Larger
still, or without block mode, XcpMaster.write() falls back to several DOWNLOAD commands of their own SET_MTA; the
ECU applies each of those right away, so the object can be observed half old/half new between them until the last
one lands. write() and write_value() return whether it was the atomic case; write_is_atomic() says so in advance,
before anything is sent.

Usage (python-can, e.g. Vector CANcaseXL channel 0):
  xcp_master.py --a2l app.ecu.a2l [--interface vector --channel 0] list
  xcp_master.py --a2l app.ecu.a2l read DemoSwc.CalPrm.FilterAlpha
  xcp_master.py --a2l app.ecu.a2l write DemoSwc.CalPrm.FilterAlpha 1.0
  xcp_master.py --a2l app.ecu.a2l epk [--try-write]
  xcp_master.py --a2l app.ecu.a2l watch DemoSwc.MotorNtc_mV.MotorNtc_mV DemoSwc.MotorNtcFiltered_mV.MotorNtcFiltered_mV
"""

from __future__ import annotations

import argparse
import re
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path

CONNECT, DISCONNECT, GET_STATUS, SYNCH, GET_COMM_MODE_INFO = 0xFF, 0xFE, 0xFD, 0xFC, 0xFB
SET_MTA, UPLOAD, SHORT_UPLOAD, DOWNLOAD, DOWNLOAD_NEXT = 0xF6, 0xF5, 0xF4, 0xF0, 0xEF

ERRORS = {
    0x00: "ERR_CMD_SYNCH", 0x10: "ERR_CMD_BUSY", 0x20: "ERR_CMD_UNKNOWN", 0x21: "ERR_CMD_SYNTAX",
    0x22: "ERR_OUT_OF_RANGE", 0x23: "ERR_WRITE_PROTECTED", 0x24: "ERR_ACCESS_DENIED", 0x25: "ERR_ACCESS_LOCKED",
    0x26: "ERR_PAGE_NOT_VALID", 0x27: "ERR_MODE_NOT_VALID", 0x28: "ERR_SEGMENT_NOT_VALID", 0x29: "ERR_SEQUENCE",
    0x31: "ERR_GENERIC",
}

# A2L datatype -> struct code, bytes
DATATYPES = {
    "UBYTE": ("B", 1), "SBYTE": ("b", 1), "UWORD": ("H", 2), "SWORD": ("h", 2), "ULONG": ("I", 4),
    "SLONG": ("i", 4), "FLOAT32_IEEE": ("f", 4), "FLOAT64_IEEE": ("d", 8),
}


class XcpError(Exception):
    def __init__(self, code: int, text: str = ""):
        self.code = code
        super().__init__(text or f"{ERRORS.get(code, 'error')} (0x{code:02X})")


class XcpTimeout(Exception):
    pass


# ---- transports -------------------------------------------------------------------------------------------------

class CanTransport:
    """python-can bus; command on master_id, response on slave_id (bit 31: extended), DLC 8."""

    def __init__(self, bus, master_id: int, slave_id: int):
        import can
        self._can = can
        self.bus = bus
        self.master_id = master_id
        self.slave_id = slave_id

    def send(self, data: bytes) -> None:
        payload = bytes(data) + bytes(8 - len(data))
        ext = bool(self.master_id & 0x80000000)
        self.bus.send(self._can.Message(arbitration_id=self.master_id & 0x1FFFFFFF, is_extended_id=ext,
                                        data=payload))

    def receive(self, timeout: float) -> bytes | None:
        end = time.monotonic() + timeout
        want_ext = bool(self.slave_id & 0x80000000)
        while True:
            left = end - time.monotonic()
            if left <= 0:
                return None
            msg = self.bus.recv(left)
            if msg is None:
                return None
            if msg.arbitration_id == (self.slave_id & 0x1FFFFFFF) and msg.is_extended_id == want_ext:
                return bytes(msg.data)


# ---- protocol -----------------------------------------------------------------------------------------------------

class XcpMaster:
    def __init__(self, transport, timeout: float = 0.2):
        self.transport = transport
        self.timeout = timeout
        self.little_endian = True
        self.max_cto = 8
        self.max_bs = 1

    def command(self, data: bytes | list[int], expect_response: bool = True) -> bytes:
        self.transport.send(bytes(data))
        if not expect_response:
            return b""
        response = self.transport.receive(self.timeout)
        if response is None:
            raise XcpTimeout(f"no response to command 0x{bytes(data)[0]:02X}")
        if response[0] == 0xFE:
            raise XcpError(response[1])
        if response[0] != 0xFF:
            raise XcpError(-1, f"unexpected packet {response.hex(' ')}")
        return response

    def _dword(self, value: int) -> bytes:
        return struct.pack("<I" if self.little_endian else ">I", value)

    def connect(self) -> dict:
        r = self.command([CONNECT, 0])
        self.little_endian = (r[2] & 0x01) == 0
        self.max_cto = r[3]
        info = {"resource": r[1], "comm_mode_basic": r[2], "max_cto": r[3],
                "max_dto": struct.unpack("<H" if self.little_endian else ">H", r[4:6])[0],
                "protocol_layer": r[6], "transport_layer": r[7]}
        if r[2] & 0x80:  # GET_COMM_MODE_INFO available
            m = self.command([GET_COMM_MODE_INFO])
            if m[2] & 0x01:
                self.max_bs = m[4]
            info["max_bs"] = m[4]
        return info

    def disconnect(self) -> None:
        self.command([DISCONNECT])

    def get_status(self) -> bytes:
        return self.command([GET_STATUS])

    def synch(self) -> int:
        try:
            self.command([SYNCH])
        except XcpError as exc:
            return exc.code
        return -1

    def set_mta(self, address: int) -> None:
        self.command(bytes([SET_MTA, 0, 0, 0]) + self._dword(address))

    def short_upload(self, address: int, n: int) -> bytes:
        return self.command(bytes([SHORT_UPLOAD, n, 0, 0]) + self._dword(address))[1:1 + n]

    def upload(self, n: int) -> bytes:
        return self.command([UPLOAD, n])[1:1 + n]

    def read(self, address: int, n: int) -> bytes:
        data = b""
        chunk = self.max_cto - 1
        while len(data) < n:
            part = min(chunk, n - len(data))
            data += self.short_upload(address + len(data), part)
        return data

    def download(self, data: bytes) -> bool:
        """One DOWNLOAD at the current MTA: a single packet, or - if it reported block mode at CONNECT
        (GET_COMM_MODE_INFO) and data fits one block - DOWNLOAD followed by DOWNLOAD_NEXT. Either way the slave
        applies it as one change. Returns False without sending anything if data is longer than one block (no
        block mode, or more than max_bs packets): the caller falls back to several DOWNLOAD commands of its own
        (write(), TOOL-199) - not the same thing, see there."""
        per = self.max_cto - 2
        if len(data) <= per:
            self.command(bytes([DOWNLOAD, len(data)]) + data)
            return True
        if len(data) > per * self.max_bs:
            return False
        self.command(bytes([DOWNLOAD, len(data)]) + data[:per], expect_response=False)
        pos = per
        while pos < len(data):
            last = pos + per >= len(data)
            self.command(bytes([DOWNLOAD_NEXT, len(data) - pos]) + data[pos:pos + per], expect_response=last)
            pos += per
        return True

    def write(self, address: int, data: bytes, element_size: int = 1) -> bool:
        """Writes data at address (TOOL-199). True: one DOWNLOAD, or one DOWNLOAD_NEXT block the slave reported it
        can hold (see download()) - the ECU applies it as one change. False: it did not fit one block (no block
        mode reported by CONNECT/GET_COMM_MODE_INFO, or simply too long), so it went out as several DOWNLOAD
        commands of their own SET_MTA, each applied by the ECU right away - an object read on the bus between two
        of them can be seen half old, half new (not atomic from the application's point of view, unlike a single
        DOWNLOAD/block). element_size: bytes of one element of data (from the A2L datatype); every DOWNLOAD of the
        fallback stays a whole number of elements, because a slave checks that per command (ERR_OUT_OF_RANGE
        otherwise, ecu/xcp XcpSlave::checkWrite). See XcpMaster.write_is_atomic() to know this in advance, before
        anything is sent (the Studio tells the operator, studio.core.xcplink.WritePlan.detail)."""
        self.set_mta(address)
        if self.download(data):
            return True
        per = self.max_cto - 2
        if element_size > per:
            raise XcpError(0x22, f"{element_size} bytes per element do not fit one CAN frame "
                                 f"({per} bytes payload): this slave cannot write it without block mode")
        group = (per * max(self.max_bs, 1) // element_size) * element_size
        if group == 0:
            raise XcpError(0x22, f"{element_size} bytes per element need at least that much block capacity "
                                 f"(GET_COMM_MODE_INFO), this slave offers {per * max(self.max_bs, 1)}")
        for pos in range(0, len(data), group):
            if pos:                                          # the MTA is already there for pos == 0
                self.set_mta(address + pos)
            self.download(data[pos:pos + group])
        return False

    def write_is_atomic(self, n: int) -> bool:
        """Whether write() of n bytes would reach the ECU as one DOWNLOAD/block (True) or as several DOWNLOAD
        commands of their own (False, TOOL-199) - to warn before anything is sent, not only after."""
        per = self.max_cto - 2
        return n <= per or n <= per * max(self.max_bs, 1)


# ---- A2L (as written by a2lgen) -------------------------------------------------------------------------------------

@dataclass
class A2lObject:
    name: str
    kind: str        # MEASUREMENT or CHARACTERISTIC
    address: int
    datatype: str
    count: int
    coeffs: tuple[float, ...]  # RAT_FUNC a..f: stored = (b * phys + c) / f (a = d = e = 0)
    unit: str = ""
    limits: tuple | None = None  # RTE-26: physical (lower, upper) of a calibration object


class A2l:
    def __init__(self, text: str):
        self.little_endian = "BYTE_ORDER MSB_FIRST" not in text
        self.master_id = self._int(r"CAN_ID_MASTER\s+(\S+)", text)
        self.slave_id = self._int(r"CAN_ID_SLAVE\s+(\S+)", text)
        m = re.search(r"BAUDRATE\s+(\d+)", text)
        self.bitrate = int(m.group(1)) if m else None
        epk = re.search(r"^\s*EPK \"([^\"]*)\"", text, re.M)
        self.epk = epk.group(1) if epk else ""                    # RTE-26
        self.epk_address = self._int(r"ADDR_EPK\s+(\S+)", text)   # TOOL-151
        methods = {}
        # one block per COMPU_METHOD: RAT_FUNC with COEFFS, TAB_VERB (value table: the stored number, RTE-26)
        for m in re.finditer(r"/begin COMPU_METHOD (\S+)(.*?)/end COMPU_METHOD", text, re.S):
            coeffs = re.search(r"COEFFS ([^\n]+)\n", m.group(2))
            methods[m.group(1)] = tuple(float(v) for v in coeffs.group(1).split()) if coeffs else (0, 1, 0, 0, 0, 1)
        layouts = {}
        for m in re.finditer(r"/begin RECORD_LAYOUT (\S+)(.*?)/end RECORD_LAYOUT", text, re.S):
            layouts[m.group(1)] = re.findall(r"(FNC_VALUES|AXIS_PTS_X|AXIS_PTS_Y) \d+ (\S+)", m.group(2))
        self.objects: dict[str, A2lObject] = {}
        for m in re.finditer(r"/begin CHARACTERISTIC (\S+) \"[^\"]*\"\s+(VALUE|VAL_BLK|CURVE|MAP) (\S+) (\S+) \S+ "
                             r"(\S+) (\S+) (\S+)(.*?)/end CHARACTERISTIC", text, re.S):
            name, kind, address, layout, method = m.group(1), m.group(2), int(m.group(3), 0), m.group(4), m.group(5)
            body = re.sub(r"/begin AXIS_DESCR.*?/end AXIS_DESCR", "", m.group(8), flags=re.S)
            unit = re.search(r"PHYS_UNIT \"([^\"]*)\"", body)
            limits = (float(m.group(6)), float(m.group(7)))
            if kind in ("VALUE", "VAL_BLK"):
                count = re.search(r"NUMBER (\d+)", body)
                self.objects[name] = A2lObject(name, "CHARACTERISTIC", address, layouts[layout][0][1],
                                               int(count.group(1)) if count else 1, methods[method],
                                               unit.group(1) if unit else "", limits)
                continue
            # RTE-26: CURVE/MAP - the axes in order X, Y; own breakpoints (STD_AXIS) lie in the record in front
            axes = re.findall(r"/begin AXIS_DESCR (\S+) \S+ (\S+) (\d+) (\S+) (\S+)(.*?)/end AXIS_DESCR",
                              m.group(8), re.S)
            parts = dict(layouts[layout])
            offset, count = 0, 1
            for letter, (attribute, axis_method, points, low, high, rest) in zip("XY", axes):
                count *= int(points)
                if attribute != "STD_AXIS":
                    continue
                dtype = parts[f"AXIS_PTS_{letter}"]
                size = DATATYPES[dtype][1]
                offset = -(-offset // size) * size
                axis_unit = re.search(r"PHYS_UNIT \"([^\"]*)\"", rest)
                self.objects[f"{name}_{letter}"] = A2lObject(f"{name}_{letter}", "CHARACTERISTIC", address + offset,
                                                             dtype, int(points), methods[axis_method],
                                                             axis_unit.group(1) if axis_unit else "",
                                                             (float(low), float(high)))
                offset += size * int(points)
            dtype = parts["FNC_VALUES"]
            offset = -(-offset // DATATYPES[dtype][1]) * DATATYPES[dtype][1]
            self.objects[name] = A2lObject(name, "CHARACTERISTIC", address + offset, dtype, count, methods[method],
                                           unit.group(1) if unit else "", limits)
        for m in re.finditer(r"/begin AXIS_PTS (\S+) \"[^\"]*\"\s+(\S+) \S+ (\S+) \S+ (\S+) (\d+) (\S+) (\S+)"
                             r"(.*?)/end AXIS_PTS", text, re.S):
            unit = re.search(r"PHYS_UNIT \"([^\"]*)\"", m.group(8))
            self.objects[m.group(1)] = A2lObject(m.group(1), "CHARACTERISTIC", int(m.group(2), 0),
                                                 dict(layouts[m.group(3)])["AXIS_PTS_X"], int(m.group(5)),
                                                 methods[m.group(4)], unit.group(1) if unit else "",
                                                 (float(m.group(6)), float(m.group(7))))
        for m in re.finditer(r"/begin MEASUREMENT (\S+) \"[^\"]*\"\s+(\S+) (\S+) .*?ECU_ADDRESS (\S+)(.*?)"
                             r"/end MEASUREMENT", text, re.S):
            dim = re.search(r"MATRIX_DIM (\d+)", m.group(5))      # RTE-25: an array port
            unit = re.search(r"PHYS_UNIT \"([^\"]*)\"", m.group(5))  # RTE-29: unit of a fixed point signal
            self.objects[m.group(1)] = A2lObject(m.group(1), "MEASUREMENT", int(m.group(4), 0), m.group(2),
                                                 int(dim.group(1)) if dim else 1, methods[m.group(3)],
                                                 unit.group(1) if unit else "")

    @staticmethod
    def _int(pattern: str, text: str) -> int | None:
        m = re.search(pattern, text)
        return int(m.group(1), 0) if m else None

    def get(self, name: str) -> A2lObject:
        try:
            return self.objects[name]
        except KeyError:
            raise KeyError(f"{name} is not in the A2L (list shows the names)") from None

    def decode(self, obj: A2lObject, data: bytes) -> list[float]:
        code, size = DATATYPES[obj.datatype]
        order = "<" if self.little_endian else ">"
        raws = struct.unpack(order + code * obj.count, data[:size * obj.count])
        _a, b, c, _d, _e, f = obj.coeffs
        return [(f * raw - c) / b for raw in raws]

    def encode(self, obj: A2lObject, values: list[float]) -> bytes:
        code, _size = DATATYPES[obj.datatype]
        order = "<" if self.little_endian else ">"
        _a, b, c, _d, _e, f = obj.coeffs
        raws = []
        for v in values:
            raw = (b * v + c) / f
            raws.append(raw if code in "fd" else int(round(raw)))
        return struct.pack(order + code * len(raws), *raws)

    def size(self, obj: A2lObject) -> int:
        return DATATYPES[obj.datatype][1] * obj.count


def read_value(master: XcpMaster, a2l: A2l, name: str) -> list[float]:
    obj = a2l.get(name)
    return a2l.decode(obj, master.read(obj.address, a2l.size(obj)))


def write_value(master: XcpMaster, a2l: A2l, name: str, values: list[float]) -> bool:
    """Returns write()'s atomic flag (TOOL-199): False if the slave had no usable block mode for this object's
    size and it went out as several DOWNLOAD commands (not applied as one by the ECU)."""
    obj = a2l.get(name)
    if obj.kind != "CHARACTERISTIC":
        raise KeyError(f"{name} is a MEASUREMENT (read only)")
    if len(values) != obj.count:
        raise ValueError(f"{name} has {obj.count} element(s), {len(values)} given")
    element_size = DATATYPES[obj.datatype][1]
    return master.write(obj.address, a2l.encode(obj, values), element_size)


def read_epk(master: XcpMaster, a2l: A2l) -> str:
    """TOOL-151: the EPK text on the ECU at ADDR_EPK of the A2L (as many bytes as the EPK of the A2L has)."""
    if not a2l.epk or a2l.epk_address is None:
        raise KeyError("the A2L has no EPK/ADDR_EPK (MOD_PAR)")
    return master.read(a2l.epk_address, len(a2l.epk)).decode("ascii", errors="replace")


def _format(values: list[float]) -> str:
    return " ".join(f"{v:g}" for v in values)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--a2l", type=Path, required=True)
    ap.add_argument("--interface", default="vector")
    ap.add_argument("--channel", default="0")
    ap.add_argument("--bitrate", type=int, help="default: BAUDRATE of the A2L")
    ap.add_argument("--app-name", default=None, help="Vector application name (python-can)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("connect")
    r = sub.add_parser("read")
    r.add_argument("names", nargs="+")
    w = sub.add_parser("write")
    w.add_argument("name")
    w.add_argument("values", nargs="+", type=float)
    ep = sub.add_parser("epk", help="TOOL-151: compare the EPK on the ECU with the A2L (exit code 1 if different)")
    ep.add_argument("--try-write", action="store_true",
                    help="also DOWNLOAD the same text to ADDR_EPK; the ECU has to refuse it (ERR_WRITE_PROTECTED)")
    wa = sub.add_parser("watch")
    wa.add_argument("names", nargs="+")
    wa.add_argument("--period", type=float, default=0.1)
    wa.add_argument("--count", type=int, default=0, help="0: until Ctrl+C")
    args = ap.parse_args(argv)

    a2l = A2l(args.a2l.read_text(encoding="utf-8"))
    if args.cmd == "list":
        for obj in a2l.objects.values():
            print(f"{obj.kind:14} 0x{obj.address:08X} {obj.datatype:12} x{obj.count} {obj.name} {obj.unit}")
        return 0
    import can
    kwargs = {"app_name": args.app_name} if args.interface == "vector" else {}
    channel = int(args.channel) if str(args.channel).isdigit() else args.channel
    with can.Bus(interface=args.interface, channel=channel, bitrate=args.bitrate or a2l.bitrate, **kwargs) as bus:
        master = XcpMaster(CanTransport(bus, a2l.master_id, a2l.slave_id))
        info = master.connect()
        try:
            if args.cmd == "connect":
                print(f"connected: {info}")
            elif args.cmd == "read":
                for name in args.names:
                    print(f"{name} = {_format(read_value(master, a2l, name))}")
            elif args.cmd == "write":
                atomic = write_value(master, a2l, args.name, args.values)
                note = "" if atomic else (" (TOOL-199: no block mode for this size, sent as several DOWNLOAD - "
                                          "could be observed half-written on the bus until the last one landed)")
                print(f"{args.name} = {_format(read_value(master, a2l, args.name))} (read back){note}")
            elif args.cmd == "epk":
                ecu = read_epk(master, a2l)
                print(f"EPK A2L: {a2l.epk!r} at 0x{a2l.epk_address:08X}")
                print(f"EPK ECU: {ecu!r} -> {'match' if ecu == a2l.epk else 'MISMATCH'}")
                result = 0 if ecu == a2l.epk else 1
                if args.try_write:
                    # the same bytes, so even a wrongly accepted write would not change the text
                    try:
                        master.write(a2l.epk_address, a2l.epk.encode("ascii")[:master.max_cto - 2])
                        print("DOWNLOAD to ADDR_EPK: ACCEPTED (wrong, must be refused)")
                        result = 1
                    except XcpError as exc:
                        print(f"DOWNLOAD to ADDR_EPK: refused ({exc})")
                        result = result if exc.code == 0x23 else 1
                return result
            elif args.cmd == "watch":
                n = 0
                while args.count == 0 or n < args.count:
                    t = time.monotonic()
                    line = "  ".join(f"{name.split('.')[-1]}={_format(read_value(master, a2l, name))}"
                                     for name in args.names)
                    print(f"{time.strftime('%H:%M:%S')} {line}", flush=True)
                    n += 1
                    time.sleep(max(0.0, args.period - (time.monotonic() - t)))
        except KeyboardInterrupt:
            pass
        finally:
            master.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
