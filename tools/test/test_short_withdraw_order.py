"""DOC-44 (clause SSR-092(d)): the hardware threshold watch withdraws the active short before it forces the group.

SSR-092(d): a finding of the group's hardware threshold path or its latched fault input shall withdraw the short of the
group (SSR-089 (f)) before the family port forces the group.

The withdrawal itself (what the walks, the lock and the enable do afterwards) is shown by the host test short-hold. What
that test cannot show is the wiring in the poll of the threshold watch (platforms/s32k1xx/port/main/src/integration/
HwThresholdWatch.h, cyclic()): this test reads the source of that poll and requires

1. a call of shortHold::removeForHardwarePath() that is guarded by the project's active-short switch
   (NX_SAFE_OUTPUT_SELECT), so that every other project is built as before (SSR-092 note);
2. a condition on that call that is true for a latched finding and for the timer's fault latch
   (hwThreshold::faultDetected);
3. the call textually before the inhibit of the group, which is the first thing the poll writes to the timer.

Built-in faults (the check applied to a modified copy of the source) must be found: no call, a call after the inhibit,
a condition without the fault latch, a condition without the findings, a call outside the active-short guard, and a
call that cannot be reached (inside an `if (false)`).

Run: .venv/Scripts/python tools/test/test_short_withdraw_order.py
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WATCH = REPO / "platforms/s32k1xx/port/main/src/integration/HwThresholdWatch.h"


def cyclic_body(source: str) -> str:
    """The text of HwThresholdWatch::cyclic() from its signature to the matching closing brace."""
    start = source.index("uint32_t cyclic()")
    brace = source.index("{", start)
    depth = 0
    for i in range(brace, len(source)):
        depth += {"{": 1, "}": -1}.get(source[i], 0)
        if depth == 0:
            return source[brace:i + 1]
    raise AssertionError("cyclic() is not closed")


def findings(source: str) -> list[str]:
    """Problems of the wiring in cyclic(), empty if it is right."""
    body = cyclic_body(source)
    problems = []
    guard = re.search(r"#if defined\(NX_SAFE_OUTPUT_SELECT\)\n(.*?)#endif", body, re.S)
    if guard is None:
        return ["no #if defined(NX_SAFE_OUTPUT_SELECT) block in cyclic()"]
    block = guard.group(1)
    condition = re.search(r"if \(([^\n]*)\)\s*\{\s*(?://[^\n]*\s*)*shortHold::removeForHardwarePath\(\);\s*\}", block)
    if condition is None:
        problems.append("the guarded block has no `if (...) { shortHold::removeForHardwarePath(); }`")
    else:
        text = condition.group(1)
        if "latched != 0U" not in text:
            problems.append(f"the condition `{text}` does not name the latched findings")
        if "hwThreshold::faultDetected(" not in text:
            problems.append(f"the condition `{text}` does not name the timer's fault latch")
        if text.strip() in ("false", "0", "0U"):
            problems.append("the condition can never be true")
    if body.count("removeForHardwarePath") != 1:
        problems.append(f"removeForHardwarePath appears {body.count('removeForHardwarePath')} times in cyclic(), "
                        "expected exactly once (inside the guard)")
    call = body.find("removeForHardwarePath")
    inhibit = body.find("_inhibit.inhibit()")
    if inhibit < 0:
        problems.append("no inhibit of the group in cyclic()")
    elif call < 0 or call > inhibit:
        problems.append("the short is not withdrawn before the group is inhibited")
    if re.search(r"if \((false|0|0U)\)", body):
        problems.append("a branch of cyclic() that can never run")
    return problems


class ShortWithdrawOrderTest(unittest.TestCase):
    source = ""

    @classmethod
    def setUpClass(cls):
        cls.source = WATCH.read_text(encoding="utf-8").replace("\r\n", "\n")

    def test_watch_withdraws_the_short_before_it_inhibits_the_group(self):
        self.assertEqual(findings(self.source), [])

    def test_mutants_are_detected(self):
        src = self.source
        call = "            shortHold::removeForHardwarePath();\n"
        condition = "if ((latched != 0U) || ::bios::hwThreshold::faultDetected(::bios::ftmPwm::ftm()))"
        inhibit = "        if (latched != 0U)\n        {\n            _inhibit.inhibit();\n        }\n"
        for needle in (call, condition, inhibit, "#if defined(NX_SAFE_OUTPUT_SELECT)"):
            self.assertEqual(src.count(needle), 1, needle)
        mutants = {
            "no call": src.replace(call, ""),
            "call after the inhibit": src.replace(call, "").replace(
                inhibit, inhibit + "#if defined(NX_SAFE_OUTPUT_SELECT)\n        shortHold::removeForHardwarePath();\n#endif\n"),
            "condition without the fault latch": src.replace(
                condition, "if ((latched != 0U))"),
            "condition without the findings": src.replace(
                condition, "if (::bios::hwThreshold::faultDetected(::bios::ftmPwm::ftm()))"),
            "call outside the active-short guard": src.replace("#if defined(NX_SAFE_OUTPUT_SELECT)\n", "").replace(
                "#endif\n        if (latched != 0U)\n", "        if (latched != 0U)\n"),
            "unreachable call": src.replace(condition, "if (false)"),
        }
        for name, mutant in mutants.items():
            with self.subTest(mutant=name):
                self.assertNotEqual(mutant, src)
                self.assertNotEqual(findings(mutant), [], name)


if __name__ == "__main__":
    unittest.main()
