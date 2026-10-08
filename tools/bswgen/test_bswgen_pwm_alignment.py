"""PWM group counting and trigger output in bswgen (IO-61): register values of ftmPwmConfiguration.h and of the SafeIo
register list for pwm.<group>.alignment and pwm.<group>.trigger.

Own profile tools/bswgen/test/profiles/projects/testproject_pwm_center (three pairs on FTM1 channels 2..7, 64 MHz,
20 kHz, 750 ns); variants are written as copies. The waveform itself (duty, dead time, trigger instant on a register
model) is checked by platforms/s32k1xx/bsp/bspFtmPwmGroup/test/test_ftm_pwm_group.py.

Run: .venv/Scripts/python -m unittest tools/bswgen/test_bswgen_pwm_alignment.py
"""

import copy
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import bswgen  # noqa: E402

PROFILE = HERE / "test" / "profiles" / "projects" / "testproject_pwm_center" / "project.yaml"
BOARD = HERE / "test" / "profiles" / "boards" / "testboard_pwm_center" / "board.yaml"
CONFIG = Path("bsp") / "io" / "outputPwm" / "ftmPwmConfiguration.h"


class PwmAlignmentGeneratorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_with(self, use_changes=None, board=None):
        project = yaml.safe_load(PROFILE.read_text(encoding="utf-8"))
        use = project["pwm"]["INVERTER"]
        for key, value in (use_changes or {}).items():
            if value is None:
                use.pop(key, None)
            else:
                use[key] = value
        board_dir = self.tmp / "boards" / "b"
        board_dir.mkdir(parents=True, exist_ok=True)
        doc = yaml.safe_load(BOARD.read_text(encoding="utf-8")) if board is None else board
        (board_dir / "board.yaml").write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        project["project"]["board"] = "../../boards/b"
        path = self.tmp / "projects" / "p" / "project.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        gen = bswgen.generate(path, self.tmp / "out")
        return gen, (self.tmp / "out" / CONFIG).read_text(encoding="utf-8")

    def test_center_with_channel_trigger(self):
        gen, cfg = self.run_with()
        p = gen.pwm
        self.assertEqual((p["center"], p["period_ticks"], p["counter_top"], p["prescaler_shift"]), (True, 3200, 1600, 0))
        self.assertEqual(p["trigger"]["kind"], "channel")
        self.assertEqual((p["trigger"]["channel"], p["trigger"]["value"]), (0, 1600))   # turning point
        self.assertTrue(p["io61"])
        self.assertIn("#ifndef NX_FTM_PWM_IO61", cfg)
        self.assertIn("/* MOD           */ COUNTER_TOP,", cfg)
        self.assertIn("/* EXTTRIG       */ FTM_EXTTRIG_CH0TRIG_MASK,", cfg)
        self.assertIn("/* COMBINE       */ (COMBINE_PAIR << 8U) | (COMBINE_PAIR << 16U) | (COMBINE_PAIR << 24U),", cfg)
        self.assertNotIn("FTM_COMBINE_COMBINE0_MASK", cfg)
        # SafeIo checks what the driver writes once: CPWMS in SC, the turning point in MOD, no COMBINE bit
        regs = {e["name"]: e["expected"] for e in gen.safe_io if e["cls"] == "PWM_TIMER"}
        self.assertEqual(regs["INVERTER_SC"], 0x00FC0028)
        self.assertEqual(regs["INVERTER_MOD"], 1600)
        self.assertEqual(regs["INVERTER_COMBINE"], 0x32323200)

    def test_trigger_instants(self):
        cases = [  # (alignment, point) -> kind, value
            (("center", "zero_high"), ("init", None)),
            (("edge", "zero_low"), ("init", None)),
            (("edge", "zero_high"), ("channel", 1600)),     # middle of the 3200 tick period
        ]
        for (alignment, point), (kind, value) in cases:
            with self.subTest(alignment=alignment, point=point):
                gen, cfg = self.run_with({"alignment": alignment, "trigger": {"point": point}})
                t = gen.pwm["trigger"]
                self.assertEqual((t["kind"], t.get("value")), (kind, value))
                if kind == "init":
                    self.assertIn("/* EXTTRIG       */ FTM_EXTTRIG_INITTRIGEN_MASK,", cfg)
                    self.assertNotIn("TRIGGER_CHANNEL", cfg)
                # only the edge counting with the init trigger can be driven by the driver before IO-61
                self.assertEqual("NX_FTM_PWM_IO61" in cfg, (alignment, point) != ("edge", "zero_low"))

    def test_edge_without_trigger_is_the_configuration_before_io61(self):
        gen, cfg = self.run_with({"alignment": None, "trigger": None})
        p = gen.pwm
        self.assertEqual((p["center"], p["period_ticks"], p["counter_top"], p["trigger"], p["io61"]),
                         (False, 3200, 3199, None, False))
        self.assertNotIn("IO-61", cfg)
        self.assertIn("/* EXTTRIG       */ 0U,", cfg)
        self.assertIn("/* MOD           */ PERIOD_TICKS - 1U,", cfg)
        regs = {e["name"]: e["expected"] for e in gen.safe_io if e["cls"] == "PWM_TIMER"}
        self.assertEqual((regs["INVERTER_SC"], regs["INVERTER_MOD"], regs["INVERTER_COMBINE"]),
                         (0x00FC0008, 3199, 0x33333300))

    def test_channel_trigger_needs_a_free_channel(self):
        board = yaml.safe_load(BOARD.read_text(encoding="utf-8"))
        grp = board["pwm"][0]
        # a fourth pair on FTM1 channels 0/1 ([MUX] S32K144 LQFP64: FTM1_CH0 PTB2, FTM1_CH1 PTB3): all 8 used
        grp["pairs"].insert(0, {"name": "PHASE_X", "high_side": "PHASE_X_HS", "low_side": "PHASE_X_LS"})
        grp["channels"][:0] = [{"name": "PHASE_X_HS", "channel": 0, "pin": "PTB2"},
                               {"name": "PHASE_X_LS", "channel": 1, "pin": "PTB3"}]
        with self.assertRaises(bswgen.ConfigError) as ctx:
            self.run_with(board=copy.deepcopy(board))
        self.assertIn("PWM INVERTER: trigger point zero_low needs a channel match, but the group uses all 8 channels "
                      "of FTM1 (pwm.INVERTER.trigger)", str(ctx.exception))
        # the counter minimum of the up-down counter needs no channel
        gen, _ = self.run_with({"trigger": {"point": "zero_high"}}, board=copy.deepcopy(board))
        self.assertEqual(gen.pwm["trigger"]["kind"], "init")

    def test_last_line_of_defence_has_the_nxconfig_text(self):
        with self.assertRaises(bswgen.ConfigError) as ctx:
            self.run_with({"frequency_hz": 400_000, "deadtime_ns": 500})
        self.assertIn("(alignment center): frequency 400000 Hz leaves 80 counter ticks per counting direction",
                      str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
