"""Reference output of the safety-relevant board generates (PLAT-78, tool-classification.md proposal B, TF1, SSR-081).

bswgen (with blgen's memory layout, which bswgen writes too) renders each profile; the files of section 4 of
doc/safety/tool-classification.md are compared with the reviewed copies in test/reference/<profile>/:

  safeOutputsConfiguration.h   safe outputs table and safe_off count (SM-23, SM-24)
  safeIoConfiguration.h        register list SafeIo locks and checks (SM-21, SM-22; SYS-24)
  safeSystemConfiguration.h    safe system integration (SM-18 reference band, SM-23 user)
  safeLifecycleConfiguration.h safety task period, watchdog time-out, lateness and the derived watchdog service
                               interval (SM-01, SM-04; SYS-25, PLAT-75 TF1), SYS-53 the LPO limits of mcudb and
                               the service interval at the fastest LPO
  safeSupervisorConfiguration.h reset-loop limit and fault-free time (SM-50, SSR-065; SYS-43), window of the
                               device-bus correlation and its derived length in cycles (SYS-29, SYS-53)
  derivativeFacts.h            MPU region count, NVIC bits, core (SM-12, SM-13)
  interruptPriorities.h        interrupt ranks -> levels
  adcReferenceConfiguration.h  ADC reference band (SM-18)
  boardClock.h                 clock plan (every time in the image, watchdog relation)
  memoryLayout.h, bl_memory.h, bl_memory.ld   memory layout (SM-14, SM-51, both linker scripts)

Profiles: three own test profiles (safe outputs with PWM group and active-low output on S32K144, the same on S32K116
Cortex-M0+, ADC reference band), the open build test S32K148 without SBC with two CAN buses and a bootloader, the
open S32K116 EVB project (smallest memory layout of the family), the full-featured own test profile
testproject_full (PLAT-62: SBC, two CAN buses, gate driver with PWM group, high-side switch, sensors - in every tree,
the open export included) and the eval board demo (private project: skipped when it is missing, e.g. in the open
export).

  ReferenceOutputTest    every profile against its reference, a difference fails with a unified diff
  StabilityTest          a second process with another hash seed, working directory and output directory renders
                         the same reference (no dict order, path or environment in the output)
  TemplateMutantTest     a changed template turns the comparison red (one mutant per checked template), and so
                         does changed generator code for the file that has no template (module parameter header)
  ReferenceToolTest      normalisation, difference report and update on synthetic data
  ReferenceInventoryTest every profile has a reference and every reference a profile; no absolute path inside

Update after a reviewed change of generator, template or profile (then review `git diff` of test/reference):
    .venv/Scripts/python tools/bswgen/test_reference_output.py --update [profile ...]
Run: .venv/Scripts/python -m unittest tools/bswgen/test_reference_output.py
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
sys.path.insert(0, str(HERE / "test"))

import bswgen  # noqa: E402
import reference_output as ref  # noqa: E402

REFERENCE = HERE / "test" / "reference"
OWN = HERE / "test" / "profiles" / "projects"
UPDATE = "tools/bswgen/test_reference_output.py --update"

FILES = (
    "bsp/safeOutputs/safeOutputsConfiguration.h",
    "bsp/safeIo/safeIoConfiguration.h",
    "integration/safeSystemConfiguration.h",
    "integration/safeLifecycleConfiguration.h",
    "integration/safeSupervisorConfiguration.h",
    "mcu/derivativeFacts.h",
    "interrupts/interruptPriorities.h",
    "bsp/adc/adcReferenceConfiguration.h",
    "bsp/clock/boardClock.h",
    "memory/memoryLayout.h",
    "bootloader/bl_memory.h",
    "bootloader/bl_memory.ld",
)


def _bswgen(project: Path):
    return lambda out: bswgen.generate(project, out)


# PLAT-94: the channel tables of the I/O kinds bswgen generates as arrays. They are part of the reference of the
# profile without any I/O channel only: there every one of them is empty, and an empty table is what broke the
# target build (inputConfiguration.h indexed the only row of an empty array).
IO_FILES = (
    "bsp/io/io/ioConfiguration.h",
    "bsp/io/input/inputConfiguration.h",
    "bsp/io/input/inputConfigurationStrings.h",
    "bsp/io/output/outputConfiguration.h",
    "bsp/io/output/outputConfigurationStrings.h",
    "bsp/adc/analogInputConfiguration.h",
    "bsp/adc/analogInputConfigurationStrings.h",
    "bsp/adc/analogInputScaleConfiguration.h",
    "bsp/adc/analogInputSamplingConfiguration.h",
    "bsp/adc/analogSensorConfiguration.h",
    "bsp/io/outputPwm/outputPwmConfiguration.h",
    "bsp/io/outputPwm/outputPwmConfigurationStrings.h",
    "bsp/io/outputPwm/ftmPwmConfiguration.h",
    "bsp/capture/inputCaptureConfiguration.h",
    "bsp/hall/hallDecoderConfiguration.h",
    "bsp/hall/hallSensorConfiguration.h",
    # IO-54: table of the pin-interrupt dispatcher and pin events - empty lists, no include, nothing linked
    "bsp/pinIrq/pinIrqConfiguration.h",
    "bsp/pinEvent/pinEventConfiguration.h",
)


def _profile(name: str, project: Path, comment: str, files: tuple = FILES) -> ref.Profile:
    return ref.Profile(name, _bswgen(project), files, comment, (project,))


PROFILES = [
    _profile("testproject_safe_outputs", OWN / "testproject_safe_outputs" / "project.yaml",
             "own: S32K144, PWM group, active-low and active-high safe_off outputs"),
    _profile("testproject_safe_outputs_k116", OWN / "testproject_safe_outputs_k116" / "project.yaml",
             "own: S32K116 (Cortex-M0+, 2 NVIC bits, other MPU and clock)"),
    _profile("testproject_adc_reference", OWN / "testproject_adc_reference" / "project.yaml",
             "own: board adc_reference_mv"),
    _profile("testproject_no_io", OWN / "testproject_no_io" / "project.yaml",
             "own: no I/O channel at all - every generated channel table is empty (PLAT-94)", FILES + IO_FILES),
    _profile("testproject_pwm_center", OWN / "testproject_pwm_center" / "project.yaml",
             "own: PWM group with up-down counting and trigger on a channel match (IO-61)",
             FILES + ("bsp/io/outputPwm/ftmPwmConfiguration.h",)),
    _profile("buildtest_s32k148_min", REPO / "projects" / "buildtest_s32k148_min" / "project.yaml",
             "open build test: S32K148, no SBC, two CAN buses, bootloader and memory layout"),
    _profile("s32k116evb_minimal", REPO / "projects" / "s32k116evb_minimal" / "project.yaml",
             "open: S32K116 EVB, smallest flash/RAM layout of the family with bootloader"),
    _profile("testproject_full", REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full"
             / "project.yaml",
             "own (PLAT-62): full-featured S32K144 profile in place of the private eval board in the open tree"),
    _profile("evalboard_s32k144_demo", REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml",
             "demo of the eval board (private project)"),
]
BY_NAME = {p.name: p for p in PROFILES}

# (template, old, new, profile, file that must differ): one mutant per template of FILES
MUTANTS = [
    ("safeOutputsConfiguration.h.j2", "X({{ e.name }}, {{ e.address }}, {{ e.value }},",
     "X({{ e.name }}, {{ e.value }}, {{ e.address }},", "testproject_safe_outputs",
     "bsp/safeOutputs/safeOutputsConfiguration.h"),
    ("safeOutputsConfiguration.h.j2", "{{ safe_off_outputs }}", "{{ safe_off_outputs - 1 }}",
     "testproject_full", "bsp/safeOutputs/safeOutputsConfiguration.h"),     # PLAT-62: was the eval board demo
    ("safeIoConfiguration.h.j2", "{{ \"0x%08X\"|format(e.expected) }}UL", "{{ \"0x%08X\"|format(e.mask) }}UL",
     "testproject_safe_outputs_k116", "bsp/safeIo/safeIoConfiguration.h"),
    ("derivativeFacts.h.j2", "#define NX_NVIC_PRIORITY_BITS {{ irq.bits }}",
     "#define NX_NVIC_PRIORITY_BITS {{ irq.bits + 1 }}", "testproject_safe_outputs_k116", "mcu/derivativeFacts.h"),
    ("interruptPriorities.h.j2", "#define {{ e.rank_macro }}  {{ e.rank }}\n#define {{ e.macro }} NX_IRQ_OS_LEVEL",
     "#define {{ e.rank_macro }}  {{ e.rank + 1 }}\n#define {{ e.macro }} NX_IRQ_OS_LEVEL",
     "buildtest_s32k148_min", "interrupts/interruptPriorities.h"),
    ("adcReferenceConfiguration.h.j2", "{{ adc_reference_mv }}U", "{{ adc_reference_mv + 100 }}U",
     "testproject_adc_reference", "bsp/adc/adcReferenceConfiguration.h"),
    ("boardClock.h.j2", "#define NX_CLOCK_BUS_HZ  {{ c.bus_hz }}U", "#define NX_CLOCK_BUS_HZ  {{ c.core_hz }}U",
     "testproject_safe_outputs", "bsp/clock/boardClock.h"),
    ("memoryLayout.h.j2", "#define NX_MEMORY_APP_NOINIT_SIZE {{", "#define NX_MEMORY_APP_NOINIT_SIZE 0 + {{",
     "buildtest_s32k148_min", "memory/memoryLayout.h"),
    ("bl_memory.ld.j2", 'format(m.app_start) }};', 'format(m.app_start + 2048) }};',
     "buildtest_s32k148_min", "bootloader/bl_memory.ld"),
    # a changed comment is a change too: the reference guards every line, not only the values
    ("bl_memory.h.j2", "\n", "\n/* changed */\n", "buildtest_s32k148_min", "bootloader/bl_memory.h"),
    # PLAT-94: the empty tables of the profile without any I/O channel - the row of an empty table must not be
    # indexed (that broke the target build), and the end markers must stay
    ("inputConfiguration.h.j2", "    return sfDigitalInputConfigurations[0];",
     "    return &sfDigitalInputConfigurations[0][0];", "testproject_no_io", "bsp/io/input/inputConfiguration.h"),
    ("outputConfiguration.h.j2", "    return sfOutputConfigurations[0];", "    return &sfOutputConfigurations[0][0];",
     "testproject_no_io", "bsp/io/output/outputConfiguration.h"),
]


def _skip_unless(profile: ref.Profile):
    if not profile.available:
        raise unittest.SkipTest(f"{profile.name}: input missing ({', '.join(map(str, profile.inputs))})")


class ReferenceTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nx_ref_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class ReferenceOutputTest(ReferenceTestBase):
    def test_profiles_match_reference(self):
        for profile in PROFILES:
            with self.subTest(profile=profile.name):
                _skip_unless(profile)
                actual = ref.render(profile, self.tmp)
                diffs = ref.differences(ref.stored(REFERENCE, profile), actual, profile.name)
                if diffs:
                    self.fail(f"generated files of {profile.name} differ from the reviewed reference "
                              f"(tools/bswgen/test/reference/{profile.name}).\nIf the change is intended: "
                              f"{UPDATE} {profile.name}, then review the diff.\n\n" + "\n\n".join(diffs))


class LicenceLineTest(ReferenceTestBase):
    """PLAT-120: every file the generator writes carries the SPDX line of the generator output exception."""

    PROFILE_NAMES = ("buildtest_s32k148_min", "testproject_no_io", "s32k116evb_minimal", "testproject_full")

    def test_every_generated_file_carries_the_line(self):
        for name in self.PROFILE_NAMES:
            with self.subTest(profile=name):
                profile = BY_NAME[name]
                _skip_unless(profile)
                ref.render(profile, self.tmp)
                out = self.tmp / name
                self.assertGreater(len(list(out.rglob("*"))), 20)          # the whole output, not only FILES
                self.assertEqual([], ref.licence_gaps(out))

    def test_without_the_stamp_the_check_finds_every_file(self):
        """Counter-probe: a generator that does not stamp is reported (the check above is not vacuous)."""
        from nxconfig import genlicense
        profile = BY_NAME["buildtest_s32k148_min"]
        with unittest.mock.patch.object(genlicense, "stamp", lambda text, path, style=None: text):
            ref.render(profile, self.tmp)
        out = self.tmp / profile.name
        files = [p for p in out.rglob("*") if p.is_file()]
        self.assertGreater(len(ref.licence_gaps(out)), len(files) // 2)


class StabilityTest(ReferenceTestBase):
    def test_other_process_renders_the_same(self):
        """Another hash seed, working directory, output directory and without NX_* variables: same bytes."""
        env = {k: v for k, v in os.environ.items() if not k.startswith("NX_")}
        env.update(PYTHONHASHSEED="4711", PYTHONUTF8="1")
        dump = self.tmp / "dump"
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--dump", str(dump)],
                                cwd=self.tmp, env=env, capture_output=True, text=True, timeout=600)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for profile in PROFILES:
            if not profile.available:
                continue
            with self.subTest(profile=profile.name):
                diffs = ref.differences(ref.stored(REFERENCE, profile), ref.stored(dump, profile), profile.name)
                self.assertEqual(diffs, [], "\n\n".join(diffs))

    def test_twice_in_one_process_is_equal(self):
        profile = BY_NAME["testproject_safe_outputs"]
        first = ref.render(profile, self.tmp / "a")
        self.assertEqual(first, ref.render(profile, self.tmp / "b"))


class TemplateMutantTest(ReferenceTestBase):
    def test_every_checked_template_has_a_mutant(self):
        templates = {p.name for p in (HERE / "templates").glob("*.j2")}
        rendered_by = {Path(f).name + ".j2" for f in FILES} & templates
        # safeSystemConfiguration.h, safeLifecycleConfiguration.h, safeSupervisorConfiguration.h: modules.py
        self.assertEqual(len(rendered_by), len(FILES) - 3)
        self.assertEqual(rendered_by - {m[0] for m in MUTANTS}, set())

    def test_changed_generator_code_turns_red(self):
        """The module parameter header is written by Python (modules.py), not by a template: mutate the code."""
        original = bswgen.modules.render_parameter_header

        def mutant(*args, **kwargs):
            return original(*args, **kwargs).replace("TOLERANCE_MV 200U", "TOLERANCE_MV 250U")

        profile = BY_NAME["testproject_full"]          # PLAT-62: was the eval board demo (private)
        _skip_unless(profile)
        with unittest.mock.patch.object(bswgen.modules, "render_parameter_header", mutant):
            actual = ref.render(profile, self.tmp)
        diffs = ref.differences(ref.stored(REFERENCE, profile), actual, profile.name)
        self.assertEqual(len(diffs), 1, diffs)
        self.assertIn("+#define NX_MODULE_SAFESYSTEM_PARAM_ADC_REFERENCE_TOLERANCE_MV 250U", diffs[0])

    def test_changed_derivation_turns_red(self):
        """SYS-25/SYS-53: the derived values are part of the reference (own profile, not the demo) - the watchdog
        service interval at the fastest LPO (7, the nominal derivation would give 8), the LPO limits of mcudb and the
        device-bus window in cycles."""
        original = bswgen.modules.render_parameter_header
        profile = BY_NAME["testproject_safe_outputs"]
        for old, new, line in (
                ("SERVICE_CYCLES 7U", "SERVICE_CYCLES 8U",
                 "NX_MODULE_SAFELIFECYCLE_DERIVED_WATCHDOG_SERVICE_CYCLES 8U"),
                ("MAX_PER_MILLE 1086U", "MAX_PER_MILLE 1102U",
                 "NX_MODULE_SAFELIFECYCLE_DERIVED_LPO_MAX_PER_MILLE 1102U"),
                ("MIN_PER_MILLE 882U", "MIN_PER_MILLE 883U", "NX_MODULE_SAFELIFECYCLE_DERIVED_LPO_MIN_PER_MILLE 883U"),
                ("WINDOW_CYCLES 20U", "WINDOW_CYCLES 19U",
                 "NX_MODULE_SAFESUPERVISOR_DERIVED_DEVICE_BUS_WINDOW_CYCLES 19U"),
                ("RESET_LOOP_LIMIT 3U", "RESET_LOOP_LIMIT 4U", "NX_MODULE_SAFESUPERVISOR_PARAM_RESET_LOOP_LIMIT 4U")):
            with self.subTest(new=new):
                def mutant(*args, **kwargs):
                    text = original(*args, **kwargs)
                    return text.replace(old, new)

                with unittest.mock.patch.object(bswgen.modules, "render_parameter_header", mutant):
                    actual = ref.render(profile, self.tmp / new.replace(" ", "_"))
                diffs = ref.differences(ref.stored(REFERENCE, profile), actual, profile.name)
                self.assertEqual(len(diffs), 1, diffs)
                self.assertIn("+#define " + line, diffs[0])

    def test_changed_template_turns_red(self):
        for template, old, new, name, rel in MUTANTS:
            profile = BY_NAME[name]
            with self.subTest(template=template, new=new):
                _skip_unless(profile)
                with ref.mutated_template(template, old, new):
                    actual = ref.render(profile, self.tmp / f"m{MUTANTS.index((template, old, new, name, rel))}")
                diffs = ref.differences(ref.stored(REFERENCE, profile), actual, profile.name)
                self.assertTrue(any(d.startswith(f"--- reference/{name}/{rel}") for d in diffs),
                                f"mutant of {template} not detected in {rel}: {diffs}")

    def test_mutant_of_a_missing_text_is_refused(self):
        profile = BY_NAME["testproject_safe_outputs"]
        with ref.mutated_template("boardClock.h.j2", "no such text", "x"), self.assertRaises(AssertionError):
            ref.render(profile, self.tmp)


class ReferenceToolTest(ReferenceTestBase):
    def test_normalise_removes_machine_parts(self):
        out = self.tmp / "out"
        text = (f'#include "{out.as_posix()}/a.h"\r\n// {REPO}\\tools\r\n'
                "// built 2026-09-22 03:14:15 and 2026-09-22T03:14Z\r\n#define X 2026\r\n")
        self.assertEqual(ref.normalise(text, out),
                         '#include "<out>/a.h"\n// <repo>\\tools\n// built <timestamp> and <timestamp>\n'
                         "#define X 2026\n")

    def test_differences_and_update(self):
        profile = ref.Profile("synthetic", lambda out: None, ("a.h", "b/c.ld"))
        refdir = self.tmp / "reference"
        self.assertEqual(ref.write(refdir, profile, {"a.h": "x\n", "b/c.ld": "y\n"}),
                         ["added    synthetic/a.h", "added    synthetic/b/c.ld"])
        self.assertEqual(ref.differences(ref.stored(refdir, profile), {"a.h": "x\n", "b/c.ld": "y\n"}, "synthetic"),
                         [])
        diffs = ref.differences(ref.stored(refdir, profile), {"a.h": "x\nz\n", "new.h": "n\n"}, "synthetic")
        self.assertEqual(len(diffs), 3, diffs)
        self.assertIn("+z", diffs[0])
        self.assertIn("synthetic/b/c.ld: in the reference, but no longer generated", diffs[1])
        self.assertIn("synthetic/new.h: generated, but not in the reference", diffs[2])
        ref.write(refdir, profile, {"a.h": "x\nz\n"})
        self.assertEqual(ref.stored(refdir, profile), {"a.h": "x\nz\n"})
        self.assertFalse((refdir / "synthetic" / "b").exists())

    def test_long_diff_is_cut(self):
        diffs = ref.differences({"a": "".join(f"{i}\n" for i in range(500))}, {"a": ""}, "p")
        self.assertIn("more diff lines", diffs[0])
        self.assertLessEqual(len(diffs[0].splitlines()), ref.MAX_DIFF_LINES + 1)


class ReferenceInventoryTest(unittest.TestCase):
    def test_every_profile_has_a_reference_and_back(self):
        stored = {p.name for p in REFERENCE.iterdir() if p.is_dir()}
        self.assertEqual(stored - set(BY_NAME), set(), "reference without profile")
        for profile in PROFILES:
            if profile.available:
                self.assertIn(profile.name, stored, f"no reference - run {UPDATE} {profile.name}")

    def test_every_checked_file_is_in_some_reference(self):
        present = {rel for p in PROFILES for rel in ref.stored(REFERENCE, p)}
        self.assertEqual(set(FILES) - present, set())

    def test_no_machine_path_in_the_reference(self):
        pattern = re.compile(r"[A-Za-z]:[\\/]|/home/|/tmp/|<out>|<repo>")
        for path in REFERENCE.rglob("*"):
            if path.is_file():
                with self.subTest(path=path.relative_to(REFERENCE).as_posix()):
                    self.assertIsNone(pattern.search(path.read_text(encoding="utf-8")))


def main(argv: list[str]) -> int:
    work = Path(tempfile.mkdtemp(prefix="nx_ref_"))
    try:
        if argv[:1] == ["--update"]:
            return ref.update_main(argv[1:], PROFILES, REFERENCE, work)
        if argv[:1] == ["--dump"] and len(argv) == 2:
            for profile in PROFILES:
                if profile.available:
                    ref.write(Path(argv[1]), profile, ref.render(profile, work))
            return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)
    unittest.main(argv=[sys.argv[0], *argv])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
