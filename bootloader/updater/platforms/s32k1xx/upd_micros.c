/* Time measurement of the updater, family s32k1xx: upd_micros() (upd_hal.h) on the free running counter of the core
 * architecture (cycle_counter/<dwt|lpit>/upd_cycle_counter.h, PLAT-28). Moved out of upd_main.c for the host test.
 *
 * PLAT-86, two changes against the former upd_cycle_count() / CYCLES_PER_US:
 *
 * 1. The microseconds are counted up from the cycles since the previous call (the remainder below one microsecond
 *    stays in lastCount), so upd_micros() wraps at 2^32 us like the unsigned arithmetic of bl_update.c elapsed()
 *    expects. The division of the raw count wrapped at 2^32 / CYCLES_PER_US us (89 s at 48 MHz): a window across that
 *    point came out near 2^32 us. Condition now: one call at least every 2^32 cycles (89 s at 48 MHz); the updater
 *    calls it within each flash window of a few milliseconds.
 * 2. Before every read it asks the counter whether it still runs. A debugger that closes its session may switch the
 *    DWT off (DEMCR.TRCENA, doc/hardware/debug-session-close.md); the counter stands still and its reads are UNKNOWN.
 *    upd_micros() then switches it on again and continues from the value read after the restart: the time in which
 *    it stood still is missing once from the measured windows (they come out shorter), the count never jumps.
 *    Restarts are counted (upd_micros_restarts()).
 *
 * Only called from the main loop of the updater (no interrupt uses it), so no lock is needed. */

#include "upd_micros.h"

#include "bootloader/bl_board.h"
#include "upd_cycle_counter.h" /* per core architecture: cycle_counter/<dwt|lpit> (PLAT-28) */
#include "upd_hal.h"

#include <stdint.h>

#define CYCLES_PER_US (BL_CORE_CLOCK_HZ / 1000000UL) /* counter runs at the reset clock */

#if (BL_CORE_CLOCK_HZ % 1000000UL) != 0UL
#error "upd_micros needs a counter clock of whole megahertz (BL_CORE_CLOCK_HZ)"
#endif

static uint32_t lastCount; /* counter value up to which cycles are counted in micros */
static uint32_t micros;
static uint32_t restarts;

void upd_micros_start(void)
{
    upd_cycle_counter_start();
    lastCount = upd_cycle_count();
    micros    = 0U;
    restarts  = 0U;
}

uint32_t upd_micros(void)
{
    if (!upd_cycle_counter_running())
    {
        upd_cycle_counter_restart();
        lastCount = upd_cycle_count();
        ++restarts;
        return micros;
    }
    uint32_t const now = upd_cycle_count();
    uint32_t const us  = (now - lastCount) / CYCLES_PER_US;
    micros += us;
    lastCount += us * CYCLES_PER_US;
    return micros;
}

uint32_t upd_micros_restarts(void) { return restarts; }
