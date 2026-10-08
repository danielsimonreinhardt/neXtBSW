"""NVM-16: tools/rtegen/rtegen.py DEM_BSW_REPORTERS knows the block-wise NvM event names now (TOOL-178 follow-up:
"DEM_BSW_REPORTERS kennt die nvm.<ereignis>.<Block>-Namen noch nicht"). Uses the test/dem fixture of NVM-03
(test_rtegen_dem.py) read-only - nothing here changes it, so its own tests keep their exact counts and messages.

Run: .venv/Scripts/python -m unittest tools/rtegen/test_rtegen_dem_nvm_reporters.py
"""

import contextlib
import copy
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))

import rtegen  # noqa: E402
from test_rtegen_dem import DIAG, ECU, MULTI_TASKS, PROJECT, description  # noqa: E402


class DemBswReporterTest(unittest.TestCase):
    """The pure lookup (no generation needed)."""

    def test_known_literal_names_unchanged(self):
        self.assertIsNotNone(rtegen.dem_bsw_reporter("rte.deadline"))
        self.assertIsNotNone(rtegen.dem_bsw_reporter("calibration.segment"))
        self.assertIsNotNone(rtegen.dem_bsw_reporter("calibration.plausibility"))

    def test_nvm_block_events_are_recognised_for_any_block_name(self):
        for event in ("nvm.default_loaded.Trim", "nvm.default_loaded.BootCount",
                     "nvm.copy_repair_failed.Counter", "nvm.copy_repaired.Counter"):
            self.assertIsNotNone(rtegen.dem_bsw_reporter(event), event)

    def test_a_pattern_matches_only_its_own_prefix(self):
        self.assertIsNone(rtegen.dem_bsw_reporter("nvm.default_loaded"))    # no block: not the pattern
        self.assertIsNone(rtegen.dem_bsw_reporter("nvm.default_loadedX.Trim"))
        self.assertIsNotNone(rtegen.dem_bsw_reporter("can.busoff.CAN1"))    # COM-46: reported by the CAN system

    def test_unknown_bsw_source_still_warns(self):
        # IO-59/NVM-08: the supply is sampled by the RTE, not reported - no reporter; an unknown name still has none
        self.assertIsNone(rtegen.dem_bsw_reporter("bsw.power.undervoltage"))
        self.assertIsNone(rtegen.dem_bsw_reporter("nothing.reports.this"))


class GenerationTest(unittest.TestCase):
    """The fixture of NVM-03 (test/dem), one DTC's event renamed in memory only - the checked-in files are
    untouched, so RteDemGenTest/RteDemHostTest keep exactly the counts and messages they assert."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_with(self, diag):
        path = self.tmp / "rte.yaml"
        path.write_text(yaml.safe_dump(ECU, sort_keys=False), encoding="utf-8")
        desc = description(diag, self.tmp)
        with contextlib.redirect_stderr(io.StringIO()) as err:
            rtegen.generate(path, PROJECT, self.tmp / "out", REPO, MULTI_TASKS, diag=desc)
        return err.getvalue()

    def test_nvm_default_loaded_event_generates_without_a_warning(self):
        diag = copy.deepcopy(DIAG)
        diag["dtcs"][2]["event"] = {"bsw": "nvm.default_loaded.Trim"}    # was can.busoff.CAN1 (BusOff, index 2)
        err = self.run_with(diag)
        self.assertNotIn("is not reported by any BSW module", err)

    def test_can_busoff_is_reported_now(self):
        # COM-46/NVM-08 (was: still warned until the CAN system reported it)
        err = self.run_with(DIAG)
        self.assertNotIn("can.busoff.CAN1 is not reported", err)

    def test_unknown_event_still_warns(self):
        diag = copy.deepcopy(DIAG)
        diag["dtcs"][2]["event"] = {"bsw": "nothing.reports.this"}
        err = self.run_with(diag)
        self.assertIn("nothing.reports.this is not reported by any BSW module of neXtBSW yet", err)


if __name__ == "__main__":
    unittest.main()
