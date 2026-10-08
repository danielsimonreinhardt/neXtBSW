"""Host tests of the neXtBSW version identity (ARCH-55), the workspace pin nextbsw.yaml (ARCH-56) and the format
migrations (TOOL-66).

A synthetic workspace in %TEMP% (outside the repository) with its own board, derivative and module - a copy of the
synthetic test profile (testboard/testproject on TESTMCU64) under invented names, never a neXtBSW board as the
yardstick. The pin names this tree (the running one) by a relative path, as a submodule would.
"""

import contextlib
import dataclasses
import difflib
import io
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import nxconfig  # noqa: E402
from nxconfig import __main__ as cli  # noqa: E402
from nxconfig import config as nxcfg  # noqa: E402
from nxconfig import migrate as nxmigrate  # noqa: E402
from nxconfig import version as nxversion  # noqa: E402
from nxconfig import workspace as nxws  # noqa: E402
from nxconfig import yamledit  # noqa: E402

REPO = nxcfg.REPO
PROFILES = HERE / "test" / "profiles"
TEST_MCUDB = HERE / "test" / "mcudb"

DERIVATIVE = "ACMEMCU64"
BOARD = "acme_ctl_b1"
PROJECT = "acme_main"
MODULE = "acmeLevelMon"


def relpath(target: Path, start: Path) -> str:
    try:
        return Path(os.path.relpath(target, start)).as_posix()
    except ValueError:                              # other drive: an absolute path
        return target.as_posix()


class WorkspaceBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nxws-test-")).resolve()
        self.ws = self.tmp / "acme-ctl"
        self.version = nxversion.read_version(REPO)
        # own derivative: the synthetic TESTMCU64 under another name
        text = (TEST_MCUDB / "testfam" / "TESTMCU64.yaml").read_text(encoding="utf-8")
        self.write(f"mcudb/testfam/{DERIVATIVE}.yaml", text.replace("name: TESTMCU64", f"name: {DERIVATIVE}"))
        # own board on it
        text = (PROFILES / "boards" / "testboard" / "board.yaml").read_text(encoding="utf-8")
        self.write(f"boards/{BOARD}/board.yaml",
                   text.replace("name: testboard", f"name: {BOARD}").replace("mcu: TESTMCU64", f"mcu: {DERIVATIVE}"))
        # own project on the board
        text = (PROFILES / "projects" / "testproject" / "project.yaml").read_text(encoding="utf-8")
        self.write(f"projects/{PROJECT}/project.yaml",
                   text.replace("name: testproject", f"name: {PROJECT}").replace("../../boards/testboard",
                                                                               f"../../boards/{BOARD}"))
        # own module of the workspace (catalogue entry: no integration needed)
        self.write(f"libs/{MODULE}/module.yaml",
                   f"module: {MODULE}\nkind: library\ndescription: level monitor of the customer\n")
        self.pin()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, rel: str, text: str, root: Path | None = None) -> Path:
        file = (root or self.ws) / rel
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding="utf-8")
        return file

    def pin(self, version=None, path=None, search="search:\n  mcudb: [mcudb]\n  modules: [libs]\n  boards: [boards]\n",
            extra="") -> Path:
        path = path if path is not None else relpath(REPO, self.ws)
        return self.write("nextbsw.yaml",
                          "# synthetic workspace pin (test data)\nschema_version: 1\nnextbsw:\n"
                          f"  version: {version or self.version}\n  source: submodule\n  path: {path}\n"
                          f"  profile: open\n{extra}{search}")

    @property
    def project(self) -> Path:
        return self.ws / "projects" / PROJECT / "project.yaml"

    def check(self, mcudb=nxcfg.MCUDB):
        return nxconfig.check(self.project, mcudb)

    def errors(self, mcudb=nxcfg.MCUDB) -> list:
        return [m for m in self.check(mcudb)[1] if m.severity == "error"]

    def pin_error(self) -> nxconfig.Message:
        """The first error; it must be reported in nextbsw.yaml with a line."""
        errors = self.errors()
        self.assertTrue(errors, "expected an error")
        m = errors[0]
        self.assertEqual(m.file, self.ws / "nextbsw.yaml", str(m))
        self.assertIsNotNone(m.line, str(m))
        return m


