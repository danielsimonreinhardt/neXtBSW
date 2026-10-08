"""PLAT-93 (customer-project.md N13): the commits of a customer image - neXtBSW commit and workspace commit - in the
build header of the diagnosis (tools/diaggen/buildid.py), readable as DID 0xF1F2 (workspace.version) and in the console
("info", ecu/consoleCommands identity/Identity.cpp), and never part of the configuration identity of PLAT-79.

Own test profile: a synthetic customer workspace in a temporary folder OUTSIDE the neXtBSW tree (invented project, no
neXtBSW example board), with its own git repository, without one ("nogit"), and the tree as its own workspace ("-").

Run: .venv/Scripts/python -m unittest tools/diaggen/test_buildid_workspace.py
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "tools"))

import buildid  # noqa: E402
import diaggen  # noqa: E402
from nxconfig import identity as nxidentity  # noqa: E402

GIT = ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
       "-c", "tag.gpgsign=false"]


def run(folder: Path, *args: str) -> str:
    done = subprocess.run([*GIT, "-C", str(folder), *args], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def make_repository(folder: Path, tag: str | None = None) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "nextbsw.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    run(folder, "init", "-q")
    run(folder, "add", "-A")
    run(folder, "commit", "-q", "-m", "first")
    if tag:
        run(folder, "tag", "-a", tag, "-m", tag)


class WorkspaceCommitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.tree = self.tmp / "nextbsw"            # stands for the pinned neXtBSW tree
        self.tree.mkdir()
        (self.tree / "VERSION").write_text("3.4.5\n", encoding="utf-8")
        self.workspace = self.tmp / "heatpump-ecu"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_workspace_with_repository_names_its_own_tag(self):
        make_repository(self.workspace, tag="hp-v2.1")
        self.assertEqual(buildid.workspace_text_of(self.workspace, self.tree), "hp-v2.1")
        # the tree has no repository here: the neXtBSW part says so, independent of the workspace
        self.assertEqual(buildid.text_of(self.tree), "3.4.5 nogit")

    def test_commit_after_the_tag_and_dirty_state(self):
        make_repository(self.workspace, tag="hp-v2.1")
        (self.workspace / "more.txt").write_text("x\n", encoding="utf-8")
        run(self.workspace, "add", "-A")
        run(self.workspace, "commit", "-q", "-m", "second")
        commit = run(self.workspace, "rev-parse", "--short", "HEAD")
        self.assertEqual(buildid.workspace_text_of(self.workspace, self.tree), f"hp-v2.1-1-g{commit}")
        (self.workspace / "more.txt").write_text("changed\n", encoding="utf-8")
        dirty = buildid.workspace_text_of(self.workspace, self.tree)
        self.assertRegex(dirty, rf"^hp-v2\.1-1-g{commit}-dirty\+[0-9a-f]{{8}}$")      # never looks like a release
        self.assertLessEqual(len(dirty), buildid.WORKSPACE_MAX_LENGTH)

    def test_workspace_without_repository_is_nogit(self):
        self.workspace.mkdir()
        (self.workspace / "nextbsw.yaml").write_text("schema_version: 1\n", encoding="utf-8")
        self.assertEqual(buildid.workspace_text_of(self.workspace, self.tree), "nogit")

    def test_workspace_inside_another_checkout_does_not_report_that_checkout(self):
        outer = self.tmp / "outer"
        make_repository(outer, tag="outer-v9")
        inner = outer / "unpacked-workspace"          # an unpacked archive inside somebody else's repository
        inner.mkdir()
        self.assertEqual(buildid.workspace_text_of(inner, self.tree), "nogit")

    def test_no_workspace_of_its_own(self):
        self.assertEqual(buildid.workspace_text_of(None, self.tree), "-")
        self.assertEqual(buildid.workspace_text_of(self.tree, self.tree), "-")      # project in the tree itself

    def test_header_has_all_three_values(self):
        out = self.tmp / "diag" / "DiagBuildId.h"
        self.assertTrue(buildid.write(out, "3.4.5 v3.4.5-2-gabc1234", "hp-v2.1"))
        text = out.read_text(encoding="utf-8")
        self.assertIn('#define NX_DIAG_NEXTBSW_VERSION "3.4.5 v3.4.5-2-gabc1234"\n', text)
        self.assertIn('#define NX_DIAG_NEXTBSW_COMMIT "v3.4.5-2-gabc1234"\n', text)
        self.assertIn('#define NX_DIAG_WORKSPACE_VERSION "hp-v2.1"\n', text)
        self.assertFalse(buildid.write(out, "3.4.5 v3.4.5-2-gabc1234", "hp-v2.1"))          # unchanged: not rewritten
        self.assertTrue(buildid.write(out, "3.4.5 v3.4.5-2-gabc1234", "hp-v2.2"))           # a new workspace commit is

    def test_command_line_with_workspace(self):
        make_repository(self.workspace, tag="hp-v2.1")
        for workspace, expected in ((self.workspace, "hp-v2.1"), (None, "-")):
            out = self.tmp / f"h_{expected}.h"
            args = [sys.executable, str(HERE / "buildid.py"), "-o", str(out), "--tree", str(self.tree)]
            if workspace is not None:
                args += ["--workspace", str(workspace)]
            result = subprocess.run(args, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f'NX_DIAG_WORKSPACE_VERSION "{expected}"', out.read_text(encoding="utf-8"))
        # without .git the whole command line gives "nogit"
        bare = self.tmp / "bare"
        bare.mkdir()
        out = self.tmp / "bare.h"
        subprocess.run([sys.executable, str(HERE / "buildid.py"), "-o", str(out), "--tree", str(self.tree),
                        "--workspace", str(bare)], check=True, capture_output=True)
        self.assertIn('NX_DIAG_WORKSPACE_VERSION "nogit"', out.read_text(encoding="utf-8"))

    def test_text_is_printable_ascii_and_fits(self):
        text = buildid.header("1.0.0 x", 'a"b\\cä' + "z" * 80)
        line = [ln for ln in text.splitlines() if "WORKSPACE_VERSION" in ln][0]
        self.assertNotIn("ä", line)
        self.assertEqual(line.count('"'), 2)

    def test_the_commit_is_not_an_input_of_the_image_identity(self):
        # the identity of PLAT-79 lists its entries; none of them carries a commit, a workspace or a describe text
        fields = nxidentity.Identity(version="3.4.5", configuration=(), generators=()).fields()
        for name, value, _ in fields:
            self.assertNotIn("workspace", name)
            self.assertNotIn("commit", name)
            self.assertNotRegex(value, r"-g[0-9a-f]{7}")
        # the generator sources of the identity do not include buildid.py's output, only its tree files
        self.assertFalse(any("DiagBuildId" in folder for folder, _, _ in nxidentity.GENERATOR_SOURCES))


class StandardDescriptionTest(unittest.TestCase):
    def test_f1f2_is_in_the_standard_description_and_nowhere_else(self):
        data = yaml.safe_load((HERE / "standard_diag.yaml").read_text(encoding="utf-8"))
        ids = [d["identifier"] for d in data["data_identifiers"]]
        self.assertEqual(ids.count(0xF1F2), 1)
        entry = [d for d in data["data_identifiers"] if d["identifier"] == 0xF1F2][0]
        self.assertEqual(entry["data"][0]["source"], {"bsw": "workspace.version"})
        self.assertEqual(entry["data"][0]["length_bytes"], buildid.WORKSPACE_MAX_LENGTH)
        self.assertEqual(len(ids), len(set(ids)))                       # no identifier twice: no collision
        for path in (REPO / "bootloader").rglob("*"):                  # nor an identifier of the bootloader
            if path.is_file() and path.suffix in (".c", ".h", ".py", ".yaml"):
                self.assertNotIn("0XF1F2", path.read_text(encoding="utf-8", errors="replace").upper(), str(path))

    def test_stack_value_is_text_and_read_only(self):
        spec = diaggen.STACK_VALUES["workspace.version"]
        self.assertEqual(spec["types"], ("ascii",))
        self.assertFalse(spec["write"])

    def test_the_wiring(self):
        cmake = (REPO / "ecu" / "uds" / "CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn('--workspace "${NX_WORKSPACE}"', cmake)
        self.assertIn("add_dependencies(consoleCommands diagBuildId)", cmake)
        console = (REPO / "ecu" / "consoleCommands" / "src" / "identity" / "Identity.cpp").read_text(encoding="utf-8")
        self.assertIn("<diag/DiagBuildId.h>", console)
        self.assertIn("NX_DIAG_WORKSPACE_VERSION", console)
        system = (REPO / "ecu" / "uds" / "src" / "systems" / "DiagSystem.cpp").read_text(encoding="utf-8")
        self.assertIn("NX_DIAG_WORKSPACE_VERSION", system)


if __name__ == "__main__":
    unittest.main()
