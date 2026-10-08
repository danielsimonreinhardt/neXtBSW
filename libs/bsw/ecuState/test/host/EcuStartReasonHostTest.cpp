/* SYS-18 host test: the start reasons of the ECU state management from two sources - the role power_supply and
 * the reset status of the MCU (ecuState/McuStartReasons.h). Board finding 2026-09-19: after "safety wdg" the ECU
 * state management reported "wake-up sources 0x00000002" (RESET), because the SBC with the debug jumper open does
 * not see a reset of the MCU alone, while bsw.reset.reason already said WATCHDOG from RCM_SRS.
 *
 * Covers a board with a supply device, a board without one (PermanentPowerSupply) and a family that reports no
 * reset status at all; no register and no platform is involved.
 * Build and run: libs/bsw/ecuState/test/test_ecustate_host.py (zig c++). */

#include "ecuState/EcuStateController.h"
#include "ecuState/EcuStateMachine.h"
#include "ecuState/EcuStateRte.h"
#include "ecuState/McuStartReasons.h"
#include "ecuState/PermanentPowerSupply.h"

#include <interrupts/SuspendResumeAllInterruptsScopedLock.h> // host stub: test/host/stubs

#include <cstdio>

namespace
{
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

using namespace ::ecustate;

constexpr uint8_t RUN_LEVEL      = 9U;
constexpr uint8_t SHUTDOWN_LEVEL = 1U;

constexpr EcuStateConfig PROFILE{
    1000U,
    300U,
    800U,
    500U,
    {100U, 200U},
    WakeupSelection{wakeup::canBus(0U), 0U},
    WakeupDuringShutdown::RESTART,
    AfterWatchdogReset::RUN,
    ShutdownStepError::RESTART};

/* Ranking of the reader of these bits (bswservice::reset::reasonOf, RTE-05/RTE-16). Repeated here on purpose: the
 * BSW service sits above the RTE and cannot be linked into a test of libs/bsw. That both agree is checked where
 * both exist, in platforms/s32k1xx/port/mcuResetReason/test/host/BswServiceProvidersHostTest.cpp. */
enum class Reason : uint8_t
{
    UNKNOWN,
    POWER_ON,
    RESET,
    WATCHDOG,
    SUPPLY,
    WAKEUP,
};

Reason reasonOf(uint32_t const sources)
{
    if ((sources & wakeup::WATCHDOG) != 0U)
    {
        return Reason::WATCHDOG;
    }
    if ((sources & wakeup::SUPPLY) != 0U)
    {
        return Reason::SUPPLY;
    }
    if ((sources & (wakeup::CAN_BUSES | wakeup::LOCALS | wakeup::TIMER)) != 0U)
    {
        return Reason::WAKEUP;
    }
    if ((sources & wakeup::POWER) != 0U)
    {
        return Reason::POWER_ON;
    }
    if ((sources & wakeup::RESET) != 0U)
    {
        return Reason::RESET;
    }
    return Reason::UNKNOWN;
}

/** Supply device seen through the role, with the start reasons of the board case under test. */
class FakeSupply final : public IPowerSupply
{
public:
    explicit FakeSupply(uint32_t const startup) : _startup(startup) {}

    char const* getName() const override { return "fake"; }

    bool isPowerDownSupported() const override { return true; }

    uint32_t getStartupWakeupSources() const override { return _startup; }

    uint32_t takeWakeupEvents() override { return 0U; }

    uint32_t getSupportedWakeupSources() const override { return wakeup::CAN_BUSES | wakeup::LOCALS; }

    uint32_t getCyclicWakeupMs(uint32_t const) const override { return 0U; }

    PowerDownResult powerDown(uint32_t const, WakeupSelection const&) override
    {
        return PowerDownResult::STARTED;
    }

private:
    uint32_t _startup;
};

class FakeLifecycle final : public ILifecycleControl
{
public:
    uint8_t level                = 0U;
    uint8_t target               = RUN_LEVEL;
    EcuStateController* listener = nullptr;

    void transitionToLevel(uint8_t const l) override { target = l; }

