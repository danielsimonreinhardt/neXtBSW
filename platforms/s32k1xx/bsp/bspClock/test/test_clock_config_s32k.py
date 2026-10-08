# SPDX-License-Identifier: Apache-2.0
"""Clock driver of the family s32k1xx with bounded waits (PLAT-85): meaningfulness of the host test.

Deliberately broken variants of the driver copy (src/clockConfig.cpp) and of the start-up check (src/ClockCheck.cpp)
must make the host test test/host/ClockConfigS32kHostTest.cpp fail (profile s32k148: crystal, SPLL; same compiler
line as clock-config-s32k-s32k148 in tools/test/tests.yaml). Each mutation is applied to a copy of the file it touches,
the host test is compiled against the copy with zig c++ and run. A mutant counts as detected if the test exits
non-zero or hangs (a wait without limit is the finding of PLAT-72 itself); a mutant that does not compile is a fault
of this script.

Run: .venv/Scripts/python -m unittest platforms/s32k1xx/bsp/bspClock/test/test_clock_config_s32k.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
OPENBSW = ROOT / "external/openbsw"
RUN_LIMIT_S = 30

K = "platforms/s32k1xx/bsp/bspClock"
DRIVER = f"{K}/src/clockConfig.cpp"
CHECK = f"{K}/src/ClockCheck.cpp"

INCLUDE = [f"{K}/test/host/profiles/s32k148", f"{K}/test/host/driverModel", f"{K}/include",
           "platforms/common/clockCheck/include", "external/openbsw/platforms/s32k1xx/bsp/bspClock/include",
           "external/openbsw/platforms/s32k1xx/bsp/bspClock", "external/openbsw/libs/3rdparty/etl/include"]
SYSTEM_INCLUDE = ["external/openbsw/platforms/s32k1xx/bsp/bspMcu/include/3rdparty/nxp"]
SOURCES = [DRIVER, CHECK, "platforms/common/clockCheck/src/nxClockCheckReport.cpp",
           f"{K}/test/host/OpenBswClockConfig.cpp", f"{K}/test/host/ClockConfigS32kHostTest.cpp"]

# name: [(file, old, new, occurrences), ...]; `old` must occur exactly `occurrences` times
MUTATIONS = {
    "SOSC waits without limit (OpenBSW behaviour)": [
        (DRIVER, "uint32_t polls = SOSC_START_POLLS;", "uint32_t polls = 0U;", 1)],
    "SPLL waits without limit": [
        (DRIVER, "uint32_t polls = SPLL_START_POLLS;", "uint32_t polls = 0U;", 1)],
    "FIRC waits without limit": [
        (DRIVER, "uint32_t polls = FIRC_START_POLLS;", "uint32_t polls = 0U;", 1)],
    "SIRC waits without limit": [
        (DRIVER, "uint32_t polls = SIRC_START_POLLS;", "uint32_t polls = 0U;", 1)],
    "clock switch waits without limit": [
        (DRIVER, "uint32_t polls = CLOCK_SWITCH_POLLS;", "uint32_t polls = 0U;", 1)],
    "SOSC budget halved (10 ms)": [
        (DRIVER, "SOSC_START_POLLS   = 20000U * POLLS_PER_US;", "SOSC_START_POLLS   = 10000U * POLLS_PER_US;", 1)],
    "SPLL budget other than 5 ms": [
        (DRIVER, "SPLL_START_POLLS = 5000U * POLLS_PER_US;", "SPLL_START_POLLS = 4000U * POLLS_PER_US;", 1)],
    "FIRC budget other than 1 ms": [
        (DRIVER, "FIRC_START_POLLS   = 1000U * POLLS_PER_US;", "FIRC_START_POLLS   = 999U * POLLS_PER_US;", 1)],
    "SIRC budget other than 1 ms": [
        (DRIVER, "SIRC_START_POLLS   = 1000U * POLLS_PER_US;", "SIRC_START_POLLS   = 1001U * POLLS_PER_US;", 1)],
    "budgets for 48 MHz instead of the family maximum 112 MHz": [
        (DRIVER, "POLLS_PER_US       = 112U;", "POLLS_PER_US       = 48U;", 1)],
    "SOSC monitor on although the crystal did not start": [
        (DRIVER, "    bool const soscValid = soscStart();\n    if (soscValid)\n    {\n        soscMon();\n    }",
         "    bool const soscValid = soscStart();\n    soscMon();", 1)],
    "SPLL started without a valid reference": [
        (DRIVER, "    // the SPLL needs the SOSC as reference\n    if (soscValid)", "    if (true)", 1)],
    "SPLL monitor on without lock": [
        (DRIVER, "        if (spllStart())\n        {\n            spllMon();\n        }",
         "        (void)spllStart();\n        spllMon();", 1)],
    "planned source requested although not valid": [
        (DRIVER, "    if (isPlannedSourceValid())", "    if (true)", 1)],
    "switch to FIRC although it did not start": [
        (DRIVER, "        if (fircStart())\n        {\n            fircSysClk();\n        }",
         "        (void)fircStart();\n        fircSysClk();", 1)],
    "switch to SIRC although it did not start": [
        (DRIVER, "        if (sircStart())\n        {\n            sircSysClk();\n        }",
         "        (void)sircStart();\n        sircSysClk();", 1)],
    "SIRC stopped while it is the system clock": [
        (DRIVER, "if (sircNeedsConfig && !isSircSysClk())", "if (sircNeedsConfig)", 1)],
    "systemClock2Pll goes on without crystal": [
        (DRIVER, "            if (!soscStart())\n            {\n                return; // PLAT-85: stay on the current clock\n"
                 "            }\n            soscMon();",
         "            (void)soscStart();\n            soscMon();", 1)],
    "systemClock2Firc switches without FIRC": [
        (DRIVER, "        if (!fircStart())\n        {\n            return; // PLAT-85: stay on the current clock\n"
                 "        }\n    }\n\n    fircSysClk();",
         "        (void)fircStart();\n    }\n\n    fircSysClk();", 1)],
    "copy writes one register more than the original (normal start slower)": [
        (DRIVER, "    SCG->CLKOUTCNFG = SCG_CLKOUTCNFG_CLKOUTSEL(CLOCKoUT_SELECT);",
         "    SCG->CLKOUTCNFG = SCG_CLKOUTCNFG_CLKOUTSEL(CLOCKoUT_SELECT);\n    SCG->CLKOUTCNFG = SCG_CLKOUTCNFG_CLKOUTSEL(CLOCKoUT_SELECT);", 1)],
    "check: SIRC valid flag ignored": [
        (CHECK, "((SCG->SIRCCSR & SCG_SIRCCSR_SIRCVLD_MASK) != 0U) && ", "", 1)],
    "check: FIRC valid flag ignored": [
        (CHECK, " && ((SCG->FIRCCSR & SCG_FIRCCSR_FIRCVLD_MASK) != 0U)", "", 1)],
}


def compile_and_run(root: Path, workdir: Path, name: str, strict: bool = True):
    """Returns (compiled, passed, output); sources from `root` (a copy with the mutation), headers from ROOT."""
    exe = workdir / f"{name}.exe"
    cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion"]
    cmd += ["-Werror"] if strict else ["-Wno-unused-parameter", "-Wno-unused-function", "-Wno-unused-variable"]
    cmd += [f"-I{ROOT / i}" for i in INCLUDE]
    cmd += [f"-isystem{ROOT / i}" for i in SYSTEM_INCLUDE]
    cmd += [str(root / s) for s in SOURCES] + ["-o", str(exe)]
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


@unittest.skipUnless((OPENBSW / "platforms/s32k1xx").is_dir(), "submodule external/openbsw missing")
class ClockConfigMutationTest(unittest.TestCase):
    def test_mutants_are_detected(self):
        work = Path(tempfile.mkdtemp(prefix="nx_clockconfig_mut_"))
        try:
            compiled, passed, output = compile_and_run(ROOT, work, "original")
            self.assertTrue(compiled and passed, f"original does not pass:\n{output}")
            survivors, invalid = [], []
            for index, (name, changes) in enumerate(MUTATIONS.items()):
                copy = work / f"m{index}"
                for source in SOURCES:
                    (copy / source).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(ROOT / source, copy / source)
                apply(copy, changes)
                compiled, passed, output = compile_and_run(copy, work, f"m{index}", strict=False)
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
            self.assertEqual(survivors, [], "mutants not detected by the host test")
        finally:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    unittest.main()
