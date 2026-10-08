/* ARCH-14 host test for the DWT based SystemTimer.cpp of the s32k1xx board port against a register model.
 *
 * The model counts the exact simulated time in units of 1 / lcm(core clock, FIRC) s next to the 32 bit DWT cycle
 * counter, so every value of the system timer can be compared with the exact number of ticks. A reference model of
 * the OpenBSW algorithm (lastDwt = curDwt) is fed with the same DWT reads and shows the drift of the old code.
 * PLAT-67: the model stops the counter while DEMCR.TRCENA or DWT_CTRL.CYCCNTENA is 0 (a debugger that closes its
 * session clears TRCENA) and then answers reads with an UNKNOWN value; testDebuggerDisconnect() checks the restart.
 * Profiles: -DNX_CLOCK_CORE_HZ=80000000U (eval board), default 64 MHz, 8 MHz; FIRC 48 MHz.
 * Build and run: see doc/changelog.d/ARCH-14.md (zig c++, no target hardware needed). */

#include "bsp/timer/SystemTimer.h"
#include "interrupts/SuspendResumeAllInterruptsScopedLock.h"
#include "mcu/mcu.h"

#include <bsp/clock/boardClock.h>

#include <cinttypes>
#include <cstdio>
#include <functional>
#include <numeric>
#include <random>
#include <vector>

extern "C"
{
void initSystemTimerHelper(bool sleep);
uint32_t getSystemTimerCycleCounterRestarts(void);
}

namespace systemTimerModel
{
DwtModel dwt;
CoreDebugModel coreDebug;
LpitModel lpit0;
} // namespace systemTimerModel

namespace
{
int failures = 0;
int checks   = 0;

#define CHECK(cond)                                                                                  \
    do                                                                                               \
    {                                                                                                \
        ++checks;                                                                                    \
        if (!(cond))                                                                                 \
        {                                                                                            \
            ++failures;                                                                              \
            std::printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);                            \
        }                                                                                            \
    } while (false)

uint64_t const RUN_HZ         = NX_CLOCK_CORE_HZ;
uint64_t const IDLE_HZ        = NX_CLOCK_FIRC_HZ;
uint64_t const TICK_HZ        = std::gcd(RUN_HZ, IDLE_HZ);
uint64_t const UNIT_HZ        = std::lcm(RUN_HZ, IDLE_HZ); // exact time unit of the model
uint64_t const UNITS_PER_TICK = UNIT_HZ / TICK_HZ;
uint32_t const RATIO_RUN      = static_cast<uint32_t>(RUN_HZ / TICK_HZ);
uint32_t const RATIO_IDLE     = static_cast<uint32_t>(IDLE_HZ / TICK_HZ);
uint64_t const CYCLES_PER_US  = RUN_HZ / 1000000U;

struct Simulation
{
    uint32_t cycCnt = 0;  // DWT_CYCCNT, wraps at 2^32
    uint64_t units  = 0;  // exact simulated time
    bool idle       = false;
    int lockDepth   = 0;
    uint64_t reads  = 0;
    uint64_t unlockedReads = 0;
    uint32_t autoAdvance   = 0; // cycles that pass with every DWT read (busy waiting)
    std::function<void()> pendingIsr;     // runs when the outermost lock is released
    std::function<void()> preemptingIsr;  // runs between an unlocked DWT read and the use of the value
    bool inIsr = false;
    std::vector<uint64_t> expectedAtRead; // exact ticks at each DWT read, popped by checkedTicks()
    bool recordReads = false;
    // Reference model of the OpenBSW algorithm, fed with the same reads
    uint64_t oldTicks = 0;
    uint32_t oldLast  = 0;
    uint32_t oldRatio = RATIO_RUN;
    // PLAT-67: a CYCCNT read while TRCENA is 0 is UNKNOWN; the board showed the frozen value (default), a test case
    // answers with another value instead
    bool unknownReadsFrozen = true;
    uint32_t unknownRead    = 0xDEADBEEFU;
} sim;

uint32_t const TRCENA    = 0x01000000U;
uint32_t const CYCCNTENA = 0x00000001U;

