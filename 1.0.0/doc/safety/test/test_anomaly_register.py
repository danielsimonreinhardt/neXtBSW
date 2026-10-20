"""SYS-77: consistency of the safety anomaly register and the document register doc/safety/anomaly-register.md.

Checks
- anomaly register (2-5.4.3): IDs AN-nnn unique and in order; every source names a document of SOURCES and a finding
  that exists there; every finding of the source tables listed in SOURCES is registered exactly once (a new finding in
  a safety document without a register row is an error) - including the major and minor findings of the confirmation
  review records doc/safety/reviews/records/CR-nn-k.md (SYS-118, source `reviews/CR-nn-k F-nn`); status words; "Raised by", "Owner" and "Closed by" name
  tasks of doc/todo.yaml or roles of the safety plan; an open row has an open task or is escalated to the safety
  manager (section 4); review, closed and accepted rows carry a rationale; closed rows name the closing task, the
  evidence and a reviewer other than the author; accepted rows are accepted by the user (safety manager); a closed
  row is not closed by a task that is still open;
- escalations: every escalated row exists and is not closed, every open row owned by a role only is escalated;
- document register (8-10.4.5): every safety document (doc/safety/*.md, doc/safety/reviews/*.md, the .sdoc files of
  doc/requirements/safety: ssr, goals, assumptions, mechanisms - DOC-48) has one row with title,
  author, approver, issue, status and change history; authors are tasks of doc/todo.yaml; an accepted document names
  the user's decision, a document in review names what its approval waits for; a row that says the document carries
  its state line must be right;
- the counts in "Result in one page" match the tables;
- licence rule for ISO 26262 (user 2026-09-22): cited by part and clause only.
Counter-checks with deliberately broken copies of the register show that each rule sees its fault.

In the exported tree (DOC-85) some sources stay in the development repository (decision K1: gap analysis, RTOS
comparison, review records, traceability matrix) and the public todo.yaml lacks the done tasks. The export lists
what it left out on purpose (doc/unpublished.yaml, tools/distcheck/unpublished.py): a listed source document is not
read (its findings are not checked against it), a listed document counts as existing, a listed task with its status
as a task. Everything else is checked as in the development repository; an absent source that is not listed stays
an error. The development repository has no list.

Run: .venv/Scripts/python -m unittest doc/safety/test/test_anomaly_register.py
"""

import importlib.util
import pathlib
import re
import sys
import unittest

import yaml

REPO = pathlib.Path(__file__).resolve().parents[3]


def load_unpublished():
    """DOC-85: the export's list of what it left out on purpose (empty in the development repository)."""
    path = REPO / "tools" / "distcheck" / "unpublished.py"
    spec = importlib.util.spec_from_file_location("distcheck_unpublished", path)
    module = sys.modules.setdefault("distcheck_unpublished", importlib.util.module_from_spec(spec))
    if not hasattr(module, "load"):
        spec.loader.exec_module(module)
    return module.load(REPO)


UNPUBLISHED = load_unpublished()
SAFETY = REPO / "doc" / "safety"
DOC = SAFETY / "anomaly-register.md"
SSR_SDOC = REPO / "doc" / "requirements" / "safety" / "ssr.sdoc"
# the safety catalogue and the SSR as StrictDoc documents (DOC-21 step 3); registered by their file name (DOC-48)
CATALOGUE_SDOC = sorted((REPO / "doc" / "requirements" / "safety").glob("*.sdoc"))

ANOMALY_STATUS = ("open", "in progress", "review", "closed", "accepted")
DOC_STATUS = ("draft", "in review", "accepted", "generated")
ROLES = ("safety manager", "project manager", "coordinator")
NONE = ("—", "-", "")

ANOMALY_COLUMNS = ("ID", "Source", "Raised by", "Anomaly", "Owner", "Status", "Closed by",
                   "Closure rationale / next step", "Evidence", "Reviewer")
ESCALATION_COLUMNS = ("ID", "Anomaly", "Why escalated", "Decision needed", "Board item")
DOCUMENT_COLUMNS = ("Document", "Title", "Author", "Approver", "Issue", "Status", "State line", "Change history")

