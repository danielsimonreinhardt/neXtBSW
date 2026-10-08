/**
 * Lock policy for AdcAccess on FreeRTOS: one statically allocated FreeRTOS mutex per converter
 * instance. FreeRTOS mutexes use priority inheritance, so a low priority task holding the converter
 * runs at the priority of the highest waiter until it releases it.
 *
 * Callers that must not wait use the exclusive section (all interrupts suspended):
 *   - scheduler not running (before start or suspended) or mutexes not created yet,
 *   - the platform reports an interrupt context or locked interrupts (InterruptContext below).
 *
 * InterruptContext is the platform part of the question "may this caller block?" (ARCH-26). It is a
 * static type next to the interrupt lock of the platform and knows the core registers; this
 * contract does not:
 *   static bool interruptsLockedOrInIsr()   true in an ISR/exception handler and while the
 *                                           interrupts are masked in any way the core has
 *                                           (e.g. SuspendResumeAllInterruptsScopedLock,
 *                                           taskENTER_CRITICAL)
 * Example: the Cortex-M implementation of the s32k1xx port (bspConfiguration, bsp/adc).
 *
 * Requires configUSE_MUTEXES, configSUPPORT_STATIC_ALLOCATION and INCLUDE_xTaskGetSchedulerState.
 * Suitable for converters whose conversion time is too long to suspend interrupts for it.
 */

#pragma once

#include <FreeRTOS.h>
#include <bsp/timer/SystemTimer.h>
#include <interrupts/suspendResumeAllInterrupts.h>
#include <semphr.h>
#include <task.h>

#include <cstddef>
#include <cstdint>

namespace adc
{

template<size_t InstanceCount, typename InterruptContext>
class FreeRtosAdcLock
{
public:
    using State = uint32_t;

    explicit FreeRtosAdcLock(TickType_t const timeoutTicks) : _timeoutTicks(timeoutTicks) {}

    /** Creates the mutexes; allowed before the scheduler starts. */
    void init()
    {
        for (size_t i = 0U; i < InstanceCount; ++i)
        {
            _mutex[i] = xSemaphoreCreateMutexStatic(&_storage[i]);
        }
        _initialized = true;
    }

    bool canBlock() const
    {
        return _initialized && (xTaskGetSchedulerState() == taskSCHEDULER_RUNNING)
               && (!InterruptContext::interruptsLockedOrInIsr());
    }

    bool tryLock(size_t const instance) { return xSemaphoreTake(_mutex[instance], 0U) == pdTRUE; }

    bool lock(size_t const instance)
    {
        return xSemaphoreTake(_mutex[instance], _timeoutTicks) == pdTRUE;
    }

    void unlock(size_t const instance) { (void)xSemaphoreGive(_mutex[instance]); }

    State enterExclusive() const { return getMachineStateRegisterValueAndSuspendAllInterrupts(); }

    void exitExclusive(State const state) const { resumeAllInterrupts(state); }

    uint32_t nowNs() const { return static_cast<uint32_t>(getSystemTimeNs()); }

private:
    TickType_t const _timeoutTicks;
    StaticSemaphore_t _storage[InstanceCount]{};
    SemaphoreHandle_t _mutex[InstanceCount]{};
    bool _initialized = false;
};

} // namespace adc
