// Host test of the values of the basic software the diagnostic jobs read through the RTE (COM-32): ECU description
// test/diag/rte.yaml with the description test/diag_bsw/diag.yaml, project buildtest_s32k148_min (ADC channel POTI
// with the sensor conversion bswgen generates, internal input BANDGAP, CAN buses CAN0/CAN1). Calls the generated
// rte::diag functions the way the jobs of ecu/uds and the fault memory (snapshots) do and checks:
//   - every value comes from its BSW hand-over (ecu.state, ecu.wakeup, adc.*, bsw.*), converted and clamped to the
//     element type,
//   - the BSW values are read outside the RTE lock (ADC conversions may wait on their own lock, IO-14), a port of
//     the same identifier under it,
//   - a failed ADC conversion makes the read return false (NRC 0x22), except for a quality alone (QLF_ERROR).
// Build and run: tools/rtegen/test_rtegen_diag.py (RteDiagBswHostTest).

#include "HostStubs.h"
#include "RteConfig.h"
#include "RteDiag.h"
#include "Rte_Internal.h"

#include <bsp/adc/analogSensorConfiguration.h>

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
int deepestLock   = 0; // RTE lock depth seen by any BSW hand-over

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

void seen()
{
    if (async::LockType::depth > deepestLock)
    {
        deepestLock = async::LockType::depth;
    }
}

uint8_t hostState      = 0U;
uint32_t hostWakeup    = 0U;
uint8_t powerState     = 0U;
uint16_t powerMv       = 0U;
uint8_t powerHealth    = 0U;
uint8_t resetReason    = 0U;
uint32_t resetSources  = 0U;
uint32_t timeMs        = 0U;
unsigned serviceReads  = 0U;
} // namespace

// the BSW side (libs/bsw/ecuState, ecu/rte/services), as the target links it
namespace ecustate
{
namespace rte
{
void readState(uint8_t& state)
{
    seen();
    state = hostState;
}

void readWakeupSources(uint32_t& sources)
{
    seen();
    sources = hostWakeup;
}
} // namespace rte
} // namespace ecustate

namespace bswservice
{
namespace rte
{
void readPower(uint8_t& state, uint16_t& mv, uint8_t& health)
{
    seen();
    ++serviceReads;
    state  = powerState;
    mv     = powerMv;
    health = powerHealth;
}

void readReset(uint8_t& reason, uint32_t& sources, uint8_t& health)
{
    seen();
    ++serviceReads;
    reason  = resetReason;
    sources = resetSources;
    health  = 1U;
}

void readTime(uint32_t& ms, uint32_t& us)
{
    seen();
    ++serviceReads;
    ms = timeMs;
    us = timeMs * 1000U;
}
} // namespace rte
} // namespace bswservice

