// Host stub of the interrupt lock of the platform (PLAT-28), in place of OpenBSW libs/bsp/bspInterrupts. On the host
// nothing preempts the test, so the stub only records what the module does with the lock: how often it was taken,
// whether it is balanced, and what a merker probe saw while it was held.
#pragma once

#include <functional>

namespace ecustate_test
{
inline unsigned lockCount    = 0U;
inline unsigned lockDepth    = 0U;
inline unsigned lockMaxDepth = 0U;
// Runs when the lock is released, like an interrupt that was pending while it was held (may set merkers again).
inline std::function<void()> onUnlock;
} // namespace ecustate_test

namespace interrupts
{
class SuspendResumeAllInterruptsScopedLock
{
public:
    SuspendResumeAllInterruptsScopedLock()
    {
        ++::ecustate_test::lockCount;
        ++::ecustate_test::lockDepth;
        if (::ecustate_test::lockDepth > ::ecustate_test::lockMaxDepth)
        {
            ::ecustate_test::lockMaxDepth = ::ecustate_test::lockDepth;
        }
    }

    ~SuspendResumeAllInterruptsScopedLock()
    {
        --::ecustate_test::lockDepth;
        if ((::ecustate_test::lockDepth == 0U) && ::ecustate_test::onUnlock)
        {
            auto const pending = ::ecustate_test::onUnlock;
            ::ecustate_test::onUnlock = nullptr;
            pending();
        }
    }

    SuspendResumeAllInterruptsScopedLock(SuspendResumeAllInterruptsScopedLock const&)            = delete;
    SuspendResumeAllInterruptsScopedLock& operator=(SuspendResumeAllInterruptsScopedLock const&) = delete;
};

} // namespace interrupts
