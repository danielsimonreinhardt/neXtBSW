"""Host test of the workspace run of tools/test (TOOL-67, customer-project.md gap N12).

Everything happens in `%TEMP%`, with **invented** names throughout: the customer repository `zeta`, the board
`zeta_pump_c1` on the derivative `ZETAMCU128`, the module `zetaLevelMon`. The eval board is never the yardstick
(CLAUDE.md, guiding principle), and no file of this repository is read or written by the run under test.

The neXtBSW tree is synthetic as well: a folder with `VERSION` and a copy of `tools/nxconfig/version.py`, plus a
test list of invented entries. That way the test states exactly what a workspace inherits from a tree and can
prove, file by file, that the run wrote nothing into it.

What is checked:

  - discovery: the pin above a folder, the walk stopping at a tree and at a git repository without a pin;
  - the pin (ARCH-55/56): version and path against the tree of this run, and every way it can be wrong;
  - the list: only entries marked `in_workspace` carry over, `inherit:` adds further ones by name, an unknown
    name and a name clash are errors of the list;
  - the inventory: an unlisted test file **of the workspace** turns the run red, one of the tree never does;
  - placeholders and working directory: `{workspace}`/`{tree}`, a workspace entry runs in the workspace;
  - the build root lies in the workspace, and one inside the tree is refused;
  - the tree is unchanged after a complete run - file by file, size and modification time.

Run: .venv/Scripts/python -m unittest tools/test/test_workspace.py
"""

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import run_tests  # noqa: E402
import workspace as ws_support  # noqa: E402

# invented: nothing of this exists in the repository
BOARD = "zeta_pump_c1"
DERIVATIVE = "ZETAMCU128"
MODULE = "zetaLevelMon"
TREE_VERSION = "7.3.1"

# The list of the synthetic tree. Two entries carry the mark, one does not and one is only reachable through
# `inherit:` - so the test states the rule instead of relying on the real list.
TREE_LIST = """
version: 1
groups: [python, target]
tools:
  gibtsnicht:
    kind: env
    name: NX_TOOL_THAT_DOES_NOT_EXIST
    hint: erfundenes Werkzeug
tests:
  - name: baum-mechanik
    group: python
    kind: command
    comment: Mechanik des Baums, auf die ein Workspace baut - laeuft auch im Workspace
    argv: ["{python}", -c, "print('1 checks, 0 failures')"]
    in_workspace: true
    covers: []
  - name: baum-umgebung
    group: python
    kind: command
    comment: zweite uebernommene Mechanik; meldet, in welchem Ordner sie laeuft
    argv: ["{python}", -c, "import os; print('cwd=' + os.getcwd())"]
    in_workspace: true
    covers: []
  - name: baum-eigenes-board
    group: target
    kind: command
    comment: baut ein Board des Baums - gehoert nicht in die Pipeline eines Kunden
    argv: ["{python}", -c, "print('board des baums')"]
    covers: []
  - name: baum-auf-wunsch
    group: python
    kind: command
    comment: ein Eintrag, den ein Workspace mit inherit selbst dazunehmen kann
    argv: ["{python}", -c, "print('auf wunsch')"]
    covers: []
"""

# The workspace's own list: its projects, its components, its presets - in the format of the tree.
WORKSPACE_LIST = """
version: 1
groups: [python, host, target]
inventory:
  patterns: ["test_*.py"]
  skip_dirs: [.git, .venv, build, __pycache__, nextbsw]
  ignore:
    - path: werkzeuge/test_nicht_gelistet.py
      comment: Beispiel fuer einen bewusst nicht gelisteten Fund
tests:
  - name: zeta-konfiguration
    group: python
    kind: command
    comment: prueft die Konfiguration des eigenen Projekts und nennt Workspace und Baum
    argv: ["{python}", -c, "import sys; print('workspace=' + sys.argv[1]); print('tree=' + sys.argv[2])",
           "{workspace}", "{tree}"]
    covers: [tests/test_zeta_konfiguration.py]
  - name: zeta-arbeitsverzeichnis
    group: python
    kind: command
    comment: laeuft im Workspace, nicht im Baum
    argv: ["{python}", -c, "import os; print('cwd=' + os.getcwd())"]
    covers: []
  - name: zeta-bauwurzel
    group: target
    kind: command
    comment: bekommt die Bau-Wurzel des Workspace
    argv: ["{python}", -c, "import sys; print('build=' + sys.argv[1])", "{build}"]
    covers: []
"""


