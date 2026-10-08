#pragma once

#include "mcuLink/McuLink.h"

#include <stdbool.h>
#include <stdint.h>

/* The master's end of one link (BL-15). It drives transactions; it does not route.
 *
 * The division of labour with BL-17 is deliberate. This file answers "when is a transaction worth making and
 * who moves the bytes"; the gateway of BL-17 answers "which slave does this diagnostic address belong to,
 * what does the tester get told while the slave is quiet, and which NRC does a link failure become"
 * (multi-mcu-flash.md sections 5 and 9.5). Keeping them apart is what lets the master's application use the
 * same link for ordinary data exchange (section 6.3) without carrying any of the gateway with it.
 *
 * One instance per slave. Several instances may share one SPI bus - they differ in the chip select `port`
 * they pass to bl_link_hal.h - and nothing here holds a table, a maximum or a slave count (R8): the caller
 * owns the array and the generated slave list says how long it is. */

typedef struct
{
    mcuLink_t link; /* the protocol; the caller drives messages through mcuLink_send/_receive on this */
    uint8_t* txFrame;
    uint8_t* rxFrame;
    uint8_t port;     /* chip select of this slave, as bl_link_hal.h understands it */
    uint8_t frameLen;
} bl_link_master_t;

/* Sets one link up. The frame buffers belong to the caller and must hold cfg->frameLen bytes each; cfg->master
 * is forced, everything else is the generated per-slave configuration. False on a configuration that cannot
 * work - the same check mcuLink_init() makes. */
bool bl_link_master_open(bl_link_master_t* m, uint8_t port, mcuLinkConfig_t const* cfg, uint8_t* txFrame,
                         uint8_t* rxFrame, uint32_t nowMs);

/* Makes at most one transaction, if one is due, and keeps the timers running. Call it in every cycle.
 *
 * "If one is due" is the whole readiness question of section 9.4 and it is settled inside mcuLink: a segment
 * waiting, an acknowledgement outstanding, the slave's ready line raised, or the polling period elapsed. A
 * board with a ready line and one without run the same code - only bl_link_ready_in() answers differently. */
void bl_link_master_poll(bl_link_master_t* m, uint32_t nowMs);