// PLAT-67: the DWT counts only with DEMCR.TRCENA and DWT_CTRL.CYCCNTENA set
bool dwtCounting()
{
    return ((::systemTimerModel::coreDebug.DEMCR.value & TRCENA) != 0U)
           && ((::systemTimerModel::dwt.CTRL.value & CYCCNTENA) != 0U);
}

uint64_t unitsPerCycle() { return sim.idle ? (UNIT_HZ / IDLE_HZ) : (UNIT_HZ / RUN_HZ); }

uint64_t expectedTicks() { return sim.units / UNITS_PER_TICK; }

void advance(uint64_t const cycles)
{
    if (dwtCounting())
    {
        sim.cycCnt = static_cast<uint32_t>(sim.cycCnt + cycles);
    }
    sim.units += cycles * unitsPerCycle();
}

void runIsr(std::function<void()> const& isr)
{
    sim.inIsr = true;
    isr();
    sim.inIsr = false;
}

} // namespace

namespace systemTimerModel
{
uint32_t readCycCnt()
{
    ++sim.reads;
    bool const traceEnabled = (::systemTimerModel::coreDebug.DEMCR.value & TRCENA) != 0U;
    uint32_t const value    = (traceEnabled || sim.unknownReadsFrozen) ? sim.cycCnt : sim.unknownRead;
    if (sim.recordReads)
    {
        sim.expectedAtRead.push_back(expectedTicks());
    }
    sim.oldTicks += (value - sim.oldLast) / sim.oldRatio;
    sim.oldLast = value;
    if (sim.lockDepth == 0)
    {
        ++sim.unlockedReads;
        if (sim.preemptingIsr && !sim.inIsr)
        {
            runIsr(sim.preemptingIsr);
        }
    }
    advance(sim.autoAdvance);
    return value;
}

void writeCycCnt(uint32_t const value) { sim.cycCnt = value; }

void lockInterrupts() { ++sim.lockDepth; }

void unlockInterrupts()
{
    --sim.lockDepth;
    if ((sim.lockDepth == 0) && sim.pendingIsr && !sim.inIsr)
    {
        std::function<void()> const isr = sim.pendingIsr;
        sim.pendingIsr                  = nullptr;
        runIsr(isr);
    }
}
} // namespace systemTimerModel