def make_tree(root: Path, version: str = TREE_VERSION) -> Path:
    """A folder that passes as a neXtBSW tree: VERSION and tools/nxconfig/version.py (copied from this tree)."""
    (root / "tools" / "nxconfig").mkdir(parents=True, exist_ok=True)
    (root / "VERSION").write_text(version + "\n", encoding="utf-8")
    (root / "tools" / "nxconfig" / "version.py").write_text(
        (REPO / "tools" / "nxconfig" / "version.py").read_text(encoding="utf-8"), encoding="utf-8")
    (root / "tools" / "test").mkdir(parents=True, exist_ok=True)
    (root / "tools" / "test" / "tests.yaml").write_text(TREE_LIST, encoding="utf-8")
    # a test file of the tree that is in no `covers` of the workspace - it must never be held against a customer
    (root / "tools" / "nxconfig" / "test_etwas_vom_baum.py").write_text("x = 1\n", encoding="utf-8")
    return root


def make_workspace(root: Path, tree: Path, version: str = TREE_VERSION, with_list: bool = True) -> Path:
    """The customer repository: pin, own board and derivative, own module, own test list under ci/."""
    root.mkdir(parents=True, exist_ok=True)
    relative = Path(__import__("os").path.relpath(tree, root)).as_posix()
    (root / ws_support.PIN_FILE).write_text(
        "# synthetic workspace of tools/test/test_workspace.py - test data, no real customer\n"
        "schema_version: 1\n"
        "nextbsw:\n"
        f"  version: {version}\n"
        "  source: submodule\n"
        f"  path: {relative}\n"
        "  profile: open\n"
        "search:\n"
        "  mcudb: [mcudb]\n"
        "  modules: [libs]\n"
        "  boards: [boards]\n", encoding="utf-8")
    (root / "mcudb" / "zeta1xx").mkdir(parents=True, exist_ok=True)
    (root / "mcudb" / "zeta1xx" / f"{DERIVATIVE}.yaml").write_text(f"derivative:\n  name: {DERIVATIVE}\n",
                                                                   encoding="utf-8")
    (root / "boards" / BOARD).mkdir(parents=True, exist_ok=True)
    (root / "boards" / BOARD / "board.yaml").write_text(f"board:\n  name: {BOARD}\n  mcu: {DERIVATIVE}\n",
                                                        encoding="utf-8")
    (root / "libs" / MODULE).mkdir(parents=True, exist_ok=True)
    (root / "libs" / MODULE / "module.yaml").write_text(
        f"module: {MODULE}\nkind: library\ndescription: level monitor of the customer\n", encoding="utf-8")
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests" / "test_zeta_konfiguration.py").write_text("x = 1\n", encoding="utf-8")
    if with_list:
        (root / ws_support.CI_DIR).mkdir(exist_ok=True)
        (root / ws_support.CI_DIR / ws_support.LIST_NAME).write_text(WORKSPACE_LIST, encoding="utf-8")
    return root


def run(argv, tree: Path, workspace: Path):
    """Runs the runner in this process against the synthetic tree and returns (exit code, output)."""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = run_tests.main([*argv, "--repo", str(tree), "--tests", str(tree / "tools" / "test" / "tests.yaml"),
                               "--workspace", str(workspace)])
    return code, buffer.getvalue()


