/* Host test stub of safeUtils/SafetyLogger.h for RomCheckHostTest (SYS-38): counts the log lines per level. */

#pragma once

namespace util
{
namespace logger
{
struct Component
{};

constexpr Component SAFETY{};

struct Logger
{
    static int& errors()
    {
        static int count = 0;
        return count;
    }

    static int& debugs()
    {
        static int count = 0;
        return count;
    }

    template<typename... Args>
    static void error(Component, char const*, Args...)
    {
        ++errors();
    }

    template<typename... Args>
    static void debug(Component, char const*, Args...)
    {
        ++debugs();
    }
};
} // namespace logger
} // namespace util
