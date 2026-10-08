"""SYS-20: consistency of the failure analysis doc/safety/fmea.md with the safety concept and the test list.

Checks
- every stage-1 mechanism of the catalogue (SM nodes with STAGE 1 in doc/requirements/safety/mechanisms.sdoc, moved
  there from concept.md section 6 by DOC-21 step 3, read through tools/trace and StrictDoc's JSON export) has its own
  section "#### SM-xx" in fmea.md, and fmea.md analyses no mechanism the catalogue does not know;
- every test id cited in an "Evidence" row of fmea.md exists in tools/test/tests.yaml;
- no "Diagnostic coverage" row states a percentage (the concept forbids invented coverage figures);
- the findings F9 ... of fmea.md are each referenced from concept.md section 9.1, so the numbers stay claimed.

Run: .venv/Scripts/python -m unittest doc/safety/test/test_fmea.py  (or: python doc/safety/test/test_fmea.py)
"""

import pathlib
import re
import sys
import unittest

import yaml

REPO = pathlib.Path(__file__).resolve().parents[3]
SAFETY = REPO / "doc" / "safety"


sys.path.insert(0, str(REPO / "tools" / "trace"))
import tracecheck  # noqa: E402  (StrictDoc model of the safety catalogue, DOC-21)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_safety_plan import load_unpublished  # noqa: E402  (DOC-85: what the export left out on purpose)


def stage1_mechanisms(model) -> set:
    """Mechanism IDs of the catalogue with STAGE 1 (credited or not); device-dependent (stage 2) ones out."""
    return set(model.stage1_mechanisms())


def analysed_mechanisms(fmea_text: str) -> set:
    return set(re.findall(r"^#### (SM-\d\d)\b", fmea_text, flags=re.MULTILINE))


def evidence_test_ids(fmea_text: str) -> set:
    ids = set()
    for line in fmea_text.splitlines():
        if line.startswith("| Evidence |"):
            for token in re.findall(r"`([^`]+)`", line):
                if re.fullmatch(r"[a-z][a-z0-9-]*", token):
                    ids.add(token)
    return ids


def coverage_rows(fmea_text: str) -> list:
    return [line for line in fmea_text.splitlines() if line.startswith("| Diagnostic coverage |")]


class FmeaConsistency(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.concept = (SAFETY / "concept.md").read_text(encoding="utf-8")
        cls.model = tracecheck.safety_model()
        cls.fmea = (SAFETY / "fmea.md").read_text(encoding="utf-8")
        tests = yaml.safe_load((REPO / "tools" / "test" / "tests.yaml").read_text(encoding="utf-8"))
        cls.test_ids = {entry["name"] for entry in tests["tests"]}
        # DOC-85: in the export an entry the export removed (and lists) counts; the list is empty in the repository
        cls.unpublished_tests = set(load_unpublished().tests)

    def test_every_stage1_mechanism_is_analysed(self):
        expected = stage1_mechanisms(self.model)
        self.assertGreaterEqual(len(expected), 20, "catalogue parse found too few mechanisms")
        self.assertNotIn("SM-33", expected)
        self.assertNotIn("SM-52", expected)
        analysed = analysed_mechanisms(self.fmea)
        self.assertEqual(sorted(expected - analysed), [], "stage-1 mechanisms without analysis")
        self.assertEqual(sorted(analysed - expected), [], "analysed mechanisms unknown to the concept")

    def test_cited_tests_exist(self):
        cited = evidence_test_ids(self.fmea)
        self.assertGreater(len(cited), 20)
        self.assertEqual(sorted(cited - self.test_ids - self.unpublished_tests), [],
                         "evidence cites tests missing in tools/test/tests.yaml")

    def test_no_coverage_percentages(self):
        rows = coverage_rows(self.fmea)
        self.assertGreater(len(rows), 20)
        for row in rows:
            self.assertIsNone(re.search(r"\d\s*%", row), row[:80])

    def test_new_findings_are_referenced_in_concept(self):
        findings = set(re.findall(r"^\| \*\*(F\d+)\*\* \|", self.fmea, flags=re.MULTILINE))
        self.assertIn("F9", findings)
        section = self.concept.split("### 9.1", 1)[1].split("### 9.2", 1)[0]
        for finding in sorted(findings):
            self.assertIn(f"**{finding}**", section, f"{finding} not referenced in concept.md section 9.1")

    # Counter-checks: the parsers must see a broken document
    def test_parsers_detect_breakage(self):
        broken = self.fmea.replace("#### SM-14", "#### SM-99", 1)
        self.assertNotIn("SM-14", analysed_mechanisms(broken))
        broken = self.fmea.replace("| Evidence | `rom-check`", "| Evidence | `rom-check-missing`", 1)
        self.assertIn("rom-check-missing", evidence_test_ids(broken))
        broken = self.fmea.replace("| Diagnostic coverage | **high**", "| Diagnostic coverage | 99 %", 1)
        self.assertTrue(any(re.search(r"\d\s*%", row) for row in coverage_rows(broken)))


if __name__ == "__main__":
    sys.exit(unittest.main())
