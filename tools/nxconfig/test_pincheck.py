"""Host tests of the release strictness of the workspace pin (TOOL-65, customer-project.md gap N10).

Everything runs against **synthetic git repositories in %TEMP%**, never against this repository and never against
a board: a tiny invented neXtBSW tree (a `VERSION` file and a `tools/nxconfig` folder - the two things
`workspace.is_tree` asks for) under an invented customer workspace `acme-ctl`, on an invented version. The cases
are exactly the states a pin can be in:

  annotated tag on master | lightweight tag | no tag at all (development pin) | a different tag at HEAD |
  the tag somewhere else in the history | the tag on a side branch off master | no master ref |
  dirty tree | no git checkout at all (source: archive) | overlays of another version or missing.

Because every repository is built here from nothing, the test says what git state it is describing - it does not
depend on the state this repository happens to be in.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from nxconfig import __main__ as cli  # noqa: E402
from nxconfig import pincheck  # noqa: E402

VERSION = "1.2.3"
TAG = "v1.2.3"
GIT = shutil.which("git")

#: `git commit` and `git tag -a` refuse to run without an identity, and a test must not depend on the one this
#: workstation happens to have.
IDENTITY = ["-c", "user.name=neXtBSW Test", "-c", "user.email=test@example.invalid",
            "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false"]


def git(repo: Path, *args: str) -> str:
    done = subprocess.run([GIT, "-C", str(repo), *IDENTITY, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60)
    if done.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} in {repo}: {done.returncode} {done.stderr.strip()}")
    return done.stdout.strip()


@unittest.skipUnless(GIT, "git is not installed")
class PinBase(unittest.TestCase):
    """A workspace `acme-ctl` in %TEMP% with an invented neXtBSW tree `nextbsw/` under it."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nxpin-test-")).resolve()
        self.ws = self.tmp / "acme-ctl"
        self.tree = self.ws / "nextbsw"
        self.make_tree(self.tree, VERSION)
        self.pin()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------------ fixtures

    def make_tree(self, folder: Path, version: str, with_git: bool = True) -> Path:
        """An invented neXtBSW tree: VERSION plus a tools/nxconfig folder, optionally as a git repository."""
        (folder / "tools" / "nxconfig").mkdir(parents=True, exist_ok=True)
        (folder / "VERSION").write_text(f"{version}\n", encoding="utf-8")
        (folder / "tools" / "nxconfig" / "marker.txt").write_text("synthetic tree (test data)\n", encoding="utf-8")
        (folder / "README.md").write_text("invented tree for the pin check test\n", encoding="utf-8")
        if with_git:
            git(folder, "init", "-q")
            git(folder, "checkout", "-q", "-B", "master")
            git(folder, "add", "-A")
            git(folder, "commit", "-q", "-m", "synthetic release")
        return folder

    def tag(self, folder: Path | None = None, name: str = TAG, annotated: bool = True) -> None:
        folder = folder or self.tree
        git(folder, "tag", "-a", name, "-m", f"release {name}") if annotated else git(folder, "tag", name)

    def detach(self, folder: Path | None = None, name: str = TAG) -> None:
        """Check the tag out the way a git submodule is checked out: detached HEAD."""
        git(folder or self.tree, "checkout", "-q", name)

    def commit(self, folder: Path | None = None, text: str = "more\n") -> None:
        folder = folder or self.tree
        (folder / "README.md").write_text(text, encoding="utf-8")
        git(folder, "add", "-A")
        git(folder, "commit", "-q", "-m", "work after the release")

    def dirty(self, folder: Path | None = None) -> None:
        (folder or self.tree).joinpath("README.md").write_text("local change\n", encoding="utf-8")

    def pin(self, version: str = VERSION, path: str = "nextbsw", source: str = "submodule",
            overlays: str = "", extra: str = "") -> Path:
        file = self.ws / "nextbsw.yaml"
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("# synthetic workspace pin (test data)\n"
                        "schema_version: 1\n"
                        "nextbsw:\n"
                        f"  version: {version}\n"
                        f"  source: {source}\n"
                        f"  path: {path}\n"
                        "  profile: open\n"
                        f"{extra}"
                        "search:\n"
                        "  mcudb: [mcudb]\n"
                        "  modules: [libs]\n"
                        f"{overlays}", encoding="utf-8")
        return file

    # ------------------------------------------------------------------ helpers

    def check(self, release: bool = False) -> pincheck.PinStatus:
        return pincheck.check(self.ws / "nextbsw.yaml", release=release)

    def codes(self, status: pincheck.PinStatus, severity: str | None = None) -> list[str]:
        return [f.code for f in status.findings if severity is None or f.severity == severity]


