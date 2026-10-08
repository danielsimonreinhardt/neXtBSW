#pragma once

#include <stdbool.h>
#include <stdint.h>

/* What the inter-MCU link needs of the hardware (BL-15, doc/architecture/multi-mcu-flash.md section 4).
 *
 * Everything above this header - the protocol in libs/bsw/mcuLink and the transport in bl_transport_link.c -
 * is family neutral. Everything below it is a family's SPI peripheral and a few GPIOs, implemented in
 * bootloader/platforms/<family> from the generated wiring (BL-16 for the slave side, BL-17 for the master
 * side). No register and no pin name appears above this line; R2 of the target picture.
 *
 * The split follows the two roles of section 2: the slave answers transactions it does not start, the master
 * makes them. A family implements the half its projects need; a bootloader links only the half it uses. */

/* ---------------------------------------------------------------- both roles */

/* Brings the port up from the generated wiring (instance, pins, clock, frame format). */
void bl_link_init(void);

/* A value that is new for **each start of this MCU** - the session counter of the frame format (McuLink.h,
 * multi-mcu-flash.md section 10.2). This is what makes a restart in the middle of a dialogue detectable at
 * all, so its quality matters:
 *
 *   - preferred: a counter in no-init RAM, incremented on every start. Then two consecutive starts can never
 *     look alike, which is exactly the case that has to be caught.
 *   - acceptable: an entropy source (hal_entropy()). One start in 256 then repeats the previous value and the
 *     restart is missed; the dialogue fails later on a sequence error instead of at once with NRC 0x24.
 *
 * A constant is not acceptable and defeats the mechanism. BL-16 chooses per family and states which. */
uint8_t bl_link_session(void);

/* ---------------------------------------------------------------- slave side (BL-16) */

/* Arms the peripheral for the next transaction: `len` bytes out of txFrame while `len` bytes come into
 * rxFrame. The slave cannot know when the master will clock, so the frame has to be ready beforehand - this
 * is the one-exchange lag the protocol is built around (McuLink.h). Both buffers stay owned by the caller
 * until bl_link_exchange_done() reports the transaction. */
void bl_link_arm(uint8_t* txFrame, uint8_t* rxFrame, uint8_t len);

/* True once for each completed transaction; reading it clears the flag. */
bool bl_link_exchange_done(void);

/* Drives the slave's ready line where the board wires one (section 9.4). Without a line this is empty and
 * the master polls instead - the difference is wiring, not protocol. */
void bl_link_ready_out(bool level);

/* ---------------------------------------------------------------- master side (BL-17 uses it) */

/* One transaction with the slave on chip select `port`: `len` bytes out of tx while `len` bytes come into rx.
 * False on a port that does not answer (no clock, wrong wiring); the protocol then sees a frame without its
 * start byte and counts it, exactly as it counts a lost one. */
bool bl_link_transfer(uint8_t port, uint8_t const* tx, uint8_t* rx, uint8_t len);

/* Level of that slave's ready line, where one is wired. A board without the line reports true, which turns
 * the link into the polling variant without a branch anywhere above (section 9.4). */
bool bl_link_ready_in(uint8_t port);
