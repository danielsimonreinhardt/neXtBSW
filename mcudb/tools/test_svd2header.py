"""Tests of the SVD to device header generator (PLAT-11).

- synthetic mini SVD: register layout, reserved gaps, arrays, clusters, unions, instances, interrupts, core
  configuration; the generated header is compiled for Cortex-M0+ when arm-none-eabi-gcc is available
- deliberate errors in SVD and rules (the generator must refuse to guess)
- real comparison: S32K148 generated from the SVD OpenBSW ships (BSD-3-Clause) against the OpenBSW S32K148 header,
  restricted to the symbols the s32k1xx drivers and our board port use

Run: .venv/Scripts/python -m unittest mcudb/tools/test_svd2header.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import svd2header as s2h  # noqa: E402

REPO = HERE.parents[1]
OPENBSW_NXP = REPO / "external/openbsw/platforms/s32k1xx/bsp/bspMcu/include/3rdparty/nxp"
GCC_PATH = os.pathsep.join(p for p in (os.environ.get("NX_ARM_GCC_BIN"), os.environ.get("PATH")) if p)
ARM_GCC = shutil.which("arm-none-eabi-gcc", path=GCC_PATH)

# Differences that remain between the generated S32K148 header and the OpenBSW one for the symbols the drivers use.
# All of them are data the SVD does not contain, not naming differences.
KNOWN_S32K148_DIFFERENCES = [
    # the SVD has only the 32-bit view of CRC_DATA, the header also has the byte and half-word views
    "type CRC_Type: member DATAu.DATA_8.LL missing",
    "type CRC_Type: member DATAu.DATA_8.LU missing",
    "type CRC_Type: member DATAu.DATA_8.HL missing",
    "type CRC_Type: member DATAu.DATA_8.HU missing",
    "type CRC_Type: member DATAu.DATA_16.L missing",
    "type CRC_Type: member DATAu.DATA_16.H missing",
    # same for PDB_PODLY: S32K148_M4.svd has no 16-bit views DLY2/DLY1 (the S32K116 SVD has them as alternates);
    # PDB_Type is a driver type since IO-62 (bspSampleGroup), so all its members are compared
    "type PDB_Type: member POnDLY[0].ACCESS16BIT.DLY2 missing",
    "type PDB_Type: member POnDLY[0].ACCESS16BIT.DLY1 missing",
    # S32K148_M4.svd has no <interrupt> elements at all, so there is no IRQn_Type to generate
    "irq WDOG_EWM_IRQn: missing",
    # same reason, the vector of the debug halt guard (IO-32, DebugHaltGuardSystem.h)
    "irq LPIT0_Ch1_IRQn: missing",
    # LPUART<n>_RxTx_IRQn is no longer among the symbols used by hand: since ARCH-15 the board port takes the
    # instance and its interrupt from the generated bsp/uart/uartConfiguration.h; the FlexCAN message buffer
    # interrupts neither since PLAT-12 (bsp/can/canDeviceConfiguration.h, NX_CAN_MB_VECTORS)
]

SVD = """<?xml version="1.0" encoding="UTF-8"?>
<device schemaVersion="1.3">
  <vendor>Test</vendor>
  <name>TESTMCU</name>
  <version>1.0</version>
  <licenseText>Test licence: redistribution permitted.</licenseText>
  <cpu>
    <name>CM0PLUS</name>
    <revision>r0p0</revision>
    <endian>little</endian>
    <mpuPresent>true</mpuPresent>
    <fpuPresent>false</fpuPresent>
    <nvicPrioBits>2</nvicPrioBits>
    <vendorSystickConfig>false</vendorSystickConfig>
  </cpu>
  <addressUnitBits>8</addressUnitBits>
  <width>32</width>
  <peripherals>
    <peripheral>
      <name>TMR0</name>
      <description>Timer</description>
      <groupName>TMR</groupName>
      <baseAddress>0x40000000</baseAddress>
      <addressBlock><offset>0</offset><size>0x40</size><usage>registers</usage></addressBlock>
      <interrupt><name>TMR0_OVF</name><description>Timer 0 overflow</description><value>3</value></interrupt>
      <registers>
        <register>
          <name>SC</name><description>Status and control</description><addressOffset>0x0</addressOffset>
          <size>32</size><access>read-write</access>
          <fields>
            <field><name>EN</name><description>Enable</description><bitOffset>0</bitOffset><bitWidth>1</bitWidth></field>
            <field><name>PS</name><description>Prescaler</description><bitOffset>4</bitOffset><bitWidth>3</bitWidth></field>
          </fields>
        </register>
        <register>
          <name>CNT</name><description>Counter</description><addressOffset>0x8</addressOffset>
          <size>32</size><access>read-only</access>
          <fields><field><name>COUNT</name><bitOffset>0</bitOffset><bitWidth>16</bitWidth></field></fields>
        </register>
        <register>
          <dim>4</dim><dimIncrement>0x4</dimIncrement>
          <name>DLY[%s]</name><description>Delay</description><addressOffset>0x10</addressOffset>
          <size>32</size><access>read-write</access>
          <fields><field><name>VAL</name><bitOffset>0</bitOffset><bitWidth>16</bitWidth></field></fields>
        </register>
        <cluster>
          <dim>2</dim><dimIncrement>0x8</dimIncrement>
          <name>CHANNEL[%s]</name><description>Channel</description><addressOffset>0x20</addressOffset>
          <register>
            <name>CSC</name><description>Channel status</description><addressOffset>0</addressOffset>
            <size>32</size><access>read-write</access>
            <fields><field><name>CHF</name><bitOffset>7</bitOffset><bitWidth>1</bitWidth></field></fields>
          </register>
          <register>
            <name>CV</name><description>Channel value</description><addressOffset>0x4</addressOffset>
            <size>32</size><access>read-write</access>
            <fields><field><name>VAL</name><bitOffset>0</bitOffset><bitWidth>16</bitWidth></field></fields>
          </register>
        </cluster>
      </registers>
    </peripheral>
    <peripheral derivedFrom="TMR0">
      <name>TMR1</name>
      <baseAddress>0x40001000</baseAddress>
      <interrupt><name>TMR1_OVF</name><description>Timer 1 overflow</description><value>4</value></interrupt>
    </peripheral>
    <peripheral>
      <name>CFG</name>
      <description>Configuration</description>
      <baseAddress>0x40002000</baseAddress>
      <addressBlock><offset>0</offset><size>0x10</size><usage>registers</usage></addressBlock>
      <registers>
        <register>
          <name>CFG_TMR0</name><description>Clock of TMR0</description><addressOffset>0x4</addressOffset>
          <size>32</size><access>read-write</access>
          <fields><field><name>CGC</name><bitOffset>30</bitOffset><bitWidth>1</bitWidth></field></fields>
        </register>
        <register>
          <name>CFG_TMR1</name><description>Clock of TMR1</description><addressOffset>0xC</addressOffset>
          <size>32</size><access>read-write</access>
          <fields><field><name>CGC</name><bitOffset>30</bitOffset><bitWidth>1</bitWidth></field></fields>
        </register>
      </registers>
    </peripheral>
  </peripherals>