class ReleasedPin(PinBase):
    """The state a customer release is built from."""

    def test_annotated_tag_on_master_and_clean_is_green(self):
        self.tag()
        self.detach()
        status = self.check()
        self.assertEqual(self.codes(status), ["ok"], status.text)
        self.assertTrue(status.ok)
        self.assertTrue(status.usable)
        self.assertFalse(status.development)
        self.assertEqual(status.tag, TAG)
        self.assertEqual(status.version, VERSION)

    def test_release_strictness_changes_nothing_for_a_released_pin(self):
        self.tag()
        self.detach()
        self.assertEqual(self.codes(self.check(release=True)), ["ok"])

    def test_headline_names_version_and_tag(self):
        self.tag()
        self.detach()
        self.assertEqual(self.check().headline, f"Pin {VERSION} = Tag {TAG}")


class DevelopmentPin(PinBase):
    """No tag at all - the state every customer starts in. Warning in daily work, error for a release."""

    def test_without_a_tag_it_is_only_a_warning(self):
        status = self.check()
        self.assertEqual(self.codes(status), ["no-tag"])
        self.assertEqual(self.codes(status, "warning"), ["no-tag"])
        self.assertEqual(status.errors, ())
        self.assertTrue(status.usable, "daily work must not be blocked by a development pin")
        self.assertTrue(status.development)
        self.assertFalse(status.ok)

    def test_with_release_strictness_it_is_an_error(self):
        status = self.check(release=True)
        self.assertEqual(self.codes(status, "error"), ["no-tag"])
        self.assertFalse(status.usable)

    def test_the_warning_says_which_tag_to_check_out(self):
        text = self.check().findings[0].text
        self.assertIn(TAG, text)
        self.assertIn("Entwicklungsstand", text)

    def test_a_dirty_development_pin_is_an_error_anyway(self):
        self.dirty()
        status = self.check()
        self.assertIn("dirty", self.codes(status, "error"))
        self.assertFalse(status.usable)


class WrongTag(PinBase):
    """Everything that looks like a tag but is not the released baseline the pin names."""

    def test_lightweight_tag_is_an_error(self):
        self.tag(annotated=False)
        self.detach()
        status = self.check()
        self.assertIn("tag-lightweight", self.codes(status, "error"))
        self.assertIn("annotierten Tag", status.text)

    def test_another_release_tag_at_head_is_an_error(self):
        self.tag(name="v0.9.0")
        self.detach(name="v0.9.0")
        status = self.check()
        self.assertEqual(self.codes(status, "error"), ["tag-mismatch"])
        self.assertIn("v0.9.0", status.text)
        self.assertFalse(status.development, "a tagged tree is no development pin")

    def test_the_tag_further_back_in_the_history_is_an_error(self):
        self.tag()
        self.commit()                               # HEAD moved past the tag
        status = self.check()
        self.assertEqual(self.codes(status, "error"), ["tag-elsewhere"])
        self.assertIn(TAG, status.text)

    def test_a_tag_off_master_is_an_error(self):
        git(self.tree, "checkout", "-q", "-b", "side")
        self.commit(text="side branch\n")
        self.tag()
        self.detach()
        status = self.check()
        self.assertEqual(self.codes(status, "error"), ["tag-unreachable"])
        self.assertIn("master", status.text)

    def test_without_a_master_ref_it_is_a_warning_and_a_release_error(self):
        self.tag()
        self.detach()
        git(self.tree, "branch", "-q", "-D", "master")
        self.assertEqual(self.codes(self.check(), "warning"), ["master-unknown"])
        self.assertEqual(self.codes(self.check(release=True), "error"), ["master-unknown"])


