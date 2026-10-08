# SPDX-License-Identifier: Apache-2.0
"""Meaningfulness of the TMC6200 host test for IO-44: deliberately broken variants must fail it.

Each mutation is applied to a copy of src/gd/Tmc6200.cpp or of include/gd/Tmc6200.h, the host test
(Tmc6200HostTest.cpp, unchanged) is compiled against the copy with zig c++ exactly as the tools/test entry
`tmc6200` does, and run. A mutated header is put into an include directory in front of the module's own one.
A mutant counts as detected if the test exits non-zero or hangs; a mutant that does not compile is a fault of
this script.

The mutations are the ways the three answers of IO-44 could be undone:
  1. a reported short (GSTAT shortdet_u/v/w) no longer switches the bridge off - the board gap of 2026-09-23,
  2. the enable rule lets a mixture of phase states through or refuses a common state (IO-45: the rule is one check
     over all three phases - all off, all switching or all full - instead of "every phase switches"),
  3. `gd clear` reports success although the condition is still there.

Run: .venv/Scripts/python libs/bsw/gdTmc6200/test/host/test_io44_mutations.py (about 1 min)

Not in tools/test/tests.yaml yet - that file belongs to the coordinator; the entry is proposed in
doc/changelog.d/IO-44.md.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODULE = HERE.parents[1]
ROOT = HERE.parents[4]
SRC = "src/gd/Tmc6200.cpp"
HDR = "include/gd/Tmc6200.h"
RUN_LIMIT_S = 60

# name: (file, [(old, new, occurrences), ...]); `old` must occur exactly `occurrences` times in that file
MUTATIONS = {
    # ---- 1. the safety-relevant answer: a reported short must switch off
    "short detected is only reported again (the board gap of 2026-09-23)": (HDR, [
        ("| SHORT_TO_SUPPLY_V | SHORT_TO_SUPPLY_W | SHORT_DETECTED | ENABLE_MISMATCH",
         "| SHORT_TO_SUPPLY_V | SHORT_TO_SUPPLY_W | ENABLE_MISMATCH", 1),
    ]),
    "shortdet of phase W is not mapped to a fault": (SRC, [
        ("{TMC6200_GSTAT_SHORTDET_U_MASK | TMC6200_GSTAT_SHORTDET_V_MASK | TMC6200_GSTAT_SHORTDET_W_MASK,",
         "{TMC6200_GSTAT_SHORTDET_U_MASK | TMC6200_GSTAT_SHORTDET_V_MASK,", 1),
    ]),
    "the shutdown only latches, DRV_EN stays high": (SRC, [
        ("        ++_counters.shutdowns;\n        switchOff();\n        _state = State::DISABLED;",
         "        ++_counters.shutdowns;\n        _state = State::DISABLED;", 1),
    ]),
    "a shutdown fault does not block the next enable": (SRC, [
        ("    if ((getLatchedFaults() & fault::SHUTDOWN) != 0U)\n    {\n        return Status::NOT_ALLOWED;\n    }\n",
         "", 1),
    ]),
    "only the current faults switch off, not the ones of this cycle": (SRC, [
        ("if ((_state == State::ENABLED) && ((faults & fault::SHUTDOWN) != 0U))",
         "if ((_state == State::ENABLED) && ((faults & fault::SHUTDOWN & ~fault::SHORT_DETECTED) != 0U))", 1),
    ]),
    # ---- 2. the enable rule
    # IO-45: the rule over all three phases (one common state) - each of these undoes a part of its form
    "the rule of IO-44 again: every phase must switch": (HDR, [
        ("        return (phaseState(duty.u) == phaseState(duty.v)) && (phaseState(duty.v) == phaseState(duty.w));",
         "        return (phaseState(duty.u) == PhaseState::SWITCHING) && (phaseState(duty.v) == PhaseState::SWITCHING)\n"
         "               && (phaseState(duty.w) == PhaseState::SWITCHING);", 1),
    ]),
    "phase W is not compared": (HDR, [
        ("        return (phaseState(duty.u) == phaseState(duty.v)) && (phaseState(duty.v) == phaseState(duty.w));",
         "        return (phaseState(duty.u) == phaseState(duty.v));", 1),
    ]),
    "phase U is not compared": (HDR, [
        ("        return (phaseState(duty.u) == phaseState(duty.v)) && (phaseState(duty.v) == phaseState(duty.w));",
         "        return (phaseState(duty.v) == phaseState(duty.w));", 1),
    ]),
    "full is no state of its own (upper end stop removed: 50/50/100 passes)": (HDR, [
        ("        return (duty == 0U) ? PhaseState::OFF : ((duty >= DUTY_FULL) ? PhaseState::FULL : PhaseState::SWITCHING);",
         "        return (duty == 0U) ? PhaseState::OFF : PhaseState::SWITCHING;", 1),
    ]),
    "off is no state of its own (0/0/50 passes)": (HDR, [
        ("        return (duty == 0U) ? PhaseState::OFF : ((duty >= DUTY_FULL) ? PhaseState::FULL : PhaseState::SWITCHING);",
         "        return (duty >= DUTY_FULL) ? PhaseState::FULL : PhaseState::SWITCHING;", 1),
    ]),
    "full starts one step too late (exactly DUTY_FULL counts as switching)": (HDR, [
        ("((duty >= DUTY_FULL) ? PhaseState::FULL", "((duty > DUTY_FULL) ? PhaseState::FULL", 1),
    ]),
    "off and full are the same state (0/0/100 passes)": (HDR, [
        ("((duty >= DUTY_FULL) ? PhaseState::FULL : PhaseState::SWITCHING)",
         "((duty >= DUTY_FULL) ? PhaseState::OFF : PhaseState::SWITCHING)", 1),
    ]),
    "the rule is never applied (source ignored)": (SRC, [
        ("    if (_dutySource != nullptr)\n", "    if (false)\n", 1),
    ]),
    "an unknown duty counts as known (the driver guesses)": (SRC, [
        ("if (!known || !isCommonState(duty))", "if (((void)known, !isCommonState(duty)))", 1),
    ]),
    "the refusal switches DRV_EN on anyway": (SRC, [
        ("        if (!known || !isCommonState(duty))\n        {\n            return Status::PHASES_MIXED;\n        }\n",
         "        (void)known;\n", 1),
    ]),
    "the refusal is reported as ok": (SRC, [
        ("            return Status::PHASES_MIXED;", "            return Status::OK;", 1),
    ]),
    "the reason of the refusal is lost in the reply": (SRC, [
        ('case Status::PHASES_MIXED: return "phases mixed";',
         'case Status::PHASES_MIXED: return "not allowed";', 1),
    ]),
    "the duty of the refused attempt is not kept": (SRC, [
        ("        _lastDuty        = duty;\n", "", 1),
    ]),
    # ---- 3. an honest clear
    "clear reports success although the condition is still there": (SRC, [
        ("    return (_faults == 0U) ? Status::OK : Status::NOT_CLEARED;", "    return Status::OK;", 1),
    ]),
    "clear judges the latched bits instead of what the device reports now": (SRC, [
        ("    _faults        = faultsOf(gstat, ioin, _gconf);\n    _latchedFaults = _faults;\n    // IO-44",
         "    _faults        = 0U;\n    _latchedFaults = _faults;\n    // IO-44", 1),
    ]),
    "clear answers 'fault still present' even when it is clean": (SRC, [
        ("    return (_faults == 0U) ? Status::OK : Status::NOT_CLEARED;", "    return Status::NOT_CLEARED;", 1),
    ]),
}


def compile_and_run(source: Path, header_dir: Path | None, workdir: Path, name: str):
    """Returns (compiled, passed, output)."""
    exe = workdir / f"{name}.exe"
    cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion",
           "-Wno-sign-conversion", "-Werror",
           *([f"-I{header_dir}"] if header_dir is not None else []),
           f"-I{MODULE / 'include'}",
           str(source), str(HERE / "Tmc6200HostTest.cpp"), "-o", str(exe)]
    build = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=600)
    if build.returncode != 0:
        return False, False, build.stdout + build.stderr
    try:
        run = subprocess.run([str(exe)], cwd=ROOT, capture_output=True, text=True, timeout=RUN_LIMIT_S)
    except subprocess.TimeoutExpired:
        return True, False, "hangs"
    return True, run.returncode == 0, run.stdout[-2000:]


def mutate(rel: str, target: Path, changes):
    text = (MODULE / rel).read_text(encoding="utf-8")
    for old, new, occurrences in changes:
        found = text.count(old)
        if found != occurrences:
            raise AssertionError(f"{rel}: {old!r} found {found} times, expected {occurrences}")
        text = text.replace(old, new)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


class Tmc6200Io44MutationTest(unittest.TestCase):
    def test_mutants_are_detected(self):
        work = Path(tempfile.mkdtemp(prefix="nx_tmc6200_io44_mut_"))
        try:
            compiled, passed, output = compile_and_run(MODULE / SRC, None, work, "original")
            self.assertTrue(compiled and passed, f"original does not pass:\n{output}")
            survivors, invalid = [], []
            for index, (name, (rel, changes)) in enumerate(MUTATIONS.items()):
                if rel == SRC:
                    source, header_dir = work / f"Tmc6200_m{index}.cpp", None
                    mutate(rel, source, changes)
                else:
                    source, header_dir = MODULE / SRC, work / f"include_m{index}"
                    mutate(rel, header_dir / "gd" / Path(rel).name, changes)
                compiled, passed, output = compile_and_run(source, header_dir, work, f"m{index}")
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
