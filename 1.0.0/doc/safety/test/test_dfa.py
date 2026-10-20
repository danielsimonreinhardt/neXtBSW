"""SYS-70/SYS-95: consistency of the dependent failure analysis doc/safety/dfa.md (the script of its section 10.2).

Checks
- every mechanism SM-nn cited exists in the safety catalogue (doc/requirements/safety/mechanisms.sdoc, read as text:
  no StrictDoc needed), and every mechanism of fmea.md section 3 ("#### SM-nn") is assigned to a shared resource in
  section 4 of the analysis;
- every task ID cited (SYS-, DOC-, PLAT-, IO-, ARCH-, NVM-, BL-, COM-, TOOL-, RTE-) exists in doc/todo.yaml;
- every test ID cited in the status column of section 5 and in the evidence column of section 8.1 exists in
  tools/test/tests.yaml (a trailing `*` is a pattern);
- every row DF-nn of section 5 carries one status word of section 1.3, the DF numbers run without gap, and the counts
  of section 0 (marker dfa-counts) equal the counted rows - covered including covered (host), whose number is named too;
- every finding DFA-1 ... DFA-6 has its row in the findings table of section 8 and in the implementation status of
  section 8.1 (SYS-95), and the status rows name their evidence;
- every proposed assumption of use of section 9 (AoU-DFA-x) is a row of the safety manual with status `proposed`;
- percentages only as timing tolerances of a clock (no coverage figures), and the licence rule of the safety plan
  (no table or figure of the standard, no recommendation marks);
- every relative link resolves.
Counter-checks with deliberately broken copies show that each rule sees its fault.
In the exported tree (DOC-85) the export removes test entries that need unpublished files and the public todo.yaml
lacks the done tasks; a cited test or task that is absent is accepted only if the export lists it as left out on
purpose (doc/unpublished.yaml, tools/distcheck/unpublished.py). The development repository has no list.

Run: .venv/Scripts/python -m unittest doc/safety/test/test_dfa.py
"""

import copy
import fnmatch
import importlib.util
import pathlib
import re
import sys
import unittest

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_safety_plan import licence_problems  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[3]
SAFETY = REPO / "doc" / "safety"
DOC = SAFETY / "dfa.md"

STATUS_WORDS = ("covered (host)", "covered", "partial", "open", "AoU", "residual", "not plausible")
TASK_RE = re.compile(r"\b((?:SYS|DOC|PLAT|IO|ARCH|NVM|BL|COM|TOOL|RTE)-\d+)\b")
FINDINGS = [f"DFA-{n}" for n in range(1, 7)]
# Mechanisms that fmea.md gained after the baseline of this analysis (ae92e89, SYS-70) and that section 4 does not
# assign yet - the open point O6 of section 10.3, for the next issue of the analysis. The test fails when one of them
# is assigned (remove it here) or when fmea.md gains another one (assign it, or add it here and to O6).
LATER_THAN_BASELINE = {"SM-26", "SM-27", "SM-55", "SM-58", "SM-59", "SM-60"}


def section(text: str, start: str, end: str) -> str:
    part = text.split(start, 1)[1] if start in text else ""
    return part.split(end, 1)[0] if end in part else part


def cells(line: str) -> list:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def expand(text: str) -> set:
    """Mechanism IDs of a text: single 'SM-14' and ranges 'SM-10 … SM-13'."""
    ids = set()
    for a, b in re.findall(r"(SM-\d\d)(?:\s*…\s*(SM-\d\d))?", text):
        if b:
            ids.update(f"SM-{n:02d}" for n in range(int(a[3:]), int(b[3:]) + 1))
        else:
            ids.add(a)
    return ids


def df_rows(text: str) -> list:
    """(ID, status cell) of every row of section 5."""
    body = section(text, "## 5. Potential dependent failures", "\n## 6.")
    return [(c[0], c[-1]) for c in (cells(line) for line in body.splitlines() if re.match(r"^\| DF-\d\d \|", line))]


def status_of(cell: str):
    for word in STATUS_WORDS:
        if cell.startswith(f"**{word}**") or cell.startswith(f"**{word} "):
            return word
    return None