# Source tables whose rows are safety anomalies: document -> list of (start, end, regex of the finding ID).
# start/end cut the section (None = whole document). Questions to the user (Q, U, Q-S, Q-CM, TN) are decisions, not
# anomalies, and stay on the decision board; trace warnings are produced by tools/trace (section 1.2).
SOURCES = {
    "concept.md": [(None, None, r"^\| \*\*(F\d+)\*\* \|"), (None, None, r"^\| \*\*(G\d+)\*\* \|")],
    "fmea.md": [("## 6. New findings", "\n## 7.", r"^\| \*\*(F\d+)\*\* \|"),
                ("## 9. Open points", "\n## 10.", r"^\| (N\d+) \|")],
    "tool-classification.md": [("### 9.1 Findings", "### 9.2", r"^\| \*\*(TF\d+)\*\* \|")],
    "iso26262-gap.md": [("## 7. Findings", "\n## 8.", r"^\| (SG\d+) \|")],
    "cm-plan.md": [("## 10. Gaps", "\n## 11.", r"^\| (G\d+) \|")],
    "static-analysis.md": [(None, None, r"^\| (BEF-\d+) \|")],
    # the SSR in doc/requirements/safety/ssr.sdoc (DOC-21 step 3; was doc/safety/requirements.yaml): the findings R1 ...
    # of the inspection INS-SSR-01 in its review section
    "ssr.sdoc": [(None, None, r"^\s*- \{id: (R\d+),")],
    "hsi-s32k1xx.md": [("## 8. Findings", "\n## 9.", r"^\| \*\*(H\d+)\*\* \|")],
    "dfa.md": [("## 8. New findings", "\n## 9.", r"^\| \*\*(DFA-\d+)\*\* \|"),
               ("## 10.", "\n## 11.", r"^\| (O\d+) \|")],
    "rtos-comparison.md": [("### 1.4 Findings", "**Status after SYS-80", r"^\| (R\d+) \|")],
    "sw-architecture.md": [("### 7.4 Findings", "### 7.5", r"^\| (STK-\d+) \|")],
    "hardware-mechanisms.md": [("## 5. Open points", "\n## 6.", r"^\| (\d+) \|")],
    "safety-case.md": [("## 9. Proposals and open points", "\n## 10.", r"^\| ([SO]\d+) \|")],
    "safety-plan.md": [("## 9. Open points", "\n## 10.", r"^\| (O\d+) \|")],
    # wave cloud-1001 (coordinator): DOC-22 findings DE1 ... DE7; of SYS-62 the anomaly QA-1 (code and comment
    # disagree) and the findings Q-1, Q-3, Q-5, Q-6 - QA-2 ... QA-4 are covered by assumptions of use of the report
    # (SMON-A2, A4, A5), Q-2/Q-4 are planned work of the report, Q-7 is a remark and Q-8 was closed at the merge
    "dev-environment.md": [("## 8. Findings", "\n## 9.", r"^\| (DE\d+) \|")],
    "component-qualification.md": [("### 5.6 Anomalies", "### 5.7", r"^\| (QA-1) \|"),
                                   ("## 6. Findings", "\n## 7.", r"^\| (Q-[1356]) \|")],
    # DOC-55 (CM-02 decision Z7): the findings ASA-1 ... ASA-12 of the architecture safety analysis (SYS-71)
    "architecture-safety-analysis.md": [("## 6. New findings", "\n## 7.", r"^\| \*\*(ASA-\d+)\*\* \|")],
    # DOC-59 (re-check of CM-04, RN): findings against the released verification plan, VF-1 ... in its section 10.1;
    # an open finding of the record CR-04-1 would contradict its verdict `accepted`
    "verification-plan.md": [("### 10.1 Findings", "\n## 11.", r"^\| (VF-\d+) \|")],
}
# A finding that is the same anomaly as one registered under another source (registered once): ASA-3 is fmea.md N5
# (AN-130, decision S173-1; CM-02 decision Z7).
REGISTERED_AS = {("architecture-safety-analysis.md", "ASA-3"): ("fmea.md", "N5")}
# SYS-118: the records of the confirmation reviews (doc/safety/reviews/README.md section 5, step 4). Every major and
# minor finding F-nn of a record CR-nn-k is registered with the source `reviews/CR-nn-k F-nn`; remarks are not. A
# record may have no such finding, so these sources are not required to contribute one (test_sources_found).
RECORDS = SAFETY / "reviews" / "records"
RECORD_FINDINGS = [("<!-- findings:start -->", "<!-- findings:end -->", r"^\| (F-\d\d) \| (?:major|minor) \|")]
for _record in (sorted(RECORDS.glob("CR-*.md")) if RECORDS.is_dir() else []):
    SOURCES[f"reviews/{_record.stem}"] = RECORD_FINDINGS
