#pragma once

#include <etl/span.h>

#include <cstdint>

namespace uds
{
namespace app
{
/* Longest version string findBootloaderVersion() returns */
static constexpr uint8_t BOOTLOADER_VERSION_MAX_LENGTH = 32U;

/**
 * Finds the bootloader version string (BOOTLOADER_VERSION_PREFIX followed by printable characters,
 * terminated by NUL) in \p area; without one, a string with BOOTLOADER_VERSION_PREFIX_FORMER (PLAT-08).
 * Returns the string without NUL, or an empty span.
 *
 * What is searched for is the plain text, as every bootloader stores it - before and after BL-22. The
 * numberOfModules byte of ISO 14229-1 Annex C.1 belongs to the response, not to the string: since COM-28 the
 * diagnostic description (diag.yaml, identifier F180) puts it in front of this text (`bootloader.version`, a
 * value of the diagnostic stack, tools/diaggen). So an application of any age finds a bootloader of any age,
 * and both report the same text.
 */
::etl::span<uint8_t const> findBootloaderVersion(::etl::span<uint8_t const> area);

} // namespace app
} // namespace uds
