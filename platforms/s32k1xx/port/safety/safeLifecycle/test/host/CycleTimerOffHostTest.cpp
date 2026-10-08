/* SYS-52 (board finding 2026-09-22, SYS-40 check B7(a) of doc/safety/fmea.md): the time base of the safety cycle
 * switched off from outside - LPTMR0_CSR written with 0x4 by the debugger (TEN cleared, TFC kept) while the ECU runs.
 * End-to-end on the host with the real code of the whole path: SafetyManager (safeLifecycle/SafetyManager.cpp), the
 * real S32K1 timer (safeSupervisionCheck/LptmrCycleTimer.cpp) against a register model that behaves like [RM13]
 * chapter 49 (below), the real period check (safeWatchdog/CyclePeriodCheck.cpp) and the real reaction
 * (safeSupervisor/SafeSupervisor.cpp). The other sub-checks are the stubs of SafetyCycleHostTest (stubs/).
 *
 * What it shows:
 * - TEN cleared between two cycles is SAFETY_CYCLE_PERIOD_VIOLATION kind 3 with the fields 0x2 at the very next
 *   cycle, before any other check of that cycle: log line, synchronous console line (SYS-52), reset - so the
 *   reaction comes one period (plus the tolerated lateness) after the fault, far inside the watchdog time-out;
 * - the cycle that finds it does not touch CNR at all (no latch write, no read) and accesses the LPTMR a bounded
 *   number of times - there is no loop that could wait for a stopped counter (hypothesis (a) of SYS-52);
 * - the configuration is checked before the counter is used (hypothesis (b)): the stopped counter never gets to
 *   the interval or window comparison first;
 * - the same with TEN cleared in the middle of a window, with TCF set before (the value 0x85 read on the board);
 * - TEN cleared and set again (someone restarts the timer, CNR from 0) is still caught: kind 1 at the next cycle;
 * - PCC_LPTMR0[CGC] cleared: kind 3 fields 0x1 and no LPTMR access at all (it would be a bus fault);
 *   SIM_LPOCLKS[LPO1KCLKEN] cleared: kind 3 fields 0x10.
 *
 * Register model ([RM13] 49.4.1.2, 49.5.4): writing CSR with TEN 0 resets the counter and TCF; TCF is write-1-to-
 * clear; the counter runs only with TEN set, PSR = LPO1K and LPO1KCLKEN set; a CNR write latches the counter into
 * the readable register. Every LPTMR access is logged, and one with the clock gate off is counted as a bus fault.
 *
 * Build and run (zig c++, no hardware):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -Iplatforms/s32k1xx/port/safety/safeLifecycle/test/host/stubs \
 *       -Iplatforms/s32k1xx/port/safety/safeLifecycle/include \
 *       -Iplatforms/s32k1xx/port/safety/safeSupervisionCheck/test/host/lptmrStubs \
 *       -Iecu/safety/safeSupervisor/include -Iecu/safety/safeSupervisor/test/host/stubs \
 *       -isystem external/openbsw/libs/safety/safeMonitor/include \
 *       platforms/s32k1xx/port/safety/safeLifecycle/test/host/CycleTimerOffHostTest.cpp \
 *       platforms/s32k1xx/port/safety/safeLifecycle/src/safeLifecycle/SafetyManager.cpp \
 *       platforms/s32k1xx/port/safety/safeSupervisionCheck/src/safeSupervisionCheck/LptmrCycleTimer.cpp \
 *       ecu/safety/safeWatchdog/src/safeWatchdog/CyclePeriodCheck.cpp \
 *       -o build/host/cycletimeroff.exe
 */

#include <cstdint>
#include <cstdio>
#include <string>

namespace test_trace
{
std::string trace;
std::string console;
int lockDepth = 0;

void add(char const c) { trace.push_back(c); }

void print(char const* const format, char const* const event)
{
    add('P');
    char line[160];
    (void)std::snprintf(line, sizeof(line), format, event);
    console += line;
}
} // namespace test_trace

#define NX_SAFE_SUPERVISOR_HOLDING()              (false)
#define NX_SAFE_SUPERVISOR_CONSOLE(format, event) (::test_trace::print(format, event))

extern "C" void softwareSystemReset(void) { ::test_trace::add('R'); }

