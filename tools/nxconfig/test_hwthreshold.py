"""Hardware thresholds (SYS-86): derivative facts of the comparator and the checks of hwthreshold.py.

Own profile only (tools/bswgen/test/profiles: testboard_hw_threshold / testproject_hw_threshold, S32K144 LQFP100,
bridge on FTM3), copied into a temporary tree and varied per test; the demo project is only checked to be unchanged.
Every expected number is calculated by hand from the board values in the comments below, not by the code under test
(counter-check of the inverse characteristic and of the rounding towards the safe side):

  DC_LINK_I    2 mOhm * gain 20 = 40 mV/A around 2500 mV (half of SUPPLY_5V 5000 mV): U = 2500 + 0.04 * I[mA]
  BUS_VOLTAGE  divider 24k / 2k2: U = U_bus * 2200 / 26200
  DAC          8 bit: level k gives V_in * (k + 1) / 256; supply V_in 5000 mV (LSB 19.53 mV), bandgap 1000 mV

Run: .venv/Scripts/python -m unittest tools/nxconfig/test_hwthreshold.py
"""

import copy
import shutil
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))

import nxconfig  # noqa: E402
from nxconfig import hwthreshold  # noqa: E402
from nxconfig.config import _read, Reporter  # noqa: E402
from nxconfig.model import Derivative  # noqa: E402

PROFILES = REPO / "tools" / "bswgen" / "test" / "profiles"
BOARD = PROFILES / "boards" / "testboard_hw_threshold" / "board.yaml"
PROJECT = PROFILES / "projects" / "testproject_hw_threshold" / "project.yaml"
# PLAT-62: the own full-featured test profile (synthetic testboard_full/testproject_full) instead of the eval board
# project, which stays private (PLAT-46 F10) - a test against it would be red in the exported open tree. What
# proves something about the eval board itself uses EVAL_DEMO and is skipped where it is missing.
DEMO = REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full" / "project.yaml"
EVAL_DEMO = REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml"


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class RealDerivatives(unittest.TestCase):
    """The mcudb facts: CMP0 of the S32K14x with the data sheet limits, nothing claimed for the S32K116."""

    def test_s32k14x_cmp0(self):
        for name in ("S32K142", "S32K144", "S32K146", "S32K148"):
            d = _read(Reporter(), "derivative", REPO / f"mcudb/s32k1xx/{name}.yaml", Derivative)
            self.assertIsNotNone(d, name)
            (cmp,) = d.comparators
            self.assertEqual((cmp.name, cmp.inputs, cmp.dac_bits, cmp.supply_reference), ("CMP0", 8, 8, "VDDA"))
            self.assertEqual((cmp.bandgap_min_mv, cmp.bandgap_mv, cmp.bandgap_max_mv), (970, 1000, 1030))
            self.assertEqual((cmp.offset_max_mv, cmp.dac_inl_max_millilsb), (25, 750))
            self.assertEqual((cmp.propagation_max_ns, cmp.settling_max_us), (500, 30))
            # FTM0..3 fault inputs 0..2 through TRGMUX; FTM4..7 of the S32K146/148 are pin only ([RM13] 47.1.3)
            self.assertEqual(cmp.fault_inputs, {f"FTM{i}": [0, 1, 2] for i in range(4)})
            self.assertIn("[DS13] Table 44", cmp.source)
            self.assertFalse(cmp.verify)

    def test_s32k116_not_described(self):
        # FTM0 of the S32K116 has one fault input ([RM13] Table 47-1) - which one is not stated, so nothing is claimed
        d = _read(Reporter(), "derivative", REPO / "mcudb/s32k1xx/S32K116.yaml", Derivative)
        self.assertEqual(d.comparators, [])

    def test_model_rejects_a_comparator_without_reference_or_with_a_broken_band(self):
        base = {"name": "CMP0", "inputs": 8, "dac_bits": 8, "offset_max_mv": 25, "dac_inl_max_millilsb": 750,
                "propagation_max_ns": 500, "settling_max_us": 30, "fault_inputs": {"FTM0": [0]}}
        from nxconfig.model import DerivativeComparator
        with self.assertRaisesRegex(ValueError, "no reference"):
            DerivativeComparator.model_validate(base)
        with self.assertRaisesRegex(ValueError, "bandgap_min_mv <= bandgap_mv <= bandgap_max_mv"):
            DerivativeComparator.model_validate({**base, "bandgap_mv": 1000, "bandgap_min_mv": 1010,
                                                 "bandgap_max_mv": 1030})
        with self.assertRaisesRegex(ValueError, "needs bandgap_min_mv"):
            DerivativeComparator.model_validate({**base, "bandgap_mv": 1000})


