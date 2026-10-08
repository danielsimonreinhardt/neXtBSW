// RTE-16 host test: providers of the BSW services bsw.power and bsw.reset against the real hand-overs
// ecu/rte/services/src/bswService/BswServiceRte.cpp and libs/bsw/ecuState/src/ecuState/EcuStateRte.cpp.
//
// Built once per profile (tools/test/tests.yaml, rte16-providers-*):
//   NX_TEST_WITH_SBC=1  board with UJA113x: NX_BSW_POWER_PROVIDER=1, SbcPowerReport.h maps the battery monitor,
//                       start reasons of the role power_supply as Uja113xPowerSupply reports them
//   NX_TEST_WITH_SBC=0  board without SBC: NX_BSW_POWER_PROVIDER=0 (no provider), role power_supply is
//                       PermanentPowerSupply (start reasons wakeup::UNKNOWN)
//   NX_BSW_MCU_RESET_REASON=1/0  with or without the MCU reset reason (a family without provider)
// The MCU reset reason is simulated with the pure s32k1xx mapping (mcuResetReason/RcmResetReason.h): the test plays
// the passes through the start-up with the watchdog fast test and feeds the result into mcuStartReasons().

#include "bswService/BswServiceRte.h"
#include "ecuState/EcuStateRte.h"
#include "ecuState/McuStartReasons.h"
#include "mcuResetReason/RcmResetReason.h"

#if NX_BSW_MCU_RESET_REASON
#include "bswService/McuResetReason.h"
#endif
#if NX_TEST_WITH_SBC
#include "systems/SbcPowerReport.h"
#endif

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

namespace wakeup = ::ecustate::wakeup;
using ::bswservice::Health;
using Reason = ::bswservice::reset::Reason;
using PowerState = ::bswservice::power::State;

uint32_t mcuReasons = 0U; // what the simulated MCU family reports

struct ResetValue
{
    uint8_t reason;
    uint32_t sources;
    uint8_t health;
};

ResetValue readReset()
{
    ResetValue v{0xFFU, 0xFFFFFFFFUL, 0xFFU};
    ::bswservice::rte::readReset(v.reason, v.sources, v.health);
    return v;
}

struct PowerValue
{
    uint8_t state;
    uint16_t mv;
    uint8_t health;
};

PowerValue readPower()
{
    PowerValue v{0xFFU, 0xFFFFU, 0xFFU};
    ::bswservice::rte::readPower(v.state, v.mv, v.health);
    return v;
}

uint8_t u8(Reason const r) { return static_cast<uint8_t>(r); }
uint8_t u8(Health const h) { return static_cast<uint8_t>(h); }
uint8_t u8(PowerState const s) { return static_cast<uint8_t>(s); }

// ---- s32k1xx: RCM_SRS mapping and capture across the watchdog fast test

void rcmMapping()
{
    using namespace ::rcm;
    CHECK(startReasonsOf(0U) == wakeup::NONE);
    CHECK(startReasonsOf(SRS_POR | SRS_LVD) == wakeup::POWER); // power-on sets LVD as well: not SUPPLY
    CHECK(startReasonsOf(SRS_POR) == wakeup::POWER);
    CHECK(startReasonsOf(SRS_LVD) == wakeup::SUPPLY);          // brown-out without power-on
    CHECK(startReasonsOf(SRS_WDOG) == wakeup::WATCHDOG);
    uint32_t const resets[]
        = {SRS_LOC, SRS_LOL, SRS_CMU_LOC, SRS_PIN, SRS_JTAG, SRS_LOCKUP, SRS_SW, SRS_MDM_AP, SRS_SACKERR};
    for (uint32_t const bit : resets)
    {
        CHECK(startReasonsOf(bit) == wakeup::RESET);
    }
    CHECK(startReasonsOf((1UL << 0U) | (1UL << 12U) | (1UL << 14U)) == wakeup::NONE); // reserved bits
    CHECK(startReasonsOf(SRS_WDOG | SRS_PIN) == (wakeup::WATCHDOG | wakeup::RESET));
    CHECK(startReasonsOf(SRS_WDOG | SRS_SW) == wakeup::WATCHDOG); // SYS-103: SW is the watchdog handler's follow-up
    CHECK(startReasonsOf(0xFFFFFFFFUL) == (wakeup::POWER | wakeup::WATCHDOG | wakeup::RESET));

    CHECK(isWatchdogTestReset(SRS_WDOG, WDOG_TST_FAST_LOW));
    CHECK(isWatchdogTestReset(SRS_WDOG, WDOG_TST_FAST_HIGH));
    CHECK(!isWatchdogTestReset(SRS_WDOG, 1U)); // user mode: a real watchdog reset
    CHECK(!isWatchdogTestReset(SRS_WDOG, 0U));
    CHECK(!isWatchdogTestReset(SRS_PIN, WDOG_TST_FAST_LOW));
}

