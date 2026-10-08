/* COM-47: SIL test of the BSW event rte.deadline - the real deadline supervision of RteSystem.cpp (SYS-05), the real
 * reporter dem/DeadlineReport.h and the real fault memory dem::Manager (libs/bsw/dem), no target hardware.
 *
 * Board 2026-09-30 (TOOL-131 test image, a runnable of 15 ms once in the 10 ms task): 14 FF FF FF and "dem clear"
 * answered positively, but 19 02 FF showed 0x2F again at once, also after "rte reset"; only 11 01 gave 0x50. Cause:
 * RteSystem reported FAILED once and never PASSED while running, and the fault memory keeps a reported level and
 * samples it every cycle - so every clear was followed by the same FAILED sample. This test replays the board
 * sequence and more; the mutants of test_rte_deadline_dem.py (the old behaviour among them) must fail it.
 *
 * RteSystem.cpp is compiled against the stubs of RteDeadlineHostTest.cpp (virtual clock, fake scheduler, own test
 * profile, not the eval board: task "swc" 10 ms with runnables of 10/100 ms, task "fast" 1 ms with 1/5 ms) and with
 * NX_DEM_RTE_DEADLINE, as the generated dem/demBswReporters.h sets it for a project with an rte.deadline DTC. The
 * test plays the context of the fault memory: every 10 ms (its cycle) readDeadlineState + DeadlineReport::update +
 * Manager::mainFunction, exactly what DemCycle of RteSystem.cpp runs on the target.
 *
 * Two fault memories read the same supervision: A with the debouncing of the standard diagnostics (counter 1/-1),
 * B with passed_threshold -5 (five clean cycles of the fault memory before PASSED) - the configured debouncing decides.
 *
 * What is checked:
 *   1. no deadline checked yet: no result, status stays 0x50;
 *   2. healthy load: PASSED, status 0x00 (tested, never failed);
 *   3. the board case, 15 ms once in the 10 ms task: 0x2F (failed, pending, confirmed, stored) - like the board;
 *   4. the next clean cycle: testFailed cleared (0x2E), B only after five clean cycles;
 *   5. clear (14 FF FF FF): 0x50, then PASSED: 0x00 and stays so, occurrence 0 - before COM-47 0x2F again;
 *   6. a lasting violation: failed again after a clear (the fault is really there), PASSED once it ends;
 *   7. a 100 ms runnable that missed once keeps the level until its next call met the deadline, then PASSED;
 *   8. without a fault memory the violation is not lost: the first fault memory samples it failed;
 *   9. 85 off while the deadlines recover (reports discarded, COM-43): after 85 on PASSED, not the level of before.
 *
 * Build and run (from the repository root):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -DNX_DEM_RTE_DEADLINE=1 -Iecu/application/include -Iecu/application/src/systems \
 *       -Iecu/application/src/systems/test/host/stubs -Ilibs/bsw/dem/include \
 *       ecu/application/src/systems/test/host/RteDeadlineDemHostTest.cpp \
 *       ecu/application/src/systems/RteSystem.cpp -o build/host/rte_deadline_dem_test.exe
 *   build/host/rte_deadline_dem_test.exe
 */

#include "dem/DeadlineReport.h"
#include "systems/RteSystem.h"

#include <async/Async.h>
#include <bsp/timer/SystemTimer.h>
#include <dem/Dem.h>

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

// ---------------------------------------------------------------- the fault memories of the test

namespace
{
struct NoLock
{
    NoLock() {} // user-provided: "Lock const lock;" in dem::Manager
};

using Manager = ::dem::Manager<NoLock>;

constexpr uint32_t DTC_NUMBER = 0x160100U; // +0x0100 of the neXtBSW block (tools/nxconfig/bswdtc.py)

::dem::DtcConfig makeDtc(int8_t const passedThreshold)
{
    ::dem::DtcConfig d{};
    d.name                  = "RteDeadline";
    d.number                = DTC_NUMBER;
    d.severity              = 0x20U;
    d.priority              = 2U;
    d.confirmationThreshold = 1U;
    d.agingThreshold        = 40U;
    d.cycle                 = 0U;
    d.agingCycle            = 0U;
    d.conditions            = 0U;
    d.debounce              = ::dem::DebounceKind::COUNTER;
    d.counter               = ::dem::CounterDebounce{1, passedThreshold, 1U, 1U, 0, 0};
    d.time                  = ::dem::TimeDebounce{0U, 0U};
    d.snapshots             = nullptr;
    d.snapshotCount         = 0U;
    d.extended              = nullptr;
    d.extendedCount         = 0U;
    d.reportedBy            = ::rtesupervision::DEADLINE_EVENT;
    return d;
}

::dem::DtcConfig const DTC_A[] = {makeDtc(-1)}; // standard diagnostics (tools/diaggen/standard_diag.yaml)
::dem::DtcConfig const DTC_B[] = {makeDtc(-5)}; // five clean cycles of the fault memory before PASSED

::dem::Config const CONFIG_A = {DTC_A, 1U, 2U, true, 1U, 0x01U, 0U, 0U, 10U, 1U};
::dem::Config const CONFIG_B = {DTC_B, 1U, 2U, true, 1U, 0x01U, 0U, 0U, 10U, 1U};

struct Memory
{
    explicit Memory(::dem::Config const& config) : manager(config, states, entries, data, scratch) {}