namespace
{
void startTimer()
{
    sim            = Simulation();
    ::systemTimerModel::coreDebug.DEMCR.value = 0U; // reset values: DWT off until initSystemTimer()
    ::systemTimerModel::dwt.CTRL.value        = 0U;
    sim.cycCnt     = 0x12345678U; // any value before init
    initSystemTimer();
    initSystemTimerHelper(false); // as StaticBsp
    sim.units    = 0U;
    sim.oldTicks = 0U;
    sim.oldLast  = sim.cycCnt;
    sim.oldRatio = RATIO_RUN;
}

void switchClock(bool const idle)
{
    sim.idle = idle;
    initSystemTimerHelper(idle);
    sim.oldRatio = idle ? RATIO_IDLE : RATIO_RUN;
}

double ticksToSeconds(int64_t const ticks) { return static_cast<double>(ticks) / static_cast<double>(TICK_HZ); }

double unitsToHours(uint64_t const units)
{
    return static_cast<double>(units) / static_cast<double>(UNIT_HZ) / 3600.0;
}

enum class OldCode
{
    DRIFTS,
    EXACT
};

// Calls getSystemTicks() in steps given by `step` until `seconds` of simulated time have passed.
void testCallRate(char const* const name, uint64_t const seconds, OldCode const oldCode,
                  std::function<uint64_t(std::mt19937_64&)> const& step)
{
    startTimer();
    std::mt19937_64 rng(4711U);
    uint64_t const endUnits = seconds * UNIT_HZ;
    uint64_t calls          = 0U;
    uint64_t deviations     = 0U;
    uint64_t maxDeviation   = 0U;
    bool monotonic          = true;
    uint64_t last           = 0U;
    while (sim.units < endUnits)
    {
        advance(step(rng));
        uint64_t const ticks    = getSystemTicks();
        uint64_t const expected = expectedTicks();
        ++calls;
        if (ticks != expected)
        {
            ++deviations;
            uint64_t const d = (ticks > expected) ? (ticks - expected) : (expected - ticks);
            maxDeviation     = (d > maxDeviation) ? d : maxDeviation;
        }
        monotonic = monotonic && (ticks >= last);
        last      = ticks;
    }
    int64_t const oldDrift = static_cast<int64_t>(expectedTicks()) - static_cast<int64_t>(sim.oldTicks);
    std::printf(
        "  %-34s %6.2f h %10" PRIu64 " calls: deviation %" PRIu64 " ticks, OpenBSW algorithm behind %.6f s (%.3f ms/h)\n",
        name, unitsToHours(sim.units), calls, maxDeviation, ticksToSeconds(oldDrift),
        ticksToSeconds(oldDrift) * 1000.0 / unitsToHours(sim.units));
    CHECK(deviations == 0U);
    CHECK(monotonic);
    CHECK(sim.unlockedReads == 0U);
    if ((oldCode == OldCode::DRIFTS) && (RATIO_RUN > 1U))
    {
        CHECK(oldDrift > 0); // the scenario is sensitive to the lost remainder
    }
    if (oldCode == OldCode::EXACT)
    {
        CHECK(oldDrift == 0);
    }
}

void testCallRates()
{
    std::printf("call rates (core %" PRIu64 " Hz, FIRC %" PRIu64 " Hz, tick %" PRIu64 " Hz)\n", RUN_HZ, IDLE_HZ,
                TICK_HZ);
    testCallRate("every 1 us (whole ticks)", 10U, OldCode::EXACT, [](std::mt19937_64&) { return CYCLES_PER_US; });
    testCallRate("every 1 us + 3 cycles", 10U, OldCode::DRIFTS, [](std::mt19937_64&) { return CYCLES_PER_US + 3U; });
    testCallRate("every 997 us + 0..999 cycles", 10U * 3600U, OldCode::DRIFTS,
                 [](std::mt19937_64& rng) { return (997U * CYCLES_PER_US) + (rng() % 1000U); });
    testCallRate("random 1 cycle .. 10 ms", 24U * 3600U, OldCode::DRIFTS,
                 [](std::mt19937_64& rng) { return 1U + (rng() % (10000U * CYCLES_PER_US)); });
    testCallRate("random 1 .. 20 cycles", 1U, OldCode::DRIFTS,
                 [](std::mt19937_64& rng) { return 1U + (rng() % 20U); });
}

void testWraparound()
{
    std::printf("wraparound of the 32 bit DWT counter\n");
    // Across the wrap in single cycles
    startTimer();
    advance(0xFFFFFFF0U);
    CHECK(getSystemTicks() == expectedTicks());
    bool exact = true;
    for (int i = 0; i < 40; ++i)
    {
        advance(1U);
        uint64_t const ticks = getSystemTicks();
        exact                = exact && (ticks == expectedTicks());
    }
    CHECK(exact);
    CHECK(sim.cycCnt < 0x100U);

    // Longest allowed gap (2^32 - ratio cycles) after calls that leave a remainder, over simulated hours
    startTimer();
    std::mt19937_64 rng(42U);
    exact          = true;
    bool monotonic = true;
    uint64_t last  = 0U;
    for (int i = 0; i < 1000; ++i)
    {
        advance(1U + (rng() % (3U * RATIO_RUN)));
        uint64_t ticks = getSystemTicks();
        exact          = exact && (ticks == expectedTicks());
        monotonic      = monotonic && (ticks >= last);
        advance(0x100000000ULL - RATIO_RUN);
        ticks     = getSystemTicks();
        exact     = exact && (ticks == expectedTicks());
        monotonic = monotonic && (ticks >= last);
        last      = ticks;
    }
    std::printf("  1000 gaps of 2^32 - %" PRIu32 " cycles (%.2f h): %s\n", RATIO_RUN, unitsToHours(sim.units),
                exact ? "exact" : "DEVIATION");
    CHECK(exact);
    CHECK(monotonic);

    // Documented limit: a gap of a whole DWT period or more is lost (called at least every OS tick in practice)
    startTimer();
    advance(0x100000000ULL + 5U);
    CHECK((expectedTicks() - getSystemTicks()) == (0x100000000ULL / RATIO_RUN));
}

void testClockSwitch()
{
    std::printf("clock switches run <-> idle (initSystemTimerHelper)\n");
    startTimer();
    std::mt19937_64 rng(1234U);
    bool exact         = true;
    bool monotonic     = true;
    bool frequencyOk   = true;
    uint64_t last      = 0U;
    uint64_t switches  = 0U;
    for (int i = 0; i < 2000000; ++i)
    {
        uint64_t const r = rng() % 10U;
        advance(1U + (rng() % 3000U));
        if (r < 2U)
        {
            switchClock(!sim.idle);
            ++switches;
        }
        else if (r < 3U)
        {
            switchClock(sim.idle); // same clock again, as StaticBsp at start
        }
        uint64_t const ticks = getSystemTicks();
        exact                = exact && (ticks == expectedTicks());
        monotonic            = monotonic && (ticks >= last);
        last                 = ticks;
        frequencyOk          = frequencyOk && (getFastTicksPerSecond() == (sim.idle ? IDLE_HZ : RUN_HZ));
    }
    CHECK(exact);
    CHECK(monotonic);
    CHECK(frequencyOk);
    int64_t const oldDrift = static_cast<int64_t>(expectedTicks()) - static_cast<int64_t>(sim.oldTicks);
    std::printf("  random, %" PRIu64 " switches: %s, OpenBSW algorithm behind %" PRId64 " ticks\n", switches,
                exact ? "exact" : "DEVIATION", oldDrift);

    // Idle pattern: 997 us + jitter running, 3 us idle, for one simulated hour
    startTimer();
    exact     = true;
    monotonic = true;
    last      = 0U;
    while (sim.units < (3600U * UNIT_HZ))
    {
        advance((997U * CYCLES_PER_US) + (rng() % 1000U));
        switchClock(true);
        advance((3U * IDLE_HZ / 1000000U) + (rng() % 7U));
        switchClock(false);
        uint64_t const ticks = getSystemTicks();
        exact                = exact && (ticks == expectedTicks());
        monotonic            = monotonic && (ticks >= last);
        last                 = ticks;
    }
    CHECK(exact);
    CHECK(monotonic);
    std::printf("  idle 3 us per ms for %.2f h: %s\n", unitsToHours(sim.units), exact ? "exact" : "DEVIATION");
}

// Task and interrupt calls: every value must be exact at its DWT read, reads only with interrupts locked.
uint64_t checkedTicks(bool& exact)
{
    uint64_t const ticks = getSystemTicks();
    if (sim.expectedAtRead.empty())
    {
        exact = false;
        return ticks;
    }
    exact = exact && (ticks == sim.expectedAtRead.back());
    sim.expectedAtRead.pop_back();
    return ticks;
}

void testTaskAndIsr()
{
    std::printf("task and interrupt calls\n");
    startTimer();
    std::mt19937_64 rng(99U);
    bool exact        = true;
    uint64_t isrCalls = 0U;
    auto const isr    = [&]() {
        advance(rng() % 50U);
        (void)checkedTicks(exact);
        ++isrCalls;
    };
    sim.recordReads   = true;
    sim.preemptingIsr = isr; // only reached if the timer reads the DWT counter without lock
    for (int i = 0; i < 1000000; ++i)
    {
        advance(1U + (rng() % 10000U));
        if ((rng() % 3U) == 0U)
        {
            sim.pendingIsr = isr; // interrupt pending during the locked section
        }
        (void)checkedTicks(exact);
        if ((rng() % 50U) == 0U)
        {
            switchClock(!sim.idle);
            sim.expectedAtRead.clear();
        }
    }
    std::printf("  1000000 task calls, %" PRIu64 " interrupt calls: %s, unlocked reads %" PRIu64 "\n", isrCalls,
                exact ? "exact" : "DEVIATION", sim.unlockedReads);
    CHECK(exact);
    CHECK(sim.unlockedReads == 0U);
    CHECK(isrCalls > 300000U);
}

void testConversionsAndInit()
{
    std::printf("conversions, init, busy wait\n");
    sim.cycCnt = 0x55AA55AAU;
    startTimer();
    CHECK(sim.cycCnt == 0U); // initSystemTimer restarts the counter
    CHECK((::systemTimerModel::coreDebug.DEMCR.value & 0x01000000U) != 0U);
    CHECK((::systemTimerModel::dwt.CTRL.value & 0x1U) != 0U);
    CHECK(getFastTicksPerSecond() == RUN_HZ);
    for (int s = 0; s < 3600; ++s)
    {
        advance(RUN_HZ);
        (void)getSystemTicks();
    }
    CHECK(getSystemTicks() == (3600U * TICK_HZ));
    CHECK(getSystemTimeMs64Bit() == 3600000U);
    CHECK(getSystemTimeMs32Bit() == 3600000U);
    CHECK(getSystemTimeUs64Bit() == 3600000000ULL);
    CHECK(getSystemTimeUs32Bit() == 3600000000U);
    CHECK(getSystemTimeNs() == 3600000000000ULL);
    CHECK(getSystemTicks32Bit() == static_cast<uint32_t>(3600U * TICK_HZ));
    CHECK(systemTicksToTimeUs(getSystemTicks()) == 3600000000ULL);
    CHECK(systemTicksToTimeNs(TICK_HZ) == 1000000000ULL);
    CHECK(getFastTicks() == sim.cycCnt);

    // sysDelayUs busy waits on the timer: 7 cycles pass with every read
    uint64_t const start = sim.units;
    sim.autoAdvance      = 7U;
    sysDelayUs(1500U);
    sim.autoAdvance            = 0U;
    uint64_t const elapsedUs = (sim.units - start) * 1000000U / UNIT_HZ;
    CHECK(elapsedUs >= 1499U);
    CHECK(elapsedUs <= 1501U);
}

// PLAT-67: the J-Link clears DEMCR.TRCENA when it closes its session; the DWT counter stands still until the next
// system time read switches it on again. Time until then is lost once, the system time never jumps forward.
void debuggerDisconnect(bool const alsoCycCntEna, uint32_t const cycCntAfter, bool const writeCycCnt)
{
    ::systemTimerModel::coreDebug.DEMCR.value &= ~TRCENA;
    if (alsoCycCntEna)
    {
        ::systemTimerModel::dwt.CTRL.value &= ~CYCCNTENA;
    }
    if (writeCycCnt)
    {
        sim.cycCnt = cycCntAfter; // a debugger or the UNKNOWN state left another value in the counter
    }
}

struct DisconnectCase
{
    char const* name;
    bool alsoCycCntEna;
    bool writeCycCnt;
    uint32_t cycCntAfter;
    bool unknownReadsFrozen;
};

void testDebuggerDisconnect()
{
    std::printf("debugger disconnect clears DEMCR.TRCENA (PLAT-67)\n");
    DisconnectCase const cases[] = {
        {"TRCENA cleared, counter kept", false, false, 0U, true},
        {"TRCENA and CYCCNTENA cleared", true, false, 0U, true},
        {"counter left at 0", false, true, 0U, true},
        {"counter left at 0xFFFFFFF0", true, true, 0xFFFFFFF0U, true},
        {"reads while off not frozen", false, false, 0U, false},
    };
    for (DisconnectCase const& c : cases)
    {
        startTimer();
        sim.unknownReadsFrozen = c.unknownReadsFrozen;
        CHECK(getSystemTimerCycleCounterRestarts() == 0U);
        // 1 s running with a call every 997 us + jitter
        std::mt19937_64 rng(7U);
        uint64_t last = 0U;
        while (sim.units < UNIT_HZ)
        {
            advance((997U * CYCLES_PER_US) + (rng() % 1000U));
            last = getSystemTicks();
        }
        CHECK(last == expectedTicks());
        uint64_t const unitsAtLastRead = sim.units;
        // Disconnect 300 us after the last read, then 700 us without any read (counter stands still)
        advance(300U * CYCLES_PER_US);
        debuggerDisconnect(c.alsoCycCntEna, c.cycCntAfter, c.writeCycCnt);
        uint32_t const frozen = sim.cycCnt;
        advance(700U * CYCLES_PER_US);
        CHECK(sim.cycCnt == frozen);
        uint64_t const lostTicks = (sim.units - unitsAtLastRead) / UNITS_PER_TICK;
        // The first read restarts the counter: no jump forward, the time lost once is the gap since the last read
        uint64_t const ticks = getSystemTicks();
        CHECK(dwtCounting());
        CHECK(getSystemTimerCycleCounterRestarts() == 1U);
        CHECK(ticks == last);
        uint64_t const offset = expectedTicks() - ticks;
        CHECK((offset >= lostTicks) && (offset <= (lostTicks + 1U)));
        // Afterwards the time runs exactly again (offset constant up to the tick phase), monotonic, no restart
        bool exact     = true;
        bool monotonic = true;
        last           = ticks;
        for (int i = 0; i < 100000; ++i)
        {
            advance(1U + (rng() % (2000U * CYCLES_PER_US)));
            uint64_t const t = getSystemTicks();
            uint64_t const d = expectedTicks() - t;
            exact            = exact && (d >= lostTicks) && (d <= (lostTicks + 1U));
            monotonic        = monotonic && (t >= last);
            last             = t;
        }
        CHECK(exact);
        CHECK(monotonic);
        CHECK(getSystemTimerCycleCounterRestarts() == 1U);
        CHECK(sim.unlockedReads == 0U);
        std::printf("  %-30s lost %.3f ms once (gap since the last read), then %s\n", c.name,
                    ticksToSeconds(static_cast<int64_t>(offset)) * 1000.0, exact ? "exact" : "DEVIATION");
    }

    // A clock switch (idle entry) as the first read after the disconnect restarts the counter as well
    startTimer();
    advance(5000U * CYCLES_PER_US);
    uint64_t const before = getSystemTicks();
    debuggerDisconnect(true, 0U, false);
    advance(100U * CYCLES_PER_US);
    switchClock(true);
    CHECK(dwtCounting());
    CHECK(getSystemTimerCycleCounterRestarts() == 1U);
    advance(IDLE_HZ / 1000U); // 1 ms at FIRC
    switchClock(false);
    uint64_t const after = getSystemTicks();
    CHECK(after >= before);
    CHECK((expectedTicks() - after) <= ((100U * TICK_HZ / 1000000U) + 2U));

    // Scheduler view: a task due every 10 ms by the system time (async scheduleAtFixedRate), 1 ms OS wake-ups.
    // Without the restart the time stands still after the disconnect and the task (the watchdog trigger) never
    // runs again; with it, every disconnect delays the task by at most the gap to the previous read.
    startTimer();
    uint32_t due        = 10000U;
    uint32_t runs       = 0U;
    uint32_t maxLateUs  = 0U;
    uint64_t const step = 1000U * CYCLES_PER_US;
    for (uint32_t ms = 1U; ms <= 10000U; ++ms)
    {
        advance(step);
        if ((ms % 1000U) == 500U)
        {
            debuggerDisconnect(false, 0U, false); // ten J-Link sessions closed at 0.5 s, 1.5 s, ...
        }
        uint32_t const now = getSystemTimeUs32Bit();
        if (static_cast<int32_t>(now - due) >= 0)
        {
            ++runs;
            due += 10000U;
            uint32_t const realUs = static_cast<uint32_t>(sim.units * 1000000U / UNIT_HZ);
            uint32_t const late   = realUs - (runs * 10000U);
            maxLateUs             = (late > maxLateUs) ? late : maxLateUs;
        }
    }
    std::printf("  10 s, 10 disconnects: 10 ms task ran %" PRIu32 " of 1000 times, restarts %" PRIu32
                ", late by at most %" PRIu32 " us (sum of the lost gaps)\n",
                runs, getSystemTimerCycleCounterRestarts(), maxLateUs);
    CHECK(getSystemTimerCycleCounterRestarts() == 10U);
    CHECK(runs >= 999U);
    CHECK(maxLateUs <= 11000U);

    // initSystemTimer() starts counting the restarts from zero again
    startTimer();
    CHECK(getSystemTimerCycleCounterRestarts() == 0U);
}

} // namespace

int main()
{
    testCallRates();
    testWraparound();
    testClockSwitch();
    testTaskAndIsr();
    testConversionsAndInit();
    testDebuggerDisconnect();
    std::printf("%s: %d checks, %d failures\n", (failures == 0) ? "PASS" : "FAIL", checks, failures);
    return (failures == 0) ? 0 : 1;
}
