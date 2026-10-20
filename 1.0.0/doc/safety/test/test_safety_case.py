"""SYS-64: every evidence link of the safety case doc/safety/safety-case.md resolves, and the case is consistent.

Checks
- evidence links: every reference resolves - a path exists, `path::text` names a file that contains the text,
  `test:<name>` names an entry of tools/test/tests.yaml whose field `requirements` contains every SSR of the evidence
  row; `—` only for a missing item; kind, status and "since" from their vocabularies;
- status: available and partial items resolve completely, a missing item does not; partial and missing items name
  an open task of doc/todo.yaml (a carrier task that is done is an error: update the case);
- increments: "since" names an increment of section 7; when the tag is present in the clone, every item since
  v0.1.0 resolves in the tree of that tag (a moved work product under its old path, MOVED) and every item since
  `next` does not; the header names the working state
  of the row `next`; an increment's baseline is a release tag vX.Y.Z or a release candidate vX.Y.Z-rcN (TOOL-232)
  not newer than VERSION, and once that tag is in the clone the row names the commit the tag points at - the row
  itself is written after the tag, in a later documentation commit (section 7 rule 1), so the commit is read from
  the tag, never from HEAD;
- claims: product claims are the assumed safety goals (goals.sdoc) word for word, the process claim is the claim
  of the safety plan; every claim has an argument, every argument a claim;
- product argument: the basis is exactly the set of SSR that ssr.sdoc derives from the goal, every SSR
  has one test evidence row (ET-nnn) that names at least one test unless the SSR is open, every test and board
  record of the basis is cited; the verdict equals the stage-1 coverage (COVERAGE of the goal in goals.sdoc);
- context argument: the basis is exactly the set of AoU cited by ssr.sdoc, each defined in assumptions.sdoc or the
  tool classification;
  (DOC-21 step 3: the SSR, goals and assumptions of use are StrictDoc nodes in doc/requirements/safety, read through
  tools/trace and StrictDoc's JSON export, instead of requirements.yaml and the tables of concept.md and fmea.md)
- process argument: the four rows cite every activity of the safety plan exactly once and every work product the
  plan names for them; a row is "supported" only if all its activities are done or tailored;
- verdicts: "supported" only without deficits and with all evidence available; the top claim is not stronger than
  its weakest sub-claim;
- deficits: cited both ways with the claims they affect, carried by an open task or a tailoring of the plan;
- every evidence item is cited by an argument and every cited one is defined;
- the numbers in the "Result in one page" match the tables;
- licence rule for ISO 26262: cited by part and clause only.
Counter-checks with deliberately broken copies of the document show that each rule sees its fault.
In the exported tree (DOC-85) a reference may point at something the export left out on purpose (K1 documents,
board captures, removed test entries, the done tasks and internal fields of todo.yaml). It resolves only if the
export lists it (doc/unpublished.yaml, tools/distcheck/unpublished.py): a listed path counts as existing (its text
is not checked), a text in a file published only in a cleaned form is not checked, a listed test entry counts as
existing (its requirements are not checked), a listed task counts with its status. The development repository has
no list; there and for everything unlisted the checks are unchanged.

Run: .venv/Scripts/python -m unittest doc/safety/test/test_safety_case.py
"""

import pathlib
import re
import subprocess
import sys
import unittest

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_safety_plan import (ACTIVITY_COLUMNS, CLAIM, TAILORING_COLUMNS, _cells, block,  # noqa: E402
                              licence_problems, load_tasks, load_unpublished, paths, refs, table)

REPO = pathlib.Path(__file__).resolve().parents[3]
SAFETY = REPO / "doc" / "safety"
sys.path.insert(0, str(REPO / "tools" / "trace"))
import tracecheck  # noqa: E402  (StrictDoc model of the SSR and the safety catalogue, DOC-21)
DOC = SAFETY / "safety-case.md"

CLAIM_COLUMNS = ("ID", "Kind", "Claim", "Source")
ARGUMENT_COLUMNS = ("ID", "Claim", "Strategy", "Basis", "Evidence", "Deficits", "Verdict")
EVIDENCE_COLUMNS = ("ID", "Evidence", "Kind", "Reference", "Requirements", "Since", "Status", "Task")
DEFICIT_COLUMNS = ("ID", "Deficit", "Affects", "Task", "Consequence until removed")
INCREMENT_COLUMNS = ("Increment", "Baseline", "Commit", "Safety status", "Change to the case")

KINDS = ("work product", "analysis", "test", "board record", "decision", "record")
STATUS = ("available", "partial", "missing")
VERDICTS = ("supported", "supported with limits", "partly supported", "not supported")   # strong to weak
EVIDENCE_TABLES = ("case-evidence-work", "case-evidence-board", "case-evidence-tests")
# a release tag, also a release candidate (Release R1 = v1.0.0-rc1; DOC-79)
TAG = r"v\d+\.\d+\.\d+(?:-rc[1-9]\d*)?"   # TOOL-232: rcN with N = 1, 2, ... (no rc0, no leading zero)
# development releases without an increment of their own (CM-05 decision Z5 a, DOC-79): the header of a working
# state after them may name them although section 7 has no row for them
WITHOUT_INCREMENT = ("v0.2.0",)
# stage-1 coverage of a goal (COVERAGE of the ASG node; concept.md 2.2 before DOC-21 step 3) -> strongest verdict
COVERAGE = {"partial": "partly supported", "limited": "supported with limits", "none": "not supported",
            "covered": "supported"}
AOU_SOURCES = ("../requirements/safety/assumptions.sdoc", "tool-classification.md")
# a work product that moved after a release: its path today -> its path in the older tags (the "since" check reads the
# tag's tree); DOC-21 step 3 moved the SSR to StrictDoc
MOVED = {"doc/requirements/safety/ssr.sdoc": "doc/safety/requirements.yaml"}


def version(tag: str) -> tuple:
    """(major, minor, patch) of a release tag; a release candidate sorts with its version."""
    return tuple(int(x) for x in re.match(r"v(\d+)\.(\d+)\.(\d+)", tag).groups())


def strength(verdict: str) -> int:
    return len(VERDICTS) - VERDICTS.index(verdict)


