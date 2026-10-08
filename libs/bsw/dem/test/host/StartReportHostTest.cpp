/* SYS-113: host test of the start report of the fault memory (dem/StartReport.h) and of the cause of a safety reset as
 * BSW sources (dem/SafetyResetReport.h). Own test profile, not the eval board: six DTCs in the real fault memory core
 * (dem::Manager) - the reset loop, the fallback, the MCU watchdog with an enable condition, the fault exception with
 * time debouncing, the deadman configuration check and one DTC of the degraded reaction (safety.device_fault) that
 * another monitor reports - and a second profile without the deadman DTC (fallback).
 *
 * The start result comes from the real reset-loop rule of the safe supervisor (safeSupervisor/ResetLoopLimit.h,
 * resetloop::onStart on a record in RAM), so the cause codes of both headers are checked against each other.
 *
 * Fault injection: safety reset by the watchdog, by a fault exception, by an event of the supervisor with and without
 * its own DTC, by DEVICE_FAULT, an unknown cause; several in a row (reset loop); a start that did not follow a safety
 * reset with an old cause in the record; the report before the fault memory runs; an enable condition that holds
 * late; time debouncing; ControlDTCSetting (85) off at the start; a clear after the hand-over; a status left from the
 * operation cycle before; a fault memory that is replaced.
 *
 * SYS-196: the occurrence report (dem/OccurrenceReport.h) with its own profile - the DTC of a foreign safe_off level
 * taken back with a snapshot of output and counts: no occurrence no DTC (counter-check), one occurrence one failure
 * then PASSED, folding, the snapshot of the first confirmation, the reset (NvM images into a new fault memory), 14
 * FFFFFF, both task orders, time debouncing, an enable condition, 85 off, a status saved with testFailed.
 *
 * Build and run: libs/bsw/dem/test/test_start_report.py (also with deliberate faults).
 */

#include <dem/Dem.h>
#include <dem/OccurrenceReport.h>
#include <dem/SafetyResetReport.h>
#include <dem/StartReport.h>

#include <safeSupervisor/ResetLoopLimit.h>

#include <cstdio>
#include <cstring>

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

struct TestLock
{
    TestLock() {}

    ~TestLock() {}
};

using ::dem::Result;
namespace st = ::dem::status;
namespace sr = ::dem::safetyreset;
namespace rl = ::safety::resetloop;
using Manager = ::dem::Manager<TestLock>;

// ---- the cause codes are the ones of the reset-loop record
static_assert(sr::CAUSE_NONE == rl::CAUSE_NONE, "cause code");
static_assert(sr::CAUSE_WATCHDOG == rl::CAUSE_WATCHDOG, "cause code");
static_assert(sr::CAUSE_FAULT_EXCEPTION == rl::CAUSE_FAULT_EXCEPTION, "cause code");
static_assert(sr::CAUSE_EVENT == rl::CAUSE_EVENT, "cause code");

constexpr uint32_t EVENT_DEADMAN      = 0x10U; // SafeSupervisor::Event::DEADMAN_CONFIGURATION_ERROR
constexpr uint32_t EVENT_ETL_ASSERT   = 0x17U; // ETL_ASSERT
constexpr uint32_t EVENT_DEVICE_FAULT = 0x13U; // DEVICE_FAULT

size_t indexOf(char const* const event)
{
    for (size_t i = 0U; i < sr::SOURCES; ++i)
    {
        if (std::strcmp(sr::EVENTS[i], event) == 0)
        {
            return i;
        }
    }
    return sr::SOURCES;
}

uint32_t bitOf(char const* const event) { return ::dem::StartReport::bit(indexOf(event)); }

// ---- test profile: the safety DTCs as the standard description writes them (reported by the BSW, one report
// matures, confirmation threshold 1, no enable condition), with deliberate differences to cover the hand-over
constexpr uint32_t CONDITION_RUNNING = 0x1U;
::dem::DtcConfig const DTCS[] = {
    {"SafetyResetLoop", 0x1605FFU, 0x20U, 1U, 1U, 40U, 0U, 0U, 0x0U, ::dem::DebounceKind::COUNTER, {1, -1, 1U, 1U, 0, 0},
     {0U, 0U}, nullptr, 0U, nullptr, 0U, "safety.reset_loop"},
    {"SafetyResetOther", 0x1605FBU, 0x20U, 1U, 1U, 40U, 0U, 0U, 0x0U, ::dem::DebounceKind::COUNTER, {1, -1, 1U, 1U, 0, 0},
     {0U, 0U}, nullptr, 0U, nullptr, 0U, "safety.reset_other"},
    // enable condition: the failure is taken over only once the ECU runs
    {"SafetyResetMcuWatchdog", 0x1605FDU, 0x20U, 1U, 1U, 40U, 0U, 0U, CONDITION_RUNNING, ::dem::DebounceKind::COUNTER,
     {1, -1, 1U, 1U, 0, 0}, {0U, 0U}, nullptr, 0U, nullptr, 0U, "safety.mcu_watchdog"},
    // time debouncing: the failure has to stay reported for 30 ms (three cycles)
    {"SafetyResetFaultException", 0x1605FCU, 0x20U, 1U, 1U, 40U, 0U, 0U, 0x0U, ::dem::DebounceKind::TIME,
     {1, -1, 1U, 1U, 0, 0}, {30U, 30U}, nullptr, 0U, nullptr, 0U, "safety.fault_exception"},
    {"SafetyResetDeadman", 0x160510U, 0x20U, 1U, 1U, 40U, 0U, 0U, 0x0U, ::dem::DebounceKind::COUNTER,
     {1, -1, 1U, 1U, 0, 0}, {0U, 0U}, nullptr, 0U, nullptr, 0U, "safety.deadman_configuration_error"},
    // another monitor's event: never touched by the start report
    {"SafetyDegraded", 0x160513U, 0x40U, 2U, 1U, 40U, 0U, 0U, 0x0U, ::dem::DebounceKind::COUNTER, {1, -1, 1U, 1U, 0, 0},
     {0U, 0U}, nullptr, 0U, nullptr, 0U, "safety.device_fault"},
};
constexpr uint16_t LOOP = 0U, OTHER = 1U, WDG = 2U, FEX = 3U, DEADMAN = 4U, DEGRADED = 5U;
constexpr uint16_t DTC_COUNT = 6U;
::dem::Config const CONFIG = {DTCS, DTC_COUNT, 6U, true, 1U, 0x1U, 1U, 0x0U, 10U, 1U};
// second profile: the project left the deadman DTC out (and the reset loop)
::dem::Config const CONFIG_WITHOUT_DEADMAN = {&DTCS[1], 3U, 3U, true, 1U, 0x1U, 1U, 0x0U, 10U, 1U};

