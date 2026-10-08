"""NVM-05: the NvM block EcuStatistics (operating time and reset statistics of the application) in nvm.py - its fixed
size in the layout, type/length refused, warnings for a write rate the EEPROM does not last, and its index for the
NvM DTCs (bswdtc.block_index). Synthetic profile of test_nvm.py (not the eval board), plus the demo project as it is.

Run: .venv/Scripts/python -m unittest tools/nxconfig/test_nvm_ecu_stats.py
"""

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import nxconfig  # noqa: E402
from nxconfig import bswdtc, nvm  # noqa: E402
import test_nvm  # noqa: E402
from test_nvm import Profile  # noqa: E402

setUpModule, tearDownModule = test_nvm.setUpModule, test_nvm.tearDownModule   # SYS-95: pwmgate off, see test_nvm.py

GOOD = {"write": "change", "min_interval_ms": 3600000}
STATS = nvm.ECU_STATS_BLOCK


class EcuStatisticsBlockTest(Profile):
    def test_size_and_layout(self):
        config, messages = self.check({"blocks": {STATS: dict(GOOD), "Trim": {"type": "uint16"}}})
        self.assertEqual([str(m) for m in messages if m.severity in ("error", "warning")], [])
        d = config.nvm.definitions[STATS]
        self.assertEqual((d.size, d.bytes, d.integrity), (52, 120, "qm"))   # 2 x (52 + 8)
        self.assertEqual(config.nvm.writes[STATS], ("CHANGE", 3600000))
        # name order like tools/rtegen when nvm.blocks has entries: EcuStatistics before Trim
        self.assertEqual(config.nvm.layout, {STATS: 0, "Trim": 120})
        asil, _ = self.check({"blocks": {STATS: dict(GOOD, integrity="asil_b")}})
        self.assertEqual(asil.nvm.definitions[STATS].bytes, 128)              # 2 x (52 + 12)

    def test_type_and_length_refused(self):
        errors = self.messages({"blocks": {STATS: dict(GOOD, type="uint8", length=52)}})
        text = (f"{STATS} is the block of the operating time and reset statistics (NVM-05): its layout of 52 bytes "
                f"comes from the application; remove ")
        self.assertEqual(sorted(errors), [(f"nvm.blocks.{STATS}.length", text + "length"),
                                          (f"nvm.blocks.{STATS}.type", text + "type")])

    def test_write_rate_warnings(self):
        for entry, key, fragment in (({}, "write", "only at the run-down"),
                                     ({"write": "shutdown"}, "write", "only at the run-down"),
                                     ({"write": "change"}, "min_interval_ms", "min_interval_ms 0 is below 3600000"),
                                     ({"write": "change", "min_interval_ms": 900000}, "min_interval_ms",
                                      "min_interval_ms 900000 is below 3600000")):
            with self.subTest(entry=entry):
                warnings = self.messages({"blocks": {STATS: entry}}, severity="warning")
                self.assertEqual(len(warnings), 1, warnings)
                self.assertEqual(warnings[0][0], f"nvm.blocks.{STATS}.{key}")
                self.assertIn(fragment, warnings[0][1])
        self.assertEqual(self.messages({"blocks": {STATS: dict(GOOD, min_interval_ms=7200000)}}, severity="warning"),
                         [])

    def test_no_connection_warning(self):
        """A reserved block needs no connection nvm.EcuStatistics (the demo has an ECU description)."""
        # PLAT-62: the own full test profile (same NvM block) instead of the private eval board project
        config, messages = nxconfig.check(REPO / "tools/nxconfig/test/profiles/projects/testproject_full/project.yaml")
        self.assertEqual([str(m) for m in messages if m.severity in ("error", "warning")], [])
        self.assertIn(STATS, config.nvm.definitions)
        self.assertEqual(config.nvm.writes[STATS], ("CHANGE", 3600000))

    def test_block_index_leaves_it_out(self):
        self.assertEqual(bswdtc.block_index("Trim", ["Alpha", STATS, "Trim", "DemEvents"]), 1)
        with self.assertRaises(bswdtc.DerivationError):
            bswdtc.block_index(STATS, ["Trim"])

    def test_same_numbers_as_rtegen_and_the_application(self):
        sys.path.insert(0, str(REPO / "tools" / "rtegen"))
        import rtegen  # noqa: PLC0415

        self.assertEqual((rtegen.ECU_STATS_BLOCK, rtegen.ECU_STATS_BYTES), (STATS, nvm.ECU_STATS_BYTES))
        header = (REPO / "ecu/application/src/systems/stats/EcuStatistics.h").read_text(encoding="utf-8")
        self.assertIn(f'constexpr char const BLOCK_NAME[] = "{STATS}";', header)
        self.assertIn(f"constexpr uint16_t RECORD_BYTES = {nvm.ECU_STATS_BYTES}U;", header)


if __name__ == "__main__":
    unittest.main()
