"""Host tests of the KiCad netlist import (TOOL-76), Qt free.

Own profiles only (CLAUDE.md, review question 7): the invented derivative TESTMCU64 of tools/nxconfig/test/mcudb in
its invented package QFP64 (STM32-like names: PA11, FDCAN1, USART2), invented netlists of an invented board
(tools/nxconfig/test/netlist: KiCad 8 S-expression and KiCad 5 XML), and two derivatives built here in memory - one
with pin names that carry underscores and instances without an underscore in the signal (like a Cortex-R family),
one with a crystal and SWD pins. Never the eval board, never a board of the tree.

What is covered:
  - both KiCad formats read into the same tree; pin names from the node (KiCad 6+) or from libparts (KiCad 5);
  - the MCU is found by its pin names, a close second is warned about, `ref` decides, no pin names is an error;
  - per net a proposal: bus role and instance from the functions the pin can carry, analog, PWM pair, capture,
    digital input/output with low-active naming; debug nets, supply pins and unnamed nets are left out;
  - own rules (glob, regular expression, ignore, name template) and manual correction (kind, name) come first;
  - the grouped entries are checked against the pin multiplexing; a forced impossible function is refused;
  - the preview never replaces a row entered by hand unless the caller accepts it; an earlier import is updated;
  - the board file names the netlist and states the source per row, and the written board carries a project;
  - hints only: crystal and its value, debug connector, components on a bus (no device is created);
  - the command line `nxconfig board netlist` and `nxconfig board new --netlist`.
"""

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from nxconfig import netlist as nl  # noqa: E402
from nxconfig import newboard as nb  # noqa: E402
from nxconfig import newproject  # noqa: E402

TEST_MCUDB = HERE / "test" / "mcudb"
NETLISTS = HERE / "test" / "netlist"
NET = NETLISTS / "acme_ctrl.net"
XML = NETLISTS / "acme_ctrl_kicad5.xml"
RULES = NETLISTS / "rules.yaml"
MODULE_DIRS = (HERE.parents[1] / "libs" / "bsw", HERE.parents[1] / "libs" / "bsp")


def testmcu64() -> nb.BoardDraft:
    derivative = nb.find_derivative("TESTMCU64", TEST_MCUDB)
    package, messages = nb.load_package(derivative, "QFP64")
    assert package is not None and not messages, messages
    return nb.BoardDraft("acme_ctrl", derivative, package, description="ACME controller", supply_v=3.3,
                         sosc_hz=16_000_000, debug_interface="SWD", debug_probe="J-Link", adc_reference_mv=3300)


def memory_draft(name: str, signals: dict, instances: dict) -> nb.BoardDraft:
    """A derivative that exists only here (no file of the mcudb): enough for proposals and checks."""
    derivative = nb.DerivativeChoice(name, HERE / "test" / f"{name}.yaml", "memfam", "cortex-r4", ("PKG",))
    return nb.BoardDraft(name.lower(), derivative, nb.Package(derivative, "PKG", signals, True, instances))


def sexpr(components: str, nets: str) -> str:
    return f'(export (version "E") (design (tool "Eeschema 8.0.4")) (components {components}) (nets {nets}))'


def by_net(suggestion) -> dict:
    return {p.net: p for p in suggestion.proposals}


