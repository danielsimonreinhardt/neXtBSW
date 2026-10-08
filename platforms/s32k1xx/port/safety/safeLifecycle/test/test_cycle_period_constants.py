"""SYS-40 (finding F9 of doc/safety/fmea.md, SM-04): the constants of the safety task's period check.

The period check of SafetyManager (platforms/s32k1xx/port/safety/safeLifecycle) derives its bounds from the nominal
period of the safety task. SYS-25: the period, the tolerated lateness and the time-out are one project value each
(modules.safeLifecycle.task_period_ms, cycle_lateness_ms, watchdog_timeout_ms; defaults in this directory's
module.yaml), the window of the check - the watchdog service interval - is derived by tools/nxconfig. SYS-53: the
LPO range is a fact of the derivative (mcudb clock.lpo, data sheet), its limits in per mille are derived by
tools/nxconfig as well, and the service interval is derived at the fastest LPO (decision sys25-lpo: demo 7 cycles).
This test takes the values from the configuration (nxconfig.check of the own test profile and, if present, the demo
project) and checks that the code has no second copy of them: SafetySystem.cpp schedules the task and
SafetyManager.h/.cpp check it with the generated macros, not with literals of their own (before SYS-25 this test kept
two literals equal).

It also recomputes the bounds and the times that doc/safety/fmea.md states for SM-04 from those values, with exact
fractions and independently of the C++ formula and of tools/nxconfig (checks.period_check_bounds):
- the largest interval that passes (14 ticks) and the smallest window (59 ticks, 68 before SYS-53);
- that no healthy interval (P + D at the fastest LPO) and no healthy window (W * P - D at the slowest) is reported;
- the guaranteed detection time of a long interval (17 ms) and the mean period below which a window is always
  reported (7.6 ms);
- the watchdog relation at the fastest LPO of mcudb: 3 x 7 x 10 ms fit into the 230.2 ms of 250 ms at 139 kHz.

Run: .venv/Scripts/python -m unittest platforms/s32k1xx/port/safety/safeLifecycle/test/test_cycle_period_constants.py
"""

from __future__ import annotations

import math
import re
import sys
import unittest
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[5]
sys.path.insert(0, str(REPO / "tools"))

import nxconfig  # noqa: E402
from nxconfig import checks  # noqa: E402

SAFETY_SYSTEM = REPO / "ecu/application/src/systems/SafetySystem.cpp"
MANAGER_H = REPO / "platforms/s32k1xx/port/safety/safeLifecycle/include/safeLifecycle/SafetyManager.h"
MANAGER_CPP = REPO / "platforms/s32k1xx/port/safety/safeLifecycle/src/safeLifecycle/SafetyManager.cpp"
WATCHDOG_H = REPO / "ecu/safety/safeWatchdog/include/safeWatchdog/SafeWatchdog.h"
TIMER_H = REPO / "platforms/s32k1xx/port/safety/safeSupervisionCheck/include/safeSupervisionCheck/LptmrCycleTimer.h"
FMEA = REPO / "doc/safety/fmea.md"
# own test profile of the family (S32K144 without modules.safeLifecycle: the defaults of the manifest)
PROFILE = REPO / "tools/bswgen/test/profiles/projects/testproject_safe_outputs/project.yaml"
DEMO_PROJECT = REPO / "projects/evalboard_s32k144_demo/project.yaml"
# SYS-53: LPO range of the profile's derivative, read here without tools/nxconfig
PROFILE_DERIVATIVE = REPO / "mcudb/s32k1xx/S32K144.yaml"

COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)
MACRO = "NX_MODULE_SAFELIFECYCLE_"


def initializer(path: Path, name: str) -> str:
    """Initializer of the one constexpr definition of `name` in `path` (comments ignored)."""
    text = COMMENT.sub("", path.read_text(encoding="utf-8"))
    found = re.findall(r"\bconstexpr\s+(?:static\s+)?(?:const\s+)?\w+\s+" + name + r"\s*=\s*([^;]+?)\s*;", text)
    if len(found) != 1:
        raise AssertionError(f"{path.relative_to(REPO) if path.is_relative_to(REPO) else path}: "
                             f"expected one constexpr {name}, found {len(found)}")
    return found[0]


