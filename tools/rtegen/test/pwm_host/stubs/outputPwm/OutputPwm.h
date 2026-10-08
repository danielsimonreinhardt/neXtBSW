// Host stub of outputPwm/OutputPwm.h: records setDuty calls, result configurable.
#pragma once

#include "../../../can_host/stubs/HostStubs.h"

#include <cstdint>
#include <vector>

namespace bios
{
struct OutputPwm
{
    enum outputPwm
    {
        dummyPwm,
        MOT1_U,
        MOT1_V,
        MOT1_W,
    };

    struct Call
    {
        uint16_t chan;
        uint16_t duty;
    };

    static std::vector<Call> calls;
    static ::bsp::BspReturnCode result;

    static ::bsp::BspReturnCode setDuty(uint16_t chan, uint16_t duty, bool = false)
    {
        calls.push_back(Call{chan, duty});
        return result;
    }
};
} // namespace bios
