#pragma once

#include <stdbool.h>
#include <stdint.h>

void bl_init(void);
void bl_poll(void);

/* BL-28: a device the bootloader has to serve while it runs, e.g. the watchdog of an SBC (project value
 * bootloader.sbc_watchdog: serve, and since BL-41 a UJA113x without software development mode with leave too).
 * Defined by the device hook that needs it (bootloader/devices/<module>), whose bootloader.cmake then defines
 * BL_DEVICE_SERVICE for this file; without it the core calls nothing. Called from every bl_poll() and once more
 * right before the jump to the application (beforeJump), so that the application gets a whole period to take
 * over. */
void bl_device_service(uint32_t nowMs, bool beforeJump);
