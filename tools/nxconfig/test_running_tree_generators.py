"""Host tests of `running_tree` through **all four** tools (TOOL-87): nxconfig, diaggen, bswgen, rtegen.

TOOL-85 gave `nxconfig.check()` the parameter `running_tree`: the caller says which neXtBSW tree it runs as, and
a workspace pin that names that tree is then accepted instead of refused ("use the tools of the pinned tree",
customer-project.md section 3.1). The three generators did not hand the parameter down, so they would still have
refused a pin on another tree - and the Studio calls all four in the same block (studio/core/workspace.check),
so its stopgap (`as_tool_of` and `_TOOL_LOCK` in studio/core/tree.py, TOOL-60) could not go.

This test runs one pin on a **second** tree through every one of the four entry points, in both directions:

  - **without** the parameter every tool refuses the pin, with exactly the message of TOOL-85 - the rule of the
    command line is unchanged, and that is what "default None behaves exactly as today" means here;
  - **with** it every tool checks the project of the pinned tree, reports the findings of *that* tree's
    configuration and gets as far as its own stage;
  - the derivative database and the module folders follow the named tree (they were default values bound at
    import in bswgen);
  - no module variable is moved, and the four tools run against two trees **at the same time** in four threads -
    the evidence that the Studio's lock is not needed any more.

The fixture is the one of TOOL-85 (`test_running_tree.Tree`, two invented trees in %TEMP%), reused instead of
copied: a tree with its own derivative, its own family port template, its own board port and a customer
workspace whose `nextbsw.yaml` pins it. Everything is invented (`aalto`, `virta`); no neXtBSW board, no eval
board and no folder of the repository under test is the yardstick.

The file lies next to the fixture and next to the test of the parameter itself, because the contract under test
is the one of `nxconfig.check()` - this test asserts that its four callers keep it. Only test code lives here;
the nxconfig package itself gains no dependency on a generator.
"""

