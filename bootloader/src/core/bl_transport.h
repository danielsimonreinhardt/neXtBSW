#pragma once

#include "bl_config.h"

#include <stdbool.h>
#include <stdint.h>

/* Transport of the bootloader's UDS server (BL-14, doc/architecture/multi-mcu-flash.md requirement R1).
 *
 * The core (uds.c, bl_main.c) exchanges whole UDS messages through this interface. It knows neither frames nor
 * addresses nor a bus: which transport carries the messages is a decision of the configuration, written by
 * tools/bswgen/blgen.py as BL_TRANSPORT into the generated bl_diag.h, and exactly one translation unit
 * implements the interface below.
 *
 * A transport carries one message at a time in each direction. Everything a transport needs of the hardware it
 * takes from bl_hal.h itself; no hal_can_* call and no CAN identifier appears above this line. */

/* The transports the core can be built with. They are named here, not in the generat: the generat states the
 * choice (`#define BL_TRANSPORT BL_TRANSPORT_CAN`), so the value exists exactly once. */
#define BL_TRANSPORT_CAN  1 /* ISO 15765-2 on CAN, src/core/isotp.c - what every project generates today */
#define BL_TRANSPORT_LINK 2 /* inter-MCU link of a slave MCU (BL-15/BL-16); its implementation follows there */

#ifndef BL_TRANSPORT
#error "no transport selection in the generated bootloader/bl_diag.h (tools/bswgen/blgen.py, BL-14)"
#endif
#if (BL_TRANSPORT != BL_TRANSPORT_CAN) && (BL_TRANSPORT != BL_TRANSPORT_LINK)
#error "unknown BL_TRANSPORT in the generated bootloader/bl_diag.h (BL-14)"
#endif

void bl_transport_init(void);

/* The longest UDS message this transport carries in one piece, in bytes, the same in both directions. The core
 * announces no larger block than this (RequestDownload, maxNumberOfBlockLength) and refuses a longer request.
 * It belongs to the interface so that a gateway can refuse a request that is too large for the target before it
 * opens the link to it (doc/architecture/multi-mcu-flash.md section 17, ARCH-47). */
uint16_t bl_transport_max_message_len(void);

/* Receive work and timers of the transport; called in every cycle of bl_poll(). */
void bl_transport_poll(uint32_t nowMs);

/* The received message or NULL. `functional` marks a request the transport delivered as a broadcast, which the
 * core answers without a negative response. The message stays valid until bl_transport_release(). */
uint8_t const* bl_transport_receive(uint16_t* len, bool* functional);
void bl_transport_release(void);

/* BL-29: a request is on its way or waiting - true from its first frame (ISO 15765-2: the first frame of a
 * segmented message, or a single frame) until bl_transport_release(), false again when a reception ends in an
 * error. The core stops its S3server timer for that time (ISO 14229-2, session layer timing): a block that takes
 * longer than S3 to arrive must not find the session gone. */
bool bl_transport_rx_pending(void);

/* Hands one message over for transmission; false if the transport is busy or the message does not fit. */
bool bl_transport_send(uint8_t const* msg, uint16_t len, uint32_t nowMs);

/* Ready for the next message: nothing of an earlier one is still being transmitted. */
bool bl_transport_tx_idle(void);

/* Everything handed over has physically left the interface. This is the point at which the core may reset or
 * jump into the application without cutting off the last response. */
bool bl_transport_tx_done(void);
