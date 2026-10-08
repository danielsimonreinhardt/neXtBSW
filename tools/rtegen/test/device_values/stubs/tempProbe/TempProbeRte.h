// Reading of the synthetic module tempProbe (RTE-18 test profile, test/device_values/modules/tempProbe/module.yaml
// rte.reading): one member per value of the manifest. A real driver declares its reading the same way in the header
// its manifest names (rte.include); the integration defines ::integration::rte::readTempProbe_<Instance>().
#pragma once

#include <cstdint>

namespace tempprobe
{
struct Reading
{
    int16_t temperature;  // 0.1 degC
    uint32_t resistance;  // ohm
    bool alarm;           // above the alarm threshold
    uint8_t quality;      // QLF_* values of COM-03
};
} // namespace tempprobe
