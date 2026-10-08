# SPDX-License-Identifier: Apache-2.0
"""Host test of the console reference (DOC-09): doc/reference/console.md collected from the sources.

A small source tree of its own (below) carries every case the collector has to handle: a macro group, a
PlainCommandInfo table of a per-device group, ParentCommand/SimpleCommand members with a command added from
another file, the `help` command of the console, an OpenBSW tester that is used and one that is not, a commented-out
group, a group under test/, preprocessor and CMake conditions (module, build option, PLATFORM_SUPPORT_* of the board
port, family directory), parameters from `param:` and `Usage:`, board rules from a help text and from a board
README. One run checks the real sources: the groups the application must have, and that the checked-in page is
current (the latter also in test_docgen.py).

    .venv/Scripts/python tools/docgen/test_console.py
"""

from __future__ import annotations

import functools
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tools.docgen import console, reference  # noqa: E402

TREE = {
    # --- OpenBSW (read only in the real repository)
    "external/openbsw/libs/bsw/util/src/util/command/HelpCommand.cpp": """
        HelpCommand::HelpCommand(ICommand& cmd, uint32_t const w)
        : HelpCommand(cmd, "help", "Show all commands.", w) {}
    """,
    "external/openbsw/libs/bsp/tester/src/PinTester.cpp": """
        DEFINE_COMMAND_GROUP_GET_INFO_BEGIN(PinTester, "pin", "pins console")
        COMMAND_GROUP_COMMAND(1U, "get", "get pin (param: <pin>)")
        DEFINE_COMMAND_GROUP_GET_INFO_END
    """,
    "external/openbsw/libs/bsp/tester/src/UnusedTester.cpp": """
        DEFINE_COMMAND_GROUP_GET_INFO_BEGIN(UnusedTester, "unused", "never declared by neXtBSW")
        COMMAND_GROUP_COMMAND(1U, "x", "x")
        DEFINE_COMMAND_GROUP_GET_INFO_END
    """,
    "external/openbsw/libs/bsw/logger/include/logger/LogCmd.h": """
        class LogCmd {
            ::util::command::SimpleCommand _levelCommand;
            ::util::command::ParentCommand _root;
        };
    """,
    "external/openbsw/libs/bsw/logger/src/logger/LogCmd.cpp": """
        LogCmd::LogCmd(Config& c)
        : _levelCommand(
              "level",
              "get/set levels. "
              "Usage: log level [<component>] [<level>]",
              ExecuteFunction::create<LogCmd, &LogCmd::level>(*this))
        , _root("log", "log settings")
        {}
    """,
    # --- neXtBSW application
    "ecu/CMakeLists.txt": "add_subdirectory(app)\n",
    "ecu/app/CMakeLists.txt": "add_executable(app.ecu src/app.cpp)\ntarget_link_libraries(app.ecu PRIVATE main)\n",
    "ecu/app/src/app.cpp": """
        LogCmd logCmd(config);
        class Lost {
            ::util::command::SimpleCommand _command;
        };
        Lost::Lost(ParentCommand& root)
        : _command("lost", "entries lost. Usage: log lost", f) { root.addCommand(_command); }
        // DEFINE_COMMAND_GROUP_GET_INFO_BEGIN(Ghost, "ghost", "commented out")
        /* DEFINE_COMMAND_GROUP_GET_INFO_BEGIN(Ghost2, "ghost2", "commented out") */
    """,
    "platforms/fam/port/main/CMakeLists.txt": """
        add_library(main src/systems/BspSystem.cpp)
        if ("fooDrv" IN_LIST NX_MODULES)
            target_sources(main PRIVATE src/systems/FooSystem.cpp)
        endif ()
        if (PLATFORM_SUPPORT_CAN)
            target_sources(main PRIVATE src/systems/BusSystem.cpp)
        endif ()
        option(NX_TEST_CMD "console command tst (test builds only)" OFF)
        if (NX_TEST_CMD)
            target_sources(main PRIVATE src/systems/TestCmd.cpp)
            target_compile_definitions(main PRIVATE NX_TEST_CMD=1)
        endif ()
        option(NX_FOO_ZAP "console command foo zap (test builds only)" OFF)
        if (NX_FOO_ZAP)
            target_compile_definitions(main PRIVATE NX_FOO_ZAP=1)
        endif ()
    """,
    "platforms/fam/port/main/include/systems/BspSystem.h": """
        #pragma once
        #ifndef BSP_SYSTEM_H
        #define BSP_SYSTEM_H
        class BspSystem {
            bios::PinTester _pinTester;
        #ifdef NX_TEST_CMD
            TestCmd _testCmd;
        #endif
        };
        #endif
    """,
    "platforms/fam/port/main/src/systems/BspSystem.cpp": "BspSystem::BspSystem() : _pinTester() {}\n",
    "platforms/fam/port/main/src/systems/FooSystem.cpp": """
        DEFINE_COMMAND_GROUP_GET_INFO_BEGIN(FooSystem::Command, "foo", "Foo chip")
        COMMAND_GROUP_COMMAND(ID_STATUS, "status", "state and counters")
        COMMAND_GROUP_COMMAND(ID_WRITE, "write", "write register (param: <address> <value>), not 0x00")
        COMMAND_GROUP_COMMAND(ID_SLEEP, "sleep", "enter sleep (supply off)")
        COMMAND_GROUP_COMMAND(ID_RAW, "raw", "channel[0-9] value")
        #ifdef NX_FOO_ZAP
        COMMAND_GROUP_COMMAND(ID_ZAP, "zap", "TEST BUILD: zap - only with the user at the board")
        #endif
        DEFINE_COMMAND_GROUP_GET_INFO_END
    """,
    "platforms/fam/port/main/src/systems/BusSystem.cpp": """
        DEFINE_COMMAND_GROUP_GET_INFO_BEGIN(BusSystem::Command, "bus", "buses")
        COMMAND_GROUP_COMMAND(
            ID_SEND,
            "send",
            "send a frame (param: [<bus>] <id> [<byte> ...], up to 8 bytes)")
        DEFINE_COMMAND_GROUP_GET_INFO_END
    """,
    "platforms/fam/port/main/src/systems/TestCmd.cpp": """
        DEFINE_COMMAND_GROUP_GET_INFO_BEGIN(TestCmd, "tst", "test command (test build only)")
        COMMAND_GROUP_COMMAND(1U, "go", "go")
        DEFINE_COMMAND_GROUP_GET_INFO_END
    """,
    "libs/bsw/fooDrv/module.yaml": "kind: device_driver\nconsole: foo\n",
    "libs/bsw/fooDrv/test/host/FooTest.cpp": """
        DEFINE_COMMAND_GROUP_GET_INFO_BEGIN(FooTestCmd, "testonly", "only in a host test")
        DEFINE_COMMAND_GROUP_GET_INFO_END
    """,
    "libs/bsw/barDrv/module.yaml": """
        kind: device_driver
        parameters:
          console: { type: str, default: bar, description: console command group of the instance }
        console: instance
    """,
    "libs/bsw/barDrv/src/BarSystem.cpp": """
        BarSystem::Command::Command(BarSystem& system)
        : _system(system)
        , _info{
              {system._names.command, system._names.description, 0U},
              {"on", "switch on (load supply! board rules apply)", ID_ON},
              {"off", "switch off, then wait", ID_OFF},
              {nullptr, nullptr, 0U},
          }
        {}
    """,
    "ecu/platforms/b1/Options.cmake": "set(PLATFORM_SUPPORT_CAN\n    ON\n    CACHE BOOL \"CAN\" FORCE)\n",
    "ecu/platforms/b2/Options.cmake": "set(PLATFORM_SUPPORT_CAN\n    OFF\n    CACHE BOOL \"CAN\" FORCE)\n",
    "boards/b1/README.md": """
        # Board b1

        - **Only with a person at the board:** `foo sleep`, `b1bar on`,
          `flash tool`. Other text.
        - **Never:** `foo write`.
    """,
}

