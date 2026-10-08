"""Host tests of the XCP section of a project (TOOL-26): tools/nxconfig/model.py XcpUse and diag.py check_xcp.

Own profile: the synthetic bootloader project on testboard (family testfam, buses VEH and DIAG, other diagnostic
ids than the eval board), plus a generated rte.yaml and DBC in a temporary tree. The demo project is checked too.

Run: .venv/Scripts/python -m unittest tools/nxconfig/test_xcp.py
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
from nxconfig import diag  # noqa: E402
from nxconfig.config import Reporter  # noqa: E402

TEST_MCUDB = HERE / "test" / "mcudb"
TEST_BOARD = HERE / "test/profiles/boards/testboard/board.yaml"
BL_PROJECT = yaml.safe_load((HERE / "test/profiles/projects/testproject_bootloader/project.yaml")
                            .read_text(encoding="utf-8"))
# PLAT-62: the own full-featured test profile (synthetic testboard_full/testproject_full) instead of the eval board
# project, which stays private (PLAT-46 F10) - a test against it would be red in the exported open tree. What
# proves something about the eval board itself uses EVAL_DEMO_PROJECT and is skipped where it is missing.
DEMO_PROJECT = REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full" / "project.yaml"
EVAL_DEMO_PROJECT = REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml"
EXT = 0x80000000                                    # bit 31: 29-bit identifier (project.yaml, DBC)
# DBC of the profile: one standard and one extended message on VEH
DBC = """VERSION ""

BU_: ECU TESTER

BO_ 1904 VehStatus: 8 ECU
 SG_ Speed : 0|16@1+ (1,0) [0|65535] "" TESTER

BO_ {ext} VehExtended: 8 ECU
 SG_ Value : 0|8@1+ (1,0) [0|255] "" TESTER
""".format(ext=EXT | 0x18FF00F1)


class XcpProfile(unittest.TestCase):
    """Writes the bootloader profile with an xcp section (and optionally an ECU description) into a temp tree."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def project(self, xcp=None, rte=None):
        project = copy.deepcopy(BL_PROJECT)
        project["project"]["board"] = "../../boards/testboard"
        if xcp is not None:
            project["xcp"] = xcp
        if rte is not None:
            # ARCH-34: nxconfig checks rte.yaml against its schema - complete the sections this test is not about
            rte = {"schema_version": 2, "components": [{"name": "Swc", "path": "swc/Swc"}], **copy.deepcopy(rte)}
            if "can" in rte:
                rte["can"].setdefault("node", "ECU")
                # RTE-22: relative to the rte.yaml below, which lies two folders under self.tmp
                rte["can"].setdefault("dbc", "../../profile.dbc")
            project["project"]["rte"] = "rte.yaml"
            project_dir = self.tmp / "projects" / "testproject"
            project_dir.mkdir(parents=True, exist_ok=True)
            (project_dir / "rte.yaml").write_text(yaml.safe_dump(rte, sort_keys=False), encoding="utf-8")
        return project

    def dbc(self) -> str:
        path = self.tmp / "profile.dbc"
        path.write_text(DBC, encoding="utf-8")
        return path.as_posix()                      # absolute: REPO / absolute == absolute

    def check(self, project):
        board_dir = self.tmp / "boards" / "testboard"
        project_dir = self.tmp / "projects" / "testproject"
        board_dir.mkdir(parents=True, exist_ok=True)
        project_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(TEST_BOARD, board_dir / "board.yaml")
        path = project_dir / "project.yaml"
        path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        return nxconfig.check(path, TEST_MCUDB)

    def errors(self, project):
        _, messages = self.check(project)
        return [m for m in messages if m.severity == "error"]

    def expect(self, fragment, project, path):
        errors = self.errors(project)
        match = [m for m in errors if fragment in m.text]
        self.assertTrue(match, f"'{fragment}' not in {[str(m) for m in errors]}")
        self.assertEqual((match[0].file.name, match[0].yaml_path), ("project.yaml", path))
        self.assertIsInstance(match[0].line, int)
        return match[0]


