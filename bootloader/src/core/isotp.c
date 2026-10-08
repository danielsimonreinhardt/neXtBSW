/* CAN transport of the bootloader core (BL-14): ISO 15765-2 (normal addressing, classic CAN) behind
 * bl_transport.h. The CAN identifiers of the generated bl_diag.h and every hal_can_* call of the bootloader
 * core live here and nowhere above; a second transport (the inter-MCU link, BL-15/BL-16) is a file of its own
 * that implements the same interface. Compiled when the generated bl_diag.h selects BL_TRANSPORT_CAN. */

#include "bl_transport.h"

#if BL_TRANSPORT == BL_TRANSPORT_CAN

#include "bl_hal.h"
#include "isotp.h"

#include <stddef.h>
#include <string.h>

#define PCI_SF 0x0U
#define PCI_FF 0x1U
#define PCI_CF 0x2U
#define PCI_FC 0x3U

#define FC_CTS      0x0U
#define FC_WAIT     0x1U
#define FC_OVERFLOW 0x2U

/* Frames taken out of the hardware in one poll, so one busy bus cannot hold up the rest of the cycle. */
#define MAX_FRAMES_PER_POLL 16U

/* The one connection of this bootloader: one tester, one pair of identifiers (bl_diag.h). */
static isotp_t tp;

static bool send_frame(uint8_t const* payload, uint8_t len)
{
    uint8_t frame[8];
    (void)memset(frame, BL_ISOTP_PADDING, sizeof(frame));
    (void)memcpy(frame, payload, len);
    return hal_can_send(BL_CAN_ID_RESPONSE, frame, 8U);
}

static void send_flow_control(uint8_t status)
{
    uint8_t const fc[3] = {(uint8_t)((PCI_FC << 4) | status), BL_ISOTP_BLOCK_SIZE, BL_ISOTP_STMIN_MS};
    (void)send_frame(fc, sizeof(fc));
}

static uint32_t stmin_to_ms(uint8_t stmin)
{
    if (stmin <= 0x7FU)
    {
        return stmin;
    }
    /* 0xF1..0xF9 are 100..900 us; round up to one tick */
    return ((stmin >= 0xF1U) && (stmin <= 0xF9U)) ? 1UL : 127UL;
}

static void on_single_frame(uint8_t const* data, uint8_t len, bool functional)
{
    uint8_t const dl = data[0] & 0x0FU;
    if ((dl == 0U) || (dl > 7U) || ((uint8_t)(dl + 1U) > len))
    {
        return;
    }
    (void)memcpy(tp.rxBuf, &data[1], dl);
    tp.rxLen        = dl;
    tp.rxFunctional = functional;
    tp.rxState      = ISOTP_RX_COMPLETE;
}

static void on_first_frame(uint8_t const* data, uint8_t len, uint32_t nowMs)
{
    uint16_t const dl = (uint16_t)(((uint16_t)(data[0] & 0x0FU) << 8) | data[1]);
    if ((len < 8U) || (dl < 8U))
    {
        return;
    }
    if (dl > sizeof(tp.rxBuf))
    {
        send_flow_control(FC_OVERFLOW);
        tp.rxState = ISOTP_RX_IDLE;
        return;
    }
    (void)memcpy(tp.rxBuf, &data[2], 6U);
    tp.rxLen        = 6U;
    tp.rxExpected   = dl;
    tp.rxSn         = 1U;
    tp.rxBlockCount = 0U;
    tp.rxFunctional = false;
    tp.rxLastMs     = nowMs;
    tp.rxState      = ISOTP_RX_RECEIVING;
    send_flow_control(FC_CTS);
}

