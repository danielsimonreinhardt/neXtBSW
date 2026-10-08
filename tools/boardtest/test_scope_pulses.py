# SPDX-License-Identifier: Apache-2.0
"""Host test of tools/boardtest/scope_pulses.py (SYS-147) against synthetic captures in the LabControl CSV format.

Run: .venv/Scripts/python tools/boardtest/test_scope_pulses.py
"""

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scope_pulses as sp  # noqa: E402

HEADER = ("# scope=picoscope:default model=PicoScope 2204A timestamp=2026-09-30T22:09:35.000\n"
          "# sample_interval_s=2e-06 trigger_index=250 triggered=True\n"
          "# A: range_v=10 coupling=DC probe=1 overrange=False B: range_v=10 coupling=DC probe=1 overrange=False\n")


def capture(levels_b, step=2e-6, t0=-0.0005, levels_a=None, columns="time_s,A_V,B_V"):
    """CSV text with channel B following `levels_b` (list of (duration_s, volts)) sampled every `step`."""
    rows = []
    t = t0
    for duration, volts in levels_b:
        n = int(round(duration / step))
        for _ in range(n):
            a = 0.0 if levels_a is None else levels_a
            rows.append(f"{t:.9g},{a},{volts}")
            t += step
    return HEADER + columns + "\n" + "\n".join(rows) + "\n"


class ScopePulsesTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="nx_scope_pulses_"))

    def write(self, text, name="c.csv"):
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path

    def run_main(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = sp.main([str(a) for a in argv])
        return code, out.getvalue()

    def test_pin_case_a_pulse_of_0_2_ms(self):
        # the board finding: about 0.2 ms at 5 V, low before and after
        path = self.write(capture([(0.0005, 0.02), (0.0002, 5.0), (0.0013, 0.03)]))
        times, volts = sp.read_channel(path, "B")
        pulses = sp.find_pulses(times, volts, 2.5)
        self.assertEqual(len(pulses), 1)
        self.assertTrue(pulses[0].closed)
        self.assertAlmostEqual(pulses[0].width_s, 0.0002, delta=4e-6)
        self.assertAlmostEqual(pulses[0].start_s, 0.0, delta=4e-6)
        code, out = self.run_main(path, "--channel", "B", "--expect", "one", "--max-width-ms", "1.1")
        self.assertEqual(code, 0, out)
        self.assertIn("passed", out)
        # a take-back slower than the hold of S1 (1 ms poll) is not passed
        code, out = self.run_main(path, "--channel", "B", "--expect", "one", "--max-width-ms", "0.1")
        self.assertEqual(code, 1, out)
        self.assertIn("above 0.100 ms", out)

    def test_reset_without_intervention_must_stay_low(self):
        quiet = self.write(capture([(0.002, 0.04)]), "quiet.csv")
        self.assertEqual(self.run_main(quiet, "--channel", "B", "--expect", "none")[0], 0)
        glitch = self.write(capture([(0.001, 0.0), (0.00001, 4.9), (0.001, 0.0)]), "glitch.csv")
        code, out = self.run_main(glitch, "--channel", "B", "--expect", "none")
        self.assertEqual(code, 1, out)
        self.assertIn("expected no active level", out)

    def test_open_levels_at_the_edges(self):
        # IO-45 step 3: DRV_EN switched on and stays on until the end of the capture
        rising = self.write(capture([(0.0004, 0.0), (0.0016, 5.0)]), "rising.csv")
        times, volts = sp.read_channel(rising, "B")
        pulses = sp.find_pulses(times, volts, 2.5)
        self.assertEqual(len(pulses), 1)
        self.assertIsNotNone(pulses[0].start_s)
        self.assertIsNone(pulses[0].end_s)
        self.assertIsNone(pulses[0].width_s)
        code, out = self.run_main(rising, "--channel", "B", "--expect", "one")
        self.assertEqual(code, 1, out)
        self.assertIn("open", out)
        # already high at the start
        high = self.write(capture([(0.001, 5.0), (0.001, 0.0)]), "high.csv")
        pulses = sp.find_pulses(*sp.read_channel(high, "B"), 2.5)
        self.assertEqual(len(pulses), 1)
        self.assertIsNone(pulses[0].start_s)
        self.assertIsNotNone(pulses[0].end_s)

    def test_active_low_counts_chip_select_bursts(self):
        # O5 (IO-30): chip select idles high, low for about 80 us per 10 ms cycle
        levels = []
        for _ in range(3):
            levels += [(0.0099, 5.0), (0.00008, 0.05)]
        levels.append((0.001, 5.0))
        path = self.write(capture(levels, step=1e-6, t0=0.0))
        pulses = sp.find_pulses(*sp.read_channel(path, "B"), 2.5, low=True)
        self.assertEqual(len(pulses), 3)
        for p in pulses:
            self.assertAlmostEqual(p.width_s, 80e-6, delta=2e-6)
        self.assertEqual(sp.find_pulses(*sp.read_channel(path, "B"), 2.5), [
            sp.Pulse(None, pulses[0].start_s), sp.Pulse(pulses[0].end_s, pulses[1].start_s),
            sp.Pulse(pulses[1].end_s, pulses[2].start_s), sp.Pulse(pulses[2].end_s, None)])

    def test_several_pulses_and_limits(self):
        path = self.write(capture([(0.0002, 0.0), (0.00005, 5.0), (0.0002, 0.0), (0.0003, 5.0), (0.0002, 0.0)]))
        pulses = sp.find_pulses(*sp.read_channel(path, "B"), 2.5)
        self.assertEqual([round(p.width_s * 1e6) for p in pulses], [50, 300])
        self.assertEqual(sp.judge(pulses, "any", 0.0001, None),
                         [f"pulse at {pulses[0].start_s * 1e3:.3f} ms is 0.050 ms wide, below 0.100 ms"])
        self.assertEqual(len(sp.judge(pulses, "one", None, None)), 1)
        self.assertEqual(sp.judge(pulses, "any", 0.00001, 0.001), [])

    def test_threshold_matters(self):
        # a 10:1 probe left on 1:1 in the software shows 0.5 V for a 5 V level (board 2026-09-30, IO-45)
        path = self.write(capture([(0.0005, 0.0), (0.0002, 0.5), (0.0005, 0.0)]))
        self.assertEqual(sp.find_pulses(*sp.read_channel(path, "B"), 2.5), [])
        self.assertEqual(len(sp.find_pulses(*sp.read_channel(path, "B"), 0.25)), 1)

    def test_format_errors(self):
        self.assertEqual(self.run_main(self.dir / "missing.csv")[0], 2)
        bad = self.write("# only comments\n", "bad.csv")
        self.assertEqual(self.run_main(bad)[0], 2)
        header = self.write(HEADER + "t,A_V\n0,1\n", "header.csv")
        with self.assertRaises(sp.CaptureError):
            sp.read_channel(header, "A")
        only_b = self.write(capture([(0.0001, 0.0)], columns="time_s,B_V").replace(",0.0,", ",", ), "b.csv")
        with self.assertRaises(sp.CaptureError):
            sp.read_channel(only_b, "A")
        broken = self.write(HEADER + "time_s,A_V\n0,x\n", "broken.csv")
        code, out = self.run_main(broken)
        self.assertEqual(code, 2)
        self.assertIn("not numeric", out)


if __name__ == "__main__":
    unittest.main()
