/* SYS-40 (finding F9 of doc/safety/fmea.md, SM-04): supervision of the safety task's own period against a time
 * base that does not depend on the scheduler.
 *
 * Why: the safety task is scheduled every 10 ms, but the only supervision of its period was the MCU watchdog,
 * serviced every WATCHDOG_CYCLIC_COUNTER = 7 cycles with a 250 ms time-out - it fires only once the cycle exceeds
 * 31.25 ms. A safety task that runs at any period between 10 and 31.25 ms (a wrong period, starvation by interrupt
 * load, a clock plan error that slows the OS tick, a timer fault) went unnoticed, and so did one that runs too fast
 * (the watchdog has no window).
 *
 * How: the family port supplies a free-running hardware counter on a clock of its own (S32K1: the LPTMR on the
 * 1 kHz LPO clock, safeSupervisionCheck/LptmrCycleTimer.h). The safety task reads it at the start of every cycle
 * and hands the value to sample():
 * - **interval too long**: the distance to the previous cycle start is larger than maxIntervalTicks. Checked in
 *   every cycle, so a slowed task is reported by the first slow cycle, before its checks run.
 * - **window too short**: the sum of windowCycles consecutive intervals is smaller than minWindowTicks. A single
 *   short interval is legitimate (the scheduler catches up after a late cycle, fixed rate), a whole window is not:
 *   the task runs too fast, or the counter runs too slow - including a counter that stopped, which would otherwise
 *   silently switch this supervision off.
 * The bounds are derived by the caller from the nominal period, the tolerated lateness of a cycle start and the
 * tolerance of the counter's clock (SafetyManager of the family port); this class only compares.
 *
 * Hardware independent: the counter width is a parameter (counterMask), so a 16-bit LPTMR, a 24-bit SysTick-like
 * counter or a 32-bit timer of another family fit. The state is four words, zero initialized (.bss) - in the family
 * port it lives in the MPU protected RAM of the safety task (libsafe*), and the limits are passed in on every call
 * instead of being stored, so no initialized data lands outside the protected region.
 */

#pragma once

#include <cstdint>

namespace safety
{

/** Bounds of the cycle period in ticks of the counter, derived by the caller (all constant data). */
struct CyclePeriodLimits
{
    uint32_t maxIntervalTicks; ///< one interval longer than this is a violation
    uint32_t minWindowTicks;   ///< windowCycles intervals shorter than this in sum are a violation
    uint32_t windowCycles;     ///< intervals per window (> 0)
    uint32_t counterMask;      ///< width of the free-running counter: 0xFFFF for 16 bit, 0xFFFFFFFF for 32 bit
};

class CyclePeriodCheck
{
public:
    /** Kind of a violation, bits 31..24 of the context word. */
    enum Kind : uint32_t
    {
        NONE                 = 0U,
        INTERVAL_TOO_LONG    = 1U, ///< value: the interval in ticks
        WINDOW_TOO_SHORT     = 2U, ///< value: the sum of the window in ticks
        TIMER_CONFIGURATION  = 3U, ///< value: deviating fields of the family's counter (family specific)
        // SYS-95: 4 and 5 are TimeBaseCheck's (safeWatchdog/ProgressChecks.h, system time against this counter)
    };

    static constexpr uint32_t KIND_SHIFT = 24U;
    static constexpr uint32_t VALUE_MASK = 0x00FFFFFFU;

    /** Context word of a violation: kind in bits 31..24, value in bits 23..0 (saturated). */
    static constexpr uint32_t context(Kind const kind, uint32_t const value)
    {
        return (static_cast<uint32_t>(kind) << KIND_SHIFT) | ((value > VALUE_MASK) ? VALUE_MASK : value);
    }

    static constexpr Kind kindOf(uint32_t const context) { return static_cast<Kind>(context >> KIND_SHIFT); }

    static constexpr uint32_t valueOf(uint32_t const context) { return context & VALUE_MASK; }

    constexpr CyclePeriodCheck() = default;

    /** Forget the previous sample: the next sample() only sets the reference (after the counter was started). */
    void restart();

    /**
     * One cycle start. The first sample after construction or restart() is the reference and reports nothing.
     * \param counter value of the free-running counter now (only the bits of limits.counterMask are used)
     * \return 0 if the period is within its bounds, otherwise the context word of the violation (see context());
     *         an interval that is too long is reported before the window of the same sample is evaluated
     */
    uint32_t sample(uint32_t counter, CyclePeriodLimits const& limits);

    /** Intervals in the current window (for tests and the console). */
    uint32_t windowCount() const { return _windowCount; }

private:
    uint32_t _last        = 0U;
    uint32_t _windowSum   = 0U;
    uint32_t _windowCount = 0U;
    uint32_t _started     = 0U; // 0 until the first sample after construction or restart()
};

} // namespace safety
