#pragma once

/* Register model of the family s32k1xx for the host tests of bootloader/platforms/s32k1xx (BL-05).
 *
 * Register layout and bit masks come from the vendor headers of the submodule (BSD-3, S32K148 rev. 1.1a) - a test
 * must not invent register offsets, otherwise it only confirms its own idea of the hardware. Redirected are the
 * instance pointers: they point into plain memory, so the code under test writes into arrays that the test reads
 * back. Peripherals the bootloader HAL touches on the SPI path: LPSPI, PCC, PORT. */

#include "S32K148_COMMON.h"
#include "S32K148_LPSPI.h"
#include "S32K148_PCC.h"
#include "S32K148_PORT.h"
#include "S32K148_SCG.h" /* SOSCCFG field macros for the oscillator range (bl_sosc_s32k1xx.h) */

#define BL_MODEL_LPSPI_COUNT 3
#define BL_MODEL_PORT_COUNT  5

extern LPSPI_Type bl_model_lpspi[BL_MODEL_LPSPI_COUNT];
extern PCC_Type bl_model_pcc;
extern PORT_Type bl_model_port[BL_MODEL_PORT_COUNT];

#undef IP_LPSPI0
#undef IP_LPSPI1
#undef IP_LPSPI2
#define IP_LPSPI0 (&bl_model_lpspi[0])
#define IP_LPSPI1 (&bl_model_lpspi[1])
#define IP_LPSPI2 (&bl_model_lpspi[2])

#undef IP_PCC
#define IP_PCC (&bl_model_pcc)

#undef IP_PORTA
#undef IP_PORTB
#undef IP_PORTC
#undef IP_PORTD
#undef IP_PORTE
#define IP_PORTA (&bl_model_port[0])
#define IP_PORTB (&bl_model_port[1])
#define IP_PORTC (&bl_model_port[2])
#define IP_PORTD (&bl_model_port[3])
#define IP_PORTE (&bl_model_port[4])
