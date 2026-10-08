"""PLAT-95 (finding of TOOL-59): the requirements of a family port template and their check in checks.py.

The hand-written family port `platforms/<family>/port` (PLAT-23) is compiled into every project of its family. Its
code reads configured names no schema asks for - for s32k1xx the internal ADC channels of the safety supervision
(Ai_BANDGAP, Ai_ADCINTERNAL0) and the pins of the safe state. Until now a project without them failed in the
compiler. Since PLAT-95 the port states them in `platforms/<family>/port/port.yaml` and `checks.port_requirements`
checks them against project, board and derivative, with file, line, YAML path and what to do. A requirement that
holds only for some projects of the family does not belong there - the FlexCAN device the s32k1xx port wired by hand
until ARCH-61 was that case (ARCH-42, PLATFORM_SUPPORT_CAN); the group `can` of the format is covered here with the
invented port.

Own test profile, no eval board: the synthetic derivative TESTMCU64 (family `testfam`, ADC1 with the internal
channels VREFINT and TEMP), the synthetic board `testboard` and the synthetic project `testproject` of
`tools/nxconfig/test`, together with an invented family port `platforms/testfam/port/port.yaml` and an invented
board port `ecu/platforms/testboard/Options.cmake` in a temporary tree. The requirement is varied, not the profile:
a port that asks for something the board or the project does not have is the negative case, and every message is
checked for its file, its YAML path and what it tells the reader to do. The four s32k1xx projects of the tree are
checked once at the end as a regression guard - they are the subject there, never the yardstick for the design.

Run: .venv/Scripts/python -m unittest tools/nxconfig/test_port_requirements.py
"""

import copy
import shutil
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))

import nxconfig  # noqa: E402
from nxconfig import checks  # noqa: E402
from nxconfig import config as nxcfg  # noqa: E402
from nxconfig.config import Reporter  # noqa: E402

TEST_MCUDB = HERE / "test" / "mcudb"
TEST_BOARD = yaml.safe_load((HERE / "test/profiles/boards/testboard/board.yaml").read_text(encoding="utf-8"))
TEST_PROJECT = yaml.safe_load((HERE / "test/profiles/projects/testproject/project.yaml").read_text(encoding="utf-8"))
FAMILY = "testfam"                                  # family of TESTMCU64
BOARD = "testboard"

# The invented family port: what it needs from the synthetic profile. Every entry is met by the profile as it
# stands, so a test that changes one entry shows exactly one finding.
MANIFEST = {
    "schema_version": 1,
    "port": FAMILY,
    "requires": {
        "adc_internal": [
            {"name": "VREFINT", "channel": "VREFINT",
             "code": "platforms/testfam/port/safety/TestSupervision.cpp",
             "reason": "the reference monitor of the test port reads it every cycle"},
        ],
        "can": [
            {"name": "VEH", "instance": "FDCAN1",
             "reason": "the test port wires this controller by hand"},
        ],
        "board": [
            {"key": "adc_reference_mv", "reason": "the reference monitor compares its reading with it"},
        ],
        "project": [
            {"key": "safety", "reason": "the safe state of the test port uses the pins of this section"},
        ],
    },
}

# s32k1xx projects of the tree; private projects may be missing, they are skipped then
S32K_PROJECTS = ("buildtest_s32k148_min", "evalboard_s32k144_charge", "evalboard_s32k144_demo",
                 "s32k116evb_minimal")


def requirement(group: str, **changes) -> dict:
    """The manifest with the first entry of `group` changed (None as a value drops the key)."""
    manifest = copy.deepcopy(MANIFEST)
    entry = manifest["requires"][group][0]
    for key, value in changes.items():
        if value is None:
            entry.pop(key, None)
        else:
            entry[key] = value
    return manifest


