/* SYS-41 (SM-04 and SM-06 of doc/safety/fmea.md, task T2): host test of the safety cycle with the real
 * SafetyManager (safeLifecycle/SafetyManager.cpp) and the real SafeSupervisor (reaction); the sub-checks are stubs
 * that record themselves (stubs/cycleStubs.h: p period timer check, m memory, s system, o IO, W watchdog,
 * d deadman; E log error, P synchronous console line (SYS-52), R reset). SYS-40: the period check runs with the
 * real CyclePeriodCheck.cpp and the bounds SafetyManager.cpp derives; only the timer (LptmrCycleTimer) is scripted.
 *
 * What it shows:
 * - checkpoints per sub-check: the order SafetyManager::cyclic hits them matches SafeSupervisor::SafetyManagerSequence
 *   - many cycles without an event, the watchdog every 8th cycle;
 * - a cycle that ended early (checkpoints ENTER and MEMORY_CHECKED, then nothing) is a
 *   SAFETY_MANAGER_SEQUENCE_DEVIATION at the next cycle - before SYS-41 only the brackets were checked and such a
 *   cycle, if it still reached LEAVE, passed;
 * - the deadman check runs in every cycle from the safety task when the project has a deadman, and a deviation is
 *   DEADMAN_CONFIGURATION_ERROR with the fields in the context (S1 + S2); without a deadman nothing is checked.
 * - SYS-40 (F9, SM-04) - the slowed cycle: cycle starts are simulated in real time (nominal 10 ms grid, up to 2 ms
 *   late, catch-up of the fixed rate) against an LPO tick anywhere in 0.882 ... 1.102 kHz with an arbitrary phase.
 *   Healthy runs never report; a cycle of 17 ms or more is SAFETY_CYCLE_PERIOD_VIOLATION (kind 1) at its own start,
 *   before any other check of that cycle - whatever the LPO; a task at 5 ms and a stopped timer are kind 2 at the end
 *   of the first window; a deviating timer configuration is kind 3 and the timer is not read.
 *
 * Built-in faults (run by hand for SYS-41/SYS-40): a checkpoint removed or two swapped in SafetyManager.cpp, the
 * deadman check not called or its result ignored, the period check moved behind the memory check, the lateness or
 * the LPO tolerance left out of the bounds, the window check dropped - each makes this test red.
 *
 * Build and run (zig c++, no hardware):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -Iplatforms/s32k1xx/port/safety/safeLifecycle/test/host/stubs \
 *       -Iplatforms/s32k1xx/port/safety/safeLifecycle/include \
 *       -Iecu/safety/safeSupervisor/include -Iecu/safety/safeSupervisor/test/host/stubs \
 *       -isystem external/openbsw/libs/safety/safeMonitor/include \
 *       platforms/s32k1xx/port/safety/safeLifecycle/test/host/SafetyCycleHostTest.cpp \
 *       platforms/s32k1xx/port/safety/safeLifecycle/src/safeLifecycle/SafetyManager.cpp \
 *       ecu/safety/safeWatchdog/src/safeWatchdog/CyclePeriodCheck.cpp \
 *       -o build/host/safetycycle.exe
 */

#include <cmath>
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

#include <safeLifecycle/SafetyManager.h>
#include <safeSupervisionCheck/LpitDeadmanCheck.h>
#include <safeSupervisionCheck/LptmrCycleTimer.h>
#include <safeWatchdog/CyclePeriodCheck.h>

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

// SYS-40: the scripted cycle timer (stubs/safeSupervisionCheck/LptmrCycleTimer.h)
void startCycleTimer() { ++test::timerStarts; }

uint32_t readCycleTimer()
{
    ++test::timerReads;
    return test::counter & CYCLE_TIMER_MASK;
}

uint32_t checkCycleTimer()
{
    ::test_trace::add('p');
    return test::timerFields;
}
} // namespace supervision
} // namespace safety

// SYS-40: state of the period check, defined in SafetyManager.cpp
extern ::safety::CyclePeriodCheck safetyCyclePeriodCheck;