</device>
"""

RULES = """
schema_version: 1
family: test
peripherals:
  CFG: {module: CFG}
modules:
  TMR:
    layout:
      - cluster: CONTROLS
        stride: 8
        members:
          - {name: CnSC, match: '^CHANNEL(\\d)_CSC$'}
          - {name: CnV, match: '^CHANNEL(\\d)_CV$'}
  CFG:
    layout:
      - {sparse_array: CFGn, match: '^CFG_(\\w+)$', stride: 4, index_macro: 'CFG_{}_INDEX'}
derivatives:
  TESTMCU:
    svd: {file: test.svd}
    interrupts: {TMR1_OVF: TIMER1_OVERFLOW}
"""


def write(folder: Path, svd=SVD, rules=RULES) -> tuple[Path, Path]:
    (folder / "test.svd").write_text(svd, encoding="utf-8")
    (folder / "rules.yaml").write_text(rules, encoding="utf-8")
    return folder / "test.svd", folder / "rules.yaml"


def generate(folder: Path, svd=SVD, rules=RULES, derivative="TESTMCU") -> str:
    svd_file, rules_file = write(folder, svd, rules)
    return s2h.generate(derivative, rules_file, svd_file)[0]


class SyntheticSvdTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="svd2header-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def layout(self, **kwargs):
        text = generate(self.tmp, **kwargs)
        self.text = text
        return s2h.header_layout(text)

    def test_registers_arrays_and_clusters(self):
        layout = self.layout()
        members = dict((p, (o, s)) for p, o, s in layout["types"]["TMR_Type"])
        self.assertEqual(members["SC"], (0x0, 4))
        self.assertEqual(members["CNT"], (0x8, 4))          # the gap at 0x4 becomes RESERVED
        self.assertEqual(members["DLY[0]"], (0x10, 4))
        self.assertEqual(members["DLY[3]"], (0x1C, 4))
        self.assertEqual(members["CONTROLS[0].CnSC"], (0x20, 4))
        self.assertEqual(members["CONTROLS[1].CnV"], (0x2C, 4))
        self.assertEqual(layout["macros"]["TMR_DLY_COUNT"], "4u")
        self.assertEqual(layout["macros"]["TMR_CONTROLS_COUNT"], "2u")
        self.assertIn("__I  uint32_t CNT;", self.text)      # read-only qualifier
        self.assertIn("uint8_t RESERVED_0[4];", self.text)

    def test_field_macros(self):
        macros = self.layout()["macros"]
        self.assertEqual(macros["TMR_SC_PS_MASK"], "(0x70U)")
        self.assertEqual(macros["TMR_SC_PS_SHIFT"], "(4U)")
        self.assertEqual(macros["TMR_SC_PS_WIDTH"], "(3U)")
        self.assertEqual(macros["TMR_SC_PS(x)"],
                         "(((uint32_t)(((uint32_t)(x)) << TMR_SC_PS_SHIFT)) & TMR_SC_PS_MASK)")
        self.assertEqual(macros["TMR_CnSC_CHF_MASK"], "(0x80U)")

    def test_instances_and_sparse_array(self):
        macros = self.layout()["macros"]
        self.assertEqual(macros["TMR_INSTANCE_COUNT"], "(2u)")
        self.assertEqual(macros["IP_TMR0_BASE"], "(0x40000000u)")
        self.assertEqual(macros["IP_TMR1"], "((TMR_Type *)IP_TMR1_BASE)")
        self.assertEqual(macros["IP_TMR_BASE_PTRS"], "{ IP_TMR0, IP_TMR1 }")
        self.assertEqual(macros["CFG_CFGn_COUNT"], "4u")        # 0x0..0xC
        self.assertEqual(macros["CFG_TMR0_INDEX"], "1")
        self.assertEqual(macros["CFG_TMR1_INDEX"], "3")

    def test_interrupts_and_core_configuration(self):
        layout = self.layout()
        self.assertEqual(layout["irqs"],
                         {"NotAvail_IRQn": -128, "NonMaskableInt_IRQn": -14, "HardFault_IRQn": -13,
                          "SVCall_IRQn": -5, "PendSV_IRQn": -2, "SysTick_IRQn": -1,
                          "TMR0_OVF_IRQn": 3, "TIMER1_OVERFLOW_IRQn": 4})
        self.assertEqual(layout["macros"]["NUMBER_OF_INT_VECTORS"], "21")
        self.assertEqual(layout["macros"]["__NVIC_PRIO_BITS"], "2")
        self.assertIn("__VTOR_PRESENT", layout["macros"])
        self.assertIn("#define __FPU_PRESENT                   0", self.text)
        self.assertIn("Test licence: redistribution permitted.", self.text)   # licence notice carried over

    def test_cluster_stride_is_derived_from_the_second_element(self):
        """Without `stride` in the rule the step comes from the SVD (offset of element 1 minus element 0)."""
        layout = self.layout(rules=RULES.replace("        stride: 8\n", ""))
        members = dict((p, (o, s)) for p, o, s in layout["types"]["TMR_Type"])
        self.assertEqual(members["CONTROLS[1].CnSC"], (0x28, 4))
        self.assertEqual(layout["macros"]["TMR_CONTROLS_COUNT"], "2u")

    BANKS = "".join(f"""        <register>
          <name>BANK{b}VAL{v}</name><description>Bank value</description><addressOffset>{0x30 + 8 * b + 4 * v:#x}</addressOffset>
          <size>32</size><access>read-write</access>
          <fields><field><name>VAL</name><bitOffset>0</bitOffset><bitWidth>16</bitWidth></field></fields>
        </register>
""" for b in (1, 0) for v in (1, 0))
    BANK_RULES = RULES.replace("""          - {name: CnV, match: '^CHANNEL(\\d)_CV$'}
""", """          - {name: CnV, match: '^CHANNEL(\\d)_CV$'}
      - cluster: BANK
        stride: 8
        members:
          - {name: VAL, match: '^BANK(\\d)VAL(\\d)$', array: true}
      - cluster: ONE
        stride: 4
        members:
          - {name: CNT, match: '^CNT$', element: 0}
""")

    def test_cluster_with_an_array_member_and_a_fixed_element(self):
        """IO-62 PDB layout: CH[n].DLY[m] from flat CHnDLYm, POnDLY[0].PODLY from a register without index."""
        self.assertNotEqual(self.BANK_RULES, RULES)
        layout = self.layout(svd=SVD.replace("        <cluster>\n", self.BANKS + "        <cluster>\n", 1),
                             rules=self.BANK_RULES)
        members = dict((p, (o, s)) for p, o, s in layout["types"]["TMR_Type"])
        self.assertEqual(members["BANK[0].VAL[0]"], (0x30, 4))
        self.assertEqual(members["BANK[1].VAL[1]"], (0x3C, 4))
        self.assertEqual(members["ONE[0].CNT"], (0x8, 4))
        self.assertEqual(layout["macros"]["TMR_BANK_COUNT"], "2u")
        self.assertEqual(layout["macros"]["TMR_VAL_COUNT"], "2u")
        self.assertEqual(layout["macros"]["TMR_VAL_VAL_MASK"], "(0xFFFFU)")

    def test_cluster_array_member_needs_two_groups(self):
        rules = self.BANK_RULES.replace("'^BANK(\\d)VAL(\\d)$'", "'^BANK(\\d)VAL\\d$'")
        self.assertNotEqual(rules, self.BANK_RULES)
        with self.assertRaises(s2h.SvdError) as e:
            generate(self.tmp, svd=SVD.replace("        <cluster>\n", self.BANKS + "        <cluster>\n", 1),
                     rules=rules)
        self.assertIn("array member needs groups", str(e.exception))

    def test_generated_header_is_reproducible(self):
        self.assertEqual(generate(self.tmp), generate(self.tmp))

    @unittest.skipUnless(ARM_GCC, "arm-none-eabi-gcc not found")
    def test_generated_header_compiles_for_cortex_m0plus(self):
        header = self.tmp / "TESTMCU.h"
        header.write_text(generate(self.tmp), encoding="utf-8")
        source = self.tmp / "probe.c"
        source.write_text(textwrap.dedent("""
            #include "TESTMCU.h"
            unsigned probe(void)
            {
                IP_TMR0->SC = TMR_SC_PS(3) | TMR_SC_EN_MASK;
                IP_TMR1->CONTROLS[1].CnV = TMR_CnV_VAL(0x1234);
                IP_CFG->CFGn[CFG_TMR0_INDEX] |= CFG_CFGn_CGC_MASK;
                return IP_TMR0->CNT & TMR_CNT_COUNT_MASK;
            }
            volatile int irq = TMR0_OVF_IRQn;
            """), encoding="utf-8")
        out = subprocess.run([ARM_GCC, "-mcpu=cortex-m0plus", "-mthumb", "-std=c99", "-Wall", "-Wextra", "-Werror",
                              "-c", str(source), "-o", str(self.tmp / "probe.o"), f"-I{self.tmp}"],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)


class RefusesToGuessTest(unittest.TestCase):
    """Deliberate errors: the generator must report them instead of inventing a layout."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="svd2header-err-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def fails(self, message, svd=SVD, rules=RULES, derivative="TESTMCU"):
        with self.assertRaises(s2h.SvdError) as e:
            generate(self.tmp, svd, rules, derivative)
        self.assertIn(message, str(e.exception))

    def test_overlapping_registers_without_a_rule(self):
        svd = SVD.replace("<addressOffset>0x8</addressOffset>", "<addressOffset>0x0</addressOffset>", 1)
        self.fails("overlaps", svd)

    def test_instances_with_different_layouts(self):
        svd = SVD.replace('<name>TMR1</name>\n      <baseAddress>0x40001000</baseAddress>',
                          '<name>TMR1</name>\n      <baseAddress>0x40001000</baseAddress>\n'
                          '      <registers><register><name>SC</name><addressOffset>0x30</addressOffset>'
                          '<size>32</size></register></registers>')
        self.fails("register SC at 0x30", svd)

    def test_two_names_for_one_interrupt_number(self):
        svd = SVD.replace("<name>TMR1_OVF</name><description>Timer 1 overflow</description><value>4</value>",
                          "<name>TMR1_OTHER</name><description>x</description><value>3</value>")
        self.fails("named TMR0_OVF and TMR1_OTHER", svd)

    def test_interrupt_rename_without_a_matching_interrupt(self):
        rules = RULES.replace("TMR1_OVF: TIMER1_OVERFLOW", "TMR9_OVF: TIMER9")
        self.fails("['TMR9_OVF'] not in the SVD", rules=rules)

    def test_array_element_at_the_wrong_offset(self):
        svd = SVD.replace("<dimIncrement>0x4</dimIncrement>", "<dimIncrement>0x8</dimIncrement>", 1)
        rules = RULES.replace("""  CFG:
    layout:""", """  TMR:
    layout:
      - {array: DLY, match: '^DLY(\\d)$'}
  CFG:
    layout:""")
        self.fails("step", svd, rules.replace("""  TMR:
    layout:
      - cluster: CONTROLS""", """  TMRX:
    layout:
      - cluster: CONTROLS"""))

    def test_cluster_with_a_wrong_stride(self):
        self.fails("does not fit cluster CONTROLS", rules=RULES.replace("stride: 8", "stride: 4"))

    def test_cluster_member_missing_in_one_element(self):
        svd = SVD.replace("""          <register>
            <name>CV</name><description>Channel value</description><addressOffset>0x4</addressOffset>
            <size>32</size><access>read-write</access>
            <fields><field><name>VAL</name><bitOffset>0</bitOffset><bitWidth>16</bitWidth></field></fields>
          </register>
""", "")
        self.fails("member CnV missing", svd)

    def test_match_without_an_index_group(self):
        rules = RULES.replace("'^CHANNEL(\\d)_CSC$'", "'^CHANNEL\\d_CSC$'")
        self.assertNotEqual(rules, RULES)
        self.fails("needs a group for the index", rules=rules)

    def test_array_indices_not_starting_at_zero(self):
        rules = RULES.replace("- {sparse_array: CFGn, match: '^CFG_(\\w+)$', stride: 4, index_macro: 'CFG_{}_INDEX'}",
                              "- {array: CFGn, match: '^CFG_TMR(\\d)$', index_base: 1}")
        self.assertNotEqual(rules, RULES)
        self.fails("not numbered 0..n-1", rules=rules)

    def test_dim_index_does_not_match_dim(self):
        svd = SVD.replace("<dim>4</dim><dimIncrement>0x4</dimIncrement>",
                          "<dim>4</dim><dimIncrement>0x4</dimIncrement><dimIndex>0-2</dimIndex>")
        self.fails("dimIndex", svd)

    def test_unknown_core(self):
        self.fails("CPU 'CM33' not supported", SVD.replace("<name>CM0PLUS</name>", "<name>CM33</name>"))

    def test_derivative_without_rules(self):
        self.fails("no entry derivatives.OTHER", derivative="OTHER")

    def test_recorded_svd_checksum_must_match(self):
        rules = RULES.replace("svd: {file: test.svd}", "svd: {file: test.svd, sha256: '00ff'}")
        self.fails("differs from 00ff", rules=rules)

    def test_field_outside_the_register(self):
        svd = SVD.replace("<name>PS</name><description>Prescaler</description><bitOffset>4</bitOffset>",
                          "<name>PS</name><description>Prescaler</description><bitOffset>31</bitOffset>")
        self.fails("outside the 32-bit register", svd)

    def test_unknown_rules_key(self):
        with self.assertRaises(s2h.SvdError) as e:
            generate(self.tmp, rules=RULES + "\nunknown: 1\n")
        self.assertIn("unknown key", str(e.exception))


@unittest.skipUnless((OPENBSW_NXP / "S32K148_M4.svd").is_file(), "OpenBSW submodule not checked out")
class IrqVectorsTest(unittest.TestCase):
    """PLAT-12: external interrupt list of a derivative (startup/nxIrqVectors.inc) from its device header."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="svd2header-irq-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def vectors(self, handlers=None) -> list[str]:
        irqs = s2h.header_layout(generate(self.tmp))["irqs"]
        text = s2h.irq_vectors_inc(irqs, "TESTMCU", handlers)
        return [line.split("/*")[0].strip() for line in text.splitlines() if line.startswith("NX_IRQ")]

    def test_one_line_per_number_from_zero_with_gaps_reserved(self):
        # synthetic SVD: TMR0_OVF = 3, TMR1_OVF renamed TIMER1_OVERFLOW = 4; core exceptions are not listed
        self.assertEqual(self.vectors(), ["NX_IRQ_RESERVED()", "NX_IRQ_RESERVED()", "NX_IRQ_RESERVED()",
                                          "NX_IRQ_VECTOR(TMR0_OVF_IRQHandler)",
                                          "NX_IRQ_VECTOR(TIMER1_OVERFLOW_IRQHandler)"])

    def test_handler_override_of_the_family_port(self):
        self.assertEqual(self.vectors({"TMR0_OVF": "Safe_Tmr_IRQHandler"})[3], "NX_IRQ_VECTOR(Safe_Tmr_IRQHandler)")

    def test_two_names_for_one_number_are_rejected(self):
        with self.assertRaises(s2h.SvdError):
            s2h.irq_vectors_inc({"A_IRQn": 0, "B_IRQn": 0}, "x")

    def test_s32k148_header_of_openbsw(self):
        """The M4F header (not generated) gives the list as well: every number 0..NUMBER_OF_INT_VECTORS-17."""
        layout = s2h.header_layout(s2h.read_header(OPENBSW_NXP / "S32K148.h"))
        text = s2h.irq_vectors_inc(layout["irqs"], "S32K148")
        lines = [line for line in text.splitlines() if line.startswith("NX_IRQ")]
        self.assertEqual(len(lines), int(layout["macros"]["NUMBER_OF_INT_VECTORS"]) - 16)
        self.assertIn("NX_IRQ_VECTOR(CAN0_ORed_0_15_MB_IRQHandler)", text)
        self.assertIn("NX_IRQ_VECTOR(WDOG_EWM_IRQHandler)", text)

    def test_cli_writes_the_list_from_a_header(self):
        out = self.tmp / "startup" / "nxIrqVectors.inc"
        self.assertEqual(s2h.main(["irqvectors", "--header", str(OPENBSW_NXP / "S32K148.h"), "--out", str(out)]), 0)
        self.assertIn("NX_IRQ_VECTOR(LPUART0_RxTx_IRQHandler)", out.read_text(encoding="utf-8"))

    @unittest.skipUnless(os.environ.get("NX_SVD_DIR") and (Path(os.environ.get("NX_SVD_DIR", "")) /
                                                           "S32K116.svd").is_file(),
                         "S32K116.svd not found (NX_SVD_DIR, workstation level)")
    def test_s32k116_has_exactly_the_32_vectors_of_armv6m(self):
        rules = s2h.rules_path("s32k1xx")
        text, _ = s2h.generate("S32K116", rules, Path(os.environ["NX_SVD_DIR"]) / "S32K116.svd")
        inc = s2h.irq_vectors_inc(s2h.header_layout(text)["irqs"], "S32K116")
        lines = [line for line in inc.splitlines() if line.startswith("NX_IRQ")]
        self.assertEqual(len(lines), 32)
        self.assertIn("NX_IRQ_VECTOR(CAN0_ORed_0_31_MB_IRQHandler)", lines[11])
        self.assertIn("NX_IRQ_VECTOR(WDOG_IRQHandler)", lines[22])
        self.assertEqual(lines[27].split("/*")[0].strip(), "NX_IRQ_RESERVED()")


class S32K148ReferenceTest(unittest.TestCase):
    """The repository rules must reproduce the OpenBSW S32K148 header from the SVD OpenBSW ships with it."""

    @classmethod
    def setUpClass(cls):
        rules_file = s2h.rules_path("s32k1xx")
        cls.rules = s2h.load_rules(rules_file)
        cls.device = s2h.load_svd(OPENBSW_NXP / "S32K148_M4.svd")
        cls.text = s2h.render(cls.device, cls.rules, "S32K148", "S32K148_M4.svd", "-", rules_file.name)
        cls.generated = s2h.header_layout(cls.text)
        cls.reference_text = s2h.read_header(OPENBSW_NXP / "S32K148.h")
        cls.reference = s2h.header_layout(cls.reference_text)

    def test_symbols_used_by_the_drivers_match(self):
        used = set(s2h.used_symbols(self.reference_text))
        diffs = s2h.compare_layouts(self.generated, self.reference, symbols=used)
        self.assertEqual(diffs, KNOWN_S32K148_DIFFERENCES)

    def test_driver_symbols_are_found_in_the_sources(self):
        used = s2h.used_symbols(self.reference_text)
        self.assertGreater(len(used), 300)
        for symbol in ("FTM_CnSC_CHF_MASK", "PCC_FlexCAN0_INDEX", "LPSPI_Type", ".PCCn", "MPU_RGDAAC_M3SM"):
            self.assertIn(symbol, used, symbol)

    def test_layouts_of_the_modules_the_drivers_use_match(self):
        modules = {"ADC", "CRC", "ERM", "FLEXCAN", "FTFC", "FTM", "GPIO", "LMEM", "LPIT", "LPSPI", "LPUART", "MPU",
                   "PCC", "PORT", "RCM", "SCG", "SIM", "WDOG"}
        diffs = [d for d in s2h.compare_layouts(self.generated, self.reference, modules=modules)
                 if d not in KNOWN_S32K148_DIFFERENCES]
        # remaining differences must be fields or registers the SVD does not describe, never different names/offsets
        self.assertEqual([d for d in diffs if "missing" not in d], [])
        self.assertLess(len(diffs), 40, diffs)

    def test_instance_counts_and_pcc_indices(self):
        macros = self.generated["macros"]
        for name in ("FTM_INSTANCE_COUNT", "FLEXCAN_INSTANCE_COUNT", "LPSPI_INSTANCE_COUNT", "ADC_INSTANCE_COUNT",
                     "PCC_PCCn_COUNT", "PCC_FTM7_INDEX", "PCC_ENET_INDEX", "MPU_RGD_COUNT", "FLEXCAN_RAMn_COUNT"):
            self.assertEqual(macros.get(name), self.reference["macros"].get(name), name)


if __name__ == "__main__":
    unittest.main()
