"""DOC-19: consistency of the safety manual doc/safety/safety-manual.md with its sources.

Checks
- assumptions of use: every AoU of assumptions.sdoc (status concept or proposed), of tool-classification.md section 6,
  of config-range.md section 5 (AoU-CFG-n) and of security-interplay.md section 4 (AoU-SEC-n) has exactly one row in
  the manual; every row names a source and a status out of concept / proposed / new here; `concept` exactly for the
  AoUs of the concept (also when another source proposed them first, DOC-38); a row that is in none of the sources is `new here`; the AoUs announced in
  partner-and-degraded.md for mechanisms that do not exist yet (AoU-31) are not in the tables of obligations
  but in section 7.6; AoU-33 of partner-and-degraded.md 7.1 is an obligation since CM-10 (CR-10-1 F-03, DOC-49);
- mechanisms: every SM of the catalogue (mechanisms.sdoc) has a row; exactly the device-dependent, optional and
  stage-2 mechanisms are marked not credited; every credited mechanism has its section in fmea.md; DOC-50 (CM-10
  F-02): the column "Clause; neXtBSW rating" is "—" or names neXtBSW's own rating E1 ... E3, never a coverage class
  (low / medium / high, "typical") taken from the standard;
- requirements: every SSR of ssr.sdoc that is not `open` is allocated to an assumed technical safety
  requirement of section 3.2, every `open` one is named as not allocated, no unknown SSR;
- assumed safety goals of section 3.1 equal those of goals.sdoc;
  (DOC-21 step 3: SSR, goals, assumptions of use and mechanisms are StrictDoc nodes in doc/requirements/safety, read
  through tools/trace and StrictDoc's JSON export, instead of requirements.yaml and the tables of concept.md 2.2, 4, 6
  and fmea.md 6)
- tools: the table of section 9.1 equals the tool summary of tool-classification.md (tool, TCL today, TCL target);
- tailoring: the IDs of section 12 equal T-01 ... of safety-plan.md section 3;
- counts of section 0; references to config-range.md and security-interplay.md (proposals C4 and S4); every relative
  link resolves; licence rule for ISO 26262 (part and clause only);
- DOC-47: every AoU proposal of dfa.md section 9 and architecture-safety-analysis.md section 7.1 is a row of section 7
  or a row of the covered table 7.7 that names at least one existing row; an AoU of concept section 4 whose text says
  "open for acceptance" reads `proposed`, except AoU-37 ... AoU-39, made binding by CM-10 (Z4, DOC-49) while the
  words may still stand in the source (DOC-48 removes them);
- DOC-47, only in a git repository with the history (skipped in the open export without .git or without history,
  PLAT-62): the state line says `Working state` once the manual changed after the tag it names as baseline, and a
  working state names the newest tag; every commit since the acceptance of issue 1 (DOC-38, c44f94ad) that changed
  the manual has a row in section 16 (by its short hash or the task ID that leads its subject); every AoU row that was
  not in the accepted issue ends its source with "added after acceptance", and no row of the accepted issue does.
Counter-checks with deliberately broken copies show that each rule sees its fault.

Run: .venv/Scripts/python -m unittest doc/safety/test/test_safety_manual.py
"""

import copy
import pathlib
import re
import shutil
import subprocess
import sys
import unittest

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_safety_plan import block, licence_problems, table  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[3]
SAFETY = REPO / "doc" / "safety"
sys.path.insert(0, str(REPO / "tools" / "trace"))
import tracecheck  # noqa: E402  (StrictDoc model of the SSR and the safety catalogue, DOC-21)
DOC = SAFETY / "safety-manual.md"

AOU_COLUMNS = ("ID", "The integrator's obligation", "How to fulfil it", "Source", "Status")
SEC_COLUMNS = ("ID", "The integrator's obligation", "Source")
SM_COLUMNS = ("ID", "Mechanism", "Credited", "Guaranteed detection (nominal)", "Reaction", "FHTI", "Class",
              "Clause; neXtBSW rating (gap section 3)", "Latent faults of the mechanism")
