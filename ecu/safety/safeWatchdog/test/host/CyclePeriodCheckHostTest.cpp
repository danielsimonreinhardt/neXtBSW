/* SYS-40 (finding F9 of doc/safety/fmea.md, SM-04): host test of the hardware independent period check
 * (safeWatchdog/CyclePeriodCheck.cpp) with its own test profiles - not the demo's values: a 32-bit counter with a
 * 5 ms period and a window of 4, and a 16-bit counter, so the check is shown for other families and periods too.
 *
 * What it shows:
 * - the first sample after construction and after restart() is the reference, whatever the counter shows;
 * - an interval up to maxIntervalTicks passes, one tick more is INTERVAL_TOO_LONG with the interval as value;
 * - the difference is taken in the counter's width: across the wrap-around of a 16- and a 32-bit counter;
 * - a window of windowCycles intervals below minWindowTicks is WINDOW_TOO_SHORT with the sum as value, exactly at the
 *   last interval of the window, and the next window starts empty; a single short interval (catch-up) is no
 *   violation, and a long interval counts into its window (the catch-up burst after it is no violation either);
 * - a stopped counter is WINDOW_TOO_SHORT with value 0;
 * - context(): kind in bits 31..24, value saturated to 24 bits; both violations in one sample report the long
 *   interval.
 *
 * Built-in faults (run by hand for SYS-40): window check dropped, long interval not counted into the window,
 * difference without the counter mask, first sample compared against 0, >= instead of > - each makes this test red.
 *
 * Build and run (zig c++, no hardware):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -Iecu/safety/safeWatchdog/include \
 *       ecu/safety/safeWatchdog/test/host/CyclePeriodCheckHostTest.cpp \
 *       ecu/safety/safeWatchdog/src/safeWatchdog/CyclePeriodCheck.cpp \
 *       -o build/host/cycleperiodcheck.exe
 */

#include <safeWatchdog/CyclePeriodCheck.h>

#include <cstdint>
#include <cstdio>
#include <initializer_list>

