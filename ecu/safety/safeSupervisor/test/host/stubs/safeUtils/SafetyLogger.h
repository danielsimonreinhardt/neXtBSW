/* SYS-23 host test stub: every log line is recorded, 'E' for an error and 'w' for a warning. */
#pragma once

#include <cstdio>
#include <string>

namespace test_trace
{
void add(char c);
/* SYS-169: the text of the last error line, formatted as the logger would */
inline std::string lastError;
}

namespace util
{
namespace logger
{
constexpr int SAFETY = 0;

struct Logger
{
    template<typename... Args>
    static void error(int, char const* const format, Args... args)
    {
        char line[256];
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wformat-security"
#pragma clang diagnostic ignored "-Wformat-nonliteral"
#pragma clang diagnostic ignored "-Wformat"
        (void)std::snprintf(line, sizeof(line), format, args...);
#pragma clang diagnostic pop
        ::test_trace::lastError = line;
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
