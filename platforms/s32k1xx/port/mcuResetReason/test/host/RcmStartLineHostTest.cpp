// SYS-84 host test: the start-up line "main(RCM::SRS ...)" of the s32k1xx port reports the reset status of the start,
// not the RCM_SRS of the most recent reset.
//
// Board finding 2026-09-23 (SYS-43, captures 13-18): the line said 0x20 (WDOG) after a power-on and after the reset
// button, because main() printed RCM_SRS after the watchdog fast test - whose two watchdog resets are always the most
// recent ones. Board 2026-09-29 (SYS-84, J-Link): after a pin reset RCM_SRS was 0x20 and the capture before main()
// held 0x40 (PIN), after the J-Link reset 0x20 against 0x400 (SW).
//
// The test plays every start with the register values the MCU can report (RcmResetReason.h, RM Rev. 13 RCM_SRS):
// the first pass with the value of the real reset and the watchdog test field left by the previous start, then the
// resets of the fast test (TST 2, TST 3), and checks the line main() prints in the last pass.

#include "mcuResetReason/RcmResetReason.h"
// SYS-148: the port's reading of the fault-handler record (watchdog vector or fault exception)
#include "mcuResetReason/SafetyResetSources.h"
// SYS-103: the reset-loop rule of SYS-43 (header only, family neutral), by path: the test entry has no include
// directory for it
#include "../../../../../../ecu/safety/safeSupervisor/include/safeSupervisor/ResetLoopLimit.h"

#include <cstdio>
#include <cstring>
#include <string>

namespace
{
unsigned checks   = 0U;
unsigned failures = 0U;

#define CHECK(c)                                                     \
    do                                                               \
    {                                                                \
        ++checks;                                                    \
        if (!(c))                                                    \
        {                                                            \
            ++failures;                                              \
            std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); \
        }                                                            \
    } while (0)

#define CHECK_LINE(actual, expected)                                                                         \
    do                                                                                                       \
    {                                                                                                        \
        std::string const a_ = (actual);                                                                     \
        ++checks;                                                                                            \
        if (a_ != (expected))                                                                                \
        {                                                                                                    \
            ++failures;                                                                                      \
            std::printf("FAIL %s:%d:\n  got      '%s'\n  expected '%s'\n", __FILE__, __LINE__, a_.c_str(), \
                        (expected));                                                                         \
        }                                                                                                    \
    } while (0)

std::string lineOf(::rcm::StartStatus const& s)
{
    char buf[::rcm::START_LINE_SIZE];
    std::memset(buf, 'X', sizeof(buf));
    size_t const n = ::rcm::formatStartLine(buf, sizeof(buf), s);
    CHECK(n == std::strlen(buf));
    return std::string(buf);
}

/** What main() prints after a start whose first pass had \p srs with the test field \p tst. */
std::string startLine(::rcm::Record volatile& record, uint32_t const srs, uint8_t const tst, bool const fastTest)
{
    // pass 1: the real reset
    (void)::rcm::capture(record, srs, tst);
    uint32_t last = srs;
    if (fastTest)
    {
        // pass 2 and 3: the resets of the watchdog fast test (OpenBSW safeBspMcuWatchdog)
        (void)::rcm::capture(record, ::rcm::SRS_WDOG, ::rcm::WDOG_TST_FAST_LOW);
        (void)::rcm::capture(record, ::rcm::SRS_WDOG, ::rcm::WDOG_TST_FAST_HIGH);
        last = ::rcm::SRS_WDOG;
    }
    return lineOf(::rcm::startStatusOf(record, last));
}

