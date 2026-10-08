"""Pin events on digital inputs (IO-51): derivative facts of all four families and the checks of pinevent.py.

Own profiles only: copies of the synthetic test board/project on invented derivatives (EXTI-like lines, EIRQ-like
lines from the pin multiplexing table, a VIM core), a copy of the rm4x test board on the real RM46L852, and a copy
of the demo project (real S32K144 with the Hall sensor of the eval board) - the demo itself stays unchanged.

Run: .venv/Scripts/python -m unittest tools/nxconfig/test_pinevent.py
"""

import copy
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))

import nxconfig  # noqa: E402
from nxconfig import pinevent  # noqa: E402
from nxconfig.config import _read, Reporter  # noqa: E402
from nxconfig.model import Derivative, PinInterruptRoute as Route  # noqa: E402

TEST_MCUDB = HERE / "test" / "mcudb"
PROFILES = HERE / "test" / "profiles"
# PLAT-62: the own full-featured test profile (synthetic testboard_full/testproject_full) instead of the eval board
# project, which stays private (PLAT-46 F10) - a test against it would be red in the exported open tree. What
# proves something about the eval board itself uses EVAL_DEMO_PROJECT and is skipped where it is missing.
DEMO_PROJECT = REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full" / "project.yaml"
EVAL_DEMO_PROJECT = REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml"
EMPTY_RTE = ("schema_version: 2\ntasks: [{ name: swc, period_ms: 10, priority: 30, stack_bytes: 2048 }]\n"
             "components: []\n")


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def derivative(path: Path) -> Derivative:
    d = _read(Reporter(), "derivative", path, Derivative)
    assert d is not None, path
    return d


def event(**values) -> dict:
    base = {"edge": "activate", "debounce_us": 2000, "max_events": 20, "window_ms": 100, "rearm_ms": 1000}
    base.update(values)
    return base


