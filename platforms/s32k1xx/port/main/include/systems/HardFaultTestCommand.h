// Console command "hardfault" (PLAT-20): triggers hard faults on purpose to test the dump. Only built with the
// CMake option NX_HARDFAULT_TEST_COMMAND=ON (test builds), never part of the standard demo.

#pragma once

#include <util/command/GroupCommand.h>

namespace systems
{

class HardFaultTestCommand : public ::util::command::GroupCommand
{
protected:
    DECLARE_COMMAND_GROUP_GET_INFO
    void executeCommand(::util::command::CommandContext& context, uint8_t idx) override;
};

} // namespace systems
