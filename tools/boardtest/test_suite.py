"""Host tests of the board test suite per SSR (SYS-75): no board, no adapter.

What turns red here instead of at the board:
  * the suite and doc/requirements/safety/ssr.sdoc drift apart (a board clause without a case or a reason, an unknown
    SSR, a missing reference capture), or the tables of doc/safety/board-test-spec.md are not the generated ones;
  * a check that no longer matches what the board printed: every procedure's console checks run against the board
    captures it names (captures/evalboard_s32k144_demo), and every check is shown to matter - removing the lines an
    `expect` finds, or putting a line an `absent` forbids into its window, turns the procedure red;
  * the runner: complete procedures (start-up after a power cycle, safety reset with the CAN gap, diagnostic session
    with communication control, sleep and wake-up, flashing) run against a simulated board whose console text comes
    from the captures and whose CAN side answers UDS with the answers recorded on the board (COM-28), on a virtual
    clock; the transcript, the evaluation and the run record are checked;
  * a second, synthetic project (own test profile) whose suite, project value and module default are read the same
    way - nothing of the eval board is built into the tool.

    .venv/Scripts/python -m unittest tools/boardtest/test_suite.py
"""

import contextlib
import io
import json
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path

import can

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cli  # noqa: E402
import console as con  # noqa: E402
import project as proj  # noqa: E402
import suite as st  # noqa: E402

ROOT = HERE.parents[1]
DEMO = ROOT / "projects" / "evalboard_s32k144_demo" / "project.yaml"
CAPTURES = HERE / "captures" / "evalboard_s32k144_demo"
SUITE = st.SUITES / "evalboard_s32k144_demo.yaml"
SESSION = CAPTURES / "2026-09-29_board" / "board0929_02_session.console.txt"
UDS_RECORD = CAPTURES / "2026-09-21_board" / "24_com28_uds.txt"
# PLAT-62: the board suite is the test plan of the eval board itself - its project and its recordings are private
# (PLAT-46 F10, tools/distcheck/distribution.yaml); the exported open tree skips these tests instead of failing
eval_board_suite = unittest.skipUnless(
    DEMO.is_file() and CAPTURES.is_dir(),
    "private eval board project and recordings not in this tree (exported open tree, PLAT-62)")
ECU_STATE_SRC = ROOT / "ecu" / "application" / "src" / "systems" / "EcuStateSystem.cpp"
ECU_STATE_NAMES = ROOT / "libs" / "bsw" / "ecuState" / "src" / "ecuState" / "EcuStateMachine.cpp"
RCM_DOC = ROOT / "platforms" / "s32k1xx" / "port" / "mcuResetReason" / "include" / "mcuResetReason" / "RcmResetReason.h"

_CACHE = {}


def demo_suite() -> st.Suite:
    if "suite" not in _CACHE:
        _CACHE["suite"] = st.load_suite(SUITE)
    return _CACHE["suite"]


def nodes():
    if "nodes" not in _CACHE:
        _CACHE["nodes"] = st.read_ssr()
    return _CACHE["nodes"]


def demo_setup() -> proj.ProjectSetup:
    if "setup" not in _CACHE:
        _CACHE["setup"] = proj.load(DEMO, with_diag=True)
    return _CACHE["setup"]


def sample_of(pattern: str) -> str:
    """A line that matches the first top-level alternative of an `absent` pattern (for the mutation tests)."""
    depth, first = 0, ""
    for i, ch in enumerate(pattern):
        if ch == "(" and (i == 0 or pattern[i - 1] != "\\"):
            depth += 1
        elif ch == ")" and pattern[i - 1] != "\\":
            depth -= 1
        elif ch == "|" and depth == 0:
            break
        first += ch
    sample = re.sub(r"\\(.)", r"\1", first).lstrip("^")
    assert re.search(pattern, sample, re.MULTILINE), (pattern, sample)
    return sample


def power_on_start() -> str:
    """The start part of the session capture with the start line of a power-on (format of RcmResetReason.h)."""
    text = SESSION.read_text(encoding="utf-8")
    start = text[:text.index(">>> help")]
    return start.replace("main(RCM::SRS 0x400 SW, last reset 0x20 WDOG fast test)",
                         "main(RCM::SRS 0x82 POR+LVD, last reset 0x20 WDOG fast test)")


# ------------------------------------------------------------------------------------------------ consistency

