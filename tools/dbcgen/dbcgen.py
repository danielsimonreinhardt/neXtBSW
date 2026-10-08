"""Generates C pack/unpack code for CAN messages from a DBC file.

Input:
  DBC file (parsed with cantools), optionally a node: only messages that node sends or receives
Output (C99, no heap, no floating point in pack/unpack):
  CanDb.h, CanDb.c: per message the ID, length, cycle and timeout time, a struct with the raw signal values,
  Init/Pack/Unpack functions and per signal inline conversions between raw and physical values
  (integer arithmetic with rounding and clamping to the DBC value range, double as alternative).

Supported: classic CAN (up to 8 bytes), standard and extended IDs, Intel and Motorola byte order, signed
and unsigned integer signals up to 32 bit, factor and offset. Not supported: multiplexing, IEEE float
signals, CAN FD. Unsupported messages can stay in the DBC as long as they are not generated.

DBC attributes used:
  GenMsgCycleTime (BO_)    cycle time in ms, 0 = not cyclic
  GenMsgTimeoutTime (BO_)  receive timeout in ms (project convention), default 3 x GenMsgCycleTime
  GenSigStartValue (SG_)   initial raw value, default 0
  GenMsgSendType (BO_)     how the message is sent (COM-38, Message.send_mode): cyclic, event (on change) or both;
                           not written to CanDb.h, tools/rtegen reads it
  GenMsgDelayTime (BO_)    minimum gap in ms between two sends of an event or mixed message (COM-38), default 0
"""

import argparse
import math
import re
import sys
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from nxconfig import genlicense  # noqa: E402  (PLAT-120)
EXTENDED_ID_FLAG = 0x80000000  # same bit as OpenBSW CanId::EXTENDED_QUALIFIER_BIT
MAX_SIGNAL_BITS = 32
MAX_DENOMINATOR = 10**9
INT64_LIMIT = 2**62  # headroom for the intermediate products of the integer conversion
C_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
C_KEYWORDS = {"auto", "break", "case", "char", "const", "continue", "default", "do", "double", "else", "enum",
              "extern", "float", "for", "goto", "if", "inline", "int", "long", "register", "restrict", "return",
              "short", "signed", "sizeof", "static", "struct", "switch", "typedef", "union", "unsigned", "void",
              "volatile", "while"}


class DbcError(Exception):
    pass


# COM-38: how the RTE sends a message, from GenMsgSendType (compared without case). The names are the common ones
# of Vector tools and AUTOSAR ComTxMode; "event" means: sent when the packed signal values change.
SEND_CYCLIC, SEND_EVENT, SEND_MIXED = "cyclic", "event", "mixed"
SEND_MODES = {
    "cyclic": SEND_CYCLIC,
    "spontaneous": SEND_EVENT, "spontaneouswithdelay": SEND_EVENT, "onchange": SEND_EVENT,
    "triggered": SEND_EVENT, "event": SEND_EVENT,
    "cyclicandspontaneous": SEND_MIXED, "cyclicandspontaneouswithdelay": SEND_MIXED,
    "cyclicandonchange": SEND_MIXED, "cyclicandtriggered": SEND_MIXED, "mixed": SEND_MIXED,
}
# no send type given: the message is cyclic (the behaviour before COM-38, GenMsgCycleTime decides)
NO_SEND_TYPE = {"", "nomsgsendtype", "none", "notused", "nosendtype"}
# the names offered for a new GenMsgSendType value (Studio, messages of tools/rtegen)
SEND_TYPE_CHOICES = ("Cyclic", "Spontaneous", "CyclicAndSpontaneous")


def send_mode(send_type: str | None) -> str | None:
    """COM-38: SEND_CYCLIC, SEND_EVENT or SEND_MIXED for a GenMsgSendType text; None if the RTE does not support
    it (e.g. IfActive, OnWrite: sending on every write without a change is not implemented)."""
    key = (send_type or "").strip().lower()
    return SEND_CYCLIC if key in NO_SEND_TYPE else SEND_MODES.get(key)


def to_fraction(value) -> Fraction:
    """Exact fraction of a DBC number: 0.1 is 1/10, not the nearest double."""
    if isinstance(value, Fraction):
        return value
    if isinstance(value, int):
        return Fraction(value)
    if not math.isfinite(value):
        raise ValueError(f"{value} is not finite")
    return Fraction(repr(float(value)))


