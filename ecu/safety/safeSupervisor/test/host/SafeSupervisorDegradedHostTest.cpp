/* SYS-117: host test of the degraded reaction SD of the safe supervisor (SafeSupervisor.cpp, safeSupervisor/Degraded.h
 * and DegradedRte.cpp - the real code), with fault injection.
 *
 * Own test profile (not the eval board, not a real project): three safe-output groups over four table entries and four
 * mappings of DEVICE_FAULT - two devices on one SPI bus (both mapped, so a lost bus degrades both groups), one device on
 * a second SPI bus whose neighbour is not mapped (a lost bus stays fail-safe), and one device on no bus. The values are
 * what tools/nxconfig/degraded.py derives into integration/safeSupervisorConfiguration.h; they are defined here before
 * the unit under test, like the generated header would.
 *
 * What it shows (doc/safety/partner-and-degraded.md part B, decisions B1-B6):
 * - SD only for a mapped device, a well-formed word, a class other than IDENTITY and the configured bus; every other
 *   DEVICE_FAULT keeps its fail-safe reaction (S1 held resp. S1 + S2) exactly as before;
 * - the order outputs - latch - record - report, only the entries of the groups, no reset, no limp home, no S1;
 * - groups accumulate and never return in the power cycle (B6), the last group escalates to S1 without reset (B5);
 * - a lost bus degrades the groups of all its devices, or nothing (fail-safe) if one of its devices is not mapped;
 * - the groups are walked again in every safety cycle; leaving S1 releases none of them; a later fail-safe event keeps
 *   its reaction;
 * - the record in no-init RAM: power-on clears it (the one way back), any other start re-applies its groups before the
 *   BSP, an implausible record or latch reads as every group (fail-safe), a record that drifted is written again;
 * - the RTE reader reports the groups, and every group with "not plausible" when the latch is corrupted;
 * - the closed list: exactly DEVICE_FAULT is degradable, every MCU-internal event is not;
 * - SYS-138 (review K6): the degraded groups are read back in every safety cycle before their walk (their entries, not
 *   the group bits; not for S1, which SafeIo reads back), not confirmed is LOCK_REGISTER_ERROR (S1 + S2); the hold of S1
 *   reads back in every round before its walk and ends in S1 + S2 when S1 is not confirmed; the S1 + S2 sequence writes
 *   its console line only for a confirmed S1 (the family port's read-back is faked here, the real one is in safe-io).
 * The meaningfulness is checked by test_degraded_mutations.py (built-in faults in SafeSupervisor.cpp and Degraded.h, and
 * configuration headers that must not compile).
 *
 * Build and run (zig c++, no hardware):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -DPLATFORM_SUPPORT_IO -DPLATFORM_SUPPORT_WATCHDOG \
 *       -Iecu/safety/safeSupervisor/include -Iecu/safety/safeSupervisor/test/host/stubs \
 *       -isystem external/openbsw/libs/safety/safeMonitor/include \
 *       ecu/safety/safeSupervisor/test/host/SafeSupervisorDegradedHostTest.cpp -o build/host/safesupervisor_sd.exe
 *   build/host/safesupervisor_sd.exe
 */

#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>

// ---- test profile: what tools/nxconfig derives for three groups and four mappings ----
// groups: 0 = entry 0 (PWM group A), 1 = entry 1 (PWM group B), 2 = entries 2 and 3 (PWM group C and its enable)
#define NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_GROUPS         3U
#define NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_GROUP_0_ENTRIES 0x1U
#define NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_GROUP_1_ENTRIES 0x2U
#define NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_GROUP_2_ENTRIES 0xCU
// mappings: event DEVICE_FAULT (0x13) << 24 | instance << 16 | bus << 8 | groups
#ifndef NX_TEST_MAPPINGS_OVERRIDE
#define NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPINGS               4U
#define NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPING_0              0x13032001U // device 3, SPI 0 -> group 0
#define NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPING_0_BUS_GROUPS   0x3U        // devices 3, 4: both mapped
#define NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPING_1              0x13042002U // device 4, SPI 0 -> group 1
#define NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPING_1_BUS_GROUPS   0x3U
#define NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPING_2              0x13052104U // device 5, SPI 1 -> group 2
#define NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPING_2_BUS_GROUPS   0x0U        // device 6 on SPI 1 unmapped
#define NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPING_3              0x1307FF02U // device 7, no bus -> group 1
#define NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPING_3_BUS_GROUPS   0x0U
#endif

