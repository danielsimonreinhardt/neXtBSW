/* SYS-41 host test stub of safeMemory/SafeMemory.h: the sub-check records 'm'. */
#pragma once

#include "../cycleStubs.h"

namespace safety
{
namespace safe_memory
{
inline void cyclic() { ::test_trace::add('m'); }
} // namespace safe_memory
} // namespace safety
