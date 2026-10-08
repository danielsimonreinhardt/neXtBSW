"""SYS-37: static rule - who may write the MPU protected safety RAM, and from which context.

Board finding 2026-09-21 (SYS-05, captures 25/26): the RTE task called
`SafeSupervisor::getInstance().rteTimingMonitor.trigger(context)`. A safeMonitor monitor stores its context in itself
and calls SafeSupervisor::handle() - writes into the RAM that the MPU keeps read-only outside the safety task and the
SafetyShell (fusa gate, SM-10; on the S32K1 every .bss of a libsafe* archive). IMPRECISERR, HardFault, reset loop.
Host tests could not see it: on the host every byte is writable.

The rule (source scan, no tool needed):
1. A call of trigger()/check()/hit() on a monitor of the SafeSupervisor that is declared as a direct monitor
   (TriggerMonitor, SequenceMonitor, ValueMonitor, SafetyManagerSequenceMonitor in SafeSupervisor.h) is allowed only
   in the files of SAFETY_CONTEXT - each with the context it runs in: `safety-task` (called only from
   SafetyManager::init/cyclic, gate open or inside its SafetyShell) or `shell` (the file opens a SafetyShell itself,
   checked). A monitor declared as DeferredTriggerMonitor (safeSupervisor/SafetyEventMailbox.h) may be triggered
   from anywhere - that is what it is for.
2. The same for the other writers of the protected RAM that are reachable from outside: enterLimpHome()/
   leaveLimpHome() of the supervisor, deliverDeferredEvents(), deliverDeviceReports() (SYS-29),
   startResetLoopLimit() (SYS-43, may raise RESET_LOOP_LIMIT), reportStackOverflow()/reportRtosAssert() (SYS-80,
   the kernel fault hooks of the family port), and every method of the SafeSystem object reached
   through getSafeSystem() except the reading ones (READ_ONLY_SAFE_SYSTEM) - "safeadc reset" of the console wrote
   safeSystem from the console task (same fault, found by this rule, SYS-37).
3. Every entry of SAFETY_CONTEXT is still needed (a stale entry would silently allow a later call).

A new call from another file makes the test red with the file, the line and what to do: declare the monitor as
DeferredTriggerMonitor (box in ordinary RAM, delivered by the safety task), or - after review of the context - add
the file to SAFETY_CONTEXT with its reason.

The ELF part (skipped without a target build) checks the other half on the linked image: the supervisor lies inside
[__MPU_BSS_START, __MPU_BSS_END), every event box declared in SafeSupervisor.h and the request word of
"safeadc reset" outside it, in RAM the startup code clears.

Run: .venv/Scripts/python -m unittest ecu/safety/safeSupervisor/test/test_safety_context.py
     (ELF part: NX_APP_ELF=<app.ecu.elf> or a demo build found by tools/test/build_dirs.py; arm-none-eabi-nm from
     PATH or NX_ARM_GCC_BIN)
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import unittest
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
HEADER = REPO / "ecu/safety/safeSupervisor/include/safeSupervisor/SafeSupervisor.h"

SCAN_ROOTS = ("ecu", "platforms", "libs", "swc", "bootloader")
SCAN_SUFFIXES = {".c", ".cpp", ".h", ".hpp"}
SKIP_DIRS = {"test", "tests", "generated", "external", "build", "__pycache__"}

# SYS-98: KernelFaultMonitor (stackOverflowMonitor, rtosAssertMonitor) stores its context in the protected RAM like the
# others and sets limp home - direct as well
DIRECT_TYPES = {"TriggerMonitor", "SequenceMonitor", "ValueMonitor", "SafetyManagerSequenceMonitor",
                "KernelFaultMonitor"}
DEFERRED_TYPES = {"DeferredTriggerMonitor"}

# Files that run with write access to the protected RAM, and why (reviewed SYS-37). Kind `shell`: the file must
# open a SafetyShell itself; kind `safety-task`: its functions are called only from SafetyManager::init()/cyclic()
# (init inside a SafetyShell, cyclic inside ProtectedRamScopedUnlock), and the safety task has the highest priority.
SAFETY_CONTEXT: dict[str, tuple[str, str]] = {
    "platforms/s32k1xx/port/main/src/main.cpp":
        ("shell", "construction of the supervisor and watchdog self-test in main(), before the scheduler"),
    "platforms/s32k1xx/port/safety/safeLifecycle/src/safeLifecycle/SafetyManager.cpp":
        ("safety-task", "the safety task itself: MPU state and sequence monitors, delivery of deferred reports"),
    "ecu/safety/safeWatchdog/src/safeWatchdog/SafeWatchdog.cpp":
        ("safety-task", "SafeWatchdog::cyclic from SafetyManager::cyclic"),
    "platforms/s32k1xx/port/safety/safeMemory/src/safeMemory/SafeMemory.cpp":
        ("safety-task", "safe_memory::cyclic from SafetyManager::cyclic"),
    "platforms/s32k1xx/port/safety/safeMemory/src/safeMemory/RomCheck.cpp":
        ("shell", "RomCheck::idle checks the result inside its own SafetyShell"),
    "platforms/s32k1xx/port/safety/safeSystem/src/safeSystem/SafeSystem.cpp":
        ("safety-task", "SafeSystem::cyclic from SafetyManager::cyclic"),
    "platforms/s32k1xx/port/safety/safeIo/src/safeIo/SafeIo.cpp":
        ("safety-task", "SafeIo::init (SafetyShell of SafetyManager::init) and SafeIo::cyclic"),
    # SYS-80: the kernel fault hooks (stack overflow, failed assertion) run in whatever context the kernel found the
    # fault in - the context switch, a task, an interrupt - and open their own SafetyShell before they report
    "platforms/s32k1xx/port/main/src/os/isr/isr_sys.cpp":
        ("shell", "nxKernelStackOverflow/nxKernelAssertFailed report to the supervisor inside their own SafetyShell"),
    # SYS-95 (DFA-1, DFA-2, DFA-6 a): the progress checks, called only from SafetyManager::cyclic
    # (checkSupervisionPeripherals, gate open)
    "platforms/s32k1xx/port/safety/safeLifecycle/src/safeLifecycle/ProgressSupervision.cpp":
        ("safety-task", "progress::cyclic from SafetyManager::cyclic: ROM-check passes, deadman counter, system time"),
}

# SafeSystem methods that only read (the console may call them; the counters are copied in one exclusive section).
READ_ONLY_SAFE_SYSTEM = {"getAdcDiagnostics"}

MONITOR_CALL = re.compile(r"\b(\w+Monitor)\s*\.\s*(trigger|check|hit)\s*\(")
SUPERVISOR_WRITE = re.compile(  # SYS-29: device boxes; SYS-43: the reset-loop limit at start; SYS-80: kernel faults
    r"\.\s*(enterLimpHome|leaveLimpHome|deliverDeferredEvents|deliverDeviceReports|startResetLoopLimit"
    r"|reportStackOverflow|reportRtosAssert)\s*\(")
SAFE_SYSTEM_CALL = re.compile(r"\bgetSafeSystem\s*\(\s*\)\s*\.\s*(\w+)\s*\(")
MEMBER = re.compile(r"^\s*(\w+)\s+(\w+Monitor)\s*;", re.MULTILINE)
MAILBOX = re.compile(r"^\s*extern\s+SafetyEventMailbox\s+(\w+)\s*;", re.MULTILINE)
COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    text: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.text}"


def monitors(header_text: str) -> tuple[set[str], set[str]]:
    """(direct, deferred) monitor member names of SafeSupervisor.h."""
    direct, deferred = set(), set()
    for kind, name in MEMBER.findall(COMMENT.sub("", header_text)):
        if kind in DIRECT_TYPES:
            direct.add(name)
        elif kind in DEFERRED_TYPES:
            deferred.add(name)
    return direct, deferred


def strip_comments(text: str) -> str:
    """Comments replaced by blanks of the same line structure, so line numbers stay right."""
    return COMMENT.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def sources(repo: Path):
    for root in SCAN_ROOTS:
        base = repo / root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.suffix not in SCAN_SUFFIXES or not path.is_file():
                continue
            if SKIP_DIRS.intersection(path.relative_to(repo).parts[:-1]):
                continue
            yield path


def scan(repo: Path, header_text: str, files: dict[str, str] | None = None,
         context: dict[str, tuple[str, str]] | None = None) -> tuple[list[Finding], set[str]]:
    """Findings of the rule and the SAFETY_CONTEXT entries that were used. `files` (relative path -> text) replaces
    the scan of the repository (self-test)."""
    context = SAFETY_CONTEXT if context is None else context
    direct, _ = monitors(header_text)
    if files is None:
        files = {p.relative_to(repo).as_posix(): p.read_text(encoding="utf-8", errors="replace")
                 for p in sources(repo)}
    findings: list[Finding] = []
    used: set[str] = set()
    for rel, raw in files.items():
        if rel.endswith("safeSupervisor/src/safeSupervisor/SafeSupervisor.cpp"):
            continue  # the supervisor itself (handle() runs where the monitor was triggered)
        text = strip_comments(raw)
        allowed = rel in context
        hits: list[tuple[int, str]] = []
        for m in MONITOR_CALL.finditer(text):
            if m.group(1) in direct:
                hits.append((m.start(), f"{m.group(1)}.{m.group(2)}() of a direct monitor"))
        for m in SUPERVISOR_WRITE.finditer(text):
            hits.append((m.start(), f"{m.group(1)}() writes the supervisor"))
        for m in SAFE_SYSTEM_CALL.finditer(text):
            if m.group(1) not in READ_ONLY_SAFE_SYSTEM:
                hits.append((m.start(), f"getSafeSystem().{m.group(1)}() may write the protected SafeSystem"))
        if not hits:
            continue
        if allowed:
            used.add(rel)
            kind, _reason = context[rel]
            if kind == "shell" and not re.search(r"\bSafetyShell\b\s+(const\s+)?\w+", text):
                findings.append(Finding(rel, 1, "listed as 'shell' but opens no SafetyShell"))
            continue
        for pos, what in hits:
            line = text.count("\n", 0, pos) + 1
            findings.append(Finding(
                rel, line,
                f"{what} outside the safety context (MPU protected RAM is read-only here) - declare the monitor as "
                "DeferredTriggerMonitor (SafetyEventMailbox.h) or hand the work to the safety task; add the file to "
                "SAFETY_CONTEXT only if it really runs in the safety task or inside a SafetyShell"))
    return findings, used


class SafetyContextRule(unittest.TestCase):
    def setUp(self):
        self.header = HEADER.read_text(encoding="utf-8")

    def test_no_write_to_the_protected_ram_outside_the_safety_context(self):
        findings, _ = scan(REPO, self.header)
        self.assertEqual([], [str(f) for f in findings])

    def test_every_safety_context_entry_is_needed(self):
        _, used = scan(REPO, self.header)
        self.assertEqual([], sorted(set(SAFETY_CONTEXT) - used))

    def test_rte_monitor_is_deferred(self):
        direct, deferred = monitors(self.header)
        self.assertIn("rteTimingMonitor", deferred)
        self.assertNotIn("rteTimingMonitor", direct)
        self.assertGreaterEqual(len(direct), 10)  # the parser still sees the direct monitors

    def test_device_monitors_are_direct(self):
        """SYS-29 (decision `sys37-postfach-abschaltend`): neither device monitor is deferred - DEVICE_FAULT may
        switch off and must be raised in the safety context itself; DEVICE_REPORT reaches handle() only through
        deliverDeviceReports() of the safety task or a check there. A driver may only post into its own box."""
        direct, deferred = monitors(self.header)
        for name in ("deviceFaultMonitor", "deviceReportMonitor"):
            self.assertIn(name, direct)
            self.assertNotIn(name, deferred)

    # --- the rule finds what it is for (mutants of the real tree)

    def test_device_fault_or_delivery_outside_the_safety_task_is_found(self):
        """SYS-29: a driver that raises DEVICE_FAULT from its own task, or delivers device boxes itself, is a
        finding; posting into its DeviceReportBox is allowed anywhere."""
        files = {
            "libs/bsw/someDriver/src/Driver.cpp":
                "void f() {\n  sup().deviceFaultMonitor.trigger(word);\n  sup().deviceReportMonitor.trigger(w);\n"
                "  box.report(bus, 1U, FaultClass::OUTPUT);\n}\n",
            "ecu/application/src/systems/Other.cpp":
                "void g() { sup().deliverDeviceReports(box); }\n",
        }
        findings, _ = scan(REPO, self.header, files=files)
        self.assertEqual(sorted((f.path, f.line) for f in findings),
                         [("ecu/application/src/systems/Other.cpp", 1),
                          ("libs/bsw/someDriver/src/Driver.cpp", 2),
                          ("libs/bsw/someDriver/src/Driver.cpp", 3)])

    def test_board_finding_is_found_on_the_declaration_before_sys37(self):
        before = self.header.replace("DeferredTriggerMonitor rteTimingMonitor;", "TriggerMonitor rteTimingMonitor;")
        self.assertNotEqual(before, self.header)
        findings, _ = scan(REPO, before)
        self.assertTrue(any(f.path == "ecu/application/src/systems/RteSystem.cpp"
                            and "rteTimingMonitor.trigger()" in f.text for f in findings), findings)

    def test_safeadc_reset_before_sys37_is_found(self):
        files = {"platforms/s32k1xx/port/safety/adcSafetyConsole/src/adcSafetyConsole/AdcSafetyConsole.cpp":
                 "void f() {\n  getSafeSystem().getAdcDiagnostics();\n  getSafeSystem().resetAdcDiagnostics();\n}\n"}
        findings, _ = scan(REPO, self.header, files=files)
        self.assertEqual([3], [f.line for f in findings])

    def test_direct_trigger_from_a_driver_or_isr_is_found(self):
        files = {
            "libs/bsw/someDriver/src/Driver.cpp":
                "void f() { ::safety::SafeSupervisor::getInstance().dmaEccMonitor.trigger(1U); }\n",
            "platforms/s32k1xx/port/main/src/os/isr/isr_x.cpp":
                "void g() {\n  // romCheckMonitor.trigger(); in a comment is fine\n  auto& s = sup();\n"
                "  s.safeWatchdogSequenceMonitor.hit(E::ENTER);\n  s.leaveLimpHome();\n}\n",
            "ecu/application/src/systems/Other.cpp":
                "void h() { sup().rteTimingMonitor.trigger(7U); }\n",  # deferred: allowed anywhere
        }
        findings, _ = scan(REPO, self.header, files=files)
        self.assertEqual(sorted((f.path, f.line) for f in findings),
                         [("libs/bsw/someDriver/src/Driver.cpp", 1),
                          ("platforms/s32k1xx/port/main/src/os/isr/isr_x.cpp", 4),
                          ("platforms/s32k1xx/port/main/src/os/isr/isr_x.cpp", 5)])

    def test_shell_entry_without_a_shell_is_found(self):
        files = {"platforms/s32k1xx/port/safety/safeMemory/src/safeMemory/RomCheck.cpp":
                 "void f() { supervisor.romCheckMonitor.trigger(); }\n"}
        findings, _ = scan(REPO, self.header, files=files)
        self.assertEqual(1, len(findings))
        self.assertIn("opens no SafetyShell", findings[0].text)


# ---------------------------------------------------------------------------------------------------- ELF part

def _load_build_dirs():
    spec = importlib.util.spec_from_file_location("nx_build_dirs", REPO / "tools/test/build_dirs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def application_elf() -> Path | None:
    env = os.environ.get("NX_APP_ELF")
    if env:
        return Path(env)
    build = _load_build_dirs().demo_build_dir(REPO)
    elf = Path(build) / "ecu/application/RelWithDebInfo/app.ecu.elf"
    return elf if elf.is_file() else None


GCC_PATH = os.pathsep.join(p for p in (os.environ.get("NX_ARM_GCC_BIN"), os.environ.get("PATH")) if p)
ARM_NM = shutil.which("arm-none-eabi-nm", path=GCC_PATH)
ARM_OBJDUMP = shutil.which("arm-none-eabi-objdump", path=GCC_PATH)

# ELF form of rule 1: functions of the linked image that may call SafeSupervisor::handle() - the safety context of
# SAFETY_CONTEXT by function name (prefix). A direct monitor inlined into a function outside the safety context
# shows up as a call of handle() from there; safeMonitor template instances that were not inlined are transparent
# (their callers are checked instead).
HANDLE = "safety::SafeSupervisor::handle("
HANDLE_CALLERS = (
    "main",
    "safety::SafetyManager::",
    "safety::SafeWatchdog::",
    "safety::safe_memory::",
    "safety::SafeSystem::",
    "safety::SafeIo::",
    "safety::RomCheck::",
    "safety::progress::",  # SYS-95: progress::cyclic, called only from SafetyManager::cyclic (rule 1)
    "safety::SafeSupervisor::deliverDeferredEvents(",
    "safety::SafeSupervisor::deliverDeviceReports(",  # SYS-29: its drain lambda, called by the safety task
    "safety::SafeSupervisor::startResetLoopLimit(",  # SYS-43: called only from main() (rule 2)
    "safety::SafeSupervisor::reportStackOverflow(",  # SYS-80: called only inside the SafetyShell of isr_sys.cpp
    "safety::SafeSupervisor::reportRtosAssert(",  # SYS-80: the same (rule 2)
    "safety::DeferredTrigger<",
)
FUNCTION = re.compile(r"^[0-9a-f]+ <(.+)>:$")
CALL = re.compile(r"\s(?:bl|blx|b|b\.w|b\.n)\s+[0-9a-f]+ <(.+?)(?:\+0x[0-9a-f]+)?>\s*$")


def handle_callers(disassembly: str) -> list[str]:
    """Functions outside HANDLE_CALLERS that call SafeSupervisor::handle() (directly or through a safeMonitor
    instance that was not inlined)."""
    calls: dict[str, set[str]] = {}
    function = ""
    for line in disassembly.splitlines():
        m = FUNCTION.match(line)
        if m:
            function = m.group(1)
            continue
        c = CALL.search(line)
        if c and function and c.group(1) != function:
            calls.setdefault(c.group(1), set()).add(function)
    targets = [name for name in calls if name.startswith(HANDLE)]
    seen, findings = set(targets), set()
    while targets:
        target = targets.pop()
        for caller in calls.get(target, ()):
            if caller.startswith(HANDLE_CALLERS) or caller.startswith(HANDLE):
                continue
            if caller.startswith("safeMonitor::"):
                if caller not in seen:
                    seen.add(caller)
                    targets.append(caller)
                continue
            findings.add(caller)
    return sorted(findings)


def elf_symbols(elf: Path) -> dict[str, tuple[int, int]]:
    """Demangled symbol -> (address, size)."""
    out = subprocess.run([ARM_NM, "-C", "-S", str(elf)], capture_output=True, text=True, check=True).stdout
    result: dict[str, tuple[int, int]] = {}
    for line in out.splitlines():
        parts = line.split(maxsplit=3)
        if len(parts) == 4 and re.fullmatch(r"[0-9a-f]+", parts[1]):
            result[parts[3]] = (int(parts[0], 16), int(parts[1], 16))
        elif len(parts) == 3:
            result.setdefault(parts[2], (int(parts[0], 16), 0))
    return result


class HandleCallerRule(unittest.TestCase):
    """The ELF form of the rule on disassembly text (no tool needed)."""

    BEFORE_SYS37 = """
