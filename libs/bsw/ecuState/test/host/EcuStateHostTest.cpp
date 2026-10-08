/* Host test of the ECU state management (libs/bsw/ecuState) without SBC: state machine, controller with a model of
 * the OpenBSW lifecycle (one level per transition step), a fake power supply and the permanent supply of a board
 * without switchable supply, RTE hand-over. Own test profile (times differ from the module defaults).
 * Build and run: libs/bsw/ecuState/test/test_ecustate_host.py (zig c++). */

#include "ecuState/EcuStateController.h"
#include "ecuState/EcuStateMachine.h"
#include "ecuState/EcuStateRte.h"
#include "ecuState/PermanentPowerSupply.h"

#include <interrupts/SuspendResumeAllInterruptsScopedLock.h> // host stub: test/host/stubs

#include <cstdio>
#include <string_view>
#include <vector>

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
using Action         = EcuStateMachine::Action;
using RestartReason  = EcuStateMachine::RestartReason;
using PowerDownResult = IPowerSupply::PowerDownResult;

// Test profile: not the module defaults (5000/2000/5000/2000).
constexpr EcuStateConfig PROFILE{
    1000U,           // min run
    300U,            // post-run
    800U,            // shutdown timeout
    500U,            // sleep timeout
    {100U, 200U},    // SYS-02 step limits: NvM, CAN
    WakeupSelection{wakeup::canBus(0U) | wakeup::local(1U), 0U},
    WakeupDuringShutdown::RESTART,
    AfterWatchdogReset::RUN,
    ShutdownStepError::RESTART};

constexpr uint8_t RUN_LEVEL      = 9U;
constexpr uint8_t SHUTDOWN_LEVEL = 1U;

/* Power supply with scripted answers (e.g. an MCU low-power mode or a SBC seen through the role). */
class FakeSupply final : public IPowerSupply
{
public:
    bool supported                 = true;
    uint32_t startupSources        = wakeup::POWER;
    uint32_t pendingEvents         = 0U;
    PowerDownResult result         = PowerDownResult::STARTED;
    std::vector<uint32_t> powerDownCalls;

    char const* getName() const override { return "fake"; }

    bool isPowerDownSupported() const override { return supported; }

    uint32_t getStartupWakeupSources() const override { return startupSources; }

    uint32_t takeWakeupEvents() override
    {
        uint32_t const e = pendingEvents;
        pendingEvents    = 0U;
        return e;
    }

    uint32_t getSupportedWakeupSources() const override { return supportedSources; }

    uint32_t getCyclicWakeupMs(uint32_t const requestedMs) const override
    {
        return (requestedMs == 0U) ? 0U : (((requestedMs + 63U) / 64U) * 64U); // 64 ms grid
    }

    PowerDownResult powerDown(uint32_t const nowMs, WakeupSelection const& selection) override
    {
        powerDownCalls.push_back(nowMs);
        armed = selection;
        return result;
    }

    uint32_t supportedSources = wakeup::CAN_BUSES | wakeup::LOCALS | wakeup::TIMER;
    WakeupSelection armed{0U, 0U};
};

/* OpenBSW lifecycle as seen by listeners: one level per step, levels 1..9, level 0 = reset. */
class FakeLifecycle final : public ILifecycleControl
{
public:
    uint8_t level  = 0U;
    uint8_t target = 0U;
    bool stuck     = false; // a component never finishes its shutdown
    std::vector<uint8_t> requests;
    EcuStateController* listener = nullptr;

    void transitionToLevel(uint8_t const l) override
    {
        requests.push_back(l);
        target = l;
    }

    void step()
    {
        if (level < target)
        {
            ++level;
            listener->lifecycleLevelReached(level, false);
        }
        else if ((level > target) && !stuck)
        {
            --level;
            listener->lifecycleLevelReached(level, true);
        }
        else
        {
            // level reached
        }
    }

    bool resetRequested() const { return !requests.empty() && (requests.back() == 0U); }
};

struct Rig
{
    FakeLifecycle lifecycle;
    ShutdownSequence sequence; // SYS-02: no provider at all, every run-down step is "not present"
    EcuStateController controller;
    uint32_t nowMs = 0U;

    Rig(EcuStateConfig const& config, IPowerSupply& supply)
    : lifecycle(), sequence(), controller(config, supply, lifecycle, sequence, RUN_LEVEL, SHUTDOWN_LEVEL)
    {
        ::ecustate::rte::reset();
        lifecycle.listener = &controller;
        lifecycle.target   = RUN_LEVEL; // app: transitionToLevel(MaxNumLevels)
    }