class VersionIdentity(unittest.TestCase):
    """ARCH-55: VERSION is the only source."""

    def test_tree_version(self):
        version = nxversion.read_version(REPO)
        self.assertTrue(nxversion.is_version(version), version)
        self.assertEqual(nxconfig.nextbsw_version(), version)
        self.assertEqual(nxversion.parse(version), tuple(int(p) for p in version.split(".")))

    def test_cmake_reads_version_file(self):
        """project(VERSION) comes from VERSION, no second literal version in the root CMakeLists.txt."""
        text = (REPO / "CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn('file(STRINGS "${NX_ROOT}/VERSION" NX_VERSION', text)
        project = re.search(r"project\(\s*\"neXtBSW\"(.*?)\)", text, re.S).group(1)
        self.assertIn("VERSION ${NX_VERSION}", project)
        self.assertIsNone(re.search(r"VERSION \d", project))

    # PLAT-62: the Studio is not distributed (F12) - the exported open tree has no tools/studio
    @unittest.skipUnless((REPO / "tools" / "studio" / "core" / "project.py").is_file(),
                         "tools/studio not in this tree (exported open tree, PLAT-62)")
    def test_studio_reads_the_same_version(self):
        text = (REPO / "tools" / "studio" / "core" / "project.py").read_text(encoding="utf-8")
        self.assertIn("nxconfig.nextbsw_version(self.repo)", text)

    def test_malformed_versions(self):
        tmp = Path(tempfile.mkdtemp(prefix="nxver-test-"))
        try:
            for text in ("1.2\n", "1.2.3.4\n", "v1.2.3\n", "1.02.3\n", "1.2.3-rc.1\n", "1.2.3\n1.2.4\n", "\n"):
                with self.subTest(text=text):
                    (tmp / "VERSION").write_text(text, encoding="utf-8")
                    with self.assertRaises(nxversion.VersionError):
                        nxversion.read_version(tmp)
            (tmp / "VERSION").write_bytes(b"10.0.7\r\n")                 # CRLF checkout on Windows
            self.assertEqual(nxversion.read_version(tmp), "10.0.7")
            (tmp / "VERSION").unlink()
            with self.assertRaisesRegex(nxversion.VersionError, "cannot read"):
                nxversion.read_version(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_tags(self):
        self.assertEqual(nxversion.tag_of("1.4.2"), "v1.4.2")
        self.assertEqual(nxversion.version_of_tag("v1.4.2"), "1.4.2")
        for tag in ("1.4.2", "v1.4", "release-1.4.2", "v1.4.2-rc1"):
            self.assertIsNone(nxversion.version_of_tag(tag), tag)

    def test_cli_version(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["version"]), 0)
        text = out.getvalue()
        self.assertIn(f"neXtBSW {nxversion.read_version(REPO)}", text)
        self.assertIn("git describe:", text)


class Discovery(WorkspaceBase):
    """Walk-up from project.yaml, stop at a neXtBSW tree or a git repository, fallback to the running tree."""

    def test_walk_up_from_project(self):
        pin = self.ws / "nextbsw.yaml"
        self.assertEqual(nxws.find_pin_file(self.project), pin)
        self.assertEqual(nxws.find_pin_file(self.project.parent), pin)
        self.assertEqual(nxws.find_pin_file(self.ws), pin)
        deep = self.ws / "projects" / PROJECT / "swc" / "LevelMon"
        deep.mkdir(parents=True)
        self.assertEqual(nxws.find_pin_file(deep), pin)

    def test_projects_of_the_tree_have_no_pin(self):
        """Development case: projects/ of the neXtBSW repository keeps working unchanged (no nextbsw.yaml)."""
        self.assertFalse((REPO / nxws.PIN_FILE).exists())
        projects = sorted((REPO / "projects").glob("*/project.yaml"))
        self.assertTrue(projects)
        for project in projects:
            self.assertIsNone(nxws.find_pin_file(project), project)

    def test_walk_stops_at_a_tree(self):
        """A project of a neXtBSW tree inside the workspace (nextbsw/projects/...) belongs to that tree."""
        tree = self.ws / "nextbsw"
        self.write("VERSION", f"{self.version}\n", tree)
        (tree / "tools" / "nxconfig").mkdir(parents=True)
        example = self.write("projects/example/project.yaml", "schema_version: 1\n", tree)
        self.assertTrue(nxws.is_tree(tree))
        self.assertIsNone(nxws.find_pin_file(example))

    def test_walk_stops_at_a_git_repository(self):
        """A repository without nextbsw.yaml is no workspace, even below a folder that has one."""
        inner = self.ws / "vendor" / "other"
        (inner / ".git").mkdir(parents=True)
        project = self.write("projects/p/project.yaml", "schema_version: 1\n", inner)
        self.assertIsNone(nxws.find_pin_file(project))

    def test_without_pin_the_running_tree(self):
        (self.ws / "nextbsw.yaml").unlink()
        reporter = nxcfg.Reporter()
        self.assertEqual(nxws.discover(self.project, reporter, REPO), (False, None))
        # the board's derivative is only in the workspace: without the pin it is not found
        errors = self.errors()
        self.assertEqual(len(errors), 1)
        self.assertIn(f"no derivative description {DERIVATIVE}.yaml in {nxcfg.MCUDB}", errors[0].text)


class WorkspaceCheck(WorkspaceBase):
    """A project of the synthetic workspace with its own board, derivative and module."""

    def test_project_of_the_workspace_checks_clean(self):
        config, messages = self.check()
        self.assertIsNotNone(config, "\n".join(str(m) for m in messages))
        self.assertEqual([str(m) for m in messages if m.severity == "error"], [])
        self.assertEqual(config.workspace.file, self.ws / "nextbsw.yaml")
        self.assertEqual(config.workspace.tree, REPO.resolve())
        self.assertEqual(config.derivative_file, self.ws / "mcudb" / "testfam" / f"{DERIVATIVE}.yaml")
        self.assertEqual(config.board.board.name, BOARD)
        # workspace module first, the tree's modules still there
        self.assertEqual(config.modules.files[MODULE], self.ws / "libs" / MODULE / "module.yaml")
        self.assertIn("canPhyPassive", config.modules.manifests)
        self.assertEqual(config.workspace.board_dirs(), (self.ws / "boards", REPO.resolve() / "boards"))
        settings, _ = nxcfg.build_settings(config)
        self.assertIn((self.ws / "nextbsw.yaml").as_posix(), settings["NX_CONFIG_FILES"])
        self.assertIn(config.derivative_file.as_posix(), settings["NX_CONFIG_FILES"])

    def test_search_paths_are_optional(self):
        """A workspace that only uses the tree's derivatives: the tree's mcudb is still searched."""
        text = (self.ws / "boards" / BOARD / "board.yaml").read_text(encoding="utf-8")
        self.write(f"boards/{BOARD}/board.yaml", text.replace(f"mcu: {DERIVATIVE}", "mcu: TESTMCU64"))
        self.pin(search="")
        config, messages = self.check(TEST_MCUDB)
        self.assertIsNotNone(config, "\n".join(str(m) for m in messages))
        self.assertEqual(config.workspace.mcudb, ())
        self.assertEqual(config.derivative_file, TEST_MCUDB / "testfam" / "TESTMCU64.yaml")

    def test_cli_version_shows_the_pin(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["version", str(self.project)]), 0)
        text = out.getvalue()
        self.assertIn(f"workspace:    {self.ws}", text)
        self.assertIn(f"pin:          {self.version}, submodule", text)
        self.assertIn("search.mcudb:", text)

    def test_cli_check(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["check", str(self.project)]), 0, err.getvalue())
        self.assertIn(f"{PROJECT}: ok (board {BOARD}, {DERIVATIVE}", out.getvalue())


