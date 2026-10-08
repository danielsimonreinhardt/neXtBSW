/* Host test stub of util/format/SharedStringWriter.h (SYS-05): appends to the CommandContext instead of
 * allocating a shared UART stream. Float is not used by RteSystem.cpp. */

#pragma once

#include "util/command/GroupCommand.h"

#include <cstdarg>
#include <cstdio>

namespace util
{
namespace format
{

class SharedStringWriter
{
public:
    explicit SharedStringWriter(::util::command::CommandContext& context) : _context(context) {}

    SharedStringWriter& printf(char const* const format, ...)
    {
        char line[512];
        va_list args;
        va_start(args, format);
        int const written = vsnprintf(line, sizeof(line), format, args);
        va_end(args);
        if (written > 0)
        {
            _context.output.append(line);
        }
        return *this;
    }

private:
    ::util::command::CommandContext& _context;
};

} // namespace format
} // namespace util
