"""ARCH-52: the generated module binding - a fourth, synthetic driver without a line in the family port.

The synthetic module synthDriver (tools/bswgen/test/binding/modules, known only to this test) declares in its manifest
what is wired (SPI device, a safe_off output, an optional fault input, an analog input), when and where it runs
(lifecycle level and task), its console group per instance and its RTE values, and names its trait
(include/synth/SynthBinding.h). tools/bswgen writes the integration of every instance (integration/moduleBinding.h)
for the synthetic profile testproject_binding (two instances on two SPI buses, one without the optional input).

- The generat: registration in level and task of the manifest, component = console group, parameters typed, the
  optional input only where the board wires it, one safe_off adapter per instance, one RTE reader per instance.
- The host test (test/binding_host/ModuleBindingHostTest.cpp) compiles that generat with the trait, the framework of
  the family integration (ModuleBinding.h) and host binders in place of a family's, and runs it.
- No line of the family port knows the driver; the framework and the family binders name no device driver module.
- A rule only the trait knows stops the build with the trait's message; lifecycle level and task follow the manifest.
- Mutants of the framework and of the generat must fail the host test.

Run: .venv/Scripts/python -m unittest tools/bswgen/test_bswgen_binding.py
"""

import re
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
sys.path.insert(0, str(REPO / "tools"))

import bswgen  # noqa: E402
import modules  # noqa: E402

PROFILES = HERE / "test" / "profiles"
PROJECT = PROFILES / "projects" / "testproject_binding" / "project.yaml"
BOARD = PROFILES / "boards" / "testboard_binding"
SYNTH_MODULES = HERE / "test" / "binding" / "modules"
SYNTH_INCLUDE = SYNTH_MODULES / "synthDriver" / "include"
HOST = HERE / "test" / "binding_host"
INTEGRATION = REPO / "platforms" / "s32k1xx" / "port" / "main" / "src" / "integration"
FRAMEWORK = INTEGRATION / "ModuleBinding.h"
FAMILY_BINDERS = INTEGRATION / "ModuleBinders.h"
OPENBSW = REPO / "external" / "openbsw"
ETL = OPENBSW / "libs" / "3rdparty" / "etl" / "include"
# PLAT-62: the own full-featured test profile (synthetic testboard_full/testproject_full) instead of the eval board
# project, which stays private (PLAT-46 F10) - a test against it would be red in the exported open tree. What
# proves something about the eval board itself uses EVAL_DEMO and is skipped where it is missing.
DEMO = REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full" / "project.yaml"
EVAL_DEMO = REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml"
BINDING = Path("integration/moduleBinding.h")
BINDING_CONFIGURATION = Path("integration/moduleBindingConfiguration.h")

ATTACH_A = "        SYN_A_SAFE_OFF_EN.attach(*SYN_A_SYSTEM, safeOffLock(SYN_A::PWM_BOUND));\n"
# (name, "framework" or "generat", old, new): every `old` must occur exactly once
MUTANTS = [
    ("the adapter does not hand the driver the lock", "framework",
     "        Access::setLock(system, lock);\n", "        (void)lock;\n"),
    ("the adapter forgets the enables at switch-off", "framework",
     "        _enablesAtSwitchOff = Access::enables(*_system);\n", ""),
    ("the adapter does not switch off", "framework", "        Access::off(*_system);\n", ""),
    ("the generat attaches no adapter to SYN_A", "generat", ATTACH_A, ""),
    ("the generat registers at level 2", "generat", "    if (level == 1U)\n", "    if (level == 2U)\n"),
    ("the generat creates SYN_A in the CAN task", "generat",
     "create(SYN_A_SYSTEM, TASK_BSP, SYN_A_PARTS)", "create(SYN_A_SYSTEM, TASK_CAN, SYN_A_PARTS)"),
    ("the generat gives SYN_A the parts of SYN_B", "generat",
     "create(SYN_A_SYSTEM, TASK_BSP, SYN_A_PARTS)", "create(SYN_A_SYSTEM, TASK_BSP, SYN_B_PARTS)"),
    ("the generat reads SYN_A for the RTE of SYN_B", "generat",
     "::integration::binding::SYN_B_SYSTEM.has_value() ? &*::integration::binding::SYN_B_SYSTEM",
     "::integration::binding::SYN_A_SYSTEM.has_value() ? &*::integration::binding::SYN_A_SYSTEM"),
]


def module_dirs(extra: Path = SYNTH_MODULES):
    return (*modules.MODULE_DIRS, extra)