@eval_board_suite
class SuiteConsistencyTest(unittest.TestCase):
    def test_suite_is_consistent_with_the_ssr_document(self):
        self.assertEqual(st.validate(demo_suite(), nodes()), [])

    def test_ssr_reader_finds_clauses_methods_and_withdrawn(self):
        n = nodes()
        self.assertEqual(n["SSR-003(a)"].verification, ("test", "board"))
        self.assertEqual(n["SSR-003(a)"].parent, "SSR-003")
        self.assertEqual(n["SSR-010(d)"].status, "withdrawn")
        self.assertFalse(n["SSR-010(d)"].board)
        self.assertIn("SSR-070", st.board_items(n))           # an SSR without clauses
        self.assertNotIn("SSR-003", st.board_items(n))         # the parent of clauses is not an item itself
        self.assertNotIn("SSR-001(a)", st.board_items(n))      # test only

    def test_every_board_clause_has_a_case_or_a_reason(self):
        s = demo_suite()
        for item in st.board_items(nodes()):
            with self.subTest(item=item):
                self.assertTrue(s.covering(item) or item in s.exemptions or st.ssr_of(item) in s.exemptions)

    def test_operating_cases_of_the_task_are_all_present(self):
        present = {p.oc for p in demo_suite().procedures.values() if p.runnable}
        self.assertEqual(present, set(st.OPERATING_CASES))

    def test_scope_24v_motor_are_named_where_the_case_needs_them(self):
        s = demo_suite()
        self.assertIn("scope", s.procedure("BT-S1-02").equipment)
        self.assertIn("24v", s.procedure("BT-S1-02").equipment)
        self.assertTrue({"scope", "24v", "motor"} <= set(s.procedure("BT-PWM-01").equipment))
        self.assertNotIn("scope", s.procedure("BT-START-01").equipment)

    def test_validation_finds_faults(self):
        s = st.load_suite(SUITE)
        proc = s.procedure("BT-S2-01")
        proc.covers = proc.covers + ("SSR-999",)
        proc.oc = "BOOT"
        proc.equipment = ("console", "laser")
        s.procedures["BT-S1H-02"].references = [["2026-09-22_board/does_not_exist.txt"]]
        del s.procedures["BT-RUN-06"]                      # the only case of SSR-016
        problems = "\n".join(st.validate(s, nodes()))
        for text in ("SSR-999 is not in", "'BOOT' is not one of", "'laser' is not one of", "does_not_exist.txt",
                     "SSR-016(a) is verified on the board", "can_gap without 'can'"):
            self.assertIn(text, problems)

    def test_suite_file_errors_name_the_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.yaml"
            path.write_text("project: p\nprocedures:\n  BT-X-01:\n    steps:\n      - send: a\n        check:\n"
                            "          - expect: '('\n", encoding="utf-8")
            with self.assertRaisesRegex(st.SuiteError, r"procedures.BT-X-01.steps\[0\].check\[0\].expect"):
                st.load_suite(path)
            path.write_text("project: p\nprocedures:\n  BT-X-01:\n    steps:\n      - jump: 1\n", encoding="utf-8")
            with self.assertRaisesRegex(st.SuiteError, r"steps\[0\]: no action"):
                st.load_suite(path)


@eval_board_suite
class SpecDocumentTest(unittest.TestCase):
    """doc/safety/board-test-spec.md carries the generated tables between its markers."""

    def test_tables_are_the_generated_ones(self):
        text = st.SPEC_DOC.read_text(encoding="utf-8")
        start, end = "<!-- bts-matrix:start -->\n", "<!-- bts-matrix:end -->"
        self.assertIn(start, text)
        self.assertIn(end, text)
        block = text[text.index(start) + len(start):text.index(end)]
        self.assertEqual(block.strip(), st.matrix(demo_suite(), nodes()).strip(),
                         "regenerate with: python tools/boardtest --project projects/evalboard_s32k144_demo suite "
                         "matrix")

    def test_matrix_marks_scope_24v_motor_per_ssr(self):
        rows = {line.split("|")[1].strip(): line for line in st.matrix(demo_suite(), nodes()).splitlines()
                if line.startswith("| SSR-")}
        cells = [c.strip() for c in rows["SSR-005"].split("|")]
        self.assertEqual(cells[6:9], ["yes", "yes", "-"])          # scope, 24 V, motor (BT-S1-02)
        cells = [c.strip() for c in rows["SSR-089"].split("|")]
        self.assertEqual(cells[6:9], ["yes", "yes", "yes"])        # BT-PWM-01
        self.assertIn("not on this project", rows["SSR-089"])


# ------------------------------------------------------------------------------------------------ evaluator

