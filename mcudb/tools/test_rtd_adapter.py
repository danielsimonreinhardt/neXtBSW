"""Tests of mcudb/tools/rtd_adapter.py (PLAT-32): RTD device headers from hal_nxp and the adapter to driver names.

Run: .venv/Scripts/python -m unittest mcudb/tools/test_rtd_adapter.py
"""

import copy
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import rtd_adapter as ra  # noqa: E402
import svd2header as s2h  # noqa: E402

RULES_FILE = ra.rules_path("s32k1xx")


class RepositoryStateTest(unittest.TestCase):
    """What is checked in: vendor files as recorded, adapter up to date, every driver symbol resolves."""

    @classmethod
    def setUpClass(cls):
        cls.rules = ra.load_rules(RULES_FILE)

    def test_check_passes(self):
        self.assertEqual(ra.main(["check", "--derivative", "S32K116"]), 0)

    def test_vendor_files_are_the_recorded_bsd3_files(self):
        self.assertEqual(ra.verify_vendor_files(self.rules, "S32K116"), [])
        files = self.rules["derivatives"]["S32K116"]["files"]
        self.assertEqual(len(files), 40)
        for name in files:
            text = (ra.vendor_dir(self.rules) / name).read_text(encoding="utf-8")
            self.assertIn("SPDX-License-Identifier: BSD-3-Clause", text, name)
            self.assertNotIn("Apache", text, name)
            self.assertNotIn("Confidential", text, name)

    def test_adapter_matches_the_rules(self):
        text = ra.render(self.rules, "S32K116", RULES_FILE)
        self.assertEqual(ra.adapter_path(self.rules, "S32K116").read_text(encoding="utf-8"), text)
        self.assertIn("SPDX-License-Identifier: BSD-3-Clause", text)
        self.assertIn('#include "rtd/S32K116.h"', text)

    def test_every_driver_symbol_resolves_or_is_absent_with_reason(self):
        report, stale = ra.symbol_report(self.rules, "S32K116")
        self.assertEqual([r for r in report if r[1] == "MISSING"], [])
        self.assertEqual(stale, [])
        statuses = {sym: status for sym, status, _ in report}
        for sym in ("FLEXCAN_Type", "FLEXCAN_MCR_HALT_MASK", ".RAMn", "ADC_SC1A_ADCH", ".EARn", ".PIDR",
                    "PCC_FlexCAN0_INDEX", "__NVIC_PRIO_BITS"):
            self.assertEqual(statuses.get(sym), "ok", sym)
        self.assertTrue(statuses["IP_LMEM"].startswith("absent"))
        self.assertTrue(statuses["SIM_CHIPCTL_PDB_BB_SEL_MASK"].startswith("absent"))   # PLAT-48: guarded in the port
        self.assertGreater(len(report), 400)

    def test_interrupts_read_through_the_adapter(self):
        """tools/bswgen reads the interrupt list of the derivative from this header (PLAT-12)."""
        layout = s2h.header_layout(s2h.read_header(ra.adapter_path(self.rules, "S32K116")))
        irqs = layout["irqs"]
        self.assertEqual(len(s2h.external_irqs(irqs)), 31)
        self.assertEqual(irqs["CAN0_ORed_0_31_MB_IRQn"], 11)
        self.assertEqual(irqs["WDOG_IRQn"], 22)
        self.assertEqual(irqs["LPUART0_RxTx_IRQn"], 31)
        self.assertNotIn("WDOG_EWM_IRQn", irqs)

    def test_no_workstation_file_needed(self):
        """Neither the rules nor the adapter point at NX_SVD_DIR or an SVD file."""
        text = RULES_FILE.read_text(encoding="utf-8") + ra.adapter_path(self.rules, "S32K116").read_text("utf-8")
        self.assertFalse("NX_SVD_DIR" in text, "NX_SVD_DIR named")
        includes = [line for line in text.splitlines() if line.startswith("#include")]
        self.assertEqual(includes, ['#include "rtd/S32K116.h"'])


