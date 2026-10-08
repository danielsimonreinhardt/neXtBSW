# SPDX-License-Identifier: Apache-2.0
"""SYS-80: the kernel configuration of the S32K1 family switches on only what the code uses, and its fault hooks react.

Findings R2/R4 of doc/safety/rtos-comparison.md section 1.4: configASSERT was empty, and far more kernel switches were on
than the code uses (every one enlarges the kernel surface a qualification per ISO 26262-8 clause 12 has to cover).

Static part (ConfigTest, no build needed) - on the effective configuration, OpenBSW's
external/openbsw/libs/bsw/asyncFreeRtos/freeRtosConfiguration/FreeRTOSConfig.h followed by the family's
platforms/s32k1xx/port/freeRtosCoreConfiguration/include/os/FreeRtosPlatformConfig.h (with its #undef):
1. every switch that enables kernel code (an INCLUDE_* at 1, a feature switch not 0) has a reviewed entry in SWITCHES
   with the API that uses it, and that API is really called somewhere outside the kernel (ecu, platforms, libs, the
   OpenBSW libraries and family platform; not the build tests with their own FreeRTOSConfig.h);
2. every config*/INCLUDE_* name the family file defines is read by the kernel, its ports or other code - a name
   nobody reads (INCLUDE_pcTaskGetTaskName, configUSE_FPU, ... before SYS-80) is dead configuration;
3. configASSERT is not empty: a failed check calls nxKernelAssertFailed(); configCHECK_FOR_STACK_OVERFLOW is 2.
Built-in faults (the rules applied to modified family files) must be found: an INCLUDE_vTaskDelete, a dead name,
recursive mutexes or the queue registry switched on again, an empty configASSERT, stack check 1.

Image part (DemoImageTest, after target-demo; NX_APP_ELF or the demo build of tools/test/build_dirs.py):
4. vApplicationStackOverflowHook calls nxKernelStackOverflow() and no printf; nxKernelStackOverflow and
   nxKernelAssertFailed hand over to SafeSupervisor::reportStackOverflow()/reportRtosAssert(); the kernel calls
   nxKernelAssertFailed() from its checking functions (task creation, scheduler start, notifications, the interrupt
   priority check of the port);
5. HardFault_Handler_Final, BusFault_Handler and UsageFault_Handler (SYS-59 rest) call safeConsoleFlush() twice and
   safeConsoleWrite() once per line they can write, then softwareSystemReset(), and nothing else - no printf; the
   order is shown by the host test kernel-fault-reaction. SYS-169: nxHardFaultFinal has two lines (WATCHDOG RESET
   for the pre-reset interrupt of the watchdog, HARD FAULT RESET otherwise), of which one is written, and reads the
   exception number of the dump through the leaf functions getDumpRegion/getDumpException; SYS-97: the C part of HardFault_Handler_Final is nxHardFaultFinal, the overflow line of the
   interrupt stack nxIsrStackOverflowFinal - both under this rule; HardFault_Handler_Final itself is a naked check of
   the MSP that uses no stack and branches to exactly these two;
6. the API functions whose switches SYS-80 removed are not in the image, nor the queue registry.

Run: .venv/Scripts/python -m unittest platforms/s32k1xx/port/main/src/osHooks/test/test_freertos_config.py
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[6]
OPENBSW_CONFIG = REPO / "external/openbsw/libs/bsw/asyncFreeRtos/freeRtosConfiguration/FreeRTOSConfig.h"
FAMILY_CONFIG = REPO / "platforms/s32k1xx/port/freeRtosCoreConfiguration/include/os/FreeRtosPlatformConfig.h"

# The kernel and the ports of the family: what reads a configuration name
KERNEL_SOURCES = (
    REPO / "external/openbsw/libs/3rdparty/freeRtos/src",
    REPO / "external/openbsw/libs/3rdparty/freeRtos/include",
    REPO / "external/openbsw/platforms/s32k1xx/3rdparty/freertos_cm4_sysTick",
    REPO / "platforms/s32k1xx/3rdparty/freertos_cm0_sysTick",
)
# Code that may use the kernel API (a user of a switch); kernels, mocks, tests and build tests with their own
# FreeRTOSConfig.h are no users
USER_ROOTS = (
    REPO / "ecu",
    REPO / "platforms",
    REPO / "libs",
    REPO / "external/openbsw/libs/bsw",
    REPO / "external/openbsw/platforms/s32k1xx",
)
SKIP_PARTS = {"test", "tests", "mock", "3rdparty", "generated", "build", "__pycache__"}
SUFFIXES = {".c", ".cpp", ".h", ".hpp"}

# Every switch that enables kernel code, with the API calls that use it and who makes them (reviewed SYS-80).
# The API pattern must match a call outside the kernel; otherwise the switch is unused.
SWITCHES: dict[str, tuple[str, str]] = {
    # set by OpenBSW (asyncFreeRtos FreeRTOSConfig.h), used by its adapter
    "INCLUDE_xTaskGetCurrentTaskHandle": (r"\bxTaskGetCurrentTaskHandle\s*\(", "FreeRtosAdapter::getCurrentTaskContext"),
    "INCLUDE_xTaskGetIdleTaskHandle": (r"\bxTaskGetIdleTaskHandle\s*\(", "FreeRtosAdapter::runningHook"),
    "INCLUDE_uxTaskGetStackHighWaterMark": (r"\buxTaskGetStackHighWaterMark\s*\(",
                                            "TaskContext::getUnusedStackSize (console stats stack)"),
    "INCLUDE_vTaskSuspend": (r"\bvTaskSuspend\s*\(|\bvTaskResume\s*\(",
                             "async::suspend/resume (TaskContext.h); also makes portMAX_DELAY waits unbounded"),
    "INCLUDE_xTimerPendFunctionCall": (r"\bxEventGroupSetBitsFromISR\s*\(|\bxTimerPendFunctionCall(FromISR)?\s*\(",
                                       "FutureSupport::notify from an interrupt (OpenBSW storage), via the timer task"),
    "configUSE_TIMERS": (r"\bxEventGroupSetBitsFromISR\s*\(|\bxTimerCreate(Static)?\s*\(|\bxTimerPendFunctionCall",
                         "FutureSupport::notify from an interrupt; finding R3 of doc/safety/rtos-comparison.md: no "
                         "image of neXtBSW links it - switching the timer task off needs ecu/application (SYS-80 report)"),
    "configUSE_TRACE_FACILITY": (r"\buxTaskGetTaskNumber\s*\(|\bvTaskSetTaskNumber\s*\(",
                                 "context number of the async layer (FreeRTOSConfig.h traceTASK_CREATE, adapter)"),
    "configUSE_IDLE_HOOK": (r"\bvApplicationIdleHook\s*\(", "async idle context (asyncFreeRtos Async.cpp)"),
    "configCHECK_FOR_STACK_OVERFLOW": (r"\bvApplicationStackOverflowHook\s*\(",
                                       "SYS-80 R1: hook of the family port, reaction nxKernelStackOverflow"),
    "configINCLUDE_FREERTOS_TASK_C_ADDITIONS_H": (r"\bFREERTOS_TASKS_C_ADDITIONS_INIT\b|\basyncInitialized\s*\(",
                                                  "OpenBSW code in tasks.c (asyncInitialized at scheduler start)"),
    "configSUPPORT_STATIC_ALLOCATION": (r"\bxTaskCreateStatic\s*\(", "one static task per async context"),
    "configUSE_PREEMPTION": (r"\bxTaskCreateStatic\s*\(", "preemptive scheduling of the async contexts"),
    # set by the family
    "INCLUDE_xTaskGetSchedulerState": (r"\bxTaskGetSchedulerState\s*\(",
                                       "FreeRtosAdcLock (ADC lock_policy rtos_mutex of a project)"),
    "configUSE_MUTEXES": (r"\bxSemaphoreCreateMutex(Static)?\s*\(", "FreeRtosAdcLock (ADC lock_policy rtos_mutex)"),
}
# Feature switches: any value other than 0 enables kernel code (the rest are sizes or plain values)
FEATURES = {
    "configUSE_MUTEXES", "configUSE_RECURSIVE_MUTEXES", "configUSE_COUNTING_SEMAPHORES", "configQUEUE_REGISTRY_SIZE",
    "configGENERATE_RUN_TIME_STATS", "configUSE_TIMERS", "configUSE_TRACE_FACILITY", "configUSE_IDLE_HOOK",
    "configUSE_TICK_HOOK", "configCHECK_FOR_STACK_OVERFLOW", "configUSE_MALLOC_FAILED_HOOK",
    "configUSE_APPLICATION_TASK_TAG", "configNUM_THREAD_LOCAL_STORAGE_POINTERS", "configUSE_CO_ROUTINES",
    "configSUPPORT_DYNAMIC_ALLOCATION", "configSUPPORT_STATIC_ALLOCATION", "configUSE_TICKLESS_IDLE",
    "configINCLUDE_FREERTOS_TASK_C_ADDITIONS_H", "configUSE_PREEMPTION", "configRECORD_STACK_HIGH_ADDRESS",
    "configIDLE_SHOULD_YIELD", "configUSE_NEWLIB_REENTRANT", "configUSE_QUEUE_SETS", "configUSE_TASK_NOTIFICATIONS",
    "configUSE_STATS_FORMATTING_FUNCTIONS", "configUSE_DAEMON_TASK_STARTUP_HOOK", "configUSE_SB_COMPLETED_CALLBACK",
}

DEFINE = re.compile(r"^\s*#\s*define\s+((?:config|INCLUDE_)\w+)(?:\([^)]*\))?(?:\s+(.*?))?\s*$")
UNDEF = re.compile(r"^\s*#\s*undef\s+((?:config|INCLUDE_)\w+)\s*$")
COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def logical_lines(text: str) -> list[str]:
    """Comments removed, continuation lines joined."""
    text = COMMENT.sub(" ", text).replace("\\\r\n", " ").replace("\\\n", " ")
    return text.splitlines()


def effective_config(openbsw_text: str, family_text: str) -> dict[str, str]:
    """name -> value of the configuration the kernel sees: OpenBSW's file, then the family's (which it includes last).
    Conditional blocks are not evaluated - a name defined twice keeps its last value, which is good enough for the
    switches (the family file defines each of them once)."""
    config: dict[str, str] = {}
    for text in (openbsw_text, family_text):
        for line in logical_lines(text):
            m = DEFINE.match(line)
            if m:
                config[m.group(1)] = (m.group(2) or "").strip()
                continue
            m = UNDEF.match(line)
            if m:
                config.pop(m.group(1), None)
    return config


def family_names(family_text: str) -> set[str]:
    return {m.group(1) for line in logical_lines(family_text) for m in [DEFINE.match(line)] if m}


def is_on(name: str, value: str) -> bool:
    """True if the switch enables kernel code."""
    if not (name.startswith("INCLUDE_") or name in FEATURES):
        return False
    v = value.strip().strip("()").rstrip("uUlL")
    return v not in ("0", "")


def _files(roots, skip_parts=SKIP_PARTS):
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.suffix not in SUFFIXES or not path.is_file():
                continue
            if skip_parts.intersection(path.relative_to(root).parts[:-1]):
                continue
            yield path


_CACHE: dict[str, str] = {}


def user_code() -> str:
    """All code that may call the kernel API, comments removed, one string (cached)."""
    if "users" not in _CACHE:
        parts = []
        for path in _files(USER_ROOTS):
            if path.name in ("FreeRTOSConfig.h", "FreeRtosPlatformConfig.h"):
                continue
            parts.append(COMMENT.sub(" ", _text(path)))
        # OpenBSW's async configuration hooks count as users (trace macros, tasks.c additions)
        parts.append(COMMENT.sub(" ", _text(OPENBSW_CONFIG)))
        _CACHE["users"] = "\n".join(parts)
    return _CACHE["users"]


def reader_code() -> str:
    """Kernel, ports and every other code: where a configuration name may be read (cached)."""
    if "readers" not in _CACHE:
        parts = [_text(p) for p in _files(KERNEL_SOURCES, skip_parts={"test", "mock"})]
        parts.append(user_code())
        _CACHE["readers"] = "\n".join(parts)
    return _CACHE["readers"]


def config_findings(openbsw_text: str, family_text: str) -> list[str]:
    config = effective_config(openbsw_text, family_text)
    users = user_code()
    readers = reader_code()
    found = []
    for name, value in sorted(config.items()):
        if not is_on(name, value):
            continue
        entry = SWITCHES.get(name)
        if entry is None:
            found.append(f"{name} = {value} enables kernel code, but no reviewed user is listed (SWITCHES)")
            continue
        if not re.search(entry[0], users):
            found.append(f"{name} = {value}: nothing outside the kernel calls {entry[0]} - unused switch")
    for name in sorted(family_names(family_text)):
        own = re.compile(r"\b" + re.escape(name) + r"\b")
        if not own.search(readers):
            found.append(f"{name} is defined by the family, but neither the kernel nor any code reads it")
    assert_value = config.get("configASSERT", "")
    if "nxKernelAssertFailed" not in assert_value:
        found.append(f"configASSERT does not call nxKernelAssertFailed() (finding R2): '{assert_value}'")
    if config.get("configCHECK_FOR_STACK_OVERFLOW", "").strip("() ") != "2":
        found.append("configCHECK_FOR_STACK_OVERFLOW is not 2 (pattern check of the stack end, finding R1)")
    return found


class ConfigTest(unittest.TestCase):
    def setUp(self):
        if not OPENBSW_CONFIG.is_file():
            self.skipTest("external/openbsw missing (git submodule update --init external/openbsw)")
        self.openbsw = _text(OPENBSW_CONFIG)
        self.family = _text(FAMILY_CONFIG)

    def test_only_used_switches_and_live_names(self):
        self.assertEqual(config_findings(self.openbsw, self.family), [])

    def test_every_listed_switch_is_still_on(self):
        """A switch that went off (or away) leaves a stale entry in SWITCHES - it would allow it again silently."""
        config = effective_config(self.openbsw, self.family)
        stale = [n for n in SWITCHES if not is_on(n, config.get(n, "0"))]
        self.assertEqual(stale, [])

    # --- the rules find what they are for (mutants of the family file)

    def _mutant(self, old: str, new: str) -> list[str]:
        family = self.family.replace("\r\n", "\n")
        self.assertIn(old, family)
        return config_findings(self.openbsw, family.replace(old, new, 1))

    def test_include_switch_without_user_is_found(self):
        found = self._mutant("#define INCLUDE_xTaskGetSchedulerState 1",
                             "#define INCLUDE_xTaskGetSchedulerState 1\n#define INCLUDE_vTaskDelete 1")
        self.assertTrue(any("INCLUDE_vTaskDelete" in f for f in found), found)

    def test_dead_name_is_found(self):
        found = self._mutant("#define INCLUDE_xTaskGetSchedulerState 1",
                             "#define INCLUDE_xTaskGetSchedulerState 1\n#define INCLUDE_pcTaskGetTaskName 1")
        self.assertTrue(any("INCLUDE_pcTaskGetTaskName is defined by the family" in f for f in found), found)
        found = self._mutant("#define configUSE_MALLOC_FAILED_HOOK            0",
                             "#define configUSE_MALLOC_FAILED_HOOK            0\n#define configUSE_FPU 0")
        self.assertTrue(any("configUSE_FPU" in f for f in found), found)

    def test_feature_switch_without_user_is_found(self):
        found = self._mutant("#define configUSE_RECURSIVE_MUTEXES             0",
                             "#define configUSE_RECURSIVE_MUTEXES             1")
        self.assertTrue(any("configUSE_RECURSIVE_MUTEXES" in f for f in found), found)
        found = self._mutant("#define configQUEUE_REGISTRY_SIZE 0", "#define configQUEUE_REGISTRY_SIZE 30")
        self.assertTrue(any("configQUEUE_REGISTRY_SIZE" in f for f in found), found)
        found = self._mutant("#define configGENERATE_RUN_TIME_STATS (0)", "#define configGENERATE_RUN_TIME_STATS (1)")
        self.assertTrue(any("configGENERATE_RUN_TIME_STATS" in f for f in found), found)

    def test_empty_assert_is_found(self):
        start = self.family.replace("\r\n", "\n").index("#define configASSERT(x)")
        end = self.family.replace("\r\n", "\n").index("} while (0)", start) + len("} while (0)")
        macro = self.family.replace("\r\n", "\n")[start:end]
        found = self._mutant(macro, "#define DEV_ASSERT(x)\n#define configASSERT(x) DEV_ASSERT(x)")
        self.assertTrue(any("configASSERT does not call" in f for f in found), found)

    def test_weaker_stack_check_is_found(self):
        found = self._mutant("#define configCHECK_FOR_STACK_OVERFLOW          2",
                             "#define configCHECK_FOR_STACK_OVERFLOW          1")
        self.assertTrue(any("configCHECK_FOR_STACK_OVERFLOW is not 2" in f for f in found), found)


# ---------------------------------------------------------------------------------------------------- image part

GCC_PATH = os.pathsep.join(p for p in (os.environ.get("NX_ARM_GCC_BIN"), os.environ.get("PATH")) if p)
ARM_OBJDUMP = shutil.which("arm-none-eabi-objdump", path=GCC_PATH)
ARM_NM = shutil.which("arm-none-eabi-nm", path=GCC_PATH)

HOOK = "vApplicationStackOverflowHook"
STACK_REACTION = "nxKernelStackOverflow"
ASSERT_REACTION = "nxKernelAssertFailed"
REPORT_STACK = "safety::SafeSupervisor::reportStackOverflow(char const*, unsigned long)"
REPORT_ASSERT = "safety::SafeSupervisor::reportRtosAssert(unsigned long, unsigned long)"
FLUSH = "safety::safeConsoleFlush()"
WRITE = "safety::safeConsoleWrite(char const*, unsigned long)"
RESET = "softwareSystemReset"
# SYS-97: HardFault_Handler_Final is a naked check of the interrupt stack now; its C part (the line and the reset) is
# nxHardFaultFinal, and nxIsrStackOverflowFinal writes the line after an overflow of the interrupt stack into its guard
FAULT_HANDLERS = ("nxHardFaultFinal", "BusFault_Handler", "UsageFault_Handler", "nxIsrStackOverflowFinal")
# SYS-169: lines per handler (each written as flush, write, flush) and the dump readers nxHardFaultFinal may call to
# choose its line - leaf functions on the no-init dump (no call, so no printf, kernel or logger behind them)
FAULT_LINES = {"nxHardFaultFinal": 2}
DUMP_READERS = ("hardfault::getDumpRegion()", "hardfault::getDumpException(unsigned long const*)")
FAULT_HANDLER_READERS = {"nxHardFaultFinal": DUMP_READERS}
HARD_FAULT_FINAL = "HardFault_Handler_Final"
HARD_FAULT_FINAL_TARGETS = ("nxHardFaultFinal", "nxIsrStackOverflowFinal")
PRINTF = re.compile(r"printf|puts|putchar|__outchar|charIO|Logger::|logger::")
# kernel functions that must call the assert hook (one of each kind of check)
ASSERTING = ("xTaskCreateStatic", "xPortStartScheduler", "vPortValidateInterruptPriority", "xTaskGenericNotify")
# API functions whose switches SYS-80 removed, and the queue registry
REMOVED = ("vTaskDelete", "eTaskGetState", "vTaskPrioritySet", "uxTaskPriorityGet", "xQueueGetMutexHolder",
           "vTaskDelay", "xTaskDelayUntil", "xTaskResumeFromISR", "xQueueRegistry", "vQueueAddToRegistry",
           "xQueueCreateCountingSemaphoreStatic", "xQueueTakeMutexRecursive")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


# the disassembly reader of SYS-39/SYS-59 (functions, direct calls and tail calls)
HOLD_ELF = _load("nx_hold_console_elf", REPO / "ecu/safety/safeSupervisor/test/test_hold_console_elf.py")


def application_elf() -> Path | None:
    env = os.environ.get("NX_APP_ELF")
    if env:
        return Path(env)
    build = _load("nx_build_dirs", REPO / "tools/test/build_dirs.py").demo_build_dir(REPO)
    elf = Path(build) / "ecu/application/RelWithDebInfo/app.ecu.elf"
    return elf if elf.is_file() else None


def calls_of(table, name: str) -> list[str]:
    body = table.get(name)
    return [] if body is None else [f for _, f in HOLD_ELF.callees(body, name)]


def image_findings(table, symbols: set[str]) -> list[str]:
    found = []
    hook = calls_of(table, HOOK)
    if HOOK not in table:
        found.append(f"{HOOK} not in the image")
    elif STACK_REACTION not in hook or any(PRINTF.search(f) for f in hook):
        found.append(f"{HOOK} calls {hook}, expected {STACK_REACTION} and no printf (finding R1)")
    for reaction, report in ((STACK_REACTION, REPORT_STACK), (ASSERT_REACTION, REPORT_ASSERT)):
        calls = calls_of(table, reaction)
        if report not in calls or RESET not in calls:
            found.append(f"{reaction} calls {calls}, expected {report} and {RESET}")
    for function in ASSERTING:
        matches = [n for n in table if n == function or n.startswith(function + ".")]
        if not any(ASSERT_REACTION in calls_of(table, n) for n in matches):
            found.append(f"{function} does not call {ASSERT_REACTION} - kernel checks compiled out (finding R2)")
    for handler in FAULT_HANDLERS:
        calls = calls_of(table, handler)
        lines = FAULT_LINES.get(handler, 1)
        readers = FAULT_HANDLER_READERS.get(handler, ())
        if (calls.count(FLUSH) != 2 * lines or calls.count(WRITE) != lines or RESET not in calls
                or any(PRINTF.search(f) for f in calls)
                or any(f not in (FLUSH, WRITE, RESET) + readers for f in calls)):
            found.append(f"{handler} calls {calls}, expected {2 * lines} x safeConsoleFlush, {lines} x "
                         f"safeConsoleWrite, softwareSystemReset{', ' + ', '.join(readers) if readers else ''} "
                         "and nothing else, no printf")
        for reader in readers:
            if reader in calls and calls_of(table, reader):
                found.append(f"{reader} (called by {handler}) calls {calls_of(table, reader)}, expected a leaf")
    # SYS-97: the entry customHardFaultHandler branches to uses no stack (no push, no frame) and goes on to the C part
    # or, after an overflow of the interrupt stack, to its own line
    body = table.get(HARD_FAULT_FINAL, [])
    targets = calls_of(table, HARD_FAULT_FINAL)
    if (not body or any(op.startswith(("push", "vpush")) or (op.startswith(("stmdb", "sub", "str")) and "sp" in ops)
                        for _, op, ops in body)
            or any(t not in targets for t in HARD_FAULT_FINAL_TARGETS)):
        found.append(f"{HARD_FAULT_FINAL} uses the stack or does not branch to {HARD_FAULT_FINAL_TARGETS} "
                     f"(branches: {targets})")
    for name in REMOVED:
        if name in symbols:
            found.append(f"{name} is in the image although its switch was removed (finding R4)")
    return found


@unittest.skipUnless(ARM_OBJDUMP and ARM_NM, "arm-none-eabi-objdump/nm not found (PATH or NX_ARM_GCC_BIN)")
class DemoImageTest(unittest.TestCase):
    table: dict = {}
    symbols: set[str] = set()

    @classmethod
    def setUpClass(cls):
        elf = application_elf()
        if elf is None or not elf.is_file():
            return
        out = subprocess.run([ARM_OBJDUMP, "-d", "-C", str(elf)], capture_output=True, text=True, check=True).stdout
        cls.table = HOLD_ELF.functions(out)
        nm = subprocess.run([ARM_NM, "-C", str(elf)], capture_output=True, text=True, check=True).stdout
        cls.symbols = {p[2] for p in (line.split(maxsplit=2) for line in nm.splitlines()) if len(p) == 3}

    def setUp(self):
        if not self.table:
            self.skipTest("no application ELF (NX_APP_ELF or a demo build, tools/test/build_dirs.py)")

    def test_kernel_fault_hooks_and_fault_handlers(self):
        self.assertEqual(image_findings(self.table, self.symbols), [])

    def test_built_in_faults_are_found(self):
        """The rules on modified tables: the hook of before SYS-80, asserts compiled out, printf in the fault path."""
        table = dict(self.table)
        table[HOOK] = [(0x100, "bl", "200 <printf>")]
        self.assertTrue(any(HOOK in f for f in image_findings(table, self.symbols)))
        table = {n: [i for i in b if ASSERT_REACTION not in i[2]] for n, b in self.table.items()}
        self.assertTrue(any("kernel checks compiled out" in f for f in image_findings(table, self.symbols)))
        table = dict(self.table)
        table["BusFault_Handler"] = [(0x100, "bl", "200 <printf>"), (0x104, "bl", f"300 <{RESET}>")]
        self.assertTrue(any("BusFault_Handler" in f for f in image_findings(table, self.symbols)))
        # SYS-169: a further call in the hard fault line path, one of its two lines lost, a dump reader with a callee
        table = dict(self.table)
        table["nxHardFaultFinal"] = list(self.table["nxHardFaultFinal"]) + [(0x100, "bl", "200 <xTaskGetTickCount>")]
        self.assertTrue(any("nxHardFaultFinal" in f for f in image_findings(table, self.symbols)))
        table = dict(self.table)
        body = list(self.table["nxHardFaultFinal"])
        first_write = next(i for i, ins in enumerate(body) if WRITE in ins[2])
        table["nxHardFaultFinal"] = body[:first_write] + body[first_write + 1:]
        self.assertTrue(any("nxHardFaultFinal" in f for f in image_findings(table, self.symbols)))
        table = dict(self.table)
        table[DUMP_READERS[1]] = [(0x100, "bl", "200 <printf>")]
        self.assertTrue(any("expected a leaf" in f for f in image_findings(table, self.symbols)))
        self.assertTrue(any("vTaskDelete" in f for f in image_findings(self.table, self.symbols | {"vTaskDelete"})))
        # SYS-97: a prologue in the MSP check of the hard fault, or a lost branch to the overflow line
        table = dict(self.table)
        table[HARD_FAULT_FINAL] = [(0x100, "push", "{r3, lr}")] + list(self.table[HARD_FAULT_FINAL])
        self.assertTrue(any(HARD_FAULT_FINAL in f for f in image_findings(table, self.symbols)))
        table = dict(self.table)
        table[HARD_FAULT_FINAL] = [i for i in self.table[HARD_FAULT_FINAL] if "nxIsrStackOverflowFinal" not in i[2]]
        self.assertTrue(any(HARD_FAULT_FINAL in f for f in image_findings(table, self.symbols)))


if __name__ == "__main__":
    unittest.main()
