// SIL test of NVM-08 (with COM-46 and IO-59): the BSW sources of the fault memory through the generated RTE of the
// test/dem profile, extended by tools/rtegen/test_rtegen_dem_bsw_sources.py with
//   dtc 4 SupplyUnder    bsw.power.undervoltage, state of the provider, time debouncing 50/30 ms
//   dtc 5 SupplyOver     bsw.power.overvoltage, failed_above 15000 mV, time debouncing 50/30 ms
//   dtc 6 SupplyUnderMv  bsw.power.undervoltage, failed_below 10500 mV, counter 1/-1, no condition (the supply
//                        itself decides)
//   dtc 7 SafetyDegraded safety.device_fault, reported by the integration (dem/SafetyFaultReport.h)
//   condition 2 SupplyOk bsw.power.ok
// and the reporters the generated dem/demBswReporters.h names (NX_DEM_CAN_BUSOFF: can.busoff.CAN1 of the profile,
// NX_DEM_SAFETY_DEVICE_FAULT). The harness plays the BSW service bsw.power (readPower), the ECU state, the CAN system
// (one CanBusOffReport per generated bus, as platforms/<family>/port CanSystem.cpp builds them) and the safe supervisor
// (degraded groups). Fault injection: under-/overvoltage, a provider without a reading, a lost provider, a provider
// that does not measure the voltage, bus-off, a degraded group, an implausible latch of the supervisor.
// Build and run: tools/rtegen/test_rtegen_dem_bsw_sources.py.

#include "HostStubs.h"
#include "RteConfig.h"
#include "Rte_Internal.h"
#include "dem/DemRte.h"
#include "dem/SafetyFaultReport.h"
#include "dem/demBswReporters.h"

#include <can/CanBusOffReport.h>

#include <cstdio>
#include <cstring>

bool bios::DigitalInput::value         = false;
uint32_t bios::AnalogInputScale::value = 0U;
uint8_t bios::Output::level[2]         = {0U, 0U};
int async::LockType::depth             = 0;

extern "C" void Test_Trace(uint8 const /* id */) {}

extern "C" void Test_Hook(uint8 const /* where */) {}

namespace
{
uint8_t ecuState = 1U; // RUN

struct Power
{
    uint8_t state;  // bswservice::power::State
    uint16_t mv;
    uint8_t health; // bswservice::Health: 0 pending, 1 ok, 2 failed
};

Power power = {0U, 0U, 0U};

uint32_t degradedGroups = 0U;
bool latchPlausible     = true;
} // namespace

namespace ecustate
{
namespace rte
{
void readState(uint8_t& state) { state = ecuState; }
} // namespace rte
} // namespace ecustate

namespace bswservice
{
namespace rte
{
void readPower(uint8_t& state, uint16_t& supplyMv, uint8_t& health)
{
    state    = power.state;
    supplyMv = power.mv;
    health   = power.health;
}
} // namespace rte
} // namespace bswservice

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

namespace st = ::dem::status;
using ::dem::Result;

constexpr uint16_t SUPPLY_UNDER    = 4U;
constexpr uint16_t SUPPLY_OVER     = 5U;
constexpr uint16_t SUPPLY_UNDER_MV = 6U;
constexpr uint16_t SAFETY          = 7U;
constexpr uint8_t SUPPLY_OK        = 2U;

constexpr uint8_t POWER_OK    = 1U;
constexpr uint8_t POWER_UNDER = 2U;
constexpr uint8_t POWER_OVER  = 3U;
constexpr uint8_t POWER_FAULT = 4U;

Result sample(uint16_t const dtc) { return static_cast<Result>(::rte::demSample(dtc)); }

void setPower(uint8_t const state, uint16_t const mv, uint8_t const health = 1U) { power = Power{state, mv, health}; }

// ---- the reporters of the platform and the integration, as the generated header names them
#ifndef NX_DEM_CAN_BUSOFF
#error "the profile has a bus-off DTC: NX_DEM_CAN_BUSOFF must be generated"
#endif
#ifndef NX_DEM_SAFETY_DEVICE_FAULT
#error "the profile has a safety.device_fault DTC: NX_DEM_SAFETY_DEVICE_FAULT must be generated"
#endif

struct BusOffDtc
{
    char const* bus;
    ::can::CanBusOffReport report;
};

#define BUSOFF_DTC(bus, event) BusOffDtc{#bus, ::can::CanBusOffReport(event)},
BusOffDtc busOffDtcs[] = {NX_DEM_CAN_BUSOFF(BUSOFF_DTC)};
#undef BUSOFF_DTC

