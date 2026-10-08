// SPDX-License-Identifier: Apache-2.0
#pragma once

/* PLAT-98: text of the console commands "version" and "info" (identity/console/IdentityCommand.h), pure and
 * header only for the host test (ecu/consoleCommands/test/host/IdentityHostTest.cpp). Out is anything with
 * write(char const*): the SharedStringWriter of the command, a string in the test. */

#include "identity/Identity.h"

#include <etl/span.h>

#include <cstddef>
#include <cstdint>

namespace identity
{
/** Longest bootloader text shown (the string the diagnosis finds in the bootloader's flash, F180). */
constexpr size_t BOOTLOADER_TEXT_MAX_CHARS = 32U;

/** Text of the bootloader when none was found in its flash (no UDS in the image, or no version string). */
constexpr char const BOOTLOADER_UNKNOWN[] = "unknown";

/** Width of the name column of "info" (longest name "configuration" plus two blanks). */
constexpr size_t INFO_NAME_COLUMN = 15U;

/**
 * The bootloader version string found in flash as text: printable ASCII up to the first other byte (NUL), at most
 * BOOTLOADER_TEXT_MAX_CHARS; BOOTLOADER_UNKNOWN if nothing printable is there. Flash is never trusted to be a
 * terminated string.
 */
inline void bootloaderText(::etl::span<uint8_t const> const found, char (&out)[BOOTLOADER_TEXT_MAX_CHARS + 1U])
{
    size_t n = 0U;
    while ((n < found.size()) && (n < BOOTLOADER_TEXT_MAX_CHARS) && (found[n] >= 0x20U) && (found[n] < 0x7FU))
    {
        out[n] = static_cast<char>(found[n]);
        ++n;
    }
    if (n == 0U)
    {
        for (; BOOTLOADER_UNKNOWN[n] != '\0'; ++n)
        {
            out[n] = BOOTLOADER_UNKNOWN[n];
        }
    }
    out[n] = '\0';
}

namespace detail
{
/**
 * Calls visit(field) in the order of the output: the application (first build field), then the run-time fields,
 * then the remaining build fields.
 */
template<class Visit>
void forEachField(::etl::span<Field const> const build, ::etl::span<Field const> const runtime, Visit&& visit)
{
    if (!build.empty())
    {
        visit(build[0U]);
    }
    for (Field const& field : runtime)
    {
        visit(field);
    }
    for (size_t i = 1U; i < build.size(); ++i)
    {
        visit(build[i]);
    }
}

inline char const* valueOf(Field const& field) { return (field.value != nullptr) ? field.value : "?"; }
} // namespace detail

/**
 * One line: "application <name>, bootloader <version>, neXtBSW <version>, board <board>/<derivative>" and every
 * further field marked versionLine, then "\n".
 */
template<class Out>
void writeVersionLine(Out& out, ::etl::span<Field const> const build, ::etl::span<Field const> const runtime)
{
    bool first = true;
    detail::forEachField(
        build,
        runtime,
        [&out, &first](Field const& field)
        {
            if (!field.versionLine)
            {
                return;
            }
            if (!first)
            {
                out.write(", ");
            }
            first = false;
            out.write(field.name);
            out.write(" ");
            out.write(detail::valueOf(field));
        });
    out.write("\n");
}

/** Every field on its own line, the name in a column of INFO_NAME_COLUMN characters. */
template<class Out>
void writeInfo(Out& out, ::etl::span<Field const> const build, ::etl::span<Field const> const runtime)
{
    detail::forEachField(
        build,
        runtime,
        [&out](Field const& field)
        {
            out.write(field.name);
            size_t length = 0U;
            while (field.name[length] != '\0')
            {
                ++length;
            }
            do
            {
                out.write(" ");
                ++length;
            } while (length < INFO_NAME_COLUMN);
            out.write(detail::valueOf(field));
            out.write("\n");
        });
}

} // namespace identity
