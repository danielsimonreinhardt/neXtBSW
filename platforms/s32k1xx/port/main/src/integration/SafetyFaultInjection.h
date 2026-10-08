#pragma once

/* SYS-51: fault injection into two supervision peripherals of the S32K1 family, for the board checks that a J-Link
 * cannot do (board 2026-09-22, session e2, captures 17/18): a J-Link write to LPIT0_MCR while the application runs
 * read back unchanged at once, and the 128 bus clocks after the WDOG unlock cannot be hit from the probe. The
 * console group "inject" (SafetyFaultInjectionCommand.cpp) calls these functions; both exist only in a test build
 * with the CMake option NX_SAFETY_FAULT_INJECTION_COMMAND (default OFF, never in the standard build - guarded by
 * test/test_safety_fault_injection_guard.py).
 *
 * What each injection must produce is the reaction the checks already specify:
 * - deadman (SYS-41/SYS-48, SM-06): one field of the LPIT deadman channel differs from the configuration its owner
 *   set up (LpitHaltDeadman::EXPECTED_CONFIGURATION, handed to the check by deadmanExpectedConfiguration()) -> the
 *   safety task reports DEADMAN_CONFIGURATION_ERROR with that field's bit within one cycle, S1 + S2 (reset);
 * - watchdog (SYS-41, SM-02, fmea.md B6): WDOG_CS[INT] cleared while the watchdog runs, every other field kept ->
 *   SAFE_WATCHDOG_CONFIGURATION_ERROR with fields 0x2 at the next watchdog check (every WATCHDOG_CYCLIC_COUNTER
 *   safety cycles), S1 + S2 (reset).
 *
 * Header only and private to the board integration, so the host test runs exactly this code against register models
 * together with the real checks (tools/test -k safety-fault-injection). Register access only; the family neutral
 * supervisor (ecu/safety/safeSupervisor) is not touched. */

#include "interrupt_manager.h"
#include "mcu/mcu.h"

#include <interrupts/SuspendResumeAllInterruptsScopedLock.h>
#include <safeSupervisionCheck/LpitDeadmanCheck.h>
#include <safeSupervisionCheck/WatchdogControlCheck.h>

#include <cstdint>

