"""NVM-16: a defective NvM block (CRC/read error, defaults loaded) as a DTC - tools/nxconfig/bswdtc.py nvm_dtc_yaml().

The generator only assembles a `dtcs[]` entry per NvM block (name, event.bsw, bsw_instance) in the shape
tools/diaggen/standard_diag.yaml already uses for the calibration segment; the numbering itself (TOOL-178,
bswdtc.PLAN) and the reporting at run time (libs/bsw/nvm, ecu/application/src/systems/nvm/NvmEvents.h, NVM-19) are
not repeated here. Checked:

  NvmDtcYamlTest       the produced YAML parses as `Dtc` entries with the derived numbers of the plan, one entry
                       for a QM block, three (with the two ASIL-only sources) for an ASIL block
  ContractTest         the event name prefixes match ecu/application/src/systems/nvm/NvmEvents.h (DEM_EVENTS) and
                       tools/rtegen/rtegen.py (DEM_BSW_REPORTERS) recognises them (no more "not reported" warning)
  BlockIndexTest       bswdtc.block_index() - the bsw_instance of a block among its project's other NvM blocks
  NvmDtcEntriesTest    bswdtc.nvm_dtc_entries() - the dict form nvm_dtc_yaml() renders as YAML text, used by the
                       Studio dialog "Neuer NvM-Block" (NVM-20) to insert entries via yamldoc (comment keeping)
                       instead of pasting text
  StandardDiagNvmBlocksTest   tools/nxconfig/newproject.standard_diag(nvm_blocks=...) (NVM-20): a project already
                       defining NvM blocks gets their dtcs[] right in the generated diag.yaml, before dtc_groups

Run: .venv/Scripts/python -m unittest tools/nxconfig/test_bswdtc_nvm.py
"""

