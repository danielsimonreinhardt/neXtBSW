# SPDX-License-Identifier: Apache-2.0
"""IO-33: SBC pin wiring as board fact (board.yaml devices[].pins), battery monitor and switch-off as project values
(project.yaml modules.<sbc>), local wake-up inputs derived from the board - checked by nxconfig with YAML path.

Profile of DeviceCountTest in test_nxconfig.py (not the eval board): testboard_s32k_8mhz with a UJA1131HW/FD/5V/4Y
as transceiver of VEH on SPI_A. The grid arithmetic is compared with fixed values of the driver (Uja113xHostTest
batteryThresholdConversion, board measurement IO-10: 9020 / 17961 mV, window 314 mV).
"""

import copy
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import test_nxconfig as base  # noqa: E402

import nxconfig  # noqa: E402
from nxconfig import supply  # noqa: E402
from nxconfig.config import MCUDB  # noqa: E402


# SYS-95 (DFA-4 a): the synthetic testboard's PWM groups have no gate driver, so the pwmgate check would add its
# warning to every check of this module; the warning is tested in test_pwmgate.py and test_nxconfig.py
# (PwmGateInSyntheticProfileTest), here it is switched off in-process like in test_nxconfig.py.
def setUpModule():
    base._pwmgate_off.start()


def tearDownModule():
    base._pwmgate_off.stop()


class SupplyProfile(base.Profiles):
    SBC, BUSES = base.DeviceCountTest.SBC, base.DeviceCountTest.BUSES
    sbc_board = base.DeviceCountTest.sbc_board
    manifest_dirs = base.ModuleCheckTest.manifest_dirs

    def write(self, board=None, project=None) -> Path:
        path = super().write(board, project)
        text = path.read_text(encoding="utf-8").replace("boards/testboard_s32k_8mhz", "boards/testboard")
        path.write_text(text, encoding="utf-8")
        return path

    def check(self, board=None, project=None, mcudb=MCUDB, dirs=None):
        board = self.board_with() if board is None else board
        project = self.project_with() if project is None else project
        if dirs is None:
            return nxconfig.check(self.write(board, project), mcudb)
        return nxconfig.check(self.write(board, project), mcudb, dirs)

    def board_with(self, pins=None, part=None) -> dict:
        board = self.sbc_board(spi_devices=("SBC", None))
        if part is not None:
            board["devices"][0]["part"] = part
        if pins is not None:
            board["devices"][0]["pins"] = pins
        return board

    @staticmethod
    def project_with(sbc=None, ecu_state=None) -> dict:
        project = copy.deepcopy(base.S32K_PROJECT)
        project["use"]["spi"] = ["SPI_A"]
        modules = project.setdefault("modules", {})
        if sbc is not None:
            modules["SBC"] = sbc
        if ecu_state is not None:
            modules["ecuState"] = ecu_state
        return project

    def messages(self, board=None, project=None, severity=None) -> list:
        _, messages = self.check(board, project)
        return [m for m in messages if severity is None or m.severity == severity]

    def one(self, fragment, board=None, project=None, severity="error"):
        found = [m for m in self.messages(board, project, severity) if fragment in m.text]
        self.assertEqual(len(found), 1, [str(m) for m in self.messages(board, project)])
        self.assertIsNotNone(found[0].line, str(found[0]))
        return found[0]


