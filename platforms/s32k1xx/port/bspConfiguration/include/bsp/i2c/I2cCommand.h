// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "util/command/GroupCommand.h"

namespace bsp
{
namespace i2c
{

/**
 * IO-28: console group "i2c" over the used I2C buses (bsp/i2c/I2cBuses.h). <bus> is the board name of the bus
 * (i2c[].name), numbers are decimal or 0x hex. Transfers run in the task of the console and block it for at most
 * the time limit of the bus (project i2c.<bus>.timeout_ms) per call.
 *   buses                              buses with requested and selected SCL rate and error counter
 *   probe <bus> <addr>                 address byte with STOP, ACK or the reason why not
 *   scan <bus>                         probe 0x08..0x77, list the addresses that acknowledge
 *   rd <bus> <addr> <n> [<byte>...]    write the bytes without STOP (register address), then read n bytes
 *   wr <bus> <addr> [<byte>...]        write the bytes with STOP
 * Register it like the other testers (AsyncCommandWrapper in the console context).
 */
class I2cCommand final : public ::util::command::GroupCommand
{
public:
    I2cCommand() {}

protected:
    DECLARE_COMMAND_GROUP_GET_INFO
    void executeCommand(::util::command::CommandContext& context, uint8_t idx) override;
};

} // namespace i2c
} // namespace bsp
