/* Host test of the supervised restart (SYS-173, safety review 2026-10-02, SYS69-S4, finding ECS-F01): every SM-53
 * reaction requests the lifecycle restart level 0; a lifecycle that never carries it out (a component that never
 * finishes its shutdown) is ended by the direct reset of the family port (ecuState/DirectReset.h) after
 * EcuStateConfig::shutdownTimeoutMs - once, not earlier, and not when the restart completes.
 * Model of the OpenBSW lifecycle that can hang, fake supply, no hardware.
 * Build and run: libs/bsw/ecuState/test/test_ecustate_host.py (zig c++). */

#include "ecuState/DirectReset.h"
#include "ecuState/EcuStateController.h"
#include "ecuState/EcuStateMachine.h"
#include "ecuState/EcuStateRte.h"

#include <cstdio>
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
using PowerDownResult = IPowerSupply::PowerDownResult;
using RestartReason   = EcuStateMachine::RestartReason;

constexpr uint8_t RUN_LEVEL      = 9U;
constexpr uint8_t SHUTDOWN_LEVEL = 1U;
constexpr uint32_t CYCLE_MS      = 10U;

// Own test profile: no minimum run, no post-run; shutdown time-out 300 ms (not the module default 5000 ms).
constexpr EcuStateConfig PROFILE{
    0U,   // min run
    0U,   // post-run
    300U, // shutdown timeout: also the bound of the restart (SYS-173)
    200U, // sleep timeout
    {100U, 50U, SAFE_OUTPUTS_STEP_TIMEOUT_MS},
    WakeupSelection{wakeup::canBus(0U), 0U},
    WakeupDuringShutdown::RESTART,
    AfterWatchdogReset::RUN,
    ShutdownStepError::RESTART};

// The family hook as the port registers it; on a target it does not return.
uint32_t directResets   = 0U;
uint32_t directResetAtMs = 0U;
uint32_t clockMs        = 0U;

void countingDirectReset()
{
    ++directResets;
    directResetAtMs = clockMs;
}

class FakeSupply final : public IPowerSupply
{
public:
    PowerDownResult result = PowerDownResult::STARTED;
    uint32_t events        = 0U;
    std::vector<uint32_t> powerDownCalls;

    char const* getName() const override { return "fake"; }

    bool isPowerDownSupported() const override { return true; }

    uint32_t getStartupWakeupSources() const override { return wakeup::POWER; }

    uint32_t takeWakeupEvents() override
    {
        uint32_t const e = events;
        events           = 0U;
        return e;
    }

    uint32_t getSupportedWakeupSources() const override { return wakeup::CAN_BUSES; }

    uint32_t getCyclicWakeupMs(uint32_t const requestedMs) const override { return requestedMs; }

    PowerDownResult powerDown(uint32_t const nowMs, WakeupSelection const& /* selection */) override
    {
        powerDownCalls.push_back(nowMs);
        return result;
    }
};

/** Run-down step that never finishes (answers RUNNING until its time limit cuts it off). */
class HangingStep final : public IShutdownStep
{
public:
    uint32_t aborts = 0U;

    char const* getName() const override { return "hang"; }

    Result start(uint32_t) override { return Result::RUNNING; }

    Result poll(uint32_t) override { return Result::RUNNING; }

    char const* getFailureReason() const override { return "-"; }

    void abort(uint32_t) override { ++aborts; }
};

/**
 * OpenBSW lifecycle model. Goes one level per cycle towards the target; stuckAtLevel != 0xFF models a component of
 * that level that never finishes its shutdown: the lifecycle stays there and never carries out a new target.
 */
class Lifecycle final : public ILifecycleControl
{
public:
    uint8_t level        = 0U;
    uint8_t target       = RUN_LEVEL;
    uint8_t stuckAtLevel = 0xFFU;
    std::vector<uint8_t> requests;
    EcuStateController* listener = nullptr;

    void transitionToLevel(uint8_t const l) override
    {
        requests.push_back(l);
        target = l;
    }

    void step()
    {
        if ((level != target) && (level != stuckAtLevel))
        {
            bool const down = level > target;
            level           = down ? static_cast<uint8_t>(level - 1U) : static_cast<uint8_t>(level + 1U);
            listener->lifecycleLevelReached(level, down);
        }
    }

