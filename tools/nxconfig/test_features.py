"""ARCH-58 (customer-project.md N5): the feature switches of the build, derived instead of hand-written.

Until ARCH-58 every buildable board needed `ecu/platforms/<board>/Options.cmake` with `OPENBSW_PLATFORM` and ten
`PLATFORM_SUPPORT_*` switches, and the root CMakeLists.txt refused a board without it. `tools/nxconfig/features.py`
derives them now: what the **project** uses (I/O, CAN, transport, UDS) from `project.yaml`, Ethernet from the build
option `NX_ETHERNET`, and the two switches that say whether the **port of the family** has the driver at all
(watchdog, MPU) from `ecu/platforms/<family>/features.yaml`.

Own test profile, no eval board: the synthetic derivative TESTMCU64 (family `testfam`), the synthetic board
`testboard` and the synthetic project `testproject` of `tools/nxconfig/test`, together with an invented family
statement in a temporary tree. The **project** is varied, not the tree - every switch is flipped by changing the
one entry it is derived from, so a test that changes nothing else shows exactly one changed switch. Every finding
of the family file is checked for its file and its YAML path.

The four s32k1xx projects of the tree are checked once at the end as a regression guard: the derived switches have
to be exactly what stood in their hand-written `Options.cmake` before ARCH-58 (the table below is that file
content, copied here on purpose - the guard has to break when a derivation starts to drift). They are the subject
there, never the yardstick for the design.

Run: .venv/Scripts/python -m unittest tools/nxconfig/test_features.py
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
from nxconfig import config as nxcfg  # noqa: E402
from nxconfig import features  # noqa: E402
from nxconfig.config import Reporter  # noqa: E402

TEST_MCUDB = HERE / "test" / "mcudb"
TEST_BOARD = yaml.safe_load((HERE / "test/profiles/boards/testboard/board.yaml").read_text(encoding="utf-8"))
TEST_PROJECT = yaml.safe_load((HERE / "test/profiles/projects/testproject/project.yaml").read_text(encoding="utf-8"))
FAMILY = "testfam"                                  # family of TESTMCU64

# The invented family statement: the port of testfam drives the watchdog and programs the MPU.
FAMILY_FILE = {
    "schema_version": 1,
    "family": FAMILY,
    "provides": [
        {"name": "watchdog", "reason": "the test port services the watchdog of the MCU",
         "code": "platforms/testfam/port/safety/TestWatchdog.cpp"},
        {"name": "mpu", "reason": "the test port programs the memory protection unit"},
    ],
}

# A diagnostics section the synthetic project does not have (UDS addresses of application and bootloader).
DIAGNOSTICS = {"request_id": 0x7E0, "response_id": 0x7E8}

# Regression guard: what `ecu/platforms/<board>/Options.cmake` of the tree held before ARCH-58.
TREE_BOARDS = {
    "buildtest_s32k148_min": dict(IO=True, CAN=True, ETHERNET=False, TRANSPORT=True, UDS=True,
                                  WATCHDOG=True, MPU=True),
    "evalboard_s32k144_charge": dict(IO=True, CAN=True, ETHERNET=False, TRANSPORT=True, UDS=True,
                                     WATCHDOG=True, MPU=True),
    "evalboard_s32k144_demo": dict(IO=True, CAN=True, ETHERNET=False, TRANSPORT=True, UDS=True,
                                   WATCHDOG=True, MPU=True),
    # PLAT-12: the lean profile has no application CAN (use.can empty, the bootloader keeps CAN0), hence no
    # transport and no UDS server in the application
    "s32k116evb_minimal": dict(IO=True, CAN=False, ETHERNET=False, TRANSPORT=False, UDS=False,
                               WATCHDOG=True, MPU=True),
}


class Profile(unittest.TestCase):
    """Synthetic profile plus an invented family statement in a temporary tree."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nx_arch58_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def tree(self, family_file=FAMILY_FILE, port_template: bool = True, text: str | None = None,
             family: str = FAMILY) -> Path:
        """Temporary tree with ecu/platforms/<family>/features.yaml and, if asked, a family port template."""
        root = self.tmp / "tree"
        if port_template:
            template = root / "platforms" / family / "port" / "CMakeLists.txt"
            template.parent.mkdir(parents=True, exist_ok=True)
            template.write_text("# family port template of the test family\n", encoding="utf-8")
        if family_file is not None or text is not None:
            file = root / "ecu" / "platforms" / family / "features.yaml"
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(text if text is not None else yaml.safe_dump(family_file, sort_keys=False),
                            encoding="utf-8")
        root.mkdir(parents=True, exist_ok=True)
        return root

    def profile(self, board=None, project=None) -> Path:
        board_dir, project_dir = self.tmp / "boards" / "testboard", self.tmp / "projects" / "testproject"
        board_dir.mkdir(parents=True, exist_ok=True)
        project_dir.mkdir(parents=True, exist_ok=True)
        (board_dir / "board.yaml").write_text(
            yaml.safe_dump(TEST_BOARD if board is None else board, sort_keys=False), encoding="utf-8")
        file = project_dir / "project.yaml"
        file.write_text(yaml.safe_dump(TEST_PROJECT if project is None else project, sort_keys=False),
                        encoding="utf-8")
        return file

    def config(self, board=None, project=None):
        """The loaded profile; the profile itself must be free of errors."""
        config, messages = nxconfig.check(self.profile(board, project), TEST_MCUDB)
        self.assertIsNotNone(config, [str(m) for m in messages if m.severity == "error"])
        return config

    def derive(self, board=None, project=None, **tree):
        """The derived switches and the findings of the family statement alone."""
        config = self.config(board, project)
        rep = Reporter(sources=dict(config.sources))
        return features.derive(config, rep, self.tree(**tree)), rep.messages

    def values(self, board=None, project=None, **tree) -> dict:
        switches, messages = self.derive(board, project, **tree)
        self.assertEqual([str(m) for m in messages], [])
        return {name: s.value for name, s in switches.items()}

    @staticmethod
    def project():
        return copy.deepcopy(TEST_PROJECT)

    @staticmethod
    def board():
        return copy.deepcopy(TEST_BOARD)