class PinFindings(WorkspaceBase):
    """Every finding of the pin in nextbsw.yaml with line and YAML path."""

    def test_version_mismatch_is_an_error(self):
        self.pin(version="99.0.0")
        m = self.pin_error()
        self.assertEqual(m.path, ("nextbsw", "version"))
        self.assertIn("99.0.0", m.text)
        self.assertIn(f"is {self.version}", m.text)
        self.assertIn("v99.0.0", m.text)

    def test_schema(self):
        cases = {
            "version: 1.4": ("nextbsw", "version"),             # YAML float, not a string
            "version: 1.4.x": ("nextbsw", "version"),
            "source: git": ("nextbsw", "source"),
            "profile: public": ("nextbsw", "profile"),
        }
        for line, path in cases.items():
            with self.subTest(line=line):
                key = line.split(":")[0]
                pin = self.pin().read_text(encoding="utf-8")
                self.write("nextbsw.yaml", re.sub(rf"^  {key}: .*$", f"  {line}", pin, flags=re.M))
                m = self.pin_error()
                self.assertEqual(m.path, path, str(m))

    def test_no_fourth_configuration_level(self):
        """ECU values do not belong into the pin: unknown top-level keys are errors."""
        self.pin(extra="clock:\n  system_hz: 48000000\n")
        m = self.pin_error()
        self.assertEqual(m.path, ("clock",))

    def test_archive_needs_url_and_sha256(self):
        pin = (self.ws / "nextbsw.yaml").read_text(encoding="utf-8").replace("source: submodule", "source: archive")
        self.write("nextbsw.yaml", pin)
        m = self.pin_error()
        self.assertEqual(m.path, ("nextbsw",))
        self.assertIn("url and sha256", m.text)
        self.write("nextbsw.yaml", pin.replace("  profile: open\n", "  profile: open\n  url: https://example.invalid/"
                                               "nextbsw.zip\n  sha256: " + "ab" * 32 + "\n"))
        config, messages = self.check()
        self.assertIsNotNone(config, "\n".join(str(m) for m in messages))

    def test_submodule_takes_no_archive_fields(self):
        self.pin(extra="")
        pin = (self.ws / "nextbsw.yaml").read_text(encoding="utf-8")
        self.write("nextbsw.yaml", pin.replace("  profile: open\n", "  profile: open\n  sha256: " + "cd" * 32 + "\n"))
        self.assertIn("belong to source archive", self.pin_error().text)

    def test_tree_not_found(self):
        self.pin(path="nextbsw")
        m = self.pin_error()
        self.assertEqual(m.path, ("nextbsw", "path"))
        self.assertIn("git submodule update --init --recursive", m.text)

    def test_folder_is_no_tree(self):
        (self.ws / "nextbsw").mkdir()
        self.pin(path="nextbsw")
        self.assertIn("is no neXtBSW tree", self.pin_error().text)

    def test_other_tree_than_the_running_one(self):
        tree = self.ws / "nextbsw"
        self.write("VERSION", f"{self.version}\n", tree)
        (tree / "tools" / "nxconfig").mkdir(parents=True)
        self.pin(path="nextbsw")
        m = self.pin_error()
        self.assertEqual(m.path, ("nextbsw", "path"))
        self.assertIn("this tool runs from", m.text)

    def test_search_path_not_found(self):
        self.pin(search="search:\n  mcudb: [mcudb, derivatives]\n")
        m = self.pin_error()
        self.assertEqual(m.path, ("search", "mcudb", 1))
        self.assertIn("derivatives not found", m.text)

    def test_search_path_inside_the_tree(self):
        inside = relpath(REPO / "mcudb", self.ws)
        self.pin(search=f"search:\n  mcudb: [mcudb, '{inside}']\n")
        m = self.pin_error()
        self.assertEqual(m.path, ("search", "mcudb", 1))
        self.assertIn("inside the neXtBSW tree", m.text)

    def test_duplicate_derivative_names_both_paths(self):
        tree_db = self.tmp / "treedb"
        shutil.copytree(self.ws / "mcudb", tree_db)
        errors = self.errors(tree_db)
        self.assertEqual(len(errors), 1, [str(m) for m in errors])
        m = errors[0]
        self.assertEqual((m.file, m.path), (self.ws / "nextbsw.yaml", ("search", "mcudb", 0)))
        self.assertIn(str(self.ws / "mcudb" / "testfam" / f"{DERIVATIVE}.yaml"), m.text)
        self.assertIn(str(tree_db / "testfam" / f"{DERIVATIVE}.yaml"), m.text)

    def test_duplicate_module_names_both_paths(self):
        tree_module = REPO / "libs" / "bsw" / "canPhyPassive" / "module.yaml"
        own = self.write("libs/canPhyPassive/module.yaml", tree_module.read_text(encoding="utf-8"))
        errors = [m for m in self.errors() if "canPhyPassive" in m.text]
        self.assertTrue(errors)
        m = errors[0]
        self.assertEqual({m.file, *[p for p in (own, tree_module) if str(p) in m.text]}, {own, tree_module})
        self.assertEqual(m.path, ("module",))

    def test_duplicate_board_name_both_paths(self):
        """The board name selects the board port: a workspace board must not reuse a name of the tree."""
        tree_board = sorted(p.parent for p in (REPO / "boards").glob("*/board.yaml"))[0]
        text = (self.ws / "boards" / BOARD / "board.yaml").read_text(encoding="utf-8")
        board_file = self.write(f"boards/{BOARD}/board.yaml", text.replace(f"name: {BOARD}", f"name: {tree_board.name}"))
        errors = self.errors()
        self.assertEqual(len(errors), 1, [str(m) for m in errors])
        m = errors[0]
        self.assertEqual((m.file, m.path), (board_file, ("board", "name")))
        self.assertIn(str(tree_board), m.text)
        self.assertIn(str(board_file.parent), m.text)


# --------------------------------------------------------------------------------------- TOOL-66: migrations

