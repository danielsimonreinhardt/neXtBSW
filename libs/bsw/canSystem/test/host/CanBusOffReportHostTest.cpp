/* Host test of CAN bus-off as a source of the fault memory (COM-46) and of the generic reporting side of a BSW
 * monitor (dem/EventReporter.h, NVM-08). Own test profile, not the eval board: two CAN buses VEH (index 0) and BODY
 * (index 1) whose DTCs "can.busoff.VEH"/"can.busoff.BODY" run in the real fault memory core (dem::Manager) with the
 * debouncing of the standard description (failed_threshold 1, passed_threshold -1), and a third bus without a DTC.
 *
 * Fault injection: bus-off on one bus only (the other must stay clean), a bus-off that is over before the next
 * reading (latched flag only), a series of bus-offs with the recovery pause, a bus-off before the fault memory runs,
 * a fault memory that is replaced (new start), the bus stopped (state unknown: no result), a bus without a DTC,
 * ControlDTCSetting (85) off and on again while the bus goes bus-off and recovers (COM-48).
 *
 * Build and run: libs/bsw/canSystem/test/test_can_bus_off_report.py (also with deliberate faults).
 */

#include "can/CanBusOffReport.h"
#include "can/CanBusState.h"

#include <dem/Dem.h>
#include <dem/EventReporter.h>

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

using ::can::CanBusOffReport;
using ::can::CanBusState;
using ::dem::Result;
namespace st = ::dem::status;
using Manager = ::dem::Manager<TestLock>;

// ---- test profile: the bus-off DTCs of two buses as the standard description writes them (reported by the BSW)
::dem::DtcConfig const DTCS[] = {
    {"CanBusOffVEH", 0x160300U, 0x40U, 2U, 1U, 40U, 0U, 0U, 0x0U, ::dem::DebounceKind::COUNTER, {1, -1, 1U, 1U, 0, 0},
     {0U, 0U}, nullptr, 0U, nullptr, 0U, "can.busoff.VEH"},
    {"CanBusOffBODY", 0x160301U, 0x40U, 2U, 1U, 40U, 0U, 0U, 0x0U, ::dem::DebounceKind::COUNTER, {1, -1, 1U, 1U, 0, 0},
     {0U, 0U}, nullptr, 0U, nullptr, 0U, "can.busoff.BODY"},
};
constexpr uint16_t DTC_COUNT = 2U;
::dem::Config const CONFIG   = {DTCS, DTC_COUNT, 2U, true, 1U, 0x1U, 0U, 0x0U, 10U, 1U};

class NoSource : public ::dem::ISource
{
public:
    Result sample(uint16_t) override { return Result::NONE; }

    bool condition(uint8_t) override { return true; }

    uint8_t cycleState(uint8_t) override { return CYCLE_AUTOMATIC; }
};

/** One fault memory with its storage, as the integration (ecu/application DemRte.h) owns it. */
struct Memory
{
    ::dem::DtcState states[DTC_COUNT];
    ::dem::Entry entries[2];
    uint8_t data[2];
    uint8_t scratch[1];
    Manager manager;
    NoSource source;

    Memory() : states(), entries(), data(), scratch(), manager(CONFIG, states, entries, data, scratch), source()
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

/** Records what a monitor reports (EventReporter alone). */
class Recorder : public ::dem::IFaultMemory
{
public:
    Result log[16];
    uint16_t dtcs[16];
    unsigned count   = 0U;
    unsigned lookups = 0U;
    char const* known = "can.busoff.VEH";

    uint16_t dtcCount() const override { return 1U; }

    ::dem::DtcConfig const& dtcConfig(uint16_t) const override { return DTCS[0]; }

    bool dtcInfo(uint16_t, ::dem::DtcInfo&) const override { return false; }

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
        return (std::strcmp(name, known) == 0) ? 7 : -1;
    }

    void report(uint16_t const dtc, Result const result) override
    {
        if (count < 16U)
        {
            dtcs[count] = dtc;
            log[count]  = result;
        }
        ++count;
    }
};

// ------------------------------------------------------------------------------------ EventReporter alone

