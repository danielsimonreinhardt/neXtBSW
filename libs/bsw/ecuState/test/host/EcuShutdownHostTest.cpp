/* Host test of the run-down before the sleep (SYS-02, libs/bsw/ecuState): order of the steps, a step without
 * provider ("not present", the state of the NvM step while NVM-01 is blocked), a step that fails, a step that
 * runs into its own time limit, both policies for a step error, and a board without switchable supply that
 * never gets there. SYS-174: the safe outputs step last, IShutdownStep::abort() of a timed-out step, and no
 * step_error continue past the NvM step. Scripted steps, model of the OpenBSW lifecycle, no hardware.
 * Build and run: libs/bsw/ecuState/test/test_ecustate_host.py (zig c++). */

#include "ecuState/EcuStateController.h"
#include "ecuState/EcuStateMachine.h"
#include "ecuState/EcuStateRte.h"
#include "ecuState/PermanentPowerSupply.h"

#include <cstdio>
#include <string>
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
using Action          = EcuStateMachine::Action;
using PowerDownResult = IPowerSupply::PowerDownResult;

constexpr uint8_t RUN_LEVEL      = 9U;
constexpr uint8_t SHUTDOWN_LEVEL = 1U;

// Own test profile: no minimum run time and no post-run, so every test reaches the run-down at once.
// Step limits 100 ms (NvM) and 50 ms (CAN) are not the module defaults (1000/500).
constexpr EcuStateConfig PROFILE{
    0U,             // min run
    0U,             // post-run
    800U,           // shutdown timeout
    500U,           // sleep timeout
    {100U, 50U, SAFE_OUTPUTS_STEP_TIMEOUT_MS}, // step limits: NvM, CAN, safe outputs (SYS-174)
    WakeupSelection{wakeup::canBus(0U), 0U},
    WakeupDuringShutdown::SLEEP, // the wake-up policy is not what this test is about
    AfterWatchdogReset::RUN,
    ShutdownStepError::RESTART};

/** Step with a scripted answer; records the order of the calls of the whole run-down. */
class ScriptedStep final : public IShutdownStep
{
public:
    ScriptedStep(char const* const name, std::vector<std::string>& log) : _name(name), _log(log) {}

    uint32_t runningCycles = 0U; // answers RUNNING this often before it finishes
    Result finish          = Result::DONE;
    char const* reason     = "-";
    uint32_t startedAtMs   = 0U;
    uint32_t polls         = 0U;
    uint32_t starts        = 0U;
    uint32_t aborts        = 0U; // SYS-174
    uint32_t abortedAtMs   = 0U;
    uint32_t callsAfterAbort = 0U;

    char const* getName() const override { return _name; }

    void abort(uint32_t const nowMs) override
    {
        ++aborts;
        abortedAtMs = nowMs;
        _log.push_back(std::string(_name) + " abort");
    }

    Result start(uint32_t const nowMs) override
    {
        callsAfterAbort += aborts;
        ++starts;
        startedAtMs = nowMs;
        _log.push_back(std::string(_name) + " start");
        return answer();
    }

    Result poll(uint32_t const nowMs) override
    {
        callsAfterAbort += aborts;
        ++polls;
        (void)nowMs;
        return answer();
    }

    char const* getFailureReason() const override { return reason; }

private:
    Result answer()
    {
        if (_seen < runningCycles)
        {
            ++_seen;
            return Result::RUNNING;
        }
        _log.push_back(std::string(_name) + ((finish == Result::DONE) ? " done" : " failed"));
        return finish;
    }

    char const* _name;
    std::vector<std::string>& _log;
    uint32_t _seen = 0U;
};

class FakeSupply final : public IPowerSupply
{
public:
    bool supported                  = true;
    uint32_t supportedSources       = wakeup::CAN_BUSES | wakeup::LOCALS | wakeup::TIMER;
    PowerDownResult result          = PowerDownResult::STARTED;
    std::vector<uint32_t> powerDownCalls;

