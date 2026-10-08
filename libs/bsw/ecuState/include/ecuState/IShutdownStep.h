#pragma once

#include "ecuState/EcuState.h"
#include "ecuState/IOutputFlush.h"

#include <cstdint>

namespace ecustate
{

/**
 * Run-down before the power down (SYS-02), executed in exactly this order while the ECU is in State::RUNDOWN,
 * that is before the OpenBSW lifecycle is taken down: the application, the RTE and CAN are still alive.
 *
 * The numbers are the index into EcuStateConfig::stepTimeoutMs and into ShutdownSequence.
 */
enum class ShutdownStep : uint8_t
{
    NVM          = 0U, // persist the non-volatile data (NvM of the application, data flash gate; NVM-07, NVM-23)
    CAN_STOP     = 1U, // stop transmitting and leave the transceivers ready for a wake-up
    SAFE_OUTPUTS = 2U, // SYS-174: power outputs to their safe state through the safe outputs table, kept there
};

/**
 * SYS-174 (safety review 2026-10-02, SYS69-S5, finding ECS-F04): whether ShutdownStepError::CONTINUE may go on after a
 * failure or time-out of \p step. Never for the NvM step: it may still have a data flash program or erase in flight,
 * which the power down would cut; a half written block is worse than a restart.
 */
constexpr bool isContinuableStep(ShutdownStep const step) { return step != ShutdownStep::NVM; }

/** How far one run-down step got. Everything but DONE and NOT_PRESENT is a failure of the step. */
enum class StepState : uint8_t
{
    PENDING,     // not reached yet
    RUNNING,     // started, being polled
    DONE,        // finished
    NOT_PRESENT, // no provider for this step in this project: nothing to do, not an error
    FAILED,      // the provider reported an error
    TIMEOUT,     // the provider did not finish within EcuStateConfig::stepTimeoutMs of this step
    SKIPPED,     // an earlier step aborted the run-down (ShutdownStepError::RESTART)
};

char const* shutdownStepName(ShutdownStep step);
char const* stepStateName(StepState state);

/**
 * One step of the run-down. The ECU state management calls start() once and then poll() in every cycle
 * (10 ms) until the step answers DONE or FAILED; both calls come from the task of the power supply and must
 * not block. A step that answers RUNNING for longer than its time limit is cut off with StepState::TIMEOUT and
 * told so through abort() (SYS-174).
 */
class IShutdownStep
{
public:
    enum class Result : uint8_t
    {
        RUNNING,
        DONE,
        FAILED,
    };

    /** Short name for console and log, e.g. "CAN". */
    virtual char const* getName() const = 0;

    /** Begins the step. */
    virtual Result start(uint32_t nowMs) = 0;

    /** Progress of a step that answered RUNNING. */
    virtual Result poll(uint32_t nowMs) = 0;

    /**
     * Why the step answered FAILED; "-" if it did not. Shown by "ecu status" and logged: a string literal of at most
     * 24 characters (SYS-16, checked by tools/logcheck).
     */
    virtual char const* getFailureReason() const = 0;

    /**
     * SYS-174 (finding ECS-F04): the step answered RUNNING past its time limit and was cut off (StepState::TIMEOUT).
     * Called once, from the task of the power supply, before the run-down goes on or the restart is requested; no
     * start() or poll() follows. The provider stops what it can stop at once and starts nothing new - it must not
     * block. The default does nothing: a step whose work cannot be stopped (or which has none left) keeps it, and the
     * state machine never continues past the NvM step (isContinuableStep()).
     */
    virtual void abort(uint32_t /* nowMs */) {}

protected:
    IShutdownStep()                                = default;
    IShutdownStep(IShutdownStep const&)            = default;
    IShutdownStep& operator=(IShutdownStep const&) = default;
    ~IShutdownStep()                               = default;
};

/**
 * The providers of the run-down steps, wired by the integration (family port, later intgen). A step without provider
 * is reported as StepState::NOT_PRESENT and skipped (a project without NvM, without CAN, or - SAFE_OUTPUTS - without
 * power outputs). The default sequence has no provider at all, so such a project runs the run-down through without
 * doing anything.
 */
class ShutdownSequence
{
public:
    void set(ShutdownStep const step, IShutdownStep& provider) { _steps[static_cast<uint8_t>(step)] = &provider; }

    /** nullptr: this project has no provider for \p step. */
    IShutdownStep* get(ShutdownStep const step) const { return _steps[static_cast<uint8_t>(step)]; }

    /** SYS-09: output (log, console) drained after entering State::SLEEP and before the power down. */
    void setOutputFlush(IOutputFlush& flush) { _outputFlush = &flush; }

    /** nullptr: no flush, the power down follows POWER_DOWN_DELAY_MS after entering State::SLEEP. */
    IOutputFlush* getOutputFlush() const { return _outputFlush; }

private:
    IShutdownStep* _steps[SHUTDOWN_STEP_COUNT] = {};
    IOutputFlush* _outputFlush                 = nullptr;
};

} // namespace ecustate
