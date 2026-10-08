// Host stub of the OpenBSW system timer contract (IO-52): the test owns the microsecond time. Every read advances it
// by one microsecond and is counted, so a dispatcher that stamps each edge with its own read (instead of once per
// interrupt) or reads the time for an interrupt that is not its own is visible.
#pragma once

#include <cstdint>

extern uint32_t nxTestNowUs;
extern unsigned nxTestTimeReads;

inline uint32_t getSystemTimeUs32Bit(void)
{
    ++nxTestTimeReads;
    return nxTestNowUs++;
}
