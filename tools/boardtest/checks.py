"""The board tests: console echo, system time drift, CAN probe, qualifier phases, wake frame (COM-40).

Every test takes its transports (console, python-can bus), a clock and a sleep function from the caller, so the
host tests run the same code against recorded answers and a virtual CAN bus in a fraction of the time. Values of
the ECU come from project.ProjectSetup. Each test returns a Result: the lines to print and whether it passed.

Rule of all tests: read and parse **once before** anything long starts. A format the parser does not know stops
the test within seconds (FormatError) instead of producing "0 of 200 answers" after the whole run.
"""

import itertools
import threading
import time
from dataclasses import dataclass, field

import can

from console import Console, FormatError, qualifier, task_cycles
from project import MessageSetup, ProjectSetup, SetupError

# qualifier values of the RTE (tools/rtegen QLF_VALUES, Rte_Qlf.h), read from the generator itself
QLF = None


def qlf_values() -> dict[str, int]:
    global QLF
    if QLF is None:
        import rtegen

        QLF = dict(rtegen.QLF)
    return QLF


@dataclass
class Result:
    name: str
    passed: bool = True
    lines: list = field(default_factory=list)
    values: dict = field(default_factory=dict)

    def say(self, text: str) -> None:
        self.lines.append(text)

    def fail(self, text: str) -> None:
        self.passed = False
        self.lines.append(f"FAIL: {text}")


# ------------------------------------------------------------------------------------------------ console echo

def echo(console: Console, setup: ProjectSetup, count: int = 200, command: str = "rte runnables",
         timeout_s: float = 1.2, format_probe: int = 3) -> Result:
    """`count` console commands, each answer must parse completely (IO-17: lost characters in the UART RX path).

    The first `format_probe` answers decide about the format: if none of them parses, the firmware answers in a
    format this parser does not know - that stops the test at once (FormatError) instead of counting `count`
    losses."""
    result = Result("echo")
    failures = []
    format_errors = []
    for index in range(count):
        answer = console.command(command, timeout_s)
        try:
            task_cycles(answer.text, setup.tasks)
        except FormatError as e:
            failures.append((index, answer.text.strip().splitlines()[:2]))
            if index < format_probe:
                format_errors.append(e)
                if len(format_errors) == format_probe:
                    raise FormatError(f"the first {format_probe} answers to '{command}' have an unknown format: "
                                      f"{format_errors[0]}") from None
    for index, head in failures[:10]:
        result.say(f"  answer {index} unusable: {head}")
    result.values = {"commands": count, "failed": len(failures)}
    result.say(f"{count} commands '{command}', {len(failures)} answers unusable")
    if failures:
        result.fail(f"{len(failures)} of {count} answers lost or garbled")
    return result


# ------------------------------------------------------------------------------------------------ drift

def drift(console: Console, setup: ProjectSetup, seconds: float, sleep=time.sleep, task: str | None = None,
          command: str = "rte runnables", limit_ppm: float | None = None) -> Result:
    """System time of the ECU against the PC: cycle counter of an RTE task times its period, over `seconds`.

    Time stamp of a reading is the arrival of the task's line on the PC (not the end of the answer). Resolution
    about one task period plus the console latency: at 10 ms and 10 min roughly 20 ppm (ARCH-14)."""
    result = Result("drift")

    def reading():
        answer = console.command(command)
        tasks = task_cycles(answer.text, setup.tasks)
        name = task or (setup.tasks[0] if setup.tasks else next(iter(tasks)))
        if name not in tasks:
            raise FormatError(f"task {name} not in the answer (found: {', '.join(tasks)})")
        stamp = answer.time_of(rf"^\s*task {name} |\"name\":\"{name}\"")
        if stamp is None:
            raise FormatError(f"no arrival time for the line of task {name}")
        return stamp, tasks[name]

    t0, first = reading()                       # fails here, within seconds, if the format is unknown
    result.say(f"start: task {first.name}, every {first.period_ms} ms, cycles {first.cycles}")
    sleep(seconds)
    t1, second = reading()
    if second.cycles < first.cycles:
        result.fail(f"cycle counter went back from {first.cycles} to {second.cycles} (reset during the test?)")
        return result
    pc = t1 - t0
    board = (second.cycles - first.cycles) * first.period_ms / 1000.0
    ppm = 1e6 * (board - pc) / pc if pc > 0 else 0.0
    resolution = 1e6 * (first.period_ms / 1000.0) / pc if pc > 0 else float("inf")
    result.values = {"pc_s": pc, "board_s": board, "ppm": ppm, "resolution_ppm": resolution}
    result.say(f"PC {pc:.3f} s, board {board:.3f} s, drift {ppm:+.0f} ppm (resolution about {resolution:.0f} ppm)")
    if limit_ppm is not None and abs(ppm) > limit_ppm + resolution:
        result.fail(f"drift {ppm:+.0f} ppm above the limit of {limit_ppm:g} ppm")
    return result


