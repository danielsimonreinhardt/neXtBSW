#pragma once

/* SYS-137: record of the safe console lines of one start, in the no-init RAM of the family port, reported by the
 * next start - evidence on the ECU itself whether the SAFETY RESET line (or a HARD FAULT RESET line) of the start
 * before left the MCU, independent of whoever records the console.
 *
 * Board finding 2026-09-30 (SYS-73 (1), captures sys73_04/05/07): after the injected ECC fault of the start-up test in
 * main() (INTEGRITY_EVENT_DMA_ECC_ERROR before the BSP) the next start counted the safety reset (last cause 0x107,
 * RCM_SRS SW) but no capture showed the SAFETY RESET line - in two of the three not even the complete log of the next
 * start. The code path writes the line synchronously (safeConsoleFlush, safeConsoleWrite, safeConsoleFlush, reset;
 * doc/changelog.d/SYS-137.md); what the recorder saw cannot tell a missing line from a reader that was not reading.
 * The two record words of SYS-39 (nxSafeConsoleRequested/-Written) cannot tell either: they are in .bss, which the
 * start-up code clears at the reset that follows the line.
 *
 * So every line and every flush of the safe console is counted here, in RAM the reset keeps:
 * - lines        lines handed to safeConsoleWrite() (a start that resets writes one, a hold one);
 * - requested    bytes of the last line, written: bytes of it the transmitter took (== requested: the line is out,
 *                except its last character, which the flush after it covers);
 * - flushesOk    flushes that found the transmitter idle, flushTimeouts: flushes that gave up (dead transmitter; the
 *                supervisor then skips its line - requested stays 0 for that reset);
 * - check        the other words folded with the magic, so a record the run overwrote is recognized.
 * main() takes it once per start, after the watchdog fast test (whose resets keep it), and reports it with the start
 * lines ("safe console of the start before: ..."), then it is empty again. boardInit() clears it after power-on and
 * low voltage, before anything reads it (the RAM has ECC and its content is undefined then).
 *
 * Contract: noteLine()/noteFlush() are stores to RAM only - no call, no loop, no interrupt enable - so they keep the
 * polling-only rule of safeConsoleWrite()/safeConsoleFlush() (test_hold_console_elf.py) and can run in a fault
 * handler with the MPU gate in any state (the record is ordinary no-init RAM, not the protected safety RAM).
 * Header only, pure logic over the record: the host test (integration/test/host/SafetyResetConsoleHostTest.cpp) runs
 * exactly this code. */

#include <cstdint>

namespace platform
{
namespace safeconsole
{
/** Marks a record written by this layout ("SCR1"); anything else in the words is not a record. */
constexpr uint32_t RECORD_MAGIC = 0x53435231U;

/** Counters saturate here (a start writes one line and two flushes; more means starts without a take in between). */
constexpr uint32_t COUNT_MAX = 0xFFFFU;

struct Record
{
    uint32_t magic;
    uint32_t lines;
    uint32_t requested;
    uint32_t written;
    uint32_t flushesOk;
    uint32_t flushTimeouts;
    uint32_t check;
};

/** What main() reports: the counters of the start before, and whether the record was intact. */
struct Snapshot
{
    bool present; // the record held something (a line or a flush)
    bool intact;  // magic and check agree (false: overwritten, the numbers are not reported)
    uint32_t lines;
    uint32_t requested;
    uint32_t written;
    uint32_t flushesOk;
    uint32_t flushTimeouts;
};

inline uint32_t checkOf(
    uint32_t const lines,
    uint32_t const requested,
    uint32_t const written,
    uint32_t const flushesOk,
    uint32_t const flushTimeouts)
{
    return ~(RECORD_MAGIC ^ lines ^ (requested << 8U) ^ (written << 16U) ^ (flushesOk << 4U) ^ (flushTimeouts << 12U));
}

inline void write(
    Record volatile& record,
    uint32_t const lines,
    uint32_t const requested,
    uint32_t const written,
    uint32_t const flushesOk,
    uint32_t const flushTimeouts)
{
    record.magic         = RECORD_MAGIC;
    record.lines         = lines;
    record.requested     = requested;
    record.written       = written;
    record.flushesOk     = flushesOk;
    record.flushTimeouts = flushTimeouts;
    record.check         = checkOf(lines, requested, written, flushesOk, flushTimeouts);
}

inline bool isIntact(Record volatile const& record)
{
    return (record.magic == RECORD_MAGIC)
           && (record.check
               == checkOf(record.lines, record.requested, record.written, record.flushesOk, record.flushTimeouts));
}

/** Empty record: after power-on (boardInit), and after each take. Whole words only (the RAM has ECC). */
inline void clear(Record volatile& record) { write(record, 0U, 0U, 0U, 0U, 0U); }

inline uint32_t saturatedIncrement(uint32_t const value) { return (value < COUNT_MAX) ? (value + 1U) : COUNT_MAX; }

/** safeConsoleWrite() handed `requested` bytes to the transmitter loop, which took `written` of them. A record that
 * is not intact starts again from this line. */
inline void noteLine(Record volatile& record, uint32_t const requested, uint32_t const written)
{
    bool const intact = isIntact(record);
    write(
        record,
        saturatedIncrement(intact ? record.lines : 0U),
        requested,
        written,
        intact ? record.flushesOk : 0U,
        intact ? record.flushTimeouts : 0U);
}

/** safeConsoleFlush() ended: `idle` = the transmitter got idle, false = the time-out ran out. */
inline void noteFlush(Record volatile& record, bool const idle)
{
    bool const intact        = isIntact(record);
    uint32_t const lines     = intact ? record.lines : 0U;
    uint32_t const requested = intact ? record.requested : 0U;
    uint32_t const written   = intact ? record.written : 0U;
    uint32_t const ok        = intact ? record.flushesOk : 0U;
    uint32_t const timeouts  = intact ? record.flushTimeouts : 0U;
    write(
        record,
        lines,
        requested,
        written,
        idle ? saturatedIncrement(ok) : ok,
        idle ? timeouts : saturatedIncrement(timeouts));
}

/** Reads the record of the start before and empties it (once per start, main()). */
inline Snapshot take(Record volatile& record)
{
    Snapshot snapshot{};
    snapshot.intact = isIntact(record);
    if (snapshot.intact)
    {
        snapshot.lines         = record.lines;
        snapshot.requested     = record.requested;
        snapshot.written       = record.written;
        snapshot.flushesOk     = record.flushesOk;
        snapshot.flushTimeouts = record.flushTimeouts;
        snapshot.present = (snapshot.lines != 0U) || (snapshot.flushesOk != 0U) || (snapshot.flushTimeouts != 0U);
    }
    else
    {
        // a record of this layout the run overwrote is worth a line; words that never were a record are not
        snapshot.present = (record.magic == RECORD_MAGIC);
    }
    clear(record);
    return snapshot;
}
} // namespace safeconsole
} // namespace platform

extern "C"
{
/** The record of the target (SafeConsole.cpp, .noinit): written by safeConsoleWrite()/safeConsoleFlush(), cleared by
 * boardInit() after power-on, taken by main(). */
extern ::platform::safeconsole::Record volatile nxSafeConsoleRecord;
}
