/* SYS-95 (finding DFA-2 of doc/safety/dfa.md, DF-05): does the counter of the halt deadman (SM-06) move?
 *
 * The configuration re-check of SYS-41 (LpitDeadmanCheck.h) compares registers. A stopped LPIT functional clock - its
 * source (SIRC, FIRC, SOSC, SPLL divider) switched off, or the PCC gate or source of the LPIT changed - leaves every
 * register as configured, but the counter stands still: the deadman never expires, SM-06 is latent until the next
 * stall, which it then misses. On ARMv7-M nothing else notices (the system time runs on the DWT there).
 *
 * The check reads the current value of the deadman's channel and polls it until it changes, at most `maxPolls`
 * times. The caller derives the budget from the clock plan so that it spans at least two ticks of the LPIT at the
 * fastest core clock (every poll takes at least one core cycle): a running counter changes within the budget, a
 * stopped one does not. In a healthy cycle the loop ends after at most one LPIT tick.
 *
 * The verdict needs the channel enabled. The retrigger of the BSP task disables and enables the channel
 * (CLRTEN/SETTEN, LpitHaltDeadman::retrigger) and the deadman's interrupt stops it after an expiry; a safety task that
 * preempted the BSP task between the two writes sees T_EN = 0 and a counter that legitimately stands still. Then the
 * check reports NOT_RUNNING and the caller decides nothing in this cycle - the configuration check covers a channel
 * that stays disabled (no field of it: the retrigger heals T_EN; the deadman's own expiry follows a stall).
 *
 * Pure register reads, no data of its own (libsafe* archive). Register facts ([RM] Rev. 13 chapter 48, LPIT): CVALn
 * is the current value of the down counter of channel n, readable at any time; TCTRLn[T_EN] enables the channel.
 * Whether CVAL keeps its last value or reads a reset value while the functional clock is off is not stated by the
 * reference manual - both stand still, which is what the check needs (open point O4 of dfa.md, board check SYS95-B2).
 */

#pragma once

#include "safeSupervisionCheck/LpitDeadmanCheck.h"

#include <cstdint>

namespace safety
{
namespace supervision
{
/** Result of one progress check of the deadman counter. */
enum class LpitCounterProgress : uint8_t
{
    MOVING      = 0U, ///< the counter changed within the budget
    STOPPED     = 1U, ///< enabled channel, the counter did not change within the budget: the LPIT clock stands still
    NOT_RUNNING = 2U, ///< the channel is disabled (between CLRTEN and SETTEN of a retrigger, or after an expiry), or
                      ///< the expected configuration names no channel of the block: no verdict in this cycle
};

/** Deviating field of the deadman reported with DEADMAN_CONFIGURATION_ERROR when the counter stands still (SYS-95);
 * continues the mask of LpitDeadmanField. */
constexpr uint32_t DEADMAN_FIELD_COUNTER_STOPPED = 0x100U;

/**
 * Polls the counter of the deadman's channel until it changes, at most `maxPolls` times (>= 1).
 * \return MOVING, STOPPED or NOT_RUNNING (see LpitCounterProgress)
 */
LpitCounterProgress checkLpitDeadmanProgress(LpitDeadmanConfiguration const& expected, uint32_t maxPolls);

/**
 * Poll budget for an LPIT clock of `lpitHz` and a core clock of at most `coreHz`: two LPIT ticks in core cycles,
 * rounded up, plus two. A poll takes at least one core cycle, so the budget never ends before two ticks have passed.
 */
constexpr uint32_t lpitProgressPolls(uint32_t const coreHz, uint32_t const lpitHz)
{
    return (lpitHz == 0U) ? 0U : ((2U * ((coreHz + lpitHz - 1U) / lpitHz)) + 2U);
}

} // namespace supervision
} // namespace safety
