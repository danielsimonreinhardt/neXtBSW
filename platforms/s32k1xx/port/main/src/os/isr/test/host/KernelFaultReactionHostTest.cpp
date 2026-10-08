/* SYS-80: host test of the reactions of the S32K1 family port to faults the kernel finds in itself, and of the last
 * line of the fault exception handlers - the real code of
 *   - platforms/s32k1xx/port/main/src/osHooks/freertos/osHooks.cpp (vApplicationStackOverflowHook),
 *   - platforms/s32k1xx/port/main/src/os/isr/isr_sys.cpp (nxKernelStackOverflow, nxKernelAssertFailed,
 *     HardFault_Handler_Final, BusFault_Handler, UsageFault_Handler),
 *   - platforms/s32k1xx/port/freeRtosCoreConfiguration/include/os/FreeRtosPlatformConfig.h (configASSERT),
 *   - ecu/safety/safeSupervisor/src/safeSupervisor/SafeSupervisor.cpp (STACK_OVERFLOW, RTOS_ASSERT),
 * with stubs for the kernel, the SafetyShell, the safe outputs, the console transmitter and the reset.
 *
 * Findings of doc/safety/rtos-comparison.md section 1.4 it shows closed:
 * - R1: a stack overflow the kernel detects no longer returns into the kernel - outputs off (S1), mark of the
 *   reset-loop record, flush, "SAFETY RESET (STACK_OVERFLOW, task <name>, context <n>)", flush, reset (S2), all inside
 *   the SafetyShell (interrupts suspended, protected RAM writable), no log line;
 * - R2: configASSERT is no longer empty - a failed check takes the same path as RTOS_ASSERT with the code address of
 *   the call (inside the checking function) and the source line; a check that holds does nothing.
 * From SYS-59: the fault exception handlers write their "HARD FAULT RESET" line between two flushes instead of printf,
 * skip it on a dead transmitter, and reset; the ECC check stays before the reset.
 *
 * The reset stub throws, so each reaction ends where the target resets; reaching the loop after the reset request, or
 * a hook that returns, is a finding. Mutants: test_kernel_fault_reaction_mutations.py next to this file.
 *
 * Build and run (zig c++, no hardware; tools/test -k kernel-fault-reaction):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -DPLATFORM_SUPPORT_IO -DPLATFORM_SUPPORT_WATCHDOG -DUNIT_TEST -DSUPPORT_FREERTOS \
 *       -Iplatforms/s32k1xx/port/main/src/os/isr/test/host/kernelFaultStubs \
 *       -Iecu/safety/safeSupervisor/include -Iecu/safety/safeSupervisor/test/host/stubs \
 *       -Iecu/safety/safeSupervisor/src -Iplatforms/s32k1xx/port/main/src/os/isr \
 *       -Iplatforms/s32k1xx/port/main/src/osHooks -Iplatforms/s32k1xx/port/freeRtosCoreConfiguration/include \
 *       -isystem external/openbsw/libs/safety/safeMonitor/include \
 *       platforms/s32k1xx/port/main/src/os/isr/test/host/KernelFaultReactionHostTest.cpp -o build/host/kfr.exe
 */

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <string>

namespace test_trace
{
std::string trace;   // S safe outputs, E log error, w log warning, O SafetyShell, F flush, P line, C ECC check, R reset
std::string console; // the lines as the transmitter got them
int lockDepth         = 0;
bool transmitterIdle  = true;
bool printedInShell   = false; // the last line was written inside the SafetyShell
bool printedLocked    = false; // ... with all interrupts suspended
bool markedAtFlush    = false; // the reset-loop record was marked when the first flush ran
struct ResetReached
{};

void add(char const c) { trace.push_back(c); }
} // namespace test_trace

namespace test_shell
{
int depth = 0;
}

namespace test_kernel
{
int taskNumberReads = 0;
}

extern "C" void softwareSystemReset(void)
{
    ::test_trace::add('R');
    throw ::test_trace::ResetReached{};
}

#define NX_SAFE_SUPERVISOR_HOLDING() (false)
// NX_SAFE_SUPERVISOR_CONSOLE, _RESET_CONSOLE and _RESET_FLUSH deliberately not replaced: the target path is under test

#include "safeSupervisor/SafeSupervisor.cpp" // NOLINT: the unit under test (include path ecu/safety/safeSupervisor/src)
#include "isr_sys.cpp"                       // NOLINT: the unit under test (include path .../main/src/os/isr)
#include "freertos/osHooks.cpp"              // NOLINT: the unit under test (include path .../main/src/osHooks)

