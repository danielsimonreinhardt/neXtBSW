/* ARCH-14 host test stub: interrupt lock with nesting depth; releasing the outermost lock lets a pending simulated
 * interrupt run (SystemTimerHostTest.cpp). */

#pragma once

#include <cstdint>

namespace systemTimerModel
{
void lockInterrupts();
void unlockInterrupts();
} // namespace systemTimerModel

namespace interrupts
{
class SuspendResumeAllInterruptsScopedLock
{
public:
    SuspendResumeAllInterruptsScopedLock() { ::systemTimerModel::lockInterrupts(); }

    ~SuspendResumeAllInterruptsScopedLock() { ::systemTimerModel::unlockInterrupts(); }

    SuspendResumeAllInterruptsScopedLock(SuspendResumeAllInterruptsScopedLock const&)            = delete;
    SuspendResumeAllInterruptsScopedLock& operator=(SuspendResumeAllInterruptsScopedLock const&) = delete;
};
} // namespace interrupts
