"""Which entries of the test list a change touches (TOOL-79): `tools/test --since <ref>`.

Finding of the coordinator 2026-09-23: twice in one day a test group landed red on dev because after his own rework
he checked the wrong selection - `safety-tools` after ARCH-57 (NX_ROOT in the root CMakeLists.txt) and the Studio
tests after RTE-22 (paths relative to rte.yaml). No agent report named the affected group, and in both cases a
mapping file -> entry would have named it.

The mapping is data of the list, not a guess:

    covers, args, argv, sources,     what the entry runs or compiles
    include                           - a changed file equal to one of them or lying
                                     below one of them (a directory) triggers the entry
    watch                            optional field of an entry: further files, directories or glob patterns the
                                     entry depends on without running them (the root CMakeLists.txt, test profiles
                                     a Python test reads, a document it checks)
    list                             derived: an entry whose own lines in tests.yaml changed (new, other args ...)
    import                           derived: the repository modules the entry's Python files import, transitively
                                     (plus the conftest.py files pytest loads). Found by parsing, never by running.

An entry that has no mapping at all (a cmake_preset build, whose sources are the whole tree) is always selected -
nothing proves that it is unaffected. A changed file that triggers no entry is the actual finding: code without a
test. It is reported at the end, code and configuration apart from documentation.

This module only reads: git and the files of the repository. It knows nothing of Qt and nothing of the runner.
"""

from __future__ import annotations

import ast
import fnmatch
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

DEFAULT_REF = "origin/dev"
PLACEHOLDER_ROOTS = ("{repo}/", "{tree}/", "{workspace}/")
NO_PATH = ("{tmp}", "{build}", "{python}")
MAPPED_FIELDS = ("covers", "watch", "args", "argv", "sources", "include")
# covers and watch are paths by definition; the others also carry options, sub-commands and code (-c "..."), so a
# value of theirs counts only if it names an existing file or directory of the repository
PATH_FIELDS = ("covers", "watch")
# Files that are documentation, not code or configuration: without an entry they are listed apart, because
# "code without a test" is the finding the coordinator asked for and prose would drown it
DOC_SUFFIXES = (".md", ".rst", ".pdf", ".png", ".svg", ".jpg", ".jpeg", ".gif", ".html", ".drawio")
# Where a Python file of the repository finds its imports besides its own folder: the repository root
# (`import tools.x`) and tools/ (the tests put it on sys.path and write `import nxconfig`, `import rtegen`)
IMPORT_ROOTS = ("", "tools")


class SinceError(Exception):
    """git could not name the changed files (unknown reference, no repository)."""


@dataclass
class Claim:
    """One path an entry claims: `pattern` is a file, a directory or a glob; `field` where it comes from."""
    field: str
    pattern: str
    via: str = ""          # for field "import": the file of the entry that pulls the module in

    def matches(self, path: str) -> bool:
        if any(c in self.pattern for c in "*?["):
            return fnmatch.fnmatchcase(path, self.pattern)
        return path == self.pattern or path.startswith(self.pattern.rstrip("/") + "/")

    def describe(self) -> str:
        if self.field == "list":
            return f"{self.via} in {self.pattern}"
        return f"{self.field} ueber {self.via}" if self.via else f"{self.field}: {self.pattern}"


@dataclass
class Selection:
    """Result of a mapping: which entry a changed file triggered, and what stayed without an entry."""
    ref: str
    base: str
    changed: list[str]
    triggers: dict[str, list[tuple[str, Claim]]] = field(default_factory=dict)   # entry -> [(file, claim)]
    unmapped_entries: list[str] = field(default_factory=list)                     # no claim at all: always run
    orphans: list[str] = field(default_factory=list)                              # changed, no entry

    @property
    def names(self) -> set[str]:
        """Entries to run: the triggered ones, and - if anything changed at all - every entry without a mapping."""
        return set(self.triggers) | (set(self.unmapped_entries) if self.changed else set())

    def orphans_code(self) -> list[str]:
        return [p for p in self.orphans if not p.lower().endswith(DOC_SUFFIXES)]

    def orphans_doc(self) -> list[str]:
        return [p for p in self.orphans if p.lower().endswith(DOC_SUFFIXES)]


