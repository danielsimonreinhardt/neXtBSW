# SPDX-License-Identifier: Apache-2.0
"""Clock start-up check (PLAT-72): contract copies and meaningfulness of the host tests.

1. **Contract**: clock/nxClockCheck.h exists once per family (platform rule P2: platforms/<family> does not depend on
   platforms/common) and must be the same file everywhere, because the reporter of platforms/common/clockCheck reads
   it. Compared byte for byte after normalising line ends.
2. **Mutants** (slow, a few minutes): deliberately broken variants of the stm32 clock driver, of both family checks
   and of the reporter must make the host tests of tools/test/tests.yaml fail (clock-check-stm32-nucleo,
   clock-check-s32k-evalboard). Each mutation is applied to a copy of the repository files it touches, the host
   test is compiled against the copy with zig c++ and run. A mutant counts as detected if the test exits non-zero or
   hangs; a mutant that does not compile is a fault of this script.

Run: .venv/Scripts/python -m unittest platforms/common/clockCheck/test/test_clock_check.py
     (NX_CLOCK_CHECK_MUTANTS=0 skips part 2)
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
OPENBSW = ROOT / "external/openbsw"
RUN_LIMIT_S = 60

CONTRACT_COPIES = [
    "platforms/stm32/bsp/bspClock/include/clock/nxClockCheck.h",
    "platforms/s32k1xx/bsp/bspClock/include/clock/nxClockCheck.h",
]

STM32 = "platforms/stm32/bsp/bspClock"
S32K = "platforms/s32k1xx/bsp/bspClock"
REPORT = "platforms/common/clockCheck/src/nxClockCheckReport.cpp"
STM32_CONFIG = f"{STM32}/src/clockConfig_stm32.cpp"
STM32_CHECK = f"{STM32}/src/clockCheck_stm32.cpp"
S32K_CHECK = f"{S32K}/src/ClockCheck.cpp"

# The two host tests (same compiler line as their entries in tools/test/tests.yaml)
BUILDS = {
    "stm32": {
        "include": [f"{STM32}/test/host/profiles/nucleo", f"{STM32}/test/host/model", f"{STM32}/include",
                    "platforms/common/clockCheck/include", "platforms/stm32/bsp/bspMcu/include/3rdparty/st/stm32f4",
                    "external/openbsw/platforms/stm32/bsp/bspClock/include"],
        "system_include": [],
        "sources": [STM32_CONFIG, STM32_CHECK, REPORT, f"{STM32}/test/host/ClockCheckStm32HostTest.cpp"],
    },
    "s32k": {
        "include": [f"{S32K}/test/host/profiles/evalboard", f"{S32K}/test/host/model",
                    f"{S32K}/include", "platforms/common/clockCheck/include"],
        "system_include": ["external/openbsw/platforms/s32k1xx/bsp/bspMcu/include/3rdparty/nxp"],
        "sources": [S32K_CHECK, REPORT, f"{S32K}/test/host/ClockCheckS32kHostTest.cpp"],
    },
}

# name: (build, [(file, old, new, occurrences), ...]); `old` must occur exactly `occurrences` times
MUTATIONS = {
    "stm32 driver: no time-out, waits for ever (OpenBSW s32k1xx behaviour)": ("stm32", [
        (STM32_CONFIG, "for (uint32_t timeout = CLOCK_TIMEOUT; timeout > 0U; --timeout)\n    {\n        if (((reg",
         "for (uint32_t timeout = CLOCK_TIMEOUT; true; --timeout)\n    {\n        if (((reg", 1)]),
    "stm32 driver: switches to the PLL although HSE is missing": ("stm32", [
        (STM32_CONFIG, "        return; // no oscillator: stay on the reset clock", "        // no oscillator", 1)]),
    "stm32 check: HSE ready flag ignored": ("stm32", [
        (STM32_CHECK, "if ((cr & RCC_CR_HSERDY) == 0U)", "if (false)", 1)]),
    "stm32 check: PLL lock ignored": ("stm32", [
        (STM32_CHECK, "if ((cr & RCC_CR_PLLRDY) == 0U)", "if (false)", 1)]),
    "stm32 check: requested source (SW) instead of switch status (SWS)": ("stm32", [
        (STM32_CHECK, "switch (cfgr & RCC_CFGR_SWS)\n    {\n        case RCC_CFGR_SWS_HSI: return NX_CLOCK_HSI_HZ;",
         "switch ((cfgr & RCC_CFGR_SW) << 2U)\n    {\n        case RCC_CFGR_SWS_HSI: return NX_CLOCK_HSI_HZ;", 1)]),
    "stm32 check: switch status not compared": ("stm32", [
        (STM32_CHECK, "| RCC_CFGR_SW_Msk | RCC_CFGR_SWS_Msk;", "| RCC_CFGR_SW_Msk;", 1),
        (STM32_CHECK, "| NX_STM32_CFGR_SW | NX_STM32_CFGR_SWS;", "| NX_STM32_CFGR_SW;", 1)]),
    "stm32 check: APB dividers not compared": ("stm32", [
        (STM32_CHECK, "RCC_CFGR_HPRE_Msk | RCC_CFGR_PPRE1_Msk | RCC_CFGR_PPRE2_Msk | RCC_CFGR_SW_Msk",
         "RCC_CFGR_HPRE_Msk | RCC_CFGR_SW_Msk", 1)]),
    "stm32 check: PLL factors not compared": ("stm32", [
        (STM32_CHECK, "if ((pllcfgr & PLLCFGR_CHECKED) != (NX_STM32_PLLCFGR & PLLCFGR_CHECKED))", "if (false)", 1)]),
    "stm32 check: AHB divider 32 exists (no gap)": ("stm32", [
        (STM32_CHECK, "return 1UL << ((shift < 5U) ? shift : (shift + 1U));", "return 1UL << shift;", 1)]),
    "stm32 check: PLL P divider taken as code": ("stm32", [
        (STM32_CHECK, "* 2U) + 2U;", "* 1U) + 1U;", 1)]),
    "stm32 check: planned instead of actual clock": ("stm32", [
        (STM32_CHECK, "result->actualCoreHz  = systemClockHz(cfgr, pllcfgr) / ahbDivider(cfgr);",
         "result->actualCoreHz  = NX_CLOCK_HCLK_HZ;", 1)]),
    "stm32 check: SystemCoreClock not corrected": ("stm32", [
        (STM32_CHECK, "        SystemCoreClock = result->actualCoreHz;\n", "", 1)]),
    "stm32 check: clock security flag ignored": ("stm32", [
        (STM32_CHECK, "((RCC->CIR & RCC_CIR_CSSF_Msk) != 0U)", "false", 1)]),
    "s32k check: oscillator valid flag ignored": ("s32k", [
        (S32K_CHECK, "if ((sosccsr & SCG_SOSCCSR_SOSCVLD_MASK) == 0U)", "if (false)", 1)]),
    "s32k check: PLL valid flag ignored": ("s32k", [
        (S32K_CHECK, "if ((spllcsr & SCG_SPLLCSR_SPLLVLD_MASK) == 0U)", "if (false)", 1)]),
    "s32k check: only the source compared, not the dividers": ("s32k", [
        (S32K_CHECK, "= SCG_CSR_SCS_MASK | SCG_CSR_DIVCORE_MASK | SCG_CSR_DIVBUS_MASK | SCG_CSR_DIVSLOW_MASK;",
         "= SCG_CSR_SCS_MASK;", 1)]),
    "s32k check: DIVSLOW not compared": ("s32k", [
        (S32K_CHECK, " | SCG_CSR_DIVSLOW_MASK;", ";", 1)]),
    "s32k check: DIVCORE ignored in the actual clock": ("s32k", [
        (S32K_CHECK, "SCG_CSR_DIVCORE_SHIFT) + 1U;", "SCG_CSR_DIVCORE_SHIFT) * 0U + 1U;", 1)]),
    "s32k check: SPLL frequency for FIRC": ("s32k", [
        (S32K_CHECK, "case SCS_FIRC: return NX_CLOCK_FIRC_HZ;", "case SCS_FIRC: return NX_CLOCK_SPLL_HZ;", 1)]),
    "s32k check: loss of clock reset not reported": ("s32k", [
        (S32K_CHECK, "uint32_t resetMask = RCM_SRS_LOC_MASK;", "uint32_t resetMask = 0U;", 1)]),
    "s32k check: loss of lock reset not reported": ("s32k", [
        (S32K_CHECK, "    resetMask |= RCM_SRS_LOL_MASK;\n", "", 1)]),
    "s32k check: SOSC error flag not reported": ("s32k", [
        (S32K_CHECK, " || ((sosccsr & SCG_SOSCCSR_SOSCERR_MASK) != 0U);", ";", 1)]),
    "s32k check: SPLL error flag not reported": ("s32k", [
        (S32K_CHECK, "event = event || ((spllcsr & SCG_SPLLCSR_SPLLERR_MASK) != 0U);", "", 1)]),
    "report: monitor event of a correct clock returns 0": ("s32k", [
        (REPORT, "|| (check.monitor != NX_CLOCK_CHECK_MONITOR_NONE)) ? 1 : 0;", ") ? 1 : 0;", 1)]),
    "report: result not copied": ("s32k", [
        (REPORT, "        *result = check;\n", "", 1)]),
    "stm32 check: source of HSE reported as HSI": ("stm32", [
        (STM32_CHECK, "case RCC_CFGR_SWS_HSE: return NX_CLOCK_SOURCE_EXTERNAL;",
         "case RCC_CFGR_SWS_HSE: return NX_CLOCK_SOURCE_INTERNAL_FAST;", 1)]),
    "stm32 check: PLL reference taken from the wrong bit": ("stm32", [
        (STM32_CHECK, "return ((pllcfgr & RCC_PLLCFGR_PLLSRC_Msk) != 0U) ? NX_CLOCK_SOURCE_EXTERNAL",
         "return ((pllcfgr & RCC_PLLCFGR_PLLSRC_Msk) == 0U) ? NX_CLOCK_SOURCE_EXTERNAL", 1)]),
    "stm32 check: source not derived from the switch status": ("stm32", [
        (STM32_CHECK, "result->source        = sourceOf(cfgr);",
         "result->source        = NX_CLOCK_SOURCE_PLL;", 1)]),
    "s32k check: FIRC reported as SPLL": ("s32k", [
        (S32K_CHECK, "case SCS_FIRC: return NX_CLOCK_SOURCE_INTERNAL_FAST;",
         "case SCS_FIRC: return NX_CLOCK_SOURCE_PLL;", 1)]),
    "s32k check: source of SOSC reported as internal": ("s32k", [
        (S32K_CHECK, "case SCS_SOSC: return NX_CLOCK_SOURCE_EXTERNAL;",
         "case SCS_SOSC: return NX_CLOCK_SOURCE_INTERNAL_SLOW;", 1)]),
    "s32k check: source taken from the plan, not from SCG_CSR": ("s32k", [
        (S32K_CHECK, "result->source        = sourceOf((csr & SCG_CSR_SCS_MASK) >> SCG_CSR_SCS_SHIFT);",
         "result->source        = sourceOf(static_cast<uint32_t>(_SCG_RCCR_) >> SCG_CSR_SCS_SHIFT & 0xFU);", 1)]),
    "report: source not in the line": ("s32k", [
        (REPORT, '"clock: %s, core %lu Hz, source %s%s",', '"clock: %s, core %lu Hz%.0s%s",', 1)]),
    "report: fallback to the internal oscillator named as the PLL": ("stm32", [
        (REPORT, 'case NX_CLOCK_SOURCE_INTERNAL_FAST: return "internal fast RC";',
         'case NX_CLOCK_SOURCE_INTERNAL_FAST: return "PLL on external oscillator";', 1)]),
    "report: monitor event not in the line": ("s32k", [
        (REPORT, '? ", clock monitor event" : "";', '? "" : "";', 1)]),
    "report: planned and actual clock swapped": ("stm32", [
        (REPORT, "        static_cast<unsigned long>(result->actualCoreHz),\n"
                 "        static_cast<unsigned long>(result->plannedCoreHz),",
         "        static_cast<unsigned long>(result->plannedCoreHz),\n"
         "        static_cast<unsigned long>(result->actualCoreHz),", 1)]),
}


# TOOL-244: the family stm32 is private and missing in the exported open tree; the contract copies, the build and the
# mutants of stm32 only exist where the family does (the development repository has all of them)
if not (ROOT / STM32).is_dir():
    CONTRACT_COPIES = [c for c in CONTRACT_COPIES if (ROOT / c).is_file()]
    del BUILDS["stm32"]
    MUTATIONS = {name: value for name, value in MUTATIONS.items() if value[0] in BUILDS}


def normalised(path: Path) -> bytes:
    return path.read_bytes().replace(b"\r\n", b"\n")


def compile_and_run(root: Path, build: str, workdir: Path, name: str, strict: bool = True):
    """Returns (compiled, passed, output); sources from `root` (a copy with the mutation), headers from ROOT.
    Mutants compile without -Werror: a removed use of a parameter is a warning, not a fault of the mutant."""
    spec = BUILDS[build]
    exe = workdir / f"{name}.exe"
    cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion"]
    cmd += ["-Werror"] if strict else ["-Wno-unused-parameter", "-Wno-unused-function", "-Wno-unused-const-variable"]
    cmd += [f"-I{ROOT / i}" for i in spec["include"]]
    cmd += [f"-isystem{ROOT / i}" for i in spec["system_include"]]
    cmd += [str(root / s) for s in spec["sources"]] + ["-o", str(exe)]
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        return False, False, result.stdout + result.stderr
    try:
        run = subprocess.run([str(exe)], cwd=ROOT, capture_output=True, text=True, timeout=RUN_LIMIT_S)
    except subprocess.TimeoutExpired:
        return True, False, "hangs"
    return True, run.returncode == 0, run.stdout[-2000:]


def apply(root: Path, changes):
    for rel, old, new, occurrences in changes:
        path = root / rel
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        found = text.count(old)
        if found != occurrences:
            raise AssertionError(f"{rel}: {old!r} found {found} times, expected {occurrences}")
        path.write_text(text.replace(old, new), encoding="utf-8")


class ContractTest(unittest.TestCase):
    def test_family_copies_are_identical(self):
        first = normalised(ROOT / CONTRACT_COPIES[0])
        for other in CONTRACT_COPIES[1:]:
            self.assertEqual(first, normalised(ROOT / other), f"{other} differs from {CONTRACT_COPIES[0]}")


@unittest.skipIf(os.environ.get("NX_CLOCK_CHECK_MUTANTS") == "0", "NX_CLOCK_CHECK_MUTANTS=0")
@unittest.skipUnless((OPENBSW / "platforms/s32k1xx").is_dir(), "submodule external/openbsw missing")
class ClockCheckMutationTest(unittest.TestCase):
    def test_mutants_are_detected(self):
        work = Path(tempfile.mkdtemp(prefix="nx_clockcheck_mut_"))
        try:
            for build in BUILDS:
                compiled, passed, output = compile_and_run(ROOT, build, work, f"original_{build}")
                self.assertTrue(compiled and passed, f"original {build} does not pass:\n{output}")
            survivors, invalid = [], []
            for index, (name, (build, changes)) in enumerate(MUTATIONS.items()):
                copy = work / f"m{index}"
                for source in BUILDS[build]["sources"]:
                    (copy / source).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(ROOT / source, copy / source)
                apply(copy, changes)
                compiled, passed, output = compile_and_run(copy, build, work, f"m{index}", strict=False)
                if not compiled:
                    invalid.append(name)
                    state = "does not compile"
                elif passed:
                    survivors.append(name)
                    state = "SURVIVED"
                else:
                    state = "detected" + (" (hangs)" if output == "hangs" else "")
                print(f"{state:>18}: {name}", flush=True)
                if name in invalid:
                    print(output[-1500:])
            print(f"{len(MUTATIONS)} mutants, {len(survivors)} survived, {len(invalid)} did not compile")
            self.assertEqual(invalid, [], "mutants must compile")
            self.assertEqual(survivors, [], "mutants not detected by the host tests")
        finally:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    unittest.main()
