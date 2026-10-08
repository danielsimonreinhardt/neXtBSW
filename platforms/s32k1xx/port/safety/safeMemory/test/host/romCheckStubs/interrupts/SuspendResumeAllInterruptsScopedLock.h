/* Host test stub of interrupts/SuspendResumeAllInterruptsScopedLock.h for RomCheckHostTest (SYS-44): counts the
 * locks, so the test can show that the flash write indicator changes and reads its counters under the lock. */

#pragma once

namespace romcheck_test
{
inline int& locks()
{
    static int count = 0;
    return count;
}
} // namespace romcheck_test

namespace interrupts
{
class SuspendResumeAllInterruptsScopedLock final
{
public:
    SuspendResumeAllInterruptsScopedLock() { ++::romcheck_test::locks(); }

    SuspendResumeAllInterruptsScopedLock(SuspendResumeAllInterruptsScopedLock const&)            = delete;
    SuspendResumeAllInterruptsScopedLock& operator=(SuspendResumeAllInterruptsScopedLock const&) = delete;
};
} // namespace interrupts