    char const* getName() const override { return "fake"; }

    bool isPowerDownSupported() const override { return supported; }

    uint32_t getStartupWakeupSources() const override { return wakeup::POWER; }

    uint32_t pendingEvents = 0U;

    uint32_t takeWakeupEvents() override
    {
        uint32_t const e = pendingEvents;
        pendingEvents    = 0U;
        return e;
    }

    uint32_t getSupportedWakeupSources() const override { return supportedSources; }

    uint32_t getCyclicWakeupMs(uint32_t const requestedMs) const override { return requestedMs; }

    PowerDownResult powerDown(uint32_t const nowMs, WakeupSelection const& /* selection */) override
    {
        powerDownCalls.push_back(nowMs);
        return result;
    }
};

class FakeLifecycle final : public ILifecycleControl
{
public:
    uint8_t level  = 0U;
    uint8_t target = RUN_LEVEL;
    std::vector<uint8_t> requests;
    EcuStateController* listener = nullptr;

    void transitionToLevel(uint8_t const l) override
    {
        requests.push_back(l);
        target = l;
    }

    void step()
    {
        if (level != target)
        {
            bool const down = level > target;
            level           = down ? static_cast<uint8_t>(level - 1U) : static_cast<uint8_t>(level + 1U);
            listener->lifecycleLevelReached(level, down);
        }
    }

    bool resetRequested() const { return !requests.empty() && (requests.back() == 0U); }
};

struct Rig
{
    std::vector<std::string> log;
    ScriptedStep nvm{"NvM", log};
    ScriptedStep can{"CAN", log};
    ScriptedStep outputs{"Out", log}; // SYS-174
    ShutdownSequence sequence;
    FakeLifecycle lifecycle;
    EcuStateController controller;
    uint32_t nowMs = 0U;

    Rig(EcuStateConfig const& config, IPowerSupply& supply)
    : controller(config, supply, lifecycle, sequence, RUN_LEVEL, SHUTDOWN_LEVEL)
    {
        ::ecustate::rte::reset();
        lifecycle.listener = &controller;
        lifecycle.target   = RUN_LEVEL;
    }

    void withNvm() { sequence.set(ShutdownStep::NVM, nvm); }

    void withCan() { sequence.set(ShutdownStep::CAN_STOP, can); }

    void withOutputs() { sequence.set(ShutdownStep::SAFE_OUTPUTS, outputs); }

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

    EcuStateMachine const& machine() const { return controller.getStateMachine(); }

    std::string logText() const
    {
        std::string text;
        for (std::string const& entry : log)
        {
            text += entry;
            text += ";";
        }
        return text;
    }
};

// ---------------------------------------------------------------------------------------------------------------

/**
 * The run-down keeps its order: NvM first, CAN second, the safe outputs last (SYS-174), the lifecycle shutdown only
 * afterwards.
 */
