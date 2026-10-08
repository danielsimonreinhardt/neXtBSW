# SPDX-License-Identifier: Apache-2.0
"""Host tests of the release publication tool (TOOL-69, doc/architecture/customer-project.md gap N15).

Everything here runs against **temporary repositories in %TEMP%**, never against this repository: a tiny
invented git tree (VERSION, CHANGELOG.md, optionally a distcheck rule set of its own) built from nothing, so the
test says what state it describes instead of depending on where this tree's own history and tags happen to
stand. Every positive case ("the tag is ready", "the profile exports") is paired with a counter-check ("dirty
blocks it", "a finding blocks it, and nothing was written") - the point of this tool is that it never mutates the
real repository, so the tests prove exactly that: `plan_tag`/`build_release_plan` never call `git tag`, and a
refused `create_tag` leaves no tag behind.

    python -m unittest tools/ci/test_release.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))

from tools.ci import ci as ci_module            # noqa: E402
from tools.ci import release                    # noqa: E402

GIT = shutil.which("git")

#: `git commit` and `git tag -a` refuse to run without an identity, and a test must not depend on the one this
#: workstation happens to have (same fixture as tools/nxconfig/test_pincheck.py).
IDENTITY = ["-c", "user.name=neXtBSW Test", "-c", "user.email=test@example.invalid",
            "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false"]


def git(repo: Path, *args: str) -> str:
    done = subprocess.run([GIT, "-C", str(repo), *IDENTITY, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60)
    if done.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} in {repo}: {done.returncode} {done.stderr.strip()}")
    return done.stdout.strip()


def write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def commit(repo: Path, message: str) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)


@unittest.skipUnless(GIT, "git is not installed")
class GitFixture(unittest.TestCase):
    """A synthetic git tree `nextbsw` under %TEMP%, on `master`, with a VERSION file."""

    VERSION = "1.0.0"

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="nxrelease-test-")).resolve()
        self.tree = self.tmp / "nextbsw"
        self.tree.mkdir()
        git(self.tree, "init", "-q")
        git(self.tree, "checkout", "-q", "-B", "master")
        write(self.tree, "VERSION", f"{self.VERSION}\n")
        write(self.tree, "CHANGELOG.md", "# Changelog\n\n- first entry\n")
        commit(self.tree, "synthetic release")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)


class TagPlanning(GitFixture):
    """plan_tag is read-only and names every reason a release tag is not ready yet (SYS-68)."""

    def test_ready_on_a_clean_master(self):
        plan = release.plan_tag(self.tree, self.VERSION)
        self.assertEqual([], plan.blocking, plan.blocking)
        self.assertTrue(plan.ready)
        self.assertEqual(plan.tag, "v1.0.0")
        self.assertTrue(plan.on_master)
        self.assertFalse(plan.dirty)
        self.assertFalse(plan.exists)

    def test_command_is_an_annotated_tag(self):
        plan = release.plan_tag(self.tree, self.VERSION, message="release 1.0.0")
        self.assertEqual(plan.command, ["git", "-C", str(self.tree), "tag", "-a", "v1.0.0", "-m", "release 1.0.0"])

    def test_default_message_names_the_version(self):
        plan = release.plan_tag(self.tree, self.VERSION)
        self.assertIn("1.0.0", plan.command[-1])

    def test_dirty_tree_blocks_and_says_so(self):
        write(self.tree, "VERSION", "1.0.0\nstray\n")  # uncommitted change, still git-valid text
        plan = release.plan_tag(self.tree, self.VERSION)
        self.assertTrue(plan.dirty)
        self.assertTrue(any("dirty" in b for b in plan.blocking), plan.blocking)

    def test_existing_tag_blocks(self):
        git(self.tree, "tag", "-a", "-m", "already there", "v1.0.0")
        plan = release.plan_tag(self.tree, self.VERSION)
        self.assertTrue(plan.exists)
        self.assertTrue(any("already exists" in b for b in plan.blocking), plan.blocking)

    def test_version_mismatch_blocks(self):
        plan = release.plan_tag(self.tree, "2.0.0")
        self.assertFalse(plan.version_matches)
        self.assertTrue(any("VERSION" in b for b in plan.blocking), plan.blocking)

    def test_not_on_master_blocks(self):
        git(self.tree, "checkout", "-q", "-b", "feature")
        write(self.tree, "src.txt", "x\n")
        commit(self.tree, "on a side branch")
        plan = release.plan_tag(self.tree, self.VERSION)
        self.assertEqual(plan.master_ref, "master")
        self.assertFalse(plan.on_master)
        self.assertTrue(any("master" in b for b in plan.blocking), plan.blocking)

    def test_no_master_ref_blocks(self):
        git(self.tree, "branch", "-m", "master", "trunk")
        plan = release.plan_tag(self.tree, self.VERSION)
        self.assertEqual(plan.master_ref, "")
        self.assertTrue(any("no master ref" in b for b in plan.blocking), plan.blocking)

    def test_not_a_git_checkout_blocks(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        write(plain, "VERSION", "1.0.0\n")
        plan = release.plan_tag(plain, "1.0.0")
        self.assertTrue(any("not a git checkout" in b for b in plan.blocking), plan.blocking)

    def test_malformed_version_raises(self):
        with self.assertRaises(release.ReleaseError):
            release.plan_tag(self.tree, "1.0")

    def test_plan_tag_never_creates_a_tag(self):
        """Gegenprobe: asking the question never answers it by acting."""
        before = git(self.tree, "tag", "-l")
        release.plan_tag(self.tree, self.VERSION)
        self.assertEqual(before, git(self.tree, "tag", "-l"))


class CreateTag(GitFixture):
    """create_tag is the one place that actually tags - exercised only against this throw-away repository."""

    def test_creates_a_real_annotated_tag(self):
        plan = release.create_tag(self.tree, self.VERSION, message="release 1.0.0")
        self.assertTrue(plan.ready)
        self.assertEqual(git(self.tree, "cat-file", "-t", "refs/tags/v1.0.0"), "tag")
        self.assertIn("release 1.0.0", git(self.tree, "tag", "-l", "-n1", "v1.0.0"))

    def test_refuses_and_creates_nothing_when_dirty(self):
        write(self.tree, "VERSION", "1.0.0\nstray\n")
        before = git(self.tree, "tag", "-l")
        with self.assertRaises(release.ReleaseError):
            release.create_tag(self.tree, self.VERSION)
        self.assertEqual(before, git(self.tree, "tag", "-l"))

    def test_refuses_when_the_tag_already_exists(self):
        git(self.tree, "tag", "-a", "-m", "x", "v1.0.0")
        with self.assertRaises(release.ReleaseError):
            release.create_tag(self.tree, self.VERSION)


class ChangelogNotes(GitFixture):
    """Release notes are what CHANGELOG.md gained since the previous release tag, wherever in the file."""

    def test_first_release_takes_the_whole_file(self):
        notes, since = release.changelog_release_notes(self.tree, "1.0.0")
        self.assertIsNone(since)
        self.assertIn("first entry", notes)

    def test_previous_tag_is_found_once_it_exists(self):
        git(self.tree, "tag", "-a", "-m", "x", "v1.0.0")
        self.assertIsNone(release.previous_release_tag(self.tree, "1.0.0"))       # nothing older than 1.0.0
        self.assertEqual("v1.0.0", release.previous_release_tag(self.tree, "1.1.0"))
        self.assertEqual("v1.0.0", release.previous_release_tag(self.tree))       # newest overall

    def test_notes_are_only_the_new_lines(self):
        git(self.tree, "tag", "-a", "-m", "x", "v1.0.0")
        write(self.tree, "CHANGELOG.md", "# Changelog\n\n- first entry\n- second entry (new)\n")
        commit(self.tree, "add entry")
        notes, since = release.changelog_release_notes(self.tree, "1.1.0")
        self.assertEqual("v1.0.0", since)
        self.assertIn("second entry (new)", notes)
        self.assertNotIn("first entry", notes)

    def test_uncommitted_changelog_edits_count_too(self):
        """The plan is asked before the tag (and often before the commit) exists - it reads the working tree."""
        git(self.tree, "tag", "-a", "-m", "x", "v1.0.0")
        write(self.tree, "CHANGELOG.md", "# Changelog\n\n- first entry\n- uncommitted new line\n")
        notes, _ = release.changelog_release_notes(self.tree, "1.1.0")
        self.assertIn("uncommitted new line", notes)

    def test_explicit_since_tag_overrides_autodetection(self):
        git(self.tree, "tag", "-a", "-m", "x", "v1.0.0")
        notes, since = release.changelog_release_notes(self.tree, "1.0.0", since_tag="v1.0.0")
        self.assertEqual("v1.0.0", since)
        self.assertIn("no new content", notes)

    def test_no_new_content_says_so(self):
        git(self.tree, "tag", "-a", "-m", "x", "v1.0.0")
        notes, _ = release.changelog_release_notes(self.tree, "1.1.0")
        self.assertIn("no new content", notes)

    def test_no_tags_at_all_gives_no_previous_tag(self):
        self.assertIsNone(release.previous_release_tag(self.tree))


class CiReportArchive(unittest.TestCase):
    """The interim storage for CI reports of a tagged baseline until SYS-67 defines its own (SYS-67 is open)."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="nxrelease-archive-"))
        self.source = self.tmp / "ci-out"
        self.source.mkdir()
        (self.source / "summary.md").write_text("# CI\n\nOK\n", encoding="utf-8")
        self.archive_root = self.tmp / "archive"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_source_returns_none(self):
        self.assertIsNone(release.archive_ci_report(self.tmp / "no-such-dir", "v1.0.0", self.archive_root))

    def test_copies_the_report_under_the_tag(self):
        dest = release.archive_ci_report(self.source, "v1.0.0", self.archive_root)
        self.assertEqual(dest, self.archive_root / "v1.0.0")
        self.assertEqual("# CI\n\nOK\n", (dest / "summary.md").read_text(encoding="utf-8"))

    def test_existing_archive_refuses_without_force(self):
        release.archive_ci_report(self.source, "v1.0.0", self.archive_root)
        with self.assertRaises(release.ReleaseError):
            release.archive_ci_report(self.source, "v1.0.0", self.archive_root)

    def test_force_overwrites(self):
        release.archive_ci_report(self.source, "v1.0.0", self.archive_root)
        (self.source / "extra.txt").write_text("new\n", encoding="utf-8")
        dest = release.archive_ci_report(self.source, "v1.0.0", self.archive_root, force=True)
        self.assertTrue((dest / "extra.txt").is_file())

    def test_default_archive_root_honours_the_env_var(self):
        env = {"NX_RELEASE_ARCHIVE": str(self.archive_root)}
        self.assertEqual(self.archive_root, release.default_archive_root(env))

    def test_default_archive_root_is_a_per_user_cache_not_the_tree(self):
        root = release.default_archive_root({})
        self.assertIn("nextbsw", root.parts)
        self.assertIn("release-archive", root.parts)
        self.assertNotIn(str(REPO), str(root))


