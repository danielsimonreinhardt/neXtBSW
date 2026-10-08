"""Host tests of `nxconfig.check(running_tree=...)` (TOOL-85): which neXtBSW tree a check runs as.

Until now the tree was the module variable `nxconfig.config.REPO`, bound once at import. A workspace pin that
names another tree was refused ("use the tools of the pinned tree", customer-project.md section 3.1) - and the
check then returned **no configuration at all**, so such a project could not be opened. That is right for a
generator on the command line, but wrong for a tool that switches the tree on purpose: the Studio picks the tree
per opened project (TOOL-61), and `nextbsw/` in a customer repository is a second checkout of the same tree. The
Studio helped itself by moving the module variable under a lock (studio/core/tree.py `as_tool_of`, TOOL-60) -
a global that every thread of the process shares.

The caller now says which tree it runs as, and the default is the tree this package lies in, so nothing changes
for the command line and the generators.

Everything here is synthetic and invented: **two** neXtBSW trees `talvi` and `kesa` in %TEMP%, each with its own
derivative, its own family port template and its own board port, and one customer workspace per tree whose pin
names it. No neXtBSW board, no eval board and no folder of the repository under test is the yardstick - the
trees are built from the synthetic test profile (TESTMCU64/testboard/testproject) under other names.
"""

import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import nxconfig  # noqa: E402
from nxconfig import checks as nxchecks  # noqa: E402
from nxconfig import config as nxcfg  # noqa: E402
from nxconfig import workspace as nxws  # noqa: E402

PROFILES = HERE / "test" / "profiles"
TEST_MCUDB = HERE / "test" / "mcudb"

#: version both trees carry - invented, and deliberately not the version of the repository under test
TREE_VERSION = "7.3.2"
FAMILY = "testfam"                                  # the family of the synthetic derivative, kept in both trees


class Tree:
    """One synthetic neXtBSW tree plus the customer workspace whose pin names it."""

    def __init__(self, root: Path, name: str):
        self.name = name
        self.root = root / f"nextbsw-{name}"
        self.workspace = root / f"{name}-ctl"
        self.derivative = f"{name.upper()}MCU64"
        self.board = f"{name}_ctl_b1"
        self.project_name = f"{name}_main"
        self.family_module = f"{name}FamPort"       # module of platforms/<family>/port of THIS tree
        self.board_module = f"{name}BoardPort"      # module of ecu/platforms/<board> of THIS tree
        self._build_tree()
        self._build_workspace()

    # ------------------------------------------------------------------ the tree

    def _build_tree(self) -> None:
        write(self.root / "VERSION", f"{TREE_VERSION}\n")
        # workspace.is_tree(): VERSION and tools/nxconfig make a folder a neXtBSW tree
        (self.root / "tools" / "nxconfig").mkdir(parents=True, exist_ok=True)
        for parts in (("libs", "bsw"), ("libs", "bsp"), ("boards",)):
            self.root.joinpath(*parts).mkdir(parents=True, exist_ok=True)
        # the derivative of this tree: the synthetic TESTMCU64 under another name
        text = (TEST_MCUDB / FAMILY / "TESTMCU64.yaml").read_text(encoding="utf-8")
        write(self.root / "mcudb" / FAMILY / f"{self.derivative}.yaml",
              text.replace("name: TESTMCU64", f"name: {self.derivative}"))
        # the two port folders of this tree (config.port_module_dirs): family template and board port. A
        # catalogue entry needs no integration, so its presence in cfg.modules is the whole statement.
        write(self.root / "platforms" / FAMILY / "port" / self.family_module / "module.yaml",
              module_yaml(self.family_module, f"family port template of tree {self.name}"))
        write(self.root / "ecu" / "platforms" / self.board / self.board_module / "module.yaml",
              module_yaml(self.board_module, f"board port of {self.board} in tree {self.name}"))
        self._copy_name_limits()

    def _copy_name_limits(self) -> None:
        """The headers the name limits of ARCH-43 are read from (checks.NAME_LIMITS), copied into this tree.

        A synthetic tree is a **tree**, not an empty folder. `name_lengths` still reads these limits from the
        tree this package lies in (the remaining TOOL-61 limit, see `checks.run`), so the copy changes nothing
        today - it makes the fixture a complete tree, so the test keeps its meaning when that check follows
        `running_tree` too. Only the folders the table names are copied, and only their headers: no module
        manifest comes with them, so the module catalogue of a synthetic tree stays the two port modules above.
        """
        for limit in nxchecks.NAME_LIMITS.values():
            if not limit.required or "{family}" in limit.root:
                continue                            # optional, or a folder of the family port template
            source = nxcfg.REPO / limit.root
            if source.is_dir():
                shutil.copytree(source, self.root / limit.root, dirs_exist_ok=True)

    # ------------------------------------------------------------------ the workspace that pins it

    def _build_workspace(self) -> None:
        text = (PROFILES / "boards" / "testboard" / "board.yaml").read_text(encoding="utf-8")
        write(self.workspace / "boards" / self.board / "board.yaml",
              text.replace("name: testboard", f"name: {self.board}")
                  .replace("mcu: TESTMCU64", f"mcu: {self.derivative}"))
        text = (PROFILES / "projects" / "testproject" / "project.yaml").read_text(encoding="utf-8")
        write(self.workspace / "projects" / self.project_name / "project.yaml",
              text.replace("name: testproject", f"name: {self.project_name}")
                  .replace("../../boards/testboard", f"../../boards/{self.board}"))
        self.pin()

    def pin(self, tree: Path | None = None, version: str = TREE_VERSION) -> Path:
        """Writes nextbsw.yaml; `tree` (default: this tree) is stored relative, as a submodule would be."""
        target = Path(tree) if tree is not None else self.root
        relative = Path(_relpath(target, self.workspace)).as_posix()
        return write(self.workspace / nxws.PIN_FILE,
                     "# synthetic workspace pin (test data)\n"
                     "schema_version: 1\n"
                     "nextbsw:\n"
                     f"  version: {version}\n"
                     "  source: submodule\n"
                     f"  path: {relative}\n"
                     "  profile: open\n"
                     "search:\n"
                     f"  boards: [boards]\n")

    @property
    def project(self) -> Path:
        return self.workspace / "projects" / self.project_name / "project.yaml"


