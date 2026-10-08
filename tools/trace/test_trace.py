# SPDX-License-Identifier: Apache-2.0
"""Host tests of tools/trace (SYS-22): traceability of the safety requirements.

Parts:
  1. A synthetic repository of its own (concept, fmea, tests.yaml, requirements, code) - the good case has no error
     and exactly the expected warnings; the matrix is deterministic.
  2. Deliberately broken inputs, one rule each: every check fires with the expected kind and YAML path (dead
     references to goals, mechanisms, findings, files, symbols, tests and requirements; code tags; stage-2 mechanism
     in a stage-1 requirement; mechanism and goal without requirement; requirement without test; evidence test
     without requirement; AoU missing in an existing safety manual; stale matrix; schema errors).
  3. The repository itself: no error, the catalogue parse sees the concept's ID spaces, and none of the mechanisms
     fmea.md F15 named as review-only is reported "without test" any more (F15 closed by SYS-41 and SYS-42).

  4. DOC-43 (step 3b, class SdocClauses): clause nodes on the fixture export - rebuilt text, clause warnings
     (clause-without-test, test-not-allocated), clause errors (test of another requirement or unknown, methods outside
     or not covering the requirement's, withdrawn clause with tests, letters, UID, parent, statement next to
     clauses), review status values and the matrix with review column and clause table.

    python -m unittest tools/trace/test_trace.py
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import sdocmodel  # noqa: E402
import tracecheck  # noqa: E402

CONCEPT = """\
# concept

### 2.2 Assumed safety goals

| ID | Goal | ASIL |
|---|---|---|
| ASG-01 | Outputs off on fault | B |
| ASG-02 | Program flow supervised | B |

## 4. Assumptions of use

| ID | Assumption |
|---|---|
| AoU-01 | The integrator declares every output. |

## 6. Catalogue of safety mechanisms

| ID | Mechanism | Fault model |
|---|---|---|
| SM-01 | **Watchdog** | task stops |
| SM-02 | **Configuration check** | watchdog reconfigured |
| SM-33 | **Transceiver status** *(device-dependent: described, not credited in stage 1)* | bus |

## 7. Timing

## 9. Findings and gaps

### 9.1 Findings

| ID | Finding |
|---|---|
| **F1** | no reaction |

### 9.5 Gaps

| ID | Gap |
|---|---|
| **G1** | no read-back |

---

## 13. Later stage

### 13.3 SM-40 — Gate driver (`libs/bsw/gd`)
"""

FMEA = """\
# fmea

## 3. Per-mechanism analysis

#### SM-01 — Watchdog

| Field | Content |
|---|---|
| Evidence | `wd-test` (relation); board 2026-09-18 |

#### SM-02 — Configuration check

| Field | Content |
|---|---|
| Evidence | **review** only |

## 6. New findings

| ID | Finding |
|---|---|
| **F9** | period not supervised |

| ID | Assumption |
|---|---|
| AoU-02 (proposed) | restart often enough |

## 7. Proposed tasks

| # | Title |
|---|---|
| **T1** | supervise the period |

## 9. Open points

| ID | Point |
|---|---|
| N1 | mapping of classes |
"""

TESTS = """\
version: 1
tests:
  - name: wd-test
    group: python
    kind: unittest
    comment: watchdog
    requirements: [SSR-001]
  - name: other
    group: python
    kind: unittest
    comment: unrelated
"""

CODE = """\
// watchdog of the synthetic repository
void Watchdog::service() {}
bool Watchdog::check() { return true; }
"""

REQUIREMENTS = """\
version: 1
requirements:
  - id: SSR-001
    title: Watchdog serviced
    text: The watchdog shall be serviced by the safety task only.
    derived_from: [ASG-02, AoU-01]
    mechanisms: [SM-01]
    findings: [F1]
    review: inspected
    stage: 1
    asil: B
    status: implemented
    verification: [test]
    code:
      - file: ecu/wd.cpp
        symbol: Watchdog::service
  - id: SSR-002
    title: Configuration checked
    text: The watchdog configuration shall be checked.
    derived_from: [ASG-01]
    mechanisms: [SM-02]
    findings: [F9, G1, T1, N1]
    review: rework
    stage: 1
    asil: B
    status: partial
    verification: [review]
    code:
      - file: ecu/wd.cpp
        symbol: check