import io
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
# the generators are modules, not packages (tools/<name>/<name>.py); the Studio puts the same folders on the
# path (studio/core/workspace.py)
for _path in (TOOLS, TOOLS / "bswgen", TOOLS / "rtegen", TOOLS / "dbcgen", TOOLS / "diaggen"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import nxconfig  # noqa: E402
from nxconfig import config as nxcfg  # noqa: E402
from nxconfig import workspace as nxws  # noqa: E402
from nxconfig.test_running_tree import FAMILY, Tree, errors  # noqa: E402  (fixture of TOOL-85)

import bswgen  # noqa: E402
import diaggen  # noqa: E402
import rtegen  # noqa: E402

#: YAML path of the pin refusal; `nextbsw.path` is the value that names the tree
PIN_PATH = ("nextbsw", "path")

#: Two findings of the **configuration** that every tool reports once it got through nxconfig: the synthetic
#: board carries two CAN transceivers without a module. They prove that the configuration of the pinned tree was
#: really loaded and checked, whichever tool asked.
BOARD_WARNINGS = (("can", 0, "phy"), ("can", 1, "phy"))


def tool_messages(name: str, project: Path, running_tree: Path | None = None) -> list:
    """The messages of one of the four entry points - the four calls of studio/core/workspace.check()."""
    if name == "nxconfig":
        return list(nxconfig.check(project, running_tree=running_tree)[1])
    if name == "diaggen":
        return list(diaggen.validate(project, running_tree=running_tree))
    if name == "bswgen":
        return list(bswgen.validate(project, running_tree=running_tree))
    if name == "rtegen":
        return list(rtegen.validate(project, running_tree=running_tree))
    raise AssertionError(f"unknown tool {name}")


TOOLS_UNDER_TEST = ("nxconfig", "diaggen", "bswgen", "rtegen")


class PinOnASecondTreeThroughEveryTool(unittest.TestCase):
    """One pin, two trees, four tools - with and without the parameter."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="nxtree-tools-")).resolve()
        cls.aalto = Tree(cls.tmp, "aalto")
        cls.virta = Tree(cls.tmp, "virta")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def pin_errors(self, messages, tree: Tree) -> list:
        return [m for m in messages if m.file == tree.workspace / nxws.PIN_FILE]

    # ------------------------------------------------------------------ 1. without it: refused, as before

    def test_every_tool_still_refuses_a_foreign_pin(self):
        """Default (no running_tree): all four refuse the pin of the second tree, with the same message.

        This is the rule of the command line and must not soften because the parameter exists: a generator
        started in tree A must not silently work with tree B.
        """
        for tree in (self.aalto, self.virta):
            for name in TOOLS_UNDER_TEST:
                with self.subTest(tool=name, tree=tree.name):
                    messages = tool_messages(name, tree.project)
                    found = errors(messages)
                    self.assertEqual(len(found), 1, messages)
                    self.assertEqual(found[0].file, tree.workspace / nxws.PIN_FILE)
                    self.assertEqual(found[0].path, PIN_PATH)
                    self.assertIsNotNone(found[0].line)
                    self.assertIn(str(tree.root), found[0].text)
                    self.assertIn(str(nxcfg.REPO), found[0].text)

    def test_every_tool_refuses_the_pin_of_the_other_tree(self):
        """running_tree = tree A, pin = tree B: refused by all four. The parameter is not a switch-off."""
        for tree, other in ((self.aalto, self.virta), (self.virta, self.aalto)):
            for name in TOOLS_UNDER_TEST:
                with self.subTest(tool=name, tree=tree.name):
                    found = errors(tool_messages(name, tree.project, running_tree=other.root))
                    self.assertEqual(len(found), 1, found)
                    self.assertEqual(found[0].path, PIN_PATH)
                    self.assertIn(str(other.root), found[0].text)
                    self.assertIn(str(tree.root), found[0].text)

    # ------------------------------------------------------------------ 2. with it: every tool checks

    def test_every_tool_checks_the_project_of_the_pinned_tree(self):
        """running_tree = the pinned tree: no tool complains about the pin any more, and all of them see the
        findings of that tree's configuration (the two transceivers without a module)."""
        for tree in (self.aalto, self.virta):
            for name in TOOLS_UNDER_TEST:
                with self.subTest(tool=name, tree=tree.name):
                    messages = tool_messages(name, tree.project, running_tree=tree.root)
                    self.assertEqual(self.pin_errors(messages, tree), [], messages)
                    paths = [m.path for m in messages]
                    for path in BOARD_WARNINGS:
                        self.assertIn(path, paths, messages)

    def test_each_generator_reaches_its_own_stage(self):
        """Past nxconfig, each generator judges the project by its own rules - that is the proof that the
        configuration really arrived, not just that one message disappeared.

        The synthetic tree is deliberately not a supported one, so the answers are the ones of the generators:
        diaggen falls back to its default description and has nothing to complain about, bswgen does not know
        the invented family, and rtegen misses the ECU description the project does not name.
        """
        tree = self.aalto
        self.assertEqual(errors(diaggen.validate(tree.project, running_tree=tree.root)), [])
        bsw = errors(bswgen.validate(tree.project, running_tree=tree.root))
        self.assertTrue(any(FAMILY in m.text and "bswgen" in m.text for m in bsw), bsw)
        rte = errors(rtegen.validate(tree.project, running_tree=tree.root))
        self.assertTrue(any(m.path == ("project", "rte") for m in rte), rte)

    def test_diaggen_load_project_takes_the_running_tree(self):
        """diaggen's load entry (nxconfig.load) hands the parameter down as well: with it the description of
        the pinned project is built, without it the load is refused as before."""
        tree = self.virta
        description = diaggen.load_project(tree.project, running_tree=tree.root)
        self.assertTrue(description.default)            # no diag.yaml: the generator's default description
        with self.assertRaises(diaggen.DiagGenError):
            diaggen.load_project(tree.project)

    def test_bswgen_nvm_partition_takes_the_running_tree(self):
        """bswgen's second entry into nxconfig.check (show_nvm_partition, NVM-07) hands it down too.

        Without the parameter the pin is refused and the project is never read; with it the command gets as far
        as its own answer - this project has no partition. Both return 1, so the output is what tells them
        apart.
        """
        tree = self.virta
        out = io.StringIO()
        self.assertEqual(bswgen.show_nvm_partition(tree.project, out=out, running_tree=tree.root), 1)
        self.assertIn("no nvm.partition", out.getvalue())
        out = io.StringIO()
        self.assertEqual(bswgen.show_nvm_partition(tree.project, out=out), 1)
        self.assertEqual(out.getvalue(), "")

    # ------------------------------------------------------------------ 3. the folders follow the tree

    def test_derivative_and_modules_come_from_the_running_tree(self):
        """bswgen carried the derivative database and the module folders as default values bound at import
        (`nxconfig.MCUDB`, `modules.MODULE_DIRS`); they now follow the running tree.

        `_checked` is the shared internal of bswgen.validate() and bswgen.generate() and the only way to see
        the configuration a validate run worked with.
        """
        for tree, other in ((self.aalto, self.virta), (self.virta, self.aalto)):
            with self.subTest(tree=tree.name):
                config, _messages = bswgen._checked(tree.project, None, None, running_tree=tree.root)
                self.assertIsNotNone(config)
                self.assertEqual(config.running_tree, tree.root)
                self.assertEqual(config.derivative_file,
                                 tree.root / "mcudb" / FAMILY / f"{tree.derivative}.yaml")
                self.assertIn(tree.family_module, config.modules.manifests)
                self.assertIn(tree.board_module, config.modules.manifests)
                self.assertNotIn(other.board_module, config.modules.manifests)

    def test_the_new_defaults_are_the_old_ones(self):
        """The folders the three tools now derive from the running tree are, without one, exactly the values
        they carried before: `nxconfig.MCUDB`, `modules.MODULE_DIRS` and rtegen's `HERE.parents[1]`.

        This is the whole "default None behaves exactly as today" claim, written down where it can break.
        """
        self.assertEqual(nxcfg.mcudb_dir(None), nxconfig.MCUDB)
        self.assertEqual(nxcfg.module_dirs_of(None), bswgen.modules.MODULE_DIRS)
        self.assertEqual(nxcfg.tree_of(None), rtegen.HERE.parents[1])
        # and with a tree they are that tree's folders (rtegen reads the BSW task manifest from `repo`)
        self.assertEqual(nxcfg.mcudb_dir(self.aalto.root), self.aalto.root / "mcudb")
        self.assertEqual(nxcfg.tree_of(self.aalto.root), self.aalto.root)

    # ------------------------------------------------------------------ 4. no global, no lock

    def test_no_module_variable_is_moved_by_any_tool(self):
        """After a run of all four with another running tree, nxconfig's module variables are untouched.

        This is what the Studio's `as_tool_of` did instead (it moved `nxconfig.config.REPO` for the duration of
        a check), and the reason it needed a lock.
        """
        before = (nxcfg.REPO, nxcfg.MCUDB, nxcfg.MODULE_DIRS)
        for name in TOOLS_UNDER_TEST:
            tool_messages(name, self.virta.project, running_tree=self.virta.root)
            self.assertEqual((nxcfg.REPO, nxcfg.MCUDB, nxcfg.MODULE_DIRS), before, name)

    def test_four_tools_two_trees_at_the_same_time(self):
        """The four tools run **concurrently**, two of them on each tree, and each keeps its own tree.

        The Studio checks in a worker thread and calls all four in one block. With a module variable this
        needed `_TOOL_LOCK` around the whole block; with a parameter the four calls do not know about each
        other. Without the parameter (the state before TOOL-87) the two generators of the second tree would
        report the pin error here.
        """
        pairs = [("nxconfig", self.aalto), ("diaggen", self.virta),
                 ("bswgen", self.aalto), ("rtegen", self.virta)]
        results: dict[str, list] = {}
        failures: dict[str, BaseException] = {}
        barrier = threading.Barrier(len(pairs))

        def run(name: str, tree: Tree):
            try:
                barrier.wait(timeout=60)
                results[name] = tool_messages(name, tree.project, running_tree=tree.root)
            except BaseException as e:                  # noqa: BLE001 - a thread must not die silently
                failures[name] = e

        threads = [threading.Thread(target=run, args=pair) for pair in pairs]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=180)
            self.assertFalse(t.is_alive(), "a check did not finish")
        self.assertEqual(failures, {})
        for name, tree in pairs:
            with self.subTest(tool=name, tree=tree.name):
                self.assertEqual(self.pin_errors(results[name], tree), [], results[name])
                self.assertIn(BOARD_WARNINGS[0], [m.path for m in results[name]])

    # ------------------------------------------------------------------ 5. nothing changed for today's callers

    def test_default_is_the_tree_of_this_package(self):
        """A project of the tree itself keeps working through all four tools without the parameter - that is
        the promise "default None behaves exactly as today"."""
        # PLAT-62: the eval board project where it exists, else an open project of the tree (exported open tree)
        project = next(p for p in (nxcfg.REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml",
                                   nxcfg.REPO / "projects" / "buildtest_s32k148_min" / "project.yaml") if p.is_file())
        for name in TOOLS_UNDER_TEST:
            with self.subTest(tool=name):
                without = tool_messages(name, project)
                with_default = tool_messages(name, project, running_tree=nxcfg.REPO)
                self.assertEqual([str(m) for m in without], [str(m) for m in with_default])


if __name__ == "__main__":
    unittest.main()
