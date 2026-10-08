/* SYS-41 (finding F12 of doc/safety/fmea.md, SM-02/SM-03): host test of SafeWatchdog with the real code.
 *
 * Units under test: ecu/safety/safeWatchdog/src/safeWatchdog/SafeWatchdog.cpp, the family check
 * platforms/s32k1xx/port/safety/safeSupervisionCheck (WatchdogControlCheck.cpp) and the real SafeSupervisor
 * (reaction). The watchdog driver is a stub with the driver's own configuration check (CS[EN], CS[CLK], TOVAL) on a
 * register model of the WDOG (stubs/).
 *
 * What it shows:
 * - the configuration that Watchdog::enableWatchdog(250 ms, interrupt on) writes passes, cycle after cycle;
 * - every single corrupted control field is a SAFE_WATCHDOG_CONFIGURATION_ERROR with its field in the context -
 *   in particular CS[INT] (the warning interrupt that de-energises before the reset), CS[WIN], CS[PRES] and
 *   CS[UPDATE], which the driver's check does not see (before SYS-41: no event);
 * - an extra service between two checks is SAFE_WATCHDOG_SERVICE_DEVIATION, and the service bracket closes.
 *
 * Built-in faults (mutants, run by hand for SYS-41): the family check not called, INT compared against the wrong
 * value, one field left out - each makes this test red (see doc/changelog.d/SYS-41.md).
 *
 * Build and run (zig c++, no hardware):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -DPLATFORM_SUPPORT_WATCHDOG \
 *       -Iecu/safety/safeWatchdog/test/host/stubs -Iecu/safety/safeWatchdog/include \
 *       -Iplatforms/s32k1xx/port/safety/safeSupervisionCheck/include \
 *       -Iecu/safety/safeSupervisor/include -Iecu/safety/safeSupervisor/test/host/stubs \
 *       -isystem external/openbsw/libs/safety/safeMonitor/include \
 *       ecu/safety/safeWatchdog/test/host/SafeWatchdogHostTest.cpp \
 *       ecu/safety/safeWatchdog/src/safeWatchdog/SafeWatchdog.cpp \
 *       platforms/s32k1xx/port/safety/safeSupervisionCheck/src/safeSupervisionCheck/WatchdogControlCheck.cpp \
 *       -o build/host/safewatchdog.exe
 */

#include <cstdint>
#include <cstdio>
#include <string>

namespace test_trace
{
std::string trace;
int lockDepth = 0;

void add(char const c) { trace.push_back(c); }
} // namespace test_trace

#define NX_SAFE_SUPERVISOR_HOLDING()              (false)
#define NX_SAFE_SUPERVISOR_CONSOLE(format, event) ((void)(format), (void)(event), ::test_trace::add('P'))

extern "C" void softwareSystemReset(void) { ::test_trace::add('R'); }

#include "../../../safeSupervisor/src/safeSupervisor/SafeSupervisor.cpp" // NOLINT: the reaction, real code

#include <mcu/mcu.h>
#include <safeSupervisionCheck/WatchdogControlCheck.h>
#include <safeWatchdog/SafeWatchdog.h>
#include <watchdog/Watchdog.h>

WdogType wdogRegisters{};

namespace safety
{
SafetyEventMailbox rteTimingMailbox;
} // namespace safety