namespace safety
{
SafetyEventMailbox rteTimingMailbox;

namespace
{
::safety::resetloop::Record volatile* watchedRecord = nullptr;
}

bool safeConsoleFlush()
{
    if (::test_trace::trace.find('F') == std::string::npos)
    {
        ::test_trace::markedAtFlush
            = (watchedRecord != nullptr) && (watchedRecord->pending != ::safety::resetloop::CAUSE_NONE);
    }
    ::test_trace::add('F');
    return ::test_trace::transmitterIdle;
}

uint32_t safeConsoleWrite(char const* const data, uint32_t const length)
{
    ::test_trace::add('P');
    ::test_trace::printedInShell = (::test_shell::depth > 0);
    ::test_trace::printedLocked  = (::test_trace::lockDepth > 0);
    ::test_trace::console.append(data, length);
    return length;
}
} // namespace safety

namespace
{
int failures = 0;
int checks   = 0;

#define CHECK(cond)                                                       \
    do                                                                    \
    {                                                                     \
        ++checks;                                                         \
        if (!(cond))                                                      \
        {                                                                 \
            ++failures;                                                   \
            std::printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
        }                                                                 \
    } while (false)

using ::safety::SafeSupervisor;
using Event = SafeSupervisor::Event;
namespace rl = ::safety::resetloop;

rl::Record volatile record;

/** A fresh supervisor as main() of the port leaves it: constructed, reset-loop record registered, limp home left. */
SafeSupervisor& fresh()
{
    ::safety::safeSupervisorConstructor.construct();
    auto& supervisor = SafeSupervisor::getInstance();
    rl::clear(record);
    (void)supervisor.startResetLoopLimit(record, rl::Start{}, 3U, 100U);
    supervisor.leaveLimpHome();
    ::safety::watchedRecord         = &record;
    ::test_trace::trace.clear();
    ::test_trace::console.clear();
    ::test_trace::lockDepth       = 0;
    ::test_trace::transmitterIdle = true;
    ::test_trace::printedInShell  = false;
    ::test_trace::printedLocked   = false;
    ::test_trace::markedAtFlush   = false;
    ::test_shell::depth           = 0;
    ::test_kernel::taskNumberReads = 0;
    ::test_dump::exception         = 3U; // SYS-169: a hard fault dump
    return supervisor;
}

enum class End
{
    RESET,    // the reset was requested - the end on the target
    RETURNED, // the function came back: the kernel would go on
    LOOP      // the loop after the reset request was reached: the reset did not come
};

template<typename F>
End run(F const& f)
{
    try
    {
        f();
    }
    catch (::test_trace::ResetReached const&)
    {
        return End::RESET;
    }
    catch (::test_trace::LoopReached const&)
    {
        return End::LOOP;
    }
    return End::RETURNED;
}

#ifdef PLATFORM_SUPPORT_IO
constexpr char const* SAFE = "S";
#else
constexpr char const* SAFE = "";
#endif

// ----------------------------------------------------------------------------------------------------- R1

/** R1: the kernel's stack check hits (vApplicationStackOverflowHook from the context switch): S1 + S2 with the line,
 * inside the SafetyShell, in the order of SYS-59, never back into the kernel. */
void testStackOverflowEndsInTheSafeState()
{
    auto& supervisor = fresh();
    TestTaskControlBlock task{4U, "can"};
    End const end = run([&] { vApplicationStackOverflowHook(&task, task.name); });
    CHECK(end == End::RESET);
    CHECK(::test_trace::trace == std::string("O") + SAFE + "FPFR");
    CHECK(::test_trace::console == "\r\nSAFETY RESET (STACK_OVERFLOW, task can, context 4): outputs off\r\n");
    CHECK(::test_trace::printedInShell);
    CHECK(::test_trace::printedLocked);
    CHECK(::test_trace::markedAtFlush);
    CHECK(record.pending == (rl::CAUSE_EVENT | 0x15U)); // "last cause 0x115" at the next start
    CHECK(supervisor.inLimpHomeState());
    CHECK(::test_kernel::taskNumberReads == 1);
    CHECK(supervisor.stackOverflowMonitor.getContext() == 4U);
    if (::test_trace::trace != std::string("O") + SAFE + "FPFR")
    {
        std::printf("  stack overflow: trace '%s', line '%s'\n", ::test_trace::trace.c_str(),
                    ::test_trace::console.c_str());
    }
}

/** R1: the name comes from the kernel's data next to an overflowed stack - only printable ASCII, bounded, reaches the
 * line; a missing name is "?". The context number is the task's own. */
void testStackOverflowNameIsBoundedAndPrintable()
{
    struct Case
    {
        TestTaskControlBlock task;
        bool withName;
        char const* line;
    };
    Case const cases[] = {
        {{1U, "bsp"}, true, "\r\nSAFETY RESET (STACK_OVERFLOW, task bsp, context 1): outputs off\r\n"},
        {{2U, {'a', '\x1b', 'b', '\xff', 'c', '\0'}}, true,
         "\r\nSAFETY RESET (STACK_OVERFLOW, task a?b?c, context 2): outputs off\r\n"},
        {{3U, ""}, true, "\r\nSAFETY RESET (STACK_OVERFLOW, task ?, context 3): outputs off\r\n"},
        {{5U, "sysadmin"}, false, "\r\nSAFETY RESET (STACK_OVERFLOW, task ?, context 5): outputs off\r\n"},
    };
    for (Case const& c : cases)
    {
        (void)fresh();
        TestTaskControlBlock task = c.task;
        End const end = run([&] { vApplicationStackOverflowHook(&task, c.withName ? task.name : nullptr); });
        CHECK(end == End::RESET);
        CHECK(::test_trace::console == c.line);
        if (::test_trace::console != c.line)
        {
            std::printf("  name case: line '%s'\n", ::test_trace::console.c_str());
        }
    }
    // a name without its terminating 0 within the kernel's buffer: at most 15 characters, the line stays whole
    (void)fresh();
    char unterminated[40];
    for (char& ch : unterminated)
    {
        ch = 'x';
    }
    unterminated[39] = '\0';
    TestTaskControlBlock task{7U, "swc"};
    End const end = run([&] { vApplicationStackOverflowHook(&task, unterminated); });
    CHECK(end == End::RESET);
    CHECK(::test_trace::console
          == "\r\nSAFETY RESET (STACK_OVERFLOW, task xxxxxxxxxxxxxxx, context 7): outputs off\r\n");
    CHECK(::test_trace::console.size() < ::safety::SAFE_CONSOLE_LINE_SIZE);
}

/** R1: a transmitter that does not get idle costs the line, never the reaction. */
void testStackOverflowWithDeadConsoleStillResets()
{
    (void)fresh();
    ::test_trace::transmitterIdle = false;
    TestTaskControlBlock task{4U, "can"};
    End const end = run([&] { vApplicationStackOverflowHook(&task, task.name); });
    CHECK(end == End::RESET);
    CHECK(::test_trace::trace == std::string("O") + SAFE + "FR");
    CHECK(::test_trace::console.empty());
    CHECK(record.pending == (rl::CAUSE_EVENT | 0x15U));
}

// ----------------------------------------------------------------------------------------------------- R2

// A kernel function with one check, as the kernel sources use configASSERT (the real macro of the family).
__attribute__((noinline)) void kernelCheck(bool const ok) { configASSERT(ok); } constexpr uint32_t CHECK_LINE = __LINE__;

/** R2: a failed kernel check is RTOS_ASSERT - S1 + S2 with the line naming the code address of the call (inside the
 * checking function) and its source line; a check that holds costs nothing. */
void testFailedAssertionEndsInTheSafeState()
{
    auto& supervisor = fresh();
    End const passing = run([] { kernelCheck(true); });
    CHECK(passing == End::RETURNED);
    CHECK(::test_trace::trace.empty());

    (void)fresh();
    End const end = run([] { kernelCheck(false); });
    CHECK(end == End::RESET);
    CHECK(::test_trace::trace == std::string("O") + SAFE + "FPFR");
    CHECK(::test_trace::printedInShell);
    CHECK(::test_trace::printedLocked);
    CHECK(::test_trace::markedAtFlush);
    CHECK(record.pending == (rl::CAUSE_EVENT | 0x16U)); // "last cause 0x116" at the next start
    CHECK(supervisor.inLimpHomeState());

    // "\r\nSAFETY RESET (RTOS_ASSERT, at 0x<call site>, line <CHECK_LINE>): outputs off\r\n"
    std::string const& line  = ::test_trace::console;
    std::string const prefix = "\r\nSAFETY RESET (RTOS_ASSERT, at 0x";
    std::string const suffix = ", line " + std::to_string(CHECK_LINE) + "): outputs off\r\n";
    bool const shape         = (line.compare(0U, prefix.size(), prefix) == 0) && (line.size() > (prefix.size() + suffix.size()))
                       && (line.compare(line.size() - suffix.size(), suffix.size(), suffix) == 0);
    CHECK(shape);
    if (shape)
    {
        std::string const hex = line.substr(prefix.size(), line.size() - prefix.size() - suffix.size());
        auto const site       = static_cast<uint32_t>(std::strtoul(hex.c_str(), nullptr, 16));
        auto const function   = static_cast<uint32_t>(reinterpret_cast<uintptr_t>(&kernelCheck));
        CHECK(site == supervisor.rtosAssertMonitor.getContext());
        CHECK((site - function) < 256U); // the call lies inside the checking function
    }
    else
    {
        std::printf("  assertion: line '%s'\n", line.c_str());
    }
}

// ------------------------------------------------------------------------------------ fault exception handlers

/** From SYS-59: the fault exception handlers write their line between two flushes (no printf) and reset (SYS-98:
 * the hard fault no longer runs the ECC check, whose log line the reset discarded); on a dead transmitter the line is skipped. None of them runs the supervisor (the outputs went off in
 * customHardFaultHandler, in assembler, before the dump). */
void testFaultHandlersWriteTheirLineAndReset()
{
    struct Case
    {
        void (*handler)();
        char const* line;
        char const* trace;
    };
    Case const cases[] = {
        {&HardFault_Handler_Final, "\r\nHARD FAULT RESET: outputs off, dump saved\r\n", "FPFR"},
        {&BusFault_Handler, "\r\nHARD FAULT RESET: bus fault, no dump\r\n", "FPFR"},
        {&UsageFault_Handler, "\r\nHARD FAULT RESET: usage fault, no dump\r\n", "FPFR"},
    };
    for (Case const& c : cases)
    {
        (void)fresh();
        End const end = run(c.handler);
        CHECK(end == End::RESET);
        CHECK(::test_trace::trace == c.trace);
        CHECK(::test_trace::console == c.line);
        if ((::test_trace::trace != c.trace) || (::test_trace::console != c.line))
        {
            std::printf("  fault handler: trace '%s', line '%s'\n", ::test_trace::trace.c_str(),
                        ::test_trace::console.c_str());
        }

        (void)fresh();
        ::test_trace::transmitterIdle = false;
        End const dead = run(c.handler);
        CHECK(dead == End::RESET);
        CHECK(::test_trace::trace == "FR");
        CHECK(::test_trace::console.empty());
    }
}

// ------------------------------------------------------------------ SYS-169: line of the watchdog path

/** SYS-169: the watchdog's pre-reset interrupt (dump of vector WDOG_EWM_IRQn + 16 = 38) takes the hard fault path;
 * its line says WATCHDOG RESET. Every other record - hard fault (3), no valid dump (0), another vector - keeps
 * HARD FAULT RESET, and so does the line of the stack overflow (the guard record decides before the dump). The
 * order of the line (flush, line, flush, reset) and the dead transmitter are the same as for every fault line. */
void testWatchdogLine()
{
    char const* const watchdogLine = "\r\nWATCHDOG RESET: outputs off, dump saved\r\n";
    char const* const faultLine    = "\r\nHARD FAULT RESET: outputs off, dump saved\r\n";
    struct Case
    {
        uint32_t exception;
        char const* line;
    };
    Case const cases[] = {
        {38U, watchdogLine}, // the watchdog's vector: S32K1 IRQ 22 + 16
        {3U, faultLine},     // hard fault
        {0U, faultLine},     // no valid dump (getDumpException is 0): a doubtful record stays a fault
        {37U, faultLine},    // the neighbours of the watchdog's vector
        {39U, faultLine},
        {22U, faultLine}, // the IRQ number without the offset of 16
    };
    for (Case const& c : cases)
    {
        (void)fresh();
        ::test_dump::exception = c.exception;
        End const end          = run(&HardFault_Handler_Final);
        CHECK(end == End::RESET);
        CHECK(::test_trace::trace == "FPFR");
        CHECK(::test_trace::console == c.line);
        if (::test_trace::console != c.line)
        {
            std::printf("  exception %u: line '%s'\n", static_cast<unsigned>(c.exception),
                        ::test_trace::console.c_str());
        }
        // dead transmitter: no line of either kind, still the reset
        (void)fresh();
        ::test_dump::exception        = c.exception;
        ::test_trace::transmitterIdle = false;
        CHECK(run(&HardFault_Handler_Final) == End::RESET);
        CHECK(::test_trace::trace == "FR");
        CHECK(::test_trace::console.empty());
    }
    // the stack overflow of the interrupt stack keeps its own line, whatever the dump says
    (void)fresh();
    ::test_dump::exception = 38U;
    nxIsrStackOverflowMsp  = 0x200047D0U;
    CHECK(run(&HardFault_Handler_Final) == End::RESET);
    CHECK(::test_trace::console == "\r\nHARD FAULT RESET: interrupt stack overflow (guard), outputs off\r\n");
    nxIsrStackOverflowMsp = 0U;
    // bus and usage fault never ran the dump: their lines do not depend on it
    (void)fresh();
    ::test_dump::exception = 38U;
    CHECK(run(&BusFault_Handler) == End::RESET);
    CHECK(::test_trace::console == "\r\nHARD FAULT RESET: bus fault, no dump\r\n");
}

// ------------------------------------------------------------------ SYS-97: overflow of the interrupt stack

/** HardFault_Handler records an MSP below the stack limit (an overflow into the guard) before the dump; the final
 * part of the hard fault then writes the overflow line instead of the dump line - one line, between two flushes,
 * and the reset. Without a record the dump line stays. */
void testInterruptStackOverflowLine()
{
    (void)fresh();
    nxIsrStackOverflowMsp = 0x200047D0U; // 16 bytes into a guard ending at 0x200047E0
    End const end = run(&HardFault_Handler_Final);
    CHECK(end == End::RESET);
    CHECK(::test_trace::trace == "FPFR");
    CHECK(::test_trace::console == "\r\nHARD FAULT RESET: interrupt stack overflow (guard), outputs off\r\n");
    if (::test_trace::console != "\r\nHARD FAULT RESET: interrupt stack overflow (guard), outputs off\r\n")
    {
        std::printf("  overflow: trace '%s', line '%s'\n", ::test_trace::trace.c_str(), ::test_trace::console.c_str());
    }
    // dead transmitter: no line, still the reset
    (void)fresh();
    ::test_trace::transmitterIdle = false;
    CHECK(run(&HardFault_Handler_Final) == End::RESET);
    CHECK(::test_trace::trace == "FR");
    // no record: the ordinary line
    (void)fresh();
    nxIsrStackOverflowMsp = 0U;
    CHECK(run(&HardFault_Handler_Final) == End::RESET);
    CHECK(::test_trace::console == "\r\nHARD FAULT RESET: outputs off, dump saved\r\n");
}

/** The fill pattern and its high-water mark (os/IsrStack.h) on a plain array standing in for the MSP. */
void testInterruptStackFillPattern()
{
    uint32_t stack[64] = {};
    uint32_t* const limit = &stack[0];
    uint32_t* const top   = &stack[64];
    ::isrstack::fillWords(limit, top);
    CHECK(::isrstack::untouchedBytes(limit, top) == 256U); // nothing used
    stack[63] = 0U;                                        // one push at the top
    CHECK(::isrstack::untouchedBytes(limit, top) == 252U);
    stack[40] = 0x12345678U; // the deepest write decides, not the number of changed words
    CHECK(::isrstack::untouchedBytes(limit, top) == 160U);
    stack[0] = 0U; // the lowest word: the stack reached its limit
    CHECK(::isrstack::untouchedBytes(limit, top) == 0U);
    // a pushed value equal to the pattern hides one word at most - it is counted as untouched, the next deeper
    // write still ends the count (the pattern is a counter-check, the bound is tools/stackuse)
    ::isrstack::fillWords(limit, top);
    stack[20] = 1U;
    CHECK(::isrstack::untouchedBytes(limit, top) == 80U);
    // fill of an empty range writes nothing
    stack[0] = 7U;
    ::isrstack::fillWords(limit, limit);
    CHECK(stack[0] == 7U);
}
} // namespace

int main()
{
    testStackOverflowEndsInTheSafeState();
    testStackOverflowNameIsBoundedAndPrintable();
    testStackOverflowWithDeadConsoleStillResets();
    testFailedAssertionEndsInTheSafeState();
    testFaultHandlersWriteTheirLineAndReset();
    testWatchdogLine(); // SYS-169
    testInterruptStackOverflowLine();
    testInterruptStackFillPattern();

    std::printf("%d checks, %d failures\n", checks, failures);
    return failures == 0 ? 0 : 1;
}