void testReporterReportsEveryCall()
{
    Recorder memory;
    ::dem::EventReporter reporter("can.busoff.VEH");
    reporter.update(Result::PASSED, false, &memory);
    CHECK(memory.lookups == 1U);
    CHECK(reporter.dtc() == 7);
    CHECK(memory.count == 1U);
    CHECK(memory.dtcs[0] == 7U);
    CHECK(memory.log[0] == Result::PASSED);
    reporter.update(Result::PASSED, false, &memory);
    reporter.update(Result::PASSED, false, &memory);
    CHECK(memory.count == 3U);       // COM-48: the level at every call, a discarded report (85 off) is made good
    CHECK(memory.log[1] == Result::PASSED);
    CHECK(memory.log[2] == Result::PASSED);
    CHECK(memory.lookups == 1U);     // the DTC is looked up once per fault memory
    reporter.update(Result::FAILED, false, &memory);
    CHECK(memory.count == 4U);
    CHECK(memory.log[3] == Result::FAILED);
    reporter.update(Result::NONE, false, &memory);
    CHECK(memory.count == 4U);       // no result: nothing reported, the last level stays
    reporter.update(Result::FAILED, false, &memory);
    CHECK(memory.count == 5U);
    CHECK(memory.log[4] == Result::FAILED);
    reporter.update(Result::PASSED, false, &memory);
    CHECK(memory.count == 6U);
    CHECK(memory.log[5] == Result::PASSED);
    CHECK(reporter.reported() == Result::PASSED);
    CHECK(memory.lookups == 1U);
}

void testReporterBeforeTheFaultMemory()
{
    Recorder memory;
    ::dem::EventReporter reporter("can.busoff.VEH");
    // the monitor runs before the fault memory: a failure then is not lost
    reporter.update(Result::PASSED, false, nullptr);
    reporter.update(Result::FAILED, false, nullptr);
    reporter.update(Result::PASSED, false, nullptr);
    CHECK(memory.count == 0U);
    CHECK(reporter.dtc() == -1);
    reporter.update(Result::PASSED, false, &memory);
    CHECK(memory.count == 2U);
    CHECK(memory.log[0] == Result::FAILED);
    CHECK(memory.log[1] == Result::PASSED);
    // nothing failed before: only the level
    Recorder clean;
    ::dem::EventReporter quiet("can.busoff.VEH");
    quiet.update(Result::PASSED, false, nullptr);
    quiet.update(Result::PASSED, false, &clean);
    CHECK(clean.count == 1U);
    CHECK(clean.log[0] == Result::PASSED);
}

void testReporterMissedFailureAndNewMemory()
{
    Recorder memory;
    ::dem::EventReporter reporter("can.busoff.VEH");
    reporter.update(Result::PASSED, false, &memory);
    reporter.update(Result::PASSED, true, &memory); // failed and passed again between two calls
    CHECK(memory.count == 3U);
    CHECK(memory.log[1] == Result::FAILED);
    CHECK(memory.log[2] == Result::PASSED);
    // failing now and a failure since the last call: one FAILED
    reporter.update(Result::FAILED, true, &memory);
    CHECK(memory.count == 4U);
    CHECK(memory.log[3] == Result::FAILED);
    // a new fault memory (restart of the integration): looked up again, the level reported again
    Recorder second;
    reporter.update(Result::FAILED, false, &second);
    CHECK(second.lookups == 1U);
    CHECK(second.count == 1U);
    CHECK(second.log[0] == Result::FAILED);
    // FAILED went to the old memory only: a failure since the last call reaches the next new one before the level
    Recorder third;
    reporter.update(Result::PASSED, true, &third);
    CHECK(third.count == 2U);
    CHECK(third.log[0] == Result::FAILED);
    CHECK(third.log[1] == Result::PASSED);
}

void testReporterWithoutDtc()
{
    Recorder memory;
    ::dem::EventReporter reporter("can.busoff.OTHER");
    reporter.update(Result::FAILED, false, &memory);
    reporter.update(Result::PASSED, true, &memory);
    reporter.update(Result::FAILED, false, &memory);
    CHECK(memory.count == 0U);   // the project describes no DTC of this name
    CHECK(memory.lookups == 1U); // and it is not looked up again
    CHECK(reporter.dtc() == -1);
}

// --------------------------------------------------------------- bus-off of two buses in the fault memory

struct Bus
{
    ::can::CanBusMonitor monitor;
    CanBusOffReport report;

    explicit Bus(char const* const event) : monitor(), report(event) {}

    /** One supervision cycle as the CAN system of the port runs it: read the state, feed the monitor and the report. */
    void cycle(CanBusState const state, bool const latchedBusOff, ::dem::IFaultMemory* const memory)
    {
        (void)monitor.update(state, 10U);
        report.update(state, latchedBusOff, memory);
    }
};

void testResultOf()
{
    CHECK(CanBusOffReport::resultOf(CanBusState::UNKNOWN) == Result::NONE);
    CHECK(CanBusOffReport::resultOf(CanBusState::ERROR_ACTIVE) == Result::PASSED);
    CHECK(CanBusOffReport::resultOf(CanBusState::ERROR_PASSIVE) == Result::PASSED);
    CHECK(CanBusOffReport::resultOf(CanBusState::BUS_OFF) == Result::FAILED);
}

