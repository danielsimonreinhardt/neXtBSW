"""SYS-39: in the linked image the console line of the hold goes out without interrupts and without a console task.

Board finding 2026-09-21 (SYS-38, capture 46): after ROM_CHECK_FAILURE (hold from the idle context) no line
"SAFE STATE HELD (ROM_CHECK_FAILURE)" reached the console. Up to SYS-39 the hold printed through printf and the
charIo layer of OpenBSW. Since SYS-39 it formats the line itself and calls safety::safeConsoleWrite() of the family
port (safeSupervisor/SafeConsole.h). The host test (safe-console) runs the logic; this test checks the image:

1. SafeSupervisor::holdSafeState() calls safeConsoleWrite() exactly once, after the first walk of the safe outputs
   table and before the hold loop, and calls no printf/charIo/logger function;
2. everything reachable from safeConsoleWrite() (calls and tail calls, transitively) is free of printf/charIo,
   logger, async, RTOS and heap functions, has no indirect call (its target could be anything) and no instruction
   that enables interrupts (cpsie, msr to PRIMASK/BASEPRI) - the line is sent by polling the transmitter only;
   and it does reach the UART driver (bsp::Uart::waitForTxReady and bsp::Uart::write);
3. the two record words of the line (nxSafeConsoleRequested/-Written, read by J-Link at the board check) lie in RAM
   outside the MPU protected safety region: the hold may run with the MPU gate closed;
4. SYS-59: SafeSupervisor::handle() calls safety::safeConsoleFlush() (around the SAFETY RESET line of every S1 + S2
   reaction) and softwareSystemReset(), and everything reachable from safeConsoleFlush() follows the rules of 2 -
   no printf/charIo/logger/RTOS/heap, no indirect call, no interrupt enable: it only polls the transmitter.
   SYS-98: handle() may reach both through the S1 + S2 sequence it shares with the fault paths (resetWithLine);
5. SYS-98 (findings STK-3/STK-4 of doc/safety/sw-architecture.md): the fault reactions that run on whatever stack
   the fault hit - the kernel faults (resetOnKernelFault, from configASSERT in SysTick and the context switch), a
   failed ETL_ASSERT (reportEtlAssert, etlAssertFailed) and an ECC error from a fault interrupt (reportEccError) -
   reach safeConsoleFlush(), safeConsoleWrite() and softwareSystemReset(), and nothing of printf/charIo, the logger,
   the async/RTOS layer or the heap, and make no indirect call. The rule is what tools/stackuse showed for STK-4 (the
   logger's 248 B frame behind handle() on the interrupt stack), held as a property of the image.

Built-in faults (the rule applied to modified disassembly) must be found: a printf call in the hold, the line
before the table walk, the line inside the loop, an interrupt enable or an indirect call behind safeConsoleWrite();
SYS-98: a logger call in the kernel fault reaction, a fault reaction without the flush, and handle() taken as a fault
path (it reaches the logger - the reason the kernel faults no longer go through it).

Run: .venv/Scripts/python -m unittest ecu/safety/safeSupervisor/test/test_hold_console_elf.py
     (ELF: NX_APP_ELF=<app.ecu.elf> or the demo build of tools/test/build_dirs.py; arm-none-eabi-nm/objdump from PATH
     or NX_ARM_GCC_BIN)
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
REPO = HERE.parents[3]

GCC_PATH = os.pathsep.join(p for p in (os.environ.get("NX_ARM_GCC_BIN"), os.environ.get("PATH")) if p)
ARM_NM = shutil.which("arm-none-eabi-nm", path=GCC_PATH)
ARM_OBJDUMP = shutil.which("arm-none-eabi-objdump", path=GCC_PATH)

HOLD = "safety::SafeSupervisor::holdSafeState(char const*)"
WRITE = "safety::safeConsoleWrite(char const*, unsigned long)"
HANDLE = "safety::SafeSupervisor::handle(safety::SafeSupervisor::Event const&)"
FLUSH = "safety::safeConsoleFlush()"
RESET = "softwareSystemReset"
ENTER_SAFE_STATE = "safety::SafeState::enterSafeState()"
# SYS-98: the S1 + S2 sequence (safe state, mark, flush, line, flush, reset) that handle() shares with the fault paths
RESET_REACTION = "safety::(anonymous namespace)::resetWithLine("
# SYS-98: fault reactions and whether the demo image must contain them (the other two are linked once the family port
# calls reportEccError() and main.cpp registers etlAssertFailed() with etl::set_assert_function)
FAULT_PATHS = {
    "safety::SafeSupervisor::resetOnKernelFault(safety::SafeSupervisor::Event const&)": True,
    "safety::SafeSupervisor::reportEtlAssert(unsigned long) const": True,
    "safety::SafeSupervisor::reportEccError(unsigned long) const": False,
    "safety::etlAssertFailed(etl::exception const&)": False,
    # SYS-97: end of the hard fault reaction after an overflow of the interrupt stack into its guard
    "nxIsrStackOverflowFinal": True,
}
UART_NEEDED = ("bsp::Uart::waitForTxReady()", "bsp::Uart::write(")
RECORDS = ("nxSafeConsoleRequested", "nxSafeConsoleWritten")

# Functions that must not run while the line is written: printf and the charIo layer of OpenBSW, the logger, the
# async/RTOS layer, the heap.
FORBIDDEN = re.compile(
    r"printf|vfctprintf|fctprintf|putchar|__outchar|__outedit|charIO|SerialLogger|putByteToStdout|etl_putchar"
    r"|logger::|Logger::|async::|Async|vTask|xTask|xQueue|xSemaphore|vPort|xPort|FreeRTOS|pvPortMalloc|malloc"
    r"|operator new|_sbrk|tx_")
FUNCTION = re.compile(r"^([0-9a-f]+) <(.+)>:$")
INSN = re.compile(r"^\s*([0-9a-f]+):\s+(?:[0-9a-f]{4}(?: [0-9a-f]{4})?|[0-9a-f]{8})\s+(\S+)\s*(.*)$")
DIRECT = re.compile(r"^([0-9a-f]+) <(.+?)(\+0x[0-9a-f]+)?>\s*$")
CALLS = {"bl", "b", "b.w", "b.n"}  # call and unconditional (tail) branch, direct target
INTERRUPT_ENABLE = re.compile(r"^(cpsie)|^msr\b.*\b(primask|basepri)", re.IGNORECASE)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def application_elf() -> Path | None:
    env = os.environ.get("NX_APP_ELF")
    if env:
        return Path(env)
    build = _load("nx_build_dirs", REPO / "tools/test/build_dirs.py").demo_build_dir(REPO)
    elf = Path(build) / "ecu/application/RelWithDebInfo/app.ecu.elf"
    return elf if elf.is_file() else None


def functions(disassembly: str) -> dict[str, list[tuple[int, str, str]]]:
    """Demangled function name -> instructions (address, mnemonic, operands)."""
    table: dict[str, list[tuple[int, str, str]]] = {}
    current = None
    for line in disassembly.splitlines():
        m = FUNCTION.match(line)
        if m:
            current = table.setdefault(m.group(2), [])
            continue
        m = INSN.match(line)
        if m and current is not None:
            current.append((int(m.group(1), 16), m.group(2), m.group(3).strip()))
    return table


def branches(body: list[tuple[int, str, str]]):
    """(address, mnemonic, target address, target function, offset) of every direct branch or call."""
    for address, op, ops in body:
        m = DIRECT.match(ops)
        if m and op.startswith("b") and op not in ("bx", "blx", "bic", "bics"):
            yield address, op, int(m.group(1), 16), m.group(2), m.group(3)


def callees(body: list[tuple[int, str, str]], name: str) -> list[tuple[int, str]]:
    """(address, function) of every call and unconditional tail call into the entry of another function."""
    return [(a, f) for a, op, _, f, offset in branches(body) if op in CALLS and not offset and f != name]


def hold_findings(table: dict[str, list[tuple[int, str, str]]]) -> list[str]:
    body = table.get(HOLD)
    if body is None:
        return [f"{HOLD} not in the image"]
    found = []
    calls = callees(body, HOLD)
    writes = [a for a, f in calls if f == WRITE]
    if len(writes) != 1:
        found.append(f"holdSafeState() calls safeConsoleWrite() {len(writes)} times, expected once")
    for _, f in calls:
        if FORBIDDEN.search(f):
            found.append(f"holdSafeState() calls {f}")
    enters = [a for a, f in calls if f == ENTER_SAFE_STATE]
    if writes and (not enters or enters[0] > writes[0]):
        found.append("the line is written before the first walk of the safe outputs table")
    # the hold loop: branches back inside the function; the line must come before the earliest loop start
    loop_starts = [target for a, _, target, f, _ in branches(body) if f == HOLD and target <= a]
    if not loop_starts:
        found.append("holdSafeState() has no hold loop")
    elif writes and min(loop_starts) <= writes[0]:
        found.append("the line is written inside the hold loop, not once before it")
    return found


def polling_only_findings(table: dict[str, list[tuple[int, str, str]]], root: str, label: str):
    """Findings of rule 2 for everything reachable from `root`, and the set of reached functions."""
    found = []
    seen = set()
    todo = [root]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        if FORBIDDEN.search(name):
            found.append(f"{label} reaches {name}")
            continue
        body = table.get(name)
        if body is None:
            continue  # not disassembled (data or a veneer): nothing to follow
        for address, op, ops in body:
            if INTERRUPT_ENABLE.search(f"{op} {ops}"):
                found.append(f"{name} enables interrupts at 0x{address:x}: {op} {ops}")
            if (op == "blx" and not DIRECT.match(ops)) or (op == "bx" and ops != "lr"):
                found.append(f"{name} makes an indirect call at 0x{address:x}: {op} {ops}")
        todo.extend(f for _, f in callees(body, name))
    return found, seen


def write_findings(table: dict[str, list[tuple[int, str, str]]]) -> list[str]:
    if WRITE not in table:
        return [f"{WRITE} not in the image"]
    found, seen = polling_only_findings(table, WRITE, "safeConsoleWrite()")
    for needed in UART_NEEDED:
        if not any(n.startswith(needed) for n in seen):
            found.append(f"safeConsoleWrite() does not reach {needed}")
    return found


def reaction_calls(table: dict[str, list[tuple[int, str, str]]], root: str) -> list[str]:
    """Functions `root` calls, including those of the shared S1 + S2 sequence it calls (SYS-98)."""
    calls = [f for _, f in callees(table.get(root, []), root)]
    for f in list(calls):
        if f.startswith(RESET_REACTION):
            calls.extend(g for _, g in callees(table.get(f, []), f))
    return calls


def reset_findings(table: dict[str, list[tuple[int, str, str]]]) -> list[str]:
    """SYS-59: the reset path of handle() flushes the console, and the flush only polls."""
    body = table.get(HANDLE)
    if body is None:
        return [f"{HANDLE} not in the image"]
    if FLUSH not in table:
        return [f"{FLUSH} not in the image"]
    calls = reaction_calls(table, HANDLE)
    found = []
    if FLUSH not in calls:
        found.append("handle() does not call safeConsoleFlush()")
    if RESET not in calls:
        found.append("handle() does not call softwareSystemReset()")
    found.extend(polling_only_findings(table, FLUSH, "safeConsoleFlush()")[0])
    return found


def fault_path_findings(table: dict[str, list[tuple[int, str, str]]], paths=None) -> list[str]:
    """SYS-98, rule 5: every fault reaction reaches flush, line and reset, and nothing FORBIDDEN, no indirect call."""
    found = []
    for root, required in (FAULT_PATHS if paths is None else paths).items():
        if root not in table:
            if required:
                found.append(f"{root} not in the image")
            continue
        seen = set()
        todo = [root]
        while todo:
            name = todo.pop()
            if name in seen:
                continue
            seen.add(name)
            if FORBIDDEN.search(name):
                found.append(f"{root} reaches {name}")
                continue
            body = table.get(name)
            if body is None:
                continue
            for address, op, ops in body:
                if (op == "blx" and not DIRECT.match(ops)) or (op == "bx" and ops != "lr"):
                    found.append(f"{root}: {name} makes an indirect call at 0x{address:x}: {op} {ops}")
            todo.extend(f for _, f in callees(body, name))
        for needed in (FLUSH, WRITE, RESET):
            if needed not in seen:
                found.append(f"{root} does not reach {needed}")
    return found


def nm_symbols(elf: Path) -> dict[str, tuple[int, str]]:
    out = subprocess.run([ARM_NM, str(elf)], capture_output=True, text=True, check=True).stdout
    return {p[2]: (int(p[0], 16), p[1]) for p in (line.split() for line in out.splitlines()) if len(p) == 3}


@unittest.skipUnless(ARM_NM and ARM_OBJDUMP, "arm-none-eabi-nm/objdump not found (PATH or NX_ARM_GCC_BIN)")
class HoldConsoleInTheImage(unittest.TestCase):
    table: dict[str, list[tuple[int, str, str]]] = {}
    elf: Path | None = None

    @classmethod
    def setUpClass(cls):
        cls.elf = application_elf()
        if cls.elf is None or not cls.elf.is_file():
            return
        out = subprocess.run([ARM_OBJDUMP, "-d", "-C", str(cls.elf)],
                             capture_output=True, text=True, check=True).stdout
        cls.table = functions(out)

    def setUp(self):
        if not self.table:
            self.skipTest("no application ELF (NX_APP_ELF or a demo build, tools/test/build_dirs.py)")

    def test_hold_writes_the_line_once_before_the_loop_without_printf(self):
        self.assertEqual(hold_findings(self.table), [])

    def test_the_line_goes_out_by_polling_only(self):
        self.assertEqual(write_findings(self.table), [])

    def test_reset_path_flushes_by_polling_only(self):
        self.assertEqual(reset_findings(self.table), [])

    def test_fault_paths_without_logger_or_printf(self):
        self.assertEqual(fault_path_findings(self.table), [])

    def test_records_lie_outside_the_protected_ram(self):
        symbols = nm_symbols(self.elf)
        self.assertIn("__MPU_BSS_START", symbols)
        start, end = symbols["__MPU_BSS_START"][0], symbols["__MPU_BSS_END"][0]
        for record in RECORDS:
            self.assertIn(record, symbols)
            address, kind = symbols[record]
            self.assertEqual(kind, "B", f"{record}: global .bss expected, nm type {kind}")
            self.assertFalse(start <= address < end, f"{record} in the MPU protected RAM")

    def test_built_in_faults_are_found(self):
        hold = self.table[HOLD]
        write_at = next(a for a, op, ops in hold if WRITE in ops)
        enter_at = next(a for a, op, ops in hold if ENTER_SAFE_STATE in ops)

        def with_hold(body):
            return dict(self.table, **{HOLD: body})

        printf = [(a, op, ops.replace(WRITE, "debug_printf") if a == write_at else ops) for a, op, ops in hold]
        self.assertTrue(hold_findings(with_hold(printf)))
        before = [(a, op, ops.replace(ENTER_SAFE_STATE, "x()") if a == enter_at else ops) for a, op, ops in hold]
        self.assertTrue(hold_findings(with_hold(before)))
        twice = hold + [(hold[-1][0] + 2, "bl", f"{write_at:x} <{WRITE}>")]
        self.assertTrue(hold_findings(with_hold(twice)))
        # the loop jumps back to the call: the line would be written in every round
        back = [a for a, _, target, f, _ in branches(hold) if f == HOLD and target <= a]
        self.assertTrue(back)
        loop = [(a, op, f"{write_at:x} <{HOLD}+0x{write_at - hold[0][0]:x}>" if a == back[-1] else ops)
                for a, op, ops in hold]
        self.assertTrue(hold_findings(with_hold(loop)))

        body = self.table[WRITE]
        cpsie = dict(self.table, **{WRITE: body + [(body[-1][0] + 2, "cpsie", "i")]})
        self.assertTrue(write_findings(cpsie))
        indirect = dict(self.table, **{WRITE: body + [(body[-1][0] + 2, "blx", "r3")]})
        self.assertTrue(write_findings(indirect))
        logger = dict(self.table, **{WRITE: body + [(body[-1][0] + 2, "bl", "1234 <util::logger::Logger::error()>")]})
        self.assertTrue(write_findings(logger))

        # SYS-59: a flush that waits through the RTOS or the logger, a reset path without the flush
        flush = self.table[FLUSH]
        rtos = dict(self.table, **{FLUSH: flush + [(flush[-1][0] + 2, "bl", "1234 <vTaskDelay>")]})
        self.assertTrue(reset_findings(rtos))
        enable = dict(self.table, **{FLUSH: flush + [(flush[-1][0] + 2, "cpsie", "i")]})
        self.assertTrue(reset_findings(enable))
        handle = self.table[HANDLE]
        reactions = [n for n in self.table if n.startswith(RESET_REACTION)]
        without = {n: [(a, op, ops.replace(FLUSH, "x()")) for a, op, ops in self.table[n]]
                   for n in [HANDLE, *reactions]}
        self.assertTrue(reset_findings(dict(self.table, **without)))

        # SYS-98: a log line in the kernel fault reaction, a fault reaction without the flush, handle() as a fault path
        kernel = "safety::SafeSupervisor::resetOnKernelFault(safety::SafeSupervisor::Event const&)"
        body = self.table[kernel]
        logged = dict(self.table, **{kernel: body + [(body[-1][0] + 2, "bl", "1234 <util::logger::Logger::warn()>")]})
        self.assertTrue(fault_path_findings(logged))
        self.assertTrue(fault_path_findings(dict(self.table, **without)))
        self.assertTrue(any("Logger" in f for f in fault_path_findings(self.table, {HANDLE: True})))


if __name__ == "__main__":
    unittest.main()
