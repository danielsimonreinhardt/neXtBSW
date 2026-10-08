# SPDX-License-Identifier: Apache-2.0
"""Core of the distribution check (PLAT-50, concept in doc/architecture/lizenz-und-release.md section 5.3).

No Qt, no GUI, no network. git is only used to read the list of tracked files and, for --history, the objects of a
revision range. Every finding carries rule, severity, tree relative path and, where the rule works on text, the
1-based line number, so the output can be read by a human and by a machine (--json), like tools/nxconfig.

Model
-----
Every file of a tree gets exactly one distribution class:

    open         may be published (open source line)
    licensed     only for customers with a contract, no NDA knowledge
    nda          derived from documents under NDA
    private      internal (planning, own board, own measurements, Studio)
    workstation  must not even be in the private repository (vendor packages, keys)

The class comes from the module manifest (module.yaml with ``distribution:``) if there is one, otherwise from the
path table of distribution.yaml (most specific glob wins; ties: the later entry wins). A file that matches nothing
is ``unclassified`` and that is an error: "not classified = forbidden" (concept section 5.2).

A profile (open, licensed, nda) is exported. A file may be part of it if its class is not higher than the profile.

Rules (the numbering follows the concept, R9 lives in exporter.py)
---------------------------------------------------------------
    R1  classification    file without class, or class above the profile
    R2  spdx              unknown / forbidden SPDX identifier, LicenseRef in the open profile;
                          warning if a source file has no header and the licence only comes from the table
    R3  content patterns  words of NDA documents; the private pattern list stays outside the repository; the
                          header lines Simulink Coder writes into generated code and ARXML (PLAT-119, F9 A)
    R4  binary files      binary or document files without an explicit entry, PDF text scan
    R5  references        a file of the profile points to content that is not exported (CMake, #include,
                          project/board/module references, python imports)
    R6  third party       checksum file next to a copied third party source does not match, LICENSE missing
    R7  secrets           key material, private repository address, workstation paths
    R8  history           blobs of a revision range (a fresh export has exactly one commit)
    R10 notice            NOTICE of the export is missing or does not match the classification
    R11 evidence          value of the derivative database or of an open board without public evidence: non-public
                          source, or ``verify: true`` (decision of 2026-09-17, F8, TOOL-241)
    R12 export cut        unbalanced or nested markers of a private section (export:private-begin/-end, DOC-74)
    R13 export overlay    a substitute of export.overlay is unusable: source not in the profile, target already a
                          file of the profile, nothing to replace; in an exported tree: target differs from source
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

import yaml

RULES_VERSION = 1
CLASSES = ("open", "licensed", "nda", "private", "workstation")
RANK = {name: index for index, name in enumerate(CLASSES)}
PROFILES = ("open", "licensed", "nda")
UNCLASSIFIED = "unclassified"
CLASS_DE = {"open": "offen", "licensed": "lizenziert", "nda": "NDA", "private": "privat",
            "workstation": "Arbeitsplatz", UNCLASSIFIED: "nicht klassifiziert"}
ALL_RULES = ("R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R10", "R11", "R12", "R13")
MAX_TEXT_BYTES = 4_000_000                      # files larger than this are not scanned line by line
IGNORED_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules", ".venv", "build", ".claude"}

# A header, not a mention in prose: the expression must be followed by nothing but the end of a comment.
SPDX_RE = re.compile(r"SPDX-License-Identifier:\s*([A-Za-z0-9.+\-]+(?:\s+(?:AND|OR|WITH)\s+\(?[A-Za-z0-9.+\-]+\)?)*)"
                     r"\s*(?:\*/|-->)?\s*$")
LICENSE_REF_RE = re.compile(r"\bLicenseRef-[A-Za-z0-9.\-]+")
INCLUDE_RE = re.compile(r'^\s*#\s*include\s*[<"]([^">]+)[">]')
ADD_SUBDIR_RE = re.compile(r"add_subdirectory\s*\(\s*([^\s)]+)")
CMAKE_INCLUDE_RE = re.compile(r"^\s*include\s*\(\s*([^\s)]+)", re.MULTILINE)
LINK_RE = re.compile(r"target_link_libraries\s*\(([^)]*)\)", re.DOTALL)
DEFINE_TARGET_RE = re.compile(r"add_(?:library|executable)\s*\(\s*([A-Za-z0-9_.\-]+)")
PY_IMPORT_RE = re.compile(r"^\s*(?:from\s+([A-Za-z0-9_.]+)\s+import|import\s+([A-Za-z0-9_.]+))")


class RulesError(Exception):
    """distribution.yaml cannot be used at all (missing, no mapping, unknown class)."""


# ------------------------------------------------------------------------------------------- findings


@dataclass(frozen=True)
class Finding:
    severity: str                               # error | warning | info
    rule: str                                   # R1 ... R11
    text: str
    path: str = ""                              # tree relative, posix
    line: int | None = None                     # 1-based, None if the rule does not work on lines
    detail: str = ""

    def __str__(self) -> str:
        where = self.path or ""
        if self.line:
            where += f":{self.line}"
        head = f"{where}: " if where else ""
        tail = f" ({self.detail})" if self.detail else ""
        return f"{head}{self.severity}: [{self.rule}] {self.text}{tail}"


@dataclass
class Report:
    profile: str
    tree: str
    files: int = 0
    exported: int = 0
    by_class: dict[str, int] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)      # checks that could not run, with reason
    ran: list[str] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "warning"]

    def to_json(self) -> str:
        return json.dumps({
            "profile": self.profile, "tree": self.tree, "files": self.files, "exported": self.exported,
            "by_class": self.by_class, "ran": self.ran, "skipped": self.skipped,
            "errors": len(self.errors), "warnings": len(self.warnings),
            "findings": [asdict(f) for f in self.findings],
        }, indent=2, ensure_ascii=False)


def format_text(report: Report, max_per_rule: int = 20) -> str:
    """Human readable report: header, findings grouped by rule, summary."""
    out = [f"distcheck: Profil {report.profile}, Baum {report.tree}",
           f"  {report.files} Dateien, davon {report.exported} im Profil "
           + ", ".join(f"{CLASS_DE.get(k, k)}={v}" for k, v in sorted(report.by_class.items(),
                                                                     key=lambda kv: RANK.get(kv[0], 99)))]
    for note in report.skipped:
        out.append(f"  UEBERSPRUNGEN: {note}")
    for note in report.ran:
        if note.startswith(("Export-Links:", "Export-Schnitt:")):   # DOC-73/DOC-74: what the export replaces
            out.append(f"  EXPORT: {note.split(':', 1)[1].strip()}")
    by_rule: dict[str, list[Finding]] = {}
    for finding in report.findings:
        by_rule.setdefault(finding.rule, []).append(finding)
    for rule in sorted(by_rule, key=lambda r: (len(r), r)):
        group = by_rule[rule]
        errors = sum(1 for f in group if f.severity == "error")
        out.append(f"\n{rule}: {len(group)} Befund(e), davon {errors} Fehler")
        shown = group if max_per_rule <= 0 else group[:max_per_rule]
        out += [f"  {finding}" for finding in shown]
        if len(shown) < len(group):
            out.append(f"  ... {len(group) - len(shown)} weitere (--max-per-rule 0 zeigt alle, --json alle)")
    out.append(f"\nErgebnis: {len(report.errors)} Fehler, {len(report.warnings)} Warnung(en)")
    return "\n".join(out)


# ------------------------------------------------------------------------------------------- rules


@dataclass
class PathRule:
    glob: str
    cls: str
    license: str | None = None                  # SPDX expression for files without their own header
    reason: str = ""
    verify: bool = False                        # classification not backed by a decision yet -> warning
    binary_ok: bool = False                     # binary/document file is wanted in the export (R4)
    cleaned: bool = False                       # published only as the cleaned output of an export clean rule
    fallback: bool = False                      # catch all entry: matching it means nobody classified the file
    allow: list[str] = field(default_factory=list)   # ids of content/secret patterns tolerated here
    index: int = 0

    @property
    def specificity(self) -> tuple[int, int, int, int]:
        """Depth, fixed leading part, fixed characters overall (docs/*.pdf before docs/**), then file order."""
        literal = self.glob.split("*")[0]
        fixed = len(self.glob.replace("*", "").replace("?", ""))
        return (self.glob.count("/"), len(literal), fixed, self.index)


@dataclass
class Pattern:
    id: str
    regex: re.Pattern
    text: str
    severity: str = "error"

    @classmethod
    def parse(cls, raw: dict, where: str) -> "Pattern":
        try:
            return cls(id=str(raw["id"]), regex=re.compile(raw["regex"], re.IGNORECASE if raw.get("ignore_case", True)
                                                           else 0),
                       text=str(raw.get("text", raw["id"])), severity=str(raw.get("severity", "error")))
        except (KeyError, re.error) as exc:
            raise RulesError(f"{where}: Muster nicht verwendbar ({exc})") from exc


@dataclass
class Substitution:
    """PLAT-114: a text the export replaces inside otherwise unchanged files (export.substitute of distribution.yaml),
    e.g. the link of an internal review page in a safety record. The rest of the file stays as it is, and the check
    of the source tree scans the replaced text - so it reports exactly what would remain in the export."""
    glob: str
    regex: re.Pattern
    replacement: str
    reason: str = ""

    @classmethod
    def parse(cls, raw: dict, where: str) -> "Substitution":
        try:
            return cls(glob=str(raw["glob"]), regex=re.compile(raw["regex"]), replacement=str(raw["with"]),
                       reason=str(raw.get("reason", "")))
        except (KeyError, TypeError, re.error) as exc:
            raise RulesError(f"{where}: Ersetzung nicht verwendbar (glob, regex, with) ({exc})") from exc


@dataclass
class Overlay:
    """PLAT-119 (decision F9 A): files the export writes in place of a path that is not part of the profile - the
    hand-written substitute of the Simulink code of swc/DemoSwc. `files` maps the target in the export to its source
    in the tree. Active for a profile when no file under `replaces` is in it."""
    replaces: str
    files: dict[str, str]
    reason: str = ""

    @classmethod
    def parse(cls, raw: dict, where: str) -> "Overlay":
        try:
            replaces = str(raw["replaces"]).strip("/")
            files = {str(target).strip("/"): str(source).strip("/") for target, source in dict(raw["files"]).items()}
        except (KeyError, TypeError, ValueError) as exc:
            raise RulesError(f"{where}: Ersatz nicht verwendbar (replaces, files) ({exc})") from exc
        if not replaces or not files or not all(files) or not all(files.values()):
            raise RulesError(f"{where}: Ersatz braucht replaces und mindestens eine Datei (Ziel: Quelle)")
        return cls(replaces=replaces, files=files, reason=str(raw.get("reason", "")))

    def covers(self, path: str) -> bool:
        return path == self.replaces or path.startswith(self.replaces + "/")


def active_overlays(tree: "Tree", rules: "Rules", profile: str) -> list[Overlay]:
    """The overlays of export.overlay that the export of `profile` applies: nothing under `replaces` is exported."""
    inside = set(tree.exported(profile))
    return [o for o in rules.overlays if not any(o.covers(p) for p in inside)]


def overlay_findings(tree: "Tree", rules: "Rules", profile: str, exported_tree: bool) -> list[Finding]:
    """PLAT-119: every active overlay can be applied (source tree) or was applied (exported tree)."""
    out = []
    inside = set(tree.exported(profile))
    where = "tools/distcheck/distribution.yaml"
    for overlay in active_overlays(tree, rules, profile):
        if not exported_tree and not any(overlay.covers(p) for p in tree.files):
            out.append(Finding("error", "R13", f"export.overlay: {overlay.replaces} gibt es im Baum nicht - der Ersatz "
                                               f"ersetzt nichts (Eintrag veraltet?)", where))
        for target, source in overlay.files.items():
            if source not in inside:
                out.append(Finding("error", "R13", f"export.overlay: Quelle {source} von {target} liegt nicht im "
                                                   f"Profil {profile}", source))
            if overlay.covers(target):
                out.append(Finding("error", "R13", f"export.overlay: Ziel {target} liegt im ersetzten Pfad "
                                                   f"{overlay.replaces}", target))
            if exported_tree:
                if target not in tree.files:
                    out.append(Finding("error", "R13", f"export.overlay: Ersatz {target} fehlt im exportierten Baum",
                                       target))
                elif source in tree.files and (tree.root / target).read_bytes() != (tree.root / source).read_bytes():
                    out.append(Finding("error", "R13", f"export.overlay: {target} weicht von seiner Quelle {source} ab",
                                       target))
            elif target in tree.files and target in inside:
                out.append(Finding("error", "R13", f"export.overlay: Ziel {target} ist schon eine Datei des Profils "
                                                   f"{profile} - der Ersatz wuerde sie ueberschreiben", target))
    return out


def substituted(rules: "Rules", path: str, text: str | None) -> str | None:
    """The text of `path` as the export writes it, or None if no substitution changes it."""
    if text is None:
        return None
    new = text
    for sub in rules.substitutions:
        if _match(path, sub.glob):
            new = sub.regex.sub(sub.replacement, new)
    return new if new != text else None


def substitution_count(rules: "Rules", path: str, text: str | None) -> int:
    """How many texts export.substitute replaces in `path` (DOC-73: the export lists its replacements)."""
    if text is None:
        return 0
    count = 0
    for sub in rules.substitutions:
        if _match(path, sub.glob):
            text, hits = sub.regex.subn(sub.replacement, text)
            count += hits
    return count


# ------------------------------------------------------------------------------------------- unpublished links
# DOC-73 (decision K1 of 2026-10-04): documents of the development repository that are not published (review records,
# gap analysis, migration plan, ...) stay where they are, and the published documents keep linking to them in the
# repository. The export must not carry dead links: every Markdown link from an exported file to a file (or a folder)
# of the tree that is not exported is replaced by its link text plus a fixed note. The source text stays unchanged.

NOT_PUBLISHED = "not published, available on request"
LINK_SUFFIXES = (".md",)                       # files whose links are rewritten in the export

# [text](target "title") and ![alt](target); the text may hold one level of brackets, the target may be <...>
MD_LINK_RE = re.compile(r"(!?)\[((?:[^\[\]\n]|\[[^\[\]\n]*\])*)\]"
                        r"\(\s*(<[^<>\n]*>|[^()\s]+(?:\([^()\s]*\)[^()\s]*)*)"
                        r"(?:\s+(?:\"[^\"\n]*\"|'[^'\n]*'|\([^()\n]*\)))?\s*\)")
# reference definition [label]: target "title"
MD_REF_DEF_RE = re.compile(r"^ {0,3}\[([^\[\]\n]+)\]:[ \t]*(<[^<>\n]*>|\S+)(?:[ \t]+.*)?$")
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


@dataclass(frozen=True)
class LinkReplacement:
    """One link the export replaced: file, 1-based line, resolved tree path of the target, the link as written."""
    path: str
    line: int
    target: str
    link: str


def link_target(source: str, href: str) -> str | None:
    """Tree relative path a Markdown link of `source` points to, or None for a web address, mail link or pure anchor.
    The anchor (#...) and a query are dropped; a leading / means the root of the tree (as on the code host)."""
    href = href.strip()
    if href.startswith("<") and href.endswith(">"):
        href = href[1:-1].strip()
    if not href or href.startswith(("#", "//")) or re.match(r"^[A-Za-z][A-Za-z0-9+.\-]*:", href):
        return None
    from urllib.parse import unquote
    href = unquote(href.split("#", 1)[0].split("?", 1)[0])
    if not href:
        return None
    if href.startswith("/"):
        return _join("", href.lstrip("/"))
    return _join(source.rsplit("/", 1)[0] if "/" in source else "", href)


def link_anchor(href: str) -> str:
    """The anchor of a Markdown link target (without '#', decoded, lower case), or '' if it has none."""
    href = href.strip()
    if href.startswith("<") and href.endswith(">"):
        href = href[1:-1].strip()
    if "#" not in href:
        return ""
    from urllib.parse import unquote
    return unquote(href.split("#", 1)[1]).strip().lower()


class LinkIndex:
    """Which paths of a tree are published: files of the profile, files written by a clean rule and generated files.
    A folder counts as published as soon as one file below it is. DOC-74: `private_anchors` names per published file
    the anchors that lie in a private section (export cut) - they do not exist in the export."""

    def __init__(self, files: Iterable[str], available: Iterable[str],
                 private_anchors: dict[str, Iterable[str]] | None = None):
        self.files = set(files)
        self.available = set(available)
        self.private_anchors = {path: {a.lower() for a in anchors}
                                for path, anchors in (private_anchors or {}).items()}
        self.dirs: dict[str, bool] = {}
        for path in self.files:
            parts = path.split("/")
            published = path in self.available
            for depth in range(1, len(parts)):
                folder = "/".join(parts[:depth])
                self.dirs[folder] = self.dirs.get(folder, False) or published

    @classmethod
    def of(cls, tree: "Tree", rules: "Rules", profile: str) -> "LinkIndex":
        available = set(tree.exported(profile))
        available |= {str(entry.get("file")) for entry in rules.export.get("clean") or []}
        available |= {str(name) for name in rules.export.get("generate") or {}}
        anchors: dict[str, set[str]] = {}
        for path in sorted(available & set(tree.files)):
            text = tree.text(path) if path.endswith(LINK_SUFFIXES) else None
            if not text or PRIVATE_MARK not in text:
                continue
            try:
                found = {a for cut in private_sections(path, text) for a in cut.anchors}
            except PrivateSectionError:
                continue                                   # check reports it (R12); the export refuses to run
            if found:
                anchors[path] = found
        return cls(tree.files, available, anchors)

    def private_anchor(self, target: str, anchor: str) -> bool:
        """True if `anchor` of the published file `target` lies in a private section (DOC-74)."""
        return bool(anchor) and anchor.lower() in self.private_anchors.get(target, set())

    def unpublished(self, target: str) -> bool:
        """True if the target lies in the tree but not in the export. Unknown paths are not judged here."""
        target = target.rstrip("/")
        if not target:
            return False
        if target in self.files:
            return target not in self.available
        if target in self.dirs:
            return not self.dirs[target]
        return False


def rewrite_unpublished_links(path: str, text: str, index: LinkIndex,
                              note: str = NOT_PUBLISHED) -> tuple[str, list[LinkReplacement]]:
    """The text of `path` with every link to an unpublished target replaced by `link text (note)`.

    Inline links and images, reference links ([text][label], [label][], [label]) and their definitions are handled;
    links inside fenced code blocks stay as written (they are examples, not links). Line ends are kept."""
    if not path.endswith(LINK_SUFFIXES):
        return text, []
    lines = text.splitlines(keepends=True)
    fenced = fenced_lines(lines)

    def gone(href: str) -> str | None:
        """What the export lacks of a link target: the unpublished file, or `file#anchor` of an anchor in a private
        section (DOC-74, also a pure anchor of this file); None if the target is published or unknown."""
        target = link_target(path, href)
        if target is not None and index.unpublished(target):
            return target
        anchor = link_anchor(href)
        if target is None and href.strip().lstrip("<").startswith("#"):
            target = path
        if target is not None and index.private_anchor(target, anchor):
            return f"{target}#{anchor}"
        return None

    # reference definitions that point to an unpublished target
    hidden: dict[str, str] = {}
    for line, is_code in zip(lines, fenced):
        definition = None if is_code else MD_REF_DEF_RE.match(line.rstrip("\r\n"))
        if definition:
            target = gone(definition.group(2))
            if target is not None:
                hidden[definition.group(1).strip().lower()] = target
    out: list[str] = []
    found: list[LinkReplacement] = []

    def replaced(label: str) -> str:
        label = label.strip()
        return f"{label} ({note})" if label else f"({note})"

    for number, (line, is_code) in enumerate(zip(lines, fenced), start=1):
        if is_code:
            out.append(line)
            continue
        definition = MD_REF_DEF_RE.match(line.rstrip("\r\n"))
        if definition and definition.group(1).strip().lower() in hidden:
            found.append(LinkReplacement(path, number, hidden[definition.group(1).strip().lower()],
                                         line.rstrip("\r\n")))
            continue                                   # the definition itself goes, its uses are replaced below

        def inline(match: re.Match) -> str:
            target = gone(match.group(3))
            if target is None:
                return match.group(0)
            found.append(LinkReplacement(path, number, target, match.group(0)))
            return replaced(match.group(2))

        new = MD_LINK_RE.sub(inline, line)
        if hidden:
            def reference(match: re.Match) -> str:
                label = (match.group(2) or match.group(1)).strip().lower()
                if label not in hidden:
                    return match.group(0)
                found.append(LinkReplacement(path, number, hidden[label], match.group(0)))
                return replaced(match.group(1))

            # [text][label] / [label][] / [label] that is no inline link and no definition
            new = re.sub(r"!?\[((?:[^\[\]\n]|\[[^\[\]\n]*\])*)\](?:\[([^\[\]\n]*)\]|(?![(:\[]))", reference, new)
        out.append(new)
    return "".join(out), found


def fenced_lines(lines: Sequence[str]) -> list[bool]:
    """Per line: True if it belongs to a fenced code block (the fences included)."""
    in_fence = ""
    fenced: list[bool] = []
    for line in lines:
        fence = FENCE_RE.match(line)
        if fence and (not in_fence or fence.group(1)[0] == in_fence[0]):
            fenced.append(True)
            in_fence = "" if in_fence else fence.group(1)
            continue
        fenced.append(bool(in_fence))
    return fenced


# ------------------------------------------------------------------------------------------- private sections
# DOC-74 (decision K1 of 2026-10-04): a published document may hold sections that stay in the development repository
# (partner-and-degraded.md parts B and C). They are marked in the source, outside fenced code blocks:
#
#     <!-- export:private-begin [label] -->
#     ...
#     <!-- export:private-end -->
#
# The export replaces each block (markers included) by one line `*label (not published, available on request)*`.
# The anchors of the headings (GitHub style) and of `<a id/name>` inside a block do not exist in the export: links to
# them - from any published Markdown file, also pure anchors of the same file - are replaced like links to an
# unpublished file and listed. The source text stays unchanged; unbalanced or nested markers are an error (R12).

PRIVATE_MARK = "export:private-"
PRIVATE_BEGIN_RE = re.compile(r"^ {0,3}<!--\s*export:private-begin(?:[ \t]+(.*?))?\s*-->\s*$")
PRIVATE_END_RE = re.compile(r"^ {0,3}<!--\s*export:private-end\s*-->\s*$")
HEADING_RE = re.compile(r"^ {0,3}#{1,6}[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$")
HTML_ANCHOR_RE = re.compile(r"<a\s+(?:id|name)\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE)


class PrivateSectionError(ValueError):
    """Markers of a private section that cannot be cut (begin without end, end without begin, nested)."""

    def __init__(self, line: int, message: str):
        super().__init__(message)
        self.line = line


@dataclass(frozen=True)
class PrivateCut:
    """One private section: file, 1-based lines of its markers, label, the anchors that lie in it."""
    path: str
    begin: int
    end: int
    label: str
    anchors: tuple[str, ...]


def heading_slug(heading: str) -> str:
    """GitHub's anchor of a heading text (as tools/docgen/test_mermaid.py: links -> text, no code ticks/emphasis,
    lower case, punctuation except '-' and '_' removed, spaces -> '-')."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading)
    text = text.replace("`", "").replace("*", "").strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def private_sections(path: str, text: str) -> list[PrivateCut]:
    """The private sections of a Markdown text; raises PrivateSectionError for unusable markers."""
    lines = text.splitlines()
    fenced = fenced_lines(lines)
    cuts: list[PrivateCut] = []
    seen: dict[str, int] = {}
    begin: tuple[int, str] | None = None
    anchors: list[str] = []
    for number, (line, is_code) in enumerate(zip(lines, fenced), start=1):
        if is_code:
            continue
        opening, closing = PRIVATE_BEGIN_RE.match(line), PRIVATE_END_RE.match(line)
        if opening:
            if begin is not None:
                raise PrivateSectionError(number, f"privater Abschnitt in privatem Abschnitt (Beginn Zeile {begin[0]})")
            begin, anchors = (number, (opening.group(1) or "").strip()), []
            continue
        if closing:
            if begin is None:
                raise PrivateSectionError(number, "Ende eines privaten Abschnitts ohne Beginn")
            cuts.append(PrivateCut(path, begin[0], number, begin[1], tuple(anchors)))
            begin = None
            continue
        if PRIVATE_MARK in line and line.lstrip().startswith("<!--"):
            raise PrivateSectionError(number, "Markierung eines privaten Abschnitts nicht lesbar "
                                              "(<!-- export:private-begin [Text] --> / <!-- export:private-end -->)")
        heading = HEADING_RE.match(line)
        found = []
        if heading:
            base = heading_slug(heading.group(1))
            count = seen.get(base, 0)
            seen[base] = count + 1
            found.append(base if count == 0 else f"{base}-{count}")
        found += [a.lower() for a in HTML_ANCHOR_RE.findall(line)]
        if begin is not None:
            anchors += found
    if begin is not None:
        raise PrivateSectionError(begin[0], "privater Abschnitt ohne Ende (<!-- export:private-end -->)")
    return cuts


def cut_private_sections(path: str, text: str, note: str = NOT_PUBLISHED) -> tuple[str, list[PrivateCut]]:
    """The text of `path` as the export writes it: every private section replaced by one line naming it. Only
    Markdown files are cut; line ends are kept. Raises PrivateSectionError for unusable markers."""
    if not path.endswith(LINK_SUFFIXES) or PRIVATE_MARK not in text:
        return text, []
    cuts = private_sections(path, text)
    if not cuts:
        return text, []
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    position = 0
    for cut in cuts:
        out += lines[position:cut.begin - 1]
        ending = lines[cut.end - 1][len(lines[cut.end - 1].rstrip("\r\n")):] or "\n"
        out.append((f"*{cut.label} ({note})*" if cut.label else f"*({note})*") + ending)
        position = cut.end
    out += lines[position:]
    return "".join(out), cuts


@dataclass
class Rules:
    entries: list[PathRule] = field(default_factory=list)
    licenses: dict[str, list[str]] = field(default_factory=dict)      # profile -> allowed SPDX identifiers
    source_suffixes: set[str] = field(default_factory=set)
    binary_suffixes: set[str] = field(default_factory=set)
    skip_scan_suffixes: set[str] = field(default_factory=set)
    content_patterns: list[Pattern] = field(default_factory=list)     # public base list (R3)
    secret_patterns: list[Pattern] = field(default_factory=list)      # R7
    evidence: dict = field(default_factory=dict)                      # R11
    integrity: list[dict] = field(default_factory=list)               # R6
    external_targets: set[str] = field(default_factory=set)           # R5: Ziele aus OpenBSW (TOOL-244)
    export: dict = field(default_factory=dict)                        # exporter.py
    substitutions: list[Substitution] = field(default_factory=list)   # export.substitute (PLAT-114)
    overlays: list[Overlay] = field(default_factory=list)             # export.overlay (PLAT-119)
    pattern_file: str = ""                                            # private pattern list (R3), outside the repo
    file: Path | None = None

    def rule_for(self, path: str) -> PathRule | None:
        """Most specific glob wins; among equally specific ones the later entry of the file wins."""
        best: PathRule | None = None
        for entry in self.entries:
            if _match(path, entry.glob) and (best is None or entry.specificity > best.specificity):
                best = entry
        return best


def _match(path: str, glob: str) -> bool:
    """Glob on posix paths. ** crosses directories, * and ? stay inside one path element."""
    return _glob_re(glob).match(path) is not None


_GLOB_CACHE: dict[str, re.Pattern] = {}


def _glob_re(glob: str) -> re.Pattern:
    cached = _GLOB_CACHE.get(glob)
    if cached is not None:
        return cached
    out, i = "", 0
    while i < len(glob):
        char = glob[i]
        if glob.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif glob.startswith("**", i):
            out += ".*"
            i += 2
        elif char == "*":
            out += "[^/]*"
            i += 1
        elif char == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(char)
            i += 1
    compiled = re.compile(out + r"\Z")
    _GLOB_CACHE[glob] = compiled
    return compiled


def load_rules(path: Path) -> Rules:
    """Read distribution.yaml. Raises RulesError for anything that makes the table unusable."""
    if not path.is_file():
        raise RulesError(f"Regeldatei fehlt: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RulesError(f"{path}: kein lesbares YAML ({exc})") from exc
    if not isinstance(raw, dict):
        raise RulesError(f"{path}: erwartet wird eine Zuordnung (Schlüssel: version, paths, ...)")
    if raw.get("version") != RULES_VERSION:
        raise RulesError(f"{path}: version {raw.get('version')!r}, erwartet {RULES_VERSION}")
    rules = Rules(file=path)
    for index, raw_entry in enumerate(raw.get("paths") or []):
        if not isinstance(raw_entry, dict) or "glob" not in raw_entry or "class" not in raw_entry:
            raise RulesError(f"{path}: paths[{index}] braucht glob und class")
        cls = str(raw_entry["class"])
        if cls not in CLASSES:
            raise RulesError(f"{path}: paths[{index}] unbekannte Klasse {cls!r}, erlaubt {', '.join(CLASSES)}")
        rules.entries.append(PathRule(
            glob=str(raw_entry["glob"]), cls=cls, license=raw_entry.get("license"),
            reason=str(raw_entry.get("reason", "")), verify=bool(raw_entry.get("verify", False)),
            binary_ok=bool(raw_entry.get("binary_ok", False)), cleaned=bool(raw_entry.get("cleaned", False)),
            fallback=bool(raw_entry.get("fallback", str(raw_entry["glob"]) == "**")),
            allow=list(raw_entry.get("allow") or []), index=index))
    profiles = raw.get("profiles") or {}
    for name, spec in profiles.items():
        rules.licenses[name] = list((spec or {}).get("licenses") or [])
    suffixes = raw.get("suffixes") or {}
    rules.source_suffixes = {s.lower() for s in suffixes.get("source") or []}
    rules.binary_suffixes = {s.lower() for s in suffixes.get("binary") or []}
    rules.skip_scan_suffixes = {s.lower() for s in suffixes.get("no_text_scan") or []}
    rules.content_patterns = [Pattern.parse(p, f"{path}: content_patterns") for p in raw.get("content_patterns") or []]
    rules.secret_patterns = [Pattern.parse(p, f"{path}: secrets") for p in raw.get("secrets") or []]
    rules.evidence = raw.get("evidence") or {}
    rules.integrity = list(raw.get("integrity") or [])
    rules.external_targets = set(raw.get("external_targets") or [])
    rules.export = raw.get("export") or {}
    for index, entry in enumerate(rules.export.get("substitute") or []):
        rules.substitutions.append(Substitution.parse(entry, f"{path}: export.substitute[{index}]"))
    for index, entry in enumerate(rules.export.get("overlay") or []):
        rules.overlays.append(Overlay.parse(entry, f"{path}: export.overlay[{index}]"))
    rules.pattern_file = str(raw.get("pattern_file", ""))
    return rules


def load_private_patterns(path: Path) -> list[Pattern]:
    """Private list of NDA words. Lives outside the repository; a missing file is not an error (the caller says so)."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise RulesError(f"{path}: erwartet wird eine Zuordnung mit dem Schlüssel patterns")
    return [Pattern.parse(p, f"{path}: patterns") for p in raw.get("patterns") or []]


# ------------------------------------------------------------------------------------------- tree


@dataclass
class Tree:
    """The files of a tree, their classes and a cache of their text."""
    root: Path
    files: list[str]                                          # tree relative posix paths, sorted
    cls: dict[str, str] = field(default_factory=dict)         # path -> class or UNCLASSIFIED
    source_of: dict[str, str] = field(default_factory=dict)   # path -> where the class comes from
    rule_of: dict[str, PathRule | None] = field(default_factory=dict)
    license_of: dict[str, str | None] = field(default_factory=dict)
    _text: dict[str, str | None] = field(default_factory=dict)

    def text(self, path: str) -> str | None:
        """File content as text, or None for binary/too large files. Read once per path."""
        if path in self._text:
            return self._text[path]
        full = self.root / path
        value: str | None = None
        try:
            if full.suffix.lower() not in _no_scan(self) and full.stat().st_size <= MAX_TEXT_BYTES:
                data = full.read_bytes()
                if b"\0" not in data[:8192]:
                    value = data.decode("utf-8", errors="replace")
        except OSError:
            value = None
        self._text[path] = value
        return value

    def exported(self, profile: str) -> list[str]:
        limit = RANK[profile]
        return [p for p in self.files if self.cls.get(p) != UNCLASSIFIED and RANK[self.cls[p]] <= limit]


def _no_scan(tree: Tree) -> set[str]:
    return getattr(tree, "_no_scan_suffixes", set())


def list_files(root: Path, use_git: bool = True) -> list[str]:
    """Tracked files of the tree (git), or a walk of the directory if it is not a repository."""
    if use_git and (root / ".git").exists():
        try:
            out = subprocess.run(["git", "-C", str(root), "ls-files"], capture_output=True, text=True,
                                 encoding="utf-8", check=True).stdout
            files = [line.strip() for line in out.splitlines() if line.strip()]
            if files:
                return sorted(files)
        except (OSError, subprocess.CalledProcessError):
            pass
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        # a checkout of a submodule (external/openbsw linked in for the build proof) is not part of the tree
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS and not (Path(dirpath, d) / ".git").exists()]
        for name in filenames:
            rel = Path(dirpath, name).relative_to(root).as_posix()
            files.append(rel)
    return sorted(files)


def build_tree(root: Path, rules: Rules, use_git: bool = True, files: Sequence[str] | None = None,
               exported: bool = False) -> Tree:
    """Collect the files and classify each of them (manifest first, then the path table).

    exported=True means the tree already is an export: files that are only published as the cleaned output of an
    export rule (cleaned: true) count as open there, because what lies in the tree is the cleaned version.
    """
    tree = Tree(root=root, files=sorted(files) if files is not None else list_files(root, use_git))
    setattr(tree, "_no_scan_suffixes", rules.skip_scan_suffixes)
    manifests = _manifest_classes(tree)
    for path in tree.files:
        manifest_dir = _longest_prefix(path, manifests)
        rule = rules.rule_for(path)
        tree.rule_of[path] = rule
        if manifest_dir is not None:
            cls, license_id, where = manifests[manifest_dir]
            tree.cls[path] = cls
            tree.source_of[path] = where
            tree.license_of[path] = license_id or (rule.license if rule else None)
        elif rule is not None:
            tree.cls[path] = "open" if (exported and rule.cleaned) else rule.cls
            tree.source_of[path] = f"{rules.file.name if rules.file else 'distribution.yaml'}: {rule.glob}"
            tree.license_of[path] = rule.license
        else:
            tree.cls[path] = UNCLASSIFIED
            tree.source_of[path] = ""
            tree.license_of[path] = None
    return tree


def _longest_prefix(path: str, dirs: dict[str, tuple]) -> str | None:
    best = None
    for candidate in dirs:
        if path.startswith(candidate + "/") and (best is None or len(candidate) > len(best)):
            best = candidate
    return best


def _manifest_classes(tree: Tree) -> dict[str, tuple[str, str | None, str]]:
    """Module manifests with distribution:. The manifest beats the path table (concept section 5.2)."""
    out: dict[str, tuple[str, str | None, str]] = {}
    for path in tree.files:
        if not path.endswith("module.yaml"):
            continue
        text = tree.text(path)
        if not text:
            continue
        try:
            raw = yaml.safe_load(text)
        except yaml.YAMLError:
            continue
        if not isinstance(raw, dict) or "distribution" not in raw:
            continue
        cls = str(raw["distribution"])
        if cls not in CLASSES:
            continue
        out[path.rsplit("/", 1)[0]] = (cls, raw.get("license"), f"{path}: distribution")
    return out


# ------------------------------------------------------------------------------------------- checks


def check(root: Path, rules: Rules, profile: str = "open", *, references: bool = True,
          private_patterns: Path | None = None, history: str | None = None, use_git: bool = True,
          skip_rules: Iterable[str] = (), files: Sequence[str] | None = None,
          exported_tree: bool = False) -> Report:
    """Run every rule on the tree and return one report."""
    if profile not in PROFILES:
        raise RulesError(f"unbekanntes Profil {profile!r}, erlaubt {', '.join(PROFILES)}")
    skip = set(skip_rules)
    tree = build_tree(root, rules, use_git=use_git, files=files, exported=exported_tree)
    report = Report(profile=profile, tree=str(root), files=len(tree.files))
    for path in tree.files:
        report.by_class[tree.cls[path]] = report.by_class.get(tree.cls[path], 0) + 1
    in_profile = tree.exported(profile)
    report.exported = len(in_profile)

    patterns = list(rules.content_patterns)
    if private_patterns is None:
        report.skipped.append("R3: keine private Musterliste angegeben (nur die öffentliche Grundliste läuft)")
    elif not Path(private_patterns).is_file():
        report.skipped.append(f"R3: private Musterliste {private_patterns} fehlt - die NDA-Textprüfung lief NUR mit "
                              f"der öffentlichen Grundliste und ist damit unvollständig")
    else:
        extra = load_private_patterns(Path(private_patterns))
        patterns += extra
        report.ran.append(f"R3: private Musterliste mit {len(extra)} Muster(n) geladen")

    # Files with an export clean rule are scanned in their cleaned form, i.e. in the exported tree; in the source
    # tree their raw text would only report what the export removes anyway.
    scanned = list(in_profile)
    if not exported_tree:
        cleaned = {str(entry.get("file")) for entry in rules.export.get("clean") or []}
        held_back = sorted(p for p in in_profile if p in cleaned)
        scanned = [p for p in in_profile if p not in cleaned]
        if held_back:
            report.skipped.append(f"R3/R7: {', '.join(held_back)} werden beim Export bereinigt und dort geprüft")
        # PLAT-114: files with an export substitution are scanned as the export writes them
        replaced = [p for p in scanned if substituted(rules, p, tree.text(p)) is not None]
        for p in replaced:
            tree._text[p] = substituted(rules, p, tree.text(p))
        if replaced:
            report.ran.append(f"R3/R7: {len(replaced)} Datei(en) mit den Ersetzungen des Exports geprüft "
                              f"(export.substitute): {', '.join(replaced[:5])}{' ...' if len(replaced) > 5 else ''}")
        # DOC-74: private sections of published documents are cut by the export (before its links are rewritten)
        index = LinkIndex.of(tree, rules, profile)
        sections: dict[str, int] = {}
        for p in scanned:
            text = tree.text(p)
            if text is None or not p.endswith(LINK_SUFFIXES) or PRIVATE_MARK not in text:
                continue
            try:
                new, cuts = cut_private_sections(p, text)
            except PrivateSectionError as exc:
                report.findings.append(Finding("error", "R12", f"Export-Schnitt nicht moeglich: {exc}", p, exc.line))
                continue
            if cuts:
                tree._text[p] = new
                sections[p] = len(cuts)
        if sections:
            report.ran.append(f"Export-Schnitt: {sum(sections.values())} privater Abschnitt(e) in {len(sections)} "
                              f"Datei(en) fallen beim Export weg ({', '.join(sorted(sections))})")
        # DOC-73: links to unpublished files are replaced by the export; scan the text the export writes
        links: dict[str, int] = {}
        for p in scanned:
            text = tree.text(p)
            if text is None or not p.endswith(LINK_SUFFIXES):
                continue
            new, found = rewrite_unpublished_links(p, text, index)
            if found:
                tree._text[p] = new
                links[p] = len(found)
        if links:
            report.ran.append(f"Export-Links: {sum(links.values())} Verweis(e) in {len(links)} Datei(en) auf nicht "
                              f"veröffentlichte Dateien werden beim Export durch '{NOT_PUBLISHED}' ersetzt")

    steps = [("R1", lambda: _r1_classification(tree, profile)),
             ("R2", lambda: _r2_spdx(tree, rules, profile, in_profile)),
             ("R3", lambda: _r3_content(tree, rules, patterns, scanned)),
             ("R4", lambda: _r4_binaries(tree, rules, in_profile, report)),
             ("R6", lambda: _r6_integrity(tree, rules, in_profile)),
             ("R7", lambda: _r7_secrets(tree, rules, scanned)),
             ("R10", lambda: _r10_notice(tree, rules, profile, in_profile, exported_tree)),
             ("R11", lambda: _r11_evidence(tree, rules, in_profile)),
             ("R13", lambda: overlay_findings(tree, rules, profile, exported_tree))]
    if references:
        steps.append(("R5", lambda: _r5_references(tree, profile, in_profile, set(in_profile) - set(scanned),
                                                         rules.external_targets)))
    else:
        report.skipped.append("R5: Referenzprüfung nicht angefordert (--references)")
    if history:
        steps.append(("R8", lambda: _r8_history(root, rules, profile, history, report, exported_tree)))

    for name, step in steps:
        if name in skip:
            report.skipped.append(f"{name}: per --skip-rule ausgelassen")
            continue
        report.findings += step()
        if name not in (n.split(":")[0] for n in report.ran):
            report.ran.append(name)
    report.findings.sort(key=lambda f: (f.rule, f.path, f.line or 0))
    return report


def _r1_classification(tree: Tree, profile: str) -> list[Finding]:
    out = []
    limit = RANK[profile]
    for path in tree.files:
        cls = tree.cls[path]
        if cls == UNCLASSIFIED:
            out.append(Finding("error", "R1", "keine Klassifizierung (nicht klassifiziert = im Export verboten)",
                               path))
            continue
        if RANK[cls] > limit:
            rule = tree.rule_of.get(path)
            if rule is not None and rule.fallback:
                out.append(Finding("warning", "R1", "nur von der Grundregel erfasst - keine eigene "
                                                    f"Klassifizierung (behandelt als {CLASS_DE[cls]})", path))
            out.append(Finding("info", "R1", f"nicht im Profil {profile}: Klasse {CLASS_DE[cls]}", path,
                               detail=tree.source_of.get(path, "")))
            continue
        rule = tree.rule_of.get(path)
        if rule is not None and rule.fallback:
            out.append(Finding("warning", "R1", "nur von der Grundregel erfasst - keine eigene Klassifizierung "
                                                f"(behandelt als {CLASS_DE[cls]})", path))
        if rule is not None and rule.verify:
            out.append(Finding("warning", "R1", f"Klasse {CLASS_DE[cls]} ist noch nicht belegt (verify: true)", path,
                               detail=rule.reason or rule.glob))
    return out


def _r2_spdx(tree: Tree, rules: Rules, profile: str, in_profile: Sequence[str]) -> list[Finding]:
    allowed = set(rules.licenses.get(profile) or [])
    out = []
    for path in in_profile:
        suffix = Path(path).suffix.lower()
        text = tree.text(path)
        declared = None
        line_no = None
        if text:
            for index, line in enumerate(text.splitlines()[:60], start=1):
                match = SPDX_RE.search(line)
                if match:
                    declared, line_no = match.group(1).strip(), index
                    break
        if declared:
            for token in re.split(r"\s+(?:AND|OR|WITH)\s+", declared):
                token = token.strip("() ")
                if not token:
                    continue
                if LICENSE_REF_RE.fullmatch(token) and profile == "open":
                    out.append(Finding("error", "R2", f"LicenseRef im Profil open verboten: {token}", path, line_no))
                elif token not in allowed and not LICENSE_REF_RE.fullmatch(token):
                    out.append(Finding("error", "R2", f"Lizenz {token} nicht in der Allowlist des Profils {profile}",
                                       path, line_no, detail=", ".join(sorted(allowed))))
                elif LICENSE_REF_RE.fullmatch(token) and token not in allowed:
                    out.append(Finding("error", "R2", f"Lizenz {token} nicht in der Allowlist des Profils {profile}",
                                       path, line_no))
            continue
        if suffix not in rules.source_suffixes:
            continue
        fallback = tree.license_of.get(path)
        if not fallback:
            out.append(Finding("error", "R2", "kein SPDX-Kopf und keine Lizenz aus der Tabelle", path))
        elif fallback not in allowed:
            out.append(Finding("error", "R2", f"Lizenz {fallback} aus der Tabelle nicht in der Allowlist "
                                              f"des Profils {profile}", path))
        else:
            out.append(Finding("warning", "R2", f"kein SPDX-Kopf, Lizenz nur aus der Tabelle ({fallback})", path))
    return out


def _r3_content(tree: Tree, rules: Rules, patterns: Sequence[Pattern], in_profile: Sequence[str]) -> list[Finding]:
    return _scan(tree, patterns, in_profile, "R3", "Textmuster")


def _r7_secrets(tree: Tree, rules: Rules, in_profile: Sequence[str]) -> list[Finding]:
    return _scan(tree, rules.secret_patterns, in_profile, "R7", "Geheimnis")


def _scan(tree: Tree, patterns: Sequence[Pattern], in_profile: Sequence[str], rule_id: str,
          label: str) -> list[Finding]:
    out = []
    if not patterns:
        return out
    for path in in_profile:
        allow = set(tree.rule_of[path].allow) if tree.rule_of.get(path) else set()
        text = tree.text(path)
        if text is None:
            continue
        for index, line in enumerate(text.splitlines(), start=1):
            for pattern in patterns:
                if pattern.id in allow:
                    continue
                match = pattern.regex.search(line)
                if match:
                    out.append(Finding(pattern.severity, rule_id, f"{label} {pattern.id}: {pattern.text}", path,
                                       index, detail=_snippet(match.group(0))))
    return out


def _snippet(text: str, limit: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "..."


def _r4_binaries(tree: Tree, rules: Rules, in_profile: Sequence[str], report: Report) -> list[Finding]:
    out = []
    pdfs = []
    for path in in_profile:
        suffix = Path(path).suffix.lower()
        if suffix not in rules.binary_suffixes:
            continue
        rule = tree.rule_of.get(path)
        if rule is None or not rule.binary_ok:
            out.append(Finding("error", "R4", f"Binär-/Dokumentdatei {suffix} ohne ausdrücklichen Eintrag "
                                              f"(binary_ok) im Profil", path))
        elif suffix == ".pdf":
            pdfs.append(path)
    if pdfs:
        out += _scan_pdfs(tree, rules, pdfs, report)
    return out


def _scan_pdfs(tree: Tree, rules: Rules, pdfs: Sequence[str], report: Report) -> list[Finding]:
    """Text of the first and last pages against the content patterns (rule of 2026-09-17)."""
    try:
        import logging                                           # noqa: PLC0415

        from pypdf import PdfReader                              # noqa: PLC0415 - optional dependency
        logging.getLogger("pypdf").setLevel(logging.ERROR)       # a broken PDF becomes a finding, not log noise
    except ImportError:
        report.skipped.append(f"R4: pypdf fehlt, {len(pdfs)} PDF(s) nicht auf Aufdrucke geprüft")
        return []
    out = []
    for path in pdfs:
        try:
            reader = PdfReader(str(tree.root / path))
            pages = list(reader.pages[:4]) + list(reader.pages[-1:])
            text = "\n".join(page.extract_text() or "" for page in pages)
        except Exception as exc:                                  # noqa: BLE001 - a broken PDF is a finding
            out.append(Finding("warning", "R4", f"PDF nicht lesbar: {exc}", path))
            continue
        for pattern in rules.content_patterns:
            match = pattern.regex.search(text)
            if match:
                out.append(Finding("error", "R4", f"PDF-Aufdruck {pattern.id}: {pattern.text}", path,
                                   detail=_snippet(match.group(0))))
    return out


def _r5_references(tree: Tree, profile: str, in_profile: Sequence[str],
                   held_back: set[str] = frozenset(), external: set[str] = frozenset()) -> list[Finding]:
    """Does a file of the profile point to something that is not exported?"""
    out = []
    inside = set(in_profile)
    suffix_index: dict[str, list[str]] = {}
    for path in tree.files:
        parts = path.split("/")
        for start in range(len(parts)):
            suffix_index.setdefault("/".join(parts[start:]), []).append(path)
    targets: dict[str, list[str]] = {}
    for path in tree.files:
        if path.endswith("CMakeLists.txt") or path.endswith(".cmake"):
            text = tree.text(path) or ""
            for name in DEFINE_TARGET_RE.findall(text):
                targets.setdefault(name, []).append(path)

    def outside(candidates: Sequence[str]) -> bool:
        """True if every candidate is outside the profile (an ambiguous name is not reported)."""
        return bool(candidates) and all(candidate not in inside for candidate in candidates)

    for path in in_profile:
        text = tree.text(path)
        if text is None:
            continue
        lines = text.splitlines()
        base = path.rsplit("/", 1)[0] if "/" in path else ""
        is_cmake = path.endswith("CMakeLists.txt") or path.endswith(".cmake")
        is_c = Path(path).suffix.lower() in {".c", ".cpp", ".h", ".hpp", ".cc", ".inc"}
        is_py = path.endswith(".py")
        is_yaml = path.endswith(".yaml") or path.endswith(".yml")
        for index, line in enumerate(lines, start=1):
            if is_c:
                match = INCLUDE_RE.match(line)
                if match:
                    hits = suffix_index.get(match.group(1).replace("\\", "/"), [])
                    if outside(hits):
                        out.append(Finding("error", "R5", f"#include zeigt aus dem Profil heraus: {match.group(1)}",
                                           path, index,
                                           detail=", ".join(f"{h} [{CLASS_DE[tree.cls[h]]}]" for h in hits[:3])))
            elif is_cmake:
                match = ADD_SUBDIR_RE.search(line)
                if match:
                    target = _join(base, match.group(1).strip('"'))
                    if target and not any(p == target or p.startswith(target + "/") for p in inside) \
                            and any(p.startswith(target + "/") for p in tree.files):
                        out.append(Finding("error", "R5", f"add_subdirectory auf nicht exportierten Ordner: {target}",
                                           path, index))
            elif is_py:
                match = PY_IMPORT_RE.match(line)
                if match:
                    module = (match.group(1) or match.group(2) or "").split(".")
                    candidate = "/".join(module) + ".py"
                    hits = suffix_index.get(candidate, []) + suffix_index.get("/".join(module) + "/__init__.py", [])
                    if outside(hits):
                        out.append(Finding("error", "R5", f"import zeigt aus dem Profil heraus: {'.'.join(module)}",
                                           path, index))
        if is_cmake:
            for name, line_no in _linked_targets(text):
                # TOOL-244: ein Ziel, das auch eine Datei des Profils definiert (Platzhalter in den Bautests der
                # Familien), ist im Export vorhanden; gemeldet wird nur, wenn jede Definition draussen liegt
                where = targets.get(name)
                if where and name not in external and all(w not in inside for w in where):
                    out.append(Finding("error", "R5", f"target_link_libraries auf {name}, definiert in einer nicht "
                                                      f"exportierten Datei", path, line_no, detail=where[0]))
        if is_yaml:
            out += _yaml_references(tree, path, inside)
    out += _named_private_dirs(tree, [p for p in in_profile if p not in held_back], inside)
    return out


NAME_SCAN_SUFFIXES = {".py", ".c", ".cpp", ".h", ".hpp", ".cmake", ".txt", ".yaml", ".yml", ".json", ".j2"}


def _named_private_dirs(tree: Tree, in_profile: Sequence[str], inside: set[str]) -> list[Finding]:
    """Warning: code or configuration of the profile names a directory that is not exported (a test that loads
    the project of the own board as fixture, a tool path of the Studio). Such a file usually breaks in the export,
    which is exactly what R9 then shows; this finds it before. Only distinctive names (with '_', a digit or an
    inner capital) are searched, so common words like 'studio' do not flood the report."""
    dirs: dict[str, set[str]] = {}
    for path in tree.files:
        parts = path.split("/")
        for depth in range(1, len(parts)):
            dirs.setdefault("/".join(parts[:depth]), set()).add(path)
    names: dict[str, str] = {}
    for directory, files in dirs.items():
        name = directory.rsplit("/", 1)[-1]
        distinctive = "_" in name or any(c.isdigit() for c in name) or any(c.isupper() for c in name[1:])
        if distinctive and len(name) >= 6 and not (files & inside):
            parent_open = any(p in inside for p in dirs.get(directory.rsplit("/", 1)[0], ())) if "/" in directory \
                else True
            if parent_open:
                names.setdefault(name, directory)
    if not names:
        return []
    pattern = re.compile(r"(?<![A-Za-z0-9_])(" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
                         + r")(?![A-Za-z0-9_])")
    out = []
    for path in in_profile:
        if Path(path).suffix.lower() not in NAME_SCAN_SUFFIXES and not path.endswith("CMakeLists.txt"):
            continue
        rule = tree.rule_of.get(path)
        if rule is not None and "private_dir_names" in rule.allow:
            continue
        text = tree.text(path)
        if not text:
            continue
        for index, line in enumerate(text.splitlines(), start=1):
            for match in pattern.finditer(line):
                out.append(Finding("warning", "R5", f"nennt den nicht exportierten Ordner {names[match.group(1)]}",
                                   path, index))
                break
    return out


def _linked_targets(text: str) -> list[tuple[str, int]]:
    """(target, 1-based line) of every library named in a target_link_libraries call (not the linking target)."""
    out = []
    for match in LINK_RE.finditer(text):
        line_no = text.count("\n", 0, match.start()) + 1
        names = []
        for token in re.split(r"\s+", match.group(1).strip()):
            token = token.strip('"')
            if token and token.upper() not in {"PUBLIC", "PRIVATE", "INTERFACE"} and not token.startswith("$"):
                names.append(token)
        out += [(name, line_no) for name in names[1:]]
    return out


def _yaml_references(tree: Tree, path: str, inside: set[str]) -> list[Finding]:
    """board:/derivative:/modules: of a project or board file must point into the profile."""
    out = []
    text = tree.text(path) or ""
    for index, line in enumerate(text.splitlines(), start=1):
        match = re.match(r"\s*(board|derivative|mcu)\s*:\s*([A-Za-z0-9_./\-]+)", line)
        if not match:
            continue
        key, name = match.group(1), match.group(2).strip('"\'')
        if key == "board":
            candidates = [p for p in tree.files if p.startswith(f"boards/{name}/")]
        else:
            candidates = [p for p in tree.files if p.endswith(f"/{name}.yaml") and p.startswith("mcudb/")]
        if candidates and all(c not in inside for c in candidates):
            out.append(Finding("error", "R5", f"{key}: {name} ist nicht im Profil", path, index,
                               detail=candidates[0]))
    return out


def _join(base: str, rel: str) -> str:
    parts = [p for p in (f"{base}/{rel}" if base else rel).split("/") if p not in ("", ".")]
    out: list[str] = []
    for part in parts:
        if part == "..":
            if out:
                out.pop()
        else:
            out.append(part)
    return "/".join(out)


def _r6_integrity(tree: Tree, rules: Rules, in_profile: Sequence[str]) -> list[Finding]:
    """Checksum files of copied third party sources, and a LICENSE/README next to every such directory."""
    out = []
    inside = set(in_profile)
    for path in in_profile:
        if not path.endswith(".sha256"):
            continue
        text = tree.text(path) or ""
        base = path.rsplit("/", 1)[0]
        for index, line in enumerate(text.splitlines(), start=1):
            match = re.match(r"\s*([0-9a-fA-F]{64})\s+\*?(\S+)", line)
            if not match:
                continue
            digest, name = match.group(1).lower(), match.group(2)
            target = tree.root / _join(base, name)
            if not target.is_file():
                out.append(Finding("warning", "R6", f"Prüfsummendatei nennt {name}, die Datei fehlt", path, index))
                continue
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
            if actual != digest:
                out.append(Finding("error", "R6", f"SHA-256 von {name} weicht ab", path, index,
                                   detail=f"{actual[:16]}... statt {digest[:16]}..."))
    for entry in rules.integrity:
        directory = str(entry.get("dir", "")).rstrip("/")
        if not any(p.startswith(directory + "/") for p in inside):
            continue
        for required in entry.get("requires") or []:
            if f"{directory}/{required}" not in inside:
                out.append(Finding("error", "R6", f"Fremdquelle ohne {required}", directory + "/"))
    for path in in_profile:
        parts = path.split("/")
        if "3rdparty" not in parts and "sources" not in parts:
            continue
        key = "3rdparty" if "3rdparty" in parts else "sources"
        root_dir = "/".join(parts[:parts.index(key) + 2])
        if not any(p.startswith(root_dir + "/") and p.rsplit("/", 1)[-1].upper().startswith(("LICENSE", "README"))
                   for p in inside):
            out.append(Finding("warning", "R6", "Fremdquelle ohne LICENSE oder README im Ordner", root_dir + "/"))
    return out


def _r8_history(root: Path, rules: Rules, profile: str, revisions: str, report: Report,
                exported_tree: bool = False) -> list[Finding]:
    """Every path that ever held a blob in a revision range. A fresh export has exactly one commit."""
    out: list[Finding] = []
    try:
        commits = subprocess.run(["git", "-C", str(root), "rev-list", "--count", revisions], capture_output=True,
                                 text=True, encoding="utf-8", check=True).stdout.strip()
        touched = subprocess.run(["git", "-C", str(root), "log", "--format=", "--name-only", "--no-renames",
                                  revisions], capture_output=True, text=True, encoding="utf-8", check=True).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        report.skipped.append(f"R8: git log {revisions} nicht ausführbar ({exc})")
        return out
    if commits.isdigit() and int(commits) > 1:
        out.append(Finding("warning", "R8", f"{commits} Commits im Bereich {revisions}; ein frischer Export hat "
                                            f"genau einen (Entscheidung F5)"))
    names = sorted({line.strip() for line in touched.splitlines() if line.strip()})
    tree = build_tree(root, rules, use_git=False, files=names, exported=exported_tree)
    for path in tree.files:
        cls = tree.cls[path]
        if cls == UNCLASSIFIED:
            out.append(Finding("error", "R8", f"Objekt der Historie ohne Klassifizierung ({revisions})", path))
        elif RANK[cls] > RANK[profile]:
            out.append(Finding("error", "R8", f"Objekt der Historie gehört zu Klasse {CLASS_DE[cls]} ({revisions})",
                               path))
    return out


def _r10_notice(tree: Tree, rules: Rules, profile: str, in_profile: Sequence[str],
                exported_tree: bool = False) -> list[Finding]:
    """The export needs LICENSE, NOTICE and the contribution note; NOTICE must list every licence in use."""
    out = []
    required = (rules.export.get("required_files") or ["LICENSE", "NOTICE"])
    inside = set(in_profile)
    for name in required:
        if name not in inside:
            out.append(Finding("error" if exported_tree else "warning", "R10",
                               f"{name} fehlt im Baum (wird beim Export erzeugt)", name))
    notice = tree.text("NOTICE") if "NOTICE" in inside else None
    if notice is not None:
        used = sorted({lic for lic in (tree.license_of.get(p) for p in in_profile) if lic})
        for lic in used:
            if lic not in notice:
                out.append(Finding("error", "R10", f"NOTICE nennt die verwendete Lizenz {lic} nicht", "NOTICE"))
        # PLAT-120: a profile that allows the generator output exception (SPDX addition written into every
        # generated file) has to carry its section in the NOTICE
        for addition in sorted(a for a in (rules.licenses.get(profile) or []) if a.startswith("AdditionRef-")):
            if addition not in notice:
                out.append(Finding("error", "R10", f"NOTICE enthält den Abschnitt der Ausnahme {addition} nicht",
                                   "NOTICE"))
    return out


def _r11_evidence(tree: Tree, rules: Rules, in_profile: Sequence[str]) -> list[Finding]:
    """Decision of 2026-09-17: values of the derivative database only with public evidence."""
    out = []
    globs = rules.evidence.get("files") or []
    forbidden = [Pattern.parse(p, "evidence.forbidden_sources") for p in rules.evidence.get("forbidden_sources") or []]
    if not globs or not forbidden:
        return out
    for path in in_profile:
        if not any(_match(path, g) for g in globs):
            continue
        text = tree.text(path)
        if text is None:
            continue
        for index, line in enumerate(text.splitlines(), start=1):
            if "source" not in line:
                continue
            for pattern in forbidden:
                match = pattern.regex.search(line)
                if match:
                    out.append(Finding(pattern.severity, "R11", f"Beleg ohne öffentliche Quelle: {pattern.text}",
                                       path, index, detail=_snippet(match.group(0))))
    out += _r11_unverified(tree, rules, in_profile)
    return out


_VERIFY_TRUE = re.compile(r"\bverify\s*:\s*true\b", re.IGNORECASE)
_NAME_KEY = re.compile(r"\bname\s*:\s*([A-Za-z0-9_.\[\]-]+)")
_BLOCK_KEY = re.compile(r"^(\s*)(?:-\s+)?([A-Za-z0-9_.-]+)\s*:")


def _yaml_key_path(lines: Sequence[str], index: int) -> str:
    """Readable key of the value on lines[index]: enclosing block keys plus the entry name or the own key."""
    line = lines[index].split("#", 1)[0]
    named = _NAME_KEY.search(line)
    own = _BLOCK_KEY.match(line)
    indent = len(line) - len(line.lstrip())
    parts: list[str] = []
    for prev in reversed(lines[:index]):
        stripped = prev.split("#", 1)[0].rstrip()
        match = _BLOCK_KEY.match(stripped)
        if not match or len(match.group(1)) >= indent:
            continue
        item = stripped.lstrip().startswith("- ")
        if stripped.endswith(":") or item:
            entry = _NAME_KEY.search(stripped)
            parts.append(entry.group(1) if entry and item else match.group(2))
            indent = len(match.group(1))
            if indent == 0:
                break
    parts.reverse()
    if named:
        parts.append(named.group(1))
    elif own and own.group(2) != "verify":
        parts.append(own.group(2))
    return ".".join(parts)


def _r11_unverified(tree: Tree, rules: Rules, in_profile: Sequence[str]) -> list[Finding]:
    """F8 (lizenz-und-release.md): a value marked ``verify: true`` has no public evidence yet and must not be exported.

    The export aborts with the list (error) instead of replacing the value by a placeholder, because the tree has to
    stay buildable and a placeholder would change generated code. A value is released per file by a decision
    (``evidence.unverified.accepted``: glob + reason), so nothing is silently published.
    """
    cfg = rules.evidence.get("unverified") or {}
    globs = rules.evidence.get("files") or []
    if not cfg or not globs:
        return []
    accepted = [a.get("glob", "") for a in cfg.get("accepted") or []]
    severity = str(cfg.get("severity", "error"))
    out = []
    for path in in_profile:
        if not any(_match(path, g) for g in globs) or any(_match(path, g) for g in accepted):
            continue
        text = tree.text(path)
        if text is None:
            continue
        lines = text.splitlines()
        for index, line in enumerate(lines):
            code = line.split("#", 1)[0]
            if _VERIFY_TRUE.search(code):
                out.append(Finding(severity, "R11", "Wert ohne öffentlichen Beleg (verify: true, F8) - nicht im "
                                                    "offenen Export", path, index + 1,
                                   detail=_yaml_key_path(lines, index)))
    return out