class Scene(unittest.TestCase):
    """A synthetic tree and a synthetic workspace beside it, both fresh per test class."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="nxws_"))
        cls.tree = make_tree(cls.tmp / "nextbsw")
        cls.workspace = make_workspace(cls.tmp / "zeta", cls.tree)


class Discovery(Scene):
    """Walk-up like tools/nxconfig/workspace.py, so both tools find the same folder."""

    def test_pin_is_found_from_a_subfolder(self):
        found = ws_support.find_workspace(self.workspace / "projects" / "deep")
        self.assertEqual(found, self.workspace.resolve())

    def test_walk_stops_at_the_tree(self):
        self.assertIsNone(ws_support.find_workspace(self.tree / "tools" / "nxconfig"))

    def test_walk_stops_at_a_git_repository_without_a_pin(self):
        other = self.tmp / "fremd"
        (other / ".git").mkdir(parents=True)
        (other / "unten").mkdir()
        self.assertIsNone(ws_support.find_workspace(other / "unten"))

    def test_folder_without_a_pin_is_no_workspace(self):
        with self.assertRaises(ws_support.WorkspaceError) as caught:
            ws_support.open_workspace(self.tmp, self.tree)
        self.assertIn(ws_support.PIN_FILE, str(caught.exception))

    def test_paths_of_a_workspace(self):
        ws = ws_support.open_workspace(self.workspace, self.tree)
        self.assertEqual(ws.list_file, self.workspace.resolve() / "ci" / "tests.yaml")
        self.assertEqual(ws.pipeline_file, self.workspace.resolve() / "ci" / "pipeline.toml")
        self.assertTrue(ws.build_root.is_relative_to(self.workspace.resolve()))


class Pin(unittest.TestCase):
    """ARCH-55/56: the version of the tree against nextbsw.yaml - the check a pipeline has to make."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nxws_pin_"))
        self.tree = make_tree(self.tmp / "nextbsw")
        self.workspace = make_workspace(self.tmp / "zeta", self.tree)

    def findings(self, tree=None):
        return ws_support.pin_findings(ws_support.open_workspace(self.workspace, tree or self.tree))

    def test_matching_pin_is_green(self):
        found = self.findings()
        self.assertEqual([f.status for f in found], ["ok"], [f.text for f in found])
        self.assertIn(TREE_VERSION, found[0].text)

    def test_other_version_in_the_tree_is_red(self):
        (self.tree / "VERSION").write_text("7.4.0\n", encoding="utf-8")
        texts = [f.text for f in self.findings() if f.status == "fail"]
        self.assertTrue(any("7.4.0" in t and TREE_VERSION in t for t in texts), texts)
        self.assertTrue(any(f"v{TREE_VERSION}" in t for t in texts), texts)

    def test_pin_pointing_at_another_tree_is_red(self):
        other = make_tree(self.tmp / "anderer")
        texts = [f.text for f in self.findings(other) if f.status == "fail"]
        self.assertTrue(any("nextbsw.path" in t for t in texts), texts)

    def test_unreadable_pin_is_red_and_not_an_exception(self):
        (self.workspace / ws_support.PIN_FILE).write_text("nextbsw: [\n", encoding="utf-8")
        found = self.findings()
        self.assertEqual([f.status for f in found], ["fail"])

    def test_wrong_schema_version_is_red(self):
        text = (self.workspace / ws_support.PIN_FILE).read_text(encoding="utf-8")
        (self.workspace / ws_support.PIN_FILE).write_text(text.replace("schema_version: 1", "schema_version: 2"),
                                                          encoding="utf-8")
        texts = [f.text for f in self.findings() if f.status == "fail"]
        self.assertTrue(any("schema_version" in t for t in texts), texts)

    def test_missing_version_is_red(self):
        text = (self.workspace / ws_support.PIN_FILE).read_text(encoding="utf-8")
        (self.workspace / ws_support.PIN_FILE).write_text(text.replace(f"  version: {TREE_VERSION}\n", ""),
                                                          encoding="utf-8")
        texts = [f.text for f in self.findings() if f.status == "fail"]
        self.assertTrue(any("nextbsw.version" in t for t in texts), texts)


