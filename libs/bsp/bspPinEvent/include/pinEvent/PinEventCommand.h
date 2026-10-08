// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "pinEvent/PinEvent.h"

#include <util/command/GroupCommand.h>

namespace pinevent
{

/**
 * Console group "pinevent" (IO-53):
 *   show    per input: counter, time stamp of the last accepted edge, qualifier, trips of the rate limit,
 *           bounces, glitches, armed or the time until the automatic re-arm
 *   config  per input: edge, debounce, rate limit, hold-off and the edges the detector reports
 * Read only and lock free (PinEventConsole.h), so it runs in the console context directly. The integration
 * (IO-54) creates one instance for the table of the project and registers it in the console context.
 */
class PinEventCommand final : public ::util::command::GroupCommand
{
public:
    explicit PinEventCommand(PinEvents const& events) : _events(events) {}

protected:
    DECLARE_COMMAND_GROUP_GET_INFO
    void executeCommand(::util::command::CommandContext& context, uint8_t idx) override;

private:
    PinEvents const& _events;
};

} // namespace pinevent
