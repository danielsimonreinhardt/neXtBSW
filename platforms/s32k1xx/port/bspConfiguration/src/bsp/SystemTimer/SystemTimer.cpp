/********************************************************************************
 * Copyright (c) 2024 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

/**
 * Contains
 * \file
 * \ingroup bspConfig
 */
#include "bsp/timer/SystemTimer.h"

#include "interrupts/SuspendResumeAllInterruptsScopedLock.h"
#include "mcu/mcu.h"

// ARCH-13: frequencies of the generated clock plan (tools/bswgen, project clock.system_hz)
#include <bsp/clock/boardClock.h>

#include <etl/platform.h>

#include <numeric>

/* PLAT-12: source of the free running cycle counter, per core architecture.
 *
 * ARMv7-M (Cortex-M4F/M7) counts core clock cycles in DWT->CYCCNT. **ARMv6-M has no DWT and no CoreDebug**
 * (a Cortex-M0+ has neither the Data Watchpoint and Trace unit nor DEMCR - CMSIS core_cm0plus.h has no such
 * structures), so the counter of this file would not even compile. The family port then counts the 32-bit
 * channel 0 of the LPIT, which initSystemTimer() already sets up for every derivative: TVAL 0xFFFFFFFF, free
 * running. The channel counts **down**, so it is read as (0xFFFFFFFF - CVAL) to keep the up-counting arithmetic
 * (and its unsigned wraparound) of the rest of this file unchanged.
 *
 * Difference in behaviour: the LPIT hangs on its own PCC clock (mcudb clock.pcc.LPIT, generated
 * NX_CLOCK_LPIT_HZ), not on the core clock. The switch to the idle clock (ARCH-14, systemClock2Firc) therefore
 * does not change the tick frequency - the conversion of the pending cycles simply has nothing to do. That makes
 * the ARMv6-M time source coarser (LPIT frequency instead of the core frequency) but immune to clock switches.
 *
 * ARCH-24: the architecture comes from the generated mcu/derivativeFacts.h (NX_CORE_ARCHITECTURE_*, included by
 * mcu/mcu.h), not from __ARM_ARCH, which is 7 for Cortex-M4F and Cortex-R4F alike (finding ARCH-19). This file is a
 * Cortex-M template (DWT, CoreDebug): any other architecture stops the build.
 */
#if defined(NX_CORE_ARCHITECTURE_ARMV6M)
#define NX_SYSTEM_TIMER_LPIT 1
#elif defined(NX_CORE_ARCHITECTURE_ARMV7M) || defined(NX_CORE_ARCHITECTURE_ARMV8M)
#define NX_SYSTEM_TIMER_LPIT 0
#else
#error "SystemTimer.cpp is a Cortex-M template: needs NX_CORE_ARCHITECTURE_ARMV6M, ARMV7M or ARMV8M (mcu/derivativeFacts.h)"
#endif

#if NX_SYSTEM_TIMER_LPIT && !defined(NX_CLOCK_LPIT_HZ)
#error "the ARMv6-M system timer counts LPIT ticks and needs NX_CLOCK_LPIT_HZ (mcudb clock.pcc.LPIT)"
#endif

