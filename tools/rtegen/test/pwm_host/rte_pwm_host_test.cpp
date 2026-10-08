// Host test of the pwm.<Pair> target in the generated RteConfig.cpp (writeOutputs): no write of the start
// value (outputs stay masked), write on change only, saturation to 10000 = 100 %, retry while the driver
// refuses (no client registered). ECU: ecu/rte.yaml without CAN, MotorNtcFiltered_mV -> pwm.MOT1_V.
// Build and run: tools/rtegen/test_rtegen.py (RtePwmHostTest).

#include "HostStubs.h"
#include "RteConfig.h"
#include "Rte_Internal.h"
#include "outputPwm/OutputPwm.h"

#include <cstdio>

bool bios::DigitalInput::value         = false;
uint32_t bios::AnalogInputScale::value = 0U;
uint8_t bios::Output::level[2]         = {0U, 0U};
int async::LockType::depth             = 0;
std::vector<bios::OutputPwm::Call> bios::OutputPwm::calls;
::bsp::BspReturnCode bios::OutputPwm::result = ::bsp::BSP_OK;

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

bool lastCall(uint16_t chan, uint16_t duty)
{
    auto const& c = bios::OutputPwm::calls;
    return !c.empty() && (c.back().chan == chan) && (c.back().duty == duty);
}
} // namespace

int main()
{
    using bios::OutputPwm;
    uint32_t& duty = Rte_Buf_DemoSwc_MotorNtcFiltered_mV_MotorNtcFiltered_mV;

    // start value: never written, the driver keeps all outputs masked
    for (int i = 0; i < 5; ++i)
    {
        ::rte::writeOutputs();
    }
    CHECK(OutputPwm::calls.empty());

    duty = 2500U;
    ::rte::writeOutputs();
    CHECK(OutputPwm::calls.size() == 1U);
    CHECK(lastCall(OutputPwm::MOT1_V, 2500U));
    ::rte::writeOutputs();
    CHECK(OutputPwm::calls.size() == 1U); // unchanged: no write

    duty = 70000U; // saturates
    ::rte::writeOutputs();
    CHECK(lastCall(OutputPwm::MOT1_V, 10000U));
    duty = 10001U; // different buffer value, same duty: written once
    ::rte::writeOutputs();
    CHECK(lastCall(OutputPwm::MOT1_V, 10000U) && (OutputPwm::calls.size() == 3U));

    // refused (driver not registered): retried every cycle until accepted
    OutputPwm::result = ::bsp::BSP_ERROR;
    duty              = 1U;
    ::rte::writeOutputs();
    ::rte::writeOutputs();
    CHECK(OutputPwm::calls.size() == 5U);
    OutputPwm::result = ::bsp::BSP_OK;
    ::rte::writeOutputs();
    ::rte::writeOutputs();
    CHECK(OutputPwm::calls.size() == 6U);
    CHECK(lastCall(OutputPwm::MOT1_V, 1U));

    // back to the start value 0 after a change is written
    duty = 0U;
    ::rte::writeOutputs();
    CHECK(lastCall(OutputPwm::MOT1_V, 0U) && (OutputPwm::calls.size() == 7U));
    for (auto const& c : OutputPwm::calls)
    {
        CHECK(c.chan == OutputPwm::MOT1_V);
    }

    std::printf("rte_pwm_host_test: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