::dem::EventReporter safetyDeviceFault(::safety::faultmemory::DEVICE_FAULT_EVENT);

void cycle(::dem::DemRte& dem)
{
    ::safety::faultmemory::reportDegraded(
        safetyDeviceFault,
        [](uint32_t& groups)
        {
            groups = latchPlausible ? degradedGroups : 0xFFFFFFFFU;
            return latchPlausible;
        },
        ::dem::registry.memory);
    dem.cycle();
}

void cycles(::dem::DemRte& dem, unsigned const n)
{
    for (unsigned i = 0U; i < n; ++i)
    {
        cycle(dem);
    }
}

uint8_t status(::dem::DemRte& dem, uint16_t const dtc)
{
    ::dem::DtcInfo info{};
    (void)dem.manager().dtcInfo(dtc, info);
    return info.status;
}

void testSamples()
{
    // no reading yet (provider not started) and a lost provider: no result, the condition does not hold
    setPower(POWER_OK, 12000U, 0U);
    CHECK(sample(SUPPLY_UNDER) == Result::NONE);
    CHECK(sample(SUPPLY_OVER) == Result::NONE);
    CHECK(sample(SUPPLY_UNDER_MV) == Result::NONE);
    CHECK(!::rte::demCondition(SUPPLY_OK));
    setPower(POWER_UNDER, 8000U, 2U);
    CHECK(sample(SUPPLY_UNDER) == Result::NONE);
    CHECK(sample(SUPPLY_UNDER_MV) == Result::NONE);
    // supply OK
    setPower(POWER_OK, 12000U);
    CHECK(sample(SUPPLY_UNDER) == Result::PREPASSED);
    CHECK(sample(SUPPLY_OVER) == Result::PREPASSED);
    CHECK(sample(SUPPLY_UNDER_MV) == Result::PREPASSED);
    CHECK(::rte::demCondition(SUPPLY_OK));
    // undervoltage of the provider (its threshold) at 8 V
    setPower(POWER_UNDER, 8000U);
    CHECK(sample(SUPPLY_UNDER) == Result::PREFAILED);
    CHECK(sample(SUPPLY_OVER) == Result::PREPASSED);
    CHECK(sample(SUPPLY_UNDER_MV) == Result::PREFAILED);
    CHECK(!::rte::demCondition(SUPPLY_OK));
    // 10 V: the provider says OK, the project's own threshold of 10.5 V fails
    setPower(POWER_OK, 10000U);
    CHECK(sample(SUPPLY_UNDER) == Result::PREPASSED);
    CHECK(sample(SUPPLY_UNDER_MV) == Result::PREFAILED);
    CHECK(sample(SUPPLY_OVER) == Result::PREPASSED);
    // exactly at the thresholds: not failed
    setPower(POWER_OK, 10500U);
    CHECK(sample(SUPPLY_UNDER_MV) == Result::PREPASSED);
    setPower(POWER_OK, 15000U);
    CHECK(sample(SUPPLY_OVER) == Result::PREPASSED);
    // overvoltage: 16 V against the limit of 15 V, and the provider's state
    setPower(POWER_OVER, 16000U);
    CHECK(sample(SUPPLY_OVER) == Result::PREFAILED);
    CHECK(sample(SUPPLY_UNDER) == Result::PREPASSED);
    CHECK(sample(SUPPLY_UNDER_MV) == Result::PREPASSED);
    CHECK(!::rte::demCondition(SUPPLY_OK));
    setPower(POWER_OK, 15001U);
    CHECK(sample(SUPPLY_OVER) == Result::PREFAILED);
    // provider without thresholds (state UNKNOWN) and a regulator fault: the state tells nothing about undervoltage
    setPower(0U, 12000U);
    CHECK(sample(SUPPLY_UNDER) == Result::NONE);
    CHECK(sample(SUPPLY_UNDER_MV) == Result::PREPASSED); // the voltage still does
    setPower(POWER_FAULT, 12000U);
    CHECK(sample(SUPPLY_UNDER) == Result::NONE);
    // provider that does not measure the voltage (0 mV): no result of a limit, the state still counts
    setPower(POWER_UNDER, 0U);
    CHECK(sample(SUPPLY_UNDER_MV) == Result::NONE);
    CHECK(sample(SUPPLY_OVER) == Result::NONE);
    CHECK(sample(SUPPLY_UNDER) == Result::PREFAILED);
    // reported events are not sampled
    CHECK(sample(2U) == Result::NONE);
    CHECK(sample(SAFETY) == Result::NONE);
}

