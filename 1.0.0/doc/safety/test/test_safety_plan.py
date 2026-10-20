"""SYS-60: consistency of the safety plan doc/safety/safety-plan.md with the gap analysis, the task list and the tree.

Checks
- the claim decided by the user (iso-q2) stands between the markers claim:start/end, and the limitations it implies
  (no quality management system, no evidence of competence) are named;
- the role table names a project manager and a safety manager (2-6.4.2), and every "Responsible" of the activity
  table and every "Reviewer" of the confirmation table is a role of that table;
- the activity table covers parts 2, 6, 8 and 9; row IDs are unique and their part matches the clause;
- every gap row 2-nn, 6-nn, 8-nn, 9-nn of doc/safety/iso26262-gap.md sections 2.1-2.4 is served by at least one
  activity, and no activity cites a gap row that does not exist;
- work products: every path named in the activity, confirmation and release tables exists or its row has an open
  task (status other than done in doc/todo.yaml) or a proposal of section 8; a task that is done while its work
  product is still missing is an error; a row with status "done" names only existing paths; a row that is not done
  or tailored has at least one open task or proposal, so somebody does it;
- every task ID named exists in doc/todo.yaml, every proposal and every tailoring ID used is defined, every defined
  one is used, proposals have a non-trivial "done when" and tailorings a rationale;
- a tailored row names a tailoring ID; the confirmation measures that need I1 at ASIL B cover safety plan, safety
  analyses, DFA, verification plan and safety case; the release preconditions R1-R6 each name a task;
- the numbers in the "Result in one page" match the tables;
- licence rule for ISO 26262 (user 2026-09-22): cited by part and clause only, no table or figure, no recommendation
  marks;
- SYS-182 (confirmation review CR-01-1, finding F-08 / K01-08): the plan does not lag behind -
  a proposal of section 8 that a task carries (column "Task") is no longer named in sections 4 to 6, the row names the
  task; a row is not `planned` when one of its tasks has started (in_progress, review, done) and its work products
  exist; a confirmation measure is `prepared` only while no record under doc/safety/reviews/records carries a verdict,
  `performed`/`closed` only with such a record, `closed` only with the verdict `accepted`; the mapping of section 6
  names every precondition P1 ... of cm-plan.md section 5.1 exactly once and every R1 ... R6.
Counter-checks with deliberately broken copies of the document show that each rule sees its fault.

Run: .venv/Scripts/python -m unittest doc/safety/test/test_safety_plan.py
"""

import importlib.util
import pathlib
import re
import sys
import unittest

import yaml

REPO = pathlib.Path(__file__).resolve().parents[3]
SAFETY = REPO / "doc" / "safety"
DOC = SAFETY / "safety-plan.md"


def load_unpublished():
    """DOC-85: the export's list of what it left out on purpose (doc/unpublished.yaml, tools/distcheck/unpublished.py);
    empty in the development repository. The safety tests that import from here accept an absent source of the
    exported tree only if it is listed."""
    path = REPO / "tools" / "distcheck" / "unpublished.py"
    spec = importlib.util.spec_from_file_location("distcheck_unpublished", path)
    module = sys.modules.setdefault("distcheck_unpublished", importlib.util.module_from_spec(spec))
    if not hasattr(module, "load"):
        spec.loader.exec_module(module)
    return module.load(REPO)
GAP = SAFETY / "iso26262-gap.md"
CM_PLAN = SAFETY / "cm-plan.md"
RECORDS = SAFETY / "reviews" / "records"

CLAIM = "neXtBSW is developed following the methods of ISO 26262-6 at ASIL B, without the management part."
PARTS = ("2", "6", "8", "9")
STATUS = ("done", "partial", "in progress", "planned", "tailored")
CM_STATUS = ("prepared", "performed", "closed")
INDEPENDENCE = ("I1 required at B", "I1 by project choice", "I1 by project choice (recommended at B)")
# ISO 26262-2, 6-4.9/6.4.10: at ASIL B these confirmation reviews need a person other than the author
I1_REQUIRED = ("doc/safety/safety-plan.md", "doc/safety/fmea.md", "doc/safety/dfa.md",
               "doc/safety/verification-plan.md", "doc/safety/safety-case.md")

ROLE_COLUMNS = ("Role", "Held by", "Responsibility and authority")
ACTIVITY_COLUMNS = ("ID", "Clause", "Activity", "Objective", "Gap rows", "Responsible", "Work product", "Task",
                    "Status")
