"""Host tests of tools/schedana (TOOL-09).

Run: .venv/Scripts/python -m unittest tools/schedana/test_schedana.py

Test profiles: the constructed schedules in test/scenarios (load free, overloaded, priority inversion, data flow
mistakes) and the console captures in test/console. Only three tests use the demo project and the multi task
fixture of tools/rtegen, so nothing here depends on the eval board (review question 7).
"""

import copy
import json
import re
import subprocess
import sys
import unittest
import unittest.mock
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from schedana import cli, dataflow, measure, report, scenario, scheduling  # noqa: E402
from schedana.findings import ERROR, WARNING  # noqa: E402
from schedana.model import ModelError  # noqa: E402

SCENARIOS = HERE / "test" / "scenarios"
CONSOLE = HERE / "test" / "console"
# PLAT-62: the own full test profile (the demo component in one task on testboard_full) instead of the eval board
# project, which stays private (PLAT-46 F10) - a test against it would be red in the exported open tree. The console
# recordings of the eval board are private as well (tools/boardtest/captures): tests on them skip where they are missing.
DEMO = REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full"
BOARD_CAPTURE = REPO / "tools/boardtest/captures/evalboard_s32k144_demo/2026-09-16_measure.console.txt"
board_capture = unittest.skipUnless(BOARD_CAPTURE.is_file(),
                                    "private eval board recording not in this tree (exported open tree, PLAT-62)")
MULTITASK = REPO / "tools" / "rtegen" / "test" / "multitask"
IMPLICIT = REPO / "tools" / "rtegen" / "test" / "implicit"   # RTE-09: implicit access with a copy per runnable
UNCONNECTED = REPO / "tools" / "rtegen" / "test" / "unconnected"   # RTE-27: required ports without connection
RTE_SOURCE = REPO / "ecu" / "application" / "src" / "systems" / "RteSystem.cpp"
# SYS-13: the task, runnable and init lines of "rte runnables|json" are printed by the write* templates of
# RteConsole.h; RteSystem.cpp keeps the load test marker, the JSON header and the end marker.
RTE_CONSOLE = REPO / "ecu" / "application" / "src" / "systems" / "RteConsole.h"
PRINTF_CALL = r'out\.printf\(\s*((?:"(?:[^"\\]|\\.)*"\s*)+)'


def printf_formats(block: str) -> list:
    """Format strings (adjacent literals joined) of the out.printf calls in `block`, in source order."""
    return ["".join(re.findall(r'"((?:[^"\\]|\\.)*)"', chunk)) for chunk in re.findall(PRINTF_CALL, block)]


def function_body(source: str, name: str, file_name: str) -> str:
    """Body of the C++ function `name` (text between its braces), braces in literals and comments skipped.

    Same approach as tools/boardtest (TOOL-27): per function, not per case block, so moving a call between files
    does not matter, renaming or removing a write* function fails with its name."""
    match = re.search(rf"\b{re.escape(name)}\s*\([^)]*\)\s*{{", source)
    if not match:
        raise AssertionError(f"function {name} not found in {file_name}")
    depth, i = 1, match.end()
    while depth:
        if i >= len(source):
            raise AssertionError(f"function {name} in {file_name} has no closing brace")
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


def console_formats(*functions: str) -> list:
    """Format strings of the given write* functions of RteConsole.h, function by function."""
    source = RTE_CONSOLE.read_text(encoding="utf-8")
    formats = []
    for name in functions:
        found = printf_formats(function_body(source, name, RTE_CONSOLE.name))
        if not found:
            raise AssertionError(f"no out.printf in {name} ({RTE_CONSOLE.name})")
        formats += found
    return formats


def load_scenario(name: str):
    return scenario.load(SCENARIOS / f"{name}.yaml")


def analyse_scenario(name: str):
    return report.analyse(load_scenario(name))


def codes(result) -> set:
    return {f.code for f in result.findings}


def by_code(result, code: str) -> list:
    return [f for f in result.findings if f.code == code]


class ScenarioTest(unittest.TestCase):
    def test_load_free_system_has_no_error_and_no_warning(self):
        result = analyse_scenario("idle")
        self.assertEqual(result.counts[ERROR], 0, codes(result))
        self.assertEqual(result.counts[WARNING], 0, codes(result))
        load = result.scheduling["utilisation"]
        self.assertAlmostEqual(load["total_percent"], 2.35, places=2)
        self.assertEqual([e["percent"] for e in load["tasks"]], [1.0, 0.29, 0.5, 0.06])

    def test_response_time_of_the_lowest_priority_task(self):
        # base (C 100) + ctrl (65) + bus (50) + guard (30) + one ISR run (5) = 250 us, computed by hand
        result = analyse_scenario("idle")
        entry = next(e for e in result.scheduling["response_times"]["tasks"] if e["task"] == "base")
        self.assertEqual(entry["response_us"], 250.0)
        self.assertTrue(entry["converged"])
        self.assertEqual(entry["slack_us"], 9750.0)
        # "bus" is both a task and an ISR group name (as in the ECU application: task can and ISR group can)
        sources = {(i["kind"], i["source"]): float(i["us"]) for i in entry["interference"]}
        self.assertEqual(sources, {("task", "ctrl"): 65.0, ("task", "bus"): 50.0, ("task", "guard"): 30.0,
                                   ("isr", "bus"): 5.0})

    def test_simulation_agrees_with_the_response_time_analysis(self):
        result = analyse_scenario("idle")
        simulated = result.scheduling["hyperperiod"]["simulation"]["tasks"]
        for entry in result.scheduling["response_times"]["tasks"]:
            self.assertLessEqual(simulated[entry["task"]]["max_response_us"], entry["response_us"] + 1e-6,
                                 f"{entry['task']}: the simulation must stay below the upper bound")

    def test_overloaded_system(self):
        result = analyse_scenario("overloaded")
        self.assertIn("system_overloaded", codes(result))
        self.assertIn("deadline_missed", codes(result))
        self.assertIn("response_diverges", codes(result))
        self.assertIn("load_peak", codes(result))
        self.assertAlmostEqual(result.scheduling["utilisation"]["total_percent"], 103.0, places=2)
        ctrl = next(e for e in result.scheduling["response_times"]["tasks"] if e["task"] == "ctrl")
        self.assertEqual(ctrl["wcet_us"], 910.0)      # 10 overhead + 500 + 400 in the cycle with both runnables
        self.assertEqual(ctrl["response_us"], 1912.0)  # 910 + 2 blocking + 400 bus + 200 guard + 4 x 100 ISR
        self.assertLess(ctrl["slack_us"], 0)
        self.assertEqual([f.target for f in by_code(result, "task_overloaded")], [])

    def test_priority_inversion_is_a_warning_with_a_reason_and_no_error(self):
        result = analyse_scenario("priority_inversion")
        self.assertEqual(result.counts[ERROR], 0, codes(result))
        warning = by_code(result, "application_above_critical_bsw")
        self.assertEqual(len(warning), 1)
        self.assertEqual(warning[0].severity, WARNING)
        self.assertEqual(warning[0].detail["application_task"], "ctrl")
        self.assertEqual(warning[0].detail["bsw_task"], "bus")
        self.assertEqual(warning[0].detail["roles"], ["communication"])
        self.assertEqual(warning[0].detail["delay_us"], 300.0)
        self.assertIn("The choice stays yours", warning[0].message)
        self.assertEqual(warning[0].where, "tasks[2]")
        self.assertIn("deadline_tight", codes(result))

    def test_priority_warning_only_for_critical_roles(self):
        # base has the role io: an application task above it is not worth a warning
        data = yaml.safe_load((SCENARIOS / "priority_inversion.yaml").read_text(encoding="utf-8"))
        for task in data["tasks"]:
            if task["name"] == "bus":
                task["roles"] = ["io"]
        result = report.analyse(scenario.from_dict(data, "mutant"))
        self.assertNotIn("application_above_critical_bsw", codes(result))

    def test_dataflow_mistakes(self):
        result = analyse_scenario("dataflow_errors")
        found = codes(result)
        for code in ("reader_before_writer", "rate_transition_not_harmonic", "cross_task_age",
                     "atomic_hand_over", "signal_without_writer", "signal_without_reader", "dataflow_cycle"):
            self.assertIn(code, found)
        stale = by_code(result, "reader_before_writer")[0]
        self.assertEqual(stale.detail, {"reader": "Fast.First", "writer": "Fast.Second", "age_ms": 3})
        self.assertEqual(stale.where, "tasks[1].runnables[0]")
        ring_without_delay = dataflow.cycle_findings(
            [{"runnables": ["A", "B", "A"], "signals": ["x", "y"], "delay_cycles": 0, "delay_ms": 0,
              "tasks": ["t"]}])
        self.assertEqual(ring_without_delay[0].code, "dataflow_cycle_without_delay")
        self.assertEqual(ring_without_delay[0].severity, ERROR)
        ring = next(c for c in result.dataflow["cycles"] if len(c["runnables"]) == 4)
        self.assertEqual(ring["runnables"], ["Fast.First", "Slow.Run7ms", "Fast.Second", "Fast.First"])
        self.assertEqual(ring["delay_cycles"], 3)
        self.assertEqual(ring["delay_ms"], 23)
        ages = {f.detail["worst_age_ms"] for f in by_code(result, "cross_task_age")}
        self.assertEqual(ages, {10})

    def test_configuration_errors_name_the_path(self):
        base = yaml.safe_load((SCENARIOS / "idle.yaml").read_text(encoding="utf-8"))
        cases = [
            (lambda d: d["tasks"][1].update(unknown=1), "tasks[1].unknown: unknown key"),
            (lambda d: d["tasks"][1]["runnables"][0].update(offset_ms=3), "offset_ms: 3 ms is no multiple"),
            (lambda d: d["tasks"][1]["runnables"][0].update(offset_ms=10), "must be below the period"),
            (lambda d: d["tasks"][1].pop("priority"), "tasks[1].priority: value is required"),
            (lambda d: d["tasks"][1].update(kind="middleware"), "tasks[1].kind: expected bsw or application"),
            (lambda d: d["tasks"][1].update(name="base"), "tasks[1].name: base is used twice"),
            (lambda d: d["tasks"][1]["runnables"][0].update(period_ms=15), "no multiple of the period 10 ms"),
        ]
        for mutate, fragment in cases:
            data = copy.deepcopy(base)
            mutate(data)
            with self.assertRaises(ModelError) as ctx:
                scenario.from_dict(data, "test.yaml")
            self.assertIn(fragment, str(ctx.exception))

    def test_scenario_round_trip(self):
        model = load_scenario("dataflow_errors")
        again = scenario.from_dict(scenario.to_dict(model), "round-trip")
        self.assertEqual([(t.name, t.kind, t.os_priority, t.period_ms) for t in model.tasks],
                         [(t.name, t.kind, t.os_priority, t.period_ms) for t in again.tasks])
        self.assertEqual({s.name: sorted(a.label for a in s.accesses) for s in model.signals},
                         {s.name: sorted(a.label for a in s.accesses) for s in again.signals})
        self.assertEqual(report.analyse(model).to_dict()["findings"],
                         report.analyse(again).to_dict()["findings"])


