// Host stub of interrupts/suspendResumeAllInterrupts.h for the FlexNVM partition test (NVM-07), see cache/cache.h.

#pragma once

#include "cache/cache.h"

#include <cstdint>

inline uint32_t getMachineStateRegisterValueAndSuspendAllInterrupts()
{
    ++fakeCore.suspends;
    uint32_t const old    = fakeCore.interruptsOn ? 0U : 1U;
    fakeCore.interruptsOn = false;
    return old;
}

inline void resumeAllInterrupts(uint32_t const old)
{
    ++fakeCore.resumes;
    fakeCore.interruptsOn = (old == 0U);
    if (fakeCore.interruptsOn && (fakeCore.onResume != nullptr))
    {
        void (*const other)() = fakeCore.onResume;
        fakeCore.onResume     = nullptr; // the other task runs once; it may arm itself again
        other();
    }
}