void everyResetReason()
{
    using namespace ::rcm;
    struct Case
    {
        uint32_t srs;
        char const* expected;
    };
    // Every start of the application runs the fast test (TST 1 = user mode left by the previous start, 0 after a
    // power-on). The number printed is the reason of the start, the fast test's WDOG only as "last reset".
    Case const cases[] = {
        {SRS_POR | SRS_LVD, "main(RCM::SRS 0x82 POR+LVD, last reset 0x20 WDOG fast test)"},
        {SRS_POR, "main(RCM::SRS 0x80 POR, last reset 0x20 WDOG fast test)"},
        {SRS_LVD, "main(RCM::SRS 0x2 LVD, last reset 0x20 WDOG fast test)"},
        {SRS_PIN, "main(RCM::SRS 0x40 PIN, last reset 0x20 WDOG fast test)"},
        {SRS_SW, "main(RCM::SRS 0x400 SW, last reset 0x20 WDOG fast test)"},
        {SRS_MDM_AP, "main(RCM::SRS 0x800 MDM_AP, last reset 0x20 WDOG fast test)"},
        {SRS_LOCKUP, "main(RCM::SRS 0x200 LOCKUP, last reset 0x20 WDOG fast test)"},
        {SRS_JTAG, "main(RCM::SRS 0x100 JTAG, last reset 0x20 WDOG fast test)"},
        {SRS_LOC, "main(RCM::SRS 0x4 LOC, last reset 0x20 WDOG fast test)"},
        {SRS_LOL, "main(RCM::SRS 0x8 LOL, last reset 0x20 WDOG fast test)"},
        {SRS_CMU_LOC, "main(RCM::SRS 0x10 CMU_LOC, last reset 0x20 WDOG fast test)"},
        {SRS_SACKERR, "main(RCM::SRS 0x2000 SACKERR, last reset 0x20 WDOG fast test)"},
        {SRS_SW | SRS_WDOG, "main(RCM::SRS 0x420 WDOG+SW, last reset 0x20 WDOG fast test)"},
        {SRS_PIN | SRS_MDM_AP, "main(RCM::SRS 0x840 PIN+MDM_AP, last reset 0x20 WDOG fast test)"},
        // a real watchdog time-out: start and last reset are both WDOG, one number
        {SRS_WDOG, "main(RCM::SRS 0x20 WDOG)"},
    };
    for (Case const& c : cases)
    {
        ::rcm::Record volatile record{0xA5A5A5A5UL, 0x5A5A5A5AUL}; // random no-init RAM
        uint8_t const tst = ((c.srs & SRS_POR) != 0U) ? 0U : 1U;
        CHECK_LINE(startLine(record, c.srs, tst, true), c.expected);
    }
}

void startsInARow()
{
    using namespace ::rcm;
    // one no-init record across several starts, as on the board: power-on, reset pin, J-Link, watchdog, power-on
    ::rcm::Record volatile record{0x12345678UL, 0x9ABCDEF0UL};
    CHECK_LINE(startLine(record, SRS_POR | SRS_LVD, 0U, true),
               "main(RCM::SRS 0x82 POR+LVD, last reset 0x20 WDOG fast test)");
    CHECK_LINE(startLine(record, SRS_PIN, 1U, true), "main(RCM::SRS 0x40 PIN, last reset 0x20 WDOG fast test)");
    CHECK_LINE(startLine(record, SRS_SW, 1U, true), "main(RCM::SRS 0x400 SW, last reset 0x20 WDOG fast test)");
    CHECK_LINE(startLine(record, SRS_WDOG, 1U, true), "main(RCM::SRS 0x20 WDOG)");
    CHECK_LINE(startLine(record, SRS_POR | SRS_LVD, 0U, true),
               "main(RCM::SRS 0x82 POR+LVD, last reset 0x20 WDOG fast test)");
}

void withoutFastTestReset()
{
    using namespace ::rcm;
    // A reset that finds TST still 3: the fast test counts as passed and does not reset again, RCM_SRS in main() is
    // the start's own (board 2026-09-23, capture 17 after the held safe state: "main(RCM::SRS 0x40)", SbcEarlyStart
    // "SRS 0x40, TST 3" - the only 0x40 of that session). One number, no "last reset".
    ::rcm::Record volatile record{0U, 0U};
    CHECK_LINE(startLine(record, SRS_PIN, WDOG_TST_FAST_HIGH, false), "main(RCM::SRS 0x40 PIN)");
    // Fast test failed (no reset): the first pass is the start
    CHECK_LINE(startLine(record, SRS_POR | SRS_LVD, 0U, false), "main(RCM::SRS 0x82 POR+LVD)");
}