def references(cell: str) -> list:
    return re.findall(r"`([^`]+)`", cell)


def model_goals(model) -> dict:
    """ASG-nn -> (goal text, COVERAGE) from the ASG nodes (goals.sdoc); withdrawn goals left out."""
    return {gid: (" ".join(f.get("STATEMENT", "").split()), f.get("COVERAGE", ""))
            for gid, f in model.goals.items() if f.get("STATUS") != "withdrawn"}


class Repo:
    """The repository facts the case is checked against; overridable for counter-checks."""

    def __init__(self):
        self.unpublished = load_unpublished()          # DOC-85: empty in the development repository
        self.tasks = load_tasks(self.unpublished)
        self.tests = {e["name"]: set(e.get("requirements") or [])
                      for e in yaml.safe_load((REPO / "tools/test/tests.yaml").read_text(encoding="utf-8"))["tests"]}
        model = tracecheck.safety_model()
        self.requirements = model.requirements
        self.goals = model_goals(model)
        plan = (SAFETY / "safety-plan.md").read_text(encoding="utf-8")
        self.activities = table(plan, "plan-activities", ACTIVITY_COLUMNS)
        self.tailoring = {r["ID"] for r in table(plan, "plan-tailoring", TAILORING_COLUMNS)}
        self.aou_text = "\n".join((SAFETY / s).read_text(encoding="utf-8") for s in AOU_SOURCES)
        self.files = {}

    def read(self, path: str):
        if path not in self.files:
            f = REPO / path
            self.files[path] = f.read_text(encoding="utf-8", errors="replace") if f.is_file() else None
        return self.files[path]

    def exists(self, path: str) -> bool:
        return (REPO / path).exists() or self.unpublished.file(path)


class TagTree:
    """Read access to the tree of a tag; `available` is False when the tag is not in the clone."""

    def __init__(self, tag: str):
        self.tag = tag
        self.available = subprocess.run(["git", "-C", str(REPO), "rev-parse", "-q", "--verify", f"{tag}^{{commit}}"],
                                        capture_output=True).returncode == 0
        self.cache = {}

    def commit(self) -> str:
        return subprocess.run(["git", "-C", str(REPO), "rev-parse", f"{self.tag}^{{commit}}"],
                              capture_output=True, text=True).stdout.strip()

    @staticmethod
    def commit_of(tag: str):
        """The commit `tag` points at in this clone; None while the tag does not exist (TOOL-232: the increment of a
        release candidate gets its tag commit only after the tag is set)."""
        done = subprocess.run(["git", "-C", str(REPO), "rev-parse", "-q", "--verify", f"{tag}^{{commit}}"],
                              capture_output=True, text=True)
        return done.stdout.strip() if done.returncode == 0 and done.stdout.strip() else None

    def exists(self, path: str) -> bool:
        return subprocess.run(["git", "-C", str(REPO), "cat-file", "-e", f"{self.tag}:{path}"],
                              capture_output=True).returncode == 0

    def read(self, path: str):
        if path not in self.cache:
            r = subprocess.run(["git", "-C", str(REPO), "show", f"{self.tag}:{path}"], capture_output=True)
            self.cache[path] = r.stdout.decode("utf-8", "replace") if r.returncode == 0 else None
        return self.cache[path]

    def tests(self) -> dict:
        data = yaml.safe_load(self.read("tools/test/tests.yaml") or "tests: []")
        return {e["name"]: set(e.get("requirements") or []) for e in data["tests"]}


def resolves(ref: str, requirements: list, exists, read, tests, unpublished=None) -> str:
    """'' when the reference resolves, else the reason. `unpublished` (DOC-85): the export's list - a listed test
    entry resolves, the text of a listed path or of a file published only in a cleaned form is not checked."""
    if ref.startswith("test:"):
        name = ref[5:]
        if name not in tests:
            if unpublished is not None and name in unpublished.tests:
                return ""
            return f"test entry {name} not in tools/test/tests.yaml"
        missing = [r for r in requirements if r.startswith("SSR-") and r not in tests[name]]
        return f"test entry {name} does not name {', '.join(missing)}" if missing else ""
    path, _, needle = ref.partition("::")
    if not exists(path):
        return f"{path} does not exist"
    if needle:
        content = read(path)
        if unpublished is not None and (unpublished.content_hidden(path)
                                        or (content is None and unpublished.file(path))):
            return ""
        if content is None or needle not in content:
            return f"{path} does not contain '{needle}'"
    return ""


