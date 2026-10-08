// NVM-17: hex fields of the csec console lines (M1..M3, AUTHORIZATION in; M4/M5, UID, CHALLENGE out), exactly the
// format tools/csec prints: upper-case hex digits, no separator. Reading also takes lower case; a field of another
// length or with another character is refused as a whole (nothing of it is used). Header only, no hardware.

#pragma once

#include <cstddef>
#include <cstdint>

namespace platform
{
namespace csechex
{
inline bool digit(char const c, uint8_t& value)
{
    if ((c >= '0') && (c <= '9'))
    {
        value = static_cast<uint8_t>(c - '0');
        return true;
    }
    if ((c >= 'A') && (c <= 'F'))
    {
        value = static_cast<uint8_t>((c - 'A') + 10);
        return true;
    }
    if ((c >= 'a') && (c <= 'f'))
    {
        value = static_cast<uint8_t>((c - 'a') + 10);
        return true;
    }
    return false;
}

/** \p length characters of \p text into exactly \p bytes bytes of \p out; false (out untouched) otherwise. */
inline bool parse(char const* const text, size_t const length, uint8_t* const out, size_t const bytes)
{
    if ((text == nullptr) || (length != (2U * bytes)))
    {
        return false;
    }
    for (size_t i = 0U; i < length; ++i)
    {
        uint8_t value = 0U;
        if (!digit(text[i], value))
        {
            return false;
        }
    }
    for (size_t i = 0U; i < bytes; ++i)
    {
        uint8_t high = 0U;
        uint8_t low  = 0U;
        (void)digit(text[2U * i], high);
        (void)digit(text[(2U * i) + 1U], low);
        out[i] = static_cast<uint8_t>((high << 4U) | low);
    }
    return true;
}

/** \p bytes bytes as upper-case hex into \p out (2 * bytes + 1 characters, terminated). */
inline void format(uint8_t const* const in, size_t const bytes, char* const out)
{
    constexpr char DIGITS[] = "0123456789ABCDEF";
    for (size_t i = 0U; i < bytes; ++i)
    {
        out[2U * i]         = DIGITS[in[i] >> 4U];
        out[(2U * i) + 1U]  = DIGITS[in[i] & 0xFU];
    }
    out[2U * bytes] = '\0';
}
} // namespace csechex
} // namespace platform