TAILORING_COLUMNS = ("ID", "Clause(s)", "Tailoring", "Rationale", "Consequence")
CM_COLUMNS = ("ID", "Measure", "Work product", "Independence", "Reviewer", "When", "Task", "Status")
RELEASE_COLUMNS = ("ID", "Precondition before tagging `vX.Y.Z` on `master`", "Evidence", "Task")
PROPOSAL_COLUMNS = ("ID", "Title", "Done when", "Prio", "Task")
RELEASE_MAP_COLUMNS = ("cm-plan", "Safety plan", "Note")
# task states of doc/todo.yaml in which work on the task has started (SYS-182: a row with such a task is not 'planned')
STARTED = ("in_progress", "review", "done")


def _cells(line: str) -> list:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def block(text: str, name: str) -> str:
    try:
        return text.split(f"<!-- {name}:start -->", 1)[1].split(f"<!-- {name}:end -->", 1)[0]
    except IndexError:
        raise ValueError(f"markers {name}:start/end missing") from None


def table(text: str, name: str, columns: tuple) -> list:
    """Rows of the marked table as dicts; raises ValueError on a malformed table."""
    lines = [line for line in block(text, name).splitlines() if line.startswith("|")]
    if len(lines) < 3 or tuple(_cells(lines[0])) != columns:
        raise ValueError(f"{name}: header is not {columns}")
    rows = []
    for line in lines[2:]:
        cells = _cells(line)
        if len(cells) != len(columns):
            raise ValueError(f"{name}: row with {len(cells)} cells: {line[:60]}")
        rows.append(dict(zip(columns, cells)))
    return rows


def paths(cell: str) -> list:
    """Repository paths of a cell (backticked tokens that contain a slash or a dot)."""
    return [p.rstrip("/") for p in re.findall(r"`([^`]+)`", cell) if "/" in p or "." in p]


def refs(cell: str) -> list:
    return [r.strip() for r in cell.split(",") if r.strip() and r.strip() != "—"]


def cm_plan_preconditions(cm_text: str) -> list:
    """IDs P1 ... of cm-plan.md section 5.1."""
    section = cm_text.split("### 5.1 Preconditions", 1)[1].split("### 5.2", 1)[0]
    return re.findall(r"^\| (P\d+) \|", section, flags=re.MULTILINE)


def load_records() -> dict:
    """Verdicts of the confirmation review records: measure CM-nn -> [verdict of run 1, run 2, ...]."""
    result = {}
    files = sorted(RECORDS.glob("CR-*.md"), key=lambda p: int(p.stem.rsplit("-", 1)[1])) if RECORDS.is_dir() else []
    for path in files:
        m = re.search(r"^\| Verdict \| ([^|]+) \|", path.read_text(encoding="utf-8"), flags=re.MULTILINE)
        result.setdefault("CM-" + path.stem.split("-")[1], []).append(m.group(1).strip() if m else "pending")
    return result


def gap_rows(gap_text: str) -> set:
    """Row IDs of iso26262-gap.md sections 2.1-2.4 (parts 2, 6, 8, 9)."""
    section = gap_text.split("### 2.1 Part 2", 1)[1].split("### 2.5 Part 10", 1)[0]
    return set(re.findall(r"^\|\s*([2689]-\d\d)\s*\|", section, flags=re.MULTILINE))