void orderOfTheSteps()
{
    FakeSupply supply;
    Rig rig(PROFILE, supply);
    rig.withNvm();
    rig.withCan();
    rig.withOutputs();
    rig.run(100U);
    CHECK(rig.state() == State::RUN);
    rig.controller.setConsoleSleepRequest(true);
    rig.run(10U);
    CHECK(rig.state() == State::STANDBY);
    rig.run(10U);
    CHECK(rig.state() == State::RUNDOWN);
    CHECK(rig.machine().getShutdownStep() == ShutdownStep::CAN_STOP); // NvM was done in the same cycle
    CHECK(rig.lifecycle.requests.empty());                            // CAN is still alive
    CHECK(rig.logText() == "NvM start;NvM done;");
    rig.run(10U);
    CHECK(rig.logText() == "NvM start;NvM done;CAN start;CAN done;");
    CHECK(rig.state() == State::RUNDOWN);
    CHECK(rig.machine().getShutdownStep() == ShutdownStep::SAFE_OUTPUTS);
    CHECK(rig.lifecycle.requests.empty());
    rig.run(10U);
    CHECK(rig.logText() == "NvM start;NvM done;CAN start;CAN done;Out start;Out done;");
    CHECK(rig.state() == State::SHUTDOWN);
    CHECK((rig.lifecycle.requests.size() == 1U) && (rig.lifecycle.requests[0] == SHUTDOWN_LEVEL));
    CHECK(rig.machine().getStepState(ShutdownStep::NVM) == StepState::DONE);
    CHECK(rig.machine().getStepState(ShutdownStep::CAN_STOP) == StepState::DONE);
    CHECK(rig.machine().getStepState(ShutdownStep::SAFE_OUTPUTS) == StepState::DONE);
    CHECK(rig.machine().getStepAbortState() == StepState::PENDING);
    CHECK(rig.controller.getAbortedSteps() == 0U); // nothing timed out, nothing aborted
    // lifecycle down, then the power down after the log delay
    rig.run(500U);
    CHECK(rig.state() == State::SLEEP);
    CHECK(rig.controller.getPowerDownResult() == PowerDownResult::STARTED);
    CHECK(supply.powerDownCalls.size() == 1U);
}

/** No NvM in this project (NVM-01): the step is reported as not present, the run-down goes on. */
void missingNvmIsReportedNotInvented()
{
    FakeSupply supply;
    Rig rig(PROFILE, supply);
    rig.withCan(); // only CAN has a provider
    rig.run(100U);
    rig.controller.setConsoleSleepRequest(true);
    rig.run(50U);
    CHECK(rig.machine().getStepState(ShutdownStep::NVM) == StepState::NOT_PRESENT);
    CHECK(rig.machine().getStepState(ShutdownStep::CAN_STOP) == StepState::DONE);
    // SYS-174: a board without power outputs has no safe outputs step either
    CHECK(rig.machine().getStepState(ShutdownStep::SAFE_OUTPUTS) == StepState::NOT_PRESENT);
    CHECK(rig.machine().getCounters().stepNotPresent == 2U);
    CHECK(rig.machine().getCounters().stepFailed == 0U);
    CHECK(rig.logText() == "CAN start;CAN done;"); // nothing was invented for the NvM
    CHECK(rig.nvm.starts == 0U);
    CHECK(!rig.machine().isRestartIssued());
    rig.run(500U);
    CHECK(rig.state() == State::SLEEP);
}

/** A step that answers RUNNING is polled until it finishes; the ECU does not sleep in between. */
void stepIsPolledUntilItFinishes()
{
    FakeSupply supply;
    Rig rig(PROFILE, supply);
    rig.withNvm();
    rig.withCan();
    rig.can.runningCycles = 3U; // 30 ms of 50 ms
    rig.run(100U);
    rig.controller.setConsoleSleepRequest(true);
    rig.run(20U);
    CHECK(rig.state() == State::RUNDOWN);
    CHECK(rig.machine().getShutdownStep() == ShutdownStep::CAN_STOP);
    rig.run(20U);
    CHECK(rig.state() == State::RUNDOWN); // still inside its limit
    CHECK(rig.machine().getStepState(ShutdownStep::CAN_STOP) == StepState::RUNNING);
    CHECK(rig.lifecycle.requests.empty());
    rig.run(30U); // CAN done, then the safe outputs step (no provider) in the next cycle
    CHECK(rig.state() == State::SHUTDOWN);
    CHECK(rig.can.polls == 3U);
    CHECK(rig.machine().getStepState(ShutdownStep::CAN_STOP) == StepState::DONE);
    CHECK(rig.machine().getStepAbortState() == StepState::PENDING);
}

