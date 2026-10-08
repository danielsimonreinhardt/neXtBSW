"""Host test of `nxconfig new` in a customer workspace (TOOL-216, gap N20 of doc/architecture/customer-project.md).

Finding of the DOC-36 run with the release tag v0.2.0: `nxconfig new --board <board of the workspace>` looked only
into <tree>/boards, and `nxconfig new --copy` wrote project.board as an absolute path. Test profile: the synthetic
workspace of test_workspace.py in %TEMP% (own derivative ACMEMCU64, own board acme_ctl_b1, own project acme_main,
own module), pinning the running tree by a relative path - never a neXtBSW board as the yardstick.
"""

import contextlib
import dataclasses
import io
import os
import sys
import unittest
from pathlib import Path

from ruamel.yaml import YAML

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import nxconfig  # noqa: E402
from nxconfig import __main__ as cli  # noqa: E402
from nxconfig import config as nxcfg  # noqa: E402
from nxconfig import newproject as np  # noqa: E402
from nxconfig import workspace as nxws  # noqa: E402
from test_workspace import BOARD, PROFILES, PROJECT, REPO, WorkspaceBase  # noqa: E402


def load(file: Path) -> dict:
    return YAML(typ="safe").load(file.read_text(encoding="utf-8"))


class NewInWorkspace(WorkspaceBase):
    def workspace(self) -> nxws.Workspace:
        reporter = nxcfg.Reporter()
        found, ws = nxws.discover(self.ws, reporter, REPO)
        self.assertTrue(found and ws is not None, [str(m) for m in reporter.messages])
        return ws

    def run_cli(self, *argv, cwd=None):
        out, err = io.StringIO(), io.StringIO()
        old = Path.cwd()
        os.chdir(cwd or self.ws)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    code = cli.main(list(argv))
                except SystemExit as e:
                    code = e.code
        finally:
            os.chdir(old)
        return code, out.getvalue(), err.getvalue()

    def test_board_of_the_workspace_is_found(self):
        """The board is only in <workspace>/boards: the lookup of the tree alone does not know it (the finding)."""
        self.assertNotIn(BOARD, [b.name for b in np.list_boards(REPO)])
        ws = self.workspace()
        names = [b.name for b in np.list_boards(ws.root, workspace=ws)]
        self.assertIn(BOARD, names)
        # tree boards stay available behind it
        self.assertTrue(set(b.name for b in np.list_boards(REPO)) <= set(names))

    def test_empty_project_on_a_board_of_the_workspace(self):
        ws = self.workspace()
        plan = np.plan_empty("fresh", BOARD, **np.workspace_arguments(ws))
        self.assertEqual(plan.target, (self.ws / "projects" / "fresh").resolve())
        result = np.create(plan)
        project = load(plan.project_file)["project"]
        self.assertEqual(project["board"], f"../../boards/{BOARD}")        # relative, below the workspace
        self.assertEqual([str(m) for m in result.errors], [])

    def test_cli_empty_project_and_list(self):
        code, out, err = self.run_cli("new", "--list")
        self.assertEqual(code, 0, err)
        self.assertIn(BOARD, out)
        self.assertIn(PROJECT, out)
        code, out, err = self.run_cli("new", "cli_fresh", "--board", BOARD)
        self.assertEqual(code, 0, out + err)
        file = self.ws / "projects" / "cli_fresh" / "project.yaml"
        self.assertEqual(load(file)["project"]["board"], f"../../boards/{BOARD}")
        # from a subfolder of the workspace (the walk-up of the pin) and with --workspace from anywhere
        code, out, err = self.run_cli("new", "cli_two", "--board", BOARD, cwd=self.ws / "projects")
        self.assertEqual(code, 0, out + err)
        code, out, err = self.run_cli("new", "cli_three", "--board", BOARD, "--workspace", str(self.ws),
                                      cwd=self.tmp)
        self.assertEqual(code, 0, out + err)
        self.assertTrue((self.ws / "projects" / "cli_three" / "project.yaml").is_file())

    def test_unknown_board_names_the_places(self):
        code, out, err = self.run_cli("new", "nope", "--board", "no_such_board")
        self.assertEqual(code, 1)
        self.assertIn(str(self.ws / "boards"), err)
        self.assertIn(str(REPO / "boards"), err)
        self.assertFalse((self.ws / "projects" / "nope").exists())

    def test_name_taken_by_a_project_of_the_workspace(self):
        code, out, err = self.run_cli("new", PROJECT, "--board", BOARD)
        self.assertEqual(code, 1)
        self.assertIn("already used", err)

    def test_copy_writes_a_relative_board(self):
        """The finding: the copy wrote project.board as an absolute path."""
        code, out, err = self.run_cli("new", "copied", "--copy", PROJECT)
        self.assertEqual(code, 0, out + err)
        board = load(self.ws / "projects" / "copied" / "project.yaml")["project"]["board"]
        self.assertEqual(board, f"../../boards/{BOARD}")
        self.assertFalse(Path(board).is_absolute())

    def test_copy_of_a_tree_project_gets_a_relative_board_into_the_tree(self):
        """A project of the pinned tree copied into the workspace: the board of the tree is reached relative.

        The pinned tree lies below the workspace root as a submodule does (a tree of its own with one board and one
        project, planning only - the running tree of the test is elsewhere)."""
        tree = self.ws / "nextbsw"
        board_text = (PROFILES / "boards" / "testboard" / "board.yaml").read_text(encoding="utf-8")
        project_text = (PROFILES / "projects" / "testproject" / "project.yaml").read_text(encoding="utf-8")
        self.write("boards/treeboard/board.yaml", board_text.replace("testboard", "treeboard"), root=tree)
        self.write("projects/treeproj/project.yaml",
                   project_text.replace("testproject", "treeproj").replace("testboard", "treeboard"), root=tree)
        self.write("VERSION", self.version + "\n", root=tree)
        (tree / "tools" / "nxconfig").mkdir(parents=True)
        ws = dataclasses.replace(self.workspace(), tree=tree)
        plan = np.plan_copy("tree_copy", "treeproj", **np.workspace_arguments(ws))
        text = next(f.text for f in plan.files if f.path == "project.yaml")
        board = YAML(typ="safe").load(text)["project"]["board"]
        self.assertEqual(board, "../../nextbsw/boards/treeboard")
        self.assertEqual((plan.target / board).resolve(), (tree / "boards" / "treeboard").resolve())

    def test_outside_a_workspace_nothing_changes(self):
        """Without nextbsw.yaml above the folder the tree is used, as before (list shows no workspace board)."""
        code, out, err = self.run_cli("new", "--list", cwd=self.tmp)
        self.assertEqual(code, 0, err)
        self.assertNotIn(BOARD, out)

    def test_pin_with_errors_stops_before_writing(self):
        self.pin(version="0.0.1")
        code, out, err = self.run_cli("new", "bad", "--board", BOARD)
        self.assertEqual(code, 1)
        self.assertIn("nextbsw.yaml", err)
        self.assertFalse((self.ws / "projects" / "bad").exists())


if __name__ == "__main__":
    unittest.main()
