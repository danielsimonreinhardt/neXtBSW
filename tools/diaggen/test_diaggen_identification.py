"""COM-44: identification values of the diagnostic stack in tools/diaggen - hardware.number (F191), nextbsw.version
(F195) and compatibility.* (the record of F1A0) - and tools/diaggen/buildid.py, the neXtBSW version and commit of
a build.

Own profile ecu/uds/test/host/identification_profile/diag.yaml (the one the host test serves), changed copies for
every check; the answers themselves are compared with the bootloader by ecu/uds/test/host/
test_app_uds_identification.py.

Run: .venv/Scripts/python -m unittest tools/diaggen/test_diaggen_identification.py
"""

import copy
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "tools" / "bswgen"))

import buildid  # noqa: E402
import diaggen  # noqa: E402

PROFILE = REPO / "ecu" / "uds" / "test" / "host" / "identification_profile" / "diag.yaml"
PROFILE_DATA = yaml.safe_load(PROFILE.read_text(encoding="utf-8"))
# PLAT-62: the own full-featured test profile (synthetic testboard_full/testproject_full) instead of the eval board
# project, which stays private (PLAT-46 F10) - a test against it would be red in the exported open tree. What
# proves something about the eval board itself uses EVAL_DEMO and is skipped where it is missing.
DEMO = REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full" / "project.yaml"
EVAL_DEMO = REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml"
COMPATIBILITY = ("compatibility.version", "compatibility.target", "compatibility.layout", "compatibility.app_start",
                 "compatibility.app_end", "compatibility.record_address", "compatibility.state")


class StackValueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def load(self, data: dict) -> diaggen.Description:
        path = self.tmp / "diag.yaml"
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        return diaggen.load_file(path)

    def changed(self, change) -> dict:
        data = copy.deepcopy(PROFILE_DATA)
        change(data)
        return data

    def findings(self, d: diaggen.Description) -> list[tuple[str, tuple, str]]:
        diaggen.check(d)
        return [(m.severity, m.path, m.text) for m in d.messages]

    def test_profile_is_accepted_and_generated(self):
        d = self.load(PROFILE_DATA)
        self.assertEqual(self.findings(d), [])
        cpp = diaggen.plan(self.load(PROFILE_DATA)).source_cpp
        self.assertIn("values.hardwareNumber", cpp)
        self.assertIn("values.nextbswVersion", cpp)
        for member in ("version", "target", "layout", "appStart", "appEnd", "recordAddress", "state"):
            self.assertIn(f"values.compatibility.{member}", cpp)
        # F1A0 of the bootloader: 1 + 5 * 4 + 1 bytes; the texts fill their length (COM-33)
        self.assertIn("{0xFD10U, 22U, 0U,", cpp)
        self.assertIn("{0xF191U, 32U, 0U,", cpp)
        self.assertIn("{0xF195U, 48U, 0U,", cpp)
        self.assertIn("values.configurationIdentity", cpp)           # PLAT-79
        self.assertIn("{0xF1F1U, 64U, 0U,", cpp)

    def test_every_value_is_known_and_read_only(self):
        for name in ("hardware.number", "nextbsw.version", "configuration.identity", *COMPATIBILITY):   # PLAT-79
            with self.subTest(name=name):
                spec = diaggen.STACK_VALUES[name]
                self.assertFalse(spec["write"])
                self.assertFalse(name.split(".")[0] in ("ecu", "adc", "bsw"))   # values of the stack, not the RTE

    def test_texts_are_ascii(self):
        d = self.load(self.changed(lambda x: x["data_identifiers"][0]["data"][0].update(type="uint8",
                                                                                         length_bytes=None)))
        found = self.findings(d)
        self.assertTrue(any(p == ("data_identifiers", 0, "data", 0, "type") and "hardware.number" in t
                            for _, p, t in found), found)

    def test_values_are_not_writable(self):
        def change(data):
            data["data_identifiers"][2].update(write=True)
        found = self.findings(self.load(self.changed(change)))
        self.assertTrue(any(p == ("data_identifiers", 2, "write") and "compatibility.version" in t
                            for _, p, t in found), found)

    def test_compatibility_fields_are_integers(self):
        def change(data):
            data["data_identifiers"][2]["data"][1].update(type="ascii", length_bytes=4)
        found = self.findings(self.load(self.changed(change)))
        self.assertTrue(any(p == ("data_identifiers", 2, "data", 1, "type") for _, p, t in found), found)

    def test_hardware_number_longer_than_the_element(self):
        """The hardware number is an identity: a cut text is a warning with the YAML path (not silent)."""
        d = self.load(self.changed(lambda x: x["data_identifiers"][0]["data"][0].update(length_bytes=12)))
        d.hardware_number = "evalboard_s32k144/S32K144"
        found = self.findings(d)
        self.assertEqual([(s, p) for s, p, _ in found], [("warning", ("data_identifiers", 0, "data", 0, "length_bytes"))])
        self.assertIn("25 characters", found[0][2])
        # it still generates (a warning, not an error)
        d = self.load(self.changed(lambda x: x["data_identifiers"][0]["data"][0].update(length_bytes=12)))
        d.hardware_number = "evalboard_s32k144/S32K144"
        diaggen.plan(d)

    def test_hardware_number_of_a_project(self):
        """Derived from board and derivative of the project, the text of BL_HARDWARE_ID (tools/bswgen blgen)."""
        import blgen  # noqa: E402

        d = diaggen.load_project(DEMO)
        config = diaggen.nxconfig.load(DEMO)
        self.assertEqual(d.hardware_number,
                         blgen.hardware_id(config.board.board.name, config.derivative.derivative.name))


class BuildIdTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_text_of_this_tree(self):
        text = buildid.text_of(REPO)
        version = (REPO / "VERSION").read_text(encoding="utf-8").strip()
        self.assertTrue(text.startswith(version + " "), text)
        self.assertLessEqual(len(text), buildid.MAX_LENGTH)
        self.assertTrue(all(0x20 <= ord(c) < 0x7F and c not in '"\\' for c in text))

    def test_tree_without_git(self):
        tree = self.tmp / "tree"
        tree.mkdir()
        (tree / "VERSION").write_text("1.2.3\n", encoding="utf-8")
        self.assertEqual(buildid.text_of(tree), "1.2.3 nogit")
        (tree / "VERSION").write_text("not a version\n", encoding="utf-8")
        self.assertEqual(buildid.text_of(tree), "? nogit")

    def test_header_is_written_only_on_a_change(self):
        out = self.tmp / "diag" / "DiagBuildId.h"
        self.assertTrue(buildid.write(out, "0.1.0 abc"))
        self.assertIn('#define NX_DIAG_NEXTBSW_VERSION "0.1.0 abc"', out.read_text(encoding="utf-8"))
        self.assertFalse(buildid.write(out, "0.1.0 abc"))
        self.assertTrue(buildid.write(out, "0.1.0 abd"))

    def test_command_line(self):
        out = self.tmp / "DiagBuildId.h"
        result = subprocess.run([sys.executable, str(HERE / "buildid.py"), "-o", str(out), "--tree", str(REPO)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("NX_DIAG_NEXTBSW_VERSION", out.read_text(encoding="utf-8"))

    def test_the_build_runs_it(self):
        cmake = (REPO / "ecu" / "uds" / "CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn("tools/diaggen/buildid.py", cmake)
        self.assertIn("add_dependencies(appUds diagBuildId)", cmake)
        source = (REPO / "ecu" / "uds" / "src" / "systems" / "DiagSystem.cpp").read_text(encoding="utf-8")
        self.assertIn("NX_DIAG_NEXTBSW_VERSION", source)
        self.assertIn("BL_HARDWARE_ID", source)


if __name__ == "__main__":
    unittest.main()