class DerivationTest(Profile):
    def test_the_synthetic_profile(self):
        """The profile as it stands: I/O and CAN from what it uses, no diagnostics, the port's two switches on."""
        self.assertEqual(self.values(),
                         dict(IO=True, CAN=True, ETHERNET=False, TRANSPORT=False, UDS=False,
                              WATCHDOG=True, MPU=True))

    def test_every_switch_names_its_level_and_its_reason(self):
        """A switch without a sentence that says where it comes from is useless in `cmake -LAH` and in the Studio."""
        switches, _ = self.derive()
        self.assertEqual(sorted(switches), sorted(name for name, _ in features.FEATURES))
        for name, switch in switches.items():
            self.assertIn(switch.level, features.LEVELS, name)
            self.assertTrue(len(switch.why) > 20, f"{name}: {switch.why!r}")
            self.assertEqual(switch.cmake, "ON" if switch.value else "OFF", name)

    def test_can_follows_use_can(self):
        """ARCH-42: a project may legitimately use no CAN bus - the switch follows `use.can`, nothing else."""
        project = self.project()
        project["use"]["can"] = []
        del project["can"]
        values = self.values(project=project)
        self.assertFalse(values["CAN"])
        self.assertTrue(values["IO"], "only CAN may change")
        switches, _ = self.derive(project=project)
        self.assertIn("use.can", switches["CAN"].why)

    def test_can_names_the_buses_it_found(self):
        switches, _ = self.derive()
        self.assertIn("VEH", switches["CAN"].why)
        self.assertIn("DIAG", switches["CAN"].why)

    def test_transport_and_uds_follow_the_diagnostics_section(self):
        """The transport layer carries the diagnostic stack: both switches hang on one section of the project."""
        project = self.project()
        project["diagnostics"] = copy.deepcopy(DIAGNOSTICS)
        values = self.values(project=project)
        self.assertTrue(values["TRANSPORT"])
        self.assertTrue(values["UDS"])
        switches, _ = self.derive(project=project)
        self.assertIn("diagnostics", switches["UDS"].why)
        self.assertIn("diagnostics", switches["TRANSPORT"].why)

    def test_diagnostics_without_a_can_bus_of_the_application(self):
        """PLAT-96: diagnostics without a CAN bus in the application serve the bootloader only - no transport, no
        UDS server (both on made the OpenBSW transport router ask for busid::CAN_0, a compiler error)."""
        project = self.project()
        project["diagnostics"] = copy.deepcopy(DIAGNOSTICS)
        project["use"]["can"] = []
        del project["can"]
        values = self.values(project=project)
        self.assertFalse(values["CAN"])
        self.assertFalse(values["TRANSPORT"])
        self.assertFalse(values["UDS"])
        switches, _ = self.derive(project=project)
        for name in ("TRANSPORT", "UDS"):
            self.assertIn("uses no CAN bus", switches[name].why, name)
            self.assertIn("bootloader", switches[name].why, name)

    def test_io_off_when_the_project_uses_no_io_entry_of_its_board(self):
        """A project that uses no digital I/O, no ADC, no capture channel and no PWM group needs no I/O layer."""
        project = self.project()
        project["use"] = {"can": ["VEH", "DIAG"], "uart": "all"}
        for key in ("pwm", "adc_internal", "safety"):
            project.pop(key, None)
        values = self.values(project=project)
        self.assertFalse(values["IO"])
        self.assertTrue(values["CAN"], "only IO may change")

    def test_io_on_for_a_project_that_only_reads_an_internal_adc_channel(self):
        """No board entry, but the safety supervision of a port reads the bandgap: that is an ADC use as well."""
        project = self.project()
        project["use"] = {"can": ["VEH", "DIAG"], "uart": "all"}
        project.pop("pwm", None)
        project.pop("safety", None)
        values = self.values(project=project)
        self.assertTrue(values["IO"])
        switches, _ = self.derive(project=project)
        self.assertIn("adc_internal", switches["IO"].why)

    def test_io_on_for_a_project_that_only_uses_a_pwm_group(self):
        project = self.project()
        project["use"] = {"can": ["VEH", "DIAG"], "uart": "all"}
        project.pop("adc_internal", None)
        project.pop("safety", None)
        self.assertTrue(self.values(project=project)["IO"])

    def test_ethernet_is_never_a_board_value(self):
        """PLAT-56: Ethernet is the build option NX_ETHERNET, checked against the derivative - not a board switch."""
        switches, _ = self.derive()
        self.assertFalse(switches["ETHERNET"].value)
        self.assertEqual(switches["ETHERNET"].level, "option")
        self.assertIn("NX_ETHERNET", switches["ETHERNET"].why)

    def test_the_port_switches_come_from_the_family_statement(self):
        """Take `mpu` out of the family file and only PLATFORM_SUPPORT_MPU goes off."""
        family_file = copy.deepcopy(FAMILY_FILE)
        family_file["provides"] = [e for e in family_file["provides"] if e["name"] != "mpu"]
        values = self.values(family_file=family_file)
        self.assertFalse(values["MPU"])
        self.assertTrue(values["WATCHDOG"], "only MPU may change")

    def test_the_port_switch_quotes_the_reason_of_the_family(self):
        switches, _ = self.derive()
        self.assertIn("services the watchdog", switches["WATCHDOG"].why)
        self.assertIn("TestWatchdog.cpp", switches["WATCHDOG"].why)
        self.assertIn("features.yaml", switches["WATCHDOG"].why)

    def test_the_port_switch_says_where_the_statement_is_missing(self):
        """Off because nobody said otherwise - the message has to name the file to write."""
        switches, _ = self.derive(family_file=None, port_template=False)
        self.assertFalse(switches["MPU"].value)
        self.assertIn(f"ecu/platforms/{FAMILY}/features.yaml", switches["MPU"].why)

    def test_settings_carry_value_and_reason(self):
        """What reaches project.cmake: NX_FEATURE_<NAME> and NX_FEATURE_<NAME>_WHY for every switch."""
        config = self.config()
        out = features.settings(config, None, self.tree())
        for name, _ in features.FEATURES:
            self.assertIn(f"NX_FEATURE_{name}", out)
            self.assertIn(out[f"NX_FEATURE_{name}"], ("ON", "OFF"))
            self.assertTrue(out[f"NX_FEATURE_{name}_WHY"].split(":")[0] in features.LEVELS)

    def test_the_family_file_is_a_configure_input(self):
        """A changed statement has to reach the configure step (NX_CONFIG_FILES)."""
        config = self.config()
        root = self.tree()
        features.derive(config, None, root)
        self.assertIn(features.family_features_file(FAMILY, root), config.code_inputs)