PROJECTS = [
    console.ProjectInfo("p1", "b1", "fam", {"fooDrv", "barDrv"}, {"barDrv": ["b1bar"]}),
    console.ProjectInfo("p2", "b2", "other", set(), {}),
    console.ProjectInfo("p3", None, None, error="not loadable (see `tools/nxconfig check`)"),
]


def make_tree(root: Path, tree: dict[str, str] = TREE) -> Path:
    for rel, text in tree.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")
    return root


class ConsoleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = make_tree(Path(cls.tmp.name))
        cls.doc = console.collect(cls.root, PROJECTS)
        cls.by = {g.name or f"<{g.module}>": g for g in cls.doc.groups}
        cls.text = console.render(cls.doc)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_groups_found_and_skipped(self):
        self.assertEqual(sorted(self.by), ["<barDrv>", "bus", "foo", "help", "log", "pin", "tst"])
        # an OpenBSW tester no neXtBSW source declares, a commented-out group and a host test are not listed
        for name in ("unused", "ghost", "ghost2", "testonly"):
            self.assertNotIn(name, self.by)
        self.assertEqual(self.by["pin"].origin, "OpenBSW")
        self.assertEqual(self.by["foo"].origin, "neXtBSW")

    def test_commands_help_and_parameters(self):
        foo = self.by["foo"]
        self.assertEqual([c.name for c in foo.commands], ["status", "write", "sleep", "raw", "zap"])
        self.assertEqual(foo.help, "Foo chip")
        self.assertEqual(foo.commands[1].params, "<address> <value>")
        self.assertIsNone(foo.commands[0].params)
        # help text over several string literals and lines
        self.assertEqual(self.by["bus"].commands[0].params, "[<bus>] <id> [<byte> ...]")
        self.assertEqual(self.by["pin"].commands[0].params, "<pin>")

    def test_parent_command_with_member_from_another_file(self):
        log = self.by["log"]
        self.assertEqual([c.name for c in log.commands], ["level", "lost"])   # own constructor first
        self.assertEqual(log.commands[0].params, "[<component>] [<level>]")
        self.assertEqual(log.commands[1].params, "")
        self.assertIn("| `level` | `log level [<component>] [<level>]` |", self.text)
        self.assertIn("| `lost` | `log lost` |", self.text)

    def test_help_command(self):
        self.assertTrue(self.by["help"].simple)
        self.assertIn(f"| `help {console.HELP_PARAMS}` | Show all commands. |", self.text)

    def test_conditions(self):
        kinds = lambda g: [(c.kind, c.name) for c in g.conds]   # noqa: E731
        self.assertEqual(kinds(self.by["foo"]), [("family", "fam"), ("module", "fooDrv")])
        self.assertEqual([(c.kind, c.name) for c in self.by["foo"].commands[4].conds], [("option", "NX_FOO_ZAP")])
        self.assertEqual(kinds(self.by["bus"]), [("family", "fam"), ("platform", "PLATFORM_SUPPORT_CAN")])
        # build option from the CMake condition of the source file and from the #ifdef around the member;
        # the include guard is no condition
        self.assertEqual(kinds(self.by["tst"]), [("family", "fam"), ("option", "NX_TEST_CMD")])
        # OpenBSW tester: family of the neXtBSW file that declares it
        self.assertEqual(kinds(self.by["pin"]), [("family", "fam")])
        self.assertEqual(kinds(self.by["help"]), [])
        self.assertEqual(kinds(self.by["log"]), [])
        bar = self.by["<barDrv>"]
        self.assertEqual(kinds(bar), [("module", "barDrv")])
        self.assertIsNone(bar.name)
        self.assertEqual(bar.default_name, "bar")
        self.assertIn("build option `-DNX_TEST_CMD=ON` (default OFF)", self.text)
        # ARCH-58: der Schalter wird abgeleitet; ohne nxconfig im synthetischen Baum bleibt der Rueckfall
        self.assertIn("`PLATFORM_SUPPORT_CAN`, derived from project and board (on in `b1`)", self.text)

    def test_board_rules(self):
        rules = self.doc.boards
        self.assertEqual([r.board for r in rules], ["b1"])
        self.assertEqual(rules[0].items, ["foo sleep", "b1bar on", "flash tool"])   # the bullet only, wrapped
        self.assertEqual(sorted(rules[0].resolved), ["b1bar on", "foo sleep"])
        foo_sleep = next(line for line in self.text.splitlines() if line.startswith("| `sleep` | `foo sleep`"))
        self.assertIn("yes on [`b1`](../../boards/b1/README.md) as `foo sleep`", foo_sleep)
        bar_on = next(line for line in self.text.splitlines() if line.startswith("| `on` | `<bar> on`"))
        self.assertIn("yes (help text)", bar_on)
        self.assertIn("as `b1bar on`", bar_on)
        zap = next(line for line in self.text.splitlines() if line.startswith("| `zap` |"))
        self.assertIn("yes (help text)", zap)
        write = next(line for line in self.text.splitlines() if line.startswith("| `write` | `foo write"))
        self.assertTrue(write.endswith("|  |"), write)                  # "Never" list is not the board rule list

    def test_projects(self):
        rows = {line.split(" | ")[0][3:-1]: line for line in self.text.splitlines() if line.startswith("| `p")}
        p1 = rows["p1"].split(" | ")
        self.assertEqual(p1[3], "[`bus`](#c-bus), [`foo`](#c-foo), [`help`](#c-help), [`log`](#c-log), "
                                "[`pin`](#c-pin), [`b1bar`](#c-instance-bardrv)")
        self.assertEqual(p1[4], "[`foo zap`](#c-foo) (`NX_FOO_ZAP`), [`tst`](#c-tst) (`NX_TEST_CMD`)")
        p2 = rows["p2"].split(" | ")
        self.assertEqual(p2[3], "[`help`](#c-help), [`log`](#c-log)")
        self.assertIn("not loadable", rows["p3"])

    def test_gaps(self):
        gaps = console.gaps(self.doc)
        whats = [w for _, w in gaps]
        self.assertTrue(any(where.endswith("foo raw") for where, _ in gaps), gaps)
        self.assertIn("board rule `flash tool` is not a console command of the board's projects", whats)
        self.assertIn("not loadable (see `tools/nxconfig check`)", whats)
        self.assertFalse(any(where.endswith("foo write") for where, _ in gaps))

    def test_markdown_and_determinism(self):
        self.assertEqual(self.text, console.render(console.collect(self.root, PROJECTS)))
        self.assertNotIn("\r", self.text)
        # `<` outside code spans is escaped
        self.assertIn("(param: &lt;address> &lt;value>)", self.text)

    def test_page_in_reference_and_check(self):
        generated = reference.build_pages([], [], self.doc)
        self.assertIn("console.md", generated)
        self.assertIn("[Console commands](console.md) - the 7 command groups", generated["README.md"])
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            reference.write(out, generated)
            self.assertEqual(reference.check(out, generated), [])
            # a changed help text in the code makes the checked-in page stale
            root = Path(tmp) / "src"
            tree = dict(TREE)
            tree["platforms/fam/port/main/src/systems/FooSystem.cpp"] = \
                TREE["platforms/fam/port/main/src/systems/FooSystem.cpp"].replace("state and counters", "changed")
            make_tree(root, tree)
            report = reference.check(out, reference.build_pages([], [], console.collect(root, PROJECTS)))
            self.assertIn("console.md: differs from its sources", report)
            self.assertTrue(any(line.startswith("    +") and "changed" in line for line in report), report)

    def test_openbsw_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = {k: v for k, v in TREE.items() if not k.startswith("external/")}
            with self.assertRaises(console.SourceMissing):
                console.collect(make_tree(Path(tmp), tree), PROJECTS)