class ReleaseTargetsConfig(unittest.TestCase):
    """Where an export would be pushed (customer-project.md Q2; repositories created by the user 2026-10-02)."""

    def test_missing_file_is_empty(self):
        self.assertEqual({}, release.load_release_targets(Path(tempfile.mkdtemp()) / "no-such-file.yaml"))

    def test_loads_configured_targets(self):
        tmp = Path(tempfile.mkdtemp())
        path = tmp / "targets.yaml"
        path.write_text("targets:\n  open: https://example.invalid/nextbsw-open.git\n", encoding="utf-8")
        try:
            self.assertEqual({"open": "https://example.invalid/nextbsw-open.git"},
                             release.load_release_targets(path))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_the_real_file_names_the_open_repository_only(self):
        """Gegenprobe against the actual tools/distcheck/release-targets.yaml: PLAT-114 enters the open repository the
        user created on 2026-10-02; licensed and NDA lines have none yet and stay undecided, not a guessed URL. The
        Studio's repository is recorded beside the profiles, never as one (it is no distcheck profile, F12), and the
        private development repository is never a target."""
        targets = release.load_release_targets(release.TARGETS_FILE)
        self.assertEqual({"open", "licensed", "nda"}, set(targets))
        self.assertEqual("https://github.com/danielsimonreinhardt/neXtBSW.git", targets["open"])
        self.assertTrue(all(targets[p] in (None, "") for p in ("licensed", "nda")), targets)
        import yaml
        data = yaml.safe_load(release.TARGETS_FILE.read_text(encoding="utf-8"))
        self.assertTrue(str(data["studio"]).endswith("/neXtBSW-Studio.git"), data["studio"])
        self.assertNotIn("neXtBSW_development", release.TARGETS_FILE.read_text(encoding="utf-8"))