class SafetyReleaseSignature(PinBase):
    """cm-plan.md G3, decision Q-CM2: a tag whose message says 'safety release' must carry a signature.

    Only the PRESENCE of a signature is judged (git needs the signer's allowed-signers file to verify one, which
    is `git tag -v`, step 7 of cm-plan.md 5.2). The signed tag is therefore built by hand: a tag object with a
    signature block appended to the message, exactly the shape `git tag -s` produces - no key, no ssh-keygen."""

    FAKE_SIGNATURE = ("-----BEGIN SSH SIGNATURE-----\n"
                      "U1NIU0lH (synthetic test data, not a real signature)\n"
                      "-----END SSH SIGNATURE-----\n")

    def tag_with(self, message: str, signed: bool = False, name: str = TAG, folder: Path | None = None) -> None:
        folder = folder or self.tree
        head = git(folder, "rev-parse", "HEAD")
        body = (f"object {head}\ntype commit\ntag {name}\n"
                f"tagger neXtBSW Test <test@example.invalid> 1790000000 +0000\n\n{message}\n")
        if signed:
            body += self.FAKE_SIGNATURE
        done = subprocess.run([GIT, "-C", str(folder), "hash-object", "-t", "tag", "-w", "--stdin"],
                              input=body.encode("utf-8"), capture_output=True, timeout=60)   # bytes: no CRLF
        self.assertEqual(done.returncode, 0, done.stderr)
        git(folder, "update-ref", f"refs/tags/{name}", done.stdout.decode().strip())
        self.detach(folder, name=name)

    def test_unsigned_safety_release_is_a_warning_and_a_release_error(self):
        self.tag_with("neXtBSW 1.2.3\n\nStatus: safety release")
        status = self.check()
        self.assertEqual(self.codes(status), ["tag-unsigned"])
        self.assertEqual(self.codes(status, "warning"), ["tag-unsigned"])
        finding = status.findings[0]
        self.assertEqual(finding.path, ("nextbsw", "path"))
        self.assertIn("nextbsw.yaml", str(finding))
        self.assertIn("tag -v", finding.text)
        release = self.check(release=True)
        self.assertEqual(self.codes(release, "error"), ["tag-unsigned"])
        self.assertFalse(release.ok)

    def test_signed_safety_release_is_green(self):
        self.tag_with("neXtBSW 1.2.3\n\nStatus: safety release", signed=True)
        for release in (False, True):
            status = self.check(release=release)
            self.assertEqual(self.codes(status), ["ok"], status.text)
            self.assertEqual(status.tag, TAG)

    def test_development_release_needs_no_signature(self):
        self.tag_with("neXtBSW 1.2.3\n\nStatus: development release")
        self.assertEqual(self.codes(self.check(release=True)), ["ok"])

    def test_plain_annotated_tag_needs_no_signature(self):
        self.tag()
        self.detach()
        self.assertEqual(self.codes(self.check(release=True)), ["ok"])

    def test_wording_is_case_and_separator_tolerant(self):
        for wording in ("Safety Release", "SAFETY RELEASE", "safety-release", "safety_release"):
            with self.subTest(wording=wording):
                git(self.tree, "checkout", "-q", "master")
                self.tag_with(f"neXtBSW 1.2.3\n\nStatus: {wording}")
                self.assertEqual(self.codes(self.check(release=True), "error"), ["tag-unsigned"])
                git(self.tree, "checkout", "-q", "master")
                git(self.tree, "tag", "-d", TAG)

    def test_a_negated_mention_is_no_claim(self):
        self.tag_with("neXtBSW 1.2.3\n\nStatus: development release, not a safety release")
        self.assertEqual(self.codes(self.check(release=True)), ["ok"])

    def test_message_facts(self):
        head = "object x\ntype commit\ntag t\ntagger a <b> 1 +0000\n\n"
        facts = pincheck.tag_message_facts
        self.assertEqual(facts(head + "safety release"), (True, False))
        self.assertEqual(facts(head + "Status: development release"), (False, False))
        self.assertEqual(facts(head + "safety release\n-----BEGIN PGP SIGNATURE-----\nx"), (True, True))
        # text inside the signature block is no message
        self.assertEqual(facts(head + "release\n-----BEGIN SSH SIGNATURE-----\nsafety release\n"
                                      "-----END SSH SIGNATURE-----\n"), (False, True))

    def test_overlay_with_unsigned_safety_release_is_an_error(self):
        self.tag()
        self.detach()
        over = self.make_tree(self.ws / "nextbsw-licensed", VERSION)
        self.tag_with("safety release", folder=over)
        self.pin(overlays="  overlays:\n    - path: nextbsw-licensed\n      version: 1.2.3\n")
        status = self.check(release=True)
        self.assertEqual(self.codes(status, "error"), ["tag-unsigned"], status.text)
        self.assertIn("nextbsw-licensed", status.findings[0].text)


