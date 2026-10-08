/* The bootloader's UDS transport over the inter-MCU link (BL-15; the slave of
 * doc/architecture/multi-mcu-flash.md, used by BL-16).
 *
 * This is the second implementation of bootloader/src/core/bl_transport.h, next to src/core/isotp.c. It is
 * deliberately thin: everything that is protocol lives in libs/bsw/mcuLink, everything that is hardware lives
 * behind bl_link_hal.h, and what remains here is the mapping of one interface onto the other. That the two
 * interfaces line up as closely as they do is not luck - bl_transport.h was cut in BL-14 for exactly this
 * second user.
 *
 * The same mcuLink core serves the master's gateway (bl_link_master.c) and an application (libs/bsw/mcuLink
 * as a CMake target, multi-mcu-flash.md section 6.3). Nothing of OpenBSW, no RTOS and no family appears in
 * any of the three. */

#include "bl_transport.h"

#if BL_TRANSPORT == BL_TRANSPORT_LINK

#include "bl_link_hal.h"
#include "mcuLink/McuLink.h"

/* Link parameters. ARCH-48 puts them into the generated bootloader/bl_diag.h out of the master project's
 * slave entry, so both sides of one link can never be described with different numbers (section 7.5). Until
 * then these defaults apply, and they are the values the host test of BL-15 measured:
 *
 *   frame length 64  - 1026 bytes, the bootloader's block, divide exactly into 18 segments of 57 bytes, and
 *                      at 80.0 % the frame is the most efficient of the sizes measured. Larger frames waste
 *                      more on the padding of the last segment than they save on headers.
 *   window 2         - fills the one-exchange lag of the full-duplex wire; 1 makes the link stop and wait.
 *   ack timeout 20 ms, 8 retries - a slave that is merely slow says BUSY and does not consume these. */
#ifndef BL_LINK_FRAME_LEN
#define BL_LINK_FRAME_LEN 64U
#endif
#ifndef BL_LINK_WINDOW
#define BL_LINK_WINDOW 2U
#endif
#ifndef BL_LINK_ACK_TIMEOUT_MS
#define BL_LINK_ACK_TIMEOUT_MS 20U
#endif
#ifndef BL_LINK_RETRIES
#define BL_LINK_RETRIES 8U
#endif
#ifndef BL_LINK_READY_LINE
#define BL_LINK_READY_LINE false
#endif

static mcuLink_t link;

/* One message in each direction, as on CAN: the same two buffers isotp_t holds, so swapping the transport
 * costs no RAM. The frame buffers are the only addition (2 x BL_LINK_FRAME_LEN). */
static uint8_t rxMsg[BL_UDS_BUFFER_SIZE];
static uint8_t txMsg[BL_UDS_BUFFER_SIZE];
static uint8_t txFrame[BL_LINK_FRAME_LEN];
static uint8_t rxFrame[BL_LINK_FRAME_LEN];

void bl_transport_init(void)
{
    bl_link_init();

    mcuLinkConfig_t cfg;
    cfg.rxMsg         = rxMsg;
    cfg.txMsg         = txMsg;
    cfg.rxMsgSize     = (uint16_t)sizeof(rxMsg);
    cfg.txMsgSize     = (uint16_t)sizeof(txMsg);
    cfg.ackTimeoutMs  = BL_LINK_ACK_TIMEOUT_MS;
    cfg.pollPeriodMs  = 0U; /* a slave never makes clock; the period is the master's business */
    cfg.frameLen      = BL_LINK_FRAME_LEN;
    cfg.window        = BL_LINK_WINDOW;
    cfg.retries       = BL_LINK_RETRIES;
    cfg.session       = bl_link_session();
    cfg.master        = false;
    cfg.readyLine     = BL_LINK_READY_LINE;
    (void)mcuLink_init(&link, &cfg, 0U);

    /* The peripheral has to hold a valid frame before the master's first clock edge. */
    mcuLink_frameOut(&link, txFrame, 0U);
    bl_link_arm(txFrame, rxFrame, BL_LINK_FRAME_LEN);
    bl_link_ready_out(mcuLink_readyOut(&link));
}

uint16_t bl_transport_max_message_len(void) { return mcuLink_maxMessageLen(&link); }

void bl_transport_poll(uint32_t nowMs)
{
    if (bl_link_exchange_done())
    {
        mcuLink_frameIn(&link, rxFrame, nowMs);
        /* Re-arm at once and with a freshly built frame: it carries the acknowledgement for what just
         * arrived, and the master may clock again immediately. */
        mcuLink_frameOut(&link, txFrame, nowMs);
        bl_link_arm(txFrame, rxFrame, BL_LINK_FRAME_LEN);
    }
    mcuLink_poll(&link, nowMs);
    bl_link_ready_out(mcuLink_readyOut(&link));
}

uint8_t const* bl_transport_receive(uint16_t* len, bool* functional)
{
    /* A link is point to point: there is no broadcast on it and therefore no functionally addressed request.
     * Where the tester sends one, it is answered by the master for itself and is not forwarded
     * (multi-mcu-flash.md section 5.4). */
    *functional = false;
    return mcuLink_receive(&link, len);
}

void bl_transport_release(void) { mcuLink_release(&link); }

/* BL-29: a message is in progress from its first segment on (rxActive) and pending until the core releases it
 * (rxComplete); the core holds its S3server for that time. A reception the link abandons clears rxActive. */
bool bl_transport_rx_pending(void) { return link.rxActive || link.rxComplete; }

bool bl_transport_send(uint8_t const* msg, uint16_t len, uint32_t nowMs)
{
    (void)nowMs;
    return mcuLink_send(&link, msg, len);
}

bool bl_transport_tx_idle(void) { return mcuLink_txIdle(&link); }

/* On this link the acknowledgement of the peer is the proof that the answer arrived - stronger than "the
 * bytes left the interface", which is all CAN can say. So the core may reset or jump as soon as this is
 * true, with no risk of cutting off the last response. */
bool bl_transport_tx_done(void) { return mcuLink_txDone(&link); }

/* The events the link latches (peer restart, a message that could not be delivered) belong to the gateway,
 * which turns them into the NRCs of section 9.5. A slave's own core has no use for them: it answers what it
 * receives, and what it never received it never answers. BL-17 reads them through bl_link_master.h. */

#endif /* BL_TRANSPORT == BL_TRANSPORT_LINK */
