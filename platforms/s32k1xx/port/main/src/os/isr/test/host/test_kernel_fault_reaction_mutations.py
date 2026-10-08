# SPDX-License-Identifier: Apache-2.0
"""Meaningfulness of kernel-fault-reaction (SYS-80): broken variants of the kernel fault reactions must fail it.

Each mutation is applied to copies of the units under test - osHooks.cpp, isr_sys.cpp, the kernel configuration of the
family (FreeRtosPlatformConfig.h) and SafeSupervisor.cpp; the host test (KernelFaultReactionHostTest.cpp, unchanged)
is compiled against the copies with zig c++ exactly as the tools/test entry kernel-fault-reaction does, and run. A
mutant counts as detected if the test exits non-zero or hangs; a mutant that does not compile is a fault of this
script.
Run: .venv/Scripts/python platforms/s32k1xx/port/main/src/os/isr/test/host/test_kernel_fault_reaction_mutations.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[8]
TEST = HERE / "KernelFaultReactionHostTest.cpp"
RUN_LIMIT_S = 120

# copy name -> directory of the repository (copied whole, tests left out)
UNITS = {
    "supervisorSrc": ROOT / "ecu/safety/safeSupervisor/src",
    "isr": ROOT / "platforms/s32k1xx/port/main/src/os/isr",
    "osHooks": ROOT / "platforms/s32k1xx/port/main/src/osHooks",
    "rtosConfig": ROOT / "platforms/s32k1xx/port/freeRtosCoreConfiguration/include",
}
HOOKS = "osHooks/freertos/osHooks.cpp"
ISR = "isr/isr_sys.cpp"
CONFIG = "rtosConfig/os/FreeRtosPlatformConfig.h"
SUPERVISOR = "supervisorSrc/safeSupervisor/SafeSupervisor.cpp"

HOOK_CALL = "    nxKernelStackOverflow(static_cast<uint32_t>(uxTaskGetTaskNumber(xTask)), pcTaskName);\n"
ASSERT_MACRO = """#define configASSERT(x)                               \\
    do                                                \\
    {                                                 \\
        if ((x) == 0)                                 \\
        {                                             \\
            nxKernelAssertFailed((uint32_t)__LINE__); \\
        }                                             \\
    } while (0)"""
FAULT_LINE = """    if (::safety::safeConsoleFlush())
    {
        (void)::safety::safeConsoleWrite(line, N - 1U);
        (void)::safety::safeConsoleFlush();
    }
