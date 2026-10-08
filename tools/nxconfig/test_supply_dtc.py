"""IO-59: the supply DTCs of diag.yaml (bsw.power.undervoltage/overvoltage, condition bsw.power.ok) against the supply
of the project - tools/nxconfig/supply.py _supply_dtcs.

Own profiles only: the synthetic boards of tools/nxconfig/test/profiles plus one synthetic board with a UJA1131 (role
power_supply) on TESTMCU64 - never the eval board. Each case starts from the new project of the wizard (standard
diagnostics) and changes one thing; every finding is compared with its severity and YAML path.

Run: .venv/Scripts/python -m unittest tools/nxconfig/test_supply_dtc.py
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from ruamel.yaml import YAML

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from nxconfig import config as nxcfg  # noqa: E402
from nxconfig import newproject as np  # noqa: E402
from nxconfig import supply  # noqa: E402

PROFILES = HERE / "test" / "profiles"
TEST_MCUDB = HERE / "test" / "mcudb"
TASKS = Path("ecu") / "asyncCoreConfiguration" / "tasks.yaml"

# Test data only: the testboard with its first CAN bus behind a UJA1131 SBC (provider of the role power_supply).
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

UNDER = "bsw.power.undervoltage"
OVER = "bsw.power.overvoltage"


def yaml_rt() -> YAML:
    y = YAML(typ="rt")
    y.width = 4096
    return y


class SupplyDtcTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="nxsupply-test-"))
        cls.repo = cls.tmp / "repo"
        shutil.copytree(PROFILES, cls.repo)
        (cls.repo / "boards" / "testboard_sbc").mkdir()
        (cls.repo / "boards" / "testboard_sbc" / "board.yaml").write_text(SBC_BOARD, encoding="utf-8")
        (cls.repo / TASKS).parent.mkdir(parents=True)
        shutil.copyfile(nxcfg.REPO / TASKS, cls.repo / TASKS)
        cls.projects = {}
        for board in ("testboard_sbc", "testboard"):
            plan = np.plan_empty(f"sup_{board}", board, repo=cls.repo, mcudb=TEST_MCUDB, with_diag=True)
            result = np.create(plan)
            assert not result.errors, [str(m) for m in result.errors]
            cls.projects[board] = result.project_file

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.saved = {b: (p.parent / "diag.yaml").read_text(encoding="utf-8") for b, p in self.projects.items()}

    def tearDown(self):
        for board, text in self.saved.items():
            (self.projects[board].parent / "diag.yaml").write_text(text, encoding="utf-8")

    def edit(self, board: str, change) -> None:
        file = self.projects[board].parent / "diag.yaml"
        y = yaml_rt()
        data = y.load(file.read_text(encoding="utf-8"))
        change(data)
        with file.open("w", encoding="utf-8") as out:
            y.dump(data, out)

    def findings(self, board: str) -> list[tuple[str, str, str]]:
        _cfg, messages = nxcfg.check(self.projects[board], mcudb=TEST_MCUDB)
        return [(m.severity, m.yaml_path, m.text) for m in messages
                if "power" in m.text or "supply" in m.text.lower()]

    @staticmethod
    def dtc_index(data, event: str) -> int:
        return next(i for i, d in enumerate(data["dtcs"]) if d["event"].get("bsw") == event)

    # ---------------------------------------------------------------------------------------------- clean cases

    def test_wizard_project_with_sbc_is_clean(self):
        # the SBC monitors 9000/18000 mV (module defaults): the state of the provider decides, nothing to report
        text = (self.projects["testboard_sbc"].parent / "diag.yaml").read_text(encoding="utf-8")
        self.assertIn(UNDER, text)
        self.assertIn(OVER, text)
        self.assertEqual(self.findings("testboard_sbc"), [])

    def test_limits_in_the_right_direction_are_clean(self):
        def change(data):
            data["dtcs"][self.dtc_index(data, UNDER)]["event"]["failed_below"] = 10500
            data["dtcs"][self.dtc_index(data, OVER)]["event"]["failed_above"] = 15000
            data["enable_conditions"].append({"name": "SupplyOk", "bsw": "bsw.power.ok", "initial": False})
        self.edit("testboard_sbc", change)
        self.assertEqual(self.findings("testboard_sbc"), [])

    # ----------------------------------------------------------------------------------------- injected faults

    def test_wrong_direction(self):
        def change(data):
            data["dtcs"][self.dtc_index(data, UNDER)]["event"]["failed_above"] = 9000
            data["dtcs"][self.dtc_index(data, OVER)]["event"]["failed_below"] = 16000
        self.edit("testboard_sbc", change)
        found = self.findings("testboard_sbc")
        under = self.dtc_index_of("testboard_sbc", UNDER)
        over = self.dtc_index_of("testboard_sbc", OVER)
        self.assertIn(("error", f"dtcs[{under}].event.failed_above"), [(s, p) for s, p, _ in found])
        self.assertIn(("error", f"dtcs[{over}].event.failed_below"), [(s, p) for s, p, _ in found])
        self.assertTrue(any("give failed_below (mV)" in t for *_x, t in found))

    def test_result_other_than_limit(self):
        self.edit("testboard_sbc", lambda d: d["dtcs"][self.dtc_index(d, UNDER)]["event"].__setitem__("result",
                                                                                                    "boolean"))
        under = self.dtc_index_of("testboard_sbc", UNDER)
        self.assertIn(("error", f"dtcs[{under}].event.result"), [(s, p) for s, p, _ in self.findings("testboard_sbc")])

    def test_other_power_name_as_event(self):
        def change(data):
            dtc = data["dtcs"][self.dtc_index(data, UNDER)]
            dtc["event"]["bsw"] = "bsw.power.supply_mv"
            dtc["number"] = 0x901001                      # no source of the plan: no derived number
        self.edit("testboard_sbc", change)
        found = self.findings("testboard_sbc")
        self.assertTrue(any(s == "error" and p.endswith(".event.bsw") and "no event of the supply" in t
                            for s, p, t in found), found)

    def test_threshold_of_the_sbc_switched_off(self):
        project = self.projects["testboard_sbc"]
        original = project.read_text(encoding="utf-8")
        try:
            y = yaml_rt()
            data = y.load(original)
            data.setdefault("modules", {})["SBC"] = {"undervoltage_mv": 0}
            with project.open("w", encoding="utf-8") as out:
                y.dump(data, out)
            found = self.findings("testboard_sbc")
            under = self.dtc_index_of("testboard_sbc", UNDER)
            self.assertIn(("warning", f"dtcs[{under}].event.bsw"), [(s, p) for s, p, _ in found], found)
            self.assertTrue(any("modules.SBC.undervoltage_mv 0" in t for *_x, t in found))
            # with a limit of its own the DTC compares the voltage: no warning
            self.edit("testboard_sbc", lambda d: d["dtcs"][self.dtc_index(d, UNDER)]["event"].__setitem__(
                "failed_below", 10000))
            self.assertNotIn(("warning", f"dtcs[{under}].event.bsw"),
                             [(s, p) for s, p, _ in self.findings("testboard_sbc")])
        finally:
            project.write_text(original, encoding="utf-8")

    def test_limit_outside_the_monitor_range(self):
        self.edit("testboard_sbc", lambda d: d["dtcs"][self.dtc_index(d, OVER)]["event"].__setitem__(
            "failed_above", 24000))
        over = self.dtc_index_of("testboard_sbc", OVER)
        self.assertIn(("warning", f"dtcs[{over}].event.failed_above"),
                      [(s, p) for s, p, _ in self.findings("testboard_sbc")])

    def test_no_provider(self):
        # the plain testboard has no supply measurement: the wizard writes no supply DTC; one added by hand (and a
        # condition on bsw.power.ok) never gets a result
        text = (self.projects["testboard"].parent / "diag.yaml").read_text(encoding="utf-8")
        self.assertNotIn("bsw.power.", text)
        self.assertEqual(self.findings("testboard"), [])

        def change(data):
            data["dtcs"].append({"name": "Under", "number": 0x901000, "severity": "check_at_next_halt",
                                 "operation_cycle": "PowerCycle", "event": {"bsw": UNDER},
                                 "debounce": {"time": {"failed_ms": 100, "passed_ms": 100}}})
            data["enable_conditions"].append({"name": "SupplyOk", "bsw": "bsw.power.ok", "initial": False})
        self.edit("testboard", change)
        found = [(s, p) for s, p, _ in self.findings("testboard")]
        index = self.dtc_index_of("testboard", UNDER)
        condition = len(self.load("testboard")["enable_conditions"]) - 1
        self.assertIn(("warning", f"dtcs[{index}].event.bsw"), found)
        self.assertIn(("warning", f"enable_conditions[{condition}].bsw"), found)

    # ----------------------------------------------------------------------------------------------- helpers

    def load(self, board: str):
        return YAML(typ="safe").load((self.projects[board].parent / "diag.yaml").read_text(encoding="utf-8"))

    def dtc_index_of(self, board: str, event: str) -> int:
        return self.dtc_index(self.load(board), event)


class ProvidersTest(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(supply.SUPPLY_EVENTS, {UNDER: "failed_below", OVER: "failed_above"})
        self.assertEqual(supply.SUPPLY_CONDITION, "bsw.power.ok")


if __name__ == "__main__":
    unittest.main()
