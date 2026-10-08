/* IO-45: host test of the phase duty source of a gate driver (GateDriverDutySource.h) - the conversion of the timer's
 * on time into the per mille of the driver's contract, and the enable rule of the real TMC6200 driver
 * (libs/bsw/gdTmc6200, all three phases in one state) with that source attached, as Integration.cpp attaches it.
 *
 * Own test profile, not the eval board: three periods (4000 ticks as the demo's 20 kHz edge counting, 3200 as a
 * center-aligned 64 MHz profile, and a tiny one of 7 ticks), and a register-free group model that answers
 * highSideOnTicks() like ftmPwm::FtmPwmGroup (whose answers against a simulated FTM waveform are tested by
 * platforms/s32k1xx/bsp/bspFtmPwmGroup/test/test_ftm_pwm_group.py).
 *
 * What it shows:
 * - per mille keeps the state of every on time exactly: 0 only for no on time, DUTY_FULL only for the whole period,
 *   anything between in 1 .. DUTY_FULL - 1 - for every on time of every period (no rounding moves a phase into
 *   another state);
 * - the unit trap of IO-45: the console/RTE value 5000 (0.01 %) of 50 % arrives as 500 per mille, never as 5000;
 * - an unknown pair, another number of pairs or a period of 0 make the source answer "unknown", and the driver refuses;
 * - with the source attached the driver enables for all off, all switching and all full and refuses every mixture
 *   (the board points 50/50/0, 0/0/50, 50/50/100) without a DRV_EN pulse; the masked group after the start (all off)
 *   is a common state;
 * - a duty the counter cannot represent (0.01 % of 4000 ticks: 0.4 tick, on time 0) is OFF, as at the pin.
 * The meaningfulness is checked by test_gate_driver_duty_source_mutations.py.
 *
 * Build and run (zig c++, no hardware): tools/test -k gate-driver-duty-source
 */

#include "GateDriverDutySource.h"

#include <cstdio>
#include <cstring>

namespace
{
int failures = 0;
int checks   = 0;

#define CHECK(cond)                                                                  \
    do                                                                               \
    {                                                                                \
        ++checks;                                                                    \
        if (!(cond))                                                                 \
        {                                                                            \
            ++failures;                                                              \
            std::printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);            \
        }                                                                            \
    } while (false)

using ::gd::PhaseState;
using ::gd::Tmc6200;
using Status = ::gd::Tmc6200::Status;

PhaseState stateOfTicks(uint32_t const on, uint32_t const period)
{
    return (on == 0U) ? PhaseState::OFF : ((on >= period) ? PhaseState::FULL : PhaseState::SWITCHING);
}

/* The group as the source sees it: on time per pair in ticks, or "cannot tell" (masked/forced differently, both
 * forced on, inverted - FtmPwmGroup::highSideOnTicks()). */
struct FakeGroup
{
    uint32_t ticks[4] = {0U, 0U, 0U, 0U};
    bool known[4]     = {true, true, true, true};
    mutable int reads = 0;

    bool highSideOnTicks(uint8_t const pair, uint32_t& value) const
    {
        ++reads;
        if ((pair >= 4U) || !known[pair])
        {
            return false;
        }
        value = ticks[pair];
        return true;
    }
};

// ---- the source as Integration.cpp attaches it: one group of the project, its pair count and its period ----
FakeGroup group;
uint8_t pairCount    = 3U;
uint32_t periodTicks = 4000U;

bool source(::gd::PhaseDuty& duty) { return ::integration::readPhaseDuty(group, pairCount, periodTicks, duty); }

