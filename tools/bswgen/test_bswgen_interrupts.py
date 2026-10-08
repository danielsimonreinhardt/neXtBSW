"""Tests of the generated interrupt priorities and derivative features (PLAT-21).

The generated header interrupts/interruptPriorities.h feeds platforms/common/interruptPriority with the NVIC
priority bits of the derivative, the RTOS syscall level of the project and a level macro per interrupt rank; the
generated features/derivativeFeatures.h says which derivative features exist (LMEM cache). Both are compiled here
for the host with the unchanged nxInterruptPriority.h, with the eval board profile and with modified copies
(other priority bits, no cache, no interrupt at all), so the values are not only checked as text.

ARCH-24: mcu/derivativeFacts.h names the core architecture and interrupt controller (NX_CORE_ARCHITECTURE_*,
NX_CORE_INTERRUPT_CONTROLLER_*); on a VIM core (synthetic test profile TESTR4F/testproject_r4f) the ranks become
VIM channels (NX_IRQ_CHANNEL_*), also compiled with arm-none-eabi-gcc -mcpu=cortex-r4f when it is available.

Run: .venv/Scripts/python -m unittest tools/bswgen/test_bswgen_interrupts.py
(the compile tests need ziglang in the .venv)
"""

import copy
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import get_args

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))

import bswgen  # noqa: E402
import nxconfig  # noqa: E402
from nxconfig import model  # noqa: E402
from nxconfig.config import CORES  # noqa: E402

# PLAT-62: the own full test profile (same interrupt ranks as the eval board demo) instead of the eval board, which
# stays private (PLAT-46 F10) - a test against it would be red in the exported open tree
PROFILES = REPO / "tools/nxconfig/test/profiles"
BOARD = yaml.safe_load((PROFILES / "boards/testboard_full/board.yaml").read_text(encoding="utf-8"))
PROJECT = yaml.safe_load((PROFILES / "projects/testproject_full/project.yaml").read_text(encoding="utf-8"))
PRIORITY_INCLUDE = REPO / "platforms/common/interruptPriority/include"
TEST_MCUDB = REPO / "tools/nxconfig/test/mcudb"
R4F_PROJECT = REPO / "tools/nxconfig/test/profiles/projects/testproject_r4f/project.yaml"
GCC_PATH = os.pathsep.join(p for p in (os.environ.get("NX_ARM_GCC_BIN"), os.environ.get("PATH")) if p)
ARM_GCC = shutil.which("arm-none-eabi-gcc", path=GCC_PATH)

try:
    import ziglang  # noqa: F401
    ZIG = True
except ImportError:
    ZIG = False


