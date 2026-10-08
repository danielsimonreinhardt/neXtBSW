"""Host tests of the new-project core (TOOL-59): name and folder rules, empty project derived from board and
derivative, copy with rewritten paths, all-or-nothing writing, command line.

Own profiles only: a copy of the synthetic boards and projects of tools/nxconfig/test/profiles under a temporary
repository root (TESTMCU64 of the test mcudb, and a synthetic board on the real S32K142, S32K116 and RM46L852
derivatives) - never the eval board or the demo project as the yardstick.
"""

import contextlib
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ruamel.yaml import YAML

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import nxconfig  # noqa: E402
from nxconfig import __main__ as cli  # noqa: E402
from nxconfig import newproject as np  # noqa: E402

PROFILES = HERE / "test" / "profiles"
TEST_MCUDB = HERE / "test" / "mcudb"
TASKS = Path("ecu") / "asyncCoreConfiguration" / "tasks.yaml"


# Own synthetic board on the smallest real derivative (S32K116, 1 KB SRAM_L + 14 KB SRAM_U, no PLL): one CAN bus
# without transceiver module, a console, one output and one input. Pins from mcudb S32K116_LQFP48 - test data only.
TINY116 = """schema_version: 1
board:
  name: tiny116
  mcu: S32K116
  package: LQFP48
  clock: { sosc_hz: 8000000 }
  adc_reference_mv: 3300
can:
  - { name: BUS, instance: FlexCAN0, rx: { pin: PTE4 }, tx: { pin: PTE5 } }
uart:
  - { name: CON, instance: LPUART0, rx: { pin: PTB0 }, tx: { pin: PTB1 } }
dio:
  inputs:
    - { name: SW, pin: PTD2 }
  outputs:
    - { name: LAMP, pin: PTD0 }
"""


def load(file: Path) -> dict:
    return YAML(typ="safe").load(file.read_text(encoding="utf-8"))


class NewProjectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nxnew-test-"))
        self.repo = self.tmp / "repo"
        shutil.copytree(PROFILES, self.repo)
        (self.repo / TASKS).parent.mkdir(parents=True)
        # own BSW task manifest: the default task of the empty project comes from the tree it is created in
        (self.repo / TASKS).write_text("schema_version: 1\ntick_us: 1000\nmax_priorities: 32\nmin_stack_bytes: 512\n"
                                       "tasks:\n  - { name: bsp, priority: 20 }\n  - { name: can, priority: 40 }\n"
                                       "application_default: { name: appx, priority: 33, stack_bytes: 1536 }\n",
                                       encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def empty(self, name="fresh", board="testboard", mcudb=TEST_MCUDB, **kwargs):
        plan = np.plan_empty(name, board, repo=self.repo, mcudb=mcudb, **kwargs)
        return plan, np.create(plan)

    def assert_clean(self, result):
        self.assertEqual([str(m) for m in result.errors], [])

    # ------------------------------------------------------------------ lists and rules

    def test_boards_and_projects_of_the_tree(self):
        boards = {b.name: b for b in np.list_boards(self.repo, TEST_MCUDB)}
        self.assertIn("testboard", boards)
        self.assertEqual(boards["testboard"].mcu, "TESTMCU64")
        # ARCH-58: what makes a board buildable is the port template of its family, not a hand-written
        # ecu/platforms/<board>/Options.cmake (the feature switches are derived since then).
        self.assertFalse(boards["testboard"].buildable)          # family testfam has no port template here
        port = self.repo / "platforms" / "testfam" / "port" / "CMakeLists.txt"
        port.parent.mkdir(parents=True)
        port.write_text("# family port template of testfam\n", encoding="utf-8")
        self.assertTrue({b.name: b for b in np.list_boards(self.repo, TEST_MCUDB)}["testboard"].buildable)
        projects = {p.name: p for p in np.list_projects(self.repo)}
        self.assertEqual(projects["testproject"].board, "testboard")

    def test_name_rules(self):
        self.assertEqual(np.check_name("body_ctrl2", self.repo), [])
        for bad in ("", "2fast", "with-dash", "with space", "ümlaut", "x" * 49):
            with self.subTest(name=bad):
                self.assertIn("a letter first", " ".join(np.check_name(bad, self.repo)))
        self.assertIn("reserved word", np.check_name("static", self.repo)[0])
        self.assertIn("device name", np.check_name("COM1", self.repo)[0])
        # collision with an existing project name: the build directory <build root>/<name> would be shared
        self.assertIn("already used", np.check_name("TestProject", self.repo)[0])

    def test_folder_rules(self):
        busy = self.tmp / "busy"
        busy.mkdir()
        (busy / "x.txt").write_text("x", encoding="utf-8")
        self.assertIn("not empty", np.check_target(busy, self.repo)[0])
        self.assertEqual(np.check_target(self.tmp / "new", self.repo), [])
        (self.tmp / "empty").mkdir()
        self.assertEqual(np.check_target(self.tmp / "empty", self.repo), [])
        self.assertIn("build/", np.check_target(self.repo / "build" / "p", self.repo)[0])
        with self.assertRaises(np.NewProjectError) as e:
            np.plan_empty("fresh", "testboard", busy, repo=self.repo, mcudb=TEST_MCUDB)
        self.assertIn("not empty", str(e.exception))
        with self.assertRaises(np.NewProjectError):
            np.plan_empty("fresh", "no_such_board", repo=self.repo, mcudb=TEST_MCUDB)

    # ------------------------------------------------------------------ empty project

    def test_empty_project_is_checked_clean(self):
        plan, result = self.empty(with_diag=True, description="Fresh ECU")
        self.assert_clean(result)
        folder = self.repo / "projects" / "fresh"
        self.assertEqual(result.project_file, folder / "project.yaml")
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["diag.yaml", "project.yaml", "rte.yaml"])
        data = load(folder / "project.yaml")
        self.assertEqual(data["project"], {"name": "fresh", "board": "../../boards/testboard", "rte": "rte.yaml",
                                           "diag": "diag.yaml", "description": "Fresh ECU"})
        # derived from board and derivative (TESTMCU64: no clock limits, 256 KB flash with 8 KB sectors, 64 KB RAM)
        self.assertEqual(data["clock"]["system_hz"], 48_000_000)
        self.assertEqual(data["memory"], {"flash": "FLASH", "bootloader": {"size": 0x8000, "ram": "RAM",
                                                                           "ram_size": 0x8000, "stack_size": 0x1000},
                                          "application": {"ram": ["RAM"], "noinit_size": 0x400}})
        self.assertEqual(data["bootloader"], {"can": "VEH"})             # first CAN bus of the board
        self.assertEqual(data["use"]["can"], ["VEH"])
        self.assertEqual(data["use"]["uart"], ["CONSOLE"])
        # PLAT-94: no digital input is used (the empty project reads nothing); TOOL-110: no output either - the
        # outputs came in only as the pins of the safety section, which the wizard no longer picks
        self.assertNotIn("dio", data["use"])
        self.assertNotIn("KEY", str(data["use"]))
        self.assertNotIn("interrupts", data)                             # TESTMCU64 has no priority bits
        self.assertNotIn("safety", data)
        rte = load(folder / "rte.yaml")
        self.assertEqual(rte["components"], [])
        self.assertEqual(rte["tasks"], [{"name": "appx", "period_ms": 10, "priority": 33, "stack_bytes": 1536}])
        # TOOL-108: the task grows a runnables list with every assignment - block lines, not `- { ... }`
        text = (folder / "rte.yaml").read_text(encoding="utf-8")
        self.assertIn("tasks:\n  - name: appx\n    period_ms: 10\n    priority: 33\n    stack_bytes: 1536\n", text)
        self.assertNotIn("{ name: appx", text)
        # TOOL-131: the standard diagnostics of neXtBSW (details: tools/diaggen/test_standard_diag.py)
        diag = load(folder / "diag.yaml")
        self.assertEqual([d["identifier"] for d in diag["data_identifiers"]], [0xF180, 0xF181, 0xF186, 0xF18C, 0xF191, 0xF195,
                                                                           0xF1F1, 0xF1F2, 0x0120, 0x0121, 0x0130])   # PLAT-79: F1F1, PLAT-93: F1F2, NVM-05: 0x0130
        # COM-46: and the bus-off DTC of the used CAN bus (bsw_instance 0: its index)
        # SYS-113: then the causes of a safety reset (tools/diaggen/standard_diag.yaml)
        self.assertEqual([d["event"]["bsw"] for d in diag["dtcs"]],
                         ["rte.deadline", "can.busoff.VEH", "safety.mcu_watchdog", "safety.fault_exception",
                          "safety.deadman_configuration_error", "safety.reset_other", "safety.reset_loop"])
        self.assertEqual(diag["dtcs"][1]["bsw_instance"], 0)
        self.assertTrue(any("no port template" in n for n in plan.notes))   # ARCH-58: family, not board port
        # the RTE generator accepts it as well (no component, one task)
        sys.path.insert(0, str(HERE.parent / "rtegen"))
        import rtegen
        shutil.copyfile(HERE.parent.parent / TASKS, self.repo / TASKS)
        self.assertEqual([str(m) for m in rtegen.validate(result.project_file, repo=self.repo, mcudb=TEST_MCUDB)
                          if m.severity == "error"], [])

    def test_empty_project_on_real_derivatives(self):
        # synthetic boards on real mcudb derivatives: the clock model, interrupt ranks, test pin and memory come from
        # the derivative, and a pin carrying a debug or oscillator function is never the test pin
        (self.repo / "boards" / "tiny116").mkdir()
        (self.repo / "boards" / "tiny116" / "board.yaml").write_text(TINY116, encoding="utf-8")
        cases = [("testboard_s32k142_bl", 80_000_000, 0x8000),
                 ("tiny116", 48_000_000, 0x4000)]                                   # S32K116: 128 KB, 14 KB RAM
        if (HERE / "test/profiles/boards/testboard_rm46").is_dir():                # private (TOOL-244): not in the export
            cases.append(("testboard_rm46", None, None))
        for board, clock, flash in cases:
            with self.subTest(board=board):
                plan, result = self.empty(f"n_{board}", board, mcudb=nxconfig.MCUDB)
                self.assert_clean(result)
                data = load(result.project_file)
                if clock:
                    self.assertEqual(data["clock"]["system_hz"], clock)
                if flash:
                    self.assertEqual(data["memory"]["bootloader"]["size"], flash)
                    self.assertIn("interrupts", data)
                if "safety" in data:
                    self.assertNotIn(data["safety"]["test_pin"], ("PTA4", "PTC4", "PTC5", "PTA10", "PTA5"))
        small = load(self.repo / "projects" / "n_tiny116" / "project.yaml")["memory"]
        self.assertEqual(small["application"]["ram"], ["SRAM_U"])        # 1 KB SRAM_L does not start the RAM
        self.assertEqual(small["application"]["noinit_size"], 0x200)

    def test_test_pin_never_carries_debug_or_oscillator_functions(self):
        signals = {"GPIO": {"PA0": 0, "PA1": 0, "PA2": 0, "PA3": 0, "PA4": 0}, "SWD_DIO": {"PA0": 7},
                   "JTAG_TCLK": {"PA1": 7}, "EXTAL": {"PA2": 0}, "UART_TX": {"PA3": 2}}
        self.assertEqual(np.free_test_pin(signals, set()), "PA3")          # a plain alternate function is fine
        self.assertEqual(np.free_test_pin(signals, {"PA3"}), "PA4")        # described by the board: taken
        self.assertIsNone(np.free_test_pin(signals, {"PA3", "PA4"}))
        self.assertIsNone(np.free_test_pin({}, set()))

    def test_empty_project_outside_the_tree(self):
        target = self.tmp / "elsewhere" / "deep" / "fresh"
        plan, result = self.empty(target=target)
        self.assert_clean(result)
        board = load(target / "project.yaml")["project"]["board"]
        self.assertEqual(Path(board), (self.repo / "boards" / "testboard").resolve())   # absolute outside the tree

    # ------------------------------------------------------------------ copy

    def make_source(self) -> Path:
        """projects/src_proj: testproject plus rte.yaml with one own and one reusable component, DBC, build output."""
        src = self.repo / "projects" / "src_proj"
        src.mkdir()
        text = (self.repo / "projects" / "testproject" / "project.yaml").read_text(encoding="utf-8")
        text = text.replace("name: testproject", "name: src_proj") \
            .replace("  board: ../../boards/testboard", "  board: ../../boards/testboard   # the board\n  rte: rte.yaml")
        (src / "project.yaml").write_text("# Check: nxconfig check projects/src_proj/project.yaml\n" + text,
                                          encoding="utf-8")
        # RTE-22: format 2 - every path relative to this rte.yaml (the reusable component with ../.., the
        # project's own next to it), so the copy has to rewrite exactly those that no longer point at the same place
        (src / "rte.yaml").write_text(
            "# ECU description of projects/src_proj/\n"
            "schema_version: 2\n"
            "components:\n"
            "  - name: OwnSwc\n"
            "    path: swc/OwnSwc      # project specific\n"
            "  - { name: SharedSwc, path: ../../swc/SharedSwc }\n"
            "  - { name: WinSwc, path: 'swc\\WinSwc' }   # written with backslashes\n"
            "can:\n"
            "  dbc: net.dbc\n"
            "  node: ECU\n", encoding="utf-8")
        self.source_rte = (src / "rte.yaml").read_text(encoding="utf-8")
        (src / "net.dbc").write_text("VERSION \"\"\n", encoding="utf-8")
        for rel in ("swc/OwnSwc/generated/arxml/OwnSwc.arxml", "swc/OwnSwc/OwnSwc.slx", "swc/WinSwc/swc.yaml",
                    "build/x.o",
                    "generated/rte/Rte.h", "swc/OwnSwc/slprj/cache.mat", "swc/OwnSwc/OwnSwc.slxc",
                    "__pycache__/x.pyc"):
            (src / rel).parent.mkdir(parents=True, exist_ok=True)
            (src / rel).write_bytes(b"x")
        return src

    def test_copy_rewrites_paths_and_leaves_build_output(self):
        src = self.make_source()
        plan = np.plan_copy("copy1", "src_proj", repo=self.repo, mcudb=TEST_MCUDB, description="Variant B")
        self.assertFalse((self.repo / "projects" / "copy1").exists())            # planning writes nothing
        result = np.create(plan)
        self.assert_clean(result)
        dst = self.repo / "projects" / "copy1"
        files = sorted(p.relative_to(dst).as_posix() for p in dst.rglob("*") if p.is_file())
        self.assertEqual(files, ["net.dbc", "project.yaml", "rte.yaml", "swc/OwnSwc/OwnSwc.slx",
                                 "swc/OwnSwc/generated/arxml/OwnSwc.arxml", "swc/WinSwc/swc.yaml"])
        project = (dst / "project.yaml").read_text(encoding="utf-8")
        self.assertIn("  name: copy1\n", project)
        self.assertIn("  board: ../../boards/testboard   # the board\n", project)   # layout and comments kept
        self.assertIn("  description: Variant B\n", project)
        self.assertIn("# Check: nxconfig check projects/copy1/project.yaml", project)
        rte = (dst / "rte.yaml").read_text(encoding="utf-8")
        # the copy sits at the same depth, so the paths of the copied component and the DBC keep their text
        self.assertIn("    path: swc/OwnSwc      # project specific\n", rte)
        self.assertIn("  - { name: SharedSwc, path: ../../swc/SharedSwc }\n", rte)   # reusable: still referenced
        # found by where it points, not by its spelling: the backslashes become one spelling of the same place
        self.assertIn("  - { name: WinSwc, path: swc/WinSwc }   # written with backslashes\n", rte)
        self.assertIn("  dbc: net.dbc\n", rte)
        self.assertIn("# ECU description of projects/copy1/", rte)
        self.assertEqual((src / "rte.yaml").read_text(encoding="utf-8"), self.source_rte, "source untouched")

    def test_copy_into_another_folder_depth(self):
        self.make_source()
        target = self.repo / "projects" / "variants" / "copy2"
        result = np.create(np.plan_copy("copy2", self.repo / "projects" / "src_proj", target, repo=self.repo,
                                        mcudb=TEST_MCUDB))
        self.assert_clean(result)
        self.assertEqual(load(target / "project.yaml")["project"]["board"], "../../../boards/testboard")
        rte = load(target / "rte.yaml")
        # RTE-22: the copied component keeps its place next to the description, the referenced one is written from
        # the new depth - it is one folder deeper than the source, so it needs one `..` more
        self.assertEqual(rte["components"][0]["path"], "swc/OwnSwc")
        self.assertEqual(rte["components"][1]["path"], "../../../swc/SharedSwc")
        self.assertEqual(rte["can"]["dbc"], "net.dbc")

    def test_copy_of_a_project_in_the_tree_checks_clean(self):
        for source in ("testproject", "testproject_bootloader"):
            with self.subTest(source=source):
                result = np.create(np.plan_copy(f"c_{source}", source, repo=self.repo, mcudb=TEST_MCUDB))
                self.assert_clean(result)
                self.assertEqual(load(result.project_file)["project"]["name"], f"c_{source}")

    def test_the_wizard_never_creates_a_project_without_rte(self):
        """TOOL-114: the RTE is required. The empty project has one; a copy of a source without project.rte (the
        synthetic testproject here, projects/nucleo_f446re_demo before TOOL-114) gets an rte.yaml with one task
        and no component, named in project.rte, that rtegen accepts."""
        _, empty = self.empty(name="e114")
        self.assertEqual(load(empty.project_file)["project"]["rte"], "rte.yaml")
        source = self.repo / "projects" / "testproject" / "project.yaml"
        self.assertNotIn("rte", load(source)["project"])                  # the precondition of this test
        plan = np.plan_copy("c114", "testproject", repo=self.repo, mcudb=TEST_MCUDB, description="with RTE")
        self.assertIn("rte.yaml", [f.path for f in plan.files])
        self.assertTrue(any("project.rte: the source has none" in n for n in plan.notes), plan.notes)
        result = np.create(plan)
        self.assert_clean(result)
        project = load(result.project_file)["project"]
        self.assertEqual((project["name"], project["rte"], project["description"]), ("c114", "rte.yaml", "with RTE"))
        rte = load(result.project_file.parent / "rte.yaml")
        self.assertEqual(rte["components"], [])
        self.assertEqual(rte["tasks"], [{"name": "appx", "period_ms": 10, "priority": 33, "stack_bytes": 1536}])
        self.assertIn("board: ../../boards/testboard", source.read_text(encoding="utf-8"))
        self.assertFalse((source.parent / "rte.yaml").exists(), "source untouched")
        sys.path.insert(0, str(HERE.parent / "rtegen"))
        import rtegen
        shutil.copyfile(HERE.parent.parent / TASKS, self.repo / TASKS)
        self.assertEqual([str(m) for m in rtegen.validate(result.project_file, repo=self.repo, mcudb=TEST_MCUDB)
                          if m.severity == "error"], [])
        # a name that is taken in the source is not overwritten
        (source.parent / "rte.yaml").write_text("# not the ECU description of this project\n", encoding="utf-8")
        plan = np.plan_copy("c114b", "testproject", repo=self.repo, mcudb=TEST_MCUDB)
        self.assertEqual(load(np.create(plan).project_file)["project"]["rte"], "rte_1.yaml")

    # ------------------------------------------------------------------ all or nothing

    def test_failed_write_leaves_nothing(self):
        self.make_source()
        target = self.repo / "new_parent" / "copy3"
        plan = np.plan_copy("copy3", "src_proj", target, repo=self.repo, mcudb=TEST_MCUDB)
        with mock.patch.object(np.shutil, "copy2", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                np.create(plan)
        self.assertFalse((self.repo / "new_parent").exists())            # created parent removed again
        leftovers = [p for p in self.repo.rglob("*nxnew*")]
        self.assertEqual(leftovers, [])
        # the target appeared meanwhile: refused, nothing written
        target.mkdir(parents=True)
        (target / "other.txt").write_text("x", encoding="utf-8")
        with self.assertRaises(np.NewProjectError):
            np.create(plan)
        self.assertEqual(sorted(p.name for p in target.iterdir()), ["other.txt"])

    # ------------------------------------------------------------------ command line

    def test_command_line(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["new", "bad-name", "--board", "testboard"])
        self.assertEqual(code, 1)
        self.assertIn("a letter first", err.getvalue())
        target = self.tmp / "cli_dry"
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(["new", "cli_dry", "--copy", str(self.repo / "projects" / "testproject"),
                             "--dir", str(target), "--dry-run"])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("copy project cli_dry", out.getvalue())
        self.assertIn("  project.yaml", out.getvalue())
        self.assertFalse(target.exists())

    # ------------------------------------------------------------------ TOOL-106 (F5): the signals of the board

    def test_all_signals_of_the_board_are_used(self):
        """F5 of the TOOL-75 run: an empty project used only CAN, UART and the outputs of the safety demo - the
        digital inputs, ADC and capture channels of the board were missing without a word. With signals="all"
        every bus, input, output, ADC and capture channel of the synthetic testboard is in `use`, every CAN bus and
        UART gets its bit/baud rate, and nxconfig finds no error. PWM stays off, and the notes say why."""
        plan, result = self.empty("allsig", signals="all")
        self.assert_clean(result)
        data = load(result.project_file)
        board = load(self.repo / "boards" / "testboard" / "board.yaml")
        names = {kind: [e["name"] for e in board.get(kind, [])] for kind in np.USE_KINDS}
        for kind in np.USE_KINDS:
            with self.subTest(kind=kind):
                self.assertEqual(data["use"].get(kind, []), names[kind])
        dio = [e["name"] for e in board["dio"]["outputs"] + board["dio"]["inputs"]]
        self.assertEqual(sorted(data["use"]["dio"]), sorted(dio))
        self.assertTrue(all(k in dio for k in ("KEY", "LED_GREEN", "LED_RED")))
        self.assertEqual(sorted(data["can"]), sorted(names["can"]))           # a bit rate for every bus
        self.assertEqual(sorted(data["uart"]), sorted(names["uart"]))
        self.assertNotIn("pwm", data)
        self.assertTrue(any("PWM group(s) BRIDGE not used" in n for n in plan.notes), plan.notes)
        self.assertFalse(any("not used by this project" in n for n in plan.notes), plan.notes)

    def test_minimal_signals_name_what_stays_off(self):
        """The default stays the small project of TOOL-59 - but what it leaves out is named, not dropped."""
        plan, result = self.empty("minsig")
        self.assert_clean(result)
        data = load(result.project_file)
        self.assertEqual(data["use"], {"can": ["VEH"], "uart": ["CONSOLE"]})
        note = next(n for n in plan.notes if n.startswith("not used by this project"))
        for name in ("DIAG", "MEM_SPI", "VBAT", "TEMP_EXT", "SPEED", "RC_IN", "KEY", "LED_GREEN", "LED_RED"):
            self.assertIn(name, note)

    def test_all_signals_give_every_bus_an_interrupt_rank(self):
        """A derivative with interrupt priorities (TESTK1X, s32k1xx): two CAN buses and a UART, each with a rank -
        without one nxconfig refuses the second bus ('is used, but has no interrupt rank')."""
        board = self.repo / "boards" / "k1x_two"
        board.mkdir(parents=True)
        (board / "board.yaml").write_text(
            "schema_version: 1\nboard:\n  name: k1x_two\n  mcu: TESTK1X\n  package: LQFP64\n"
            "  clock: { sosc_hz: 8000000 }\n  adc_reference_mv: 5000\n"
            "can:\n  - { name: CAN0, instance: FlexCAN0, rx: { pin: PTE4 }, tx: { pin: PTE5 } }\n"
            "  - { name: CAN1, instance: FlexCAN1, rx: { pin: PTA12 }, tx: { pin: PTA13 } }\n"
            "uart:\n  - { name: DBG, instance: LPUART1, rx: { pin: PTC8 }, tx: { pin: PTC9 } }\n"
            "adc:\n  - { name: TEMP, instance: ADC0, channel: SE0, pin: PTA0 }\n"
            "input_capture:\n  - { name: SPEED, instance: FTM6, channel: 0, pin: PTB2 }\n"
            "dio:\n  inputs:\n    - { name: FLOW_OK, pin: PTA2 }\n"
            "  outputs:\n    - { name: PUMP_EN, pin: PTA3 }\n    - { name: FAULT_LED, pin: PTD16 }\n",
            encoding="utf-8")
        _plan, result = self.empty("k1xall", board="k1x_two", signals="all")
        self.assert_clean(result)
        data = load(result.project_file)
        self.assertEqual(data["use"]["can"], ["CAN0", "CAN1"])
        self.assertEqual(data["use"]["adc"], ["TEMP"])
        self.assertEqual(data["use"]["input_capture"], ["SPEED"])
        self.assertEqual(sorted(data["use"]["dio"]), ["FAULT_LED", "FLOW_OK", "PUMP_EN"])
        self.assertEqual([(r["source"], r["name"], r["rank"]) for r in data["interrupts"]["ranks"]],
                         [("can", "CAN0", 0), ("can", "CAN1", 1), ("uart", "DBG", 2)])
        self.assertEqual(data["bootloader"]["can"], "CAN0")                    # the first bus stays the boot bus

    # ------------------------------------------------------------------ TOOL-110 (F6): the safety section

    PUMP = ("schema_version: 1\nboard:\n  name: pump_ctrl\n  mcu: TESTK1X\n  package: LQFP64\n"
            "  clock: { sosc_hz: 8000000 }\n  adc_reference_mv: 5000\n"
            "can:\n  - { name: CAN0, instance: FlexCAN0, rx: { pin: PTE4 }, tx: { pin: PTE5 } }\n"
            "uart:\n  - { name: DBG, instance: LPUART1, rx: { pin: PTC8 }, tx: { pin: PTC9 } }\n"
            "adc:\n  - { name: FLOW, instance: ADC0, channel: SE0, pin: PTA0 }\n"
            "input_capture:\n  - { name: SPEED, instance: FTM6, channel: 0, pin: PTB2 }\n"
            "dio:\n  inputs:\n    - { name: FLOW_OK, pin: PTA2 }\n"
            "  outputs:\n    - { name: PUMP_EN, pin: PTA3 }\n    - { name: FAULT_LED, pin: PTD16 }\n")

    def pump_board(self) -> str:
        """Synthetic pump controller like the board of the TOOL-75 run (F6), on the synthetic s32k1xx derivative
        TESTK1X: the enable of the pump is the FIRST output and an LED the second - the old wizard wrote
        safe_state_output PUMP_EN, locked_pin FAULT_LED and the first GPIO pin the board file does not name."""
        folder = self.repo / "boards" / "pump_ctrl"
        folder.mkdir(parents=True)
        (folder / "board.yaml").write_text(self.PUMP, encoding="utf-8")
        return "pump_ctrl"

    def test_the_safety_section_is_never_derived(self):
        """F6: without an explicit choice nothing is written into `safety` - not for "minimal", not for "all".
        The section stands commented out with what each key does and what the board offers, and the notes say so."""
        board = self.pump_board()
        for signals in np.SIGNAL_CHOICES:
            with self.subTest(signals=signals):
                plan, result = self.empty(f"pump_{signals}", board=board, mcudb=TEST_MCUDB, signals=signals)
                self.assert_clean(result)
                data = load(result.project_file)
                self.assertNotIn("safety", data)
                text = result.project_file.read_text(encoding="utf-8")
                self.assertNotIn("\nsafety:", text)
                self.assertIn("# NOT chosen (TOOL-110)", text)
                self.assertIn("# Digital outputs of the board: PUMP_EN (PTA3), FAULT_LED (PTD16).", text)
                for key in np.SAFETY_KEYS:
                    self.assertIn(f"#   {key}: ", text)
                self.assertIn("Never an actuator", text)
                note = next(n for n in plan.notes if n.startswith("safety pins NOT chosen"))
                self.assertIn("--safe-state-output", note)
        self.assertNotIn("dio", load(self.repo / "projects" / "pump_minimal" / "project.yaml")["use"])

    def test_the_candidates_of_the_board(self):
        """What the Studio page and --safety-candidates offer: every output, and the GPIO pins the board file does
        not name - not the capture pin PTB2 (unused by a minimal project, but wired), no bus or DIO pin."""
        offer = np.safety_candidates(self.pump_board(), self.repo, TEST_MCUDB)
        self.assertEqual(offer.outputs, [("PUMP_EN", "PTA3"), ("FAULT_LED", "PTD16")])
        self.assertEqual(offer.test_pins, ["PTB3", "PTB4", "PTE8"])
        self.assertFalse(offer.required)                # the test tree has no port template of the family

    def test_the_family_port_that_needs_the_section_is_named(self):
        """A family whose port reads the section (s32k1xx: platforms/s32k1xx/port/port.yaml, project: safety):
        the note and the commented section say that nxconfig refuses the build until it is set."""
        port = self.repo / "platforms" / "s32k1xx" / "port" / "CMakeLists.txt"
        port.parent.mkdir(parents=True)
        port.write_text("# family port template of s32k1xx\n", encoding="utf-8")
        board = self.pump_board()
        offer = np.safety_candidates(board, self.repo, TEST_MCUDB)
        self.assertTrue(offer.required)
        self.assertEqual(offer.port, "platforms/s32k1xx/port/port.yaml")
        plan = np.plan_empty("pump_port", board, repo=self.repo, mcudb=TEST_MCUDB)
        note = next(n for n in plan.notes if n.startswith("safety pins NOT chosen"))
        self.assertIn("the family port needs the section (platforms/s32k1xx/port/port.yaml)", note)
        text = next(f.text for f in plan.files if f.path == "project.yaml")
        self.assertIn("The family port needs this section", text)

    def test_the_plan_says_before_writing_that_the_port_needs_the_section(self):
        """TOOL-126 (user test 2026-09-25, 4hlixmir): "not now" on a family port that reads `safety` ended in a
        project with 'error: the family port s32k1xx needs safety of the project'. The plan knows it before
        anything is written: `safety_required` names the manifest. With the pins chosen it is empty. (Whether the
        check then reports the error depends on the board lying in boards/ of the running tree - the synthetic
        tree here does not, so `expected_errors` is proven on its own below.)"""
        port = self.repo / "platforms" / "s32k1xx" / "port" / "CMakeLists.txt"
        port.parent.mkdir(parents=True)
        port.write_text("# family port template of s32k1xx\n", encoding="utf-8")
        board = self.pump_board()
        plan = np.plan_empty("pump_req", board, repo=self.repo, mcudb=TEST_MCUDB)
        self.assertEqual(plan.safety_required, "platforms/s32k1xx/port/port.yaml")
        self.assertFalse((self.repo / "projects" / "pump_req").exists())
        offer = np.safety_candidates(board, self.repo, TEST_MCUDB)
        chosen = np.plan_empty("pump_ok", board, repo=self.repo, mcudb=TEST_MCUDB,
                               safety=np.SafetyChoice("FAULT_LED", "PUMP_EN", offer.test_pins[0]))
        self.assertEqual(chosen.safety_required, "")
        self.assertEqual([str(m) for m in chosen.expected_errors], [])
        result = np.create(chosen)
        self.assert_clean(result)                   # what the plan expected is what the check reports
        # without a port template nothing is required, and a clean project expects no error
        self.assertEqual(np.plan_empty("tb", "testboard", repo=self.repo, mcudb=TEST_MCUDB).safety_required, "")

    def test_the_plan_keeps_the_errors_of_its_final_check(self):
        """TOOL-126: the check of the whole planned file set used to be thrown away after the clock search - an
        error there (a section the family port needs, a peripheral clock of an 'all signals' project) came back
        only after create(). The plan now carries it as `expected_errors`, before anything is written."""
        from nxconfig.messages import Message

        real = np._check_text
        invented = Message("error", "invented finding of the final check", None, ("use", "dio"))

        def check(folder, files, mcudb, module_dirs, port_requirements=False):
            cfg, found = real(folder, files, mcudb, module_dirs, port_requirements)
            return cfg, list(found) + ([invented] if Path(folder).name == "full" else [])

        with mock.patch.object(np, "_check_text", side_effect=check):
            plan = np.plan_empty("exp_err", "testboard", repo=self.repo, mcudb=TEST_MCUDB)
        self.assertEqual(plan.expected_errors, [invented])
        self.assertFalse((self.repo / "projects" / "exp_err").exists())
        clean = np.plan_empty("exp_ok", "testboard", repo=self.repo, mcudb=TEST_MCUDB)
        self.assertEqual(clean.expected_errors, [])

    def test_command_line_stops_when_the_port_needs_the_section(self):
        """TOOL-126: `nxconfig new --board` without the three safety options stops before writing, with the hint
        how to choose them - when the family port reads the section; otherwise it creates as before."""
        info = np.BoardInfo("pump_ctrl", self.repo / "boards" / "pump_ctrl" / "board.yaml", "TESTK1X", port=True)
        planned = np.Plan(self.repo, "cli_req", self.repo / "projects" / "cli_req", "empty", board=info,
                          safety_required="platforms/s32k1xx/port/port.yaml")
        for argv in ([], ["--dry-run"]):
            err = io.StringIO()
            with self.subTest(argv=argv), mock.patch.object(np, "plan", return_value=planned), \
                    mock.patch.object(np, "create") as created, contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(err):
                code = cli.main(["new", "cli_req", "--board", "pump_ctrl", *argv])
            self.assertEqual(code, 1)
            created.assert_not_called()
            text = err.getvalue()
            self.assertIn("needs the safety section (platforms/s32k1xx/port/port.yaml)", text)
            self.assertIn("--safe-state-output", text)
            self.assertIn("--safety-candidates --board pump_ctrl", text)
            self.assertIn("Nothing written", text)

    def test_the_users_choice_is_written_as_chosen(self):
        board = self.pump_board()
        offer = np.safety_candidates(board, self.repo, TEST_MCUDB)
        choice = np.SafetyChoice("FAULT_LED", "PUMP_EN", offer.test_pins[-1])
        plan, result = self.empty("pump_chosen", board=board, mcudb=TEST_MCUDB, safety=choice)
        self.assert_clean(result)
        data = load(result.project_file)
        self.assertEqual(data["safety"], {"safe_state_output": "FAULT_LED", "locked_pin": "PUMP_EN",
                                          "test_pin": offer.test_pins[-1]})
        self.assertEqual(data["use"]["dio"], ["FAULT_LED", "PUMP_EN"])        # both outputs are used
        self.assertIn("# chosen by the user when the project was created, not by the wizard.",
                      result.project_file.read_text(encoding="utf-8"))
        self.assertTrue(any(n.startswith("safety section as chosen") for n in plan.notes), plan.notes)

    def test_an_implausible_choice_is_refused_before_anything_is_written(self):
        board = self.pump_board()
        free = np.safety_candidates(board, self.repo, TEST_MCUDB).test_pins[0]
        cases = [(np.SafetyChoice("FAULT_LED", "FAULT_LED", free), "are both FAULT_LED"),
                 (np.SafetyChoice("FLOW_OK", "PUMP_EN", free), "'FLOW_OK' is no digital output"),
                 (np.SafetyChoice("FAULT_LED", "PUMP_EN", "PTB2"), "'PTB2' is no free GPIO pin"),
                 (np.SafetyChoice("FAULT_LED", "PUMP_EN", "PTZ99"), "'PTZ99' is no free GPIO pin")]
        for choice, fragment in cases:
            with self.subTest(fragment=fragment), self.assertRaises(np.NewProjectError) as e:
                np.plan_empty("pump_bad", board, repo=self.repo, mcudb=TEST_MCUDB, safety=choice)
            self.assertIn(fragment, str(e.exception))
        self.assertFalse((self.repo / "projects" / "pump_bad").exists())
        with self.assertRaises(np.NewProjectError):
            np.plan_empty("pump_bad", board, repo=self.repo, mcudb=TEST_MCUDB, safety="PUMP_EN")

    def test_command_line_safety_options(self):
        """Nothing safety related without an explicit choice: all three options or none, only for --board."""
        for argv in (["--test-pin", "PTA0"], ["--safe-state-output", "A", "--locked-pin", "B"]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()) as err, \
                    self.assertRaises(SystemExit) as stop:
                cli.main(["new", "cli_s", "--board", "testboard", "--dry-run", *argv])
            self.assertEqual(stop.exception.code, 2)
            self.assertIn("go together", err.getvalue())
        with mock.patch.object(np, "plan", side_effect=np.NewProjectError(["stop"])) as planned, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            cli.main(["new", "cli_s", "--board", "testboard", "--dry-run", "--safe-state-output", "LED_RED",
                      "--locked-pin", "LED_GREEN", "--test-pin", "PC14"])
            cli.main(["new", "cli_s", "--board", "testboard", "--dry-run"])
        self.assertEqual([c.kwargs["safety"] for c in planned.call_args_list],
                         [np.SafetyChoice("LED_RED", "LED_GREEN", "PC14"), None])
        out = io.StringIO()
        with mock.patch.object(np, "safety_candidates", return_value=np.SafetyCandidates(
                "testboard", [("LED_RED", "PB1")], ["PC14"], True, "platforms/x/port/port.yaml")), \
                contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["new", "--safety-candidates", "--board", "testboard"]), 0)
        self.assertIn("digital outputs: LED_RED (PB1)", out.getvalue())
        self.assertIn("test pin candidates (GPIO pins the board file does not describe): PC14", out.getvalue())
        self.assertIn("the family port needs the section (platforms/x/port/port.yaml)", out.getvalue())

    def test_unknown_signal_choice_is_refused(self):
        with self.assertRaises(np.NewProjectError) as e:
            np.plan_empty("badsig", "testboard", repo=self.repo, mcudb=TEST_MCUDB, signals="some")
        self.assertIn("unknown signal choice", str(e.exception))

    def test_command_line_all_signals(self):
        """--all-signals reaches the core as signals="all"; without it the core keeps "minimal"."""
        for argv, expected in ((["--all-signals"], "all"), ([], "minimal")):
            with self.subTest(argv=argv), mock.patch.object(np, "plan", side_effect=np.NewProjectError(["stop"])) \
                    as planned, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = cli.main(["new", "cliall", "--board", "testboard", "--dry-run", *argv])
            self.assertEqual(code, 1)
            self.assertEqual(planned.call_args.kwargs["signals"], expected)

    def test_command_line_calibration(self):
        """TOOL-178 (proposal A of TOOL-131): --calibration reaches plan_empty, only with --board; --diag names
        the standard diagnostics."""
        for argv, expected in ((["--calibration"], True), ([], False)):
            with self.subTest(argv=argv), mock.patch.object(np, "plan", side_effect=np.NewProjectError(["stop"])) \
                    as planned, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = cli.main(["new", "clical", "--board", "testboard", "--diag", "--dry-run", *argv])
            self.assertEqual(code, 1)
            self.assertEqual(planned.call_args.kwargs["calibration"], expected)
        with contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit) as stop:
            cli.main(["new", "clical", "--copy", "testproject", "--calibration", "--dry-run"])
        self.assertEqual(stop.exception.code, 2)
        self.assertIn("--calibration only with --board", err.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as out, self.assertRaises(SystemExit):
            cli.main(["new", "--help"])
        self.assertIn("standard diagnostics of neXtBSW", " ".join(out.getvalue().split()))
        # the real core: the plan of an empty project with the option carries the calibration segment
        plan = np.plan_empty("clical", "testboard", repo=self.repo, mcudb=TEST_MCUDB, with_diag=True,
                             calibration=True)
        project = next(f.text for f in plan.files if f.path == "project.yaml")
        self.assertIn("calibration:", project)


if __name__ == "__main__":
    unittest.main()
