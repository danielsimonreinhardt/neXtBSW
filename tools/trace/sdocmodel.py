# SPDX-License-Identifier: Apache-2.0
"""The StrictDoc model of the neXtBSW requirements for tools/trace (DOC-21).

StrictDoc (pinned in tools/requirements-docs.txt) reads the .sdoc files and checks what its grammar can express:
field names, choices, required fields, unique UIDs, relation roles and that every parent exists. tools/trace reads
StrictDoc's **JSON export** (the documented export format, not StrictDoc's Python API) and checks the domain rules
on top. This module:

    find_strictdoc()      the strictdoc executable: NX_STRICTDOC_VENV, the CI tool cache, next to the interpreter,
                          PATH (StrictDoc lives in its own venv, `python -m tools.ci setup --tool strictdoc`)
    export()              runs `strictdoc export` into a folder and returns the exit code and the error lines
    cached_export()       the JSON export of a project, reused while the content of the project folder and the
                          StrictDoc executable are the same (for the document tests of doc/safety, which read the
                          safety catalogue and the SSR several times per run; the CI check exports afresh)
    parse_export()        the JSON dict -> SdocModel (nodes with fields and relations, in document order)
    scan_uids()           an independent reading of the .sdoc files: every `UID:` line outside a multi-line value,
                          with file and line - the UID cross-check (TD1 of the tool classification) and the location
                          of every message
    ssr_records()         the SSR nodes in the record shape of doc/safety/requirements.yaml, so that the loader of
                          tracecheck stays the same for both sources; since DOC-43 with the review status and the
                          CLAUSE nodes of each SSR
    rst_problems()        the field texts StrictDoc's HTML export would refuse as RST, checked with the docutils of
                          the StrictDoc venv in one run (seconds instead of the minutes of the HTML export)

No StrictDoc import, no Qt, no dependency beyond the standard library: neXtBSW Studio can call it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

STRICTDOC_VERSION = "0.30.1"

# UID formats per node type (migration plan section 3). The area list of BSR is closed.
BSR_AREAS = ("COM", "DIAG", "NVM", "IO", "MODE", "SAFE", "RTE", "BL", "PLAT", "PORT", "GEN", "STUDIO", "TOOL")
UID_FORMATS = {
    "STK": re.compile(r"STK-\d{3}"),
    "BSR": re.compile(r"BSR-(?:" + "|".join(BSR_AREAS) + r")-\d{3}"),
    "MOD": re.compile(r"MOD-[A-Z][A-Z0-9]*-\d{3}"),
    "SSR": re.compile(r"SSR-\d{3}"),
    "ASG": re.compile(r"ASG-\d{2}"),
    "AOU": re.compile(r"AoU-\d{2}"),
    "SM": re.compile(r"SM-\d{2}"),
    "CLAUSE": re.compile(r"SSR-\d{3}\([a-z]\)"),       # DOC-43: one lettered clause of an SSR
}
REQUIREMENT_TYPES = ("STK", "BSR", "MOD", "SSR")
CATALOGUE_TYPES = ("ASG", "AOU", "SM")

# Relation roles of the SSR (grammar doc/requirements/nextbsw.sgra) and the node types they may point at.
ROLE_DERIVED_FROM = "DerivedFrom"
ROLE_SPECIFIES = "Specifies"
PARENT_TARGETS = {
    ("SSR", None): ("BSR",),
    ("SSR", ROLE_DERIVED_FROM): ("ASG", "AOU"),
    ("SSR", ROLE_SPECIFIES): ("SM",),
    ("BSR", None): ("STK",),
    ("MOD", None): ("BSR",),
    ("CLAUSE", None): ("SSR",),
}

_UID_LINE = re.compile(r"^UID:\s*(\S+)\s*$")
_BLOCK_START = re.compile(r"^[A-Z_0-9]+:\s*>>>\s*$")


@dataclass
class Relation:
    type: str            # Parent, Child, File
    value: str           # UID of the other node, or the file path
    role: str | None = None


@dataclass
class SdocNode:
    type: str                       # grammar element: SSR, BSR, ASG, ...
    uid: str
    fields: dict[str, str]
    relations: list[Relation] = field(default_factory=list)
    document: str = ""              # title of the document it is in

    def parents(self, role: str | None = "*") -> list[str]:
        """UIDs of the Parent relations; role "*" = every role, None = only relations without a role."""
        return [r.value for r in self.relations if r.type == "Parent" and (role == "*" or r.role == role)]


@dataclass
class SdocModel:
    nodes: list[SdocNode]
    documents: list[str]
    uids: set[str]                  # every UID of the export: documents, sections, nodes
    raw: dict = field(default_factory=dict, repr=False, compare=False)   # the JSON export itself (RST check)

    def of_type(self, *types: str) -> list[SdocNode]:
        return [n for n in self.nodes if n.type in types]

    def by_uid(self) -> dict[str, SdocNode]:
        return {n.uid: n for n in self.nodes}


# ---------------------------------------------------------------------------------------------- running StrictDoc


def default_venv() -> Path:
    """The StrictDoc venv in the CI tool cache (same rule as tools/ci default_cache)."""
    if os.environ.get("NX_STRICTDOC_VENV"):
        return Path(os.environ["NX_STRICTDOC_VENV"])
    if os.environ.get("NX_CI_CACHE"):
        cache = Path(os.environ["NX_CI_CACHE"])
    elif os.name == "nt":
        cache = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "nextbsw" / "ci-cache"
    else:
        cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "nextbsw" / "ci-cache"
    return cache / "strictdoc-venv"


def find_strictdoc() -> str | None:
    names = ("strictdoc.exe", "strictdoc") if os.name == "nt" else ("strictdoc",)
    for folder in (default_venv() / ("Scripts" if os.name == "nt" else "bin"), Path(sys.executable).parent):
        for name in names:
            if (folder / name).is_file():
                return str(folder / name)
    return shutil.which("strictdoc")


def export(input_dir: Path, out_dir: Path, formats: str = "json", source_scan: bool = False,
           strictdoc: str | None = None, timeout: int = 1200) -> tuple[int, list[str]]:
    """`strictdoc export` of the project in `input_dir` (its strictdoc_config.py) into `out_dir`.

    Returns (exit code, error lines). The JSON lands in out_dir/json/index.json. StrictDoc writes the JSON with the
    locale's encoding, hence PYTHONUTF8=1."""
    exe = strictdoc or find_strictdoc()
    if exe is None:
        return 127, [f"strictdoc {STRICTDOC_VERSION} not found (NX_STRICTDOC_VENV, {default_venv()}, next to the "
                     "interpreter, PATH) - install it with: python -m tools.ci setup --tool strictdoc"]
    env = {**os.environ, "PYTHONUTF8": "1"}
    env.pop("NX_SDOC_SOURCE_SCAN", None)
    if source_scan:
        env["NX_SDOC_SOURCE_SCAN"] = "1"
    argv = [exe, "export", str(input_dir), "--config", str(input_dir), "--formats", formats,
            "--no-parallelization", "--output-dir", str(out_dir)]
    try:
        run = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
                             timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, [f"strictdoc export timed out after {timeout} s"]
    lines = (run.stdout + run.stderr).splitlines()
    errors = [line.strip() for line in lines if re.search(r"error|exception|traceback", line, re.I)]
    if run.returncode and not errors:
        errors = [line.strip() for line in lines[-5:] if line.strip()]
    return run.returncode, errors


