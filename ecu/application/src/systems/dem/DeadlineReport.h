// COM-47: the runtime supervision of the RTE (SYS-05) as the BSW event "rte.deadline" of the fault memory - a level
// that follows the deadlines, not a single report per violation.
//
// Before COM-47 RteSystem reported FAILED on the first violation of a measurement window and PASSED only when a new
// window started (and "rte reset" did not even do that). The fault memory keeps the level a BSW monitor reported and
// samples it every cycle (dem::Manager::report, "the reported level stays", a clear keeps it), so after one violation
// the DTC failed again in the first cycle after every clear (14 FF FF FF, "dem clear") until the next restart
// (board 2026-09-30, TOOL-131 test image: 0x2F right after 54, also after "rte reset"; 0x50 only after 11 01).
//
// Now every task cycle and every periodic runnable keeps whether its last check missed its deadline (written by the
// task that owns it, RteSystem.cpp), and once per cycle of the fault memory (its context) the level is
//   a violation since the last cycle (COM-45 counters moved) or a source whose last check missed   PREFAILED
//   otherwise, once at least one deadline was checked                                               PREPASSED
//   no deadline checked yet                                                                          NONE
// PREFAILED/PREPASSED leave the maturing to the debouncing of the DTC (diag.yaml dtcs[].debounce): with the counter
// 1/-1 of the standard diagnostics one missed deadline fails the DTC and one clean cycle of the fault memory passes
// it; a project that wants a longer clean time before PASSED configures it there. A missed deadline that is over
// before the fault memory samples is not lost (the counters moved). The safe supervisor still gets its event once per
// window (SYS-05); this is only the recording side (IFaultMemory.h).
//
// Compiled only when the project has a DTC of this event (NX_DEM_RTE_DEADLINE of the generated
// dem/demBswReporters.h), so a project without it builds exactly the code it built before. Family and board neutral;
// SIL test with the real RteSystem.cpp and dem::Manager: test/host/RteDeadlineDemHostTest.cpp.

#pragma once

#include <dem/IFaultMemory.h>

#include <cstdint>

namespace rtesupervision
{

/** BSW event name of the deadline supervision (diag.yaml dtcs[].event.bsw). */
constexpr char const DEADLINE_EVENT[] = "rte.deadline";

/** What the deadline supervision knows now (RteSystem.cpp readDeadlineState, single words of every task). */
struct DeadlineState
{
    bool checked;        // at least one deadline was checked since the start
    bool failing;        // a task cycle or a periodic runnable missed its deadline at its last check
    uint32_t violations; // violations since the start (COM-45, saturating)
};

/** The state of the supervision now; defined in RteSystem.cpp (with NX_DEM_RTE_DEADLINE). */
void readDeadlineState(DeadlineState& state);

/** Level of the event: \p newViolation, a violation since the last evaluation. */
constexpr ::dem::Result deadlineLevel(DeadlineState const& state, bool const newViolation)
{
    return (state.failing || newViolation) ? ::dem::Result::PREFAILED
                                           : (state.checked ? ::dem::Result::PREPASSED : ::dem::Result::NONE);
}

/**
 * Reporter of the event, updated once per cycle of the fault memory from its context. It reports the level in every
 * cycle, not only on a change (dem::EventReporter does the same since COM-48): a report the fault memory discarded while ControlDTCSetting was
 * off (COM-43) would otherwise leave the level of before in place until the deadlines change again - a DTC that
 * failed before 85 off and whose deadlines recovered meanwhile would fail again after 85 on, cycle by cycle. One
 * report per cycle of the fault memory costs one lock.
 */
class DeadlineReport
{
public:
    void update(DeadlineState const& state, ::dem::IFaultMemory* const memory)
    {
        if (memory == nullptr)
        {
            return; // counters are compared once a fault memory runs: a violation before it is still seen
        }
        if (memory != _memory)
        {
            _memory = memory;
            _dtc    = memory->findReportedEvent(DEADLINE_EVENT);
        }
        bool const newViolation = state.violations != _violations;
        _violations             = state.violations;
        ::dem::Result const level = deadlineLevel(state, newViolation);
        if ((_dtc >= 0) && (level != ::dem::Result::NONE))
        {
            memory->report(static_cast<uint16_t>(_dtc), level);
        }
    }

    /** Index of the DTC of rte.deadline, -1 while no fault memory was seen or it has none. */
    int32_t dtc() const { return _dtc; }

private:
    ::dem::IFaultMemory* _memory = nullptr;
    int32_t _dtc                 = -1;
    uint32_t _violations         = 0U;
};

} // namespace rtesupervision