class WhichEntriesRun(Scene):
    """The tree contributes only what it marked; the workspace contributes its own list."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.json = cls.tmp / "lauf.json"
        cls.code, cls.out = run(["--json", str(cls.json)], cls.tree, cls.workspace)
        cls.data = json.loads(cls.json.read_text(encoding="utf-8"))
        cls.by_name = {t["name"]: t for t in cls.data["tests"]}

    def test_run_is_green(self):
        self.assertEqual(self.code, 0, self.out)

    def test_marked_tree_entries_run(self):
        self.assertIn("baum-mechanik", self.by_name)
        self.assertIn("baum-umgebung", self.by_name)

    def test_unmarked_tree_entries_stay_in_the_tree(self):
        self.assertNotIn("baum-eigenes-board", self.by_name)
        self.assertNotIn("baum-auf-wunsch", self.by_name)

    def test_the_workspaces_own_entries_run(self):
        for name in ("zeta-konfiguration", "zeta-arbeitsverzeichnis", "zeta-bauwurzel"):
            self.assertEqual(self.by_name[name]["status"], "pass", self.by_name[name])

    def test_origin_is_in_the_json(self):
        self.assertEqual(self.by_name["baum-mechanik"]["origin"], "tree")
        self.assertEqual(self.by_name["zeta-konfiguration"]["origin"], "workspace")

    def test_json_names_workspace_and_tree(self):
        self.assertEqual(Path(self.data["workspace"]), self.workspace.resolve())
        self.assertEqual(Path(self.data["repo"]), self.tree.resolve())

    def test_report_names_what_was_inherited(self):
        self.assertIn("Uebernommen aus dem Baum (2)", self.out)
        self.assertIn("baum-mechanik", self.out)

    def test_list_shows_the_merged_list(self):
        code, out = run(["--list"], self.tree, self.workspace)
        self.assertEqual(code, 0, out)
        self.assertIn("zeta-konfiguration", out)
        self.assertIn("baum-mechanik", out)
        self.assertNotIn("baum-eigenes-board", out)

    def test_selection_works_across_both_lists(self):
        code, out = run(["-k", "zeta-"], self.tree, self.workspace)
        self.assertEqual(code, 0, out)
        self.assertIn("zeta-konfiguration", out)
        self.assertNotIn("baum-mechanik ", out)

    def test_list_as_json_names_every_entry_with_its_origin(self):
        """TOOL-109: the Studio's page "Host-Tests" reads the offer of a workspace from here."""
        file = self.tmp / "liste.json"
        code, out = run(["--list", "--json", str(file)], self.tree, self.workspace)
        self.assertEqual(code, 0, out)
        data = json.loads(file.read_text(encoding="utf-8"))
        self.assertEqual(data["schema"], "nxtest-list/1")
        self.assertEqual(Path(data["workspace"]), self.workspace.resolve())
        entries = {t["name"]: t for t in data["tests"]}
        self.assertEqual(set(entries), {"baum-mechanik", "baum-umgebung", "zeta-konfiguration",
                                        "zeta-arbeitsverzeichnis", "zeta-bauwurzel"})
        self.assertEqual(entries["baum-mechanik"]["origin"], "tree")
        self.assertEqual(entries["zeta-konfiguration"]["origin"], "workspace")
        self.assertTrue(entries["zeta-konfiguration"]["comment"])

    def test_skip_takes_out_exactly_the_named_entries(self):
        """TOOL-109: the entries a Studio user switched off - by exact name, not as a pattern."""
        file = self.tmp / "ohne.json"
        code, out = run(["--skip", "baum-mechanik", "--skip", "zeta-bauwurzel", "--skip", "gibt-es-nicht",
                         "--json", str(file)], self.tree, self.workspace)
        self.assertEqual(code, 0, out)
        ran = {t["name"] for t in json.loads(file.read_text(encoding="utf-8"))["tests"]}
        self.assertEqual(ran, {"baum-umgebung", "zeta-konfiguration", "zeta-arbeitsverzeichnis"})
        self.assertIn("--skip gibt-es-nicht - kein Eintrag dieses Namens", out)


