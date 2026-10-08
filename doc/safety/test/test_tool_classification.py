"""PLAT-75: consistency of the tool classification doc/safety/tool-classification.md with the repository.

Checks
- the summary table (between the markers tool-summary:start/end) applies the classification rule of section 1.1
  with the class names of ISO 26262-8, clause 11.4.5.2 (PLAT-75 norm follow-up):
  TI1 (no impact) -> TCL 1; TI2 with TD1/TD2/TD3 -> TCL 1/2/3; the target level is never worse than today's;
- every tool named in the task (nxconfig, bswgen, rtegen, dbcgen, blgen, a2lgen, schedana, Studio) has a row;
- every path of the table exists, and every test named in the column "Tests" is an entry of tools/test/tests.yaml;
- every tool the build runs (a script or tool folder under a `tools/` directory referenced from a CMakeLists.txt or
  *.cmake outside external/) is covered by a row whose path contains it;
- every proposal letter used in the table is defined in section 7 with a non-trivial "done when";
- every requirement listed in section 8 exists in ssr.sdoc (doc/requirements/safety, read through tools/trace and
  StrictDoc's JSON export since DOC-21 step 3), points back to this document, and has the status
  of the column "Status" there (open, or implemented once its proposal is done - SYS-49: SSR-080 and SSR-084,
  PLAT-80: SSR-083, PLAT-82: SSR-081 with B and F); an
  implemented one is named by at least one entry of tools/test/tests.yaml and its proposal is marked done in section 7;
- licence rule for ISO 26262 (user 2026-09-22): the standard is cited by part and clause only - no reference to one
  of its tables or figures and no recommendation marks copied from its tables;
- the record of the norm questions TN1-TN5 (markers tn-record:start/end) has exactly one row per question, a status
  of "answered", "answered, decision open" or "open", an answered row cites at least one clause of ISO 26262, and
  every row says what it changed or why it is still open.
Counter-checks with deliberately broken copies of the document show that each rule sees its fault.

Run: .venv/Scripts/python -m unittest doc/safety/test/test_tool_classification.py
"""

import pathlib
import re
import subprocess
import sys
import unittest

import yaml

REPO = pathlib.Path(__file__).resolve().parents[3]
SAFETY = REPO / "doc" / "safety"
sys.path.insert(0, str(REPO / "tools" / "trace"))
import tracecheck  # noqa: E402  (StrictDoc model of the SSR, DOC-21)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_safety_plan import load_unpublished  # noqa: E402  (DOC-85: what the export left out on purpose)
DOC = SAFETY / "tool-classification.md"

TASK_TOOLS = ("nxconfig", "bswgen", "rtegen", "dbcgen", "blgen", "a2lgen", "schedana", "Studio")
RULE = {("TI1", "TD1"): 1, ("TI1", "TD2"): 1, ("TI1", "TD3"): 1,
        ("TI2", "TD1"): 1, ("TI2", "TD2"): 2, ("TI2", "TD3"): 3}
TN_STATUS = ("answered", "answered, decision open", "open")
TN_COLUMNS = ("ID", "Question", "Status", "Answer (own words, with clause)", "Changed in this document")
COLUMNS = ("Tool", "Path", "TI", "TD today", "TCL today", "TCL target", "Safeguards today", "Tests", "Proposals")


def _cells(line: str) -> list:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def summary_rows(text: str) -> list:
    """Rows of the summary table as dicts; raises ValueError on a malformed table."""
    try:
        block = text.split("<!-- tool-summary:start -->", 1)[1].split("<!-- tool-summary:end -->", 1)[0]
    except IndexError:
        raise ValueError("summary markers missing") from None
    lines = [line for line in block.splitlines() if line.startswith("|")]
    if len(lines) < 3 or tuple(_cells(lines[0])) != COLUMNS:
        raise ValueError(f"summary header is not {COLUMNS}")
    rows = []
    for line in lines[2:]:
        cells = _cells(line)
        if len(cells) != len(COLUMNS):
            raise ValueError(f"row with {len(cells)} cells: {line[:60]}")
        rows.append(dict(zip(COLUMNS, cells)))
    return rows