@unittest.skipUnless((REPO / console.OPENBSW / "libs").is_dir(), "external/openbsw not checked out")
class RealConsoleTest(unittest.TestCase):
    def test_real_groups(self):
        doc = console.collect_real()
        names = {g.name for g in doc.groups if g.name}
        # "out": since SYS-150 a neXtBSW subclass of the OpenBSW tester (SafeOffOutputTester) registers the group
        for name in ("help", "lc", "stats", "logger", "rte", "ecu", "can", "sbc", "adc", "safety", "out", "in", "pwm"):
            self.assertIn(name, names)
        out = next(g for g in doc.groups if g.name == "out")
        self.assertIn(("console", "NX_CONSOLE"), [(c.kind, c.name) for c in out.conds])   # PLAT-106
        modules = {g.module for g in doc.groups if not g.name}
        self.assertEqual(modules, {"gdTmc6200", "highSideSwitch", "bccMc33771"})
        gd = next(g for g in doc.groups if g.module == "gdTmc6200")
        enable = next(c for c in gd.commands if c.name == "enable")
        self.assertEqual([(c.kind, c.name) for c in enable.conds], [("option", "NX_GD_ENABLE_COMMAND")])
        sbc = next(g for g in doc.groups if g.name == "sbc")
        self.assertIn(("module", "sbcUja113x"), [(c.kind, c.name) for c in sbc.conds])
        text = reference.build_pages()["console.md"]
        self.assertIn("| `sleep` | `sbc sleep` |", text)
        # every link into the repository points to an existing file or directory
        import re
        for target in re.findall(r"\]\((\.\./\.\./[^)#]+)\)", text):
            self.assertTrue((reference.OUT_DIR / target).resolve().exists(), target)

    def test_console_condition_and_split(self):
        """PLAT-106: the registration guard of NX_CONSOLE is the project's console, also inside an `&&`; a negated
        conjunction is not split; projects decide by console.enabled."""
        cond = console.Cond("pp", "(NX_I2C_BUS_COUNT > 0) && (!defined(NX_CONSOLE) || NX_CONSOLE)", False)
        resolved = console.resolve_pp(REPO, cond, {})
        self.assertEqual([c.kind for c in resolved], ["cmake", "console"])
        self.assertEqual(console._top_level_and("(A || B) && C"), ["A || B", "C"])
        negated = console.resolve_pp(REPO, console.Cond("pp", "A && B", True), {})
        self.assertEqual(len(negated), 1)
        on = console.Cond("console", "NX_CONSOLE", False)
        with_console = console.ProjectInfo("p", None, None, console=True)
        without = console.ProjectInfo("q", None, None, console=False)
        self.assertTrue(console.evaluate(on, with_console, None))
        self.assertFalse(console.evaluate(on, without, None))
        self.assertIsNone(console.evaluate(on, console.ProjectInfo("r", None, None), None))

    def test_checked_in_page_is_current(self):
        report = reference.check(reference.OUT_DIR, reference.build_pages())
        self.assertEqual(report, [], "doc/reference is stale - regenerate with "
                                     f"{reference.REGENERATE} and commit:\n" + "\n".join(report))


