"""DOC-29 / NXCPP-02: the root build compiles C and C++ in ISO mode (-std=c99 / -std=c++17), not in GNU mode.

Static check of the root CMakeLists.txt; the build of every profile (tools/test group target) is the dynamic proof,
because a GNU extension outside platform code would then fail the compilation.

    .venv/Scripts/python -m unittest cmake/test/test_iso_mode.py
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def active_lines(text: str) -> list[str]:
    return [line.split("#", 1)[0].strip() for line in text.splitlines()]


class IsoModeTest(unittest.TestCase):
    def setUp(self):
        self.lines = active_lines((ROOT / "CMakeLists.txt").read_text(encoding="utf-8"))

    def test_extensions_off(self):
        for lang in ("C", "CXX"):
            self.assertIn(f"set(CMAKE_{lang}_EXTENSIONS OFF)", self.lines,
                          f"CMakeLists.txt: CMAKE_{lang}_EXTENSIONS must be OFF (NXCPP-02, DE2)")

    def test_standards_unchanged(self):
        self.assertIn("set(CMAKE_C_STANDARD 99)", self.lines)
        self.assertIn("set(CMAKE_CXX_STANDARD 17)", self.lines)

    def test_no_gnu_dialect_flag_in_presets(self):
        for name in ("CMakePresets.json", "cmake/NxPresets.json"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"-std=gnu", text), f"{name}: no -std=gnu* flag")

    def test_fp_contract_is_explicit(self):
        # ISO mode changes GCC's default -ffp-contract; the choice must stay visible in the build, not implicit.
        self.assertTrue(any("-ffp-contract=" in line for line in self.lines),
                        "CMakeLists.txt: -ffp-contract must be set explicitly")


if __name__ == "__main__":
    unittest.main()