    void step()
    {
        if (level < target)
        {
            ++level;
            listener->lifecycleLevelReached(level, false);
        }
        else if (level > target)
        {
            --level;
            listener->lifecycleLevelReached(level, true);
        }
    }
};

/** Runs one start up to the run level and returns the start reasons the ECU state management published. */
struct Rig
{
    FakeLifecycle lifecycle;
    ShutdownSequence sequence;
    EcuStateController controller;

    Rig(EcuStateConfig const& config, IPowerSupply& supply)
    : lifecycle(), sequence(), controller(config, supply, lifecycle, sequence, RUN_LEVEL, SHUTDOWN_LEVEL)
    {
        ::ecustate::rte::reset();
        lifecycle.listener = &controller;
    }

    void startUp()
    {
        for (uint32_t t = 0U; t < 200U; t += 10U)
        {
            lifecycle.step();
            controller.cyclic(t + 10U);
        }
    }

    uint32_t startReasons() const { return controller.getStateMachine().getStartupWakeupSources(); }
};

uint32_t rteWakeup()
{
    uint32_t w = 0xFFFFFFFFU;
    ::ecustate::rte::readWakeupSources(w);
    return w;
}

// ---- the registered family hook

uint32_t mcuReasons = wakeup::NONE;

uint32_t mcuSource() { return mcuReasons; }

// ---------------------------------------------------------------------------------------------------------------

/** The union itself: nothing is lost, wakeup::UNKNOWN gives way, the ranking of the reader is kept. */
void combineKeepsTheRanking()
{
    CHECK(combineStartReasons(wakeup::NONE, wakeup::NONE) == wakeup::NONE);
    CHECK(combineStartReasons(wakeup::POWER, wakeup::NONE) == wakeup::POWER);   // family without reset status
    CHECK(combineStartReasons(wakeup::NONE, wakeup::POWER) == wakeup::POWER);   // supply not up yet
    CHECK(combineStartReasons(wakeup::UNKNOWN, wakeup::NONE) == wakeup::UNKNOWN); // neither of them knows
    CHECK(combineStartReasons(wakeup::UNKNOWN, wakeup::WATCHDOG) == wakeup::WATCHDOG); // UNKNOWN gives way
    CHECK(combineStartReasons(wakeup::RESET, wakeup::WATCHDOG) == (wakeup::RESET | wakeup::WATCHDOG));

    // The board case of 2026-09-19 and its neighbours, read the way bsw.reset reads them
    CHECK(reasonOf(combineStartReasons(wakeup::RESET, wakeup::WATCHDOG)) == Reason::WATCHDOG); // MCU watchdog
    CHECK(reasonOf(combineStartReasons(wakeup::WATCHDOG, wakeup::RESET)) == Reason::WATCHDOG); // SBC watchdog
    CHECK(reasonOf(combineStartReasons(wakeup::RESET, wakeup::SUPPLY)) == Reason::SUPPLY);     // brown-out
    // a CAN wake-up with the MCU supply off looks like a power-on to the MCU: the wake-up wins
    CHECK(reasonOf(combineStartReasons(wakeup::canBus(0U), wakeup::POWER)) == Reason::WAKEUP);
    CHECK(reasonOf(combineStartReasons(wakeup::RESET, wakeup::POWER)) == Reason::POWER_ON);
    CHECK(reasonOf(combineStartReasons(wakeup::UNKNOWN, wakeup::RESET)) == Reason::RESET);
    CHECK(reasonOf(combineStartReasons(wakeup::UNKNOWN, wakeup::NONE)) == Reason::UNKNOWN);
}

/** Without a registered family the ECU state management works exactly as before SYS-18. */
void familyWithoutResetStatus()
{
    setMcuStartReasonSource(nullptr);
    FakeSupply supply(wakeup::RESET);
    Rig rig(PROFILE, supply);
    rig.startUp();
    CHECK(mcuStartReasons() == wakeup::NONE);
    CHECK(rig.startReasons() == wakeup::RESET);
    CHECK(rig.controller.getMcuStartReasons() == wakeup::NONE);
    CHECK(rteWakeup() == wakeup::RESET);
}

/** Board finding 2026-09-19: SBC "power-on, MCU reset alone", MCU "watchdog" - the watchdog must show up. */
void mcuWatchdogBehindASupplyThatDoesNotSeeIt()
{
    setMcuStartReasonSource(&mcuSource);
    mcuReasons = wakeup::WATCHDOG;
    FakeSupply supply(wakeup::RESET);
    Rig rig(PROFILE, supply);
    rig.startUp();
    CHECK(rig.startReasons() == (wakeup::RESET | wakeup::WATCHDOG));
    CHECK(rig.controller.getMcuStartReasons() == wakeup::WATCHDOG);
    CHECK(rteWakeup() == (wakeup::RESET | wakeup::WATCHDOG)); // ecu.wakeup and ecu.wakeup.WATCHDOG
    CHECK(reasonOf(rig.startReasons()) == Reason::WATCHDOG);  // the same reason bsw.reset reports
    setMcuStartReasonSource(nullptr);
}

/** Board without any supply device: PermanentPowerSupply says UNKNOWN, the MCU knows (Prueffrage 1). */
void boardWithoutSupplyDevice()
{
    setMcuStartReasonSource(&mcuSource);
    mcuReasons = wakeup::WATCHDOG;
    PermanentPowerSupply supply; // default start reasons: wakeup::UNKNOWN
    Rig rig(PROFILE, supply);
    rig.startUp();
    CHECK(rig.startReasons() == wakeup::WATCHDOG); // no UNKNOWN left over
    CHECK(reasonOf(rig.startReasons()) == Reason::WATCHDOG);

    mcuReasons = wakeup::POWER;
    PermanentPowerSupply supply2;
    Rig rig2(PROFILE, supply2);
    rig2.startUp();
    CHECK(rig2.startReasons() == wakeup::POWER);
    CHECK(reasonOf(rig2.startReasons()) == Reason::POWER_ON);

    // Family without reset status on such a board: unchanged, the ECU cannot tell
    setMcuStartReasonSource(nullptr);
    PermanentPowerSupply supply3;
    Rig rig3(PROFILE, supply3);
    rig3.startUp();
    CHECK(rig3.startReasons() == wakeup::UNKNOWN);
    CHECK(reasonOf(rig3.startReasons()) == Reason::UNKNOWN);
}

/** The supply keeps its own reasons: a wake-up of the supply is not overwritten by the MCU's power-on. */
void supplyReasonsSurvive()
{
    setMcuStartReasonSource(&mcuSource);
    mcuReasons = wakeup::POWER; // MCU supply was off during the sleep
    FakeSupply supply(wakeup::canBus(0U));
    Rig rig(PROFILE, supply);
    rig.startUp();
    CHECK(rig.startReasons() == (wakeup::canBus(0U) | wakeup::POWER));
    CHECK(reasonOf(rig.startReasons()) == Reason::WAKEUP);
    setMcuStartReasonSource(nullptr);
}

/** after_watchdog_reset: sleep now also answers to a watchdog reset only the MCU saw (functional safety, SYS-18). */
void afterWatchdogResetUsesBothSources()
{
    EcuStateConfig config = PROFILE;
    config.afterWatchdogReset = AfterWatchdogReset::SLEEP;

    setMcuStartReasonSource(&mcuSource);
    mcuReasons = wakeup::WATCHDOG;
    FakeSupply supply(wakeup::RESET);
    Rig rig(config, supply);
    rig.startUp();
    CHECK(rig.controller.getStateMachine().isSleepForced());

    // The same start without the MCU source: the watchdog stays invisible, no forced sleep (the old behaviour)
    setMcuStartReasonSource(nullptr);
    FakeSupply supply2(wakeup::RESET);
    Rig rig2(config, supply2);
    rig2.startUp();
    CHECK(!rig2.controller.getStateMachine().isSleepForced());
}

} // namespace

int main()
{
    struct Test
    {
        char const* name;
        void (*fn)();
    } const tests[] = {
        {"combineKeepsTheRanking", combineKeepsTheRanking},
        {"familyWithoutResetStatus", familyWithoutResetStatus},
        {"mcuWatchdogBehindASupplyThatDoesNotSeeIt", mcuWatchdogBehindASupplyThatDoesNotSeeIt},
        {"boardWithoutSupplyDevice", boardWithoutSupplyDevice},
        {"supplyReasonsSurvive", supplyReasonsSurvive},
        {"afterWatchdogResetUsesBothSources", afterWatchdogResetUsesBothSources},
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