class Plan:
    """The parsed plan with the repository facts it is checked against."""

    def __init__(self, text: str, tasks: dict, gap: set, exists=None, records=None, preconditions=None):
        self.text = text
        self.tasks = tasks                                    # task ID -> status
        self.gap = gap
        self.exists = exists or (lambda p: (REPO / p).exists())
        self.records = load_records() if records is None else records          # CM-nn -> [verdict per run]
        self.preconditions = (cm_plan_preconditions(CM_PLAN.read_text(encoding="utf-8"))
                              if preconditions is None else preconditions)    # P1 ... of cm-plan.md 5.1
        self.roles = table(text, "plan-roles", ROLE_COLUMNS)
        self.activities = table(text, "plan-activities", ACTIVITY_COLUMNS)
        self.tailoring = table(text, "plan-tailoring", TAILORING_COLUMNS)
        self.confirmation = table(text, "plan-confirmation", CM_COLUMNS)
        self.release = table(text, "plan-release", RELEASE_COLUMNS)
        self.release_map = table(text, "plan-release-map", RELEASE_MAP_COLUMNS)
        self.proposals = table(text, "plan-proposals", PROPOSAL_COLUMNS)

    # ---- helpers
    def carried_by(self, proposal: str) -> list:
        """Tasks that carry a proposal of section 8 (column "Task"); empty while no task carries it."""
        row = next((p for p in self.proposals if p["ID"] == proposal), None)
        return refs(row["Task"]) if row else []

    def open_task(self, ref: str) -> bool:
        if re.fullmatch(r"N\d+", ref):
            # a proposal counts as open work only while no task carries it (SYS-182)
            return ref in {p["ID"] for p in self.proposals} and not self.carried_by(ref)
        return ref in self.tasks and self.tasks[ref] != "done"

    def ref_problems(self, where: str, row_refs: list) -> list:
        problems = []
        tailoring = {t["ID"] for t in self.tailoring}
        proposals = {p["ID"] for p in self.proposals}
        for ref in row_refs:
            if re.fullmatch(r"T-\d\d", ref):
                if ref not in tailoring:
                    problems.append(f"{where}: tailoring {ref} not defined in section 3")
            elif re.fullmatch(r"N\d+", ref):
                if ref not in proposals:
                    problems.append(f"{where}: proposal {ref} not defined in section 8")
                elif self.carried_by(ref):
                    carriers = ", ".join(f"{t} ({self.tasks.get(t, '?')})" for t in self.carried_by(ref))
                    problems.append(f"{where}: still names proposal {ref}, which {carriers} carries - name the task "
                                    "(plan lags behind, section 7)")
            elif ref not in self.tasks:
                problems.append(f"{where}: task {ref} not in doc/todo.yaml")
        return problems

    def work_product_problems(self, where: str, cell: str, row_refs: list, status: str) -> list:
        problems = []
        missing = [p for p in paths(cell) if not self.exists(p)]
        has_open = any(self.open_task(r) for r in row_refs)
        for path in missing:
            if status == "done":
                problems.append(f"{where}: status done, but {path} does not exist")
            elif not has_open:
                problems.append(f"{where}: {path} does not exist and no open task or proposal produces it")
        return problems

    # ---- rules
    def claim_problems(self) -> list:
        problems = []
        claim = block(self.text, "claim")
        if CLAIM not in claim.replace("**", ""):
            problems.append("claim (iso-q2) missing or changed")
        for limitation in ("no quality management system", "no evidence of competence"):
            if limitation not in self.text:
                problems.append(f"limitation not stated: {limitation}")
        return problems

    def role_problems(self) -> list:
        problems = []
        names = {r["Role"]: r["Held by"] for r in self.roles}
        for role in ("project manager", "safety manager"):
            held = names.get(role, "")
            if not held or "not yet named" in held or held in ("-", "—", "open"):
                problems.append(f"role {role} not appointed")
        for row in self.activities:
            if row["Responsible"] not in names:
                problems.append(f"{row['ID']}: responsible '{row['Responsible']}' is not a role of section 2")
        for row in self.confirmation:
            if row["Reviewer"] not in names:
                problems.append(f"{row['ID']}: reviewer '{row['Reviewer']}' is not a role of section 2")
            if row["Reviewer"] in ("author", "coordinator"):
                problems.append(f"{row['ID']}: the author side cannot perform a confirmation review")
        return problems

    def activity_problems(self) -> list:
        problems = []
        ids = [row["ID"] for row in self.activities]
        if len(ids) != len(set(ids)):
            problems.append("duplicate activity IDs")
        for part in PARTS:
            if not any(i.startswith(f"P{part}-") for i in ids):
                problems.append(f"no activity for part {part}")
        cited = set()
        for row in self.activities:
            where = row["ID"]
            match = re.fullmatch(r"P([2689])-\d\d", where)
            if not match:
                problems.append(f"{where}: ID not of the form P<part>-nn")
                continue
            if not row["Clause"].startswith(f"{match.group(1)}-"):
                problems.append(f"{where}: clause {row['Clause']} is not in part {match.group(1)}")
            rows = refs(row["Gap rows"])
            for gap_id in rows:
                if gap_id not in self.gap:
                    problems.append(f"{where}: gap row {gap_id} does not exist in iso26262-gap.md")
                elif not gap_id.startswith(f"{match.group(1)}-"):
                    problems.append(f"{where}: gap row {gap_id} belongs to another part")
            cited.update(rows)
            status = row["Status"]
            if status not in STATUS:
                problems.append(f"{where}: status '{status}' not in {STATUS}")
            row_refs = refs(row["Task"])
            problems += self.ref_problems(where, row_refs)
            if status == "tailored":
                if not any(re.fullmatch(r"T-\d\d", r) for r in row_refs):
                    problems.append(f"{where}: tailored without a tailoring ID")
            elif row["Work product"] in ("—", "-", ""):
                problems.append(f"{where}: no work product")
            if status in ("partial", "in progress", "planned") and not any(self.open_task(r) for r in row_refs):
                problems.append(f"{where}: status {status}, but no open task or proposal carries it")
            problems += self.work_product_problems(where, row["Work product"], row_refs, status)
        problems += [f"gap row {g} is served by no activity" for g in sorted(self.gap - cited)]
        return problems

    def tailoring_problems(self) -> list:
        problems = []
        used = set()
        for row in self.activities:
            used.update(r for r in refs(row["Task"]) if re.fullmatch(r"T-\d\d", r))
        for row in self.tailoring:
            if len(row["Rationale"]) < 60:
                problems.append(f"{row['ID']}: rationale missing or too short")
            if not re.search(r"\b[2-9]-\d+(\.\d+)*", row["Clause(s)"]) and "part" not in row["Clause(s)"]:
                problems.append(f"{row['ID']}: no clause")
            if row["ID"] not in used:
                problems.append(f"{row['ID']}: defined but used by no activity")
        return problems

    def confirmation_problems(self) -> list:
        problems = []
        required = set()
        for row in self.confirmation:
            where = row["ID"]
            if row["Independence"] not in INDEPENDENCE:
                problems.append(f"{where}: independence '{row['Independence']}' not in {INDEPENDENCE}")
            if row["Status"] not in CM_STATUS:
                problems.append(f"{where}: status '{row['Status']}' not in {CM_STATUS}")
            row_refs = refs(row["Task"])
            problems += self.ref_problems(where, row_refs)
            problems += self.work_product_problems(where, row["Work product"], row_refs, row["Status"])
            if row["Independence"] == "I1 required at B":
                required.update(paths(row["Work product"]))
        problems += [f"no I1 confirmation review of {p}" for p in I1_REQUIRED if p not in required]
        return problems

    def release_problems(self) -> list:
        problems = []
        if [r["ID"] for r in self.release] != [f"R{n}" for n in range(1, 7)]:
            problems.append("release preconditions are not R1..R6")
        for row in self.release:
            row_refs = refs(row["Task"])
            if not row_refs:
                problems.append(f"{row['ID']}: no task")
            problems += self.ref_problems(row["ID"], row_refs)
        return problems

    def proposal_problems(self) -> list:
        problems = []
        used = set()
        for row in self.activities:
            used.update(r for r in refs(row["Task"]) if re.fullmatch(r"N\d+", r))
        for row in self.proposals:
            if len(row["Done when"]) < 60:
                problems.append(f"{row['ID']}: 'done when' missing or too short")
            carriers = refs(row["Task"])
            for task in carriers:
                if task not in self.tasks:
                    problems.append(f"{row['ID']}: carried by task {task}, which is not in doc/todo.yaml")
            if not carriers and row["ID"] not in used:
                problems.append(f"{row['ID']}: proposed but used by no activity")
        return problems

    def lag_problems(self) -> list:
        """SYS-182 (CR-01-1 F-08): statuses that are lower than the task list and the records say."""
        problems = []
        for row in self.activities:
            if row["Status"] != "planned":
                continue
            started = [r for r in refs(row["Task"]) if self.tasks.get(r) in STARTED]
            products = paths(row["Work product"])
            if started and products and all(self.exists(p) for p in products):
                problems.append(f"{row['ID']}: status planned, but {', '.join(started)} started and the work product "
                                "exists - at least partial (plan lags behind, section 7)")
        for row in self.confirmation:
            verdicts = [v for v in self.records.get(row["ID"], []) if v != "pending"]
            status = row["Status"]
            if status == "prepared" and verdicts:
                problems.append(f"{row['ID']}: status prepared, but a record carries the verdict '{verdicts[-1]}' - "
                                "performed (plan lags behind, section 7)")
            if status in ("performed", "closed") and not verdicts:
                problems.append(f"{row['ID']}: status {status} without a record that carries a verdict")
            if status == "closed" and verdicts and verdicts[-1] != "accepted":
                problems.append(f"{row['ID']}: status closed, but the last verdict is '{verdicts[-1]}'")
        mapped = [r["cm-plan"] for r in self.release_map]
        if sorted(mapped) != sorted(self.preconditions) or len(set(mapped)) != len(mapped):
            problems.append(f"release map: cm-plan preconditions {mapped} differ from cm-plan.md 5.1 "
                            f"{self.preconditions}")
        release = {r["ID"] for r in self.release}
        named = set()
        for r in self.release_map:
            for ref in refs(r["Safety plan"]):
                if ref not in release:
                    problems.append(f"release map: {r['cm-plan']} names {ref}, which is not a precondition of section 6")
                named.add(ref)
        problems += [f"release map: {r} maps to no precondition of cm-plan.md" for r in sorted(release - named)]
        return problems

    def summary_problems(self) -> list:
        """The counts of section 0 must match the tables."""
        problems = []
        result = self.text.split("## 0. Result in one page", 1)[1].split("\n## 1.", 1)[0]
        counts = {s: sum(1 for r in self.activities if r["Status"] == s) for s in STATUS}
        per_part = {p: sum(1 for r in self.activities if r["ID"].startswith(f"P{p}-")) for p in PARTS}
        expected = [
            f"**{len(self.activities)}** activities of parts 2 ({per_part['2']}), 6 ({per_part['6']}), "
            f"8 ({per_part['8']}) and 9 ({per_part['9']})",
            f"all **{len(self.gap)}** gap rows",
            "Status: " + ", ".join(f"{counts[s]} {s}" for s in STATUS),
            f"{len(self.tailoring)} tailoring decisions",
            f"{len(self.confirmation)} measures; "
            f"{sum(1 for r in self.confirmation if r['Independence'] == 'I1 required at B')} of them need independence I1",
            f"| New tasks proposed (section 8) | {len(self.proposals)}:",
        ]
        problems += [f"section 0 does not say: {e}" for e in expected if e not in result]
        return problems

    def all_problems(self) -> list:
        return (self.claim_problems() + self.role_problems() + self.activity_problems() + self.tailoring_problems()
                + self.confirmation_problems() + self.release_problems() + self.proposal_problems()
                + self.summary_problems() + self.lag_problems() + licence_problems(self.text))


