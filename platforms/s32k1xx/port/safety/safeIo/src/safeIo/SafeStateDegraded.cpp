/* SYS-117: the degraded reaction SD - a subset of the safe outputs table goes to its safe state (safeIo/SafeState.h).
 *
 * The groups are subsets of the one table the fault handlers, the halt deadman and S1 walk (IO-32, SM-23: one list,
 * not two). tools/nxconfig derives, per safe-output group of the project (safety.output_groups), the bits of its
 * entries - today the SWOCTRL entry of its PWM group, the first block of the table in the order of the used PWM groups
 * (tools/bswgen collect_safe_outputs); the safe_off outputs bound to a PWM group join with IO-45. The walk is the one
 * of nxSafeOutputsSwitchOff() (hardFaultHandler/safeOutputs.h) with a mask: same gate check, same 32-bit store, same
 * barrier - checked against it by the host test (test/host/SafeStateDegradedHostTest.cpp).
 *
 * No latch, no indicator: S1 is not entered, the drivers of the other groups keep running. Holding the groups (a
 * driver or the debug halt guard that releases a timer) is the supervisor's part: it walks them again in every safety
 * cycle (SafeSupervisor::holdDegradedGroups). Own translation unit: linked only into a project with a degraded mapping.
 */

#include "safeIo/SafeState.h"

#include <hardFaultHandler/safeOutputs.h>

#include <cstdint>

namespace safety
{

void SafeState::enterDegradedState(uint32_t const entries)
{
#if defined(NX_SAFE_OUTPUT_SELECT)
    // SYS-116: a degraded group goes to its selected safe state (concept 4.5) - the extended table, same entries
    NxSafeOutputSelect const* entry = &nxSafeOutputsSelect[0];
#else
    NxSafeOutput const* entry = &nxSafeOutputs[0];
#endif
    for (uint32_t index = 0U; (entry->address != 0U) && (index < 32U); ++index, ++entry)
    {
        if ((entries & (static_cast<uint32_t>(1U) << index)) == 0U)
        {
            continue;
        }
        if (entry->gateAddress != 0U)
        {
            volatile uint32_t const* const gate = NX_SAFE_OUTPUT_REG(entry->gateAddress);
            if ((*gate & entry->gateMask) == 0U)
            {
                continue;
            }
        }
#if defined(NX_SAFE_OUTPUT_SELECT)
        *NX_SAFE_OUTPUT_REG(entry->address) = nxSafeOutputSelectedValue(entry);
#else
        *NX_SAFE_OUTPUT_REG(entry->address) = entry->value;
#endif
    }
#if defined(__ARM_ARCH)
    __asm volatile("dsb" ::: "memory");
#endif
}

} // namespace safety