class ReadingTest(unittest.TestCase):
    def test_sexpr_and_xml_read_into_the_same_tree(self):
        net = nl.read(NET)
        self.assertEqual(net.format, "sexpr")
        self.assertEqual(net.tool, "Eeschema 8.0.4")
        self.assertEqual(net.components["U2"].value, "TJA1051T/3")
        can_rx = next(n for n in net.nets if n.label == "CAN_VEH_RX")
        self.assertEqual(can_rx.name, "/CAN_VEH_RX")
        self.assertEqual({(n.ref, n.pin) for n in can_rx.nodes}, {("U1", "44"), ("U2", "4")})
        xml = nl.read(XML)
        self.assertEqual(xml.format, "xml")
        node = next(n for n in xml.nets if n.label == "LED_ERR_N").nodes[0]
        self.assertEqual(node.name, "")                         # KiCad 5: no pinfunction at the node ...
        self.assertEqual(xml.pin_name(node), "PC13")            # ... the name comes from libparts

    def test_sheet_path_escapes_and_unnamed_nets(self):
        net = nl.parse(sexpr('(comp (ref "U1") (value "X"))',
                             '(net (code "1") (name "/sheet a/SBC_\\"CS\\"") (node (ref "U1") (pin "1")))'
                             '(net (code "2") (name "Net-(U1-Pad2)") (node (ref "U1") (pin "2")))'
                             '(net (code "3") (name "unconnected-(U1-PA0-Pad3)") (node (ref "U1") (pin "3")))'))
        self.assertEqual(net.nets[0].label, 'SBC_"CS"')
        self.assertEqual([n.named for n in net.nets], [True, False, False])

    def test_what_is_no_kicad_netlist_is_refused_with_the_reason(self):
        for text, reason in (("", "neither"), ("(export (nets", "unbalanced"), ("(kicad_sch)", "no (export"),
                             ("<eagle/>", "not a KiCad netlist"), ("<export><nets>", "XML cannot be read"),
                             ('(export (design (tool "unterminated)))', "unterminated")):
            with self.subTest(text=text), self.assertRaises(nl.NetlistError) as caught:
                nl.parse(text)
            self.assertIn(reason, str(caught.exception))
        with self.assertRaises(nl.NetlistError) as caught:
            nl.read(NETLISTS / "missing.net")
        self.assertIn("missing.net", str(caught.exception))

    def test_pin_names_resolve_against_the_package(self):
        pins = {"PTE4", "N2HET1_15", "CAN1RX", "PA0"}
        for name, pin in (("PTE4", "PTE4"), ("PTE4/CAN0_RX", "PTE4"), ("pte4 (CAN0_RX)", "PTE4"),
                          ("N2HET1_15/ECAP1", "N2HET1_15"), ("~{CAN1RX}", "CAN1RX"), ("VDD", ""),
                          ("PTE", ""), ("N2HET1", "")):
            with self.subTest(name=name):
                self.assertEqual(nl.resolve_pin(name, pins), pin)


class McuTest(unittest.TestCase):
    def setUp(self):
        self.draft = testmcu64()
        self.netlist = nl.read(NET)

    def test_the_mcu_is_the_component_with_the_pin_names_of_the_package(self):
        ref, messages = nl.find_mcu(self.netlist, self.draft.package)
        self.assertEqual((ref, messages), ("U1", []))

    def test_ref_decides_and_a_wrong_ref_says_why(self):
        self.assertEqual(nl.find_mcu(self.netlist, self.draft.package, "U1")[0], "U1")
        ref, messages = nl.find_mcu(self.netlist, self.draft.package, "U9")
        self.assertEqual(ref, "")
        self.assertIn("U9 is not in the netlist", messages[0].text)
        ref, messages = nl.find_mcu(self.netlist, self.draft.package, "U2")
        self.assertIn("no pin name of U2", messages[0].text)

    def test_a_close_second_candidate_is_warned_about(self):
        pins = "".join(f'(pin (num "{i}") (name "PA{i}"))' for i in range(1, 8))
        text = sexpr('(comp (ref "U1") (value "TESTMCU64") (libsource (lib "a") (part "M")))'
                     '(comp (ref "U7") (value "OTHER") (libsource (lib "a") (part "M")))',
                     "").replace("(nets )", f'(libparts (libpart (lib "a") (part "M") (pins {pins}))) (nets )')
        ref, messages = nl.find_mcu(nl.parse(text), self.draft.package)
        self.assertEqual(ref, "U1")                             # the value names the derivative
        self.assertIn("U7", messages[0].text)
        self.assertEqual(messages[0].severity, "warning")

    def test_a_symbol_without_pin_names_is_an_error_with_the_way_out(self):
        text = sexpr('(comp (ref "U1") (value "TESTMCU64"))',
                     '(net (code "1") (name "/CAN_RX") (node (ref "U1") (pin "44")))')
        suggestion = nl.suggest(nl.parse(text), self.draft)
        self.assertEqual(suggestion.mcu, "")
        self.assertIn("pin names, not numbers", suggestion.messages[0].text)
        self.assertEqual(nl.regroup(suggestion, self.draft).changes, [])