# ------------------------------------------------------------------------------------------------ CAN probe

class RecordingBus(can.BusABC):
    """Writes every frame the test sends or receives to a python-can logger (.asc: the CAN capture format of the
    host tests), without taking frames away from the test as a Notifier would.

    `owns_logger` (default True): shutdown() also stops the logger. False for a bus that is only one of several
    channel opens sharing the same logger (COM-40 wake probe, cli.can_opener): stopping the logger between attempts
    would truncate the trace, so the caller stops it once, itself, after the last attempt."""

    def __init__(self, bus: can.BusABC, logger, owns_logger: bool = True, **kwargs):
        super().__init__(channel=getattr(bus, "channel_info", "recording"), **kwargs)
        self.inner, self.logger, self.owns_logger = bus, logger, owns_logger
        self.lock = threading.Lock()

    def send(self, msg, timeout=None):
        self.inner.send(msg, timeout)
        stamped = can.Message(timestamp=time.time(), arbitration_id=msg.arbitration_id, data=msg.data,
                              is_extended_id=msg.is_extended_id, is_rx=False, channel=msg.channel)
        with self.lock:
            self.logger.on_message_received(stamped)

    def _recv_internal(self, timeout):
        msg = self.inner.recv(timeout)
        if msg is not None:
            with self.lock:
                self.logger.on_message_received(msg)
        return msg, False

    def shutdown(self):
        if self.owns_logger:
            with self.lock:
                self.logger.stop()
        self.inner.shutdown()
        super().shutdown()


class AdapterTime:
    """Host clock -> time base of the adapter's frame time stamps (TOOL-46).

    python-can stamps a frame with the adapter's clock (Vector: hardware time plus an offset to time.time() taken
    at open), the tests measure with time.monotonic - two clocks with different zero points. The offset between
    them is learnt from the frames themselves: host time at reception minus time stamp is transport delay plus
    offset, and its minimum over all frames is the offset plus the smallest delay. A frame the driver hands over
    late only makes its own difference larger, so it cannot spoil the estimate. With that, "stamped before the
    start of the measurement" is decided in the adapter's time, not by when recv() returned the frame. Frames
    without a time stamp (timestamp 0) teach nothing and are never judged."""

    def __init__(self):
        self.offset: float | None = None

    def learn(self, msg: can.Message, host: float) -> None:
        if msg.timestamp:
            delay = host - msg.timestamp
            self.offset = delay if self.offset is None else min(self.offset, delay)

    def of(self, host: float) -> float | None:
        """Adapter time that belongs to host time `host`, None before the first stamped frame."""
        return None if self.offset is None else host - self.offset

    def before(self, msg_timestamp: float, host: float) -> bool:
        """Whether a frame stamped `msg_timestamp` was on the bus before host time `host`."""
        start = self.of(host)
        return bool(msg_timestamp) and start is not None and msg_timestamp < start


