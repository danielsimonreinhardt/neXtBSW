"""SYS-89: calibration segment of a project (memory.calibration) - placement (memory.py) and rules (calibration.py).

Own test profile: the synthetic bootloader project on testboard (derivative TESTMCU64 of family testfam, code flash
0x08000000 with 8-KB sectors, bootloader 16 KB), in a copy of the test mcudb with a made-up data flash and emulated
EEPROM, a second code flash region, and an ECU description with components whose swc.yaml carries safety classes.
The real S32K144 through a copy of the demo project (4-KB sectors, application from 0x8000) and the build test
profile S32K148 (buildtest_s32k148_min) with the RTE-26 calibration component, not only the eval board.

Run: .venv/Scripts/python -m unittest tools/nxconfig/test_calibration.py
"""

import copy
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))

import nxconfig  # noqa: E402
from nxconfig import calibration  # noqa: E402

TEST_MCUDB = HERE / "test" / "mcudb"
TEST_BOARD = HERE / "test" / "profiles" / "boards" / "testboard" / "board.yaml"
BL_PROJECT = yaml.safe_load((HERE / "test/profiles/projects/testproject_bootloader/project.yaml")
                            .read_text(encoding="utf-8"))
# PLAT-62: the own full-featured test profile (synthetic testboard_full/testproject_full) instead of the eval board
# project, which stays private (PLAT-46 F10) - a test against it would be red in the exported open tree. What
# proves something about the eval board itself uses EVAL_DEMO and is skipped where it is missing.
DEMO = REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full"
EVAL_DEMO = REPO / "projects" / "evalboard_s32k144_demo"
S32K148 = REPO / "projects" / "buildtest_s32k148_min"
CALIB_RTE = REPO / "tools" / "rtegen" / "test" / "calib" / "rte.yaml"

EMULATION = {
    "sizes": [{"code": 0x1, "eeprom_bytes": 2048}, {"code": 0xF, "eeprom_bytes": 0}],
    "partitions": [{"code": 0x2, "data_flash_bytes": 0x10000, "backup_bytes": 0},
                   {"code": 0x6, "data_flash_bytes": 0x4000, "backup_bytes": 0xC000}],
    "source": "synthetic",
}
APP_START = 0x08004000          # bootloader 0x4000 behind the flash start
SECTOR = 0x2000


def swc(name: str, parameters: list) -> dict:
    return {"version": 1, "name": name, "implementation": "c", "runnables": [{"name": "Run10ms", "period_ms": 10}],
            "parameters": parameters}


def diag_with_dtcs(dtcs: list) -> dict:
    """A minimal but schema-valid diag.yaml (ARCH-51): only what _check_dtcs (diagdesc.py) needs besides the DTCs
    themselves - one operation cycle and a fault_memory section."""
    return {"schema_version": 1, "operation_cycles": [{"name": "Power"}],
            "fault_memory": {"max_entries": 4, "overflow": "replace_oldest"}, "dtcs": dtcs}


def calibration_dtc(number: int = 0x100010, name: str = "CalibrationSegment", debounce: dict | None = None) -> dict:
    """A DTC whose event is RteSystem's report of the calibration segment (SYS-89: event.bsw calibration.segment).

    Debounce defaults to failed_threshold: 1, passed_threshold: -1 (SYS-102): reportCalibrationToDem() reports
    FAILED or PASSED exactly once at start-up, so the plain schema default (127/-128, meant for a periodically
    resampled event) would never mature.
    """
    if debounce is None:
        debounce = {"counter": {"failed_threshold": 1, "passed_threshold": -1}}
    return {"number": number, "name": name, "operation_cycle": "Power",
            "event": {"bsw": calibration.CALIBRATION_SEGMENT_EVENT}, "debounce": debounce}