class Source : public ::dem::ISource
{
public:
    bool running = true;

    Result sample(uint16_t) override { return Result::NONE; }

    bool condition(uint8_t) override { return running; }

    uint8_t cycleState(uint8_t) override { return CYCLE_AUTOMATIC; }
};

/** One fault memory with its storage, as the integration (ecu/application DemRte.h) owns it. */
struct Memory
{
    ::dem::DtcState states[DTC_COUNT];
    ::dem::Entry entries[DTC_COUNT];
    uint8_t data[DTC_COUNT];
    uint8_t scratch[1];
    Manager manager;
    Source source;

    explicit Memory(::dem::Config const& config = CONFIG)
    : states(), entries(), data(), scratch(), manager(config, states, entries, data, scratch), source()
    {
        manager.start();
    }

    void cycle() { manager.mainFunction(source); }

    uint8_t status(uint16_t const dtc) const
    {
        ::dem::DtcInfo info{};
        (void)manager.dtcInfo(dtc, info);
        return info.status;
    }

    uint8_t occurrence(uint16_t const dtc) const
    {
        ::dem::DtcInfo info{};
        (void)manager.dtcInfo(dtc, info);
        return info.occurrence;
    }
};

/** Records what the start report sends (and answers dtcInfo with a settable status). */
class Recorder : public ::dem::IFaultMemory
{
public:
    Result log[32];
    uint16_t dtcs[32];
    unsigned count    = 0U;
    unsigned lookups  = 0U;
    uint8_t statusNow = 0U;

    uint16_t dtcCount() const override { return 1U; }

    ::dem::DtcConfig const& dtcConfig(uint16_t) const override { return DTCS[0]; }

    bool dtcInfo(uint16_t, ::dem::DtcInfo& info) const override
    {
        info        = ::dem::DtcInfo{};
        info.status = statusNow;
        return true;
    }

    int32_t findDtc(uint32_t) const override { return -1; }

    uint16_t countByStatusMask(uint8_t) const override { return 0U; }

    ::dem::MemoryInfo memoryInfo() const override { return ::dem::MemoryInfo{}; }

    int32_t readSnapshot(uint16_t, uint8_t, uint8_t*, size_t) const override { return -1; }

    int32_t readExtended(uint16_t, uint8_t, uint8_t*, size_t) const override { return -1; }

    bool clear(uint32_t) override { return false; }

    void setDtcSetting(bool) override {}

    int32_t findReportedEvent(char const* const name) const override
    {
        ++const_cast<Recorder*>(this)->lookups;
        return (std::strcmp(name, "safety.mcu_watchdog") == 0) ? 3 : -1;
    }

    void report(uint16_t const dtc, Result const result) override
    {
        if (count < 32U)
        {
            dtcs[count] = dtc;
            log[count]  = result;
        }
        ++count;
    }
};

/** A start as main() of the family port runs it: the record of the starts before, then the start result. */
rl::StartResult startAfter(rl::Record& record, bool const watchdog, bool const faultException, uint32_t const mark)
{
    if (mark != rl::CAUSE_NONE)
    {
        rl::markSafetyReset(record, mark);
    }
    rl::Start start{};
    start.watchdog       = watchdog;
    start.faultException = faultException;
    return rl::onStart(record, start);
}

void setStart(sr::Report& report, rl::StartResult const& result)
{
    report.setStart(result.counted, result.count, result.lastCause);
}

constexpr uint8_t FAILED_NOW = st::TEST_FAILED | st::TEST_FAILED_THIS_OPERATION_CYCLE | st::PENDING_DTC
                               | st::CONFIRMED_DTC | st::TEST_FAILED_SINCE_LAST_CLEAR;
constexpr uint8_t TESTED_BITS = st::TEST_NOT_COMPLETED_SINCE_LAST_CLEAR | st::TEST_NOT_COMPLETED_THIS_OPERATION_CYCLE;

// ------------------------------------------------------------------------------------ the table and the rule