class DetectionTest(unittest.TestCase):
    """Each check must fail on a deliberately broken input (otherwise it proves nothing)."""

    def setUp(self):
        self.rules = ra.load_rules(RULES_FILE)

    def broken(self):
        return copy.deepcopy(self.rules)

    def test_changed_vendor_file(self):
        rules = self.broken()
        rules["derivatives"]["S32K116"]["files"]["S32K116_FTM.h"] = "0" * 64
        problems = ra.verify_vendor_files(rules, "S32K116")
        self.assertEqual(len(problems), 1)
        self.assertIn("S32K116_FTM.h: SHA-256", problems[0])

    def test_changed_license_file(self):
        rules = self.broken()
        rules["origin"]["license_file"]["sha256"] = "1" * 64
        self.assertIn("LICENSE.BSD-3: SHA-256", " ".join(ra.verify_vendor_files(rules, "S32K116")))

    def test_unrecorded_vendor_file(self):
        rules = self.broken()
        del rules["derivatives"]["S32K116"]["files"]["S32K116_PDB.h"]
        self.assertIn("S32K116_PDB.h: not recorded", " ".join(ra.verify_vendor_files(rules, "S32K116")))

    def test_outdated_adapter(self):
        rules = self.broken()
        rules["derivatives"]["S32K116"]["core"]["__NVIC_PRIO_BITS"]["value"] = "3U"
        text = ra.render(rules, "S32K116", RULES_FILE)
        self.assertNotEqual(ra.adapter_path(rules, "S32K116").read_text(encoding="utf-8"), text)

    def test_replacement_of_other_size(self):
        rules = self.broken()
        rules["modules"]["FLEXCAN"]["members"][0]["replace"] = "__IO uint32_t RAMn[64];"
        with self.assertRaisesRegex(ra.RtdError, "has 256 bytes, the vendor member 512"):
            ra.render(rules, "S32K116", RULES_FILE)

    def test_replacement_that_matches_nothing(self):
        rules = self.broken()
        rules["modules"]["ERM"]["members"][0]["match"] = r"__I\s+uint32_t\s+EARX\[1\];"
        with self.assertRaisesRegex(ra.RtdError, "0 matches in ERM_Type"):
            ra.render(rules, "S32K116", RULES_FILE)

    def test_appended_member_at_the_wrong_offset(self):
        rules = self.broken()
        rules["modules"]["GPIO"]["append"][0]["offset"] = 0x1C
        with self.assertRaisesRegex(ra.RtdError, "appended member PIDR lies at 0x18, rules say 28"):
            ra.render(rules, "S32K116", RULES_FILE)

    def test_absent_instance_unknown_to_the_vendor_header(self):
        rules = self.broken()
        rules["derivatives"]["S32K116"]["absent"]["ENET"] = "test"
        with self.assertRaisesRegex(ra.RtdError, "no IP_ENET"):
            ra.render(rules, "S32K116", RULES_FILE)

    def test_compat_macro_the_vendor_defines(self):
        rules = self.broken()
        rules["derivatives"]["S32K116"].setdefault("compat", {})["SIM_CHIPCTL_CLKOUTEN_MASK"] = {"value": "1", "why": "test"}
        with self.assertRaisesRegex(ra.RtdError, "the vendor header defines it"):
            ra.render(rules, "S32K116", RULES_FILE)

    def test_unresolved_driver_symbol(self):
        rules = self.broken()
        patterns = rules["derivatives"]["S32K116"]["not_on_derivative"]
        rules["derivatives"]["S32K116"]["not_on_derivative"] = [p for p in patterns if "SPLL" not in p["match"]]
        report, stale = ra.symbol_report(rules, "S32K116")
        missing = {sym for sym, status, _ in report if status == "MISSING"}
        self.assertIn("SCG_SPLLCSR_SPLLEN_MASK", missing)
        self.assertEqual(stale, [])

    def test_stale_absent_pattern(self):
        rules = self.broken()
        rules["derivatives"]["S32K116"]["not_on_derivative"].append({"match": "FTM_CnSC_CHF_MASK", "why": "test"})
        _, stale = ra.symbol_report(rules, "S32K116")
        self.assertEqual(stale, ["FTM_CnSC_CHF_MASK"])

    def test_unknown_rule_key(self):
        path = Path(self.id().replace(".", "_") + ".yaml")
        tmp = HERE / "__pycache__" / path
        tmp.parent.mkdir(exist_ok=True)
        tmp.write_text(RULES_FILE.read_text(encoding="utf-8").replace("    macro_prefix:\n      - {vendor: CAN_",
                                                                      "    macro_prefixes:\n      - {vendor: CAN_", 1),
                       encoding="utf-8")
        self.addCleanup(tmp.unlink)
        with self.assertRaisesRegex(ra.RtdError, "modules.FLEXCAN: unknown key 'macro_prefixes'"):
            ra.load_rules(tmp)


if __name__ == "__main__":
    unittest.main()
