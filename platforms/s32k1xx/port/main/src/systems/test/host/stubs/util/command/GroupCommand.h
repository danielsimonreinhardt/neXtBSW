// Host stub (NVM-22, FlexNvmCommandHostTest.cpp): just enough of OpenBSW's GroupCommand for systems/FlexNvmCommand.h
// to compile - the test checks flexnvmrun::runPartition() and the journal, not the console group.

#pragma once

#include <cstdint>

namespace util
{
namespace command
{
class CommandContext;

class GroupCommand
{
public:
    virtual ~GroupCommand() = default;

protected:
    virtual void executeCommand(CommandContext& context, uint8_t idx) = 0;
};
} // namespace command
} // namespace util

#define DECLARE_COMMAND_GROUP_GET_INFO
