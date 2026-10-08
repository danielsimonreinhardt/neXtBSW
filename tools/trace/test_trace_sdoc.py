# SPDX-License-Identifier: Apache-2.0
"""Host tests of tools/trace against the real StrictDoc (DOC-21). Needs strictdoc 0.30.1 (tools/requirements-docs.txt,
`python -m tools.ci setup --tool strictdoc`); the tools/test entry `trace-sdoc` is skipped without it, and the CI
counts that skip as red.

Parts:
  1. The fixture (tools/trace/fixtures/sdoc, the real grammar and configuration of doc/requirements) exported by
     StrictDoc equals the checked-in JSON fixtures/sdoc/index.json, which trace-tool uses without StrictDoc - a
     change of the grammar or of StrictDoc's export format shows up here.
  2. The good case through the real export: no error, the same warnings as the requirements.yaml case of
     test_trace.py, and the SSR records equal the records of the synthetic requirements.yaml (lossless mapping).
  3. Faults StrictDoc refuses (choice value, parent that does not exist, duplicate UID, unregistered relation role):
     a `schema` error that quotes StrictDoc.
  4. Faults StrictDoc accepts and tools/trace refuses, all in one exported copy: stage-2 mechanism in a stage-1
     requirement, DerivedFrom pointing at a mechanism, UID format, unknown finding, BSR parent of the wrong type.
  5. The project doc/requirements of the repository exports without error (grammar and configuration load).
  6. The repository itself (DOC-21 step 3, moved from test_trace.py): the default source is the StrictDoc project,
     no error, the catalogue (7 goals, 41 AoU, none proposed since DOC-51, 31 credited stage-1 mechanisms,
     SM-26/SM-27/SM-58/SM-59/SM-60 not credited), every stage-1 mechanism and goal has a requirement, the F15 mechanisms keep their tests, and the
     safety model the doc/safety tests read agrees with the check.
  7. DOC-43 (step 3b): clause nodes through the real export (grammar element CLAUSE, rebuilt text, clause warning),
     the RST check of the field texts against a fixture with broken RST, and the repository's 160 clauses with
     R3/R4/R5, the review status of every SSR and no RST error.

    python tools/trace/test_trace_sdoc.py [--regenerate]      --regenerate rewrites fixtures/sdoc/index.json
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import sdocmodel  # noqa: E402
import tracecheck  # noqa: E402
import test_trace  # noqa: E402

FIXTURE = HERE / "fixtures" / "sdoc"
FIXTURE_JSON = FIXTURE / "index.json"
PROJECT = tracecheck.REPO / tracecheck.SDOC_ROOT


def export_json(project: Path) -> tuple[int, list[str], dict | None]:
    with tempfile.TemporaryDirectory(prefix="nx_trace_sdoc_test_") as tmp:
        code, errors = sdocmodel.export(project, Path(tmp))
        index = Path(tmp) / "json" / "index.json"
        data = json.loads(index.read_text(encoding="utf-8")) if code == 0 and index.is_file() else None
    return code, errors, data


def regenerate() -> int:
    tree = test_trace.SdocTree(json_file=False)
    try:
        code, errors, data = export_json(tree.root / tracecheck.SDOC_ROOT)
    finally:
        tree.close()
    if code:
        print("\n".join(errors))
        return 1
    FIXTURE_JSON.write_text(json.dumps(data, indent=4) + "\n", encoding="utf-8", newline="\n")
    print(f"{FIXTURE_JSON}: {len(sdocmodel.parse_export(data).nodes)} nodes")
    return 0


@unittest.skipIf(sdocmodel.find_strictdoc() is None, "strictdoc not installed (python -m tools.ci setup --tool "
                 "strictdoc)")
class RealStrictDoc(unittest.TestCase):
    def tree(self, **overrides) -> test_trace.SdocTree:
        tree = test_trace.SdocTree(json_file=False, **overrides)
        self.addCleanup(tree.close)
        return tree

    def test_fixture_json_is_the_export(self):
        tree = self.tree()
        code, errors, data = export_json(tree.root / tracecheck.SDOC_ROOT)
        self.assertEqual(code, 0, errors)
        self.assertEqual(data, json.loads(FIXTURE_JSON.read_text(encoding="utf-8")),
                         "StrictDoc's export differs from fixtures/sdoc/index.json - grammar or export format "
                         "changed; check and run: python tools/trace/test_trace_sdoc.py --regenerate")

    def test_good_case_through_the_export(self):
        tree = self.tree()
        tree.matrix()
        result = tree.check()
        self.assertEqual(test_trace.kinds(result), set(), [str(m) for m in result.messages])
        self.assertEqual(test_trace.kinds(result, tracecheck.WARNING),
                         {"req-without-test", "mech-without-test", "aou-without-manual"})
        # lossless: the SSR of the .sdoc are the records of the synthetic requirements.yaml
        yaml_tree = test_trace.Tree()
        self.addCleanup(yaml_tree.close)
        as_yaml = yaml_tree.check(matrix=False).requirements
        self.assertEqual([vars(r) for r in result.requirements], [vars(r) for r in as_yaml])

    def test_faults_strictdoc_refuses(self):
        for name, old, new in (
                ("choice", "STATUS: partial\n", "STATUS: done\n"),
                ("missing parent", "VALUE: SM-02\n", "VALUE: SM-99\n"),
                ("duplicate", "UID: SSR-002\n", "UID: SSR-001\n"),
                ("role", "ROLE: Specifies\n", "ROLE: Credits\n")):
            with self.subTest(name):
                text = (FIXTURE / "ssr.sdoc").read_text(encoding="utf-8")
                self.assertIn(old, text)
                tree = self.tree(**{"doc/requirements/ssr.sdoc": text.replace(old, new, 1)})
                result = tree.check(matrix=False)
                schema = [m for m in test_trace.find(result, "schema") if m.text.startswith("StrictDoc:")]
                self.assertTrue(schema, [str(m) for m in result.messages])
                self.assertEqual(result.requirements, [])

    def test_faults_trace_refuses(self):
        ssr = (FIXTURE / "ssr.sdoc").read_text(encoding="utf-8")
        ssr = ssr.replace("  VALUE: SM-02\n", "  VALUE: SM-02\n  ROLE: Specifies\n- TYPE: Parent\n  VALUE: SM-33\n", 1)
        ssr = ssr.replace("  VALUE: AoU-01\n", "  VALUE: SM-02\n", 1).replace("FINDINGS: F1\n", "FINDINGS: F77\n")
        ssr = ssr.replace("  VALUE: BSR-SAFE-001\n", "  VALUE: STK-001\n")
        bsw = (FIXTURE / "stakeholder_bsw.sdoc").read_text(encoding="utf-8").replace("BSR-SAFE-001", "BSR-XYZ-001")
        tree = self.tree(**{"doc/requirements/ssr.sdoc": ssr, "doc/requirements/stakeholder_bsw.sdoc": bsw})
        result = tree.check(matrix=False)
        base = "doc/requirements/ssr.sdoc"
        wheres = {(m.kind, m.where) for m in result.messages if m.severity == tracecheck.ERROR}
        self.assertIn(("stage", f"{base}: SSR-002.RELATIONS(Specifies)[1]"), wheres)
        self.assertIn(("schema", f"{base}: SSR-001.RELATIONS(DerivedFrom)[1]"), wheres)
        self.assertIn(("dead-ref", f"{base}: SSR-001.FINDINGS[0]"), wheres)
        self.assertIn(("schema", f"{base}: SSR-002.RELATIONS(Parent)[0]"), wheres)
        self.assertIn(("schema", "doc/requirements/stakeholder_bsw.sdoc: BSR-XYZ-001.UID"), wheres)

    def test_clause_nodes_through_the_export(self):
        # DOC-43: the CLAUSE element of the real grammar; SSR-001 split into two clauses, one with a test entry
        ssr = (FIXTURE / "ssr.sdoc").read_text(encoding="utf-8")
        ssr = ssr.replace("STATEMENT: The watchdog shall be serviced by the safety task only.\n", "", 1)
        clauses = "".join(
            f"[CLAUSE]\nUID: SSR-001({letter})\nSTATEMENT: {text}\nVERIFICATION: test\n{tests}"
            "RELATIONS:\n- TYPE: Parent\n  VALUE: SSR-001\n\n"
            for letter, text, tests in (("a", test_trace.CLAUSE_TEXTS["a"], "TESTS: wd-test\n"),
                                        ("b", test_trace.CLAUSE_TEXTS["b"], "REVIEW_STATUS: accepted\n")))
        ssr = ssr.replace("[SSR]\nUID: SSR-002", clauses + "[SSR]\nUID: SSR-002", 1)
        result = self.tree(**{"doc/requirements/ssr.sdoc": ssr}).check(matrix=False)
        self.assertEqual(test_trace.kinds(result), set(), [str(m) for m in result.messages])
        req = {r.id: r for r in result.requirements}["SSR-001"]
        self.assertEqual([(c.id, c.tests, c.review) for c in req.clauses],
                         [("SSR-001(a)", ["wd-test"], ""), ("SSR-001(b)", [], "accepted")])
        self.assertEqual(req.text, f"(a) {test_trace.CLAUSE_TEXTS['a']} (b) {test_trace.CLAUSE_TEXTS['b']}")
        self.assertEqual([m.where for m in test_trace.find(result, "clause-without-test", tracecheck.WARNING)],
                         ["doc/requirements/ssr.sdoc: SSR-001(b).TESTS"])

    def test_rst_of_the_field_texts(self):
        # DOC-43: what the HTML export would refuse (an emphasis without its end, a list ending without a blank line)
        # is an error of the fast check, at the node and field
        ssr = (FIXTURE / "ssr.sdoc").read_text(encoding="utf-8")
        ssr = ssr.replace("STATEMENT: The watchdog shall be serviced by the safety task only.\n",
                          "STATEMENT: The watchdog shall be *serviced by the safety task only.\n", 1)
        ssr = ssr.replace("STATEMENT: The watchdog configuration shall be checked.\n",
                          "STATEMENT: >>>\n- first item\nnot indented\n<<<\n", 1)
        tree = self.tree(**{"doc/requirements/ssr.sdoc": ssr})
        result = tracecheck.check(tree.root, verify_matrix=False, sdoc="doc/requirements", rst=True)
        wheres = {m.where for m in test_trace.find(result, "rst")}
        self.assertEqual(wheres, {"doc/requirements/ssr.sdoc: SSR-001.STATEMENT",
                                  "doc/requirements/ssr.sdoc: SSR-002.STATEMENT"}, [str(m) for m in result.messages])
        # the good fixture has none, and the check stays off unless asked for
        self.assertEqual(test_trace.find(tracecheck.check(self.tree().root, verify_matrix=False,
                                                          sdoc="doc/requirements", rst=True), "rst"), [])
        self.assertEqual(test_trace.find(tree.check(matrix=False), "rst"), [])

    def test_repository_project_exports(self):
        code, errors, data = export_json(PROJECT)
        self.assertEqual(code, 0, errors)
        self.assertIsNotNone(data)


@unittest.skipIf(sdocmodel.find_strictdoc() is None, "strictdoc not installed (python -m tools.ci setup --tool "
                 "strictdoc)")
class Repository(unittest.TestCase):
    """The real StrictDoc project doc/requirements (SSR, ASG, AoU, SM since DOC-21 step 3; moved here from
    test_trace.py, because the default source of the repository needs StrictDoc now) against the real concept, fmea,
    tests.yaml and code."""

    @classmethod
    def setUpClass(cls):
        cls.result = tracecheck.check(cache=True, rst=True)

    def test_clauses_and_review_status(self):
        # DOC-43 (step 3b): every lettered clause a node of its own, with verification and tests per clause
        reqs = {r.id: r for r in self.result.requirements}
        clauses = [c for r in self.result.requirements for c in r.clauses]
        self.assertEqual(len(clauses), 160)
        self.assertEqual([c.id for c in clauses if c.withdrawn], ["SSR-010(d)"])   # R3, AN-048
        self.assertNotIn("before the watchdog resets the MCU", reqs["SSR-010"].text)
        self.assertIn("the repetition during operation is SSR-067", reqs["SSR-024"].text)   # R4, AN-049
        self.assertNotIn("safe_off", reqs["SSR-007"].title)                                 # R5, AN-050
        self.assertEqual({r.id for r in self.result.requirements if not r.review}, set())
        self.assertEqual([c.review for c in reqs["SSR-001"].clauses], ["", "accepted", ""])  # REV-SYS117-R9
        self.assertEqual(reqs["SSR-001"].clauses[2].verification, ["review"])
        # every clause test is an entry of its requirement, and no clause runs dry silently; DOC-85: in an exported
        # tree without the entries the export removed on purpose (doc/unpublished.yaml; empty in the repository)
        removed = self.result.unpublished_tests
        if not (tracecheck.REPO / tracecheck.UNPUBLISHED_LIST).is_file():
            self.assertEqual(removed, frozenset())
        for req in self.result.requirements:
            for clause in req.clauses:
                self.assertLessEqual(set(clause.tests) - removed, set(req.tests), clause.id)
        self.assertEqual(test_trace.find(self.result, "rst"), [])

    def test_default_source_is_strictdoc(self):
        self.assertEqual(self.result.origin, tracecheck.SDOC_ROOT)
        self.assertFalse((tracecheck.REPO / tracecheck.REQUIREMENTS).exists(), "requirements.yaml is gone (Q2)")

    def test_no_error(self):
        errors = [str(m) for m in self.result.messages if m.severity == tracecheck.ERROR]
        self.assertEqual(errors, [])

    def test_catalogue(self):
        cat = self.result.catalogue
        self.assertEqual(sorted(cat.goals), [f"ASG-0{i}" for i in range(1, 8)])
        self.assertGreaterEqual(len([a for a, proposed in cat.aous.items() if not proposed]), 35)
        # DOC-51 (CM-07 ZF, ZG): AoU-24/AoU-25/AoU-40 binding, AoU-33 a node - no proposed AoU left
        self.assertGreaterEqual(len([a for a, proposed in cat.aous.items() if not proposed]), 41)
        self.assertEqual(sorted(a for a, proposed in cat.aous.items() if proposed), [])
        stage1 = {m for m, v in cat.mechanisms.items() if v.stage1 and v.credited}
        self.assertEqual(len(stage1), 31)  # SM-34 (SYS-29), SM-55 (SYS-73) dazu; SM-26/SM-27 not credited for R1 (DOC-51, CM-07 ZD)
        # SM-58 (SYS-86), SM-59 (SYS-125) and SM-60 (NVM-25) are stage-1 nodes, but not credited (CREDITED: no): no
        # requirement needed
        for mid in ("SM-26", "SM-27", "SM-58", "SM-59", "SM-60"):
            self.assertTrue(cat.mechanisms[mid].stage1)
            self.assertFalse(cat.mechanisms[mid].credited)
        self.assertTrue({"SM-33", "SM-52", "SM-40", "SM-41", "SM-42", "SM-43"}.isdisjoint(stage1))
        self.assertTrue({"F1", "F9", "F16", "G1", "G6", "T1", "T5", "N4"} <= cat.findings)
        self.assertTrue(all(w.startswith("doc/requirements/safety/") for w in cat.where.values()))

    def test_every_stage1_mechanism_and_goal_has_a_requirement(self):
        covered = {m for r in self.result.requirements for m in r.mechanisms}
        stage1 = {m for m, v in self.result.catalogue.mechanisms.items() if v.stage1 and v.credited}
        self.assertEqual(sorted(stage1 - covered), [])
        goals = {g for r in self.result.requirements for g in r.derived_from}
        self.assertEqual(sorted(set(self.result.catalogue.goals) - goals), [])

    def test_mechanisms_of_f15_have_tests(self):
        # fmea.md F15: the evidence of these mechanisms was code review only, and the tool reported them "without
        # test" (the rule itself is covered by the synthetic tree of test_trace.py). SYS-41 added host tests for
        # SM-02, SM-03, SM-15 and SM-16, SYS-42 for SM-11 (safety-gate) and SM-21/SM-22 (safe-io) - F15 is closed,
        # none of them may fall back to review only.
        reported = {m.where.split(": ")[1] for m in test_trace.find(self.result, "mech-without-test",
                                                                   tracecheck.WARNING)}
        self.assertFalse({"SM-02", "SM-03", "SM-11", "SM-15", "SM-16", "SM-21", "SM-22"} & reported, reported)

    def test_requirement_rom_check_context_exists(self):
        # SYS-20 hands this requirement to SYS-22 (fmea.md section 5.1, F3)
        req = {r.id: r for r in self.result.requirements}["SSR-025"]
        self.assertIn("SM-14", req.mechanisms)
        self.assertIn("F3", req.findings)
        self.assertIn("interrupts suspended", req.text)

    def test_safety_model_for_the_documents(self):
        model = tracecheck.safety_model()
        self.assertEqual(len(model.requirements), len(self.result.requirements))
        self.assertEqual(set(model.goals), set(self.result.catalogue.goals))
        self.assertEqual(set(model.stage1_mechanisms(credited_only=True)),
                         {m for m, v in self.result.catalogue.mechanisms.items() if v.stage1 and v.credited})
        self.assertEqual(model.files["SSR-001"], "doc/requirements/safety/ssr.sdoc")
        # the cache is keyed by the content: a second load reuses the export
        index, errors = sdocmodel.cached_export(PROJECT)
        self.assertEqual(errors, [])
        self.assertTrue(index.is_file())


if __name__ == "__main__":
    if "--regenerate" in sys.argv:
        sys.exit(regenerate())
    unittest.main()