class PortProfile(unittest.TestCase):
    """Synthetic profile plus an invented family port and board port in a temporary tree."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nx_plat95_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def tree(self, manifest=MANIFEST, board_port: bool = True, text: str | None = None) -> Path:
        """Temporary tree with platforms/<family>/port/port.yaml and, if `board_port`, the port template.

        ARCH-58: what says that a board is really compiled is the **port template** of its family (plus a board
        that lies in the `boards/` folder of the tree, which `profile()` writes) - until then it was a board port
        `ecu/platforms/<board>/Options.cmake`, which is optional since the feature switches are derived. The flag
        keeps its meaning for the tests: `False` = this profile is never built.
        """
        root = self.tmp / "tree"
        file = root / "platforms" / FAMILY / "port" / "port.yaml"
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text if text is not None else yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
        if board_port:
            (file.parent / "CMakeLists.txt").write_text(f"# family port template of {FAMILY}\n", encoding="utf-8")
        return root

    def profile(self, board=None, project=None) -> Path:
        board_dir = self.tmp / "tree" / "boards" / BOARD
        project_dir = self.tmp / "tree" / "projects" / "testproject"
        board_dir.mkdir(parents=True, exist_ok=True)
        project_dir.mkdir(parents=True, exist_ok=True)
        (board_dir / "board.yaml").write_text(
            yaml.safe_dump(TEST_BOARD if board is None else board, sort_keys=False), encoding="utf-8")
        file = project_dir / "project.yaml"
        file.write_text(yaml.safe_dump(TEST_PROJECT if project is None else project, sort_keys=False),
                        encoding="utf-8")
        return file

    def config(self, board=None, project=None):
        """The loaded profile; the profile itself must be free of errors, so only PLAT-95 findings remain."""
        config, messages = nxconfig.check(self.profile(board, project), TEST_MCUDB)
        self.assertIsNotNone(config, [str(m) for m in messages if m.severity == "error"])
        return config

    def findings(self, board=None, project=None, manifest=MANIFEST, board_port: bool = True,
                 text: str | None = None):
        """The messages of checks.port_requirements alone, against the invented tree."""
        config = self.config(board, project)
        rep = Reporter(sources=dict(config.sources))
        result = checks.port_requirements(config, rep, self.tree(manifest, board_port, text))
        return result, rep.messages

    def one(self, **kwargs):
        """Exactly one finding; returns it."""
        _, messages = self.findings(**kwargs)
        self.assertEqual(len(messages), 1, [str(m) for m in messages])
        return messages[0]

    @staticmethod
    def board():
        return copy.deepcopy(TEST_BOARD)

    @staticmethod
    def project():
        return copy.deepcopy(TEST_PROJECT)


class SatisfiedTest(PortProfile):
    def test_profile_meets_every_requirement(self):
        """The synthetic profile has all four: nothing is reported, the manifest is read."""
        result, messages = self.findings()
        self.assertEqual([str(m) for m in messages], [])
        self.assertEqual(result.port, FAMILY)
        self.assertEqual([e["name"] for e in result.entries("adc_internal")], ["VREFINT"])

    def test_a_family_without_a_port_template_is_not_built(self):
        """A family whose port template is missing is a configuration-only profile: the root CMakeLists.txt
        refuses to configure it, so a missing entry cannot fail a build and is not reported (ARCH-58)."""
        project = self.project()
        del project["adc_internal"]
        del project["safety"]
        config = self.config(project=project)
        self.assertFalse(checks.built_with_family_port(config, self.tree(board_port=False)))
        result, messages = self.findings(project=project, board_port=False)
        self.assertEqual([str(m) for m in messages], [])
        self.assertIsNotNone(result)                # the manifest itself is still read and checked

    def test_family_without_a_port_manifest(self):
        """A family whose port has no manifest (or no port at all) has nothing to check."""
        config = self.config()
        rep = Reporter(sources=dict(config.sources))
        self.assertIsNone(checks.port_requirements(config, rep, self.tmp / "empty"))
        self.assertEqual(rep.messages, [])

    def test_the_check_never_swallows_a_finding_of_another_check(self):
        """PLAT-95 runs last in checks.run and is no gate.

        The stages after the project checks are guarded by `if not rep.has_errors`, so a port requirement reported
        early would hide the finding a later stage would have made - they name different values of different
        levels, and the reader needs both. Here the project misses the required section *and* uses two board ADC
        channels on the same pin (the resource check, which is gated): both have to come out of one check.
        """
        board = self.board()
        board["adc"][1]["channel"], board["adc"][1]["pin"] = board["adc"][0]["channel"], board["adc"][0]["pin"]
        project = self.project()
        del project["safety"]
        project["use"]["adc"] = ["VBAT", "TEMP_EXT"]
        root = self.tree()
        manifest_of, built_with = checks.port_manifest_file, checks.built_with_family_port
        with unittest.mock.patch.object(checks, "port_manifest_file",
                                        lambda family, repo=None: manifest_of(family, root)), \
             unittest.mock.patch.object(checks, "built_with_family_port",
                                        lambda cfg, repo=None: built_with(cfg, root)):
            config, messages = nxconfig.check(self.profile(board, project), TEST_MCUDB)
        self.assertIsNone(config)
        errors = [m for m in messages if m.severity == "error"]
        self.assertIn("safety", [m.yaml_path for m in errors], [str(m) for m in errors])
        self.assertTrue([m for m in errors if "ADC1 IN1 is used by" in m.text], [str(m) for m in errors])

    def test_manifest_is_a_configure_input(self):
        """A changed requirement has to reach the configure step (NX_CONFIG_FILES)."""
        config = self.config()
        rep = Reporter(sources=dict(config.sources))
        root = self.tree()
        checks.port_requirements(config, rep, root)
        self.assertIn(checks.port_manifest_file(FAMILY, root), config.code_inputs)


class AdcInternalTest(PortProfile):
    def test_missing_entry_names_the_line_to_add(self):
        """No adc_internal entry of that name: the message carries the ready entry with an instance that offers
        the channel, at project.yaml adc_internal."""
        project = self.project()
        del project["adc_internal"]
        message = self.one(project=project)
        self.assertEqual((message.severity, message.file.name, message.yaml_path),
                         ("error", "project.yaml", "adc_internal"))
        self.assertIn("{ name: VREFINT, instance: ADC1, channel: VREFINT }", message.text)
        self.assertIn("TESTMCU64 offers VREFINT on ADC1", message.text)
        self.assertIn("the reference monitor of the test port reads it every cycle", message.text)
        self.assertIn("platforms/testfam/port/safety/TestSupervision.cpp", message.text)
        self.assertIn("port.yaml", message.text)

    def test_entry_of_that_name_on_the_wrong_channel(self):
        """The name is there but reads another internal channel: reported with file, line and YAML path."""
        project = self.project()
        project["adc_internal"][0]["channel"] = "TEMP"
        message = self.one(project=project)
        self.assertEqual((message.file.name, message.yaml_path), ("project.yaml", "adc_internal[0].channel"))
        self.assertIsNotNone(message.line)
        line = message.file.read_text(encoding="utf-8").splitlines()[message.line - 1]
        self.assertIn("TEMP", line, f"line {message.line} of the project does not carry the wrong channel: {line}")
        self.assertIn("needs VREFINT behind that name", message.text)
        self.assertIn("bios::AnalogInput::Ai_VREFINT", message.text)

    def test_derivative_offers_the_channel_nowhere(self):
        """No ADC of the derivative has the channel: the finding is at the derivative, not at the project - this
        derivative cannot carry this port at all."""
        message = self.one(manifest=requirement("adc_internal", name="BANDGAP", channel="BANDGAP"))
        self.assertEqual((message.file.name, message.yaml_path), ("TESTMCU64.yaml", "instances"))
        self.assertIn("has no ADC instance that offers it", message.text)
        self.assertIn("internal_channels", message.text)

    def test_second_requirement_is_checked_too(self):
        """Two internal channels, one of them missing: exactly the missing one is reported."""
        manifest = copy.deepcopy(MANIFEST)
        manifest["requires"]["adc_internal"].append({"name": "TEMP_INT", "channel": "TEMP"})
        message = self.one(manifest=manifest)
        self.assertEqual(message.yaml_path, "adc_internal")
        self.assertIn("{ name: TEMP_INT, instance: ADC1, channel: TEMP }", message.text)


class CanTest(PortProfile):
    def test_board_has_no_bus_of_that_name(self):
        message = self.one(manifest=requirement("can", name="CAN0", instance="FDCAN1"))
        self.assertEqual((message.file.name, message.yaml_path), ("board.yaml", "can"))
        self.assertIn("wires the CAN bus CAN0 by hand", message.text)
        self.assertIn("has: VEH, DIAG", message.text)

    def test_bus_on_another_controller_instance(self):
        message = self.one(manifest=requirement("can", instance="FDCAN2"))
        self.assertEqual((message.file.name, message.yaml_path), ("board.yaml", "can[0].instance"))
        self.assertIsNotNone(message.line)
        self.assertIn("holds the registers of FDCAN2", message.text)
        self.assertIn("board puts that bus on FDCAN1", message.text)

    def test_board_has_the_bus_but_the_project_does_not_use_it(self):
        project = self.project()
        project["use"]["can"] = ["VEH"]
        del project["can"]["DIAG"]
        message = self.one(project=project, manifest=requirement("can", name="DIAG", instance="FDCAN2"))
        self.assertEqual((message.file.name, message.yaml_path), ("project.yaml", "use.can"))
        self.assertIn("the project does not use it", message.text)
        self.assertIn("use.can: VEH", message.text)


class ValueTest(PortProfile):
    def test_missing_board_value(self):
        """A board value the port's code needs: the message is at board.yaml board.<key>."""
        board = self.board()
        del board["board"]["adc_reference_mv"]
        project = self.project()
        project["use"]["adc"] = []                  # otherwise the existing ADC check reports it first
        del project["adc_internal"]
        _, messages = self.findings(board=board, project=project)
        found = [m for m in messages if m.yaml_path == "board.adc_reference_mv"]
        self.assertEqual(len(found), 1, [str(m) for m in messages])
        self.assertEqual(found[0].file.name, "board.yaml")
        self.assertIn("needs board.adc_reference_mv of the board", found[0].text)
        self.assertIn("the reference monitor compares its reading with it", found[0].text)

    def test_missing_project_section(self):
        project = self.project()
        del project["safety"]
        message = self.one(project=project)
        self.assertEqual((message.file.name, message.yaml_path), ("project.yaml", "safety"))
        self.assertIn("needs safety of the project", message.text)
        self.assertIn("the safe state of the test port uses the pins of this section", message.text)

    def test_requirement_that_is_no_entry_of_the_schema(self):
        """A manifest may only require entries the schema has - otherwise it promises a check nxconfig cannot do."""
        message = self.one(manifest=requirement("project", key="no_such_section"))
        self.assertEqual((message.file.name, message.yaml_path), ("port.yaml", "requires.project"))
        self.assertIn("no entry of the project schema", message.text)