class ProposalTest(unittest.TestCase):
    def setUp(self):
        self.draft = testmcu64()
        self.suggestion = nl.suggest(nl.read(NET), self.draft)
        self.p = by_net(self.suggestion)

    def test_every_pin_on_a_named_net_gets_a_proposal(self):
        expected = {
            "CAN_VEH_RX": ("can", "CAN_VEH", "rx", "FDCAN1"), "CAN_VEH_TX": ("can", "CAN_VEH", "tx", "FDCAN1"),
            "CON_TXD": ("uart", "CON", "tx", "USART2"), "CON_RXD": ("uart", "CON", "rx", "USART2"),
            "SBC_SCK": ("spi", "SBC", "sck", "SPI1"), "SBC_MISO": ("spi", "SBC", "sin", "SPI1"),
            "SBC_MOSI": ("spi", "SBC", "sout", "SPI1"), "SBC_~{CS}": ("spi", "SBC", "pcs", "SPI1"),
            "LED_ERR_N": ("dio_out", "LED_ERR_N", "pin", ""), "BTN_START": ("dio_in", "BTN_START", "pin", ""),
            "VBAT_SENSE": ("adc", "VBAT_SENSE", "pin", "ADC1"), "PWM_U_HS": ("pwm", "PWM_U", "UH", "TIM1"),
            "PWM_U_LS": ("pwm", "PWM_U", "UL", "TIM1"), "HALL_A": ("input_capture", "HALL_A", "pin", "TIM2"),
        }
        got = {net: (p.kind, p.name, p.role, p.instance) for net, p in self.p.items() if p.kind}
        self.assertEqual(got, expected)
        self.assertEqual(self.p["SBC_~{CS}"].index, 0)
        self.assertEqual(self.p["VBAT_SENSE"].channel, "IN1")
        self.assertEqual((self.p["PWM_U_HS"].channel, self.p["PWM_U_LS"].channel), ("CH0", "CH1"))

    def test_active_level_follows_the_naming_convention(self):
        self.assertEqual(self.p["LED_ERR_N"].active, "low")
        self.assertEqual(self.p["BTN_START"].active, "high")
        for net, low in (("~{EN}", True), ("NFAULT", True), ("LED_RED", False), ("RESET#", True),
                         ("NET_OUT", False), ("EN_B", True)):
            with self.subTest(net=net):
                self.assertEqual(nl._active_low(net), low)

    def test_what_is_no_board_entry_is_left_out_with_the_reason(self):
        self.assertEqual(self.p["TP5"].kind, "")
        self.assertIn("choose the kind by hand", self.p["TP5"].reason)
        self.assertEqual(self.p["SWDIO"].kind, "")
        self.assertIn("debug net", self.p["SWDIO"].reason)
        skipped = {(net, why.split(" ")[0]) for net, _num, _name, why in self.suggestion.skipped}
        self.assertEqual(skipped, {("GND", "not"), ("Net-(U1-PB2)", "net")})

    def test_a_pin_without_the_role_in_the_net_name_is_no_bus(self):
        """PA11 can carry FDCAN1_RX - but a LED on it is a LED, not half a CAN bus."""
        p = nl.classify(nl.Proposal("LED_GREEN", "PA11"), self.draft.package)
        self.assertEqual((p.kind, p.name, p.active), ("dio_out", "LED_GREEN", "high"))

    def test_hints_name_what_the_wizard_decides(self):
        hints = "\n".join(self.suggestion.hints)
        self.assertIn("debug: PA13 (SWDIO) goes to J1", hints)
        self.assertIn("U2 (TJA1051T/3) on CAN_VEH", hints)
        self.assertIn("U3 (ACME9000) on SBC", hints)
        self.assertNotIn("J2", hints)                            # a connector is no component with a role