def constant(path: Path, name: str) -> int:
    """The one constexpr integer literal `name` in `path`."""
    value = initializer(path, name)
    match = re.fullmatch(r"(0[xX][0-9a-fA-F]+|\d+)U?", value)
    if match is None:
        raise AssertionError(f"{path.name}: {name} = {value} is no integer literal")
    return int(match.group(1), 0)


def derivative_lpo() -> tuple[int, int, int]:
    """(nominal, min, max) in Hz of the LPO of the profile's derivative (mcudb clock.lpo)."""
    text = PROFILE_DERIVATIVE.read_text(encoding="utf-8")
    match = re.search(r"\blpo:\s*\{\s*nominal_hz:\s*(\d+),\s*min_hz:\s*(\d+),\s*max_hz:\s*(\d+)", text)
    if match is None:
        raise AssertionError(f"{PROFILE_DERIVATIVE.name}: no clock.lpo")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def timing(project: Path = PROFILE) -> checks.SafetyTiming:
    config, messages = nxconfig.check(project)
    if config is None:
        raise AssertionError("; ".join(m.text for m in messages if m.severity == "error"))
    return config.safety_timing


class Bounds:
    """The bounds as SafetyManager.cpp derives them, recomputed with fractions."""

    def __init__(self, values: checks.SafetyTiming | None = None):
        values = values or timing()
        self.period = values.period_ms
        self.late = values.lateness_ms
        self.window = values.service_cycles
        hz = constant(TIMER_H, "CYCLE_TIMER_NOMINAL_HZ")
        self.fmin = Fraction(hz, 1000) * Fraction(values.lpo_min_per_mille, 1000)
        self.fmax = Fraction(hz, 1000) * Fraction(values.lpo_max_per_mille, 1000)
        self.max_interval = math.floor((self.period + self.late) * self.fmax) + 1
        self.min_window = math.floor((self.window * self.period - self.late) * self.fmin)