def counts(text: str) -> dict:
    result = {w: 0 for w in STATUS_WORDS}
    for _, cell in df_rows(text):
        word = status_of(cell)
        if word is not None:
            result[word] += 1
    return result


def stated_counts(text: str) -> dict:
    page = section(text, "## 0. Result in one page", "\n## 1.")
    m = re.search(r"<!-- dfa-counts -->\*\*(\d+) covered\*\* by an existing measure \((\d+) of them covered \(host\) "
                  r"only[^)]*\), \*\*(\d+) partial\*\*, \*\*(\d+) open\*\*, \*\*(\d+) AoU\*\*, \*\*(\d+) residual\*\*, "
                  r"\*\*(\d+) not plausible\*\*", page)
    if m is None:
        return {}
    keys = ("covered+host", "covered (host)", "partial", "open", "AoU", "residual", "not plausible")
    return dict(zip(keys, (int(g) for g in m.groups())))


def status_rows(text: str) -> dict:
    body = section(text, "<!-- dfa-status:start -->", "<!-- dfa-status:end -->")
    rows = {}
    for line in body.splitlines():
        c = cells(line)
        if line.startswith("| DFA-") and len(c) == 5:
            rows[c[0]] = c
    return rows


def cited_test_ids(text: str) -> set:
    ids = set()
    for _, cell in df_rows(text):
        for decision, token in re.findall(r"(decision\s+)?`([^`]+)`", cell):
            if not decision and re.fullmatch(r"[a-z][a-z0-9-]*-[a-z0-9*-]+", token):
                ids.add(token)
    for row in status_rows(text).values():
        ids.update(t for t in re.findall(r"`([^`]+)`", row[3]) if re.fullmatch(r"[a-z][a-z0-9*-]*", t))
    return ids


def percentage_problems(text: str) -> list:
    return [line.strip()[:80] for line in text.splitlines()
            if re.search(r"\d\s*%", line) and "clock" not in line.lower()]


def link_problems(text: str) -> list:
    problems = []
    for target in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", text):
        if "://" not in target and not (SAFETY / target).resolve().exists():
            problems.append(f"link target {target} does not exist")
    return problems


def load_unpublished():
    """DOC-85: the export's list of what it left out on purpose (empty in the development repository)."""
    path = REPO / "tools" / "distcheck" / "unpublished.py"
    spec = importlib.util.spec_from_file_location("distcheck_unpublished", path)
    module = sys.modules.setdefault("distcheck_unpublished", importlib.util.module_from_spec(spec))
    if not hasattr(module, "load"):
        spec.loader.exec_module(module)
    return module.load(REPO)


class Sources:
    def __init__(self):
        unpublished = load_unpublished()
        # DOC-85: test entries and tasks the export left out on purpose (empty in the development repository)
        self.unpublished_tests = set(unpublished.tests)
        self.unpublished_tasks = set(unpublished.tasks)
        self.mechanisms = set(re.findall(r"UID: (SM-\d\d)\b", (REPO / "doc" / "requirements" / "safety" /
                                                               "mechanisms.sdoc").read_text(encoding="utf-8")))
        self.fmea_sections = set(re.findall(r"^#### (SM-\d\d)\b", (SAFETY / "fmea.md").read_text(encoding="utf-8"),
                                            flags=re.MULTILINE))
        todo = yaml.safe_load((REPO / "doc" / "todo.yaml").read_text(encoding="utf-8"))
        # a task merged after the last export of the plan is named by the changelog (coordinator's merge)
        self.tasks = {t["id"] for t in todo["tasks"]} | set(TASK_RE.findall(
            (REPO / "CHANGELOG.md").read_text(encoding="utf-8"))) | self.unpublished_tasks
        tests = yaml.safe_load((REPO / "tools" / "test" / "tests.yaml").read_text(encoding="utf-8"))
        self.tests = {entry["name"] for entry in tests["tests"]}
        self.manual = (SAFETY / "safety-manual.md").read_text(encoding="utf-8")