class Tree(unittest.TestCase):
    """Temporary tree with an mcudb copy, boards and projects."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.mcudb = self.tmp / "mcudb"
        shutil.copytree(TEST_MCUDB, self.mcudb)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_check(self, board: dict, project: dict, name: str = "t", mcudb: Path | None = None):
        board_dir, project_dir = self.tmp / "boards" / name, self.tmp / "projects" / name
        board_dir.mkdir(parents=True, exist_ok=True)
        project_dir.mkdir(parents=True, exist_ok=True)
        (board_dir / "board.yaml").write_text(yaml.safe_dump(board, sort_keys=False), encoding="utf-8")
        project = copy.deepcopy(project)
        project["project"]["board"] = f"../../boards/{name}"
        path = project_dir / "project.yaml"
        path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        return nxconfig.check(path, mcudb if mcudb is not None else self.mcudb)

    def errors(self, messages):
        return [(m.yaml_path, m.text) for m in messages if m.severity == "error"]

    def assert_error(self, messages, path: str, fragment: str, level: str = "project"):
        errors = [m for m in messages if m.severity == "error"]
        match = [m for m in errors if m.yaml_path == path and fragment in m.text]
        self.assertTrue(match, f"no error {path!r} with '{fragment}' in {[str(m) for m in errors]}")
        self.assertEqual(match[0].file.name, f"{level}.yaml")
        self.assertIsNotNone(match[0].line)


# ------------------------------------------------------------------------------------------ derivative facts


class RealDerivatives(unittest.TestCase):
    """The mcudb entries of the four families: which pin reaches which vector through which line."""

    def test_s32k14x_one_vector_per_port(self):
        facts = derivative(REPO / "mcudb/s32k1xx/S32K144.yaml").pin_interrupts
        self.assertEqual(facts.routes("PTC12"), [Route("PORTC", None)])
        self.assertEqual(facts.routes("PTA17"), [Route("PORTA", None)])
        self.assertEqual(facts.routes("PTA18"), [])
        self.assertEqual(derivative(REPO / "mcudb/s32k1xx/S32K148.yaml").pin_interrupts.routes("PTE25"),
                         [Route("PORTE", None)])

    def test_s32k11x_one_vector_for_all_ports(self):
        facts = derivative(REPO / "mcudb/s32k1xx/S32K116.yaml").pin_interrupts
        self.assertEqual({r.vector for pin in ("PTA0", "PTB5", "PTE7") for r in facts.routes(pin)}, {"PORT"})

    def test_stm32_line_per_pin_number(self):
        for name in ("STM32F446RE", "STM32G474RE"):
            facts = derivative(REPO / f"mcudb/stm32/{name}.yaml").pin_interrupts
            self.assertEqual(facts.routes("PA3"), [Route("EXTI3", "EXTI3")])
            self.assertEqual(facts.routes("PB7"), [Route("EXTI9_5", "EXTI7")])
            self.assertEqual(facts.routes("PC13"), [Route("EXTI15_10", "EXTI13")])
            self.assertFalse(facts.verify)

    def test_s32k3_eirq_only_at_the_pins_of_the_table(self):
        d = derivative(REPO / "mcudb/s32k3xx/S32K344.yaml")
        table = load(REPO / "mcudb/s32k3xx" / d.pinmux["HDQFP172"].file)["signals"]

        def pins(signal):
            return set(table.get(signal, {}))
        facts = d.pin_interrupts
        self.assertEqual(facts.routes("PTA0", pins), [Route("SIUL2_0", "EIRQ0")])
        self.assertEqual(facts.routes("PTE9", pins), [Route("SIUL2_1", "EIRQ8")])
        self.assertEqual(facts.routes("PTA17", pins), [])        # no EIRQ function at this pin
        self.assertEqual(facts.routes("PTA0"), [])               # without a table no line is reachable
        self.assertTrue(facts.verify)                            # vector grouping not from a document in the repo

    def test_rm46_gio_pins_on_either_request(self):
        d = derivative(REPO / "mcudb/rm4x/RM46L852.yaml")
        self.assertEqual(d.pin_interrupts.routes("GIOB_1"), [Route("GIO_A", None), Route("GIO_B", None)])
        self.assertEqual(d.pin_interrupts.routes("N2HET1_17"), [])
        for vector in d.pin_interrupts.vectors:
            self.assertIn(vector.name, d.vim.requests)

    def test_every_derivative_of_the_repository_describes_pin_interrupts(self):
        files = sorted(p for p in (REPO / "mcudb").glob("*/*.yaml") if not p.name.endswith(".pinmux.yaml"))
        # TOOL-244: stm32, rm4x and s32k3xx are private (four derivatives), missing in the exported open tree
        self.assertGreaterEqual(len(files), 9 if (REPO / "mcudb/stm32").is_dir() else 5)
        for file in files:
            with self.subTest(file.name):
                self.assertIsNotNone(derivative(file).pin_interrupts)

    def test_model_rejects_inconsistent_facts(self):
        base = load(TEST_MCUDB / "testfam" / "TESTMCU64.yaml")
        cases = {
            "line EXTI9 ends in no vector": {"lines": [{"name": "EXTI9", "pins": "P[A-H]9"}],
                                             "vectors": [{"name": "V", "pins": "PA0"}]},
            "unknown line": {"vectors": [{"name": "V", "lines": ["L1"]}]},
            "exactly one of pins and lines": {"vectors": [{"name": "V", "pins": "PA0", "lines": ["L"]}],
                                              "lines": [{"name": "L", "pins": "PA0"}]},
            "invalid regular expression": {"vectors": [{"name": "V", "pins": "PA("}]},
            "ends in two vectors": {"lines": [{"name": "L", "signal": "EIRQ"}],
                                    "vectors": [{"name": "V1", "lines": ["L"]}, {"name": "V2", "lines": ["L"]}]},
            "named twice": {"vectors": [{"name": "V", "pins": "PA0"}, {"name": "V", "pins": "PA1"}]},
        }
        tmp = Path(tempfile.mkdtemp())
        try:
            for fragment, facts in cases.items():
                with self.subTest(fragment):
                    data = copy.deepcopy(base)
                    data["pin_interrupts"] = facts
                    file = tmp / "X.yaml"
                    file.write_text(yaml.safe_dump(data), encoding="utf-8")
                    rep = Reporter()
                    self.assertIsNone(_read(rep, "derivative", file, Derivative))
                    self.assertTrue(any(fragment in m.text for m in rep.messages), [m.text for m in rep.messages])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------------------------------ invented derivatives


# EXTI-like: line n serves pin n of one port (like stm32), lines 10..15 share a vector; only rising edges
LINES_FACTS = {
    "edges": ["rising", "falling", "both"],
    "lines": [{"name": f"L{n}", "pins": f"P[A-H]{n}"} for n in range(16)],
    "vectors": [{"name": f"V{n}", "lines": [f"L{n}"]} for n in range(10)]
    + [{"name": "V15_10", "lines": [f"L{n}" for n in range(10, 16)]}],
    "source": "synthetic",
}


class InventedDerivatives(Tree):
    """testboard/testproject (TESTMCU64-like, not S32K1) with pin interrupt facts written by the test."""

    def facts(self, facts, name="TESTMCU64"):
        file = self.mcudb / "testfam" / f"{name}.yaml"
        data = load(file)
        if "vim" not in data:                                       # ranks need NVIC priority bits
            data["derivative"].setdefault("nvic_priority_bits", 4)
        if facts is None:
            data.pop("pin_interrupts", None)
        else:
            data["pin_interrupts"] = facts
        file.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    def profile(self, events: dict, ranks: dict, extra_inputs=()):
        board = load(PROFILES / "boards/testboard/board.yaml")
        board["dio"]["inputs"].extend(extra_inputs)
        project = load(PROFILES / "projects/testproject/project.yaml")
        project["pin_events"] = events
        ranks_list = [{"source": "can", "name": n, "rank": 0} for n in ("VEH", "DIAG")]
        ranks_list += [{"source": "dio", "name": n, "rank": r} for n, r in ranks.items()]
        project["interrupts"] = {"ranks": ranks_list}
        return board, project

    def test_line_shared_by_two_pins_excludes_them(self):
        self.facts(LINES_FACTS)
        board, project = self.profile({"KEY": event(), "KEY2": event()}, {"KEY": 2, "KEY2": 2},
                                      [{"name": "KEY2", "pin": "PA13"}])
        config, messages = self.run_check(board, project)
        self.assert_error(messages, "pin_events.KEY2", "needs line L13 of TESTMCU64, which KEY (PC13, pin event)")

    def test_shared_vector_needs_the_same_rank(self):
        self.facts(LINES_FACTS)
        board, project = self.profile({"KEY": event(), "KEY3": event()}, {"KEY": 2, "KEY3": 3},
                                      [{"name": "KEY3", "pin": "PC15"}])
        _, messages = self.run_check(board, project)
        self.assert_error(messages, "pin_events.KEY3", "vector V15_10 of TESTMCU64 already serves KEY with rank 2")
        board, project = self.profile({"KEY": event(), "KEY3": event()}, {"KEY": 2, "KEY3": 2},
                                      [{"name": "KEY3", "pin": "PC15"}])
        config, messages = self.run_check(board, project)
        self.assertEqual(self.errors(messages), [])
        uses = {u.input: u for u in config.pin_events}
        self.assertEqual((uses["KEY"].vector, uses["KEY"].line, uses["KEY"].shares), ("V15_10", "L13", ("KEY3",)))
        # KEY is active low on the board: activate is the falling edge
        self.assertEqual((uses["KEY"].edge, uses["KEY"].physical_edge), ("activate", "falling"))

    def test_own_vectors_take_different_ranks(self):
        self.facts(LINES_FACTS)
        board, project = self.profile({"KEY": event(), "KEY2": event(edge="both")}, {"KEY": 2, "KEY2": 5},
                                      [{"name": "KEY2", "pin": "PB4", "active": "high"}])
        config, messages = self.run_check(board, project)
        self.assertEqual(self.errors(messages), [])
        self.assertEqual([(u.input, u.vector, u.rank, u.physical_edge) for u in config.pin_events],
                         [("KEY", "V15_10", 2, "falling"), ("KEY2", "V4", 5, "both")])
        info = pinevent.info(config)
        self.assertEqual(info[0], "pin event KEY (PC13): edge activate = falling (active low), vector V15_10 via "
                                  "line L13, rank 2, debounce 2000 us, at most 20 per 100 ms (200/s), re-armed "
                                  "1000 ms after the limit trips")

    def test_pins_of_a_signal_only(self):
        """EIRQ-like: one line reachable only from the pins of a pin multiplexing signal (s32k3xx SIUL2)."""
        data = load(self.mcudb / "testfam" / "TESTMCU64.yaml")
        data["derivative"]["nvic_priority_bits"] = 4
        data["pinmux"]["QFP64"]["signals"]["EIRQ0"] = {"PC13": {"imcr": 16, "imcr_sss": 1}}
        data["pin_interrupts"] = {"lines": [{"name": "EIRQ0", "signal": "EIRQ0"}],
                                  "vectors": [{"name": "SIUL", "lines": ["EIRQ0"]}]}
        (self.mcudb / "testfam" / "TESTMCU64.yaml").write_text(yaml.safe_dump(data, sort_keys=False),
                                                                encoding="utf-8")
        board, project = self.profile({"KEY": event(), "KEY3": event()}, {"KEY": 1, "KEY3": 1},
                                      [{"name": "KEY3", "pin": "PC15"}])
        config, messages = self.run_check(board, project)
        self.assert_error(messages, "pin_events.KEY3", "pin PC15 of TESTMCU64 cannot raise an interrupt")
        self.assertEqual([(u.input, u.vector, u.line) for u in config.pin_events] if config else [], [])
        board, project = self.profile({"KEY": event()}, {"KEY": 1})
        config, messages = self.run_check(board, project)
        self.assertEqual(self.errors(messages), [])
        self.assertEqual([(u.vector, u.line) for u in config.pin_events], [("SIUL", "EIRQ0")])

    def test_edge_the_detectors_cannot_see(self):
        facts = dict(LINES_FACTS, edges=["rising"])
        self.facts(facts)
        board, project = self.profile({"KEY": event()}, {"KEY": 1})
        _, messages = self.run_check(board, project)
        self.assert_error(messages, "pin_events.KEY.edge", "edge activate of an active-low input is a falling edge")
        board, project = self.profile({"KEY": event(edge="deactivate", debounce_us=0)}, {"KEY": 1})
        _, messages = self.run_check(board, project)
        self.assertEqual(self.errors(messages), [])
        # IO-54: debouncing confirms the level from the last change inside the lock-out, so the detector must
        # report both edges (pinEvent detectorEdges()) - a rising-only detector cannot debounce
        board, project = self.profile({"KEY": event(edge="deactivate")}, {"KEY": 1})
        _, messages = self.run_check(board, project)
        self.assert_error(messages, "pin_events.KEY.debounce_us", "debounce_us 2000 needs an edge detector that "
                                                                  "reports both edges")
        self.assertEqual(len(self.errors(messages)), 1)

    def test_derivative_without_facts(self):
        self.facts(None)
        board, project = self.profile({"KEY": event()}, {"KEY": 1})
        _, messages = self.run_check(board, project)
        self.assert_error(messages, "pin_events.KEY", "does not describe which pins can raise an interrupt")

    def test_entry_rules(self):
        self.facts(LINES_FACTS)
        board, project = self.profile({"KEY": event(debounce_us=5000, wakeup=True), "LED_GREEN": event(),
                                       "NOPE": event()}, {})
        _, messages = self.run_check(board, project)
        self.assert_error(messages, "pin_events.KEY", "the input has no interrupt rank")
        self.assert_error(messages, "pin_events.KEY.wakeup", "wakeup is reserved for an MCU low-power mode")
        self.assert_error(messages, "pin_events.KEY.debounce_us", "debounce_us 5000 must be shorter than the "
                                                                  "shortest spacing the rate limit allows (100 ms / "
                                                                  "20 events = 5000 us)")
        self.assert_error(messages, "pin_events.LED_GREEN", "is a digital output of board")
        self.assert_error(messages, "pin_events.NOPE", "has no digital input NOPE")

    def test_unused_input(self):
        self.facts(LINES_FACTS)
        board, project = self.profile({"KEY": event()}, {})
        project["use"]["dio"] = ["LED_GREEN", "LED_RED"]
        _, messages = self.run_check(board, project)
        self.assert_error(messages, "pin_events.KEY", "the project does not use the input (use.dio)")

    def test_value_ranges_are_schema_limits(self):
        self.facts(LINES_FACTS)
        for key, value in (("max_events", 0), ("window_ms", 0), ("rearm_ms", 0), ("debounce_us", -1),
                           ("debounce_us", 1_000_001), ("max_events", 65536), ("edge", "rising")):
            with self.subTest(key=key, value=value):
                board, project = self.profile({"KEY": event(**{key: value})}, {"KEY": 1})
                config, messages = self.run_check(board, project)
                self.assertIsNone(config)
                self.assertTrue(any(m.yaml_path.startswith(f"pin_events.KEY.{key}") for m in messages),
                                [str(m) for m in messages])

    def test_pin_claimed_by_an_input_capture(self):
        """One consumer per pin: an input capture on the same pin is refused by the pin claim."""
        self.facts(LINES_FACTS)
        board, project = self.profile({"RC": event()}, {"RC": 1}, [{"name": "RC", "pin": "PA15"}])
        _, messages = self.run_check(board, project)
        self.assertTrue(any("pin PA15 is used by" in text for _, text in self.errors(messages)), messages)

    def test_vim_core_vector_must_be_a_request(self):
        board = load(PROFILES / "boards/testboard_r4f/board.yaml")
        board["dio"] = {"inputs": [{"name": "IN_A", "pin": "GIOB1"}]}
        board["can"] = board["can"][:1]
        project = load(PROFILES / "projects/testproject_r4f/project.yaml")
        project["use"]["dio"] = "all"
        project["can"].pop("BUS2")
        project["interrupts"]["ranks"] = [{"source": "can", "name": "BUS1", "rank": 0},
                                          {"source": "dio", "name": "IN_A", "rank": 1}]
        project["pin_events"] = {"IN_A": event()}
        self.facts({"vectors": [{"name": "GIO_HIGH", "pins": "GIO[AB][0-7]"}]}, name="TESTR4F")
        _, messages = self.run_check(board, project, "r4f")
        self.assert_error(messages, "pin_interrupts", "pin interrupt vector GIO_HIGH is no request of the VIM",
                          level="TESTR4F")


# ------------------------------------------------------------------------------------------ real derivatives


class Rm46(Tree):
    """Real RM46L852 (VIM): every GIO pin can use request GIO_A or GIO_B - two ranks at most."""

    def profile(self, ranks: dict):
        board = load(PROFILES / "boards/testboard_rm46/board.yaml")
        board["dio"] = {"inputs": [{"name": n, "pin": p} for n, p in
                                   (("IN_A", "GIOA_0"), ("IN_B", "GIOA_1"), ("IN_C", "GIOB_0"))]}
        project = load(PROFILES / "projects/testproject_rm46/project.yaml")
        project["use"]["dio"] = "all"
        project["pin_events"] = {n: event() for n in ranks}
        project["interrupts"]["ranks"] += [{"source": "dio", "name": n, "rank": r} for n, r in ranks.items()]
        return board, project

    def test_two_ranks_fit(self):
        config, messages = self.run_check(*self.profile({"IN_A": 2, "IN_B": 3, "IN_C": 2}),
                                          mcudb=REPO / "mcudb")
        self.assertEqual(self.errors(messages), [])
        self.assertEqual([(u.input, u.vector) for u in config.pin_events],
                         [("IN_A", "GIO_A"), ("IN_B", "GIO_B"), ("IN_C", "GIO_A")])

    def test_third_rank_has_no_request(self):
        _, messages = self.run_check(*self.profile({"IN_A": 2, "IN_B": 3, "IN_C": 4}), mcudb=REPO / "mcudb")
        self.assert_error(messages, "pin_events.IN_C", "vector GIO_A of RM46L852 already serves IN_A with rank 2 (pin event); "
                          "vector GIO_B of RM46L852 already serves IN_B with rank 3 (pin event); one vector has one "
                          "priority: give the inputs at most 2 different ranks")


class DemoCopy(Tree):
    """Copy of the full test profile (PLAT-62, testboard_full, S32K144): ports A..E, buttons and two Hall signals on
    PORTD, the third Hall signal on PORTE."""

    def demo(self, events: dict, ranks: dict, hall: bool = False):
        project = load(DEMO_PROJECT)
        project["project"]["board"] = (REPO / "tools" / "nxconfig" / "test" / "profiles" / "boards" / "testboard_full").as_posix()
        folder = self.tmp / "demo"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "rte.yaml").write_text(EMPTY_RTE, encoding="utf-8")
        project["project"]["rte"] = "rte.yaml"
        project["project"].pop("diag", None)
        (project.get("safety") or {}).pop("safe_state_output_shared_with_application", None)
        project["pin_events"] = events
        project["interrupts"]["ranks"] += [{"source": "dio", "name": n, "rank": r} for n, r in ranks.items()]
        if hall:
            project["modules"]["HALL1"] = {"arrangement": "deg120", "pole_pairs": 4, "min_speed_rpm": 100,
                                           "max_speed_rpm": 3000, "debounce_us": 20, "standstill_ms": 200,
                                           "forward": "sector_up"}
            project["interrupts"]["ranks"] += [{"source": "dio", "name": n, "rank": 2}
                                               for n in ("HALL_UX", "HALL_V", "HALL_WY")]
        path = folder / "project.yaml"
        path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        return nxconfig.check(path)

    def test_demo_itself_unchanged_and_without_messages(self):
        config, messages = nxconfig.check(DEMO_PROJECT)
        self.assertEqual([str(m) for m in messages], [])
        self.assertEqual(config.pin_events, [])
        self.assertNotIn("pin_events", load(DEMO_PROJECT))

    def test_button_event(self):
        config, messages = self.demo({"BUTTON1": event()}, {"BUTTON1": 6})
        self.assertEqual([str(m) for m in messages], [])
        (use,) = config.pin_events
        self.assertEqual((use.pin, use.vector, use.line, use.physical_edge, use.rank), ("PTD5", "PORTD", None,
                                                                                      "falling", 6))

    def test_same_port_same_rank(self):
        _, messages = self.demo({"BUTTON1": event(), "BUTTON2": event()}, {"BUTTON1": 6, "BUTTON2": 7})
        self.assert_error(messages, "pin_events.BUTTON2", "vector PORTD of S32K144 already serves BUTTON1 with rank 6")

    def test_hall_shares_the_port_vector(self):
        _, messages = self.demo({"BUTTON1": event()}, {"BUTTON1": 6}, hall=True)
        self.assert_error(messages, "pin_events.BUTTON1", "already serves HALL_UX, HALL_V with rank 2 (hall HALL1)")
        config, messages = self.demo({"BUTTON1": event()}, {"BUTTON1": 2}, hall=True)
        self.assertEqual(self.errors(messages), [])
        self.assertEqual(config.pin_events[0].shares, ("HALL_UX", "HALL_V"))

    def test_hall_signal_is_no_event_input(self):
        _, messages = self.demo({"HALL_V": event()}, {}, hall=True)
        self.assert_error(messages, "pin_events.HALL_V", "is a signal of the Hall sensor HALL1")


if __name__ == "__main__":
    unittest.main()
