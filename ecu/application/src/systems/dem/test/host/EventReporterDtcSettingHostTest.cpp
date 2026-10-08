/* COM-48: host SIL test of the reporters of the BSW events against ControlDTCSetting (85) - the real fault memory core
 * (dem::Manager, libs/bsw/dem), the real reporting side dem::EventReporter, the real CAN bus-off report
 * (can/CanBusOffReport.h, COM-46) and the real reporters of the safe supervisor (dem/SafetyFaultReport.h: NVM-08
 * safety.device_fault, SYS-136 safety.hw_shutoff) with test doubles for the supervisor's read-only readers.
 *
 * Finding of COM-47: the fault memory discards report() while 85 is off (COM-43) and EventReporter reported only on a
 * change, so after 85 on the level of before 85 off stayed in place. The test plays the context of the fault memory
 * like DemCycle in RteSystem.cpp: every cycle the reporters first, then mainFunction(); the CAN system feeds its report
 * in the same cycle (its own task on the target, the order does not matter for the result).
 *
 * Own test profile, not the eval board: two CAN buses VEH/BODY with a bus-off DTC each, the two safety DTCs, and a
 * sampled DTC without a reporter (must stay untouched), debouncing of the standard description (1/-1).
 *
 * Build and run: ecu/application/src/systems/dem/test/test_event_reporter_dtc_setting.py (also with deliberate faults).
 */

#include "can/CanBusOffReport.h"
#include "can/CanBusState.h"
#include "dem/SafetyFaultReport.h"

#include <dem/Dem.h>
#include <dem/EventReporter.h>

#include <cstdio>

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

constexpr uint16_t VEH       = 0U;
constexpr uint16_t BODY      = 1U;
constexpr uint16_t DEGRADED  = 2U;
constexpr uint16_t SHUTOFF   = 3U;
constexpr uint16_t SAMPLED   = 4U;
constexpr uint16_t DTC_COUNT = 5U;

::dem::DtcConfig const DTCS[DTC_COUNT] = {
    {"CanBusOffVEH", 0x160300U, 0x40U, 2U, 1U, 40U, 0U, 0U, 0x0U, ::dem::DebounceKind::COUNTER, {1, -1, 1U, 1U, 0, 0},
     {0U, 0U}, nullptr, 0U, nullptr, 0U, "can.busoff.VEH"},
    {"CanBusOffBODY", 0x160301U, 0x40U, 2U, 1U, 40U, 0U, 0U, 0x0U, ::dem::DebounceKind::COUNTER, {1, -1, 1U, 1U, 0, 0},
     {0U, 0U}, nullptr, 0U, nullptr, 0U, "can.busoff.BODY"},
    {"SafetyDeviceFault", 0x160505U, 0x40U, 1U, 1U, 40U, 0U, 0U, 0x0U, ::dem::DebounceKind::COUNTER,
     {1, -1, 1U, 1U, 0, 0}, {0U, 0U}, nullptr, 0U, nullptr, 0U, ::safety::faultmemory::DEVICE_FAULT_EVENT},
    {"SafetyHwShutoff", 0x1605FEU, 0x40U, 1U, 1U, 40U, 0U, 0U, 0x0U, ::dem::DebounceKind::COUNTER,
     {1, -1, 1U, 1U, 0, 0}, {0U, 0U}, nullptr, 0U, nullptr, 0U, ::safety::faultmemory::HW_SHUTOFF_EVENT},
    {"Sampled", 0x160700U, 0x40U, 3U, 1U, 40U, 0U, 0U, 0x0U, ::dem::DebounceKind::COUNTER, {1, -1, 1U, 1U, 0, 0},
     {0U, 0U}, nullptr, 0U, nullptr, 0U, nullptr},
};
::dem::Config const CONFIG = {DTCS, DTC_COUNT, 4U, true, 1U, 0x1U, 0U, 0x0U, 10U, 1U};

class Source : public ::dem::ISource
{
public:
    Result sample(uint16_t) override { return Result::NONE; } // the sampled DTC gets no result: stays untested

    bool condition(uint8_t) override { return true; }

    uint8_t cycleState(uint8_t) override { return CYCLE_AUTOMATIC; }
};

/** The supervisor's read-only state as the readers return it (safety::degraded::rte::readGroups, hwShutoff::read). */
struct Supervisor
{
    uint32_t groups    = 0U;
    bool groupsValid   = true;
    uint32_t context   = 0U;
    bool contextValid  = true;
};

