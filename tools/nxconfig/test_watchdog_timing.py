"""SYS-25 (after SYS-23 and SYS-40) and SYS-53: times of the safety task as module parameters of safeLifecycle, the
watchdog service interval derived at the fastest LPO, the LPO limits from mcudb, the module parameters of the safe
supervisor, and the checks of tools/nxconfig/checks.py section "safety timing".

The three values are project values modules.safeLifecycle.{task_period_ms, watchdog_timeout_ms, cycle_lateness_ms}
(defaults in platforms/s32k1xx/port/safety/safeLifecycle/module.yaml). SYS-53: the LPO range of the derivative is a
fact of mcudb (clock.lpo, data sheet); derived are its limits in per mille (rounded outwards) and the watchdog service
interval floor(time-out x 1000 / (3 x period x fastest per mille)) - the relation 3 x service interval <= time-out of
SYS-23 (finding F2 of doc/safety/concept.md) holds by construction at the fastest LPO (decision sys25-lpo) and is
checked again as counter-check. The safe supervisor (platforms/s32k1xx/port/safety/safeSupervisor/module.yaml) has the
reset-loop limit (SYS-43) and the window of the device-bus correlation (SYS-29) as module parameters; the window in
safety cycles is derived. Checked with the YAML path of the value: time-out against three periods and against the
S32K1 WDOG (511 ms), the static_assert conditions of the period check (SafetyManager.cpp), a derivative without LPO
range, the fault-free time of the reset-loop limit, warnings for the device-bus correlation.

Own test profile: a copy of tools/bswgen/test/profiles/projects/testproject_safe_outputs (S32K144, two TMC6200 on two
SPI buses) with its own module values, plus small source trees with other constants of the period-check timer and the
correlation, and copies of mcudb/s32k1xx with another LPO range. The demo project once, if it is there (private
project).

Run: .venv/Scripts/python -m unittest tools/nxconfig/test_watchdog_timing.py
"""

import shutil
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))

import nxconfig  # noqa: E402
from nxconfig import checks  # noqa: E402
from nxconfig import config as nxcfg  # noqa: E402
from nxconfig import model  # noqa: E402
from nxconfig.config import Reporter  # noqa: E402

PROFILE = REPO / "tools" / "bswgen" / "test" / "profiles" / "projects" / "testproject_safe_outputs" / "project.yaml"
# PLAT-62: the own full-featured test profile (synthetic testboard_full/testproject_full) instead of the eval board
# project, which stays private (PLAT-46 F10) - a test against it would be red in the exported open tree. What
# proves something about the eval board itself uses EVAL_DEMO_PROJECT and is skipped where it is missing.
DEMO_PROJECT = REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full" / "project.yaml"
EVAL_DEMO_PROJECT = REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml"
NUCLEO_PROJECT = REPO / "projects" / "nucleo_f446re_demo" / "project.yaml"
MANIFEST = REPO / "platforms" / "s32k1xx" / "port" / "safety" / "safeLifecycle" / "module.yaml"
SUPERVISOR_MANIFEST = REPO / "platforms" / "s32k1xx" / "port" / "safety" / "safeSupervisor" / "module.yaml"
TIMER_H = "platforms/s32k1xx/port/safety/safeSupervisionCheck/include/safeSupervisionCheck/LptmrCycleTimer.h"
SUPERVISOR_H = "ecu/safety/safeSupervisor/include/safeSupervisor/SafeSupervisor.h"
MCUDB_FAMILY = REPO / "mcudb" / "s32k1xx"
PATH = "modules.safeLifecycle."
SUPERVISOR = "modules.safeSupervisor."
# demo values since SYS-53: every 7 cycles at the fastest LPO (139 kHz), period-check bounds 14 and 59 ticks
DEMO = checks.SafetyTiming(10, 250, 2, 7, 14, 59, 882, 1086)


