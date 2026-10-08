/* SYS-41 host test stub of safeSystem/SafeSystem.h: the sub-check records 's'. */
#pragma once

#include "../cycleStubs.h"

namespace safety
{
class SafeSystem
{
public:
    void init() {}

    void cyclic() { ::test_trace::add('s'); }

    void resetAdcDiagnostics() {}
};

SafeSystem& getSafeSystem();
} // namespace safety
