# SPDX-License-Identifier: Apache-2.0
"""Meaningfulness of the TMC6200 host test for SYS-147: deliberately broken variants must fail it.

SYS-147: DRV_EN is the driver's output. Every cyclic() drives it low again while it is not commanded high - in every
state (DISABLED, FAILED, UNINITIALIZED, locked by S1) - and after the supervision, so an uncommanded high level the
device shows in IOIN is latched as ENABLE_MISMATCH before it is taken back. The host test
(Tmc6200HostTest.cpp, foreignDriverEnableIsTakenBack) writes the pin behind the driver's back like the console
`out set`, a debugger or a stray store would.

Same mechanics as test_io44_mutations.py (whose helpers are reused): each mutation is applied to a copy of
src/gd/Tmc6200.cpp, the unchanged host test is compiled against it with zig c++ and run; a mutant counts as detected
if the test exits non-zero or hangs, a mutant that does not compile is a fault of this script.

Run: .venv/Scripts/python libs/bsw/gdTmc6200/test/host/test_sys147_mutations.py (about 1 min, 2 min under load)
"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_io44_mutations as io44  # noqa: E402  (helpers only; its TestCase is not collected here)

MODULE = io44.MODULE
SRC = io44.SRC

HOLD_CALL = "        supervise(nowMs, lockedBefore);\n    }\n    holdDriverEnableLow();\n}"
HOLD_BODY = "    if (!_driverEnableCommanded)\n    {\n        (void)_io.setDriverEnable(false);\n    }"

# name: [(old, new, occurrences), ...] on src/gd/Tmc6200.cpp
MUTATIONS = {
    "the hold is never called (the state before SYS-147)": [
        (HOLD_CALL, "        supervise(nowMs, lockedBefore);\n    }\n}", 1),
    ],
    "the hold runs before the supervision (the foreign level is no longer latched as a mismatch)": [
        ("    bool const lockedBefore = _locked;\n    (void)updateEnableLock(); // SYS-36: before anything reads DRV_EN back\n",
         "    bool const lockedBefore = _locked;\n    holdDriverEnableLow();\n"
         "    (void)updateEnableLock(); // SYS-36: before anything reads DRV_EN back\n", 1),
        (HOLD_CALL, "        supervise(nowMs, lockedBefore);\n    }\n}", 1),
    ],
    "the hold only in the supervised states (FAILED waits for the next retry)": [
        (HOLD_CALL, "        supervise(nowMs, lockedBefore);\n        holdDriverEnableLow();\n    }\n}", 1),
    ],
    "the hold only when IOIN showed the level (blind without supply)": [
        (HOLD_BODY,
         "    if (!_driverEnableCommanded && ((_lastIoin & TMC6200_IOIN_DRV_EN_MASK) != 0U))\n"
         "    {\n        (void)_io.setDriverEnable(false);\n    }", 1),
    ],
    "the hold ignores the commanded level (an enabled bridge is switched off every cycle)": [
        (HOLD_BODY, "    (void)_io.setDriverEnable(false);", 1),
    ],
    "the hold writes the wrong level": [
        (HOLD_BODY, "    if (!_driverEnableCommanded)\n    {\n        (void)_io.setDriverEnable(true);\n    }", 1),
    ],
    "the hold is skipped while S1 locks the driver": [
        (HOLD_BODY, "    if (!_driverEnableCommanded && !_locked)\n    {\n        (void)_io.setDriverEnable(false);\n    }", 1),
    ],
}


class Tmc6200Sys147MutationTest(unittest.TestCase):
    def test_mutants_are_detected(self):
        work = Path(tempfile.mkdtemp(prefix="nx_tmc6200_sys147_mut_"))
        try:
            compiled, passed, output = io44.compile_and_run(MODULE / SRC, None, work, "original")
            self.assertTrue(compiled and passed, f"original does not pass:\n{output}")
            survivors, invalid = [], []
            for index, (name, changes) in enumerate(MUTATIONS.items()):
                source = work / f"Tmc6200_m{index}.cpp"
                io44.mutate(SRC, source, changes)
                compiled, passed, output = io44.compile_and_run(source, None, work, f"m{index}")
                if not compiled:
                    invalid.append(name)
                    state = "does not compile"
                elif passed:
                    survivors.append(name)
                    state = "SURVIVED"
                else:
                    state = "detected" + (" (hangs)" if output == "hangs" else "")
                print(f"{state:>18}: {name}", flush=True)
                if not compiled:
                    print(output[-1500:])
            print(f"{len(MUTATIONS)} mutants, {len(survivors)} survived, {len(invalid)} did not compile")
            self.assertEqual(invalid, [], "mutants must compile")
            self.assertEqual(survivors, [], "mutants not detected by the host test")
        finally:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    unittest.main()
