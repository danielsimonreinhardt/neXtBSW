"""Console of the ECU application: transport, command/answer, recorded transcripts and the parsers.

Every value a board test reads from the console goes through a parser here. A parser that does not find what it
needs raises FormatError instead of returning nothing - that is the lesson of the board test 2026-09-16: after
RTE-02 renamed "ticks" to "cycles" in "rte runnables", the measurement scripts found no value, counted every answer
as lost and ran nine minutes before anybody noticed. test_boardtest.py checks the parsers against recorded
answers of the board (captures/) and against the format strings in the firmware source.
"""

import codecs
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from schedana import measure  # noqa: E402  (same parser as the Studio analysis, one place per format)


class FormatError(Exception):
    """An answer of the console does not have the expected format (firmware changed, wrong command)."""


# ------------------------------------------------------------------------------------------------ transport

class SerialTransport:
    """pyserial port, non blocking reads. Opened only by the command line (cli.py), never by the tests."""

    def __init__(self, port: str, baudrate: int):
        import serial

        self._serial = serial.Serial(port, baudrate, timeout=0, write_timeout=1)

    def read(self) -> bytes:
        waiting = self._serial.in_waiting
        return self._serial.read(waiting) if waiting else b""

    def write(self, data: bytes) -> None:
        self._serial.write(data)

    def discard_input(self) -> None:
        self._serial.reset_input_buffer()

    def close(self) -> None:
        self._serial.close()


class Recorder:
    """Wraps a transport and writes a transcript in the capture format (### command, answer) - the same format
    the host tests replay, so a board run can be kept as a new capture.

    Every write goes to the operating system at once (flush): a run that is ended without closing the file - the
    shell or the agent tool kills the process after its time limit, the window is closed - keeps everything read
    until then. Before TOOL-25 the text stayed in Python's 8 KB write buffer until close(), and a killed
    'capture 90' left an empty file although log lines had arrived."""

    def __init__(self, transport, path: Path):
        self.transport = transport
        self.file = open(path, "w", encoding="utf-8", newline="\n")
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")   # UTF-8 split across two reads
        self._cr = False                                                    # chunk ended in CR (CR LF split)
        self._line_start = True                                             # last write ended a line (note())

    def _write(self, text: str) -> None:
        if text:
            self._line_start = text.endswith("\n")
        self.file.write(text)
        self.file.flush()

    def read(self) -> bytes:
        data = self.transport.read()
        if data:
            text = self._decoder.decode(data)
            if self._cr and text.startswith("\n"):
                text = text[1:]
            self._cr = text.endswith("\r")
            self._write(text.replace("\r\n", "\n").replace("\r", "\n"))
        return data

    def write(self, data: bytes) -> None:
        text = data.decode("utf-8", "replace").strip()
        if text:
            self._write(f"\n### {text}\n")
        self.transport.write(data)

    def discard_input(self) -> None:
        self.transport.discard_input()

    def note(self, line: str) -> None:
        """A line of the test itself in the transcript (SYS-75 suite): '# ...' for what the test did, '#> ...' for
        results of CAN steps, operator answers and external tools, which the suite's checks read like console text."""
        self._write(("" if self._line_start else "\n") + f"{line}\n")

    def close(self) -> None:
        self.file.close()
        self.transport.close()


# ------------------------------------------------------------------------------------------------ transcripts