0000d408 <(anonymous namespace)::reportTimingViolation(unsigned long, unsigned int, unsigned long)>:
    d410:\tf00d faaa \tbl\t1a968 <safety::SafeSupervisor::getInstance()>
    d420:\tf00d fbbb \tbl\t1a000 <safety::SafeSupervisor::handle(safety::SafeSupervisor::Event const&)>
000189f4 <safety::SafetyManager::cyclic()>:
    18a20:\tf001 ffab \tbl\t1a000 <safety::SafeSupervisor::handle(safety::SafeSupervisor::Event const&)>
00021000 <safeMonitor::Trigger<safety::SafeSupervisor, E, M, unsigned long>::trigger(unsigned long const&)>:
    21004:\tf7f8 ff00 \tb.w\t1a000 <safety::SafeSupervisor::handle(safety::SafeSupervisor::Event const&)>
00022000 <safety::AdcSafetyCommand::executeCommand(util::command::CommandContext&, unsigned char)>:
    22010:\tf7ff fff0 \tbl\t21000 <safeMonitor::Trigger<safety::SafeSupervisor, E, M, unsigned long>::trigger(unsigned long const&)>
00023000 <safety::SafeIo::cyclic()>:
    23010:\tf7ff fff0 \tbl\t21000 <safeMonitor::Trigger<safety::SafeSupervisor, E, M, unsigned long>::trigger(unsigned long const&)>