class RuleTest(unittest.TestCase):
    def setUp(self):
        self.draft = testmcu64()

    def test_rules_from_a_file_come_before_the_patterns(self):
        rules = nl.read_rules(RULES)
        p = by_net(nl.suggest(nl.read(NET), self.draft, rules=rules))
        self.assertEqual((p["TP5"].kind, p["TP5"].name), ("dio_out", "TP5_OUT"))
        self.assertEqual(p["HALL_A"].kind, "")
        self.assertIn("ignore", p["HALL_A"].reason)
        self.assertEqual({p["CAN_VEH_RX"].name, p["CAN_VEH_TX"].name}, {"VEHICLE"})
        self.assertIn("rule re:CAN_VEH_(RX|TX)", p["CAN_VEH_RX"].reason)

    def test_a_rule_cannot_force_a_function_the_pin_does_not_have(self):
        p = by_net(nl.suggest(nl.read(NET), self.draft, rules=[nl.rule_from_text("LED_ERR_N=can")]))
        self.assertEqual(p["LED_ERR_N"].kind, "")
        self.assertIn("cannot carry a can function", p["LED_ERR_N"].reason)

    def test_bad_rules_are_refused(self):
        for text, reason in (("TP5", "expected NET=KIND"), ("TP5=motor", "unknown kind"),
                             ("re:[=dio_out", "unterminated")):
            with self.subTest(text=text), self.assertRaises(nl.NetlistError) as caught:
                nl.rule_from_text(text)
            self.assertIn(reason, str(caught.exception))
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "rules.yaml"
            bad.write_text("rules:\n  - { net: A, kind: dio_in, colour: red }\n", encoding="utf-8")
            with self.assertRaises(nl.NetlistError) as caught:
                nl.read_rules(bad)
            self.assertIn("colour", str(caught.exception))

    def test_manual_correction_of_kind_and_name(self):
        suggestion = nl.suggest(nl.read(NET), self.draft)
        proposals = suggestion.proposals
        tp5 = next(i for i, p in enumerate(proposals) if p.net == "TP5")
        proposals[tp5] = nl.reassign(proposals[tp5], nb.DIO_IN, self.draft.package)
        self.assertEqual((proposals[tp5].kind, proposals[tp5].name), ("dio_in", "TP5"))
        self.assertTrue(proposals[tp5].reason.startswith("by hand"))
        can_rx = next(p for p in proposals if p.net == "CAN_VEH_RX")
        nl.rename(proposals, can_rx, "VEH")                     # the other pin of the bus follows
        self.assertEqual({p.name for p in proposals if p.kind == "can"}, {"VEH"})
        hall = next(i for i, p in enumerate(proposals) if p.net == "HALL_A")
        proposals[hall] = nl.reassign(proposals[hall], "", self.draft.package)
        imp = nl.regroup(suggestion, self.draft)
        names = {(c.entry.kind, c.entry.name) for c in imp.changes}
        self.assertIn(("can", "VEH"), names)
        self.assertIn(("dio_in", "TP5"), names)
        self.assertNotIn(("input_capture", "HALL_A"), names)