void testTable()
{
    CHECK(sr::SOURCES <= ::dem::StartReport::MAX_SOURCES);
    CHECK(indexOf("safety.reset_loop") == sr::RESET_LOOP);
    CHECK(indexOf("safety.reset_other") == sr::RESET_OTHER);
    CHECK(sr::CAUSES[indexOf("safety.mcu_watchdog")] == rl::CAUSE_WATCHDOG);
    CHECK(sr::CAUSES[indexOf("safety.fault_exception")] == rl::CAUSE_FAULT_EXCEPTION);
    CHECK(sr::CAUSES[indexOf("safety.deadman_configuration_error")] == (rl::CAUSE_EVENT | EVENT_DEADMAN));
    CHECK(sr::CAUSES[indexOf("safety.etl_assert")] == (rl::CAUSE_EVENT | EVENT_ETL_ASSERT));
    // the degraded reaction is another monitor's event, the hold at the limit and the report of a device never reset
    CHECK(indexOf("safety.device_fault") == sr::SOURCES);
    CHECK(indexOf("safety.reset_loop_limit") == sr::SOURCES);
    CHECK(indexOf("safety.device_report") == sr::SOURCES);
    // held or not resetting today: a DTC of these names must not pass at every start
    CHECK(indexOf("safety.rom_check_failure") == sr::SOURCES);
    CHECK(indexOf("safety.watchdog_startup_check_failure") == sr::SOURCES);
    CHECK(indexOf("safety.check_register_error") == sr::SOURCES);
    CHECK(indexOf("safety.rte_timing_violation") == sr::SOURCES);
    CHECK(sr::failedOf(true, 1U, rl::CAUSE_EVENT | 0x08U) == bitOf("safety.reset_other"));
    // every cause once
    for (size_t i = sr::FIRST_CAUSE; i < sr::SOURCES; ++i)
    {
        CHECK(sr::CAUSES[i] != sr::CAUSE_NONE);
        for (size_t j = i + 1U; j < sr::SOURCES; ++j)
        {
            CHECK(sr::CAUSES[i] != sr::CAUSES[j]);
            CHECK(std::strcmp(sr::EVENTS[i], sr::EVENTS[j]) != 0);
        }
    }
    CHECK(sr::INSTANCE_RESET_LOOP == 0xFFU);
    CHECK(sr::INSTANCE_MCU_WATCHDOG == 0xFDU);
    CHECK(sr::INSTANCE_FAULT_EXCEPTION == 0xFCU);
    CHECK(sr::INSTANCE_RESET_OTHER == 0xFBU);
}

void testFailedOf()
{
    // no safety reset before this start: nothing failed, whatever the record says
    CHECK(sr::failedOf(false, 0U, rl::CAUSE_NONE) == 0U);
    CHECK(sr::failedOf(false, 2U, rl::CAUSE_WATCHDOG) == 0U);
    // one safety reset: its cause only
    CHECK(sr::failedOf(true, 1U, rl::CAUSE_WATCHDOG) == bitOf("safety.mcu_watchdog"));
    CHECK(sr::failedOf(true, 1U, rl::CAUSE_FAULT_EXCEPTION) == bitOf("safety.fault_exception"));
    CHECK(sr::failedOf(true, 1U, rl::CAUSE_EVENT | EVENT_DEADMAN) == bitOf("safety.deadman_configuration_error"));
    // two or more in a row: the reset loop as well
    CHECK(sr::failedOf(true, 2U, rl::CAUSE_WATCHDOG) == (bitOf("safety.mcu_watchdog") | bitOf("safety.reset_loop")));
    CHECK(sr::failedOf(true, 0xFFFFU, rl::CAUSE_EVENT | EVENT_ETL_ASSERT)
          == (bitOf("safety.etl_assert") | bitOf("safety.reset_loop")));
    // a cause without a source of its own (DEVICE_FAULT, a new event, a damaged record): the fallback
    CHECK(sr::failedOf(true, 1U, rl::CAUSE_EVENT | EVENT_DEVICE_FAULT) == bitOf("safety.reset_other"));
    CHECK(sr::failedOf(true, 1U, 0x1FFU) == bitOf("safety.reset_other"));
    CHECK(sr::failedOf(true, 1U, 0x3U) == bitOf("safety.reset_other"));
    CHECK(sr::failedOf(true, 3U, 0x3U) == (bitOf("safety.reset_other") | bitOf("safety.reset_loop")));
    // the fallback covers every cause, never itself nor the loop
    CHECK((sr::otherCovers() & bitOf("safety.reset_other")) == 0U);
    CHECK((sr::otherCovers() & bitOf("safety.reset_loop")) == 0U);
    CHECK((sr::otherCovers() & bitOf("safety.mcu_watchdog")) != 0U);
    CHECK((sr::otherCovers() & bitOf("safety.etl_assert")) != 0U);
}

// ------------------------------------------------------------------------------------ StartReport alone

void testStartReportAlone()
{
    char const* const names[] = {"safety.mcu_watchdog", "safety.missing"};
    ::dem::StartReport report(names, 2U);
    report.setFailed(0x1U);
    Recorder memory;
    // before the fault memory: nothing, and nothing lost
    report.update(nullptr);
    report.update(nullptr);
    CHECK(memory.count == 0U);
    report.update(&memory);
    CHECK(memory.lookups == 2U);
    CHECK(report.found() == 0x1U); // only the first has a DTC
    CHECK(memory.count == 1U);
    CHECK(memory.dtcs[0] == 3U);
    CHECK(memory.log[0] == Result::FAILED);
    // not taken over yet: FAILED again in every call
    report.update(&memory);
    report.update(&memory);
    CHECK(memory.count == 3U);
    CHECK(memory.log[2] == Result::FAILED);
    // a status from before (failed since the last clear) is no hand-over
    memory.statusNow = st::TEST_FAILED_SINCE_LAST_CLEAR | st::CONFIRMED_DTC | st::TEST_FAILED;
    report.update(&memory);
    CHECK(memory.count == 4U);
    CHECK(memory.log[3] == Result::FAILED);
    // taken over: PASSED once, then nothing
    memory.statusNow = st::TEST_FAILED_THIS_OPERATION_CYCLE | st::TEST_FAILED;
    report.update(&memory);
    CHECK(memory.count == 5U);
    CHECK(memory.log[4] == Result::PASSED);
    CHECK(report.pending() == 0U);
    report.update(&memory);
    report.update(&memory);
    CHECK(memory.count == 5U);
    CHECK(memory.lookups == 2U);
    // a new fault memory gets everything again
    Recorder second;
    report.update(&second);
    CHECK(second.lookups == 2U);
    CHECK(second.count == 1U);
    CHECK(second.log[0] == Result::FAILED);
    // a source that did not fail: PASSED once
    ::dem::StartReport clean(names, 2U);
    clean.setFailed(0U);
    Recorder third;
    clean.update(&third);
    clean.update(&third);
    CHECK(third.count == 1U);
    CHECK(third.log[0] == Result::PASSED);
    CHECK(clean.pending() == 0U);
}