def rule_problems(rows: list) -> list:
    problems = []
    for row in rows:
        key = (row["TI"], row["TD today"])
        if key not in RULE:
            problems.append(f"{row['Tool']}: unknown TI/TD {key}")
            continue
        today = re.fullmatch(r"TCL ([123])", row["TCL today"])
        target = re.fullmatch(r"TCL ([123])", row["TCL target"])
        if not today or not target:
            problems.append(f"{row['Tool']}: TCL not of the form 'TCL n'")
            continue
        if int(today.group(1)) != RULE[key]:
            problems.append(f"{row['Tool']}: {key} gives TCL {RULE[key]}, the table says {row['TCL today']}")
        if int(target.group(1)) > int(today.group(1)):
            problems.append(f"{row['Tool']}: target {row['TCL target']} worse than today")
    return problems


def row_paths(row: dict) -> list:
    return re.findall(r"`([^`]+)`", row["Path"])


def row_tests(row: dict) -> list:
    return re.findall(r"`([^`]+)`", row["Tests"])


def row_proposals(row: dict) -> set:
    return set() if row["Proposals"] in ("-", "") else {p.strip() for p in row["Proposals"].split(",")}


def proposals(text: str) -> dict:
    """Letter -> "done when" text of the table in section 7."""
    section = text.split("## 7. Proposed tasks", 1)[1].split("\n## 8.", 1)[0]
    found = {}
    for line in section.splitlines():
        match = re.match(r"\|\s*\*\*([A-Z])\*\*\s*\|", line)
        if match:
            cells = _cells(line)
            found[match.group(1)] = cells[2] if len(cells) > 2 else ""
    return found


def section8_requirements(text: str) -> dict:
    """Requirement ID -> (status word of the column "Status", proposal letters) of the table in section 8."""
    section = text.split("## 8. Requirements added", 1)[1].split("\n## 9.", 1)[0]
    found = {}
    for line in section.splitlines():
        if re.match(r"\|\s*SSR-\d{3}\s*\|", line):
            cells = _cells(line)
            status = cells[5].split()[0] if len(cells) == 6 and cells[5] else ""
            found[cells[0]] = (status, {p.strip() for p in cells[4].split(",")} if len(cells) > 4 else set())
    return found


def done_proposals(text: str) -> set:
    """Letters of section 7 whose row says "done" in the column "Priority, dependencies"."""
    section = text.split("## 7. Proposed tasks", 1)[1].split("\n## 8.", 1)[0]
    done = set()
    for line in section.splitlines():
        match = re.match(r"\|\s*\*\*([A-Z])\*\*\s*\|", line)
        if match and len(_cells(line)) > 3 and "**done" in _cells(line)[3]:
            done.add(match.group(1))
    return done


def status_problems(listed: dict, requirements: list, test_links: dict, done: set) -> list:
    """Disagreements between section 8, section 7 and ssr.sdoc / tests.yaml (test_links: SSR -> tests)."""
    by_id = {r["id"]: r for r in requirements}
    problems = []
    for rid, (status, letters) in sorted(listed.items()):
        if rid not in by_id:
            problems.append(f"{rid} missing in ssr.sdoc")
            continue
        if status not in ("open", "implemented"):
            problems.append(f"{rid}: status column '{status}' is not open or implemented")
        if by_id[rid]["status"] != status:
            problems.append(f"{rid}: section 8 says '{status}', ssr.sdoc says '{by_id[rid]['status']}'")
        if "tool-classification.md" not in by_id[rid].get("note", ""):
            problems.append(f"{rid}: note does not point back to tool-classification.md")
        if status == "implemented":
            if not test_links.get(rid):
                problems.append(f"{rid}: implemented, but no entry of tests.yaml names it")
            if not letters & done:
                problems.append(f"{rid}: implemented, but none of its proposals {sorted(letters)} is done")
    return problems