/** The CAN step fails: with step_error restart the run-down aborts, nothing sleeps, the reason stays readable. */
void failedStepAbortsTheRunDown()
{
    FakeSupply supply;
    Rig rig(PROFILE, supply);
    rig.withNvm();
    rig.withCan();
    rig.can.finish = IShutdownStep::Result::FAILED;
    rig.can.reason = "transceiver refused mute";
    rig.run(100U);
    rig.controller.setConsoleSleepRequest(true);
    rig.run(50U);
    CHECK(rig.machine().getStepState(ShutdownStep::NVM) == StepState::DONE);
    CHECK(rig.machine().getStepState(ShutdownStep::CAN_STOP) == StepState::FAILED);
    CHECK(rig.machine().getStepAbort() == ShutdownStep::CAN_STOP);
    CHECK(rig.machine().getStepAbortState() == StepState::FAILED);
    CHECK(std::string_view(rig.sequence.get(ShutdownStep::CAN_STOP)->getFailureReason())
          == "transceiver refused mute");
    CHECK(rig.machine().getCounters().stepFailed == 1U);
    CHECK(rig.machine().isRestartIssued());
    CHECK(rig.machine().getRestartReason() == EcuStateMachine::RestartReason::RUN_DOWN_STEP_FAILED);
    CHECK(rig.lifecycle.resetRequested());
    rig.run(1000U);
    CHECK(supply.powerDownCalls.empty()); // a half prepared ECU does not sleep
    CHECK(rig.lifecycle.requests.size() == 1U); // requested exactly once
}

/** The first step fails: the later steps are marked skipped, so the console shows what did not happen. */
void abortSkipsTheRemainingSteps()
{
    FakeSupply supply;
    Rig rig(PROFILE, supply);
    rig.withNvm();
    rig.withCan();
    rig.nvm.finish = IShutdownStep::Result::FAILED;
    rig.nvm.reason = "flash busy";
    rig.run(100U);
    rig.controller.setConsoleSleepRequest(true);
    rig.run(50U);
    CHECK(rig.machine().getStepState(ShutdownStep::NVM) == StepState::FAILED);
    CHECK(rig.machine().getStepState(ShutdownStep::CAN_STOP) == StepState::SKIPPED);
    CHECK(rig.can.starts == 0U);
    CHECK(rig.logText() == "NvM start;NvM failed;");
    CHECK(rig.lifecycle.resetRequested());
}

/** step_error continue: the failure of the CAN step is noted and the ECU sleeps anyway. */
void stepErrorContinueSleepsAnyway()
{
    FakeSupply supply;
    EcuStateConfig c     = PROFILE;
    c.shutdownStepError  = ShutdownStepError::CONTINUE;
    Rig rig(c, supply);
    rig.withNvm();
    rig.withCan();
    rig.withOutputs();
    rig.can.finish = IShutdownStep::Result::FAILED;
    rig.run(100U);
    rig.controller.setConsoleSleepRequest(true);
    rig.run(50U);
    CHECK(rig.machine().getStepState(ShutdownStep::NVM) == StepState::DONE);
    CHECK(rig.machine().getStepState(ShutdownStep::CAN_STOP) == StepState::FAILED);
    CHECK(rig.machine().getStepState(ShutdownStep::SAFE_OUTPUTS) == StepState::DONE); // outputs safe all the same
    CHECK(rig.machine().getStepAbortState() == StepState::FAILED); // still readable afterwards
    CHECK(!rig.machine().isRestartIssued());
    rig.run(500U);
    CHECK(rig.state() == State::SLEEP);
    CHECK(supply.powerDownCalls.size() == 1U);
}

/**
 * SYS-174 (safety review 2026-10-02, SYS69-S5, ECS-F04): step_error continue never goes past the NvM step. A failed
 * or timed-out NvM step may still program or erase the data flash; the power down would cut it. The ECU restarts.
 */
