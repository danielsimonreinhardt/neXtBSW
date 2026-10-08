/* Family hook of the bootloader core (BL-08, contract in bootloader/src/core/bl_hal.h): what an application of the
 * family s32k1xx has at its start. Cortex-M0+/M4F: vector table with initial stack pointer and Thumb reset
 * handler. Definition file, compiled inside bootloader/src/core/app_image.c. */

#include "arch/cortex_m_app_entry.inc"
