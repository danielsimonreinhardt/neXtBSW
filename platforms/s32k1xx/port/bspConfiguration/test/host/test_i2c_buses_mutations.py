"""IO-28: Aussagekraft des Host-Tests I2cBusesHostTest.cpp - jede Mutante von src/bsp/i2c/I2cBuses.cpp muss ihn rot
machen. Die Mutanten werden in eine Kopie der Quelle geschrieben, der Quellbaum bleibt unveraendert.
Run: .venv/Scripts/python -m unittest platforms/s32k1xx/port/bspConfiguration/test/host/test_i2c_buses_mutations.py
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PORT = HERE.parents[1]
REPO = PORT.parents[3]
SOURCE = PORT / "src/bsp/i2c/I2cBuses.cpp"
TEST = HERE / "I2cBusesHostTest.cpp"

MUTANTS = {
    "Setup bei jedem Transfer": ("            _started = bridge::init(_index);", "            _started = bridge::init(_index) && false;"),
    "kein Setup": ("return start() ? toResult(bridge::write", "return true ? toResult(bridge::write"),
    "Setup-Fehler ignoriert": ("_started = bridge::init(_index);", "(void)bridge::init(_index); _started = true;"),
    "falscher Bus": ("explicit FlexioI2cBus(size_t const index) : _index(index)",
                     "explicit FlexioI2cBus(size_t const index) : _index(index * 0U)"),
    "unbekannter Wert durchgereicht": ("(value <= bridge::RESULT_BUS_ERROR) ? static_cast<I2cResult>(value)",
                                       "true ? static_cast<I2cResult>(value)"),
    "STOP verloren": ("bridge::read(_index, address, data, length, sendStop)", "bridge::read(_index, address, data, length, sendStop || true)"),
    "Name mit Praefix": ("if ((n == length) && (candidate[n] == '\\0'))", "if (n == length)"),
    "Gross/klein unterschieden": ("(lower(candidate[n]) == lower(name[n]))", "(lower(candidate[n]) == name[n])"),
    "Index ohne Grenze": ("return (index < BUS_COUNT) ? &buses[index] : nullptr;", "return &buses[index];"),
    "Rate des falschen Busses": ("return (index < BUS_COUNT) ? requestedRates[index] : 0U;", "return (index < BUS_COUNT) ? requestedRates[0] : 0U;"),
}


def build_and_run(source: Path, out: Path) -> bool:
    exe = out / "t.exe"
    build = subprocess.run([sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra",
                            "-Wconversion", "-Werror", "-I", str(HERE / "i2cStubs"), "-I", str(PORT / "include"),
                            "-I", str(PORT / "src/bsp/i2c"), "-I", str(REPO / "libs/bsp/bspI2c/include"),
                            str(source), str(TEST), "-o", str(exe)], capture_output=True, text=True)
    if build.returncode != 0:
        raise AssertionError(f"does not compile - a mutant must fail at run time, not at build time:\n{build.stderr}")
    return subprocess.run([str(exe)], capture_output=True, text=True, timeout=60).returncode == 0


class MutationTest(unittest.TestCase):
    def setUp(self):
        try:
            import ziglang  # noqa: F401
        except ImportError:
            self.skipTest("ziglang not installed")
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_original_passes(self):
        self.assertTrue(build_and_run(SOURCE, self.tmp))

    def test_every_mutant_fails(self):
        original = SOURCE.read_text(encoding="utf-8")
        for name, (old, new) in MUTANTS.items():
            with self.subTest(mutant=name):
                self.assertEqual(original.count(old), 1, name)
                folder = self.tmp / name.replace(" ", "_").replace("/", "_")
                folder.mkdir()
                source = folder / "I2cBuses.cpp"
                source.write_text(original.replace(old, new), encoding="utf-8")
                self.assertFalse(build_and_run(source, folder), f"mutant survived: {name}")


if __name__ == "__main__":
    unittest.main()
