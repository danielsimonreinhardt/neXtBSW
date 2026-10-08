"""SYS-116: tools/nxconfig/safestate.py - the safe states of a PWM group (doc/safety/partner-and-degraded.md part C):
every rule of the check table of section 5.11 with its YAML path, the two code facts that gate the active short (read
from the tree, refused while missing), and projects without the key unchanged (freewheel only, nothing derived).

Profiles: tools/bswgen/test/profiles (testboard_active_short/testproject_active_short, testboard_safe_outputs) copied
into a temporary tree and varied per test; the eval board demo.

Run: .venv/Scripts/python -m unittest tools/nxconfig/test_safestate.py
"""

import re
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
from nxconfig import safestate  # noqa: E402

PROFILES = REPO / "tools" / "bswgen" / "test" / "profiles"
# PLAT-62: the own full-featured test profile (synthetic testboard_full/testproject_full) instead of the eval board
# project, which stays private (PLAT-46 F10) - a test against it would be red in the exported open tree. What
# proves something about the eval board itself uses EVAL_DEMO_PROJECT and is skipped where it is missing.
DEMO_PROJECT = REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full" / "project.yaml"
EVAL_DEMO_PROJECT = REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml"
WHERE = "pwm.BRIDGE.safe_states"
FACTS_PRESENT = safestate.CodeFacts(())


def load(path: Path):
    yaml = YAML()
    return yaml, yaml.load(path.read_text(encoding="utf-8"))


class SafeStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for kind, name in (("boards", "testboard_active_short"), ("boards", "testboard_safe_outputs"),
                           ("projects", "testproject_active_short")):
            shutil.copytree(PROFILES / kind / name, self.tmp / kind / name)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def edit(self, rel: str, change):
        path = self.tmp / rel
        yaml, data = load(path)
        change(data)
        with path.open("w", encoding="utf-8") as f:
            yaml.dump(data, f)

    def pwm(self, **values):
        def change(data):
            for key, value in values.items():
                if value is None:
                    data["pwm"]["BRIDGE"].pop(key, None)
                else:
                    data["pwm"]["BRIDGE"][key] = value
        self.edit("projects/testproject_active_short/project.yaml", change)

    def board(self, name="testboard_active_short", **values):
        def change(data):
            for key, value in values.items():
                if value is None:
                    data["pwm"][0].pop(key, None)
                else:
                    data["pwm"][0][key] = value
        self.edit(f"boards/{name}/board.yaml", change)

    def check(self, facts=FACTS_PRESENT):
        project = self.tmp / "projects" / "testproject_active_short" / "project.yaml"
        if facts is None:
            return nxconfig.check(project)
        with mock.patch.object(safestate, "read_facts", lambda repo, family: facts):
            return nxconfig.check(project)

    @staticmethod
    def of(messages, severity):
        return [(m.yaml_path, m.text) for m in messages if m.severity == severity]

    def assertFinding(self, messages, severity, path, text):
        found = self.of(messages, severity)
        self.assertTrue(any(p == path and text in t for p, t in found), f"no {severity} {path!r} ~ {text!r}: {found}")

    def test_valid_profile(self):
        config, messages = self.check()
        self.assertEqual(self.of(messages, "error"), [])
        plan = config.safe_states
        self.assertTrue(plan.select)
        group = plan.groups[0]
        self.assertEqual((group.name, group.allowed, group.allowed_mask, group.test, group.test_us),
                         ("BRIDGE", ("freewheel", "active_short_low", "active_short_high"), 0x7, True, 500))
        self.assertEqual(group.bound_outputs, ("GD_A_DRV_EN",))
        # board states freewheel as its reset state: the concept's warning (C3), nothing else from safestate
        self.assertFinding(messages, "warning", WHERE, "reset state is freewheel")
        self.assertEqual([p for p, _ in self.of(messages, "warning") if p.startswith("pwm.BRIDGE")], [WHERE])

    def test_code_facts_of_this_tree(self):
        """SYS-140: the supervisor's bridge-fault rule (C4) is in the tree with every class of the decision. The port's
        hold (C2, SYS-139) decides the rest: missing, the short is refused with exactly that one error; present, the
        profile checks without an error and the supervisor gets its bridge devices."""
        facts = safestate.read_facts(REPO, "s32k1xx")
        self.assertNotIn(safestate.BRIDGE_FAULT_MACRO, {m for _, m, _ in facts.missing})
        text = (REPO / safestate.BRIDGE_FAULT_HEADER).read_text(encoding="utf-8")
        self.assertEqual(safestate._macro_list(text, safestate.BRIDGE_FAULT_MACRO), safestate.BRIDGE_FAULT_CLASSES)
        found = re.search(r"namespace bridge\s*\{.*?constexpr uint32_t MAX_DEVICES = (\d+)U;", text, re.S)
        self.assertEqual(int(found.group(1)), safestate.BRIDGE_MAX_DEVICES)
        config, messages = self.check(facts=None)
        errors = self.of(messages, "error")
        if safestate.SHORT_HOLD_MACRO in {m for _, m, _ in facts.missing}:
            self.assertEqual(len(errors), 1, errors)
            self.assertFinding(messages, "error", WHERE, safestate.SHORT_HOLD_MACRO)
        else:
            self.assertEqual(errors, [])
            self.assertIn("gdTmc6200", facts.hold_modules)
            self.assertEqual(config.safe_states.groups[0].allowed,
                             ("freewheel", "active_short_low", "active_short_high"))

    def test_code_facts_are_read_from_the_tree(self):
        tree = self.tmp / "tree"
        hold = Path(safestate.SHORT_HOLD_HEADER.format(family="s32k1xx"))
        for rel, text in ((safestate.BRIDGE_FAULT_HEADER,
                           f"#define {safestate.BRIDGE_FAULT_MACRO}(X) X(OUTPUT) X(SUPPLY) \\\n    X(INTERNAL)\n"),
                          (hold, f"#define {safestate.SHORT_HOLD_MACRO}(X) X(gdTmc6200) X(otherDriver)\n")):
            (tree / rel).parent.mkdir(parents=True, exist_ok=True)
            (tree / rel).write_text(f"#pragma once\n{text}", encoding="utf-8")
        facts = safestate.read_facts(tree, "s32k1xx")
        self.assertEqual(facts.missing, ())
        self.assertEqual(facts.hold_modules, ("gdTmc6200", "otherDriver"))
        self.assertEqual(len(safestate.read_facts(tree, "s32k3xx").missing), 1)     # hold of another family port
        # SYS-140: a list without one of the classes of C4 is no fact
        for lacking in safestate.BRIDGE_FAULT_CLASSES:
            listed = " ".join(f"X({c})" for c in safestate.BRIDGE_FAULT_CLASSES if c != lacking)
            (tree / safestate.BRIDGE_FAULT_HEADER).write_text(
                f"#define {safestate.BRIDGE_FAULT_MACRO}(X) {listed} X(IDENTITY)\n", encoding="utf-8")
            missing = safestate.read_facts(tree, "s32k1xx").missing
            self.assertEqual(len(missing), 1, lacking)
            self.assertIn(f"lacks {lacking}", missing[0][2])
        # the plain form of the hold keeps no module's enable
        (tree / hold).write_text(f"#define {safestate.SHORT_HOLD_MACRO} 1\n", encoding="utf-8")
        self.assertEqual(safestate.read_facts(tree, "s32k1xx").hold_modules, ())
        (tree / safestate.BRIDGE_FAULT_HEADER).write_text(f"// {safestate.BRIDGE_FAULT_MACRO}\n", encoding="utf-8")
        self.assertEqual(len(safestate.read_facts(tree, "s32k1xx").missing), 1)     # a comment is no definition

    def test_enable_of_a_module_the_port_does_not_keep(self):
        """SYS-140: the bound enable (GD_A, gdTmc6200) must belong to a module of NX_SAFE_STATE_SHORT_HOLD."""
        _, messages = self.check(facts=safestate.CodeFacts((), hold_modules=("otherDriver",)))
        self.assertFinding(messages, "error", WHERE, "GD_A_DRV_EN (GD_A.drv_en) is the enable of BRIDGE")
        self.assertFinding(messages, "error", WHERE, "adapter of gdTmc6200 would switch it off")
        config, messages = self.check(facts=safestate.CodeFacts((), hold_modules=("gdTmc6200",)))
        self.assertEqual(self.of(messages, "error"), [])
        self.assertTrue(config.safe_states.select)

    def test_bridge_devices_for_the_supervisor(self):
        """SYS-140: the device bound to the group with a short (GD_A, instance 0, selection group 0); GD_B is bound
        to no group and is no bridge device."""
        config, messages = self.check()
        self.assertEqual(self.of(messages, "error"), [])
        derived = config.integration.modules[safestate.SUPERVISOR_MODULE].derived
        self.assertEqual(derived["bridge_fault_devices"][0], 1)
        self.assertEqual(derived["bridge_fault_device_0"][0], (0 << 8) | 0x1)
        self.assertIn("GD_A", derived["bridge_fault_device_0"][1])
        self.assertNotIn("bridge_fault_device_1", derived)
        # without a gate driver bound to the group: no device, but the count (a malformed report still removes it)
        self.board(gate_driver=None)
        config, messages = self.check()
        self.assertEqual(self.of(messages, "error"), [])
        derived = config.integration.modules[safestate.SUPERVISOR_MODULE].derived
        self.assertEqual(derived["bridge_fault_devices"][0], 0)
        # freewheel only: nothing derived at all
        self.board(gate_driver="GD_A")
        self.pwm(safe_states=["freewheel"], safe_state_test=None, safe_state_test_us=None)
        config, messages = self.check()
        derived = config.integration.modules[safestate.SUPERVISOR_MODULE].derived
        self.assertFalse(any(k.startswith("bridge_fault") for k in derived))

    def test_freewheel_must_be_listed_and_nothing_twice(self):
        self.pwm(safe_states=["active_short_low"])
        _, messages = self.check()
        self.assertFinding(messages, "error", WHERE, "freewheel must be listed")
        self.pwm(safe_states=["freewheel", "active_short_low", "active_short_low"])
        _, messages = self.check()
        self.assertFinding(messages, "error", WHERE, "listed twice: active_short_low")

    def test_short_high_needs_a_high_side_that_holds(self):
        self.board(high_side_hold=False)
        _, messages = self.check()
        self.assertFinding(messages, "error", WHERE, "active_short_high needs a high side that can stay on")
        self.pwm(safe_states=["freewheel", "active_short_low"])
        _, messages = self.check()
        self.assertEqual(self.of(messages, "error"), [])

    def test_start_test_values(self):
        self.pwm(safe_state_test_us=None)
        _, messages = self.check()
        self.assertFinding(messages, "error", "pwm.BRIDGE.safe_state_test_us", "needs its duration")
        self.pwm(safe_state_test=None, safe_state_test_us=None)
        _, messages = self.check()
        self.assertFinding(messages, "warning", "pwm.BRIDGE.safe_state_test", "latent fault")
        self.pwm(safe_state_test_us=100)
        _, messages = self.check()
        self.assertFinding(messages, "warning", "pwm.BRIDGE.safe_state_test_us", "no effect")
        self.pwm(safe_states=["freewheel"], safe_state_test=True)
        _, messages = self.check()
        self.assertFinding(messages, "error", "pwm.BRIDGE.safe_state_test", "builds an active short")
        self.pwm(safe_state_test_us=0)
        _, messages = self.check()
        self.assertTrue(any("safe_state_test_us" in p for p, _ in self.of(messages, "error")))

    def test_gate_driver_and_reset_state(self):
        self.board(gate_driver=None, bridge_reset_state="active_short_low")
        config, messages = self.check()
        self.assertFinding(messages, "warning", WHERE, "names no gate driver")
        self.assertFalse(any("reset state" in t for _, t in self.of(messages, "warning")))
        self.assertEqual(config.safe_states.groups[0].bound_outputs, ())
        self.board(gate_driver="GD_A", bridge_reset_state=None)
        _, messages = self.check()
        self.assertFinding(messages, "warning", WHERE, "reset state is not stated")

    def test_enable_of_a_shorted_group_belongs_to_it_alone(self):
        def shared(data):
            data["devices"][1]["connect"]["drv_en"] = "GD_A_DRV_EN"      # GD_B uses the enable of GD_A
        self.edit("boards/testboard_active_short/board.yaml", shared)
        config, messages = self.check()
        # refused already by the connection rule of the board (one output, one connection); safestate.py keeps its
        # own rule for the case that rule is ever relaxed
        self.assertIsNone(config)
        self.assertFinding(messages, "error", "devices[1].connect.drv_en", "already connected")

    def test_family_without_the_short(self):
        with mock.patch.dict(safestate.SHORT_FAMILIES, {}, clear=True):
            _, messages = self.check()
        self.assertFinding(messages, "error", WHERE, "cannot force an active short")

    def test_projects_without_the_key_are_unchanged(self):
        for project in (DEMO_PROJECT, PROFILES / "projects" / "testproject_safe_outputs" / "project.yaml"):
            with self.subTest(project=project.parent.name):
                config, messages = nxconfig.check(project)
                self.assertIsNotNone(config, [str(m) for m in messages if m.severity == "error"])
                self.assertFalse(config.safe_states.select)
                self.assertTrue(all(g.allowed == ("freewheel",) and not g.test for g in config.safe_states.groups))
                self.assertFalse(any("safe_state" in m.yaml_path for m in messages))
                # SYS-140: no derived value more for the supervisor (its object code stays as it was)
                supervisor = config.integration.modules.get(safestate.SUPERVISOR_MODULE)
                if supervisor is not None:
                    self.assertFalse(any(k.startswith("bridge_fault") for k in supervisor.derived))


if __name__ == "__main__":
    unittest.main()