def plausibility_dtc(number: int = 0x100030, name: str = "CalibrationPlausibility",
                     debounce: dict | None = None) -> dict:
    """A DTC whose event is PlausibilityMonitor's report of the in-place check (SYS-90: event.bsw
    calibration.plausibility), same maturing default as calibration_dtc (SYS-102)."""
    if debounce is None:
        debounce = {"counter": {"failed_threshold": 1, "passed_threshold": -1}}
    return {"number": number, "name": name, "operation_cycle": "Power",
            "event": {"bsw": calibration.CALIBRATION_PLAUSIBILITY_EVENT}, "debounce": debounce}


def other_dtc(number: int = 0x100020, name: str = "Other") -> dict:
    """A DTC of some other BSW event, to show SYS-101 looks at event.bsw and not just at any DTC being present."""
    return {"number": number, "name": name, "operation_cycle": "Power",
            "event": {"bsw": "rte.deadline"}, "debounce": {"counter": {}}}


class Profile(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.mcudb = self.tmp / "mcudb"
        shutil.copytree(TEST_MCUDB, self.mcudb)
        derivative_file = self.mcudb / "testfam" / "TESTMCU64.yaml"
        derivative = yaml.safe_load(derivative_file.read_text(encoding="utf-8"))
        derivative["memory"] += [
            {"name": "DFLASH", "kind": "data_flash", "start": 0x10000000, "size": 0x10000, "sector_size": 0x800},
            {"name": "EERAM", "kind": "eeprom_ram", "start": 0x14000000, "size": 0x1000},
            {"name": "BOOTROM", "kind": "code_flash", "start": 0x00100000, "size": 0x8000, "sector_size": 0x1000,
             "program_size": 8},
        ]
        derivative["eeprom_emulation"] = EMULATION
        derivative_file.write_text(yaml.safe_dump(derivative, sort_keys=False), encoding="utf-8")
        board_dir = self.tmp / "boards" / "testboard"
        board_dir.mkdir(parents=True)
        shutil.copyfile(TEST_BOARD, board_dir / "board.yaml")
        self.project_dir = self.tmp / "projects" / "testproject"
        self.project_dir.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def check(self, cal, xcp=None, nvm=None, components=None, diag=None):
        project = copy.deepcopy(BL_PROJECT)
        project["project"]["board"] = "../../boards/testboard"
        if cal is not None:
            project["memory"]["calibration"] = cal
        if xcp is not None:
            project["xcp"] = xcp
        if nvm is not None:
            project["nvm"] = nvm
        if components is not None:
            entries = []
            for name, parameters in components.items():
                folder = self.project_dir / "swc" / name
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "swc.yaml").write_text(yaml.safe_dump(swc(name, parameters), sort_keys=False),
                                                 encoding="utf-8")
                entries.append({"name": name, "path": f"swc/{name}"})
            (self.project_dir / "rte.yaml").write_text(
                yaml.safe_dump({"schema_version": 2, "components": entries}, sort_keys=False), encoding="utf-8")
            project["project"]["rte"] = "rte.yaml"
        if diag is not None:
            (self.project_dir / "diag.yaml").write_text(yaml.safe_dump(diag, sort_keys=False), encoding="utf-8")
            project["project"]["diag"] = "diag.yaml"
        path = self.project_dir / "project.yaml"
        path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        return nxconfig.check(path, self.mcudb)

    def errors(self, cal, **kw):
        _, messages = self.check(cal, **kw)
        return [(m.yaml_path, m.text) for m in messages if m.severity == "error"]

    def warnings(self, cal, **kw):
        _, messages = self.check(cal, **kw)
        return [(m.yaml_path, m.text) for m in messages if m.severity == "warning"]


