"""SYS-95 (finding DFA-4 (a) of doc/safety/dfa.md): tools/nxconfig/pwmgate.py - a used PWM group whose power stage no
safe_off output on another peripheral de-energises is a warning with the YAML path of the group.

Profiles: tools/bswgen/test/profiles (testboard_active_short/testproject_active_short: a PWM group whose gate driver
GD_A has the safe_off enable GD_A_DRV_EN) copied into a temporary tree and varied per test; testproject_pwm_center
(a group without a gate driver) as it is in the tree.

Run: .venv/Scripts/python -m unittest tools/nxconfig/test_pwmgate.py
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ruamel.yaml import YAML

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))

import nxconfig  # noqa: E402
from nxconfig import pwmgate  # noqa: E402
from nxconfig.config import Reporter  # noqa: E402

PROFILES = REPO / "tools" / "bswgen" / "test" / "profiles"
CENTER_PROJECT = PROFILES / "projects" / "testproject_pwm_center" / "project.yaml"


def warnings_of(messages, path):
    return [m.text for m in messages if m.severity == "warning" and m.yaml_path == path and "DFA-4" in m.text]


class PwmGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for kind, name in (("boards", "testboard_active_short"), ("boards", "testboard_safe_outputs"),
                           ("projects", "testproject_active_short")):
            shutil.copytree(PROFILES / kind / name, self.tmp / kind / name)
        self.project = self.tmp / "projects" / "testproject_active_short" / "project.yaml"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def edit(self, rel: str, change):
        path = self.tmp / rel
        yaml = YAML()
        data = yaml.load(path.read_text(encoding="utf-8"))
        change(data)
        with path.open("w", encoding="utf-8") as f:
            yaml.dump(data, f)

    def test_bound_enable_no_warning(self):
        config, messages = nxconfig.check(self.project)
        self.assertIsNotNone(config, [str(m) for m in messages if m.severity == "error"])
        self.assertEqual(warnings_of(messages, "pwm.BRIDGE"), [])
        self.assertEqual(pwmgate.unprotected_groups(config), [])

    def test_group_without_gate_driver(self):
        def unbind(data):
            data["pwm"][0].pop("gate_driver", None)
        self.edit("boards/testboard_active_short/board.yaml", unbind)
        config, messages = nxconfig.check(self.project)
        self.assertIsNotNone(config, [str(m) for m in messages if m.severity == "error"])
        found = warnings_of(messages, "pwm.BRIDGE")
        self.assertEqual(len(found), 1, found)
        self.assertIn("names no gate driver", found[0])
        self.assertIn("AoU-07", found[0])
        self.assertEqual(pwmgate.unprotected_groups(config), ["BRIDGE"])
        # a warning, not an error: the project stays usable
        self.assertFalse(any(m.severity == "error" and m.yaml_path == "pwm.BRIDGE" for m in messages))

    def test_gate_driver_without_safe_off_output(self):
        # the gate driver stays bound, but no safe_off entry of the table is bound to the group (as when its module
        # declares no safe_off connection): the same binding rule the table and the walk follow
        config, _ = nxconfig.check(self.project)
        self.assertIsNotNone(config)
        from nxconfig import degraded
        self.assertTrue([e for e in degraded.safe_off_entries(config) if "BRIDGE" in e.pwm],
                        "profile changed: GD_A_DRV_EN is no longer bound to BRIDGE")
        saved = degraded.safe_off_entries
        with mock.patch.object(degraded, "safe_off_entries",
                               lambda cfg: [e for e in saved(cfg) if "BRIDGE" not in e.pwm]):
            rep = Reporter()
            self.assertEqual(pwmgate.check(config, rep), ["BRIDGE"])
        texts = [m.text for m in rep.messages if m.severity == "warning" and m.yaml_path == "pwm.BRIDGE"]
        self.assertEqual(len(texts), 1, texts)
        self.assertIn("its gate driver GD_A has no safe_off output in the project", texts[0])

    def test_profile_without_gate_driver_in_the_tree(self):
        config, messages = nxconfig.check(CENTER_PROJECT)
        self.assertIsNotNone(config, [str(m) for m in messages if m.severity == "error"])
        self.assertEqual(len(warnings_of(messages, "pwm.INVERTER")), 1)

    def test_project_without_pwm(self):
        project = PROFILES / "projects" / "testproject_safe_outputs" / "project.yaml"
        config, messages = nxconfig.check(project)
        self.assertIsNotNone(config)
        if not config.selected_pwm():
            self.assertFalse(any("DFA-4" in m.text for m in messages))
        self.assertEqual(pwmgate.check(type("Cfg", (), {"integration": None})(), Reporter()), [])


if __name__ == "__main__":
    unittest.main()