class Generated(unittest.TestCase):
    """Runs bswgen on a copy of the full test profile (board, project and, if needed, the derivative)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def generate(self, project=None, board=None, derivative=None):
        board_dir, project_dir = self.tmp / "boards/b", self.tmp / "projects/p"
        board_dir.mkdir(parents=True, exist_ok=True)
        project_dir.mkdir(parents=True, exist_ok=True)
        (board_dir / "board.yaml").write_text(yaml.safe_dump(board or BOARD, sort_keys=False), encoding="utf-8")
        project = copy.deepcopy(project or PROJECT)
        project["project"]["board"] = "../../boards/b"
        project["project"].pop("rte", None)
        project["project"].pop("diag", None)  # the copy has no diag.yaml next to it (ARCH-51)
        path = project_dir / "project.yaml"
        path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        mcudb = []
        if derivative is not None:
            db = self.tmp / "mcudb"
            if not db.exists():
                shutil.copytree(REPO / "mcudb", db, ignore=shutil.ignore_patterns("sources", "tools"))
            file = db / "s32k1xx/S32K144.yaml"
            doc = yaml.safe_load(file.read_text(encoding="utf-8"))
            derivative(doc)
            file.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
            mcudb = [db]
        bswgen.generate(path, self.tmp / "out", *mcudb)
        return self.tmp / "out"

    @staticmethod
    def project(**interrupts):
        project = copy.deepcopy(PROJECT)
        project["use"]["can"] = ["CAN0"]        # COM-05: the demo also uses CAN1; these profiles rank CAN0 only
        if interrupts:
            project["interrupts"] = interrupts
        else:
            project.pop("interrupts", None)
        return project

    def read(self, out: Path, rel: str) -> str:
        return (out / rel).read_text(encoding="utf-8")

    def values(self, out: Path, *expressions: str, language="c++"):
        """Compiles a program that prints the given macro expressions and returns their values."""
        body = "".join(f'    printf("%u\\n", (unsigned)({e}));\n' for e in expressions)
        source = self.tmp / ("probe.c" if language == "c" else "probe.cpp")
        source.write_text('#include <stdio.h>\n#include "interrupts/interruptPriorities.h"\n'
                          '#include "features/derivativeFeatures.h"\n'
                          f"int main(void)\n{{\n{body}    return 0;\n}}\n", encoding="utf-8")
        r = self.compile(out, source, language)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        exe = self.tmp / "probe.exe"
        out_text = subprocess.run([str(exe)], capture_output=True, text=True, check=True).stdout
        return [int(line) for line in out_text.split()]

    def compile(self, out: Path, source: Path, language="c++"):
        std, driver = ("-std=c99", "cc") if language == "c" else ("-std=c++17", "c++")
        return subprocess.run([sys.executable, "-m", "ziglang", driver, std, "-Wall", "-Wextra", "-Werror",
                               f"-I{out}", f"-I{PRIORITY_INCLUDE}", str(source), "-o", str(self.tmp / "probe.exe")],
                              capture_output=True, text=True)


class InterruptPriorityHeaderTest(Generated):
    def test_eval_board_levels(self):
        """The demo project keeps the levels verified at the board: CAN 8 (OpenBSW reference), UART-RX 9 (IO-17)."""
        out = self.generate()
        text = self.read(out, "interrupts/interruptPriorities.h")
        # ARCH-16: the bits of the derivative are defined once, in mcu/derivativeFacts.h, and included from there
        self.assertIn('#include "mcu/derivativeFacts.h"', text)
        self.assertNotIn("#define NX_NVIC_PRIORITY_BITS", text)
        self.assertIn("#define NX_NVIC_PRIORITY_BITS 4", self.read(out, "mcu/derivativeFacts.h"))
        self.assertIn("#define NX_IRQ_MAX_SYSCALL_LEVEL 5", text)
        self.assertIn('#include "interrupts/nxInterruptPriority.h"', text)
        self.assertIn("// can CAN0: rank 3 -> NVIC level 8", text)
        self.assertIn("// uart UART: rank 4 -> NVIC level 9", text)
        self.assertEqual(self.read(out, "features/derivativeFeatures.h").count("#define NX_FEATURE_LMEM_CACHE 1"), 1)

    @unittest.skipUnless(ZIG, "ziglang not installed")
    def test_eval_board_levels_compiled(self):
        out = self.generate()
        for language in ("c", "c++"):
            with self.subTest(language=language):
                # ARCH-07: every used CAN bus has a rank (COM-05: CAN1 too, same level as CAN0)
                self.assertEqual(self.values(out, "NX_IRQ_LEVEL_CAN_CAN0",
                                             "NX_IRQ_LEVEL_UART_UART", "NX_IRQ_RANK_CAN_CAN0",
                                             "NX_IRQ_OS_RANK_COUNT", "NX_IRQ_LOWEST_LEVEL",
                                             "NX_FREERTOS_MAX_SYSCALL_INTERRUPT_PRIORITY",
                                             "NX_FREERTOS_KERNEL_INTERRUPT_PRIORITY",
                                             "NX_FEATURE_LMEM_CACHE", language=language),
                                 [8, 9, 3, 11, 15, 0x50, 0xF0, 1])

    @unittest.skipUnless(ZIG, "ziglang not installed")
    def test_other_derivative_and_syscall_level(self):
        """Same ranks, derivative with 3 priority bits and no LMEM cache: the levels follow, the project stays."""
        project = self.project(max_syscall_level=1,
                               ranks=[{"source": "can", "name": "CAN0", "rank": 3},
                                      {"source": "uart", "name": "UART", "rank": 4}])

        def derivative(doc):
            doc["derivative"]["nvic_priority_bits"] = 3
            doc["derivative"]["features"] = []

        out = self.generate(project=project, derivative=derivative)
        self.assertIn("implements 3 NVIC priority bits", self.read(out, "interrupts/interruptPriorities.h"))
        self.assertIn("#define NX_NVIC_PRIORITY_BITS 3", self.read(out, "mcu/derivativeFacts.h"))
        self.assertEqual(self.values(out, "NX_IRQ_LEVEL_CAN_CAN0", "NX_IRQ_LEVEL_UART_UART", "NX_IRQ_OS_RANK_COUNT",
                                     "NX_FEATURE_LMEM_CACHE"),
                         [4, 5, 7, 0])

    @unittest.skipUnless(ZIG, "ziglang not installed")
    def test_project_without_interrupt_ranks(self):
        """A project that names no rank still gets a usable header (bits and syscall level)."""
        # ARCH-33: a used CAN bus without rank is an error of nxconfig, so the project uses no CAN bus and no UART
        project = self.project()
        project["use"]["can"], project["use"]["uart"] = [], []
        project["modules"]["ecuState"]["wakeup_can"] = "none"     # the demo names CAN0 (COM-05)
        project.pop("xcp", None)        # TOOL-26: XCP needs a CAN bus
        out = self.generate(project=project)
        text = self.read(out, "interrupts/interruptPriorities.h")
        self.assertIn("The project describes no interrupt rank", text)
        self.assertNotIn("NX_IRQ_LEVEL_CAN", text)
        # default of the core architecture (ARMv7-M): syscall level 1, so 15 ranks
        self.assertEqual(self.values(out, "NX_IRQ_MAX_SYSCALL_LEVEL", "NX_IRQ_OS_RANK_COUNT"), [1, 15])

    @unittest.skipUnless(ZIG, "ziglang not installed")
    def test_rank_beyond_the_derivative_does_not_compile(self):
        """Second line of defence behind nxconfig: a rank that does not fit is a compile error."""
        def derivative(doc):
            doc["derivative"]["nvic_priority_bits"] = 3

        out = self.generate(project=self.project(max_syscall_level=1,
                                                 ranks=[{"source": "can", "name": "CAN0", "rank": 6}]),
                            derivative=derivative)
        self.assertEqual(self.values(out, "NX_IRQ_LEVEL_CAN_CAN0"), [7])         # last available rank
        source = self.tmp / "bad.cpp"
        source.write_text('#include "interrupts/interruptPriorities.h"\n'
                          "unsigned level = NX_IRQ_OS_LEVEL(7);\n", encoding="utf-8")
        r = self.compile(out, source)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("array size is negative", r.stderr)

    def test_derivative_without_priority_bits(self):
        """Without the bits of the derivative nothing is generated: nxconfig reports it with file, line and path."""
        def without_bits(doc):
            doc["derivative"].pop("nvic_priority_bits", None)

        with self.assertRaises(bswgen.ConfigError) as ctx:
            self.generate(derivative=without_bits)
        self.assertIn("S32K144.yaml", str(ctx.exception))
        self.assertIn("(derivative.nvic_priority_bits)", str(ctx.exception))
        self.assertIn("S32K144 has no nvic_priority_bits", str(ctx.exception))
        # a project without interrupt priorities passes nxconfig (the entry is optional): bswgen stops instead
        with self.assertRaises(bswgen.ConfigError) as ctx:
            self.generate(project=self.project(), derivative=without_bits)
        self.assertIn("derivative has no nvic_priority_bits in mcudb", str(ctx.exception))

    def test_every_derivative_feature_has_a_macro(self):
        """One source: every feature of the schema (model.DerivativeInfo.features) is generated."""
        annotation = model.DerivativeInfo.model_fields["features"].annotation
        (literal,) = get_args(annotation)                       # list[Literal[...]]
        self.assertEqual(set(get_args(literal)), set(bswgen.FEATURE_MACROS))


class CoreMacroTest(Generated):
    """ARCH-24: the core architecture and controller come from the generator (config.CORES), not from __ARM_ARCH."""

    def test_eval_board_core_macros(self):
        out = self.generate()
        facts = self.read(out, "mcu/derivativeFacts.h")
        self.assertIn("#define NX_CORE_ARCHITECTURE_ARMV7M 1\n#define NX_CORE_INTERRUPT_CONTROLLER_NVIC 1\n", facts)
        self.assertEqual(facts.count("#define NX_CORE_"), 2)
        self.assertNotIn("NX_VIM_", facts)
        # the NVIC header of the eval board is unchanged by ARCH-24 (no VIM text, same level macros)
        self.assertNotIn("VIM", self.read(out, "interrupts/interruptPriorities.h"))

    def test_every_core_of_the_table(self):
        """Every core of nxconfig CORES gives macros that nxInterruptPriority.h knows (one source)."""
        header = (PRIORITY_INCLUDE / "interrupts/nxInterruptPriority.h").read_text(encoding="utf-8")
        for name in CORES:
            with self.subTest(core=name):
                config = SimpleNamespace(derivative=SimpleNamespace(derivative=SimpleNamespace(core=name, name="X")))
                core = bswgen.core_context(config)
                self.assertEqual(core["architecture_macro"], f"NX_CORE_ARCHITECTURE_{CORES[name].architecture.upper()}")
                self.assertIn(core["architecture_macro"], header)
                self.assertIn(core["controller_macro"], header)
        with self.assertRaises(bswgen.ConfigError):
            bswgen.core_context(SimpleNamespace(derivative=SimpleNamespace(
                derivative=SimpleNamespace(core="cortex-a53", name="X"))))

    def test_cortex_m0plus_project(self):
        """The S32K116 project (bswgen needs its SVD there) gets ARMv6-M: LPIT system timer, syscall level 0."""
        config = nxconfig.load(REPO / "projects/s32k116evb_minimal/project.yaml")
        core = bswgen.core_context(config)
        self.assertEqual((core["architecture_macro"], core["controller_macro"]),
                         ("NX_CORE_ARCHITECTURE_ARMV6M", "NX_CORE_INTERRUPT_CONTROLLER_NVIC"))

    def test_stm32_generator_writes_the_same_macros(self):
        import stm32gen
        config = nxconfig.load(REPO / "projects/nucleo_f446re_demo/project.yaml")
        facts = stm32gen.derivative_facts(config, "test")
        self.assertIn("#define NX_CORE_ARCHITECTURE_ARMV7M 1\n#define NX_CORE_INTERRUPT_CONTROLLER_NVIC 1\n", facts)

    @unittest.skipUnless(ZIG, "ziglang not installed")
    def test_macros_reach_the_priority_header(self):
        """Without the core macros of the generated derivativeFacts.h the priority header stops the build."""
        out = self.generate()
        facts = out / "mcu/derivativeFacts.h"
        facts.write_text(facts.read_text(encoding="utf-8").replace("#define NX_CORE_ARCHITECTURE_ARMV7M 1\n", ""),
                         encoding="utf-8")
        source = self.tmp / "bad.cpp"
        source.write_text('#include "interrupts/interruptPriorities.h"\n', encoding="utf-8")
        r = self.compile(out, source)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("exactly one NX_CORE_ARCHITECTURE_*", r.stderr)


class VimInterruptHeaderTest(Generated):
    """ARCH-24: interrupt ranks on a VIM core (synthetic TESTR4F, testproject_r4f of ARCH-23) become channels.

    bswgen as a whole serves the family s32k1xx; the two interrupt headers are rendered here with the contexts a
    Cortex-R family generator gets from this module (core_context, vim_interrupt_context)."""

    def config(self, derivative=None, project=None):
        mcudb = self.tmp / "mcudb"
        shutil.rmtree(mcudb, ignore_errors=True)
        shutil.copytree(TEST_MCUDB, mcudb)
        if derivative is not None:
            file = mcudb / "testfam/TESTR4F.yaml"
            doc = yaml.safe_load(file.read_text(encoding="utf-8"))
            derivative(doc)
            file.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        board_dir, project_dir = self.tmp / "boards/testboard_r4f", self.tmp / "projects/testproject_r4f"
        shutil.copytree(R4F_PROJECT.parents[2] / "boards/testboard_r4f", board_dir, dirs_exist_ok=True)
        project_dir.mkdir(parents=True, exist_ok=True)
        doc = yaml.safe_load(R4F_PROJECT.read_text(encoding="utf-8"))
        if project is not None:
            project(doc)
        path = project_dir / "project.yaml"
        path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        return nxconfig.load(path, mcudb)

    def render(self, config) -> Path:
        env = bswgen.template_env()
        ctx = {"source": "testproject_r4f", "irq": bswgen.vim_interrupt_context(config),
               "core": bswgen.core_context(config), "adc_converters": [], "mpu": None}
        out = self.tmp / "out"
        for rel, template in (("interrupts/interruptPriorities.h", "interruptPriorities.h.j2"),
                              ("mcu/derivativeFacts.h", "derivativeFacts.h.j2")):
            (out / rel).parent.mkdir(parents=True, exist_ok=True)
            (out / rel).write_text(env.get_template(template).render(ctx), encoding="utf-8")
        return out

    def test_channels_in_rank_order(self):
        """Ranks in reverse YAML order (BUS2 rank 1 first): BUS1 takes the first free channel 2, BUS2 channel 3."""
        out = self.render(self.config())
        facts = self.read(out, "mcu/derivativeFacts.h")
        self.assertIn("#define NX_CORE_ARCHITECTURE_ARMV7R 1\n#define NX_CORE_INTERRUPT_CONTROLLER_VIM 1\n", facts)
        self.assertNotIn("NX_NVIC_PRIORITY_BITS", facts)
        self.assertIn("#define NX_VIM_CHANNEL_COUNT 48\n#define NX_VIM_FIXED_CHANNEL_COUNT 2\n", facts)
        self.assertIn("#define NX_VIM_FIXED_MASK0 0x00000003UL\n", facts)
        text = self.read(out, "interrupts/interruptPriorities.h")
        self.assertNotIn("NX_IRQ_MAX_SYSCALL_LEVEL", text)
        self.assertNotIn("NVIC", text)
        self.assertIn("// can BUS1: rank 0 -> position 0 -> VIM channel 2", text)
        self.assertIn("#define NX_IRQ_CHANNEL_CAN_BUS1 NX_IRQ_OS_CHANNEL(2)", text)
        self.assertIn("#define NX_IRQ_CHANNEL_CAN_BUS2 NX_IRQ_OS_CHANNEL(3)", text)
        if ZIG:
            self.assertEqual(self.vim_values(out, "NX_IRQ_CHANNEL_CAN_BUS1", "NX_IRQ_CHANNEL_CAN_BUS2",
                                             "NX_IRQ_RANK_CAN_BUS2", "NX_IRQ_OS_CHANNEL_COUNT"), [2, 3, 1, 46])

    def test_fixed_channel_between_and_equal_ranks(self):
        """A fixed channel between the free ones is skipped; equal ranks keep the YAML order."""
        def derivative(doc):
            doc["vim"]["fixed"].append({"channel": 3, "request": "SYNTH_RTI", "kind": "irq"})

        def project(doc):
            doc["interrupts"]["ranks"] = [{"source": "can", "name": "BUS2", "rank": 5},
                                          {"source": "can", "name": "BUS1", "rank": 5}]

        out = self.render(self.config(derivative, project))
        text = self.read(out, "interrupts/interruptPriorities.h")
        self.assertIn("#define NX_IRQ_CHANNEL_CAN_BUS2 NX_IRQ_OS_CHANNEL(2)", text)
        self.assertIn("#define NX_IRQ_CHANNEL_CAN_BUS1 NX_IRQ_OS_CHANNEL(4)", text)
        self.assertIn("#define NX_VIM_FIXED_MASK0 0x0000000BUL", self.read(out, "mcu/derivativeFacts.h"))
        if ZIG:
            self.assertEqual(self.vim_values(out, "NX_IRQ_CHANNEL_CAN_BUS2", "NX_IRQ_CHANNEL_CAN_BUS1",
                                             "NX_VIM_CHANNEL_IS_FIXED(3)", "NX_IRQ_OS_CHANNEL_COUNT"), [2, 4, 1, 45])

    def test_fixed_channels_above_32_bits(self):
        def derivative(doc):
            doc["vim"]["channels"] = 96
            doc["vim"]["fixed"].append({"channel": 70, "request": "SYNTH_HIGH", "kind": "irq"})

        facts = self.read(self.render(self.config(derivative)), "mcu/derivativeFacts.h")
        self.assertIn("#define NX_VIM_FIXED_MASK0 0x00000003UL\n#define NX_VIM_FIXED_MASK1 0x00000000UL\n"
                      "#define NX_VIM_FIXED_MASK2 0x00000040UL\n#define NX_VIM_FIXED_MASK3 0x00000000UL\n", facts)

    def test_too_many_channels_for_the_header(self):
        def derivative(doc):
            doc["vim"]["channels"] = 129

        with self.assertRaises(bswgen.ConfigError) as ctx:
            bswgen.vim_interrupt_context(self.config(derivative))
        self.assertIn("at most 128", str(ctx.exception))

    @unittest.skipUnless(ARM_GCC, "arm-none-eabi-gcc (NX_ARM_GCC_BIN) not found")
    def test_generated_header_compiles_for_cortex_r4f(self):
        """With the ARM compiler for the R4F (__ARM_ARCH 7 like the M4F): no BASEPRI rules, channels as generated."""
        out = self.render(self.config())
        source = self.tmp / "probe.c"
        source.write_text('#include "interrupts/interruptPriorities.h"\n'
                          "_Static_assert(NX_IRQ_CHANNEL_CAN_BUS2 == 3U, \"channel\");\nint nxProbe;\n",
                          encoding="utf-8")
        r = subprocess.run([ARM_GCC, "-mcpu=cortex-r4f", "-std=c11", "-Wall", "-Wextra", "-Werror", "-fsyntax-only",
                            f"-I{out}", f"-I{PRIORITY_INCLUDE}", str(source)], capture_output=True, text=True,
                           env={**os.environ, "PATH": GCC_PATH})
        self.assertEqual(r.returncode, 0, r.stderr)

    def vim_values(self, out: Path, *expressions: str):
        body = "".join(f'    printf("%u\\n", (unsigned)({e}));\n' for e in expressions)
        source = self.tmp / "vim.c"
        source.write_text('#include <stdio.h>\n#include "interrupts/interruptPriorities.h"\n'
                          f"int main(void)\n{{\n{body}    return 0;\n}}\n", encoding="utf-8")
        r = self.compile(out, source, "c")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        text = subprocess.run([str(self.tmp / "probe.exe")], capture_output=True, text=True, check=True).stdout
        return [int(line) for line in text.split()]


if __name__ == "__main__":
    unittest.main()
