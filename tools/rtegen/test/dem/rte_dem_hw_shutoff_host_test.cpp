// SIL test of SYS-136: the hardware shut-off path of a PWM group (SYS-130) at the RTE and in the fault memory, through
// the generated RTE of the test/dem profile, extended by tools/rtegen/test_rtegen_safety_hw_shutoff.py with
//   AppSwc.Speed      (uint32,  task app)    <- safety.hw_shutoff       the context word
//   MotorSwc.Enable   (boolean, task motor)  <- safety.hw_shutoff       anything reported
//   MotorSwc.Setpoint (uint16,  task motor)  <- safety.qlf.hw_shutoff   QLF_ERROR if the record is implausible
//   dtc 4 HwShutoff   safety.hw_shutoff, reported by the integration (dem/SafetyFaultReport.h, reportHwShutoff)
// The harness plays the safe supervisor's reader safety::hwShutoff::read (HwShutoff.h: the reported bits, or 0xFFFFFFFF
// and false for an implausible record) and the ECU state. Fault injection: a report of the family port, a second
// report with more bits, an implausible record, a reset (the record reads 0 again).
// Build and run: tools/rtegen/test_rtegen_safety_hw_shutoff.py.

#include "HostStubs.h"
#include "RteConfig.h"
#include "Rte_Internal.h"
#include "dem/DemRte.h"
#include "dem/SafetyFaultReport.h"
#include "dem/demBswReporters.h"

#include <cstdio>

bool bios::DigitalInput::value         = false;
uint32_t bios::AnalogInputScale::value = 0U;
uint8_t bios::Output::level[2]         = {0U, 0U};
int async::LockType::depth             = 0;

extern "C" void Test_Trace(uint8 const /* id */) {}

extern "C" void Test_Hook(uint8 const /* where */) {}

namespace
{
uint8_t ecuState = 1U; // RUN

// the supervisor's record (HwShutoff.cpp): reported bits and whether its two copies agree
uint32_t reported   = 0U;
bool recordPlausible = true;
unsigned reads       = 0U;
} // namespace

namespace ecustate
{
namespace rte
{
void readState(uint8_t& state) { state = ecuState; }
} // namespace rte
} // namespace ecustate

namespace safety
{
namespace hwShutoff
{
// the reader of safeSupervisor/HwShutoff.h, played: an implausible record reads ALL and false
bool read(uint32_t& context)
{
    ++reads;
    context = recordPlausible ? reported : 0xFFFFFFFFU;
    return recordPlausible;
}
} // namespace hwShutoff
} // namespace safety

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

namespace st = ::dem::status;
using ::dem::Result;

constexpr size_t TASK_MOTOR  = 0U;
constexpr size_t TASK_APP    = 1U;
constexpr uint16_t HW_DTC    = 4U; // QLF_OK/QLF_ERROR: the generated Rte_Qlf.h

#ifndef NX_DEM_SAFETY_HW_SHUTOFF
#error "the profile has a safety.hw_shutoff DTC: NX_DEM_SAFETY_HW_SHUTOFF must be generated"
#endif
#ifdef NX_DEM_SAFETY_DEVICE_FAULT
#error "the profile has no safety.device_fault DTC: NX_DEM_SAFETY_DEVICE_FAULT must not be generated"
#endif

void readAll()
{
    ::rte::readInputs(TASK_MOTOR);
    ::rte::readInputs(TASK_APP);
}

uint32_t word() { return Rte_Buf_AppSwc_Speed_Speed; }

bool anything() { return Rte_Buf_MotorSwc_Enable_Enable != 0U; }

uint16_t qualifier() { return Rte_Cpy_motor_MotorSwc_Setpoint_Setpoint; }

