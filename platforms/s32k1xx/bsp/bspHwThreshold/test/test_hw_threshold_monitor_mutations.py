"""SYS-128/SYS-130: meaningfulness of test_hw_threshold_monitor.py - built-in faults in the family code
(hwThreshold/HwThresholdMonitor.h), the PWM driver (ComplementaryPwm.h) and the power stage poll
(integration/HwThresholdWatch.h) must each make HwThresholdMonitorHostTest.cpp fail or not compile.

    .venv/Scripts/python -m unittest platforms/s32k1xx/bsp/bspHwThreshold/test/test_hw_threshold_monitor_mutations.py
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_hw_threshold_monitor as base  # noqa: E402

FAMILY = Path("hwThreshold/HwThresholdMonitor.h")
DRIVER = Path("bsp/io/outputPwm/ComplementaryPwm.h")
WATCH = Path("HwThresholdWatch.h")

# (what, where, old, new): `where` is "family", "port" or "integration"; every `old` must occur exactly once
MUTANTS = [
    # SYS-128 start test
    ("start test: first comparison not evaluated", "family", FAMILY,
     "        findings |= START_STUCK_ABOVE;\n", ""),
    ("start test: second comparison not evaluated", "family", FAMILY,
     "        findings |= START_STUCK_NOT_ABOVE;\n", ""),
    ("start test: DAC on the minus side in both comparisons", "family", FAMILY,
     "dacOnPlus ? (dac | CMP_C1_INPSEL(0U) | CMP_C1_INNSEL(1U) | CMP_C1_MSEL(input))",
     "dacOnPlus ? (dac | CMP_C1_INPSEL(1U) | CMP_C1_INNSEL(0U) | CMP_C1_PSEL(input))"),
    ("start test without settling", "family", FAMILY,
     "    wait(c.settleUs);\n    if ((cmp.C0 & CMP_C0_COUT_MASK) != 0U)", "    if ((cmp.C0 & CMP_C0_COUT_MASK) != 0U)"),
    ("start test leaves the comparator configured for the test", "family", FAMILY,
     "    cmp.C0 = 0U;\n    cmp.C1 = 0U;\n    return findings;", "    return findings;"),
    # SYS-128 register check
    ("comparator off not reported", "family", FAMILY, "        findings |= COMPARATOR_OFF;\n", ""),
    ("C0 compared with its status bits", "family", FAMILY,
     "& CMP_C0_CONFIG_MASK & ~CMP_C0_EN_MASK", "& ~CMP_C0_EN_MASK"),
    ("C1 not checked", "family", FAMILY, "        findings |= REFERENCE;\n", ""),
    ("round robin not checked", "family", FAMILY, "        findings |= ROUND_ROBIN;\n", ""),
    ("lock of the route not checked", "family", FAMILY, " || ((route & TRGMUX_TRGMUXn_LK_MASK) == 0U)", ""),
    ("fault source select not checked", "family", FAMILY, "        findings |= SOURCE;\n", ""),
    ("FLTCTRL not checked", "family", FAMILY, "&& ((ftm.FLTCTRL & FTM_FLTCTRL_CONFIG_MASK) == f.fltctrl)", ""),
    ("FAULTEN not checked", "family", FAMILY, "((ftm.COMBINE & f.faulten) == f.faulten)", "true"),
    # SYS-128 consistency
    ("one sample makes NOT_LATCHED", "family", FAMILY, "NOT_LATCHED_SAMPLES = 2U;", "NOT_LATCHED_SAMPLES = 1U;"),
    ("the fault latch of the timer not consulted", "family", FAMILY,
     "if (above && !faultDetected(ftm))", "if (above)"),
    ("a disabled comparator taken as above", "family", FAMILY,
     "bool const above = ((c0 & CMP_C0_EN_MASK) != 0U) && ((c0 & CMP_C0_COUT_MASK) != 0U);",
     "bool const above = ((c0 & CMP_C0_COUT_MASK) != 0U);"),
    # findings latch
    ("latch word not checked", "family", FAMILY,
     "((word >> 16U) == (~findings & 0xFFFFU)) ? findings :", "(word != 1U) ? findings :"),
    ("latch overwritten instead of added to", "family", FAMILY,
     "findingsLatch       = latchWordOf(kept | findings);", "findingsLatch       = latchWordOf(findings);"),
    # SYS-130 report context of the safety cycle
    ("report without the timer's fault latch", "family", FAMILY,
     "return reportContext(faultDetected(ftm), findings, timerOf(c));",
     "return reportContext(false, findings, timerOf(c));"),
    ("findings latch not in the report", "family", FAMILY,
     "configurationFindings(cmp, trgmux, sim, c) | latchedFindings();", "configurationFindings(cmp, trgmux, sim, c);"),
    ("fault control not in the report", "family", FAMILY,
     "        findings |= FAULT_CONTROL;\n    }\n    return reportContext", "    }\n    return reportContext"),
    ("findings at the wrong place of the word", "family", FAMILY,
     "((findings & 0xFFFFU) << 8U)", "((findings & 0xFFFFU) << 16U)"),
    ("timer at the wrong place of the word", "family", FAMILY,
     "((timer & 0xFFU) << 24U)", "((timer & 0xFFU) << 16U)"),
    # PWM driver
    ("no start test in init", "port", DRIVER,
     "        hwThreshold::latchFindings(\n"
     "            hwThreshold::startTest(hwThresholdConfig::cmp(), hwThresholdConfig::CONFIG, &sysDelayUs));\n", ""),
    ("failed start test does not force the group", "port", DRIVER,
     "            _ftm.getFtm().SWOCTRL = ftmPwm::CHANNEL_MASK;\n        }\n#endif\n#ifdef NX_PWM_GROUP_LOCK",
     "        }\n#endif\n#ifdef NX_PWM_GROUP_LOCK"),
    # SYS-129 path test behind the comparator (family code)
    ("path test: fault without forcing not evaluated", "family", FAMILY,
     "        findings |= PATH_FAULT_UNFORCED;\n", ""),
    ("path test: missing trip not evaluated", "family", FAMILY, "        findings |= PATH_NO_TRIP;\n", ""),
    ("path test: failed clearing not evaluated", "family", FAMILY, "        findings |= PATH_NO_CLEAR;\n", ""),
    ("path test: route not selected", "family", FAMILY,
     "    trgmux.TRGMUXn[c.trgmuxIndex] = disabled | c.trgmuxSel;\n", ""),
    ("path test: route locked by the test", "family", FAMILY,
     "trgmux.TRGMUXn[c.trgmuxIndex] = disabled | c.trgmuxSel;",
     "trgmux.TRGMUXn[c.trgmuxIndex] = disabled | c.trgmuxSel | TRGMUX_TRGMUXn_LK_MASK;"),
    ("path test: comparator not forced above", "family", FAMILY,
     "    cmp.C1 = startTestC1(c, true);\n    cmp.C0 = ON;\n    wait(c.settleUs);\n    wait(c.settleUs);",
     "    cmp.C1 = startTestC1(c, false);\n    cmp.C0 = ON;\n    wait(c.settleUs);\n    wait(c.settleUs);"),
    ("path test: trip checked after one wait only", "family", FAMILY,
     "    wait(c.settleUs);\n    wait(c.settleUs);\n    if ((ftm.FMS & seen) != seen)",
     "    wait(c.settleUs);\n    if ((ftm.FMS & seen) != seen)"),
    ("path test: cleared before the input fell", "family", FAMILY,
     "    wait(c.settleUs);\n    uint32_t const status", "    uint32_t const status"),
    ("path test: clearing checked without a wait", "family", FAMILY,
     "    wait(c.settleUs);\n    if ((ftm.FMS & seen) != 0U)\n    {\n        findings |= PATH_NO_CLEAR;",
     "    if ((ftm.FMS & seen) != 0U)\n    {\n        findings |= PATH_NO_CLEAR;"),
    ("path test: flags of the other inputs cleared as well", "family", FAMILY,
     "| FTM_FMS_FAULTF_MASK | (FLAGS & ~flag);", "| FTM_FMS_FAULTF_MASK;"),
    ("path test: 0 written to FAULTF (clears every input)", "family", FAMILY,
     "| FTM_FMS_FAULTF_MASK | (FLAGS & ~flag);", "| (FLAGS & ~flag);"),
    ("path test: flag of the wrong fault input", "family", FAMILY,
     "uint32_t const flag      = 1UL << faultInputOf(c);", "uint32_t const flag      = 1UL << (faultInputOf(c) + 1U);"),
    ("path test: route left selected", "family", FAMILY, "    trgmux.TRGMUXn[c.trgmuxIndex] = disabled;\n", ""),
    ("path test: comparator not configured again", "family", FAMILY,
     "    cmp.C1                        = c.cmpC1;\n", ""),
    # SYS-129 path test in the PWM driver
    ("no path test in init", "port", DRIVER,
     "            hwThreshold::latchFindings(hwThreshold::pathTest(\n"
     "                hwThresholdConfig::cmp(), hwThresholdConfig::trgmux(), _ftm.getFtm(), hwThresholdConfig::CONFIG,\n"
     "                &sysDelayUs));\n", ""),
    ("failed path test does not force the group", "port", DRIVER,
     "                _ftm.getFtm().SWOCTRL = ftmPwm::CHANNEL_MASK; // freewheel has priority (review SYS-128 C2)\n", ""),
    ("path test after a failed start test", "port", DRIVER,
     "        if (hwThreshold::latchedFindings() == 0U)\n        {\n            hwThreshold::latchFindings(hwThreshold::pathTest(",
     "        if (true)\n        {\n            hwThreshold::latchFindings(hwThreshold::pathTest("),
    # power stage poll
    ("poll does not force the group", "integration", WATCH, "            _inhibit.inhibit();\n", ""),
    ("poll does not latch its findings", "integration", WATCH,
     "            ::bios::hwThreshold::latchFindings(found);\n", ""),
    ("poll reports the same finding again", "integration", WATCH,
     "_reported            = latched;", "_reported            = 0U;"),
]

SOURCES = {"family": base.MODULE / "include", "port": base.PORT_INCLUDE, "integration": base.INTEGRATION}


@unittest.skipUnless(base.zig_available(), "ziglang missing (requires: ziglang)")
@unittest.skipUnless((base.OPENBSW / "platforms").is_dir(), "external/openbsw missing (git submodule update)")
class MutationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.gen = base.generate(cls.tmp / "gen")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_unmutated_passes(self):
        verdict, output = base.build_and_run(self.gen, self.tmp, "clean")
        self.assertEqual(verdict, "ok", output[-3000:])

    def test_mutants(self):
        for n, (what, where, file, old, new) in enumerate(MUTANTS):
            with self.subTest(mutant=what):
                root = self.tmp / f"m{n}"
                roots = {}
                for key, source in SOURCES.items():
                    roots[key] = root / key
                    shutil.copytree(source, roots[key])
                target = roots[where] / file
                text = target.read_text(encoding="utf-8").replace("\r\n", "\n")
                self.assertEqual(text.count(old), 1, f"mutation point of '{what}' not unique")
                target.write_text(text.replace(old, new), encoding="utf-8")
                verdict, output = base.build_and_run(self.gen, self.tmp, f"m{n}", roots["family"], roots["port"],
                                                     roots["integration"])
                self.assertIn(verdict, ("compile", "fail"), f"mutant '{what}' survived:\n{output[-1500:]}")


if __name__ == "__main__":
    unittest.main()
