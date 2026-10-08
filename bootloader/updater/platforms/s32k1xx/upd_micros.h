#pragma once

/* Time measurement of the updater, family s32k1xx (PLAT-86): upd_micros() of upd_hal.h on the free running counter
 * of the core architecture (cycle_counter/<dwt|lpit>/upd_cycle_counter.h). */

#include <stdint.h>

/* Starts the counter and the microsecond count at 0; called once before the first upd_micros() */
void upd_micros_start(void);

/* How often upd_micros() found the counter switched off by a debugger and restarted it (PLAT-86) */
uint32_t upd_micros_restarts(void);
