/* SYS-95 (findings DFA-1, DFA-2 and DFA-6 (a) of doc/safety/dfa.md): host test of the progress checks of the safety
 * cycle on the S32K1 (safeLifecycle/ProgressSupervision.cpp, the real code, compiled as an image with the ROM
 * re-check, NX_ROM_CHECK_IMAGE) with the real ProgressChecks.cpp and the real SafeSupervisor (reaction). Scripted:
 * the ROM check's result, period and passes, the deadman's verdict, the system time and the LPTMR of the period check
 * (progressStubs before the stubs of the safety cycle tests). Test profile: safety task 10 ms, lateness 2 ms, window 8
 * (stubs/integration/safeLifecycleConfiguration.h), core 64 MHz and LPIT 4 MHz (progressStubs/bsp/clock).
 *
 * What it shows:
 * - healthy cycles - system time and LPTMR advancing alike at either end of the LPO range, the deadman moving, a
 *   ROM-check pass every second at a period of 1 s - never raise an event, for 3000 cycles;
 * - DFA-2: the budget of the deadman check is derived from the clock plan (2 x 16 + 2 = 34 polls); STOPPED is
 *   DEADMAN_CONFIGURATION_ERROR with the field 0x100 (S1 + S2); NOT_RUNNING is no finding; without a deadman the
 *   check is not called;
 * - DFA-6 (a): a stopped system time is SAFETY_CYCLE_PERIOD_VIOLATION kind 4 at the end of the first window (8
 *   cycles, value 0), a system time at double speed kind 5; while the cycle timer's configuration deviates the system
 *   time is not compared (kind 3 is the period check's), and the check starts again afterwards without a finding;
 * - DFA-1: a pass counter that stands still is ROM_CHECK_FAILURE with context 7 after three periods in safety cycles
 *   (300 cycles at 1 s / 10 ms: none at cycle 300, the report at cycle 301 - S1 held), once; the first pass that
 *   never comes after the start likewise; nothing with period 0 and nothing after the ROM check reported a failure
 *   itself; a period of a day does not overflow the deadline.
 *
 * Built-in faults (run by hand for SYS-95): the deadman verdict ignored, the cycle timer check before the system time
 * dropped, the deadline computed from one period instead of three - each makes this test red.
 *
 * Build and run (zig c++, no hardware):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -Iplatforms/s32k1xx/port/safety/safeLifecycle/test/host/progressStubs \
 *       -Iplatforms/s32k1xx/port/safety/safeLifecycle/include \
 *       -Iplatforms/s32k1xx/port/safety/safeLifecycle/test/host/stubs \
 *       -Iecu/safety/safeWatchdog/include \
 *       -Iecu/safety/safeSupervisor/include -Iecu/safety/safeSupervisor/test/host/stubs \
 *       -isystem external/openbsw/libs/safety/safeMonitor/include \
 *       platforms/s32k1xx/port/safety/safeLifecycle/test/host/ProgressSupervisionHostTest.cpp \
 *       ecu/safety/safeWatchdog/src/safeWatchdog/ProgressChecks.cpp \
 *       -o build/host/progresssupervision.exe
 */

#include <cstdint>
#include <cstdio>
#include <string>

namespace test_trace
{
std::string trace;
int lockDepth = 0;

void add(char const c) { trace.push_back(c); }
} // namespace test_trace

#define NX_SAFE_SUPERVISOR_HOLDING()              (false)
#define NX_SAFE_SUPERVISOR_CONSOLE(format, event) ((void)(format), (void)(event), ::test_trace::add('P'))

extern "C" void softwareSystemReset(void) { ::test_trace::add('R'); }

#include "../../../../../../../ecu/safety/safeSupervisor/src/safeSupervisor/SafeSupervisor.cpp" // NOLINT

// The image of the test runs the ROM re-check (cmake/NxRomCheck.cmake sets this for safeLifecycle exactly then)
#define NX_ROM_CHECK_IMAGE 1
#include "../../src/safeLifecycle/ProgressSupervision.cpp" // NOLINT

namespace test_cycle
{
bool gateLocked = true;
} // namespace test_cycle

namespace safety
{
SafetyEventMailbox rteTimingMailbox;

namespace supervision
{
namespace test
{
LpitDeadmanConfiguration const* expected = nullptr;
uint32_t result                          = 0U;
uint32_t counter                         = 0U;
uint32_t timerFields                     = 0U;
int timerReads                           = 0;
int timerStarts                          = 0;
} // namespace test

void startCycleTimer() { ++test::timerStarts; }

uint32_t readCycleTimer()
{
    ++test::timerReads;
    return test::counter & CYCLE_TIMER_MASK;
}

uint32_t checkCycleTimer() { return test::timerFields; }
} // namespace supervision
} // namespace safety

