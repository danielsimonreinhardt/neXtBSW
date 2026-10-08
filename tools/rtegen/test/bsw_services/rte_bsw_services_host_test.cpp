// Host test of the BSW service ports (RTE-05): the generated RteConfig.cpp for test/bsw_services/rte.yaml (project
// buildtest_s32k148_min without SBC, BSW task profile test/multitask/tasks.yaml) against the real hand-overs
// ecu/rte/services/src/bswService/BswServiceRte.cpp and libs/bsw/ecuState/src/ecuState/EcuStateRte.cpp, with a
// system timer the test sets. The component ServiceSwc reads every bsw.* value in task app (10 ms) and the time and
// the reset reason once more in task motor (1 ms).
// Checked: the hand-over itself (reason from start reasons, health, packing of the power word), the values and
// qualifiers the component sees before any provider, with a provider, after a provider failure, the reset reasons
// from the start reasons of the role power_supply, one time reading per task cycle (ms and us consistent beyond
// 32 bits, the same value for the whole cycle, own reading in the second task).
// Build and run: tools/rtegen/test_rtegen.py (RteBswServiceHostTest).

#include "HostStubs.h"
#include "RteConfig.h"
#include "Rte_Internal.h"
#include "bswService/BswServiceRte.h"
#include "ecuState/EcuStateRte.h"

#include <bsp/timer/SystemTimer.h>

#include <cstdio>
#include <cstring>

extern "C"
{
#include "ServiceSwc.h"
}

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

uint64_t nowUs    = 0U;
unsigned timeReads = 0U;
size_t motor      = 0xFFU;
size_t app        = 0xFFU;

size_t taskIndex(char const* const name)
{
    for (size_t i = 0U; i < ::rte::TASK_COUNT; ++i)
    {
        if (std::strcmp(::rte::TASKS[i].name, name) == 0)
        {
            return i;
        }
    }
    return 0xFFU;
}

void cycleApp()
{
    ::rte::readInputs(app);
    ServiceSwc_Run10ms();
    ::rte::writeOutputs(app);
}

void cycleMotor()
{
    ::rte::readInputs(motor);
    ServiceSwc_Run1ms();
    ::rte::writeOutputs(motor);
}

void handOver()
{
    using ::bswservice::Health;
    using ::bswservice::reset::Reason;
    namespace wakeup = ::ecustate::wakeup;
    namespace reset  = ::bswservice::reset;

    // most specific reason first
    CHECK(reset::reasonOf(wakeup::NONE) == Reason::UNKNOWN);
    CHECK(reset::reasonOf(wakeup::UNKNOWN) == Reason::UNKNOWN);
    CHECK(reset::reasonOf(wakeup::POWER) == Reason::POWER_ON);
    CHECK(reset::reasonOf(wakeup::RESET) == Reason::RESET);
    CHECK(reset::reasonOf(wakeup::WATCHDOG) == Reason::WATCHDOG);
    CHECK(reset::reasonOf(wakeup::SUPPLY) == Reason::SUPPLY);
    CHECK(reset::reasonOf(wakeup::TIMER) == Reason::WAKEUP);
    CHECK(reset::reasonOf(wakeup::canBus(3U)) == Reason::WAKEUP);
    CHECK(reset::reasonOf(wakeup::local(7U)) == Reason::WAKEUP);
    CHECK(reset::reasonOf(wakeup::POWER | wakeup::canBus(0U)) == Reason::WAKEUP); // sleep with supply off
    CHECK(reset::reasonOf(wakeup::POWER | wakeup::RESET) == Reason::POWER_ON);
    CHECK(reset::reasonOf(wakeup::WATCHDOG | wakeup::POWER | wakeup::canBus(0U)) == Reason::WATCHDOG);
    CHECK(reset::reasonOf(wakeup::SUPPLY | wakeup::TIMER) == Reason::SUPPLY);
    CHECK(reset::reasonOf(wakeup::UNKNOWN | wakeup::RESET) == Reason::RESET);
    CHECK(reset::healthOf(wakeup::NONE) == Health::PENDING);
    CHECK(reset::healthOf(wakeup::UNKNOWN) == Health::FAILED);
    CHECK(reset::healthOf(wakeup::UNKNOWN | wakeup::POWER) == Health::OK);
    CHECK(reset::healthOf(wakeup::RESET) == Health::OK);

    // power word: state, health and voltage belong together, a failure keeps state and voltage
    ::bswservice::rte::reset();
    uint8_t state    = 0xFFU;
    uint16_t mv      = 0xFFFFU;
    uint8_t health   = 0xFFU;
    ::bswservice::rte::readPower(state, mv, health);
    CHECK((state == 0U) && (mv == 0U) && (health == 0U));
    ::bswservice::power::publish(::bswservice::power::State::OVERVOLTAGE, 0xFEDCU);
    ::bswservice::rte::readPower(state, mv, health);
    CHECK((state == 3U) && (mv == 0xFEDCU) && (health == 1U));
    ::bswservice::power::publishFailure();
    ::bswservice::rte::readPower(state, mv, health);
    CHECK((state == 3U) && (mv == 0xFEDCU) && (health == 2U));
    ::bswservice::rte::reset();
    ::bswservice::rte::readPower(state, mv, health);
    CHECK((state == 0U) && (mv == 0U) && (health == 0U));

    // one timer reading gives both values, ms beyond 32 bits of us
    nowUs      = 0x123456789ULL;
    uint32_t ms = 0U;
    uint32_t us = 0U;
    unsigned const before = timeReads;
    ::bswservice::rte::readTime(ms, us);
    CHECK(timeReads == before + 1U);
    CHECK(us == 0x23456789U);
    CHECK(ms == static_cast<uint32_t>(0x123456789ULL / 1000U));
}
} // namespace

