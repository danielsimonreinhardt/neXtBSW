/* Host test stub of bsp/adc/AnalogInput.h (IO-16): a scriptable AnalogInput::get instead of the
 * S32K1 ADC. Only the two channels SafeSystem reads are declared; the channel numbers do not
 * matter, the test scripts the answer per channel. */

#pragma once

#include <bsp/Bsp.h>
#include <cstdint>
#include <safeMemory/MemoryProtection.h>

namespace bios
{

class AnalogInput
{
public:
    enum AdcChannel
    {
        Ai_BANDGAP = 14,
        Ai_ADCINTERNAL0 = 15,
        _AdcChannelMax = 16,
    };

    /// Scripted answer of one channel.
    struct Response
    {
        bsp::BspReturnCode ret = bsp::BSP_OK;
        uint32_t value         = 0U;
    };

    static Response bandgap;
    static Response internalSupply;
    static uint32_t calls;
    static uint32_t callsWithOpenGate;
    /// Value written to the out parameter when a get fails, so the test can prove SafeSystem
    /// does not use it.
    static uint32_t garbageOnError;

    static bsp::BspReturnCode get(uint16_t const channel, uint32_t& value)
    {
        ++calls;
        if (!::safety::MemoryProtection::gateClosed)
        {
            ++callsWithOpenGate;
        }
        Response const& r = (channel == Ai_BANDGAP) ? bandgap : internalSupply;
        value             = (r.ret == bsp::BSP_OK) ? r.value : garbageOnError;
        return r.ret;
    }

    static void reset()
    {
        bandgap           = Response{};
        internalSupply    = Response{};
        calls             = 0U;
        callsWithOpenGate = 0U;
        garbageOnError    = 0U;
    }
};

} // namespace bios