void recordLost()
{
    using namespace ::rcm;
    // A fast-test reset without an intact record: the start is unknown - never the fast test's WDOG as the reason
    ::rcm::Record volatile lost{SRS_PIN, 0U};
    (void)capture(lost, SRS_WDOG, WDOG_TST_FAST_LOW);
    CHECK_LINE(lineOf(startStatusOf(lost, SRS_WDOG)), "main(RCM::SRS unknown, last reset 0x20 WDOG fast test)");
    ::rcm::Record volatile stale{SRS_WDOG, ~SRS_WDOG}; // right complement, wrong key
    CHECK(!startStatusOf(stale, SRS_WDOG).known);
    CHECK_LINE(lineOf(startStatusOf(stale, SRS_WDOG)), "main(RCM::SRS unknown, last reset 0x20 WDOG fast test)");
}

/*
 * SYS-103: a real watchdog reset reports WDOG + SW (0x420) or WDOG alone (0x20), depending on whether the fault
 * handler behind the watchdog's pre-reset interrupt reaches its AIRCR.SYSRESETREQ inside the reset delay of the RCM
 * (board 2026-09-29, captures sys103_*). Both are the same start reason, and both count on the reset-loop limit of
 * SYS-43 by the MCU source - also when the hard fault dump (the other source) is missing.
 */
void watchdogWithHandlerReset()
{
    using namespace ::rcm;
    namespace wakeup = ::ecustate::wakeup;
    CHECK(startReasonsOf(SRS_WDOG) == wakeup::WATCHDOG);
    CHECK(startReasonsOf(SRS_WDOG | SRS_SW) == wakeup::WATCHDOG); // the handler's own SYSRESETREQ: no RESET
    CHECK(startReasonsOf(SRS_SW) == wakeup::RESET);               // software reset without watchdog: unchanged
    // any other reset next to WDOG stays a reset reason of its own (also with the handler's SW)
    uint32_t const others[] = {SRS_PIN, SRS_MDM_AP, SRS_JTAG, SRS_LOCKUP, SRS_LOC, SRS_LOL, SRS_CMU_LOC, SRS_SACKERR};
    for (uint32_t const bit : others)
    {
        CHECK(startReasonsOf(SRS_WDOG | bit) == (wakeup::WATCHDOG | wakeup::RESET));
        CHECK(startReasonsOf(SRS_WDOG | SRS_SW | bit) == (wakeup::WATCHDOG | wakeup::RESET));
        CHECK(startReasonsOf(SRS_SW | bit) == wakeup::RESET);
    }
    CHECK(startReasonsOf(SRS_POR | SRS_LVD | SRS_WDOG | SRS_SW) == (wakeup::POWER | wakeup::WATCHDOG));

    // through the resets of the fast test: the start reason of the start, not of the test
    ::rcm::Record volatile record{0xA5A5A5A5UL, 0x5A5A5A5AUL};
    (void)capture(record, SRS_WDOG | SRS_SW, 1U);
    (void)capture(record, SRS_WDOG, WDOG_TST_FAST_LOW);
    CHECK(capture(record, SRS_WDOG, WDOG_TST_FAST_HIGH) == wakeup::WATCHDOG);
    // the start-up line keeps the raw register value
    CHECK_LINE(lineOf(startStatusOf(record, SRS_WDOG)), "main(RCM::SRS 0x420 WDOG+SW, last reset 0x20 WDOG fast test)");
}