/* TMC6200 as far as init(), enable() and disable() need it (the model of DebugHaltGuardHostTest.cpp). */
class FakeTmc6200
: public ::gd::ITmc6200Spi
, public ::gd::ITmc6200Io
{
public:
    uint32_t reg[128];
    bool drvEnPin        = false;
    int enableHighWrites = 0;

    FakeTmc6200()
    {
        std::memset(reg, 0, sizeof(reg));
        reg[TMC6200_REG_GCONF] = TMC6200_GCONF_RESET;
        reg[TMC6200_REG_IOIN]  = static_cast<uint32_t>(TMC6200_VERSION) << 24U;
    }

    bool transfer(uint64_t const tx, uint64_t& rx) override
    {
        uint8_t const addressByte = static_cast<uint8_t>(tx >> 32U);
        uint8_t const address     = addressByte & TMC6200_ADDRESS_MASK;
        uint32_t const data       = static_cast<uint32_t>(tx);
        if ((addressByte & TMC6200_WRITE_FLAG) != 0U)
        {
            reg[address] = (address == TMC6200_REG_GSTAT) ? (reg[address] & ~data) : data;
            rx           = 0U;
            return true;
        }
        uint32_t value = reg[address];
        if (address == TMC6200_REG_IOIN)
        {
            value = (value & ~TMC6200_IOIN_DRV_EN_MASK) | (drvEnPin ? TMC6200_IOIN_DRV_EN_MASK : 0U);
        }
        rx = value;
        return true;
    }

    bool setDriverEnable(bool const on) override
    {
        if (on)
        {
            ++enableHighWrites;
        }
        drvEnPin = on;
        return true;
    }

    bool readFault(bool& active) override
    {
        active = false;
        return true;
    }
};

::gd::Tmc6200Config const CONFIG = ::gd::keepAllConfig();

void setTicks(uint32_t const u, uint32_t const v, uint32_t const w)
{
    group.ticks[0] = u;
    group.ticks[1] = v;
    group.ticks[2] = w;
}

// on time of a duty in 0.01 % as ComplementaryPwm::edges() computes it for the edge counting (floor)
uint32_t ticksOfCentiPercent(uint32_t const duty, uint32_t const period) { return (period * duty) / 10000U; }

void conversionKeepsTheStateOfEveryOnTime()
{
    static constexpr uint32_t const periods[] = {4000U, 3200U, 7U, 1U, 65535U};
    for (uint32_t const period : periods)
    {
        for (uint32_t on = 0U; on <= period + 2U; ++on)
        {
            uint16_t const perMille = ::integration::perMilleOfPeriod(on, period);
            CHECK(Tmc6200::phaseState(perMille) == stateOfTicks(on, period));
            CHECK(perMille <= Tmc6200::DUTY_FULL);
        }
    }
    // the values themselves: rounded down, kept off both ends
    CHECK(::integration::perMilleOfPeriod(0U, 4000U) == 0U);
    CHECK(::integration::perMilleOfPeriod(1U, 4000U) == 1U);   // 0.25 per mille: switching, not off
    CHECK(::integration::perMilleOfPeriod(4U, 4000U) == 1U);
    CHECK(::integration::perMilleOfPeriod(2000U, 4000U) == 500U);
    CHECK(::integration::perMilleOfPeriod(3999U, 4000U) == 999U); // 999.75 per mille: switching, not full
    CHECK(::integration::perMilleOfPeriod(4000U, 4000U) == 1000U);
    CHECK(::integration::perMilleOfPeriod(4001U, 4000U) == 1000U);
    CHECK(::integration::perMilleOfPeriod(1U, 3U) == 333U);
    CHECK(::integration::perMilleOfPeriod(5U, 0U) == 0U);
    CHECK(::integration::perMilleOfPeriod(0xFFFFFFFFU, 0xFFFFFFFFU) == 1000U); // no overflow in the product
    CHECK(::integration::perMilleOfPeriod(0xFFFFFFFEU, 0xFFFFFFFFU) == 999U);
}

void theUnitTrapOfTheConsole()
{
    // "pwm set <pair> 5000" (0.01 %) = 50 %: the timer holds half a period, the driver sees 500 per mille
    uint32_t const half = ticksOfCentiPercent(5000U, 4000U);
    CHECK(half == 2000U);
    CHECK(::integration::perMilleOfPeriod(half, 4000U) == 500U);
    // every console value: per mille = floor(value / 10) apart from the ends, never the value itself
    for (uint32_t value = 0U; value <= 10000U; ++value)
    {
        uint32_t const on       = ticksOfCentiPercent(value, 4000U);
        uint16_t const perMille = ::integration::perMilleOfPeriod(on, 4000U);
        CHECK(perMille <= Tmc6200::DUTY_FULL);
        if ((value >= 10U) && (value < 10000U))
        {
            CHECK((perMille == value / 10U) || ((value / 10U) == 0U));
        }
    }
    // 0.01 % is 0.4 tick at 4000 ticks: the counter has no on time, the phase is off - as at the pin
    CHECK(ticksOfCentiPercent(1U, 4000U) == 0U);
    CHECK(Tmc6200::phaseState(::integration::perMilleOfPeriod(ticksOfCentiPercent(1U, 4000U), 4000U))
          == PhaseState::OFF);
}