namespace test_trace
{
std::string trace;
std::vector<uint32_t> walks;          // entry masks of the degraded walks, in order
std::vector<uint32_t> latchAtWalk;    // the supervisor's groups when each walk ran (outputs before the latch)
int lockDepth = 0;
bool lockedAtWalk = true;             // every degraded walk ran with the interrupt lock taken (handle())
bool inHandle     = false;            // set by the test around handle() paths

void add(char const c) { trace.push_back(c); }

void print(char const*, char const*) { add('P'); }

void degraded(uint32_t entries); // defined below, reads the supervisor
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

extern "C" void softwareSystemReset(void) { ::test_trace::add('R'); }

/* SYS-138 (review K6): the supervisor reads back the safe state in the hold of S1, for the degraded groups and before
 * the reset of every S1 + S2 reaction (safeSupervisor/SafeStateReadback.h). The family port's read-back is faked here
 * (the real one: host test safe-io with SafeIo.cpp); it answers "confirmed" unless a test says otherwise, so every
 * trace of the tests before SYS-138 stays as it was. */
#define NX_SAFE_SUPERVISOR_READBACK 1

namespace test_readback
{
uint32_t s1Answer     = 0U; // what safeStateReadback() returns
uint32_t failEntries  = 0U; // entries degradedStateReadback() finds not confirmed
int s1Calls           = 0;
int failFromCall      = 0;  // > 0: safeStateReadback() finds entry 0 not confirmed from this call on
std::vector<uint32_t> asked; // entry masks degradedStateReadback() was asked for
size_t walksAtFirstAsk = 0U; // degraded walks before the first question of a test

void reset()
{
    s1Answer        = 0U;
    failEntries     = 0U;
    s1Calls         = 0;
    failFromCall    = 0;
    walksAtFirstAsk = 0U;
    asked.clear();
}
} // namespace test_readback

// the latch of the supervisor is private; the test injects faults into it
#define private public
#include "../../src/safeSupervisor/SafeSupervisor.cpp" // NOLINT: the unit under test
#undef private

namespace safety
{
uint32_t safeStateReadback()
{
    ++::test_readback::s1Calls;
    if ((::test_readback::failFromCall > 0) && (::test_readback::s1Calls >= ::test_readback::failFromCall))
    {
        return 0x07000000U;
    }
    return ::test_readback::s1Answer;
}

uint32_t degradedStateReadback(uint32_t const entries)
{
    if (::test_readback::asked.empty())
    {
        ::test_readback::walksAtFirstAsk = ::test_trace::walks.size();
    }
    ::test_readback::asked.push_back(entries);
    uint32_t const failed = entries & ::test_readback::failEntries;
    for (uint32_t index = 0U; index < 32U; ++index)
    {
        if ((failed & (1U << index)) != 0U)
        {
            return 0x07000000U | index; // class SAFE_OUTPUT, entry
        }
    }
    return 0U;
}
} // namespace safety
#include "../../src/safeSupervisor/DegradedRte.cpp" // NOLINT: SYS-117, the RTE reader of the degraded groups

namespace safety
{
SafetyEventMailbox rteTimingMailbox;
} // namespace safety

namespace test_trace
{
void degraded(uint32_t const entries)
{
    add('D');
    walks.push_back(entries);
    uint32_t groups = 0U;
    (void)::safety::degraded::rte::readGroups(groups);
    latchAtWalk.push_back(groups);
    if (inHandle && (lockDepth <= 0))
    {
        lockedAtWalk = false;
    }
}
} // namespace test_trace

namespace
{
using ::safety::SafeSupervisor;
using Event = SafeSupervisor::Event;
namespace device   = ::safety::device;
namespace degraded = ::safety::degraded;

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

static_assert(static_cast<uint32_t>(Event::DEVICE_FAULT) == 0x13U, "test profile: event id of DEVICE_FAULT");

constexpr uint8_t SPI0 = device::busId(device::BusKind::SPI, 0U);
constexpr uint8_t SPI1 = device::busId(device::BusKind::SPI, 1U);
static_assert((SPI0 == 0x20U) && (SPI1 == 0x21U), "test profile: bus ids");

constexpr uint32_t fault(uint8_t const bus, uint8_t const instance, device::FaultClass const cls)
{
    return device::context(bus, instance, cls, 0x5AU);
}

/** A fresh supervisor after a power-on start (record cleared), out of limp home, empty traces. */
SafeSupervisor& fresh()
{
    ::safety::safeSupervisorConstructor.construct();
    auto& supervisor = SafeSupervisor::getInstance();
    static ::safety::resetloop::Record volatile loop{};
    ::safety::resetloop::Start start{};
    start.powerOn = true;
    ::safety::degradedRecord.groups = 0xDEADBEEFU; // random content after power-on
    (void)supervisor.startResetLoopLimit(loop, start, 3U, 100U);
    supervisor.leaveLimpHome();
    (void)::safety::rteTimingMailbox.drain([](uint32_t) {});
    ::test_trace::trace.clear();
    ::test_trace::walks.clear();
    ::test_trace::latchAtWalk.clear();
    ::test_trace::lockDepth    = 0;
    ::test_trace::lockedAtWalk = true;
    ::test_trace::inHandle     = false;
    ::test_hold::left          = 2;
    ::test_readback::reset();
    return supervisor;
}

uint32_t groupsOf(SafeSupervisor const& supervisor)
{
    uint32_t groups = 0xFFU;
    (void)supervisor.degradedGroups(groups);
    return groups;
}

void deviceFault(SafeSupervisor& supervisor, uint32_t const word)
{
    ::test_trace::inHandle = true;
    supervisor.deviceFaultMonitor.trigger(word);
    ::test_trace::inHandle = false;
}

/** Two communication faults of devices on `bus` within the window: the supervisor regards the bus as lost (F5). */
void loseBus(SafeSupervisor& supervisor, uint8_t const bus, uint8_t const a, uint8_t const b)
{
    supervisor.deviceReportMonitor.trigger(fault(bus, a, device::FaultClass::COMMUNICATION));
    supervisor.deviceReportMonitor.trigger(fault(bus, b, device::FaultClass::COMMUNICATION));
}

std::string const RESET = "ESFPFR"; // log, S1, flush, line, flush, reset (SYS-59)

// ------------------------------------------------------------------------------------------------ closed list
void testOnlyTheClosedListIsDegradable()
{
    for (uint32_t id = 0U; id <= static_cast<uint32_t>(Event::ETL_ASSERT); ++id)
    {
        auto const event = static_cast<Event>(id);
        CHECK(SafeSupervisor::isDegradableEvent(event) == (event == Event::DEVICE_FAULT));
    }
    // the MCU-internal faults by name (concept section 4.2, "fail-safe only")
    Event const failSafe[] = {Event::SAFETY_MANAGER_SEQUENCE_DEVIATION, Event::WATCHDOG_STARTUP_CHECK_FAILURE,
                              Event::SAFE_WATCHDOG_SEQUENCE_DEVIATION,  Event::SAFE_WATCHDOG_CONFIGURATION_ERROR,
                              Event::SAFE_WATCHDOG_SERVICE_DEVIATION,   Event::MPU_UNLOCKED_ON_SAFETY_MANAGER_ENTRY,
                              Event::MPU_LOCKED_ON_SAFETY_MANAGER_EXIT, Event::INTEGRITY_EVENT_DMA_ECC_ERROR,
                              Event::ROM_CHECK_FAILURE,                 Event::MPU_NOT_ENABLED,
                              Event::MPU_CONFIGURATION_ERROR,           Event::ADC_REFERENCE_ERROR,
                              Event::INTERNAL_SUPPLIES_ERROR,           Event::CHECK_REGISTER_ERROR,
                              Event::LOCK_REGISTER_ERROR,               Event::DEADMAN_CONFIGURATION_ERROR,
                              Event::SAFETY_CYCLE_PERIOD_VIOLATION,     Event::RESET_LOOP_LIMIT,
                              Event::STACK_OVERFLOW,                    Event::RTOS_ASSERT,
                              Event::ETL_ASSERT,                        Event::RTE_TIMING_VIOLATION,
                              Event::DEVICE_REPORT};
    for (Event const event : failSafe)
    {
        CHECK(!SafeSupervisor::isDegradableEvent(event));
        CHECK(SafeSupervisor::degradedGroupsOf(event, 3U, SPI0, false) == 0U);
    }
    CHECK(SafeSupervisor::degradedGroupsOf(Event::DEVICE_FAULT, 3U, SPI0, false) == 0x1U);
}

// ------------------------------------------------------------------------------------------ fail-safe as before
void testUnmappedDeviceFaultKeepsItsReaction()
{
    auto& supervisor = fresh();
    deviceFault(supervisor, fault(SPI1, 6U, device::FaultClass::OUTPUT)); // device 6: not mapped
    CHECK(::test_trace::trace == RESET);
    CHECK(::test_trace::walks.empty());
    CHECK(groupsOf(supervisor) == 0U);
    CHECK(supervisor.inLimpHomeState());
}

void testIdentityAndMalformedStayFailSafe()
{
    auto& supervisor = fresh();
    deviceFault(supervisor, fault(SPI0, 3U, device::FaultClass::IDENTITY)); // mapped device, but a wrong part
    CHECK(::test_trace::trace == "ESPSWSW");                                 // S1 held (hold rounds 2)
    CHECK(::test_trace::walks.empty());
    CHECK(groupsOf(supervisor) == 0U);

    auto& again = fresh();
    uint32_t const malformed = device::context(SPI0, 3U, static_cast<device::FaultClass>(0xEEU));
    deviceFault(again, malformed);
    CHECK(::test_trace::trace == RESET);
    CHECK(::test_trace::walks.empty());

    auto& wrongBus = fresh(); // the word names another bus than the configured one of device 3: inconsistent
    deviceFault(wrongBus, fault(SPI1, 3U, device::FaultClass::OUTPUT));
    CHECK(::test_trace::trace == RESET);
    CHECK(groupsOf(wrongBus) == 0U);
}

// ----------------------------------------------------------------------------------------------- SD itself
void testMappedDeviceFaultDegradesItsGroup()
{
    auto& supervisor    = fresh();
    uint32_t const word = fault(SPI0, 3U, device::FaultClass::OUTPUT);
    deviceFault(supervisor, word);
    CHECK(::test_trace::trace == "EDE");          // log of the fault, walk of the group, log of SD - no S, no R
    CHECK(::test_trace::walks == std::vector<uint32_t>{0x1U});
    CHECK(::test_trace::latchAtWalk == std::vector<uint32_t>{0x0U}); // the outputs before the latch
    CHECK(::test_trace::lockedAtWalk);
    CHECK(groupsOf(supervisor) == 0x1U);
    CHECK(!supervisor.inLimpHomeState());
    CHECK(degraded::isValid(::safety::degradedRecord, 0x7U));
    CHECK(::safety::degradedRecord.groups == 0x1U);
    CHECK(::safety::degradedRecord.event == 0x13U);
    CHECK(::safety::degradedRecord.context == word);
    uint32_t rte = 0U;
    CHECK(::safety::degraded::rte::readGroups(rte));
    CHECK(rte == 0x1U);
    // a group of two table entries (a PWM group and its enable), and two groups together without the third
    auto& pair = fresh();
    deviceFault(pair, fault(SPI1, 5U, device::FaultClass::OUTPUT));
    CHECK(::test_trace::walks == std::vector<uint32_t>{0xCU});
    deviceFault(pair, fault(SPI0, 3U, device::FaultClass::OUTPUT));
    CHECK(::test_trace::walks == (std::vector<uint32_t>{0xCU, 0xDU}));
    CHECK(groupsOf(pair) == 0x5U);
    // every class other than IDENTITY is degradable
    for (uint8_t cls = 1U; cls <= device::FAULT_CLASS_LAST; ++cls)
    {
        if (cls == static_cast<uint8_t>(device::FaultClass::IDENTITY))
        {
            continue;
        }
        auto& other = fresh();
        deviceFault(other, fault(device::NO_BUS, 7U, static_cast<device::FaultClass>(cls)));
        CHECK(::test_trace::trace == "EDE");
        CHECK(groupsOf(other) == 0x2U);
    }
}

void testGroupsAccumulateAndEscalate()
{
    auto& supervisor = fresh();
    deviceFault(supervisor, fault(SPI0, 3U, device::FaultClass::OUTPUT));
    deviceFault(supervisor, fault(SPI0, 4U, device::FaultClass::SUPPLY));
    CHECK(::test_trace::walks == (std::vector<uint32_t>{0x1U, 0x3U})); // the second walk keeps the first group
    CHECK(groupsOf(supervisor) == 0x3U);
    CHECK(::test_trace::trace.find('S') == std::string::npos);
    // a second fault of the same device changes nothing but the record's context
    deviceFault(supervisor, fault(SPI0, 3U, device::FaultClass::TEMPERATURE));
    CHECK(groupsOf(supervisor) == 0x3U);
    // the last group: S1 without a reset (B5)
    ::test_trace::trace.clear();
    deviceFault(supervisor, fault(SPI1, 5U, device::FaultClass::OUTPUT));
    CHECK(::test_trace::trace == "ESEE"); // log, S1 (all outputs), log of SD, log of the escalation
    CHECK(::test_trace::trace.find('R') == std::string::npos);
    CHECK(groupsOf(supervisor) == 0x7U);
    CHECK(::safety::degradedRecord.groups == 0x7U);
    CHECK(!supervisor.inLimpHomeState());
}

void testLostBusDegradesAllItsDevicesOrNothing()
{
    auto& supervisor = fresh();
    loseBus(supervisor, SPI0, 3U, 4U);
    CHECK(supervisor.deviceBusLost(SPI0));
    ::test_trace::trace.clear();
    deviceFault(supervisor, fault(SPI0, 3U, device::FaultClass::COMMUNICATION));
    CHECK(::test_trace::trace == "EDE");
    CHECK(groupsOf(supervisor) == 0x3U); // both devices of the lost bus

    auto& other = fresh(); // SPI 1: device 6 is not mapped, so a lost SPI 1 is fail-safe
    loseBus(other, SPI1, 5U, 6U);
    CHECK(other.deviceBusLost(SPI1));
    ::test_trace::trace.clear();
    deviceFault(other, fault(SPI1, 5U, device::FaultClass::COMMUNICATION));
    CHECK(::test_trace::trace == RESET);
    CHECK(groupsOf(other) == 0U);
}

// ---------------------------------------------------------------------------------------------- hold and return
void testGroupsAreHeldEveryCycleAndNeverReturn()
{
    auto& supervisor = fresh();
    supervisor.deliverDeferredEvents();
    CHECK(::test_trace::walks.empty()); // nothing degraded: nothing walked
    deviceFault(supervisor, fault(SPI0, 4U, device::FaultClass::OUTPUT));
    ::test_trace::trace.clear();
    ::test_trace::walks.clear();
    for (int i = 0; i < 3; ++i)
    {
        supervisor.deliverDeferredEvents();
    }
    CHECK(::test_trace::walks == (std::vector<uint32_t>{0x2U, 0x2U, 0x2U}));
    // leaving S1 (SM-22 return path) releases no degraded group
    supervisor.checkRegisterMonitor.trigger(false);
    supervisor.checkRegisterMonitor.trigger(true);
    CHECK(groupsOf(supervisor) == 0x2U);
    ::test_trace::walks.clear();
    supervisor.deliverDeferredEvents();
    CHECK(::test_trace::walks == std::vector<uint32_t>{0x2U});
    // escalated: S1 again in every cycle, no subset walk needed
    deviceFault(supervisor, fault(SPI0, 3U, device::FaultClass::OUTPUT));
    deviceFault(supervisor, fault(SPI1, 5U, device::FaultClass::OUTPUT));
    ::test_trace::trace.clear();
    supervisor.checkRegisterMonitor.trigger(false);
    supervisor.checkRegisterMonitor.trigger(true); // leaves S1 ...
    supervisor.deliverDeferredEvents();            // ... and the next cycle enters it again
    CHECK(::test_trace::trace == "ESELS");
}

void testLaterFailSafeEventKeepsItsReaction()
{
    auto& supervisor = fresh();
    deviceFault(supervisor, fault(SPI0, 3U, device::FaultClass::OUTPUT));
    ::test_trace::trace.clear();
    supervisor.handle(Event::SAFE_WATCHDOG_SEQUENCE_DEVIATION);
    CHECK(::test_trace::trace == RESET);
    CHECK(::safety::degradedRecord.groups == 0x1U); // kept for the next start
    // an unmapped device after SD: its own fail-safe reaction
    auto& other = fresh();
    deviceFault(other, fault(SPI0, 3U, device::FaultClass::OUTPUT));
    ::test_trace::trace.clear();
    deviceFault(other, fault(SPI1, 6U, device::FaultClass::OUTPUT));
    CHECK(::test_trace::trace == RESET);
}

// ------------------------------------------------------------------------------------------ faults injected
void testCorruptedLatchDegradesEverything()
{
    auto& supervisor = fresh();
    deviceFault(supervisor, fault(SPI0, 3U, device::FaultClass::OUTPUT));
    supervisor._degradedGroupsInverted = 0U; // wild write into the protected RAM
    uint32_t rte = 0U;
    CHECK(!::safety::degraded::rte::readGroups(rte));
    CHECK(rte == 0x7U); // the application reads every group degraded
    ::test_trace::trace.clear();
    supervisor.deliverDeferredEvents();
    CHECK(::test_trace::trace.find('S') != std::string::npos); // every group: S1
    CHECK(groupsOf(supervisor) == 0x7U);
    CHECK(::safety::degraded::rte::readGroups(rte) && (rte == 0x7U));

    auto& bits = fresh(); // a latch with a group the project does not have
    bits._degradedGroups         = 0x8U;
    bits._degradedGroupsInverted = ~0x8U;
    CHECK(groupsOf(bits) == 0x7U);
}

void testRecordIsKeptInStep()
{
    auto& supervisor = fresh();
    deviceFault(supervisor, fault(SPI0, 3U, device::FaultClass::OUTPUT));
    ::safety::degradedRecord.check = 0U; // wild write into the no-init RAM
    supervisor.deliverDeferredEvents();
    CHECK(degraded::isValid(::safety::degradedRecord, 0x7U));
    CHECK(::safety::degradedRecord.groups == 0x1U);
    // a valid record with one more group adds it (only ever in the safe direction)
    degraded::write(::safety::degradedRecord, 0x3U, 0x13U, 0U);
    ::test_trace::walks.clear();
    supervisor.deliverDeferredEvents();
    CHECK(groupsOf(supervisor) == 0x3U);
    CHECK(!::test_trace::walks.empty() && (::test_trace::walks.front() == 0x3U));
    // a valid record with fewer groups is written back from the latch
    degraded::write(::safety::degradedRecord, 0x0U, 0x13U, 0U);
    supervisor.deliverDeferredEvents();
    CHECK(::safety::degradedRecord.groups == 0x3U);
    CHECK(groupsOf(supervisor) == 0x3U);
}

// --------------------------------------------------------------------------------------------------- start
void startWith(SafeSupervisor& supervisor, bool const powerOn)
{
    static ::safety::resetloop::Record volatile loop{};
    ::safety::resetloop::clear(loop);
    ::safety::resetloop::Start start{};
    start.powerOn  = powerOn;
    start.watchdog = !powerOn;
    ::safety::safeSupervisorConstructor.construct(); // the supervisor's RAM is new after every reset
    ::test_trace::trace.clear();
    ::test_trace::walks.clear();
    (void)supervisor.startResetLoopLimit(loop, start, 3U, 100U);
}

void testRecordSurvivesSafetyResetAndPowerOnClears()
{
    auto& supervisor = fresh();
    deviceFault(supervisor, fault(SPI0, 4U, device::FaultClass::OUTPUT));
    // safety reset (any start without power-on): the group goes to its safe state again before the BSP
    startWith(supervisor, false);
    CHECK(::test_trace::walks == std::vector<uint32_t>{0x2U});
    CHECK(groupsOf(SafeSupervisor::getInstance()) == 0x2U);
    CHECK(::test_trace::trace.find('R') == std::string::npos);
    // ... and again after the next one
    startWith(supervisor, false);
    CHECK(groupsOf(SafeSupervisor::getInstance()) == 0x2U);
    // power-on: the only way back (B6)
    startWith(supervisor, true);
    CHECK(::test_trace::walks.empty());
    CHECK(groupsOf(SafeSupervisor::getInstance()) == 0U);
    CHECK(degraded::isValid(::safety::degradedRecord, 0x7U) && (::safety::degradedRecord.groups == 0U));
    // all groups in the record: S1 at the start
    degraded::write(::safety::degradedRecord, 0x7U, 0x13U, 0U);
    startWith(supervisor, false);
    CHECK(::test_trace::trace.find('S') != std::string::npos);
    CHECK(groupsOf(SafeSupervisor::getInstance()) == 0x7U);
}

void testImplausibleRecordReadsAsEveryGroup()
{
    auto& supervisor = fresh();
    ::safety::degradedRecord.check = 0x12345678U; // overwritten
    startWith(supervisor, false);
    CHECK(groupsOf(SafeSupervisor::getInstance()) == 0x7U);
    CHECK(::test_trace::trace.find('S') != std::string::npos);
    CHECK(::safety::degradedRecord.event == degraded::EVENT_IMPLAUSIBLE);
    // a record with a group the project does not have is implausible as well
    degraded::write(::safety::degradedRecord, 0x9U, 0x13U, 0U);
    startWith(supervisor, false);
    CHECK(groupsOf(SafeSupervisor::getInstance()) == 0x7U);
    // after power-on the random content is never read
    ::safety::degradedRecord.check = 0x12345678U;
    startWith(supervisor, true);
    CHECK(groupsOf(SafeSupervisor::getInstance()) == 0U);
}

void testRecordRules()
{
    degraded::Record volatile record{};
    degraded::clear(record);
    CHECK(degraded::isValid(record, 0x1U));
    degraded::write(record, 0x1U, 0x13U, 0xABCDU);
    CHECK(degraded::isValid(record, 0x1U));
    CHECK(!degraded::isValid(record, 0x0U)); // a group outside the project
    uint32_t const fields[] = {0U, 1U, 2U, 3U};
    for (uint32_t const f : fields)
    {
        degraded::write(record, 0x1U, 0x13U, 0xABCDU);
        uint32_t volatile* const word = &record.groups + f;
        *word                         = *word ^ 0x100U; // one bit of one field
        CHECK(!degraded::isValid(record, 0x1FFU));
    }
    CHECK(degraded::allGroupsOf(3U) == 0x7U);
    CHECK(degraded::allGroupsOf(8U) == 0xFFU);
}
// IO-45: the lock of the drivers of a group - per entry of the safe outputs table, from the same latch
void testEntryLockFollowsTheGroups()
{
    // the profile: group 0 = entry 0, group 1 = entry 1, group 2 = entries 2 and 3 (a PWM group and its enable)
    CHECK(SafeSupervisor::groupsOfEntry(0U) == 0x1U);
    CHECK(SafeSupervisor::groupsOfEntry(1U) == 0x2U);
    CHECK(SafeSupervisor::groupsOfEntry(2U) == 0x4U);
    CHECK(SafeSupervisor::groupsOfEntry(3U) == 0x4U);
    CHECK(SafeSupervisor::groupsOfEntry(4U) == 0x0U); // an entry of no group (a safe_off output without PWM group)
    CHECK(SafeSupervisor::groupsOfEntry(31U) == 0x0U);
    CHECK(SafeSupervisor::groupsOfEntry(32U) == 0x0U);
    CHECK(SafeSupervisor::groupsOfEntry(0xFFFFFFFFU) == 0x0U);

    auto& supervisor = fresh();
    for (uint32_t entry = 0U; entry < 32U; ++entry)
    {
        CHECK(!supervisor.isEntryDegraded(entry)); // nothing degraded after a power-on
    }
    deviceFault(supervisor, fault(SPI1, 5U, device::FaultClass::OUTPUT)); // mapping 2: group 2
    CHECK(groupsOf(supervisor) == 0x4U);
    CHECK(!supervisor.isEntryDegraded(0U));
    CHECK(!supervisor.isEntryDegraded(1U));
    CHECK(supervisor.isEntryDegraded(2U)); // the PWM group of group 2 ...
    CHECK(supervisor.isEntryDegraded(3U)); // ... and its enable: one lock for the group
    CHECK(!supervisor.isEntryDegraded(4U));
    deviceFault(supervisor, fault(SPI0, 3U, device::FaultClass::OUTPUT)); // mapping 0: group 0 as well
    CHECK(supervisor.isEntryDegraded(0U));
    CHECK(!supervisor.isEntryDegraded(1U));
    // leaving S1 releases no degraded group, so no entry either (decision B6 (a))
    ::safety::SafeState::leaveSafeState();
    CHECK(supervisor.isEntryDegraded(0U) && supervisor.isEntryDegraded(3U));
    // a corrupted latch locks every entry of every group - never an entry of no group
    supervisor._degradedGroupsInverted = 0U;
    CHECK(supervisor.isEntryDegraded(1U));
    CHECK(!supervisor.isEntryDegraded(4U));
}
// SYS-123: the degraded groups applied again right after the BSP start (main() of the family port)
void testGroupsAppliedAgainAfterTheBspStart()
{
    auto& supervisor = fresh();
    supervisor.applyDegradedGroups();
    CHECK(::test_trace::walks.empty());          // nothing degraded: nothing written
    CHECK(::test_trace::trace.find('S') == std::string::npos);
    deviceFault(supervisor, fault(SPI1, 5U, device::FaultClass::OUTPUT)); // group 2
    ::test_trace::walks.clear();
    ::test_trace::trace.clear();
    supervisor.applyDegradedGroups();
    CHECK(::test_trace::walks.size() == 1U);
    CHECK(!::test_trace::walks.empty() && (::test_trace::walks[0] == 0xCU)); // the entries of group 2
    CHECK(::test_trace::trace.find('S') == std::string::npos);                // no S1
    CHECK(groupsOf(supervisor) == 0x4U);                                       // state unchanged
    // every group degraded: S1 like the hold
    deviceFault(supervisor, fault(SPI0, 3U, device::FaultClass::OUTPUT));
    deviceFault(supervisor, fault(SPI0, 4U, device::FaultClass::OUTPUT));
    ::test_trace::trace.clear();
    supervisor.applyDegradedGroups();
    CHECK(::test_trace::trace.find('S') != std::string::npos);
    // a corrupted latch: every group, i.e. S1
    auto& other = fresh();
    other._degradedGroupsInverted = 0U;
    ::test_trace::trace.clear();
    other.applyDegradedGroups();
    CHECK(::test_trace::trace.find('S') != std::string::npos);
}
// ------------------------------------------------------------------ SYS-138: read-back of SD, the hold, S1 + S2
constexpr uint32_t CAUSE_LOCK_REGISTER
    = ::safety::resetloop::CAUSE_EVENT | static_cast<uint32_t>(Event::LOCK_REGISTER_ERROR);

void testDegradedGroupsAreReadBackBeforeTheirWalk()
{
    auto& supervisor = fresh();
    supervisor.deliverDeferredEvents();
    CHECK(::test_readback::asked.empty()); // nothing degraded: nothing read back
    deviceFault(supervisor, fault(SPI0, 4U, device::FaultClass::OUTPUT)); // group 1 = entry 1
    ::test_trace::trace.clear();
    ::test_trace::walks.clear();
    for (int i = 0; i < 3; ++i)
    {
        supervisor.deliverDeferredEvents();
    }
    CHECK(::test_readback::asked == (std::vector<uint32_t>{0x2U, 0x2U, 0x2U}));
    CHECK(::test_trace::walks == (std::vector<uint32_t>{0x2U, 0x2U, 0x2U}));
    CHECK(::test_trace::trace == "DDD"); // confirmed: the walks only, no report, no reset
    // the entries of the groups, not the group bits: group 2 is entries 2 and 3
    deviceFault(supervisor, fault(SPI1, 5U, device::FaultClass::OUTPUT));
    ::test_readback::asked.clear();
    supervisor.deliverDeferredEvents();
    CHECK(::test_readback::asked == std::vector<uint32_t>{0xEU});
    // not confirmed (entry 3, the enable of group 2): read back before the walk of the cycle, which does not run;
    // LOCK_REGISTER_ERROR with class SAFE_OUTPUT and the entry, S1 + S2 (the reaction of SafeIo, review K5)
    ::test_readback::failEntries = 0x8U;
    ::test_readback::asked.clear();
    ::test_trace::trace.clear();
    ::test_trace::walks.clear();
    supervisor.deliverDeferredEvents();
    CHECK(::test_readback::asked == std::vector<uint32_t>{0xEU});
    CHECK(::test_trace::walks.empty());
    CHECK(::test_trace::trace == RESET);
    CHECK(supervisor.lockRegisterMonitor.getContext() == 0x07000003U);
    CHECK(supervisor.inLimpHomeState());
    CHECK(supervisor._resetLoopRecord->pending == CAUSE_LOCK_REGISTER);
    // the first question of a cycle comes before its walk
    auto& order = fresh();
    deviceFault(order, fault(SPI0, 3U, device::FaultClass::OUTPUT));
    ::test_trace::walks.clear();
    ::test_readback::reset();
    order.deliverDeferredEvents();
    CHECK(::test_readback::walksAtFirstAsk == 0U);
    CHECK(::test_trace::walks == std::vector<uint32_t>{0x1U});
    // every group degraded: S1 with its latch, read back by SafeIo - not asked here
    auto& all = fresh();
    deviceFault(all, fault(SPI0, 3U, device::FaultClass::OUTPUT));
    deviceFault(all, fault(SPI0, 4U, device::FaultClass::OUTPUT));
    deviceFault(all, fault(SPI1, 5U, device::FaultClass::OUTPUT));
    CHECK(groupsOf(all) == 0x7U);
    ::test_readback::reset();
    all.deliverDeferredEvents();
    CHECK(::test_readback::asked.empty());
}

void testHoldIsConfirmedByReadbackInEveryRound()
{
    auto& supervisor  = fresh();
    ::test_hold::left = 3;
    supervisor.romCheckMonitor.trigger(0U);
    CHECK(::test_trace::trace == "ESPSWSWSW"); // log, walk, line, 3 rounds of walk and watchdog: no reset
    CHECK(::test_readback::s1Calls == 3);     // one read-back per round
    CHECK(supervisor.holdRounds() == 3U);
    // S1 not confirmed: LOCK_REGISTER_ERROR (S1 + S2) in the first round, before its walk; no line (not confirmed
    // before the reset either), the cause in the reset-loop record
    auto& other            = fresh();
    ::test_hold::left      = 3;
    ::test_readback::s1Answer = 0x07000001U;
    other.romCheckMonitor.trigger(0U);
    CHECK(::test_trace::trace == "ESPSR");
    CHECK(other.holdRounds() == 0U);
    CHECK(::test_hold::left == 2);
    CHECK(other._resetLoopRecord->pending == CAUSE_LOCK_REGISTER);
    // a fault that appears in round 3 of the hold of a DEVICE_FAULT of class IDENTITY (from the safety task): two
    // rounds held, then the reset
    auto& late                    = fresh();
    ::test_hold::left             = 5;
    ::test_readback::failFromCall = 3;
    late.deviceFaultMonitor.trigger(fault(SPI0, 9U, device::FaultClass::IDENTITY));
    CHECK(::test_trace::trace == "ESPSWSWSR");
    CHECK(late.holdRounds() == 2U);
}

void testResetLineOnlyForConfirmedS1()
{
    auto& supervisor = fresh();
    supervisor.deadmanConfigMonitor.trigger(1U);
    CHECK(::test_trace::trace == RESET);
    CHECK(::test_readback::s1Calls == 1);
    auto& other               = fresh();
    ::test_readback::s1Answer = 0x07000000U;
    other.deadmanConfigMonitor.trigger(1U);
    CHECK(::test_trace::trace == "ESR"); // no flush, no line: the reset at once
    CHECK(other._resetLoopRecord->pending
          == (::safety::resetloop::CAUSE_EVENT | static_cast<uint32_t>(Event::DEADMAN_CONFIGURATION_ERROR)));
    ::test_trace::trace.clear();
    other.reportEtlAssert(0x1234U); // the fault paths share the sequence
    CHECK(::test_trace::trace == "SR");
    ::test_readback::s1Answer = 0U;
    ::test_trace::trace.clear();
    other.reportEtlAssert(0x1234U);
    CHECK(::test_trace::trace == "SFPFR");
}
} // namespace

int main()
{
    testDegradedGroupsAreReadBackBeforeTheirWalk(); // SYS-138
    testHoldIsConfirmedByReadbackInEveryRound();     // SYS-138
    testResetLineOnlyForConfirmedS1();               // SYS-138
    testEntryLockFollowsTheGroups();
    testGroupsAppliedAgainAfterTheBspStart();
    testOnlyTheClosedListIsDegradable();
    testUnmappedDeviceFaultKeepsItsReaction();
    testIdentityAndMalformedStayFailSafe();
    testMappedDeviceFaultDegradesItsGroup();
    testGroupsAccumulateAndEscalate();
    testLostBusDegradesAllItsDevicesOrNothing();
    testGroupsAreHeldEveryCycleAndNeverReturn();
    testLaterFailSafeEventKeepsItsReaction();
    testCorruptedLatchDegradesEverything();
    testRecordIsKeptInStep();
    testRecordSurvivesSafetyResetAndPowerOnClears();
    testImplausibleRecordReadsAsEveryGroup();
    testRecordRules();
    std::printf("SafeSupervisorDegradedHostTest: %d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
