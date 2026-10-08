"""SYS-117: the RTE signal of the degraded reaction - safety.degraded, safety.degraded.<GROUP>, safety.qlf.degraded.

The application reads which safe-output groups the safe supervisor has put into their safe state
(doc/safety/partner-and-degraded.md section 4.1, step 3), through safety::degraded::rte::readGroups of
ecu/safety/safeSupervisor (DegradedRte.cpp). Checked here: the generated declaration and read per task, the bit of a
group from project.yaml safety.output_groups, the qualifier, every refused connection, and that an ECU description
without such a target gets no line of it.

Run: .venv/Scripts/python -m unittest tools/rtegen/test_rtegen_safety.py
"""

import copy
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))

import rtegen  # noqa: E402
import test_rtegen  # noqa: E402  (fixtures; its test classes are not imported into this module)

NO_CAN, PROJECT = test_rtegen.NO_CAN, test_rtegen.PROJECT

GROUPS = [{"name": "DRIVE_A", "pwm": "MOT1"}, {"name": "DRIVE_B", "pwm": "MOT2"}, {"name": "AUX", "pwm": "PUMP"}]


def with_groups(groups):
    """The demo configuration with safe-output groups (only their names and order matter to the RTE)."""
    config = copy.copy(PROJECT)
    project = PROJECT.project.model_copy(deep=True)
    project.safety = project.safety.model_copy(update={"output_groups": [
        rtegen.nxmodel.SafetyOutputGroup(**g) for g in groups]})
    config.project = project
    return config


class RteSafetyTest(unittest.TestCase):
    setUp, tearDown = test_rtegen.RteGenTest.setUp, test_rtegen.RteGenTest.tearDown

    def ecu(self, **connections):
        ecu = copy.deepcopy(NO_CAN)
        ecu["connections"].update(connections)
        return ecu

    def run_with(self, ecu, config=None):
        import yaml
        path = self.tmp / "rte.yaml"
        path.write_text(yaml.safe_dump(ecu), encoding="utf-8")
        return rtegen.generate(path, config if config is not None else with_groups(GROUPS), self.tmp / "out", REPO)

    def expect_error(self, ecu, fragment, config=None):
        with self.assertRaises(rtegen.RteError) as ctx:
            self.run_with(ecu, config)
        self.assertIn(fragment, str(ctx.exception))

    def generated(self, name="RteConfig.cpp"):
        return (self.tmp / "out" / name).read_text(encoding="utf-8")

    def test_without_safety_targets_no_safety_code(self):
        self.run_with(NO_CAN)
        self.assertNotIn("degraded", self.generated())

    def test_mask_group_and_qualifier(self):
        _, buffers, _ = self.run_with(self.ecu(**{
            "DemoSwc.MotorNtc_mV.MotorNtc_mV": "safety.degraded",
            "DemoSwc.Button1.Button1": "safety.degraded.AUX",
            "DemoSwc.PcCommandQlf.PcCommandQlf": "safety.qlf.degraded"}))
        config = self.generated()
        self.assertEqual((buffers["DemoSwc.Button1.Button1"].bsw, buffers["DemoSwc.Button1.Button1"].bsw_signal),
                         ("safety.degraded.", "0x04U"))
        self.assertEqual(config.count("bool readGroups(uint32_t& groups);"), 1)
        self.assertIn("bool const degradedPlausible = ::safety::degraded::rte::readGroups(degraded);", config)
        self.assertIn("Rte_Buf_DemoSwc_MotorNtc_mV_MotorNtc_mV = static_cast<uint32>(degraded);", config)
        self.assertIn("Rte_Buf_DemoSwc_Button1_Button1 = ((degraded & 0x04U) != 0U) ? 1U : 0U;", config)
        ok, error = rtegen.QLF["OK"], rtegen.QLF["ERROR"]
        self.assertIn(f"= static_cast<uint8>(degradedPlausible ? {ok}U : {error}U);", config)
        # one reading per task cycle, however many ports read it
        self.assertEqual(len(re.findall(r"readGroups\(degraded\)", config)), 1)

    def test_group_bits_follow_the_project_order(self):
        _, buffers, _ = self.run_with(self.ecu(**{"DemoSwc.Button1.Button1": "safety.degraded.DRIVE_A"}))
        self.assertEqual(buffers["DemoSwc.Button1.Button1"].bsw_signal, "0x01U")
        _, buffers, _ = self.run_with(self.ecu(**{"DemoSwc.Button1.Button1": "safety.degraded.DRIVE_B"}))
        self.assertEqual(buffers["DemoSwc.Button1.Button1"].bsw_signal, "0x02U")
        # without the qualifier the result of the reading is not kept (no unused variable, -Werror)
        config = self.generated()
        self.assertIn("(void)::safety::degraded::rte::readGroups(degraded);", config)
        self.assertNotIn("degradedPlausible", config)

    def test_target_errors(self):
        cases = [
            ({"DemoSwc.Button1.Button1": "safety.degraded.NOPE"}, "unknown safe-output group NOPE"),
            ({"DemoSwc.Button1.Button1": "safety.state"}, "unknown safety target"),
            ({"DemoSwc.Button1.Button1": "safety.degraded"}, "type boolean does not fit safety.degraded"),
            ({"DemoSwc.MotorNtc_mV.MotorNtc_mV": "safety.degraded.AUX"}, "type uint32 does not fit"),
            ({"DemoSwc.Button1.Button1": "safety.qlf.degraded"}, "type boolean does not fit safety.qlf.degraded"),
            ({"DemoSwc.Led1.Led1": "safety.degraded.AUX"}, "output port cannot use safety.degraded.AUX"),
        ]
        for connections, fragment in cases:
            with self.subTest(connections=connections):
                self.expect_error(self.ecu(**connections), fragment)
        # the demo itself has no safe-output groups: a group target names none
        self.expect_error(self.ecu(**{"DemoSwc.Button1.Button1": "safety.degraded.DRIVE_A"}),
                          "safety.output_groups is empty", PROJECT)

    def test_reader_signature_matches_the_supervisor(self):
        source = (REPO / "ecu/safety/safeSupervisor/src/safeSupervisor/DegradedRte.cpp").read_text(encoding="utf-8")
        self.assertIn("bool readGroups(uint32_t& groups)", source)
        self.assertIn("namespace degraded", source)
        self.assertIn("safety", rtegen.nxmodel.RTE_RESERVED_PREFIXES)


if __name__ == "__main__":
    unittest.main()
