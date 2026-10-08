// Host stub of the capture driver of the port (platforms/s32k1xx/port/bspConfiguration/.../InputCapture.h):
// only what the generated RteConfig.cpp touches, plus counters so that the test can see how often the RTE
// starts, updates and reads the measurement. The real driver is tested with a register model in
// tools/bswgen/test/capture_host; the target build checks the real signatures.

#pragma once

#include <cstdint>

namespace bios
{
namespace capture
{
enum ChannelId
{
    Ci_PWM_IN,
    _captureChannelMax,
};
} // namespace capture

class InputCapture
{
public:
    static constexpr uint8_t QUALITY_DEBOUNCE = 0U;
    static constexpr uint8_t QUALITY_OK       = 1U;
    static constexpr uint8_t QUALITY_TIMEOUT  = 8U;
    static constexpr uint8_t QUALITY_ERROR    = 16U;

    struct Measurement
    {
        uint32_t frequency_mhz;
        uint32_t period_us;
        uint16_t duty_permille;
        uint8_t quality;
    };

    static void init() { ++inits; }

    static void update() { ++updates; }

    static Measurement get(uint16_t const channel)
    {
        ++reads;
        lastChannel = channel;
        return value;
    }

    static Measurement value;
    static uint16_t lastChannel;
    static unsigned inits;
    static unsigned updates;
    static unsigned reads;
};

} // namespace bios