    /** 10 ms cycles: lifecycle step, controller cycle. */
    void run(uint32_t const durationMs)
    {
        for (uint32_t t = 0U; t < durationMs; t += 10U)
        {
            nowMs += 10U;
            lifecycle.step();
            controller.cyclic(nowMs);
        }
    }

    State state() const { return controller.getStateMachine().getState(); }

    /** Runs until the state changes or \p limitMs passes; returns the time it took. */
    uint32_t runUntilStateChanges(uint32_t const limitMs)
    {
        State const before = state();
        uint32_t const start = nowMs;
        while ((state() == before) && ((nowMs - start) < limitMs))
        {
            run(10U);
        }
        return nowMs - start;
    }
};

uint8_t rteState()
{
    uint8_t s = 0xFFU;
    ::ecustate::rte::readState(s);
    return s;
}

uint32_t rteWakeup()
{
    uint32_t w = 0xFFFFFFFFU;
    ::ecustate::rte::readWakeupSources(w);
    return w;
}

// ---------------------------------------------------------------------------------------------------------------

void wakeupBitsAndNames()
{
    CHECK(wakeup::canBus(0U) == 0x100U);
    CHECK(wakeup::canBus(7U) == 0x8000U);
    CHECK(wakeup::canBus(8U) == wakeup::UNKNOWN);
    CHECK(wakeup::local(0U) == 0x10000U);
    CHECK(wakeup::local(8U) == wakeup::UNKNOWN);
    CHECK((wakeup::CAN_BUSES & wakeup::LOCALS) == 0U);
    CHECK(((wakeup::POWER | wakeup::RESET | wakeup::WATCHDOG | wakeup::SUPPLY | wakeup::TIMER | wakeup::UNKNOWN)
           & (wakeup::CAN_BUSES | wakeup::LOCALS))
          == 0U);
    CHECK(static_cast<uint8_t>(State::STARTUP) == 0U);
    CHECK(static_cast<uint8_t>(State::SLEEP) == 4U);
    // SYS-02 appended RUNDOWN, the values 0..4 of SYS-01 stay as the RTE knows them
    CHECK(static_cast<uint8_t>(State::RUNDOWN) == 5U);
    CHECK(std::string_view(stateName(State::STANDBY)) == "Standby");
    CHECK(std::string_view(stateName(State::RUNDOWN)) == "Rundown");
    CHECK(std::string_view(shutdownStepName(ShutdownStep::NVM)) == "NvM");
    CHECK(std::string_view(shutdownStepName(ShutdownStep::CAN_STOP)) == "CAN");
    CHECK(std::string_view(stepStateName(StepState::NOT_PRESENT)) == "not present");
    CHECK(std::string_view(shutdownStepName(ShutdownStep::SAFE_OUTPUTS)) == "Out");
    // SYS-174: the safe outputs step is the last one, and only the NvM step is never continued
    CHECK(static_cast<uint8_t>(ShutdownStep::SAFE_OUTPUTS) + 1U == SHUTDOWN_STEP_COUNT);
    CHECK(!isContinuableStep(ShutdownStep::NVM));
    CHECK(isContinuableStep(ShutdownStep::CAN_STOP));
    CHECK(isContinuableStep(ShutdownStep::SAFE_OUTPUTS));
}

void configValidation()
{
    CHECK(isValidEcuStateConfig(PROFILE));
    EcuStateConfig c = PROFILE;
    c.sleepTimeoutMs = POWER_DOWN_DELAY_MS;
    CHECK(!isValidEcuStateConfig(c)); // power down would never be requested before the timeout
    c                   = PROFILE;
    c.shutdownTimeoutMs = 99U;
    CHECK(!isValidEcuStateConfig(c));
    c          = PROFILE;
    c.minRunMs = MAX_TIME_MS + 1U;
    CHECK(!isValidEcuStateConfig(c));
    c           = PROFILE;
    c.postRunMs = 0U;
    c.minRunMs  = 0U;
    CHECK(isValidEcuStateConfig(c));
    // SYS-02: step limits, and TIMER only together with a period
    c                  = PROFILE;
    c.stepTimeoutMs[1] = 9U;
    CHECK(!isValidEcuStateConfig(c));
    c                 = PROFILE;
    c.wakeup.cyclicMs = 100U; // TIMER not selected: the period would do nothing
    CHECK(!isValidEcuStateConfig(c));
    c                = PROFILE;
    c.wakeup.sources = PROFILE.wakeup.sources | wakeup::TIMER; // TIMER without a period
    CHECK(!isValidEcuStateConfig(c));
    c.wakeup.cyclicMs = 64U;
    CHECK(isValidEcuStateConfig(c));
    c                = PROFILE;
    c.wakeup.sources = wakeup::POWER; // no source a supply can arm
    CHECK(!isValidEcuStateConfig(c));
}