# Synthetic ECU description of the workspace: invented component and DBC, and deliberately full of the things a
# migration must not destroy - a header comment, comments between the entries, aligned trailing comments, a flow
# entry with spaces inside the braces, quoted text and blank lines.
RTE_TEXT = """\
# ECU description of the customer workspace (test data - invented component, no neXtBSW board).
# Paths are relative to the root of the workspace today (rtegen --repo).

tasks:
  - name: level10
    period_ms: 10
    priority: 25            # between bsp (20) and can (40)
    stack_bytes: 1024
    runnables: [LevelMonSwc.Run10ms]

# The component lives in the workspace, not in the neXtBSW tree.
components:
  - { name: LevelMonSwc, path: swc/LevelMonSwc }   # flow style, spaces inside the braces

can:
  dbc: projects/acme_main/acme.dbc     # DBC of the ECU
  node: "ACME_CTL"
  bus: VEH
"""


def demo_step(kind: str = "project", to_version: int = 2) -> nxmigrate.Step:
    """A synthetic format step for the tests: project.description becomes project.purpose."""

    def concerns(doc, ctx):
        return doc.has("project", "description")

    def change(doc, ctx):
        doc.rename(("project", "description"), "purpose")

    return nxmigrate.Step(kind, to_version, "project: description becomes purpose",
                          "synthetic step of the host tests", change, concerns)


def registry(kind: str = "project", reads: int = 2, steps=None):
    """A patched registry: `kind` is read at `reads`, `steps` are the registered steps."""
    kinds = dict(nxmigrate.KINDS)
    kinds[kind] = dataclasses.replace(kinds[kind], reads=reads)
    return mock.patch.multiple(nxmigrate, KINDS=kinds, LEVELS={k.level: k for k in kinds.values() if k.level},
                               STEPS=list(steps if steps is not None else [demo_step(kind, reads)]))


