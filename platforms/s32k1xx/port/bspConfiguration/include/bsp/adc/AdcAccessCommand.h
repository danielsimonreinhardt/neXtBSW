#pragma once

#include "util/command/GroupCommand.h"

namespace bios
{

/**
 * Console group "adcaccess": statistics of the serialized ADC access of AnalogInput (IO-14).
 *   stats   conversions, waits, nested conversions, maximum wait/conversion/exclusive time,
 *           polling timeouts per instance and the derived conversion time and polling bound
 *           (IO-16, bsp/adc/AdcConversionTiming.h)
 *   reset   clear the statistics and the timeout counters
 * Register it like the other BSP testers (BspSystem, AsyncCommandWrapper).
 */
class AdcAccessCommand : public ::util::command::GroupCommand
{
public:
    AdcAccessCommand() {}

protected:
    DECLARE_COMMAND_GROUP_GET_INFO
    void executeCommand(::util::command::CommandContext& context, uint8_t idx) override;
};

} // namespace bios