    ::dem::DtcState states[1]{};
    ::dem::Entry entries[2]{};
    uint8_t data[2]{};
    uint8_t scratch[1]{};
    Manager manager;
    ::rtesupervision::DeadlineReport report;
};

class NoSource final : public ::dem::ISource
{
public:
    ::dem::Result sample(uint16_t const /*dtc*/) override { return ::dem::Result::NONE; }

    bool condition(uint8_t const /*condition*/) override { return true; }

    uint8_t cycleState(uint8_t const /*cycle*/) override { return CYCLE_AUTOMATIC; }
};

NoSource source;
Memory* memA = nullptr;
Memory* memB = nullptr;

// ---------------------------------------------------------------- test helpers

unsigned failures = 0U;
unsigned checks   = 0U; // the test runner reads "<n> checks, 0 failures"

void checkEqual(uint32_t const actual, uint32_t const expected, char const* const what)
{
    ++checks;
    if (actual != expected)
    {
        ++failures;
        (void)printf("FAIL: %s: 0x%02X, expected 0x%02X\n", what, static_cast<unsigned>(actual),
                     static_cast<unsigned>(expected));
    }
}

::dem::DtcInfo info(Memory const& m)
{
    ::dem::DtcInfo i{};
    (void)m.manager.dtcInfo(0U, i);
    return i;
}

uint32_t statusA() { return info(*memA).status; }

uint32_t statusB() { return info(*memB).status; }

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

/* One cycle of the fault memory (context of the NvM on the target, DemCycle of RteSystem.cpp): level, then sample. */
void demCycle(Memory& m)
{
    ::rtesupervision::DeadlineState state{};
    ::rtesupervision::readDeadlineState(state);
    m.report.update(state, &m.manager);
    m.manager.mainFunction(source);
}

void demCycles()
{
    demCycle(*memA);
    demCycle(*memB);
}

/* 10 ms of the ECU: one cycle of swc, ten of fast, one cycle of each fault memory. */
void step(unsigned const n)
{
    for (unsigned i = 0U; i < n; ++i)
    {
        runCycles(0U, 1U);
        runCycles(1U, 10U);
        demCycles();
    }
}
} // namespace

