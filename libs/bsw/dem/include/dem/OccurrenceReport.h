// SYS-196: a BSW source whose faults are single occurrences at run time, on their way to the fault memory.
//
// Some monitors find a fault that is already gone when the fault memory could sample it: the hold of the commanded
// level of the safe_off outputs (SYS-151) takes a foreign active level back within one poll of 1 ms, so there is no
// level to report and nothing left at the next cycle of the fault memory. Such a monitor counts occurrences instead
// (occurred()), and OccurrenceReport hands every batch of them to the fault memory as one failure:
//   - update() every cycle of the caller (a task of the BSW): once a fault memory is registered it looks up the DTC of
//     the event name (diag.yaml dtcs[].event.bsw, IFaultMemory::findReportedEvent); without a DTC nothing is
//     reported (the occurrences are still counted);
//   - occurrences not handed over yet start a hand-over: FAILED in every call until the fault memory shows that it
//     took the failure over (status bits testFailed and testFailedThisOperationCycle) - whatever kept it from taking
//     it so far (enable conditions, debouncing time, 85 off, no cycle running), the failure is not lost; then PASSED
//     once, so the DTC keeps its pending/confirmed bits, its entry with the snapshot of the first confirmation and
//     its occurrence counter, but does not stay testFailed, and a clear (14) after the hand-over removes it for good;
//   - occurrences during a hand-over are folded into the next one (one more failure of the DTC, one more count of its
//     occurrence counter); several between two calls are one hand-over. The rate of the fault memory's failures is
//     therefore at most one per two cycles of the caller, and never higher than the rate of the monitor's own
//     reports (the hold reports at most once per second and output);
//   - a fault memory that is replaced (another instance registered) gets the hand-over in progress again.
// Only recording: the reaction stays with the monitor (here the take-back of the hold). One instance per event,
// occurred() and update() from one context; report() of the fault memory itself may be called from any context.
//
// Header only, board and family neutral; host test libs/bsw/dem/test/test_start_report.py
// (test/host/StartReportHostTest.cpp, with the real fault memory core).

#pragma once

#include "dem/IFaultMemory.h"

#include <cstdint>

namespace dem
{

class OccurrenceReport
{
public:
    /** \p name: BSW event name of the source (diag.yaml dtcs[].event.bsw); must outlive the report. */
    constexpr explicit OccurrenceReport(char const* const name) : _name(name) {}

    /** One occurrence of the fault; the next update() hands it over (with every other one not handed over yet). */
    void occurred() { ++_occurred; }

    /** One cycle of the caller; \p memory: the fault memory (nullptr: none running yet). */
    void update(IFaultMemory* const memory)
    {
        if (memory == nullptr)
        {
            return;
        }
        if (memory != _memory)
        {
            _memory           = memory;
            int32_t const dtc = memory->findReportedEvent(_name);
            _found            = (dtc >= 0) && (dtc <= 0xFFFF);
            _dtc              = _found ? static_cast<uint16_t>(dtc) : uint16_t{0U};
            // a hand-over in progress goes on with this memory: FAILED until it took the failure over
        }
        if (!_found)
        {
            return;
        }
        if (!_handingOver)
        {
            if (_occurred == _covered)
            {
                return;
            }
            _handingOver = true;
            _inFlight    = _occurred;
            memory->report(_dtc, Result::FAILED);
            return;
        }
        if (failureTaken(*memory, _dtc))
        {
            memory->report(_dtc, Result::PASSED);
            _covered     = _inFlight;
            _handingOver = false;
            ++_handOvers;
        }
        else
        {
            memory->report(_dtc, Result::FAILED);
        }
    }

    /** The same with the registered fault memory of the ECU (dem::registry.memory). */
    void update() { update(registry.memory); }

    /** Occurrences since the start (wrapping). */
    uint32_t occurrences() const { return _occurred; }

    /** Occurrences covered by completed hand-overs; the rest of occurrences() is still due. */
    uint32_t covered() const { return _covered; }

    /** Completed hand-overs (failures the fault memory took over, then PASSED). */
    uint32_t handOvers() const { return _handOvers; }

    /** The fault memory seen last has a DTC of the event. */
    bool found() const { return _found; }

    /** A failure is being handed over (FAILED reported, not taken over yet). */
    bool handingOver() const { return _handingOver; }

private:
    /** The fault memory took the failure over: testFailed now, and in this operation cycle. */
    static bool failureTaken(IFaultMemory const& memory, uint16_t const dtc)
    {
        DtcInfo info{};
        uint8_t const taken = static_cast<uint8_t>(status::TEST_FAILED | status::TEST_FAILED_THIS_OPERATION_CYCLE);
        return memory.dtcInfo(dtc, info) && ((info.status & taken) == taken);
    }

    char const* _name;
    IFaultMemory* _memory = nullptr;
    uint32_t _occurred    = 0U;
    uint32_t _covered     = 0U;
    uint32_t _inFlight    = 0U;
    uint32_t _handOvers   = 0U;
    uint16_t _dtc         = 0U;
    bool _found           = false;
    bool _handingOver     = false;
};

} // namespace dem