def proc_of(*steps) -> st.Procedure:
    built = []
    for i, checks in enumerate(steps):
        built.append(st.Step(f"s{i + 1}", "listen", {"listen": 1}, st._checks(checks, "t", f"s{i + 1}")))
    return st.Procedure("BT-T-01", "t", "RUN", ("SSR-003",), "dev", "", ("console",), built, [], "x", [], True, "",
                        "")


@eval_board_suite
class EvaluatorTest(unittest.TestCase):
    def test_order_matters(self):
        proc = proc_of([{"expect": "A"}, {"expect": "B"}])
        self.assertTrue(st.evaluate(proc, "x A y B").passed)
        self.assertFalse(st.evaluate(proc, "B then A").passed)

    def test_absent_only_in_its_window(self):
        proc = proc_of([{"expect": "start"}, {"absent": "FAULT"}, {"expect": "end"}])
        self.assertTrue(st.evaluate(proc, "FAULT\nstart\nok\nend\nFAULT").passed)
        ev = st.evaluate(proc, "start\nFAULT here\nend")
        self.assertFalse(ev.passed)
        self.assertIn("FAULT here", ev.failures()[0].detail)

    def test_absent_at_the_end_runs_to_the_end(self):
        proc = proc_of([{"expect": "HELD"}, {"absent": "main\\(RCM"}])
        self.assertTrue(st.evaluate(proc, "HELD\nquiet").passed)
        self.assertFalse(st.evaluate(proc, "HELD\nmain(RCM::SRS 0x20)").passed)

    def test_optional_values_and_constraints(self):
        proc = proc_of([{"optional": "EPK (?P<epk>\\S+)"}, {"expect": "cause (?P<c>0x\\w+)"},
                        {"equals": {"c": "0x2"}}, {"differs": {"c": "0x1"}}])
        ev = st.evaluate(proc, "cause 0x2")
        self.assertTrue(ev.passed)
        self.assertEqual(ev.values, {"c": "0x2"})
        self.assertFalse(st.evaluate(proc, "cause 0x1").passed)

    def test_tool_lines_skipped_in_a_replay_only(self):
        proc = proc_of([{"expect": "#> canprobe: passed"}])
        self.assertTrue(st.evaluate(proc, "console only", tool_checks=False).passed)
        self.assertFalse(st.evaluate(proc, "console only").passed)
        self.assertTrue(st.evaluate(proc, "#> canprobe: passed").passed)

    def test_recorder_stamps_and_colours_are_ignored(self):
        proc = proc_of([{"expect": "^MCU watchdog fast tests successful$"}, {"expect": "^LIFECYCLE: INFO"}])
        text = "06:49:21  MCU watchdog fast tests successful\r\n  13.719 CON \x1b[90mLIFECYCLE\x1b[0m: INFO x\r\n"
        self.assertTrue(st.evaluate(proc, text).passed)

    def test_delta_against_project_value_and_module_default(self):
        proc = proc_of([{"expect": "(?P<a>\\d+): ROM"}, {"expect": "(?P<b>\\d+): ROM"},
                        {"delta": {"from": "a", "to": "b", "project": "modules.safeMemory.rom_check_period_ms",
                                   "tolerance": 100}}])
        # the demo project leaves the value to the module manifest (default 60000)
        self.assertTrue(st.evaluate(proc, "34: ROM\n60033: ROM", project_file=DEMO).passed)
        self.assertFalse(st.evaluate(proc, "34: ROM\n30033: ROM", project_file=DEMO).passed)
        # own test profile: a project that sets its own period
        with tempfile.TemporaryDirectory() as tmp:
            other = Path(tmp) / "project.yaml"
            other.write_text("modules:\n  safeMemory:\n    rom_check_period_ms: 2000\n", encoding="utf-8")
            self.assertTrue(st.evaluate(proc, "100: ROM\n2100: ROM", project_file=other).passed)
            self.assertFalse(st.evaluate(proc, "34: ROM\n60033: ROM", project_file=other).passed)


# ------------------------------------------------------------------------------------------------ references

