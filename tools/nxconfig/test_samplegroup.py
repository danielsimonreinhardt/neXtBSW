"""Sample groups (IO-62): derivative facts of the trigger chain and the checks of samplegroup.py.

Own profile only (tools/bswgen/test/profiles: testboard_sample_group / testproject_sample_group, S32K144 LQFP100,
bridge on FTM3 center aligned, trigger at zero_low), copied into a temporary tree and varied per test; the demo
project is only checked to be unchanged. Expected numbers by hand from the profile, not from the code under test:

  PDB clock      SYS_CLK = core 80 MHz -> 12.5 ns; delay 500 ns = 40 clocks; + 2 start clocks = 525 ns
  conversion     bspAdcAccess defaults: (42 + 1 + 28 + 5) = 76 ADC clocks at 48 MHz (FIRCDIV2) = 1583.3 -> 1584 ns
                 + 5 bus clocks at 40 MHz = 125 ns -> 1709 ns
  next sample    back-to-back: end of the previous conversion + 2 PDB clocks (25 ns) -> 525 + 1709 + 25 = 2259 ns
  end            2259 + 1709 = 3968 ns; PWM period 50 us at 20 kHz

Run: .venv/Scripts/python -m unittest tools/nxconfig/test_samplegroup.py
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
from nxconfig import samplegroup  # noqa: E402
from nxconfig.config import _read, Reporter  # noqa: E402
from nxconfig.model import Derivative, DerivativeTriggerRouter  # noqa: E402

PROFILES = REPO / "tools" / "bswgen" / "test" / "profiles"
BOARD = PROFILES / "boards" / "testboard_sample_group" / "board.yaml"
PROJECT = PROFILES / "projects" / "testproject_sample_group" / "project.yaml"
# PLAT-62: the own full-featured test profile (synthetic testboard_full/testproject_full) instead of the eval board
# project, which stays private (PLAT-46 F10) - a test against it would be red in the exported open tree. What
# proves something about the eval board itself uses EVAL_DEMO and is skipped where it is missing.
DEMO = REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full" / "project.yaml"
EVAL_DEMO = REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml"


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class RealDerivatives(unittest.TestCase):
    """The mcudb facts: TRGMUX codes and outputs and the two PDBs of the S32K14x ([RM13] chapter 19, 43, 46)."""

    def test_s32k14x_router_and_sequencers(self):
        ftms = {"S32K142": 4, "S32K144": 4, "S32K146": 6, "S32K148": 8}
        for name, count in ftms.items():
            d = _read(Reporter(), "derivative", REPO / f"mcudb/s32k1xx/{name}.yaml", Derivative)
            self.assertIsNotNone(d, name)
            r = d.trigger_router
            self.assertEqual((r.name, r.register_lock), ("TRGMUX", True))
            # [RM13] Table 19-1: FTM0_INIT_TRIG 0x16 .. FTM3_EXT_TRIG 0x1D, FTM4_INIT_TRIG 0x45 .. FTM7_EXT_TRIG 0x4C
            self.assertEqual(r.sources["FTM0_INIT_TRIG"], 0x16)
            self.assertEqual(r.sources["FTM3_EXT_TRIG"], 0x1D)
            self.assertEqual(r.sources["CMP0_OUT"], 0x0E)          # the code SYS-86 writes (HwThreshold.h)
            self.assertEqual(len(r.timer_triggers), count)
            if count == 8:
                self.assertEqual((r.sources["FTM4_INIT_TRIG"], r.sources["FTM7_EXT_TRIG"]), (0x45, 0x4C))
            targets = {t.name: (t.register_index, t.selector) for t in r.targets}
            # Figure 19-2: out56 = PDB0_trigger_in0, out60 = PDB1_trigger_in0; index = out / 4 (TRGMUX_PDB0_INDEX 14)
            self.assertEqual(targets["PDB0_TRIGGER_IN0"], (14, 0))
            self.assertEqual(targets["PDB1_TRIGGER_IN0"], (15, 0))
            # out41 = FTM0_FAULT0 (TRGMUX_FTM0 SEL1), out55 = FTM3_FAULT2: the route of SYS-86 (HwThreshold.h)
            self.assertEqual(targets["FTM0_FAULT0"], (10, 1))
            self.assertEqual(targets["FTM3_FAULT2"], (13, 3))
            seqs = {(s.name, s.adc, s.trigger_target, s.slots, s.back_to_back, s.counter_bits, s.clock, s.start_clocks)
                    for s in d.sample_sequencers}
            self.assertEqual(seqs, {("PDB0", "ADC0", "PDB0_TRIGGER_IN0", 8, True, 16, "core", 2),
                                    ("PDB1", "ADC1", "PDB1_TRIGGER_IN0", 8, True, 16, "core", 2)})
            self.assertTrue(all("[RM13]" in s.source for s in d.sample_sequencers))

    def test_s32k116_not_described(self):
        # one PDB, "no chaining supported" on S32K11x ([RM13] Table 46-5): nothing claimed until checked on a part
        d = _read(Reporter(), "derivative", REPO / "mcudb/s32k1xx/S32K116.yaml", Derivative)
        self.assertIsNone(d.trigger_router)
        self.assertEqual(d.sample_sequencers, [])

    def test_router_model_rejects_broken_references(self):
        base = {"name": "R", "sources": {"A": 1}, "targets": [{"name": "T", "register_index": 1, "selector": 0}]}
        with self.assertRaisesRegex(ValueError, "no router input"):
            DerivativeTriggerRouter.model_validate({**base, "timer_triggers": {"TIM": {"counter": "A", "match": "B"}}})
        with self.assertRaisesRegex(ValueError, "share one selector field"):
            DerivativeTriggerRouter.model_validate({**base, "targets": base["targets"] + [
                {"name": "U", "register_index": 1, "selector": 0}]})
        with self.assertRaisesRegex(ValueError, "is no target"):
            DerivativeTriggerRouter.model_validate({**base, "fault_inputs": {"TIM": ["X"]}})


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

    def check(self):
        board_dir, project_dir = self.tmp / "boards" / "b", self.tmp / "projects" / "p"
        board_dir.mkdir(parents=True, exist_ok=True)
        project_dir.mkdir(parents=True, exist_ok=True)
        (board_dir / "board.yaml").write_text(yaml.safe_dump(self.board, sort_keys=False), encoding="utf-8")
        project = copy.deepcopy(self.project)
        project["project"]["board"] = "../../boards/b"
        path = project_dir / "project.yaml"
        path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        return nxconfig.check(path, self.mcudb)

    def derivative(self, change):
        """Change the S32K144 file of the temporary mcudb (dict in, dict out)."""
        path = self.mcudb / "s32k1xx" / "S32K144.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        change(data)
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    @property
    def group(self) -> dict:
        return self.project["sample_groups"]["MOT_SAMPLE"]

    def plan(self, messages, config):
        errors = [str(m) for m in messages if m.severity == "error"]
        self.assertEqual(errors, [])
        (plan,) = config.sample_groups
        return plan

    def assert_error(self, messages, path: str, fragment: str, level: str = "project"):
        errors = [m for m in messages if m.severity == "error"]
        match = [m for m in errors if m.yaml_path == path and fragment in m.text]
        self.assertTrue(match, f"no error {path!r} with '{fragment}' in {[str(m) for m in errors]}")
        self.assertEqual(match[0].file.name, f"{level}.yaml")
        self.assertIsNotNone(match[0].line)


class Plans(Tree):
    """Derived values against the hand calculation."""

    def test_profile(self):
        config, messages = self.check()
        g = self.plan(messages, config)
        self.assertEqual((g.pwm, g.timer, g.trigger_point, g.router), ("BRIDGE", "FTM3", "zero_low", "TRGMUX"))
        self.assertEqual(g.timer_trigger, {"counter": 0x1C, "match": 0x1D})
        self.assertEqual((g.delay_ns, g.delay_ticks, g.sequencer_hz), (500, 40, 80_000_000))
        self.assertEqual(g.realised_delay_ns, 525)
        # result order = project order; slots per ADC in project order
        self.assertEqual([(m.name, m.adc, m.channel, m.sequencer, m.slot) for m in g.members],
                         [("PHASE_I_U", "ADC0", "SE4", "PDB0", 0), ("PHASE_I_V", "ADC1", "SE2", "PDB1", 0),
                          ("PHASE_I_W", "ADC1", "SE3", "PDB1", 1), ("DC_LINK_U", "ADC0", "SE10", "PDB0", 1)])
        self.assertEqual([m.sample_ns for m in g.members], [525, 525, 2259, 2259])
        self.assertEqual([(s.name, s.adc, s.target, s.register_index, s.selector, s.slots, s.conversion_ns)
                          for s in g.sequencers],
                         [("PDB0", "ADC0", "PDB0_TRIGGER_IN0", 14, 0, 2, 1709),
                          ("PDB1", "ADC1", "PDB1_TRIGGER_IN0", 15, 0, 2, 1709)])
        self.assertEqual(g.end_ns, 3968)
        self.assertEqual(g.completing, "PDB0")                # equal ends: the first sequencer in result order
        self.assertEqual(g.period_ns, 50_000)
        self.assertTrue(g.back_to_back and g.confirmed)
        line = samplegroup.info(config)[0]
        self.assertIn("PHASE_I_W 2259 ns", line)
        self.assertIn("complete 3968 ns", line)

    def test_same_adc_warns_with_the_offset(self):
        config, messages = self.check()
        self.plan(messages, config)
        warnings = [m for m in messages if m.severity == "warning" and "same ADC" in m.text]
        self.assertEqual(sorted(m.yaml_path for m in warnings),
                         ["sample_groups.MOT_SAMPLE.channels[2]", "sample_groups.MOT_SAMPLE.channels[3]"])
        self.assertTrue(all("1734 ns apart" in m.text for m in warnings), [m.text for m in warnings])

    def test_parallel_only_no_warning_and_the_longer_adc_completes(self):
        self.group["channels"] = ["PHASE_I_U", "PHASE_I_V", "PHASE_I_W"]
        self.group["delay_ns"] = 0
        config, messages = self.check()
        g = self.plan(messages, config)
        self.assertEqual(g.completing, "PDB1")                # two conversions on ADC1, one on ADC0
        self.assertEqual(g.realised_delay_ns, 25)            # 0 clocks + 2 start clocks
        self.assertEqual(g.end_ns, 25 + 1709 + 25 + 1709)
        self.assertEqual(len([m for m in messages if "same ADC" in m.text]), 1)
        self.group["channels"] = ["PHASE_I_U", "PHASE_I_V"]
        config, messages = self.check()
        g = self.plan(messages, config)
        self.assertEqual([m for m in messages if "same ADC" in m.text], [])
        self.assertEqual([m.sample_ns for m in g.members], [25, 25])  # sampled at the same instant

    def test_delay_rounds_to_the_nearest_clock(self):
        self.group["delay_ns"] = 506                          # 40.48 clocks -> 40
        config, messages = self.check()
        self.assertEqual(self.plan(messages, config).delay_ticks, 40)
        self.group["delay_ns"] = 507                          # 40.56 -> 41
        config, messages = self.check()
        self.assertEqual(self.plan(messages, config).delay_ticks, 41)

    def test_hardware_averaging_warns(self):
        # 4 x (42 + 1 + 28 + 5) = 304 ADC clocks at 48 MHz = 6334 ns + 4 x 5 bus clocks = 500 ns -> 6834 ns
        self.project["modules"] = {"bspAdcAccess": {"averages": 4}}
        config, messages = self.check()
        g = self.plan(messages, config)
        self.assertEqual(g.sequencers[0].conversion_ns, 6834)
        warnings = [m for m in messages if m.severity == "warning" and "averages 4" in m.text]
        self.assertEqual([m.yaml_path for m in warnings], ["sample_groups.MOT_SAMPLE"])
        self.assertIn("spreads over 6834 ns", warnings[0].text)

    def test_unconfirmed(self):
        self.group["verify"] = True
        config, messages = self.check()
        self.assertFalse(self.plan(messages, config).confirmed)

    def test_demo_unchanged(self):
        config, messages = nxconfig.check(DEMO)
        self.assertIsNotNone(config)
        self.assertEqual(config.sample_groups, [])
        self.assertEqual([m for m in messages if "sample group" in m.text], [])


class Rejections(Tree):
    """Every refusal with the YAML path of the value that causes it."""

    def test_pwm_group_not_used(self):
        self.group["pwm"] = "OTHER"
        _, messages = self.check()
        self.assert_error(messages, "sample_groups.MOT_SAMPLE.pwm", "is not used by the project")

    def test_pwm_group_without_trigger(self):
        del self.project["pwm"]["BRIDGE"]["trigger"]
        _, messages = self.check()
        self.assert_error(messages, "sample_groups.MOT_SAMPLE.pwm", "has no trigger output")

    def test_timer_without_router_input(self):
        self.derivative(lambda d: d["trigger_router"]["timer_triggers"].pop("FTM3"))
        _, messages = self.check()
        self.assert_error(messages, "sample_groups.MOT_SAMPLE.pwm", "reach no input of the TRGMUX")

    def test_derivative_without_chain(self):
        self.derivative(lambda d: d.pop("sample_sequencers"))
        _, messages = self.check()
        self.assert_error(messages, "sample_groups", "describes no sequencer")

    def test_unknown_duplicate_and_unfitted_channels(self):
        self.group["channels"] = ["PHASE_I_U", "NOPE"]
        _, messages = self.check()
        self.assert_error(messages, "sample_groups.MOT_SAMPLE.channels[1]", "has no ADC entry NOPE")
        self.group["channels"] = ["PHASE_I_U", "PHASE_I_U"]
        _, messages = self.check()
        self.assert_error(messages, "sample_groups.MOT_SAMPLE.channels[1]", "listed twice")
        self.board["adc"][1]["fitted"] = False
        self.group["channels"] = ["PHASE_I_U", "PHASE_I_V"]
        _, messages = self.check()
        self.assert_error(messages, "sample_groups.MOT_SAMPLE.channels[1]", "not fitted")

    def test_pin_of_a_channel_used_otherwise(self):
        self.board["dio"] = {"outputs": [{"name": "LED", "pin": "PTD3"}]}
        self.project["use"] = {"dio": ["LED"]}
        _, messages = self.check()
        self.assert_error(messages, "sample_groups.MOT_SAMPLE.channels[2]", "pin PTD3 of PHASE_I_W is used by")

    def test_software_conversion_on_an_exclusive_adc(self):
        self.project["use"] = {"adc": ["AUX_SENSE"]}
        _, messages = self.check()
        self.assert_error(messages, "sample_groups.MOT_SAMPLE.channels[1]",
                          "ADC1 is exclusive to the hardware chain of the group, but the project converts on it in "
                          "software: adc AUX_SENSE (use.adc)")
        # use.adc: all selects the members themselves: every ADC of the group is reported
        self.project["use"] = {"adc": "all"}
        _, messages = self.check()
        self.assert_error(messages, "sample_groups.MOT_SAMPLE.channels[0]", "ADC0 is exclusive")

    def test_internal_channel_on_an_exclusive_adc(self):
        self.board["board"]["adc_reference_mv"] = 5000
        self.project["adc_internal"] = [{"name": "BANDGAP", "instance": "ADC0", "channel": "BANDGAP"}]
        _, messages = self.check()
        self.assert_error(messages, "sample_groups.MOT_SAMPLE.channels[0]", "adc_internal BANDGAP")

    def test_too_many_channels_for_the_sequencer(self):
        self.derivative(lambda d: d["sample_sequencers"][1].update(slots=1))
        _, messages = self.check()
        self.assert_error(messages, "sample_groups.MOT_SAMPLE.channels[2]", "converts at most 1 per trigger")

    def test_one_adc_per_group(self):
        self.project["sample_groups"]["SECOND"] = {"pwm": "BRIDGE", "channels": ["AUX_SENSE"]}
        self.project["interrupts"]["ranks"].append({"source": "sample_group", "name": "SECOND", "rank": 1})
        _, messages = self.check()
        self.assert_error(messages, "sample_groups.SECOND.channels[0]",
                          "ADC1 (sequencer PDB1) already belongs to sample group MOT_SAMPLE")

    def test_router_register_locked_by_a_hardware_threshold(self):
        # synthetic derivative: the input of PDB1 in the register of the FTM3 fault inputs (TRGMUX_FTM3 SEL0), whose
        # route the hardware threshold of SYS-86 locks with LK
        def move(d):
            for t in d["trigger_router"]["targets"]:
                if t["name"] == "PDB1_TRIGGER_IN0":
                    t.update(register_index=13, selector=0)
        self.derivative(move)
        self.board["rails"] = [{"name": "SUPPLY_3V3", "mv": 3300, "source": "synthetic test rail IO-62"}]
        self.board["comparators"] = {"supply": {"rail": "SUPPLY_3V3"},
                                     "inputs": [{"name": "I_CMP", "instance": "CMP0", "input": 3, "pin": "PTE8",
                                                 "signal": "PHASE_I_U"}]}
        self.project["hw_thresholds"] = {"OVERCURRENT": {"input": "I_CMP", "trip": "above", "threshold": 20000,
                                                         "reference": "supply", "pwm": "BRIDGE"}}
        config, messages = self.check()
        self.assert_error(messages, "sample_groups.MOT_SAMPLE.channels[1]",
                          "register 13 of output PDB1_TRIGGER_IN0 (start of PDB1) is locked until reset by hardware "
                          "threshold OVERCURRENT")

    def test_router_output_taken_by_a_hardware_threshold(self):
        def share(d):
            for s in d["sample_sequencers"]:
                if s["name"] == "PDB1":
                    s["trigger_target"] = "FTM3_FAULT0"
            d["trigger_router"]["register_lock"] = False
        self.derivative(share)
        self.board["comparators"] = {"supply": {"rail": "SUPPLY_3V3"},
                                     "inputs": [{"name": "I_CMP", "instance": "CMP0", "input": 3, "pin": "PTE8",
                                                 "signal": "PHASE_I_U"}]}
        self.project["hw_thresholds"] = {"OVERCURRENT": {"input": "I_CMP", "trip": "above", "threshold": 20000,
                                                         "reference": "supply", "pwm": "BRIDGE"}}
        _, messages = self.check()
        self.assert_error(messages, "sample_groups.MOT_SAMPLE.channels[1]",
                          "output FTM3_FAULT0 that starts PDB1 is used by hardware threshold OVERCURRENT")

    def test_delay_beyond_the_counter(self):
        self.derivative(lambda d: [s.update(counter_bits=8) for s in d["sample_sequencers"]])
        self.group["delay_ns"] = 4000                         # 320 clocks > 255
        _, messages = self.check()
        self.assert_error(messages, "sample_groups.MOT_SAMPLE.delay_ns", "its counter holds at most 255")

    def test_group_longer_than_the_period(self):
        self.project["pwm"]["BRIDGE"]["frequency_hz"] = 300_000   # 3333 ns < 3968 ns
        self.project["pwm"]["BRIDGE"]["deadtime_ns"] = 100
        _, messages = self.check()
        self.assert_error(messages, "sample_groups.MOT_SAMPLE.channels", "not inside one period")

    def test_rank_missing_and_rank_of_an_unknown_group(self):
        self.project["interrupts"]["ranks"] = []
        _, messages = self.check()
        self.assert_error(messages, "sample_groups.MOT_SAMPLE", "has no interrupt rank")
        self.project["interrupts"]["ranks"] = [{"source": "sample_group", "name": "MOT_SAMPLE", "rank": 0},
                                               {"source": "sample_group", "name": "GHOST", "rank": 1}]
        _, messages = self.check()
        self.assert_error(messages, "interrupts.ranks[1].name", "the project has no sample group GHOST")


if __name__ == "__main__":
    unittest.main()