void continueNeverPassesTheNvmStep()
{
    // failed
    {
        FakeSupply supply;
        EcuStateConfig c    = PROFILE;
        c.shutdownStepError = ShutdownStepError::CONTINUE;
        Rig rig(c, supply);
        rig.withNvm();
        rig.withCan();
        rig.withOutputs();
        rig.nvm.finish = IShutdownStep::Result::FAILED;
        rig.run(100U);
        rig.controller.setConsoleSleepRequest(true);
        rig.run(50U);
        CHECK(rig.machine().getStepState(ShutdownStep::NVM) == StepState::FAILED);
        CHECK(rig.machine().getStepState(ShutdownStep::CAN_STOP) == StepState::SKIPPED);
        CHECK(rig.machine().getStepState(ShutdownStep::SAFE_OUTPUTS) == StepState::SKIPPED);
        CHECK(rig.machine().getRestartReason() == EcuStateMachine::RestartReason::RUN_DOWN_STEP_FAILED);
        CHECK(rig.lifecycle.resetRequested());
        CHECK(rig.can.starts == 0U);
        rig.run(1000U);
        CHECK(supply.powerDownCalls.empty());
    }
    // timed out: told so through abort(), then the restart - never the power down
    {
        FakeSupply supply;
        EcuStateConfig c    = PROFILE;
        c.shutdownStepError = ShutdownStepError::CONTINUE;
        Rig rig(c, supply);
        rig.withNvm();
        rig.withCan();
        rig.nvm.runningCycles = 10000U;
        rig.run(100U);
        rig.controller.setConsoleSleepRequest(true);
        rig.run(300U);
        CHECK(rig.machine().getStepState(ShutdownStep::NVM) == StepState::TIMEOUT);
        CHECK(rig.nvm.aborts == 1U);
        CHECK(rig.nvm.callsAfterAbort == 0U);
        CHECK(rig.machine().getRestartReason() == EcuStateMachine::RestartReason::RUN_DOWN_STEP_FAILED);
        CHECK(rig.lifecycle.resetRequested());
        // the abort comes before the restart request (log order: abort, nothing after it)
        CHECK(rig.logText() == "NvM start;NvM abort;");
        rig.run(1000U);
        CHECK(supply.powerDownCalls.empty());
    }
}

/**
 * SYS-174 (ECS-F04): a step cut off by its time limit is told so exactly once through IShutdownStep::abort(), in the
 * cycle of the time-out, and is not started or polled again; with continue the next step follows.
 */
void timedOutStepIsAborted()
{
    FakeSupply supply;
    EcuStateConfig c    = PROFILE;
    c.shutdownStepError = ShutdownStepError::CONTINUE;
    Rig rig(c, supply);
    rig.withNvm();
    rig.withCan();
    rig.withOutputs();
    rig.can.runningCycles = 10000U;
    rig.run(100U);
    rig.controller.setConsoleSleepRequest(true);
    rig.run(20U);
    CHECK(rig.machine().getShutdownStep() == ShutdownStep::CAN_STOP);
    uint32_t const startedAt = rig.nowMs;
    rig.run(40U);
    CHECK(rig.can.aborts == 0U); // inside its limit
    rig.run(20U);
    CHECK(rig.machine().getStepState(ShutdownStep::CAN_STOP) == StepState::TIMEOUT);
    CHECK(rig.can.aborts == 1U);
    CHECK((rig.can.abortedAtMs - startedAt) >= PROFILE.stepTimeoutMs[1]);
    CHECK((rig.can.abortedAtMs - startedAt) <= PROFILE.stepTimeoutMs[1] + 10U);
    CHECK(rig.controller.getAbortedSteps() == 1U);
    rig.run(1000U);
    CHECK(rig.can.aborts == 1U);
    CHECK(rig.can.callsAfterAbort == 0U);
    CHECK(rig.outputs.starts == 1U); // continue: the safe outputs step still runs
    CHECK(rig.nvm.aborts == 0U);     // finished steps are not aborted
    CHECK(rig.outputs.aborts == 0U);
    CHECK(rig.state() == State::SLEEP);
}

