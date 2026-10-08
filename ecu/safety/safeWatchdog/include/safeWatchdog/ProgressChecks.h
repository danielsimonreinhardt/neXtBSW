/* SYS-95 (findings DFA-1 and DFA-6 of doc/safety/dfa.md): hardware independent progress checks of the safety task.
 *
 * Two dependent failures of the stage-1 argument have the same shape: something that is supposed to advance stands
 * still, and nothing on another time base notices.
 * - DFA-1: the run-time ROM re-check (SM-14) runs in the idle context. When the idle context is starved (CPU
 *   overload, a task that never blocks) no pass is started and RomCheck itself, which only runs inside that context,
 *   cannot report it. ProgressDeadline counts safety cycles since a progress counter (the passes of the ROM check)
 *   last changed and reports once when that exceeds a deadline in cycles.
 * - DFA-6: the time-outs of the SPI transfers and of the CAN reception supervision count on the OpenBSW system time.
 *   If it stops, every such wait is unbounded and every reception time-out stays silent. TimeBaseCheck compares the
 *   advance of the system time over a window of safety cycles with the advance of an independent reference counter
 *   (on the S32K1 the LPTMR of the period check SM-04, on the LPO) within the tolerance of the reference's clock.
 *
 * Both classes only compare; the family port reads the counters and maps a finding to an event of the safe
 * supervisor (safeLifecycle/ProgressSupervision.cpp on the S32K1). The state is a few words, zero initialized (.bss,
 * so in the MPU protected RAM of the safety task in the family port), and the limits are passed on every call, like
 * CyclePeriodCheck.
 */

#pragma once

#include <cstdint>

namespace safety
{

/**
 * DFA-1: a counter that has to advance at least once within `deadlineCycles` calls of sample().
 * The first sample after construction or restart() is the reference. A deadline of 0 switches the check off.
 */
class ProgressDeadline
{
public:
    constexpr ProgressDeadline() = default;

    /** Forget the reference: the next sample() only takes it. */
    void restart();

    /**
     * One safety cycle.
     * \param progress the counter that has to advance (any change counts, a wrap-around included)
     * \param deadlineCycles the largest number of cycles without a change that is still fine; 0 = no check
     * \return true exactly once when the counter stood still for more than deadlineCycles cycles; a later change
     *         re-arms the check
     */
    bool sample(uint32_t progress, uint32_t deadlineCycles);

    /** Cycles since the last change of the counter (for tests and the console). */
    uint32_t cyclesSinceProgress() const { return _cycles; }

private:
    uint32_t _last     = 0U;
    uint32_t _cycles   = 0U;
    uint32_t _started  = 0U; // 0 until the first sample after construction or restart()
    uint32_t _reported = 0U; // 1 after the report until the next change
};

/** Bounds of the system time check (all constant data, derived by the caller). */
struct TimeBaseLimits
{
    uint32_t referenceHz;     ///< nominal frequency of the reference counter
    uint32_t minPerMille;     ///< slowest clock of the reference relative to nominal, per mille (<= 1000)
    uint32_t maxPerMille;     ///< fastest clock of the reference relative to nominal, per mille (>= 1000)
    uint32_t windowCycles;    ///< cycles per window (> 0)
    uint32_t referenceMask;   ///< width of the reference counter: 0xFFFF for 16 bit
    uint32_t slackMs;         ///< tolerated loss of system time per window (one-off lag of the time base)
};

class TimeBaseCheck
{
public:
    /** Kind of a finding, bits 31..24 of the context word; the value (bits 23..0) is the system time of the window
     * in ms. The numbers continue the kinds of CyclePeriodCheck, so both share the event SAFETY_CYCLE_PERIOD_VIOLATION
     * without ambiguity. */
    enum Kind : uint32_t
    {
        NONE             = 0U,
        SYSTEM_TIME_SLOW = 4U, ///< the system time advanced less than the reference allows (stopped: value 0)
        SYSTEM_TIME_FAST = 5U, ///< the system time advanced more than the reference allows
    };

    static constexpr uint32_t KIND_SHIFT = 24U;
    static constexpr uint32_t VALUE_MASK = 0x00FFFFFFU;

    static constexpr uint32_t context(Kind const kind, uint32_t const value)
    {
        return (static_cast<uint32_t>(kind) << KIND_SHIFT) | ((value > VALUE_MASK) ? VALUE_MASK : value);
    }

    static constexpr Kind kindOf(uint32_t const context) { return static_cast<Kind>(context >> KIND_SHIFT); }

    static constexpr uint32_t valueOf(uint32_t const context) { return context & VALUE_MASK; }

    /**
     * Smallest system time in ms that `referenceTicks` ticks of the reference may correspond to: the reference at its
     * fastest clock, one tick of quantisation of the reference and one ms of the system time, minus the slack.
     */
    static uint32_t lowerBoundMs(uint32_t referenceTicks, TimeBaseLimits const& limits);
    /** Largest system time in ms for `referenceTicks` ticks: the reference at its slowest clock, plus quantisation. */
    static uint32_t upperBoundMs(uint32_t referenceTicks, TimeBaseLimits const& limits);

    constexpr TimeBaseCheck() = default;

    /** Forget the previous sample: the next sample() only sets the reference. */
    void restart();

    /**
     * One safety cycle: the system time and the reference counter, read one right after the other.
     * \return 0 while fine or within a window, otherwise the context word of the finding (evaluated at the end of
     *         each window of limits.windowCycles intervals; the next window starts empty)
     */
    uint32_t sample(uint32_t systemTimeMs, uint32_t referenceCounter, TimeBaseLimits const& limits);

private:
    uint32_t _lastTime     = 0U;
    uint32_t _lastRef      = 0U;
    uint32_t _sumTime      = 0U;
    uint32_t _sumRef       = 0U;
    uint32_t _count        = 0U;
    uint32_t _started      = 0U;
};

} // namespace safety