void startupReachesRun()
{
    FakeSupply supply;
    supply.startupSources = wakeup::canBus(1U);
    Rig rig(PROFILE, supply);
    rig.run(50U);
    CHECK(rig.state() == State::STARTUP); // lifecycle at level 5
    CHECK(rteState() == static_cast<uint8_t>(State::STARTUP));
    CHECK(rteWakeup() == 0U);
    rig.run(50U);
    CHECK(rig.lifecycle.level == RUN_LEVEL);
    CHECK(rig.state() == State::RUN);
    CHECK(rig.controller.getStateMachine().getStartupWakeupSources() == wakeup::canBus(1U));
    CHECK(rteState() == static_cast<uint8_t>(State::RUN));
    CHECK(rteWakeup() == wakeup::canBus(1U));
    rig.run(10000U);
    CHECK(rig.state() == State::RUN); // no request: stays
    CHECK(rig.lifecycle.requests.empty());
    CHECK(supply.powerDownCalls.empty());
}

void sleepSequence()
{
    FakeSupply supply;
    Rig rig(PROFILE, supply);
    rig.run(100U);
    CHECK(rig.state() == State::RUN);
    uint32_t const runSince = rig.nowMs - rig.controller.getStateMachine().getTimeInStateMs(rig.nowMs);
    ::ecustate::rte::writeSleepRequest(true);
    // minimum run time counts from reaching RUN
    uint32_t const toStandby = rig.runUntilStateChanges(5000U);
    CHECK(rig.state() == State::STANDBY);
    CHECK((rig.nowMs - runSince) >= PROFILE.minRunMs);
    CHECK((rig.nowMs - runSince) <= PROFILE.minRunMs + 10U);
    CHECK(toStandby > 0U);
    CHECK(rteState() == static_cast<uint8_t>(State::STANDBY));
    CHECK(rig.lifecycle.requests.empty());
    // post-run
    uint32_t const postRun = rig.runUntilStateChanges(5000U);
    CHECK(rig.state() == State::RUNDOWN);
    CHECK((postRun >= PROFILE.postRunMs) && (postRun <= PROFILE.postRunMs + 10U));
    CHECK(rig.lifecycle.requests.empty()); // SYS-02: the run-down comes before the lifecycle shutdown
    // run-down: this project has no provider for any step, all are reported as not present
    uint32_t const runDown = rig.runUntilStateChanges(1000U);
    CHECK(rig.state() == State::SHUTDOWN);
    CHECK(runDown <= 40U);
    CHECK(rig.controller.getStateMachine().getStepState(ShutdownStep::NVM) == StepState::NOT_PRESENT);
    CHECK(rig.controller.getStateMachine().getStepState(ShutdownStep::CAN_STOP) == StepState::NOT_PRESENT);
    CHECK(rig.controller.getStateMachine().getStepState(ShutdownStep::SAFE_OUTPUTS) == StepState::NOT_PRESENT);
    CHECK(rig.controller.getStateMachine().getCounters().stepNotPresent == 3U);
    CHECK((rig.lifecycle.requests.size() == 1U) && (rig.lifecycle.requests[0] == SHUTDOWN_LEVEL));
    // lifecycle goes down level by level, SLEEP when level 1 is reached
    rig.runUntilStateChanges(1000U);
    CHECK(rig.state() == State::SLEEP);
    CHECK(rig.lifecycle.level == SHUTDOWN_LEVEL);
    CHECK(supply.powerDownCalls.empty()); // log delay first
    uint32_t const sleepSince = rig.nowMs;
    rig.run(POWER_DOWN_DELAY_MS + 10U);
    CHECK(supply.powerDownCalls.size() == 1U);
    CHECK(!supply.powerDownCalls.empty() && ((supply.powerDownCalls[0] - sleepSince) >= POWER_DOWN_DELAY_MS));
    CHECK(rig.controller.hasPowerDownResult());
    CHECK(rig.controller.getPowerDownResult() == PowerDownResult::STARTED);
    // SYS-02: the supply is asked to arm exactly the selection the project made
    CHECK(supply.armed.sources == PROFILE.wakeup.sources);
    CHECK(supply.armed.cyclicMs == 0U);
    CHECK(rig.controller.getArmedWakeupSources() == PROFILE.wakeup.sources);
    CHECK(rig.controller.getMissingWakeupSources() == 0U);
    CHECK(!rig.lifecycle.resetRequested());
    // the MCU keeps running: sleep timeout -> restart, only once
    rig.run(PROFILE.sleepTimeoutMs);
    CHECK(rig.lifecycle.resetRequested());
    CHECK(rig.controller.getStateMachine().getRestartReason() == RestartReason::SLEEP_TIMEOUT);
    size_t const requests = rig.lifecycle.requests.size();
    rig.run(2000U);
    CHECK(rig.lifecycle.requests.size() == requests);
    CHECK(supply.powerDownCalls.size() == 1U);
    CHECK(rig.controller.getStateMachine().getCounters().standbyEntered == 1U);
}