class EntriesAndPreviewTest(unittest.TestCase):
    def setUp(self):
        self.draft = testmcu64()

    def test_entries_pass_the_pin_multiplexing_check(self):
        imp = nl.prepare(NET, self.draft)
        self.assertEqual(imp.errors, [])
        self.assertEqual(nl.summary(imp.changes), {"add": 8, "same": 0, "replace": 0, "conflict": 0, "error": 0})
        spi = next(c.entry for c in imp.changes if c.entry.kind == nb.SPI)
        self.assertEqual([(p.role, p.pin, p.function, p.index) for p in spi.pins],
                         [("sck", "PA5", "SPI1_SCK", None), ("sin", "PA6", "SPI1_SIN", None),
                          ("sout", "PA7", "SPI1_SOUT", None), ("pcs", "PA4", "SPI1_PCS0", 0)])
        self.assertTrue(all(c.entry.source_id == nb.KICAD.id for c in imp.changes))
        self.assertEqual(spi.origin, "acme_ctrl.net, nets SBC_MISO, SBC_MOSI, SBC_SCK, SBC_~{CS}")

    def test_a_corrected_pin_the_package_cannot_carry_is_an_error(self):
        suggestion = nl.suggest(nl.read(NET), self.draft)
        led = next(p for p in suggestion.proposals if p.net == "LED_ERR_N")
        led.pin = "PC14"                                        # manual correction to a pin without that function
        led.kind, led.name, led.instance, led.channel = nb.ADC, "LEVEL", "ADC1", "IN1"
        imp = nl.regroup(suggestion, self.draft)
        change = next(c for c in imp.changes if c.entry.name == "LEVEL")
        self.assertEqual(change.action, nl.ERROR)
        self.assertIn("cannot carry ADC1_IN1", change.reason)
        nl.apply(self.draft, imp.changes)
        self.assertNotIn("LEVEL", [e.name for e in self.draft.entries])

    def test_nothing_typed_in_is_replaced_without_being_accepted(self):
        # by hand: an output LED on PC13 (the netlist has LED_ERR_N there) and a CAN bus named CAN_VEH
        self.assertEqual(self.draft.add(nb.dio(nb.DIO_OUT, "LED", "PC13", "low")), [])
        self.assertEqual(self.draft.add(nb.bus(nb.CAN, "CAN_VEH", "FDCAN2", {"rx": "PB12", "tx": "PB13"})), [])
        self.assertEqual(self.draft.add(nb.dio(nb.DIO_IN, "BTN_START", "PB0")), [])      # identical row
        imp = nl.prepare(NET, self.draft)
        actions = {c.entry.name: c.action for c in imp.changes}
        self.assertEqual(actions["LED_ERR_N"], nl.CONFLICT)
        self.assertEqual(actions["CAN_VEH"], nl.CONFLICT)
        self.assertEqual(actions["BTN_START"], nl.SAME)
        self.assertEqual(actions["CON"], nl.ADD)
        led = next(c for c in imp.changes if c.entry.name == "LED_ERR_N")
        self.assertIn("entered by hand", led.reason)
        self.assertEqual([e.name for e in led.displaced], ["LED"])

        done = nl.apply(self.draft, imp.changes)                # no accept: the typed rows stay as they are
        names = {e.name: e for e in self.draft.entries}
        self.assertEqual(names["LED"].pins[0].pin, "PC13")
        self.assertEqual(names["CAN_VEH"].instance, "FDCAN2")
        self.assertNotIn("LED_ERR_N", names)
        self.assertEqual(len(done), 5)                          # 8 entries: 2 conflicts, 1 identical

        imp = nl.prepare(NET, self.draft)                       # second run: the import is there, only conflicts
        self.assertEqual(nl.summary(imp.changes), {"add": 0, "same": 6, "replace": 0, "conflict": 2, "error": 0})
        led = next(c for c in imp.changes if c.entry.name == "LED_ERR_N")
        done = nl.apply(self.draft, imp.changes, accept={led.key})
        self.assertEqual([c.entry.name for c in done], ["LED_ERR_N"])
        names = {e.name: e for e in self.draft.entries}
        self.assertNotIn("LED", names)
        self.assertEqual(names["LED_ERR_N"].source_id, nb.KICAD.id)
        self.assertEqual(names["CAN_VEH"].instance, "FDCAN2")    # not accepted: still the typed one

    def test_an_earlier_import_is_updated_not_asked_for(self):
        nl.apply(self.draft, nl.prepare(NET, self.draft).changes)
        suggestion = nl.suggest(nl.read(NET), self.draft)
        led = next(p for p in suggestion.proposals if p.net == "LED_ERR_N")
        led.pin = "PC15"                                        # the schematic moved the LED
        imp = nl.regroup(suggestion, self.draft)
        change = next(c for c in imp.changes if c.entry.name == "LED_ERR_N")
        self.assertEqual(change.action, nl.REPLACE)
        nl.apply(self.draft, imp.changes)
        self.assertEqual([e.pins[0].pin for e in self.draft.entries if e.name == "LED_ERR_N"], ["PC15"])

    def test_grouping_findings(self):
        suggestion = nl.suggest(nl.read(NET), self.draft)
        p = by_net(suggestion)
        p["CAN_VEH_TX"].kind = ""                                # half a bus
        p["PWM_U_LS"].kind = ""                                  # half a pair
        p["BTN_START"].name = "LED_ERR_N"                         # two pins, one single-pin entry
        p["BTN_START"].kind = nb.DIO_OUT
        imp = nl.regroup(suggestion, self.draft)
        texts = "\n".join(m.text for m in imp.messages)
        self.assertIn("CAN_VEH: no net for tx", texts)
        self.assertIn("pair(s) U without a high and a low side", texts)
        self.assertIn("LED_ERR_N: 2 pins", texts)


