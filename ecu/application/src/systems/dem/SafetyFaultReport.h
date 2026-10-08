// NVM-08: a logging reporter of the safe supervisor for the fault memory - only recording, the reaction stays with the
// supervisor (SafeSupervisor::handle), and nothing here writes a byte of it.
//
// What it reads is the existing read-only interface of the supervisor that the RTE already uses: the safe-output
// groups in the degraded reaction SD (SYS-117, safety::degraded::rte::readGroups, DegradedRte.cpp, any task). SD is
// the one reaction of the supervisor that keeps the ECU running and is entered on a device fault (DEVICE_FAULT with a
// degraded mapping of the project), so it is the one that can be recorded while it lasts:
//   a group degraded, or the supervisor's latch implausible (it then reads every group, fail-safe)   FAILED
//   no group degraded                                                                                 PASSED
// BSW event "safety.device_fault" (tools/nxconfig/bswdtc.py: +0x05nn, nn = SafeSupervisor::Event::DEVICE_FAULT). The
// degraded groups stay until the next power cycle and are applied again after a reset (SYS-117/SYS-123), so the DTC
// is failed again at every start in between. The cause of a safety reset (every other reaction resets at once, before
// the fault memory could store anything) is reported after the next start by SYS-113.
//
// Family and board neutral; host test: tools/rtegen/test_rtegen_dem_bsw_sources.py (SIL with the generated RTE).
// SYS-136 adds the hardware shut-off path below (BSW event safety.hw_shutoff, host test
// tools/rtegen/test_rtegen_safety_hw_shutoff.py).

#pragma once

#include <dem/EventReporter.h>

#include <cstdint>

namespace safety
{
namespace faultmemory
{

/** BSW event name of the degraded reaction after a device fault (diag.yaml dtcs[].event.bsw). */
constexpr char const DEVICE_FAULT_EVENT[] = "safety.device_fault";

/** Level of the degraded reaction: \p plausible and \p groups as safety::degraded::rte::readGroups() returns them. */
constexpr ::dem::Result degradedResult(bool const plausible, uint32_t const groups)
{
    return (!plausible || (groups != 0U)) ? ::dem::Result::FAILED : ::dem::Result::PASSED;
}

/** Reports the degraded reaction; \p read: bool(uint32_t& groups), the supervisor's reader (or a test double). */
template<class Read>
void reportDegraded(::dem::EventReporter& reporter, Read const& read, ::dem::IFaultMemory* const memory)
{
    uint32_t groups      = 0U;
    bool const plausible = read(groups);
    reporter.update(degradedResult(plausible, groups), false, memory);
}

/* SYS-136 (safety review SYS-128/SYS-130 C7): the hardware shut-off path of a PWM group - a path that switched the
 * group off without the core (S32K1: comparator -> trigger mux -> fault input of the timer, SYS-86) and that the
 * family port reported to the safe supervisor (safety::hwShutoff::report, SYS-130; the reaction S1 held until reset
 * is the supervisor's). Read through safety::hwShutoff::read (safeSupervisor/HwShutoff.h, any task):
 *   anything reported, or the supervisor's record implausible (it then reads every bit, fail-safe)   FAILED
 *   nothing reported                                                                                  PASSED
 * BSW event "safety.hw_shutoff" (tools/nxconfig/bswdtc.py: +0x05FE, a fixed sub-type of the safety block - the path
 * is no SafeSupervisor::Event). The report lasts until the next reset (the hardware path latches until reset, the
 * record lies in the supervisor's RAM), so the DTC stays failed for the rest of the operation cycle; the snapshot
 * data of the DTC come from the project's description, the context word itself is in the log line of the report
 * and in the RTE signal safety.hw_shutoff. */

/** BSW event name of the hardware shut-off path (diag.yaml dtcs[].event.bsw). */
constexpr char const HW_SHUTOFF_EVENT[] = "safety.hw_shutoff";

/** Level of the hardware shut-off path: \p plausible and \p context as safety::hwShutoff::read() returns them. */
constexpr ::dem::Result hwShutoffResult(bool const plausible, uint32_t const context)
{
    return (!plausible || (context != 0U)) ? ::dem::Result::FAILED : ::dem::Result::PASSED;
}

/** Reports the hardware shut-off path; \p read: bool(uint32_t& context), the supervisor's reader (or a test double). */
template<class Read>
void reportHwShutoff(::dem::EventReporter& reporter, Read const& read, ::dem::IFaultMemory* const memory)
{
    uint32_t context     = 0U;
    bool const plausible = read(context);
    reporter.update(hwShutoffResult(plausible, context), false, memory);
}

} // namespace faultmemory
} // namespace safety