void standbyReturnsToRunWhenRequestWithdrawn()
{
    FakeSupply supply;
    EcuStateConfig c = PROFILE;
    c.minRunMs       = 0U;
    Rig rig(c, supply);
    rig.run(100U);
    ::ecustate::rte::writeSleepRequest(true);
    rig.run(20U);
    CHECK(rig.state() == State::STANDBY);
    rig.run(PROFILE.postRunMs - 50U);
    ::ecustate::rte::writeSleepRequest(false);
    rig.run(10U);
    CHECK(rig.state() == State::RUN);
    CHECK(rig.controller.getStateMachine().getCounters().standbyCancelled == 1U);
    rig.run(2000U);
    CHECK(rig.state() == State::RUN);
    CHECK(rig.lifecycle.requests.empty());
    // requested again: the post-run starts from the beginning
    ::ecustate::rte::writeSleepRequest(true);
    rig.run(10U);
    CHECK(rig.state() == State::STANDBY);
    uint32_t const postRun = rig.runUntilStateChanges(5000U);
    CHECK((postRun >= PROFILE.postRunMs) && (rig.state() == State::RUNDOWN));
}

/**
 * NVM-26: the published state (rte::publishedState()) follows the state machine every cycle, and
 * isOnWayToPowerDown() is true exactly from STANDBY on - including STANDBY that returns to RUN.
 */
void publishedStateForPowerDownWork()
{
    CHECK(!isOnWayToPowerDown(State::STARTUP));
    CHECK(!isOnWayToPowerDown(State::RUN));
    CHECK(isOnWayToPowerDown(State::STANDBY));
    CHECK(isOnWayToPowerDown(State::RUNDOWN));
    CHECK(isOnWayToPowerDown(State::SHUTDOWN));
    CHECK(isOnWayToPowerDown(State::SLEEP));
    CHECK(isOnWayToPowerDown(static_cast<State>(6U))); // outside the enumeration: the safe side
    CHECK(isOnWayToPowerDown(static_cast<State>(0xFFU)));

    FakeSupply supply;
    EcuStateConfig c = PROFILE;
    c.minRunMs       = 0U;
    Rig rig(c, supply);
    CHECK(::ecustate::rte::publishedState() == State::STARTUP); // before the first cycle
    uint32_t seen     = 0U; // bit per state seen
    uint32_t mismatch = 0U;
    auto cycles       = [&rig, &seen, &mismatch](uint32_t const ms)
    {
        for (uint32_t t = 0U; t < ms; t += 10U)
        {
            rig.run(10U);
            State const p = ::ecustate::rte::publishedState();
            mismatch += (p == rig.state()) ? 0U : 1U;
            seen |= 1UL << static_cast<uint8_t>(p);
        }
    };
    cycles(100U);
    CHECK(::ecustate::rte::publishedState() == State::RUN);
    ::ecustate::rte::writeSleepRequest(true);
    cycles(20U);
    CHECK(::ecustate::rte::publishedState() == State::STANDBY);
    CHECK(isOnWayToPowerDown(::ecustate::rte::publishedState()));
    ::ecustate::rte::writeSleepRequest(false);
    cycles(10U);
    CHECK(::ecustate::rte::publishedState() == State::RUN);
    CHECK(!isOnWayToPowerDown(::ecustate::rte::publishedState()));
    ::ecustate::rte::writeSleepRequest(true);
    for (uint32_t t = 0U; (t < 3000U) && (rig.state() != State::SLEEP); t += 10U)
    {
        cycles(10U); // post-run, run-down, shutdown, sleep
    }
    CHECK(mismatch == 0U);
    CHECK((seen & (1UL << static_cast<uint8_t>(State::RUNDOWN))) != 0U);
    CHECK((seen & (1UL << static_cast<uint8_t>(State::SHUTDOWN))) != 0U);
    CHECK((seen & (1UL << static_cast<uint8_t>(State::SLEEP))) != 0U);
    CHECK(isOnWayToPowerDown(::ecustate::rte::publishedState()));
    ::ecustate::rte::reset();
    CHECK(::ecustate::rte::publishedState() == State::STARTUP);
}

