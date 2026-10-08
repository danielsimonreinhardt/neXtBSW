#pragma once

/* SYS-140 (decision C4, doc/safety/partner-and-degraded.md 5.9): the two functions of the family port's safe state
 * selection that the safe supervisor calls when a bridge fault removes the active short (safeSupervisor/Degraded.h,
 * NX_SAFE_BRIDGE_FAULT_CLASSES). Family neutral: only the contract, no register and no table.
 *
 * Every family port whose PWM groups can be put into an active short defines them - S32K1:
 * platforms/s32k1xx/hardFaultHandler (hardFaultHandler/safeStateSelection.h, library nxSafeOutputSelection, linked only
 * in a project that allows a short). The supervisor uses them only in such a project
 * (NX_MODULE_SAFESUPERVISOR_DERIVED_BRIDGE_FAULT_DEVICES, tools/nxconfig/safestate.py); a family port without them
 * does not link, which is the intended stop.
 *
 * Contract (the family's header says the same): callable from the safety task, a few loads and stores of ordinary RAM,
 * nothing blocks. nxSafeStateBlockShort() is idempotent, selects freewheel first and keeps the short away until the
 * next power-on; an unknown group is ignored. SYS-172 (HFH-F06): the family makes the block atomic against a select
 * of another task or interrupt (S32K1: a short interrupt lock), so no select can give the short back; a running start
 * test of the group ends in freewheel. */

#include <cstdint>

extern "C"
{
/** Number of PWM groups with a selection word (every used PWM group of the project). */
uint32_t nxSafeStateGroupCount(void);

/** Removes the active short of `group` for this power cycle and selects freewheel. */
void nxSafeStateBlockShort(uint32_t group);
}
