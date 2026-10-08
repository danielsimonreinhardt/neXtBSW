#pragma once

/* SYS-139 (decision C2 (a) of the user, 2026-09-29; doc/safety/partner-and-degraded.md 5.4 and 5.7): the family port
 * keeps an active short that the walks of the safe outputs table have applied, instead of taking it away in the next
 * driver cycle. Used only in a project in which a PWM group allows an active short (NX_SAFE_OUTPUT_SELECT, the
 * extended table of hardFaultHandler/safeOutputs.h); every other project does not include this header.
 *
 * "The group is in a short" means exactly what the walks would write now for the group's timer entry: the selection
 * word holds exactly the pattern of an allowed short and the clock of the timer is on (nxSafeOutputSelectedValue()).
 * Blocked (a bridge fault, SYS-140; a failed start test; a finding of the hardware threshold path) or any doubt reads
 * freewheel, and every rule below then falls back to the behaviour without a short.
 *
 * The three rules (all in this family port; the drivers of libs/bsw are not changed):
 * 1. **Enable adapter** (the gate driver bound to the group, board pwm[].gate_driver): the S1 lock and the lock of a
 *    degraded group do not switch its enable off while the group is in a short **and** the driver is enabled - the
 *    lock reads "not locked" for that driver (lock()), the adapter reports "held" to the hold of S1 and does not
 *    switch the enable off for the debug halt guard (keepEnable()). A driver that is not enabled stays locked:
 *    enable() is refused in S1 - nothing is switched on (SSR-004 (a)), an enable that was on is only not switched off.
 * 2. **PWM inhibit of the debug halt guard and of the hold** (FtmPwmInhibit): writes the output control the walks
 *    write for the group (outputControl()) - the short pattern in a short, freewheel otherwise - never plain freewheel
 *    over a selected short.
 * 3. **Group lock of SYS-123** (ComplementaryPwm::lockIfDegraded, ftmPwm::lockedOutputControl()): a degraded group
 *    gets the same value as 2 - its selected safe state (concept 4.5), not always freewheel.
 * The hardware threshold path has priority (safety review SYS-128 C2): a finding or the timer's fault latch removes
 * the short for the power cycle (nxSafeStateBlockShort) before the group is forced (HwThresholdWatch.h), so rules 1-3
 * give freewheel from then on; the fault input overrides the output control in hardware anyway.
 *
 * Hardware independent: the table entry is passed in; host test test/host/ShortHoldHostTest.cpp. */

#include "hardFaultHandler/safeOutputs.h"
#include "hardFaultHandler/safeStateSelection.h"

#include <cstdint>

#if !defined(NX_SAFE_OUTPUT_SELECT)
#error "ShortHold.h: only for a project with an active short (NX_SAFE_OUTPUT_SELECT)"
#endif

namespace integration
{
namespace shortHold
{
/** Entry of the PWM group in the safe outputs table (tools/bswgen: the used PWM groups first; this port has one). */
constexpr uint32_t PWM_GROUP_ENTRY = 0U;
/** Its selection group (hardFaultHandler: nxSafeStateSelection[0], the first used PWM group). */
constexpr uint32_t PWM_GROUP = 0U;

/** true while the walks write a short for `entry` (selection word exactly a pattern, timer clock on). */
inline bool inShort(NxSafeOutputSelect const& entry) { return nxSafeOutputSelectedValue(&entry) != entry.value; }

/** true while the walks write a short for the project's PWM group. */
inline bool groupInShort() { return inShort(nxSafeOutputsSelect[PWM_GROUP_ENTRY]); }

/**
 * Rule 2/3: the output control the walks write for the group - read from the table entry, but only if that entry is
 * the output control register `reg` (anything else, e.g. a table of another layout: `freewheel`).
 */
inline uint32_t outputControl(volatile uint32_t const* const reg, uint32_t const freewheel)
{
    NxSafeOutputSelect const& entry = nxSafeOutputsSelect[PWM_GROUP_ENTRY];
    return (NX_SAFE_OUTPUT_REG(entry.address) == reg) ? nxSafeOutputSelectedValue(&entry) : freewheel;
}

/** Rule 1: the enable of a bound, enabled driver stays on while its group is in a short. */
inline bool keepEnable(bool const driverEnabled) { return driverEnabled && groupInShort(); }

/** Rule 1: the lock of a bound driver - the base lock (S1, degraded group) unless the enable is kept. */
inline bool lock(bool const baseLocked, bool const driverEnabled) { return baseLocked && !keepEnable(driverEnabled); }

/** The hardware threshold path found something or tripped: the short is gone until power-on (idempotent). */
inline void removeForHardwarePath() { nxSafeStateBlockShort(PWM_GROUP); }
} // namespace shortHold
} // namespace integration
