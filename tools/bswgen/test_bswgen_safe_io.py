"""SYS-24 (finding F4, SM-21/SM-22; PLAT-75 TF5): register list of SafeIo generated from the configuration.

bswgen writes bsp/safeIo/safeIoConfiguration.h - which registers the safety layer locks at start and checks in every
safety cycle. These tests render it for own profiles and the projects of the repository and compare every entry with
the bswgen-independent expectation of tools/bswgen/test/safe_io_expectation.py (YAML, mcudb pin multiplexing,
reference-manual addresses), and the clock-dependent FTM words with the driver configuration of the same run
(ftmPwmConfiguration.h: SafeIo must check exactly what ComplementaryPwm writes).

  ExpectationTest        the lists of the profiles written out by hand (own S32K144 and S32K116 profiles, demo)
  GeneratedListTest      every profile: generated list == expectation, entry by entry
  RuleTest               the rules the generator enforces (indicator not locked, unknown pin attribute, a board
                         without PWM group, a project without safety section; SYS-153: access width and mask, the
                         low-voltage detect reset entry against the family port's LowVoltageDetectReset.h)
  GeneratorMutantTest    mutants of the generator (dropped pin, wrong function, lock bit missing, direction,
                         gate of another port, FTM value, order) turn the comparison red

Run: .venv/Scripts/python -m unittest tools/bswgen/test_bswgen_safe_io.py
"""

import re
import shutil
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "test"))

import bswgen  # noqa: E402
import safe_io_expectation as sie  # noqa: E402

OWN = HERE / "test" / "profiles" / "projects"
PROFILES = {
    "testproject_safe_outputs": OWN / "testproject_safe_outputs" / "project.yaml",
    "testproject_safe_outputs_k116": OWN / "testproject_safe_outputs_k116" / "project.yaml",
    "buildtest_s32k148_min": REPO / "projects" / "buildtest_s32k148_min" / "project.yaml",
    "s32k116evb_minimal": REPO / "projects" / "s32k116evb_minimal" / "project.yaml",
    "evalboard_s32k144_charge": REPO / "projects" / "evalboard_s32k144_charge" / "project.yaml",
    "evalboard_s32k144_demo": REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml",
}
HEADER = "bsp/safeIo/safeIoConfiguration.h"
FTM_HEADER = "bsp/io/outputPwm/ftmPwmConfiguration.h"
PCR = 0x8743
LK = 0x8000
ISF = 0x01000000
CGC = 0x40000000


def _available(name: str) -> Path:
    path = PROFILES[name]
    if not path.exists():
        raise unittest.SkipTest(f"{name}: {path} missing (private project)")
    return path


class GeneratedBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nx_safeio_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def render(self, project: Path, tag: str = "out") -> tuple[list[sie.Entry], dict | None]:
        out = self.tmp / tag
        bswgen.generate(project, out)
        ftm = (out / FTM_HEADER).read_text(encoding="utf-8")
        driver = sie.driver_values(ftm) if "PERIOD_TICKS" in ftm else None
        return sie.parse((out / HEADER).read_text(encoding="utf-8")), driver