class InheritedOnRequest(unittest.TestCase):
    """A workspace may take further entries of the tree - by name, and an unknown name is an error."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nxws_inh_"))
        self.tree = make_tree(self.tmp / "nextbsw")
        self.workspace = make_workspace(self.tmp / "zeta", self.tree)
        self.list_file = self.workspace / ws_support.CI_DIR / ws_support.LIST_NAME

    def test_named_entry_is_added(self):
        self.list_file.write_text("inherit: [baum-auf-wunsch]\n" + WORKSPACE_LIST, encoding="utf-8")
        json_file = self.tmp / "lauf.json"
        code, out = run(["--json", str(json_file)], self.tree, self.workspace)
        self.assertEqual(code, 0, out)
        names = {t["name"] for t in json.loads(json_file.read_text(encoding="utf-8"))["tests"]}
        self.assertIn("baum-auf-wunsch", names)
        self.assertNotIn("baum-eigenes-board", names)

    def test_unknown_name_is_an_error_of_the_list(self):
        self.list_file.write_text("inherit: [gibtsnicht]\n" + WORKSPACE_LIST, encoding="utf-8")
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = run_tests.main(["--repo", str(self.tree), "--workspace", str(self.workspace),
                                   "--tests", str(self.tree / "tools" / "test" / "tests.yaml")])
        self.assertEqual(code, 2)

    def test_a_name_that_clashes_with_an_inherited_entry_is_an_error(self):
        self.list_file.write_text(WORKSPACE_LIST.replace("zeta-konfiguration", "baum-mechanik"), encoding="utf-8")
        with self.assertRaises(run_tests.ListError) as caught:
            run_tests.merge_lists(run_tests.load_list(self.tree / "tools" / "test" / "tests.yaml"),
                                  run_tests.load_list(self.list_file, run_tests.WORKSPACE))
        self.assertIn("baum-mechanik", str(caught.exception))


class Inventory(unittest.TestCase):
    """The completeness check looks at the workspace - never at the test files of the tree."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nxws_inv_"))
        self.tree = make_tree(self.tmp / "nextbsw")
        self.workspace = make_workspace(self.tmp / "zeta", self.tree)

    def test_test_file_of_the_tree_is_never_held_against_the_customer(self):
        code, out = run(["--group", "python"], self.tree, self.workspace)
        self.assertEqual(code, 0, out)
        self.assertNotIn("test_etwas_vom_baum.py", out)
        self.assertNotIn("NICHT IN", out)

    def test_unlisted_test_file_of_the_workspace_is_red(self):
        (self.workspace / "tests" / "test_neu.py").write_text("x = 1\n", encoding="utf-8")
        code, out = run(["--group", "python"], self.tree, self.workspace)
        self.assertEqual(code, 1, out)
        self.assertIn("NICHT IN ci/tests.yaml", out)
        self.assertIn("test_neu.py", out)

    def test_ignored_file_of_the_workspace_is_not_reported(self):
        (self.workspace / "werkzeuge").mkdir()
        (self.workspace / "werkzeuge" / "test_nicht_gelistet.py").write_text("x = 1\n", encoding="utf-8")
        code, out = run(["--group", "python"], self.tree, self.workspace)
        self.assertEqual(code, 0, out)

    def test_without_a_list_the_check_is_named_and_skipped(self):
        empty = make_workspace(self.tmp / "ohne_liste", self.tree, with_list=False)
        (empty / "tests" / "test_irgendwas.py").write_text("x = 1\n", encoding="utf-8")
        code, out = run([], self.tree, empty)
        self.assertEqual(code, 0, out)
        self.assertIn("keine ci/tests.yaml", out)
        self.assertNotIn("NICHT IN", out)


