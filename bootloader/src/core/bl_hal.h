#pragma once

#include <stdbool.h>
#include <stdint.h>

/* Implemented by the family HAL (bootloader/platforms/<family>) and by the host test build. */

void hal_init(void);
uint32_t hal_millis(void);
uint32_t hal_entropy(void);
/* Optional (BL-03): `len` bytes of a hardware random generator, false if the family has none or it failed. The
 * core has a weak default returning false (src/core/seedkey.c); a family with a generator defines it. */
bool hal_random(uint8_t* out, uint32_t len);

bool hal_can_send(uint32_t id, uint8_t const* data, uint8_t len);
bool hal_can_tx_idle(void);
bool hal_can_receive(uint32_t* id, uint8_t* data, uint8_t* len);

uint8_t const* hal_flash_ptr(uint32_t address);
bool hal_flash_erase_sector(uint32_t address);
/* len must be a multiple of the phrase size, the target area must be erased */
bool hal_flash_program(uint32_t address, uint8_t const* data, uint32_t len);

/* Family hook (BL-08): whether an image of `size` bytes at `start`, CRC already checked, has what the family's
 * hal_jump_to_app() needs. Cortex-M: vector table with initial stack pointer in the application RAM and Thumb
 * reset handler inside the image (platforms/common/arch/cortex_m_app_entry.inc); Cortex-R without VTOR: the fixed
 * forwarding table of decision E2 (doc/architecture/non-cortex-m-audit.md). The core knows no image start of its
 * own. Defined in bootloader/platforms/<family>/bl_family_image.inc, compiled inside src/core/app_image.c. */
bool hal_app_entry_plausible(uint32_t start, uint32_t size);

bool hal_boot_request_consume(void);
/* Family hook (BL-33): true if the reset that started this run belongs to a reset sequence the application runs on
 * its own and that ends in the application again, so a tester has nothing to catch there - s32k1xx: the two resets
 * of the MCU watchdog fast test (RCM_SRS = WDOG only and WDOG_CS[TST] = 2 or 3, hal_s32k1xx.c). The core then jumps
 * into a valid application without waiting for the startup window (BL_STARTUP_WINDOW_MS). A boot request, an
 * invalid application and a tester session keep the bootloader as before; a power-on must always return false, so
 * the window of the first start stays the tester's way in. A family without such a sequence returns false. */
bool hal_reset_skips_startup_window(void);
void hal_reset(void);
/* Starts the application whose image begins at appStart (BL_APP_START) and does not return. What lies at appStart
 * and how it is entered is the family's convention (checked before by hal_app_entry_plausible()): Cortex-M
 * loads stack pointer and reset handler from the vector table there and moves VTOR; a core without VTOR enters
 * the application's start and forwards its exceptions through a fixed table (decision E2). */
void hal_jump_to_app(uint32_t appStart);
