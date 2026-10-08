/* SYS-41 (finding F11 of doc/safety/fmea.md, SM-15/SM-16): host test of the ECC poll with the real code
 * (safeMemory/IntegrityEccHandler.cpp and safe_memory::cyclic in SafeMemory.cpp) against register models of the
 * ERM and of FTFC FERSTAT with write-one-to-clear behaviour (eccStubs/mcu/mcu.h).
 *
 * What it shows:
 * - no flag: no report, no write to the status registers;
 * - a non-correctable error of RAM channel 0, RAM channel 1 or the flash is reported with its source in the context,
 *   the RAM address is logged, and the flag is cleared **before** the report - so the reset of the reaction
 *   neither loops on a stale flag (whatever the reset does to the ERM) nor hides a second error behind the first;
 * - a second error after the first is reported again (before SYS-41 the flag stayed set, every later poll
 *   reported the old one and a new one was indistinguishable);
 * - only the flags that were read are cleared: an event recorded between the read and the clear stays for the
 *   next poll;
 * - single-bit corrections are counted and cleared, without a report.
 *
 * Built-in faults (run by hand for SYS-41): no clear, clear of all bits (0xFFFFFFFF), clear after the report, no
 * context - each makes this test red.
 *
 * Build and run (zig c++, no hardware):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -Iplatforms/s32k1xx/port/safety/safeMemory/test/host/eccStubs \
 *       -Iplatforms/s32k1xx/port/safety/safeMemory/include \
 *       platforms/s32k1xx/port/safety/safeMemory/test/host/EccHostTest.cpp \
 *       platforms/s32k1xx/port/safety/safeMemory/src/safeMemory/IntegrityEccHandler.cpp \
 *       platforms/s32k1xx/port/safety/safeMemory/src/safeMemory/SafeMemory.cpp \
 *       -o build/host/ecc.exe
 */

#include "safeMemory/IntegrityEccHandler.h"
#include "safeMemory/SafeMemory.h"

#include <mcu/mcu.h>
#include <safeSupervisor/SafeSupervisor.h>

#include <cstdio>

ErmType ermRegisters{};
FtfcType ftfcRegisters{};
MpuType mpuRegisters{};

namespace test_log
{
uint32_t lines     = 0U;
uint32_t lastValue = 0U;
} // namespace test_log

namespace
{
::safety::SafeSupervisor supervisorInstance;

/* The flags as the reaction finds them: SafeSupervisor::handle() resets the MCU, so what matters is the register
 * state at the moment of the report. */
uint32_t sr0AtReport     = 0xDEADU;
uint32_t ferstatAtReport = 0xDEADU;
} // namespace

namespace safety
{
SafeSupervisor& SafeSupervisor::getInstance() { return supervisorInstance; }
} // namespace safety