int main()
{
    // ---- ecu.state, ecu.wakeup, ecu.wakeup.CAN1 (bit 8 + index 1 of the CAN bus)
    uint8_t state    = 0xFFU;
    uint32_t wakeup  = 0U;
    uint8_t canWake  = 0xFFU;
    hostState        = 1U; // RUN
    hostWakeup       = 0x00000001U | 0x00000200U;
    int const before = async::LockType::count;
    CHECK(rte::diag::readDid_EcuStatus(state, wakeup, canWake));
    CHECK(state == 1U);
    CHECK(wakeup == 0x00000201U);
    CHECK(canWake == 1U);
    CHECK(async::LockType::count == before); // no port: no lock at all
    hostState  = 5U;                         // RUNDOWN
    hostWakeup = 0x00000100U;                // CAN0, not CAN1
    CHECK(rte::diag::readDid_EcuStatus(state, wakeup, canWake));
    CHECK(state == 5U);
    CHECK(canWake == 0U);

    // ---- adc.mv (clamped to uint16), adc.raw, adc.phys (int16, the conversion of bswgen), adc.qlf
    uint16_t voltage = 0U;
    uint16_t raw     = 0U;
    int16_t value    = 0;
    uint8_t quality  = 0xFFU;
    bios::AnalogInputScale::value                      = 70000U;
    bios::AnalogInput::raw[bios::AnalogInput::Ai_POTI] = 2048U;
    CHECK(rte::diag::readDid_Poti(voltage, raw, value, quality));
    CHECK(voltage == 65535U);
    CHECK(raw == 2048U);
    uint8_t expectedQuality = 0U;
    int32_t const expected  = nxAdcPhys_POTI(2048U, &expectedQuality);
    CHECK(value == static_cast<int16_t>(expected));
    CHECK(expected > 0);
    CHECK(quality == QLF_OK);
    bios::AnalogInputScale::value                      = 1234U;
    bios::AnalogInput::raw[bios::AnalogInput::Ai_POTI] = 0U; // end of the converter: value held, quality range
    CHECK(rte::diag::readDid_Poti(voltage, raw, value, quality));
    CHECK(voltage == 1234U);
    CHECK(quality == QLF_ERROR_RANGE);
    // a failed conversion is no value: false (the job answers NRC 0x22, a snapshot stores 0xFF)
    bios::AnalogInput::failing = true;
    CHECK(!rte::diag::readDid_Poti(voltage, raw, value, quality));
    CHECK(quality == QLF_ERROR);
    bios::AnalogInput::failing      = false;
    bios::AnalogInputScale::failing = true;
    CHECK(!rte::diag::readDid_Poti(voltage, raw, value, quality));
    bios::AnalogInputScale::failing = false;

    // ---- adc.mv of an internal input as float32; the quality alone stays an answer when the conversion fails
    float bandgap       = 0.0F;
    uint8_t potiQuality = 0U;
    bios::AnalogInputScale::value = 1200U;
    CHECK(rte::diag::readDid_Converter(bandgap, potiQuality));
    CHECK(bandgap == 1200.0F);
    bios::AnalogInput::failing = true;
    CHECK(rte::diag::readDid_Converter(bandgap, potiQuality));
    CHECK(potiQuality == QLF_ERROR);
    bios::AnalogInput::failing      = false;
    bios::AnalogInputScale::failing = true;
    CHECK(!rte::diag::readDid_Converter(bandgap, potiQuality));
    bios::AnalogInputScale::failing = false;

    // ---- bsw.power.*, bsw.reset.*, bsw.time.ms: one reading per service
    uint8_t pState = 0U, ok = 0U, pQlf = 0U, reason = 0U, watchdog = 0U;
    uint16_t supply = 0U;
    uint32_t uptime = 0U;
    powerState      = 1U; // OK
    powerMv         = 12345U;
    powerHealth     = 1U;
    resetReason     = 3U;           // WATCHDOG
    resetSources    = 0x00000004U;  // wakeup::WATCHDOG
    timeMs          = 123456U;
    serviceReads    = 0U;
    CHECK(rte::diag::readDid_Supply(pState, ok, supply, pQlf, reason, watchdog, uptime));
    CHECK(serviceReads == 3U);
    CHECK(pState == 1U);
    CHECK(ok == 1U);
    CHECK(supply == 12345U);
    CHECK(pQlf == QLF_OK);
    CHECK(reason == 3U);
    CHECK(watchdog == 1U);
    CHECK(uptime == 123456U);
    powerHealth  = 2U; // provider cannot tell
    resetSources = 0U;
    CHECK(rte::diag::readDid_Supply(pState, ok, supply, pQlf, reason, watchdog, uptime));
    CHECK(ok == 0U);
    CHECK(pQlf == QLF_ERROR);
    CHECK(watchdog == 0U);

    // ---- a port and a BSW value in one identifier: the port under the lock, the BSW value before it
    uint16_t speed               = 0U;
    Rte_Buf_MotorSwc_Speed_Speed = 42U;
    hostState                    = 2U;
    int const locks              = async::LockType::count;
    CHECK(rte::diag::readDid_MotorStatus(speed, state));
    CHECK(speed == 42U);
    CHECK(state == 2U);
    CHECK(async::LockType::count == locks + 1);

    // the port functions of COM-28 report true
    CHECK(rte::diag::readDid_MotorSpeed(speed));
    CHECK(speed == 42U);

    CHECK(deepestLock == 0); // no BSW hand-over ran under the RTE lock
    CHECK(async::LockType::depth == 0);

    std::printf("rte diag bsw host test: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
