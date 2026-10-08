// Host stub of io/Io.h: generated pin enum, setDefaultConfiguration records the FTM state at the call.
#pragma once

#include "bsp/Bsp.h"

#include <cstdint>

namespace bios
{
class Io
{
public:
#include "bsp/io/io/ioConfiguration.h"

    static bsp::BspReturnCode setDefaultConfiguration(uint16_t io);
};
} // namespace bios