class OffsetTest(unittest.TestCase):
    def scenario_with_two_slow_runnables(self, offsets):
        return scenario.from_dict({
            "schema_version": 1, "name": "offsets", "tick_us": 1000,
            "tasks": [{
                "name": "app", "kind": "application", "priority": 30, "period_ms": 10,
                "runnables": [
                    {"name": "A.Run100ms", "period_ms": 100, "offset_ms": offsets[0], "wcet_us": 400},
                    {"name": "A.Run50ms", "period_ms": 50, "offset_ms": offsets[1], "wcet_us": 300},
                    {"name": "A.Run10ms", "period_ms": 10, "wcet_us": 100},
                ],
            }],
        }, "offsets")

    def test_offsets_are_suggested_and_lower_the_peak(self):
        model = self.scenario_with_two_slow_runnables((0, 0))
        suggestions = scheduling.offset_suggestions(model)
        self.assertEqual(len(suggestions), 1)
        entry = suggestions[0]
        self.assertEqual(entry["peak_before_us"], 800.0)   # 100 + 300 + 400 in the same cycle
        self.assertEqual(entry["peak_after_us"], 500.0)    # 100 + 400 in one cycle, 100 + 300 in another
        self.assertTrue(entry["changed"])
        for name, offset in entry["offsets_ms"].items():
            runnable = model.runnable(name)
            self.assertEqual(offset % model.task("app").period_ms, 0)
            self.assertLess(offset, runnable.period_ms)

    def test_no_suggestion_when_the_offsets_are_already_good(self):
        model = self.scenario_with_two_slow_runnables((0, 10))
        best = self.scenario_with_two_slow_runnables((0, 10))
        self.assertEqual(scheduling.offset_suggestions(model), [])
        self.assertEqual(max(best.task("app").cycle_us(t) for t in range(best.task("app").hyper_ticks())), 500.0)


class MeasureTest(unittest.TestCase):
    @board_capture
    def test_board_capture_with_console_indent(self):
        # TOOL-05: the real console puts one space in front of every line; before the fix only "rte json" was read
        m = measure.parse(BOARD_CAPTURE.read_text(encoding="utf-8"))
        swc = m.tasks["swc"]
        self.assertEqual((swc.cycles, swc.load_percent, swc.stack_used), (37006, 0.40, 764))
        self.assertIn("stats cpu", m.sources)

    def test_board_capture(self):
        m = measure.parse((CONSOLE / "demo_board.txt").read_text(encoding="utf-8"))
        self.assertEqual(m.sources, ["rte runnables", "stats cpu", "stats stack"])
        swc = m.tasks["swc"]
        self.assertEqual((swc.context, swc.period_ms, swc.cycles, swc.overruns, swc.max_us), (3, 10, 1256, 0, 80))
        self.assertEqual((swc.load_percent, swc.avg_us, swc.stack_used, swc.stack_size), (0.33, 33.0, 676, 2048))
        self.assertEqual(swc.stack_free, 1372)
        self.assertEqual(m.runnables["DemoSwc.Run10ms"].calls, 1256)
        self.assertEqual(m.runnables["DemoSwc.Run100ms"].max_us, 9.0)
        self.assertTrue(m.runnables["DemoSwc.DemoSwc_Init"].init)
        self.assertEqual(m.window_us, 1000123.0)
        self.assertEqual(m.isrs["can"].runs, 213)
        self.assertAlmostEqual(m.isrs["can"].period_ms, 4.695, places=3)
        self.assertEqual(m.tasks["idle"].load_percent, 97.32)
        self.assertEqual(m.lines_ignored, 0)
        self.assertEqual(m.lines_noise, 3)  # the echo of the three commands
        self.assertEqual(m.problems, [])

    def test_json_capture_brings_minimum_mean_and_jitter(self):
        m = measure.parse((CONSOLE / "demo_board_json.txt").read_text(encoding="utf-8"))
        self.assertEqual(m.sources, ["rte json"])
        self.assertTrue(m.complete)
        swc = m.tasks["swc"]
        self.assertEqual((swc.min_us, swc.mean_us, swc.max_us), (28.0, 33.0, 80.0))
        self.assertEqual(swc.jitter_us, 27.0)  # 10014 - 9987 us between two cycle starts
        run10 = m.runnables["DemoSwc.Run10ms"]
        self.assertEqual((run10.min_us, run10.mean_us, run10.max_us, run10.jitter_us), (2.0, 3.0, 7.0, 5.0))
        self.assertEqual(m.up_us, 12563412.0)
        self.assertEqual(m.problems, [])

    def test_lost_and_garbled_lines_are_reported_not_ignored(self):
        m = measure.parse((CONSOLE / "demo_board_lossy.txt").read_text(encoding="utf-8"))
        self.assertEqual(m.lines_ignored, 3)  # truncated JSON line, "80 s" instead of "80 us", stack used > size
        self.assertFalse(m.complete)
        self.assertNotIn("DemoSwc.Run10ms", m.runnables)
        self.assertEqual(m.runnables["DemoSwc.Run100ms"].calls, 126)   # the line behind the lost one still counts
        self.assertIsNone(m.tasks["bsp"].stack_size)                   # implausible line dropped
        self.assertEqual(m.tasks["swc"].stack_used, 676)
        self.assertTrue(any("lost 1 of 2 runnable lines" in p for p in m.problems))
        self.assertTrue(any("used 588 > size 204" in p for p in m.problems))

    def test_stats_rows_without_a_header_are_assigned_by_name(self):
        text = ("bsp              1.07 %        10 ms    125      101 us     42 us    134 us\n"
                "can              0.45 %         4 ms    213       21 us      9 us     34 us\n")
        m = measure.parse(text, known_tasks=["bsp"], known_isrs=["can"])
        self.assertEqual(m.tasks["bsp"].avg_us, 101.0)
        self.assertEqual(m.isrs["can"].runs, 213)
        m2 = measure.parse(text)  # without the model: everything counts as a task, with a remark
        self.assertIn("can", m2.tasks)
        self.assertTrue(any("without table header" in p for p in m2.problems))

    def test_noise_and_control_characters_do_not_break_the_parser(self):
        text = ("> rte runnables\r\n"
                "\x00task swc (context 3): every 10 ms, cycles 5, overruns 0, max 80 us\r\n"
                "1234 DEBUG some log line from another task\r\n"
                "  DemoSwc.Run10ms: every 10 ms, offset 0 ms, calls 5, last 3 us, max 7 us\r\n")
        m = measure.parse(text)
        self.assertEqual(m.tasks["swc"].cycles, 5)
        self.assertEqual(m.runnables["DemoSwc.Run10ms"].calls, 5)
        self.assertEqual(m.lines_ignored, 1)

    def test_measured_values_land_in_the_model(self):
        model = load_scenario("idle")
        text = ("task ctrl (context 2): every 10 ms, cycles 100, overruns 0, max 70 us\n"
                "  Ctrl.Sense10ms: every 10 ms, offset 0 ms, calls 100, last 18 us, max 22 us\n"
                "  Ctrl.Log100ms: every 100 ms, offset 0 ms, calls 10, last 30 us, max 44 us\n"
                "task                    %        total   runs      avg      min      max\n"
                "base             1.00 %        10 ms    100      100 us     42 us    120 us\n"
                "\n"
                "isr group               %        total   runs      avg      min      max\n"
                "bus              0.50 %         5 ms    950       5 us      3 us      9 us\n"
                "\n"
                "measurement time:  1000000 us\n")
        m = measure.parse(text, known_tasks=[t.name for t in model.tasks],
                          known_isrs=[i.name for i in model.isrs])
        remarks = measure.apply_to(model, m)
        self.assertEqual(model.runnable("Ctrl.Sense10ms").wcet_us, 22.0)
        self.assertEqual(model.runnable("Ctrl.Log100ms").wcet_us, 44.0)
        self.assertEqual(model.task("ctrl").overhead_us, 4.0)   # 70 measured cycle - 22 - 44
        self.assertEqual(model.task("base").wcet_us, 120.0)
        isr = model.isrs[0]
        self.assertEqual(isr.wcet_us, 9.0)
        self.assertEqual(isr.period_ms, 1.0)  # 1.0526 ms measured, rounded down to the OS tick grid
        self.assertTrue(any("rounded down to the OS tick grid" in r for r in remarks))

    def test_json_format_of_the_target_matches_the_parser(self):
        """Builds sample lines from the printf formats of the firmware and parses them.

        This is the contract between the firmware and the parser: renaming a key on either side fails here.
        Header and end marker come from RteSystem.cpp, the task/runnable/init objects from RteConsole.h (SYS-13).
        """
        source = RTE_SOURCE.read_text(encoding="utf-8")
        block = source.split("case ID_JSON:", 1)[1].split("case ID_RESET:", 1)[0]
        self.assertIn("Format::JSON", block, "rte json must print the statistics of RteConsole.h as JSON")
        frame = printf_formats(block)
        self.assertGreaterEqual(len(frame), 2, "rte json: header and end marker expected in RteSystem.cpp")
        body = console_formats("writeJsonTask", "writeJsonRunnable", "writeJsonInit")
        formats = frame[:-1] + body + frame[-1:]          # header ... objects ... end marker
        text, line = "", ""
        counter = iter(range(3, 400))
        for fmt in formats:
            fmt = fmt.replace('\\"', '"').replace("\\r\\n", "\n")
            fmt = re.sub(r"%s", lambda _: "swc" if '"task"' in fmt or '"name"' in fmt else "x", fmt, count=1)
            fmt = re.sub(r"%s", "DemoSwc.Run10ms", fmt)
            fmt = re.sub(r"%u", lambda _: str(next(counter)), fmt)
            line += fmt
            if line.endswith("\n"):
                text += line
                line = ""
        self.assertEqual(line, "", "every JSON line must end with \\r\\n")
        self.assertGreaterEqual(text.count("\n"), 5)
        m = measure.parse(text)
        self.assertEqual(m.lines_ignored, 0, text)
        self.assertEqual(m.sources, ["rte json"])
        self.assertIn("swc", m.tasks)
        self.assertIn("DemoSwc.Run10ms", m.runnables)
        self.assertTrue(m.announced.get("end_seen"))
        for key in ("min_us", "mean_us", "max_us", "min_delta_us", "max_delta_us"):
            self.assertIn(key, "".join(body), f"{key} must stay in the JSON output")

    def test_text_format_of_the_target_matches_the_parser(self):
        source = RTE_SOURCE.read_text(encoding="utf-8")
        block = source.split("case ID_RUNNABLES:", 1)[1].split("case ID_JSON:", 1)[0]
        self.assertIn("Format::TEXT", block, "rte runnables must print the statistics of RteConsole.h as text")
        formats = printf_formats(block) + console_formats("writeTaskLine", "writeRunnableLine", "writeInitLine")
        self.assertEqual(len(formats), 4)  # load test marker (RteSystem.cpp), task, runnable, init line (RteConsole.h)
        text = ""
        for fmt in formats:
            fmt = fmt.replace("\\r\\n", "\n")
            fmt = re.sub(r"%s", "swc" if "task %s" in fmt else "DemoSwc.Run10ms", fmt, count=1)
            fmt = re.sub(r"%u", lambda _: "7", fmt)
            text += fmt
        m = measure.parse(text)
        self.assertEqual(m.lines_ignored, 0, text)
        self.assertEqual(m.tasks["swc"].cycles, 7)
        self.assertEqual(m.runnables["DemoSwc.Run10ms"].calls, 7)
        self.assertEqual(m.load_test, {"runnable_index": 7, "us": 7})