class XcpCheckTest(XcpProfile):

    def test_valid_29_bit_ids_on_the_second_bus(self):
        project = self.project({"bus": "DIAG", "master_id": EXT | 0x18DA00F1, "slave_id": EXT | 0x18DAF100})
        config, messages = self.check(project)
        self.assertEqual([str(m) for m in messages], [])
        self.assertEqual(config.project.xcp.bus, "DIAG")
        self.assertEqual(config.project.xcp.master_id, 0x98DA00F1)

    def test_no_section_no_xcp(self):
        config, messages = self.check(self.project())
        self.assertEqual([str(m) for m in messages], [])
        self.assertIsNone(config.project.xcp)

    def test_errors_with_yaml_path(self):
        cases = [
            ({"bus": "DIAG", "master_id": 0x800, "slave_id": 0x6A1},
             "CAN id 0x800 is outside the 11-bit range", "xcp.master_id"),
            ({"bus": "DIAG", "master_id": 0x6A0, "slave_id": 0x1FFFF},
             "set (0x8001FFFF)", "xcp.slave_id"),
            ({"bus": "DIAG", "master_id": 0xA0000000, "slave_id": 0x6A1},
             "CAN id 0xA0000000 is outside the 29-bit range", "xcp.master_id"),
            ({"bus": "DIAG", "master_id": 0x6A0, "slave_id": 0x6A0},
             "slave_id 0x6A0 is also master_id", "xcp.slave_id"),
            ({"bus": "DIAG", "master_id": EXT | 0x100, "slave_id": EXT | 0x100},
             "slave_id 0x80000100 (29-bit 0x100) is also master_id", "xcp.slave_id"),
            ({"bus": "CAN7", "master_id": 0x6A0, "slave_id": 0x6A1},
             "bus CAN7 is not a CAN bus of the project (use.can: VEH, DIAG)", "xcp.bus"),
            ({"bus": "DIAG", "master_id": -1, "slave_id": 0x6A1},
             "greater than or equal to 0", "xcp.master_id"),
            ({"bus": "DIAG", "slave_id": 0x6A1}, "Field required", "xcp.master_id"),
            ({"bus": "DIAG", "master_id": 0x6A0, "slave_id": 0x6A1, "response_id": 1},
             "Extra inputs are not permitted", "xcp.response_id"),
        ]
        for xcp, fragment, path in cases:
            with self.subTest(fragment=fragment):
                self.expect(fragment, self.project(xcp), path)

    def test_collision_with_the_diagnostic_ids_on_the_same_bus(self):
        rte = {"uds": {"bus": "DIAG"}}
        for key, value in (("request_id", 0x611), ("functional_id", 0x6DF), ("response_id", 0x622)):
            with self.subTest(key=key):
                project = self.project({"master_id": value, "slave_id": 0x6A1}, rte)   # default: diagnostic bus
                self.expect(f"master_id 0x{value:X} is also diagnostics.{key} on bus DIAG", project, "xcp.master_id")
        self.expect("slave_id 0x622 is also diagnostics.response_id on bus DIAG",
                    self.project({"bus": "DIAG", "master_id": 0x6A0, "slave_id": 0x622}, rte), "xcp.slave_id")
        # the same id on the other bus, and the 29-bit form of it, are no collision
        for xcp in ({"bus": "VEH", "master_id": 0x611, "slave_id": 0x622},
                    {"master_id": EXT | 0x611, "slave_id": EXT | 0x622}):
            with self.subTest(xcp=xcp):
                self.assertEqual([str(m) for m in self.errors(self.project(xcp, rte))], [])

    def test_collision_with_the_dbc_of_the_bus(self):
        rte = {"can": {"bus": "VEH", "dbc": self.dbc()}, "uds": {"bus": "DIAG"}}
        self.expect("master_id 0x770 is also the CAN id of message VehStatus",
                    self.project({"bus": "VEH", "master_id": 0x770, "slave_id": 0x771}, rte), "xcp.master_id")
        self.expect("slave_id 0x98FF00F1 (29-bit 0x18FF00F1) is also the CAN id of message VehExtended",
                    self.project({"bus": "VEH", "master_id": 0x6A0, "slave_id": EXT | 0x18FF00F1}, rte),
                    "xcp.slave_id")
        # the DBC is on VEH: on DIAG (the default, the diagnostic bus) the same ids are free
        self.assertEqual(self.errors(self.project({"master_id": 0x770, "slave_id": EXT | 0x18FF00F1}, rte)), [])
        # standard 0x0F1 and extended 0x0F1 are different frames
        self.assertEqual(self.errors(self.project({"bus": "VEH", "master_id": 0x6A0, "slave_id": 0x0F1}, rte)), [])

    def test_default_bus_is_the_diagnostic_bus(self):
        """Without xcp.bus the bus of rte.yaml uds.bus (else can.bus, else the first bus) - as tools/rtegen."""
        config, messages = self.check(self.project({"master_id": 0x6A0, "slave_id": 0x6A1}, {"uds": {"bus": "DIAG"}}))
        self.assertEqual(messages, [])
        rep = Reporter()
        self.assertEqual(diag.check_xcp(config, rep, *diag._ecu_buses(config)), "DIAG")
        config, _ = self.check(self.project({"master_id": 0x6A0, "slave_id": 0x6A1}, {"can": {"bus": "DIAG"}}))
        self.assertEqual(diag.check_xcp(config, rep, *diag._ecu_buses(config)), "DIAG")   # bus of the signals
        config, _ = self.check(self.project({"master_id": 0x6A0, "slave_id": 0x6A1}, {}))
        self.assertEqual(diag.check_xcp(config, rep, *diag._ecu_buses(config)), "VEH")    # first used bus
        config, _ = self.check(self.project({"master_id": 0x6A0, "slave_id": 0x6A1}))      # no ECU description
        self.assertEqual(diag.check_xcp(config, rep, *diag._ecu_buses(config)), "VEH")
        self.assertEqual(rep.messages, [])

    def test_no_bus_at_all(self):
        """A project without CAN buses: xcp needs a bus; the other checks of the project stay quiet."""
        project = self.project({"master_id": 0x6A0, "slave_id": 0x6A1})
        project["use"]["can"] = []
        project.pop("bootloader")
        project.pop("can")
        project.pop("memory")
        project.pop("diagnostics")
        errors = self.errors(project)
        self.assertEqual([m.yaml_path for m in errors], ["xcp"], [str(m) for m in errors])
        self.assertIn("no bus for XCP", errors[0].text)