# DOC-85: a record the export left out is still a source of the register (its rows stay), only its text is absent
for _record in UNPUBLISHED.under("doc/safety/reviews/records", "CR-*.md"):
    SOURCES.setdefault(f"reviews/{pathlib.PurePosixPath(_record).stem}", RECORD_FINDINGS)
# fmea.md continues the numbering of concept.md: its F9 ... are the concept's F9 ..., registered once with the concept.
ALSO_REGISTERED_AS = {"fmea.md": {"F": "concept.md"}}


def _cells(line: str) -> list:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def block(text: str, name: str) -> str:
    try:
        return text.split(f"<!-- {name}:start -->", 1)[1].split(f"<!-- {name}:end -->", 1)[0]
    except IndexError:
        raise ValueError(f"markers {name}:start/end missing") from None


def table(text: str, name: str, columns: tuple, allow_empty: bool = False) -> list:
    """Rows of the marked table as dicts; raises ValueError on a malformed table."""
    lines = [line for line in block(text, name).splitlines() if line.startswith("|")]
    if len(lines) < (2 if allow_empty else 3) or tuple(_cells(lines[0])) != columns:
        raise ValueError(f"{name}: header is not {columns}")
    rows = []
    for line in lines[2:]:
        cells = _cells(line)
        if len(cells) != len(columns):
            raise ValueError(f"{name}: row with {len(cells)} cells: {line[:60]}")
        rows.append(dict(zip(columns, cells)))
    return rows


def refs(cell: str) -> list:
    """Comma-separated references, with an optional explanation in parentheses dropped."""
    cell = re.sub(r"\([^)]*\)", "", cell)
    return [r.strip() for r in cell.split(",") if r.strip() not in NONE]


def section(text: str, start, end) -> str:
    if start is not None:
        if start not in text:
            return ""
        text = text.split(start, 1)[1]
    if end is not None:
        text = text.split(end, 1)[0]
    return text


def findings(texts: dict) -> set:
    """(document, finding ID) of every row of the source tables."""
    found = set()
    for doc, parts in SOURCES.items():
        for start, end, pattern in parts:
            for fid in re.findall(pattern, section(texts.get(doc, ""), start, end), flags=re.MULTILINE):
                found.add((doc, fid))
    return found


def licence_problems(text: str) -> list:
    """References that go beyond part and clause: tables or figures, and the recommendation marks of its tables."""
    problems = [f"reference to a table or figure: '{m}'" for m in re.findall(r"\b(?:Table|Figure)\s+[A-Z]?\d+", text)]
    problems += [f"recommendation mark copied: '{line.strip()[:60]}'" for line in text.splitlines()
                 if re.search(r"(^|[\s|])(\+\+|\+|o)\s*\|\s*(\+\+|\+|o)(\s|\|)", line)]
    return problems


