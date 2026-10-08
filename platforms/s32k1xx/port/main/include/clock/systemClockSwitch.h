/**
 * ARCH-13: system clock switch at runtime (e.g. low power idle) for the s32k1xx board port.
 *
 * Use these functions instead of calling systemClock2Firc()/systemClock2Pll() of OpenBSW bspClock directly: those
 * write a fixed SysTick reload for 1 ms at 48 MHz or 80 MHz (spllSysClk: 80000 - 1) and do not update the system
 * timer. Here the OS tick (SysTick) and the DWT based system timer follow the frequencies of the generated clock plan
 * (bsp/clock/boardClock.h, project clock.system_hz). The boot path needs none of this: configurPll() runs with
 * SysTick stopped, FreeRTOS programs the reload from configCPU_CLOCK_HZ (NX_CLOCK_CORE_HZ) at scheduler start.
 */

#pragma once

#include "FreeRTOS.h"
#include "bsp/clock/boardClock.h"
#include "bsp/timer/SystemTimer.h"
#include "clock/clockConfig.h"
#include "interrupts/SuspendResumeAllInterruptsScopedLock.h"
#include "mcu/mcu.h"

#include <cstdint>

extern "C"
{
// bspConfiguration SystemTimer.cpp: DWT frequency after a clock switch (sleep: FIRC, else NX_CLOCK_CORE_HZ)
void initSystemTimerHelper(bool sleep);
}

namespace platform
{
namespace clock
{
static_assert(
    (NX_CLOCK_CORE_HZ % configTICK_RATE_HZ) == 0U, "OS tick is no whole number of system clock cycles");
static_assert(
    (NX_CLOCK_FIRC_HZ % configTICK_RATE_HZ) == 0U, "OS tick is no whole number of FIRC clock cycles");

/// SysTick reload for one OS tick at the given core clock, restarts the current tick period.
inline void setSysTickForCoreClock(uint32_t const coreHz)
{
    SysTick->LOAD = (coreHz / configTICK_RATE_HZ) - 1U;
    SysTick->VAL  = 0U;
}

/// Core clock FIRC with DIVCORE 1 (OpenBSW fircSysClk), e.g. for idle.
inline void switchToFirc()
{
    ::interrupts::SuspendResumeAllInterruptsScopedLock const lock;
    // PLAT-21 (finding of ARCH-14): count the cycles since the last reading at the old frequency before switching.
    // initSystemTimerHelper() below converts only the remainder; everything the switch itself takes (SPLL start-up)
    // stays uncertain, but at most a few microseconds instead of the whole time since the last reading.
    (void)getSystemTicks();
    systemClock2Firc();
    setSysTickForCoreClock(NX_CLOCK_FIRC_HZ);
    initSystemTimerHelper(true);
}

/// Back to the system clock of the clock plan (source, dividers and frequency of boardClock.h).
inline void switchToSystemClock()
{
    ::interrupts::SuspendResumeAllInterruptsScopedLock const lock;
    // PLAT-21: as in switchToFirc(), account the elapsed cycles before the frequency changes
    (void)getSystemTicks();
#if defined(NX_CLOCK_SYSTEM_SOURCE_SPLL)
    // restarts SOSC and SPLL if they were stopped, writes _SCG_RCCR_ (SysTick reload corrected below)
    systemClock2Pll();
#else
    // FIRC, SIRC and SOSC keep running while another clock is the system clock (configurPll)
    do
    {
        SCG->RCCR = _SCG_RCCR_;
    } while (SCG->CSR != _SCG_RCCR_);
#endif
    setSysTickForCoreClock(NX_CLOCK_CORE_HZ);
    initSystemTimerHelper(false);
}

} // namespace clock
} // namespace platform