class BoardFileTest(unittest.TestCase):
    def test_board_file_names_the_netlist_and_the_source_per_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draft = testmcu64()
            draft.repo = root
            draft.schematic = NET.name
            nl.apply(draft, nl.prepare(NET, draft).changes)
            draft.add(nb.dio(nb.DIO_OUT, "RELAY", "PB1"))
            result = nb.create(nb.plan(draft, root / "boards" / "acme_ctrl", root, port=False), TEST_MCUDB,
                               MODULE_DIRS)
            self.assertEqual([str(m) for m in result.errors], [])
            text = result.board_file.read_text(encoding="utf-8")
            self.assertIn("[SCH] KiCad netlist acme_ctrl.net", text)
            self.assertIn("# from KiCad netlist (acme_ctrl.net, net LED_ERR_N)", text)
            self.assertIn("# from KiCad netlist (acme_ctrl.net, nets CAN_VEH_RX, CAN_VEH_TX)", text)
            relay = text.index("name: RELAY")
            self.assertNotIn("KiCad", text[text.rindex("\n", 0, text.rindex("\n", 0, relay)):relay])
            readme = (root / "boards" / "acme_ctrl" / "README.md").read_text(encoding="utf-8")
            self.assertIn("read off `acme_ctrl.net`", readme)
            # the board reads back, and an empty project on it passes nxconfig (create() proved it already)
            back, messages = nb.draft_from_file(result.board_file, TEST_MCUDB, root)
            self.assertEqual({e.name for e in back.entries},
                             {"CAN_VEH", "CON", "SBC", "LED_ERR_N", "RELAY", "BTN_START", "VBAT_SENSE", "PWM_U",
                              "HALL_A"})
            info = newproject.BoardInfo("acme_ctrl", result.board_file, "TESTMCU64", port=False)
            plan = newproject.plan_empty("acme_app", info, root / "projects" / "acme_app", root, mcudb=TEST_MCUDB)
            self.assertEqual(newproject.create(plan).errors, [])

    def test_a_board_without_netlist_reads_as_before(self):
        draft = testmcu64()
        draft.add(nb.dio(nb.DIO_OUT, "LED", "PC13"))
        text = nb.render_board(draft)
        self.assertIn("[SCH] the schematic of this board - **not stated yet**", text)
        self.assertNotIn("from KiCad", text)


