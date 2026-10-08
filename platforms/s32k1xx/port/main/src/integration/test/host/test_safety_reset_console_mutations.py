# SPDX-License-Identifier: Apache-2.0
"""Meaningfulness of safety-reset-console (SYS-59): broken variants of the SAFETY RESET line must fail it.

Each mutation is applied to copies of ecu/safety/safeSupervisor/src, platforms/s32k1xx/port/main/src/integration and
(SYS-137) platforms/s32k1xx/port/main/src/safeConsole;
the host test (SafetyResetConsoleHostTest.cpp, copied along, unchanged) is compiled against the copies with zig c++
exactly as the tools/test entry safety-reset-console does, and run. A mutant counts as detected if the test exits
non-zero or hangs; a mutant that does not compile is a fault of this script.
Run: .venv/Scripts/python platforms/s32k1xx/port/main/src/integration/test/host/test_safety_reset_console_mutations.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[7]
SUPERVISOR = ROOT / "ecu/safety/safeSupervisor"
INTEGRATION = ROOT / "platforms/s32k1xx/port/main/src/integration"
SAFE_CONSOLE = ROOT / "platforms/s32k1xx/port/main/src/safeConsole"
RUN_LIMIT_S = 120

SOURCE = "supervisor/src/safeSupervisor/SafeSupervisor.cpp"
FLUSH = "integration/SafeConsoleFlush.h"
RECORD = "safeConsole/SafeConsoleRecord.h"  # SYS-137

# SYS-98: the sequence S1 - mark - flush - line - flush - reset lives in resetWithLine(), shared by handle() and the
# fault paths (kernel faults, ETL_ASSERT, ECC from a fault interrupt)
MARK = """    if (record != nullptr)
    {
        resetloop::markSafetyReset(*record, resetloop::CAUSE_EVENT | ::etl::to_underlying(event));
    }
"""
# SYS-95: the line needs a confirmed safe state (SYS-138 read-back) and an idle transmitter
LINE = """    if (confirmed && NX_SAFE_SUPERVISOR_RESET_FLUSH())
    {
        writeResetLine(reason);
        (void)NX_SAFE_SUPERVISOR_RESET_FLUSH();
    }
"""
SAFE_STATE = """#ifdef PLATFORM_SUPPORT_IO
    // SYS-06: the power outputs go off before the reset"""
# the line moved in front of the safe state: `confirmed` is declared after the safe state, so the moved copy
# keeps the flush condition only (and the old place keeps `confirmed` used, -Werror)
EARLY_LINE = LINE.replace("confirmed && ", "")
RESET = """    // reset the MCU
    softwareSystemReset();
}
"""

# name: [(file, old, new, occurrences), ...]; `old` must occur exactly `occurrences` times
MUTATIONS = {
    "the line is missing (no event writes it)": [
        (SOURCE, "    NX_SAFE_SUPERVISOR_RESET_CONSOLE(line, length);\n", "    (void)line;\n    (void)length;\n", 1),
    ],
    "the line only for SAFETY_CYCLE_PERIOD_VIOLATION (the state of SYS-52)": [
        (SOURCE, "    char line[SAFE_CONSOLE_LINE_SIZE];\n    size_t length = resetloop::detail::append(line, sizeof(line), 0U, \"\\r\\nSAFETY RESET (\");\n",
         "    if (reason.detail != ResetDetail::PERIOD)\n    {\n        return;\n    }\n"
         "    char line[SAFE_CONSOLE_LINE_SIZE];\n    size_t length = resetloop::detail::append(line, sizeof(line), 0U, \"\\r\\nSAFETY RESET (\");\n", 1),
    ],
    "the flush is unbounded": [
        (FLUSH, "for (uint32_t polls = 0U; polls < maxPolls; ++polls)",
         "for (uint32_t polls = 0U; polls < maxPolls; polls = (polls + 1U) % maxPolls)", 1),
    ],
    "the flush does not wait at all": [
        (FLUSH, "        if (port.transmissionComplete())\n", "        if (port.transmissionComplete() || (polls == 0U))\n", 1),
    ],
    "the line comes before the safe state (order S1 -> line swapped)": [
        (SOURCE, LINE, "    (void)confirmed;\n", 1),
        (SOURCE, SAFE_STATE, EARLY_LINE + SAFE_STATE, 1),
    ],
    "the reset comes before the line (order line -> reset swapped)": [
        (SOURCE, LINE, "    softwareSystemReset();\n" + LINE, 1),
    ],
    "no flush after the line (its last character is cut)": [
        (SOURCE, "        (void)NX_SAFE_SUPERVISOR_RESET_FLUSH();\n", "", 1),
    ],
    "no flush before the line (a dead console costs two time-outs)": [
        (SOURCE, "    if (confirmed && NX_SAFE_SUPERVISOR_RESET_FLUSH())\n", "    if (confirmed)\n", 1),
    ],
    "the result of the flush is ignored (a dead console gets the line anyway)": [
        (SOURCE, "    if (confirmed && NX_SAFE_SUPERVISOR_RESET_FLUSH())\n",
         "    if (confirmed && (NX_SAFE_SUPERVISOR_RESET_FLUSH() || (reason.event != nullptr)))\n", 1),
    ],
    "the mark of the reset-loop record after the line": [
        (SOURCE, MARK, "", 1),
        (SOURCE, RESET, MARK + RESET, 1),
    ],
    "no CR LF in front of the line": [
        (SOURCE, "\"\\r\\nSAFETY RESET (\"", "\"SAFETY RESET (\"", 1),
    ],
    "fields in decimal instead of hex": [
        (SOURCE, "            reason = ResetLine{\"DEADMAN_CONFIGURATION_ERROR\", \"fields\", ResetDetail::HEX, fields};",
         "            reason = ResetLine{\"DEADMAN_CONFIGURATION_ERROR\", \"fields\", ResetDetail::DECIMAL, fields};", 1),
    ],
    # SYS-137: the record of the safe console that the next start reports
    "SYS-137: a flush time-out counted as an idle flush": [
        (RECORD, "        idle ? saturatedIncrement(ok) : ok,\n        idle ? timeouts : saturatedIncrement(timeouts));",
         "        (static_cast<void>(idle), saturatedIncrement(ok)),\n        timeouts);", 1),
    ],
    "SYS-137: the take does not empty the record (the next start reports it again)": [
        (RECORD, "    clear(record);\n    return snapshot;", "    return snapshot;", 1),
    ],
    "SYS-137: the requested bytes recorded as written": [
        (RECORD, "        requested,\n        written,\n        intact ? record.flushesOk : 0U,",
         "        requested,\n        requested + (written * 0U),\n        intact ? record.flushesOk : 0U,", 1),
    ],
    "SYS-137: the check does not cover the written bytes (an overwritten record passes as intact)": [
        (RECORD, " ^ (written << 16U)", " ^ (written * 0U)", 1),
    ],
    "SYS-137: garbage without the magic reported as a record": [
        (RECORD, "        snapshot.present = (record.magic == RECORD_MAGIC);", "        snapshot.present = true;", 1),
    ],
}