// One start of the application: the real reset, then the two resets of the fast test (low, high byte).
uint32_t startWithFastTest(::rcm::Record volatile& record, uint32_t const srs, uint8_t const tstBefore)
{
    uint32_t reasons = ::rcm::capture(record, srs, tstBefore);
    CHECK(reasons == ::rcm::startReasonsOf(srs)); // known in the first pass already, before main()
    reasons = ::rcm::capture(record, ::rcm::SRS_WDOG, ::rcm::WDOG_TST_FAST_LOW);
    CHECK(reasons == ::rcm::startReasonsOf(srs));
    reasons = ::rcm::capture(record, ::rcm::SRS_WDOG, ::rcm::WDOG_TST_FAST_HIGH);
    return reasons; // TST is 1 (user mode) afterwards
}

void rcmCapture()
{
    ::rcm::Record volatile record{0xA5A5A5A5UL, 0x12345678UL}; // random RAM after power-on
    CHECK(startWithFastTest(record, ::rcm::SRS_POR | ::rcm::SRS_LVD, 0U) == wakeup::POWER);
    CHECK(startWithFastTest(record, ::rcm::SRS_WDOG, 1U) == wakeup::WATCHDOG); // real watchdog reset
    CHECK(startWithFastTest(record, ::rcm::SRS_PIN, 1U) == wakeup::RESET);    // reset pin, SBC or debugger
    CHECK(startWithFastTest(record, ::rcm::SRS_SW, 1U) == wakeup::RESET);     // software reset (bootloader)
    CHECK(startWithFastTest(record, ::rcm::SRS_LVD, 1U) == wakeup::SUPPLY);

    // A fast-test reset without a recorded start (no-init RAM lost): unknown, never the test's WDOG
    ::rcm::Record volatile lost{0x00000020UL, 0x00000000UL};
    CHECK(::rcm::capture(lost, ::rcm::SRS_WDOG, ::rcm::WDOG_TST_FAST_LOW) == wakeup::NONE);
    ::rcm::Record volatile stale{::rcm::SRS_WDOG, ~::rcm::SRS_WDOG}; // right complement, wrong key
    CHECK(::rcm::capture(stale, ::rcm::SRS_WDOG, ::rcm::WDOG_TST_FAST_HIGH) == wakeup::NONE);

    // Fast test failed (no reset): the first pass stays the result
    ::rcm::Record volatile once{0U, 0U};
    CHECK(::rcm::capture(once, ::rcm::SRS_POR, 0U) == wakeup::POWER);
}

// ---- bsw.reset: both sources combined with the ranking of RTE-05

