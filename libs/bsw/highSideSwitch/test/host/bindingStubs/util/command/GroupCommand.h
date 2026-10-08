#pragma once

// Host stub of the OpenBSW group command for HighSideSwitchBindingHostTest.cpp (ARCH-66).

#include <cstdint>

namespace util
{
namespace command
{
class CommandContext;

class GroupCommand
{
public:
    struct PlainCommandInfo
    {
        char const* id;
        char const* description;
    };

protected:
    virtual PlainCommandInfo const* getInfo() const                   = 0;
    virtual void executeCommand(CommandContext& context, uint8_t idx) = 0;
    ~GroupCommand()                                                   = default;
};
} // namespace command
} // namespace util
