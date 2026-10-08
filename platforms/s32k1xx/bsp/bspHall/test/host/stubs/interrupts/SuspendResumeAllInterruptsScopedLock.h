// Host stub of the interrupt lock (IO-07). On the host nothing preempts the test, but the test counts how
// often the driver took the lock, so a lost lock around the snapshot is visible.
#pragma once

extern unsigned nxTestLockCount;
extern unsigned nxTestLockDepth;
extern unsigned nxTestLockMaxDepth;

namespace interrupts
{
class SuspendResumeAllInterruptsScopedLock
{
public:
    SuspendResumeAllInterruptsScopedLock()
    {
        ++nxTestLockCount;
        ++nxTestLockDepth;
        if (nxTestLockDepth > nxTestLockMaxDepth)
        {
            nxTestLockMaxDepth = nxTestLockDepth;
        }
    }

    ~SuspendResumeAllInterruptsScopedLock() { --nxTestLockDepth; }
};

} // namespace interrupts