void permanentSupplyRefusesSleep()
{
    // Board without switchable supply (no SBC): sleep requests are refused, the ECU stays in RUN.
    PermanentPowerSupply supply;
    EcuStateConfig c = PROFILE;
    c.minRunMs       = 0U;
    Rig rig(c, supply);
    rig.run(100U);
    CHECK(rig.state() == State::RUN);
    CHECK(rteWakeup() == wakeup::UNKNOWN);
    ::ecustate::rte::writeSleepRequest(true);
    rig.run(5000U);
    CHECK(rig.state() == State::RUN);
    CHECK(rig.controller.getStateMachine().isSleepRefused());
    CHECK(rig.controller.getStateMachine().getCounters().sleepRefused == 1U); // once per request
    CHECK(rig.lifecycle.requests.empty());
    ::ecustate::rte::writeSleepRequest(false);
    rig.run(10U);
    CHECK(!rig.controller.getStateMachine().isSleepRefused());
    rig.controller.setConsoleSleepRequest(true);
    rig.run(100U);
    CHECK(rig.controller.getStateMachine().getCounters().sleepRefused == 2U);
    CHECK(rig.state() == State::RUN);
    CHECK(!rig.controller.hasPowerDownResult());

    PermanentPowerSupply known(wakeup::POWER);
    CHECK(known.getStartupWakeupSources() == wakeup::POWER);
    CHECK(known.powerDown(0U, WakeupSelection{wakeup::canBus(0U), 0U}) == PowerDownResult::NOT_SUPPORTED);
    CHECK(known.getSupportedWakeupSources() == wakeup::NONE);
    CHECK(known.getCyclicWakeupMs(1000U) == 0U);
}

/**
 * SYS-02: a supply that can power down but cannot arm any selected wake-up source must not sleep, the ECU
 * would never come back. A source it does not have is reported, the rest is armed.
 */
void wakeupSourcesDecideWhetherSleepIsPossible()
{
    {
        FakeSupply supply;
        supply.supportedSources = wakeup::local(4U); // e.g. a supply without CAN wake-up
        EcuStateConfig c        = PROFILE;
        c.minRunMs              = 0U;
        Rig rig(c, supply);
        rig.run(100U);
        CHECK(rig.controller.getArmedWakeupSources() == 0U);
        CHECK(rig.controller.getMissingWakeupSources() == PROFILE.wakeup.sources);
        rig.controller.setConsoleSleepRequest(true);
        rig.run(2000U);
        CHECK(rig.state() == State::RUN);
        CHECK(rig.controller.getStateMachine().isSleepRefused());
        CHECK(supply.powerDownCalls.empty());
    }
    {
        // one of the two selected sources exists: sleep works, the other one is reported
        FakeSupply supply;
        supply.supportedSources = wakeup::canBus(0U) | wakeup::TIMER;
        EcuStateConfig c        = PROFILE;
        c.minRunMs              = 0U;
        c.postRunMs             = 0U;
        Rig rig(c, supply);
        rig.run(100U);
        CHECK(rig.controller.getArmedWakeupSources() == wakeup::canBus(0U));
        CHECK(rig.controller.getMissingWakeupSources() == wakeup::local(1U));
        rig.controller.setConsoleSleepRequest(true);
        rig.run(400U);
        CHECK(supply.powerDownCalls.size() == 1U);
        CHECK(supply.armed.sources == wakeup::canBus(0U)); // not the source the board does not have
    }
    {
        // cyclic wake-up: the supply answers with its own grid (64 ms here), the ECU passes that on
        FakeSupply supply;
        EcuStateConfig c    = PROFILE;
        c.minRunMs          = 0U;
        c.postRunMs         = 0U;
        c.wakeup.sources    = wakeup::canBus(0U) | wakeup::TIMER;
        c.wakeup.cyclicMs   = 100U;
        CHECK(isValidEcuStateConfig(c));
        Rig rig(c, supply);
        rig.run(100U);
        CHECK(rig.controller.getCyclicWakeupMs() == 128U);
        rig.controller.setConsoleSleepRequest(true);
        rig.run(400U);
        CHECK(supply.powerDownCalls.size() == 1U);
        CHECK(supply.armed.cyclicMs == 128U);
        CHECK((supply.armed.sources & wakeup::TIMER) != 0U);
    }
}

