"""DOC-48 (formal part of SYS-100): quality rules of the software safety requirements doc/requirements/safety/ssr.sdoc.

Checks, on an own reading of the .sdoc text (no StrictDoc needed):
- the requirement text is free of implementation detail (rule SYS-72 of the ssr.sdoc header): no code identifier in
  the STATEMENT of an SSR or of a clause - no `::`, no call `name()`, no back-quoted code, no identifier in
  camelCase or with an underscore, no source file, no path below a code folder - and no value tied to a platform
  family ("for the S32K1 family"); the one known exception is the family value of SSR-016(b) (finding R6 of the
  inspection, AN-051, open), allowed by name and only for that text;
- every SSR carries a REVIEW_STATUS with one of the values of the header (proposed, rework, inspected, accepted), and
  a clause that carries one uses the same values;
- reported, not failed: a clause in force with more than one "shall" (atomicity, rule SYS-72) - printed as a warning
  with the clause IDs, because whether a "shall" pair is one statement (a check together with its event) is a
  judgement of the inspection.
Counter-checks with deliberately broken copies show that each rule sees its fault.

Run: .venv/Scripts/python -m unittest doc/safety/test/test_requirements_quality.py
"""

import pathlib
import re
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[3]
SSR_SDOC = REPO / "doc" / "requirements" / "safety" / "ssr.sdoc"

REVIEW_STATUS = ("proposed", "rework", "inspected", "accepted")

# known exceptions of the identifier rule: (node UID, the exact finding) - each one an open inspection finding
ALLOWED = {
    ("SSR-016(b)", "family value 'for the S32K1 family'"),     # R6 / AN-051: the deadman time-out of the family port
}

CODE_PATTERNS = (
    ("scope operator", re.compile(r"::")),
    ("function call", re.compile(r"\b[A-Za-z_]\w*\(\)")),
    ("back-quoted code", re.compile(r"`[^`]+`")),
    ("camelCase identifier", re.compile(r"\b[a-z]+[A-Z][A-Za-z0-9]*\b")),
    ("CamelCase identifier", re.compile(r"\b[A-Z][a-z0-9]+[A-Z][a-z][A-Za-z0-9]*\b")),
    ("identifier with underscore", re.compile(r"\b[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+\b")),
    ("source file", re.compile(r"\b\w+\.(?:c|cpp|h|hpp|s|S|ld|py|yaml|cmake)\b")),
    ("code path", re.compile(r"\b(?:ecu|libs|platforms|tools|bootloader|boards|projects|mcudb)/\w")),
    ("family value", re.compile(r"\bfor the S32K\w* family\b")),
)


def nodes(text: str) -> list:
    """[{'kind': 'SSR'|'CLAUSE', 'line': n, 'UID': ..., field: value}] in document order; multi-line values joined."""
    result, node, key, buf = [], None, None, []
    for n, line in enumerate(text.splitlines(), 1):
        if key is not None:                       # inside a >>> ... <<< value
            if line == "<<<":
                node[key] = " ".join(buf)
                key, buf = None, []
            else:
                buf.append(line.strip())
            continue
        m = re.fullmatch(r"\[([A-Z_]+)\]", line)
        if m:
            node = {"kind": m.group(1), "line": n}
            result.append(node)
            continue
        if node is None or re.fullmatch(r"\[\[/?SECTION\]\]", line):
            continue
        m = re.fullmatch(r"([A-Z_]+): (.*)", line)
        if m:
            if m.group(2) == ">>>":
                key, buf = m.group(1), []
            else:
                node[m.group(1)] = m.group(2)
    return [n for n in result if n["kind"] in ("SSR", "CLAUSE")]


def code_findings(node: dict) -> list:
    """[(uid, finding)] for one node's STATEMENT."""
    text = node.get("STATEMENT", "")
    found = []
    for name, pattern in CODE_PATTERNS:
        for m in pattern.finditer(text):
            label = f"family value '{m.group(0)}'" if name == "family value" else f"{name} '{m.group(0)}'"
            found.append((node["UID"], label))
    return found


def identifier_problems(all_nodes: list) -> list:
    problems = []
    seen_allowed = set()
    for node in all_nodes:
        if node.get("STATUS") == "withdrawn":
            continue
        for uid, finding in code_findings(node):
            if (uid, finding) in ALLOWED:
                seen_allowed.add((uid, finding))
                continue
            problems.append(f"{uid} (ssr.sdoc line {node['line']}): {finding} in the requirement text")
    for uid, finding in sorted(ALLOWED - seen_allowed):
        problems.append(f"{uid}: allowed exception '{finding}' no longer occurs - remove it from ALLOWED")
    return problems


def review_status_problems(all_nodes: list) -> list:
    problems = []
    for node in all_nodes:
        value = node.get("REVIEW_STATUS")
        if node["kind"] == "SSR" and value is None:
            problems.append(f"{node['UID']} (ssr.sdoc line {node['line']}): no REVIEW_STATUS")
        elif value is not None and value not in REVIEW_STATUS:
            problems.append(f"{node['UID']}: REVIEW_STATUS '{value}' not in {REVIEW_STATUS}")
    return problems


