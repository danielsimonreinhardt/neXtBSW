/* SYS-06: host stub of io/Io.h (OpenBSW external/openbsw/platforms/s32k1xx/bsp/bspIo/include/io/Io.h) with what
 * SafeState.cpp uses: the pin configuration of the generated pin enum. The pin ids are the ones a generated
 * ioConfiguration.h would supply; SAFETY_SAFE_STATE_PIN is the indicator output of the project
 * (project.yaml safety.safe_state_output). Every configuration write is recorded so that the test can tell what
 * the safe state did to the indicator and in which order. */
#pragma once

#include <cstdint>
#include <string>

namespace bsp
{
enum BspReturnCode
{
    BSP_OK    = 0,
    BSP_ERROR = 1,
};
} // namespace bsp

namespace bios
{

class Io
{
public:
    Io() = delete;

    enum
    {
        PINDISABLE = 0,
        GPIO       = (1 << 8),
    };

    enum
    {
        STRENGTH_ON = (1 << 6),
    };

    // as a generated ioConfiguration.h supplies them
    enum PinId
    {
        SOME_OUTPUT = 0,
        SAFETY_SAFE_STATE_PIN,
        NUMBER_OF_INPUTS_AND_OUTPUTS,
    };

    struct PinConfiguration
    {
        uint8_t port;
        uint8_t pinNumber;
        uint8_t dir;
        uint8_t pinCfgExt;
        uint32_t pinCfg;
    };

    static PinConfiguration configuration[NUMBER_OF_INPUTS_AND_OUTPUTS];
    static int setCalls;
    static std::string* trace; // one character per configuration write, shared with the register trace

    static bsp::BspReturnCode getConfiguration(uint16_t const io, PinConfiguration& cfg)
    {
        cfg = configuration[io];
        return bsp::BSP_OK;
    }

    static bsp::BspReturnCode setConfiguration(uint16_t const io, PinConfiguration const& cfg)
    {
        configuration[io] = cfg;
        ++setCalls;
        if (trace != nullptr)
        {
            *trace += 'I';
        }
        return bsp::BSP_OK;
    }
};

} // namespace bios
