// Host test of a device value of a module that only its manifest describes (RTE-18): the synthetic temperature
// probe tempProbe (test/device_values/modules/tempProbe/module.yaml rte) on a copy of the build test project with two
// instances. The generated RteConfig.cpp declares one reader per instance the RTE reads, calls it once per cycle of
// each task that reads the instance, and copies the connected members into the buffers (a boolean port gets 0/1).
// ECU: test/device_values/rte.yaml - PROBE_A alarm and qlf in app10 (task 0), PROBE_B resistance in app100 (task 1).
// Build and run: tools/rtegen/test_rtegen_device_values.py (DeviceValueHostTest).

#include "HostStubs.h"
#include "RteConfig.h"
#include "Rte_Internal.h"
#include "tempProbe/TempProbeRte.h"

#include <cstdio>

bool bios::DigitalInput::value         = false;
uint32_t bios::AnalogInputScale::value = 0U;
uint8_t bios::Output::level[2]         = {0U, 0U};
int async::LockType::depth             = 0;

namespace
{
unsigned checks   = 0U;
unsigned failures = 0U;

::tempprobe::Reading probeA{};
::tempprobe::Reading probeB{};
unsigned readsA = 0U;
unsigned readsB = 0U;

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

// The integration of the module: one reader per instance (manifest rte.read = readTempProbe)
namespace integration
{
namespace rte
{
void readTempProbe_PROBE_A(::tempprobe::Reading& reading)
{
    ++readsA;
    reading = probeA;
}

void readTempProbe_PROBE_B(::tempprobe::Reading& reading)
{
    ++readsB;
    reading = probeB;
}
} // namespace rte
} // namespace integration

int main()
{
    ::rte::init();
    CHECK(readsA == 0U && readsB == 0U); // the RTE starts nothing of the module

    // task app10 reads PROBE_A only: alarm (boolean port) and quality
    probeA = {215, 1080U, true, 1U};
    probeB = {-40, 107000U, false, 8U};
    ::rte::readInputs(0U);
    CHECK(readsA == 1U);
    CHECK(readsB == 0U);
    CHECK(Rte_Buf_DemoSwc_Button1_Button1 == 1U);
    CHECK(Rte_Buf_DemoSwc_PcCommandQlf_PcCommandQlf == 1U); // QLF_OK
    CHECK(Rte_Buf_DemoSwc_MotorNtc_mV_MotorNtc_mV == 0U);   // app100 has not run

    // task app100 reads PROBE_B only: resistance
    ::rte::readInputs(1U);
    CHECK(readsA == 1U);
    CHECK(readsB == 1U);
    CHECK(Rte_Buf_DemoSwc_MotorNtc_mV_MotorNtc_mV == 107000U);

    // the next cycle takes the new reading, the quality of the driver is copied as it is
    probeA = {-120, 0U, false, 8U}; // QLF_ERROR_TIMEOUT
    ::rte::readInputs(0U);
    CHECK(readsA == 2U);
    CHECK(Rte_Buf_DemoSwc_Button1_Button1 == 0U);
    CHECK(Rte_Buf_DemoSwc_PcCommandQlf_PcCommandQlf == 8U);

    ::rte::writeOutputs(0U); // outputs never call a reader
    ::rte::writeOutputs(1U);
    CHECK(readsA == 2U && readsB == 1U);

    std::printf("rte_device_values_host_test: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
