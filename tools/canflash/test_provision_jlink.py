"""J-Link Commander scripts of provision.py keep the DWT running after the session (PLAT-86).

The J-Link clears DEMCR.TRCENA when it closes a debug session ("debug de-init") and stops the DWT cycle counter, the
time base of the application (doc/hardware/debug-session-close.md, board finding PLAT-67). Every script that ends with
the application running must therefore contain `exec SetSkipDebugDeInit = 1` after `connect` - with the value, the
form without it is rejected with "Expected a dec value" (board 2026-09-22).

Checked without hardware, for JTAG and SWD and two derivatives: the UID script and the provisioning script have the
line exactly once, right after `connect` and before any command that touches the target, and they end with `g` and
`exit`.

Run: .venv/Scripts/python -m unittest tools/canflash/test_provision_jlink.py
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import provision  # noqa: E402

EXPECTED = "exec SetSkipDebugDeInit = 1"   # stated here, not taken from provision.py


def plan(device: str, interface: str) -> "provision.Plan":
    return provision.Plan(Path("project"), "test", "test", bytes(range(16)), 0x7E000, 0x1000, bytes(48), device,
                          interface, "1000", 0x40048054, 16)


class SkipDebugDeInitTest(unittest.TestCase):
    CASES = (("S32K144", "JTAG"), ("S32K148", "SWD"), ("STM32F446RE", "SWD"))

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="provision_jlink_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def scripts(self, device: str, interface: str) -> dict[str, str]:
        files = provision.write_files(plan(device, interface), self.tmp / f"{device}_{interface}")
        return {"uid": provision.uid_script(device, interface, "1000", 0x40048054, 16),
                "provision": files["script"].read_text(encoding="ascii")}

    def test_every_script_keeps_the_dwt_running(self):
        for device, interface in self.CASES:
            for name, text in self.scripts(device, interface).items():
                with self.subTest(device=device, interface=interface, script=name):
                    lines = text.splitlines()
                    self.assertEqual(lines.count(EXPECTED), 1, text)
                    at = lines.index("connect")
                    self.assertEqual(lines[at + 1], EXPECTED, "directly after connect")
                    head = lines[:at]
                    self.assertTrue(all(line.split()[0] in ("device", "si", "speed", "jtagconf") for line in head),
                                    head)
                    self.assertEqual(lines[-2:], ["g", "exit"], "the application runs after the script")

    def test_the_command_carries_its_value(self):
        """Without the value J-Link Commander stops the script (-ExitOnError) with 'Expected a dec value'."""
        for device, interface in self.CASES:
            for name, text in self.scripts(device, interface).items():
                with self.subTest(device=device, interface=interface, script=name):
                    for line in text.splitlines():
                        if "SetSkipDebugDeInit" in line:
                            self.assertRegex(line, r"^exec SetSkipDebugDeInit = 1$")


if __name__ == "__main__":
    unittest.main()