extern "C" uint64_t getSystemTimeUs64Bit(void)
{
    ++timeReads;
    return nowUs;
}

extern "C" void Test_Hook(uint8 const where)
{
    if (where == 1U)
    {
        nowUs += 777U; // time goes on inside the runnable
    }
}

int main()
{
    handOver();

    using ::ecustate::State;
    namespace wakeup = ::ecustate::wakeup;
    ::bswservice::rte::reset();
    ::ecustate::rte::reset();

    CHECK(::rte::TASK_COUNT == 2U);
    motor = taskIndex("motor");
    app   = taskIndex("app");
    CHECK((motor != 0xFFU) && (app != 0xFFU));

    // ---- before any provider: values 0, qualifiers QLF_DEBOUNCE ----
    nowUs = 5000000U + 123U;
    cycleApp();
    CHECK(ServiceSwc_PowerState == 0U);
    CHECK(ServiceSwc_PowerOk == 0U);
    CHECK(ServiceSwc_SupplyMv == 0U);
    CHECK(ServiceSwc_PowerQlf == QLF_DEBOUNCE);
    CHECK(ServiceSwc_ResetReason == 0U);
    CHECK(ServiceSwc_ResetWatchdog == 0U);
    CHECK(ServiceSwc_ResetPowerOn == 0U);
    CHECK(ServiceSwc_ResetQlf == QLF_DEBOUNCE);
    CHECK(ServiceSwc_TimeMs == 5000U);
    CHECK(ServiceSwc_TimeUs == 5000123U);
    // the port keeps the value of this cycle although the timer went on inside the runnable
    CHECK(ServiceSwc_TimeUsAgain == 5000123U);
    CHECK(nowUs == 5000900U);

    // ---- power: provider reports ----
    ::bswservice::power::publish(::bswservice::power::State::OK, 13800U);
    cycleApp();
    CHECK(ServiceSwc_PowerState == 1U);
    CHECK(ServiceSwc_PowerOk == 1U);
    CHECK(ServiceSwc_SupplyMv == 13800U);
    CHECK(ServiceSwc_PowerQlf == QLF_OK);
    ::bswservice::power::publish(::bswservice::power::State::UNDERVOLTAGE, 5900U);
    cycleApp();
    CHECK(ServiceSwc_PowerState == 2U);
    CHECK(ServiceSwc_PowerOk == 0U);
    CHECK(ServiceSwc_SupplyMv == 5900U);
    CHECK(ServiceSwc_PowerQlf == QLF_OK);
    ::bswservice::power::publish(::bswservice::power::State::OK, 12000U);
    ::bswservice::power::publishFailure(); // monitor lost: last values, QLF_ERROR, not OK
    cycleApp();
    CHECK(ServiceSwc_PowerState == 1U);
    CHECK(ServiceSwc_PowerOk == 0U);
    CHECK(ServiceSwc_SupplyMv == 12000U);
    CHECK(ServiceSwc_PowerQlf == QLF_ERROR);

    // ---- reset: start reasons of the role power_supply, published by the ECU state management ----
    ::ecustate::rte::publish(State::RUN, wakeup::WATCHDOG | wakeup::RESET);
    cycleApp();
    CHECK(ServiceSwc_ResetReason == 3U);
    CHECK(ServiceSwc_ResetWatchdog == 1U);
    CHECK(ServiceSwc_ResetPowerOn == 0U);
    CHECK(ServiceSwc_ResetQlf == QLF_OK);
    ::ecustate::rte::publish(State::RUN, wakeup::POWER | wakeup::canBus(1U)); // woken by the second bus
    cycleApp();
    CHECK(ServiceSwc_ResetReason == 5U);
    CHECK(ServiceSwc_ResetWatchdog == 0U);
    CHECK(ServiceSwc_ResetPowerOn == 1U);
    CHECK(ServiceSwc_ResetQlf == QLF_OK);
    ::ecustate::rte::publish(State::RUN, wakeup::UNKNOWN); // supply cannot tell (e.g. no reset-cause provider)
    cycleApp();
    CHECK(ServiceSwc_ResetReason == 0U);
    CHECK(ServiceSwc_ResetQlf == QLF_ERROR);

    // ---- second task: own reading of time and reset reason ----
    ::ecustate::rte::publish(State::RUN, wakeup::SUPPLY);
    nowUs = 0x100000000ULL + 42U; // us beyond 32 bits: the port wraps, the ms value does not jump
    unsigned const before = timeReads;
    cycleMotor();
    CHECK(timeReads == before + 1U);
    CHECK(ServiceSwc_FastUs == 42U);
    CHECK(ServiceSwc_FastReason == 4U);
    CHECK(ServiceSwc_TimeUs != ServiceSwc_FastUs); // task app keeps its own reading until its next cycle
    cycleApp();
    CHECK(ServiceSwc_TimeUs == 42U);
    CHECK(ServiceSwc_TimeMs == static_cast<uint32_t>((0x100000000ULL + 42U) / 1000U));
    CHECK(ServiceSwc_ResetReason == 4U);
    CHECK(timeReads == before + 2U); // one reading per task cycle, not one per port

    std::printf("rte_bsw_services_host_test: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
