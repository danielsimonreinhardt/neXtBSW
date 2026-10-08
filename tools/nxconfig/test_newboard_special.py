"""Host test of the special pins of the board wizard (TOOL-188), Qt free.

The crystal pins are named differently per family: EXTAL/XTAL (s32k1xx), RCC_OSC_IN/OUT and RCC_OSC32_IN/OUT
(stm32), OSCIN/OSCOUT (rm4x). The pattern of tools/nxconfig/newboard.py SPECIAL_PIN_KINDS must find all of them
in the real pin multiplexing tables of the database, and nothing on an ordinary pin. The derivatives are only read.
"""

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from nxconfig import newboard as nb  # noqa: E402


def _package(derivative: str, package: str) -> "nb.Package":
    loaded, messages = nb.load_package(nb.find_derivative(derivative), package)
    assert loaded is not None and loaded.complete, messages
    return loaded


class SpecialPins(unittest.TestCase):
    def test_stm32_crystal_pins_are_special(self):
        package = _package("STM32F446RE", "LQFP64")
        for pin, signal in (("PH0", "RCC_OSC_IN"), ("PH1", "RCC_OSC_OUT"),
                            ("PC14", "RCC_OSC32_IN"), ("PC15", "RCC_OSC32_OUT")):
            text = package.special(pin)
            self.assertTrue(text.startswith("oscillator pin"), f"{pin}: {text!r}")
            self.assertIn(signal, text)
        self.assertTrue(package.special("PA13").startswith("debug pin"))       # SWDIO
        self.assertEqual(package.special("PA5"), "")                            # an ordinary pin stays free

    def test_s32k1xx_crystal_pins_stay_special(self):
        package = _package("S32K144", "LQFP64")
        self.assertIn("EXTAL", package.special("PTB7"))
        self.assertIn("XTAL", package.special("PTB6"))
        self.assertTrue(package.special("PTB7").startswith("oscillator pin"))
        self.assertEqual(package.special("PTD15"), "")

    def test_pattern_needs_a_name_start_or_underscore(self):
        pattern = next(p for kind, p, _ in nb.SPECIAL_PIN_KINDS if kind == "oscillator")
        for name in ("EXTAL", "XTAL", "OSCIN", "OSC32_IN", "RCC_OSC_IN", "RCC_OSC32_OUT", "rcc_osc_in"):
            self.assertIsNotNone(pattern.search(name), name)
        for name in ("LPOSC_TRIM", "CANOSCX", "PTA1", "FTM0_CH1"):
            self.assertIsNone(pattern.search(name), name)


if __name__ == "__main__":
    unittest.main()
