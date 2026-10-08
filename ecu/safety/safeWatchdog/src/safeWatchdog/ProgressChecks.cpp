// SYS-95: progress checks of the safety task - ROM-check passes (DFA-1) and system time (DFA-6), see the header.

#include "safeWatchdog/ProgressChecks.h"

namespace safety
{

void ProgressDeadline::restart()
{
    _last     = 0U;
    _cycles   = 0U;
    _started  = 0U;
    _reported = 0U;
}

bool ProgressDeadline::sample(uint32_t const progress, uint32_t const deadlineCycles)
{
    if ((_started == 0U) || (progress != _last))
    {
        _last     = progress;
        _cycles   = 0U;
        _started  = 1U;
        _reported = 0U;
        return false;
    }
    if (_cycles < 0xFFFFFFFFU)
    {
        ++_cycles;
    }
    if ((deadlineCycles == 0U) || (_cycles <= deadlineCycles) || (_reported != 0U))
    {
        return false;
    }
    _reported = 1U;
    return true;
}

namespace
{
constexpr uint64_t US_PER_MS_TIMES_PER_MILLE = 1000000ULL; // ms per s (1000) x per mille (1000)
} // namespace

uint32_t TimeBaseCheck::lowerBoundMs(uint32_t const referenceTicks, TimeBaseLimits const& limits)
{
    // At its fastest clock the reference counts referenceHz * maxPerMille / 1000 ticks per second; the counter
    // difference may be one tick more than the time that passed, the ms of the system time one less, and the two
    // reads are not simultaneous (one more ms either way).
    if (referenceTicks <= 1U)
    {
        return 0U;
    }
    uint64_t const ticksPerSecondTimesThousand = static_cast<uint64_t>(limits.referenceHz) * limits.maxPerMille;
    uint64_t const ms = (static_cast<uint64_t>(referenceTicks - 1U) * US_PER_MS_TIMES_PER_MILLE)
                        / ticksPerSecondTimesThousand;
    uint64_t const margin = 2ULL + limits.slackMs;
    return (ms > margin) ? static_cast<uint32_t>(ms - margin) : 0U;
}

uint32_t TimeBaseCheck::upperBoundMs(uint32_t const referenceTicks, TimeBaseLimits const& limits)
{
    uint64_t const ticksPerSecondTimesThousand = static_cast<uint64_t>(limits.referenceHz) * limits.minPerMille;
    uint64_t const ms = ((static_cast<uint64_t>(referenceTicks) + 1ULL) * US_PER_MS_TIMES_PER_MILLE
                         + ticksPerSecondTimesThousand - 1ULL)
                        / ticksPerSecondTimesThousand;
    uint64_t const bound = ms + 2ULL;
    return (bound > 0xFFFFFFFFULL) ? 0xFFFFFFFFU : static_cast<uint32_t>(bound);
}

void TimeBaseCheck::restart()
{
    _lastTime = 0U;
    _lastRef  = 0U;
    _sumTime  = 0U;
    _sumRef   = 0U;
    _count    = 0U;
    _started  = 0U;
}

uint32_t TimeBaseCheck::sample(uint32_t const systemTimeMs, uint32_t const referenceCounter, TimeBaseLimits const& limits)
{
    uint32_t const ref = referenceCounter & limits.referenceMask;
    if (_started == 0U)
    {
        _lastTime = systemTimeMs;
        _lastRef  = ref;
        _sumTime  = 0U;
        _sumRef   = 0U;
        _count    = 0U;
        _started  = 1U;
        return 0U;
    }
    // Unsigned differences: the ms of the system time across their 32-bit wrap, the reference in its own width (one
    // wrap of a 16-bit counter at 1 kHz is 65 s, far beyond the watchdog that ends a safety task this slow).
    uint32_t const dTime = systemTimeMs - _lastTime;
    uint32_t const dRef  = (ref - _lastRef) & limits.referenceMask;
    _lastTime            = systemTimeMs;
    _lastRef             = ref;
    _sumTime             = ((0xFFFFFFFFU - _sumTime) < dTime) ? 0xFFFFFFFFU : (_sumTime + dTime);
    _sumRef              = ((0xFFFFFFFFU - _sumRef) < dRef) ? 0xFFFFFFFFU : (_sumRef + dRef);
    ++_count;
    if (_count < limits.windowCycles)
    {
        return 0U;
    }
    uint32_t const sumTime = _sumTime;
    uint32_t const sumRef  = _sumRef;
    _sumTime               = 0U;
    _sumRef                = 0U;
    _count                 = 0U;
    if (sumTime < lowerBoundMs(sumRef, limits))
    {
        return context(SYSTEM_TIME_SLOW, sumTime);
    }
    if (sumTime > upperBoundMs(sumRef, limits))
    {
        return context(SYSTEM_TIME_FAST, sumTime);
    }
    return 0U;
}

} // namespace safety
