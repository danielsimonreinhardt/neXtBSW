/* SYS-23: host test of the reactions of the safe supervisor (safeSupervisor/SafeSupervisor.cpp, the real code).
 *
 * What it shows (finding F1 of doc/safety/concept.md):
 * - WATCHDOG_STARTUP_CHECK_FAILURE (SM-07) and ROM_CHECK_FAILURE (SM-14) no longer return without a reaction: the
 *   supervisor enters limp home and holds the safe state S1 **without a reset** - the safe outputs table is walked
 *   in every round of the hold, the MCU watchdog is serviced in every round (no watchdog reset ends the hold and
 *   restarts the same image), all interrupts stay suspended, and softwareSystemReset() is never called.
 * - The events keep their other reactions: every resetting event walks the safe outputs table *before* the reset
 *   (SYS-06), RTE_TIMING_VIOLATION stays report-only (SM-05, S3), CHECK_REGISTER_ERROR enters or leaves S1 without
 *   a reset (SM-22).
 * - No event of the supervisor ends without a reaction except the ones that do so by decision (SM-05, SM-22, and
 *   DEVICE_REPORT of SYS-29).
 * - SYS-29 (gap G5, SM-34, finding F5): the device context word; DEVICE_REPORT is S3 for every word, also through
 *   the DeviceReportBox of a package (malformed words refused); DEVICE_FAULT holds for IDENTITY and resets for every
 *   other class and a malformed word; communication faults of two devices on one bus within the window are one
 *   "bus lost" event, per bus, with an inclusive window, 8 tracked buses, and the verdict read by the RTE reader
 *   (DeviceReportRte.cpp). Checked against 9 built-in faults in SafeSupervisor.cpp (all detected, SYS-29).
 *
 * - SYS-43 (finding F13 / task T4 of doc/safety/fmea.md, SSR-065): the reset-loop limit (safeSupervisor/
 *   ResetLoopLimit.h) - the record rules (power-on clears, a marked reset, a watchdog reset and a fault exception
 *   count, every other reset keeps the count, random or overwritten content counts from 0, saturation), the mark
 *   before every reset of an S1 + S2 reaction and never before a hold or a report, the hold without reset from the
 *   limit on (also after a reset that is no safety reset: only a power cycle ends it), the clearing after the
 *   fault-free time counted in safety cycles, and the console line of the hold. Checked against built-in faults
 *   (doc/changelog.d/SYS-43.md).
 * - SYS-52: SAFETY_CYCLE_PERIOD_VIOLATION writes one synchronous console line with kind and value after the safe
 *   outputs and before the reset (its asynchronous log line is lost with the reset), inside the interrupt lock.
 * - SYS-80 (findings R1/R2 of doc/safety/rtos-comparison.md): STACK_OVERFLOW and RTOS_ASSERT, reported by the kernel
 *   hooks of the family port, reset like every S1 + S2 event (line with task name and context resp. code address and
 *   line, own reset-loop cause) but without a log line; the task name reaches the line printable and bounded.
 * - SYS-98 (findings STK-3/STK-4 of doc/safety/sw-architecture.md, SYS-83): a failed ETL_ASSERT (etlAssertFailed,
 *   the handler for etl::set_assert_function) is an S1 + S2 reaction of its own - safe outputs, mark with cause
 *   0x117, SAFETY RESET line with the address, reset - without a log line and without setting limp home (it only
 *   reads the supervisor); the kernel faults reach their reaction without handle() (KernelFaultReaction).
 * - SYS-59 (board 2026-09-22, captures 144-148: no line after the deadman and watchdog configuration errors): every
 *   S1 + S2 reaction writes its SAFETY RESET line with the event and its context, in the order safe outputs, mark of
 *   the reset-loop record, console flush (F), line (P), flush (F), reset; a console that does not get idle costs the
 *   line, never the reset.
 *
 * Built twice (tools/test/tests.yaml): safe-supervisor with PLATFORM_SUPPORT_IO and PLATFORM_SUPPORT_WATCHDOG
 * like the S32K1 boards, safe-supervisor-bare without both - a board without power outputs and without a
 * watchdog driver of the family, where the hold still has to hold (it must not fall through to a reset).
 *
 * The hold loop has no end on the target; here NX_SAFE_SUPERVISOR_HOLDING() ends it after HOLD_ROUNDS rounds,
 * so handle() returns and the trace can be checked. The stubs under stubs/ record every effect as one character:
 * E log error, w log warning, S enter safe state, L leave safe state, W watchdog service, R reset, P the
 * synchronous console line of the hold or of a reset (NX_SAFE_SUPERVISOR_CONSOLE, its text is kept), F the console
 * flush of the family port (NX_SAFE_SUPERVISOR_RESET_FLUSH, SYS-59).
 *
 * Build and run (zig c++, no hardware):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -DPLATFORM_SUPPORT_IO -DPLATFORM_SUPPORT_WATCHDOG \
 *       -Iecu/safety/safeSupervisor/include -Iecu/safety/safeSupervisor/test/host/stubs \
 *       -isystem external/openbsw/libs/safety/safeMonitor/include \
 *       ecu/safety/safeSupervisor/test/host/SafeSupervisorHostTest.cpp -o build/host/safesupervisor.exe
 *   build/host/safesupervisor.exe
 */

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>

namespace test_trace
{
std::string trace;
std::string console; // text of the console lines, formatted as printf would
int lockDepth      = 0;
bool printedLocked = false; // SYS-52: the last console line was written with the interrupt lock taken
bool transmitterIdle = true; // SYS-59: what the console flush of the family port reports
bool markedAtFlush   = false; // SYS-59: the reset-loop record was marked when the first flush of a reset ran
bool flush();                 // SYS-59: defined below, it looks at the record under test

void add(char const c) { trace.push_back(c); }

void print(char const* const format, char const* const event)
{
    add('P');
    printedLocked = (lockDepth > 0);
    char line[160];
    (void)std::snprintf(line, sizeof(line), format, event);
    console += line;
}
} // namespace test_trace

namespace test_hold
{
constexpr int HOLD_ROUNDS = 3;
int left                  = 0;
bool locked               = true; // every round ran with the interrupt lock of handle() taken

bool holding()
{
    locked = locked && (::test_trace::lockDepth > 0);
    if (left <= 0)
    {
        return false;
    }
    --left;
    return true;
}
} // namespace test_hold

#define NX_SAFE_SUPERVISOR_HOLDING()                 (::test_hold::holding())
#define NX_SAFE_SUPERVISOR_CONSOLE(format, event) (::test_trace::print(format, event))
#define NX_SAFE_SUPERVISOR_RESET_FLUSH()             (::test_trace::flush())

extern "C" void softwareSystemReset(void) { ::test_trace::add('R'); }

#include "../../src/safeSupervisor/SafeSupervisor.cpp" // NOLINT: the unit under test
#include "../../src/safeSupervisor/DeviceReportRte.cpp" // NOLINT: SYS-29, the RTE reader of the bus verdict

namespace safety
{
// SYS-37: the box of the deferred RTE monitor; on the target the family port defines it outside the MPU region
SafetyEventMailbox rteTimingMailbox;
} // namespace safety

namespace test_trace
{
::safety::resetloop::Record volatile* watchedRecord = nullptr; // SYS-59: the record whose mark flush() looks at

bool flush()
{
    if (trace.find('F') == std::string::npos)
    {
        markedAtFlush = (watchedRecord != nullptr) && (watchedRecord->pending != ::safety::resetloop::CAUSE_NONE);
    }
    add('F');
    return transmitterIdle;
}
} // namespace test_trace