def cached_export(input_dir: Path, strictdoc: str | None = None) -> tuple[Path | None, list[str]]:
    """(path of index.json, error lines) of the JSON export of `input_dir`, from a cache in the temp folder keyed by
    the content of every file below `input_dir` and by the StrictDoc executable. A failed export is not cached."""
    exe = strictdoc or find_strictdoc()
    if exe is None:
        return None, [f"strictdoc {STRICTDOC_VERSION} not found (NX_STRICTDOC_VENV, {default_venv()}, next to the "
                      "interpreter, PATH) - install it with: python -m tools.ci setup --tool strictdoc"]
    digest = hashlib.sha256(f"{STRICTDOC_VERSION}|{exe}|{os.path.getmtime(exe)}".encode())
    for path in sorted(input_dir.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            digest.update(path.relative_to(input_dir).as_posix().encode() + b"|" + path.read_bytes() + b"|")
    cache = Path(tempfile.gettempdir()) / "nx_trace_sdoc_cache" / digest.hexdigest()[:20]
    index = cache / "json" / "index.json"
    if index.is_file():
        return index, []
    cache.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=cache.name + "_", dir=cache.parent))
    code, errors = export(input_dir, work, strictdoc=exe)
    if code != 0 or not (work / "json" / "index.json").is_file():
        shutil.rmtree(work, ignore_errors=True)
        return None, errors or [f"strictdoc export ended with exit code {code}"]
    try:
        work.rename(cache)
    except OSError:                       # a parallel run was faster - its export is the same
        shutil.rmtree(work, ignore_errors=True)
    return index, []