@eval_board_suite
class ReferenceReplayTest(unittest.TestCase):
    """Every procedure against the board captures it names, and every check shown to matter (mutations)."""

    def test_references_pass(self):
        s = demo_suite()
        count = 0
        for proc in s.procedures.values():
            for ref, ev in st.replay(s, proc, DEMO):
                with self.subTest(proc=proc.pid, ref=ref):
                    self.assertTrue(ev.passed, "\n".join(ev.lines()))
                    count += 1
        self.assertGreaterEqual(count, 20)

    def test_removing_what_an_expect_finds_fails(self):
        s = demo_suite()
        for proc in s.procedures.values():
            for ref in proc.references:
                text = st.normalise(st.reference_text(s, ref))
                for check in proc.checks:
                    if check.kind != "expect" or check.tool:
                        continue
                    regex = re.compile(check.pattern, re.MULTILINE)
                    mutated = "\n".join(line for line in text.split("\n") if not regex.search(line))
                    with self.subTest(proc=proc.pid, check=check.pattern):
                        self.assertFalse(st.evaluate(proc, mutated, tool_checks=False, project_file=DEMO).passed)

    def test_a_forbidden_line_in_an_absent_window_fails(self):
        s = demo_suite()
        tested = 0
        for proc in s.procedures.values():
            for ref in proc.references:
                text = st.normalise(st.reference_text(s, ref))
                ev = st.evaluate(proc, text, tool_checks=False, project_file=DEMO)
                for outcome in ev.outcomes:
                    if outcome.check.kind != "absent" or not outcome.span:
                        continue
                    start, end = outcome.span
                    cut = text.find("\n", start, end)
                    cut = end if cut < 0 else cut
                    mutated = text[:cut] + "\n" + sample_of(outcome.check.pattern) + "\n" + text[cut:]
                    with self.subTest(proc=proc.pid, check=outcome.check.pattern):
                        self.assertFalse(st.evaluate(proc, mutated, tool_checks=False, project_file=DEMO).passed)
                        tested += 1
        self.assertGreaterEqual(tested, 15)

    def test_counter_captures_fail(self):
        s = demo_suite()
        cases = [
            # a watchdog reset is not the supervisor's S1 + S2 of lc assert
            ("BT-S2-03", "2026-09-23_board/14_sys43_taster_wdg1.raw.txt"),
            # a held safe state is no restart
            ("BT-S2-01", "2026-09-22_board/94_tool51_romfail.console.txt"),
            # the third reset holds: no "reset loop: 1 of"
            ("BT-S1H-04", "2026-09-23_board/14_sys43_taster_wdg3.raw.txt"),
            # a software-reset start is no power-on start
            ("BT-START-02", "2026-09-29_board/board0929_02_session.console.txt"),
            # the dump of a watchdog warning is no ROM failure
            ("BT-S1H-01", "2026-09-22_board/63_sys43_b10_wdg1.console.txt"),
        ]
        for pid, name in cases:
            with self.subTest(pid=pid, capture=name):
                text = (CAPTURES / name).read_text(encoding="utf-8")
                self.assertFalse(st.evaluate(s.procedure(pid), text, tool_checks=False, project_file=DEMO).passed)

    def test_power_on_start_from_the_documented_start_line(self):
        self.assertIn("main(RCM::SRS 0x82 POR+LVD, last reset 0x20 WDOG fast test)",
                      RCM_DOC.read_text(encoding="utf-8"))
        ev = st.evaluate(demo_suite().procedure("BT-START-02"), power_on_start(), tool_checks=False)
        self.assertTrue(ev.passed, "\n".join(ev.lines()))
        self.assertEqual(ev.values["start_reason"], "POR+LVD")

    def test_fault_free_time_last_step_against_its_capture(self):
        proc = demo_suite().procedure("BT-S1H-04")
        last = st.Procedure(proc.pid, proc.title, proc.oc, proc.covers, proc.image, proc.build, proc.equipment,
                            proc.steps[-1:], [], "x", [], True, "", "")
        text = (CAPTURES / "2026-09-22_board" / "66_sys43_b11_wdg3.console.txt").read_text(encoding="utf-8")
        self.assertTrue(st.evaluate(last, text, tool_checks=False).passed)

    def test_sleep_lines_are_the_firmware_formats(self):
        source = ECU_STATE_SRC.read_text(encoding="utf-8")
        self.assertIn('"ECU state %s -> %s"', source)
        self.assertIn('"sleep requested: Standby after min run, Rundown after %u ms post-run, then Shutdown and "',
                      source)
        self.assertIn('"ECU restart: %s"', source)
        names = ECU_STATE_NAMES.read_text(encoding="utf-8")
        for name in ("Run", "Standby", "Rundown", "Shutdown"):
            self.assertIn(f'return "{name}";', names)


# ------------------------------------------------------------------------------------------------ simulated board

class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.lock = threading.Lock()

    def clock(self):
        return self.now

    def sleep(self, seconds):
        with self.lock:
            self.now += max(seconds, 0.0005)


