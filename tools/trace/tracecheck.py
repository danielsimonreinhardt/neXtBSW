# SPDX-License-Identifier: Apache-2.0
"""Traceability of the neXtBSW safety requirements (SYS-22): goal / assumption -> requirement -> code -> test.

Sources (all inside the repository). Since DOC-21 step 3 the requirements and the safety catalogue are StrictDoc
documents (user decisions Q2 and Q5, migration plan doc/requirements/migration-plan.md):

    doc/requirements/safety/ssr.sdoc          the software safety requirements with stable IDs (SSR-nnn)
    doc/requirements/safety/goals.sdoc        ASG-* (assumed safety goals)
    doc/requirements/safety/assumptions.sdoc  AoU-* (assumptions of use; status concept or proposed)
    doc/requirements/safety/mechanisms.sdoc   SM-* (mechanisms, STAGE 1 or 2 (device-dependent); CREDITED no = a
                                              stage-1 mechanism that is described but claims nothing)
    doc/safety/concept.md          F* (findings), G* (coverage gaps)
    doc/safety/fmea.md             findings F9 ..., proposed tasks T*, open points N*, the "#### SM-xx" sections and
                                   the test IDs cited as evidence
    tools/test/tests.yaml          requirement -> test: an entry names the requirements it verifies in the field
                                   `requirements: [SSR-nnn, ...]`
    source code                    requirement -> code: the field CODE of an SSR (`file :: symbol`; the file must
                                   exist and contain the symbol) and/or a comment tag `nx-req: SSR-nnn` in the code
    doc/safety/safety-manual.md    every AoU needs a place there (the manual is DOC-19; while it is missing, every AoU
                                   is reported as a warning)

The format before step 3 (doc/safety/requirements.yaml with the catalogue in the tables of concept.md and fmea.md) is
still read with `--requirements FILE` (synthetic repositories of the host test, the equivalence check of
migrate_sdoc.py); without --sdoc and --requirements the source is doc/requirements when it exists.

Checks, each message with the file and the YAML path (or the file and line of a tag):

    error    dead references (unknown ASG/AoU/SM/F/G/T/N, unknown requirement in tests.yaml or in a tag, missing file,
             symbol not in the file, test cited as evidence that tests.yaml does not have), schema errors, a stage-1
             requirement that credits a stage-2 (device-dependent) or a not-credited mechanism, a credited stage-1
             mechanism without any requirement, a goal without any requirement, an implemented requirement whose verification says "test"
             but that no test names, a test cited as evidence of a credited mechanism in fmea.md that names no
             requirement, an AoU missing
             from an existing safety manual, and a generated matrix that is not up to date
    warning  a requirement without a test (verified by review, analysis or board only), a mechanism without a test,
             an open (not yet implemented) requirement, a goal with open requirements only, an AoU without a place
             in the safety manual while the manual does not exist
    info     a code link without the matching `nx-req` tag in that file (suggested tag)

In an exported tree (DOC-85) the export lists the test entries it removed on purpose in doc/unpublished.yaml
(tools/distcheck/unpublished.py): a test named by a clause or cited in fmea.md that is listed there is no dead
reference, and a requirement whose clauses name only such tests counts as tested; a matrix the export left out
(listed file) is not compared. An unlisted missing test or matrix stays an error, a list the export did not write is
an error; without the list (development repository) nothing changes.

The core has no Qt and no dependency beyond PyYAML, so neXtBSW Studio can call it.

**StrictDoc model (DOC-21).** The checks run on the StrictDoc documents: StrictDoc exports its model as JSON (sdocmodel.py runs it as a separate program, nothing of
StrictDoc is imported), the SSR become the same records, and the safety catalogue (ASG, AoU, SM) comes from the
StrictDoc nodes of the same name; findings (F, G, T, N) and the evidence tests stay in concept.md and fmea.md. On
top: every UID of the .sdoc files is counted independently and compared with the export (a node StrictDoc lost is
an error), the UID format per node type, and the node type at the end of every parent relation. Messages name the
.sdoc file and the field (`doc/requirements/safety/ssr.sdoc: SSR-001.RELATIONS(DerivedFrom)[0]`). A StrictDoc
error (grammar, choice, missing parent, duplicate UID) is a `schema` error with StrictDoc's message.

**Clauses (DOC-43, step 3b).** A lettered clause of an SSR is a CLAUSE node SSR-nnn(a) with its own VERIFICATION and
TESTS (test entries of the requirement that verify the clause); the requirement's text is rebuilt from its clauses.
Errors: clause UID and letter order, parent, statement and clauses together, clause methods outside the
requirement's or a method of the requirement no clause names, a clause test that is unknown or does not name the
requirement. Warnings: a clause verified by test without a test entry, a test entry no clause claims. REVIEW_STATUS
of every SSR (and of a clause where it differs) is shown in the matrix. `check` on the command line also checks the
RST of every field text the way StrictDoc's HTML export renders it (kind `rst`).

    python tools/trace check [--strict]          messages, exit 1 on an error (with --strict also on a warning)
    python tools/trace matrix [--out FILE]       write the traceability matrix (default doc/safety/traceability.md)
    python tools/trace tags                      suggested code tags (file, symbol, requirement)
    python tools/trace check --sdoc DIR [--sdoc-json index.json]   on another StrictDoc project / an existing export
    python tools/trace check --requirements FILE  on a requirements.yaml in the format before DOC-21 step 3
    python tools/trace export --out DIR          StrictDoc JSON export of doc/requirements (DIR/json/index.json)
    python tools/trace html --out DIR            StrictDoc HTML export with the scan of the code markers (slow)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sdocmodel  # noqa: E402  (same folder; standard library only)

REPO = Path(__file__).resolve().parents[2]
REQUIREMENTS = "doc/safety/requirements.yaml"
SDOC_ROOT = "doc/requirements"

DEFAULT_SOURCES = {
    "concept": "doc/safety/concept.md",
    "fmea": "doc/safety/fmea.md",
    "tests": "tools/test/tests.yaml",
    "safety_manual": "doc/safety/safety-manual.md",
    "matrix": "doc/safety/traceability.md",
}
DEFAULT_TAG_SCAN = {
    "roots": ["ecu", "platforms", "libs", "bootloader", "tools", "cmake", "swc"],
    "suffixes": [".c", ".h", ".cpp", ".hpp", ".S", ".s", ".py", ".cmake", ".j2", ".in", ".ld", ".dld"],
    "skip_dirs": ["external", "build", "generated", ".venv", ".git", "__pycache__", "node_modules"],
    "skip_paths": ["tools/trace"],
}

REQ_ID = re.compile(r"SSR-\d{3}")
TAG = re.compile(r"nx-req:\s*((?:SSR-\d+)(?:\s*,\s*SSR-\d+)*)")
STATUSES = ("implemented", "partial", "open")
IMPLEMENTED = ("implemented", "partial")
METHODS = ("test", "review", "analysis", "board")
ASILS = ("QM", "A", "B", "C", "D")
REQ_KEYS = {"id", "title", "text", "derived_from", "mechanisms", "findings", "stage", "asil", "status",
            "verification", "code", "note", "review", "clauses", "statement_and_clauses"}
# DOC-43: review status of a requirement / clause (field REVIEW_STATUS, statuses of the review record in ssr.sdoc)
REVIEW_STATUSES = ("proposed", "rework", "inspected", "accepted")
CLAUSE_STATUSES = ("implemented", "partial", "open", "withdrawn")
REQUIRED_KEYS = ("id", "title", "text", "derived_from", "stage", "asil", "status", "verification")

ERROR, WARNING, INFO = "error", "warning", "info"
SEVERITY_ORDER = {ERROR: 0, WARNING: 1, INFO: 2}


@dataclass
class Message:
    severity: str
    kind: str       # short category, e.g. dead-ref, req-without-test
    where: str      # file: YAML path, or file:line
    text: str

    def __str__(self) -> str:
        return f"{self.severity}: {self.where}: {self.text} [{self.kind}]"


@dataclass
class Mechanism:
    id: str
    name: str
    stage1: bool
    # DOC-38: a stage-1 row of the catalogue marked "*not credited*" (SM-58, SYS-86: optional hardware path whose
    # effectiveness is not claimed, review SYS-86 H3) is described but carries no claim - it needs no requirement and
    # no requirement may credit it
    credited: bool = True


@dataclass
class Catalogue:
    goals: dict[str, str] = field(default_factory=dict)          # ASG-xx -> goal text
    aous: dict[str, bool] = field(default_factory=dict)          # AoU-xx -> proposed (only in fmea.md)
    mechanisms: dict[str, Mechanism] = field(default_factory=dict)
    findings: set[str] = field(default_factory=set)              # F*, G*, T*, N*
    evidence_tests: dict[str, list[str]] = field(default_factory=dict)  # test id -> mechanisms citing it
    where: dict[str, str] = field(default_factory=dict)          # ASG/AoU/SM -> "file: ID" (StrictDoc model only)
    withdrawn: set[str] = field(default_factory=set)             # withdrawn ASG/AoU/SM (StrictDoc model only)

    def kind_of(self, ident: str) -> str | None:
        if ident in self.goals:
            return "goal"
        if ident in self.aous:
            return "aou"
        if ident in self.mechanisms:
            return "mechanism"
        if ident in self.findings:
            return "finding"
        return None


@dataclass
class CodeLink:
    file: str
    symbol: str
    tagged: bool = False


@dataclass
class Clause:
    """One lettered clause of a requirement (DOC-43, StrictDoc node CLAUSE, UID SSR-nnn(a))."""
    id: str
    text: str
    verification: list[str]
    tests: list[str]
    status: str = ""            # empty = that of its requirement; "withdrawn" = dropped, kept for its UID
    review: str = ""            # empty = that of its requirement

    @property
    def withdrawn(self) -> bool:
        return self.status == "withdrawn"


@dataclass
class Requirement:
    id: str
    title: str
    text: str
    derived_from: list[str]
    mechanisms: list[str]
    findings: list[str]
    stage: int
    asil: str
    status: str
    verification: list[str]
    code: list[CodeLink]
    note: str = ""
    tests: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)   # "file:line" of every nx-req tag naming this requirement
    review: str = ""                                 # REVIEW_STATUS (DOC-43); empty in the format before step 3
    clauses: list[Clause] = field(default_factory=list)


@dataclass
class Result:
    messages: list[Message]
    catalogue: Catalogue
    requirements: list[Requirement]
    tests: dict[str, list[str]]          # test name -> requirement IDs named in tests.yaml
    sources: dict[str, str]
    manual_exists: bool
    manual_ids: set[str]
    origin: str = REQUIREMENTS           # requirements.yaml, or the StrictDoc project folder (DOC-21)
    # DOC-85: test entries the export removed on purpose (doc/unpublished.yaml); empty in the development repository
    unpublished_tests: frozenset = frozenset()

    def count(self, severity: str) -> int:
        return sum(1 for m in self.messages if m.severity == severity)


# ---------------------------------------------------------------------------------------------- catalogue parsing


def _section(text: str, start: str, end_pattern: str = r"\n## ") -> str:
    """Text from the heading line starting with `start` up to the next heading matched by `end_pattern`."""
    index = text.find(start)
    if index < 0:
        return ""
    rest = text[index + len(start):]
    match = re.search(end_pattern, rest)
    return rest[: match.start()] if match else rest


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_catalogue(concept: str, fmea: str) -> Catalogue:
    cat = Catalogue()
    for line in _section(concept, "### 2.2 ").splitlines():
        m = re.match(r"\|\s*(ASG-\d\d)\s*\|", line)
        if m:
            cat.goals[m.group(1)] = _cells(line)[1]
    for line in _section(concept, "## 4. ").splitlines():
        m = re.match(r"\|\s*(AoU-\d\d)\s*\|", line)
        if m:
            cat.aous[m.group(1)] = False
    for line in _section(fmea, "## 6. ").splitlines():
        m = re.match(r"\|\s*(AoU-\d\d)\s*\(proposed\)\s*\|", line)
        if m and m.group(1) not in cat.aous:
            cat.aous[m.group(1)] = True
    for line in _section(concept, "## 6. Catalogue of safety mechanisms").splitlines():
        m = re.match(r"\|\s*(SM-\d\d)\s*\|\s*\*\*(.+?)\*\*", line)
        if m:
            stage1 = "device-dependent" not in line
            cat.mechanisms[m.group(1)] = Mechanism(m.group(1), m.group(2), stage1,
                                                   stage1 and "*not credited*" not in line)
    for m in re.finditer(r"^### 13\.\d+ (SM-\d\d) \S+ (.+)$", concept, flags=re.MULTILINE):
        cat.mechanisms.setdefault(m.group(1), Mechanism(m.group(1), m.group(2).strip(), False))
    for line in _section(concept, "### 9.1 ", r"\n### ").splitlines():
        cat.findings.update(re.findall(r"^\|\s*\*\*(F\d+)\*\*\s*\|", line))
    for line in _section(concept, "### 9.5 ", r"\n(## |---)").splitlines():
        cat.findings.update(re.findall(r"^\|\s*\*\*(G\d+)\*\*\s*\|", line))
    for line in _section(fmea, "## 6. ").splitlines():
        cat.findings.update(re.findall(r"^\|\s*\*\*(F\d+)\*\*\s*\|", line))
    for line in _section(fmea, "## 7. ").splitlines():
        cat.findings.update(re.findall(r"^\|\s*\*\*(T\d+)\*\*\s*\|", line))
    for line in _section(fmea, "## 9. ").splitlines():
        cat.findings.update(re.findall(r"^\|\s*(N\d+)\s*\|", line))
    # evidence: test IDs in the "| Evidence |" row of each "#### SM-xx" section (same rule as doc/safety/test)
    current = None
    for line in fmea.splitlines():
        head = re.match(r"^#### (SM-\d\d)\b", line)
        if head:
            current = head.group(1)
        elif line.startswith("| Evidence |") and current:
            for token in re.findall(r"`([^`]+)`", line):
                if re.fullmatch(r"[a-z][a-z0-9-]*", token):
                    cat.evidence_tests.setdefault(token, [])
                    if current not in cat.evidence_tests[token]:
                        cat.evidence_tests[token].append(current)
    return cat


# ---------------------------------------------------------------------------------------------- loading


def _read(repo: Path, rel: str) -> str | None:
    path = repo / rel
    return path.read_text(encoding="utf-8") if path.is_file() else None


def _as_list(value) -> list | None:
    if value is None:
        return []
    return value if isinstance(value, list) else None


def load_requirements(data, catalogue: Catalogue, repo: Path, req_file: str,
                      messages: list[Message], locate: Callable[[str], str] | None = None) -> list[Requirement]:
    """`locate` turns a record path (`requirements[SSR-001].derived_from[0]`) into the message location; default
    `<req_file>: <path>`. The StrictDoc source passes its own (file and field of the .sdoc)."""
    locate = locate or (lambda path: f"{req_file}: {path}")

    def msg(severity, kind, path, text):
        messages.append(Message(severity, kind, locate(path), text))

    if not isinstance(data, dict) or not isinstance(data.get("requirements"), list):
        msg(ERROR, "schema", "requirements", "missing list 'requirements'")
        return []
    if data.get("version") != 1:
        msg(ERROR, "schema", "version", f"version {data.get('version')!r} not supported (expected 1)")
    result: list[Requirement] = []
    seen: set[str] = set()
    for index, raw in enumerate(data["requirements"]):
        if not isinstance(raw, dict):
            msg(ERROR, "schema", f"requirements[{index}]", "entry is not a mapping")
            continue
        rid = str(raw.get("id", ""))
        base = f"requirements[{rid}]" if rid else f"requirements[{index}]"
        bad = False
        for key in REQUIRED_KEYS:
            if key not in raw or raw[key] in (None, "", []):
                msg(ERROR, "schema", f"{base}.{key}", "missing")
                bad = True
        for key in sorted(set(raw) - REQ_KEYS):
            msg(ERROR, "schema", f"{base}.{key}", f"unknown key (known: {', '.join(sorted(REQ_KEYS))})")
        if rid and not REQ_ID.fullmatch(rid):
            msg(ERROR, "schema", f"{base}.id", f"'{rid}' does not match SSR-nnn")
            bad = True
        if rid in seen:
            msg(ERROR, "schema", f"{base}.id", f"duplicate ID {rid}")
            bad = True
        seen.add(rid)
        lists = {}
        for key in ("derived_from", "mechanisms", "findings", "verification", "code"):
            value = _as_list(raw.get(key))
            if value is None:
                msg(ERROR, "schema", f"{base}.{key}", "must be a list")
                bad = True
                value = []
            lists[key] = value
        status, stage, asil = raw.get("status"), raw.get("stage"), str(raw.get("asil", ""))
        if status is not None and status not in STATUSES:
            msg(ERROR, "schema", f"{base}.status", f"'{status}' not one of {', '.join(STATUSES)}")
            bad = True
        if stage is not None and stage not in (1, 2):
            msg(ERROR, "schema", f"{base}.stage", f"'{stage}' not 1 or 2")
            bad = True
        if asil and asil not in ASILS:
            msg(ERROR, "schema", f"{base}.asil", f"'{asil}' not one of {', '.join(ASILS)}")
        for i, method in enumerate(lists["verification"]):
            if method not in METHODS:
                msg(ERROR, "schema", f"{base}.verification[{i}]", f"'{method}' not one of {', '.join(METHODS)}")
        # references to the concept / fmea ID spaces
        for i, ident in enumerate(lists["derived_from"]):
            kind = catalogue.kind_of(str(ident))
            if kind is None and str(ident) in catalogue.withdrawn:
                msg(ERROR, "dead-ref", f"{base}.derived_from[{i}]", f"'{ident}' is withdrawn")
            elif kind is None:
                msg(ERROR, "dead-ref", f"{base}.derived_from[{i}]", f"'{ident}' is unknown to concept.md/fmea.md")
            elif kind not in ("goal", "aou"):
                msg(ERROR, "schema", f"{base}.derived_from[{i}]",
                    f"'{ident}' is a {kind}; derived_from takes assumed goals (ASG-*) and assumptions (AoU-*)")
        for i, ident in enumerate(lists["mechanisms"]):
            mech = catalogue.mechanisms.get(str(ident))
            if mech is None and str(ident) in catalogue.withdrawn:
                msg(ERROR, "dead-ref", f"{base}.mechanisms[{i}]", f"'{ident}' is withdrawn")
            elif mech is None:
                msg(ERROR, "dead-ref", f"{base}.mechanisms[{i}]", f"'{ident}' is not a mechanism of concept.md")
            elif stage == 1 and not mech.stage1:
                msg(ERROR, "stage", f"{base}.mechanisms[{i}]",
                    f"{ident} is device-dependent (stage 2) and must not be credited by a stage-1 requirement "
                    "(strict separation, concept.md section 1.4)")
            elif not mech.credited:
                msg(ERROR, "stage", f"{base}.mechanisms[{i}]",
                    f"{ident} is marked not credited in the concept catalogue and must not be credited by a "
                    "requirement")
        for i, ident in enumerate(lists["findings"]):
            if str(ident) not in catalogue.findings:
                msg(ERROR, "dead-ref", f"{base}.findings[{i}]",
                    f"'{ident}' is not a finding, gap, task or open point of concept.md/fmea.md")
        code: list[CodeLink] = []
        for i, link in enumerate(lists["code"]):
            where = f"{base}.code[{i}]"
            if not isinstance(link, dict) or set(link) - {"file", "symbol"} or not link.get("file") \
                    or not link.get("symbol"):
                msg(ERROR, "schema", where, "needs exactly 'file' and 'symbol'")
                continue
            rel, symbol = str(link["file"]), str(link["symbol"])
            text = _read(repo, rel)
            if text is None:
                msg(ERROR, "dead-ref", f"{where}.file", f"file '{rel}' does not exist")
            elif not re.search(r"(?<!\w)" + re.escape(symbol) + r"(?!\w)", text):
                msg(ERROR, "dead-ref", f"{where}.symbol", f"symbol '{symbol}' not found in '{rel}'")
            code.append(CodeLink(rel, symbol))
        if status in IMPLEMENTED and not lists["code"] and not bad:
            msg(ERROR, "schema", f"{base}.code", f"status '{status}' needs at least one code link")
        review = str(raw.get("review", "") or "")
        if review and review not in REVIEW_STATUSES:
            msg(ERROR, "schema", f"{base}.review", f"'{review}' not one of {', '.join(REVIEW_STATUSES)}")
        clauses = load_clauses(rid, raw, [str(x) for x in lists["verification"]], base, msg)
        if bad:
            continue
        result.append(Requirement(
            id=rid, title=str(raw["title"]).strip(), text=" ".join(str(raw["text"]).split()),
            derived_from=[str(x) for x in lists["derived_from"]], mechanisms=[str(x) for x in lists["mechanisms"]],
            findings=[str(x) for x in lists["findings"]], stage=int(stage), asil=asil, status=str(status),
            verification=[str(x) for x in lists["verification"]], code=code,
            note=" ".join(str(raw.get("note", "")).split()), review=review, clauses=clauses))
    return result


def load_clauses(rid: str, raw: dict, verification: list[str], base: str, msg) -> list[Clause]:
    """The lettered clauses of one requirement record (DOC-43): UID SSR-nnn(a) with the requirement's ID, letters
    a, b, c ... in document order (a withdrawn clause keeps its letter), the methods of a clause among those of its
    requirement and, over the clauses in force, all of them; no test entry for a withdrawn clause. A requirement with
    clauses has no statement of its own (`statement_and_clauses`)."""
    if raw.get("statement_and_clauses"):
        msg(ERROR, "schema", f"{base}.text", "a requirement with clause nodes has no statement of its own - its text "
            "is the clauses")
    raw_clauses = raw.get("clauses") or []
    if not isinstance(raw_clauses, list):
        msg(ERROR, "schema", f"{base}.clauses", "must be a list")
        return []
    clauses: list[Clause] = []
    used: set[str] = set()
    for i, item in enumerate(raw_clauses):
        item = item if isinstance(item, dict) else {}
        cid = str(item.get("id", ""))
        where = f"{base}.clauses[{cid or i}]"
        expected = f"{rid}({chr(ord('a') + i)})"
        if cid != expected:
            msg(ERROR, "schema", f"{where}.id", f"clause '{cid}' out of order or not of {rid} - expected {expected} "
                "(letters a, b, c ... in document order; a withdrawn clause keeps its letter)")
        status = str(item.get("status", "") or "")
        if status and status not in CLAUSE_STATUSES:
            msg(ERROR, "schema", f"{where}.status", f"'{status}' not one of {', '.join(CLAUSE_STATUSES)}")
        review = str(item.get("review", "") or "")
        if review and review not in REVIEW_STATUSES:
            msg(ERROR, "schema", f"{where}.review", f"'{review}' not one of {', '.join(REVIEW_STATUSES)}")
        methods = [str(m) for m in item.get("verification") or []]
        if not methods:
            msg(ERROR, "schema", f"{where}.verification", "missing")
        for j, method in enumerate(methods):
            if method not in METHODS:
                msg(ERROR, "schema", f"{where}.verification[{j}]", f"'{method}' not one of {', '.join(METHODS)}")
            elif method not in verification:
                msg(ERROR, "clause-verification", f"{where}.verification[{j}]",
                    f"'{method}' is not a verification method of {rid} ({', '.join(verification)})")
        tests = [str(t) for t in item.get("tests") or []]
        if status == "withdrawn" and tests:
            msg(ERROR, "schema", f"{where}.tests", "a withdrawn clause claims no test")
        if status != "withdrawn":
            used.update(methods)
        text = " ".join(str(item.get("text", "")).split())
        if not text:
            msg(ERROR, "schema", f"{where}.text", "missing")
        clauses.append(Clause(cid, text, methods, tests, status, review))
    missing = [m for m in verification if clauses and m not in used]
    if missing:
        msg(ERROR, "clause-verification", f"{base}.verification",
            f"{', '.join(missing)} named by {rid}, but by none of its clauses in force")
    return clauses


def load_test_links(data, known: set[str], tests_file: str, messages: list[Message], label: str = REQUIREMENTS,
                    withdrawn: set[str] = frozenset()) -> dict[str, list[str]]:
    links: dict[str, list[str]] = {}
    if not isinstance(data, dict) or not isinstance(data.get("tests"), list):
        messages.append(Message(ERROR, "schema", f"{tests_file}: tests", "missing list 'tests'"))
        return links
    for entry in data["tests"]:
        if not isinstance(entry, dict) or "name" not in entry:
            continue
        name = str(entry["name"])
        value = entry.get("requirements")
        links[name] = []
        if value is None:
            continue
        where = f"{tests_file}: tests[{name}].requirements"
        if not isinstance(value, list):
            messages.append(Message(ERROR, "schema", where, "must be a list of requirement IDs"))
            continue
        for i, rid in enumerate(value):
            rid = str(rid)
            if rid in withdrawn:
                messages.append(Message(ERROR, "dead-ref", f"{where}[{i}]", f"'{rid}' is withdrawn"))
            elif rid not in known:
                messages.append(Message(ERROR, "dead-ref", f"{where}[{i}]",
                                        f"'{rid}' is not a requirement of {label}"))
            elif rid not in links[name]:
                links[name].append(rid)
    return links


def tag_comment(file: str, rid: str) -> str:
    """The tag as a comment in the language of `file`."""
    suffix = Path(file).suffix
    if suffix in (".py", ".cmake", ".yaml"):
        return f"# nx-req: {rid}"
    if suffix == ".j2":
        return f"{{# nx-req: {rid} #}}"
    return f"// nx-req: {rid}"


def scan_tags(repo: Path, config: dict) -> list[tuple[str, int, str]]:
    """(file, line, requirement ID) of every `nx-req: SSR-nnn` tag below the configured roots."""
    found: list[tuple[str, int, str]] = []
    skip_dirs = set(config["skip_dirs"])
    skip_paths = tuple(p.rstrip("/") + "/" for p in config["skip_paths"])
    suffixes = set(config["suffixes"])
    for root in config["roots"]:
        base = repo / root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix not in suffixes:
                continue
            rel = path.relative_to(repo).as_posix()
            if rel.startswith(skip_paths) or set(Path(rel).parts[:-1]) & skip_dirs:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if "nx-req" not in text:
                continue
            for number, line in enumerate(text.splitlines(), 1):
                for match in TAG.finditer(line):
                    for rid in re.split(r"\s*,\s*", match.group(1)):
                        found.append((rel, number, rid))
    return found


# ---------------------------------------------------------------------------------------------- the check


@dataclass
class _Source:
    """What the rules need, loaded either from requirements.yaml or from the StrictDoc model."""
    sources: dict[str, str]
    tag_scan: dict
    texts: dict[str, str | None]
    catalogue: Catalogue
    requirements: list[Requirement]
    known: set[str]                      # requirement IDs a test entry may name
    withdrawn: set[str]
    locate: Callable[[str], str]         # record path -> message location
    origin: str


def _load_yaml(repo: Path, req_file: str, messages: list[Message]) -> _Source | None:
    raw_text = _read(repo, req_file)
    if raw_text is None:
        messages.append(Message(ERROR, "dead-ref", req_file, "requirements file does not exist"))
        return None
    data = yaml.safe_load(raw_text) or {}
    sources = dict(DEFAULT_SOURCES)
    sources.update({k: str(v) for k, v in (data.get("sources") or {}).items()} if isinstance(data, dict) else {})
    tag_scan = dict(DEFAULT_TAG_SCAN)
    if isinstance(data, dict) and isinstance(data.get("tag_scan"), dict):
        tag_scan.update(data["tag_scan"])

    texts = {}
    for key in ("concept", "fmea", "tests"):
        texts[key] = _read(repo, sources[key])
        if texts[key] is None:
            messages.append(Message(ERROR, "dead-ref", f"{req_file}: sources.{key}",
                                    f"'{sources[key]}' does not exist"))
    catalogue = parse_catalogue(texts["concept"] or "", texts["fmea"] or "")
    if texts["concept"] is not None and (not catalogue.goals or not catalogue.mechanisms or not catalogue.aous):
        messages.append(Message(ERROR, "schema", sources["concept"],
                                "no ASG, AoU or SM table found - has the concept's structure changed?"))
    requirements = load_requirements(data, catalogue, repo, req_file, messages)
    return _Source(sources, tag_scan, texts, catalogue, requirements, {r.id for r in requirements}, set(),
                   lambda path: f"{req_file}: {path}", req_file)


# record key of requirements.yaml -> field (or relation role) of the SSR grammar, for message locations
SDOC_FIELDS = {"id": "UID", "title": "TITLE", "text": "STATEMENT", "derived_from": "RELATIONS(DerivedFrom)",
               "mechanisms": "RELATIONS(Specifies)", "findings": "FINDINGS", "stage": "STAGE", "asil": "ASIL",
               "status": "STATUS", "verification": "VERIFICATION", "code": "CODE", "note": "NOTE",
               "review": "REVIEW_STATUS"}
# record key of a clause -> field of the CLAUSE grammar (DOC-43)
SDOC_CLAUSE_FIELDS = {"id": "UID", "text": "STATEMENT", "status": "STATUS", "verification": "VERIFICATION",
                      "tests": "TESTS", "review": "REVIEW_STATUS"}


def load_sdoc_model(repo: Path, sdoc_dir: str, messages: list[Message],
                    sdoc_json: Path | None = None, cache: bool = False) -> sdocmodel.SdocModel | None:
    """StrictDoc's JSON export of `sdoc_dir` (run now, or read from `sdoc_json`); a StrictDoc error is a schema error.
    With `cache` an export of the same content from an earlier run is reused (sdocmodel.cached_export)."""
    if sdoc_json is None and cache:
        sdoc_json, errors = sdocmodel.cached_export(repo / sdoc_dir)
        if sdoc_json is None:
            for line in errors[:20]:
                messages.append(Message(ERROR, "schema", sdoc_dir, f"StrictDoc: {line}"))
            return None
    if sdoc_json is not None:
        try:
            return sdocmodel.parse_export(json.loads(Path(sdoc_json).read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            messages.append(Message(ERROR, "schema", str(sdoc_json), f"StrictDoc export not readable: {exc}"))
            return None
    with tempfile.TemporaryDirectory(prefix="nx_trace_sdoc_") as tmp:
        code, errors = sdocmodel.export(repo / sdoc_dir, Path(tmp))
        index = Path(tmp) / "json" / "index.json"
        if code != 0 or not index.is_file():
            for line in errors[:20] or [f"strictdoc export ended with exit code {code}"]:
                messages.append(Message(ERROR, "schema", sdoc_dir, f"StrictDoc: {line}"))
            return None
        return sdocmodel.parse_export(json.loads(index.read_text(encoding="utf-8")))


def _load_sdoc(repo: Path, sdoc_dir: str, messages: list[Message], sdoc_json: Path | None,
               cache: bool = False, rst: bool = False) -> _Source | None:
    sdoc_dir = sdoc_dir.replace("\\", "/").rstrip("/")
    root = repo / sdoc_dir
    if not root.is_dir():
        messages.append(Message(ERROR, "dead-ref", sdoc_dir, "StrictDoc project folder does not exist"))
        return None
    model = load_sdoc_model(repo, sdoc_dir, messages, sdoc_json, cache)
    if model is None:
        return None
    model_data = model.raw
    sources = dict(DEFAULT_SOURCES)
    texts = {}
    for key in ("concept", "fmea", "tests"):
        texts[key] = _read(repo, sources[key])
        if texts[key] is None:
            messages.append(Message(ERROR, "dead-ref", f"{sdoc_dir}: sources.{key}", f"'{sources[key]}' does not exist"))

    # the independent reading: every UID line of the .sdoc files against the export (TD1, tool classification)
    scanned = sdocmodel.scan_uids(sdocmodel.sdoc_files(root), repo)
    files = {uid: locs[0][0] for uid, locs in scanned.items()}
    for uid, locs in sorted(scanned.items()):
        for rel, line in locs[1:]:
            messages.append(Message(ERROR, "schema", f"{rel}:{line}",
                                    f"duplicate UID {uid} (first at {locs[0][0]}:{locs[0][1]})"))
        if uid not in model.uids:
            messages.append(Message(ERROR, "uid-lost", f"{locs[0][0]}:{locs[0][1]}",
                                    f"{uid} is in the .sdoc file but not in StrictDoc's export"))
    for uid in sorted(model.uids - set(scanned)):
        messages.append(Message(ERROR, "uid-lost", f"{sdoc_dir}: {uid}",
                                f"{uid} is in StrictDoc's export but in no .sdoc file below {sdoc_dir}"))

    def where(uid: str) -> str:
        return f"{files.get(uid, sdoc_dir)}: {uid}"

    # UID format per node type, node type at the end of every parent relation
    by_uid = model.by_uid()
    for node in model.nodes:
        fmt = sdocmodel.UID_FORMATS.get(node.type)
        if node.type != "SSR" and fmt is not None and not fmt.fullmatch(node.uid):   # SSR: load_requirements
            messages.append(Message(ERROR, "schema", f"{where(node.uid)}.UID",
                                    f"'{node.uid}' does not match the {node.type} format {fmt.pattern}"))
        roles: dict[str | None, int] = {}
        for rel in node.relations:
            if rel.type != "Parent":
                continue
            index = roles[rel.role] = roles.get(rel.role, -1) + 1
            if node.type == "SSR" and rel.role in (sdocmodel.ROLE_DERIVED_FROM, sdocmodel.ROLE_SPECIFIES):
                continue   # checked with the requirement (derived_from / mechanisms)
            loc = f"{where(node.uid)}.RELATIONS({rel.role or 'Parent'})[{index}]"
            allowed = sdocmodel.PARENT_TARGETS.get((node.type, rel.role))
            target = by_uid.get(rel.value)
            if allowed is None:
                messages.append(Message(ERROR, "schema", loc, f"{node.type} takes no parent relation"
                                        + (f" with the role {rel.role}" if rel.role else "")))
            elif target is None:
                messages.append(Message(ERROR, "dead-ref", loc, f"'{rel.value}' is no node of the StrictDoc model"))
            elif target.type not in allowed:
                messages.append(Message(ERROR, "schema", loc, f"'{rel.value}' is a {target.type}; the parent of a "
                                        f"{node.type} is a {' or '.join(allowed)}"))

    # the safety catalogue from the ASG / AOU / SM nodes; findings and evidence tests stay in concept.md / fmea.md
    catalogue = parse_catalogue(texts["concept"] or "", texts["fmea"] or "")
    catalogue.goals.clear()
    catalogue.aous.clear()
    catalogue.mechanisms.clear()
    for node in model.of_type(*sdocmodel.CATALOGUE_TYPES):
        catalogue.where[node.uid] = where(node.uid)
        status = node.fields.get("STATUS", "")
        if status == "withdrawn":
            catalogue.withdrawn.add(node.uid)
        elif node.type == "ASG":
            catalogue.goals[node.uid] = " ".join(node.fields.get("STATEMENT", "").split())
        elif node.type == "AOU":
            catalogue.aous[node.uid] = status == "proposed"
        else:
            stage1 = node.fields.get("STAGE") == "1"
            # CREDITED no: a stage-1 mechanism that is described but claims nothing (SM-58, SM-59; DOC-38)
            catalogue.mechanisms[node.uid] = Mechanism(node.uid, node.fields.get("TITLE", "").strip(), stage1,
                                                       stage1 and node.fields.get("CREDITED", "yes") != "no")
            if stage1 and not node.fields.get("FAULT_MODEL", "").strip():
                messages.append(Message(ERROR, "schema", f"{where(node.uid)}.FAULT_MODEL",
                                        "a stage-1 mechanism needs its fault model"))
    if not catalogue.goals or not catalogue.mechanisms or not catalogue.aous:
        messages.append(Message(ERROR, "schema", sdoc_dir, "no ASG, AOU or SM node in the StrictDoc model"))
    # the failure analysis analyses only mechanisms of the catalogue (its "#### SM-xx" sections, fmea.md section 3)
    for line_no, line in enumerate((texts["fmea"] or "").splitlines(), 1):
        head = re.match(r"^#### (SM-\d\d)\b", line)
        if head and head.group(1) not in catalogue.mechanisms:
            what = "withdrawn" if head.group(1) in catalogue.withdrawn else "no SM node of the StrictDoc model"
            messages.append(Message(ERROR, "dead-ref", f"{sources['fmea']}:{line_no}", f"{head.group(1)} is {what}"))

    # clause nodes (DOC-43): exactly one plain parent, the SSR (its type is checked above with every parent relation)
    for node in model.of_type("CLAUSE"):
        parents = node.parents(None)
        if len(parents) != 1:
            messages.append(Message(ERROR, "schema", f"{where(node.uid)}.RELATIONS(Parent)",
                                    f"a clause needs exactly one parent, its SSR ({len(parents)} given)"))

    def locate(path: str) -> str:
        match = re.fullmatch(r"requirements\[([^\]]+)\](?:\.(\w+)(.*))?", path)
        if not match:
            return f"{sdoc_dir}: {path}"
        rid, key, rest = match.groups()
        if key == "clauses":
            clause = re.fullmatch(r"\[([^\]]+)\](?:\.(\w+)(.*))?", rest)
            if clause and clause.group(1) in files:
                cid, ckey, crest = clause.groups()
                return where(cid) + (f".{SDOC_CLAUSE_FIELDS.get(ckey, ckey.upper())}{crest}" if ckey else "")
        return where(rid) + (f".{SDOC_FIELDS.get(key, key.upper())}{rest}" if key else "")

    # the field texts as RST (DOC-43): what the HTML export would refuse, found in seconds instead of minutes
    if rst and model_data is not None:
        problems = sdocmodel.rst_problems(model_data)
        for node, name, text in problems or []:
            messages.append(Message(ERROR, "rst", f"{where(node)}.{name}" if node in files else
                                    f"{sdoc_dir}: {node}.{name}", f"RST markup StrictDoc's HTML export refuses: {text}"))

    records = sdocmodel.ssr_records(model)
    withdrawn = {r["id"] for r in records if r.get("status") == "withdrawn"}
    withdrawn |= {n.uid for n in model.of_type("BSR", "MOD", "STK") if n.fields.get("STATUS") == "withdrawn"}
    records = [r for r in records if r["id"] not in withdrawn]
    requirements = load_requirements({"version": 1, "requirements": records}, catalogue, repo, sdoc_dir, messages,
                                     locate)
    known = {r.id for r in requirements} | {n.uid for n in model.of_type("BSR", "MOD") if n.uid not in withdrawn}
    return _Source(sources, dict(DEFAULT_TAG_SCAN), texts, catalogue, requirements, known, withdrawn, locate,
                   sdoc_dir)


def default_source(repo: Path) -> tuple[str | None, str | None]:
    """(sdoc, req_file): the StrictDoc project doc/requirements when the repository has it (DOC-21 step 3, the only
    source of the SSR since then), otherwise a requirements.yaml (synthetic repositories of the host test)."""
    return (SDOC_ROOT, None) if (repo / SDOC_ROOT).is_dir() else (None, REQUIREMENTS)


UNPUBLISHED_LIST = "doc/unpublished.yaml"


def unpublished_list(repo: Path, messages: list[Message]):
    """DOC-85: what an export left out on purpose, from its list doc/unpublished.yaml (format and reader:
    tools/distcheck/unpublished.py) - removed test entries, unpublished files such as the matrix. Only an exported
    tree has the list; without it (development repository) the list is empty and every check is as strict as before.
    A list the export did not write is an error (and counts as empty)."""
    import importlib.util
    reader = REPO / "tools" / "distcheck" / "unpublished.py"
    spec = importlib.util.spec_from_file_location("distcheck_unpublished", reader)
    module = sys.modules.setdefault("distcheck_unpublished", importlib.util.module_from_spec(spec))
    if not hasattr(module, "load"):
        spec.loader.exec_module(module)
    try:
        return module.load(repo, UNPUBLISHED_LIST)
    except module.UnpublishedError as exc:
        messages.append(Message(ERROR, "unpublished-list", UNPUBLISHED_LIST, str(exc)))
        return module.Unpublished()


def check(repo: Path = REPO, req_file: str | None = None, verify_matrix: bool = True, sdoc: str | None = None,
          sdoc_json: Path | None = None, cache: bool = False, rst: bool = False) -> Result:
    """All rules. Source: with `sdoc` the StrictDoc project folder (DOC-21), with `req_file` a requirements.yaml;
    neither: default_source(). `cache` reuses an earlier StrictDoc export of the same .sdoc content (doc tests).
    `rst` adds the RST check of the field texts (DOC-43; needs StrictDoc's venv, about 10 s; the command line
    `check` sets it)."""
    messages: list[Message] = []
    if not sdoc and not req_file:
        sdoc, req_file = default_source(repo)
    src = _load_sdoc(repo, sdoc, messages, sdoc_json, cache, rst) if sdoc else _load_yaml(repo, req_file, messages)
    if src is None:
        return Result(messages, Catalogue(), [], {}, dict(DEFAULT_SOURCES), False, set(), sdoc or req_file)
    sources, texts, catalogue, requirements, locate = src.sources, src.texts, src.catalogue, src.requirements, src.locate

    def at(ident: str, default: str) -> str:
        return catalogue.where.get(ident, f"{default}: {ident}")

    by_id = {r.id: r for r in requirements}
    tests_data = yaml.safe_load(texts["tests"]) if texts["tests"] else {}
    links = load_test_links(tests_data, src.known, sources["tests"], messages, src.origin, src.withdrawn)
    listed = unpublished_list(repo, messages)                    # DOC-85: empty outside an exported tree
    removed = set(listed.tests) - set(links)
    for name, rids in links.items():
        for rid in rids:
            if rid in by_id:
                by_id[rid].tests.append(name)

    # tags in the code
    for rel, line, rid in scan_tags(repo, src.tag_scan):
        if rid not in by_id:
            messages.append(Message(ERROR, "dead-ref", f"{rel}:{line}", f"tag names unknown requirement '{rid}'"))
            continue
        req = by_id[rid]
        req.tags.append(f"{rel}:{line}")
        for link in req.code:
            if link.file == rel:
                link.tagged = True

    # requirement -> test
    for req in requirements:
        base = locate(f"requirements[{req.id}]")
        if req.status == "open":
            messages.append(Message(WARNING, "req-open", base,
                                    f"open requirement (not implemented yet): {req.title}"))
            continue
        if req.tests:
            continue
        if any(t in removed for c in req.clauses if not c.withdrawn for t in c.tests):
            continue        # DOC-85: its test entries are in the development repository only (export list)
        if "test" in req.verification:
            messages.append(Message(ERROR, "req-without-test", locate(f"requirements[{req.id}].verification"),
                                    "verification names 'test', but no entry in tests.yaml names this requirement"))
        else:
            how = ", ".join(req.verification)
            messages.append(Message(WARNING, "req-without-test", base,
                                    f"requirement without test (verified by {how} only): {req.title}"))

    # clause -> test (DOC-43, finding R7 of INS-SSR-01): a clause names test entries of its requirement only; a clause
    # verified by test without an entry, and an entry of the requirement that no clause claims, are warnings
    for req in requirements:
        for clause in req.clauses:
            for i, name in enumerate(clause.tests):
                where = locate(f"requirements[{req.id}].clauses[{clause.id}].tests[{i}]")
                if name in removed:
                    continue        # DOC-85: removed by the export on purpose (listed), not a dead reference
                if name not in links:
                    messages.append(Message(ERROR, "dead-ref", where, f"test '{name}' is not in {sources['tests']}"))
                elif req.id not in links[name]:
                    messages.append(Message(ERROR, "clause-test", where, f"test '{name}' does not name {req.id} in "
                                            f"{sources['tests']} (field requirements) - a clause claims only tests "
                                            "of its requirement"))
        if req.status == "open" or not req.clauses:
            continue
        for clause in req.clauses:
            if not clause.withdrawn and "test" in clause.verification and not clause.tests:
                messages.append(Message(WARNING, "clause-without-test",
                                        locate(f"requirements[{req.id}].clauses[{clause.id}].tests"),
                                        "verification names 'test', but the clause names no test entry"))
        claimed = {t for c in req.clauses if not c.withdrawn for t in c.tests}
        for name in req.tests:
            if name not in claimed:
                messages.append(Message(WARNING, "test-not-allocated", locate(f"requirements[{req.id}]"),
                                        f"test '{name}' names {req.id} in {sources['tests']}, but no clause of it "
                                        "claims the test"))

    # suggested tags
    for req in requirements:
        for i, link in enumerate(req.code):
            if not link.tagged:
                messages.append(Message(INFO, "tag-suggested", locate(f"requirements[{req.id}].code[{i}]"),
                                        f"suggested tag '{tag_comment(link.file, req.id)}' at {link.symbol} in {link.file}"))

    # mechanisms: every stage-1 mechanism has a requirement, and a test through one of them
    for mid, mech in sorted(catalogue.mechanisms.items()):
        reqs = [r for r in requirements if mid in r.mechanisms]
        if not mech.stage1 or not mech.credited:
            continue
        if not reqs:
            messages.append(Message(ERROR, "mech-without-req", at(mid, sources["concept"]),
                                    f"stage-1 mechanism '{mech.name}' has no requirement"))
        elif not any(r.tests for r in reqs if r.status in IMPLEMENTED):
            messages.append(Message(WARNING, "mech-without-test", at(mid, sources["concept"]),
                                    f"mechanism '{mech.name}' has no test (requirements: "
                                    f"{', '.join(r.id for r in reqs)})"))

    # goals
    for gid in sorted(catalogue.goals):
        reqs = [r for r in requirements if gid in r.derived_from]
        if not reqs:
            messages.append(Message(ERROR, "goal-without-req", at(gid, sources["concept"]),
                                    "assumed safety goal without any requirement"))
        elif not any(r.status in IMPLEMENTED for r in reqs):
            messages.append(Message(WARNING, "goal-open", at(gid, sources["concept"]),
                                    f"only open requirements ({', '.join(r.id for r in reqs)}) - no stage-1 "
                                    "coverage today"))

    # tests cited as evidence in fmea.md
    for name, mechs in sorted(catalogue.evidence_tests.items()):
        where = f"{sources['fmea']}: {', '.join(mechs)}.Evidence"
        credited = [m for m in mechs if m not in catalogue.mechanisms or catalogue.mechanisms[m].credited]
        if name in removed:
            continue                # DOC-85: removed by the export on purpose (listed)
        if name not in links:
            messages.append(Message(ERROR, "dead-ref", where, f"cites test '{name}' that tests.yaml does not have"))
        elif not links[name] and credited:
            # the evidence of a mechanism marked not credited (SM-58) claims nothing, so its tests need no requirement
            messages.append(Message(ERROR, "test-without-req", f"{sources['tests']}: tests[{name}].requirements",
                                    f"test is cited as evidence for {', '.join(mechs)} in fmea.md but names no "
                                    "requirement"))

    # AoU -> safety manual
    manual = _read(repo, sources["safety_manual"])
    manual_ids = set(re.findall(r"AoU-\d\d", manual)) if manual is not None else set()
    for aid in sorted(catalogue.aous):
        if aid in manual_ids:
            continue
        where = at(aid, sources["fmea" if catalogue.aous[aid] else "concept"])
        if manual is None:
            messages.append(Message(WARNING, "aou-without-manual", where,
                                    f"no place in the safety manual ({sources['safety_manual']} does not exist yet, "
                                    "DOC-19)"))
        else:
            messages.append(Message(ERROR, "aou-without-manual", where,
                                    f"not mentioned in {sources['safety_manual']}"))

    messages.sort(key=lambda m: (SEVERITY_ORDER[m.severity], m.kind, m.where))
    result = Result(messages, catalogue, requirements, links, sources, manual is not None, manual_ids, src.origin,
                    frozenset(removed))
    if verify_matrix and not any(m.severity == ERROR for m in messages):
        current = _read(repo, sources["matrix"])
        if current is None and listed.file(sources["matrix"]):
            pass            # DOC-85: the export left the generated matrix out on purpose (listed); nothing to compare
        elif current is None or current.replace("\r\n", "\n") != render_matrix(result):
            messages.insert(0, Message(ERROR, "matrix-stale", sources["matrix"],
                                       "traceability matrix is missing or not up to date - run: "
                                       "python tools/trace matrix"))
    return result


# ---------------------------------------------------------------------------------------------- safety model for documents


@dataclass
class SafetyModel:
    """The SSR and the safety catalogue of the StrictDoc model, for the consistency tests of doc/safety (DOC-21 step 3:
    they read what used to be requirements.yaml and the tables of concept.md 2.2, 4, 6 and fmea.md 6 from here).

    requirements: the SSR in the record shape of requirements.yaml (id, title, text, derived_from, mechanisms,
                  findings, stage, asil, status, verification, code, note), whitespace of text/title/note folded to
                  single spaces like the YAML did; withdrawn ones included with their status
    goals, aous, mechanisms: UID -> the node's fields (StrictDoc field names: STATEMENT, ASIL, COVERAGE, STATUS,
                  STAGE, CREDITED, TITLE ...), in document order
    files:        UID -> the .sdoc file (relative to the repository)"""
    requirements: list[dict]
    goals: dict[str, dict]
    aous: dict[str, dict]
    mechanisms: dict[str, dict]
    files: dict[str, str]

    def stage1_mechanisms(self, credited_only: bool = False) -> list[str]:
        return [uid for uid, f in self.mechanisms.items() if f.get("STAGE") == "1" and f.get("STATUS") != "withdrawn"
                and (not credited_only or f.get("CREDITED", "yes") != "no")]


def safety_model(repo: Path = REPO, sdoc_dir: str = SDOC_ROOT) -> SafetyModel:
    """Load the model through StrictDoc's JSON export (cached per content, sdocmodel.cached_export). Raises
    RuntimeError with StrictDoc's messages when it cannot be exported (StrictDoc missing, grammar error)."""
    index, errors = sdocmodel.cached_export(repo / sdoc_dir)
    if index is None:
        raise RuntimeError(f"{sdoc_dir}: StrictDoc export failed: " + "; ".join(errors[:5]))
    model = sdocmodel.parse_export(json.loads(index.read_text(encoding="utf-8")))
    records = sdocmodel.ssr_records(model)
    for record in records:
        for key in ("title", "text", "note"):
            if key in record:
                record[key] = " ".join(str(record[key]).split())
        for key in ("derived_from", "mechanisms", "findings", "code"):
            record.setdefault(key, [])
    scanned = sdocmodel.scan_uids(sdocmodel.sdoc_files(repo / sdoc_dir), repo)

    def of(node_type: str) -> dict[str, dict]:
        return {n.uid: dict(n.fields) for n in model.of_type(node_type)}

    return SafetyModel(records, of("ASG"), of("AOU"), of("SM"),
                       {uid: locs[0][0] for uid, locs in scanned.items()})


