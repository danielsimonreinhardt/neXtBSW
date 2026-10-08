# SPDX-License-Identifier: Apache-2.0
"""Meaningfulness of the TMC6200 host test for IO-67: deliberately broken variants must fail it.

IO-67 (board 2026-09-30): a start without the supply VS of the power stage latched COMMUNICATION, and only
`gd clear` removed it after the supply was there. Since IO-67 an all-zero IOIN datagram is NO_SUPPLY: latched only
when the device goes missing out of DISABLED or ENABLED, a current fault only while the driver has not brought the
device up; every other startup failure still latches, and nothing switches on by itself. The host test
(Tmc6200HostTest.cpp, missingSupplyIsNotACommunicationFault and the IO-29 tests) pins each part of that rule.

Same mechanics as test_io44_mutations.py (whose helpers are reused): each mutation is applied to a copy of
src/gd/Tmc6200.cpp or include/gd/Tmc6200.h, the unchanged host test is compiled against it with zig c++ and run;
a mutant counts as detected if the test exits non-zero or hangs, a mutant that does not compile is a fault of this
script.

Run: .venv/Scripts/python libs/bsw/gdTmc6200/test/host/test_io67_mutations.py (about 1 min, 2 min under load)
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
HDR = io44.HDR

LATCH_RULE = "    if ((status != Status::NO_SUPPLY) || wasRunning)\n"
SIGNATURE = "    if ((address == TMC6200_REG_IOIN) && ((rx & FRAME_MASK) == 0U))\n"

# name: (file, [(old, new, occurrences), ...])
MUTATIONS = {
    "a missing supply at the start latches again (the board finding of 2026-09-30)": (SRC, [
        (LATCH_RULE, "    if (((void)wasRunning, true))\n", 1),
    ]),
    "a supply lost while running is not latched": (SRC, [
        (LATCH_RULE, "    if (((void)wasRunning, status != Status::NO_SUPPLY))\n", 1),
    ]),
    "only a supply lost while ENABLED counts as running (DISABLED forgets it)": (SRC, [
        ("    bool const wasRunning = (_state == State::DISABLED) || (_state == State::ENABLED);\n",
         "    bool const wasRunning = (_state == State::ENABLED);\n", 1),
    ]),
    "the all-zero answer is not recognized (wrong device, COMMUNICATION as before IO-67)": (SRC, [
        (SIGNATURE, "    if (false)\n", 1),
    ]),
    "the status byte is ignored (zero data alone counts as missing supply)": (SRC, [
        (SIGNATURE, "    if ((address == TMC6200_REG_IOIN) && ((rx & TMC6200_DATA_MASK) == 0U))\n", 1),
    ]),
    "every register reading 0 counts as missing supply (GSTAT 0 of a supplied device)": (SRC, [
        (SIGNATURE, "    if ((rx & FRAME_MASK) == 0U)\n", 1),
    ]),
    "a missing supply is reported as a communication fault": (SRC, [
        ("                       : (status == Status::NO_SUPPLY)  ? fault::NO_SUPPLY\n", "", 1),
    ]),
    "the answer class of an attempt adds to the previous one instead of replacing it": (SRC, [
        ("    _faults = static_cast<uint16_t>((static_cast<uint32_t>(_faults) & ~static_cast<uint32_t>(answer)) | f);\n",
         "    (void)answer;\n    _faults = static_cast<uint16_t>(_faults | f);\n", 1),
    ]),
    "a lost supply does not block the next enable (not a shutdown fault)": (HDR, [
        ("                              | TEST_MODE | NO_SUPPLY;", "                              | TEST_MODE;", 1),
    ]),
    "the reply does not name both causes": (SRC, [
        ('case Status::NO_SUPPLY: return "no supply or SDO low";', 'case Status::NO_SUPPLY: return "no supply";', 1),
    ]),
}


class Tmc6200Io67MutationTest(unittest.TestCase):
    def test_mutants_are_detected(self):
        work = Path(tempfile.mkdtemp(prefix="nx_tmc6200_io67_mut_"))
        try:
            compiled, passed, output = io44.compile_and_run(MODULE / SRC, None, work, "original")
            self.assertTrue(compiled and passed, f"original does not pass:\n{output}")
            survivors, invalid = [], []
            for index, (name, (rel, changes)) in enumerate(MUTATIONS.items()):
                if rel == SRC:
                    source, header_dir = work / f"Tmc6200_m{index}.cpp", None
                    io44.mutate(rel, source, changes)
                else:
                    source, header_dir = MODULE / SRC, work / f"include_m{index}"
                    io44.mutate(rel, header_dir / "gd" / Path(rel).name, changes)
                compiled, passed, output = io44.compile_and_run(source, header_dir, work, f"m{index}")
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