if __name__ == "__main__":
    unittest.main()


class TrackedProjectsTest(unittest.TestCase):
    """TOOL-81: the generated page describes the repository, not one workstation.

    Someone trying out their own project under `projects/` used to land in `doc/reference/console.md` and made
    the comparison test red - three times, and once it even reached `dev`.
    """

    def test_untracked_project_is_left_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            (repo / "projects" / "tracked").mkdir(parents=True)
            (repo / "projects" / "own_try").mkdir(parents=True)
            (repo / "projects" / "tracked" / "project.yaml").write_text("project:\n  name: tracked\n",
                                                                       encoding="utf-8")
            (repo / "projects" / "own_try" / "project.yaml").write_text("project:\n  name: own_try\n",
                                                                       encoding="utf-8")
            run = functools.partial(subprocess.run, cwd=repo, capture_output=True, text=True, check=True)
            run(["git", "init", "-q"])
            run(["git", "add", "projects/tracked/project.yaml"])
            run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "tracked only"])

            console.project_files.cache_clear()
            names = [f.parent.name for f in console.project_files(repo)]
            self.assertEqual(names, ["tracked"])
            console.project_files.cache_clear()

    def test_without_git_every_project_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            for name in ("a", "b"):
                (repo / "projects" / name).mkdir(parents=True)
                (repo / "projects" / name / "project.yaml").write_text(f"project:\n  name: {name}\n",
                                                                      encoding="utf-8")
            console.project_files.cache_clear()
            names = [f.parent.name for f in console.project_files(repo)]
            self.assertEqual(names, ["a", "b"])      # an exported tree has nothing to tell them apart by
            console.project_files.cache_clear()
