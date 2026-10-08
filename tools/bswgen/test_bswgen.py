"""Unit tests for tools/bswgen against the eval board demo project (board and project copied and modified).

Structure, reference and resource checks belong to tools/nxconfig (test_nxconfig.py with synthetic profiles);
here the s32k1xx driver checks, the generated headers and that nxconfig findings reach the generator are tested.

Run: .venv/Scripts/python -m unittest tools/bswgen/test_bswgen.py
"""

import copy
import functools
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))

import bswgen  # noqa: E402
import modules  # noqa: E402
import nxconfig  # noqa: E402
from nxconfig import can as nxcan  # noqa: E402
from nxconfig import sensor as nxsensor  # noqa: E402
from nxconfig import uart as nxuart  # noqa: E402

# PLAT-62: the own full-featured test profile (synthetic testboard_full/testproject_full) instead of the eval board,
# which stays private (PLAT-46 F10) - a test against it would be red in the exported open tree. What proves something
# about the eval board itself uses EVAL_BOARD_FILE/EVAL_PROJECT_FILE and is skipped where they are missing.
PROFILES = REPO / "tools" / "nxconfig" / "test" / "profiles"
BOARD_FILE = PROFILES / "boards" / "testboard_full" / "board.yaml"
PROJECT_FILE = PROFILES / "projects" / "testproject_full" / "project.yaml"
EVAL_BOARD_FILE = REPO / "boards" / "evalboard_s32k144" / "board.yaml"
EVAL_PROJECT_FILE = REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml"
EVAL_CHARGE_FILE = REPO / "projects" / "evalboard_s32k144_charge" / "project.yaml"
eval_board = unittest.skipUnless(EVAL_PROJECT_FILE.is_file(),
                                 "private eval board project not in this tree (exported open tree, PLAT-62)")


def on_eval_board(test):
    """PLAT-62: the test states values of the eval board itself (its pins, channels, crystal, clock, wiring) - it runs
    against the eval board where it exists (private tree) and is skipped in the exported open tree. The module
    constants BOARD/PROJECT/BOARD_FILE/PROJECT_FILE point to the eval board while the test runs; everything else in
    this file runs on the own full test profile."""

    @functools.wraps(test)
    def run(self, *args, **kwargs):
        if not EVAL_PROJECT_FILE.is_file():
            raise unittest.SkipTest("private eval board project not in this tree (exported open tree, PLAT-62)")
        module = globals()
        saved = {name: module[name] for name in ("BOARD_FILE", "PROJECT_FILE", "BOARD", "PROJECT")}
        module.update(BOARD_FILE=EVAL_BOARD_FILE, PROJECT_FILE=EVAL_PROJECT_FILE,
                      BOARD=yaml.safe_load(EVAL_BOARD_FILE.read_text(encoding="utf-8")),
                      PROJECT=yaml.safe_load(EVAL_PROJECT_FILE.read_text(encoding="utf-8")))
        try:
            return test(self, *args, **kwargs)
        finally:
            module.update(saved)

    return run


def existing(*cases):
    """PLAT-62: the cases whose project file (first element, or the case itself) exists - the eval board projects
    are private and missing in the exported open tree, the other cases run there as well."""
    return [c for c in cases if (c[0] if isinstance(c, tuple) else c).is_file()]
BOARD = yaml.safe_load(BOARD_FILE.read_text(encoding="utf-8"))
PROJECT = yaml.safe_load(PROJECT_FILE.read_text(encoding="utf-8"))
# IO-24 expected the warning "PWM group MOT1 has no power_stage" ([("warning", "pwm[0]")]) on every check of the demo
# project. IO-26 entered the facts of the MOT1 power stage and the project margin: no message any more.
EVAL_OPEN_POWER_STAGE = []


def findings(messages) -> list[tuple[str, str]]:
    return [(m.severity, m.yaml_path) for m in messages]


def without_rte(project: dict) -> dict:
    """A copy of a project checked without its ECU description and diagnostics (the copy has no rte.yaml/diag.yaml
    next to it, ARCH-51). SYS-87: without the ECU description nothing writes the indicator, so the demo's
    confirmation that the application does would be reported as superfluous - true, but not what these tests are
    about (as with_empty_rte in tools/nxconfig/test_nxconfig.py)."""
    project["project"].pop("rte", None)
    project["project"].pop("diag", None)
    (project.get("safety") or {}).pop("safe_state_output_shared_with_application", None)
    return project


def with_rank_of(project: dict, bus: str) -> dict:
    """ARCH-33: the CAN ranks of the project for exactly the bus `bus` (rank 3 like the demo project)."""
    ranks = project["interrupts"]["ranks"]
    ranks[:] = [r for r in ranks if r["source"] != "can"] + [{"source": "can", "name": bus, "rank": 3}]
    return project


def one_bus_demo() -> dict:
    """COM-05: the demo project with CAN0 only, as before the diagnostic CAN (CAN1, FlexCAN2) was switched on."""
    project = copy.deepcopy(PROJECT)
    project["use"]["can"] = ["CAN0"]
    return with_rank_of(project, "CAN0")


def scale(raw: int, num: int, den: int, offset: int) -> int:
    """Python model of bios::scaleLinear (AnalogInputScaleLinear.h)."""
    return min(max(num * raw // den + offset, 0), 0xFFFFFFFF)


class BswGenAdcTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_with(self, board=None, project=None, mcudb=None):
        board_dir, project_dir = self.tmp / "boards" / "b", self.tmp / "projects" / "p"
        board_dir.mkdir(parents=True, exist_ok=True)
        project_dir.mkdir(parents=True, exist_ok=True)
        board = self.board() if board is None else board
        (board_dir / "board.yaml").write_text(yaml.safe_dump(board, sort_keys=False), encoding="utf-8")
        project = copy.deepcopy(self.project() if project is None else project)
        project["project"]["board"] = "../../boards/b"
        without_rte(project)
        path = project_dir / "project.yaml"
        path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        return bswgen.generate(path, self.tmp / "out", *([mcudb] if mcudb else []))

    def read(self, rel):
        return (self.tmp / "out" / "bsp" / "adc" / rel).read_text(encoding="utf-8")

    def expect_error(self, board=None, fragment=None, project=None, mcudb=None):
        with self.assertRaises(bswgen.ConfigError) as ctx:
            self.run_with(board, project, mcudb)
        self.assertIn(fragment, str(ctx.exception))

    def board(self):
        return copy.deepcopy(BOARD)

    def project(self):
        return copy.deepcopy(PROJECT)

    # --- generated headers -------------------------------------------------------------------------

    @on_eval_board
    def test_all_board_channels_are_generated(self):
        self.run_with()
        enum = self.read("analogInputConfiguration.h")
        names = re.findall(r"\*/ Ai_(\w+),", enum)
        expected = [bswgen.identifier(c["name"]) for c in BOARD["adc"]] + ["BANDGAP", "ADCINTERNAL0"]
        self.assertEqual(names, expected)
        self.assertIn("Ai_SIG_12VBAT_CURRENT", enum)
        self.assertIn("ADC0_CHANNEL_COUNT = 7U", enum)
        self.assertIn("ADC1_CHANNEL_COUNT = 9U", enum)
        strings = re.findall(r'"(Ai_\w+)"', self.read("analogInputConfigurationStrings.h"))
        self.assertEqual(strings, [f"Ai_{n}" for n in expected])
        scales = re.findall(r"&scaleLinear<(\d+)U, (\d+)U, (-?\d+)>, // Ai_(\w+)",
                            self.read("analogInputScaleConfiguration.h"))
        self.assertEqual([s[3] for s in scales], expected)
        self.assertIn("from project.yaml (board evalboard_s32k144)", enum)  # outside the repository: file name only

    def test_only_selected_channels_are_generated(self):
        project = self.project()
        project["use"]["adc"] = ["BEMF_U", "MOTOR_NTC"]
        del project["modules"]["KL30B_SW"]             # its current sense channel is no longer used
        project.pop("adc_sampling", None)              # averages channels that are no longer used (IO-31)
        board = self.run_with(project=project)
        self.assertEqual([c["name"] for c in board.adc], ["MOTOR_NTC", "BEMF_U", "BANDGAP", "ADCINTERNAL0"])
        self.assertNotIn("ADC_CURRENT_U", board.pins)

    @on_eval_board
    def test_channel_table_rows(self):
        self.run_with()
        cfg = self.read("analogInputConfiguration.h")
        self.assertIn("{ADC_0_CH, _ADCCHANNEL0, Io::MOTOR_NTC, _ADCCHANNEL0}", cfg)
        self.assertIn("{ADC_0_CH, _ADCCHANNEL9, Io::SIG_12VBAT_CURRENT, _ADCCHANNEL2}", cfg)
        self.assertIn("{ADC_1_CH, _ADCCHANNEL0, Io::ADC_CURRENT_U, _ADCCHANNEL0}", cfg)
        self.assertIn("{ADC_1_CH, _ADCCHANNEL12, Io::PROXY_ANALOG, _ADCCHANNEL8}", cfg)
        # Safety channels keep the hand-written configuration (same ADCH input and SC1 slot)
        self.assertIn("{ADC_0_CH, _BANDGAP, Io::PORT_UNAVAILABLE, _ADCCHANNEL13}", cfg)
        self.assertIn("{ADC_0_CH, _ADCINTERNAL0, Io::PORT_UNAVAILABLE, _ADCCHANNEL14}", cfg)

    @on_eval_board
    def test_default_scale_matches_previous_configuration(self):
        doc = self.board()
        for c in doc["adc"]:
            c["scale"] = {"factor": 1}                 # board profile carries real dividers since IO-12
        board = self.run_with(doc)
        for c in board.adc:
            self.assertEqual((c["num"], c["den"], c["offset"]), (4096, 4095, 0), c["name"])

    @on_eval_board
    def test_board_dividers_from_schematic(self):
        board = {c["name"]: c for c in self.run_with().adc}
        for name, full_scale in (("SENSE_12VGATE", 14598), ("BEMF_U", 48779), ("PILOT_ANALOG", 15639)):
            c = board[name]
            self.assertEqual(scale(4095, c["num"], c["den"], c["offset"]), full_scale, name)
        # previous: scaleFunction<uint32_t, 4096 * 1000 / 4095, 1000, 0> -> raw * 1000 / 1000
        for raw in range(4095):
            self.assertEqual(scale(raw, 4096, 4095, 0), raw * (4096 * 1000 // 4095) // 1000)
        self.assertEqual(scale(4095, 4096, 4095, 0), 4096)

    def test_divider_factor_and_offset(self):
        doc = self.board()
        doc["adc"][0]["scale"] = {"divider": {"r_top": 100000, "r_bottom": 10000}}
        doc["adc"][1]["scale"] = {"factor": 2.5, "offset_mv": -1250}
        doc["board"]["adc_reference_mv"] = 5000
        board = self.run_with(doc)
        c0, c1 = board.adc[0], board.adc[1]
        self.assertEqual(scale(4095, c0["num"], c0["den"], c0["offset"]), 55000)
        self.assertEqual(scale(0, c1["num"], c1["den"], c1["offset"]), 0)  # clamped
        self.assertEqual(scale(4095, c1["num"], c1["den"], c1["offset"]), 11250)
        text = self.read("analogInputScaleConfiguration.h")
        self.assertIn(f"&scaleLinear<{c1['num']}U, {c1['den']}U, -1250>", text)

    def test_output_is_stable(self):
        self.run_with()
        first = {p.name: p.read_text(encoding="utf-8") for p in (self.tmp / "out" / "bsp" / "adc").iterdir()}
        self.run_with()
        second = {p.name: p.read_text(encoding="utf-8") for p in (self.tmp / "out" / "bsp" / "adc").iterdir()}
        self.assertEqual(first, second)

    # --- validation --------------------------------------------------------------------------------

    def test_unknown_instance(self):
        doc = self.board()
        doc["adc"][0]["instance"] = "ADC2"
        self.expect_error(doc, "S32K144 has no instance ADC2")

    def test_unknown_internal_instance(self):
        project = self.project()
        project["adc_internal"][0]["instance"] = "ADC7"
        self.expect_error(project=project, fragment="has no ADC instance ADC7")

    def test_unknown_channel(self):
        doc = self.board()
        doc["adc"][0]["channel"] = "SE40"
        self.expect_error(doc, "unknown channel 'SE40'")
        doc["adc"][0]["channel"] = "CH0"
        self.expect_error(doc, "unknown channel 'CH0'")

    @on_eval_board
    def test_channel_not_on_pin(self):
        doc = self.board()
        doc["adc"][0]["channel"] = "SE1"  # ADC0_SE1 is PTA1, not PTA0
        self.expect_error(doc, "has no function ADC0_SE1 on PTA0")

    def test_unknown_internal_channel(self):
        project = self.project()
        project["adc_internal"][0]["channel"] = "TEMPSENSOR"
        self.expect_error(project=project, fragment="unknown internal channel 'TEMPSENSOR'")

    def test_reserved_internal_channels_of_adc1(self):
        # [RM13] Table 43-3: internal channels 0..3 of ADC1 are reserved (ARCH-21)
        for channel in ("INTERNAL0", "INTERNAL1", "INTERNAL2", "INTERNAL3"):
            with self.subTest(channel=channel):
                project = self.project()
                project["adc_internal"].append({"name": "RESERVED", "instance": "ADC1", "channel": channel})
                self.expect_error(project=project, fragment=f"unknown internal channel '{channel}' of ADC1 "
                                                            "(available: BANDGAP, VREFSH, VREFS)")

    @on_eval_board
    def test_duplicate_channel(self):
        doc = self.board()
        doc["adc"].append(dict(doc["adc"][0], name="OTHER"))
        self.expect_error(doc, "ADC0 SE0 is used by adc OTHER and by adc MOTOR_NTC")
        doc["adc"][-1]["pin"] = "PTA1"  # ADC0_SE0 exists only on PTA0
        self.expect_error(doc, "S32K144 LQFP100 has no function ADC0_SE0 on PTA1")
        project = self.project()
        project["adc_internal"].append({"name": "BANDGAP2", "instance": "ADC0", "channel": "BANDGAP"})
        self.expect_error(project=project, fragment="ADC0 BANDGAP is used by adc_internal BANDGAP2")

    def test_duplicate_name(self):
        project = self.project()
        project["adc_internal"][0]["name"] = "MOTOR_NTC"
        self.expect_error(project=project, fragment="duplicate ADC channel name MOTOR_NTC")

    @on_eval_board
    def test_too_many_channels(self):
        # All 16 external channels of ADC1 (S32K144 LQFP100) as board channels; the reserved internal channels of
        # ADC1 cannot fill it ([RM13] Table 43-3). The PWM group is dropped to free PTB12..PTB16.
        doc, project = self.board(), self.project()
        doc.pop("pwm")
        project.pop("pwm")
        project.pop("adc_sampling", None)  # averages over MOT1, which this test changes (IO-31)
        extra = {"SE6": "PTD4", "SE7": "PTB12", "SE8": "PTB13", "SE9": "PTB14", "SE13": "PTA16", "SE14": "PTB15",
                 "SE15": "PTB16"}
        doc["adc"] += [{"name": f"ADC1_{ch}", "instance": "ADC1", "channel": ch, "pin": pin}
                       for ch, pin in extra.items()]
        self.assertEqual(sum(c["instance"] == "ADC1" for c in doc["adc"]), bswgen.ADC_SLOTS)
        self.expect_error(doc, project=project, fragment="ADC1: 16 channels, at most 15 supported")

    @on_eval_board
    def test_invalid_scale(self):
        cases = [
            ({"factor": 0}, "greater than 0"),
            ({"factor": -2}, "greater than 0"),
            ({"factor": "x"}, "valid number"),
            ({"factor": float("inf")}, "finite number"),
            ({"factor": 1, "divider": {"r_top": 1, "r_bottom": 1}}, "both factor and divider"),
            ({"divider": {"r_top": 1000, "r_bottom": 0}}, "greater than 0"),
            ({"divider": {"r_top": 1000}}, "Field required"),
            ({"offset_mv": 1.5}, "valid integer"),
            ({"offset_mv": 1 << 40}, "less than or equal"),
            ({"gain": 2}, "Extra inputs are not permitted"),
            ({"factor": 1.234567891}, "too precise"),
            ({"factor": 2000000}, "exceeds uint32"),
            ({"factor": 1000000, "offset_mv": (1 << 31) - 1}, "exceeds uint32"),
        ]
        for spec, fragment in cases:
            with self.subTest(spec=spec):
                doc = self.board()
                doc["adc"][0]["scale"] = spec
                self.expect_error(doc, fragment)

    def test_invalid_reference(self):
        mcudb = self.tmp / "mcudb"
        shutil.copytree(REPO / "mcudb", mcudb)
        derivative = mcudb / "s32k1xx" / "S32K144.yaml"
        text = derivative.read_text(encoding="utf-8")
        derivative.write_text(text.replace("resolution_bits: 12", "resolution_bits: 10"), encoding="utf-8")
        self.expect_error(fragment="configured for 12 bit, ADC0 of S32K144 has 10 bit", mcudb=mcudb)
        doc = self.board()
        doc["board"]["adc_reference_mv"] = 0
        self.expect_error(doc, "greater than 0")
        doc["board"].pop("adc_reference_mv")
        self.expect_error(doc, "defines no board.adc_reference_mv")


class BswGenPwmTest(unittest.TestCase):
    """pwm: complementary FTM group, register calculation and validation (bsp/io/outputPwm)."""

    setUp, tearDown = BswGenAdcTest.setUp, BswGenAdcTest.tearDown
    run_with, expect_error = BswGenAdcTest.run_with, BswGenAdcTest.expect_error
    board, project = BswGenAdcTest.board, BswGenAdcTest.project

    def read(self, rel):
        return (self.tmp / "out" / "bsp" / "io" / "outputPwm" / rel).read_text(encoding="utf-8")

    def group(self, doc):
        return doc["pwm"][0]

    def use(self, project):
        return project["pwm"]["MOT1"]

    @on_eval_board
    def test_board_profile(self):
        board = self.run_with()
        p = board.pwm
        # 80 MHz / 20 kHz = 4000 ticks; 1000 ns = 80 clock ticks = 20 x DTPS 4
        self.assertEqual((p["prescaler_shift"], p["period_ticks"]), (0, 4000))
        self.assertEqual((p["deadtime_dtps"], p["deadtime_div"], p["deadtime_ticks"]), (2, 4, 20))
        self.assertEqual((p["channel_mask"], p["pol"]), (0x3F, 0))
        self.assertEqual([(q["name"], q["high_channel"]) for q in p["pairs"]],
                         [("MOT1_U", 0), ("MOT1_V", 2), ("MOT1_W", 4)])
        enum = self.read("outputPwmConfiguration.h")
        self.assertIn("MOT1_U = _pwmDynamicStart,", enum)
        self.assertEqual(re.findall(r'"(\w+)"', self.read("outputPwmConfigurationStrings.h")),
                         ["dummyPwm", "MOT1_U", "MOT1_V", "MOT1_W"])
        cfg = self.read("ftmPwmConfiguration.h")
        self.assertIn("PERIOD_TICKS       = 4000U;", cfg)
        self.assertIn("DEADTIME_TICKS     = 20U;", cfg)
        self.assertIn("(COMBINE_PAIR << 0U) | (COMBINE_PAIR << 8U) | (COMBINE_PAIR << 16U)", cfg)
        # Ftm::init() writes PAIRnDEADTIME after DEADTIME: 0 there removed the dead time on the board (scope)
        for i in range(3):
            self.assertIn(f"/* PAIR{i}DEADTIME */ FTM_PAIR{i}DEADTIME_DTPS(DEADTIME_DTPS) | "
                          f"FTM_PAIR{i}DEADTIME_DTVAL(DEADTIME_TICKS),", cfg)
        self.assertIn("/* PAIR3DEADTIME */ 0U,", cfg)
        self.assertIn("{OutputPwm::MOT1_W, 4U, Io::MOT1_W_PWM_HS, Io::MOT1_W_PWM_LS},", cfg)
        self.assertIn("inline FTM_Type& ftm() { return *FTM0; }", cfg)
        self.assertIn("verify: true", cfg)
        io = (self.tmp / "out" / "bsp" / "io" / "io" / "ioConfiguration.h").read_text(encoding="utf-8")
        self.assertIn("{_PORTB_, PB12, _OUT, 0, (uint32_t) ALT2}", io)  # FTM0_CH0
        self.assertNotIn("PE8,", io)                                    # PILOT_DUTY unused (shares FTM0)

    def test_verify_from_board_or_project(self):
        doc, project = self.board(), self.project()
        self.group(doc)["verify"] = False
        self.use(project)["verify"] = False
        self.assertFalse(self.run_with(doc, project).pwm["verify"])
        self.use(project)["verify"] = True
        self.assertTrue(self.run_with(doc, project).pwm["verify"])

    def test_prescaler_rounding_and_polarity(self):
        doc, project = self.board(), self.project()
        u = self.use(project)
        u["frequency_hz"], u["deadtime_ns"] = 1000, 5000       # 80000 ticks > 16 bit -> PS 1 (40000)
        self.group(doc)["active"] = {"high_side": "high", "low_side": "low"}
        p = self.run_with(doc, project).pwm
        self.assertEqual((p["prescaler_shift"], p["period_ticks"]), (1, 40000))
        self.assertEqual((p["deadtime_div"], p["deadtime_ticks"]), (16, 25))  # 400 ticks: /4 = 100 > 63
        self.assertEqual(p["pol"], 0b101010)
        u["frequency_hz"], u["deadtime_ns"] = 19000, 1001      # 4210.5 -> 4211 ticks; 80.08 -> 21 x 4
        p = self.run_with(doc, project).pwm
        self.assertEqual((p["period_ticks"], p["deadtime_ticks"]), (4211, 21))
        self.assertIn("realised 18997.863 Hz", self.read("ftmPwmConfiguration.h"))
        self.assertIn("realised 1050.0 ns", self.read("ftmPwmConfiguration.h"))

    def test_without_pwm_group(self):
        """ARCH-17: a project without a pwm section (board without a motor bridge) gets 0 groups, no #error."""
        project = self.project()
        project["pwm"] = {}                                    # board group stays, the project does not use it
        project.pop("adc_sampling", None)  # averages over MOT1, which this test changes (IO-31)
        board = self.run_with(project=project)
        self.assertIsNone(board.pwm)
        self.assertNotIn("MOT1_U_PWM_HS", board.pins)
        self.assertEqual(re.findall(r'"(\w+)"', self.read("outputPwmConfigurationStrings.h")), ["dummyPwm"])
        self.assertIn("_pwmDynamicMark  = _pwmDynamicStart,", self.read("outputPwmConfiguration.h"))
        cfg = self.read("ftmPwmConfiguration.h")
        self.assertNotIn("#error", cfg)                        # ARCH-09 B4: the #error broke the link of the board
        self.assertIn("#define NX_PWM_GROUP_COUNT 0\n", cfg)
        self.assertIn("#define NX_PWM_GROUP_INSTANCES(X)\n", cfg)
        # nothing of the driver: no FTM include, no register values, so the header costs nothing on a board
        # whose port does not link bspFtm either
        self.assertNotIn("#include", cfg)
        self.assertNotIn("PERIOD_TICKS", cfg)
        # the header is what the port asks (#if NX_PWM_GROUP_COUNT > 0): 0 must be the literal after the name
        self.assertRegex(cfg, r"(?m)^#define NX_PWM_GROUP_COUNT 0$")
        # the FTM0 counter is free without the group: the input capture channel sharing it is now usable
        project["use"]["input_capture"] = "all"
        self.assertIn("PILOT_DUTY", self.run_with(project=project).pins)

    @on_eval_board
    def test_pwm_group_chosen_by_the_project(self):
        """Board with two groups (FTM0 and FTM3): the project picks one, the generat holds exactly that one."""
        doc, project = self.board(), self.project()
        self.add_second_group(doc, project)
        use = dict(self.use(project))
        project["pwm"] = {"MOT2": use}                          # only the second group is used
        project.pop("adc_sampling", None)  # averages over MOT1, which this test changes (IO-31)
        board = self.run_with(doc, project)
        self.assertEqual((board.pwm["name"], board.pwm["instance"]), ("MOT2", "FTM3"))
        cfg = self.read("ftmPwmConfiguration.h")
        self.assertIn("#define NX_PWM_GROUP_COUNT 1\n", cfg)
        self.assertIn("#define NX_PWM_GROUP_INSTANCES(X) X(MOT2)\n", cfg)
        self.assertIn("inline FTM_Type& ftm() { return *FTM3; }", cfg)
        enum = self.read("outputPwmConfiguration.h")
        self.assertIn("MOT2_U = _pwmDynamicStart,", enum)
        self.assertNotIn("MOT1_U", enum)                        # the unused group leaves no channel behind
        self.assertEqual(re.findall(r'"(\w+)"', self.read("outputPwmConfigurationStrings.h")),
                         ["dummyPwm", "MOT2_U", "MOT2_V", "MOT2_W"])
        io = (self.tmp / "out" / "bsp" / "io" / "io" / "ioConfiguration.h").read_text(encoding="utf-8")
        self.assertIn("{_PORTB_, PB8, _OUT, 0, (uint32_t) ALT2}", io)   # FTM3_CH0 of the used group
        self.assertNotIn("PB12,", io)                                   # FTM0_CH0 of the unused group
        # the other group of the same board: same board, only the project entry changes
        project["pwm"] = {"MOT1": use}
        board = self.run_with(doc, project)
        self.assertEqual((board.pwm["name"], board.pwm["instance"]), ("MOT1", "FTM0"))
        self.assertIn("#define NX_PWM_GROUP_INSTANCES(X) X(MOT1)\n", self.read("ftmPwmConfiguration.h"))
        self.assertIn("MOT1_U = _pwmDynamicStart,", self.read("outputPwmConfiguration.h"))

    def test_output_is_stable(self):
        self.run_with()
        first = {p.name: p.read_text(encoding="utf-8") for p in (self.tmp / "out" / "bsp" / "io" / "outputPwm").iterdir()}
        self.run_with()
        second = {p.name: p.read_text(encoding="utf-8") for p in (self.tmp / "out" / "bsp" / "io" / "outputPwm").iterdir()}
        self.assertEqual(first, second)

    def test_invalid_parameters(self):
        """Project values against the FTM counter limits and the schema (both nxconfig since ARCH-33)."""
        cases = [
            # 50 kHz = 1600 ticks, 10000 ns = 800 ticks: 2 x 800 >= 1600
            (lambda u: u.update(frequency_hz=50_000, deadtime_ns=10_000),
             "(pwm.MOT1.deadtime_ns): error: PWM group MOT1: dead time 10000 ns must be shorter than half the PWM "
             "period (10000 ns at 50000 Hz): two dead times per period"),
            (lambda u: u.update(frequency_hz=50_000, deadtime_ns=9_999), None),  # 50 x 16 = 800 -> still too long
            (lambda u: u.update(frequency_hz=50_000, deadtime_ns=9_800), "ok"),  # 49 x 16 = 784 < 800
            (lambda u: u.update(deadtime_ns=12_601),
             "(pwm.MOT1.deadtime_ns): error: PWM group MOT1: dead time 12601 ns exceeds the FlexTimer maximum of "
             "12600 ns (63 x 16 periods of the counter clock 80000000 Hz of FTM0)"),
            (lambda u: u.update(frequency_hz=900_000),
             "(pwm.MOT1.frequency_hz): error: PWM group MOT1: frequency 900000 Hz leaves 89 counter ticks per "
             "period at the counter clock 80000000 Hz of FTM0, at least 100 needed (duty resolution)"),
            (lambda u: u.update(frequency_hz=5),
             "(pwm.MOT1.frequency_hz): error: PWM group MOT1: frequency 5 Hz is too low for the counter clock "
             "80000000 Hz of FTM0 (largest prescaler 128, at most 65535 ticks per period)"),
            (lambda u: u.update(frequency_hz=0), "greater than 0"),
            (lambda u: u.update(deadtime_ns=0), "greater than 0"),
            (lambda u: u.update(deadtime_ns=1.5), "valid integer"),
            (lambda u: u.pop("deadtime_ns"), "Field required"),
        ]
        for i, (change, fragment) in enumerate(cases):
            with self.subTest(case=i, fragment=fragment):
                project = self.project()
                project.pop("adc_sampling", None)  # 16 samples no longer fit a 50 kHz period (IO-31)
                change(self.use(project))
                if fragment == "ok":
                    self.assertEqual(self.run_with(project=project).pwm["deadtime_ticks"], 49)
                else:
                    self.expect_error(project=project, fragment=fragment or "shorter than half the PWM period")

    def test_ftm_clock_follows_the_system_clock(self):
        """The former 80-MHz lock (SUPPORTED_SYSTEM_HZ) is replaced by the clock model: 48 MHz from FIRC."""
        project = self.project()
        project["clock"]["system_hz"] = 48_000_000
        board = self.run_with(project=project)
        p = board.pwm
        # 48 MHz / 20 kHz = 2400 ticks; 1000 ns = 48 clock ticks, fits DTVAL without prescaler
        self.assertEqual((p["clock_hz"], p["period_ticks"], p["deadtime_div"], p["deadtime_ticks"]),
                         (48_000_000, 2400, 1, 48))
        self.assertIn("CLOCK_HZ           = 48000000U;", self.read("ftmPwmConfiguration.h"))
        self.assertEqual((board.clock["source"], board.clock["divcore"]), ("FIRC", 1))
        self.assertEqual(board.warnings, [])                 # ARCH-13: the port follows NX_CLOCK_CORE_HZ
        project["clock"]["system_hz"] = 61_000_000             # prime factor 61: neither divider nor multiplier
        self.expect_error(project=project, fragment="system clock 61 MHz cannot be derived exactly")

    def test_invalid_group(self):
        """Board wiring: FTM pairing rule (bswgen) and structure (nxconfig)."""
        def pair(i, **kw):
            return lambda g: g["pairs"][i].update(kw)

        def swap_numbers(g):  # pins follow the channels (pin function), the pair keeps its sides
            g["channels"][0]["channel"], g["channels"][1]["channel"] = 1, 0
            g["channels"][0]["pin"], g["channels"][1]["pin"] = g["channels"][1]["pin"], g["channels"][0]["pin"]

        cases = [
            (lambda g: g.update(mode="independent"), "Input should be 'complementary'"),
            (lambda g: g.update(instance="FTM7"), "S32K144 has no instance FTM7"),
            (lambda g: g.update(instance="LPSPI0"), "LPSPI0 is a spi instance, expected timer"),
            (lambda g: g.update(active={"high_side": "high"}), "Field required"),
            (lambda g: g.update(active={"high_side": "high", "low_side": "on"}), "Input should be 'high' or 'low'"),
            (lambda g: g.update(dead_time=1), "Extra inputs are not permitted"),
            (swap_numbers, "high side must be FTM channel 2k and low side 2k+1"),
            (pair(0, high_side="MOT1_U_PWM_LS", low_side="MOT1_U_PWM_HS"), "high side must be FTM channel 2k"),
            (pair(1, high_side="MOT1_U_PWM_HS"), "is already used by pair MOT1_U"),
            (pair(0, high_side="NOPE"), "high_side NOPE is not in channels"),
            (pair(1, name="MOT1_U"), "duplicate name MOT1_U"),
            (lambda g: g["pairs"].pop(), "MOT1_W_PWM_HS, MOT1_W_PWM_LS belong to no pair"),
            (lambda g: g.update(pairs=[]), "at least 1 item"),
            (lambda g: g["pairs"][0].pop("name"), "Field required"),
            (lambda g: g["channels"][1].update(channel=0), "channel 0 used twice"),
            (lambda g: g["channels"][1].update(channel=8), "FTM0 has channels 0..7"),
        ]
        for i, (change, fragment) in enumerate(cases):
            with self.subTest(case=i, fragment=fragment):
                doc = self.board()
                change(self.group(doc))
                self.expect_error(doc, fragment)

    @on_eval_board
    def test_pins_must_have_the_ftm_function(self):
        doc = self.board()
        self.group(doc)["instance"] = "FTM1"
        self.expect_error(doc, "has no function FTM1_CH0 on PTB12")
        doc = self.board()
        self.group(doc)["channels"][0]["pin"] = "PTD5"  # unused pin without FTM0_CH0
        self.expect_error(doc, "has no function FTM0_CH0 on PTD5")

    def add_second_group(self, doc, project) -> list[str]:
        """Copies the board group MOT1 to a second group MOT2 on FTM3 and frees its pins in the project."""
        second = copy.deepcopy(self.group(doc))
        second.update(name="MOT2", instance="FTM3")
        second.pop("gate_driver", None)                         # IO-25: MOT1_GD drives MOT1 only
        for p in second["pairs"]:
            for key in ("name", "high_side", "low_side"):
                p[key] = p[key].replace("MOT1", "MOT2")
        ftm3_pins = ["PTB8", "PTB9", "PTB10", "PTB11", "PTC10", "PTC11"]  # FTM3_CH0..5 (ALT2)
        for ch, pin in zip(second["channels"], ftm3_pins):
            ch.update(name=ch["name"].replace("MOT1", "MOT2"), pin=pin)
            ch.pop("pad")
        doc["pwm"].append(second)
        project["use"]["dio"] = [io["name"] for io in doc["dio"]["inputs"] + doc["dio"]["outputs"]
                                 if io["pin"] not in ftm3_pins]
        return ftm3_pins

    @on_eval_board
    def test_two_used_groups_are_rejected_by_name(self):
        """One driver instance per group is not implemented (ARCH-17): an error naming both, not a silent choice."""
        doc, project = self.board(), self.project()
        self.add_second_group(doc, project)
        project["pwm"]["MOT2"] = dict(self.use(project))
        self.expect_error(doc, "uses 2 groups (MOT1 on FTM0, MOT2 on FTM3)", project=project)
        self.expect_error(doc, "supports one group per project", project=project)

    def test_input_capture_on_the_pwm_timer_is_rejected(self):
        project = self.project()
        project["use"]["input_capture"] = "all"
        self.expect_error(project=project, fragment="FTM0 counter is used by input_capture PILOT_DUTY and by pwm MOT1")

    def test_other_family_is_rejected(self):
        mcudb = self.tmp / "mcudb"
        shutil.copytree(REPO / "mcudb", mcudb)
        (mcudb / "s32k1xx").rename(mcudb / "otherfam")
        derivative = mcudb / "otherfam" / "S32K144.yaml"
        derivative.write_text(derivative.read_text(encoding="utf-8").replace("family: s32k1xx", "family: otherfam"),
                              encoding="utf-8")
        # PLAT-23: safeSystem and capture are modules of the s32k1xx port template (platforms/<family>/port), so
        # an invented family does not have them - drop the project entries, otherwise the missing module is
        # reported first (a real other family brings its own manifests for the same parameters)
        project = self.project()
        for module in ("safeSystem", "capture"):
            project.get("modules", {}).pop(module, None)
        # IO-07: the same holds for the hall module of the family port (board device HALL1)
        board = self.board()
        board["devices"] = [d for d in board.get("devices", []) if d.get("module") != "hall"]
        self.expect_error(board=board, project=project, fragment="family otherfam is not supported by bswgen",
                          mcudb=mcudb)


class BswGenDerivativeTest(unittest.TestCase):
    """Derivative facts from mcudb: pin multiplexing per package, device header (mcu/mcuDerivative.h)."""

    setUp, tearDown = BswGenAdcTest.setUp, BswGenAdcTest.tearDown
    run_with, expect_error = BswGenAdcTest.run_with, BswGenAdcTest.expect_error
    board, project = BswGenAdcTest.board, BswGenAdcTest.project

    def read(self, rel):
        return (self.tmp / "out" / rel).read_text(encoding="utf-8")

    def test_device_header_replaces_hand_written_mcu_h_block(self):
        self.run_with()
        generated = self.read("mcu/mcuDerivative.h")
        directives = re.compile(r'^#(?:include "3rdparty/nxp/\w+\.h"|undef PCC_\w+_INDEX)$', re.M)
        # the block mcu.h had by hand until ARCH-08 (include + 9 peripherals only on S32K148)
        hand_written = ['#include "3rdparty/nxp/S32K148.h"'] + [
            f"#undef PCC_{name}_INDEX" for name in
            ("ENET", "FTM4", "FTM5", "FTM6", "FTM7", "LPI2C1", "QSPI", "SAI0", "SAI1")]
        self.assertEqual(directives.findall(generated), hand_written)
        # ARCH-08: the family's mcu.h includes the generated header and has no copy of the block any more
        # (PLAT-23: platforms/s32k1xx/bsp/bspMcu instead of a copy per board port)
        mcu_h = (REPO / "platforms/s32k1xx/bsp/bspMcu/include/mcu/mcu.h").read_text(encoding="utf-8")
        self.assertIn('#include "mcu/mcuDerivative.h"', mcu_h)
        self.assertEqual(directives.findall(mcu_h), [])

    def test_other_derivative_of_the_family(self):
        """Same wiring on an S32K148 LQFP176: pin table from its own hal_nxp data, header without #undef."""
        self.run_with()
        io_144 = re.sub(r"from .*? -", "", self.read("bsp/io/io/ioConfiguration.h"))
        doc = self.board()
        doc["board"].update(mcu="S32K148", package="LQFP176")
        project = self.project()
        project.pop("nvm", None)  # the demo's FlexNVM partition (NVM-07) is S32K144-specific
        self.run_with(doc, project)
        self.assertEqual(re.sub(r"from .*? -", "", self.read("bsp/io/io/ioConfiguration.h")), io_144)
        header = self.read("mcu/mcuDerivative.h")
        self.assertIn('#include "3rdparty/nxp/S32K148.h"', header)
        self.assertNotIn("#undef", header)

    @on_eval_board
    def test_package_without_pin_multiplexing(self):
        doc = self.board()
        doc["board"]["package"] = "LQFP64"
        self.expect_error(doc, "S32K144 LQFP64 has no pin PTE15")        # table from mcudb (ARCH-11)
        mcudb = self.tmp / "mcudb"                # all packages have tables since ARCH-11: remove two of them
        shutil.copytree(REPO / "mcudb", mcudb, ignore=shutil.ignore_patterns("sources", "tools"))
        for name, package in (("S32K144", "LQFP64"), ("S32K146", "LQFP100")):
            file = mcudb / "s32k1xx" / f"{name}.yaml"
            text = re.sub(rf"^  {package}: \{{ file: .*\n", "", file.read_text(encoding="utf-8"), flags=re.M)
            file.write_text(text, encoding="utf-8")
        self.expect_error(doc, "S32K144 LQFP64: no complete pin multiplexing table in mcudb", mcudb=mcudb)
        doc["board"].update(mcu="S32K146", package="LQFP100")
        self.expect_error(doc, "S32K146 LQFP100: no complete pin multiplexing table in mcudb", mcudb=mcudb)
        doc["board"]["mcu"] = "S32K142"           # smaller derivative: instances from mcudb
        self.expect_error(doc, "S32K142 has no instance LPSPI2 (available: LPSPI0, LPSPI1)")

    def test_package_decides_the_pins(self):
        doc = self.board()
        doc["board"].update(mcu="S32K148", package="LQFP176")
        doc["dio"]["outputs"].append({"name": "EXTRA", "pin": "PTA20"})   # only on the 176-pin package
        project = self.project()
        project["use"]["dio"] = "all"
        project.pop("nvm", None)  # the demo's FlexNVM partition (NVM-07) is S32K144-specific
        self.assertIn("EXTRA", self.run_with(doc, project).pins)
        doc["board"].update(mcu="S32K144", package="LQFP100")
        self.expect_error(doc, "invalid pin name 'PTA20' for S32K144", project=project)

    @on_eval_board
    def test_i2c_bus_on_flexio_and_on_an_i2c_instance(self):
        """TOOL-120: a used I2C bus on the FlexIO is muxed to FlexIO_D<flexio_pin>; on an LPI2C (no flexio_pin
        needed since TOOL-120) the family port has no driver - bswgen refuses with the text of nxconfig
        (checks.i2c_driver_problem), which reports it first for a board that is built. The board is a modified
        copy; the bus itself is invented (LPI2C0 on PTB6/PTB7)."""
        gen = self.run_with()
        self.assertEqual([(gen.pins[n].pin, gen.pins[n].mux) for n in ("I2C_SDA", "I2C_SCL")],
                         [("PTE4", 6), ("PTE5", 6)])                     # FlexIO_D6/D7, ALT6
        doc = self.board()
        doc["i2c"][0].update(instance="LPI2C0", sda={"pin": "PTB6"}, scl={"pin": "PTB7"})
        self.expect_error(doc, "I2C bus I2C is on LPI2C0 (class i2c); the family port s32k1xx has no driver for "
                               "it - its I2C driver is the FlexIO I2C master")
        project = self.project()
        project["use"]["i2c"] = []                  # described, not used: nothing to drive
        gen = self.run_with(doc, project)
        self.assertNotIn("I2C_SDA", gen.pins)


class BswGenPwmHostTest(unittest.TestCase):
    """Compiles ComplementaryPwm.h with the generated configuration and runs test/pwm_host."""

    OPENBSW = REPO / "external" / "openbsw"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        bswgen.generate(PROJECT_FILE, self.tmp / "gen")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def compile(self, extra_include=None, source="complementary_pwm_host_test.cpp", gen="gen"):
        host = HERE / "test" / "pwm_host"
        includes = [host / "stubs"] + ([extra_include] if extra_include else []) + [
            self.tmp / gen,
            REPO / "platforms/s32k1xx/port/bspConfiguration/include",
            REPO / "platforms/s32k1xx/bsp/bspFtmPwmGroup/include",  # IO-61: channel values of both countings
        ]
        # OpenBSW and NXP headers are not ours: no -Wconversion for them
        system = [
            self.OPENBSW / "platforms/s32k1xx/bsp/bspFtm/include",
            self.OPENBSW / "platforms/s32k1xx/bsp/bspMcu/include/3rdparty/nxp",
            self.OPENBSW / "libs/bsw/bsp/include",
        ]
        exe = self.tmp / f"{Path(source).stem}.exe"
        cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
               "-Wconversion", "-Wno-deprecated-volatile", *[f"-I{i}" for i in includes],
               *[f"-isystem{i}" for i in system], str(host / source), "-o", str(exe)]
        return subprocess.run(cmd, capture_output=True, text=True), exe

    def test_driver_runtime(self):
        r, exe = self.compile()
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        r = subprocess.run([str(exe)], capture_output=True, text=True)
        print("\n" + r.stdout.strip(), file=sys.stderr)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:])

    def test_without_pwm_group_the_generat_compiles_on_its_own(self):
        """ARCH-17: the generat of a project without a pwm group is includable (0 groups, no FTM, no driver)."""
        project = yaml.safe_load(PROJECT_FILE.read_text(encoding="utf-8"))
        project["pwm"] = {}
        project.pop("adc_sampling", None)  # averages over MOT1, which this test changes (IO-31)
        project["project"]["board"] = str(BOARD_FILE.parent.as_posix())
        project["project"]["rte"] = (PROJECT_FILE.parent / "rte.yaml").as_posix()   # TOOL-114: required
        project["project"].pop("diag", None)  # the copy has no diag.yaml next to it (ARCH-51)
        path = self.tmp / "project_without_pwm.yaml"
        path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        bswgen.generate(path, self.tmp / "gen_no_pwm")
        r, exe = self.compile(source="no_pwm_group_host_test.cpp", gen="gen_no_pwm")
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        r = subprocess.run([str(exe)], capture_output=True, text=True)
        print("\n" + r.stdout.strip(), file=sys.stderr)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:])
        # built with the eval board generat (one group) the same file must fail: the test would pass anyway
        r, _ = self.compile(source="no_pwm_group_host_test.cpp")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("NX_PWM_GROUP_COUNT", r.stderr)

    def test_static_asserts_reject_hand_edits(self):
        cfg = (self.tmp / "gen" / "bsp" / "io" / "outputPwm" / "ftmPwmConfiguration.h").read_text(encoding="utf-8")
        cases = [
            ("PERIOD_TICKS       = 4000U;", "PERIOD_TICKS       = 150U;", "dead time must be shorter"),
            ("DEADTIME_TICKS     = 20U;", "DEADTIME_TICKS     = 19U;", "realised dead time is shorter"),
            ("DEADTIME_PRESCALER = 4U;", "DEADTIME_PRESCALER = 16U;", "DTPS and divider do not match"),
            ("{OutputPwm::MOT1_V, 2U,", "{OutputPwm::MOT1_V, 3U,", "pairs must use FTM channels 2k/2k+1"),
            ("FREQUENCY_HZ       = 20000U;", "FREQUENCY_HZ       = 20500U;", "deviates more than 1 %"),
        ]
        for old, new, message in cases:
            with self.subTest(message=message):
                self.assertIn(old, cfg)
                patched = self.tmp / "patched" / "bsp" / "io" / "outputPwm"
                patched.mkdir(parents=True, exist_ok=True)
                (patched / "ftmPwmConfiguration.h").write_text(cfg.replace(old, new), encoding="utf-8")
                r, _ = self.compile(extra_include=self.tmp / "patched")
                self.assertNotEqual(r.returncode, 0)
                self.assertIn(message, r.stderr)


class BswGenCaptureTest(unittest.TestCase):
    """input_capture: FTM dual edge capture derived from board, project parameters and clock model (IO-03).

    The profile is its own one: a copy of the synthetic 8 MHz s32k1xx profile of tools/nxconfig (64 MHz system
    clock, LQFP64) with two capture channels on two counters and without a PWM group. The eval board demo
    project uses one channel on FTM2, so it alone would not show two counters, the sharing of the parameters or
    another prescaler; both are checked here.
    """

    S32K_BOARD = REPO / "tools/nxconfig/test/profiles/boards/testboard_s32k_8mhz/board.yaml"
    S32K_PROJECT = REPO / "tools/nxconfig/test/profiles/projects/testproject_s32k_8mhz/project.yaml"
    # free pins of that LQFP64 profile with an even FTM channel (pin multiplexing from mcudb)
    CHANNELS = [{"name": "TACHO", "instance": "FTM0", "channel": 4, "pin": "PTB4"},
                {"name": "PILOT", "instance": "FTM2", "channel": 0, "pin": "PTC5"}]
    PARAMETERS = {"min_frequency_hz": 100, "timeout_ms": 100, "update_period_ms": 10}

    setUp, tearDown = BswGenAdcTest.setUp, BswGenAdcTest.tearDown

    def board(self):
        doc = yaml.safe_load(self.S32K_BOARD.read_text(encoding="utf-8"))
        doc["input_capture"] = copy.deepcopy(self.CHANNELS)
        return doc

    def project(self, **parameters):
        doc = yaml.safe_load(self.S32K_PROJECT.read_text(encoding="utf-8"))
        doc["use"]["input_capture"] = "all"
        doc.pop("pwm", None)     # ARCH-17: without a group the FTM counters are free for the capture
        doc.setdefault("modules", {})["capture"] = {**self.PARAMETERS, **parameters}
        return doc

    def write(self, board=None, project=None) -> Path:
        board_dir, project_dir = self.tmp / "boards" / "b", self.tmp / "projects" / "p"
        board_dir.mkdir(parents=True, exist_ok=True)
        project_dir.mkdir(parents=True, exist_ok=True)
        (board_dir / "board.yaml").write_text(yaml.safe_dump(board or self.board(), sort_keys=False),
                                              encoding="utf-8")
        doc = copy.deepcopy(project or self.project())
        doc["project"]["board"] = "../../boards/b"
        without_rte(doc)
        path = project_dir / "project.yaml"
        path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        return path

    def run_with(self, board=None, project=None):
        return bswgen.generate(self.write(board, project), self.tmp / "out")

    def expect_error(self, fragment, board=None, project=None):
        with self.assertRaises(bswgen.ConfigError) as ctx:
            self.run_with(board, project)
        self.assertIn(fragment, str(ctx.exception))

    def read(self, rel="inputCaptureConfiguration.h"):
        return (self.tmp / "out" / "bsp" / "capture" / rel).read_text(encoding="utf-8")

    def test_two_channels_on_two_counters(self):
        cap = self.run_with().capture
        self.assertEqual([c["name"] for c in cap["channels"]], ["TACHO", "PILOT"])
        self.assertEqual([c["timer"] for c in cap["channels"]], [0, 1])
        self.assertEqual([t["instance"] for t in cap["timers"]], ["FTM0", "FTM2"])
        # 64 MHz counter clock, one period of 100 Hz has to fit half the counter range (32768 ticks):
        # prescaler 32 -> 2 MHz, 20000 ticks for the slowest period, 1000 ticks as resolution limit,
        # timeout 100 ms = 200000 ticks, update cycle 10 ms = 20000 ticks
        for t in cap["timers"]:
            self.assertEqual((t["counter_hz"], t["prescaler_shift"], t["clock_hz"]), (64000000, 5, 2000000))
            self.assertEqual((t["min_period_ticks"], t["max_period_ticks"]), (1000, 20000))
            self.assertEqual((t["timeout_ticks"], t["update_ticks"]), (200000, 20000))
            self.assertEqual((t["wrap_us"], t["tick_ns"]), (32768, 500))
            self.assertEqual((t["min_frequency_actual"], t["max_frequency_actual"]), ("100", "2000"))
        header = self.read()
        self.assertIn("#define NX_CAPTURE_CHANNEL_COUNT 2", header)
        self.assertIn("#define NX_CAPTURE_CHANNELS(X) X(TACHO) X(PILOT)", header)
        self.assertIn("Ci_TACHO, // FTM0 channel 4/5 on PTB4", header)
        self.assertIn("Ci_PILOT, // FTM2 channel 0/1 on PTC5", header)
        self.assertIn("constexpr uint8_t TIMER_COUNT    = 2U;", header)
        self.assertIn("case 1U: return *FTM2;", header)
        self.assertIn("default: return *FTM0;", header)
        self.assertIn("{ 2000000U, 200000U, 20000U, 1000U, 20000U },", header)
        self.assertIn("{ 0U, 4U }, // TACHO on FTM0", header)
        self.assertIn("{ 1U, 0U }, // PILOT on FTM2", header)
        self.assertEqual(re.findall(r'^    "(\w+)",$', header, re.M), ["TACHO", "PILOT"])
        self.assertIn("FTM_SC_PS(5U)", header)
        self.assertIn("(COMBINE_DECAPEN_PAIR << pairShift(4U)), // DECAPEN of the pairs 4/5", header)
        self.assertIn("(COMBINE_DECAPEN_PAIR << pairShift(0U)), // DECAPEN of the pairs 0/1", header)
        self.assertIn("CONFIGURATION_FTM0", header)
        self.assertIn("CONFIGURATION_FTM2", header)
        # the pin of a capture channel is an input muxed to the FTM (like an ADC or DIO pin)
        io = (self.tmp / "out" / "bsp" / "io" / "io" / "ioConfiguration.h").read_text(encoding="utf-8")
        self.assertRegex(io, r"\{_PORTB_, PB4, _IN, 0, \(uint32_t\) ALT\d\}")
        self.assertRegex(io, r"\{_PORTC_, PC5, _IN, 0, \(uint32_t\) ALT\d\}")

    @on_eval_board
    def test_clock_model_decides_the_prescaler(self):
        """ARCH-06: the eval board runs the same measurement at 80 MHz, so it gets another prescaler."""
        demo = bswgen.generate(PROJECT_FILE, self.tmp / "demo").capture
        self.assertEqual([c["name"] for c in demo["channels"]], ["PWM_IN"])
        self.assertEqual((demo["min_frequency_hz"], demo["timeout_ms"], demo["update_period_ms"]), (40, 200, 10))
        self.assertTrue(demo["verify"])  # the frequency range of the RC input is a placeholder
        t = demo["timers"][0]
        self.assertEqual((t["instance"], t["counter_hz"], t["prescaler_shift"], t["clock_hz"]),
                         ("FTM2", 80000000, 6, 1250000))
        self.assertEqual((t["min_period_ticks"], t["max_period_ticks"]), (1000, 31250))
        self.assertEqual((t["timeout_ticks"], t["update_ticks"], t["wrap_us"]), (250000, 12500, 52429))

    def test_parameters_come_from_the_project_with_the_defaults_of_the_module(self):
        project = self.project()
        project["modules"].pop("capture")
        cap = self.run_with(project=project).capture
        # defaults of platforms/s32k1xx/port/bspConfiguration/src/bsp/capture/module.yaml
        self.assertEqual((cap["min_frequency_hz"], cap["timeout_ms"], cap["update_period_ms"]), (50, 200, 10))
        self.assertFalse(cap["verify"])
        self.assertEqual(cap["timers"][0]["prescaler_shift"], 6)   # 50 Hz at 64 MHz needs the prescaler 64
        project = self.project()
        project["modules"]["capture"]["verify"] = True
        self.assertTrue(self.run_with(project=project).capture["verify"])

    def test_family_without_the_capture_module_is_reported(self):
        """A family whose port does not bring the module cannot measure: that is said, not measured silently."""
        config, messages = bswgen.nxconfig.check(self.write())
        self.assertFalse([m for m in messages if m.severity == "error"], messages)
        config.integration.modules.pop(bswgen.CAPTURE_MODULE)   # as if the port had no manifest for it
        board = bswgen.Board(config)
        board.pwm = None
        with self.assertRaises(bswgen.ConfigError) as ctx:
            board.collect_capture()
        self.assertIn("module capture has no manifest or no parameters", str(ctx.exception))
        self.assertIn("project modules.capture", str(ctx.exception))

    def test_value_outside_the_module_schema_is_reported_with_its_yaml_path(self):
        messages = bswgen.validate(self.write(project=self.project(timeout_ms=0)))
        errors = [m for m in messages if m.severity == "error"]
        self.assertTrue(errors, "a timeout of 0 ms must be rejected")
        self.assertEqual(errors[0].path, ("modules", "capture", "timeout_ms"))
        self.assertIn("out of range 1..60000 ms", errors[0].text)

    def test_counter_of_a_pwm_group_is_not_shared(self):
        """ARCH-02/IO-04: one counter is used in one way; the error names the YAML path, nothing is reassigned."""
        board, project = self.board(), self.project()
        board["input_capture"] = [{"name": "TACHO", "instance": "FTM1", "channel": 4, "pin": "PTA10"}]
        project["pwm"] = {"FAN": {"frequency_hz": 25000, "deadtime_ns": 2000}}   # FAN is on FTM1
        messages = bswgen.validate(self.write(board, project))
        errors = [(m.text, m.path) for m in messages if m.severity == "error"]
        self.assertIn(("FTM1 counter is used by input_capture TACHO and by pwm FAN", ("use", "input_capture")),
                      errors)
        # the generator refuses as well (guard), it does not choose a counter silently
        self.expect_error("FTM1 counter is used by input_capture TACHO", board, project)

    def test_configuration_errors(self):
        cases = [
            # a signal on an odd channel cannot be measured: the pair needs 2k and 2k+1
            (lambda b, p: b["input_capture"][0].update(channel=5, pin="PTB5"),
             "FTM0 channel 5 is odd, but the dual edge capture measures on the even channel"),
            (lambda b, p: b["input_capture"][0].update(channel=5, pin="PTB5"),
             "use channel 4 (pin with function FTM0_CH4) for this signal"),
            # 100 kHz would leave 640 ticks per period: no 0.1 % duty resolution
            (lambda b, p: p["modules"]["capture"].update(min_frequency_hz=100000),
             "min_frequency_hz 100000 leaves 640 counter ticks per period at clock 64000000 Hz, at least 1000"),
            # 0.5 Hz does not fit the counter even with the largest prescaler
            (lambda b, p: p["modules"]["capture"].update(min_frequency_hz=1, timeout_ms=60000),
             "needs more than 32768 counter ticks per period even with prescaler 128"),
            # 400 Hz gives prescaler 8 (8 MHz): the counter then wraps within the update cycle
            (lambda b, p: p["modules"]["capture"].update(min_frequency_hz=400),
             "the counter wraps after 8192 us, update_period_ms 10 is 80000 ticks"),
            # a timeout shorter than two periods of the slowest signal would report a healthy signal as missing
            (lambda b, p: p["modules"]["capture"].update(timeout_ms=20),
             "timeout_ms 20 is shorter than two periods of min_frequency_hz 100 plus one update cycle (30 ms)"),
        ]
        for change, fragment in cases:
            with self.subTest(fragment=fragment):
                board, project = self.board(), self.project()
                change(board, project)
                self.expect_error(fragment, board, project)

    def test_without_a_channel_nothing_is_generated(self):
        project = self.project()
        project["use"]["input_capture"] = []
        self.assertIsNone(self.run_with(project=project).capture)
        header = self.read()
        self.assertIn("#define NX_CAPTURE_CHANNEL_COUNT 0", header)
        self.assertIn("#define NX_CAPTURE_CHANNELS(X)\n", header)
        for absent in ("#include", "FTM_SC_PS", "COMBINE_DECAPEN_PAIR", "static_assert"):
            self.assertNotIn(absent, header)


class BswGenCaptureHostTest(unittest.TestCase):
    """Compiles bsp/capture/InputCapture with the generated configuration and runs test/capture_host."""

    OPENBSW = REPO / "external" / "openbsw"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def compile(self, gen, source="input_capture_host_test.cpp", extra_include=None):
        host = HERE / "test" / "capture_host"
        includes = [host / "stubs", HERE / "test" / "pwm_host" / "stubs"] + (
            [extra_include] if extra_include else []) + [
            self.tmp / gen, REPO / "platforms/s32k1xx/port/bspConfiguration/include"]
        system = [self.OPENBSW / "platforms/s32k1xx/bsp/bspFtm/include",
                  self.OPENBSW / "platforms/s32k1xx/bsp/bspMcu/include/3rdparty/nxp",
                  self.OPENBSW / "libs/bsw/bsp/include"]
        exe = self.tmp / f"{Path(source).stem}_{gen}.exe"
        cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
               "-Wconversion", "-Wno-deprecated-volatile", *[f"-I{i}" for i in includes],
               *[f"-isystem{i}" for i in system], str(host / source),
               str(REPO / "platforms/s32k1xx/port/bspConfiguration/src/bsp/capture/InputCapture.cpp"),
               "-o", str(exe)]
        return subprocess.run(cmd, capture_output=True, text=True), exe

    def run_test(self, gen, source="input_capture_host_test.cpp"):
        r, exe = self.compile(gen, source)
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        r = subprocess.run([str(exe)], capture_output=True, text=True)
        print("\n" + r.stdout.strip(), file=sys.stderr)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:])

    def generate(self, name, board=None, project=None):
        """Generat of the demo project (name 'demo') or of the two-channel test profile."""
        if board is None and project is None:
            bswgen.generate(PROJECT_FILE, self.tmp / name)
            return name
        helper = BswGenCaptureTest("test_two_channels_on_two_counters")
        helper.tmp = self.tmp
        bswgen.generate(helper.write(board, project), self.tmp / name)
        return name

    def test_driver_runtime_eval_board(self):
        """One channel on FTM2 (eval board demo: PWM_IN, 1.25 MHz, 40..1250 Hz)."""
        self.run_test(self.generate("demo"))

    def test_driver_runtime_two_channels(self):
        """Two channels on two counters (own profile, 2 MHz, 100..2000 Hz)."""
        helper = BswGenCaptureTest("test_two_channels_on_two_counters")
        self.run_test(self.generate("two", helper.board(), helper.project()))

    def test_without_a_channel_the_driver_still_links(self):
        helper = BswGenCaptureTest("test_two_channels_on_two_counters")
        project = helper.project()
        project["use"]["input_capture"] = []
        self.run_test(self.generate("none", helper.board(), project), "no_capture_host_test.cpp")
        # with a generat that has a channel the same file must fail: the test would pass anyway
        r, _ = self.compile("demo" if (self.tmp / "demo").is_dir() else self.generate("demo"),
                            "no_capture_host_test.cpp")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("NX_CAPTURE_CHANNEL_COUNT", r.stderr)

    def test_static_asserts_reject_hand_edits(self):
        self.generate("demo")
        cfg = (self.tmp / "demo" / "bsp" / "capture" / "inputCaptureConfiguration.h").read_text(encoding="utf-8")
        cases = [
            ("{ 0U, 4U }", "{ 0U, 5U }", "even channel of a free FTM pair"),          # odd channel
            ("{ 0U, 4U }", "{ 1U, 4U }", "even channel of a free FTM pair"),          # timer that does not exist
            ("1000U, 31250U }", "1000U, 40000U }", "do not fit the counter range"),   # period beyond half the range
            ("250000U, 12500U", "40000U, 12500U", "do not fit the counter range"),    # timeout below two periods
            ("1000U, 31250U }", "900U, 31250U }", "do not fit the counter range"),    # duty resolution lost
            ("NX_CAPTURE_CHANNEL_COUNT 1", "NX_CAPTURE_CHANNEL_COUNT 2", "does not match the macro"),
        ]
        for old, new, message in cases:
            with self.subTest(message=message, new=new):
                self.assertIn(old, cfg)
                patched = self.tmp / "patched" / "bsp" / "capture"
                patched.mkdir(parents=True, exist_ok=True)
                (patched / "inputCaptureConfiguration.h").write_text(cfg.replace(old, new, 1), encoding="utf-8")
                r, _ = self.compile("demo", extra_include=self.tmp / "patched")
                self.assertNotEqual(r.returncode, 0)
                self.assertIn(message, r.stderr)


class BswGenHallTest(unittest.TestCase):
    """hall: sector table, pins, pin interrupts and times of a hall sensor set (IO-07).

    The profile is its own one: a copy of the synthetic 8 MHz s32k1xx profile of tools/nxconfig (LQFP64) with
    **two** sensor sets - one 120 degrees with one pole pair on a single port, one 60 degrees with eight pole
    pairs, inverted signals, sector_down and signals spread over two ports. The eval board has one set, so it
    alone would show neither arrangement, neither inversion nor two ports; both profiles are checked here and
    in the host test.
    """

    S32K_BOARD = REPO / "tools/nxconfig/test/profiles/boards/testboard_s32k_8mhz/board.yaml"
    S32K_PROJECT = REPO / "tools/nxconfig/test/profiles/projects/testproject_s32k_8mhz/project.yaml"
    # free GPIO pins of that LQFP64 profile (pin multiplexing from mcudb)
    INPUTS = [{"name": "HALL_A_U", "pin": "PTA0"}, {"name": "HALL_A_V", "pin": "PTA1"},
              {"name": "HALL_A_W", "pin": "PTA2"},
              {"name": "HALL_B_U", "pin": "PTC6", "active": "low"},
              {"name": "HALL_B_V", "pin": "PTD3", "active": "low"},
              {"name": "HALL_B_W", "pin": "PTD4", "active": "low"}]
    DEVICES = [{"name": "MOT_A", "part": "hall3", "module": "hall",
                "connect": {"u": "HALL_A_U", "v": "HALL_A_V", "w": "HALL_A_W"}},
               {"name": "MOT_B", "part": "hall3_open_emitter", "module": "hall",
                "connect": {"u": "HALL_B_U", "v": "HALL_B_V", "w": "HALL_B_W"}}]
    PARAMETERS = {
        "MOT_A": {"arrangement": "deg120", "pole_pairs": 1, "min_speed_rpm": 120, "max_speed_rpm": 3000,
                  "debounce_us": 50, "standstill_ms": 300},
        "MOT_B": {"arrangement": "deg60", "pole_pairs": 8, "forward": "sector_down", "min_speed_rpm": 10,
                  "max_speed_rpm": 1000, "debounce_us": 20, "standstill_ms": 500},
    }
    RANKS = [{"source": "dio", "name": "HALL_A_U", "rank": 2}, {"source": "dio", "name": "HALL_A_V", "rank": 2},
             {"source": "dio", "name": "HALL_A_W", "rank": 2}, {"source": "dio", "name": "HALL_B_U", "rank": 3},
             {"source": "dio", "name": "HALL_B_V", "rank": 3}, {"source": "dio", "name": "HALL_B_W", "rank": 3}]
    # the eval board demo project does not use its hall device; the board test build adds these two entries
    DEMO_PARAMETERS = {"arrangement": "deg120", "pole_pairs": 4, "min_speed_rpm": 60, "max_speed_rpm": 6000,
                       "debounce_us": 20, "standstill_ms": 200}
    DEMO_RANKS = [{"source": "dio", "name": "HALL_UX", "rank": 1}, {"source": "dio", "name": "HALL_V", "rank": 1},
                  {"source": "dio", "name": "HALL_WY", "rank": 1}]

    setUp, tearDown = BswGenAdcTest.setUp, BswGenAdcTest.tearDown

    def board(self):
        doc = yaml.safe_load(self.S32K_BOARD.read_text(encoding="utf-8"))
        doc.setdefault("dio", {}).setdefault("inputs", []).extend(copy.deepcopy(self.INPUTS))
        doc.setdefault("devices", []).extend(copy.deepcopy(self.DEVICES))
        return doc

    def project(self, **overrides):
        doc = yaml.safe_load(self.S32K_PROJECT.read_text(encoding="utf-8"))
        doc["use"]["dio"] = "all"
        modules = doc.setdefault("modules", {})
        for name, values in self.PARAMETERS.items():
            modules[name] = {**values, **overrides.get(name, {})}
        doc.setdefault("interrupts", {}).setdefault("ranks", []).extend(copy.deepcopy(self.RANKS))
        return doc

    @staticmethod
    def demo_project():
        """The eval board demo project with its hall device switched on (the probe build of the board test)."""
        doc = copy.deepcopy(PROJECT)
        doc["modules"]["HALL1"] = dict(BswGenHallTest.DEMO_PARAMETERS)
        doc["interrupts"]["ranks"].extend(copy.deepcopy(BswGenHallTest.DEMO_RANKS))
        return doc

    def write(self, board=None, project=None) -> Path:
        board_dir, project_dir = self.tmp / "boards" / "b", self.tmp / "projects" / "p"
        board_dir.mkdir(parents=True, exist_ok=True)
        project_dir.mkdir(parents=True, exist_ok=True)
        (board_dir / "board.yaml").write_text(yaml.safe_dump(board or self.board(), sort_keys=False),
                                              encoding="utf-8")
        doc = copy.deepcopy(project or self.project())
        doc["project"]["board"] = "../../boards/b"
        without_rte(doc)
        path = project_dir / "project.yaml"
        path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        return path

    def run_with(self, board=None, project=None):
        return bswgen.generate(self.write(board, project), self.tmp / "out")

    def expect_error(self, fragment, board=None, project=None):
        with self.assertRaises(bswgen.ConfigError) as ctx:
            self.run_with(board, project)
        self.assertIn(fragment, str(ctx.exception))

    def read(self, rel=None):
        """One generated hall header, or both (family neutral decoder part first) if `rel` is None."""
        names = [rel] if rel else ["hallDecoderConfiguration.h", "hallSensorConfiguration.h"]
        return "".join((self.tmp / "out" / "bsp" / "hall" / n).read_text(encoding="utf-8") for n in names)

    def test_two_sensor_sets_with_both_arrangements(self):
        hall = self.run_with().hall
        self.assertEqual([s["name"] for s in hall["sensors"]], ["MOT_A", "MOT_B"])
        a, b = hall["sensors"]
        # sector table from the documented sequences, invalid states derived, nothing configured twice
        self.assertEqual(a["states"], ["100", "110", "010", "011", "001", "101"])
        self.assertEqual(a["invalid"], ["000", "111"])
        self.assertEqual(a["sectors"], [0, 5, 3, 4, 1, 6, 2, 0])
        self.assertEqual(b["states"], ["111", "110", "100", "000", "001", "011"])
        self.assertEqual(b["invalid"], ["010", "101"])
        self.assertEqual(b["sectors"], [4, 5, 0, 6, 3, 0, 2, 1])
        # the board decides the inversion (dio active: low), not the project
        self.assertEqual((a["invert"], b["invert"]), (0, 7))
        self.assertEqual((a["forward_sign"], b["forward_sign"]), (1, -1))
        self.assertEqual((a["pole_pairs"], b["pole_pairs"]), (1, 8))
        # MOT_A sits on one port, MOT_B on two: three pin interrupts in total
        self.assertEqual([p["port"] for p in hall["ports"]], ["PORTA", "PORTC", "PORTD"])
        self.assertEqual([p["mask"] for p in hall["ports"]], [0b111, 1 << 6, (1 << 3) | (1 << 4)])
        self.assertEqual([p["rank"] for p in hall["ports"]], [2, 3, 3])
        self.assertEqual([p["level"] for p in hall["ports"]], [3, 4, 4])   # max_syscall_level 1 + rank
        self.assertEqual((a["ports"], b["ports"]), (0b001, 0b110))
        self.assertEqual(hall["port_mask"], 0b01101)                        # PORTA, PORTC, PORTD
        # derived times: shortest and longest sector of the speed range
        self.assertEqual((a["sector_us_min"], a["sector_us_max"]), (3333, 83333))
        self.assertEqual((b["sector_us_min"], b["sector_us_max"]), (1250, 125000))
        self.assertEqual((a["standstill_us"], b["standstill_us"]), (300000, 500000))
        header = self.read()
        self.assertIn("#define NX_HALL_SENSOR_COUNT 2", header)
        self.assertIn("#define NX_HALL_SENSORS(X) X(MOT_A) X(MOT_B)", header)
        self.assertIn("#define NX_HALL_PORT_COUNT 3", header)
        self.assertIn("X(0, PORTA, PORTA_IRQHandler, 0) X(1, PORTC, PORTC_IRQHandler, 2) "
                      "X(2, PORTD, PORTD_IRQHandler, 3)", header)
        self.assertIn("#define NX_HALL_PORT_MASK 0x0DU", header)
        self.assertIn("{ 0U, 0x00000007U, PORTA_IRQn, 3U },", header)
        self.assertIn("{ 2U, 0x00000040U, PORTC_IRQn, 4U },", header)
        self.assertIn("{ 3U, 0x00000018U, PORTD_IRQn, 4U },", header)
        self.assertIn("{ { 0U, 0U }, { 0U, 1U }, { 0U, 2U } },", header)          # MOT_A: PTA0, PTA1, PTA2
        self.assertIn("{ { 2U, 6U }, { 3U, 3U }, { 3U, 4U } },", header)          # MOT_B: PTC6, PTD3, PTD4
        self.assertIn("{ 0U, 5U, 3U, 4U, 1U, 6U, 2U, 0U }, // state 0..7 -> sector", header)
        self.assertIn("{ 4U, 5U, 0U, 6U, 3U, 0U, 2U, 1U }, // state 0..7 -> sector", header)
        self.assertIn("0x0U, 0x01U,", header)                                     # MOT_A: not inverted, PORTA
        self.assertIn("0x7U, 0x06U,", header)                                     # MOT_B: inverted, PORTC+PORTD
        self.assertIn("-1, 8U, 20U, 500000U,", header)                            # sector_down, 8 pole pairs
        self.assertEqual(re.findall(r'^    "(\w+)",$', header, re.M), ["MOT_A", "MOT_B"])

    @on_eval_board
    def test_eval_board_sensor_set(self):
        """The eval board: one set of 120 degrees, signals spread over PORTA and PORTB (board pin multiplexing)."""
        hall = bswgen.generate(PROJECT_FILE, self.tmp / "demo").hall
        self.assertIsNone(hall)     # the demo project does not switch the device on (image stays byte identical)
        board = copy.deepcopy(BOARD)
        hall = self.run_with(board, self.demo_project()).hall
        sensor = hall["sensors"][0]
        self.assertEqual(sensor["name"], "HALL1")
        self.assertEqual([(p["pin"], p["port_index"], p["pin_number"]) for p in sensor["pins"]],
                         [("PTA1", 0, 1), ("PTB8", 1, 8), ("PTB9", 1, 9)])
        self.assertEqual([p["port"] for p in hall["ports"]], ["PORTA", "PORTB"])
        self.assertEqual([p["level"] for p in hall["ports"]], [6, 6])       # max_syscall_level 5 + rank 1
        self.assertEqual(sensor["invert"], 0)                               # the board does not invert
        self.assertEqual((sensor["pole_pairs"], sensor["forward_sign"]), (4, 1))

    def test_a_signal_without_an_interrupt_rank_is_reported(self):
        project = self.project()
        project["interrupts"]["ranks"] = [r for r in project["interrupts"]["ranks"] if r["name"] != "HALL_A_V"]
        self.expect_error("the hall signal HALL_A_V (v, PTA1) has no interrupt rank", project=project)
        self.expect_error("interrupts.ranks, PLAT-21", project=project)

    def test_signals_of_one_port_need_the_same_rank(self):
        project = self.project()
        for rank in project["interrupts"]["ranks"]:
            if rank["name"] == "HALL_A_W":
                rank["rank"] = 5
        self.expect_error("one port has one pin interrupt vector and therefore one NVIC level",
                          project=project)

    def test_signals_of_one_sensor_need_the_same_rank(self):
        """Two ports of one sensor must not preempt each other: each interrupt steps the same state."""
        board, project = self.board(), self.project()
        # move one signal of MOT_A to another port, then give it another rank
        board["dio"]["inputs"][-6]["pin"] = "PTE2"   # HALL_A_U
        for rank in project["interrupts"]["ranks"]:
            if rank["name"] == "HALL_A_U":
                rank["rank"] = 6
        self.expect_error("the interrupts of one sensor must not interrupt each other", board, project)

    def test_configuration_errors(self):
        cases = [
            # 6000 rpm with 8 pole pairs leaves 208 us per sector, a debounce of 100 us swallows edges
            ({"MOT_B": {"debounce_us": 100, "max_speed_rpm": 6000}},
             "debounce_us 100 is more than a 4th of the shortest sector time (208 us"),
            # 1 rpm with 1 pole pair is 10 s per sector, a standstill of 300 ms reports a healthy motor as stopped
            ({"MOT_A": {"min_speed_rpm": 1}},
             "standstill_ms 300 is not longer than 2 sector times of min_speed_rpm 1"),
            # 200000 rpm with 64 pole pairs is below one microsecond per sector
            ({"MOT_A": {"max_speed_rpm": 200000, "pole_pairs": 64, "min_speed_rpm": 1000, "standstill_ms": 300}},
             "leaves less than one microsecond per sector"),
        ]
        for overrides, fragment in cases:
            with self.subTest(fragment=fragment):
                self.expect_error(fragment, project=self.project(**overrides))

    def test_value_outside_the_module_schema_is_reported_with_its_yaml_path(self):
        messages = bswgen.validate(self.write(project=self.project(MOT_A={"pole_pairs": 0})))
        errors = [m for m in messages if m.severity == "error"]
        self.assertTrue(errors, "a motor without pole pairs must be rejected")
        self.assertEqual(errors[0].path, ("modules", "MOT_A", "pole_pairs"))
        self.assertIn("out of range 1..64", errors[0].text)

    def test_arrangement_is_a_fixed_choice(self):
        messages = bswgen.validate(self.write(project=self.project(MOT_A={"arrangement": "deg90"})))
        errors = [m for m in messages if m.severity == "error"]
        self.assertTrue(errors)
        self.assertEqual(errors[0].path, ("modules", "MOT_A", "arrangement"))
        self.assertIn("deg120|deg60", errors[0].text)

    def test_missing_motor_facts_are_reported(self):
        """pole_pairs and the speed range have no default: they are facts of the motor, not values to guess."""
        for parameter in ("pole_pairs", "min_speed_rpm", "max_speed_rpm"):
            with self.subTest(parameter=parameter):
                project = self.project()
                project["modules"]["MOT_A"].pop(parameter)
                messages = bswgen.validate(self.write(project=project))
                texts = [m.text for m in messages if m.severity == "error"]
                self.assertTrue(any(f"missing parameter {parameter} of module hall" in t for t in texts), texts)

    def test_without_a_device_nothing_is_generated(self):
        board = self.board()
        board["devices"] = [d for d in board["devices"] if d["name"] not in ("MOT_A", "MOT_B")]
        project = self.project()
        project["modules"].pop("MOT_A")
        project["modules"].pop("MOT_B")
        project["interrupts"]["ranks"] = [r for r in project["interrupts"]["ranks"]
                                          if not r["name"].startswith("HALL_")]
        self.assertIsNone(self.run_with(board, project).hall)
        decoder, header = self.read("hallDecoderConfiguration.h"), self.read("hallSensorConfiguration.h")
        self.assertIn("#define NX_HALL_SENSOR_COUNT 0", decoder)
        self.assertIn("#define NX_HALL_SENSORS(X)\n", decoder)
        self.assertIn("#define NX_HALL_PORT_MASK 0x00U", header)
        # both headers are written for every project (GeneratedIncludesTest, PLAT-25); the family header includes
        # the neutral one and nothing else
        self.assertEqual(re.findall(r"#include .*", header), ['#include "bsp/hall/hallDecoderConfiguration.h"'])
        for absent in ("#include", "PORT_PCR", "static_assert", "struct SensorConfig", "DECODERS"):
            self.assertNotIn(absent, decoder)
        for absent in ("PORT_PCR", "static_assert", "struct SensorConfig", "mcu.h"):
            self.assertNotIn(absent, header)

    def test_device_without_project_parameters_is_not_instantiated(self):
        """A board device the project says nothing about is not used - the eval board demo is exactly that."""
        project = self.project()
        project["modules"].pop("MOT_B")
        hall = self.run_with(project=project).hall
        self.assertEqual([s["name"] for s in hall["sensors"]], ["MOT_A"])
        self.assertEqual([p["port"] for p in hall["ports"]], ["PORTA"])

    def test_port_vector_mask_matches_the_vector_table(self):
        """bsp/hall/HallVectors.h must say exactly which PORT<x>_IRQHandler the vector table of the port has."""
        vectors = (REPO / "platforms/s32k1xx/port/bspConfiguration/include/bsp/hall/HallVectors.h") \
            .read_text(encoding="utf-8")
        match = re.search(r"#define NX_PORT_PIN_VECTORS\s+(0x[0-9A-Fa-f]+)U", vectors)
        self.assertIsNotNone(match, "HallVectors.h must define NX_PORT_PIN_VECTORS")
        declared = int(match.group(1), 16)
        # SYS-170: the ARMv7-M table of the port is the generated list of the derivative (eval board S32K144);
        # every listed vector runs through Safe_<handler>, whose handler is weak (IsrWrapper.cpp)
        with tempfile.TemporaryDirectory() as tmp:
            bswgen.generate(EVAL_PROJECT_FILE if EVAL_PROJECT_FILE.exists() else PROJECT_FILE, Path(tmp))
            vectors = (Path(tmp) / "startup" / "nxIrqVectors.inc").read_text(encoding="utf-8")
        actual = 0
        for index, letter in enumerate("ABCDE"):
            if re.search(rf"^NX_IRQ_VECTOR\(PORT{letter}_IRQHandler\)", vectors, re.M):
                actual |= 1 << index
        startup = (REPO / "platforms/s32k1xx/port/main/src/bsp/startUp.S").read_text(encoding="utf-8")
        self.assertIn('#include "startup/nxIrqVectors.inc"', startup)
        self.assertEqual(declared, actual,
                         "NX_PORT_PIN_VECTORS does not match the PORT<x>_IRQHandler vectors of the generated "
                         "startup/nxIrqVectors.inc (diff in doc/changelog.d/IO-07.md)")


class BswGenHallHostTest(unittest.TestCase):
    """Compiles the access hall/HallSensors (with libs/bsw/hallDecoder) against the generated configuration and runs
    test/host of the access."""

    OPENBSW = REPO / "external" / "openbsw"
    DRIVER = REPO / "platforms/s32k1xx/bsp/bspHall"
    DECODER = REPO / "libs/bsw/hallDecoder"
    # IO-52: the pin interrupt of the Hall pins runs through the family dispatcher and the pin edge contract
    DISPATCHER = REPO / "platforms/s32k1xx/bsp/bspPinIrq"
    PIN_EDGE = REPO / "libs/bsp/bspPinEdge"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def compile(self, gen, source="HallSensorsHostTest.cpp", extra_include=None, driver=None, dispatcher=None):
        host = self.DRIVER / "test" / "host"
        includes = [host / "stubs"] + ([extra_include] if extra_include else []) + [
            self.tmp / gen, self.DRIVER / "include", self.DECODER / "include", self.DISPATCHER / "include",
            self.PIN_EDGE / "include"]
        system = [self.OPENBSW / "platforms/s32k1xx/bsp/bspMcu/include/3rdparty/nxp"]
        exe = self.tmp / f"{Path(source).stem}_{gen}.exe"
        # without a hall device the port links no dispatcher (bspPinIrq only with a consumer pin), so neither
        # does the test of that case
        with_dispatcher = source != "NoHallSensorHostTest.cpp"
        cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
               "-Wconversion", "-Wno-deprecated-volatile", *[f"-I{i}" for i in includes],
               *[f"-isystem{i}" for i in system], str(host / source),
               str(driver or (self.DRIVER / "src/hall/HallSensors.cpp")),
               *([str(dispatcher or (self.DISPATCHER / "src/pinIrq/PinIrqDispatcher.cpp"))] if with_dispatcher
                 else []),
               str(self.DECODER / "src/hallDecoder/HallDecoder.cpp"), "-o", str(exe)]
        return subprocess.run(cmd, capture_output=True, text=True), exe

    def run_test(self, gen, source="HallSensorsHostTest.cpp", driver=None):
        r, exe = self.compile(gen, source, driver=driver)
        self.assertEqual(r.returncode, 0, r.stderr[-4000:])
        r = subprocess.run([str(exe)], capture_output=True, text=True)
        print("\n" + r.stdout.strip(), file=sys.stderr)
        self.assertEqual(r.returncode, 0, r.stdout[-4000:])
        return r.stdout

    def helper(self) -> "BswGenHallTest":
        helper = BswGenHallTest("test_two_sensor_sets_with_both_arrangements")
        helper.tmp = self.tmp
        return helper

    def generate(self, name, board=None, project=None):
        helper = self.helper()
        bswgen.generate(helper.write(board, project), self.tmp / name)
        return name

    def test_driver_runtime_two_sets(self):
        """Two sets: 120 degrees / 1 pole pair / one port, 60 degrees / 8 pole pairs / inverted / two ports."""
        self.run_test(self.generate("two"))

    def test_driver_runtime_eval_board(self):
        """The eval board set (120 degrees, 4 pole pairs, PORTA and PORTB) with the probe build parameters."""
        helper = self.helper()
        self.run_test(self.generate("demo", copy.deepcopy(BOARD), helper.demo_project()))

    def test_without_a_device_the_driver_still_links(self):
        helper = self.helper()
        board = helper.board()
        board["devices"] = [d for d in board["devices"] if d["name"] not in ("MOT_A", "MOT_B")]
        project = helper.project()
        project["modules"].pop("MOT_A")
        project["modules"].pop("MOT_B")
        project["interrupts"]["ranks"] = [r for r in project["interrupts"]["ranks"]
                                          if not r["name"].startswith("HALL_")]
        self.run_test(self.generate("none", board, project), "NoHallSensorHostTest.cpp")
        # with a generat that has a sensor the same file must fail: the test would pass anyway
        r, _ = self.compile("two" if (self.tmp / "two").is_dir() else self.generate("two"),
                            "NoHallSensorHostTest.cpp")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("NX_HALL_SENSOR_COUNT", r.stderr)

    def test_static_asserts_reject_hand_edits(self):
        self.generate("two")
        gen = self.tmp / "two" / "bsp" / "hall"
        files = {name: (gen / name).read_text(encoding="utf-8")
                 for name in ("hallDecoderConfiguration.h", "hallSensorConfiguration.h")}
        cases = [
            # a sector twice in one table: two positions would look the same
            ("{ 0U, 5U, 3U, 4U, 1U, 6U, 2U, 0U }", "{ 0U, 5U, 3U, 4U, 1U, 6U, 5U, 0U }",
             "six states to the sectors 1..6"),
            # only five valid states
            ("{ 0U, 5U, 3U, 4U, 1U, 6U, 2U, 0U }", "{ 0U, 5U, 3U, 4U, 1U, 6U, 0U, 0U }",
             "six states to the sectors 1..6"),
            # a direction sign that is neither forward nor backward
            ("1, 1U, 50U, 300000U", "0, 1U, 50U, 300000U", "six states to the sectors 1..6"),
            # a signal on a pin the interrupt of its port does not cover
            ("{ 0U, 0x00000007U, PORTA_IRQn, 3U }", "{ 0U, 0x00000005U, PORTA_IRQn, 3U }",
             "port interrupt reaches its sensor"),
            # a standstill time that does not fit the microsecond time base six times over
            ("50U, 300000U", "50U, 400000000U", "do not fit the microsecond time base"),
            # sensor count out of step with the macro
            ("NX_HALL_SENSOR_COUNT 2", "NX_HALL_SENSOR_COUNT 3", "does not match the macro"),
        ]
        for old, new, message in cases:
            with self.subTest(message=message, new=new):
                patched = self.tmp / "patched" / "bsp" / "hall"
                patched.mkdir(parents=True, exist_ok=True)
                hits = [name for name, text in files.items() if old in text]
                self.assertEqual(len(hits), 1, f"{old} must stand in exactly one generated hall header")
                # both headers into the patched directory: the family header includes the neutral one with
                # quotes, which would otherwise find the unpatched sibling first
                for name, text in files.items():
                    (patched / name).write_text(text.replace(old, new, 1) if name == hits[0] else text,
                                                encoding="utf-8")
                r, _ = self.compile("two", extra_include=self.tmp / "patched")
                self.assertNotEqual(r.returncode, 0)
                self.assertIn(message, r.stderr)

    # Mutation test (IO-07): the host test of the access has to find these faults in
    # platforms/s32k1xx/bsp/bspHall/src/hall/HallSensors.cpp; the test must then fail (or not compile). The faults
    # of the evaluation are the mutations of HallDecoderHostTest.
    MUTATIONS = [
        ("inversion of the board ignored", "return static_cast<uint8_t>(state ^ cfg.invert);", "return state;"),
        ("read without the interrupt lock",
         "interrupts::SuspendResumeAllInterruptsScopedLock const lock;\n    uint32_t const now  = getSystemTimeUs32Bit();",
         "uint32_t const now  = getSystemTimeUs32Bit();"),
        ("time taken before the lock",
         "interrupts::SuspendResumeAllInterruptsScopedLock const lock;\n    uint32_t const now  = getSystemTimeUs32Bit();",
         "uint32_t const now  = getSystemTimeUs32Bit();\n    interrupts::SuspendResumeAllInterruptsScopedLock const lock;"),
        ("decoder not started from the pins", "decoders[i].start(sample(hall::SENSORS[i]), now);",
         "decoders[i].start(0U, now);"),
        # IO-52: the edge of the dispatcher reaches the sensor of its channel, at the time of the interrupt
        ("edge of every channel given to the first sensor", "HallSensors::edge(channel, timestampUs);",
         "HallSensors::edge(static_cast<uint16_t>(channel & 0U), timestampUs);"),
        ("edge stamped with its own time instead of the interrupt time",
         "decoders[sensor].edge(sample(hall::SENSORS[sensor]), nowUs);",
         "decoders[sensor].edge(sample(hall::SENSORS[sensor]), nowUs + 1U);"),
    ]
    # IO-52: faults of the family dispatcher (platforms/s32k1xx/bsp/bspPinIrq) that the Hall path must notice as
    # well - the moved checks of the IO-07 access (foreign flags, stale flags, second port, NVIC level). The
    # dispatcher has its own host test with more mutants (pin-irq, test_pin_irq_host.py).
    DISPATCHER_MUTATIONS = [
        ("interrupt flags of other pins cleared", "regs.ISFR = flags;", "regs.ISFR = 0xFFFFFFFFU;"),
        ("stale flags not cleared at init", "PORT_REGS[p]->ISFR = mask;", "(void)mask;"),
        ("second port of a sensor not served", "uint8_t const ports = _config.vectors[vector].ports;",
         "uint8_t const ports = 1U;"),
        ("NVIC level of the rank ignored", "NVIC_SetPriority(vec.irq, vec.level);", "NVIC_SetPriority(vec.irq, 0U);"),
        ("edge not armed", "writeIrqc(*PORT_REGS[use.port], use.pin, static_cast<uint32_t>(use.edge));",
         "writeIrqc(*PORT_REGS[use.port], use.pin, 0U);"),
    ]

    def survivors(self, mutations, source_path, key):
        source = source_path.read_text(encoding="utf-8")
        survivors = []
        for name, old, new in mutations:
            with self.subTest(mutation=name):
                self.assertIn(old, source, f"mutation '{name}' does not apply any more")
                patched = self.tmp / "mutants"
                patched.mkdir(parents=True, exist_ok=True)
                path = patched / f"{name.replace(' ', '_')}.cpp"
                path.write_text(source.replace(old, new, 1), encoding="utf-8")
                r, exe = self.compile("two", **{key: path})
                if r.returncode != 0:
                    continue        # does not even compile: found
                run = subprocess.run([str(exe)], capture_output=True, text=True)
                if run.returncode == 0:
                    survivors.append(name)
        return survivors

    def test_mutations_are_found(self):
        """Every built-in fault has to make the host test fail, otherwise the test says nothing."""
        self.generate("two")
        survivors = self.survivors(self.MUTATIONS, self.DRIVER / "src/hall/HallSensors.cpp", "driver")
        survivors += self.survivors(self.DISPATCHER_MUTATIONS, self.DISPATCHER / "src/pinIrq/PinIrqDispatcher.cpp",
                                    "dispatcher")
        self.assertEqual(survivors, [], f"the host test did not notice these faults: {survivors}")


class HallDecoderHostTest(unittest.TestCase):
    """libs/bsw/hallDecoder (IO-07): family neutral Hall evaluation, host test without MCU stubs and its mutations."""

    DECODER = REPO / "libs/bsw/hallDecoder"
    TEST = DECODER / "test/host/HallDecoderHostTest.cpp"
    SOURCE = DECODER / "src/hallDecoder/HallDecoder.cpp"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def compile(self, source, name="hall_decoder_host_test"):
        exe = self.tmp / f"{name}.exe"
        cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
               "-Wconversion", f"-I{self.DECODER / 'include'}", str(source), str(self.TEST), "-o", str(exe)]
        return subprocess.run(cmd, capture_output=True, text=True), exe

    def test_decoder_runtime(self):
        r, exe = self.compile(self.SOURCE)
        self.assertEqual(r.returncode, 0, r.stderr[-4000:])
        r = subprocess.run([str(exe)], capture_output=True, text=True)
        print("\n" + r.stdout.strip(), file=sys.stderr)
        self.assertEqual(r.returncode, 0, r.stdout[-4000:])

    def test_decoder_is_family_neutral(self):
        """No MCU header, no register, no system timer, no generated configuration (treiber-granularitaet.md)."""
        for path in [*(self.DECODER / "include").rglob("*.h"), *(self.DECODER / "src").rglob("*.cpp")]:
            text = path.read_text(encoding="utf-8")
            includes = re.findall(r'#include\s*[<"]([^>"]+)[>"]', text)
            with self.subTest(file=path.name):
                self.assertEqual([i for i in includes if i not in ("cstdint", "hallDecoder/HallDecoder.h")], [])
                for word in ("mcu", "PORT_", "GPIO", "NVIC", "SystemTimer", "getSystemTime", "Configuration.h"):
                    self.assertNotIn(word, text)

    # Faults of the evaluation the decoder host test has to find.
    MUTATIONS = [
        ("no debouncing", "if ((nowUs - _core.lastEdgeUs) < _config.debounceUs)\n    {", "if (false)\n    {"),
        ("bounce moves the state", "++_statistics.bounces;\n        return;",
         "++_statistics.bounces;\n        _core.lastState = state;\n        return;"),
        ("glitch never taken back (no confirmation at the next edge)",
         "if (_core.provisional && ((nowUs - _core.lastEdgeUs) >= _config.debounceUs))\n    {\n        settle(_core.levelSeen, nowUs);",
         "if (false)\n    {\n        settle(_core.levelSeen, nowUs);"),
        ("glitch never taken back (no confirmation in read)",
         "if (_core.provisional && ((nowUs - _core.lastEdgeUs) >= _config.debounceUs))\n    {\n        settle(state, nowUs);",
         "if (false)\n    {\n        settle(state, nowUs);"),
        ("take back keeps the statistics of the glitch", "_statistics             = _statisticsBefore;", ""),
        ("third state after a glitch not accepted", "accept(level, glitchUs);", ""),
        ("window checked with the new level", "settle(_core.levelSeen, nowUs);", "settle(state, nowUs);"),
        ("invalid state becomes a sector", "if (sector == 0U)\n    {\n        // A combination",
         "if (false)\n    {\n        // A combination"),
        ("a jumped sector is a step", "++_statistics.sectorJumps;\n            _core.faulted = true;",
         "++_statistics.sectorJumps;"),
        ("period over five steps instead of six",
         "static_cast<uint8_t>((_core.write + STAMPS - SECTORS) & STAMP_MASK)",
         "static_cast<uint8_t>((_core.write + STAMPS - SECTORS + 1U) & STAMP_MASK)"),
        ("result before a full revolution", "if (_core.steps < SECTORS)\n    {\n        return;",
         "if (_core.steps < 1U)\n    {\n        return;"),
        ("standstill not latched", "_core.standstill = true;", "_core.standstill = false;"),
        ("standstill never detected", "((nowUs - _core.lastEdgeUs) > _config.standstillUs) && !_core.standstill",
         "false"),
        ("standstill keeps the run", "_core.lastSector = 0U;\n        ++_statistics.standstills;",
         "++_statistics.standstills;"),
        ("direction not taken from the transition", "direction = static_cast<int8_t>(-_config.forwardSign);",
         "direction = _config.forwardSign;"),
        ("pole pairs ignored in the mechanical period", "_core.periodUs * _config.polePairs", "_core.periodUs"),
        ("sampled position ignored in read", "uint8_t const sector = _config.sectors[state];\n    Reading r;",
         "uint8_t const sector = _config.sectors[_core.lastState];\n    Reading r;"),
    ]

    def test_mutations_are_found(self):
        source = self.SOURCE.read_text(encoding="utf-8")
        survivors = []
        for index, (name, old, new) in enumerate(self.MUTATIONS):
            with self.subTest(mutation=name):
                self.assertIn(old, source, f"mutation '{name}' does not apply any more")
                path = self.tmp / f"mutant_{index}.cpp"
                path.write_text(source.replace(old, new, 1), encoding="utf-8")
                r, exe = self.compile(path, f"mutant_{index}")
                if r.returncode != 0:
                    continue
                if subprocess.run([str(exe)], capture_output=True, text=True).returncode == 0:
                    survivors.append(name)
        self.assertEqual(survivors, [], f"the decoder host test did not notice these faults: {survivors}")


class BswGenClockTest(unittest.TestCase):
    """Generated clock configuration (boardClock.h, CAN bit timing, UART baud register) of the clock model (ARCH-06).

    The eval board values are compared with the hand-written configuration they replace (commit ba278f3:
    bsp/clock/boardClock.h, canConfiguration.h, sbcConfiguration.h, UartConfig.cpp). The headers are compiled on the
    host with those expectations as static_assert, for the eval board and for the 8-MHz s32k1xx test profile.
    """

    OPENBSW = REPO / "external" / "openbsw"
    S32K_PROJECT = REPO / "tools/nxconfig/test/profiles/projects/testproject_s32k_8mhz/project.yaml"
    # ARCH-18: same board, but this project chooses other functional clocks than the derivative's defaults
    PCC_PROJECT = REPO / "tools/nxconfig/test/profiles/projects/testproject_s32k_pcc/project.yaml"
    # Register model of the PCC for the host: the generated nxEnablePeripheralClocks() writes into it, the test
    # reads the result. The indices are the ones of the S32K1 device header (S32K148_PCC.h PCC_<x>_INDEX).
    PCC_STUB = """
#include <cstdint>
struct PccModel { volatile uint32_t PCCn[128]; };
extern PccModel pccModel;
#define PCC (&pccModel)
#define PCC_FTFC_INDEX 32
#define PCC_DMAMUX_INDEX 33
#define PCC_CRC_INDEX 50
#define PCC_PDB0_INDEX 54
#define PCC_RTC_INDEX 61
#define PCC_PORTA_INDEX 73
#define PCC_PORTB_INDEX 74
#define PCC_PORTC_INDEX 75
#define PCC_PORTD_INDEX 76
#define PCC_PORTE_INDEX 77
#define PCC_CMP0_INDEX 92
#define PCC_LPIT_INDEX 55
#define PCC_LPTMR0_INDEX 64
#define PCC_FTM0_INDEX 56
#define PCC_FTM1_INDEX 57
#define PCC_FTM2_INDEX 58
#define PCC_ADC0_INDEX 59
#define PCC_ADC1_INDEX 39
#define PCC_LPSPI0_INDEX 44
#define PCC_LPSPI1_INDEX 45
#define PCC_LPSPI2_INDEX 46
#define PCC_LPUART0_INDEX 106
#define PCC_LPUART1_INDEX 107
#define PCC_LPUART2_INDEX 108
#define PCC_LPI2C0_INDEX 102
#define PCC_FlexIO_INDEX 90
#define PCC_FlexCAN0_INDEX 36
#define PCC_FlexCAN1_INDEX 37
#define PCC_FlexCAN2_INDEX 43
"""

    # previous hand-written values of the eval board (80 MHz from SPLL, 40 MHz crystal)
    EVAL_EXPECTATIONS = """
static_assert(_SCG_RCCR_ == ((6U << 24) | (1U << 16) | (1U << 4) | 2U), "RCCR");
static_assert(PLL_CONFIG == ((3U << 8) | ((32U - 16U) << 16)), "SPLLCFG PREDIV 4, MULT 32");
static_assert(_SOSCDIV1 == 1 && _SOSCDIV2 == 1 && _FIRCDIV1 == 1 && _FIRCDIV2 == 1, "DIV");
static_assert(_SIRCDIV1 == 1 && _SIRCDIV2 == 1, "DIV");
// ARCH-22: no longer the hand-written 1 - S32K1xx RM Rev. 13 Table 27-1 allows SPLLDIV1 80 MHz, SPLLDIV2 40 MHz in RUN
static_assert(_SPLLDIV1 == 2 && _SPLLDIV2 == 3, "SPLLDIV1 / 2 = 80 MHz, SPLLDIV2 / 4 = 40 MHz");
static_assert(NX_CLOCK_FTM1_HZ == 80000000U && NX_CLOCK_FTM3_HZ == 80000000U, "FTM1/FTM3 default SPLLDIV1");
static_assert(CPU_XTAL_CLK_HZ == 40000000U, "crystal");
static_assert(CLOCK_SOURCE_FTM0 == (1UL << 24) && CLOCK_SOURCE_FTM1 == (6UL << 24) && CLOCK_SOURCE_FTM2 == (1UL << 24), "FTM");
static_assert(CLOCK_SOURCE_FTM3 == (6UL << 24), "FTM");
static_assert(CLOCK_SOURCE_ADC0 == (3UL << 24) && CLOCK_SOURCE_ADC1 == (3UL << 24), "ADC");
static_assert(CLOCK_SOURCE_LPSPI0 == (3UL << 24) && CLOCK_SOURCE_LPSPI1 == (3UL << 24) && CLOCK_SOURCE_LPSPI2 == (3UL << 24), "LPSPI");
static_assert(CLOCK_SOURCE_LPIT == (2UL << 24) && CLOCK_SOURCE_LPTMR0 == (1UL << 24), "LPIT/LPTMR");
static_assert(CLOCK_SOURCE_FLEXIO == (1UL << 24) && CLOCK_SOURCE_LPI2C0 == (1UL << 24), "FlexIO/LPI2C");
static_assert(CLOCK_SOURCE_LPUART0 == (1UL << 24) && CLOCK_SOURCE_LPUART1 == (3UL << 24) && CLOCK_SOURCE_LPUART2 == (3UL << 24), "LPUART");
// ARCH-18: no CLOCK_SOURCE_FTM4/FTM5/ENET any more - the S32K144 has none of them, and mcu/mcuDerivative.h
// undefines PCC_FTM4_INDEX/PCC_FTM5_INDEX/PCC_ENET_INDEX of the S32K148 header, so the driver leaves the blocks out.
#if defined(CLOCK_SOURCE_FTM4) || defined(CLOCK_SOURCE_FTM5) || defined(CLOCK_SOURCE_ENET)
#error "macro of a peripheral the derivative does not have"
#endif
static_assert(CLOCKoUT_SELECT == 0 && _RCM_SRIE_ == ((3UL) | (1UL << 7) | (1UL << 5)), "legacy");
static_assert(NX_CLOCK_LPSPI1_HZ == 48000000U, "sbcConfiguration.h LPSPI1 functional clock");
static_assert(NX_CLOCK_CORE_HZ == 80000000U && NX_CLOCK_BUS_HZ == 40000000U, "FreeRtosPlatformConfig.h");
static_assert(::bsp::can::CAN0_CTRL1_TIMING == 0x04620006U && ::bsp::can::CAN0_BITRATE == 500000U, "CAN0 CTRL1");
static_assert(NX_UART_UART_BAUD_REGISTER == ((15U << 24) + 26U), "UartConfig.cpp: OSR(15) + SBR(26)");
"""
    # 8 MHz crystal, 64 MHz system clock: calculated by hand
    S32K_EXPECTATIONS = """
static_assert(_SCG_RCCR_ == ((6U << 24) | (1U << 16) | (1U << 4) | 2U), "SPLL / 2, bus / 2, slow / 3");
static_assert(PLL_CONFIG == ((0U << 8) | ((32U - 16U) << 16)), "8 MHz / 1 * 32 = VCO 256 MHz");
static_assert(CPU_XTAL_CLK_HZ == 8000000U && NX_CLOCK_SPLL_HZ == 128000000U && NX_CLOCK_CORE_HZ == 64000000U, "clocks");
static_assert(_SPLLDIV1 == 2 && _SPLLDIV2 == 3 && _SOSCDIV1 == 1 && _SOSCDIV2 == 1, "SPLL 128 MHz: DIV1 / 2, DIV2 / 4");
static_assert(NX_CLOCK_BUS_HZ == 32000000U, "bus / 2 is below 40 MHz with the SPLL");
static_assert(::bsp::can::VEH_CTRL1_TIMING == ((0U << 24) | (1U << 22) | (4U << 19) | (1U << 16) | 7U), "VEH 1+8+5+2, SJW 2");
static_assert(::bsp::can::DIAG_CTRL1_TIMING == ((1U << 24) | (3U << 22) | (3U << 19) | (3U << 16) | 6U), "DIAG /2, 1+7+4+4");
static_assert(NX_UART_CONSOLE_BAUD_REGISTER == ((15U << 24) | 13U), "230400 Bd: 48 MHz / (16 x 13)");
"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def compile(self, gen: Path, expectations: str, extra_include=None):
        source = self.tmp / "clock_host_test.cpp"
        source.write_text('#include "bsp/clock/boardClock.h"\n#include "bsp/can/canBitTimingConfiguration.h"\n'
                          '#include "bsp/uart/uartConfiguration.h"\n' + expectations + "int main() { return 0; }\n",
                          encoding="utf-8")
        includes = ([extra_include] if extra_include else []) + [
            gen, REPO / "platforms/s32k1xx/port/bspConfiguration/include"]
        cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
               *[f"-I{i}" for i in includes], str(source), "-o", str(self.tmp / "clock_host_test.exe")]
        return subprocess.run(cmd, capture_output=True, text=True)

    @on_eval_board
    def test_eval_board_values_match_the_hand_written_configuration(self):
        board = bswgen.generate(PROJECT_FILE, self.tmp / "gen")
        self.assertEqual(board.warnings, [])
        r = self.compile(self.tmp / "gen", self.EVAL_EXPECTATIONS)
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        text = (self.tmp / "gen/bsp/clock/boardClock.h").read_text(encoding="utf-8")
        self.assertIn("#define CPU_XTAL_CLK_HZ 40000000U", text)
        self.assertIn("#define _SCG_RCCR_SCS     (6UL << 24)", text)   # TOOL-200: UL suffix (SYS-105 M7)

    def test_other_crystal_and_system_clock(self):
        board = bswgen.generate(self.S32K_PROJECT, self.tmp / "gen")
        self.assertEqual(board.warnings, [])                 # ARCH-13: no port warning for 64 MHz any more
        # ARCH-42: the application uses VEH only, DIAG is on FlexCAN1 (16 message buffers on the S32K144)
        self.assertEqual([c["name"] for c in board.can], ["VEH"])
        self.assertEqual((board.pwm["clock_hz"], board.pwm["period_ticks"]), (64_000_000, 2560))
        only_veh = "\n".join(line for line in self.S32K_EXPECTATIONS.splitlines() if "DIAG_" not in line)
        r = self.compile(self.tmp / "gen", only_veh)
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        # The second bit rate (prescaler 2) reaches the generat as soon as DIAG is used; the bit timing does not
        # depend on the message buffers, so this part runs with a driver for 16 buffers (COM-26).
        project = yaml.safe_load(self.S32K_PROJECT.read_text(encoding="utf-8"))
        project["project"]["board"] = (REPO / "tools/nxconfig/test/profiles/boards/testboard_s32k_8mhz").as_posix()
        project["use"]["can"] = "all"
        project["interrupts"]["ranks"].append({"source": "can", "name": "DIAG", "rank": 3})
        (self.tmp / "both").mkdir()
        (self.tmp / "both/project.yaml").write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        with mock.patch.dict(bswgen.nxchecks.CAN_DRIVER_MESSAGE_BUFFERS, {"s32k1xx": (32, "OpenBSW FlexCANDevice")}), \
                self.assertRaisesRegex(bswgen.ConfigError, "CAN bus DIAG is on FlexCAN1, which has 16 message buffers"):
            bswgen.generate(self.tmp / "both/project.yaml", self.tmp / "gen2")
        board = bswgen.generate(self.tmp / "both/project.yaml", self.tmp / "gen2")     # COM-26: 16 buffers served
        self.assertEqual([c["name"] for c in board.can], ["VEH", "DIAG"])
        r = self.compile(self.tmp / "gen2", self.S32K_EXPECTATIONS)
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        self.assertIn("DIAG on FlexCAN1: 250000 bit/s, 16 tq = 1 + 7 + 4 + 4, sample point 75 %",
                      (self.tmp / "gen2/bsp/can/canBitTimingConfiguration.h").read_text(encoding="utf-8"))

    def test_derived_output_dividers_reach_the_generat(self):
        """ARCH-22: other limits in the derivative -> other SCG dividers in boardClock.h, without touching a template.

        8-MHz profile (SPLL 128 MHz, 64 MHz core) against a copy of mcudb whose S32K144 carries synthetic limits.
        """
        mcudb = self.tmp / "mcudb"
        shutil.copytree(REPO / "mcudb", mcudb, ignore=shutil.ignore_patterns("sources"))
        file = mcudb / "s32k1xx" / "S32K144.yaml"
        doc = yaml.safe_load(file.read_text(encoding="utf-8"))
        doc["clock"]["output_max_hz"]["max_hz"].update(SPLLDIV1=20_000_000, SPLLDIV2=3_000_000, FIRCDIV2=12_000_000,
                                                       SIRCDIV2=4_000_000)
        doc["clock"]["system_source_max_hz"]["SPLL"]["max_hz"]["bus"] = 16_000_000
        file.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        board = bswgen.generate(self.S32K_PROJECT, self.tmp / "gen", mcudb)
        self.assertEqual(board.warnings, [])
        text = (self.tmp / "gen/bsp/clock/boardClock.h").read_text(encoding="utf-8")
        self.assertIn("#define _SPLLDIV1 (4UL) // / 8 = 16000000 Hz, at most 20000000 Hz", text)
        self.assertIn("#define _SPLLDIV2 (7UL) // / 64 = 2000000 Hz, at most 3000000 Hz", text)
        self.assertIn("#define _SOSCDIV2 (1UL) // / 1 = 8000000 Hz, at most 40000000 Hz", text)
        r = self.compile(self.tmp / "gen", """
static_assert(_SPLLDIV1 == 4 && _SPLLDIV2 == 7 && _FIRCDIV1 == 1 && _FIRCDIV2 == 3, "SPLL / 8, / 64; FIRC / 1, / 4");
static_assert(_SIRCDIV1 == 1 && _SIRCDIV2 == 2 && _SOSCDIV1 == 1 && _SOSCDIV2 == 1, "SIRC / 1, / 2; SOSC / 1");
static_assert(_SCG_RCCR_DIVBUS == (3 << 4) && NX_CLOCK_BUS_HZ == 16000000U, "bus / 4 below 16 MHz from the SPLL");
static_assert(NX_CLOCK_FIRC_HZ == 48000000U && NX_CLOCK_SIRC_HZ == 8000000U, "sources stay undivided");
static_assert(NX_CLOCK_LPUART1_HZ == 12000000U && NX_CLOCK_LPIT_HZ == 4000000U, "functional clocks divided");
static_assert(NX_CLOCK_FTM1_HZ == 16000000U, "FTM1 default SPLLDIV1");
static_assert(NX_UART_CONSOLE_BAUD_REGISTER != ((15U << 24) | 13U), "UART divider from 12 MHz, not from 48 MHz");
""")
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])

    def test_without_crystal(self):
        doc = yaml.safe_load((REPO / "tools/nxconfig/test/profiles/boards/testboard_s32k_8mhz/board.yaml")
                             .read_text(encoding="utf-8"))
        del doc["board"]["clock"]
        project = yaml.safe_load(self.S32K_PROJECT.read_text(encoding="utf-8"))
        project["clock"]["system_hz"] = 48_000_000
        project["use"]["can"], project["can"] = [], {}
        (self.tmp / "boards/b").mkdir(parents=True)
        (self.tmp / "projects/p").mkdir(parents=True)
        (self.tmp / "boards/b/board.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
        project["project"]["board"] = "../../boards/b"
        path = self.tmp / "projects/p/project.yaml"
        path.write_text(yaml.safe_dump(project), encoding="utf-8")
        bswgen.generate(path, self.tmp / "gen")
        text = (self.tmp / "gen/bsp/clock/boardClock.h").read_text(encoding="utf-8")
        self.assertNotIn("CPU_XTAL_CLK_HZ", text)            # clockConfig.cpp starts neither SOSC nor SPLL
        self.assertIn("#define PLL_CONFIG (0UL)", text)
        self.assertIn("#define CLOCK_SOURCE_FTM1 CLOCKSET_PCC(SYSTEMCLICK_SCS_FIRC_CLK, 0, 0)", text)
        self.assertIn("the project uses no CAN bus", (self.tmp / "gen/bsp/can/canBitTimingConfiguration.h")
                      .read_text(encoding="utf-8"))
        r = self.compile(self.tmp / "gen", 'static_assert(_SCG_RCCR_ == ((3U << 24) | 1U), "FIRC, slow / 2");\n')
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])

    def run_peripheral_clocks(self, gen: Path, checks: str, extra_include=None) -> subprocess.CompletedProcess:
        """Compiles and runs the generated bsp/clock/peripheralClocks.h against a PCC register model (ARCH-18)."""
        stub = self.tmp / "stubs" / "mcu"
        stub.mkdir(parents=True, exist_ok=True)
        (stub / "mcu.h").write_text("#pragma once\n" + self.PCC_STUB, encoding="utf-8")
        source = self.tmp / "pcc_host_test.cpp"
        source.write_text('#include "bsp/clock/peripheralClocks.h"\n\n#include <cstdio>\n\n'
                          "PccModel pccModel{};\n\n"
                          "static int fail = 0;\n"
                          "static void expect(const char* name, uint32_t got, uint32_t want)\n"
                          "{\n"
                          "    if (got != want)\n"
                          "    {\n"
                          '        printf("%s: got 0x%08x, want 0x%08x\\n", name, got, want);\n'
                          "        ++fail;\n"
                          "    }\n"
                          "}\n\n"
                          "int main()\n{\n    nxEnablePeripheralClocks();\n" + checks +
                          "    return fail;\n}\n", encoding="utf-8")
        includes = ([extra_include] if extra_include else []) + [self.tmp / "stubs", gen]
        exe = self.tmp / "pcc_host_test.exe"
        cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
               *[f"-I{i}" for i in includes], str(source), "-o", str(exe)]
        build = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(build.returncode, 0, build.stderr[-3000:])
        return subprocess.run([str(exe)], capture_output=True, text=True)

    def target_compile_peripheral_clocks(self, gen: Path):
        """Compiles the generated PCC writes against the *real* device header with the flags of the target build.

        The register index of a gate is named after the instance of the device header (PCC_FlexIO_INDEX), the
        clock select after the gate of the driver (CLOCK_SOURCE_FLEXIO) - only the target compiler sees a
        mismatch, the host stub would accept any spelling.
        """
        name = "arm-none-eabi-g++.exe" if sys.platform == "win32" else "arm-none-eabi-g++"
        env_bin = os.environ.get("NX_ARM_GCC_BIN")
        gxx = Path(env_bin) / name if env_bin and (Path(env_bin) / name).exists() else shutil.which(name)
        if not gxx:
            print("\narm-none-eabi-g++ not found: target compiler check skipped", file=sys.stderr)
            return
        source = self.tmp / "pcc_target_check.cpp"
        source.write_text('#include "bsp/clock/peripheralClocks.h"\n'
                          'extern "C" void nxCheckPeripheralClocks(void);\n'
                          'extern "C" void nxCheckPeripheralClocks(void) { nxEnablePeripheralClocks(); }\n',
                          encoding="utf-8")
        includes = [gen, REPO / "platforms/s32k1xx/bsp/bspMcu/include",
                    self.OPENBSW / "platforms/s32k1xx/bsp/bspMcu/include",
                    self.OPENBSW / "libs/3rdparty/cmsis"]
        cmd = [str(gxx), "-std=c++17", "-mcpu=cortex-m4", "-mthumb", "-O2", "-fshort-enums", "-Wall", "-Wextra",
               "-Wvla", "-Werror", "-fsyntax-only", *[f"-I{i}" for i in includes], str(source)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])

    @on_eval_board
    def test_pcc_writes_of_the_eval_board(self):
        """ARCH-18: one PCC write per used instance, with the clock select of the configuration."""
        bswgen.generate(PROJECT_FILE, self.tmp / "gen")
        text = (self.tmp / "gen/bsp/clock/peripheralClocks.h").read_text(encoding="utf-8")
        self.assertIn("// FTM0: used by pwm MOT1, SOSCDIV1 (default of the derivative), 40000000 Hz", text)
        self.assertIn("// FlexCAN0: used by can CAN0, no clock select (PCS)", text)
        self.assertIn("// LPIT: clock gate without an instance (mcudb clock.pcc), SIRCDIV2", text)
        # FTM1/FTM3 are not used by this project: the OpenBSW driver switches their clock on anyway, the
        # generated function does not (that is the point of "per used instance")
        self.assertNotIn("PCC_FTM1_INDEX", text)
        self.assertNotIn("PCC_LPUART0_INDEX", text)
        result = self.run_peripheral_clocks(self.tmp / "gen", """
    expect("FTM0", pccModel.PCCn[PCC_FTM0_INDEX], (1u << 24) | 0x40000000u);   // SOSCDIV1
    expect("FTM2", pccModel.PCCn[PCC_FTM2_INDEX], (1u << 24) | 0x40000000u);   // SOSCDIV1, input capture
    expect("ADC0", pccModel.PCCn[PCC_ADC0_INDEX], (3u << 24) | 0x40000000u);   // FIRCDIV2
    expect("LPSPI1", pccModel.PCCn[PCC_LPSPI1_INDEX], (3u << 24) | 0x40000000u);
    expect("LPUART2", pccModel.PCCn[PCC_LPUART2_INDEX], (3u << 24) | 0x40000000u);
    expect("FLEXIO", pccModel.PCCn[PCC_FlexIO_INDEX], (1u << 24) | 0x40000000u);
    expect("LPIT", pccModel.PCCn[PCC_LPIT_INDEX], (2u << 24) | 0x40000000u);   // SIRCDIV2
    expect("FlexCAN0", pccModel.PCCn[PCC_FlexCAN0_INDEX], 0x40000000u);        // no clock select
    expect("PORTA", pccModel.PCCn[PCC_PORTA_INDEX], 0x40000000u);
    expect("FTM1 (unused)", pccModel.PCCn[PCC_FTM1_INDEX], 0u);
    expect("LPUART0 (unused)", pccModel.PCCn[PCC_LPUART0_INDEX], 0u);
""")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.target_compile_peripheral_clocks(self.tmp / "gen")

    def test_project_decision_reaches_the_generat(self):
        """ARCH-18: testproject_s32k_pcc chooses SOSC for the console and FIRC for the PWM timer."""
        board = bswgen.generate(self.PCC_PROJECT, self.tmp / "gen")
        self.assertEqual(board.warnings, [])
        clock = (self.tmp / "gen/bsp/clock/boardClock.h").read_text(encoding="utf-8")
        self.assertIn("#define NX_CLOCK_LPUART1_HZ 8000000U // SOSCDIV2 (peripheral_clocks)", clock)
        self.assertIn("#define NX_CLOCK_FTM1_HZ 48000000U // FIRCDIV1 (peripheral_clocks)", clock)
        self.assertIn("#define CLOCK_SOURCE_LPUART1 CLOCKSET_PCC(SYSTEMCLICK_SCS_OSC_CLK, 0, 0)", clock)
        pcc = (self.tmp / "gen/bsp/clock/peripheralClocks.h").read_text(encoding="utf-8")
        self.assertIn("// LPUART1: used by uart CONSOLE, SOSCDIV2 (project.yaml peripheral_clocks)", pcc)
        self.assertIn("// FTM1: used by pwm FAN, FIRCDIV1 (project.yaml peripheral_clocks)", pcc)
        # the PWM counter still runs on the system clock (FTM SC.CLKS = 01), not on the PCC clock
        self.assertEqual((board.pwm["clock_hz"], board.pwm["period_ticks"]), (64_000_000, 2560))
        # the UART divider is calculated from the chosen 8 MHz, not from the 48 MHz of the default
        r = self.compile(self.tmp / "gen", 'static_assert(NX_UART_CONSOLE_BAUD_REGISTER == ((22U << 24) | 3U),'
                                           ' "115200 Bd from 8 MHz: OSR 23 x SBR 3");\n')
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        result = self.run_peripheral_clocks(self.tmp / "gen", """
    expect("LPUART1", pccModel.PCCn[PCC_LPUART1_INDEX], (1u << 24) | 0x40000000u);  // SOSCDIV2, chosen
    expect("FTM1", pccModel.PCCn[PCC_FTM1_INDEX], (3u << 24) | 0x40000000u);        // FIRCDIV1, chosen
    expect("ADC0", pccModel.PCCn[PCC_ADC0_INDEX], (1u << 24) | 0x40000000u);        // SOSCDIV2, chosen
""")
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_instance_outside_the_driver_gates(self):
        """ARCH-18: FTM6 of an S32K148 gets its PCC write from the generator, with a warning for the port.

        The OpenBSW clock driver does not write PCC_FTM6 (there is no CLOCK_SOURCE_FTM6 in its list), so the
        clock reaches the silicon only once the board port calls the generated function. That is the second
        half of ARCH-09 B5: the configuration is complete, the integration is not - and it says so.
        """
        board = yaml.safe_load((REPO / "boards/buildtest_s32k148/board.yaml").read_text(encoding="utf-8"))
        project = yaml.safe_load((REPO / "projects/buildtest_s32k148_min/project.yaml").read_text(encoding="utf-8"))
        board["pwm"][0]["instance"] = "FTM6"             # [MUX] FTM6_CH0 PTB20, FTM6_CH1 PTB21 (LQFP176)
        board["pwm"][0]["channels"] = [{"name": "MOT1_U_PWM_HS", "channel": 0, "pin": "PTB20"},
                                       {"name": "MOT1_U_PWM_LS", "channel": 1, "pin": "PTB21"}]
        project["pwm"] = {"MOT1": {"frequency_hz": 20000, "deadtime_ns": 1000, "verify": True}}
        project["peripheral_clocks"] = {"FTM6": {"select": "SOSC", "divider": 1}}
        without_rte(project)
        (self.tmp / "boards/b").mkdir(parents=True)
        (self.tmp / "projects/p").mkdir(parents=True)
        (self.tmp / "boards/b/board.yaml").write_text(yaml.safe_dump(board, sort_keys=False), encoding="utf-8")
        project["project"]["board"] = "../../boards/b"
        path = self.tmp / "projects/p/project.yaml"
        path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        gen = self.tmp / "gen"
        board_gen = bswgen.generate(path, gen)
        self.assertEqual(board_gen.warnings,
                         ["FTM6 is used and its clock is in the generated bsp/clock/peripheralClocks.h, but the "
                          "s32k1xx clock driver of OpenBSW (clockConfig.cpp enablePeripheralClocks) does not "
                          "write PCC_FTM6: the clock stays off until the board port calls "
                          "nxEnablePeripheralClocks() (ARCH-18)"])
        pcc = (gen / "bsp/clock/peripheralClocks.h").read_text(encoding="utf-8")
        self.assertIn("#define NX_PCC_FTM6 ((1UL << 24) | NX_PCC_CGC)", pcc)
        self.assertIn("    PCC->PCCn[PCC_FTM6_INDEX] = NX_PCC_FTM6;", pcc)
        # boardClock.h has no CLOCK_SOURCE_FTM6: the OpenBSW driver does not know the gate
        clock = (gen / "bsp/clock/boardClock.h").read_text(encoding="utf-8")
        self.assertNotIn("CLOCK_SOURCE_FTM6", clock)
        self.assertIn("#define NX_CLOCK_FTM6_HZ 8000000U // SOSCDIV1 (peripheral_clocks)", clock)
        self.target_compile_peripheral_clocks(gen)

    def test_pcc_host_test_rejects_hand_edits(self):
        """Built-in errors: the host test must see a wrong clock select, a missing write and a wrong gate."""
        bswgen.generate(PROJECT_FILE, self.tmp / "gen")
        text = (self.tmp / "gen/bsp/clock/peripheralClocks.h").read_text(encoding="utf-8")
        checks = """
    expect("FTM0", pccModel.PCCn[PCC_FTM0_INDEX], (1u << 24) | 0x40000000u);
    expect("ADC0", pccModel.PCCn[PCC_ADC0_INDEX], (3u << 24) | 0x40000000u);
    expect("LPIT", pccModel.PCCn[PCC_LPIT_INDEX], (2u << 24) | 0x40000000u);
    expect("PORTD", pccModel.PCCn[PCC_PORTD_INDEX], 0x40000000u);
"""
        cases = [
            # wrong clock select of a used instance (SOSC instead of FIRC for the ADC)
            ("#define NX_PCC_ADC0 ((3UL << 24) | NX_PCC_CGC)", "#define NX_PCC_ADC0 ((1UL << 24) | NX_PCC_CGC)",
             "ADC0"),
            # clock gate forgotten
            ("    PCC->PCCn[PCC_PORTD_INDEX] = NX_PCC_PORTD;\n", "", "PORTD"),
            # clock switched on without selecting the clock first (PCS is only writable while CGC is 0)
            ("#define NX_PCC_LPIT ((2UL << 24) | NX_PCC_CGC)", "#define NX_PCC_LPIT ((5UL << 24) | NX_PCC_CGC)",
             "LPIT"),
        ]
        for old, new, name in cases:
            with self.subTest(name=name):
                self.assertIn(old, text)
                patched = self.tmp / "patched"
                shutil.rmtree(patched, ignore_errors=True)
                (patched / "bsp/clock").mkdir(parents=True)
                (patched / "bsp/clock/peripheralClocks.h").write_text(text.replace(old, new), encoding="utf-8")
                result = self.run_peripheral_clocks(self.tmp / "gen", checks, extra_include=patched)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn(name, result.stdout)

    @on_eval_board
    def test_static_asserts_reject_hand_edits(self):
        bswgen.generate(PROJECT_FILE, self.tmp / "gen")
        can = (self.tmp / "gen/bsp/can/canBitTimingConfiguration.h").read_text(encoding="utf-8")
        clock = (self.tmp / "gen/bsp/clock/boardClock.h").read_text(encoding="utf-8")
        cases = [
            ("bsp/can/canBitTimingConfiguration.h", can, "    5U, // prescaler", "    4U, // prescaler", "CAN0: bit rate"),
            ("bsp/can/canBitTimingConfiguration.h", can, "    3U, // phaseSeg2", "    1U, // phaseSeg2",
             "CAN0: invalid bit timing"),
            ("bsp/can/canBitTimingConfiguration.h", can, "CAN0_CTRL1_TIMING         = 0x04620006U",
             "CAN0_CTRL1_TIMING         = 0x04620007U", "CAN0: CTRL1"),
            ("bsp/clock/boardClock.h", clock, "(16UL << 16)", "(15UL << 16)", "SPLLCFG PREDIV 4, MULT 32"),
            ("bsp/clock/boardClock.h", clock, "#define NX_CLOCK_LPSPI1_HZ 48000000U", "#define NX_CLOCK_LPSPI1_HZ 40000000U",
             "LPSPI1 functional clock"),
            # ARCH-22: the SPLL outputs undivided again (the configuration before ARCH-22)
            ("bsp/clock/boardClock.h", clock, "#define _SPLLDIV2 (3UL)", "#define _SPLLDIV2 (1UL)",
             "SPLLDIV1 / 2 = 80 MHz, SPLLDIV2 / 4 = 40 MHz"),
        ]
        for rel, text, old, new, message in cases:
            with self.subTest(message=message):
                self.assertIn(old, text)
                patched = self.tmp / "patched"
                shutil.rmtree(patched, ignore_errors=True)
                (patched / rel).parent.mkdir(parents=True)
                (patched / rel).write_text(text.replace(old, new), encoding="utf-8")
                r = self.compile(self.tmp / "gen", self.EVAL_EXPECTATIONS, extra_include=patched)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn(message, r.stderr)


class DigitalIoHostTestBase(unittest.TestCase):
    """Generated digital input/output configuration with the OpenBSW driver (DRIVER) on the host (ARCH-13, PLAT-94).

    Host compiler zig c++ (clang); the target compiler arm-none-eabi-g++ additionally compiles the OpenBSW source with
    the warning flags of the target build when it is found (NX_ARM_GCC_BIN or PATH), GCC warns other cases than clang.
    PLAT-94: that is a real compile at -O2, not -fsyntax-only - -Warray-bounds comes from the optimiser, and the
    syntax check had passed the empty input table that broke the target build of a project without digital input.
    """

    OPENBSW = REPO / "external" / "openbsw"
    NO_INPUT_PROJECT = REPO / "tools/nxconfig/test/profiles/projects/testproject_s32k_no_inputs/project.yaml"
    S32K_PROJECT = REPO / "tools/nxconfig/test/profiles/projects/testproject_s32k_8mhz/project.yaml"
    # PLAT-94: own profile without any I/O channel (no input, output, ADC, PWM, capture)
    NO_IO_PROJECT = HERE / "test/profiles/projects/testproject_no_io/project.yaml"
    HOST_FLAGS: tuple = ()

    IO_STUB = """// Host stub of io/Io.h (s32k1xx bspIo): generated pin enum, all pins read high
#pragma once

#include "bsp/Bsp.h"

#include <cstdint>

namespace bios
{
class Io
{
public:
#include "bsp/io/io/ioConfiguration.h"

    enum Level
    {
        LOW,
        HIGH
    };

    static bool const LOW_ACTIVE  = true;
    static bool const HIGH_ACTIVE = false;
    static uint16_t const NUMBER_OF_IOS = NUMBER_OF_INPUTS_AND_OUTPUTS;

    static bool getPin(uint16_t) { return true; }
    static bsp::BspReturnCode setDefaultConfiguration(uint16_t io);
    static bsp::BspReturnCode setPin(uint16_t io, bool vol);
    static bsp::BspReturnCode resetConfig(uint16_t io);
};
} // namespace bios
"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "stubs/io").mkdir(parents=True)
        (self.tmp / "stubs/io/Io.h").write_text(self.IO_STUB, encoding="utf-8")
        (self.tmp / self.SOURCE_NAME).write_text(self.TEST_SOURCE, encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def includes(self, gen: Path):
        return [self.tmp, self.tmp / "stubs", gen] + [self.OPENBSW / i for i in (
            "libs/bsp/bspInputManager/include", "libs/bsp/bspOutputManager/include",
            "libs/bsp/bspDynamicClient/include", "libs/bsp/bspInterrupts/include",
            "libs/bsp/bspInterrupts/mock/include", "libs/bsw/bsp/include")]

    def system_includes(self):
        return [REPO / "ecu/etl_profile", self.OPENBSW / "libs/3rdparty/etl/include"]

    def run_host(self, project: Path, expectations: str):
        gen = self.tmp / "gen"
        bswgen.generate(project, gen)
        (self.tmp / "expectations.inc").write_text(expectations, encoding="utf-8")
        exe = self.tmp / "driver_host_test.exe"
        # HOST_FLAGS: clang-only warnings about the unchanged OpenBSW source; the target compile below keeps all flags
        cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
               *self.HOST_FLAGS,
               *[f"-I{i}" for i in self.includes(gen)], *[f"-isystem{i}" for i in self.system_includes()],
               str(self.tmp / self.SOURCE_NAME), str(self.OPENBSW / self.DRIVER), "-o", str(exe)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        r = subprocess.run([str(exe)], capture_output=True, text=True)
        print("\n" + r.stdout.strip(), file=sys.stderr)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:])
        self.assertRegex(r.stdout, r"\d+ checks, 0 failures")
        self.target_compile_check(gen)
        return gen

    def target_compile_check(self, gen: Path):
        """The OpenBSW driver with the generated header, compiled (not only parsed) with the warning flags of the
        target build (CMakePresets.json): -Warray-bounds and other warnings of the optimiser only show up here."""
        name = "arm-none-eabi-g++.exe" if sys.platform == "win32" else "arm-none-eabi-g++"
        env_bin = os.environ.get("NX_ARM_GCC_BIN")
        gxx = Path(env_bin) / name if env_bin and (Path(env_bin) / name).exists() else shutil.which(name)
        if not gxx:
            print("\narm-none-eabi-g++ not found: target compiler check skipped", file=sys.stderr)
            return
        cmd = [str(gxx), "-std=c++17", "-mcpu=cortex-m4", "-mthumb", "-O2", "-fshort-enums", "-Wall", "-Wextra",
               "-Wvla", "-Werror", "-c", "-o", str(self.tmp / "driver_target.o"),
               *[f"-I{i}" for i in self.includes(gen)], *[f"-isystem{i}" for i in self.system_includes()],
               str(self.OPENBSW / self.DRIVER)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        self.assertNotIn("warning:", r.stderr)


class BswGenDigitalInputHostTest(DigitalIoHostTestBase):
    """Generated inputConfiguration.h with OpenBSW DigitalInput.cpp, with and without digital inputs (ARCH-13,
    PLAT-94)."""

    DRIVER = "libs/bsp/bspInputManager/src/inputManager/DigitalInput.cpp"
    SOURCE_NAME = "input_host_test.cpp"
    TEST_SOURCE = """#include "inputManager/DigitalInput.h"

#include <cstdio>
#include <string_view>

namespace
{
int configured = 0;
int checks     = 0;
int failures   = 0;
void check(bool const ok, char const* const what)
{
    ++checks;
    if (!ok)
    {
        std::printf("FAIL: %s\\n", what);
        ++failures;
    }
}
} // namespace

namespace bios
{
bsp::BspReturnCode Io::setDefaultConfiguration(uint16_t const io)
{
    configured += (io < Io::NUMBER_OF_IOS) ? 1 : 0;
    return bsp::BSP_OK;
}
} // namespace bios

extern "C"
{
uint32_t getMachineStateRegisterValueAndSuspendAllInterrupts() { return 0U; }

void resumeAllInterrupts(uint32_t) {}
}

int main()
{
    using bios::DigitalInput;
    DigitalInput input;
    input.init(0U);
    input.cyclic();
    bool level = false;
#include "expectations.inc"
    check(DigitalInput::get(DigitalInput::PORT_UNAVAILABLE, level) == bsp::BSP_ERROR, "no channel behind the last");
    std::printf("%u digital inputs, %d checks, %d failures\\n",
                static_cast<unsigned>(DigitalInput::TOTAL_NUMBER_OF_DIGITAL_INPUTS), checks, failures);
    return failures;
}
"""

    def test_project_without_digital_input(self):
        gen = self.run_host(self.NO_INPUT_PROJECT, """
    check(DigitalInput::TOTAL_NUMBER_OF_DIGITAL_INPUTS == 0U, "no digital input");
    check(DigitalInput::NUMBER_OF_INTERNAL_DIGITAL_INPUTS == 0U, "no internal digital input");
    check(DigitalInput::NUMBER_OF_EXTERNAL_DIGITAL_INPUTS == 0U, "no external digital input");
    check(configured == 0, "no pin configured");
    check(DigitalInput::setDynamicClient(0U, 0U, nullptr) == bsp::BSP_ERROR, "no dynamic input");
""")
        text = (gen / "bsp/io/input/inputConfiguration.h").read_text(encoding="utf-8")
        self.assertIn("LAST_INTERNAL_DIGITAL_INPUT = -1,", text)
        self.assertIn('"lastStatic"', (gen / "bsp/io/input/inputConfigurationStrings.h").read_text(encoding="utf-8"))
        # PLAT-94: no element of the empty row is indexed
        self.assertNotIn("return &sfDigitalInputConfigurations[0][0];", text)
        self.assertIn("return sfDigitalInputConfigurations[0];", text)

    def test_project_without_any_io(self):
        """PLAT-94: own profile without any I/O channel - the input part."""
        self.run_host(self.NO_IO_PROJECT, """
    check(DigitalInput::TOTAL_NUMBER_OF_DIGITAL_INPUTS == 0U, "no digital input");
    check(configured == 0, "no pin configured");
    check(DigitalInput::get(DigitalInput::PORT_UNAVAILABLE, level) == bsp::BSP_ERROR, "nothing to read");
    check(std::string_view(DigitalInput::getName(0U)) == "lastStatic", "only the end marker is named");
""")

    def test_project_with_digital_input(self):
        # KEY on PTE8, active low: the stub pin level high reads as inactive
        gen = self.run_host(self.S32K_PROJECT, """
    check(DigitalInput::TOTAL_NUMBER_OF_DIGITAL_INPUTS == 1U, "one digital input");
    check(configured == 1, "KEY configured");
    check(DigitalInput::get(DigitalInput::KEY, level) == bsp::BSP_OK && !level, "KEY active low");
    check(std::string_view(DigitalInput::getName(DigitalInput::KEY)) == "KEY", "name");
""")
        # with an input the table is indexed as before (the demo image stays byte-identical, PLAT-94)
        self.assertIn("return &sfDigitalInputConfigurations[0][0];",
                      (gen / "bsp/io/input/inputConfiguration.h").read_text(encoding="utf-8"))


class BswGenDigitalOutputHostTest(DigitalIoHostTestBase):
    """PLAT-94: generated outputConfiguration.h with OpenBSW Output.cpp on the host and with the target compiler, with
    and without digital outputs - the same empty-row case as the inputs (sfOutputConfigurations[1][0])."""

    DRIVER = "libs/bsp/bspOutputManager/src/outputManager/Output.cpp"
    SOURCE_NAME = "output_host_test.cpp"
    # Output.cpp tests the dynamic range with (chan != NUMBER_OF_INTERNAL_OUTPUTS) && (chan < TOTAL_NUMBER_OF_OUTPUTS);
    # with 0 outputs clang sees 0 != chan < 1 as always false. GCC (target) has no such warning in -Wall -Wextra.
    HOST_FLAGS = ("-Wno-tautological-overlap-compare",)
    TEST_SOURCE = """#include "outputManager/Output.h"

#include <cstdio>
#include <string_view>

namespace
{
int configured = 0;
int written    = 0;
int checks     = 0;
int failures   = 0;
void check(bool const ok, char const* const what)
{
    ++checks;
    if (!ok)
    {
        std::printf("FAIL: %s\\n", what);
        ++failures;
    }
}
} // namespace

namespace bios
{
bsp::BspReturnCode Io::setDefaultConfiguration(uint16_t const io)
{
    configured += (io < Io::NUMBER_OF_IOS) ? 1 : 0;
    return bsp::BSP_OK;
}

bsp::BspReturnCode Io::setPin(uint16_t const io, bool)
{
    written += (io < Io::NUMBER_OF_IOS) ? 1 : 0;
    return bsp::BSP_OK;
}

bsp::BspReturnCode Io::resetConfig(uint16_t) { return bsp::BSP_OK; }
} // namespace bios

extern "C"
{
uint32_t getMachineStateRegisterValueAndSuspendAllInterrupts() { return 0U; }

void resumeAllInterrupts(uint32_t) {}
}

int main()
{
    using bios::Output;
    Output output;
    output.init(0U);
    bool level = false;
#include "expectations.inc"
    check(Output::get(Output::PORT_UNAVAILABLE, level) == bsp::BSP_ERROR, "no channel behind the last");
    std::printf("%u digital outputs, %d checks, %d failures\\n",
                static_cast<unsigned>(Output::NUMBER_OF_INTERNAL_OUTPUTS), checks, failures);
    return failures;
}
"""

    def test_project_without_any_io(self):
        gen = self.run_host(self.NO_IO_PROJECT, """
    check(Output::NUMBER_OF_INTERNAL_OUTPUTS == 0U, "no digital output");
    check(configured == 0 && written == 0, "no pin configured or written");
    check(Output::set(Output::NUMBER_OF_INTERNAL_OUTPUTS, 1U) == bsp::BSP_ERROR, "nothing to set");
    check(Output::setDynamicClient(0U, 0U, nullptr) == bsp::BSP_ERROR, "no dynamic output");
    check(std::string_view(Output::getName(0U)) == "lastStatic", "only the end marker is named");
""")
        text = (gen / "bsp/io/output/outputConfiguration.h").read_text(encoding="utf-8")
        self.assertNotIn("return &sfOutputConfigurations[0][0];", text)
        self.assertIn("return sfOutputConfigurations[0];", text)

    def test_project_with_digital_output(self):
        # LED on PTD16, active high, default low
        gen = self.run_host(self.NO_INPUT_PROJECT, """
    check(Output::NUMBER_OF_INTERNAL_OUTPUTS == 1U, "one digital output");
    check(configured == 1 && written == 1, "LED configured and set to its default");
    check(Output::set(Output::LED, 1U) == bsp::BSP_OK, "LED set");
    check(Output::get(Output::LED, level) == bsp::BSP_OK && level, "LED reads the stub level high");
    check(std::string_view(Output::getName(Output::LED)) == "LED", "name");
""")
        self.assertIn("return &sfOutputConfigurations[0][0];",
                      (gen / "bsp/io/output/outputConfiguration.h").read_text(encoding="utf-8"))


NXCONFIG_TEST = REPO / "tools" / "nxconfig" / "test"
SWITCH_PROJECT = NXCONFIG_TEST / "profiles" / "projects" / "testproject_switches" / "project.yaml"
LIBS_BSW = REPO / "libs" / "bsw"


class ModuleIntegrationTest(unittest.TestCase):
    """Module integration rendered by bswgen (modules.py); the checks themselves run in nxconfig (ARCH-12).

    Uses copies of the eval board demo, manifests written by the tests and the synthetic nxconfig profiles (no SBC,
    two CAN buses, two high-side switches, other family); the real manifests in libs/bsw are checked for
    consistency. Detailed checks of connections and parameters: tools/nxconfig/test_nxconfig.py (ModuleCheckTest).
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, board, project) -> Path:
        board_dir, project_dir = self.tmp / "boards" / "b", self.tmp / "projects" / "p"
        board_dir.mkdir(parents=True, exist_ok=True)
        project_dir.mkdir(parents=True, exist_ok=True)
        (board_dir / "board.yaml").write_text(yaml.safe_dump(board, sort_keys=False), encoding="utf-8")
        project = copy.deepcopy(project)
        project["project"]["board"] = "../../boards/b"
        without_rte(project)
        path = project_dir / "project.yaml"
        path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        return path

    def module_dirs(self, **extra):
        """Real manifests plus test manifests {name: yaml text} in an extra directory."""
        base = self.tmp / "modules"
        for name, text in extra.items():
            (base / name).mkdir(parents=True, exist_ok=True)
            (base / name / "module.yaml").write_text(text, encoding="utf-8")
        return (*modules.MODULE_DIRS, base)

    def demo(self, board=None, project=None, module_dirs=modules.MODULE_DIRS):
        """nxconfig.check of a demo copy: (integration or None, all messages)."""
        path = self.write(board or copy.deepcopy(BOARD), project or copy.deepcopy(PROJECT))
        config, messages = modules.nxconfig.check(path, modules.nxconfig.MCUDB, module_dirs)
        return (config.integration if config else None), messages

    @staticmethod
    def errors(messages):
        return [m for m in messages if m.severity == "error"]

    # --- manifests ---------------------------------------------------------------------------------

    def test_repository_manifests(self):
        module_set = modules.load_manifests()
        self.assertEqual(module_set.messages, [])
        self.assertLessEqual({"sbcUja113x", "highSideSwitch", "canPhyPassive", "canSystem"},
                             set(module_set.manifests))
        valid, messages = modules.nxchecks._manifests(module_set)
        self.assertEqual(messages, [])
        for name, manifest in module_set.manifests.items():
            with self.subTest(module=name):
                # Manifests live in libs/bsw, libs/bsp and board ports (module_set.files knows where)
                cmake = (module_set.files[name].parent / "CMakeLists.txt").read_text(encoding="utf-8")
                self.assertIn(f"add_library({manifest.cmake_target}", cmake)
                if manifest.kind == "device_driver":
                    self.assertTrue(manifest.parts)
        self.assertIn("can_phy", module_set.manifests["sbcUja113x"].provides)
        self.assertIn("early_init", module_set.manifests["sbcUja113x"].provides)
        self.assertIn("can_phy", module_set.manifests["canPhyPassive"].provides)
        hss = module_set.manifests["highSideSwitch"]
        self.assertEqual({k: c.kind for k, c in hss.connect.items()},
                         {"in": "digital_output", "den": "digital_output", "is": "analog_input"})

    def test_high_side_switch_parameters_match_the_driver(self):
        # Every parameter except console is a field of HighSideSwitchConfig and used by HighSideSwitchIntegration.h,
        # every constraint of module.yaml is also a rule of isValidHighSideSwitchConfig (and the other way round).
        hss = modules.load_manifests().manifests["highSideSwitch"]
        integration_h = (LIBS_BSW / "highSideSwitch/include/highSideSwitch/HighSideSwitchIntegration.h").read_text(
            encoding="utf-8")
        driver_h = (LIBS_BSW / "highSideSwitch/include/highSideSwitch/HighSideSwitch.h").read_text(encoding="utf-8")
        camel = {p: re.sub(r"_([a-z])", lambda m: m.group(1).upper(), p) for p in hss.parameters}
        used = re.findall(r"NX_DEVICE_##device##_PARAM_(\w+)", integration_h)
        self.assertEqual(sorted(p.lower() for p in used), sorted(hss.parameters))
        struct = re.search(r"struct HighSideSwitchConfig\s*\{(.*?)\};", driver_h, re.S).group(1)
        fields = re.findall(r"uint32_t (\w+);", struct)
        self.assertEqual([camel[p] for p in hss.parameters if p != "console"], fields)
        rules = {tuple(c.split()) for c in hss.constraints}
        for left, op, right in rules:
            self.assertIn(f"c.{camel[left]} {op} c.{camel[right]}", driver_h)
        compared = re.findall(r"\(c\.(\w+) ([<>=!]=?) c\.(\w+)\)", driver_h)
        back = {v: k for k, v in camel.items()}
        self.assertEqual({(back[a], op, back[b]) for a, op, b in compared}, rules)
        for a, op, value in re.findall(r"\(c\.(\w+) ([<>]=?) (\d+)U\)", driver_h):
            spec = hss.parameters[back[a]]
            self.assertEqual({">": spec.min - 1, "<=": spec.max}[op], int(value), f"{a} {op} {value}")

    def test_manifest_errors_have_file_path_and_line(self):
        module_set = modules.load_manifests(self.module_dirs(
            badKey="module: badKey\nkind: device_driver\nparts: [X1]\nfoo: 1\n",
            wrongDir="module: otherName\nkind: service\n",
            badKind="module: badKind\n\nkind: gadget\n",
            sbcUja113x="module: sbcUja113x\nkind: service\n",
        ))
        by_file = {m.file.parent.name: m for m in module_set.messages}
        self.assertEqual(by_file["badKey"].yaml_path, "foo")
        self.assertEqual(by_file["badKey"].line, 4)
        self.assertIn("Extra inputs are not permitted", by_file["badKey"].text)
        self.assertEqual(by_file["wrongDir"].yaml_path, "module")
        self.assertIn("directory of the same name", by_file["wrongDir"].text)
        self.assertEqual((by_file["badKind"].yaml_path, by_file["badKind"].line), ("kind", 3))
        self.assertIn("also described in", by_file["sbcUja113x"].text)
        self.assertNotIn("badKey", module_set.manifests)

    # --- eval board demo ---------------------------------------------------------------------------

    @on_eval_board
    def test_demo_project(self):
        integration, messages = self.demo()
        self.assertEqual(findings(messages), EVAL_OPEN_POWER_STAGE)
        module_set = modules.load_manifests()
        devices = {d.name: (d.module, d.spi, d.can_phy) for d in integration.devices}
        # COM-05: the diag transceiver DIAG_PHY belongs to CAN1, which the demo project uses (use.can)
        self.assertEqual(devices, {
            "SBC": ("sbcUja113x", ["SBC_SPI"], ["CAN0"]),
            "MOT1_GD": ("gdTmc6200", ["MOT1_GD_SPI"], []),             # IO-25: active gate driver
            "KL30B_SW": ("highSideSwitch", [], []),
            "DIAG_PHY": ("canPhyPassive", [], ["CAN1"]),
        })
        kl30b = integration.instances("highSideSwitch")[0]
        self.assertEqual(kl30b.connect, {"in": "EN_SW_KL30B", "den": "DIAG_EN_SW_KL30B", "is": "CURRENTSENSE_KL30B"})
        self.assertTrue(kl30b.verify)
        self.assertEqual([(b.name, b.index, b.phy, b.module) for b in integration.can],
                         [("CAN0", 0, "SBC", "sbcUja113x"), ("CAN1", 1, "DIAG_PHY", "canPhyPassive")])
        header = modules.render_header(integration, module_set, "demo")
        for line in ["#define NX_MODULE_SBCUJA113X_COUNT 1", "#define NX_MODULE_HIGHSIDESWITCH_COUNT 1",
                     "#define NX_DEVICE_SBC_MODULE_SBCUJA113X 1", "#define NX_DEVICE_KL30B_SW_MODULE_HIGHSIDESWITCH 1",
                     "#define NX_CAN_BUS_COUNT 2", "#define NX_CAN_CAN0_INDEX 0",
                     "#define NX_CAN_CAN0_PHY_MODULE_SBCUJA113X 1",
                     # ARCH-07: buses per role provider, the integration wires one list per module it knows
                     "#define NX_CAN_PHY_BUSES_SBCUJA113X(X) X(CAN0, 0)",
                     "#define NX_CAN_PHY_COUNT_SBCUJA113X 1",
                     "#define NX_CAN_PHY_BUSES_CANPHYPASSIVE(X) X(CAN1, 1)",
                     "#define NX_CAN_PHY_COUNT_CANPHYPASSIVE 1",
                     "#define NX_USE_SPI_SBC_SPI 1", "#define NX_USE_DIO_EN_SW_KL30B 1",
                     "#define NX_USE_DIO_DIAG_EN_SW_KL30B 1", "#define NX_USE_ADC_CURRENTSENSE_KL30B 1",
                     "#define NX_USE_ADC_12VBAT_CURRENT 1"]:
            self.assertIn(line + "\n", header)
        self.assertNotIn("NX_MODULE_CANSYSTEM_COUNT", header)          # services have no device instances
        cmake = modules.render_cmake(integration, module_set, "demo")
        self.assertIn("set(NX_MODULES canPhyPassive gdTmc6200 highSideSwitch sbcUja113x)", cmake)      # COM-05: CAN1
        self.assertIn("set(NX_MODULE_TARGETS canPhyPassive gdTmc6200 highSideSwitchSystem sbcUja113x)", cmake)
        # PLAT-25: the used signals by kind, the CMake side of NX_USE_<KIND>_<NAME>; the family SPI driver
        # (bspLpspi) hangs on "SPI used at all", not on the SBC module
        self.assertIn("set(NX_USE_SPI SBC_SPI MOT1_GD_SPI CSC_SPI)", cmake)
        self.assertRegex(cmake, r"set\(NX_USE_ADC [A-Z0-9_ ]*CURRENTSENSE_KL30B")
        self.assertRegex(cmake, r"set\(NX_USE_DIO [A-Z0-9_ ]*EN_SW_KL30B")

    def test_two_buses_of_the_eval_board(self):
        """ARCH-07: with both buses in use.can the diag transceiver becomes a role provider of its own."""
        project = copy.deepcopy(PROJECT)
        project["use"]["can"] = "all"                     # the demo carries the rank of CAN1 (COM-05)
        integration, messages = self.demo(project=project)
        self.assertEqual(findings(messages), EVAL_OPEN_POWER_STAGE)
        self.assertEqual([(b.name, b.index, b.phy, b.module) for b in integration.can],
                         [("CAN0", 0, "SBC", "sbcUja113x"), ("CAN1", 1, "DIAG_PHY", "canPhyPassive")])
        header = modules.render_header(integration, modules.load_manifests(), "two")
        for line in ["#define NX_CAN_BUS_COUNT 2", "#define NX_CAN_CAN1_INDEX 1",
                     "#define NX_CAN_CAN1_PHY_MODULE_CANPHYPASSIVE 1",
                     "#define NX_CAN_PHY_BUSES_SBCUJA113X(X) X(CAN0, 0)",
                     "#define NX_CAN_PHY_BUSES_CANPHYPASSIVE(X) X(CAN1, 1)",
                     "#define NX_CAN_PHY_COUNT_CANPHYPASSIVE 1"]:
            self.assertIn(line + "\n", header)

    @on_eval_board
    def test_generated_files(self):
        out = self.tmp / "gen"
        bswgen.generate(PROJECT_FILE, out)
        header = (out / "integration" / "moduleConfiguration.h").read_text(encoding="utf-8")
        self.assertIn(f"from {PROJECT_FILE.relative_to(REPO).as_posix()}", header)
        self.assertIn("#define NX_CAN_CAN0_PHY_MODULE_SBCUJA113X 1", header)
        # KL30B: the values that were written by hand in Integration.cpp before ARCH-12 (board test IO-08/ARCH-05),
        # since IO-21 as load currents in mA that trip at the same ADC codes as the former 2000/3500/20/500 mV
        hss = (out / "integration" / "highSideSwitchConfiguration.h").read_text(encoding="utf-8")
        self.assertIn("#define NX_MODULE_HIGHSIDESWITCH_INSTANCES(X) X(KL30B_SW)\n", hss)
        expected = {"CYCLE_MS": "10U", "OVERCURRENT_MA": "3633U", "OVERCURRENT_DEBOUNCE_MS": "30U",
                    "FAULT_MA": "6359U", "FAULT_DEBOUNCE_MS": "20U", "SWITCH_ON_BLANKING_MS": "20U",
                    "OPEN_LOAD_MA": "36U", "OFF_MAX_MA": "909U", "OFF_DEBOUNCE_MS": "50U", "CONSOLE": '"kl30b"'}
        for name, value in expected.items():
            self.assertRegex(hss, rf"#define NX_DEVICE_KL30B_SW_PARAM_{name} {re.escape(value)}( //.*)?\n")
        for line in ['#define NX_DEVICE_KL30B_SW_PART "BTS5030-1EJA"', '#define NX_DEVICE_KL30B_SW_LABEL "KL30B"',
                     "#define NX_DEVICE_KL30B_SW_IO_IN EN_SW_KL30B", "#define NX_DEVICE_KL30B_SW_IO_DEN DIAG_EN_SW_KL30B",
                     "#define NX_DEVICE_KL30B_SW_IO_IS CURRENTSENSE_KL30B"]:
            self.assertIn(line + "\n", hss)
        self.assertIn("verify: not confirmed", hss)
        cmake = (out / "integration" / "modules.cmake").read_text(encoding="utf-8")
        self.assertIn("sbcUja113x", cmake)
        # CMake parses the include file
        probe = self.tmp / "probe.cmake"
        probe.write_text(f'include("{(out / "integration" / "modules.cmake").as_posix()}")\n'
                         'if (NOT "highSideSwitch" IN_LIST NX_MODULES)\n  message(FATAL_ERROR "missing")\nendif ()\n'
                         # PLAT-25: the switch the board port uses for the family SPI driver
                         'if (NOT NX_USE_SPI)\n  message(FATAL_ERROR "NX_USE_SPI empty")\nendif ()\n'
                         'if (NOT "SBC_SPI" IN_LIST NX_USE_SPI)\n  message(FATAL_ERROR "SBC_SPI")\nendif ()\n',
                         encoding="utf-8")
        cmake_exe = shutil.which("cmake")
        if cmake_exe:
            r = subprocess.run([cmake_exe, "-P", str(probe)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    @on_eval_board
    def test_project_without_sbc(self):
        # The project leaves out the SBC's SPI bus, CAN0 and the KL30B switch: no SBC module, CAN1 becomes the
        # first bus, the high-side switch header has an empty instance list.
        project = copy.deepcopy(PROJECT)
        project["use"]["spi"] = ["MOT1_GD_SPI", "CSC_SPI"]
        project["use"]["can"] = ["CAN1"]
        with_rank_of(project, "CAN1")               # ARCH-33: every used CAN bus needs an interrupt rank
        project["use"]["dio"] = [o["name"] for o in BOARD["dio"]["outputs"] if "KL30B" not in o["name"]]
        del project["modules"]
        project["bootloader"]["can"] = "CAN1"       # CAN0 would need the SBC's bootloader hook (ARCH-08)
        integration, messages = self.demo(project=project)
        self.assertEqual(self.errors(messages), [])
        module_set = modules.load_manifests()
        self.assertEqual(integration.module_count("sbcUja113x"), 0)
        self.assertEqual([(b.name, b.index, b.module) for b in integration.can], [("CAN1", 0, "canPhyPassive")])
        header = modules.render_header(integration, module_set, "p")
        self.assertIn("#define NX_MODULE_SBCUJA113X_COUNT 0\n", header)
        self.assertIn("#define NX_MODULE_HIGHSIDESWITCH_COUNT 0\n", header)
        self.assertNotIn("NX_DEVICE_SBC_", header)
        self.assertNotIn("NX_USE_DIO_EN_SW_KL30B", header)
        self.assertNotIn("NX_CAN_CAN0", header)
        self.assertIn("#define NX_CAN_CAN1_INDEX 0\n", header)
        hss = modules.render_all(integration, module_set, "p")["integration/highSideSwitchConfiguration.h"]
        self.assertIn("#define NX_MODULE_HIGHSIDESWITCH_INSTANCES(X)\n", hss)
        self.assertNotIn("NX_DEVICE_", hss)
        # PLAT-25: SPI without SBC. The board uses two other SPI buses, so the family SPI driver is linked from
        # NX_USE_SPI, while NX_MODULES names no device module of the SBC (that was impossible before: the SPI
        # signals existed only as C macros).
        cmake = modules.render_cmake(integration, module_set, "p")
        self.assertIn("set(NX_USE_SPI MOT1_GD_SPI CSC_SPI)", cmake)
        self.assertNotIn("sbcUja113x", cmake)

    @on_eval_board
    def test_module_entry_of_a_bus_device(self):
        # SBC is used through its buses; a modules entry is allowed then, but an error if none of its buses is used.
        project = copy.deepcopy(PROJECT)
        project["modules"]["SBC"] = {}
        integration, messages = self.demo(project=project)
        self.assertEqual(findings(messages), EVAL_OPEN_POWER_STAGE)
        project["use"]["spi"] = ["MOT1_GD_SPI", "CSC_SPI"]
        project["use"]["can"] = ["CAN1"]
        with_rank_of(project, "CAN1")               # ARCH-33: every used CAN bus needs an interrupt rank
        project["modules"]["ecuState"]["wakeup_can"] = "all"     # the demo names CAN0 (COM-05)
        _, messages = self.demo(project=project)
        (error,) = self.errors(messages)
        self.assertEqual((error.file.name, error.yaml_path), ("project.yaml", "modules.SBC"))
        self.assertIn("device SBC is not used: the project uses none of its buses (SBC_SPI, CAN0)", error.text)

    def test_connection_to_a_board_entry_with_leading_digit(self):
        # NX_DEVICE_*_IO_* carries the C identifier of the generated board headers (Ai_SIG_12VBAT_CURRENT).
        board = copy.deepcopy(BOARD)
        board["devices"].append({"name": "AUX_SW", "part": "BTS5030-1EJA",
                                 "connect": {"in": "EN_VOLTAGEFILTER", "den": "EN_CURRENTFILTER",
                                             "is": "12VBAT_CURRENT"}})
        project = copy.deepcopy(PROJECT)
        # IO-21: levels within what 12VBAT_CURRENT reports (-2048..2048 mA); the KL30B levels would be out of range
        project["modules"]["AUX_SW"] = dict(project["modules"]["KL30B_SW"], console="aux", overcurrent_ma=1500,
                                            fault_ma=2000, open_load_ma=20, off_max_ma=500)
        integration, messages = self.demo(board=board, project=project)
        self.assertEqual(findings(messages), EVAL_OPEN_POWER_STAGE)
        hss = modules.render_all(integration, modules.load_manifests(), "p")["integration/highSideSwitchConfiguration.h"]
        self.assertIn("#define NX_MODULE_HIGHSIDESWITCH_INSTANCES(X) X(KL30B_SW) X(AUX_SW)\n", hss)
        self.assertIn("#define NX_DEVICE_AUX_SW_IO_IS SIG_12VBAT_CURRENT\n", hss)
        self.assertIn('#define NX_DEVICE_AUX_SW_LABEL "AUX"\n', hss)

    def test_kl30b_signals_without_module_entry(self):
        # Signals used, but no modules.KL30B_SW: the switch is not instantiated (outputs stay plain board outputs).
        project = copy.deepcopy(PROJECT)
        del project["modules"]
        integration, messages = self.demo(project=project)
        self.assertEqual(findings(messages), EVAL_OPEN_POWER_STAGE)
        self.assertEqual(integration.module_count("highSideSwitch"), 0)
        self.assertIn("NX_USE_DIO_EN_SW_KL30B", modules.render_header(integration, modules.load_manifests(), "p"))

    def test_transceiver_without_its_control_bus(self):
        project = copy.deepcopy(PROJECT)
        project["use"]["spi"] = ["MOT1_GD_SPI"]
        integration, messages = self.demo(project=project)
        self.assertIsNone(integration)
        (error,) = self.errors(messages)
        self.assertEqual(error.yaml_path, "spi[0].device")
        self.assertIn("CAN bus CAN0 uses device SBC as transceiver, but the project does not use its SPI bus SBC_SPI",
                      error.text)
        self.assertEqual(error.file.name, "board.yaml")
        self.assertIsNotNone(error.line)
        with self.assertRaises(bswgen.ConfigError) as ctx:
            bswgen.generate(self.tmp / "projects" / "p" / "project.yaml", self.tmp / "out")
        self.assertIn("board.yaml:", str(ctx.exception))

    @on_eval_board
    def test_other_transceiver_parts(self):
        board = copy.deepcopy(BOARD)
        board["devices"][0]["part"] = "TJA1051T/3"           # board variant with a passive transceiver on CAN0
        board["devices"][0].pop("pins", None)                # IO-33: HVIO/EN pins belong to the SBC part only
        board["devices"][0].pop("connect", None)             # IO-68: INTN1 is a connection of the SBC module only
        integration, messages = self.demo(board=board)
        self.assertEqual(findings(messages), EVAL_OPEN_POWER_STAGE)
        self.assertEqual(integration.can[0].module, "canPhyPassive")
        self.assertEqual(integration.module_count("sbcUja113x"), 0)
        project = copy.deepcopy(PROJECT)
        project["use"]["spi"] = ["CSC_SPI"]                  # a passive part needs no control bus
        integration, messages = self.demo(board=board, project=project)
        # SYS-95 (DFA-4 a): without its SPI bus the gate driver of MOT1 is not used, so no safe_off output is bound
        # to the group any more - the pwmgate warning is right here
        self.assertEqual(findings(messages), EVAL_OPEN_POWER_STAGE + [("warning", "pwm.MOT1")])
        self.assertEqual([(d.name, d.module, d.spi, d.can_phy) for d in integration.devices][0],
                         ("SBC", "canPhyPassive", [], ["CAN0"]))

        board["devices"][0]["part"] = "MCP2562"              # no module: passive with a warning on the bus
        integration, messages = self.demo(board=board)
        self.assertEqual(findings(messages), [("warning", "can[0].phy")] + EVAL_OPEN_POWER_STAGE)
        warning = messages[0]
        self.assertIn("no module for part MCP2562 of transceiver SBC", warning.text)
        self.assertEqual(integration.can[0].module, "canPhyPassive")
        self.assertEqual(integration.messages, [warning])

        del board["can"][0]["phy"]
        integration, messages = self.demo(board=board)
        self.assertIn("describes no transceiver", messages[0].text)

    @on_eval_board
    def test_role_and_contract_errors(self):
        dirs = self.module_dirs(
            gateDriver="module: gateDriver\nkind: device_driver\nparts: ['TMC6200*']\nrequires: { spi_device: 1 }\n",
            fakeSbc="module: fakeSbc\nkind: device_driver\nparts: ['SN65HVD1050*']\n",
        )
        board = copy.deepcopy(BOARD)
        _, messages = self.demo(board=board, module_dirs=dirs)
        texts = {m.yaml_path: m.text for m in self.errors(messages)}
        # SN65HVD1050D matches canPhyPassive and fakeSbc
        self.assertIn("served by several modules: canPhyPassive, fakeSbc", texts["devices[3].part"])
        board["devices"][3]["module"] = "canPhyPassive"      # the board chooses one: no error
        _, messages = self.demo(board=board, module_dirs=dirs)
        self.assertEqual(self.errors(messages), [])

        shutil.rmtree(self.tmp / "modules")
        dirs = self.module_dirs(
            gateDriver="module: gateDriver\nkind: device_driver\nparts: ['TMC6200*']\nrequires: { spi_device: 1 }\n",
            csc="module: csc\nkind: device_driver\nparts: ['MC33771*']\n",
            noPhy="module: noPhy\nkind: device_driver\nparts: ['UJA1131*']\n",
        )
        board = copy.deepcopy(BOARD)
        board["spi"] = [s for s in board["spi"] if s["device"] != "MOT1_GD"]
        project = copy.deepcopy(PROJECT)
        project["use"]["spi"] = "all"
        _, messages = self.demo(board=board, project=project, module_dirs=dirs)
        texts = {m.yaml_path: m.text for m in self.errors(messages)}
        # IO-25: the board names the module of MOT1_GD (gdTmc6200), the test manifest gateDriver does not apply
        self.assertIn("device MOT1_GD (gdTmc6200) needs an SPI bus", texts["devices[1]"])
        self.assertIn("served by several modules: noPhy, sbcUja113x", texts["devices[0].part"])

        only = self.tmp / "only" / "noPhy"                   # DIAG_PHY served by a module without can_phy
        only.mkdir(parents=True)
        (only / "module.yaml").write_text("module: noPhy\nkind: device_driver\nparts: ['SN65HVD1050*']\n",
                                          encoding="utf-8")
        project = copy.deepcopy(PROJECT)
        del project["modules"]
        _, messages = self.demo(project=project, module_dirs=(only.parent,))
        texts = {m.yaml_path: m.text for m in self.errors(messages)}
        self.assertIn("does not provide the role can_phy", texts["can[1].phy"])

    @on_eval_board
    def test_gate_driver_spi_and_pwm_wiring(self):
        """IO-25: SPI bus and PWM group of a gate driver instance, generated for the family port integration."""
        module_set = modules.load_manifests()
        self.assertIn("gate_driver", module_set.manifests["gdTmc6200"].provides)
        self.assertEqual(module_set.manifests["gdTmc6200"].parts, ["TMC6200-TA*"])

        def header(board=None, project=None):
            integration, messages = self.demo(board=board, project=project)
            self.assertEqual(self.errors(messages), [])
            return modules.render_all(integration, module_set, "p")["integration/gdTmc6200Configuration.h"]

        gd = header()
        for line in ["#define NX_MODULE_GDTMC6200_INSTANCES(X) X(MOT1_GD)", "#define NX_DEVICE_MOT1_GD_SPI_COUNT 1",
                     "#define NX_DEVICE_MOT1_GD_SPI_BUS MOT1_GD_SPI", "#define NX_DEVICE_MOT1_GD_SPI_INSTANCE LPSPI0",
                     "#define NX_DEVICE_MOT1_GD_SPI_PCS 0U", "#define NX_DEVICE_MOT1_GD_PWM_GROUP MOT1",
                     "#define NX_DEVICE_MOT1_GD_PWM_COMPLEMENTARY 1",
                     "#define NX_DEVICE_MOT1_GD_IO_DRV_EN MOT1_GD_DRV_EN",
                     "#define NX_DEVICE_MOT1_GD_PARAM_SHORT_RETRIES (-1)"]:
            self.assertIn(line + "\n", gd)

        # own profile: gate driver renamed, on another SPI instance (the bus of CSC), PWM group not used by the project
        board = copy.deepcopy(BOARD)
        dev = next(d for d in board["devices"] if d["name"] == "MOT1_GD")
        dev["name"] = "PUMP_GD"
        next(b for b in board["spi"] if b.get("device") == "MOT1_GD")["device"] = "CSC"
        next(b for b in board["spi"] if b["name"] == "CSC_SPI")["device"] = "PUMP_GD"
        board["pwm"][0]["gate_driver"] = "PUMP_GD"
        project = copy.deepcopy(PROJECT)
        del project["pwm"]
        project.pop("adc_sampling", None)  # averages over MOT1, which this test changes (IO-31)
        gd = header(board, project)
        self.assertIn("#define NX_MODULE_GDTMC6200_INSTANCES(X) X(PUMP_GD)\n", gd)
        self.assertIn("#define NX_DEVICE_PUMP_GD_SPI_BUS CSC_SPI\n", gd)
        self.assertIn("#define NX_DEVICE_PUMP_GD_SPI_INSTANCE LPSPI2\n", gd)
        self.assertIn("#define NX_DEVICE_PUMP_GD_PWM_COMPLEMENTARY 0\n", gd)   # group not used: reset interface mode
        self.assertNotIn("PWM_GROUP", gd)
        # a board without the link: same as an unused group
        board = copy.deepcopy(BOARD)
        del board["pwm"][0]["gate_driver"]
        gd = header(board)
        self.assertIn("#define NX_DEVICE_MOT1_GD_PWM_COMPLEMENTARY 0\n", gd)
        # highSideSwitch neither requires SPI nor provides gate_driver: its header has no such macros
        integration, _ = self.demo()
        hss = modules.render_all(integration, module_set, "p")["integration/highSideSwitchConfiguration.h"]
        self.assertNotIn("_SPI_", hss)
        self.assertNotIn("_PWM_", hss)

    def test_gate_driver_short_detection_parameters(self):
        """IO-35: short detection of the TMC6200 as project values in mV and ns, generated as parameter macros that
        the family integration converts to register fields; defaults 0 = device reset value (no SPI write)."""
        module_set = modules.load_manifests()

        def header(project):
            integration, messages = self.demo(project=project)
            self.assertEqual(self.errors(messages), [])
            return modules.render_all(integration, module_set, "p")["integration/gdTmc6200Configuration.h"]

        gd = header(copy.deepcopy(PROJECT))
        for line in ["#define NX_DEVICE_MOT1_GD_PARAM_SHORT_TO_GROUND_MV 0U // mV",
                     "#define NX_DEVICE_MOT1_GD_PARAM_SHORT_TO_SUPPLY_MV 0U // mV",
                     "#define NX_DEVICE_MOT1_GD_PARAM_SHORT_FILTER_NS 0U // ns",
                     "#define NX_DEVICE_MOT1_GD_PARAM_SHORT_DELAY_NS 0U // ns"]:
            self.assertIn(line + "\n", gd)
        project = copy.deepcopy(PROJECT)
        project.setdefault("modules", {}).setdefault("MOT1_GD", {}).update(
            short_to_ground_mv=1560, short_to_supply_mv=937, short_filter_ns=3000, short_delay_ns=1500)
        gd = header(project)
        for line in ["#define NX_DEVICE_MOT1_GD_PARAM_SHORT_TO_GROUND_MV 1560U // mV",
                     "#define NX_DEVICE_MOT1_GD_PARAM_SHORT_TO_SUPPLY_MV 937U // mV",
                     "#define NX_DEVICE_MOT1_GD_PARAM_SHORT_FILTER_NS 3000U // ns",
                     "#define NX_DEVICE_MOT1_GD_PARAM_SHORT_DELAY_NS 1500U // ns"]:
            self.assertIn(line + "\n", gd)
        # a filter time the device does not have is stopped by nxconfig with the YAML path, before any build
        project["modules"]["MOT1_GD"]["short_filter_ns"] = 1500
        _, messages = self.demo(project=project)
        texts = {m.yaml_path: m.text for m in self.errors(messages)}
        self.assertIn("1500 is not one of 0, 100, 1000, 2000, 3000 ns", texts["modules.MOT1_GD.short_filter_ns"])

    def test_gate_driver_link_errors(self):
        """IO-25: pwm[].gate_driver names a board device whose module provides the role gate_driver."""
        cases = [
            ("NO_SUCH_GD", "unknown device NO_SUCH_GD"),
            ("KL30B_SW", "device KL30B_SW (highSideSwitch) is no gate driver: module highSideSwitch does not provide "
                         "the role gate_driver"),
        ]
        for device, fragment in cases:
            with self.subTest(device=device):
                board = copy.deepcopy(BOARD)
                board["pwm"][0]["gate_driver"] = device
                _, messages = self.demo(board=board)
                texts = {m.yaml_path: m.text for m in self.errors(messages)}
                self.assertIn(fragment, texts.get("pwm[0].gate_driver", ""))
        board = copy.deepcopy(BOARD)
        second = copy.deepcopy(board["pwm"][0])
        second["name"] = "MOT2"
        second["instance"] = "FTM3"
        for ch in second["channels"]:
            ch["name"] = ch["name"].replace("MOT1", "MOT2")
        for pair in second["pairs"]:
            for key in ("name", "high_side", "low_side"):
                pair[key] = pair[key].replace("MOT1", "MOT2")
        board["pwm"].append(second)
        _, messages = self.demo(board=board)
        texts = [m.text for m in self.errors(messages) if m.yaml_path == "pwm[1].gate_driver"]
        self.assertEqual(texts, ["device MOT1_GD is already the gate driver of PWM group MOT1"])

    @on_eval_board
    def test_integrate_with_other_manifests(self):
        # modules.integrate() re-runs the nxconfig integration of a loaded project with another manifest set.
        config = modules.nxconfig.load(PROJECT_FILE)
        self.assertIs(config.integration.__class__, modules.Integration)
        integration = modules.integrate(config, modules.load_manifests((self.tmp / "none",)))
        self.assertEqual(integration.devices, [])
        self.assertEqual({b.module for b in integration.can}, {"canPhyPassive"})
        found = {(m.severity, m.file.name, m.yaml_path): m for m in integration.messages}
        self.assertIn("unknown module highSideSwitch", found["error", "board.yaml", "devices[4].module"].text)
        self.assertIsNotNone(found["warning", "board.yaml", "can[0].phy"].line)

    # --- synthetic profiles ------------------------------------------------------------------------

    def test_synthetic_profile_two_buses_no_sbc(self):
        project_file = NXCONFIG_TEST / "profiles" / "projects" / "testproject" / "project.yaml"
        config = modules.nxconfig.load(project_file, NXCONFIG_TEST / "mcudb")
        module_set = modules.load_manifests()
        integration = config.integration
        self.assertEqual(integration.messages, [])
        self.assertEqual([(b.name, b.index, b.phy, b.module) for b in integration.can],
                         [("VEH", 0, "PHY_A", "canPhyPassive"), ("DIAG", 1, "PHY_B", "canPhyPassive")])
        self.assertEqual([d.name for d in integration.devices], ["PHY_A", "PHY_B"])   # EEPROM: no module
        header = modules.render_header(integration, module_set, "testproject")
        self.assertIn("#define NX_MODULE_SBCUJA113X_COUNT 0\n", header)
        self.assertIn("#define NX_MODULE_CANPHYPASSIVE_COUNT 2\n", header)
        self.assertIn("#define NX_CAN_BUS_COUNT 2\n", header)
        self.assertIn("#define NX_CAN_DIAG_INDEX 1\n", header)
        self.assertIn("#define NX_USE_SPI_MEM_SPI 1\n", header)
        self.assertNotIn("NX_USE_ADC_TEMP_EXT", header)        # board channel not used by the project
        # PLAT-25: same information for CMake. The profile has an EEPROM on SPI and no SBC: a family SPI driver
        # is linked (NX_USE_SPI), no device module is (NX_MODULES lists only the passive transceivers).
        cmake = modules.render_cmake(integration, module_set, "testproject")
        self.assertIn("set(NX_MODULES canPhyPassive)", cmake)
        self.assertIn("set(NX_USE_SPI MEM_SPI)", cmake)

    def test_synthetic_profile_two_high_side_switches(self):
        config = modules.nxconfig.load(SWITCH_PROJECT, NXCONFIG_TEST / "mcudb")
        integration = config.integration
        self.assertEqual(integration.messages, [])
        files = modules.render_all(integration, config.modules, "testproject_switches")
        self.assertIn("#define NX_MODULE_HIGHSIDESWITCH_COUNT 2\n", files["integration/moduleConfiguration.h"])
        self.assertIn("set(NX_MODULES highSideSwitch)", files["integration/modules.cmake"])
        hss = files["integration/highSideSwitchConfiguration.h"]
        self.assertIn("#define NX_MODULE_HIGHSIDESWITCH_INSTANCES(X) X(LAMP_SW) X(PUMP_SW)\n", hss)
        self.assertIn("#define NX_DEVICE_LAMP_SW_PARAM_CYCLE_MS 10U", hss)             # module default
        self.assertIn('#define NX_DEVICE_PUMP_SW_PARAM_CONSOLE "pump_2"\n', hss)
        self.assertIn('#define NX_DEVICE_PUMP_SW_LABEL "PUMP_2"\n', hss)
        self.assertIn("#define NX_DEVICE_PUMP_SW_IO_IS PUMP_IS\n", hss)
        self.assertIn("// PUMP_SW: BTS5030-1EJA - parameter values marked verify", hss)
        self.assertNotIn("// LAMP_SW: BTS5030-1EJA - parameter values marked verify", hss)
        self.assertEqual(set(files), {"integration/moduleConfiguration.h", "integration/modules.cmake",
                                      "integration/highSideSwitchConfiguration.h",
                                      # IO-25: gdTmc6200 has connections and parameters, header also without instance
                                      "integration/gdTmc6200Configuration.h",
                                      # ARCH-40: sbcUja113x has an SPI bus, header also without instance
                                      "integration/sbcUja113xConfiguration.h",
                                      # module without a board device, always part of the build (IO-19)
                                      "integration/bspAdcAccessConfiguration.h",
                                      # ECU state management, project values since SYS-08 (code uses EcuStateConfig.h)
                                      "integration/ecuStateConfiguration.h",
                                      # ARCH-52: generated module binding, always (empty without a bound module)
                                      "integration/moduleBinding.h", "integration/moduleBindingConfiguration.h"})

    def test_module_header_values(self):
        # C spellings: signed ranges without U, bool 0/1, escaped strings, identifiers of board entries.
        spec = modules.nxconfig.model.ParameterSpec
        self.assertEqual(modules._c_value(spec(type="int", min=-5, max=5), -3), "(-3)")
        self.assertEqual(modules._c_value(spec(type="int", min=0, max=5), 3), "3U")
        self.assertEqual(modules._c_value(spec(type="bool"), True), "1")
        self.assertEqual(modules._c_value(spec(type="str"), 'a"b\\'), '"a\\"b\\\\"')
        self.assertEqual(modules.identifier("12VBAT_CURRENT"), "SIG_12VBAT_CURRENT")
        self.assertIs(bswgen.identifier, modules.identifier)


class CanBusTest(unittest.TestCase):
    """ARCH-07: bus ids, FlexCAN devices and interrupt vectors of every CAN bus the project uses.

    Profiles: the eval board demo (one bus on FlexCAN0 with the SBC as transceiver), the same board with both
    buses (second bus on FlexCAN2, passive transceiver) and the build-test profile S32K148 (two buses, no SBC,
    two different bit rates). The checks of the bus names belong to nxconfig (tools/nxconfig/can.py).
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    write = ModuleIntegrationTest.write

    def generate(self, board=None, project=None, out="gen"):
        path = self.write(board or copy.deepcopy(BOARD), project or copy.deepcopy(PROJECT))
        bswgen.generate(path, self.tmp / out)
        return self.read(out)

    def read(self, out="gen"):
        return ((self.tmp / out / "busid" / "busIdConfiguration.h").read_text(encoding="utf-8"),
                (self.tmp / out / "bsp" / "can" / "canDeviceConfiguration.h").read_text(encoding="utf-8"))

    def expect_error(self, fragment, board=None, project=None):
        with self.assertRaises(bswgen.ConfigError) as ctx:
            self.generate(board, project)
        self.assertIn(fragment, str(ctx.exception))

    @staticmethod
    def two_bus_project(rename: str | None = None):
        """Demo project with both CAN buses of the eval board; `rename` renames the second one."""
        project = copy.deepcopy(PROJECT)
        project["use"]["can"] = "all"
        name = rename or "CAN1"
        if rename is not None:
            project["can"][rename] = project["can"].pop("CAN1")
        # every used bus needs an interrupt rank (PLAT-21, checked by tools/nxconfig/can.py); the demo carries the
        # one of CAN1 since COM-05
        ranks = project["interrupts"]["ranks"]
        can1 = [r for r in ranks if r["source"] == "can" and r["name"] == "CAN1"]
        if can1:
            can1[0]["name"] = name
        else:
            ranks.append({"source": "can", "name": name, "rank": 3})
        return project

    def test_demo_project_one_bus(self):
        ids, dev = self.generate(project=one_bus_demo())     # COM-05: the demo itself uses CAN1 too
        self.assertIn("#define NX_BUSID_SELFDIAG 1\n", ids)
        self.assertIn("#define NX_BUSID_CAN_BUSES(X) \\\n    X(CAN0, 2, 0)\n", ids)
        self.assertIn("#define NX_BUSID_CAN_COUNT 1\n", ids)
        self.assertIn("#define NX_BUSID_ETH_0 3\n", ids)        # bus ids as before ARCH-07
        self.assertIn("#define NX_BUSID_ETH_1 4\n", ids)
        self.assertIn("#define NX_BUSID_LAST 4\n", ids)
        self.assertIn("#define NX_BUSID_ALIAS_CAN_0 CAN0\n", ids)
        self.assertIn('#define NX_CAN_BUSES(X) \\\n    X(CAN0, 0, 2, 0, "FlexCAN0")\n', dev)
        # PLAT-12: the vectors of the derivative (S32K14x: split receive/transmit message buffer interrupts)
        self.assertIn("#define NX_CAN_MB_VECTORS(X) \\\n"
                      "    X##_RX(CAN0, 0, CAN0_ORed_0_15_MB_IRQHandler, CAN0_ORed_0_15_MB_IRQn) \\\n"
                      "    X##_TX(CAN0, 0, CAN0_ORed_16_31_MB_IRQHandler, CAN0_ORed_16_31_MB_IRQn)\n", dev)
        self.assertIn("#define NX_CAN_DEVICE_COUNT 1\n", dev)
        # same values as the hand-written Can0Config before ARCH-07
        self.assertIn("constexpr ::bios::FlexCANDevice::Config CAN0_DEVICE_CONFIG = {\n"
                      "    IP_FLEXCAN0_BASE, // baseAddress\n"
                      "    CAN0_BITRATE, // baudrate\n", dev)
        self.assertIn("    ::bios::Io::CAN0_TX, // txPort\n    ::bios::Io::CAN0_RX, // rxPort\n", dev)
        self.assertIn("    14U, // numRxBufsStd", dev)
        self.assertIn("    2U, // numRxBufsExt", dev)
        self.assertIn("    15U, // numTxBufsApp", dev)
        self.assertIn("    0x00000100U, // wake-up source of this bus (bit 8", dev)
        self.assertIn("static_assert((CAN0_CTRL1_TIMING & FLEXCAN_CTRL1_CLKSRC_MASK) == 0U", dev)
        self.assertNotIn("CAN1", dev)

    def test_two_buses_of_the_eval_board(self):
        # ARCH-42: refused with FlexCANDevice (test_second_bus_of_the_eval_board_has_too_few_message_buffers); the
        # mechanics of a second bus (COM-26: FlexCAN2 with 16 buffers is served)
        ids, dev = self.generate(project=self.two_bus_project())
        self.assertIn("#define NX_BUSID_CAN_BUSES(X) \\\n    X(CAN0, 2, 0) \\\n    X(CAN1, 3, 1)\n", ids)
        self.assertIn("#define NX_BUSID_CAN1 3 // CAN1 on FlexCAN2, transceiver DIAG_PHY\n", ids)
        self.assertIn("#define NX_BUSID_ETH_0 4\n", ids)        # Ethernet follows the CAN buses
        self.assertIn("#define NX_BUSID_LAST 5\n", ids)
        # second bus: FlexCAN2 with its own vectors, bus id 3, wake-up bit 9
        self.assertIn('X(CAN1, 1, 3, 2, "FlexCAN2")\n', dev)
        # ARCH-42: FlexCAN2 of the S32K144 has 16 buffers and only the vector of buffers 0..15 (IRQ 96 does not
        # exist, although the S32K148 device header names CAN2_ORed_16_31_MB_IRQn); it serves receive and transmit
        self.assertIn("    X##_TX(CAN0, 0, CAN0_ORed_16_31_MB_IRQHandler, CAN0_ORed_16_31_MB_IRQn) \\\n"
                      "    X##_RX_TX(CAN1, 2, CAN2_ORed_0_15_MB_IRQHandler, CAN2_ORed_0_15_MB_IRQn)\n", dev)
        self.assertNotIn("CAN2_ORed_16_31", dev)
        self.assertIn("#define NX_CAN_DEVICE_COUNT 2\n", dev)
        self.assertIn("    IP_FLEXCAN2_BASE, // baseAddress", dev)
        self.assertIn("    ::bios::Io::CAN1_TX, // txPort", dev)
        self.assertIn("    0x00000200U, // wake-up source of this bus (bit 9", dev)
        self.assertIn("transceiver DIAG_PHY (module canPhyPassive), bus id 3 (::busid::CAN1)", dev)

    # ---------------------------------------------------------------- COM-37: behaviour after bus off per bus

    BUS_OFF_HEADER = REPO / "libs/bsw/canSystem/include/can/CanBusOffRecovery.h"
    BUS_OFF_KEYS = ("first_pause_ms", "max_pause_ms", "discard_after", "quiet_ms", "summary_ms")

    def bus_off(self, out="gen") -> str:
        return (self.tmp / out / "bsp" / "can" / "canBusOffConfiguration.h").read_text(encoding="utf-8")

    @staticmethod
    def bus_off_values(text: str, ident: str) -> tuple[int, ...]:
        """The five numbers of <ident>_BUS_OFF_CONFIG of a generated canBusOffConfiguration.h."""
        body = re.search(rf"Config {ident}_BUS_OFF_CONFIG = {{(.*?)\n}};", text, re.S)
        assert body is not None, f"{ident}_BUS_OFF_CONFIG missing"
        return tuple(int(v) for v in re.findall(r"^\s+(\d+)U,", body.group(1), re.M))

    def s32k148_project(self, **bus_off) -> Path:
        """Copy of the build-test profile S32K148 (two buses, no SBC, not the eval board) with bus_off per bus."""
        src = REPO / "projects" / "buildtest_s32k148_min" / "project.yaml"
        project = yaml.safe_load(src.read_text(encoding="utf-8"))
        project["project"]["board"] = (REPO / "boards" / "buildtest_s32k148").as_posix()
        project["project"]["rte"] = (src.parent / "rte.yaml").as_posix()
        for bus, values in bus_off.items():
            project["can"][bus]["bus_off"] = values
        path = self.tmp / "project_s32k148.yaml"
        path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        return path

    def test_bus_off_defaults_are_the_default_config_of_the_library(self):
        # the schema defaults (tools/nxconfig/model.py CanBusOff) and CanBusOffRecovery::DEFAULT_CONFIG are the same
        # numbers, and the demo without can.CAN0.bus_off gets exactly them
        header = self.BUS_OFF_HEADER.read_text(encoding="utf-8")
        match = re.search(r"DEFAULT_CONFIG = \{([^}]*)\};", header)
        self.assertIsNotNone(match)
        library = tuple(int(v.strip().rstrip("U")) for v in match.group(1).split(","))
        schema = nxconfig.model.CanBusOff()
        self.assertEqual(library, tuple(getattr(schema, k) for k in self.BUS_OFF_KEYS))
        self.generate()
        text = self.bus_off()
        self.assertEqual(self.bus_off_values(text, "CAN0"), library)
        self.assertIn("// CAN0: defaults (no can.CAN0.bus_off)", text)
        self.assertIn('#include "can/CanBusOffRecovery.h"', text)
        self.assertEqual(self.bus_off_values(text, "CAN1"), library)     # COM-05: diagnostic CAN, defaults too

    def test_bus_off_values_per_bus_on_the_s32k148_profile(self):
        bswgen.generate(self.s32k148_project(CAN1={"max_pause_ms": 0, "discard_after": 0, "quiet_ms": 500,
                                                   "summary_ms": 1000}), self.tmp / "gen148")
        text = self.bus_off("gen148")
        self.assertEqual(self.bus_off_values(text, "CAN0"), (10, 1000, 3, 2000, 10000))
        self.assertEqual(self.bus_off_values(text, "CAN1"), (10, 0, 0, 500, 1000))
        self.assertIn("// CAN1: project can.CAN1.bus_off", text)
        self.assertIn("0 = not held (automatic recovery)", text)
        self.assertIn("(0 = never)", text)

    def test_bus_off_configuration_compiles_with_the_library(self):
        """The generated header against the real CanBusOffRecovery.h: types fit, each bus has its own values."""
        bswgen.generate(self.s32k148_project(CAN1={"first_pause_ms": 25, "max_pause_ms": 400, "discard_after": 1,
                                                   "quiet_ms": 800, "summary_ms": 5000}), self.tmp / "gen148")
        source = self.tmp / "bus_off_config_test.cpp"
        source.write_text(
            '#include "bsp/can/canBusOffConfiguration.h"\n'
            "#include <cstdio>\n"
            "using ::can::CanBusOffRecovery;\n"
            "static_assert(::bsp::can::CAN0_BUS_OFF_CONFIG.maxPauseMs\n"
            "              == CanBusOffRecovery::DEFAULT_CONFIG.maxPauseMs, \"CAN0 defaults\");\n"
            "static_assert(::bsp::can::CAN1_BUS_OFF_CONFIG.firstPauseMs == 25U, \"CAN1 first pause\");\n"
            "static_assert(::bsp::can::CAN1_BUS_OFF_CONFIG.discardAfter == 1U, \"CAN1 discard\");\n"
            "int main() {\n"
            "    CanBusOffRecovery const can1(::bsp::can::CAN1_BUS_OFF_CONFIG);\n"
            "    CanBusOffRecovery const can0(::bsp::can::CAN0_BUS_OFF_CONFIG);\n"
            "    std::printf(\"%u %u %u\\n\", static_cast<unsigned>(can1.pauseFor(5U)),\n"
            "                static_cast<unsigned>(can0.pauseFor(5U)), can1.holdsRecovery() ? 1U : 0U);\n"
            "    return 0;\n"
            "}\n", encoding="utf-8")
        exe = self.tmp / "bus_off_config_test.exe"
        cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
               "-Wconversion", f"-I{self.tmp / 'gen148'}", f"-I{REPO / 'libs/bsw/canSystem/include'}", str(source),
               "-o", str(exe)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        r = subprocess.run([str(exe)], capture_output=True, text=True)
        self.assertEqual(r.stdout.split(), ["400", "160", "1"])   # 25, 50, 100, 200, 400 / 10 .. 160

    def test_bus_off_without_can_bus(self):
        # own profile without any I/O (and without CAN): the header exists and is empty
        bswgen.generate(HERE / "test/profiles/projects/testproject_no_io/project.yaml", self.tmp / "gen_no_io")
        text = self.bus_off("gen_no_io")
        self.assertIn("// the project uses no CAN bus", text)
        self.assertNotIn("_BUS_OFF_CONFIG =", text)

    def test_build_test_profile_s32k148(self):
        """Two buses with different bit rates on another derivative and crystal (ARCH-09 profile)."""
        bswgen.generate(REPO / "projects" / "buildtest_s32k148_min" / "project.yaml", self.tmp / "gen148")
        ids, dev = self.read("gen148")
        timing = (self.tmp / "gen148" / "bsp" / "can" / "canBitTimingConfiguration.h").read_text(encoding="utf-8")
        self.assertIn("#define NX_BUSID_CAN0 2 // CAN0 on FlexCAN0, transceiver CAN0_PHY\n", ids)
        self.assertIn("#define NX_BUSID_CAN1 3 // CAN1 on FlexCAN2, transceiver CAN1_PHY\n", ids)
        self.assertIn('X(CAN1, 1, 3, 2, "FlexCAN2"', dev)
        self.assertIn("transceiver CAN1_PHY (module canPhyPassive)", dev)
        # ARCH-17: the profile describes a board without a motor bridge and uses no PWM group
        pwm = (self.tmp / "gen148" / "bsp" / "io" / "outputPwm" / "ftmPwmConfiguration.h").read_text(encoding="utf-8")
        self.assertIn("#define NX_PWM_GROUP_COUNT 0\n", pwm)
        self.assertNotIn("#error", pwm)
        # bit timing per bus (ARCH-06) with the bit rates of the profile
        self.assertIn("constexpr uint32_t const CAN0_BITRATE              = 500000U;", timing)
        self.assertIn("constexpr uint32_t const CAN1_BITRATE              = 250000U;", timing)
        ctrl1 = [re.search(rf"{b}_CTRL1_TIMING         = (0x[0-9A-F]+)U", timing).group(1) for b in ("CAN0", "CAN1")]
        self.assertNotEqual(*ctrl1)

    def check(self, board, project):
        """Runs the CAN checks of nxconfig on a board/project copy and returns the messages."""
        config = bswgen.nxconfig.load(self.write(board, project))
        reporter = bswgen.Reporter(sources=dict(config.sources))
        nxcan.check_buses(config, reporter)
        return reporter.messages

    def test_bus_name_must_not_take_another_bus_id(self):
        # the name of a bus becomes its bus id (busid::<name>), reported with the YAML path of the board entry
        for name, reserved in (("ETH_0", "busid::ETH_0"), ("SELFDIAG", "busid::SELFDIAG")):
            board = copy.deepcopy(BOARD)
            board["can"][1]["name"] = name
            project = self.two_bus_project(rename=name)
            with self.subTest(name=name):
                (message,) = self.check(board, project)
                self.assertEqual((message.severity, message.file.name, message.yaml_path),
                                 ("error", "board.yaml", "can[1].name"))
                self.assertIn(f"collides with the bus id {reserved}", message.text)
                self.assertIsInstance(message.line, int)
                self.expect_error("communication configuration rejected", board=board, project=project)
        # the checked-in profiles keep their names
        self.assertEqual(self.check(copy.deepcopy(BOARD), copy.deepcopy(PROJECT)), [])

    def test_more_buses_than_wake_up_bits(self):
        # no board of the repository has nine CAN buses: the limit is checked with a stub configuration
        buses = [SimpleNamespace(name=f"CAN{i}") for i in range(nxcan.MAX_CAN_BUSES + 1)]
        config = SimpleNamespace(board=SimpleNamespace(can=buses), selected=lambda kind: buses,
                                 project=SimpleNamespace(interrupts=SimpleNamespace(
                                     ranks=[SimpleNamespace(source="can", name=b.name) for b in buses])))
        reporter = bswgen.Reporter()
        nxcan.check_buses(config, reporter)
        (message,) = reporter.messages
        self.assertEqual((message.severity, message.yaml_path), ("error", "use.can"))
        self.assertIn("9 CAN buses are used, at most 8 are supported", message.text)

    def test_limits_match_the_interfaces(self):
        # one wake-up bit per bus (ecuState) and one dispatch slot per FlexCAN instance (CanFlex2Transceiver)
        self.assertEqual((nxcan.MAX_CAN_BUSES, bswgen.CAN_WAKEUP_COUNT, bswgen.CAN_WAKEUP_SHIFT), (8, 8, 8))
        header = (REPO / "libs/bsw/ecuState/include/ecuState/EcuState.h").read_text(encoding="utf-8")
        self.assertIn(f"CAN_BUS_COUNT = {nxcan.MAX_CAN_BUSES}U;", header)
        self.assertIn(f"CAN_BUS_SHIFT = {bswgen.CAN_WAKEUP_SHIFT}U;", header)
        driver = (REPO / "external/openbsw/platforms/s32k1xx/bsp/canflex2Transceiver/include/can/transceiver/"
                  "canflex2/CanFlex2Transceiver.h").read_text(encoding="utf-8")
        self.assertIn(f"fpCanTransceivers[{bswgen.FLEXCAN_MAX_INSTANCE + 1}]", driver)

    def test_second_bus_of_the_eval_board_has_too_few_message_buffers(self):
        """ARCH-42 (COM-05): FlexCAN2 of the S32K144 has 16 message buffers, FlexCANDevice of OpenBSW transmitted from
        16..31.

        With a driver that needs more buffers than the instance has, nxconfig rejects the bus with its YAML path
        (tools/nxconfig/test_nxconfig.py MessageBufferTest); bswgen repeats the text as its last line of defence when
        it is called on a configuration nxconfig did not check.
        """
        # COM-26: the family port's driver splits the 16 buffers; with the requirement of the OpenBSW driver (32) the
        # guard still reports it
        self.generate(project=self.two_bus_project())
        openbsw_driver = mock.patch.dict(bswgen.nxchecks.CAN_DRIVER_MESSAGE_BUFFERS,
                                         {"s32k1xx": (32, "FlexCANDevice transmits from buffers 16..31")})
        with openbsw_driver:
            self.expect_error("CAN bus CAN1 is on FlexCAN2, which has 16 message buffers on the S32K144; the CAN "
                              "driver of s32k1xx needs 32", project=self.two_bus_project())
        config = bswgen.nxconfig.load(self.write(copy.deepcopy(BOARD), self.two_bus_project()))
        can1 = next(b for b in config.board.can if b.name == "CAN1")
        board = object.__new__(bswgen.Board)              # _can_device alone, without the checks of generate()
        board.config, board.irq_levels = config, {("can", "CAN1"): (3, 8)}
        with openbsw_driver, self.assertRaises(bswgen.ConfigError) as ctx:
            board._can_device(can1, "CAN1", 1, None)
        with openbsw_driver:
            self.assertEqual(str(ctx.exception), bswgen.nxchecks.can_message_buffers_problem(config, can1))

    def test_driver_requirement_matches_flexcan_device(self):
        """ARCH-42/COM-26: the requirement in nxconfig is the smallest instance the family port's driver splits."""
        self.assertEqual(bswgen.nxchecks.FLEXCAN_DRIVER_MESSAGE_BUFFERS, bswgen.CAN_MIN_MESSAGE_BUFFERS)
        self.assertEqual(bswgen.nxchecks.CAN_DRIVER_MESSAGE_BUFFERS["s32k1xx"][0], bswgen.CAN_MIN_MESSAGE_BUFFERS)
        driver = (REPO / "platforms/s32k1xx/port/bspConfiguration/bspFlexCan/include/can/FlexCANDevice.h") \
            .read_text(encoding="utf-8")
        self.assertIn("static_cast<uint8_t>(count / 2U), static_cast<uint8_t>(count - 1U)", driver)
        # the generated split of 32 buffers is the one of the OpenBSW driver
        self.assertEqual(bswgen.CAN_RX_BUFS_STD + bswgen.CAN_RX_BUFS_EXT, 16)
        self.assertEqual(16 + bswgen.CAN_TX_BUFS_APP + 1, bswgen.CAN_DEFAULT_MESSAGE_BUFFERS)

    def test_no_vector_for_buffers_the_instance_does_not_have(self):
        """ARCH-42: vectors follow message_buffers of mcudb, not only the device header (S32K144 uses S32K148's)."""
        config = bswgen.nxconfig.load(self.write(copy.deepcopy(BOARD), self.two_bus_project()))
        buses = {b.name: b for b in config.board.can}
        irqs = {f"CAN{n}_ORed_{r}_MB_IRQn": 0 for n in (0, 2) for r in ("0_15", "16_31")}
        self.assertEqual(bswgen.flexcan_mb_irqs(config, buses["CAN0"], irqs),
                         ["CAN0_ORed_0_15_MB_IRQn", "CAN0_ORed_16_31_MB_IRQn"])
        self.assertEqual(bswgen.flexcan_mb_irqs(config, buses["CAN1"], irqs), ["CAN2_ORed_0_15_MB_IRQn"])
        # without the fact the header decides, as before ARCH-42
        unknown = config.derivative.instances["FlexCAN2"].model_copy(update={"message_buffers": None})
        without = SimpleNamespace(derivative=SimpleNamespace(instances={"FlexCAN2": unknown}))
        self.assertEqual(bswgen.flexcan_mb_irqs(without, buses["CAN1"], irqs),
                         ["CAN2_ORed_0_15_MB_IRQn", "CAN2_ORed_16_31_MB_IRQn"])
        # generated interrupt names of the used buses (nxIrqNames.h)
        self.generate(project=self.two_bus_project())
        names = (self.tmp / "gen" / "interrupts" / "nxIrqNames.h").read_text(encoding="utf-8")
        self.assertIn("X(CAN1, CAN2_ORed_0_15_MB_IRQn)", names)
        self.assertNotIn("CAN2_ORed_16_31", names)


class CanBufferSplitTest(unittest.TestCase):
    """COM-26: message buffers split per FlexCAN instance (mcudb message_buffers) for FlexCANDevice of the family port.

    Profiles: the eval board demo (CAN0 on FlexCAN0, 32 buffers - generated split unchanged), the eval board with
    both buses (CAN1 on FlexCAN2, 16 buffers; accepted by nxconfig since the COM-26 requirement of 8 buffers), the
    S32K148 build test profile and every S32K1xx
    derivative of mcudb.
    """

    setUp, tearDown = CanBusTest.setUp, CanBusTest.tearDown
    write = ModuleIntegrationTest.write
    generate, read = CanBusTest.generate, CanBusTest.read
    DRIVER = REPO / "platforms/s32k1xx/port/bspConfiguration/bspFlexCan/include/can/FlexCANDevice.h"

    @staticmethod
    def device_config(dev: str, bus: str) -> str:
        return re.search(rf"{bus}_DEVICE_CONFIG = \{{\n(.*?)\n\}};", dev, re.S).group(1)

    @staticmethod
    def valid(split) -> bool:
        """Python replica of FlexCANDevice::isValidLayout() (driver copy of the family port)."""
        n = split.buffers
        return (4 <= n <= 32 and n % 2 == 0 and split.rx_std + split.rx_ext <= n // 2
                and n // 2 + split.tx_app <= n - 1)

    def test_demo_project_keeps_the_split_of_32_buffers(self):
        _, dev = self.generate()
        config = self.device_config(dev, "CAN0")
        self.assertIn("    14U, // numRxBufsStd", config)
        self.assertIn("    2U, // numRxBufsExt", config)
        self.assertIn("    15U, // numTxBufsApp", config)
        self.assertIn("    32U, // numMessageBuffers: message buffers of FlexCAN0 (mcudb), receive 0..15, transmit "
                      "16..31", config)
        self.assertTrue(config.rstrip().endswith("transmit 16..31"))      # last member of Config
        self.assertIn("static_assert(\n    ::bios::FlexCANDevice::isValidLayout(CAN0_DEVICE_CONFIG),", dev)
        self.assertIn("#ifndef NX_FLEXCAN_MESSAGE_BUFFER_LAYOUT\n#error", dev)
        self.assertNotIn("FIRST_TRANSMIT_BUFFER", dev)                  # constants of the OpenBSW driver are gone
        self.assertIn("X##_TX(CAN0, 0, CAN0_ORed_16_31_MB_IRQHandler", dev)

    def test_second_bus_on_16_buffers(self):
        _, dev = self.generate(project=CanBusTest.two_bus_project())
        config = self.device_config(dev, "CAN1")
        self.assertIn("    IP_FLEXCAN2_BASE, // baseAddress", config)
        self.assertIn("    6U, // numRxBufsStd", config)
        self.assertIn("    2U, // numRxBufsExt", config)
        self.assertIn("    7U, // numTxBufsApp", config)
        self.assertIn("    16U, // numMessageBuffers: message buffers of FlexCAN2 (mcudb), receive 0..7, transmit 8..15",
                      config)
        self.assertIn("    32U, // numMessageBuffers: message buffers of FlexCAN0", self.device_config(dev, "CAN0"))
        # one vector for receive and transmit (FlexCANDevice::transmitISR() checks the flag of the callback buffer)
        self.assertIn("X##_RX_TX(CAN1, 2, CAN2_ORed_0_15_MB_IRQHandler, CAN2_ORed_0_15_MB_IRQn)", dev)
        self.assertNotIn("CAN2_ORed_16_31", dev)

    def test_split_of_every_s32k1xx_instance(self):
        """S32K-RM Rev. 13 Table 55-1 via mcudb: 32 -> 14/2/15 (callback 31), 16 -> 6/2/7 (callback 15)."""
        expected = {32: (14, 2, 15, 16, 31), 16: (6, 2, 7, 8, 15)}
        seen = set()
        for path in sorted((REPO / "mcudb/s32k1xx").glob("S32K1??.yaml")):
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
            for name, inst in doc["instances"].items():
                if inst.get("class") != "can":
                    continue
                config = SimpleNamespace(derivative=SimpleNamespace(instances={
                    name: SimpleNamespace(message_buffers=inst.get("message_buffers"))}))
                split = bswgen.can_buffer_split(config, SimpleNamespace(name="BUS", instance=name))
                with self.subTest(derivative=path.stem, instance=name):
                    self.assertEqual((split.rx_std, split.rx_ext, split.tx_app, split.first_transmit, split.callback),
                                     expected[inst["message_buffers"]])
                    self.assertEqual(split.buffers, inst["message_buffers"])
                    self.assertTrue(self.valid(split))
                    seen.add(split.buffers)
        self.assertEqual(seen, {16, 32})

    def test_split_limits(self):
        def split(buffers):
            config = SimpleNamespace(derivative=SimpleNamespace(instances={
                "FlexCAN0": SimpleNamespace(message_buffers=buffers)}))
            return bswgen.can_buffer_split(config, SimpleNamespace(name="BUS", instance="FlexCAN0"))

        self.assertEqual(split(None).buffers, 32)                      # unknown: the OpenBSW assumption
        self.assertEqual((split(64).buffers, split(64).rx_std), (32, 14))   # driver uses IFLAG1 only
        self.assertEqual((split(8).rx_std, split(8).tx_app), (2, 3))
        for bad in (6, 15):
            with self.subTest(buffers=bad), self.assertRaises(bswgen.ConfigError) as ctx:
                split(bad)
            self.assertIn(f"FlexCAN0 has {bad} message buffers", str(ctx.exception))
        # the 32-buffer constants rtegen falls back to are the split of 32
        self.assertEqual((bswgen.CAN_RX_BUFS_STD, bswgen.CAN_RX_BUFS_EXT, bswgen.CAN_TX_BUFS_APP), (14, 2, 15))

    def test_split_vectors_need_transmit_from_16(self):
        """Separate receive/transmit vectors (buffers 0..15 / 16..31) only fit a split that transmits from 16."""
        bus = SimpleNamespace(name="BUS", instance="FlexCAN0")
        board = SimpleNamespace(config=SimpleNamespace(derivative=SimpleNamespace(
            instances={"FlexCAN0": SimpleNamespace(message_buffers=None)},
            derivative=SimpleNamespace(name="S32K144"))),
            irqs={"CAN0_ORed_0_15_MB_IRQn": 0, "CAN0_ORed_16_31_MB_IRQn": 0})
        ok = bswgen.CanBufferSplit(32, 14, 2, 15)
        self.assertEqual([v["serves"] for v in bswgen.Board._can_mb_vectors(board, bus, 0, ok)], ["RX", "TX"])
        with self.assertRaises(bswgen.ConfigError) as ctx:
            bswgen.Board._can_mb_vectors(board, bus, 0, bswgen.CanBufferSplit(24, 10, 2, 11))
        self.assertIn("transmit from buffer 12, but the derivative splits the interrupts at buffer 16", str(ctx.exception))

    def test_driver_copy_matches_the_generator(self):
        """The family port's FlexCANDevice.h holds the limits the generator splits for."""
        driver = self.DRIVER.read_text(encoding="utf-8")
        self.assertRegex(driver, rf"DEFAULT_MESSAGE_BUFFERS = {bswgen.CAN_DEFAULT_MESSAGE_BUFFERS}U;")
        self.assertRegex(driver, r"MAX_MESSAGE_BUFFERS\s*= 32U;")
        self.assertIn("uint8_t numMessageBuffers;\n    };", driver)      # last member: old initialisers stay valid
        self.assertIn("#define NX_FLEXCAN_MESSAGE_BUFFER_LAYOUT 1", driver)
        self.assertNotIn("FIRST_TRANSMIT_BUFFER", driver)
        cmake = (REPO / "platforms/s32k1xx/port/bspConfiguration/CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn('set_property(TARGET bspFlexCan PROPERTY SOURCES\n'
                      '                 "${CMAKE_CURRENT_LIST_DIR}/bspFlexCan/src/can/FlexCANDevice.cpp")', cmake)
        self.assertIn('target_include_directories(bspFlexCan BEFORE PUBLIC "${CMAKE_CURRENT_LIST_DIR}/bspFlexCan/include")',
                      cmake)


UART_PROJECT =HERE / "test" / "profiles" / "projects" / "testproject_uart" / "project.yaml"
UART_BOARD = HERE / "test" / "profiles" / "boards" / "testboard_uart" / "board.yaml"
PORT = REPO / "platforms" / "s32k1xx" / "port"          # PLAT-23: family port template, no longer a board port


class ValidationBeforeGeneratorTest(unittest.TestCase):
    """ARCH-33: a missing CAN rank and a PWM frequency/dead time the FTM cannot count are rejected by nxconfig with
    file, line and YAML path; bswgen keeps its check only as the last line of defence, with the same text.

    Errors built in on purpose: the demo project without the rank of CAN0 and with a dead time of 40 us (the two
    gaps DOC-02 found, doc/guide/build-and-flash.md). The last line is reached only by bypassing nxconfig: the
    checked configuration is changed afterwards and handed to collect() directly.
    """

    CAN_TEXT = ("CAN bus CAN0 is used, but has no interrupt rank: the board port cannot set the level of its message "
                "buffer interrupts (interrupts.ranks: {source: can, name: CAN0, rank: <n>})")
    PWM_TEXT = ("PWM group MOT1: dead time 40000 ns exceeds the FlexTimer maximum of 12600 ns (63 x 16 periods of the "
                "counter clock 80000000 Hz of FTM0)")

    setUp, tearDown = CanBusTest.setUp, CanBusTest.tearDown
    write = ModuleIntegrationTest.write

    def without_can_rank(self):
        project = one_bus_demo()                    # one message for CAN0 (COM-05: the demo also uses CAN1)
        project["interrupts"]["ranks"] = [r for r in project["interrupts"]["ranks"] if r["source"] != "can"]
        return project

    def long_dead_time(self):
        project = copy.deepcopy(PROJECT)
        project["pwm"]["MOT1"]["deadtime_ns"] = 40_000
        return project

    def test_nxconfig_rejects_before_the_generator(self):
        for project, path, text in ((self.without_can_rank(), "interrupts.ranks", self.CAN_TEXT),
                                    (self.long_dead_time(), "pwm.MOT1.deadtime_ns", self.PWM_TEXT)):
            with self.subTest(path=path):
                file = self.write(copy.deepcopy(BOARD), project)
                # validate() (Studio, TOOL-07): the finding of nxconfig with file, line and path, nothing else
                errors = [m for m in bswgen.validate(file) if m.severity == "error"]
                self.assertEqual([(m.file.name, m.yaml_path, m.text) for m in errors], [("project.yaml", path, text)])
                self.assertIsInstance(errors[0].line, int)
                # generate() stops with the same message before anything is written
                with self.assertRaises(bswgen.ConfigError) as ctx:
                    bswgen.generate(file, self.tmp / "gen")
                self.assertRegex(str(ctx.exception), rf"^project\.yaml:\d+ \({re.escape(path)}\): error: "
                                                     rf"{re.escape(text)}$")
                self.assertFalse((self.tmp / "gen").exists())

    def checked(self):
        config, messages = nxconfig.check(self.write(copy.deepcopy(BOARD), copy.deepcopy(PROJECT)))
        self.assertEqual([m for m in messages if m.severity == "error"], [])
        return config

    def test_last_line_of_defence_can_rank(self):
        config = self.checked()
        config.project.interrupts.ranks[:] = [r for r in config.project.interrupts.ranks if r.source != "can"]
        with self.assertRaises(bswgen.ConfigError) as ctx:
            bswgen.collect(bswgen.Board(config))
        self.assertEqual(str(ctx.exception), self.CAN_TEXT)
        self.assertEqual(str(ctx.exception), nxconfig.checks.can_rank_missing("CAN0"))

    def test_last_line_of_defence_pwm_counter(self):
        config = self.checked()
        config.project.pwm["MOT1"].deadtime_ns = 40_000
        with self.assertRaises(bswgen.ConfigError) as ctx:
            bswgen.collect(bswgen.Board(config))
        self.assertEqual(str(ctx.exception), self.PWM_TEXT)
        # with the value of the project the generator computes what nxconfig accepted (one rule, pwm_timing)
        config.project.pwm["MOT1"].deadtime_ns = 1_000
        pwm = bswgen.collect(bswgen.Board(config)).pwm
        self.assertEqual((pwm["period_ticks"], pwm["deadtime_dtps"], pwm["deadtime_div"], pwm["deadtime_ticks"]),
                         (4000, 2, 4, 20))


class UartTest(unittest.TestCase):
    """ARCH-15: LPUART instance, receive interrupt, interrupt level and console of every used UART.

    Profiles: the eval board demo (one UART on LPUART2, the console), the build-test profile S32K148 (console on
    LPUART1 - before ARCH-15 the board port wrote to LPUART2 without any sign, ARCH-09 B2) and the synthetic
    profile testproject_uart with **two** UARTs on two other instances. The checks of the UART names and of the
    missing interrupt rank belong to nxconfig (tools/nxconfig/uart.py).
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    write = ModuleIntegrationTest.write

    def generate(self, project_file=None, board=None, project=None, out="gen"):
        path = project_file or self.write(board or copy.deepcopy(BOARD), project or copy.deepcopy(PROJECT))
        bswgen.generate(path, self.tmp / out)
        return (self.tmp / out / "bsp" / "uart" / "uartConfiguration.h").read_text(encoding="utf-8")

    def check(self, board, project):
        """Runs the UART checks of nxconfig on a board/project copy and returns the messages."""
        config = bswgen.nxconfig.load(self.write(board, project))
        reporter = bswgen.Reporter(sources=dict(config.sources))
        nxuart.check_uarts(config, reporter)
        return reporter.messages

    # --- generated configuration ------------------------------------------------------------------

    @on_eval_board
    def test_eval_board_console_on_lpuart2(self):
        header = self.generate()
        # same instance, interrupt and priority the board port had by hand before ARCH-15 (IO-17: level 9)
        self.assertIn("#define NX_UART_UART_INSTANCE        2U // LPUART2", header)
        self.assertIn("#define NX_UART_UART_IRQ_LEVEL       9U // receive interrupt: rank 4 of the project",
                      header)
        self.assertIn('#define NX_UART_INSTANCES(X) \\\n    X(UART, 0, IP_LPUART2, "UART on LPUART2", '
                      "NX_UART_UART_BAUD_REGISTER, LPUART2_RxTx_IRQn, LPUART2_RxTx_IRQHandler, "
                      "NX_UART_UART_IRQ_LEVEL)\n", header)
        self.assertIn("#define NX_UART_COUNT 1\n", header)
        self.assertIn("#define NX_UART_CONSOLE UART\n", header)
        self.assertIn("#define NX_UART_UART_BAUD_REGISTER   0x0F00001AU", header)  # unchanged (ARCH-13)

    def test_build_test_profile_console_on_lpuart1(self):
        """ARCH-09 B2: a board with the console on LPUART1 must reach LPUART1, not LPUART2."""
        header = self.generate(REPO / "projects" / "buildtest_s32k148_min" / "project.yaml")
        self.assertIn("#define NX_UART_UART_INSTANCE        1U // LPUART1", header)
        self.assertIn('X(UART, 0, IP_LPUART1, "UART on LPUART1", NX_UART_UART_BAUD_REGISTER, '
                      "LPUART1_RxTx_IRQn, LPUART1_RxTx_IRQHandler, NX_UART_UART_IRQ_LEVEL)", header)
        self.assertNotIn("LPUART2", header)
        # and the board profile really describes the EVB console (PTC6/PTC7) again
        board = yaml.safe_load(
            (REPO / "boards" / "buildtest_s32k148" / "board.yaml").read_text(encoding="utf-8"))
        self.assertEqual([(u["instance"], u["rx"]["pin"], u["tx"]["pin"]) for u in board["uart"]],
                         [("LPUART1", "PTC6", "PTC7")])

    def test_two_uarts_on_other_instances(self):
        """Own profile (question 7): two UARTs, other instances, own baud rates and interrupt ranks."""
        header = self.generate(UART_PROJECT)
        self.assertIn("#define NX_UART_CONSOLE_INSTANCE        1U // LPUART1", header)
        self.assertIn("#define NX_UART_AUX_INSTANCE        0U // LPUART0", header)
        self.assertIn("#define NX_UART_CONSOLE_IRQ_LEVEL       9U", header)    # rank 4 + syscall level 5
        self.assertIn("#define NX_UART_AUX_IRQ_LEVEL       11U", header)       # rank 6 + syscall level 5
        self.assertIn("#define NX_UART_CONSOLE_BAUDRATE        115200U", header)
        self.assertIn("#define NX_UART_AUX_BAUDRATE        9600U", header)
        self.assertNotEqual(*re.findall(r"#define NX_UART_\w+_BAUD_REGISTER\s+(0x[0-9A-F]+)U", header))
        self.assertIn("#define NX_UART_COUNT 2\n", header)
        self.assertIn("#define NX_UART_CONSOLE CONSOLE\n", header)             # first used UART, not LPUART2
        entries = re.findall(r"    X\((\w+), (\d), (IP_LPUART\d), ", header)
        self.assertEqual(entries, [("CONSOLE", "0", "IP_LPUART1"), ("AUX", "1", "IP_LPUART0")])

    def test_project_selects_the_console_through_use(self):
        """The console is the first used UART: using only AUX moves it there, without a code change."""
        project = yaml.safe_load(UART_PROJECT.read_text(encoding="utf-8"))
        project["use"]["uart"] = ["AUX"]
        del project["uart"]["CONSOLE"]
        project["interrupts"]["ranks"] = [r for r in project["interrupts"]["ranks"] if r["name"] == "AUX"]
        header = self.generate(board=yaml.safe_load(UART_BOARD.read_text(encoding="utf-8")), project=project)
        self.assertIn("#define NX_UART_COUNT 1\n", header)
        self.assertIn("#define NX_UART_CONSOLE AUX\n", header)
        self.assertNotIn("CONSOLE_INSTANCE", header)

    def test_no_uart_used(self):
        project = yaml.safe_load(UART_PROJECT.read_text(encoding="utf-8"))
        project["use"]["uart"], project["uart"], project["interrupts"]["ranks"] = [], {}, []
        header = self.generate(board=yaml.safe_load(UART_BOARD.read_text(encoding="utf-8")), project=project)
        self.assertIn("// the project uses no UART (use.uart)\n#define NX_UART_INSTANCES(X)\n", header)
        self.assertIn("#define NX_UART_COUNT 0\n", header)
        self.assertNotIn("NX_UART_CONSOLE", header)          # the board port stops with its own #error

    # --- checks of nxconfig (tools/nxconfig/uart.py) ----------------------------------------------

    def test_used_uart_without_interrupt_rank(self):
        project = copy.deepcopy(PROJECT)
        project["interrupts"]["ranks"] = [r for r in project["interrupts"]["ranks"] if r["source"] != "uart"]
        (message,) = self.check(copy.deepcopy(BOARD), project)
        self.assertEqual((message.severity, message.file.name, message.yaml_path),
                         ("warning", "project.yaml", "interrupts.ranks"))
        self.assertIn("UART UART is used, but has no interrupt rank", message.text)
        self.assertIsInstance(message.line, int)
        # the level macro is generated undefined on purpose: the board port stops at its name
        header = self.generate(project=project)
        self.assertIn("#define NX_UART_UART_IRQ_LEVEL       NX_IRQ_LEVEL_UART_UART\n", header)
        self.assertIn("NX_UART_UART_IRQ_LEVEL stays undefined on purpose", header)

    def test_uart_name_must_not_take_a_fixed_enumerator(self):
        for name in nxuart.RESERVED_UART_NAMES:
            board, project = copy.deepcopy(BOARD), copy.deepcopy(PROJECT)
            board["uart"][0]["name"] = name
            project["uart"] = {name: project["uart"].pop("UART")}
            project["use"]["uart"] = [name]
            for rank in project["interrupts"]["ranks"]:
                if rank["source"] == "uart":
                    rank["name"] = name
            with self.subTest(name=name):
                (message,) = self.check(board, project)
                self.assertEqual((message.severity, message.file.name, message.yaml_path),
                                 ("error", "board.yaml", "uart[0].name"))
                self.assertIn(f"collides with the fixed enumerator bsp::Uart::Id::{name}", message.text)
                self.assertIsInstance(message.line, int)
                with self.assertRaises(bswgen.ConfigError) as ctx:
                    self.generate(board=board, project=project)
                self.assertIn("communication configuration rejected", str(ctx.exception))
        # the checked-in profiles keep their names
        self.assertEqual(self.check(copy.deepcopy(BOARD), copy.deepcopy(PROJECT)), [])

    def test_console_is_not_obvious_with_several_uarts(self):
        config = bswgen.nxconfig.load(UART_PROJECT)
        reporter = bswgen.Reporter(sources=dict(config.sources))
        nxuart.check_uarts(config, reporter)
        (message,) = reporter.messages
        self.assertEqual((message.severity, message.yaml_path), ("warning", "use.uart"))
        self.assertIn("the console (stdin/stdout) is the first one in board order, CONSOLE on LPUART1",
                      message.text)

    def test_instance_must_be_an_lpuart(self):
        # No s32k1xx derivative has a UART instance that is not an LPUART, so the guard of the generated table
        # is checked with a stub entry (as test_more_buses_than_wake_up_bits does for CAN).
        bus = SimpleNamespace(name="CONSOLE", instance="USART2")
        with self.assertRaises(bswgen.ConfigError) as ctx:
            bswgen.Board._uart_device(SimpleNamespace(irq_levels={}), bus, "CONSOLE", 0)
        self.assertIn("UART CONSOLE: instance USART2 is no LPUART", str(ctx.exception))
        self.assertIn("bsp::Uart drives LPUART", str(ctx.exception))

    # --- the vector table of the board port -------------------------------------------------------

    def test_port_vector_mask_matches_the_vector_table(self):
        """NX_PORT_LPUART_VECTORS must list exactly the LPUART vectors startUp.S routes to a handler."""
        # SYS-170: the ARMv7-M table is the generated list of the derivative (S32K144 of the eval board)
        startup = (PORT / "main" / "src" / "bsp" / "startUp.S").read_text(encoding="utf-8")
        self.assertIn('#include "startup/nxIrqVectors.inc"', startup)
        with tempfile.TemporaryDirectory() as tmp:
            bswgen.generate(EVAL_PROJECT_FILE if EVAL_PROJECT_FILE.exists() else PROJECT_FILE, Path(tmp))
            table = (Path(tmp) / "startup" / "nxIrqVectors.inc").read_text(encoding="utf-8")
            wrapped = (Path(tmp) / "interrupts" / "nxSafeIsrVectors.h").read_text(encoding="utf-8")
        vectors = (PORT / "bspConfiguration" / "include" / "bsp" / "uart" / "UartVectors.h") \
            .read_text(encoding="utf-8")
        vectored = sorted(int(n) for n in re.findall(r"^NX_IRQ_VECTOR\(LPUART(\d)_RxTx_IRQHandler\)", table,
                                                     re.MULTILINE))
        mask = int(re.search(r"#define NX_PORT_LPUART_VECTORS\s+(0x[0-9A-Fa-f]+)U", vectors).group(1), 16)
        self.assertEqual(vectored, [0, 1, 2])
        self.assertEqual(mask, sum(1 << n for n in vectored))
        # every vectored name has a weak default (IsrWrapper.cpp), so an unused instance needs no handler (IO-17)
        for n in vectored:
            self.assertIn(f"X(LPUART{n}_RxTx_IRQHandler, 0)", wrapped)

    def test_board_port_holds_no_uart_instance_any_more(self):
        """The reason for ARCH-09 B2: the port must not name an LPUART instance, interrupt or handler itself."""
        for rel in ("bspConfiguration/src/bsp/uart/UartConfig.cpp",
                    "bspConfiguration/include/bsp/uart/UartConfig.h",
                    "bspConfiguration/src/bsp/stdIo/stdIo.cpp"):
            text = (PORT / rel).read_text(encoding="utf-8")
            with self.subTest(file=rel):
                self.assertNotRegex(text, r"IP_LPUART\d|LPUART\d_RxTx_IRQ")

    def test_no_board_port_copies_the_family_port(self):
        """PLAT-23 (finding ARCH-09 B6): a board port is Options.cmake plus board peculiarities.

        Until PLAT-23 the build-test port copied the safeSystem manifest and mcu.h of the eval board port and
        pulled in its sources with add_subdirectory. The manifest now lives once in the family port template and
        tools/nxconfig finds it for every board of the family (config.port_module_dirs).
        """
        self.assertTrue((PORT / "safety" / "safeSystem" / "module.yaml").is_file())
        boards = sorted(p for p in (REPO / "ecu" / "platforms").iterdir() if p.is_dir())
        self.assertTrue(boards)
        for port in boards:
            with self.subTest(board=port.name):
                others = [b.name for b in boards if b != port]
                for file in sorted(p for p in port.rglob("*") if p.is_file()):
                    rel = file.relative_to(port).as_posix()
                    text = file.read_text(encoding="utf-8", errors="replace")
                    self.assertNotIn(rel, ("mcu/include/mcu/mcu.h",),
                                     "the family header platforms/<family>/bsp/bspMcu is the only mcu.h")
                    self.assertFalse(rel.endswith("module.yaml") and (PORT / rel).is_file(),
                                     f"{rel} is a copy of the family port manifest")
                    for other in others:
                        self.assertNotIn(other, text, f"{rel} points at the board port {other}")


class UartHostTest(unittest.TestCase):
    """Builds Uart::Id and the configuration table from the generated X-macro on the host (ARCH-15).

    The same construction the board port uses (bspConfiguration/include/bsp/uart/UartConfig.h and
    src/bsp/uart/UartConfig.cpp) with stubs for the device header, for the two-UART profile. Deliberate errors
    show that the checks bite: an instance without a vector in the board port, and a console that is not one of
    the generated UARTs.
    """

    PRELUDE = """
#include "bsp/uart/uartConfiguration.h"
#include <cstddef>
#include <cstdint>

// stand-ins for the symbols of the device header the generated macros name
struct LpuartType { uint32_t BAUD; };
static LpuartType lpuart0, lpuart1;
[[maybe_unused]] static LpuartType lpuart2;
#define IP_LPUART0 (&lpuart0)
#define IP_LPUART1 (&lpuart1)
#define IP_LPUART2 (&lpuart2)
enum IRQn_Type { LPUART0_RxTx_IRQn = 31, LPUART1_RxTx_IRQn = 33, LPUART2_RxTx_IRQn = 35 };
extern "C" void LPUART0_RxTx_IRQHandler(void);
extern "C" void LPUART1_RxTx_IRQHandler(void);
extern "C" void LPUART2_RxTx_IRQHandler(void);

// UartConfig.h of the board port
enum class UartId : size_t
{
#define NX_UART_ID(uart, index, regs, label, baud, irqn, handler, level) uart = index,
    NX_UART_INSTANCES(NX_UART_ID)
#undef NX_UART_ID
    INVALID  = NX_UART_COUNT,
    TERMINAL = NX_UART_CONSOLE,
};

// UartConfig.cpp of the board port: one entry and one interrupt handler per UART
// NOTE: a member accessed in a macro body must not be named like a macro parameter (regs, level, ...)
struct Entry { LpuartType* base; IRQn_Type irqNumber; uint8_t priority; char const* text; uint32_t reg; };
static Entry const entries[] = {
#define NX_UART_ENTRY(uart, index, regs, label, baud, irqn, handler, level) \\
    {regs, irqn, level, label, baud},
    NX_UART_INSTANCES(NX_UART_ENTRY)
#undef NX_UART_ENTRY
};
static_assert(sizeof(entries) / sizeof(entries[0]) == NX_UART_COUNT, "one entry per UART");

#define NX_UART_VECTOR_ASSERT(uart, index, regs, label, baud, irqn, handler, level) \\
    static_assert((NX_PORT_LPUART_VECTORS & (1U << NX_UART_##uart##_INSTANCE)) != 0U, \\
                  label ": no vector in the board port");
NX_UART_INSTANCES(NX_UART_VECTOR_ASSERT)
#undef NX_UART_VECTOR_ASSERT

#define NX_UART_ISR(uart, index, regs, label, baud, irqn, handler, level) \\
    extern "C" void handler(void) { entries[index].base->BAUD = 0U; }
NX_UART_INSTANCES(NX_UART_ISR)
#undef NX_UART_ISR
"""
    EXPECTATIONS = """
static_assert(NX_UART_COUNT == 2, "two UARTs");
static_assert(UartId::TERMINAL == UartId::CONSOLE, "console is the first used UART");
static_assert(static_cast<size_t>(UartId::CONSOLE) == 0 && static_cast<size_t>(UartId::AUX) == 1, "order");
static_assert(static_cast<size_t>(UartId::INVALID) == 2, "INVALID closes the enumeration");
int main()
{
    if (entries[0].base != IP_LPUART1 || entries[1].base != IP_LPUART0) { return 1; }
    if (entries[0].irqNumber != LPUART1_RxTx_IRQn || entries[1].irqNumber != LPUART0_RxTx_IRQn) { return 2; }
    if (entries[0].priority != 9U || entries[1].priority != 11U) { return 3; }
    if (entries[0].reg != 0x0F00001AU || entries[1].reg == entries[0].reg) { return 4; }
    LPUART1_RxTx_IRQHandler();
    LPUART0_RxTx_IRQHandler();
    return (lpuart0.BAUD == 0U && lpuart1.BAUD == 0U) ? 0 : 5;
}
"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.gen = self.tmp / "gen"
        bswgen.generate(UART_PROJECT, self.gen)
        self.vectors = self.tmp / "vectors"
        self.vectors.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def compile(self, source: str, mask: str = "0x07U", name="uart_host_test"):
        """Compiles PRELUDE + source against the generated header and the given board port vector mask."""
        path = self.tmp / f"{name}.cpp"
        path.write_text(f"#define NX_PORT_LPUART_VECTORS {mask}\n" + self.PRELUDE + source,
                        encoding="utf-8")
        cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
               f"-I{self.gen}", str(path), "-o", str(self.tmp / f"{name}.exe")]
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_two_uarts_build_the_enum_the_table_and_the_handlers(self):
        r = self.compile(self.EXPECTATIONS)
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        run = subprocess.run([str(self.tmp / "uart_host_test.exe")], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, f"expectation {run.returncode} failed")

    def test_used_instance_without_a_vector_is_a_build_error(self):
        # board port with vectors for LPUART1 and LPUART2 only: AUX on LPUART0 must not pass silently
        r = self.compile(self.EXPECTATIONS, mask="0x06U", name="uart_missing_vector")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("AUX on LPUART0: no vector in the board port", r.stderr)
        self.assertNotIn("CONSOLE on LPUART1: no vector", r.stderr)

    def test_console_must_be_one_of_the_generated_uarts(self):
        header = self.gen / "bsp" / "uart" / "uartConfiguration.h"
        text = header.read_text(encoding="utf-8")
        header.write_text(text.replace("#define NX_UART_CONSOLE CONSOLE",
                                       "#define NX_UART_CONSOLE SERIAL"), encoding="utf-8")
        r = self.compile(self.EXPECTATIONS, name="uart_wrong_console")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("SERIAL", r.stderr)


class BswGenAdcAccessTest(unittest.TestCase):
    """IO-19: converter settings of the project as ADC register values, derived timing, policy for CMake.

    The numbers themselves are derived by tools/nxconfig/adc.py (tested with its own profiles in
    test_nxconfig.py) and cross-checked against the C++ arithmetic by a static_assert in
    bsp/adc/AdcConversionTiming.h; here only the rendering is tested.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.out = self.tmp / "gen"
        bswgen.generate(PROJECT_FILE, self.out)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def read(self, rel):
        return (self.out / rel).read_text(encoding="utf-8")

    def test_module_parameters_of_the_project(self):
        # Modules without a board device: the values stand once in project.yaml modules.<module> (ARCH-12)
        access = self.read("integration/bspAdcAccessConfiguration.h")
        for line in ['#define NX_MODULE_BSPADCACCESS_PARAM_LOCK_POLICY "interrupt_lock"',
                     "#define NX_MODULE_BSPADCACCESS_PARAM_LOCK_POLICY_IS_INTERRUPT_LOCK 1",
                     "#define NX_MODULE_BSPADCACCESS_PARAM_LOCK_POLICY_IS_RTOS_MUTEX 0",
                     "#define NX_MODULE_BSPADCACCESS_PARAM_POLL_SAFETY_FACTOR 16U",
                     "#define NX_MODULE_BSPADCACCESS_PARAM_SAMPLE_CLOCKS 42U"]:
            self.assertIn(line, access)
        self.assertRegex(access, r"#define NX_MODULE_BSPADCACCESS_PARAM_LOCK_TIMEOUT_MS 100U( //.*)?\n")
        self.assertRegex(access, r"#define NX_MODULE_BSPADCACCESS_PARAM_MAX_INTERRUPT_SUSPEND_US 50U( //.*)?\n")
        safe = self.read("integration/safeSystemConfiguration.h")
        self.assertIn("#define NX_MODULE_SAFESYSTEM_PARAM_ADC_READ_ERROR_LIMIT 10U", safe)
        # a board port module: found through the board of the project, not through libs/
        self.assertIn("modules.safeSystem", safe)

    def test_register_values_and_derived_timing(self):
        # The board verified state of IO-16 must not change: conversion 1709 ns (76 ADC clocks at 48 MHz),
        # poll limit 2192 (factor 16). The register values are the ones AnalogInput.h had by hand.
        header = self.read("bsp/adc/adcAccessConfiguration.h")
        for line in ["#define NX_ADC_CFG1 (ADC_CFG1_ADIV(0) | ADC_CFG1_MODE(1))",
                     "#define NX_ADC_CFG2 ADC_CFG2_SMPLTS(41)",
                     "#define NX_ADC_SC3  (ADC_SC3_CAL(0) | ADC_SC3_ADCO(0) | ADC_SC3_AVGE(0) | ADC_SC3_AVGS(0))",
                     "#define NX_ADC_RESOLUTION_BITS 12U",
                     "#define NX_ADC_CLOCK_HZ 48000000U",
                     "#define NX_ADC_CONVERSION_CLOCKS 76U",
                     "#define NX_ADC_CONVERSION_NS 1709U",
                     "#define NX_ADC_POLL_CYCLES 137U",
                     "#define NX_ADC_POLL_LIMIT 2192U",
                     "#define NX_ADC_POLL_SAFETY_FACTOR 16U",
                     "#define NX_ADC_MAX_INTERRUPT_SUSPEND_NS 50000U",
                     "#define NX_ADC_ACCESS_INSTANCE_ADC0 1",
                     "#define NX_ADC_ACCESS_INSTANCE_ADC1 1"]:
            self.assertIn(line, header)
        self.assertIn("sample 42 + hold 1 + compare 28 (12 bit) + adder 5) * 1 = 76 ADC clocks", header)

    def test_policy_for_cmake(self):
        text = self.read("bsp/adc/adcAccessConfiguration.cmake")
        self.assertIn('set(NX_ADC_ACCESS_POLICY "interrupt_lock")', text)
        self.assertIn("set(NX_ADC_ACCESS_LOCK_TIMEOUT_MS 100)", text)
        self.assertIn("set(NX_ADC_CONVERSION_NS 1709)", text)
        probe = self.tmp / "probe.cmake"
        probe.write_text(f'include("{(self.out / "bsp/adc/adcAccessConfiguration.cmake").as_posix()}")\n'
                         'message(STATUS "policy=${NX_ADC_ACCESS_POLICY} limit=${NX_ADC_POLL_LIMIT}")\n',
                         encoding="utf-8")
        r = subprocess.run(["cmake", "-P", str(probe)], capture_output=True, text=True)
        if r.returncode != 0 and "not found" in (r.stderr or "").lower():
            self.skipTest("cmake not available")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("policy=interrupt_lock limit=2192", r.stderr + r.stdout)

    def test_other_converter_settings_change_everything(self):
        # Another sample time, divider and averaging: registers and bound follow without a code change (question 2)
        project = copy.deepcopy(PROJECT)
        project["project"]["board"] = BOARD_FILE.parent.as_posix()
        project["project"]["rte"] = (PROJECT_FILE.parent / "rte.yaml").as_posix()   # TOOL-114: required
        project["project"].pop("diag", None)  # the copy has no diag.yaml next to it (ARCH-51)
        project["modules"]["bspAdcAccess"].update(sample_clocks=12, clock_divider=4, averages=4)
        project.pop("adc_sampling", None)  # 16 slower conversions no longer fit one MOT1 period (IO-31)
        project_dir = self.tmp / "projects" / "p"
        project_dir.mkdir(parents=True, exist_ok=True)
        path = project_dir / "project.yaml"
        path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        bswgen.generate(path, self.tmp / "out2")
        header = (self.tmp / "out2" / "bsp/adc/adcAccessConfiguration.h").read_text(encoding="utf-8")
        self.assertIn("#define NX_ADC_CFG1 (ADC_CFG1_ADIV(2) | ADC_CFG1_MODE(1))", header)
        self.assertIn("#define NX_ADC_CFG2 ADC_CFG2_SMPLTS(11)", header)
        self.assertIn("ADC_SC3_AVGE(1) | ADC_SC3_AVGS(0)", header)
        self.assertIn("#define NX_ADC_CLOCK_HZ 12000000U", header)   # 48 MHz / 4
        self.assertIn("#define NX_ADC_CONVERSION_CLOCKS 184U", header)  # (12 + 1 + 28 + 5) * 4
        self.assertNotIn("#define NX_ADC_POLL_LIMIT 2192U", header)


SAMPLING_PROJECT = HERE / "test" / "profiles" / "projects" / "testproject_adc_sampling" / "project.yaml"


def sampling(**entries):
    """IO-31: project adc_sampling entries as the schema extension (doc-proposal-IO-31.diff) would load them; the
    generator reads them through bswgen.adc_sampling_specs, which the tests patch until the schema has the section."""
    specs = {name: SimpleNamespace(**{"kind": "pwm_period_average", **values}) for name, values in entries.items()}
    return mock.patch.object(bswgen, "adc_sampling_specs", return_value=specs)


class BswGenAdcSamplingTest(unittest.TestCase):
    """IO-31: averaging of an ADC channel over one period of a PWM group (generated
    bsp/adc/analogInputSamplingConfiguration.h), own profile testproject_adc_sampling plus the eval board demo."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def generate(self, project=SAMPLING_PROJECT, name="gen"):
        board = bswgen.generate(project, self.tmp / name)
        text = (self.tmp / name / "bsp/adc/analogInputSamplingConfiguration.h").read_text(encoding="utf-8")
        return board, text

    def expect_error(self, fragment, project=SAMPLING_PROJECT, **entries):
        with sampling(**entries), self.assertRaises(bswgen.ConfigError) as ctx:
            bswgen.generate(project, self.tmp / "err")
        self.assertIn(fragment, str(ctx.exception))

    def project_copy(self, change) -> Path:
        """The synthetic project with a change, next to its board (relative board path kept)."""
        doc = yaml.safe_load(SAMPLING_PROJECT.read_text(encoding="utf-8"))
        change(doc)
        doc["project"]["board"] = (SAMPLING_PROJECT.parents[2] / "boards" / "testboard_adc_sampling").as_posix()
        path = self.tmp / "p" / "project.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        return path

    def test_without_entries_every_channel_converts_once(self):
        # today's behaviour for every project without the section, and for every project while the schema lacks it
        board, text = self.generate(SAMPLING_PROJECT, "own")
        self.assertEqual(bswgen.adc_sampling_specs(board.config), {})
        self.assertTrue(all(p is None for p in board.adc_sampling))
        self.assertIn("#define NX_ADC_AVERAGED_CHANNEL_COUNT 0U", text)
        self.assertNotIn("analogInputSamplingConfiguration[]", text)
        self.assertIn("none: every channel converts once per read", text)

    def test_demo_averages_the_motor_currents(self):
        # IO-31, user decision 2026-09-28: the demo averages TSCURRENT and the phase currents over one MOT1 period
        board, text = self.generate(PROJECT_FILE, "demo")
        self.assertEqual(sorted(bswgen.adc_sampling_specs(board.config)),
                         ["ADC_CURRENT_U", "ADC_CURRENT_V", "ADC_CURRENT_W", "SENSE_TSCURRENT"])
        self.assertIn("#define NX_ADC_AVERAGED_CHANNEL_COUNT 4U", text)

    def test_realized_period_not_the_nominal_one(self):
        # 64 MHz / 15 kHz = 4266.7 -> 4267 ticks = 14998.8 Hz = 66671.9 ns: 8 samples every 8334 ns (nominal: 8333)
        with sampling(DC_LINK_I={"pwm_group": "BRIDGE", "samples": 8}):
            board, text = self.generate()
        self.assertEqual(board.pwm["period_ticks"], 4267)
        self.assertEqual(board.pwm["period_ns"], Fraction(4267 * 10**9, 64_000_000))
        plan = board.adc_sampling[0]
        self.assertEqual((plan["samples"], plan["spacing_ns"], plan["window_ns"]), (8, 8334, 7 * 8334))
        self.assertIn("#define NX_ADC_AVERAGED_CHANNEL_COUNT 1U", text)
        rows = re.findall(r"/\* +(\d+) \*/ \{(\d+)U, (\d+)U\}, // Ai_(\w+)", text)
        self.assertEqual(rows, [("0", "8", "8334", "DC_LINK_I"), ("1", "1", "0", "BUS_VOLTAGE"),
                                ("2", "1", "0", "SPARE_I")])
        self.assertIn("8 conversions every 8334 ns over one period of BRIDGE (14998.828 Hz", text)
        self.assertEqual(board.warnings, [])

    def test_eval_board_tscurrent(self):
        # the channel of the IO-15 load test: 20 kHz exactly (80 MHz / 4000), 16 samples every 3125 ns
        with sampling(SENSE_TSCURRENT={"pwm_group": "MOT1", "samples": 16}):
            board, text = self.generate(PROJECT_FILE, "demo")
        index = [c["name"] for c in board.adc].index("SENSE_TSCURRENT")
        self.assertEqual(board.adc_sampling[index]["spacing_ns"], 3125)
        self.assertIn(f"/* {index:2d} */ {{16U, 3125U}}, // Ai_SENSE_TSCURRENT: averaged over MOT1", text)
        self.assertEqual(text.count("{1U, 0U}"), len(board.adc) - 1)

    def test_rejected_entries(self):
        both = {"pwm_group": "BRIDGE", "samples": 8}
        self.expect_error("no ADC channel NOPE used by the project", NOPE=both)
        self.expect_error("the circuit of SPARE_I is not fitted on board testboard_adc_sampling", SPARE_I=both)
        self.expect_error("kind 'pwm_sync' is not supported", DC_LINK_I={**both, "kind": "pwm_sync"})
        self.expect_error("PWM group MOTOR is not used by the project (used: BRIDGE)",
                          DC_LINK_I={"pwm_group": "MOTOR", "samples": 8})
        for bad in (1, 65, True, "8"):
            self.expect_error(f"samples {bad} outside 2..64", DC_LINK_I={"pwm_group": "BRIDGE", "samples": bad})
        # 66671.9 ns / 1741 ns: at most 38 conversions fit into one period
        self.expect_error("39 conversions of 1741 ns do not fit into one PWM period of 66672 ns (at most 38)",
                          DC_LINK_I={"pwm_group": "BRIDGE", "samples": 39})
        # internal channels are read by the safety checks with the interrupts suspended
        self.expect_error("BANDGAP is an internal channel", PROJECT_FILE,
                          BANDGAP={"pwm_group": "MOT1", "samples": 8})

    def test_channel_not_selected_by_the_project(self):
        path = self.project_copy(lambda d: d["use"].update(adc=["BUS_VOLTAGE"]))
        self.expect_error("no ADC channel DC_LINK_I used by the project (use.adc: BUS_VOLTAGE)", path,
                          DC_LINK_I={"pwm_group": "BRIDGE", "samples": 8})

    def test_project_without_pwm_group(self):
        # a board with a bridge, a project that does not use it: no period to average over (ARCH-17 stays intact)
        path = self.project_copy(lambda d: d.pop("pwm"))
        board, text = self.generate(path, "nopwm")
        self.assertIsNone(board.pwm)
        self.assertIn("#define NX_ADC_AVERAGED_CHANNEL_COUNT 0U", text)
        self.expect_error("the project uses no PWM group, there is no period to average over", path,
                          DC_LINK_I={"pwm_group": "BRIDGE", "samples": 8})

    def test_tight_spacing_is_a_warning(self):
        # 30 samples: 2222 ns spacing for 1741 ns conversions fit, but leave less than half a conversion
        with sampling(DC_LINK_I={"pwm_group": "BRIDGE", "samples": 30}):
            board, _ = self.generate()
        self.assertEqual(len(board.warnings), 1)
        self.assertIn("adc_sampling DC_LINK_I: 30 conversions of 1741 ns leave 481 ns between two samples",
                      board.warnings[0])
        self.assertIn("at most 25 samples keep half a conversion free", board.warnings[0])
        with sampling(DC_LINK_I={"pwm_group": "BRIDGE", "samples": 25}):
            board, _ = self.generate(name="gen25")
        self.assertEqual(board.warnings, [])

    @unittest.skipUnless("adc_sampling" in nxconfig.model.Project.model_fields,
                         "schema extension IO-31 not applied (platforms/s32k1xx/doc-proposal-IO-31.diff)")
    def test_project_yaml_end_to_end(self):
        # with the schema extension: the section in project.yaml, checked by nxconfig, rendered without any patch
        path = self.project_copy(lambda d: d.update(adc_sampling={
            "DC_LINK_I": {"kind": "pwm_period_average", "pwm_group": "BRIDGE", "samples": 8}}))
        board, text = self.generate(path, "yaml")
        self.assertEqual(board.adc_sampling[0]["spacing_ns"], 8334)
        self.assertIn("/*  0 */ {8U, 8334U}, // Ai_DC_LINK_I: averaged over BRIDGE", text)
        # nxconfig rejects what the generator would reject, with the YAML path, before the generator runs
        path = self.project_copy(lambda d: d.update(adc_sampling={
            "DC_LINK_I": {"kind": "pwm_period_average", "pwm_group": "BRIDGE", "samples": 39}}))
        messages = [m for m in bswgen.validate(path) if m.severity == "error"]
        self.assertEqual([m.yaml_path for m in messages], ["adc_sampling.DC_LINK_I.samples"])

    def test_plan_arithmetic(self):
        plan = bswgen.adc_sampling_plan(Fraction(50000), 16, 1709)
        self.assertEqual((plan.spacing_ns, plan.window_ns, plan.problem, plan.warning), (3125, 46875, None, None))
        self.assertEqual(bswgen.adc_sampling_plan(Fraction(100000, 3), 2, 1709).spacing_ns, 16667)  # half up
        self.assertIsNotNone(bswgen.adc_sampling_plan(Fraction(50000), 29, 1709).warning)
        self.assertIsNone(bswgen.adc_sampling_plan(Fraction(50000), 29, 1709).problem)       # 29 * 1709 = 49561
        self.assertIsNotNone(bswgen.adc_sampling_plan(Fraction(50000), 30, 1709).problem)    # 51270 > 50000


class BswGenAdcSamplingHostTest(unittest.TestCase):
    """IO-31: the generated table compiled with adc/AdcAveraging.h (zig c++, -Werror) against a ripple of exactly the
    realized PWM period; mutated tables (nominal instead of realized period, one sample less) must fail."""

    SOURCE = HERE / "test" / "sampling_host" / "adc_sampling_host_test.cpp"
    FLAGS = ["-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion", "-Wno-sign-conversion", "-Werror"]

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build_and_run(self, gen: Path, board, channel: str | None):
        ticks, shift, clock = board.pwm["period_ticks"], board.pwm["prescaler_shift"], board.pwm["clock_hz"]
        period_ps = Fraction(ticks << shift, clock) * 10**12
        self.assertEqual(period_ps.denominator, 1)
        index = [c["name"] for c in board.adc].index(channel) if channel else 0
        exe = self.tmp / f"{gen.name}.exe"
        cmd = [sys.executable, "-m", "ziglang", "c++", *self.FLAGS, f"-I{REPO / 'libs/bsp/bspAdcAccess/include'}",
               f"-isystem{REPO / 'external/openbsw/libs/bsw/bsp/include'}", f"-I{gen}",
               f"-DNX_TEST_CHANNELS={len(board.adc)}U", f"-DNX_TEST_CHANNEL={index}U",
               f"-DNX_TEST_PERIOD_PS={period_ps.numerator}ULL", str(self.SOURCE), "-o", str(exe)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        return subprocess.run([str(exe)], capture_output=True, text=True)

    def run_profile(self, project, name, channel, entries):
        gen = self.tmp / name
        with sampling(**entries):
            board = bswgen.generate(project, gen)
        r = self.build_and_run(gen, board, channel)
        print("\n" + r.stdout.strip(), file=sys.stderr)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:])
        self.assertRegex(r.stdout, r"\d+ checks, 0 failures")
        return gen, board

    def test_synthetic_profile(self):
        self.run_profile(SAMPLING_PROJECT, "own", "DC_LINK_I", {"DC_LINK_I": {"pwm_group": "BRIDGE", "samples": 8}})

    def test_eval_board_demo(self):
        self.run_profile(PROJECT_FILE, "demo", "SENSE_TSCURRENT",
                         {"SENSE_TSCURRENT": {"pwm_group": "MOT1", "samples": 16}})

    def test_without_averaged_channel(self):
        self.run_profile(SAMPLING_PROJECT, "none", None, {})

    def test_built_in_faults_are_detected(self):
        gen, board = self.run_profile(SAMPLING_PROJECT, "mutants", "DC_LINK_I",
                                      {"DC_LINK_I": {"pwm_group": "BRIDGE", "samples": 8}})
        header = (gen / "bsp/adc/analogInputSamplingConfiguration.h").read_text(encoding="utf-8")
        mutants = {
            "spacing of the nominal instead of the realized period": ("{8U, 8334U}", "{8U, 8333U}"),
            "one sample less over the same spacing": ("{8U, 8334U}", "{7U, 8334U}"),
            "half the period": ("{8U, 8334U}", "{8U, 4167U}"),
            "averaged channel on the wrong row": ("/*  0 */ {8U, 8334U}", "/*  0 */ {1U, 0U}"),
        }
        for index, (what, (old, new)) in enumerate(mutants.items()):
            with self.subTest(mutant=what):
                self.assertEqual(header.count(old), 1, what)
                patched = self.tmp / f"mutant{index}"
                shutil.copytree(gen, patched)
                (patched / "bsp/adc/analogInputSamplingConfiguration.h").write_text(header.replace(old, new, 1),
                                                                                  encoding="utf-8")
                r = self.build_and_run(patched, board, "DC_LINK_I")
                self.assertNotEqual(r.returncode, 0, f"mutant not detected: {what}\n{r.stdout[-500:]}")


class ModuleHostTest(unittest.TestCase):
    """Compiles and runs the host tests of the modules touched by the integration (zig c++)."""

    FLAGS = ["-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion", "-Wno-sign-conversion", "-Werror"]

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_host_test(self, name, includes, system, sources):
        exe = self.tmp / f"{name}.exe"
        cmd = [sys.executable, "-m", "ziglang", "c++", *self.FLAGS, *[f"-I{REPO / i}" for i in includes],
               *[f"-isystem{REPO / i}" for i in system], *[str(REPO / s) for s in sources], "-o", str(exe)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        r = subprocess.run([str(exe)], capture_output=True, text=True)
        print("\n" + r.stdout.strip().splitlines()[-1], file=sys.stderr)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:])
        self.assertRegex(r.stdout, r"\d+ checks, 0 failures")
        return r.stdout

    def test_high_side_switch(self):
        self.run_host_test("high_side_switch", ["libs/bsw/highSideSwitch/include"], [], [
            "libs/bsw/highSideSwitch/src/highSideSwitch/HighSideSwitch.cpp",
            "libs/bsw/highSideSwitch/src/highSideSwitch/HighSideSwitchChannel.cpp",
            "libs/bsw/highSideSwitch/test/host/HighSideSwitchHostTest.cpp"])

    def switch_integration_headers(self, gen: Path) -> dict[str, str]:
        """Generated headers of the synthetic two-switch profile: module integration and (IO-21) the sensor
        characteristics of its IS channels, the latter rendered with the bswgen template alone (the test family of
        the profile has no complete board generation)."""
        config = modules.nxconfig.load(SWITCH_PROJECT, NXCONFIG_TEST / "mcudb")
        files = modules.render_all(config.integration, config.modules, "testproject_switches")
        sensors = bswgen.template_env().get_template("analogSensorConfiguration.h.j2").render(
            source="testproject_switches", quality_ok=nxsensor.QUALITY_OK, quality_range=nxsensor.QUALITY_RANGE,
            sensors=[{**plan.__dict__, "ident": bswgen.identifier(plan.name)} for plan in config.adc_sensors.values()])
        headers = {"integration/highSideSwitchConfiguration.h": files["integration/highSideSwitchConfiguration.h"],
                   "bsp/adc/analogSensorConfiguration.h": sensors}
        for rel, text in headers.items():
            (gen / rel).parent.mkdir(parents=True, exist_ok=True)
            (gen / rel).write_text(text, encoding="utf-8")
        return headers

    SWITCH_INTEGRATION_SOURCES = ["libs/bsw/highSideSwitch/src/highSideSwitch/HighSideSwitch.cpp",
                                  "libs/bsw/highSideSwitch/test/host/HighSideSwitchIntegrationHostTest.cpp"]

    def test_high_side_switch_integration(self):
        # Generated headers of the synthetic two-switch profile -> HighSideSwitchIntegration.h and the IS
        # characteristics (IO-21) -> state machine.
        self.switch_integration_headers(self.tmp / "gen")
        self.run_host_test("high_side_switch_integration", ["libs/bsw/highSideSwitch/include", self.tmp / "gen"], [],
                           self.SWITCH_INTEGRATION_SOURCES)

    def test_high_side_switch_sense_built_in_faults(self):
        # IO-21: the conversion checks of the host tests find wrong characteristics and a wrong adapter.
        gen = self.tmp / "gen"
        headers = self.switch_integration_headers(gen)
        sense_h = (LIBS_BSW / "highSideSwitch/include/highSideSwitch/HighSideSwitchSense.h").read_text(encoding="utf-8")
        mutants = {
            # LAMP_IS: reduced coefficient a of raw * 110 / 91 (1500 * 3300 / (4095 * 1000)) one too high
            "characteristic off by one": ("bsp/adc/analogSensorConfiguration.h", "INT64_C(110) * x", "INT64_C(111) * x"),
            "adapter passes the raw code": ("highSideSwitch/HighSideSwitchSense.h",
                                            "return (value > 0) ? static_cast<uint32_t>(value) : 0U;",
                                            "return (value > 0) ? static_cast<uint32_t>(raw) : 0U;"),
            "adapter drops the end of the range": ("highSideSwitch/HighSideSwitchSense.h",
                                                   "return (value > 0) ? static_cast<uint32_t>(value) : 0U;",
                                                   "return (quality == 1U) ? static_cast<uint32_t>(value) : 0U;"),
        }
        for index, (what, (rel, old, new)) in enumerate(mutants.items()):
            with self.subTest(mutant=what):
                patched = self.tmp / f"mutant{index}"
                shutil.copytree(gen, patched)
                source = sense_h if rel.startswith("highSideSwitch/") else headers[rel]
                self.assertEqual(source.count(old), 1, what)
                (patched / rel).parent.mkdir(parents=True, exist_ok=True)
                (patched / rel).write_text(source.replace(old, new, 1), encoding="utf-8")
                exe = self.tmp / f"mutant{index}.exe"
                cmd = [sys.executable, "-m", "ziglang", "c++", *self.FLAGS, f"-I{patched}",
                       f"-I{REPO / 'libs/bsw/highSideSwitch/include'}",
                       *[str(REPO / s) for s in self.SWITCH_INTEGRATION_SOURCES], "-o", str(exe)]
                r = subprocess.run(cmd, capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr[-3000:])
                r = subprocess.run([str(exe)], capture_output=True, text=True)
                self.assertNotEqual(r.returncode, 0, f"mutant not detected: {what}\n{r.stdout[-500:]}")

    # IO-19: the three host tests of IO-14/IO-16 run in the Python test series as well
    def test_adc_access(self):
        self.run_host_test("adc_access", ["libs/bsp/bspAdcAccess/include"],
                           ["external/openbsw/libs/bsw/bsp/include"],
                           ["libs/bsp/bspAdcAccess/test/host/AdcAccessHostTest.cpp"])

    # IO-31: averaging over one PWM period, model test and built-in faults of adc/AdcAveraging.h
    AVERAGING_TEST = "libs/bsp/bspAdcAccess/test/host/AdcAveragingHostTest.cpp"

    def test_adc_averaging(self):
        self.run_host_test("adc_averaging", ["libs/bsp/bspAdcAccess/include"],
                           ["external/openbsw/libs/bsw/bsp/include"], [self.AVERAGING_TEST])

    def test_adc_averaging_built_in_faults(self):
        header = (REPO / "libs/bsp/bspAdcAccess/include/adc/AdcAveraging.h").read_text(encoding="utf-8")
        mutants = {
            "samples back to back (no spacing)": ("uint32_t const offset = static_cast<uint32_t>(k) * spec.spacingNs;",
                                                  "uint32_t const offset = 0U;"),
            "mean truncated": ("(sum + (spec.samples / 2U)) / spec.samples", "sum / spec.samples"),
            "failed conversion ignored": ("            if (convert(raw) != bsp::BSP_OK)\n",
                                          "            if (false && convert(raw) != bsp::BSP_OK)\n"),
            "late samples not counted": ("(maxLate > (spec.spacingNs / 4U))", "false"),
            "grid restarts after each sample": ("if (!waitUntil(t0, offset, spec.spacingNs))",
                                                "if (!waitUntil(_lock.nowNs(), (k == 0U) ? 0U : spec.spacingNs, "
                                                "spec.spacingNs))"),
            "single conversion averaged as well": ("if (spec.samples <= 1U)", "if (spec.samples < 1U)"),
        }
        for index, (what, (old, new)) in enumerate(mutants.items()):
            with self.subTest(mutant=what):
                self.assertEqual(header.count(old), 1, what)
                include = self.tmp / f"mutant{index}"
                (include / "adc").mkdir(parents=True)
                (include / "adc" / "AdcAveraging.h").write_text(header.replace(old, new, 1), encoding="utf-8")
                exe = self.tmp / f"averaging_mutant{index}.exe"
                cmd = [sys.executable, "-m", "ziglang", "c++", *self.FLAGS, f"-I{include}",
                       f"-isystem{REPO / 'external/openbsw/libs/bsw/bsp/include'}", str(REPO / self.AVERAGING_TEST),
                       "-o", str(exe)]
                r = subprocess.run(cmd, capture_output=True, text=True)
                if r.returncode == 0:
                    r = subprocess.run([str(exe)], capture_output=True, text=True)
                self.assertNotEqual(r.returncode, 0, f"mutant not detected: {what}\n{r.stdout[-800:]}")

    def test_free_rtos_adc_lock(self):
        self.run_host_test(
            "free_rtos_adc_lock",
            ["libs/bsp/bspAdcAccess/include", "libs/bsp/bspAdcAccess/test/host/stubs"],
            ["external/openbsw/libs/bsw/bsp/include",
             "external/openbsw/libs/bsp/bspInterrupts/mock/include"],
            ["libs/bsp/bspAdcAccess/test/host/FreeRtosAdcLockHostTest.cpp"])

    def test_safe_system(self):
        safe = "platforms/s32k1xx/port/safety/safeSystem"
        self.run_host_test(
            "safe_system", [f"{safe}/include", f"{safe}/test/host/stubs"],
            ["external/openbsw/libs/bsw/bsp/include",
             "external/openbsw/libs/bsp/bspInterrupts/include",
             "external/openbsw/libs/bsp/bspInterrupts/mock/include"],
            [f"{safe}/test/host/SafeSystemHostTest.cpp", f"{safe}/src/safeSystem/SafeSystem.cpp"])

    def test_can_phy_gate(self):
        self.run_host_test(
            "can_phy_gate",
            ["libs/bsw/canSystem/include", "libs/bsw/canPhyPassive/include", "libs/bsw/sbcUja113x/include",
             "libs/bsp/bspSpi/include"],
            ["external/openbsw/libs/bsw/bsp/include"],
            ["libs/bsw/sbcUja113x/src/sbc/Uja113x.cpp", "libs/bsw/canSystem/test/host/CanPhyGateHostTest.cpp"])


S32K116_PROJECT = REPO / "projects" / "s32k116evb_minimal" / "project.yaml"


class DerivativeInterruptsTest(unittest.TestCase):
    """PLAT-12: everything the interrupt vectors of the derivative decide comes from its device header.

    Eval board (S32K144 with the OpenBSW S32K148 header, always available) and the S32K116EVB profile (header
    RTD headers from Zephyr hal_nxp with the adapter of mcudb/tools/rtd_adapter.py, checked in since PLAT-32).
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def read(self, rel):
        return (self.tmp / rel).read_text(encoding="utf-8")

    def test_eval_board_names_and_vector_list(self):
        bswgen.generate(PROJECT_FILE, self.tmp)
        names = self.read("interrupts/nxIrqNames.h")
        self.assertIn("#define NX_CAN_MB_IRQS_CAN0(X) \\\n    X(CAN0, CAN0_ORed_0_15_MB_IRQn) \\\n"
                      "    X(CAN0, CAN0_ORed_16_31_MB_IRQn)\n", names)
        vectors = self.read("startup/nxIrqVectors.inc")
        # SYS-170: the list names the handler, the startup routes every vector through Safe_<handler>
        self.assertIn("NX_IRQ_VECTOR(WDOG_EWM_IRQHandler)", vectors)       # family port names
        self.assertIn("NX_IRQ_VECTOR(FTFC_Fault_IRQHandler)", vectors)
        self.assertNotIn("Safe_", vectors)
        # the S32K144 has WDOG_EWM_IRQn itself: no alias, and no generated device header next to the build
        self.assertNotIn("#define WDOG_EWM_IRQn", self.read("mcu/mcuDerivative.h"))
        self.assertFalse((self.tmp / "3rdparty").exists())
        self.assertIn("#define NX_ADC_INSTANCE_COUNT 2", self.read("mcu/derivativeFacts.h"))
        self.assertIn("#define NX_CLOCK_LPIT_HZ", self.read("bsp/clock/boardClock.h"))

    def test_every_non_safety_vector_is_wrapped(self):
        """SYS-170 (SYS69-S1, SFP-F01): interrupts/nxSafeIsrVectors.h lists every handler of the vector list except the
        safety handlers, in vector order and once each; the CAN message buffer vectors of the used buses are served by
        the integration (no weak default), every other one is not."""
        bswgen.generate(PROJECT_FILE, self.tmp)
        vectors = [line.split("/*")[0].strip() for line in self.read("startup/nxIrqVectors.inc").splitlines()
                   if line.startswith("NX_IRQ_VECTOR(")]
        handlers = list(dict.fromkeys(v[len("NX_IRQ_VECTOR("):-1] for v in vectors))
        text = self.read("interrupts/nxSafeIsrVectors.h")
        wrapped = re.findall(r"^    X\((\w+), ([01])\)", text, re.M)
        self.assertEqual([h for h, _ in wrapped], [h for h in handlers if h not in bswgen.SAFETY_IRQ_HANDLERS])
        self.assertIn(f"#define NX_SAFE_ISR_VECTOR_COUNT {len(wrapped)}U", text)
        for safety in bswgen.SAFETY_IRQ_HANDLERS:
            with self.subTest(safety=safety):
                self.assertIn(safety, handlers)                                     # S32K14x has all four
                self.assertNotIn(f"X({safety},", text)
        # UART, SPI, pin events, FTM, LPIT, ADC, DMA of the review list (SysTick is a core vector: IsrWrapper.cpp)
        for wanted in ("LPUART0_RxTx_IRQHandler", "LPSPI0_IRQHandler", "PORTA_IRQHandler",
                       "FTM0_Ch0_Ch1_IRQHandler", "LPIT0_Ch0_IRQHandler", "ADC0_IRQHandler", "DMA0_IRQHandler"):
            with self.subTest(handler=wanted):
                self.assertIn((wanted, "0"), wrapped)
        mb = re.findall(r"X##_\w+\(\w+, \d+, (\w+), \w+\)", self.read("bsp/can/canDeviceConfiguration.h"))
        self.assertTrue(mb)
        self.assertEqual(sorted(h for h, s in wrapped if s == "1"), sorted(mb))

    def test_safe_isr_list_without_served_handlers_and_without_vectors(self):
        """SYS-170: the list function itself - aliases folded into one handler, safety handlers left out, served
        handlers marked, an empty list still a valid macro."""
        irqs = {"DMA0_IRQn": 0, "FTFC_IRQn": 1, "WDOG_IRQn": 2, "LPIT_IRQn": 3, "ERM_IRQn": 4, "PORTA_IRQn": 5,
                "CAN0_ORed_0_31_MB_IRQn": 6, "SysTick_IRQn": -1}
        text = bswgen.safe_isr_vectors("test", irqs, {"CAN0_ORed_0_31_MB_IRQHandler"})
        self.assertEqual(re.findall(r"X\((\w+), ([01])\)", text),
                         [("DMA0_IRQHandler", "0"), ("PORTA_IRQHandler", "0"), ("CAN0_ORed_0_31_MB_IRQHandler", "1")])
        self.assertIn("#define NX_SAFE_ISR_VECTOR_COUNT 3U", text)
        empty = bswgen.safe_isr_vectors("test", {"FTFC_IRQn": 0, "WDOG_IRQn": 1, "ERM_IRQn": 2}, set())
        self.assertIn("#define NX_SAFE_ISR_VECTORS(X)\n", empty)
        self.assertIn("#define NX_SAFE_ISR_VECTOR_COUNT 0U", empty)

    def test_startups_route_every_vector_through_its_wrapper(self):
        """SYS-170: both startups prefix every handler of the list with Safe_ (ARMv6-M template with
        NX_STARTUP_SAFE_ISR, which the application sets), SysTick through Safe_SysTick_Handler; the ARMv7-M table
        has no hand-written external vector and no weak handler stub left."""
        v7 = (PORT / "main" / "src" / "bsp" / "startUp.S").read_text(encoding="utf-8")
        v6 = (REPO / "platforms" / "s32k1xx" / "startup" / "armv6m" / "startUp.S").read_text(encoding="utf-8")
        cmake = (PORT / "main" / "CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn("#define NX_IRQ_VECTOR(handler) .long Safe_##handler ;", v7)
        self.assertIn('#include "startup/nxIrqVectors.inc"', v7)
        self.assertIn("Safe_SysTick_Handler", v7)
        self.assertNotRegex(v7, r"\.long\s+SysTick_Handler")
        self.assertNotRegex(v7, r"\.weak\s+\w+_IRQHandler")
        self.assertNotRegex(v7, r"\.long\s+\w+_IRQHandler")              # no hand-written external vector
        self.assertIn("#define NX_IRQ_VECTOR(handler) .long Safe_##handler ;", v6)
        self.assertIn("#define NX_SYSTICK_VECTOR      Safe_SysTick_Handler", v6)
        self.assertIn("target_compile_definitions(startUpAsm PRIVATE NX_STARTUP_SAFE_ISR)", cmake)

    def test_s32k116_profile_needs_no_workstation_file(self):
        """PLAT-32: the S32K116 device header is checked in (RTD headers from hal_nxp), NX_SVD_DIR is not needed."""
        env = {k: v for k, v in os.environ.items() if k != "NX_SVD_DIR"}
        run = subprocess.run([sys.executable, str(HERE / "bswgen.py"), str(S32K116_PROJECT), str(self.tmp)],
                             capture_output=True, text=True, env={**env, "PYTHONUTF8": "1"})
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotIn("NX_SVD_DIR", run.stderr)

    def test_s32k116_profile(self):
        bswgen.generate(S32K116_PROJECT, self.tmp)
        self.assertFalse((self.tmp / "3rdparty").exists())            # read from the repository, nothing generated
        derivative = self.read("mcu/mcuDerivative.h")
        self.assertIn("#define WDOG_EWM_IRQn WDOG_IRQn", derivative)
        self.assertIn("#define FTFC_Fault_IRQn FTFC_IRQn", derivative)
        self.assertIn("#define ERM_double_fault_IRQn ERM_IRQn", derivative)
        # PLAT-12: the lean profile has no application CAN (use.can empty); the message buffer vector of CAN0, derived
        # from the device header, is still checked - on a copy of the profile that uses the bus
        text = S32K116_PROJECT.read_text(encoding="utf-8")
        text = text.replace("  board: ../../boards/s32k116evb\n",
                            f"  board: {(REPO / 'boards' / 's32k116evb').as_posix()}\n")
        text = text.replace("  rte: rte.yaml\n", f"  rte: {(S32K116_PROJECT.parent / 'rte.yaml').as_posix()}\n")
        text = text.replace("\nuse:\n", "\nuse:\n  can: all\n")
        text = text.replace("  ranks:\n", "  ranks:\n    - { source: can, name: CAN0, rank: 0 }\n")
        with_can = self.tmp / "with_can"
        with_can.mkdir()
        (with_can / "project.yaml").write_text(text, encoding="utf-8")
        bswgen.generate(with_can / "project.yaml", with_can / "out")
        dev = (with_can / "out" / "bsp" / "can" / "canDeviceConfiguration.h").read_text(encoding="utf-8")
        self.assertIn("#define NX_CAN_MB_VECTORS(X) \\\n"
                      "    X##_RX_TX(CAN0, 0, CAN0_ORed_0_31_MB_IRQHandler, CAN0_ORed_0_31_MB_IRQn)\n", dev)
        vectors = [line.split("/*")[0].strip() for line in self.read("startup/nxIrqVectors.inc").splitlines()
                   if line.startswith("NX_IRQ")]
        self.assertEqual(len(vectors), 32)                                          # ARMv6-M maximum
        self.assertEqual(vectors[22], "NX_IRQ_VECTOR(WDOG_EWM_IRQHandler)")
        self.assertEqual(vectors[18], "NX_IRQ_VECTOR(FTFC_Fault_IRQHandler)")
        self.assertEqual(vectors[5], "NX_IRQ_VECTOR(ERM_double_fault_IRQHandler)")
        # SYS-170: the lean profile wraps every other vector of the S32K116, none of them served by the integration
        wrapped = re.findall(r"X\((\w+), ([01])\)", self.read("interrupts/nxSafeIsrVectors.h"))
        handlers = [v[len("NX_IRQ_VECTOR("):-1] for v in vectors if v.startswith("NX_IRQ_VECTOR(")]
        self.assertEqual([h for h, _ in wrapped], [h for h in dict.fromkeys(handlers) if h not in
                                                  bswgen.SAFETY_IRQ_HANDLERS])
        self.assertNotIn("LPIT0_Ch1_IRQHandler", [h for h, _ in wrapped])     # shared LPIT vector = halt deadman
        self.assertEqual({s for _, s in wrapped}, {"0"})
        self.assertIn("#define NX_ADC_INSTANCE_COUNT 1", self.read("mcu/derivativeFacts.h"))
        memory = self.read("memory/memoryLayout.h")
        self.assertIn("#define NX_MEMORY_APP_RAMFUNCTIONS_SIZE 0x00000020", memory)       # PLAT-12: no RAM function
        self.assertIn("#define NX_MEMORY_APP_HOT_RAM_SIZE 0x00002300", memory)
        self.assertIn("#define NX_MEMORY_APP_ISR_STACK_SIZE 0x00000400", memory)

    def test_header_from_a_register_description(self):
        """PLAT-69: generated_by regdesc2header (RM46L852, decision F3) - the header is generated from the register
        description in mcudb/<family>/registers like the standalone tool does, without any workstation file.
        Only the header branch: bswgen itself serves s32k1xx, the rm4x family generator follows with PLAT-37."""
        rep = nxconfig.config.Reporter()
        file = REPO / "mcudb" / "rm4x" / "RM46L852.yaml"
        d = nxconfig.config._read(rep, "derivative", file, nxconfig.model.Derivative)
        self.assertIsNotNone(d, [str(m) for m in rep.messages])
        self.assertEqual(d.device_header.generated_by, "regdesc2header")
        text, irqs = bswgen.device_header_text(SimpleNamespace(derivative=d))
        regdesc = bswgen._regdesc2header("rm4x")
        self.assertEqual(text, regdesc.render(regdesc.validate(regdesc.load()), guard="RM46L852_REGS_H"))
        self.assertIn("} IOMM_Type;", text)
        self.assertEqual(irqs, {})                          # VIM: requests come from mcudb vim.requests
        # a family without the generator gets a clear error instead of an import failure
        other = d.model_copy(update={"derivative": d.derivative.model_copy(update={"family": "nofamily"})})
        with self.assertRaisesRegex(bswgen.ConfigError, "mcudb/nofamily/tools/regdesc2header.py"):
            bswgen.device_header_text(SimpleNamespace(derivative=other))
        # a broken description is reported as a configuration error
        with mock.patch.object(regdesc, "load", return_value=[{"schema": "other"}]), \
                self.assertRaisesRegex(bswgen.ConfigError, "register description mcudb/rm4x/registers"):
            bswgen.device_header_text(SimpleNamespace(derivative=d))

    def test_svd_generation_is_not_part_of_the_build(self):
        """PLAT-48: generated_by svd2header (PLAT-11) is gone from bswgen: a clear error, no workstation file read."""
        rep = nxconfig.config.Reporter()
        file = REPO / "mcudb" / "rm4x" / "RM46L852.yaml"
        d = nxconfig.config._read(rep, "derivative", file, nxconfig.model.Derivative)
        self.assertIsNotNone(d, [str(m) for m in rep.messages])
        svd = d.model_copy(update={"device_header": d.device_header.model_copy(update={"generated_by": "svd2header"})})
        with self.assertRaisesRegex(bswgen.ConfigError, "svd2header is not supported by the build"):
            bswgen.device_header_text(SimpleNamespace(derivative=svd))


SENSOR_PROJECT = REPO / "tools/nxconfig/test/profiles/projects/testproject_sensors/project.yaml"


def arm_gcc(tool: str) -> str | None:
    name = f"arm-none-eabi-{tool}.exe" if sys.platform == "win32" else f"arm-none-eabi-{tool}"
    env_bin = os.environ.get("NX_ARM_GCC_BIN")
    return str(Path(env_bin) / name) if env_bin and (Path(env_bin) / name).exists() else shutil.which(name)


class BswGenSensorTest(unittest.TestCase):
    """IO-18: generated integer sensor conversion bsp/adc/analogSensorConfiguration.h."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def header(self, project=None) -> str:
        project = project or PROJECT_FILE          # PLAT-62: at call time (on_eval_board swaps the constant)
        bswgen.generate(project, self.tmp / "gen")
        return (self.tmp / "gen/bsp/adc/analogSensorConfiguration.h").read_text(encoding="utf-8")

    @on_eval_board
    def test_eval_board_channels(self):
        header = self.header()
        # fitted and used channels only; MOTOR_NTC is not fitted (R148, user 2026-09-16), phase currents and BEMF are
        # fitted since 2026-09-21 (user)
        self.assertIn("#define NX_ADC_SENSORS(X) X(SENSE_12VGATE) X(SIG_12VBAT_CURRENT) X(SENSE_TSVOLTAGE) "
                      "X(SENSE_TSCURRENT) X(ADC_CURRENT_U) X(ADC_CURRENT_V) X(ADC_CURRENT_W) X(BEMF_W) X(BEMF_U) "
                      "X(BEMF_V) X(CURRENTSENSE_KL30B) X(PILOT_ANALOG) X(PROXY_ANALOG)\n", header)
        self.assertNotIn("MOTOR_NTC", header)
        self.assertIn(" * ADC_CURRENT_U: shunt_amplifier -> mA", header)
        self.assertIsNone(re.search(r"\b(float|double)\b", header))
        self.assertIn(" * SENSE_TSCURRENT: shunt_amplifier -> mA\n *   (U - 2506 mV) / (100 V/V * 500 uOhm)", header)
        self.assertIn("quality OK for raw 10..4094\n *   value range -50120..31800 mA", header)
        self.assertIn(" * PROXY_ANALOG: pullup -> ohm (UNCONFIRMED: source missing or verify)", header)
        self.assertIn(" * CURRENTSENSE_KL30B: highside_is -> mA\n *   2180 * U / 1200 ohm", header)
        self.assertIn("#define NX_ADC_SENSOR_QUALITY_RANGE ((uint8_t)32U)", header)

    @on_eval_board
    def test_existing_scaling_is_unchanged(self):
        """adc.mv keeps its generat: the sensor blocks do not touch analogInputScaleConfiguration.h (IO-18)."""
        bswgen.generate(PROJECT_FILE, self.tmp / "gen")
        scales = re.findall(r"&scaleLinear<(\d+)U, (\d+)U, (-?\d+)>, // Ai_(\w+)",
                            (self.tmp / "gen/bsp/adc/analogInputScaleConfiguration.h").read_text(encoding="utf-8"))
        expected = {"MOTOR_NTC": (4096, 4095), "SENSE_12VGATE": (569344, 159705), "SIG_12VBAT_CURRENT": (4096, 4095),
                    "SENSE_TSVOLTAGE": (536576, 45045), "SENSE_TSCURRENT": (4096, 4095),
                    "ADC_CURRENT_U": (4096, 4095), "BEMF_U": (536576, 45045), "CURRENTSENSE_KL30B": (4096, 4095),
                    "PILOT_ANALOG": (8192, 2145), "PROXY_ANALOG": (4096, 4095)}
        found = {name: (int(num), int(den)) for num, den, _, name in scales}
        for name, value in expected.items():
            self.assertEqual(found[name], value, name)

    def test_synthetic_profile(self):
        header = self.header(SENSOR_PROJECT)
        self.assertIn("#define NX_ADC_SENSOR_COUNT 7", header)
        self.assertNotIn("SPARE", header)
        self.assertIn("return nxAdcSensorTable(points, 34U, board, quality);", header)     # NTC -40..125 degC
        self.assertIn("return nxAdcSensorTable(points, 4U, board, quality);", header)      # level table
        self.assertIn("(INT64_C(-11) * x) + INT64_C(50505)", header)                        # pull-up denominator
        self.assertIn("int64_t const x = (raw > 4094U) ? INT64_C(4094) : (int64_t)raw;", header)  # open NTC input
        self.assertIsNone(re.search(r"\b(float|double)\b", header))


class BswGenSensorHostTest(unittest.TestCase):
    """IO-18: every raw code of the generated conversion against the Python reference (tools/nxconfig/sensor.py),
    on the host (zig c++ and zig cc as C99), for the eval board demo and the synthetic S32K116 profile, with built-in
    faults; the target compiler builds it for Cortex-M0+ without FPU and must not pull in floating point."""

    SOURCE = HERE / "test" / "sensor_host" / "adc_sensor_host_test.cpp"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def generate(self, project: Path, name: str) -> Path:
        gen = self.tmp / name
        bswgen.generate(project, gen)
        config = nxconfig.load(project)
        lines = ["// Generated by test_bswgen.py (BswGenSensorHostTest) from tools/nxconfig/sensor.py evaluate()",
                 "#pragma once", "#include <cstdint>",
                 f"#define NX_TEST_EXPECTED_CHANNELS {len(config.adc_sensors)}U"]
        for plan in config.adc_sensors.values():
            ident = bswgen.identifier(plan.name)
            raws = list(range(plan.max_raw + 1)) + [plan.max_raw + 1, 0xFFFF]
            rows = ", ".join("{%d, %d, %d}" % (raw, *nxsensor.evaluate(plan, raw)) for raw in raws)
            lines += [f"static int64_t const NX_TEST_EXPECTED_{ident}[][3] = {{{rows}}};",
                      f"static unsigned const NX_TEST_EXPECTED_{ident}_COUNT = {len(raws)}U;"]
        (gen / "sensorExpected.h").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return gen

    def build_and_run(self, gen: Path, header_dir: Path | None = None):
        exe = self.tmp / f"{gen.name}_{header_dir.name if header_dir else 'gen'}.exe"
        cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
               "-Wconversion", "-Wno-sign-conversion", *([f"-I{header_dir}"] if header_dir else []), f"-I{gen}",
               str(self.SOURCE), "-o", str(exe)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        return subprocess.run([str(exe)], capture_output=True, text=True)

    def run_profile(self, project: Path, name: str) -> Path:
        gen = self.generate(project, name)
        r = self.build_and_run(gen)
        print("\n" + r.stdout.strip(), file=sys.stderr)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:])
        self.assertRegex(r.stdout, r"\d+ checks, 0 failures")
        self.c99_and_target(gen)
        return gen

    def c99_and_target(self, gen: Path):
        """The header is C as well (zig cc -std=c99) and builds for Cortex-M0+ without floating point helpers."""
        src = self.tmp / f"{gen.name}_use.c"
        src.write_text('#include "bsp/adc/analogSensorConfiguration.h"\n'
                       "#define USE(name) int32_t use_##name(uint16_t raw, uint8_t* q) { return nxAdcPhys_##name(raw, q); }\n"
                       "NX_ADC_SENSORS(USE)\n", encoding="utf-8")
        flags = ["-O2", "-Wall", "-Wextra", "-Wconversion", "-Wno-sign-conversion", "-Werror", "-Wmissing-prototypes",
                 "-Wno-missing-prototypes", f"-I{gen}", "-c", str(src)]
        r = subprocess.run([sys.executable, "-m", "ziglang", "cc", "-std=c99", *flags, "-o",
                            str(self.tmp / f"{gen.name}_use.o")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        gcc, nm = arm_gcc("gcc"), arm_gcc("nm")
        if not gcc or not nm:
            print("\narm-none-eabi-gcc not found: target compiler check skipped", file=sys.stderr)
            return
        obj = self.tmp / f"{gen.name}_m0plus.o"
        r = subprocess.run([gcc, "-std=c99", "-mcpu=cortex-m0plus", "-mthumb", "-mfloat-abi=soft", "-Wdouble-promotion",
                            "-Wfloat-equal", *flags, "-o", str(obj)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        undefined = subprocess.run([nm, "-u", str(obj)], capture_output=True, text=True).stdout
        self.assertNotRegex(undefined, r"__aeabi_[fd]|__(add|sub|mul|div|fix|float)[sd]f")

    def test_eval_board_demo(self):
        self.run_profile(PROJECT_FILE, "demo")

    def test_synthetic_profile(self):
        self.run_profile(SENSOR_PROJECT, "sensors")

    def test_built_in_faults_are_detected(self):
        gen = self.generate(SENSOR_PROJECT, "mutants")
        header = (gen / "bsp/adc/analogSensorConfiguration.h").read_text(encoding="utf-8")
        mutants = {
            "rounding truncates": ("(num >= 0) ? ((num + (den / 2)) / den) : -(((-num) + (den / 2)) / den)",
                                   "num / den"),
            "table end keeps quality OK": ("    *quality = NX_ADC_SENSOR_QUALITY_RANGE;\n    return points[count - 1U][1];",
                                           "    return points[count - 1U][1];"),
            "amplifier range one code too wide": ("(raw <= 3970U)", "(raw <= 3971U)"),
            "interpolation without the segment start": ("return (int32_t)(y0 + nxAdcSensorDivRound(",
                                                        "return (int32_t)(nxAdcSensorDivRound("),
            "open pull-up not clamped": ("(raw > 4094U) ? INT64_C(4094)", "(raw > 4095U) ? INT64_C(4095)"),
        }
        for index, (what, (old, new)) in enumerate(mutants.items()):
            with self.subTest(mutant=what):
                self.assertGreaterEqual(header.count(old), 1, what)
                patched = self.tmp / f"mutant{index}"
                (patched / "bsp/adc").mkdir(parents=True)
                (patched / "bsp/adc/analogSensorConfiguration.h").write_text(header.replace(old, new, 1),
                                                                            encoding="utf-8")
                r = self.build_and_run(gen, patched)
                self.assertNotEqual(r.returncode, 0, f"mutant not detected: {what}\n{r.stdout[-500:]}")


SPI_PROJECT = HERE / "test" / "profiles" / "projects" / "testproject_spi" / "project.yaml"
SPI_PCR_RE = re.compile(r"^PCR (PT[A-E]\d{1,2}) 0x([0-9A-F]{8})$")
PORT_PCR_MUX_SHIFT, PORT_PCR_MUX_MASK = 8, 0x700     # S32K1xx RM PORT_PCRn[MUX], NXP S32K148_PORT.h


class BswGenSpiPinsHostTest(unittest.TestCase):
    """ARCH-38: every pin of every SPI bus the project uses is muxed to its LPSPI function, whatever driver uses it.

    Board test 2026-09-18 (IO-25): PTB0..PTB3 of the TMC6200 were never muxed - StaticBsp::hwInit muxed outputs,
    inputs, PWM and ADC, the SBC pins were muxed by SbcEarlyStart (only after a watchdog test reset) and by the
    bootloader, the gate driver bus by nobody. The LPSPI ran without pins, reported no error and read 0; no host test
    could see it, because the register model of the driver has no pin multiplexing.

    Here the function of the board port (lifecycle/SpiBusPins.h) runs with the generat of a project, the real OpenBSW
    Io driver and a RAM model of PORTA..PORTE. The expectation is read from board.yaml, the project's use.spi and the
    pin multiplexing table of mcudb - not from the generator: each of the four pins of each used bus carries
    PORT_PCR.MUX of <instance>_SCK/SIN/SOUT/PCS<index> on exactly that pin, and no other PCR changes. Profiles:
    testproject_spi (own test profile: a gate driver with its module on LPSPI1, a device **without a driver** in the
    project on a second bus LPSPI0 with chip select 1, and a board bus the project does not use), the eval board demo
    (three buses, among them MOT1_GD_SPI on PTB0..PTB3) and the S32K116 profile without SPI. Mutants: a pin missing
    in the generat, the board port without the function body (the state before ARCH-38).
    """

    OPENBSW = REPO / "external" / "openbsw"
    HOST = HERE / "test" / "spi_host"
    LIFECYCLE = PORT / "main" / "src" / "lifecycle"

    setUp, tearDown = BswGenAdcTest.setUp, BswGenAdcTest.tearDown

    @staticmethod
    def expected(project_file: Path) -> dict[str, int]:
        """Pin -> PORT_PCR.MUX of its LPSPI signal, for every pin of every used SPI bus (board order)."""
        project = yaml.safe_load(project_file.read_text(encoding="utf-8"))
        board = yaml.safe_load((project_file.parent / project["project"]["board"] / "board.yaml")
                               .read_text(encoding="utf-8"))
        mcu, package = board["board"]["mcu"], board["board"]["package"]
        table = yaml.safe_load((REPO / "mcudb" / "s32k1xx" / f"{mcu}_{package}.pinmux.yaml")
                               .read_text(encoding="utf-8"))["signals"]
        use = project.get("use", {}).get("spi", [])
        buses = [b for b in board.get("spi", []) if use == "all" or b["name"] in use]
        pins = {}
        for bus in buses:
            for role in ("sck", "sin", "sout", "pcs"):
                signal = f"{bus['instance']}_{'PCS' + str(bus['pcs']['index']) if role == 'pcs' else role.upper()}"
                pin = bus[role]["pin"]
                pins[pin] = table[signal][pin]
        return pins

    def build_and_run(self, gen: Path, first=()) -> subprocess.CompletedProcess:
        """Compiles the host test with the generat `gen` (directories in `first` searched before) and runs it."""
        includes = [*first, self.HOST / "stubs", self.LIFECYCLE, gen,
                    self.OPENBSW / "platforms/s32k1xx/bsp/bspIo/include", self.OPENBSW / "libs/bsw/bsp/include"]
        system = [self.OPENBSW / "platforms/s32k1xx/bsp/bspMcu/include/3rdparty/nxp"]
        exe = self.tmp / f"spi_pins_{len(list(self.tmp.glob('*.exe')))}.exe"
        cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
               *[f"-I{i}" for i in includes], *[f"-isystem{i}" for i in system],
               str(self.HOST / "spi_bus_pins_host_test.cpp"),
               str(self.OPENBSW / "platforms/s32k1xx/bsp/bspIo/src/io/Io.cpp"), "-o", str(exe)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        return subprocess.run([str(exe)], capture_output=True, text=True)

    @staticmethod
    def muxed(output: str) -> dict[str, int]:
        """Pin -> PORT_PCR.MUX of every PCR the run changed."""
        pins = {}
        for line in output.splitlines():
            m = SPI_PCR_RE.match(line)
            if m:
                pins[m.group(1)] = (int(m.group(2), 16) & PORT_PCR_MUX_MASK) >> PORT_PCR_MUX_SHIFT
        return pins

    def generate(self, project_file: Path, name: str) -> Path:
        gen = self.tmp / name
        bswgen.generate(project_file, gen)
        return gen

    @on_eval_board
    def test_every_pin_of_every_used_bus_is_muxed(self):
        for project_file, buses in ((SPI_PROJECT, 2), (PROJECT_FILE, 3), (S32K116_PROJECT, 0)):
            with self.subTest(project=project_file.parent.name):
                expected = self.expected(project_file)
                self.assertEqual(len(expected), 4 * buses)
                r = self.build_and_run(self.generate(project_file, project_file.parent.name))
                self.assertEqual(r.returncode, 0, r.stdout[-2000:])
                print(f"\n{project_file.parent.name}: {r.stdout.strip()}", file=sys.stderr)
                self.assertEqual(self.muxed(r.stdout), expected)
                self.assertIn(f"LISTED {4 * buses} CHANGED {4 * buses}", r.stdout)

    def test_the_test_profile_covers_the_gap(self):
        """The profile has what the eval board alone would not show; the unused bus and its device stay untouched."""
        expected = self.expected(SPI_PROJECT)
        # MEM_SPI: device without a driver module, second LPSPI, chip select 1 (LPSPI0_PCS1 on PTB5)
        self.assertEqual({p: expected[p] for p in ("PTB2", "PTB3", "PTB4", "PTB5")},
                         {"PTB2": 3, "PTB3": 3, "PTB4": 3, "PTB5": 3})
        config, _ = nxconfig.check(SPI_PROJECT)
        self.assertIsNotNone(config)
        self.assertEqual({d.name: d.module for d in config.integration.devices}, {"GD": "gdTmc6200"})
        # SENS_SPI is on the board, not used: its pins are neither in the Io table nor muxed
        for pin in ("PTC15", "PTC0", "PTC1", "PTC14"):
            self.assertNotIn(pin, expected)

    def test_mutants_are_detected(self):
        gen = self.generate(SPI_PROJECT, "gen")
        header = (gen / "bsp/spi/spiBusConfiguration.h").read_text(encoding="utf-8")
        helper = (self.LIFECYCLE / "SpiBusPins.h").read_text(encoding="utf-8")
        mutants = {
            # the chip select of the bus without a driver is missing in the generat
            "pin missing in the generat": ("bsp/spi/spiBusConfiguration.h", header,
                                           " X(MEM_SPI_PCS)\n#define NX_SPI_BUS_PIN_COUNT", "\n#define NX_SPI_BUS_PIN_COUNT"),
            # the board port as before ARCH-38: nobody muxes the SPI pins
            "board port muxes nothing": ("SpiBusPins.h", helper, "    NX_SPI_BUS_PINS(SPI_BUS_PIN_MUX)\n", ""),
        }
        expected = self.expected(SPI_PROJECT)
        for index, (what, (rel, text, old, new)) in enumerate(mutants.items()):
            with self.subTest(mutant=what):
                self.assertEqual(text.replace("\r\n", "\n").count(old), 1, what)
                patched = self.tmp / f"mutant{index}"
                (patched / rel).parent.mkdir(parents=True, exist_ok=True)
                (patched / rel).write_text(text.replace("\r\n", "\n").replace(old, new), encoding="utf-8")
                r = self.build_and_run(gen, first=(patched,))
                self.assertEqual(r.returncode, 0, r.stdout[-2000:])
                self.assertNotEqual(self.muxed(r.stdout), expected, f"mutant not detected: {what}")

    def test_the_board_port_calls_it_before_any_system(self):
        """StaticBsp::hwInit (main(), before app_main and every lifecycle component) runs the function first."""
        source = (self.LIFECYCLE / "StaticBsp.cpp").read_text(encoding="utf-8")
        body = source[source.index("void StaticBsp::hwInit()"):]
        body = body[:body.index("\n}")]
        self.assertIn('#include "SpiBusPins.h"', source)
        self.assertLess(body.index("::platform::muxSpiBusPins();"), body.index("_output.init(0);"))
        main = (PORT / "main" / "src" / "main.cpp").read_text(encoding="utf-8")
        main = main[main.index("\nint main()"):]
        self.assertLess(main.index("::platform::staticBsp.init();"), main.index("app_main();"))
        # no driver adapter muxes pins of its own any more (one rule instead of single solutions)
        integration = (PORT / "main" / "src" / "integration" / "Integration.cpp").read_text(encoding="utf-8")
        self.assertNotIn("setDefaultConfiguration", integration)


SBC_HOST_MCU_H = """\
// Host stub of mcu/mcu.h for the SBC SPI test (ARCH-40): real PORT, GPIO and LPSPI register layouts of the NXP
// headers; the ports and the three LPSPI instances are RAM models, so the test sees which instance SPI_CONFIG names.
#pragma once

#include <cstdint>

#define __IO volatile
#define __I  volatile const
#define __O  volatile
#include "S32K148_GPIO.h"
#include "S32K148_LPSPI.h"
#include "S32K148_PORT.h"

extern GPIO_Type fakeGpio[5];
extern PORT_Type fakePort[5];
extern LPSPI_Type fakeLpspi[3];
#define GPIO_BASE_PTRS {&fakeGpio[0], &fakeGpio[1], &fakeGpio[2], &fakeGpio[3], &fakeGpio[4]}
#define PORT_BASE_PTRS {&fakePort[0], &fakePort[1], &fakePort[2], &fakePort[3], &fakePort[4]}
#undef IP_LPSPI0
#undef IP_LPSPI1
#undef IP_LPSPI2
#define IP_LPSPI0 (&fakeLpspi[0])
#define IP_LPSPI1 (&fakeLpspi[1])
#define IP_LPSPI2 (&fakeLpspi[2])
"""

SBC_HOST_MAIN = """\
// ARCH-40: the SBC's SPI master configuration (bsp/sbc/sbcConfiguration.h) and the early pin multiplexing of its bus
// (sbc/SbcSpiPins.h, SbcEarlyStart) against the generat of a project. Prints the LPSPI instance SPI_CONFIG points
// to, its functional clock and chip select, and every PCR the pin function changed (all PCRs start at 0).
#include "SbcSpiPins.h"
#include "mcu/mcu.h"

#include <cstdio>

GPIO_Type fakeGpio[5]   = {};
PORT_Type fakePort[5]   = {};
LPSPI_Type fakeLpspi[3] = {};

int main()
{
    ::systems::muxSbcSpiPins();

    ::bsp::spi::LpspiConfig const& spi = ::bsp::sbc::SPI_CONFIG;
    int instance                       = -1;
    for (int i = 0; i < 3; ++i)
    {
        if (spi.base == &fakeLpspi[i])
        {
            instance = i;
        }
    }
    std::printf("SPI LPSPI%d CLOCK %lu PCS %u BAUD %lu FRAME %u CPOL %d CPHA %d\\n", instance,
                static_cast<unsigned long>(spi.functionalClockHz), static_cast<unsigned>(spi.pcs),
                static_cast<unsigned long>(spi.baudrateHz), static_cast<unsigned>(spi.frameBits),
                spi.cpol ? 1 : 0, spi.cpha ? 1 : 0);
    char const portNames[] = "ABCDE";
    for (unsigned port = 0U; port < 5U; ++port)
    {
        for (unsigned pin = 0U; pin < PORT_PCR_COUNT; ++pin)
        {
            uint32_t const pcr = fakePort[port].PCR[pin];
            if (pcr != 0U)
            {
                std::printf("PCR PT%c%u 0x%08lX\\n", portNames[port], pin, static_cast<unsigned long>(pcr));
            }
        }
    }
    return 0;
}
"""
SBC_SPI_RE = re.compile(r"^SPI LPSPI(-?\d+) CLOCK (\d+) PCS (\d+) BAUD (\d+) FRAME (\d+) CPOL (\d) CPHA (\d)$", re.M)


class BswGenSbcSpiHostTest(unittest.TestCase):
    """ARCH-40: the SBC's SPI bus - instance, chip select, functional clock and the pins SbcEarlyStart muxes - comes
    from the project, not from the port code.

    Before, bsp/sbc/sbcConfiguration.h named IP_LPSPI1, NX_CLOCK_LPSPI1_HZ and PCS0 and SbcEarlyStart the board names
    SBC_SPI_SCK/SIN/SOUT/PCS: facts of the eval board in the family port. Now both take the bus of the sbcUja113x
    device from the generat (integration/sbcUja113xConfiguration.h, bsp/spi/spiBusConfiguration.h,
    bsp/clock/boardClock.h). Here the real header and the real pin function run on the host with the real OpenBSW Io
    driver and a RAM model of PORTA..PORTE and LPSPI0..2. The expectation is read from board.yaml (bus of the SBC
    device), the pin multiplexing table of mcudb and the clock plan of nxconfig. Profiles: an own test profile
    (testboard_spi with an SBC named SYSTEM_BASIS on bus SYS_SPI, LPSPI0, chip select 1, PTB2..PTB5, its clock from
    SOSC (8 MHz) instead of FIRCDIV2 (48 MHz), and a second bus of a gate driver on LPSPI1 whose pins must stay
    untouched) and the eval board demo (LPSPI1, PCS0, PTD0/PTD1/PTE0/PTE1, 48 MHz, unchanged). Mutants: the header
    as before ARCH-40 (IP_LPSPI1 fixed), the early mux of every SPI bus instead of the SBC's.
    """

    OPENBSW = REPO / "external" / "openbsw"
    SBC_DIR = PORT / "main" / "src" / "sbc"
    CONFIG_H = PORT / "bspConfiguration" / "include" / "bsp" / "sbc" / "sbcConfiguration.h"

    setUp, tearDown = BswGenAdcTest.setUp, BswGenAdcTest.tearDown

    def profile(self) -> Path:
        """Own test profile: testboard_spi with an SBC on its second bus, the LPSPI0 clock chosen from SOSC."""
        board = yaml.safe_load((HERE / "test/profiles/boards/testboard_spi/board.yaml").read_text(encoding="utf-8"))
        board["board"]["name"] = "testboard_sbc"
        board["devices"][1] = {"name": "SYSTEM_BASIS", "part": "UJA1131HW/FD/5V/4Y", "module": "sbcUja113x"}
        board["spi"][1].update(name="SYS_SPI", device="SYSTEM_BASIS")
        project = yaml.safe_load(SPI_PROJECT.read_text(encoding="utf-8"))
        project["project"] = {"name": "testproject_sbc", "board": "../../boards/testboard_sbc"}
        project["use"]["spi"] = ["GD_SPI", "SYS_SPI"]
        project["peripheral_clocks"] = {"LPSPI0": {"select": "SOSC", "divider": 2}}
        for rel, doc in (("boards/testboard_sbc/board.yaml", board), ("projects/testproject_sbc/project.yaml", project)):
            (self.tmp / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.tmp / rel).write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        return self.tmp / "projects/testproject_sbc/project.yaml"

    @staticmethod
    def expected(project_file: Path) -> dict:
        """Instance, clock and chip select of the SBC's bus and pin -> PORT_PCR.MUX of its four pins."""
        config, messages = nxconfig.check(project_file)
        assert config is not None, messages
        [sbc] = [d for d in config.integration.devices if d.module == "sbcUja113x"]
        project = yaml.safe_load(project_file.read_text(encoding="utf-8"))
        board = yaml.safe_load((project_file.parent / project["project"]["board"] / "board.yaml")
                               .read_text(encoding="utf-8"))
        [bus] = [b for b in board["spi"] if b.get("device") == sbc.name]
        table = yaml.safe_load((REPO / "mcudb" / "s32k1xx" / f"{board['board']['mcu']}_{board['board']['package']}"
                                ".pinmux.yaml").read_text(encoding="utf-8"))["signals"]
        pins = {}
        for role in ("sck", "sin", "sout", "pcs"):
            signal = f"{bus['instance']}_{'PCS' + str(bus['pcs']['index']) if role == 'pcs' else role.upper()}"
            pins[bus[role]["pin"]] = table[signal][bus[role]["pin"]]
        return {"instance": int(bus["instance"][-1]), "clock": int(config.clock.peripheral_hz[bus["instance"]]),
                "pcs": bus["pcs"]["index"], "pins": pins}

    def build_and_run(self, gen: Path, first=()) -> str:
        stubs = self.tmp / "stubs"
        (stubs / "mcu").mkdir(parents=True, exist_ok=True)
        (stubs / "mcu" / "mcu.h").write_text(SBC_HOST_MCU_H, encoding="utf-8")
        main = self.tmp / "sbc_spi_host_test.cpp"
        main.write_text(SBC_HOST_MAIN, encoding="utf-8")
        includes = [*first, stubs, self.SBC_DIR, PORT / "bspConfiguration" / "include", gen,
                    REPO / "libs/bsw/sbcUja113x/include", REPO / "libs/bsp/bspSpi/include",
                    REPO / "platforms/s32k1xx/bsp/bspLpspi/include",
                    self.OPENBSW / "platforms/s32k1xx/bsp/bspIo/include", self.OPENBSW / "libs/bsw/bsp/include"]
        system = [self.OPENBSW / "platforms/s32k1xx/bsp/bspMcu/include/3rdparty/nxp"]
        exe = self.tmp / f"sbc_spi_{len(list(self.tmp.glob('*.exe')))}.exe"
        cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
               *[f"-I{i}" for i in includes], *[f"-isystem{i}" for i in system], str(main),
               str(self.OPENBSW / "platforms/s32k1xx/bsp/bspIo/src/io/Io.cpp"), "-o", str(exe)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        run = subprocess.run([str(exe)], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout[-2000:])
        return run.stdout

    @staticmethod
    def observed(output: str) -> dict:
        m = SBC_SPI_RE.search(output)
        assert m, output
        return {"instance": int(m.group(1)), "clock": int(m.group(2)), "pcs": int(m.group(3)),
                "pins": BswGenSpiPinsHostTest.muxed(output)}

    @on_eval_board
    def test_sbc_bus_comes_from_the_project(self):
        own = self.profile()
        for project_file in (own, PROJECT_FILE):
            with self.subTest(project=project_file.parent.name):
                expected = self.expected(project_file)
                gen = self.tmp / f"gen_{project_file.parent.name}"
                bswgen.generate(project_file, gen)
                output = self.build_and_run(gen)
                print(f"\n{project_file.parent.name}: {output.strip()}", file=sys.stderr)
                self.assertEqual(self.observed(output), expected)
                # the SBC's own protocol values stay those of the datasheet, whatever bus it is on
                self.assertIn("BAUD 500000 FRAME 16 CPOL 0 CPHA 1", output)
        # the profile really differs from the eval board in every value the port code took from it before
        own_expected, eval_expected = self.expected(own), self.expected(PROJECT_FILE)
        for key in ("instance", "clock", "pcs"):
            self.assertNotEqual(own_expected[key], eval_expected[key], key)
        self.assertFalse(set(own_expected["pins"]) & set(eval_expected["pins"]))
        self.assertEqual(eval_expected, {"instance": 1, "clock": 48_000_000, "pcs": 0,
                                         "pins": {"PTD0": 3, "PTD1": 3, "PTE0": 5, "PTE1": 5}})

    def test_mutants_are_detected(self):
        project_file = self.profile()
        gen = self.tmp / "gen"
        bswgen.generate(project_file, gen)
        expected = self.expected(project_file)
        config_h = self.CONFIG_H.read_text(encoding="utf-8").replace("\r\n", "\n")
        pins_h = (self.SBC_DIR / "SbcSpiPins.h").read_text(encoding="utf-8").replace("\r\n", "\n")
        mutants = {
            # sbcConfiguration.h as before ARCH-40: instance, clock and chip select of the eval board
            "fixed LPSPI1": ("bsp/sbc/sbcConfiguration.h", config_h,
                             "    BSP_SBC_CONCAT(IP_, BSP_SBC_SPI(INSTANCE)),\n"
                             "    BSP_SBC_CONCAT(BSP_SBC_CONCAT(NX_CLOCK_, BSP_SBC_SPI(INSTANCE)), _HZ),\n",
                             "    IP_LPSPI1,\n    NX_CLOCK_LPSPI1_HZ,\n"),
            "fixed chip select": ("bsp/sbc/sbcConfiguration.h", config_h,
                                  "static_cast<uint8_t>(BSP_SBC_SPI(PCS)),", "0U,"),
            # the early start muxes every used SPI bus instead of the SBC's (touches the gate driver bus)
            "every bus muxed": ("SbcSpiPins.h", pins_h,
                                "BSP_SBC_CONCAT(NX_SPI_BUS_PINS_, BSP_SBC_SPI(BUS))(SBC_SPI_PIN_MUX)",
                                "NX_SPI_BUS_PINS(SBC_SPI_PIN_MUX)"),
        }
        for index, (what, (rel, text, old, new)) in enumerate(mutants.items()):
            with self.subTest(mutant=what):
                self.assertEqual(text.count(old), 1, what)
                patched = self.tmp / f"mutant{index}"
                (patched / rel).parent.mkdir(parents=True, exist_ok=True)
                (patched / rel).write_text(text.replace(old, new), encoding="utf-8")
                self.assertNotEqual(self.observed(self.build_and_run(gen, first=(patched,))), expected,
                                    f"mutant not detected: {what}")

    def test_port_code_names_no_board_bus(self):
        """No eval board fact left in the SBC port code: no LPSPI instance, clock or SBC_SPI pin name."""
        for path in (self.CONFIG_H, self.SBC_DIR / "SbcEarlyStart.cpp", self.SBC_DIR / "SbcSpiPins.h"):
            code = re.sub(r"/\*.*?\*/|//[^\n]*", "", path.read_text(encoding="utf-8"), flags=re.S)
            with self.subTest(file=path.name):
                self.assertNotRegex(code, r"LPSPI\d|SBC_SPI_(SCK|SIN|SOUT|PCS)|PT[A-E]\d")
        early = (self.SBC_DIR / "SbcEarlyStart.cpp").read_text(encoding="utf-8")
        self.assertIn("muxSbcSpiPins();", early)

    @on_eval_board
    def test_no_sbc_and_generated_header(self):
        """The module header exists in every project (the port includes it only with an SBC); its content."""
        gen = self.tmp / "gen_k116"
        bswgen.generate(S32K116_PROJECT, gen)
        self.assertIn("#define NX_MODULE_SBCUJA113X_INSTANCES(X)\n",
                      (gen / "integration" / "sbcUja113xConfiguration.h").read_text(encoding="utf-8"))
        gen = self.tmp / "gen_demo"
        bswgen.generate(PROJECT_FILE, gen)
        header = (gen / "integration" / "sbcUja113xConfiguration.h").read_text(encoding="utf-8")
        for line in ("#define NX_MODULE_SBCUJA113X_INSTANCES(X) X(SBC)", "#define NX_DEVICE_SBC_SPI_COUNT 1",
                     "#define NX_DEVICE_SBC_SPI_BUS SBC_SPI", "#define NX_DEVICE_SBC_SPI_INSTANCE LPSPI1",
                     "#define NX_DEVICE_SBC_SPI_PCS 0U"):
            self.assertIn(line + "\n", header)
        self.assertNotIn("CAN_WAKEUP_FRAME", header)                # COM-21: the demo has no wake-up frame


CAN_WAKEUP_MAIN = """\
// COM-21: the generated initializer against the driver's sbc::CanWakeupFrame and its own validity check.
#include "integration/sbcUja113xConfiguration.h"
#include "sbc/Uja113x.h"

#include <cstdio>

static ::sbc::CanWakeupFrame const FRAME = NX_SBC_CAN_WAKEUP_FRAME_INIT;

int main()
{
    std::printf("FRAME %lu %lx %lx %d %d %u %02x%02x%02x%02x%02x%02x%02x%02x %d VALID %d\\n",
                static_cast<unsigned long>(FRAME.bitrate), static_cast<unsigned long>(FRAME.id),
                static_cast<unsigned long>(FRAME.idCompareMask), FRAME.extendedId ? 1 : 0, FRAME.evaluateData ? 1 : 0,
                static_cast<unsigned>(FRAME.dlc), FRAME.dataMask[0], FRAME.dataMask[1], FRAME.dataMask[2],
                FRAME.dataMask[3], FRAME.dataMask[4], FRAME.dataMask[5], FRAME.dataMask[6], FRAME.dataMask[7],
                FRAME.fdPassive ? 1 : 0, ::sbc::Uja113x::isValidCanWakeupFrame(FRAME) ? 1 : 0);
    return 0;
}
"""


class BswGenSbcCanWakeupFrameTest(unittest.TestCase):
    """COM-21 (diff proposal 2 of COM-07): project.yaml modules.<sbc>.can_wakeup_frame becomes
    NX_SBC_CAN_WAKEUP_FRAME(_INIT) in integration/sbcUja113xConfiguration.h, the bit rate from can.<bus>.bitrate.
    Own profile (not the eval board): testboard_s32k_8mhz (8 MHz crystal, 64 MHz) with a UJA1131HW/FD named
    SYSTEM_BASIS as transceiver of VEH (FlexCAN0, 500 kbit/s) on LPSPI2 PCS0; the initializer is compiled against
    the real driver header and checked by sbc::Uja113x::isValidCanWakeupFrame(). Without the entry the header has
    no wake-up frame (eval board: BswGenSbcSpiHostTest.test_no_sbc_and_generated_header)."""

    setUp, tearDown = BswGenAdcTest.setUp, BswGenAdcTest.tearDown
    PROFILES = HERE.parent / "nxconfig" / "test" / "profiles"

    def profile(self, frame: dict | None) -> Path:
        board = yaml.safe_load((self.PROFILES / "boards/testboard_s32k_8mhz/board.yaml").read_text(encoding="utf-8"))
        project = yaml.safe_load((self.PROFILES / "projects/testproject_s32k_8mhz/project.yaml")
                                 .read_text(encoding="utf-8"))
        board["board"]["name"] = "testboard_pn"
        board["devices"][0] = {"name": "SYSTEM_BASIS", "part": "UJA1131HW/FD/5V/4Y", "module": "sbcUja113x"}
        board["can"][0]["phy"] = "SYSTEM_BASIS"
        board["spi"] = [{"name": "SYS_SPI", "instance": "LPSPI2", "sck": {"pin": "PTC15"}, "sin": {"pin": "PTC0"},
                         "sout": {"pin": "PTC1"}, "pcs": {"pin": "PTC14", "index": 0}, "device": "SYSTEM_BASIS"}]
        project["project"] = {"name": "testproject_pn", "board": "../../boards/testboard_pn"}
        project["use"]["spi"] = ["SYS_SPI"]
        if frame is not None:
            project["modules"] = {"SYSTEM_BASIS": {"can_wakeup_frame": frame}}
        for rel, doc in (("boards/testboard_pn/board.yaml", board), ("projects/testproject_pn/project.yaml", project)):
            (self.tmp / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.tmp / rel).write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        self.bus, self.bitrate = "VEH", project["can"]["VEH"]["bitrate"]
        return self.tmp / "projects/testproject_pn/project.yaml"

    def header(self, frame: dict | None) -> str:
        gen = self.tmp / "gen"
        bswgen.generate(self.profile(frame), gen)
        return (gen / "integration" / "sbcUja113xConfiguration.h").read_text(encoding="utf-8")

    def run_frame(self, header: str) -> str:
        gen = self.tmp / "hdr"
        (gen / "integration").mkdir(parents=True, exist_ok=True)
        (gen / "integration" / "sbcUja113xConfiguration.h").write_text(header, encoding="utf-8")
        main = self.tmp / "can_wakeup_frame.cpp"
        main.write_text(CAN_WAKEUP_MAIN, encoding="utf-8")
        exe = self.tmp / f"can_wakeup_{len(list(self.tmp.glob('*.exe')))}.exe"
        includes = [gen, REPO / "libs/bsw/sbcUja113x/include", REPO / "libs/bsp/bspSpi/include",
                    REPO / "platforms/s32k1xx/bsp/bspLpspi/include"]
        cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra",
               "-Wmissing-field-initializers", "-Werror", *[f"-I{i}" for i in includes], str(main),
               str(REPO / "libs/bsw/sbcUja113x/src/sbc/Uja113x.cpp"), "-o", str(exe)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        run = subprocess.run([str(exe)], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout)
        return run.stdout.strip()

    def test_without_frame_no_macro(self):
        self.assertNotIn("CAN_WAKEUP_FRAME", self.header(None))

    def test_standard_frame(self):
        header = self.header({"id": 0x123})
        self.assertIn("#define NX_SBC_CAN_WAKEUP_FRAME 1\n", header)
        self.assertIn(f"#define NX_SBC_CAN_WAKEUP_FRAME_INIT {{{self.bitrate}UL, 0x123UL, 0x7FFUL, false, false, 0U, "
                      "{0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U}, false}\n", header)
        self.assertIn(f"bit rate of CAN bus {self.bus} (can.{self.bus}.bitrate)", header)
        self.assertEqual(self.run_frame(header), f"FRAME {self.bitrate} 123 7ff 0 0 0 0000000000000000 0 VALID 1")

    def test_extended_frame_with_data(self):
        header = self.header({"id": 0x18DA10F1, "extended": True, "compare_mask": 0x1FFFFF00, "dlc": 2,
                              "data_mask": [0x00, 0x04], "fd_passive": True})
        self.assertEqual(self.run_frame(header),
                         f"FRAME {self.bitrate} 18da10f1 1fffff00 1 1 2 0004000000000000 1 VALID 1")


SBC_PROBE_MAIN = """\
// ARCH-41: compile probe of bsp/sbc/sbcConfiguration.h against the generat of a project. Prints the protocol values
// of SPI_CONFIG; for the error profiles only its #error lines matter.
#include "bsp/sbc/sbcConfiguration.h"

#include <cstdio>

GPIO_Type fakeGpio[5]   = {};
PORT_Type fakePort[5]   = {};
LPSPI_Type fakeLpspi[3] = {};

int main()
{
    ::bsp::spi::LpspiConfig const& spi = ::bsp::sbc::SPI_CONFIG;
    std::printf("BAUD %lu FRAME %u CPOL %d CPHA %d TIMEOUT %lu\\n", static_cast<unsigned long>(spi.baudrateHz),
                static_cast<unsigned>(spi.frameBits), spi.cpol ? 1 : 0, spi.cpha ? 1 : 0,
                static_cast<unsigned long>(spi.timeoutUs));
    return 0;
}
"""
SBC_PROBE_RE = re.compile(r"^BAUD (\d+) FRAME (\d+) CPOL (\d) CPHA (\d) TIMEOUT (\d+)$", re.M)
SBC_PROFILES = HERE / "test" / "profiles" / "projects"
# UJA113x series data sheet Rev. 2 (doc/hardware/uja113x/UJA113X_SER.pdf), independent of driver and port: 7.16 p.90
# (16-bit register access, SCK idles LOW, shifted on the rising and sampled on the falling edge = CPOL 0, CPHA 1),
# Table 91 p.115 (tcy(clk) >= 250 ns).
DS_UJA113X_FRAME_BITS, DS_UJA113X_CPOL, DS_UJA113X_CPHA, DS_UJA113X_MAX_SCK_HZ = 16, 0, 1, 4_000_000


class BswGenSbcConfigurationProbeTest(unittest.TestCase):
    """ARCH-41: the SPI protocol values of the SBC come from the module sbcUja113x, and the #error branches of
    bsp/sbc/sbcConfiguration.h stop the build of a project the port cannot serve.

    Protocol values: the real port header is compiled with the generat of three projects on different buses, clocks
    and chip selects (own profile testproject_sbc of ARCH-40, the eval board demo); the printed rate, frame, clock
    polarity/phase and timeout are the same everywhere and fit the data sheet (not read from the driver header). The
    initializer of SPI_CONFIG names no protocol literal any more (500000, 16U, false/true, 5000 stood there before).

    Error branches (compile probe, the build must fail with exactly the expected message): own test profiles on
    testboard_sbc_errors - testproject_sbc_two (two UJA113x) and testproject_sbc_two_buses (one UJA113x on two used
    SPI buses) - and the S32K116 project without SBC. Mutants: the header without the #if of each branch.

    ARCH-44: nxconfig refuses the first two itself (checks.device_counts, with YAML path); their generat for the
    compile probe is made with that rule switched off (`unchecked`). test_nxconfig_rule_equals_header_rule runs both
    rules against the same profiles: nxconfig refuses a project exactly when the port header does.
    """

    MESSAGES = {"two": "more than one UJA113x in this project",
                "two_buses": "the UJA113x needs exactly one SPI bus",
                "none": "no SBC in this project"}

    setUp, tearDown = BswGenAdcTest.setUp, BswGenAdcTest.tearDown

    @staticmethod
    def unchecked():
        """nxconfig without the SBC count rule of ARCH-44, so that bswgen writes the generat the header must refuse."""
        return mock.patch.object(nxconfig.checks, "device_counts", lambda *args: None)

    def generate(self, project_file: Path) -> Path:
        gen = self.tmp / f"gen_{project_file.parent.name}"
        if not gen.exists():
            with self.unchecked():
                bswgen.generate(project_file, gen)
        return gen

    def compile(self, gen: Path, first=(), run=False) -> subprocess.CompletedProcess:
        stubs = self.tmp / "stubs"
        (stubs / "mcu").mkdir(parents=True, exist_ok=True)
        (stubs / "mcu" / "mcu.h").write_text(SBC_HOST_MCU_H, encoding="utf-8")
        main = self.tmp / "sbc_probe.cpp"
        # error branches: a translation unit with nothing but the header, so every diagnostic is the header's
        main.write_text(SBC_PROBE_MAIN if run else '#include "bsp/sbc/sbcConfiguration.h"\n', encoding="utf-8")
        openbsw = REPO / "external" / "openbsw"
        includes = [*first, stubs, PORT / "bspConfiguration" / "include", gen, REPO / "libs/bsw/sbcUja113x/include",
                    REPO / "libs/bsp/bspSpi/include", REPO / "platforms/s32k1xx/bsp/bspLpspi/include"]
        system = [openbsw / "platforms/s32k1xx/bsp/bspMcu/include/3rdparty/nxp"]
        exe = self.tmp / f"sbc_probe_{len(list(self.tmp.glob('*.exe')))}.exe"
        cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
               *[f"-I{i}" for i in includes], *[f"-isystem{i}" for i in system], str(main),
               *(["-o", str(exe)] if run else ["-c", "-o", str(self.tmp / "sbc_probe.o")])]
        r = subprocess.run(cmd, capture_output=True, text=True)
        # the probe must fail in the header, never in the compiler driver (that would pass every error check)
        self.assertNotIn("zig: error", r.stderr)
        if not run or r.returncode != 0:
            return r
        return subprocess.run([str(exe)], capture_output=True, text=True)

    @staticmethod
    def first_error(result: subprocess.CompletedProcess) -> str:
        errors = re.findall(r"\berror: (.*)", result.stderr)
        return f"{len(errors)} error(s), first {errors[0]}" if errors else "no error"

    def assert_refused(self, result: subprocess.CompletedProcess, branch: str):
        self.assertNotEqual(result.returncode, 0, f"{branch}: the build did not stop")
        # exactly the message of this branch, no second #error and no follow-up error of the header
        errors = re.findall(r"\berror: (.*)", result.stderr)
        self.assertEqual(len(errors), 1, result.stderr[-3000:])
        self.assertTrue(errors[0].startswith(f'"{self.MESSAGES[branch]}'), errors[0])

    def error_profiles(self) -> dict[str, Path]:
        # TOOL-243c: the S32K116EVB profile is private until PLAT-13 - branch "none" only where the tree has it
        return {"two": SBC_PROFILES / "testproject_sbc_two" / "project.yaml",
                "two_buses": SBC_PROFILES / "testproject_sbc_two_buses" / "project.yaml",
                **({"none": S32K116_PROJECT} if S32K116_PROJECT.is_file() else {})}

    def test_error_branches_stop_the_build(self):
        for branch, project_file in self.error_profiles().items():
            with self.subTest(branch=branch):
                with self.unchecked():
                    config, messages = nxconfig.check(project_file)
                self.assertIsNotNone(config, messages)
                self.assertFalse([m for m in messages if m.severity == "error"], messages)
                gen = self.generate(project_file)
                # the profile really is the case it claims to be (counted from the check, not from the generat)
                sbcs = [d for d in config.integration.devices if d.module == "sbcUja113x"]
                self.assertEqual({"two": [2], "two_buses": [1], "none": [0]}[branch], [len(sbcs)])
                if branch == "two_buses":
                    self.assertEqual(len(sbcs[0].spi), 2)
                result = self.compile(gen)
                print(f"\n{project_file.parent.name}: {self.first_error(result)}", file=sys.stderr)
                self.assert_refused(result, branch)

    def test_error_branch_mutants_are_detected(self):
        header = (PORT / "bspConfiguration" / "include" / "bsp" / "sbc" / "sbcConfiguration.h").read_text(
            encoding="utf-8").replace("\r\n", "\n")
        mutants = {"two": ("#elif NX_MODULE_SBCUJA113X_COUNT > 1\n", "#elif 0\n"),
                   "two_buses": ("#if BSP_SBC_SPI(COUNT) != 1\n", "#if 0\n")}
        profiles = self.error_profiles()
        for index, (branch, (old, new)) in enumerate(mutants.items()):
            with self.subTest(mutant=branch):
                self.assertEqual(header.count(old), 1, branch)
                patched = self.tmp / f"mutant{index}"
                (patched / "bsp" / "sbc").mkdir(parents=True, exist_ok=True)
                (patched / "bsp" / "sbc" / "sbcConfiguration.h").write_text(header.replace(old, new),
                                                                             encoding="utf-8")
                result = self.compile(self.generate(profiles[branch]), first=(patched,))
                with self.assertRaises(AssertionError, msg=f"mutant not detected: {branch}"):
                    self.assert_refused(result, branch)
                print(f"\nmutant {branch}: exit {result.returncode}, {self.first_error(result)}", file=sys.stderr)

    def equality_profiles(self) -> dict[str, Path]:
        """Projects with at least one UJA113x (only they compile the port header, PLAT-24): the real ones, the own
        profile of ARCH-40, the two error profiles and variants of testboard_sbc_errors written here."""
        profiles = {"demo": PROJECT_FILE,
                    **({"eval_demo": EVAL_PROJECT_FILE, "charge": EVAL_CHARGE_FILE} if EVAL_PROJECT_FILE.is_file()
                       else {}),
                    "testproject_sbc": BswGenSbcSpiHostTest.profile(self),
                    **{k: v for k, v in self.error_profiles().items() if k != "none"}}
        base = yaml.safe_load((SBC_PROFILES / "testproject_sbc_two" / "project.yaml").read_text(encoding="utf-8"))
        board = SBC_PROFILES.parent / "boards" / "testboard_sbc_errors"
        variants = {"sbc_a_only": ["SBCA_SPI"], "sbc_b_second_bus": ["SBCB2_SPI"],
                    "two_and_two_buses": ["SBCA_SPI", "SBCB_SPI", "SBCB2_SPI"], "all": "all"}
        for name, spi in variants.items():
            project = copy.deepcopy(base)
            project["project"] = {"name": f"sbc_{name}",
                                  "board": Path(os.path.relpath(board, self.tmp / name)).as_posix()}
            project["use"] = {"spi": spi}
            (self.tmp / name).mkdir()
            (self.tmp / name / "project.yaml").write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
            profiles[name] = self.tmp / name / "project.yaml"
        return profiles

    def test_nxconfig_rule_equals_header_rule(self):
        """ARCH-44: nxconfig refuses a project with an SBC exactly when the #error rule of sbcConfiguration.h does."""
        refused = {}
        for name, project_file in self.equality_profiles().items():
            with self.subTest(profile=name):
                with self.unchecked():
                    config, messages = nxconfig.check(project_file)
                # without the rule the profile is valid, so a refusal below is the rule's and nothing else
                self.assertIsNotNone(config, [str(m) for m in messages])
                self.assertTrue([d for d in config.integration.devices if d.module == "sbcUja113x"], name)
                _, messages = nxconfig.check(project_file)
                errors = [m for m in messages if m.severity == "error"]
                result = self.compile(self.generate(project_file))
                header_refuses = result.returncode != 0
                if header_refuses:
                    self.assertRegex(self.first_error(result), r'^1 error\(s\), first "(more than one UJA113x|'
                                     r'the UJA113x needs exactly one SPI bus)')
                refused[name] = (bool(errors), header_refuses)
                print(f"\n{name}: nxconfig {'refuses' if errors else 'accepts'}, header "
                      f"{'refuses' if header_refuses else 'accepts'}"
                      + "".join(f"\n  {m}" for m in errors), file=sys.stderr)
                self.assertEqual(bool(errors), header_refuses, [str(m) for m in errors] + [result.stderr[-2000:]])
                for m in errors:
                    # file, line and YAML path of the project entry that adds the second SBC or bus
                    self.assertEqual((m.file.name, m.yaml_path[:7]), ("project.yaml", "use.spi"), str(m))
                    self.assertIsNotNone(m.line, str(m))
                    line = m.file.read_text(encoding="utf-8").splitlines()[m.line - 1]
                    selection = yaml.safe_load(m.file.read_text(encoding="utf-8"))["use"]["spi"]
                    index = re.fullmatch(r"use\.spi(?:\[(\d+)\])?", m.yaml_path).group(1)
                    self.assertIn("spi:" if index is None else selection[int(index)], line, str(m))
        # both outcomes occur, and every refusal is one of both rules
        self.assertEqual(set(refused.values()), {(True, True), (False, False)}, refused)
        self.assertEqual([k for k, v in refused.items() if v[0]], ["two", "two_buses", "two_and_two_buses", "all"])

    def test_protocol_values_are_module_values(self):
        own = BswGenSbcSpiHostTest.profile(self)
        seen = {}
        for project_file in (own, PROJECT_FILE):
            with self.subTest(project=project_file.parent.name):
                run = self.compile(self.generate(project_file), run=True)
                self.assertEqual(run.returncode, 0, (run.stdout + run.stderr)[-3000:])
                m = SBC_PROBE_RE.search(run.stdout)
                self.assertIsNotNone(m, run.stdout)
                baud, frame, cpol, cpha, timeout = (int(g) for g in m.groups())
                print(f"\n{project_file.parent.name}: {m.group(0)}", file=sys.stderr)
                self.assertEqual((frame, cpol, cpha), (DS_UJA113X_FRAME_BITS, DS_UJA113X_CPOL, DS_UJA113X_CPHA))
                self.assertLessEqual(baud, DS_UJA113X_MAX_SCK_HZ)
                # unchanged by ARCH-41: 500 kHz (board-verified 2026-09-14) and 5 ms per frame
                self.assertEqual((baud, timeout), (500_000, 5000))
                seen[project_file.parent.name] = m.group(0)
        self.assertEqual(len(set(seen.values())), 1, seen)

    def test_port_header_names_no_protocol_value(self):
        code = re.sub(r"/\*.*?\*/|//[^\n]*", "",
                      (PORT / "bspConfiguration" / "include" / "bsp" / "sbc" / "sbcConfiguration.h")
                      .read_text(encoding="utf-8"), flags=re.S)
        m = re.search(r"LpspiConfig const SPI_CONFIG = \{(.*?)\};", code, flags=re.S)
        self.assertIsNotNone(m)
        entries = [e.strip() for e in m.group(1).split(",\n") if e.strip()]
        self.assertEqual(len(entries), 8, entries)
        for entry in entries:
            with self.subTest(entry=entry):
                self.assertRegex(entry, r"BSP_SBC_(CONCAT|SPI)\(|^::sbc::Uja113xSpi::[A-Z_]+,?$")
                self.assertNotRegex(entry, r"\b\d+U?L?\b(?!\))|\btrue\b|\bfalse\b")


SAFE_OUTPUTS_PROJECT = HERE / "test" / "profiles" / "projects" / "testproject_safe_outputs" / "project.yaml"
# SYS-49: the expectation and the reference manual register facts come from one place, the bswgen-independent
# tools/bswgen/test/safe_outputs_expectation.py (PLAT-77), which the image check of the linked ELF uses as well.
sys.path.insert(0, str(HERE / "test"))
import safe_outputs_expectation  # noqa: E402

RM_PSOR, RM_PCOR, RM_SWOCTRL = (safe_outputs_expectation.RM_PSOR, safe_outputs_expectation.RM_PCOR,
                                safe_outputs_expectation.RM_SWOCTRL)


class BswGenSafeOutputsHostTest(unittest.TestCase):
    """IO-32: the table of register writes the fault handlers work through first (bsp/safeOutputs/
    safeOutputsConfiguration.h, table nxSafeOutputs in platforms/s32k1xx/port/main/src/os/isr/isr_wdg.cpp).

    The real table definition of the port is compiled on the host (UNIT_TEST: without the naked handlers) with the
    real NXP register layouts and printed. The expectation is tools/bswgen/test/safe_outputs_expectation.expected
    (PLAT-77, shared with the image check; SYS-49 removed the own copy of the rules here). It is read from board.yaml,
    the project's use and the module manifests (connect.<name>.safe_off), with register addresses from the reference
    manual - not from the generator:
    first every used PWM group (FTM SWOCTRL = its channel mask), then every safe_off output of a module instance in
    board order (GPIO PCOR for active high, PSOR for active low, bit of the pin), each gated by the PCC register of
    its peripheral. Profiles: testproject_safe_outputs (own: two gate drivers with DRV_EN active high on PTD16 and
    active low on PTA7, a PWM group on FTM1 channels 2..5 with active-low low sides, a plain LED output), the eval
    board demo (FTM0 0x3F, DRV_EN PTC11), the charge project (gate driver without PWM group), the S32K148 build
    profile and the S32K116 project (no power outputs: empty table). Mutants: PCOR for an active-low output, every
    output instead of the safe_off ones, enables before the PWM group, the port without table entries.
    """

    NXP = REPO / "external" / "openbsw" / "platforms" / "s32k1xx" / "bsp" / "bspMcu" / "include" / "3rdparty" / "nxp"
    HOST = HERE / "test" / "safe_outputs_host"
    ISR_WDG = PORT / "main" / "src" / "os" / "isr" / "isr_wdg.cpp"
    HARD_FAULT_INCLUDE = REPO / "platforms" / "s32k1xx" / "hardFaultHandler" / "include"

    setUp, tearDown = BswGenAdcTest.setUp, BswGenAdcTest.tearDown

    @staticmethod
    def expected(project_file: Path) -> list[tuple[int, int, int, int]]:
        """The four words of every entry, from the shared independent expectation (SYS-49: no own copy of the rules
        any more; the image check and this host test now judge the table by the same specification)."""
        return [entry.words for entry in safe_outputs_expectation.expected(project_file)]

    def build_and_run(self, gen: Path, isr_wdg: Path | None = None) -> list[tuple[int, int, int, int]]:
        exe = self.tmp / f"safe_outputs_{len(list(self.tmp.glob('*.exe')))}.exe"
        cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
               "-DUNIT_TEST", f"-I{self.HOST / 'stubs'}", f"-I{gen}", f"-I{self.HARD_FAULT_INCLUDE}",
               f"-isystem{self.NXP}", str(self.HOST / "safe_outputs_host_test.cpp"), str(isr_wdg or self.ISR_WDG),
               "-o", str(exe)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        r = subprocess.run([str(exe)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)
        rows = [tuple(int(v, 16) for v in line.split()[1:]) for line in r.stdout.splitlines()
                if line.startswith("ENTRY ")]
        self.assertIn(f"COUNT {len(rows)}", r.stdout)
        return rows

    def generate(self, project_file: Path, name: str) -> Path:
        gen = self.tmp / name
        bswgen.generate(project_file, gen)
        return gen

    def header(self, gen: Path) -> Path:
        return gen / "bsp" / "safeOutputs" / "safeOutputsConfiguration.h"

    def test_table_of_every_profile(self):
        profiles = ((SAFE_OUTPUTS_PROJECT, 3), (PROJECT_FILE, 3),
                    *existing((EVAL_PROJECT_FILE, 3), (EVAL_CHARGE_FILE, 2)),
                    (REPO / "projects" / "buildtest_s32k148_min" / "project.yaml", 0),
                    *existing((S32K116_PROJECT, 0)))         # TOOL-243c: private until PLAT-13
        for project_file, count in profiles:
            with self.subTest(project=project_file.parent.name):
                expected = self.expected(project_file)
                self.assertEqual(len(expected), count)
                rows = self.build_and_run(self.generate(project_file, project_file.parent.name))
                print(f"\n{project_file.parent.name}: " + ", ".join(f"[0x{a:08X}] = 0x{v:X}" for a, v, _, _ in rows),
                      file=sys.stderr)
                self.assertEqual(rows, expected)

    def test_own_profile_covers_both_levels(self):
        """What the eval board alone does not show: an active-low enable (PSOR), another FTM and channel mask,
        two instances of the module, an output without safe_off."""
        rows = self.expected(SAFE_OUTPUTS_PROJECT)
        self.assertEqual(rows[0][:2], (0x40039000 + RM_SWOCTRL, 0x3C))
        self.assertEqual(rows[1][:2], (0x400FF0C0 + RM_PCOR, 1 << 16))     # GD_A, PTD16, active high
        self.assertEqual(rows[2][:2], (0x400FF000 + RM_PSOR, 1 << 7))      # GD_B, PTA7, active low
        header = self.header(self.generate(SAFE_OUTPUTS_PROJECT, "own")).read_text(encoding="utf-8")
        self.assertNotIn("LED", header)
        self.assertIn("#define NX_SAFE_OUTPUT_COUNT 3\n", header)

    def test_empty_table_without_power_outputs(self):
        header = self.header(self.generate(S32K116_PROJECT, "k116")).read_text(encoding="utf-8")
        self.assertIn("#define NX_SAFE_OUTPUT_COUNT 0\n", header)
        self.assertIn("#define NX_SAFE_OFF_OUTPUT_COUNT 0\n", header)
        self.assertRegex(header, r"#define NX_SAFE_OUTPUTS\(X\)\n")

    def test_safe_off_output_count_counts_only_the_module_outputs(self):
        """IO-32: the count the integration checks its IPowerStageOutput adapters against (deadman, decision 6).

        It counts the safe_off outputs of module instances, not the PWM groups - a PWM group is switched off
        through the FTM, it has no driver whose state could drift."""
        for project_file, name, count in (
                (SAFE_OUTPUTS_PROJECT, "off_own", 2),      # two gate drivers, one PWM group, LED without safe_off
                # IO-37: the test profile, eval board demo and charge add the high side switch (in, safe_off)
                (PROJECT_FILE, "off_demo", 2),             # gate driver and high side switch, plus one PWM group
                *existing((EVAL_PROJECT_FILE, "off_eval", 2), (EVAL_CHARGE_FILE, "off_charge", 2)),
                (REPO / "projects" / "buildtest_s32k148_min" / "project.yaml", "off_k148", 0)):
            with self.subTest(project=project_file.parent.name):
                header = self.header(self.generate(project_file, name)).read_text(encoding="utf-8")
                self.assertIn(f"#define NX_SAFE_OFF_OUTPUT_COUNT {count}\n", header)

    def test_safe_off_only_for_outputs(self):
        with self.assertRaises(Exception) as ctx:
            nxconfig.model.ConnectSpec(kind="digital_input", safe_off=True)
        self.assertIn("safe_off needs kind digital_output", str(ctx.exception))

    def test_mutants_are_detected(self):
        expected = self.expected(SAFE_OUTPUTS_PROJECT)
        clean = self.generate(SAFE_OUTPUTS_PROJECT, "clean")
        text = self.header(clean).read_text(encoding="utf-8")
        head, rest = text.split("#define NX_SAFE_OUTPUTS(X) \\\n")
        body, tail = rest.split("\n// clang-format on", 1)     # SYS-30: the read-back list follows
        items = body.split(" \\\n")                     # comment, X(...), comment, X(...), ...
        pairs = [items[i:i + 2] for i in range(0, len(items), 2)]
        enables_first = (head + "#define NX_SAFE_OUTPUTS(X) \\\n"
                         + " \\\n".join(sum(pairs[1:] + pairs[:1], [])) + "\n// clang-format on" + tail)
        mutants = {
            "PCOR for an active-low output": text.replace("PSOR", "PCOR"),
            "enables before the PWM group": enables_first,
        }
        for name, mutant in mutants.items():
            with self.subTest(mutant=name):
                self.assertNotEqual(mutant, text)
                gen = self.tmp / f"mutant_{len(list(self.tmp.glob('mutant_*')))}"
                shutil.copytree(clean, gen)
                self.header(gen).write_text(mutant, encoding="utf-8")
                self.assertNotEqual(self.build_and_run(gen), expected)
        with self.subTest(mutant="every output instead of the safe_off ones"):
            original = bswgen.Board.collect_safe_outputs

            def every_output(board):
                original(board)
                led = next(o for o in board.config.selected("dio.outputs") if o.name == "LED")
                board.safe_outputs.append({"name": "LED", "what": "LED", "value": "(1UL << 15U)", "gate": "PORTD",
                                           "address": "IP_PTD_BASE + offsetof(GPIO_Type, PCOR)",
                                           # SYS-30: the read-back the generator would give it
                                           "readback": {"address": "IP_PTD_BASE + offsetof(GPIO_Type, PDIR)",
                                                        "mask": "(1UL << 15U)", "expected": "0U"}})
                self.assertEqual(led.pin, "PTD15")

            bswgen.Board.collect_safe_outputs = every_output
            try:
                gen = self.generate(SAFE_OUTPUTS_PROJECT, "mutant_led")
            finally:
                bswgen.Board.collect_safe_outputs = original
            self.assertNotEqual(self.build_and_run(gen), expected)
        with self.subTest(mutant="port without table entries"):
            source = self.ISR_WDG.read_text(encoding="utf-8")
            mutant = source.replace("= {NX_SAFE_OUTPUTS(NX_SAFE_OUTPUT_ENTRY){0U, 0U, 0U, 0U}};\n\n#undef",
                                    "= {{0U, 0U, 0U, 0U}};\n\n#undef")
            self.assertNotEqual(mutant, source)
            path = self.tmp / "isr_wdg_mutant.cpp"
            path.write_text(mutant, encoding="utf-8")
            self.assertEqual(self.build_and_run(clean, path), [])


REFERENCE_PROJECT = HERE / "test" / "profiles" / "projects" / "testproject_adc_reference" / "project.yaml"
SAFE_SYSTEM = "platforms/s32k1xx/port/safety/safeSystem"
SAFE_SYSTEM_SOURCE = REPO / SAFE_SYSTEM / "src" / "safeSystem" / "SafeSystem.cpp"
SAFE_SYSTEM_HEADERS = ("bsp/adc/adcReferenceConfiguration.h", "integration/safeSystemConfiguration.h")


class BswGenAdcReferenceTest(unittest.TestCase):
    """ARCH-32: band of the reference check of safeSystem - nominal VREFH from the board (generated
    bsp/adc/adcReferenceConfiguration.h), tolerance from the project (modules.safeSystem.adc_reference_tolerance_mv).
    Own profile testproject_adc_reference (5000 mV +- 350 mV) next to the eval board projects (4096 mV +- 200 mV);
    the SafeSystem host test runs against the generated headers, and SafeSystem.cpp with the old constants must fail.
    """

    FLAGS = ModuleHostTest.FLAGS
    SYSTEM = ["external/openbsw/libs/bsw/bsp/include",
              "external/openbsw/libs/bsp/bspInterrupts/include", "external/openbsw/libs/bsp/bspInterrupts/mock/include"]

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def generate(self, project, name="gen"):
        bswgen.generate(project, self.tmp / name)
        return self.tmp / name

    def headers(self, project, name="gen") -> Path:
        """Only the two generated headers SafeSystem reads, in an include directory of their own (the rest of the
        generated tree would hide the stubs of the host test)."""
        gen = self.generate(project, name)
        include = self.tmp / f"{name}_include"
        for rel in SAFE_SYSTEM_HEADERS:
            (include / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(gen / rel, include / rel)
        return include

    def build_and_run(self, name, include=None, source=SAFE_SYSTEM_SOURCE):
        exe = self.tmp / f"{name}.exe"
        includes = ([include] if include else []) + [REPO / SAFE_SYSTEM / "include",
                                                     REPO / SAFE_SYSTEM / "test" / "host" / "stubs"]
        cmd = [sys.executable, "-m", "ziglang", "c++", *self.FLAGS, *[f"-I{i}" for i in includes],
               *[f"-isystem{REPO / s}" for s in self.SYSTEM],
               str(REPO / SAFE_SYSTEM / "test" / "host" / "SafeSystemHostTest.cpp"), str(source), "-o", str(exe)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            return r
        return subprocess.run([str(exe)], capture_output=True, text=True)

    def test_nominal_from_board_tolerance_from_project(self):
        gen = self.generate(REFERENCE_PROJECT)
        ref = (gen / "bsp/adc/adcReferenceConfiguration.h").read_text(encoding="utf-8")
        self.assertIn("#define NX_ADC_REFERENCE_MV 5000U // mV", ref)   # testboard_adc_sampling
        self.assertIn("testproject_adc_reference", ref)
        safe = (gen / "integration/safeSystemConfiguration.h").read_text(encoding="utf-8")
        self.assertIn("#define NX_MODULE_SAFESYSTEM_PARAM_ADC_REFERENCE_TOLERANCE_MV 350U // mV", safe)
        self.assertIn("#define NX_MODULE_SAFESYSTEM_PARAM_ADC_READ_ERROR_LIMIT 6U", safe)

    def test_projects_of_the_repository(self):
        # eval board: the values the constants had (4096 mV +- 200 mV, the default of the module); the 5 V boards
        # get their own nominal value, which the constant got wrong
        # PLAT-62: plus the own test profile (5 V board); the eval board projects only where they exist
        for project_file, mv in (*existing((EVAL_PROJECT_FILE, 4096), (EVAL_CHARGE_FILE, 4096)),
                                 (REPO / "projects" / "buildtest_s32k148_min" / "project.yaml", 5000),
                                 # TOOL-243c: the S32K116EVB profile is private until PLAT-13
                                 *existing((S32K116_PROJECT, 5000)),
                                 (PROJECT_FILE, 5000)):
            project = project_file.parent.name
            with self.subTest(project=project):
                gen = self.generate(project_file, project)
                ref = (gen / "bsp/adc/adcReferenceConfiguration.h").read_text(encoding="utf-8")
                self.assertIn(f"#define NX_ADC_REFERENCE_MV {mv}U // mV", ref)
                safe = (gen / "integration/safeSystemConfiguration.h").read_text(encoding="utf-8")
                self.assertIn("#define NX_MODULE_SAFESYSTEM_PARAM_ADC_REFERENCE_TOLERANCE_MV 200U // mV", safe)

    def test_board_without_reference(self):
        # no ADC in use: nxconfig accepts a board without adc_reference_mv, the macro stays undefined and
        # SafeSystem.cpp stops with an #error instead of assuming a value
        board = yaml.safe_load((REFERENCE_PROJECT.parents[2] / "boards" / "testboard_adc_sampling" / "board.yaml")
                               .read_text(encoding="utf-8"))
        board["board"].pop("adc_reference_mv")
        (self.tmp / "b").mkdir()
        (self.tmp / "b" / "board.yaml").write_text(yaml.safe_dump(board, sort_keys=False), encoding="utf-8")
        project = yaml.safe_load(REFERENCE_PROJECT.read_text(encoding="utf-8"))
        project["project"]["board"] = (self.tmp / "b").as_posix()
        project.pop("adc_internal")
        path = self.tmp / "p" / "project.yaml"
        path.parent.mkdir()
        path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        gen = self.generate(path)
        ref = (gen / "bsp/adc/adcReferenceConfiguration.h").read_text(encoding="utf-8")
        self.assertNotIn("#define NX_ADC_REFERENCE_MV", ref)
        self.assertIn("defines no board.adc_reference_mv", ref)
        include = self.tmp / "noref_include"
        for rel in SAFE_SYSTEM_HEADERS:
            (include / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(gen / rel, include / rel)
        r = self.build_and_run("noref", include)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("board.adc_reference_mv", r.stderr)

    def test_host_test_against_generated_headers(self):
        for name, project in (("own_profile", REFERENCE_PROJECT), ("full_profile", PROJECT_FILE),
                              *([("eval_board", EVAL_PROJECT_FILE)] if EVAL_PROJECT_FILE.is_file() else [])):
            with self.subTest(profile=name):
                r = self.build_and_run(name, self.headers(project, name))
                self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-3000:])
                self.assertRegex(r.stdout, r"\d+ checks, 0 failures")

    def test_old_constants_are_detected(self):
        # Gegenprobe: SafeSystem.cpp with the constants of the eval board again, against the stubs (5000 mV +- 300 mV)
        # and against the own profile (5000 mV +- 350 mV)
        source = SAFE_SYSTEM_SOURCE.read_text(encoding="utf-8")
        mutants = {
            "nominal value of the eval board": ("VREFH_NOMINAL_MV   = NX_ADC_REFERENCE_MV;",
                                                "VREFH_NOMINAL_MV   = 4096U;"),
            "tolerance of 200 mV": ("VREFH_TOLERANCE_MV = NX_MODULE_SAFESYSTEM_PARAM_ADC_REFERENCE_TOLERANCE_MV;",
                                    "VREFH_TOLERANCE_MV = 200U;"),
            "upper limit not checked": ("        || (_adcRef > (VREFH_NOMINAL_MV + VREFH_TOLERANCE_MV)))",
                                        "        || false)"),
            "lower limit not checked": ("if ((_adcRef < (VREFH_NOMINAL_MV - VREFH_TOLERANCE_MV))",
                                        "if (false"),
        }
        own = self.headers(REFERENCE_PROJECT, "own")
        for index, (what, (old, new)) in enumerate(mutants.items()):
            self.assertEqual(source.count(old), 1, what)
            path = self.tmp / f"SafeSystem_mutant{index}.cpp"
            path.write_text(source.replace(old, new, 1), encoding="utf-8")
            for profile, include in (("stubs", None), ("own profile", own)):
                with self.subTest(mutant=what, profile=profile):
                    r = self.build_and_run(f"mutant{index}_{profile.replace(' ', '_')}", include, path)
                    self.assertNotEqual(r.returncode, 0, f"mutant not detected: {what}\n{r.stdout[-800:]}")
                    self.assertRegex(r.stdout, r"\d+ checks, [1-9]\d* failures")


if __name__ == "__main__":
    unittest.main()