namespace
{
using ::safety::RomCheck;
using ::safety::SafeSupervisor;
using ::safety::TimeBaseCheck;
namespace sv  = ::safety::supervision;
namespace svt = ::safety::supervision::test;

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

constexpr sv::LpitDeadmanConfiguration DEADMAN{1U};

SafeSupervisor& fresh(bool const withDeadman)
{
    ::safety::safeSupervisorConstructor.construct();
    auto& supervisor = SafeSupervisor::getInstance();
    supervisor.leaveLimpHome();
    svt::expected          = withDeadman ? &DEADMAN : nullptr;
    svt::counter           = 1234U;
    svt::timerFields       = 0U;
    svt::timerReads        = 0;
    svt::progress          = sv::LpitCounterProgress::MOVING;
    svt::polls             = 0U;
    svt::progressChecks    = 0;
    test_time::nowMs       = 0xFFFFFF00U; // across the wrap of the ms
    test_time::reads       = 0;
    RomCheck::scriptedResult   = RomCheck::Result::ROMCHECK_OK;
    RomCheck::scriptedPeriodMs = 1000U;
    RomCheck::scriptedPasses   = 1U;
    ::safetyRomCheckProgress.restart();
    ::safetySystemTimeCheck.restart();
    ::test_trace::trace.clear();
    return supervisor;
}

/** One safety cycle 10 ms after the previous one: `timeMs` of system time and `ticks` of the LPTMR. */
void cycle(uint32_t const timeMs = 10U, uint32_t const ticks = 10U)
{
    test_time::nowMs += timeMs;
    svt::counter += ticks;
    ::safety::progress::cyclic();
}

bool quiet() { return ::test_trace::trace.find_first_of("EPR") == std::string::npos; }

void testHealthyCyclesRaiseNothing()
{
    for (uint32_t const lpoTicksPer100Ms : {89U, 100U, 108U}) // the LPTMR at 0.89, 1.0 and 1.08 kHz
    {
        auto& supervisor = fresh(true);
        for (int i = 1; i <= 3000; ++i)
        {
            // ticks of the LPTMR in this cycle: the LPO rate, spread over the cycles of 100 ms
            uint32_t const before = ((static_cast<uint32_t>(i) - 1U) * lpoTicksPer100Ms) / 10U;
            uint32_t const after  = (static_cast<uint32_t>(i) * lpoTicksPer100Ms) / 10U;
            if ((i % 100) == 0)
            {
                ++RomCheck::scriptedPasses; // a pass every second
            }
            cycle(10U, after - before);
        }
        CHECK(quiet());
        CHECK(!supervisor.inLimpHomeState());
        CHECK(svt::progressChecks == 3000);
        if (!quiet())
        {
            std::printf("  trace '%s'\n", ::test_trace::trace.c_str());
        }
    }
}

void testDeadmanBudgetAndStop()
{
    auto& supervisor = fresh(true);
    cycle();
    CHECK(svt::polls == 34U); // 2 x (64 MHz / 4 MHz) + 2
    svt::progress = sv::LpitCounterProgress::NOT_RUNNING;
    cycle();
    CHECK(quiet());
    svt::progress = sv::LpitCounterProgress::STOPPED;
    cycle();
    CHECK(::test_trace::trace.find('R') != std::string::npos); // S1 + S2
    CHECK(supervisor.deadmanConfigMonitor.getContext() == sv::DEADMAN_FIELD_COUNTER_STOPPED);
    // without a deadman the check is not called
    fresh(false);
    svt::progress = sv::LpitCounterProgress::STOPPED;
    for (int i = 0; i < 20; ++i)
    {
        cycle();
    }
    CHECK(svt::progressChecks == 0);
    CHECK(quiet());
}

void testStoppedSystemTime()
{
    auto& supervisor = fresh(false);
    cycle(); // reference
    for (int i = 1; i < 8; ++i)
    {
        cycle(0U, 10U);
        CHECK(quiet());
    }
    cycle(0U, 10U); // end of the first window
    CHECK(::test_trace::trace.find('R') != std::string::npos);
    uint32_t const context = supervisor.safetyCyclePeriodMonitor.getContext();
    CHECK(TimeBaseCheck::kindOf(context) == TimeBaseCheck::SYSTEM_TIME_SLOW);
    CHECK(TimeBaseCheck::valueOf(context) == 0U);
}

void testFastSystemTime()
{
    auto& supervisor = fresh(false);
    for (int i = 0; i < 9; ++i)
    {
        cycle(20U, 10U);
    }
    CHECK(TimeBaseCheck::kindOf(supervisor.safetyCyclePeriodMonitor.getContext()) == TimeBaseCheck::SYSTEM_TIME_FAST);
}

void testTimerConfigurationSuspendsTheComparison()
{
    fresh(false);
    cycle();
    cycle();
    svt::timerFields   = 0x2U; // TEN cleared: the period check reports kind 3
    int const reads    = test_time::reads;
    int const timerRds = svt::timerReads;
    for (int i = 0; i < 20; ++i)
    {
        cycle(0U, 0U);
    }
    CHECK(test_time::reads == reads);     // the system time was not compared
    CHECK(svt::timerReads == timerRds);   // nor the timer read
    CHECK(quiet());
    svt::timerFields = 0U;
    for (int i = 0; i < 30; ++i)
    {
        cycle();
    }
    CHECK(quiet()); // starts again from a new reference
}

void testRomCheckNotCalled()
{
    auto& supervisor = fresh(false);
    for (int i = 0; i < 300; ++i)
    {
        cycle(); // the first cycle takes the reference, then 299 without a pass
    }
    cycle(); // 300 cycles without a change: still within three periods
    CHECK(quiet());
    CHECK(!supervisor.inLimpHomeState());
    cycle(); // the 301st
    CHECK(supervisor.inLimpHomeState()); // ROM_CHECK_FAILURE: S1 held
    CHECK(::test_trace::trace.find('R') == std::string::npos);
    CHECK(supervisor.romCheckMonitor.getContext() == ::safety::progress::ROM_CHECK_CONTEXT_NOT_CALLED);
    std::string const after = ::test_trace::trace;
    for (int i = 0; i < 1000; ++i)
    {
        cycle();
    }
    CHECK(::test_trace::trace == after); // once
}

void testFirstPassNeverComes()
{
    auto& supervisor           = fresh(false);
    RomCheck::scriptedResult   = RomCheck::Result::ROMCHECK_REQUESTED;
    RomCheck::scriptedPasses   = 0U;
    RomCheck::scriptedPeriodMs = 60000U; // the module default: 3 x 6000 cycles
    for (int i = 0; i <= 18000; ++i)
    {
        cycle();
    }
    CHECK(!supervisor.inLimpHomeState());
    cycle();
    CHECK(supervisor.inLimpHomeState());
}

void testRomCheckNotWatched()
{
    // period 0: once per start, no deadline
    auto& supervisor           = fresh(false);
    RomCheck::scriptedPeriodMs = 0U;
    for (int i = 0; i < 5000; ++i)
    {
        cycle();
    }
    CHECK(!supervisor.inLimpHomeState());
    // after the ROM check reported a failure itself
    for (RomCheck::Result const r :
         {RomCheck::Result::ROMCHECK_FAILED, RomCheck::Result::ROMCHECK_OVERDUE, RomCheck::Result::ROMCHECK_UNIT_FAILED})
    {
        auto& s                  = fresh(false);
        RomCheck::scriptedResult = r;
        for (int i = 0; i < 1000; ++i)
        {
            cycle();
        }
        CHECK(!s.inLimpHomeState());
    }
}

void testDeadlineOfADay()
{
    CHECK(::safety::progress::romCheckDeadlineCycles(86400000U) == 25920000U); // 3 days of 10 ms cycles
    CHECK(::safety::progress::romCheckDeadlineCycles(0U) == 0U);
    CHECK(::safety::progress::romCheckDeadlineCycles(1U) == 1U); // rounded up
}

} // namespace

int main()
{
    struct
    {
        char const* name;
        void (*fn)();
    } const tests[] = {
        {"testHealthyCyclesRaiseNothing", testHealthyCyclesRaiseNothing},
        {"testDeadmanBudgetAndStop", testDeadmanBudgetAndStop},
        {"testStoppedSystemTime", testStoppedSystemTime},
        {"testFastSystemTime", testFastSystemTime},
        {"testTimerConfigurationSuspendsTheComparison", testTimerConfigurationSuspendsTheComparison},
        {"testRomCheckNotCalled", testRomCheckNotCalled},
        {"testFirstPassNeverComes", testFirstPassNeverComes},
        {"testRomCheckNotWatched", testRomCheckNotWatched},
        {"testDeadlineOfADay", testDeadlineOfADay},
    };
    for (auto const& test : tests)
    {
        int const before = failures;
        test.fn();
        std::printf("%s %s\n", (failures == before) ? "ok  " : "FAIL", test.name);
    }
    std::printf("%d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
