// Host test of the capture.<Channel>.<value> target in the generated RteConfig.cpp (IO-03): the RTE starts the
// measurement in init(), updates it once per cycle, reads every channel once and copies the value a port asked
// for - also while the quality reports a held value (the component sees the held value and the quality, never a
// silent zero). ECU: rte.yaml of the demo project without CAN, MotorNtc_mV -> capture.PWM_IN.freq_mhz.
// Build and run: tools/rtegen/test_rtegen.py (RteCaptureHostTest).

#include "HostStubs.h"
#include "RteConfig.h"
#include "Rte_Internal.h"
#include "bsp/capture/InputCapture.h"

#include <cstdio>

bool bios::DigitalInput::value         = false;
uint32_t bios::AnalogInputScale::value = 0U;
uint8_t bios::Output::level[2]         = {0U, 0U};
int async::LockType::depth             = 0;
bios::InputCapture::Measurement bios::InputCapture::value = {0U, 0U, 0U, bios::InputCapture::QUALITY_DEBOUNCE};
uint16_t bios::InputCapture::lastChannel = 0xFFFFU;
unsigned bios::InputCapture::inits       = 0U;
unsigned bios::InputCapture::updates     = 0U;
unsigned bios::InputCapture::reads       = 0U;

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
} // namespace

int main()
{
    using bios::InputCapture;
    uint32_t& value = Rte_Buf_DemoSwc_MotorNtc_mV_MotorNtc_mV;

    // the RTE starts the measurement itself: it is the only user of the capture inputs
    CHECK(InputCapture::inits == 0U);
    ::rte::init();
    CHECK(InputCapture::inits == 1U);
    CHECK(InputCapture::updates == 0U);

    // one update and one read per cycle, the value of the connected quantity lands in the buffer
    InputCapture::value = {50123U, 19951U, 250U, InputCapture::QUALITY_OK};
    ::rte::readInputs();
    CHECK(InputCapture::updates == 1U);
    CHECK(InputCapture::reads == 1U);
    CHECK(InputCapture::lastChannel == static_cast<uint16_t>(bios::capture::Ci_PWM_IN));
    CHECK(value == 50123U); // milli-Hertz of 50.123 Hz

    InputCapture::value.frequency_mhz = 1000000U;
    ::rte::readInputs();
    CHECK(InputCapture::updates == 2U);
    CHECK(value == 1000000U);

    // a missing signal: the driver holds the last value and reports the quality; the RTE copies what it reads
    // (the component decides, the RTE never substitutes a zero)
    InputCapture::value.quality = InputCapture::QUALITY_TIMEOUT;
    ::rte::readInputs();
    CHECK(value == 1000000U);
    CHECK(InputCapture::updates == 3U);

    ::rte::writeOutputs(); // outputs of the same task must not touch the measurement
    CHECK(InputCapture::updates == 3U);
    CHECK(InputCapture::reads == 3U);

    std::printf("rte_capture_host_test: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