def write(file: Path, text: str) -> Path:
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text, encoding="utf-8")
    return file


def module_yaml(name: str, description: str) -> str:
    return f"module: {name}\nkind: library\ndescription: {description}\n"


def _relpath(target: Path, start: Path) -> str:
    try:
        return os.path.relpath(target, start)
    except ValueError:                              # another drive: an absolute path
        return str(target)


def errors(messages) -> list:
    return [m for m in messages if m.severity == "error"]


class TwoTrees(unittest.TestCase):
    """Two trees, one workspace each - the parameter decides which tree a check runs as."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="nxtree-test-")).resolve()
        cls.talvi = Tree(cls.tmp, "talvi")
        cls.kesa = Tree(cls.tmp, "kesa")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ------------------------------------------------------------------ the fixture itself

    def test_both_trees_are_trees(self):
        """Both folders are neXtBSW trees by the rule of workspace.is_tree, and they are two."""
        for tree in (self.talvi, self.kesa):
            self.assertTrue(nxws.is_tree(tree.root), tree.root)
        self.assertNotEqual(self.talvi.root, self.kesa.root)

    # ------------------------------------------------------------------ 1. without the parameter: as before

    def test_pin_of_another_tree_is_still_refused(self):
        """Default (no running_tree): the pin names a tree other than the running one - error, no configuration.

        This is the rule of customer-project.md 3.1 for the command line and the generators; it must not soften
        just because the parameter exists.
        """
        config, messages = nxconfig.check(self.kesa.project)
        self.assertIsNone(config)
        found = errors(messages)
        self.assertTrue(found, messages)
        message = found[0]
        self.assertEqual(message.file, self.kesa.workspace / nxws.PIN_FILE)
        self.assertIsNotNone(message.line)
        self.assertEqual(message.path, ("nextbsw", "path"))
        self.assertIn(str(self.kesa.root), message.text)
        self.assertIn(str(nxcfg.REPO), message.text)

    def test_running_tree_of_the_other_tree_is_refused(self):
        """A caller that runs as tree A must not check a project pinned to tree B either."""
        config, messages = nxconfig.check(self.kesa.project, running_tree=self.talvi.root)
        self.assertIsNone(config)
        found = errors(messages)
        self.assertTrue(found, messages)
        self.assertIn(str(self.talvi.root), found[0].text)
        self.assertIn(str(self.kesa.root), found[0].text)

    # ------------------------------------------------------------------ 2. with the parameter: it checks

    def test_pinned_tree_as_running_tree_checks(self):
        """running_tree = the pinned tree: the project checks, with no error at all."""
        for tree in (self.talvi, self.kesa):
            with self.subTest(tree=tree.name):
                config, messages = nxconfig.check(tree.project, running_tree=tree.root)
                self.assertEqual(errors(messages), [], messages)
                self.assertIsNotNone(config)
                self.assertEqual(config.running_tree, tree.root)
                self.assertEqual(config.workspace.tree, tree.root)

    def test_derivative_comes_from_the_running_tree(self):
        """No mcudb given: the derivative is the one of the running tree, not of the tree this package lies in.

        The workspaces have no `search.mcudb`, and the repository under test knows neither derivative - so the
        file that was read can only have come from the tree the parameter named.
        """
        for tree in (self.talvi, self.kesa):
            with self.subTest(tree=tree.name):
                config, _ = nxconfig.check(tree.project, running_tree=tree.root)
                self.assertEqual(config.derivative_file,
                                 tree.root / "mcudb" / FAMILY / f"{tree.derivative}.yaml")
                self.assertEqual(config.derivative.derivative.name, tree.derivative)

    def test_port_folders_follow_the_running_tree(self):
        """FAMILY_PORTS and BOARD_PORTS are no longer bound at import: both port modules are the tree's own.

        This is the half of TOOL-85 that made the parameter a promise the tool did not keep: the pin was judged
        against the named tree, but the port folders still came from the tree the process was started from.
        """
        for tree, other in ((self.talvi, self.kesa), (self.kesa, self.talvi)):
            with self.subTest(tree=tree.name):
                config, _ = nxconfig.check(tree.project, running_tree=tree.root)
                self.assertIn(tree.family_module, config.modules.manifests)
                self.assertIn(tree.board_module, config.modules.manifests)
                self.assertNotIn(other.family_module, config.modules.manifests)
                self.assertNotIn(other.board_module, config.modules.manifests)
                self.assertEqual(config.modules.files[tree.family_module],
                                 tree.root / "platforms" / FAMILY / "port" / tree.family_module / "module.yaml")
                self.assertEqual(config.modules.files[tree.board_module],
                                 tree.root / "ecu" / "platforms" / tree.board / tree.board_module / "module.yaml")

    def test_port_module_dirs_take_a_tree(self):
        """config.port_module_dirs(tree=...) names the folders of that tree; without one, of the running tree."""
        for tree in (self.talvi, self.kesa):
            with self.subTest(tree=tree.name):
                self.assertEqual(nxcfg.port_module_dirs(tree.board, FAMILY, tree=tree.root),
                                 (tree.root / "platforms" / FAMILY / "port",
                                  tree.root / "ecu" / "platforms" / tree.board))
        # the default is still the tree this package lies in - nothing of the two temporary trees is in it
        self.assertEqual(nxcfg.port_module_dirs(self.kesa.board, FAMILY), ())
        self.assertEqual(nxcfg.family_ports_dir(), nxcfg.REPO / "platforms")
        self.assertEqual(nxcfg.board_ports_dir(), nxcfg.REPO / "ecu" / "platforms")

    # ------------------------------------------------------------------ 3. no global is moved any more

    def test_no_module_variable_is_moved(self):
        """A check with another running tree leaves nxconfig.config.REPO and its folders untouched."""
        before = (nxcfg.REPO, nxcfg.MCUDB, nxcfg.MODULE_DIRS)
        nxconfig.check(self.kesa.project, running_tree=self.kesa.root)
        self.assertEqual((nxcfg.REPO, nxcfg.MCUDB, nxcfg.MODULE_DIRS), before)

    def test_two_trees_at_the_same_time(self):
        """Two checks of two trees run **concurrently** and each keeps its own tree.

        The Studio loads a project in a worker thread. With a module variable this needed a lock around every
        check (studio/core/tree.py _TOOL_LOCK); with a parameter the two calls do not know about each other.
        """
        results: dict[str, object] = {}
        barrier = threading.Barrier(2)

        def run(tree):
            barrier.wait(timeout=30)
            config, messages = nxconfig.check(tree.project, running_tree=tree.root)
            results[tree.name] = (config, messages)

        threads = [threading.Thread(target=run, args=(t,)) for t in (self.talvi, self.kesa)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)
            self.assertFalse(t.is_alive(), "a check did not finish")
        for tree in (self.talvi, self.kesa):
            config, messages = results[tree.name]
            self.assertEqual(errors(messages), [], messages)
            self.assertEqual(config.running_tree, tree.root)
            self.assertIn(tree.board_module, config.modules.manifests)

    # ------------------------------------------------------------------ 4. what the callers of today see

    def test_project_of_the_tree_itself_is_unchanged(self):
        """A project without a pin keeps working with the tree it lies in (customer-project.md 6.4)."""
        # PLAT-62: the eval board project where it exists, else an open project of the tree (exported open tree)
        project = next(p for p in (nxcfg.REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml",
                                   nxcfg.REPO / "projects" / "buildtest_s32k148_min" / "project.yaml") if p.is_file())
        config, messages = nxconfig.check(project)
        self.assertEqual(errors(messages), [], messages)
        self.assertEqual(config.running_tree, nxcfg.REPO)

    def test_load_passes_the_running_tree_on(self):
        """nxconfig.load() (the generators' entry) takes the parameter and hands it down."""
        config = nxconfig.load(self.kesa.project, running_tree=self.kesa.root)
        self.assertEqual(config.running_tree, self.kesa.root)
        with self.assertRaises(nxconfig.ConfigErrors):
            nxconfig.load(self.kesa.project)        # without it: refused as before

    def test_build_settings_of_a_foreign_tree(self):
        """The build settings of a project checked with another running tree name that tree's files."""
        config, _ = nxconfig.check(self.kesa.project, running_tree=self.kesa.root)
        settings, _messages = nxcfg.build_settings(config)
        files = settings["NX_CONFIG_FILES"]
        self.assertIn(config.derivative_file.as_posix(), files)
        self.assertIn((self.kesa.workspace / nxws.PIN_FILE).as_posix(), files)


class PinStaysStrictOnTheCommandLine(unittest.TestCase):
    """The rule itself is unchanged: a wrong version, a missing tree and a foreign tree stay errors."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nxtree-strict-")).resolve()
        self.tree = Tree(self.tmp, "routa")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_wrong_version_is_an_error_with_the_running_tree(self):
        """running_tree does not switch off the version check of the pin (ARCH-55)."""
        self.tree.pin(version="9.9.9")
        config, messages = nxconfig.check(self.tree.project, running_tree=self.tree.root)
        self.assertIsNone(config)
        found = errors(messages)
        self.assertTrue(found, messages)
        self.assertEqual(found[0].path, ("nextbsw", "version"))
        self.assertIn("9.9.9", found[0].text)
        self.assertIn(TREE_VERSION, found[0].text)

    def test_missing_tree_is_an_error_with_the_running_tree(self):
        """A pin whose folder does not exist stays an error, whatever the caller says it runs as."""
        self.tree.pin(tree=self.tmp / "no-such-tree")
        config, messages = nxconfig.check(self.tree.project, running_tree=self.tree.root)
        self.assertIsNone(config)
        found = errors(messages)
        self.assertTrue(found, messages)
        self.assertEqual(found[0].path, ("nextbsw", "path"))
        self.assertIn("not found", found[0].text)


if __name__ == "__main__":
    unittest.main()