class ProjectTest(unittest.TestCase):
    """The three tests that use the repository: the demo project and the multi task fixture of tools/rtegen."""

    @classmethod
    def setUpClass(cls):
        from schedana import rteload
        cls.rteload = rteload
        cls.model = rteload.load(DEMO)

    def test_demo_project_configuration(self):
        model = self.model
        self.assertEqual([(t.name, t.os_priority, t.kind) for t in model.tasks],
                         [("background", 1, "bsw"), ("bsp", 2, "bsw"), ("swc", 3, "application"),
                          ("can", 4, "bsw"), ("sysadmin", 5, "bsw"), ("safety", 6, "bsw")])
        self.assertEqual([i.name for i in model.isrs], ["test", "can"])
        self.assertEqual(model.tick_us, 1000)
        self.assertEqual([(r.name, r.period_ms, r.offset_ms, r.order) for r in model.runnables()],
                         [("DemoSwc.Run10ms", 10, 0, 0), ("DemoSwc.Run100ms", 100, 0, 1)])
        self.assertEqual(model.task("swc").stack_bytes, 2048)
        self.assertTrue(model.task("safety").above_application)
        self.assertEqual(model.task("can").roles, ("communication",))
        self.assertEqual(float(model.hyperperiod_ms()), 100.0)
        self.assertFalse(model.timing_complete())
        # the demo has one application task, so no buffer is shared and nothing is handed over between tasks
        self.assertEqual({s.consistency for s in model.signals}, {"single_task"})
        button = model.signal("DemoSwc.Button1.Button1")
        self.assertEqual([a.label for a in button.accesses],
                         ["readInputs(swc) dio.in.BUTTON1", "DemoSwc.Run10ms"])
        led = model.signal("DemoSwc.Led1.Led1")
        self.assertEqual(sorted(a.label for a in led.accesses),
                         ["DemoSwc.Run10ms", "writeOutputs(swc) can.tx.EVB_Status",
                          "writeOutputs(swc) dio.out.LED1"])

    def test_demo_project_with_the_board_measurement(self):
        model = self.rteload.load(DEMO)
        m = measure.parse((CONSOLE / "demo_board.txt").read_text(encoding="utf-8"),
                          known_tasks=[t.name for t in model.tasks], known_isrs=[i.name for i in model.isrs])
        remarks = measure.apply_to(model, m)
        result = report.analyse(model, m, remarks)
        self.assertEqual(result.counts[ERROR], 0, codes(result))
        self.assertEqual(result.counts[WARNING], 0, codes(result))
        swc = next(e for e in result.scheduling["utilisation"]["tasks"] if e["task"] == "swc")
        self.assertEqual(swc["worst_cycle_us"], 80.0)          # measured longest cycle of the task
        self.assertAlmostEqual(swc["utilisation"], (64 + 7) / 10000.0 + 9 / 100000.0, places=6)
        comparison = by_code(result, "load_measured_vs_analysed")[0]
        self.assertAlmostEqual(comparison.detail["measured_percent"], 2.68, places=2)
        self.assertGreater(comparison.detail["analysed_percent"], comparison.detail["measured_percent"])
        self.assertIn("signal_without_reader", codes(result))  # the monitored PC_Command signals
        json.loads(result.to_json())

    def test_demo_project_with_the_overrun_capture(self):
        model = self.rteload.load(DEMO)
        m = measure.parse((CONSOLE / "demo_overrun.txt").read_text(encoding="utf-8"),
                          known_tasks=[t.name for t in model.tasks], known_isrs=[i.name for i in model.isrs])
        result = report.analyse(model, m, measure.apply_to(model, m))
        found = codes(result)
        for code in ("measured_overruns", "task_overloaded", "system_overloaded", "cycle_close_to_period",
                     "stack_reserve_low", "deadline_missed", "load_test_build"):
            self.assertIn(code, found)
        self.assertEqual(m.load_test, {"runnable_index": 0, "us": 12000})
        overruns = by_code(result, "measured_overruns")[0]
        self.assertEqual(overruns.detail, {"overruns": 1198, "cycles": 1200})
        self.assertEqual(overruns.where, "tasks[0]")

    def test_multitask_fixture_two_tasks_with_hand_over(self):
        """The RTE-02 host test fixture: two application tasks, its own BSW task profile, copies and locks."""
        from schedana import rteload
        config = rteload.nxconfig.load(DEMO / "project.yaml")  # board signals of the fixture connections
        model = rteload.load_rte(MULTITASK / "rte.yaml", config, repo=REPO,
                                 tasks_manifest=MULTITASK / "tasks.yaml")
        self.assertEqual([(t.name, t.os_priority) for t in model.tasks],
                         [("comm", 1), ("app", 2), ("motor", 3), ("io", 4), ("guard", 5)])
        self.assertEqual(model.tick_us, 500)
        self.assertEqual([t.period_ms for t in model.application_tasks()], [10, 1])
        self.assertEqual(model.runnable("AppSwc.Run50ms").offset_ms, 20)
        # Speed/Wide go from motor to app: copies at the task transition
        speed = model.signal("MotorSwc.Speed.Speed")
        self.assertEqual(speed.consistency, "task_copy")
        # Setpoint is read by MotorSwc in two tasks: atomic hand over
        self.assertEqual(model.signal("AppSwc.Setpoint.Setpoint").consistency, "atomic")
        result = report.analyse(model)
        self.assertIn("cross_task_age", codes(result))
        self.assertIn("atomic_hand_over", codes(result))
        ages = {f.detail["worst_age_ms"] for f in by_code(result, "cross_task_age")}
        self.assertEqual(ages, {11})  # 1 ms writer cycle + 10 ms reader cycle
        # motor (1 ms) runs above app (10 ms): rate monotonic, so no remark about the priority order
        self.assertNotIn("not_rate_monotonic", codes(result))
        # no implicit access in this fixture: no RTE-09 section in the report
        self.assertNotIn("implicit_access", result.to_dict())
        self.assertNotIn("Implizite Zugriffe", result.to_text())

    def test_implicit_fixture_copies_per_runnable(self):
        """RTE-09: the implicit access fixture of tools/rtegen - copies per runnable (memory, lock sections), an
        implicit reader of an atomic signal keeps its value for the whole run, the explicit reader does not."""
        from schedana import rteload
        config = rteload.nxconfig.load(DEMO / "project.yaml")
        model = rteload.load_rte(IMPLICIT / "rte.yaml", config, repo=REPO, tasks_manifest=MULTITASK / "tasks.yaml")
        run1ms = model.runnable("ProducerSwc.Run1ms")
        self.assertEqual((run1ms.implicit_bytes, run1ms.implicit_locks), (18, 0))      # task copy: no lock
        fast = model.runnable("ConsumerSwc.RunFast1ms")
        self.assertEqual((fast.implicit_bytes, fast.implicit_locks), (4, 1))           # one lock before, none after
        self.assertEqual((model.runnable("ConsumerSwc.RunImp10ms").implicit_bytes,
                          model.runnable("ConsumerSwc.RunImp10ms").implicit_locks), (22, 1))
        self.assertEqual(model.runnable("ConsumerSwc.RunExp10ms").implicit_bytes, 0)   # explicit only
        count = model.signal("ProducerSwc.Count.Count")
        self.assertEqual(count.consistency, "atomic")
        readers = {(a.runnable, a.where, a.implicit) for a in count.readers()}
        self.assertEqual(readers, {("ConsumerSwc.RunFast1ms", "ConsumerSwc.CountImp.Count", True),
                                   ("ConsumerSwc.RunFast1ms", "ConsumerSwc.CountExp.Count", False),
                                   ("ConsumerSwc.RunImp10ms", "ConsumerSwc.CountImp.Count", True),
                                   ("ConsumerSwc.RunExp10ms", "ConsumerSwc.CountExp.Count", False)})
        writer = next(a for a in count.writers() if a.runnable == "ProducerSwc.Run1ms")
        self.assertTrue(writer.implicit)
        result = report.analyse(model)
        implicit = [f for f in by_code(result, "implicit_hand_over")]
        self.assertEqual({f.detail["for"] for f in implicit}, {"ConsumerSwc.RunImp10ms"})
        atomic = {f.detail["for"] for f in by_code(result, "atomic_hand_over")}
        self.assertIn("ConsumerSwc.RunExp10ms", atomic)                                 # the counter-check
        self.assertNotIn("ConsumerSwc.RunImp10ms", atomic)
        data = result.to_dict()
        self.assertEqual(data["implicit_access"]["bytes"], 18 + 4 + 22)                  # the init runnable has no task
        self.assertEqual(list(data)[list(data).index("dataflow") + 1], "implicit_access")
        signal = next(s for s in data["dataflow"]["signals"] if s["signal"] == "ProducerSwc.Count.Count")
        self.assertIn({"task": "slow", "by": "ConsumerSwc.RunImp10ms", "order": 0, "implicit": True},
                      signal["readers"])
        text = result.to_text()
        self.assertIn("Implizite Zugriffe (Kopie je Runnable)", text)
        self.assertIn("3 Runnables, 44 Byte Kopien", text)
        json.loads(result.to_json())

    def test_unconnected_ports_are_a_warning(self):
        """RTE-27 (Studio test 2026-09-28: the schedule analysis stopped at 'required port ... is not connected'):
        the profile tools/rtegen/test/unconnected loads, every unconnected port is one warning with the text of
        rtegen at connections, no second 'signal_without_writer' for it; its runnables are analysed as usual."""
        from schedana import rteload
        config = rteload.nxconfig.load(DEMO / "project.yaml")
        model = rteload.load_rte(UNCONNECTED / "rte.yaml", config, repo=REPO,
                                 tasks_manifest=MULTITASK / "tasks.yaml")
        self.assertIsNotNone(model.runnable("InitSinkSwc.Run10ms"))
        result = report.analyse(model)
        found = by_code(result, "unconnected_port")
        self.assertEqual(sorted(f.target for f in found),
                         ["ConsumerSwc.CountExp.Count", "ConsumerSwc.CountImp.Count", "ConsumerSwc.Pair.Pair",
                          "ConsumerSwc.Samples.Samples", "InitSinkSwc.LevelQlf.LevelQlf",
                          "InitSinkSwc.Level_mV.Level_mV"])
        self.assertEqual({(f.severity, f.where) for f in found}, {(WARNING, "connections")})
        qlf = next(f for f in found if f.target == "InitSinkSwc.LevelQlf.LevelQlf")
        self.assertTrue(qlf.message.startswith("required port InitSinkSwc.LevelQlf.LevelQlf is not connected: it "
                                               "reads QLF_NOT_AVAILABLE (64, no source)"), qlf.message)
        self.assertEqual(by_code(result, "signal_without_writer"), [])
        self.assertEqual(result.counts[ERROR], 0)
        self.assertIn("unconnected_port", result.to_json())

    def test_budget_file(self):
        model = self.rteload.load(DEMO)
        budget = self.rteload.load_budget(HERE / "test" / "budget_demo.yaml")
        remarks = self.rteload.apply_budget(model, budget, "budget_demo.yaml")
        self.assertEqual(remarks, [])
        self.assertEqual(model.max_lock_us, 1.71)
        self.assertEqual(model.task("bsp").wcet_us, 157)
        self.assertEqual(model.task("can").deadline_ms, 5)
        self.assertEqual([i.period_ms for i in model.isrs], [1000, 1])
        # the budget covers the BSW tasks and the ISR groups; the runtime of a runnable only comes from the target
        self.assertFalse(model.timing_complete())
        self.assertIn("runtime_missing", codes(report.analyse(model)))
        result = report.analyse(model)
        self.assertEqual(result.counts[ERROR], 0, codes(result))
        for entry in result.scheduling["response_times"]["tasks"]:
            self.assertEqual(entry["blocking_us"], 1.71)

    def test_budget_errors_name_the_path(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "b.yaml"
            path.write_text("tasks:\n  bsp:\n    wcet: 10\n", encoding="utf-8")
            with self.assertRaises(ModelError) as ctx:
                self.rteload.load_budget(path)
            self.assertIn("b.yaml: tasks.bsp.wcet: unknown key", str(ctx.exception))


class ReportTest(unittest.TestCase):
    def test_json_report(self):
        result = analyse_scenario("overloaded")
        data = json.loads(result.to_json())
        self.assertEqual(data["schema"], "schedana/1")
        for key in ("system", "assumptions", "utilisation", "response_times", "hyperperiod",
                    "offset_suggestions", "priorities", "dataflow", "findings", "summary"):
            self.assertIn(key, data)
        self.assertEqual(data["summary"]["ok"], False)
        self.assertEqual(data["system"]["application_tasks"], ["ctrl"])
        self.assertTrue(all({"severity", "code", "message", "target", "where", "detail"} <= set(f)
                            for f in data["findings"]))
        self.assertEqual(len(data["assumptions"]), 7)

    def test_text_report(self):
        text = analyse_scenario("dataflow_errors").to_text(verbose=True)
        for heading in ("Auslastung je Task", "Antwortzeitanalyse", "Prioritaeten", "Datenfluss", "Befunde",
                        "Annahmen"):
            self.assertIn(heading, text)
        self.assertIn("FEHLER", analyse_scenario("overloaded").to_text())

    def test_findings_carry_a_place(self):
        result = analyse_scenario("dataflow_errors")
        for finding in result.findings:
            self.assertTrue(finding.target, finding)
            if finding.code in ("reader_before_writer", "application_above_critical_bsw"):
                self.assertTrue(finding.where.startswith("tasks["), finding.where)


class SensitivityTest(unittest.TestCase):
    """Deliberately introduced mistakes: every one of them must show up as a finding (Aussagekraft)."""

    def mutate(self, name: str, mutate) -> set:
        data = yaml.safe_load((SCENARIOS / f"{name}.yaml").read_text(encoding="utf-8"))
        mutate(data)
        return codes(report.analyse(scenario.from_dict(data, f"{name}-mutant")))

    def test_introduced_mistakes_are_found(self):
        cases = [
            # (scenario, mutation, expected code)
            ("idle", lambda d: d["tasks"][1]["runnables"][0].update(wcet_us=20000), "task_overloaded"),
            ("idle", lambda d: d["isrs"][0].update(wcet_us=1200), "system_overloaded"),
            ("idle", lambda d: d["isrs"][0].update(wcet_us=850), "load_high"),
            ("idle", lambda d: d["tasks"][1].update(priority=45), "application_above_critical_bsw"),
            ("idle", lambda d: d["tasks"][1].update(deadline_ms=0.1), "deadline_missed"),
            ("idle", lambda d: d["tasks"][1].update(deadline_ms=0.18), "deadline_tight"),
            ("idle", lambda d: d["tasks"][1]["runnables"].reverse(), "reader_before_writer"),
            ("idle", lambda d: d["tasks"][1]["runnables"][0].update(writes=[]), "signal_without_writer"),
            ("idle", lambda d: d["tasks"][1]["runnables"][1].update(reads=[]), "signal_without_reader"),
            ("idle", lambda d: d["tasks"][2].update(period_ms=100), "not_rate_monotonic"),
            ("idle", lambda d: d["tasks"][1]["runnables"][0].pop("wcet_us"), "runtime_missing"),
            ("idle", lambda d: d["tasks"][2].pop("wcet_us"), "interference_unknown"),
            # 21 ms is a multiple of 3 ms: the beat turns into a plain rate transition
            ("dataflow_errors", lambda d: d["tasks"][2]["runnables"][0].update(period_ms=21),
             "rate_transition_up"),
            ("overloaded", lambda d: d["tasks"][2].update(priority=30), "priority_used_twice"),
            ("overloaded", lambda d: d["tasks"][1].update(priority=70), "above_application_violated"),
        ]
        for name, mutate, expected in cases:
            with self.subTest(scenario=name, expected=expected):
                found = self.mutate(name, mutate)
                self.assertIn(expected, found)
                if expected != "not_rate_monotonic":
                    self.assertNotIn(expected, codes(analyse_scenario(name)),
                                     "the finding must not already be there without the mistake")

    def test_measurement_mistakes_are_found(self):
        model = load_scenario("idle")
        text = ("task ctrl (context 2): every 10 ms, cycles 100, overruns 17, max 9500 us\n"
                "  Ctrl.Sense10ms: every 5 ms, offset 0 ms, calls 200, last 18 us, max 22 us\n"
                "stack:task=ctrl,size=1024,used=990\n")
        m = measure.parse(text, known_tasks=[t.name for t in model.tasks])
        result = report.analyse(model, m, measure.apply_to(model, m))
        found = codes(result)
        for code in ("measured_overruns", "stack_reserve_low", "cycle_close_to_period",
                     "measured_period_differs"):
            self.assertIn(code, found)
        self.assertEqual(by_code(result, "stack_reserve_low")[0].severity, ERROR)  # 96 % of the stack

    def test_removing_a_check_is_noticed(self):
        """A guard against a silently disabled check: the four groups must each produce at least one finding."""
        groups = {
            "scheduling": scheduling.scheduling_analysis(load_scenario("overloaded"))["findings"],
            "dataflow": dataflow.analyse(load_scenario("dataflow_errors"))["findings"],
        }
        for name, findings in groups.items():
            self.assertTrue(findings, name)
        from schedana import priorities
        model = load_scenario("priority_inversion")
        result = priorities.analyse(model, scheduling.response_times(model))
        self.assertTrue(result["findings"])
        self.assertTrue(report.measurement_findings(
            load_scenario("idle"),
            measure.parse("task ctrl (context 2): every 10 ms, cycles 9, overruns 9, max 90000 us\n")))


class CliTest(unittest.TestCase):
    def test_analyse_text_and_json(self):
        from io import StringIO
        out = StringIO()
        old, sys.stdout = sys.stdout, out
        try:
            code = cli.main(["analyse", "--scenario", str(SCENARIOS / "idle.yaml")])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        self.assertIn("Auslastung je Task", out.getvalue())

        out = StringIO()
        old, sys.stdout = sys.stdout, out
        try:
            code = cli.main(["analyse", "--scenario", str(SCENARIOS / "overloaded.yaml"), "--json", "-"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 1)  # errors -> exit code 1
        self.assertEqual(json.loads(out.getvalue())["summary"]["errors"], 5)

    def test_fail_on(self):
        argv = ["analyse", "--scenario", str(SCENARIOS / "priority_inversion.yaml")]
        from io import StringIO
        for option, expected in (("none", 0), ("error", 0), ("warning", 1)):
            out = StringIO()
            old, sys.stdout = sys.stdout, out
            try:
                code = cli.main(argv + ["--fail-on", option])
            finally:
                sys.stdout = old
            self.assertEqual(code, expected, option)

    def test_measure_subcommand_reads_a_file(self):
        from io import StringIO
        out = StringIO()
        old, sys.stdout = sys.stdout, out
        try:
            code = cli.main(["measure", "--input", str(CONSOLE / "demo_board.txt"), "--json"])
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        data = json.loads(out.getvalue())
        self.assertEqual(data["tasks"]["swc"]["cycles"], 1256)

    def test_error_message_instead_of_a_traceback(self):
        from io import StringIO
        err = StringIO()
        old, sys.stderr = sys.stderr, err
        try:
            code = cli.main(["analyse", "--scenario", str(SCENARIOS / "does_not_exist.yaml")])
        finally:
            sys.stderr = old
        self.assertEqual(code, 2)
        self.assertIn("schedana: error:", err.getvalue())

    def test_core_runs_without_qt(self):
        """Review question 8: the core stays without Qt, so a web frontend can use it (TOOL-02/TOOL-10)."""
        code = ("import sys; sys.path.insert(0, %r);\n"
                "import schedana, schedana.cli, schedana.rteload, schedana.report\n"
                "bad = [m for m in sys.modules if m.split('.')[0] in ('PySide6', 'shiboken6', 'PyQt5')]\n"
                "print('QT:' + ','.join(bad))\n") % str(HERE.parent)
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(REPO))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("QT:\n", result.stdout.replace("\r\n", "\n"))

    def test_no_qt_import_in_the_sources(self):
        for path in sorted(HERE.glob("*.py")):
            if path.name == Path(__file__).name:
                continue  # this file names the forbidden modules in the check above
            text = path.read_text(encoding="utf-8")
            for forbidden in ("PySide6", "shiboken6", "PyQt"):
                self.assertNotIn(forbidden, text, f"{path.name} must stay free of Qt")


MODES_PROFILE = REPO / "tools" / "rtegen" / "test" / "modes"   # RTE-08 test profile of tools/rtegen


def mode_console(fmt_values: list) -> str:
    """Lines of writeModeLine/writeJsonMode (RteConsole.h) rendered with the given values, one list per line:
    strings for %s, numbers for %u, in order."""
    text = ""
    for fmts, values in fmt_values:
        strings = iter(v for v in values if isinstance(v, str))
        numbers = iter(v for v in values if not isinstance(v, str))
        line = "".join(fmts).replace('\\"', '"').replace("\\r\\n", "\n")
        line = re.sub(r"%s", lambda _: next(strings), line)
        line = re.sub(r"%u", lambda _: str(next(numbers)), line)
        text += line
    return text


class ModeSwitchTest(unittest.TestCase):
    """RTE-17: mode switch runnables (RTE-08) as event driven load per task - test profile scenarios/modes.yaml
    (values computed by hand in the file) and the RTE-08 test profile of tools/rtegen."""

    def test_worst_switch_per_task(self):
        model = load_scenario("modes")
        slow, fast = model.task("slow"), model.task("fast")
        self.assertEqual([r.label for r in slow.mode_runnables],
                         ["on entry STARTUP", "on entry RUN", "on exit RUN", "on exit STANDBY",
                          "on transition STANDBY -> RUN"])
        switch = slow.mode_switch()
        self.assertEqual(switch["us"], 300.0)
        self.assertEqual((switch["from"], switch["to"], switch["initial"]), ("STANDBY", "RUN", False))
        self.assertEqual(switch["runnables"], ["Slow.OnStandbyExit (on exit STANDBY)",
                                               "Slow.OnWake (on transition STANDBY -> RUN)",
                                               "Slow.OnRunEntry (on entry RUN)"])
        self.assertEqual(fast.mode_switch()["us"], 30.0)            # names in lower case are ECU modes, too
        # the first cycle enters STARTUP, then switches on: with a large STARTUP entry that is the worst switch
        model.task("slow").mode_runnables[0].wcet_us = 400
        switch = model.task("slow").mode_switch()
        self.assertEqual((switch["us"], switch["initial"], switch["to"]), (600.0, True, "RUN"))

    def test_response_time_with_mode_switches(self):
        result = analyse_scenario("modes")
        times = {e["task"]: e for e in result.scheduling["response_times"]["tasks"]}
        self.assertEqual(times["fast"]["wcet_us"], 130.0)
        self.assertEqual(times["fast"]["response_us"], 130.0)
        self.assertEqual(times["slow"]["wcet_us"], 800.0)           # 500 periodic + 300 worst switch
        self.assertEqual(times["slow"]["response_us"], 930.0)       # + 100 fast cycle + 30 fast switch (A8)
        self.assertIn({"source": "fast", "kind": "mode_switch", "jobs": 1, "us": 30.0},
                      times["slow"]["interference"])
        self.assertEqual(times["slow"]["mode_switch_us"], 300.0)
        # event driven: not part of the long term utilisation
        load = {e["task"]: e for e in result.scheduling["utilisation"]["tasks"]}
        self.assertEqual((load["slow"]["percent"], load["fast"]["percent"]), (5.0, 10.0))
        self.assertEqual(load["slow"]["mode_switch_us"], 300.0)
        self.assertEqual(sorted(f.target for f in by_code(result, "mode_switch_load")), ["fast", "slow"])
        switches = {e["task"]: e for e in result.scheduling["mode_switches"]}
        self.assertEqual(switches["slow"]["worst_cycle_with_switch_us"], 800.0)
        # report: JSON section, text section and assumption A8
        data = json.loads(result.to_json())
        self.assertEqual([e["task"] for e in data["mode_switches"]], ["slow", "fast"])
        self.assertEqual(list(data).index("mode_switches"), list(data).index("offset_suggestions") + 1)
        self.assertTrue(data["assumptions"][-1].startswith("A8 "))
        text = result.to_text()
        self.assertIn("Task slow: schlimmster Wechsel STANDBY -> RUN 300 us, mit dem laengsten periodischen Zyklus "
                      "800 us", text)
        self.assertIn("    Slow.OnWake on transition STANDBY -> RUN: C 60 us *", text)
        # a value written by a mode switch runnable: no rate (event driven), no "swap the entries" advice
        self.assertFalse([f for f in by_code(result, "rate_transition_up") if "OnRunEntry" in f.message])
        self.assertFalse([f for f in by_code(result, "reader_before_writer") if "OnRunEntry" in f.message])

    def test_without_mode_runnables_the_report_is_unchanged(self):
        """Systems without mode switch runnables: no new key, no new line, no new assumption (the byte comparison
        of all projects and scenarios before/after RTE-17 is in the changelog; this keeps it)."""
        data = yaml.safe_load((SCENARIOS / "modes.yaml").read_text(encoding="utf-8"))
        for task in data["tasks"]:
            task.pop("mode_runnables")
        result = report.analyse(scenario.from_dict(data, "without"))
        text, as_json = result.to_text(), result.to_json()
        for word in ("mode_switch", "Moduswechsel", "A8 ", "mode_runnables"):
            self.assertNotIn(word, text + as_json)
        self.assertEqual(result.to_dict()["assumptions"], report.ASSUMPTIONS)
        for name in ("idle", "overloaded", "priority_inversion", "dataflow_errors"):
            dump = analyse_scenario(name).to_json() + analyse_scenario(name).to_text()
            self.assertNotIn("mode", dump.lower().replace("model", ""), name)

    def test_missing_runtime_of_a_mode_switch_runnable(self):
        data = yaml.safe_load((SCENARIOS / "modes.yaml").read_text(encoding="utf-8"))
        del data["tasks"][0]["mode_runnables"][2]["wcet_us"]           # Slow.OnRunExit
        result = report.analyse(scenario.from_dict(data, "missing"))
        slow = next(e for e in result.scheduling["response_times"]["tasks"] if e["task"] == "slow")
        self.assertIsNone(slow["mode_switch_us"])
        self.assertEqual(slow["wcet_us"], 500.0)                       # without a switch, and said so
        missing = by_code(result, "mode_runtime_missing")
        self.assertEqual(len(missing), 1)
        self.assertIn("Slow.OnRunExit (on exit RUN)", missing[0].message)
        self.assertFalse(result.model.timing_complete())

    def test_scenario_errors_name_the_path(self):
        base = yaml.safe_load((SCENARIOS / "modes.yaml").read_text(encoding="utf-8"))
        cases = [
            (lambda d: d["tasks"][0]["mode_runnables"][0].update(event="enter"),
             "tasks[0].mode_runnables[0].event: expected exit, transition, entry"),
            (lambda d: d["tasks"][0]["mode_runnables"][4].pop("from"),
             "tasks[0].mode_runnables[4].from: a transition needs the mode it leaves"),
            (lambda d: d["tasks"][0]["mode_runnables"][0].update({"from": "RUN"}),
             "tasks[0].mode_runnables[0].from: only a transition has a mode it leaves"),
            (lambda d: d["tasks"][0]["mode_runnables"][4].update({"from": "run"}),
             "a transition from RUN to itself never happens"),
            (lambda d: d["tasks"][0]["mode_runnables"][1].update(period_ms=10),
             "tasks[0].mode_runnables[1].period_ms: unknown key"),
            (lambda d: d["tasks"][0]["mode_runnables"][1].pop("mode"),
             "tasks[0].mode_runnables[1].mode: expected an ECU mode"),
            (lambda d: d["tasks"][1].update(kind="bsw"),
             "tasks[1].mode_runnables: only an application task runs mode switch runnables"),
        ]
        for mutate, fragment in cases:
            data = copy.deepcopy(base)
            mutate(data)
            with self.assertRaises(ModelError) as ctx:
                scenario.from_dict(data, "mutant")
            self.assertIn(fragment, str(ctx.exception))

    def test_scenario_round_trip(self):
        model = load_scenario("modes")
        again = scenario.from_dict(scenario.to_dict(model), "again")
        self.assertEqual([(r.name, r.label, r.wcet_us, r.writes) for r in again.mode_runnables()],
                         [(r.name, r.label, r.wcet_us, r.writes) for r in model.mode_runnables()])
        self.assertEqual(report.analyse(again).to_dict()["response_times"],
                         report.analyse(model).to_dict()["response_times"])

    # ---- measurement: the formats of the firmware (RteConsole.h) ----

    def formats(self):
        return console_formats("writeModeLine"), console_formats("writeJsonMode")

    def test_text_and_json_format_of_the_target_match_the_parser(self):
        text_fmt, json_fmt = self.formats()
        self.assertEqual(len(text_fmt), 1)
        text = mode_console([
            (text_fmt, ["Slow.OnWake", "slow", "transition", "STANDBY", " -> ", "RUN", 2, 61, 70]),
            (text_fmt, ["Slow.OnRunEntry", "slow", "entry", "", "", "RUN", 3, 190, 210]),
        ])
        self.assertIn("mode Slow.OnWake (task slow): on transition STANDBY -> RUN, calls 2, last 61 us, max 70 us",
                      text)
        m = measure.parse(text)
        self.assertEqual(m.lines_ignored, 0, text)
        self.assertEqual(m.sources, ["rte runnables"])
        wake = m.modes[("slow", "Slow.OnWake", "transition", "STANDBY", "RUN")]
        self.assertEqual((wake.calls, wake.last_us, wake.max_us), (2, 61.0, 70.0))
        self.assertEqual(m.modes[("slow", "Slow.OnRunEntry", "entry", None, "RUN")].calls, 3)
        json_text = mode_console([
            (json_fmt, ["slow", "Slow.OnWake", "transition", "RUN", "STANDBY", 2, 61, 55, 60, 70]),
            (json_fmt, ["slow", "Slow.OnRunExit", "exit", "RUN", "", 0, 0, 0, 0, 0]),
        ])
        m = measure.parse(json_text)
        self.assertEqual(m.lines_ignored, 0, json_text)
        wake = m.modes[("slow", "Slow.OnWake", "transition", "STANDBY", "RUN")]
        self.assertEqual((wake.min_us, wake.mean_us, wake.max_us), (55.0, 60.0, 70.0))
        for key in ("calls", "min_us", "mean_us", "max_us", "event", "from"):
            self.assertIn(key, "".join(json_fmt), f"{key} must stay in the JSON output")

    def test_rte_json_announces_the_mode_lines(self):
        """The header of rte json counts the mode lines (RteSystem.cpp), so a lost one is noticed."""
        source = RTE_SOURCE.read_text(encoding="utf-8")
        block = source.split("case ID_JSON:", 1)[1].split("case ID_RESET:", 1)[0]
        self.assertIn('\\"modes\\":%u', block)
        self.assertIn("writeModeStatistics(out, source, RunInOwner(), modeBuffer, rteconsole::Format::JSON)", block)
        runnables = source.split("case ID_RUNNABLES:", 1)[1].split("case ID_JSON:", 1)[0]
        self.assertIn("writeModeStatistics(out, source, RunInOwner(), modeBuffer, rteconsole::Format::TEXT)",
                      runnables)
        _, json_fmt = self.formats()
        header = '{"t":"rte","v":1,"tasks":1,"runnables":1,"periodic":1,"up_us":5,"modes":2}\n'
        task = '{"t":"task","name":"slow","context":2,"period_ms":10,"cycles":9}\n'
        runnable = '{"t":"runnable","task":"slow","name":"Slow.Run10ms","period_ms":10,"offset_ms":0,"calls":9}\n'
        mode = mode_console([(json_fmt, ["slow", "Slow.OnRunEntry", "entry", "RUN", "", 1, 5, 5, 5, 5])])
        m = measure.parse(header + task + runnable + mode + '{"t":"end","v":1}\n')
        self.assertFalse(m.complete)
        self.assertIn("rte json: lost 1 of 2 mode lines", m.problems)
        self.assertEqual(m.to_dict()["announced"]["modes"], 2)
        self.assertEqual(list(m.to_dict()).index("modes"), list(m.to_dict()).index("runnables") + 1)

    def test_measured_values_land_in_the_model(self):
        _, json_fmt = self.formats()
        text = mode_console([
            (json_fmt, ["slow", "Slow.OnWake", "transition", "RUN", "STANDBY", 2, 61, 55, 60, 70]),
            (json_fmt, ["slow", "Slow.OnRunExit", "exit", "RUN", "", 0, 0, 0, 0, 0]),
            (json_fmt, ["fast", "Slow.OnRunEntry", "entry", "RUN", "", 4, 9, 8, 9, 11]),
        ])
        model = load_scenario("modes")
        for r in model.mode_runnables():
            r.wcet_us = None
        remarks = measure.apply_to(model, measure.parse(text))
        by_name = {r.name: r for r in model.mode_runnables()}
        self.assertEqual((by_name["Slow.OnWake"].wcet_us, by_name["Slow.OnWake"].mean_us), (70.0, 60.0))
        self.assertEqual(by_name["Slow.OnRunEntry"].wcet_us, 11.0)   # found in another task: taken, but said
        self.assertIn("mode switch runnable Slow.OnRunEntry on entry RUN runs in task fast on the target, the "
                      "configuration says slow", remarks)
        self.assertIsNone(by_name["Slow.OnRunExit"].wcet_us)         # did not run: no runtime
        self.assertIn("mode switch runnable Slow.OnRunExit on exit RUN did not run during the measurement window: "
                      "no runtime (switch the ECU mode while measuring)", remarks)
        self.assertIn("no measurement for mode switch runnable Slow.OnStartupEntry on entry STARTUP", remarks)

    # ---- the RTE-08 test profile of tools/rtegen ----

    def test_rtegen_mode_profile(self):
        from schedana import rteload
        config = rteload.nxconfig.load(REPO / "projects" / "buildtest_s32k148_min" / "project.yaml")
        model = rteload.load_rte(MODES_PROFILE / "rte.yaml", config, repo=REPO,
                                 tasks_manifest=MULTITASK / "tasks.yaml")
        self.assertEqual([(r.task, r.name, r.label, r.where) for r in model.mode_runnables()], [
            ("slow", "ModeSwc.OnStartupEntry", "on entry STARTUP", "tasks[1].runnables[2]"),
            ("slow", "ModeSwc.OnRunEntry", "on entry RUN", "tasks[1].runnables[3]"),
            ("slow", "ModeSwc.OnRunExit", "on exit RUN", "tasks[1].runnables[4]"),
            ("slow", "AlphaSwc.OnStandbyExit", "on exit STANDBY", "tasks[1].runnables[5]"),
            ("fast", "ModeSwc.OnStandbyToRun", "on transition STANDBY -> RUN", "tasks[0].runnables[1]"),
        ])
        # the same rows as rte::MODE_RUNNABLES of the generated RTE (tools/rtegen, grouped by task)
        # a mode switch runnable accesses in its task at the cycle start, not as an init runnable
        entry = model.signal("ModeSwc.EntryCount.EntryCount")
        self.assertIn(("slow", "ModeSwc.OnRunEntry", -1, "w"),
                      [(a.task, a.runnable, a.order, a.mode) for a in entry.accesses])
        self.assertNotIn(-2, [a.order for a in entry.accesses])
        result = report.analyse(model)
        self.assertEqual([e["task"] for e in result.scheduling["mode_switches"]], ["slow", "fast"])
        self.assertEqual(result.scheduling["mode_switches"][0]["worst_switch"]["us"], None)  # no runtimes yet
        self.assertNotIn("rate_transition_up", codes(result))


# Synthetic console answer shaped like the board capture of 2026-09-16 (tools/boardtest/captures/.../
# 2026-09-16_measure.console.txt): one blank in front of every output line, the acknowledgement " ok" after every
# answer, log lines of the OpenBSW logger in between, the "### <command>" headings of a recorded capture. The
# numbers are made up; a task and an ISR group are both called "can", as on the target.
SYNTHETIC_CAPTURE = """\
# Synthetic capture (TOOL-15 test), same shape as a recorded board answer
### rte runnables
rte runnables
 task swc (context 3): every 10 ms, cycles 500, overruns 0, max 60 us
   DemoSwc.Run10ms: every 10 ms, offset 0 ms, calls 500, last 2 us, max 3 us
   DemoSwc.Run100ms: every 100 ms, offset 0 ms, calls 50, last 3 us, max 4 us
 init DemoSwc.DemoSwc_Init: calls 1, last 9 us
 ok

> 12001: RefApp: CONSOLE: INFO: Received console command "rte runnables"

> 12020: RefApp: CONSOLE: INFO: Console command succeeded
### stats cpu
stats cpu
 task                    %       total   runs       avg       min       max
 ---------------------------------------------------------------------------
 idle              97.00 %       970 ms    300   3000 us    700 us   5000 us
 swc                0.40 %         4 ms    100     40 us     35 us     60 us
 can                0.41 %         4 ms    200     20 us     19 us     22 us

 isr group               %       total   runs       avg       min       max
 ---------------------------------------------------------------------------
 can                0.10 %         1 ms    100     10 us      9 us     12 us

 measurement time:  1000000 us
 ok

> 13190: RefApp: CONSOLE: INFO: Received console command "stats cpu"
this line is garbled
"""


class Tool15Test(unittest.TestCase):
    """TOOL-15: interfaces for the Studio (where of reader_before_writer, execution slices of the simulation) and
    the findings of the board test 2026-09-16 (" ok" counted as unreadable, can named twice)."""

    def test_acknowledgements_and_log_lines_are_not_unreadable(self):
        m = measure.parse(SYNTHETIC_CAPTURE)
        self.assertEqual(m.lines_ignored, 1)                        # only the garbled line
        self.assertEqual(m.ignored_examples, ["this line is garbled"])
        self.assertEqual(m.lines_noise, 10)  # comment, 2 headings, 2 echoes, 2 " ok", 3 log lines
        self.assertEqual((m.tasks["swc"].cycles, m.tasks["can"].runs, m.isrs["can"].runs), (500, 200, 100))
        self.assertEqual(m.problems, ["1 of 24 lines were not readable and are ignored "
                                      "(example: 'this line is garbled')"])
        # without the garbled line nothing is reported at all
        clean = measure.parse(SYNTHETIC_CAPTURE.replace("this line is garbled\n", ""))
        self.assertEqual((clean.lines_ignored, clean.problems), (0, []))
        self.assertNotIn("measurement_incomplete",
                         {f.code for f in report.measurement_findings(load_scenario("idle"), clean)})
        self.assertIn("measurement_incomplete", {f.code for f in report.measurement_findings(load_scenario("idle"), m)})

    @board_capture
    def test_recorded_board_capture_has_no_unreadable_line(self):
        m = measure.parse(BOARD_CAPTURE.read_text(encoding="utf-8"))
        self.assertEqual((m.lines_ignored, m.problems), (0, []))   # was 18 of 55 (" ok", log lines, headings)

    def test_an_ok_inside_a_value_line_is_still_a_value(self):
        m = measure.parse(" task ok (context 3): every 10 ms, cycles 5, overruns 0, max 80 us\n ok\n")
        self.assertEqual((m.tasks["ok"].cycles, m.lines_used, m.lines_noise), (5, 1, 1))

    def test_runtime_missing_names_a_task_and_an_isr_group_of_the_same_name_once(self):
        model = scenario.from_dict({
            "schema_version": 1, "name": "same_name", "tick_us": 1000,
            "tasks": [{"name": "can", "kind": "bsw", "priority": 40, "period_ms": 5},
                      {"name": "bsp", "kind": "bsw", "priority": 20, "period_ms": 10}],
            "isrs": [{"name": "can"}, {"name": "test"}],
        }, "same_name")
        missing = by_code(report.analyse(model), "runtime_missing")[0]
        self.assertEqual(missing.message.count("can"), 1, missing.message)
        self.assertIn("no runtime for bsp, can (task and ISR group), test (ISR group):", missing.message)
        self.assertEqual(missing.detail["missing"], ["bsp", "can", "test"])
        self.assertEqual(missing.detail["missing_by_kind"], {"tasks": ["bsp", "can"], "isr_groups": ["can", "test"]})
        utilisation = scheduling.utilisation(model)
        self.assertEqual(utilisation["without_runtime"], ["bsp", "can", "test"])

    def test_reader_before_writer_names_the_yaml_path_of_the_reading_entry(self):
        """In a project (rteload) the access carries the port; `where` is the runnable entry, the port the detail."""
        from schedana import rteload
        config = rteload.nxconfig.load(DEMO / "project.yaml")
        model = rteload.load_rte(MULTITASK / "rte.yaml", config, repo=REPO,
                                 tasks_manifest=MULTITASK / "tasks.yaml")
        stale = by_code(report.analyse(model), "reader_before_writer")
        self.assertTrue(stale)
        for finding in stale:
            reader = model.runnable(finding.detail["reader"])
            self.assertEqual(finding.where, reader.where)
            self.assertRegex(finding.where, r"^tasks\[\d+\]\.runnables\[\d+\]$")
            self.assertIn(finding.detail["reader"].split(".")[0], finding.detail["port"])
        # a scenario has no ports: where is the entry as before, no extra key in the detail
        scenario_stale = by_code(analyse_scenario("dataflow_errors"), "reader_before_writer")[0]
        self.assertEqual(scenario_stale.where, "tasks[1].runnables[0]")
        self.assertNotIn("port", scenario_stale.detail)

    def test_simulation_with_trace_returns_the_execution_slices(self):
        model = load_scenario("idle")
        hyper_us = float(model.hyperperiod_ms()) * 1000.0
        jobs = scheduling.jobs_of(model, hyper_us)
        plain = scheduling.simulate(jobs, hyper_us)
        traced = scheduling.simulate(jobs, hyper_us, trace=True)
        self.assertNotIn("pieces", plain)
        self.assertEqual({k: v for k, v in traced.items() if k != "pieces"}, plain)   # same aggregates
        pieces = traced["pieces"]
        self.assertTrue(pieces)
        # slices never overlap and are in time order (one core)
        for before, after in zip(pieces, pieces[1:]):
            self.assertLessEqual(before["end_us"], after["start_us"] + 1e-9)
        # the slices of every task add up to its work, and each activation is numbered from 1
        for name, stats in plain["tasks"].items():
            mine = [p for p in pieces if p["job"]["name"] == name]
            self.assertAlmostEqual(sum(p["end_us"] - p["start_us"] for p in mine), stats["busy_us"], places=6)
            self.assertEqual(sorted({p["job"]["number"] for p in mine}), list(range(1, stats["jobs"] + 1)))
        # a preempted activation continues where it stopped (from_us), never before its release
        for piece in pieces:
            self.assertGreaterEqual(piece["start_us"], piece["job"]["release_us"] - 1e-9)
            self.assertLessEqual(piece["from_us"] + piece["end_us"] - piece["start_us"],
                                 piece["job"]["cost_us"] + 1e-6)

    def test_trace_marks_the_late_activations(self):
        model = load_scenario("overloaded")
        hyper_us = float(model.hyperperiod_ms()) * 1000.0
        traced = scheduling.simulate(scheduling.jobs_of(model, hyper_us), hyper_us, trace=True)
        late = {id(p["job"]): p["job"] for p in traced["pieces"] if p["job"]["late"]}
        misses = sum(s["misses"] for s in traced["tasks"].values())
        self.assertTrue(misses)
        self.assertEqual(len(late), misses)



def _old_on_tick_grid(period_ms, tick_us, runs=None):
    """Mutant 1 (TOOL-09 behaviour): measured period always rounded down to the tick grid."""
    tick_ms = max(tick_us, 1) / 1000.0
    return round(max(1, int(period_ms / tick_ms)) * tick_ms, 6), "rounded down to the OS tick grid"


def _old_jobs_within(model, hyper_us, max_jobs=None):
    """Mutant 2 (TOOL-09 behaviour): the list is cut per task and returned unsorted once MAX_JOBS is exceeded."""
    jobs = []
    for source in scheduling._job_sources(model, hyper_us):
        for job in source:
            jobs.append(job)
            if len(jobs) > scheduling.MAX_JOBS:
                return jobs, hyper_us
    jobs.sort(key=lambda j: (j["release_us"], -j["priority"]))
    return jobs, hyper_us


def _simulate_without_sort():
    """Mutant 3: simulate() without its own sort of an unsorted job list."""
    import inspect
    source = inspect.getsource(scheduling.simulate)
    line = '        pending.sort(key=lambda j: (j["release_us"], -j["priority"]))\n'
    assert line in source, "the sort in simulate() moved - adapt the mutant"
    namespace = dict(vars(scheduling))
    exec(source.replace(line, "        pass\n"), namespace)  # noqa: S102 - a mutant of our own code
    return namespace["simulate"]


class Tool34Test(unittest.TestCase):
    """TOOL-34: measured periods on the tick grid instead of rounded down, job list always sorted by release.

    Each check is a function returning the list of problems, so the same check runs against the code (must be
    empty) and against a mutant with the old behaviour (must not be empty)."""

    @classmethod
    def setUpClass(cls):
        from schedana import rteload
        cls.rteload = rteload

    def board_model(self):
        model = self.rteload.load(DEMO)
        m = measure.parse(BOARD_CAPTURE.read_text(encoding="utf-8"), known_tasks=[t.name for t in model.tasks],
                          known_isrs=[i.name for i in model.isrs])
        return model, m, measure.apply_to(model, m)

    def board_problems(self) -> list:
        """The real capture of 2026-09-16 with the demo project: no deadline miss, small hyper period."""
        model, m, remarks = self.board_model()
        result = report.analyse(model, m, remarks)
        problems = [f"{f.code} {f.target}" for f in result.findings
                    if f.code in ("simulated_deadline_missed", "deadline_missed", "response_diverges")]
        hyper = result.scheduling["hyperperiod"]
        if hyper["hyperperiod_ms"] != 1000.0 or hyper["truncated"] or hyper["capped"]:
            problems.append(f"hyper period {hyper['hyperperiod_ms']} ms, truncated {hyper['truncated']}, "
                            f"capped {hyper['capped']}")
        periods = {t.name: t.period_ms for t in model.tasks}
        if periods != {"background": 1000.0, "bsp": 10.0, "swc": 10, "can": 5.0, "sysadmin": 10.0, "safety": 10.0}:
            problems.append(f"periods {periods}")
        return problems

    def sorted_problems(self, simulate=None) -> list:
        """More activations than MAX_JOBS: sorted list, cut at a release instant, simulation without misses."""
        simulate = simulate or scheduling.simulate
        problems = []
        model = load_scenario("idle")
        hyper_us = float(model.hyperperiod_ms()) * 1000.0
        with unittest.mock.patch.object(scheduling, "MAX_JOBS", 50):
            jobs = scheduling.jobs_of(model, hyper_us)
            _, horizon = scheduling.jobs_within(model, hyper_us)
            hyper = scheduling.hyperperiod(model)
        releases = [j["release_us"] for j in jobs]
        if releases != sorted(releases):
            problems.append("jobs_of is not sorted by release time")
        if len(jobs) > 50:
            problems.append(f"{len(jobs)} jobs, more than MAX_JOBS")
        if {(j["name"], j["kind"]) for j in jobs} != {("base", "bsw"), ("ctrl", "application"), ("bus", "bsw"),
                                                      ("guard", "bsw"), ("bus", "isr")}:
            problems.append(f"missing sources: {sorted({j['name'] for j in jobs})}")
        if any(r >= horizon for r in releases):
            problems.append("a job at or after the horizon")
        full = scheduling.jobs_of(model, hyper_us)
        if [j for j in full if j["release_us"] < horizon] != jobs:
            problems.append("the cut list is not the complete prefix of the full list")
        if not hyper["truncated"] or hyper.get("analysed_ms") != horizon / 1000.0:
            problems.append("hyperperiod() does not report the cut")
        misses = {n: s["misses"] for n, s in simulate(jobs, horizon)["tasks"].items() if s["misses"]}
        if misses:
            problems.append(f"misses {misses}")
        shuffled = list(reversed(full))
        if simulate(shuffled, hyper_us) != simulate(full, hyper_us):
            problems.append("simulate() depends on the order of its input")
        return problems

    @board_capture
    def test_board_capture_has_no_false_deadline_misses(self):
        self.assertEqual(self.board_problems(), [])
        _, _, remarks = self.board_model()
        self.assertIn("task bsp: activation period 9.99995 ms derived from 100 runs in the measurement window, "
                      "used as 10.0 ms (the nearest multiple of the OS tick, within the measurement uncertainty)",
                      remarks)

    @board_capture
    def test_a_real_overload_on_the_board_model_is_still_reported(self):
        model, m, remarks = self.board_model()
        model.task("bsp").wcet_us = 12000.0   # longer than its 10 ms period: bsp and the task below fall behind
        result = report.analyse(model, m, remarks)
        late = {f.target for f in by_code(result, "simulated_deadline_missed")}
        self.assertTrue({"bsp", "background"} <= late, late)
        self.assertNotIn("safety", late)      # above bsp: undisturbed
        self.assertIn("overloaded", {s.stem for s in SCENARIOS.glob("*.yaml")})
        self.assertIn("simulated_deadline_missed", codes(analyse_scenario("overloaded")))

    def test_job_list_is_sorted_and_cut_at_a_release_instant(self):
        self.assertEqual(self.sorted_problems(), [])

    def test_truncation_is_a_clear_finding(self):
        model = load_scenario("idle")
        with unittest.mock.patch.object(scheduling, "MAX_JOBS", 50):
            result = scheduling.scheduling_analysis(model)
        finding = next(f for f in result["findings"] if f.code == "hyperperiod_truncated")
        hyper = result["hyperperiod"]
        # 5 jobs at 0 ms, the ISR every 1 ms, the three 10 ms tasks: the 50th job is released at 36 ms
        self.assertEqual((hyper["activations"], hyper["activations_analysed"]), (132, 50))
        self.assertEqual(hyper["analysed_ms"], 37.0)   # the next release instant, not in the list
        self.assertIn("has 132 activations, more than the limit of 50: load windows and simulation cover only "
                      "the first 37.0 ms", finding.message)
        self.assertEqual(finding.detail["limit"], 50)
        self.assertTrue(all(w["start_us"] < hyper["analysed_ms"] * 1000.0 for w in hyper["windows"]))
        # without the limit nothing is cut and the report has no extra keys
        full = scheduling.hyperperiod(model)
        self.assertFalse(full["truncated"])
        self.assertNotIn("analysed_ms", full)

    def test_rounding_on_the_tick_grid(self):
        cases = [
            # (measured ms, runs, tick us, expected ms, word in the reason)
            (9.99995, 100, 1000, 10.0, "nearest"),        # board 2026-09-16: was 9 ms
            (4.999975, 200, 1000, 5.0, "nearest"),
            (999.995, 1, 1000, 1000.0, "nearest"),
            (10.08, 100, 1000, 10.0, "nearest"),          # within +-1 run of 100
            (1.0526, 950, 1000, 1.0, "rounded down"),     # sporadic ISR: clearly off the grid, conservative
            (4.695, 213, 1000, 4.0, "rounded down"),
            (7.5, 133, 1000, 7.0, "rounded down"),
            (2.4999, 400, 500, 2.5, "nearest"),           # 0.5 ms tick (rtegen multitask profile)
            (0.05, 20000, 1000, 0.05, "faster"),          # 20 kHz ISR: not one tick, that would hide 95 %
        ]
        for measured, runs, tick, expected, word in cases:
            with self.subTest(measured=measured):
                value, how = measure._on_tick_grid(measured, tick, runs)
                self.assertAlmostEqual(value, expected, places=6)
                self.assertIn(word, how)
                self.assertLessEqual(value, measured * (1.0 + measure._tolerance(runs)) + 1e-9)

    def test_configured_period_stays_and_a_different_rate_is_remarked(self):
        model = load_scenario("idle")
        text = ("task                    %        total   runs      avg      min      max\n"
                "base             1.00 %        10 ms    100      100 us     42 us    120 us\n"
                "bus              1.00 %        10 ms    125       40 us     22 us     50 us\n"
                "\n"
                "measurement time:  1000000 us\n")
        m = measure.parse(text, known_tasks=[t.name for t in model.tasks], known_isrs=["bus_isr"])
        remarks = measure.apply_to(model, m)
        self.assertEqual((model.task("base").period_ms, model.task("bus").period_ms), (10, 10))
        self.assertFalse(any("task base" in r for r in remarks), remarks)
        self.assertIn("task bus: 125 runs in the measurement window are one activation every 8.000 ms, the "
                      "configured period is 10 ms; the configured period is used", remarks)

    def test_mutants_are_noticed(self):
        if BOARD_CAPTURE.is_file():                  # PLAT-62: the recording is private (exported tree: skipped)
            with unittest.mock.patch.object(measure, "_on_tick_grid", _old_on_tick_grid):
                problems = self.board_problems()
            self.assertTrue(any("hyper period 99900.0 ms" in p for p in problems), problems)
        with unittest.mock.patch.object(scheduling, "jobs_within", _old_jobs_within):
            problems = self.sorted_problems()
        self.assertIn("jobs_of is not sorted by release time", problems)
        problems = self.sorted_problems(simulate=_simulate_without_sort())
        self.assertEqual(problems, ["simulate() depends on the order of its input"])
        # the old code had both: an unsorted list into a simulation that trusts the order - false misses
        with unittest.mock.patch.object(scheduling, "jobs_within", _old_jobs_within):
            problems = self.sorted_problems(simulate=_simulate_without_sort())
        self.assertTrue(any(p.startswith("misses") for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
