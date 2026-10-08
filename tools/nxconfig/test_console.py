"""PLAT-27: tools/nxconfig/console.py - console and logger as a function of the project, logger buffer size.

- nxconfig check with the synthetic profile testproject_s32k142_bl (28 KB application RAM, bootloader, no SBC) copied
  into a temporary tree: no section -> no message, console on, no buffer value for CMake (the default of the root
  CMakeLists.txt stays, image as before); console off -> NX_CONSOLE OFF; a buffer value -> NX_LOGGER_BUFFER_BYTES;
  messages with file, line and YAML path: below the default (warning), outside 512..65536 (schema error), larger
  than the application RAM (error), a value without logger (warning)
- the eval board demo without section: console on, no buffer value (byte-identical image)
- the generated project.cmake (render_cmake) carries NX_CONSOLE and, only with a project value, the buffer size

Run: .venv/Scripts/python -m unittest tools/nxconfig/test_console.py
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from ruamel.yaml import YAML

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))

import nxconfig  # noqa: E402
from nxconfig import config as nxcfg  # noqa: E402
from nxconfig import console  # noqa: E402

PROFILES = HERE / "test" / "profiles"
# PLAT-62: the own full-featured test profile (synthetic testboard_full/testproject_full) instead of the eval board
# project, which stays private (PLAT-46 F10) - a test against it would be red in the exported open tree. What
# proves something about the eval board itself uses EVAL_DEMO_PROJECT and is skipped where it is missing.
DEMO_PROJECT = REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full" / "project.yaml"
EVAL_DEMO_PROJECT = REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml"
PROJECT = "projects/testproject_s32k142_bl/project.yaml"
SECTION = ("console",)


class ConsoleCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for kind, name in (("boards", "testboard_s32k142_bl"), ("projects", "testproject_s32k142_bl")):
            shutil.copytree(PROFILES / kind / name, self.tmp / kind / name)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def check(self, section=None):
        path = self.tmp / PROJECT
        yaml = YAML()
        data = yaml.load(path.read_text(encoding="utf-8"))
        if section is None:
            data.pop("console", None)
        else:
            data["console"] = section
        with path.open("w", encoding="utf-8") as f:
            yaml.dump(data, f)
        return nxconfig.check(path)

    @staticmethod
    def findings(messages, severity=None):
        return [m for m in messages if m.path[:1] == SECTION and severity in (None, m.severity)]

    def settings(self, config):
        return nxcfg.build_settings(config)[0]

    def test_without_section(self):
        config, messages = self.check()
        self.assertIsNotNone(config, [str(m) for m in messages])
        self.assertEqual(self.findings(messages), [])
        settings = self.settings(config)
        self.assertEqual(settings["NX_CONSOLE"], "ON")
        self.assertNotIn("NX_LOGGER_BUFFER_BYTES", settings)

    def test_console_off(self):
        config, messages = self.check({"enabled": False})
        self.assertIsNotNone(config, [str(m) for m in messages])
        self.assertEqual(self.findings(messages), [])
        settings = self.settings(config)
        self.assertEqual(settings["NX_CONSOLE"], "OFF")
        self.assertNotIn("NX_LOGGER_BUFFER_BYTES", settings)
        self.assertIn('set(NX_CONSOLE "OFF")', nxcfg.render_cmake(settings, "test"))

    def test_buffer_value(self):
        config, messages = self.check({"logger_buffer_bytes": 16384})
        self.assertIsNotNone(config, [str(m) for m in messages])
        self.assertEqual(self.findings(messages), [])
        settings = self.settings(config)
        self.assertEqual((settings["NX_CONSOLE"], settings["NX_LOGGER_BUFFER_BYTES"]), ("ON", "16384"))
        self.assertIn('set(NX_LOGGER_BUFFER_BYTES "16384")', nxcfg.render_cmake(settings, "test"))

    def test_buffer_below_default_warns(self):
        config, messages = self.check({"logger_buffer_bytes": 4096})
        self.assertIsNotNone(config, [str(m) for m in messages])
        warnings = self.findings(messages, "warning")
        self.assertEqual([m.path for m in warnings], [("console", "logger_buffer_bytes")])
        self.assertEqual(warnings[0].file.name, "project.yaml")
        self.assertIsInstance(warnings[0].line, int)
        self.assertIn("logger lost", warnings[0].text)
        self.assertEqual(self.settings(config)["NX_LOGGER_BUFFER_BYTES"], "4096")

    def test_buffer_outside_bounds(self):
        for value in (256, 70000):
            with self.subTest(value=value):
                _, messages = self.check({"logger_buffer_bytes": value})
                errors = self.findings(messages, "error")
                self.assertEqual([m.path for m in errors], [("console", "logger_buffer_bytes")],
                                 [str(m) for m in messages])
                self.assertIsInstance(errors[0].line, int)

    def test_buffer_larger_than_ram(self):
        # 28 KB application RAM (SRAM_L + SRAM_U of the profile)
        _, messages = self.check({"logger_buffer_bytes": 32768})
        errors = self.findings(messages, "error")
        self.assertEqual([m.path for m in errors], [("console", "logger_buffer_bytes")], [str(m) for m in messages])
        self.assertIn("application RAM of 28672 B", errors[0].text)

    def test_buffer_without_logger_warns(self):
        config, messages = self.check({"enabled": False, "logger_buffer_bytes": 4096})
        self.assertIsNotNone(config, [str(m) for m in messages])
        warnings = self.findings(messages, "warning")
        self.assertEqual([m.path for m in warnings], [("console", "logger_buffer_bytes")])
        self.assertIn("no effect", warnings[0].text)
        self.assertNotIn("NX_LOGGER_BUFFER_BYTES", self.settings(config))

    def test_unknown_key(self):
        _, messages = self.check({"enable": False})
        self.assertTrue(any(m.severity == "error" and m.path[:1] == SECTION for m in messages),
                        [str(m) for m in messages])


class DemoTest(unittest.TestCase):
    def test_demo_unchanged(self):
        """Eval board demo without section: console and logger on, buffer from the root CMakeLists.txt (8192)."""
        config, messages = nxconfig.check(DEMO_PROJECT)
        self.assertIsNotNone(config, [str(m) for m in messages if m.severity == "error"])
        self.assertIsNone(config.project.console)
        self.assertTrue(console.enabled(config))
        self.assertEqual(console.settings(config), {"NX_CONSOLE": "ON"})


if __name__ == "__main__":
    unittest.main()
