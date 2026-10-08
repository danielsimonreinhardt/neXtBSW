"""Host tests of the standard diagnostic description of neXtBSW (TOOL-131): the template
tools/diaggen/standard_diag.yaml, its rendering per project (tools/nxconfig/newproject.py standard_diag) and the new
project that carries it - without and with a supply measurement, without and with a calibration segment.

Own profiles only: the synthetic boards of tools/nxconfig/test/profiles under a temporary repository root plus one
synthetic board with a UJA1131 (role power_supply) on TESTMCU64 - never the eval board. Every generated project must
pass nxconfig, rtegen and diaggen without an error.
"""

import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from ruamel.yaml import YAML

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "rtegen"))
sys.path.insert(0, str(HERE))                   # diaggen.py, not the folder tools/diaggen

import diaggen  # noqa: E402
import rtegen  # noqa: E402
from nxconfig import bswdtc  # noqa: E402
from nxconfig import config as nxcfg  # noqa: E402
from nxconfig import newproject as np  # noqa: E402
from nxconfig.model import DiagDescription  # noqa: E402

TEMPLATE = HERE / "standard_diag.yaml"
NXCONFIG_TEST = HERE.parent / "nxconfig" / "test"
PROFILES = NXCONFIG_TEST / "profiles"
TEST_MCUDB = NXCONFIG_TEST / "mcudb"
TASKS = Path("ecu") / "asyncCoreConfiguration" / "tasks.yaml"

# Synthetic board: the testboard with its first CAN bus behind a UJA1131 SBC on MEM_SPI (a provider of the role
# power_supply). Test data only.
SBC_BOARD = """schema_version: 1
board:
  name: testboard_sbc
  mcu: TESTMCU64
  package: QFP64
  clock: { sosc_hz: 8000000 }
  adc_reference_mv: 3300
devices:
  - { name: SBC, part: UJA1131HW/FD/5V/4Y }
  - { name: PHY_B, part: TJA1051 }
spi:
  - name: SBC_SPI
    instance: SPI1
    sck: { pin: PA5 }
    sin: { pin: PA6 }
    sout: { pin: PA7 }
    pcs: { pin: PA4, index: 0 }
    device: SBC
can:
  - { name: VEH, instance: FDCAN1, rx: { pin: PA11 }, tx: { pin: PA12 }, phy: SBC }
  - { name: DIAG, instance: FDCAN2, rx: { pin: PB12 }, tx: { pin: PB13 }, phy: PHY_B }
uart:
  - { name: CONSOLE, instance: USART2, rx: { pin: PA3 }, tx: { pin: PA2 } }
"""

SUPPLY_SOURCES = {"bsw.power.supply_mv", "bsw.power.qlf"}
CALIBRATION_EVENTS = {"calibration.segment", "calibration.plausibility"}
EXAMPLE_BUSOFF = "can.busoff.CAN0"          # COM-46: the example bus of the `# [each can_bus]` block
SUPPLY_EVENTS = {"bsw.power.undervoltage", "bsw.power.overvoltage"}     # IO-59: sampled by the RTE, not reported
# SYS-113: the cause of a safety reset, reported after the next start (libs/bsw/dem dem/SafetyResetReport.h)
SAFETY_RESET_EVENTS = {"safety.mcu_watchdog", "safety.fault_exception", "safety.deadman_configuration_error",
                       "safety.reset_other", "safety.reset_loop"}
SAFETY_RESET_NUMBERS = {"SafetyResetMcuWatchdog": 0x1605FD, "SafetyResetFaultException": 0x1605FC,
                        "SafetyResetDeadmanConfiguration": 0x160510, "SafetyResetOther": 0x1605FB,
                        "SafetyResetLoop": 0x1605FF}


def safe_load(text: str):
    return YAML(typ="safe").load(text)


def description(text: str) -> DiagDescription:
    return DiagDescription.model_validate(safe_load(text))


