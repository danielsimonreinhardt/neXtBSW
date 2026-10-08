#pragma once

#include <cstdint>

namespace ecustate
{

/**
 * ECU states (libs/bsw/ecuState/doc/ecuState.md). The numeric values are visible to software components
 * through the RTE (ecu.state), keep them stable. RUNDOWN was appended by SYS-02 and therefore carries the
 * highest number although it sits between STANDBY and SHUTDOWN in the sequence:
 * STARTUP -> RUN <-> STANDBY -> RUNDOWN -> SHUTDOWN -> SLEEP.
 */
enum class State : uint8_t
{
    STARTUP  = 0U, // lifecycle climbing to the run level
    RUN      = 1U, // normal operation
    STANDBY  = 2U, // sleep requested, application still running (post-run), return to RUN possible
    SHUTDOWN = 3U, // lifecycle going down to the shutdown level, no return
    SLEEP    = 4U, // power down requested from the power supply
    RUNDOWN  = 5U, // run-down steps before the shutdown (SYS-02: NvM, CAN), no return
};

char const* stateName(State state);

/**
 * NVM-26: true from STANDBY on - STANDBY, RUNDOWN, SHUTDOWN, SLEEP: the ECU is on its way to a power down (STANDBY can
 * still return to RUN). Work that a power down must not cut (a data flash erase or program, safety review NVM23-S1)
 * is not started any more then. Only STARTUP and RUN answer false; a value outside the enumeration answers true (the
 * safe side).
 */
constexpr bool isOnWayToPowerDown(State const state) { return (state != State::STARTUP) && (state != State::RUN); }

/**
 * Wake-up and start reasons as bit mask (ecu.wakeup). Provided by the power supply role; several bits can be set.
 * Bit layout is part of the RTE interface, keep it stable.
 */
namespace wakeup
{
constexpr uint32_t NONE     = 0U;
constexpr uint32_t POWER    = 1UL << 0U; // supply switched on (power-on)
constexpr uint32_t RESET    = 1UL << 1U; // MCU reset without wake-up event (reset pin, debugger, software reset)
constexpr uint32_t WATCHDOG = 1UL << 2U; // watchdog reset
constexpr uint32_t SUPPLY   = 1UL << 3U; // recovered from a supply fault (undervoltage, overload)
constexpr uint32_t TIMER    = 1UL << 4U; // cyclic wake-up timer (e.g. watchdog period in sleep)
constexpr uint32_t UNKNOWN  = 1UL << 7U; // the power supply cannot tell

constexpr uint8_t CAN_BUS_SHIFT = 8U;  // bit 8 + index of the CAN bus (NX_CAN_<bus>_INDEX)
constexpr uint8_t CAN_BUS_COUNT = 8U;
constexpr uint8_t LOCAL_SHIFT   = 16U; // bit 16 + index of a local wake-up input of the power supply
constexpr uint8_t LOCAL_COUNT   = 8U;

constexpr uint32_t CAN_BUSES = 0xFFUL << CAN_BUS_SHIFT;
constexpr uint32_t LOCALS    = 0xFFUL << LOCAL_SHIFT;

/** Bit of CAN bus \p index; UNKNOWN if the index has no bit. */
constexpr uint32_t canBus(uint8_t const index)
{
    return (index < CAN_BUS_COUNT) ? (1UL << (CAN_BUS_SHIFT + index)) : UNKNOWN;
}

/** Bit of local wake-up input \p index; UNKNOWN if the index has no bit. */
constexpr uint32_t local(uint8_t const index)
{
    return (index < LOCAL_COUNT) ? (1UL << (LOCAL_SHIFT + index)) : UNKNOWN;
}
} // namespace wakeup

/** What happens with a wake-up event that arrives while the lifecycle is shut down. */
enum class WakeupDuringShutdown : uint8_t
{
    RESTART, // restart the ECU (lifecycle level 0) instead of powering down
    SLEEP,   // power down anyway (a supply that sees the event refuses and the ECU restarts then)
};

/** Behaviour after a start whose reasons contain wakeup::WATCHDOG. */
enum class AfterWatchdogReset : uint8_t
{
    RUN,   // like any other start: RUN until the application requests sleep
    SLEEP, // STANDBY right after the run level is reached, then sleep without a request
};

/**
 * What happens when a run-down step (SYS-02) fails or exceeds its time limit. SYS-174 (safety review 2026-10-02,
 * SYS69-S5, finding ECS-F04): CONTINUE never applies to ShutdownStep::NVM - a timed-out NvM step may still have a data
 * flash command in flight, and the power down would cut it. That step always restarts (isContinuableStep() in
 * ecuState/IShutdownStep.h); tools/rtegen refuses step_error continue for a project with an NvM step.
 */
enum class ShutdownStepError : uint8_t
{
    RESTART,  // give up the sleep and restart the ECU: a half prepared ECU must not go to sleep
    CONTINUE, // note the step and go on: sleeping matters more than the step (not for the NvM step)
};

/** Number of run-down steps before the lifecycle shutdown, see ecuState/IShutdownStep.h (ShutdownStep). */
constexpr uint8_t SHUTDOWN_STEP_COUNT = 3U;

/**
 * SYS-174: time limit of the run-down step ShutdownStep::SAFE_OUTPUTS. Not a project value: the step walks the safe
 * outputs table synchronously in start() (microseconds), so the limit only catches a provider that breaks that
 * contract. Two cycles of the ECU state management (10 ms) and more, so that a provider answering RUNNING once is
 * not cut off by the cycle jitter.
 */
constexpr uint32_t SAFE_OUTPUTS_STEP_TIMEOUT_MS = 50U;

/**
 * Wake-up sources the ECU arms before the power down (SYS-02). Project values; which of them a board can
 * really serve answers the power supply (IPowerSupply::getSupportedWakeupSources()).
 */
struct WakeupSelection
{
    uint32_t sources;  // ecustate::wakeup bits: one per CAN bus, per local input, TIMER for cyclic wake-up
    uint32_t cyclicMs; // requested period of the cyclic wake-up, 0: no cyclic wake-up
};

/**
 * Project parameters (libs/bsw/ecuState/module.yaml, values from the ECU description, tools/rtegen).
 * No defaults here: the manifest holds them.
 */
struct EcuStateConfig
{
    uint32_t minRunMs;          // RUN at least this long after the start before a sleep request is served
    uint32_t postRunMs;         // STANDBY duration before the shutdown
    // lifecycle shutdown not done within this time: restart. SYS-173: also the bound of the restart itself - the way
    // to lifecycle level 0 not done within this time after the restart request: direct reset (ecuState/DirectReset.h)
    uint32_t shutdownTimeoutMs;
    uint32_t sleepTimeoutMs;    // still running this long after the power down request: restart
    // Time limit of every run-down step, in the order of ShutdownStep (SYS-02): NVM, CAN_STOP, SAFE_OUTPUTS. SYS-174:
    // the entry of SAFE_OUTPUTS is a copy of SAFE_OUTPUTS_STEP_TIMEOUT_MS for the console (ECU_STATE_CONFIG()); the
    // state machine uses the constant itself, so a configuration written before SYS-174 (two values) stays valid.
    uint32_t stepTimeoutMs[SHUTDOWN_STEP_COUNT];
    WakeupSelection wakeup;
    WakeupDuringShutdown wakeupDuringShutdown;
    AfterWatchdogReset afterWatchdogReset;
    ShutdownStepError shutdownStepError;
};

/**
 * Delay between entering SLEEP and the power down request, lets the log reach the console. Only without an output
 * flush provider (ShutdownSequence::setOutputFlush, SYS-09); with one the power down follows the flush.
 */
constexpr uint32_t POWER_DOWN_DELAY_MS = 100U;

constexpr uint32_t MAX_TIME_MS = 3600000U; // 1 h, upper limit of every time parameter

constexpr bool isValidEcuStateConfig(EcuStateConfig const& c)
{
    return (c.minRunMs <= MAX_TIME_MS) && (c.postRunMs <= MAX_TIME_MS) && (c.shutdownTimeoutMs >= 100U)
           && (c.shutdownTimeoutMs <= MAX_TIME_MS) && (c.sleepTimeoutMs > POWER_DOWN_DELAY_MS)
           && (c.sleepTimeoutMs <= MAX_TIME_MS) && (c.stepTimeoutMs[0] >= 10U)
           && (c.stepTimeoutMs[0] <= MAX_TIME_MS) && (c.stepTimeoutMs[1] >= 10U)
           && (c.stepTimeoutMs[1] <= MAX_TIME_MS) && (c.wakeup.cyclicMs <= MAX_TIME_MS)
           // TIMER means cyclic wake-up: without a period it would never fire
           && (((c.wakeup.sources & wakeup::TIMER) == 0U) == (c.wakeup.cyclicMs == 0U))
           // only the sources a supply can arm: no fixed start reason (POWER, RESET, ...) except TIMER
           && ((c.wakeup.sources & ~(wakeup::CAN_BUSES | wakeup::LOCALS | wakeup::TIMER)) == 0U);
}

} // namespace ecustate