static void on_consecutive_frame(uint8_t const* data, uint8_t len, uint32_t nowMs)
{
    if (tp.rxState != ISOTP_RX_RECEIVING)
    {
        return;
    }
    if ((data[0] & 0x0FU) != tp.rxSn)
    {
        tp.rxState = ISOTP_RX_IDLE;
        return;
    }
    uint16_t remaining = (uint16_t)(tp.rxExpected - tp.rxLen);
    uint16_t chunk     = (remaining > 7U) ? 7U : remaining;
    if ((uint16_t)(chunk + 1U) > len)
    {
        tp.rxState = ISOTP_RX_IDLE;
        return;
    }
    (void)memcpy(&tp.rxBuf[tp.rxLen], &data[1], chunk);
    tp.rxLen    = (uint16_t)(tp.rxLen + chunk);
    tp.rxSn     = (uint8_t)((tp.rxSn + 1U) & 0x0FU);
    tp.rxLastMs = nowMs;

    if (tp.rxLen >= tp.rxExpected)
    {
        tp.rxState = ISOTP_RX_COMPLETE;
        return;
    }
#if BL_ISOTP_BLOCK_SIZE != 0U
    tp.rxBlockCount++;
    if (tp.rxBlockCount >= BL_ISOTP_BLOCK_SIZE)
    {
        tp.rxBlockCount = 0U;
        send_flow_control(FC_CTS);
    }
#endif
}

static void on_flow_control(uint8_t const* data, uint8_t len, uint32_t nowMs)
{
    if ((tp.txState != ISOTP_TX_WAIT_FC) || (len < 3U))
    {
        return;
    }
    switch (data[0] & 0x0FU)
    {
        case FC_CTS:
            tp.txBlockSize  = data[1];
            tp.txBlockCount = 0U;
            tp.txStminMs    = stmin_to_ms(data[2]);
            tp.txState      = ISOTP_TX_SENDING;
            tp.txLastMs     = nowMs - tp.txStminMs; /* first CF immediately */
            break;
        case FC_WAIT:
            tp.txLastMs = nowMs;
            break;
        default:
            tp.txState = ISOTP_TX_IDLE;
            break;
    }
}

static void on_frame(uint32_t id, uint8_t const* data, uint8_t len, uint32_t nowMs)
{
    bool const functional = (id == BL_CAN_ID_REQUEST_FUNC);
    if (((id != BL_CAN_ID_REQUEST_PHYS) && !functional) || (len == 0U))
    {
        return;
    }
    uint8_t const pci = (uint8_t)(data[0] >> 4);
    if (functional && (pci != PCI_SF))
    {
        return;
    }
    if ((tp.rxState == ISOTP_RX_COMPLETE) && (pci != PCI_FC))
    {
        return; /* previous request not processed yet */
    }
    switch (pci)
    {
        case PCI_SF: on_single_frame(data, len, functional); break;
        case PCI_FF: on_first_frame(data, len, nowMs); break;
        case PCI_CF: on_consecutive_frame(data, len, nowMs); break;
        case PCI_FC: on_flow_control(data, len, nowMs); break;
        default: break;
    }
}

void bl_transport_init(void)
{
    (void)memset(&tp, 0, sizeof(tp));
    tp.rxState = ISOTP_RX_IDLE;
    tp.txState = ISOTP_TX_IDLE;
}

/* ISO 15765-2 carries up to 4095 bytes in a segmented message; what this bootloader holds is its own buffer. */
uint16_t bl_transport_max_message_len(void) { return (uint16_t)BL_UDS_BUFFER_SIZE; }

/* BL-25: whether more than `limitMs` have passed since `sinceMs`. A time stamp "after" now (difference in the upper
 * half of the range) counts as no time passed, not as 49 days: the tick is an interrupt, and a stamp taken from a
 * newer reading than the caller's `nowMs` must not expire a timer. */
static bool timer_expired(uint32_t nowMs, uint32_t sinceMs, uint32_t limitMs)
{
    uint32_t const elapsed = nowMs - sinceMs;
    bool expired           = false;
    if ((elapsed <= 0x7FFFFFFFUL) && (elapsed > limitMs))
    {
        expired = true;
    }
    return expired;
}

