// Host stub of the OpenBSW system timer contract (IO-07): the test owns the microsecond time, so the 32-bit
// wrap of the real counter can be crossed on purpose in a few steps. It also counts reads of the time outside the
// interrupt lock (nxTestLockDepth of the lock stub), so a get() that takes the time before the lock is visible.
#pragma once

#include <cstdint>

extern uint32_t nxTestNowUs;
extern unsigned nxTestLockDepth;
extern unsigned nxTestTimeReadsUnlocked;

inline uint32_t getSystemTimeUs32Bit(void)
{
    if (nxTestLockDepth == 0U)
    {
        ++nxTestTimeReadsUnlocked;
    }
    return nxTestNowUs;
}
