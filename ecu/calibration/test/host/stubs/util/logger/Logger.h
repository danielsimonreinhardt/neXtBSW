// Host stub of the OpenBSW logger for the plausibility host test (SYS-90): the last line in a buffer, as the target
// would format it (vsnprintf), and its level - so the test sees what PlausibilityMonitor.cpp logs.
#pragma once

#include <cstdarg>
#include <cstdio>

namespace util
{
namespace logger
{
struct Component
{
    char const* name;
};

struct HostLog
{
    char line[512];
    bool error;
    unsigned count;
};

inline HostLog hostLog{{0}, false, 0U};

class Logger
{
public:
    static void info(Component const&, char const* const format, ...)
    {
        va_list args;
        va_start(args, format);
        record(false, format, args);
        va_end(args);
    }

    static void error(Component const&, char const* const format, ...)
    {
        va_list args;
        va_start(args, format);
        record(true, format, args);
        va_end(args);
    }

private:
    static void record(bool const error, char const* const format, va_list args)
    {
        (void)std::vsnprintf(hostLog.line, sizeof(hostLog.line), format, args);
        hostLog.error = error;
        ++hostLog.count;
    }
};
} // namespace logger
} // namespace util