class DirtyTree(PinBase):
    def test_a_dirty_release_tree_is_an_error(self):
        self.tag()
        self.detach()
        self.dirty()
        status = self.check()
        self.assertEqual(self.codes(status, "error"), ["dirty"])
        self.assertIn("schmutzig", status.text)

    def test_an_untracked_file_makes_the_tree_dirty(self):
        self.tag()
        self.detach()
        (self.tree / "scratch.txt").write_text("left over\n", encoding="utf-8")
        self.assertIn("dirty", self.codes(self.check(), "error"))


class NoGitCheckout(PinBase):
    """A tree that was unpacked instead of cloned."""

    def setUp(self):
        super().setUp()
        shutil.rmtree(self.tree, ignore_errors=True)
        self.make_tree(self.tree, VERSION, with_git=False)

    def test_source_archive_is_no_finding(self):
        self.pin(source="archive", extra="  url: https://example.invalid/nextbsw-1.2.3.tar.gz\n"
                                         f"  sha256: {'0' * 64}\n")
        status = self.check(release=True)
        self.assertEqual(self.codes(status), ["no-git"])
        self.assertEqual(self.codes(status, "ok"), ["no-git"])
        self.assertTrue(status.ok, "an archive pin is judged by its sha256, not by a tag")

    def test_source_submodule_without_git_warns_and_fails_a_release(self):
        self.assertEqual(self.codes(self.check(), "warning"), ["no-git"])
        self.assertEqual(self.codes(self.check(release=True), "error"), ["no-git"])

    def test_git_that_cannot_be_started_is_no_crash(self):
        status = pincheck.check(self.ws / "nextbsw.yaml", run=lambda *a, **k: (-1, "git not found"))
        self.assertEqual(self.codes(status), ["no-git"])


class ArchiveUnderCustomerRepo(PinBase):
    """TOOL-86 finding: `nextbsw/` unpacked from a release archive has no `.git` of its own, but the customer
    workspace around it does. A plain `git -C nextbsw rev-parse HEAD` still succeeds then - git walks up and
    silently answers with the CUSTOMER repository's HEAD and tags, so the archive was judged as "steht auf keinem
    Release-Tag" and `tools/ci run --release` refused every archive workspace. `facts()` must notice that the
    repository git actually found is not `nextbsw/` itself and treat the tree as `is_git=False`, exactly as it
    would without any enclosing git at all."""

    def setUp(self):
        super().setUp()
        shutil.rmtree(self.tree, ignore_errors=True)
        self.make_tree(self.tree, VERSION, with_git=False)            # the archive: no git of its own
        git(self.ws, "init", "-q")                                    # the CUSTOMER repository around it
        git(self.ws, "checkout", "-q", "-B", "master")
        (self.ws / "README.md").write_text("customer repository\n", encoding="utf-8")
        git(self.ws, "add", "-A")
        git(self.ws, "commit", "-q", "-m", "customer work")
        self.tag(self.ws, name="v9.9.9")                              # a release of the CUSTOMER, not of neXtBSW
        self.pin(source="archive", extra="  url: https://example.invalid/nextbsw-1.2.3.tar.gz\n"
                                         f"  sha256: {'0' * 64}\n")

    def test_facts_do_not_borrow_the_enclosing_repositorys_head(self):
        found = pincheck.facts(self.tree, pincheck.nxversion.tag_of(VERSION))
        self.assertFalse(found.is_git)

    def test_archive_is_judged_by_its_checksum_not_the_customer_repositorys_tag(self):
        status = self.check(release=True)
        self.assertEqual(self.codes(status), ["no-git"])
        self.assertEqual(self.codes(status, "ok"), ["no-git"])
        self.assertTrue(status.ok, status.text)
        self.assertNotIn("v9.9.9", status.text)

    def test_release_run_of_the_archive_tree_alone_is_unaffected_too(self):
        status = pincheck.check_tree(self.tree, source="archive", release=True)
        self.assertEqual([f.code for f in status.findings], ["no-git"])
        self.assertTrue(status.ok, status.text)