namespace
{
using ::safety::SafeSupervisor;
using ::safety::SafetyManager;
using Sequence = SafeSupervisor::SafetyManagerSequence;
namespace svt  = ::safety::supervision::test;

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

constexpr ::safety::supervision::LpitDeadmanConfiguration DEADMAN{1U};

SafeSupervisor& fresh(bool const withDeadman)
{
    ::safety::safeSupervisorConstructor.construct();
    auto& supervisor = SafeSupervisor::getInstance();
    supervisor.leaveLimpHome();
    ::test_cycle::gateLocked = true;
    svt::expected            = withDeadman ? &DEADMAN : nullptr;
    svt::result              = 0U;
    svt::counter             = 0U;
    svt::timerFields         = 0U;
    svt::timerReads          = 0;
    ::safetyCyclePeriodCheck.restart();
    ::test_trace::trace.clear();
    return supervisor;
}

/** SYS-40: one cycle `ticks` of the timer after the previous one (a nominal 10 ms cycle is 10 ticks). */
void cycleAfter(SafetyManager& manager, uint32_t const ticks)
{
    svt::counter += ticks;
    manager.cyclic();
}

/** Trace of n healthy cycles from a fresh manager: the watchdog step in every 8th cycle, before the sub-checks. */
std::string healthy(int const cycles, bool const withDeadman)
{
    std::string t;
    for (int i = 1; i <= cycles; ++i)
    {
        t += 'p';
        if ((i % 8) == 0)
        {
            t += 'W';
        }
        t += "mso";
        if (withDeadman)
        {
            t += 'd';
        }
    }
    return t;
}

void testCheckpointsMatchTheCycle()
{
    for (bool const withDeadman : {true, false})
    {
        auto& supervisor = fresh(withDeadman);
        SafetyManager manager;
        for (int i = 0; i < 24; ++i)
        {
            cycleAfter(manager, 10U);
        }
        CHECK(::test_trace::trace == healthy(24, withDeadman));
        CHECK(!supervisor.inLimpHomeState());
        if (::test_trace::trace != healthy(24, withDeadman))
        {
            std::printf("  trace '%s'\n", ::test_trace::trace.c_str());
        }
    }
}

void testCycleThatEndedEarlyIsDetected()
{
    // A cycle that returned after the memory check (left out system, IO and deadman, and the LEAVE): the checkpoints
    // it did hit are the ones of a real cycle. The next cycle's ENTER is out of order.
    auto& supervisor = fresh(true);
    SafetyManager manager;
    cycleAfter(manager, 10U);
    supervisor.safetyManagerSequenceMonitor.hit(Sequence::SAFETY_MANAGER_ENTER);
    supervisor.safetyManagerSequenceMonitor.hit(Sequence::SAFETY_MANAGER_PERIOD_CHECKED);
    supervisor.safetyManagerSequenceMonitor.hit(Sequence::SAFETY_MANAGER_MEMORY_CHECKED);
    CHECK(!supervisor.inLimpHomeState());
    ::test_trace::trace.clear();
    cycleAfter(manager, 10U);
    CHECK(::test_trace::trace.substr(0, 3) == "EPR"); // at the ENTER of the next cycle (SYS-59: with its line)
    CHECK(supervisor.inLimpHomeState());
}

void testSkippedSubCheckIsDetected()
{
    // A cycle that reached LEAVE but left out the IO check: with brackets only (before SYS-41) this passed.
    auto& supervisor = fresh(true);
    auto& m          = supervisor.safetyManagerSequenceMonitor;
    m.hit(Sequence::SAFETY_MANAGER_ENTER);
    m.hit(Sequence::SAFETY_MANAGER_PERIOD_CHECKED);
    m.hit(Sequence::SAFETY_MANAGER_MEMORY_CHECKED);
    m.hit(Sequence::SAFETY_MANAGER_SYSTEM_CHECKED);
    CHECK(::test_trace::trace.empty());
    m.hit(Sequence::SAFETY_MANAGER_PERIPHERALS_CHECKED); // IO_CHECKED missing
    CHECK(::test_trace::trace == "EPR"); // SYS-59: every reset writes its console line (P) first
}

void testDeadmanDeviationResets()
{
    auto& supervisor = fresh(true);
    SafetyManager manager;
    cycleAfter(manager, 10U);
    svt::result = 0x06U; // MCR[DBG_EN] and MIER
    ::test_trace::trace.clear();
    cycleAfter(manager, 10U);
    CHECK(::test_trace::trace == "pmsodEPR"); // SYS-59: every reset writes its console line (P) first
    CHECK(supervisor.deadmanConfigMonitor.getContext() == 0x06U);
    CHECK(supervisor.inLimpHomeState());
}

void testWithoutDeadmanNothingIsChecked()
{
    auto& supervisor = fresh(false);
    SafetyManager manager;
    svt::result = 0xFFU; // would report if the check ran
    cycleAfter(manager, 10U);
    CHECK(::test_trace::trace == "pmso");
    CHECK(!supervisor.inLimpHomeState());
}

// ---- SYS-40 (F9, SM-04): the period of the safety task --------------------------------------------------------

using ::safety::CyclePeriodCheck;

/* Real time of the simulation: cycle starts in ms, the LPO tick as a rate (ticks per ms) and a phase. The counter is
 * floor(t * rate + phase), 16 bit - started near the wrap-around, so every run also crosses it. */
struct Clock
{
    double rate;
    double phase;

