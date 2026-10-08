"""Host tests of the board tests against recorded console answers, a CAN trace and the firmware's format strings.

No hardware: the console is a replay of captures/, the CAN bus a python-can virtual bus with a simulated ECU.
What must turn red here instead of after nine minutes at the board:
  * an answer format the parsers do not know (RTE-02: "ticks" -> "cycles"), in the capture and in the firmware
    source (RteConsole.h printf strings, rtegen signal links);
  * a test that would run on although its first answers are unusable (echo, drift, qualifier fail fast);
  * wrong frames (E2E CRC, counter), wrong expectations per qualifier phase, a missing TesterPresent answer.

    .venv/Scripts/python -m unittest tools/boardtest/test_boardtest.py
"""

import re
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import can

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import checks  # noqa: E402
import console as con  # noqa: E402
import project as proj  # noqa: E402

ROOT = HERE.parents[1]
CAPTURES = HERE / "captures" / "evalboard_s32k144_demo"
STATUS = CAPTURES / "2026-09-16_status.console.txt"
MEASURE = CAPTURES / "2026-09-16_measure.console.txt"
SIGNALS = CAPTURES / "2026-09-30_board" / "tool15_01_rte_signals.console.txt"
CAN_TRACE = CAPTURES / "2026-09-30_board" / "tool15_02_canprobe.asc"
FIRMWARE = ROOT / "ecu" / "application" / "src" / "systems" / "RteConsole.h"
# PLAT-62: the own full test profile (demo component, demo CAN matrix, diagnostic description) instead of the eval
# board project, which stays private (PLAT-46 F10). The recordings of the eval board under captures/ are private as
# well (tools/distcheck/distribution.yaml): the replays of them skip where they are missing (exported open tree).
DEMO = ROOT / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full" / "project.yaml"
recordings = unittest.skipUnless(CAPTURES.is_dir(),
                                 "private recordings of the eval board not in this tree (exported open tree, PLAT-62)")

_SETUP = {}


def demo_setup(with_diag=False) -> proj.ProjectSetup:
    key = with_diag
    if key not in _SETUP:
        _SETUP[key] = proj.load(DEMO, with_diag=with_diag)
    return _SETUP[key]


def ticks_instead_of_cycles(text: str) -> str:
    """The format of 'rte runnables' before RTE-02, which silently broke the coordinator's scripts."""
    return text.replace("cycles", "ticks")


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += max(seconds, 0.0005)


