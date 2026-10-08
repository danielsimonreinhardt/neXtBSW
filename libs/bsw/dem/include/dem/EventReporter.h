// COM-46/NVM-08: one BSW monitor on its way to the fault memory - the reporting side of dem::IFaultMemory.
//
// A BSW monitor (CAN bus-off per bus, the supply, the safe supervisor) knows a level: its test fails now, passes now,
// or it cannot tell. The fault memory takes a reported level over in its next cycle and samples it every cycle until
// the next report (dem::Manager::report, "the reported level stays"). EventReporter does the rest for the monitor:
//   - it finds the DTC of its BSW event name (diag.yaml dtcs[].event.bsw) once a fault memory is registered
//     (dem::registry.memory) - a monitor that starts before the fault memory only keeps its level;
//   - it reports the level at every call that has one (COM-48), not only when it changes - see below;
//   - a failure the monitor saw between two calls (e.g. a bus-off that was already over) is reported as FAILED right
//     before the level, so the fault memory samples it once (dem::Manager::takeLatchLocked);
//   - without a DTC of its name (the project describes none) it does nothing after the first lookup.
// Only reporting: a reaction stays with the monitor (IFaultMemory.h). One reporter per event, updated from one
// context (the monitor's task); the fault memory's report() itself may be called from any context.
//
// COM-48, why every call and not only a change: the fault memory discards a report while ControlDTCSetting is off
// (COM-43, dem::Manager::report) and keeps the level reported before. A reporter that reported only changes left the
// level of before 85 off in place after 85 on until the monitor's level changed again - a bus that went bus-off before
// 85 off and recovered meanwhile made the DTC fail again after 85 on, cycle by cycle, even after a clear (finding of
// COM-47; rte.deadline reports every cycle since COM-47 for the same reason, dem/DeadlineReport.h). Reporting the
// level at every call makes the fault memory hold the monitor's current level one call after any report it did not
// take over, whatever the reason, without an interface to ask whether it takes reports (IFaultMemory, its vtable and
// every project with a fault memory stay as they are). A failure that was over while 85 was off (missed FAILED) stays
// discarded, as COM-43 wants; a failure that lasts beyond 85 on is recorded by the next call. The cost is one
// report() - one lock of the fault memory - per call of a monitor with a DTC: once per supervision cycle of the CAN
// system per bus, once per cycle of the fault memory for the safe supervisor.
//
// Header only, board and family neutral; host tests: libs/bsw/canSystem/test/test_can_bus_off_report.py (reporter
// alone and bus-off), ecu/application/src/systems/dem/test/test_event_reporter_dtc_setting.py (SIL with 85 off/on).

#pragma once

#include "dem/IFaultMemory.h"

#include <cstdint>

namespace dem
{

class EventReporter
{
public:
    /** \p name: BSW event name of the monitor; must outlive the reporter (a string literal or a static buffer). */
    explicit constexpr EventReporter(char const* const name) : _name(name) {}

    /**
     * The monitor's level now: FAILED/PREFAILED (test fails), PASSED/PREPASSED (test passes), NONE (no result, e.g.
     * the bus is not open yet). \p failedSinceLast: the test failed at some point since the last call although the
     * level no longer says so. \p memory: the fault memory to report to (nullptr: none running yet).
     */
    void update(Result const level, bool const failedSinceLast, IFaultMemory* const memory)
    {
        _failedPending = _failedPending || failedSinceLast || isFailed(level);
        if (memory == nullptr)
        {
            return;
        }
        if (memory != _memory)
        {
            _memory   = memory;
            _dtc      = memory->findReportedEvent(_name);
            _reported = Result::NONE;
        }
        bool const missed = _failedPending && !isFailed(level) && !isFailed(_reported);
        _failedPending    = false;
        if (_dtc < 0)
        {
            return;
        }
        if (missed)
        {
            memory->report(static_cast<uint16_t>(_dtc), Result::FAILED);
            _reported = Result::FAILED;
        }
        if (level != Result::NONE) // COM-48: every call, a report discarded while 85 was off is made good
        {
            memory->report(static_cast<uint16_t>(_dtc), level);
            _reported = level;
        }
    }

    /** The same with the registered fault memory of the ECU (dem::registry.memory). */
    void update(Result const level, bool const failedSinceLast = false)
    {
        update(level, failedSinceLast, registry.memory);
    }

    char const* name() const { return _name; }

    /** Index of the DTC of the event, -1 while no fault memory was seen or it has none of this name. */
    int32_t dtc() const { return _dtc; }

    /** Last level reported to the fault memory (NONE: nothing reported yet). */
    Result reported() const { return _reported; }

private:
    static constexpr bool isFailed(Result const r) { return (r == Result::FAILED) || (r == Result::PREFAILED); }

    char const* _name;
    IFaultMemory* _memory = nullptr;
    int32_t _dtc          = -1;
    Result _reported      = Result::NONE;
    bool _failedPending   = false;
};

} // namespace dem
