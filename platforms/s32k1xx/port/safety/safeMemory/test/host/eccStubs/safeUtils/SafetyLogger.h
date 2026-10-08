/* SYS-41 host test stub of safeUtils/SafetyLogger.h for the ECC poll: counts the debug lines and keeps the first
 * argument of the last one (the address of a RAM error). */
#pragma once

#include <cstdint>

namespace test_log
{
extern uint32_t lines;
extern uint32_t lastValue;

inline void record() { ++lines; }

inline void record(uint32_t const value)
{
    ++lines;
    lastValue = value;
}
} // namespace test_log

namespace util
{
namespace logger
{
constexpr int SAFETY = 0;

struct Logger
{
    template<typename... Args>
    static void debug(int, char const*, Args... args)
    {
        ::test_log::record(args...);
    }
};
} // namespace logger
} // namespace util