def licence_problems(text: str) -> list:
    """References that go beyond part and clause: tables or figures, and the recommendation marks of its tables."""
    problems = [f"reference to a table or figure: '{m}'" for m in re.findall(r"\b(?:Table|Figure)\s+[A-Z]?\d+", text)]
    problems += [f"recommendation mark copied: '{line.strip()[:60]}'" for line in text.splitlines()
                 if re.search(r"(^|[\s|])(\+\+|\+|o)\s*\|\s*(\+\+|\+|o)(\s|\|)", line)]
    return problems


def tn_record(text: str) -> list:
    """Rows of the TN record as dicts; raises ValueError on a malformed table."""
    try:
        block = text.split("<!-- tn-record:start -->", 1)[1].split("<!-- tn-record:end -->", 1)[0]
    except IndexError:
        raise ValueError("tn-record markers missing") from None
    lines = [line for line in block.splitlines() if line.startswith("|")]
    if len(lines) < 2 or tuple(_cells(lines[0])) != TN_COLUMNS:
        raise ValueError(f"TN record header is not {TN_COLUMNS}")
    rows = []
    for line in lines[2:]:
        cells = _cells(line)
        if len(cells) != len(TN_COLUMNS):
            raise ValueError(f"TN row with {len(cells)} cells: {line[:60]}")
        rows.append(dict(zip(TN_COLUMNS, cells)))
    return rows


def tn_problems(rows: list) -> list:
    problems = []
    ids = [row["ID"] for row in rows]
    if ids != [f"TN{n}" for n in range(1, 6)]:
        problems.append(f"TN record rows are {ids}, expected TN1..TN5 once each")
    for row in rows:
        if row["Status"] not in TN_STATUS:
            problems.append(f"{row['ID']}: status '{row['Status']}' not in {TN_STATUS}")
        if row["Status"].startswith("answered") and not re.search(
                r"ISO 26262-\d+, (?:Annex [A-Z], )?clauses? [A-Z]?\d+", row["Answer (own words, with clause)"]):
            problems.append(f"{row['ID']}: answered without a clause of ISO 26262")
        if len(row["Changed in this document"]) < 10:
            problems.append(f"{row['ID']}: no consequence or reason given")
    return problems


def cmake_files() -> list:
    """CMakeLists.txt and *.cmake of the tree: git's file list in a clone; DOC-85: an exported tree has no .git, there
    the files on disk (without build output, external/ and the like)."""
    if (REPO / ".git").exists():
        return subprocess.run(["git", "ls-files", "*CMakeLists.txt", "*.cmake"], cwd=REPO, capture_output=True,
                              text=True, check=True).stdout.split()
    import os
    skip = {"build", "external", ".venv", ".git", "__pycache__", "node_modules"}
    found = []
    for folder, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in skip and not d.startswith("build")]
        base = pathlib.Path(folder).relative_to(REPO)
        found += [(base / f).as_posix() for f in files if f == "CMakeLists.txt" or f.endswith(".cmake")]
    return sorted(found)


def build_tool_paths() -> set:
    """Repository-relative paths under a `tools/` directory that CMake files outside external/ reference in code."""
    listed = cmake_files()
    found = set()
    # NX_ROOT ist seit ARCH-57 die Wurzel des neXtBSW-Baums: CMAKE_SOURCE_DIR zeigt in einem Workspace
    # ausserhalb des Baums auf das Kunden-Repo und wurde deshalb an 34 Stellen ersetzt
    pattern = re.compile(r"\$\{(CMAKE_SOURCE_DIR|CMAKE_CURRENT_SOURCE_DIR|NX_ROOT)\}/((?:[\w.-]+/)*?tools/[\w.-]+(?:/[\w.-]+)?)")
    for rel in listed:
        if rel.startswith(("external/", "build/")):
            continue
        base = pathlib.PurePosixPath(rel).parent
        for line in (REPO / rel).read_text(encoding="utf-8", errors="replace").splitlines():
            code = line.split("#", 1)[0]
            for var, path in pattern.findall(code):
                repo_relative = var in ("CMAKE_SOURCE_DIR", "NX_ROOT")
                resolved = pathlib.PurePosixPath(path) if repo_relative else base / path
                if (REPO / resolved).exists():
                    found.add(str(resolved))
    return found