    uint32_t restartRequests() const
    {
        uint32_t n = 0U;
        for (uint8_t const r : requests)
        {
            n += (r == EcuStateController::RESTART_LEVEL) ? 1U : 0U;
        }
        return n;
    }
};

struct Rig
{
    ShutdownSequence sequence;
    Lifecycle lifecycle;
    EcuStateController controller;
    bool ecuComponentRunning = true; // false once the lifecycle took level 1 (ecuState's own level) down

    Rig(EcuStateConfig const& config, IPowerSupply& supply)
    : controller(config, supply, lifecycle, sequence, RUN_LEVEL, SHUTDOWN_LEVEL)
    {
        ::ecustate::rte::reset();
        lifecycle.listener = &controller;
        clockMs            = 0U;
        directResets       = 0U;
        directResetAtMs    = 0U;
    }

    void run(uint32_t const durationMs)
    {
        for (uint32_t t = 0U; t < durationMs; t += CYCLE_MS)
        {
            clockMs += CYCLE_MS;
            lifecycle.step();
            // EcuStateSystem::shutdown() cancels the cycle when level 1 is taken down (way to level 0)
            ecuComponentRunning = ecuComponentRunning && (lifecycle.level >= SHUTDOWN_LEVEL);
            if (ecuComponentRunning)
            {
                controller.cyclic(clockMs);
            }
        }
    }

    /** RUN -> run-down -> SHUTDOWN, the lifecycle hanging at \p stuckAt (0xFF: no hang). */
    void sleepRequested(uint8_t const stuckAt)
    {
        run(150U); // up to the run level first
        lifecycle.stuckAtLevel = stuckAt;
        controller.setConsoleSleepRequest(true);
    }

    EcuStateMachine const& machine() const { return controller.getStateMachine(); }
};

// ---------------------------------------------------------------------------------------------------------------

/**
 * The finding itself: a level-5 component never finishes its shutdown. The shutdown time-out requests the restart,
 * the lifecycle cannot carry it out (still waiting for that component), so the restart never happens on its own.
 * The controller resets directly after the shutdown time-out, once.
 */
void lifecycleThatNeverCompletesIsEndedByTheDirectReset()
{
    setDirectReset(&countingDirectReset);
    FakeSupply supply;
    Rig rig(PROFILE, supply);
    rig.sleepRequested(5U);
    rig.run(100U);
    CHECK(rig.machine().getState() == State::SHUTDOWN);
    CHECK(rig.lifecycle.level == 5U); // stuck
    // shutdown time-out: restart requested once, not carried out
    for (uint32_t t = 0U; (t < 2U * PROFILE.shutdownTimeoutMs) && !rig.controller.isRestartRequested(); t += CYCLE_MS)
    {
        rig.run(CYCLE_MS);
    }
    uint32_t const requestedAt = clockMs;
    CHECK(rig.machine().getRestartReason() == RestartReason::SHUTDOWN_TIMEOUT);
    CHECK(rig.controller.isRestartRequested());
    CHECK(rig.lifecycle.restartRequests() == 1U);
    CHECK(rig.lifecycle.level == 5U);
    // the lifecycle gets the shutdown time-out for the restart, not a cycle less ...
    rig.run(PROFILE.shutdownTimeoutMs - CYCLE_MS);
    CHECK(directResets == 0U);
    CHECK(!rig.controller.isDirectResetIssued());
    // ... and not a cycle more
    rig.run(2U * CYCLE_MS);
    CHECK(directResets == 1U);
    CHECK(rig.controller.isDirectResetIssued());
    CHECK((directResetAtMs - requestedAt) >= PROFILE.shutdownTimeoutMs);
    CHECK((directResetAtMs - requestedAt) <= PROFILE.shutdownTimeoutMs + CYCLE_MS);
    // the host hook returns: it is not called again, and the lifecycle is not asked a second time
    rig.run(5000U);
    CHECK(directResets == 1U);
    CHECK(rig.lifecycle.restartRequests() == 1U);
    CHECK(supply.powerDownCalls.empty());
    setDirectReset(nullptr);
}