def licence_problems(text: str) -> list:
    """References that go beyond part and clause: tables or figures, and the recommendation marks of its tables."""
    problems = [f"reference to a table or figure: '{m}'" for m in re.findall(r"\b(?:Table|Figure)\s+[A-Z]?\d+", text)]
    problems += [f"recommendation mark copied: '{line.strip()[:60]}'" for line in text.splitlines()
                 if re.search(r"(^|[\s|])(\+\+|\+|o)\s*\|\s*(\+\+|\+|o)(\s|\|)", line)]
    return problems


def load_tasks(unpublished=None) -> dict:
    """Task ID -> status of doc/todo.yaml; DOC-85: in the export also the tasks the public todo.yaml leaves out
    (listed by the export with their status), todo.yaml wins."""
    data = yaml.safe_load((REPO / "doc" / "todo.yaml").read_text(encoding="utf-8"))
    unpublished = load_unpublished() if unpublished is None else unpublished
    return dict(unpublished.tasks) | {t["id"]: t.get("status", "open") for t in data["tasks"]}


class SafetyPlanConsistency(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DOC.read_text(encoding="utf-8")
        cls.tasks = load_tasks()
        cls.gap = gap_rows(GAP.read_text(encoding="utf-8"))
        cls.plan = Plan(cls.text, cls.tasks, cls.gap)

    def plan_of(self, text=None, tasks=None, exists=None, records=None):
        return Plan(self.text if text is None else text, self.tasks if tasks is None else tasks, self.gap, exists,
                    records=records)

    def test_gap_rows_found(self):
        # 17 + 40 + 29 + 11 rows in sections 2.1-2.4 of iso26262-gap.md (SYS-58)
        self.assertEqual(len(self.gap), 97)

    def test_claim(self):
        self.assertEqual(self.plan.claim_problems(), [])

    def test_roles(self):
        self.assertEqual(self.plan.role_problems(), [])

    def test_activities(self):
        self.assertEqual(self.plan.activity_problems(), [])

    def test_tailoring(self):
        self.assertEqual(self.plan.tailoring_problems(), [])

    def test_confirmation_measures(self):
        self.assertEqual(self.plan.confirmation_problems(), [])

    def test_release(self):
        self.assertEqual(self.plan.release_problems(), [])

    def test_proposals(self):
        self.assertEqual(self.plan.proposal_problems(), [])

    def test_summary(self):
        self.assertEqual(self.plan.summary_problems(), [])

    def test_standard_cited_by_part_and_clause_only(self):
        self.assertEqual(licence_problems(self.text), [])

    def test_plan_does_not_lag_behind(self):
        # SYS-182 (CR-01-1 F-08, K01-08)
        self.assertEqual(self.plan.lag_problems(), [])

    def test_counter_checks_lagging_plan(self):
        """SYS-182: each lag rule sees a stale row, a stale measure status and an incomplete R/P mapping."""
        def sub(source, old, new):
            self.assertIn(old, source)
            return source.replace(old, new, 1)

        text = self.text
        # a carried proposal still named by a row (the state of issue 1: P2-02 named N1 after SYS-92 was done)
        broken = sub(text, "| `doc/safety/security-interplay.md` | SYS-92 | done |",
                     "| `doc/safety/security-interplay.md` | N1 | planned |")
        self.assertTrue(any("still names proposal N1" in p for p in self.plan_of(broken).activity_problems()))
        # the same proposal not yet carried: then naming it is right
        n1 = next(line for line in text.splitlines() if line.startswith("| N1 |"))
        uncarried = sub(broken, n1, n1.rsplit("| SYS-92 |", 1)[0] + "| — |")
        self.assertFalse(any("N1" in p for p in self.plan_of(uncarried).activity_problems()))
        # a carried proposal whose task is unknown
        self.assertTrue(self.plan_of(sub(text, n1, n1.rsplit("| SYS-92 |", 1)[0] + "| SYS-9999 |"))
                        .proposal_problems())
        # status too low: planned although a task has started and the work product exists (P6-06 of issue 1)
        broken = sub(text, "| SYS-65, SYS-61, SYS-21 | partial |", "| SYS-65, SYS-61, SYS-21 | planned |")
        self.assertTrue(any("P6-06" in p for p in self.plan_of(broken).lag_problems()))
        # ... but planned is right while every task is still open
        tasks = dict(self.tasks, **{"SYS-65": "open", "SYS-61": "open"})
        self.assertFalse(any("P6-06" in p for p in self.plan_of(broken, tasks=tasks).lag_problems()))
        # ... and while the work product does not exist yet
        exists = lambda p: p != "doc/safety/coexistence.md" and (REPO / p).exists()   # noqa: E731
        self.assertFalse(any("P6-06" in p for p in self.plan_of(broken, exists=exists).lag_problems()))
        # measure status against the records
        cm01 = next(line for line in text.splitlines() if line.startswith("| CM-01 |"))
        status = re.search(r"\| (prepared|performed|closed) \|$", cm01).group(1)    # the state of the live plan
        prepared = sub(text, cm01, cm01.rsplit("| " + status + " |", 1)[0] + "| prepared |")
        self.assertTrue(any("CM-01" in p for p in self.plan_of(prepared).lag_problems()))
        self.assertFalse(any("CM-01" in p for p in self.plan_of(prepared, records={}).lag_problems()))
        self.assertTrue(any("CM-01" in p for p in self.plan_of(records={}).lag_problems()))
        self.assertTrue(any("CM-01" in p for p in self.plan_of(records={"CM-01": ["pending"]}).lag_problems()))
        closed = sub(text, cm01, cm01.rsplit("| " + status + " |", 1)[0] + "| closed |")
        self.assertTrue(any("CM-01" in p for p in self.plan_of(
            closed, records={"CM-01": ["accepted with conditions"]}).lag_problems()))
        self.assertFalse(any("CM-01" in p for p in self.plan_of(
            closed, records={"CM-01": ["accepted with conditions", "accepted"]}).lag_problems()))
        # release map: a cm-plan precondition missing, a precondition of section 6 mapped nowhere, unknown R
        p8 = next(line for line in block(text, "plan-release-map").splitlines() if line.startswith("| P8 |"))
        self.assertTrue(self.plan_of(sub(text, p8 + "\n", "")).lag_problems())
        p9 = next(line for line in block(text, "plan-release-map").splitlines() if line.startswith("| P9 |"))
        self.assertTrue(any("R6" in p for p in self.plan_of(sub(text, p9, p9.replace("| R6 |", "| — |")))
                            .lag_problems()))
        self.assertTrue(self.plan_of(sub(text, p9, p9.replace("| R6 |", "| R7 |"))).lag_problems())
        with self.assertRaises(ValueError):
            self.plan_of(sub(text, "<!-- plan-release-map:start -->", ""))

    # Counter-checks: every rule must see a broken document or a changed repository
    def test_counter_checks(self):
        def sub(source, old, new, count=-1):
            """str.replace that fails when the edit does not hit, so no counter-check passes vacuously."""
            self.assertIn(old, source)
            return source.replace(old, new, count)

        text = self.text
        # claim and roles
        self.assertTrue(self.plan_of(sub(text, "without the management part.", "including part 2.", 1))
                        .claim_problems())
        self.assertTrue(self.plan_of(sub(text, "no evidence of competence", "competence")).claim_problems())
        broken = sub(text, "| safety manager | the repository owner", "| safety manager | **not yet named**", 1)
        self.assertTrue(self.plan_of(broken).role_problems())
        broken = sub(text, "| Rules and processes for functional safety | written rules that name the lifecycle, "
                              "the roles and the work products, and that are followed in every task | 2-01 | "
                              "safety manager |",
                              "| Rules and processes for functional safety | written rules that name the lifecycle, "
                              "the roles and the work products, and that are followed in every task | 2-01 | "
                              "somebody |", 1)
        self.assertTrue(self.plan_of(broken).role_problems())
        broken = sub(text, "| I1 required at B | confirmation reviewer |", "| I1 required at B | author |", 1)
        self.assertTrue(self.plan_of(broken).role_problems())
        # activities: gap coverage, unknown gap row, wrong part, status word
        self.assertTrue(any("2-02 is served by no activity" in p
                            for p in self.plan_of(sub(text, "| 2-02 | safety manager |", "| 2-01 | safety manager |",
                                                               1)).activity_problems()))
        self.assertTrue(self.plan_of(sub(text, "| 2-02 | safety manager |", "| 2-02, 2-99 | safety manager |", 1))
                        .activity_problems())
        self.assertTrue(self.plan_of(sub(text, "| 2-02 | safety manager |", "| 2-02, 6-01 | safety manager |", 1))
                        .activity_problems())
        self.assertTrue(self.plan_of(sub(text, "| SYS-66, SYS-93 | in progress |", "| SYS-66, SYS-93 | ongoing |", 1))
                        .activity_problems())
        # work products: missing file without open task; task done but file missing; done row with missing file
        tasks = dict(self.tasks, **{"SYS-70": "done", "SYS-95": "done", "SYS-195": "done"})
        exists = lambda p: p != "doc/safety/dfa.md" and (REPO / p).exists()   # noqa: E731
        problems = self.plan_of(tasks=tasks, exists=exists).activity_problems()
        self.assertTrue(any("dfa.md does not exist" in p for p in problems), problems)
        broken = sub(text, "| `doc/safety/hsi-s32k1xx.md` | SYS-66, SYS-93 | in progress |",
                              "| `doc/safety/hsi-s32k1xx.md` | SYS-66, SYS-93 | done |", 1)
        exists = lambda p: p != "doc/safety/hsi-s32k1xx.md" and (REPO / p).exists()   # noqa: E731
        self.assertTrue(self.plan_of(broken, exists=exists).activity_problems())
        broken = sub(text, "| `doc/safety/security-interplay.md` | SYS-92 | done |",
                              "| `doc/safety/security-interplay.md` | — | planned |", 1)
        self.assertTrue(self.plan_of(broken).activity_problems())
        # references: unknown task, undefined proposal, undefined tailoring, tailored without T-ref
        self.assertTrue(self.plan_of(sub(text, "| SYS-77, SYS-63, SYS-118 | partial |", "| SYS-999 | partial |", 1))
                        .activity_problems())
        self.assertTrue(self.plan_of(sub(text, "| SYS-92 | done |", "| N99 | done |", 1)).activity_problems())
        self.assertTrue(self.plan_of(sub(text, "| — | T-04 | tailored |", "| — | T-44 | tailored |", 1))
                        .activity_problems())
        self.assertTrue(self.plan_of(sub(text, "| — | T-04 | tailored |", "| — | SYS-60 | tailored |", 1))
                        .activity_problems())
        # a partial row whose tasks are all done has nobody who does it (P2-14: SYS-63 is done, SYS-110 open;
        # SYS-77/SYS-63 of P2-03, used before, are both done since 2026-09-29 - DOC-35)
        # DOC-38: P2-14 also names SYS-118 and T-11 - the counter-check used P6-05 (SYS-65 done, SYS-97 open);
        # SAFETYDOC-1001: SYS-97 is done and P6-05 with it, so it uses P2-10 (DOC-19 done, DOC-38 open)
        # SAFETYDOC-1002: DOC-19, DOC-38 (P2-10) and DOC-22, DOC-28, DOC-29 (P6-01) are done and both rows with them,
        # so it uses P2-14 (SYS-63 and SYS-110 done, SYS-118 open)
        tasks = dict(self.tasks, **{"SYS-118": "done"})
        self.assertTrue(any("P2-14" in p for p in self.plan_of(tasks=tasks).activity_problems()))
        # tailoring: rationale, unused tailoring
        row = next(line for line in text.splitlines() if line.startswith("| T-08 |"))
        rationale = _cells(row)[3]
        self.assertTrue(self.plan_of(sub(text, rationale, "not needed", 1)).tailoring_problems())
        broken = sub(text, "| — | T-08 | tailored |", "| — | T-07 | tailored |", 1)
        self.assertTrue(any("T-08" in p for p in self.plan_of(broken).tailoring_problems()))
        # confirmation: missing I1 review of the DFA, unknown independence, unknown status
        broken = sub(text, "| `doc/safety/dfa.md` | I1 required at B |", "| `doc/safety/dfa.md` | I1 by project "
                              "choice |", 1)
        self.assertTrue(any("dfa.md" in p for p in self.plan_of(broken).confirmation_problems()))
        self.assertTrue(self.plan_of(sub(text, "| I1 required at B | confirmation reviewer |",
                                                  "| I0 | confirmation reviewer |", 1)).confirmation_problems())
        self.assertTrue(self.plan_of(sub(text, "| SYS-63 | prepared |", "| SYS-63 | maybe |", 1))
                        .confirmation_problems())
        # release: missing precondition, precondition without task
        row = next(line for line in text.splitlines() if line.startswith("| R5 |"))
        self.assertTrue(self.plan_of(sub(text, row + "\n", "", 1)).release_problems())
        self.assertTrue(self.plan_of(sub(text, row, sub(row, "| SYS-118, SYS-69, SYS-120 |", "|  |"), 1)).release_problems())
        # proposals: short done-when, unused proposal
        row = next(line for line in text.splitlines() if line.startswith("| N4 |"))
        self.assertTrue(self.plan_of(sub(text, row, "| N4 | Roles | tbd | P1 | SYS-92 |", 1)).proposal_problems())
        # SYS-182: N1 ... N10 are carried by tasks; the one not yet carried (N11) must be used by an activity
        self.assertTrue(self.plan_of(sub(text, "| SYS-92, N11 | partial |", "| SYS-92 | partial |", 1))
                        .proposal_problems())
        # summary numbers
        self.assertTrue(self.plan_of(sub(text, "**53** activities", "**52** activities", 1)).summary_problems())
        self.assertTrue(self.plan_of(sub(text, "| SYS-66, SYS-93 | in progress |", "| SYS-66, SYS-93, N2 | planned |", 1))
                        .summary_problems())
        # licence rule
        self.assertEqual(licence_problems(text + "\nsee 6-9.4.4\n"), [])
        self.assertTrue(licence_problems(text + "\nsee ISO 26262-2, Table 1\n"))
        self.assertTrue(licence_problems(text + "\n| 1a | Inspection | + | ++ | ++ | ++ |\n"))
        # malformed tables
        with self.assertRaises(ValueError):
            self.plan_of(sub(text, "<!-- plan-activities:start -->", "", 1))
        with self.assertRaises(ValueError):
            self.plan_of(sub(text, "| ID | Clause | Activity |", "| ID | Clause | Task name |", 1))


if __name__ == "__main__":
    sys.exit(unittest.main())
