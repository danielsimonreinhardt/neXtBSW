/* SYS-138 (safety review SYS-116/SYS-30, point K6): read-back of the safe state for the paths of the safe supervisor in
 * which SafeIo::cyclic() does not look - the hold of S1 without a reset (SYS-23: no task runs), the groups of the
 * degraded reaction SD (SYS-117/SYS-135: no S1 latch) and the S1 + S2 sequence before the reset that the fault
 * reactions share (resetWithLine(): kernel faults, ETL_ASSERT, ECC error, every resetting event).
 *
 * The supervisor names what it needs, the family port implements it (like safeSupervisor/SafeConsole.h and
 * safeSupervisor/SafeStateShort.h). S32K1: platforms/s32k1xx/port/safety/safeIo/src/safeIo/SafeIo.cpp, with the
 * generated read-back list of the safe outputs table (NX_SAFE_OUTPUT_READBACKS, SYS-30) and the same acceptance rule as
 * SafeIo::cyclic() - an active short selected for a group (SYS-116/SYS-139) is a safe state as well.
 *
 * Compiled in only where the build says the family port provides it: NX_SAFE_SUPERVISOR_READBACK=1, set by
 * safeSupervisor/CMakeLists.txt together with the link to safeIo (PLATFORM_SUPPORT_IO). Without it SafeSupervisor.cpp
 * does not read back (a board without power outputs, the host tests of other modules).
 *
 * Both functions only read registers and write nothing - no RAM, no driver, no RTOS service, no lock - so they are
 * callable from any context the callers run in: the hold loop with every interrupt suspended, the safety task, a
 * fault interrupt, before the scheduler.
 *
 * Result: 0 if every judged entry shows its safe state, otherwise the context of the first one that does not - class
 * SAFE_OUTPUT (7) in bits 31..24 and the entry of the safe outputs table in bits 15..0, the context SafeIo reports
 * with LOCK_REGISTER_ERROR (safeIo/RegisterCheck.h). Never 0 for a finding (entry 0 reads 0x07000000).
 *
 * What is judged (the rule of the family port, S32K1):
 * - nothing before SafeIo::init() has run: before the BSP start the pins are in their reset configuration (pin
 *   function disabled, nothing drives them) and the register list has not verified pin functions and clock gates;
 * - afterwards every entry: its read-back register under the mask equals the expectation; an entry whose clock gate
 *   reads off is NOT confirmed (the register list of SYS-24 expects every such gate on, and a gated timer keeps its
 *   outputs frozen where the walk cannot reach them).
 */
#pragma once

#include <cstdint>

namespace safety
{
/** SYS-138: read-back of S1 - every entry of the safe outputs table. */
uint32_t safeStateReadback();

/**
 * SYS-138: read-back of the degraded groups - only the entries whose bit is set in `entries` (bit i = entry i of the
 * safe outputs table, as SafeState::enterDegradedState() takes them; entries from 32 on are not addressable here).
 */
uint32_t degradedStateReadback(uint32_t entries);
} // namespace safety
