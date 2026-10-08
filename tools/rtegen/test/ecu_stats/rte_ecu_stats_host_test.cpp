// Host test of the operating time and reset statistics as diagnostic values (NVM-05, bsw.stats.*, bsw.stats_mcu.*,
// bsw.stats_supply.*): ECU description test/diag/rte.yaml with a description of test_rtegen_ecu_stats.py (identifier
// 0x0130 EcuStatistics as in tools/diaggen/standard_diag.yaml, plus a narrow copy), project buildtest_s32k148_min.
// Calls the generated rte::diag functions the way the jobs of ecu/uds do and checks:
//   - every element comes from its own value of its reader (bswstatus::rte, ecu/application RteSystem.cpp) - each
//     reader value distinct, so a swapped argument or element shows up,
//   - one reading per reader and function, outside the RTE lock, clamped to a narrower element type,
//   - the values never make a read fail (no NRC 0x22).
// Build and run: tools/rtegen/test_rtegen_ecu_stats.py (RteEcuStatsHostTest).

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
int deepestLock   = 0;

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

uint32_t hostOperating  = 0U;
unsigned statsReads     = 0U;
unsigned mcuReads       = 0U;
unsigned supplyReads    = 0U;
} // namespace

// the integration side (ecu/application RteSystem.cpp), as the target links it; every value distinct
namespace bswstatus
{
namespace rte
{
void readEcuStatistics(uint32_t& operatingS, uint32_t& starts, uint8_t& persistent, uint32_t& lastMcu, uint32_t& lastSupply)
{
    seen();
    ++statsReads;
    operatingS = hostOperating;
    starts     = 1001U;
    persistent = 1U;
    lastMcu    = 0x00000004U;
    lastSupply = 0x00000101U;
}

// clang-format off
void readMcuResetCounts(uint16_t& power, uint16_t& reset, uint16_t& watchdog, uint16_t& supply, uint16_t& timer, uint16_t& can, uint16_t& local, uint16_t& unknown)
{
    seen();
    ++mcuReads;
    power = 11U; reset = 12U; watchdog = 13U; supply = 14U; timer = 15U; can = 16U; local = 17U; unknown = 18U;
}

void readSupplyResetCounts(uint16_t& power, uint16_t& reset, uint16_t& watchdog, uint16_t& supply, uint16_t& timer, uint16_t& can, uint16_t& local, uint16_t& unknown)
{
    seen();
    ++supplyReads;
    power = 21U; reset = 22U; watchdog = 23U; supply = 24U; timer = 25U; can = 26U; local = 27U; unknown = 28U;
}
// clang-format on
} // namespace rte
} // namespace bswstatus

int main()
{
    // ---- 0x0130 as in the standard description
    uint32_t operating = 0U, starts = 0U, lastMcu = 0U, lastSupply = 0U;
    uint8_t persistent = 0U;
    uint16_t m[5]      = {};
    uint16_t s[8]      = {};
    hostOperating      = 360000U;
    int const before   = async::LockType::count;
    CHECK(rte::diag::readDid_EcuStatistics(
        operating, starts, persistent, lastMcu, lastSupply, m[0], m[1], m[2], m[3], m[4], s[0], s[1], s[2], s[3], s[4],
        s[5], s[6], s[7]));
    CHECK((statsReads == 1U) && (mcuReads == 1U) && (supplyReads == 1U));
    CHECK(async::LockType::count == before); // no port: no lock
    CHECK(operating == 360000U);
    CHECK(starts == 1001U);
    CHECK(persistent == 1U);
    CHECK(lastMcu == 0x00000004U);
    CHECK(lastSupply == 0x00000101U);
    // MCU: power, reset, watchdog, supply, unknown
    CHECK((m[0] == 11U) && (m[1] == 12U) && (m[2] == 13U) && (m[3] == 14U) && (m[4] == 18U));
    for (unsigned i = 0U; i < 8U; ++i)
    {
        CHECK(s[i] == (21U + i));
    }

    // ---- a narrow copy: operating time clamped to 16 bit, one cause only
    uint16_t operating16 = 0U;
    uint8_t watchdog8    = 0U;
    CHECK(rte::diag::readDid_EcuStatisticsNarrow(operating16, watchdog8));
    CHECK(operating16 == 0xFFFFU); // clamped, not cut
    CHECK(watchdog8 == 13U);
    hostOperating = 1234U;
    CHECK(rte::diag::readDid_EcuStatisticsNarrow(operating16, watchdog8));
    CHECK(operating16 == 1234U);
    CHECK(supplyReads == 1U); // the narrow identifier does not read the supply counts

    CHECK(deepestLock == 0);
    CHECK(async::LockType::depth == 0);
    std::printf("rte ecu stats host test: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
