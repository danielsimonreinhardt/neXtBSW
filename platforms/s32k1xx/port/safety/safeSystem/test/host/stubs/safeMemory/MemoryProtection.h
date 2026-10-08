/* Host test stub of safeMemory/MemoryProtection.h (IO-16): counts the gate calls, so the test
 * can check that the ADC readings stay inside the fusa gate. */

#pragma once

#include <cstdint>

namespace safety
{
class MemoryProtection
{
public:
    static uint32_t closeCalls;
    static uint32_t openCalls;
    static bool gateClosed;

    static void fusaGateClose()
    {
        ++closeCalls;
        gateClosed = true;
    }

    static void fusaGateOpen()
    {
        ++openCalls;
        gateClosed = false;
    }

    static void reset()
    {
        closeCalls = 0U;
        openCalls  = 0U;
        gateClosed = false;
    }
};

} // namespace safety
