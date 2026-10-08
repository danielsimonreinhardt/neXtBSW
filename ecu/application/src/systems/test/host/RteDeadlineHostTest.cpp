/* Host test of the RTE runtime supervision (SYS-05), no target hardware needed.
 *
 * The production ecu/application/src/systems/RteSystem.cpp is compiled against stubs (stubs/): a virtual
 * clock instead of the system timer, a fake scheduler that only records the task runnables so the test can
 * run the cycles itself, a console that does nothing, and a SafeSupervisor that records every trigger with
 * its context instead of entering limp home and resetting the MCU. What is tested is therefore the real
 * detection logic, driven by an artificially slow runnable.
 *
 * Own test profile (stubs/RteConfig.h), not the eval board: two application tasks with freely chosen
 * priorities and periods - "swc" (context 5, 10 ms) with runnables of 10 ms and 100 ms, "fast" (context 6,
 * 1 ms) with runnables of 1 ms and 5 ms - and one init runnable.
 *
 * What is checked:
 *   1. Healthy load: no event at all, so the supervisor is not woken by normal jitter.
 *   2. A runnable that needs 12 ms in a 10 ms task: two events - the runnable missed its own period and the
 *      cycle of its task missed the task period. Kind, index and overshoot are decoded from the context word.
 *   3. Latching: the same violation in every following cycle reports once, not once per cycle.
 *   4. A runnable slower than the task cycle but inside its own period (2 ms in the 1 ms task, own period
 *      5 ms): only the task cycle is an event, the runnable is not.
 *   5. Boundary: a runnable that needs exactly its period is not a violation, one microsecond more is. This
 *      is the deliberate fault of the test - a mutant with ">=" instead of ">" fails case 5, one without the
 *      latch fails case 3, one that takes the deadline from anywhere but the configured period fails case 4.
 *
 * Build and run (from the repository root):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion \
 *       -Werror -DNX_RTE_SAFE_SUPERVISOR=1 \
 *       -Iecu/application/include -Iecu/application/src/systems \
 *       -Iecu/application/src/systems/test/host/stubs \
 *       ecu/application/src/systems/test/host/RteDeadlineHostTest.cpp \
 *       ecu/application/src/systems/RteSystem.cpp -o build/host/rte_deadline_test.exe
 *   build/host/rte_deadline_test.exe
 */

#include "systems/RteSystem.h"

#include <async/Async.h>
#include <bsp/timer/SystemTimer.h>
#include <safeSupervisor/SafeSupervisor.h>

#include <cstdio>

// ---------------------------------------------------------------- stubs the test owns

namespace
{
uint32_t nowUs = 0U;

/* Cost of one call of each runnable in microseconds; the test changes them to make a runnable slow. */
uint32_t cost[5] = {100U, 200U, 20U, 30U, 0U};
unsigned calls[5]{};

void spend(size_t const index)
{
    nowUs += cost[index];
    ++calls[index];
}

void run10ms() { spend(0U); }

void run100ms() { spend(1U); }

void run1ms() { spend(2U); }

void run5ms() { spend(3U); }

void runInit() { spend(4U); }

bool value = true;
} // namespace

uint32_t getSystemTimeUs32Bit() { return nowUs; }

namespace safety
{
namespace
{
SafeSupervisor supervisorInstance;
} // namespace

SafeSupervisor& SafeSupervisor::getInstance() { return supervisorInstance; }
} // namespace safety

// ---------------------------------------------------------------- the test profile (generated on the target)

namespace rte
{
Runnable const RUNNABLES[] = {
    //  name                 function     periodMs offsetMs task  periodTicks offsetTicks
    {"Demo.Run10ms",  &run10ms,  10U,  0U, 0U,  1U,  0U},
    {"Demo.Run100ms", &run100ms, 100U, 0U, 0U, 10U,  0U},
    {"Demo.Run1ms",   &run1ms,   1U,   0U, 1U,  1U,  0U},
    {"Demo.Run5ms",   &run5ms,   5U,   0U, 1U,  5U,  0U},
    {"Demo.Init",     &runInit,  0U,   0U, NO_TASK, 0U, 0U},
};

static_assert(sizeof(RUNNABLES) / sizeof(RUNNABLES[0]) == RUNNABLE_TABLE_SIZE, "runnable table"); // RTE-19

Task const TASKS[] = {
    //  name    context periodMs firstRunnable runnableCount hyperTicks
    {"swc",  5U, 10U, 0U, 2U, 10U},
    {"fast", 6U,  1U, 2U, 2U,  5U},
};

Signal const SIGNALS[] = {
    {"Demo.Out.Value", nullptr, SignalType::BOOLEAN, &value},
};

size_t const SIGNAL_COUNT = 1U;

void init() {}

void readInputs(size_t const /*task*/) {}

void writeOutputs(size_t const /*task*/) {}
} // namespace rte

// ---------------------------------------------------------------- test helpers