class PlacementTest(Profile):
    def test_without_calibration_nothing_changes(self):
        config, messages = self.check(None)
        self.assertEqual([str(m) for m in messages if m.severity == "error"], [])
        self.assertIsNone(config.memory.calibration)
        self.assertEqual(config.memory.app_start, APP_START)

    def test_code_flash_behind_the_first_application_sector(self):
        config, messages = self.check({"region": "FLASH", "size": 2 * SECTOR})
        self.assertEqual([str(m) for m in messages if m.severity == "error"], [])
        cal = config.memory.calibration
        self.assertEqual((cal.region, cal.start, cal.size, cal.end), ("FLASH", APP_START + SECTOR, 2 * SECTOR,
                                                                      APP_START + 3 * SECTOR))
        self.assertEqual((cal.sector_size, cal.in_application_flash), (SECTOR, True))
        self.assertEqual((cal.on_invalid, cal.production), ("refuse", False))       # the defaults of the schema
        # the bootloader keeps its view of the application area (the segment is part of it until BL-31)
        self.assertEqual((config.memory.app_start, config.memory.app_end), (APP_START, config.memory.key_address))

    def test_profile_and_reaction_are_taken_over(self):
        config, _ = self.check({"region": "FLASH", "size": SECTOR, "on_invalid": "warn", "profile": "production"})
        self.assertEqual((config.memory.calibration.on_invalid, config.memory.calibration.production), ("warn", True))

    def test_whole_sectors(self):
        self.assertEqual(self.errors({"region": "FLASH", "size": 0x1800}),
                         [("memory.calibration.size", "calibration segment 0x1800 is no multiple of the sector size "
                                                      "0x2000 of FLASH (whole sectors, so that it can be erased on "
                                                      "its own)")])

    def test_segment_must_leave_application_code(self):
        errors = self.errors({"region": "FLASH", "size": 0x36000})
        self.assertEqual(errors[0][0], "memory.calibration.size")
        self.assertIn("leaves no application code in FLASH", errors[0][1])

    def test_unknown_region_names_the_candidates(self):
        errors = self.errors({"region": "RAM", "size": SECTOR})
        self.assertEqual(errors, [("memory.calibration.region", "TESTMCU64 has no flash region RAM for the "
                                                                "calibration segment (possible: FLASH, DFLASH)")])

    def test_other_code_flash_region_is_refused(self):
        errors = self.errors({"region": "BOOTROM", "size": 0x1000})
        self.assertEqual(errors[0][0], "memory.calibration.region")
        self.assertIn("must be the one of the application (memory.flash: FLASH)", errors[0][1])

    def test_unknown_values_are_schema_errors(self):
        for key, value in (("on_invalid", "ignore"), ("profile", "release"), ("size", 0)):
            with self.subTest(key=key):
                errors = self.errors({"region": "FLASH", "size": SECTOR, key: value})
                self.assertTrue(any(path.startswith(f"memory.calibration.{key}") for path, _ in errors), errors)


class DataFlashTest(Profile):
    def test_needs_the_partition(self):
        errors = self.errors({"region": "DFLASH", "size": 0x800})
        self.assertEqual(errors[0][0], "memory.calibration.region")
        self.assertIn("nvm.partition must say how much of it stays data flash", errors[0][1])

    def test_must_fit_into_the_data_flash_of_the_partition(self):
        errors = self.errors({"region": "DFLASH", "size": 0x8000},
                             nvm={"partition": {"eeprom_bytes": 2048, "data_flash_bytes": 0x4000}})
        self.assertEqual(errors[0], ("memory.calibration.size", "calibration segment 0x8000 does not fit into the "
                                                                "0x4000 bytes of data flash of nvm.partition"))

    def test_refused_until_the_bootloader_writes_it(self):
        errors = self.errors({"region": "DFLASH", "size": 0x800},
                             nvm={"partition": {"eeprom_bytes": 2048, "data_flash_bytes": 0x4000}})
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0][0], "memory.calibration.region")
        self.assertIn("(BL-31, NVM-12)", errors[0][1])
        self.assertIn("use FLASH until then", errors[0][1])


