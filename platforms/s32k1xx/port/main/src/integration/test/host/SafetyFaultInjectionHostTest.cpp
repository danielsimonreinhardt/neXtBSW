/* SYS-51: host test of the fault injection of the test build (SafetyFaultInjection.h, the real code behind the console
 * group "inject") against register models of the LPIT, the NVIC and the WDOG, together with the real configuration
 * checks of the safety task (safeSupervisionCheck/LpitDeadmanCheck.cpp and WatchdogControlCheck.cpp, SYS-41) and the
 * real deadman (LpitHaltDeadman.h, SYS-48) that sets up the configuration first.
 *
 * What it shows - the effect the board checks SYS-48 point 2 and SYS-45 B6 rely on:
 * - deadman: after LpitHaltDeadman::init() and the first retrigger the check is clean; each injection (tval, mier,
 *   dbgen, prio) makes the check report exactly the bit of that field (DEADMAN_CONFIGURATION_ERROR context), nothing
 *   else of the LPIT changes (channel 0 of the system time, TCTRL, the other fields), the fault survives the
 *   retrigger of the halt guard, and the MIER/MCR read-modify-write runs with all interrupts suspended; an
 *   implausible channel writes nothing;
 * - watchdog: from the control word the board read (WDOG_CS 0x25EB, board 2026-09-22 capture 16) the injection
 *   leaves exactly WATCHDOG_FIELD_INT (0x2) for the check - EN, CLK, TOVAL (the driver's own check), WIN, PRES,
 *   UPDATE, CMD32EN, TST unchanged; it follows the driver's sequence: unlock word to CNT with interrupts suspended,
 *   ULK awaited, one write to CS inside the 128 bus clock window, RCS awaited, no TOVAL/WIN write, no violation
 *   (which would be a reset on the chip); a pending FLG is not cleared; nothing is written when INT is already 0,
 *   UPDATE is 0, CMD32EN is 0 or ULK does not come; a missing RCS is reported.
 * The command around it (text, parsing, registration) is built only with NX_SAFETY_FAULT_INJECTION_COMMAND; that the
 * standard build has none of it is test_safety_fault_injection_guard.py.
 *
 * Build and run (zig c++, no hardware; tools/test -k safety-fault-injection):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -DNX_SAFE_SUPERVISION_CHECK_NO_WEAK_DEFAULT \
 *       -Iplatforms/s32k1xx/port/main/src/integration/test/host/faultInjectionStubs \
 *       -Iplatforms/s32k1xx/port/main/src/integration/test/host/deadmanStubs \
 *       -Iplatforms/s32k1xx/port/main/src/integration -Iplatforms/s32k1xx/hardFaultHandler/include \
 *       -Iplatforms/s32k1xx/port/safety/safeSupervisionCheck/include \
 *       platforms/s32k1xx/port/main/src/integration/test/host/SafetyFaultInjectionHostTest.cpp \
 *       platforms/s32k1xx/port/safety/safeSupervisionCheck/src/safeSupervisionCheck/LpitDeadmanCheck.cpp \
 *       platforms/s32k1xx/port/safety/safeSupervisionCheck/src/safeSupervisionCheck/WatchdogControlCheck.cpp \
 *       -o build/host/safetyfaultinjection.exe
 */

#include <cstdint>
#include <cstdio>

/* ------------------------------------------------------------------ register model of the safe outputs table */

namespace test_outputs
{
uint32_t pin = 0xFFFFFFFFU;

volatile uint32_t* reg(uint32_t const address)
{
    (void)address;
    return &pin;
}
} // namespace test_outputs

#define NX_SAFE_OUTPUT_REG(address) (::test_outputs::reg(address))

#include "LpitHaltDeadman.h"      // sets up the deadman as on the target
#include "SafetyFaultInjection.h" // the unit under test