class CyclePeriodConstantsTest(unittest.TestCase):
    def test_code_takes_the_values_from_the_configuration(self):
        """One value each: the task is scheduled and checked with the generated macros, no literal in the code."""
        uses = [(SAFETY_SYSTEM, "SYSTEM_CYCLE_TIME", MACRO + "PARAM_TASK_PERIOD_MS"),
                (MANAGER_H, "CYCLE_PERIOD_MS", MACRO + "PARAM_TASK_PERIOD_MS"),
                (MANAGER_H, "CYCLE_LATENESS_MS", MACRO + "PARAM_CYCLE_LATENESS_MS"),
                (MANAGER_H, "WATCHDOG_CYCLIC_COUNTER", MACRO + "DERIVED_WATCHDOG_SERVICE_CYCLES"),
                (MANAGER_CPP, "LPO_MIN_PER_MILLE", MACRO + "DERIVED_LPO_MIN_PER_MILLE"),
                (MANAGER_CPP, "LPO_MAX_PER_MILLE", MACRO + "DERIVED_LPO_MAX_PER_MILLE"),
                (WATCHDOG_H, "WATCHDOG_TIMEOUT_MS", MACRO + "PARAM_WATCHDOG_TIMEOUT_MS")]
        for path, name, macro in uses:
            with self.subTest(name=name):
                self.assertEqual(initializer(path, name), macro)

    def test_a_literal_would_be_found(self):
        """Counter-check of the test above: a second copy of the period as a literal is seen."""
        with self.assertRaises(AssertionError):
            constant(SAFETY_SYSTEM, "SYSTEM_CYCLE_TIME")
        self.assertEqual(initializer(TIMER_H, "CYCLE_TIMER_NOMINAL_HZ"), "1000U")

    def test_configuration_values(self):
        """Manifest defaults (own profile) and the demo: 10 ms, 2 ms, time-out 250 ms, window 7 cycles (SYS-53, at
        the fastest LPO; 8 before), LPO 882 ... 1086 per mille."""
        projects = [PROFILE] + ([DEMO_PROJECT] if DEMO_PROJECT.is_file() else [])
        for project in projects:
            with self.subTest(project=project.parent.name):
                t = timing(project)
                self.assertEqual((t.period_ms, t.lateness_ms, t.timeout_ms, t.service_cycles), (10, 2, 250, 7))
                self.assertEqual((t.lpo_min_per_mille, t.lpo_max_per_mille), (882, 1086))

    def test_nxconfig_bounds_match_the_fractions(self):
        """tools/nxconfig reports the static_asserts with the integer formula of SafetyManager.cpp; the same
        bounds come out of the exact fractions here, for the demo values and for other ones."""
        hz = constant(TIMER_H, "CYCLE_TIMER_NOMINAL_HZ")
        t = timing()
        low, high = t.lpo_min_per_mille, t.lpo_max_per_mille
        for period, late, timeout in ((10, 2, 250), (5, 1, 250), (20, 3, 400), (7, 0, 511), (1, 0, 50)):
            with self.subTest(period=period, late=late, timeout=timeout):
                cycles = checks.service_cycles(period, timeout, high)
                b = Bounds(checks.SafetyTiming(period, timeout, late, cycles, None, None, low, high))
                self.assertEqual(checks.period_check_bounds(period, late, cycles, hz, low, high),
                                 (b.max_interval, b.min_window))

    def test_lpo_limits_cover_the_data_sheet(self):
        # [DS] Rev. 13 Table 33: 113 ... 139 kHz (S32K1xx), up to 141 kHz (S32K14xW), nominal 128 kHz; mcudb has the
        # S32K1xx column, the per-mille limits are rounded outwards
        lpo = derivative_lpo()
        self.assertEqual(lpo, (128000, 113000, 139000))
        b = Bounds()
        self.assertLessEqual(b.fmin, Fraction(lpo[1], lpo[0]))
        self.assertGreaterEqual(b.fmax, Fraction(lpo[2], lpo[0]))
        self.assertLess(b.fmax - Fraction(lpo[2], lpo[0]), Fraction(1, 1000))    # not wider than the rounding

    def test_watchdog_relation_at_the_fastest_lpo(self):
        """SYS-53 (sys25-lpo): three service intervals fit into the shortest time-out of the data sheet, the WDOG
        loaded for the nominal LPO and running at its fastest - recomputed with exact fractions of mcudb."""
        nominal, _, fastest = derivative_lpo()
        t = timing()
        shortest = Fraction(t.timeout_ms * nominal, fastest)                 # 250 ms at 139/128: 230.2 ms
        self.assertEqual(round(float(shortest), 1), 230.2)
        self.assertLessEqual(3 * t.service_cycles * t.period_ms, shortest)
        self.assertGreater(3 * (t.service_cycles + 1) * t.period_ms, shortest)  # the most that fit

    def test_bounds_and_times_of_the_fmea(self):
        b = Bounds()
        self.assertEqual(b.max_interval, 14)
        self.assertEqual(b.min_window, 59)
        # a counter difference misses the true time by less than one tick either way
        healthy_interval = (b.period + b.late) * b.fmax          # longest healthy interval, fastest tick
        self.assertLessEqual(math.ceil(healthy_interval), b.max_interval)
        healthy_window = (b.window * b.period - b.late) * b.fmin  # shortest healthy window, slowest tick
        self.assertGreaterEqual(math.floor(healthy_window), b.min_window)
        # guaranteed detection: floor(t * f) > max_interval for every t >= 17 ms at every LPO of the data sheet
        # (slowest 113/128 kHz; the per-mille constant is rounded down only to keep healthy windows safe)
        guaranteed = Fraction(b.max_interval + 1) / Fraction(113, 128)
        self.assertEqual(math.ceil(guaranteed), 17)
        # window: always reported below this mean period at every LPO (ceil(t * f) < min_window, fastest of mcudb)
        nominal, _, fastest = derivative_lpo()
        fast = Fraction(b.min_window - 1) / Fraction(fastest, nominal) / b.window
        self.assertEqual(round(float(fast), 1), 7.6)

    def test_fmea_states_the_derived_times(self):
        text = FMEA.read_text(encoding="utf-8")
        section = text[text.index("#### SM-04"):]
        section = section[:section.index("\n#### ")]
        for fact in ("17 ms", "14 ticks", "59 ticks", "7.6 ms"):
            self.assertIn(fact, section, f"fmea.md SM-04 should state {fact}")


if __name__ == "__main__":
    unittest.main()