class SimBoard:
    """Console of a simulated board: answers commands from a table (text of the captures), and text that actions
    queue (a power cycle, a wake-up) arrives at the next reads - CR LF like the firmware, in pieces."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.out = bytearray()
        self.writes = []

    def push(self, text: str):
        self.out += text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")

    def write(self, data: bytes):
        command = data.decode("ascii").strip()
        self.writes.append(command)
        answer = self.answers.get(command)
        if callable(answer):
            answer = answer()
        self.push(answer if answer is not None else f"> {command}\n unknown command\n ok\n")

    def read(self) -> bytes:
        chunk = bytes(self.out[:180])
        del self.out[:180]
        return chunk

    def discard_input(self):
        pass

    def close(self):
        pass


class FakeOperator:
    def __init__(self, on_instruct=None, answers=None):
        self.on_instruct = on_instruct or (lambda text: None)
        self.answers = answers or {}
        self.instructions = []

    def instruct(self, text):
        self.instructions.append(text)
        self.on_instruct(text)

    def confirm(self, question):
        return True

    def ask(self, question):
        return self.answers.get(question, "")


def recorded_uds() -> dict[bytes, bytes]:
    """Request -> response of the board session COM-28 (2026-09-21), first answer per request."""
    table = {}
    for line in UDS_RECORD.read_text(encoding="utf-8").splitlines():
        if "->" not in line:
            continue
        left, right = line.split("->", 1)
        req = re.search(r"((?:[0-9a-f]{2} )*[0-9a-f]{2})\s*$", left.rstrip())
        if req:
            table.setdefault(bytes.fromhex(req.group(1)), bytes.fromhex(right.strip()))
    return table


class VirtualEcu:
    """CAN side of the simulated board on the virtual clock: the cyclic messages of the DBC, UDS on the project's
    diagnostic addresses with the recorded answers, session state for F186, communication control (28 03 / 10 01),
    and sleep (no frames) until a frame arrives, then `on_wake`."""

    def __init__(self, setup, clock: FakeClock, asleep=False, on_wake=None):
        self.setup, self.clock = setup, clock
        self.recorded = recorded_uds()
        self.session = 1
        self.tx_enabled = not asleep
        self.asleep = asleep
        self.on_wake = on_wake
        self.queue = []                                    # (due, can.Message)
        self.next_due = {m.frame_id: clock.now + 0.01 for m in setup.tx.values() if m.cycle_ms > 0}
        self.pending_cf = b""
        self.requests = []

    def answer(self, req: bytes) -> bytes | None:
        if req[:1] == b"\x10":
            self.session = req[1]
            if req[1] == 1:
                self.tx_enabled = True
            return self.recorded.get(req, bytes([0x50, req[1], 0x00, 0x32, 0x01, 0xF4]))
        if req == bytes.fromhex("22 f1 86"):
            return bytes([0x62, 0xF1, 0x86, self.session])
        if req == bytes.fromhex("22 f1 81"):
            return bytes.fromhex("62 f1 81 01") + b"nextbsw-app 0.1.0"     # text of the COM-28 record
        if req == bytes.fromhex("3e 00"):
            return bytes([0x7E, 0x00])
        if req == bytes.fromhex("28 03 01"):
            self.tx_enabled = False
            return bytes([0x68, 0x03])
        return self.recorded.get(req, bytes([0x7F, req[0], 0x11]))

    def receive(self, msg: can.Message):
        diag = self.setup.diag
        if self.asleep:
            self.asleep = False
            self.tx_enabled = True
            self.next_due = {i: self.clock.now + 0.8 for i in self.next_due}
            if self.on_wake:
                self.on_wake()
            return
        if msg.arbitration_id != diag.request_id:
            return
        data = bytes(msg.data)
        if data[0] >> 4 == 3 and self.pending_cf:
            index = 1
            while self.pending_cf:
                part, self.pending_cf = self.pending_cf[:7], self.pending_cf[7:]
                self.send_frame(bytes([0x20 | (index & 0x0F)]) + part)
                index += 1
            return
        if data[0] >> 4 != 0:
            return
        req = data[1:1 + data[0]]
        self.requests.append(req)
        resp = self.answer(req)
        if len(resp) <= 7:
            self.send_frame(bytes([len(resp)]) + resp)
        else:
            self.send_frame(bytes([0x10 | (len(resp) >> 8), len(resp) & 0xFF]) + resp[:6])
            self.pending_cf = resp[6:]

    def send_frame(self, data: bytes):
        data = data + bytes([self.setup.diag.padding] * (8 - len(data)))
        self.queue.append((self.clock.now + 0.002, can.Message(arbitration_id=self.setup.diag.response_id,
                                                                data=data, is_extended_id=False)))

    def next_frame(self, timeout: float):
        """The next frame due within `timeout` (virtual time advanced to it), else None after `timeout`."""
        if not self.tx_enabled or self.asleep:              # cyclic frames not sent while silent or asleep
            for frame_id in self.next_due:
                while self.next_due[frame_id] <= self.clock.now:
                    self.next_due[frame_id] += self.cycle(frame_id)
        candidates = [(due, i, msg) for i, (due, msg) in enumerate(self.queue)]
        if self.tx_enabled and not self.asleep:
            for frame_id, due in self.next_due.items():
                candidates.append((due, -1 - frame_id, frame_id))
        limit = self.clock.now + (timeout or 0.0)
        if not candidates or min(c[0] for c in candidates) > limit:
            self.clock.now = limit
            for frame_id in self.next_due:                 # frames not sent while silent or asleep
                while self.next_due[frame_id] <= self.clock.now:
                    self.next_due[frame_id] += self.cycle(frame_id)
            return None
        due, key, item = min(candidates, key=lambda c: c[0])
        self.clock.now = max(self.clock.now, due)
        if key >= 0:
            msg = self.queue.pop(key)[1]
        else:
            self.next_due[item] = due + self.cycle(item)
            msg = can.Message(arbitration_id=item, data=bytes(8), is_extended_id=False)
        msg.timestamp = self.clock.now
        return msg

    def cycle(self, frame_id) -> float:
        return next(m.cycle_ms for m in self.setup.tx.values() if m.frame_id == frame_id) / 1000.0


class EcuBus(can.BusABC):
    def __init__(self, ecu: VirtualEcu):
        super().__init__(channel="sim")
        self.ecu = ecu

    def send(self, msg, timeout=None):
        self.ecu.receive(msg)

    def recv(self, timeout=None):
        return self.ecu.next_frame(timeout)

    def _recv_internal(self, timeout):
        return self.ecu.next_frame(timeout), False


class GapBus(can.BusABC):
    """Frames of the ECU before and after a reset, stamped on the adapter's clock (for can_gap)."""

    def __init__(self, frame_id: int, stamps):
        super().__init__(channel="gap")
        self.frames = [can.Message(timestamp=t, arbitration_id=frame_id, data=bytes(8)) for t in stamps]

    def send(self, msg, timeout=None):
        pass

    def recv(self, timeout=None):
        return self.frames.pop(0) if self.frames else None

    def _recv_internal(self, timeout):
        return self.recv(timeout), False


