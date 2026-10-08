"""SYS-94: the citation rule of the configuration management plan doc/safety/cm-plan.md (gap G5, sections 6.5 and 4.3).

Checks
- state line: every document directly under doc/safety (*.md, *.yaml) except the generated ones, and every StrictDoc
  document of the safety requirements and the safety catalogue (doc/requirements/safety/*.sdoc, DOC-21 step 3),
  carries exactly one line `Baseline: vX.Y.Z` or `Working state: dev at <commit> (after vX.Y.Z)`, in its header
  (before the first `## ` heading or `---` rule; in YAML the leading comment block, with `# ` in front; in a .sdoc
  file the text before the first section or requirement node); nothing else on the line;
- generated documents: the exempt files are exactly the rows of the document register (anomaly-register.md) with
  status `generated`, and they carry no line;
- releases: a `Baseline` tag is a release tag of the clone and not newer than VERSION, or it is `v<VERSION>` before
  that tag exists (the release candidate of step 1 of 5.2: the tag name is known before the tag, REL-020); a
  `Working state` commit resolves, is an ancestor of HEAD, and the named release is the newest release the commit
  contains;
- release candidates (TOOL-232): a release tag is `vX.Y.Z` or `vX.Y.Z-rcN` (N = 1, 2, ... without leading zero;
  Release R1 = `v1.0.0-rc1` on VERSION 1.0.0). A candidate counts as a release everywhere above; it sorts before the
  release of its version. Before it exists, `Baseline: v<VERSION>-rcN` is accepted like `v<VERSION>`; a candidate of
  another version is not;
- release checkout: when HEAD carries the tag v<VERSION>, every line is `Baseline: v<VERSION>` (P4, step 7 of 5.2);
  a candidate tag on HEAD does not trigger this rule (cm-plan.md 6.5 item 4 names the tag `v` + VERSION);
- baselines: the table of cm-plan.md 4.3 lists exactly the release tags of `git for-each-ref refs/tags/v*`, each with
  its full commit; only a tag of VERSION (`v<VERSION>` or `v<VERSION>-rcN`) may still lack its row, because its
  commit (the merge on master) is made after the tree it tags and is recorded after the tag (step 9 of 5.2,
  REL-020, TOOL-232).
The git checks are skipped with a message in a shallow clone or a clone without release tags; the text checks always
run. Counter-checks with deliberately broken copies show that each rule sees its fault.
In the exported tree (DOC-85) a generated document of the register may stay in the development repository (decision
K1: the traceability matrix); it is accepted as absent only if the export lists it as left out on purpose
(doc/unpublished.yaml, tools/distcheck/unpublished.py). The development repository has no list.

Run: .venv/Scripts/python -m unittest doc/safety/test/test_cm_plan.py
"""

import importlib.util
import pathlib
import re
import subprocess
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[3]


def load_unpublished():
    """DOC-85: the export's list of what it left out on purpose (empty in the development repository)."""
    path = REPO / "tools" / "distcheck" / "unpublished.py"
    spec = importlib.util.spec_from_file_location("distcheck_unpublished", path)
    module = sys.modules.setdefault("distcheck_unpublished", importlib.util.module_from_spec(spec))
    if not hasattr(module, "load"):
        spec.loader.exec_module(module)
    return module.load(REPO)


def unpublished_documents(unpublished) -> set:
    """Names (as documents() names them) of the documents directly under doc/safety the export left out."""
    return {pathlib.PurePosixPath(rel).name for rel in unpublished.files
            if pathlib.PurePosixPath(rel).parent.as_posix() == "doc/safety" and rel.endswith((".md", ".yaml"))}
SAFETY = REPO / "doc" / "safety"
SDOC_SAFETY = REPO / "doc" / "requirements" / "safety"     # SSR, ASG, AoU, SM as StrictDoc documents (DOC-21)
PLAN = SAFETY / "cm-plan.md"
REGISTER = SAFETY / "anomaly-register.md"

