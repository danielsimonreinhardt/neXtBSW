"""ARCH-52: the manifest fields of the generated module binding and their checks, each with its YAML path.

- `binding` (header and type of the module's trait): only for a device driver, needs lifecycle, a level the platform
  is offered (1, 2), contracts the family binders provide (spi_device: 1), RTE access `reading`, an existing header;
  the lifecycle task of a bound module must be a BSW task of the task manifest.
- `connect.<x>.optional`: the board may leave the connection open; not together with safe_off. Wired on the board but
  not used by the project, it counts as not wired (warning), the device stays (IO-68).
- `max_instances`: every used device beyond it is reported at the project entry that brings it in.

Own profile only: the synthetic module synthDriver (tools/bswgen/test/binding/modules) on testboard_binding /
testproject_binding, copied into a temporary tree and varied per test. The repository manifests stay valid.

Run: .venv/Scripts/python -m unittest tools/nxconfig/test_binding.py
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
from nxconfig.config import MODULE_DIRS, load_modules  # noqa: E402
from nxconfig.model import ConnectSpec  # noqa: E402

PROFILES = REPO / "tools" / "bswgen" / "test" / "profiles"
BOARD = PROFILES / "boards" / "testboard_binding" / "board.yaml"
PROJECT = PROFILES / "projects" / "testproject_binding" / "project.yaml"
SYNTH = REPO / "tools" / "bswgen" / "test" / "binding" / "modules" / "synthDriver"


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class BindingChecksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nx_binding_check_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def check(self, manifest: dict | None = None, board: dict | None = None, project: dict | None = None):
        """nxconfig.check of the profile with the synthetic module (manifest varied); (config, messages)."""
        modules = self.tmp / "modules"
        if modules.exists():
            shutil.rmtree(modules)
        shutil.copytree(SYNTH.parent, modules)
        if manifest is not None:
            (modules / "synthDriver" / "module.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False),
                                                                 encoding="utf-8")
        board_dir, project_dir = self.tmp / "boards" / "b", self.tmp / "projects" / "p"
        board_dir.mkdir(parents=True, exist_ok=True)
        project_dir.mkdir(parents=True, exist_ok=True)
        (board_dir / "board.yaml").write_text(yaml.safe_dump(board or load(BOARD), sort_keys=False), encoding="utf-8")
        project = copy.deepcopy(project or load(PROJECT))
        project["project"]["board"] = "../../boards/b"
        path = project_dir / "project.yaml"
        path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        return nxconfig.check(path, nxconfig.MCUDB, (*MODULE_DIRS, modules))

    @staticmethod
    def errors(messages):
        return [m for m in messages if m.severity == "error"]

    def manifest(self, **changes) -> dict:
        m = load(SYNTH / "module.yaml")
        for key, value in changes.items():
            if value is None:
                m.pop(key, None)
            else:
                m[key] = value
        return m

    def only_error(self, messages, path: str, text: str, file: str = "module.yaml"):
        errors = self.errors(messages)
        self.assertEqual(len(errors), 1, errors)
        (m,) = errors
        self.assertEqual((m.file.name, m.yaml_path), (file, path))
        self.assertIn(text, m.text)
        return m

    # --- the profile itself -----------------------------------------------------------------------------

    def test_profile_is_clean(self):
        config, messages = self.check()
        self.assertIsNotNone(config, messages)
        self.assertEqual(self.errors(messages), [])
        self.assertEqual([d.name for d in config.integration.devices], ["SYN_A", "SYN_B"])
        self.assertNotIn("fault", config.integration.devices[1].connect)

    def test_repository_manifests_stay_valid(self):
        modules = load_modules()
        self.assertEqual([m for m in modules.messages if m.severity == "error"], [])

    # --- binding ----------------------------------------------------------------------------------------

    def test_binding_needs_a_device_driver(self):
        m = self.manifest(kind="service", parts=None, requires=None, connect=None, console=None, rte=None,
                          parameters=None)
        _, messages = self.check(m)
        self.assertTrue(any(e.yaml_path == "binding" and "binding needs a device driver" in e.text
                            for e in self.errors(messages)), messages)

    def test_binding_needs_lifecycle(self):
        _, messages = self.check(self.manifest(lifecycle=None))
        self.only_error(messages, "binding", "binding needs lifecycle (level and task)")

    def test_lifecycle_level_the_platform_offers(self):
        _, messages = self.check(self.manifest(lifecycle={"level": 3, "task": "bsp"}))
        self.only_error(messages, "lifecycle.level", "lifecycle level 3 is not offered to the platform")

    def test_lifecycle_task_is_a_bsw_task(self):
        _, messages = self.check(self.manifest(lifecycle={"level": 1, "task": "motor"}))
        m = self.only_error(messages, "lifecycle.task", "task motor of module synthDriver is no BSW task")
        self.assertIn("TASK_MOTOR", m.text)
        for task in ("can", "safety", "idle"):
            with self.subTest(task=task):
                _, messages = self.check(self.manifest(lifecycle={"level": 2, "task": task}))
                self.assertEqual(self.errors(messages), [])

    def test_contracts_the_binders_offer(self):
        _, messages = self.check(self.manifest(requires={"spi_device": 2}))
        self.assertTrue(any(e.yaml_path == "requires.spi_device" and "cannot be bound by the generator" in e.text
                            for e in self.errors(messages)), messages)

    def test_rte_access_reading_only(self):
        m = self.manifest()
        m["rte"] = {"prefix": "synth", "access": "gate_driver", "values": {"faults": {"types": ["uint16"],
                                                                                        "unit": "bits"}}}
        _, messages = self.check(m)
        self.assertTrue(any(e.yaml_path == "rte.access" and "a bound module uses access reading" in e.text
                            for e in self.errors(messages)), messages)

    def test_trait_header_must_exist(self):
        _, messages = self.check(self.manifest(binding={"include": "synth/Missing.h", "trait": "::synth::X"}))
        self.only_error(messages, "binding.include", "header synth/Missing.h not found")

    def test_trait_name_is_a_cpp_type(self):
        _, messages = self.check(self.manifest(binding={"include": "synth/SynthBinding.h", "trait": "synth-binding"}))
        self.assertTrue(any(e.yaml_path.startswith("binding.trait") for e in self.errors(messages)), messages)

    # --- optional connections ---------------------------------------------------------------------------

    def test_optional_connection_may_stay_open_others_not(self):
        board = load(BOARD)
        del board["devices"][1]["connect"]["sense"]           # sense is not optional
        _, messages = self.check(board=board)
        m = self.only_error(messages, "devices[1].connect", "device SYN_B (synthDriver) needs connect: sense",
                            file="board.yaml")
        self.assertNotIn("fault", m.text)

    def test_unused_optional_connection_counts_as_not_wired(self):
        # IO-68: the board wires the optional fault of SYN_A, the project does not use that input: the device stays,
        # without the connection (warning with the project path) - a required connection still drops the device
        board, project = load(BOARD), load(PROJECT)
        inputs = [e["name"] for e in board["dio"]["inputs"]]
        project["use"]["dio"] = [n for n in inputs if n != "SYN_A_FAULT"] + [o["name"] for o in board["dio"]["outputs"]]
        config, messages = self.check(board=board, project=project)
        self.assertEqual(self.errors(messages), [])
        self.assertEqual([d.name for d in config.integration.devices], ["SYN_A", "SYN_B"])
        self.assertNotIn("fault", config.integration.devices[0].connect)
        self.assertIn("en", config.integration.devices[0].connect)
        warnings = [m for m in messages if m.severity == "warning" and "SYN_A_FAULT" in m.text]
        self.assertEqual(len(warnings), 1, messages)
        self.assertIn("the optional connection fault is treated as not wired", warnings[0].text)
        self.assertEqual(warnings[0].yaml_path, "modules.SYN_A")
        # a required connection the project does not use: the device is left out as before
        project["use"]["dio"] = [n for n in project["use"]["dio"] if n != "SYN_A_EN"]
        config, messages = self.check(board=board, project=project)
        self.assertNotIn("SYN_A", [d.name for d in config.integration.devices])
        self.assertTrue(any("SYN_A is not instantiated" in m.text for m in messages), messages)

    def test_safe_off_cannot_be_optional(self):
        with self.assertRaises(ValueError) as caught:
            ConnectSpec(kind="digital_output", safe_off=True, optional=True)
        self.assertIn("a safe_off connection cannot be optional", str(caught.exception))
        m = self.manifest()
        m["connect"]["en"]["optional"] = True
        _, messages = self.check(m)
        self.assertTrue(any(e.yaml_path.startswith("connect.en") and "cannot be optional" in e.text
                            for e in self.errors(messages)), messages)

    # --- max_instances ----------------------------------------------------------------------------------

    def test_max_instances(self):
        _, messages = self.check(self.manifest(max_instances=1))
        m = self.only_error(messages, "use.spi", "device SYN_B (synthDriver) is instance 2 of module synthDriver, "
                            "which allows at most 1 per project", file="project.yaml")
        self.assertIn("already used: SYN_A", m.text)
        project = load(PROJECT)
        project["use"]["spi"] = ["B_SPI"]
        del project["modules"]["SYN_A"]
        _, messages = self.check(self.manifest(max_instances=1), project=project)
        self.assertEqual(self.errors(messages), [])
        _, messages = self.check(self.manifest(max_instances=2))
        self.assertEqual(self.errors(messages), [])


if __name__ == "__main__":
    unittest.main()
