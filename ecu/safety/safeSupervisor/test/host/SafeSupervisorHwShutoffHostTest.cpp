/* SYS-130 (safety review SYS-86 H7): host test of the report of a hardware shut-off path to the safe supervisor -
 * safeSupervisor/HwShutoff.h with the real HwShutoff.cpp and the real SafeSupervisor.cpp built as for an image whose
 * family port reports (NX_SAFE_SUPERVISOR_HW_SHUTOFF 1), with fault injection into the record.
 *
 * What it shows:
 * - a report is logged and enters S1 **without a reset** (no 'R', no reset-loop mark, no limp home of the supervisor);
 * - only new bits react: the same context in every safety cycle does nothing, a further bit reacts once more, and
 *   the reader returns the bits reported so far (OR);
 * - a report without context (0) is malformed and taken as everything (fail-safe);
 * - the record in protected RAM with its copy: a mismatch reads as everything, and the next report writes it again;
 * - the return path of the register check (CHECK_REGISTER_ERROR with "ok") does not leave S1 while a report is
 *   latched, and leaves it as before while nothing is reported.
 *
 * SYS-135 (safety review SYS-128/SYS-130 C1, decision of the safety responsible 2026-09-30): the degraded reaction of
 * a path the project maps to SD. Own test profile (not the eval board): two safe-output groups - group 0 = entry 0,
 * the PWM group on timer 3 with the hardware threshold; group 1 = entries 1 and 2 - and two mappings as
 * tools/nxconfig/degraded.py derives them: HW_SHUTOFF of timer 3 -> group 0, DEVICE_FAULT of device 7 -> group 1. It
 * shows: a trip of timer 3 degrades group 0 only (its entries, no S1, no reset, no limp home, inside the interrupt
 * lock), once; the record of the degraded groups names HW_SHUTOFF and the context; findings of the path, a trip with
 * findings after the trip, an unmapped timer, a context with an undefined bit, a malformed report and an implausible
 * record keep S1 held; the register check may leave S1 after a degraded report (the group stays degraded and is held
 * in the safety cycle), not after an S1 report; the last group escalates to S1 without a reset; a reset without
 * power-on keeps the group degraded although the reset re-arms the path. Built with -DNX_TEST_HW_SHUTOFF_WITHOUT_SD
 * (test_hw_shutoff_mutations.py does that) it is the image of a project with a threshold and no mapping: the S1 tests
 * pass unchanged.
 *
 * The trace characters are the ones of SafeSupervisorHostTest.cpp: E log error, w log warning, S enter S1, L leave S1,
 * R reset, P console line, F console flush; D a degraded walk (entries in test_trace::walks).
 *
 * Build and run (zig c++, no hardware): tools/test -k safe-supervisor-hw-shutoff
 */

#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>

#ifndef NX_TEST_HW_SHUTOFF_WITHOUT_SD
// ---- SYS-135 test profile: what tools/nxconfig derives for two groups and two mappings ----
#define NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_GROUPS          2U
#define NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_GROUP_0_ENTRIES 0x1U // PWM group on timer 3 (hardware threshold)
#define NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_GROUP_1_ENTRIES 0x6U // another PWM group and its enable
#define NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPINGS        2U
// HW_SHUTOFF (0xFE) << 24 | timer 3 << 16 | NO_BUS << 8 | group 0
#define NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPING_0            0xFE03FF01U
#define NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPING_0_BUS_GROUPS 0x0U
// DEVICE_FAULT (0x13) << 24 | device 7 << 16 | NO_BUS << 8 | group 1
#define NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPING_1            0x1307FF02U
#define NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPING_1_BUS_GROUPS 0x0U
#endif

