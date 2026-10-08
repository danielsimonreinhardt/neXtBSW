"""IO-28: Aussagekraft des Host-Tests I2cTransferHostTest.cpp - jede Mutante von i2c/I2cTransfer.h muss ihn rot machen.

Die Mutanten werden in eine Kopie des Headers geschrieben (Include-Pfad davor), der Quellbaum bleibt unveraendert.
Run: .venv/Scripts/python -m unittest libs/bsp/bspI2c/test/host/test_i2c_transfer_mutations.py
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[4]
HEADER = REPO / "libs/bsp/bspI2c/include/i2c/I2cTransfer.h"
TEST = HERE / "I2cTransferHostTest.cpp"

MUTANTS = {
    "probe ohne STOP": ("bus.write(address, nullptr, 0U, true)", "bus.write(address, nullptr, 0U, false)"),
    "Schreibteil mit STOP": ("bus.write(address, txData, txLength, false)", "bus.write(address, txData, txLength, true)"),
    "Lesen trotz Schreibfehler": ("            return written;\n", ""),
    "Lesen ohne STOP": ("return bus.read(address, rxData, rxLength, true);",
                        "return bus.read(address, rxData, rxLength, false);"),
    "Scan ab 0x00": ("constexpr uint8_t FIRST_DEVICE_ADDRESS = 0x08U;", "constexpr uint8_t FIRST_DEVICE_ADDRESS = 0x00U;"),
    "Scan bis 0x7F": ("constexpr uint8_t LAST_DEVICE_ADDRESS  = 0x77U;", "constexpr uint8_t LAST_DEVICE_ADDRESS  = 0x7FU;"),
    "Scan bricht bei NACK ab": ("else if (probed != I2cResult::ADDRESS_NACK)", "else"),
    "Scan laeuft nach Busfehler weiter": ("            break;\n", ""),
    "Bit falsch gesetzt": ("result.found[a / 32U] |= (1U << (a % 32U));", "result.found[a / 32U] |= (1U << (a % 31U));"),
    "Zaehler fehlt": ("            ++result.count;\n", ""),
    "Fensteranfang nicht begrenzt": ("        first = FIRST_DEVICE_ADDRESS;\n", ""),
    "Name doppelt": ('case I2cResult::TIMEOUT: return "timeout";', 'case I2cResult::TIMEOUT: return "bus error";'),
    "contains ohne Grenze": ("return (address <= MAX_ADDRESS) && ", "return "),
}


def build_and_run(include: Path, out: Path) -> bool:
    exe = out / "t.exe"
    build = subprocess.run([sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra",
                            "-Wconversion", "-Werror", "-I", str(include), "-I", str(REPO / "libs/bsp/bspI2c/include"),
                            str(TEST), "-o", str(exe)], capture_output=True, text=True)
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
        self.assertTrue(build_and_run(self.tmp / "none", self.tmp))

    def test_every_mutant_fails(self):
        original = HEADER.read_text(encoding="utf-8")
        for name, (old, new) in MUTANTS.items():
            with self.subTest(mutant=name):
                self.assertEqual(original.count(old), 1, name)
                include = self.tmp / name.replace(" ", "_")
                (include / "i2c").mkdir(parents=True)
                (include / "i2c" / "I2cTransfer.h").write_text(original.replace(old, new), encoding="utf-8")
                self.assertFalse(build_and_run(include, include), f"mutant survived: {name}")


if __name__ == "__main__":
    unittest.main()