void testFallbackAlone()
{
    char const* const names[] = {"safety.mcu_watchdog", "safety.missing", "safety.reset_other"};
    Recorder memory;
    ::dem::StartReport report(names, 3U);
    report.setFailed(0x2U);            // the source without a DTC failed
    report.setFallback(0U, 0x2U | 0x1U); // stands for both (the index itself is no cover)
    report.update(&memory);
    CHECK(report.reportedFailed() == 0x3U);
    CHECK(memory.count == 1U);
    CHECK(memory.log[0] == Result::FAILED);
    // the failed source has its DTC: no fallback
    ::dem::StartReport own(names, 3U);
    own.setFailed(0x1U);
    own.setFallback(1U, 0x1U);
    Recorder second;
    own.update(&second);
    CHECK(own.reportedFailed() == 0x1U);
}

// ------------------------------------------------------------------- safety reset in the real fault memory

void testWatchdogResetWithEnableCondition()
{
    rl::Record record{};
    rl::clear(record);
    rl::StartResult const result = startAfter(record, true, false, rl::CAUSE_NONE);
    CHECK(result.counted);
    CHECK(result.count == 1U);
    sr::Report report;
    setStart(report, result);
    Memory memory;
    memory.source.running = false; // the ECU is not in RUN yet: the watchdog DTC's samples are discarded
    report.update(nullptr);        // the BSP task runs before the fault memory
    report.update(&memory.manager);
    memory.cycle();
    report.update(&memory.manager);
    memory.cycle();
    CHECK((memory.status(WDG) & st::TEST_FAILED) == 0U);
    CHECK((report.report().pending() & bitOf("safety.mcu_watchdog")) != 0U); // still waiting
    // the others are handed over: tested and passed
    CHECK(memory.status(DEADMAN) == 0U);
    CHECK(memory.status(FEX) == 0U);
    CHECK(memory.status(LOOP) == 0U);
    CHECK(memory.status(OTHER) == 0U);
    CHECK(memory.status(DEGRADED) == st::INITIAL); // another monitor's DTC is not touched
    // RUN: the failure is taken over at once, then PASSED
    memory.source.running = true;
    report.update(&memory.manager);
    memory.cycle();
    CHECK(memory.status(WDG) == FAILED_NOW);
    CHECK(memory.occurrence(WDG) == 1U);
    report.update(&memory.manager);
    memory.cycle();
    CHECK(report.report().pending() == 0U);
    CHECK((memory.status(WDG) & st::TEST_FAILED) == 0U);       // not stuck failed
    CHECK((memory.status(WDG) & st::CONFIRMED_DTC) != 0U);     // but recorded
    CHECK((memory.status(WDG) & st::TEST_FAILED_THIS_OPERATION_CYCLE) != 0U);
    CHECK(memory.manager.memoryInfo().entriesUsed == 1U);
    // a clear in the same run removes it for good
    CHECK(memory.manager.clear(::dem::GROUP_ALL_DTCS));
    for (int i = 0; i < 5; ++i)
    {
        report.update(&memory.manager);
        memory.cycle();
    }
    CHECK((memory.status(WDG) & (st::TEST_FAILED | st::CONFIRMED_DTC | st::TEST_FAILED_SINCE_LAST_CLEAR)) == 0U);
    CHECK(memory.occurrence(WDG) == 0U);
}

void testFaultExceptionWithTimeDebouncing()
{
    rl::Record record{};
    rl::clear(record);
    sr::Report report;
    setStart(report, startAfter(record, false, true, rl::CAUSE_NONE));
    Memory memory;
    // 30 ms of FAILED reports mature the time debounced DTC: the report must stay until then
    for (int i = 0; i < 6; ++i)
    {
        report.update(&memory.manager);
        memory.cycle();
    }
    CHECK((memory.status(FEX) & st::CONFIRMED_DTC) != 0U);
    CHECK(memory.occurrence(FEX) == 1U);
    CHECK((memory.status(WDG) & st::TEST_FAILED_SINCE_LAST_CLEAR) == 0U);
    CHECK((memory.status(LOOP) & st::TEST_FAILED_SINCE_LAST_CLEAR) == 0U);
}

