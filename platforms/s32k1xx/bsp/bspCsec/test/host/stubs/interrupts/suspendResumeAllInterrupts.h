// Host stub of interrupts/suspendResumeAllInterrupts.h for the CSEc recovery test (NVM-13), see cache/cache.h.

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
    if (fakeCore.onResume != nullptr)
    {
        fakeCore.onResume();
    }
    fakeCore.interruptsOn = (old == 0U);
}