namespace
{
#if NX_SYSTEM_TIMER_LPIT
// LPIT0 channel 0 counts down from 0xFFFFFFFF on its own PCC clock, independent of the core clock switch
uint32_t const DWT_FREQ_HZ_RUN  = NX_CLOCK_LPIT_HZ;
uint32_t const DWT_FREQ_HZ_IDLE = NX_CLOCK_LPIT_HZ;

uint32_t readCycleCounter() { return 0xFFFFFFFFU - LPIT0->TMR[0].CVAL; }
#else
// DWT->CYCCNT counts core clock cycles: the system clock in run mode, FIRC in idle (systemClock2Firc)
uint32_t const DWT_FREQ_HZ_RUN  = NX_CLOCK_CORE_HZ;
uint32_t const DWT_FREQ_HZ_IDLE = NX_CLOCK_FIRC_HZ;

uint32_t readCycleCounter() { return DWT->CYCCNT; }

// DWT and CoreDebug of CMSIS (ARMv7-M architecture reference manual DDI 0403E, C1.6 DEMCR, C1.8 DWT_CTRL)
uint32_t const DEMCR_TRCENA       = 0x01000000U;
uint32_t const DWT_CTRL_CYCCNTENA = 0x00000001U;
#endif
// Common divider of all DWT frequencies; whole MHz so that ticks convert exactly to micro- and nanoseconds
uint32_t const TICK_FREQ_HZ = std::gcd(DWT_FREQ_HZ_RUN, DWT_FREQ_HZ_IDLE);
static_assert(
    (TICK_FREQ_HZ % 1000000U) == 0U,
    "SystemTimer: common divider of system clock (NX_CLOCK_CORE_HZ) and FIRC is no whole MHz, choose "
    "another clock.system_hz");
uint32_t const DWT_FREQ_MHZ_RUN  = DWT_FREQ_HZ_RUN / 1000000U;
uint32_t const DWT_FREQ_MHZ_IDLE = DWT_FREQ_HZ_IDLE / 1000000U;
uint32_t const TICK_FREQ_MHZ     = TICK_FREQ_HZ / 1000000U;

uint32_t const DWT_TICKS_RATIO_RUN  = DWT_FREQ_MHZ_RUN / TICK_FREQ_MHZ;
uint32_t const DWT_TICKS_RATIO_IDLE = DWT_FREQ_MHZ_IDLE / TICK_FREQ_MHZ;
// ARCH-14: time unit below one DWT cycle, only for clock switches. One tick is RUN * IDLE units, one DWT cycle is
// IDLE units in run mode and RUN units in idle.
uint32_t const UNITS_PER_TICK = DWT_TICKS_RATIO_RUN * DWT_TICKS_RATIO_IDLE;

struct
{
    uint64_t ticks;         // Main ticks counter, never overflows
    uint32_t lastDwt;       // DWT value up to which cycles are counted in ticks (< dwtTicksRatio cycles behind)
    uint32_t dwtTicksRatio;
    uint32_t dwtFreqMhz;
    uint32_t subCycleUnits; // Time on top of lastDwt shorter than one DWT cycle, left over from a clock switch
} state = {0, 0, DWT_TICKS_RATIO_RUN, DWT_FREQ_MHZ_RUN, 0};

// PLAT-67: how often readRunningCycleCounter() found the DWT switched off and restarted it (never on ARMv6-M)
uint32_t cycleCounterRestarts = 0U;

// Interrupts must be locked. Counts only whole ticks and keeps the remaining cycles in lastDwt (ARCH-14: the
// OpenBSW reference sets lastDwt = curDwt and loses up to dwtTicksRatio - 1 cycles per call, so the time depended on
// the call rate). The unsigned difference handles the wraparound of the 32 bit counter as long as less than
// 2^32 - dwtTicksRatio cycles pass between two calls. subCycleUnits never completes a tick on its own (it is below
// one cycle and the cycles are below a whole tick), so it is only needed at the next clock switch.
uint64_t updateTicksLocked(uint32_t const curDwt)
{
    uint32_t const newTicks = (curDwt - state.lastDwt) / state.dwtTicksRatio;
    state.ticks += newTicks;
    state.lastDwt += newTicks * state.dwtTicksRatio;
    return state.ticks;
}

#if NX_SYSTEM_TIMER_LPIT
// Interrupts must be locked. The LPIT channel is no debug resource, a debugger does not switch it off.
uint32_t readRunningCycleCounter() { return readCycleCounter(); }
#else
// PLAT-67: DEMCR.TRCENA was found cleared (interrupts locked). The J-Link clears it when it closes a debug session
// ("debug de-init", SEGGER release notes V6.44b: command string SetSkipDebugDeInit keeps it). TRCENA 0 switches off
// the whole DWT: CYCCNT stands still (board 2026-09-18/21) - the system time stops, no task deadline becomes due any
// more and the watchdog is not served (reset about 0.2 .. 1 s after the J-Link exit, exception 38 WDOG_EWM). While
// TRCENA is 0, reads of the DWT return UNKNOWN values, so the value before the restart is not trusted: counting
// resumes at the value read after it. The time in which the counter stood still (from the last read before the
// debugger switched it off to this read) is lost once; the system time stays monotonic and never jumps forward.
// Not inlined, so that the check in the fast path stays small enough to be inlined into every time function.
__attribute__((noinline, cold)) uint32_t restartCycleCounter()
{
    CoreDebug->DEMCR = CoreDebug->DEMCR | DEMCR_TRCENA;
    DWT->CTRL        = DWT->CTRL | DWT_CTRL_CYCCNTENA;
    uint32_t const curDwt = readCycleCounter();
    state.lastDwt         = curDwt;
    ++cycleCounterRestarts;
    return curDwt;
}

// Interrupts must be locked. Costs one load of DEMCR (private peripheral bus) and a branch not taken per call.
uint32_t readRunningCycleCounter()
{
    if ((CoreDebug->DEMCR & DEMCR_TRCENA) == 0U)
    {
        return restartCycleCounter();
    }
    return readCycleCounter();
}
#endif

// Must be called more than once per DWT period (2^32 / NX_CLOCK_CORE_HZ, ~53 sec at 80 MHz). Called implicitly
// through all getSomething() functions. PLAT-67: always inlined as before the DEMCR check (GCC 11 -O2 stopped
// inlining it with the check, which added a call to every time function).
__attribute__((always_inline)) inline uint64_t updateTicks()
{
    ETL_MAYBE_UNUSED const interrupts::SuspendResumeAllInterruptsScopedLock lock;
    return updateTicksLocked(readRunningCycleCounter());
}

} // namespace

