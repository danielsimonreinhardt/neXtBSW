/* SYS-95 host test stub of the OpenBSW bsp/timer/SystemTimer.h: the system time in ms is scripted by the test. */
#pragma once

#include <cstdint>

namespace test_time
{
inline uint32_t nowMs = 0U;
inline int reads      = 0;
} // namespace test_time

inline uint32_t getSystemTimeMs32Bit()
{
    ++test_time::reads;
    return test_time::nowMs;
}