void testBusOffOnOneBus()
{
    Memory memory;
    Bus veh("can.busoff.VEH");
    Bus body("can.busoff.BODY");
    Bus third("can.busoff.LIN"); // a bus without a DTC in this project
    for (int i = 0; i < 5; ++i)
    {
        veh.cycle(CanBusState::ERROR_ACTIVE, false, &memory.manager);
        body.cycle(CanBusState::ERROR_ACTIVE, false, &memory.manager);
        third.cycle(CanBusState::ERROR_ACTIVE, false, &memory.manager);
        memory.cycle();
    }
    CHECK(veh.report.reporter().dtc() == 0);
    CHECK(body.report.reporter().dtc() == 1);
    CHECK(third.report.reporter().dtc() == -1);
    CHECK((memory.status(0U) & st::TEST_FAILED) == 0U);
    CHECK((memory.status(0U) & st::TEST_NOT_COMPLETED_THIS_OPERATION_CYCLE) == 0U); // tested: PASSED
    CHECK((memory.status(1U) & st::TEST_FAILED) == 0U);

    // fault injection: BODY goes bus-off (TEC > 255), VEH stays on the bus
    body.cycle(CanBusState::ERROR_PASSIVE, false, &memory.manager);
    memory.cycle();
    CHECK((memory.status(1U) & st::TEST_FAILED) == 0U); // error passive is no bus-off
    body.cycle(CanBusState::BUS_OFF, true, &memory.manager);
    veh.cycle(CanBusState::ERROR_ACTIVE, false, &memory.manager);
    memory.cycle();
    CHECK((memory.status(1U) & st::TEST_FAILED) != 0U);
    CHECK((memory.status(1U) & st::CONFIRMED_DTC) != 0U);
    CHECK((memory.status(1U) & st::PENDING_DTC) != 0U);
    CHECK((memory.status(0U) & (st::TEST_FAILED | st::CONFIRMED_DTC | st::PENDING_DTC)) == 0U);
    // still bus off (recovery pause of COM-35): stays failed, one occurrence
    for (int i = 0; i < 10; ++i)
    {
        body.cycle(CanBusState::BUS_OFF, false, &memory.manager);
        memory.cycle();
    }
    CHECK((memory.status(1U) & st::TEST_FAILED) != 0U);
    CHECK(memory.occurrence(1U) == 1U);
    // back on the bus: test passes, confirmed and pending stay for this operation cycle
    body.cycle(CanBusState::ERROR_ACTIVE, false, &memory.manager);
    memory.cycle();
    CHECK((memory.status(1U) & st::TEST_FAILED) == 0U);
    CHECK((memory.status(1U) & st::CONFIRMED_DTC) != 0U);
    CHECK((memory.status(0U) & st::CONFIRMED_DTC) == 0U);
}

void testShortBusOffAndSeries()
{
    Memory memory;
    Bus veh("can.busoff.VEH");
    veh.cycle(CanBusState::ERROR_ACTIVE, false, &memory.manager);
    memory.cycle();
    // a bus-off that was over before this reading (automatic recovery within one 10 ms cycle): BOFFINT only
    veh.cycle(CanBusState::ERROR_ACTIVE, true, &memory.manager);
    memory.cycle();
    CHECK((memory.status(0U) & st::TEST_FAILED_THIS_OPERATION_CYCLE) != 0U);
    CHECK((memory.status(0U) & st::CONFIRMED_DTC) != 0U);
    CHECK(memory.occurrence(0U) == 1U);
    memory.cycle();
    CHECK((memory.status(0U) & st::TEST_FAILED) == 0U); // the level afterwards: passed
    // a series: bus off, back, bus off again - every entry is one occurrence
    for (int i = 0; i < 3; ++i)
    {
        veh.cycle(CanBusState::BUS_OFF, true, &memory.manager);
        memory.cycle();
        veh.cycle(CanBusState::ERROR_PASSIVE, false, &memory.manager);
        memory.cycle();
    }
    CHECK(memory.occurrence(0U) == 4U);
    CHECK((memory.status(0U) & st::TEST_FAILED) == 0U);
}