class YamlEditing(unittest.TestCase):
    """yamledit: a change touches its line and nothing else - that is what keeps comments and layout."""

    def edit(self, text: str, change) -> str:
        doc = yamledit.YamlFile.of(Path("test.yaml"), text)
        change(doc)
        return doc.result()

    def test_untouched_file_is_returned_byte_for_byte(self):
        # PLAT-62: the eval board files where they exist (private), the commented open profiles in every tree
        for name in ("projects/evalboard_s32k144_demo/project.yaml", "boards/evalboard_s32k144/board.yaml",
                     "boards/s32k116evb/board.yaml", "tools/nxconfig/test/profiles/boards/testboard_full/board.yaml",
                     "mcudb/s32k1xx/S32K148.yaml"):
            if not (REPO / name).is_file():
                continue
            with self.subTest(name=name):
                text = (REPO / name).read_text(encoding="utf-8")
                self.assertEqual(yamledit.YamlFile.of(Path(name), text).result(), text)

    def test_set_keeps_the_trailing_comment_and_its_column(self):
        text = "a:\n  b: 1            # why\n  c: 2\n"
        out = self.edit(text, lambda d: d.set(("a", "b"), 42))
        self.assertEqual(out, "a:\n  b: 42           # why\n  c: 2\n")
        self.assertEqual(out.index("#"), text.index("#"))

    def test_set_in_a_flow_mapping_changes_only_the_value(self):
        text = "items:\n  - { name: A, path: swc/A }   # a component\n"
        out = self.edit(text, lambda d: d.set(("items", 0, "path"), "../../swc/A"))
        self.assertEqual(out, "items:\n  - { name: A, path: ../../swc/A }   # a component\n")

    def test_set_quoted_text_stays_quoted_style(self):
        text = 'a: "old text"  # note\n'
        out = self.edit(text, lambda d: d.set(("a",), "new text"))
        self.assertEqual(out, 'a: "new text"  # note\n')

    def test_set_refuses_a_block_scalar(self):
        doc = yamledit.YamlFile.of(Path("t.yaml"), "a: >\n  folded\n  text\n")
        with self.assertRaisesRegex(yamledit.EditError, "block scalar"):
            doc.set(("a",), "x")

    def test_add_goes_first_and_keeps_the_header_comment(self):
        text = "# header\n# second line\n\nproject:\n  name: p\n"
        out = self.edit(text, lambda d: d.add(("schema_version",), 2))
        self.assertEqual(out, "# header\n# second line\n\nschema_version: 2\n\nproject:\n  name: p\n")

    def test_add_refuses_a_key_that_is_there(self):
        doc = yamledit.YamlFile.of(Path("t.yaml"), "schema_version: 1\na: 1\n")
        with self.assertRaisesRegex(yamledit.EditError, "already exists"):
            doc.add(("schema_version",), 2)

    def test_an_empty_collection_can_only_be_written_in_flow(self):
        """The ground TOOL-95 stands on: ruamel drops the flow marker of `[]` and `{}`, and the length replaces it.

        A key with nothing under it is not an empty collection but None, so YAML has no block spelling for one:
        a loaded collection of length 0 can only have come from `[]` or `{}`. Held against ruamel, not claimed.
        """
        doc = yamledit.YamlFile.of(Path("t.yaml"), "items: []\nmap: {}\nfull: [a, b]\nfmap: {a: 1}\nnothing:\n")
        empty_seq, empty_map = doc.get("items"), doc.get("map")
        self.assertEqual((empty_seq, empty_map), ([], {}))            # collections, both of length 0
        self.assertIsNone(empty_seq.fa.flow_style())                  # ruamel itself says nothing ...
        self.assertIsNone(empty_map.fa.flow_style())
        self.assertIs(doc.get("nothing"), None)                       # a key without a value is not a collection
        self.assertTrue(yamledit.is_flow(empty_seq))                  # ... the length answers instead
        self.assertTrue(yamledit.is_flow(empty_map))
        self.assertTrue(yamledit.is_flow(doc.get("full")))            # the filled ones keep their answer
        self.assertTrue(yamledit.is_flow(doc.get("fmap")))
        self.assertFalse(yamledit.is_flow(doc.get("nothing")))
        self.assertFalse(yamledit.is_flow([]))                        # never loaded: no `fa`, no claim
        self.assertFalse(yamledit.is_flow("text"))

    def test_add_into_an_empty_flow_mapping_names_the_flow_line(self):
        """It was refused before and it stays refused - but with the reason that is really there.

        `add()` inserts a line into a block; `map: {}` has no lines of its own, exactly like `map: {a: 1}`.
        Until TOOL-95 the empty one was reported as "is empty", which points at a missing value instead.
        """
        for text in ("map: {}\nrest: 1\n", "map: {a: 1}\nrest: 1\n"):
            with self.subTest(text=text):
                doc = yamledit.YamlFile.of(Path("t.yaml"), text)
                with self.assertRaisesRegex(yamledit.EditError, "written in flow style"):
                    doc.add(("map", "new"), 1)
                self.assertEqual(doc.result(), text)                  # a refused edit changes nothing

    def test_a_flow_collection_is_still_edited_in_its_line(self):
        """The counter-check: the length criterion must not disturb a collection that has entries."""
        text = "items:\n  - { name: A, path: swc/A }\nmap: {a: 1}\n"
        self.assertEqual(self.edit(text, lambda d: d.set(("items", 0, "path"), "swc/B")),
                         "items:\n  - { name: A, path: swc/B }\nmap: {a: 1}\n")
        doc = yamledit.YamlFile.of(Path("t.yaml"), text)
        with self.assertRaisesRegex(yamledit.EditError, "flow collection"):
            doc.remove(("map", "a"))

    def test_remove_takes_the_whole_block(self):
        text = "a:\n  x: 1\n  y: 2\nb: 3\n"
        self.assertEqual(self.edit(text, lambda d: d.remove(("a",))), "b: 3\n")

    def test_rename_keeps_value_and_comment(self):
        text = "project:\n  description: hello   # text\n"
        self.assertEqual(self.edit(text, lambda d: d.rename(("project", "description"), "purpose")),
                         "project:\n  purpose: hello   # text\n")

    def test_crlf_and_a_missing_final_newline_survive(self):
        doc = yamledit.YamlFile.of(Path("t.yaml"), "a: 1\r\nb: 2")
        doc.set(("a",), 5)
        self.assertEqual(doc.result(), "a: 5\r\nb: 2")

    def test_two_changes_of_one_line_are_refused(self):
        doc = yamledit.YamlFile.of(Path("t.yaml"), "a: 1\n")
        doc.set(("a",), 2)
        with self.assertRaisesRegex(yamledit.EditError, "touch line 1"):
            doc.set(("a",), 3)

    def test_a_written_value_comes_back_as_the_same_string(self):
        """TOOL-98: `represent()` must not write text that loads back as another type - or not at all.

        Measured before the change, writing the value and loading the file again: `'null'` came back as None and
        `'123'` as the int 123, and `'abc:'` and `'- x'` produced a file that raised a ScannerError. No migration
        step writes such a value today (they write relative posix paths and an int), so nothing was broken - but
        the next step that writes a string an author typed would have destroyed the file silently, and a
        migration is the moment one notices that last. The rule is `yamldoc._quote_needed()` from the Studio,
        taken over rather than invented a second time.
        """
        for value in ("null", "123", "abc:", "- x", "~", "yes", "1.5", "-3", "", "#x", "a: b"):
            with self.subTest(value=value):
                doc = yamledit.YamlFile.of(Path("t.yaml"), "a: x\n")
                doc.set(("a",), value)
                text = doc.result()
                back = yamledit.yaml().load(text)          # raises if the file no longer parses
                self.assertEqual(back["a"], value)
                self.assertIsInstance(back["a"], str)

    def test_an_ordinary_value_is_still_written_without_quotes(self):
        """The counter-check to the one above: quoting everything would trade one problem for a worse one.

        These are the values migrations really write - relative posix paths and plain names. They must stay
        readable, or the fix has made every migrated file harder to review than the problem it prevents.
        """
        for value in ("swc/A/A.c", "../../swc/A", "boards/evalboard_s32k144", "hello world", "MyComponent"):
            with self.subTest(value=value):
                out = self.edit("a: x\n", lambda d, v=value: d.set(("a",), v))
                self.assertEqual(out, f"a: {value}\n")
                self.assertEqual(yamledit.yaml().load(out)["a"], value)
        # and `add()` writes through the same function
        self.assertEqual(self.edit("a: 1\n", lambda d: d.add(("path",), "swc/A")), "path: swc/A\na: 1\n")
        self.assertEqual(self.edit("a: 1\n", lambda d: d.add(("v",), "null")), 'v: "null"\na: 1\n')

    def test_rewrite_is_marked(self):
        doc = yamledit.YamlFile.of(Path("t.yaml"), "a:\n  - { x: 1 }\n")
        doc.tree["b"] = 2
        doc.rewrite("restructured")
        self.assertTrue(doc.reformatted)
        self.assertIn("b: 2", doc.result())


