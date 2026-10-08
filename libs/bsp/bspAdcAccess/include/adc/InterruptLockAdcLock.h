/**
 * Lock policy for AdcAccess without RTOS objects: every conversion runs with all interrupts
 * suspended (OpenBSW suspendResumeAllInterrupts, available on every platform).
 *
 * Suitable when one conversion is short compared to the tolerable interrupt latency (S32K1 at
 * 48 MHz ADC clock: about 2 us, see IO-14). No waiting, no priority inversion (the section
 * acts as a priority ceiling above every task and ISR), usable from ISRs and before the scheduler
 * starts. For slow converters use FreeRtosAdcLock.
 */

#pragma once

#include <bsp/timer/SystemTimer.h>
#include <interrupts/suspendResumeAllInterrupts.h>

#include <cstddef>
#include <cstdint>

namespace adc
{

class InterruptLockAdcLock
{
public:
    using State = uint32_t;

    bool canBlock() const { return false; }

    // Never called while canBlock() is false; present to satisfy the policy.
    bool tryLock(size_t) { return true; }

    bool lock(size_t) { return true; }

    void unlock(size_t) {}

    State enterExclusive() const { return getMachineStateRegisterValueAndSuspendAllInterrupts(); }

    void exitExclusive(State const state) const { resumeAllInterrupts(state); }

    uint32_t nowNs() const { return static_cast<uint32_t>(getSystemTimeNs()); }
};

} // namespace adc
