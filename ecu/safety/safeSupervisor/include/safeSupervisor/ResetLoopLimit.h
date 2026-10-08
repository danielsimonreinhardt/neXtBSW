/* SYS-43 (finding F13 / task T4 of doc/safety/fmea.md, SSR-065): limit on consecutive safety resets.
 *
 * Why: a permanent fault that a stage-1 mechanism answers with S1 + S2 (safe state, then reset) restarts the ECU
 * endlessly, and every restart runs the application until the first safety cycle finds the fault again - the safe
 * state is left briefly at every restart. A system basis chip limits that with its own reset counter (SM-52), but
 * that is device-dependent and not credited, and a board without one has no limit at all. Decision of the user
 * 2026-09-21 (decision board `sys43-resetschleife`): count consecutive safety resets in no-init RAM (survives a
 * software reset, cleared at power-on and after a configured time of fault-free operation); after N of them the
 * ECU holds the safe state without a reset (like `sys23-halt`: SafeSupervisor::holdSafeState); only a power cycle
 * ends it.
 *
 * What counts as a safety reset, from two independent sources:
 * - the ECU's own record (this file, in no-init RAM): the supervisor marks it immediately before the reset of an
 *   S1 + S2 reaction (SafeSupervisor::handle), with the event as cause;
 * - the reset status of the MCU as the family port captured it before main() (s32k1xx: RCM_SRS through
 *   platforms/s32k1xx/port/mcuResetReason, the MCU source of SM-50): a watchdog reset (program-flow supervision
 *   SM-01, not the watchdog fast test of the start-up) and a power-on; plus the fault-exception record of the port
 *   (s32k1xx: the hard fault dump of SM-17, which resets after the dump, and a core lockup).
 *   A watchdog reason counts only **alone**: board finding 2026-09-22 - a debugger reset (J-Link VC_CORERESET +
 *   AIRCR.SYSRESETREQ) reports RESET | WATCHDOG (RCM_SRS with the watchdog bit next to the reset bit), a real
 *   time-out of the watchdog sets the watchdog bit only. A start with another reset reason next to the watchdog
 *   (Start::otherReset) is therefore no safety reset by the MCU source.
 * Every other reset (reset pin, debugger, a software reset nobody marked - e.g. a diagnostic ECU reset or the jump
 * into the bootloader) neither counts nor clears: it is no evidence that the fault is gone. A power-on (or a
 * low-voltage reset, where the RAM content is lost) clears the record.
 *
 * Family neutral, no register access: the family port places the record in its no-init RAM, clears it where the
 * RAM content may be undefined (s32k1xx: boardInit() on POR/LVD, before the first read - the RAM has ECC), and
 * classifies the start; the supervisor keeps the rule. Host test: test/host/SafeSupervisorHostTest.cpp.
 */

#pragma once

#include <cstddef>
#include <cstdint>