void testFaultMemory()
{
    ::dem::DemRte dem;
    (void)dem.start();
    CHECK(::dem::registry.memory == &dem.manager());
    for (BusOffDtc& b : busOffDtcs)
    {
        b.report.update(::can::CanBusState::ERROR_ACTIVE, false);
    }
    setPower(POWER_OK, 12500U);
    degradedGroups = 0U;
    cycles(dem, 10U);
    CHECK((status(dem, SUPPLY_UNDER) & (st::TEST_FAILED | st::TEST_NOT_COMPLETED_THIS_OPERATION_CYCLE)) == 0U);
    CHECK((status(dem, SAFETY) & (st::TEST_FAILED | st::TEST_NOT_COMPLETED_THIS_OPERATION_CYCLE)) == 0U);
    CHECK((status(dem, 2U) & st::TEST_NOT_COMPLETED_THIS_OPERATION_CYCLE) == 0U); // bus-off tested: passed

    // undervoltage for 40 ms: debouncing 50 ms - not matured
    setPower(POWER_UNDER, 8200U);
    cycles(dem, 4U);
    CHECK((status(dem, SUPPLY_UNDER) & st::TEST_FAILED) == 0U);
    CHECK((status(dem, SUPPLY_UNDER_MV) & st::TEST_FAILED) != 0U); // counter 1: at once
    // back to 12 V: the undervoltage does not mature
    setPower(POWER_OK, 12000U);
    cycles(dem, 10U);
    CHECK((status(dem, SUPPLY_UNDER) & st::CONFIRMED_DTC) == 0U);
    // undervoltage lasting 100 ms: matures, only that DTC
    setPower(POWER_UNDER, 8200U);
    cycles(dem, 10U);
    CHECK((status(dem, SUPPLY_UNDER) & st::TEST_FAILED) != 0U);
    CHECK((status(dem, SUPPLY_UNDER) & st::CONFIRMED_DTC) != 0U);
    CHECK((status(dem, SUPPLY_OVER) & st::TEST_FAILED) == 0U);
    // provider lost while low: no result, the status stays
    setPower(POWER_UNDER, 8200U, 2U);
    cycles(dem, 10U);
    CHECK((status(dem, SUPPLY_UNDER) & st::TEST_FAILED) != 0U);
    // supply back: passes after the passed time
    setPower(POWER_OK, 12500U);
    cycles(dem, 5U);
    CHECK((status(dem, SUPPLY_UNDER) & st::TEST_FAILED) == 0U);
    CHECK((status(dem, SUPPLY_UNDER) & st::CONFIRMED_DTC) != 0U); // confirmed stays

    // overvoltage 16 V for 100 ms against the project limit of 15 V
    setPower(POWER_OK, 16000U);
    cycles(dem, 10U);
    CHECK((status(dem, SUPPLY_OVER) & st::CONFIRMED_DTC) != 0U);
    setPower(POWER_OK, 12500U);
    cycles(dem, 5U);

    // bus-off on CAN1 through the generated reporter list
    for (BusOffDtc& b : busOffDtcs)
    {
        CHECK(b.report.reporter().dtc() == 2);
        CHECK(std::strcmp(b.bus, "CAN1") == 0);
        b.report.update(::can::CanBusState::BUS_OFF, true);
    }
    cycle(dem);
    CHECK((status(dem, 2U) & st::TEST_FAILED) != 0U);
    for (BusOffDtc& b : busOffDtcs)
    {
        b.report.update(::can::CanBusState::ERROR_PASSIVE, false);
    }
    cycle(dem);
    CHECK((status(dem, 2U) & st::TEST_FAILED) == 0U);
    CHECK((status(dem, 2U) & st::CONFIRMED_DTC) != 0U);

    // the safe supervisor degrades group 1 after a device fault: recorded while it lasts
    CHECK(safetyDeviceFault.dtc() == SAFETY);
    degradedGroups = 0x2U;
    cycle(dem);
    CHECK((status(dem, SAFETY) & st::TEST_FAILED) != 0U);
    CHECK((status(dem, SAFETY) & st::CONFIRMED_DTC) != 0U);
    degradedGroups = 0U;
    cycle(dem);
    CHECK((status(dem, SAFETY) & st::TEST_FAILED) == 0U);
    // an implausible latch reads every group degraded (fail-safe): recorded as failed as well
    latchPlausible = false;
    cycle(dem);
    CHECK((status(dem, SAFETY) & st::TEST_FAILED) != 0U);
    latchPlausible = true;
    cycle(dem);

    // the ECU leaves RUN: DTCs with the condition Running stand still - the supply DTCs have none and go on
    dem.stop();
    ::dem::registry.memory = nullptr;
}
} // namespace

int main()
{
    testSamples();
    testFaultMemory();
    std::printf("%u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
