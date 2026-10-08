#pragma once

/* Free running counter of the updater's time measurement (upd_micros), core architecture ARMv7-M (PLAT-28):
 * the DWT cycle counter counts core clock cycles, BL_CORE_CLOCK_HZ per second (the updater keeps the reset clock).
 * It keeps counting while a flash command runs with interrupts disabled, which a SysTick count would not.
 * Selected by bootloader/updater/CMakeLists.txt from NX_CORE_ARCHITECTURE (derivative database, core).
 *
 * PLAT-86: a debugger that closes its session may clear DEMCR.TRCENA (J-Link "debug de-init", board finding PLAT-67,
 * doc/hardware/debug-session-close.md). The whole DWT stops then and CYCCNT stands still; its reads are UNKNOWN while
 * TRCENA is 0. upd_micros.c asks upd_cycle_counter_running() before every read and restarts the counter. */

#include "mcu/mcu.h"

#include <stdbool.h>
#include <stdint.h>

static inline void upd_cycle_counter_start(void)
{
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CYCCNT = 0U;
    DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
}

/* Counts up at BL_CORE_CLOCK_HZ, wraps at 2^32 */
static inline uint32_t upd_cycle_count(void) { return DWT->CYCCNT; }

/* false if a debugger switched the DWT off (DWT_CTRL is only read while TRCENA is set, otherwise it is UNKNOWN) */
static inline bool upd_cycle_counter_running(void)
{
    bool running = false;
    if (((CoreDebug->DEMCR & CoreDebug_DEMCR_TRCENA_Msk) != 0U) && ((DWT->CTRL & DWT_CTRL_CYCCNTENA_Msk) != 0U))
    {
        running = true;
    }
    return running;
}

/* Switches the counter on again without resetting it; it continues from the value it holds now */
static inline void upd_cycle_counter_restart(void)
{
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
}