extern "C"
{
void initSystemTimer()
{
    ETL_MAYBE_UNUSED const interrupts::SuspendResumeAllInterruptsScopedLock lock;

    LPIT0->MCR          = 0x1;
    LPIT0->TMR[0].TCTRL = 0x00U;
    LPIT0->TMR[0].TVAL  = 0xFFFFFFFFU;
    LPIT0->SETTEN       = 0x1U;

#if !NX_SYSTEM_TIMER_LPIT
    CoreDebug->DEMCR = CoreDebug->DEMCR | DEMCR_TRCENA;
    DWT->CYCCNT      = 0;
    DWT->CTRL        = DWT->CTRL | DWT_CTRL_CYCCNTENA;
#endif

    state.ticks = 0; // General ticks counter, never overflows
#if NX_SYSTEM_TIMER_LPIT
    // The LPIT channel cannot be reset to a known value here (SETTEN above just started it), so counting begins
    // at its current value instead of at zero.
    state.lastDwt = readCycleCounter();
#else
    state.lastDwt = 0; // DWT->CYCCNT was just cleared
#endif
    state.dwtTicksRatio = DWT_TICKS_RATIO_RUN;
    state.dwtFreqMhz    = DWT_FREQ_MHZ_RUN;
    state.subCycleUnits = 0;
    cycleCounterRestarts = 0U;
}

void initSystemTimerHelper(bool const sleep)
{
    ETL_MAYBE_UNUSED const interrupts::SuspendResumeAllInterruptsScopedLock lock;
    uint32_t const curDwt = readRunningCycleCounter();
    (void)updateTicksLocked(curDwt);
    // ARCH-14: the cycles not yet counted ran at the old frequency. Convert them to cycles at the new frequency and
    // keep the part shorter than one new cycle, so that a clock switch loses no time either.
    uint32_t const pendingUnits
        = ((curDwt - state.lastDwt) * (UNITS_PER_TICK / state.dwtTicksRatio)) + state.subCycleUnits;
    state.dwtFreqMhz    = sleep ? DWT_FREQ_MHZ_IDLE : DWT_FREQ_MHZ_RUN;
    state.dwtTicksRatio = state.dwtFreqMhz / TICK_FREQ_MHZ;
    uint32_t const newCycleUnits = UNITS_PER_TICK / state.dwtTicksRatio;
    state.lastDwt                = curDwt - (pendingUnits / newCycleUnits);
    state.subCycleUnits          = pendingUnits % newCycleUnits;
}

uint64_t getSystemTicks(void) { return updateTicks(); }

uint32_t getSystemTicks32Bit(void) { return static_cast<uint32_t>(updateTicks()); }

uint64_t getSystemTimeNs(void) { return updateTicks() * 1000U / TICK_FREQ_MHZ; }

uint64_t getSystemTimeUs64Bit(void) { return updateTicks() / TICK_FREQ_MHZ; }

uint32_t getSystemTimeUs32Bit(void) { return static_cast<uint32_t>(updateTicks() / TICK_FREQ_MHZ); }

uint64_t getSystemTimeMs64Bit(void) { return updateTicks() / TICK_FREQ_MHZ / 1000U; }

uint32_t getSystemTimeMs32Bit(void)
{
    return static_cast<uint32_t>(updateTicks() / TICK_FREQ_MHZ / 1000U);
}

uint64_t systemTicksToTimeUs(uint64_t const ticks) { return ticks / TICK_FREQ_MHZ; }

uint64_t systemTicksToTimeNs(uint64_t const ticks) { return ticks * 1000U / TICK_FREQ_MHZ; }

// Raw counter for short runtime measurements: does not restart a stopped DWT (the next system time read does)
uint32_t getFastTicks(void) { return readCycleCounter(); }

// PLAT-67: number of restarts of the DWT cycle counter since initSystemTimer() (StaticBsp logs a change)
uint32_t getSystemTimerCycleCounterRestarts(void) { return cycleCounterRestarts; }

uint32_t getFastTicksPerSecond(void)
{
    ETL_MAYBE_UNUSED const interrupts::SuspendResumeAllInterruptsScopedLock lock;
    return state.dwtFreqMhz * 1000000;
}

void sysDelayUs(uint32_t const delay)
{
    uint32_t const start = getSystemTimeUs32Bit();
    // Use unsigned wraparound: (now - start) yields the elapsed microseconds modulo 2^32, which is
    // correct as long as the real elapsed time is < 2^32 us (~71.6 min) and we poll often enough.
    while ((getSystemTimeUs32Bit() - start) < delay) {}
}

} // extern "C"
