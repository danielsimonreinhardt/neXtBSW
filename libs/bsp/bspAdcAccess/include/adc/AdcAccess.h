/**
 * Serialized access to synchronous (polled) ADC conversions from several tasks and from contexts
 * that must not wait (ISR, interrupts or scheduler locked, before the scheduler starts).
 *
 * MCU- and RTOS-neutral. The RTOS/core specific part is the Lock policy, the register specific
 * part is the Converter; both are static types (architecture D1).
 *
 * Lock policy (one blocking lock per converter instance, e.g. an RTOS mutex with priority
 * inheritance, see FreeRtosAdcLock.h):
 *   bool     canBlock() const               caller may wait: task context, scheduler running,
 *                                           interrupts enabled
 *   bool     tryLock(size_t instance)       take without waiting
 *   bool     lock(size_t instance)          take, waiting with a bounded timeout (false = timeout)
 *   void     unlock(size_t instance)
 *   State    enterExclusive()               no preemption at all (e.g. interrupts disabled),
 *   void     exitExclusive(State)           nestable, kept short
 *   uint32_t nowNs()                        free running time in ns (wraps), for statistics only
 *
 * Converter (one per build, instance = ADC peripheral):
 *   bsp::BspReturnCode convert(size_t instance, uint8_t input, uint16_t& value)
 *       complete synchronous conversion of an input
 *   void restart(size_t instance, uint8_t input)
 *       start a conversion of input again and return when its result is ready, without consuming
 *       the result. Used after a caller that cannot wait has converted in between: a preempted
 *       task conversion then still delivers the value of its own input.
 * Both must return after a bounded time even if the converter never signals completion: they may
 * run with the interrupts suspended. Derive the bound from the conversion time, not from a large
 * constant - see adc/AdcPollLimit.h.
 *
 * Contexts:
 *   - Task (canBlock): lock of the instance (waiters inherit priority), mark the instance busy,
 *     convert with interrupts enabled, clear busy, unlock. The exclusive sections around the busy
 *     flag last a few instructions.
 *   - Cannot wait (ISR, interrupts/scheduler locked, before scheduler start): convert inside an
 *     exclusive section. If a task conversion of the same instance was preempted, restart it
 *     afterwards. Never blocks, so no deadlock; the added latency is one or two conversions.
 * Single core only: the exclusive section must stop every other caller.
 *
 * Properties this adds to the ADC contract (ARCH-03/05; the contract itself is still the OpenBSW
 * header, so they are documented here and at the implementation, IO-19):
 *   1. A synchronous read is callable from every context: task, ISR, with the interrupts or the
 *      scheduler suspended and before the scheduler starts. It is the implementation that
 *      serializes, not the caller; a caller never has to know who else reads.
 *   2. A synchronous read returns after a time derived from the conversion time of the configured
 *      converter, never after an arbitrary constant. Its bound covers the worst case of this
 *      wrapper (one conversion plus the restart of a preempted one) times a safety factor of the
 *      project, and it holds even if the converter never signals completion.
 *   3. How long a read may suspend the interrupts is a decision of the project, not of the driver
 *      (module parameters lock_policy and max_interrupt_suspend_us, checked against the derived
 *      conversion time by tools/nxconfig).
 *   4. A failed read is reported, never substituted by a value. The caller decides what a failure
 *      means; a safety monitor must not read a failure as a measurement (IO-16).
 */

#pragma once

#include <bsp/Bsp.h>

#include <cstddef>
#include <cstdint>