def runner_for(board: SimBoard, open_bus, operator, out: Path, clock: FakeClock, run_command=None, image=None):
    return st.Runner(demo_suite(), demo_setup(), lambda: board, open_bus, operator, out, clock=clock.clock,
                     sleep=clock.sleep, run_command=run_command, image=image)


def no_bus():
    raise AssertionError("this procedure must not open the CAN adapter")


@eval_board_suite
class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        self.clock = FakeClock()

    def tearDown(self):
        self.tmp.cleanup()

    def test_power_on_start_passes_and_writes_transcript_and_record(self):
        board = SimBoard()
        operator = FakeOperator(on_instruct=lambda text: board.push(power_on_start()))
        run = runner_for(board, no_bus, operator, self.out, self.clock).run(demo_suite().procedure("BT-START-02"))
        self.assertTrue(run.passed, run.error or "\n".join(run.evaluation.lines()))
        self.assertIn("power cycle", operator.instructions[0])
        text = run.transcript.read_text(encoding="utf-8")
        self.assertIn("# BT-START-02: Power-on start", text)
        self.assertIn("# operator: Switch the supply", text)
        self.assertIn("main(RCM::SRS 0x82 POR+LVD", text)
        image = self.out / "app.hex"
        image.write_text(":00000001FF\n", encoding="utf-8")
        record = st.write_record(self.out, demo_suite(), [run], "v0.2.0", image, "tester", "2026-10-02T03:00:00")
        data = json.loads(record.read_text(encoding="utf-8"))
        self.assertEqual(data["results"][0]["result"], "passed")
        self.assertEqual(data["results"][0]["values"]["start_reason"], "POR+LVD")
        self.assertIn("SSR-050(c)", data["results"][0]["covers"])
        self.assertEqual(len(data["image"]["sha256"]), 64)
        self.assertEqual(data["baseline"], "v0.2.0")
        self.assertIn("| BT-START-02 | passed |", (self.out / "summary.md").read_text(encoding="utf-8"))

    def test_a_software_reset_start_fails_the_power_on_case(self):
        board = SimBoard()
        start = SESSION.read_text(encoding="utf-8")
        operator = FakeOperator(on_instruct=lambda text: board.push(start[:start.index(">>> help")]))
        run = runner_for(board, no_bus, operator, self.out, self.clock).run(demo_suite().procedure("BT-START-02"))
        self.assertFalse(run.passed)
        self.assertIn("POR", " ".join(o.check.pattern for o in run.evaluation.failures()))

    def test_safety_reset_with_restart_gap(self):
        restart = (CAPTURES / "2026-10-02_suite-integ12" / "BT-S2-01.console.txt").read_text(encoding="utf-8")
        # the tool lines of the recorded run ("#> can gap 857 ms") are not board output
        restart = "\n".join(line for line in restart.split("\n") if not line.startswith("#"))
        board = SimBoard({"safety wdg": restart})
        frame_id = demo_setup().tx["EVB_Status"].frame_id
        stamps = [10.0, 10.1, 10.2, 11.25, 11.35]           # 1050 ms without a frame across the reset
        run = runner_for(board, lambda: GapBus(frame_id, stamps), FakeOperator(), self.out, self.clock) \
            .run(demo_suite().procedure("BT-S2-01"))
        self.assertTrue(run.passed, run.error or "\n".join(run.evaluation.lines()))
        self.assertEqual(run.evaluation.values["restart_gap_ms"], "1050")
        self.assertEqual(run.evaluation.values["last_cause"], "0x1")
        self.assertEqual(board.writes, ["safety wdg"])

    def test_diagnostic_session_with_communication_control(self):
        rom = "\n60033: RefApp: SAFETY: DEBUG: ROM check passed successfully\n"
        board = SimBoard()
        ecu = VirtualEcu(demo_setup(), self.clock)
        original = ecu.receive

        def receive(msg):             # the board logs its periodic ROM check while the tester works
            original(msg)
            if bytes(msg.data[1:3]) == b"\x10\x01" and ecu.session == 1:
                board.push(rom)
        ecu.receive = receive
        run = runner_for(board, lambda: EcuBus(ecu), FakeOperator(), self.out, self.clock) \
            .run(demo_suite().procedure("BT-DIAG-01"))
        self.assertTrue(run.passed, run.error or "\n".join(run.evaluation.lines()))
        text = run.transcript.read_text(encoding="utf-8")
        self.assertIn("#> uds 10 03 -> 50 03 00 32 01 f4", text)            # recorded on the board (COM-28)
        self.assertIn("#> uds 22 f1 86 -> 62 f1 86 03", text)
        self.assertIn("#> can: 0 frame(s) from the ECU", text)
        self.assertIn("#> canprobe: passed", text)

    def test_diagnostic_session_fails_when_the_ecu_keeps_sending(self):
        board = SimBoard()
        ecu = VirtualEcu(demo_setup(), self.clock)
        answer = ecu.answer
        ecu.answer = lambda req: bytes([0x68, 0x03]) if req == bytes.fromhex("28 03 01") else answer(req)
        board.push("\n60033: RefApp: SAFETY: DEBUG: ROM check passed successfully\n")
        run = runner_for(board, lambda: EcuBus(ecu), FakeOperator(), self.out, self.clock) \
            .run(demo_suite().procedure("BT-DIAG-01"))
        self.assertFalse(run.passed)
        self.assertIn("#> can: 0 frame", " ".join(o.check.pattern for o in run.evaluation.failures()))

    def test_sleep_and_wake_up(self):
        status = con.answers_of(CAPTURES / "2026-09-21_board" / "02_sys18_poweron_baseline.console.txt",
                                "ecu status")[0]
        board = SimBoard({
            "ecu status": "> ecu status\n" + status,
            "ecu sleep": lambda: (
                "> ecu sleep\n sleep requested: Standby after min run, Rundown after 2000 ms post-run, then Shutdown "
                "and Sleep (withdraw: ecu run). Armed wake-up sources 0x00000100 CAN bus 0\n ok\n"
                "30001: RefApp: LIFECYCLE: INFO: ECU state Run -> Standby\n"
                "32002: RefApp: LIFECYCLE: INFO: ECU state Standby -> Rundown\n"
                "32010: RefApp: LIFECYCLE: INFO: ECU state Rundown -> Shutdown\n"),
        })
        restart = SESSION.read_text(encoding="utf-8")
        restart = restart[:restart.index(">>> help")].replace("wake-up sources 0x00000002",
                                                              "wake-up sources 0x00000100")
        ecu = VirtualEcu(demo_setup(), self.clock)
        original = ecu.answer

        def answer(req):
            return original(req)
        ecu.answer = answer
        state = {}

        def open_bus():
            if board.writes and board.writes[-1] == "ecu sleep" and "slept" not in state:
                state["slept"] = True
                ecu.asleep, ecu.tx_enabled = True, False
                ecu.on_wake = lambda: board.push(restart)
            return EcuBus(ecu)

        run = runner_for(board, open_bus, FakeOperator(), self.out, self.clock) \
            .run(demo_suite().procedure("BT-SLEEP-01"))
        self.assertTrue(run.passed, run.error or "\n".join(run.evaluation.lines()))
        self.assertEqual(run.evaluation.values["wakeup_sources"], "0x00000100")
        text = run.transcript.read_text(encoding="utf-8")
        self.assertIn("#> wake: 3 frame(s) 0x7E0 sent", text)

    def test_flash_with_the_recorded_canflash_output(self):
        board = SimBoard()
        ecu = VirtualEcu(demo_setup(), self.clock)
        flash_log = (CAPTURES / "2026-09-21_board" / "43_flash_dev.log").read_text(encoding="utf-8")
        boot = (CAPTURES / "2026-09-21_board" / "44_flash_dev.console.txt").read_text(encoding="utf-8")
        calls = []

        def run_command(argv):
            calls.append(argv)
            board.push(boot)
            return 0, flash_log

        image = self.out / "app.hex"
        image.write_text(":00000001FF\n", encoding="utf-8")
        run = runner_for(board, lambda: EcuBus(ecu), FakeOperator(), self.out, self.clock, run_command, image) \
            .run(demo_suite().procedure("BT-FLASH-01"))
        self.assertTrue(run.passed, run.error or "\n".join(run.evaluation.lines()))
        self.assertEqual(calls[0][1:], ["tools/canflash/canflash.py", "flash", str(image)])
        self.assertEqual(run.evaluation.values["image_crc"], "0xC3BAF2B5")
        self.assertIn("#> uds 22 f1 81 -> 62 f1 81 01 6e 65 78 74", run.transcript.read_text(encoding="utf-8"))

    def test_a_broken_step_is_reported_with_the_procedure(self):
        def broken_bus():
            raise can.CanInitializationError("no adapter")
        run = runner_for(SimBoard(), broken_bus, FakeOperator(), self.out, self.clock) \
            .run(demo_suite().procedure("BT-DIAG-01"))
        self.assertFalse(run.passed)
        self.assertIn("no adapter", run.error)
        self.assertIn("#> error", run.transcript.read_text(encoding="utf-8"))