void supplyLosesPowerDownDuringStandby()
{
    FakeSupply supply;
    EcuStateConfig c = PROFILE;
    c.minRunMs       = 0U;
    Rig rig(c, supply);
    rig.run(100U);
    rig.controller.setConsoleSleepRequest(true);
    rig.run(20U);
    CHECK(rig.state() == State::STANDBY);
    supply.supported = false; // e.g. SBC driver lost Normal mode
    rig.run(10U);
    CHECK(rig.state() == State::RUN);
    CHECK(rig.controller.getStateMachine().isSleepRefused());
    rig.run(2000U);
    CHECK(rig.state() == State::RUN);
    supply.supported = true;
    rig.run(20U);
    CHECK(rig.state() == State::STANDBY);
}

void wakeupDuringStandbyRestartsPostRun()
{
    FakeSupply supply;
    EcuStateConfig c = PROFILE;
    c.minRunMs       = 0U;
    Rig rig(c, supply);
    rig.run(100U);
    ::ecustate::rte::writeSleepRequest(true);
    rig.run(10U);
    CHECK(rig.state() == State::STANDBY);
    rig.run(250U);
    supply.pendingEvents = wakeup::canBus(0U);
    rig.run(10U);
    CHECK(rig.state() == State::STANDBY);
    uint32_t const rest = rig.runUntilStateChanges(5000U);
    CHECK(rest >= PROFILE.postRunMs - 10U); // started again, not the remaining 50 ms
    CHECK(rig.state() == State::RUNDOWN);
    CHECK(rig.controller.getStateMachine().getCounters().postRunRestarted == 1U);
}

void wakeupDuringShutdown()
{
    for (WakeupDuringShutdown const policy : {WakeupDuringShutdown::RESTART, WakeupDuringShutdown::SLEEP})
    {
        FakeSupply supply;
        EcuStateConfig c       = PROFILE;
        c.minRunMs             = 0U;
        c.postRunMs            = 0U;
        c.wakeupDuringShutdown = policy;
        Rig rig(c, supply);
        rig.run(100U);
        ::ecustate::rte::writeSleepRequest(true);
        rig.run(40U); // RUN -> STANDBY -> RUNDOWN (two steps without provider) -> SHUTDOWN
        CHECK(rig.state() == State::SHUTDOWN);
        CHECK(rig.lifecycle.level > SHUTDOWN_LEVEL + 3U);
        supply.pendingEvents = wakeup::local(2U);
        rig.run(10U);
        CHECK(rig.controller.getStateMachine().getWakeupEvents() == wakeup::local(2U));
        ::ecustate::rte::writeSleepRequest(false); // no way back in SHUTDOWN
        rig.run(300U);
        if (policy == WakeupDuringShutdown::RESTART)
        {
            CHECK(rig.lifecycle.resetRequested());
            CHECK(rig.controller.getStateMachine().getRestartReason() == RestartReason::WAKEUP_DURING_SHUTDOWN);
            CHECK(supply.powerDownCalls.empty());
            CHECK(rig.state() == State::SHUTDOWN);
        }
        else
        {
            CHECK(!rig.lifecycle.resetRequested());
            CHECK(rig.state() == State::SLEEP);
            CHECK(supply.powerDownCalls.size() == 1U);
        }
    }
}

void powerDownRefusedRestarts()
{
    for (PowerDownResult const result :
         {PowerDownResult::WAKEUP_PENDING, PowerDownResult::FAILED, PowerDownResult::NOT_SUPPORTED})
    {
        FakeSupply supply;
        supply.result    = result;
        EcuStateConfig c = PROFILE;
        c.minRunMs       = 0U;
        c.postRunMs      = 0U;
        Rig rig(c, supply);
        rig.run(100U);
        ::ecustate::rte::writeSleepRequest(true);
        rig.run(300U);
        CHECK(supply.powerDownCalls.size() == 1U);
        CHECK(rig.lifecycle.resetRequested());
        CHECK(rig.controller.getStateMachine().getRestartReason() == RestartReason::POWER_DOWN_REFUSED);
        CHECK(rig.controller.getPowerDownResult() == result);
        rig.run(1000U);
        CHECK(supply.powerDownCalls.size() == 1U); // not retried
    }
}