# ---------------------------------------------------------------------------- git


def _git(repo: Path, *argv: str) -> str:
    try:
        done = subprocess.run(["git", *argv], cwd=repo, capture_output=True, text=True, encoding="utf-8",
                              errors="replace")
    except OSError as exc:
        raise SinceError(f"git nicht aufrufbar: {exc}") from exc
    if done.returncode:
        raise SinceError(f"git {' '.join(argv)}: {done.stderr.strip() or done.returncode}")
    return done.stdout


def changed_files(repo: Path, ref: str = DEFAULT_REF) -> tuple[str, list[str]]:
    """(base commit, changed files) of the working tree against the merge base of `ref` and HEAD.

    The merge base, not `ref` itself: what dev gained in the meantime is not this branch's change. Compared with the
    working tree, so committed, staged and unstaged changes count alike, and untracked files too - the coordinator
    checks exactly after his own uncommitted rework. Renames count as deletion plus addition: a test that named the
    old path is affected as much as one that names the new one.

    `A..B` compares two commits instead (no working tree, no untracked files) - to replay what a past merge
    touched: `--since <merge>^1..<merge>`."""
    if ".." in ref and "..." not in ref:
        old, new = ref.split("..", 1)
        for side in (old, new or "HEAD"):
            _git(repo, "rev-parse", "--verify", "--quiet", f"{side}^{{commit}}")
        listed = _git(repo, "diff", "--name-only", "--no-renames", old, new or "HEAD").splitlines()
        return _git(repo, "rev-parse", old).strip(), sorted({p.strip() for p in listed if p.strip()})
    _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    try:
        base = _git(repo, "merge-base", ref, "HEAD").strip()
    except SinceError:
        base = _git(repo, "rev-parse", ref).strip()      # unrelated histories: compare with the reference itself
    listed = _git(repo, "diff", "--name-only", "--no-renames", base).splitlines()
    listed += _git(repo, "ls-files", "--others", "--exclude-standard").splitlines()
    return base, sorted({p.strip() for p in listed if p.strip()})


def edited_entries(repo: Path, ref: str, base: str, list_file: Path) -> set[str] | None:
    """Names of the entries whose definition in the test list differs from the base (new or changed) - an edited
    entry is affected by its own change. None if the list did not exist at the base or is not in the repository."""
    import yaml
    try:
        rel = list_file.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return None
    try:
        old = yaml.safe_load(_git(repo, "show", f"{base}:{rel}")) or {}
        if ".." in ref and "..." not in ref:
            new = yaml.safe_load(_git(repo, "show", f"{ref.split('..', 1)[1] or 'HEAD'}:{rel}")) or {}
        else:
            new = yaml.safe_load(list_file.read_text(encoding="utf-8")) or {}
    except (SinceError, OSError, yaml.YAMLError):
        return None
    before = {e.get("name"): e for e in old.get("tests") or [] if isinstance(e, dict)}
    changed = {e.get("name") for e in new.get("tests") or [] if isinstance(e, dict) and before.get(e.get("name")) != e}
    if old.get("defaults") != new.get("defaults"):      # a changed default changes every entry of that kind
        kinds = {k for k in {*(old.get("defaults") or {}), *(new.get("defaults") or {})}
                 if (old.get("defaults") or {}).get(k) != (new.get("defaults") or {}).get(k)}
        changed |= {e.get("name") for e in new.get("tests") or [] if isinstance(e, dict) and e.get("kind") in kinds}
    return {n for n in changed if n}


# ---------------------------------------------------------------------------- claims of an entry


def _as_path(argument) -> str:
    text = str(argument).replace("\\", "/").split("::", 1)[0]
    for root in PLACEHOLDER_ROOTS:
        if text.startswith(root):
            text = text[len(root):]
    text = text[2:] if text.startswith("./") else text
    return text.rstrip("/")


def _usable(text: str) -> bool:
    """A value that names a place in the repository: not an option, not a scratch path, not the root itself."""
    return bool(text) and not text.startswith("-") and text not in (".", "{repo}", "{tree}", "{workspace}") \
        and not any(p in text for p in NO_PATH) and not text.startswith("/") and ":" not in text[:3]