"""


class Tree:
    """A synthetic repository in a temporary directory."""

    def __init__(self, **overrides: str | None):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        files = {
            "doc/safety/concept.md": CONCEPT,
            "doc/safety/fmea.md": FMEA,
            "tools/test/tests.yaml": TESTS,
            "doc/safety/requirements.yaml": REQUIREMENTS,
            "ecu/wd.cpp": CODE,
        }
        files.update(overrides)
        for rel, text in files.items():
            if text is not None:
                self.write(rel, text)

    def write(self, rel: str, text: str, newline: str = "\n") -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline=newline)

    def matrix(self) -> None:
        with redirect_stdout(io.StringIO()):
            tracecheck.main(["matrix", "--repo", str(self.root)])

    def check(self, matrix: bool = True) -> tracecheck.Result:
        return tracecheck.check(self.root, verify_matrix=matrix)

    def close(self) -> None:
        self._tmp.cleanup()


SDOC_FIXTURE = HERE / "fixtures" / "sdoc"
SDOC_JSON = "build/sdoc_index.json"          # inside the synthetic tree; not below doc/requirements


class SdocTree(Tree):
    """The synthetic repository with the StrictDoc project doc/requirements (DOC-21): the real grammar and
    configuration of the repository, the fixture .sdoc files and - unless json_file is False - the checked-in export
    fixtures/sdoc/index.json, so that the checks run on the StrictDoc model without StrictDoc."""

    def __init__(self, json_data: dict | None = None, json_file: bool = True, **overrides: str | None):
        files = {f"doc/requirements/{p.name}": p.read_text(encoding="utf-8") for p in SDOC_FIXTURE.glob("*.sdoc")}
        for name in ("nextbsw.sgra", "strictdoc_config.py"):
            files[f"doc/requirements/{name}"] = (tracecheck.REPO / "doc/requirements" / name).read_text(encoding="utf-8")
        files.update(overrides)
        super().__init__(**files)
        self.json_file = json_file
        if json_file:
            data = json_data if json_data is not None else fixture_json()
            self.write(SDOC_JSON, json.dumps(data, indent=2))

    def args(self) -> list[str]:
        extra = ["--sdoc-json", str(self.root / SDOC_JSON)] if self.json_file else []
        return ["--repo", str(self.root), "--sdoc", "doc/requirements", *extra]

    def matrix(self) -> None:
        with redirect_stdout(io.StringIO()):
            tracecheck.main(["matrix", *self.args()])

    def check(self, matrix: bool = True) -> tracecheck.Result:
        return tracecheck.check(self.root, verify_matrix=matrix, sdoc="doc/requirements",
                                sdoc_json=(self.root / SDOC_JSON) if self.json_file else None)


def fixture_json() -> dict:
    return json.loads((SDOC_FIXTURE / "index.json").read_text(encoding="utf-8"))


def sdoc_node(data: dict, uid: str) -> dict:
    """The node `uid` in a StrictDoc JSON export (for edits)."""
    def walk(container):
        for child in container.get("NODES", []):
            if child.get("UID") == uid:
                return child
            found = walk(child)
            if found:
                return found
        return None
    for document in data["DOCUMENTS"]:
        node = walk(document)
        if node:
            return node
    raise KeyError(uid)


def drop_sdoc_node(data: dict, uid: str) -> None:
    def walk(container):
        container["NODES"] = [c for c in container.get("NODES", []) if c.get("UID") != uid]
        for child in container["NODES"]:
            walk(child)
    for document in data["DOCUMENTS"]:
        walk(document)


CLAUSE_TEXTS = {"a": "The watchdog shall be serviced by the safety task.",
                "b": "No other task shall service the watchdog."}


def clause_node(cid: str, text: str, verification: str = "test", tests: str = "", parent: str = "SSR-001",
                **extra: str) -> dict:
    """A CLAUSE node as StrictDoc exports it (DOC-43)."""
    node = {"_NODE_TYPE": "CLAUSE", "UID": cid, "STATEMENT": text, "VERIFICATION": verification}
    if tests:
        node["TESTS"] = tests
    node.update(extra)
    node["RELATIONS"] = [{"TYPE": "Parent", "VALUE": parent}] if parent else []
    return node


def with_clauses(data: dict, clauses: list[dict], keep_statement: bool = False) -> str:
    """SSR-001 of the fixture split into `clauses`: the nodes go into the export after SSR-001 (its STATEMENT
    removed unless keep_statement), and the .sdoc text that matches them is returned (UID cross-check)."""
    def walk(container):
        nodes = container.get("NODES", [])
        for i, child in enumerate(nodes):
            if child.get("UID") == "SSR-001":
                if not keep_statement:
                    child.pop("STATEMENT")
                nodes[i + 1:i + 1] = clauses
                return True
            if walk(child):
                return True
        return False
    for document in data["DOCUMENTS"]:
        if walk(document):
            break
    text = (SDOC_FIXTURE / "ssr.sdoc").read_text(encoding="utf-8")
    if not keep_statement:
        text = text.replace("STATEMENT: The watchdog shall be serviced by the safety task only.\n", "", 1)
    sdoc_nodes = "".join(f"[CLAUSE]\nUID: {c['UID']}\nSTATEMENT: {c['STATEMENT']}\nVERIFICATION: {c['VERIFICATION']}\n"
                         f"RELATIONS:\n- TYPE: Parent\n  VALUE: SSR-001\n\n" for c in clauses)
    return text.replace("[SSR]\nUID: SSR-002", sdoc_nodes + "[SSR]\nUID: SSR-002", 1)


def kinds(result, severity=tracecheck.ERROR) -> set:
    return {m.kind for m in result.messages if m.severity == severity}


def find(result, kind, severity=tracecheck.ERROR) -> list:
    return [m for m in result.messages if m.kind == kind and m.severity == severity]


class SyntheticGoodCase(unittest.TestCase):
    def setUp(self):
        self.tree = Tree()
        self.addCleanup(self.tree.close)

    def test_catalogue(self):
        cat = tracecheck.parse_catalogue(CONCEPT, FMEA)
        self.assertEqual(sorted(cat.goals), ["ASG-01", "ASG-02"])
        self.assertEqual(cat.aous, {"AoU-01": False, "AoU-02": True})
        self.assertEqual({m for m, v in cat.mechanisms.items() if v.stage1}, {"SM-01", "SM-02"})
        self.assertEqual({m for m, v in cat.mechanisms.items() if not v.stage1}, {"SM-33", "SM-40"})
        self.assertEqual(cat.findings, {"F1", "G1", "F9", "T1", "N1"})
        self.assertEqual(cat.evidence_tests, {"wd-test": ["SM-01"]})

    def test_good_case_has_no_error(self):
        self.tree.matrix()
        result = self.tree.check()
        self.assertEqual(kinds(result), set(), [str(m) for m in result.messages])
        self.assertEqual(kinds(result, tracecheck.WARNING),
                         {"req-without-test", "mech-without-test", "aou-without-manual"})
        self.assertEqual([m.where for m in find(result, "mech-without-test", tracecheck.WARNING)],
                         ["doc/safety/concept.md: SM-02"])
        # a proposed AoU is reported at fmea.md, where it is defined
        self.assertIn("doc/safety/fmea.md: AoU-02",
                      [m.where for m in find(result, "aou-without-manual", tracecheck.WARNING)])
        self.assertEqual(len(find(result, "tag-suggested", tracecheck.INFO)), 2)
        req = {r.id: r for r in result.requirements}
        self.assertEqual(req["SSR-001"].tests, ["wd-test"])
        self.assertEqual(req["SSR-002"].tests, [])

    def test_matrix_is_deterministic_and_checked(self):
        result = self.tree.check(matrix=False)
        text = tracecheck.render_matrix(result)
        self.assertEqual(text, tracecheck.render_matrix(self.tree.check(matrix=False)))
        self.assertIn("| SSR-001 | **Watchdog serviced** |", text)
        self.assertIn("| SM-02 Configuration check | SSR-002 | **no test** |", text)
        # missing and stale matrix are errors; a CRLF checkout of the same content is not
        self.assertIn("matrix-stale", kinds(self.tree.check()))
        self.tree.write("doc/safety/traceability.md", text.replace("Watchdog serviced", "changed"))
        self.assertIn("matrix-stale", kinds(self.tree.check()))
        self.tree.write("doc/safety/traceability.md", text, newline="\r\n")
        self.assertNotIn("matrix-stale", kinds(self.tree.check()))

    def test_cli(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(tracecheck.main(["check", "--repo", str(self.tree.root)]), 1)   # no matrix yet
        self.tree.matrix()
        with redirect_stdout(out):
            self.assertEqual(tracecheck.main(["check", "--repo", str(self.tree.root)]), 0)
            self.assertEqual(tracecheck.main(["check", "--strict", "--repo", str(self.tree.root)]), 1)
            self.assertEqual(tracecheck.main(["tags", "--repo", str(self.tree.root)]), 0)
        self.assertIn("ecu/wd.cpp\tWatchdog::service\t// nx-req: SSR-001", out.getvalue())
        self.assertEqual(tracecheck.tag_comment("a/b.py", "SSR-001"), "# nx-req: SSR-001")
        self.assertEqual(tracecheck.tag_comment("a/b.cpp.j2", "SSR-001"), "{# nx-req: SSR-001 #}")


class DeliberateFaults(unittest.TestCase):
    """Each rule against an input broken on purpose."""

    def run_tree(self, **overrides) -> tracecheck.Result:
        tree = Tree(**overrides)
        self.addCleanup(tree.close)
        return tree.check(matrix=False)

    def assertMessage(self, result, kind, where, severity=tracecheck.ERROR):
        wheres = [m.where for m in find(result, kind, severity)]
        self.assertIn(where, wheres, [str(m) for m in result.messages])

    def test_dead_reference_to_goal_mechanism_and_finding(self):
        text = REQUIREMENTS.replace("derived_from: [ASG-02, AoU-01]", "derived_from: [ASG-09, AoU-01]")
        text = text.replace("mechanisms: [SM-01]", "mechanisms: [SM-99]").replace("findings: [F1]", "findings: [F77]")
        result = self.run_tree(**{"doc/safety/requirements.yaml": text})
        base = "doc/safety/requirements.yaml: requirements[SSR-001]"
        self.assertMessage(result, "dead-ref", f"{base}.derived_from[0]")
        self.assertMessage(result, "dead-ref", f"{base}.mechanisms[0]")
        self.assertMessage(result, "dead-ref", f"{base}.findings[0]")

    def test_derived_from_takes_goals_and_assumptions_only(self):
        text = REQUIREMENTS.replace("derived_from: [ASG-01]", "derived_from: [SM-02]")
        result = self.run_tree(**{"doc/safety/requirements.yaml": text})
        self.assertMessage(result, "schema", "doc/safety/requirements.yaml: requirements[SSR-002].derived_from[0]")

    def test_dead_reference_to_file_and_symbol(self):
        text = REQUIREMENTS.replace("symbol: Watchdog::service", "symbol: Watchdog::serv")
        text = text.replace("      - file: ecu/wd.cpp\n        symbol: check", "      - file: ecu/gone.cpp\n"
                                                                              "        symbol: check")
        result = self.run_tree(**{"doc/safety/requirements.yaml": text})
        self.assertMessage(result, "dead-ref", "doc/safety/requirements.yaml: requirements[SSR-001].code[0].symbol")
        self.assertMessage(result, "dead-ref", "doc/safety/requirements.yaml: requirements[SSR-002].code[0].file")

    def test_symbol_needs_a_whole_word(self):
        # "check" must not be satisfied by "checked" or "rechecks"
        result = self.run_tree(**{"ecu/wd.cpp": "void Watchdog::service() {}\n// rechecks, checked\n"})
        self.assertMessage(result, "dead-ref", "doc/safety/requirements.yaml: requirements[SSR-002].code[0].symbol")

    def test_test_names_unknown_requirement(self):
        result = self.run_tree(**{"tools/test/tests.yaml": TESTS.replace("[SSR-001]", "[SSR-001, SSR-099]")})
        self.assertMessage(result, "dead-ref", "tools/test/tests.yaml: tests[wd-test].requirements[1]")

    def test_requirement_and_evidence_test_without_link(self):
        result = self.run_tree(**{"tools/test/tests.yaml": TESTS.replace("    requirements: [SSR-001]\n", "")})
        self.assertMessage(result, "req-without-test", "doc/safety/requirements.yaml: requirements[SSR-001].verification")
        self.assertMessage(result, "test-without-req", "tools/test/tests.yaml: tests[wd-test].requirements")
        self.assertMessage(result, "mech-without-test", "doc/safety/concept.md: SM-01", tracecheck.WARNING)

    def test_evidence_cites_missing_test(self):
        result = self.run_tree(**{"doc/safety/fmea.md": FMEA.replace("`wd-test` (relation)", "`wd-test`, `no-such`")})
        self.assertMessage(result, "dead-ref", "doc/safety/fmea.md: SM-01.Evidence")

    def test_stage2_mechanism_refused_in_stage1(self):
        for mech in ("SM-33", "SM-40"):
            text = REQUIREMENTS.replace("mechanisms: [SM-02]", f"mechanisms: [SM-02, {mech}]")
            result = self.run_tree(**{"doc/safety/requirements.yaml": text})
            self.assertMessage(result, "stage", "doc/safety/requirements.yaml: requirements[SSR-002].mechanisms[1]")
        text = REQUIREMENTS.replace("mechanisms: [SM-02]", "mechanisms: [SM-02, SM-40]").replace(
            "    stage: 1\n    asil: B\n    status: partial", "    stage: 2\n    asil: B\n    status: partial")
        self.assertNotIn("stage", kinds(self.run_tree(**{"doc/safety/requirements.yaml": text})))

    def test_not_credited_mechanism(self):
        # DOC-38: a stage-1 row marked "*not credited*" (SM-58 in the real concept) needs no requirement, and a
        # requirement that credits it is refused
        concept = CONCEPT.replace(
            "| SM-33 |", "| SM-58 | **Hardware threshold** (optional, *not credited*) | core stalled |\n| SM-33 |")
        cat = tracecheck.parse_catalogue(concept, FMEA)
        self.assertTrue(cat.mechanisms["SM-58"].stage1)
        self.assertFalse(cat.mechanisms["SM-58"].credited)
        self.assertTrue(cat.mechanisms["SM-01"].credited)
        result = self.run_tree(**{"doc/safety/concept.md": concept})
        self.assertFalse([m for m in find(result, "mech-without-req") if "SM-58" in m.where])
        text = REQUIREMENTS.replace("mechanisms: [SM-02]", "mechanisms: [SM-02, SM-58]")
        result = self.run_tree(**{"doc/safety/concept.md": concept, "doc/safety/requirements.yaml": text})
        self.assertMessage(result, "stage", "doc/safety/requirements.yaml: requirements[SSR-002].mechanisms[1]")
        # a test cited only as evidence of the not-credited mechanism needs no requirement; for a credited one it does
        fmea = FMEA.replace("## 6. New findings", "#### SM-58 — Hardware threshold\n\n| Field | Content |\n"
                            "|---|---|\n| Evidence | `other` (host) |\n\n## 6. New findings")
        result = self.run_tree(**{"doc/safety/concept.md": concept, "doc/safety/fmea.md": fmea})
        self.assertFalse(find(result, "test-without-req"), [str(m) for m in result.messages])
        # without the mark the same row is a credited stage-1 mechanism: it needs a requirement, and so does its test
        result = self.run_tree(**{"doc/safety/concept.md": concept.replace(" (optional, *not credited*)", ""),
                                  "doc/safety/fmea.md": fmea})
        self.assertMessage(result, "mech-without-req", "doc/safety/concept.md: SM-58")
        self.assertMessage(result, "test-without-req", "tools/test/tests.yaml: tests[other].requirements")

    def test_mechanism_and_goal_without_requirement(self):
        text = REQUIREMENTS.split("  - id: SSR-002")[0]
        result = self.run_tree(**{"doc/safety/requirements.yaml": text})
        self.assertMessage(result, "mech-without-req", "doc/safety/concept.md: SM-02")
        self.assertMessage(result, "goal-without-req", "doc/safety/concept.md: ASG-01")

    def test_open_requirement_is_a_warning_not_an_error(self):
        text = REQUIREMENTS.replace("    status: partial\n    verification: [review]\n    code:\n      - file: ecu/wd.cpp\n"
                                    "        symbol: check\n", "    status: open\n    verification: [test]\n")
        result = self.run_tree(**{"doc/safety/requirements.yaml": text})
        self.assertEqual(kinds(result), set(), [str(m) for m in result.messages])
        self.assertMessage(result, "req-open", "doc/safety/requirements.yaml: requirements[SSR-002]", tracecheck.WARNING)
        self.assertMessage(result, "goal-open", "doc/safety/concept.md: ASG-01", tracecheck.WARNING)

    def test_code_tags(self):
        tagged = CODE.replace("void Watchdog::service", "// nx-req: SSR-001\nvoid Watchdog::service")
        result = self.run_tree(**{"ecu/wd.cpp": tagged, "libs/x/y.c": "/* nx-req: SSR-001, SSR-042 */\n",
                                  "tools/trace/example.py": "# nx-req: SSR-777 (the tool's own files are skipped)\n",
                                  "external/z.c": "// nx-req: SSR-778\n"})
        self.assertEqual([m.where for m in find(result, "dead-ref")], ["libs/x/y.c:1"])
        req = {r.id: r for r in result.requirements}
        self.assertTrue(req["SSR-001"].code[0].tagged)
        self.assertEqual(req["SSR-001"].tags, ["ecu/wd.cpp:2", "libs/x/y.c:1"])
        self.assertEqual(len(find(result, "tag-suggested", tracecheck.INFO)), 1)   # only SSR-002 left

    def test_safety_manual(self):
        result = self.run_tree(**{"doc/safety/safety-manual.md": "# Manual\n\n## AoU-01 Declare outputs\n"})
        self.assertMessage(result, "aou-without-manual", "doc/safety/fmea.md: AoU-02")
        self.assertEqual(len(find(result, "aou-without-manual")), 1)
        result = self.run_tree(**{"doc/safety/safety-manual.md": "AoU-01, AoU-02\n"})
        self.assertNotIn("aou-without-manual", kinds(result) | kinds(result, tracecheck.WARNING))

    def test_schema(self):
        text = REQUIREMENTS.replace("    title: Configuration checked", "    title: Configuration checked\n    colour: red")
        text = text.replace("status: partial", "status: done").replace("id: SSR-002", "id: SSR-001")
        result = self.run_tree(**{"doc/safety/requirements.yaml": text})
        wheres = {m.where for m in find(result, "schema")}
        self.assertIn("doc/safety/requirements.yaml: requirements[SSR-001].colour", wheres)
        self.assertIn("doc/safety/requirements.yaml: requirements[SSR-001].status", wheres)
        self.assertIn("doc/safety/requirements.yaml: requirements[SSR-001].id", wheres)   # duplicate
        text = REQUIREMENTS.replace("    code:\n      - file: ecu/wd.cpp\n        symbol: check\n", "")
        self.assertMessage(self.run_tree(**{"doc/safety/requirements.yaml": text}), "schema",
                           "doc/safety/requirements.yaml: requirements[SSR-002].code")
        text = REQUIREMENTS.replace("id: SSR-002", "id: REQ-2")
        self.assertMessage(self.run_tree(**{"doc/safety/requirements.yaml": text}), "schema",
                           "doc/safety/requirements.yaml: requirements[REQ-2].id")

    def test_missing_sources(self):
        result = self.run_tree(**{"doc/safety/fmea.md": None})
        self.assertMessage(result, "dead-ref", "doc/safety/requirements.yaml: sources.fmea")
        tree = Tree(**{"doc/safety/requirements.yaml": None})
        self.addCleanup(tree.close)
        self.assertIn("dead-ref", kinds(tree.check()))


class SdocGoodCase(unittest.TestCase):
    """The same synthetic repository with the StrictDoc model (fixture JSON, no StrictDoc needed)."""

    def setUp(self):
        self.tree = SdocTree()
        self.addCleanup(self.tree.close)

    def test_model(self):
        model = sdocmodel.parse_export(fixture_json())
        self.assertEqual([n.uid for n in model.of_type("SSR")], ["SSR-001", "SSR-002", "SSR-003"])
        self.assertEqual(model.by_uid()["SSR-001"].parents(sdocmodel.ROLE_DERIVED_FROM), ["ASG-02", "AoU-01"])
        self.assertEqual(model.by_uid()["SSR-002"].parents(None), ["BSR-SAFE-001"])
        self.assertEqual(len(model.documents), 3)
        # the independent scan: every node UID, none from inside a multi-line value (the TEXT names SSR-999)
        scanned = sdocmodel.scan_uids(sdocmodel.sdoc_files(self.tree.root / "doc/requirements"), self.tree.root)
        self.assertEqual(set(scanned), model.uids)
        self.assertNotIn("SSR-999", scanned)
        self.assertEqual(scanned["SSR-001"], [("doc/requirements/ssr.sdoc", 10)])
        self.assertEqual(sdocmodel.split_list("F9, G1,T1 ,"), ["F9", "G1", "T1"])

    def test_good_case_equals_the_yaml_case(self):
        self.tree.matrix()
        result = self.tree.check()
        self.assertEqual(kinds(result), set(), [str(m) for m in result.messages])
        self.assertEqual(kinds(result, tracecheck.WARNING),
                         {"req-without-test", "mech-without-test", "aou-without-manual"})
        self.assertEqual([m.where for m in find(result, "mech-without-test", tracecheck.WARNING)],
                         ["doc/requirements/safety_catalogue.sdoc: SM-02"])
        self.assertIn("doc/requirements/safety_catalogue.sdoc: AoU-02",
                      [m.where for m in find(result, "aou-without-manual", tracecheck.WARNING)])
        # the catalogue comes from the nodes (withdrawn ASG-03 left out), findings from concept.md / fmea.md
        cat = result.catalogue
        self.assertEqual(sorted(cat.goals), ["ASG-01", "ASG-02"])
        self.assertEqual(cat.aous, {"AoU-01": False, "AoU-02": True})
        self.assertEqual({m for m, v in cat.mechanisms.items() if not v.stage1}, {"SM-33", "SM-40"})
        self.assertEqual(cat.withdrawn, {"ASG-03"})
        self.assertEqual(cat.findings, {"F1", "G1", "F9", "T1", "N1"})
        # same requirements as the requirements.yaml of the same tree; the withdrawn SSR-003 is not checked
        yaml_tree = Tree()
        self.addCleanup(yaml_tree.close)
        yaml_result = yaml_tree.check(matrix=False)
        self.assertEqual([vars(r) for r in result.requirements], [vars(r) for r in yaml_result.requirements])

    def test_matrix_names_the_sdoc(self):
        result = self.tree.check(matrix=False)
        text = tracecheck.render_matrix(result)
        self.assertIn("from the StrictDoc documents in `doc/requirements`", text)
        self.assertIn("| SSR-001 | **Watchdog serviced** |", text)
        self.assertIn("matrix-stale", kinds(self.tree.check()))
        self.tree.matrix()
        self.assertNotIn("matrix-stale", kinds(self.tree.check()))
        self.tree.write("doc/safety/traceability.md", text.replace("Watchdog serviced", "changed"))
        self.assertIn("matrix-stale", kinds(self.tree.check()))

    def test_cli(self):
        out = io.StringIO()
        self.tree.matrix()
        with redirect_stdout(out):
            self.assertEqual(tracecheck.main(["check", *self.tree.args()]), 0)
            self.assertEqual(tracecheck.main(["check", "--strict", *self.tree.args()]), 1)
        self.assertIn("trace: 2 requirements", out.getvalue())

    def test_bsr_in_tests_yaml(self):
        tree = SdocTree(**{"tools/test/tests.yaml": TESTS.replace("[SSR-001]", "[SSR-001, BSR-SAFE-001]")})
        self.addCleanup(tree.close)
        self.assertEqual(kinds(tree.check(matrix=False)), set())


class SdocFaults(unittest.TestCase):
    """Each rule on the StrictDoc model, against an export (and .sdoc files) broken on purpose."""

    SSR = "doc/requirements/ssr.sdoc"
    CAT = "doc/requirements/safety_catalogue.sdoc"

    def run_tree(self, edit=None, **overrides) -> tracecheck.Result:
        data = fixture_json()
        if edit:
            edit(data)
        tree = SdocTree(json_data=data, **overrides)
        self.addCleanup(tree.close)
        return tree.check(matrix=False)

    def assertMessage(self, result, kind, where, severity=tracecheck.ERROR):
        wheres = [m.where for m in find(result, kind, severity)]
        self.assertIn(where, wheres, [str(m) for m in result.messages])

    @staticmethod
    def relation(data, uid, index, value):
        sdoc_node(data, uid)["RELATIONS"][index]["VALUE"] = value

    def test_dead_reference_to_goal_mechanism_and_finding(self):
        def edit(data):
            self.relation(data, "SSR-001", 0, "ASG-09")
            self.relation(data, "SSR-001", 2, "SM-99")
            sdoc_node(data, "SSR-001")["FINDINGS"] = "F77"
        result = self.run_tree(edit)
        self.assertMessage(result, "dead-ref", f"{self.SSR}: SSR-001.RELATIONS(DerivedFrom)[0]")
        self.assertMessage(result, "dead-ref", f"{self.SSR}: SSR-001.RELATIONS(Specifies)[0]")
        self.assertMessage(result, "dead-ref", f"{self.SSR}: SSR-001.FINDINGS[0]")

    def test_derived_from_takes_goals_and_assumptions_only(self):
        result = self.run_tree(lambda data: self.relation(data, "SSR-002", 1, "SM-02"))
        self.assertMessage(result, "schema", f"{self.SSR}: SSR-002.RELATIONS(DerivedFrom)[0]")

    def test_withdrawn_goal_and_requirement(self):
        result = self.run_tree(lambda data: self.relation(data, "SSR-002", 1, "ASG-03"),
                               **{"tools/test/tests.yaml": TESTS.replace("[SSR-001]", "[SSR-001, SSR-003]")})
        self.assertIn("withdrawn", [m for m in find(result, "dead-ref")
                                    if m.where == f"{self.SSR}: SSR-002.RELATIONS(DerivedFrom)[0]"][0].text)
        self.assertIn("withdrawn", [m for m in find(result, "dead-ref")
                                    if m.where == "tools/test/tests.yaml: tests[wd-test].requirements[1]"][0].text)

    def test_stage2_mechanism_refused_in_stage1(self):
        def edit(data):
            sdoc_node(data, "SSR-002")["RELATIONS"].append({"TYPE": "Parent", "VALUE": "SM-40", "ROLE": "Specifies"})
        self.assertMessage(self.run_tree(edit), "stage", f"{self.SSR}: SSR-002.RELATIONS(Specifies)[1]")

    def test_mechanism_and_goal_without_requirement(self):
        ssr = (SDOC_FIXTURE / "ssr.sdoc").read_text(encoding="utf-8")
        ssr = ssr[:ssr.index("[SSR]\nUID: SSR-002")] + ssr[ssr.index("[SSR]\nUID: SSR-003"):]
        result = self.run_tree(lambda data: drop_sdoc_node(data, "SSR-002"), **{self.SSR: ssr})
        self.assertMessage(result, "mech-without-req", f"{self.CAT}: SM-02")
        self.assertMessage(result, "goal-without-req", f"{self.CAT}: ASG-01")   # SSR-003 is withdrawn
        self.assertNotIn("uid-lost", kinds(result))

    def test_requirement_and_evidence_test_without_link(self):
        result = self.run_tree(**{"tools/test/tests.yaml": TESTS.replace("    requirements: [SSR-001]\n", "")})
        self.assertMessage(result, "req-without-test", f"{self.SSR}: SSR-001.VERIFICATION")
        self.assertMessage(result, "test-without-req", "tools/test/tests.yaml: tests[wd-test].requirements")
        self.assertMessage(result, "mech-without-test", f"{self.CAT}: SM-01", tracecheck.WARNING)

    def test_safety_manual(self):
        result = self.run_tree(**{"doc/safety/safety-manual.md": "# Manual\n\n## AoU-01 Declare outputs\n"})
        self.assertMessage(result, "aou-without-manual", f"{self.CAT}: AoU-02")
        self.assertEqual(len(find(result, "aou-without-manual")), 1)

    def test_uid_cross_check(self):
        # StrictDoc "lost" a node: in the .sdoc file, not in the export
        result = self.run_tree(lambda data: drop_sdoc_node(data, "SSR-002"))
        self.assertMessage(result, "uid-lost", f"{self.SSR}:34")
        # a node in the export that no .sdoc file has
        result = self.run_tree(lambda data: sdoc_node(data, "SSR-002").update(UID="SSR-004"))
        self.assertMessage(result, "uid-lost", "doc/requirements: SSR-004")
        # a field lost on the way: required field missing in the export
        result = self.run_tree(lambda data: sdoc_node(data, "SSR-001").pop("STATUS"))
        self.assertMessage(result, "schema", f"{self.SSR}: SSR-001.STATUS")
        # the same UID twice in the files (StrictDoc refuses it within a document, the scan across documents)
        bsw = (SDOC_FIXTURE / "stakeholder_bsw.sdoc").read_text(encoding="utf-8") + "\n[STK]\nUID: SSR-001\n"
        result = self.run_tree(**{"doc/requirements/stakeholder_bsw.sdoc": bsw})
        self.assertIn("duplicate UID SSR-001", " ".join(m.text for m in find(result, "schema")))

    def test_uid_format_and_parent_types(self):
        ssr = (SDOC_FIXTURE / "ssr.sdoc").read_text(encoding="utf-8").replace("SSR-002", "SSR-82")
        bsw = (SDOC_FIXTURE / "stakeholder_bsw.sdoc").read_text(encoding="utf-8").replace("STK-001", "STK-1")

        def edit(data):
            sdoc_node(data, "SSR-002").update(UID="SSR-82")
            sdoc_node(data, "STK-001").update(UID="STK-1", RELATIONS=[{"TYPE": "Parent", "VALUE": "BSR-SAFE-001"}])
            sdoc_node(data, "BSR-SAFE-001")["RELATIONS"] = [{"TYPE": "Parent", "VALUE": "SM-01"},
                                                            {"TYPE": "Parent", "VALUE": "BSR-GONE-001"}]
        result = self.run_tree(edit, **{self.SSR: ssr, "doc/requirements/stakeholder_bsw.sdoc": bsw})
        bsw_file = "doc/requirements/stakeholder_bsw.sdoc"
        self.assertMessage(result, "schema", f"{self.SSR}: SSR-82.UID")
        self.assertMessage(result, "schema", f"{bsw_file}: STK-1.UID")
        self.assertMessage(result, "schema", f"{bsw_file}: STK-1.RELATIONS(Parent)[0]")          # STK has no parent
        self.assertMessage(result, "schema", f"{bsw_file}: BSR-SAFE-001.RELATIONS(Parent)[0]")   # SM is no STK
        self.assertMessage(result, "dead-ref", f"{bsw_file}: BSR-SAFE-001.RELATIONS(Parent)[1]")

    def test_not_credited_mechanism(self):
        # DOC-21 step 3: CREDITED no on a stage-1 SM node (SM-58, SM-59) - described, claims nothing: it needs no
        # requirement, and a requirement that credits it is refused
        def edit(data):
            sdoc_node(data, "SM-02")["CREDITED"] = "no"
        result = self.run_tree(edit)
        self.assertTrue(result.catalogue.mechanisms["SM-02"].stage1)
        self.assertFalse(result.catalogue.mechanisms["SM-02"].credited)
        self.assertMessage(result, "stage", f"{self.SSR}: SSR-002.RELATIONS(Specifies)[0]")
        self.assertNotIn("SM-02", [m.where.split(": ")[1] for m in find(result, "mech-without-req")])

    def test_stage1_mechanism_needs_fault_model(self):
        result = self.run_tree(lambda data: sdoc_node(data, "SM-01").pop("FAULT_MODEL"))
        self.assertMessage(result, "schema", f"{self.CAT}: SM-01.FAULT_MODEL")
        # a stage-2 mechanism may describe itself by its item table only
        result = self.run_tree(lambda data: sdoc_node(data, "SM-40").pop("FAULT_MODEL"))
        self.assertNotIn(f"{self.CAT}: SM-40.FAULT_MODEL", [m.where for m in find(result, "schema")])

    def test_fmea_section_names_a_mechanism_node(self):
        result = self.run_tree(**{"doc/safety/fmea.md": FMEA.replace("#### SM-02 —", "#### SM-07 —")})
        self.assertMessage(result, "dead-ref", "doc/safety/fmea.md:" + str(FMEA.splitlines().index(
            "#### SM-02 — Configuration check") + 1))

    def test_default_source(self):
        # the StrictDoc project when the repository has it (DOC-21 step 3), otherwise a requirements.yaml
        tree = SdocTree()
        self.addCleanup(tree.close)
        self.assertEqual(tracecheck.default_source(tree.root), (tracecheck.SDOC_ROOT, None))
        plain = Tree()
        self.addCleanup(plain.close)
        self.assertEqual(tracecheck.default_source(plain.root), (None, tracecheck.REQUIREMENTS))
        self.assertEqual(plain.check(matrix=False).origin, tracecheck.REQUIREMENTS)

    def test_catalogue_missing_and_strictdoc_missing(self):
        def edit(data):
            for uid in ("SM-01", "SM-02", "SM-33", "SM-40"):
                drop_sdoc_node(data, uid)
        result = self.run_tree(edit)
        self.assertIn("no ASG, AOU or SM node in the StrictDoc model", [m.text for m in find(result, "schema")])
        # without a JSON file tools/trace runs StrictDoc; a missing StrictDoc is an error, never a silent pass
        tree = SdocTree(json_file=False)
        self.addCleanup(tree.close)
        original = sdocmodel.find_strictdoc
        sdocmodel.find_strictdoc = lambda: None
        self.addCleanup(setattr, sdocmodel, "find_strictdoc", original)
        result = tree.check(matrix=False)
        self.assertTrue(any("not found" in m.text for m in find(result, "schema")), [str(m) for m in result.messages])


class SdocClauses(unittest.TestCase):
    """DOC-43 (step 3b): one node per lettered clause with its own verification and test entries, review status."""

    SSR = "doc/requirements/ssr.sdoc"

    def run_tree(self, clauses: list[dict], edit=None, keep_statement: bool = False, matrix: bool = False,
                 **overrides) -> tracecheck.Result:
        data = fixture_json()
        text = with_clauses(data, clauses, keep_statement)
        if edit:
            edit(data)
        tree = SdocTree(json_data=data, **{self.SSR: text, **overrides})
        self.addCleanup(tree.close)
        if matrix:
            tree.matrix()
        return tree.check(matrix=matrix)

    def good(self) -> list[dict]:
        return [clause_node("SSR-001(a)", CLAUSE_TEXTS["a"], tests="wd-test"),
                clause_node("SSR-001(b)", CLAUSE_TEXTS["b"])]

    def where(self, result, kind, severity=tracecheck.ERROR) -> list[str]:
        return [m.where for m in find(result, kind, severity)]

    def test_clauses_are_read_and_the_text_rebuilt(self):
        result = self.run_tree(self.good(), matrix=True)
        self.assertEqual(kinds(result), set(), [str(m) for m in result.messages])
        req = {r.id: r for r in result.requirements}["SSR-001"]
        self.assertEqual([c.id for c in req.clauses], ["SSR-001(a)", "SSR-001(b)"])
        self.assertEqual(req.text, f"(a) {CLAUSE_TEXTS['a']} (b) {CLAUSE_TEXTS['b']}")
        self.assertEqual(req.clauses[0].tests, ["wd-test"])
        self.assertEqual(req.review, "inspected")
        # R7: a clause verified by test without a test entry is a warning, at the clause's field
        self.assertEqual(self.where(result, "clause-without-test", tracecheck.WARNING), [f"{self.SSR}: SSR-001(b).TESTS"])
        self.assertEqual(self.where(result, "test-not-allocated", tracecheck.WARNING), [])
        text = tracecheck.render_matrix(result)
        self.assertIn("## Clauses", text)
        self.assertIn("| SSR-001(a) | implemented | test | `wd-test` | - |", text)
        self.assertIn("| SSR-001(b) | implemented | test | **none** | - |", text)
        self.assertIn("| Review status | proposed 0, rework 1, inspected 1, accepted 0 |", text)
        self.assertIn(" | inspected |", text)

    def test_test_entry_no_clause_claims(self):
        clauses = [clause_node("SSR-001(a)", CLAUSE_TEXTS["a"]), clause_node("SSR-001(b)", CLAUSE_TEXTS["b"])]
        result = self.run_tree(clauses)
        self.assertEqual(self.where(result, "test-not-allocated", tracecheck.WARNING), [f"{self.SSR}: SSR-001"])

    def test_clause_test_must_be_an_entry_of_its_requirement(self):
        clauses = [clause_node("SSR-001(a)", CLAUSE_TEXTS["a"], tests="wd-test, other, gone"),
                   clause_node("SSR-001(b)", CLAUSE_TEXTS["b"], tests="wd-test")]
        result = self.run_tree(clauses)
        self.assertEqual(self.where(result, "clause-test"), [f"{self.SSR}: SSR-001(a).TESTS[1]"])
        self.assertIn(f"{self.SSR}: SSR-001(a).TESTS[2]", self.where(result, "dead-ref"))

    def test_tests_the_export_removed(self):
        """DOC-85: in an exported tree a test entry the export removed and lists in doc/unpublished.yaml is no dead
        reference, and a requirement whose clauses name only such tests counts as tested; an unlisted missing test
        stays a dead reference, a list the export did not write is an error, and without a list nothing changes."""
        listed = {"doc/unpublished.yaml": "generator: tools/distcheck export\ntests: [gone, wd-test]\n"}
        clauses = [clause_node("SSR-001(a)", CLAUSE_TEXTS["a"], tests="wd-test, gone, missing"),
                   clause_node("SSR-001(b)", CLAUSE_TEXTS["b"], tests="wd-test")]
        result = self.run_tree(clauses, **listed)
        self.assertEqual(self.where(result, "dead-ref"), [f"{self.SSR}: SSR-001(a).TESTS[2]"])   # missing only
        self.assertEqual(result.unpublished_tests, {"gone"})        # wd-test is in tests.yaml: not removed
        result = self.run_tree(clauses)
        self.assertEqual(self.where(result, "dead-ref"), [f"{self.SSR}: SSR-001(a).TESTS[1]",
                                                          f"{self.SSR}: SSR-001(a).TESTS[2]"])
        self.assertEqual(result.unpublished_tests, frozenset())
        foreign = {"doc/unpublished.yaml": "tests: [gone]\n"}
        result = self.run_tree(clauses, **foreign)
        self.assertEqual(self.where(result, "unpublished-list"), ["doc/unpublished.yaml"])
        self.assertIn(f"{self.SSR}: SSR-001(a).TESTS[1]", self.where(result, "dead-ref"))
        # a requirement whose only test entries the export removed: tested in the export, not without the list
        tests = TESTS.replace("    requirements: [SSR-001]\n", "")
        only = [clause_node("SSR-001(a)", CLAUSE_TEXTS["a"], tests="gone"), clause_node("SSR-001(b)", CLAUSE_TEXTS["b"])]
        result = self.run_tree(only, **listed, **{"tools/test/tests.yaml": tests})
        self.assertEqual((self.where(result, "dead-ref"), self.where(result, "req-without-test")), ([], []))
        result = self.run_tree(only, **{"tools/test/tests.yaml": tests})
        self.assertTrue(self.where(result, "dead-ref") and self.where(result, "req-without-test"))

    def test_matrix_the_export_left_out(self):
        """DOC-85: a missing matrix that the export lists as left out is not compared; unlisted it is stale, and a
        matrix that is there is compared even when listed."""
        matrix = "doc/safety/traceability.md"
        with_list = f"generator: tools/distcheck export\nfiles: [{matrix}]\n"
        for files, stale in (({"doc/unpublished.yaml": with_list}, False), ({}, True),
                             ({"doc/unpublished.yaml": with_list, matrix: "# old matrix\n"}, True),
                             ({"doc/unpublished.yaml": "generator: tools/distcheck export\nfiles: [other.md]\n"}, True)):
            data = fixture_json()
            tree = SdocTree(json_data=data, **{self.SSR: with_clauses(data, self.good()), **files})
            self.addCleanup(tree.close)
            result = tree.check(matrix=True)
            self.assertEqual(bool(self.where(result, "matrix-stale")), stale, files)

    def test_clause_verification_within_its_requirement(self):
        clauses = [clause_node("SSR-001(a)", CLAUSE_TEXTS["a"], verification="test, board", tests="wd-test"),
                   clause_node("SSR-001(b)", CLAUSE_TEXTS["b"], verification="review")]
        result = self.run_tree(clauses)
        self.assertIn(f"{self.SSR}: SSR-001(a).VERIFICATION[1]", self.where(result, "clause-verification"))
        self.assertIn(f"{self.SSR}: SSR-001(b).VERIFICATION[0]", self.where(result, "clause-verification"))
        # every method of the requirement is claimed by a clause in force
        clauses = [clause_node("SSR-001(a)", CLAUSE_TEXTS["a"], verification="test", tests="wd-test",
                               STATUS="withdrawn")]
        result = self.run_tree(clauses)
        self.assertIn(f"{self.SSR}: SSR-001.VERIFICATION", self.where(result, "clause-verification"))
        self.assertIn(f"{self.SSR}: SSR-001(a).TESTS", self.where(result, "schema"))   # withdrawn claims no test

    def test_withdrawn_clause_keeps_its_letter_and_leaves_the_text(self):
        clauses = [clause_node("SSR-001(a)", CLAUSE_TEXTS["a"], verification="test", STATUS="withdrawn"),
                   clause_node("SSR-001(b)", CLAUSE_TEXTS["b"], tests="wd-test")]
        result = self.run_tree(clauses)
        self.assertEqual(kinds(result), set(), [str(m) for m in result.messages])
        req = {r.id: r for r in result.requirements}["SSR-001"]
        self.assertEqual(req.text, f"(b) {CLAUSE_TEXTS['b']}")
        self.assertEqual(self.where(result, "clause-without-test", tracecheck.WARNING), [])

    def test_letters_uid_and_parent(self):
        clauses = [clause_node("SSR-001(b)", CLAUSE_TEXTS["a"], tests="wd-test"),
                   clause_node("SSR-001(a)", CLAUSE_TEXTS["b"])]
        self.assertIn(f"{self.SSR}: SSR-001(b).UID", self.where(self.run_tree(clauses), "schema"))
        # a clause UID of another format, a clause without parent and a clause under a goal
        clauses = [clause_node("SSR-001(a)", CLAUSE_TEXTS["a"], tests="wd-test"),
                   clause_node("SSR-1(b)", CLAUSE_TEXTS["b"], parent=""),
                   clause_node("SSR-001(c)", CLAUSE_TEXTS["b"], parent="ASG-01")]
        result = self.run_tree(clauses)
        wheres = self.where(result, "schema")
        self.assertIn(f"{self.SSR}: SSR-1(b).UID", wheres)
        self.assertIn(f"{self.SSR}: SSR-1(b).RELATIONS(Parent)", wheres)
        self.assertIn(f"{self.SSR}: SSR-001(c).RELATIONS(Parent)[0]", wheres)

    def test_statement_and_clauses_exclude_each_other(self):
        result = self.run_tree(self.good(), keep_statement=True)
        self.assertIn(f"{self.SSR}: SSR-001.STATEMENT", self.where(result, "schema"))

    def test_review_status_values(self):
        def edit(data):
            sdoc_node(data, "SSR-002")["REVIEW_STATUS"] = "done"
            sdoc_node(data, "SSR-001(b)")["REVIEW_STATUS"] = "maybe"
        result = self.run_tree(self.good(), edit)
        self.assertIn(f"{self.SSR}: SSR-002.REVIEW_STATUS", self.where(result, "schema"))
        self.assertIn(f"{self.SSR}: SSR-001(b).REVIEW_STATUS", self.where(result, "schema"))


if __name__ == "__main__":
    unittest.main()