"""

# name: [(file, old, new, occurrences), ...]; `old` must occur exactly `occurrences` times
MUTATIONS = {
    "the stack overflow hook prints and returns (the state before SYS-80, finding R1)": [
        (HOOKS, HOOK_CALL, "    (void)xTask;\n    (void)pcTaskName;\n    printf(\"vApplicationStackOverflowHook\\r\\n\");\n", 1),
    ],
    "configASSERT is empty (the state before SYS-80, finding R2)": [
        (CONFIG, ASSERT_MACRO, "#define configASSERT(x) ((void)(x))", 1),
    ],
    "configASSERT fires on a check that holds": [
        (CONFIG, "        if ((x) == 0)                                 \\", "        if ((x) != 0)                                 \\", 1),
    ],
    "the stack overflow reaction without the SafetyShell (protected RAM read-only, interrupts on)": [
        (ISR, "    ::safety::SafetyShell const shell;\n    ::safety::SafeSupervisor::getInstance().reportStackOverflow(",
         "    ::safety::SafeSupervisor::getInstance().reportStackOverflow(", 1),
    ],
    "the assertion reaction without the SafetyShell": [
        (ISR, "    ::safety::SafetyShell const shell;\n    ::safety::SafeSupervisor::getInstance().reportRtosAssert(",
         "    ::safety::SafeSupervisor::getInstance().reportRtosAssert(", 1),
    ],
    "wrong order: the stack overflow resets before the supervisor's reaction (no S1, no line)": [
        (ISR, "    ::safety::SafeSupervisor::getInstance().reportStackOverflow(taskName, taskNumber);\n",
         "    softwareSystemReset();\n    ::safety::SafeSupervisor::getInstance().reportStackOverflow(taskName, taskNumber);\n", 1),
    ],
    "STACK_OVERFLOW returns without a reaction in handle()": [
        (SUPERVISOR, "                               _kernelFaultText};\n            break;\n",
         "                               _kernelFaultText};\n            (void)reason;\n            return;\n", 1),
    ],
    "the task name reaches the line unfiltered": [
        (SUPERVISOR, "            text[length] = ((c >= ' ') && (c <= '~')) ? c : '?';\n",
         "            text[length] = c;\n", 1),
    ],
    "STACK_OVERFLOW writes a log line (logger over possibly overwritten RAM)": [
        (SUPERVISOR, "            reason = ResetLine{\"STACK_OVERFLOW\",",
         "            Logger::error(SAFETY, \"Event: STACK_OVERFLOW\");\n            reason = ResetLine{\"STACK_OVERFLOW\",", 1),
    ],
    "the assertion reports no call site": [
        (ISR, "reportRtosAssert(callSite, line);", "reportRtosAssert(0U, line);\n    (void)callSite;", 1),
    ],
    "the assertion reports no source line": [
        (ISR, "reportRtosAssert(callSite, line);", "reportRtosAssert(callSite, 0U);\n    (void)line;", 1),
    ],
    "hard fault path: the line before the first flush (a dead transmitter still gets it)": [
        (ISR, FAULT_LINE, "    (void)::safety::safeConsoleFlush();\n    (void)::safety::safeConsoleWrite(line, N - 1U);\n"
                          "    (void)::safety::safeConsoleFlush();\n", 1),
    ],
    "hard fault path: no flush after the line (the reset cuts its last character)": [
        (ISR, "        (void)::safety::safeConsoleWrite(line, N - 1U);\n        (void)::safety::safeConsoleFlush();\n",
         "        (void)::safety::safeConsoleWrite(line, N - 1U);\n", 1),
    ],
    "hard fault path: reset before the line": [
        (ISR, "    writeFaultLine(\"\\r\\nHARD FAULT RESET: outputs off, dump saved\\r\\n\");\n",
         "    softwareSystemReset();\n    writeFaultLine(\"\\r\\nHARD FAULT RESET: outputs off, dump saved\\r\\n\");\n", 1),
    ],
    "watchdog dump: line of a hard fault (SYS-169)": [
        (ISR, "    if (::hardfault::getDumpException(::hardfault::getDumpRegion()) == (static_cast<uint32_t>(WDOG_EWM_IRQn) + 16U))\n",
         "    if (false)\n", 1),
    ],
    "hard fault dump: line of the watchdog (SYS-169)": [
        (ISR, "    if (::hardfault::getDumpException(::hardfault::getDumpRegion()) == (static_cast<uint32_t>(WDOG_EWM_IRQn) + 16U))\n",
         "    if (true)\n", 1),
    ],
    "watchdog vector without the offset of 16 (SYS-169)": [
        (ISR, "(static_cast<uint32_t>(WDOG_EWM_IRQn) + 16U))\n    {\n        writeFaultLine(\"\\r\\nWATCHDOG",
         "(static_cast<uint32_t>(WDOG_EWM_IRQn) + 0U))\n    {\n        writeFaultLine(\"\\r\\nWATCHDOG", 1),
    ],
    "hard fault path: no reset after the line": [
        (ISR, "    writeFaultLine(\"\\r\\nHARD FAULT RESET: bus fault, no dump\\r\\n\");\n    softwareSystemReset();\n",
         "    writeFaultLine(\"\\r\\nHARD FAULT RESET: bus fault, no dump\\r\\n\");\n", 1),
    ],
}


def copy_tree(tree: Path):
    for name, source in UNITS.items():
        shutil.copytree(source, tree / name, ignore=shutil.ignore_patterns("test", "*.py", "__pycache__"))
    # line ends as git stores them (the working tree may have CR LF), so the mutations match
    for path in tree.rglob("*"):
        if path.is_file() and path.suffix in {".cpp", ".h"}:
            path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))


def compile_and_run(tree: Path, workdir: Path, name: str):
    """Returns (compiled, passed, output)."""
    exe = workdir / f"{name}.exe"
    cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion",
           "-Wno-sign-conversion", "-Werror", "-DPLATFORM_SUPPORT_IO", "-DPLATFORM_SUPPORT_WATCHDOG", "-DUNIT_TEST",
           "-DSUPPORT_FREERTOS",
           f"-I{HERE / 'kernelFaultStubs'}",
           f"-I{ROOT / 'ecu/safety/safeSupervisor/include'}",
           f"-I{ROOT / 'ecu/safety/safeSupervisor/test/host/stubs'}",
           f"-I{tree / 'supervisorSrc'}",
           f"-I{tree / 'isr'}",
           f"-I{tree / 'osHooks'}",
           f"-I{tree / 'rtosConfig'}",
           f"-isystem{ROOT / 'external/openbsw/libs/safety/safeMonitor/include'}",
           str(TEST), "-o", str(exe)]
    build = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=600)
    if build.returncode != 0:
        return False, False, build.stdout + build.stderr
    try:
        run = subprocess.run([str(exe)], cwd=ROOT, capture_output=True, text=True, errors="replace",
                             timeout=RUN_LIMIT_S)
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
        path.write_text(text.replace(old, new), encoding="utf-8", newline="\n")


class KernelFaultReactionMutationTest(unittest.TestCase):
    def test_mutants_are_detected(self):
        work = Path(tempfile.mkdtemp(prefix="nx_sys80_mut_"))
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