# a release tag vX.Y.Z or a release candidate vX.Y.Z-rcN (TOOL-232); groups are non-capturing so that the patterns
# built from it keep their own group numbers
TAG = r"v\d+\.\d+\.\d+(?:-rc[1-9]\d*)?"
_TAG_PARTS = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:-rc([1-9]\d*))?$")
STATE_START = re.compile(r"^(?:# )?(Baseline|Working state)\b")
BASELINE = re.compile(rf"^(?:# )?Baseline: ({TAG})$")
WORKING = re.compile(rf"^(?:# )?Working state: dev at ([0-9a-f]{{7,40}}) \(after ({TAG})\)$")


def version_key(tag: str) -> tuple:
    """Order of release tags: by version; a candidate before the release of its version, rc1 before rc2."""
    major, minor, patch, rc = _TAG_PARTS.match(tag).groups()
    return (int(major), int(minor), int(patch)) + ((0, int(rc)) if rc else (1, 0))


def of_version(tag: str, version: str) -> bool:
    """`tag` is the release `v<version>` or one of its candidates `v<version>-rcN` (TOOL-232)."""
    return re.fullmatch(TAG, tag) is not None and (tag == f"v{version}" or tag.startswith(f"v{version}-rc"))


def documents() -> dict:
    """File name -> text of every document directly under doc/safety, and of the .sdoc files of the safety
    requirements and the safety catalogue (named by their file name, like the document register does)."""
    docs = {p.name: p.read_text(encoding="utf-8") for p in sorted(SAFETY.iterdir())
            if p.is_file() and p.suffix in (".md", ".yaml")}
    docs.update({p.name: p.read_text(encoding="utf-8") for p in sorted(SDOC_SAFETY.glob("*.sdoc"))})
    return docs


