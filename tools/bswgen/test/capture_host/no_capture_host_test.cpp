// Host test of the generat of a project *without* input_capture (IO-03, lesson of ARCH-17): the generated
// bsp/capture/inputCaptureConfiguration.h stays includable, reports 0 channels and costs nothing - no FTM
// header, no register value, no driver state. The driver of the port is compiled the same way the port does
// it, so a board without such an input links (the counterpart of input_capture_host_test.cpp).
// Build and run: tools/bswgen/test_bswgen.py (BswGenCaptureHostTest).

#include "bsp/capture/InputCapture.h"

#include <cstdio>

#define FIELD(name) char name;

namespace
{
// NX_CAPTURE_CHANNELS must be empty: it adds no member to a structure
struct Probe
{
    char first;
    NX_CAPTURE_CHANNELS(FIELD)
};

static_assert(NX_CAPTURE_CHANNEL_COUNT == 0, "the project uses no input_capture channel");
static_assert(sizeof(Probe) == sizeof(char), "NX_CAPTURE_CHANNELS must be an empty list");
} // namespace

int main()
{
    unsigned failures = 0U;
    bios::InputCapture::init();   // no channel: nothing is configured, nothing is started
    bios::InputCapture::update();
    bios::InputCapture::Measurement const m = bios::InputCapture::get(0U);
    if ((m.quality != bios::InputCapture::QUALITY_DEBOUNCE) || (m.frequency_mhz != 0U))
    {
        ++failures;
        std::printf("FAIL: a channel that does not exist must report no measurement\n");
    }
    if (bios::InputCapture::getName(0U)[0] != '\0')
    {
        ++failures;
        std::printf("FAIL: a channel that does not exist has no name\n");
    }
    if (bios::InputCapture::ChannelCountAll != 0U)
    {
        ++failures;
        std::printf("FAIL: ChannelCountAll must be 0\n");
    }
    std::printf("no_capture_host_test: NX_CAPTURE_CHANNEL_COUNT %d, %u failures\n",
                NX_CAPTURE_CHANNEL_COUNT, failures);
    return (failures == 0U) ? 0 : 1;
}
