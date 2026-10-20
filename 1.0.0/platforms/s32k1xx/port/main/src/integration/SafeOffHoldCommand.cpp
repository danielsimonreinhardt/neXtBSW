// NOLINTBEGIN(cppcoreguidelines-pro-type-vararg): StringWriter API is variadic by design.

#include "SafeOffHoldCommand.h"

#include "outputManager/Output.h"
#include "util/command/CommandContext.h"
#include "util/format/SharedStringWriter.h"

namespace integration
{

// clang-format off
DEFINE_COMMAND_GROUP_GET_INFO_BEGIN(SafeOffHoldCommand, "safeoff", "safe_off outputs: hold of the commanded level")
COMMAND_GROUP_COMMAND(SafeOffHoldCommand::SHOW_COMMAND, "show", "per output: commanded, pin, foreign levels taken back")
DEFINE_COMMAND_GROUP_GET_INFO_END
// clang-format on

void SafeOffHoldCommand::executeCommand(::util::command::CommandContext& context, uint8_t const idx)
{
    if ((idx != SHOW_COMMAND) || !context.checkEol())
    {
        return;
    }
    Snapshot snapshot = {};
    _take(snapshot);
    ::util::format::SharedStringWriter out(context);
    uint8_t const count = (snapshot.count < MAX_OUTPUTS) ? snapshot.count : MAX_OUTPUTS;
    if ((safeoff::COUNT == 0U) || (count == 0U))
    {
        (void)out.printf("no safe_off output\r\n");
        return;
    }
    for (uint8_t i = 0U; i < count; ++i)
    {
        int32_t const output = safeoff::outputOf(i);
        (void)out.printf(
            "%d : %s -> commanded %d, pin %d, taken back %u\r\n",
            static_cast<int>(output),
            ::bios::Output::getName(static_cast<uint16_t>(output)),
            snapshot.commanded[i] ? 1 : 0,
            snapshot.active[i] ? 1 : 0,
            static_cast<unsigned>(snapshot.takeBacks[i]));
    }
    (void)out.printf("taken back in total: %u\r\n", static_cast<unsigned>(snapshot.total));
    // SYS-194: the take-backs the hold has reported (DEVICE_REPORT, S3); below the total only while one is due
    (void)out.printf(
        "reported to the safe supervisor: %u in %u DEVICE_REPORT(s)\r\n",
        static_cast<unsigned>(snapshot.reported),
        static_cast<unsigned>(snapshot.reports));
}

} // namespace integration

// NOLINTEND(cppcoreguidelines-pro-type-vararg)
