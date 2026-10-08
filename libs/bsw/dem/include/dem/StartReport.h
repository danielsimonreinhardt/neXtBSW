// SYS-113: results a BSW source finds once per start, on their way to the fault memory.
//
// Some faults end the operation before the fault memory could record them - a safety reset (S1 + S2) restarts the
// ECU at once. What survives is a record of the family port (no-init RAM) that the next start reads before anything
// else runs. StartReport hands what that start found to the fault memory once the memory runs:
//   - one bit per source (setFailed(), once per start, before the first update()): 1 the source failed before this
//     start, 0 it did not - the check was made, so its DTC passes;
//   - update() every cycle of the caller (a task of the BSW): once a fault memory is registered it looks up the DTC
//     of every source name (diag.yaml dtcs[].event.bsw, IFaultMemory::findReportedEvent); a source without a DTC
//     is left out;
//   - a failed source is reported FAILED in every call until the fault memory shows that it took the failure over
//     (status bit testFailedThisOperationCycle) - whatever kept it from taking it so far (enable conditions,
//     debouncing time, 85 off), the failure is not lost; then PASSED once, so the DTC keeps its pending/confirmed
//     bits and its entry but does not stay testFailed, and a clear (14) in the same run removes it for good;
//   - a source that did not fail is reported PASSED once (test completed this operation cycle);
//   - optionally one source is the fallback of others (setFallback()): it fails when one of them failed but has no
//     DTC of its own in the project - so a failure never goes unrecorded just because its DTC was left out;
//   - a fault memory that is replaced (another instance registered) gets everything again.
// After the hand-over the report costs one comparison per call. Only recording: the reaction stays with the monitor
// that found the fault (here the safe supervisor and its reset). One instance per set of sources, updated from one
// context; report() of the fault memory itself may be called from any context.
//
// Header only, board and family neutral; host test libs/bsw/dem/test/test_start_report.py.

#pragma once

#include "dem/IFaultMemory.h"

#include <cstddef>
#include <cstdint>

namespace dem
{

class StartReport
{
public:
    /** Most sources of one report (bits of setFailed()). */
    static constexpr size_t MAX_SOURCES = 32U;

    static constexpr uint32_t bit(size_t const i) { return (i < MAX_SOURCES) ? (static_cast<uint32_t>(1U) << i) : 0U; }

    /** \p names: BSW event names of the sources, \p count of them (at most MAX_SOURCES); must outlive the report. */
    constexpr StartReport(char const* const* const names, size_t const count)
    : _names(names), _count((count < MAX_SOURCES) ? count : MAX_SOURCES)
    {}

    /** What this start found: bit i set = source i failed before it. Once per start, before the first update(). */
    void setFailed(uint32_t const failed) { _failed = failed; }

    /**
     * Source \p index fails as well when one of the sources in \p covers (bit i) failed and has no DTC in the fault
     * memory. Once, before the first update(); \p index must not be among \p covers.
     */
    void setFallback(size_t const index, uint32_t const covers)
    {
        _fallback = index;
        _covers   = covers & ~bit(index);
    }

    /** One cycle of the caller; \p memory: the fault memory (nullptr: none running yet). */
    void update(IFaultMemory* const memory)
    {
        if (memory == nullptr)
        {
            return;
        }
        if (memory != _memory)
        {
            _memory  = memory;
            _pending = 0U;
            _found   = 0U;
            for (size_t i = 0U; i < _count; ++i)
            {
                int32_t const dtc = memory->findReportedEvent(_names[i]);
                if ((dtc >= 0) && (dtc <= 0xFFFF))
                {
                    _dtc[i] = static_cast<uint16_t>(dtc);
                    _pending |= bit(i);
                    _found |= bit(i);
                }
            }
            _reported = _failed;
            if ((_fallback < _count) && ((_failed & _covers & ~_found) != 0U))
            {
                _reported |= bit(_fallback);
            }
        }
        for (size_t i = 0U; (i < _count) && (_pending != 0U); ++i)
        {
            if ((_pending & bit(i)) == 0U)
            {
                continue;
            }
            uint16_t const dtc = _dtc[i];
            if (((_reported & bit(i)) != 0U) && !failureTaken(*memory, dtc))
            {
                memory->report(dtc, Result::FAILED);
            }
            else
            {
                memory->report(dtc, Result::PASSED);
                _pending &= ~bit(i);
            }
        }
    }

    /** The same with the registered fault memory of the ECU (dem::registry.memory). */
    void update() { update(registry.memory); }

    uint32_t failed() const { return _failed; }

    /** What is reported as failed to the fault memory seen last: failed() and the fallback (bit i). */
    uint32_t reportedFailed() const { return _reported; }

    /** Sources still to be handed over (bit i), 0 once everything is reported. */
    uint32_t pending() const { return _pending; }

    /** Sources with a DTC in the fault memory seen last (bit i), 0 while none was seen. */
    uint32_t found() const { return _found; }

private:
    /** The fault memory recorded the failure in this operation cycle (it may have been cleared since). */
    static bool failureTaken(IFaultMemory const& memory, uint16_t const dtc)
    {
        DtcInfo info{};
        return memory.dtcInfo(dtc, info) && ((info.status & status::TEST_FAILED_THIS_OPERATION_CYCLE) != 0U);
    }

    char const* const* _names;
    size_t _count;
    IFaultMemory* _memory = nullptr;
    uint32_t _failed      = 0U;
    uint32_t _pending     = 0U;
    uint32_t _found       = 0U;
    uint32_t _reported    = 0U;
    size_t _fallback      = MAX_SOURCES;
    uint32_t _covers      = 0U;
    uint16_t _dtc[MAX_SOURCES]{};
};

} // namespace dem