class ExpectationTest(unittest.TestCase):
    """The expectation itself, written out by hand for three profiles (addresses from the reference manual)."""

    def test_own_s32k144_profile(self):
        entries = sie.expected(PROFILES["testproject_safe_outputs"])
        pcr = [(e.name, hex(e.address), hex(e.expected)) for e in entries if e.cls == "PIN_FUNCTION"]
        # FTM1 CH2..5 on PTB? per board, then PTD16 (active high) and PTA7 (active low) of the two gate drivers
        self.assertEqual([e.cls for e in entries].count("PIN_FUNCTION"), len(pcr))
        self.assertTrue(all(e.lock_bits == LK and e.lock_clear == ISF and e.mask == PCR
                            for e in entries if e.cls == "PIN_FUNCTION"))
        gates = [e.name for e in entries if e.cls == "CLOCK_GATE"]
        self.assertEqual(gates[0], "PCC_FTM1")
        self.assertEqual(entries[[e.name for e in entries].index("PCC_FTM1")].address, 0x40065000 + 4 * 57)
        gpio = {e.name: (hex(e.address), hex(e.mask), hex(e.expected)) for e in entries if e.cls == "GPIO_DIRECTION"}
        self.assertEqual(gpio["PTA_PDDR"], (hex(0x400FF014), hex(1 << 7), hex(1 << 7)))
        self.assertEqual(gpio["PTA_PIDR"], (hex(0x400FF018), hex(1 << 7), "0x0"))
        self.assertEqual(gpio["PTD_PDDR"], (hex(0x400FF0D4), hex(1 << 16), hex(1 << 16)))

    def test_own_s32k116_profile(self):
        entries = sie.expected(PROFILES["testproject_safe_outputs_k116"])
        pcr = [(e.name, e.address, e.expected) for e in entries if e.cls == "PIN_FUNCTION"]
        # group DUAL on FTM0 (not the board's first group HALF): PTC2, PTC3, PTE8, PTE9 with MUX 2, then the active-low
        # enable GD_C_DRV_EN on PTC9 (GPIO); the LED without a module is not locked, the pins of HALF are not either
        self.assertEqual(pcr, [("DUAL_U_HS", 0x4004B000 + 4 * 2, 0x8200), ("DUAL_U_LS", 0x4004B000 + 4 * 3, 0x8200),
                               ("DUAL_V_HS", 0x4004D000 + 4 * 8, 0x8200), ("DUAL_V_LS", 0x4004D000 + 4 * 9, 0x8200),
                               ("GD_C_DRV_EN", 0x4004B000 + 4 * 9, 0x8100)])
        self.assertEqual([e.name for e in entries if e.cls == "CLOCK_GATE"], ["PCC_FTM0", "PCC_PORTC", "PCC_PORTE"])
        ftm = {e.name: (e.address, e.mask, e.expected) for e in entries if e.cls == "PWM_TIMER"}
        self.assertEqual(ftm["DUAL_SC"], (0x40038000, 0x00FF003F, 0x00CC0008))       # PWMEN of CH2/3/6/7
        self.assertEqual(ftm["DUAL_COMBINE"], (0x40038064, 0x77777777, 0x33003300))  # pairs 1 and 3
        self.assertEqual(ftm["DUAL_POL"], (0x40038070, 0xFF, 0x88))                   # low sides active low
        self.assertEqual(ftm["DUAL_CONF"], (0x40038084, 0xC0, 0x40))
        self.assertEqual(sorted(n for n in ftm if n.endswith("DEADTIME")),
                         ["DUAL_PAIR1DEADTIME", "DUAL_PAIR3DEADTIME"])
        self.assertEqual(ftm["DUAL_PAIR3DEADTIME"][0], 0x400380B8)

    def test_demo(self):
        entries = sie.expected(_available("evalboard_s32k144_demo"))
        names = [e.name for e in entries if e.cls == "PIN_FUNCTION"]
        self.assertEqual(names, ["MOT1_U_PWM_HS", "MOT1_U_PWM_LS", "MOT1_V_PWM_HS", "MOT1_V_PWM_LS", "MOT1_W_PWM_HS",
                                 "MOT1_W_PWM_LS", "MOT1_GD_DRV_EN", "EN_SW_KL30B", "LED2"])
        self.assertEqual(len(entries), 28)

    def test_board_without_power_outputs_locks_the_role_pin_only(self):
        entries = sie.expected(PROFILES["buildtest_s32k148_min"])
        self.assertEqual([e.cls for e in entries], ["PIN_FUNCTION", "GPIO_DIRECTION", "GPIO_DIRECTION",
                                                    "CLOCK_GATE", "SUPERVISION"])

    def test_low_voltage_detect_reset_in_every_profile(self):
        # SYS-153: PMC_LVDSC1 at 0x4007D000 (RM 41.7.1), LVDIE | LVDRE judged, LVDRE expected, 8-bit, never locked
        for name in ("testproject_safe_outputs", "testproject_safe_outputs_k116", "buildtest_s32k148_min"):
            last = sie.expected(PROFILES[name])[-1]
            self.assertEqual((last.cls, last.address, last.mask, last.expected, last.lock_bits, last.lock_clear,
                              last.width), ("SUPERVISION", 0x4007D000, 0x30, 0x10, 0, 0, 8), name)

    def test_address_expressions(self):
        self.assertEqual(sie.address_value("IP_PORTD_BASE + offsetof(PORT_Type, PCR) + 4UL * 17U"),
                         0x4004C000 + 68)
        self.assertEqual(sie.address_value("IP_PCC_BASE + offsetof(PCC_Type, PCCn) + 4UL * PCC_FTM3_INDEX"),
                         0x40065000 + 4 * 38)
        self.assertEqual(sie.address_value("IP_FTM2_BASE + offsetof(FTM_Type, PAIR2DEADTIME)"), 0x4003A0B0)
        with self.assertRaises(sie.so.ExpectationError):
            sie.address_value("IP_LPIT0_BASE + 4U")
        self.assertEqual(sie.address_value("IP_PMC_BASE + offsetof(PMC_Type, LVDSC1)"), 0x4007D000)


