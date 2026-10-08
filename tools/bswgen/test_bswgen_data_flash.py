"""NVM-12: generated nvm/nvmDataFlash.h - the areas of the data flash (project.yaml nvm.data_flash).

Profiles: the demo project of the eval board without nvm section (no areas), a copy with the decision nvm01-eeesize
(32 KB data flash, DEPART 0xB, 2 KB sectors) and two calibration areas plus a fixed data area, and the project
buildtest_s32k148_min with another split (448 KB data flash, DEPART 0x4, 4 KB sectors). The header is compiled as C
and as C++ - the C++ probe builds the table of dataflash::AreaService (libs/bsw/nvmDataFlash) from NX_DFLASH_AREAS and
checks it at compile time.

Run: .venv/Scripts/python -m unittest tools/bswgen/test_bswgen_data_flash.py
"""

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import bswgen  # noqa: E402
from test_bswgen_nvm import DECISION, K148, demo_copy  # noqa: E402

CONTRACT = REPO / "libs" / "bsw" / "nvmDataFlash" / "include"
AREAS = {"areas": {"CalMain": {"size": 0x2000, "purpose": "calibration"},
                   "CalBackup": {"size": 0x2000, "purpose": "calibration"},
                   "Trip": {"size": 0x800, "purpose": "data", "offset": 0x7800}}}

CPP_PROBE = """#include "nvmDataFlash.h"
#include <dataflash/DataFlashAreas.h>

#define NX_ROW(name, offset, size, purpose) {#name, offset, size, static_cast<::dataflash::Purpose>(purpose)},
static constexpr ::dataflash::Area AREAS[] = {NX_DFLASH_AREAS(NX_ROW)};
static_assert(sizeof(AREAS) / sizeof(AREAS[0]) == NX_DFLASH_AREA_COUNT, "count");
EXTRA
int main() { return (AREAS[0].size == 0U) ? 1 : 0; }
"""


def defines(text: str) -> dict[str, str]:
    return dict(re.findall(r"^#define (NX_DFLASH\w*) (.*)$", text, re.M))


class DataFlashHeaderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def generate(self, project: Path) -> str:
        out = self.tmp / "out"
        bswgen.generate(project, out)
        return (out / "nvm" / "nvmDataFlash.h").read_text(encoding="utf-8")

    def compiles(self, text: str, extra: str = "", with_areas: bool = True):
        (self.tmp / "nvmDataFlash.h").write_text(text, encoding="utf-8")
        c = self.tmp / "probe.c"
        c.write_text('#include "nvmDataFlash.h"\n#define NX_ONE(n, o, s, p) + 1\n'
                     "int main(void) { return (int)(0 NX_DFLASH_AREAS(NX_ONE)) - (int)NX_DFLASH_AREA_COUNT; }\n",
                     encoding="utf-8")
        probes = [("cc", "-std=c11", c)]
        if with_areas:
            cpp = self.tmp / "probe.cpp"
            cpp.write_text(CPP_PROBE.replace("EXTRA", extra), encoding="utf-8")
            probes.append(("c++", "-std=c++17", cpp))
        for tool, std, src in probes:
            exe = self.tmp / f"{src.stem}_{tool}.exe"
            r = subprocess.run([sys.executable, "-m", "ziglang", tool, std, "-Wall", "-Wextra", "-Werror",
                                f"-I{self.tmp}", f"-I{CONTRACT}", str(src), "-o", str(exe)],
                               capture_output=True, text=True, timeout=600)
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertEqual(subprocess.run([str(exe)], timeout=60).returncode, 0, src.name)

    def test_demo_without_data_flash(self):
        text = self.generate(demo_copy(self.tmp))
        self.assertEqual(defines(text), {"NX_DFLASH_AREA_COUNT": "0U"})
        self.assertIn("No data flash in the project", text)
        self.assertNotIn("NX_DFLASH_START", text)
        self.compiles(text, with_areas=False)

    def test_decision_nvm01_eeesize_with_areas(self):
        text = self.generate(demo_copy(self.tmp, {**DECISION, "data_flash": AREAS}))
        d = defines(text)
        self.assertEqual(d["NX_DFLASH_START"], "0x10000000U")
        self.assertEqual(d["NX_DFLASH_SIZE"], "0x8000U")
        self.assertEqual(d["NX_DFLASH_SECTOR_SIZE"], "0x800U")
        self.assertEqual(d["NX_DFLASH_PARTITION_CODE"], "0xBU")
        self.assertEqual(d["NX_DFLASH_AREA_COUNT"], "3U")
        self.assertEqual((d["NX_DFLASH_AREA_CALBACKUP_OFFSET"], d["NX_DFLASH_AREA_CALMAIN_OFFSET"],
                          d["NX_DFLASH_AREA_TRIP_OFFSET"]), ("0x0U", "0x2000U", "0x7800U"))
        self.assertEqual(d["NX_DFLASH_AREA_TRIP_ADDRESS"], "0x10007800U")
        self.assertEqual(d["NX_DFLASH_AREA_CALMAIN_INDEX"], "1U")
        self.assertNotIn("NX_DFLASH_RESERVED_BYTES", d)
        self.assertIn("X(Trip, 0x7800U, 0x800U, 1)", text)
        self.assertIn("X(CalMain, 0x2000U, 0x2000U, 0)", text)
        self.assertIn("18432 bytes in areas, 14336 bytes free", text)
        self.compiles(text, extra='static_assert(AREAS[NX_DFLASH_AREA_TRIP_INDEX].purpose == ::dataflash::Purpose::DATA, '
                                  '"purpose");\nstatic_assert(AREAS[2].offset == NX_DFLASH_AREA_TRIP_OFFSET, "offset");')

    def test_other_derivative_other_split(self):
        project = demo_copy(self.tmp, {"partition": {"eeprom_bytes": 4096, "data_flash_bytes": 0x70000},
                                       "data_flash": {"areas": {"Maps": {"size": 0x10000, "purpose": "calibration"}}}},
                            base=K148)
        d = defines(self.generate(project))
        self.assertEqual((d["NX_DFLASH_SIZE"], d["NX_DFLASH_SECTOR_SIZE"], d["NX_DFLASH_PARTITION_CODE"]),
                         ("0x70000U", "0x1000U", "0x4U"))
        self.assertEqual(d["NX_DFLASH_AREA_MAPS_SIZE"], "0x10000U")

    def test_areas_that_do_not_fit_generate_nothing(self):
        too_big = {"areas": {"CalMain": {"size": 0x9000, "purpose": "calibration"}}}
        with self.assertRaises(bswgen.ConfigError) as e:
            bswgen.generate(demo_copy(self.tmp, {**DECISION, "data_flash": too_big}), self.tmp / "out")
        self.assertIn("nvm.data_flash.areas", str(e.exception))
        self.assertIn("4096 bytes too many", str(e.exception))


if __name__ == "__main__":
    unittest.main()
