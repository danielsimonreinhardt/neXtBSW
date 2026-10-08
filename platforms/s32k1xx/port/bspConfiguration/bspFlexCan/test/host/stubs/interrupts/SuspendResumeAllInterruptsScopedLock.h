// SPDX-License-Identifier: Apache-2.0
// Host stub of the interrupt lock (bspInterrupts) for the FlexCAN host test (COM-26): single threaded, no lock.
#pragma once

namespace interrupts
{
class SuspendResumeAllInterruptsScopedLock
{
public:
    SuspendResumeAllInterruptsScopedLock() {}
};
} // namespace interrupts