namespace
{
using ::safety::CyclePeriodCheck;
using ::safety::CyclePeriodLimits;

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

// Test profile A: 32-bit counter at 1 MHz, period 5 ms, window 4 cycles (another family, another period)
constexpr CyclePeriodLimits A{6000U, 19000U, 4U, 0xFFFFFFFFU};
// Test profile B: 16-bit counter, 10 ticks per period, window 8 (the shape of the S32K1 port)
constexpr CyclePeriodLimits B{14U, 68U, 8U, 0xFFFFU};

uint32_t kind(uint32_t const context) { return static_cast<uint32_t>(CyclePeriodCheck::kindOf(context)); }

void testFirstSampleIsTheReference()
{
    CyclePeriodCheck check;
    CHECK(check.sample(0xDEADBEEFU, A) == 0U); // no interval yet
    CHECK(check.sample(0xDEADBEEFU + 5000U, A) == 0U);
    check.restart();
    CHECK(check.sample(12345U, A) == 0U); // after restart() the reference again, not 12345 - (0xDEADBEEF + 5000)
    CHECK(check.sample(12345U + 5000U, A) == 0U);
}

void testIntervalBound()
{
    CyclePeriodCheck check;
    uint32_t t = 100U;
    (void)check.sample(t, A);
    t += A.maxIntervalTicks;
    CHECK(check.sample(t, A) == 0U); // exactly the bound passes
    t += A.maxIntervalTicks + 1U;
    uint32_t const c = check.sample(t, A);
    CHECK(kind(c) == CyclePeriodCheck::INTERVAL_TOO_LONG);
    CHECK(CyclePeriodCheck::valueOf(c) == A.maxIntervalTicks + 1U);
}

void testWrapAround()
{
    for (CyclePeriodLimits const* limits : {&A, &B})
    {
        CyclePeriodCheck check;
        uint32_t const start = limits->counterMask - 3U; // wraps within the next interval
        (void)check.sample(start, *limits);
        uint32_t const period = (limits == &A) ? 5000U : 10U;
        CHECK(check.sample(start + period, *limits) == 0U); // unmasked value: the port may hand in more bits
        CHECK(check.sample((start + (2U * period)) & limits->counterMask, *limits) == 0U);
    }
}

void testWindow()
{
    // Profile B: 8 intervals of 8 ticks = 64 < 68 - reported at the 8th interval, not before, with the sum
    CyclePeriodCheck check;
    uint32_t t = 0U;
    (void)check.sample(t, B);
    for (uint32_t i = 1U; i < B.windowCycles; ++i)
    {
        t += 8U;
        CHECK(check.sample(t, B) == 0U);
    }
    t += 8U;
    uint32_t const c = check.sample(t, B);
    CHECK(kind(c) == CyclePeriodCheck::WINDOW_TOO_SHORT);
    CHECK(CyclePeriodCheck::valueOf(c) == 64U);
    CHECK(check.windowCount() == 0U); // the next window starts empty
    // healthy window after it: 8 x 10 = 80 >= 68
    for (uint32_t i = 0U; i < B.windowCycles; ++i)
    {
        t += 10U;
        CHECK(check.sample(t, B) == 0U);
    }
}

void testCatchUpIsNoViolation()
{
    // Fixed rate: a late start (14 ticks, the bound) is followed by a short interval (6) - both single intervals of
    // a healthy window.
    CyclePeriodCheck check;
    uint32_t t = 0U;
    (void)check.sample(t, B);
    uint32_t const intervals[] = {10U, 14U, 6U, 10U, 10U, 14U, 6U, 10U, 10U, 10U, 10U, 10U, 10U, 10U, 10U, 10U};
    for (uint32_t const i : intervals)
    {
        t += i;
        CHECK(check.sample(t, B) == 0U);
    }
}

void testLongIntervalCountsIntoItsWindow()
{
    // A long interval (reported) followed by the burst of the catch-up (four intervals of 1 tick): the window
    // holds the long interval as well, so the burst is not reported a second time as "too fast".
    CyclePeriodCheck check;
    uint32_t t = 0U;
    (void)check.sample(t, B);
    uint32_t const intervals[] = {10U, 10U, 50U, 1U, 1U, 1U, 1U, 10U};
    uint32_t reports           = 0U;
    for (uint32_t const i : intervals)
    {
        t += i;
        uint32_t const c = check.sample(t, B);
        if (c != 0U)
        {
            ++reports;
            CHECK(kind(c) == CyclePeriodCheck::INTERVAL_TOO_LONG);
        }
    }
    CHECK(reports == 1U);
}

void testStoppedCounter()
{
    CyclePeriodCheck check;
    uint32_t c = 0U;
    for (uint32_t i = 0U; i <= A.windowCycles; ++i)
    {
        c = check.sample(777U, A);
    }
    CHECK(kind(c) == CyclePeriodCheck::WINDOW_TOO_SHORT);
    CHECK(CyclePeriodCheck::valueOf(c) == 0U);
}

void testBothInOneSampleReportsTheLongInterval()
{
    // Profile A: window of 4; three intervals of 1 tick, then one of 6001 - window sum 6004 < 19000 and interval
    // above 6000 in the same sample: the long interval is reported (it is the more direct statement).
    CyclePeriodCheck check;
    uint32_t t = 0U;
    (void)check.sample(t, A);
    for (int i = 0; i < 3; ++i)
    {
        t += 1U;
        (void)check.sample(t, A);
    }
    t += 6001U;
    uint32_t const c = check.sample(t, A);
    CHECK(kind(c) == CyclePeriodCheck::INTERVAL_TOO_LONG);
    CHECK(CyclePeriodCheck::valueOf(c) == 6001U);
}

void testContextWord()
{
    CHECK(CyclePeriodCheck::context(CyclePeriodCheck::TIMER_CONFIGURATION, 0x12U) == 0x03000012U);
    CHECK(CyclePeriodCheck::context(CyclePeriodCheck::INTERVAL_TOO_LONG, 0x7FFFFFFFU) == 0x01FFFFFFU); // saturated
    CHECK(CyclePeriodCheck::kindOf(0x02000044U) == CyclePeriodCheck::WINDOW_TOO_SHORT);
    CHECK(CyclePeriodCheck::valueOf(0x02000044U) == 0x44U);
}
} // namespace

int main()
{
    testFirstSampleIsTheReference();
    testIntervalBound();
    testWrapAround();
    testWindow();
    testCatchUpIsNoViolation();
    testLongIntervalCountsIntoItsWindow();
    testStoppedCounter();
    testBothInOneSampleReportsTheLongInterval();
    testContextWord();
    std::printf("%d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