class Analysis:
    def __init__(self, text: str, src: Sources):
        self.text = text
        self.src = src

    def mechanism_problems(self) -> list:
        cited = set(re.findall(r"\bSM-\d\d\b", self.text))
        problems = [f"{m} cited but not in the catalogue" for m in sorted(cited - self.src.mechanisms)]
        assigned = expand(section(self.text, "## 4. Shared resources", "\n## 5."))
        unassigned = self.src.fmea_sections - assigned
        problems += [f"{m} of fmea.md section 3 assigned to no resource in section 4 (assign it, or name it in O6)"
                     for m in sorted(unassigned - LATER_THAN_BASELINE)]
        problems += [f"{m} is assigned now: remove it from LATER_THAN_BASELINE and O6"
                     for m in sorted(LATER_THAN_BASELINE & assigned)]
        open_points = section(self.text, "### 10.3", "\n---")
        problems += [f"{m} not named in open point O6" for m in sorted(LATER_THAN_BASELINE) if m not in open_points]
        return problems

    def task_problems(self) -> list:
        cited = set(TASK_RE.findall(self.text))
        return [f"{t} cited but not in doc/todo.yaml" for t in sorted(cited - self.src.tasks)]

    def test_problems(self) -> list:
        problems = []
        known = self.src.tests | self.src.unpublished_tests
        for token in sorted(cited_test_ids(self.text)):
            if not any(fnmatch.fnmatchcase(name, token) for name in known):
                problems.append(f"test {token} cited but not in tools/test/tests.yaml")
        return problems

    def status_problems(self) -> list:
        rows = df_rows(self.text)
        problems = [f"{i}: no status word of section 1.3" for i, cell in rows if status_of(cell) is None]
        numbers = sorted(int(i[3:]) for i, _ in rows)
        if numbers != list(range(1, len(numbers) + 1)):
            problems.append(f"DF numbers not unique or with a gap: {numbers}")
        found = counts(self.text)
        stated = stated_counts(self.text)
        if not stated:
            return problems + ["section 0 states no counts (marker dfa-counts)"]
        expected = {"covered+host": found["covered"] + found["covered (host)"],
                    **{k: found[k] for k in ("covered (host)", "partial", "open", "AoU", "residual", "not plausible")}}
        problems += [f"section 0 says {stated[k]} {k}, section 5 has {expected[k]}"
                     for k in expected if stated.get(k) != expected[k]]
        if f"**{len(rows)}** (section 5)" not in section(self.text, "## 0. Result in one page", "\n## 1."):
            problems.append(f"section 0 does not say **{len(rows)}** potential dependent failures")
        return problems

    def finding_problems(self) -> list:
        problems = []
        table = section(self.text, "## 8. New findings", "\n### 8.1")
        rows = status_rows(self.text)
        for finding in FINDINGS:
            if f"| **{finding}** |" not in table:
                problems.append(f"{finding} missing in the findings table of section 8")
            row = rows.get(finding)
            if row is None:
                problems.append(f"{finding} missing in the implementation status of section 8.1")
            elif not re.search(r"`[a-z][a-z0-9-]*`", row[3]):
                problems.append(f"{finding}: no evidence named in section 8.1")
        return problems

    def aou_problems(self) -> list:
        proposed = re.findall(r"^\| (AoU-DFA-[a-z]) \|", section(self.text, "## 9. Proposed assumptions", "\n## 10."),
                              flags=re.MULTILINE)
        problems = [] if proposed else ["section 9 proposes no AoU-DFA"]
        for aou in proposed:
            row = next((line for line in self.src.manual.splitlines() if line.startswith(f"| {aou} |")), None)
            if row is None:
                problems.append(f"{aou} has no row in the safety manual")
            elif cells(row)[-1] != "proposed":
                problems.append(f"{aou}: status in the safety manual is not 'proposed'")
        return problems

    def all_problems(self) -> list:
        return (self.mechanism_problems() + self.task_problems() + self.test_problems() + self.status_problems()
                + self.finding_problems() + self.aou_problems() + percentage_problems(self.text)
                + licence_problems(self.text) + link_problems(self.text))


