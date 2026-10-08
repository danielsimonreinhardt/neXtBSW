#pragma once

/* IO-32: the deadman timer of the debug halt guard on the S32K1 family (moved out of DebugHaltGuardSystem.h with
 * SYS-48, so that a host test can run its init() against a register model and against the configuration check of
 * SYS-41; the guard, the interrupt vector and the strong deadmanExpectedConfiguration() stay in
 * DebugHaltGuardSystem.h). Header only, private to the board integration. */

#include "hardFaultHandler/safeOutputs.h"
#include "interrupt_manager.h"
#include "interrupts/interruptPriorities.h"
#include "mcu/mcu.h"

#include <bsp/clock/boardClock.h>
#include <safeSupervisionCheck/LpitDeadmanCheck.h>

#include <cstdint>

namespace integration
{

#ifndef NX_CLOCK_LPIT_HZ
#error "the halt deadman counts LPIT ticks and needs NX_CLOCK_LPIT_HZ (mcudb clock.pcc.LPIT, generated boardClock.h)"
#endif

/**
 * IO-32: deadman timer of the halt detection - an LPIT channel that keeps counting while the core is halted.
 *
 * Family part of the pattern "a timer channel that runs while the core is halted, retriggered by software, with an
 * interrupt". Other families supply their own timer for it: S32K3 PIT with MCR[FRZ] = 0, STM32 a timer whose
 * DBGMCU freeze bit stays clear, TI RM4x a timer outside the RTI (RTIGCTRL.COS applies to both RTI counters).
 * Only this class and the vector name are family specific; the logic in DebugHaltGuard.h is not.
 *
 * Resources: LPIT channel 1. Channel 0 is the free running system time of the ARMv6-M port (SystemTimer.cpp),
 * which initSystemTimer() starts on every derivative together with MCR[M_CEN]; this class only adds DBG_EN with a
 * read-modify-write, so it must run after initSystemTimer(). MCR[DBG_EN] is common to all channels ([RM] Rev. 13
 * 48.5.2 p. 1546): on ARMv6-M the system time therefore keeps counting through a halt as well - wall clock time,
 * and only in projects that have a power stage (user decision 2026-09-19, question 5).
 *
 * Clock: the LPIT functional clock of the generated clock plan (mcudb clock.pcc.LPIT, NX_CLOCK_LPIT_HZ); the idle
 * clock switch (ARCH-14) does not touch it. In STOP modes the LPIT stops with DOZE_EN = 0 - so does the retrigger,
 * hence no false trigger.
 *
 * All state is static: the interrupt handler below is a free function and there is exactly one deadman.
 */
class LpitHaltDeadman
{
public:
    /** Longest standstill of the software that is not reported. Constant of the mechanism, not a project value. */
    static constexpr uint32_t TIMEOUT_MS = 10U;
    static constexpr uint8_t CHANNEL     = 1U;
    static constexpr uint32_t CHANNEL_MASK = 1UL << CHANNEL;

    // Down counter: the period is TVAL + 1 ticks ([RM] 48.5.6 p. 1541)
    static constexpr uint32_t TIMEOUT_TICKS = (NX_CLOCK_LPIT_HZ / 1000U) * TIMEOUT_MS;
    static_assert(TIMEOUT_TICKS > 1U, "LPIT clock too slow for the halt deadman timeout");
    static_assert(
        (NX_CLOCK_LPIT_HZ % 1000U) == 0U, "LPIT clock is no whole kHz: the deadman timeout would not be exact");

    /* Interrupt level above the RTOS syscall boundary (user decision 2026-09-19, question 4): the handler uses no
     * RTOS service and must not be delayed by an RTOS critical section. On ARMv6-M there is no such level -
     * NX_IRQ_MAX_SYSCALL_LEVEL must be 0 there and the kernel masks everything with PRIMASK (nxNvicPriority.h), so
     * the handler takes the highest level and an RTOS critical section does delay it by its own length. */
    static constexpr unsigned LEVEL = (NX_IRQ_MAX_SYSCALL_LEVEL > 0U) ? (NX_IRQ_MAX_SYSCALL_LEVEL - 1U) : 0U;