RATING_COLUMN = SM_COLUMNS[7]
# DOC-50 (CM-10 F-02): the column carries a clause and neXtBSW's own rating E1 ... E3, no class of the standard
RATING = re.compile(r"\*\*E[123]\*\*")
STANDARD_CLASS = re.compile(r"\btypical\b|\b(?:low|medium|high)\b", re.IGNORECASE)
TOOL_COLUMNS = ("Tool", "TCL today", "TCL target")
TAILORING_COLUMNS = ("ID", "Tailoring", "The integrator's side")
STATUS = ("concept", "proposed", "new here")
AFTER_ACCEPTANCE = "added after acceptance"  # DOC-47: end of the source of a row that was not in issue 1
COVERED_COLUMNS = ("ID", "Proposed by", "Covered by")
ACCEPTED = "c44f94ad"  # DOC-38: issue 1 accepted in the safety review of DOC-19 (2026-09-30)
MANUAL_PATH = "doc/safety/safety-manual.md"
TAG = r"v\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?"
STATE_LINE = re.compile(rf"^(?:Baseline: ({TAG})|Working state: dev at [0-9a-f]{{7,40}} \(after ({TAG})\))$",
                        flags=re.MULTILINE)
# not credited in stage 1 (concept 1.4, 6.5; fmea scope): device-dependent, optional hardware path, stage 2; SYS-116:
# selectable safe state and its start test (accepted by nxconfig since SYS-139/SYS-140, start test called since
# SYS-141/SYS-143; not credited, board proof open)
NOT_CREDITED = {"SM-33", "SM-52", "SM-58", "SM-40", "SM-41", "SM-42", "SM-43", "SM-26", "SM-27",
                "SM-59",  # SYS-125: LVD reset of the MCU, hardware switched on by the family port
                "SM-60"}  # NVM-25: lock of the FlexNVM block, proposed
ANNOUNCED = ("AoU-31",)
# DOC-49 (CM-10, CR-10-1 F-03): announced in issue 1, an obligation for projects with an active short since CM-10;
# its source is the proposal of partner-and-degraded.md 7.1
IN_FORCE = ("AoU-33",)
# DOC-49 (CM-10 Z4): binding with the review, read `concept` even while the source text still says "open for
# acceptance" (the source is corrected by DOC-48)
BINDING_BY_REVIEW = ("AoU-37", "AoU-38", "AoU-39")
# DOC-59 (re-check of CM-02, decision Z7I b): proposed by the architecture safety analysis, binding by the review
# decision because accepted residual risks rest on them (AN-165, AN-170, AN-172); read `concept` although they are
# not nodes of concept section 4
BINDING_BY_DECISION = ("AoU-ASA-a", "AoU-ASA-b", "AoU-ASA-d")


def section(text: str, start: str, end: str = None) -> str:
    part = text.split(start, 1)[1] if start in text else ""
    return part.split(end, 1)[0] if end and end in part else part


def expand(cell: str) -> list:
    """IDs of a cell: 'SM-33, SM-52' and ranges 'SM-40 … SM-43'."""
    ids = []
    for a, b in re.findall(r"(SM-\d\d)(?:\s*…\s*(SM-\d\d))?", cell):
        if b:
            ids += [f"SM-{n:02d}" for n in range(int(a[3:]), int(b[3:]) + 1)]
        else:
            ids.append(a)
    return ids


def tables(text: str, name: str, columns: tuple) -> list:
    """Rows of several tables with the same header inside one marked block (the subsections of section 7)."""
    rows = []
    for part in re.split(r"\n(?=\| ID \|)", block(text, name)):
        if part.startswith("| ID |"):
            rows += table(f"<!-- {name}:start -->\n{part}\n<!-- {name}:end -->", name, columns)
    if not rows:
        raise ValueError(f"{name}: no table")
    return rows


