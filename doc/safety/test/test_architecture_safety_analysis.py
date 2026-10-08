"""SYS-71: consistency of the architecture-level safety analysis doc/safety/architecture-safety-analysis.md.

Checks
- the failure-mode tables of section 3 cover the five parts (RTE, ecuState, CAN, NvM, bootloader), every id is
  unique and carries the prefix of its part;
- every SSR cited exists in doc/requirements/safety/ssr.sdoc, every SM cited exists in the catalogue
  (doc/requirements/safety/mechanisms.sdoc, stage 1 and stage 2; DOC-21 step 3, read through tools/trace);
- every test id cited in an "Evidence" cell exists in tools/test/tests.yaml;
- every open verdict names a finding (ASA-n or a known one), every ASA-n used is defined in section 6 and every
  defined one is used in section 3 or 5;
- the counts of section 0 (failure modes per part, verdicts, single points per tree) match the tables and trees;
- the fault trees for ASG-01, ASG-02 and ASG-06 exist and each lists its single points;
- no percentage is stated (no invented numbers, concept section 0).

Run: .venv/Scripts/python -m unittest doc/safety/test/test_architecture_safety_analysis.py
     (or: python doc/safety/test/test_architecture_safety_analysis.py)
"""

import pathlib
import re
import sys
import unittest

import yaml

REPO = pathlib.Path(__file__).resolve().parents[3]
SAFETY = REPO / "doc" / "safety"
DOC = SAFETY / "architecture-safety-analysis.md"
sys.path.insert(0, str(REPO / "tools" / "trace"))
import tracecheck  # noqa: E402  (StrictDoc model of the SSR and the catalogue, DOC-21)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_safety_plan import load_unpublished  # noqa: E402  (DOC-85: what the export left out on purpose)

PARTS = {"RTE": "3.1", "ECU": "3.2", "CAN": "3.3", "NVM": "3.4", "BL": "3.5"}
KNOWN_FINDINGS = {"F10", "N4", "DF-42", "DFA-6", "G1", "F4"}


