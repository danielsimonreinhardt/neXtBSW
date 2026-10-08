/* DOC-44 host test stub of interrupts/SuspendResumeAllInterruptsScopedLock.h for SafetyShellUnlockHostTest: counts the
 * nesting depth of the interrupt lock, so the test can see whether the gate of the protected RAM changes with all
 * interrupts suspended. */

#pragma once

namespace unlock_test
{
inline int& lockDepth()
{
    static int depth = 0;
    return depth;
}
} // namespace unlock_test

namespace interrupts
{
class SuspendResumeAllInterruptsScopedLock final
{
public:
    SuspendResumeAllInterruptsScopedLock() { ++::unlock_test::lockDepth(); }

    ~SuspendResumeAllInterruptsScopedLock() { --::unlock_test::lockDepth(); }

    SuspendResumeAllInterruptsScopedLock(SuspendResumeAllInterruptsScopedLock const&)            = delete;
    SuspendResumeAllInterruptsScopedLock& operator=(SuspendResumeAllInterruptsScopedLock const&) = delete;
};
} // namespace interrupts