void shutdownTimeoutRestarts()
{
    FakeSupply supply;
    EcuStateConfig c = PROFILE;
    c.minRunMs       = 0U;
    c.postRunMs      = 0U;
    Rig rig(c, supply);
    rig.run(100U);
    rig.lifecycle.stuck = true;
    ::ecustate::rte::writeSleepRequest(true);
    rig.run(40U); // RUN -> STANDBY -> RUNDOWN (two steps without provider) -> SHUTDOWN
    CHECK(rig.state() == State::SHUTDOWN);
    // an upward event at the shutdown level (e.g. "lc level 1" from level 0 in a test setup) is not the shutdown
    rig.controller.lifecycleLevelReached(SHUTDOWN_LEVEL, false);
    rig.run(10U);
    CHECK(rig.state() == State::SHUTDOWN);
    CHECK(supply.powerDownCalls.empty());
    rig.run(PROFILE.shutdownTimeoutMs - 30U);
    CHECK(!rig.lifecycle.resetRequested());
    rig.run(20U);
    CHECK(rig.lifecycle.resetRequested());
    CHECK(rig.controller.getStateMachine().getRestartReason() == RestartReason::SHUTDOWN_TIMEOUT);
    CHECK(supply.powerDownCalls.empty());
}

void afterWatchdogReset()
{
    // RUN (default): a watchdog start is an ordinary start
    {
        FakeSupply supply;
        supply.startupSources = wakeup::WATCHDOG;
        Rig rig(PROFILE, supply);
        rig.run(5000U);
        CHECK(rig.state() == State::RUN);
        CHECK(rteWakeup() == wakeup::WATCHDOG);
    }
    // SLEEP: STANDBY after the minimum run time without request, a withdrawn request does not keep it awake
    {
        FakeSupply supply;
        supply.startupSources = wakeup::WATCHDOG | wakeup::canBus(0U);
        EcuStateConfig c      = PROFILE;
        c.afterWatchdogReset  = AfterWatchdogReset::SLEEP;
        Rig rig(c, supply);
        ::ecustate::rte::writeSleepRequest(false);
        rig.run(100U + PROFILE.minRunMs);
        CHECK(rig.state() == State::STANDBY);
        CHECK(rig.controller.getStateMachine().isSleepForced());
        rig.run(PROFILE.postRunMs + 400U);
        CHECK(rig.state() == State::SLEEP);
    }
    // SLEEP, other start reason: no forced sleep
    {
        FakeSupply supply;
        EcuStateConfig c     = PROFILE;
        c.afterWatchdogReset = AfterWatchdogReset::SLEEP;
        Rig rig(c, supply);
        rig.run(5000U);
        CHECK(rig.state() == State::RUN);
    }
    // SLEEP with a supply that cannot power down: back to RUN, not forced any more
    {
        PermanentPowerSupply supply(wakeup::WATCHDOG);
        EcuStateConfig c     = PROFILE;
        c.afterWatchdogReset = AfterWatchdogReset::SLEEP;
        Rig rig(c, supply);
        rig.run(5000U);
        CHECK(rig.state() == State::RUN);
        CHECK(rig.controller.getStateMachine().getCounters().sleepRefused == 1U);
    }
}

void manualLifecycleChangesAreIgnored()
{
    FakeSupply supply;
    Rig rig(PROFILE, supply);
    rig.run(100U);
    CHECK(rig.state() == State::RUN);
    uint32_t const since = rig.controller.getStateMachine().getTimeInStateMs(rig.nowMs);
    // console "lc level 1" and back: no state change, no power down
    rig.lifecycle.target = SHUTDOWN_LEVEL;
    rig.run(200U);
    CHECK(rig.state() == State::RUN);
    rig.lifecycle.target = RUN_LEVEL;
    rig.run(200U);
    CHECK(rig.state() == State::RUN);
    CHECK(rig.controller.getStateMachine().getTimeInStateMs(rig.nowMs) >= since + 400U);
    CHECK(supply.powerDownCalls.empty());
    CHECK(rig.lifecycle.requests.empty());
    // direct calls in the wrong state
    EcuStateMachine m(PROFILE);
    CHECK(m.shutdownLevelReached(0U) == Action::NONE);
    CHECK(m.powerDownResult(PowerDownResult::FAILED, 0U) == Action::NONE);
    CHECK(m.cyclic(0U, true, true, 0U) == Action::NONE); // STARTUP ignores requests
    CHECK(m.getState() == State::STARTUP);
}

void rteAndConsoleRequests()
{
    FakeSupply supply;
    EcuStateConfig c = PROFILE;
    c.minRunMs       = 0U;
    Rig rig(c, supply);
    CHECK(!::ecustate::rte::isSleepRequestConnected());
    rig.run(100U);
    ::ecustate::rte::writeSleepRequest(false);
    CHECK(::ecustate::rte::isSleepRequestConnected());
    rig.controller.setConsoleSleepRequest(true); // console overrides a component that wants to stay awake
    rig.run(20U);
    CHECK(rig.state() == State::STANDBY);
    rig.controller.setConsoleSleepRequest(false);
    rig.run(10U);
    CHECK(rig.state() == State::RUN);
    ::ecustate::rte::writeSleepRequest(true);
    rig.run(10U);
    CHECK(rig.state() == State::STANDBY);
}