class Sources:
    """The documents the manual restates, read once."""

    def __init__(self):
        read = lambda name: (SAFETY / name).read_text(encoding="utf-8")  # noqa: E731
        model = tracecheck.safety_model()
        fmea = read("fmea.md")
        tools = read("tool-classification.md")
        pad = read("partner-and-degraded.md")
        self.fmea_sections = set(re.findall(r"^#### (SM-\d\d) ", fmea, flags=re.MULTILINE))
        self.concept_aou = [a for a, f in model.aous.items() if f.get("STATUS") == "concept"]
        # DOC-47: an entry of concept section 4 that is still open for acceptance says so in its text
        # (assumptions.sdoc, section text); the manual reads it `proposed`
        self.open_for_acceptance = [a for a in self.concept_aou
                                    if "open for acceptance" in model.aous[a].get("STATEMENT", "")
                                    and a not in BINDING_BY_REVIEW]
        # DOC-47: the proposals of the dependent failure analysis and of the architecture safety analysis, which the
        # manual carries as rows or as covered by existing rows (section 7.7)
        self.dfa_aou = re.findall(r"^\| (AoU-DFA-[a-z]) \|", section(read("dfa.md"), "## 9. Proposed assumptions",
                                                                    "\n## 10."), flags=re.MULTILINE)
        self.asa_aou = re.findall(r"^\| (AoU-ASA-[a-z]) \|",
                                  section(read("architecture-safety-analysis.md"), "### 7.1 Assumptions of use",
                                          "\n### 7.2"), flags=re.MULTILINE)
        self.proposed_aou = ([a for a, f in model.aous.items() if f.get("STATUS") == "proposed"]
                             + re.findall(r"^\| (AoU-\d\d) \|", section(tools, "## 6. Proposed assumptions",
                                                                         "\n## 7."), flags=re.MULTILINE)
                             + [r["ID"] for r in table(read("config-range.md"), "cfg-aou",
                                                       ("ID", "Assumption of use", "Values"))]
                             # SYS-95: the assumptions proposed by the dependent failure analysis (DOC-19 part of DFA-5)
                             + self.dfa_aou
                             # DOC-47: and by the architecture safety analysis (G-12/K10-02)
                             + self.asa_aou
                             # DOC-49: AoU-33 of partner-and-degraded.md 7.1, in force since CM-10 (F-03)
                             + [a for a in IN_FORCE if a in pad])
        self.sec_aou = [r["ID"] for r in table(read("security-interplay.md"), "sec-aou",
                                               ("ID", "Assumption of use", "Interfaces"))]
        self.announced = [a for a in ANNOUNCED if a in pad]
        self.mechanisms = [m for m, f in model.mechanisms.items() if f.get("STATUS") != "withdrawn"]
        self.goals = [g for g, f in model.goals.items() if f.get("STATUS") != "withdrawn"]
        self.tools = [(r["Tool"], r["TCL today"], r["TCL target"]) for r in table(
            tools, "tool-summary", ("Tool", "Path", "TI", "TD today", "TCL today", "TCL target", "Safeguards today",
                                    "Tests", "Proposals"))]
        plan = read("safety-plan.md")
        self.tailoring = [r["ID"] for r in table(plan, "plan-tailoring",
                                                 ("ID", "Clause(s)", "Tailoring", "Rationale", "Consequence"))]
        self.ssr = {r["id"]: r["status"] for r in model.requirements if r["status"] != "withdrawn"}


