/* SYS-41 host test stub of safeUtils/SafetyLogger.h for the safety cycle: like the supervisor's stub ('E' error,
 * 'w' warning) plus debug, which records nothing. */
#pragma once

#include "../cycleStubs.h"

namespace util
{
namespace logger
{
constexpr int SAFETY = 0;

struct Logger
{
    template<typename... Args>
    static void debug(int, char const*, Args...)
    {}

    template<typename... Args>
    static void error(int, char const*, Args...)
    {
        ::test_trace::add('E');
    }

    template<typename... Args>
    static void warn(int, char const*, Args...)
    {
        ::test_trace::add('w');
    }
};
} // namespace logger
} // namespace util
