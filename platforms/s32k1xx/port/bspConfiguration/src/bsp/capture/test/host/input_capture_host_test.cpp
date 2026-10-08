// Host test of bsp/capture/InputCapture.h with the configuration bswgen generates for
// tools/nxconfig/test/profiles/projects/testproject_full_charge (PLAT-62; input_capture: [PILOT_DUTY], FTM0 CH6/7,
// IO-03/IO-48).
//
// IO-48 (board 2026-09-28, 9c): InputCapture::init() configured and started the FTM but never called
// Io::setDefaultConfiguration() for the channel's pin, so PORTE_PCR8 stayed 0 (GPIO reset state) and the
// measurement saw no edge although the FTM was running and ioConfiguration.h already had the correct ALT
// function for PTE8. This test fails against the pre-fix driver the same way the board did: pinCalls would stay
// empty. FTM0 is a RAM model with the real register layout (S32K148_FTM.h), OpenBSW ftm/Ftm.h is used unchanged;
// Io is a stub around the real generated pin enum.
//
// Build and run: platforms/s32k1xx/port/bspConfiguration/src/bsp/capture/test/host/test_input_capture_host.py

#include "bsp/capture/InputCapture.h"

#include "io/Io.h"

#include <cstdio>
#include <vector>

FTM_Type fakeFtm0 = {};

namespace
{
unsigned checks   = 0U;
unsigned failures = 0U;

#define CHECK(c)                                                     \
    do                                                               \
    {                                                                \
        ++checks;                                                    \
        if (!(c))                                                    \
        {                                                            \
            ++failures;                                              \
            std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); \
        }                                                            \
    } while (0)

struct PinCall
{
    uint16_t pin;
    FTM_Type ftm; // register state when the pin was muxed
};

std::vector<PinCall> pinCalls;
} // namespace

namespace bios
{
bsp::BspReturnCode Io::setDefaultConfiguration(uint16_t const io)
{
    pinCalls.push_back(PinCall{io, fakeFtm0});
    return bsp::BSP_OK;
}
} // namespace bios

using bios::InputCapture;

int main()
{
    // the charge project has exactly one channel: PILOT_DUTY on FTM0 CH6/7
    CHECK(InputCapture::ChannelCountAll == 1U);

    fakeFtm0.SC = 0x12345678U; // garbage, init() must end with the configured value
    CHECK(pinCalls.empty());
    InputCapture::init();

    // the bug (IO-48): without the fix pinCalls stays empty and PTE8 is never muxed to FTM0_CH6
    CHECK(pinCalls.size() == 1U);
    if (!pinCalls.empty())
    {
        CHECK(pinCalls[0].pin == bios::Io::PILOT_DUTY);
        // the pin is muxed after the FTM is configured and running dual edge capture on pair 6/7
        // (pairShift(6) = 24, COMBINE bit 2 of the pair = DECAPEN0, ARCH-06 timer setup, unchanged by IO-48)
        FTM_Type const& s = pinCalls[0].ftm;
        CHECK((s.MODE & FTM_MODE_FTMEN_MASK) != 0U);
        CHECK((s.COMBINE & (1UL << (24U + 2U))) != 0U); // DECAPEN0 of pair 6/7 (pairShift(6) = 24)
    }

    FTM_Type const& f = fakeFtm0;
    // arm(PHASE_PERIOD): both channels of the pair capture the rising edge, DECAP armed
    CHECK(f.CONTROLS[6].CnSC == FTM_CnSC_ELSA_MASK);
    CHECK(f.CONTROLS[7].CnSC == FTM_CnSC_ELSA_MASK);
    CHECK((f.COMBINE & (1UL << (24U + 3U))) != 0U); // DECAP0 of pair 6/7 armed

    // no measurement yet
    InputCapture::Measurement const m = InputCapture::get(0U);
    CHECK(m.quality == InputCapture::QUALITY_DEBOUNCE);

    // init() again (e.g. a restart): the pin is muxed again, not skipped
    InputCapture::init();
    CHECK(pinCalls.size() == 2U);
    if (pinCalls.size() == 2U)
    {
        CHECK(pinCalls[1].pin == bios::Io::PILOT_DUTY);
    }

    std::printf("input_capture_host_test: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
