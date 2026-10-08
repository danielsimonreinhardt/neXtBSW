/* SYS-41 host test stub of safeMemory/MemoryProtection.h: the fusa gate as one flag. */
#pragma once

#include "../cycleStubs.h"

namespace safety
{
class MemoryProtection
{
public:
    static bool fusaGateIsLocked() { return ::test_cycle::gateLocked; }
};
} // namespace safety