int main()
{
    Memory a(CONFIG_A);
    Memory b(CONFIG_B);
    memA = &a;
    memB = &b;
    a.manager.start();
    b.manager.start();

    ::systems::RteSystem rteSystem;
    rteSystem.init();
    rteSystem.run();
    checkEqual(static_cast<uint32_t>(::async::scheduledCount), 2U, "both application tasks were scheduled");

    // 1. no deadline checked yet: no result
    demCycles();
    checkEqual(statusA(), 0x50U, "1: nothing checked, status stays 0x50");

    // 2. healthy load: tested and passed, each after its debouncing
    step(1U);
    checkEqual(statusA(), 0x00U, "2: healthy load passes (A)");
    checkEqual(statusB(), 0x50U, "2: B not matured after one clean cycle");
    step(4U);
    checkEqual(statusB(), 0x00U, "2: healthy load passes (B, five clean cycles)");

    // 3. the board case: Demo.Run10ms needs 15 ms once in the 10 ms task (like LoadSwc.Run10ms, TOOL-131 image)
    cost[0] = 15000U;
    runCycles(0U, 1U);
    cost[0] = 100U;
    runCycles(1U, 10U);
    demCycles();
    checkEqual(statusA(), 0x2FU, "3: one missed deadline: failed, pending, confirmed (A, like the board)");
    checkEqual(statusB(), 0x2FU, "3: one missed deadline: failed, pending, confirmed (B)");
    checkEqual(info(a).occurrence, 1U, "3: occurrence 1");
    checkEqual(info(a).stored ? 1U : 0U, 1U, "3: stored");

    // 4. the deadline is met again: PASSED after the debouncing of each DTC
    step(1U);
    checkEqual(statusA(), 0x2EU, "4: met again: testFailed cleared at once (A, counter 1/-1)");
    checkEqual(statusB(), 0x2FU, "4: B still failed after one clean cycle");
    step(3U);
    checkEqual(statusB(), 0x2FU, "4: B still failed after four clean cycles");
    step(1U);
    checkEqual(statusB(), 0x2EU, "4: B passed after five clean cycles");

    // 5. clear (14 FF FF FF / "dem clear"): stays cleared, then PASSED - the board showed 0x2F again here
    (void)a.manager.clear(::dem::GROUP_ALL_DTCS);
    (void)b.manager.clear(::dem::GROUP_ALL_DTCS);
    checkEqual(statusA(), 0x50U, "5: cleared");
    step(1U);
    checkEqual(statusA(), 0x00U, "5: first cycle after the clear: PASSED, not failed again (A)");
    step(10U);
    checkEqual(statusA(), 0x00U, "5: stays passed (A)");
    checkEqual(statusB(), 0x00U, "5: stays passed (B)");
    checkEqual(info(a).occurrence, 0U, "5: occurrence stays 0 after the clear");
    checkEqual(info(a).stored ? 1U : 0U, 0U, "5: no entry after the clear");

    // 6. a lasting violation: a clear cannot hide it, it passes once the deadline is met again
    cost[0] = 15000U;
    step(3U);
    checkEqual(statusA(), 0x2FU, "6: lasting violation fails");
    (void)a.manager.clear(::dem::GROUP_ALL_DTCS);
    step(1U);
    checkEqual(statusA(), 0x2FU, "6: still violated: failed again after the clear (the fault is there)");
    cost[0] = 100U;
    step(1U);
    checkEqual(statusA(), 0x2EU, "6: met again: passed");
    (void)a.manager.clear(::dem::GROUP_ALL_DTCS);
    (void)b.manager.clear(::dem::GROUP_ALL_DTCS);
    step(2U);
    checkEqual(statusA(), 0x00U, "6: cleared and passed");

    // 7. Demo.Run100ms misses its 100 ms once: the level stays until its next call met the deadline
    unsigned const before = calls[1];
    cost[1]               = 100001U;
    while (calls[1] == before)
    {
        step(1U);
    }
    cost[1] = 200U;
    checkEqual(statusA(), 0x2FU, "7: the 100 ms runnable missed its period");
    step(5U);
    checkEqual(statusA(), 0x2FU, "7: not checked again yet: still failed (5 cycles later)");
    unsigned const missed = calls[1];
    while (calls[1] == missed)
    {
        step(1U);
    }
    checkEqual(statusA(), 0x2EU, "7: its next call met the deadline: passed");

    // 9. ControlDTCSetting off (85) while the deadlines recover: the reports are discarded (COM-43), after 85 on the
    //    level of now counts - not the failed one of before 85 off
    (void)a.manager.clear(::dem::GROUP_ALL_DTCS);
    step(2U);
    cost[0] = 15000U;
    step(1U);
    checkEqual(statusA(), 0x2FU, "9: violation before 85 off");
    a.manager.setDtcSetting(false);
    cost[0] = 100U;
    step(3U);
    checkEqual(statusA(), 0x2FU, "9: 85 off: no status change");
    a.manager.setDtcSetting(true);
    step(1U);
    checkEqual(statusA(), 0x2EU, "9: 85 on: the deadlines hold, passed");

    // 8. a violation before a fault memory runs is not lost (EventReporter semantics, counters of COM-45)
    Memory late(CONFIG_A);
    late.manager.start();
    cost[0] = 15000U;
    runCycles(0U, 1U);
    cost[0] = 100U;
    runCycles(0U, 1U); // met again before the fault memory ever ran
    late.report.update(::rtesupervision::DeadlineState{true, false, 0U}, nullptr); // no fault memory yet
    demCycle(late);
    checkEqual(info(late).status, 0x2FU, "8: the first fault memory samples the earlier violation");
    demCycle(late);
    checkEqual(info(late).status, 0x2EU, "8: then PASSED");

    rteSystem.shutdown();

    (void)printf("rte deadline dem host test: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