def covered(path: str, rows: list) -> bool:
    for row in rows:
        for prefix in row_paths(row):
            if path == prefix or path.startswith(prefix.rstrip("/") + "/"):
                return True
    return False


class ToolClassificationConsistency(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DOC.read_text(encoding="utf-8")
        cls.rows = summary_rows(cls.text)
        tests = yaml.safe_load((REPO / "tools" / "test" / "tests.yaml").read_text(encoding="utf-8"))
        cls.test_ids = {entry["name"] for entry in tests["tests"]}
        # DOC-85: in the export a path or test entry the export left out (and lists) counts; empty in the repository
        cls.unpublished = load_unpublished()
        cls.test_links = {}
        for entry in tests["tests"]:
            for rid in entry.get("requirements") or []:
                cls.test_links.setdefault(rid, []).append(entry["name"])
        cls.requirements = tracecheck.safety_model().requirements

    def test_rule_applied(self):
        self.assertGreaterEqual(len(self.rows), 15)
        self.assertEqual(rule_problems(self.rows), [])

    def test_task_tools_present(self):
        names = {row["Tool"] for row in self.rows}
        self.assertEqual([t for t in TASK_TOOLS if t not in names], [])

    def test_paths_exist(self):
        for row in self.rows:
            paths = row_paths(row)
            self.assertTrue(paths, row["Tool"])
            for path in paths:
                self.assertTrue((REPO / path).exists() or self.unpublished.file(path),
                                f"{row['Tool']}: {path} does not exist")

    def test_named_tests_exist(self):
        for row in self.rows:
            tests = row_tests(row)
            self.assertTrue(tests, f"{row['Tool']}: no test named")
            self.assertEqual(sorted(set(tests) - self.test_ids - self.unpublished.tests), [],
                             f"{row['Tool']}: tests missing in tests.yaml")

    def test_every_build_tool_is_classified(self):
        paths = build_tool_paths()
        self.assertGreaterEqual(len(paths), 8, "CMake scan found too few tool references")
        self.assertEqual(sorted(p for p in paths if not covered(p, self.rows)), [],
                         "tools run by the build without a row in the summary table")

    def test_proposals_defined(self):
        defined = proposals(self.text)
        used = set().union(*(row_proposals(row) for row in self.rows))
        self.assertEqual(sorted(used - set(defined)), [], "proposal letters without a definition in section 7")
        for letter, done_when in defined.items():
            self.assertGreater(len(done_when), 60, f"proposal {letter}: 'done when' missing or too short")

    def test_requirements_exist_with_their_status(self):
        listed = section8_requirements(self.text)
        self.assertEqual(sorted(listed), [f"SSR-{n:03d}" for n in range(80, 85)])
        self.assertEqual(status_problems(listed, self.requirements, self.test_links, done_proposals(self.text)), [])
        # SYS-49: proposals A (PLAT-77) and E (PLAT-81) are done, D (PLAT-80) since 2026-09-22; PLAT-82: B (PLAT-78)
        # and F (PLAT-82) are done and SSR-081 is implemented; SAFETYDOC-1002: C (PLAT-79) is done, SSR-082 implemented
        self.assertEqual({rid: s for rid, (s, _) in listed.items()},
                         {"SSR-080": "implemented", "SSR-081": "implemented", "SSR-082": "implemented",
                          "SSR-083": "implemented", "SSR-084": "implemented"})
        self.assertEqual(done_proposals(self.text), {"A", "B", "C", "D", "E", "F"})

    def test_standard_cited_by_part_and_clause_only(self):
        self.assertEqual(licence_problems(self.text), [])

    def test_tn_record(self):
        self.assertEqual(tn_problems(tn_record(self.text)), [])

    # Counter-checks: every rule must see a broken document
    def test_parsers_detect_breakage(self):
        row = next(line for line in self.text.splitlines() if line.startswith("| bswgen | `tools/bswgen` |"))
        broken = self.text.replace(row, row.replace("| TCL 2 | TCL 1 |", "| TCL 1 | TCL 1 |"), 1)
        self.assertTrue(rule_problems(summary_rows(broken)))
        broken = self.text.replace(row, row.replace("| TCL 2 | TCL 1 |", "| TCL 2 | TCL 3 |"), 1)
        self.assertTrue(rule_problems(summary_rows(broken)))
        broken = self.text.replace(row + "\n", "", 1)
        self.assertFalse(covered("tools/bswgen/bswgen.py", summary_rows(broken)))
        self.assertNotIn("bswgen", {r["Tool"] for r in summary_rows(broken)})
        broken = self.text.replace("`bswgen-mpu`", "`bswgen-mpu-missing`", 1)
        tests = {t for r in summary_rows(broken) for t in row_tests(r)}
        self.assertIn("bswgen-mpu-missing", tests)
        broken = self.text.replace("| **A** | Safe outputs table checked entry by entry in the linked image | ",
                                   "| **A** | Safe outputs table checked entry by entry in the linked image | tbd | ", 1)
        self.assertLessEqual(len(proposals(broken)["A"]), 60)
        broken = self.text.replace("| **H** |", "| H |", 1)
        self.assertNotIn("H", proposals(broken))
        # licence rule and TN record (PLAT-75 norm follow-up)
        self.assertEqual(licence_problems(self.text + "\nsee ISO 26262-8, clause 11.4.6\n"), [])
        self.assertTrue(licence_problems(self.text + "\nsee ISO 26262-8, Table 5\n"))
        self.assertTrue(licence_problems(self.text + "\n| 1c | Validation | + | + | + | ++ |\n"))
        rows = tn_record(self.text)
        self.assertTrue(tn_problems(rows[:4]))                                           # TN5 missing
        self.assertTrue(tn_problems(rows + rows[-1:]))                                    # TN5 twice
        self.assertTrue(tn_problems([dict(rows[0], Status="maybe")] + rows[1:]))
        answer = "Answer (own words, with clause)"
        self.assertTrue(tn_problems([dict(rows[0], **{answer: "yes, per use case"})] + rows[1:]))
        self.assertTrue(tn_problems(rows[:-1] + [dict(rows[-1], **{"Changed in this document": "-"})]))
        with self.assertRaises(ValueError):
            tn_record(self.text.replace("<!-- tn-record:start -->", "", 1))
        with self.assertRaises(ValueError):
            summary_rows(self.text.replace("<!-- tool-summary:start -->", "", 1))
        # status rules of section 8 (SYS-49)
        done = done_proposals(self.text)
        listed = section8_requirements(self.text)
        broken = section8_requirements(self.text.replace("| E | implemented (PLAT-81) |", "| E | open |", 1))
        self.assertTrue(status_problems(broken, self.requirements, self.test_links, done))  # disagrees with sdoc
        reqs = [dict(r, status="open") if r["id"] == "SSR-084" else r for r in self.requirements]
        self.assertTrue(status_problems(listed, reqs, self.test_links, done))
        links = {rid: tests for rid, tests in self.test_links.items() if rid != "SSR-080"}
        self.assertTrue(status_problems(listed, self.requirements, links, done))    # implemented without a test
        self.assertTrue(status_problems(listed, self.requirements, self.test_links, done - {"E"}))  # E not done
        reqs = [dict(r, note="") if r["id"] == "SSR-082" else r for r in self.requirements]
        self.assertTrue(status_problems(listed, reqs, self.test_links, done))      # no pointer back
        broken = section8_requirements(self.text.replace("| C | implemented (PLAT-79) |", "| C | planned |", 1))
        self.assertTrue(status_problems(broken, self.requirements, self.test_links, done))
        self.assertNotIn("A", done_proposals(self.text.replace("— **done: PLAT-77**", "—", 1)))


if __name__ == "__main__":
    sys.exit(unittest.main())