class MigrationRegistry(unittest.TestCase):
    """The registry must not drift from the schemas, and the step chains must have no gap."""

    def test_reads_equals_the_literal_of_the_schema(self):
        import typing
        for kind in nxmigrate.KINDS.values():
            if kind.model is None:
                continue
            with self.subTest(kind=kind.name):
                field = kind.model.model_fields.get(kind.key)
                if field is None:
                    # a format that does not carry the key yet (rte.yaml until N3) can only be version 1
                    self.assertTrue(kind.optional, f"{kind.name}: {kind.key} is not in {kind.model.__name__}")
                    self.assertEqual(kind.reads, 1, f"{kind.name}: a format without the key cannot be read at "
                                                    f"{kind.reads}")
                    continue
                self.assertEqual(typing.get_args(field.annotation), (kind.reads,),
                                 f"{kind.name}: model.py and migrate.KINDS disagree about the format version")

    def test_swc_uses_the_key_and_version_of_tools_swc(self):
        sys.path.insert(0, str(REPO / "tools" / "swc"))
        import swc_yaml
        self.assertEqual(nxmigrate.KINDS["swc"].reads, swc_yaml.SCHEMA_VERSION)
        self.assertEqual(nxmigrate.KINDS["swc"].file, swc_yaml.FILE_NAME)

    def test_every_step_is_named_by_its_target_version_without_a_gap(self):
        for kind in nxmigrate.KINDS.values():
            versions = [s.to_version for s in nxmigrate.steps_of(kind.name)]
            with self.subTest(kind=kind.name):
                self.assertEqual(versions, sorted(set(versions)), "two steps produce the same version")
                self.assertEqual(versions, list(range(2, 2 + len(versions))), "the chain starts at 2 and has no gap")
                self.assertTrue(all(s.id == f"{s.kind}:{s.to_version}" for s in nxmigrate.steps_of(kind.name)))

    def test_levels_match_the_reporter_levels_of_config(self):
        for level in nxcfg.LEVELS + (nxcfg.RTE_LEVEL, nxcfg.DIAG_LEVEL, nxws.WORKSPACE_LEVEL):
            self.assertIn(level, nxmigrate.LEVELS, f"level {level} has no format in migrate.KINDS")

    def test_the_tree_itself_needs_no_migration(self):
        """Every configuration file of this repository is in the format this tree reads."""
        plan = nxmigrate.plan(REPO, REPO)
        self.assertEqual([f"{f.file}: {f.problem}" for f in plan.problems], [])
        self.assertEqual([str(f.file) for f in plan.todo], [])
        self.assertGreater(len(plan.files), 40, "the walk found almost nothing - the collection is broken")
        kinds = {f.kind.name for f in plan.files}
        self.assertLessEqual({"project", "board", "derivative", "rte", "diag", "swc", "pinmux"}, kinds)

    def test_list_names_every_step_with_its_state(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["migrate", "--list"]), 0)
        text = out.getvalue()
        # RTE-22 released rte:2, so it is no longer prepared: the tree reads format 2 and the step runs on its own
        self.assertRegex(text, r"rte\s+rte\.yaml\s+schema_version 2")
        self.assertRegex(text, r"rte:2\s+.*\[runs automatically\]")
        self.assertRegex(text, r"swc\s+swc\.yaml\s+version 1")

    def test_a_prepared_step_is_shown_as_prepared(self):
        """A step whose version the tree does not read yet must say so - `--list` is where that is read off."""
        with registry("rte", reads=1, steps=[demo_step("rte", 2)]):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(cli.main(["migrate", "--list"]), 0)
            self.assertIn("prepared", out.getvalue())