void combineRanking()
{
    namespace reset = ::bswservice::reset;
    CHECK(reset::combine(wakeup::NONE, wakeup::NONE) == wakeup::NONE);
    CHECK(reset::combine(wakeup::NONE, wakeup::POWER) == wakeup::POWER);             // before the run level
    CHECK(reset::combine(wakeup::UNKNOWN, wakeup::NONE) == wakeup::UNKNOWN);         // MCU cannot tell either
    CHECK(reset::combine(wakeup::UNKNOWN, wakeup::POWER) == wakeup::POWER);          // PermanentPowerSupply
    CHECK(reset::combine(wakeup::POWER, wakeup::POWER) == wakeup::POWER);
    CHECK(reset::reasonOf(reset::combine(wakeup::RESET, wakeup::WATCHDOG)) == Reason::WATCHDOG); // MCU watchdog
    CHECK(reset::reasonOf(reset::combine(wakeup::WATCHDOG, wakeup::RESET)) == Reason::WATCHDOG); // SBC watchdog
    CHECK(reset::reasonOf(reset::combine(wakeup::canBus(0U), wakeup::POWER)) == Reason::WAKEUP); // sleep, V1 off
    CHECK(reset::reasonOf(reset::combine(wakeup::TIMER, wakeup::POWER)) == Reason::WAKEUP);
    CHECK(reset::reasonOf(reset::combine(wakeup::SUPPLY, wakeup::RESET)) == Reason::SUPPLY);
    CHECK(reset::reasonOf(reset::combine(wakeup::RESET, wakeup::SUPPLY)) == Reason::SUPPLY);     // MCU brown-out
    CHECK(reset::reasonOf(reset::combine(wakeup::RESET, wakeup::POWER)) == Reason::POWER_ON);
    CHECK(reset::reasonOf(reset::combine(wakeup::UNKNOWN, wakeup::RESET)) == Reason::RESET);
    CHECK(reset::healthOf(reset::combine(wakeup::UNKNOWN, wakeup::RESET)) == Health::OK);
    CHECK(reset::healthOf(reset::combine(wakeup::UNKNOWN, wakeup::NONE)) == Health::FAILED);

    // SYS-18: the ECU state management joins the same two sources one layer below (it cannot call the BSW
    // service, which sits above the RTE). Both rules have to agree, otherwise ecu.wakeup and bsw.reset would
    // contradict each other again - which is exactly the board finding of 2026-09-19.
    uint32_t const reasons[]
        = {wakeup::NONE,
           wakeup::UNKNOWN,
           wakeup::POWER,
           wakeup::RESET,
           wakeup::WATCHDOG,
           wakeup::SUPPLY,
           wakeup::TIMER,
           wakeup::canBus(0U),
           wakeup::local(3U),
           wakeup::POWER | wakeup::WATCHDOG,
           wakeup::RESET | wakeup::SUPPLY};
    for (uint32_t const supply : reasons)
    {
        for (uint32_t const mcu : reasons)
        {
            CHECK(::ecustate::combineStartReasons(supply, mcu) == reset::combine(supply, mcu));
        }
    }
}

/** Start reasons of the role power_supply on this profile's board for a start with the MCU reset \p srs. */
uint32_t supplyReasonsFor(uint32_t const srs, uint32_t const sbcReasons)
{
#if NX_TEST_WITH_SBC
    (void)srs;
    return sbcReasons;
#else
    (void)srs;
    (void)sbcReasons;
    return wakeup::UNKNOWN; // PermanentPowerSupply
#endif
}

struct StartCase
{
    char const* name;
    uint32_t srs;       // RCM_SRS of the start
    uint32_t sbc;       // Uja113xPowerSupply::getStartupWakeupSources() for the same start (JP1 open)
    Reason expected;    // bsw.reset.reason after the run level
    Reason expectedWithoutMcu;
};