class Tree(unittest.TestCase):
    """Temporary tree: a copy of the s32k1xx derivative files, the test board and project, varied per test."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.mcudb = self.tmp / "mcudb"
        shutil.copytree(REPO / "mcudb" / "s32k1xx", self.mcudb / "s32k1xx")
        self.board = load(BOARD)
        self.project = load(PROJECT)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def check(self, board=None, project=None):
        board_dir, project_dir = self.tmp / "boards" / "b", self.tmp / "projects" / "p"
        board_dir.mkdir(parents=True, exist_ok=True)
        project_dir.mkdir(parents=True, exist_ok=True)
        (board_dir / "board.yaml").write_text(yaml.safe_dump(board or self.board, sort_keys=False), encoding="utf-8")
        project = copy.deepcopy(project or self.project)
        project["project"]["board"] = "../../boards/b"
        path = project_dir / "project.yaml"
        path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        return nxconfig.check(path, self.mcudb)

    def threshold(self, **values):
        t = self.project["hw_thresholds"]["DC_OVERCURRENT"]
        t.update(values)
        return t

    def plan(self, messages, config):
        errors = [str(m) for m in messages if m.severity == "error"]
        self.assertEqual(errors, [])
        (plan,) = config.hw_thresholds
        return plan

    def assert_error(self, messages, path: str, fragment: str, level: str = "project"):
        errors = [m for m in messages if m.severity == "error"]
        match = [m for m in errors if m.yaml_path == path and fragment in m.text]
        self.assertTrue(match, f"no error {path!r} with '{fragment}' in {[str(m) for m in errors]}")
        self.assertEqual(match[0].file.name, f"{level}.yaml")
        self.assertIsNotNone(match[0].line)


class Plans(Tree):
    """Derived values against the hand calculation."""

    def test_profile_overcurrent_above_on_the_supply(self):
        config, messages = self.check()
        t = self.plan(messages, config)
        # 40000 mA -> 2500 + 1600 = 4100 mV; 4100 * 256 / 5000 = 209.92 -> level floor - 1 = 208
        self.assertEqual(t.pin_mv, Fraction(4100))
        self.assertEqual((t.dac_level, t.dac_bits, t.pin_trip), (208, 8, "above"))
        self.assertEqual(t.dac_mv, Fraction(5000 * 209, 256))                    # 4082.03 mV
        self.assertEqual(t.realised, (Fraction(5000 * 209, 256) - 2500) * 25)   # 39550.8 mA <= 40000: safe side
        self.assertLessEqual(t.realised, 40000)
        # band: offset 25 mV + INL 0.75 LSB (14.65 mV) at the pin, 25 mA per mV; supply tolerance not included
        spread = (25 + Fraction(3, 4) * Fraction(5000, 256)) * 25
        self.assertEqual(t.band, (t.realised - spread, t.realised + spread))
        self.assertTrue(t.supply_tolerance_open)
        # route and filter: first fault input of FTM3; 200 ns at 64 MHz = 12.8 -> 13 clocks; 500 ns + 17 clocks
        self.assertEqual((t.timer, t.fault_input, t.filter_ticks), ("FTM3", 0, 13))
        self.assertEqual(t.timer_hz, 64_000_000)
        self.assertEqual(t.reaction_max_ns, 500 + Fraction(17 * 10**9, 64_000_000))
        self.assertTrue(t.confirmed)
        self.assertIn("latched until reset", hwthreshold.info(config)[0])

    def test_below_rounds_the_other_way(self):
        # -30000 mA -> 2500 - 1200 = 1300 mV; 1300 * 256 / 5000 = 66.56 -> ceil - 1 = 66 -> 1308.6 mV = -29785 mA
        self.threshold(trip="below", threshold=-30000, filter_ns=0)
        config, messages = self.check()
        t = self.plan(messages, config)
        self.assertEqual((t.dac_level, t.pin_trip, t.filter_ticks), (66, "below", 0))
        self.assertEqual(t.realised, (Fraction(5000 * 67, 256) - 2500) * 25)
        self.assertGreaterEqual(t.realised, -30000)                             # trips at or above the threshold
        self.assertEqual(t.reaction_max_ns, 500 + Fraction(3 * 10**9, 64_000_000))  # 3 clocks without filter

    def test_bandgap_on_the_shared_adc_pin(self):
        # BUS_VOLTAGE at PTC2 = ADC0_SE10 = CMP0_IN5 (claimed by the used ADC channel: allowed)
        # 11000 mV -> 11000 * 2200 / 26200 = 923.66 mV; * 256 / 1000 = 236.45 -> 235 -> 921.875 mV = 10978.7 mV
        self.threshold(input="BUS_VOLTAGE_CMP", threshold=11000, reference="bandgap", filter_ns=0)
        config, messages = self.check()
        t = self.plan(messages, config)
        self.assertEqual((t.input_index, t.pin, t.dac_level, t.reference), (5, "PTC2", 235, "bandgap"))
        self.assertEqual(t.realised, Fraction(1000 * 236, 256) * Fraction(26200, 2200))
        self.assertLessEqual(t.realised, 11000)
        # bandgap 970 .. 1030 mV widens the band beyond offset and INL
        lsb = Fraction(1000, 256)
        spread = 25 + Fraction(3, 4) * lsb
        low = (Fraction(1000 * 236, 256) * Fraction(970, 1000) - spread) * Fraction(26200, 2200)
        high = (Fraction(1000 * 236, 256) * Fraction(1030, 1000) + spread) * Fraction(26200, 2200)
        self.assertEqual(t.band, (low, high))
        self.assertFalse(t.supply_tolerance_open)

    def test_a_falling_characteristic_swaps_the_pin_direction(self):
        # linear sensor value = 3000 - U (gain -1 per mV): value above 1000 <=> pin below 2000 mV
        self.board["adc"][0]["sensor"] = {"kind": "linear", "unit": "mV", "gain_num": -1, "offset": 3000,
                                          "source": "synthetic falling characteristic SYS-86"}
        self.threshold(threshold=1000, filter_ns=0)
        config, messages = self.check()
        t = self.plan(messages, config)
        self.assertEqual((t.pin_trip, t.pin_mv), ("below", Fraction(2000)))
        # the pin trips below: the DAC is the next level at or ABOVE 2000 mV - 2000 * 256 / 5000 = 102.4 -> ceil 103
        # -> level 102 -> 5000 * 103 / 256 = 2011.7 mV = value 988.3 (the level below, 1992.2 mV = 1007.8, is late)
        self.assertEqual(t.dac_level, 102)
        self.assertEqual(t.dac_mv, Fraction(5000 * 103, 256))
        self.assertLessEqual(t.realised, 1000)                                  # value above: trips at or below

    def test_unconfirmed_characteristic_warns(self):
        self.board["adc"][0]["sensor"]["verify"] = True
        config, messages = self.check()
        t = self.plan(messages, config)
        self.assertFalse(t.confirmed)
        warnings = [m for m in messages if m.severity == "warning" and "DC_OVERCURRENT" in m.text]
        self.assertTrue(any(m.yaml_path == "adc[0]" and "not confirmed" in m.text for m in warnings), warnings)

    def test_demo_unchanged(self):
        config, messages = nxconfig.check(DEMO)
        self.assertIsNotNone(config)
        self.assertEqual(config.hw_thresholds, [])
        self.assertEqual([m for m in messages if "hardware threshold" in m.text], [])


class Refusals(Tree):
    """Every refusal with its YAML path, file and line."""

    def test_unknown_input(self):
        self.threshold(input="NOPE")
        self.assert_error(self.check()[1], "hw_thresholds.DC_OVERCURRENT.input", "has no comparator input NOPE")

    def test_comparator_the_derivative_does_not_have(self):
        self.board["comparators"]["inputs"][0]["instance"] = "CMP1"
        self.assert_error(self.check()[1], "comparators.inputs[0].instance", "describes no comparator CMP1", "board")

    def test_input_number(self):
        self.board["comparators"]["inputs"][0]["input"] = 8
        self.assert_error(self.check()[1], "comparators.inputs[0].input", "IN0 .. IN7", "board")

    def test_pin_is_no_comparator_input(self):
        self.board["comparators"]["inputs"][0]["pin"] = "PTE9"
        self.assert_error(self.check()[1], "comparators.inputs[0].pin", "PTE9 is no CMP0_IN3", "board")

    def test_debug_pin(self):
        self.board["comparators"]["inputs"][0].update(input=2, pin="PTC4")
        self.assert_error(self.check()[1], "comparators.inputs[0].pin", "debug interface pin", "board")

    def test_pin_used_otherwise(self):
        self.board["dio"] = {"inputs": [{"name": "SWITCH", "pin": "PTE8"}]}
        self.project["use"]["dio"] = "all"
        self.assert_error(self.check()[1], "hw_thresholds.DC_OVERCURRENT.input", "is used by dio SWITCH")

    def test_signal_unknown_unfitted_or_not_linear(self):
        board = copy.deepcopy(self.board)
        board["comparators"]["inputs"][0]["signal"] = "NOPE"
        self.assert_error(self.check(board)[1], "comparators.inputs[0].signal", "no ADC entry NOPE", "board")
        board = copy.deepcopy(self.board)
        board["adc"][0] = {"name": "DC_LINK_I", "instance": "ADC0", "channel": "SE12", "pin": "PTC14",
                           "fitted": False}
        self.assert_error(self.check(board)[1], "comparators.inputs[0].signal", "not fitted", "board")
        board = copy.deepcopy(self.board)
        board["adc"][0]["sensor"] = {"kind": "pullup", "pullup_ohm": 10000, "supply": {"rail": "SUPPLY_5V"},
                                     "source": "synthetic"}
        self.assert_error(self.check(board)[1], "comparators.inputs[0].signal", "no linear characteristic", "board")

    def test_pwm_group_not_used(self):
        del self.project["pwm"]
        self.assert_error(self.check()[1], "hw_thresholds.DC_OVERCURRENT.pwm", "is not used by the project")

    def test_timer_not_reached(self):
        path = self.mcudb / "s32k1xx" / "S32K144.yaml"
        path.write_text(path.read_text(encoding="utf-8").replace(
            "fault_inputs: { FTM0: [0, 1, 2], FTM1: [0, 1, 2], FTM2: [0, 1, 2], FTM3: [0, 1, 2] }",
            "fault_inputs: { FTM0: [0, 1, 2] }"), encoding="utf-8")
        self.assert_error(self.check()[1], "hw_thresholds.DC_OVERCURRENT.pwm", "reaches no fault input of FTM3")

    def test_one_threshold_per_comparator(self):
        self.project["hw_thresholds"]["BUS_OVERVOLTAGE"] = {"input": "BUS_VOLTAGE_CMP", "trip": "above",
                                                             "threshold": 11000, "reference": "bandgap",
                                                             "pwm": "BRIDGE"}
        self.assert_error(self.check()[1], "hw_thresholds.BUS_OVERVOLTAGE.input", "already serves")

    def test_supply_reference_needs_the_board_supply(self):
        del self.board["comparators"]["supply"]
        self.assert_error(self.check()[1], "comparators.supply", "must state the voltage at VDDA", "board")

    def test_threshold_outside_the_reference(self):
        self.threshold(threshold=70000)                       # 2500 + 2800 = 5300 mV > 5000 mV
        self.assert_error(self.check()[1], "hw_thresholds.DC_OVERCURRENT.threshold", "outside the range")
        self.threshold(threshold=40000, reference="bandgap")  # 4100 mV > 1000 mV
        self.assert_error(self.check()[1], "hw_thresholds.DC_OVERCURRENT.threshold", "outside the range")
        self.threshold(threshold=-62400, reference="supply", trip="above")  # 2500 - 2496 = 4 mV < LSB 19.5 mV
        self.assert_error(self.check()[1], "hw_thresholds.DC_OVERCURRENT.threshold", "below the lowest DAC level")

    def test_filter_longer_than_the_fault_filter(self):
        self.threshold(filter_ns=300)                         # 19.2 -> 20 clocks > 15; 15 clocks = 234 ns
        self.assert_error(self.check()[1], "hw_thresholds.DC_OVERCURRENT.filter_ns", "at most 234 ns")

    def test_schema_refuses_unknown_direction_and_long_filter(self):
        self.threshold(trip="over")
        self.assertIsNone(self.check()[0])
        self.threshold(trip="above", filter_ns=100_001)
        self.assertIsNone(self.check()[0])


if __name__ == "__main__":
    unittest.main()