class Profile(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nx_wdg_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def project(self, safe: dict | None = None, **modules) -> Path:
        """The own profile with modules.safeLifecycle = `safe` (None: not named, defaults of the manifest)."""
        doc = yaml.safe_load(PROFILE.read_text(encoding="utf-8"))
        doc["project"]["board"] = (PROFILE.parent / doc["project"]["board"]).resolve().as_posix()
        if safe is not None:
            doc["modules"]["safeLifecycle"] = safe
        for name, values in modules.items():
            doc["modules"][name] = {**doc["modules"].get(name, {}), **values}
        file = self.tmp / "project" / "project.yaml"
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        return file

    def check(self, safe: dict | None = None, mcudb: Path | None = None, **modules):
        if mcudb is None:
            return nxconfig.check(self.project(safe, **modules))
        return nxconfig.check(self.project(safe, **modules), mcudb=mcudb)

    def safety(self, messages):
        """Messages of the safety timing: those at modules.safeLifecycle.*/safeSupervisor.* and the correlation
        warnings."""
        return [m for m in messages if m.yaml_path.startswith(("modules.safeLifecycle", "modules.safeSupervisor"))
                or "SYS-29" in m.text]

    def tree(self, timer: dict | None = None, supervisor: dict | None = None) -> Path:
        """Source tree with the constants of the period-check timer and of the device-bus correlation."""
        timer = {"CYCLE_TIMER_NOMINAL_HZ": "1000U", "CYCLE_TIMER_MASK": "0xFFFFU", **(timer or {})}
        supervisor = {"DEVICE_BUS_SLOTS": "8U", **(supervisor or {})}
        root = self.tmp / "repo"
        for rel, consts, static in ((TIMER_H, timer, ""), (SUPERVISOR_H, supervisor, "static ")):
            file = root / rel
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("".join(f"{static}constexpr uint32_t {k} = {v};\n" for k, v in consts.items()),
                            encoding="utf-8")
        return root

    def mcudb(self, lpo: str | None) -> Path:
        """Copy of mcudb/s32k1xx whose S32K144 has the LPO entry `lpo` (flow mapping; None: no entry)."""
        root = self.tmp / "mcudb"
        shutil.copytree(MCUDB_FAMILY, root / "s32k1xx")
        file = root / "s32k1xx" / "S32K144.yaml"
        lines = file.read_text(encoding="utf-8").splitlines()
        start = next(i for i, line in enumerate(lines) if line.startswith("  lpo:"))
        end = next(i for i in range(start + 1, len(lines)) if not lines[i].startswith("         "))
        lines[start:end] = [f"  lpo: {lpo}"] if lpo is not None else []
        file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return root

    def rerun(self, config, repo: Path):
        """safety_timing of a loaded project again, against the constants of `repo`."""
        rep = Reporter(sources=dict(config.sources))
        return checks.safety_timing(config, rep, repo), rep.messages


class DerivationTest(Profile):
    def test_manifest_defaults_are_the_demo_values(self):
        """Without modules.safeLifecycle the defaults of the manifest apply: 10 ms, 250 ms, 2 ms -> every 7 cycles at
        the fastest LPO (SYS-53), bounds of the period check 14 and 59 ticks (SYS-40)."""
        config, messages = self.check()
        self.assertIsNotNone(config, [m.text for m in messages if m.severity == "error"])
        self.assertEqual(self.safety(messages), [])
        self.assertEqual(config.safety_timing, DEMO)
        self.assertEqual(config.safety_timing.service_interval_ms, 70)
        use = config.integration.modules["safeLifecycle"]
        self.assertEqual({k: v for k, (v, _) in use.derived.items()},
                         {checks.LPO_MIN: 882, checks.LPO_MAX: 1086, checks.SERVICE_CYCLES: 7})
        self.assertEqual(list(use.derived), [checks.LPO_MIN, checks.LPO_MAX, checks.SERVICE_CYCLES])  # header order
        self.assertIn("floor(250000 / 32580)", use.derived[checks.SERVICE_CYCLES][1])
        self.assertIn("230.2 ms", use.derived[checks.SERVICE_CYCLES][1])
        self.assertIn("floor(113000000 / 128000) (mcudb S32K144", use.derived[checks.LPO_MIN][1])
        self.assertIn("ceil(139000000 / 128000) (mcudb S32K144", use.derived[checks.LPO_MAX][1])

    def test_fastest_lpo_takes_one_cycle_less_than_nominal(self):
        """Decision sys25-lpo: nominal 3 x 8 x 10 = 240 ms fit into 250 ms, but not into the 230.2 ms at 139 kHz."""
        self.assertEqual(checks.service_cycles(10, 250), 8)
        self.assertEqual(checks.service_cycles(10, 250, 1086), 7)
        self.assertEqual(checks.service_cycles(10, 250, 1102), 7)       # S32K14xW, 141 kHz: 226.9 ms

    def test_derived_from_other_values(self):
        config, _ = self.check({"task_period_ms": 20, "watchdog_timeout_ms": 400, "cycle_lateness_ms": 3})
        self.assertEqual((config.safety_timing.service_cycles, config.safety_timing.service_interval_ms), (6, 120))

    def test_threshold_of_seven_cycles(self):
        """3 x 7 x 10 ms at 1086 per mille need 228.06 ms: 229 ms give 7 cycles, 228 ms only 6."""
        config, messages = self.check({"watchdog_timeout_ms": 229})
        self.assertEqual(self.safety(messages), [])
        self.assertEqual(config.safety_timing.service_cycles, 7)
        config, _ = self.check({"watchdog_timeout_ms": 228})
        self.assertEqual(config.safety_timing.service_cycles, 6)

    def test_relation_itself(self):
        """The SYS-23 relation on plain numbers: nominal 3 x 8 x 10 = 240 <= 240 holds, <= 239 not, 0 cycles never;
        at the fastest LPO the time-out shrinks (1200 per mille: 288 ms for 240 ms) - equality still holds."""
        self.assertEqual(checks.watchdog_timing_messages(10, 240, 8), [])
        self.assertIn("3 x 8 cycles x 10 ms = 240 ms", checks.watchdog_timing_messages(10, 239, 8)[0])
        self.assertIn("3 x 9 cycles x 10 ms = 270 ms", checks.watchdog_timing_messages(10, 250, 9)[0])
        self.assertIn("shorter than 3 periods", checks.watchdog_timing_messages(100, 250, 0)[0])
        self.assertEqual(checks.watchdog_timing_messages(10, 288, 8, max_per_mille=1200), [])
        text = checks.watchdog_timing_messages(10, 287, 8, max_per_mille=1200)[0]
        self.assertIn("expires after 239.2 ms", text)
        self.assertIn("1200 per mille", text)
        self.assertIn("3 x 8 cycles x 10 ms = 240 ms", checks.watchdog_timing_messages(10, 250, 8,
                                                                                         max_per_mille=1086)[0])
        for per_mille in (1000, 1086, 1102, 1250):
            for period in range(1, 40):
                for timeout in range(1, 400, 7):
                    cycles = checks.service_cycles(period, timeout, per_mille)
                    messages = checks.watchdog_timing_messages(period, timeout, cycles, max_per_mille=per_mille)
                    self.assertEqual(messages == [], cycles >= 1)
                    self.assertTrue(checks.watchdog_timing_messages(period, timeout, cycles + 1,
                                                                    max_per_mille=per_mille))  # the largest one

    def test_counter_check_catches_a_wrong_derivation(self):
        """The SYS-23 comparison stays as counter-check: a derivation one cycle too long (here: the nominal one of
        SYS-25) is an error at the fastest LPO."""
        nominal = lambda period, timeout, per_mille=1000: timeout // (3 * period)  # noqa: E731
        with unittest.mock.patch.object(checks, "service_cycles", nominal):
            config, messages = self.check()
        self.assertIsNone(config)
        found = [m for m in messages if m.severity == "error"]
        self.assertEqual([m.yaml_path for m in found], [PATH + "watchdog_timeout_ms"])
        self.assertIn("3 x 8 cycles x 10 ms = 240 ms", found[0].text)
        self.assertIn("expires after 230.2 ms", found[0].text)


class LpoTest(Profile):
    """SYS-53: the LPO range is a derivative fact (mcudb clock.lpo), no longer a constant of LptmrCycleTimer.h."""

    def test_every_s32k1xx_derivative_has_the_data_sheet_range(self):
        for file in sorted(MCUDB_FAMILY.glob("S32K1[0-9][0-9].yaml")):
            with self.subTest(derivative=file.stem):
                doc = yaml.safe_load(file.read_text(encoding="utf-8"))
                lpo = doc["clock"]["lpo"]
                self.assertEqual((lpo["nominal_hz"], lpo["min_hz"], lpo["max_hz"]), (128000, 113000, 139000))
                self.assertIn("Table 33", lpo["source"])

    def test_limits_are_rounded_outwards(self):
        lpo = model.LowPowerOscillator
        self.assertEqual(checks.lpo_per_mille(lpo(nominal_hz=128000, min_hz=113000, max_hz=139000)), (882, 1086))
        # the S32K14xW column gives the former constants of LptmrCycleTimer.h
        self.assertEqual(checks.lpo_per_mille(lpo(nominal_hz=128000, min_hz=113000, max_hz=141000)), (882, 1102))
        self.assertEqual(checks.lpo_per_mille(lpo(nominal_hz=1000, min_hz=1000, max_hz=1000)), (1000, 1000))

    def test_range_must_contain_the_nominal_value(self):
        with self.assertRaises(ValueError):
            model.LowPowerOscillator(nominal_hz=128000, min_hz=130000, max_hz=139000)

    def test_own_mcudb_with_another_range(self):
        """100 ... 160 kHz: 781 ... 1250 per mille -> every 6 cycles, bounds 16 and 45 ticks."""
        mcudb = self.mcudb("{ nominal_hz: 128000, min_hz: 100000, max_hz: 160000, source: test }")
        config, messages = self.check(mcudb=mcudb)
        self.assertIsNotNone(config, [m.text for m in messages if m.severity == "error"])
        self.assertEqual(config.safety_timing, checks.SafetyTiming(10, 250, 2, 6, 16, 45, 781, 1250))
        use = config.integration.modules["safeLifecycle"]
        self.assertEqual(use.derived[checks.SERVICE_CYCLES][0], 6)
        self.assertIn("ceil(160000000 / 128000)", use.derived[checks.LPO_MAX][1])

    def test_derivative_without_lpo_range(self):
        """The S32K1 WDOG counts the LPO: without clock.lpo the service interval cannot be derived."""
        config, messages = self.check(mcudb=self.mcudb(None))
        self.assertIsNone(config)
        found = [m for m in messages if m.severity == "error"]
        self.assertEqual([(m.file.name, m.yaml_path) for m in found], [("S32K144.yaml", "clock")])
        self.assertIn("no LPO range (clock.lpo", found[0].text)

    def test_range_without_nominal_value_is_a_schema_error(self):
        config, messages = self.check(mcudb=self.mcudb("{ nominal_hz: 128000, min_hz: 130000, max_hz: 139000 }"))
        self.assertIsNone(config)
        found = [m for m in messages if m.severity == "error"]
        self.assertTrue(found)
        self.assertTrue(all(m.yaml_path.startswith("clock.lpo") for m in found), [m.yaml_path for m in found])
        self.assertIn("min_hz <= nominal_hz <= max_hz", found[0].text)


class TimeoutTest(Profile):
    def test_timeout_shorter_than_three_periods(self):
        """20 ms task, 50 ms time-out: not even one cycle per service - error at the time-out, configure stops."""
        config, messages = self.check({"task_period_ms": 20, "watchdog_timeout_ms": 50})
        self.assertIsNone(config)
        found = [m for m in self.safety(messages) if m.severity == "error"]
        self.assertEqual([m.yaml_path for m in found], [PATH + "watchdog_timeout_ms"])
        self.assertIn("shorter than 3 periods", found[0].text)
        self.assertIn("3 x 20 ms = 60 ms", found[0].text)
        self.assertIn("expires after 46 ms", found[0].text)
        self.assertEqual(found[0].file, self.tmp / "project" / "project.yaml")
        self.assertIsInstance(found[0].line, int)

    def test_s32k1_wdog_limit(self):
        """TOVAL is 16 bit at 128 kHz: 511 ms is the longest time-out (15 cycles at the fastest LPO), 512 ms is an
        error."""
        config, messages = self.check({"watchdog_timeout_ms": 511})
        self.assertIsNotNone(config)
        self.assertEqual(config.safety_timing.service_cycles, 15)
        config, messages = self.check({"watchdog_timeout_ms": 512})
        self.assertIsNone(config)
        found = [m for m in self.safety(messages) if m.severity == "error"]
        self.assertEqual([m.yaml_path for m in found], [PATH + "watchdog_timeout_ms"])
        self.assertIn("at most 511 ms", found[0].text)

    def test_lateness_of_a_whole_period(self):
        """cycle_lateness_ms < task_period_ms is a constraint of the manifest."""
        config, messages = self.check({"cycle_lateness_ms": 10})
        self.assertIsNone(config)
        found = [m for m in messages if m.severity == "error"]
        self.assertEqual([m.yaml_path for m in found], [PATH + "cycle_lateness_ms"])
        self.assertIn("cycle_lateness_ms < task_period_ms", found[0].text)

    def test_value_outside_the_schema(self):
        config, messages = self.check({"task_period_ms": 0})
        self.assertIsNone(config)
        self.assertEqual([m.yaml_path for m in messages if m.severity == "error"], [PATH + "task_period_ms"])


class PeriodCheckTest(Profile):
    """The static_assert conditions of SafetyManager.cpp, reported before the compiler sees them."""

    def test_window_of_one_cycle_is_no_window(self):
        """10 ms task, 40 ms time-out: service every cycle, a window of one interval (at most 14 ticks) cannot be
        longer than the longest interval (at least 7 ticks)."""
        config, messages = self.check({"watchdog_timeout_ms": 40})
        self.assertIsNone(config)
        found = [m for m in self.safety(messages) if m.severity == "error"]
        self.assertEqual([m.yaml_path for m in found], [PATH + "task_period_ms"])
        self.assertIn("one interval (at most 14 ticks) is not shorter than the window of 1 cycles (at least 7 ticks)",
                      found[0].text)
        self.assertIn("SafetyManager.cpp", found[0].text)
        self.assertIn("LPO 882..1086 per mille of nominal (mcudb S32K144 clock.lpo)", found[0].text)

    def test_window_without_a_tick(self):
        config, messages = self.check({"task_period_ms": 1, "watchdog_timeout_ms": 4, "cycle_lateness_ms": 0})
        self.assertIsNone(config)
        texts = [m.text for m in self.safety(messages) if m.severity == "error"]
        self.assertEqual(len(texts), 1)
        self.assertIn("spans no tick of the timer (0 ticks)", texts[0])

    def test_two_cycles_are_enough_for_the_demo_period(self):
        """66 ms: service every 2 cycles (at 139 kHz 60.8 ms > 60 ms), window 15 ticks > interval 14 ticks."""
        config, messages = self.check({"watchdog_timeout_ms": 66})
        self.assertIsNotNone(config)
        self.assertEqual((config.safety_timing.interval_max_ticks, config.safety_timing.window_min_ticks), (14, 15))

    def test_window_beyond_the_wrap_around(self):
        """A 6-bit counter (other timer, own tree): 7 cycles x 14 ticks do not fit into one wrap-around."""
        config, _ = self.check()
        timing, messages = self.rerun(config, self.tree(timer={"CYCLE_TIMER_MASK": "0x3FU"}))
        self.assertIsNone(timing)
        self.assertEqual([m.yaml_path for m in messages], [PATH + "task_period_ms"])
        self.assertIn("does not fit into one wrap-around of the timer (mask 0x3f)", messages[0].text)

    def test_own_tree_with_the_real_values_passes(self):
        config, _ = self.check()
        timing, messages = self.rerun(config, self.tree())
        self.assertEqual(messages, [])
        self.assertEqual(timing, DEMO)

    def test_lpo_constants_in_the_header_are_not_read(self):
        """SYS-53: per-mille constants left in the timer header change nothing - mcudb is the one source."""
        config, _ = self.check()
        timing, _ = self.rerun(config, self.tree(timer={"LPO_TOLERANCE_MIN_PER_MILLE": "800U",
                                                        "LPO_TOLERANCE_MAX_PER_MILLE": "1200U"}))
        self.assertEqual(timing, DEMO)
        text = (REPO / TIMER_H).read_text(encoding="utf-8")
        self.assertNotIn("constexpr uint32_t LPO_TOLERANCE", text)

    def test_family_without_period_check_timer(self):
        """No period-check timer in the family port: nothing of it to check, the watchdog values still derived."""
        config, _ = self.check()
        empty = self.tmp / "empty"
        empty.mkdir()
        timing, messages = self.rerun(config, empty)
        self.assertEqual(messages, [])
        self.assertEqual((timing.service_cycles, timing.interval_max_ticks), (7, None))

    def test_constant_that_is_no_literal(self):
        config, _ = self.check()
        timing, messages = self.rerun(config, self.tree(timer={"CYCLE_TIMER_MASK": "0xFFFFU + 0U"}))
        self.assertIsNone(timing)
        self.assertEqual([(m.severity, m.yaml_path) for m in messages], [("error", PATH + "task_period_ms")])
        self.assertIn("not an integer literal", messages[0].text)


class SafeSupervisorTest(Profile):
    """SYS-53: module parameters of the safe supervisor - reset-loop limit (SYS-43) and device-bus window (SYS-29)."""

    def test_defaults(self):
        config, messages = self.check()
        self.assertEqual(self.safety(messages), [])
        use = config.integration.modules["safeSupervisor"]
        self.assertEqual(use.parameters, {"reset_loop_limit": 3, "reset_loop_clear_ms": 60000,
                                          "device_bus_window_ms": 200})
        self.assertEqual(use.derived[checks.BUS_WINDOW_CYCLES], (20, "ceil(device_bus_window_ms / "
                                                                     "safeLifecycle.task_period_ms) = ceil(200 / 10)"))

    def test_project_values(self):
        config, messages = self.check(safeSupervisor={"reset_loop_limit": 5, "reset_loop_clear_ms": 10000,
                                                      "device_bus_window_ms": 500})
        self.assertEqual(self.safety(messages), [])
        use = config.integration.modules["safeSupervisor"]
        self.assertEqual((use.parameters["reset_loop_limit"], use.parameters["reset_loop_clear_ms"]), (5, 10000))
        self.assertEqual(use.derived[checks.BUS_WINDOW_CYCLES][0], 50)

    def test_window_keeps_its_time_with_another_period(self):
        """The window is a time: a 5 ms task counts 40 cycles for 200 ms (before SYS-53 20 cycles, 100 ms); a
        3 ms task rounds up to 67 cycles (201 ms), never below the time set."""
        config, messages = self.check({"task_period_ms": 5}, GD_A={"cycle_ms": 100})
        self.assertEqual(self.safety(messages), [])
        self.assertEqual(config.integration.modules["safeSupervisor"].derived[checks.BUS_WINDOW_CYCLES][0], 40)
        config, _ = self.check({"task_period_ms": 3})
        self.assertEqual(config.integration.modules["safeSupervisor"].derived[checks.BUS_WINDOW_CYCLES][0], 67)

    def test_limits_of_the_schema(self):
        for name, value in (("reset_loop_limit", 0), ("reset_loop_limit", 256), ("reset_loop_clear_ms", 9),
                            ("reset_loop_clear_ms", 86400001), ("device_bus_window_ms", 0)):
            with self.subTest(name=name, value=value):
                config, messages = self.check(safeSupervisor={name: value})
                self.assertIsNone(config)
                self.assertEqual([m.yaml_path for m in messages if m.severity == "error"], [SUPERVISOR + name])

    def test_fault_free_time_shorter_than_a_period(self):
        """reset_loop_clear_ms 10 ms at a 20 ms task: the static_assert of main.cpp, here with the YAML path."""
        config, messages = self.check({"task_period_ms": 20, "watchdog_timeout_ms": 400},
                                      safeSupervisor={"reset_loop_clear_ms": 10})
        self.assertIsNone(config)
        found = [m for m in messages if m.severity == "error"]
        self.assertEqual([m.yaml_path for m in found], [SUPERVISOR + "reset_loop_clear_ms"])
        self.assertIn("shorter than one period of the safety task (task_period_ms 20 ms)", found[0].text)
        config, messages = self.check({"task_period_ms": 20, "watchdog_timeout_ms": 400},
                                      safeSupervisor={"reset_loop_clear_ms": 20})
        self.assertIsNotNone(config)

    def test_supervisor_without_safety_task(self):
        """A port with the supervisor's manifest but no safeLifecycle cannot count its times: error."""
        config, _ = self.check()
        del config.integration.modules["safeLifecycle"]
        manifests = dict(config.modules.manifests)
        del manifests["safeLifecycle"]
        with unittest.mock.patch.object(config.modules, "manifests", manifests):
            timing, messages = self.rerun(config, REPO)
        self.assertIsNone(timing)
        self.assertEqual([(m.severity, m.yaml_path) for m in messages], [("error", "modules.safeSupervisor")])

    def test_window_shorter_than_two_device_cycles(self):
        """150 ms window: the TMC6200 GD_A is supervised every 100 ms -> warning at the window."""
        config, messages = self.check(GD_A={"cycle_ms": 100}, safeSupervisor={"device_bus_window_ms": 150})
        self.assertIsNotNone(config)
        found = [m for m in self.safety(messages) if m.severity == "warning"]
        self.assertEqual([m.yaml_path for m in found], [SUPERVISOR + "device_bus_window_ms"])
        self.assertIn("window of 150 ms (15 safety cycles of 10 ms = 150 ms)", found[0].text)
        self.assertIn("GD_A, cycle_ms 100 ms", found[0].text)

    def test_long_enough_window(self):
        _, messages = self.check(GD_A={"cycle_ms": 100})                # 200 ms
        self.assertEqual(self.safety(messages), [])

    def test_more_device_buses_than_slots(self):
        config, _ = self.check()
        _, messages = self.rerun(config, self.tree(supervisor={"DEVICE_BUS_SLOTS": "1U"}))
        self.assertEqual([(m.severity, m.yaml_path) for m in messages], [("warning", "use.spi")])
        self.assertIn("devices on 2 (GD_A_SPI, GD_B_SPI)", messages[0].text)

    def test_window_constant_is_generated_not_written(self):
        """SafeSupervisor.h takes the window from the generated macro, no literal of its own."""
        text = (REPO / SUPERVISOR_H).read_text(encoding="utf-8")
        self.assertIn("DEVICE_BUS_WINDOW_CYCLES = NX_MODULE_SAFESUPERVISOR_DERIVED_DEVICE_BUS_WINDOW_CYCLES;", text)
        main = (REPO / "platforms/s32k1xx/port/main/src/main.cpp").read_text(encoding="utf-8")
        self.assertNotIn("PROVISIONAL", main)
        self.assertNotIn("#define NX_MODULE_SAFESUPERVISOR", main)


class ProjectsTest(Profile):
    def test_code_inputs_are_configure_inputs(self):
        """The files the constants were read from and the manifests are in NX_CONFIG_FILES (configure step reruns)."""
        config, _ = self.check()
        settings, _ = nxcfg.build_settings(config)
        files = [Path(f).resolve().as_posix() for f in settings["NX_CONFIG_FILES"]]
        for path in (MANIFEST, SUPERVISOR_MANIFEST, REPO / TIMER_H, REPO / SUPERVISOR_H):
            self.assertIn(path.resolve().as_posix(), files)

    def test_family_without_safety_package(self):
        """stm32 has no safeLifecycle module: nothing to derive, nothing to check."""
        if not NUCLEO_PROJECT.is_file():
            self.skipTest("nucleo_f446re_demo missing")
        config, messages = nxconfig.check(NUCLEO_PROJECT)
        self.assertIsNotNone(config)
        self.assertIsNone(config.safety_timing)
        self.assertNotIn("safeLifecycle", config.integration.modules)
        self.assertNotIn("safeSupervisor", config.integration.modules)

    def test_demo_project(self):
        """The eval board demo: 10 ms, 250 ms, 2 ms, every 7 cycles since SYS-53 (was 8), window 20 cycles."""
        if not DEMO_PROJECT.is_file():
            self.skipTest("private demo project missing")
        config, messages = nxconfig.check(DEMO_PROJECT)
        self.assertIsNotNone(config)
        self.assertEqual(self.safety(messages), [])
        self.assertEqual(config.safety_timing, DEMO)
        self.assertEqual(config.integration.modules["safeSupervisor"].derived[checks.BUS_WINDOW_CYCLES][0], 20)


if __name__ == "__main__":
    unittest.main()