class ManifestTest(PortProfile):
    """A manifest that cannot be used is reported at its own file and YAML path, and nothing is checked with it."""

    def broken(self, manifest=MANIFEST, text: str | None = None):
        result, messages = self.findings(manifest=manifest, text=text)
        self.assertIsNone(result)
        self.assertTrue(messages, "a broken manifest must be reported")
        for message in messages:
            self.assertEqual((message.severity, message.file.name), ("error", "port.yaml"))
        return messages

    def test_wrong_schema_version(self):
        manifest = copy.deepcopy(MANIFEST)
        manifest["schema_version"] = 2
        message = self.broken(manifest)[0]
        self.assertEqual(message.yaml_path, "schema_version")
        self.assertIn("expected 1", message.text)

    def test_manifest_of_another_family(self):
        manifest = copy.deepcopy(MANIFEST)
        manifest["port"] = "s32k1xx"
        message = self.broken(manifest)[0]
        self.assertEqual(message.yaml_path, "port")
        self.assertIn("belongs to the family whose folder it is in", message.text)

    def test_unknown_requirement_group(self):
        manifest = copy.deepcopy(MANIFEST)
        manifest["requires"]["pins"] = [{"name": "PA0"}]
        message = self.broken(manifest)[0]
        self.assertEqual(message.yaml_path, "requires.pins")
        self.assertIn("unknown requirement group", message.text)

    def test_entry_without_the_keys_of_its_group(self):
        manifest = requirement("adc_internal", channel=None)
        message = self.broken(manifest)[0]
        self.assertEqual(message.yaml_path, "requires.adc_internal[0].channel")
        self.assertIn("needs `channel:`", message.text)

    def test_unknown_key_of_an_entry(self):
        manifest = requirement("adc_internal", instance="ADC1")
        message = self.broken(manifest)[0]
        self.assertEqual(message.yaml_path, "requires.adc_internal[0].instance")
        self.assertIn("unknown key", message.text)
        self.assertIn("name, channel, reason, code", message.text)

    def test_not_a_mapping(self):
        message = self.broken(text="- a\n- b\n")[0]
        self.assertIn("mapping at the top level", message.text)

    def test_invalid_yaml(self):
        message = self.broken(text="port: [unclosed\n")[0]
        self.assertIn("cannot read the port manifest", message.text)