namespace adc
{

struct AdcAccessStatistics
{
    uint32_t conversions;     ///< all conversions that reached the converter
    uint32_t contended;       ///< task callers that found the lock taken and waited
    uint32_t lockTimeouts;    ///< task callers that gave up waiting (conversion not done)
    uint32_t exclusive;       ///< conversions from contexts that cannot wait
    uint32_t nested;          ///< of those: a preempted task conversion was restarted
    uint32_t errors;          ///< converter reported an error or instance out of range
    uint32_t maxWaitNs;       ///< longest wait for the lock (task callers)
    uint32_t maxConversionNs; ///< longest conversion of a task caller (lock held)
    uint32_t maxExclusiveNs;  ///< longest exclusive section (conversion plus restart)
};

template<typename Lock, typename Converter, size_t InstanceCount>
class AdcAccess
{
public:
    AdcAccess(Lock& lock, Converter& converter) : _lock(lock), _converter(converter) {}

    bsp::BspReturnCode convert(size_t const instance, uint8_t const input, uint16_t& value)
    {
        if (instance >= InstanceCount)
        {
            auto const state = _lock.enterExclusive();
            ++_stats.errors;
            _lock.exitExclusive(state);
            return bsp::BSP_ERROR;
        }
        return _lock.canBlock() ? convertWaiting(instance, input, value)
                                : convertExclusive(instance, input, value);
    }

    AdcAccessStatistics getStatistics() const
    {
        auto const state                = _lock.enterExclusive();
        AdcAccessStatistics const stats = _stats;
        _lock.exitExclusive(state);
        return stats;
    }

    void resetStatistics()
    {
        auto const state = _lock.enterExclusive();
        _stats           = AdcAccessStatistics{};
        _lock.exitExclusive(state);
    }

private:
    struct Active
    {
        bool busy;
        uint8_t input;
    };

    static uint32_t elapsed(uint32_t const start, uint32_t const end) { return end - start; }

    static void updateMax(uint32_t& max, uint32_t const value)
    {
        if (value > max)
        {
            max = value;
        }
    }

    bsp::BspReturnCode convertWaiting(size_t const instance, uint8_t const input, uint16_t& value)
    {
        uint32_t const waitStart = _lock.nowNs();
        bool const contended     = !_lock.tryLock(instance);
        if (contended && !_lock.lock(instance))
        {
            auto const state = _lock.enterExclusive();
            ++_stats.contended;
            ++_stats.lockTimeouts;
            updateMax(_stats.maxWaitNs, elapsed(waitStart, _lock.nowNs()));
            _lock.exitExclusive(state);
            return bsp::BSP_ERROR;
        }
        uint32_t const convStart = _lock.nowNs();
        {
            auto const state        = _lock.enterExclusive();
            _active[instance].input = input;
            _active[instance].busy  = true;
            _lock.exitExclusive(state);
        }

        bsp::BspReturnCode const ret = _converter.convert(instance, input, value);

        {
            uint32_t const convEnd = _lock.nowNs();
            auto const state       = _lock.enterExclusive();
            _active[instance].busy = false;
            ++_stats.conversions;
            if (contended)
            {
                ++_stats.contended;
            }
            if (ret != bsp::BSP_OK)
            {
                ++_stats.errors;
            }
            updateMax(_stats.maxWaitNs, elapsed(waitStart, convStart));
            updateMax(_stats.maxConversionNs, elapsed(convStart, convEnd));
            _lock.exitExclusive(state);
        }
        _lock.unlock(instance);
        return ret;
    }

    bsp::BspReturnCode convertExclusive(size_t const instance, uint8_t const input, uint16_t& value)
    {
        auto const state             = _lock.enterExclusive();
        uint32_t const start         = _lock.nowNs();
        bsp::BspReturnCode const ret = _converter.convert(instance, input, value);
        if (_active[instance].busy)
        {
            _converter.restart(instance, _active[instance].input);
            ++_stats.nested;
        }
        ++_stats.conversions;
        ++_stats.exclusive;
        if (ret != bsp::BSP_OK)
        {
            ++_stats.errors;
        }
        updateMax(_stats.maxExclusiveNs, elapsed(start, _lock.nowNs()));
        _lock.exitExclusive(state);
        return ret;
    }

    Lock& _lock;
    Converter& _converter;
    Active _active[InstanceCount]{};
    AdcAccessStatistics _stats{};
};

} // namespace adc