class Migrations(WorkspaceBase):
    """nxconfig migrate on the synthetic workspace: dry run, backup, comments, a step end to end."""

    def setUp(self):
        super().setUp()
        self.rte = self.write(f"projects/{PROJECT}/rte.yaml", RTE_TEXT)
        text = self.project.read_text(encoding="utf-8")
        self.write(f"projects/{PROJECT}/project.yaml",
                   text.replace(f"  name: {PROJECT}\n", f"  name: {PROJECT}\n  description: level control\n"))

    def plan(self, steps=(), path=None):
        return nxmigrate.plan(path or self.ws, REPO, tuple(steps))

    def test_the_workspace_is_current_and_every_file_is_found(self):
        plan = self.plan()
        self.assertEqual([f"{f.file}: {f.problem}" for f in plan.problems], [])
        # RTE-22: the ECU description of the fixture is deliberately still format 1 - it is what the step rte:2 is
        # exercised on below. Every other file of the workspace is in the format this tree reads.
        self.assertEqual([f.file for f in plan.todo], [self.rte])
        found = {f.kind.name: f.file for f in plan.files}
        self.assertEqual(found["workspace"], self.ws / "nextbsw.yaml")
        self.assertEqual(found["project"], self.project)
        self.assertEqual(found["board"], self.ws / "boards" / BOARD / "board.yaml")
        self.assertEqual(found["derivative"], self.ws / "mcudb" / "testfam" / f"{DERIVATIVE}.yaml")
        self.assertEqual(found["rte"], self.rte)

    def test_the_tree_of_the_workspace_is_not_walked(self):
        """A neXtBSW tree inside the workspace brings its own files; its release migrates them, not this run."""
        tree = self.ws / "nextbsw"
        self.write("VERSION", f"{self.version}\n", tree)
        (tree / "tools" / "nxconfig").mkdir(parents=True)
        self.write("projects/inner/project.yaml", "schema_version: 1\nproject:\n  name: inner\n", tree)
        self.assertNotIn(tree / "projects" / "inner" / "project.yaml", [f.file for f in self.plan().files])

    def test_dry_run_writes_nothing(self):
        before = self.rte.read_bytes()
        plan = self.plan(["rte:2"])
        item = next(f for f in plan.files if f.file == self.rte)
        self.assertTrue(item.migrates)
        self.assertIn("../../swc/LevelMonSwc", item.diff)
        self.assertEqual(self.rte.read_bytes(), before, "the dry run changed the file")
        self.assertFalse((self.ws / nxmigrate.BACKUP_DIR).exists(), "the dry run made a backup")

    def test_write_makes_a_backup_first_and_keeps_every_other_line(self):
        before = self.rte.read_text(encoding="utf-8")
        plan = self.plan(["rte:2"])
        folder, written = nxmigrate.apply(plan)
        self.assertEqual(written, [self.rte])
        self.assertEqual((folder / f"projects/{PROJECT}/rte.yaml").read_text(encoding="utf-8"), before,
                         "the backup is not the old file")
        self.assertTrue(folder.is_relative_to(self.ws / nxmigrate.BACKUP_DIR))
        after = self.rte.read_text(encoding="utf-8")
        # exactly the lines the step names: the two paths, plus the new schema_version with its blank line
        self.assertEqual(
            [line for line in difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0)
             if line[:1] in "+-" and line[:3] not in ("+++", "---")],
            ["+",
             "+schema_version: 2",
             "-  - { name: LevelMonSwc, path: swc/LevelMonSwc }   # flow style, spaces inside the braces",
             "+  - { name: LevelMonSwc, path: ../../swc/LevelMonSwc }   # flow style, spaces inside the braces",
             "-  dbc: projects/acme_main/acme.dbc     # DBC of the ECU",
             "+  dbc: acme.dbc" + " " * 24 + "# DBC of the ECU"])
        # the trailing comment kept its column although the value got shorter
        old_line = next(li for li in before.splitlines() if li.startswith("  dbc:"))
        new_line = next(li for li in after.splitlines() if li.startswith("  dbc:"))
        self.assertEqual(new_line.index("#"), old_line.index("#"))

    def test_migrated_file_is_valid_yaml_with_the_new_values(self):
        nxmigrate.apply(self.plan(["rte:2"]))
        doc = yamledit.YamlFile.read(self.rte)
        self.assertEqual(doc.get("schema_version"), 2)
        self.assertEqual(doc.get("components", 0, "path"), "../../swc/LevelMonSwc")
        self.assertEqual(doc.get("can", "dbc"), "acme.dbc")
        self.assertEqual(doc.get("can", "node"), "ACME_CTL")
        self.assertEqual(doc.get("tasks", 0, "priority"), 25)
        self.assertEqual(((self.rte.parent / doc.get("components", 0, "path")).resolve()),
                         (self.ws / "swc" / "LevelMonSwc").resolve())

    def test_a_second_run_finds_nothing_to_do(self):
        nxmigrate.apply(self.plan(["rte:2"]))
        again = self.plan(["rte:2"])
        self.assertEqual(again.todo, [])
        self.assertEqual(again.problems, [])

    def test_a_file_without_the_paths_only_gets_the_version(self):
        rte = self.write(f"projects/{PROJECT}/plain/rte.yaml", "# only tasks here\ntasks: []\n")
        item = next(f for f in self.plan(["rte:2"]).files if f.file == rte)
        self.assertEqual(item.changes[0][1], ["(this file has nothing the step changes)", "schema_version: added, 2"])
        self.assertIn("+schema_version: 2", item.diff)

    def test_a_synthetic_step_runs_through_plan_and_apply(self):
        """The registry is the extension point: a kind read at 2 plus its step migrates project.yaml."""
        with registry():
            plan = self.plan()
            item = next(f for f in plan.files if f.file == self.project)
            self.assertEqual([s.id for s in item.steps], ["project:2"])
            folder, written = nxmigrate.apply(plan)
        text = self.project.read_text(encoding="utf-8")
        self.assertIn("schema_version: 2", text)
        self.assertIn("purpose:", text)
        self.assertNotIn("description:", text)
        self.assertTrue((folder / f"projects/{PROJECT}/project.yaml").is_file())

    def test_too_new_schema_version_is_refused_with_the_reason(self):
        text = self.project.read_text(encoding="utf-8").replace("schema_version: 1", "schema_version: 2")
        self.write(f"projects/{PROJECT}/project.yaml", text)
        errors = self.errors()
        self.assertEqual(len(errors), 1, [str(m) for m in errors])
        m = errors[0]
        self.assertEqual((m.file, m.path), (self.project, ("schema_version",)))
        self.assertIsNotNone(m.line)
        self.assertIn("older than the files", m.text)
        self.assertIn("this neXtBSW reads 1", m.text)
        item = next(f for f in self.plan().files if f.file == self.project)
        self.assertIn("newer than this neXtBSW reads", item.problem)
        self.assertIsNone(item.new_text)

    def test_too_old_schema_version_names_file_line_path_and_command(self):
        with registry():
            errors = self.errors()
            self.assertEqual(len(errors), 1, [str(m) for m in errors])
            m = errors[0]
            self.assertEqual((m.file, m.path), (self.project, ("schema_version",)))
            self.assertEqual(m.line, 2)
            self.assertIn("project:2", m.text)
            self.assertIn(f"migrate {self.ws.as_posix()}", m.text)
            self.assertIn("--write", m.text)
            self.assertIn(nxmigrate.BACKUP_DIR, m.text)
            self.assertIn((REPO / "tools" / "nxconfig").as_posix(), m.text)

    def test_the_command_of_a_prepared_step_carries_the_step(self):
        """A step beyond what the tree reads is only run when it is named, so the command has to name it.

        RTE-22 made rte:2 the format this tree reads, so the prepared case needs a registry of its own again.
        """
        with registry("rte", reads=1, steps=[demo_step("rte", 2)]):
            steps = nxmigrate.chain(nxmigrate.KINDS["rte"], 1, ("rte:2",))
            self.assertTrue(steps and steps[0].prepared)
            self.assertIn("--step rte:2", nxmigrate.command(self.ws, REPO, steps))

    def test_a_broken_file_is_a_problem_not_a_crash(self):
        broken = self.write("projects/broken/project.yaml", "schema_version: 1\nproject: [\n")
        item = next(f for f in self.plan().files if f.file == broken)
        self.assertIn("invalid YAML", item.problem)
        self.assertNotIn(broken, [f.file for f in self.plan().todo], "a file that cannot be read is not migrated")

    def test_a_file_without_a_version_key_is_a_problem(self):
        bare = self.write("boards/bare/board.yaml", "board:\n  name: bare\n")
        item = next(f for f in self.plan().files if f.file == bare)
        self.assertIn("schema_version is missing", item.problem)

    def test_cli_dry_run_then_write(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(cli.main(["migrate", str(self.ws), "--step", "rte:2"]), 0, err.getvalue())
        text = out.getvalue()
        self.assertIn("dry run, nothing is written", text)
        self.assertIn("rte:2", text)
        self.assertIn("+schema_version: 2", text)
        self.assertIn("1 file(s) to migrate", text)
        self.assertEqual(self.rte.read_text(encoding="utf-8"), RTE_TEXT)

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["migrate", str(self.ws), "--step", "rte:2", "--write", "--no-diff"]), 0)
        self.assertIn("written; the old files are in", out.getvalue())
        self.assertNotEqual(self.rte.read_text(encoding="utf-8"), RTE_TEXT)
        backups = sorted((self.ws / nxmigrate.BACKUP_DIR).iterdir())
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / f"projects/{PROJECT}/rte.yaml").read_text(encoding="utf-8"), RTE_TEXT)

    def test_cli_reports_an_unknown_step_and_a_missing_path(self):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            with self.assertRaises(SystemExit):
                cli.main(["migrate", str(self.ws), "--step", "rte:99"])
        self.assertIn("unknown step", err.getvalue())
        with contextlib.redirect_stderr(io.StringIO()) as err, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["migrate", str(self.ws / "nowhere")]), 1)
        self.assertIn("does not exist", err.getvalue())

    def test_cli_returns_one_for_a_file_it_cannot_read(self):
        self.write("projects/broken/project.yaml", "schema_version: 1\nproject: [\n")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["migrate", str(self.ws), "--no-diff"]), 1)
        self.assertIn("invalid YAML", out.getvalue())

    def test_a_single_file_can_be_migrated(self):
        plan = self.plan(["rte:2"], path=self.rte)
        self.assertEqual([f.file for f in plan.files], [self.rte])
        self.assertEqual(plan.root, self.ws, "the base of the old paths must be the workspace, not the folder")

    def test_a_folder_of_the_workspace_keeps_the_workspace_as_base(self):
        plan = self.plan(["rte:2"], path=self.rte.parent)
        self.assertEqual(sorted(f.file for f in plan.files), sorted([self.project, self.rte]))
        self.assertEqual(plan.root, self.ws)
        item = next(f for f in plan.files if f.file == self.rte)
        self.assertIn("path: ../../swc/LevelMonSwc", item.new_text)

    def test_a_project_folder_named_like_a_build_directory_is_not_skipped(self):
        """RTE-22: the walk pruned every folder starting with `build`, so `buildtest_s32k148_min` of this tree
        kept format 1 while all its neighbours were migrated. Only real build output may be pruned."""
        kept = self.write("buildtest_acme/project.yaml", "schema_version: 1\nproject:\n  name: buildtest_acme\n")
        rte = self.write("buildtest_acme/rte.yaml", RTE_TEXT)
        # real build output: the folder itself holds no configuration, so it stays pruned with everything below it
        self.write("build/CMakeCache.txt", "CMAKE_PROJECT_NAME:STATIC=acme\n")
        pruned = self.write("build/generated/project.yaml", "schema_version: 1\nproject:\n  name: generated\n")
        found = [f.file for f in self.plan().files]
        self.assertIn(kept, found, "a project whose name starts with 'build' was pruned")
        self.assertIn(rte, found)
        self.assertNotIn(pruned, found, "real build output was walked")