void resetService()
{
    using ::rcm::SRS_LVD;
    using ::rcm::SRS_PIN;
    using ::rcm::SRS_POR;
    using ::rcm::SRS_SW;
    using ::rcm::SRS_WDOG;
#if NX_TEST_WITH_SBC
    StartCase const cases[] = {
        {"power-on", SRS_POR | SRS_LVD, wakeup::POWER, Reason::POWER_ON, Reason::POWER_ON},
        // MCU watchdog with JP1 open: the SBC only sees an MCU-only start (RSS unchanged) -> RESET
        {"MCU watchdog", SRS_WDOG, wakeup::RESET, Reason::WATCHDOG, Reason::RESET},
        // SBC watchdog: the SBC pulls RSTN, the MCU sees a pin reset
        {"SBC watchdog", SRS_PIN, wakeup::WATCHDOG, Reason::WATCHDOG, Reason::WATCHDOG},
        // wake-up by CAN from Sleep with V1 off: the MCU sees a power-on
        {"CAN wake-up", SRS_POR | SRS_LVD, wakeup::canBus(0U), Reason::WAKEUP, Reason::WAKEUP},
        {"reset pin", SRS_PIN, wakeup::RESET, Reason::RESET, Reason::RESET},
        {"software reset", SRS_SW, wakeup::RESET, Reason::RESET, Reason::RESET},
        // SBC startup failed: the supply cannot tell, the MCU still can
        {"SBC unknown", SRS_POR | SRS_LVD, wakeup::UNKNOWN, Reason::POWER_ON, Reason::UNKNOWN},
        {"MCU brown-out", SRS_LVD, wakeup::RESET, Reason::SUPPLY, Reason::RESET},
    };
#else
    StartCase const cases[] = {
        {"power-on", SRS_POR | SRS_LVD, 0U, Reason::POWER_ON, Reason::UNKNOWN},
        {"MCU watchdog", SRS_WDOG, 0U, Reason::WATCHDOG, Reason::UNKNOWN},
        {"reset pin", SRS_PIN, 0U, Reason::RESET, Reason::UNKNOWN},
        {"software reset", SRS_SW, 0U, Reason::RESET, Reason::UNKNOWN},
        {"MCU brown-out", SRS_LVD, 0U, Reason::SUPPLY, Reason::UNKNOWN},
    };
#endif
    for (StartCase const& c : cases)
    {
        ::ecustate::rte::reset();
        ::rcm::Record volatile record{0U, 0U};
        mcuReasons = startWithFastTest(record, c.srs, 1U);

        // Before the run level: only the MCU knows (RTE-16), the role power_supply publishes 0 until then
        ResetValue v = readReset();
#if NX_BSW_MCU_RESET_REASON
        CHECK(v.reason == u8(::bswservice::reset::reasonOf(::rcm::startReasonsOf(c.srs))));
        CHECK(v.health == u8(Health::OK));
        CHECK(v.sources == ::rcm::startReasonsOf(c.srs));
#else
        CHECK(v.reason == u8(Reason::UNKNOWN));
        CHECK(v.health == u8(Health::PENDING));
        CHECK(v.sources == wakeup::NONE);
#endif

        // Run level: the role power_supply publishes its start reasons
        ::ecustate::rte::publish(::ecustate::State::RUN, supplyReasonsFor(c.srs, c.sbc));
        v = readReset();
#if NX_BSW_MCU_RESET_REASON
        Reason const expected = c.expected;
#else
        Reason const expected = c.expectedWithoutMcu;
#endif
        if (v.reason != u8(expected))
        {
            std::printf("  case %s: reason %u, expected %u\n", c.name, v.reason, u8(expected));
        }
        CHECK(v.reason == u8(expected));
        CHECK(v.health == u8((expected == Reason::UNKNOWN) ? Health::FAILED : Health::OK));
        // the booleans bsw.reset.watchdog/power_on of the RTE come from these bits
        CHECK(((v.sources & wakeup::WATCHDOG) != 0U) == (expected == Reason::WATCHDOG));
        CHECK((v.sources & wakeup::UNKNOWN) == ((expected == Reason::UNKNOWN) ? wakeup::UNKNOWN : 0U));
    }
    ::ecustate::rte::reset();
    mcuReasons = 0U;
}

// ---- bsw.power

