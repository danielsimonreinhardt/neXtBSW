#pragma once

/* BL-34 (SYS-125, safety review SYS125-R5 a, safety mechanism SM-59): the bootloader switches the low-voltage detect
 * reset of the PMC on, as the application does in boardInit(). LVDRE is 0 after power-on and only the application set
 * it, so from power-on to the jump - and for as long as a tester holds the bootloader for an update - the MCU ran on
 * between VLVD and VLVR without reset.
 *
 * Implemented in bl_lvd_s32k1xx.cpp with the family port's own function (platforms/s32k1xx/port/main/include/pmc/
 * LowVoltageDetectReset.h): one definition of the register value for bootloader and application, checked against the
 * device header there. Called by bl_system_init() from the reset handler, right after the watchdog is off and before
 * the RAM is initialized (no data used). */

#ifdef __cplusplus
extern "C" {
#endif

void bl_lvd_reset_enable(void);

#ifdef __cplusplus
}
#endif