void unknownStatesAreUnknown()
{
    ::gd::PhaseDuty duty{7U, 7U, 7U};
    setTicks(2000U, 2000U, 2000U);
    pairCount   = 3U;
    periodTicks = 4000U;
    CHECK(source(duty) && (duty.u == 500U) && (duty.v == 500U) && (duty.w == 500U));
    for (uint8_t pair = 0U; pair < 3U; ++pair)
    {
        group.known[pair] = false;
        CHECK(!source(duty));
        group.known[pair] = true;
    }
    group.known[3] = false; // a fourth pair of the group is none of the phases
    CHECK(source(duty));
    group.known[3] = true;
    static constexpr uint8_t const otherCounts[] = {0U, 1U, 2U, 4U};
    for (uint8_t const count : otherCounts)
    {
        pairCount = count;
        CHECK(!source(duty));
    }
    pairCount   = 3U;
    periodTicks = 0U;
    CHECK(!source(duty));
    periodTicks = 4000U;
}

void driverWithTheSource()
{
    FakeTmc6200 dev;
    Tmc6200 driver(dev, dev, CONFIG);
    CHECK(driver.init(0U) == Status::OK);
    driver.setPhaseDutySource(&source);
    pairCount   = 3U;
    periodTicks = 4000U;

    struct Case
    {
        uint32_t u;
        uint32_t v;
        uint32_t w;
        bool allowed;
    };
    // on times in ticks of 4000 (per mille x 4); the board points of B-IO44-3 and their neighbours
    static constexpr Case const cases[] = {
        {0U, 0U, 0U, true},             // the masked group after the start: all off
        {2000U, 2000U, 2000U, true},    // all switching, 50 %
        {400U, 2000U, 3600U, true},     // all switching, unequal (a field vector)
        {4000U, 4000U, 4000U, true},    // all full
        {2000U, 2000U, 0U, false},      // 50/50/0, capture 45
        {0U, 0U, 2000U, false},         // 0/0/50, capture 46
        {2000U, 2000U, 4000U, false},   // 50/50/100, B-IO44-3
        {1U, 0U, 0U, false},            // the smallest on time is switching
        {3999U, 4000U, 4000U, false},   // one tick short of full is switching
    };
    for (Case const& c : cases)
    {
        setTicks(c.u, c.v, c.w);
        int const pulsesBefore = dev.enableHighWrites;
        Status const status    = driver.enable();
        CHECK((status == Status::OK) == c.allowed);
        if (c.allowed)
        {
            CHECK(dev.drvEnPin);
            CHECK(driver.disable() == Status::OK);
        }
        else
        {
            CHECK(status == Status::PHASES_MIXED);
            CHECK(dev.enableHighWrites == pulsesBefore); // no DRV_EN pulse (B-IO44-5)
            CHECK(!dev.drvEnPin);
        }
        // what the driver saw is what the timer holds, in per mille
        CHECK(driver.getLastPhaseDuty().u == ::integration::perMilleOfPeriod(c.u, 4000U));
        CHECK(driver.getLastPhaseDuty().w == ::integration::perMilleOfPeriod(c.w, 4000U));
    }
    // a pair whose state the registers do not tell: refused, the driver does not guess
    setTicks(2000U, 2000U, 2000U);
    group.known[1] = false;
    int const pulsesBefore = dev.enableHighWrites;
    CHECK(driver.enable() == Status::PHASES_MIXED);
    CHECK(dev.enableHighWrites == pulsesBefore);
    group.known[1] = true;
    // a group with two pairs never enables through the source (Integration.cpp attaches it only to three)
    pairCount = 2U;
    CHECK(driver.enable() == Status::PHASES_MIXED);
    pairCount = 3U;
    CHECK(driver.enable() == Status::OK);
    CHECK(group.reads > 0);
}

} // namespace

int main()
{
    struct
    {
        char const* name;
        void (*fn)();
    } const tests[] = {
        {"conversionKeepsTheStateOfEveryOnTime", conversionKeepsTheStateOfEveryOnTime},
        {"theUnitTrapOfTheConsole", theUnitTrapOfTheConsole},
        {"unknownStatesAreUnknown", unknownStatesAreUnknown},
        {"driverWithTheSource", driverWithTheSource},
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
