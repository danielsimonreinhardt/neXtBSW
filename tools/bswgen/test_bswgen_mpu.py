"""Tests of the generated MPU facts of the derivative (ARCH-16).

mcudb holds the number of MPU region descriptors per derivative (mpu.regions, S32K144 8, S32K148 16), bswgen
turns it into mcu/derivativeFacts.h (NX_MPU_REGIONS and the CESR[NRGD] code of the family), and
ecu/.../safeMemory/mpu.h uses it instead of a fixed 8. The host test MpuHostTest.cpp is compiled and run against
a register model of the MPU for both region counts, so the header is checked as code and not only as text.

Finding ARCH-09 B3: with the fixed 8 the S32K148 (NRGD = 2) reported an enabled MPU as disabled, and the safe
supervisor would have gone to limp home in every safety cycle.

Run: .venv/Scripts/python -m unittest tools/bswgen/test_bswgen_mpu.py
(the compile tests need ziglang in the .venv)
"""

import copy
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

import bswgen  # noqa: E402

# PLAT-62: an S32K144 project = the own full test profile (memory layout of the eval board demo) instead of the
# eval board project, which stays private (PLAT-46 F10) and is missing in the exported open tree
EVAL_PROJECT = REPO / "tools/nxconfig/test/profiles/projects/testproject_full/project.yaml"
BUILDTEST_PROJECT = REPO / "projects/buildtest_s32k148_min/project.yaml"
SAFE_MEMORY = REPO / "platforms/s32k1xx/port/safety/safeMemory"       # PLAT-23: family port template
HOST_TEST = SAFE_MEMORY / "test/host/MpuHostTest.cpp"
STUBS = SAFE_MEMORY / "test/host/stubs"

FLAGS = ["-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion", "-Wno-sign-conversion", "-Werror"]

try:
    import ziglang  # noqa: F401
    ZIG = True
except ImportError:
    ZIG = False