class XcpDemoProjectTest(XcpProfile):
    """The demo project: XCP 0x7F0/0x7F1 on the diagnostic bus CAN0 next to UDS 0x7E0/0x7DF/0x7E8 and the DBC."""

    def demo(self, **xcp):
        project = yaml.safe_load(DEMO_PROJECT.read_text(encoding="utf-8"))
        project["project"]["board"] = (REPO / "tools" / "nxconfig" / "test" / "profiles" / "boards" / "testboard_full").as_posix()
        project["project"]["rte"] = (DEMO_PROJECT.parent / "rte.yaml").as_posix()
        project["project"]["diag"] = (DEMO_PROJECT.parent / "diag.yaml").as_posix()    # ARCH-51
        project["xcp"].update(xcp)
        path = self.tmp / "project.yaml"
        path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        _, messages = nxconfig.check(path)
        return [m for m in messages if m.severity == "error"]

    def test_demo_values(self):
        config, messages = nxconfig.check(DEMO_PROJECT)
        self.assertEqual([m for m in messages if m.severity == "error"], [])
        self.assertEqual((config.project.xcp.bus, config.project.xcp.master_id, config.project.xcp.slave_id),
                         (None, 0x7F0, 0x7F1))

    def test_demo_collisions(self):
        errors = self.demo(master_id=0x7E0)
        self.assertEqual([(m.yaml_path, "diagnostics.request_id on bus CAN0" in m.text) for m in errors],
                         [("xcp.master_id", True)])
        errors = self.demo(slave_id=0x210)                                  # PC_Command of EvalBoard.dbc
        self.assertEqual([m.yaml_path for m in errors], ["xcp.slave_id"])
        self.assertIn("message PC_Command", errors[0].text)
        self.assertEqual(self.demo(bus="CAN1"), [])                        # COM-05: the demo uses the diagnostic CAN
        errors = self.demo(bus="CAN2")                                      # not a bus of the board
        self.assertEqual([m.yaml_path for m in errors], ["xcp.bus"])
        self.assertIn("bus CAN2 is not a CAN bus of the project (use.can: CAN0, CAN1)", errors[0].text)


if __name__ == "__main__":
    unittest.main()
