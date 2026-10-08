#include "ecuState/EcuStateMachine.h"

namespace ecustate
{

char const* stateName(State const state)
{
    switch (state)
    {
        case State::STARTUP: return "Startup";
        case State::RUN: return "Run";
        case State::STANDBY: return "Standby";
        case State::RUNDOWN: return "Rundown";
        case State::SHUTDOWN: return "Shutdown";
        case State::SLEEP: return "Sleep";
        default: return "?";
    }
}

char const* shutdownStepName(ShutdownStep const step)
{
    switch (step)
    {
        case ShutdownStep::NVM: return "NvM";
        case ShutdownStep::CAN_STOP: return "CAN";
        case ShutdownStep::SAFE_OUTPUTS: return "Out"; // logcheck bound: 3 characters
        default: return "?";
    }
}

char const* stepStateName(StepState const state)
{
    switch (state)
    {
        case StepState::PENDING: return "pending";
        case StepState::RUNNING: return "running";
        case StepState::DONE: return "done";
        case StepState::NOT_PRESENT: return "not present";
        case StepState::FAILED: return "failed";
        case StepState::TIMEOUT: return "timeout";
        case StepState::SKIPPED: return "skipped";
        default: return "?";
    }
}

EcuStateMachine::EcuStateMachine(EcuStateConfig const& config)
: _config(config)
, _state(State::STARTUP)
, _stateSinceMs(0U)
, _runSinceMs(0U)
, _stepSinceMs(0U)
, _startupWakeupSources(wakeup::NONE)
, _wakeupEvents(wakeup::NONE)
, _counters()
, _restartReason(RestartReason::NONE)
, _stepStates()
, _stepAbortState(StepState::PENDING)
, _stepAbort(ShutdownStep::NVM)
, _step(0U)
, _runLevelReached(false)
, _sleepRefused(false)
, _sleepForced(false)
, _restartIssued(false)
, _powerDownRequested(false)
, _powerDownSinceMs(0U)
{
    for (StepState& s : _stepStates)
    {
        s = StepState::PENDING;
    }
}

void EcuStateMachine::enter(State const state, uint32_t const nowMs)
{
    _state        = state;
    _stateSinceMs = nowMs;
}

EcuStateMachine::Action EcuStateMachine::restart(RestartReason const reason)
{
    if (_restartIssued)
    {
        return Action::NONE;
    }
    _restartIssued = true;
    _restartReason = reason;
    return Action::RESTART;
}

uint32_t EcuStateMachine::stepLimitMs(uint8_t const step) const
{
    // SYS-174: the safe outputs step has a fixed limit of the mechanism, not a project value (EcuState.h)
    uint32_t limit = 0U;
    if (step == static_cast<uint8_t>(ShutdownStep::SAFE_OUTPUTS))
    {
        limit = SAFE_OUTPUTS_STEP_TIMEOUT_MS;
    }
    else if (step < SHUTDOWN_STEP_COUNT)
    {
        limit = _config.stepTimeoutMs[step];
    }
    else
    {
        // run-down done or aborted: no step, no limit
    }
    return limit;
}

uint32_t EcuStateMachine::getShutdownStepTimeoutMs() const
{
    return stepLimitMs(_step);
}

void EcuStateMachine::runLevelReached(uint32_t const nowMs, uint32_t const startupWakeupSources)
{
    // Only the first time: a manual lifecycle change (console "lc level") does not restart the state machine.
    if ((_state != State::STARTUP) || _runLevelReached)
    {
        return;
    }
    _runLevelReached      = true;
    _startupWakeupSources = startupWakeupSources;
    _runSinceMs           = nowMs;
    if ((_config.afterWatchdogReset == AfterWatchdogReset::SLEEP)
        && ((startupWakeupSources & wakeup::WATCHDOG) != 0U))
    {
        _sleepForced = true;
    }
    enter(State::RUN, nowMs);
}

void EcuStateMachine::startRunDown(uint32_t const nowMs)
{
    for (StepState& s : _stepStates)
    {
        s = StepState::PENDING;
    }
    _stepAbortState = StepState::PENDING;
    _step           = 0U;
    _stepSinceMs    = nowMs;
    enter(State::RUNDOWN, nowMs);
}

EcuStateMachine::Action EcuStateMachine::nextStep(uint32_t const nowMs)
{
    ++_step;
    _stepSinceMs = nowMs;
    if (_step < SHUTDOWN_STEP_COUNT)
    {
        return Action::NONE; // the next cycle drives the next step
    }
    enter(State::SHUTDOWN, nowMs);
    return Action::SHUTDOWN;
}

EcuStateMachine::Action
EcuStateMachine::abortRunDown(ShutdownStep const step, StepState const state, uint32_t const nowMs)
{
    _stepStates[static_cast<uint8_t>(step)] = state;
    ++_counters.stepFailed;
    if (_stepAbortState == StepState::PENDING)
    {
        _stepAbort      = step;
        _stepAbortState = state;
    }
    // SYS-174 (ECS-F04): never past the NvM step - it may still program or erase the data flash
    if ((_config.shutdownStepError == ShutdownStepError::CONTINUE) && isContinuableStep(step))
    {
        return nextStep(nowMs);
    }
    // A half prepared ECU must not sleep: the remaining steps are skipped and the ECU restarts.
    for (uint8_t i = static_cast<uint8_t>(static_cast<uint8_t>(step) + 1U); i < SHUTDOWN_STEP_COUNT; ++i)
    {
        _stepStates[i] = StepState::SKIPPED;
    }
    _step = SHUTDOWN_STEP_COUNT;
    return restart(RestartReason::RUN_DOWN_STEP_FAILED);
}

EcuStateMachine::Action EcuStateMachine::cyclic(
    uint32_t const nowMs, bool const sleepRequested, bool const powerDownSupported, uint32_t const wakeupEvents)
{
    bool const wantsSleep = sleepRequested || _sleepForced;
    switch (_state)
    {
        case State::RUN:
        {
            if (!wantsSleep)
            {
                _sleepRefused = false;
                break;
            }
            if (!powerDownSupported)
            {
                if (!_sleepRefused)
                {
                    _sleepRefused = true;
                    ++_counters.sleepRefused;
                }
                break;
            }
            _sleepRefused = false;
            if ((nowMs - _runSinceMs) >= _config.minRunMs)
            {
                _wakeupEvents = wakeup::NONE;
                ++_counters.standbyEntered;
                enter(State::STANDBY, nowMs);
            }
            break;
        }
        case State::STANDBY:
        {
            if (!wantsSleep || !powerDownSupported)
            {
                // A forced sleep (watchdog reset) is only given up if the supply cannot power down.
                ++_counters.standbyCancelled;
                if (!powerDownSupported)
                {
                    _sleepRefused = true;
                    ++_counters.sleepRefused;
                    _sleepForced = false;
                }
                enter(State::RUN, nowMs);
                break;
            }
            if (wakeupEvents != 0U)
            {
                // Wake-up while the application still runs: the post-run time starts again.
                _wakeupEvents |= wakeupEvents;
                ++_counters.postRunRestarted;
                _stateSinceMs = nowMs;
                break;
            }
            if ((nowMs - _stateSinceMs) >= _config.postRunMs)
            {
                // SYS-02: the run-down (NvM, CAN) runs before the lifecycle goes down, no way back.
                startRunDown(nowMs);
                return Action::RUN_DOWN_STEP;
            }
            break;
        }
        case State::RUNDOWN:
        {
            _wakeupEvents |= wakeupEvents;
            if (_step >= SHUTDOWN_STEP_COUNT)
            {
                break; // aborted, waiting for the restart
            }
            if ((nowMs - _stepSinceMs) >= stepLimitMs(_step))
            {
                return abortRunDown(getShutdownStep(), StepState::TIMEOUT, nowMs);
            }
            return Action::RUN_DOWN_STEP;
        }
        case State::SHUTDOWN:
        {
            _wakeupEvents |= wakeupEvents;
            if ((nowMs - _stateSinceMs) >= _config.shutdownTimeoutMs)
            {
                return restart(RestartReason::SHUTDOWN_TIMEOUT);
            }
            break;
        }
        case State::SLEEP:
        {
            _wakeupEvents |= wakeupEvents;
            // SYS-09: from the power down request on, not from entering SLEEP (output flush before the request)
            if (_powerDownRequested && ((nowMs - _powerDownSinceMs) >= _config.sleepTimeoutMs))
            {
                return restart(RestartReason::SLEEP_TIMEOUT);
            }
            break;
        }
        case State::STARTUP:
        default: break;
    }
    return Action::NONE;
}

EcuStateMachine::Action EcuStateMachine::shutdownStepResult(StepState const result, uint32_t const nowMs)
{
    if ((_state != State::RUNDOWN) || (_step >= SHUTDOWN_STEP_COUNT) || _restartIssued)
    {
        return Action::NONE;
    }
    ShutdownStep const step = getShutdownStep();
    switch (result)
    {
        case StepState::DONE:
        case StepState::NOT_PRESENT:
        {
            _stepStates[static_cast<uint8_t>(step)] = result;
            if (result == StepState::NOT_PRESENT)
            {
                ++_counters.stepNotPresent;
            }
            return nextStep(nowMs);
        }
        case StepState::FAILED:
        case StepState::TIMEOUT: return abortRunDown(step, result, nowMs);
        case StepState::RUNNING:
        {
            _stepStates[static_cast<uint8_t>(step)] = StepState::RUNNING;
            return Action::NONE;
        }
        default: return Action::NONE;
    }
}

EcuStateMachine::Action EcuStateMachine::shutdownLevelReached(uint32_t const nowMs)
{
    if ((_state != State::SHUTDOWN) || _restartIssued)
    {
        return Action::NONE;
    }
    if ((_wakeupEvents != 0U) && (_config.wakeupDuringShutdown == WakeupDuringShutdown::RESTART))
    {
        return restart(RestartReason::WAKEUP_DURING_SHUTDOWN);
    }
    enter(State::SLEEP, nowMs);
    return Action::POWER_DOWN;
}

EcuStateMachine::Action
EcuStateMachine::powerDownResult(IPowerSupply::PowerDownResult const result, uint32_t const nowMs)
{
    if (_state != State::SLEEP)
    {
        return Action::NONE;
    }
    if (!_powerDownRequested)
    {
        _powerDownRequested = true;
        _powerDownSinceMs   = nowMs;
    }
    if (result == IPowerSupply::PowerDownResult::STARTED)
    {
        return Action::NONE; // sleep timeout supervises a power down that does not take effect
    }
    return restart(RestartReason::POWER_DOWN_REFUSED);
}

char const* EcuStateMachine::restartReasonName(RestartReason const reason)
{
    switch (reason)
    {
        case RestartReason::NONE: return "none";
        case RestartReason::SHUTDOWN_TIMEOUT: return "shutdown timeout";
        case RestartReason::WAKEUP_DURING_SHUTDOWN: return "wake-up during shutdown";
        case RestartReason::POWER_DOWN_REFUSED: return "power down refused";
        case RestartReason::SLEEP_TIMEOUT: return "sleep timeout";
        case RestartReason::RUN_DOWN_STEP_FAILED: return "run-down step failed";
        default: return "?";
    }
}

} // namespace ecustate