def project_copy(tmp: Path, name: str, **params) -> Path:
    """testproject_binding with other module parameters, in `tmp` (board path absolute)."""
    project = yaml.safe_load(PROJECT.read_text(encoding="utf-8"))
    project["project"]["board"] = BOARD.as_posix()
    for device, values in params.items():
        project["modules"][device].update(values)
    path = tmp / name / "project.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
    return path


class ModuleBindingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="nx_binding_"))
        cls.gen = cls.tmp / "gen"
        bswgen.generate(PROJECT, cls.gen, module_dirs=module_dirs())
        cls.text = (cls.gen / BINDING).read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def compile_and_run(self, name: str, gen: Path | None = None, first_include: Path | None = None):
        includes = ([first_include] if first_include else []) + [
            HOST / "stubs", gen or self.gen, INTEGRATION, SYNTH_INCLUDE, REPO / "libs" / "bsp" / "bspSpi" / "include"]
        exe = self.tmp / f"{name}.exe"
        cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
               "-Wconversion", *[f"-I{i}" for i in includes], f"-isystem{ETL}",
               str(HOST / "ModuleBindingHostTest.cpp"), "-o", str(exe)]
        built = subprocess.run(cmd, capture_output=True, text=True)
        if built.returncode != 0:
            return built, "compile"
        return subprocess.run([str(exe)], capture_output=True, text=True, timeout=60), "run"

    # --- the generat ------------------------------------------------------------------------------------

    def test_generat(self):
        counts = (self.gen / BINDING_CONFIGURATION).read_text(encoding="utf-8")
        for line in ("#define NX_BINDING_INSTANCE_COUNT 2", "#define NX_BINDING_SAFE_OFF_COUNT 2",
                     "#define NX_BINDING_SPI_DEVICE_COUNT 2", "#define NX_BINDING_ANALOG_INPUT_COUNT 2"):
            self.assertIn(line + "\n", counts)
        t = self.text
        self.assertIn('#include "synth/SynthBinding.h"\n', t)
        self.assertIn('#include "integration/synthDriverConfiguration.h"\n', t)
        # level 1 and task bsp of the manifest, component = console group of the instance, board order
        body = t[t.index("inline void addComponents"):]
        self.assertEqual(re.findall(r"if \(level == (\d)U\)", body), ["1"])
        self.assertLess(body.index("SYN_A::COMPONENT"), body.index("SYN_B::COMPONENT"))
        self.assertEqual(body.count("TASK_BSP"), 2)
        self.assertIn("static constexpr char const COMPONENT[] = NX_DEVICE_SYN_A_PARAM_CONSOLE;", t)
        # parameters typed from the manifest: unsigned, signed, string
        self.assertIn("static constexpr uint32_t CYCLE_MS = NX_DEVICE_SYN_A_PARAM_CYCLE_MS;", t)
        self.assertIn("static constexpr int32_t OFFSET_C = NX_DEVICE_SYN_A_PARAM_OFFSET_C;", t)
        self.assertIn("static constexpr char const MODE[] = NX_DEVICE_SYN_A_PARAM_MODE;", t)
        # the optional fault input: wired on SYN_A, open on SYN_B (no value, wired false)
        self.assertIn("static constexpr auto FAULT = NX_BINDER_DIGITAL_INPUT(SYN_A_FAULT);", t)
        syn_b = t[t.index("struct SYN_B"):t.index("SYN_B_SPI{")]
        self.assertNotIn("static constexpr auto FAULT", syn_b)
        self.assertIn("static constexpr bool FAULT_WIRED = false;", syn_b)
        # SPI bus of the board (through the family binder), one safe_off adapter per instance, RTE readers
        self.assertIn("NX_BINDER_SPI_BUS(NX_DEVICE_SYN_B_SPI_INSTANCE, NX_DEVICE_SYN_B_SPI_PCS)", t)
        self.assertIn("#define NX_BINDING_SAFE_OFF_OUTPUT_REFS &::integration::binding::SYN_A_SAFE_OFF_EN, "
                      "&::integration::binding::SYN_B_SAFE_OFF_EN,\n", t)
        self.assertIn("void readSynth_SYN_A(::synth::Reading& reading)", t)
        self.assertIn('"synthDriver SYN_A: configuration invalid (project.yaml modules.SYN_A)"', t)
        # the per-module header tells the optional connection
        header = (self.gen / "integration" / "synthDriverConfiguration.h").read_text(encoding="utf-8")
        self.assertIn("#define NX_DEVICE_SYN_A_IO_FAULT_WIRED 1\n", header)
        self.assertIn("#define NX_DEVICE_SYN_B_IO_FAULT_WIRED 0\n", header)
        self.assertNotIn("NX_DEVICE_SYN_B_IO_FAULT ", header)
        # the lifecycle of every device driver as its manifest declares it (hand-written integrations assert it)
        overview = (self.gen / "integration" / "moduleConfiguration.h").read_text(encoding="utf-8")
        for line in ("#define NX_MODULE_SYNTHDRIVER_LIFECYCLE_LEVEL 1U", "#define NX_MODULE_SYNTHDRIVER_LIFECYCLE_TASK "
                     "TASK_BSP", "#define NX_MODULE_GDTMC6200_LIFECYCLE_LEVEL 1U"):
            self.assertIn(line + "\n", overview)
        # the build learns target and directory of the bound module from the generat, not from a line per driver
        cmake = (self.gen / "integration" / "modules.cmake").read_text(encoding="utf-8")
        self.assertIn("set(NX_BINDING_TARGETS synthDriver)\n", cmake)
        dirs = re.search(r'set\(NX_BINDING_MODULE_DIRS "?([^"\n]*)"?\)', cmake)
        self.assertEqual(Path(dirs.group(1)).resolve(), (SYNTH_MODULES / "synthDriver").resolve())
        cmake_exe = shutil.which("cmake")
        if cmake_exe:
            probe = self.tmp / "probe.cmake"
            probe.write_text(f'include("{(self.gen / "integration" / "modules.cmake").as_posix()}")\n'
                             'list(LENGTH NX_BINDING_MODULE_DIRS n)\n'
                             'if (NOT n EQUAL 1 OR NOT EXISTS "${NX_BINDING_MODULE_DIRS}/module.yaml")\n'
                             '  message(FATAL_ERROR "NX_BINDING_MODULE_DIRS: ${NX_BINDING_MODULE_DIRS}")\nendif ()\n',
                             encoding="utf-8")
            r = subprocess.run([cmake_exe, "-P", str(probe)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_generat_is_family_neutral(self):
        # no family construct in the generat: registers, clocks and drivers come through the binders only
        for word in ("LPSPI", "IP_", "NX_CLOCK_", "bios::", "Lpspi", "Ai_", "nxAdcPhys_", "s32k"):
            self.assertNotIn(word, self.text.split("\n", 1)[1], word)

    def test_demo_binds_the_high_side_switch(self):
        # ARCH-66: the demo binds exactly its high-side switch KL30B_SW through the generated binding; the gate driver
        # MOT1_GD and the SBC stay hand-written in the family port
        gen = self.tmp / "demo"
        bswgen.generate(DEMO, gen)
        counts = (gen / BINDING_CONFIGURATION).read_text(encoding="utf-8")
        self.assertIn("#define NX_BINDING_INSTANCE_COUNT 1\n", counts)
        self.assertIn("#define NX_BINDING_SAFE_OFF_COUNT 1\n", counts)
        self.assertIn("#define NX_BINDING_SPI_DEVICE_COUNT 0\n", counts)
        text = (gen / BINDING).read_text(encoding="utf-8")
        self.assertIn("#define NX_BINDING_SAFE_OFF_OUTPUT_REFS &::integration::binding::KL30B_SW_SAFE_OFF_IN,\n", text)
        self.assertIn('#include "highSideSwitch/HighSideSwitchBinding.h"', text)
        for word in ("MOT1_GD", "gdTmc6200", "gd/", "sbc/"):
            self.assertNotIn(word, text, word)
        cmake = (gen / "integration" / "modules.cmake").read_text(encoding="utf-8")
        self.assertIn("set(NX_BINDING_TARGETS highSideSwitchSystem)\n", cmake)
        self.assertRegex(cmake, r'set\(NX_BINDING_MODULE_DIRS "?[^\n]*/libs/bsw/highSideSwitch"?\)\n')

    def test_the_module_is_known_to_the_test_only(self):
        with self.assertRaises(bswgen.ConfigError) as caught:
            bswgen.generate(PROJECT, self.tmp / "without", module_dirs=modules.MODULE_DIRS)
        self.assertIn("unknown module synthDriver", str(caught.exception))

    # --- no line in the family port ---------------------------------------------------------------------

    def test_family_port_has_no_line_of_the_driver(self):
        pattern = re.compile(r"synthDriver|SynthBinding|synth/|\bSYN_[AB]\b")
        for path in (REPO / "platforms").rglob("*"):
            if path.is_file() and path.suffix in (".h", ".hpp", ".cpp", ".c", ".txt", ".cmake", ".yaml"):
                with self.subTest(path=path.relative_to(REPO).as_posix()):
                    self.assertIsNone(pattern.search(path.read_text(encoding="utf-8", errors="replace")))

    def test_framework_and_binders_name_no_device_driver(self):
        drivers = [n for n, m in modules.load_manifests(module_dirs()).manifests.items() if m.kind == "device_driver"]
        self.assertIn("synthDriver", drivers)
        self.assertIn("gdTmc6200", drivers)
        for path in (FRAMEWORK, FAMILY_BINDERS):
            text = path.read_text(encoding="utf-8")
            for name in drivers:
                with self.subTest(file=path.name, module=name):
                    self.assertNotIn(name, text)
            for include in ("gd/", "highSideSwitch/", "sbc/", "bcc/"):
                self.assertNotIn(f'#include "{include}', text)

    # --- the host test ----------------------------------------------------------------------------------

    def test_host_test(self):
        r, stage = self.compile_and_run("binding")
        self.assertEqual(r.returncode, 0, f"{stage}: {r.stdout[-3000:]}{r.stderr[-3000:]}")
        self.assertIn("0 failures", r.stdout)
        print("\n" + r.stdout.strip(), file=sys.stderr)

    def test_a_rule_of_the_trait_stops_the_build(self):
        # cycle_ms 15 is allowed by the manifest (1..1000) but not by the driver (an even cycle): nxconfig accepts it,
        # the static_assert of the generat stops the build with the device and the YAML path
        gen = self.tmp / "odd"
        bswgen.generate(project_copy(self.tmp, "odd_project", SYN_B={"cycle_ms": 15}), gen, module_dirs=module_dirs())
        r, stage = self.compile_and_run("odd", gen=gen)
        self.assertEqual(stage, "compile")
        self.assertIn("synthDriver SYN_B: configuration invalid (project.yaml modules.SYN_B)", r.stderr)
        self.assertNotIn("SYN_A: configuration invalid", r.stderr)

    def test_level_and_task_follow_the_manifest(self):
        moved = self.tmp / "moved_modules"
        shutil.copytree(SYNTH_MODULES, moved)
        manifest = moved / "synthDriver" / "module.yaml"
        text = manifest.read_text(encoding="utf-8")
        self.assertEqual(text.count("lifecycle: { level: 1, task: bsp }"), 1)
        manifest.write_text(text.replace("lifecycle: { level: 1, task: bsp }", "lifecycle: { level: 2, task: can }"),
                            encoding="utf-8")
        gen = self.tmp / "moved"
        bswgen.generate(PROJECT, gen, module_dirs=module_dirs(moved))
        body = (gen / BINDING).read_text(encoding="utf-8")
        body = body[body.index("inline void addComponents"):]
        self.assertEqual(re.findall(r"if \(level == (\d)U\)", body), ["2"])
        self.assertEqual(body.count("TASK_CAN"), 2)
        self.assertNotIn("TASK_BSP", body)

    def test_mutants_are_detected(self):
        survivors = []
        framework = FRAMEWORK.read_text(encoding="utf-8").replace("\r\n", "\n")
        for index, (name, where, old, new) in enumerate(MUTANTS):
            with self.subTest(mutant=name):
                first = None
                gen = self.gen
                if where == "framework":
                    self.assertEqual(framework.count(old), 1, name)
                    first = self.tmp / f"mutant{index}"
                    first.mkdir(parents=True, exist_ok=True)
                    (first / "ModuleBinding.h").write_text(framework.replace(old, new), encoding="utf-8")
                else:
                    self.assertEqual(self.text.count(old), 1, name)
                    gen = self.tmp / f"mutant_gen{index}"
                    shutil.copytree(self.gen, gen)
                    (gen / BINDING).write_text(self.text.replace(old, new), encoding="utf-8")
                r, stage = self.compile_and_run(f"mutant{index}", gen=gen, first_include=first)
                # every mutant compiles: it is the host test that has to catch it, not the compiler
                self.assertEqual(stage, "run", f"{name}: {r.stderr[-2000:]}")
                if r.returncode == 0:
                    survivors.append(name)
                print(f"\nmutant '{name}': {stage} exit {r.returncode}", file=sys.stderr)
        self.assertEqual(survivors, [], "mutants not detected")


if __name__ == "__main__":
    unittest.main()
