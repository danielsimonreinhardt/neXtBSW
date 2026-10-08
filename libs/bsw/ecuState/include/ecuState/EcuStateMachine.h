#pragma once

#include "ecuState/EcuState.h"
#include "ecuState/IPowerSupply.h"
#include "ecuState/IShutdownStep.h"

#include <cstdint>

namespace ecustate
{

/**
 * ECU state machine STARTUP -> RUN <-> STANDBY -> RUNDOWN -> SHUTDOWN -> SLEEP
 * (libs/bsw/ecuState/doc/ecuState.md).
 * Pure logic: time and events are passed in, the caller executes the returned action. No OpenBSW dependency.
 */
class EcuStateMachine
{
public:
    enum class Action : uint8_t
    {
        NONE,
        RUN_DOWN_STEP, // drive the step getShutdownStep(), then call shutdownStepResult()
        SHUTDOWN,      // take the lifecycle down to the shutdown level, then call shutdownLevelReached()
        POWER_DOWN,    // call IPowerSupply::powerDown(), then powerDownResult()
        RESTART,       // restart the ECU (lifecycle level 0); issued once
    };

    enum class RestartReason : uint8_t
    {
        NONE,
        SHUTDOWN_TIMEOUT,       // lifecycle did not reach the shutdown level in time
        WAKEUP_DURING_SHUTDOWN, // wake-up event during RUNDOWN/SHUTDOWN (WakeupDuringShutdown::RESTART)
        POWER_DOWN_REFUSED,     // the power supply refused or failed
        SLEEP_TIMEOUT,          // still running after the power down request
        RUN_DOWN_STEP_FAILED,   // a run-down step failed or timed out (ShutdownStepError::RESTART)
    };

    struct Counters
    {
        uint16_t sleepRefused;     // sleep requests the power supply cannot serve (stayed in RUN)
        uint16_t standbyEntered;
        uint16_t standbyCancelled; // request withdrawn or supply lost its power down ability during STANDBY
        uint16_t postRunRestarted; // wake-up events during STANDBY
        uint16_t stepFailed;       // run-down steps that failed or timed out (SYS-02)
        uint16_t stepNotPresent;   // run-down steps without a provider (SYS-02, e.g. NvM: NVM-01)
    };

    explicit EcuStateMachine(EcuStateConfig const& config);

    /** Lifecycle reached the run level for the first time: STARTUP -> RUN (or STANDBY, AfterWatchdogReset). */
    void runLevelReached(uint32_t nowMs, uint32_t startupWakeupSources);

    /**
     * Call cyclically (e.g. every 10 ms).
     * \param sleepRequested     combined sleep request of the application (RTE) and the console
     * \param powerDownSupported the power supply can power down AND at least one wake-up source is armed
     * \param wakeupEvents       IPowerSupply::takeWakeupEvents()
     */
    Action cyclic(uint32_t nowMs, bool sleepRequested, bool powerDownSupported, uint32_t wakeupEvents);

    /**
     * Result of the run-down step getShutdownStep() after Action::RUN_DOWN_STEP (SYS-02).
     * \param result StepState::DONE, ::FAILED or ::NOT_PRESENT (no provider); ::RUNNING keeps the step
     */
    Action shutdownStepResult(StepState result, uint32_t nowMs);

    /** Lifecycle reached the shutdown level after Action::SHUTDOWN. */
    Action shutdownLevelReached(uint32_t nowMs);

    /**
     * Result of IPowerSupply::powerDown() after Action::POWER_DOWN, called when powerDown() was called. The sleep
     * timeout counts from here (SYS-09): the wait before the call (log delay or output flush with its own limit) is
     * bounded by the controller and does not eat into sleepTimeoutMs.
     */
    Action powerDownResult(IPowerSupply::PowerDownResult result, uint32_t nowMs);

    State getState() const { return _state; }

    uint32_t getTimeInStateMs(uint32_t const nowMs) const { return nowMs - _stateSinceMs; }

    /** Reasons of this start, 0 before the run level is reached. */
    uint32_t getStartupWakeupSources() const { return _startupWakeupSources; }

    /** Wake-up events collected during STANDBY, RUNDOWN and SHUTDOWN. */
    uint32_t getWakeupEvents() const { return _wakeupEvents; }

    /** True while a sleep request is refused because the supply cannot power down. */
    bool isSleepRefused() const { return _sleepRefused; }

    /** STANDBY without sleep request after a watchdog reset (AfterWatchdogReset::SLEEP). */
    bool isSleepForced() const { return _sleepForced; }

    bool isRestartIssued() const { return _restartIssued; }

    RestartReason getRestartReason() const { return _restartReason; }

    /** Run-down step being driven; valid while getState() == State::RUNDOWN (SYS-02). */
    ShutdownStep getShutdownStep() const { return static_cast<ShutdownStep>(_step); }

    /** Time limit of the step being driven. */
    uint32_t getShutdownStepTimeoutMs() const;

    /** How long the step being driven has been running. */
    uint32_t getTimeInStepMs(uint32_t const nowMs) const { return nowMs - _stepSinceMs; }

    /** Result of every run-down step of this run-down, StepState::PENDING before it was reached. */
    StepState getStepState(ShutdownStep const step) const { return _stepStates[static_cast<uint8_t>(step)]; }

    /** Step whose failure aborted the run-down; only meaningful with getStepAbortState() != PENDING. */
    ShutdownStep getStepAbort() const { return _stepAbort; }

    /** Why the run-down was aborted (FAILED or TIMEOUT), PENDING if it was not (SYS-02). */
    StepState getStepAbortState() const { return _stepAbortState; }

    Counters const& getCounters() const { return _counters; }

    EcuStateConfig const& getConfig() const { return _config; }

    static char const* restartReasonName(RestartReason reason);

private:
    void enter(State state, uint32_t nowMs);
    void startRunDown(uint32_t nowMs);
    Action nextStep(uint32_t nowMs);
    Action abortRunDown(ShutdownStep step, StepState state, uint32_t nowMs);
    Action restart(RestartReason reason);
    /** Time limit of run-down step \p step (index of ShutdownStep), 0 for an index past the last step. */
    uint32_t stepLimitMs(uint8_t step) const;

    EcuStateConfig const& _config;
    State _state;
    uint32_t _stateSinceMs;
    uint32_t _runSinceMs;
    uint32_t _stepSinceMs;
    uint32_t _startupWakeupSources;
    uint32_t _wakeupEvents;
    Counters _counters;
    RestartReason _restartReason;
    StepState _stepStates[SHUTDOWN_STEP_COUNT];
    StepState _stepAbortState;
    ShutdownStep _stepAbort;
    uint8_t _step; // index of the run-down step, SHUTDOWN_STEP_COUNT: run-down done
    bool _runLevelReached;
    bool _sleepRefused;
    bool _sleepForced;
    bool _restartIssued;
    bool _powerDownRequested;   // powerDownResult() was called in SLEEP (SYS-09)
    uint32_t _powerDownSinceMs; // time of that call, start of the sleep timeout
};

} // namespace ecustate
