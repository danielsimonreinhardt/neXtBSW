/* SYS-41 host test stub of safeMemory/MemoryProtection.h for the ECC poll: the MPU descriptors are in order. */
#pragma once

#include <cstdint>

namespace safety
{
class MemoryProtection
{
public:
    static bool areRegionsConfiguredCorrectly(uint8_t& failedRegion)
    {
        failedRegion = 0U;
        return true;
    }
};
} // namespace safety