class ReplayTransport:
    """Console of a recorded board: answers each command with a recorded (or generated) answer, CR LF like the
    firmware, split into two chunks so that lines arrive in pieces."""

    def __init__(self, answers):
        self.answers = answers          # command -> str | callable(index) -> str
        self.writes = []
        self.pending = []

    def write(self, data: bytes):
        command = data.decode("ascii").strip()
        self.writes.append(command)
        answer = self.answers.get(command, f"{command}\n unknown command\n")
        if callable(answer):
            answer = answer(len(self.writes) - 1)
        raw = answer.replace("\n", "\r\n").encode()
        self.pending += [raw[: len(raw) // 2], raw[len(raw) // 2:]]

    def read(self) -> bytes:
        return self.pending.pop(0) if self.pending else b""

    def discard_input(self):
        pass

    def close(self):
        pass


def console_for(answers, clock=None):
    clock = clock or FakeClock()
    transport = ReplayTransport(answers)
    return con.Console(transport, clock=clock.clock, sleep=clock.sleep), transport, clock


# ------------------------------------------------------------------------------------------------ parsers


@recordings
class CaptureParserTest(unittest.TestCase):
    def test_runnables_and_json_of_the_recorded_board(self):
        expected = {("rte runnables", STATUS): [35112], ("rte runnables", MEASURE): [37006],
                    ("rte json", MEASURE): [36848]}
        for (command, path), cycles in expected.items():
            with self.subTest(command=command, capture=path.name):
                answers = con.answers_of(path, command)
                self.assertEqual(len(answers), len(cycles))
                for answer, count in zip(answers, cycles):
                    tasks = con.task_cycles(answer, ["swc"])
                    self.assertEqual((tasks["swc"].period_ms, tasks["swc"].cycles), (10, count))

    def test_ticks_instead_of_cycles_is_a_format_error(self):
        """Board test 2026-09-16: RTE-02 renamed the counter and two 9-minute runs were lost."""
        for path in (STATUS, MEASURE):
            answer = con.answers_of(path, "rte runnables")[0]
            with self.subTest(path.name), self.assertRaises(con.FormatError) as ctx:
                con.task_cycles(ticks_instead_of_cycles(answer), ["swc"])
            self.assertIn("ticks", str(ctx.exception))

    def test_missing_task_of_the_ecu_description(self):
        answer = con.answers_of(MEASURE, "rte runnables")[0]
        with self.assertRaises(con.FormatError) as ctx:
            con.task_cycles(answer, ["swc", "motor"])
        self.assertIn("motor", str(ctx.exception))

    def test_qualifier_of_the_signals_answer(self):
        """Board 2026-09-30 (board-0930-remote): one "rte signals" answer of the recorded session."""
        answers = con.answers_of(SIGNALS, "rte signals")
        self.assertEqual(len(answers), 1)
        self.assertEqual(con.qualifier(answers[0], "PC_Command"), 8)
        values = con.signals(answers[0])
        self.assertEqual(values["PC_Command.Setpoint_mV"].number, 2500.0)
        self.assertEqual(values["PC_Command.Offset_mV"].number, -100)
        self.assertIn("can.tx.EVB_Status.Led2", values["DemoSwc.Led2.Led2"].links)
        with self.assertRaises(con.FormatError):
            con.qualifier(answers[0].replace("can.qlf.", "can.quality."), "PC_Command")
        with self.assertRaises(con.FormatError):
            con.qualifier(answers[0], "EVB_Status")

    def test_scaled_signal_line(self):
        """RTE-29: a fixed point signal reads '<physical>[ <unit>] [<stored>]' (writeSignalLine, RteConsole.h)."""
        values = con.signals(" TypeSwc.Temp.Temp = 21.0 degC [610]  <-> can.tx.Msg.Temp\r\n"
                             " TypeSink.In.Value = -2048.0000 [-32768]\r\n"
                             " Msg.Qlf = 8  <-> can.qlf.Msg\r\n")
        temp = values["TypeSwc.Temp.Temp"]
        self.assertEqual((temp.number, temp.unit, temp.stored, temp.links), (21.0, "degC", 610, ("can.tx.Msg.Temp",)))
        sink = values["TypeSink.In.Value"]
        self.assertEqual((sink.number, sink.unit, sink.stored), (-2048.0, "", -32768))
        self.assertEqual((values["Msg.Qlf"].number, values["Msg.Qlf"].stored), (8, None))

    def test_capture_format(self):
        pairs = con.load_capture(STATUS)
        self.assertEqual([c for c, _ in pairs][:3], ["ecu status", "sbc status", "safeadc stats"])
        self.assertFalse(any(a.startswith("#") for _, a in pairs))


class FirmwareFormatTest(unittest.TestCase):
    """The parsers against the format strings of the firmware source: a renamed field in RteConsole.h turns this
    test red in the same commit, before any board run.

    Since SYS-13 the lines of "rte runnables|json|signals" are printed by the write* templates of RteConsole.h
    (RteSystem.cpp only adds the JSON header and end marker). The format strings are taken per function, not per
    case block, so moving a call between files or reordering the switch does not matter - renaming or removing a
    write* function does, and is reported with the missing name."""

    @classmethod
    def setUpClass(cls):
        if not FIRMWARE.is_file():
            raise unittest.SkipTest(f"{FIRMWARE} missing")
        cls.source = FIRMWARE.read_text(encoding="utf-8")

    # --- source access

    @staticmethod
    def function_body(source: str, name: str) -> str:
        """Body of the C++ function `name` (text between its braces), braces in string literals and comments
        skipped. Raises AssertionError if the function is not there."""
        match = re.search(rf"\b{re.escape(name)}\s*\([^)]*\)\s*{{", source)
        if not match:
            raise AssertionError(f"function {name} not found in {FIRMWARE.name}")
        depth, i = 1, match.end()
        while depth:
            if i >= len(source):
                raise AssertionError(f"function {name} in {FIRMWARE.name} has no closing brace")
            if source.startswith("//", i):
                i = source.index("\n", i)
            elif source.startswith("/*", i):
                i = source.index("*/", i) + 1
            elif source[i] in "\"'":
                quote, i = source[i], i + 1
                while source[i] != quote:
                    i += 2 if source[i] == "\\" else 1
            elif source[i] == "{":
                depth += 1
            elif source[i] == "}":
                depth -= 1
            i += 1
        return source[match.end():i - 1]

    @classmethod
    def formats(cls, source: str, function: str) -> list[str]:
        """Format strings (adjacent literals joined) of the out.printf calls in `function`, in source order."""
        calls = re.findall(r"out\.printf\(\s*((?:\"(?:[^\"\\]|\\.)*\"\s*)+)", cls.function_body(source, function))
        formats = ["".join(re.findall(r"\"((?:[^\"\\]|\\.)*)\"", call)) for call in calls]
        if not formats:
            raise AssertionError(f"no out.printf in {function}")
        return formats

    @staticmethod
    def unescape(fmt: str) -> str:
        return fmt.replace("\\r\\n", "\n").replace('\\"', '"')

    @classmethod
    def render(cls, fmt: str, strings, numbers) -> str:
        """printf of the firmware with sample arguments: %s from `strings`, %u/%d from `numbers`, in order."""
        strings, numbers = iter(strings), iter(numbers)
        text = re.sub(r"%[sud]", lambda m: str(next(strings) if m.group() == "%s" else next(numbers)), fmt)
        return cls.unescape(text)

    @classmethod
    def pattern(cls, fmt: str) -> str:
        """Regular expression of the text a format prints (without the line end)."""
        parts = re.split(r"(%[sud])", cls.unescape(fmt).rstrip("\n"))
        conv = {"%s": r"\S+", "%u": r"\d+", "%d": r"-?\d+"}
        return "".join(conv.get(p, re.escape(p)) for p in parts)

    def link_separator(self, source: str) -> str:
        """The string writeSignalLine prints in front of the BSW links (argument of its second printf)."""
        separators = re.findall(r"\?\s*\"((?:[^\"\\]|\\.)+)\"\s*:", self.function_body(source, "writeSignalLine"))
        # RTE-29: the scaled line chooses the blank in front of the unit the same way - not a link separator
        separators = [s for s in separators if s.strip()]
        self.assertEqual(len(separators), 1, "writeSignalLine: expected one '? \"<separator>\" :' argument")
        return separators[0]

    # --- sample lines of the firmware

    def runnables_line(self, source: str) -> str:
        task = self.formats(source, "writeTaskLine")
        self.assertEqual(len(task), 1)
        return self.render(task[0], ["swc"], [3, 10, 12345, 0, 68])

    def json_line(self, source: str) -> str:
        # the task object is printed in three calls: name/context/period/cycles, runtimes, cycle distances
        parts = self.formats(source, "writeJsonTask")
        numbers = iter(range(3, 100))
        return "".join(self.render(p, ["swc"], numbers) for p in parts)

    def signal_formats(self, source: str) -> tuple:
        """writeSignalLine: head '%s = ', tail '%s%s\\r\\n' and, since RTE-29, between them the two calls of a scaled
        signal '<physical>[ <unit>] [' ... ']' (empty when the firmware has no scaling)."""
        formats = self.formats(source, "writeSignalLine")
        head, scaled, tail = formats[0], formats[1:-1], formats[-1]
        if scaled:
            self.assertEqual([self.unescape(f) for f in scaled], ["%s%s%s [", "]"],
                             "writeSignalLine: unexpected format of a scaled signal (RTE-29)")
        return head, scaled, tail

    def signal_line(self, source: str, label: str, value: int, link: str, physical: tuple = ()) -> str:
        """One line of "rte signals"; `physical` = (text, unit) prints it as a scaled signal (RTE-29)."""
        head, scaled, tail = self.signal_formats(source)
        value_fmt = self.formats(source, "writeValue")[0]              # UNSIGNED
        value_text = self.render(value_fmt, [], [value])
        if physical:
            text, unit = physical
            value_text = (self.render(scaled[0], [text, " " if unit else "", unit], []) + value_text
                          + self.render(scaled[1], [], []))
        return (self.render(head, [label], []) + value_text
                + self.render(tail, [self.link_separator(source), link], []))

    def line_patterns(self, source: str) -> list:
        """Every line kind RteConsole.h prints for "rte runnables|json|signals"."""
        whole = [self.pattern(f) for f in self.formats(source, "writeTaskLine")]
        whole += [self.pattern(f) for f in self.formats(source, "writeRunnableLine")]
        whole += [self.pattern(f) for f in self.formats(source, "writeInitLine")]
        for function in ("writeJsonTask", "writeJsonRunnable", "writeJsonInit"):
            whole.append("".join(self.pattern(f) for f in self.formats(source, function)))
        head, scaled, tail = self.signal_formats(source)
        # tail "%s%s\r\n": separator and links, both empty without links
        self.assertEqual(self.unescape(tail), "%s%s\n", "writeSignalLine: unexpected end of the line")
        values = "|".join(self.pattern(f) for f in self.formats(source, "writeValue"))
        if scaled:
            # RTE-29: '<physical>[ <unit>] [<stored>]'
            values += rf"|-?\d+(?:\.\d+)?(?: [^\[\]]+)? \[(?:{values})\]"
        whole.append(self.pattern(head) + f"(?:{values})" + f"(?:{re.escape(self.link_separator(source))}.+)?")
        return [re.compile(p) for p in whole]

    # --- tests

    def test_rte_runnables_format_of_the_firmware(self):
        line = self.runnables_line(self.source)
        self.assertEqual(con.task_cycles(line, ["swc"])["swc"].cycles, 12345)

    def test_rte_json_format_of_the_firmware(self):
        line = self.json_line(self.source)
        self.assertIn('"t":"task"', line)
        tasks = con.task_cycles(line, ["swc"])
        self.assertEqual(tasks["swc"].period_ms, 4)            # 2nd number of the sample: context 3, period 4
        self.assertEqual(tasks["swc"].cycles, 5)

    def test_a_renamed_counter_in_the_firmware_is_caught(self):
        """Evidence for the two tests above: the source as it looked before RTE-02 does not parse."""
        old = self.source.replace("cycles %u", "ticks %u").replace('\\"cycles\\"', '\\"ticks\\"')
        self.assertNotEqual(old, self.source, "the replacement must hit the source, else this proves nothing")
        with self.assertRaises(con.FormatError):
            con.task_cycles(self.runnables_line(old), ["swc"])
        with self.assertRaises(con.FormatError):
            con.task_cycles(self.json_line(old), ["swc"])

    def test_rte_signals_format_of_the_firmware_and_generator(self):
        import rtegen

        # rtegen: label '<Msg>.Qlf', links joined by ', ', qualifier link 'can.qlf.<Msg>'
        self.assertEqual(rtegen.QLF_KIND, "can.qlf")
        buffers = {}
        key = rtegen.qlf_buffer(buffers, "PC_Command")
        buffer = buffers[key]
        line = self.signal_line(self.source, buffer.label, 4, buffer.connection)
        self.assertEqual(con.qualifier(line, "PC_Command"), 4)

    def test_scaled_signal_format_of_the_firmware(self):
        """RTE-29: the line of a fixed point signal as writeSignalLine prints it parses with value, unit and stored
        value in tools/boardtest. PLAT-114: the same line against the Studio's live values (studio.core.livedata) is
        checked on the Studio's side (tools/studio/tests/test_livedata.py) - an open test does not import the Studio,
        which is not distributed (F12)."""
        if len(self.formats(self.source, "writeSignalLine")) == 2:
            self.skipTest("firmware without the scaled signal line (RTE-29)")
        for unit in ("degC", ""):
            line = self.signal_line(self.source, "TypeSwc.Temp.Temp", 610, "can.tx.Msg.Temp", ("21.0", unit))
            value = con.signals(line)["TypeSwc.Temp.Temp"]
            self.assertEqual((value.number, value.unit, value.stored, value.links),
                             (21.0, unit, 610, ("can.tx.Msg.Temp",)), line)
            self.assertTrue(any(p.fullmatch(line.rstrip("\n")) for p in self.line_patterns(self.source)), line)

    @recordings
    def test_captures_match_the_firmware_formats(self):
        """Every line of the recorded answers to "rte runnables|json|signals" is one the firmware prints today:
        the captures under captures/ stay valid replays (SYS-13 moved the printf calls, not the text)."""
        patterns = self.line_patterns(self.source)
        checked, unknown = 0, {}
        for capture in sorted(CAPTURES.glob("*.console.txt")):
            for command, answer in con.load_capture(capture):
                if command not in ("rte runnables", "rte json", "rte signals"):
                    continue
                lines = con.console_dedent(answer).splitlines()
                # output starts after the echo of the command; log lines of other tasks may stand in front of it
                self.assertIn(command, lines, f"{capture.name}: answer to '{command}' without its echo")
                for line in lines[lines.index(command) + 1:]:
                    if con.ANSWER_END.match(line):
                        break
                    if not line or line.startswith(('{"t":"rte",', '{"t":"end",')):
                        continue                                # blank; header and end marker: RteSystem.cpp
                    if not any(p.fullmatch(line) for p in patterns):
                        unknown.setdefault(re.sub(r"\d+", "<n>", line), capture.name)
                    checked += 1
        self.assertGreater(checked, 100, "captures without rte answers - wrong directory?")
        self.assertFalse(unknown, f"{len(unknown)} line kind(s) of the captures are printed by no format of "
                                  f"{FIRMWARE.name}, e.g.:\n" + "\n".join(f"  {c}: {l}" for l, c in
                                                                        list(unknown.items())[:5]))


# ------------------------------------------------------------------------------------------------ echo, drift


@recordings
class EchoTest(unittest.TestCase):
    def test_200_recorded_answers_pass(self):
        answer = con.answers_of(MEASURE, "rte runnables")[0]
        console, transport, _ = console_for({"rte runnables": answer})
        result = checks.echo(console, demo_setup(), 200)
        self.assertTrue(result.passed, result.lines)
        self.assertEqual(result.values, {"commands": 200, "failed": 0})
        self.assertEqual(len(transport.writes), 200)

    def test_garbled_answers_are_counted(self):
        answer = con.answers_of(MEASURE, "rte runnables")[0]

        def sometimes_garbled(index):
            # IO-17: characters lost in the RX path, here the counter line loses "cycles"
            return answer.replace("cycles", "cycls") if index % 50 == 49 else answer
        console, _, _ = console_for({"rte runnables": sometimes_garbled})
        result = checks.echo(console, demo_setup(), 200)
        self.assertFalse(result.passed)
        self.assertEqual(result.values["failed"], 4)

    def test_unknown_format_stops_after_the_first_answers(self):
        answer = ticks_instead_of_cycles(con.answers_of(MEASURE, "rte runnables")[0])
        console, transport, _ = console_for({"rte runnables": answer})
        with self.assertRaises(con.FormatError) as ctx:
            checks.echo(console, demo_setup(), 200)
        self.assertEqual(len(transport.writes), 3, "must stop after the format probe, not after 200 commands")
        self.assertIn("ticks", str(ctx.exception))


@recordings
class DriftTest(unittest.TestCase):
    def board(self, clock, ppm, template):
        """Answers with the cycle counter of a board whose clock runs `ppm` fast."""
        start = clock.now

        def answer(_index):
            cycles = int((clock.now - start) * (1 + ppm * 1e-6) / 0.010) + 5000
            return re.sub(r"cycles \d+", f"cycles {cycles}", template)
        return answer

    def test_drift_of_a_simulated_board(self):
        template = con.answers_of(STATUS, "rte runnables")[0]
        for ppm in (0, 166, -166):
            with self.subTest(ppm=ppm):
                clock = FakeClock()
                console, _, _ = console_for({"rte runnables": self.board(clock, ppm, template)}, clock)
                result = checks.drift(console, demo_setup(), 600.0, sleep=clock.sleep, limit_ppm=100)
                self.assertAlmostEqual(result.values["ppm"], ppm, delta=result.values["resolution_ppm"] + 1)
                self.assertEqual(result.passed, abs(ppm) < 100, result.lines)

    def test_unknown_format_fails_before_waiting(self):
        template = ticks_instead_of_cycles(con.answers_of(STATUS, "rte runnables")[0])
        clock = FakeClock()
        console, _, _ = console_for({"rte runnables": template}, clock)
        with self.assertRaises(con.FormatError):
            checks.drift(console, demo_setup(), 540.0, sleep=clock.sleep)
        self.assertNotIn(540.0, clock.sleeps, "the long wait must not start with an unreadable counter")

    def test_json_answer_works_as_well(self):
        template = con.answers_of(MEASURE, "rte json")[0]
        clock = FakeClock()
        start = clock.now

        def answer(_index):
            return re.sub(r'"cycles":\d+', f'"cycles":{int((clock.now - start) / 0.010)}', template)
        console, _, _ = console_for({"rte json": answer}, clock)
        result = checks.drift(console, demo_setup(), 100.0, sleep=clock.sleep, command="rte json")
        self.assertLess(abs(result.values["ppm"]), result.values["resolution_ppm"] + 1)


# ------------------------------------------------------------------------------------------------ CAN


class TracePlayer:
    """The schedule of a recorded CAN trace played as the ECU: the frames the ECU sent, with their timing (and the
    recorded answer to a TesterPresent). No thread, no clock of its own: SimulatedBus drives it on simulated time
    (TOOL-202 - on real time the tests depended on how fast the machine scheduled a thread)."""

    def __init__(self, trace, answer_uds=True, drop_every=0, speed=1.0):
        frames = list(can.LogReader(str(trace)))
        self.sent = [m for m in frames if m.is_rx and not (m.data[:3] == b"\x02~\x00")]
        self.uds_answer = next(m for m in frames if m.is_rx and m.data[:3] == b"\x02~\x00")
        self.uds_request = next(m for m in frames if not m.is_rx)
        self.uds_latency = self.uds_answer.timestamp - self.uds_request.timestamp
        self.answer_uds, self.drop_every, self.speed = answer_uds, drop_every, speed
        # TOOL-202: the replay period comes from the recording, not from a constant (tool15_02 is 3.41 s long, the
        # old fixed 3.0 s made the second pass start before the first one was over: a burst of overdue frames at
        # the wrap). Start-up burst = frames without a gap > 1 ms from the first; it happened once, when the
        # adapter went on the bus, so later passes replay only the steady stream behind it. Period = length of the
        # steady stream plus one cycle of its last frame (cycle of the last id in the recording), so the stream
        # continues without a gap or a double frame.
        burst = 1
        while burst < len(self.sent) and self.sent[burst].timestamp - self.sent[burst - 1].timestamp < 0.001:
            burst += 1
        self.steady = self.sent[burst:] or self.sent
        last = self.steady[-1]
        earlier = [m.timestamp for m in self.steady[:-1] if m.arbitration_id == last.arbitration_id]
        cycle = last.timestamp - earlier[-1] if earlier else 0.0
        self.period = last.timestamp - self.steady[0].timestamp + cycle

    def frame_at(self, index):
        """The index-th frame of the replay and its time from the start, in seconds (speed 1)."""
        first = self.sent[0].timestamp
        if index < len(self.sent):
            msg, loops = self.sent[index], 0
        else:
            passes, position = divmod(index - len(self.sent), len(self.steady))
            msg, loops = self.steady[position], passes + 1
        return msg, (msg.timestamp - first) + loops * self.period


class SimClock:
    """Simulated time of the CAN tests: `now` is the monotonic clock of the probe, `WALL0 + now` the time stamps the
    adapter gives a frame. It only moves when SimulatedBus.recv() waits."""

    WALL0 = 1_780_000_000.0

    def __init__(self):
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def wall(self, t: float | None = None) -> float:
        return self.WALL0 + (self.t if t is None else t)


class SimulatedBus(can.BusABC):
    """The CAN bus of a probe: the ECU of a TracePlayer on simulated time. recv(timeout) delivers every frame due
    inside the timeout in order and moves the clock to it, or to the end of the timeout if none is due - so the
    probe sees exactly the timing of the recording, however busy the machine is (TOOL-202). A TesterPresent sent to
    the ECU is answered after the recorded delay. `recv` is overridden, not `_recv_internal`: python-can's own loop
    waits on the real clock."""

    def __init__(self, player: TracePlayer, clock: SimClock, **kwargs):
        super().__init__(channel="simulated", **kwargs)
        self.player, self.clock = player, clock
        self.index = 0
        self.per_id: dict[int, int] = {}
        self.answers: list[float] = []

    def send(self, msg, timeout=None):
        player = self.player
        if player.answer_uds and msg.arbitration_id == player.uds_request.arbitration_id                 and bytes(msg.data[:3]) == b"\x02>\x00":
            self.answers.append(self.clock.t + player.uds_latency)

    def _next_frame(self):
        """(due time, message) of the next frame that is sent at all (drop_every swallows some)."""
        player = self.player
        while True:
            msg, offset = player.frame_at(self.index)
            count = self.per_id[msg.arbitration_id] = self.per_id.get(msg.arbitration_id, -1) + 1
            dropped = player.drop_every and count % player.drop_every == 1                 and msg.arbitration_id == player.sent[0].arbitration_id
            if not dropped:
                return offset / player.speed, msg
            self.index += 1

    def recv(self, timeout=None):
        deadline = self.clock.t + (timeout if timeout is not None else 1e9)
        if not hasattr(self, "_pending"):
            self._pending = self._next_frame()
        due, msg = self._pending
        if self.answers and min(self.answers) <= min(due, deadline):
            when = min(self.answers)
            self.answers.remove(when)
            self.clock.t = max(self.clock.t, when)
            return can.Message(timestamp=self.clock.wall(when), arbitration_id=self.player.uds_answer.arbitration_id,
                               data=self.player.uds_answer.data, is_extended_id=False)
        if due <= deadline:
            self.clock.t = max(self.clock.t, due)
            self.index += 1
            self._pending = self._next_frame()
            return can.Message(timestamp=self.clock.wall(due), arbitration_id=msg.arbitration_id, data=msg.data,
                               is_extended_id=False)
        self.clock.t = deadline
        return None


@recordings
class TracePlayerScheduleTest(unittest.TestCase):
    """TOOL-202: the replay period follows the recording (tool15_02: 3.41 s, not the 3.0 s it was fixed to)."""

    def setUp(self):
        self.player = TracePlayer(CAN_TRACE)

    def stamps(self, frame_id, passes=3):
        count = len(self.player.sent) + (passes - 1) * len(self.player.steady)
        return [t for m, t in map(self.player.frame_at, range(count)) if m.arbitration_id == frame_id]

    def test_period_is_longer_than_the_recording_by_one_cycle(self):
        frames = list(can.LogReader(str(CAN_TRACE)))
        length = max(m.timestamp for m in frames)
        self.assertGreater(length, 3.0, "the recording is longer than the old fixed period - the reason for this test")
        self.assertAlmostEqual(self.player.period, 3.4995, delta=0.002)   # steady stream 0.0067 .. 3.4063 s + 100 ms
        self.assertGreater(self.player.period, length - self.player.steady[0].timestamp)

    def test_status_stream_stays_at_100_ms_over_the_wrap(self):
        stamps = self.stamps(0x110)
        steady = stamps[8:]                              # behind the start-up burst of 8 frames
        gaps = [b - a for a, b in zip(steady, steady[1:])]
        self.assertGreater(len(steady), 2 * 34)
        self.assertLess(max(gaps) - min(gaps), 0.005, gaps)
        self.assertAlmostEqual(sum(gaps) / len(gaps), 0.1, delta=0.002)

    def test_burst_is_replayed_once(self):
        stamps = self.stamps(0x110, passes=4)
        self.assertEqual(sum(1 for t in stamps if t < 0.004), 8, "the start-up burst of the recording")
        for t in stamps[8:]:
            self.assertEqual(sum(1 for u in stamps if t <= u < t + 0.005), 1, f"burst again at {t:.4f} s")


@recordings
class CanProbeTest(unittest.TestCase):
    def probe(self, **player):
        clock = SimClock()
        tester = SimulatedBus(TracePlayer(CAN_TRACE, **player), clock)
        return checks.can_probe(tester, demo_setup(with_diag=True), seconds=1.5, clock=clock.now)

    def test_recorded_trace_passes(self):
        result = self.probe()
        self.assertTrue(result.passed, result.lines)
        self.assertIn("0x110", result.values["ids"])
        self.assertTrue(any("7e 00" in line for line in result.lines), result.lines)

    def test_missing_testerpresent_answer_fails(self):
        result = self.probe(answer_uds=False)
        self.assertFalse(result.passed)
        self.assertIn("TesterPresent", "".join(result.lines))

    def test_wrong_cycle_time_fails(self):
        result = self.probe(drop_every=2)          # every second EVB_Status missing: 200 ms instead of 100 ms
        self.assertFalse(result.passed)
        self.assertIn("EVB_Status: cycle", "".join(result.lines))

    def test_trace_frames_are_e2e_protected_as_the_rte_does(self):
        """Board 2026-09-30 (board-0930-remote): E2E P11 of EVB_Status checked with the reference model. The
        recorded startup burst (TOOL-46: frames the ECU had queued, sent back to back the moment the adapter went
        on the bus, captures[...]/2026-09-30_board/tool15_02_canprobe.asc index 0-7) repeats the already-seen
        counter 2 once at the transition into the steady 100 ms stream (index 8: WRONGSEQUENCE) - every frame of
        the steady stream from there on is OK."""
        import e2e_ref

        status = demo_setup().tx["EVB_Status"]
        e2e = status.e2e
        cfg = e2e_ref.P11Config(counter_offset=e2e.counter_offset, crc_offset=e2e.crc_offset, data_id=e2e.data_id,
                                data_id_mode=e2e_ref.DATAID_BOTH, data_length=e2e.data_length)
        state = e2e_ref.P11CheckState()
        frames = [m for m in can.LogReader(str(CAN_TRACE)) if m.arbitration_id == status.frame_id]
        statuses = []
        for frame in frames:
            e2e_ref.p11_check(cfg, state, bytes(frame.data))
            statuses.append(state.status)
        self.assertEqual(statuses[1:8], [e2e_ref.P11STATUS_OK] * 7, statuses)
        self.assertEqual(statuses[8], e2e_ref.P11STATUS_WRONGSEQUENCE, statuses)
        self.assertEqual(set(statuses[9:]), {e2e_ref.P11STATUS_OK})


class LateBacklogBus(can.BusABC):
    """Bus double of the VN1610 finding (TOOL-46): passes the frames of `inner` through, but holds a backlog of old
    frames back and hands it over only `delay_s` after the first recv() - so recv(0.0) at the start finds nothing.
    On the simulated time of `clock` (TOOL-202).

    `stamp` gives the time stamp of the backlog: a fixed adapter time (frames from before the measurement that the
    driver delivers late) or None (stamped at delivery, like the burst the ECU sends when the adapter goes on the
    bus). Spacing 0.24 ms, as in captures/.../2026-09-16_canprobe.asc."""

    def __init__(self, inner: SimulatedBus, clock: SimClock, frame_id: int, count: int, delay_s: float,
                 stamp: float | None, **kwargs):
        super().__init__(channel="late-backlog", **kwargs)
        self.inner, self.clock, self.frame_id, self.count = inner, clock, frame_id, count
        self.delay_s, self.stamp = delay_s, stamp
        self.first_recv = None
        self.delivered = 0

    def send(self, msg, timeout=None):
        self.inner.send(msg, timeout)

    def recv(self, timeout=None):
        now = self.clock.t
        if self.first_recv is None:
            self.first_recv = now
        if self.delivered < self.count and now - self.first_recv >= self.delay_s:
            base = self.stamp if self.stamp is not None else self.clock.wall()
            msg = can.Message(timestamp=base + 0.00024 * self.delivered, arbitration_id=self.frame_id,
                              data=bytes(8), is_extended_id=False)
            self.delivered += 1
            return msg
        wait = timeout
        if self.delivered < self.count and timeout is not None:
            wait = max(0.0, min(timeout, self.first_recv + self.delay_s - now))
        return self.inner.recv(wait)

    def shutdown(self):
        pass


@recordings
class CanProbeBacklogTest(unittest.TestCase):
    """TOOL-46, board finding 2026-09-21 (VN1610): about 8 old frames came after the start of the window although
    can_probe emptied the queue with recv(0.0) before - 80 ms instead of 100 ms in 3 s, 95 ms in 15 s.

    TOOL-202: on simulated time. On real time (a thread that plays the recording) these tests failed on a loaded
    machine - a starved thread sends its overdue frames in a burst, and the probe measures exactly that."""

    SECONDS = 1.5

    def probe(self, delay_s, stamp, **kwargs):
        clock = SimClock()
        status = demo_setup().tx["EVB_Status"]
        tester = LateBacklogBus(SimulatedBus(TracePlayer(CAN_TRACE), clock), clock, status.frame_id, 8, delay_s,
                                None if stamp is None else clock.wall(stamp))
        result = checks.can_probe(tester, demo_setup(with_diag=True), seconds=self.SECONDS, clock=clock.now, **kwargs)
        self.assertEqual(tester.delivered, 8, "the double must have delivered its backlog")
        return result, status

    def cycle(self, result, status) -> float:
        line = next(line for line in result.lines if f"{status.name} 0x{status.frame_id:X}:" in line)
        return float(re.search(r"cycle ([\d.]+) ms", line).group(1))

    def test_old_frames_delivered_inside_the_window_are_discarded(self):
        """Backlog stamped before the probe began, handed over 0.6 s later - after the settling pause, inside the
        window, exactly where recv(0.0) could not reach it."""
        result, status = self.probe(0.6, 0.0)
        self.assertTrue(result.passed, result.lines)
        # the 8 of the backlog, plus at most one pair of the ECU (0x110, 0x111) sent just before the window began
        # and received just after - rightly left out as well
        self.assertGreaterEqual(result.values["stale_frames"], 8, result.lines)
        self.assertLessEqual(result.values["stale_frames"], 10, result.lines)
        self.assertLess(abs(self.cycle(result, status) - status.cycle_ms), 0.1 * status.cycle_ms, result.lines)

    def test_without_the_time_stamp_the_same_backlog_is_the_board_finding(self):
        """Evidence that the double reproduces the finding: judged by arrival (no settling, no time stamp check)
        the 8 frames shorten the cycle beyond the tolerance, as on the board."""
        with mock.patch.object(checks.AdapterTime, "before", return_value=False):
            result, status = self.probe(0.6, 0.0, settle_s=0.0)
        self.assertFalse(result.passed, result.lines)
        self.assertLess(self.cycle(result, status), 0.8 * status.cycle_ms, result.lines)

    def test_burst_when_the_adapter_goes_on_the_bus_falls_into_the_settling_pause(self):
        """The shape of the captures: 8 frames 0.24 ms apart, stamped when they reached the bus, shortly after the
        adapter was opened. They belong to the settling pause, not to the window."""
        result, status = self.probe(0.02, None)
        self.assertTrue(result.passed, result.lines)
        self.assertGreaterEqual(result.values["settle_frames"], 8, result.lines)
        # at most the pair of the ECU sent just before the window and received just after it (see above)
        self.assertLessEqual(result.values["stale_frames"], 2, result.lines)
        self.assertLess(abs(self.cycle(result, status) - status.cycle_ms), 0.1 * status.cycle_ms, result.lines)


class AdapterTimeTest(unittest.TestCase):
    def msg(self, stamp):
        return can.Message(timestamp=stamp, arbitration_id=0x110, data=bytes(8))

    def test_offset_is_the_smallest_delay_and_a_late_frame_does_not_spoil_it(self):
        clock = checks.AdapterTime()
        self.assertIsNone(clock.of(5.0))
        self.assertFalse(clock.before(1.0, 5.0), "nothing learnt yet: nothing is judged")
        clock.learn(self.msg(1_000_000.000), 10.002)      # 2 ms transport delay
        clock.learn(self.msg(1_000_000.100), 10.101)      # 1 ms: the better estimate
        clock.learn(self.msg(999_999.500), 10.150)        # delivered 650 ms late
        self.assertAlmostEqual(clock.offset, 10.101 - 1_000_000.100)
        self.assertTrue(clock.before(999_999.500, 10.120))
        self.assertFalse(clock.before(1_000_000.125, 10.120))

    def test_frames_without_a_time_stamp_are_neither_learnt_nor_judged(self):
        clock = checks.AdapterTime()
        clock.learn(self.msg(0.0), 10.0)
        self.assertIsNone(clock.offset)
        clock.learn(self.msg(100.0), 10.0)
        self.assertFalse(clock.before(0.0, 50.0))


# ------------------------------------------------------------------------------------------------ wake (COM-40)

class FakeWakeBus:
    """One channel open of send_wake_frames(): records what was sent and that it was shut down, so a bus reused
    across attempts (instead of reopened) shows up as a shorter `log` than expected."""

    def __init__(self, log):
        self.log = log
        self.entry = ["open"]
        log.append(self.entry)
        self.shutdown_called = False

    def send(self, msg):
        self.entry.append(("send", msg.arbitration_id, bytes(msg.data), msg.is_extended_id))

    def shutdown(self):
        self.shutdown_called = True
        self.entry.append("shutdown")


class WakeFrameTest(unittest.TestCase):
    """checks.send_wake_frames against a fake adapter double - no hardware, no CLI."""

    def test_channel_is_reopened_before_every_attempt(self):
        log, buses = [], []

        def open_bus():
            bus = FakeWakeBus(log)
            buses.append(bus)
            return bus

        clock = FakeClock()
        frame = can.Message(arbitration_id=0x123, is_extended_id=False, data=b"")
        result = checks.send_wake_frames(open_bus, frame, count=3, gap_s=0.2, sleep=clock.sleep)
        self.assertTrue(result.passed)
        self.assertEqual(result.values["attempts"], 3)
        self.assertEqual(len(buses), 3, "a fresh bus every attempt")
        self.assertEqual(len({id(b) for b in buses}), 3, "never the same bus object twice")
        for entry in log:
            self.assertEqual(entry, ["open", ("send", 0x123, b"", False), "shutdown"])
        self.assertTrue(all(b.shutdown_called for b in buses))
        self.assertEqual(clock.sleeps, [0.2, 0.2], "gap between attempts, none after the last one")

    def test_frame_content_reaches_the_bus_unchanged(self):
        log = []
        frame = can.Message(arbitration_id=0x1FFFFFFF, is_extended_id=True, data=bytes.fromhex("0011"))
        checks.send_wake_frames(lambda: FakeWakeBus(log), frame, count=1, sleep=lambda s: None)
        self.assertEqual(log[0][1], ("send", 0x1FFFFFFF, b"\x00\x11", True))


class WakeCommandLineTest(unittest.TestCase):
    """'boardtest wake' against a mocked adapter (Attrappe, no hardware, COM-40): the project's bit timing must
    reach canflash.bus_for by default, an explicit override must reach it instead (Gegenprobe), and the channel
    must be a new bus object on every attempt."""

    def setUp(self):
        sys.path.insert(0, str(ROOT / "tools" / "canflash"))
        import canflash

        self.canflash = canflash

    def run_wake(self, sub_args, sent, global_args=()):
        import cli

        calls = []

        def fake_bus_for(interface, channel, timing, app_name=None):
            calls.append((interface, channel, timing, app_name))
            bus = mock.Mock()
            bus.send.side_effect = lambda msg: sent.append(msg)
            return bus

        # global options (--bitrate/--sample-point/--sjw/--quanta) belong to the top-level parser and must
        # precede the subcommand name, like every other boardtest invocation (README.md "Common options").
        with mock.patch.object(self.canflash, "bus_for", fake_bus_for):
            code = cli.main(["--project", str(DEMO), *global_args, "wake", *sub_args])
        return code, calls

    def test_default_timing_is_the_project_and_channel_reopens_per_attempt(self):
        sent = []
        code, calls = self.run_wake(["0x123", "--count", "3", "--gap", "0"], sent)
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 3, "reopened before every attempt, not one bus for all three")
        for interface, channel, timing, app_name in calls:
            self.assertEqual(interface, "vector")
            self.assertEqual((timing.bitrate, timing.sample_point_percent, timing.sjw),
                             (500000, 81.25, 2))            # the demo project's bus, not a constant of the test
        self.assertEqual(len(sent), 3)
        self.assertTrue(all(msg.arbitration_id == 0x123 and len(msg.data) == 0 for msg in sent))

    def test_explicit_timing_overrides_the_project(self):
        """Gegenprobe: values that differ from the demo project's (500000/81.25/2/16) must reach the adapter
        unchanged - proves the default in the first test is really read from the project, not coincidental."""
        sent = []
        code, calls = self.run_wake(["0x123"], sent,
                                    global_args=["--bitrate", "250000", "--sample-point", "75", "--sjw", "1",
                                                "--quanta", "10"])
        self.assertEqual(code, 0)
        timing = calls[0][2]
        self.assertEqual((timing.bitrate, timing.sample_point_percent, timing.sjw, timing.quanta),
                         (250000, 75.0, 1, 10))

    def test_data_and_extended_id_reach_the_frame(self):
        sent = []
        self.run_wake(["0x1FFFFFFF", "--extended", "--data", "0011"], sent)
        self.assertEqual((sent[0].arbitration_id, sent[0].is_extended_id, bytes(sent[0].data)),
                         (0x1FFFFFFF, True, b"\x00\x11"))

    def test_missing_can_bus_is_reported(self):
        """Second build profile has no CAN bus at all (ProjectSetupTest): 'wake' must fail with SetupError like
        canprobe/qualifier, not with an attribute error on setup.timing."""
        import cli

        code = cli.main(["--project", str(ROOT / "projects" / "buildtest_s32k148_min"), "wake", "0x123"])
        self.assertEqual(code, 2)


class FakeRteEcu(threading.Thread):
    """ECU with the demo's qualifier behaviour on a virtual bus, time scaled: checks the received E2E frames with
    the reference CRC, computes the qualifier (debounce, timeout), sends the observed message with Led2 blinking
    only while the qualifier is OK, and answers "rte signals" in the recorded format."""

    def __init__(self, channel, setup, scale, broken=None):
        super().__init__(daemon=True)
        import e2e_ref

        self.e2e_ref = e2e_ref
        self.bus = can.Bus(interface="virtual", channel=channel)
        self.rx = setup.rx["PC_Command"]
        self.tx = setup.tx["EVB_Status"]
        self.scale, self.broken = scale, broken
        self.qlf = checks.qlf_values()
        self.last_frame = None
        self.last_counter = None
        self.error_kind, self.error_since = None, None
        self.seen_valid = False
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.template = con.answers_of(SIGNALS, "rte signals")[0]

    def qualifier(self, now):
        q = self.qlf
        timeout = self.rx.timeout_ms / 1000 * self.scale
        debounce = self.rx.debounce_ms / 1000 * self.scale
        if self.last_frame is None:
            return q["ERROR_TIMEOUT"] if now - self.started > timeout else q["DEBOUNCE"]
        if now - self.last_frame > timeout:
            return q["ERROR_TIMEOUT"]
        if self.error_kind is None:
            return q["OK"] if self.seen_valid else q["DEBOUNCE"]
        if self.broken == "crc_ignored" and self.error_kind == "ERROR_CRC":
            return q["OK"]
        return q[self.error_kind] if now - self.error_since >= debounce else q["DEBOUNCE"]

    def receive(self, data: bytes, now: float):
        e2e = self.rx.e2e
        counter = (data[e2e.counter_offset // 8] >> (e2e.counter_offset % 8)) & 0x0F
        crc = self.e2e_ref.p01_crc(data, len(data), e2e.crc_offset, e2e.data_id, self.e2e_ref.DATAID_BOTH, counter)
        if crc != data[e2e.crc_offset // 8]:
            kind = "ERROR_CRC"
        elif self.last_counter is not None and counter == self.last_counter:
            kind = "ERROR_ALIVE"
        else:
            kind = None
        self.last_counter = counter
        self.last_frame = now
        if kind is None:
            self.seen_valid = True
            self.error_kind = None
        elif kind != self.error_kind:
            self.error_kind, self.error_since = kind, now

    def signals_answer(self, _index):
        with self.lock:
            value = self.qualifier(time.monotonic())
        return re.sub(r"PC_Command\.Qlf = \d+", f"PC_Command.Qlf = {value}", self.template)

    def run(self):
        self.started = time.monotonic()
        cycle = self.tx.cycle_ms / 1000 * self.scale
        blink = 0.5 * self.scale
        next_send, counter = self.started, 0
        while not self.stop.is_set():
            msg = self.bus.recv(0.002)
            now = time.monotonic()
            with self.lock:
                if msg is not None and msg.arbitration_id == self.rx.frame_id:
                    self.receive(bytes(msg.data), now)
                ok = self.qualifier(now) == self.qlf["OK"]
            if now >= next_send:
                led2 = int((now - self.started) // blink) % 2 if ok else 0
                raw = self.tx.codec.encode({"Led1": 1, "Led2": led2, "MotorNtcFiltered_mV": 1642, "EvbCrc": 0,
                                            "SwVersion": 1, "EvbCounter": 0}, scaling=False, strict=False)
                self.bus.send(can.Message(arbitration_id=self.tx.frame_id, data=self.tx.e2e.protect(counter, raw),
                                          is_extended_id=False))
                counter = (counter + 1) % 15
                next_send += cycle

    def close(self):
        self.stop.set()
        self.join()
        self.bus.shutdown()


@recordings
class QualifierTest(unittest.TestCase):
    SCALE = 0.5           # 50 ms cycle, 100 ms debounce, 250 ms timeout, 2 s per phase (margin for a loaded PC)

    def run_phases(self, broken=None, signals=None, observe="EVB_Status.Led2", **kwargs):
        channel = f"qlf{time.monotonic_ns()}"
        setup = demo_setup()
        ecu = FakeRteEcu(channel, setup, self.SCALE, broken)
        ecu.start()
        self.addCleanup(ecu.close)
        tester = can.Bus(interface="virtual", channel=channel)
        self.addCleanup(tester.shutdown)
        console = con.Console(ReplayTransport({"rte signals": signals or ecu.signals_answer}))
        return checks.qualifier_phases(console, tester, setup, observe=observe, expect_gated=observe is not None,
                                       time_scale=self.SCALE, **kwargs), tester

    def test_all_phases_against_a_correct_ecu(self):
        result, _ = self.run_phases()
        self.assertTrue(result.passed, "\n".join(result.lines))
        q = checks.qlf_values()
        self.assertEqual(result.values["qlf"][1:], [q["OK"], q["ERROR_CRC"], q["OK"], q["ERROR_ALIVE"], q["OK"],
                                                    q["ERROR_TIMEOUT"], q["OK"]])
        # Led2 blinks in the OK phases (index 1, 3, 5, 7) and stays 0 in all others
        observed = result.values["observed"]
        self.assertTrue(all(observed[i][2] > 0 for i in (1, 3, 5, 7)), observed)
        self.assertTrue(all(observed[i][1] == 0 for i in (0, 2, 4, 6)), observed)

    def test_an_ecu_that_ignores_crc_errors_fails(self):
        """By the qualifier alone (no observed signal): the phase expectation itself must catch it."""
        result, _ = self.run_phases(broken="crc_ignored", observe=None)
        self.assertFalse(result.passed)
        failures = [line for line in result.lines if line.startswith("FAIL")]
        self.assertEqual(len(failures), 1, result.lines)
        self.assertIn("3 permanently wrong CRC: expected ERROR_CRC, qualifier 1", failures[0])

    def test_unknown_signals_format_stops_before_sending(self):
        template = con.answers_of(SIGNALS, "rte signals")[0].replace("can.qlf.", "qlf:")
        channel = f"qlffmt{time.monotonic_ns()}"
        listener = can.Bus(interface="virtual", channel=channel)
        self.addCleanup(listener.shutdown)
        tester = can.Bus(interface="virtual", channel=channel)
        self.addCleanup(tester.shutdown)
        console = con.Console(ReplayTransport({"rte signals": template}))
        with self.assertRaises(con.FormatError):
            checks.qualifier_phases(console, tester, demo_setup(), time_scale=self.SCALE)
        self.assertIsNone(listener.recv(0.2), "no frame may be sent with an unreadable qualifier")

    def test_frames_of_each_kind(self):
        setup = demo_setup()
        msg = setup.rx["PC_Command"]
        sender = checks.FrameSender(None, msg, bytes.fromhex("03E8382F00000000"), 0.1)
        # the frames of the COM-03 board test instructions (CHANGELOG), counter 0 and 1
        self.assertEqual(sender.frame("valid", 0).hex(), "03e8382fef000000")
        self.assertEqual(sender.frame("valid", 1).hex(), "03e8382f60010000")
        self.assertEqual(sender.frame("stuck", 7), sender.frame("valid", 0))
        self.assertNotEqual(sender.frame("badcrc", 2)[4], sender.frame("valid", 2)[4])
        self.assertEqual(sender.frame("badcrc", 2)[5], 2)
        self.assertIsNone(sender.frame("off", 3))


class ProjectSetupTest(unittest.TestCase):
    def test_values_come_from_the_project(self):
        setup = demo_setup(with_diag=True)
        self.assertEqual((setup.baudrate, setup.bus, setup.node, setup.tasks), (115200, "CAN0", "EVB", ["swc"]))
        self.assertEqual(setup.timing.segments(), {"sjw_abr": 2, "tseg1_abr": 12, "tseg2_abr": 3})
        self.assertEqual((setup.diag.request_id, setup.diag.response_id), (0x7E0, 0x7E8))
        self.assertEqual(sorted(setup.tx), ["EVB_Analog", "EVB_Status"])
        pc = setup.rx["PC_Command"]
        self.assertEqual((pc.frame_id, pc.cycle_ms, pc.timeout_ms, pc.debounce_ms), (0x210, 100, 500, 200))
        self.assertEqual((pc.e2e.profile, pc.e2e.data_id, pc.e2e.crc_offset, pc.e2e.counter_offset),
                         ("P01", 0x210, 32, 40))

    def test_second_profile_two_tasks_other_ids_no_can(self):
        """Build profile S32K148: two RTE tasks, other diagnostic addresses, no CAN signals - nothing of the eval
        board carries over, and a test that needs CAN says so instead of using the eval board's ids."""
        setup = proj.load(ROOT / "projects" / "buildtest_s32k148_min", with_diag=True)
        self.assertEqual((setup.diag.request_id, setup.diag.response_id), (0x6F0, 0x6F8))
        self.assertEqual(setup.tasks, ["app10", "app100"])
        self.assertIsNone(setup.timing)
        self.assertEqual((setup.tx, setup.rx), ({}, {}))
        answer = ("rte runnables\n task app10 (context 3): every 10 ms, cycles 500, overruns 0, max 50 us\n"
                  " task app100 (context 4): every 100 ms, cycles 50, overruns 0, max 90 us\n ok\n")
        console, _, _ = console_for({"rte runnables": answer})
        self.assertTrue(checks.echo(console, setup, 5).passed)
        console, _, _ = console_for({"rte runnables": answer.replace(" task app100", " tusk app100")})
        with self.assertRaises(con.FormatError) as ctx:
            checks.echo(console, setup, 5)
        self.assertIn("app100", str(ctx.exception))
        with self.assertRaises(proj.SetupError):
            checks.qualifier_phases(console, None, setup)

    def test_command_line_needs_the_port(self):
        import cli

        self.assertEqual(cli.main(["--project", str(DEMO), "echo"]), 2)


# ------------------------------------------------------------------------------------------------ capture (TOOL-25)

class SimulatedPort:
    """Serial port of an ECU that writes log lines on its own, at given seconds after the port was opened (on the
    FakeClock that replaces time.monotonic). Stands in for con.SerialTransport, so the command line runs unchanged.

    `watch_at`: seconds at which the test looks at the recording on disk and at the printed output while the
    capture still runs - what a killed process (time limit of the shell or agent tool) would leave behind.
    `interrupt_at`: seconds at which the operator presses Ctrl+C."""

    lines: list = []
    watch_at = None
    interrupt_at = None
    clock = None
    record = None
    stdout = None
    instance = None

    def __init__(self, port, baudrate):
        cls = type(self)
        cls.instance = self
        self.t0 = cls.clock.now
        self.pending = list(cls.lines)
        self.writes = []
        self.watched = None

    def elapsed(self):
        return type(self).clock.now - self.t0

    def read(self) -> bytes:
        cls = type(self)
        if cls.watch_at is not None and self.watched is None and self.elapsed() >= cls.watch_at:
            path = Path(cls.record)
            self.watched = (path.read_text(encoding="utf-8") if path.exists() else "", cls.stdout.getvalue())
        if cls.interrupt_at is not None and self.elapsed() >= cls.interrupt_at:
            raise KeyboardInterrupt
        data = b""
        while self.pending and self.pending[0][0] <= self.elapsed():
            data += self.pending.pop(0)[1].encode() + b"\r\n"
        return data

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    def discard_input(self) -> None:
        pass

    def close(self) -> None:
        pass


class CaptureCommandLineTest(unittest.TestCase):
    """'boardtest capture N [command] --record <stem>' against a simulated port (finding of the board appointment
    2026-09-18: without a command the recording stayed empty although log lines came)."""

    def setUp(self):
        import tempfile

        self.tmp = Path(tempfile.mkdtemp(prefix="boardtest_capture_"))
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)

    def run_capture(self, seconds, command=None, lines=(), watch_at=None, interrupt_at=None):
        import contextlib
        import io
        from unittest import mock

        import cli

        clock = FakeClock()
        stem = self.tmp / "run"
        stdout, stderr = io.StringIO(), io.StringIO()
        SimulatedPort.lines, SimulatedPort.clock = list(lines), clock
        SimulatedPort.watch_at, SimulatedPort.interrupt_at = watch_at, interrupt_at
        SimulatedPort.record, SimulatedPort.stdout = stem.with_suffix(".console.txt"), stdout
        argv = ["--project", str(DEMO), "--port", "SIM", "--record", str(stem), "capture", str(seconds)]
        argv += [command] if command else []
        with mock.patch.object(con, "SerialTransport", SimulatedPort), \
                mock.patch("time.monotonic", clock.clock), mock.patch("time.sleep", clock.sleep), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = cli.main(argv)
            except KeyboardInterrupt:           # would stop the whole unittest run
                code = "KeyboardInterrupt escaped the capture"
        recorded = stem.with_suffix(".console.txt").read_text(encoding="utf-8")
        return code, recorded, stdout.getvalue(), stderr.getvalue(), SimulatedPort.instance

    @staticmethod
    def log(seconds):
        return [(t, f"{t * 1000}: ECU log line {t}") for t in seconds]

    def assert_lines(self, text, seconds, absent=()):
        for t, line in self.log(seconds):
            self.assertIn(line + "\n", text)
        for t, line in self.log(absent):
            self.assertNotIn(line, text)

    def test_without_command_log_lines_are_recorded_and_printed_while_running(self):
        code, recorded, printed, _, port = self.run_capture(20, lines=self.log(range(1, 20)), watch_at=10.5)
        self.assertEqual(code, 0)
        self.assertEqual(port.writes, [])                               # nothing sent to the ECU
        on_disk, shown = port.watched
        self.assert_lines(on_disk, range(1, 11), absent=[11])           # a killed run keeps these
        self.assert_lines(shown, range(1, 11), absent=[11])             # and the operator saw them
        self.assert_lines(recorded, range(1, 20))
        self.assert_lines(printed, range(1, 20))
        self.assertNotIn("\n\n", recorded.strip("\n"))                 # CR LF once

    def test_with_command_log_lines_are_recorded_and_printed_while_running(self):
        code, recorded, printed, _, port = self.run_capture(20, "lc reboot", lines=self.log(range(1, 20)),
                                                            watch_at=10.5)
        self.assertEqual(code, 0)
        self.assertEqual(port.writes, [b"lc reboot\r\n"])
        on_disk, shown = port.watched
        self.assertTrue(on_disk.startswith("\n### lc reboot\n"), on_disk)
        self.assert_lines(on_disk, range(1, 11), absent=[11])
        self.assert_lines(shown, range(1, 11), absent=[11])
        self.assertTrue(recorded.startswith("\n### lc reboot\n"), recorded)
        self.assert_lines(recorded, range(1, 20))
        self.assert_lines(printed, range(1, 20))
        self.assertEqual(con.load_capture(self.tmp / "run.console.txt")[0][0], "lc reboot")

    def test_port_sends_only_after_some_seconds(self):
        code, recorded, printed, _, _ = self.run_capture(10, lines=self.log([6, 7, 9]))
        self.assertEqual(code, 0)
        self.assert_lines(recorded, [6, 7, 9])
        self.assert_lines(printed, [6, 7, 9])

    def test_ctrl_c_keeps_what_was_read(self):
        for command in (None, "lc reboot"):
            with self.subTest(command=command):
                code, recorded, printed, errors, _ = self.run_capture(90, command, lines=self.log(range(1, 90)),
                                                                      interrupt_at=7.5)
                self.assertEqual(code, 130)
                self.assert_lines(recorded, range(1, 8), absent=[8])
                self.assert_lines(printed, range(1, 8), absent=[8])
                self.assertIn("capture interrupted after 7.5 s of 90 s", errors)

    def test_recorder_joins_cr_lf_and_utf8_split_across_reads(self):
        chunks = [b"12: Temperatur 25 \xc2", b"\xb0C\r", b"\n13: ok\r\n"]

        class Chunks:
            def read(self):
                return chunks.pop(0) if chunks else b""

            def close(self):
                pass

        path = self.tmp / "split.console.txt"
        recorder = con.Recorder(Chunks(), path)
        for _ in range(4):
            recorder.read()
        recorder.close()
        self.assertEqual(path.read_text(encoding="utf-8"), "12: Temperatur 25 °C\n13: ok\n")


class StdoutEncodingTest(unittest.TestCase):
    """cli.main forces stdout to UTF-8 with errors=replace (TOOL-15, board finding 2026-09-16: 'Konsole nicht
    UTF-8' - a redirected stdout otherwise falls back to the OS locale's codepage, e.g. cp1252 on a German
    Windows, which is not necessarily what later reads the file or terminal back, and umlauts turn into
    mojibake). A live console is unaffected (Python's own console I/O on Windows is UTF-8, PEP 528); this only
    fixes the redirected case, which is also the only one a host test can observe."""

    def test_stdout_is_reconfigured_to_utf8_replace(self):
        import contextlib
        import io

        import cli

        fake_stdout = mock.MagicMock()
        stderr = io.StringIO()
        with mock.patch("sys.stdout", fake_stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(["--project", str(DEMO), "echo", "--count", "1"])
        self.assertEqual(code, 2)                                 # no --port: fails right after reconfigure
        self.assertIn("--port is needed", stderr.getvalue())
        fake_stdout.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    unittest.main(verbosity=2)