void testBusStoppedAndBeforeTheMemory()
{
    Memory memory;
    Bus veh("can.busoff.VEH");
    // bus-off at the start, before the fault memory is registered: reported once it runs
    veh.cycle(CanBusState::BUS_OFF, true, nullptr);
    veh.cycle(CanBusState::ERROR_ACTIVE, false, nullptr);
    CHECK((memory.status(0U) & st::TEST_FAILED_THIS_OPERATION_CYCLE) == 0U);
    veh.cycle(CanBusState::ERROR_ACTIVE, false, &memory.manager);
    memory.cycle();
    CHECK((memory.status(0U) & st::CONFIRMED_DTC) != 0U);
    memory.cycle();
    CHECK((memory.status(0U) & st::TEST_FAILED) == 0U);
    // the bus is stopped (shutdown): no result, the status stays as it is
    uint8_t const before = memory.status(0U);
    for (int i = 0; i < 5; ++i)
    {
        veh.cycle(CanBusState::UNKNOWN, false, &memory.manager);
        memory.cycle();
    }
    CHECK(memory.status(0U) == before);
    // a new start (new fault memory, cleared): the level is reported again
    Memory next;
    veh.cycle(CanBusState::ERROR_ACTIVE, false, &next.manager);
    next.cycle();
    CHECK((next.status(0U) & st::TEST_NOT_COMPLETED_THIS_OPERATION_CYCLE) == 0U);
    CHECK((next.status(0U) & st::TEST_FAILED) == 0U);
}
void testControlDtcSetting()
{
    // COM-48 (finding of COM-47): 85 off discards every report (COM-43); after 85 on the fault memory must hold the
    // level of the bus now, not the one of before 85 off
    Memory memory;
    Bus veh("can.busoff.VEH");
    Bus body("can.busoff.BODY");
    veh.cycle(CanBusState::ERROR_ACTIVE, false, &memory.manager);
    body.cycle(CanBusState::ERROR_ACTIVE, false, &memory.manager);
    memory.cycle();
    // VEH goes bus-off, then 85 off (preprogramming step); VEH recovers while it is off
    veh.cycle(CanBusState::BUS_OFF, true, &memory.manager);
    memory.cycle();
    CHECK((memory.status(0U) & st::TEST_FAILED) != 0U);
    memory.manager.setDtcSetting(false);
    for (int i = 0; i < 3; ++i)
    {
        veh.cycle(CanBusState::ERROR_ACTIVE, false, &memory.manager);
        body.cycle(CanBusState::ERROR_ACTIVE, false, &memory.manager);
        memory.cycle();
    }
    // BODY goes bus-off only while 85 is off and recovers before 85 on: never a DTC (COM-43)
    body.cycle(CanBusState::BUS_OFF, true, &memory.manager);
    memory.cycle();
    body.cycle(CanBusState::ERROR_ACTIVE, false, &memory.manager);
    memory.cycle();
    CHECK((memory.status(0U) & st::TEST_FAILED) != 0U); // no status change while off
    CHECK((memory.status(1U) & (st::TEST_FAILED | st::TEST_FAILED_THIS_OPERATION_CYCLE)) == 0U);
    // clear while off: 0x50; 85 on
    CHECK(memory.manager.clear(::dem::GROUP_ALL_DTCS));
    memory.manager.setDtcSetting(true);
    for (int i = 0; i < 5; ++i)
    {
        veh.cycle(CanBusState::ERROR_ACTIVE, false, &memory.manager);
        body.cycle(CanBusState::ERROR_ACTIVE, false, &memory.manager);
        memory.cycle();
        CHECK((memory.status(0U) & st::TEST_FAILED_THIS_OPERATION_CYCLE) == 0U); // before COM-48: 0x2F again
        CHECK((memory.status(1U) & st::TEST_FAILED_THIS_OPERATION_CYCLE) == 0U);
    }
    CHECK(memory.status(0U) == 0x00U); // tested and passed after the clear: the current level of the bus
    CHECK(memory.status(1U) == 0x00U);
    CHECK(memory.occurrence(0U) == 0U);

    // a bus-off that lasts beyond 85 on is recorded at once
    memory.manager.setDtcSetting(false);
    veh.cycle(CanBusState::BUS_OFF, true, &memory.manager);
    memory.cycle();
    veh.cycle(CanBusState::BUS_OFF, false, &memory.manager);
    memory.cycle();
    CHECK((memory.status(0U) & st::TEST_FAILED) == 0U);
    memory.manager.setDtcSetting(true);
    veh.cycle(CanBusState::BUS_OFF, false, &memory.manager);
    memory.cycle();
    CHECK((memory.status(0U) & st::TEST_FAILED) != 0U);
    CHECK((memory.status(0U) & st::CONFIRMED_DTC) != 0U);
    CHECK(memory.occurrence(0U) == 1U);
    veh.cycle(CanBusState::ERROR_ACTIVE, false, &memory.manager);
    memory.cycle();
    CHECK((memory.status(0U) & st::TEST_FAILED) == 0U);
}
} // namespace

int main()
{
    testReporterReportsEveryCall();
    testReporterBeforeTheFaultMemory();
    testReporterMissedFailureAndNewMemory();
    testReporterWithoutDtc();
    testResultOf();
    testBusOffOnOneBus();
    testShortBusOffAndSeries();
    testBusStoppedAndBeforeTheMemory();
    testControlDtcSetting();
    std::printf("%u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