def can_probe(bus: can.BusABC, setup: ProjectSetup, seconds: float = 3.0, tolerance: float = 0.2,
              clock=time.monotonic, settle_s: float = 0.3) -> Result:
    """Listens to the bus: the cyclic messages the ECU sends (DBC, node of the ECU description) with their cycle
    time, and a TesterPresent (3E 00) to the ECU's diagnostic address must be answered (7E 00).

    Before the window, `settle_s` seconds of listening whose frames are thrown away; and a frame whose time stamp
    lies before the start of the window is thrown away even if it arrives inside the window (TOOL-46, AdapterTime).
    Board finding 2026-09-16 and 2026-09-21 (VN1610): right after opening, the first frames come as a burst with
    0.24 ms spacing (captures 2026-09-16_canprobe.asc, 2026-09-19_canprobe.asc: 8 x 0x110 and 7 x 0x111 with
    consecutive, older E2E counters) - by all appearances frames the ECU had queued while no other node acknowledged,
    sent back to back the moment the adapter went on the bus. Emptying the queue with recv(0.0) did not catch them:
    they reached the tool only after the window had started, and counted they shortened the cycle (3 s: 80 ms
    instead of 100 ms and once FAIL, 15 s: 95 ms, about 8 frames too many)."""
    result = Result("canprobe")
    expected = {msg.frame_id: msg for msg in setup.tx.values() if msg.cycle_ms > 0}
    seen: dict[int, list[float]] = {}
    adapter = AdapterTime()
    settled = 0
    settle_end = clock() + settle_s
    while clock() < settle_end:
        msg = bus.recv(max(0.0, min(0.05, settle_end - clock())))
        if msg is not None:
            adapter.learn(msg, clock())
            settled += 1
    start = clock()
    end = start + seconds
    frames: list[tuple[can.Message, float]] = []
    while clock() < end:
        msg = bus.recv(0.05)
        if msg is None or msg.is_error_frame:
            continue
        host = clock()
        adapter.learn(msg, host)
        frames.append((msg, host))
    stale = 0
    for msg, host in frames:           # judged at the end, with everything the window taught about the offset
        if adapter.before(msg.timestamp, start):
            stale += 1
            continue
        seen.setdefault(msg.arbitration_id, []).append(msg.timestamp if msg.timestamp else host)
    result.values["ids"] = {f"0x{i:X}": len(v) for i, v in sorted(seen.items())}
    result.values["settle_frames"] = settled
    result.values["stale_frames"] = stale
    result.say(f"received in {seconds:g} s: " + (", ".join(f"0x{i:X} x{len(v)}" for i, v in sorted(seen.items()))
                                                  or "nothing"))
    if settled or stale:
        result.say(f"  discarded: {settled} frame(s) in the {settle_s:g} s before the window, {stale} delivered in "
                   "the window but stamped before it")
    for frame_id, msg in sorted(expected.items()):
        stamps = seen.get(frame_id, [])
        if len(stamps) < 2:
            result.fail(f"{msg.name} (0x{frame_id:X}, every {msg.cycle_ms} ms in the DBC) not received")
            continue
        cycle_ms = 1000.0 * (stamps[-1] - stamps[0]) / (len(stamps) - 1)
        result.say(f"  {msg.name} 0x{frame_id:X}: {len(stamps)} frames, cycle {cycle_ms:.1f} ms (DBC {msg.cycle_ms} ms)")
        if abs(cycle_ms - msg.cycle_ms) > tolerance * msg.cycle_ms:
            result.fail(f"{msg.name}: cycle {cycle_ms:.1f} ms instead of {msg.cycle_ms} ms")
    unknown = sorted(set(seen) - set(expected))
    if unknown:
        result.say("  not sent by the ECU according to the DBC: " + ", ".join(f"0x{i:X}" for i in unknown))
    if setup.diag is None:
        result.say("  no diagnostic addresses in the project: TesterPresent skipped")
        return result
    diag = setup.diag
    bus.send(can.Message(arbitration_id=diag.request_id, data=bytes([0x02, 0x3E, 0x00]) + bytes([diag.padding] * 5),
                         is_extended_id=False))
    answer = None
    end = clock() + 1.0
    while clock() < end and answer is None:
        msg = bus.recv(0.05)
        if msg is not None and msg.arbitration_id == diag.response_id and bytes(msg.data[:3]) == b"\x02\x7E\x00":
            answer = bytes(msg.data)
    result.say(f"  UDS 3E 00 to 0x{diag.request_id:X}: " + (answer.hex(" ") if answer else "no answer"))
    if answer is None:
        result.fail(f"no TesterPresent answer on 0x{diag.response_id:X}")
    return result


# ------------------------------------------------------------------------------------------------ wake (COM-40)