class Case:
    def __init__(self, text: str, repo: Repo):
        self.text = text
        self.repo = repo
        self.claims = table(text, "case-claims", CLAIM_COLUMNS)
        self.product = table(text, "case-product", ARGUMENT_COLUMNS)
        self.process = table(text, "case-process", ARGUMENT_COLUMNS)
        self.evidence = [row for name in EVIDENCE_TABLES for row in table(text, name, EVIDENCE_COLUMNS)]
        self.deficits = table(text, "case-deficits", DEFICIT_COLUMNS)
        self.increments = table(text, "case-increments", INCREMENT_COLUMNS)
        self.arguments = self.product + self.process
        self.by_id = {e["ID"]: e for e in self.evidence}

    def open_task(self, ref: str) -> bool:
        return ref in self.repo.tasks and self.repo.tasks[ref] != "done"

    # ---- evidence
    def evidence_problems(self) -> list:
        problems = []
        ids = [e["ID"] for e in self.evidence]
        problems += [f"duplicate evidence ID {i}" for i in sorted({i for i in ids if ids.count(i) > 1})]
        since_values = {i["Baseline"] for i in self.increments}
        for e in self.evidence:
            where = e["ID"]
            if not re.fullmatch(r"E[WDRBT]-\d\d\d?", where):
                problems.append(f"{where}: ID not of the form E<W|D|R|B|T>-nn")
            if e["Kind"] not in KINDS:
                problems.append(f"{where}: kind '{e['Kind']}' not in {KINDS}")
            status = e["Status"]
            if status not in STATUS:
                problems.append(f"{where}: status '{status}' not in {STATUS}")
                continue
            reqs = refs(e["Requirements"])
            refs_ = references(e["Reference"])
            if e["Kind"] in ("test", "board record") and not reqs:
                problems.append(f"{where}: {e['Kind']} without requirement")
            for r in reqs:
                if not re.fullmatch(r"(SSR|AoU)-\d\d\d?", r):
                    problems.append(f"{where}: '{r}' is not an SSR or AoU")
            if e["Kind"] == "test" and any(not r.startswith("test:") for r in refs_):
                problems.append(f"{where}: a test item may only reference test entries")
            if e["Kind"] != "test" and any(r.startswith("test:") for r in refs_):
                problems.append(f"{where}: test entries belong to test items")
            reasons = [x for x in (resolves(r, reqs, self.repo.exists, self.repo.read, self.repo.tests,
                                            self.repo.unpublished) for r in refs_) if x]
            tasks = refs(e["Task"])
            for t in tasks:
                if t not in self.repo.tasks:
                    problems.append(f"{where}: task {t} not in doc/todo.yaml")
            if status == "missing":
                if refs_ and not reasons:
                    problems.append(f"{where}: marked missing, but every reference resolves")
                if e["Since"] != "—":
                    problems.append(f"{where}: a missing item has no 'since'")
            else:
                if not refs_:
                    problems.append(f"{where}: {status} without a reference")
                problems += [f"{where}: {x}" for x in reasons]
                if e["Since"] not in since_values:
                    problems.append(f"{where}: since '{e['Since']}' is no increment of section 7")
            if status in ("partial", "missing"):
                if not any(self.open_task(t) for t in tasks):
                    problems.append(f"{where}: {status}, but no open task carries it (update the case)")
            elif tasks:
                problems.append(f"{where}: available, but names a task")
        return problems

    def since_problems(self, tree: TagTree) -> list:
        """Items since the tag resolve in its tree, items since `next` do not (needs the tag in the clone)."""
        problems = []
        tests = tree.tests()

        def old(path: str) -> str:
            return path if tree.exists(path) else MOVED.get(path, path)

        for e in self.evidence:
            if e["Status"] == "missing":
                continue
            reqs = refs(e["Requirements"])
            ok = all(not resolves(r, reqs, lambda p: tree.exists(old(p)), lambda p: tree.read(old(p)), tests)
                     for r in references(e["Reference"]))
            if e["Since"] == tree.tag and not ok:
                problems.append(f"{e['ID']}: since {tree.tag}, but does not resolve in that tag")
            # DOC-84: a later increment (a release candidate after its tag, or `next`) is newer than the tag as well
            if e["Since"] not in (tree.tag, "—") and ok:
                problems.append(f"{e['ID']}: since {e['Since']}, but already resolves in {tree.tag}")
        return problems

    def increment_problems(self, tree=None, commit_of=None, version_text: str = None) -> list:
        """`tree`: the tag tree of v0.1.0 (its row must name its commit). `commit_of(tag)`: the commit a tag points
        at, None while it does not exist - every increment whose tag exists must name that commit, wherever the row
        was written (TOOL-232). `version_text`: VERSION; no increment may be newer."""
        problems = []
        names = [i["Baseline"] for i in self.increments]
        if not names or names[-1] != "next":
            problems.append("the last increment must be the working state 'next'")
        for inc in self.increments:
            tagged = inc["Baseline"] != "next"
            if tagged and not re.fullmatch(TAG, inc["Baseline"]):
                problems.append(f"{inc['Increment']}: baseline '{inc['Baseline']}' is no tag vX.Y.Z or vX.Y.Z-rcN")
                tagged = False
            if not re.fullmatch(r"[0-9a-f]{7,40}", inc["Commit"]):
                problems.append(f"{inc['Increment']}: commit '{inc['Commit']}' is no hash")
            if tree is not None and inc["Baseline"] == tree.tag and not tree.commit().startswith(inc["Commit"]):
                problems.append(f"{inc['Increment']}: {tree.tag} is not commit {inc['Commit']}")
            if tagged and version_text and version(inc["Baseline"]) > version(f"v{version_text}"):
                problems.append(f"{inc['Increment']}: baseline {inc['Baseline']} is newer than VERSION {version_text}")
            full = commit_of(inc["Baseline"]) if tagged and commit_of else None
            if full and not full.startswith(inc["Commit"]):
                problems.append(f"{inc['Increment']}: {inc['Baseline']} is commit {full[:12]}, the row names "
                                f"{inc['Commit']} (the row names the commit of the tag, section 7 rule 1)")
        header = self.text.split("\n---", 1)[0]
        work = next((i for i in self.increments if i["Baseline"] == "next"), None)
        releases = [n for n in names if n != "next"]
        baseline = re.search(rf"^Baseline: ({TAG})$", header, flags=re.MULTILINE)
        working = re.search(rf"^Working state: dev at ([0-9a-f]+) \(after ({TAG})\)$", header,
                            flags=re.MULTILINE)
        if working and work:
            if working.group(1) != work["Commit"]:
                problems.append("header working state and increment 'next' name different commits")
            named = working.group(2)
            skipped = named in WITHOUT_INCREMENT and releases and version(named) > version(releases[-1])
            if releases and named != releases[-1] and not skipped:
                problems.append("header names another release than the last increment")
        elif not baseline:
            problems.append("header has neither 'Baseline: vX.Y.Z' nor 'Working state: dev at <commit> (after vX.Y.Z)'")
        return problems

    # ---- claims and arguments
    def claim_problems(self) -> list:
        problems = []
        goals = self.repo.goals
        ids = [c["ID"] for c in self.claims]
        claimed = {a["Claim"] for a in self.arguments}
        for c in self.claims:
            where = c["ID"]
            if c["Kind"] == "product":
                goal = goals.get(c["Source"])
                if goal is None:
                    problems.append(f"{where}: source {c['Source']} is no assumed safety goal of goals.sdoc")
                elif goal[0] != c["Claim"]:
                    problems.append(f"{where}: text differs from {c['Source']} in goals.sdoc")
            elif c["Kind"] == "process":
                if c["Claim"] != CLAIM:
                    problems.append(f"{where}: text differs from the claim of the safety plan")
            elif c["Kind"] not in ("top", "context"):
                problems.append(f"{where}: kind '{c['Kind']}' unknown")
            if where not in claimed:
                problems.append(f"{where}: no argument")
        missing_goals = set(goals) - {c["Source"] for c in self.claims if c["Kind"] == "product"}
        problems += [f"assumed safety goal {g} has no claim" for g in sorted(missing_goals)]
        for a in self.arguments:
            if a["Claim"] not in ids:
                problems.append(f"{a['ID']}: claim {a['Claim']} not defined")
        if [c["Kind"] for c in self.claims].count("top") != 1:
            problems.append("exactly one top claim expected")
        return problems

    def _claim(self, cid):
        return next((c for c in self.claims if c["ID"] == cid), None)

    def argument_problems(self) -> list:
        problems = []
        ids = [a["ID"] for a in self.arguments]
        problems += [f"duplicate argument ID {i}" for i in sorted({i for i in ids if ids.count(i) > 1})]
        deficits = {d["ID"]: d for d in self.deficits}
        reqs = {q["id"]: q for q in self.repo.requirements}
        goals = self.repo.goals
        verdict_of = {}
        for a in self.arguments:
            where = a["ID"]
            verdict = a["Verdict"]
            if verdict not in VERDICTS:
                problems.append(f"{where}: verdict '{verdict}' not in {VERDICTS}")
                continue
            cited = refs(a["Evidence"])
            for e in cited:
                if e not in self.by_id:
                    problems.append(f"{where}: evidence {e} not defined")
            for d in refs(a["Deficits"]):
                if d not in deficits:
                    problems.append(f"{where}: deficit {d} not defined")
                elif a["Claim"] not in refs(deficits[d]["Affects"]):
                    problems.append(f"{where}: deficit {d} does not affect {a['Claim']}")
            if verdict == "supported":
                if refs(a["Deficits"]):
                    problems.append(f"{where}: supported, but with deficits")
                if any(self.by_id.get(e, {}).get("Status") != "available" for e in cited):
                    problems.append(f"{where}: supported, but not all evidence available")
            claim = self._claim(a["Claim"])
            if claim is None:
                continue
            basis = refs(a["Basis"])
            if claim["Kind"] == "product":
                goal = claim["Source"]
                expected = {q["id"] for q in self.repo.requirements if goal in q["derived_from"]}
                problems += [f"{where}: {r} is derived from {goal} but not in the basis"
                             for r in sorted(expected - set(basis))]
                problems += [f"{where}: {r} is not derived from {goal}" for r in sorted(set(basis) - expected)]
                if goal in goals:
                    allowed = COVERAGE.get(goals[goal][1])
                    if allowed is None:
                        problems.append(f"{where}: coverage '{goals[goal][1]}' of {goal} unknown to the test")
                    elif verdict != allowed:
                        problems.append(f"{where}: verdict '{verdict}', the coverage of {goal} in goals.sdoc "
                                        f"gives '{allowed}'")
                problems += self._evidence_of_basis(where, basis, cited)
            elif claim["Kind"] == "context":
                expected = {x for q in self.repo.requirements for x in q["derived_from"] if x.startswith("AoU-")}
                problems += [f"{where}: {x} is cited by a requirement but not in the basis"
                             for x in sorted(expected - set(basis))]
                problems += [f"{where}: {x} is cited by no requirement" for x in sorted(set(basis) - expected)]
                problems += [f"{where}: {x} is defined nowhere" for x in basis
                             if not re.search(rf"\b{x}\b", self.repo.aou_text)]
                problems += self._evidence_of_basis(where, basis, cited)
            elif claim["Kind"] == "process":
                pass    # checked in process_problems
            for r in basis:
                if r.startswith("SSR-") and r not in reqs:
                    problems.append(f"{where}: {r} not in ssr.sdoc")
            verdict_of.setdefault(a["Claim"], []).append(verdict)
        # the top claim is never stronger than its weakest sub-claim
        top = next((a for a in self.arguments if (self._claim(a["Claim"]) or {}).get("Kind") == "top"), None)
        if top is not None:
            subs = refs(top["Basis"])
            expected = [c["ID"] for c in self.claims if c["Kind"] != "top"]
            if sorted(subs) != sorted(expected):
                problems.append(f"{top['ID']}: basis must be every other claim")
            weakest = min((strength(v) for c in subs for v in verdict_of.get(c, [])), default=None)
            if weakest is not None and top["Verdict"] in VERDICTS and strength(top["Verdict"]) > weakest:
                problems.append(f"{top['ID']}: verdict stronger than its weakest sub-claim")
        return problems

    def _evidence_of_basis(self, where: str, basis: list, cited: list) -> list:
        """Every test and board record of the basis is cited; every SSR has its ET row."""
        problems = []
        for e in self.evidence:
            if e["Kind"] in ("test", "board record") and set(refs(e["Requirements"])) & set(basis):
                if e["ID"] not in cited:
                    problems.append(f"{where}: {e['ID']} supports the basis but is not cited")
        for r in basis:
            if r.startswith("SSR-") and f"ET-{r[4:]}" not in self.by_id:
                problems.append(f"{where}: {r} has no test evidence row ET-{r[4:]}")
        return problems

    def test_evidence_problems(self) -> list:
        """One ET row per SSR, with the SSR, and with at least one test unless the SSR is open."""
        problems = []
        reqs = {q["id"]: q for q in self.repo.requirements}
        tests = [e for e in self.evidence if e["Kind"] == "test"]
        for e in tests:
            ssr = f"SSR-{e['ID'][3:]}"
            if refs(e["Requirements"]) != [ssr]:
                problems.append(f"{e['ID']}: requirements must be exactly {ssr}")
            if ssr not in reqs:
                problems.append(f"{e['ID']}: {ssr} not in ssr.sdoc")
            elif reqs[ssr]["status"] == "open":
                if e["Status"] != "missing":
                    problems.append(f"{e['ID']}: {ssr} is open, the evidence must be missing")
            elif e["Status"] == "missing" or not references(e["Reference"]):
                problems.append(f"{e['ID']}: {ssr} is {reqs[ssr]['status']}, but no test is cited")
        have = {f"SSR-{e['ID'][3:]}" for e in tests}
        problems += [f"{r} has no test evidence row" for r in sorted(set(reqs) - have)]
        return problems

    def process_problems(self) -> list:
        problems = []
        plan = {a["ID"]: a for a in self.repo.activities}
        seen = []
        for a in self.process:
            where = a["ID"]
            basis = refs(a["Basis"])
            seen += basis
            cited_refs = set()
            for e in refs(a["Evidence"]):
                for r in references(self.by_id.get(e, {}).get("Reference", "")):
                    cited_refs.add(r.partition("::")[0])
            for act in basis:
                if act not in plan:
                    problems.append(f"{where}: activity {act} not in the safety plan")
                    continue
                for p in paths(plan[act]["Work product"]):
                    if p not in cited_refs:
                        problems.append(f"{where}: work product {p} of {act} is cited by no evidence")
            statuses = {plan[x]["Status"] for x in basis if x in plan}
            if a["Verdict"] == "supported" and statuses - {"done", "tailored"}:
                problems.append(f"{where}: supported, but activities are not done")
        problems += [f"activity {x} argued more than once" for x in sorted({x for x in seen if seen.count(x) > 1})]
        problems += [f"activity {x} of the safety plan is not argued" for x in sorted(set(plan) - set(seen))]
        return problems

    def deficit_problems(self) -> list:
        problems = []
        cited = {}
        for a in self.arguments:
            for d in refs(a["Deficits"]):
                cited.setdefault(d, set()).add(a["Claim"])
        claim_ids = {c["ID"] for c in self.claims}
        for d in self.deficits:
            where = d["ID"]
            for c in refs(d["Affects"]):
                if c not in claim_ids:
                    problems.append(f"{where}: claim {c} not defined")
                elif c not in cited.get(where, set()):
                    problems.append(f"{where}: affects {c}, but no argument of {c} cites it")
            tasks = refs(d["Task"])
            if not tasks:
                problems.append(f"{where}: no task")
            for t in tasks:
                if re.fullmatch(r"T-\d\d", t):
                    if t not in self.repo.tailoring:
                        problems.append(f"{where}: tailoring {t} not in the safety plan")
                elif t not in self.repo.tasks:
                    problems.append(f"{where}: task {t} not in doc/todo.yaml")
            if not any(self.open_task(t) or t in self.repo.tailoring for t in tasks):
                problems.append(f"{where}: no open task or tailoring carries it (update the case)")
        return problems

    def usage_problems(self) -> list:
        cited = {e for a in self.arguments for e in refs(a["Evidence"])}
        return [f"{e['ID']}: cited by no argument" for e in self.evidence if e["ID"] not in cited]

    def summary_problems(self) -> list:
        problems = []
        result = self.text.split("## 0. Result in one page", 1)[1].split("\n## 1.", 1)[0]
        kinds = {k: sum(1 for e in self.evidence if e["Kind"] == k) for k in KINDS}
        status = {s: sum(1 for e in self.evidence if e["Status"] == s) for s in STATUS}
        product = [a["Verdict"] for a in self.product
                   if (self._claim(a["Claim"]) or {}).get("Kind") == "product"]
        process = [a["Verdict"] for a in self.process]
        kind_text = ", ".join(f"{kinds[k]} {k}" for k in sorted(KINDS, key=lambda k: -kinds[k]) if kinds[k])
        # the items already present in the first increment (v0.1.0); DOC-84: the items of a later increment (Release
        # R1 after its tag) are not counted here
        first = self.increments[0]["Baseline"] if self.increments else None
        in_tag = sum(1 for e in self.evidence if e["Since"] == first)
        expected = [
            f"{len(self.claims)} claims",
            "Verdicts: " + ", ".join(f"{product.count(v)} {v}" for v in VERDICTS),
            f"over the {len(self.repo.activities)} activities of the safety plan",
            "Verdicts: " + ", ".join(f"{process.count(v)} {v}" for v in VERDICTS if process.count(v)),
            f"**{len(self.evidence)}** items: {kind_text}",
            f"Status: {status['available']} available, {status['partial']} partial, {status['missing']} missing",
            f"**{len(self.deficits)}** assurance deficits",
            f"{in_tag} of today's evidence items already present",
        ]
        problems += [f"section 0 does not say: {e}" for e in expected if e not in result]
        increment = next((i for i in self.increments if i["Baseline"] == "next"), None)
        if increment:
            text = (f"{len(self.claims)} claims, {len(self.arguments)} arguments, {len(self.evidence)} evidence items, "
                    f"{len(self.deficits)} deficits")
            if text not in increment["Change to the case"]:
                problems.append(f"increment 'next' does not say: {text}")
        return problems

    def all_problems(self) -> list:
        return (self.evidence_problems() + self.increment_problems() + self.claim_problems()
                + self.argument_problems() + self.test_evidence_problems() + self.process_problems()
                + self.deficit_problems() + self.usage_problems() + self.summary_problems()
                + licence_problems(self.text))