class FamilyPortTest(unittest.TestCase):
    """The real manifest of the s32k1xx family port and the projects of the tree (regression guard)."""

    def setUp(self):
        self.file = checks.port_manifest_file("s32k1xx")

    def test_manifest_is_usable(self):
        requirements, messages = checks.read_port_manifest(self.file, "s32k1xx")
        self.assertEqual([str(m) for m in messages], [])
        self.assertIsNotNone(requirements)
        self.assertEqual([(e["name"], e["channel"]) for e in requirements.entries("adc_internal")],
                         [("BANDGAP", "BANDGAP"), ("ADCINTERNAL0", "INTERNAL0")])
        # No `can` requirement: since ARCH-61 the port demands no bus by name or instance (every bus comes from the
        # generated lists), and an application may use no CAN bus at all (ARCH-42, PLAT-96).
        self.assertEqual(requirements.entries("can"), [])
        self.assertEqual([e["key"] for e in requirements.entries("board")], ["adc_reference_mv"])
        # PLAT-96: `memory` - the application linker script of the port needs the partitioning behind the
        # bootloader; a project without it stopped at configure (memoryLayout.h missing) instead of in nxconfig
        self.assertEqual([e["key"] for e in requirements.entries("project")], ["safety", "memory"])
        for group in requirements.groups.values():
            for entry in group:
                self.assertTrue(entry.get("reason"), entry)

    def test_every_requirement_names_a_file_of_the_port(self):
        """`code:` points at the file of the port that reads the entry - a stale path would mislead the reader."""
        requirements, _ = checks.read_port_manifest(self.file, "s32k1xx")
        for group in requirements.groups.values():
            for entry in group:
                if "code" in entry:
                    self.assertTrue((REPO / entry["code"]).is_file(), entry["code"])

    def test_the_family_port_needs_the_internal_channels_its_code_reads(self):
        """The two enumerators SafeSystem.cpp reads are exactly the two required names (Ai_<name>)."""
        source = (REPO / "platforms/s32k1xx/port/safety/safeSystem/src/safeSystem/SafeSystem.cpp"
                  ).read_text(encoding="utf-8")
        requirements, _ = checks.read_port_manifest(self.file, "s32k1xx")
        for entry in requirements.entries("adc_internal"):
            self.assertIn(f"Ai_{entry['name']}", source)

    def test_projects_of_the_tree_meet_the_requirements(self):
        for name in S32K_PROJECTS:
            file = REPO / "projects" / name / "project.yaml"
            if not file.is_file():
                continue                            # private project, not in every checkout
            with self.subTest(project=name):
                config, messages = nxconfig.check(file)
                self.assertIsNotNone(config, [str(m) for m in messages if m.severity == "error"])
                self.assertIn(self.file, config.code_inputs)    # the check ran inside checks.run
                settings, _ = nxcfg.build_settings(config)
                self.assertIn(self.file.resolve().as_posix(),
                              [Path(f).resolve().as_posix() for f in settings["NX_CONFIG_FILES"]])

    def test_a_project_of_another_family_has_nothing_to_check(self):
        """stm32 has no port manifest: nothing is required, nothing is reported."""
        file = REPO / "projects" / "nucleo_f446re_demo" / "project.yaml"
        if not file.is_file():
            self.skipTest("nucleo_f446re_demo missing")
        self.assertFalse(checks.port_manifest_file("stm32").is_file())
        config, messages = nxconfig.check(file)
        self.assertIsNotNone(config, [str(m) for m in messages if m.severity == "error"])


if __name__ == "__main__":
    unittest.main()