/**
 * SYS-174: the safe outputs step itself hangs: its limit is the constant of the mechanism
 * (SAFE_OUTPUTS_STEP_TIMEOUT_MS), not the project value of another step.
 */
void safeOutputsStepHasItsOwnLimit()
{
    FakeSupply supply;
    EcuStateConfig c     = PROFILE;
    c.stepTimeoutMs[2]   = 3600000U; // a stale value in the configuration does not apply
    Rig rig(c, supply);
    rig.withOutputs();
    rig.outputs.runningCycles = 10000U;
    rig.run(100U);
    rig.controller.setConsoleSleepRequest(true);
    rig.run(40U);
    CHECK(rig.machine().getShutdownStep() == ShutdownStep::SAFE_OUTPUTS);
    CHECK(rig.machine().getShutdownStepTimeoutMs() == SAFE_OUTPUTS_STEP_TIMEOUT_MS);
    uint32_t const startedAt = rig.outputs.startedAtMs;
    rig.run(SAFE_OUTPUTS_STEP_TIMEOUT_MS + 20U);
    CHECK(rig.machine().getStepState(ShutdownStep::SAFE_OUTPUTS) == StepState::TIMEOUT);
    CHECK((rig.outputs.abortedAtMs - startedAt) <= SAFE_OUTPUTS_STEP_TIMEOUT_MS + 10U);
    CHECK(rig.outputs.aborts == 1U);
    CHECK(rig.machine().getRestartReason() == EcuStateMachine::RestartReason::RUN_DOWN_STEP_FAILED);
}

/** A step that never finishes is cut off by its own limit, each step with its own value. */
void stepTimeoutPerStep()
{
    // NvM hangs: limit 100 ms
    {
        FakeSupply supply;
        Rig rig(PROFILE, supply);
        rig.withNvm();
        rig.withCan();
        rig.nvm.runningCycles = 10000U;
        rig.run(100U);
        rig.controller.setConsoleSleepRequest(true);
        rig.run(20U);
        uint32_t const startedAt = rig.nowMs;
        CHECK(rig.state() == State::RUNDOWN);
        rig.run(80U);
        CHECK(rig.state() == State::RUNDOWN); // 90 ms of 100 ms
        CHECK(!rig.machine().isRestartIssued());
        rig.run(20U);
        CHECK(rig.machine().getStepState(ShutdownStep::NVM) == StepState::TIMEOUT);
        CHECK((rig.nowMs - startedAt) >= PROFILE.stepTimeoutMs[0]);
        CHECK((rig.nowMs - startedAt) <= PROFILE.stepTimeoutMs[0] + 20U);
        CHECK(rig.machine().getStepState(ShutdownStep::CAN_STOP) == StepState::SKIPPED);
        CHECK(rig.machine().getRestartReason() == EcuStateMachine::RestartReason::RUN_DOWN_STEP_FAILED);
        CHECK(supply.powerDownCalls.empty());
    }
    // CAN hangs: shorter limit of 50 ms, and with continue the ECU sleeps anyway
    {
        FakeSupply supply;
        EcuStateConfig c    = PROFILE;
        c.shutdownStepError = ShutdownStepError::CONTINUE;
        Rig rig(c, supply);
        rig.withNvm();
        rig.withCan();
        rig.can.runningCycles = 10000U;
        rig.run(100U);
        rig.controller.setConsoleSleepRequest(true);
        rig.run(20U);
        CHECK(rig.machine().getShutdownStep() == ShutdownStep::CAN_STOP);
        uint32_t const startedAt = rig.nowMs;
        rig.run(40U);
        CHECK(rig.state() == State::RUNDOWN);
        rig.run(20U);
        CHECK(rig.machine().getStepState(ShutdownStep::CAN_STOP) == StepState::TIMEOUT);
        CHECK((rig.nowMs - startedAt) <= PROFILE.stepTimeoutMs[1] + 20U);
        CHECK(rig.state() == State::SHUTDOWN);
        rig.run(500U);
        CHECK(rig.state() == State::SLEEP);
    }
}

