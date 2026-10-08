// Host test of the ECU state targets in the generated RteConfig.cpp (SYS-01) against the real hand-over
// libs/bsw/ecuState/src/ecuState/EcuStateRte.cpp. ECU: rte.yaml of the demo without CAN,
// Button1 <- ecu.wakeup.CAN0 (the bus of the demo project), MotorNtc_mV <- ecu.state, Led1 and Led2 -> ecu.sleep_request (and LED1/LED2).
// Build and run: tools/rtegen/test_rtegen.py (RteEcuStateHostTest).

#include "HostStubs.h"
#include "RteConfig.h"
#include "Rte_Internal.h"
#include "ecuState/EcuStateRte.h"

#include <cstdio>

bool bios::DigitalInput::value         = false;
uint32_t bios::AnalogInputScale::value = 0U;
uint8_t bios::Output::level[2]         = {0U, 0U};
int async::LockType::depth             = 0;

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
    using ::ecustate::State;
    namespace wakeup = ::ecustate::wakeup;
    namespace hand = ::ecustate::rte;
    uint8_t& canWake  = Rte_Buf_DemoSwc_Button1_Button1;
    uint32_t& state   = Rte_Buf_DemoSwc_MotorNtc_mV_MotorNtc_mV;
    uint8_t& request1 = Rte_Buf_DemoSwc_Led1_Led1;
    uint8_t& request2 = Rte_Buf_DemoSwc_Led2_Led2;

    // start: STARTUP, no reasons yet
    ::rte::readInputs();
    CHECK(state == static_cast<uint32_t>(State::STARTUP));
    CHECK(canWake == 0U);
    CHECK(!hand::isSleepRequestConnected());

    hand::publish(State::RUN, wakeup::canBus(0U) | wakeup::POWER);
    ::rte::readInputs();
    CHECK(state == static_cast<uint32_t>(State::RUN));
    CHECK(canWake == 1U);

    hand::publish(State::STANDBY, wakeup::canBus(1U)); // other bus: CAN0 not set
    ::rte::readInputs();
    CHECK(state == static_cast<uint32_t>(State::STANDBY));
    CHECK(canWake == 0U);

    // sleep request: AND of both components
    request1 = 1U;
    request2 = 0U;
    ::rte::writeOutputs();
    CHECK(hand::isSleepRequestConnected());
    CHECK(!hand::isSleepRequested());
    CHECK(bios::Output::level[0] == 1U); // LED1 still driven
    request2 = 1U;
    ::rte::writeOutputs();
    CHECK(hand::isSleepRequested());
    request1 = 0U;
    ::rte::writeOutputs();
    CHECK(!hand::isSleepRequested());

    std::printf("rte_ecu_state_host_test: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
