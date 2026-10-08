"""COM-43: tools/diaggen for the product build (NX_PRODUCT_BUILD, SYS-74) - CommunicationControl (28) and
ControlDTCSetting (85) need security access there (decision 2026-09-29, TOOL-131), during development the extended
session suffices.

Own test profile test/profile/diag.yaml (two levels: Workshop in the session Service = 0x03, Factory without a list,
i.e. every session but the default one), plus changed copies; the demo and buildtest_s32k148_min for validate().
The C++ side (NRC 0x33, unlock, lock after a session change) is ecu/uds/test/host/test_app_uds_product.py.

Run: .venv/Scripts/python -m unittest tools/diaggen/test_diaggen_product.py
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

import diaggen  # noqa: E402

PROFILE = HERE / "test" / "profile" / "diag.yaml"
PROFILE_DATA = yaml.safe_load(PROFILE.read_text(encoding="utf-8"))
# PLAT-62: the own full-featured test profile (synthetic testboard_full/testproject_full) instead of the eval board
# project, which stays private (PLAT-46 F10) - a test against it would be red in the exported open tree. What
# proves something about the eval board itself uses EVAL_DEMO and is skipped where it is missing.
DEMO = REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full" / "project.yaml"
EVAL_DEMO = REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml"
MIN = REPO / "projects" / "buildtest_s32k148_min" / "project.yaml"


class ProductBuildTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def load(self, data: dict, product: bool) -> diaggen.Description:
        path = self.tmp / "diag.yaml"
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        d = diaggen.load_file(path)
        d.product = product
        return d

    def changed(self, change) -> dict:
        data = copy.deepcopy(PROFILE_DATA)
        change(data)
        return data

    def errors(self, d: diaggen.Description) -> list[tuple[tuple, str]]:
        diaggen.check(d)
        return [(m.path, m.text) for m in d.messages if m.severity == "error"]

    def test_development_build_needs_no_security(self):
        d = self.load(PROFILE_DATA, product=False)
        self.assertEqual(diaggen.preprogramming_security_mask(d), 0)
        header = diaggen.plan(d).preprogramming_h
        self.assertIn("#define NX_DIAG_PRODUCT_BUILD 0", header)
        self.assertIn("DIAG_PREPROGRAMMING_SECURITY = 0x00U; // no security access (development build)", header)
        # a description without any level is fine for development
        self.assertEqual(self.errors(self.load(self.changed(lambda x: x.pop("security")), product=False)), [])

    def test_product_build_takes_every_level_of_the_extended_session(self):
        d = self.load(PROFILE_DATA, product=True)
        # Workshop (bit 0): session Service = 0x03; Factory (bit 1): no list = every session but the default one
        self.assertEqual(diaggen.preprogramming_security_mask(d), 0x03)
        header = diaggen.plan(d).preprogramming_h
        self.assertIn("#define NX_DIAG_PRODUCT_BUILD 1", header)
        self.assertIn("DIAG_PREPROGRAMMING_SECURITY = 0x03U; // Workshop, Factory", header)

    def test_level_of_the_default_session_only_does_not_count(self):
        """Only the extended session serves 28/85: a level listed for the default session alone opens nothing."""
        def change(data):
            data["security"] = [dict(data["security"][0], sessions=["Base"])]
        d = self.load(self.changed(change), product=True)
        self.assertEqual(diaggen.preprogramming_security_mask(d), 0)
        found = self.errors(d)
        self.assertTrue(any(p == ("security",) and "NX_PRODUCT_BUILD" in t for p, t in found), found)

    def test_product_build_without_a_level_is_refused(self):
        d = self.load(self.changed(lambda x: x.pop("security")), product=True)
        found = self.errors(d)
        self.assertEqual(len(found), 1, found)
        self.assertEqual(found[0][0], ("security",))
        self.assertIn("28", found[0][1])
        self.assertIn("85", found[0][1])
        with self.assertRaises(diaggen.DiagGenError):
            diaggen.plan(self.load(self.changed(lambda x: x.pop("security")), product=True))

    def test_validate_of_the_projects(self):
        """The demo has the level Application in its extended session; a project without a description (the
        default one has no level) cannot be built as a product."""
        self.assertEqual([m for m in diaggen.validate(DEMO, product=True) if m.severity == "error"], [])
        self.assertEqual([m for m in diaggen.validate(DEMO) if m.severity == "error"], [])
        product = [m for m in diaggen.validate(MIN, product=True) if m.severity == "error"]
        development = [m for m in diaggen.validate(MIN) if m.severity == "error"]
        self.assertEqual(development, [])
        if not any(True for _ in diaggen.load_project(MIN).desc.security):
            self.assertTrue(any(tuple(m.path) == ("security",) for m in product), product)

    def test_command_line(self):
        out = self.tmp / "out"
        for flag, expected in (([], "#define NX_DIAG_PRODUCT_BUILD 0"), (["--product"], "#define NX_DIAG_PRODUCT_BUILD 1")):
            result = subprocess.run([sys.executable, str(HERE / "diaggen.py"), str(DEMO), str(out), *flag],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(expected, (out / "diag" / "DiagPreprogramming.h").read_text(encoding="utf-8"))
        path = self.tmp / "nosec.yaml"
        path.write_text(yaml.safe_dump(self.changed(lambda x: x.pop("security")), sort_keys=False), encoding="utf-8")
        result = subprocess.run([sys.executable, str(HERE / "diaggen.py"), str(DEMO), str(out), "--diag", str(path),
                                 "--product"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("NX_PRODUCT_BUILD", result.stderr)


if __name__ == "__main__":
    unittest.main()
