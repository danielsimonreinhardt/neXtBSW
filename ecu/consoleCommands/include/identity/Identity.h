// SPDX-License-Identifier: Apache-2.0
#pragma once

/* PLAT-98: identification of the image at run time - what the console shows ("version", "info") and what prefixes
 * every log line. Every value comes from the build (ecu/consoleCommands/CMakeLists.txt): the project name of
 * project.yaml (NX_PROJECT_NAME), the neXtBSW version of the tree (VERSION, ARCH-55), board and derivative of the
 * project (NX_BOARD, NX_DERIVATIVE). Nothing here is written by hand for one project, so a customer project shows
 * its own name.
 *
 * Extension point for PLAT-79 (configuration and generator identification in the image): a generated header
 * <identity/nxIdentityFields.h>, if it is on the include path of consoleCommands, defines
 *     #define NX_IDENTITY_GENERATED_FIELDS {"configuration", "<text>", false}, {"generator", "<text>", false},
 * (a list of Field initialisers, each followed by a comma). Its entries are appended to buildFields(): "info"
 * prints every one on its own line, "version" also puts those with versionLine = true into its line. Nothing else
 * changes - the commands and their host test take the list as it is.
 */

#include <etl/span.h>

#include <cstddef>

namespace identity
{
/** One entry of the identification: name and text, both printable ASCII and NUL terminated. */
struct Field
{
    char const* name;
    char const* value;
    bool versionLine; ///< also part of the one-line output of "version"
};

/**
 * Longest application name in the prefix of a log line; a longer project name is cut there in the prefix only
 * ("version" and "info" show it in full). Keeps the characters of a printed log line per byte of the logger buffer
 * within what the console flush limit assumes (CHARACTERS_PER_LOGGER_BYTE, platforms/s32k1xx/port/main/src/
 * integration/IdleOutputDrain.h, SYS-09): the shortest entry (12 bytes) prints about 54 + name + message characters.
 */
constexpr size_t LOG_NAME_MAX_CHARS = 24U;

/** Text of the log prefix: a name cut to LOG_NAME_MAX_CHARS, terminated. */
struct LogNameText
{
    char text[LOG_NAME_MAX_CHARS + 1U];
};

/** The log prefix of an application name (compile time: logNameOf(NX_IDENTITY_APPLICATION)). */
template<size_t N>
constexpr LogNameText logNameOf(char const (&name)[N])
{
    // one bound: the shorter of the array (without its terminator) and the prefix
    constexpr size_t LIMIT = ((N - 1U) < LOG_NAME_MAX_CHARS) ? (N - 1U) : LOG_NAME_MAX_CHARS;
    LogNameText prefix{};
    size_t i = 0U;
    for (; (i < LIMIT) && (name[i] != '\0'); ++i)
    {
        prefix.text[i] = name[i];
    }
    prefix.text[i] = '\0';
    return prefix;
}

/** Name of the application: project.name of the project (NX_PROJECT_NAME). */
extern char const APPLICATION_NAME[];

/**
 * APPLICATION_NAME cut to LOG_NAME_MAX_CHARS: the name every log line carries (logger composition, logger.cpp).
 * Constant-initialised, so it may be used by the constructor of another static object.
 */
extern char const* const LOG_NAME;

/** neXtBSW version of the tree the image was built from (VERSION, MAJOR.MINOR.PATCH). */
extern char const NEXTBSW_VERSION[];

/** "<board>/<derivative>" of the project - the same text as the hardware number of the diagnosis (BL_HARDWARE_ID). */
extern char const BOARD[];

/**
 * Identification known at build time, in the order "version" prints it: application first, then neXtBSW version,
 * board and the generated entries (PLAT-79). Values found at run time (the bootloader in flash) are added by the
 * console command after the application.
 */
::etl::span<Field const> buildFields();

} // namespace identity