namespace
{
using ::safety::SafeSupervisor;
using Event = SafeSupervisor::Event;

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

#ifdef PLATFORM_SUPPORT_IO
constexpr char const* SAFE = "S";
#else
constexpr char const* SAFE = "";
#endif
#ifdef PLATFORM_SUPPORT_WATCHDOG
constexpr char const* SERVICE = "W";
#else
constexpr char const* SERVICE = "";
#endif

/** Trace of a hold: log line, safe outputs walk, the console line once, then per round the safe outputs walk
 * and the watchdog service. */
std::string holdTrace()
{
    std::string t = std::string("E") + SAFE + "P";
    for (int i = 0; i < ::test_hold::HOLD_ROUNDS; ++i)
    {
        t += SAFE;
        t += SERVICE;
    }
    return t;
}

/** Trace of a resetting event: log line, safe outputs walk, console flush, the SAFETY RESET line, flush, reset - in
 * this order (SYS-59; SYS-52 had the line for SAFETY_CYCLE_PERIOD_VIOLATION only, without the flushes). */
std::string resetTrace() { return std::string("E") + SAFE + "FPFR"; }

/** SYS-80: the kernel faults (STACK_OVERFLOW, RTOS_ASSERT) reset the same way, but without a log line (the logger
 * would run over memory the fault may have overwritten, and its line is lost with the reset anyway). SYS-98: so does
 * ETL_ASSERT. */
bool isKernelFault(Event const event)
{
    return (event == Event::STACK_OVERFLOW) || (event == Event::RTOS_ASSERT) || (event == Event::ETL_ASSERT);
}

std::string resetTraceOf(Event const event)
{
    return isKernelFault(event) ? (std::string(SAFE) + "FPFR") : resetTrace();
}

/** SYS-59: the same with a console whose transmitter does not get idle - one flush, no line, the reset. */
std::string deadConsoleResetTrace() { return std::string("E") + SAFE + "FR"; }

/** A fresh supervisor (as main() constructs it) that has left limp home, and empty records. */
SafeSupervisor& fresh()
{
    ::safety::safeSupervisorConstructor.construct();
    auto& supervisor = SafeSupervisor::getInstance();
    supervisor.leaveLimpHome();
    (void)::safety::rteTimingMailbox.drain([](uint32_t) {}); // SYS-37: no report left over from a test before
    ::test_trace::trace.clear();
    ::test_trace::console.clear();
    ::test_trace::lockDepth = 0;
    ::test_trace::transmitterIdle = true;
    ::test_trace::markedAtFlush   = false;
    ::test_trace::watchedRecord   = nullptr;
    ::test_hold::left       = ::test_hold::HOLD_ROUNDS;
    ::test_hold::locked     = true;
    return supervisor;
}

/** F1, SM-07: the watchdog self-test failed (raised in main() of the family port before the BSP starts). */
void testWatchdogStartupFailureHoldsTheSafeState()
{
    auto& supervisor = fresh();
    supervisor.watchdogStartupCheckMonitor.trigger();
    CHECK(::test_trace::trace == holdTrace());
    CHECK(::test_trace::trace.find('R') == std::string::npos); // no reset: it would run into the same test
    CHECK(supervisor.inLimpHomeState());
    CHECK(supervisor.holdRounds() == static_cast<uint32_t>(::test_hold::HOLD_ROUNDS));
    CHECK(::test_hold::locked); // nothing else runs during the hold
    CHECK(::test_trace::lockDepth == 0);
}

/** F1, SM-14: the ROM check found an image that is not the linked one (raised in the idle path). */
void testRomCheckFailureHoldsTheSafeState()
{
    auto& supervisor = fresh();
    supervisor.romCheckMonitor.trigger();
    CHECK(::test_trace::trace == holdTrace());
    CHECK(::test_trace::trace.find('R') == std::string::npos); // no reset: the wrong image would start again
    CHECK(supervisor.inLimpHomeState());
    CHECK(supervisor.holdRounds() == static_cast<uint32_t>(::test_hold::HOLD_ROUNDS));
    CHECK(::test_hold::locked);
}

/** A single round: limp home is entered, and the round starts with the safe outputs walk, then the service. */
void testHoldStartsWithTheSafeState()
{
    auto& supervisor      = fresh();
    ::test_hold::left     = 1;
    supervisor.handle(Event::ROM_CHECK_FAILURE);
    CHECK(::test_trace::trace == std::string("E") + SAFE + "P" + SAFE + SERVICE);
    CHECK(supervisor.inLimpHomeState());
}

/** The console line of the hold: written exactly once, before the loop and after the first safe outputs walk,
 * also over many rounds, with the event in it and CR LF at the end (the terminal convention of main()). */
void testHoldWritesOneConsoleLine()
{
    auto& supervisor  = fresh();
    ::test_hold::left = 50;
    supervisor.watchdogStartupCheckMonitor.trigger();
    std::string const& t = ::test_trace::trace;
    CHECK(supervisor.holdRounds() == 50U);
    CHECK(std::count(t.begin(), t.end(), 'P') == 1);
    CHECK(t.find('P') == std::string(std::string("E") + SAFE).size()); // outputs off before the printing
    CHECK(::test_trace::console
          == "SAFE STATE HELD (WATCHDOG_STARTUP_CHECK_FAILURE): outputs off, no reset - power cycle required\r\n");

    auto& again = fresh();
    again.romCheckMonitor.trigger();
    CHECK(std::count(::test_trace::trace.begin(), ::test_trace::trace.end(), 'P') == 1);
    CHECK(::test_trace::console
          == "SAFE STATE HELD (ROM_CHECK_FAILURE): outputs off, no reset - power cycle required\r\n");

    // No hold line on any other path: a resetting event writes its SAFETY RESET line (SYS-59), a report-only event
    // nothing
    auto& other = fresh();
    other.mpuConfigMonitor.trigger(3U);
    CHECK(::test_trace::console == "\r\nSAFETY RESET (MPU_CONFIGURATION_ERROR, region 3): outputs off\r\n");
    ::test_trace::console.clear();
    other.rteTimingMonitor.trigger(1U);
    other.deliverDeferredEvents();
    CHECK(::test_trace::console.empty());
}

/** Counter-example: a resetting event still walks the safe outputs table first and then resets, no hold. */
void testResettingEventWalksTheTableBeforeTheReset()
{
    auto& supervisor = fresh();
    supervisor.mpuEnableMonitor.trigger();
    CHECK(::test_trace::trace == resetTrace());
    CHECK(supervisor.inLimpHomeState());
    CHECK(supervisor.holdRounds() == 0U);
    CHECK(::test_trace::trace.find('W') == std::string::npos);
}

/** SYS-52 (board check B7(a) of doc/safety/fmea.md): the period violation resets like before, but writes one
 * synchronous console line with kind and value first - after the safe outputs and the mark of the reset-loop record
 * (SYS-59: the mark moved before the line), before the reset. Kind 3 with the fields 0x2 is the stopped time base
 * (LPTMR0_CSR[TEN] cleared). */
void testPeriodViolationWritesItsLineBeforeTheReset()
{
    ::safety::resetloop::Record volatile record{};
    ::safety::resetloop::clear(record);
    ::safety::resetloop::Start const start{};
    auto& supervisor = fresh();
    (void)supervisor.startResetLoopLimit(record, start, 3U, 100U);
    ::test_trace::watchedRecord = &record;
    ::test_trace::trace.clear();
    supervisor.safetyCyclePeriodMonitor.trigger(0x03000002U); // kind 3, fields 0x2
    CHECK(::test_trace::trace == resetTrace());
    CHECK(::test_trace::console
          == "\r\nSAFETY RESET (SAFETY_CYCLE_PERIOD_VIOLATION, kind 3, value 2): outputs off\r\n");
    CHECK(::test_trace::printedLocked); // inside the interrupt lock of handle(), like the reset after it
    CHECK(::test_trace::markedAtFlush); // SYS-59: marked before the console output starts
    CHECK(record.pending
          == (::safety::resetloop::CAUSE_EVENT
              | static_cast<uint32_t>(Event::SAFETY_CYCLE_PERIOD_VIOLATION))); // cause 0x111 at the next start
    CHECK(supervisor.inLimpHomeState());

    // Kind 1 with the largest value of the context and kind 2 with 0 ticks (stopped counter)
    auto& again = fresh();
    again.safetyCyclePeriodMonitor.trigger(0x01FFFFFFU);
    CHECK(::test_trace::console
          == "\r\nSAFETY RESET (SAFETY_CYCLE_PERIOD_VIOLATION, kind 1, value 16777215): outputs off\r\n");
    auto& third = fresh();
    third.safetyCyclePeriodMonitor.trigger(0x02000000U);
    CHECK(::test_trace::console
          == "\r\nSAFETY RESET (SAFETY_CYCLE_PERIOD_VIOLATION, kind 2, value 0): outputs off\r\n");
    CHECK(::test_trace::console.size() < ::safety::SAFE_CONSOLE_LINE_SIZE);
}

/** SYS-59: the line of every resetting event (the whole S1 + S2 list of handle()), with its context in the words
 * of its log line. The deadman and watchdog cases are the board checks P1-P3 of SYS-51 (fields 0x10, 0x4, 0x40,
 * 0x2 of the deadman, 0x2 of the watchdog interrupt), which reset without a line before SYS-59 (captures 144-148). */
void testEveryResetWritesItsLine()
{
    struct Case
    {
        Event event;
        uint32_t context;
        char const* text;
    };
    Case const cases[] = {
        {Event::SAFETY_MANAGER_SEQUENCE_DEVIATION, 0U, "SAFETY_MANAGER_SEQUENCE_DEVIATION"},
        {Event::SAFE_WATCHDOG_SEQUENCE_DEVIATION, 0U, "SAFE_WATCHDOG_SEQUENCE_DEVIATION"},
        {Event::SAFE_WATCHDOG_CONFIGURATION_ERROR, 0x2U, "SAFE_WATCHDOG_CONFIGURATION_ERROR, fields 0x2"},
        {Event::SAFE_WATCHDOG_CONFIGURATION_ERROR, 0x1FU, "SAFE_WATCHDOG_CONFIGURATION_ERROR, fields 0x1f"},
        {Event::SAFE_WATCHDOG_SERVICE_DEVIATION, 0U, "SAFE_WATCHDOG_SERVICE_DEVIATION"},
        {Event::MPU_UNLOCKED_ON_SAFETY_MANAGER_ENTRY, 0U, "MPU_UNLOCKED_ON_SAFETY_MANAGER_ENTRY"},
        {Event::MPU_LOCKED_ON_SAFETY_MANAGER_EXIT, 0U, "MPU_LOCKED_ON_SAFETY_MANAGER_EXIT"},
        {Event::INTEGRITY_EVENT_DMA_ECC_ERROR, 0x5U, "INTEGRITY_EVENT_DMA_ECC_ERROR, sources 0x5"},
        {Event::MPU_NOT_ENABLED, 0U, "MPU_NOT_ENABLED"},
        {Event::MPU_CONFIGURATION_ERROR, 7U, "MPU_CONFIGURATION_ERROR, region 7"},
        {Event::ADC_REFERENCE_ERROR, 2U, "ADC_REFERENCE_ERROR, context 2"},
        {Event::INTERNAL_SUPPLIES_ERROR, 1U, "INTERNAL_SUPPLIES_ERROR, context 1"},
        {Event::LOCK_REGISTER_ERROR, 12U, "LOCK_REGISTER_ERROR, register 12"},
        {Event::DEADMAN_CONFIGURATION_ERROR, 0x10U, "DEADMAN_CONFIGURATION_ERROR, fields 0x10"},
        {Event::DEADMAN_CONFIGURATION_ERROR, 0x4U, "DEADMAN_CONFIGURATION_ERROR, fields 0x4"},
        {Event::DEADMAN_CONFIGURATION_ERROR, 0x40U, "DEADMAN_CONFIGURATION_ERROR, fields 0x40"},
        {Event::DEADMAN_CONFIGURATION_ERROR, 0x2U, "DEADMAN_CONFIGURATION_ERROR, fields 0x2"},
        {Event::DEADMAN_CONFIGURATION_ERROR, 0xFFFFFFFFU, "DEADMAN_CONFIGURATION_ERROR, fields 0xffffffff"},
        {Event::SAFETY_CYCLE_PERIOD_VIOLATION, 0x0100000FU, "SAFETY_CYCLE_PERIOD_VIOLATION, kind 1, value 15"},
        {Event::DEVICE_FAULT, 0x2107075AU, "DEVICE_FAULT, word 0x2107075a"},
    };
    for (Case const& c : cases)
    {
        ::safety::resetloop::Record volatile record{};
        ::safety::resetloop::clear(record);
        auto& supervisor = fresh();
        (void)supervisor.startResetLoopLimit(record, ::safety::resetloop::Start{}, 3U, 100U);
        ::test_trace::watchedRecord = &record;
        ::test_trace::trace.clear();
        switch (c.event)
        {
            case Event::SAFE_WATCHDOG_CONFIGURATION_ERROR: supervisor.safeWatchdogConfigMonitor.check(false, c.context); break;
            case Event::INTEGRITY_EVENT_DMA_ECC_ERROR: supervisor.dmaEccMonitor.trigger(c.context); break;
            case Event::MPU_CONFIGURATION_ERROR: supervisor.mpuConfigMonitor.trigger(c.context); break;
            case Event::ADC_REFERENCE_ERROR: supervisor.adcReferenceMonitor.trigger(c.context); break;
            case Event::INTERNAL_SUPPLIES_ERROR: supervisor.internalSuppliesMonitor.trigger(c.context); break;
            case Event::LOCK_REGISTER_ERROR: supervisor.lockRegisterMonitor.trigger(c.context); break;
            case Event::DEADMAN_CONFIGURATION_ERROR: supervisor.deadmanConfigMonitor.trigger(c.context); break;
            case Event::SAFETY_CYCLE_PERIOD_VIOLATION: supervisor.safetyCyclePeriodMonitor.trigger(c.context); break;
            case Event::DEVICE_FAULT: supervisor.deviceFaultMonitor.trigger(c.context); break;
            default: supervisor.handle(c.event); break;
        }
        std::string const expected = std::string("\r\nSAFETY RESET (") + c.text + "): outputs off\r\n";
        CHECK(::test_trace::console == expected);
        CHECK(::test_trace::trace == resetTrace());
        CHECK(::test_trace::printedLocked);
        CHECK(::test_trace::markedAtFlush);
        CHECK(record.pending == (::safety::resetloop::CAUSE_EVENT | ::etl::to_underlying(c.event)));
        CHECK(::test_trace::console.size() < ::safety::SAFE_CONSOLE_LINE_SIZE);
        if ((::test_trace::console != expected) || (::test_trace::trace != resetTrace()))
        {
            std::printf("  event %u: trace '%s', line '%s'\n", static_cast<unsigned>(c.event),
                        ::test_trace::trace.c_str(), ::test_trace::console.c_str());
        }
    }

    // the longest event name with the longest context still fits the line buffer whole (nothing shortened)
    char const* const longest = "MPU_UNLOCKED_ON_SAFETY_MANAGER_ENTRY, fields 0xffffffff";
    CHECK((std::strlen("\r\nSAFETY RESET (") + std::strlen(longest) + std::strlen("): outputs off\r\n"))
          < ::safety::SAFE_CONSOLE_LINE_SIZE);

    // an unknown event id (default branch) names its id
    auto& unknown = fresh();
    unknown.handle(static_cast<Event>(0xEEU));
    CHECK(::test_trace::console == "\r\nSAFETY RESET (unknown event, id 238): outputs off\r\n");
}

/** SYS-80 (findings R1/R2 of doc/safety/rtos-comparison.md): a stack overflow and a failed kernel assertion, as the
 * family port reports them from its kernel hooks, end in S1 + S2 with their SAFETY RESET line, in the order of
 * SYS-59, marked with their own cause, and without a log line. The task name reaches the line only as printable ASCII
 * and bounded (it comes from kernel data next to an overflowed stack). */
void testKernelFaultsResetWithTheirLine()
{
    struct Case
    {
        Event event;
        char const* name; // STACK_OVERFLOW: the task name as the kernel hands it over
        uint32_t context;
        uint32_t line;    // RTOS_ASSERT: source line of the check
        char const* text;
    };
    char const longName[] = "background_long_name";
    Case const cases[] = {
        {Event::STACK_OVERFLOW, "can", 4U, 0U, "STACK_OVERFLOW, task can, context 4"},
        {Event::STACK_OVERFLOW, "safety", 6U, 0U, "STACK_OVERFLOW, task safety, context 6"},
        {Event::STACK_OVERFLOW, "a\x01" "b\x7f" "c", 2U, 0U, "STACK_OVERFLOW, task a?b?c, context 2"},
        {Event::STACK_OVERFLOW, "\xe4" "bc", 1U, 0U, "STACK_OVERFLOW, task ?bc, context 1"},
        {Event::STACK_OVERFLOW, "", 3U, 0U, "STACK_OVERFLOW, task ?, context 3"},
        {Event::STACK_OVERFLOW, nullptr, 3U, 0U, "STACK_OVERFLOW, task ?, context 3"},
        {Event::STACK_OVERFLOW, longName, 0xFFFFFFFFU, 0U, "STACK_OVERFLOW, task background_long, context 4294967295"},
        {Event::RTOS_ASSERT, nullptr, 0x0001A328U, 1234U, "RTOS_ASSERT, at 0x1a328, line 1234"},
        {Event::RTOS_ASSERT, nullptr, 0U, 0U, "RTOS_ASSERT, at 0x0, line 0"},
    };
    for (Case const& c : cases)
    {
        ::safety::resetloop::Record volatile record{};
        ::safety::resetloop::clear(record);
        auto& supervisor = fresh();
        (void)supervisor.startResetLoopLimit(record, ::safety::resetloop::Start{}, 3U, 100U);
        ::test_trace::watchedRecord = &record;
        ::test_trace::trace.clear();
        if (c.event == Event::STACK_OVERFLOW)
        {
            supervisor.reportStackOverflow(c.name, c.context);
        }
        else
        {
            supervisor.reportRtosAssert(c.context, c.line);
        }
        std::string const expected = std::string("\r\nSAFETY RESET (") + c.text + "): outputs off\r\n";
        CHECK(::test_trace::console == expected);
        CHECK(::test_trace::trace == resetTraceOf(c.event)); // S1, flush, line, flush, reset - no log line
        CHECK(::test_trace::printedLocked);
        CHECK(::test_trace::markedAtFlush);
        CHECK(record.pending == (::safety::resetloop::CAUSE_EVENT | ::etl::to_underlying(c.event)));
        CHECK(supervisor.inLimpHomeState());
        CHECK(::test_trace::console.size() < ::safety::SAFE_CONSOLE_LINE_SIZE);
        if ((::test_trace::console != expected) || (::test_trace::trace != resetTraceOf(c.event)))
        {
            std::printf("  kernel fault: trace '%s', line '%s'\n", ::test_trace::trace.c_str(),
                        ::test_trace::console.c_str());
        }
    }
    // the causes the next start reports ("last cause 0x115" / "0x116")
    CHECK((::safety::resetloop::CAUSE_EVENT | ::etl::to_underlying(Event::STACK_OVERFLOW)) == 0x115U);
    CHECK((::safety::resetloop::CAUSE_EVENT | ::etl::to_underlying(Event::RTOS_ASSERT)) == 0x116U);

    // a dead console costs the line, never the reaction
    ::safety::resetloop::Record volatile record{};
    ::safety::resetloop::clear(record);
    auto& supervisor = fresh();
    (void)supervisor.startResetLoopLimit(record, ::safety::resetloop::Start{}, 3U, 100U);
    ::test_trace::watchedRecord   = &record;
    ::test_trace::transmitterIdle = false;
    ::test_trace::trace.clear();
    supervisor.reportStackOverflow("can", 4U);
    CHECK(::test_trace::trace == std::string(SAFE) + "FR");
    CHECK(::test_trace::console.empty());
    CHECK(record.pending == (::safety::resetloop::CAUSE_EVENT | ::etl::to_underlying(Event::STACK_OVERFLOW)));
}

/** SYS-98 (STK-3, SYS-83): a failed ETL_ASSERT, through the handler registered with etl::set_assert_function, is
 * an S1 + S2 reaction with its own line and cause - in the order of SYS-59, inside the interrupt lock, without a log
 * line - and leaves the limp-home flag alone (reportEtlAssert() is const: it may run in a context without write
 * access to the protected RAM, see SafetyContextMpuHostTest.cpp). */
void testEtlAssertResetsWithItsLine()
{
    ::safety::resetloop::Record volatile record{};
    ::safety::resetloop::clear(record);
    auto& supervisor = fresh();
    (void)supervisor.startResetLoopLimit(record, ::safety::resetloop::Start{}, 3U, 100U);
    ::test_trace::watchedRecord = &record;
    ::test_trace::trace.clear();
    supervisor.reportEtlAssert(0x0001A328U);
    CHECK(::test_trace::console == "\r\nSAFETY RESET (ETL_ASSERT, at 0x1a328): outputs off\r\n");
    CHECK(::test_trace::trace == resetTraceOf(Event::ETL_ASSERT)); // S1, flush, line, flush, reset - no log line
    CHECK(::test_trace::printedLocked);
    CHECK(::test_trace::markedAtFlush);
    CHECK(::test_trace::lockDepth == 0);
    CHECK(record.pending == (::safety::resetloop::CAUSE_EVENT | ::etl::to_underlying(Event::ETL_ASSERT)));
    CHECK((::safety::resetloop::CAUSE_EVENT | ::etl::to_underlying(Event::ETL_ASSERT)) == 0x117U);
    CHECK(!supervisor.inLimpHomeState());

    // the handler itself: the address of its caller, then the reset; on the host the reset returns, so the
    // handler's own fallback reset follows
    auto& handled = fresh();
    (void)handled.startResetLoopLimit(record, ::safety::resetloop::Start{}, 3U, 100U);
    ::test_trace::watchedRecord = &record;
    ::test_trace::trace.clear();
    void (*const handler)(::etl::exception const&) = &::safety::etlAssertFailed; // as the ETL calls it
    handler(::etl::exception{});
    std::string const prefix = "\r\nSAFETY RESET (ETL_ASSERT, at 0x";
    CHECK(::test_trace::console.compare(0U, prefix.size(), prefix) == 0);
    CHECK(::test_trace::console.find("0x0)") == std::string::npos); // a real return address, not 0
    CHECK(::test_trace::trace == (resetTraceOf(Event::ETL_ASSERT) + "R"));
    CHECK(record.pending == (::safety::resetloop::CAUSE_EVENT | ::etl::to_underlying(Event::ETL_ASSERT)));

    // before startResetLoopLimit() (an assert before main() registered the record): the same reset, unmarked
    ::safety::resetloop::clear(record);
    auto& early = fresh();
    early.reportEtlAssert(0U);
    CHECK(::test_trace::console == "\r\nSAFETY RESET (ETL_ASSERT, at 0x0): outputs off\r\n");
    CHECK(::test_trace::trace == resetTraceOf(Event::ETL_ASSERT));
    CHECK(record.pending == ::safety::resetloop::CAUSE_NONE);

    // a dead console costs the line, never the reaction
    auto& dead = fresh();
    (void)dead.startResetLoopLimit(record, ::safety::resetloop::Start{}, 3U, 100U);
    ::test_trace::transmitterIdle = false;
    ::test_trace::trace.clear();
    dead.reportEtlAssert(0x100U);
    CHECK(::test_trace::trace == std::string(SAFE) + "FR");
    CHECK(::test_trace::console.empty());
    CHECK(record.pending == (::safety::resetloop::CAUSE_EVENT | ::etl::to_underlying(Event::ETL_ASSERT)));
}

/** SYS-98 (STK-4): a non-correctable ECC error from a fault interrupt - the line and cause of the poll's event in
 * handle(), without its log line, limp home left alone (const, like reportEtlAssert). */
void testEccErrorFromInterruptResetsWithoutLog()
{
    ::safety::resetloop::Record volatile record{};
    ::safety::resetloop::clear(record);
    auto& supervisor = fresh();
    (void)supervisor.startResetLoopLimit(record, ::safety::resetloop::Start{}, 3U, 100U);
    ::test_trace::watchedRecord = &record;
    ::test_trace::trace.clear();
    supervisor.reportEccError(0x1U);
    std::string const line = "\r\nSAFETY RESET (INTEGRITY_EVENT_DMA_ECC_ERROR, sources 0x1): outputs off\r\n";
    CHECK(::test_trace::console == line);
    CHECK(::test_trace::trace == std::string(SAFE) + "FPFR"); // no log line
    CHECK(::test_trace::printedLocked);
    CHECK(::test_trace::markedAtFlush);
    CHECK(record.pending
          == (::safety::resetloop::CAUSE_EVENT | ::etl::to_underlying(Event::INTEGRITY_EVENT_DMA_ECC_ERROR)));
    CHECK(!supervisor.inLimpHomeState());

    // the same line as the poll's report through handle() (which logs first)
    auto& polled = fresh();
    polled.dmaEccMonitor.trigger(0x1U);
    CHECK(::test_trace::console == line);
    CHECK(::test_trace::trace == resetTrace());
}

/** SYS-98 (STK-4): the kernel faults reach their reaction through their own handler, not handle() - the monitors
 * keep their context (the family port's test reads it) and handle() forwards the two events to the same reaction. */
void testKernelFaultsBypassHandle()
{
    auto& supervisor = fresh();
    supervisor.rtosAssertMonitor.trigger(0x2000U);
    CHECK(supervisor.rtosAssertMonitor.getContext() == 0x2000U);
    CHECK(::test_trace::console == "\r\nSAFETY RESET (RTOS_ASSERT, at 0x2000, line 0): outputs off\r\n");
    CHECK(::test_trace::trace == resetTraceOf(Event::RTOS_ASSERT));
    CHECK(supervisor.inLimpHomeState());
    CHECK(::test_trace::lockDepth == 0);

    auto& forwarded = fresh();
    forwarded.handle(Event::STACK_OVERFLOW); // context 0, no name reported
    CHECK(::test_trace::console == "\r\nSAFETY RESET (STACK_OVERFLOW, task ?, context 0): outputs off\r\n");
    CHECK(::test_trace::trace == resetTraceOf(Event::STACK_OVERFLOW));
    CHECK(::test_trace::lockDepth == 0);
}

/** SYS-59: a console whose transmitter does not get idle (dead, or still sending after the flush's time-out) costs
 * one flush and the line - never the reset, the safe state or the mark. */
void testDeadConsoleStillResets()
{
    ::safety::resetloop::Record volatile record{};
    ::safety::resetloop::clear(record);
    auto& supervisor = fresh();
    (void)supervisor.startResetLoopLimit(record, ::safety::resetloop::Start{}, 3U, 100U);
    ::test_trace::watchedRecord  = &record;
    ::test_trace::transmitterIdle = false;
    ::test_trace::trace.clear();
    supervisor.deadmanConfigMonitor.trigger(0x10U);
    CHECK(::test_trace::trace == deadConsoleResetTrace());
    CHECK(::test_trace::console.empty());
    CHECK(::test_trace::markedAtFlush);
    CHECK(record.pending == (::safety::resetloop::CAUSE_EVENT | ::etl::to_underlying(Event::DEADMAN_CONFIGURATION_ERROR)));
    CHECK(supervisor.inLimpHomeState());
}

/** SM-05 stays S3: a timing violation is reported and nothing else happens. SYS-37: the report of the RTE task
 * waits in the box until the safety task delivers it; only then does handle() run. */
void testTimingViolationIsReportOnly()
{
    auto& supervisor = fresh();
    supervisor.rteTimingMonitor.trigger(0x01020003U);
    CHECK(::test_trace::trace.empty()); // posted only - the reporter's context wrote nothing but the box
    CHECK(::safety::rteTimingMailbox.pending() == 1U);
    supervisor.deliverDeferredEvents();
    CHECK(::test_trace::trace == "E");
    CHECK(supervisor.rteTimingMonitor.getContext() == 0x01020003U);
    CHECK(::safety::rteTimingMailbox.pending() == 0U);
    CHECK(!supervisor.inLimpHomeState());
    CHECK(::test_trace::lockDepth == 0);
    ::test_trace::trace.clear();
    supervisor.deliverDeferredEvents(); // nothing waiting: nothing happens
    CHECK(::test_trace::trace.empty());
}

/** SYS-37: several reports between two safety cycles arrive in their order, each through handle(). */
void testDeferredReportsKeepTheirOrder()
{
    auto& supervisor = fresh();
    uint32_t seen[4]  = {};
    uint32_t count    = 0U;
    supervisor.rteTimingMonitor.trigger(0x01000001U);
    supervisor.rteTimingMonitor.trigger(0x02010002U);
    supervisor.rteTimingMonitor.trigger(0x01020003U);
    uint32_t const delivered = ::safety::rteTimingMailbox.drain([&](uint32_t const context) {
        if (count < 4U)
        {
            seen[count] = context;
        }
        ++count;
    });
    CHECK(delivered == 3U);
    CHECK(count == 3U);
    CHECK(seen[0] == 0x01000001U);
    CHECK(seen[1] == 0x02010002U);
    CHECK(seen[2] == 0x01020003U);

    supervisor.rteTimingMonitor.trigger(0x01000001U);
    supervisor.rteTimingMonitor.trigger(0x02010002U);
    supervisor.deliverDeferredEvents();
    CHECK(::test_trace::trace == "EE");
    CHECK(supervisor.rteTimingMonitor.getContext() == 0x02010002U); // the last one handled
    CHECK(!supervisor.inLimpHomeState());
}

/** SYS-37: a full box keeps the first CAPACITY reports and then says that more were lost - once, and still
 * without a reaction beyond the log (S3). */
void testFullBoxReportsTheLoss()
{
    auto& supervisor        = fresh();
    uint32_t const capacity = ::safety::SafetyEventMailbox::CAPACITY;
    for (uint32_t i = 0U; i < capacity + 3U; ++i)
    {
        supervisor.rteTimingMonitor.trigger(0x01000000U + i);
    }
    CHECK(::safety::rteTimingMailbox.pending() == capacity);
    CHECK(::safety::rteTimingMailbox.lost() == 3U);
    supervisor.deliverDeferredEvents();
    CHECK(::test_trace::trace == std::string(capacity + 1U, 'E'));
    CHECK(supervisor.rteTimingMonitor.getContext() == ::safety::SafetyEventMailbox::CONTEXT_LOST);
    CHECK(::safety::rteTimingMailbox.lost() == 0U);
    CHECK(::test_trace::trace.find('R') == std::string::npos);
    CHECK(!supervisor.inLimpHomeState());
    // the box works again afterwards
    ::test_trace::trace.clear();
    supervisor.rteTimingMonitor.trigger(0x02000005U);
    supervisor.deliverDeferredEvents();
    CHECK(::test_trace::trace == "E");
    CHECK(supervisor.rteTimingMonitor.getContext() == 0x02000005U);
}

/** SYS-37: the box is ordinary RAM, so nothing in it is trusted. A slot whose inverted copy does not match, or
 * indices that claim more than the box holds, are reported as CONTEXT_CORRUPTED - never delivered as a report
 * of their own - and the box starts empty again. */
void testCorruptedBoxIsReportedNotDelivered()
{
    struct Raw
    {
        uint32_t slots[::safety::SafetyEventMailbox::CAPACITY * 2U];
        uint32_t head;
        uint32_t tail;
        uint32_t lost;
    };
    static_assert(sizeof(Raw) == sizeof(::safety::SafetyEventMailbox), "layout of the box changed");

    // a wild write into a waiting slot
    auto& supervisor = fresh();
    supervisor.rteTimingMonitor.trigger(0x01000001U);
    supervisor.rteTimingMonitor.trigger(0x01000002U);
    Raw raw{};
    std::memcpy(&raw, &::safety::rteTimingMailbox, sizeof(raw));
    raw.slots[0] ^= 0x00010000U; // context of the first report changed, its inverted copy not
    std::memcpy(static_cast<void*>(&::safety::rteTimingMailbox), &raw, sizeof(raw));
    supervisor.deliverDeferredEvents();
    CHECK(::test_trace::trace == "E");
    CHECK(supervisor.rteTimingMonitor.getContext() == ::safety::SafetyEventMailbox::CONTEXT_CORRUPTED);
    CHECK(::safety::rteTimingMailbox.pending() == 0U);
    CHECK(!supervisor.inLimpHomeState());

    // broken indices
    ::test_trace::trace.clear();
    std::memcpy(&raw, &::safety::rteTimingMailbox, sizeof(raw));
    raw.head = raw.tail + ::safety::SafetyEventMailbox::CAPACITY + 5U;
    std::memcpy(static_cast<void*>(&::safety::rteTimingMailbox), &raw, sizeof(raw));
    supervisor.rteTimingMonitor.trigger(0x01000003U); // not stored: the box refuses while broken
    supervisor.deliverDeferredEvents();
    CHECK(::test_trace::trace == "E");
    CHECK(supervisor.rteTimingMonitor.getContext() == ::safety::SafetyEventMailbox::CONTEXT_CORRUPTED);
    CHECK(::safety::rteTimingMailbox.pending() == 0U);

    // and it works again
    ::test_trace::trace.clear();
    supervisor.rteTimingMonitor.trigger(0x01000004U);
    supervisor.deliverDeferredEvents();
    CHECK(supervisor.rteTimingMonitor.getContext() == 0x01000004U);
    CHECK(::test_trace::trace == "E");
}

/** SM-22 stays S1 without reset, in both directions. */
void testCheckRegisterErrorEntersOrLeavesWithoutReset()
{
    auto& supervisor = fresh();
    supervisor.checkRegisterMonitor.trigger(0U);
#ifdef PLATFORM_SUPPORT_IO
    CHECK(::test_trace::trace == "ES");
#else
    CHECK(::test_trace::trace == "E");
#endif
    ::test_trace::trace.clear();
    supervisor.checkRegisterMonitor.trigger(1U);
#ifdef PLATFORM_SUPPORT_IO
    CHECK(::test_trace::trace == "EL");
#else
    CHECK(::test_trace::trace == "E");
#endif
    CHECK(!supervisor.inLimpHomeState());
}

/** SYS-169: the log line of the register check names what deviated - an entry of the register list by class
 * (bits 31..24 of the context) and index (bits 15..0), the SAFETY_TEST pin of the demo (context 0) by its name; the
 * return (context CHECK_REGISTER_CONTEXT_OK) says nothing about a register. The reaction does not depend on the
 * context: every value other than the one of the return enters S1, and a register context is never mistaken for
 * the return. */
void testCheckRegisterErrorNamesTheContext()
{
#ifdef PLATFORM_SUPPORT_IO
    auto& supervisor = fresh();
    supervisor.checkRegisterMonitor.trigger(0U);
    CHECK(::test_trace::trace == "ES");
    CHECK(::test_trace::lastError == "Event: CHECK_REGISTER_ERROR (enter safe state, SAFETY_TEST pin)");

    struct Case
    {
        uint32_t context;
        char const* line;
    };
    Case const cases[] = {
        {0x06000003U, "Event: CHECK_REGISTER_ERROR (enter safe state, register class 6, entry 3)"},
        {0x01000000U, "Event: CHECK_REGISTER_ERROR (enter safe state, register class 1, entry 0)"},
        {0x04001234U, "Event: CHECK_REGISTER_ERROR (enter safe state, register class 4, entry 4660)"},
        {0x02000002U, "Event: CHECK_REGISTER_ERROR (enter safe state, register class 2, entry 2)"},
        {2U, "Event: CHECK_REGISTER_ERROR (enter safe state, register class 0, entry 2)"}, // not the return value
    };
    for (Case const& c : cases)
    {
        auto& s = fresh();
        s.checkRegisterMonitor.trigger(c.context);
        CHECK(::test_trace::trace == "ES");
        CHECK(::test_trace::lastError == c.line);
        if (::test_trace::lastError != c.line)
        {
            std::printf("  context 0x%08X: line '%s'\n", static_cast<unsigned>(c.context),
                        ::test_trace::lastError.c_str());
        }
    }

    auto& s = fresh();
    s.checkRegisterMonitor.trigger(::safety::SafeSupervisor::CHECK_REGISTER_CONTEXT_OK);
    CHECK(::test_trace::trace == "EL");
    CHECK(::test_trace::lastError == "Event: CHECK_REGISTER_ERROR (leave safe state)");
#endif
}

/** F1 as a property over every event: only SM-05, SM-22 and the device reports (SYS-29, S3 by decision) end
 * without reset or hold; every other event ends in the reset (safe state first) or, for the faults a reset cannot
 * remove, in the hold. DEVICE_FAULT runs with the context 0 here - malformed, so the fail-safe reset. */
void testNoEventEndsWithoutAReaction()
{
    auto const last = static_cast<uint8_t>(Event::ETL_ASSERT); // SYS-98: the last event
    for (uint8_t id = 0U; id <= last; ++id)
    {
        auto const event = static_cast<Event>(id);
        auto& supervisor = fresh();
        supervisor.handle(event);
        std::string const& t = ::test_trace::trace;
        if ((event == Event::RTE_TIMING_VIOLATION) || (event == Event::CHECK_REGISTER_ERROR)
            || (event == Event::DEVICE_REPORT))
        {
            CHECK(t.find('R') == std::string::npos);
            CHECK(supervisor.holdRounds() == 0U);
            continue;
        }
        bool const holds = (event == Event::WATCHDOG_STARTUP_CHECK_FAILURE) || (event == Event::ROM_CHECK_FAILURE)
                           || (event == Event::RESET_LOOP_LIMIT); // SYS-43
        if (holds)
        {
            CHECK(t == holdTrace());
        }
        else
        {
            CHECK(t == resetTraceOf(event));
            if (t != resetTraceOf(event))
            {
                std::printf("  event %u: trace '%s'\n", static_cast<unsigned>(id), t.c_str());
            }
        }
        CHECK(supervisor.inLimpHomeState());
    }
}

/** An unknown event id resets as before (default branch), safe state first. */
void testUnknownEventResets()
{
    auto& supervisor = fresh();
    supervisor.handle(static_cast<Event>(0xEEU));
    CHECK(::test_trace::trace == std::string("w") + SAFE + "FPFR"); // SYS-59: with its line
    CHECK(supervisor.inLimpHomeState());
}

// ------------------------------------------------------------------------------------------------ SYS-29 (G5, F5)

namespace dev = ::safety::device;
using dev::FaultClass;

constexpr uint8_t SPI0 = dev::busId(dev::BusKind::SPI, 0U);
constexpr uint8_t SPI1 = dev::busId(dev::BusKind::SPI, 1U);
constexpr uint8_t I2C0 = dev::busId(dev::BusKind::I2C, 0U);
constexpr uint32_t WINDOW = SafeSupervisor::DEVICE_BUS_WINDOW_CYCLES;

/** The context word: layout, bus ids and which words are malformed (checked at compile time). */
void testDeviceContextWord()
{
    static_assert(SPI0 == 0x20U, "SPI kind 1 in bits 7..5 of the bus id");
    static_assert(dev::busId(dev::BusKind::CAN, 31U) == 0x9FU, "CAN kind 4, index 31");
    static_assert(dev::busId(dev::BusKind::SPI, 32U) == 0U, "index beyond 31 is no bus id");
    static_assert(dev::busId(dev::BusKind::NONE, 0U) == 0U, "NONE only through NO_BUS");
    static_assert(dev::context(SPI1, 7U, FaultClass::OUTPUT, 0x5AU) == 0x2107075AU, "layout");
    static_assert(dev::busOf(0x2107075AU) == SPI1 && dev::deviceOf(0x2107075AU) == 7U
                      && dev::faultOf(0x2107075AU) == 7U && dev::detailOf(0x2107075AU) == 0x5AU,
                  "fields");
    static_assert(dev::isWellFormed(dev::context(dev::NO_BUS, 0U, FaultClass::STALE)), "NO_BUS is allowed");
    static_assert(!dev::isBus(dev::NO_BUS), "NO_BUS is not correlated");
    static_assert(!dev::isWellFormed(0U), "a forgotten word is malformed");
    static_assert(!dev::isWellFormed(dev::context(SPI0, 1U, FaultClass::INVALID)), "class 0");
    static_assert(!dev::isWellFormed(dev::context(SPI0, 1U, static_cast<FaultClass>(dev::FAULT_CLASS_LAST + 1U))),
                  "unknown class");
    static_assert(!dev::isWellFormed(dev::context(0xC0U, 1U, FaultClass::OUTPUT)), "reserved bus kind");
    static_assert(!dev::isWellFormed(::safety::SafetyEventMailbox::CONTEXT_LOST), "box word LOST");
    static_assert(!dev::isWellFormed(::safety::SafetyEventMailbox::CONTEXT_CORRUPTED), "box word CORRUPTED");
    CHECK(true);
}

/** Decision `sys37-postfach-abschaltend`: DEVICE_REPORT is S3 for every word - every class, malformed words and
 * the box's own LOST/CORRUPTED words. No safe state, no reset, no hold, no limp home. */
void testDeviceReportIsReportOnlyForEveryWord()
{
    for (uint32_t cls = 0U; cls <= 0xFFU; ++cls)
    {
        for (uint32_t const word :
             {dev::context(SPI0, 1U, static_cast<FaultClass>(cls)), dev::context(dev::NO_BUS, 2U,
                                                                                 static_cast<FaultClass>(cls))})
        {
            auto& supervisor = fresh();
            supervisor.deviceReportMonitor.trigger(word);
            CHECK(::test_trace::trace == "E");
            CHECK(!supervisor.inLimpHomeState());
            CHECK(supervisor.holdRounds() == 0U);
        }
    }
    for (uint32_t const word : {uint32_t{0U}, ::safety::SafetyEventMailbox::CONTEXT_LOST,
                                ::safety::SafetyEventMailbox::CONTEXT_CORRUPTED})
    {
        auto& supervisor = fresh();
        supervisor.deviceReportMonitor.trigger(word);
        CHECK(::test_trace::trace == "E");
        CHECK(!supervisor.inLimpHomeState());
    }
}

/** The box of a package: reporting writes nothing but the box (any context), the safety task delivers, a
 * malformed word is refused at the door, a full box says so once - all S3. */
void testDeviceReportBoxPath()
{
    auto& supervisor = fresh();
    ::safety::DeviceReportBox box;
    CHECK(box.report(SPI0, 3U, FaultClass::TEMPERATURE, 9U));
    CHECK(!box.report(0U));                                      // malformed: refused, not stored
    CHECK(!box.report(dev::context(0U, 3U, FaultClass::OUTPUT))); // bus id 0 is no bus
    CHECK(box.pending() == 1U);
    CHECK(::test_trace::trace.empty());                           // posting touched nothing of the supervisor
    CHECK(supervisor.deliverDeviceReports(box) == 1U);
    CHECK(::test_trace::trace == "E");
    CHECK(supervisor.deviceReportMonitor.getContext() == dev::context(SPI0, 3U, FaultClass::TEMPERATURE, 9U));
    CHECK(box.pending() == 0U);
    CHECK(supervisor.deliverDeviceReports(box) == 0U); // nothing waiting, nothing happens
    CHECK(::test_trace::trace == "E");

    ::test_trace::trace.clear();
    uint32_t const capacity = ::safety::SafetyEventMailbox::CAPACITY;
    for (uint32_t i = 0U; i < capacity + 2U; ++i)
    {
        (void)box.report(dev::NO_BUS, static_cast<uint8_t>(i), FaultClass::SUPPLY);
    }
    CHECK(box.lost() == 2U);
    CHECK(supervisor.deliverDeviceReports(box) == capacity + 1U);
    CHECK(supervisor.deviceReportMonitor.getContext() == ::safety::SafetyEventMailbox::CONTEXT_LOST);
    CHECK(::test_trace::trace == std::string(capacity + 1U, 'E'));
    CHECK(!supervisor.inLimpHomeState());

    // the box of one package is not the box of the RTE deadlines (one box per source, SYS-37)
    CHECK(::safety::rteTimingMailbox.pending() == 0U);
}

/** Nothing from a box can switch off: a word whose class would hold the safe state as DEVICE_FAULT (IDENTITY)
 * stays a report when it comes through the box. */
void testSwitchingOffWordFromABoxStaysAReport()
{
    auto& supervisor = fresh();
    ::safety::DeviceReportBox box;
    CHECK(box.report(SPI0, 1U, FaultClass::IDENTITY));
    CHECK(box.report(SPI0, 1U, FaultClass::OUTPUT));
    (void)supervisor.deliverDeviceReports(box);
    CHECK(::test_trace::trace == "EE");
    CHECK(::test_trace::trace.find('R') == std::string::npos);
    CHECK(supervisor.holdRounds() == 0U);
    CHECK(!supervisor.inLimpHomeState());
}

/** SYS-194 (safety review BL38-P1 2026-10-03, SYS147-B1 b): the report of the safe_off hold of the family port
 * (Integration.cpp: NO_BUS, NO_DEVICE, ACTUATOR_STATE, detail = output number) is well formed, arrives through its
 * box as DEVICE_REPORT with its word, and stays S3 also when it comes in every cycle (a stuck foreign level), for every
 * output number. Counter-check: the same word raised as DEVICE_FAULT would switch off (S1 + S2) - so it is the path
 * through the box, not the word, that keeps it a report. */
void testSafeOffTakeBackReport()
{
    constexpr uint32_t word3 = dev::context(dev::NO_BUS, dev::NO_DEVICE, FaultClass::ACTUATOR_STATE, 3U);
    static_assert(dev::isWellFormed(word3), "the box accepts the word of the safe_off hold");
    static_assert(dev::NO_DEVICE == 0xFFU, "outside the board device instances, which count from 0");
    auto& supervisor = fresh();
    ::safety::DeviceReportBox box;
    CHECK(box.report(word3));
    CHECK(::test_trace::trace.empty()); // posting from TASK_BSP touches nothing of the supervisor
    CHECK(supervisor.deliverDeviceReports(box) == 1U);
    CHECK(::test_trace::trace == "E");
    CHECK(supervisor.deviceReportMonitor.getContext() == word3);
    for (uint32_t cycle = 0U; cycle < 100U; ++cycle)
    {
        for (uint32_t output = 0U; output <= 0xFFU; output += 0x55U)
        {
            CHECK(box.report(dev::context(dev::NO_BUS, dev::NO_DEVICE, FaultClass::ACTUATOR_STATE,
                                          static_cast<uint8_t>(output))));
        }
        (void)supervisor.deliverDeviceReports(box);
        supervisor.deliverDeferredEvents();
    }
    CHECK(::test_trace::trace == std::string(1U + 100U * 4U, 'E'));
    CHECK(::test_trace::trace.find('R') == std::string::npos);
    CHECK(!supervisor.inLimpHomeState());
    CHECK(supervisor.holdRounds() == 0U);

    auto& counter = fresh();
    counter.deviceFaultMonitor.trigger(word3);
    CHECK(::test_trace::trace == resetTrace());
    CHECK(counter.inLimpHomeState());
}

/** DEVICE_FAULT (safety task only): IDENTITY holds the safe state without reset, every other class and a malformed
 * word end in S1 + S2 - the safe outputs first, then the reset. */
void testDeviceFaultReactionPerClass()
{
    for (uint32_t cls = 0U; cls <= (dev::FAULT_CLASS_LAST + 1U); ++cls)
    {
        auto& supervisor = fresh();
        supervisor.deviceFaultMonitor.trigger(dev::context(SPI0, 4U, static_cast<FaultClass>(cls), 1U));
        std::string const& t = ::test_trace::trace;
        if (cls == static_cast<uint32_t>(FaultClass::IDENTITY))
        {
            CHECK(t == holdTrace());
            CHECK(::test_trace::console
                  == "SAFE STATE HELD (DEVICE_FAULT): outputs off, no reset - power cycle required\r\n");
        }
        else
        {
            CHECK(t == resetTrace());
            if (t != resetTrace())
            {
                std::printf("  class %u: trace '%s'\n", static_cast<unsigned>(cls), t.c_str());
            }
        }
        CHECK(supervisor.inLimpHomeState());
    }
    // IDENTITY in a malformed word (reserved bus kind) is not trusted to choose the hold: fail-safe reset
    auto& supervisor = fresh();
    supervisor.deviceFaultMonitor.trigger(dev::context(0xC0U, 4U, FaultClass::IDENTITY));
    CHECK(::test_trace::trace == resetTrace());
}

void cycles(SafeSupervisor& supervisor, uint32_t n)
{
    for (uint32_t i = 0U; i < n; ++i)
    {
        supervisor.deliverDeferredEvents();
    }
}

/** F5: communication faults of two devices on one bus within the window are one event "bus lost"; further ones in
 * the window are folded into it; the verdict reaches the RTE reader; it ends after a window without faults. */
void testDeviceBusCorrelation()
{
    auto& supervisor = fresh();
    auto const comm  = [&](uint8_t const bus, uint8_t const device) {
        supervisor.deviceReportMonitor.trigger(dev::context(bus, device, FaultClass::COMMUNICATION));
    };
    comm(SPI0, 1U);
    CHECK(::test_trace::trace == "E"); // one device: its own report
    comm(SPI0, 1U);
    CHECK(::test_trace::trace == "EE"); // the same device again: still no bus verdict
    CHECK(!supervisor.deviceBusLost(SPI0));
    cycles(supervisor, 3U);
    comm(SPI0, 2U);
    CHECK(::test_trace::trace == "EEE"); // a second device: one "bus lost" line
    CHECK(supervisor.deviceBusLost(SPI0));
    CHECK(::safety::device::rte::busLost(SPI0));
    comm(SPI0, 3U);
    comm(SPI0, 1U);
    CHECK(::test_trace::trace == "EEE"); // folded into the event of this window
    CHECK(!supervisor.inLimpHomeState());
    CHECK(::test_trace::trace.find('R') == std::string::npos);
    cycles(supervisor, WINDOW);
    CHECK(supervisor.deviceBusLost(SPI0)); // exactly one window after the last fault: still lost
    cycles(supervisor, 1U);
    CHECK(!supervisor.deviceBusLost(SPI0)); // one window without faults: the verdict ends
    CHECK(!::safety::device::rte::busLost(SPI0));
    comm(SPI0, 2U);
    CHECK(::test_trace::trace == "EEEE"); // a new window starts with a plain report
    CHECK(!supervisor.deviceBusLost(SPI0));
    comm(SPI0, 2U);
    CHECK(!supervisor.deviceBusLost(SPI0));
}

/** The window is inclusive: a second device exactly WINDOW cycles later still correlates, one cycle more not. */
void testDeviceBusWindowBoundary()
{
    auto& supervisor = fresh();
    supervisor.deviceReportMonitor.trigger(dev::context(SPI1, 1U, FaultClass::COMMUNICATION));
    cycles(supervisor, WINDOW);
    supervisor.deviceReportMonitor.trigger(dev::context(SPI1, 2U, FaultClass::COMMUNICATION));
    CHECK(supervisor.deviceBusLost(SPI1));

    auto& other = fresh();
    other.deviceReportMonitor.trigger(dev::context(SPI1, 1U, FaultClass::COMMUNICATION));
    cycles(other, WINDOW + 1U);
    other.deviceReportMonitor.trigger(dev::context(SPI1, 2U, FaultClass::COMMUNICATION));
    CHECK(!other.deviceBusLost(SPI1));
}

/** Only communication faults on a real bus correlate: NO_BUS devices, other classes and switching-off faults do
 * not make a bus lost. */
void testNoCorrelationWithoutABusOrForOtherClasses()
{
    auto& supervisor = fresh();
    supervisor.deviceReportMonitor.trigger(dev::context(dev::NO_BUS, 1U, FaultClass::COMMUNICATION));
    supervisor.deviceReportMonitor.trigger(dev::context(dev::NO_BUS, 2U, FaultClass::COMMUNICATION));
    CHECK(!supervisor.deviceBusLost(dev::NO_BUS));
    supervisor.deviceReportMonitor.trigger(dev::context(SPI0, 1U, FaultClass::OUTPUT));
    supervisor.deviceReportMonitor.trigger(dev::context(SPI0, 2U, FaultClass::TEMPERATURE));
    CHECK(!supervisor.deviceBusLost(SPI0));
    CHECK(::test_trace::trace == "EEEE"); // every one logged on its own
    CHECK(!supervisor.deviceBusLost(0U)); // bus id 0 is never tracked
}

/** Two or more buses: the verdict is per bus id; the same index on another bus kind is another bus. */
void testDeviceBusesAreIndependent()
{
    auto& supervisor = fresh();
    for (uint8_t const bus : {SPI0, I2C0})
    {
        supervisor.deviceReportMonitor.trigger(dev::context(bus, 1U, FaultClass::COMMUNICATION));
    }
    supervisor.deviceReportMonitor.trigger(dev::context(SPI1, 1U, FaultClass::COMMUNICATION));
    CHECK(!supervisor.deviceBusLost(SPI0));
    CHECK(!supervisor.deviceBusLost(I2C0));
    supervisor.deviceReportMonitor.trigger(dev::context(I2C0, 2U, FaultClass::COMMUNICATION));
    CHECK(supervisor.deviceBusLost(I2C0));
    CHECK(!supervisor.deviceBusLost(SPI0));
    CHECK(!supervisor.deviceBusLost(SPI1));
}

/** DEVICE_BUS_SLOTS buses at the same time; one more is reported without correlation (warning), and a slot whose
 * window is over is taken again. */
void testDeviceBusSlots()
{
    auto& supervisor    = fresh();
    uint32_t const slots = SafeSupervisor::DEVICE_BUS_SLOTS;
    for (uint32_t i = 0U; i < slots; ++i)
    {
        supervisor.deviceReportMonitor.trigger(
            dev::context(dev::busId(dev::BusKind::SPI, static_cast<uint8_t>(i)), 1U, FaultClass::COMMUNICATION));
    }
    CHECK(::test_trace::trace == std::string(slots, 'E'));
    uint8_t const extra = dev::busId(dev::BusKind::CAN, 0U);
    ::test_trace::trace.clear();
    supervisor.deviceReportMonitor.trigger(dev::context(extra, 1U, FaultClass::COMMUNICATION));
    supervisor.deviceReportMonitor.trigger(dev::context(extra, 2U, FaultClass::COMMUNICATION));
    CHECK(::test_trace::trace == "wEwE");
    CHECK(!supervisor.deviceBusLost(extra));
    cycles(supervisor, WINDOW + 1U);
    ::test_trace::trace.clear();
    supervisor.deviceReportMonitor.trigger(dev::context(extra, 1U, FaultClass::COMMUNICATION));
    supervisor.deviceReportMonitor.trigger(dev::context(extra, 2U, FaultClass::COMMUNICATION));
    CHECK(::test_trace::trace == "EE");
    CHECK(supervisor.deviceBusLost(extra));
}

// ------------------------------------------------------------------------------------------------ SYS-43 (F13, T4)

namespace rl = ::safety::resetloop;

constexpr uint32_t LIMIT        = 3U;
constexpr uint32_t CLEAR_CYCLES = 5U;

rl::Start powerOn() { return rl::Start{true, false, false, false}; }

rl::Start watchdogReset() { return rl::Start{false, true, false, false}; } // a real time-out: WATCHDOG alone

rl::Start faultReset() { return rl::Start{false, false, true, true}; } // dump or lockup, software reset

rl::Start otherReset() { return rl::Start{false, false, false, true}; } // pin, debugger, unmarked software reset

// Board finding 2026-09-22: a J-Link reset (VC_CORERESET + SYSRESETREQ) starts with RESET | WATCHDOG (0x06)
rl::Start debuggerReset() { return rl::Start{false, true, false, true}; }

uint32_t eventCause(Event const event) { return rl::CAUSE_EVENT | ::etl::to_underlying(event); }

/** Random content as after power-on without the port's clearing: not a valid record. */
void setRandom(rl::Record volatile& r)
{
    r.count     = 0x1234U;
    r.pending   = 0xA5A5A5A5U;
    r.lastCause = 0x5A5A5A5AU;
    r.check     = 0xDEADBEEFU;
}

/** The record rules of resetloop::onStart/markSafetyReset, without the supervisor. */
void testResetLoopRecordRules()
{
    rl::Record volatile r{};
    setRandom(r);
    CHECK(!rl::isValid(r));

    // power-on clears whatever was there, and the start is not counted
    rl::StartResult s = rl::onStart(r, powerOn());
    CHECK(rl::isValid(r) && (r.count == 0U) && (s.count == 0U) && !s.counted);

    // a reset nobody marked, without watchdog or fault exception, is no safety reset
    s = rl::onStart(r, otherReset());
    CHECK((s.count == 0U) && !s.counted && s.recordValid);

    // each source counts: the supervisor's mark (with its event), a fault exception, the watchdog
    rl::markSafetyReset(r, eventCause(Event::MPU_NOT_ENABLED));
    CHECK(rl::isValid(r) && (r.pending == eventCause(Event::MPU_NOT_ENABLED)) && (r.count == 0U));
    s = rl::onStart(r, otherReset());
    CHECK((s.count == 1U) && s.counted && (s.lastCause == eventCause(Event::MPU_NOT_ENABLED)));
    CHECK(r.pending == rl::CAUSE_NONE); // counted once, not again at the next start
    s = rl::onStart(r, faultReset());
    CHECK((s.count == 2U) && (s.lastCause == rl::CAUSE_FAULT_EXCEPTION));
    s = rl::onStart(r, watchdogReset());
    CHECK((s.count == 3U) && (s.lastCause == rl::CAUSE_WATCHDOG));

    // a reset in between that is no safety reset keeps the count (no evidence that the fault is gone)
    s = rl::onStart(r, otherReset());
    CHECK((s.count == 3U) && !s.counted && (s.lastCause == rl::CAUSE_WATCHDOG));

    // board finding 2026-09-22: a debugger reset reports RESET | WATCHDOG (0x06) - neither counts nor clears
    s = rl::onStart(r, debuggerReset());
    CHECK((s.count == 3U) && !s.counted && (s.lastCause == rl::CAUSE_WATCHDOG) && rl::isValid(r));
    rl::write(r, 0U, rl::CAUSE_NONE, rl::CAUSE_NONE);
    s = rl::onStart(r, debuggerReset());
    CHECK((s.count == 0U) && !s.counted);
    s = rl::onStart(r, watchdogReset()); // WATCHDOG alone (0x04): a real time-out counts
    CHECK((s.count == 1U) && s.counted && (s.lastCause == rl::CAUSE_WATCHDOG));
    rl::write(r, 3U, rl::CAUSE_NONE, rl::CAUSE_WATCHDOG);

    // the mark names the cause even when the watchdog or a fault exception came as well
    rl::markSafetyReset(r, eventCause(Event::DEVICE_FAULT));
    s = rl::onStart(r, rl::Start{false, true, true, true});
    CHECK((s.count == 4U) && (s.lastCause == eventCause(Event::DEVICE_FAULT)));

    // power-on clears the count and a pending mark
    rl::markSafetyReset(r, eventCause(Event::MPU_NOT_ENABLED));
    s = rl::onStart(r, rl::Start{true, true, true, true});
    CHECK((s.count == 0U) && rl::isValid(r) && (r.pending == rl::CAUSE_NONE));

    // random or overwritten content: the start counts from 0 (a safety reset is the first one)
    setRandom(r);
    s = rl::onStart(r, watchdogReset());
    CHECK((s.count == 1U) && !s.recordValid && rl::isValid(r));
    setRandom(r);
    s = rl::onStart(r, otherReset());
    CHECK((s.count == 0U) && !s.recordValid && rl::isValid(r));
    // a mark on an overwritten record starts again from 0 with the mark
    setRandom(r);
    rl::markSafetyReset(r, eventCause(Event::MPU_NOT_ENABLED));
    s = rl::onStart(r, otherReset());
    CHECK((s.count == 1U) && s.recordValid);

    // every single flipped bit in any word makes the record invalid
    rl::write(r, 2U, eventCause(Event::MPU_NOT_ENABLED), rl::CAUSE_WATCHDOG);
    CHECK(rl::isValid(r));
    bool allFound = true;
    for (uint32_t word = 0U; word < 4U; ++word)
    {
        for (uint32_t bit = 0U; bit < 32U; ++bit)
        {
            rl::Record volatile copy{};
            rl::write(copy, 2U, eventCause(Event::MPU_NOT_ENABLED), rl::CAUSE_WATCHDOG);
            uint32_t volatile* const words[] = {&copy.count, &copy.pending, &copy.lastCause, &copy.check};
            *words[word] = *words[word] ^ (1U << bit);
            allFound     = allFound && !rl::isValid(copy);
        }
    }
    CHECK(allFound);

    // saturation instead of wrap-around
    rl::write(r, rl::COUNT_MAX, rl::CAUSE_NONE, rl::CAUSE_WATCHDOG);
    s = rl::onStart(r, watchdogReset());
    CHECK((s.count == rl::COUNT_MAX) && rl::isValid(r));

    // the limit, the context word and the fault-free time in cycles
    static_assert(!rl::limitReached(2U, 3U) && rl::limitReached(3U, 3U) && rl::limitReached(4U, 3U), "limit");
    static_assert(rl::limitReached(1U, 1U) && !rl::limitReached(0U, 1U), "limit 1: the first safety reset holds");
    static_assert(!rl::limitReached(0xFFFFU, 0U), "limit 0 never holds (the port requires >= 1)");
    static_assert(rl::contextOf(3U, 0x109U) == 0x01090003U, "context: cause 31..16, count 15..0");
    static_assert(rl::clearCyclesOf(60000U, 10U) == 6000U, "one minute of 10 ms cycles");
    static_assert(rl::clearCyclesOf(15U, 10U) == 2U, "rounded up: never shorter than configured");
    static_assert(rl::clearCyclesOf(0U, 10U) == 1U, "at least one cycle");
    static_assert(rl::clearCyclesOf(86400000U, 7U) == 12342858U, "a day of 7 ms cycles");
}

/** The event text of the hold, and that the whole console line fits (nothing of it is shortened). */
void testResetLoopHoldText()
{
    char text[::safety::SAFE_CONSOLE_LINE_SIZE];
    (void)rl::formatHoldText(text, sizeof(text), 3U, eventCause(Event::MPU_NOT_ENABLED));
    CHECK(std::string(text) == "RESET_LOOP_LIMIT, 3 safety resets, last: event 9");
    (void)rl::formatHoldText(text, sizeof(text), 12U, rl::CAUSE_WATCHDOG);
    CHECK(std::string(text) == "RESET_LOOP_LIMIT, 12 safety resets, last: watchdog");
    (void)rl::formatHoldText(text, sizeof(text), 1U, rl::CAUSE_FAULT_EXCEPTION);
    CHECK(std::string(text) == "RESET_LOOP_LIMIT, 1 safety resets, last: fault exception");
    (void)rl::formatHoldText(text, sizeof(text), 0U, rl::CAUSE_NONE);
    CHECK(std::string(text) == "RESET_LOOP_LIMIT, 0 safety resets, last: unknown");
    char small[8];
    size_t const n = rl::formatHoldText(small, sizeof(small), 3U, rl::CAUSE_WATCHDOG);
    CHECK((n == 7U) && (std::string(small) == "RESET_L")); // bounded, terminated

    // longest text (largest count, longest cause) in the real console line of the hold: kept whole
    (void)rl::formatHoldText(text, sizeof(text), rl::COUNT_MAX, rl::CAUSE_FAULT_EXCEPTION);
    char line[::safety::SAFE_CONSOLE_LINE_SIZE];
    (void)::safety::formatConsoleLine(
        line, sizeof(line), "SAFE STATE HELD (%s): outputs off, no reset - power cycle required\r\n", text);
    CHECK(std::string(line)
          == std::string("SAFE STATE HELD (") + text + "): outputs off, no reset - power cycle required\r\n");
}

/** One start of the ECU as main() of the family port runs it: a fresh supervisor, then the reset-loop limit. */
rl::StartResult start(rl::Record volatile& record, rl::Start const& how)
{
    auto& supervisor = fresh();
    return supervisor.startResetLoopLimit(record, how, LIMIT, CLEAR_CYCLES);
}

/** The loop of F13: a permanent fault resets at every start; the third safety reset in a row holds S1 without a
 * reset, before anything of the application ran, with the count and the last event on the console. */
void testResetLoopHoldsAtTheLimit()
{
    rl::Record volatile r{};
    setRandom(r);

    rl::StartResult s = start(r, powerOn());
    auto& supervisor  = SafeSupervisor::getInstance();
    CHECK((s.count == 0U) && ::test_trace::trace.empty() && (supervisor.holdRounds() == 0U));
    for (uint32_t round = 1U; round < LIMIT; ++round)
    {
        supervisor.mpuEnableMonitor.trigger(); // the permanent fault, found by the first safety cycle
        CHECK(::test_trace::trace == resetTrace());
        CHECK(r.pending == eventCause(Event::MPU_NOT_ENABLED));
        s = start(r, otherReset()); // software reset: the MCU says "reset", the mark says "safety"
        CHECK((s.count == round) && s.counted);
        CHECK(::test_trace::trace.empty()); // below the limit: nothing but the count
        CHECK(!supervisor.inLimpHomeState());
    }
    supervisor.mpuEnableMonitor.trigger();
    s = start(r, otherReset());
    CHECK(s.count == LIMIT);
    CHECK(::test_trace::trace == holdTrace());
    CHECK(::test_trace::trace.find('R') == std::string::npos);
    CHECK(supervisor.inLimpHomeState());
    CHECK(supervisor.holdRounds() == static_cast<uint32_t>(::test_hold::HOLD_ROUNDS));
    CHECK(::test_hold::locked);
    CHECK(::test_trace::console
          == "SAFE STATE HELD (RESET_LOOP_LIMIT, 3 safety resets, last: event 9): outputs off, no reset - power "
             "cycle required\r\n");
    CHECK(supervisor.resetLoopMonitor.getContext() == rl::contextOf(LIMIT, eventCause(Event::MPU_NOT_ENABLED)));
    CHECK(r.pending == rl::CAUSE_NONE); // the hold marks nothing

    // Only a power cycle ends it: a reset from outside (pin, debugger) finds the count and holds again
    s = start(r, otherReset());
    CHECK((s.count == LIMIT) && (::test_trace::trace == holdTrace()));
    s = start(r, powerOn());
    CHECK((s.count == 0U) && ::test_trace::trace.empty());
}

/** Watchdog resets (SM-01) and fault exceptions (SM-17) do not pass handle(): they count through the MCU's reset
 * status and the dump, and mixed causes form one loop. */
void testResetLoopCountsWatchdogAndFaultExceptions()
{
    rl::Record volatile r{};
    setRandom(r);
    (void)start(r, powerOn());
    CHECK(start(r, watchdogReset()).count == 1U);
    CHECK(::test_trace::trace.empty());
    CHECK(start(r, faultReset()).count == 2U);
    CHECK(::test_trace::trace.empty());
    rl::StartResult const s = start(r, watchdogReset());
    CHECK((s.count == 3U) && (::test_trace::trace == holdTrace()));
    CHECK(::test_trace::console.find("last: watchdog") != std::string::npos);
}

/** The fault-free time: counted in safety cycles (deliverDeferredEvents, one per cycle) from the start; the count
 * is cleared once, exactly when the time is over, and a later safety reset counts as the first again. */
void testResetLoopClearsAfterTheFaultFreeTime()
{
    rl::Record volatile r{};
    setRandom(r);
    (void)start(r, powerOn());
    (void)start(r, watchdogReset());
    (void)start(r, watchdogReset());
    auto& supervisor = SafeSupervisor::getInstance();
    CHECK(r.count == 2U);
    for (uint32_t i = 1U; i < CLEAR_CYCLES; ++i)
    {
        supervisor.deliverDeferredEvents();
    }
    CHECK(r.count == 2U);                  // one cycle short: the loop is still a loop
    supervisor.mpuEnableMonitor.trigger(); // a fault now continues the loop
    rl::StartResult s = start(r, otherReset());
    CHECK((s.count == 3U) && (::test_trace::trace == holdTrace()));

    (void)start(r, powerOn());
    (void)start(r, watchdogReset());
    (void)start(r, watchdogReset());
    for (uint32_t i = 0U; i < CLEAR_CYCLES; ++i)
    {
        supervisor.deliverDeferredEvents();
    }
    CHECK((r.count == 0U) && rl::isValid(r));
    CHECK(::test_trace::trace.empty());                   // clearing is silent
    rl::write(r, 1U, rl::CAUSE_NONE, rl::CAUSE_WATCHDOG); // only once per run: nothing clears it again
    for (uint32_t i = 0U; i < 3U * CLEAR_CYCLES; ++i)
    {
        supervisor.deliverDeferredEvents();
    }
    CHECK(r.count == 1U);
    rl::write(r, 0U, rl::CAUSE_NONE, rl::CAUSE_NONE);
    supervisor.mpuEnableMonitor.trigger(); // a fault after the fault-free time
    s = start(r, otherReset());
    CHECK((s.count == 1U) && ::test_trace::trace.empty());
}

/** The mark: before every reset of an S1 + S2 reaction (all resetting events and an unknown one), never on a hold,
 * a report-only event or CHECK_REGISTER_ERROR, and nothing without a record (as before SYS-43). */
void testResetLoopMarkOnlyBeforeAReset()
{
    rl::Record volatile r{};
    setRandom(r);
    auto const last = static_cast<uint8_t>(Event::ETL_ASSERT); // SYS-98: the last event
    uint32_t marked = 0U;
    for (uint8_t id = 0U; id <= last; ++id)
    {
        auto const event = static_cast<Event>(id);
        (void)start(r, powerOn());
        SafeSupervisor::getInstance().handle(event);
        bool const resets = (::test_trace::trace.find('R') != std::string::npos);
        CHECK(resets == (r.pending == eventCause(event)));
        CHECK(resets || (r.pending == rl::CAUSE_NONE));
        CHECK(rl::isValid(r));
        marked += resets ? 1U : 0U;
    }
    CHECK(marked == 18U); // 24 events: 3 holds, 3 without reset (SM-05, SM-22, DEVICE_REPORT)
    (void)start(r, powerOn());
    SafeSupervisor::getInstance().handle(static_cast<Event>(0xEEU));
    CHECK(r.pending == (rl::CAUSE_EVENT | 0xEEU));

    // without startResetLoopLimit() the supervisor has no record and resets unmarked
    rl::write(r, 0U, rl::CAUSE_NONE, rl::CAUSE_NONE);
    auto& unregistered = fresh();
    unregistered.mpuEnableMonitor.trigger();
    CHECK((::test_trace::trace == resetTrace()) && (r.pending == rl::CAUSE_NONE));
    unregistered.deliverDeferredEvents(); // and no fault-free time either
    CHECK(rl::isValid(r));
}

/** Board finding 2026-09-22: debug restarts with the J-Link (RESET | WATCHDOG) never lead to the hold, however
 * many, and do not hide a loop either: a real watchdog time-out after them continues the count. */
void testResetLoopDebuggerRestartsDoNotCount()
{
    rl::Record volatile r{};
    setRandom(r);
    (void)start(r, powerOn());
    for (uint32_t i = 0U; i < 10U; ++i)
    {
        rl::StartResult const s = start(r, debuggerReset());
        CHECK((s.count == 0U) && ::test_trace::trace.empty());
    }
    (void)start(r, watchdogReset());
    (void)start(r, debuggerReset());
    CHECK(start(r, watchdogReset()).count == 2U);
    (void)start(r, debuggerReset());
    rl::StartResult const s = start(r, watchdogReset());
    CHECK((s.count == 3U) && (::test_trace::trace == holdTrace()));
}

/** Limit 1: the first safety reset already holds; the start after power-on never does. */
void testResetLoopLimitOne()
{
    rl::Record volatile r{};
    setRandom(r);
    auto& supervisor  = fresh();
    rl::StartResult s = supervisor.startResetLoopLimit(r, powerOn(), 1U, CLEAR_CYCLES);
    CHECK((s.count == 0U) && ::test_trace::trace.empty());
    auto& again = fresh();
    s           = again.startResetLoopLimit(r, watchdogReset(), 1U, CLEAR_CYCLES);
    CHECK((s.count == 1U) && (::test_trace::trace == holdTrace()));
}
} // namespace