class Identity(PinBase):
    """The checks that do not need git at all - they keep working when the tree is not where it should be."""

    def test_missing_tree_is_an_error(self):
        self.pin(path="nextbsw-elsewhere")
        status = self.check()
        self.assertEqual(self.codes(status, "error"), ["tree-missing"])

    def test_version_of_the_tree_must_equal_the_pin(self):
        self.pin(version="9.9.9")
        status = self.check()
        self.assertIn("tree-version", self.codes(status, "error"))
        self.assertIn("9.9.9", status.text)
        self.assertIn(VERSION, status.text)

    def test_a_version_that_is_no_semver_is_an_error(self):
        self.pin(version="1.2")
        self.assertEqual(self.codes(self.check(), "error"), ["version-missing"])

    def test_an_unreadable_pin_is_one_error(self):
        (self.ws / "nextbsw.yaml").write_text("nextbsw: [oh, dear\n", encoding="utf-8")
        status = self.check()
        self.assertEqual(self.codes(status, "error"), ["pin-unreadable"])


class Overlays(PinBase):
    """Licensed overlay trees (customer-project.md 5.8, N16 groundwork): same release, same provenance."""

    def overlay(self, version: str = VERSION, path: str = "nextbsw-licensed") -> str:
        return f"  overlays:\n    - path: {path}\n      version: {version}\n"

    def test_an_overlay_of_the_same_version_with_its_tag_is_green(self):
        self.tag()
        self.detach()
        over = self.make_tree(self.ws / "nextbsw-licensed", VERSION)
        self.tag(over)
        self.detach(over)
        self.pin(overlays=self.overlay())
        self.assertEqual(self.codes(self.check()), ["ok"], self.check().text)

    def test_an_overlay_of_another_version_is_an_error(self):
        self.tag()
        self.detach()
        over = self.make_tree(self.ws / "nextbsw-licensed", "1.2.4")
        self.tag(over, name="v1.2.4")
        self.detach(over, name="v1.2.4")
        self.pin(overlays=self.overlay(version="1.2.4"))
        status = self.check()
        self.assertIn("overlay-version", self.codes(status, "error"))
        self.assertIn("1.2.4", status.text)

    def test_a_missing_overlay_folder_is_an_error(self):
        self.tag()
        self.detach()
        self.pin(overlays=self.overlay())
        self.assertEqual(self.codes(self.check(), "error"), ["overlay-missing"])

    def test_an_overlay_is_checked_like_the_pinned_tree(self):
        self.tag()
        self.detach()
        self.make_tree(self.ws / "nextbsw-licensed", VERSION)          # committed, but never tagged
        self.pin(overlays=self.overlay())
        status = self.check()
        self.assertEqual(self.codes(status, "warning"), ["no-tag"])
        self.assertIn("Overlay nextbsw-licensed", status.text)
        self.assertEqual(self.codes(self.check(release=True), "error"), ["no-tag"])

    def test_the_overlay_finding_names_its_yaml_path(self):
        self.tag()
        self.detach()
        self.pin(overlays=self.overlay(version="9.9.9"))
        finding = next(f for f in self.check().findings if f.code == "overlay-version")
        self.assertEqual(finding.yaml_path, "search.overlays[0].version")