def send_wake_frames(open_bus, frame: can.Message, count: int = 1, gap_s: float = 0.2, sleep=time.sleep) -> Result:
    """Sends `frame` up to `count` times, opening a fresh CAN bus with `open_bus()` for every attempt and shutting
    it down again right afterwards - never one bus kept open across attempts.

    Board finding COM-39 (2026-09-28): the Vector default bit timing did not wake the matching frame, the project's
    (81.25 % sample point, SJW 2, 16 tq) woke it at once - the partial-networking decoder is stricter about the bit
    timing than FlexCAN. `open_bus` (cli.can_opener) already carries the project's timing, so this function never
    hard-codes it. Second finding of the same appointment: a frame nobody acknowledges - exactly what a wake probe
    sends before the ECU is awake - stays queued in the VN1610's own transmit mailbox and blocks every later send if
    the channel stays open; reopening it here (`open_bus()`/`shutdown()` per attempt) clears that mailbox."""
    result = Result("wake")
    result.say(f"frame 0x{frame.arbitration_id:X}" + (" extended" if frame.is_extended_id else "")
               + f" dlc {len(frame.data)}" + (f" data {bytes(frame.data).hex(' ')}" if len(frame.data) else ""))
    for attempt in range(1, count + 1):
        bus = open_bus()
        try:
            bus.send(frame)
        finally:
            bus.shutdown()
        result.say(f"  attempt {attempt}/{count}: sent, channel reopened")
        if attempt < count:
            sleep(gap_s)
    result.values["attempts"] = count
    return result


# ------------------------------------------------------------------------------------------------ qualifier

@dataclass
class Phase:
    name: str
    kind: str                  # valid, badcrc, stuck, off
    expected: tuple            # qualifier names, any of them passes
    ok_phase: bool             # the observed signal follows a released value (QLF_OK)


PHASES = (
    Phase("1 before the first frame", "off", ("DEBOUNCE", "ERROR_TIMEOUT"), False),
    Phase("2 valid stream", "valid", ("OK",), True),
    Phase("3 permanently wrong CRC", "badcrc", ("ERROR_CRC",), False),
    Phase("4 valid again", "valid", ("OK",), True),
    Phase("5 counter stuck (alive)", "stuck", ("ERROR_ALIVE",), False),
    Phase("6 valid again", "valid", ("OK",), True),
    Phase("7 sending off (timeout)", "off", ("ERROR_TIMEOUT",), False),
    Phase("8 healing", "valid", ("OK",), True),
)


