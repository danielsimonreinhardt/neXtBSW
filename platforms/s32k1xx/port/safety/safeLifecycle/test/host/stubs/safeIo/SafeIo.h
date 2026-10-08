/* SYS-41 host test stub of safeIo/SafeIo.h: the sub-check records 'o'. */
#pragma once

#include "../cycleStubs.h"

namespace safety
{
class SafeIo
{
public:
    void init() {}

    void cyclic() { ::test_trace::add('o'); }
};
} // namespace safety
