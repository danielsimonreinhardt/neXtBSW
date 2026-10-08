# SPDX-License-Identifier: Apache-2.0
"""PWM group of the family s32k1xx with both countings, trigger output and one-step duty write (IO-61).

tools/bswgen renders the own test profiles (tools/bswgen/test/profiles: testproject_pwm_center, three pairs on FTM1
channels 2..7 at 64 MHz; testproject_safe_outputs_k116, two pairs on FTM0 channels 2/3 and 6/7 at 48 MHz) with the
variants of pwm.<group>.alignment and pwm.<group>.trigger. For every variant test/host/FtmPwmGroupHostTest.cpp is
compiled with zig c++ against the generated ftmPwmConfiguration.h and ftmPwm/FtmPwmGroup.h and run on a register model
of the FlexTimer; what the project asks for (frequency, dead time, alignment, trigger point) is passed as macros, so
the host test compares the simulated waveform with the project, not with the generated constants.

  FtmPwmGroupVariantsTest  every variant passes; edge and center of the same project have the same period and dead
                           time (MOD halved, not the frequency); the trigger lies in the middle of its zero vector
  FtmPwmGroupGuardTest     a configuration only the IO-61 aware driver can drive (center, channel trigger) does not
                           compile without NX_FTM_PWM_IO61; edge with the init trigger needs no new driver
  FtmPwmGroupMutantTest    deliberately broken copies of FtmPwmGroup.h and of the generated header must fail

Run: .venv/Scripts/python -m unittest platforms/s32k1xx/bsp/bspFtmPwmGroup/test/test_ftm_pwm_group.py
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
MODULE = HERE.parent
ROOT = HERE.parents[4]
OPENBSW = ROOT / "external" / "openbsw"
PROFILES = ROOT / "tools" / "bswgen" / "test" / "profiles" / "projects"
sys.path.insert(0, str(ROOT / "tools" / "bswgen"))
sys.path.insert(0, str(ROOT / "tools"))

import bswgen  # noqa: E402

RUN_LIMIT_S = 120
HEADER = "ftmPwm/FtmPwmGroup.h"
GENERATED = "bsp/io/outputPwm/ftmPwmConfiguration.h"
TRIGGER_CODE = {None: 0, "zero_low": 1, "zero_high": 2}

# name: (profile, group, pwm use overrides)
VARIANTS = {
    "center_zero_low": ("testproject_pwm_center", "INVERTER", {}),
    "center_zero_high": ("testproject_pwm_center", "INVERTER", {"trigger": {"point": "zero_high"}}),
    "center_none": ("testproject_pwm_center", "INVERTER", {"trigger": None}),
    "edge_zero_low": ("testproject_pwm_center", "INVERTER", {"alignment": "edge"}),
    "edge_zero_high": ("testproject_pwm_center", "INVERTER", {"alignment": "edge",
                                                              "trigger": {"point": "zero_high"}}),
    "edge_none": ("testproject_pwm_center", "INVERTER", {"alignment": None, "trigger": None}),
    # other derivative (S32K116, Cortex-M0+), other instance, two pairs with a gap, 48 MHz
    "k116_center_zero_low": ("testproject_safe_outputs_k116", "DUAL", {"alignment": "center",
                                                                        "trigger": {"point": "zero_low"}}),
    "k116_edge_zero_high": ("testproject_safe_outputs_k116", "DUAL", {"trigger": {"point": "zero_high"}}),
    # 200 Hz: the up-down counter needs the prescaler (48 MHz / 2 / 200 = 120000 > 16 bit -> PS 1), dead time /4
    "k116_center_prescaler": ("testproject_safe_outputs_k116", "DUAL", {"frequency_hz": 200, "deadtime_ns": 5000,
                                                                         "alignment": "center",
                                                                         "trigger": {"point": "zero_high"}}),
    "k116_edge_prescaler": ("testproject_safe_outputs_k116", "DUAL", {"frequency_hz": 200, "deadtime_ns": 5000,
                                                                       "trigger": {"point": "zero_high"}}),
}


def render(tmp: Path, variant: str) -> tuple[Path, dict]:
    """Project copy of the profile with the variant, rendered by bswgen; returns (generated dir, pwm use)."""
    profile, group, changes = VARIANTS[variant]
    src = PROFILES / profile / "project.yaml"
    project = yaml.safe_load(src.read_text(encoding="utf-8"))
    project["project"]["board"] = str((src.parent / project["project"]["board"]).resolve().as_posix())
    use = project["pwm"][group]
    for key, value in changes.items():
        if value is None:
            use.pop(key, None)
        else:
            use[key] = value
    project["pwm"] = {group: use}
    project.pop("adc_sampling", None)
    path = tmp / variant / "project.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
    out = tmp / variant / "gen"
    bswgen.generate(path, out)
    return out, use


def expectations(use: dict, clock_hz: int) -> list[str]:
    trigger = (use.get("trigger") or {}).get("point")
    return [f"-DEXPECT_CLOCK_HZ={clock_hz}U", f"-DEXPECT_FREQUENCY_HZ={use['frequency_hz']}U",
            f"-DEXPECT_DEADTIME_NS={use['deadtime_ns']}U",
            f"-DEXPECT_CENTER={1 if use.get('alignment') == 'center' else 0}",
            f"-DEXPECT_TRIGGER={TRIGGER_CODE[trigger]}"]


def clock_of(gen: Path) -> int:
    text = (gen / GENERATED).read_text(encoding="utf-8")
    return int(re.search(r"CLOCK_HZ\s+= (\d+)U", text).group(1))


def compile_and_run(tmp: Path, gen: Path, defines: list[str], module_include: Path = MODULE / "include",
                    io61: bool = True, name: str = "host") -> tuple[subprocess.CompletedProcess, str]:
    includes = [HERE / "host" / "stubs", module_include, gen]
    system = [OPENBSW / "platforms/s32k1xx/bsp/bspFtm/include",
              OPENBSW / "platforms/s32k1xx/bsp/bspMcu/include/3rdparty/nxp",
              OPENBSW / "libs/bsw/bsp/include"]
    exe = tmp / f"{name}.exe"
    cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror", "-Wconversion",
           "-Wno-deprecated-volatile", *defines, *(["-DNX_FTM_PWM_IO61=1"] if io61 else []),
           *[f"-I{i}" for i in includes], *[f"-isystem{i}" for i in system],
           str(HERE / "host" / "FtmPwmGroupHostTest.cpp"), "-o", str(exe)]
    built = subprocess.run(cmd, capture_output=True, text=True)
    if built.returncode != 0:
        return built, "compile"
    try:
        ran = subprocess.run([str(exe)], capture_output=True, text=True, timeout=RUN_LIMIT_S)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess([str(exe)], 124, "timeout", ""), "run"
    return ran, "run"


def measured(stdout: str) -> dict:
    values = {}
    for key, value in re.findall(r"(\w+)=(-?[\d.]+)", " ".join(re.findall(r"MEASURED (.*)", stdout))):
        values[key] = float(value)
    return values


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ftmpwm_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_variant(self, variant: str, **kwargs):
        gen, use = render(self.tmp, variant)
        r, stage = compile_and_run(self.tmp, gen, expectations(use, clock_of(gen)), name=variant, **kwargs)
        return r, stage, gen, use


class FtmPwmGroupVariantsTest(_Tmp):
    def test_every_variant(self):
        results = {}
        for variant in VARIANTS:
            with self.subTest(variant=variant):
                r, stage, _, _ = self.run_variant(variant)
                self.assertEqual(r.returncode, 0, f"{stage}: {r.stdout[-3000:]}{r.stderr[-3000:]}")
                print(f"\n{variant}: " + r.stdout.strip().splitlines()[-1], file=sys.stderr)
                results[variant] = measured(r.stdout)
        # same project, other counting: same period and dead time
        for edge, center in (("edge_zero_low", "center_zero_low"), ("edge_none", "center_none"),
                             ("k116_edge_prescaler", "k116_center_prescaler")):
            with self.subTest(pair=(edge, center)):
                self.assertEqual(results[edge]["period_clocks"], results[center]["period_clocks"])
                self.assertEqual(results[edge]["deadtime_clocks"], results[center]["deadtime_clocks"])
        # the trigger sits half a dead time before the middle of its zero vector (the dead time delays the rising
        # edges only), never more than one counter tick (plus the half clock of an odd run) further
        for variant, facts in results.items():
            if "trigger_offset_clocks" in facts:
                offset = facts["trigger_offset_clocks"] + facts["deadtime_clocks"] / 2
                print(f"{variant}: trigger {facts['trigger_offset_clocks']:+.1f} clocks from the middle of the zero "
                      f"vector, {offset:+.1f} after correcting half the dead time", file=sys.stderr)
                self.assertLessEqual(abs(offset), facts["tick_clocks"] + 0.5, variant)

    def test_generated_values_of_the_center_profile(self):
        gen, _ = render(self.tmp, "center_zero_low")
        text = (gen / GENERATED).read_text(encoding="utf-8")
        # 64 MHz / 20 kHz = 3200 ticks per period, turning point 1600; 750 ns = 48 clocks, DTPS /1
        self.assertIn("PERIOD_TICKS       = 3200U;", text)
        self.assertIn("COUNTER_TOP        = 1600U;", text)
        self.assertIn("DEADTIME_TICKS     = 48U;", text)
        self.assertIn("TRIGGER_CHANNEL     = 0U;", text)            # FTM1 channels 2..7 used, 0 is free
        self.assertIn("TRIGGER_VALUE      = 1600U;", text)
        self.assertIn("/* EXTTRIG       */ FTM_EXTTRIG_CH0TRIG_MASK,", text)
        self.assertIn("FTM_SC_CPWMS_MASK", text)
        gen, _ = render(self.tmp, "edge_zero_low")
        text = (gen / GENERATED).read_text(encoding="utf-8")
        self.assertIn("/* EXTTRIG       */ FTM_EXTTRIG_INITTRIGEN_MASK,", text)
        self.assertNotIn("CPWMS", text)
        self.assertNotIn("NX_FTM_PWM_IO61", text)                   # the driver before IO-61 can drive it
        gen, _ = render(self.tmp, "k116_center_prescaler")
        text = (gen / GENERATED).read_text(encoding="utf-8")
        self.assertIn("PRESCALER_SHIFT    = 1U;", text)
        self.assertIn("COUNTER_TOP        = 60000U;", text)
        self.assertIn("/* EXTTRIG       */ FTM_EXTTRIG_INITTRIGEN_MASK,", text)  # zero_high of center: init trigger
        self.assertNotIn("TRIGGER_VALUE", text)


class FtmPwmGroupGuardTest(_Tmp):
    def test_center_and_channel_trigger_need_the_io61_driver(self):
        for variant in ("center_none", "edge_zero_high"):
            with self.subTest(variant=variant):
                r, stage, _, _ = self.run_variant(variant, io61=False)
                self.assertEqual(stage, "compile")
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("NX_FTM_PWM_IO61", r.stderr)

    def test_edge_with_init_trigger_needs_no_new_driver(self):
        r, stage, _, _ = self.run_variant("edge_zero_low", io61=False)
        self.assertEqual(r.returncode, 0, f"{stage}: {r.stdout[-2000:]}{r.stderr[-2000:]}")


# (name, variants, file, old, new): `old` must occur exactly once
MUTANTS = [
    ("center duty over the whole counter range instead of the turning point", ("center_zero_low",), HEADER,
     "return PairValues{static_cast<uint16_t>(((top * q15) + (Q15_ONE / 2U)) / Q15_ONE), 0U};",
     "return PairValues{static_cast<uint16_t>(((top * q15) + (Q15_ONE / 2U)) / (2U * Q15_ONE)), 0U};"),
    ("center 100 % at the turning point (one tick off)", ("center_zero_low",), HEADER,
     "return PairValues{static_cast<uint16_t>(top + 1U), 0U};", "return PairValues{static_cast<uint16_t>(top), 0U};"),
    ("edge pulse not in the period middle", ("edge_zero_high",), HEADER,
     "uint32_t const c0 = (periodTicks - high) / 2U;", "uint32_t const c0 = (periodTicks - high) / 4U;"),
    ("duties written without the software trigger", ("center_zero_low", "edge_none"), HEADER,
     "        _ftm.SYNC = _ftm.SYNC | FTM_SYNC_SWSYNC_MASK;\n        return !pending;", "        return !pending;"),
    ("last pair not written", ("center_zero_low",), HEADER,
     "        for (uint8_t i = 0U; i < PAIR_COUNT; ++i)\n        {\n            PairValues const v",
     "        for (uint8_t i = 0U; (i + 1U) < PAIR_COUNT; ++i)\n        {\n            PairValues const v"),
    ("pending software trigger not reported", ("edge_zero_low",), HEADER,
     "return !pending;", "return !pending || (duties != nullptr);"),
    ("trigger channel at another counter value", ("center_zero_low", "edge_zero_high"), HEADER,
     "_ftm.CONTROLS[TRIGGER_CHANNEL].CnV  = TRIGGER_VALUE;", "_ftm.CONTROLS[TRIGGER_CHANNEL].CnV  = TRIGGER_VALUE / 2U;"),
    ("trigger channel unmasked", ("center_zero_low",), HEADER,
     "_ftm.OUTMASK                        = _ftm.OUTMASK | (1U << TRIGGER_CHANNEL);",
     "_ftm.OUTMASK                        = _ftm.OUTMASK;"),
    # IO-45: the on time read back for the enable rule of a gate driver (highSideOnTicks)
    ("read back: a masked pair reads its channel values", ("center_none", "edge_none"), HEADER,
     "        if (hsMasked)\n        {\n            ticks = 0U;\n            return true;\n        }\n", ""),
    ("read back: the forced inactive state is ignored", ("center_none", "k116_edge_zero_high"), HEADER,
     "        if (hsForced)\n        {", "        if (hsForced && hsForcedOn)\n        {"),
    ("read back: a forced 1 is off", ("edge_none",), HEADER,
     "ticks = (hsForcedOn && !hsMasked) ? PERIOD_TICKS : 0U;", "ticks = 0U;"),
    ("read back: shoot-through pattern accepted", ("center_none",), HEADER,
     "            if (hsForcedOn && lsForcedOn)\n", "            if (hsForcedOn && lsForcedOn && !hsForced)\n"),
    ("read back: inverted pair accepted", ("edge_none",), HEADER,
     "if ((((_ftm.INVCTRL >> (hs / 2U)) & 1U) != 0U) || (hsForced != lsForced)", "if ((hsForced != lsForced)"),
    ("read back: half a pair masked accepted", ("center_none",), HEADER,
     "|| (hsForced != lsForced) || (hsMasked != lsMasked))", "|| (hsForced != lsForced) || ((hsMasked != lsMasked) && lsMasked))"),
    ("read back: center on time one tick long", ("center_zero_low", "k116_center_prescaler"), HEADER,
     "((2U * c0) - 1U));", "(2U * c0));"),
    ("read back: center 100 % at the turning point", ("center_none",), HEADER,
     "((c0 > COUNTER_TOP) ? PERIOD_TICKS", "((c0 >= COUNTER_TOP) ? PERIOD_TICKS"),
    ("read back: edge from the start of the period", ("edge_zero_low", "k116_edge_prescaler"), HEADER,
     "ticks               = (to > from) ? (to - from) : 0U;", "ticks               = (to > from) ? to : 0U;"),
    ("generated: center without CPWMS", ("center_zero_low",), GENERATED,
     "FTM_SC_CLKS(1U) | FTM_SC_CPWMS_MASK | FTM_SC_PS(PRESCALER_SHIFT)", "FTM_SC_CLKS(1U) | FTM_SC_PS(PRESCALER_SHIFT)"),
    ("generated: center with COMBINE", ("center_none",), GENERATED,
     "constexpr uint32_t COMBINE_PAIR = FTM_COMBINE_COMP0_MASK |",
     "constexpr uint32_t COMBINE_PAIR = FTM_COMBINE_COMBINE0_MASK | FTM_COMBINE_COMP0_MASK |"),
    ("generated: init trigger instead of the channel match", ("center_zero_low",), GENERATED,
     "/* EXTTRIG       */ FTM_EXTTRIG_CH0TRIG_MASK,", "/* EXTTRIG       */ FTM_EXTTRIG_INITTRIGEN_MASK,"),
    ("generated: no trigger at all", ("edge_zero_low",), GENERATED,
     "/* EXTTRIG       */ FTM_EXTTRIG_INITTRIGEN_MASK,", "/* EXTTRIG       */ 0U,"),
    ("generated: dead time one step shorter", ("center_none",), GENERATED,
     "DEADTIME_TICKS     = 48U;", "DEADTIME_TICKS     = 47U;"),
]


class FtmPwmGroupMutantTest(_Tmp):
    def test_mutants_are_detected(self):
        for name, variants, file, old, new in MUTANTS:
            for variant in variants:
                with self.subTest(mutant=name, variant=variant):
                    gen, use = render(self.tmp, variant)
                    include = MODULE / "include"
                    if file == HEADER:
                        include = self.tmp / f"mut_{variant}" / "include"
                        shutil.copytree(MODULE / "include", include, dirs_exist_ok=True)
                        target = include / HEADER
                    else:
                        target = gen / GENERATED
                    text = target.read_text(encoding="utf-8")
                    self.assertEqual(text.count(old), 1, f"mutation point of '{name}' not unique in {file}")
                    target.write_text(text.replace(old, new), encoding="utf-8")
                    r, stage = compile_and_run(self.tmp, gen, expectations(use, clock_of(gen)), include,
                                               name=f"mut_{variant}")
                    self.assertNotEqual(r.returncode, 0, f"mutant '{name}' survived on {variant}")
                    self.assertNotIn("error:", r.stderr if stage == "run" else "", "mutant must compile")
                    if stage == "compile":
                        # a static_assert of the generat caught it; that counts as detection only with its text
                        self.assertIn("static assertion failed", r.stderr, r.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