class MessagePlaces(PinBase):
    """Every message names file, line and YAML path, and says what to do (review question 8)."""

    def line_of(self, needle: str) -> int:
        text = (self.ws / "nextbsw.yaml").read_text(encoding="utf-8").splitlines()
        return next(i + 1 for i, line in enumerate(text) if needle in line)

    def test_version_finding_points_at_nextbsw_version(self):
        self.pin(version="9.9.9")
        finding = next(f for f in self.check().findings if f.code == "tree-version")
        self.assertEqual(finding.yaml_path, "nextbsw.version")
        self.assertEqual(finding.line, self.line_of("version: 9.9.9"))
        self.assertEqual(finding.file.name, "nextbsw.yaml")
        self.assertEqual(finding.where(), f"nextbsw.yaml:{finding.line} (nextbsw.version)")

    def test_tree_findings_point_at_nextbsw_path(self):
        finding = next(f for f in self.check().findings if f.code == "no-tag")
        self.assertEqual(finding.yaml_path, "nextbsw.path")
        self.assertEqual(finding.line, self.line_of("path: nextbsw"))

    def test_every_finding_carries_file_path_and_line(self):
        self.pin(overlays="  overlays:\n    - path: nextbsw-licensed\n      version: 9.9.9\n")
        for finding in self.check().findings:
            self.assertIsNotNone(finding.file, finding.text)
            self.assertTrue(finding.path, finding.text)
            self.assertIsNotNone(finding.line, f"{finding.code}: no line")


class LineNumbers(unittest.TestCase):
    """`line_of_path` replaces ruamel here (tools/ci loads this module without pydantic)."""

    TEXT = ("# comment\n"                   # 1
            "schema_version: 1\n"           # 2
            "nextbsw:\n"                    # 3
            "  version: 1.2.3\n"            # 4
            "  source: submodule\n"         # 5
            "  path: nextbsw\n"             # 6
            "search:\n"                     # 7
            "  mcudb: [mcudb]\n"            # 8
            "  overlays:\n"                 # 9
            "    - path: a\n"               # 10
            "      version: 1.2.3\n"        # 11
            "    - path: b\n"               # 12
            "      version: 1.2.4\n")       # 13

    def line(self, path, text=None):
        return pincheck.line_of_path(text if text is not None else self.TEXT, path)

    def test_nested_key(self):
        self.assertEqual(self.line(("nextbsw", "version")), 4)
        self.assertEqual(self.line(("nextbsw", "path")), 6)
        self.assertEqual(self.line(("nextbsw",)), 3)

    def test_sequence_items_and_their_keys(self):
        self.assertEqual(self.line(("search", "overlays")), 9)
        self.assertEqual(self.line(("search", "overlays", 0)), 10)
        self.assertEqual(self.line(("search", "overlays", 0, "version")), 11)
        self.assertEqual(self.line(("search", "overlays", 1, "path")), 12)
        self.assertEqual(self.line(("search", "overlays", 1, "version")), 13)

    def test_unknown_key_gives_the_deepest_one_that_exists(self):
        self.assertEqual(self.line(("nextbsw", "profile")), 3)
        self.assertIsNone(self.line(("does", "not", "exist")))

    def test_flow_style_gives_the_line_of_the_flow_node(self):
        text = "search:\n  overlays: [{path: a, version: 1.2.3}]\n"
        self.assertEqual(self.line(("search", "overlays", 0, "version"), text), 2)

    def test_a_sequence_at_the_column_of_its_key(self):
        text = "overlays:\n- path: a\n  version: 1.2.3\n- path: b\n  version: 1.2.4\n"
        self.assertEqual(self.line(("overlays", 0, "version"), text), 3)
        self.assertEqual(self.line(("overlays", 1, "path"), text), 4)

    def test_no_text_is_no_crash(self):
        self.assertIsNone(self.line(("nextbsw", "version"), ""))


class Discovery(PinBase):
    """`check_workspace` walks up like git finds .git - and says "nothing to check" outside a workspace."""

    def test_finds_the_pin_above_a_project(self):
        project = self.ws / "projects" / "acme_main"
        project.mkdir(parents=True)
        (project / "project.yaml").write_text("schema_version: 1\n", encoding="utf-8")
        status = pincheck.check_workspace(project / "project.yaml")
        self.assertIsNotNone(status)
        self.assertEqual(status.version, VERSION)

    def test_a_folder_without_a_pin_has_nothing_to_check(self):
        plain = self.tmp / "somewhere"
        plain.mkdir()
        self.assertIsNone(pincheck.check_workspace(plain))

    def test_the_walk_stops_at_a_nextbsw_tree(self):
        inside = self.tree / "projects" / "demo"
        inside.mkdir(parents=True)
        self.assertIsNone(pincheck.check_workspace(inside),
                          "a project of the tree itself is the development case, not a pinned workspace")