namespace safety
{
namespace resetloop
{
/** Cause of a counted safety reset (Record::lastCause, Record::pending). */
constexpr uint32_t CAUSE_NONE            = 0U;
constexpr uint32_t CAUSE_WATCHDOG        = 1U;     // MCU watchdog reset (not the fast test of the start-up)
constexpr uint32_t CAUSE_FAULT_EXCEPTION = 2U;     // fault exception of the core (s32k1xx: hard fault dump, SM-17)
constexpr uint32_t CAUSE_EVENT           = 0x100U; // | event id of SafeSupervisor::Event: an S1 + S2 reaction

/** Largest count kept; further safety resets saturate here. */
constexpr uint32_t COUNT_MAX = 0xFFFFU;

/**
 * The record in no-init RAM (placed by the family port). Plain words only: the family port may clear it before the
 * RAM initialization of the start-up. Content after power-on is random; `check` tells a written record from that.
 */
struct Record
{
    uint32_t count;     // consecutive safety resets so far
    uint32_t pending;   // cause of a safety reset marked by the supervisor but not yet counted, CAUSE_NONE if none
    uint32_t lastCause; // cause of the last counted safety reset
    uint32_t check;     // checkOf(count, pending, lastCause)
};

constexpr uint32_t RECORD_KEY = 0x524C3433UL; // "RL43"

constexpr uint32_t rotl(uint32_t const value, uint32_t const bits)
{
    return (value << bits) | (value >> (32U - bits));
}

constexpr uint32_t checkOf(uint32_t const count, uint32_t const pending, uint32_t const lastCause)
{
    return ~(RECORD_KEY ^ count ^ rotl(pending, 11U) ^ rotl(lastCause, 21U));
}

inline bool isValid(Record const volatile& record)
{
    uint32_t const count = record.count;
    return (count <= COUNT_MAX) && (record.check == checkOf(count, record.pending, record.lastCause));
}

inline void write(Record volatile& record, uint32_t const count, uint32_t const pending, uint32_t const lastCause)
{
    record.count     = count;
    record.pending   = pending;
    record.lastCause = lastCause;
    record.check     = checkOf(count, pending, lastCause);
}

/** Power-on, low-voltage reset, or the fault-free time is over: no safety reset in a row any more. */
inline void clear(Record volatile& record) { write(record, 0U, CAUSE_NONE, CAUSE_NONE); }

/** How this start came about, as the family port sees it. */
struct Start
{
    bool powerOn;        // power-on or low-voltage reset: RAM content lost, the record is cleared
    bool watchdog;       // the MCU watchdog reset the MCU (a real time-out, not the fast test of the start-up)
    bool faultException; // the port's fault-exception handler reset the MCU (s32k1xx: hard fault dump present,
                         // or a core lockup)
    bool otherReset;     // a reset reason other than power-on and watchdog in the same start (pin, debugger,
                         // software, ...): a watchdog bit next to it does not count (debugger reset, see above)
};

/** Result of the start. */
struct StartResult
{
    uint32_t count;     // consecutive safety resets including this start's
    uint32_t lastCause; // cause of the last counted one (CAUSE_NONE if count is 0)
    bool counted;       // this start followed a safety reset
    bool recordValid;   // the record was intact (false: random content or overwritten - counted from 0)
};

/**
 * Called once per start, after the start-up's own resets (s32k1xx: after the watchdog fast test). Counts this start
 * if it followed a safety reset, clears on power-on, keeps the count otherwise, and writes the record back with no
 * mark pending. The supervisor's mark has priority as cause (it names the event); without one a fault exception,
 * then the watchdog.
 */
inline StartResult onStart(Record volatile& record, Start const& start)
{
    if (start.powerOn)
    {
        clear(record);
        return StartResult{0U, CAUSE_NONE, false, true};
    }
    bool const valid   = isValid(record);
    uint32_t count     = valid ? record.count : 0U;
    uint32_t lastCause = valid ? record.lastCause : CAUSE_NONE;
    uint32_t cause     = valid ? record.pending : CAUSE_NONE;
    if (cause == CAUSE_NONE)
    {
        if (start.faultException)
        {
            cause = CAUSE_FAULT_EXCEPTION;
        }
        else if (start.watchdog && !start.otherReset)
        {
            cause = CAUSE_WATCHDOG;
        }
    }
    bool const counted = (cause != CAUSE_NONE);
    if (counted)
    {
        count     = (count < COUNT_MAX) ? (count + 1U) : COUNT_MAX;
        lastCause = cause;
    }
    write(record, count, CAUSE_NONE, lastCause);
    return StartResult{count, lastCause, counted, valid};
}

/**
 * The supervisor resets for `cause` now (S1 + S2): the next start counts it. The count is kept; a record that is
 * no longer intact (overwritten during the run) is started again from 0.
 */
inline void markSafetyReset(Record volatile& record, uint32_t const cause)
{
    bool const valid = isValid(record);
    write(record, valid ? record.count : 0U, cause, valid ? record.lastCause : CAUSE_NONE);
}

/** True if the start with `count` consecutive safety resets must hold the safe state (`limit` >= 1). */
constexpr bool limitReached(uint32_t const count, uint32_t const limit) { return (limit != 0U) && (count >= limit); }

/** Safety cycles of the fault-free time: `clearMs` rounded up to whole cycles of `cyclePeriodMs`, at least one. */
constexpr uint32_t clearCyclesOf(uint32_t const clearMs, uint32_t const cyclePeriodMs)
{
    if (cyclePeriodMs == 0U)
    {
        return 1U;
    }
    uint32_t const cycles = (clearMs / cyclePeriodMs) + (((clearMs % cyclePeriodMs) != 0U) ? 1U : 0U);
    return (cycles != 0U) ? cycles : 1U;
}

/** Context word of the supervisor's RESET_LOOP_LIMIT event: cause in bits 31..16, count in bits 15..0. */
constexpr uint32_t contextOf(uint32_t const count, uint32_t const lastCause)
{
    return ((lastCause & 0xFFFFU) << 16U) | (count & 0xFFFFU);
}

namespace detail
{
inline size_t append(char* const text, size_t const size, size_t length, char const* part)
{
    while ((*part != '\0') && ((length + 1U) < size))
    {
        text[length] = *part;
        ++length;
        ++part;
    }
    text[length] = '\0';
    return length;
}

inline size_t appendNumber(char* const text, size_t const size, size_t const length, uint32_t value)
{
    char digits[11];
    size_t n = 0U;
    do
    {
        digits[n] = static_cast<char>('0' + static_cast<char>(value % 10U));
        ++n;
        value /= 10U;
    } while ((value != 0U) && (n < sizeof(digits)));
    char reversed[12];
    for (size_t i = 0U; i < n; ++i)
    {
        reversed[i] = digits[n - 1U - i];
    }
    reversed[n] = '\0';
    return append(text, size, length, reversed);
}
} // namespace detail

/**
 * Event text of the hold, for the console line of SafeSupervisor::holdSafeState (no printf):
 * "RESET_LOOP_LIMIT, 3 safety resets, last: event 5" (resp. "last: watchdog", "last: fault exception").
 * \return length without the terminating 0 (`size` >= 1)
 */
inline size_t formatHoldText(char* const text, size_t const size, uint32_t const count, uint32_t const lastCause)
{
    text[0]       = '\0';
    size_t length = detail::append(text, size, 0U, "RESET_LOOP_LIMIT, ");
    length        = detail::appendNumber(text, size, length, count);
    length        = detail::append(text, size, length, " safety resets, last: ");
    if (lastCause == CAUSE_WATCHDOG)
    {
        length = detail::append(text, size, length, "watchdog");
    }
    else if (lastCause == CAUSE_FAULT_EXCEPTION)
    {
        length = detail::append(text, size, length, "fault exception");
    }
    else if ((lastCause & CAUSE_EVENT) != 0U)
    {
        length = detail::append(text, size, length, "event ");
        length = detail::appendNumber(text, size, length, lastCause & 0xFFU);
    }
    else
    {
        length = detail::append(text, size, length, "unknown");
    }
    return length;
}
} // namespace resetloop
} // namespace safety