def copy_tree(tree: Path):
    shutil.copytree(SUPERVISOR / "src", tree / "supervisor/src")
    shutil.copytree(SUPERVISOR / "include", tree / "supervisor/include")
    # the test itself comes along; compile_and_run() points its include of SafeSupervisor.cpp at the copy
    shutil.copytree(INTEGRATION, tree / "integration", ignore=shutil.ignore_patterns("*.py", "__pycache__"))
    shutil.copytree(SAFE_CONSOLE, tree / "safeConsole", ignore=shutil.ignore_patterns("test"))


def compile_and_run(tree: Path, workdir: Path, name: str):
    """Returns (compiled, passed, output). `tree` holds the supervisor and integration copies."""
    exe = workdir / f"{name}.exe"
    test = tree / "integration/test/host/SafetyResetConsoleHostTest.cpp"
    text = test.read_text(encoding="utf-8")
    original = "#include \"../../../../../../../../ecu/safety/safeSupervisor/src/safeSupervisor/SafeSupervisor.cpp\""
    if text.count(original) != 1:
        raise AssertionError("the include of SafeSupervisor.cpp in the test changed")
    copied = (tree / SOURCE).as_posix()
    test.write_text(text.replace(original, f"#include \"{copied}\""), encoding="utf-8")
    cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion",
           "-Wno-sign-conversion", "-Werror", "-DPLATFORM_SUPPORT_IO", "-DPLATFORM_SUPPORT_WATCHDOG",
           f"-I{tree / 'supervisor/include'}",
           f"-I{ROOT / 'ecu/safety/safeSupervisor/test/host/stubs'}",
           f"-I{tree / 'safeConsole'}",
           f"-I{tree / 'integration'}",
           f"-isystem{ROOT / 'external/openbsw/libs/safety/safeMonitor/include'}",
           str(test), "-o", str(exe)]
    build = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=600)
    if build.returncode != 0:
        return False, False, build.stdout + build.stderr
    try:
        run = subprocess.run([str(exe)], cwd=ROOT, capture_output=True, text=True, timeout=RUN_LIMIT_S)
    except subprocess.TimeoutExpired:
        return True, False, "hangs"
    return True, run.returncode == 0, run.stdout[-2000:]


def apply(tree: Path, changes):
    for rel, old, new, occurrences in changes:
        path = tree / rel
        text = path.read_text(encoding="utf-8")
        found = text.count(old)
        if found != occurrences:
            raise AssertionError(f"{rel}: {old!r} found {found} times, expected {occurrences}")
        path.write_text(text.replace(old, new), encoding="utf-8")


class SafetyResetConsoleMutationTest(unittest.TestCase):
    def test_mutants_are_detected(self):
        work = Path(tempfile.mkdtemp(prefix="nx_sys59_mut_"))
        try:
            original = work / "original"
            copy_tree(original)
            compiled, passed, output = compile_and_run(original, work, "original")
            self.assertTrue(compiled and passed, f"original does not pass:\n{output}")
            survivors, invalid = [], []
            for index, (name, changes) in enumerate(MUTATIONS.items()):
                tree = work / f"m{index}"
                copy_tree(tree)
                apply(tree, changes)
                compiled, passed, output = compile_and_run(tree, work, f"m{index}")
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