class PathsRelativeToTheFile(WorkspaceBase):
    """RTE-22 (gap N3): an ECU description outside the tree finds its components and its DBC.

    The workspace lies in %TEMP% and has no repository root the old paths could have been based on. The component
    is reached with `../..`, so the test would also fail if the paths were silently resolved against the workspace
    root instead of against the rte.yaml.
    """

    def setUp(self):
        super().setUp()
        sys.path.insert(0, str(REPO / "tools" / "rtegen"))
        import rtegen
        self.rtegen = rtegen
        # the component lives beside the project, not below it: swc/<name> of the workspace. The two synthetic ARXML
        # components of the RTE-09 test (written by hand, open) instead of the Simulink test model TypeSwc, whose
        # generated code is not exported (PLAT-119) - so the case runs in the exported tree too (TOOL-245)
        source = REPO / "tools" / "rtegen" / "test" / "implicit"
        for name in ("ProducerSwc", "ConsumerSwc"):
            shutil.copytree(source / name, self.ws / "swc" / name)
        (self.ws / "can").mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / "tools" / "rtegen" / "test" / "can_host" / "qualifier.dbc", self.ws / "can" / "acme.dbc")
        text = (source / "rte.yaml").read_text(encoding="utf-8")
        text = text.replace("path: ProducerSwc", "path: ../../swc/ProducerSwc") \
                   .replace("path: ConsumerSwc", "path: ../../swc/ConsumerSwc")
        self.assertEqual(text.count("path: ../../swc/"), 2, "the fixture changed: both components must be moved")
        self.rte = self.write(f"projects/{PROJECT}/rte.yaml", text)

    def test_the_components_are_found_next_to_the_description_not_under_a_repository_root(self):
        ecu = self.rtegen.load_ecu(self.rte)
        self.assertEqual(self.rtegen.paths_base(ecu, self.rte, REPO), self.rte.parent)
        self.assertEqual(len(ecu.components), 2)
        for entry in ecu.components:
            with self.subTest(component=entry.name):
                # path_of is what the generator resolves with: absolute and normalised, no `..` left in the middle
                folder = self.rtegen.path_of(ecu, self.rte, REPO, entry.path)
                self.assertNotIn("..", folder.parts, f"{entry.name}: {folder} is not normalised")
                self.assertTrue((folder / "generated" / "arxml").is_dir(), f"{entry.name}: {folder} not found")
                self.assertTrue(folder.is_relative_to(self.ws), "resolved outside the workspace")

    def test_the_dbc_is_found_relative_to_the_description(self):
        text = self.rte.read_text(encoding="utf-8") + "\ncan:\n  dbc: ../../can/acme.dbc\n  node: EVB\n  bus: VEH\n"
        self.rte.write_text(text, encoding="utf-8")
        ecu = self.rtegen.load_ecu(self.rte)
        dbc = self.rtegen.path_of(ecu, self.rte, REPO, ecu.can.dbc)
        self.assertTrue(dbc.is_file(), f"{dbc} not found")
        self.assertEqual(dbc, self.ws / "can" / "acme.dbc")

    def test_format_one_still_falls_back_to_the_repository_root(self):
        """`rtegen --repo` stays the fallback for a description that does not carry the key yet."""
        old = {"components": [{"name": "DemoSwc", "path": "swc/DemoSwc"}]}
        self.assertEqual(self.rtegen.schema_version_of(old), 1)
        self.assertEqual(self.rtegen.paths_base(old, self.rte, REPO), REPO)


if __name__ == "__main__":
    unittest.main()
