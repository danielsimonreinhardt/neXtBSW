"""Hardware threshold of bswgen and the family s32k1xx (SYS-86): CMP0 -> TRGMUX -> FTM fault input.

bswgen turns the plan of tools/nxconfig/hwthreshold.py into register values: bsp/hwThreshold/hwThresholdConfiguration.h
(comparator, trigger mux, fault source select, with static_asserts against the FTM configuration), the fault part of
bsp/io/outputPwm/ftmPwmConfiguration.h (MODE[FAULTM], COMBINE[FAULTEN], FLTCTRL, FLTPOL) and the register list of
SafeIo (SM-22). Profiles:

  - testproject_hw_threshold (own: S32K144, bridge on FTM3, DC link current above 40 A at CMP0_IN3, 200 ns filter);
  - the demo project and the other checked-in s32k1xx projects: no threshold - NX_HW_THRESHOLD_COUNT 0 and an FTM
    configuration without any SYS-86 part (byte identity with the generator before SYS-86 was shown once against
    integ/night4, doc/changelog.d/SYS-86.md; here the absence is kept).

Host test (zig c++): platforms/s32k1xx/bsp/bspHwThreshold/test/host/HwThresholdHostTest.cpp compiles the real PWM
driver bsp/io/outputPwm/ComplementaryPwm.h with the generat of the own profile and checks the order of the steps,
the registers, the TRGMUX lock and - with a model of the fault path built from the reference manual - that the
generated values switch the bridge off exactly at the DAC level. Mutants in the family code, the PWM driver, the
templates and the generator (constants and rounding of nxconfig) must each make it fail or not compile.

Run: .venv/Scripts/python -m unittest tools/bswgen/test_bswgen_hwthreshold.py (tools/test -k bswgen-hwthreshold)
"""

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
from nxconfig import hwthreshold  # noqa: E402

PROFILE = HERE / "test" / "profiles" / "projects" / "testproject_hw_threshold" / "project.yaml"
PROJECTS = [REPO / "projects" / name / "project.yaml"
            for name in ("evalboard_s32k144_demo", "evalboard_s32k144_charge", "buildtest_s32k148_min",
                         "s32k116evb_minimal")]
MODULE = REPO / "platforms" / "s32k1xx" / "bsp" / "bspHwThreshold"
HOST = MODULE / "test" / "host"
TEST = HOST / "HwThresholdHostTest.cpp"
FAMILY = MODULE / "include" / "hwThreshold" / "HwThreshold.h"
PORT_INCLUDE = REPO / "platforms" / "s32k1xx" / "port" / "bspConfiguration" / "include"
DRIVER = PORT_INCLUDE / "bsp" / "io" / "outputPwm" / "ComplementaryPwm.h"
PWM_STUBS = HERE / "test" / "pwm_host" / "stubs"
# IO-61: ComplementaryPwm.h builds on the group module
PWM_GROUP = REPO / "platforms" / "s32k1xx" / "bsp" / "bspFtmPwmGroup" / "include"
OPENBSW = REPO / "external" / "openbsw"
FLAGS = ["-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror", "-Wconversion", "-Wno-deprecated-volatile"]

