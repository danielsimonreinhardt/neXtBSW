"""COM-46/NVM-08: `bsw_instance` of the BSW DTCs with instances is a project fact - tools/nxconfig/diagdesc.py
_check_bsw_instances and tools/nxconfig/bswdtc.py SAFETY_CAUSES.

  SafetyCausesTest    SAFETY_CAUSES against SafeSupervisor::Event (ecu/safety/safeSupervisor/include/safeSupervisor/
                      SafeSupervisor.h): the low byte of safety.<cause> is the number of the event
  BusOffInstanceTest  the new project of the wizard with every signal of the synthetic testboard (buses VEH, DIAG):
                      one bus-off DTC per bus with its index, clean; injected faults - swapped indices, a bus the project
                      does not use, a wrong cause number - each an error at its YAML path; SYS-196: the DTC
                      safety.safe_off_take_back and the values bsw.safeoff.* in a project without safe_off output
                      (error at each path), counter-check with testproject_switches (two safe_off inputs: no finding)

Own profiles only (tools/nxconfig/test/profiles), never the eval board.
Run: .venv/Scripts/python -m unittest tools/nxconfig/test_bswdtc_sources.py
"""

import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from ruamel.yaml import YAML

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))

from nxconfig import bswdtc  # noqa: E402
from nxconfig import config as nxcfg  # noqa: E402
from nxconfig import newproject as np  # noqa: E402

PROFILES = HERE / "test" / "profiles"
TEST_MCUDB = HERE / "test" / "mcudb"
TASKS = Path("ecu") / "asyncCoreConfiguration" / "tasks.yaml"
SUPERVISOR_H = REPO / "ecu" / "safety" / "safeSupervisor" / "include" / "safeSupervisor" / "SafeSupervisor.h"


def supervisor_events() -> list[str]:
    """The enumerators of SafeSupervisor::Event in their order (comments removed)."""
    text = SUPERVISOR_H.read_text(encoding="utf-8")
    body = text.split("enum class Event : uint8_t", 1)[1].split("};", 1)[0]
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    body = re.sub(r"//[^\n]*", "", body)
    return [name.strip() for name in body.strip().strip("{").split(",") if name.strip()]


class SafetyCausesTest(unittest.TestCase):
    def test_causes_are_the_event_numbers(self):
        events = supervisor_events()
        self.assertIn("DEVICE_FAULT", events)
        for cause, number in bswdtc.SAFETY_CAUSES.items():
            with self.subTest(cause=cause):
                self.assertEqual(events.index(cause.upper()), number)
        # the plan's own fixed sub-type of the block (+0x05FF reset loop) is no event number
        self.assertLess(len(events), 0xFF)

    def test_numbers_follow_the_plan(self):
        self.assertEqual(bswdtc.derive("safety.device_fault", 0x160000, 0x10000, 0x13), 0x160513)
        self.assertEqual(bswdtc.derive("can.busoff.DIAG", 0x160000, 0x10000, 1), 0x160301)
        # SYS-113: the causes of a safety reset that are no event have fixed sub-types, the events their number
        self.assertEqual(bswdtc.derive("safety.mcu_watchdog", 0x160000, 0x10000), 0x1605FD)
        self.assertEqual(bswdtc.derive("safety.fault_exception", 0x160000, 0x10000), 0x1605FC)
        self.assertEqual(bswdtc.derive("safety.reset_other", 0x160000, 0x10000), 0x1605FB)
        self.assertEqual(bswdtc.derive("safety.deadman_configuration_error", 0x160000, 0x10000, 0x10), 0x160510)
        # SYS-196: the take-back of a foreign safe_off level, a fixed sub-type below the causes of a safety reset
        self.assertEqual(bswdtc.derive(bswdtc.SAFE_OFF_TAKE_BACK, 0x160000, 0x10000), 0x1605FA)
        self.assertEqual(bswdtc.derive(bswdtc.SAFE_OFF_TAKE_BACK, 0x200000, 0x1000), 0x2005FA)
        with self.assertRaises(bswdtc.DerivationError):     # no instance: one DTC for every safe_off output
            bswdtc.derive(bswdtc.SAFE_OFF_TAKE_BACK, 0x160000, 0x10000, 0)
        with self.assertRaises(bswdtc.DerivationError):     # a fixed sub-type takes no bsw_instance
            bswdtc.derive("safety.mcu_watchdog", 0x160000, 0x10000, 0xFD)
        with self.assertRaises(bswdtc.DerivationError):     # an event cause cannot take a fixed sub-type
            bswdtc.derive("safety.etl_assert", 0x160000, 0x10000, 0xFB)

    def test_causes_are_the_sources_of_the_safety_reset_report(self):
        # SYS-113: libs/bsw/dem dem/SafetyResetReport.h reports exactly these names; the events of SAFETY_CAUSES
        # except device_fault (the degraded reaction, reported by RteSystem) and the fixed sub-types of the plan
        header = (REPO / "libs" / "bsw" / "dem" / "include" / "dem" / "SafetyResetReport.h").read_text(encoding="utf-8")
        names = set(re.findall(r'^\s+"safety\.([a-z_]+)",$', header.split("EVENTS[] = {", 1)[1].split("};", 1)[0],
                               re.M))
        fixed = {pattern.split(".", 1)[1]: low for pattern, low in bswdtc.SOURCES["safety"].events
                 if low is not None and pattern not in ("safety.hw_shutoff", bswdtc.SAFE_OFF_TAKE_BACK)}
        self.assertEqual(fixed, {"reset_loop": 0xFF, "mcu_watchdog": 0xFD, "fault_exception": 0xFC,
                                 "reset_other": 0xFB})
        self.assertEqual(names, (set(bswdtc.SAFETY_CAUSES) - {"device_fault"}) | set(fixed))
        for name, low in fixed.items():
            with self.subTest(name=name):
                self.assertRegex(header, rf"INSTANCE_{name.upper()}\s*=\s*0x{low:02X}U;")


class BusOffInstanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="nxbswdtc-test-"))
        cls.repo = cls.tmp / "repo"
        shutil.copytree(PROFILES, cls.repo)
        (cls.repo / TASKS).parent.mkdir(parents=True)
        shutil.copyfile(nxcfg.REPO / TASKS, cls.repo / TASKS)
        plan = np.plan_empty("busoff_all", "testboard", repo=cls.repo, mcudb=TEST_MCUDB, with_diag=True,
                             signals="all")
        result = np.create(plan)
        assert not result.errors, [str(m) for m in result.errors]
        cls.project = result.project_file
        cls.diag_file = cls.project.parent / "diag.yaml"
        cls.original = cls.diag_file.read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def tearDown(self):
        self.diag_file.write_text(self.original, encoding="utf-8")

    def edit(self, change):
        y = YAML(typ="rt")
        y.width = 4096
        data = y.load(self.original)
        change(data)
        with self.diag_file.open("w", encoding="utf-8") as out:
            y.dump(data, out)

    def errors(self) -> list[tuple[str, str]]:
        _cfg, messages = nxcfg.check(self.project, mcudb=TEST_MCUDB)
        return [(m.yaml_path, m.text) for m in messages if m.severity == "error"]

    @staticmethod
    def index_of(data, name: str) -> int:
        return next(i for i, d in enumerate(data["dtcs"]) if d["name"] == name)

    def test_wizard_writes_one_dtc_per_bus(self):
        data = YAML(typ="safe").load(self.original)
        busoff = [(d["name"], d["event"]["bsw"], d["bsw_instance"]) for d in data["dtcs"]
                  if d["event"].get("bsw", "").startswith("can.busoff.")]
        self.assertEqual(busoff, [("CanBusOffVEH", "can.busoff.VEH", 0), ("CanBusOffDIAG", "can.busoff.DIAG", 1)])
        self.assertEqual(self.errors(), [])

    def test_swapped_index(self):
        def swap(data):
            data["dtcs"][self.index_of(data, "CanBusOffVEH")]["bsw_instance"] = 1
            data["dtcs"][self.index_of(data, "CanBusOffDIAG")]["bsw_instance"] = 0
        self.edit(swap)
        data = YAML(typ="safe").load(self.diag_file.read_text(encoding="utf-8"))
        found = self.errors()
        veh, diag = self.index_of(data, "CanBusOffVEH"), self.index_of(data, "CanBusOffDIAG")
        self.assertIn(f"dtcs[{veh}].bsw_instance", [p for p, _ in found])
        self.assertIn(f"dtcs[{diag}].bsw_instance", [p for p, _ in found])
        self.assertTrue(any("bus VEH has the index 0 among the used CAN buses (VEH, DIAG" in t for _, t in found))

    def test_bus_not_used(self):
        def other(data):
            dtc = data["dtcs"][self.index_of(data, "CanBusOffDIAG")]
            dtc["event"]["bsw"] = "can.busoff.BODY"
        self.edit(other)
        data = YAML(typ="safe").load(self.diag_file.read_text(encoding="utf-8"))
        path = f"dtcs[{self.index_of(data, 'CanBusOffDIAG')}].event.bsw"
        found = self.errors()
        self.assertIn(path, [p for p, _ in found], found)
        self.assertTrue(any("names no CAN bus the project uses (VEH, DIAG" in t for _, t in found))

    def test_wrong_cause_number(self):
        def safety(data):
            data["dtcs"].append({"name": "SafetyDegraded", "severity": "check_immediately",
                                 "operation_cycle": "PowerCycle", "bsw_instance": 0x12,
                                 "event": {"bsw": "safety.device_fault"},
                                 "debounce": {"counter": {"failed_threshold": 1, "passed_threshold": -1}}})
        self.edit(safety)
        data = YAML(typ="safe").load(self.diag_file.read_text(encoding="utf-8"))
        found = self.errors()
        self.assertIn(f"dtcs[{self.index_of(data, 'SafetyDegraded')}].bsw_instance", [p for p, _ in found], found)
        self.assertTrue(any("has the number 0x13 (SafeSupervisor::Event), not 0x12" in t for _, t in found))

        def right(data):
            safety(data)
            data["dtcs"][-1]["bsw_instance"] = 0x13
        self.edit(right)
        self.assertEqual(self.errors(), [])

    # SYS-196 (decision SYS194-4 b): the DTC of a foreign safe_off level taken back only where the hold runs

    @staticmethod
    def add_safe_off_take_back(data):
        data["dtcs"].append({"name": "SafeOffTakeBack", "severity": "check_immediately",
                             "operation_cycle": "PowerCycle", "confirmation_threshold": 1,
                             "event": {"bsw": bswdtc.SAFE_OFF_TAKE_BACK},
                             "debounce": {"counter": {"failed_threshold": 1, "passed_threshold": -1}}})
        data["data_identifiers"].append({
            "identifier": 0x0140, "name": "SafeOffTakeBack", "read": True, "read_sessions": ["Default"],
            "data": [{"name": "output", "type": "uint8", "source": {"bsw": "bsw.safeoff.output"}},
                     {"name": "takeBacks", "type": "uint32", "source": {"bsw": "bsw.safeoff.take_backs"}}]})

    def test_safe_off_take_back_without_safe_off_output(self):
        # the wizard project of the testboard integrates no device with a safe_off output: no hold, no DTC
        cfg, _messages = nxcfg.check(self.project, mcudb=TEST_MCUDB)
        from nxconfig import degraded
        self.assertEqual(degraded.safe_off_entries(cfg), [])
        self.edit(self.add_safe_off_take_back)
        data = YAML(typ="safe").load(self.diag_file.read_text(encoding="utf-8"))
        dtc = self.index_of(data, "SafeOffTakeBack")
        did = next(i for i, d in enumerate(data["data_identifiers"]) if d["name"] == "SafeOffTakeBack")
        found = self.errors()
        paths = [p for p, _ in found]
        self.assertIn(f"dtcs[{dtc}].event.bsw", paths, found)
        self.assertIn(f"data_identifiers[{did}].data[0].source.bsw", paths, found)
        self.assertIn(f"data_identifiers[{did}].data[1].source.bsw", paths, found)
        self.assertTrue(all("no safe_off output" in t for _, t in found), found)
        self.assertEqual(len(found), 3, found)

    def test_safe_off_take_back_with_safe_off_outputs(self):
        # counter-check: testproject_switches integrates two high-side switches with a safe_off input - the same
        # entries are no finding there (the check alone, the project has no diagnostic connection of its own)
        from nxconfig import degraded
        from nxconfig import diagdesc
        cfg, _messages = nxcfg.check(PROFILES / "projects" / "testproject_switches" / "project.yaml",
                                     mcudb=TEST_MCUDB)
        self.assertEqual([e.output for e in degraded.safe_off_entries(cfg)], ["LAMP_IN", "PUMP_IN"])
        own, _ = nxcfg.check(self.project, mcudb=TEST_MCUDB)   # before the edit: a clean wizard project
        self.assertIsNotNone(own)
        self.edit(self.add_safe_off_take_back)
        desc, messages, _tree = nxcfg.read_diag(self.diag_file)
        self.assertIsNotNone(desc, [str(m) for m in messages])
        rep = nxcfg.Reporter()
        diagdesc._check_safe_off_take_back(cfg, rep, desc)
        self.assertEqual(rep.messages, [])
        # and the same description against the wizard project: the check itself reports both
        rep = nxcfg.Reporter()
        diagdesc._check_safe_off_take_back(own, rep, desc)
        self.assertEqual(len(rep.messages), 3)


if __name__ == "__main__":
    unittest.main()
