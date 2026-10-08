/**
 * Averaging of one ADC channel over one period of a PWM group (IO-31).
 *
 * A channel behind a switching power stage (e.g. the DC link current of a motor bridge) carries the ripple of
 * the PWM. A single conversion at a random phase of the PWM period scatters by the ripple (eval board, IO-15:
 * +-0.4 A at a mean of 0.5 A). This class converts `samples` times at equally spaced instants that cover exactly
 * one PWM period and returns the rounded mean of the raw values:
 *
 *   t_k = t_0 + k * spacingNs,  k = 0 .. samples - 1,  spacingNs = PWM period / samples
 *
 * For a periodic signal the mean of N equally spaced samples over exactly one period equals the period mean
 * for every harmonic that is no multiple of N; the phase of t_0 does not matter. No synchronization with the
 * timer is needed, only the period (generated from the realized PWM frequency, tools/bswgen) and a time source
 * running from the same oscillator as the PWM counter (DWT/system time: both come from the system clock).
 *
 * Every conversion goes through the serialized access (adc::AdcAccess) on its own, so the interrupt suspension
 * per conversion stays that of one conversion (IO-14/IO-19). The lock is not held across the window: other
 * tasks and ISRs may convert between two samples; they only delay the next sample (counted as "late"). The
 * calling task is busy for one PWM period per read (busy wait, no RTOS call). Averaged channels are meant for
 * task callers; a caller with the interrupts suspended would keep them suspended for the whole window.
 *
 * Policy types (static, D1):
 *   Lock        nowNs(), enterExclusive(), exitExclusive(state) - the lock policy of adc::AdcAccess
 *   convert     callable bsp::BspReturnCode(uint16_t& raw) - one conversion of the channel
 *
 * Properties of the ADC contract (adc/AdcAccess.h) that hold here as well:
 *   - a failed conversion ends the read with BSP_ERROR and no substitute value (property 4);
 *   - the read returns after a bounded time (property 2): samples conversions (each bounded by the access) plus
 *     the waits; a wait gives up after spacingNs polls of the time source, which is longer than spacingNs as long
 *     as one poll takes at least 1 ns (cores below 1 GHz) - a stalled time source is reported as an error.
 */

#pragma once

#include <bsp/Bsp.h>

#include <cstdint>

namespace adc
{

/// Averaging of one channel. samples <= 1: a single conversion (today's behaviour).
struct AveragingSpec
{
    uint16_t samples;   ///< conversions per read
    uint32_t spacingNs; ///< time between two conversions, samples * spacingNs = one PWM period
};

struct AdcAveragingStatistics
{
    uint32_t reads;       ///< averaged reads (not the single conversions)
    uint32_t late;        ///< reads with a sample more than a quarter spacing after its instant
    uint32_t errors;      ///< reads ended by a failed conversion or a stalled time source
    uint32_t maxWindowNs; ///< longest time from the first to the last sample instant
    uint32_t maxLateNs;   ///< largest delay of a sample behind its instant
};

template<typename Lock>
class AdcAveraging
{
public:
    explicit AdcAveraging(Lock& lock) : _lock(lock) {}

    template<typename Convert>
    bsp::BspReturnCode read(AveragingSpec const& spec, Convert&& convert, uint16_t& value)
    {
        if (spec.samples <= 1U)
        {
            return convert(value);
        }
        uint32_t sum        = 0U;
        uint32_t maxLate    = 0U;
        uint32_t const t0   = _lock.nowNs();
        uint32_t lastSample = t0;
        for (uint16_t k = 0U; k < spec.samples; ++k)
        {
            uint32_t const offset = static_cast<uint32_t>(k) * spec.spacingNs;
            if (!waitUntil(t0, offset, spec.spacingNs))
            {
                countError();
                return bsp::BSP_ERROR;
            }
            lastSample          = _lock.nowNs();
            uint32_t const late = (lastSample - t0) - offset;
            if (late > maxLate)
            {
                maxLate = late;
            }
            uint16_t raw = 0U;
            if (convert(raw) != bsp::BSP_OK)
            {
                countError();
                return bsp::BSP_ERROR;
            }
            sum += raw;
        }
        value = static_cast<uint16_t>((sum + (spec.samples / 2U)) / spec.samples);
        countRead(lastSample - t0, maxLate, (maxLate > (spec.spacingNs / 4U)));
        return bsp::BSP_OK;
    }

    AdcAveragingStatistics getStatistics() const
    {
        auto const state                   = _lock.enterExclusive();
        AdcAveragingStatistics const stats = _stats;
        _lock.exitExclusive(state);
        return stats;
    }

    void resetStatistics()
    {
        auto const state = _lock.enterExclusive();
        _stats           = AdcAveragingStatistics{};
        _lock.exitExclusive(state);
    }

private:
    /// Busy wait until offset ns after t0 (wrap-safe). False if the time source did not advance far enough
    /// within `bound` polls.
    bool waitUntil(uint32_t const t0, uint32_t const offset, uint32_t const bound) const
    {
        uint32_t polls = 0U;
        while ((_lock.nowNs() - t0) < offset)
        {
            ++polls;
            if (polls > bound)
            {
                return false;
            }
        }
        return true;
    }

    void countError()
    {
        auto const state = _lock.enterExclusive();
        ++_stats.errors;
        _lock.exitExclusive(state);
    }

    void countRead(uint32_t const windowNs, uint32_t const lateNs, bool const late)
    {
        auto const state = _lock.enterExclusive();
        ++_stats.reads;
        if (late)
        {
            ++_stats.late;
        }
        if (windowNs > _stats.maxWindowNs)
        {
            _stats.maxWindowNs = windowNs;
        }
        if (lateNs > _stats.maxLateNs)
        {
            _stats.maxLateNs = lateNs;
        }
        _lock.exitExclusive(state);
    }

    Lock& _lock;
    AdcAveragingStatistics _stats{};
};

} // namespace adc
