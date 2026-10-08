/* SYS-95 (finding DFA-2 of doc/safety/dfa.md, DF-05): host test of the deadman progress check
 * (safeSupervisionCheck/LpitDeadmanProgress.cpp, the real code) against an LPIT model whose counter counts one tick
 * every n reads (progressStubs/mcu/mcu.h).
 *
 * What it shows:
 * - a running counter is MOVING for every ratio of core to LPIT clock with the budget lpitProgressPolls() derives -
 *   test profiles 80 MHz / 8 MHz (the eval board's clock plan), 112 MHz / 1 MHz and 48 MHz / 48 MHz - and also
 *   right at the end of the budget (the tick falls on the last poll);
 * - a stopped LPIT clock is STOPPED after exactly the budget of polls, with the channel enabled;
 * - a disabled channel (T_EN 0, between CLRTEN and SETTEN of a retrigger) is NOT_RUNNING without polling the counter,
 *   and a channel that the deadman's interrupt stops during the polls is NOT_RUNNING, not STOPPED;
 * - a channel number outside the register block is NOT_RUNNING without any register access;
 * - a budget of 0 still polls once; lpitProgressPolls() of the test profiles.
 *
 * Built-in faults (run by hand for SYS-95): the T_EN test before the polls removed, the T_EN test after the polls
 * removed - each makes this test red.
 *
 * Build and run (zig c++, no hardware):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -Iplatforms/s32k1xx/port/safety/safeSupervisionCheck/test/host/progressStubs \
 *       -Iplatforms/s32k1xx/port/safety/safeSupervisionCheck/include \
 *       platforms/s32k1xx/port/safety/safeSupervisionCheck/test/host/LpitDeadmanProgressHostTest.cpp \
 *       platforms/s32k1xx/port/safety/safeSupervisionCheck/src/safeSupervisionCheck/LpitDeadmanProgress.cpp \
 *       -o build/host/lpitdeadmanprogress.exe
 */

#include <safeSupervisionCheck/LpitDeadmanProgress.h>

#include <cstdio>

LpitType lpitRegisters{};

namespace
{
namespace sv = ::safety::supervision;
using sv::LpitCounterProgress;

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

constexpr uint32_t CHANNEL = 1U;
constexpr sv::LpitDeadmanConfiguration EXPECTED{CHANNEL, 79999U, LPIT0_Ch1_IRQn, 4U};

/** Channel 1 enabled and counting, `pollsPerTick` reads per LPIT tick. */
LpitCounterModel& setUp(uint32_t const pollsPerTick, bool const running = true)
{
    lpitRegisters             = LpitType{};
    auto& tmr                 = lpitRegisters.TMR[CHANNEL];
    tmr.TCTRL                 = LPIT_TMR_TCTRL_T_EN_MASK;
    tmr.TVAL                  = EXPECTED.timerValue;
    tmr.CVAL                  = LpitCounterModel{};
    tmr.CVAL.value            = 41234U;
    tmr.CVAL.pollsPerTick     = pollsPerTick;
    tmr.CVAL.clockRunning     = running;
    tmr.CVAL.tctrl            = &tmr.TCTRL;
    return tmr.CVAL;
}

void testBudget()
{
    CHECK(sv::lpitProgressPolls(80000000U, 8000000U) == 22U);
    CHECK(sv::lpitProgressPolls(112000000U, 1000000U) == 226U);
    CHECK(sv::lpitProgressPolls(48000000U, 48000000U) == 4U);
    CHECK(sv::lpitProgressPolls(80000000U, 3000000U) == 56U); // 26.67 rounded up to 27
    CHECK(sv::lpitProgressPolls(80000000U, 0U) == 0U);
}

void testRunningCounterMoves()
{
    struct
    {
        uint32_t core;
        uint32_t lpit;
    } const profiles[] = {{80000000U, 8000000U}, {112000000U, 1000000U}, {48000000U, 48000000U}};
    for (auto const& p : profiles)
    {
        uint32_t const budget = sv::lpitProgressPolls(p.core, p.lpit);
        // the slowest healthy case: one poll per core cycle, so one LPIT tick takes core / lpit polls
        uint32_t const ratio = (p.core + p.lpit - 1U) / p.lpit;
        auto& model          = setUp(ratio);
        CHECK(sv::checkLpitDeadmanProgress(EXPECTED, budget) == LpitCounterProgress::MOVING);
        CHECK(model.reads <= (ratio + 1U)); // ends with the first change, within one tick
    }
}

void testTickOnTheLastPoll()
{
    // the first read is the reference, the tick falls on read budget + 1 = the last poll
    auto& model = setUp(10U);
    CHECK(sv::checkLpitDeadmanProgress(EXPECTED, 9U) == LpitCounterProgress::MOVING);
    CHECK(model.reads == 10U);
    setUp(11U);
    CHECK(sv::checkLpitDeadmanProgress(EXPECTED, 9U) == LpitCounterProgress::STOPPED); // one poll short
}

void testStoppedClock()
{
    auto& model = setUp(1U, false);
    CHECK(sv::checkLpitDeadmanProgress(EXPECTED, 22U) == LpitCounterProgress::STOPPED);
    CHECK(model.reads == 23U); // the reference and the whole budget
    CHECK(sv::DEADMAN_FIELD_COUNTER_STOPPED == 0x100U);
    CHECK((sv::DEADMAN_FIELD_COUNTER_STOPPED & 0xFFU) == 0U); // no overlap with the fields of SYS-41
}

void testDisabledChannel()
{
    auto& model                       = setUp(1U, false);
    lpitRegisters.TMR[CHANNEL].TCTRL = 0U;
    CHECK(sv::checkLpitDeadmanProgress(EXPECTED, 22U) == LpitCounterProgress::NOT_RUNNING);
    CHECK(model.reads == 0U);
    // the deadman expires during the polls: its interrupt stops the channel
    auto& expiring        = setUp(1U, false);
    expiring.disableAfter = 5U;
    CHECK(sv::checkLpitDeadmanProgress(EXPECTED, 22U) == LpitCounterProgress::NOT_RUNNING);
}

void testChannelOutsideTheBlock()
{
    auto& model = setUp(1U, false);
    sv::LpitDeadmanConfiguration const wrong{LPIT_TMR_COUNT, 79999U, LPIT0_Ch1_IRQn, 4U};
    CHECK(sv::checkLpitDeadmanProgress(wrong, 22U) == LpitCounterProgress::NOT_RUNNING);
    CHECK(model.reads == 0U);
}

void testZeroBudgetPollsOnce()
{
    auto& model = setUp(1U);
    CHECK(sv::checkLpitDeadmanProgress(EXPECTED, 0U) == LpitCounterProgress::MOVING);
    CHECK(model.reads == 2U);
}

} // namespace

int main()
{
    struct
    {
        char const* name;
        void (*fn)();
    } const tests[] = {
        {"testBudget", testBudget},
        {"testRunningCounterMoves", testRunningCounterMoves},
        {"testTickOnTheLastPoll", testTickOnTheLastPoll},
        {"testStoppedClock", testStoppedClock},
        {"testDisabledChannel", testDisabledChannel},
        {"testChannelOutsideTheBlock", testChannelOutsideTheBlock},
        {"testZeroBudgetPollsOnce", testZeroBudgetPollsOnce},
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
