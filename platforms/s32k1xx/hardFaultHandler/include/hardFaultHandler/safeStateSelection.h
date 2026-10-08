/* SYS-116: selection of the safe state of a PWM group - freewheel or an active short (doc/safety/
 * partner-and-degraded.md part C, decisions C1-C6 of 2026-09-29).
 *
 * The project allows per PWM group a list of safe states (project.yaml pwm.<group>.safe_states, checked by
 * tools/nxconfig/safestate.py); the application selects the one in force at run time (decision C6 (a): through an
 * RTE port, validated by this service). What the switch-off paths do with the selection is written in
 * hardFaultHandler/safeOutputs.h (NX_SAFE_STATE_WORD_*): this service is the only writer of the selection words
 * nxSafeStateSelection[], and it writes only a pattern of a state the group allows and that is still available.
 *
 * Available only in a project built with NX_SAFE_OUTPUT_SELECT (a group allows a short); the functions are defined
 * in src/SafeOutputSelection.cpp of the library nxSafeOutputSelection.
 *
 * Call context: any task or interrupt, in privileged mode. Each function is a few loads and at most one store per
 * word; nothing blocks. SYS-172: the check of the block and the store that depends on it (select, start test begin),
 * the stores of a block and the restore of the start test end each run under a short interrupt lock (PRIMASK saved and
 * restored, nesting allowed), so a block cannot fall between a check and its store.
 * The service does not raise events itself - it returns a result; the caller (the RTE glue for a refused request,
 * the integration for a failed start test) reports it, and the reaction stays the safe supervisor's.
 */

#ifndef HARD_FAULT_HANDLER_SAFE_STATE_SELECTION_H
#define HARD_FAULT_HANDLER_SAFE_STATE_SELECTION_H

#include <stdint.h>

#define NX_SAFE_STATE_FREEWHEEL   0U /* every switch of the group off */
#define NX_SAFE_STATE_SHORT_LOW   1U /* every low-side switch on, every high-side switch off */
#define NX_SAFE_STATE_SHORT_HIGH  2U /* every high-side switch on, every low-side switch off */
#define NX_SAFE_STATE_COUNT       3U
#define NX_SAFE_STATE_BIT(state)  (1UL << (state)) /* bit of a state in the allowed mask (NX_SAFE_STATE_GROUPS) */

/* Results */
#define NX_SAFE_STATE_OK                0
#define NX_SAFE_STATE_ERROR_GROUP       (-1) /* no such group */
#define NX_SAFE_STATE_ERROR_NOT_ALLOWED (-2) /* the project does not allow the state for the group */
#define NX_SAFE_STATE_ERROR_BLOCKED     (-3) /* the short was removed for this power cycle (bridge fault, test) */
#define NX_SAFE_STATE_ERROR_CLOCK       (-4) /* start test: the clock of the group's timer is off */
#define NX_SAFE_STATE_ERROR_READBACK    (-5) /* start test: the forcing register does not hold the pattern */
#define NX_SAFE_STATE_ERROR_SEQUENCE    (-6) /* start test: end without begin, or begin twice */

#ifdef __cplusplus
extern "C"
{
#endif

/** Number of PWM groups with a selection word (every used PWM group of the project). */
uint32_t nxSafeStateGroupCount(void);

/** Allowed states of `group` as NX_SAFE_STATE_BIT mask, minus a blocked short; 0 for an unknown group. */
uint32_t nxSafeStateAllowed(uint32_t group);

/**
 * Selects the safe state of `group` (NX_SAFE_STATE_*). Freewheel is always accepted. A short is accepted only if
 * the project allows it for the group and it is not blocked; a refused request leaves the selection unchanged
 * (decision C6 (a): refused requests are reported by the caller, S3).
 */
int32_t nxSafeStateSelect(uint32_t group, uint32_t state);

/** The state the walks would apply for `group` now, from its word alone (NX_SAFE_STATE_*; freewheel for an unknown
 * group or any word that is not a pattern). The clock of the timer is not considered. */
uint32_t nxSafeStateSelected(uint32_t group);

/**
 * Removes the shorts of `group` for this power cycle and selects freewheel: a bridge fault (decision C4, the safe
 * supervisor's list) or a failed start test. Idempotent. Nothing gives a short back but the next power-on.
 * A start test of the group that is running ends at once in freewheel (SYS-172); its end returns
 * NX_SAFE_STATE_ERROR_SEQUENCE.
 */
void nxSafeStateBlockShort(uint32_t group);

/**
 * Start-up test of the short (decision C5 (a), register part; doc/safety/partner-and-degraded.md section 5.10).
 * The caller runs it once per start, at standstill, with the gate driver enabled and before the application
 * enables the group, and waits the configured time between begin and end (NX_SAFE_STATE_GROUPS, test duration):
 *
 *   begin: forces the short pattern `state` on every entry of the group whose short value differs from its
 *          freewheel value and is not the "leave as it is" store 0 (the timer's output control), and reads each of
 *          them back; the values found before are kept for end.
 *   end:   writes the values found by begin back and reads them back.
 *
 * A failure of either blocks the group's shorts (nxSafeStateBlockShort) and forces freewheel before it returns;
 * the caller raises the event (proposed SAFE_STATE_TEST_FAILURE, degradable). A phase-voltage check (IO-64) and the
 * read-back of the gate-driver enable belong to the caller.
 */
int32_t nxSafeStateShortTestBegin(uint32_t group, uint32_t state);
int32_t nxSafeStateShortTestEnd(uint32_t group);

#ifdef __cplusplus
}
#endif

#endif /* HARD_FAULT_HANDLER_SAFE_STATE_SELECTION_H */
