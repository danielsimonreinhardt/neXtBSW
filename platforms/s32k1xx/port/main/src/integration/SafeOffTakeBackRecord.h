#pragma once

/* SYS-196 (safety review SYS-194 2026-10-03, decision SYS194-4 b): the take-backs of the hold of the commanded level
 * (SafeOffCommandHold.h, SYS-151) as a DTC of the fault memory, so a foreign active level of a safe_off output is
 * still on record after the reset (NvM blocks DemEvents/DemEntries, NVM-03). SYS-194 reports every take-back to the
 * safe supervisor (DEVICE_REPORT, S3) - a record in RAM that a reset loses.
 *
 * This record sits on the report of the hold: the integration calls onReport() with every report the hold hands over
 * (Report of SafeOffCommandHold, the first take-back in the poll that found it, then at most once per
 * REPORT_INTERVAL_POLLS (1 s) and output - the folding of SYS-194). Take-backs of that output not recorded yet are one
 * occurrence (Occurred: the integration counts it for dem::OccurrenceReport, BSW event safety.safe_off_take_back);
 * a report the safe supervisor's box refused and the hold hands over again in the next poll is no new occurrence.
 * So the DTC fails at most once per second and output, never for a level the driver commands (the hold takes nothing
 * back then, counter-check in the host test), and independently of whether the box accepted the report.
 *
 * Snapshot of the DTC: the output of the last occurrence (its console number) and its take-backs, with the total of
 * the hold (bswstatus::rte::readSafeOffStatus in Integration.cpp, identifier 0x0140 of the demo). Single words written
 * by the hold's task and read by the fault memory and the diagnosis: a reading between two writes may show the output
 * of one occurrence with the count of the next one - one count apart, never a torn word.
 *
 * Recording only (QM): it reads the counters of the hold and never touches an output; the reaction was the
 * take-back. Same task as the hold (TASK_BSP). Hardware independent; host test
 * test/host/SafeOffCommandHoldHostTest.cpp (testFaultMemoryRecord). */

#include <cstdint>

namespace integration
{

class SafeOffTakeBackRecord
{
public:
    using Occurred = void (*)(); // one occurrence for the fault memory

    /** Output number while nothing was recorded since the start. */
    static constexpr uint8_t NO_OUTPUT = 0xFFU;

    /**
     * \param count     safe_off outputs of the hold
     * \param takeBacks the hold's take-backs per output (SafeOffCommandHold perOutput), \p count words
     * \param recorded  \p count words owned by the caller: per output the take-backs covered by an occurrence
     * \param occurred  called once per occurrence; nullptr: only recorded here
     */
    SafeOffTakeBackRecord(
        uint8_t const count, uint32_t const* const takeBacks, uint32_t* const recorded, Occurred const occurred)
    : _takeBacks(takeBacks), _recorded(recorded), _occurred(occurred), _count(count)
    {}

    SafeOffTakeBackRecord(SafeOffTakeBackRecord const&)            = delete;
    SafeOffTakeBackRecord& operator=(SafeOffTakeBackRecord const&) = delete;

    /** With every report of the hold for safe_off output \p index, whose console number is \p output. */
    void onReport(uint8_t const index, uint8_t const output)
    {
        if ((index >= _count) || (_takeBacks == nullptr) || (_recorded == nullptr))
        {
            return;
        }
        uint32_t const now = _takeBacks[index];
        if (now == _recorded[index])
        {
            return; // the same take-backs handed over again (the box refused them): no new occurrence
        }
        _recorded[index] = now;
        _lastIndex       = index;
        _lastOutput      = output;
        ++_occurrences;
        if (_occurred != nullptr)
        {
            _occurred();
        }
    }

    /** Console number of the output of the last occurrence, NO_OUTPUT before the first one. */
    uint8_t lastOutput() const { return _lastOutput; }

    /** Take-backs of the output of the last occurrence since the start (0 before the first one). */
    uint32_t lastOutputTakeBacks() const
    {
        return ((_occurrences != 0U) && (_takeBacks != nullptr)) ? _takeBacks[_lastIndex] : 0U;
    }

    /** Occurrences since the start. */
    uint32_t occurrences() const { return _occurrences; }

private:
    uint32_t const* _takeBacks;
    uint32_t* _recorded;
    Occurred _occurred;
    uint32_t _occurrences = 0U;
    uint8_t _count;
    uint8_t _lastIndex  = 0U;
    uint8_t _lastOutput = NO_OUTPUT;
};

} // namespace integration
