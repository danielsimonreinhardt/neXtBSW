"""SYS-116: the value sets of the safe states in the generated safe outputs table (tools/bswgen collect_safe_outputs,
bsp/safeOutputs/safeOutputsConfiguration.h and .cmake).

- Projects without an active short (eval board demo and charge, testproject_safe_outputs, S32K116, S32K148 build test)
  get no extended table and NX_SAFE_OUTPUT_SELECT OFF - their generated header is the one before SYS-116 (the
  reference output of test_reference_output.py holds it byte for byte).
- testproject_active_short (freewheel and both shorts, start test) and testproject_active_short_low (short low only,
  no bound-enable facts on the board): the real table definition of the target (platforms/s32k1xx/hardFaultHandler/
  src/SafeOutputSelection.cpp) is compiled on the host with the generat and the real NXP register layouts, its
  compile-time checks run, and every entry and group is compared with the independent expectation
  test/safe_state_expectation.py (board.yaml, project, module manifests, reference manual). The same unit is compiled
  for Cortex-M4 and Cortex-M0+ with arm-none-eabi-g++ (layout check against the assembler offsets).
- Generator mutants (short values swapped, an enable switched off in the short, an enable of no group left on, the
  enable gated by its port instead of the timer, the timer entry without group) must be detected.

The code facts that gate the short (tools/nxconfig/safestate.py) do not exist in this tree yet; the generation here
runs with them simulated, as the follow-up that adds them will run.

Run: .venv/Scripts/python tools/bswgen/test_bswgen_safe_state.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE / "test"))

import bswgen  # noqa: E402
import safe_state_expectation  # noqa: E402
from nxconfig import safestate  # noqa: E402

PROFILES = HERE / "test" / "profiles" / "projects"
SHORT = PROFILES / "testproject_active_short" / "project.yaml"
SHORT_LOW = PROFILES / "testproject_active_short_low" / "project.yaml"
PLAIN = (REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml",
         REPO / "projects" / "evalboard_s32k144_charge" / "project.yaml",
         REPO / "projects" / "buildtest_s32k148_min" / "project.yaml",
         REPO / "projects" / "s32k116evb_minimal" / "project.yaml",
         PROFILES / "testproject_safe_outputs" / "project.yaml",
         REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full" / "project.yaml")
# PLAT-62: the eval board projects are private (PLAT-46 F10) and missing in the exported open tree
PLAIN = tuple(p for p in PLAIN if p.is_file())
NXP = REPO / "external" / "openbsw" / "platforms" / "s32k1xx" / "bsp" / "bspMcu" / "include" / "3rdparty" / "nxp"
HOST = HERE / "test" / "safe_outputs_host"
HARD_FAULT = REPO / "platforms" / "s32k1xx" / "hardFaultHandler"
GCC_PATH = os.pathsep.join(p for p in (os.environ.get("NX_ARM_GCC_BIN"), os.environ.get("PATH")) if p)
ARM_GXX = shutil.which("arm-none-eabi-g++", path=GCC_PATH)
FACTS = safestate.CodeFacts(())

try:
    import ziglang  # noqa: F401
    HAVE_ZIG = True
except ImportError:
    HAVE_ZIG = False


def generate(project_file: Path, out: Path) -> Path:
    with mock.patch.object(safestate, "read_facts", lambda repo, family: FACTS):
        bswgen.generate(project_file, out)
    return out


def header(gen: Path) -> str:
    return (gen / "bsp" / "safeOutputs" / "safeOutputsConfiguration.h").read_text(encoding="utf-8")


def cmake(gen: Path) -> str:
    return (gen / "bsp" / "safeOutputs" / "safeOutputsConfiguration.cmake").read_text(encoding="utf-8")


class SafeStateTableTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build_and_run(self, gen: Path) -> tuple[list[tuple[int, ...]], list[tuple]]:
        exe = self.tmp / f"select_{len(list(self.tmp.glob('*.exe')))}.exe"
        cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
               "-DNX_SAFE_OUTPUT_SELECT=1", f"-I{HOST / 'stubs'}", f"-I{gen}", f"-I{HARD_FAULT / 'include'}",
               f"-isystem{NXP}", str(HOST / "safe_state_select_host_test.cpp"), "-o", str(exe)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise AssertionError("build: " + r.stderr[-2500:])
        r = subprocess.run([str(exe)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)
        lines = r.stdout.splitlines()
        rows = [tuple(int(v, 16) for v in line.split()[1:]) for line in lines if line.startswith("ENTRY ")]
        self.assertIn(f"COUNT {len(rows)}", lines)
        groups = [(p[1], int(p[2], 16), int(p[3]), int(p[4])) for p in (line.split() for line in lines)
                  if p[0] == "GROUP"]
        return rows, groups

    @staticmethod
    def expectation(project_file: Path):
        entries, groups = safe_state_expectation.expected(project_file)
        return [e.words for e in entries], [(g.name, g.allowed, g.test, g.test_us) for g in groups]

    def test_projects_without_a_short_keep_their_table(self):
        for project in PLAIN:
            with self.subTest(project=project.parent.name):
                gen = generate(project, self.tmp / project.parent.name)
                self.assertNotIn("NX_SAFE_OUTPUT_SELECT", header(gen))
                self.assertNotIn("NX_SAFE_OUTPUTS_SELECT", header(gen))
                self.assertIn("set(NX_SAFE_OUTPUT_SELECT OFF)", cmake(gen))
                self.assertIsNone(safe_state_expectation.expected(project))

    @unittest.skipUnless(HAVE_ZIG, "ziglang not installed")
    def test_extended_table_of_the_profiles(self):
        for project in (SHORT, SHORT_LOW):
            with self.subTest(project=project.parent.name):
                gen = generate(project, self.tmp / project.parent.name)
                self.assertIn("set(NX_SAFE_OUTPUT_SELECT ON)", cmake(gen))
                self.assertIn("#define NX_SAFE_OUTPUT_SELECT_GENERATED 1", header(gen))
                rows, groups = self.build_and_run(gen)
                expect_rows, expect_groups = self.expectation(project)
                print(f"\n{project.parent.name}: " + "; ".join(
                    f"[0x{r[0]:08X}] fw 0x{r[1]:X} g{r[4]} low 0x{r[7]:X} high 0x{r[8]:X}" for r in rows),
                      file=sys.stderr)
                self.assertEqual(rows, expect_rows)
                self.assertEqual(groups, expect_groups)

    def test_own_profile_values(self):
        """What the expectation says for testproject_active_short, spelled out: FTM1 channels 2..5, low sides 3 and
        5, high sides 2 and 4; GD_A (PTD16, bound) left as it is, GD_B (PTA7, active low, not bound) off."""
        rows, groups = self.expectation(SHORT)
        self.assertEqual([r[1:2] + r[4:5] + r[7:] for r in rows],
                         [(0x3C, 1, 0x283C, 0x143C), (1 << 16, 1, 0, 0), (1 << 7, 0, 1 << 7, 1 << 7)])
        self.assertEqual(groups, [("BRIDGE", 0x7, 1, 500)])
        rows, groups = self.expectation(SHORT_LOW)
        self.assertEqual([r[7:] for r in rows], [(0x283C, 0x3C), (0, 1 << 16), (1 << 7, 1 << 7)])
        self.assertEqual(groups, [("BRIDGE", 0x3, 0, 0)])

    @unittest.skipUnless(ARM_GXX, "arm-none-eabi-g++ not found (PATH or NX_ARM_GCC_BIN)")
    def test_target_layout(self):
        gen = generate(SHORT, self.tmp / "target")
        for cpu in ("-mcpu=cortex-m4", "-mcpu=cortex-m0plus"):
            with self.subTest(cpu=cpu):
                r = subprocess.run([ARM_GXX, cpu, "-mthumb", "-O2", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                                    "-fno-exceptions", "-fno-rtti", "-DNX_SAFE_OUTPUT_SELECT=1",
                                    f"-I{HOST / 'stubs'}", f"-I{gen}", f"-I{HARD_FAULT / 'include'}",
                                    f"-isystem{NXP}", "-c", str(HARD_FAULT / "src" / "SafeOutputSelection.cpp"),
                                    "-o", str(self.tmp / "sel.o")], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr[-2500:])

    @unittest.skipUnless(HAVE_ZIG, "ziglang not installed")
    def test_generator_mutants_are_detected(self):
        expect_rows, _ = self.expectation(SHORT)
        original = bswgen.Board.collect_safe_outputs

        def mutate(change):
            def collect(board):
                original(board)
                change(board.safe_outputs)
            return collect

        def swap(entries):
            entries[0]["short_low"], entries[0]["short_high"] = entries[0]["short_high"], entries[0]["short_low"]

        def enable_off(entries):
            entries[1]["short_low"] = entries[1]["value"]

        def loose_left_on(entries):
            entries[2]["short_low"] = "0U"

        def enable_port_gate(entries):
            entries[1]["group_gate"] = "PORTD"

        def timer_without_group(entries):
            entries[0]["group"] = 0

        for name, change in {"short values swapped": swap, "bound enable off in the short": enable_off,
                             "enable of no group left on": loose_left_on,
                             "enable gated by its port, not the timer": enable_port_gate,
                             "timer entry without group": timer_without_group}.items():
            with self.subTest(mutant=name):
                bswgen.Board.collect_safe_outputs = mutate(change)
                try:
                    gen = generate(SHORT, self.tmp / f"mutant_{len(list(self.tmp.glob('mutant_*')))}")
                finally:
                    bswgen.Board.collect_safe_outputs = original
                try:
                    rows, _ = self.build_and_run(gen)
                except AssertionError:
                    continue                    # refused by a compile-time check of the table
                self.assertNotEqual(rows, expect_rows)


if __name__ == "__main__":
    unittest.main()
