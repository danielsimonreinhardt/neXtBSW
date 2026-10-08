# SPDX-License-Identifier: Apache-2.0
"""NVM-14: bswgen refuses a project that would give a key WRITE_PROTECTION, with the YAML path of nxconfig.

Profiles: the build test project buildtest_s32k148_min (S32K148, key space added here) and the eval board demo
(S32K144, 20 slots). Counter-check: the same project with flags other than WRITE_PROTECTION generates.

Run: .venv/Scripts/python -m unittest tools/bswgen/test_bswgen_security.py
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import bswgen  # noqa: E402

# PLAT-62: the own full-featured test profile (synthetic testboard_full/testproject_full) instead of the eval board
# project, which stays private (PLAT-46 F10) - a test against it would be red in the exported open tree. What
# proves something about the eval board itself uses EVAL_DEMO and is skipped where it is missing.
DEMO = REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full" / "project.yaml"
EVAL_DEMO = REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml"
K148 = REPO / "projects" / "buildtest_s32k148_min" / "project.yaml"
K148_PARTITION = {"partition": {"eeprom_bytes": 4096, "data_flash_bytes": 0x70000, "key_slots": 20}}


def copy_with(tmp: Path, base: Path, security: dict, nvm: dict | None = None) -> Path:
    project = yaml.safe_load(base.read_text(encoding="utf-8"))
    info = project["project"]
    for key in ("board", "rte", "diag"):
        if key in info:
            info[key] = str((base.parent / info[key]).resolve())
    if nvm is not None:
        project["nvm"] = nvm
    project["security"] = security
    path = tmp / "project.yaml"
    path.write_text(yaml.safe_dump(project, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


class WriteProtectionRefusedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def generate(self, project: Path):
        bswgen.generate(project, self.tmp / "out")

    def test_profiles(self):
        for base, nvm in ((K148, K148_PARTITION), (DEMO, None)):
            with self.subTest(project=base.parent.name):
                ok = copy_with(self.tmp, base, {"keys": {"KEY_1": {"flags": ["key_usage", "boot_protection"]}}}, nvm)
                self.generate(ok)                                   # counter-check: generates
                self.assertTrue((self.tmp / "out" / "nvm" / "nvmPartition.h").is_file())
                shutil.rmtree(self.tmp / "out")
                bad = copy_with(self.tmp, base, {"keys": {"KEY_1": {"flags": ["key_usage", "WRITE_PROTECTION"]}}},
                                nvm)
                with self.assertRaises(bswgen.ConfigError) as e:
                    self.generate(bad)
                self.assertIn("security.keys.KEY_1.flags", str(e.exception))
                self.assertIn("WRITE_PROTECTION is never allowed", str(e.exception))
                self.assertFalse((self.tmp / "out" / "nvm" / "nvmPartition.h").exists())


if __name__ == "__main__":
    unittest.main()
