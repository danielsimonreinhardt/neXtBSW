/* SYS-140 (decision C4, doc/safety/partner-and-degraded.md 5.9): host test of the removal of the active short on a bridge
 * fault - the real SafeSupervisor.cpp and Degraded.h (NX_SAFE_BRIDGE_FAULT_CLASSES), built as for a project in which a
 * PWM group allows an active short, with fault injection.
 *
 * Own test profile (not the eval board, not a real project): three selection groups (PWM groups with a selection word)
 * and three devices bound to them - device 3 (SPI 0) to group 0, device 6 (no bus) to groups 1 and 2 (the gate driver
 * of two bridges), device 7 (SPI 1) to group 2 - plus one degraded mapping (device 3 -> safe-output group 0), so the
 * order against the degraded walk is visible too. The values are what tools/nxconfig/safestate.py and degraded.py
 * derive into integration/safeSupervisorConfiguration.h; they are defined here before the unit under test, like the
 * generated header would. The family's selection service is a stub that records every block ('b' in the trace, the
 * group in `blocked`).
 *
 * What it shows:
 * - exactly OUTPUT, SUPPLY and INTERNAL are bridge fault classes (every value 0..255 checked);
 * - DEVICE_FAULT of a bound device with such a class removes the short of exactly the groups of that device, and does
 *   it first: before the log line, before the degraded walk (SD), before S1 of the reset and before the hold - with the
 *   interrupt lock of handle() taken;
 * - every other class (COMMUNICATION, IDENTITY, TEMPERATURE, ...) keeps the selection, and so does a device bound to
 *   no group; the reaction of every DEVICE_FAULT is otherwise exactly the one without an active short;
 * - a malformed word removes the short of every group the family has (never more than nxSafeStateGroupCount());
 * - DEVICE_REPORT (report only, S3) removes nothing, whatever its class; no other event removes anything.
 * The meaningfulness is checked by test_bridge_fault_mutations.py (built-in faults in SafeSupervisor.cpp and Degraded.h).
 *
 * Build and run (zig c++, no hardware): tools/test -k safe-supervisor-bridge-fault
 */

#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>

// ---- test profile: what tools/nxconfig derives for three selection groups and three bound devices ----
// safestate.py: instance << 8 | selection groups
#define NX_MODULE_SAFESUPERVISOR_DERIVED_BRIDGE_FAULT_DEVICES  3U
#define NX_MODULE_SAFESUPERVISOR_DERIVED_BRIDGE_FAULT_DEVICE_0 0x0301U // device 3 -> group 0
#define NX_MODULE_SAFESUPERVISOR_DERIVED_BRIDGE_FAULT_DEVICE_1 0x0606U // device 6 -> groups 1, 2
#define NX_MODULE_SAFESUPERVISOR_DERIVED_BRIDGE_FAULT_DEVICE_2 0x0704U // device 7 -> group 2
// degraded.py: one safe-output group (entry 0), DEVICE_FAULT of device 3 on SPI 0 -> it
#define NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_GROUPS                3U
#define NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_GROUP_0_ENTRIES       0x1U
#define NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_GROUP_1_ENTRIES       0x2U
#define NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_GROUP_2_ENTRIES       0x4U
#define NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPINGS              1U
#define NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPING_0             0x13032001U
#define NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPING_0_BUS_GROUPS  0x0U