int main()
{
    testWatchdogStartupFailureHoldsTheSafeState();
    testRomCheckFailureHoldsTheSafeState();
    testHoldStartsWithTheSafeState();
    testHoldWritesOneConsoleLine();
    testResettingEventWalksTheTableBeforeTheReset();
    testPeriodViolationWritesItsLineBeforeTheReset();
    testEveryResetWritesItsLine();
    testDeadConsoleStillResets();
    testKernelFaultsResetWithTheirLine();
    testEtlAssertResetsWithItsLine();
    testKernelFaultsBypassHandle();
    testEccErrorFromInterruptResetsWithoutLog();
    testTimingViolationIsReportOnly();
    testDeferredReportsKeepTheirOrder();
    testFullBoxReportsTheLoss();
    testCorruptedBoxIsReportedNotDelivered();
    testCheckRegisterErrorEntersOrLeavesWithoutReset();
    testCheckRegisterErrorNamesTheContext(); // SYS-169
    testNoEventEndsWithoutAReaction();
    testUnknownEventResets();
    testDeviceContextWord();
    testDeviceReportIsReportOnlyForEveryWord();
    testDeviceReportBoxPath();
    testSwitchingOffWordFromABoxStaysAReport();
    testSafeOffTakeBackReport(); // SYS-194
    testDeviceFaultReactionPerClass();
    testDeviceBusCorrelation();
    testDeviceBusWindowBoundary();
    testNoCorrelationWithoutABusOrForOtherClasses();
    testDeviceBusesAreIndependent();
    testDeviceBusSlots();
    testResetLoopRecordRules();
    testResetLoopHoldText();
    testResetLoopHoldsAtTheLimit();
    testResetLoopCountsWatchdogAndFaultExceptions();
    testResetLoopClearsAfterTheFaultFreeTime();
    testResetLoopMarkOnlyBeforeAReset();
    testResetLoopLimitOne();
    testResetLoopDebuggerRestartsDoNotCount();

    std::printf("%d checks, %d failures\n", checks, failures);
    return failures == 0 ? 0 : 1;
}
