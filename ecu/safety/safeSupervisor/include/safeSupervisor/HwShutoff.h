#pragma once

/* SYS-130 (safety review SYS-86 H7, 2026-09-30): the narrow interface through which the family port reports a hardware
 * shut-off path of a PWM group to the safe supervisor - a path that switches the group off without the core (S32K1:
 * comparator -> trigger mux -> fault input of the timer, SYS-86) - and through which the application reads it.
 *
 * Family neutral: the supervisor keeps and logs the context word, it does not interpret it. What the family port puts
 * into it (S32K1, hwThreshold/HwThresholdMonitor.h reportContext): bit 0 = the timer has latched a fault (the
 * threshold tripped, FMS[FAULTF]), bits 23..8 = findings of the path (SYS-128: switched off, reconfigured, stuck,
 * not latched), bits 31..24 = index of the timer. 0 means nothing to report.
 *
 * Reaction, decided here (not by the reporter): **S1 held until reset, no reset** - every safe output off (the gate
 * driver enable with it), the ECU keeps running so that the application, the tester and the fault memory can see it.
 * No reset because the hardware path latches until reset (FTM MODE[FAULTM] 10b, safety review H1: manual recovery
 * only) - a reset would re-arm the bridge by itself. Leaving S1 through the register check's return path
 * (CHECK_REGISTER_ERROR) is refused while a report is latched (SafeSupervisor.cpp, NX_SAFE_SUPERVISOR_HW_SHUTOFF).
 *
 * SYS-135 (safety review SYS-128/SYS-130 C1, decision of the safety responsible 2026-09-30): in a project that maps
 * HW_SHUTOFF to the degraded reaction (`safety.degraded`, Degraded.h NX_SAFE_DEGRADABLE_REPORTS) a report that says
 * "tripped" and nothing else degrades **only the safe-output group of that path** (SD) instead of S1 for the whole
 * ECU - still no reset, the path stays latched (H1). The last group escalates to S1 without a reset (review SYS-117
 * R2). Findings of the path (bits 23..8), a report without context, an unmapped timer and an implausible record keep
 * S1 held as above. latched() then means "S1 held by a report": a degraded report does not keep the register check's
 * return path closed - its group is held by the degraded reaction (SafeSupervisor::holdDegradedGroups).
 *
 * The report is kept in the protected RAM of the supervisor (two copies; a mismatch reads as "everything reported",
 * fail-safe). Only an image whose family port reports links HwShutoff.cpp; every other image is unchanged. */

#include <cstdint>

namespace safety
{
namespace hwShutoff
{
/** Context that stands for "everything": a report without context (malformed) or an implausible record. */
constexpr uint32_t ALL = 0xFFFFFFFFU;

/**
 * Safety context only (protected RAM writable, SafetyManager::cyclic of the family port): reports `context` (0 is
 * malformed and reported as ALL). Only bits not reported before cause a reaction: a log line with the context and
 * S1 held, or (SYS-135) the degraded reaction of the path's group. Called in every safety cycle while the path reports something; a call without new bits does nothing.
 * \return true if the call reported new bits
 */
bool report(uint32_t context);

/**
 * Read only, from any task (the RTE, the fault memory, the console): the bits reported so far, 0 = nothing.
 * \return false if the record is implausible - `context` then reads ALL (fail-safe)
 */
bool read(uint32_t& context);

/** True while S1 is held by a report (or a record is implausible): S1 is not left. Without a degraded mapping (and
 *  before SYS-135) that is "while anything is reported"; a report answered with SD does not count (SYS-135). */
bool latched();

} // namespace hwShutoff
} // namespace safety
