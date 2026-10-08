// SPDX-License-Identifier: Apache-2.0
#pragma once

/* PLAT-98: console commands "version" (one line: application, bootloader, neXtBSW version, board) and "info" (the
 * same and every further entry of the identification, one per line, plus the state of the ROM check where the image
 * has one). The texts come from identity/IdentityText.h, the values from identity/Identity.h.
 *
 * What only the application knows is handed in as functions (nullptr: not in this image):
 * - the bootloader version string in flash (the search of the diagnosis, ecu/uds BootloaderIdentification.h),
 * - the state of the ROM check with its CRC unit self-test (SYS-44, safeMemory/RomCheck.h).
 * So this library depends on neither of them. Both commands only read; they run in the console context
 * (register with ::console::CONSOLE_CONTEXT, ecu/consoleCommands/test/test_console_context.py).
 */

#include "identity/IdentityText.h"

#include <util/command/CommandContext.h>
#include <util/command/SimpleCommand.h>

#include <etl/span.h>

#include <cstdint>

namespace identity
{
/** State of the ROM check (SM-14) as "info" prints it. */
struct RomCheckStatus
{
    char const* result;     ///< result of the last pass, as text (a pass starts with the CRC unit self-test)
    uint32_t passes;        ///< evaluated passes since start
    uint32_t discarded;     ///< passes thrown away because of a flash write
    uint32_t postponements; ///< due passes that waited for a flash write
    uint32_t periodMs;      ///< re-check period of the project, 0 = once per start
};

class IdentityCommand
{
public:
    /** Bootloader version string in flash (not necessarily terminated), empty if none. */
    using BootloaderLookup = ::etl::span<uint8_t const> (*)();
    /** Fills the state of the ROM check. */
    using RomCheckLookup = void (*)(RomCheckStatus& status);

    IdentityCommand(BootloaderLookup bootloader, RomCheckLookup romCheck);

    ::util::command::ICommand& versionCommand() { return _version; }

    ::util::command::ICommand& infoCommand() { return _info; }

private:
    void executeVersion(::util::command::CommandContext& context);
    void executeInfo(::util::command::CommandContext& context);
    ::etl::span<Field const> runtimeFields();

    BootloaderLookup _bootloaderLookup;
    RomCheckLookup _romCheckLookup;
    bool _bootloaderKnown;
    char _bootloader[BOOTLOADER_TEXT_MAX_CHARS + 1U];
    Field _runtime[1];
    ::util::command::SimpleCommand _version;
    ::util::command::SimpleCommand _info;
};

} // namespace identity