class FamilyFileTest(Profile):
    """Every finding of the family statement, with its file and its YAML path."""

    def one(self, **tree):
        _, messages = self.derive(**tree)
        self.assertEqual(len(messages), 1, [str(m) for m in messages])
        return messages[0]

    def test_a_family_that_is_built_but_says_nothing(self):
        """Port template, no features file: a warning that names the file, and the two switches stay off."""
        switches, messages = self.derive(family_file=None)
        self.assertEqual(len(messages), 1, [str(m) for m in messages])
        self.assertEqual(messages[0].severity, "warning")
        self.assertIn(f"ecu/platforms/{FAMILY}/features.yaml", messages[0].text)
        self.assertFalse(switches["WATCHDOG"].value)
        self.assertFalse(switches["MPU"].value)

    def test_a_family_that_is_not_built_says_nothing(self):
        """No port template: nothing is compiled from the family, so a missing statement is no finding."""
        switches, messages = self.derive(family_file=None, port_template=False)
        self.assertEqual([str(m) for m in messages], [])
        self.assertFalse(switches["WATCHDOG"].value)

    def test_unknown_feature_name(self):
        family_file = copy.deepcopy(FAMILY_FILE)
        family_file["provides"][1]["name"] = "can"
        message = self.one(family_file=family_file)
        self.assertEqual(message.severity, "error")
        self.assertEqual(message.yaml_path, "provides[1].name")
        self.assertIn("watchdog", message.text)
        self.assertIn("features.py", message.text)

    def test_wrong_schema_version(self):
        family_file = copy.deepcopy(FAMILY_FILE)
        family_file["schema_version"] = 2
        message = self.one(family_file=family_file)
        self.assertEqual(message.yaml_path, "schema_version")

    def test_the_file_of_another_family(self):
        family_file = copy.deepcopy(FAMILY_FILE)
        family_file["family"] = "s32k1xx"
        message = self.one(family_file=family_file)
        self.assertEqual(message.yaml_path, "family")
        self.assertIn("folder", message.text)

    def test_provides_is_not_a_list(self):
        family_file = copy.deepcopy(FAMILY_FILE)
        family_file["provides"] = {"watchdog": True}
        message = self.one(family_file=family_file)
        self.assertEqual(message.yaml_path, "provides")

    def test_an_entry_that_is_not_a_mapping(self):
        family_file = copy.deepcopy(FAMILY_FILE)
        family_file["provides"][0] = "watchdog"
        message = self.one(family_file=family_file)
        self.assertEqual(message.yaml_path, "provides[0]")

    def test_the_same_feature_twice(self):
        family_file = copy.deepcopy(FAMILY_FILE)
        family_file["provides"].append({"name": "mpu", "reason": "again"})
        message = self.one(family_file=family_file)
        self.assertEqual(message.yaml_path, "provides[2].name")
        self.assertIn("twice", message.text)

    def test_an_unknown_key(self):
        family_file = copy.deepcopy(FAMILY_FILE)
        family_file["provides"][0]["value"] = "ON"
        message = self.one(family_file=family_file)
        self.assertEqual(message.yaml_path, "provides[0].value")

    def test_broken_yaml(self):
        message = self.one(text="schema_version: 1\nprovides: [\n")
        self.assertEqual(message.severity, "error")
        self.assertEqual(message.file.name, "features.yaml")

    def test_not_a_mapping_at_the_top(self):
        message = self.one(text="- watchdog\n")
        self.assertEqual(message.severity, "error")

    def test_a_broken_file_switches_nothing_on(self):
        """An unusable statement must not silently keep the switches of the last good version."""
        family_file = copy.deepcopy(FAMILY_FILE)
        family_file["schema_version"] = 2
        switches, _ = self.derive(family_file=family_file)
        self.assertFalse(switches["WATCHDOG"].value)
        self.assertFalse(switches["MPU"].value)


