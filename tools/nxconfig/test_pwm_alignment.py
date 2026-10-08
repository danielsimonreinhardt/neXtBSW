"""Counting and trigger output of a PWM group (IO-61): project values pwm.<group>.alignment and .trigger in nxconfig.

Own profile: the s32k1xx test profile with 8 MHz crystal and 64 MHz core (group FAN on FTM1, counter clock 64 MHz).
The counter rule is the one pwm_timing() of ARCH-33 that bswgen uses as well; with alignment center the counter
counts up and down, so the counter limits and the duty resolution apply to the turning point (half the period).

Run: .venv/Scripts/python -m unittest tools/nxconfig/test_pwm_alignment.py
"""

import copy
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import nxconfig  # noqa: E402
from nxconfig import checks  # noqa: E402
from nxconfig.model import PwmUse  # noqa: E402

PROFILES = HERE / "test" / "profiles"
BOARD = yaml.safe_load((PROFILES / "boards" / "testboard_s32k_8mhz" / "board.yaml").read_text(encoding="utf-8"))
PROJECT = yaml.safe_load((PROFILES / "projects" / "testproject_s32k_8mhz" / "project.yaml").read_text(encoding="utf-8"))
LIMITS = checks.PWM_COUNTERS["s32k1xx"]


class PwmAlignmentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def messages(self, **values):
        board_dir, project_dir = self.tmp / "boards" / "testboard", self.tmp / "projects" / "testproject"
        board_dir.mkdir(parents=True, exist_ok=True)
        project_dir.mkdir(parents=True, exist_ok=True)
        (board_dir / "board.yaml").write_text(yaml.safe_dump(BOARD, sort_keys=False), encoding="utf-8")
        project = copy.deepcopy(PROJECT)
        project["project"]["board"] = "../../boards/testboard"
        project["pwm"]["FAN"].update(values)
        path = project_dir / "project.yaml"
        path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        _, messages = nxconfig.check(path, nxconfig.MCUDB)
        return [(m.yaml_path, m.text) for m in messages if m.severity == "error"]

    def test_defaults_keep_the_behaviour_before_io61(self):
        use = PwmUse(frequency_hz=20_000, deadtime_ns=1_000)
        self.assertEqual((use.alignment, use.trigger), ("edge", None))
        self.assertEqual(self.messages(), [])

    def test_center_halves_the_counter_range_not_the_frequency(self):
        edge = checks.pwm_timing(LIMITS, "G", "FTM1", 64_000_000, 20_000, 750)
        center = checks.pwm_timing(LIMITS, "G", "FTM1", 64_000_000, 20_000, 750, center=True)
        # 64 MHz / 20 kHz = 3200 ticks; center: turning point 1600, period 2 x 1600
        self.assertEqual((edge.period_ticks, edge.counter_top, edge.center), (3200, 3199, False))
        self.assertEqual((center.period_ticks, center.counter_top, center.center), (3200, 1600, True))
        self.assertEqual(edge.frequency_hz, center.frequency_hz)
        self.assertEqual((edge.deadtime_divider, edge.deadtime_ticks), (center.deadtime_divider, center.deadtime_ticks))
        self.assertEqual((center.deadtime_divider, center.deadtime_ticks), (1, 48))      # 750 ns = 48 clocks
        # odd tick count: 64 MHz / 19 kHz = 3368.4 -> edge 3368, center 2 x 1684 (the turning point is rounded)
        center = checks.pwm_timing(LIMITS, "G", "FTM1", 64_000_000, 19_000, 750, center=True)
        self.assertEqual((center.period_ticks, center.counter_top), (3368, 1684))

    def test_center_needs_a_smaller_prescaler(self):
        # 48 MHz / 200 Hz = 240000 ticks: edge 16 bit only with /4 (60000), center turning point 120000 -> /2 (60000)
        edge = checks.pwm_timing(LIMITS, "G", "FTM0", 48_000_000, 200, 5_000)
        center = checks.pwm_timing(LIMITS, "G", "FTM0", 48_000_000, 200, 5_000, center=True)
        self.assertEqual((edge.prescaler_shift, edge.period_ticks), (2, 60_000))
        self.assertEqual((center.prescaler_shift, center.period_ticks, center.counter_top), (1, 120_000, 60_000))
        self.assertEqual(edge.frequency_hz, center.frequency_hz)

    def test_messages_with_yaml_path(self):
        # 64 MHz / 400 kHz: edge 160 ticks, center turning point 80 < 100
        self.assertEqual(self.messages(frequency_hz=400_000, deadtime_ns=500), [])
        self.assertEqual(self.messages(frequency_hz=400_000, deadtime_ns=500, alignment="center"),
                         [("pwm.FAN.frequency_hz",
                           "PWM group FAN (alignment center): frequency 400000 Hz leaves 80 counter ticks per counting "
                           "direction at the counter clock 64000000 Hz of FTM1, at least 100 needed (duty resolution)")])
        # 5 Hz: edge needs 100000 ticks with /128 (too many), center 50000 per direction (fits)
        self.assertEqual([p for p, _ in self.messages(frequency_hz=5)], ["pwm.FAN.frequency_hz"])
        self.assertEqual(self.messages(frequency_hz=5, alignment="center"), [])
        self.assertEqual(self.messages(frequency_hz=3, alignment="center"),
                         [("pwm.FAN.frequency_hz",
                           "PWM group FAN (alignment center): frequency 3 Hz is too low for the counter clock 64000000 "
                           "Hz of FTM1 (largest prescaler 128, at most 65535 ticks per counting direction)")])

    def test_schema_values(self):
        self.assertEqual(self.messages(alignment="center", trigger={"point": "zero_low"}), [])
        self.assertEqual(self.messages(trigger={"point": "zero_high"}), [])
        for values, path in (({"alignment": "centre"}, "pwm.FAN.alignment"),
                             ({"trigger": {"point": "middle"}}, "pwm.FAN.trigger.point"),
                             ({"trigger": {"point": "zero_low", "delay_ns": 10}}, "pwm.FAN.trigger")):
            with self.subTest(values=values):
                paths = [p for p, _ in self.messages(**values)]
                self.assertEqual(len(paths), 1, paths)
                self.assertTrue(paths[0].startswith(path), paths)


if __name__ == "__main__":
    unittest.main()