class PlaceholdersAndPaths(Scene):
    """{workspace}/{tree}, the working directory and the build root."""

    def test_workspace_and_tree_are_separate_placeholders(self):
        code, out = run(["-k", "zeta-konfiguration"], self.tree, self.workspace)
        self.assertEqual(code, 0, out)
        runner = run_tests.Runner(self.tree.resolve(), self.tmp / "bau", sys.executable,
                                  workspace=self.workspace.resolve())
        filled = runner.placeholders(Path("egal"))
        self.assertEqual(Path(filled["workspace"]), self.workspace.resolve())
        self.assertEqual(Path(filled["tree"]), self.tree.resolve())
        self.assertEqual(filled["tree"], filled["repo"])

    def test_in_a_tree_run_workspace_is_the_tree(self):
        runner = run_tests.Runner(self.tree.resolve(), self.tmp / "bau", sys.executable)
        filled = runner.placeholders(Path("egal"))
        self.assertEqual(filled["workspace"], filled["tree"])

    def test_workspace_entry_runs_in_the_workspace_and_tree_entry_in_the_tree(self):
        json_file = self.tmp / "cwd.json"
        code, out = run(["--json", str(json_file), "-k", "arbeitsverzeichnis"], self.tree, self.workspace)
        self.assertEqual(code, 0, out)
        runner = run_tests.Runner(self.tree.resolve(), self.tmp / "bau", sys.executable,
                                  workspace=self.workspace.resolve())
        own = run_tests.Test("x", "python", "command", origin=run_tests.WORKSPACE)
        inherited = run_tests.Test("y", "python", "command", origin=run_tests.TREE)
        self.assertEqual(runner.cwd_of(own), self.workspace.resolve())
        self.assertEqual(runner.cwd_of(inherited), self.tree.resolve())

    def test_build_root_is_in_the_workspace(self):
        code, out = run(["-k", "bauwurzel"], self.tree, self.workspace)
        self.assertEqual(code, 0, out)
        self.assertIn(str(self.workspace.resolve() / "build"), out)

    def test_a_build_root_inside_the_tree_is_refused(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = run_tests.main(["--repo", str(self.tree), "--workspace", str(self.workspace),
                                   "--tests", str(self.tree / "tools" / "test" / "tests.yaml"),
                                   "--build-root", str(self.tree / "build")])
        self.assertEqual(code, 2)
        self.assertTrue(ws_support.check_build_root(self.tree / "build" / "x", self.tree).startswith("Bau-Wurzel"))
        self.assertEqual(ws_support.check_build_root(self.workspace / "build", self.tree), "")

    def test_a_module_of_the_workspace_is_known(self):
        data = run_tests.merge_lists(
            run_tests.load_list(self.tree / "tools" / "test" / "tests.yaml"),
            run_tests.load_list(self.workspace / ws_support.CI_DIR / ws_support.LIST_NAME, run_tests.WORKSPACE))
        data["tests"][-1].modules = [MODULE]
        self.assertEqual(run_tests.module_problems(data, self.tree, [self.workspace]), [])
        data["tests"][-1].modules = ["gibtsnicht"]
        self.assertTrue(run_tests.module_problems(data, self.tree, [self.workspace]))


class TreeStaysUntouched(unittest.TestCase):
    """The pinned tree is a released baseline: a workspace run reads it and writes nothing into it."""

    def test_no_file_of_the_tree_changes(self):
        tmp = Path(tempfile.mkdtemp(prefix="nxws_ro_"))
        tree = make_tree(tmp / "nextbsw")
        ws = make_workspace(tmp / "zeta", tree)
        before = ws_support.snapshot(tree, skip_dirs=("__pycache__",))
        code, out = run(["--json", str(tmp / "lauf.json")], tree, ws)
        self.assertEqual(code, 0, out)
        after = ws_support.snapshot(tree, skip_dirs=("__pycache__",))
        self.assertEqual(before, after, "der Lauf hat den Baum veraendert")


class RedWorkspaceTest(unittest.TestCase):
    """A failing test of the customer is red, and the exit code carries it to his CI."""

    def test_failing_entry_makes_the_run_red(self):
        tmp = Path(tempfile.mkdtemp(prefix="nxws_rot_"))
        tree = make_tree(tmp / "nextbsw")
        ws = make_workspace(tmp / "zeta", tree)
        (ws / ws_support.CI_DIR / ws_support.LIST_NAME).write_text(WORKSPACE_LIST + """
  - name: zeta-rot
    group: python
    kind: command
    comment: ein fehlgeschlagener Test des Kunden
    argv: ["{python}", -c, "import sys; sys.exit(4)"]
    covers: []
""", encoding="utf-8")
        code, out = run([], tree, ws)
        self.assertEqual(code, 1, out)
        self.assertIn("zeta-rot", out)
        self.assertIn("Exit-Code 4", out)


class ProbesOfTheTree(unittest.TestCase):
    """TOOL-123: the tree's probes reach a workspace run only when an inherited entry requires them - otherwise a
    customer read "Fehlende Werkzeuge: strictdoc, svd", tools of the tree he never needs."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nxws_probe_"))
        self.tree = make_tree(self.tmp / "nextbsw")
        self.workspace = make_workspace(self.tmp / "zeta", self.tree)
        self.list_file = self.workspace / ws_support.CI_DIR / ws_support.LIST_NAME

    def _merged(self):
        return run_tests.merge_lists(run_tests.load_list(self.tree / "tools" / "test" / "tests.yaml"),
                                     run_tests.load_list(self.list_file, run_tests.WORKSPACE))

    def test_an_unused_probe_of_the_tree_is_not_taken(self):
        self.assertNotIn("gibtsnicht", self._merged()["tools"])
        code, out = run([], self.tree, self.workspace)
        self.assertEqual(code, 0, out)
        self.assertNotIn("Fehlende Werkzeuge", out)

    def test_a_probe_an_inherited_entry_requires_is_taken(self):
        tree_list = self.tree / "tools" / "test" / "tests.yaml"
        tree_list.write_text(TREE_LIST.replace("    in_workspace: true\n    covers: []\n",
                                               "    in_workspace: true\n    requires: [gibtsnicht]\n    covers: []\n",
                                               1), encoding="utf-8")
        self.assertIn("gibtsnicht", self._merged()["tools"])
        code, out = run([], self.tree, self.workspace)
        self.assertIn("Fehlende Werkzeuge", out)
        self.assertIn("gibtsnicht", out)

    def test_the_workspace_keeps_its_own_probes(self):
        self.list_file.write_text(WORKSPACE_LIST.replace(
            "tests:\n", "tools:\n  eigenes:\n    kind: env\n    name: NX_ZETA_TOOL_THAT_DOES_NOT_EXIST\ntests:\n", 1),
            encoding="utf-8")
        self.assertIn("eigenes", self._merged()["tools"])


class AsACommand(unittest.TestCase):
    """The customer calls the tool of the pinned tree from his own folder - one command, no PYTHONPATH."""

    def test_call_from_the_workspace(self):
        tmp = Path(tempfile.mkdtemp(prefix="nxws_cmd_"))
        tree = make_tree(tmp / "nextbsw")
        ws = make_workspace(tmp / "zeta", tree)
        done = subprocess.run([sys.executable, str(REPO / "tools" / "test"), "--workspace", ".",
                               "--repo", str(tree), "--tests", str(tree / "tools" / "test" / "tests.yaml")],
                              cwd=ws, capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn(str(ws.resolve()), done.stdout)
        self.assertIn("zeta-konfiguration", done.stdout)


if __name__ == "__main__":
    unittest.main()