def c_int64(value: int) -> str:
    if value == -(2**63):
        return "(INT64_MIN)"
    return f"INT64_C({value})"


def c_double(value: Fraction) -> str:
    text = repr(float(value))
    return text if ("e" in text or "." in text or "inf" in text) else text + ".0"


def raw_ctype(length: int, signed: bool) -> str:
    bits = 8 if length <= 8 else 16 if length <= 16 else 32
    return f"{'int' if signed else 'uint'}{bits}_t"


def round_half_away(value: Fraction) -> int:
    return int(math.floor(value + Fraction(1, 2))) if value >= 0 else -int(math.floor(-value + Fraction(1, 2)))


@dataclass
class Signal:
    name: str
    start: int
    length: int
    motorola: bool
    signed: bool
    factor: Fraction
    offset: Fraction
    minimum: Fraction          # physical range: DBC [min|max] or the full raw range
    maximum: Fraction
    initial_raw: int
    unit: str
    receivers: list[str]
    raw_lo: int = 0            # raw values that decode into [minimum, maximum]
    raw_hi: int = 0

    @property
    def ctype(self) -> str:
        return raw_ctype(self.length, self.signed)

    @property
    def bit_min(self) -> int:
        return -(1 << (self.length - 1)) if self.signed else 0

    @property
    def bit_max(self) -> int:
        return (1 << (self.length - 1)) - 1 if self.signed else (1 << self.length) - 1

    @property
    def integer(self) -> bool:
        """Factor and offset are integers, so every physical value is an integer."""
        return self.factor.denominator == 1 and self.offset.denominator == 1

    def to_physical(self, raw: int) -> Fraction:
        return min(max(raw, self.raw_lo), self.raw_hi) * self.factor + self.offset

    def to_raw(self, physical) -> int:
        """Reference model of the generated FromInt/FromDouble: round half away from zero, then clamp."""
        raw = round_half_away((to_fraction(physical) - self.offset) / self.factor)
        return min(max(raw, self.raw_lo), self.raw_hi)

    # values used by the templates
    @property
    def c(self):
        f, o = self.factor, self.offset
        lo_phys = math.floor(self.minimum) - 1
        hi_phys = math.ceil(self.maximum) + 1
        den = o.denominator * f.numerator
        num_scale = f.denominator
        sign = -1 if den < 0 else 1
        return {
            "raw_lo": c_int64(self.raw_lo), "raw_hi": c_int64(self.raw_hi),
            "factor_int": c_int64(int(f)) if f.denominator == 1 else None,
            "offset_int": c_int64(int(o)) if o.denominator == 1 else None,
            "phys_lo": c_int64(lo_phys), "phys_hi": c_int64(hi_phys),
            # raw = round((value * od - on) * fd / (od * fn)), denominator normalised to > 0
            "od": c_int64(o.denominator), "on": c_int64(o.numerator), "fd": c_int64(sign * num_scale),
            "den": c_int64(sign * den),
            "factor": c_double(f), "offset": c_double(o),
            "raw_lo_d": c_double(Fraction(self.raw_lo)), "raw_hi_d": c_double(Fraction(self.raw_hi)),
        }


@dataclass
class Message:
    name: str
    frame_id: int
    extended: bool
    length: int
    cycle_ms: int
    timeout_ms: int
    senders: list[str]
    signals: list[Signal] = field(default_factory=list)
    comment: str | None = None
    problem: str | None = None   # reason why the message cannot be generated
    send_type: str = ""          # COM-38: GenMsgSendType as written (ENUM resolved, default applied), "" without
    delay_ms: int = 0            # COM-38: GenMsgDelayTime, minimum gap between two sends

    @property
    def send_mode(self) -> str | None:
        """COM-38: SEND_CYCLIC, SEND_EVENT or SEND_MIXED; None for a send type the RTE does not support
        (e.g. IfActive, OnWrite). Without GenMsgSendType the message is cyclic."""
        return send_mode(self.send_type)

    @property
    def can_id(self) -> int:
        return self.frame_id | (EXTENDED_ID_FLAG if self.extended else 0)

    @property
    def receivers(self) -> set[str]:
        return {r for s in self.signals for r in s.receivers}

    def signal(self, name: str) -> Signal:
        for s in self.signals:
            if s.name == name:
                return s
        raise DbcError(f"message {self.name} has no signal {name}")