def element_sources(desc: DiagDescription, did: str) -> list[str]:
    entry = next(d for d in desc.data_identifiers if d.name == did)
    return [e.source.bsw for e in entry.data]


class TemplateTest(unittest.TestCase):
    """The template itself: a valid description, fixed DTC range, the scope of the decision 2026-09-29."""

    def setUp(self):
        self.text = TEMPLATE.read_text(encoding="utf-8")
        self.full = description(self.text)          # the markers are comments: the file is the full variant

    def test_scope_of_the_decision(self):
        self.assertEqual([(s.name, s.id) for s in self.full.sessions], [("Default", 0x01), ("Extended", 0x03)])
        self.assertEqual([(s.name, s.level, s.method, s.sessions) for s in self.full.security],
                         [("Application", 0x01, "hmac-sha256", ["Extended"])])
        self.assertEqual([d.identifier for d in self.full.data_identifiers],
                         [0xF180, 0xF181, 0xF186, 0xF18C, 0xF191, 0xF195, 0xF1F1, 0xF1F2, 0x0120, 0x0121, 0x0122,
                          0x0130])   # NVM-05
        # PLAT-79: configuration identity, a value of the stack as well
        self.assertEqual(element_sources(self.full, "ConfigurationIdentity"), ["configuration.identity"])
        self.assertEqual(element_sources(self.full, "WorkspaceVersion"), ["workspace.version"])   # PLAT-93
        # COM-45: status of the basic software - the RTE always, the calibration segment only with one
        self.assertEqual(element_sources(self.full, "BswStatus"), ["bsw.rte.deadline_violations"])
        self.assertEqual(element_sources(self.full, "CalibrationState"),
                         ["bsw.calibration.layout", "bsw.calibration.dataset", "bsw.calibration.status"])
        # COM-44: hardware number and neXtBSW version are values of the stack, derived, not written in the template
        self.assertEqual(element_sources(self.full, "EcuHardwareNumber"), ["hardware.number"])
        self.assertEqual(element_sources(self.full, "SystemSupplierSoftwareVersion"), ["nextbsw.version"])
        self.assertNotIn(0xFD00, [d.identifier for d in self.full.data_identifiers])     # RAM test only in the demo
        self.assertEqual(element_sources(self.full, "EcuConditions"),
                         ["ecu.state", "bsw.power.supply_mv", "bsw.power.qlf", "bsw.reset.reason", "bsw.time.ms"])
        self.assertFalse(any(s and s.startswith("adc.") for d in self.full.data_identifiers
                             for s in (e.source.bsw for e in d.data)))                    # nothing of a board
        self.assertEqual([c.name for c in self.full.operation_cycles], ["PowerCycle"])
        self.assertEqual([(c.name, c.bsw) for c in self.full.enable_conditions], [("EcuRunning", "ecu.state")])
        self.assertEqual({d.event.bsw for d in self.full.dtcs},
                         {"rte.deadline", EXAMPLE_BUSOFF} | CALIBRATION_EVENTS | SUPPLY_EVENTS | SAFETY_RESET_EVENTS)
        self.assertEqual(self.full.routines, [])                                          # FF00/FF01 are the BL's

    def test_dtc_numbers_come_from_the_nextbsw_range(self):
        # TOOL-178: the BSW DTCs carry no number in the file, the schema derives it from bsw_dtc_range + plan
        raw = safe_load(self.text)
        self.assertEqual(raw["bsw_dtc_range"], {"base": np.NXBSW_DTC_FIRST,
                                                "size": np.NXBSW_DTC_LAST - np.NXBSW_DTC_FIRST + 1})
        self.assertFalse(any("number" in d or "display_code" in d for d in raw["dtcs"]))
        for dtc in self.full.dtcs:
            with self.subTest(dtc=dtc.name):
                self.assertTrue(dtc.number_derived)
                self.assertTrue(np.NXBSW_DTC_FIRST < dtc.number <= np.NXBSW_DTC_LAST, hex(dtc.number))
                self.assertEqual(dtc.group, "nextbsw")
                self.assertEqual(dtc.display_code, f"P{dtc.number:06X}")
        blocks = {dtc.event.bsw: dtc.number >> 8 for dtc in self.full.dtcs}
        self.assertEqual(blocks["rte.deadline"], np.NXBSW_DTC_BLOCKS["rte"])
        self.assertEqual(blocks["calibration.segment"], np.NXBSW_DTC_BLOCKS["calibration"])
        self.assertEqual(blocks["calibration.plausibility"], np.NXBSW_DTC_BLOCKS["calibration"])
        self.assertEqual(blocks[EXAMPLE_BUSOFF], np.NXBSW_DTC_BLOCKS["can_busoff"])
        self.assertEqual(blocks["bsw.power.undervoltage"], np.NXBSW_DTC_BLOCKS["supply"])
        self.assertEqual({d.name: d.number for d in self.full.dtcs},
                         {"RteDeadline": 0x160100, "CanBusOffCAN0": 0x160300, "CalibrationSegmentInvalid": 0x160200,
                          "CalibrationPlausibility": 0x160201, "SupplyUndervoltage": 0x160400,
                          "SupplyOvervoltage": 0x160401, **SAFETY_RESET_NUMBERS})
        group = self.full.dtc_groups[0]
        self.assertTrue(group.bsw)
        self.assertEqual((group.group, group.first, group.last),
                         (np.NXBSW_DTC_FIRST, np.NXBSW_DTC_FIRST, np.NXBSW_DTC_LAST))

    def test_moving_the_base_moves_every_bsw_dtc(self):
        raw = safe_load(self.text)
        raw["bsw_dtc_range"] = {"base": 0x5A0000, "size": 0x1000}
        moved = DiagDescription.model_validate(raw)
        self.assertEqual({d.name: (d.number, d.display_code) for d in moved.dtcs},
                         {"RteDeadline": (0x5A0100, "C1A0100"), "CanBusOffCAN0": (0x5A0300, "C1A0300"),
                          "CalibrationSegmentInvalid": (0x5A0200, "C1A0200"),
                          "CalibrationPlausibility": (0x5A0201, "C1A0201"),
                          "SupplyUndervoltage": (0x5A0400, "C1A0400"), "SupplyOvervoltage": (0x5A0401, "C1A0401"),
                          **{name: (number - 0x160000 + 0x5A0000, f"C1A{number & 0xFFFF:04X}")
                             for name, number in SAFETY_RESET_NUMBERS.items()}})
        group = moved.dtc_groups[0]
        self.assertEqual((group.group, group.first, group.last), (0x5A0000, 0x5A0000, 0x5A0FFF))

    def test_numbering_plan_of_the_header_matches_the_table(self):
        # the documented plan (header of the template) and bswdtc.PLAN are one table
        notes = self.text.split("\n" + np.STANDARD_DIAG_NOTES_END + "\n")[0]
        planned = {int(m, 16) for m in re.findall(r"^#   \+0x([0-9A-F]{2})xx ", notes, re.MULTILINE)}
        self.assertEqual(planned, {s.block for s in bswdtc.PLAN})
        self.assertEqual({(np.NXBSW_DTC_FIRST >> 8) + b for b in planned if b},
                         set(np.NXBSW_DTC_BLOCKS.values()))
        fixed = {int(m, 16) for m in re.findall(r"\+0x([0-9A-F]{4}) \w", notes)}
        self.assertEqual(fixed, {s.offset + low for s in bswdtc.PLAN for _p, low in s.events if low is not None})
        self.assertIn(f"0x{np.NXBSW_DTC_FIRST:06X}..0x{np.NXBSW_DTC_LAST:06X}", notes)

    def test_reported_events_mature_on_one_report(self):
        # SYS-102: the BSW reports these events, a periodic debouncing would never mature
        for dtc in self.full.dtcs:
            if dtc.event.bsw in SUPPLY_EVENTS:
                continue
            with self.subTest(dtc=dtc.name):
                self.assertEqual((dtc.debounce.counter.failed_threshold, dtc.debounce.counter.passed_threshold),
                                 (1, -1))
        # IO-59: the supply is sampled every cycle - time debouncing in whole cycles, no limit in the template (the
        # thresholds of the provider decide, project values of its module), the limit direction only via nxconfig
        for dtc in (d for d in self.full.dtcs if d.event.bsw in SUPPLY_EVENTS):
            with self.subTest(dtc=dtc.name):
                self.assertIsNone(dtc.debounce.counter)
                self.assertEqual((dtc.debounce.time.failed_ms, dtc.debounce.time.passed_ms), (500, 500))
                self.assertEqual(dtc.debounce.time.failed_ms % self.full.fault_memory.cycle_ms, 0)
                self.assertEqual((dtc.event.failed_below, dtc.event.failed_above, dtc.event.result), (None, None, None))
                self.assertIn("EcuConditions", dtc.snapshots[0].dids)
        # the start-up checks of the calibration report before RUN: no enable condition may swallow them
        self.assertTrue(all(d.enable_conditions == [] for d in self.full.dtcs if d.event.bsw in CALIBRATION_EVENTS))
        # SYS-113: neither the cause of a safety reset - a fault that resets again soon after the start still gets it
        self.assertTrue(all(d.enable_conditions == [] for d in self.full.dtcs if d.event.bsw in SAFETY_RESET_EVENTS))

    def test_the_description_generates(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "diag.yaml"
            file.write_text(self.text, encoding="utf-8")
            generated = diaggen.plan(diaggen.load_file(file))
            for did in ("F180", "F181", "F186", "F18C", "0120"):
                self.assertIn(f"{{0x{did}U, ", generated.source_cpp)
            self.assertIn("{0x160000U, 0x160000U, 0x16FFFFU}, // NeXtBSW", generated.dtc_groups_h)


class RenderTest(unittest.TestCase):
    """standard_diag(): optional parts per feature, markers and template notes removed."""

    def test_bus_off_per_used_can_bus(self):
        # COM-46: one bus-off DTC per used CAN bus, bsw_instance = index of the bus, number base + 0x0300 + index
        for buses in ((), ("VEH",), ("VEH", "DIAG"), ("CAN0", "CAN1", "CAN2")):
            for features in ((), ("supply", "calibration")):
                with self.subTest(buses=buses, features=features):
                    text = np.standard_diag("ecu_x", features, can_buses=buses)
                    self.assertNotIn("# [each", text)
                    desc = description(text)
                    busoff = [d for d in desc.dtcs if (d.event.bsw or "").startswith("can.busoff.")]
                    self.assertEqual([(d.name, d.event.bsw, d.bsw_instance, d.number) for d in busoff],
                                     [(f"CanBusOff{b}", f"can.busoff.{b}", i, 0x160300 + i)
                                      for i, b in enumerate(buses)])
                    for d in busoff:
                        bus = d.event.bsw.split(".")[-1]
                        self.assertIn(bus, d.label)
                        self.assertIn(bus, d.description)
                    if "CAN0" not in buses:
                        self.assertNotIn("CAN0", text)
                    self.assertEqual(bswdtc.source_of(busoff[0].event.bsw)[0].key if busoff else None,
                                     "can_busoff" if buses else None)

    def test_bad_bus_names(self):
        for buses in (("VEH", "VEH"), ("1BUS",), ("A-B",)):
            with self.subTest(buses=buses), self.assertRaises(np.NewProjectError):
                np.standard_diag("x", (), can_buses=buses)

    def test_every_combination(self):
        for features in ((), ("supply",), ("calibration",), ("supply", "calibration")):
            with self.subTest(features=features):
                text = np.standard_diag("ecu_x", features)
                desc = description(text)
                self.assertNotIn("# [if", text)
                self.assertNotIn("# [end", text)
                self.assertNotIn(np.STANDARD_DIAG_NOTES_END, text)
                self.assertTrue(text.startswith("# Diagnostic description (ARCH-51) of project ecu_x"))
                sources = set(element_sources(desc, "EcuConditions"))
                self.assertEqual(SUPPLY_SOURCES <= sources, "supply" in features)
                self.assertFalse(SUPPLY_SOURCES & sources and "supply" not in features)
                events = {d.event.bsw for d in desc.dtcs}
                self.assertEqual(events, {"rte.deadline"} | SAFETY_RESET_EVENTS   # SYS-113: in every variant
                                 | (CALIBRATION_EVENTS if "calibration" in features
                                                             else set())
                                 | (SUPPLY_EVENTS if "supply" in features else set()))
                # COM-45: the calibration state only with a calibration segment
                names = [d.name for d in desc.data_identifiers]
                self.assertIn("BswStatus", names)
                self.assertIn("EcuStatistics", names)   # NVM-05: in every variant
                self.assertEqual("CalibrationState" in names, "calibration" in features)

    def test_unknown_feature_and_broken_template(self):
        with self.assertRaises(np.NewProjectError):
            np.standard_diag("x", {"heater"})
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / np.STANDARD_DIAG
            broken.parent.mkdir(parents=True)
            text = TEMPLATE.read_text(encoding="utf-8")
            broken.write_text(text.replace("# [end supply]\n", ""), encoding="utf-8")
            with self.assertRaises(np.NewProjectError) as e:
                np.standard_diag("x", (), Path(tmp))
            self.assertIn("never closed", str(e.exception))
            broken.write_text(text.replace(np.STANDARD_DIAG_NOTES_END, "# notes"), encoding="utf-8")
            with self.assertRaises(np.NewProjectError):
                np.standard_diag("x", (), Path(tmp))
            # COM-46: an [each] block that runs into an [if], an unknown one, an [end] without [each]
            for broken_text, words in ((text.replace("# [end can_bus]\n", ""), "inside an [each] block"),
                                       (text.replace("# [each can_bus]", "# [each lin_bus]"), "unknown block"),
                                       (text.replace("# [each can_bus]\n", ""), "without [each")):
                broken.write_text(broken_text, encoding="utf-8")
                with self.subTest(words=words), self.assertRaises(np.NewProjectError) as e:
                    np.standard_diag("x", (), Path(tmp), can_buses=["VEH"])
                self.assertIn(words, str(e.exception))


class NewProjectTest(unittest.TestCase):
    """A new project on own test boards: without/with supply measurement, without/with calibration segment."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nxstd-test-"))
        self.repo = self.tmp / "repo"
        shutil.copytree(PROFILES, self.repo)
        (self.repo / "boards" / "testboard_sbc").mkdir()
        (self.repo / "boards" / "testboard_sbc" / "board.yaml").write_text(SBC_BOARD, encoding="utf-8")
        (self.repo / TASKS).parent.mkdir(parents=True)
        shutil.copyfile(nxcfg.REPO / TASKS, self.repo / TASKS)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def create(self, name: str, board: str, calibration: bool):
        plan = np.plan_empty(name, board, repo=self.repo, mcudb=TEST_MCUDB, with_diag=True, calibration=calibration)
        result = np.create(plan)
        self.assertEqual([str(m) for m in result.errors], [])
        return plan, result.project_file

    def generators_accept(self, project: Path):
        errors = [str(m) for m in rtegen.validate(project, repo=self.repo, mcudb=TEST_MCUDB) if m.severity == "error"]
        self.assertEqual(errors, [])
        self.assertEqual([str(m) for m in diaggen.validate(project, mcudb=TEST_MCUDB) if m.severity == "error"], [])
        out = self.tmp / "gen" / project.parent.name
        diaggen.write(diaggen.plan(diaggen.load_project(project, mcudb=TEST_MCUDB)), out, rte_stub=True)
        self.assertTrue((out / "DiagDescription.cpp").is_file())

    def test_projects_without_and_with_supply_and_calibration(self):
        for board, supply in (("testboard", False), ("testboard_sbc", True)):
            for calibration in (False, True):
                name = f"std_{'sbc' if supply else 'plain'}_{'cal' if calibration else 'nocal'}"
                with self.subTest(project=name):
                    plan, project = self.create(name, board, calibration)
                    folder = project.parent
                    data = safe_load(project.read_text(encoding="utf-8"))
                    self.assertEqual(data["project"]["diag"], "diag.yaml")
                    desc = description((folder / "diag.yaml").read_text(encoding="utf-8"))
                    sources = set(element_sources(desc, "EcuConditions"))
                    self.assertEqual(SUPPLY_SOURCES <= sources, supply)
                    self.assertEqual(bool(SUPPLY_SOURCES & sources), supply)
                    events = {d.event.bsw for d in desc.dtcs}
                    self.assertEqual(CALIBRATION_EVENTS <= events, calibration)
                    self.assertEqual(bool(CALIBRATION_EVENTS & events), calibration)
                    # IO-59: the supply DTCs only with a supply measurement (provider of the role power_supply)
                    self.assertEqual(SUPPLY_EVENTS <= events, supply)
                    self.assertEqual(bool(SUPPLY_EVENTS & events), supply)
                    self.assertIn("rte.deadline", events)
                    # COM-46: the wizard uses the first CAN bus of the board (VEH on both boards), index 0
                    busoff = [(d.name, d.bsw_instance, d.number) for d in desc.dtcs
                              if (d.event.bsw or "").startswith("can.busoff.")]
                    self.assertEqual(busoff, [("CanBusOffVEH", 0, 0x160300)])
                    self.assertIn("bus-off DTC of CAN VEH", next(n for n in plan.notes
                                                                 if n.startswith("diag.yaml: standard")))
                    # chip UID: a derivative fact (TESTMCU64 states 12 bytes, the template 16 for S32K1)
                    uid = next(d for d in desc.data_identifiers if d.identifier == 0xF18C).data[0]
                    self.assertEqual(uid.length_bytes, 12)
                    cal = data["memory"].get("calibration")
                    if calibration:
                        # TESTMCU64: 8 KB sectors - one sector, refuse, development
                        self.assertEqual(cal, {"region": "FLASH", "size": 0x2000, "on_invalid": "refuse",
                                               "profile": "development"})
                    else:
                        self.assertIsNone(cal)
                    note = next(n for n in plan.notes if n.startswith("diag.yaml: standard diagnostics"))
                    self.assertEqual("supply voltage from SBC" in note, supply)
                    self.generators_accept(project)

    def test_no_diagnostic_connection_keeps_the_empty_description(self):
        # a board without a CAN bus has no diagnostics section - the standard jobs would have no connection
        (self.repo / "boards" / "nocan").mkdir()
        (self.repo / "boards" / "nocan" / "board.yaml").write_text(
            SBC_BOARD.replace("name: testboard_sbc", "name: nocan").split("devices:")[0]
            + "uart:\n  - { name: CONSOLE, instance: USART2, rx: { pin: PA3 }, tx: { pin: PA2 } }\n",
            encoding="utf-8")
        plan = np.plan_empty("no_can", "nocan", repo=self.repo, mcudb=TEST_MCUDB, with_diag=True, calibration=True)
        diag = next(f.text for f in plan.files if f.path == "diag.yaml")
        self.assertEqual(safe_load(diag), {"schema_version": 1})
        self.assertTrue(any("standard diagnostics of neXtBSW need one" in n for n in plan.notes))
        self.assertTrue(any(n.startswith("no calibration segment") for n in plan.notes))
        self.assertNotIn("calibration:", next(f.text for f in plan.files if f.path == "project.yaml"))

    def test_without_diag_nothing_changes(self):
        plan = np.plan_empty("no_diag", "testboard", repo=self.repo, mcudb=TEST_MCUDB)
        self.assertEqual(plan.tree(), ["project.yaml", "rte.yaml"])
        self.assertFalse(any("standard diagnostics" in n for n in plan.notes))


if __name__ == "__main__":
    unittest.main()