class TreeProjectsTest(unittest.TestCase):
    """Regression guard: the projects of the tree get exactly the switches their Options.cmake held before."""

    def test_every_project_of_the_tree(self):
        for name, expected in TREE_BOARDS.items():
            file = REPO / "projects" / name / "project.yaml"
            if not file.is_file():
                continue                            # private project, not in this checkout
            with self.subTest(project=name):
                config, messages = nxconfig.check(file)
                self.assertIsNotNone(config, [str(m) for m in messages if m.severity == "error"])
                got = {n: s.value for n, s in features.derive(config).items()}
                self.assertEqual(got, expected)

    def test_the_families_of_the_tree_state_what_their_port_provides(self):
        """Every family with a port template has a features file, and it is readable."""
        for template in sorted((REPO / "platforms").glob("*/port/CMakeLists.txt")):
            family = template.parent.parent.name
            with self.subTest(family=family):
                statement, messages = features.family_features(family)
                self.assertEqual([str(m) for m in messages], [])
                self.assertIsNotNone(statement, f"family {family} has no readable features.yaml")

    def test_no_board_port_of_the_tree_sets_a_feature_switch_any_more(self):
        """ARCH-58: the board port is the place for a justified exception - the tree has none today."""
        for options in sorted((REPO / "ecu" / "platforms").glob("*/Options.cmake")):
            with self.subTest(board=options.parent.name):
                code = [line.strip() for line in options.read_text(encoding="utf-8").splitlines()
                        if line.strip() and not line.strip().startswith("#")]
                self.assertEqual(code, [], f"{options} sets something again - is it a justified exception?")


if __name__ == "__main__":
    unittest.main()
