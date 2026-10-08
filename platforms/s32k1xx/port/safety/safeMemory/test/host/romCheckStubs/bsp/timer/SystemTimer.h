/* Host test stub of bsp/timer/SystemTimer.h for RomCheckHostTest (SYS-44): the system time is set by the test. */

#pragma once

#include <cstdint>

namespace romcheck_test
{
inline uint32_t& nowMs()
{
    static uint32_t now = 0U;
    return now;
}
} // namespace romcheck_test

inline uint32_t getSystemTimeMs32Bit() { return ::romcheck_test::nowMs(); }