class MpuFacts(unittest.TestCase):
    """Generates for a real project, optionally with a patched copy of mcudb, and compiles the host test."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mpu-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def mcudb(self, derivative: str, patch) -> Path:
        """Copy of mcudb whose <derivative>.yaml went through `patch` (tables and sources are not needed)."""
        db = self.tmp / "mcudb"
        if not db.exists():
            shutil.copytree(REPO / "mcudb", db, ignore=shutil.ignore_patterns("sources", "tools"))
        file = next(db.glob(f"*/{derivative}.yaml"))
        doc = yaml.safe_load(file.read_text(encoding="utf-8"))
        patch(doc)
        file.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        return db

    def generate(self, project: Path, name: str, derivative: str = None, patch=None) -> Path:
        """bswgen output of a project; the project file itself is copied, its ECU description stays referenced."""
        project_dir = self.tmp / "projects" / name
        project_dir.mkdir(parents=True, exist_ok=True)
        doc = copy.deepcopy(yaml.safe_load(project.read_text(encoding="utf-8")))
        # the board reference is relative to the project file, which moves here
        doc["project"]["board"] = (project.parent / doc["project"]["board"]).resolve().as_posix()
        # TOOL-114: the RTE is required on a real board - the original rte.yaml, by its absolute path
        doc["project"]["rte"] = (project.parent / doc["project"]["rte"]).resolve().as_posix()
        doc["project"].pop("diag", None)  # the copy has no diag.yaml next to it (ARCH-51)
        path = project_dir / "project.yaml"
        path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        args = [self.mcudb(derivative, patch)] if patch else []
        out = self.tmp / f"out-{name}"
        bswgen.generate(path, out, *args)
        return out

    def facts(self, out: Path) -> str:
        return (out / "mcu/derivativeFacts.h").read_text(encoding="utf-8")

    def compile_host_test(self, out: Path, name: str):
        exe = self.tmp / f"{name}.exe"
        cmd = [sys.executable, "-m", "ziglang", "c++", *FLAGS, f"-I{out}", f"-I{SAFE_MEMORY / 'include'}",
               f"-I{STUBS}", str(HOST_TEST), "-o", str(exe)]
        return subprocess.run(cmd, capture_output=True, text=True), exe

    def run_host_test(self, out: Path, name: str) -> str:
        compiled, exe = self.compile_host_test(out, name)
        self.assertEqual(compiled.returncode, 0, compiled.stderr[-3000:])
        run = subprocess.run([str(exe)], capture_output=True, text=True)
        print("\n" + "\n".join(run.stdout.strip().splitlines()[-4:]), file=sys.stderr)
        self.assertEqual(run.returncode, 0, run.stdout[-3000:])
        self.assertRegex(run.stdout, r"\d+ checks, 0 failures")
        return run.stdout


class GeneratedFactsTest(MpuFacts):
    def test_eval_board_has_eight_regions(self):
        """S32K144 (eval board): 8 region descriptors, CESR[NRGD] = 0 (mcudb mpu.regions, RM Table 15-4)."""
        out = self.generate(EVAL_PROJECT, "eval")
        text = self.facts(out)
        self.assertIn("#define NX_MPU_REGIONS 8\n", text)
        self.assertIn("#define NX_MPU_CESR_NRGD 0\n", text)
        self.assertIn("RGD[0..7]", text)
        # the NVIC priority bits of the derivative live in the same header and are used from there (PLAT-10)
        self.assertIn("#define NX_NVIC_PRIORITY_BITS 4\n", text)
        self.assertIn('#include "mcu/derivativeFacts.h"',
                      (out / "interrupts/interruptPriorities.h").read_text(encoding="utf-8"))

    def test_other_derivative_has_sixteen_regions(self):
        """Question 3 (other derivative of the family): S32K148 gets 16 regions and NRGD = 2, no code change."""
        out = self.generate(BUILDTEST_PROJECT, "buildtest")
        text = self.facts(out)
        self.assertIn("#define NX_MPU_REGIONS 16\n", text)
        self.assertIn("#define NX_MPU_CESR_NRGD 2\n", text)
        self.assertIn("RGD[0..15]", text)

    def test_the_region_count_has_exactly_one_place(self):
        """One source (question 6): the count is only in mcudb, its register encoding only in this generator."""
        self.assertEqual(bswgen.MPU_NRGD_CODES, {8: 0, 12: 1, 16: 2})
        sources = {p.read_text(encoding="utf-8") for p in (REPO / "tools/bswgen/templates").glob("*.j2")}
        self.assertEqual(sum("NX_MPU_REGIONS" in t for t in sources), 1)

    def test_unknown_region_count_is_refused(self):
        """A region count the CESR[NRGD] field cannot express stops the generator with the mcudb path."""
        def ten_regions(doc):
            doc["mpu"]["regions"] = 10

        with self.assertRaises(bswgen.ConfigError) as ctx:
            self.generate(EVAL_PROJECT, "ten", derivative="S32K144", patch=ten_regions)
        self.assertIn("mcudb mpu.regions", str(ctx.exception))
        self.assertIn("S32K144", str(ctx.exception))
        self.assertIn("8, 12, 16", str(ctx.exception))

    def test_twelve_regions_are_encoded_too(self):
        """The third code of the reference manual (NRGD = 1) needs no code change either."""
        def twelve_regions(doc):
            doc["mpu"]["regions"] = 12

        out = self.generate(EVAL_PROJECT, "twelve", derivative="S32K144", patch=twelve_regions)
        self.assertIn("#define NX_MPU_REGIONS 12\n", self.facts(out))
        self.assertIn("#define NX_MPU_CESR_NRGD 1\n", self.facts(out))

    def test_derivative_without_mpu_defines_nothing(self):
        """Without an MPU in the database no count is invented; the header only says so."""
        def no_mpu(doc):
            doc.pop("mpu", None)

        out = self.generate(EVAL_PROJECT, "nompu", derivative="S32K144", patch=no_mpu)
        text = self.facts(out)
        self.assertNotIn("#define NX_MPU_REGIONS", text)
        self.assertNotIn("#define NX_MPU_CESR_NRGD", text)
        self.assertIn("describes no MPU in the derivative database", text)


@unittest.skipUnless(ZIG, "ziglang not installed")
class MpuHeaderHostTest(MpuFacts):
    def test_eight_regions(self):
        """Register model with 8 region descriptors (S32K144): enable bit, NRGD codes, descriptor access."""
        out = self.generate(EVAL_PROJECT, "eval")
        self.assertIn("8 MPU region descriptors", self.run_host_test(out, "mpu8"))

    def test_sixteen_regions(self):
        """Same source, S32K148: the enabled MPU is recognised although NRGD = 2 (finding ARCH-09 B3)."""
        out = self.generate(BUILDTEST_PROJECT, "buildtest")
        stdout = self.run_host_test(out, "mpu16")
        self.assertIn("16 MPU region descriptors", stdout)
        self.assertIn("looked disabled before ARCH-16", stdout)

    def test_twelve_regions(self):
        """A derivative the family allows but no chip uses yet: nothing in the header is hard coded to 8 or 16."""
        def twelve_regions(doc):
            doc["mpu"]["regions"] = 12

        out = self.generate(EVAL_PROJECT, "twelve", derivative="S32K144", patch=twelve_regions)
        self.assertIn("12 MPU region descriptors", self.run_host_test(out, "mpu12"))

    def test_mpu_header_without_the_facts_does_not_compile(self):
        """Second line of defence: no region count in mcudb -> #error instead of an assumed 8."""
        def no_mpu(doc):
            doc.pop("mpu", None)

        out = self.generate(EVAL_PROJECT, "nompu", derivative="S32K144", patch=no_mpu)
        compiled, _ = self.compile_host_test(out, "nompu")
        self.assertNotEqual(compiled.returncode, 0)
        self.assertIn("no MPU in the derivative database", compiled.stderr)

    def test_device_header_with_too_few_descriptors_does_not_compile(self):
        """The device header may describe a larger derivative, never a smaller one (static_assert in mpu.h)."""
        out = self.generate(BUILDTEST_PROJECT, "buildtest")
        compiled, _ = self.compile_host_test_with_rgd_count(out, 8)
        self.assertNotEqual(compiled.returncode, 0)
        self.assertIn("fewer MPU region descriptors", compiled.stderr)

    def compile_host_test_with_rgd_count(self, out: Path, count: int):
        """Copy of the stub whose device header declares only `count` region descriptors."""
        stubs = self.tmp / f"stubs-{count}"
        shutil.copytree(STUBS, stubs, dirs_exist_ok=True)
        header = stubs / "mcu" / "mcu.h"
        text = header.read_text(encoding="utf-8").replace("#define MPU_RGD_COUNT    16u",
                                                          f"#define MPU_RGD_COUNT    {count}u")
        header.write_text(text, encoding="utf-8")
        exe = self.tmp / f"rgd{count}.exe"
        cmd = [sys.executable, "-m", "ziglang", "c++", *FLAGS, f"-I{out}", f"-I{SAFE_MEMORY / 'include'}",
               f"-I{stubs}", str(HOST_TEST), "-o", str(exe)]
        return subprocess.run(cmd, capture_output=True, text=True), exe


if __name__ == "__main__":
    unittest.main()
