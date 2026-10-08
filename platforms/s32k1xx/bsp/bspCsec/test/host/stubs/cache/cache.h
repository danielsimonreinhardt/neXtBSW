// Host stub of cache/cache.h (bspCore) for the CSEc recovery test (NVM-13): records whether the code cache is on.

#pragma once

#include <cstdint>

struct FakeCore
{
    bool cacheOn        = true;
    bool interruptsOn   = true;
    uint32_t suspends   = 0U;
    uint32_t resumes    = 0U;
    uint32_t invalidates = 0U;
    void (*onResume)()   = nullptr; // NVM-17: the test looks at the journal when the interrupts come back
};

extern FakeCore fakeCore;

inline void cacheInvalidate(uint32_t, uint32_t) { ++fakeCore.invalidates; }

inline void cacheDisable() { fakeCore.cacheOn = false; }

inline void cacheEnable() { fakeCore.cacheOn = true; }
