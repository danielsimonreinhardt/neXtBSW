/* SYS-95 (findings DFA-1 and DFA-6 of doc/safety/dfa.md): host test of the hardware independent progress checks
 * (safeWatchdog/ProgressChecks.cpp) with own test profiles - not the demo's values.
 *
 * What it shows:
 * - ProgressDeadline: the first sample is the reference; a counter that changes in every cycle, or once within the
 *   deadline, never reports; one that stands still for deadline + 1 cycles reports exactly once, at that cycle; a
 *   change re-arms it; a wrap-around of the counter is a change; deadline 0 switches the check off; restart().
 * - TimeBaseCheck: bounds for a 1 kHz reference with the LPO range of the S32K1 data sheet (882 ... 1086 per mille)
 *   and for a 32 kHz 32-bit reference of another family; a healthy system time at the slowest and at the fastest
 *   reference clock never reports, over the wrap-around of both counters; a stopped system time is SYSTEM_TIME_SLOW
 *   with value 0 at the end of the first full window; a system time that runs at half or double speed is reported
 *   SLOW resp. FAST; a one-off lag of up to the slack is tolerated, one beyond it is not; context words.
 *
 * Built-in faults (run by hand for SYS-95): the change test dropped from ProgressDeadline (`progress != _last`), the
 * latch of the report removed, the window evaluated in every cycle, the reference difference taken without its mask -
 * each makes this test red.
 *
 * Build and run (zig c++, no hardware):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -Iecu/safety/safeWatchdog/include \
 *       ecu/safety/safeWatchdog/test/host/ProgressChecksHostTest.cpp \
 *       ecu/safety/safeWatchdog/src/safeWatchdog/ProgressChecks.cpp \
 *       -o build/host/progresschecks.exe
 */

#include <safeWatchdog/ProgressChecks.h>

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <initializer_list>

