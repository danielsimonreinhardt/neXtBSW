/* SYS-41 host test stub of safeMemory/ProtectedRamScopedUnlock.h: opens the gate flag for its scope. */
#pragma once

#include "../cycleStubs.h"

namespace safety
{
class ProtectedRamScopedUnlock final
{
public:
    ProtectedRamScopedUnlock() : _old(::test_cycle::gateLocked) { ::test_cycle::gateLocked = false; }

    ~ProtectedRamScopedUnlock() { ::test_cycle::gateLocked = _old; }

    ProtectedRamScopedUnlock(ProtectedRamScopedUnlock const&)            = delete;
    ProtectedRamScopedUnlock& operator=(ProtectedRamScopedUnlock const&) = delete;

private:
    bool _old;
};
} // namespace safety
