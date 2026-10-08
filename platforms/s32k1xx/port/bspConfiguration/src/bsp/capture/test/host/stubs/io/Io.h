// Host stub of io/Io.h (bspIo): the generated pin enum (real inputCaptureConfiguration.h/ioConfiguration.h of the
// evalboard_s32k144_charge project, IO-48), setDefaultConfiguration only records which pin was muxed.
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
