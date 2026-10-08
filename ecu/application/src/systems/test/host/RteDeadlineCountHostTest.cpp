/* COM-50: SIL test of the deadline violation counter of COM-45 (bsw.rte.deadline_violations, DID BswStatus), the real
 * RteSystem.cpp against the stubs of RteDeadlineHostTest.cpp - no target hardware, own test profile, not the eval board.
 *
 * Board 2026-09-30 evening (com47 image, test SWC LoadSwc: "2E 01 21 01 0F" = one run of LoadSwc.Run10ms with 15 ms
 * in the 10 ms task swc): "22 01 22" answered 2 per run, the board checklist expected 1. This test pins down that 2 is
 * the specified count, not a fault of the counter: the counter counts every violated deadline, and SYS-05 knows two
 * deadlines here - the period of the runnable (10 ms) and the period of its task cycle (10 ms). A runnable that misses
 * its own period in a task of the same period always takes the task cycle past its deadline, too (the cycle contains
 * the runnable), so one slow run counts 2. The same run of a runnable whose own period is longer than the task period
 * counts 1 (only the cycle), a cycle that is late only as a sum of runnables counts 1 as well.
 *
 * Test profile (like RteDeadlineHostTest.cpp): task "swc" 10 ms with runnables of 10 ms and 100 ms, task "fast" 1 ms
 * with runnables of 1 ms and 5 ms. The counter is read the way the diagnosis reads it: bswstatus::rte::readRteStatus
 * (the sum over all task cycles and runnables, saturating).
 *
 * What is checked:
 *   1. healthy load: 0;
 *   2. the board case - the 10 ms runnable needs 15 ms once: +2 (runnable + task cycle), the next cycle adds nothing;
 *   3. the 100 ms runnable needs 15 ms once in the 10 ms task: +1 (task cycle only, inside its own period);
 *   4. two runnables within their periods, the cycle 13 ms in sum: +1 (task cycle only);
 *   5. the 1 ms task: the 1 ms runnable needs 1.5 ms once: +2; the other task is not touched;
 *   6. a lasting violation of the 10 ms runnable for n cycles: +2n (every violation counts, not one per window);
 *   7. boundary: exactly the period is not a violation.
 *
 * Build and run (from the repository root):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -Iecu/application/include -Iecu/application/src/systems -Iecu/application/src/systems/test/host/stubs \
 *       ecu/application/src/systems/test/host/RteDeadlineCountHostTest.cpp \
 *       ecu/application/src/systems/RteSystem.cpp -o build/host/rte_deadline_count_test.exe
 *   build/host/rte_deadline_count_test.exe
 */

#include "systems/RteSystem.h"

#include <async/Async.h>
#include <bsp/timer/SystemTimer.h>

#include <cstdio>

namespace bswstatus
{
namespace rte
{
void readRteStatus(uint32_t& violations); // RteSystem.cpp, declared on the target by the generated RteConfig.cpp
} // namespace rte
} // namespace bswstatus

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
unsigned checks   = 0U; // the test runner reads "<n> checks, 0 failures" (tools/test, kind zig)

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

uint32_t violations()
{
    uint32_t v = 0U;
    ::bswstatus::rte::readRteStatus(v);
    return v;
}

/* One activation of a task, as in RteDeadlineHostTest.cpp: an overrun does not shift the rate. */
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

/* 10 ms of the ECU: one cycle of swc, ten of fast. */
void step(unsigned const n)
{
    for (unsigned i = 0U; i < n; ++i)
    {
        runCycles(0U, 1U);
        runCycles(1U, 10U);
    }
}

/* Runs until the 100 ms runnable of swc is due in the next cycle of swc (its tick is 0). */
void untilRun100msDue()
{
    unsigned const before = calls[1];
    while (calls[1] == before)
    {
        step(1U);
    }
    step(9U); // the next call is in the 10th cycle after the last one
}
} // namespace

int main()
{
    ::systems::RteSystem rteSystem;
    rteSystem.init();
    rteSystem.run();
    checkEqual(static_cast<uint32_t>(::async::scheduledCount), 2U, "both application tasks were scheduled");

    // 1. healthy load
    step(20U);
    checkEqual(violations(), 0U, "1: healthy load counts nothing");

    // 2. the board case: the 10 ms runnable needs 15 ms once (LoadSwc.Run10ms, "2E 01 21 01 0F")
    uint32_t base = violations();
    cost[0]       = 15000U;
    runCycles(0U, 1U);
    cost[0] = 100U;
    runCycles(1U, 10U);
    checkEqual(violations() - base, 2U, "2: one 15 ms run of the 10 ms runnable counts 2 (runnable + task cycle)");
    step(5U);
    checkEqual(violations() - base, 2U, "2: the following clean cycles add nothing");

    // 3. the 100 ms runnable needs 15 ms once in the 10 ms task: inside its own period, only the cycle is late
    untilRun100msDue();
    base    = violations();
    cost[1] = 15000U;
    unsigned const before100 = calls[1];
    runCycles(0U, 1U);
    cost[1] = 200U;
    checkEqual(calls[1] - before100, 1U, "3: the 100 ms runnable ran in this cycle");
    checkEqual(violations() - base, 1U, "3: 15 ms of the 100 ms runnable counts 1 (task cycle only)");
    runCycles(1U, 10U);

    // 4. both runnables within their periods, the cycle late only as their sum (8 ms + 5 ms)
    untilRun100msDue();
    base    = violations();
    cost[0] = 8000U;
    cost[1] = 5000U;
    runCycles(0U, 1U);
    cost[0] = 100U;
    cost[1] = 200U;
    checkEqual(violations() - base, 1U, "4: a cycle late as a sum of runnables counts 1");
    runCycles(1U, 10U);

    // 5. the 1 ms task: its 1 ms runnable needs 1.5 ms once
    base    = violations();
    cost[2] = 1500U;
    runCycles(1U, 1U);
    cost[2] = 20U;
    checkEqual(violations() - base, 2U, "5: 1.5 ms of the 1 ms runnable counts 2 (runnable + task cycle)");
    runCycles(1U, 9U);
    runCycles(0U, 1U);
    checkEqual(violations() - base, 2U, "5: the other task is not touched");

    // 6. a lasting violation: every violation counts (not one per measurement window like the supervisor event)
    base    = violations();
    cost[0] = 15000U;
    step(7U);
    cost[0] = 100U;
    checkEqual(violations() - base, 14U, "6: seven slow cycles count 14");

    // 7. boundary: exactly the period of runnable and task cycle is no violation, one microsecond more counts 2
    base    = violations();
    cost[0] = 10000U;
    cost[1] = 0U; // the cycle is exactly the 10 ms runnable, whether the 100 ms runnable is due or not
    runCycles(0U, 1U);
    checkEqual(violations() - base, 0U, "7: exactly the period is no violation");
    cost[0] = 10001U;
    runCycles(0U, 1U);
    checkEqual(violations() - base, 2U, "7: one microsecond more counts 2");
    cost[0] = 100U;
    cost[1] = 200U;

    rteSystem.shutdown();

    (void)printf("rte deadline count host test: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