namespace test_trace
{
std::string trace;
std::vector<uint32_t> walks; // entry masks of the degraded walks, in order
int lockDepth = 0;           // the interrupt lock of the stub
bool unlockedEntry = false;  // S1 entered or a group degraded without the interrupt lock
bool inReport      = false;  // set by the test around hwShutoff::report()

void add(char const c)
{
    if ((c == 'S') && (lockDepth <= 0))
    {
        unlockedEntry = true;
    }
    trace.push_back(c);
}

void print(char const*, char const*) { add('P'); }

void degraded(uint32_t const entries)
{
    if (inReport && (lockDepth <= 0))
    {
        unlockedEntry = true;
    }
    walks.push_back(entries);
    add('D');
}
} // namespace test_trace

namespace test_hold
{
int left = 0;

bool holding()
{
    if (left <= 0)
    {
        return false;
    }
    --left;
    return true;
}
} // namespace test_hold

#define NX_SAFE_SUPERVISOR_HOLDING()              (::test_hold::holding())
#define NX_SAFE_SUPERVISOR_CONSOLE(format, event) (::test_trace::print(format, event))
#define NX_SAFE_SUPERVISOR_RESET_FLUSH()          (::test_trace::add('F'), true)
// what safeSupervisor/CMakeLists.txt defines for an image with a hardware shut-off path (family port S32K1: a project
// with a hardware threshold)
#define NX_SAFE_SUPERVISOR_HW_SHUTOFF 1

extern "C" void softwareSystemReset(void) { ::test_trace::add('R'); }

// the latch of the degraded groups is private; the SYS-135 tests degrade another group first (HwShutoff.cpp includes
// SafeSupervisor.h since SYS-135, so the switch comes before it)
#define private public
#include "../../src/safeSupervisor/HwShutoff.cpp"      // NOLINT: SYS-130, the unit under test
#include "../../src/safeSupervisor/SafeSupervisor.cpp" // NOLINT: the reaction around it
#undef private

namespace safety
{
SafetyEventMailbox rteTimingMailbox;
} // namespace safety