@dataclass
class Database:
    path: Path
    nodes: list[str]
    messages: list[Message]

    def message(self, name: str) -> Message:
        for m in self.messages:
            if m.name == name:
                return m
        raise DbcError(f"{self.path.name}: no message {name}")

    def for_node(self, node: str) -> list[Message]:
        if node not in self.nodes:
            raise DbcError(f"{self.path.name}: node {node} is not defined (BU_: {' '.join(self.nodes)})")
        return [m for m in self.messages if node in m.senders or node in m.receivers]


def check_name(what: str, name: str):
    if not C_IDENT.match(name) or name in C_KEYWORDS:
        raise DbcError(f"{what} {name} is not a valid C identifier")


def attribute(obj, name, default):
    dbc = getattr(obj, "dbc", None)
    if dbc is None or name not in dbc.attributes:
        return default
    return dbc.attributes[name].value


def attribute_or_default(obj, db, name, default):
    """Value of an attribute of the object, else the BA_DEF_DEF_ default of the database, else `default`."""
    value = attribute(obj, name, None)
    if value is not None:
        return value
    definitions = getattr(db.dbc, "attribute_definitions", None) if db.dbc is not None else None
    definition = definitions.get(name) if definitions else None
    if definition is not None and definition.default_value is not None:
        return definition.default_value
    return default


def convert_signal(msg_name: str, s) -> Signal:
    where = f"signal {msg_name}.{s.name}"
    check_name("signal", s.name)
    if s.is_float:
        raise DbcError(f"{where}: IEEE float signals are not supported")
    if s.length > MAX_SIGNAL_BITS:
        raise DbcError(f"{where}: {s.length} bit, at most {MAX_SIGNAL_BITS} bit are supported")
    try:
        factor, offset = to_fraction(s.scale), to_fraction(s.offset)
    except ValueError as e:
        raise DbcError(f"{where}: {e}") from None
    if factor == 0:
        raise DbcError(f"{where}: factor 0")
    for name, value in (("factor", factor), ("offset", offset)):
        if value.denominator > MAX_DENOMINATOR:
            raise DbcError(f"{where}: {name} {float(value)} has too many decimal places")
    sig = Signal(s.name, s.start, s.length, s.byte_order == "big_endian", s.is_signed, factor, offset,
                 Fraction(0), Fraction(0), 0, s.unit or "", list(s.receivers))
    ends = sorted((sig.bit_min * factor + offset, sig.bit_max * factor + offset))
    if s.minimum is None and s.maximum is None or (s.minimum == 0 and s.maximum == 0):
        sig.minimum, sig.maximum = ends
    else:
        sig.minimum = to_fraction(s.minimum) if s.minimum is not None else ends[0]
        sig.maximum = to_fraction(s.maximum) if s.maximum is not None else ends[1]
        if sig.minimum > sig.maximum:
            raise DbcError(f"{where}: minimum {float(sig.minimum)} > maximum {float(sig.maximum)}")
    # raw values whose physical value lies inside [minimum, maximum]
    a, b = (sig.minimum - offset) / factor, (sig.maximum - offset) / factor
    lo, hi = min(a, b), max(a, b)
    sig.raw_lo, sig.raw_hi = math.ceil(lo), math.floor(hi)
    kind = f"{s.length} bit {'signed' if s.is_signed else 'unsigned'}"
    if sig.raw_lo < sig.bit_min or sig.raw_hi > sig.bit_max:
        raise DbcError(f"{where}: range [{float(sig.minimum):g}|{float(sig.maximum):g}] {sig.unit} does not fit "
                       f"{kind} with factor {float(factor):g} and offset {float(offset):g} "
                       f"(encodable: {float(ends[0]):g}..{float(ends[1]):g})")
    if sig.raw_lo > sig.raw_hi:
        raise DbcError(f"{where}: range [{float(sig.minimum):g}|{float(sig.maximum):g}] contains no encodable value")
    initial = s.raw_initial if s.raw_initial is not None else 0
    if initial != int(initial) or not sig.bit_min <= int(initial) <= sig.bit_max:
        raise DbcError(f"{where}: GenSigStartValue {initial} is no {kind} raw value")
    sig.initial_raw = int(initial)
    # the integer conversion multiplies the clamped physical value with od * fd
    bound = (max(abs(math.floor(sig.minimum)), abs(math.ceil(sig.maximum))) + 1) * offset.denominator
    if (bound + abs(offset.numerator)) * factor.denominator >= INT64_LIMIT \
            or abs(offset.denominator * factor.numerator) >= INT64_LIMIT \
            or (max(abs(sig.raw_lo), abs(sig.raw_hi)) * abs(factor.numerator) + abs(offset)) >= INT64_LIMIT:
        raise DbcError(f"{where}: factor/offset too large for 64 bit integer conversion")
    return sig