def load_capture(path: Path) -> list[tuple[str, str]]:
    """(command, answer) pairs of a transcript: '### <command>' lines, the answer below; '#' lines in front of the
    first command describe the capture (where, when, which firmware)."""
    pairs: list[tuple[str, list[str]]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.startswith("### "):
            pairs.append((line[4:].strip(), []))
        elif pairs:
            pairs[-1][1].append(line)
    return [(command, "\n".join(lines).strip("\n") + "\n") for command, lines in pairs]


def answers_of(path: Path, command: str) -> list[str]:
    return [answer for cmd, answer in load_capture(path) if cmd == command]


# ------------------------------------------------------------------------------------------------ console

# end of a console answer: the command interpreter prints " ok" after the output (recorded answers in
# captures/); without it the answer ends at the timeout, and Answer.complete tells the caller
ANSWER_END = re.compile(r"^\s*ok\s*$")


@dataclass
class Answer:
    command: str
    text: str
    lines: list = field(default_factory=list)       # (arrival time, line) of every complete line
    complete: bool = False                          # end marker seen before the timeout

    def time_of(self, pattern: str) -> float | None:
        """Arrival time of the first line matching `pattern` (drift: when the counter was read)."""
        regex = re.compile(pattern)
        return next((t for t, line in self.lines if regex.search(line)), None)


@dataclass
class Capture:
    text: str
    elapsed_s: float
    interrupted: bool = False                       # ended early with Ctrl+C


class Console:
    def __init__(self, transport, clock=None, sleep=None, poll_s: float = 0.002):
        # looked up when the console is made, not when the module is loaded (host tests replace time.monotonic)
        self.transport, self.poll_s = transport, poll_s
        self.clock = clock or time.monotonic
        self.sleep = sleep or time.sleep

    def capture(self, seconds: float, command: str = "", out=None) -> Capture:
        """Everything the console sends in `seconds` (after `command`, if one is given), passed on to `out` as it
        arrives - a capture of minutes shows the log while it runs, not only at the end.

        Ctrl+C ends the capture early and keeps what was read (Capture.interrupted); the caller closes the
        transport, so a Recorder has the same text in its file."""
        if command:
            self.transport.discard_input()
            self.transport.write(command.encode("ascii") + b"\r\n")
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        chunks = []
        start = self.clock()
        end = start + seconds
        interrupted = False
        try:
            while self.clock() < end:
                data = self.transport.read()
                if not data:
                    self.sleep(self.poll_s)
                    continue
                text = decoder.decode(data)
                chunks.append(text)
                if out is not None:
                    out.write(text.replace("\r\n", "\n").replace("\r", ""))
                    out.flush()
        except KeyboardInterrupt:
            interrupted = True
        return Capture("".join(chunks), self.clock() - start, interrupted)

    def command(self, command: str, timeout_s: float = 1.5, grace_s: float = 0.02) -> Answer:
        """Sends one command line and collects the answer until its end marker (plus `grace_s`) or `timeout_s`."""
        self.transport.discard_input()
        self.transport.write(command.encode("ascii") + b"\r\n")
        answer = Answer(command, "")
        pending = ""
        chunks = []
        end = self.clock() + timeout_s
        finish = None
        while self.clock() < end and (finish is None or self.clock() < finish):
            data = self.transport.read()
            if not data:
                self.sleep(self.poll_s)
                continue
            now = self.clock()
            text = data.decode("utf-8", "replace")
            chunks.append(text)
            pending += text
            *complete, pending = re.split(r"\r\n|\n|\r", pending)
            for line in complete:
                answer.lines.append((now, line))
                if finish is None and ANSWER_END.match(line):
                    answer.complete = True
                    finish = now + grace_s
        if pending:
            answer.lines.append((self.clock(), pending))
        answer.text = "".join(chunks)
        return answer


# ------------------------------------------------------------------------------------------------ parsers

def console_dedent(text: str) -> str:
    """Removes the one space the console writes in front of every output line (" task swc ...", " ok").

    The firmware's printf has "task %s ..." at the line start, the recorded board answers have " task swc ...";
    tools/schedana/measure.py expects the former (its test inputs were written without the space), so a real
    "rte runnables" answer gives no task there (finding TOOL-05, diff proposal in doc/changelog.d/TOOL-05.md)."""
    return re.sub(r"^ ", "", text, flags=re.MULTILINE)


@dataclass
class TaskCycles:
    name: str
    period_ms: int
    cycles: int


def task_cycles(text: str, expected_tasks=()) -> dict[str, TaskCycles]:
    """Cycle counters of the RTE tasks from "rte runnables" or "rte json".

    Raises FormatError if no task line can be read, or if a task of the ECU description (rte.yaml tasks) is
    missing - an answer without the counter is a format change, not a lost line, as soon as it happens in every
    answer (see echo test)."""
    parsed = measure.parse(console_dedent(text), known_tasks=expected_tasks)
    tasks = {name: TaskCycles(name, t.period_ms, t.cycles) for name, t in parsed.tasks.items()
             if t.cycles is not None and t.period_ms}
    if not tasks:
        hint = ""
        if re.search(r"^\s*task \S+ .*\bticks \d+", text, re.MULTILINE):
            hint = " (the answer counts 'ticks': firmware from before RTE-02, or the parser is out of date)"
        raise FormatError("no RTE task with period and cycle counter in the answer - expected 'task <name> "
                          "(context <n>): every <n> ms, cycles <n>, ...' (rte runnables) or {\"t\":\"task\", "
                          f"...\"cycles\":<n>}} (rte json){hint}")
    missing = [name for name in expected_tasks if name not in tasks]
    if missing:
        raise FormatError(f"answer without the cycle counter of task(s) {', '.join(missing)} of the ECU "
                          f"description (found: {', '.join(tasks)})")
    return tasks


# RTE-29: a fixed point signal (rte::SIGNAL_SCALINGS) is printed '<physical>[ <unit>] [<stored>]', e.g.
# 'TypeSwc.Temp.Temp = 21.0 degC [610]'; every other one '<value>' as before
RE_SIGNAL = re.compile(r"^\s*(?P<name>[A-Za-z_][\w.\[\]]*) = "
                       r"(?:(?P<physical>-?\d+(?:\.\d+)?)(?: (?P<unit>[^\[\]]*?))? \[(?P<stored>-?\d+)\]"
                       r"|(?P<value>-?\d+(?: e-3)?|\?))(?:\s+<-> (?P<links>.+?))?\s*$")


@dataclass
class SignalValue:
    name: str
    value: str                         # as printed; for a scaled signal the physical value (RTE-29)
    links: tuple = ()
    unit: str = ""                     # RTE-29: unit of a scaled signal ("" without)
    stored: int | None = None          # RTE-29: stored (raw) value of a scaled signal, None otherwise

    @property
    def number(self) -> float:
        if self.value.endswith(" e-3"):
            return int(self.value[:-4]) / 1000
        if "." in self.value:
            return float(self.value)
        return int(self.value)


def signals(text: str) -> dict[str, SignalValue]:
    """Lines '<label> = <value>[  <-> <link>, <link>]' of "rte signals" (writeSignalLine in RteConsole.h); a scaled
    signal '<label> = <physical>[ <unit>] [<stored>]' (RTE-29) gives the physical value, unit and stored value."""
    found = {}
    for line in text.splitlines():
        match = RE_SIGNAL.match(line)
        if match:
            links = tuple(s.strip() for s in match["links"].split(",")) if match["links"] else ()
            if match["physical"] is not None:
                found[match["name"]] = SignalValue(match["name"], match["physical"], links, match["unit"] or "",
                                                   int(match["stored"]))
            else:
                found[match["name"]] = SignalValue(match["name"], match["value"], links)
    return found


def qualifier(text: str, message: str) -> int:
    """Value of the qualifier of a received CAN message, found by its link can.qlf.<message> (not by label)."""
    link = f"can.qlf.{message}"
    for value in signals(text).values():
        if link in value.links:
            if value.value == "?":
                raise FormatError(f"'{link}' has no printable value")
            return int(value.number)
    raise FormatError(f"no signal linked to {link} in 'rte signals' - expected '<Msg>.Qlf = <n>  <-> {link}'")