void testEventResetsInARow()
{
    rl::Record record{};
    rl::clear(record);
    // the supervisor resets for DEADMAN_CONFIGURATION_ERROR three times; the first two starts are cut short
    (void)startAfter(record, false, false, rl::CAUSE_EVENT | EVENT_DEADMAN);
    rl::StartResult const result = startAfter(record, false, false, rl::CAUSE_EVENT | EVENT_DEADMAN);
    CHECK(result.count == 2U);
    sr::Report report;
    setStart(report, result);
    Memory memory;
    for (int i = 0; i < 3; ++i)
    {
        report.update(&memory.manager);
        memory.cycle();
    }
    CHECK((memory.status(DEADMAN) & st::CONFIRMED_DTC) != 0U);
    CHECK((memory.status(LOOP) & st::CONFIRMED_DTC) != 0U);
    CHECK((memory.status(OTHER) & st::TEST_FAILED_SINCE_LAST_CLEAR) == 0U);
    CHECK((memory.status(WDG) & st::TEST_FAILED_SINCE_LAST_CLEAR) == 0U);
    CHECK(memory.manager.memoryInfo().entriesUsed == 2U);
    // the next start follows a reset pin press: no safety reset, the record keeps its cause - nothing fails again
    rl::Start pin{};
    pin.otherReset = true;
    rl::StartResult const after = rl::onStart(record, pin);
    CHECK(!after.counted);
    CHECK(after.count == 2U);
    sr::Report next;
    setStart(next, after);
    Memory second;
    for (int i = 0; i < 3; ++i)
    {
        next.update(&second.manager);
        second.cycle();
    }
    for (uint16_t dtc = LOOP; dtc <= DEADMAN; ++dtc)
    {
        CHECK((second.status(dtc) & st::TEST_FAILED_SINCE_LAST_CLEAR) == 0U);
        CHECK((second.status(dtc) & TESTED_BITS) == 0U); // tested and passed
    }
}

void testFallbackInTheFaultMemory()
{
    // the project has no deadman DTC: the reset goes to safety.reset_other
    rl::Record record{};
    rl::clear(record);
    sr::Report report;
    setStart(report, startAfter(record, false, false, rl::CAUSE_EVENT | EVENT_DEADMAN));
    Memory memory(CONFIG_WITHOUT_DEADMAN); // DTCs: other, watchdog, fault exception
    for (int i = 0; i < 3; ++i)
    {
        report.update(&memory.manager);
        memory.cycle();
    }
    CHECK((memory.status(0U) & st::CONFIRMED_DTC) != 0U);
    CHECK((memory.status(1U) & st::TEST_FAILED_SINCE_LAST_CLEAR) == 0U);
    CHECK((memory.status(2U) & st::TEST_FAILED_SINCE_LAST_CLEAR) == 0U);
    // DEVICE_FAULT reset: the fallback as well, the degraded DTC stays the other monitor's
    rl::clear(record);
    sr::Report device;
    setStart(device, startAfter(record, false, false, rl::CAUSE_EVENT | EVENT_DEVICE_FAULT));
    Memory full;
    for (int i = 0; i < 3; ++i)
    {
        device.update(&full.manager);
        full.cycle();
    }
    CHECK((full.status(OTHER) & st::CONFIRMED_DTC) != 0U);
    CHECK(full.status(DEGRADED) == st::INITIAL);
    CHECK((full.status(DEADMAN) & st::TEST_FAILED_SINCE_LAST_CLEAR) == 0U);
}

void testControlDtcSettingAtTheStart()
{
    rl::Record record{};
    rl::clear(record);
    sr::Report report;
    setStart(report, startAfter(record, true, false, rl::CAUSE_NONE));
    Memory memory;
    memory.manager.setDtcSetting(false); // 85 off: reports are discarded
    for (int i = 0; i < 3; ++i)
    {
        report.update(&memory.manager);
        memory.cycle();
    }
    CHECK((memory.status(WDG) & st::TEST_FAILED_SINCE_LAST_CLEAR) == 0U);
    memory.manager.setDtcSetting(true); // 85 on: the failure is not lost
    for (int i = 0; i < 3; ++i)
    {
        report.update(&memory.manager);
        memory.cycle();
    }
    CHECK((memory.status(WDG) & st::CONFIRMED_DTC) != 0U);
    CHECK(report.report().pending() == 0U);
}

void testStatusOfTheCycleBefore()
{
    // a DTC that failed in the operation cycle before: the new cycle clears bit 1, the report must not stop early
    Memory memory;
    memory.manager.report(WDG, Result::FAILED);
    memory.cycle();
    memory.manager.report(WDG, Result::PASSED);
    memory.cycle();
    memory.manager.setCycle(0U, true); // the next operation cycle
    CHECK((memory.status(WDG) & st::TEST_FAILED_THIS_OPERATION_CYCLE) == 0U);
    CHECK((memory.status(WDG) & st::TEST_FAILED_SINCE_LAST_CLEAR) != 0U);
    CHECK(memory.occurrence(WDG) == 1U);
    rl::Record record{};
    rl::clear(record);
    sr::Report report;
    setStart(report, startAfter(record, true, false, rl::CAUSE_NONE));
    report.update(&memory.manager);
    memory.cycle();
    CHECK((memory.status(WDG) & st::TEST_FAILED_THIS_OPERATION_CYCLE) != 0U);
    CHECK(memory.occurrence(WDG) == 2U);
}

void testPowerOn()
{
    rl::Record record{};
    rl::clear(record);
    (void)startAfter(record, true, false, rl::CAUSE_NONE);
    rl::Start power{};
    power.powerOn = true;
    sr::Report report;
    setStart(report, rl::onStart(record, power));
    Memory memory;
    report.update(&memory.manager);
    memory.cycle();
    CHECK(report.report().failed() == 0U);
    CHECK(report.report().pending() == 0U);
    for (uint16_t dtc = LOOP; dtc <= DEADMAN; ++dtc)
    {
        CHECK((memory.status(dtc) & (TESTED_BITS | st::TEST_FAILED_SINCE_LAST_CLEAR)) == 0U);
    }
}
// ------------------------------------------------------------------- SYS-196: OccurrenceReport