/* The hard fault dump / lockup record of the start, as resetLoopStart() in port/main/src/main.cpp reads it. */
enum class Dump
{
    NONE,
    FAULT,    // valid dump, exception 3 (hard fault; board 2026-09-30 `safety mpu`)
    WATCHDOG, // valid dump, exception 38 (the watchdog's pre-reset interrupt; board 2026-09-30 `safety wdg`)
    CORRUPT,  // magic present, checksum wrong: no exception number
    LOCKUP    // no dump, RCM_SRS[LOCKUP] of the start
};

constexpr uint32_t WATCHDOG_EXCEPTION = 22U + 16U; // WDOG_EWM_IRQn (S32K14x) / WDOG_IRQn (S32K11x) + 16

/* The reset-loop start of SYS-43 as the s32k1xx port builds it (resetLoopStart() in port/main/src/main.cpp) from the
 * start reasons and the fault-handler record - through the same functions (mcuResetReason/SafetyResetSources.h). */
::safety::resetloop::Start resetLoopStartOf(uint32_t const srs, Dump const dump)
{
    bool const present       = (dump == Dump::FAULT) || (dump == Dump::WATCHDOG) || (dump == Dump::CORRUPT);
    uint32_t const exception = (dump == Dump::FAULT) ? 3U : ((dump == Dump::WATCHDOG) ? WATCHDOG_EXCEPTION : 0U);
    ::rcm::SafetyResetSources const s = ::rcm::safetyResetSourcesOf(
        ::rcm::startReasonsOf(srs),
        ::rcm::handlerRecordOf(present, exception, dump == Dump::LOCKUP, WATCHDOG_EXCEPTION));
    ::safety::resetloop::Start start{};
    start.powerOn        = s.powerOn;
    start.watchdog       = s.watchdog;
    start.otherReset     = s.otherReset;
    start.faultException = s.faultException;
    return start;
}

/* SYS-148: the handler record alone */
void handlerRecord()
{
    using ::rcm::HandlerRecord;
    using ::rcm::handlerRecordOf;
    CHECK(handlerRecordOf(false, 0U, false, WATCHDOG_EXCEPTION) == HandlerRecord::NONE);
    CHECK(handlerRecordOf(true, 3U, false, WATCHDOG_EXCEPTION) == HandlerRecord::FAULT_EXCEPTION);
    CHECK(handlerRecordOf(true, WATCHDOG_EXCEPTION, false, WATCHDOG_EXCEPTION) == HandlerRecord::WATCHDOG);
    // every other vector through the handler (MemManage 4, BusFault 5, UsageFault 6, an IRQ) is a fault exception
    uint32_t const others[] = {4U, 5U, 6U, 16U, 37U, 39U, 0x1FFU};
    for (uint32_t const exception : others)
    {
        CHECK(handlerRecordOf(true, exception, false, WATCHDOG_EXCEPTION) == HandlerRecord::FAULT_EXCEPTION);
    }
    // a corrupt dump has no number (0) - also when the derivative's watchdog number were 0 it is no watchdog
    CHECK(handlerRecordOf(true, 0U, false, WATCHDOG_EXCEPTION) == HandlerRecord::FAULT_EXCEPTION);
    CHECK(handlerRecordOf(true, 0U, false, 0U) == HandlerRecord::FAULT_EXCEPTION);
    // a lockup wins over everything: the handler itself faulted
    CHECK(handlerRecordOf(false, 0U, true, WATCHDOG_EXCEPTION) == HandlerRecord::FAULT_EXCEPTION);
    CHECK(handlerRecordOf(true, WATCHDOG_EXCEPTION, true, WATCHDOG_EXCEPTION) == HandlerRecord::FAULT_EXCEPTION);
}

