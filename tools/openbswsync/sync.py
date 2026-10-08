# SPDX-License-Identifier: Apache-2.0
"""Core of the OpenBSW copy check (TOOL-21). No Qt, no git except reading the submodule commit.

Model
-----
copies.yaml names the submodule and a list of roots. A root maps a directory of this repository (``path``) to a
directory of the submodule (``original``); a file ``<path>/<rel>`` is a copy of ``<submodule>/<original>/<rel>``.
Entries are keyed by ``rel``, so moving a root (PLAT-54: family port to ecu/platforms/<family>) only changes
``path``. Roots may be nested; a file belongs to the root with the longest matching path.

Entry status:
  identical  the copy equals the original (CRLF normalized); ``upstream`` is the hash of the original when recorded
  modified   the copy differs on purpose; needs ``task`` (task id) and ``reason``; ``upstream`` as above
  new        no original; key may be a glob (``uds/**``); ``task``/``reason`` optional

Findings (error = red):
  error    unlisted-copy               file has an original but no entry
  error    identical-changed           identical entry, copy differs, original unchanged since recorded
  error    upstream-changed            identical entry, original changed since recorded (submodule update)
  error    upstream-changed-modified   modified entry, original changed since recorded
  error    upstream-and-copy-changed   identical entry, copy and original both differ from the record
  error    modified-without-reason     modified entry without task or reason
  error    original-gone               identical/modified entry, original no longer in the submodule
  error    new-has-original            new entry, but the submodule has a file at the same place
  error    stale-entry                 exact entry without file in the repository
  error    list-invalid                structural error in copies.yaml, root or original directory missing
  warning  unlisted-new                file without original and without entry or pattern
  warning  modified-now-identical      modified entry, copy equals the original again
  warning  hash-outdated               identical entry, copy follows the new original, record still old
  warning  unused-pattern              new pattern that matches no file
  warning  submodule-commit            submodule commit differs from the one the list was recorded against
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

import yaml

LIST_VERSION = 1
HASH_LEN = 16
STATUSES = ("identical", "modified", "new")
STATUS_DE = {"identical": "identisch", "modified": "geändert", "new": "neu"}
# task ids of doc/todo.yaml (PLAT-23, IO-16) or the milestones of the early project phase
TASK_RE = re.compile(r"^(?:[A-Z][A-Z0-9]*-\d+|Meilenstein \d+)$")
HASH_RE = re.compile(r"^[0-9a-f]{%d}$" % HASH_LEN)
ALWAYS_IGNORED_DIRS = {"__pycache__", ".pytest_cache"}
ALWAYS_IGNORED_SUFFIXES = (".pyc", ".pyo")


class ListError(Exception):
    """copies.yaml cannot be read at all (file missing, no YAML mapping)."""


# ------------------------------------------------------------------------------------------------ helpers


def normalized_hash(path: Path) -> str:
    """SHA-256 (first 16 hex digits) of the file with CRLF normalized to LF, as compared in PLAT-52."""
    return hashlib.sha256(_normalized(path)).hexdigest()[:HASH_LEN]


def _normalized(path: Path) -> bytes:
    return path.read_bytes().replace(b"\r\n", b"\n")


def format_yaml_path(parts: Iterable[str | int]) -> str:
    """('roots', 0, 'files', 'a/b.h', 'reason') -> roots[0].files["a/b.h"].reason"""
    out = ""
    for part in parts:
        if isinstance(part, int):
            out += f"[{part}]"
        elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", part):
            out += f".{part}" if out else part
        else:
            out += f'["{part}"]'
    return out


def glob_to_regex(pattern: str) -> re.Pattern:
    """Glob on posix paths: ** crosses directories, * and ? stay inside one path element."""
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(out + r"\Z")


def is_pattern(key: str) -> bool:
    return any(c in key for c in "*?[")


def _walk(base: Path, skip: Iterable[Path] = ()) -> dict[str, Path]:
    """All files below base as {posix relative path: path}; directories in skip are left out."""
    skip_resolved = {s.resolve() for s in skip}
    files: dict[str, Path] = {}
    if not base.is_dir():
        return files
    for dirpath, dirnames, filenames in os.walk(base):
        current = Path(dirpath)
        dirnames[:] = sorted(d for d in dirnames if d not in ALWAYS_IGNORED_DIRS
                             and not (skip_resolved and (current / d).resolve() in skip_resolved))
        prefix = Path(os.path.relpath(dirpath, base)).as_posix()
        prefix = "" if prefix == "." else prefix + "/"
        for name in filenames:
            if not name.endswith(ALWAYS_IGNORED_SUFFIXES):
                files[prefix + name] = current / name
    return files


def submodule_commit(submodule: Path) -> str | None:
    """HEAD of the submodule checkout, None if it is no git checkout (synthetic test trees) or git is missing."""
    if not (submodule / ".git").exists():
        return None
    try:
        result = subprocess.run(["git", "-C", str(submodule), "rev-parse", "HEAD"], capture_output=True, text=True,
                                timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    sha = result.stdout.strip()
    return sha if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", sha) else None


# ------------------------------------------------------------------------------------------------ list model


@dataclass
class Finding:
    severity: str                # error | warning
    code: str
    text: str
    path: str | None = None      # repository path (posix) of the file concerned
    yaml: str = ""               # YAML path in copies.yaml
    hint: str = ""

    def __str__(self) -> str:
        where = self.path or ""
        if self.yaml:
            where += f" ({self.yaml})" if where else self.yaml
        out = f"{where}: " if where else ""
        out += f"{'FEHLER' if self.severity == 'error' else 'WARNUNG'} [{self.code}] {self.text}"
        return out + (f"\n    -> {self.hint}" if self.hint else "")


@dataclass
class Entry:
    key: str
    status: str
    upstream: str | None = None
    task: list[str] = field(default_factory=list)
    reason: str = ""
    yaml: tuple = ()

    @property
    def pattern(self) -> bool:
        return is_pattern(self.key)


@dataclass
class Root:
    name: str
    path: str                    # repository directory (posix, relative to the repository)
    original: str                # directory inside the submodule (posix)
    exclude: list[str] = field(default_factory=list)
    entries: dict[str, Entry] = field(default_factory=dict)
    index: int = 0

    def exact(self, rel: str) -> Entry | None:
        entry = self.entries.get(rel)
        return entry if entry is not None and not entry.pattern else None

    def matching_pattern(self, rel: str) -> Entry | None:
        for entry in self.entries.values():
            if entry.pattern and glob_to_regex(entry.key).match(rel):
                return entry
        return None


@dataclass
class CopyList:
    file: Path
    submodule: str
    commit: str | None
    roots: list[Root]
    findings: list[Finding]      # structural findings of loading


def _task_list(value) -> list[str] | None:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return list(value)
    return None


def load_list(file: Path) -> CopyList:
    """Read copies.yaml. Structural problems become list-invalid findings; ListError only if nothing is usable."""
    if not file.is_file():
        raise ListError(f"Liste nicht gefunden: {file}")
    try:
        # BaseLoader keeps every scalar a string: a hash like 0123456789012345 or 12e4... must not become a number
        data = yaml.load(file.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    except yaml.YAMLError as exc:
        raise ListError(f"{file}: kein gültiges YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ListError(f"{file}: erwartet eine YAML-Abbildung mit version, openbsw und roots")

    findings: list[Finding] = []

    def invalid(parts, text, hint=""):
        findings.append(Finding("error", "list-invalid", text, yaml=format_yaml_path(parts), hint=hint))

    if data.get("version") != str(LIST_VERSION):
        invalid(("version",), f"version muss {LIST_VERSION} sein, ist {data.get('version')!r}")
    openbsw = data.get("openbsw") or {}
    submodule = openbsw.get("path") if isinstance(openbsw, dict) else None
    if not isinstance(submodule, str) or not submodule:
        invalid(("openbsw", "path"), "Pfad des Submoduls fehlt")
        submodule = "external/openbsw"
    commit = openbsw.get("commit") if isinstance(openbsw, dict) else None
    if commit is not None and not (isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{7,40}", commit)):
        invalid(("openbsw", "commit"), f"kein Commit-Hash: {commit!r}")
        commit = None

    roots: list[Root] = []
    raw_roots = data.get("roots")
    if not isinstance(raw_roots, list) or not raw_roots:
        invalid(("roots",), "Liste der Wurzeln (roots) fehlt oder ist leer")
        raw_roots = []
    names: set[str] = set()
    for i, raw in enumerate(raw_roots):
        if not isinstance(raw, dict):
            invalid(("roots", i), "Wurzel muss eine Abbildung sein (name, path, original, files)")
            continue
        missing = [k for k in ("name", "path", "original") if not isinstance(raw.get(k), str) or not raw.get(k)]
        if missing:
            invalid(("roots", i), f"Wurzel ohne {', '.join(missing)}")
            continue
        if raw["name"] in names:
            invalid(("roots", i, "name"), f"Name der Wurzel doppelt: {raw['name']}")
        names.add(raw["name"])
        exclude = raw.get("exclude") or []
        if not isinstance(exclude, list) or not all(isinstance(e, str) for e in exclude):
            invalid(("roots", i, "exclude"), "exclude muss eine Liste von Glob-Mustern sein")
            exclude = []
        root = Root(raw["name"], raw["path"].strip("/"), raw["original"].strip("/"), exclude, index=i)
        files = raw.get("files") or {}
        if not isinstance(files, dict):
            invalid(("roots", i, "files"), "files muss eine Abbildung <relativer Pfad>: <Eintrag> sein")
            files = {}
        for key, value in files.items():
            parts = ("roots", i, "files", str(key))
            if not isinstance(value, dict):
                invalid(parts, "Eintrag muss eine Abbildung mit status sein")
                continue
            status = value.get("status")
            if status not in STATUSES:
                invalid(parts + ("status",), f"status muss einer von {', '.join(STATUSES)} sein, ist {status!r}")
                continue
            tasks = _task_list(value.get("task"))
            if tasks is None:
                invalid(parts + ("task",), "task muss eine Aufgaben-ID oder eine Liste davon sein")
                tasks = []
            for t in tasks:
                if not TASK_RE.match(t):
                    invalid(parts + ("task",), f"keine Aufgaben-ID: {t!r}",
                            hint="Form wie in doc/todo.yaml (PLAT-23) oder 'Meilenstein <n>'")
            reason = value.get("reason") or ""
            if not isinstance(reason, str):
                invalid(parts + ("reason",), "reason muss Text sein")
                reason = ""
            upstream = value.get("upstream")
            entry = Entry(str(key), status, upstream, tasks, reason.strip(), parts)
            if status == "new":
                if upstream is not None:
                    invalid(parts + ("upstream",), "Eintrag 'new' hat kein Original und damit keinen upstream-Hash")
            else:
                if is_pattern(entry.key):
                    invalid(parts, f"Muster sind nur für status new erlaubt, nicht für {status}")
                    continue
                if not isinstance(upstream, str) or not HASH_RE.match(upstream):
                    invalid(parts + ("upstream",),
                            f"upstream muss der {HASH_LEN}-stellige Hash des Originals sein, ist {upstream!r}",
                            hint="python -m tools.openbswsync accept <pfad> trägt ihn ein")
                    entry.upstream = None
            root.entries[entry.key] = entry
        roots.append(root)
    return CopyList(file, submodule, commit, roots, findings)


# ------------------------------------------------------------------------------------------------ check


@dataclass
class Row:
    root: str
    rel: str
    path: str
    original: str | None
    actual: str                  # identical | modified | new
    listed: str | None           # status of the entry, None if unlisted
    pattern: str | None = None   # key of the matching pattern entry
    task: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class RootSummary:
    name: str
    path: str
    original: str
    identical: int = 0
    modified: int = 0
    new: int = 0
    not_taken: list[str] = field(default_factory=list)


@dataclass
class Report:
    list_file: str
    submodule: str
    list_commit: str | None
    submodule_commit: str | None
    roots: list[RootSummary]
    rows: list[Row]
    findings: list[Finding]

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "warning"]

    def to_json(self) -> str:
        data = asdict(self)
        data["error_count"] = len(self.errors)
        data["warning_count"] = len(self.warnings)
        return json.dumps(data, indent=2, ensure_ascii=False)


def _owner_root(roots: list[Root], rel_repo: str) -> Root | None:
    best = None
    for root in roots:
        if rel_repo == root.path or rel_repo.startswith(root.path + "/"):
            if best is None or len(root.path) > len(best.path):
                best = root
    return best


def check(repo: Path, copy_list: CopyList) -> Report:
    """Compare the repository against the list and the submodule; the report holds rows and findings."""
    repo = repo.resolve()
    findings = list(copy_list.findings)
    submodule = repo / copy_list.submodule
    rows: list[Row] = []
    summaries: list[RootSummary] = []

    actual_commit = submodule_commit(submodule)
    if copy_list.commit and actual_commit and not actual_commit.startswith(copy_list.commit):
        findings.append(Finding(
            "warning", "submodule-commit",
            f"Liste wurde gegen OpenBSW {copy_list.commit[:7]} erfasst, das Submodul steht auf {actual_commit[:7]}",
            yaml="openbsw.commit",
            hint="nach Prüfung der Befunde: python -m tools.openbswsync accept --commit"))

    for root in copy_list.roots:
        summary = RootSummary(root.name, root.path, root.original)
        summaries.append(summary)
        root_dir = repo / root.path
        orig_dir = submodule / root.original
        if not root_dir.is_dir():
            findings.append(Finding("error", "list-invalid", f"Verzeichnis der Wurzel {root.name} fehlt: {root.path}",
                                    yaml=format_yaml_path(("roots", root.index, "path"))))
            continue
        if not orig_dir.is_dir():
            findings.append(Finding(
                "error", "list-invalid",
                f"Original der Wurzel {root.name} fehlt: {copy_list.submodule}/{root.original}",
                yaml=format_yaml_path(("roots", root.index, "original")),
                hint=f"git submodule update --init {copy_list.submodule}"))
            continue

        # nested roots belong to their own root; other roots' originals are not "not taken" of this one
        nested = [repo / r.path for r in copy_list.roots
                  if r is not root and r.path.startswith(root.path + "/")]
        nested_orig = [submodule / r.original for r in copy_list.roots
                       if r is not root and r.original.startswith(root.original + "/")]
        excludes = [glob_to_regex(e) for e in root.exclude]
        repo_files = {rel: p for rel, p in _walk(root_dir, nested).items()
                      if not any(x.match(rel) for x in excludes)}
        orig_files = {rel: p for rel, p in _walk(orig_dir, nested_orig).items()
                      if not any(x.match(rel) for x in excludes)}
        used_patterns: set[str] = set()

        for rel, path in sorted(repo_files.items()):
            repo_rel = f"{root.path}/{rel}"
            orig = orig_dir / rel
            has_orig = orig.is_file()
            entry = root.exact(rel)
            pattern = None if entry else root.matching_pattern(rel)
            if pattern:
                used_patterns.add(pattern.key)

            def yaml_of(e: Entry | None, *more) -> str:
                return format_yaml_path(e.yaml + more) if e else format_yaml_path(("roots", root.index, "files"))

            def add(severity, code, text, e=None, hint="", *more):
                findings.append(Finding(severity, code, text, repo_rel, yaml_of(e, *more), hint))

            if has_orig:
                same = _normalized(path) == _normalized(orig)
                actual = "identical" if same else "modified"
                orig_hash = normalized_hash(orig)
            else:
                same, actual, orig_hash = False, "new", None
            listed = entry.status if entry else ("new" if pattern else None)
            source = entry or pattern
            rows.append(Row(root.name, rel, repo_rel, f"{copy_list.submodule}/{root.original}/{rel}" if has_orig else None,
                            actual, listed, pattern.key if pattern else None,
                            list(source.task) if source else [], source.reason if source else ""))
            summary_attr = actual
            setattr(summary, summary_attr, getattr(summary, summary_attr) + 1)

            if has_orig:
                if entry is None:
                    add("error", "unlisted-copy",
                        ("identische Kopie" if same else "Datei weicht vom gleichnamigen OpenBSW-Original ab")
                        + " und steht nicht in der Liste",
                        None,
                        "als identical eintragen (skeleton liefert den Eintrag)" if same else
                        "als modified mit task und reason eintragen oder auf das Original zurücksetzen")
                elif entry.status == "new":
                    add("error", "new-has-original",
                        "als neu eingetragen, aber OpenBSW hat eine Datei an derselben Stelle "
                        "(upstream neu hinzugekommen?)", entry,
                        "Datei mit dem Original vergleichen und als identical oder modified eintragen", "status")
                elif entry.status == "identical":
                    recorded = entry.upstream
                    if same:
                        if recorded and recorded != orig_hash:
                            add("warning", "hash-outdated",
                                "Kopie folgt bereits dem geänderten Original, der Hash in der Liste ist alt", entry,
                                f"python -m tools.openbswsync accept {repo_rel}", "upstream")
                    elif recorded is None:
                        add("error", "identical-changed", "als identisch eingetragen, weicht aber ab", entry)
                    elif recorded == orig_hash:
                        add("error", "identical-changed",
                            "geänderte Kopie ohne Begründung: als identisch eingetragen, weicht vom unveränderten "
                            "Original ab", entry,
                            "Änderung zurücknehmen oder als modified mit task und reason eintragen", "status")
                    elif normalized_hash(path) == recorded:
                        add("error", "upstream-changed",
                            "OpenBSW hat das Original dieser identischen Kopie geändert (Submodul-Update)", entry,
                            f"Kopie nachziehen (Original: {copy_list.submodule}/{root.original}/{rel}), dann "
                            f"python -m tools.openbswsync accept {repo_rel}", "upstream")
                    else:
                        add("error", "upstream-and-copy-changed",
                            "als identisch eingetragen, aber sowohl Kopie als auch Original weichen vom erfassten "
                            "Stand ab", entry,
                            "Upstream-Änderung prüfen, Kopie nachziehen oder als modified begründen, dann accept",
                            "status")
                elif entry.status == "modified":
                    if not entry.task or not entry.reason:
                        add("error", "modified-without-reason",
                            "geänderte Kopie ohne Begründung (task und reason sind Pflicht)", entry,
                            "Aufgaben-ID und Grund der Abweichung eintragen", "reason" if entry.task else "task")
                    if entry.upstream and entry.upstream != orig_hash:
                        add("error", "upstream-changed-modified",
                            "OpenBSW hat das Original dieser geänderten Kopie geändert (Submodul-Update)", entry,
                            f"Upstream-Änderung prüfen und nachziehen (Original: "
                            f"{copy_list.submodule}/{root.original}/{rel}), dann "
                            f"python -m tools.openbswsync accept {repo_rel}", "upstream")
                    if same:
                        add("warning", "modified-now-identical",
                            "als geändert eingetragen, ist aber identisch mit dem Original", entry,
                            "status auf identical setzen, task/reason entfernen", "status")
            else:
                if entry is None and pattern is None:
                    add("warning", "unlisted-new", "neue Datei ohne Eintrag in der Liste", None,
                        "als new eintragen (einzeln oder über ein Muster wie 'uds/**')")
                elif entry is not None and entry.status in ("identical", "modified"):
                    add("error", "original-gone",
                        f"als {entry.status} eingetragen, aber das Original fehlt in OpenBSW "
                        "(upstream gelöscht oder verschoben?)", entry,
                        "neuen Ort des Originals suchen oder als new eintragen", "status")

        for key, entry in root.entries.items():
            if entry.pattern:
                if key not in used_patterns:
                    findings.append(Finding("warning", "unused-pattern", f"Muster '{key}' trifft keine Datei",
                                            f"{root.path}/{key}", format_yaml_path(entry.yaml)))
            elif key not in repo_files:
                findings.append(Finding("error", "stale-entry", "Eintrag ohne Datei im Repository",
                                        f"{root.path}/{key}", format_yaml_path(entry.yaml),
                                        "Eintrag entfernen oder Pfad der Wurzel anpassen (verschoben?)"))

        summary.not_taken = sorted(rel for rel in orig_files if rel not in repo_files)

    return Report(copy_list.file.as_posix(), copy_list.submodule, copy_list.commit, actual_commit, summaries, rows,
                  findings)


# ------------------------------------------------------------------------------------------------ output


def _short(sha: str | None) -> str:
    return sha[:7] if sha else "?"


def format_text(report: Report, show_all: bool = False, findings_only: bool = False) -> str:
    lines = [f"Kopien-Abgleich gegen OpenBSW ({report.submodule} {_short(report.submodule_commit)}, "
             f"Liste erfasst gegen {_short(report.list_commit)})"]
    marks = {"identical": "=", "modified": "~", "new": "+"}
    for s in report.roots:
        lines.append(f"[{s.name}] {s.path} <- {report.submodule}/{s.original}: identisch {s.identical}, "
                     f"geändert {s.modified}, neu {s.new}, nicht übernommen {len(s.not_taken)}")
        if findings_only:
            continue
        for row in (r for r in report.rows if r.root == s.name):
            text = f"  {marks[row.actual]} {row.rel}"
            if row.actual == "modified" and (row.task or row.reason):
                text += f"  [{', '.join(row.task)}] {row.reason}"
            elif row.pattern:
                text += f"  (Muster {row.pattern})"
            lines.append(text)
        if show_all:
            lines.extend(f"  - {rel}" for rel in s.not_taken)
    if report.findings:
        lines.append("")
        lines.extend(str(f) for f in report.findings)
    lines.append("")
    lines.append(f"Befunde: {len(report.errors)} Fehler, {len(report.warnings)} Warnungen")
    return "\n".join(lines)


def format_markdown(report: Report, show_all: bool = False) -> str:
    lines = [f"# Kopien-Abgleich gegen OpenBSW",
             "",
             f"Submodul `{report.submodule}` `{_short(report.submodule_commit)}`, Liste erfasst gegen "
             f"`{_short(report.list_commit)}`. Befunde: {len(report.errors)} Fehler, {len(report.warnings)} Warnungen.",
             "",
             "| Wurzel | Pfad | Original | identisch | geändert | neu | nicht übernommen |",
             "|---|---|---|---:|---:|---:|---:|"]
    for s in report.roots:
        lines.append(f"| {s.name} | `{s.path}` | `{report.submodule}/{s.original}` | {s.identical} | {s.modified} | "
                     f"{s.new} | {len(s.not_taken)} |")
    for s in report.roots:
        lines += ["", f"## {s.name}", "", "| Datei | Stand | Aufgabe | Begründung |", "|---|---|---|---|"]
        for row in (r for r in report.rows if r.root == s.name):
            reason = row.reason.replace("|", "\\|").replace("\n", " ")
            if row.pattern:
                reason = f"(Muster `{row.pattern}`) {reason}".strip()
            lines.append(f"| `{row.rel}` | {STATUS_DE[row.actual]} | {', '.join(row.task)} | {reason} |")
        if show_all and s.not_taken:
            lines += ["", "Nicht übernommen:", ""] + [f"- `{rel}`" for rel in s.not_taken]
    if report.findings:
        lines += ["", "## Befunde", ""]
        lines += [f"- **{'Fehler' if f.severity == 'error' else 'Warnung'}** `{f.code}` "
                  f"{'`' + f.path + '` ' if f.path else ''}{f.text}"
                  f"{' (`' + f.yaml + '`)' if f.yaml else ''}{' – ' + f.hint if f.hint else ''}"
                  for f in report.findings]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------------------------------------ maintenance


def skeleton(repo: Path, copy_list: CopyList) -> str:
    """YAML entries for every file that has no entry yet, per root (modified ones with TODO task and reason)."""
    repo = repo.resolve()
    report = check(repo, copy_list)
    submodule = repo / copy_list.submodule
    by_root: dict[str, list[str]] = {}
    for row in report.rows:
        if row.listed is not None:
            continue
        root = next(r for r in copy_list.roots if r.name == row.root)
        key = json.dumps(row.rel, ensure_ascii=False)
        if row.actual == "new":
            text = f"      {key}: {{status: new}}"
        else:
            h = normalized_hash(submodule / root.original / row.rel)
            if row.actual == "identical":
                text = f"      {key}: {{status: identical, upstream: {h}}}"
            else:
                text = (f"      {key}:\n        status: modified\n        task: TODO\n"
                        f"        reason: TODO\n        upstream: {h}")
        by_root.setdefault(row.root, []).append(text)
    out = []
    for name, texts in by_root.items():
        out.append(f"# root {name}")
        out.extend(texts)
    return "\n".join(out) + ("\n" if out else "")


def accept(repo: Path, list_file: Path, paths: list[str], all_upstream: bool = False,
           commit: bool = False) -> list[str]:
    """Record the current original as reviewed: set `upstream` of the named entries (repository paths) to the hash
    of the current original, with all_upstream for every entry whose original changed, and with commit the
    submodule commit. Only the hash values are replaced in the text, so layout and comments of copies.yaml stay
    as written. Returns the lines to print; raises ListError for a path that cannot be accepted (nothing is written
    then)."""
    repo = repo.resolve()
    copy_list = load_list(list_file)
    submodule = repo / copy_list.submodule
    lines = list_file.read_text(encoding="utf-8").splitlines(keepends=True)
    messages: list[str] = []
    requested = {p.replace("\\", "/").strip("/") for p in paths}
    wanted = set(requested)

    for root in copy_list.roots:
        for key, entry in root.entries.items():
            if entry.pattern or entry.status == "new":
                if f"{root.path}/{key}" in wanted:
                    raise ListError(f"{root.path}/{key}: Eintrag 'new' hat keinen upstream-Hash")
                continue
            repo_rel = f"{root.path}/{key}"
            orig = submodule / root.original / key
            if repo_rel not in wanted and not all_upstream:
                continue
            wanted.discard(repo_rel)
            if not orig.is_file():
                raise ListError(f"{repo_rel}: Original fehlt ({copy_list.submodule}/{root.original}/{key})")
            h = normalized_hash(orig)
            if entry.upstream == h:
                if repo_rel in requested:
                    messages.append(f"{repo_rel}: unverändert ({h})")
                continue
            copy = repo / root.path / key
            if entry.status == "identical" and copy.is_file() and normalized_hash(copy) != h:
                raise ListError(f"{repo_rel}: als identisch eingetragen, weicht aber vom Original ab - "
                                "erst die Kopie nachziehen oder als modified begründen")
            _replace_upstream(lines, root.name, key, h, repo_rel)
            messages.append(f"{repo_rel}: upstream {entry.upstream} -> {h}")
    if wanted:
        raise ListError("kein Eintrag für: " + ", ".join(sorted(wanted)))
    if commit:
        sha = submodule_commit(submodule)
        if sha is None:
            raise ListError(f"Commit des Submoduls {copy_list.submodule} nicht lesbar")
        _replace_commit(lines, sha)
        messages.append(f"openbsw.commit -> {sha}")
    with list_file.open("w", encoding="utf-8", newline="") as fh:
        fh.write("".join(lines))
    return messages


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _significant(line: str) -> bool:
    return bool(line.strip()) and not line.lstrip().startswith("#")


def _replace_upstream(lines: list[str], root_name: str, key: str, value: str, repo_rel: str) -> None:
    """Replace the value of `upstream:` of one entry in the text of copies.yaml."""
    name_re = re.compile(r"^\s*-\s*name:\s*[\"']?(.+?)[\"']?\s*$")
    start = next((i for i, line in enumerate(lines)
                  if (m := name_re.match(line)) and m.group(1) == root_name), None)
    if start is None:
        raise ListError(f"{repo_rel}: Wurzel {root_name} im Text der Liste nicht gefunden")
    end = next((i for i in range(start + 1, len(lines)) if name_re.match(lines[i])), len(lines))
    key_re = re.compile(r"^(\s*)([\"']?)" + re.escape(key) + r"\2\s*:")
    at = next((i for i in range(start, end) if key_re.match(lines[i])), None)
    if at is None:
        raise ListError(f"{repo_rel}: Eintrag im Text der Liste nicht gefunden")
    value_re = re.compile(r"(upstream:\s*)[^\s,}]+")
    key_indent = _indent(lines[at])
    for i in range(at, end):
        if i > at and _significant(lines[i]) and _indent(lines[i]) <= key_indent:
            break
        if value_re.search(lines[i]):
            lines[i] = value_re.sub(lambda m: m.group(1) + value, lines[i], count=1)
            return
    raise ListError(f"{repo_rel}: Eintrag ohne upstream-Zeile - 'upstream: TODO' ergänzen und erneut aufrufen")


def _replace_commit(lines: list[str], sha: str) -> None:
    """Set openbsw.commit in the text of copies.yaml (added below openbsw.path if missing)."""
    top = next((i for i, line in enumerate(lines) if re.match(r"^openbsw:\s*$", line)), None)
    if top is None:
        raise ListError("Abschnitt openbsw: im Text der Liste nicht gefunden")
    i, last = top + 1, top
    while i < len(lines) and (not _significant(lines[i]) or _indent(lines[i]) > 0):
        if re.match(r"^\s+commit:", lines[i]):
            lines[i] = re.sub(r"(commit:\s*)\S+", lambda m: m.group(1) + sha, lines[i], count=1)
            return
        if _significant(lines[i]):
            last = i
        i += 1
    newline = "\r\n" if lines[top].endswith("\r\n") else "\n"
    lines.insert(last + 1, f"  commit: {sha}{newline}")
