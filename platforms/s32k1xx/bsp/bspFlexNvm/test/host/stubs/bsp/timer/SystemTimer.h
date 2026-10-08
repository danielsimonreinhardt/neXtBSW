// Host stub of bsp/timer/SystemTimer.h for the FlexNVM tests (NVM-07): a system time in microseconds that the test
// advances by a fixed step per read (fakeTimeStepUs), so a wait bounded in time can be checked without real time.

#pragma once

#include <cstdint>

extern uint32_t fakeTimeUs;
extern uint32_t fakeTimeStepUs;

inline uint32_t getSystemTimeUs32Bit()
{
    uint32_t const now = fakeTimeUs;
    fakeTimeUs += fakeTimeStepUs;
    return now;
}