class RuleTest(Profile):
    XCP = {"bus": "VEH", "master_id": 0x650, "slave_id": 0x651}
    SAFE = [{"name": "Limit", "type": "uint16", "runnables": ["Run10ms"], "min": 0, "max": 100,
             "safety": "asil_b"}]
    QM = [{"name": "Gain", "type": "uint16", "runnables": ["Run10ms"]},
          {"name": "Offset", "type": "sint16", "runnables": ["Run10ms"], "safety": "qm"}]

    def test_production_has_no_xcp(self):
        errors = self.errors({"region": "FLASH", "size": SECTOR, "profile": "production"}, xcp=self.XCP)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0][0], "xcp")
        self.assertIn("production profile has no write path", errors[0][1])
        self.assertEqual(self.errors({"region": "FLASH", "size": SECTOR}, xcp=self.XCP), [])   # development

    def test_production_has_no_second_copy_for_defaults(self):
        errors = self.errors({"region": "FLASH", "size": SECTOR, "profile": "production", "on_invalid": "defaults"})
        self.assertEqual(errors[0][0], "memory.calibration.on_invalid")
        self.assertIn("needs the RAM working page", errors[0][1])

    def test_safety_relevant_parameter_requires_refuse(self):
        components = {"QmSwc": self.QM, "SafeSwc": self.SAFE}
        for reaction in ("defaults", "warn"):
            with self.subTest(on_invalid=reaction):
                errors = self.errors({"region": "FLASH", "size": SECTOR, "on_invalid": reaction},
                                     components=components)
                self.assertEqual(len(errors), 1)
                path, text = errors[0]
                self.assertEqual(path, "memory.calibration.on_invalid")
                self.assertIn(f"on_invalid {reaction} is not allowed: SafeSwc.Limit is asil_b", text)
                self.assertIn("swc/SafeSwc/swc.yaml", text)
        self.assertEqual(self.errors({"region": "FLASH", "size": SECTOR, "on_invalid": "refuse"},
                                     components=components), [])

    def test_qm_parameters_leave_the_choice(self):
        for reaction in ("defaults", "warn", "refuse"):
            with self.subTest(on_invalid=reaction):
                self.assertEqual(self.errors({"region": "FLASH", "size": SECTOR, "on_invalid": reaction},
                                             components={"QmSwc": self.QM}), [])

    def test_safety_parameters_are_listed(self):
        config, _ = self.check({"region": "FLASH", "size": SECTOR},
                               components={"QmSwc": self.QM, "SafeSwc": self.SAFE + [
                                   {"name": "Axis", "type": "uint8", "runnables": ["Run10ms"], "kind": "axis",
                                    "dim": 2, "min": 0, "max": 9, "safety": "asil_a"}]})
        found = [(p.component, p.parameter, p.safety) for p in calibration.safety_parameters(config)]
        self.assertEqual(found, [("SafeSwc", "Limit", "asil_b"), ("SafeSwc", "Axis", "asil_a")])

    def test_missing_calibration_section_is_reported(self):
        # SYS-96: without memory.calibration an ASIL parameter would stay an ordinary RTE-03 value in RAM, with no
        # segment on_invalid could ever apply to - that gap is now its own error, in the style of on_invalid below
        errors = self.errors(None, components={"QmSwc": self.QM, "SafeSwc": self.SAFE})
        self.assertEqual(len(errors), 1)
        path, text = errors[0]
        self.assertEqual(path, "memory.calibration")
        self.assertIn("memory.calibration is missing: SafeSwc.Limit is asil_b", text)
        self.assertIn("swc/SafeSwc/swc.yaml", text)

    def test_qm_only_project_needs_no_calibration_section(self):
        # a project without any safety relevant parameter is unaffected (SYS-96 does not force a segment on qm)
        self.assertEqual(self.errors(None, components={"QmSwc": self.QM}), [])
        self.assertEqual(self.errors(None), [])                             # no components at all