void resetLoopCounting()
{
    using namespace ::rcm;
    namespace rl = ::safety::resetloop;
    struct Case
    {
        char const* name;
        uint32_t srs;
        Dump dump;
        bool counted;
        uint32_t cause;
    };
    Case const cases[] = {
        // real watchdog reset, handler's SYSRESETREQ inside the reset delay (board: "main(RCM::SRS 0x420 WDOG+SW")
        {"WDOG+SW without dump", SRS_WDOG | SRS_SW, Dump::NONE, true, rl::CAUSE_WATCHDOG},
        // SYS-148: board 2026-09-30 `safety wdg` - dump of vector 38, before: "last cause 0x2"
        {"WDOG+SW with watchdog dump", SRS_WDOG | SRS_SW, Dump::WATCHDOG, true, rl::CAUSE_WATCHDOG},
        {"WDOG+SW with fault dump", SRS_WDOG | SRS_SW, Dump::FAULT, true, rl::CAUSE_FAULT_EXCEPTION},
        {"WDOG+SW with corrupt dump", SRS_WDOG | SRS_SW, Dump::CORRUPT, true, rl::CAUSE_FAULT_EXCEPTION},
        // real watchdog reset, watchdog first (board: halted before SYSRESETREQ, or interrupts masked)
        {"WDOG without dump", SRS_WDOG, Dump::NONE, true, rl::CAUSE_WATCHDOG},
        {"WDOG with watchdog dump", SRS_WDOG, Dump::WATCHDOG, true, rl::CAUSE_WATCHDOG},
        {"WDOG with fault dump", SRS_WDOG, Dump::FAULT, true, rl::CAUSE_FAULT_EXCEPTION},
        // SYS-148: board 2026-09-30 `safety mpu` - the fault handler's software reset after a dump of vector 3
        {"SW with fault dump", SRS_SW, Dump::FAULT, true, rl::CAUSE_FAULT_EXCEPTION},
        {"SW with lockup", SRS_SW | SRS_LOCKUP, Dump::LOCKUP, true, rl::CAUSE_FAULT_EXCEPTION},
        // the watchdog dump is the proof of a time-out, whatever reset bit stands next to it
        {"SW with watchdog dump", SRS_SW, Dump::WATCHDOG, true, rl::CAUSE_WATCHDOG},
        {"WDOG+PIN with watchdog dump", SRS_WDOG | SRS_PIN, Dump::WATCHDOG, true, rl::CAUSE_WATCHDOG},
        // resets that neither count nor clear
        {"J-Link / software reset", SRS_SW, Dump::NONE, false, rl::CAUSE_NONE},
        {"reset pin", SRS_PIN, Dump::NONE, false, rl::CAUSE_NONE},
        {"debugger MDM-AP", SRS_MDM_AP, Dump::NONE, false, rl::CAUSE_NONE},
        {"WDOG next to the reset pin", SRS_WDOG | SRS_PIN, Dump::NONE, false, rl::CAUSE_NONE},
        {"WDOG next to MDM-AP", SRS_WDOG | SRS_MDM_AP, Dump::NONE, false, rl::CAUSE_NONE},
        {"WDOG+SW next to the reset pin", SRS_WDOG | SRS_SW | SRS_PIN, Dump::NONE, false, rl::CAUSE_NONE},
        {"WDOG+SW next to JTAG", SRS_WDOG | SRS_SW | SRS_JTAG, Dump::NONE, false, rl::CAUSE_NONE},
    };
    for (Case const& c : cases)
    {
        rl::Record volatile record{};
        rl::write(record, 1U, rl::CAUSE_NONE, rl::CAUSE_EVENT | 5U); // one safety reset in a row before
        rl::StartResult const r = rl::onStart(record, resetLoopStartOf(c.srs, c.dump));
        bool const ok = (r.counted == c.counted) && (r.count == (c.counted ? 2U : 1U))
                        && (r.lastCause == (c.counted ? c.cause : (rl::CAUSE_EVENT | 5U)));
        CHECK(ok);
        if (!ok)
        {
            std::printf("  case '%s': counted %d count %u cause 0x%x\n",
                        c.name,
                        r.counted ? 1 : 0,
                        static_cast<unsigned>(r.count),
                        static_cast<unsigned>(r.lastCause));
        }
    }
    // power-on next to anything clears
    rl::Record volatile record{};
    rl::write(record, 2U, rl::CAUSE_NONE, rl::CAUSE_WATCHDOG);
    CHECK(rl::onStart(record, resetLoopStartOf(SRS_POR | SRS_LVD | SRS_WDOG | SRS_SW, Dump::NONE)).count == 0U);
    CHECK(rl::onStart(record, resetLoopStartOf(SRS_POR | SRS_LVD, Dump::WATCHDOG)).count == 0U);

    // three real watchdog resets in a row, each with the handler's SW and the dump lost: the limit is reached
    rl::clear(record);
    uint32_t count = 0U;
    for (int i = 0; i < 3; ++i)
    {
        count = rl::onStart(record, resetLoopStartOf(SRS_WDOG | SRS_SW, Dump::NONE)).count;
    }
    CHECK(count == 3U);
    CHECK(rl::limitReached(count, 3U));

    // SYS-148: board sequence 2026-09-30 - `safety wdg`, then `safety mpu` within the fault-free time: the two
    // causes stay apart (0x1, then 0x2), and the hold text names the last one
    rl::clear(record);
    rl::StartResult const afterWdg = rl::onStart(record, resetLoopStartOf(SRS_WDOG | SRS_SW, Dump::WATCHDOG));
    CHECK((afterWdg.count == 1U) && (afterWdg.lastCause == 0x1U));
    rl::StartResult const afterMpu = rl::onStart(record, resetLoopStartOf(SRS_SW, Dump::FAULT));
    CHECK((afterMpu.count == 2U) && (afterMpu.lastCause == 0x2U));
    char text[80];
    (void)rl::formatHoldText(text, sizeof(text), 3U, afterWdg.lastCause);
    CHECK_LINE(std::string(text), "RESET_LOOP_LIMIT, 3 safety resets, last: watchdog");
}