// Own profile of the occurrence report: the DTC of a foreign safe_off level taken back as the demo describes it
// (reported by the BSW, one report matures, confirmation 1, no enable condition, snapshot of the take-backs: output
// 1 byte, its count 4 bytes, the total 4 bytes), a time debounced one and one with an enable condition.
constexpr uint16_t DID_SAFE_OFF = 0x0140U;
::dem::DataItem const SNAP_SAFE_OFF[]           = {{DID_SAFE_OFF, 9U}};
::dem::SnapshotRecord const SNAPSHOTS_SAFE_OFF[] = {{0x01U, SNAP_SAFE_OFF, 1U}};
::dem::DtcConfig const OCC_DTCS[]               = {
    {"SafeOffTakeBack", 0x1605FAU, 0x20U, 1U, 1U, 40U, 0U, 0U, 0x0U, ::dem::DebounceKind::COUNTER, {1, -1, 1U, 1U, 0, 0},
     {0U, 0U}, SNAPSHOTS_SAFE_OFF, 1U, nullptr, 0U, "safety.safe_off_take_back"},
    {"TimeOccurrence", 0x100010U, 0x20U, 1U, 1U, 40U, 0U, 0U, 0x0U, ::dem::DebounceKind::TIME, {1, -1, 1U, 1U, 0, 0},
     {30U, 30U}, nullptr, 0U, nullptr, 0U, "test.time_occurrence"},
    {"ConditionOccurrence", 0x100011U, 0x20U, 1U, 1U, 40U, 0U, 0U, CONDITION_RUNNING, ::dem::DebounceKind::COUNTER,
     {1, -1, 1U, 1U, 0, 0}, {0U, 0U}, nullptr, 0U, nullptr, 0U, "test.condition_occurrence"},
};
constexpr uint16_t SAFE_OFF = 0U, TIMED = 1U, CONDITIONED = 2U;
constexpr uint16_t OCC_COUNT  = 3U;
constexpr uint16_t OCC_DATA   = 9U;
::dem::Config const OCC_CONFIG = {OCC_DTCS, OCC_COUNT, 3U, true, 1U, 0x1U, 1U, 0x0U, 10U, OCC_DATA};

/** The take-backs as the integration answers 0x0140 (bswstatus::rte::readSafeOffStatus): output, its count, total. */
class TakeBackReader : public ::dem::IDidReader
{
public:
    uint8_t output     = 0xFFU;
    uint32_t ofOutput  = 0U;
    uint32_t total     = 0U;
    unsigned reads     = 0U;

    bool read(uint16_t const did, uint8_t* const data, uint16_t const bytes) override
    {
        ++reads;
        if ((did != DID_SAFE_OFF) || (bytes != OCC_DATA))
        {
            return false;
        }
        data[0] = output;
        for (unsigned i = 0U; i < 4U; ++i)
        {
            data[1U + i] = static_cast<uint8_t>(ofOutput >> (24U - (8U * i)));
            data[5U + i] = static_cast<uint8_t>(total >> (24U - (8U * i)));
        }
        return true;
    }
};

struct OccMemory
{
    ::dem::DtcState states[OCC_COUNT];
    ::dem::Entry entries[3];
    uint8_t data[3U * OCC_DATA];
    uint8_t scratch[OCC_DATA];
    Manager manager;
    Source source;
    TakeBackReader reader;

    OccMemory() : states(), entries(), data(), scratch(), manager(OCC_CONFIG, states, entries, data, scratch), source()
    {
        manager.setDidReader(&reader);
    }

    uint8_t status(uint16_t const dtc) const
    {
        ::dem::DtcInfo info{};
        (void)manager.dtcInfo(dtc, info);
        return info.status;
    }

    uint8_t occurrence(uint16_t const dtc) const
    {
        ::dem::DtcInfo info{};
        (void)manager.dtcInfo(dtc, info);
        return info.occurrence;
    }

    /** One 10-ms cycle of the BSP task (update) and of the fault memory, in the order the tasks may run. */
    void cycle(::dem::OccurrenceReport& report, bool const memoryFirst = false)
    {
        if (memoryFirst)
        {
            manager.mainFunction(source);
            report.update(&manager);
        }
        else
        {
            report.update(&manager);
            manager.mainFunction(source);
        }
    }

    void cycles(::dem::OccurrenceReport& report, int const n)
    {
        for (int i = 0; i < n; ++i)
        {
            cycle(report);
        }
    }
};

constexpr uint8_t TAKEN_AND_PASSED
    = st::TEST_FAILED_THIS_OPERATION_CYCLE | st::PENDING_DTC | st::CONFIRMED_DTC | st::TEST_FAILED_SINCE_LAST_CLEAR;