"""

    def test_callers_outside_the_safety_context_are_found(self):
        self.assertEqual(
            handle_callers(self.BEFORE_SYS37),
            ["(anonymous namespace)::reportTimingViolation(unsigned long, unsigned int, unsigned long)",
             "safety::AdcSafetyCommand::executeCommand(util::command::CommandContext&, unsigned char)"])

    def test_safety_context_alone_is_clean(self):
        clean = "\n".join(line for line in self.BEFORE_SYS37.splitlines()
                          if "reportTimingViolation" not in line and "AdcSafetyCommand" not in line
                          and not line.startswith("    d4") and not line.startswith("    2201"))
        self.assertEqual([], handle_callers(clean))


@unittest.skipUnless(ARM_NM, "arm-none-eabi-nm not found (PATH or NX_ARM_GCC_BIN)")
class SafetyContextElf(unittest.TestCase):
    def setUp(self):
        self.elf = application_elf()
        if self.elf is None or not self.elf.is_file():
            self.skipTest("no application ELF (NX_APP_ELF or a demo build, tools/test/build_dirs.py)")
        self.sym = elf_symbols(self.elf)
        self.start = self.sym["__MPU_BSS_START"][0]
        self.end = self.sym["__MPU_BSS_END"][0]

    def protected(self, name: str) -> bool:
        address, size = self.sym[name]
        inside = self.start <= address < self.end
        if inside:
            self.assertLessEqual(address + size, self.end, name)
        return inside

    def cleared(self, name: str) -> bool:
        """In RAM the startup code clears: .bss [__bss_start__, __bss_end__) or the ECC initialization
        [__DATA_RAM, __DATA_RAM_END) outside the initialized data."""
        address, size = self.sym[name]
        in_bss = self.sym["__bss_start__"][0] <= address and address + size <= self.sym["__bss_end__"][0]
        in_hot_bss = ("__HOT_BSS_START" in self.sym and self.sym["__HOT_BSS_START"][0] <= address
                      and address + size <= self.sym["__HOT_BSS_END"][0])
        return in_bss or in_hot_bss

    def test_supervisor_is_protected(self):
        self.assertTrue(self.protected("safety::safeSupervisorConstructor"))

    def test_event_boxes_are_outside_the_protected_ram(self):
        boxes = MAILBOX.findall(HEADER.read_text(encoding="utf-8"))
        self.assertIn("rteTimingMailbox", boxes)
        for box in boxes:
            with self.subTest(box=box):
                name = f"safety::{box}"
                self.assertIn(name, self.sym)
                self.assertFalse(self.protected(name), f"{name} lies in the MPU protected RAM")
                self.assertTrue(self.cleared(name), f"{name} is not in RAM the startup code clears")

    @unittest.skipUnless(ARM_OBJDUMP, "arm-none-eabi-objdump not found")
    def test_handle_is_called_only_from_the_safety_context(self):
        out = subprocess.run([ARM_OBJDUMP, "-d", "-C", str(self.elf)], capture_output=True, text=True,
                             check=True).stdout
        self.assertIn(HANDLE, out)  # the image really contains the supervisor
        self.assertEqual([], handle_callers(out))

    def test_reset_loop_record_is_in_the_no_init_ram(self):
        """SYS-43 (F13): the record of the reset-loop limit must survive the software reset of a safety reaction:
        in the NoInit region above the RAM the startup code initializes (__DATA_RAM_END), below the boot request
        word pair of the bootloader and the failed-start record 8 bytes under it, and outside the MPU protected
        RAM (it is written in main() and by the safety task, never cleared by the startup code)."""
        name = "safety::resetLoopRecord"
        self.assertIn(name, self.sym)
        address, size = self.sym[name]
        self.assertEqual(size, 16, "four words: count, pending, lastCause, check")
        self.assertFalse(self.protected(name), f"{name} lies in the MPU protected RAM")
        self.assertFalse(self.cleared(name), f"{name} lies in RAM the startup code clears")
        self.assertGreaterEqual(address, self.sym["__DATA_RAM_END"][0])
        self.assertGreaterEqual(address, self.sym["__NOINIT_DATA"][0])
        self.assertGreaterEqual(address, self.sym["__hard_fault_dump_end"][0])
        boot_request = self.boot_request_address()
        if boot_request is not None:
            self.assertLessEqual(address + size, boot_request - 8, "overlaps the failed-start record/boot request")

    def boot_request_address(self) -> int | None:
        """NX_MEMORY_BOOT_REQUEST_ADDR of the generated memory layout next to the ELF's build, None if not found."""
        build = self.elf.parents[3]
        for header in build.rglob("memory/memoryLayout.h"):
            m = re.search(r"#define\s+NX_MEMORY_BOOT_REQUEST_ADDR\s+(0x[0-9A-Fa-f]+)",
                          header.read_text(encoding="utf-8"))
            if m:
                return int(m.group(1), 16)
        return None

    def test_safeadc_reset_request_is_outside_the_protected_ram(self):
        names = [n for n in self.sym if n.endswith("adcDiagnosticsResetRequest")]
        if not names:
            self.skipTest("project without the console group safeadc (PLATFORM_SUPPORT_IO)")
        for name in names:
            self.assertFalse(self.protected(name), name)

    def test_safe_off_report_box(self):
        """SYS-194 (SYS147-B1 b): the event box of the safe_off hold - posted by TASK_BSP, so outside the MPU
        protected RAM and cleared by the startup code - and the strong definition of safety::safeOffReportBox() of the
        integration, exactly one: if the weak default of safeLifecycle (nullptr) won, the safety task would never
        deliver a take-back and nothing on the host would see it."""
        name = "integration::safeOffReports"
        if name not in self.sym:
            self.skipTest("project without safe_off outputs (no hold of the integration)")
        self.assertFalse(self.protected(name), f"{name} lies in the MPU protected RAM")
        self.assertTrue(self.cleared(name), f"{name} is not in RAM the startup code clears")
        out = subprocess.run([ARM_NM, "-C", str(self.elf)], capture_output=True, text=True, check=True).stdout
        kinds = [line.split(maxsplit=2)[1] for line in out.splitlines()
                 if line.endswith(" safety::safeOffReportBox()") and len(line.split(maxsplit=2)) == 3]
        self.assertEqual(["T"], kinds, "exactly one strong definition of safety::safeOffReportBox()")


if __name__ == "__main__":
    unittest.main()