def _module_path(repo: Path, module: str) -> str | None:
    """`tools.ci` -> tools/ci (for an argument after -m)."""
    rel = module.replace(".", "/")
    if (repo / rel).is_dir() or (repo / f"{rel}.py").is_file():
        return rel if (repo / rel).is_dir() else f"{rel}.py"
    return None


def direct_claims(test, repo: Path) -> list[Claim]:
    """The paths an entry names itself: covers, args, sources, include and watch (and the module after -m)."""
    claims: list[Claim] = []
    for name in MAPPED_FIELDS:
        values = getattr(test, name, None) if name in ("covers", "watch") else test.spec.get(name)
        if isinstance(values, (str, Path)):
            values = [values]
        after_m = False
        for value in values or []:
            text = str(value)
            if after_m:
                after_m = False
                module = _module_path(repo, text)
                if module:
                    claims.append(Claim(name, module))
                continue
            if text == "-m":
                after_m = True
                continue
            path = _as_path(text)
            if _usable(path) and (name in PATH_FIELDS or (repo / path).exists()):
                claims.append(Claim(name, path))
    return claims


class ImportGraph:
    """Repository modules a Python file imports, transitively. Parsed once per file and cached."""

    def __init__(self, repo: Path):
        self.repo = repo
        self._direct: dict[str, list[str]] = {}
        self._closure: dict[str, set[str]] = {}

    def _resolve(self, name: str, folder: PurePosixPath) -> list[str]:
        rel = name.replace(".", "/")
        found = []
        for root in [str(folder), *IMPORT_ROOTS]:
            base = PurePosixPath(root) / rel if root else PurePosixPath(rel)
            for candidate in (f"{base}.py", f"{base}/__init__.py"):
                if (self.repo / candidate).is_file():
                    found.append(candidate)
            if found:
                return found
        return found

    def direct(self, path: str) -> list[str]:
        if path in self._direct:
            return self._direct[path]
        result: list[str] = []
        self._direct[path] = result
        try:
            tree = ast.parse((self.repo / path).read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError, ValueError):
            return result
        folder = PurePosixPath(path).parent
        for node in ast.walk(tree):
            names: list[tuple[str, PurePosixPath]] = []
            if isinstance(node, ast.Import):
                names = [(alias.name, folder) for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                start = folder
                for _ in range(max(0, node.level - 1)):
                    start = start.parent
                if node.level and not node.module:           # from . import x
                    names = [(alias.name, start) for alias in node.names]
                else:
                    module = node.module or ""
                    names = [(module, start)] + [(f"{module}.{alias.name}", start) for alias in node.names]
                if node.level:                               # relative: only below the package, never a root
                    for name, where in names:
                        rel = PurePosixPath(where) / name.replace(".", "/")
                        for candidate in (f"{rel}.py", f"{rel}/__init__.py"):
                            if (self.repo / candidate).is_file():
                                result.append(candidate)
                    continue
            for name, where in names:
                # a package import also loads the __init__.py of every parent package
                parts = name.split(".")
                for depth in range(1, len(parts) + 1):
                    result.extend(self._resolve(".".join(parts[:depth]), where))
        self._direct[path] = sorted(set(result) - {path})
        return self._direct[path]

    def closure(self, path: str) -> set[str]:
        if path in self._closure:
            return self._closure[path]
        seen: set[str] = set()
        todo = [path]
        while todo:
            for module in self.direct(todo.pop()):
                if module not in seen and module != path:
                    seen.add(module)
                    todo.append(module)
        self._closure[path] = seen
        return seen


def _conftests(repo: Path, path: str) -> list[str]:
    """conftest.py files pytest loads for a test file: every one from the file's folder up to the root."""
    found = []
    folder = PurePosixPath(path).parent
    while True:
        candidate = folder / "conftest.py" if str(folder) != "." else PurePosixPath("conftest.py")
        if (repo / candidate).is_file():
            found.append(str(candidate))
        if str(folder) in (".", ""):
            return found
        folder = folder.parent


def claims_of(test, repo: Path, graph: ImportGraph) -> list[Claim]:
    """Everything an entry claims: its own paths plus the modules its Python files import."""
    claims = direct_claims(test, repo)
    python_files = sorted({c.pattern for c in claims if c.pattern.endswith(".py") and (repo / c.pattern).is_file()})
    if test.kind == "pytest":
        extra = {c for p in python_files for c in _conftests(repo, p)}
        claims += [Claim("import", c, via="pytest") for c in sorted(extra - set(python_files))]
        python_files = sorted(set(python_files) | extra)
    named = {c.pattern for c in claims}
    for source in python_files:
        for module in sorted(graph.closure(source)):
            if module not in named:
                named.add(module)
                claims.append(Claim("import", module, via=source))
    return claims


# ---------------------------------------------------------------------------- mapping


def map_changes(tests, changed: list[str], repo: Path, ref: str = DEFAULT_REF, base: str = "",
                edited: set[str] | None = None, list_path: str = "tools/test/tests.yaml") -> Selection:
    """Which entry each changed file triggers. `tests` is the whole list - the files without an entry are judged
    against every entry, not against the ones a --group or -k happens to leave. `edited` names the entries whose
    own definition in the list (`list_path`) changed: the list file triggers exactly those."""
    graph = ImportGraph(repo)
    selection = Selection(ref, base, list(changed))
    hit: set[str] = set()
    for test in tests:
        if edited and test.name in edited and list_path in changed:
            selection.triggers.setdefault(test.name, []).append((list_path, Claim("list", list_path,
                                                                                  via="eigener Eintrag geaendert")))
            hit.add(list_path)
        claims = claims_of(test, repo, graph)
        if not claims:
            selection.unmapped_entries.append(test.name)
            continue
        for path in changed:
            claim = next((c for c in claims if c.field != "import" and c.matches(path)), None) \
                or next((c for c in claims if c.matches(path)), None)
            if claim and path not in [p for p, _ in selection.triggers.get(test.name, [])]:
                selection.triggers.setdefault(test.name, []).append((path, claim))
                hit.add(path)
    selection.orphans = [p for p in changed if p not in hit]
    return selection


def report_lines(selection: Selection, chosen: list[str], left_out: list[str]) -> list[str]:
    """The text of --since: per chosen entry the file that triggered it, then what the filters left out, then the
    changed files without an entry."""
    lines = [f"--since {selection.ref} (Basis {selection.base[:10] or '-'}): {len(selection.changed)} geaenderte "
             f"Datei(en), {len(selection.triggers)} Eintrag/Eintraege ausgeloest, "
             f"{len(selection.unmapped_entries)} ohne Zuordnung"]
    width = max((len(n) for n in chosen), default=4)
    for name in chosen:
        if name in selection.triggers:
            hits = selection.triggers[name]
            path, claim = hits[0]
            more = f" (+{len(hits) - 1} weitere)" if len(hits) > 1 else ""
            lines.append(f"  {name:<{width}}  {path}  [{claim.describe()}]{more}")
        elif name in selection.unmapped_entries:
            lines.append(f"  {name:<{width}}  keine Zuordnung - sicherheitshalber")
        else:
            lines.append(f"  {name:<{width}}  von einem ausgewaehlten Eintrag gebraucht (needs)")
    if left_out:
        lines.append(f"Ausgeloest, aber durch --group/-k/--slow/--skip nicht ausgewaehlt ({len(left_out)}): "
                     + ", ".join(left_out))
    code, doc = selection.orphans_code(), selection.orphans_doc()
    lines.append(f"Geaenderte Dateien ohne Testeintrag - Code ohne Test ({len(code)}):")
    lines += [f"  {p}" for p in code] or ["  (keine)"]
    if doc:
        lines.append(f"Dokumentation ohne Testeintrag ({len(doc)}): " + ", ".join(doc))
    return lines


def as_json(selection: Selection) -> dict:
    return {
        "ref": selection.ref,
        "base": selection.base,
        "changed": selection.changed,
        "triggered": {name: [{"file": path, "field": claim.field, "pattern": claim.pattern, "via": claim.via}
                             for path, claim in hits] for name, hits in sorted(selection.triggers.items())},
        "unmapped_entries": selection.unmapped_entries,
        "files_without_entry": selection.orphans_code(),
        "docs_without_entry": selection.orphans_doc(),
    }