# ---------------------------------------------------------------------------------------------- matrix


def _join(items, empty="-") -> str:
    items = list(items)
    return ", ".join(items) if items else empty


def render_matrix(result: Result) -> str:
    """The traceability matrix as Markdown. Deterministic: depends only on the sources, never on time or test runs."""
    cat, reqs, src = result.catalogue, result.requirements, result.sources
    out: list[str] = []
    add = out.append
    from_sdoc = result.origin != REQUIREMENTS
    add("# neXtBSW safety requirements - traceability matrix")
    add("")
    if from_sdoc:
        opt = "" if result.origin == SDOC_ROOT else f" --sdoc {result.origin}"
        add(f"**Generated** by `python tools/trace matrix{opt}` from the StrictDoc documents in "
            f"`{result.origin}`, [`concept.md`](concept.md) and [`fmea.md`](fmea.md) (findings, evidence tests) and "
            f"`{src['tests']}`. Do not edit by hand; `python tools/trace check{opt}` (test `safety-trace`) fails when "
            "this file is out of date.")
    else:
        add(f"**Generated** by `python tools/trace matrix` from [`requirements.yaml`](requirements.yaml), "
            f"[`concept.md`](concept.md), [`fmea.md`](fmea.md) and `{src['tests']}`. Do not edit by hand; "
            "`python tools/trace check` (test `safety-trace`) fails when this file is out of date.")
    add("")
    add("Chain: assumed safety goal (`ASG-*`) or assumption of use (`AoU-*`) -> requirement (`SSR-*`) -> "
        "mechanism (`SM-*`) -> code (file and symbol, `nx-req` tag) -> test (`tools/test/tests.yaml`, field "
        "`requirements`). A test listed here is a test *entry* that names the requirement; whether it passes is "
        "the job of the test run, not of this matrix.")
    add("")
    counts = {s: sum(1 for r in reqs if r.status == s) for s in STATUSES}
    tested = sum(1 for r in reqs if r.tests)
    tagged = sum(1 for r in reqs for c in r.code if c.tagged)
    links = sum(len(r.code) for r in reqs)
    add("## Summary")
    add("")
    add("| Item | Count |")
    add("|---|---|")
    add(f"| Requirements | {len(reqs)} ({counts['implemented']} implemented, {counts['partial']} partial, "
        f"{counts['open']} open) |")
    add(f"| Requirements with at least one test | {tested} |")
    add(f"| Code links (file + symbol) | {links}, of which carry an `nx-req` tag: {tagged} |")
    stage1 = [m for m in cat.mechanisms.values() if m.stage1 and m.credited]
    clauses = [(r, c) for r in reqs for c in r.clauses]
    if clauses:
        in_force = [(r, c) for r, c in clauses if not c.withdrawn]
        add(f"| Clause nodes | {len(clauses)} in {sum(1 for r in reqs if r.clauses)} requirements "
            f"({len(clauses) - len(in_force)} withdrawn) |")
        add(f"| Clauses with at least one test | {sum(1 for _, c in in_force if c.tests)} of "
            f"{sum(1 for r, c in in_force if 'test' in c.verification and r.status != 'open')} verified by test "
            "(open requirements left out) |")
    if any(r.review for r in reqs):
        add("| Review status | " + ", ".join(f"{s} {sum(1 for r in reqs if r.review == s)}"
                                            for s in REVIEW_STATUSES) + " |")
    add(f"| Stage-1 mechanisms | {len(stage1)} |")
    add(f"| Assumed safety goals | {len(cat.goals)} |")
    add(f"| Assumptions of use | {len(cat.aous)} "
        f"({sum(1 for p in cat.aous.values() if p)} proposed{'' if from_sdoc else ' in fmea.md'}) |")
    add("")

    add("## Requirements")
    add("")
    with_review = any(r.review for r in reqs)
    add("| ID | Requirement | From | Mechanisms | Findings | Status | Verification | Code | Tests |"
        + (" Review |" if with_review else ""))
    add("|---|---|---|---|---|---|---|---|---|" + ("---|" if with_review else ""))
    for r in reqs:
        code = "<br>".join(f"`{c.file}` `{c.symbol}`" + (" (tag)" if c.tagged else "") for c in r.code) or "-"
        tests = ", ".join(f"`{t}`" for t in r.tests) or "**none**"
        add(f"| {r.id} | **{r.title}** | {_join(r.derived_from)} | {_join(r.mechanisms)} | {_join(r.findings)} | "
            f"{r.status} | {_join(r.verification)} | {code} | {tests} |" + (f" {r.review or '-'} |" if with_review else ""))
    add("")
    if from_sdoc:
        add(f"The full text of each requirement is in the StrictDoc documents in `{result.origin}`.")
    else:
        add("The full text of each requirement is in [`requirements.yaml`](requirements.yaml).")
    add("")

    if clauses:
        add("## Clauses")
        add("")
        add("A requirement with lettered clauses has one node per clause (DOC-43); each clause names its own "
            "verification and the test entries of its requirement that verify it. Review: the clause's own review "
            "status where it differs from its requirement's.")
        add("")
        add("| Clause | Status | Verification | Tests | Review |")
        add("|---|---|---|---|---|")
        for r, c in clauses:
            if c.withdrawn:
                tests = "-"
            elif c.tests:
                tests = ", ".join(f"`{t}`" for t in c.tests)
            else:
                tests = "**none**" if "test" in c.verification and r.status != "open" else "-"
            add(f"| {c.id} | {c.status or r.status} | {_join(c.verification)} | {tests} | {c.review or '-'} |")
        add("")

    add("## Stage-1 mechanisms")
    add("")
    add("| Mechanism | Requirements | Tests |")
    add("|---|---|---|")
    for mid in sorted(m.id for m in stage1):
        mreqs = [r for r in reqs if mid in r.mechanisms]
        tests = sorted({t for r in mreqs if r.status in IMPLEMENTED for t in r.tests})
        add(f"| {mid} {cat.mechanisms[mid].name} | {_join(r.id for r in mreqs)} | "
            f"{_join(('`' + t + '`' for t in tests), '**no test**')} |")
    add("")
    device = sorted(m.id for m in cat.mechanisms.values() if not m.stage1)
    add(f"Device-dependent mechanisms ({_join(device)}) are stage 2 (concept section 1.4, 13) and are not credited "
        "by any stage-1 requirement; the check refuses such a link.")
    optional = sorted(m.id for m in cat.mechanisms.values() if m.stage1 and not m.credited)
    if optional:
        add("")
        add(f"Stage-1 mechanisms marked *not credited* in the concept catalogue ({_join(optional)}) are described "
            "but claim nothing; they need no requirement, and the check refuses a requirement that credits them.")
    add("")

    add("## Assumed safety goals")
    add("")
    add("| Goal | Requirements (implemented / partial) | Open requirements |")
    add("|---|---|---|")
    for gid in sorted(cat.goals):
        greqs = [r for r in reqs if gid in r.derived_from]
        done = [r.id for r in greqs if r.status in IMPLEMENTED]
        todo = [r.id for r in greqs if r.status == "open"]
        add(f"| {gid} | {_join(done, '**none**')} | {_join(todo)} |")
    add("")

    add("## Assumptions of use")
    add("")
    manual = src["safety_manual"]
    if result.manual_exists:
        add(f"Place in the safety manual: `{manual}` mentions the ID.")
    else:
        add(f"The safety manual `{manual}` does not exist yet (DOC-19); every AoU is reported as a warning until it "
            "has a place there.")
    add("")
    add("| AoU | Requirements derived from it | In the safety manual |")
    add("|---|---|---|")
    for aid in sorted(cat.aous):
        areqs = [r.id for r in reqs if aid in r.derived_from]
        label = aid + ((" (proposed)" if from_sdoc else " (proposed, fmea.md)") if cat.aous[aid] else "")
        add(f"| {label} | {_join(areqs)} | {'yes' if aid in result.manual_ids else '**no**'} |")
    add("")

    add("## Evidence tests of fmea.md")
    add("")
    add("| Test | Cited for | Requirements |")
    add("|---|---|---|")
    for name in sorted(cat.evidence_tests):
        add(f"| `{name}` | {_join(cat.evidence_tests[name])} | {_join(result.tests.get(name, []), '**none**')} |")
    add("")

    findings = [m for m in result.messages if m.severity in (ERROR, WARNING) and m.kind != "matrix-stale"]
    add("## Open trace findings")
    add("")
    if not findings:
        add("None.")
    else:
        add("| Severity | Kind | Where | Finding |")
        add("|---|---|---|---|")
        for m in findings:
            add(f"| {m.severity} | {m.kind} | `{m.where}` | {m.text.replace('|', '/')} |")
    add("")
    return "\n".join(out)