class DtcTest(Profile):
    """SYS-101 (board finding 2026-09-28/9c): memory.calibration and the DTC of event.bsw calibration.segment need
    each other, or RteSystem.reportCalibrationToDem() finds nothing (findReportedEvent) and stays silent. Own
    profile: a diag.yaml is only written when a test passes one, so the Profile/RuleTest cases above (no diag.yaml
    at all) stay exactly as before - a project without any diagnostic description is out of scope of this rule."""

    CAL = {"region": "FLASH", "size": SECTOR}

    def test_no_diag_description_leaves_the_rule_silent(self):
        # projects without project.diag (ARCH-51) have no fault memory to add the DTC to; unaffected by SYS-101
        for reaction in ("refuse", "defaults", "warn"):
            with self.subTest(on_invalid=reaction):
                self.assertEqual(self.errors({**self.CAL, "on_invalid": reaction}), [])

    def test_missing_dtc_is_an_error_for_refuse_and_defaults(self):
        for reaction in ("refuse", "defaults"):
            with self.subTest(on_invalid=reaction):
                errors = self.errors({**self.CAL, "on_invalid": reaction}, diag=diag_with_dtcs([other_dtc()]))
                self.assertEqual(len(errors), 1)
                path, text = errors[0]
                self.assertEqual(path, "memory.calibration")
                self.assertIn(f"on_invalid is {reaction}", text)
                self.assertIn("calibration.segment", text)

    def test_missing_dtc_is_only_a_warning_for_warn(self):
        errors = self.errors({**self.CAL, "on_invalid": "warn"}, diag=diag_with_dtcs([other_dtc()]))
        self.assertEqual(errors, [])
        warnings = self.warnings({**self.CAL, "on_invalid": "warn"}, diag=diag_with_dtcs([other_dtc()]))
        self.assertEqual(len(warnings), 1)
        path, text = warnings[0]
        self.assertEqual(path, "memory.calibration")
        self.assertIn("on_invalid is warn", text)

    def test_matching_dtc_satisfies_every_reaction(self):
        for reaction in ("refuse", "defaults", "warn"):
            with self.subTest(on_invalid=reaction):
                diag = diag_with_dtcs([other_dtc(), calibration_dtc()])
                self.assertEqual(self.errors({**self.CAL, "on_invalid": reaction}, diag=diag), [])
                self.assertEqual(self.warnings({**self.CAL, "on_invalid": reaction}, diag=diag), [])

    def test_dtc_without_memory_calibration_is_an_error(self):
        # the reverse of the rule above: without a segment NX_CALIBRATION_SEGMENT is never defined, so the DTC
        # would keep "no test result" forever (RteSystem.reportCalibrationToDem is not even compiled)
        errors = self.errors(None, diag=diag_with_dtcs([other_dtc(), calibration_dtc()]))
        self.assertEqual(len(errors), 1)
        path, text = errors[0]
        self.assertEqual(path, "dtcs[1].event.bsw")
        self.assertIn("CalibrationSegment", text)
        self.assertIn("calibration.segment", text)

    def test_dtc_of_a_different_event_does_not_need_memory_calibration(self):
        self.assertEqual(self.errors(None, diag=diag_with_dtcs([other_dtc()])), [])