class Register:
    """The parsed register with the repository facts it is checked against."""

    def __init__(self, text: str, tasks: dict, sources: dict, files: list, unpublished=frozenset()):
        self.text = text
        self.tasks = tasks                  # task ID -> status
        self.sources = sources              # document name -> text
        self.files = files                  # safety documents that must have a row
        self.unpublished = set(unpublished)  # DOC-85: names (as in the register) the export left out on purpose
        self.anomalies = table(text, "anomalies", ANOMALY_COLUMNS)
        # DOC-38: the escalation table may be empty (every escalation decided by the safety manager)
        self.escalations = table(text, "escalations", ESCALATION_COLUMNS, allow_empty=True)
        self.documents = table(text, "documents", DOCUMENT_COLUMNS)

    # ---- helpers
    def ref_problems(self, where: str, column: str, cell: str, roles=True) -> list:
        problems = []
        for ref in refs(cell):
            if roles and ref in ROLES:
                continue
            if ref not in self.tasks:
                problems.append(f"{where}: {column} '{ref}' is neither a task of doc/todo.yaml nor a role")
        return problems

    def open_task(self, ref: str) -> bool:
        return ref in self.tasks and self.tasks[ref] != "done"

    # ---- rules
    def anomaly_problems(self) -> list:
        problems = []
        ids = [row["ID"] for row in self.anomalies]
        expected = [f"AN-{n:03d}" for n in range(1, len(ids) + 1)]
        if ids != expected:
            problems.append("anomaly IDs are not AN-001 ... in order without gaps")
        registered = {}
        existing = findings(self.sources)
        for row in self.anomalies:
            where = row["ID"]
            match = re.fullmatch(r"(\S+) (\S+)", row["Source"].replace("`", ""))
            if not match or match.group(1) not in SOURCES:
                problems.append(f"{where}: source '{row['Source']}' is not '<document of SOURCES> <finding ID>'")
                continue
            key = (match.group(1), match.group(2))
            if key in registered:
                problems.append(f"{where}: source {key[0]} {key[1]} registered twice ({registered[key]})")
            registered[key] = where
            if key not in existing and key[0] not in self.unpublished:
                problems.append(f"{where}: finding {key[1]} not found in the source table of {key[0]}")
            status = row["Status"]
            if status not in ANOMALY_STATUS:
                problems.append(f"{where}: status '{status}' not in {ANOMALY_STATUS}")
                continue
            if not refs(row["Raised by"]):
                problems.append(f"{where}: 'Raised by' missing")
            problems += self.ref_problems(where, "Raised by", row["Raised by"], roles=False)
            if not refs(row["Owner"]):
                problems.append(f"{where}: no owner")
            problems += self.ref_problems(where, "Owner", row["Owner"])
            problems += self.ref_problems(where, "Closed by", row["Closed by"])
            if len(row["Anomaly"]) < 20:
                problems.append(f"{where}: anomaly not described")
            if len(row["Closure rationale / next step"]) < 20:
                problems.append(f"{where}: closure rationale or next step missing")
            closed_by = refs(row["Closed by"])
            reviewer = row["Reviewer"]
            if status in ("open", "in progress"):
                owners = refs(row["Owner"])
                if not any(self.open_task(o) for o in owners) and not any(o in ROLES for o in owners):
                    problems.append(f"{where}: status {status}, but no open task and no role owns it")
            if status in ("review", "closed") and not closed_by:
                problems.append(f"{where}: status {status} without 'Closed by'")
            if status == "closed":
                if row["Evidence"] in NONE:
                    problems.append(f"{where}: closed without evidence")
                if reviewer in NONE:
                    problems.append(f"{where}: closed without reviewer")
                elif not re.match(r"(coordinator|user|confirmation reviewer) \(", reviewer):
                    problems.append(f"{where}: reviewer '{reviewer}' is not the coordinator, the user or a "
                                    "confirmation reviewer (the author cannot review the closure)")
                for task in closed_by:
                    if self.tasks.get(task) == "open":
                        problems.append(f"{where}: closed by {task}, which is still open in doc/todo.yaml")
            if status == "accepted" and not reviewer.startswith("user ("):
                problems.append(f"{where}: accepted, but not by the user as safety manager with a decision ID")
        for doc, fid in sorted(existing - set(registered)):
            prefix = re.match(r"[A-Z]*", fid).group(0)
            alias = ALSO_REGISTERED_AS.get(doc, {}).get(prefix)
            if alias and (alias, fid) in registered:
                continue
            if REGISTERED_AS.get((doc, fid)) in registered:
                continue
            problems.append(f"finding {doc} {fid} is not in the register")
        return problems

    def escalation_problems(self) -> list:
        problems = []
        rows = {row["ID"]: row for row in self.anomalies}
        escalated = [row["ID"] for row in self.escalations]
        if len(escalated) != len(set(escalated)):
            problems.append("an anomaly is escalated twice")
        for row in self.escalations:
            anomaly = rows.get(row["ID"])
            if anomaly is None:
                problems.append(f"escalation {row['ID']}: no such anomaly")
            elif anomaly["Status"] not in ("open", "in progress"):
                problems.append(f"escalation {row['ID']}: anomaly is {anomaly['Status']}, not open")
            if len(row["Decision needed"]) < 20:
                problems.append(f"escalation {row['ID']}: decision needed not stated")
        for row in self.anomalies:
            owners = refs(row["Owner"])
            if (row["Status"] in ("open", "in progress") and owners
                    and not any(self.open_task(o) for o in owners) and row["ID"] not in escalated):
                problems.append(f"{row['ID']}: open without an open task, but not escalated to the safety manager")
        return problems

    def document_problems(self) -> list:
        problems = []
        listed = [row["Document"].replace("`", "") for row in self.documents]
        if len(listed) != len(set(listed)):
            problems.append("a document is listed twice")
        problems += [f"safety document {f} has no row in the document register" for f in self.files
                     if f not in listed]
        problems += [f"document register lists {f}, which does not exist" for f in listed
                     if f not in self.files and f not in self.unpublished]
        for row in self.documents:
            where = row["Document"].replace("`", "")
            for column in ("Title", "Issue", "Change history"):
                if row[column] in NONE:
                    problems.append(f"{where}: {column} missing")
            if not refs(row["Author"]):
                problems.append(f"{where}: no author")
            problems += self.ref_problems(where, "Author", row["Author"], roles=False)
            status, approver = row["Status"], row["Approver"]
            if status not in DOC_STATUS:
                problems.append(f"{where}: status '{status}' not in {DOC_STATUS}")
            elif status == "accepted" and not re.match(r"user \(\S+", approver):
                problems.append(f"{where}: accepted without the user's decision as approver")
            elif status in ("draft", "in review") and not re.match(r"(pending|user) \(.+\)", approver):
                problems.append(f"{where}: approver must say what the approval waits for: pending (<item>)")
            elif status == "generated" and "generated" not in row["Change history"] + approver:
                problems.append(f"{where}: generated document must name its generator")
            if row["State line"].startswith("present") and where in self.sources:
                head = "\n".join(self.sources[where].splitlines()[:15])
                if not re.search(r"(Working state|Baseline)", head):
                    problems.append(f"{where}: register says the state line is present, the header has none")
        return problems

    def summary_problems(self) -> list:
        """The counts of section 0 must match the tables."""
        result = self.text.split("## 0. Result in one page", 1)[1].split("\n## 1.", 1)[0]
        counts = {s: sum(1 for r in self.anomalies if r["Status"] == s) for s in ANOMALY_STATUS}
        docs = {s: sum(1 for r in self.documents if r["Status"] == s) for s in DOC_STATUS}
        sources = {r["Source"].replace("`", "").split(" ")[0] for r in self.anomalies}
        expected = [
            f"**{len(self.anomalies)}** anomalies from {len(sources)} safety documents",
            ", ".join(f"{counts[s]} {s}" for s in ANOMALY_STATUS),
            f"**{len(self.escalations)}** escalated",
            f"**{len(self.documents)}** safety documents: " + ", ".join(f"{docs[s]} {s}" for s in DOC_STATUS),
        ]
        return [f"section 0 does not say: {e}" for e in expected if e not in result]

    def all_problems(self) -> list:
        return (self.anomaly_problems() + self.escalation_problems() + self.document_problems()
                + self.summary_problems() + licence_problems(self.text))


