/* SYS-171 host test stub of interrupts/SuspendResumeAllInterruptsScopedLock.h for MemoryProtectionHostTest.cpp:
 * the region check does not lock; the gate functions that do are the subject of the safety gate test. */
#pragma once

namespace interrupts
{
class SuspendResumeAllInterruptsScopedLock final
{
public:
    SuspendResumeAllInterruptsScopedLock() {} // user-provided, like the real lock (no unused-variable warning)

    SuspendResumeAllInterruptsScopedLock(SuspendResumeAllInterruptsScopedLock const&)            = delete;
    SuspendResumeAllInterruptsScopedLock& operator=(SuspendResumeAllInterruptsScopedLock const&) = delete;
};
} // namespace interrupts
