"""SYS-30 (gap G1): the read-back list of the safe outputs table (NX_SAFE_OUTPUT_READBACKS of
bsp/safeOutputs/safeOutputsConfiguration.h, defined in platforms/s32k1xx/port/safety/safeIo/src/registerList/
SafeOutputReadbackList.cpp).

The real list definition of the target is compiled on the host with the generat of every profile and the real NXP
register layouts, its compile-time check runs, and every entry is compared with the independent expectation
test/safe_outputs_expectation.py (expected_readbacks: board.yaml, project, module manifests, register addresses from the
reference manual): per entry of the safe outputs table, in its order, for a PWM group its software output control with
CHnOC and CHnOCV of the group's channels (expected: all forced, none active), for a safe_off output the pin level of its
port (PDIR, expected: the inactive level), gated by the same clock as the walk. Profiles: testproject_safe_outputs
(active-high and active-low enable, FTM1), the eval board demo, the charge project, the S32K148 build profile and the
S32K116 project (empty list). Generator mutants: the data register instead of the pin level, the active-low level
inverted, the mask without the output values, the list in another order than the table.

Run: .venv/Scripts/python tools/bswgen/test_bswgen_safe_readback.py
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE / "test"))

import bswgen  # noqa: E402
import safe_outputs_expectation  # noqa: E402

PROFILES = HERE / "test" / "profiles" / "projects"
OWN = PROFILES / "testproject_safe_outputs" / "project.yaml"
PROJECTS = (OWN, REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml",
            REPO / "projects" / "evalboard_s32k144_charge" / "project.yaml",
            REPO / "projects" / "buildtest_s32k148_min" / "project.yaml",
            REPO / "projects" / "s32k116evb_minimal" / "project.yaml",
            REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full" / "project.yaml")
COUNTS = (3, 3, 2, 0, 0, 3)
# PLAT-62: the eval board projects are private (PLAT-46 F10) and missing in the exported open tree
PROJECTS, COUNTS = map(tuple, zip(*[(p, c) for p, c in zip(PROJECTS, COUNTS) if p.is_file()]))
NXP = REPO / "external" / "openbsw" / "platforms" / "s32k1xx" / "bsp" / "bspMcu" / "include" / "3rdparty" / "nxp"
HOST = HERE / "test" / "safe_outputs_host"
SAFE_IO = REPO / "platforms" / "s32k1xx" / "port" / "safety" / "safeIo"

try:
    import ziglang  # noqa: F401
    HAVE_ZIG = True
except ImportError:
    HAVE_ZIG = False


@unittest.skipUnless(HAVE_ZIG, "ziglang not installed")
class SafeReadbackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build_and_run(self, gen: Path) -> list[tuple[int, ...]] | None:
        """Rows of the list, None if the list definition does not compile (its compile-time check)."""
        exe = self.tmp / f"readback_{len(list(self.tmp.glob('*.exe')))}.exe"
        cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
               f"-I{HOST / 'stubs'}", f"-I{gen}", f"-I{SAFE_IO / 'include'}", f"-isystem{NXP}",
               str(HOST / "safe_readback_host_test.cpp"), "-o", str(exe)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            return None
        r = subprocess.run([str(exe)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)
        rows = [tuple(int(v, 16) for v in line.split()[1:]) for line in r.stdout.splitlines()
                if line.startswith("READBACK ")]
        count = next(line.split()[1:] for line in r.stdout.splitlines() if line.startswith("COUNT "))
        self.assertEqual(count[0], count[1], "one read-back per entry of the table")
        return rows

    def generate(self, project: Path, name: str) -> Path:
        gen = self.tmp / name
        bswgen.generate(project, gen)
        return gen

    def test_every_profile(self):
        for project, count in zip(PROJECTS, COUNTS):
            with self.subTest(project=project.parent.name):
                expected = safe_outputs_expectation.expected_readbacks(project)
                self.assertEqual(len(expected), count)
                rows = self.build_and_run(self.generate(project, project.parent.name))
                self.assertIsNotNone(rows, "the list definition does not compile")
                print(f"\n{project.parent.name}: " + "; ".join(
                    f"[0x{r[0]:08X}] & 0x{r[1]:X} == 0x{r[2]:X}" for r in rows), file=sys.stderr)
                self.assertEqual(rows, expected)

    def test_own_profile_values(self):
        """FTM1 channels 2..5: SWOCTRL 0x3C3C/0x3C; GD_A PTD16 active high: PDIR bit 16 low; GD_B PTA7 active low:
        PDIR bit 7 high."""
        rows = safe_outputs_expectation.expected_readbacks(OWN)
        self.assertEqual(rows[0][:3], (0x40039094, 0x3C3C, 0x3C))
        self.assertEqual(rows[1][:3], (0x400FF0D0, 1 << 16, 0))
        self.assertEqual(rows[2][:3], (0x400FF010, 1 << 7, 1 << 7))

    def test_generator_mutants_are_detected(self):
        expected = safe_outputs_expectation.expected_readbacks(OWN)
        original = bswgen.Board.collect_safe_outputs

        def mutate(change):
            def collect(board):
                original(board)
                change(board.safe_outputs)
            return collect

        def data_register(entries):
            for e in entries[1:]:
                e["readback"]["address"] = e["readback"]["address"].replace("PDIR", "PDOR")

        def active_low_inverted(entries):
            entries[2]["readback"]["expected"] = "0U"

        def mask_without_values(entries):
            entries[0]["readback"]["mask"] = entries[0]["value"]

        def other_order(entries):
            entries[1]["readback"], entries[2]["readback"] = entries[2]["readback"], entries[1]["readback"]

        for name, change in {"data register instead of the pin level": data_register,
                             "active-low level inverted": active_low_inverted,
                             "mask without the output values": mask_without_values,
                             "list in another order than the table": other_order}.items():
            with self.subTest(mutant=name):
                bswgen.Board.collect_safe_outputs = mutate(change)
                try:
                    gen = self.generate(OWN, f"mutant_{len(list(self.tmp.glob('mutant_*')))}")
                finally:
                    bswgen.Board.collect_safe_outputs = original
                self.assertNotEqual(self.build_and_run(gen), expected)


if __name__ == "__main__":
    unittest.main()