namespace
{
unsigned failures = 0U;
unsigned checks   = 0U;   // the test runner reads "<n> checks, 0 failures" (tools/test, kind zig)

void check(bool const ok, char const* const what)
{
    ++checks;
    if (!ok)
    {
        ++failures;
        (void)printf("FAIL: %s\n", what);
    }
}

void checkEqual(uint32_t const actual, uint32_t const expected, char const* const what)
{
    ++checks;
    if (actual != expected)
    {
        ++failures;
        (void)printf("FAIL: %s: %u, expected %u\n", what, static_cast<unsigned>(actual),
                     static_cast<unsigned>(expected));
    }
}

/* Context word of an event: kind in bits 31..24, index in bits 23..16, overshoot in ms in bits 15..0. */
uint32_t kindOf(uint32_t const context) { return context >> 24U; }

uint32_t indexOf(uint32_t const context) { return (context >> 16U) & 0xFFU; }

uint32_t overshootMsOf(uint32_t const context) { return context & 0xFFFFU; }

constexpr uint32_t KIND_TASK_CYCLE = 1U;
constexpr uint32_t KIND_RUNNABLE   = 2U;

::safety::SafeSupervisor::TriggerMonitor& monitor()
{
    return ::safety::SafeSupervisor::getInstance().rteTimingMonitor;
}

/* One activation of a task: the runnable of the fake scheduler runs, then the clock moves to the next
 * activation. A cycle that overran does not shift the rate - the RTOS timer keeps the period. */
void runCycles(size_t const task, unsigned const cycles)
{
    ::async::Scheduled const& s = ::async::scheduled[task];
    for (unsigned c = 0U; c < cycles; ++c)
    {
        uint32_t const start   = nowUs;
        s.runnable->execute();
        uint32_t const elapsed = nowUs - start;
        uint32_t const period  = s.periodMs * 1000U;
        nowUs                  = start + ((elapsed > period) ? elapsed : period);
    }
}
} // namespace

int main()
{
    ::systems::RteSystem rteSystem;
    rteSystem.init();
    rteSystem.run();

    check(::async::scheduledCount == 2U, "both application tasks were scheduled");
    checkEqual(::async::scheduled[0].periodMs, 10U, "period of task swc");
    checkEqual(::async::scheduled[1].periodMs, 1U, "period of task fast");
    checkEqual(calls[4], 1U, "init runnable ran once in init()");

    // 1. healthy load: every runnable well inside its period
    runCycles(0U, 20U);
    runCycles(1U, 20U);
    checkEqual(monitor().triggers, 0U, "healthy load reports no event");
    check(calls[0] == 20U, "Demo.Run10ms ran in every cycle of swc");
    check(calls[1] == 2U, "Demo.Run100ms ran every tenth cycle of swc");

    // 2. artificially slow runnable: 12 ms in a task of 10 ms
    cost[0] = 12000U;
    runCycles(0U, 1U);
    checkEqual(monitor().triggers, 2U, "slow runnable reports runnable and task cycle");
    checkEqual(kindOf(monitor().recorded[0]), KIND_RUNNABLE, "first event is the runnable");
    checkEqual(indexOf(monitor().recorded[0]), 0U, "index of Demo.Run10ms");
    checkEqual(overshootMsOf(monitor().recorded[0]), 2U, "Demo.Run10ms is 2 ms over its period");
    checkEqual(kindOf(monitor().recorded[1]), KIND_TASK_CYCLE, "second event is the task cycle");
    checkEqual(indexOf(monitor().recorded[1]), 0U, "index of task swc");
    checkEqual(overshootMsOf(monitor().recorded[1]), 2U, "cycle of swc is 2 ms over its period");

    // 3. latching: the violation repeats every cycle, the event does not
    runCycles(0U, 5U);
    checkEqual(monitor().triggers, 2U, "a latched violation is reported once per window");

    // 4. slower than the task cycle, inside its own period: only the cycle is an event
    cost[0] = 100U;
    cost[3] = 2000U;
    runCycles(1U, 5U);
    checkEqual(monitor().triggers, 3U, "only the cycle of task fast is an event");
    checkEqual(kindOf(monitor().recorded[2]), KIND_TASK_CYCLE, "third event is a task cycle");
    checkEqual(indexOf(monitor().recorded[2]), 1U, "index of task fast");
    checkEqual(overshootMsOf(monitor().recorded[2]), 1U, "cycle of fast is 1 ms over its period");

    // 5. boundary: exactly the period is no violation, one microsecond more is
    cost[1] = 100000U;
    runCycles(0U, 10U);
    checkEqual(monitor().triggers, 3U, "a runnable that needs exactly its period is no violation");
    cost[1] = 100001U;
    runCycles(0U, 10U);
    checkEqual(monitor().triggers, 4U, "one microsecond over the period is a violation");
    checkEqual(kindOf(monitor().recorded[3]), KIND_RUNNABLE, "fourth event is the runnable");
    checkEqual(indexOf(monitor().recorded[3]), 1U, "index of Demo.Run100ms");
    checkEqual(overshootMsOf(monitor().recorded[3]), 0U, "overshoot below a millisecond is reported as 0");

    rteSystem.shutdown();

    (void)printf("rte deadline host test: %u checks, %u failures (%u events)\n", checks, failures,
                 static_cast<unsigned>(monitor().triggers));
    return (failures == 0U) ? 0 : 1;
}