class GeneratedListTest(GeneratedBase):
    def check(self, name: str):
        project = _available(name)
        actual, driver = self.render(project)
        findings = sie.compare(actual, sie.expected(project), driver)
        self.assertEqual(findings, [], f"{name}:\n" + "\n".join(findings))
        return actual

    def test_own_s32k144_profile(self):
        self.check("testproject_safe_outputs")

    def test_own_s32k116_profile(self):
        actual = self.check("testproject_safe_outputs_k116")
        dead = [e for e in actual if e.name.endswith("DEADTIME")]
        self.assertEqual(len({e.expected for e in dead}), 1)

    def test_s32k148_build_test(self):
        self.check("buildtest_s32k148_min")

    def test_s32k116_evb(self):
        self.check("s32k116evb_minimal")

    def test_charge(self):
        actual = self.check("evalboard_s32k144_charge")
        self.assertFalse([e for e in actual if e.cls == "PWM_TIMER"])

    def test_demo(self):
        actual = self.check("evalboard_s32k144_demo")
        self.assertEqual(len(actual), 28)
        sc = next(e for e in actual if e.name == "MOT1_SC")
        self.assertEqual(sc.expected, 0x003F0008)


class RuleTest(GeneratedBase):
    def board(self, name: str) -> "bswgen.Board":
        config, messages = bswgen._checked(_available(name), bswgen.nxconfig.MCUDB, bswgen.modules.MODULE_DIRS)
        self.assertIsNotNone(config, messages)
        board = bswgen.Board(config)
        bswgen.collect(board)
        return board

    def test_indicator_must_not_be_locked(self):
        board = self.board("evalboard_s32k144_demo")
        board.safety["safe_state_output"] = "MOT1_GD_DRV_EN"
        with self.assertRaisesRegex(bswgen.ConfigError, r"safety.safe_state_output: MOT1_GD_DRV_EN .* locked as "
                                                        r"safe_off output"):
            board.collect_safe_io()
        board.safety["safe_state_output"] = "LED2"
        with self.assertRaisesRegex(bswgen.ConfigError, "role pin safety.locked_pin"):
            board.collect_safe_io()

    def test_unknown_pin_attribute_is_refused(self):
        board = self.board("evalboard_s32k144_demo")
        board.pins["LED2"].extra_cfg = " | (uint32_t) SLEW_FAST"
        with self.assertRaisesRegex(bswgen.ConfigError, "LED2: pin attribute SLEW_FAST"):
            board.collect_safe_io()

    def test_attributes_become_pcr_bits(self):
        board = self.board("buildtest_s32k148_min")
        role = board.safety["locked_pin"]
        board.pins[role].extra_cfg = " | (uint32_t) STRENGTH_ON | (uint32_t) PULLUP"
        board.collect_safe_io()
        entry = next(e for e in board.safe_io if e["name"] == role)
        self.assertEqual(entry["expected"], 0x8100 | 0x40 | 0x3)

    def test_project_without_safety_section_locks_the_power_pins(self):
        board = self.board("evalboard_s32k144_demo")
        board.safety = None
        board.collect_safe_io()
        self.assertNotIn("LED2", [e["name"] for e in board.safe_io])
        self.assertEqual(len([e for e in board.safe_io if e["cls"] == "PIN_FUNCTION"]), 8)

    def test_low_voltage_detect_reset_entry(self):
        # SYS-153: rendered after the timer words with an 8-bit access; every other entry keeps 32 bits
        actual, _ = self.render(PROFILES["testproject_safe_outputs"])
        header = (self.tmp / "out" / HEADER).read_text(encoding="utf-8")
        self.assertIn("X(SUPERVISION, PMC_LVDSC1, IP_PMC_BASE + offsetof(PMC_Type, LVDSC1), 0x00000030UL, "
                      "0x00000010UL, 0x00000000UL, 0x00000000UL, 8U)", header)
        self.assertEqual([e.name for e in actual if e.width == 8], ["PMC_LVDSC1"])
        self.assertEqual(actual[-1].name, "PMC_LVDSC1")

    def test_width_and_mask_are_checked(self):
        # SYS-153: a width the engine does not have, or a mask wider than the access, is refused
        board = self.board("buildtest_s32k148_min")
        with unittest.mock.patch.object(bswgen, "SAFE_IO_PMC_LVDSC1_MASK", 0x130):
            with self.assertRaisesRegex(bswgen.ConfigError, r"PMC_LVDSC1: mask 0x130 does not fit an access of 8 "
                                                            r"bits"):
                board.collect_safe_io()
        with unittest.mock.patch.object(bswgen, "SAFE_IO_WIDTHS", (32,)):
            with self.assertRaisesRegex(bswgen.ConfigError, r"PMC_LVDSC1: .* access of 8 bits \(widths: 32\)"):
                board.collect_safe_io()
        board.collect_safe_io()
        self.assertEqual(board.safe_io[-1]["width"], 8)
        self.assertTrue(all(e["width"] == 32 for e in board.safe_io[:-1]))

    def test_family_port_and_list_agree(self):
        # SYS-153: what the list expects is what boardInit() writes and what the rule of the family port accepts
        # (platforms/s32k1xx/port/main/include/pmc/LowVoltageDetectReset.h, SYS-125)
        text = (REPO / "platforms/s32k1xx/port/main/include/pmc/LowVoltageDetectReset.h").read_text(encoding="utf-8")
        consts = {m.group(1): int(m.group(2), 16)
                  for m in re.finditer(r"constexpr uint8_t (LVDSC1_\w+)\s*=\s*(0x[0-9A-F]+)U;", text)}
        self.assertEqual(consts["LVDSC1_LVDRE"] | consts["LVDSC1_LVDIE"], bswgen.SAFE_IO_PMC_LVDSC1_MASK)
        self.assertEqual(consts["LVDSC1_LVDRE"], bswgen.SAFE_IO_PMC_LVDSC1_EXPECTED)
        self.assertIn("constexpr uint8_t LVDSC1_START_VALUE = LVDSC1_LVDRE;", text)
        # mask and expected value of the port function (unsigned casts since REL-020-fix4, MISRA)
        body = re.sub(r"static_cast<uint32_t>\(([^()]*(?:\([^()]*\))?[^()]*)\)", r"\1", text)
        self.assertIn("return (lvdsc1 & (LVDSC1_LVDRE | LVDSC1_LVDIE)) == LVDSC1_LVDRE;", " ".join(body.split()))
        self.assertEqual(bswgen.SAFE_IO_PMC_LVDSC1_ADDRESS, "IP_PMC_BASE + offsetof(PMC_Type, LVDSC1)")

    def test_header_count_and_entries_agree(self):
        actual, _ = self.render(PROFILES["testproject_safe_outputs"])
        header = (self.tmp / "out" / HEADER).read_text(encoding="utf-8")
        with self.assertRaises(sie.so.ExpectationError):
            sie.parse(header.replace(f"COUNT {len(actual)}", f"COUNT {len(actual) + 1}"))