def load(path: Path) -> Database:
    import cantools  # imported late so that --help works without it

    try:
        db = cantools.database.load_file(str(path), database_format="dbc", strict=True)
    except FileNotFoundError:
        raise DbcError(f"{path}: file not found") from None
    except Exception as e:  # cantools raises various parse errors
        raise DbcError(f"{path.name}: {e}") from None
    messages = []
    names = set()
    ids = set()
    for m in db.messages:
        check_name("message", m.name)
        if m.name in names:
            raise DbcError(f"{path.name}: message name {m.name} used twice")
        names.add(m.name)
        key = (m.frame_id, m.is_extended_frame)
        if key in ids:
            raise DbcError(f"{path.name}: message {m.name}: CAN ID 0x{m.frame_id:X} used twice")
        ids.add(key)
        cycle = int(m.cycle_time or 0)
        msg = Message(m.name, m.frame_id, m.is_extended_frame, m.length, cycle, 0, list(m.senders),
                      comment=m.comment if isinstance(m.comment, str) else None)
        timeout = attribute(m, "GenMsgTimeoutTime", 0)
        msg.timeout_ms = int(timeout) if timeout else 3 * cycle
        if cycle < 0 or msg.timeout_ms < 0:
            raise DbcError(f"message {m.name}: negative cycle or timeout time")
        msg.send_type = str(m.send_type or "")
        delay = attribute_or_default(m, db, "GenMsgDelayTime", 0)
        try:
            msg.delay_ms = int(delay or 0)
        except (TypeError, ValueError):
            raise DbcError(f"message {m.name}: GenMsgDelayTime {delay!r} is no number") from None
        if msg.delay_ms < 0:
            raise DbcError(f"message {m.name}: negative GenMsgDelayTime")
        if m.is_fd or m.length > 8:
            msg.problem = f"message {m.name}: CAN FD / length {m.length} is not supported (classic CAN, 8 bytes)"
        elif m.is_multiplexed():
            msg.problem = f"message {m.name}: multiplexed messages are not supported"
        else:
            try:
                msg.signals = [convert_signal(m.name, s) for s in m.signals]
            except DbcError as e:
                msg.problem = str(e)
        messages.append(msg)
    return Database(path, [n.name for n in db.nodes], messages)


def render(messages: list[Message], source: str, prefix: str = "CanDb") -> dict[str, str]:
    for m in messages:
        if m.problem:
            raise DbcError(m.problem)
    env = Environment(loader=FileSystemLoader(HERE / "templates"), undefined=StrictUndefined,
                      keep_trailing_newline=True, trim_blocks=True, lstrip_blocks=True)
    signals = [s for m in messages for s in m.signals]
    ctx = {
        "source": source,
        "prefix": prefix,
        "messages": messages,
        "use_intel": any(not s.motorola for s in signals),
        "use_motorola": any(s.motorola for s in signals),
        "use_signed": any(s.signed and s.length < 32 for s in signals),
    }
    return genlicense.stamp_all({f"{prefix}.h": env.get_template("CanDb.h.j2").render(ctx),
                                 f"{prefix}.c": env.get_template("CanDb.c.j2").render(ctx)})


def write_files(out_dir: Path, rendered: dict[str, str]):
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, content in rendered.items():
        target = out_dir / name
        if not target.exists() or target.read_text(encoding="utf-8") != content:
            target.write_text(content, encoding="utf-8", newline="\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dbc", type=Path, help="DBC file")
    parser.add_argument("out", type=Path, help="output directory")
    parser.add_argument("--node", help="only messages sent or received by this node")
    parser.add_argument("--prefix", default="CanDb", help="file and symbol prefix (default CanDb)")
    args = parser.parse_args()
    try:
        db = load(args.dbc)
        messages = db.for_node(args.node) if args.node else db.messages
        write_files(args.out, render(messages, args.dbc.name, args.prefix))
    except (DbcError, OSError) as e:
        print(f"dbcgen: error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