import re
import sys
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
TOOLS = HERE.parent
sys.path.insert(0, str(HERE.parent))
for _path in (TOOLS, TOOLS / "rtegen"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from nxconfig import bswdtc, model as nxmodel  # noqa: E402
from nxconfig import newproject as newproject_mod  # noqa: E402

import rtegen  # noqa: E402

NVM_EVENTS_H = REPO / "ecu" / "application" / "src" / "systems" / "nvm" / "NvmEvents.h"


def dtcs_of(yaml_text: str) -> list:
    """Parsed, fully validated Dtc list of a minimal DiagDescription around `yaml_text` (bsw_dtc_range default)."""
    data = yaml.safe_load(f"dtcs:\n{yaml_text}")
    desc = nxmodel.DiagDescription(schema_version=1, fault_memory={"max_entries": 8}, dtcs=data["dtcs"])
    return desc.dtcs


class NvmDtcYamlTest(unittest.TestCase):
    def test_qm_block_gets_one_entry(self):
        dtcs = dtcs_of(bswdtc.nvm_dtc_yaml("Trim", 2, asil=False))
        self.assertEqual([d.name for d in dtcs], ["NvmTrimDefaultLoaded"])
        dtc = dtcs[0]
        self.assertEqual(dtc.event.bsw, "nvm.default_loaded.Trim")
        self.assertEqual(dtc.bsw_instance, 2)
        self.assertEqual(dtc.number, bswdtc.BSW_DTC_BASE + 0x0600 + 2)
        self.assertTrue(dtc.number_derived)
        self.assertEqual(dtc.confirmation_threshold, 1)              # matures on the first report, like SYS-89
        self.assertEqual(dtc.debounce.counter.failed_threshold, 1)
        self.assertEqual(dtc.debounce.counter.passed_threshold, -1)
        self.assertEqual(dtc.operation_cycle, "PowerCycle")

    def test_asil_block_gets_all_three_sources(self):
        dtcs = dtcs_of(bswdtc.nvm_dtc_yaml("Counter", 0, asil=True))
        self.assertEqual([d.name for d in dtcs],
                         ["NvmCounterDefaultLoaded", "NvmCounterCopyRepairFailed", "NvmCounterCopyRepaired"])
        events = [d.event.bsw for d in dtcs]
        self.assertEqual(events, ["nvm.default_loaded.Counter", "nvm.copy_repair_failed.Counter",
                                  "nvm.copy_repaired.Counter"])
        numbers = [d.number for d in dtcs]
        self.assertEqual(numbers, [bswdtc.BSW_DTC_BASE + 0x0600, bswdtc.BSW_DTC_BASE + 0x0700,
                                   bswdtc.BSW_DTC_BASE + 0x0800])
        # cross-check: the plan derives the very same numbers directly from the event names
        for event, number in zip(events, numbers):
            self.assertEqual(bswdtc.derive(event, bswdtc.BSW_DTC_BASE, bswdtc.BSW_DTC_SIZE, 0), number)

    def test_qm_block_with_place_defective(self):
        # NVM-30: a QM block may also get the DTC of one defective place (+0x09nn); an ASIL block never does
        dtcs = dtcs_of(bswdtc.nvm_dtc_yaml("Trim", 2, asil=False, place_defective=True))
        self.assertEqual([d.name for d in dtcs], ["NvmTrimDefaultLoaded", "NvmTrimPlaceDefective"])
        self.assertEqual([d.event.bsw for d in dtcs], ["nvm.default_loaded.Trim", "nvm.place_defective.Trim"])
        self.assertEqual([d.number for d in dtcs], [bswdtc.BSW_DTC_BASE + 0x0602, bswdtc.BSW_DTC_BASE + 0x0902])
        self.assertEqual(dtcs[1].confirmation_threshold, 1)
        asil = bswdtc.nvm_dtc_entries("Counter", 0, asil=True, place_defective=True)
        self.assertNotIn("nvm.place_defective.Counter", [e["event"]["bsw"] for e in asil])
        self.assertEqual(len(bswdtc.nvm_dtc_entries("Trim", 2, asil=False)), 1)   # default unchanged

    def test_custom_operation_cycle_and_index(self):
        dtcs = dtcs_of(bswdtc.nvm_dtc_yaml("Table", 0xFF, asil=False, operation_cycle="Power"))
        self.assertEqual(dtcs[0].operation_cycle, "Power")
        self.assertEqual(dtcs[0].bsw_instance, 0xFF)
        self.assertEqual(dtcs[0].number, bswdtc.BSW_DTC_BASE + 0x0600 + 0xFF)

    def test_index_out_of_range_is_refused(self):
        with self.assertRaises(bswdtc.DerivationError):
            bswdtc.nvm_dtc_yaml("Trim", 0x100, asil=False)
        with self.assertRaises(bswdtc.DerivationError):
            bswdtc.nvm_dtc_yaml("Trim", -1, asil=False)

    def test_names_stay_unique_per_block(self):
        # two blocks of one project get two independent DTC names/numbers, never colliding (OdxName uniqueness
        # is checked by diagdesc.py, not repeated here - this only shows the generator itself does not collide)
        a = dtcs_of(bswdtc.nvm_dtc_yaml("Trim", 0, asil=False))
        b = dtcs_of(bswdtc.nvm_dtc_yaml("Counter", 1, asil=False))
        self.assertNotEqual(a[0].name, b[0].name)
        self.assertNotEqual(a[0].number, b[0].number)


class ContractTest(unittest.TestCase):
    """Ties the generator to the run-time side (NvmEvents.h) and to the generator-warning side (rtegen.py), so a
    renamed event on either side breaks a test here instead of only failing silently at run time."""

    def test_event_prefixes_match_nvm_events_h(self):
        if not NVM_EVENTS_H.is_file():
            self.skipTest(f"{NVM_EVENTS_H} not found")
        source = NVM_EVENTS_H.read_text(encoding="utf-8")
        prefixes = set(re.findall(r'"(nvm\.[a-z_]+)"', source))
        expected = {bswdtc.SOURCES[key].events[0][0][:-2]
                    for key, _suffix, _text in bswdtc.NVM_DTC_SOURCES + bswdtc.NVM_QM_DTC_SOURCES}
        self.assertEqual(expected, {"nvm.default_loaded", "nvm.copy_repair_failed", "nvm.copy_repaired",
                                    "nvm.place_defective"})
        self.assertTrue(expected <= prefixes, f"{expected - prefixes} missing from {NVM_EVENTS_H}")

    def test_rtegen_no_longer_warns_about_the_generated_events(self):
        for key, _suffix, _text in bswdtc.NVM_DTC_SOURCES + bswdtc.NVM_QM_DTC_SOURCES:
            pattern = bswdtc.SOURCES[key].events[0][0]
            event = pattern[:-2] + ".SomeBlock"
            self.assertIsNotNone(rtegen.dem_bsw_reporter(event), f"{event} still warns in tools/rtegen")
        # an event outside the plan (e.g. a project's own BSW source) still warns, as it always has
        self.assertIsNone(rtegen.dem_bsw_reporter("project.something.SomeBlock"))


class BlockIndexTest(unittest.TestCase):
    """bswdtc.block_index(): the bsw_instance a block gets among the project's other NvM blocks - name order,
    like tools/rtegen lays the blocks out (component writers in name order), `block` itself included."""

    def test_position_in_name_order(self):
        self.assertEqual(bswdtc.block_index("Trim", ["Trim", "Counter"]), 1)
        self.assertEqual(bswdtc.block_index("Counter", ["Trim", "Counter"]), 0)

    def test_a_block_not_yet_among_names_still_gets_its_position(self):
        # the Studio dialog "Neuer NvM-Block" computes this before the block is written (NVM-20)
        self.assertEqual(bswdtc.block_index("New", ["Trim", "Counter"]), 1)

    def test_a_block_of_the_fault_memory_is_refused(self):
        with self.assertRaises(bswdtc.DerivationError):
            bswdtc.block_index("DemEvents", ["Trim"])


class NvmDtcEntriesTest(unittest.TestCase):
    """bswdtc.nvm_dtc_entries(): the dict form of nvm_dtc_yaml() - the same entries, for a writer (tools/studio/core
    /diagnostics.DiagModel.add_entry, yamldoc) that edits an existing diag.yaml instead of pasting YAML text."""

    def test_entries_match_the_rendered_yaml(self):
        entries = bswdtc.nvm_dtc_entries("Trim", 2, asil=False)
        dtcs = dtcs_of(bswdtc.nvm_dtc_yaml("Trim", 2, asil=False))
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["name"], dtcs[0].name)
        self.assertEqual(entry["bsw_instance"], dtcs[0].bsw_instance)
        self.assertEqual(entry["event"]["bsw"], dtcs[0].event.bsw)
        self.assertEqual(entry["operation_cycle"], dtcs[0].operation_cycle)
        self.assertEqual(entry["debounce"]["counter"]["failed_threshold"], dtcs[0].debounce.counter.failed_threshold)

    def test_asil_gets_three_entries_qm_gets_one(self):
        self.assertEqual(len(bswdtc.nvm_dtc_entries("Counter", 0, asil=True)), 3)
        self.assertEqual(len(bswdtc.nvm_dtc_entries("Counter", 0, asil=False)), 1)