def _mutated(mutation):
    original = bswgen.Board.collect_safe_io

    def collect(self):
        original(self)
        mutation(self.safe_io)
    return collect


def _first(cls: str, field: str, change):
    def mutate(entries):
        e = next(e for e in entries if e["cls"] == cls)
        e[field] = change(e[field])
    return mutate


GENERATOR_MUTANTS = {
    "a locked pin dropped": lambda es: es.remove(next(e for e in es if e["cls"] == "PIN_FUNCTION")),
    "wrong pin function": _first("PIN_FUNCTION", "expected", lambda v: v ^ 0x300),
    "lock bit not expected": _first("PIN_FUNCTION", "expected", lambda v: v & ~LK),
    "no lock bits": _first("PIN_FUNCTION", "lock_bits", lambda v: 0),
    "interrupt flag not kept out of the lock write": _first("PIN_FUNCTION", "lock_clear", lambda v: 0),
    "drive strength expected": _first("PIN_FUNCTION", "expected", lambda v: v | 0x40),
    "PDDR expects input": _first("GPIO_DIRECTION", "expected", lambda v: 0),
    "PIDR not judged": lambda es: es.remove(next(e for e in es if e["name"].endswith("_PIDR"))),
    "gate of the neighbouring peripheral": _first("CLOCK_GATE", "address", lambda v: v + " + 4UL"),
    "polarity inverted": lambda es: [e.update(expected=e["expected"] ^ 0xFF) for e in es if e["name"].endswith("POL")],
    "dead time from the other prescaler": lambda es: [e.update(expected=e["expected"] ^ 0x40) for e in es
                                                      if e["name"].endswith("DEADTIME")],
    "prescaler not in SC": lambda es: [e.update(expected=e["expected"] ^ 0x1) for e in es if e["name"].endswith("_SC")],
    "order of two entries swapped": lambda es: es.insert(1, es.pop(0)),
    # SYS-153: the low-voltage detect reset
    "LVDSC1 dropped": lambda es: es.remove(next(e for e in es if e["cls"] == "SUPERVISION")),
    "LVDSC1 read with 32 bits": _first("SUPERVISION", "width", lambda v: 32),
    "LVDSC1 interrupt not judged": _first("SUPERVISION", "mask", lambda v: v & ~0x20),
    "LVDSC1 interrupt expected on": _first("SUPERVISION", "expected", lambda v: v | 0x20),
    "LVDSC1 reset expected off": _first("SUPERVISION", "expected", lambda v: 0),
    "LVDSC2 instead of LVDSC1": _first("SUPERVISION", "address", lambda v: v + " + 1UL"),
    "a word read with 8 bits": _first("CLOCK_GATE", "width", lambda v: 8),
}


class GeneratorMutantTest(GeneratedBase):
    def test_mutants_turn_the_comparison_red(self):
        for profile in ("testproject_safe_outputs", "testproject_safe_outputs_k116"):
            project = PROFILES[profile]
            for index, (name, mutation) in enumerate(GENERATOR_MUTANTS.items()):
                with self.subTest(profile=profile, mutant=name):
                    with unittest.mock.patch.object(bswgen.Board, "collect_safe_io", _mutated(mutation)):
                        try:
                            actual, driver = self.render(project, f"{profile}_m{index}")
                        except sie.so.ExpectationError:
                            continue      # an address the reference manual facts do not know: red as well
                    self.assertNotEqual(sie.compare(actual, sie.expected(project), driver), [], name)


if __name__ == "__main__":
    unittest.main()