/**
 * Board without SBC (no switchable supply, SYS-01): the sleep request is refused in RUN, the run-down never
 * starts and no step is called.
 */
void boardWithoutSbcNeverRunsTheRunDown()
{
    PermanentPowerSupply supply;
    Rig rig(PROFILE, supply);
    rig.withNvm();
    rig.withCan();
    rig.run(100U);
    CHECK(rig.state() == State::RUN);
    ::ecustate::rte::writeSleepRequest(true);
    rig.run(5000U);
    CHECK(rig.state() == State::RUN);
    CHECK(rig.machine().isSleepRefused());
    CHECK(rig.machine().getCounters().sleepRefused == 1U);
    CHECK(rig.nvm.starts == 0U);
    CHECK(rig.can.starts == 0U);
    CHECK(rig.logText().empty());
    CHECK(rig.lifecycle.requests.empty());
    CHECK(rig.machine().getStepState(ShutdownStep::NVM) == StepState::PENDING);
    CHECK(rig.controller.getArmedWakeupSources() == 0U);
}

/**
 * There is no way back out of the run-down: a withdrawn sleep request does not stop it. A wake-up event that
 * arrives while it runs is kept and decided when the shutdown level is reached (wakeup_during_shutdown).
 */
void wakeupDuringTheRunDown()
{
    FakeSupply supply;
    EcuStateConfig c       = PROFILE;
    c.wakeupDuringShutdown = WakeupDuringShutdown::RESTART;
    Rig rig(c, supply);
    rig.withNvm();
    rig.withCan();
    rig.nvm.runningCycles = 2U;
    rig.run(100U);
    rig.controller.setConsoleSleepRequest(true);
    rig.run(10U);
    CHECK(rig.state() == State::STANDBY);
    rig.run(10U);
    CHECK(rig.state() == State::RUNDOWN);
    // withdrawn during the run-down: no way back, and a wake-up event is collected
    rig.controller.setConsoleSleepRequest(false);
    supply.pendingEvents = wakeup::canBus(0U);
    rig.run(50U);
    CHECK(rig.machine().getWakeupEvents() == wakeup::canBus(0U));
    CHECK(rig.state() == State::SHUTDOWN);
    CHECK(rig.machine().getStepState(ShutdownStep::CAN_STOP) == StepState::DONE);
    rig.run(1000U);
    CHECK(supply.powerDownCalls.empty()); // the event wins: restart instead of sleep
    CHECK(rig.lifecycle.resetRequested());
    CHECK(rig.machine().getRestartReason() == EcuStateMachine::RestartReason::WAKEUP_DURING_SHUTDOWN);
}

// ---------------------------------------------------------------------------------------------------------------

/**
 * SYS-26 (BEF-002, MISRA C 17.2): the run-down steps are driven in a loop instead of execute() ->
 * driveShutdownStep() -> execute(). Many run-downs whose steps all finish at once (done, not present, failed with
 * step_error continue) keep order and timing per cyclic() call: one step per call, the lifecycle shutdown in the
 * same call as the last step, the power down afterwards. That the loop is recursion free is shown by the static
 * analysis (python -m tools.misra run); a stack probe cannot tell the difference on the host, because clang turns
 * the old tail call into a jump from -O1 on.
 */