# ---------------------------------------------------------------------------------------------- the JSON model


def parse_export(data: dict) -> SdocModel:
    """StrictDoc's JSON export (index.json) -> SdocModel. Composite SECTION nodes are walked, TEXT nodes skipped."""
    nodes: list[SdocNode] = []
    uids: set[str] = set()
    titles: list[str] = []

    def walk(container: dict, document: str) -> None:
        for raw in container.get("NODES", []) or []:
            if not isinstance(raw, dict):
                continue
            node_type = str(raw.get("_NODE_TYPE", ""))
            uid = raw.get("UID")
            if uid:
                uids.add(str(uid))
            if node_type not in ("SECTION", "TEXT") and uid:
                fields = {k: str(v) for k, v in raw.items()
                          if not k.startswith("_") and k not in ("NODES", "RELATIONS", "MID")}
                relations = [Relation(str(r.get("TYPE", "")), str(r.get("VALUE", "")), r.get("ROLE"))
                             for r in raw.get("RELATIONS", []) or [] if isinstance(r, dict)]
                nodes.append(SdocNode(node_type, str(uid), fields, relations, document))
            walk(raw, document)

    for doc in data.get("DOCUMENTS", []) if isinstance(data, dict) else []:
        title = str(doc.get("TITLE", ""))
        titles.append(title)
        if doc.get("UID"):
            uids.add(str(doc["UID"]))
        walk(doc, title)
    return SdocModel(nodes, titles, uids, data if isinstance(data, dict) else {})


def split_list(value: str | None) -> list[str]:
    """A Tag or MultipleChoice value as StrictDoc exports it ("a, b") -> ["a", "b"]."""
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def clause_letter(uid: str) -> str:
    """'SSR-001(a)' -> 'a'; '?' for a UID that is not a clause UID."""
    match = re.search(r"\(([a-z])\)$", uid)
    return match.group(1) if match else "?"