void lineLimits()
{
    using namespace ::rcm;
    // all bits in both values: every name once, the whole line fits START_LINE_SIZE (the buffer of main())
    StartStatus const all{0xFFFFFFFFUL, 0xFFFFFFFFUL, true};
    CHECK_LINE(lineOf(all),
               "main(RCM::SRS 0xffffffff POR+LVD+WDOG+PIN+SW+MDM_AP+LOCKUP+JTAG+LOC+LOL+CMU_LOC+SACKERR)");
    CHECK_LINE(lineOf(StartStatus{0xFFFFFFFFUL, 0x3FFFUL, true}),
               "main(RCM::SRS 0xffffffff POR+LVD+WDOG+PIN+SW+MDM_AP+LOCKUP+JTAG+LOC+LOL+CMU_LOC+SACKERR, last reset "
               "0x3fff POR+LVD+WDOG+PIN+SW+MDM_AP+LOCKUP+JTAG+LOC+LOL+CMU_LOC+SACKERR fast test)");
    // 0 (no bit): number only
    CHECK_LINE(lineOf(StartStatus{0U, 0U, true}), "main(RCM::SRS 0x0)");
    // truncation keeps the terminator and never writes past the buffer
    char small[16];
    std::memset(small, 'X', sizeof(small));
    size_t const n = formatStartLine(small, 12U, StartStatus{SRS_POR | SRS_LVD, SRS_WDOG, true});
    CHECK(n == 11U);
    CHECK(std::strcmp(small, "main(RCM::S") == 0);
    CHECK(small[12] == 'X');
    char one[1] = {'X'};
    CHECK(formatStartLine(one, 1U, all) == 0U);
    CHECK(one[0] == '\0');
}
} // namespace

int main()
{
    everyResetReason();
    startsInARow();
    withoutFastTestReset();
    recordLost();
    watchdogWithHandlerReset();
    handlerRecord();
    resetLoopCounting();
    lineLimits();
    std::printf("rcm-start-line: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