class Manual:
    def __init__(self, text: str, src: Sources, exists=None):
        self.text = text
        self.src = src
        self.exists = exists or (lambda p: p.exists())
        self.aou = tables(text, "manual-aou", AOU_COLUMNS)
        self.sec = table(text, "manual-aou-sec", SEC_COLUMNS)
        self.mechanisms = table(text, "manual-mechanisms", SM_COLUMNS)
        self.tools = table(text, "manual-tools", TOOL_COLUMNS)
        self.tailoring = table(text, "manual-tailoring", TAILORING_COLUMNS)
        self.covered = table(text, "manual-aou-covered", COVERED_COLUMNS)

    def aou_problems(self) -> list:
        problems = []
        ids = [r["ID"] for r in self.aou] + [r["ID"] for r in self.sec]
        covered = [r["ID"] for r in self.covered]
        for dup in sorted({i for i in ids + covered if (ids + covered).count(i) > 1}):
            problems.append(f"{dup} has more than one row")
        expected = self.src.concept_aou + self.src.proposed_aou + self.src.sec_aou
        problems += [f"{a} of the sources has no row in the manual" for a in expected
                     if a not in ids and a not in covered]
        concept = [a for a in self.src.concept_aou if a not in self.src.open_for_acceptance] + list(BINDING_BY_DECISION)
        for row in self.aou:
            where, status = row["ID"], row["Status"]
            if status not in STATUS:
                problems.append(f"{where}: status '{status}' not in {STATUS}")
            elif (status == "concept") != (where in concept):
                problems.append(f"{where}: status '{status}' does not match concept section 4 "
                                "(an entry open for acceptance reads 'proposed')")
            elif where not in expected and status != "new here":
                problems.append(f"{where}: in no source, so it must be 'new here'")
            elif (where in self.src.proposed_aou and where not in self.src.concept_aou
                  and where not in BINDING_BY_DECISION and status != "proposed"):
                # an AoU proposed elsewhere and carried into concept section 4 (DOC-38) reads `concept`
                problems.append(f"{where}: proposed in its source, status must be 'proposed'")
            for column in ("The integrator's obligation", "How to fulfil it", "Source"):
                if len(row[column]) < 8:
                    problems.append(f"{where}: column '{column}' empty")
            if where in ANNOUNCED:
                problems.append(f"{where}: announced for a mechanism that does not exist, not an obligation yet")
        for row in self.sec:
            if "security-interplay" not in row["Source"]:
                problems.append(f"{row['ID']}: source must be security-interplay.md")
        announced = section(self.text, "### 7.6", "\n## 8.")
        problems += [f"{a} missing in section 7.6" for a in self.src.announced if a not in announced]
        return problems

    def analysis_problems(self) -> list:
        """DOC-47: the AoU proposals of the DFA and the architecture safety analysis are rows or explicitly covered."""
        problems = []
        rows = {r["ID"] for r in self.aou} | {r["ID"] for r in self.sec}
        covered = {r["ID"]: r for r in self.covered}
        for aou in self.src.dfa_aou + self.src.asa_aou:
            if aou not in rows and aou not in covered:
                problems.append(f"{aou}: proposed by an analysis, neither a row nor covered (section 7.7)")
        for aou, row in covered.items():
            if aou not in self.src.dfa_aou + self.src.asa_aou:
                problems.append(f"{aou}: covered row for a proposal no analysis makes")
            named = set(re.findall(r"AoU-[0-9A-Za-z]+(?:-[0-9a-z]+)?", row["Covered by"]))
            if not named & rows:
                problems.append(f"{aou}: 'Covered by' names no row of section 7 or 11")
            if named - rows:
                problems.append(f"{aou}: 'Covered by' names unknown rows {sorted(named - rows)}")
            if "](" not in row["Proposed by"]:
                problems.append(f"{aou}: 'Proposed by' does not link its source")
        return problems

    def after_acceptance_problems(self, accepted_text: str) -> list:
        """DOC-47: rows added after the accepted issue say so, rows of the accepted issue do not."""
        accepted = set(re.findall(r"^\| (AoU-[0-9A-Za-z]+(?:-[0-9a-z]+)?) \|", accepted_text, flags=re.MULTILINE))
        problems = []
        for row in self.aou:
            marked = row["Source"].endswith(AFTER_ACCEPTANCE)
            if row["ID"] in accepted and marked:
                problems.append(f"{row['ID']}: in the accepted issue, but marked '{AFTER_ACCEPTANCE}'")
            if row["ID"] not in accepted and not marked:
                problems.append(f"{row['ID']}: not in the accepted issue, "
                                f"its source must end with '{AFTER_ACCEPTANCE}'")
        return problems

    def mechanism_problems(self) -> list:
        problems = []
        listed = {}
        for row in self.mechanisms:
            for sm in expand(row["ID"]):
                if sm in listed:
                    problems.append(f"{sm} has more than one row")
                listed[sm] = row
        problems += [f"{sm} of the catalogue has no row" for sm in self.src.mechanisms if sm not in listed]
        for sm, row in listed.items():
            not_credited = row["Credited"].startswith("**no**")
            if (sm in NOT_CREDITED) != not_credited:
                problems.append(f"{sm}: credited column '{row['Credited']}' is wrong for stage 1")
            if not not_credited and sm not in self.src.fmea_sections:
                problems.append(f"{sm}: credited, but fmea.md has no section for it")
            rating = row[RATING_COLUMN]
            if rating != "—" and not RATING.search(rating):
                problems.append(f"{sm}: column '{RATING_COLUMN}' without neXtBSW's rating E1 ... E3 (DOC-50)")
            if STANDARD_CLASS.search(rating):
                problems.append(f"{sm}: column '{RATING_COLUMN}' names a coverage class (CM-10 F-02, DOC-50)")
        return problems

    def credited_count(self) -> int:
        return sum(1 for r in self.mechanisms for sm in expand(r["ID"])
                   if r["Credited"].startswith("yes"))

    def requirement_problems(self) -> list:
        allocated = section(self.text, "### 3.2", "\n## 4.")
        table_part, _, rest = allocated.partition("Not allocated to the element")
        named = set(re.findall(r"SSR-\d{3}", table_part))
        not_allocated = set(re.findall(r"SSR-\d{3}", rest))
        problems = [f"{s} is not in ssr.sdoc" for s in sorted((named | not_allocated) - set(self.src.ssr))]
        for ssr, status in sorted(self.src.ssr.items()):
            if status == "open" and ssr not in not_allocated:
                problems.append(f"{ssr} is open and must be named as not allocated")
            if status != "open" and ssr not in named:
                problems.append(f"{ssr} ({status}) is allocated to no assumed technical safety requirement")
            if status == "open" and ssr in named:
                problems.append(f"{ssr} is open but allocated")
        return problems

    def goal_problems(self) -> list:
        goals = re.findall(r"^\| (ASG-\d\d) \|", section(self.text, "### 3.1", "### 3.2"), flags=re.MULTILINE)
        return [] if goals == self.src.goals else [f"assumed goals {goals} differ from goals.sdoc {self.src.goals}"]

    def tool_problems(self) -> list:
        mine = [(r["Tool"], r["TCL today"], r["TCL target"]) for r in self.tools]
        return [] if mine == self.src.tools else [
            f"tool table differs from tool-classification.md: {sorted(set(mine) ^ set(self.src.tools))}"]

    def tailoring_problems(self) -> list:
        ids = [r["ID"] for r in self.tailoring]
        return [] if ids == self.src.tailoring else [f"tailoring {ids} differs from the safety plan"]

    def summary_problems(self) -> list:
        page = section(self.text, "## 0. Result in one page", "\n## 1.")
        expected = [f"<!-- manual-aou-counts -->**{len(self.aou) + len(self.sec)}**",
                    f"<!-- manual-sm-counts -->**{self.credited_count()}**",
                    f"<!-- manual-tool-counts -->**{len(self.tools)}**"]
        return [f"section 0 does not say {e}" for e in expected if e not in page]

    def reference_problems(self) -> list:
        problems = [f"proposal {p}: the manual does not reference {doc}"
                    for p, doc in (("C4", "config-range.md"), ("S4", "security-interplay.md")) if doc not in self.text]
        for target in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", self.text):
            if "://" not in target and not self.exists((SAFETY / target).resolve()):
                problems.append(f"link target {target} does not exist")
        return problems

    def all_problems(self) -> list:
        return (self.aou_problems() + self.analysis_problems() + self.mechanism_problems()
                + self.requirement_problems() + self.goal_problems() + self.tool_problems()
                + self.tailoring_problems() + self.summary_problems() + self.reference_problems()
                + licence_problems(self.text))


