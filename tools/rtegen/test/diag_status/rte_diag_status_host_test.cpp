// Host test of the status values of the basic software the diagnostic jobs read through the RTE (COM-45): ECU
// description test/diag/rte.yaml with the description test/diag_status/diag.yaml, project buildtest_s32k148_min.
// Calls the generated rte::diag functions the way the jobs of ecu/uds and the fault memory (snapshots) do and checks:
//   - every value comes from its reader (bswstatus::rte, ecu/application RteSystem.cpp), one reading per status and
//     function, converted and clamped to the element type,
//   - the readers run outside the RTE lock, a port of the same identifier under it,
//   - a status value never makes a read fail (no NRC 0x22).
// Build and run: tools/rtegen/test_rtegen_diag_status.py (RteDiagStatusHostTest).

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
int deepestLock   = 0; // RTE lock depth seen by any reader

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

uint32_t hostViolations = 0U;
uint32_t hostLayout     = 0U;
uint32_t hostDataset    = 0U;
uint8_t hostStatus      = 0U;
unsigned rteReads       = 0U;
unsigned calReads       = 0U;
} // namespace

// the integration side (ecu/application RteSystem.cpp), as the target links it
namespace bswstatus
{
namespace rte
{
void readRteStatus(uint32_t& violations)
{
    seen();
    ++rteReads;
    violations = hostViolations;
}

void readCalibrationStatus(uint32_t& layout, uint32_t& dataset, uint8_t& status)
{
    seen();
    ++calReads;
    layout  = hostLayout;
    dataset = hostDataset;
    status  = hostStatus;
}
} // namespace rte
} // namespace bswstatus

int main()
{
    // ---- bsw.rte.deadline_violations: whole and clamped, one reading for both elements
    uint32_t violations   = 0U;
    uint16_t violations16 = 0U;
    hostViolations        = 7U;
    int const before      = async::LockType::count;
    CHECK(rte::diag::readDid_RteStatus(violations, violations16));
    CHECK(rteReads == 1U);
    CHECK(violations == 7U);
    CHECK(violations16 == 7U);
    CHECK(async::LockType::count == before); // no port: no lock at all
    hostViolations = 0x12345U;
    CHECK(rte::diag::readDid_RteStatus(violations, violations16));
    CHECK(violations == 0x12345U);
    CHECK(violations16 == 0xFFFFU); // clamped, not cut to 0x2345
    hostViolations = 0xFFFFFFFFU;
    CHECK(rte::diag::readDid_RteStatus(violations, violations16));
    CHECK(violations == 0xFFFFFFFFU);

    // ---- bsw.calibration.*: one reading for the three values
    uint32_t layout  = 0U;
    uint32_t dataset = 0U;
    uint8_t status   = 0U;
    hostLayout       = 0xA1B2C3D4U;
    hostDataset      = 0x00C0FFEEU;
    hostStatus       = 6U; // LAYOUT
    CHECK(rte::diag::readDid_CalibrationState(layout, dataset, status));
    CHECK(calReads == 1U);
    CHECK(layout == 0xA1B2C3D4U);
    CHECK(dataset == 0x00C0FFEEU);
    CHECK(status == 6U);
    hostStatus = 0xFFU; // no calibration segment
    CHECK(rte::diag::readDid_CalibrationState(layout, dataset, status));
    CHECK(status == 0xFFU);

    // ---- a port and a status value in one identifier: the port under the lock, the reader before it
    uint16_t speed               = 0U;
    Rte_Buf_MotorSwc_Speed_Speed = 42U;
    hostViolations               = 3U;
    int const locks              = async::LockType::count;
    CHECK(rte::diag::readDid_MotorAndRte(speed, violations));
    CHECK(speed == 42U);
    CHECK(violations == 3U);
    CHECK(async::LockType::count == locks + 1);

    CHECK(deepestLock == 0); // no reader ran under the RTE lock
    CHECK(async::LockType::depth == 0);

    std::printf("rte diag status host test: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