class DebounceTest(Profile):
    """SYS-102 (board finding 2026-09-28/9c, "mit der Entprellung der Vorlage (failed_threshold 10) reift der DTC
    nie"): reportCalibrationToDem()/PlausibilityMonitor.startUp() report FAILED or PASSED exactly once at
    start-up, so the DTC's debounce must already mature on that single report in both directions."""

    CAL = {"region": "FLASH", "size": SECTOR}
    SAFE = [{"name": "Limit", "type": "uint16", "runnables": ["Run10ms"], "min": 0, "max": 100, "safety": "asil_b"}]

    def test_default_thresholds_never_mature(self):
        # the plain schema default (127/-128) is meant for a periodically resampled event (e.g. the demo's
        # 0x904001 template), not for a DTC a BSW event reports only once
        for reaction in ("refuse", "defaults"):
            with self.subTest(on_invalid=reaction):
                diag = diag_with_dtcs([calibration_dtc(debounce={"counter": {}})])
                errors = self.errors({**self.CAL, "on_invalid": reaction}, diag=diag)
                self.assertEqual(len(errors), 1)
                path, text = errors[0]
                self.assertEqual(path, "dtcs[0].debounce")
                self.assertIn(f"on_invalid is {reaction}", text)
                self.assertIn("failed_threshold is 127", text)
                self.assertIn("passed_threshold is -128", text)

    def test_default_thresholds_are_only_a_warning_for_warn(self):
        diag = diag_with_dtcs([calibration_dtc(debounce={"counter": {}})])
        self.assertEqual(self.errors({**self.CAL, "on_invalid": "warn"}, diag=diag), [])
        warnings = self.warnings({**self.CAL, "on_invalid": "warn"}, diag=diag)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0][0], "dtcs[0].debounce")

    def test_failed_threshold_one_matures(self):
        diag = diag_with_dtcs([calibration_dtc()])  # default: failed_threshold 1, passed_threshold -1
        self.assertEqual(self.errors({**self.CAL, "on_invalid": "refuse"}, diag=diag), [])

    def test_jump_and_increment_reach_the_threshold_on_the_first_sample(self):
        # a stricter but still one-report-safe configuration must not be flagged
        debounce = {"counter": {"failed_threshold": 5, "passed_threshold": -5, "jump_up": 3, "increment": 2,
                                "jump_down": -3, "decrement": 2}}
        diag = diag_with_dtcs([calibration_dtc(debounce=debounce)])
        self.assertEqual(self.errors({**self.CAL, "on_invalid": "refuse"}, diag=diag), [])

    def test_one_direction_short_of_the_threshold_is_still_an_error(self):
        debounce = {"counter": {"failed_threshold": 1, "passed_threshold": -2}}  # passed never reaches -2
        diag = diag_with_dtcs([calibration_dtc(debounce=debounce)])
        errors = self.errors({**self.CAL, "on_invalid": "refuse"}, diag=diag)
        self.assertEqual(len(errors), 1)
        self.assertIn("passed_threshold is -2", errors[0][1])

    def test_time_debounce_above_cycle_ms_never_matures(self):
        diag = diag_with_dtcs([calibration_dtc(debounce={"time": {"failed_ms": 50, "passed_ms": 50}})])
        errors = self.errors({**self.CAL, "on_invalid": "refuse"}, diag=diag)
        self.assertEqual(len(errors), 1)
        self.assertIn("fault_memory.cycle_ms (10)", errors[0][1])

    def test_time_debounce_at_cycle_ms_matures(self):
        diag = diag_with_dtcs([calibration_dtc(debounce={"time": {"failed_ms": 10, "passed_ms": 10}})])
        self.assertEqual(self.errors({**self.CAL, "on_invalid": "refuse"}, diag=diag), [])

    XCP = {"bus": "VEH", "master_id": 0x650, "slave_id": 0x651}

    def test_plausibility_dtc_needs_the_same_maturing_debounce_without_a_segment(self):
        # SYS-90: without memory.calibration, RteSystem.cpp's in-place check is the only caller for this event -
        # memory.calibration is then also missing itself (SYS-96), so both errors are expected together
        diag = diag_with_dtcs([plausibility_dtc(debounce={"counter": {}})])
        errors = self.errors(None, components={"SafeSwc": self.SAFE}, diag=diag)
        debounce_errors = [(p, t) for p, t in errors if p == "dtcs[0].debounce"]
        self.assertEqual(len(debounce_errors), 1, errors)
        text = debounce_errors[0][1]
        self.assertIn("calibration.plausibility", text)
        self.assertIn("PlausibilityMonitor.startUp()", text)
        self.assertIn("memory.calibration is missing", text)

    def test_plausibility_dtc_needs_the_same_maturing_debounce_with_a_segment_but_no_xcp(self):
        # a segment without an xcp section has no write path either: still a single report at start-up
        # (Calibration.cpp), so the same rule applies, following on_invalid like the segment's own rule
        diag = diag_with_dtcs([calibration_dtc(), plausibility_dtc(debounce={"counter": {}})])
        errors = self.errors({**self.CAL, "on_invalid": "refuse"}, components={"SafeSwc": self.SAFE}, diag=diag)
        self.assertEqual(len(errors), 1)
        path, text = errors[0]
        self.assertEqual(path, "dtcs[1].debounce")
        self.assertIn("calibration.plausibility", text)
        self.assertIn("on_invalid is refuse", text)

    def test_plausibility_dtc_matures_with_the_default(self):
        diag = diag_with_dtcs([calibration_dtc(), plausibility_dtc()])
        self.assertEqual(self.errors({**self.CAL, "on_invalid": "refuse"}, components={"SafeSwc": self.SAFE},
                                     diag=diag), [])

    def test_plausibility_without_a_safety_relevant_parameter_is_not_checked(self):
        # NX_RTE_HAS_PARAMETER_CHECKS is not compiled without one, so the DTC is never reported at all
        diag = diag_with_dtcs([calibration_dtc(), plausibility_dtc(debounce={"counter": {}})])
        self.assertEqual(self.errors({**self.CAL, "on_invalid": "refuse"}, diag=diag), [])

    def test_plausibility_is_out_of_scope_with_a_segment_and_xcp(self):
        # with both present, a refused XCP write (Plausibility.cpp acceptWrite()) can report the event again -
        # no longer a single report, so SYS-102 does not check it here
        diag = diag_with_dtcs([calibration_dtc(), plausibility_dtc(debounce={"counter": {}})])
        self.assertEqual(self.errors({**self.CAL, "on_invalid": "refuse"}, xcp=self.XCP,
                                     components={"SafeSwc": self.SAFE}, diag=diag), [])