void immediateStepsKeepOrderAndTiming()
{
    struct Variant
    {
        bool nvmPresent;
        IShutdownStep::Result nvmFinish;
        StepState nvmState;
        char const* logAfterNvm;
    };
    // SYS-174: a failed NvM step restarts even with step_error continue (continueNeverPassesTheNvmStep()), so the
    // variant "NvM failed" of SYS-26 is gone here
    Variant const variants[] = {
        {true, IShutdownStep::Result::DONE, StepState::DONE, "NvM start;NvM done;"},
        {false, IShutdownStep::Result::DONE, StepState::NOT_PRESENT, ""},
    };
    constexpr uint32_t RUNS = 50U;
    uint32_t runDowns       = 0U;
    for (uint32_t run = 0U; run < RUNS; ++run)
    {
        for (Variant const& v : variants)
        {
            FakeSupply supply;
            EcuStateConfig c    = PROFILE;
            c.shutdownStepError = ShutdownStepError::CONTINUE; // a failed NvM step still runs the CAN step
            Rig rig(c, supply);
            if (v.nvmPresent)
            {
                rig.withNvm();
            }
            rig.withCan();
            rig.withOutputs();
            rig.nvm.finish = v.nvmFinish;
            rig.run(100U);
            rig.controller.setConsoleSleepRequest(true);
            rig.run(10U);
            CHECK(rig.state() == State::STANDBY);
            rig.run(10U); // first step, and only that one, in this call
            CHECK(rig.state() == State::RUNDOWN);
            CHECK(rig.machine().getShutdownStep() == ShutdownStep::CAN_STOP);
            CHECK(rig.machine().getStepState(ShutdownStep::NVM) == v.nvmState);
            CHECK(rig.logText() == v.logAfterNvm);
            CHECK(rig.can.starts == 0U);
            CHECK(rig.lifecycle.requests.empty());
            rig.run(10U); // CAN step, and only that one
            CHECK(rig.logText() == (std::string(v.logAfterNvm) + "CAN start;CAN done;"));
            CHECK(rig.machine().getStepState(ShutdownStep::CAN_STOP) == StepState::DONE);
            CHECK(rig.state() == State::RUNDOWN);
            CHECK(rig.lifecycle.requests.empty());
            rig.run(10U); // SYS-174: safe outputs step and the lifecycle shutdown in the same call
            CHECK(rig.logText() == (std::string(v.logAfterNvm) + "CAN start;CAN done;Out start;Out done;"));
            CHECK(rig.state() == State::SHUTDOWN);
            CHECK((rig.lifecycle.requests.size() == 1U) && (rig.lifecycle.requests[0] == SHUTDOWN_LEVEL));
            CHECK(!rig.machine().isRestartIssued());
            CHECK(rig.nvm.polls == 0U);
            CHECK(rig.can.polls == 0U);
            CHECK(rig.outputs.polls == 0U);
            rig.run(500U);
            CHECK(rig.state() == State::SLEEP);
            CHECK(supply.powerDownCalls.size() == 1U);
            ++runDowns;
        }
    }
    CHECK(runDowns == (RUNS * 2U));
}

} // namespace

int main()
{
    struct
    {
        char const* name;
        void (*fn)();
    } const tests[] = {
        {"orderOfTheSteps", orderOfTheSteps},
        {"missingNvmIsReportedNotInvented", missingNvmIsReportedNotInvented},
        {"stepIsPolledUntilItFinishes", stepIsPolledUntilItFinishes},
        {"failedStepAbortsTheRunDown", failedStepAbortsTheRunDown},
        {"abortSkipsTheRemainingSteps", abortSkipsTheRemainingSteps},
        {"stepErrorContinueSleepsAnyway", stepErrorContinueSleepsAnyway},
        {"continueNeverPassesTheNvmStep", continueNeverPassesTheNvmStep},
        {"timedOutStepIsAborted", timedOutStepIsAborted},
        {"safeOutputsStepHasItsOwnLimit", safeOutputsStepHasItsOwnLimit},
        {"stepTimeoutPerStep", stepTimeoutPerStep},
        {"boardWithoutSbcNeverRunsTheRunDown", boardWithoutSbcNeverRunsTheRunDown},
        {"wakeupDuringTheRunDown", wakeupDuringTheRunDown},
        {"immediateStepsKeepOrderAndTiming", immediateStepsKeepOrderAndTiming},
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