class SafetyCaseConsistency(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DOC.read_text(encoding="utf-8")
        cls.repo = Repo()
        cls.case = Case(cls.text, cls.repo)
        cls.tree = TagTree("v0.1.0")

    def case_of(self, text=None, **repo_changes):
        repo = self.repo
        if repo_changes:
            repo = Repo.__new__(Repo)
            repo.__dict__.update(self.repo.__dict__)
            repo.__dict__.update(repo_changes)
        return Case(self.text if text is None else text, repo)

    def test_every_evidence_link_resolves(self):
        self.assertEqual(self.case.evidence_problems(), [])

    def test_increments(self):
        version_text = (REPO / "VERSION").read_text(encoding="utf-8").strip()
        self.assertEqual(self.case.increment_problems(self.tree if self.tree.available else None, TagTree.commit_of,
                                                      version_text), [])

    def test_since_against_the_tag(self):
        if not self.tree.available:
            self.skipTest("tag v0.1.0 not in this clone (git fetch origin tag v0.1.0)")
        self.assertEqual(self.case.since_problems(self.tree), [])

    def test_claims(self):
        self.assertEqual(self.case.claim_problems(), [])

    def test_arguments(self):
        self.assertEqual(self.case.argument_problems(), [])

    def test_test_evidence_per_requirement(self):
        self.assertEqual(self.case.test_evidence_problems(), [])

    def test_process_argument(self):
        self.assertEqual(self.case.process_problems(), [])

    def test_deficits(self):
        self.assertEqual(self.case.deficit_problems(), [])

    def test_every_evidence_is_used(self):
        self.assertEqual(self.case.usage_problems(), [])

    def test_summary(self):
        self.assertEqual(self.case.summary_problems(), [])

    def test_standard_cited_by_part_and_clause_only(self):
        self.assertEqual(licence_problems(self.text), [])

    # Counter-checks: every rule must see a broken document or a changed repository
    def test_counter_checks(self):
        def sub(source, old, new, count=1):
            """str.replace that fails when the edit does not hit, so no counter-check passes vacuously."""
            self.assertIn(old, source)
            return source.replace(old, new, count)

        def row(prefix):
            return next(line for line in self.text.splitlines() if line.startswith(prefix))

        text = self.text
        # evidence links: missing path, text anchor not found, test entry unknown, test entry without the SSR
        broken = sub(text, "`doc/safety/traceability.md` |", "`doc/safety/traceability-old.md` |")
        self.assertTrue(any("traceability-old.md does not exist" in p
                            for p in self.case_of(broken).evidence_problems()))
        # (the text and capture checks against the development repository's view: in the export todo.yaml is cleaned
        # and the captures are not published - DOC-85, checked below)
        nothing = type(self.repo.unpublished)()
        broken = sub(text, "`doc/todo.yaml::iso-q4`", "`doc/todo.yaml::iso-q99`")
        self.assertTrue(any("iso-q99" in p for p in self.case_of(broken, unpublished=nothing).evidence_problems()))
        broken = sub(text, "`test:safe-watchdog` | SSR-013 |", "`test:safe-watchdog-old` | SSR-013 |")
        self.assertTrue(any("safe-watchdog-old" in p for p in self.case_of(broken).evidence_problems()))
        broken = sub(text, "`test:safe-watchdog` | SSR-013 |", "`test:safe-system` | SSR-013 |")
        self.assertTrue(any("does not name SSR-013" in p for p in self.case_of(broken).evidence_problems()))
        tests = dict(self.repo.tests, **{"safety-cycle": set()})
        self.assertTrue(any("safety-cycle does not name SSR-014" in p
                            for p in self.case_of(tests=tests).evidence_problems()))
        # a board record whose capture file is gone
        broken = sub(text, "20_sys39_romfail.console.txt", "20_sys39_romfail_lost.console.txt")
        self.assertTrue(any("romfail_lost" in p for p in self.case_of(broken, unpublished=nothing)
                            .evidence_problems()))
        # DOC-85: what the export lists as left out on purpose resolves, anything unlisted does not
        Listed = type(self.repo.unpublished)
        listed = Listed(files=frozenset({"doc/safety/gone-analysis.md"}), folders=frozenset({"tools/gone"}),
                        cleaned=frozenset({"doc/todo.yaml"}), tests=frozenset({"gone-test"}))
        no_tests = {}
        for ref in ("doc/safety/gone-analysis.md", "doc/safety/gone-analysis.md::any text", "tools/gone/x/y.txt",
                    "doc/todo.yaml::iso-q99", "test:gone-test"):
            self.assertEqual(resolves(ref, ["SSR-001"], lambda p: listed.file(p) or p == "doc/todo.yaml",
                                      lambda p: "" if p == "doc/todo.yaml" else None, no_tests, listed), "", ref)
            self.assertTrue(resolves(ref, ["SSR-001"], lambda p: p == "doc/todo.yaml",
                                     lambda p: "" if p == "doc/todo.yaml" else None, no_tests, Listed()), ref)
        for ref in ("doc/safety/other.md", "tools/other/x.txt", "test:other-test"):
            self.assertTrue(resolves(ref, [], listed.file, lambda p: None, no_tests, listed), ref)
        self.assertEqual(load_tasks(Listed(tasks={"SYS-99999": "done"}))["SYS-99999"], "done")
        # status rules: available with a missing file, missing although present, carrier task done, no task
        broken = sub(text, "`doc/safety/verification-plan.md` | — | v1.0.0-rc1 | available | — |",
                     "`doc/safety/verification-plan-lost.md` | — | v1.0.0-rc1 | available | — |")
        self.assertTrue(self.case_of(broken).evidence_problems())
        broken = sub(text, "`tools/ci/versions.toml` | — | v0.1.0 | available | — |",
                     "`tools/ci/versions.toml` | — | — | missing | SYS-67 |")
        self.assertTrue(any("every reference resolves" in p for p in self.case_of(broken).evidence_problems()))
        # DOC-38: EW-05 (safety manual) is available since its acceptance; EW-35 and D-03 carry the rule now
        # (EW-04 is available since the release of 2026-10-02)
        tasks = dict(self.repo.tasks, **{"SYS-183": "done", "SYS-30": "done"})
        self.assertTrue(any("EW-35" in p for p in self.case_of(tasks=tasks).evidence_problems()))
        self.assertTrue(any("D-03" in p for p in self.case_of(tasks=tasks).deficit_problems()))
        broken = sub(text, "| partial | PLAT-91 |", "| partial | — |")
        self.assertTrue(self.case_of(broken).evidence_problems())
        broken = sub(text, "| v0.1.0 | available | — |", "| v0.2.0 | available | — |")
        self.assertTrue(any("no increment" in p for p in self.case_of(broken).evidence_problems()))
        self.assertTrue(self.case_of(sub(text, "| decision | `doc/todo.yaml::iso-q4`", "| verdict | "
                                                 "`doc/todo.yaml::iso-q4`")).evidence_problems())
        # since: an item marked v0.1.0 that is new, and one marked next that is old (needs the tag)
        if self.tree.available:
            broken = sub(text, "`doc/safety/cm-plan.md` | — | v1.0.0-rc1 |", "`doc/safety/cm-plan.md` | — | v0.1.0 |")
            self.assertTrue(any("EW-09" in p for p in self.case_of(broken).since_problems(self.tree)))
            broken = sub(text, "`tools/ci/versions.toml` | — | v0.1.0 |", "`tools/ci/versions.toml` | — | next |")
            self.assertTrue(any("EW-13" in p for p in self.case_of(broken).since_problems(self.tree)))
            # DOC-84: an item of the later increment v1.0.0-rc1 that is old
            broken = sub(text, "`tools/ci/versions.toml` | — | v0.1.0 |", "`tools/ci/versions.toml` | — | v1.0.0-rc1 |")
            self.assertTrue(any("EW-13" in p and "since v1.0.0-rc1" in p
                                for p in self.case_of(broken).since_problems(self.tree)))
            broken = sub(text, "| v0.1.0 | ed48460 |", "| v0.1.0 | 20b2e04 |")
            self.assertTrue(self.case_of(broken).increment_problems(self.tree))
        # increments and header
        # the state line of the header, whichever form it has (working state between releases, baseline on a
        # release candidate, REL-020)
        state = re.search(r"^(?:Working state: dev at \S+ \(after v\S+\)|Baseline: v\S+)$", text, flags=re.MULTILINE)
        self.assertIsNotNone(state, "safety-case.md carries no state line")
        self.assertTrue(self.case_of(sub(text, state.group(0), "Working state: somewhere")).increment_problems())
        working = re.search(r"\| I-\d+ \| next \|", text)   # the row of the working state (I-2 since DOC-84)
        self.assertIsNotNone(working, "safety-case.md has no increment row 'next'")
        self.assertTrue(self.case_of(sub(text, working.group(0), working.group(0).replace("next", "v0.2")))
                        .increment_problems())
        # DOC-79: v0.2.0 may be named without an increment (CM-05 Z5 a), another release may not; once an increment
        # of a later release candidate exists, the header must name that one; a candidate tag is a valid baseline
        if "(after v0.2.0)" in state.group(0):
            self.assertTrue(self.case_of(sub(text, "(after v0.2.0)", "(after v0.3.0)")).increment_problems())
            later = sub(text, "| I-1 | next |", "| I-1 | v1.0.0-rc1 |")
            later = sub(later, "<!-- case-increments:end -->",
                        "| I-2 | next | 2c065567 | working state | x |\n<!-- case-increments:end -->")
            self.assertTrue(any("another release" in p for p in self.case_of(later).increment_problems()))
            self.assertFalse(any("v1.0.0-rc1" in p for p in self.case_of(later).increment_problems()))
        # TOOL-232: the increment of Release R1 after its tag, as the documentation commit after the tag writes it -
        # I-1 gets the tag and the commit of the tag, a new row `next` opens, the header follows (section 7 rule 1)
        tag_commit = "1234567890abcdef1234567890abcdef12345678"
        if re.search(r"\| I-1 \| next \|", text):
            # before the documentation commit after the tag: build the rows
            r1 = re.sub(r"\| I-1 \| next \| [0-9a-f]+ \|", "| I-1 | v1.0.0-rc1 | 12345678 |", text)
            r1 = sub(r1, "<!-- case-increments:end -->",
                     "| I-2 | next | 12345678 | working state | x |\n<!-- case-increments:end -->")
        else:
            # DOC-84: the rows exist since the documentation commit after the tag; only their commits are replaced
            r1 = re.sub(r"\| I-1 \| v1\.0\.0-rc1 \| [0-9a-f]+ \|", "| I-1 | v1.0.0-rc1 | 12345678 |", text)
            r1 = re.sub(r"\| I-2 \| next \| [0-9a-f]+ \|", "| I-2 | next | 12345678 |", r1)
        self.assertEqual(r1.count("| 12345678 |"), 2,
                         "safety-case.md: rows I-1 (v1.0.0-rc1 or next) and I-2 not found")
        tags = {"v0.1.0": None, "v1.0.0-rc1": tag_commit}
        for header in ("Baseline: v1.0.0-rc1", "Working state: dev at 12345678 (after v1.0.0-rc1)"):
            with self.subTest(header=header):
                after = sub(r1, state.group(0), header)
                self.assertEqual(self.case_of(after).increment_problems(None, tags.get, "1.0.0"), [])
                # before the tag exists the row is not compared with anything
                self.assertEqual(self.case_of(after).increment_problems(None, lambda t: None, "1.0.0"), [])
                # counter-checks: another commit than the tag's, a malformed candidate, another version base
                wrong = {"v1.0.0-rc1": "abcdef0" + tag_commit[7:]}
                self.assertTrue(any("I-1" in p and "commit of the tag" in p
                                    for p in self.case_of(after).increment_problems(None, wrong.get, "1.0.0")))
                for bad in ("v1.0.0-rc", "v1.0.0-beta1", "v1.0.0-rc0"):
                    broken = sub(after, "| I-1 | v1.0.0-rc1 |", f"| I-1 | {bad} |")
                    self.assertTrue(any("I-1" in p and "no tag" in p
                                        for p in self.case_of(broken).increment_problems(None, tags.get, "1.0.0")),
                                    bad)
                broken = sub(after, "| I-1 | v1.0.0-rc1 |", "| I-1 | v1.0.1-rc1 |")
                self.assertTrue(any("newer than VERSION" in p
                                    for p in self.case_of(broken).increment_problems(None, tags.get, "1.0.0")))
        # claims: text drift from concept and plan, a goal without claim
        self.assertTrue(self.case_of(sub(text, "Cell balancing shall not run unintentionally |",
                                         "Cell balancing shall not run |")).claim_problems())
        self.assertTrue(self.case_of(sub(text, "without the management part. | iso-q2 |",
                                         "including part 2. | iso-q2 |")).claim_problems())
        broken = sub(text, row("| C-04 |") + "\n", "")
        self.assertTrue(any("ASG-04 has no claim" in p for p in self.case_of(broken).claim_problems()))
        # product argument: SSR missing from the basis, foreign SSR, verdict stronger than the concept allows,
        # a board record of the basis not cited, supported with deficits
        broken = sub(text, "| SSR-063, SSR-064 | EW-08,", "| SSR-064 | EW-08,")
        self.assertTrue(any("SSR-063 is derived from ASG-04" in p for p in self.case_of(broken).argument_problems()))
        broken = sub(text, "| SSR-063, SSR-064 | EW-08,", "| SSR-063, SSR-064, SSR-001 | EW-08,")
        self.assertTrue(any("SSR-001 is not derived" in p for p in self.case_of(broken).argument_problems()))
        broken = sub(text, "| D-06, D-07 | not supported |", "| D-06, D-07 | partly supported |")
        self.assertTrue(any("in goals.sdoc gives" in p for p in self.case_of(broken).argument_problems()))
        broken = sub(text, "ET-083, EB-01, EB-04, EB-07, EB-08 |", "ET-083, EB-01, EB-04, EB-07 |")
        self.assertTrue(any("EB-08 supports the basis" in p for p in self.case_of(broken).argument_problems()))
        broken = sub(text, "| D-05, D-15 | supported with limits |", "| D-05, D-15 | supported |")
        self.assertTrue(any("supported, but with deficits" in p for p in self.case_of(broken).argument_problems()))
        # a new requirement derived from a goal must show up in its argument and get its ET row
        reqs = self.repo.requirements + [{"id": "SSR-099", "derived_from": ["ASG-05"], "status": "implemented"}]
        case = self.case_of(requirements=reqs)
        self.assertTrue(any("SSR-099" in p for p in case.argument_problems()))
        self.assertTrue(any("SSR-099" in p for p in case.test_evidence_problems()))
        # an open requirement with test evidence marked available, an implemented one without tests
        broken = sub(text, "| — | SSR-061 | — | missing | SYS-31 |", "| `test:safe-state` | SSR-061 | v0.1.0 | "
                                                                      "available | — |")
        self.assertTrue(self.case_of(broken).test_evidence_problems())
        # context argument: AoU missing, AoU cited by no requirement
        broken = sub(text, "AoU-24, AoU-25, AoU-30, AoU-32 |", "AoU-24, AoU-30, AoU-32 |")
        self.assertTrue(any("AoU-25" in p for p in self.case_of(broken).argument_problems()))
        broken = sub(text, "AoU-24, AoU-25, AoU-30, AoU-32 |", "AoU-24, AoU-25, AoU-30, AoU-32, AoU-01 |")
        self.assertTrue(any("AoU-01 is cited by no requirement" in p for p in self.case_of(broken).argument_problems()))
        # top claim stronger than a sub-claim, top basis incomplete
        broken = sub(text, "| D-01, D-02, D-21, D-22 | not supported |", "| D-01, D-02, D-21, D-22 | partly supported |")
        self.assertTrue(any("weakest" in p for p in self.case_of(broken).argument_problems()))
        # process argument: activity not argued, argued twice, work product not cited, supported with open work
        broken = sub(text, "P9-01, P9-02, P9-03, P9-04 |", "P9-01, P9-02, P9-03 |")
        self.assertTrue(any("P9-04" in p for p in self.case_of(broken).process_problems()))
        broken = sub(text, "P9-01, P9-02, P9-03, P9-04 |", "P9-01, P9-02, P9-03, P9-04, P2-01 |")
        self.assertTrue(any("more than once" in p for p in self.case_of(broken).process_problems()))
        broken = sub(text, "| EW-20, EW-21, EW-22 |", "| EW-20, EW-21 |")
        self.assertTrue(any("dfa.md" in p for p in self.case_of(broken).process_problems()))
        broken = sub(text, "| D-09, D-17 | partly supported |", "| D-09, D-17 | supported |")
        self.assertTrue(any("activities are not done" in p for p in self.case_of(broken).process_problems()))
        # deficits: affects a claim whose argument does not cite it, unknown task, tailoring unknown
        broken = sub(text, "| C-09 | T-03 |", "| C-06, C-09 | T-03 |")
        self.assertTrue(any("D-14: affects C-06" in p for p in self.case_of(broken).deficit_problems()))
        # (DOC-59: D-17 names DOC-56 beside SYS-118 since the re-check of CM-02, D17 a)
        self.assertTrue(self.case_of(sub(text, "| C-06, C-07, C-09 | SYS-118, DOC-56 |",
                                         "| C-06, C-07, C-09 | SYS-999 |")).deficit_problems())
        self.assertTrue(self.case_of(sub(text, "| C-09 | T-03 |", "| C-09 | T-33 |")).deficit_problems())
        broken = sub(text, "| D-15, D-17 | partly supported |", "| D-15, D-17, D-99 | partly "
                                                                   "supported |")
        self.assertTrue(any("D-99 not defined" in p for p in self.case_of(broken).argument_problems()))
        # evidence defined but cited by nobody, cited but not defined
        broken = sub(text, "| EW-20, EW-21, EW-22 |", "| EW-20, EW-21, EW-22, EW-99 |")
        self.assertTrue(any("EW-99 not defined" in p for p in self.case_of(broken).argument_problems()))
        broken = sub(text, "EW-32, EW-33, EW-34, ER-01, ER-02 |", "EW-32, EW-33, EW-34, ER-01 |")
        self.assertTrue(any("ER-02: cited by no argument" in p for p in self.case_of(broken).usage_problems()))
        # summary numbers
        # DOC-46: 20 deficits since D-22 (R1 deviation); before 19 since D-13 was removed (PLAT-79 done)
        self.assertTrue(self.case_of(sub(text, "**20** assurance deficits", "**21** assurance deficits"))
                        .summary_problems())
        broken = sub(text, "`tools/ci/versions.toml` | — | v0.1.0 | available | — |",
                     "`tools/ci/versions.toml` | — | v0.1.0 | partial | SYS-67 |")
        self.assertTrue(self.case_of(broken).summary_problems())
        # licence rule
        self.assertTrue(licence_problems(text + "\nsee ISO 26262-6, Table 10\n"))
        # malformed tables
        with self.assertRaises(ValueError):
            self.case_of(sub(text, "<!-- case-deficits:start -->", ""))
        with self.assertRaises(ValueError):
            self.case_of(sub(text, "| ID | Kind | Claim | Source |", "| ID | Kind | Goal | Source |"))
        self.assertTrue(_cells("| a | b |") == ["a", "b"] and block(text, "case-claims"))


if __name__ == "__main__":
    sys.exit(unittest.main())
