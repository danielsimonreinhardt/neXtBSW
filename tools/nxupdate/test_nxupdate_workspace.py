"""nxupdate on a real customer workspace with the real generators (TOOL-215, finding DOC-36 of 2026-10-02).

`test_nxupdate.py` runs the workflow against invented trees with stub generators. Those stubs never looked at the
workspace's pin, so two faults stayed invisible until DOC-36 ran `nxupdate --to 0.2.0` on a real customer
project: a relative workspace path crashed (`ValueError` in `relative_to`), and with an absolute path the target
tree's `bswgen`/`rtegen` refused the "after" generation (the workspace still pinned the old tree), so every one of
the 86 generated files was reported as "removed" and the run ended with exit 1.

This test builds what a customer has, from this repository, in `%TEMP%/s32k` (short paths, ARCH-57):

  - `up.git`: a local bare clone of this repository (hard links, no network) with two invented releases on
    `master`, each an annotated tag: `v9.0.0` = the committed HEAD of this checkout with VERSION 9.0.0, and
    `v9.1.0` on top of it with VERSION 9.1.0 and one comment line added to two bswgen templates - one generated
    file whose path says "memory layout" (field-relevant) and one that says nothing (not field-relevant), so the
    report has a real, known difference produced by the real generators;
  - `ws`: a customer repository (git) with the tree as a real submodule `nextbsw` (`git submodule add
    --reference`, objects shared with `up.git`) on `v9.0.0`, a `nextbsw.yaml`, and one project with its own board
    outside the tree: a copy of the tree's `buildtest_s32k148_min`/`buildtest_s32k148` (never the eval board),
    whose `rte.yaml` reaches into the tree (`../../nextbsw/swc/DemoSwc`) the way a customer project does.

`bswgen`, `rtegen` and `nxconfig` of both releases are the real ones of this checkout's HEAD (committed state:
uncommitted edits of the generators are not part of the invented releases). Nothing is compiled and
`external/openbsw` is not needed: nxupdate only generates.

The tests run in order on one workspace: dry run from a relative path, dry run with `--tree` (a directory link
into the scratch folder that must be removed without touching the tree), and finally `--write --commit` through
the command line, after which the moved workspace must pass `nxconfig check` of its new tree.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))

from nxupdate import update  # noqa: E402

PYTHON = sys.executable
GIT = shutil.which("git")
IDENTITY = ["-c", "user.name=neXtBSW Test", "-c", "user.email=test@example.invalid",
            "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", "-c", "protocol.file.allow=always"]

OLD, NEW = "9.0.0", "9.1.0"
OPENBSW = "external/openbsw"
SOURCE_PROJECT = "projects/buildtest_s32k148_min"
SOURCE_BOARD = "boards/buildtest_s32k148"
PROJECT, BOARD = "acme_main", "acme_b1"
# the two templates release 9.1.0 changes, and the generated file each one produces (relative to the generation
# root of nxupdate: <project path>/board/...)
CHANGED_TEMPLATES = ("tools/bswgen/templates/memoryLayout.h.j2", "tools/bswgen/templates/pinEventConfiguration.h.j2")
TEST_LINE = "// nxupdate test release 9.1.0 (TOOL-215)\n"
EXPECTED_CHANGES = ("board/memory/memoryLayout.h", "board/bsp/pinEvent/pinEventConfiguration.h",
                    "board/identity/bswgen.sha256", "board/identity/nxIdentityFields.h", "rte/identity/rtegen.sha256")


def git(repo: Path | None, *args: str, env: dict | None = None, stdin: str | None = None) -> str:
    argv = [GIT, *(["-C", str(repo)] if repo is not None else []), *IDENTITY, *args]
    done = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
                          env=env, input=stdin)
    if done.returncode != 0:
        raise AssertionError(f"git {' '.join(args)}: {done.returncode} {done.stderr.strip()}")
    return done.stdout


def openbsw_checkout() -> Path | None:
    """An initialised external/openbsw to clone the workspace's nested submodule from: NX_OPENBSW_REFERENCE, this
    checkout's own, or - in a git worktree of this repository, which starts without submodules - the main
    checkout's (the same rule the agent worktrees use: --reference the main checkout)."""
    candidates = [os.environ.get("NX_OPENBSW_REFERENCE"), REPO / OPENBSW]
    common = subprocess.run([GIT, "-C", str(REPO), "rev-parse", "--git-common-dir"],
                            capture_output=True, text=True) if GIT else None
    if common is not None and common.returncode == 0 and common.stdout.strip():
        candidates.append((REPO / common.stdout.strip().splitlines()[-1]).resolve().parent / OPENBSW)
    for candidate in candidates:
        if candidate and (Path(candidate) / ".git").exists() and (Path(candidate) / "platforms").is_dir():
            return Path(candidate).resolve()
    return None


