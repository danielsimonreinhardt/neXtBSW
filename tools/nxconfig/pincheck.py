"""Release strictness of the workspace pin (TOOL-65, doc/architecture/customer-project.md gap N10).

`tools/nxconfig/workspace.py` (ARCH-56) already answers the two questions every run needs: does `nextbsw.path`
name the tree this tool runs from, and does `nextbsw.version` equal that tree's `VERSION`. That is the *identity*
of the pin. This module answers the second half, the one a release depends on - the **provenance** of the pinned
tree:

  - it stands on the **annotated** tag `v<version>` (not a lightweight one, and not a tag that merely exists
    somewhere else in the repository),
  - that tag is **reachable from `master`**, so it is a published baseline and not a side branch,
  - the tree is **clean**, so what is built is what the tag says,
  - every **overlay** (licensed tree, model B, customer-project.md 5.8) carries the same version and is itself a
    clean, tagged tree.

Why this is separate from `workspace.load`
------------------------------------------
Every one of these answers costs a `git` call in the pinned tree. `workspace.load` runs on every `nxconfig check`,
every generator start and every keystroke-triggered live check of the Studio; putting four subprocesses in that
path would be paid a few hundred times a day for an answer that only matters when an artefact leaves the house.
So the check has its own entry point and its own callers: `tools/ci run|check --release`, the status line of the
Studio and its git page.

Error or warning
----------------
A **development pin** - a pinned tree whose commit carries no release tag at all - is a **warning**, not an error.
That is the state every customer starts in (the wizard clones `master`, the first tag comes later) and the state
of anybody who follows neXtBSW between two releases. Daily work must not be red for it. `--release` turns that
warning into an error: an image that is handed over must be reproducible from a tag. Everything else - dirty,
wrong tag, lightweight tag, tag off `master`, an overlay of another version - is an **error** in both modes,
because none of them is a normal state anybody works in.

Dependencies and language
-------------------------
Standard library, plus PyYAML only inside `load_pin` - the callers that already hold the parsed pin
(`tools/ci` through `tools/test/workspace.py`, the Studio through its own reader) pass it in and pull in nothing.
No pydantic, no ruamel, no Qt: `tools/ci` loads this file directly, exactly as it loads `version.py`.

The texts of the findings are German, like the report of `tools/ci` and the banners of the Studio that print
them; the code comments and the API stay English as everywhere else. Every finding carries file, line and the
YAML path of the value it is about (`nextbsw.version`, `nextbsw.path`, `search.overlays[i].version`) and says
what to do.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

try:                                                # inside the package (nxconfig, Studio)
    from . import version as nxversion
except ImportError:                                 # loaded from its file (tools/ci): no package around it
    import importlib.util as _importlib_util

    _spec = _importlib_util.spec_from_file_location("nx_version_for_pincheck",
                                                    Path(__file__).resolve().parent / "version.py")
    nxversion = _importlib_util.module_from_spec(_spec)
    _spec.loader.exec_module(nxversion)

PIN_FILE = "nextbsw.yaml"

#: Where `master` may be found in a pinned tree. A submodule is checked out detached and usually has no local
#: branches at all, so the remote-tracking ref is the one that normally answers. The names are tried in order;
#: the first that resolves decides. Nothing here is board or customer specific.
MASTER_REFS = ("master", "origin/master", "refs/remotes/origin/master")

#: git calls are cheap but must never hang a GUI status line.
GIT_TIMEOUT = 30

ERROR, WARNING, OK = "error", "warning", "ok"


# ---------------------------------------------------------------------------- findings


@dataclass(frozen=True)
class PinFinding:
    """One statement about the pin: what, where in `nextbsw.yaml`, and what to do about it."""

    severity: str                       # "error" | "warning" | "ok"
    code: str                           # stable id, see CODES - callers may key on it, texts may be reworded
    text: str                           # German, names the deviation and the way out
    path: tuple = ()                    # YAML path in nextbsw.yaml, e.g. ("nextbsw", "version")
    line: int | None = None             # 1-based line of that value, None if it could not be located
    file: Path | None = None            # the pin file

    @property
    def yaml_path(self) -> str:
        out = ""
        for part in self.path:
            out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
        return out

    def where(self) -> str:
        """"nextbsw.yaml:4 (nextbsw.version)" - the place every message has to name."""
        parts = []
        if self.file is not None:
            parts.append(f"{self.file.name}:{self.line}" if self.line else self.file.name)
        if self.path:
            parts.append(f"({self.yaml_path})" if parts else self.yaml_path)
        return " ".join(parts)

    def __str__(self) -> str:
        where = self.where()
        return f"{where}: {self.text}" if where else self.text


#: Every code this module can produce, with what it means. Kept in one place so a caller (the Studio status line,
#: the git page) can decide per code without reading the texts.
CODES = {
    "ok": "the pinned tree is a clean, annotated, reachable release tag",
    "pin-unreadable": "nextbsw.yaml could not be parsed at all",
    "version-missing": "nextbsw.version is missing or is no MAJOR.MINOR.PATCH",
    "path-missing": "nextbsw.path is missing",
    "tree-missing": "the folder of nextbsw.path is no neXtBSW tree",
    "tree-version": "the tree's VERSION differs from nextbsw.version",
    "no-git": "the pinned tree is no git checkout, so its provenance cannot be shown",
    "dirty": "the pinned tree has local changes",
    "no-tag": "development pin: the checked-out commit carries no release tag",
    "tag-mismatch": "the commit carries a release tag, but another one than v<version>",
    "tag-lightweight": "v<version> exists but is a lightweight tag, not an annotated one",
    "tag-unsigned": "v<version> is annotated and its message says 'safety release', but the tag carries no signature",
    "tag-elsewhere": "v<version> exists but points at another commit",
    "tag-unreachable": "v<version> is not reachable from master",
    "master-unknown": "no master ref in the tree, so reachability cannot be shown",
    "overlay-version": "an overlay names another version than nextbsw.version",
    "overlay-missing": "the folder of an overlay does not exist",
}


@dataclass(frozen=True)
class PinStatus:
    """The verdict for one `nextbsw.yaml`: the findings, and the short answers a caller usually wants."""

    file: Path                                  # nextbsw.yaml (for `check_tree`, which has no pin: the tree)
    version: str = ""                           # nextbsw.version as pinned ("" if unreadable)
    tree: Path | None = None                    # the pinned tree (resolved), None if it could not be located
    release: bool = False                       # was this run judged with release strictness?
    development: bool = False                   # the pinned tree carries no release tag
    tag: str = ""                               # the annotated release tag the tree stands on ("" if none)
    describe: str = ""                          # git describe --tags --always --dirty of the pinned tree
    findings: tuple[PinFinding, ...] = ()

    @property
    def errors(self) -> tuple[PinFinding, ...]:
        return tuple(f for f in self.findings if f.severity == ERROR)

    @property
    def warnings(self) -> tuple[PinFinding, ...]:
        return tuple(f for f in self.findings if f.severity == WARNING)

    @property
    def ok(self) -> bool:
        """True if nothing speaks against this pin - in release mode that also means: no development pin."""
        return not self.errors and not self.warnings

    @property
    def usable(self) -> bool:
        """True if the pin may be built from: a development pin is usable, an error is not."""
        return not self.errors

    @property
    def severity(self) -> str:
        return ERROR if self.errors else (WARNING if self.warnings else OK)

    @property
    def headline(self) -> str:
        """One line for a status bar or a report row."""
        if self.errors:
            return f"Pin {self.version or '?'}: {len(self.errors)} Fehler"
        if self.warnings:
            return f"Pin {self.version or '?'}: Entwicklungsstand" if self.development \
                else f"Pin {self.version or '?'}: {len(self.warnings)} Warnung(en)"
        return f"Pin {self.version} = Tag {self.tag}" if self.tag else f"Pin {self.version}"

    @property
    def text(self) -> str:
        """Headline and every finding with its place - the report and the banner print the same words."""
        return "\n".join([self.headline] + [str(f) for f in self.findings if f.severity != OK])


# ---------------------------------------------------------------------------- reading the pin


def load_pin(file: Path) -> tuple[dict, str, str]:
    """(pin mapping, raw text, error). The only place that needs PyYAML; callers with a parsed pin skip it."""
    file = Path(file)
    try:
        text = file.read_text(encoding="utf-8")
    except OSError as exc:
        return {}, "", f"{PIN_FILE} nicht lesbar: {exc.strerror or exc}"
    try:
        import yaml
    except ImportError:                             # pragma: no cover - PyYAML is a core requirement
        return {}, text, "PyYAML fehlt; der Pin kann nicht gelesen werden"
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return {}, text, f"{PIN_FILE} ist kein gueltiges YAML: {exc}"
    if not isinstance(data, dict):
        return {}, text, f"{PIN_FILE}: oberste Ebene ist keine Abbildung"
    return data, text, ""


# ---------------------------------------------------------------------------- line numbers without ruamel


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _skip(line: str) -> bool:
    stripped = line.strip()
    return not stripped or stripped.startswith("#")


def _first_indent(lines: list[str], lo: int, hi: int, fallback: int) -> int:
    for i in range(lo, hi):
        if not _skip(lines[i]):
            return _indent(lines[i])
    return fallback


def _block_end(lines: list[str], start: int, hi: int, indent: int) -> int:
    """First line at or after `start` that leaves the block a `key:` at `indent` opened.

    A sequence written at the key's own column (`overlays:` then `- path: x` at indent 0) still belongs to it,
    so a dash at exactly `indent` does not end the block.
    """
    for i in range(start, hi):
        if _skip(lines[i]):
            continue
        here = _indent(lines[i])
        if here > indent:
            continue
        if here == indent and lines[i].lstrip().startswith("-"):
            continue                                # `key:` followed by `- item` at the same column
        return i
    return hi


def _item_end(lines: list[str], start: int, hi: int, indent: int) -> int:
    """First line at or after `start` that leaves one sequence item: the next dash at `indent` ends it."""
    for i in range(start, hi):
        if _skip(lines[i]):
            continue
        if _indent(lines[i]) <= indent:
            return i
    return hi


def line_of_path(text: str, path) -> int | None:
    """1-based line of the deepest element of `path` that block-style YAML `text` holds, else None.

    Same contract as `nxconfig.config.line_of`, but without ruamel: `tools/ci` loads this module on its own, and
    a pin file is the small, flat, block-style file the wizard writes. A value written in flow style
    (`overlays: [{...}]`) gives the line of the flow node itself - the deepest element that can be located
    honestly. A layout this scanner does not recognise gives None; it never guesses a line.
    """
    lines = text.splitlines()
    lo, hi, indent, found = 0, len(lines), _first_indent(lines, 0, len(lines), 0), None
    for part in path:
        if isinstance(part, int):
            hit = _seq_item(lines, lo, hi, indent, part)
        else:
            hit = _map_key(lines, lo, hi, indent, str(part))
        if hit is None:
            return found
        index, lo, hi, inline = hit
        found = index + 1
        if inline:                                  # flow style from here on: nothing deeper has its own line
            return found
        indent = _first_indent(lines, lo, hi, indent)
    return found


def _map_key(lines, lo: int, hi: int, indent: int, key: str):
    """(line index, child lo, child hi, value is flow style) of `key` in the mapping at `indent`."""
    for i in range(lo, hi):
        if _skip(lines[i]):
            continue
        here = _indent(lines[i])
        if here < indent:
            break
        if here != indent:
            continue
        name, sep, rest = lines[i].strip().partition(":")
        if not sep or name.strip().strip("\"'") != key:
            continue
        rest = rest.strip()
        inline = bool(rest) and not rest.startswith("#")
        return i, i + 1, _block_end(lines, i + 1, hi, indent), inline
    return None


def _seq_item(lines, lo: int, hi: int, indent: int, wanted: int):
    """(line index, child lo, child hi, flow style) of item `wanted` of the sequence at `indent`.

    An item that opens a mapping on its own line (`- path: x`) is rewritten in the working copy so the mapping
    scanner sees it at the column its keys really start in.
    """
    seen = -1
    for i in range(lo, hi):
        if _skip(lines[i]):
            continue
        here = _indent(lines[i])
        if here < indent:
            break
        if here != indent or not lines[i].lstrip().startswith("-"):
            continue
        seen += 1
        if seen != wanted:
            continue
        body = lines[i].lstrip()[1:]
        rest = body.strip()
        end = _item_end(lines, i + 1, hi, indent)
        if rest.startswith(("{", "[")):             # - {path: x, version: y}
            return i, i + 1, end, True
        if rest:                                    # - path: x  -> the mapping starts on this line
            lines[i] = " " * (indent + 1 + len(body) - len(body.lstrip(" "))) + rest
            return i, i, end, False
        return i, i + 1, end, False
    return None


# ---------------------------------------------------------------------------- git


def git(tree: Path, *args: str) -> tuple[int, str]:
    """(exit code, stdout) of `git -C <tree> <args>`; (-1, reason) if git could not be started at all."""
    try:
        done = subprocess.run(["git", "-C", str(tree), *args], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, str(exc)
    return done.returncode, done.stdout.strip()


@dataclass
class TreeFacts:
    """What git says about one tree. Collected once so every finding is made from the same picture."""

    is_git: bool = False
    head: str = ""
    dirty: bool = False
    describe: str = ""
    tags_at_head: tuple[str, ...] = ()          # release tags vX.Y.Z on HEAD (annotated and lightweight)
    tag_kind: str = ""                          # "tag" (annotated) | "commit" (lightweight) | "" (not there)
    tag_commit: str = ""                        # commit the wanted tag points at ("" if there is no such tag)
    tag_safety: bool = False                    # the annotation message of the wanted tag says "safety release"
    tag_signed: bool = False                    # the wanted tag object carries a signature (SSH, PGP or X.509)
    master_ref: str = ""                        # the ref reachability was measured against ("" if none)
    reachable: bool = False


#: A signature block inside a tag object (git appends it to the message): SSH (cm-plan.md G3, Q-CM2), PGP, X.509.
_SIGNATURE_MARKERS = ("-----BEGIN SSH SIGNATURE-----", "-----BEGIN PGP SIGNATURE-----",
                      "-----BEGIN SIGNED MESSAGE-----")
_SAFETY_RELEASE = re.compile(r"\bsafety[\s_-]+release\b", re.IGNORECASE)
#: A status line like "Status: development release" must not trigger, and neither must a negation such as
#: "not a safety release" / "no safety release" - the release kind is claimed, not merely mentioned.
_SAFETY_NEGATED = re.compile(r"\b(?:not|no|non|kein\w*|nicht)\s+(?:a\s+|an\s+|ein\w*\s+)?safety[\s_-]+release\b",
                             re.IGNORECASE)


def tag_message_facts(raw: str) -> tuple[bool, bool]:
    """(says safety release, is signed) from the raw tag object (`git cat-file tag`). Pure, tested on its own."""
    signed = any(marker in raw for marker in _SIGNATURE_MARKERS)
    _, _, message = raw.partition("\n\n")
    for marker in _SIGNATURE_MARKERS:
        message = message.split(marker, 1)[0]
    message = _SAFETY_NEGATED.sub("", message)
    return bool(_SAFETY_RELEASE.search(message)), signed


def facts(tree: Path, wanted_tag: str, run=git, master_refs=MASTER_REFS) -> TreeFacts:
    """Everything the rule needs about `tree`, in one pass. `run` is injected by the tests."""
    tree = Path(tree)
    code, head = run(tree, "rev-parse", "HEAD")
    if code != 0:
        return TreeFacts()
    # TOOL-86: a tree unpacked from a release archive (source: archive) has no .git of its own. Sitting inside a
    # customer repository that DOES have one, plain `git -C tree rev-parse HEAD` still succeeds - git walks up
    # and silently answers with the ENCLOSING repository's HEAD, tags and branches, none of which describe this
    # tree. `--show-toplevel` says which repository git actually found; if that is not `tree` itself, the tree
    # carries no git of its own and every other answer here would be about the wrong repository.
    code, toplevel = run(tree, "rev-parse", "--show-toplevel")
    if code != 0 or Path(toplevel).resolve() != tree.resolve():
        return TreeFacts()
    found = TreeFacts(is_git=True, head=head)
    code, out = run(tree, "status", "--porcelain")
    found.dirty = code == 0 and bool(out)
    code, out = run(tree, "describe", "--tags", "--always", "--dirty")
    found.describe = out if code == 0 else ""
    code, out = run(tree, "tag", "--points-at", "HEAD")
    if code == 0:
        found.tags_at_head = tuple(sorted(t for t in out.split() if nxversion.version_of_tag(t) is not None))
    code, out = run(tree, "cat-file", "-t", f"refs/tags/{wanted_tag}")
    found.tag_kind = out if code == 0 and out in ("tag", "commit") else ""
    if found.tag_kind:
        code, out = run(tree, "rev-list", "-n", "1", f"refs/tags/{wanted_tag}")
        found.tag_commit = out if code == 0 else ""
    if found.tag_kind == "tag":
        code, out = run(tree, "cat-file", "tag", f"refs/tags/{wanted_tag}")
        if code == 0:
            found.tag_safety, found.tag_signed = tag_message_facts(out)
    for ref in master_refs:
        code, _ = run(tree, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        if code == 0:
            found.master_ref = ref
            break
    if found.tag_kind and found.master_ref:
        code, _ = run(tree, "merge-base", "--is-ancestor", f"refs/tags/{wanted_tag}^{{commit}}", found.master_ref)
        found.reachable = code == 0
    return found


# ---------------------------------------------------------------------------- the rule


@dataclass
class _Sink:
    file: Path | None
    text: str
    out: list = field(default_factory=list)

    def add(self, severity: str, code: str, path, text: str) -> None:
        self.out.append(PinFinding(severity, code, text, tuple(path), line_of_path(self.text, tuple(path)),
                                   self.file))


def _check_tree(sink: _Sink, tree: Path, version: str, path, label: str, source: str, release: bool,
                run=git) -> TreeFacts:
    """The provenance of one tree (the pinned one or an overlay). `path` is the YAML path it is configured at."""
    tag = nxversion.tag_of(version)
    found = facts(tree, tag, run=run)
    if not found.is_git:
        if source == "archive":
            sink.add(OK, "no-git", path,
                     f"{label} ist kein git-Checkout (source: archive); die Herkunft haengt an url und sha256 "
                     f"des Archivs, ein Tag ist hier nicht pruefbar")
        else:
            sink.add(ERROR if release else WARNING, "no-git", path,
                     f"{label} ist kein git-Checkout, der Pin nennt aber source: submodule; die Herkunft des "
                     f"Baums laesst sich nicht belegen. Den Baum als Submodul einbinden "
                     f"(git submodule update --init --recursive) oder source auf archive stellen")
        return found
    if found.dirty:
        sink.add(ERROR, "dirty", path,
                 f"{label} ist schmutzig ({found.describe or 'git describe nicht verfuegbar'}); gebaut wuerde "
                 f"nicht das, was der Tag {tag} sagt. Aenderungen im Baum zuruecknehmen "
                 f"(git -C {tree} status) oder sie in neXtBSW einbringen und den Pin nachziehen")
    if not found.tag_kind:
        if found.tags_at_head:
            sink.add(ERROR, "tag-mismatch", path,
                     f"{label} steht auf dem Release-Tag {', '.join(found.tags_at_head)}, der Pin verlangt aber "
                     f"{tag}; entweder {tag} auschecken oder den Pin auf die ausgecheckte Version setzen")
        else:
            # The same fact, said the way the run judges it: a release run must not call its own error harmless.
            consequence = (f"Ein Release muss aus einem Tag reproduzierbar sein - den Tag {tag} auschecken"
                           if release else
                           f"Fuer die taegliche Arbeit ist das in Ordnung; ein Release ist daraus nicht "
                           f"reproduzierbar - vor der Abgabe den Tag {tag} auschecken")
            sink.add(ERROR if release else WARNING, "no-tag", path,
                     f"{label} steht auf keinem Release-Tag ({found.describe or found.head[:12]}): "
                     f"Entwicklungsstand, kein freigegebener Baustand. {consequence} "
                     f"(git -C {tree} checkout {tag})")
        return found
    if found.tag_kind != "tag":
        sink.add(ERROR, "tag-lightweight", path,
                 f"{tag} ist in {label} ein leichter Tag (nur ein Zeiger, ohne Autor, Datum und Nachricht); ein "
                 f"freigegebener Baustand braucht einen annotierten Tag (git tag -a {tag} -m ...), weil nur der "
                 f"belegt, wer wann freigegeben hat")
    if found.tag_kind == "tag" and found.tag_safety and not found.tag_signed:
        # cm-plan.md G3, decision Q-CM2: SSH signature from the first safety release on. Only the PRESENCE of a
        # signature is judged here (git needs the allowed-signers file of the person to verify it); the
        # verification stays `git tag -v` in step 7 of cm-plan.md 5.2.
        sink.add(ERROR if release else WARNING, "tag-unsigned", path,
                 f"{tag} sagt in {label} 'safety release', ist aber nicht signiert; ein Safety-Release braucht "
                 f"einen signierten Tag (git tag -s {tag} -m ... mit gpg.format=ssh, cm-plan.md G3). Den Tag "
                 f"neu und signiert setzen; geprueft wird er danach mit git -C {tree} tag -v {tag}")
    if found.tag_commit and found.tag_commit != found.head:
        sink.add(ERROR, "tag-elsewhere", path,
                 f"{tag} zeigt in {label} auf {found.tag_commit[:12]}, ausgecheckt ist aber "
                 f"{found.head[:12]} ({found.describe or '?'}); den Tag auschecken "
                 f"(git -C {tree} checkout {tag})")
    if not found.master_ref:
        sink.add(ERROR if release else WARNING, "master-unknown", path,
                 f"In {label} ist kein master zu finden ({', '.join(MASTER_REFS)}); dass {tag} ein "
                 f"veroeffentlichter Stand ist, laesst sich damit nicht belegen. Im Baum "
                 f"git fetch origin master ausfuehren (ein Submodul wird oft ohne Zweige geholt)")
    elif not found.reachable:
        sink.add(ERROR, "tag-unreachable", path,
                 f"{tag} ist in {label} von {found.master_ref} aus nicht erreichbar; der Tag haengt an einem "
                 f"Seitenzweig und ist kein veroeffentlichter Stand. Einen Tag pinnen, der auf master liegt, "
                 f"oder den Stand nach master bringen")
    return found


def check(pin_file: Path, pin: dict | None = None, *, release: bool = False, text: str | None = None,
          run=git) -> PinStatus:
    """Release strictness of `pin_file` (gap N10). Never raises; every deviation is a finding.

    `pin` is the already parsed `nextbsw.yaml` (the callers have it); without it PyYAML reads the file. `text`
    is its raw text for the line numbers; without it the file is read. `run` replaces the git calls in tests.
    """
    file = Path(pin_file).resolve()
    if pin is None:
        pin, raw, problem = load_pin(file)
        text = text if text is not None else raw
        if problem:
            sink = _Sink(file, text)
            sink.add(ERROR, "pin-unreadable", (), problem)
            return PinStatus(file, release=release, findings=tuple(sink.out))
    if text is None:
        try:
            text = file.read_text(encoding="utf-8")
        except OSError:
            text = ""                               # no line numbers, but the pin itself was handed in
    sink = _Sink(file, text)
    section = pin.get("nextbsw") if isinstance(pin.get("nextbsw"), dict) else {}
    version = section.get("version")
    version = version if isinstance(version, str) else ""
    rel_path = section.get("path")
    rel_path = rel_path if isinstance(rel_path, str) else ""
    source = section.get("source") if section.get("source") in ("submodule", "archive") else "submodule"

    if not nxversion.is_version(version):
        sink.add(ERROR, "version-missing", ("nextbsw", "version"),
                 f"nextbsw.version fehlt oder ist keine Version MAJOR.MINOR.PATCH ({version!r}); ohne sie ist "
                 f"nicht bestimmt, welcher Release-Tag erwartet wird")
    if not rel_path:
        sink.add(ERROR, "path-missing", ("nextbsw", "path"),
                 "nextbsw.path fehlt; ohne ihn ist der gepinnte Baum nicht auffindbar")
    if not nxversion.is_version(version) or not rel_path:
        return PinStatus(file, version, release=release, findings=tuple(sink.out))

    tree = (file.parent / rel_path).resolve()
    if not nxversion.version_file(tree).is_file() or not (tree / "tools" / "nxconfig").is_dir():
        sink.add(ERROR, "tree-missing", ("nextbsw", "path"),
                 f"{tree} ist kein neXtBSW-Baum ({nxversion.VERSION_FILE} und tools/nxconfig fehlen); "
                 f"bei source: submodule mit git submodule update --init --recursive holen")
        return PinStatus(file, version, tree, release=release, findings=tuple(sink.out))
    try:
        tree_version = nxversion.read_version(tree)
    except nxversion.VersionError as exc:
        sink.add(ERROR, "tree-version", ("nextbsw", "path"), str(exc))
        tree_version = ""
    if tree_version and tree_version != version:
        sink.add(ERROR, "tree-version", ("nextbsw", "version"),
                 f"Der Pin verlangt neXtBSW {version}, der Baum {rel_path} ist {tree_version} "
                 f"({nxversion.VERSION_FILE}); Tag {nxversion.tag_of(version)} auschecken oder den Pin bewusst "
                 f"nachziehen (customer-project.md 5.5)")

    found = _check_tree(sink, tree, version, ("nextbsw", "path"), f"Der gepinnte Baum {rel_path}", source,
                        release, run=run)
    tag = nxversion.tag_of(version) if found.tag_kind == "tag" and found.tag_commit == found.head else ""
    development = found.is_git and not found.tag_kind and not found.tags_at_head

    _check_overlays(sink, pin, file, version, release, run=run)

    # Only a run that had nothing at all to say gets the green summary. A tree that is no git checkout on
    # purpose (source: archive) already said so with its own OK finding, and that one must not be overwritten
    # by a sentence about a tag nobody looked at.
    if not sink.out:
        sink.add(OK, "ok", ("nextbsw", "version"),
                 f"neXtBSW {version} steht auf dem annotierten Tag {nxversion.tag_of(version)}, erreichbar von "
                 f"{found.master_ref or 'master'}, Baum sauber ({found.describe or version})")
    return PinStatus(file, version, tree, release, development, tag, found.describe, tuple(sink.out))


def _check_overlays(sink: _Sink, pin: dict, file: Path, version: str, release: bool, run=git) -> None:
    """Overlay trees of `search.overlays` (N16 groundwork): same version, and a tree of the same provenance.

    N10 checks only what a release depends on - that an overlay is the same release as the pinned tree and is
    itself a clean, tagged baseline. Finding the modules in it and checking them against `profile` is N16.
    """
    search = pin.get("search") if isinstance(pin.get("search"), dict) else {}
    overlays = search.get("overlays")
    if not isinstance(overlays, list):
        return
    for i, entry in enumerate(overlays):
        base = ("search", "overlays", i)
        if not isinstance(entry, dict):
            sink.add(ERROR, "overlay-version", base,
                     f"search.overlays[{i}] ist keine Abbildung mit path und version")
            continue
        rel_path = entry.get("path") if isinstance(entry.get("path"), str) else ""
        over_version = entry.get("version") if isinstance(entry.get("version"), str) else ""
        label = f"Overlay {rel_path or i}"
        if over_version != version:
            sink.add(ERROR, "overlay-version", base + ("version",),
                     f"{label} nennt neXtBSW {over_version or '(keine)'}, der Pin verlangt {version}; ein "
                     f"Overlay gehoert zum selben Release wie der gepinnte Baum (customer-project.md 5.8) - "
                     f"beide auf dieselbe Version bringen")
        if not rel_path:
            sink.add(ERROR, "overlay-missing", base + ("path",), f"search.overlays[{i}].path fehlt")
            continue
        tree = (file.parent / rel_path).resolve()
        if not tree.is_dir():
            sink.add(ERROR, "overlay-missing", base + ("path",),
                     f"{label}: {tree} ist kein Ordner; das Overlay ist nicht geholt "
                     f"(git submodule update --init --recursive)")
            continue
        _check_tree(sink, tree, over_version if nxversion.is_version(over_version) else version,
                    base + ("path",), label, "submodule", release, run=run)


# ---------------------------------------------------------------------------- what the callers ask


def check_tree(tree: Path, version: str = "", *, release: bool = False, source: str = "submodule",
               run=git) -> PinStatus:
    """The same provenance rule for a bare tree, without a pin file.

    Used where there is no workspace: the release run of neXtBSW itself (`tools/ci run --release` inside the
    tree) asks exactly this question about the tree it builds. `version` defaults to the tree's own `VERSION`.
    """
    tree = Path(tree).resolve()
    sink = _Sink(None, "")
    if not version:
        try:
            version = nxversion.read_version(tree)
        except nxversion.VersionError as exc:
            sink.add(ERROR, "tree-version", (), str(exc))
            return PinStatus(tree, release=release, findings=tuple(sink.out))
    found = _check_tree(sink, tree, version, (), f"Der Baum {tree}", source, release, run=run)
    tag = nxversion.tag_of(version) if found.tag_kind == "tag" and found.tag_commit == found.head else ""
    development = found.is_git and not found.tag_kind and not found.tags_at_head
    if not sink.out:
        sink.add(OK, "ok", (),
                 f"neXtBSW {version} steht auf dem annotierten Tag {nxversion.tag_of(version)}, erreichbar von "
                 f"{found.master_ref or 'master'}, Baum sauber ({found.describe or version})")
    return PinStatus(tree, version, tree, release, development, tag, found.describe, tuple(sink.out))


def check_workspace(start: Path, *, release: bool = False, run=git) -> PinStatus | None:
    """The pin above `start` (a project.yaml or a folder), or None if there is none - then there is nothing
    to check: the tool runs from its own tree (customer-project.md 6.4)."""
    start = Path(start).resolve()
    folder = start if start.is_dir() else start.parent
    for candidate in (folder, *folder.parents):
        pin = candidate / PIN_FILE
        if pin.is_file():
            return check(pin, release=release, run=run)
        if (nxversion.version_file(candidate).is_file() and (candidate / "tools" / "nxconfig").is_dir()) \
                or (candidate / ".git").exists():
            return None
    return None