extern "C" NxSafeOutput const nxSafeOutputs[] = {{0x400FF000U, 0U, 0U, 0U}, {0U, 0U, 0U, 0U}};

namespace test_trace
{
int lockDepth = 0;
}

LpitType lpitRegisters;
NvicModel nvicModel{};
WdogModel wdogModel;
WdogType wdogRegisters;

namespace
{
namespace sv = ::safety::supervision;
namespace fi = ::integration::faultinjection;
using ::integration::LpitHaltDeadman;

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

/* ------------------------------------------------------------------ LPIT model */

void setEnable(LpitRegister&, uint32_t const channels)
{
    for (uint32_t ch = 0U; ch < LPIT_TMR_COUNT; ++ch)
    {
        if ((channels & (1U << ch)) != 0U)
        {
            lpitRegisters.TMR[ch].TCTRL.value |= LPIT_TMR_TCTRL_T_EN_MASK;
        }
    }
}

void clearEnable(LpitRegister&, uint32_t const channels)
{
    for (uint32_t ch = 0U; ch < LPIT_TMR_COUNT; ++ch)
    {
        if ((channels & (1U << ch)) != 0U)
        {
            lpitRegisters.TMR[ch].TCTRL.value &= ~LPIT_TMR_TCTRL_T_EN_MASK;
        }
    }
}

void writeOneToClear(LpitRegister& reg, uint32_t const flags) { reg.value &= ~flags; }

// Records the interrupt lock at every MIER and MCR write (the read-modify-writes of the injection).
int lpitRmwWithoutLock = 0;

void plainWriteCheckingLock(LpitRegister& reg, uint32_t const v)
{
    if (::test_trace::lockDepth <= 0)
    {
        ++lpitRmwWithoutLock;
    }
    reg.value = v;
}

/** Reset values, what initSystemTimer() leaves (module on, channel 0 running), then the deadman as on the target:
 * init() of the halt guard and its first retrigger. */
void setUpDeadman()
{
    lpitRegisters.MCR.value  = 0U;
    lpitRegisters.MSR.value  = 0U;
    lpitRegisters.MIER.value = 0U;
    for (auto& tmr : lpitRegisters.TMR)
    {
        tmr.TVAL.value  = 0U;
        tmr.CVAL.value  = 0U;
        tmr.TCTRL.value = 0U;
    }
    lpitRegisters.SETTEN.write = &setEnable;
    lpitRegisters.CLRTEN.write = &clearEnable;
    lpitRegisters.MSR.write    = &writeOneToClear;
    lpitRegisters.MCR.write    = nullptr;
    lpitRegisters.MIER.write   = nullptr;
    nvicModel                  = NvicModel{};

    lpitRegisters.MCR                = LPIT_MCR_M_CEN_MASK;
    lpitRegisters.TMR[0].TVAL        = 0xFFFFFFFFU;
    lpitRegisters.TMR[0].TCTRL.value = LPIT_TMR_TCTRL_T_EN_MASK;
    lpitRegisters.MIER               = 0x1U;
    LpitHaltDeadman::init();
    LpitHaltDeadman::retrigger();
    // from here on the injection's writes to MIER/MCR are traced
    lpitRegisters.MCR.write  = &plainWriteCheckingLock;
    lpitRegisters.MIER.write = &plainWriteCheckingLock;
    lpitRmwWithoutLock       = 0;
    ::test_trace::lockDepth  = 0;
}

uint32_t checkDeadman() { return sv::checkLpitDeadman(LpitHaltDeadman::EXPECTED_CONFIGURATION); }

struct LpitSnapshot
{
    uint32_t mcr, mier, tval0, tctrl0, tval1, tctrl1, level1;
    bool enabled1;
};

LpitSnapshot snapshot()
{
    return {lpitRegisters.MCR,
            lpitRegisters.MIER,
            lpitRegisters.TMR[0].TVAL,
            lpitRegisters.TMR[0].TCTRL,
            lpitRegisters.TMR[1].TVAL,
            lpitRegisters.TMR[1].TCTRL,
            nvicModel.level[LPIT0_Ch1_IRQn],
            nvicModel.enabled[LPIT0_Ch1_IRQn]};
}

void testDeadmanFault(fi::DeadmanFault const fault, uint32_t const field)
{
    setUpDeadman();
    auto const& expected = LpitHaltDeadman::EXPECTED_CONFIGURATION;
    CHECK(checkDeadman() == 0U); // what the target runs with: clean
    LpitSnapshot const before = snapshot();
    uint32_t const valueBefore = fi::readDeadmanField(expected, fault);

    CHECK(fi::deadmanFieldOf(fault) == field);
    CHECK(fi::injectDeadmanFault(expected, fault));
    CHECK(::test_trace::lockDepth == 0);
    CHECK(lpitRmwWithoutLock == 0);
    CHECK(fi::readDeadmanField(expected, fault) != valueBefore);
    CHECK(checkDeadman() == field); // exactly the field: DEADMAN_CONFIGURATION_ERROR with this context

    // Only the one field changed: channel 0 of the system time, the channel's mode and the NVIC enable stay.
    LpitSnapshot const after = snapshot();
    CHECK(after.tval0 == before.tval0);
    CHECK(after.tctrl0 == before.tctrl0);
    CHECK(after.tctrl1 == before.tctrl1);
    CHECK(after.enabled1 == before.enabled1);
    CHECK((after.mcr & LPIT_MCR_M_CEN_MASK) != 0U);
    CHECK((after.mier & 0x1U) != 0U);
    CHECK((fault == fi::DeadmanFault::TIMER_VALUE) ? (after.tval1 == expected.timerValue + 1U)
                                                   : (after.tval1 == before.tval1));
    CHECK((fault == fi::DeadmanFault::IRQ_ENABLE) ? (after.mier == 0x1U) : (after.mier == before.mier));
    CHECK((fault == fi::DeadmanFault::DEBUG_ENABLE) ? (after.mcr == LPIT_MCR_M_CEN_MASK) : (after.mcr == before.mcr));
    CHECK((fault == fi::DeadmanFault::NVIC_PRIORITY) ? (after.level1 == expected.irqPriority + 1U)
                                                     : (after.level1 == before.level1));

    // The halt guard keeps retriggering every millisecond: the fault stays until the reset of the check.
    LpitHaltDeadman::retrigger();
    LpitHaltDeadman::retrigger();
    CHECK(checkDeadman() == field);
}

void testDeadmanInjection()
{
    testDeadmanFault(fi::DeadmanFault::TIMER_VALUE, sv::DEADMAN_FIELD_TIMER_VALUE);
    testDeadmanFault(fi::DeadmanFault::IRQ_ENABLE, sv::DEADMAN_FIELD_IRQ_ENABLE);
    testDeadmanFault(fi::DeadmanFault::DEBUG_ENABLE, sv::DEADMAN_FIELD_DEBUG_ENABLE);
    testDeadmanFault(fi::DeadmanFault::NVIC_PRIORITY, sv::DEADMAN_FIELD_NVIC_PRIORITY);
    // the default of "inject deadman" is the harmless one: bit 0x10 of the board checklist
    CHECK(fi::deadmanFieldOf(fi::DeadmanFault::TIMER_VALUE) == 0x10U);
}

void testDeadmanEdgeValues()
{
    setUpDeadman();
    // TVAL at the end of the range goes one tick down instead of wrapping to 0
    sv::LpitDeadmanConfiguration const top{1U, 0xFFFFFFFFU, LPIT0_Ch1_IRQn, 15U};
    lpitRegisters.TMR[1].TVAL = 0xFFFFFFFFU;
    CHECK(fi::injectDeadmanFault(top, fi::DeadmanFault::TIMER_VALUE));
    CHECK(lpitRegisters.TMR[1].TVAL == 0xFFFFFFFEU);
    // the lowest NVIC level (15 with 4 priority bits) goes one step up instead of out of range
    CHECK(fi::injectDeadmanFault(top, fi::DeadmanFault::NVIC_PRIORITY));
    CHECK(nvicModel.level[LPIT0_Ch1_IRQn] == 14U);

    // an implausible channel writes nothing (the check itself reports DEADMAN_FIELD_CONFIGURATION for it)
    setUpDeadman();
    LpitSnapshot const before = snapshot();
    sv::LpitDeadmanConfiguration const bad{LPIT_TMR_COUNT, 39999U, LPIT0_Ch1_IRQn, 2U};
    CHECK(!fi::injectDeadmanFault(bad, fi::DeadmanFault::IRQ_ENABLE));
    CHECK(!fi::injectDeadmanFault(bad, fi::DeadmanFault::TIMER_VALUE));
    LpitSnapshot const after = snapshot();
    CHECK(after.mier == before.mier);
    CHECK(after.tval1 == before.tval1);
    CHECK(sv::checkLpitDeadman(bad) == sv::DEADMAN_FIELD_CONFIGURATION);
}

/* ------------------------------------------------------------------ WDOG */

// WDOG_CS read on the board after the start (2026-09-22, capture 16): CMD32EN, RCS, CLK = 1 (LPO), EN, INT, UPDATE,
// TST = 1 (user mode after the fast test), WAIT, STOP - the word Watchdog::enableWatchdog(250, true) writes.
constexpr uint32_t BOARD_CS    = 0x25EBU;
constexpr uint32_t BOARD_TOVAL = 0x7D00U; // 250 ms at the 128 kHz LPO

void setUpWatchdog(uint32_t const cs)
{
    wdogModel         = WdogModel{};
    wdogModel.cs      = cs;
    wdogModel.toval   = BOARD_TOVAL;
    ::test_trace::lockDepth = 0;
}

void testWatchdogInterruptCleared()
{
    setUpWatchdog(BOARD_CS);
    CHECK(sv::checkWatchdogControl(true) == 0U); // the running configuration is clean
    fi::WatchdogInjection const result = fi::clearWatchdogInterrupt();
    CHECK(result.status == fi::WatchdogStatus::CLEARED);
    CHECK(result.csBefore == BOARD_CS);
    CHECK(result.csAfter == 0x25ABU); // the value the board checklist names
    CHECK(sv::checkWatchdogControl(true) == sv::WATCHDOG_FIELD_INT); // fields 0x2, nothing else
    // The fields of the driver's own check (EN, CLK, TOVAL) are untouched: WATCHDOG_FIELD_DRIVER_CHECK stays 0.
    CHECK((wdogModel.cs & WDOG_CS_EN_MASK) != 0U);
    CHECK((wdogModel.cs & WDOG_CS_CLK_MASK) == (BOARD_CS & WDOG_CS_CLK_MASK));
    CHECK(wdogModel.toval == BOARD_TOVAL);
    // Every configuration field but INT as before
    uint32_t const config = ~(WDOG_CS_INT_MASK | WDOG_CS_ULK_MASK | WDOG_CS_RCS_MASK | WDOG_CS_FLG_MASK);
    CHECK((wdogModel.cs & config) == (BOARD_CS & config));
    // The driver's sequence: one unlock, one CS write inside the window, all with interrupts suspended
    CHECK(wdogModel.unlocks == 1U);
    CHECK(wdogModel.csWrites == 1U);
    CHECK(wdogModel.tovalWrites == 0U);
    CHECK(wdogModel.violations == 0U);
    CHECK(wdogModel.writesUnlocked == 0U);
    CHECK(wdogModel.windowAtCsWrite <= WdogModel::WINDOW_BUS_CLOCKS);
    CHECK(!wdogModel.windowOpen); // the reconfiguration completed
    CHECK((wdogModel.cs & WDOG_CS_RCS_MASK) != 0U);
    CHECK((wdogModel.cs & WDOG_CS_ULK_MASK) == 0U);
    CHECK(::test_trace::lockDepth == 0);
}

void testWatchdogKeepsPendingFlag()
{
    setUpWatchdog(BOARD_CS | WDOG_CS_FLG_MASK);
    fi::WatchdogInjection const result = fi::clearWatchdogInterrupt();
    CHECK(result.status == fi::WatchdogStatus::CLEARED);
    CHECK((wdogModel.lastCsWritten & WDOG_CS_FLG_MASK) == 0U); // no write one to FLG
    CHECK((wdogModel.cs & WDOG_CS_FLG_MASK) != 0U);
}

void testWatchdogNothingWritten()
{
    struct Case
    {
        uint32_t cs;
        fi::WatchdogStatus status;
    };
    Case const cases[] = {
        {BOARD_CS & ~WDOG_CS_INT_MASK, fi::WatchdogStatus::ALREADY_CLEAR},
        {BOARD_CS & ~WDOG_CS_UPDATE_MASK, fi::WatchdogStatus::NOT_UPDATABLE},
        {BOARD_CS & ~WDOG_CS_CMD32EN_MASK, fi::WatchdogStatus::NOT_32BIT},
    };
    for (Case const& c : cases)
    {
        setUpWatchdog(c.cs);
        fi::WatchdogInjection const result = fi::clearWatchdogInterrupt();
        CHECK(result.status == c.status);
        CHECK(wdogModel.unlocks == 0U);
        CHECK(wdogModel.csWrites == 0U);
        CHECK(wdogModel.violations == 0U);
        CHECK(wdogModel.cs == c.cs);
        CHECK(::test_trace::lockDepth == 0);
    }

    // ULK never comes: no write to CS (a write outside the window would reset the chip)
    setUpWatchdog(BOARD_CS);
    wdogModel.ulkDelay = 0xFFFFFFFFU;
    fi::WatchdogInjection const noUnlock = fi::clearWatchdogInterrupt();
    CHECK(noUnlock.status == fi::WatchdogStatus::UNLOCK_TIMEOUT);
    CHECK(wdogModel.csWrites == 0U);
    CHECK(wdogModel.violations == 0U);
    CHECK(::test_trace::lockDepth == 0);

    // RCS never comes: written, reported
    setUpWatchdog(BOARD_CS);
    wdogModel.rcsDelay = 0xFFFFFFFFU;
    fi::WatchdogInjection const noRcs = fi::clearWatchdogInterrupt();
    CHECK(noRcs.status == fi::WatchdogStatus::RECONFIG_TIMEOUT);
    CHECK(wdogModel.csWrites == 1U);
    CHECK(::test_trace::lockDepth == 0);
}

void testModelRejectsLateWrite()
{
    // Counter-check of the model: a CS write after the window is a violation, so the passing tests above mean
    // something. Unlock, then burn more than 128 bus clocks with reads before writing.
    setUpWatchdog(BOARD_CS);
    WDOG->CNT = FEATURE_WDOG_UNLOCK_VALUE;
    for (int i = 0; i < 40; ++i)
    {
        (void)static_cast<uint32_t>(WDOG->CS);
    }
    WDOG->CS = BOARD_CS & ~WDOG_CS_INT_MASK;
    CHECK(wdogModel.violations == 1U);
    CHECK((wdogModel.cs & WDOG_CS_INT_MASK) != 0U);
}
} // namespace

int main()
{
    testDeadmanInjection();
    testDeadmanEdgeValues();
    testWatchdogInterruptCleared();
    testWatchdogKeepsPendingFlag();
    testWatchdogNothingWritten();
    testModelRejectsLateWrite();

    std::printf("%d checks, %d failures\n", checks, failures);
    return failures == 0 ? 0 : 1;
}