void testOccurrenceAlone()
{
    ::dem::OccurrenceReport report("safety.mcu_watchdog");
    Recorder memory;
    // nothing occurred: not even a look-up result is reported
    report.update(nullptr);
    report.update(&memory);
    CHECK(memory.lookups == 1U);
    CHECK(report.found());
    CHECK(memory.count == 0U);
    // occurrences before the fault memory runs are not lost
    ::dem::OccurrenceReport early("safety.mcu_watchdog");
    early.occurred();
    early.update(nullptr);
    CHECK(early.covered() == 0U);
    // one occurrence: FAILED until taken over (testFailed in this cycle), then PASSED once
    report.occurred();
    report.update(&memory);
    CHECK(memory.count == 1U);
    CHECK(memory.dtcs[0] == 3U);
    CHECK(memory.log[0] == Result::FAILED);
    CHECK(report.handingOver());
    report.update(&memory);
    CHECK(memory.count == 2U);
    CHECK(memory.log[1] == Result::FAILED);
    // a status left from before (testFailed without this cycle, or this cycle without testFailed) is no hand-over
    memory.statusNow = st::TEST_FAILED | st::CONFIRMED_DTC;
    report.update(&memory);
    CHECK(memory.log[2] == Result::FAILED);
    memory.statusNow = st::TEST_FAILED_THIS_OPERATION_CYCLE | st::CONFIRMED_DTC;
    report.update(&memory);
    CHECK(memory.log[3] == Result::FAILED);
    memory.statusNow = st::TEST_FAILED | st::TEST_FAILED_THIS_OPERATION_CYCLE;
    report.occurred(); // during the hand-over: folded into the next one
    report.update(&memory);
    CHECK(memory.count == 5U);
    CHECK(memory.log[4] == Result::PASSED);
    CHECK(!report.handingOver());
    CHECK(report.handOvers() == 1U);
    CHECK(report.covered() == 1U);
    CHECK(report.occurrences() == 2U);
    // the folded one: the next hand-over
    memory.statusNow = 0U;
    report.update(&memory);
    CHECK(memory.count == 6U);
    CHECK(memory.log[5] == Result::FAILED);
    memory.statusNow = st::TEST_FAILED | st::TEST_FAILED_THIS_OPERATION_CYCLE;
    report.update(&memory);
    CHECK(memory.log[6] == Result::PASSED);
    CHECK(report.covered() == 2U);
    // nothing new: nothing reported, no new look-up
    report.update(&memory);
    report.update(&memory);
    CHECK(memory.count == 7U);
    CHECK(memory.lookups == 1U);
    // without a DTC of the name: counted, never reported
    ::dem::OccurrenceReport missing("safety.missing");
    Recorder other;
    missing.occurred();
    missing.update(&other);
    missing.update(&other);
    CHECK(!missing.found());
    CHECK(other.count == 0U);
    CHECK(missing.occurrences() == 1U);
    // a new fault memory gets the hand-over in progress again
    ::dem::OccurrenceReport moved("safety.mcu_watchdog");
    Recorder first;
    moved.occurred();
    moved.update(&first);
    CHECK(first.count == 1U);
    Recorder second;
    moved.update(&second);
    CHECK(second.lookups == 1U);
    CHECK(second.count == 1U);
    CHECK(second.log[0] == Result::FAILED);
}

void testOccurrenceInTheFaultMemory()
{
    OccMemory memory;
    memory.manager.start();
    ::dem::OccurrenceReport report("safety.safe_off_take_back");
    // counter-check: no occurrence (a commanded level is no take-back) - the DTC stays untouched
    memory.cycles(report, 5);
    CHECK(memory.status(SAFE_OFF) == st::INITIAL);
    CHECK(memory.reader.reads == 0U);
    // the first take-back of output 3 (its count 1, total 1)
    memory.reader.output   = 3U;
    memory.reader.ofOutput = 1U;
    memory.reader.total    = 1U;
    report.occurred();
    memory.cycles(report, 3);
    CHECK(memory.status(SAFE_OFF) == TAKEN_AND_PASSED); // confirmed and stored, testFailed again 0
    CHECK(memory.occurrence(SAFE_OFF) == 1U);
    CHECK(report.handOvers() == 1U);
    uint8_t out[OCC_DATA] = {};
    CHECK(memory.manager.readSnapshot(SAFE_OFF, 0x01U, out, sizeof(out)) == 9);
    CHECK(out[0] == 3U);
    CHECK(out[4] == 1U);
    CHECK(out[8] == 1U);
    // further take-backs (the hold reports at most once per second and output): one more failure each, the
    // snapshot stays the one of the first confirmation
    memory.reader.output   = 5U;
    memory.reader.ofOutput = 7U;
    memory.reader.total    = 8U;
    report.occurred();
    memory.cycles(report, 3);
    CHECK(memory.occurrence(SAFE_OFF) == 2U);
    CHECK(memory.status(SAFE_OFF) == TAKEN_AND_PASSED);
    CHECK(memory.manager.readSnapshot(SAFE_OFF, 0x01U, out, sizeof(out)) == 9);
    CHECK(out[0] == 3U);
    // several between two cycles: one failure
    report.occurred();
    report.occurred();
    report.occurred();
    memory.cycles(report, 3);
    CHECK(memory.occurrence(SAFE_OFF) == 3U);
    CHECK(report.covered() == 5U);
    // the reset: the persistent state into a new fault memory (NvM blocks DemEvents/DemEntries) - the DTC stays
    uint8_t events[::dem::eventsBlockBytes(OCC_COUNT)]           = {};
    uint8_t entries[::dem::entriesBlockBytes(3U, OCC_DATA)]      = {};
    memory.manager.stop();
    memory.manager.serializeEvents(events, sizeof(events));
    memory.manager.serializeEntries(entries, sizeof(entries));
    OccMemory after;
    CHECK(after.manager.load(events, sizeof(events), entries, sizeof(entries)));
    after.manager.start();
    ::dem::OccurrenceReport fresh("safety.safe_off_take_back");
    after.cycles(fresh, 3); // nothing taken back in the new cycle: the DTC keeps confirmed and its snapshot
    CHECK((after.status(SAFE_OFF) & st::CONFIRMED_DTC) != 0U);
    CHECK((after.status(SAFE_OFF) & st::TEST_FAILED) == 0U);
    CHECK(after.occurrence(SAFE_OFF) == 3U);
    CHECK(after.manager.readSnapshot(SAFE_OFF, 0x01U, out, sizeof(out)) == 9);
    CHECK(out[0] == 3U);
    // 14 FFFFFF: cleared, and it stays cleared while nothing new is taken back
    CHECK(after.manager.clear(::dem::GROUP_ALL_DTCS));
    after.cycles(fresh, 5);
    CHECK(after.status(SAFE_OFF) == st::INITIAL);
    CHECK(after.manager.readSnapshot(SAFE_OFF, 0x01U, out, sizeof(out)) == 0);
    // the next take-back after the clear: the DTC again, with the snapshot of then
    after.reader.output   = 4U;
    after.reader.ofOutput = 1U;
    after.reader.total    = 1U;
    fresh.occurred();
    after.cycles(fresh, 3);
    CHECK((after.status(SAFE_OFF) & st::CONFIRMED_DTC) != 0U);
    CHECK(after.occurrence(SAFE_OFF) == 1U);
    CHECK(after.manager.readSnapshot(SAFE_OFF, 0x01U, out, sizeof(out)) == 9);
    CHECK(out[0] == 4U);
}

