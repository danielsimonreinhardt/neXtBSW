// SPDX-License-Identifier: Apache-2.0
#include "identity/console/IdentityCommand.h"

#include <util/format/SharedStringWriter.h>

namespace identity
{
IdentityCommand::IdentityCommand(BootloaderLookup const bootloader, RomCheckLookup const romCheck)
: _bootloaderLookup(bootloader)
, _romCheckLookup(romCheck)
, _bootloaderKnown(false)
, _bootloader()
, _runtime{{"bootloader", _bootloader, true}}
, _version(
      "version",
      "application, bootloader, neXtBSW version and board in one line",
      ::util::command::SimpleCommand::ExecuteFunction::create<IdentityCommand, &IdentityCommand::executeVersion>(
          *this))
, _info(
      "info",
      "identification of the image, one entry per line, and the state of the ROM check",
      ::util::command::SimpleCommand::ExecuteFunction::create<IdentityCommand, &IdentityCommand::executeInfo>(
          *this))
{}

::etl::span<Field const> IdentityCommand::runtimeFields()
{
    if (!_bootloaderKnown)
    {
        // The bootloader does not change while the application runs (an update resets): search its flash once
        bootloaderText(
            (_bootloaderLookup != nullptr) ? _bootloaderLookup() : ::etl::span<uint8_t const>(), _bootloader);
        _bootloaderKnown = true;
    }
    return ::etl::span<Field const>(_runtime);
}

void IdentityCommand::executeVersion(::util::command::CommandContext& context)
{
    if (context.checkEol())
    {
        ::util::format::SharedStringWriter writer(context);
        writeVersionLine(writer, buildFields(), runtimeFields());
    }
}

void IdentityCommand::executeInfo(::util::command::CommandContext& context)
{
    if (!context.checkEol())
    {
        return;
    }
    ::util::format::SharedStringWriter writer(context);
    writeInfo(writer, buildFields(), runtimeFields());
    if (_romCheckLookup == nullptr)
    {
        (void)writer.write("rom check      none in this image\n");
        return;
    }
    RomCheckStatus status{};
    _romCheckLookup(status);
    // NOLINTNEXTLINE(cppcoreguidelines-pro-type-vararg): SharedStringWriter::printf is printf-style
    (void)writer.printf(
        "rom check      %s (CRC unit self-test and CRC in every pass), passes %u, discarded %u, postponed %u, "
        "period %u ms\n",
        (status.result != nullptr) ? status.result : "?",
        static_cast<unsigned>(status.passes),
        static_cast<unsigned>(status.discarded),
        static_cast<unsigned>(status.postponements),
        static_cast<unsigned>(status.periodMs));
}

} // namespace identity