class StandardDiagNvmBlocksTest(unittest.TestCase):
    """tools/nxconfig/newproject.standard_diag(nvm_blocks=...) (NVM-20): a project that already defines NvM blocks
    (project.yaml nvm.blocks) gets their dtcs[] entries right in the generated diag.yaml - the same entries the
    Studio dialog "Neuer NvM-Block" offers to add to an existing one."""

    def test_without_nvm_blocks_nothing_changes(self):
        plain = newproject_mod.standard_diag("demo")
        self.assertNotIn("Nvm", plain)

    def test_defined_blocks_get_their_dtcs_before_dtc_groups(self):
        text = newproject_mod.standard_diag("demo", nvm_blocks={"Trim": False, "Counter": True})
        data = yaml.safe_load(text)
        desc = nxmodel.DiagDescription.model_validate(data)
        names = [d.name for d in desc.dtcs]
        # RteDeadline (always present) first, then the NvM DTCs, all before the position of dtc_groups in the text
        self.assertEqual(names[0], "RteDeadline")
        self.assertEqual(set(names[1:]),
                         {"NvmTrimDefaultLoaded", "NvmCounterDefaultLoaded", "NvmCounterCopyRepairFailed",
                          "NvmCounterCopyRepaired",
                          # SYS-113: the causes of a safety reset of the standard description
                          "SafetyResetMcuWatchdog", "SafetyResetFaultException", "SafetyResetDeadmanConfiguration",
                          "SafetyResetOther", "SafetyResetLoop"})
        self.assertLess(text.index("NvmTrimDefaultLoaded"), text.index("dtc_groups:"))
        by_name = {d.name: d for d in desc.dtcs}
        self.assertEqual(by_name["NvmTrimDefaultLoaded"].bsw_instance, 1)     # name order: Counter (0), Trim (1)
        self.assertEqual(by_name["NvmCounterDefaultLoaded"].bsw_instance, 0)

    def test_combines_with_calibration(self):
        text = newproject_mod.standard_diag("demo", features={"calibration"}, nvm_blocks={"Trim": False})
        data = yaml.safe_load(text)
        desc = nxmodel.DiagDescription.model_validate(data)
        names = {d.name for d in desc.dtcs}
        self.assertTrue({"CalibrationSegmentInvalid", "CalibrationPlausibility", "NvmTrimDefaultLoaded"} <= names)


if __name__ == "__main__":
    unittest.main()