namespace integration
{
namespace faultinjection
{

/** Field of the deadman configuration an injection falsifies (each one bit of LpitDeadmanField). */
enum class DeadmanFault : uint8_t
{
    TIMER_VALUE,   ///< TVAL one tick longer (default: harmless, the time-out grows by one LPIT tick)
    IRQ_ENABLE,    ///< MIER bit of the channel cleared (the deadman expires without an interrupt)
    DEBUG_ENABLE,  ///< MCR[DBG_EN] cleared (the counter would stop while the core is halted)
    NVIC_PRIORITY, ///< NVIC level of the channel's interrupt one step off
};

/** The LpitDeadmanField bit the configuration check reports for `fault`. */
constexpr uint32_t deadmanFieldOf(DeadmanFault const fault)
{
    return (fault == DeadmanFault::IRQ_ENABLE)      ? ::safety::supervision::DEADMAN_FIELD_IRQ_ENABLE
           : (fault == DeadmanFault::DEBUG_ENABLE)  ? ::safety::supervision::DEADMAN_FIELD_DEBUG_ENABLE
           : (fault == DeadmanFault::NVIC_PRIORITY) ? ::safety::supervision::DEADMAN_FIELD_NVIC_PRIORITY
                                                    : ::safety::supervision::DEADMAN_FIELD_TIMER_VALUE;
}

/**
 * Current value of the field `fault` falsifies (MIER and MCR masked to the one bit, TVAL, NVIC level). The channel
 * must be plausible (below LPIT_TMR_COUNT).
 */
inline uint32_t readDeadmanField(
    ::safety::supervision::LpitDeadmanConfiguration const& expected, DeadmanFault const fault)
{
    switch (fault)
    {
        case DeadmanFault::IRQ_ENABLE:
        {
            return LPIT0->MIER & (static_cast<uint32_t>(1U) << expected.channel);
        }
        case DeadmanFault::DEBUG_ENABLE:
        {
            return LPIT0->MCR & LPIT_MCR_DBG_EN_MASK;
        }
        case DeadmanFault::NVIC_PRIORITY:
        {
            return NVIC_GetPriority(expected.irq);
        }
        case DeadmanFault::TIMER_VALUE:
        default:
        {
            return LPIT0->TMR[expected.channel].TVAL;
        }
    }
}

/**
 * Falsifies one field of the deadman channel `expected` describes. Only that field changes; the channel keeps
 * running (the retrigger of the halt guard does not touch any of these fields, so the fault stays until the reset
 * the check causes). With interrupts suspended because MIER and MCR are read-modify-write.
 * \return false: nothing written (channel implausible - the check reports that without any injection)
 */
inline bool injectDeadmanFault(
    ::safety::supervision::LpitDeadmanConfiguration const& expected, DeadmanFault const fault)
{
    if (expected.channel >= LPIT_TMR_COUNT)
    {
        return false;
    }
    uint32_t const channelMask = static_cast<uint32_t>(1U) << expected.channel;
    ::interrupts::SuspendResumeAllInterruptsScopedLock const lock;
    switch (fault)
    {
        case DeadmanFault::IRQ_ENABLE:
        {
            LPIT0->MIER = LPIT0->MIER & ~channelMask;
            break;
        }
        case DeadmanFault::DEBUG_ENABLE:
        {
            LPIT0->MCR = LPIT0->MCR & ~LPIT_MCR_DBG_EN_MASK; // SW_RST reads 0: the write resets nothing
            break;
        }
        case DeadmanFault::NVIC_PRIORITY:
        {
            // One level lower (higher only at the end of the range) - the direction the check exists for: below
            // the RTOS syscall boundary an RTOS critical section would delay the walk of the safe outputs table.
            uint32_t const levels = static_cast<uint32_t>(1U) << __NVIC_PRIO_BITS;
            uint32_t const level  = ((expected.irqPriority + 1U) < levels) ? (expected.irqPriority + 1U)
                                                                           : (expected.irqPriority - 1U);
            SYS_SetPriority(expected.irq, static_cast<uint8_t>(level));
            break;
        }
        case DeadmanFault::TIMER_VALUE:
        default:
        {
            // One tick longer (shorter only at the end of the range): the retrigger reloads TVAL, so the deadman
            // keeps working with a time-out one LPIT tick off - no false expiry while the check reacts.
            LPIT0->TMR[expected.channel].TVAL = (expected.timerValue != UINT32_MAX) ? (expected.timerValue + 1U)
                                                                                    : (expected.timerValue - 1U);
            break;
        }
    }
    return true;
}

enum class WatchdogStatus : uint8_t
{
    CLEARED,         ///< CS[INT] cleared, every other field as before
    ALREADY_CLEAR,   ///< CS[INT] was 0 already - nothing written
    NOT_UPDATABLE,   ///< CS[UPDATE] = 0: the WDOG cannot be reconfigured before the next reset - nothing written
    NOT_32BIT,       ///< CS[CMD32EN] = 0: the 32-bit unlock word would be an invalid write (immediate reset)
    UNLOCK_TIMEOUT,  ///< CS[ULK] did not come - nothing written
    RECONFIG_TIMEOUT ///< CS written, CS[RCS] did not come within the wait
};

struct WatchdogInjection
{
    WatchdogStatus status;
    uint32_t csBefore;
    uint32_t csAfter;
};

/** Polls of ULK and RCS before giving up. Both come within a few WDOG clocks; the bound only keeps a broken WDOG
 * from hanging the core with interrupts suspended (the running watchdog would reset it then anyway). */
constexpr uint32_t WDOG_WAIT_POLLS = 10000U;

/* CS bits that are no configuration: FLG is write one to clear (a pending flag is kept), ULK and RCS are status. */
constexpr uint32_t WDOG_CS_STATUS_MASK = WDOG_CS_FLG_MASK | WDOG_CS_ULK_MASK | WDOG_CS_RCS_MASK;

/** Polls CS until `bit` is set, at most WDOG_WAIT_POLLS times. */
inline bool waitForWatchdogStatus(uint32_t const bit)
{
    for (uint32_t polls = 0U; polls < WDOG_WAIT_POLLS; ++polls)
    {
        if ((WDOG->CS & bit) != 0U)
        {
            return true;
        }
    }
    return false;
}

/**
 * Clears WDOG_CS[INT] while the watchdog runs, the way the BSP driver reconfigures the WDOG
 * (safeBspMcuWatchdog Watchdog::enableWatchdog, external/openbsw): all interrupts suspended, the unlock word to CNT,
 * wait for CS[ULK], write CS, wait for CS[RCS]. The new control word is computed from CS **before** the unlock, so
 * the 128 bus clocks of the unlock window ([RM] 23.3.2) only hold the poll of ULK and the one store to CS. TOVAL and
 * WIN are not written: in 32-bit command mode the write to CS completes the reconfiguration. Every field except INT
 * keeps its value (EN, CLK, UPDATE, CMD32EN, STOP, WAIT, DBG, TST, PRES, WIN), so the check sees exactly
 * WATCHDOG_FIELD_INT. A reconfiguration also restarts the counter - no watchdog reset from the injection itself.
 */
inline WatchdogInjection clearWatchdogInterrupt()
{
    ::interrupts::SuspendResumeAllInterruptsScopedLock const lock;
    uint32_t const cs = WDOG->CS;
    WatchdogInjection result{WatchdogStatus::CLEARED, cs, cs};
    if ((cs & WDOG_CS_INT_MASK) == 0U)
    {
        result.status = WatchdogStatus::ALREADY_CLEAR;
        return result;
    }
    if ((cs & WDOG_CS_UPDATE_MASK) == 0U)
    {
        result.status = WatchdogStatus::NOT_UPDATABLE;
        return result;
    }
    if ((cs & WDOG_CS_CMD32EN_MASK) == 0U)
    {
        result.status = WatchdogStatus::NOT_32BIT;
        return result;
    }
    uint32_t const controlStatus = cs & ~(WDOG_CS_INT_MASK | WDOG_CS_STATUS_MASK);

    WDOG->CNT = FEATURE_WDOG_UNLOCK_VALUE;
    if (!waitForWatchdogStatus(WDOG_CS_ULK_MASK))
    {
        result.status  = WatchdogStatus::UNLOCK_TIMEOUT;
        result.csAfter = WDOG->CS;
        return result;
    }
    WDOG->CS                = controlStatus;
    bool const reconfigured = waitForWatchdogStatus(WDOG_CS_RCS_MASK);
    result.csAfter          = WDOG->CS;
    if (!reconfigured)
    {
        result.status = WatchdogStatus::RECONFIG_TIMEOUT;
    }
    return result;
}

} // namespace faultinjection
} // namespace integration