class BatteryMonitorTest(SupplyProfile):
    def test_grid_is_the_driver_grid(self):
        # fixed values of the driver (Uja113xHostTest) and of the board measurement of IO-10
        self.assertEqual((supply.threshold_code(9000, True), supply.threshold_mv(115)), (115, 9020))
        self.assertEqual((supply.threshold_code(18000, False), supply.threshold_mv(229)), (229, 17961))
        self.assertEqual(supply.threshold_code(20000, False), 0xFF)
        self.assertEqual(supply.threshold_code(0, True), 0)
        self.assertEqual([supply.hysteresis_mv(supply.hysteresis_code(mv)) for mv in (0, 313, 314, 500, 1000, 4705,
                                                                                    4706, 4710)],
                         [0, 0, 314, 314, 941, 4392, 4706, 4706])
        # rounding never to the unsafe side over the whole range
        for mv in range(0, 20001, 7):
            self.assertGreaterEqual(supply.threshold_mv(supply.threshold_code(mv, True)) + 1, mv)
            self.assertLessEqual(supply.threshold_mv(supply.threshold_code(mv, False)) - 1, mv)

    def test_defaults_are_the_values_of_the_port_before(self):
        config, messages = self.check()
        self.assertEqual([m for m in messages if m.severity == "error"], [], [str(m) for m in messages])
        [sbc] = config.integration.instances("sbcUja113x")
        self.assertEqual(sbc.parameters, {"battery_source": "bat", "undervoltage_mv": 9000, "overvoltage_mv": 18000,
                                          "undervoltage_hysteresis_mv": 500, "overvoltage_hysteresis_mv": 500,
                                          "can_shutdown": "none", "v2_shutdown": "none", "shutdown_delay_ms": 0})
        [line] = [line for line in supply.info(config) if "battery monitor" in line]
        self.assertIn("undervoltage 9020 mV (release +314 mV), overvoltage 17961 mV (release -314 mV)", line)
        self.assertIn("CAN off: none, V2 off: none", line)

    def test_limits_of_the_data_sheet(self):
        cases = [
            ({"undervoltage_mv": 20001}, "modules.SBC.undervoltage_mv", "20001 is out of range 0..20000 mV"),
            ({"undervoltage_hysteresis_mv": 4711}, "modules.SBC.undervoltage_hysteresis_mv",
             "4711 is out of range 0..4710 mV"),
            ({"shutdown_delay_ms": -1}, "modules.SBC.shutdown_delay_ms", "-1 is out of range 0..10000 ms"),
            ({"can_shutdown": "always"}, "modules.SBC.can_shutdown", "'always' does not match"),
            ({"battery_source": "kl30"}, "modules.SBC.battery_source", "'kl30' does not match"),
            ({"undervoltage_mv": 18000}, "modules.SBC.undervoltage_mv",
             "constraint of module sbcUja113x not met: undervoltage_mv < overvoltage_mv"),
            ({"undervoltage_mv": 12000, "overvoltage_mv": 15000, "undervoltage_hysteresis_mv": 1600,
              "overvoltage_hysteresis_mv": 1600}, "modules.SBC.undervoltage_hysteresis_mv",
             "release windows of device SBC overlap: the undervoltage event ends at 13569 mV"),
        ]
        for values, path, fragment in cases:
            with self.subTest(values=values):
                m = self.one(fragment, project=self.project_with(values))
                self.assertEqual((m.file.name, m.yaml_path), ("project.yaml", path))
        # counter-check: windows that just do not overlap are accepted
        values = {"undervoltage_mv": 12000, "overvoltage_mv": 15000, "undervoltage_hysteresis_mv": 1200,
                  "overvoltage_hysteresis_mv": 1200}
        self.assertEqual(self.messages(project=self.project_with(values), severity="error"), [])

    def test_warnings(self):
        cases = [
            ({"undervoltage_mv": 1500}, "modules.SBC.undervoltage_mv", "below the input range of the battery monitor"),
            ({"can_shutdown": "undervoltage"}, "modules.SBC.can_shutdown",
             "can_shutdown undervoltage of device SBC with shutdown_delay_ms 0: the SBC switches CAN off within"),
            ({"v2_shutdown": "both"}, "modules.SBC.v2_shutdown", "switches V2 off within one conversion"),
            ({"can_shutdown": "undervoltage", "undervoltage_mv": 0, "shutdown_delay_ms": 50},
             "modules.SBC.can_shutdown", "undervoltage_mv is 0, the undervoltage event never comes"),
            ({"can_shutdown": "overvoltage", "overvoltage_mv": 20000}, "modules.SBC.can_shutdown",
             "the overvoltage event never comes"),
            ({"shutdown_delay_ms": 100}, "modules.SBC.shutdown_delay_ms", "shutdown_delay_ms 100 of device SBC has no "
                                                                          "effect"),
        ]
        for values, path, fragment in cases:
            with self.subTest(values=values):
                m = self.one(fragment, project=self.project_with(values), severity="warning")
                self.assertEqual((m.file.name, m.yaml_path), ("project.yaml", path))
        # counter-checks: the IO-13 case with a delay, overvoltage only without a delay - no warning
        for values in ({"can_shutdown": "undervoltage", "shutdown_delay_ms": 100}, {"can_shutdown": "overvoltage"},
                       {"undervoltage_mv": 2000}):
            with self.subTest(values=values):
                self.assertEqual(self.messages(project=self.project_with(values), severity="warning"), [])

    def test_info_names_the_delay(self):
        config, _ = self.check(project=self.project_with({"can_shutdown": "undervoltage", "shutdown_delay_ms": 100,
                                                          "battery_source": "batsense"}))
        [line] = [line for line in supply.info(config) if "battery monitor" in line]
        self.assertIn("battery monitor on BATSENSE", line)
        self.assertIn("CAN off: undervoltage, V2 off: none (after 100 ms by the driver)", line)

    def test_driver_of_an_older_tree_without_battery_monitor(self):
        # user test 2026-09-29: a customer repository pinned on neXtBSW 0.1.0 has an sbcUja113x without these
        # parameters - the Studio could not open the project (KeyError 'undervoltage_mv'); no check, no info
        from types import SimpleNamespace
        dev = SimpleNamespace(name="SBC", module="sbcUja113x", part="UJA1131", parameters={}, pins={})
        cfg = SimpleNamespace(integration=SimpleNamespace(devices=[dev], modules={}),
                              modules=SimpleNamespace(manifests={}))
        from nxconfig.config import Reporter
        rep = Reporter()
        supply.check(cfg, rep)
        self.assertEqual(supply.info(cfg), [])
        self.assertEqual(rep.messages, [])