def state_line_problems(text: str, tags: list, changed_since) -> list:
    """DOC-47 (cm-plan 6.1): the state line against the tags. tags: existing release tags, newest first;
    changed_since(tag) -> True if a commit after that tag changed the manual."""
    lines = STATE_LINE.findall(text)
    if len(lines) != 1:
        return [f"{len(lines)} state lines, expected one 'Baseline: vX.Y.Z' or "
                "'Working state: dev at <commit> (after vX.Y.Z)'"]
    baseline, after = lines[0]
    if baseline and baseline in tags and changed_since(baseline):
        return [f"changed after {baseline}, the state line must say "
                f"'Working state: dev at <commit> (after {baseline})'"]
    if after and tags and after != tags[0]:
        return [f"working state after {after}, but the newest tag is {tags[0]}"]
    return []


def task_of(subject: str):
    match = re.match(r"([A-Z][A-Z0-9]*-\d+[A-Z]?)\b", subject)
    return match.group(1) if match else None


def history_problems(text: str, commits: list) -> list:
    """DOC-47: every commit (hash, subject) that changed the manual since the acceptance has a row in section 16, found
    by its short hash or by the task ID that leads its subject."""
    history = section(text, "## 16. Change history", "\n## 17.")
    problems = []
    for commit, subject in commits:
        task = task_of(subject)
        if commit[:8] in history:
            continue
        if task and re.search(rf"(?<![\w-]){re.escape(task)}(?![\w-])", history):
            continue
        problems.append(f"commit {commit[:8]} ({subject[:60]}) changed the manual and has no row in section 16")
    return problems


def git(*args):
    """Output of a git command in the repository, None if git or the history is not there (open export, PLAT-62)."""
    if not (REPO / ".git").exists() or not shutil.which("git"):
        return None
    result = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, encoding="utf-8")
    return result.stdout if result.returncode == 0 else None