def scratch_base() -> Path:
    base = Path(os.environ.get("NX_NXUPDATE_TEST_DIR") or Path(tempfile.gettempdir()) / "s32k")
    base.mkdir(parents=True, exist_ok=True)
    return base


@unittest.skipUnless(GIT, "git is not installed")
class RealWorkspace(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.openbsw = openbsw_checkout()
        if cls.openbsw is None:
            raise unittest.SkipTest(f"no initialised {OPENBSW} checkout found (this checkout or the main one, or "
                                    "NX_OPENBSW_REFERENCE): git submodule update --init first")
        cls.tmp =Path(tempfile.mkdtemp(prefix="nxu-", dir=scratch_base())).resolve()
        try:
            cls._build()
        except BaseException:
            update.rmtree(cls.tmp)
            raise

    @classmethod
    def tearDownClass(cls):
        if os.environ.get("NX_NXUPDATE_TEST_KEEP"):
            print(f"\nkept: {cls.tmp}", file=sys.stderr)
            return
        update.rmtree(cls.tmp)

    # ------------------------------------------------------------------ fixture

    @classmethod
    def _build(cls):
        cls.up = cls.tmp / "up.git"
        git(None, "clone", "--quiet", "--bare", "--local", "--no-tags", str(REPO), str(cls.up))
        base = git(REPO, "rev-parse", "HEAD").strip()
        git(cls.up, "fetch", "--quiet", "--no-tags", str(REPO), f"+{base}:refs/heads/nx-base")
        base = cls._with_openbsw_gitlink(base)

        old = cls._release(base, OLD, {})
        changes = {path: git(cls.up, "show", f"{base}:{path}") + TEST_LINE for path in CHANGED_TEMPLATES}
        new = cls._release(old, NEW, changes)
        git(cls.up, "update-ref", "refs/heads/master", new)
        git(cls.up, "symbolic-ref", "HEAD", "refs/heads/master")

        cls.ws = cls.tmp / "ws"
        cls.ws.mkdir()
        git(cls.ws, "init", "--quiet")
        git(cls.ws, "checkout", "--quiet", "-B", "main")
        git(cls.ws, "submodule", "add", "--quiet", "--reference", str(cls.up), str(cls.up), "nextbsw")
        cls.tree = cls.ws / "nextbsw"
        git(cls.tree, "checkout", "--quiet", f"v{OLD}")
        # the nested submodule as a customer has it (git submodule update --init --recursive), but from the local
        # checkout of this repository instead of its URL: no network
        git(cls.tree, "-c", f"submodule.{OPENBSW}.url={cls.openbsw.as_posix()}", "submodule", "update", "--init",
            "--quiet", "--reference", str(cls.openbsw), "--", OPENBSW)

        (cls.ws / "nextbsw.yaml").write_text(
            "# acme/nextbsw.yaml - which neXtBSW this workspace is built with.\n"
            "schema_version: 1\n"
            "nextbsw:\n"
            f"  version: {OLD}                 # must equal the tree's VERSION file\n"
            "  source: submodule\n"
            "  path: nextbsw\n"
            "  profile: open\n"
            "search:\n"
            "  boards: [boards]\n", encoding="utf-8")
        project_dir = cls.ws / "projects" / PROJECT
        project_dir.mkdir(parents=True)
        board_dir = cls.ws / "boards" / BOARD
        board_dir.mkdir(parents=True)
        project = git(cls.up, "show", f"{base}:{SOURCE_PROJECT}/project.yaml")
        rte = git(cls.up, "show", f"{base}:{SOURCE_PROJECT}/rte.yaml")
        board = git(cls.up, "show", f"{base}:{SOURCE_BOARD}/board.yaml")
        for text, old_ref in ((project, f"board: ../../{SOURCE_BOARD}"), (rte, "path: ../../swc/"),
                              (board, "name: buildtest_s32k148")):
            assert old_ref in text, f"fixture: {old_ref!r} no longer in the tree's copy - adapt the test"
        (project_dir / "project.yaml").write_text(project.replace(f"board: ../../{SOURCE_BOARD}",
                                                                  f"board: ../../boards/{BOARD}"), encoding="utf-8")
        (project_dir / "rte.yaml").write_text(rte.replace("path: ../../swc/", "path: ../../nextbsw/swc/"),
                                              encoding="utf-8")
        (board_dir / "board.yaml").write_text(board.replace("name: buildtest_s32k148", f"name: {BOARD}"),
                                              encoding="utf-8")
        (cls.ws / ".gitignore").write_text("build*/\n", encoding="utf-8")
        git(cls.ws, "add", "-A")
        git(cls.ws, "commit", "--quiet", "-m", "acme: first project")

    @classmethod
    def _with_openbsw_gitlink(cls, base: str) -> str:
        """The open export is one commit without a submodule link (.gitmodules only, the content comes from
        upstream): the tree then has no gitlink for external/openbsw and `git submodule update` finds no pathspec.
        A customer's tree has the link, so the fixture adds it: a commit on `base` pointing at the checkout's HEAD.
        A tree that has the link already (the development repository) is returned unchanged."""
        if git(cls.up, "ls-tree", base, "--", OPENBSW).strip():
            return base
        index = cls.tmp / "index-gitlink"
        env = dict(os.environ, GIT_INDEX_FILE=str(index))
        git(cls.up, "read-tree", base, env=env)
        sha = git(cls.openbsw, "rev-parse", "HEAD").strip()
        git(cls.up, "update-index", "--add", "--cacheinfo", f"160000,{sha},{OPENBSW}", env=env)
        tree = git(cls.up, "write-tree", env=env).strip()
        commit = git(cls.up, "commit-tree", tree, "-p", base, "-m", "fixture: gitlink of external/openbsw").strip()
        index.unlink()
        git(cls.up, "update-ref", "refs/heads/nx-base", commit)
        return commit

    @classmethod
    def _release(cls, parent: str, version: str, changes: dict[str, str]) -> str:
        """A release commit on `parent` without a checkout (temporary index): VERSION, a CHANGELOG section and
        `changes`, tagged annotated `v<version>`."""
        index = cls.tmp / f"index-{version}"
        env = dict(os.environ, GIT_INDEX_FILE=str(index))
        git(cls.up, "read-tree", parent, env=env)
        changelog = git(cls.up, "show", f"{parent}:CHANGELOG.md")
        files = dict(changes)
        files["VERSION"] = f"{version}\n"
        files["CHANGELOG.md"] = f"## {version} - invented release of the nxupdate test\n\n{changelog}"
        for path, text in files.items():
            blob = git(cls.up, "hash-object", "-w", "--stdin", stdin=text).strip()
            git(cls.up, "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", env=env)
        tree = git(cls.up, "write-tree", env=env).strip()
        commit = git(cls.up, "commit-tree", tree, "-p", parent, "-m", f"release {version}").strip()
        git(cls.up, "tag", "-a", f"v{version}", commit, "-m", f"neXtBSW {version}")
        index.unlink()
        return commit

    # ------------------------------------------------------------------ checks shared by the runs

    def assert_clean_report(self, result: update.UpdateResult) -> None:
        self.assertEqual(result.before_problems, [])
        self.assertEqual(result.after_problems, [], "the target tree's generators must accept the staged "
                                                    "workspace (DOC-36: 'the workspace pins the tree ...')")
        self.assertTrue(result.pin_status.usable, result.pin_status.text)
        kinds = {d.change for d in result.file_diffs}
        self.assertNotIn("removed", kinds, "DOC-36: every generated file was reported as removed")
        self.assertNotIn("added", kinds)
        by_path = {d.path: d for d in result.file_diffs}
        # exactly the two files of the changed templates, plus the generation identity (PLAT-79), which names the
        # neXtBSW version and hashes the generators - nothing else, in particular no path of the staged copy
        self.assertEqual(sorted(by_path), sorted(f"projects/{PROJECT}/{p}" for p in EXPECTED_CHANGES),
                         "\n\n".join(d.diff for d in result.file_diffs))
        layout = by_path[f"projects/{PROJECT}/board/memory/memoryLayout.h"]
        pin_event = by_path[f"projects/{PROJECT}/board/bsp/pinEvent/pinEventConfiguration.h"]
        for d in (layout, pin_event):
            self.assertEqual((d.change, d.added, d.removed), ("changed", 1, 0), d.diff)
            self.assertIn(TEST_LINE.strip(), d.diff)
        self.assertTrue(layout.field_relevant)
        self.assertFalse(pin_event.field_relevant)
        self.assertIn(f"+# generator bswgen, neXtBSW {NEW}", by_path[f"projects/{PROJECT}/board/identity/bswgen.sha256"].diff)
        self.assertEqual(result.messages_new, {}, "no nxconfig check message may appear only because the run "
                                                  "happened in a staged copy")
        self.assertEqual(result.messages_gone, {})
        self.assertIn(f"## {NEW} - invented release", result.changelog)
        self.assertNotIn("after-ws", result.report_text, "paths of the staged copy must be mapped back")
        self.assertTrue(result.ok)

    def assert_tree_untouched(self) -> None:
        self.assertEqual(git(self.tree, "describe", "--tags").strip(), f"v{OLD}")
        self.assertEqual((self.tree / "VERSION").read_text(encoding="utf-8").strip(), OLD)
        self.assertTrue((self.tree / OPENBSW / "platforms").is_dir(), "the nested submodule must stay intact")
        worktrees = [line for line in git(self.tree, "worktree", "list", "--porcelain").splitlines()
                     if line.startswith("worktree ")]
        self.assertEqual(len(worktrees), 1, f"a worktree of the run was left behind: {worktrees}")
        self.assertEqual(git(self.ws, "status", "--porcelain").strip(), "")

    # ------------------------------------------------------------------ the runs

    def test_1_dry_run_from_a_relative_path(self):
        """DOC-36 part 1: `nxupdate <relative path>` crashed with ValueError (update.py:302)."""
        cwd = os.getcwd()
        os.chdir(self.tmp)
        try:
            result = update.run(Path("ws"), NEW, python=PYTHON)
        finally:
            os.chdir(cwd)
        self.assertEqual(result.workspace, self.ws)
        self.assertFalse(result.written)
        self.assert_clean_report(result)
        self.assert_tree_untouched()
        self.assertIn(f"version: {OLD}", (self.ws / "nextbsw.yaml").read_text(encoding="utf-8"))

    def test_2_dry_run_with_an_explicit_tree(self):
        """--tree: the tree is linked into the staged copy; the link must go, the tree must stay."""
        materialised = self.tmp / "t910"
        git(self.up, "worktree", "add", "--quiet", "--detach", str(materialised), f"v{NEW}")
        try:
            git(materialised, "-c", f"submodule.{OPENBSW}.url={self.openbsw.as_posix()}", "submodule", "update",
                "--init", "--quiet", "--", OPENBSW)
            result = update.run(self.ws, NEW, tree=materialised, python=PYTHON)
            self.assert_clean_report(result)
            self.assertEqual((materialised / "VERSION").read_text(encoding="utf-8").strip(), NEW,
                             "removing the scratch folder must never delete through the link")
            self.assertTrue((materialised / "tools" / "bswgen" / "bswgen.py").is_file())
            self.assert_tree_untouched()
        finally:
            update.remove_worktree(self.up, materialised)     # git refuses "worktree remove" with submodules

    def test_3_write_and_commit_on_the_command_line(self):
        """DOC-36 part 2, the whole way: --write --commit with a relative path, exit 0, and the moved workspace
        passes nxconfig check of its new tree."""
        done = subprocess.run([PYTHON, str(HERE), "ws", "--to", NEW, "--write", "--commit"], cwd=self.tmp,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
        self.assertEqual(done.returncode, 0, done.stderr[-4000:])
        self.assertIn(f"(0 added, 0 removed, {len(EXPECTED_CHANGES)} changed)", done.stdout)
        self.assertNotIn("after-ws", done.stdout)
        self.assertIn("written: yes", done.stderr)
        self.assertIn("committed: yes", done.stderr)

        self.assertIn(f"version: {NEW}", (self.ws / "nextbsw.yaml").read_text(encoding="utf-8"))
        self.assertEqual(git(self.tree, "describe", "--tags").strip(), f"v{NEW}")
        self.assertEqual(git(self.ws, "status", "--porcelain").strip(), "", "the commit must hold everything")
        self.assertIn(f"v{OLD} -> v{NEW}", git(self.ws, "log", "-1", "--format=%s"))
        self.assertEqual(git(self.ws, "remote").strip(), "", "nxupdate never pushes or configures a remote")
        report = self.ws / "doc" / "nextbsw-updates" / f"v{OLD}-v{NEW}.md"
        self.assertTrue(report.is_file())
        self.assertNotIn("Generation problems", report.read_text(encoding="utf-8"))

        check = subprocess.run([PYTHON, str(self.tree / "tools" / "nxconfig"), "check",
                                str(self.ws / "projects" / PROJECT / "project.yaml")],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)

    def test_4_same_version_reports_no_difference(self):
        """DOC-36 ran `--to` the version already pinned: the staged copy must generate exactly what the real
        workspace generates - an update to the same release is no change at all."""
        self.assertIn(f"version: {NEW}", (self.ws / "nextbsw.yaml").read_text(encoding="utf-8"), "runs after test_3")
        result = update.run(self.ws, NEW, python=PYTHON)
        self.assertEqual(result.before_problems, [])
        self.assertEqual(result.after_problems, [])
        self.assertEqual(result.file_diffs, [], "\n\n".join(d.diff for d in result.file_diffs))
        self.assertEqual((result.messages_new, result.messages_gone), ({}, {}))
        self.assertTrue(result.ok)


if __name__ == "__main__":
    unittest.main()
