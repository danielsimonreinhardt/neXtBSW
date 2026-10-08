// SYS-40: supervision of the safety task's own period (see the header).

#include "safeWatchdog/CyclePeriodCheck.h"

namespace safety
{

void CyclePeriodCheck::restart()
{
    _last        = 0U;
    _windowSum   = 0U;
    _windowCount = 0U;
    _started     = 0U;
}

uint32_t CyclePeriodCheck::sample(uint32_t const counter, CyclePeriodLimits const& limits)
{
    uint32_t const now = counter & limits.counterMask;
    if (_started == 0U)
    {
        _last        = now;
        _windowSum   = 0U;
        _windowCount = 0U;
        _started     = 1U;
        return 0U;
    }
    // Unsigned difference in the width of the counter: right across one wrap-around. More than one wrap between two
    // cycles is out of reach - at the S32K1's 16 bit and 1 kHz one wrap is 65 s, far beyond the MCU watchdog.
    uint32_t const interval = (now - _last) & limits.counterMask;
    _last                   = now;

    // The interval also counts into the window: the burst with which the scheduler catches up after a long
    // interval must not look like a task that runs too fast.
    uint32_t const room = 0xFFFFFFFFU - _windowSum;
    _windowSum          = (interval > room) ? 0xFFFFFFFFU : (_windowSum + interval);
    ++_windowCount;
    uint32_t windowResult = 0U;
    if (_windowCount >= limits.windowCycles)
    {
        if (_windowSum < limits.minWindowTicks)
        {
            windowResult = context(WINDOW_TOO_SHORT, _windowSum);
        }
        _windowSum   = 0U;
        _windowCount = 0U;
    }

    if (interval > limits.maxIntervalTicks)
    {
        return context(INTERVAL_TOO_LONG, interval);
    }
    return windowResult;
}

} // namespace safety