class TreeOnly(PinBase):
    """`check_tree` - the same rule without a pin file, for the release run of neXtBSW itself."""

    def test_a_tagged_clean_tree_is_green(self):
        self.tag()
        self.detach()
        status = pincheck.check_tree(self.tree, release=True)
        self.assertEqual([f.code for f in status.findings], ["ok"], status.text)
        self.assertEqual(status.version, VERSION)

    def test_an_untagged_tree_fails_a_release_run(self):
        self.assertEqual([f.code for f in pincheck.check_tree(self.tree, release=True).errors], ["no-tag"])
        self.assertEqual([f.code for f in pincheck.check_tree(self.tree).warnings], ["no-tag"])


class CommandLine(PinBase):
    """`nxconfig pin` - the Qt-free entry point the Studio and a customer share."""

    def run_cli(self, *argv) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["pin", *argv])
        return code, out.getvalue() + err.getvalue()

    def test_a_development_pin_is_green_without_release(self):
        code, text = self.run_cli(str(self.ws))
        self.assertEqual(code, 0, text)
        self.assertIn("no-tag", text)
        self.assertIn("warning", text)

    def test_release_makes_it_red(self):
        code, text = self.run_cli(str(self.ws), "--release")
        self.assertEqual(code, 1, text)
        self.assertIn("error", text)

    def test_a_released_pin_is_green(self):
        self.tag()
        self.detach()
        code, text = self.run_cli(str(self.ws), "--release")
        self.assertEqual(code, 0, text)
        self.assertIn(TAG, text)

    def test_json_carries_code_path_and_line(self):
        import json

        code, text = self.run_cli(str(self.ws), "--json")
        payload = json.loads(text)
        self.assertEqual(code, 0)
        self.assertEqual(payload["version"], VERSION)
        self.assertTrue(payload["development"])
        finding = payload["findings"][0]
        self.assertEqual(finding["code"], "no-tag")
        self.assertEqual(finding["path"], "nextbsw.path")
        self.assertIsNotNone(finding["line"])

    def test_outside_a_workspace_nothing_is_checked(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        code, text = self.run_cli(str(plain))
        self.assertEqual(code, 0)
        self.assertIn("nothing to check", text)


class SchemaAcceptsOverlays(unittest.TestCase):
    """The pin schema has to accept what the check reads - otherwise `nxconfig check` rejects the file first."""

    def test_overlays_are_part_of_the_pin(self):
        from nxconfig.model import WorkspacePin

        pin = WorkspacePin.model_validate({
            "schema_version": 1,
            "nextbsw": {"version": VERSION, "source": "submodule", "path": "nextbsw"},
            "search": {"overlays": [{"path": "nextbsw-licensed", "version": VERSION}]}})
        self.assertEqual(pin.search.overlays[0].path, "nextbsw-licensed")
        self.assertEqual(pin.search.overlays[0].version, VERSION)

    def test_an_overlay_needs_path_and_a_version(self):
        from pydantic import ValidationError

        from nxconfig.model import WorkspacePin

        for overlay in ({"path": "x"}, {"version": VERSION}, {"path": "x", "version": "1.2"},
                        {"path": "x", "version": VERSION, "profile": "open"}):
            with self.assertRaises(ValidationError, msg=overlay):
                WorkspacePin.model_validate({
                    "schema_version": 1,
                    "nextbsw": {"version": VERSION, "source": "submodule", "path": "nextbsw"},
                    "search": {"overlays": [overlay]}})

    def test_a_pin_without_overlays_is_unchanged(self):
        from nxconfig.model import WorkspacePin

        pin = WorkspacePin.model_validate({
            "schema_version": 1,
            "nextbsw": {"version": VERSION, "source": "submodule", "path": "nextbsw"}})
        self.assertEqual(pin.search.overlays, [])


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    unittest.main()