OPEN_RULES = textwrap.dedent("""\
    version: 1
    profiles:
      open: { licenses: [Apache-2.0] }
    content_patterns:
      - { id: leak, regex: "SECRETMARK", text: "planted leak" }
    paths:
      - { glob: "**", class: private }
      - { glob: "src/**", class: open, license: Apache-2.0 }
      - { glob: "LICENSE", class: open, license: Apache-2.0 }
      - { glob: "NOTICE", class: open, license: Apache-2.0 }
    export:
      project: ReleaseTest
      copyright: "2026 Tester"
      license: Apache-2.0
      required_files: [LICENSE, NOTICE]
      generate:
        LICENSE: { text: "Apache License 2.0 (test)\\n" }
        NOTICE: { kind: notice }
    """)


class ExportPerProfile(unittest.TestCase):
    """export_profile is `python tools/distcheck export` with the target repository folded into the result -
    a profile with findings is reported, not silently forced (allow_findings is for trial runs only)."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="nxrelease-export-"))
        self.repo = self.tmp / "repo"
        (self.repo / "src").mkdir(parents=True)
        (self.repo / "src" / "main.c").write_text(
            "// SPDX-License-Identifier: Apache-2.0\nint main(void) { return 0; }\n", encoding="utf-8")
        self.rules_path = self.tmp / "distribution.yaml"
        self.rules_path.write_text(OPEN_RULES, encoding="utf-8")
        self.out = self.tmp / "export"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_clean_profile_is_exported(self):
        result = release.export_profile(self.repo, self.out, "open", rules_path=self.rules_path,
                                        patterns_path=None, use_git=False, targets={})
        self.assertTrue(result.exported)
        self.assertEqual(0, result.errors)
        self.assertTrue((self.out / "src" / "main.c").is_file())
        self.assertTrue((self.out / "LICENSE").is_file())
        self.assertIsNone(result.target)
        self.assertEqual(release.HOSTING_NOTE, result.target_note)

    def test_a_configured_target_is_carried_over(self):
        result = release.export_profile(self.repo, self.out, "open", rules_path=self.rules_path,
                                        patterns_path=None, use_git=False,
                                        targets={"open": "https://example.invalid/nextbsw-open.git"})
        self.assertEqual("https://example.invalid/nextbsw-open.git", result.target)
        self.assertIsNone(result.target_note)

    def test_a_finding_blocks_the_export_and_writes_nothing(self):
        (self.repo / "src" / "leak.c").write_text("// SPDX-License-Identifier: Apache-2.0\nSECRETMARK\n",
                                                   encoding="utf-8")
        result = release.export_profile(self.repo, self.out, "open", rules_path=self.rules_path,
                                        patterns_path=None, use_git=False, targets={})
        self.assertFalse(result.exported)
        self.assertGreaterEqual(result.errors, 1)
        self.assertFalse(self.out.exists() and any(self.out.iterdir()))

    def test_allow_findings_forces_the_export(self):
        (self.repo / "src" / "leak.c").write_text("// SPDX-License-Identifier: Apache-2.0\nSECRETMARK\n",
                                                   encoding="utf-8")
        result = release.export_profile(self.repo, self.out, "open", rules_path=self.rules_path,
                                        patterns_path=None, use_git=False, allow_findings=True, targets={})
        self.assertTrue(result.exported)
        self.assertGreaterEqual(result.errors, 1)


class ArchiveWithSha256(unittest.TestCase):
    """Variant 3 (customer-project.md 4.4): a release archive plus its checksum, in sha256sum's own format."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="nxrelease-archive-zip-"))
        self.export_dir = self.tmp / "export"
        (self.export_dir / "sub").mkdir(parents=True)
        (self.export_dir / "LICENSE").write_text("Apache-2.0\n", encoding="utf-8")
        (self.export_dir / "sub" / "file.c").write_text("int x;\n", encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_zip_and_checksum_match_the_content(self):
        zip_path, digest = release.archive_zip_with_sha256(self.export_dir, self.tmp / "nextbsw-1.0.0-open.zip")
        self.assertTrue(zip_path.is_file())
        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
        self.assertEqual({"LICENSE", "sub/file.c"}, names)
        import hashlib
        self.assertEqual(hashlib.sha256(zip_path.read_bytes()).hexdigest(), digest)
        sha_file = zip_path.with_name(zip_path.name + ".sha256")
        self.assertEqual(f"{digest}  {zip_path.name}\n", sha_file.read_text(encoding="utf-8"))


class BuildReleasePlanEndToEnd(GitFixture):
    """The whole gap N15 in one call: local artefacts only, never a real tag, never a push."""

    def setUp(self) -> None:
        super().setUp()
        write(self.tree, "tools/distcheck/distribution.yaml", "")  # not used: plan takes an explicit rules_path
        (self.tree / "src").mkdir()
        write(self.tree, "src/main.c", "// SPDX-License-Identifier: Apache-2.0\nint main(void){return 0;}\n")
        self.rules_path = self.tree / "release-rules.yaml"
        self.rules_path.write_text(OPEN_RULES, encoding="utf-8")
        commit(self.tree, "add source")
        self.out = self.tmp / "out"

    def _plan(self, **kwargs):
        kwargs.setdefault("profiles", ["open"])
        kwargs.setdefault("use_git", False)     # the export half looks at a plain listing, not git ls-files
        return release.build_release_plan(self.tree, self.VERSION, out_dir=self.out, **kwargs)

    def test_plan_covers_tag_notes_and_export(self):
        import unittest.mock as mock

        with mock.patch.object(release, "DISTRIBUTION_FILE", self.rules_path):
            plan = self._plan(targets={})       # no target configured: the plan says so instead of an address
        self.assertEqual("v1.0.0", plan.tag)
        self.assertEqual([], plan.tag_plan["blocking"], plan.tag_plan["blocking"])
        self.assertIsNone(plan.since_tag)
        self.assertIn("first entry", plan.release_notes)
        self.assertTrue(Path(plan.release_notes_file).is_file())
        self.assertEqual(1, len(plan.profiles))
        self.assertTrue(plan.profiles[0]["exported"])
        self.assertEqual(release.HOSTING_NOTE, plan.profiles[0]["target_note"])
        # PLAT-114: with the real release-targets.yaml the open profile carries its repository and no note
        with mock.patch.object(release, "DISTRIBUTION_FILE", self.rules_path):
            plan = self._plan()
        self.assertEqual(release.load_release_targets()["open"], plan.profiles[0]["target"])
        self.assertIsNone(plan.profiles[0]["target_note"])

    def test_missing_report_dir_is_only_a_note(self):
        import unittest.mock as mock

        with mock.patch.object(release, "DISTRIBUTION_FILE", self.rules_path):
            plan = self._plan(report_dir=self.tmp / "no-such-report")
        self.assertIsNone(plan.ci_report)
        self.assertIn("does not exist yet", plan.ci_report_note)

    def test_report_dir_is_archived_under_the_tag(self):
        import unittest.mock as mock

        report_dir = self.tmp / "ci-out"
        report_dir.mkdir()
        (report_dir / "summary.md").write_text("ok\n", encoding="utf-8")
        archive_root = self.tmp / "archive"
        with mock.patch.object(release, "DISTRIBUTION_FILE", self.rules_path):
            plan = self._plan(report_dir=report_dir, archive_root=archive_root)
        self.assertEqual(str(archive_root / "v1.0.0"), plan.ci_report)
        self.assertTrue((archive_root / "v1.0.0" / "summary.md").is_file())

    def test_archive_flag_writes_zip_and_sha256(self):
        import unittest.mock as mock

        with mock.patch.object(release, "DISTRIBUTION_FILE", self.rules_path):
            plan = self._plan(archive=True)
        row = plan.profiles[0]
        self.assertIsNotNone(row["archive"])
        self.assertIsNotNone(row["sha256"])
        self.assertTrue(Path(row["archive"]).is_file())

    def test_plan_and_json_round_trip(self):
        import unittest.mock as mock

        with mock.patch.object(release, "DISTRIBUTION_FILE", self.rules_path):
            plan = self._plan()
        json_path, md_path = release.write_plan_files(plan, self.out)
        data = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual("v1.0.0", data["tag"])
        markdown = md_path.read_text(encoding="utf-8")
        self.assertIn("## Tag (SYS-68)", markdown)
        self.assertIn("tag -a v1.0.0", markdown)
        self.assertIn("## Export per profile", markdown)

    def test_never_creates_a_real_tag_or_touches_the_network(self):
        """The Gegenprobe the whole tool exists for: a plan is not a release."""
        import unittest.mock as mock

        before = git(self.tree, "tag", "-l")
        with mock.patch.object(release, "DISTRIBUTION_FILE", self.rules_path):
            self._plan(archive=True)
        self.assertEqual(before, git(self.tree, "tag", "-l"))


class ReportBinding(GitFixture):
    """SYS-67 (cm-plan.md G1): a CI report counts for a baseline only if it ran strict, green and clean on exactly
    the tree that is tagged. Each rule has its counter-check on an altered copy of a good report."""

    def setUp(self) -> None:
        super().setUp()
        self.report_dir = self.tmp / "ci-out"
        self.report_dir.mkdir()
        self.good = {"verdict": "pass", "strict": True, "slow": True, "workspace": None,
                     "provenance": {"commit": git(self.tree, "rev-parse", "HEAD"),
                                    "tree": git(self.tree, "rev-parse", "HEAD^{tree}"),
                                    "describe": "abc", "version": self.VERSION, "dirty": False, "changes_total": 0,
                                    "end": {"commit": git(self.tree, "rev-parse", "HEAD"),
                                            "tree": git(self.tree, "rev-parse", "HEAD^{tree}")}}}

    def bind(self, data: dict | None = None, version: str | None = "1.0.0") -> dict:
        if data is not None:
            (self.report_dir / "ci-result.json").write_text(json.dumps(data), encoding="utf-8")
        return release.report_binding(self.report_dir, self.tree, version)

    def altered(self, **changes) -> dict:
        data = json.loads(json.dumps(self.good))
        for key, value in changes.items():
            if key in data:
                data[key] = value
            else:
                data["provenance"][key] = value
        return data

    def test_a_good_report_is_bound(self):
        binding = self.bind(self.good)
        self.assertTrue(binding["bound"], binding["problems"])
        self.assertEqual(self.good["provenance"]["tree"], binding["tree"])

    def test_the_release_merge_with_the_same_tree_still_binds(self):
        """The report runs on dev, the tag sits on the merge commit on master - same tree, other commit."""
        git(self.tree, "checkout", "-q", "-b", "dev")
        write(self.tree, "a.txt", "candidate\n")
        commit(self.tree, "candidate")
        data = self.altered(commit=git(self.tree, "rev-parse", "HEAD"), tree=git(self.tree, "rev-parse", "HEAD^{tree}"))
        data["provenance"]["end"] = {"commit": data["provenance"]["commit"], "tree": data["provenance"]["tree"]}
        git(self.tree, "checkout", "-q", "master")
        git(self.tree, "merge", "-q", "--no-ff", "dev", "-m", "release merge")
        self.assertNotEqual(data["provenance"]["commit"], git(self.tree, "rev-parse", "HEAD"))
        self.assertTrue(self.bind(data)["bound"])

    def test_counter_checks(self):
        cases = {
            "verdict": self.altered(verdict="fail"),
            "--strict": self.altered(strict=False),
            "workspace": self.altered(workspace="C:/customer"),
            "not clean": self.altered(dirty=True),
            "moved": self.altered(end={"commit": "0" * 40, "tree": "1" * 40}),
            "tree": self.altered(tree="f" * 40, end={"commit": self.good["provenance"]["commit"], "tree": "f" * 40}),
            "VERSION": self.altered(version="0.9.0"),
            "no commit": {k: v for k, v in self.good.items() if k != "provenance"},
        }
        for needle, data in cases.items():
            binding = self.bind(data)
            self.assertFalse(binding["bound"], needle)
            self.assertTrue(any(needle in p for p in binding["problems"]), (needle, binding["problems"]))

    def test_missing_result_file(self):
        binding = self.bind()
        self.assertFalse(binding["bound"])
        self.assertIn("missing", binding["problems"][0])

    def test_plan_archives_zip_with_sha256_and_names_the_binding(self):
        import unittest.mock as mock

        self.bind(self.good)
        archive_root = self.tmp / "archive"
        rules = self.tmp / "rules.yaml"
        rules.write_text(OPEN_RULES, encoding="utf-8")
        with mock.patch.object(release, "DISTRIBUTION_FILE", rules):
            plan = release.build_release_plan(self.tree, self.VERSION, out_dir=self.tmp / "out", profiles=[],
                                              report_dir=self.report_dir, archive_root=archive_root,
                                              use_git=False)
        self.assertTrue(plan.ci_report_binding["bound"], plan.ci_report_binding["problems"])
        zip_path = Path(plan.ci_report_zip)
        self.assertEqual(archive_root / "ci-out-v1.0.0.zip", zip_path)
        import hashlib

        self.assertEqual(hashlib.sha256(zip_path.read_bytes()).hexdigest(), plan.ci_report_sha256)
        self.assertEqual(f"{plan.ci_report_sha256}  ci-out-v1.0.0.zip\n",
                         (archive_root / "ci-out-v1.0.0.zip.sha256").read_text(encoding="utf-8"))
        with zipfile.ZipFile(zip_path) as zf:
            self.assertIn("ci-result.json", zf.namelist())
        markdown = release.render_markdown(plan)
        self.assertIn(f"CI report: sha256 {plan.ci_report_sha256} (ci-out-v1.0.0.zip)", markdown)
        self.assertIn("gh release create v1.0.0", markdown)

    def test_strict_release_refuses_an_unbound_report(self):
        """`tools.ci release --strict` is the gate: a report of another tree stops it with the reason."""
        import argparse
        import unittest.mock as mock

        self.bind(self.altered(dirty=True))
        args = argparse.Namespace(version=self.VERSION, profile="", out=self.tmp / "out", report_dir=self.report_dir,
                                  archive_root=self.tmp / "archive", archive=False, allow_findings=False,
                                  tag_message=None, strict=True)
        import contextlib
        import io

        version_module = ci_module._version_module()        # read from this tree before REPO points elsewhere
        with mock.patch.object(ci_module, "REPO", self.tree), \
                mock.patch.object(ci_module, "_version_module", return_value=version_module), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(ci_module.CiError) as caught:
                ci_module.cmd_release(args)
        self.assertIn("not clean", str(caught.exception))


class ReleaseCandidate(GitFixture):
    """TOOL-232: a release candidate vX.Y.Z-rcN of VERSION X.Y.Z (Release R1 = v1.0.0-rc1) is planned, archived
    and named by its own tag; any other spelling or another version is refused before anything is written."""

    WRONG = ("v1.0.0-rc", "v1.0.0-beta1", "v1.0.0-rc0", "v1.0.0-rc01", "1.0.0-rc1", "v1.0.1-rc1", "v0.9.0-rc1")

    def setUp(self) -> None:
        super().setUp()
        self.rules = self.tmp / "rules.yaml"
        self.rules.write_text(OPEN_RULES, encoding="utf-8")

    def test_the_tag_plan_names_the_candidate(self):
        plan = release.plan_tag(self.tree, self.VERSION, tag="v1.0.0-rc1")
        self.assertEqual("v1.0.0-rc1", plan.tag)
        self.assertEqual([], plan.blocking, plan.blocking)
        self.assertEqual(plan.command, ["git", "-C", str(self.tree), "tag", "-a", "v1.0.0-rc1", "-m",
                                        "neXtBSW 1.0.0-rc1"])
        self.assertEqual("v1.0.0", release.plan_tag(self.tree, self.VERSION).tag)   # without it: unchanged
        git(self.tree, "tag", "-a", "-m", "candidate", "v1.0.0-rc1")
        plan = release.plan_tag(self.tree, self.VERSION, tag="v1.0.0-rc1")
        self.assertTrue(plan.exists)
        self.assertTrue(any("v1.0.0-rc1 already exists" in b for b in plan.blocking), plan.blocking)

    def test_a_wrong_candidate_is_refused_without_side_effects(self):
        for wrong in self.WRONG:
            with self.subTest(tag=wrong):
                with self.assertRaises(release.ReleaseError):
                    release.plan_tag(self.tree, self.VERSION, tag=wrong)
                out = self.tmp / f"out-{wrong}"
                with self.assertRaises(release.ReleaseError):
                    release.build_release_plan(self.tree, self.VERSION, out_dir=out, profiles=[], tag=wrong,
                                               use_git=False)
                self.assertFalse(out.exists(), "a refused tag must not leave an output folder behind")

    def test_archive_zip_notes_and_publication_are_named_after_the_candidate(self):
        import unittest.mock as mock

        report_dir = self.tmp / "ci-out"
        report_dir.mkdir()
        (report_dir / "summary.md").write_text("ok\n", encoding="utf-8")
        archive_root = self.tmp / "archive"
        with mock.patch.object(release, "DISTRIBUTION_FILE", self.rules):
            plan = release.build_release_plan(self.tree, self.VERSION, out_dir=self.tmp / "out", profiles=[],
                                              report_dir=report_dir, archive_root=archive_root, use_git=False,
                                              tag="v1.0.0-rc1")
        self.assertEqual("v1.0.0-rc1", plan.tag)
        self.assertEqual("1.0.0", plan.version)
        self.assertEqual(str(archive_root / "v1.0.0-rc1"), plan.ci_report)
        self.assertEqual(archive_root / "ci-out-v1.0.0-rc1.zip", Path(plan.ci_report_zip))
        self.assertTrue((archive_root / "ci-out-v1.0.0-rc1.zip.sha256").is_file())
        self.assertEqual("RELEASE_NOTES-v1.0.0-rc1.md", Path(plan.release_notes_file).name)
        markdown = release.render_markdown(plan)
        self.assertIn("(ci-out-v1.0.0-rc1.zip)", markdown)
        self.assertIn("gh release create v1.0.0-rc1", markdown)
        self.assertIn('--title "neXtBSW 1.0.0-rc1" --prerelease', markdown)
        self.assertNotIn("ci-out-v1.0.0.zip", markdown)

    def _cmd_release(self, tag=None):
        import argparse
        import contextlib
        import io
        import unittest.mock as mock

        args = argparse.Namespace(version=None, tag=tag, profile="", out=self.tmp / "out", report_dir=None,
                                  archive_root=self.tmp / "archive", archive=False, allow_findings=False,
                                  tag_message=None, strict=False)
        version_module = ci_module._version_module()
        with mock.patch.object(ci_module, "REPO", self.tree), \
                mock.patch.object(ci_module, "_version_module", return_value=version_module), \
                mock.patch.object(release, "DISTRIBUTION_FILE", self.rules), \
                contextlib.redirect_stdout(io.StringIO()):
            ci_module.cmd_release(args)
        return json.loads((self.tmp / "out" / "release-plan.json").read_text(encoding="utf-8"))

    def test_the_command_takes_the_candidate_from_head_or_from_the_option(self):
        self.assertEqual("v1.0.0", self._cmd_release()["tag"])                      # no tag on HEAD yet
        self.assertEqual("v1.0.0-rc1", self._cmd_release("v1.0.0-rc1")["tag"])      # explicit
        git(self.tree, "tag", "-a", "-m", "candidate", "v1.0.0-rc1")                # step 6 done: from HEAD
        self.assertEqual("v1.0.0-rc1", self._cmd_release()["tag"])
        git(self.tree, "tag", "-a", "-m", "candidate", "v1.0.0-rc2")
        self.assertEqual("v1.0.0-rc2", self._cmd_release()["tag"])                  # the newest candidate
        for wrong in ("v1.0.0-rc", "v1.0.0-beta1", "v1.0.1-rc1"):
            with self.subTest(tag=wrong), self.assertRaises(ci_module.CiError):
                self._cmd_release(wrong)

    def test_the_option_is_on_the_command_line(self):
        args = ci_module.build_parser().parse_args(["release", "--tag", "v1.0.0-rc1"])
        self.assertEqual("v1.0.0-rc1", args.tag)
        self.assertIsNone(ci_module.build_parser().parse_args(["release"]).tag)


class CommandLineWiring(unittest.TestCase):
    """python -m tools.ci release ... exists and defaults to the documented values (no execution here - that is
    exercised through build_release_plan/export_profile above; this only proves the parser is wired)."""

    def test_release_subcommand_is_registered(self):
        parser = ci_module.build_parser()
        args = parser.parse_args(["release", "--version", "1.2.3"])
        self.assertIs(args.func, ci_module.cmd_release)
        self.assertEqual("1.2.3", args.version)
        self.assertEqual("open", args.profile)
        self.assertFalse(args.archive)
        self.assertEqual(ci_module.REPO / "build" / "ci-out", args.report_dir)

    def test_profiles_and_archive_and_strict_flags(self):
        parser = ci_module.build_parser()
        args = parser.parse_args(["release", "--profile", "open,licensed", "--archive", "--strict",
                                  "--allow-findings"])
        self.assertEqual("open,licensed", args.profile)
        self.assertTrue(args.archive)
        self.assertTrue(args.strict)
        self.assertTrue(args.allow_findings)


if __name__ == "__main__":
    unittest.main()