def load_tasks(unpublished=UNPUBLISHED) -> dict:
    data = yaml.safe_load((REPO / "doc" / "todo.yaml").read_text(encoding="utf-8"))
    # DOC-85: in the export the tasks the public todo.yaml leaves out (done ones) come from the export's list
    return dict(unpublished.tasks) | {t["id"]: t.get("status", "open") for t in data["tasks"]}


def unpublished_names(unpublished=UNPUBLISHED) -> set:
    """DOC-85: the files the export left out, named the way the register names them (sources and documents)."""
    names = set()
    for rel in unpublished.files:
        path = pathlib.PurePosixPath(rel)
        folder = path.parent.as_posix()
        if (folder, path.suffix) in (("doc/safety", ".md"), ("doc/requirements/safety", ".sdoc")):
            names.add(path.name)
        elif (folder, path.suffix) == ("doc/safety/reviews", ".md"):
            names.add(f"reviews/{path.name}")
        elif (folder, path.suffix) == ("doc/safety/reviews/records", ".md"):
            names.add(f"reviews/{path.stem}")
    return names


def load_sources() -> dict:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(SAFETY.glob("*.md"))} | {
        f"reviews/{p.name}": p.read_text(encoding="utf-8") for p in sorted((SAFETY / "reviews").glob("*.md"))} | {
        f"reviews/{p.stem}": p.read_text(encoding="utf-8") for p in sorted(RECORDS.glob("CR-*.md"))} | {
        p.name: p.read_text(encoding="utf-8") for p in CATALOGUE_SDOC}


