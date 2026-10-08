#pragma once

#include "ecuState/EcuStateMachine.h"
#include "ecuState/IPowerSupply.h"
#include "ecuState/IShutdownStep.h"

#include <atomic>
#include <cstdint>

namespace ecustate
{

/** Lifecycle levels as the ECU state management needs them (OpenBSW: ILifecycleManager::transitionToLevel). */
class ILifecycleControl
{
public:
    virtual void transitionToLevel(uint8_t level) = 0;

protected:
    ILifecycleControl()                                    = default;
    ILifecycleControl(ILifecycleControl const&)            = default;
    ILifecycleControl& operator=(ILifecycleControl const&) = default;
    ~ILifecycleControl()                                   = default;
};

/**
 * Runs the EcuStateMachine against the lifecycle, the power supply role, the run-down steps (SYS-02) and the
 * RTE hand-over (EcuStateRte.h). Hardware and OpenBSW independent, so the whole flow is host-testable; the
 * OpenBSW system around it only adds the lifecycle component, the listener, the timer and the console.
 *
 * Contexts: lifecycleLevelReached() and setConsoleSleepRequest() from any task, everything else from the task
 * of the power supply.
 */
class EcuStateController
{
public:
    static constexpr uint8_t RESTART_LEVEL = 0U; // OpenBSW: level 0 resets the ECU (IdleHandler)

    /**
     * \param sequence      providers of the run-down steps; a step without provider is "not present"
     * \param runLevel      lifecycle level of normal operation (highest level)
     * \param shutdownLevel lifecycle level kept for sleep: the power supply and this controller live there
     */
    EcuStateController(
        EcuStateConfig const& config,
        IPowerSupply& supply,
        ILifecycleControl& lifecycle,
        ShutdownSequence const& sequence,
        uint8_t runLevel,
        uint8_t shutdownLevel);

    /** ILifecycleListener: \p shutdown true for a transition downwards. */
    void lifecycleLevelReached(uint8_t level, bool shutdown);

    void setConsoleSleepRequest(bool request) { _consoleSleepRequest.store(request); }

    bool isConsoleSleepRequested() const { return _consoleSleepRequest.load(); }

    /** Call cyclically in the context of the power supply. */
    void cyclic(uint32_t nowMs);

    EcuStateMachine const& getStateMachine() const { return _machine; }

    IPowerSupply const& getPowerSupply() const { return _supply; }

    ShutdownSequence const& getShutdownSequence() const { return _sequence; }

    bool isPowerDownPending() const { return _powerDownPending; }

    /** SYS-09: flush of the output before the power down (NOT_PRESENT without provider). */
    OutputFlushState getOutputFlushState() const { return _flushState; }

    /** Limit of the last flush, as reported by the provider when it started. */
    uint32_t getOutputFlushLimitMs() const { return _flushLimitMs; }

    /** How long the last flush took (DONE) or ran before the power down anyway (TIMEOUT). */
    uint32_t getOutputFlushDurationMs() const { return _flushDurationMs; }

    /**
     * SYS-11: true while nothing may be logged any more: from the start of the output flush (without flush
     * provider: from a started power down) until the power down is refused or a restart is issued. A line
     * logged in between would reach the output after the flush and be cut off by the power down (board test
     * SYS-09, 2026-09-18). The system around the controller holds its reports back meanwhile; they follow only
     * if the ECU keeps running.
     */
    bool isOutputClosed() const;

    bool hasPowerDownResult() const { return _hasPowerDownResult; }

    IPowerSupply::PowerDownResult getPowerDownResult() const { return _powerDownResult; }

    /**
     * SYS-18: the share of the start reasons that came from the MCU's own reset status
     * (ecuState/McuStartReasons.h), taken when the run level was reached; 0 before that and on a family that
     * does not report one. The other share is IPowerSupply::getStartupWakeupSources();
     * EcuStateMachine::getStartupWakeupSources() holds the union of both.
     */
    uint32_t getMcuStartReasons() const { return _mcuStartReasons; }

    /** Wake-up sources of the project that this power supply can arm (SYS-02); 0: no sleep possible. */
    uint32_t getArmedWakeupSources() const { return _armedWakeupSources; }

    /** Selected wake-up sources this power supply cannot arm on this board (reported, not fatal). */
    uint32_t getMissingWakeupSources() const { return _missingWakeupSources; }

    /** Period of the cyclic wake-up this supply would really use, 0 if there is none. */
    uint32_t getCyclicWakeupMs() const { return _cyclicWakeupMs; }

    /**
     * SYS-173: true once the restart (Action::RESTART, lifecycle level 0) was requested; from then on the controller
     * supervises it with EcuStateConfig::shutdownTimeoutMs.
     */
    bool isRestartRequested() const { return _restartRequested; }

    /**
     * SYS-173: true once the lifecycle did not carry the restart out within EcuStateConfig::shutdownTimeoutMs and the
     * direct reset of the family port (ecuState/DirectReset.h) was called. On a target the call does not return; true
     * here means no family hook was registered and the restart is overdue.
     */
    bool isDirectResetIssued() const { return _directResetIssued; }

    /** SYS-174: run-down steps cut off by their time limit and told so through IShutdownStep::abort(). */
    uint8_t getAbortedSteps() const { return _abortedSteps; }

    uint8_t getRunLevel() const { return _runLevel; }

    uint8_t getShutdownLevel() const { return _shutdownLevel; }

    static char const* powerDownResultName(IPowerSupply::PowerDownResult result);

private:
    void execute(EcuStateMachine::Action action, uint32_t nowMs);
    /** Drives the step getShutdownStep() once and returns the answer of the state machine (SYS-26: no call back). */
    EcuStateMachine::Action driveShutdownStep(uint32_t nowMs);
    bool isReadyForPowerDown(uint32_t nowMs);
    /** SYS-173: direct reset when the requested restart is overdue. */
    void superviseRestart(uint32_t nowMs);
    /** SYS-174: IShutdownStep::abort() of the step the state machine has just cut off by its time limit. */
    void abortTimedOutStep(uint32_t nowMs);

    EcuStateMachine _machine;
    IPowerSupply& _supply;
    ILifecycleControl& _lifecycle;
    ShutdownSequence const& _sequence;
    uint8_t const _runLevel;
    uint8_t const _shutdownLevel;
    std::atomic<bool> _runLevelReached;
    std::atomic<bool> _shutdownLevelReached;
    std::atomic<bool> _consoleSleepRequest;
    uint32_t _powerDownAtMs;
    uint32_t _mcuStartReasons;
    uint32_t _armedWakeupSources;
    uint32_t _missingWakeupSources;
    uint32_t _cyclicWakeupMs;
    uint32_t _flushStartMs;
    uint32_t _flushLimitMs;
    uint32_t _flushDurationMs;
    OutputFlushState _flushState;
    uint32_t _restartRequestedAtMs;
    uint8_t _abortedSteps;
    ShutdownStep _startedStep;
    bool _stepStarted;
    bool _restartRequested;
    bool _directResetIssued;
    bool _powerDownPending;
    bool _hasPowerDownResult;
    IPowerSupply::PowerDownResult _powerDownResult;
};

} // namespace ecustate