def several_shall(all_nodes: list) -> list:
    """UIDs of statements in force with more than one 'shall' (clauses, and SSR without clauses)."""
    return [n["UID"] for n in all_nodes
            if n.get("STATUS") != "withdrawn" and len(re.findall(r"\bshall\b", n.get("STATEMENT", ""))) > 1]


class RequirementsQuality(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = SSR_SDOC.read_text(encoding="utf-8")
        cls.nodes = nodes(cls.text)

    def test_nodes_found(self):
        ssr = [n for n in self.nodes if n["kind"] == "SSR"]
        clauses = [n for n in self.nodes if n["kind"] == "CLAUSE"]
        self.assertGreaterEqual(len(ssr), 50)
        self.assertGreaterEqual(len(clauses), 150)
        self.assertTrue(all("UID" in n for n in self.nodes))
        # every requirement in force has a text: its own STATEMENT or clauses
        with_clauses = {re.sub(r"\(\w\)$", "", n["UID"]) for n in clauses}
        for n in ssr:
            if n.get("STATUS") != "withdrawn":
                self.assertTrue("STATEMENT" in n or n["UID"] in with_clauses, n["UID"])

    def test_no_code_identifiers_in_text(self):
        self.assertEqual(identifier_problems(self.nodes), [])

    def test_review_status(self):
        self.assertEqual(review_status_problems(self.nodes), [])

    def test_atomicity_warning(self):
        many = several_shall(self.nodes)
        if many:
            print(f"\nwarning: {len(many)} statement(s) with more than one 'shall' (rule SYS-72, judged by the "
                  f"inspection): {', '.join(many)}", file=sys.stderr)

    # --- counter-checks ------------------------------------------------------------------------------------------

    def broken(self, old: str, new: str) -> list:
        assert old in self.text, f"counter-check anchor not found: {old[:60]}"
        return nodes(self.text.replace(old, new, 1))

    def test_counter_checks_identifiers(self):
        anchor = "UID: SSR-001(c)\nSTATEMENT: The reaction to an event shall be selected at exactly one place"
        for insert, label in (("SafeSupervisor::handle()", "scope operator"),
                              ("the function handle()", "function call"),
                              ("`handle`", "back-quoted code"),
                              ("the safeSupervisor", "camelCase identifier"),
                              ("the SafeSupervisor", "CamelCase identifier"),
                              ("ROM_CHECK_FAILURE", "identifier with underscore"),
                              ("SafeSupervisor.cpp", "source file"),
                              ("ecu/safety", "code path"),
                              ("for the S32K3 family", "family value")):
            problems = identifier_problems(self.broken(anchor, anchor + " " + insert))
            self.assertTrue(any(p.startswith("SSR-001(c)") and label in p for p in problems), (insert, problems))
        # the allowed exception is bound to its node: the same text elsewhere is a finding ...
        problems = identifier_problems(self.broken(anchor, anchor + " for the S32K1 family"))
        self.assertTrue(any(p.startswith("SSR-001(c)") for p in problems), problems)
        # ... and an exception that no longer occurs is reported
        problems = identifier_problems(self.broken("(10 ms for the S32K1 family)", "(the family's fixed time-out)"))
        self.assertTrue(any("no longer occurs" in p for p in problems), problems)
        # plain words, IDs and units are not identifiers
        clean = nodes("[CLAUSE]\nUID: SSR-999(a)\nSTATEMENT: The safe supervisor shall raise S1 + S2 within "
                      "10 ms after SSR-005(b) and the MCU watchdog.\n")
        self.assertEqual([p for p in identifier_problems(clean) if p.startswith("SSR-999")], [])

    def test_counter_checks_review_status(self):
        old = "UID: SSR-002\n"
        self.assertIn(old, self.text)
        cut = self.text.split(old, 1)
        head, rest = cut[0], cut[1]
        rest = rest.replace("REVIEW_STATUS: inspected\n", "", 1)
        problems = review_status_problems(nodes(head + old + rest))
        self.assertTrue(any(p.startswith("SSR-002") and "no REVIEW_STATUS" in p for p in problems), problems)
        problems = review_status_problems(self.broken("REVIEW_STATUS: accepted\n", "REVIEW_STATUS: approved\n"))
        self.assertTrue(any("'approved'" in p for p in problems), problems)

    def test_counter_checks_atomicity(self):
        anchor = "UID: SSR-001(c)\nSTATEMENT: The reaction to an event shall be selected at exactly one place"
        self.assertIn("SSR-001(c)", several_shall(self.broken(anchor, anchor + " and shall be logged")))
        self.assertNotIn("SSR-001(c)", several_shall(self.nodes))


if __name__ == "__main__":
    unittest.main()
