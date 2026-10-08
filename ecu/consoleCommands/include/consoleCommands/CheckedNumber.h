#pragma once

/* SYS-163 (safety review group A 2026-10-01, SR-110-2 a): a number argument of a console command without wrap-around.
 *
 * Finding PLAT-110 (doc/architecture/openbsw-console-number-arguments.md, B and C): CommandContext::scanIntToken<T>()
 * of OpenBSW computes in T without an overflow check, and a missing or invalid number returns 0 with the result
 * BAD_TOKEN. `lc level` read the level as uint8_t and used it without looking at the result: `lc level`,
 * `lc level x` and `lc level 256` ran the lifecycle down to level 0 (complete shut-down, without the safe state,
 * doc/safety/coexistence.md CX-2) although the console answered "error"; `dem setting 256` switched the DTC setting
 * off (256 is 0 in uint8_t).
 *
 * scanUnsigned() reads the next token in the spellings of scanIntToken (decimal, 0x/0X hex, leading 0 octal, a sign
 * '+' or '-' followed by decimal digits; the token ends at white space or the end of the line) and computes with a
 * limit instead of wrapping:
 *   - no token, or a token that is no number in these spellings: BAD_TOKEN (as scanIntToken reports it);
 *   - a number above `max` - however many digits, so no value wraps into the range - or a negative one (other than
 *     -0): BAD_VALUE.
 * In both cases the result of the context is set (the console answers "error"), `value` stays as it was and the
 * function returns false; the caller must not act. After it the caller checks the end of the line (checkEol()).
 *
 * Header only, no OpenBSW change, no board value; used by `lc level` (ecu/consoleCommands) and `dem setting`
 * (ecu/application RteSystem.cpp). */

#include <util/command/CommandContext.h>
#include <util/string/ConstString.h>

#include <cstddef>
#include <cstdint>

namespace consolecommands
{
namespace detail
{
/** Value of digit \p c in \p base, -1 if it is none (CommandContext::getDigit). */
inline int32_t digitOf(char const c, uint32_t const base)
{
    int32_t digit = -1;
    if ((c >= '0') && (c <= '9'))
    {
        digit = static_cast<int32_t>(c - '0');
    }
    else if ((c >= 'a') && (c <= 'z'))
    {
        digit = static_cast<int32_t>(c - 'a') + 10;
    }
    else if ((c >= 'A') && (c <= 'Z'))
    {
        digit = static_cast<int32_t>(c - 'A') + 10;
    }
    else
    {
        // no digit
    }
    return (digit < static_cast<int32_t>(base)) ? digit : -1;
}
} // namespace detail

/**
 * Reads the next token of \p context as a number from 0 to \p max into \p value (see above).
 * \return true for a number in range; false (result of \p context set, \p value unchanged) otherwise
 */
inline bool scanUnsigned(::util::command::CommandContext& context, uint32_t const max, uint32_t& value)
{
    ::util::string::ConstString const token = context.scanToken();
    if (context.getResult() != ::util::command::ICommand::Result::OK)
    {
        return false; // no token at all (BAD_TOKEN set by scanToken) or an earlier error
    }
    size_t const length = token.length();
    size_t pos          = 0U;
    uint32_t base       = 10U;
    bool negative       = false;
    if ((length > 0U) && ((token[0U] == '+') || (token[0U] == '-')))
    {
        negative = (token[0U] == '-');
        pos      = 1U;
    }
    else if ((length > 1U) && (token[0U] == '0') && ((token[1U] == 'x') || (token[1U] == 'X')))
    {
        base = 16U;
        pos  = 2U;
    }
    else if ((length > 0U) && (token[0U] == '0'))
    {
        base = 8U; // the leading 0 is a digit of the octal number
    }
    else
    {
        // decimal
    }
    size_t const first = pos;
    uint32_t number    = 0U;
    bool above         = false;
    for (; pos < length; ++pos)
    {
        int32_t const digit = detail::digitOf(token[pos], base);
        if (digit < 0)
        {
            break;
        }
        uint32_t const d = static_cast<uint32_t>(digit);
        // number * base + d > max, without computing it (no wrap-around for any number of digits)
        if (above || (d > max) || (number > ((max - d) / base)))
        {
            above = true;
        }
        else
        {
            number = (number * base) + d;
        }
    }
    if ((pos == first) || (pos != length))
    {
        return context.check(false, ::util::command::ICommand::Result::BAD_TOKEN);
    }
    if (above || (negative && (number != 0U)))
    {
        return context.check(false, ::util::command::ICommand::Result::BAD_VALUE);
    }
    value = number;
    return true;
}

} // namespace consolecommands
