// SPDX-License-Identifier: Apache-2.0
#pragma once

/* SYS-12: detect log entries the OpenBSW ring buffer dropped before the console printed them, pure logic without
 * any OpenBSW dependency (host test: ecu/application/src/logger/test/host/EntryLossHostTest.cpp).
 *
 * Background: logger::EntryBuffer (OpenBSW logger/EntryBuffer.h) numbers every entry it stores. When a new entry does
 * not fit, addEntry() drops the oldest entries; a reader whose position lies before the oldest remaining entry is
 * moved on to it by getNextEntry(). Nothing reports that jump, and ConsoleEntryFormatter does not print the index,
 * so the lost lines vanished silently (board test 2026-09-18 with 4096 bytes: the first nine start-up lines).
 *
 * The index is still handed to every IEntryOutput: BufferedLoggerOutput::outputEntry() passes entryRef.getIndex()
 * AFTER getNextEntry() has moved the reference on, i.e. (index of the entry) + 1. The reader sees the entries in
 * ascending order, so a delivered index larger than the expected one means exactly (delivered - expected) entries
 * were dropped before this one. Only entries the reader had not printed yet count: entries dropped after they were
 * printed leave no gap. Unsigned arithmetic keeps this right across the wrap of the 32-bit index.
 */

#include <cstddef>
#include <cstdint>

namespace logger
{

/**
 * Gap detection on the index sequence one reader delivers. Not thread-safe by itself: exactly one reader calls
 * onEntry() (the logger output context, TASK_IDLE). getTotal() may be read from any context - a single aligned
 * 32-bit load, the value is at most one entry old.
 */
template<class E = uint32_t>
class EntryLossDetector
{
public:
    /**
     * \param deliveredIndex index handed to IEntryOutput::outputEntry() (entry index + 1, see above)
     * \return number of entries dropped directly before this entry, 0 in the normal case
     */
    E onEntry(E const deliveredIndex)
    {
        E const lost = static_cast<E>(deliveredIndex - _expected);
        _expected    = static_cast<E>(deliveredIndex + 1U);
        if (lost != 0U)
        {
            uint32_t const room = UINT32_MAX - _total;
            _total              = (static_cast<uint32_t>(lost) > room) ? UINT32_MAX
                                                                        : (_total + static_cast<uint32_t>(lost));
        }
        return lost;
    }

    /** Entries lost since start, saturating at UINT32_MAX. */
    uint32_t getTotal() const { return _total; }

private:
    /** The first entry has index 0 and is delivered as 1 (fresh EntryRef, see EntryBuffer::getNextEntry()). */
    E _expected      = 1U;
    uint32_t _total  = 0U;
};

/** Longest loss line: "... 4294967295 log entries lost (total 4294967295)\n". */
constexpr size_t LOSS_LINE_MAX = 64U;

namespace detail
{
inline size_t appendText(char* const buf, size_t pos, char const* text)
{
    while (*text != '\0')
    {
        buf[pos] = *text;
        ++pos;
        ++text;
    }
    return pos;
}

inline size_t appendDecimal(char* const buf, size_t pos, uint32_t value)
{
    char digits[10];
    size_t count = 0U;
    do
    {
        digits[count] = static_cast<char>('0' + static_cast<char>(value % 10U));
        value /= 10U;
        ++count;
    } while (value != 0U);
    while (count > 0U)
    {
        --count;
        buf[pos] = digits[count];
        ++pos;
    }
    return pos;
}
} // namespace detail

/**
 * Text of the loss line, "... <lost> log entries lost (total <total>)\n" ("entry" for one), plain ASCII without
 * timestamp or colours: it is not a log entry, it marks the place where entries are missing.
 * \return number of characters written to \p buf (no terminating zero)
 */
inline size_t formatLossLine(char (&buf)[LOSS_LINE_MAX], uint32_t const lost, uint32_t const total)
{
    size_t pos = detail::appendText(buf, 0U, "... ");
    pos        = detail::appendDecimal(buf, pos, lost);
    pos        = detail::appendText(buf, pos, (lost == 1U) ? " log entry lost (total " : " log entries lost (total ");
    pos        = detail::appendDecimal(buf, pos, total);
    pos        = detail::appendText(buf, pos, ")\n");
    return pos;
}

} // namespace logger