    uint32_t at(double const ms) const
    {
        return static_cast<uint32_t>(std::floor((ms * rate) + phase + 65000.0)) & 0xFFFFU;
    }
};

// The limits of the LPO1K tick (LptmrCycleTimer.h) and nominal
constexpr double RATES[] = {0.8828, 0.95, 1.0, 1.05, 1.1016};
constexpr double PHASES[] = {0.0, 0.25, 0.5, 0.999};

uint32_t lcg = 12345U;

/** Lateness of a cycle start, 0 ... 2 ms (CYCLE_LATENESS_MS), pseudo random but reproducible. */
double lateness()
{
    lcg = (lcg * 1103515245U) + 12345U;
    return 2.0 * static_cast<double>((lcg >> 8) & 0xFFFFU) / 65535.0;
}

/** Runs the cycle at real time `ms`; returns the trace of that cycle. */
std::string cycleAt(SafetyManager& manager, Clock const& clock, double const ms)
{
    ::test_trace::trace.clear();
    svt::counter = clock.at(ms);
    manager.cyclic();
    return ::test_trace::trace;
}

bool reported(std::string const& t) { return t.find('E') != std::string::npos; }

void testHealthyPeriodNeverReports()
{
    // Every LPO within its data sheet range, every phase, lateness 0 ... 2 ms on the fixed-rate grid (a late start is
    // followed by a short interval: the catch-up), plus the two extreme patterns: alternating 0/2 ms and 2 ms once.
    int events = 0;
    for (double const rate : RATES)
    {
        for (double const phase : PHASES)
        {
            for (int pattern = 0; pattern < 3; ++pattern)
            {
                auto& supervisor = fresh(true);
                SafetyManager manager;
                Clock const clock{rate, phase};
                for (int k = 0; k < 400; ++k)
                {
                    double late = 0.0;
                    if (pattern == 0)
                    {
                        late = lateness();
                    }
                    else if (pattern == 1)
                    {
                        late = ((k % 2) == 0) ? 2.0 : 0.0;
                    }
                    else
                    {
                        late = (k == 200) ? 2.0 : 0.0;
                    }
                    if (reported(cycleAt(manager, clock, (10.0 * k) + late)))
                    {
                        ++events;
                        std::printf("  rate %.4f phase %.3f pattern %d cycle %d: reported\n", rate, phase, pattern, k);
                    }
                }
                CHECK(!supervisor.inLimpHomeState());
            }
        }
    }
    CHECK(events == 0);
}

void testSlowedCycleIsReportedBeforeItsChecks()
{
    // One cycle 17 ms after the previous one - reported at its own start, before the memory check, whatever the LPO.
    for (double const rate : RATES)
    {
        for (double const phase : PHASES)
        {
            auto& supervisor = fresh(true);
            SafetyManager manager;
            Clock const clock{rate, phase};
            for (int k = 0; k < 20; ++k)
            {
                (void)cycleAt(manager, clock, 10.0 * k);
            }
            CHECK(!supervisor.inLimpHomeState());
            std::string const t = cycleAt(manager, clock, (10.0 * 19) + 17.0);
            CHECK(t.substr(0, 4) == "pEPR");
            auto const context = supervisor.safetyCyclePeriodMonitor.getContext();
            CHECK(CyclePeriodCheck::kindOf(context) == CyclePeriodCheck::INTERVAL_TOO_LONG);
            CHECK(CyclePeriodCheck::valueOf(context) >= 15U);
            CHECK(supervisor.inLimpHomeState());
        }
    }
}

void testSteadilySlowTaskIsReportedAtTheFirstSlowCycle()
{
    // The case of finding F9: a task at 20 ms (below the watchdog's 31.25 ms) - before SYS-40 never reported.
    auto& supervisor = fresh(false);
    SafetyManager manager;
    Clock const clock{0.8828, 0.0}; // slowest LPO: the hardest case for "too long"
    (void)cycleAt(manager, clock, 0.0);
    std::string const t = cycleAt(manager, clock, 20.0);
    CHECK(t.substr(0, 4) == "pEPR");
    CHECK(CyclePeriodCheck::kindOf(supervisor.safetyCyclePeriodMonitor.getContext())
          == CyclePeriodCheck::INTERVAL_TOO_LONG);
}

void testFastTaskIsReportedAtTheEndOfTheWindow()
{
    // 5 ms at the nominal tick, and 7 ms at the fastest LPO (the edge claimed in SafetyManager.cpp: about 7.6 ms)
    for (double const period : {5.0, 7.0})
    {
        auto& supervisor = fresh(false);
        SafetyManager manager;
        Clock const clock{(period < 6.0) ? 1.0 : 1.1016, 0.5};
        (void)cycleAt(manager, clock, 0.0); // reference
        int reportedAt = 0;
        for (int k = 1; (k <= 16) && (reportedAt == 0); ++k)
        {
            if (reported(cycleAt(manager, clock, period * k)))
            {
                reportedAt = k;
            }
        }
        CHECK(reportedAt == static_cast<int>(SafetyManager::CYCLE_PERIOD_WINDOW)); // end of the first window
        CHECK(CyclePeriodCheck::kindOf(supervisor.safetyCyclePeriodMonitor.getContext())
              == CyclePeriodCheck::WINDOW_TOO_SHORT);
    }
}

void testStoppedTimerIsReported()
{
    // A timer that stopped (LPO1K gated, counter frozen) would blind the check - the window sees 0 ticks.
    auto& supervisor = fresh(false);
    SafetyManager manager;
    svt::counter = 4711U;
    for (uint32_t k = 0U; k <= SafetyManager::CYCLE_PERIOD_WINDOW; ++k)
    {
        ::test_trace::trace.clear();
        manager.cyclic();
    }
    CHECK(::test_trace::trace.substr(0, 4) == "pEPR");
    auto const context = supervisor.safetyCyclePeriodMonitor.getContext();
    CHECK(CyclePeriodCheck::kindOf(context) == CyclePeriodCheck::WINDOW_TOO_SHORT);
    CHECK(CyclePeriodCheck::valueOf(context) == 0U);
}

void testTimerConfigurationDeviationIsReportedWithoutRead()
{
    auto& supervisor = fresh(false);
    SafetyManager manager;
    cycleAfter(manager, 10U);
    int const reads  = svt::timerReads;
    svt::timerFields = 0x02U; // CSR[TEN] cleared
    ::test_trace::trace.clear();
    cycleAfter(manager, 10U);
    CHECK(::test_trace::trace.substr(0, 4) == "pEPR");
    auto const context = supervisor.safetyCyclePeriodMonitor.getContext();
    CHECK(CyclePeriodCheck::kindOf(context) == CyclePeriodCheck::TIMER_CONFIGURATION);
    CHECK(CyclePeriodCheck::valueOf(context) == 0x02U);
    CHECK(svt::timerReads == reads); // a cleared clock gate would make the read a bus fault
}

void testFirstCycleIsTheReference()
{
    // Whatever the counter shows at the first cycle (the time since init is no cycle), it is not an interval.
    auto& supervisor = fresh(false);
    SafetyManager manager;
    svt::counter = 40000U;
    manager.cyclic();
    CHECK(::test_trace::trace == "pmso");
    CHECK(!supervisor.inLimpHomeState());
}
} // namespace

int main()
{
    testCheckpointsMatchTheCycle();
    testCycleThatEndedEarlyIsDetected();
    testSkippedSubCheckIsDetected();
    testDeadmanDeviationResets();
    testWithoutDeadmanNothingIsChecked();
    testHealthyPeriodNeverReports();
    testSlowedCycleIsReportedBeforeItsChecks();
    testSteadilySlowTaskIsReportedAtTheFirstSlowCycle();
    testFastTaskIsReportedAtTheEndOfTheWindow();
    testStoppedTimerIsReported();
    testTimerConfigurationDeviationIsReportedWithoutRead();
    testFirstCycleIsTheReference();
    std::printf("%d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