void bl_transport_poll(uint32_t nowMs)
{
    uint32_t id;
    uint8_t data[8];
    uint8_t len;
    /* BL-25: every frame of this poll is stamped with the poll's own time, the one the timers below compare
     * against. Before, each frame took a fresh hal_millis(): when the tick fell between the caller's reading and
     * a frame's, rxLastMs lay one millisecond after nowMs, nowMs - rxLastMs wrapped to 0xFFFFFFFF, and the N_Cr
     * check dropped the message being received without a word. A 1026-byte TransferData spans 147 frames and
     * some 40 ms, so at the board nearly every first block was lost (bl_transfer_fail.asc, 2026-09-21). */
    for (uint8_t i = 0U; (i < MAX_FRAMES_PER_POLL) && hal_can_receive(&id, data, &len); ++i)
    {
        on_frame(id, data, len, nowMs);
    }

    if ((tp.rxState == ISOTP_RX_RECEIVING) && timer_expired(nowMs, tp.rxLastMs, BL_ISOTP_N_CR_MS))
    {
        tp.rxState = ISOTP_RX_IDLE;
    }

    if ((tp.txState == ISOTP_TX_WAIT_FC) && timer_expired(nowMs, tp.txLastMs, BL_ISOTP_N_BS_MS))
    {
        tp.txState = ISOTP_TX_IDLE;
    }

    while ((tp.txState == ISOTP_TX_SENDING) && ((nowMs - tp.txLastMs) >= tp.txStminMs) && hal_can_tx_idle())
    {
        uint8_t frame[8];
        uint16_t remaining = (uint16_t)(tp.txLen - tp.txPos);
        uint8_t chunk      = (remaining > 7U) ? 7U : (uint8_t)remaining;
        frame[0]           = (uint8_t)((PCI_CF << 4) | tp.txSn);
        (void)memcpy(&frame[1], &tp.txBuf[tp.txPos], chunk);
        if (!send_frame(frame, (uint8_t)(chunk + 1U)))
        {
            break;
        }
        tp.txPos    = (uint16_t)(tp.txPos + chunk);
        tp.txSn     = (uint8_t)((tp.txSn + 1U) & 0x0FU);
        tp.txLastMs = nowMs;

        if (tp.txPos >= tp.txLen)
        {
            tp.txState = ISOTP_TX_IDLE;
        }
        else if (tp.txBlockSize != 0U)
        {
            tp.txBlockCount++;
            if (tp.txBlockCount >= tp.txBlockSize)
            {
                tp.txState = ISOTP_TX_WAIT_FC;
            }
        }
        if (tp.txStminMs != 0U)
        {
            break;
        }
    }
}

uint8_t const* bl_transport_receive(uint16_t* len, bool* functional)
{
    if (tp.rxState != ISOTP_RX_COMPLETE)
    {
        return NULL;
    }
    *len        = tp.rxLen;
    *functional = tp.rxFunctional;
    return tp.rxBuf;
}

void bl_transport_release(void) { tp.rxState = ISOTP_RX_IDLE; }

/* BL-29: RECEIVING from the first frame on, COMPLETE until the core releases the message. A wrong sequence
 * number, a frame too short or an N_Cr timeout return to IDLE - the error indication that restarts S3. */
bool bl_transport_rx_pending(void) { return tp.rxState != ISOTP_RX_IDLE; }

bool bl_transport_send(uint8_t const* msg, uint16_t len, uint32_t nowMs)
{
    if ((tp.txState != ISOTP_TX_IDLE) || (len == 0U) || (len > sizeof(tp.txBuf)))
    {
        return false;
    }
    if (len <= 7U)
    {
        uint8_t frame[8];
        frame[0] = (uint8_t)((PCI_SF << 4) | len);
        (void)memcpy(&frame[1], msg, len);
        return send_frame(frame, (uint8_t)(len + 1U));
    }
    uint8_t frame[8];
    frame[0] = (uint8_t)((PCI_FF << 4) | ((len >> 8) & 0x0FU));
    frame[1] = (uint8_t)(len & 0xFFU);
    (void)memcpy(&frame[2], msg, 6U);
    if (!send_frame(frame, 8U))
    {
        return false;
    }
    (void)memcpy(tp.txBuf, msg, len);
    tp.txLen    = len;
    tp.txPos    = 6U;
    tp.txSn     = 1U;
    tp.txLastMs = nowMs;
    tp.txState  = ISOTP_TX_WAIT_FC;
    return true;
}

bool bl_transport_tx_idle(void) { return tp.txState == ISOTP_TX_IDLE; }

/* Idle also means the last frame has left the mailbox: the core resets and jumps only after that. */
bool bl_transport_tx_done(void) { return (tp.txState == ISOTP_TX_IDLE) && hal_can_tx_idle(); }

#else

/* ISO C forbids an empty translation unit: with another transport selected this file contributes nothing. */
typedef int bl_transport_can_unused_t;

#endif /* BL_TRANSPORT == BL_TRANSPORT_CAN */