def ssr_records(model: SdocModel) -> list[dict]:
    """The SSR nodes in the record shape of doc/safety/requirements.yaml (lossless mapping, plan section 4).

    DERIVED_FROM and MECHANISMS are relations now (Parent with the role DerivedFrom / Specifies); a plain Parent
    (the BSR the SSR constrains) goes to the extra key `parents`, which the loader passes through.

    DOC-43 (step 3b): REVIEW_STATUS -> `review`; the CLAUSE nodes whose parent is the SSR -> `clauses` (list of
    {id, text, verification, tests, status, review, note}, in document order). A requirement without a STATEMENT of
    its own gets its text back from its clauses in force: "(a) <text> (b) <text> ...", the form of the clause list
    before step 3b (a withdrawn clause is left out). A requirement with clauses and a STATEMENT keeps the
    STATEMENT; the check refuses that combination (key `statement_and_clauses`)."""
    clauses_of: dict[str, list[dict]] = {}
    for node in model.of_type("CLAUSE"):
        f = node.fields
        clause = {"id": node.uid, "text": f.get("STATEMENT", ""), "verification": split_list(f.get("VERIFICATION")),
                  "tests": split_list(f.get("TESTS")), "status": f.get("STATUS", ""),
                  "review": f.get("REVIEW_STATUS", ""), "note": f.get("NOTE", "")}
        for parent in node.parents(None)[:1]:
            clauses_of.setdefault(parent, []).append(clause)
    records = []
    for node in model.of_type("SSR"):
        f = node.fields
        record: dict = {"id": node.uid}
        for key, name in (("title", "TITLE"), ("text", "STATEMENT"), ("asil", "ASIL"), ("status", "STATUS"),
                          ("note", "NOTE"), ("review", "REVIEW_STATUS")):
            if f.get(name):
                record[key] = f[name]
        clauses = clauses_of.get(node.uid, [])
        if clauses:
            record["clauses"] = clauses
            if "text" in record:
                record["statement_and_clauses"] = True
            else:
                record["text"] = "\n".join(f"({clause_letter(c['id'])}) {c['text']}" for c in clauses
                                           if c["status"] != "withdrawn")
        stage = f.get("STAGE")
        if stage is not None:
            record["stage"] = int(stage) if stage.isdigit() else stage
        record["derived_from"] = node.parents(ROLE_DERIVED_FROM)
        record["mechanisms"] = node.parents(ROLE_SPECIFIES)
        if f.get("FINDINGS"):
            record["findings"] = split_list(f["FINDINGS"])
        record["verification"] = split_list(f.get("VERIFICATION"))
        if f.get("CODE"):
            record["code"] = []
            for line in f["CODE"].splitlines():
                if line.strip():
                    file, _, symbol = line.partition("::")
                    record["code"].append({"file": file.strip(), "symbol": symbol.strip()})
        records.append(record)
    return records


# ---------------------------------------------------------------------------------------------- RST of the field texts

# Rendered by StrictDoc as RST in the HTML export; TITLE is shown as plain text, UID/MID are identifiers.
RST_SKIP_FIELDS = ("UID", "MID", "TITLE")

# Runs in the interpreter of the StrictDoc venv (docutils is StrictDoc's dependency, not one of the repository's
# .venv). StrictDoc's RstToHtmlFragmentWriter calls publish_parts(writer="html") per field and fails the HTML export
# on any line of the warning stream; publish_parts builds its settings anew for every call (about 15 ms each), so
# the same components - standalone reader, RST parser, HTML writer with their transforms - run here with one settings
# object, and the writer translates every document as publish_parts would.
_RST_SCRIPT = r"""
import io, json, sys
from docutils.frontend import get_default_settings
from docutils.io import StringOutput
from docutils.parsers.rst import Parser
from docutils.readers.standalone import Reader
from docutils.utils import new_document
from docutils.writers.html4css1 import Writer
reader, parser, writer = Reader(), Parser(), Writer()
settings = get_default_settings(Reader, Parser, Writer)
settings.syntax_highlight = "short"
out = []
for key, text in json.load(sys.stdin):
    stream = io.StringIO()
    settings.warning_stream = stream
    try:
        document = new_document("<field>", settings)
        parser.parse(text, document)
        document.transformer.populate_from_components((reader, parser, writer))
        document.transformer.apply_transforms()
        writer.write(document, StringOutput(encoding="unicode"))
    except Exception as exc:
        stream.write(str(exc))
    if stream.getvalue().strip():
        out.append([key, " ".join(stream.getvalue().split())])
json.dump(out, sys.stdout)
"""