/** Fault memory, the reporters of the integration and of the CAN system, one cycle like DemCycle. */
struct Ecu
{
    ::dem::DtcState states[DTC_COUNT];
    ::dem::Entry entries[4];
    uint8_t data[4];
    uint8_t scratch[1];
    Manager manager;
    Source source;
    CanBusOffReport veh;
    CanBusOffReport body;
    ::dem::EventReporter deviceFault;
    ::dem::EventReporter hwShutoff;
    Supervisor supervisor;
    CanBusState vehState  = CanBusState::ERROR_ACTIVE;
    CanBusState bodyState = CanBusState::ERROR_ACTIVE;

    Ecu()
    : states()
    , entries()
    , data()
    , scratch()
    , manager(CONFIG, states, entries, data, scratch)
    , source()
    , veh("can.busoff.VEH")
    , body("can.busoff.BODY")
    , deviceFault(::safety::faultmemory::DEVICE_FAULT_EVENT)
    , hwShutoff(::safety::faultmemory::HW_SHUTOFF_EVENT)
    , supervisor()
    {
        manager.start();
    }

    /** One cycle: CAN supervision (\p vehLatched/\p bodyLatched: BOFFINT), the supervisor's reporters, the memory. */
    void cycle(bool const vehLatched = false, bool const bodyLatched = false)
    {
        veh.update(vehState, vehLatched, &manager);
        body.update(bodyState, bodyLatched, &manager);
        Supervisor const& s = supervisor;
        ::safety::faultmemory::reportDegraded(
            deviceFault,
            [&s](uint32_t& groups)
            {
                groups = s.groups;
                return s.groupsValid;
            },
            &manager);
        ::safety::faultmemory::reportHwShutoff(
            hwShutoff,
            [&s](uint32_t& context)
            {
                context = s.context;
                return s.contextValid;
            },
            &manager);
        manager.mainFunction(source);
    }