void testSignals()
{
    // nothing reported: 0, not set, qualifier OK; one reading per task
    reported        = 0U;
    recordPlausible = true;
    reads           = 0U;
    readAll();
    CHECK(reads == 2U);
    CHECK(word() == 0U);
    CHECK(!anything());
    CHECK(qualifier() == QLF_OK);

    // the family port reports timer 2, fault latched, finding "stuck" in bit 9: the whole word, all 32 bits
    reported = (2UL << 24U) | (1UL << 9U) | 1U;
    readAll();
    CHECK(word() == 0x02000201U);
    CHECK(anything());
    CHECK(qualifier() == QLF_OK);

    // only findings, no latched fault bit (switched off, reconfigured): anything reported still reads 1
    reported = 1UL << 8U;
    readAll();
    CHECK(word() == 0x00000100U);
    CHECK(anything());

    // a timer index only in the top byte: not lost in a narrower copy
    reported = 0xFF000000U;
    readAll();
    CHECK(word() == 0xFF000000U);
    CHECK(anything());

    // implausible record: every bit reported, qualifier ERROR (fail-safe)
    recordPlausible = false;
    readAll();
    CHECK(word() == 0xFFFFFFFFU);
    CHECK(anything());
    CHECK(qualifier() == QLF_ERROR);

    // after a reset the record is zero again (the path latches until reset)
    recordPlausible = true;
    reported        = 0U;
    readAll();
    CHECK(word() == 0U);
    CHECK(!anything());
    CHECK(qualifier() == QLF_OK);
}

::dem::EventReporter safetyHwShutoff(::safety::faultmemory::HW_SHUTOFF_EVENT);

void cycle(::dem::DemRte& dem)
{
    ::safety::faultmemory::reportHwShutoff(
        safetyHwShutoff, [](uint32_t& context) { return ::safety::hwShutoff::read(context); }, ::dem::registry.memory);
    dem.cycle();
}

void cycles(::dem::DemRte& dem, unsigned const n)
{
    for (unsigned i = 0U; i < n; ++i)
    {
        cycle(dem);
    }
}

uint8_t status(::dem::DemRte& dem, uint16_t const dtc)
{
    ::dem::DtcInfo info{};
    (void)dem.manager().dtcInfo(dtc, info);
    return info.status;
}

void testResult()
{
    CHECK(::safety::faultmemory::hwShutoffResult(true, 0U) == Result::PASSED);
    CHECK(::safety::faultmemory::hwShutoffResult(true, 1U) == Result::FAILED);
    CHECK(::safety::faultmemory::hwShutoffResult(true, 0x100U) == Result::FAILED);
    CHECK(::safety::faultmemory::hwShutoffResult(false, 0U) == Result::FAILED);
    CHECK(::safety::faultmemory::hwShutoffResult(false, 0xFFFFFFFFU) == Result::FAILED);
    // reported, not sampled: the RTE has no sample of it
    CHECK(static_cast<Result>(::rte::demSample(HW_DTC)) == Result::NONE);
}

void testFaultMemory()
{
    ::dem::DemRte dem;
    (void)dem.start();
    CHECK(::dem::registry.memory == &dem.manager());
    reported        = 0U;
    recordPlausible = true;
    cycles(dem, 5U);
    CHECK(safetyHwShutoff.dtc() == HW_DTC); // found by its BSW event name at the first report
    CHECK((status(dem, HW_DTC) & (st::TEST_FAILED | st::TEST_NOT_COMPLETED_THIS_OPERATION_CYCLE)) == 0U);

    // the family port reports the path: failed and confirmed at once (counter 1)
    reported = 0x01000001U;
    cycle(dem);
    CHECK((status(dem, HW_DTC) & st::TEST_FAILED) != 0U);
    CHECK((status(dem, HW_DTC) & st::CONFIRMED_DTC) != 0U);
    // a second report with more bits: stays failed
    reported = 0x01000301U;
    cycles(dem, 3U);
    CHECK((status(dem, HW_DTC) & st::TEST_FAILED) != 0U);

    // reset: the record is empty, the test passes, confirmed stays
    reported = 0U;
    cycle(dem);
    CHECK((status(dem, HW_DTC) & st::TEST_FAILED) == 0U);
    CHECK((status(dem, HW_DTC) & st::CONFIRMED_DTC) != 0U);

    // an implausible record reads every bit reported (fail-safe): failed as well
    recordPlausible = false;
    cycle(dem);
    CHECK((status(dem, HW_DTC) & st::TEST_FAILED) != 0U);
    recordPlausible = true;
    cycle(dem);
    CHECK((status(dem, HW_DTC) & st::TEST_FAILED) == 0U);

    dem.stop();
    ::dem::registry.memory = nullptr;
}
} // namespace

int main()
{
    testSignals();
    testResult();
    testFaultMemory();
    std::printf("%u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
