#include "uds/app/BootloaderIdentification.h"

#include "uds/app/AppDiagConfig.h"

#include <cstddef>

namespace uds
{
namespace app
{
namespace
{
bool isPrintable(uint8_t const c) { return (c >= 0x20U) && (c < 0x7FU); }

::etl::span<uint8_t const> findVersionWithPrefix(
    ::etl::span<uint8_t const> const area, char const* const prefix, size_t const prefixLength)
{
    if (area.size() <= prefixLength)
    {
        return {};
    }
    for (size_t pos = 0U; pos < (area.size() - prefixLength); ++pos)
    {
        size_t i = 0U;
        while ((i < prefixLength) && (area[pos + i] == static_cast<uint8_t>(prefix[i])))
        {
            ++i;
        }
        if (i < prefixLength)
        {
            continue;
        }
        size_t end = pos + prefixLength;
        while ((end < area.size()) && isPrintable(area[end])
               && ((end - pos) < BOOTLOADER_VERSION_MAX_LENGTH))
        {
            ++end;
        }
        // a real string literal: at least one character after the prefix and NUL terminated
        if ((end > (pos + prefixLength)) && (end < area.size()) && (area[end] == 0U))
        {
            return area.subspan(pos, end - pos);
        }
    }
    return {};
}
} // namespace

::etl::span<uint8_t const> findBootloaderVersion(::etl::span<uint8_t const> const area)
{
    // the project's identification, else the former name of the software (bootloader flashed before PLAT-08)
    ::etl::span<uint8_t const> const found = findVersionWithPrefix(
        area, BOOTLOADER_VERSION_PREFIX, sizeof(BOOTLOADER_VERSION_PREFIX) - 1U);
    if (!found.empty())
    {
        return found;
    }
    return findVersionWithPrefix(
        area, BOOTLOADER_VERSION_PREFIX_FORMER, sizeof(BOOTLOADER_VERSION_PREFIX_FORMER) - 1U);
}

} // namespace app
} // namespace uds