# (what, file, old, new): faults in the family code and in the PWM driver
SOURCE_MUTANTS = [
    ("lock before the selection", FAMILY,
     "    trgmux.TRGMUXn[c.trgmuxIndex] = value;\n    // LK",
     "    trgmux.TRGMUXn[c.trgmuxIndex] = value | TRGMUX_TRGMUXn_LK_MASK;\n    // LK"),
    ("no lock", FAMILY, "value | TRGMUX_TRGMUXn_LK_MASK;\n}", "value;\n}"),
    ("other SEL fields cleared", FAMILY, "(trgmux.TRGMUXn[c.trgmuxIndex] & ~c.trgmuxSelMask) | c.trgmuxSel",
     "c.trgmuxSel"),
    ("fault source not switched to TRGMUX", FAMILY, "sim.FTMOPT0 = sim.FTMOPT0 | c.simFaultSelect;", "(void)sim;"),
    ("fault source of other timers overwritten", FAMILY, "sim.FTMOPT0 = sim.FTMOPT0 | c.simFaultSelect;",
     "sim.FTMOPT0 = c.simFaultSelect;"),
    ("comparator switched on before its inputs", FAMILY, "    cmp.C1 = c.cmpC1;\n    cmp.C0 = c.cmpC0;",
     "    cmp.C0 = c.cmpC0;\n    cmp.C1 = c.cmpC1;"),
    ("round robin left as found", FAMILY, "    cmp.C2 = 0U; // no round robin\n", ""),
    ("SEL of the wrong fault input accepted", FAMILY, "uint32_t const shift = 8U * (input + 1U);",
     "uint32_t const shift = 8U * input;"),
    ("minus input from the mux accepted", FAMILY, "&& (innsel == 0U)", ""),
    ("comparator prepared after the FTM configuration", DRIVER,
     "        hwThreshold::prepare(hwThresholdConfig::cmp(), hwThresholdConfig::sim(), hwThresholdConfig::CONFIG);\n"
     "#endif\n        _ftm.init(&ftmPwm::CONFIGURATION);",
     "#endif\n        _ftm.init(&ftmPwm::CONFIGURATION);\n"
     "        hwThreshold::prepare(hwThresholdConfig::cmp(), hwThresholdConfig::sim(), hwThresholdConfig::CONFIG);"),
    ("no settling time", DRIVER, "        sysDelayUs(hwThresholdConfig::CONFIG.settleUs);\n", ""),
    ("routed before the counter runs", DRIVER,
     "        _ftm.start();\n#if NX_HW_THRESHOLD_COUNT > 0\n", "#if NX_HW_THRESHOLD_COUNT > 0\n"),
]
# (what, template, old, new): faults in the templates of the generat
TEMPLATE_MUTANTS = [
    ("fault input polarity low", "ftmPwmConfiguration.h.j2", "format(hw_threshold.ftm_fltpol)",
     "format(hw_threshold.ftm_fltpol + 1)"),
    ("FAULTEN missing in the FTM configuration", "ftmPwmConfiguration.h.j2", "format(hw_threshold.ftm_faulten)",
     "format(0)"),
    ("fault input not enabled", "ftmPwmConfiguration.h.j2", "format(hw_threshold.ftm_fltctrl)",
     "format(hw_threshold.ftm_fltctrl // 16 * 16)"),
    ("FTM of another index routed", "hwThresholdConfiguration.h.j2", "TRGMUX_{{ t.timer }}_INDEX",
     "TRGMUX_FTM2_INDEX"),
]
# (what, patch target, value): faults in the generator and in the plan of nxconfig
GENERATOR_MUTANTS = [
    ("wrong TRGMUX source code", "bswgen.TRGMUX_CMP0_OUT", 0x0D),
    ("plus input from the DAC", "bswgen.CMP_C1_INPSEL_MUX", 0),
    ("DAC switched off", "bswgen.CMP_C1_DACEN", 0),
    ("comparator output inverted for above", "bswgen.CMP_C0_PMODE", 0x1800),  # PMODE | INVT
    ("rounding away from the safe side", "nxconfig.hwthreshold.floor", "ceil"),
    ("FAULTM automatic clearing", "bswgen.FTM_MODE_FAULTM_MANUAL_ALL", 0x60),
]


