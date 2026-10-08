/* SYS-51 host test stub: the interrupt lock records how deep it is nested, so the test can show that the WDOG
 * unlock window and the read-modify-writes of the LPIT run with all interrupts suspended. */
#pragma once

namespace test_trace
{
extern int lockDepth;
}

namespace interrupts
{
class SuspendResumeAllInterruptsScopedLock
{
public:
    SuspendResumeAllInterruptsScopedLock() { ++::test_trace::lockDepth; }

    ~SuspendResumeAllInterruptsScopedLock() { --::test_trace::lockDepth; }

    SuspendResumeAllInterruptsScopedLock(SuspendResumeAllInterruptsScopedLock const&)            = delete;
    SuspendResumeAllInterruptsScopedLock& operator=(SuspendResumeAllInterruptsScopedLock const&) = delete;
};
} // namespace interrupts
