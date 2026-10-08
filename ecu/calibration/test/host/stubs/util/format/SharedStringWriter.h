// Host stub of the console writer for the plausibility host test (SYS-90): collects the output in a string.
#pragma once

#include <cstdarg>
#include <cstdio>
#include <string>

namespace util
{
namespace format
{
class SharedStringWriter
{
public:
    SharedStringWriter& printf(char const* const format, ...)
    {
        char buffer[512];
        va_list args;
        va_start(args, format);
        (void)std::vsnprintf(buffer, sizeof(buffer), format, args);
        va_end(args);
        text += buffer;
        return *this;
    }

    std::string text;
};
} // namespace format
} // namespace util
