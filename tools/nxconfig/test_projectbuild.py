# SPDX-License-Identifier: Apache-2.0
"""Host tests of the open build helper of the target build checks (PLAT-114): nxconfig/projectbuild.py and the
configure arguments of nxconfig/presets.py, which the Studio uses as well (tools/studio/tests/test_core.py,
test_configure_is_the_open_build_helper).

The project is the open synthetic build profile projects/buildtest_s32k148_min - never the eval board. Nothing is
built here; the target builds that use the helper are entries of the target group of tools/test.
"""

import ast
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))

from nxconfig import presets, projectbuild  # noqa: E402

PROJECT = REPO / "projects" / "buildtest_s32k148_min" / "project.yaml"


class ConfigureArgsTest(unittest.TestCase):
    def test_order_and_own_values(self):
        base = presets.PresetSettings(generator="Ninja", cache={"A": "1", "NX_PROJECT": "x", "Python3_EXECUTABLE": "y"})
        args = presets.configure_args(base, Path("/src"), Path("/b"), Path("/p/project.yaml"), "/py/python",
                                      ["-DOPT=ON"])
        self.assertEqual(["-S", str(Path("/src")), "-B", str(Path("/b")), "-G", "Ninja", "-DA=1",
                          "-DNX_PROJECT=/p/project.yaml", "-DPython3_EXECUTABLE=/py/python", "-DOPT=ON"], args)

    def test_base_preset_of_the_tree(self):
        base = presets.preset_settings(REPO)
        self.assertTrue(base.found)
        self.assertNotIn("NX_PROJECT", base.cache)
        self.assertEqual("ON", base.cache["CMAKE_COMPILE_WARNING_AS_ERROR"])


class ProjectBuildTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.build_dir = Path(self._tmp.name) / "pb1"

    def tearDown(self):
        self._tmp.cleanup()

    def test_open_project_checks_without_error(self):
        build = projectbuild.ProjectBuild(PROJECT, self.build_dir, repo=REPO, python="py")
        self.assertEqual([], [str(m) for m in build.errors])
        self.assertIsNotNone(build.config)

    def test_steps_configure_with_the_base_preset_then_build(self):
        with mock.patch.dict(os.environ, {"NX_ARM_GCC_BIN": "/gcc/bin", "PATH": "/usr/bin"}):
            build = projectbuild.ProjectBuild(PROJECT, self.build_dir, repo=REPO, python="C:/py/python.exe")
            configure, make = build.steps()
        self.assertEqual(["CMake konfigurieren", "Bauen"], [configure.title, make.title])
        self.assertEqual(["-S", str(REPO), "-B", str(self.build_dir), "-G", "Ninja Multi-Config"], configure.argv[1:7])
        self.assertEqual(1, sum(a.startswith("-DNX_PROJECT=") for a in configure.argv))
        self.assertIn(f"-DNX_PROJECT={PROJECT.resolve().as_posix()}", configure.argv)
        self.assertIn("-DPython3_EXECUTABLE=C:/py/python.exe", configure.argv)
        self.assertIn("-DCMAKE_COMPILE_WARNING_AS_ERROR=ON", configure.argv)
        self.assertEqual(f"/gcc/bin{os.pathsep}/usr/bin", configure.env["PATH"])
        self.assertEqual("arm-none-eabi-gcc", configure.env["CC"])
        self.assertEqual(["--build", str(self.build_dir), "--config", "RelWithDebInfo"], make.argv[1:])
        self.assertEqual(configure.env, make.env)
        self.assertEqual(REPO, configure.cwd)
        self.assertIn("NX_PROJECT", configure.command_line())

    def test_run_keeps_the_output_and_reports_failure(self):
        log: list[str] = []
        ok = projectbuild.Step("ok", [sys.executable, "-c", "print('hello')"], REPO)
        bad = projectbuild.Step("bad", [sys.executable, "-c", "import sys; sys.exit(3)"], REPO)
        with mock.patch("builtins.print"):
            self.assertTrue(projectbuild.run(ok, log))
            self.assertFalse(projectbuild.run(bad, log))
        self.assertIn("hello", log[0])

    def test_tool_prefers_nx_arm_gcc_bin(self):
        folder = Path(self._tmp.name) / "bin"
        folder.mkdir()
        exe = folder / ("arm-none-eabi-nm.exe" if os.name == "nt" else "arm-none-eabi-nm")
        exe.write_text("", encoding="utf-8")
        with mock.patch.dict(os.environ, {"NX_ARM_GCC_BIN": str(folder)}):
            self.assertEqual(str(exe), projectbuild.tool("arm-none-eabi-nm"))

    def test_open_helper_does_not_import_the_studio(self):
        """The reason it exists: an open tool must not need tools/studio (not distributed, F12)."""
        for name in ("projectbuild.py", "presets.py"):
            tree = ast.parse((HERE / name).read_text(encoding="utf-8"))
            modules = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
            modules += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
            self.assertEqual([], [m for m in modules if m.split(".")[0] == "studio"], name)


if __name__ == "__main__":
    unittest.main()