    void cycles(int const n)
    {
        for (int i = 0; i < n; ++i)
        {
            cycle();
        }
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

    bool failed(uint16_t const dtc) const { return (status(dtc) & st::TEST_FAILED) != 0U; }

    bool failedThisCycle(uint16_t const dtc) const
    {
        return (status(dtc) & st::TEST_FAILED_THIS_OPERATION_CYCLE) != 0U;
    }
};

// ------------------------------------------------------------ 1. bus-off before 85 off, recovered while off

void testBusOffRecoveredWhileOff()
{
    Ecu ecu;
    ecu.cycles(3);
    CHECK(ecu.status(VEH) == 0x00U);
    CHECK(ecu.status(DEGRADED) == 0x00U);
    CHECK(ecu.status(SHUTOFF) == 0x00U);
    CHECK(ecu.status(SAMPLED) == 0x50U); // untested, untouched by the reporters

    ecu.vehState = CanBusState::BUS_OFF;
    ecu.cycle(true);
    CHECK(ecu.failed(VEH));
    CHECK(ecu.occurrence(VEH) == 1U);

    // 85 off (preprogramming step), the bus recovers while it is off, 14 FF FF FF while off
    ecu.manager.setDtcSetting(false);
    ecu.vehState = CanBusState::ERROR_ACTIVE;
    ecu.cycles(5);
    CHECK(ecu.failed(VEH)); // no status change while off (D.2.1)
    CHECK(ecu.manager.clear(::dem::GROUP_ALL_DTCS));
    ecu.cycles(2);
    CHECK(ecu.status(VEH) == 0x50U);

    // 85 on: the current level of the bus (passed), not the one of before 85 off (failed)
    ecu.manager.setDtcSetting(true);
    for (int i = 0; i < 10; ++i)
    {
        ecu.cycle();
        CHECK(!ecu.failedThisCycle(VEH)); // before COM-48: 0x2F again in every cycle after the clear
    }
    CHECK(ecu.status(VEH) == 0x00U);
    CHECK(ecu.occurrence(VEH) == 0U);
    CHECK(ecu.status(BODY) == 0x00U);
    CHECK(ecu.status(SAMPLED) == 0x50U);
}

// ------------------------------------------------------------ 2. bus-off only while 85 is off

void testBusOffOnlyWhileOff()
{
    Ecu ecu;
    ecu.cycles(2);
    ecu.manager.setDtcSetting(false);
    ecu.bodyState = CanBusState::BUS_OFF;
    ecu.cycle(false, true);
    ecu.cycles(3);
    ecu.bodyState = CanBusState::ERROR_ACTIVE;
    ecu.cycles(2);
    ecu.manager.setDtcSetting(true);
    ecu.cycles(5);
    CHECK(ecu.status(BODY) == 0x00U); // over before 85 on: never a DTC (COM-43)
    CHECK(ecu.occurrence(BODY) == 0U);

    // bus-off that begins while off and lasts beyond 85 on: recorded in the first cycle after 85 on
    ecu.manager.setDtcSetting(false);
    ecu.bodyState = CanBusState::BUS_OFF;
    ecu.cycle(false, true);
    ecu.cycle();
    CHECK(!ecu.failed(BODY));
    ecu.manager.setDtcSetting(true);
    ecu.cycle();
    CHECK(ecu.failed(BODY));
    CHECK((ecu.status(BODY) & st::CONFIRMED_DTC) != 0U);
    CHECK(ecu.occurrence(BODY) == 1U);
    CHECK(ecu.status(VEH) == 0x00U); // the other bus stays clean
    ecu.bodyState = CanBusState::ERROR_ACTIVE;
    ecu.cycle();
    CHECK(!ecu.failed(BODY));
}

// ------------------------------------------------------------ 3. device fault (degraded reaction SD) and 85

void testDeviceFault()
{
    Ecu ecu;
    ecu.cycles(2);
    CHECK(ecu.status(DEGRADED) == 0x00U);
    // the device fault enters SD while 85 is off: the groups stay degraded until the next power cycle (SYS-117)
    ecu.manager.setDtcSetting(false);
    ecu.supervisor.groups = 0x5U;
    ecu.cycles(4);
    CHECK(ecu.status(DEGRADED) == 0x00U); // no status change while off
    ecu.manager.setDtcSetting(true);
    ecu.cycle();
    // before COM-48 the FAILED of the reporter was discarded and never repeated: no DTC for the rest of the cycle
    CHECK(ecu.failed(DEGRADED));
    CHECK((ecu.status(DEGRADED) & st::CONFIRMED_DTC) != 0U);
    CHECK(ecu.occurrence(DEGRADED) == 1U);
    ecu.cycles(5);
    CHECK(ecu.failed(DEGRADED));
    CHECK(ecu.occurrence(DEGRADED) == 1U); // one occurrence while it lasts

    // failed before 85 off, cleared while off: still degraded, so failed again right after 85 on
    ecu.manager.setDtcSetting(false);
    CHECK(ecu.manager.clear(::dem::GROUP_ALL_DTCS));
    ecu.cycles(3);
    CHECK(ecu.status(DEGRADED) == 0x50U);
    ecu.manager.setDtcSetting(true);
    ecu.cycle();
    CHECK(ecu.failed(DEGRADED));

    // the supervisor's latch implausible: fail-safe FAILED as well (reportDegraded), also after 85 on
    Ecu second;
    second.cycles(2);
    second.manager.setDtcSetting(false);
    second.supervisor.groupsValid = false;
    second.cycles(2);
    second.manager.setDtcSetting(true);
    second.cycle();
    CHECK(second.failed(DEGRADED));
    CHECK(second.status(SHUTOFF) == 0x00U);
}

// ------------------------------------------------------------ 4. hardware shut-off path and 85

void testHwShutoff()
{
    Ecu ecu;
    ecu.cycles(2);
    // the family port reports the path while 85 is off; the record lasts until reset (SYS-130)
    ecu.manager.setDtcSetting(false);
    ecu.supervisor.context = 0x00010002U;
    ecu.cycles(3);
    CHECK(ecu.status(SHUTOFF) == 0x00U);
    ecu.manager.setDtcSetting(true);
    ecu.cycle();
    CHECK(ecu.failed(SHUTOFF));
    CHECK((ecu.status(SHUTOFF) & st::CONFIRMED_DTC) != 0U);
    CHECK(ecu.occurrence(SHUTOFF) == 1U);
    CHECK(ecu.status(DEGRADED) == 0x00U);

    // cleared while 85 is off, the path still latched: failed again at once after 85 on
    ecu.manager.setDtcSetting(false);
    CHECK(ecu.manager.clear(::dem::GROUP_ALL_DTCS));
    ecu.cycles(2);
    ecu.manager.setDtcSetting(true);
    ecu.cycle();
    CHECK(ecu.failed(SHUTOFF));

    // a new start (reset: record cleared, new fault memory) - passed, and 85 off/on keeps it passed
    Ecu next;
    next.cycles(2);
    next.manager.setDtcSetting(false);
    next.cycles(2);
    next.manager.setDtcSetting(true);
    next.cycles(2);
    CHECK(next.status(SHUTOFF) == 0x00U);
    CHECK(next.status(DEGRADED) == 0x00U);
    CHECK(next.status(VEH) == 0x00U);
}
} // namespace

int main()
{
    testBusOffRecoveredWhileOff();
    testBusOffOnlyWhileOff();
    testDeviceFault();
    testHwShutoff();
    std::printf("%u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