class OtherFamiliesTest(unittest.TestCase):
    """Review question 3: nothing of the import knows a family - two naming styles built here in memory."""

    def test_pin_names_with_underscores_and_instances_without_underscore_suffix(self):
        signals = {"GPIO": {"N2HET1_15": 0, "GIOA_2": 0}, "DCAN1_RX": {"CAN1RX": {"fixed": 1}},
                   "DCAN1_TX": {"CAN1TX": {"fixed": 1}}, "N2HET1_CH15": {"N2HET1_15": 0}}
        instances = {"DCAN1": {"class": "can"}, "N2HET1": {"class": "timer"}}
        draft = memory_draft("MEMR4", signals, instances)
        text = sexpr('(comp (ref "IC4") (value "MEMR4"))',
                     '(net (code "1") (name "/DCAN1_RX") (node (ref "IC4") (pin "A1") (pinfunction "CAN1RX")))'
                     '(net (code "2") (name "/DCAN1_TX") (node (ref "IC4") (pin "A2") (pinfunction "CAN1TX")))'
                     '(net (code "3") (name "/HALL_U") (node (ref "IC4") (pin "B7") (pinfunction "N2HET1_15/ECAP")))'
                     '(net (code "4") (name "/~{FAULT}") (node (ref "IC4") (pin "C3") (pinfunction "GIOA_2")))')
        imp = nl.regroup(nl.suggest(nl.parse(text), draft), draft)
        self.assertEqual(imp.errors, [])
        got = {(c.entry.kind, c.entry.name, c.entry.instance, tuple(c.entry.pin_names())) for c in imp.changes}
        self.assertEqual(got, {("can", "DCAN1", "DCAN1", ("CAN1RX", "CAN1TX")),
                               ("input_capture", "HALL_U", "N2HET1", ("N2HET1_15",)),
                               ("dio_in", "FAULT", "", ("GIOA_2",))})
        fault = next(c.entry for c in imp.changes if c.entry.name == "FAULT")
        self.assertEqual(fault.active, "low")

    def test_crystal_hint_with_its_value_but_nothing_set(self):
        signals = {"GPIO": {"P0": 0, "P1": 0, "P2": 0}, "OSC_EXTAL": {"P0": 0}, "OSC_XTAL": {"P1": 0},
                   "SWD_CLK": {"P2": 0}}
        draft = memory_draft("MEMM0", signals, {})
        text = sexpr('(comp (ref "U1") (value "MEMM0")) (comp (ref "Y1") (value "8MHz")) '
                     '(comp (ref "J3") (value "Conn_SWD"))',
                     '(net (code "1") (name "/EXTAL") (node (ref "U1") (pin "1") (pinfunction "P0"))'
                     '  (node (ref "Y1") (pin "1")))'
                     '(net (code "2") (name "/XTAL") (node (ref "U1") (pin "2") (pinfunction "P1"))'
                     '  (node (ref "Y1") (pin "2")))'
                     '(net (code "3") (name "/SWCLK") (node (ref "U1") (pin "3") (pinfunction "P2"))'
                     '  (node (ref "J3") (pin "2")))')
        suggestion = nl.suggest(nl.parse(text), draft)
        self.assertEqual([p.kind for p in suggestion.proposals], ["", "", ""])
        hints = "\n".join(suggestion.hints)
        self.assertIn("crystal: Y1 (8MHz) on P0 - 8000000 Hz?", hints)
        self.assertEqual(hints.count("Y1"), 1)
        self.assertIn("debug: P2 (SWCLK) goes to J3", hints)
        self.assertIsNone(draft.sosc_hz)                        # a hint, never a value

    def test_frequencies(self):
        for value, hz in (("8MHz", 8_000_000), ("16M", 16_000_000), ("32.768kHz", 32_768), ("7,3728 MHz", 7_372_800),
                          ("10k", None), ("TJA1051", None), ("", None)):
            with self.subTest(value=value):
                self.assertEqual(nl.frequency_of(value), hz)