    /**
     * SYS-48: the configuration init() writes, as the safety task re-checks it in every cycle (SYS-41, SM-06,
     * safeSupervisionCheck/LpitDeadmanCheck.h). init() takes TVAL, the vector and its level from here, so the two
     * cannot drift apart; DebugHaltGuardSystem.h hands it to the check (deadmanExpectedConfiguration()).
     */
    static constexpr ::safety::supervision::LpitDeadmanConfiguration EXPECTED_CONFIGURATION{
        CHANNEL, TIMEOUT_TICKS - 1U, LPIT0_Ch1_IRQn, LEVEL};

    /** Registers and vector of the channel; the channel itself is armed by the first retrigger(), not here. */
    static void init()
    {
        LPIT0->MCR                = LPIT0->MCR | LPIT_MCR_DBG_EN_MASK; // keeps M_CEN of initSystemTimer()
        LPIT0->CLRTEN             = CHANNEL_MASK;
        LPIT0->TMR[CHANNEL].TCTRL = 0U; // 32-bit periodic counter, enable through SETTEN
        LPIT0->TMR[CHANNEL].TVAL  = EXPECTED_CONFIGURATION.timerValue;
        LPIT0->MSR                = CHANNEL_MASK; // a flag left by an earlier run is no halt of this one
        _expired                  = false;
        LPIT0->MIER               = LPIT0->MIER | CHANNEL_MASK;
        SYS_SetPriority(EXPECTED_CONFIGURATION.irq, static_cast<uint8_t>(EXPECTED_CONFIGURATION.irqPriority));
        SYS_EnableIRQ(EXPECTED_CONFIGURATION.irq);
    }

    static void stop() { LPIT0->CLRTEN = CHANNEL_MASK; }

    /**
     * Arm the deadman and restart the timeout. Called at the end of every poll, so the first poll is also what
     * arms it - from then on a standstill longer than TIMEOUT_MS switches the power outputs off.
     *
     * A write to TVAL alone would only take effect after the next expiry ([RM] 48.5.6 p. 1541), so the channel is
     * disabled and enabled again - that reloads TVAL. This also re-arms the channel after the handler stopped it.
     */
    static void retrigger()
    {
        LPIT0->CLRTEN = CHANNEL_MASK;
        LPIT0->SETTEN = CHANNEL_MASK;
    }

    /** true once per expiry of the deadman (set by the interrupt handler). */
    static bool takeExpired()
    {
        if (!_expired)
        {
            return false;
        }
        _expired = false;
        return true;
    }

    /**
     * Body of the channel interrupt. Power outputs off first, then the bookkeeping; no RTOS service, no logging,
     * no driver object - the log line and the driver state follow in the next poll of the guard.
     *
     * The flag check comes first because a derivative may share one vector for all LPIT channels (S32K11x:
     * LPIT_IRQn); it is two register reads, no call and no store before the outputs are off.
     */
    static void onInterrupt()
    {
        if ((LPIT0->MSR & CHANNEL_MASK) == 0U)
        {
            return;
        }
        nxSafeOutputsSwitchOff();
        LPIT0->CLRTEN = CHANNEL_MASK; // one report per expiry; the guard re-arms with the next retrigger()
        LPIT0->MSR    = CHANNEL_MASK; // write one to clear
        _expired      = true;
    }

private:
    // Written by the handler, read and cleared by the poll. Losing a second expiry between read and clear is
    // harmless: the poll that reads it reports the halt anyway. Without an initializer it is zero initialized
    // before any code runs (a volatile with an initializer would cost a dynamic initializer in .init_array).
    static inline volatile bool _expired;
};

} // namespace integration