#include "../../../../../../../ecu/safety/safeSupervisor/src/safeSupervisor/SafeSupervisor.cpp" // NOLINT

#include <mcu/mcu.h>
#include <safeLifecycle/SafetyManager.h>
#include <safeSupervisionCheck/LpitDeadmanCheck.h>
#include <safeSupervisionCheck/LptmrCycleTimer.h>
#include <safeWatchdog/CyclePeriodCheck.h>

// ---- register model of LPTMR0, PCC_LPTMR0 and SIM_LPOCLKS (lptmrStubs/mcu/mcu.h) ----

namespace lptmr_model
{
std::string log;
uint32_t running  = 0U;
int busFaults     = 0; // LPTMR accesses with PCC_LPTMR0[CGC] cleared
int accesses      = 0;
} // namespace lptmr_model

LptmrType lptmrRegisters{{'C', 0U}, {'P', 0U}, {'M', 0U}, {'N', 0U}};
PccType pccRegisters{};
SimType simRegisters{};

namespace
{
void access(char const kind, char const name)
{
    ++::lptmr_model::accesses;
    if ((pccRegisters.PCCn[PCC_LPTMR0_INDEX] & PCC_PCCn_CGC_MASK) == 0U)
    {
        ++::lptmr_model::busFaults;
    }
    ::lptmr_model::log += kind;
    ::lptmr_model::log += name;
    ::lptmr_model::log += ' ';
}

bool counting()
{
    return ((lptmrRegisters.CSR.value & LPTMR_CSR_TEN_MASK) != 0U)
           && ((lptmrRegisters.PSR.value & LPTMR_PSR_PCS_MASK) == 1U)
           && ((simRegisters.LPOCLKS & SIM_LPOCLKS_LPO1KCLKEN_MASK) != 0U);
}

/** n ticks of LPO1K_CLK: the counter follows only while it runs; TCF when it passes the compare value. */
void tick(uint32_t const n)
{
    for (uint32_t i = 0U; i < n; ++i)
    {
        if (counting())
        {
            if (::lptmr_model::running == lptmrRegisters.CMR.value)
            {
                lptmrRegisters.CSR.value |= LPTMR_CSR_TCF_MASK;
            }
            ::lptmr_model::running = (::lptmr_model::running + 1U) & 0xFFFFU;
        }
    }
}
} // namespace

Reg& Reg::operator=(uint32_t const v)
{
    access('w', name);
    if (name == 'N')
    {
        value = ::lptmr_model::running & 0xFFFFU; // write latches the counter ([RM13] 49.5.4)
    }
    else if (name == 'C')
    {
        uint32_t const tcf = ((value & LPTMR_CSR_TCF_MASK) != 0U) && ((v & LPTMR_CSR_TCF_MASK) == 0U)
                                 ? LPTMR_CSR_TCF_MASK
                                 : 0U; // write 1 clears
        value = (v & ~LPTMR_CSR_TCF_MASK) | tcf;
        if ((v & LPTMR_CSR_TEN_MASK) == 0U)
        {
            // [RM13] 49.4.1.2: TEN clear resets the internal logic, CNR and TCF
            value &= ~LPTMR_CSR_TCF_MASK;
            ::lptmr_model::running = 0U;
        }
    }
    else
    {
        value = v;
    }
    return *this;
}

Reg::operator uint32_t() const
{
    access('r', name);
    return value;
}

// ---- the rest of the safety cycle (stubs of SafetyCycleHostTest) ----

namespace test_cycle
{
bool gateLocked = true;
} // namespace test_cycle

namespace safety
{
SafetyEventMailbox rteTimingMailbox;

namespace supervision
{
namespace test
{
LpitDeadmanConfiguration const* expected = nullptr;
uint32_t result                          = 0U;
} // namespace test
} // namespace supervision
} // namespace safety

extern ::safety::CyclePeriodCheck safetyCyclePeriodCheck;