class CommandLineTest(unittest.TestCase):
    def run_cli(self, *argv) -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stdout(out):
            code = nb.main(list(argv))
        return code, out.getvalue()

    def test_board_netlist_shows_proposals_preview_and_hints(self):
        code, out = self.run_cli("netlist", str(NET), "--mcu", "TESTMCU64", "--mcudb", str(TEST_MCUDB),
                                 "--map", "TP5=dio_out:TESTPOINT", "--rules", str(RULES))
        self.assertEqual(code, 0, out)
        self.assertIn("MCU U1", out)
        self.assertIn("add: Digitaler Ausgang TESTPOINT (PB1)", out)
        self.assertIn("rule HALL_* -> ignore", out)
        self.assertIn("summary: 8 add", out)
        self.assertIn("left to the wizard", out)

    def test_board_netlist_against_a_board_shows_conflicts_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draft = testmcu64()
            draft.repo = root
            draft.add(nb.dio(nb.DIO_OUT, "LED", "PC13", "low"))
            draft.add(nb.dio(nb.DIO_IN, "SW", "PB0"))
            draft.add(nb.bus(nb.UART, "CON", "USART2", {"rx": "PA3", "tx": "PA2"}))
            draft.add(nb.bus(nb.CAN, "VEH", "FDCAN1", {"rx": "PA11", "tx": "PA12"}))
            result = nb.create(nb.plan(draft, root / "boards" / "acme_ctrl", root, port=False), TEST_MCUDB,
                               MODULE_DIRS)
            before = result.board_file.read_text(encoding="utf-8")
            code, out = self.run_cli("netlist", str(NET), "--board", str(result.board_file), "--mcudb",
                                     str(TEST_MCUDB))
            self.assertEqual(code, 0, out)
            self.assertIn("conflict: Digitaler Ausgang LED_ERR_N (PC13) - would replace what was entered by hand",
                          out)
            self.assertIn("same: UART (Konsole, LIN) CON", out)
            self.assertIn("nothing written", out)
            self.assertEqual(result.board_file.read_text(encoding="utf-8"), before)

    def test_board_new_with_netlist_writes_a_board_that_carries_a_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self.run_cli("new", "acme_ctrl", "--mcu", "TESTMCU64", "--package", "QFP64", "--mcudb",
                                     str(TEST_MCUDB), "--root", tmp, "--netlist", str(XML), "--supply-v", "3.3",
                                     "--sosc-hz", "16000000", "--debug-interface", "SWD",
                                     "--adc-reference-mv", "3300", "--out", "RELAY=PB1", "--in", "SW=PB0",
                                     "--uart", "CON=USART2", "--no-port")
            self.assertEqual(code, 0, out)
            self.assertIn("0 error(s) in a project on it", out)
            text = (Path(tmp) / "boards" / "acme_ctrl" / "board.yaml").read_text(encoding="utf-8")
            self.assertIn("[SCH] KiCad netlist acme_ctrl_kicad5.xml", text)
            self.assertIn("name: CAN_VEH", text)
            self.assertIn("{ name: LED_ERR_N, pin: PC13, active: low, verify: true }", text)

    def test_errors_of_the_command_line(self):
        code, out = self.run_cli("netlist", str(NET))
        self.assertEqual(code, 1)
        self.assertIn("--mcu (with --package) or --board is needed", out)
        code, out = self.run_cli("netlist", str(NETLISTS / "rules.yaml"), "--mcu", "TESTMCU64", "--mcudb",
                                 str(TEST_MCUDB))
        self.assertEqual(code, 1)
        self.assertIn("rules.yaml: neither an S-expression nor an XML file", out)
        code, out = self.run_cli("netlist", str(NET), "--mcu", "TESTMCU64", "--mcudb", str(TEST_MCUDB),
                                 "--map", "LED_ERR_N=adc")
        self.assertEqual(code, 0, out)                          # a rule that fits no function leaves the pin out
        self.assertIn("cannot carry a adc function", out)


if __name__ == "__main__":
    unittest.main()