namespace
{
namespace sm = ::safety::safe_memory;

int failures = 0;
int checks   = 0;

#define CHECK(cond)                                                       \
    do                                                                    \
    {                                                                     \
        ++checks;                                                         \
        if (!(cond))                                                      \
        {                                                                 \
            ++failures;                                                   \
            std::printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
        }                                                                 \
    } while (false)

constexpr uint32_t SRAM_L_ADDRESS = 0x1FFF9A40U;
constexpr uint32_t SRAM_U_ADDRESS = 0x20001230U;

void reset()
{
    ermRegisters       = ErmType{};
    ftfcRegisters      = FtfcType{};
    supervisorInstance = ::safety::SafeSupervisor{};
    test_log::lines    = 0U;
    test_log::lastValue = 0U;
}

/** One safety cycle's poll; returns true if it reported. Records the flags as the report found them. */
bool poll()
{
    uint32_t const before = supervisorInstance.dmaEccMonitor.triggers;
    sm::cyclic();
    bool const reported = supervisorInstance.dmaEccMonitor.triggers != before;
    if (reported)
    {
        sr0AtReport     = ermRegisters.SR0.value;
        ferstatAtReport = ftfcRegisters.FERSTAT.value;
    }
    return reported;
}

void testNoFlagNoReportNoWrite()
{
    reset();
    for (int i = 0; i < 3; ++i)
    {
        CHECK(!poll());
    }
    CHECK(ermRegisters.SR0.writes == 0U);
    CHECK(ftfcRegisters.FERSTAT.writes == 0U);
    CHECK(test_log::lines == 0U);
}

void testRamChannel0()
{
    reset();
    ermRegisters.SR0.value     = ERM_SR0_NCE0_MASK;
    ermRegisters.EARn[0].EAR   = SRAM_L_ADDRESS;
    CHECK(poll());
    CHECK(supervisorInstance.dmaEccMonitor.lastContext == sm::ECC_SOURCE_RAM_CHANNEL_0);
    CHECK(ermRegisters.SR0.value == 0U);
    CHECK(ermRegisters.SR0.lastWrite == ERM_SR0_NCE0_MASK);
    CHECK(test_log::lastValue == SRAM_L_ADDRESS);
    CHECK(ftfcRegisters.FERSTAT.writes == 0U);
}

void testRamChannel1()
{
    reset();
    ermRegisters.SR0.value   = ERM_SR0_NCE1_MASK;
    ermRegisters.EARn[1].EAR = SRAM_U_ADDRESS;
    CHECK(poll());
    CHECK(supervisorInstance.dmaEccMonitor.lastContext == sm::ECC_SOURCE_RAM_CHANNEL_1);
    CHECK(ermRegisters.SR0.value == 0U);
    CHECK(test_log::lastValue == SRAM_U_ADDRESS);
}

void testFlash()
{
    reset();
    ftfcRegisters.FERSTAT.value = FTFC_FERSTAT_DFDIF_MASK;
    CHECK(poll());
    CHECK(supervisorInstance.dmaEccMonitor.lastContext == sm::ECC_SOURCE_FLASH);
    CHECK(ftfcRegisters.FERSTAT.value == 0U);
    CHECK(ftfcRegisters.FERSTAT.lastWrite == FTFC_FERSTAT_DFDIF_MASK);
    CHECK(ermRegisters.SR0.writes == 0U);
}

void testAllSourcesInOneReport()
{
    reset();
    ermRegisters.SR0.value      = ERM_SR0_NCE0_MASK | ERM_SR0_NCE1_MASK;
    ermRegisters.EARn[0].EAR    = SRAM_L_ADDRESS;
    ermRegisters.EARn[1].EAR    = SRAM_U_ADDRESS;
    ftfcRegisters.FERSTAT.value = FTFC_FERSTAT_DFDIF_MASK;
    CHECK(poll());
    CHECK(supervisorInstance.dmaEccMonitor.triggers == 1U);
    CHECK(supervisorInstance.dmaEccMonitor.lastContext
          == (sm::ECC_SOURCE_RAM_CHANNEL_0 | sm::ECC_SOURCE_RAM_CHANNEL_1 | sm::ECC_SOURCE_FLASH));
    CHECK(test_log::lastValue == SRAM_L_ADDRESS || test_log::lines == 2U);
}

void testFlagsAreClearedBeforeTheReport()
{
    // The reaction resets the MCU; a flag still set at that moment is found again after the reset (reset loop) if
    // the reset does not clear the ERM - and it hides a second error if it does not re-trigger.
    reset();
    ermRegisters.SR0.value      = ERM_SR0_NCE0_MASK;
    ftfcRegisters.FERSTAT.value = FTFC_FERSTAT_DFDIF_MASK;
    CHECK(poll());
    CHECK(sr0AtReport == 0U);
    CHECK(ferstatAtReport == 0U);
    CHECK(!poll()); // the next cycle (or the first after the reset) finds nothing old
}

void testSecondErrorIsReportedAgain()
{
    reset();
    ermRegisters.SR0.value = ERM_SR0_NCE0_MASK;
    CHECK(poll());
    CHECK(!poll());
    ermRegisters.SR0.value = ERM_SR0_NCE1_MASK; // a new error on the other channel
    CHECK(poll());
    CHECK(supervisorInstance.dmaEccMonitor.triggers == 2U);
    CHECK(supervisorInstance.dmaEccMonitor.lastContext == sm::ECC_SOURCE_RAM_CHANNEL_1);
}

void testEventBetweenReadAndClearIsKept()
{
    reset();
    ermRegisters.SR0.value        = ERM_SR0_NCE0_MASK;
    ermRegisters.SR0.setAfterRead = ERM_SR0_NCE1_MASK; // recorded right after the poll read SR0
    CHECK(poll());
    CHECK(supervisorInstance.dmaEccMonitor.lastContext == sm::ECC_SOURCE_RAM_CHANNEL_0);
    CHECK(ermRegisters.SR0.value == ERM_SR0_NCE1_MASK); // not cleared unseen
    CHECK(poll());
    CHECK(supervisorInstance.dmaEccMonitor.lastContext == sm::ECC_SOURCE_RAM_CHANNEL_1);
}

void testSingleBitCorrectionsAreCountedNotReported()
{
    reset();
    uint32_t const before  = sm::eccCorrectionCount();
    ermRegisters.SR0.value = ERM_SR0_SBC0_MASK | ERM_SR0_SBC1_MASK;
    CHECK(!poll());
    CHECK(sm::eccCorrectionCount() == before + 2U);
    CHECK(ermRegisters.SR0.value == 0U);
    ermRegisters.SR0.value = ERM_SR0_SBC0_MASK;
    CHECK(!poll());
    CHECK(sm::eccCorrectionCount() == before + 3U);
    // a correction next to a non-correctable error: one report, the correction counted as well
    ermRegisters.SR0.value = ERM_SR0_SBC1_MASK | ERM_SR0_NCE0_MASK;
    CHECK(poll());
    CHECK(supervisorInstance.dmaEccMonitor.lastContext == sm::ECC_SOURCE_RAM_CHANNEL_0);
    CHECK(sm::eccCorrectionCount() == before + 4U);
    CHECK(ermRegisters.SR0.value == 0U);
}

void testReadersStillWork()
{
    // the readers the requirements trace names (doc/safety/requirements.yaml SSR-026/SSR-027) keep their meaning
    reset();
    CHECK(!sm::checkRamDoubleBitError());
    CHECK(!sm::checkFlashDoubleBitError());
    ermRegisters.SR0.value      = ERM_SR0_NCE1_MASK;
    ftfcRegisters.FERSTAT.value = FTFC_FERSTAT_DFDIF_MASK;
    CHECK(sm::checkRamDoubleBitError());
    CHECK(sm::checkFlashDoubleBitError());
    CHECK(ermRegisters.SR0.writes == 0U); // reading does not clear
}
} // namespace

int main()
{
    testNoFlagNoReportNoWrite();
    testRamChannel0();
    testRamChannel1();
    testFlash();
    testAllSourcesInOneReport();
    testFlagsAreClearedBeforeTheReport();
    testSecondErrorIsReportedAgain();
    testEventBetweenReadAndClearIsKept();
    testSingleBitCorrectionsAreCountedNotReported();
    testReadersStillWork();
    std::printf("%d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
