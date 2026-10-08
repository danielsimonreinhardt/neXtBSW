/* SYS-130 (safety review SYS-86 H7): the report of a hardware shut-off path to the safe supervisor and its reader -
 * see safeSupervisor/HwShutoff.h. Own translation unit of the supervisor library: linked only into an image whose
 * family port reports (S32K1: a project with a hardware threshold), its record lies in the MPU protected .bss of this
 * library like the supervisor object. */

#include "safeSupervisor/HwShutoff.h"

// SYS-135: NX_SAFE_SUPERVISOR_DEGRADED and the degraded reaction of the supervisor (Degraded.h)
#include "safeSupervisor/SafeSupervisor.h"

#include <interrupts/SuspendResumeAllInterruptsScopedLock.h>
#include <safeUtils/SafetyLogger.h>

#ifdef PLATFORM_SUPPORT_IO
#include <safeIo/SafeState.h>
#endif

namespace safety
{
namespace hwShutoff
{
namespace
{
/* The reported bits and their copy, written in the safety context only. Zero-initialised: nothing reported. */
uint32_t reported;
uint32_t reportedCopy;
/* SYS-135: the reported bits that were answered with S1 held (not degraded) and their copy - what keeps the return
 * path of the register check closed (latched()). Zero-initialised: S1 not held by a report. */
uint32_t held;
uint32_t heldCopy;
} // namespace

bool read(uint32_t& context)
{
    uint32_t const value = reported;
    if (value != reportedCopy)
    {
        context = ALL;
        return false;
    }
    context = value;
    return true;
}

bool latched()
{
    uint32_t context     = 0U;
    uint32_t const holds = held;
    bool const plausible = read(context);
    return !plausible || (holds != heldCopy) || (holds != 0U);
}

bool report(uint32_t const context)
{
    uint32_t const bits = (context != 0U) ? context : ALL;
    ::interrupts::SuspendResumeAllInterruptsScopedLock const lock;
    uint32_t known       = 0U;
    bool const plausible = read(known);
    if (plausible && ((bits & ~known) == 0U))
    {
        return false; // reported before: its reaction stands (S1 held, or its group degraded)
    }
    uint32_t const all = known | bits; // an implausible record reads ALL
    reported           = all;
    reportedCopy       = all;
#if NX_SAFE_SUPERVISOR_DEGRADED
    /* SYS-135 (safety review SYS-128/SYS-130 C1): a trip of a path the project maps to SD degrades only the group of
     * that path, the rest of the ECU keeps running, no reset (the path stays latched, H1). Findings of the path, a
     * report without context and an implausible record keep S1 held (SafeSupervisor::degradeHwShutoff). */
    if (plausible && ::safety::SafeSupervisor::getInstance().degradeHwShutoff(bits))
    {
        ::util::logger::Logger::error(
            ::util::logger::SAFETY,
            "Event: HW_SHUTOFF, hardware shut-off path 0x%x (new 0x%x), its group degraded (SD), no reset",
            all,
            bits & ~known);
        return true;
    }
#endif
    held     = all;
    heldCopy = all;
    ::util::logger::Logger::error(
        ::util::logger::SAFETY,
        "Event: HW_SHUTOFF, hardware shut-off path 0x%x (new 0x%x), safe state held until reset",
        all,
        bits & ~known);
#ifdef PLATFORM_SUPPORT_IO
    // S1 without a reset: the hardware path latches until reset, a reset would re-arm the bridge by itself
    ::safety::SafeState::enterSafeState();
#endif
    return true;
}

} // namespace hwShutoff
} // namespace safety
