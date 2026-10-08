// NOLINTBEGIN(cppcoreguidelines-pro-type-vararg): Logger/StringWriter API is variadic by design.

#include "systems/SafeOffOutputTester.h"

#include "integration/SafeOffOutputs.h"
#include "outputManager/Output.h"
#include "util/command/CommandContext.h"
#include "util/format/SharedStringWriter.h"

namespace systems
{

#ifdef NX_PRODUCT_BUILD
// SYS-119: "out" of the product image - the OpenBSW group without "set" (same ids, same texts for "all" and "get")
DEFINE_COMMAND_GROUP_GET_INFO_BEGIN(SafeOffOutputTester, "out", "outputs console")
COMMAND_GROUP_COMMAND(1U, "all", "print all outputs")
COMMAND_GROUP_COMMAND(GET_COMMAND, "get", "get output value (param: <output-id>)")
DEFINE_COMMAND_GROUP_GET_INFO_END
#endif

void SafeOffOutputTester::executeCommand(::util::command::CommandContext& context, uint8_t const idx)
{
#ifdef NX_PRODUCT_BUILD
    if (idx == SET_COMMAND)
    {
        // not listed in the product image (getInfo above); refused here as well, so no path reaches Output::set
        (void)context.check(false, ::util::command::ICommand::Result::BAD_VALUE);
        return;
    }
#endif
    if ((idx == SET_COMMAND) || (idx == GET_COMMAND))
    {
        // the output id as OutputTester will read it: the same scanner on the same remaining arguments
        ::util::command::CommandContext peek(context.getSuffix());
        uint32_t const outputNo = peek.scanIntToken<uint32_t>();
        if (peek.getResult() == ::util::command::ICommand::Result::OK)
        {
            if (outputNo >= static_cast<uint32_t>(::bios::Output::TOTAL_NUMBER_OF_OUTPUTS))
            {
                // OpenBSW checks the range only after casting to OutputId - one byte on the target (AAPCS short
                // enums), so 256 + n would reach output n. Refused here on the number itself, as OpenBSW answers
                // an id out of range: no text, BAD_VALUE.
                (void)context.check(false, ::util::command::ICommand::Result::BAD_VALUE);
                return;
            }
#ifndef NX_PRODUCT_BUILD // SYS-119: no "set" in the product image
            if ((idx == SET_COMMAND) && ::integration::safeoff::isSafeOff(outputNo))
            {
                ::util::format::SharedStringWriter out(context);
                (void)out.printf(
                    "%d : %s -> refused: safe_off output, switched by its driver only\r\n",
                    static_cast<int>(outputNo),
                    ::bios::Output::getName(static_cast<uint16_t>(outputNo)));
                (void)context.check(false, ::util::command::ICommand::Result::BAD_VALUE);
                return;
            }
#endif
        }
    }
    ::bios::OutputTester::executeCommand(context, idx);
}

} // namespace systems

// NOLINTEND(cppcoreguidelines-pro-type-vararg)
