#pragma once

/* Stand-in for bootloader/devices/sbcUja113x/sbc_uja113x.h in the host test of the CAN path (BL-39): the generated
 * board part of a board with an SBC calls these two; the SBC hook itself has its own host tests
 * (test_bl_platform_host.py, test_bl_sbc_watchdog_host.py). bl_can_host.c records the calls. */

#include <stdbool.h>

void sbc_init(bool configure);
bool sbc_watchdog_off(void);