def zig_available() -> bool:
    try:
        import ziglang  # noqa: F401
        return True
    except ImportError:
        return False


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

    def build_and_run(self, gen: Path, family_include: Path, port_include: Path, name: str):
        includes = [HOST / "stubs", PWM_STUBS, gen, port_include, family_include, PWM_GROUP]
        system = [OPENBSW / "platforms/s32k1xx/bsp/bspFtm/include",
                  OPENBSW / "platforms/s32k1xx/bsp/bspMcu/include/3rdparty/nxp",
                  OPENBSW / "libs/bsw/bsp/include"]
        exe = self.tmp / f"{name}.exe"
        r = subprocess.run([sys.executable, "-m", "ziglang", "c++", *FLAGS, *[f"-I{i}" for i in includes],
                            *[f"-isystem{i}" for i in system], str(TEST), "-o", str(exe)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            return "compile", r.stderr
        r = subprocess.run([str(exe)], capture_output=True, text=True)
        return ("ok" if r.returncode == 0 else "fail"), r.stdout

    def test_generat_of_the_profile(self):
        text = (self.tmp / "gen" / "bsp/hwThreshold/hwThresholdConfiguration.h").read_text(encoding="utf-8")
        self.assertIn("#define NX_HW_THRESHOLD_COUNT 1", text)
        self.assertIn("constexpr uint32_t CMP_C1 = 0x080098D0U;", text)
        self.assertIn("TRGMUX_FTM3_INDEX", text)
        self.assertIn("level 208 of 256 -> 4082.0 mV = 39551 mA", text)
        ftm = (self.tmp / "gen" / "bsp/io/outputPwm/ftmPwmConfiguration.h").read_text(encoding="utf-8")
        self.assertIn("/* FLTCTRL       */ 0x0D11U", ftm)
        self.assertIn("| 0x00404040U /* SYS-86: FAULTEN */", ftm)
        safe_io = (self.tmp / "gen" / "bsp/safeIo/safeIoConfiguration.h").read_text(encoding="utf-8")
        # SM-22 checks the whole path; COMBINE now expects the FAULTEN bits
        for entry in ("X(PWM_TIMER, BRIDGE_COMBINE, IP_FTM3_BASE + offsetof(FTM_Type, COMBINE), 0x77777777UL, "
                      "0x00737373UL",
                      "X(PWM_TIMER, BRIDGE_MODE, IP_FTM3_BASE + offsetof(FTM_Type, MODE), 0x00000060UL, 0x00000040UL",
                      "X(PWM_TIMER, BRIDGE_FLTCTRL, IP_FTM3_BASE + offsetof(FTM_Type, FLTCTRL), 0x00008FFFUL, "
                      "0x00000D11UL",
                      "X(PWM_TIMER, DC_OVERCURRENT_TRGMUX, IP_TRGMUX_BASE + offsetof(TRGMUX_Type, TRGMUXn) + 4UL * "
                      "TRGMUX_FTM3_INDEX, 0x80007F00UL, 0x80000E00UL",
                      "X(PWM_TIMER, DC_OVERCURRENT_SIM_FTMOPT0, IP_SIM_BASE + offsetof(SIM_Type, FTMOPT0), "
                      "0x00001000UL, 0x00001000UL",
                      "X(PWM_TIMER, DC_OVERCURRENT_CMP_C0, IP_CMP0_BASE + offsetof(CMP_Type, C0), 0x58FFDF77UL, "
                      "0x00001100UL",
                      "X(PWM_TIMER, DC_OVERCURRENT_CMP_C1, IP_CMP0_BASE + offsetof(CMP_Type, C1), 0x1BFFFFFFUL, "
                      "0x080098D0UL"):
            self.assertIn(entry, safe_io)

    def test_projects_without_threshold(self):
        for project in PROJECTS:
            if not project.is_file():
                continue
            with self.subTest(project=project.parent.name):
                out = self.tmp / f"gen_{project.parent.name}"
                bswgen.generate(project, out)
                text = (out / "bsp/hwThreshold/hwThresholdConfiguration.h").read_text(encoding="utf-8")
                self.assertIn("#define NX_HW_THRESHOLD_COUNT 0", text)
                self.assertNotIn("#include", text)
                ftm = (out / "bsp/io/outputPwm/ftmPwmConfiguration.h").read_text(encoding="utf-8")
                self.assertNotIn("SYS-86", ftm)
                if "CONFIGURATION" in ftm:
                    self.assertIn("/* FLTCTRL       */ 0U,\n", ftm)
                    self.assertIn("/* FLTPOL        */ 0U,\n", ftm)
                    self.assertIn("/* MODE          */ FTM_MODE_FTMEN_MASK | FTM_MODE_WPDIS_MASK,\n", ftm)
                safe_io = (out / "bsp/safeIo/safeIoConfiguration.h").read_text(encoding="utf-8")
                self.assertNotIn("FLTCTRL", safe_io)
                self.assertNotIn("CMP0", safe_io)

    def test_host_runtime(self):
        verdict, output = self.build_and_run(self.tmp / "gen", MODULE / "include", PORT_INCLUDE, "host")
        print("\n" + output.strip()[-2000:], file=sys.stderr)
        self.assertEqual(verdict, "ok", output[-3000:])
        self.assertRegex(output, r"hardware threshold host test: \d+ checks, 0 failures")

    def test_source_mutants(self):
        for what, file, old, new in SOURCE_MUTANTS:
            with self.subTest(mutant=what):
                text = file.read_text(encoding="utf-8").replace("\r\n", "\n")
                self.assertEqual(text.count(old), 1, f"mutant '{what}' does not apply")
                root = self.tmp / f"mut_{re.sub(r'[^a-z]+', '_', what)}"
                if file == FAMILY:
                    target = root / "family" / "hwThreshold" / "HwThreshold.h"
                    family, port = root / "family", PORT_INCLUDE
                else:
                    target = root / "port" / "bsp" / "io" / "outputPwm" / "ComplementaryPwm.h"
                    family, port = MODULE / "include", root / "port"
                target.parent.mkdir(parents=True)
                target.write_text(text.replace(old, new), encoding="utf-8")
                verdict, output = self.build_and_run(self.tmp / "gen", family, port, root.name)
                self.assertIn(verdict, ("compile", "fail"), f"mutant '{what}' survived:\n{output[-1500:]}")

    def test_template_mutants(self):
        for what, template, old, new in TEMPLATE_MUTANTS:
            with self.subTest(mutant=what):
                root = self.tmp / f"tmut_{re.sub(r'[^a-z]+', '_', what)}"
                templates = root / "templates"
                shutil.copytree(HERE / "templates", templates)
                path = templates / template
                text = path.read_text(encoding="utf-8")
                self.assertEqual(text.count(old), 1, f"mutant '{what}' does not apply")
                path.write_text(text.replace(old, new), encoding="utf-8")
                env = Environment(loader=FileSystemLoader(templates), undefined=StrictUndefined, trim_blocks=True,
                                  lstrip_blocks=True, keep_trailing_newline=True)
                with unittest.mock.patch.object(bswgen, "template_env", lambda: env):
                    bswgen.generate(PROFILE, root / "gen")
                verdict, output = self.build_and_run(root / "gen", MODULE / "include", PORT_INCLUDE, root.name)
                self.assertIn(verdict, ("compile", "fail"), f"mutant '{what}' survived:\n{output[-1500:]}")

    def test_generator_mutants(self):
        for what, target, value in GENERATOR_MUTANTS:
            with self.subTest(mutant=what):
                root = self.tmp / f"gmut_{re.sub(r'[^a-z]+', '_', what)}"
                module, name = target.rsplit(".", 1)
                owner = hwthreshold if module == "nxconfig.hwthreshold" else bswgen
                replacement = getattr(__import__("math"), value) if isinstance(value, str) else value
                with unittest.mock.patch.object(owner, name, replacement):
                    bswgen.generate(PROFILE, root / "gen")
                verdict, output = self.build_and_run(root / "gen", MODULE / "include", PORT_INCLUDE, root.name)
                self.assertIn(verdict, ("compile", "fail"), f"mutant '{what}' survived:\n{output[-1500:]}")


class Environment_(unittest.TestCase):
    def test_template_environment_is_the_generators(self):
        env = bswgen.template_env()
        self.assertTrue(env.trim_blocks and env.lstrip_blocks and env.keep_trailing_newline)


if __name__ == "__main__":
    unittest.main()
