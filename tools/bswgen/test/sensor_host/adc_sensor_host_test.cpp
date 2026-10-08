// SPDX-License-Identifier: Apache-2.0
// Host test of the generated integer sensor conversion bsp/adc/analogSensorConfiguration.h (IO-18): every raw code
// 0..full scale (and codes above it) of every used channel against the Python reference of tools/nxconfig/sensor.py
// (evaluate). The reference table sensorExpected.h is written by the test next to the generat:
//   NX_TEST_EXPECTED_<NAME>[] = {raw, value, quality}, NX_TEST_EXPECTED_<NAME>_COUNT
// Build and run: tools/bswgen/test_bswgen.py (BswGenSensorHostTest), for the eval board demo project and the
// synthetic profile testboard_sensors/testproject_sensors, and as mutants with built-in faults.

#include "bsp/adc/analogSensorConfiguration.h"
#include "sensorExpected.h"

#include <cstdio>

namespace
{
unsigned checks   = 0U;
unsigned failures = 0U;

using Conversion = int32_t (*)(uint16_t, uint8_t*);

void checkChannel(char const* const name, Conversion const convert, int64_t const (*const expected)[3],
                  unsigned const count)
{
    unsigned wrong = 0U;
    for (unsigned i = 0U; i < count; ++i)
    {
        uint16_t const raw    = static_cast<uint16_t>(expected[i][0]);
        uint8_t quality       = 0xFFU;
        int32_t const value   = convert(raw, &quality);
        ++checks;
        if ((value != expected[i][1]) || (quality != expected[i][2]))
        {
            ++failures;
            if (++wrong <= 3U)
            {
                std::printf(
                    "FAIL %s raw %u: value %ld quality %u, expected %ld quality %u\n",
                    name,
                    static_cast<unsigned>(raw),
                    static_cast<long>(value),
                    static_cast<unsigned>(quality),
                    static_cast<long>(expected[i][1]),
                    static_cast<unsigned>(expected[i][2]));
            }
        }
    }
}
} // namespace

int main()
{
    unsigned channels = 0U;
#define NX_TEST_CHANNEL(name)                                                                                     \
    ++channels;                                                                                                   \
    checkChannel(#name, &nxAdcPhys_##name, NX_TEST_EXPECTED_##name, NX_TEST_EXPECTED_##name##_COUNT);
    NX_ADC_SENSORS(NX_TEST_CHANNEL)
#undef NX_TEST_CHANNEL

    ++checks;
    if (channels != static_cast<unsigned>(NX_ADC_SENSOR_COUNT) || (channels != NX_TEST_EXPECTED_CHANNELS))
    {
        ++failures;
        std::printf("FAIL channel count %u, generated %d, reference %u\n", channels, NX_ADC_SENSOR_COUNT,
                    static_cast<unsigned>(NX_TEST_EXPECTED_CHANNELS));
    }
    std::printf("adc_sensor_host_test: %u channels, %u checks, %u failures\n", channels, checks, failures);
    return (failures == 0U) ? 0 : 1;
}