class RealDerivativeTest(unittest.TestCase):
    """The same rules on real derivatives, from copies of projects of the tree (paths made absolute)."""

    def check(self, source: Path, cal: dict, rte: Path | None = None, drop_xcp: bool = False,
             drop_diag: bool = False):
        tmp = Path(tempfile.mkdtemp())
        try:
            project = yaml.safe_load((source / "project.yaml").read_text(encoding="utf-8"))
            project["project"]["board"] = str((source / project["project"]["board"]).resolve())
            for key in ("rte", "diag"):
                if key in project["project"]:
                    project["project"][key] = str((source / project["project"][key]).resolve())
            if rte is not None:
                project["project"]["rte"] = str(rte)
            if drop_xcp:
                project.pop("xcp", None)
            if drop_diag:
                project["project"].pop("diag", None)
            project["memory"]["calibration"] = cal
            path = tmp / "project.yaml"
            path.write_text(yaml.safe_dump(project, sort_keys=False, allow_unicode=True), encoding="utf-8")
            return nxconfig.check(path)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_s32k144_demo(self):
        # SYS-101: the demo's diag.yaml has no DTC for calibration.segment (the project has no memory.calibration
        # of its own, see doc/changelog.d/SYS-101.md) - drop_diag isolates this test on the placement math of
        # memory.py, the concern it has always been about; DtcTest above covers the SYS-101 rule itself.
        config, messages = self.check(DEMO, {"region": "PFLASH", "size": 0x1000, "on_invalid": "defaults"},
                                      drop_diag=True)
        self.assertEqual([str(m) for m in messages if m.severity == "error"], [])
        cal = config.memory.calibration
        self.assertEqual((cal.start, cal.end, cal.sector_size), (0x9000, 0xA000, 0x1000))

    def test_s32k148_calibration_profile_needs_refuse(self):
        _, messages = self.check(S32K148, {"region": "PFLASH", "size": 0x1000, "on_invalid": "warn"}, rte=CALIB_RTE)
        errors = [m for m in messages if m.severity == "error"]
        self.assertEqual([m.yaml_path for m in errors], ["memory.calibration.on_invalid"])
        self.assertIn("CalMapSwc.SpeedAxis_rpm is asil_b and 2 more", errors[0].text)
        config, messages = self.check(S32K148, {"region": "PFLASH", "size": 0x1000, "profile": "production"},
                                      rte=CALIB_RTE, drop_xcp=True)
        self.assertEqual([str(m) for m in messages if m.severity == "error"], [])
        self.assertEqual((config.memory.calibration.start, config.memory.calibration.production), (0x9000, True))

    def test_s32k148_data_flash_is_refused_until_bl31(self):
        _, messages = self.check(S32K148, {"region": "FLEXNVM", "size": 0x1000})
        errors = [m for m in messages if m.severity == "error"]
        self.assertEqual([m.yaml_path for m in errors], ["memory.calibration.region"])


if __name__ == "__main__":
    unittest.main()