class FrameSender(threading.Thread):
    """Sends the received message of the ECU in its DBC cycle: valid (E2E protected, counter running), wrong CRC,
    stuck counter or nothing."""

    def __init__(self, bus: can.BusABC, msg: MessageSetup, payload: bytes, cycle_s: float):
        super().__init__(daemon=True)
        self.bus, self.msg, self.payload, self.cycle_s = bus, msg, payload, cycle_s
        self.kind = "off"
        self.stop = threading.Event()
        self.sent = 0

    def frame(self, kind: str, counter: int) -> bytes | None:
        e2e = self.msg.e2e
        if kind == "valid":
            return e2e.protect(counter, self.payload)
        if kind == "stuck":
            return e2e.protect(0, self.payload)
        if kind == "badcrc":
            data = bytearray(e2e.protect(counter, self.payload))
            data[e2e.crc_offset // 8] ^= 0xFF          # never the right checksum
            return bytes(data)
        return None

    def run(self):
        counters = itertools.cycle(range(self.msg.e2e.counter_modulo))
        next_send = time.monotonic()
        while not self.stop.is_set():
            data = self.frame(self.kind, next(counters))
            if data is not None:
                self.bus.send(can.Message(arbitration_id=self.msg.frame_id, is_extended_id=self.msg.extended,
                                          data=data))
                self.sent += 1
            next_send += self.cycle_s
            self.stop.wait(max(0.0, next_send - time.monotonic()))


class Observer(threading.Thread):
    """Records one signal of a message the ECU sends (e.g. an LED released by the qualifier).

    Each value keeps the frame's time stamp, so take(after=...) can leave out frames that were on the bus before a
    phase window began but reached the tool only inside it (TOOL-46, same burst as in can_probe: right after
    opening the bus it lands in the window of phase 1, where a gated signal must stay 0)."""

    def __init__(self, bus: can.BusABC, msg: MessageSetup, signal: str, clock=time.monotonic):
        super().__init__(daemon=True)
        self.bus, self.msg, self.signal, self.clock = bus, msg, signal, clock
        self.lock = threading.Lock()
        self.values: list[tuple[float, int]] = []
        self.adapter = AdapterTime()
        self.stop = threading.Event()

    def run(self):
        while not self.stop.is_set():
            frame = self.bus.recv(0.02)
            if frame is None:
                continue
            with self.lock:
                self.adapter.learn(frame, self.clock())
            if frame.arbitration_id == self.msg.frame_id and len(frame.data) >= self.msg.length:
                value = self.msg.raw(bytes(frame.data))[self.signal]
                with self.lock:
                    self.values.append((frame.timestamp, int(value)))

    def take(self, after: float | None = None) -> list[int]:
        """Values since the last take(); with `after` (host clock) only those of frames stamped from then on."""
        with self.lock:
            values, self.values = self.values, []
            return [value for stamp, value in values
                    if after is None or not self.adapter.before(stamp, after)]


def qualifier_phases(console: Console, bus: can.BusABC, setup: ProjectSetup, message: str | None = None,
                     payload: bytes | None = None, observe: str | None = None, expect_gated: bool = False,
                     window_s: float = 4.0, time_scale: float = 1.0, sleep=time.sleep, phases=PHASES,
                     observe_bus: can.BusABC | None = None) -> Result:
    """Qualifier of a received, E2E protected message through all states (COM-03/COM-13 board test).

    Settling time of each phase from the configuration: debounce_ms and timeout_ms of the qualifier plus two
    message cycles. `observe` (Message.Signal of a sent message) is recorded per phase; with `expect_gated` the
    signal must change in every QLF_OK phase and stay 0 otherwise (DemoSwc: Led2 blinks only with QLF_OK)."""
    result = Result("qualifier")
    qlf = qlf_values()
    names = {value: name for name, value in qlf.items()}
    if message is None:
        candidates = setup.qualified_rx()
        if not candidates:
            raise SetupError(f"{setup.project}: no received message with an E2E qualifier (rte.yaml can.qualifiers)")
        message = candidates[0]
    msg = setup.message(message)
    if msg.e2e is None or message not in setup.rx:
        raise SetupError(f"{message}: not a received message with E2E protection in the ECU description")
    cycle_s = msg.cycle_ms / 1000.0 * time_scale
    settle_s = (max(msg.debounce_ms, msg.timeout_ms) / 1000.0) * time_scale + 2 * cycle_s
    window = window_s * time_scale

    first = console.command("rte signals")
    qualifier(first.text, message)                  # unknown format: stop before anything is sent

    observer = None
    if observe:
        obs_message, _, obs_signal = observe.partition(".")
        obs = setup.message(obs_message)
        if obs_signal not in obs.signals:
            raise SetupError(f"--observe {observe}: {obs_message} has no signal {obs_signal}")
        observer = Observer(observe_bus or bus, obs, obs_signal)
        observer.start()
    sender = FrameSender(bus, msg, payload if payload is not None else msg.initial, cycle_s)
    sender.start()
    result.say(f"{message} (0x{msg.frame_id:X}) every {msg.cycle_ms} ms, E2E {msg.e2e.profile} data id "
               f"0x{msg.e2e.data_id:X}, debounce {msg.debounce_ms} ms, timeout {msg.timeout_ms} ms"
               + (f", observing {observe}" if observe else ""))
    try:
        for index, phase in enumerate(phases):
            sender.kind = phase.kind
            sleep(settle_s if index else 0.0)
            window_start = time.monotonic()
            if observer:
                observer.take()
            sleep(window)
            values = observer.take(after=window_start) if observer else []
            value = qualifier(console.command("rte signals").text, message)
            ok = any(value == qlf[name] for name in phase.expected)
            text = f"{phase.name:28s} Qlf={value:>3d} ({names.get(value, '?')})"
            if observer:
                edges = sum(1 for a, b in zip(values, values[1:]) if a != b)
                ones = sum(1 for v in values if v)
                text += f"  {observe}: {len(values)} frames, =1 {ones}, changes {edges}"
                if expect_gated and (edges == 0 if phase.ok_phase else ones != 0):
                    ok = False
                result.values.setdefault("observed", []).append((len(values), ones, edges))
            result.values.setdefault("qlf", []).append(value)
            result.say(text)
            if not ok:
                result.fail(f"{phase.name}: expected {' or '.join(phase.expected)}, qualifier {value}"
                            + (" / observed signal not gated" if observer and expect_gated else ""))
    finally:
        sender.stop.set()
        sender.join()
        if observer:
            observer.stop.set()
            observer.join()
    return result