class SafetyManualConsistency(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DOC.read_text(encoding="utf-8")
        cls.src = Sources()
        cls.manual = Manual(cls.text, cls.src)

    def manual_of(self, text=None, exists=None):
        return Manual(self.text if text is None else text, self.src, exists)

    def sub(self, source, old, new):
        self.assertIn(old, source, f"counter-check edit does not hit: {old[:80]}")
        return source.replace(old, new, 1)

    def test_sources_found(self):
        # DOC-38 (review DOC-19, D2): AoU-26 ... AoU-29 and AoU-34 ... AoU-36 carried into concept section 4
        # SYS-135 (review SYS-128/SYS-130, C3): AoU-37 in concept section 4.3
        # SYS-143 (review SYS-136/SYS-141, E2-E4): AoU-38 in concept section 4.3
        # SYS-125: AoU-39 (supply margin above the LVD threshold) in concept section 4.3
        # DOC-51 (CM-07 ZF, ZG): AoU-33 a node of concept 4.3, AoU-24/AoU-25/AoU-40 binding (`concept`)
        self.assertEqual(len(self.src.concept_aou), 41)
        self.assertTrue({"AoU-24", "AoU-25", "AoU-33", "AoU-40"} <= set(self.src.concept_aou))
        self.assertTrue({"AoU-26", "AoU-29", "AoU-34", "AoU-36"} <= set(self.src.concept_aou))
        self.assertIn("AoU-26", self.src.proposed_aou)
        self.assertIn("AoU-CFG-6", self.src.proposed_aou)
        self.assertTrue({"AoU-DFA-a", "AoU-DFA-b", "AoU-DFA-c"} <= set(self.src.proposed_aou))  # SYS-95
        self.assertEqual(len(self.src.sec_aou), 8)
        self.assertIn("SM-58", self.src.mechanisms)
        self.assertEqual(len(self.src.goals), 7)
        self.assertEqual(self.src.tailoring[0], "T-01")

    def test_assumptions(self):
        self.assertEqual(self.manual.aou_problems(), [])

    def test_analysis_proposals(self):
        # DOC-47: the AoU proposals of dfa.md section 9 and architecture-safety-analysis.md 7.1
        self.assertEqual(self.src.dfa_aou, ["AoU-DFA-a", "AoU-DFA-b", "AoU-DFA-c"])
        self.assertEqual(self.src.asa_aou, ["AoU-ASA-a", "AoU-ASA-b", "AoU-ASA-c", "AoU-ASA-d"])
        # DOC-49 (CM-10 Z4): binding, whatever the source text still says
        self.assertFalse({"AoU-37", "AoU-38", "AoU-39"} & set(self.src.open_for_acceptance))
        self.assertTrue({"AoU-37", "AoU-38", "AoU-39"} <= set(self.src.concept_aou))
        self.assertIn("AoU-33", self.src.proposed_aou)  # DOC-49 (CM-10 F-03)
        self.assertEqual(self.src.announced, ["AoU-31"])
        self.assertEqual(self.manual.analysis_problems(), [])

    def test_mechanisms(self):
        self.assertEqual(self.manual.mechanism_problems(), [])

    def test_requirements(self):
        self.assertEqual(self.manual.requirement_problems(), [])

    def test_goals(self):
        self.assertEqual(self.manual.goal_problems(), [])

    def test_tools(self):
        self.assertEqual(self.manual.tool_problems(), [])

    def test_tailoring(self):
        self.assertEqual(self.manual.tailoring_problems(), [])

    def test_summary(self):
        self.assertEqual(self.manual.summary_problems(), [])

    def test_references(self):
        self.assertEqual(self.manual.reference_problems(), [])

    def test_standard_cited_by_part_and_clause_only(self):
        self.assertEqual(licence_problems(self.text), [])

    def test_counter_checks(self):
        text = self.text
        aou = block_line(text, "| AoU-24 |")
        # an AoU of a source removed, duplicated, wrong status, announced one as obligation
        self.assertTrue(self.manual_of(self.sub(text, aou + "\n", "")).aou_problems())
        self.assertTrue(self.manual_of(self.sub(text, aou, aou + "\n" + aou)).aou_problems())
        # (DOC-51: AoU-24 reads `concept` since CM-07 ZG, so the wrong status is now `proposed`)
        self.assertTrue(self.manual_of(self.sub(text, aou, aou.replace("| concept |", "| proposed |")))
                        .aou_problems())
        self.assertTrue(self.manual_of(self.sub(text, aou, aou + "\n" + aou.replace("AoU-24", "AoU-31")
                                                .replace("| proposed |", "| new here |"))).aou_problems())
        sec = block_line(text, "| AoU-SEC-8 |")
        self.assertTrue(self.manual_of(self.sub(text, sec + "\n", "")).aou_problems())
        # a mechanism missing, a stage-2 mechanism credited
        sm = block_line(text, "| SM-19 |")
        self.assertTrue(self.manual_of(self.sub(text, sm + "\n", "")).mechanism_problems())
        sm58 = block_line(text, "| SM-58 |")
        self.assertTrue(self.manual_of(self.sub(text, sm58, sm58.replace("| **no** |", "| yes |")))
                        .mechanism_problems())
        # DOC-50: a typical class back in the rating column, a rating cell without E1 ... E3
        self.assertTrue(self.manual_of(self.sub(text, sm, sm.replace("**E1**", "**E1**, typical high")))
                        .mechanism_problems())
        self.assertTrue(self.manual_of(self.sub(text, sm, sm.replace("**E1**", "rated"))).mechanism_problems())
        # an implemented SSR no longer allocated, an open one allocated
        self.assertTrue(self.manual_of(self.sub(text, "SSR-086 |", "|")).requirement_problems())
        self.assertTrue(self.manual_of(self.sub(text, "SSR-084 |", "SSR-084, SSR-061 |")).requirement_problems())
        # goals, tools, tailoring
        self.assertTrue(self.manual_of(self.sub(text, "| ASG-07 |", "| ASG-08 |")).goal_problems())
        self.assertTrue(self.manual_of(self.sub(text, "| dbcgen | TCL 1 | TCL 1 |", "| dbcgen | TCL 2 | TCL 1 |"))
                        .tool_problems())
        self.assertTrue(self.manual_of(self.sub(text, "| T-10 |", "| T-11 |")).tailoring_problems())
        # counts, references, links, licence rule
        self.assertTrue(self.manual_of(self.sub(text, "<!-- manual-aou-counts -->**", "<!-- manual-aou-counts -->**1"))
                        .summary_problems())
        self.assertTrue(self.manual_of(text.replace("config-range.md", "config.md")).reference_problems())
        self.assertTrue(self.manual_of(exists=lambda p: p.name != "dfa.md" and p.exists()).reference_problems())
        self.assertTrue(licence_problems(text + "\nSee ISO 26262-6, Table 9.\n"))

    def test_counter_checks_doc47(self):
        text = self.text
        # an analysis proposal neither a row nor covered; a covered row that names no existing row
        asa_a = block_line(text, "| AoU-ASA-a |")
        self.assertTrue(self.manual_of(self.sub(text, asa_a + "\n", "")).analysis_problems())
        covered_c = block_line(text, "| AoU-ASA-c |")
        # (the only covered row left since DOC-49: renamed instead of removed, the table may not be empty)
        self.assertTrue(self.manual_of(self.sub(text, covered_c, covered_c.replace("AoU-ASA-c", "AoU-ASA-z")))
                        .analysis_problems())
        self.assertTrue(self.manual_of(self.sub(text, covered_c, "| AoU-ASA-c | [x](dfa.md) | nothing |"))
                        .analysis_problems())
        # a row both carried and covered
        self.assertTrue(self.manual_of(self.sub(text, covered_c, covered_c.replace("AoU-ASA-c", "AoU-ASA-a")))
                        .aou_problems())
        # DOC-49 (CM-10 F-03): AoU-ASA-b removed as a row; AoU-33 back in 7.6 only
        asa_b = block_line(text, "| AoU-ASA-b |")
        self.assertTrue(self.manual_of(self.sub(text, asa_b + "\n", "")).analysis_problems())
        aou33 = block_line(text, "| AoU-33 |")
        self.assertTrue(self.manual_of(self.sub(text, aou33 + "\n", "")).aou_problems())
        # an AoU open for acceptance read as concept (a source that still says so for an AoU not decided by a review)
        aou41 = block_line(text, "| AoU-41 |")
        src = copy.copy(self.src)
        src.open_for_acceptance = self.src.open_for_acceptance + ["AoU-41"]
        self.assertTrue(Manual(text, src).aou_problems())
        self.assertFalse(Manual(self.sub(text, aou41, aou41.replace("| concept |", "| proposed |")), src)
                         .aou_problems())
        # DOC-59: an AoU made binding by a review decision read `proposed` again
        asa_a = block_line(text, "| AoU-ASA-a |")
        self.assertTrue(self.manual_of(self.sub(text, asa_a, asa_a.replace("| concept |", "| proposed |")))
                        .aou_problems())
        aou37 = block_line(text, "| AoU-37 |")
        # rows added after the accepted issue: a missing note, a note on a row of the accepted issue
        accepted = "\n".join(line for line in text.splitlines() if AFTER_ACCEPTANCE + " |" not in line)
        self.assertEqual(self.manual.after_acceptance_problems(accepted), [])
        self.assertTrue(self.manual_of(self.sub(text, aou37, aou37.replace("; " + AFTER_ACCEPTANCE, "")))
                        .after_acceptance_problems(accepted))
        self.assertTrue(self.manual.after_acceptance_problems(accepted + "\n" + aou37))
        # state line: changed after the baseline tag, a working state after an old tag, two lines
        working = STATE_LINE.search(text).group(0)
        baseline = self.sub(text, working, "Baseline: v0.2.0")
        self.assertEqual(state_line_problems(baseline, ["v0.2.0"], lambda tag: False), [])
        self.assertEqual(state_line_problems(baseline, ["v0.1.0"], lambda tag: True), [])  # release candidate
        self.assertTrue(state_line_problems(baseline, ["v0.2.0", "v0.1.0"], lambda tag: True))
        self.assertTrue(state_line_problems(text, ["v0.3.0", "v0.2.0"], lambda tag: True))
        self.assertTrue(state_line_problems(text + "\nBaseline: v0.2.0\n", ["v0.2.0"], lambda tag: True))
        # history: a commit without a row, found by hash or by its leading task ID
        commits = [("c6359ff7aaaa", "DOC-45: Sicherheitsdokumente"), ("880e70e7bbbb", "Board 03.10.: NVM-05 done")]
        self.assertEqual(history_problems(text, commits), [])
        self.assertTrue(history_problems(text, [("0123abcd9999", "Board 04.10.: something")]))
        self.assertTrue(history_problems(text, [("0123abcd9999", "DOC-4711: something")]))
        self.assertTrue(history_problems(text.replace("880e70e7", "xxxxxxxx"), commits[1:]))


class SafetyManualHistory(unittest.TestCase):
    """DOC-47: the manual against the git history; skipped where the history is not there (open export, PLAT-62)."""

    @classmethod
    def setUpClass(cls):
        cls.text = DOC.read_text(encoding="utf-8")

    def history(self, *args) -> str:
        out = git(*args)
        if out is None:
            self.skipTest("no git repository with history (open export, PLAT-62)")
        return out

    def test_state_line_against_the_tags(self):
        tags = self.history("tag", "--list", "--merged", "HEAD", "--sort=-v:refname", "v*").split()
        if not tags:
            self.skipTest("no release tag in this repository")

        def changed(tag):
            return bool(self.history("log", "--format=%H", f"{tag}..HEAD", "--", MANUAL_PATH).strip())

        self.assertEqual(state_line_problems(self.text, tags, changed), [])

    def test_every_change_since_the_acceptance_in_section_16(self):
        if git("cat-file", "-e", f"{ACCEPTED}^{{commit}}") is None:
            self.skipTest(f"acceptance commit {ACCEPTED} not in this history (export without history)")
        log = self.history("log", "--no-merges", "--format=%H%x09%s", f"{ACCEPTED}..HEAD", "--", MANUAL_PATH)
        commits = [tuple(line.split("\t", 1)) for line in log.splitlines() if "\t" in line]
        self.assertEqual(history_problems(self.text, commits), [])

    def test_rows_added_after_the_acceptance_are_marked(self):
        accepted = git("show", f"{ACCEPTED}:{MANUAL_PATH}")
        if accepted is None:
            self.skipTest(f"acceptance commit {ACCEPTED} not in this history (export without history)")
        manual = Manual(self.text, None)  # the rows only, no sources needed
        self.assertEqual(manual.after_acceptance_problems(accepted), [])


def block_line(text: str, start: str) -> str:
    return next(line for line in text.splitlines() if line.startswith(start))


if __name__ == "__main__":
    unittest.main()