namespace test_trace
{
std::string trace;
int lockDepth = 0;
bool unlockedBlock = false; // a block outside the interrupt lock of handle()

void add(char const c) { trace.push_back(c); }

void print(char const*, char const*) { add('P'); }

void degraded(uint32_t) { add('D'); }
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

namespace test_select
{
uint32_t groupCount = 3U;
std::vector<uint32_t> blocked; // groups in the order of the calls
} // namespace test_select

#define NX_SAFE_SUPERVISOR_HOLDING()              (::test_hold::holding())
#define NX_SAFE_SUPERVISOR_CONSOLE(format, event) (::test_trace::print(format, event))
#define NX_SAFE_SUPERVISOR_RESET_FLUSH()          (::test_trace::add('F'), true)

extern "C" void softwareSystemReset(void) { ::test_trace::add('R'); }

// the family's selection service (on the target: platforms/s32k1xx/hardFaultHandler/src/SafeOutputSelection.cpp)
extern "C" uint32_t nxSafeStateGroupCount(void) { return ::test_select::groupCount; }

extern "C" void nxSafeStateBlockShort(uint32_t const group)
{
    ::test_trace::add('b');
    ::test_select::blocked.push_back(group);
    if (::test_trace::lockDepth <= 0)
    {
        ::test_trace::unlockedBlock = true;
    }
}

// bridgeGroupsOf() is private; the test asks it directly
#define private public
#include "../../src/safeSupervisor/SafeSupervisor.cpp" // NOLINT: the unit under test
#undef private

namespace safety
{
SafetyEventMailbox rteTimingMailbox;
} // namespace safety

namespace
{
using ::safety::SafeSupervisor;
using Event        = SafeSupervisor::Event;
namespace device   = ::safety::device;

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

constexpr uint8_t SPI0 = device::busId(device::BusKind::SPI, 0U);
constexpr uint8_t SPI1 = device::busId(device::BusKind::SPI, 1U);

constexpr uint32_t fault(uint8_t const bus, uint8_t const instance, device::FaultClass const cls)
{
    return device::context(bus, instance, cls, 0x3CU);
}

std::string const RESET = "ESFPFR"; // log, S1, flush, line, flush, reset (SYS-59)
std::string const HELD  = "ESPSWSW"; // log, S1, line, two rounds of the hold (S1 and the watchdog each)

SafeSupervisor& fresh()
{
    ::safety::safeSupervisorConstructor.construct();
    auto& supervisor = SafeSupervisor::getInstance();
    static ::safety::resetloop::Record volatile loop{};
    ::safety::resetloop::Start start{};
    start.powerOn = true;
    (void)supervisor.startResetLoopLimit(loop, start, 3U, 100U);
    supervisor.leaveLimpHome();
    (void)::safety::rteTimingMailbox.drain([](uint32_t) {});
    ::test_trace::trace.clear();
    ::test_trace::lockDepth     = 0;
    ::test_trace::unlockedBlock = false;
    ::test_select::blocked.clear();
    ::test_select::groupCount = 3U;
    ::test_hold::left         = 2;
    return supervisor;
}

std::vector<uint32_t> groups(std::initializer_list<uint32_t> const list) { return std::vector<uint32_t>(list); }

// ------------------------------------------------------------------------------------------------ the class list
void testExactlyTheBridgeFaultClasses()
{
    for (uint32_t value = 0U; value <= 0xFFU; ++value)
    {
        auto const cls      = static_cast<uint8_t>(value);
        bool const expected = (cls == static_cast<uint8_t>(device::FaultClass::OUTPUT))
                              || (cls == static_cast<uint8_t>(device::FaultClass::SUPPLY))
                              || (cls == static_cast<uint8_t>(device::FaultClass::INTERNAL));
        CHECK(SafeSupervisor::isBridgeFaultClass(cls) == expected);
    }
    CHECK(SafeSupervisor::bridgeGroupsOf(3U) == 0x1U);
    CHECK(SafeSupervisor::bridgeGroupsOf(6U) == 0x6U);
    CHECK(SafeSupervisor::bridgeGroupsOf(7U) == 0x4U);
    CHECK(SafeSupervisor::bridgeGroupsOf(4U) == 0U);
    CHECK(SafeSupervisor::bridgeGroupsOf(0U) == 0U);
}

// ------------------------------------------------------------------- a bridge fault removes the short first
void testBridgeFaultBlocksBeforeTheResetWalk()
{
    device::FaultClass const bridge[]
        = {device::FaultClass::OUTPUT, device::FaultClass::SUPPLY, device::FaultClass::INTERNAL};
    for (device::FaultClass const cls : bridge)
    {
        auto& supervisor = fresh();
        supervisor.deviceFaultMonitor.trigger(fault(SPI1, 7U, cls)); // device 7: no degraded mapping, S1 + S2
        CHECK(::test_trace::trace == "bE" + RESET);                   // the block, then the log line of it, ...
        CHECK(::test_select::blocked == groups({2U}));
        CHECK(!::test_trace::unlockedBlock);
        CHECK(supervisor.inLimpHomeState());
    }
}

void testBridgeFaultOfADeviceOfTwoGroups()
{
    auto& supervisor = fresh();
    supervisor.deviceFaultMonitor.trigger(fault(device::NO_BUS, 6U, device::FaultClass::SUPPLY));
    CHECK(::test_trace::trace == "bbE" + RESET);
    CHECK(::test_select::blocked == groups({1U, 2U}));
}

void testBridgeFaultBlocksBeforeTheDegradedWalk()
{
    auto& supervisor = fresh();
    supervisor.deviceFaultMonitor.trigger(fault(SPI0, 3U, device::FaultClass::OUTPUT)); // mapped: SD
    // block, its log line, the DEVICE_FAULT log line, the degraded walk, the SD log line - no reset
    CHECK(::test_trace::trace == "bEEDE");
    CHECK(::test_select::blocked == groups({0U}));
    CHECK(!::test_trace::unlockedBlock);
    CHECK(!supervisor.inLimpHomeState());
}

void testOtherClassesKeepTheSelection()
{
    device::FaultClass const others[] = {
        device::FaultClass::COMMUNICATION, device::FaultClass::CONFIGURATION, device::FaultClass::DEVICE_RESET,
        device::FaultClass::TEMPERATURE,   device::FaultClass::MEASUREMENT,   device::FaultClass::PLAUSIBILITY,
        device::FaultClass::STALE,         device::FaultClass::ACTUATOR_STATE};
    for (device::FaultClass const cls : others)
    {
        auto& supervisor = fresh();
        supervisor.deviceFaultMonitor.trigger(fault(SPI1, 7U, cls));
        CHECK(::test_trace::trace == RESET); // exactly the reaction without an active short
        CHECK(::test_select::blocked.empty());
    }
    // IDENTITY of a bound device: S1 held as before, the selection stays (not a bridge fault by the list)
    auto& held = fresh();
    held.deviceFaultMonitor.trigger(fault(SPI1, 7U, device::FaultClass::IDENTITY));
    CHECK(::test_trace::trace == HELD);
    CHECK(::test_select::blocked.empty());
    // SD of the mapped device with a class that is no bridge fault: degraded walk without a block
    auto& degraded = fresh();
    degraded.deviceFaultMonitor.trigger(fault(SPI0, 3U, device::FaultClass::TEMPERATURE));
    CHECK(::test_trace::trace == "EDE");
    CHECK(::test_select::blocked.empty());
}

void testUnboundDeviceKeepsTheSelection()
{
    uint8_t const unbound[] = {0U, 4U, 5U, 8U, 0xFFU};
    for (uint8_t const instance : unbound)
    {
        auto& supervisor = fresh();
        supervisor.deviceFaultMonitor.trigger(fault(SPI1, instance, device::FaultClass::OUTPUT));
        CHECK(::test_trace::trace == RESET);
        CHECK(::test_select::blocked.empty());
    }
}

// --------------------------------------------------------------------------------------------- malformed word
void testMalformedWordBlocksEveryGroup()
{
    uint32_t const malformed[] = {
        0U,                                                                      // a forgotten field
        device::context(SPI0, 3U, static_cast<device::FaultClass>(0xEEU)),        // unknown class
        device::context(0x00U, 3U, device::FaultClass::TEMPERATURE),             // bus kind INVALID
        device::context(0xC0U, 6U, device::FaultClass::COMMUNICATION),           // bus kind RESERVED
        ::safety::SafetyEventMailbox::CONTEXT_LOST,                              // a box word as a fault
    };
    for (uint32_t const word : malformed)
    {
        auto& supervisor = fresh();
        supervisor.deviceFaultMonitor.trigger(word);
        CHECK(::test_trace::trace == "bbbE" + RESET);
        CHECK(::test_select::blocked == groups({0U, 1U, 2U}));
        CHECK(!::test_trace::unlockedBlock);
    }
    // never beyond the groups the family has
    auto& one = fresh();
    ::test_select::groupCount = 1U;
    one.deviceFaultMonitor.trigger(0U);
    CHECK(::test_select::blocked == groups({0U}));
    auto& none = fresh();
    ::test_select::groupCount = 0U;
    none.deviceFaultMonitor.trigger(0U);
    CHECK(::test_select::blocked.empty());
    CHECK(::test_trace::trace == RESET);
    // a bound device's groups beyond the family's count are not passed on either
    auto& fewer = fresh();
    ::test_select::groupCount = 2U;
    fewer.deviceFaultMonitor.trigger(fault(device::NO_BUS, 6U, device::FaultClass::OUTPUT));
    CHECK(::test_select::blocked == groups({1U}));
}

// ------------------------------------------------------------------------------------------- nothing else blocks
void testReportsAndOtherEventsBlockNothing()
{
    auto& supervisor = fresh();
    supervisor.deviceReportMonitor.trigger(fault(SPI0, 3U, device::FaultClass::OUTPUT));
    supervisor.deviceReportMonitor.trigger(fault(SPI1, 7U, device::FaultClass::SUPPLY));
    supervisor.deviceReportMonitor.trigger(0U);
    CHECK(::test_select::blocked.empty());
    CHECK(::test_trace::trace.find('S') == std::string::npos);

    auto& reset = fresh();
    reset.lockRegisterMonitor.trigger(5U);
    CHECK(::test_select::blocked.empty());
    auto& register_ = fresh();
    register_.checkRegisterMonitor.trigger(false);
    CHECK(::test_select::blocked.empty());
    auto& cycle = fresh();
    cycle.deliverDeferredEvents();
    CHECK(::test_select::blocked.empty());
}

// ----------------------------------------------------------------------- the block is idempotent per reaction
void testRepeatedBridgeFaultBlocksAgain()
{
    auto& supervisor = fresh();
    supervisor.deviceFaultMonitor.trigger(fault(SPI0, 3U, device::FaultClass::INTERNAL));
    supervisor.deviceFaultMonitor.trigger(fault(SPI0, 3U, device::FaultClass::INTERNAL));
    // every report blocks again (the family's block is idempotent); nothing ever gives a short back
    CHECK(::test_select::blocked == groups({0U, 0U}));
}
} // namespace

int main()
{
    testExactlyTheBridgeFaultClasses();
    testBridgeFaultBlocksBeforeTheResetWalk();
    testBridgeFaultOfADeviceOfTwoGroups();
    testBridgeFaultBlocksBeforeTheDegradedWalk();
    testOtherClassesKeepTheSelection();
    testUnboundDeviceKeepsTheSelection();
    testMalformedWordBlocksEveryGroup();
    testReportsAndOtherEventsBlockNothing();
    testRepeatedBridgeFaultBlocksAgain();
    std::printf("safe supervisor bridge fault (SYS-140): %d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
