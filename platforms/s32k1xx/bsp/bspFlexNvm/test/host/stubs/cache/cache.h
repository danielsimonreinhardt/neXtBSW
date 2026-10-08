// Host stub of cache/cache.h (bspCore) for the FlexNVM partition test (NVM-07): records the calls, so the test can
// check that OpenBSW's EepromDriver runs the command with the code cache off and interrupts suspended.

#pragma once

#include <cstdint>

struct FakeCore
{
    uint32_t cacheDisables = 0U;
    uint32_t cacheEnables  = 0U;
    uint32_t invalidates   = 0U;
    bool cacheOn           = true;
    bool interruptsOn      = true;
    uint32_t suspends      = 0U;
    uint32_t resumes       = 0U;
    // NVM-25: called when interrupts are enabled again - a point where another task could run; the test plays that
    // task there (an EEPROM store, a data flash launch, a store that is still running when the reader goes on)
    void (*onResume)()     = nullptr;
};

extern FakeCore fakeCore;

inline void cacheInvalidate(uint32_t, uint32_t) { ++fakeCore.invalidates; }

inline void cacheDisable()
{
    ++fakeCore.cacheDisables;
    fakeCore.cacheOn = false;
}

inline void cacheEnable()
{
    ++fakeCore.cacheEnables;
    fakeCore.cacheOn = true;
}
