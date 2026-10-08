#pragma once

#include <stdbool.h>
#include <stdint.h>

/* SPI master of the bootloader, implemented per family in bootloader/platforms/<family> (s32k1xx: LPSPI).
 * Instance, chip select, pins and dividers are wiring and come from the generated board part; the frame format
 * is a property of the device and comes from the caller. Device hooks (bootloader/devices/<module>) use this
 * interface instead of family registers, so a hook stays family neutral (architecture section 7, D6).
 *
 * One port at a time: the bootloader talks to at most one device before CAN starts (bl_board_init of the
 * generated bootloader/bl_board.h). A second port would need a handle here and a second wiring block there. */

typedef struct
{
    uint8_t bits; /* bits per frame, 1..32 (UJA113x: 16) */
    bool cpol;    /* clock idle level high */
    bool cpha;    /* data sampled on the second clock edge */
} bl_spi_frame_t;

/* Switches the port on for this frame format (clock gate, pins, dividers of the board part). */
void bl_spi_init(bl_spi_frame_t const* frame);

/* Transfers one frame, most significant bit first; false on timeout (no device, no clock, wrong wiring).
 * The chip select of the port frames the transfer. */
bool bl_spi_transfer(uint32_t tx, uint32_t* rx);