namespace
{
using ::safety::SafeSupervisor;
namespace hw = ::safety::hwShutoff;

int failures = 0;
int checks   = 0;

#define CHECK(cond)                                                       \
    do                                                                    \
    {                                                                     \
        ++checks;                                                         \
        if (!(cond))                                                      \
        {                                                                 \
            ++failures;                                                   \
            std::printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
        }                                                                 \
    } while (false)

::safety::resetloop::Record volatile loopRecord{};

/** A fresh supervisor (power-on start, out of limp home), nothing reported, empty trace. */
SafeSupervisor& fresh()
{
    ::safety::safeSupervisorConstructor.construct();
    auto& supervisor = SafeSupervisor::getInstance();
    ::safety::resetloop::Start start{};
    start.powerOn = true;
    (void)supervisor.startResetLoopLimit(loopRecord, start, 3U, 100U);
    supervisor.leaveLimpHome();
    hw::reported            = 0U; // zero-initialised .bss: nothing reported
    hw::reportedCopy        = 0U;
    hw::held                = 0U;
    hw::heldCopy            = 0U;
    ::test_trace::trace.clear();
    ::test_trace::walks.clear();
    ::test_hold::left = 2;
    return supervisor;
}

bool report(uint32_t const context)
{
    ::test_trace::inReport = true;
    bool const reacted     = hw::report(context);
    ::test_trace::inReport = false;
    return reacted;
}

uint32_t readBack(bool& plausible)
{
    uint32_t context = 0x12345678U;
    plausible        = hw::read(context);
    return context;
}

// FTM1 tripped, the family's context word (hwThreshold::reportContext: timer 1 in 31..24, FAULTF in bit 0) - a timer
// the SYS-135 profile does not map, so these tests see S1 held in both builds
constexpr uint32_t TRIPPED = 0x01000001U;
// the comparator found switched off (SYS-128 COMPARATOR_OFF in bits 23..8)
constexpr uint32_t PATH_OFF = 0x01000100U;

void testNothingReported()
{
    fresh();
    bool plausible        = false;
    CHECK(readBack(plausible) == 0U);
    CHECK(plausible);
    CHECK(!hw::latched());
    CHECK(::test_trace::trace.empty());
}

void testReportHoldsS1WithoutReset()
{
    auto& supervisor = fresh();
    CHECK(report(TRIPPED));
    CHECK(::test_trace::trace == "ES"); // log line, S1 - no reset, no console line of a reset
    CHECK(!supervisor.inLimpHomeState());
    bool plausible = false;
    CHECK(readBack(plausible) == TRIPPED);
    CHECK(plausible);
    CHECK(hw::latched());

    // every safety cycle reports the same context again: nothing new, nothing happens
    for (int i = 0; i < 5; ++i)
    {
        CHECK(!report(TRIPPED));
    }
    CHECK(::test_trace::trace == "ES");

    // a further finding reacts once more, the reader returns both
    CHECK(report(TRIPPED | PATH_OFF));
    CHECK(::test_trace::trace == "ESES");
    CHECK(readBack(plausible) == (TRIPPED | PATH_OFF));
    CHECK(!report(PATH_OFF)); // a subset is known
    CHECK(::test_trace::trace == "ESES");
    CHECK(::test_trace::trace.find('R') == std::string::npos);
    CHECK(!::test_trace::unlockedEntry); // S1 inside the interrupt lock, like handle()
    CHECK(::test_trace::lockDepth == 0); // and released again
}

void testMalformedReportIsEverything()
{
    fresh();
    CHECK(report(0U));
    CHECK(::test_trace::trace == "ES");
    bool plausible = false;
    CHECK(readBack(plausible) == hw::ALL);
    CHECK(plausible);
    CHECK(!report(TRIPPED)); // everything is reported already
}

void testCorruptedRecord()
{
    fresh();
    CHECK(report(TRIPPED));
    // a wild write into one copy: implausible, reads as everything
    hw::reportedCopy = 0U;
    bool plausible   = true;
    CHECK(readBack(plausible) == hw::ALL);
    CHECK(!plausible);
    CHECK(hw::latched());
    // the next report of the safety cycle reacts again and writes a plausible record of everything
    CHECK(report(TRIPPED));
    CHECK(::test_trace::trace == "ESES");
    CHECK(readBack(plausible) == hw::ALL);
    CHECK(plausible);
    // a wild write that clears one copy of an empty record is caught as well
    fresh();
    hw::reported = 0x00000100U;
    CHECK(hw::latched());
    // SYS-135: and one into the record of the S1 hold
    fresh();
    hw::held = 0x00000001U;
    CHECK(hw::latched());
    fresh();
    hw::heldCopy = 0x00000001U;
    CHECK(hw::latched());
}

void testRegisterCheckDoesNotLeave()
{
    // nothing reported: the register check enters and leaves S1 as before (SM-22)
    auto& supervisor = fresh();
    supervisor.checkRegisterMonitor.trigger(false);
    supervisor.checkRegisterMonitor.trigger(true);
    CHECK(::test_trace::trace == "ESEL");

    // reported: the return of the register check keeps S1
    auto& s = fresh();
    CHECK(report(PATH_OFF));
    s.checkRegisterMonitor.trigger(false);
    s.checkRegisterMonitor.trigger(true);
    CHECK(::test_trace::trace == "ESESE"); // report, register check enters, return refused (log only)
    CHECK(::test_trace::trace.find('L') == std::string::npos);

    // an implausible record keeps it as well (fail-safe)
    auto& t = fresh();
    hw::reported = 0xFFU;
    t.checkRegisterMonitor.trigger(false);
    t.checkRegisterMonitor.trigger(true);
    CHECK(::test_trace::trace.find('L') == std::string::npos);
}

#if NX_SAFE_SUPERVISOR_DEGRADED
// ---- SYS-135 ----
namespace degraded = ::safety::degraded;

// FTM3 tripped: the timer the profile maps to group 0
constexpr uint32_t TRIPPED_MAPPED = 0x03000001U;
// a finding of that path (SYS-128 COMPARATOR_OFF)
constexpr uint32_t PATH_OFF_MAPPED = 0x03000100U;

uint32_t groupsNow(SafeSupervisor const& supervisor)
{
    uint32_t groups = 0xFFFFFFFFU;
    (void)supervisor.degradedGroups(groups);
    return groups;
}

void testTripDegradesOnlyItsGroup()
{
    auto& supervisor = fresh();
    ::test_trace::unlockedEntry = false;
    CHECK(report(TRIPPED_MAPPED));
    CHECK(::test_trace::trace == "DEE"); // group walk, SD log line, HW_SHUTOFF log line - no S1, no reset
    CHECK(::test_trace::walks.size() == 1U);
    CHECK(!::test_trace::walks.empty() && (::test_trace::walks[0] == 0x1U)); // the entries of group 0 only
    CHECK(groupsNow(supervisor) == 0x1U);
    CHECK(!supervisor.inLimpHomeState());
    CHECK(!::test_trace::unlockedEntry); // inside the interrupt lock
    CHECK(::test_trace::lockDepth == 0);
    // the record of the degraded groups names the report and its context
    CHECK(degraded::isValid(::safety::degradedRecord, 0x3U));
    CHECK(::safety::degradedRecord.groups == 0x1U);
    CHECK(::safety::degradedRecord.event == degraded::REPORT_HW_SHUTOFF);
    CHECK(::safety::degradedRecord.context == TRIPPED_MAPPED);
    // the reader of the application sees the report, S1 is not held by it
    bool plausible = false;
    CHECK(readBack(plausible) == TRIPPED_MAPPED);
    CHECK(plausible);
    CHECK(!hw::latched());
    // every safety cycle reports the same context again: nothing happens
    for (int i = 0; i < 5; ++i)
    {
        CHECK(!report(TRIPPED_MAPPED));
    }
    CHECK(::test_trace::trace == "DEE");
    CHECK(::test_trace::trace.find('R') == std::string::npos);
}

void testFindingsKeepS1()
{
    // a finding of the mapped path: S1 held as before
    auto& supervisor = fresh();
    CHECK(report(PATH_OFF_MAPPED));
    CHECK(::test_trace::trace == "ES");
    CHECK(groupsNow(supervisor) == 0U);
    CHECK(hw::latched());
    // tripped together with a finding: S1 held
    fresh();
    CHECK(report(TRIPPED_MAPPED | PATH_OFF_MAPPED));
    CHECK(::test_trace::trace == "ES");
    CHECK(hw::latched());
    // tripped first (SD), then a finding: S1 held on top of the degraded group
    auto& s = fresh();
    CHECK(report(TRIPPED_MAPPED));
    CHECK(report(TRIPPED_MAPPED | PATH_OFF_MAPPED));
    CHECK(::test_trace::trace == "DEEES");
    CHECK(groupsNow(s) == 0x1U);
    CHECK(hw::latched());
    // a context with a bit the family does not define (bits 7..1): not "tripped and nothing else"
    fresh();
    CHECK(report(0x03000003U));
    CHECK(::test_trace::trace == "ES");
    CHECK(::test_trace::trace.find('R') == std::string::npos);
}

void testUnmappedOrMalformedKeepsS1()
{
    // a timer without a mapping (TRIPPED is timer 1): S1 held, nothing degraded
    auto& supervisor = fresh();
    CHECK(report(TRIPPED));
    CHECK(::test_trace::trace == "ES");
    CHECK(groupsNow(supervisor) == 0U);
    // timer 0, whose context has no timer bits: unmapped as well
    fresh();
    CHECK(report(0x00000001U));
    CHECK(::test_trace::trace == "ES");
    // no context: everything, S1 held
    fresh();
    CHECK(report(0U));
    CHECK(::test_trace::trace == "ES");
    CHECK(hw::latched());
}

void testImplausibleRecordKeepsS1()
{
    auto& supervisor = fresh();
    CHECK(report(TRIPPED_MAPPED));
    hw::reportedCopy = 0U; // wild write
    CHECK(hw::latched());
    CHECK(report(TRIPPED_MAPPED));
    CHECK(::test_trace::trace == "DEEES"); // the second report: S1 held, not a second SD
    CHECK(groupsNow(supervisor) == 0x1U);
    bool plausible = false;
    CHECK(readBack(plausible) == hw::ALL);
    CHECK(plausible);
    CHECK(hw::latched());
}

void testRegisterCheckLeavesAfterSD()
{
    auto& supervisor = fresh();
    CHECK(report(TRIPPED_MAPPED));
    supervisor.checkRegisterMonitor.trigger(false);
    supervisor.checkRegisterMonitor.trigger(true);
    CHECK(::test_trace::trace == "DEEESEL"); // SD, register check enters S1 and leaves it again
    // the group stays degraded and is held in the next safety cycle
    CHECK(groupsNow(supervisor) == 0x1U);
    ::test_trace::walks.clear();
    supervisor.deliverDeferredEvents();
    CHECK((::test_trace::walks.size() == 1U) && (::test_trace::walks[0] == 0x1U));
}

void testLastGroupEscalatesToS1WithoutReset()
{
    auto& supervisor = fresh();
    supervisor.enterDegraded(0x2U, 0x13U, 0U); // group 1 degraded before (DEVICE_FAULT of device 7)
    ::test_trace::trace.clear();
    CHECK(report(TRIPPED_MAPPED));
    CHECK(groupsNow(supervisor) == 0x3U);
    CHECK(::test_trace::trace.find('S') != std::string::npos); // S1 - every group degraded
    CHECK(::test_trace::trace.find('R') == std::string::npos); // without a reset (review SYS-117 R2)
    CHECK(!supervisor.inLimpHomeState());
    CHECK(::safety::degradedRecord.event == degraded::REPORT_HW_SHUTOFF);
}

void testGroupStaysDegradedOverReset()
{
    auto& supervisor = fresh();
    CHECK(report(TRIPPED_MAPPED));
    // another event resets the ECU: the reset re-arms the hardware path, the record keeps the group degraded until
    // the power cycle (decision B4 (a)); the report record of HwShutoff.cpp starts empty (.bss)
    ::safety::safeSupervisorConstructor.construct();
    auto& again = SafeSupervisor::getInstance();
    ::safety::resetloop::Start start{};
    start.powerOn = false;
    ::test_trace::walks.clear();
    (void)again.startResetLoopLimit(loopRecord, start, 3U, 100U);
    CHECK(groupsNow(again) == 0x1U);
    CHECK((::test_trace::walks.size() == 1U) && (::test_trace::walks[0] == 0x1U));
    (void)supervisor;
    // power-on: the one way back
    fresh();
    CHECK(groupsNow(SafeSupervisor::getInstance()) == 0U);
}
#endif
} // namespace

int main()
{
    testNothingReported();
    testReportHoldsS1WithoutReset();
    testMalformedReportIsEverything();
    testCorruptedRecord();
    testRegisterCheckDoesNotLeave();
#if NX_SAFE_SUPERVISOR_DEGRADED
    testTripDegradesOnlyItsGroup();
    testFindingsKeepS1();
    testUnmappedOrMalformedKeepsS1();
    testImplausibleRecordKeepsS1();
    testRegisterCheckLeavesAfterSD();
    testLastGroupEscalatesToS1WithoutReset();
    testGroupStaysDegradedOverReset();
    std::printf("SafeSupervisorHwShutoffHostTest (with SD): %d checks, %d failures\n", checks, failures);
#else
    std::printf("SafeSupervisorHwShutoffHostTest (without SD): %d checks, %d failures\n", checks, failures);
#endif
    return (failures == 0) ? 0 : 1;
}
