// Host stub of outputPwm/OutputPwm.h: generated channel enum, same client interface, recorded registration.
#pragma once

#include "bsp/Bsp.h"

#include <cstdint>

namespace bios
{
class OutputPwm
{
public:
#include "bsp/io/outputPwm/outputPwmConfiguration.h"

    class IDynamicPwmClient
    {
    public:
        virtual bsp::BspReturnCode setDuty(uint16_t chan, uint16_t duty, bool immediateUpdate) = 0;
        virtual bsp::BspReturnCode setPeriod(uint16_t chan, uint16_t period)                   = 0;
    };

    static bsp::BspReturnCode
    setDynamicClient(uint16_t outputNumber, uint16_t clientOutputNumber, IDynamicPwmClient* client);
    static bsp::BspReturnCode clrDynamicClient(uint16_t outputNumber);

    static uint32_t const MAX_DUTY_CYCLE = 10000;
};
} // namespace bios