# ------------------------------------------------------------------------------------------------ command line

@eval_board_suite
class SuiteCommandLineTest(unittest.TestCase):
    def call(self, *argv) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--project", str(DEMO), "suite", *argv])
        return code, out.getvalue(), err.getvalue()

    def test_check_list_matrix_replay(self):
        code, out, _ = self.call("check")
        self.assertEqual(code, 0, out)
        self.assertIn("consistent", out)
        code, out, _ = self.call("list", "--oc", "S1H")
        self.assertEqual(code, 0)
        self.assertEqual(len(out.strip().splitlines()), 4)
        code, out, _ = self.call("matrix")
        self.assertIn("| SSR-003 |", out)
        code, out, _ = self.call("replay", "BT-S2-04")
        self.assertEqual(code, 0, out)
        self.assertIn("wdog_cause=0x103", out)

    def test_replay_of_a_given_capture_fails_on_the_wrong_one(self):
        code, out, _ = self.call("replay", "BT-S2-03", str(CAPTURES / "2026-09-23_board" /
                                                         "14_sys43_taster_wdg1.raw.txt"))
        self.assertEqual(code, 1)
        self.assertIn("FAILED", out)

    def test_run_refuses_without_out_and_for_a_case_of_another_project(self):
        code, _, err = self.call("run", "BT-START-01")
        self.assertEqual(code, 2)
        self.assertIn("--out", err)
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = self.call("run", "BT-PWM-01", "--out", tmp)
        self.assertEqual(code, 2)
        self.assertIn("does not run on this project", err)


if __name__ == "__main__":
    unittest.main()