void powerService()
{
    ::bswservice::rte::reset();
    PowerValue v = readPower();
    CHECK((v.state == u8(PowerState::UNKNOWN)) && (v.mv == 0U));
#if NX_BSW_POWER_PROVIDER
    CHECK(v.health == u8(Health::PENDING)); // QLF_DEBOUNCE until the SBC system's first reading
#else
    CHECK(v.health == u8(Health::FAILED)); // QLF_ERROR from the start: no provider will ever publish
    // stays so: nothing on this board publishes, a reset of the hand-over gives the same value
    ::bswservice::rte::reset();
    v = readPower();
    CHECK((v.state == u8(PowerState::UNKNOWN)) && (v.mv == 0U) && (v.health == u8(Health::FAILED)));
#endif

#if NX_TEST_WITH_SBC
    using ::sbc::Uja113x;
    using ::systems::sbcpower::reportOf;
    ::sbc::BatteryMonitorConfig monitor{};
    monitor.configure      = true;
    monitor.source         = ::sbc::BatteryMonitorSource::BAT;
    monitor.undervoltageMv = 7000U; // own profile, not the eval board values
    monitor.overvoltageMv  = 16000U;
    Uja113x::BatteryVoltages meas{13099U, 12490U, false, false};

    auto r = reportOf(Uja113x::Status::OK, meas, monitor);
    CHECK(!r.failed && (r.state == PowerState::OK) && (r.supplyMv == 13099U));
    meas.undervoltage = true;
    meas.batMv        = 6900U;
    r                 = reportOf(Uja113x::Status::OK, meas, monitor);
    CHECK(!r.failed && (r.state == PowerState::UNDERVOLTAGE) && (r.supplyMv == 6900U));
    meas.undervoltage = false;
    meas.overvoltage  = true;
    meas.batMv        = 16100U;
    r                 = reportOf(Uja113x::Status::OK, meas, monitor);
    CHECK(!r.failed && (r.state == PowerState::OVERVOLTAGE) && (r.supplyMv == 16100U));
    // source BATSENSE: the voltage the SBC compares
    meas.overvoltage = false;
    monitor.source   = ::sbc::BatteryMonitorSource::BATSENSE;
    r                = reportOf(Uja113x::Status::OK, meas, monitor);
    CHECK(!r.failed && (r.state == PowerState::OK) && (r.supplyMv == 12490U));
    // no monitor configuration: voltage yes, state unknown (no thresholds of the project)
    monitor.configure = false;
    meas.undervoltage = true;
    r                 = reportOf(Uja113x::Status::OK, meas, monitor);
    CHECK(!r.failed && (r.state == PowerState::UNKNOWN) && (r.supplyMv == 16100U));
    monitor.configure = true;
    // no reading
    CHECK(reportOf(Uja113x::Status::NOT_READY, meas, monitor).failed);
    CHECK(reportOf(Uja113x::Status::SPI_ERROR, meas, monitor).failed);
    CHECK(reportOf(Uja113x::Status::VERIFY_ERROR, meas, monitor).failed);

    // through the hand-over as the RTE reads it
    meas = Uja113x::BatteryVoltages{12003U, 0U, false, false};
    monitor.source = ::sbc::BatteryMonitorSource::BAT;
    ::systems::sbcpower::publish(reportOf(Uja113x::Status::OK, meas, monitor));
    v = readPower();
    CHECK((v.state == u8(PowerState::OK)) && (v.mv == 12003U) && (v.health == u8(Health::OK)));
    meas.undervoltage = true;
    meas.batMv        = 6800U;
    ::systems::sbcpower::publish(reportOf(Uja113x::Status::OK, meas, monitor));
    v = readPower();
    CHECK((v.state == u8(PowerState::UNDERVOLTAGE)) && (v.mv == 6800U) && (v.health == u8(Health::OK)));
    // SPI error: last state and voltage stay, health FAILED (QLF_ERROR)
    ::systems::sbcpower::publish(reportOf(Uja113x::Status::SPI_ERROR, meas, monitor));
    v = readPower();
    CHECK((v.state == u8(PowerState::UNDERVOLTAGE)) && (v.mv == 6800U) && (v.health == u8(Health::FAILED)));
    // SBC back: OK again
    meas.undervoltage = false;
    meas.batMv        = 12500U;
    ::systems::sbcpower::publish(reportOf(Uja113x::Status::OK, meas, monitor));
    v = readPower();
    CHECK((v.state == u8(PowerState::OK)) && (v.mv == 12500U) && (v.health == u8(Health::OK)));
    // SBC startup failed before any reading: UNKNOWN with QLF_ERROR
    ::bswservice::rte::reset();
    ::systems::sbcpower::publish(reportOf(Uja113x::Status::NOT_READY, meas, monitor));
    v = readPower();
    CHECK((v.state == u8(PowerState::UNKNOWN)) && (v.mv == 0U) && (v.health == u8(Health::FAILED)));
#endif
    ::bswservice::rte::reset();
}
} // namespace

#if NX_BSW_MCU_RESET_REASON
namespace bswservice
{
namespace reset
{
uint32_t mcuStartReasons() { return mcuReasons; }
} // namespace reset
} // namespace bswservice
#endif

// bsw.time is not part of this test (RTE-05), the hand-over still links the timer
extern "C" uint64_t getSystemTimeUs64Bit(void) { return 0U; }

int main()
{
    std::printf(
        "RTE-16 BSW service providers: SBC %d, power provider %d, MCU reset reason %d\n",
        NX_TEST_WITH_SBC,
        NX_BSW_POWER_PROVIDER,
        NX_BSW_MCU_RESET_REASON);
    rcmMapping();
    rcmCapture();
    combineRanking();
    resetService();
    powerService();
    std::printf("%u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
