"""Host test for cmake/NxBuildDirCheck.cmake (PLAT-47): a build directory that recorded the host system stops early.

Run: .venv/Scripts/python -m unittest cmake/test/test_build_dir_check.py
The recorded files follow CMake's CMakeSystem.cmake.in; the stale one is the file found in the ARCH-23 build directory.
"""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECK = ROOT / "cmake" / "NxBuildDirCheck.cmake"
CMAKE = shutil.which("cmake")

HOST_SYSTEM = """set(CMAKE_HOST_SYSTEM "Windows-10.0.19045")
set(CMAKE_HOST_SYSTEM_NAME "Windows")
set(CMAKE_HOST_SYSTEM_VERSION "10.0.19045")
set(CMAKE_HOST_SYSTEM_PROCESSOR "AMD64")

{include}

set(CMAKE_SYSTEM "{system}")
set(CMAKE_SYSTEM_NAME "{system}")
set(CMAKE_SYSTEM_VERSION "{version}")
set(CMAKE_SYSTEM_PROCESSOR "{processor}")

set(CMAKE_CROSSCOMPILING "{cross}")

set(CMAKE_SYSTEM_LOADED 1)
"""

STALE_HOST = HOST_SYSTEM.format(include="", system="Windows", version="10.0.19045", processor="AMD64", cross="FALSE")
CROSS = HOST_SYSTEM.format(include='include("C:/repo/cmake/toolchains/NxArmNoneEabi-gcc.cmake")', system="Generic",
                           version="", processor="ARM", cross="TRUE")


@unittest.skipIf(CMAKE is None, "cmake not on PATH")
class BuildDirCheckTest(unittest.TestCase):
    def run_check(self, content: str | None):
        with tempfile.TemporaryDirectory() as tmp:
            system_file = Path(tmp) / "CMakeFiles" / "4.4.3" / "CMakeSystem.cmake"
            if content is not None:
                system_file.parent.mkdir(parents=True)
                system_file.write_text(content, encoding="utf-8")
            return subprocess.run([CMAKE, f"-DNX_CHECK_SYSTEM_FILE={system_file.as_posix()}", "-P", str(CHECK)],
                                  capture_output=True, text=True)

    def test_stale_host_directory_stops_with_cause(self):
        result = self.run_check(STALE_HOST)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        message = " ".join(result.stderr.split())
        self.assertIn("configured for the host system", message)
        self.assertIn("CMAKE_SYSTEM_NAME 'Windows'", message)
        self.assertIn("cmake --fresh", message)

    def test_missing_cross_flag_counts_as_host(self):
        result = self.run_check(CROSS.replace('set(CMAKE_CROSSCOMPILING "TRUE")\n', ""))
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_cross_directory_passes(self):
        result = self.run_check(CROSS)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("NxBuildDirCheck: ok", result.stdout)

    def test_new_directory_passes(self):
        result = self.run_check(None)
        self.assertEqual(result.returncode, 0, result.stderr)


class ProjectHookTest(unittest.TestCase):
    """The root CMakeLists.txt registers the check before and after project() (ARCH-57: no longer the preset).

    Until ARCH-57 the two variables stood in the base preset as ${sourceDir}/cmake/NxBuildDirCheck.cmake. In a
    workspace outside the tree ${sourceDir} is the customer repository, and ${fileDir} of an included preset file
    resolves against the top-level preset file as well - so the path can only be formed where NX_ROOT is known.
    """

    def test_root_cmakelists_runs_check_before_and_after_project(self):
        text = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
        expected = '"${NX_ROOT}/cmake/NxBuildDirCheck.cmake"'
        self.assertIn(f"set(CMAKE_PROJECT_neXtBSW_INCLUDE_BEFORE {expected})", text)
        self.assertIn(f"set(CMAKE_PROJECT_neXtBSW_INCLUDE {expected})", text)
        self.assertIn('project(\n    "neXtBSW"', text)

    def test_base_preset_is_includable_by_a_workspace(self):
        """ARCH-57: the base presets live in cmake/NxPresets.json, which the root preset file and a workspace
        include; build directory and .venv come from ${sourceDir}, which is the workspace there."""
        root = json.loads((ROOT / "CMakePresets.json").read_text(encoding="utf-8"))
        self.assertIn("cmake/NxPresets.json", root["include"])
        base = next(p for p in json.loads((ROOT / "cmake" / "NxPresets.json").read_text(encoding="utf-8"))
                    ["configurePresets"] if p["name"] == "nx-arm-gcc")
        self.assertEqual(base["binaryDir"], "${sourceDir}/build/${presetName}")
        self.assertEqual(base["cacheVariables"]["Python3_EXECUTABLE"], "${sourceDir}/.venv/Scripts/python.exe")
        for key in ("CMAKE_PROJECT_neXtBSW_INCLUDE_BEFORE", "CMAKE_PROJECT_neXtBSW_INCLUDE"):
            self.assertNotIn(key, base["cacheVariables"])


if __name__ == "__main__":
    unittest.main()
