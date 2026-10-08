#pragma once

/* Board part of a test board for the host tests (BL-05), in place of the generated bootloader/bl_board.h
 * (tools/bswgen/blgen.py). Deliberately not the eval board (architecture section 11, question 7): another SPI
 * instance (LPSPI1 instead of LPSPI0), another chip select, other dividers, other pins, another crystal.
 *
 * Same macro names as the generator writes, so the code under test sees exactly what it sees on the target.
 * The crystal (BL_OSC_HZ) can be overridden on the command line to test the SOSC ranges. */

#include "mcu/mcu.h"

#ifndef BL_OSC_HZ
#define BL_OSC_HZ 16000000UL
#endif
#define BL_CORE_CLOCK_HZ 48000000UL

/* SBC hook of the test board: LPSPI1, chip select 2, SOSCDIV2 16 MHz / 4 (PRESCALE 2) / (12 + 2) = 285714 Hz */
#define BL_SBC_SPI           IP_LPSPI1
#define BL_SBC_SPI_PCC_INDEX PCC_LPSPI1_INDEX
#define BL_SBC_SPI_PCS       2U
#define BL_SBC_SPI_PRESCALE  2U
#define BL_SBC_SPI_SCKDIV    12U

static inline void bl_sbc_spi_pins(void)
{
    IP_PCC->PCCn[PCC_PORTB_INDEX] |= PCC_PCCn_CGC_MASK;
    IP_PCC->PCCn[PCC_PORTD_INDEX] |= PCC_PCCn_CGC_MASK;
    IP_PORTB->PCR[14U] = PORT_PCR_MUX(3U); /* PTB14 LPSPI1_SCK */
    IP_PORTB->PCR[15U] = PORT_PCR_MUX(3U); /* PTB15 LPSPI1_SIN */
    IP_PORTB->PCR[16U] = PORT_PCR_MUX(3U); /* PTB16 LPSPI1_SOUT */
    IP_PORTD->PCR[3U]  = PORT_PCR_MUX(3U); /* PTD3 LPSPI1_PCS2 */
}