def header_length(name: str, text: str) -> int:
    """Number of header lines: before the first `## ` heading or `---` rule; for YAML the leading comment block."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if name.endswith(".sdoc"):
            if line.startswith("[[SECTION]]") or re.match(r"^\[(?!DOCUMENT\]|GRAMMAR\]|TEXT\])[A-Z_]+\]$", line):
                return i
        elif name.endswith(".yaml"):
            if not line.startswith("#"):
                return i
        elif i > 0 and (line.startswith("## ") or line.strip() == "---"):
            return i
    return len(lines)


def generated_documents(register: str) -> set:
    """Documents of the register table with status `generated`."""
    body = register.split("<!-- documents:start -->", 1)[1].split("<!-- documents:end -->", 1)[0]
    rows = [[c.strip() for c in line.strip().strip("|").split("|")] for line in body.splitlines()
            if line.startswith("| `")]
    return {row[0].strip("`") for row in rows if len(row) > 5 and row[5] == "generated"}


def baseline_rows(plan: str) -> dict:
    """Tag -> full commit from the table of cm-plan.md 4.3."""
    try:
        body = plan.split("<!-- baselines:start -->", 1)[1].split("<!-- baselines:end -->", 1)[0]
    except IndexError:
        raise ValueError("cm-plan.md: markers baselines:start/end missing") from None
    rows = {}
    for line in body.splitlines():
        m = re.match(rf"^\| `({TAG})` \| `([0-9a-f]{{40}})`", line)
        if m:
            rows[m.group(1)] = m.group(2)
        elif line.startswith("| `v"):
            raise ValueError(f"cm-plan.md 4.3: row without tag and full commit: {line[:60]}")
    return rows


class Git:
    """Read-only view of the clone; `usable` is False in a shallow clone or without git."""

    def __init__(self, repo: pathlib.Path = REPO):
        self.repo = repo
        shallow = self.run("rev-parse", "--is-shallow-repository")
        self.usable = shallow is not None and shallow == "false"
        self.releases = {}   # tag -> commit
        if self.usable:
            out = self.run("for-each-ref", "refs/tags/v*", "--format=%(refname:short) %(*objectname) %(objectname)")
            for line in (out or "").splitlines():
                name, peeled, obj = (line.split(" ") + ["", ""])[:3]
                if re.fullmatch(TAG, name):
                    self.releases[name] = peeled or obj

    def run(self, *args):
        try:
            res = subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            return None
        return res.stdout.strip() if res.returncode == 0 else None

    def resolve(self, rev: str):
        return self.run("rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")

    def is_ancestor(self, a: str, b: str) -> bool:
        try:
            return subprocess.run(["git", "-C", str(self.repo), "merge-base", "--is-ancestor", a, b],
                                  capture_output=True, timeout=60).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def contains_release(self, commit: str, tag: str) -> bool:
        """The commit contains the release: the tagged commit, or for a merge on master its dev side, is an ancestor."""
        tagged = self.releases[tag]
        parents = (self.run("rev-list", "--parents", "-n1", tagged) or "").split()[1:]
        sides = [tagged] + (parents[1:] if len(parents) > 1 else [])
        return any(self.is_ancestor(s, commit) for s in sides)

    def head_tags(self) -> set:
        head = self.resolve("HEAD")
        return {t for t, c in self.releases.items() if c == head}


def state_lines(name: str, text: str) -> list:
    """(line number, line) of every line that starts like a state line."""
    return [(i, line) for i, line in enumerate(text.splitlines()) if STATE_START.match(line)]


def line_problems(docs: dict, generated: set) -> list:
    problems = []
    for name, text in docs.items():
        found = state_lines(name, text)
        if name in generated:
            problems += [f"{name}: generated document carries a state line (line {i + 1})" for i, _ in found]
            continue
        if len(found) != 1:
            problems.append(f"{name}: {len(found)} state lines, expected exactly one")
            continue
        i, line = found[0]
        if i >= header_length(name, text):
            problems.append(f"{name}: state line {i + 1} is not in the header")
        if not (BASELINE.match(line) or WORKING.match(line)):
            problems.append(f"{name}: state line '{line[:70]}' is neither 'Baseline: vX.Y.Z' nor "
                            "'Working state: dev at <commit> (after vX.Y.Z)'")
    return problems


def generated_problems(docs: dict, generated: set, unpublished: set = frozenset()) -> list:
    return [f"register lists generated document {g}, which does not exist" for g in sorted(generated)
            if g not in docs and g not in unpublished]


def release_problems(docs: dict, generated: set, git: Git, version: str) -> list:
    problems = []
    head_tags = git.head_tags()
    release_checkout = f"v{version}" in head_tags
    for name, text in docs.items():
        if name in generated:
            continue
        for _, line in state_lines(name, text):
            b, w = BASELINE.match(line), WORKING.match(line)
            if release_checkout and not (b and b.group(1) == f"v{version}"):
                problems.append(f"{name}: HEAD is release v{version}, the line must be 'Baseline: v{version}'")
            if b:
                tag = b.group(1)
                if tag not in git.releases and not of_version(tag, version):
                    problems.append(f"{name}: baseline {tag} is no release tag of this clone and not the release "
                                    f"candidate v{version} (or v{version}-rcN)")
                elif version_key(tag) > version_key(f"v{version}"):
                    problems.append(f"{name}: baseline {tag} is newer than VERSION {version}")
            elif w:
                commit, tag = w.group(1), w.group(2)
                full = git.resolve(commit)
                if full is None:
                    problems.append(f"{name}: working state commit {commit} does not resolve")
                    continue
                if not git.is_ancestor(full, "HEAD"):
                    problems.append(f"{name}: working state commit {commit} is not an ancestor of HEAD")
                if tag not in git.releases:
                    problems.append(f"{name}: release {tag} is no release tag of this clone")
                    continue
                contained = [t for t in git.releases if git.contains_release(full, t)]
                newest = max(contained, key=version_key) if contained else None
                if newest != tag:
                    problems.append(f"{name}: commit {commit} is after {newest or 'no release'}, the line says {tag}")
    return problems


def baseline_table_problems(plan: str, git: Git, version: str = None) -> list:
    """`version`: VERSION of the tree; a tag of it (v<version> or v<version>-rcN, TOOL-232) may lack its row until it
    is recorded after the tag (step 9 of 5.2)."""
    rows = baseline_rows(plan)
    problems = [f"cm-plan.md 4.3 lists {t}, which is no release tag of this clone" for t in rows
                if t not in git.releases]
    problems += [f"release tag {t} has no row in cm-plan.md 4.3" for t in git.releases
                 if t not in rows and not (version and of_version(t, version))]
    problems += [f"cm-plan.md 4.3: {t} is {git.releases[t]}, the table says {c}" for t, c in rows.items()
                 if t in git.releases and git.releases[t] != c]
    return problems


class CmPlanConsistency(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.docs = documents()
        cls.plan = PLAN.read_text(encoding="utf-8")
        cls.generated = generated_documents(REGISTER.read_text(encoding="utf-8"))
        cls.version = (REPO / "VERSION").read_text(encoding="utf-8").strip()
        cls.git = Git()
        cls.unpublished = unpublished_documents(load_unpublished())

    def need_git(self):
        if not self.git.usable:
            self.skipTest("shallow clone or no git: release checks skipped")
        if not self.git.releases:
            self.skipTest("no release tags in this clone (git fetch --tags): release checks skipped")

    def with_doc(self, name: str, text: str) -> dict:
        return dict(self.docs, **{name: text})

    # ---- the repository as it is
    def test_state_lines(self):
        self.assertTrue(self.generated, "the document register lists no generated document")
        self.assertEqual([], line_problems(self.docs, self.generated)
                         + generated_problems(self.docs, self.generated, self.unpublished))
        if not (REPO / "doc" / "unpublished.yaml").exists():
            self.assertEqual(self.unpublished, set())     # DOC-85: only an exported tree lists unpublished files

    def test_releases(self):
        self.need_git()
        self.assertEqual([], release_problems(self.docs, self.generated, self.git, self.version))

    def test_baseline_table(self):
        self.need_git()
        self.assertEqual([], baseline_table_problems(self.plan, self.git, self.version))

    # ---- counter-checks: each rule sees its fault
    def test_counter_checks_text(self):
        name = "fmea.md"
        text = self.docs[name]
        line = next(ln for _, ln in state_lines(name, text))
        # missing, doubled, moved out of the header, malformed
        self.assertTrue(line_problems(self.with_doc(name, text.replace(line + "\n", "")), self.generated))
        self.assertTrue(line_problems(self.with_doc(name, text.replace(line, line + "\n" + line)), self.generated))
        self.assertTrue(line_problems(self.with_doc(name, text.replace(line + "\n", "") + "\n" + line + "\n"),
                                      self.generated))
        self.assertTrue(line_problems(self.with_doc(name, text.replace(line, line + " checked")), self.generated))
        self.assertTrue(line_problems(self.with_doc(name, text.replace(line, "Working state: `dev` at `6f868b5`")),
                                      self.generated))
        self.assertTrue(line_problems(self.with_doc(name, text.replace(line, "Baseline: 0.1.0")), self.generated))
        # TOOL-232: a release candidate vX.Y.Z-rcN is a baseline, in both forms of the line; no other suffix is
        for good in ("Baseline: v1.0.0-rc1", "Baseline: v1.0.0-rc12", "Working state: dev at 6f868b5 (after v1.0.0-rc1)"):
            self.assertEqual(line_problems(self.with_doc(name, text.replace(line, good)), self.generated), [], good)
        for wrong in ("Baseline: v1.0.0-rc", "Baseline: v1.0.0-beta1", "Baseline: v1.0.0-rc0", "Baseline: v1.0.0-rc01",
                      "Baseline: v1.0.0-rc.1", "Baseline: v1.0.0-RC1", "Working state: dev at 6f868b5 (after v1.0.0-rc)"):
            self.assertTrue(line_problems(self.with_doc(name, text.replace(line, wrong)), self.generated), wrong)
        # a new document without the line
        self.assertTrue(line_problems(self.with_doc("new-plan.md", "# New plan\n\nStatus: draft.\n"), self.generated))
        # the YAML header comment (no YAML document is left under doc/safety since DOC-21 step 3: a synthetic one)
        yml = f"# A safety document in YAML\n#\n# {line}\n\nkey: 1\n"
        self.assertEqual(line_problems(self.with_doc("x.yaml", yml), self.generated), [])
        self.assertTrue(line_problems(self.with_doc("x.yaml", yml.replace(f"# {line}\n", "")), self.generated))
        self.assertTrue(line_problems(self.with_doc("x.yaml", yml.replace(f"# {line}", f"#   {line}")),
                                      self.generated))
        # the StrictDoc document of the SSR: missing, and moved out of the header into a requirement node
        sdoc = self.docs["ssr.sdoc"]
        sline = next(ln for _, ln in state_lines("ssr.sdoc", sdoc))
        self.assertTrue(line_problems(self.with_doc("ssr.sdoc", sdoc.replace(sline + "\n", "")), self.generated))
        # (DOC-43: the first multi-line statement after the header is that of a requirement or clause node)
        stripped = sdoc.replace(sline + "\n", "")
        node_at = stripped.index("STATEMENT: >>>\n", stripped.index("\n[SSR]\n")) + len("STATEMENT: >>>\n")
        moved = stripped[:node_at] + sline + "\n" + stripped[node_at:]
        self.assertNotEqual(moved, sdoc.replace(sline + "\n", ""))
        self.assertTrue(line_problems(self.with_doc("ssr.sdoc", moved), self.generated))
        # generated documents: carrying a line is an error, and the register must name existing files
        # (DOC-85: in the export the generated document may be left out - then a synthetic one stands in)
        present = sorted(g for g in self.generated if g in self.docs)
        if present:
            gen, docs, generated = present[0], self.docs, self.generated
        else:
            gen = "generated-example.md"
            docs, generated = self.with_doc(gen, "# Generated example\n\nNo state line.\n"), self.generated | {gen}
        self.assertTrue(line_problems(dict(docs, **{gen: line + "\n" + docs[gen]}), generated))
        self.assertTrue(line_problems(docs, set()))
        self.assertTrue(generated_problems(self.docs, self.generated | {"gone.md"}, self.unpublished))
        # DOC-85: a generated document is accepted as absent only when the export lists it as left out
        self.assertEqual(generated_problems({}, {"gone.md"}, {"gone.md"}), [])
        self.assertTrue(generated_problems({}, {"gone.md"}, {"other.md"}))
        listed = type(load_unpublished())(files=frozenset({"doc/safety/traceability.md", "doc/safety/reviews/x.md",
                                                            "doc/requirements/migration-plan.md"}))
        self.assertEqual(unpublished_documents(listed), {"traceability.md"})
        # the register's generated rows are what exempts a file
        reg = REGISTER.read_text(encoding="utf-8")
        reg_gen = sorted(self.generated)[0]
        self.assertNotIn(reg_gen, generated_documents(reg.replace(f"| `{reg_gen}` |", f"| `{reg_gen}.old` |")))
        # the baseline table needs its markers and full hashes
        with self.assertRaises(ValueError):
            baseline_rows(self.plan.replace("<!-- baselines:start -->", ""))
        rows = baseline_rows(self.plan)
        if rows:
            tag, commit = next(iter(rows.items()))
            with self.assertRaises(ValueError):
                baseline_rows(self.plan.replace(f"| `{tag}` | `{commit}`", f"| `{tag}` | `{commit[:7]}`"))
        # TOOL-232: a row of a release candidate is read (the row of Release R1 is added after its tag); a row with a
        # malformed candidate is refused like any other broken row
        row = f"| `v1.0.0-rc1` | `{'a' * 40}` | x | x | development release |\n<!-- baselines:end -->"
        self.assertEqual(baseline_rows(self.plan.replace("<!-- baselines:end -->", row))["v1.0.0-rc1"], "a" * 40)
        for wrong in ("v1.0.0-rc", "v1.0.0-beta1"):
            with self.assertRaises(ValueError):
                baseline_rows(self.plan.replace("<!-- baselines:end -->", row.replace("v1.0.0-rc1", wrong)))
        # the order of release tags: a candidate before its release, after the previous release
        self.assertLess(version_key("v0.2.0"), version_key("v1.0.0-rc1"))
        self.assertLess(version_key("v1.0.0-rc1"), version_key("v1.0.0-rc2"))
        self.assertLess(version_key("v1.0.0-rc2"), version_key("v1.0.0"))
        self.assertTrue(of_version("v1.0.0-rc1", "1.0.0") and of_version("v1.0.0", "1.0.0"))
        for wrong in ("v1.0.1-rc1", "v1.0.0-rc", "v1.0.0-beta1", "v1.0.00-rc1", "v1.0.0-rc0"):
            self.assertFalse(of_version(wrong, "1.0.0"), wrong)

    def test_counter_checks_git(self):
        self.need_git()
        name = "fmea.md"
        text = self.docs[name]
        line = next(ln for _, ln in state_lines(name, text))
        newest = max(self.git.releases, key=version_key)
        head = self.git.resolve("HEAD")
        release_checkout = f"v{self.version}" in self.git.head_tags()

        def problems(new_line: str, version: str = None, git: Git = None) -> list:
            return release_problems(self.with_doc(name, text.replace(line, new_line)), self.generated,
                                    git or self.git, version or self.version)

        self.assertEqual([], problems(line))
        # between releases fmea.md carries a working state line; on a release candidate or a release checkout every
        # line is a baseline (REL-020), so the working state checks start from a valid line made for HEAD
        working = line
        if WORKING.match(line) is None:
            contained = [t for t in self.git.releases if self.git.contains_release(head, t)]
            self.assertTrue(contained, "HEAD contains no release")
            working = f"Working state: dev at {head[:8]} (after {max(contained, key=version_key)})"
            if not release_checkout:
                self.assertEqual([], problems(working))
        w = WORKING.match(working)
        self.assertTrue(problems(f"Working state: dev at deadbee (after {newest})"))              # does not resolve
        self.assertTrue(problems(f"Working state: dev at {w.group(1)} (after v9.9.9)"))           # no such release
        # a commit before the newest release, but the line claims "after" it
        tagged = self.git.releases[newest]
        parents = (self.git.run("rev-list", "--parents", "-n1", tagged) or "").split()[1:]
        before = self.git.resolve(f"{parents[-1] if parents else tagged}~1")
        if before:
            self.assertTrue(problems(f"Working state: dev at {before[:7]} (after {newest})"))
        # a commit that is not an ancestor of HEAD: the release commit on master (not merged back into dev)
        if not self.git.is_ancestor(tagged, "HEAD"):
            self.assertTrue(problems(f"Working state: dev at {tagged[:7]} (after {newest})"))
        # baselines: unknown tag, tag newer than VERSION
        self.assertTrue(problems("Baseline: v9.9.9"))
        self.assertTrue(problems(f"Baseline: {newest}", version="0.0.1"))
        # the release candidate: the untagged baseline v<VERSION> is accepted, any other untagged one is not
        candidate = {name: "# t\n\nBaseline: v9.9.9\n"}
        self.assertEqual([], release_problems(candidate, set(), self.git, "9.9.9"))
        self.assertTrue(release_problems(candidate, set(), self.git, "9.9.10"))
        # TOOL-232: the untagged candidate v<VERSION>-rcN likewise; a candidate of another version is not accepted
        rc = {name: "# t\n\nBaseline: v9.9.9-rc1\n"}
        self.assertEqual([], release_problems(rc, set(), self.git, "9.9.9"))
        self.assertTrue(release_problems(rc, set(), self.git, "9.9.10"))          # VERSION 9.9.10, candidate of 9.9.9
        self.assertTrue(release_problems(rc, set(), self.git, "9.9.8"))           # newer than VERSION
        self.assertTrue(release_problems({name: "# t\n\nBaseline: v9.9.10-rc1\n"}, set(), self.git, "9.9.9"))
        # a candidate tag in the clone: the baseline line, and a working state after it (made for HEAD, where the
        # candidate is set); the working state line must then name the candidate, not the release before it
        tagged_rc = Git()
        tagged_rc.releases = dict(tagged_rc.releases, **{"v9.9.9-rc1": head})
        self.assertEqual([], release_problems(rc, set(), tagged_rc, "9.9.9"))
        after_rc = {name: f"# t\n\nWorking state: dev at {head[:8]} (after v9.9.9-rc1)\n"}
        self.assertEqual([], release_problems(after_rc, set(), tagged_rc, "9.9.9"))
        self.assertTrue(release_problems({name: f"# t\n\nWorking state: dev at {head[:8]} (after {newest})\n"},
                                         set(), tagged_rc, "9.9.9"))
        # a candidate on HEAD is no release checkout of VERSION: rule 4 (all lines `Baseline: v<VERSION>`) waits for
        # the release tag itself; the release tag on HEAD still triggers it
        on_rc = Git()
        on_rc.releases = tagged_rc.releases
        on_rc.head_tags = lambda: {"v9.9.9-rc1"}
        self.assertEqual([], release_problems(after_rc, set(), on_rc, "9.9.9"))
        on_release = Git()
        on_release.releases = dict(tagged_rc.releases, **{"v9.9.9": head})
        on_release.head_tags = lambda: {"v9.9.9-rc1", "v9.9.9"}
        self.assertTrue(release_problems(rc, set(), on_release, "9.9.9"))
        # a release checkout: every line must be the baseline of VERSION
        # (only head_tags changes, so no other rule can fire)
        fake = Git()
        fake.head_tags = lambda: {f"v{self.version}"}
        self.assertTrue(problems(working, git=fake))
        self.assertTrue(problems(f"Baseline: {newest}", git=fake) if newest != f"v{self.version}" else True)
        if f"v{self.version}" in self.git.releases:
            self.assertEqual([], release_problems({name: f"# t\n\nBaseline: v{self.version}\n"}, set(), fake,
                                                  self.version))
        # the baseline table against the tags
        self.assertTrue(baseline_table_problems(self.plan.replace(tagged, "0" * 40), self.git))
        extra = Git()
        extra.releases = dict(extra.releases, **{"v9.9.9": head})
        self.assertTrue(baseline_table_problems(self.plan, extra))
        self.assertTrue(baseline_table_problems(self.plan, extra, "9.9.10"))
        # only the tag of VERSION may lack its row (recorded in step 9 of 5.2); the clone's own tags are reduced to
        # those with a row, so a real tag of VERSION without its row (a release checkout) does not count here
        listed = Git()
        listed.releases = {t: c for t, c in listed.releases.items() if t in baseline_rows(self.plan)}
        listed.releases["v9.9.9"] = head
        self.assertEqual([], baseline_table_problems(self.plan, listed, "9.9.9"))
        self.assertTrue(baseline_table_problems(self.plan, listed, "9.9.10"))
        # TOOL-232: likewise a candidate of VERSION (its row comes after the tag), not a candidate of another version
        del listed.releases["v9.9.9"]
        listed.releases["v9.9.9-rc1"] = head
        self.assertEqual([], baseline_table_problems(self.plan, listed, "9.9.9"))
        self.assertTrue(baseline_table_problems(self.plan, listed, "9.9.10"))
        self.assertTrue(baseline_table_problems(self.plan, listed, "9.9.8"))
        row = f"| `v9.9.9-rc1` | `{head}` | x | x | development release |\n<!-- baselines:end -->"
        self.assertEqual([], baseline_table_problems(self.plan.replace("<!-- baselines:end -->", row), listed, "9.9.10"))
        wrong = row.replace(head, "0" * 40)
        self.assertTrue(baseline_table_problems(self.plan.replace("<!-- baselines:end -->", wrong), listed, "9.9.9"))
        empty = self.plan.split("<!-- baselines:start -->")[0] + "<!-- baselines:start -->\n<!-- baselines:end -->"
        self.assertTrue(baseline_table_problems(empty, self.git))


if __name__ == "__main__":
    unittest.main()