def rst_fragments(data: dict) -> list[tuple[str, str, str]]:
    """(node UID or document/section title, field, text) of every String field StrictDoc renders as RST - every node
    with a grammar element, TEXT nodes included; choice and tag fields are no RST."""
    out: list[tuple[str, str, str]] = []
    for doc in data.get("DOCUMENTS", []) if isinstance(data, dict) else []:
        strings = {}
        for element in (doc.get("GRAMMAR") or {}).get("ELEMENTS", []) or []:
            strings[element.get("NODE_TYPE")] = {f.get("TITLE") for f in element.get("FIELDS", [])
                                                 if f.get("TYPE") == "String"}

        def walk(container: dict, where: str) -> None:
            for raw in container.get("NODES", []) or []:
                if not isinstance(raw, dict):
                    continue
                node_type = str(raw.get("_NODE_TYPE", ""))
                name = str(raw.get("UID") or f"{where} > {raw.get('TITLE') or node_type}")
                for key in sorted(strings.get(node_type, set()) - set(RST_SKIP_FIELDS)):
                    if isinstance(raw.get(key), str) and raw[key].strip():
                        out.append((name, key, raw[key]))
                walk(raw, name if raw.get("UID") or node_type == "SECTION" else where)

        walk(doc, str(doc.get("TITLE", "")))
    return out


def rst_python(strictdoc: str | None = None) -> str | None:
    """The interpreter of StrictDoc's venv (next to the strictdoc executable), or None."""
    exe = strictdoc or find_strictdoc()
    if exe is None:
        return None
    for name in (("python.exe",) if os.name == "nt" else ("python3", "python")):
        candidate = Path(exe).parent / name
        if candidate.is_file():
            return str(candidate)
    return None


def rst_problems(data: dict, strictdoc: str | None = None, timeout: int = 300) -> list[tuple[str, str, str]] | None:
    """(node, field, docutils message) of every field text StrictDoc would refuse when it renders the HTML export -
    the fast check (seconds) instead of the slow HTML run (minutes). None when no StrictDoc venv is found."""
    python = rst_python(strictdoc)
    if python is None:
        return None
    fragments = rst_fragments(data)
    payload = json.dumps([[i, text] for i, (_, _, text) in enumerate(fragments)])
    run = subprocess.run([python, "-c", _RST_SCRIPT], input=payload, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=timeout, env={**os.environ, "PYTHONUTF8": "1"})
    if run.returncode != 0:
        return [("", "", f"RST check failed: {' '.join(run.stderr.split()[-30:])}")]
    return [(fragments[i][0], fragments[i][1], message) for i, message in json.loads(run.stdout or "[]")]


# ---------------------------------------------------------------------------------------------- independent UID scan


def sdoc_files(root: Path, exclude_dirs: tuple[str, ...] = ("poc",)) -> list[Path]:
    """Every .sdoc file below `root`, without the excluded folders (same exclusion as strictdoc_config.py)."""
    files = []
    for path in sorted(root.rglob("*.sdoc")):
        rel_parts = path.relative_to(root).parts[:-1]
        if not set(rel_parts) & set(exclude_dirs):
            files.append(path)
    return files


def scan_uids(files: list[Path], repo: Path) -> dict[str, list[tuple[str, int]]]:
    """{UID: [(file relative to repo, line)]} of every `UID:` line outside a multi-line value (>>> ... <<<)."""
    found: dict[str, list[tuple[str, int]]] = {}
    for path in files:
        try:
            rel = path.relative_to(repo).as_posix()
        except ValueError:
            rel = path.as_posix()
        in_block = False
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if in_block:
                if line.strip() == "<<<":
                    in_block = False
                continue
            if _BLOCK_START.match(line):
                in_block = True
                continue
            match = _UID_LINE.match(line)
            if match:
                found.setdefault(match.group(1), []).append((rel, number))
    return found