namespace
{
using ::safety::ProgressDeadline;
using ::safety::TimeBaseCheck;
using ::safety::TimeBaseLimits;

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

// Profile A: 16-bit reference at 1 kHz (the shape of the S32K1 LPTMR on LPO1K), LPO range of the data sheet, window 7
// cycles, slack 12 ms (period + lateness of the demo)
constexpr TimeBaseLimits A{1000U, 882U, 1086U, 7U, 0xFFFFU, 12U};
// Profile B: 32-bit reference at 32768 Hz with +-2 %, window 4 cycles, no slack (another family)
constexpr TimeBaseLimits B{32768U, 980U, 1020U, 4U, 0xFFFFFFFFU, 0U};

uint32_t kind(uint32_t const context) { return static_cast<uint32_t>(TimeBaseCheck::kindOf(context)); }

void testDeadlineFirstSampleIsReference()
{
    ProgressDeadline d;
    CHECK(!d.sample(12345U, 3U));
    CHECK(d.cyclesSinceProgress() == 0U);
    d.restart();
    CHECK(!d.sample(7U, 1U));
    CHECK(!d.sample(7U, 1U)); // one cycle without change: within the deadline
    CHECK(d.sample(7U, 1U));  // two: beyond it
}

void testDeadlineHealthyNeverReports()
{
    ProgressDeadline d;
    uint32_t passes = 0U;
    bool reported   = false;
    for (int cycle = 0; cycle < 10000; ++cycle)
    {
        if ((cycle % 50) == 0)
        {
            ++passes; // a pass every 50 cycles, deadline 50: never beyond it
        }
        reported = reported || d.sample(passes, 50U);
    }
    CHECK(!reported);
}

void testDeadlineStandstillReportsOnceAtTheDeadline()
{
    ProgressDeadline d;
    CHECK(!d.sample(3U, 5U));
    for (uint32_t i = 1U; i <= 5U; ++i)
    {
        CHECK(!d.sample(3U, 5U));
    }
    CHECK(d.cyclesSinceProgress() == 5U);
    CHECK(d.sample(3U, 5U)); // the sixth cycle without a change
    for (int i = 0; i < 100; ++i)
    {
        CHECK(!d.sample(3U, 5U)); // reported once
    }
    // a change re-arms the check
    CHECK(!d.sample(4U, 5U));
    for (uint32_t i = 1U; i <= 5U; ++i)
    {
        CHECK(!d.sample(4U, 5U));
    }
    CHECK(d.sample(4U, 5U));
}

void testDeadlineWrapIsProgress()
{
    ProgressDeadline d;
    CHECK(!d.sample(0xFFFFFFFFU, 2U));
    CHECK(!d.sample(0xFFFFFFFFU, 2U));
    CHECK(!d.sample(0U, 2U));
    CHECK(d.cyclesSinceProgress() == 0U);
}

void testDeadlineZeroIsOff()
{
    ProgressDeadline d;
    bool reported = false;
    for (int i = 0; i < 1000; ++i)
    {
        reported = reported || d.sample(1U, 0U);
    }
    CHECK(!reported);
}

void testBounds()
{
    // 70 ticks of a 1 kHz reference: at 1.086 kHz 69 ticks are 63.5 ms -> floor 63, minus 2 and the slack 12 = 49
    CHECK(TimeBaseCheck::lowerBoundMs(70U, A) == 49U);
    // at 0.882 kHz 71 ticks are 80.5 ms -> ceil 81, plus 2 = 83
    CHECK(TimeBaseCheck::upperBoundMs(70U, A) == 83U);
    CHECK(TimeBaseCheck::lowerBoundMs(0U, A) == 0U);
    CHECK(TimeBaseCheck::lowerBoundMs(1U, A) == 0U);
    // profile B: 4 x 10 ms at 32768 Hz = 1310 ticks; 1309 ticks at 33423.36 Hz are 39.16 ms -> 39 - 2 = 37
    CHECK(TimeBaseCheck::lowerBoundMs(1310U, B) == 37U);
    // 1311 ticks at 32112.64 Hz are 40.83 ms -> 41 + 2 = 43
    CHECK(TimeBaseCheck::upperBoundMs(1310U, B) == 43U);
}

/** Runs `cycles` safety cycles of `periodMs` real time; the reference runs at `refScale` of nominal, the system time
 * at `timeScale` of real time, both from an arbitrary start value. Returns the first finding (0 = none). */
uint32_t run(TimeBaseLimits const& limits, int const cycles, double const periodMs, double const refScale,
             double const timeScale, double const refStart, double const timeStart, int* findings = nullptr)
{
    TimeBaseCheck check;
    uint32_t first = 0U;
    int count      = 0;
    for (int i = 0; i < cycles; ++i)
    {
        double const realMs   = periodMs * i + 0.37 * (i % 3); // a little jitter of the cycle start
        double const refTicks = refStart + (realMs / 1000.0) * limits.referenceHz * refScale;
        double const timeMs   = timeStart + realMs * timeScale;
        uint32_t const ctx    = check.sample(
            static_cast<uint32_t>(std::fmod(std::floor(timeMs), 4294967296.0)),
            static_cast<uint32_t>(std::fmod(std::floor(refTicks), 4294967296.0)),
            limits);
        if (ctx != 0U)
        {
            ++count;
            if (first == 0U)
            {
                first = ctx;
            }
        }
    }
    if (findings != nullptr)
    {
        *findings = count;
    }
    return first;
}

void testHealthyNeverReports()
{
    for (double const scale : {0.8828, 0.95, 1.0, 1.05, 1.0859})
    {
        // 16-bit reference across its wrap-around, system time across the 32-bit wrap of the ms
        CHECK(run(A, 20000, 10.0, scale, 1.0, 65000.0, 4294967000.0) == 0U);
        CHECK(run(A, 2000, 5.0, scale, 1.0, 12.0, 0.0) == 0U);
    }
    for (double const scale : {0.9801, 1.0, 1.0199})
    {
        CHECK(run(B, 20000, 10.0, scale, 1.0, 4294960000.0, 77.0) == 0U);
    }
}

void testStoppedSystemTime()
{
    TimeBaseCheck check;
    uint32_t ref = 100U;
    CHECK(check.sample(5000U, ref, A) == 0U); // reference sample
    for (int i = 1; i < 7; ++i)
    {
        ref += 10U;
        CHECK(check.sample(5000U, ref, A) == 0U);
    }
    ref += 10U;
    uint32_t const ctx = check.sample(5000U, ref, A); // end of the first window
    CHECK(kind(ctx) == TimeBaseCheck::SYSTEM_TIME_SLOW);
    CHECK(TimeBaseCheck::valueOf(ctx) == 0U);
    // and the profile of another family
    CHECK(kind(run(B, 10, 10.0, 1.0, 0.0, 0.0, 1234.0)) == TimeBaseCheck::SYSTEM_TIME_SLOW);
}

void testWrongSpeed()
{
    int findings = 0;
    CHECK(kind(run(A, 70, 10.0, 1.0, 0.5, 0.0, 0.0, &findings)) == TimeBaseCheck::SYSTEM_TIME_SLOW);
    CHECK(findings == 9); // every window of the run (69 intervals / 7)
    CHECK(kind(run(A, 70, 10.0, 1.0, 2.0, 0.0, 0.0)) == TimeBaseCheck::SYSTEM_TIME_FAST);
    // a system time 20 % fast against the slowest reference clock of the data sheet is still seen
    CHECK(kind(run(A, 70, 10.0, 0.8828, 1.2 * 1.0, 0.0, 0.0)) == TimeBaseCheck::SYSTEM_TIME_FAST);
    CHECK(kind(run(B, 40, 10.0, 1.0, 0.9, 0.0, 0.0)) == TimeBaseCheck::SYSTEM_TIME_SLOW);
}

void testOneOffLag()
{
    // A one-off lag of the system time (the debugger switched the DWT off, PLAT-67, and the restart lost the time it
    // stood still): 20 ms lost in a window of 70 ms pass at every LPO of the data sheet, 30 ms are reported
    TimeBaseCheck check;
    uint32_t ref = 0U;
    CHECK(check.sample(0U, ref, A) == 0U);
    uint32_t time = 0U;
    uint32_t ctx  = 0U;
    for (int i = 1; i <= 7; ++i)
    {
        ref += 10U;
        time += (i <= 2) ? 0U : 10U; // 20 ms lost: 50 ms of 70 - still above the bound 49 at the fastest LPO
        ctx = check.sample(time, ref, A);
    }
    CHECK(ctx == 0U);
    ctx = 0U;
    TimeBaseCheck check2;
    ref  = 0U;
    time = 0U;
    CHECK(check2.sample(0U, ref, A) == 0U);
    for (int i = 1; i <= 7; ++i)
    {
        ref += 10U;
        time += (i <= 3) ? 0U : 10U; // 30 ms lost: 40 ms < 49
        ctx = check2.sample(time, ref, A);
    }
    CHECK(kind(ctx) == TimeBaseCheck::SYSTEM_TIME_SLOW);
    CHECK(TimeBaseCheck::valueOf(ctx) == 40U);
}

void testContextWord()
{
    CHECK(TimeBaseCheck::context(TimeBaseCheck::SYSTEM_TIME_FAST, 0x12345678U) == 0x05FFFFFFU);
    CHECK(TimeBaseCheck::context(TimeBaseCheck::SYSTEM_TIME_SLOW, 0U) == 0x04000000U);
    CHECK(TimeBaseCheck::kindOf(0x05000010U) == TimeBaseCheck::SYSTEM_TIME_FAST);
    CHECK(TimeBaseCheck::valueOf(0x05000010U) == 16U);
}

void testRestart()
{
    TimeBaseCheck check;
    CHECK(check.sample(0U, 0U, A) == 0U);
    for (int i = 0; i < 3; ++i)
    {
        CHECK(check.sample(0U, 10U * static_cast<uint32_t>(i + 1), A) == 0U);
    }
    check.restart();
    // after restart the first sample is the reference again and the window starts empty
    CHECK(check.sample(1000U, 500U, A) == 0U);
    uint32_t ctx = 0U;
    for (uint32_t i = 1U; i <= 7U; ++i)
    {
        ctx = check.sample(1000U + 10U * i, 500U + 10U * i, A);
    }
    CHECK(ctx == 0U);
}

} // namespace

int main()
{
    struct
    {
        char const* name;
        void (*fn)();
    } const tests[] = {
        {"testDeadlineFirstSampleIsReference", testDeadlineFirstSampleIsReference},
        {"testDeadlineHealthyNeverReports", testDeadlineHealthyNeverReports},
        {"testDeadlineStandstillReportsOnceAtTheDeadline", testDeadlineStandstillReportsOnceAtTheDeadline},
        {"testDeadlineWrapIsProgress", testDeadlineWrapIsProgress},
        {"testDeadlineZeroIsOff", testDeadlineZeroIsOff},
        {"testBounds", testBounds},
        {"testHealthyNeverReports", testHealthyNeverReports},
        {"testStoppedSystemTime", testStoppedSystemTime},
        {"testWrongSpeed", testWrongSpeed},
        {"testOneOffLag", testOneOffLag},
        {"testContextWord", testContextWord},
        {"testRestart", testRestart},
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
