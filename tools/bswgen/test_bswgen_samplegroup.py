"""Sample groups of bswgen and the family s32k1xx (IO-62): FTM trigger -> TRGMUX -> PDB -> ADC.

bswgen turns the plan of tools/nxconfig/samplegroup.py into register values: bsp/sampleGroup/sampleGroupConfiguration.h
(TRGMUX route, PDB channel 0 with delay and back-to-back chain, ADC slots with the interrupt on the last conversion
of the completing sequencer, SIM_ADCOPT fields, interrupt vector and level; static_asserts through
sampleGroup::isValid). Profiles:

  - testproject_sample_group (own: S32K144, bridge on FTM3 center aligned, trigger zero_low = FTM3_EXT_TRIG; group of
    four channels on ADC0 and ADC1, two after another on each);
  - the demo project and the other checked-in s32k1xx projects: no sample group - NX_SAMPLE_GROUP_COUNT 0 and nothing
    else in the new file. Byte identity of every other generated file with the generator of integ/night5 was shown
    once for all checked-in projects and own profiles (doc/changelog.d/IO-62.md); here the absence is kept.

Host test (zig c++): platforms/s32k1xx/bsp/bspSampleGroup/test/host/SampleGroupChainHostTest.cpp compiles the family
code with the generat of the own profile against register models and a model of the chain built from the reference
manual (which channels convert when, one interrupt at the end, results in project order, errors). Mutants in the
family code, the template and the generator must each make it fail or not compile. Target compile (arm-none-eabi-g++,
Cortex-M4F flags of the target build, -Werror): the generat with the real device header and the family code
instantiated as the board port would do it.

Run: .venv/Scripts/python -m unittest tools/bswgen/test_bswgen_samplegroup.py (tools/test -k bswgen-samplegroup)
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import bswgen  # noqa: E402
from jinja2 import Environment, FileSystemLoader, StrictUndefined  # noqa: E402

PROFILE = HERE / "test" / "profiles" / "projects" / "testproject_sample_group" / "project.yaml"
PROJECTS = [REPO / "projects" / name / "project.yaml"
            for name in ("evalboard_s32k144_demo", "evalboard_s32k144_charge", "buildtest_s32k148_min",
                         "s32k116evb_minimal")]
GENERAT = "bsp/sampleGroup/sampleGroupConfiguration.h"
MODULE = REPO / "platforms" / "s32k1xx" / "bsp" / "bspSampleGroup"
HOST = MODULE / "test" / "host"
TEST = HOST / "SampleGroupChainHostTest.cpp"
FAMILY = MODULE / "include" / "sampleGroup" / "SampleGroupChain.h"
CONTRACT = REPO / "libs" / "bsp" / "bspSampleGroup" / "include"
PRIORITY = [REPO / "platforms/common/interruptPriority/include",
            REPO / "platforms/common/cortex-m/interruptPriority/include"]
OPENBSW = REPO / "external" / "openbsw"
VENDOR = OPENBSW / "platforms/s32k1xx/bsp/bspMcu/include/3rdparty/nxp"
FLAGS = ["-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror", "-Wconversion", "-Wno-deprecated-volatile"]
GCC_PATH = os.pathsep.join(p for p in (os.environ.get("NX_ARM_GCC_BIN"), os.environ.get("PATH")) if p)
ARM_GXX = shutil.which("arm-none-eabi-g++", path=GCC_PATH)
# flags of the target build (cmake/toolchains/NxArmNoneEabi-gcc.cmake, S32K144 = Cortex-M4F) plus -Werror
TARGET_FLAGS = ["-mcpu=cortex-m4", "-mthumb", "-mfloat-abi=hard", "-mfpu=fpv4-sp-d16", "-std=c++17", "-Os", "-Wall",
                "-Wextra", "-Werror", "-Wno-psabi", "-fno-exceptions", "-fno-rtti", "-fno-threadsafe-statics",
                "-fshort-enums", "-mno-unaligned-access", "-ffunction-sections", "-fdata-sections"]

# (what, old, new): faults in the family code
SOURCE_MUTANTS = [
    ("routed before the PDBs are configured",
     "        _book.armed();\n        for (uint8_t s = 0U; s < SEQUENCERS; ++s)\n        {\n"
     "            route(_cfg.sequencers[s], true);\n        }\n",
     "        _book.armed();\n"),
    ("no LDOK", "            pdb.SC                   = q.pdbSc | PDB_SC_LDOK_MASK; // buffered",
     "            pdb.SC                   = q.pdbSc; // buffered"),
    ("PDB switched off before the route is removed",
     "        for (uint8_t s = 0U; s < SEQUENCERS; ++s)\n        {\n            route(_cfg.sequencers[s], false);\n"
     "        }\n        for (uint8_t s = 0U; s < SEQUENCERS; ++s)\n        {\n"
     "            _reg.pdb[_cfg.sequencers[s].converter]->SC = 0U;\n        }\n",
     "        for (uint8_t s = 0U; s < SEQUENCERS; ++s)\n        {\n"
     "            _reg.pdb[_cfg.sequencers[s].converter]->SC = 0U;\n        }\n"
     "        for (uint8_t s = 0U; s < SEQUENCERS; ++s)\n        {\n            route(_cfg.sequencers[s], false);\n"
     "        }\n"),
    ("SIM_ADCOPT left as found", "_reg.sim->ADCOPT = _reg.sim->ADCOPT & ~mask;", "(void)mask;"),
    ("software trigger kept", "        adc.SC2 = ADC_SC2_ADTRG_MASK; // hardware trigger", "        adc.SC2 = 0U; // hardware trigger"),
    ("other SEL fields cleared", "(reg & ~q.trgmuxSelMask) | (on ? q.trgmuxSel : 0U)", "(on ? q.trgmuxSel : 0U)"),
    ("results in slot order instead of project order", "->R[m.slot] & 0xFFFFU", "->R[i % 2U] & 0xFFFFU"),
    ("sequence error not cleared", "            pdb.CH[0].S = 0U; // ERR and CF", "            // ERR and CF"),
    ("no restart after a sequence error", "            restartSequencers();\n", ""),
    ("missing conversion not checked", "            if ((s != _cfg.completing) && !lastConverted(*_reg.adc[q.converter], q))",
     "            if (false)"),
    ("unbounded wait for another converter", "            if (++n >= _cfg.missingPollLimit)",
     "            if (++n == 0U)"),
    ("PWM counter not reported", "::bsp::sampleGroup::Result const result{_raw, MEMBERS, errors, sequence, ticks};",
     "::bsp::sampleGroup::Result const result{_raw, MEMBERS, errors, sequence, 0U};"),
    ("first pre-trigger back-to-back accepted by the check", "PDB_C1_BB(used & ~1UL)", "PDB_C1_BB(used)"),
]
# (what, old, new): faults in the template of the generat
TEMPLATE_MUTANTS = [
    ("TRGMUX input of another timer", "/* trgmuxSel     */ {{ \"0x%02X\"|format(q.trgmux_sel) }}U,",
     "/* trgmuxSel     */ {{ \"0x%02X\"|format(q.trgmux_sel - 2) }}U,"),
    ("delay of another clock", "/* pdbDly0       */ {{ q.pdb_dly0 }}U,", "/* pdbDly0       */ {{ q.pdb_dly0 // 2 }}U,"),
    ("interrupt of the other ADC", "constexpr IRQn_Type IRQ     = {{ g.irq }};",
     "constexpr IRQn_Type IRQ     = ADC1_IRQn;"),
    ("members swapped", "{ {{ m.sequencer }}U, {{ m.slot }}U }", "{ {{ m.sequencer }}U, {{ 1 - m.slot }}U }"),
]
# (what, patch target, value): faults in the generator
GENERATOR_MUTANTS = [
    ("TRGMUX register of the other PDB", "TRGMUX_PDB_INDEX", {"PDB0": 15, "PDB1": 14}),
    ("ADCH of the next channel", "adc_sc1_channel", lambda channel: int(channel[2:]) + 1),
    ("only the trigger select cleared in SIM_ADCOPT", "SIM_ADCOPT_ADC_FIELDS", 0x01),
    ("interrupt on every conversion", "ADC_SC1_AIEN", 0),
]


def zig_available() -> bool:
    try:
        import ziglang  # noqa: F401
        return True
    except ImportError:
        return False


class Generat(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        bswgen.generate(PROFILE, cls.tmp / "gen")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_generat_of_the_profile(self):
        text = (self.tmp / "gen" / GENERAT).read_text(encoding="utf-8")
        self.assertIn("#define NX_SAMPLE_GROUP_COUNT 1", text)
        self.assertIn("#define NX_SAMPLE_GROUPS(X) X(MOT_SAMPLE)", text)
        # FTM3 center aligned, zero_low = channel match -> FTM3_EXT_TRIG 0x1D ([RM13] Table 19-1)
        self.assertEqual(text.count("/* trgmuxSel     */ 0x1DU,"), 2)
        self.assertIn("/* trgmuxIndex   */ TRGMUX_PDB0_INDEX,", text)
        self.assertIn("/* trgmuxIndex   */ TRGMUX_PDB1_INDEX,", text)
        # two pre-triggers: EN 0x03, TOS 0x03, BB 0x02; 500 ns at 80 MHz = 40 clocks
        self.assertEqual(text.count("/* pdbCh0C1      */ 0x00020303U,"), 2)
        self.assertEqual(text.count("/* pdbDly0       */ 40U,"), 2)
        # ADC0: SE4, SE10 + AIEN (the completing sequencer); ADC1: SE2, SE3
        self.assertIn("/* sc1           */ { 0x04U, 0x4AU },", text)
        self.assertIn("/* sc1           */ { 0x02U, 0x03U },", text)
        self.assertIn("/* members    */ { { 0U, 0U }, { 1U, 0U }, { 1U, 1U }, { 0U, 1U } },", text)
        self.assertIn("constexpr IRQn_Type IRQ     = ADC0_IRQn;", text)
        self.assertIn("#define NX_SAMPLE_GROUP_MOT_SAMPLE_IRQ_HANDLER ADC0_IRQHandler", text)
        self.assertIn("first sample 525 ns after the trigger point, group complete after 3968 ns", text)
        self.assertIn("are on the same ADC ADC0", text)       # the warning of nxconfig travels with the generat
        prio = (self.tmp / "gen" / "interrupts/interruptPriorities.h").read_text(encoding="utf-8")
        self.assertIn("#define NX_IRQ_RANK_SAMPLE_GROUP_MOT_SAMPLE  0", prio)

    def test_sc1_channel_codes(self):
        self.assertEqual(bswgen.adc_sc1_channel("SE0"), 0)
        self.assertEqual(bswgen.adc_sc1_channel("SE15"), 15)
        self.assertEqual(bswgen.adc_sc1_channel("SE16"), 32)   # S32K146/148: SE16.. = ADCH 32.. (adcPhysicalName.h)
        self.assertEqual(bswgen.adc_sc1_channel("SE31"), 47)
        with self.assertRaises(bswgen.ConfigError):
            bswgen.adc_sc1_channel("BANDGAP")

    def test_projects_without_sample_group(self):
        for project in PROJECTS:
            if not project.is_file():
                continue
            with self.subTest(project=project.parent.name):
                out = self.tmp / f"gen_{project.parent.name}"
                bswgen.generate(project, out)
                text = (out / GENERAT).read_text(encoding="utf-8")
                self.assertIn("#define NX_SAMPLE_GROUP_COUNT 0", text)
                self.assertIn("#define NX_SAMPLE_GROUPS(X)\n", text)
                self.assertNotIn("#include", text)
                prio = (out / "interrupts/interruptPriorities.h").read_text(encoding="utf-8")
                self.assertNotIn("SAMPLE_GROUP", prio)

    @unittest.skipUnless(ARM_GXX, "arm-none-eabi-g++ (NX_ARM_GCC_BIN) not found")
    @unittest.skipUnless((OPENBSW / "platforms").is_dir(), "external/openbsw missing (git submodule update)")
    def test_target_compile(self):
        """The generat and the family code as the board port would use them, with the target compiler and -Werror."""
        probe = self.tmp / "probe.cpp"
        probe.write_text(
            '#include "bsp/sampleGroup/sampleGroupConfiguration.h"\n'
            "namespace cfg = bios::sampleGroupConfig::MOT_SAMPLE;\n"
            "namespace {\n"
            "bios::sampleGroup::SampleGroupChain<cfg::SEQUENCER_COUNT, cfg::MEMBER_COUNT> chain(cfg::CONFIG, "
            "cfg::registers());\n"
            "}\n"
            'extern "C" void NX_SAMPLE_GROUP_MOT_SAMPLE_IRQ_HANDLER() { chain.onComplete(); }\n'
            "bool nxProbeStart() { NVIC_SetPriority(cfg::IRQ, cfg::IRQ_LEVEL); return chain.init() && chain.arm(); }\n"
            "void nxProbeStop() { chain.disarm(); }\n", encoding="utf-8")
        includes = [self.tmp / "gen", MODULE / "include", CONTRACT, *PRIORITY,
                    OPENBSW / "platforms/s32k1xx/bsp/bspMcu/include", OPENBSW / "libs/3rdparty/cmsis"]
        obj = self.tmp / "probe.o"
        r = subprocess.run([ARM_GXX, *TARGET_FLAGS, *[f"-I{i}" for i in includes], "-c", str(probe), "-o", str(obj)],
                           capture_output=True, text=True, env={**os.environ, "PATH": GCC_PATH})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr.strip(), "", "warnings in the target compile")
        size = shutil.which("arm-none-eabi-size", path=GCC_PATH)
        if size:
            out = subprocess.run([size, str(obj)], capture_output=True, text=True).stdout
            print(f"\ntarget compile of the probe (arm-none-eabi-size):\n{out}", file=sys.stderr)


@unittest.skipUnless(zig_available(), "ziglang missing (requires: ziglang)")
@unittest.skipUnless((OPENBSW / "platforms").is_dir(), "external/openbsw missing (git submodule update)")
class HostTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        bswgen.generate(PROFILE, cls.tmp / "gen")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def build_and_run(self, gen: Path, family_include: Path, name: str):
        includes = [HOST / "stubs", gen, family_include, CONTRACT, *PRIORITY]
        exe = self.tmp / f"{name}.exe"
        r = subprocess.run([sys.executable, "-m", "ziglang", "c++", *FLAGS, *[f"-I{i}" for i in includes],
                            f"-isystem{VENDOR}", str(TEST), "-o", str(exe)], capture_output=True, text=True)
        if r.returncode != 0:
            return "compile", r.stderr
        r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=120)
        return ("ok" if r.returncode == 0 else "fail"), r.stdout

    def test_host_runtime(self):
        verdict, output = self.build_and_run(self.tmp / "gen", MODULE / "include", "host")
        print("\n" + output.strip()[-2000:], file=sys.stderr)
        self.assertEqual(verdict, "ok", output[-3000:])
        self.assertRegex(output, r"sample group chain host test: \d+ checks, 0 failures")

    def test_source_mutants(self):
        text = FAMILY.read_text(encoding="utf-8").replace("\r\n", "\n")
        for what, old, new in SOURCE_MUTANTS:
            with self.subTest(mutant=what):
                self.assertEqual(text.count(old), 1, f"mutant '{what}' does not apply")
                root = self.tmp / f"mut_{re.sub(r'[^a-z]+', '_', what)}"
                target = root / "family" / "sampleGroup" / "SampleGroupChain.h"
                target.parent.mkdir(parents=True)
                target.write_text(text.replace(old, new), encoding="utf-8")
                try:
                    verdict, output = self.build_and_run(self.tmp / "gen", root / "family", root.name)
                except subprocess.TimeoutExpired:
                    verdict, output = "fail", "timeout (endless loop)"
                self.assertIn(verdict, ("compile", "fail"), f"mutant '{what}' survived:\n{output[-1500:]}")

    def test_template_mutants(self):
        for what, old, new in TEMPLATE_MUTANTS:
            with self.subTest(mutant=what):
                root = self.tmp / f"tmut_{re.sub(r'[^a-z]+', '_', what)}"
                templates = root / "templates"
                shutil.copytree(HERE / "templates", templates)
                path = templates / "sampleGroupConfiguration.h.j2"
                text = path.read_text(encoding="utf-8")
                self.assertEqual(text.count(old), 1, f"mutant '{what}' does not apply")
                path.write_text(text.replace(old, new), encoding="utf-8")
                env = Environment(loader=FileSystemLoader(templates), undefined=StrictUndefined, trim_blocks=True,
                                  lstrip_blocks=True, keep_trailing_newline=True)
                with unittest.mock.patch.object(bswgen, "template_env", lambda: env):
                    bswgen.generate(PROFILE, root / "gen")
                verdict, output = self.build_and_run(root / "gen", MODULE / "include", root.name)
                self.assertIn(verdict, ("compile", "fail"), f"mutant '{what}' survived:\n{output[-1500:]}")

    def test_generator_mutants(self):
        for what, name, value in GENERATOR_MUTANTS:
            with self.subTest(mutant=what):
                root = self.tmp / f"gmut_{re.sub(r'[^a-z]+', '_', what)}"
                with unittest.mock.patch.object(bswgen, name, value):
                    try:
                        bswgen.generate(PROFILE, root / "gen")
                    except bswgen.ConfigError as e:
                        continue                     # refused by the generator itself: killed
                verdict, output = self.build_and_run(root / "gen", MODULE / "include", root.name)
                self.assertIn(verdict, ("compile", "fail"), f"mutant '{what}' survived:\n{output[-1500:]}")


if __name__ == "__main__":
    unittest.main()
