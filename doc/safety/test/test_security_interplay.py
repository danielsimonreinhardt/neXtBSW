"""SYS-92 (N1): consistency of doc/safety/security-interplay.md with the repository.

Checks
- the interface register has unique IDs IF-01 ... in order; every row names where it is implemented (existing
  paths), what it can change, its effect on the safety argument, the existing protection, the gap, at least one
  assumption of use of section 4 and at least one task of doc/todo.yaml or proposal of section 6;
- the interfaces N1 names are there (bootloader update over CAN, UDS, XCP), and every field-write interface module
  that exists in the tree (bootloader, updater, application UDS, XCP, calibration, console, canflash) is named in
  the "Where" column of some row;
- every assumption of use and every proposal is used by an interface row or the text; proposals have a real
  "done when"; the rules table is not empty;
- the counts of section 0 match the tables;
- once doc/safety/safety-manual.md exists (DOC-19) it references this document (proposal S4);
- licence rule for ISO 26262 (part and clause only).
Counter-checks with deliberately broken copies show that each rule sees its fault.

Run: .venv/Scripts/python -m unittest doc/safety/test/test_security_interplay.py
"""

import pathlib
import re
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_safety_plan import licence_problems, load_tasks, table  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[3]
DOC = REPO / "doc" / "safety" / "security-interplay.md"
MANUAL = REPO / "doc" / "safety" / "safety-manual.md"

INTERFACE_COLUMNS = ("ID", "Interface", "Where", "Can change", "Effect on the safety argument", "Existing protection",
                     "Gap", "Assumption", "Task")
AOU_COLUMNS = ("ID", "Assumption of use", "Interfaces")
RULE_COLUMNS = ("ID", "Rule")
PROPOSAL_COLUMNS = ("ID", "Title", "Done when", "Prio")
# modules of the tree through which code, configuration or calibration can be written in the field
FIELD_WRITE_MODULES = ("bootloader/src/core", "bootloader/updater", "ecu/uds", "ecu/xcp", "ecu/calibration",
                       "ecu/consoleCommands", "tools/canflash")
# N1 "done when": these interfaces must be in the register (keyword in the column "Interface")
N1_INTERFACES = ("bootloader", "UDS", "XCP")


def paths(cell: str) -> list:
    return [p.rstrip("/") for p in re.findall(r"`([^`]+)`", cell) if "/" in p]


def refs(cell: str) -> list:
    return [r.strip() for r in cell.split(",") if r.strip() and r.strip() != "—"]


class Interplay:
    def __init__(self, text: str, tasks: dict, exists=None, manual=None):
        self.text = text
        self.tasks = tasks
        self.exists = exists or (lambda p: (REPO / p).exists())
        self.manual = manual
        self.interfaces = table(text, "sec-interfaces", INTERFACE_COLUMNS)
        self.aou = table(text, "sec-aou", AOU_COLUMNS)
        self.rules = table(text, "sec-rules", RULE_COLUMNS)
        self.proposals = table(text, "sec-proposals", PROPOSAL_COLUMNS)

    def interface_problems(self) -> list:
        problems = []
        ids = [r["ID"] for r in self.interfaces]
        if ids != [f"IF-{n:02d}" for n in range(1, len(ids) + 1)]:
            problems.append(f"interface IDs not IF-01 ... in order: {ids}")
        aou = {a["ID"] for a in self.aou}
        proposals = {p["ID"] for p in self.proposals}
        for row in self.interfaces:
            where = row["ID"]
            for column in ("Interface", "Can change", "Effect on the safety argument", "Existing protection", "Gap"):
                if len(row[column]) < 10:
                    problems.append(f"{where}: column '{column}' empty")
            found = paths(row["Where"])
            if not found:
                problems.append(f"{where}: no implementation path")
            for path in found:
                if not self.exists(path):
                    problems.append(f"{where}: {path} does not exist")
            assumptions = refs(row["Assumption"])
            if not assumptions:
                problems.append(f"{where}: no assumption of use")
            for ref in assumptions:
                if ref not in aou:
                    problems.append(f"{where}: assumption {ref} not defined in section 4")
            tasks = refs(row["Task"])
            if not tasks:
                problems.append(f"{where}: no task or proposal")
            for ref in tasks:
                if ref not in proposals and ref not in self.tasks:
                    problems.append(f"{where}: {ref} is neither a task of doc/todo.yaml nor a proposal")
        for keyword in N1_INTERFACES:
            if not any(keyword in r["Interface"] for r in self.interfaces):
                problems.append(f"interface named by N1 missing: {keyword}")
        named = " ".join(r["Where"] for r in self.interfaces)
        for module in FIELD_WRITE_MODULES:
            if self.exists(module) and module not in named:
                problems.append(f"field-write module {module} is in the tree but in no interface row")
        return problems

    def usage_problems(self) -> list:
        problems = []
        used_aou = {a for r in self.interfaces for a in refs(r["Assumption"])}
        for row in self.aou:
            if row["ID"] not in used_aou:
                problems.append(f"{row['ID']} is used by no interface")
            if not re.fullmatch(r"AoU-SEC-\d+", row["ID"]):
                problems.append(f"{row['ID']}: not of the form AoU-SEC-n")
            for ref in refs(row["Interfaces"]):
                if ref not in {r["ID"] for r in self.interfaces}:
                    problems.append(f"{row['ID']}: interface {ref} not defined")
        before = self.text.split("<!-- sec-proposals:start -->", 1)[0]
        for prop in self.proposals:
            if len(prop["Done when"]) < 40:
                problems.append(f"proposal {prop['ID']}: 'done when' too short")
            if prop["ID"] not in before:
                problems.append(f"proposal {prop['ID']} is used nowhere")
        if not self.rules:
            problems.append("no rules for the interplay")
        return problems

    def summary_problems(self) -> list:
        problems = []
        line = next((ln for ln in self.text.splitlines() if "<!-- sec-counts -->" in ln), "")
        if f"**{len(self.interfaces)}**" not in line:
            problems.append(f"section 0 does not count {len(self.interfaces)} interfaces")
        last = self.aou[-1]["ID"] if self.aou else ""
        if f"AoU-SEC-1 … {last}" not in self.text:
            problems.append(f"section 0 does not name AoU-SEC-1 … {last}")
        if f"{len(self.rules)} rules" not in self.text:
            problems.append(f"section 0 does not count {len(self.rules)} rules")
        return problems

    def manual_problems(self) -> list:
        if self.manual is None:
            return []
        if "security-interplay.md" not in self.manual:
            return ["safety manual exists but does not reference security-interplay.md (proposal S4)"]
        return []