/** Every SM-53 reaction is supervised the same way: run-down step, refused power down, wake-up, sleep time-out. */
void everyReactionIsSupervised()
{
    setDirectReset(&countingDirectReset);
    // run-down step times out (step_error restart); the lifecycle hangs at the run level (an application component)
    {
        FakeSupply supply;
        HangingStep hang;
        Rig rig(PROFILE, supply);
        rig.sequence.set(ShutdownStep::CAN_STOP, hang);
        rig.sleepRequested(RUN_LEVEL);
        rig.run(1000U);
        CHECK(rig.machine().getRestartReason() == RestartReason::RUN_DOWN_STEP_FAILED);
        CHECK(hang.aborts == 1U); // SYS-174: told before the restart request
        CHECK(directResets == 1U);
    }
    // the supply refuses the power down; the lifecycle hangs below the shutdown level
    {
        directResets = 0U;
        FakeSupply supply;
        supply.result = PowerDownResult::FAILED;
        Rig rig(PROFILE, supply);
        rig.sleepRequested(SHUTDOWN_LEVEL);
        rig.run(2000U);
        CHECK(rig.machine().getRestartReason() == RestartReason::POWER_DOWN_REFUSED);
        CHECK(directResets == 1U);
    }
    // wake-up during the shutdown
    {
        directResets = 0U;
        FakeSupply supply;
        Rig rig(PROFILE, supply);
        rig.sleepRequested(SHUTDOWN_LEVEL);
        rig.run(30U);
        supply.events = wakeup::canBus(0U);
        rig.run(2000U);
        CHECK(rig.machine().getRestartReason() == RestartReason::WAKEUP_DURING_SHUTDOWN);
        CHECK(directResets == 1U);
    }
    // the power down does not take effect: sleep time-out
    {
        directResets = 0U;
        FakeSupply supply;
        Rig rig(PROFILE, supply);
        rig.sleepRequested(SHUTDOWN_LEVEL);
        rig.run(2000U);
        CHECK(rig.machine().getRestartReason() == RestartReason::SLEEP_TIMEOUT);
        CHECK(directResets == 1U);
    }
    setDirectReset(nullptr);
}

/**
 * A restart the lifecycle carries out in time ends with the ECU state management's own level: its cycle stops
 * (EcuStateSystem::shutdown()), the IdleHandler resets - the direct reset is never needed.
 */
void restartThatCompletesNeedsNoDirectReset()
{
    setDirectReset(&countingDirectReset);
    FakeSupply supply;
    supply.result = PowerDownResult::FAILED;
    Rig rig(PROFILE, supply);
    rig.sleepRequested(0xFFU);
    rig.run(5000U);
    CHECK(rig.controller.isRestartRequested());
    CHECK(rig.lifecycle.level == 0U);
    CHECK(!rig.ecuComponentRunning);
    CHECK(directResets == 0U);
    CHECK(!rig.controller.isDirectResetIssued());
    setDirectReset(nullptr);
}

/** Without a restart nothing is supervised: a long sleep that never ends is the sleep time-out's business. */
void noRestartNoDirectReset()
{
    setDirectReset(&countingDirectReset);
    FakeSupply supply;
    Rig rig(PROFILE, supply);
    rig.run(10000U); // RUN for a long time
    CHECK(rig.machine().getState() == State::RUN);
    CHECK(!rig.controller.isRestartRequested());
    CHECK(directResets == 0U);
    setDirectReset(nullptr);
}

/**
 * A family without the hook (or a port that has not registered it yet): the overdue restart is visible
 * (isDirectResetIssued()) and nothing is called through a null pointer.
 */
void familyWithoutHookShowsTheOverdueRestart()
{
    setDirectReset(nullptr);
    CHECK(directReset() == nullptr);
    FakeSupply supply;
    Rig rig(PROFILE, supply);
    rig.sleepRequested(5U);
    rig.run(3000U);
    CHECK(rig.controller.isRestartRequested());
    CHECK(rig.controller.isDirectResetIssued());
    CHECK(directResets == 0U);
    CHECK(rig.lifecycle.restartRequests() == 1U);
}

} // namespace

int main()
{
    struct
    {
        char const* name;
        void (*fn)();
    } const tests[] = {
        {"lifecycleThatNeverCompletesIsEndedByTheDirectReset", lifecycleThatNeverCompletesIsEndedByTheDirectReset},
        {"everyReactionIsSupervised", everyReactionIsSupervised},
        {"restartThatCompletesNeedsNoDirectReset", restartThatCompletesNeedsNoDirectReset},
        {"noRestartNoDirectReset", noRestartNoDirectReset},
        {"familyWithoutHookShowsTheOverdueRestart", familyWithoutHookShowsTheOverdueRestart},
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
