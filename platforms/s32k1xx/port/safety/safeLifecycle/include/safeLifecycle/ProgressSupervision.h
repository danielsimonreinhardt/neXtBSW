/* SYS-95 (findings DFA-1, DFA-2 and DFA-6 (a) of doc/safety/dfa.md): progress checks of the safety cycle on the S32K1.
 *
 * Three things that are supposed to advance while the safety task runs, each watched from the safety task on a time
 * base other than its own:
 * - DFA-1 (DF-36): the passes of the run-time ROM re-check (SM-14), which runs in the idle context. Deadline in
 *   safety cycles: three re-check periods (modules.safeMemory.rom_check_period_ms) - one period until the pass is
 *   due, one more that RomCheck itself grants flash writes before it reports ROMCHECK_OVERDUE, and one for the pass
 *   itself and the idle context to get the CPU. Beyond it: ROM_CHECK_FAILURE (S1 held, the reaction of every other
 *   ROM-check event). Only in an image that runs the re-check (NX_ROM_CHECK_IMAGE) and with a period other than 0;
 *   not after the ROM check reported a failure itself.
 * - DFA-2 (DF-05): the counter of the halt deadman (SM-06) moves (safeSupervisionCheck/LpitDeadmanProgress.h); a
 *   stopped LPIT clock is DEADMAN_CONFIGURATION_ERROR with the field DEADMAN_FIELD_COUNTER_STOPPED (S1 + S2, like
 *   every other deviation of the deadman). Only in a project with a deadman.
 * - DFA-6 (a) (DF-32, SPI-3): the OpenBSW system time advances like the LPTMR of the period check (SM-04, LPO) within
 *   the LPO range of the derivative, over the window of the period check; a stopped, slow or fast system time is
 *   SAFETY_CYCLE_PERIOD_VIOLATION kind 4 resp. 5 (S1 + S2: the reset restarts the time base). Not while the cycle
 *   timer's configuration deviates (the period check reports that, kind 3).
 *
 * Called once per safety cycle from SafetyManager::checkSupervisionPeripherals(), with the protected RAM writable
 * (the state lives in the .bss of this archive, MPU protected).
 */

#pragma once

#include <cstdint>

namespace safety
{
namespace progress
{
/** Context of ROM_CHECK_FAILURE raised by the progress check (DFA-1): continues the numbers of RomCheck::Result
 * (1 ... 6), "no pass, the check was not called". The supervisor holds S1 whatever the context. */
constexpr uint32_t ROM_CHECK_CONTEXT_NOT_CALLED = 7U;

/** One safety cycle of the three progress checks. */
void cyclic();
} // namespace progress
} // namespace safety
