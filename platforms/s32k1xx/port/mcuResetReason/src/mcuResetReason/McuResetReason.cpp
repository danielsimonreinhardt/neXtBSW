// RTE-16: bswservice::reset::mcuStartReasons() of the s32k1xx family from RCM_SRS (see mcuResetReason/RcmResetReason.h).

#include "bswService/McuResetReason.h"
#include "mcu/mcu.h"
#include "mcuResetReason/RcmResetReason.h"
#include "mcuResetReason/StartStatusReport.h"

#include <ecuState/McuStartReasons.h>

#include <cstdio>

// The bit positions of RcmResetReason.h against the device header of the derivative
static_assert(RCM_SRS_LVD_MASK == ::rcm::SRS_LVD, "RCM_SRS.LVD");
static_assert(RCM_SRS_LOC_MASK == ::rcm::SRS_LOC, "RCM_SRS.LOC");
#ifdef RCM_SRS_LOL_MASK
static_assert(RCM_SRS_LOL_MASK == ::rcm::SRS_LOL, "RCM_SRS.LOL");
#endif
#ifdef RCM_SRS_CMU_LOC_MASK
static_assert(RCM_SRS_CMU_LOC_MASK == ::rcm::SRS_CMU_LOC, "RCM_SRS.CMU_LOC");
#endif
static_assert(RCM_SRS_WDOG_MASK == ::rcm::SRS_WDOG, "RCM_SRS.WDOG");
static_assert(RCM_SRS_PIN_MASK == ::rcm::SRS_PIN, "RCM_SRS.PIN");
static_assert(RCM_SRS_POR_MASK == ::rcm::SRS_POR, "RCM_SRS.POR");
static_assert(RCM_SRS_JTAG_MASK == ::rcm::SRS_JTAG, "RCM_SRS.JTAG");
static_assert(RCM_SRS_LOCKUP_MASK == ::rcm::SRS_LOCKUP, "RCM_SRS.LOCKUP");
static_assert(RCM_SRS_SW_MASK == ::rcm::SRS_SW, "RCM_SRS.SW");
static_assert(RCM_SRS_MDM_AP_MASK == ::rcm::SRS_MDM_AP, "RCM_SRS.MDM_AP");
static_assert(RCM_SRS_SACKERR_MASK == ::rcm::SRS_SACKERR, "RCM_SRS.SACKERR");

namespace
{
// No-init RAM (section .noinit of the application linker script, not cleared by startUp.S): survives the resets of
// the watchdog fast test. Written before it is read in every start (the first pass after a power-on is never a
// fast-test reset), so the random content and ECC state after power-on are never read.
__attribute__((section(".noinit.mcuResetReason"))) ::rcm::Record volatile startRecord;

uint32_t startReasons    = 0U; // .bss: cleared before the constructors run
uint32_t lastResetStatus = 0U; // SYS-84: RCM_SRS of this pass, for the start-up line

// SYS-18: the same value for the ECU state management, which sits below the RTE and therefore cannot call
// bswservice::reset::mcuStartReasons(). Registered as ecuState's family hook (ecuState/McuStartReasons.h).
uint32_t startReasonsOfThisStart() { return startReasons; }

// Static constructor: runs in __libc_init_array before main(), i.e. before ::integration::earlyInit() and the watchdog
// fast test, on every pass through the start-up. Linked whenever mcuStartReasons() is (the RTE's BSW services).
struct Capture
{
    Capture()
    {
        uint32_t const srs = RCM->SRS;
        auto const tst     = static_cast<uint8_t>((WDOG->CS & WDOG_CS_TST_MASK) >> WDOG_CS_TST_SHIFT);
        startReasons       = ::rcm::capture(startRecord, srs, tst);
        lastResetStatus    = srs;
        // The target of the pointer stays valid and its value is final here; ecuState reads it when the run
        // level is reached. Registering in every pass through the start-up is harmless (same function).
        ::ecustate::setMcuStartReasonSource(&startReasonsOfThisStart);
    }
};

Capture const captureAtStart;
} // namespace

namespace bswservice
{
namespace reset
{
uint32_t mcuStartReasons() { return startReasons; }
} // namespace reset
} // namespace bswservice

namespace rcm
{
void printStartLine()
{
    char line[START_LINE_SIZE];
    (void)formatStartLine(line, sizeof(line), startStatusOf(startRecord, lastResetStatus));
    std::printf("%s\r\n", line);
}
} // namespace rcm