namespace
{
using ::safety::SafeSupervisor;
using ::safety::SafeWatchdog;
namespace sv = ::safety::supervision;

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

constexpr uint32_t TST_USER_MODE = 1U << WDOG_CS_TST_SHIFT; // left by setUserMode() after the fast test

/** A fresh supervisor, the watchdog set up as main() does it (fast test passed, then enableMcuWatchdog()). */
SafeSupervisor& fresh(SafeWatchdog& watchdog)
{
    ::safety::safeSupervisorConstructor.construct();
    auto& supervisor = SafeSupervisor::getInstance();
    supervisor.leaveLimpHome();
    wdogRegisters                                = WdogType{};
    wdogRegisters.CS                             = TST_USER_MODE;
    ::safety::bsp::Watchdog::serviceCounter      = 0U;
    watchdog.init();
    ::test_trace::trace.clear();
    return supervisor;
}

/** One watchdog step of the safety cycle as SafetyManager::cyclic runs it: ENTER bracket, then cyclic(). */
void step(SafeSupervisor& supervisor, SafeWatchdog& watchdog)
{
    supervisor.safeWatchdogSequenceMonitor.hit(SafeSupervisor::EnterLeaveSequence::ENTER);
    watchdog.cyclic();
}

void testConfigurationOfEnablePasses()
{
    SafeWatchdog watchdog;
    auto& supervisor = fresh(watchdog);
    CHECK((wdogRegisters.CS & WDOG_CS_INT_MASK) != 0U); // enableMcuWatchdog() asks for the warning interrupt
    // SYS-25: the time-out of the configuration (test profile stubs/integration: 400 ms), 128 LPO ticks per ms
    CHECK(wdogRegisters.TOVAL == (128U * NX_MODULE_SAFELIFECYCLE_PARAM_WATCHDOG_TIMEOUT_MS));
    CHECK(wdogRegisters.TOVAL == 51200U);
    CHECK(sv::checkWatchdogControl(true) == 0U);
    for (int i = 0; i < 5; ++i)
    {
        step(supervisor, watchdog);
    }
    CHECK(::test_trace::trace == "WWWWW"); // five services, no log line, no reset
    CHECK(!supervisor.inLimpHomeState());
}

struct Corruption
{
    char const* name;
    uint32_t setBits;
    uint32_t clearBits;
    uint32_t toval;       // 0 = unchanged
    uint32_t expected;    // context of the event
};

void testEveryCorruptedFieldIsReported()
{
    Corruption const cases[] = {
        {"CS[INT] cleared", 0U, WDOG_CS_INT_MASK, 0U, sv::WATCHDOG_FIELD_INT},
        {"CS[WIN] set", WDOG_CS_WIN_MASK, 0U, 0U, sv::WATCHDOG_FIELD_WIN},
        {"CS[PRES] set", WDOG_CS_PRES_MASK, 0U, 0U, sv::WATCHDOG_FIELD_PRES},
        {"CS[UPDATE] cleared", 0U, WDOG_CS_UPDATE_MASK, 0U, sv::WATCHDOG_FIELD_UPDATE},
        {"CS[EN] cleared", 0U, WDOG_CS_EN_MASK, 0U, sv::WATCHDOG_FIELD_DRIVER_CHECK},
        {"CS[CLK] bus clock", 0U, WDOG_CS_CLK_MASK, 0U, sv::WATCHDOG_FIELD_DRIVER_CHECK},
        {"TOVAL changed", 0U, 0U, 1000U, sv::WATCHDOG_FIELD_DRIVER_CHECK},
        {"INT and PRES", WDOG_CS_PRES_MASK, WDOG_CS_INT_MASK, 0U,
         sv::WATCHDOG_FIELD_INT | sv::WATCHDOG_FIELD_PRES},
    };
    for (auto const& c : cases)
    {
        SafeWatchdog watchdog;
        auto& supervisor = fresh(watchdog);
        step(supervisor, watchdog); // healthy first
        ::test_trace::trace.clear();
        wdogRegisters.CS = (wdogRegisters.CS & ~c.clearBits) | c.setBits;
        if (c.toval != 0U)
        {
            wdogRegisters.TOVAL = c.toval;
        }
        step(supervisor, watchdog);
        // log line, console line (SYS-59), reset (S2; no safe outputs without PLATFORM_SUPPORT_IO), then the service
        // of this cycle
        bool const reported = (::test_trace::trace == "EPRW");
        CHECK(reported);
        CHECK(supervisor.safeWatchdogConfigMonitor.getContext() == c.expected);
        CHECK(supervisor.inLimpHomeState());
        if (!reported || (supervisor.safeWatchdogConfigMonitor.getContext() != c.expected))
        {
            std::printf("  case '%s': trace '%s', context 0x%x\n", c.name, ::test_trace::trace.c_str(),
                        static_cast<unsigned>(supervisor.safeWatchdogConfigMonitor.getContext()));
        }
    }
}

void testFieldsTheCheckMustIgnore()
{
    // TST (user mode after the fast test), FLG, RCS, ULK and the counter change legitimately while the watchdog runs
    SafeWatchdog watchdog;
    auto& supervisor = fresh(watchdog);
    wdogRegisters.CS = wdogRegisters.CS | WDOG_CS_FLG_MASK | WDOG_CS_RCS_MASK;
    wdogRegisters.CNT = 0x1234U;
    step(supervisor, watchdog);
    CHECK(::test_trace::trace == "W");
    CHECK(!supervisor.inLimpHomeState());
}

void testInterruptExpectationFollowsTheRequest()
{
    // the family check compares INT with what SafeWatchdog requested, not with a constant
    wdogRegisters    = WdogType{};
    wdogRegisters.CS = WDOG_CS_UPDATE_MASK | WDOG_CS_EN_MASK;
    CHECK(sv::checkWatchdogControl(false) == 0U);
    CHECK(sv::checkWatchdogControl(true) == sv::WATCHDOG_FIELD_INT);
    wdogRegisters.CS = wdogRegisters.CS | WDOG_CS_INT_MASK;
    CHECK(sv::checkWatchdogControl(false) == sv::WATCHDOG_FIELD_INT);
    CHECK(sv::checkWatchdogControl(true) == 0U);
}

void testOverServiceIsReported()
{
    SafeWatchdog watchdog;
    auto& supervisor = fresh(watchdog);
    step(supervisor, watchdog);
    ::safety::bsp::Watchdog::serviceWatchdog(); // a runaway calls the service
    ::test_trace::trace.clear();
    step(supervisor, watchdog);
    CHECK(::test_trace::trace == "EPRW"); // SYS-59: every reset writes its console line (P) first
    CHECK(supervisor.inLimpHomeState());
}

void testServiceBracketCloses()
{
    // cyclic() hits LEAVE; a second cyclic() without ENTER is a sequence deviation
    SafeWatchdog watchdog;
    auto& supervisor = fresh(watchdog);
    step(supervisor, watchdog);
    ::test_trace::trace.clear();
    watchdog.cyclic();
    CHECK(::test_trace::trace == "WEPR"); // SYS-59: every reset writes its console line (P) first
}
} // namespace

int main()
{
    testConfigurationOfEnablePasses();
    testEveryCorruptedFieldIsReported();
    testFieldsTheCheckMustIgnore();
    testInterruptExpectationFollowsTheRequest();
    testOverServiceIsReported();
    testServiceBracketCloses();
    std::printf("%d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