class DevicePinTest(SupplyProfile):
    WIRED = {"HVIO1": {"use": "none"}, "HVIO2": {"use": "wake_input", "note": "WAKE button"},
             "HVIO4": {"use": "wake_input"}, "EN": {"use": "none"}}

    def test_wiring_is_checked_against_the_module(self):
        config, messages = self.check(self.board_with(self.WIRED))
        self.assertEqual([m for m in messages if m.severity == "error"], [], [str(m) for m in messages])
        [sbc] = config.integration.instances("sbcUja113x")
        self.assertEqual(sbc.pins, {"HVIO1": "none", "HVIO2": "wake_input", "HVIO4": "wake_input", "EN": "none"})
        cases = [
            ({"HVIO5": {"use": "none"}}, None, "devices[0].pins.HVIO5",
             "part UJA1131HW/FD/5V/4Y of device SBC has no pin HVIO5"),
            ({"HVIO1": {"use": "wake_input"}}, "UJA1131HW/FD/5V/0", "devices[0].pins.HVIO1",
             "part UJA1131HW/FD/5V/0 of device SBC has no pin HVIO1"),
            ({"HVIO9": {"use": "none"}}, None, "devices[0].pins.HVIO9", "module sbcUja113x has no pin HVIO9"),
            ({"EN": {"use": "output"}}, None, "devices[0].pins.EN.use",
             "module sbcUja113x has no use output for pin EN (uses: none)"),
        ]
        for pins, part, path, fragment in cases:
            with self.subTest(pins=pins, part=part):
                m = self.one(fragment, board=self.board_with(pins, part))
                self.assertEqual((m.file.name, m.yaml_path), ("board.yaml", path))
        # counter-check: the variant without partial networking has bank 1
        self.assertEqual(self.messages(self.board_with({"HVIO8": {"use": "wake_input"}}, "UJA1131HW/5V0"),
                                       severity="error"), [])

    def test_schema(self):
        m = self.one("Extra inputs are not permitted", board=self.board_with({"HVIO2": {"use": "wake_input",
                                                                                          "pull": "up"}}))
        self.assertEqual((m.file.name, m.yaml_path), ("board.yaml", "devices[0].pins.HVIO2.pull"))

    def test_device_without_module(self):
        board = self.board_with()
        board["devices"].append({"name": "FOO", "part": "NOTHING1", "pins": {"X": {"use": "none"}}})
        m = self.one("device FOO has no module (no module.yaml lists part NOTHING1), pins needs one", board=board)
        self.assertEqual(m.yaml_path, f"devices[{len(board['devices']) - 1}].pins")

    def test_manifest_rules(self):
        dirs = self.manifest_dirs(pinTest="\n".join([
            "module: pinTest", "kind: device_driver", "parts: [PIN*]",
            "pins:",
            "  A: { uses: [none], local_wakeup: 0 }",
            "  B: { uses: [none, wake_input], local_wakeup: 1 }",
            "  C: { uses: [none, wake_input], local_wakeup: 1 }", ""]))
        _, messages = self.check(dirs=dirs)
        texts = [m.text for m in messages if m.severity == "error" and m.file and m.file.name == "module.yaml"]
        self.assertTrue(any("local_wakeup needs the use wake_input (uses of A: none)" in t for t in texts), texts)
        self.assertTrue(any("local wake-up input 1 is already pin B" in t for t in texts), texts)