/* PLAT-28: the lifecycle merkers are read and cleared under the interrupt lock of the platform (no std::atomic
 * exchange, which needs libatomic on ARMv6-M). The host stub counts the lock and delivers a "pending interrupt" when
 * it is released - the earliest moment a real preemption could set a merker again. */
void lifecycleMerkersAreTakenUnderTheLock()
{
    FakeSupply supply;
    Rig rig(PROFILE, supply);
    ::ecustate_test::lockMaxDepth = 0U;
    unsigned const before         = ::ecustate_test::lockCount;
    rig.controller.cyclic(10U);
    CHECK(::ecustate_test::lockCount == before + 2U); // run level and shutdown level merker
    CHECK(::ecustate_test::lockDepth == 0U);
    CHECK(::ecustate_test::lockMaxDepth == 1U);

    // The run level arrives right when the first take releases the lock: it must survive until the next cycle.
    ::ecustate_test::onUnlock = [&rig] { rig.controller.lifecycleLevelReached(RUN_LEVEL, false); };
    rig.controller.cyclic(20U);
    CHECK(!::ecustate_test::onUnlock);
    CHECK(rig.state() == State::STARTUP);
    rig.controller.cyclic(30U);
    CHECK(rig.state() == State::RUN);

    rig.controller.cyclic(40U);
    CHECK(rig.state() == State::RUN);
    CHECK(rig.lifecycle.requests.empty());
    ::ecustate_test::onUnlock = nullptr;
}

/* PLAT-28: a merker is consumed when it is read. A shutdown level reached in RUN (console "lc level 1") is ignored
 * and must not stay pending: a later sleep would otherwise take it for the end of its own shutdown and power down
 * while the lifecycle is still stuck above the shutdown level. */
void ignoredMerkerDoesNotStayPending()
{
    FakeSupply supply;
    EcuStateConfig c = PROFILE;
    c.minRunMs       = 0U;
    c.postRunMs      = 0U;
    Rig rig(c, supply);
    rig.run(100U);
    CHECK(rig.state() == State::RUN);
    rig.controller.lifecycleLevelReached(SHUTDOWN_LEVEL, true);
    rig.run(10U);
    CHECK(rig.state() == State::RUN);
    rig.lifecycle.stuck = true;
    ::ecustate::rte::writeSleepRequest(true);
    rig.run(40U); // RUN -> STANDBY -> RUNDOWN (two steps without provider) -> SHUTDOWN
    CHECK(rig.state() == State::SHUTDOWN);
    rig.run(POWER_DOWN_DELAY_MS + 50U);
    CHECK(rig.state() == State::SHUTDOWN);
    CHECK(supply.powerDownCalls.empty());
}

} // namespace

int main()
{
    struct
    {
        char const* name;
        void (*fn)();
    } const tests[] = {
        {"wakeupBitsAndNames", wakeupBitsAndNames},
        {"configValidation", configValidation},
        {"startupReachesRun", startupReachesRun},
        {"sleepSequence", sleepSequence},
        {"standbyReturnsToRunWhenRequestWithdrawn", standbyReturnsToRunWhenRequestWithdrawn},
        {"publishedStateForPowerDownWork", publishedStateForPowerDownWork},
        {"permanentSupplyRefusesSleep", permanentSupplyRefusesSleep},
        {"wakeupSourcesDecideWhetherSleepIsPossible", wakeupSourcesDecideWhetherSleepIsPossible},
        {"supplyLosesPowerDownDuringStandby", supplyLosesPowerDownDuringStandby},
        {"wakeupDuringStandbyRestartsPostRun", wakeupDuringStandbyRestartsPostRun},
        {"wakeupDuringShutdown", wakeupDuringShutdown},
        {"powerDownRefusedRestarts", powerDownRefusedRestarts},
        {"shutdownTimeoutRestarts", shutdownTimeoutRestarts},
        {"afterWatchdogReset", afterWatchdogReset},
        {"manualLifecycleChangesAreIgnored", manualLifecycleChangesAreIgnored},
        {"rteAndConsoleRequests", rteAndConsoleRequests},
        {"lifecycleMerkersAreTakenUnderTheLock", lifecycleMerkersAreTakenUnderTheLock},
        {"ignoredMerkerDoesNotStayPending", ignoredMerkerDoesNotStayPending},
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