def safety_files() -> list:
    # the documents of the confirmation reviews (SYS-63) live in doc/safety/reviews and count as safety documents
    return sorted([p.name for p in SAFETY.glob("*.md")] + [p.name for p in CATALOGUE_SDOC]
                  + [f"reviews/{p.name}" for p in (SAFETY / "reviews").glob("*.md")])


class AnomalyRegisterConsistency(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DOC.read_text(encoding="utf-8")
        cls.tasks = load_tasks()
        cls.sources = load_sources()
        cls.files = safety_files()
        cls.unpublished = unpublished_names()
        cls.register = Register(cls.text, cls.tasks, cls.sources, cls.files, cls.unpublished)

    def register_of(self, text=None, tasks=None, sources=None, files=None, unpublished=None):
        return Register(self.text if text is None else text, self.tasks if tasks is None else tasks,
                        self.sources if sources is None else sources, self.files if files is None else files,
                        self.unpublished if unpublished is None else unpublished)

    def test_sources_found(self):
        # every source document contributes at least one finding; an empty extraction means a changed heading
        found = findings(self.sources)
        for doc, parts in SOURCES.items():
            if parts is RECORD_FINDINGS:
                continue      # a review record may have no major or minor finding
            if doc in self.unpublished:
                self.assertNotIn(doc, self.sources, f"{doc} is listed as unpublished, but the tree has it")
                continue      # DOC-85: left out of the export on purpose
            self.assertTrue(any(d == doc for d, _ in found), f"no finding extracted from {doc}")

    def test_unpublished_names(self):
        """DOC-85: the names of the export's list in the register's naming; empty in the development repository."""
        listed = type(UNPUBLISHED)(files=frozenset({
            "doc/safety/iso26262-gap.md", "doc/safety/reviews/README.md", "doc/safety/reviews/records/CR-01-1.md",
            "doc/requirements/safety/ssr.sdoc", "doc/safety/reviews/reviewlib.py", "doc/requirements/poc/ssr.sdoc"}))
        self.assertEqual(unpublished_names(listed),
                         {"iso26262-gap.md", "reviews/README.md", "reviews/CR-01-1", "ssr.sdoc"})
        if not (REPO / "doc" / "unpublished.yaml").exists():
            self.assertEqual(self.unpublished, set())

    def test_anomalies(self):
        self.assertEqual(self.register.anomaly_problems(), [])

    def test_escalations(self):
        self.assertEqual(self.register.escalation_problems(), [])

    def test_documents(self):
        self.assertEqual(self.register.document_problems(), [])

    def test_summary(self):
        self.assertEqual(self.register.summary_problems(), [])

    def test_standard_cited_by_part_and_clause_only(self):
        self.assertEqual(licence_problems(self.text), [])

    # Counter-checks: every rule must see a broken register or a changed repository
    def test_counter_checks(self):
        def sub(source, old, new, count=1):
            """str.replace that fails when the edit does not hit, so no counter-check passes vacuously."""
            self.assertIn(old, source)
            return source.replace(old, new, count)

        def row_of(anomaly_id):
            return next(line for line in self.text.splitlines() if line.startswith(f"| {anomaly_id} |"))

        def cells_of(anomaly_id):
            return _cells(row_of(anomaly_id))

        def with_cell(anomaly_id, column, value):
            cells = cells_of(anomaly_id)
            cells[ANOMALY_COLUMNS.index(column)] = value
            return sub(self.text, row_of(anomaly_id), "| " + " | ".join(cells) + " |")

        rows = self.register.anomalies
        closed = next(r["ID"] for r in rows if r["Status"] == "closed")
        open_row = next(r["ID"] for r in rows if r["Status"] == "open" and r["ID"] not in
                        {e["ID"] for e in self.register.escalations})
        if self.register.escalations:
            escalated, text_esc = self.register.escalations[0]["ID"], self.text
        else:
            # DOC-38: no escalation is open; the escalation rules are checked on a copy with a synthetic one
            escalated = open_row
            text_esc = sub(self.text, "<!-- escalations:end -->",
                           f"| {open_row} | synthetic | synthetic case of the test | the safety manager decides this "
                           "synthetic case | test |\n<!-- escalations:end -->")
            self.assertEqual(self.register_of(text_esc).escalation_problems(), [])

        # anomaly rows
        self.assertTrue(self.register_of(with_cell(closed, "ID", "AN-999")).anomaly_problems())
        self.assertTrue(self.register_of(with_cell(closed, "Source", "`concept.md F99`")).anomaly_problems())
        self.assertTrue(self.register_of(with_cell(closed, "Source", "`nowhere.md F1`")).anomaly_problems())
        self.assertTrue(self.register_of(with_cell(closed, "Status", "fixed")).anomaly_problems())
        self.assertTrue(self.register_of(with_cell(closed, "Evidence", "—")).anomaly_problems())
        self.assertTrue(self.register_of(with_cell(closed, "Reviewer", "—")).anomaly_problems())
        self.assertTrue(self.register_of(with_cell(closed, "Reviewer", "SYS-41")).anomaly_problems())
        self.assertTrue(self.register_of(with_cell(closed, "Closed by", "—")).anomaly_problems())
        self.assertTrue(self.register_of(with_cell(closed, "Closed by", "SYS-9999")).anomaly_problems())
        self.assertTrue(self.register_of(with_cell(closed, "Owner", "somebody")).anomaly_problems())
        self.assertTrue(self.register_of(with_cell(closed, "Closure rationale / next step", "ok"))
                        .anomaly_problems())
        self.assertTrue(self.register_of(with_cell(closed, "Status", "accepted")).anomaly_problems())
        closer = refs(dict(zip(ANOMALY_COLUMNS, cells_of(closed)))["Closed by"])[0]
        self.assertTrue(self.register_of(tasks=dict(self.tasks, **{closer: "open"})).anomaly_problems())
        # a finding added to a source document without a register row
        concept = sub(self.sources["concept.md"], "| **F16** |", "| **F17** | new | x | y | z |\n| **F16** |")
        problems = self.register_of(sources=dict(self.sources, **{"concept.md": concept})).anomaly_problems()
        self.assertIn("finding concept.md F17 is not in the register", problems)
        # a finding removed from its source document
        concept = sub(self.sources["concept.md"], "| **F16** |", "| F16-gone |")
        self.assertTrue(self.register_of(sources=dict(self.sources, **{"concept.md": concept})).anomaly_problems())
        # SYS-118: a minor finding added to a review record must be registered, a remark need not
        records = [d for d, parts in SOURCES.items() if parts is RECORD_FINDINGS and d in self.sources]
        if records:
            name = records[0]
            record = self.sources[name]
            for cls, expected in (("minor", True), ("remark", False)):
                added = sub(record, "<!-- findings:end -->",
                            f"| F-99 | {cls} | x | synthetic | G-01 | — | — | open |\n<!-- findings:end -->")
                problems = self.register_of(sources=dict(self.sources, **{name: added})).anomaly_problems()
                self.assertEqual(f"finding {name} F-99 is not in the register" in problems, expected, cls)
        # open row whose owner task is done
        owner = next(o for o in refs(dict(zip(ANOMALY_COLUMNS, cells_of(open_row)))["Owner"]) if o in self.tasks)
        broken = self.register_of(tasks=dict(self.tasks, **{owner: "done"}))
        self.assertTrue(broken.anomaly_problems() + broken.escalation_problems())

        # escalations: owner a role only, but not escalated; escalated row closed; unknown ID
        self.assertTrue(self.register_of(with_cell(open_row, "Owner", "safety manager")).escalation_problems())
        row_line = next(line for line in text_esc.splitlines() if line.startswith(f"| {escalated} |"))
        cells = _cells(row_line)
        cells[ANOMALY_COLUMNS.index("Status")] = "closed"
        self.assertTrue(self.register_of(sub(text_esc, row_line, "| " + " | ".join(cells) + " |"))
                        .escalation_problems())
        escalation_line = next(line for line in block(text_esc, "escalations").splitlines()
                               if line.startswith(f"| {escalated} |"))
        self.assertTrue(self.register_of(sub(text_esc, escalation_line, escalation_line.replace(escalated, "AN-999")))
                        .escalation_problems())

        # documents
        self.assertTrue(self.register_of(files=self.files + ["new-analysis.md"]).document_problems())
        self.assertTrue(self.register_of(files=[f for f in self.files if f != "dfa.md"]).document_problems())
        accepted = next(r for r in self.register.documents if r["Status"] == "accepted")
        line = next(ln for ln in block(self.text, "documents").splitlines()
                    if ln.startswith(f"| {accepted['Document']} |"))
        self.assertTrue(self.register_of(sub(self.text, line, line.replace(accepted["Approver"], "pending (x)")))
                        .document_problems())
        in_review = next(r for r in self.register.documents if r["Status"] in ("in review", "draft"))
        line = next(ln for ln in block(self.text, "documents").splitlines()
                    if ln.startswith(f"| {in_review['Document']} |"))
        self.assertTrue(self.register_of(sub(self.text, line, line.replace(in_review["Approver"], "—")))
                        .document_problems())
        self.assertTrue(self.register_of(sub(self.text, line, line.replace(in_review["Author"], "SYS-9999")))
                        .document_problems())
        self.assertTrue(self.register_of(sub(self.text, line, line.replace(f"| {in_review['Status']} |", "| released |")))
                        .document_problems())
        present = next(r for r in self.register.documents if r["State line"].startswith("present")
                       and r["Document"].replace("`", "") in self.sources)
        name = present["Document"].replace("`", "")
        stripped = re.sub(r"(Working state|Baseline)", "State", self.sources[name])
        self.assertTrue(self.register_of(sources=dict(self.sources, **{name: stripped})).document_problems())

        # DOC-85: what the export lists as left out on purpose is accepted, any other absent file is not
        def about(problems, text):
            return [p for p in problems if text in p]

        without_fmea = {k: v for k, v in self.sources.items() if k != "fmea.md"}
        missing = "not found in the source table of fmea.md"
        self.assertTrue(about(self.register_of(sources=without_fmea, unpublished=set()).anomaly_problems(), missing))
        self.assertEqual(about(self.register_of(sources=without_fmea, unpublished={"fmea.md"}).anomaly_problems(),
                               missing), [])
        without_dfa = [f for f in self.files if f != "dfa.md"]
        self.assertTrue(about(self.register_of(files=without_dfa, unpublished=set()).document_problems(), "dfa.md"))
        self.assertEqual(self.register_of(files=without_dfa, unpublished=self.unpublished | {"dfa.md"})
                         .document_problems(), self.register.document_problems())
        # a listed task counts with its status (a closing task listed as open stays an error, see above), an absent
        # task that is not listed is an error; todo.yaml wins over the list
        self.assertTrue(self.register_of(tasks={k: v for k, v in self.tasks.items() if k != closer})
                        .anomaly_problems())
        todo = load_tasks(type(UNPUBLISHED)())
        some = next(iter(todo))
        merged = load_tasks(type(UNPUBLISHED)(tasks={"SYS-99999": "done", some: "listed"}))
        self.assertEqual((merged["SYS-99999"], merged[some]), ("done", todo[some]))

        # summary and licence rule
        self.assertTrue(self.register_of(sub(self.text, f"**{len(rows)}** anomalies", f"**{len(rows) + 1}** anomalies"))
                        .summary_problems())
        self.assertTrue(licence_problems(self.text + "\nsee Table 9 of part 6\n"))


if __name__ == "__main__":
    unittest.main()