void testOccurrenceTaskOrder()
{
    // the fault memory's cycle before the BSP task's: the same result, one cycle later
    OccMemory memory;
    memory.manager.start();
    ::dem::OccurrenceReport report("safety.safe_off_take_back");
    report.occurred();
    for (int i = 0; i < 4; ++i)
    {
        memory.cycle(report, true);
    }
    CHECK(memory.status(SAFE_OFF) == TAKEN_AND_PASSED);
    CHECK(memory.occurrence(SAFE_OFF) == 1U);
    // a take-back right after the PASSED of the one before (before the fault memory took the PASSED): folded, the DTC
    // ends passed and confirmed, nothing stays testFailed
    report.occurred();
    memory.cycle(report, true);
    report.occurred();
    for (int i = 0; i < 6; ++i)
    {
        memory.cycle(report, (i % 2) == 0);
    }
    CHECK(memory.status(SAFE_OFF) == TAKEN_AND_PASSED);
    CHECK(report.covered() == report.occurrences());
    CHECK(!report.handingOver());
}

void testOccurrenceNotTakenYet()
{
    OccMemory memory;
    memory.manager.start();
    // time debouncing (30 ms): a reported FAILED matures at once (status source), the hand-over works the same
    ::dem::OccurrenceReport timed("test.time_occurrence");
    timed.occurred();
    memory.cycles(timed, 3);
    CHECK(!timed.handingOver());
    CHECK((memory.status(TIMED) & st::CONFIRMED_DTC) != 0U);
    // enable condition not fulfilled: FAILED stays reported, taken over once it holds
    ::dem::OccurrenceReport conditioned("test.condition_occurrence");
    memory.source.running = false;
    conditioned.occurred();
    memory.cycles(conditioned, 5);
    CHECK(conditioned.handingOver());
    CHECK(memory.status(CONDITIONED) == st::INITIAL);
    memory.source.running = true;
    memory.cycles(conditioned, 3);
    CHECK(!conditioned.handingOver());
    CHECK((memory.status(CONDITIONED) & st::CONFIRMED_DTC) != 0U);
    // 85 off: discarded meanwhile, not lost
    ::dem::OccurrenceReport report("safety.safe_off_take_back");
    memory.manager.setDtcSetting(false);
    report.occurred();
    memory.cycles(report, 5);
    CHECK(memory.status(SAFE_OFF) == st::INITIAL);
    memory.manager.setDtcSetting(true);
    memory.cycles(report, 3);
    CHECK((memory.status(SAFE_OFF) & st::CONFIRMED_DTC) != 0U);
    CHECK((memory.status(SAFE_OFF) & st::TEST_FAILED) == 0U);
}

void testOccurrenceStaleTestFailed()
{
    // a status saved with testFailed set (reset during a hand-over): the new cycle clears bit 1, so the next take-back
    // is not taken for handed over before the fault memory saw it
    OccMemory memory;
    memory.manager.start();
    memory.manager.report(SAFE_OFF, Result::FAILED);
    memory.manager.mainFunction(memory.source);
    memory.manager.setCycle(0U, true); // the next operation cycle
    CHECK((memory.status(SAFE_OFF) & st::TEST_FAILED) != 0U);
    CHECK((memory.status(SAFE_OFF) & st::TEST_FAILED_THIS_OPERATION_CYCLE) == 0U);
    ::dem::OccurrenceReport report("safety.safe_off_take_back");
    report.occurred();
    report.update(&memory.manager);
    report.update(&memory.manager); // the fault memory has not run: still FAILED, no PASSED yet
    CHECK(report.handingOver());
    memory.cycles(report, 3);
    CHECK(!report.handingOver());
    CHECK((memory.status(SAFE_OFF) & st::TEST_FAILED_THIS_OPERATION_CYCLE) != 0U);
    CHECK((memory.status(SAFE_OFF) & st::TEST_FAILED) == 0U);
}
} // namespace

int main()
{
    testTable();
    testFailedOf();
    testStartReportAlone();
    testFallbackAlone();
    testWatchdogResetWithEnableCondition();
    testFaultExceptionWithTimeDebouncing();
    testEventResetsInARow();
    testFallbackInTheFaultMemory();
    testControlDtcSettingAtTheStart();
    testStatusOfTheCycleBefore();
    testPowerOn();
    testOccurrenceAlone(); // SYS-196
    testOccurrenceInTheFaultMemory();
    testOccurrenceTaskOrder();
    testOccurrenceNotTakenYet();
    testOccurrenceStaleTestFailed();
    std::printf("%u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
