/* SYS-151 host test stub: the interrupt lock records how deep it is nested, so the test can show that the hold reads
 * the pin and the commanded level and writes the pin with all interrupts suspended. */
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