# ---------------------------------------------------------------------------------------------- command line


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python tools/trace", description=__doc__.split("\n\n")[0])
    parser.add_argument("command", nargs="?", default="check", choices=["check", "matrix", "tags", "export", "html"])
    parser.add_argument("--repo", type=Path, default=REPO, help="repository root (default: this repository)")
    parser.add_argument("--requirements", metavar="FILE", help="check/matrix/tags on a requirements.yaml relative to "
                        f"--repo (the format before DOC-21 step 3, e.g. {REQUIREMENTS}) instead of StrictDoc")
    parser.add_argument("--sdoc", metavar="DIR", help="check/matrix/tags on the StrictDoc project DIR (relative to "
                        f"--repo; default {SDOC_ROOT} when it exists)")
    parser.add_argument("--sdoc-json", type=Path, metavar="FILE",
                        help="with --sdoc: use this StrictDoc JSON export instead of running strictdoc")
    parser.add_argument("--strict", action="store_true", help="check: warnings also fail")
    parser.add_argument("--info", action="store_true", help="check: also print info messages (suggested tags)")
    parser.add_argument("--out", type=Path, help="matrix: output file (default: sources.matrix); export/html: "
                        "output folder")
    args = parser.parse_args(argv)
    repo = args.repo.resolve()
    if args.sdoc_json and not args.sdoc:
        parser.error("--sdoc-json needs --sdoc")
    if args.sdoc and args.requirements:
        parser.error("--sdoc and --requirements exclude each other")

    if args.command in ("export", "html"):
        if not args.out:
            parser.error(f"{args.command} needs --out DIR")
        project = repo / (args.sdoc or SDOC_ROOT)
        html = args.command == "html"
        code, errors = sdocmodel.export(project, args.out.resolve(), "html" if html else "json", source_scan=html)
        for line in errors:
            print(line)
        target = args.out / ("html" if html else "json/index.json")
        print(f"strictdoc export {'html with source scan' if html else 'json'} of {project}: "
              f"{'ok, ' + str(target) if code == 0 else f'exit code {code}'}")
        return 1 if code else 0

    if args.command == "matrix":
        result = check(repo, args.requirements, verify_matrix=False, sdoc=args.sdoc, sdoc_json=args.sdoc_json)
        out = args.out or (repo / result.sources["matrix"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_matrix(result), encoding="utf-8", newline="\n")
        errors = result.count(ERROR)
        print(f"{out}: {len(result.requirements)} requirements, {errors} errors, {result.count(WARNING)} warnings")
        for m in result.messages:
            if m.severity == ERROR:
                print(m)
        return 1 if errors else 0

    result = check(repo, args.requirements, sdoc=args.sdoc, sdoc_json=args.sdoc_json, rst=args.command == "check")
    if args.command == "tags":
        for req in result.requirements:
            for link in req.code:
                if not link.tagged:
                    print(f"{link.file}\t{link.symbol}\t{tag_comment(link.file, req.id)}")
        return 0

    shown = [m for m in result.messages if m.severity != INFO or args.info]
    for m in shown:
        print(m)
    errors, warnings = result.count(ERROR), result.count(WARNING)
    print(f"trace: {len(result.requirements)} requirements, {sum(len(r.tests) for r in result.requirements)} "
          f"requirement-test links, {errors} errors, {warnings} warnings, {result.count(INFO)} suggested tags")
    return 1 if errors or (args.strict and warnings) else 0