namespace
{
using ::safety::CyclePeriodCheck;
using ::safety::SafeSupervisor;
using ::safety::SafetyManager;

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

/** The trace of the reaction to a period violation: log line, synchronous console line, reset (no safe outputs
 * table in this build, PLATFORM_SUPPORT_IO is not defined). */
constexpr char const* REACTION = "EPR";

/** Power-on state of the registers, then SafetyManager::init() as the lifecycle runs it (starts the timer). */
SafeSupervisor& start(SafetyManager& manager)
{
    lptmrRegisters                          = LptmrType{{'C', 0U}, {'P', 0U}, {'M', 0U}, {'N', 0U}};
    pccRegisters.PCCn[PCC_LPTMR0_INDEX]     = PCC_PCCn_CGC_MASK;
    simRegisters.LPOCLKS                    = SIM_LPOCLKS_LPO1KCLKEN_MASK;
    ::lptmr_model::running                  = 0U;
    ::lptmr_model::busFaults                = 0;
    ::safety::safeSupervisorConstructor.construct();
    auto& supervisor = SafeSupervisor::getInstance();
    supervisor.leaveLimpHome();
    ::test_cycle::gateLocked = true;
    manager.init();
    ::test_trace::trace.clear();
    ::test_trace::console.clear();
    return supervisor;
}

/** One safety cycle `ticks` LPO1K ticks after the previous one; returns its trace and leaves its LPTMR log. */
std::string cycleAfter(SafetyManager& manager, uint32_t const ticks)
{
    tick(ticks);
    ::test_trace::trace.clear();
    ::lptmr_model::log.clear();
    ::lptmr_model::accesses = 0;
    manager.cyclic();
    return ::test_trace::trace;
}

/** `cycles` healthy cycles of 10 ticks; true if none of them reported anything. */
bool healthy(SafetyManager& manager, int const cycles)
{
    bool quiet = true;
    for (int k = 0; k < cycles; ++k)
    {
        std::string const t = cycleAfter(manager, 10U);
        quiet               = quiet && (t.find('E') == std::string::npos) && (t.find('R') == std::string::npos);
    }
    return quiet;
}

/** What the debugger did on the board: "w4 0x40040000 0x00000004" (TFC kept, TEN and everything else 0). */
void debuggerWritesCsr(uint32_t const value)
{
    lptmrRegisters.CSR = value;
    ::lptmr_model::log.clear();
}

void checkTimerConfigurationReport(SafeSupervisor& supervisor, uint32_t const fields)
{
    auto const context = supervisor.safetyCyclePeriodMonitor.getContext();
    CHECK(CyclePeriodCheck::kindOf(context) == CyclePeriodCheck::TIMER_CONFIGURATION);
    CHECK(CyclePeriodCheck::valueOf(context) == fields);
    CHECK(supervisor.inLimpHomeState());
}

void testStartedTimerRunsAndStaysQuiet()
{
    SafetyManager manager;
    auto& supervisor = start(manager);
    CHECK((lptmrRegisters.CSR.value & LPTMR_CSR_TEN_MASK) != 0U);
    CHECK(healthy(manager, 40));
    CHECK(!supervisor.inLimpHomeState());
    // a healthy cycle latches and reads CNR once: write N, read N
    CHECK(::lptmr_model::log.find("wN rN") != std::string::npos);
    CHECK((lptmrRegisters.CSR.value & LPTMR_CSR_TCF_MASK) != 0U); // TCF set like on the board (CSR read 0x85)
}

void testTenClearedByTheDebuggerIsKind3AtTheNextCycle()
{
    SafetyManager manager;
    auto& supervisor = start(manager);
    CHECK(healthy(manager, 25));
    CHECK(lptmrRegisters.CSR.value == (LPTMR_CSR_TCF_MASK | LPTMR_CSR_TFC_MASK | LPTMR_CSR_TEN_MASK)); // 0x85
    debuggerWritesCsr(0x4U);
    CHECK(lptmrRegisters.CSR.value == 0x4U);
    CHECK(::lptmr_model::running == 0U);
    std::string const t = cycleAfter(manager, 10U);
    CHECK(t.substr(0, 3) == REACTION); // first thing of the cycle, before the memory check
    CHECK(t.find('m') > t.find('R')); // on the target the reset ends the cycle here
    checkTimerConfigurationReport(supervisor, ::safety::supervision::CYCLE_TIMER_FIELD_ENABLE);
    CHECK(::test_trace::console
          == "\r\nSAFETY RESET (SAFETY_CYCLE_PERIOD_VIOLATION, kind 3, value 2): outputs off\r\n"); // SYS-59: CR LF first
    // no latch, no read of the stopped counter - nothing to wait for; a bounded number of register reads
    CHECK(::lptmr_model::log.find('N') == std::string::npos);
    CHECK(::lptmr_model::log.find('w') == std::string::npos);
    CHECK(::lptmr_model::accesses <= 2); // CSR and PSR
    CHECK(::lptmr_model::busFaults == 0);
}

void testTenClearedInTheMiddleOfAWindow()
{
    // Every phase of the window (the watchdog step comes every CYCLE_PERIOD_WINDOW cycles): the report is always
    // at the first cycle after the write, never at the end of the window.
    for (uint32_t phase = 0U; phase < SafetyManager::CYCLE_PERIOD_WINDOW; ++phase)
    {
        SafetyManager manager;
        auto& supervisor = start(manager);
        CHECK(healthy(manager, static_cast<int>((2U * SafetyManager::CYCLE_PERIOD_WINDOW) + phase)));
        debuggerWritesCsr(0x4U);
        std::string const t = cycleAfter(manager, 7U); // any distance: the configuration comes first
        CHECK(t.substr(0, 3) == REACTION);
        checkTimerConfigurationReport(supervisor, ::safety::supervision::CYCLE_TIMER_FIELD_ENABLE);
    }
}

void testDetectionComesLongBeforeTheWatchdog()
{
    // The fault is seen by the first cycle after it: at most one period plus the tolerated lateness later. The
    // watchdog would only fire after its time-out - SYS-40's bound of three service intervals leaves it far away.
    constexpr uint32_t latestReportMs = SafetyManager::CYCLE_PERIOD_MS + SafetyManager::CYCLE_LATENESS_MS;
    static_assert(latestReportMs < NX_MODULE_SAFELIFECYCLE_PARAM_WATCHDOG_TIMEOUT_MS, "report before the watchdog");
    CHECK((latestReportMs * 10U) < NX_MODULE_SAFELIFECYCLE_PARAM_WATCHDOG_TIMEOUT_MS); // 12 ms against 270 ms here
}

void testTenClearedAndSetAgainIsStillCaught()
{
    // Someone restarts the timer (TEN 0, then 1): CNR starts again from 0 - the next interval wraps around and is
    // far above the bound (kind 1), even though the configuration looks right again.
    SafetyManager manager;
    auto& supervisor = start(manager);
    CHECK(healthy(manager, 30));
    debuggerWritesCsr(0x4U);
    debuggerWritesCsr(0x5U);
    std::string const t = cycleAfter(manager, 10U);
    CHECK(t.substr(0, 3) == REACTION);
    auto const context = supervisor.safetyCyclePeriodMonitor.getContext();
    CHECK(CyclePeriodCheck::kindOf(context) == CyclePeriodCheck::INTERVAL_TOO_LONG);
}

void testClockGateClearedIsKind3WithoutAccess()
{
    SafetyManager manager;
    auto& supervisor = start(manager);
    CHECK(healthy(manager, 12));
    pccRegisters.PCCn[PCC_LPTMR0_INDEX] = 0U;
    std::string const t = cycleAfter(manager, 10U);
    CHECK(t.substr(0, 3) == REACTION);
    checkTimerConfigurationReport(supervisor, ::safety::supervision::CYCLE_TIMER_FIELD_CLOCK_GATE);
    CHECK(::lptmr_model::accesses == 0);
    CHECK(::lptmr_model::busFaults == 0);
}

void testLpo1kOffIsKind3()
{
    SafetyManager manager;
    auto& supervisor = start(manager);
    CHECK(healthy(manager, 12));
    simRegisters.LPOCLKS = 0U;
    std::string const t = cycleAfter(manager, 10U);
    CHECK(t.substr(0, 3) == REACTION);
    checkTimerConfigurationReport(supervisor, ::safety::supervision::CYCLE_TIMER_FIELD_LPO1K);
    CHECK(::lptmr_model::log.find('N') == std::string::npos);
}
} // namespace

int main()
{
    testStartedTimerRunsAndStaysQuiet();
    testTenClearedByTheDebuggerIsKind3AtTheNextCycle();
    testTenClearedInTheMiddleOfAWindow();
    testDetectionComesLongBeforeTheWatchdog();
    testTenClearedAndSetAgainIsStillCaught();
    testClockGateClearedIsKind3WithoutAccess();
    testLpo1kOffIsKind3();
    std::printf("%d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
