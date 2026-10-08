// NOLINTBEGIN(cppcoreguidelines-pro-type-vararg): Logger/StringWriter API is variadic by design.

#include "systems/CheckedOutputPwmTester.h"

#include "bsp/safeOutputs/safeOutputsConfiguration.h"
#include "outputPwm/OutputPwm.h"
#include "util/command/CommandContext.h"
#include "util/format/SharedStringWriter.h"

namespace systems
{
namespace
{
// SYS-162: the PWM groups of the safe outputs table - its first block, before the safe_off outputs
constexpr int32_t SAFE_PWM_GROUP_COUNT
    = static_cast<int32_t>(NX_SAFE_OUTPUT_COUNT) - static_cast<int32_t>(NX_SAFE_OFF_OUTPUT_COUNT);

constexpr uint32_t FIRST_GENERATED_CHANNEL = static_cast<uint32_t>(::bios::OutputPwm::_pwmDynamicStart);
constexpr uint32_t END_OF_CHANNELS         = static_cast<uint32_t>(::bios::OutputPwm::_pwmChannelMax);

static_assert(SAFE_PWM_GROUP_COUNT >= 0, "the PWM groups are the first block of the safe outputs table");
static_assert(
    (SAFE_PWM_GROUP_COUNT > 0) || (END_OF_CHANNELS == FIRST_GENERATED_CHANNEL),
    "generated PWM channels without a PWM group in the safe outputs table: tools/bswgen generates OutputPwm channels "
    "only for the pairs of the used PWM groups and puts each of them into the table - configure again; a PWM channel "
    "outside the table needs a mapping per group in CheckedOutputPwmTester (SYS-162)");
} // namespace

bool CheckedOutputPwmTester::isSafetyPwmChannel(uint32_t const channel)
{
    return (SAFE_PWM_GROUP_COUNT > 0) && (channel >= FIRST_GENERATED_CHANNEL) && (channel < END_OF_CHANNELS);
}

void CheckedOutputPwmTester::executeCommand(::util::command::CommandContext& context, uint8_t const idx)
{
    if ((idx == SET_COMMAND) || (idx == SET_PERIOD_COMMAND))
    {
        // the line as OutputPwmTester will read it: the same scanner on the same remaining arguments
        ::util::command::CommandContext peek(context.getSuffix());
        uint32_t const channel = peek.scanIntToken<uint32_t>();
        (void)peek.scanIntToken<uint32_t>(); // duty or period, clamped by OutputPwmTester
        (void)peek.checkEol();
        if (peek.getResult() != ::util::command::ICommand::Result::OK)
        {
            // P2: a missing or invalid number scans as 0 in OutputPwmTester, which then writes channel 0 or duty 0
            (void)context.check(false, peek.getResult());
            return;
        }
        bool const all = (idx == SET_COMMAND) && (channel == ALL_CHANNELS);
        if ((channel >= static_cast<uint32_t>(::bios::OutputPwm::outputPwmNumberAll)) && !all)
        {
            // P1: OutputPwmTester calls setPeriod() for such a channel, narrowed to uint16_t (65536 + k is k)
            (void)context.check(false, ::util::command::ICommand::Result::BAD_VALUE);
            return;
        }
        // SYS-162: no duty or period of a PWM group of the safe outputs table from the console ("all" includes them)
        if (all ? isSafetyPwmChannel(FIRST_GENERATED_CHANNEL) : isSafetyPwmChannel(channel))
        {
            ::util::format::SharedStringWriter out(context);
            (void)out.printf(
                "%d : %s -> refused: channel of a PWM group of the safe outputs table, driven by its function only\r\n",
                static_cast<int>(channel),
                all ? "all" : ::bios::OutputPwm::getName(static_cast<uint16_t>(channel)));
            (void)context.check(false, ::util::command::ICommand::Result::BAD_VALUE);
            return;
        }
    }
    ::bios::OutputPwmTester::executeCommand(context, idx);
}

} // namespace systems

// NOLINTEND(cppcoreguidelines-pro-type-vararg)