def section(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def failure_rows(text: str) -> list:
    """(id, cells) of every failure-mode row of section 3."""
    rows = []
    body = section(text, "\n## 3. Failure modes per part", "\n## 4.")
    for line in body.splitlines():
        match = re.match(r"\| (FM-[A-Z]+-\d\d) \|", line)
        if match:
            rows.append((match.group(1), [c.strip() for c in line.strip().strip("|").split("|")]))
    return rows


def verdict_class(verdict: str) -> str:
    if "**open" in verdict:
        return "open"
    if verdict.startswith("covered with application"):
        return "application"
    if verdict.startswith("covered"):
        return "covered"
    return "unknown"


def cited_tests(rows: list) -> set:
    ids = set()
    for _, cells in rows:
        for token in re.findall(r"`([^`]+)`", cells[5]):
            if re.fullmatch(r"[a-z][a-z0-9-]*", token):
                ids.add(token)
    return ids


def trees(text: str) -> dict:
    body = section(text, "\n## 5. Fault trees", "\n## 6.")
    result = {}
    for goal in ("ASG-01", "ASG-02", "ASG-06"):
        part = re.split(rf"\n### 5\.\d FT-{goal} ", body, maxsplit=1)[1].split("\n### ", 1)[0]
        result[goal] = part
    return result


def single_points(tree_text: str) -> int:
    code = tree_text.split("```text", 1)[1].split("```", 1)[0]
    return len(re.findall(r"\*\* single point", code))


class ArchitectureSafetyAnalysis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DOC.read_text(encoding="utf-8")
        cls.model = tracecheck.safety_model()
        cls.ssr = {r["id"] for r in cls.model.requirements}
        tests = yaml.safe_load((REPO / "tools" / "test" / "tests.yaml").read_text(encoding="utf-8"))
        cls.test_ids = {entry["name"] for entry in tests["tests"]}
        # DOC-85: in the export an entry the export removed (and lists) counts; the list is empty in the repository
        cls.unpublished_tests = set(load_unpublished().tests)
        cls.rows = failure_rows(cls.text)

    def test_parts_and_ids(self):
        ids = [fm for fm, _ in self.rows]
        self.assertEqual(len(ids), len(set(ids)), "duplicate failure-mode id")
        for prefix, number in PARTS.items():
            body = section(self.text, f"\n### {number} ", "\n### " if number != "3.5" else "\n## 4.")
            in_part = re.findall(r"^\| (FM-[A-Z]+)-\d\d \|", body, flags=re.MULTILINE)
            self.assertGreaterEqual(len(in_part), 5, f"section {number} has too few failure modes")
            self.assertEqual(set(in_part), {f"FM-{prefix}"}, f"section {number} mixes prefixes")
        for _, cells in self.rows:
            self.assertEqual(len(cells), 7, cells[0])

    def test_cited_requirements_and_mechanisms_exist(self):
        self.assertGreater(len(self.ssr), 40)
        for ssr in set(re.findall(r"\bSSR-\d{3}\b", self.text)):
            self.assertIn(ssr, self.ssr, f"{ssr} not in ssr.sdoc")
        # stage 1 and stage 2 (the device packages SM-40 ... SM-43 may be named as "not credited")
        known = set(self.model.mechanisms)
        for sm in set(re.findall(r"\bSM-\d\d\b", self.text)):
            self.assertIn(sm, known, f"{sm} not in the catalogue (mechanisms.sdoc)")

    def test_cited_tests_exist(self):
        cited = cited_tests(self.rows)
        self.assertGreater(len(cited), 20)
        self.assertEqual(sorted(cited - self.test_ids - self.unpublished_tests), [],
                         "evidence cites tests missing in tools/test/tests.yaml")

    def test_findings_defined_and_used(self):
        defined = set(re.findall(r"^\| \*\*(ASA-\d+)\*\* \|", self.text, flags=re.MULTILINE))
        self.assertGreaterEqual(len(defined), 5)
        numbers = sorted(int(f.split("-")[1]) for f in defined)
        self.assertEqual(numbers, list(range(1, len(numbers) + 1)), "ASA numbers not consecutive")
        used = set()
        for fm, cells in self.rows:
            verdict = cells[6]
            cls = verdict_class(verdict)
            self.assertNotEqual(cls, "unknown", f"{fm}: verdict word")
            refs = set(re.findall(r"ASA-\d+", verdict))
            used |= refs
            if cls == "open":
                named = refs | {k for k in KNOWN_FINDINGS if re.search(rf"\b{re.escape(k)}\b", verdict)}
                if "integrator" not in verdict:
                    self.assertTrue(named, f"{fm}: open verdict without a finding")
        used |= set(re.findall(r"ASA-\d+", section(self.text, "\n## 5. Fault trees", "\n## 6.")))
        self.assertEqual(sorted(used - defined), [], "ASA used but not defined")
        self.assertEqual(sorted(defined - used), [], "ASA defined but used nowhere in sections 3 and 5")

    def test_summary_counts_match(self):
        summary = section(self.text, "\n## 0. Result in one page", "\n## 1.")
        self.assertIn(f"**{len(self.rows)}**", summary)
        per_part = {p: sum(1 for fm, _ in self.rows if fm.startswith(f"FM-{p}-")) for p in PARTS}
        match = re.search(
            r"RTE (\d+), ecuState (\d+), CAN (\d+), NvM and calibration (\d+), bootloader (\d+)", summary)
        self.assertIsNotNone(match)
        self.assertEqual([int(g) for g in match.groups()], [per_part[p] for p in PARTS])
        classes = [verdict_class(cells[6]) for _, cells in self.rows]
        match = re.search(r"\*\*(\d+) covered\*\*.*\*\*(\d+) covered with the application\*\*.*\*\*(\d+) open\*\*",
                          summary)
        self.assertIsNotNone(match)
        self.assertEqual([int(g) for g in match.groups()],
                         [classes.count("covered"), classes.count("application"), classes.count("open")])

    def test_fault_trees(self):
        found = trees(self.text)
        summary = section(self.text, "\n## 0. Result in one page", "\n## 1.")
        for goal, body in found.items():
            self.assertIn("[OR]", body, goal)
            count = single_points(body)
            self.assertGreater(count, 0, goal)
            self.assertIn(f"Single points at stage 1 ({count})", body, f"{goal}: list does not match the tree")
            self.assertIn(f"{goal} **{count}**", summary, f"{goal}: summary count")
            for fm in set(re.findall(r"FM-[A-Z]+-\d\d", body)):
                self.assertTrue(any(fm == row for row, _ in self.rows) or fm in {"FM-RTE-01/08"}, fm)

    def test_no_percentages(self):
        self.assertIsNone(re.search(r"\d\s*%", self.text))

    # Counter-checks: the parsers must see a broken document
    def test_parsers_detect_breakage(self):
        broken = self.text.replace("| FM-RTE-02 |", "| FM-RTE-01 |", 1)
        ids = [fm for fm, _ in failure_rows(broken)]
        self.assertNotEqual(len(ids), len(set(ids)))
        broken = self.text.replace("`rte-deadline`", "`rte-deadline-missing`", 1)
        self.assertIn("rte-deadline-missing", cited_tests(failure_rows(broken)))
        broken = self.text.replace("⟨none⟩  ** single point (ASA-4) **", "⟨none⟩", 1)
        self.assertEqual(single_points(trees(broken)["ASG-01"]), single_points(trees(self.text)["ASG-01"]) - 1)
        self.assertEqual(verdict_class("**open → ASA-1**"), "open")
        self.assertEqual(verdict_class("covered with application (AoU-11)"), "application")


if __name__ == "__main__":
    sys.exit(unittest.main())
