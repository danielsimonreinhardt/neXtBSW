// SPDX-License-Identifier: Apache-2.0
#include "pinEvent/PinEventCommand.h"

#include "pinEvent/PinEventConsole.h"

#include <util/format/SharedStringWriter.h>

namespace pinevent
{

DEFINE_COMMAND_GROUP_GET_INFO_BEGIN(PinEventCommand, "pinevent", "Events on digital inputs (pin interrupts)")
COMMAND_GROUP_COMMAND(1U, "show", "counter, last edge, qualifier, trips per input")
COMMAND_GROUP_COMMAND(2U, "config", "edge, debounce, rate limit, re-arm per input")
DEFINE_COMMAND_GROUP_GET_INFO_END;

void PinEventCommand::executeCommand(::util::command::CommandContext& context, uint8_t const idx)
{
    ::util::format::SharedStringWriter out(context);
    switch (idx)
    {
        case 1U: console::writeShow(out, _events); break;
        case 2U: console::writeConfig(out, _events); break;
        default: break;
    }
}

} // namespace pinevent
