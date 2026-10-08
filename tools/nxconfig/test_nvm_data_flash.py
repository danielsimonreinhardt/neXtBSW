"""NVM-12: areas of the data flash (project.yaml nvm.data_flash) - checked against the partition and laid out (nvm.py).

Own test profile: the synthetic derivative of test_nvm (a made-up FlexNVM of 128 KB with 2 KB sectors and code
tables other than any S32K1) under the synthetic board/project of test_nxconfig - with a partition, without
eeprom_emulation (the whole region is data flash) and without data flash; plus the real S32K144 codes through a copy
of the demo project (decision nvm01-eeesize: 32 KB data flash, DEPART 1011b). layout_areas is tested on its own.

Run: .venv/Scripts/python -m unittest tools/nxconfig/test_nvm_data_flash.py
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
sys.path.insert(0, str(HERE))

import nxconfig  # noqa: E402
from nxconfig import nvm  # noqa: E402
import test_nvm  # noqa: E402
from test_nvm import DEMO, EMULATION, Profile  # noqa: E402

setUpModule, tearDownModule = test_nvm.setUpModule, test_nvm.tearDownModule   # SYS-95: pwmgate off, see test_nvm.py

PARTITION_32K = {"eeprom_bytes": 8192, "data_flash_bytes": 0x8000}   # synthetic code 0x9: 32 KB data flash
CAL = {"size": 0x2000, "purpose": "calibration"}


class DataFlashTest(Profile):
    def plan(self, nvm_use, **kw):
        config, messages = self.check(nvm_use, **kw)
        errors = [f"{m.yaml_path}: {m.text}" for m in messages if m.severity == "error"]
        return config, errors

    def test_areas_laid_out_in_the_partition(self):
        config, errors = self.plan({"partition": PARTITION_32K, "data_flash": {"areas": {
            "CalMain": CAL, "CalBackup": CAL, "Trip": {"size": 0x800, "purpose": "data", "offset": 0x7800}}}})
        self.assertEqual(errors, [])
        df = config.nvm.data_flash
        self.assertEqual((df.region, df.start, df.usable_bytes, df.sector_size, df.partition_code),
                         ("DFLASH", 0x10000000, 0x8000, 0x800, 0x9))
        self.assertEqual([(a.name, a.offset, a.size, a.purpose) for a in df.areas],
                         [("CalBackup", 0x0, 0x2000, "calibration"), ("CalMain", 0x2000, 0x2000, "calibration"),
                          ("Trip", 0x7800, 0x800, "data")])
        self.assertEqual((df.used_bytes, df.free_bytes), (0x4800, 0x3800))

    def test_areas_that_do_not_fit_the_partition(self):
        """The case of the finding 2026-09-23: 32 KB chosen - the areas must fit into them, with the numbers."""
        _, errors = self.plan({"partition": PARTITION_32K, "data_flash": {"areas": {
            "CalMain": {"size": 0x6000, "purpose": "calibration"}, "Log": {"size": 0x3000, "purpose": "data"}}}})
        self.assertEqual(errors, ["nvm.data_flash.areas: the areas need 36864 bytes, they do not fit "
                                  "into the 32768 bytes of data flash of nvm.partition (partition code 0x9): 4096 "
                                  "bytes too many; make areas smaller or choose a split with more data flash"])

    def test_whole_sectors(self):
        _, errors = self.plan({"partition": PARTITION_32K, "data_flash": {"areas": {
            "CalMain": {"size": 0x900, "purpose": "calibration"},
            "Trip": {"size": 0x800, "purpose": "data", "offset": 0x400}}}})
        self.assertEqual(len(errors), 2)
        self.assertTrue(errors[0].startswith("nvm.data_flash.areas.CalMain.size: 2304 bytes are no "
                                             "multiple of the sector size 2048 of DFLASH"), errors[0])
        self.assertTrue(errors[1].startswith("nvm.data_flash.areas.Trip.offset: offset 0x400 is not on "
                                             "a sector"), errors[1])

    def test_fixed_areas_overlap_or_leave_no_room(self):
        _, errors = self.plan({"partition": PARTITION_32K, "data_flash": {"areas": {
            "A": {"size": 0x1000, "purpose": "data", "offset": 0x1000},
            "B": {"size": 0x1000, "purpose": "data", "offset": 0x1800}}}})
        self.assertEqual(errors, ["nvm.data_flash.areas.B.offset: 0x1800..0x27FF overlaps A "
                                  "(0x1000..0x1FFF)"])
        _, errors = self.plan({"partition": PARTITION_32K, "data_flash": {"areas": {
            "A": {"size": 0x800, "purpose": "data", "offset": 0x7800}, "B": {"size": 0x800, "purpose": "data",
                                                                            "offset": 0x8000}}}})
        self.assertIn("nvm.data_flash.areas.B.offset: 0x8000..0x87FF lies outside the 32768 bytes of "
                      "data flash", errors)
        # fits in sum, but the fixed area in the middle leaves two gaps too small for the free one
        _, errors = self.plan({"partition": PARTITION_32K, "data_flash": {"areas": {
            "Fixed": {"size": 0x800, "purpose": "data", "offset": 0x4000}, "Big": {"size": 0x5000, "purpose": "data"}}}})
        self.assertEqual(errors, ["nvm.data_flash.areas: no room for Big (20480 bytes) in the 32768 bytes "
                                  "of data flash (the fixed areas leave gaps too small)"])

    def test_partition_needed_where_the_derivative_has_one(self):
        _, errors = self.plan({"data_flash": {"areas": {"CalMain": CAL}}})
        self.assertEqual(errors, ["nvm.data_flash: the data flash of TESTMCU64 is what the partition "
                                  "leaves of DFLASH: nvm.partition must say how much (data_flash_bytes)"])

    def test_partition_without_data_flash(self):
        emulation = copy.deepcopy(EMULATION)
        emulation["partitions"].append({"code": 0xA, "data_flash_bytes": 0, "backup_bytes": 0x20000})
        _, errors = self.plan({"partition": {"eeprom_bytes": 8192, "data_flash_bytes": 0},
                               "data_flash": {"areas": {"CalMain": CAL}}}, emulation=emulation)
        self.assertEqual(errors, ["nvm.data_flash: nvm.partition leaves no data flash (data_flash_bytes "
                                  "0, partition code 0xA); choose a split with data flash or remove nvm.data_flash"])

    def test_partition_errors_are_not_repeated(self):
        _, errors = self.plan({"partition": {"eeprom_bytes": 8192, "data_flash_bytes": 0x4000},
                               "data_flash": {"areas": {"CalMain": CAL}}})
        self.assertEqual(len(errors), 1)
        self.assertIn("nvm.partition.data_flash_bytes", errors[0])

    def test_derivative_without_partition_uses_the_whole_region(self):
        config, errors = self.plan({"data_flash": {"areas": {"CalMain": {"size": 0x20000, "purpose": "calibration"}}}},
                                   emulation=None)
        self.assertEqual(errors, [])
        df = config.nvm.data_flash
        self.assertEqual((df.usable_bytes, df.partition_code, df.free_bytes), (0x20000, None, 0))
        _, errors = self.plan({"data_flash": {"areas": {"CalMain": {"size": 0x20800, "purpose": "calibration"}}}},
                              emulation=None)
        self.assertIn("into the 131072 bytes of DFLASH: 2048 bytes too many", errors[0])

    def test_derivative_without_data_flash(self):
        _, errors = self.plan({"data_flash": {"areas": {"CalMain": CAL}}}, emulation=None, flash=0)
        self.assertEqual(errors, ["nvm.data_flash: TESTMCU64 has no data flash (mcudb memory of kind "
                                  "data_flash); remove nvm.data_flash"])

    def test_no_area(self):
        _, errors = self.plan({"partition": PARTITION_32K, "data_flash": {"areas": {}}})
        self.assertEqual(errors, ["nvm.data_flash.areas: name at least one area (name, size, purpose) "
                                  "or remove nvm.data_flash"])

    def test_schema(self):
        for bad in ({"size": 0x800, "purpose": "log"}, {"size": 0, "purpose": "data"}, {"purpose": "data"},
                    {"size": 0x800, "purpose": "data", "address": 0}):
            _, errors = self.plan({"partition": PARTITION_32K, "data_flash": {"areas": {"X": bad}}})
            self.assertTrue(any("nvm.data_flash.areas.X" in e for e in errors), (bad, errors))

    def test_without_data_flash_nothing_changes(self):
        config, errors = self.plan({"partition": PARTITION_32K})
        self.assertEqual(errors, [])
        self.assertIsNone(config.nvm.data_flash)


class LayoutTest(unittest.TestCase):
    def test_first_fit_on_sectors_behind_the_reserved_part(self):
        offsets = nvm.layout_areas([("A", 0x800), ("B", 0x1000), ("C", 0x800), ("F", 0x800)], {"F": 0x1800}, 0x8000,
                                   0x800,
                                   reserved=0x800)
        self.assertEqual(offsets, {"F": 0x1800, "A": 0x800, "B": 0x2000, "C": 0x1000})

    def test_fixed_area_over_the_reserved_part(self):
        with self.assertRaises(ValueError) as e:
            nvm.layout_areas([("F", 0x800)], {"F": 0x0}, 0x8000, 0x800, reserved=0x1000)
        self.assertIn("overlaps the calibration segment", str(e.exception))


class S32K144Test(unittest.TestCase):
    """The real codes: decision nvm01-eeesize (32 KB data flash, DEPART 0xB, 2 KB sectors of FLEXNVM)."""

    def test_demo_partition(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            base = DEMO / "project.yaml"
            project = yaml.safe_load(base.read_text(encoding="utf-8"))
            for key in ("board", "rte", "diag"):
                if key in project["project"]:
                    project["project"][key] = str((base.parent / project["project"][key]).resolve())
            project["nvm"] = {"partition": {"eeprom_bytes": 4096, "data_flash_bytes": 0x8000, "partition_code": 0xB,
                                            "load_on_reset": True, "key_slots": 20},
                              "data_flash": {"areas": {"CalA": {"size": 0x4000, "purpose": "calibration"},
                                                       "CalB": {"size": 0x4000, "purpose": "calibration"}}}}
            path = tmp / "project.yaml"
            path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
            config, messages = nxconfig.check(path)
            self.assertEqual([str(m) for m in messages if m.severity == "error"], [])
            df = config.nvm.data_flash
            self.assertEqual((df.region, df.start, df.usable_bytes, df.sector_size, df.partition_code),
                             ("FLEXNVM", 0x10000000, 0x8000, 0x800, 0xB))
            self.assertEqual([(a.name, a.offset) for a in df.areas], [("CalA", 0), ("CalB", 0x4000)])
            self.assertEqual(df.free_bytes, 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
