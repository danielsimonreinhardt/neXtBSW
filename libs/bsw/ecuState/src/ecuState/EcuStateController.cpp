#include "ecuState/EcuStateController.h"

#include "ecuState/DirectReset.h"
#include "ecuState/EcuStateRte.h"
#include "ecuState/McuStartReasons.h"

#include <interrupts/SuspendResumeAllInterruptsScopedLock.h>

namespace ecustate
{
namespace
{
/* SYS-18: the family port's reset status (ecuState/McuStartReasons.h). A plain pointer, constant initialised
 * before any static constructor runs, so the port may register from one of its own (s32k1xx: the capture of
 * RCM_SRS before main()). Written once during the start-up, read from the task of the power supply afterwards -
 * no atomic needed, and none wanted: std::atomic of a pointer calls libatomic on ARMv6-M (PLAT-28). */
McuStartReasonsFn mcuStartReasonSource = nullptr;

/* SYS-173: the family port's direct reset (ecuState/DirectReset.h). Same pattern as mcuStartReasonSource: constant
 * initialised, registered once during the start-up, read from the task of the power supply afterwards. */
DirectResetFn directResetHook = nullptr;

/* Reads and clears a merker another task sets (PLAT-28). std::atomic<bool>::exchange() is no option on every core:
 * ARMv6-M (Cortex-M0+) has no exclusive access instructions, GCC then calls __atomic_exchange_1 of libatomic, which
 * the embedded toolchain does not ship. The interrupt lock of the platform (OpenBSW suspendResumeAllInterrupts,
 * present on every platform) makes load and store one step on every core instead. Setting a merker stays a single
 * store and needs no lock. */
bool takeMerker(std::atomic<bool>& merker)
{
    ::interrupts::SuspendResumeAllInterruptsScopedLock const lock;
    bool const value = merker.load();
    merker.store(false);
    return value;
}
} // namespace

void setMcuStartReasonSource(McuStartReasonsFn const source) { mcuStartReasonSource = source; }

void setDirectReset(DirectResetFn const reset) { directResetHook = reset; }

DirectResetFn directReset() { return directResetHook; }

uint32_t mcuStartReasons()
{
    return (mcuStartReasonSource != nullptr) ? mcuStartReasonSource() : wakeup::NONE;
}

char const* outputFlushStateName(OutputFlushState const state)
{
    switch (state)
    {
        case OutputFlushState::NOT_PRESENT: return "not present";
        case OutputFlushState::PENDING: return "pending";
        case OutputFlushState::RUNNING: return "running";
        case OutputFlushState::DONE: return "done";
        case OutputFlushState::TIMEOUT: return "timeout";
        default: return "?";
    }
}

EcuStateController::EcuStateController(
    EcuStateConfig const& config,
    IPowerSupply& supply,
    ILifecycleControl& lifecycle,
    ShutdownSequence const& sequence,
    uint8_t const runLevel,
    uint8_t const shutdownLevel)
: _machine(config)
, _supply(supply)
, _lifecycle(lifecycle)
, _sequence(sequence)
, _runLevel(runLevel)
, _shutdownLevel(shutdownLevel)
, _runLevelReached(false)
, _shutdownLevelReached(false)
, _consoleSleepRequest(false)
, _powerDownAtMs(0U)
, _mcuStartReasons(0U)
, _armedWakeupSources(0U)
, _missingWakeupSources(0U)
, _cyclicWakeupMs(0U)
, _flushStartMs(0U)
, _flushLimitMs(0U)
, _flushDurationMs(0U)
, _flushState((sequence.getOutputFlush() != nullptr) ? OutputFlushState::PENDING : OutputFlushState::NOT_PRESENT)
, _restartRequestedAtMs(0U)
, _abortedSteps(0U)
, _startedStep(ShutdownStep::NVM)
, _stepStarted(false)
, _restartRequested(false)
, _directResetIssued(false)
, _powerDownPending(false)
, _hasPowerDownResult(false)
, _powerDownResult(IPowerSupply::PowerDownResult::NOT_SUPPORTED)
{}

void EcuStateController::lifecycleLevelReached(uint8_t const level, bool const shutdown)
{
    if (!shutdown && (level == _runLevel))
    {
        _runLevelReached.store(true);
    }
    else if (shutdown && (level == _shutdownLevel))
    {
        _shutdownLevelReached.store(true);
    }
    else
    {
        // other levels are not relevant
    }
}

void EcuStateController::cyclic(uint32_t const nowMs)
{
    // SYS-173: first, so that nothing below can keep an overdue restart from being enforced
    superviseRestart(nowMs);
    // SYS-09: a power down issued in this call waits at least for the next one, so the system around the
    // controller has logged the change to SLEEP before the output is drained.
    bool const powerDownIssuedBefore = _powerDownPending;
    // SYS-02: what the project asks for, reduced to what the supply can arm. Answered every cycle, because a
    // supply learns its abilities only when it is up (SBC: after its startup) and can lose them again.
    uint32_t const supported = _supply.getSupportedWakeupSources();
    uint32_t const selected  = _machine.getConfig().wakeup.sources;
    _armedWakeupSources      = selected & supported;
    _missingWakeupSources    = selected & ~supported;
    _cyclicWakeupMs          = _supply.getCyclicWakeupMs(_machine.getConfig().wakeup.cyclicMs);

    if (takeMerker(_runLevelReached))
    {
        // SYS-18: both sources of the start reasons - what the supply device saw and what the MCU's own reset
        // status says. A supply that does not see a reset of the MCU alone (SBC with the debug jumper open) or
        // cannot tell at all (board without supply device) no longer hides a watchdog reset.
        _mcuStartReasons = mcuStartReasons();
        _machine.runLevelReached(
            nowMs, combineStartReasons(_supply.getStartupWakeupSources(), _mcuStartReasons));
    }
    if (takeMerker(_shutdownLevelReached))
    {
        execute(_machine.shutdownLevelReached(nowMs), nowMs);
    }
    if (powerDownIssuedBefore && _powerDownPending && isReadyForPowerDown(nowMs))
    {
        _powerDownPending = false;
        _powerDownResult
            = _supply.powerDown(nowMs, WakeupSelection{_armedWakeupSources, _cyclicWakeupMs});
        _hasPowerDownResult = true;
        execute(_machine.powerDownResult(_powerDownResult, nowMs), nowMs);
    }
    bool const sleepRequested = rte::isSleepRequested() || _consoleSleepRequest.load();
    // SYS-02: without an armed wake-up source the ECU would not come back, so sleep is refused like on a
    // board whose supply cannot power down at all.
    bool const powerDownPossible = _supply.isPowerDownSupported() && (_armedWakeupSources != 0U);
    execute(_machine.cyclic(nowMs, sleepRequested, powerDownPossible, _supply.takeWakeupEvents()), nowMs);
    rte::publish(_machine.getState(), _machine.getStartupWakeupSources());
}

void EcuStateController::superviseRestart(uint32_t const nowMs)
{
    // SYS-173 (SYS69-S4, ECS-F01): the lifecycle gets the shutdown time-out to carry the restart out. A component that
    // never finishes its shutdown keeps it from ever doing so - the ECU would stay half shut down, RTE and application
    // stopped, with the safety task still servicing the watchdog. Then the MCU is reset directly.
    if (_restartRequested && !_directResetIssued
        && ((nowMs - _restartRequestedAtMs) >= _machine.getConfig().shutdownTimeoutMs))
    {
        _directResetIssued        = true;
        DirectResetFn const reset = directReset();
        if (reset != nullptr)
        {
            reset(); // does not return on a target
        }
    }
}

void EcuStateController::abortTimedOutStep(uint32_t const nowMs)
{
    // SYS-174 (SYS69-S5, ECS-F04): a step cut off by its time limit is told so before the run-down goes on or the
    // restart is requested; it starts nothing new from here on.
    if (_stepStarted && (_machine.getStepState(_startedStep) == StepState::TIMEOUT))
    {
        _stepStarted                  = false;
        IShutdownStep* const provider = _sequence.get(_startedStep);
        if (provider != nullptr)
        {
            provider->abort(nowMs);
            ++_abortedSteps;
        }
    }
}

bool EcuStateController::isReadyForPowerDown(uint32_t const nowMs)
{
    IOutputFlush* const flush = _sequence.getOutputFlush();
    if (flush == nullptr)
    {
        // No output to drain described: fixed delay for the log (SYS-01 behaviour).
        return (nowMs - _powerDownAtMs) >= POWER_DOWN_DELAY_MS;
    }
    if (_flushState != OutputFlushState::RUNNING)
    {
        _flushState      = OutputFlushState::RUNNING;
        _flushStartMs    = nowMs;
        _flushLimitMs    = flush->getLimitMs();
        _flushDurationMs = 0U;
        if (_flushLimitMs == 0U)
        {
            _flushState = OutputFlushState::DONE;
            return true;
        }
        flush->start(nowMs);
    }
    _flushDurationMs = nowMs - _flushStartMs;
    if (flush->isFlushed(nowMs))
    {
        _flushState = OutputFlushState::DONE;
        return true;
    }
    if (_flushDurationMs >= _flushLimitMs)
    {
        // Something keeps the output from draining (output context starved, line busy): sleeping matters more.
        _flushState = OutputFlushState::TIMEOUT;
        return true;
    }
    return false;
}

bool EcuStateController::isOutputClosed() const
{
    if (_machine.isRestartIssued())
    {
        return false; // the ECU keeps running until the restart: the log is readable again
    }
    if (_hasPowerDownResult)
    {
        return _powerDownResult == IPowerSupply::PowerDownResult::STARTED;
    }
    // Flush started, power down not called yet: everything logged from now on would come after the flush.
    return _flushState == OutputFlushState::RUNNING;
}

EcuStateMachine::Action EcuStateController::driveShutdownStep(uint32_t const nowMs)
{
    ShutdownStep const step       = _machine.getShutdownStep();
    IShutdownStep* const provider = _sequence.get(step);
    if (provider == nullptr)
    {
        // No provider in this project (ShutdownStep::NVM as long as NVM-01 is blocked): nothing to do.
        return _machine.shutdownStepResult(StepState::NOT_PRESENT, nowMs);
    }
    bool const started = _stepStarted && (_startedStep == step);
    IShutdownStep::Result const result = started ? provider->poll(nowMs) : provider->start(nowMs);
    _stepStarted                       = true;
    _startedStep                       = step;
    StepState state                    = StepState::RUNNING;
    switch (result)
    {
        case IShutdownStep::Result::DONE:
        {
            _stepStarted = false;
            state        = StepState::DONE;
            break;
        }
        case IShutdownStep::Result::FAILED:
        {
            _stepStarted = false;
            state        = StepState::FAILED;
            break;
        }
        case IShutdownStep::Result::RUNNING:
        default:
        {
            break;
        }
    }
    return _machine.shutdownStepResult(state, nowMs);
}

void EcuStateController::execute(EcuStateMachine::Action const action, uint32_t const nowMs)
{
    // SYS-26 (BEF-002, MISRA C 17.2): the run-down steps as a loop instead of execute() -> driveShutdownStep() ->
    // execute(). The answer of the state machine to a driven step is executed in the same call, as before. Each
    // answer other than NONE moves the run-down on by one step (or ends it), so SHUTDOWN_STEP_COUNT passes are
    // enough even for a machine that chains its steps; a step still due after that is driven by the next cyclic().
    abortTimedOutStep(nowMs);
    EcuStateMachine::Action next = action;
    for (uint8_t pass = 0U; (next == EcuStateMachine::Action::RUN_DOWN_STEP) && (pass < SHUTDOWN_STEP_COUNT); ++pass)
    {
        next = driveShutdownStep(nowMs);
    }
    switch (next)
    {
        case EcuStateMachine::Action::SHUTDOWN: _lifecycle.transitionToLevel(_shutdownLevel); break;
        case EcuStateMachine::Action::POWER_DOWN:
            _powerDownPending = true;
            _powerDownAtMs    = nowMs;
            break;
        case EcuStateMachine::Action::RESTART:
            // SYS-173: supervised from here on (superviseRestart())
            _restartRequested     = true;
            _restartRequestedAtMs = nowMs;
            _lifecycle.transitionToLevel(RESTART_LEVEL);
            break;
        case EcuStateMachine::Action::RUN_DOWN_STEP: // pass limit reached: the next cycle drives the step
        case EcuStateMachine::Action::NONE:
        default: break;
    }
}

char const* EcuStateController::powerDownResultName(IPowerSupply::PowerDownResult const result)
{
    switch (result)
    {
        case IPowerSupply::PowerDownResult::STARTED: return "started";
        case IPowerSupply::PowerDownResult::NOT_SUPPORTED: return "not supported";
        case IPowerSupply::PowerDownResult::WAKEUP_PENDING: return "wake-up pending";
        case IPowerSupply::PowerDownResult::FAILED: return "failed";
        default: return "?";
    }
}

} // namespace ecustate