class DfaConsistency(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DOC.read_text(encoding="utf-8")
        cls.src = Sources()
        cls.analysis = Analysis(cls.text, cls.src)

    def of(self, text):
        return Analysis(text, self.src)

    def sub(self, old, new):
        self.assertIn(old, self.text, f"counter-check edit does not hit: {old[:80]}")
        return self.text.replace(old, new, 1)

    def test_sources_found(self):
        self.assertGreaterEqual(len(self.src.mechanisms), 40)
        self.assertGreaterEqual(len(self.src.fmea_sections), 20)
        self.assertIn("SYS-95", self.src.tasks)
        self.assertEqual(len(df_rows(self.text)), 47)
        self.assertEqual(len(status_rows(self.text)), 6)
        self.assertGreater(len(cited_test_ids(self.text)), 10)

    def test_mechanisms(self):
        self.assertEqual(self.analysis.mechanism_problems(), [])

    def test_tasks(self):
        self.assertEqual(self.analysis.task_problems(), [])

    def test_tests(self):
        self.assertEqual(self.analysis.test_problems(), [])

    def test_status_and_counts(self):
        self.assertEqual(self.analysis.status_problems(), [])

    def test_findings(self):
        self.assertEqual(self.analysis.finding_problems(), [])

    def test_assumptions_in_the_safety_manual(self):
        self.assertEqual(self.analysis.aou_problems(), [])

    def test_no_coverage_percentages_and_licence(self):
        self.assertEqual(percentage_problems(self.text), [])
        self.assertEqual(licence_problems(self.text), [])

    def test_links(self):
        self.assertEqual(link_problems(self.text), [])

    def test_counter_checks(self):
        # an unknown mechanism, task and test
        self.assertTrue(self.of(self.text + "\nSM-99\n").mechanism_problems())
        self.assertTrue(self.of(self.sub("## 4. Shared resources", "## 4. Resources")).mechanism_problems())
        self.assertTrue(self.of(self.text + "\nSYS-9999\n").task_problems())
        missing = self.sub("(`safety-progress`, `progress-checks`;", "(`safety-progress-missing`, `progress-checks`;")
        self.assertTrue(self.of(missing).test_problems())
        # DOC-85: a test or task the export lists as left out is accepted, an unlisted absent one is not
        listed = copy.copy(self.src)
        listed.unpublished_tests = self.src.unpublished_tests | {"safety-progress-missing"}
        listed.tasks = self.src.tasks | {"SYS-9999"}
        self.assertEqual(Analysis(missing, listed).test_problems(), self.analysis.test_problems())
        self.assertEqual(Analysis(self.text + "\nSYS-9999\n", listed).task_problems(), self.analysis.task_problems())
        self.assertTrue(Analysis(self.text + "\nSYS-9998\n", listed).task_problems())
        # a row without a status word, a changed status that the counts of section 0 do not follow
        self.assertTrue(self.of(self.sub("**partial** since SYS-95 (DFA-2)", "since SYS-95 (DFA-2)"))
                        .status_problems())
        self.assertTrue(self.of(self.sub("**partial** since SYS-95 (DFA-2)", "**open** since SYS-95 (DFA-2)"))
                        .status_problems())
        self.assertTrue(self.of(self.sub("**14 partial**", "**13 partial**")).status_problems())
        # a finding without its status row, a status row without evidence
        self.assertTrue(self.of(self.sub("| DFA-6 | implemented |", "| DFA-7 | implemented |")).finding_problems())
        self.assertTrue(self.of(self.sub("| `lpit-deadman-progress`, `safety-progress` |", "| host |"))
                        .finding_problems())
        # an AoU of section 9 that the manual does not carry
        self.assertTrue(self.of(self.sub("| AoU-DFA-c |", "| AoU-DFA-x |")).aou_problems())
        # a coverage percentage, a table of the standard, a broken link
        self.assertTrue(percentage_problems(self.text + "\n| SM-14 | coverage 99 % |\n"))
        self.assertTrue(licence_problems(self.text + "\nsee Table 12\n"))
        self.assertTrue(link_problems(self.text + "\n[x](missing-file.md)\n"))


if __name__ == "__main__":
    sys.exit(unittest.main())