class SecurityInterplayConsistency(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DOC.read_text(encoding="utf-8")
        cls.tasks = load_tasks()
        cls.manual = MANUAL.read_text(encoding="utf-8") if MANUAL.exists() else None
        cls.doc = Interplay(cls.text, cls.tasks, manual=cls.manual)

    def doc_of(self, text=None, exists=None, manual=None):
        return Interplay(self.text if text is None else text, self.tasks, exists, manual)

    def test_interfaces(self):
        self.assertEqual(self.doc.interface_problems(), [])

    def test_usage(self):
        self.assertEqual(self.doc.usage_problems(), [])

    def test_summary(self):
        self.assertEqual(self.doc.summary_problems(), [])

    def test_safety_manual_reference(self):
        self.assertEqual(self.doc.manual_problems(), [])

    def test_standard_cited_by_part_and_clause_only(self):
        self.assertEqual(licence_problems(self.text), [])

    def test_counter_checks(self):
        def sub(source, old, new, count=-1):
            self.assertTrue(old in source, f"counter-check edit does not hit: {old[:80]}")
            return source.replace(old, new, count)

        text = self.text
        row5 = next(ln for ln in text.splitlines() if ln.startswith("| IF-05 |"))
        row7 = next(ln for ln in text.splitlines() if ln.startswith("| IF-07 |"))
        # interfaces: XCP row removed (N1 interface and field-write module), ID order, missing path, unknown task,
        # unknown assumption, empty gap
        broken = sub(text, row5 + "\n", "", 1)
        problems = self.doc_of(broken).interface_problems()
        self.assertTrue(any("XCP" in p for p in problems), problems)
        self.assertTrue(self.doc_of(sub(text, "| IF-07 |", "| IF-17 |", 1)).interface_problems())
        exists = lambda p: p != "ecu/xcp" and (REPO / p).exists()   # noqa: E731
        self.assertTrue(self.doc_of(exists=exists).interface_problems())
        self.assertTrue(self.doc_of(sub(text, row7, sub(row7, "| SYS-74 |", "| SYS-999 |"), 1)).interface_problems())
        self.assertTrue(self.doc_of(sub(text, row7, sub(row7, "| AoU-SEC-6 |", "| AoU-SEC-66 |"), 1))
                        .interface_problems())
        gap = row7.split(" | ")[6]
        self.assertTrue(self.doc_of(sub(text, row7, row7.replace(gap, "—"), 1)).interface_problems())
        # a field-write module of the tree that no row names
        broken = sub(text, "`ecu/consoleCommands/src/safety/console/SafetyCommand.cpp`", "`ecu/uds`", 1)
        self.assertTrue(any("ecu/consoleCommands" in p for p in self.doc_of(broken).interface_problems()))
        # usage: an unused AoU, an unused proposal, a short done-when
        self.assertTrue(self.doc_of(sub(text, row7, sub(row7, "| AoU-SEC-6 |", "| AoU-SEC-5 |"), 1))
                        .usage_problems())
        self.assertTrue(self.doc_of(sub(text, "| S3 | XCP", "| S9 | XCP", 1)).usage_problems())
        row_s1 = next(ln for ln in text.splitlines() if ln.startswith("| S1 |"))
        self.assertTrue(self.doc_of(sub(text, row_s1, "| S1 | Flash | tbd | P2 |", 1)).usage_problems())
        # summary
        self.assertTrue(self.doc_of(sub(text, "**10**: application", "**9**: application", 1)).summary_problems())
        self.assertTrue(self.doc_of(sub(text, "| 6 rules:", "| 5 rules:", 1)).summary_problems())
        # safety manual without the reference
        self.assertTrue(self.doc_of(manual="# Safety manual\n").manual_problems())
        self.assertEqual(self.doc_of(manual="see security-interplay.md\n").manual_problems(), [])
        # licence rule, malformed tables
        self.assertTrue(licence_problems(text + "\nsee ISO 26262-2, Table 1\n"))
        with self.assertRaises(ValueError):
            self.doc_of(sub(text, "<!-- sec-aou:start -->", "", 1))


if __name__ == "__main__":
    sys.exit(unittest.main())