class LocalWakeupTest(SupplyProfile):
    WIRED = DevicePinTest.WIRED

    def test_inputs_come_from_the_board(self):
        config, _ = self.check(self.board_with(self.WIRED))
        dev, inputs = supply.local_wakeup_pins(config)
        self.assertEqual(dev.name, "SBC")
        self.assertEqual(inputs, {0: ("HVIO1", "none"), 1: ("HVIO2", "wake_input"), 2: ("HVIO3", None),
                                  3: ("HVIO4", "wake_input")})      # HVIO5..8: not on the /FD/4 part
        self.assertIn("SBC local wake-up inputs wired by the board: LOCAL1 = HVIO2, LOCAL3 = HVIO4; selected "
                      "(modules.ecuState.wakeup_local): none", supply.info(config))

    def test_selection_against_the_board(self):
        board = self.board_with(self.WIRED)
        for value in ("none", "all", "LOCAL1", "LOCAL1,LOCAL3"):
            with self.subTest(wakeup_local=value):
                self.assertEqual(self.messages(board, self.project_with(ecu_state={"wakeup_local": value})), [])
        cases = [
            ("LOCAL0", "error", "wakeup_local: LOCAL0 is pin HVIO1 of SBC, which board testboard_s32k_8mhz wires as none, not "
                                "as wake_input (devices[].pins.HVIO1)"),
            ("LOCAL1,LOCAL5", "error", "wakeup_local: LOCAL5 is no local wake-up input of SBC (UJA1131HW/FD/5V/4Y; "
                                       "inputs of the part: LOCAL0 = HVIO1, LOCAL1 = HVIO2, LOCAL2 = HVIO3, "
                                       "LOCAL3 = HVIO4)"),
            ("LOCAL2", "warning", "wakeup_local: LOCAL2 is pin HVIO3 of SBC, which board testboard_s32k_8mhz does not describe"),
        ]
        for value, severity, fragment in cases:
            with self.subTest(wakeup_local=value):
                m = self.one(fragment, board, self.project_with(ecu_state={"wakeup_local": value}), severity)
                self.assertEqual((m.file.name, m.yaml_path), ("project.yaml", "modules.ecuState.wakeup_local"))

    def test_board_without_wiring(self):
        # nothing described: every named input is a warning (the supply offers none), as before IO-33 at run time
        m = self.one("LOCAL1 is pin HVIO2 of SBC, which board testboard_s32k_8mhz does not describe", self.board_with(),
                     self.project_with(ecu_state={"wakeup_local": "LOCAL1"}), "warning")
        self.assertEqual(m.yaml_path, "modules.ecuState.wakeup_local")


class EvalBoardTest(unittest.TestCase):
    """The eval board describes its SBC pins (user 2026-09-18); the demo keeps its former battery values."""

    def test_demo(self):
        config, messages = nxconfig.check(base.DEMO_PROJECT)
        self.assertEqual([m for m in messages if m.severity != "info"], [])
        [sbc] = config.integration.instances("sbcUja113x")
        self.assertEqual(sbc.pins, {"HVIO1": "none", "HVIO2": "wake_input", "HVIO3": "wake_input",
                                    "HVIO4": "wake_input", "EN": "none"})
        self.assertEqual(supply.effective(sbc.parameters), {"undervoltage_mv": 9020, "overvoltage_mv": 17961,
                                                            "undervoltage_hysteresis_mv": 314,
                                                            "overvoltage_hysteresis_mv": 314})
        self.assertEqual((sbc.parameters["can_shutdown"], sbc.parameters["v2_shutdown"]), ("none", "none"))


if __name__ == "__main__":
    unittest.main()
