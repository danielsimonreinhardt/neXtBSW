// Host test of the RTE functions for the diagnostic jobs (COM-28): ECU description test/diag/rte.yaml with the
// description test/diag/diag.yaml, synthetic components of test/multitask. Calls the generated rte::diag
// functions the way the jobs of ecu/uds do and checks the RTE buffers behind them: values read from provided
// ports (clamped to the element type), values written to required ports, the request counter of a routine step,
// every call under the RTE lock.
// Build and run: tools/rtegen/test_rtegen.py (RteDiagHostTest).

#include "HostStubs.h"
#include "RteConfig.h"
#include "RteDiag.h"
#include "Rte_Internal.h"

#include <cstdio>

extern "C"
{
#include "AppSwc.h"
#include "MotorSwc.h"
}

bool bios::DigitalInput::value         = false;
uint32_t bios::AnalogInputScale::value = 0U;
uint8_t bios::Output::level[2]         = {0U, 0U};
int async::LockType::depth             = 0;

extern "C" void Test_Trace(uint8 const /* id */) {}

extern "C" void Test_Hook(uint8 const /* where */) {}

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
    // 22 MotorSpeed: provided port uint32 -> element uint16, clamped at the top
    uint16_t speed = 0U;
    Rte_Buf_MotorSwc_Speed_Speed = 1234U;
    rte::diag::readDid_MotorSpeed(speed);
    CHECK(speed == 1234U);
    Rte_Buf_MotorSwc_Speed_Speed = 70000U;
    rte::diag::readDid_MotorSpeed(speed);
    CHECK(speed == 65535U);
    CHECK(async::LockType::depth == 0);

    // 2E MotorSetpoint: into the required port connected to did.MotorSetpoint; 22 reads the provided source
    rte::diag::writeDid_MotorSetpoint(777U);
    CHECK(Rte_Buf_MotorSwc_Setpoint_Setpoint == 777U);
    uint16_t setpoint = 0U;
    Rte_Buf_AppSwc_Setpoint_Setpoint = 4321U;
    rte::diag::readDid_MotorSetpoint(setpoint);
    CHECK(setpoint == 4321U);

    // 31 SpinUp start: request parameter into the boolean port, the counter port counts the requests
    uint32 const before = Rte_Buf_AppSwc_Speed_Speed;
    rte::diag::routine_SpinUp_start(1U);
    CHECK(Rte_Buf_MotorSwc_Enable_Enable == 1U);
    CHECK(Rte_Buf_AppSwc_Speed_Speed == before + 1U);
    rte::diag::routine_SpinUp_start(0U);
    CHECK(Rte_Buf_MotorSwc_Enable_Enable == 0U);
    CHECK(Rte_Buf_AppSwc_Speed_Speed == before + 2U);

    // 31 SpinUp results: provided port uint32 -> parameter int32, clamped at the top
    int32_t result = 0;
    Rte_Buf_MotorSwc_Speed_Speed = 5U;
    rte::diag::routine_SpinUp_results(result);
    CHECK(result == 5);
    Rte_Buf_MotorSwc_Speed_Speed = 0x90000000U;
    rte::diag::routine_SpinUp_results(result);
    CHECK(result == 2147483647);
    CHECK(async::LockType::depth == 0);

    std::printf("rte diag host test: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
