"""COM-47: tools/rtegen sets NX_DEM_RTE_DEADLINE in dem/demBswReporters.h for a DTC of the BSW event rte.deadline, so
RteSystem reports it as a level that follows the deadlines (ecu/application/src/systems/dem/DeadlineReport.h, SIL test
ecu/application/src/systems/test/test_rte_deadline_dem.py). A project without such a DTC keeps its header byte for byte.

Own test profile test/dem of NVM-03 (project buildtest_s32k148_min without SBC, its DTC Deadline), not the eval board.

Run: .venv/Scripts/python -m unittest tools/rtegen/test_rtegen_dem_deadline.py
"""

import copy
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import rtegen  # noqa: E402
from test_rtegen_dem import DIAG  # noqa: E402
from test_rtegen_dem_bsw_sources import REPORTERS, generate  # noqa: E402

MACRO = "#define NX_DEM_RTE_DEADLINE 1"


class DeadlineReporterGenTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def header(self, data: dict, out: str) -> str:
        generate(self.tmp, data, out=out)
        return (self.tmp / out / REPORTERS).read_text(encoding="utf-8")

    def test_deadline_dtc_sets_the_macro(self):
        self.assertTrue(any(d["event"].get("bsw") == "rte.deadline" for d in DIAG["dtcs"]))
        header = self.header(DIAG, "with")
        self.assertEqual(header.count(MACRO), 1)
        self.assertIn("dem/DeadlineReport.h", header)

    def test_without_it_the_header_stays_the_same(self):
        with_dtc = self.header(DIAG, "with")
        data = copy.deepcopy(DIAG)
        data["dtcs"] = [d for d in data["dtcs"] if d["event"].get("bsw") != "rte.deadline"]
        without = self.header(data, "without")
        self.assertNotIn("NX_DEM_RTE_DEADLINE", without)
        self.assertNotIn("COM-47", without)
        # the only difference is the block of COM-47 at the end: the head of the file is the one of before
        block = ("\n// COM-47: RteSystem reports rte.deadline as a level that follows the deadlines "
                 "(dem/DeadlineReport.h)\n" + MACRO + "\n")
        self.assertEqual(with_dtc.replace(block, ""), without)

    def test_reported_by_a_module_of_nextbsw(self):
        self.assertIsNotNone(rtegen.dem_bsw_reporter(rtegen.DEM_RTE_DEADLINE))


if __name__ == "__main__":
    unittest.main()
