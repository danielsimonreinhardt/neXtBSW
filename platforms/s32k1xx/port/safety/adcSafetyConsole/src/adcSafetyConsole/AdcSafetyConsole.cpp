/********************************************************************************
 * Copyright (c) 2025 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 ********************************************************************************/

#include "adcSafetyConsole/AdcSafetyConsole.h"

#include <console/SyncCommandWrapper.h>
#include <safeSystem/SafeSystem.h>
#include <util/format/SharedStringWriter.h>

#include <cstdint>

namespace
{
/* SYS-37: request of "safeadc reset" for the safety task, in ordinary RAM (see takeAdcDiagnosticsResetRequest).
 * A word with a pattern rather than a bool, so a stray write rarely reads as a request - and a false one only
 * clears statistics, never a supervision state. */
constexpr uint32_t RESET_REQUESTED = 0x5AFE0ADCU;
uint32_t volatile adcDiagnosticsResetRequest = 0U;

enum Id
{
    ID_STATS = 1,
    ID_RESET = 2,
};
} // namespace

namespace safety
{

using ::util::command::CommandContext;

DEFINE_COMMAND_GROUP_GET_INFO_BEGIN(AdcSafetyCommand, "safeadc", "ADC diagnostics of the safety supervision")
COMMAND_GROUP_COMMAND(ID_STATS, "stats", "read errors, skipped checks, reference voltage")
COMMAND_GROUP_COMMAND(ID_RESET, "reset", "clear the counters")
DEFINE_COMMAND_GROUP_GET_INFO_END;

void AdcSafetyCommand::executeCommand(CommandContext& context, uint8_t const idx)
{
    // SharedStringWriter::printf is variadic by design.
    // NOLINTBEGIN(cppcoreguidelines-pro-type-vararg)
    ::util::format::SharedStringWriter out(context);
    switch (idx)
    {
        case ID_STATS:
        {
            SafeSystem::AdcDiagnostics const d = getSafeSystem().getAdcDiagnostics();
            (void)out.printf(
                "adc read errors %u, skipped reference checks %u, skipped supply checks %u\r\n",
                d.readErrors,
                d.skippedRefChecks,
                d.skippedSupplyChecks);
            (void)out.printf(
                "consecutive reference errors %u of %u before the monitor reports\r\n",
                d.consecutiveRefErrors,
                SafeSystem::ADC_REF_ERROR_LIMIT);
            // SYS-41 (F14): the supply channel has the same limit
            (void)out.printf(
                "consecutive supply errors %u of %u before the monitor reports\r\n",
                d.consecutiveSupplyErrors,
                SafeSystem::ADC_REF_ERROR_LIMIT);
            if (d.refVoltageValid)
            {
                (void)out.printf("reference %u mV (last valid measurement)\r\n", d.refVoltageMv);
            }
            else
            {
                (void)out.printf(
                    "reference not measured yet, assuming %u mV\r\n", d.refVoltageMv);
            }
            break;
        }
        case ID_RESET:
        {
            // SYS-37: not getSafeSystem().resetAdcDiagnostics() - this is the console task, and safeSystem is in
            // the MPU protected safety RAM (bus fault). The safety task clears the counters in its next cycle.
            adcDiagnosticsResetRequest = RESET_REQUESTED;
            (void)out.printf("safeadc counters cleared (in the next safety cycle)\r\n");
            break;
        }
        default:
        {
            break;
        }
    }
    // NOLINTEND(cppcoreguidelines-pro-type-vararg)
}

namespace
{
// The wrapper registers the group at the console registry in its constructor (a function local static of
// AsyncConsole), so static initialization is enough. Read synchronously in the console task: the counters are
// copied in one exclusive section (SafeSystem::getAdcDiagnostics), and the group has nothing to schedule.
AdcSafetyCommand adcSafetyCommand;
::console::SyncCommandWrapper adcSafetyCommandWrapper(adcSafetyCommand);
} // namespace

void registerAdcSafetyConsole() {}

bool takeAdcDiagnosticsResetRequest()
{
    if (adcDiagnosticsResetRequest != RESET_REQUESTED)
    {
        return false;
    }
    adcDiagnosticsResetRequest = 0U;
    return true;
}

} // namespace safety
