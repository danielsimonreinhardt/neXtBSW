/* SPDX-License-Identifier: Apache-2.0 */

/* mcuLink - inter-MCU link protocol (BL-15). The reasoning behind every mechanism is in McuLink.h; this file
 * keeps only what a reader of the code needs.
 *
 * Written for the bootloader's budget: no table, no division, no 32-bit arithmetic where 16 bits do, one
 * translation unit, and the state flattened out of the configuration struct so the hot paths do one load
 * instead of two. */

#include "mcuLink/McuLink.h"

#include <string.h>

#define SEQ_MASK 0x0FU

/* ---------------------------------------------------------------- CRC */

uint16_t mcuLink_crc16(uint8_t const* data, uint16_t len)
{
    uint16_t crc = 0xFFFFU;
    for (uint16_t i = 0U; i < len; ++i)
    {
        crc ^= (uint16_t)((uint16_t)data[i] << 8);
        for (uint8_t bit = 0U; bit < 8U; ++bit)
        {
            crc = ((crc & 0x8000U) != 0U) ? (uint16_t)((uint16_t)(crc << 1) ^ 0x1021U) : (uint16_t)(crc << 1);
        }
    }
    return crc;
}

/* ---------------------------------------------------------------- internals */

/* Bytes of the segment that starts at `pos`; the last one is short. */
static uint16_t segment_len(mcuLink_t const* lnk, uint16_t pos)
{
    uint16_t const left = (uint16_t)(lnk->txLen - pos);
    return (left > lnk->segLen) ? (uint16_t)lnk->segLen : left;
}

/* Segments handed to a frame but not yet acknowledged. */
static uint8_t outstanding(mcuLink_t const* lnk)
{
    return (uint8_t)((lnk->txNextSeq - lnk->txBaseSeq) & SEQ_MASK);
}

static void rewind_tx(mcuLink_t* lnk)
{
    if (lnk->txNext != lnk->txBase)
    {
        lnk->txNext    = lnk->txBase;
        lnk->txNextSeq = lnk->txBaseSeq;
        lnk->stats.retransmits++;
    }
}

/* The peer restarted: no partial state of either direction may be trusted (multi-mcu-flash.md section 10.2). */
static void drop_dialogue(mcuLink_t* lnk)
{
    bool const inFlight = lnk->txActive || lnk->rxActive || lnk->rxComplete;
    if (inFlight)
    {
        lnk->events |= MCULINK_EVT_PEER_RESTART;
    }
    lnk->txActive   = false;
    lnk->txLen      = 0U;
    lnk->txBase     = 0U;
    lnk->txNext     = 0U;
    lnk->rxActive   = false;
    lnk->rxComplete = false;
    lnk->rxLen      = 0U;
    lnk->rxAckValid = false;
    lnk->tries      = 0U;
}

/* ---------------------------------------------------------------- message level */

bool mcuLink_init(mcuLink_t* lnk, mcuLinkConfig_t const* cfg, uint32_t nowMs)
{
    if ((lnk == NULL) || (cfg == NULL) || (cfg->rxMsg == NULL) || (cfg->txMsg == NULL)
        || (cfg->frameLen < MCULINK_MIN_FRAME_LEN) || (cfg->rxMsgSize == 0U) || (cfg->txMsgSize == 0U)
        || (cfg->window == 0U) || (cfg->window > (SEQ_MASK - 1U)))
    {
        return false;
    }

    (void)memset(lnk, 0, sizeof(*lnk));
    lnk->rxMsg         = cfg->rxMsg;
    lnk->txMsg         = cfg->txMsg;
    lnk->rxMsgSize     = cfg->rxMsgSize;
    lnk->txMsgSize     = cfg->txMsgSize;
    lnk->ackTimeoutMs  = cfg->ackTimeoutMs;
    lnk->pollPeriodMs  = cfg->pollPeriodMs;
    lnk->frameLen      = cfg->frameLen;
    lnk->segLen        = (uint8_t)(cfg->frameLen - MCULINK_FRAME_OVERHEAD);
    lnk->window        = cfg->window;
    lnk->retries       = cfg->retries;
    lnk->session       = cfg->session;
    lnk->master        = cfg->master;
    lnk->readyLine     = cfg->readyLine;

    /* Until the peer's line has been read, a wired link is assumed not ready; an unwired one always is. */
    lnk->peerReady      = !cfg->readyLine;
    lnk->announceReset  = true;
    lnk->lastExchangeMs = nowMs;
    lnk->txMarkMs       = nowMs;
    return true;
}

uint16_t mcuLink_maxMessageLen(mcuLink_t const* lnk)
{
    return (lnk->rxMsgSize < lnk->txMsgSize) ? lnk->rxMsgSize : lnk->txMsgSize;
}

bool mcuLink_send(mcuLink_t* lnk, uint8_t const* msg, uint16_t len)
{
    if (lnk->txActive || (len == 0U) || (len > lnk->txMsgSize))
    {
        return false;
    }
    (void)memcpy(lnk->txMsg, msg, len);
    lnk->txLen = len;
    /* The sequence number runs on across messages, so a frame of the previous one can never be mistaken for
     * the first of this one. MCULINK_F_FIRST, not the number, marks the start. */
    lnk->txBase    = 0U;
    lnk->txNext    = 0U;
    lnk->txBaseSeq = lnk->txNextSeq;
    lnk->tries     = 0U;
    lnk->txActive  = true;
    return true;
}

uint8_t const* mcuLink_receive(mcuLink_t* lnk, uint16_t* len)
{
    if (!lnk->rxComplete)
    {
        return NULL;
    }
    *len = lnk->rxLen;
    return lnk->rxMsg;
}

void mcuLink_release(mcuLink_t* lnk)
{
    lnk->rxComplete = false;
    lnk->rxLen      = 0U;
}

bool mcuLink_txIdle(mcuLink_t const* lnk) { return !lnk->txActive; }
bool mcuLink_txDone(mcuLink_t const* lnk) { return !lnk->txActive; }

uint8_t mcuLink_events(mcuLink_t* lnk)
{
    uint8_t const events = lnk->events;
    lnk->events          = 0U;
    return events;
}

void mcuLink_poll(mcuLink_t* lnk, uint32_t nowMs)
{
    /* A peer that says BUSY is doing its job, not failing: the retry budget must not run down while it works
     * (McuLink.h, flow control). The deadline over such a wait is P2* of the UDS server above. */
    if (!lnk->txActive || (lnk->txNext == lnk->txBase) || lnk->peerBusy)
    {
        return;
    }
    if ((uint32_t)(nowMs - lnk->txMarkMs) < (uint32_t)lnk->ackTimeoutMs)
    {
        return;
    }
    lnk->txMarkMs = nowMs;
    if (lnk->tries >= lnk->retries)
    {
        lnk->events |= MCULINK_EVT_TX_FAILED;
        lnk->txActive = false;
        lnk->txLen    = 0U;
        lnk->txBase   = 0U;
        lnk->txNext   = 0U;
        return;
    }
    lnk->tries++;
    rewind_tx(lnk);
}

/* ---------------------------------------------------------------- frame level */

void mcuLink_frameOut(mcuLink_t* lnk, uint8_t* frame, uint32_t nowMs)
{
    mcuLink_poll(lnk, nowMs);

    (void)memset(frame, 0, lnk->frameLen);
    frame[0] = MCULINK_SOF;

    uint8_t flags = 0U;
    uint8_t seq   = 0U;
    uint8_t len   = 0U;

    if (lnk->announceReset)
    {
        flags |= MCULINK_F_RESET;
    }
    if (lnk->rxAckValid)
    {
        flags |= MCULINK_F_ACK;
    }
    if (lnk->sendNack)
    {
        flags |= MCULINK_F_NACK;
        lnk->sendNack = false;
    }
    if (lnk->rxComplete)
    {
        /* The upper layer has not fetched the last message; a further segment would have nowhere to go. */
        flags |= MCULINK_F_BUSY;
    }

    bool const windowOpen = (outstanding(lnk) < lnk->window);
    if (lnk->txActive && (lnk->txNext < lnk->txLen) && windowOpen && lnk->peerReady && !lnk->peerBusy)
    {
        uint16_t const n = segment_len(lnk, lnk->txNext);
        (void)memcpy(&frame[MCULINK_HEADER_LEN], &lnk->txMsg[lnk->txNext], n);
        flags |= MCULINK_F_DATA;
        if (lnk->txNext == 0U)
        {
            flags |= MCULINK_F_FIRST;
        }
        if ((uint16_t)(lnk->txNext + n) >= lnk->txLen)
        {
            flags |= MCULINK_F_LAST;
        }
        seq = lnk->txNextSeq;
        len = (uint8_t)n;
        if (lnk->txNext == lnk->txBase)
        {
            lnk->txMarkMs = nowMs; /* the retry timer runs from the first unacknowledged segment */
        }
        lnk->txNext    = (uint16_t)(lnk->txNext + n);
        lnk->txNextSeq = (uint8_t)((lnk->txNextSeq + 1U) & SEQ_MASK);
    }
    else if (lnk->txActive && lnk->peerBusy && (lnk->txNext < lnk->txLen))
    {
        lnk->stats.busyHolds++;
    }
    else
    {
        /* nothing to add to this frame; it still carries the acknowledgement and the state */
    }

    frame[1] = flags;
    frame[2] = lnk->session;
    frame[3] = (uint8_t)((seq & SEQ_MASK) | (uint8_t)((lnk->rxAckSeq & SEQ_MASK) << 4));
    frame[4] = len;

    uint16_t const crc            = mcuLink_crc16(frame, (uint16_t)(lnk->frameLen - MCULINK_CRC_LEN));
    frame[lnk->frameLen - 2U]     = (uint8_t)(crc >> 8);
    frame[lnk->frameLen - 1U]     = (uint8_t)crc;
    lnk->lastExchangeMs           = nowMs;
    lnk->stats.framesOut++;
}

void mcuLink_frameIn(mcuLink_t* lnk, uint8_t const* frame, uint32_t nowMs)
{
    if (frame[0] != MCULINK_SOF)
    {
        /* No peer on the wire yet, no clock, or the transaction was cut: 0x00 and 0xFF both land here. */
        lnk->stats.framesBad++;
        return;
    }
    uint16_t const crc = (uint16_t)(((uint16_t)frame[lnk->frameLen - 2U] << 8) | frame[lnk->frameLen - 1U]);
    if (crc != mcuLink_crc16(frame, (uint16_t)(lnk->frameLen - MCULINK_CRC_LEN)))
    {
        lnk->stats.framesBad++;
        lnk->stats.crcErrors++;
        lnk->sendNack = true; /* tell the sender at once instead of spending its timeout */
        return;
    }

    uint8_t const flags = frame[1];
    uint8_t const sess  = frame[2];
    uint8_t const seq   = (uint8_t)(frame[3] & SEQ_MASK);
    uint8_t const ack   = (uint8_t)((frame[3] >> 4) & SEQ_MASK);
    uint8_t const len   = frame[4];
    if (len > lnk->segLen)
    {
        lnk->stats.framesBad++;
        return;
    }
    lnk->stats.framesIn++;

    /* --- session counter: has the peer restarted? (ARCH-47, multi-mcu-flash.md section 10.2) */
    bool const announces = ((flags & MCULINK_F_RESET) != 0U);
    if (!lnk->peerKnown)
    {
        lnk->peerKnown   = true;
        lnk->peerSession = sess;
    }
    else if ((sess != lnk->peerSession) || (announces && !lnk->peerWasReset))
    {
        lnk->peerSession = sess;
        drop_dialogue(lnk);
    }
    else
    {
        /* same session, nothing announced */
    }
    lnk->peerWasReset = announces;
    lnk->announceReset = false; /* the peer is there; stop announcing our own start */

    lnk->peerBusy = ((flags & MCULINK_F_BUSY) != 0U);

    /* --- our transmit side: what did the peer confirm, what did it reject? */
    if (((flags & MCULINK_F_ACK) != 0U) && lnk->txActive)
    {
        uint8_t const out = outstanding(lnk);
        uint8_t const dist = (uint8_t)((ack - lnk->txBaseSeq) & SEQ_MASK);
        if ((out != 0U) && (dist < out))
        {
            /* cumulative: everything up to and including `ack` has arrived */
            for (uint8_t i = 0U; i <= dist; ++i)
            {
                lnk->txBase = (uint16_t)(lnk->txBase + segment_len(lnk, lnk->txBase));
            }
            lnk->txBaseSeq = (uint8_t)((lnk->txBaseSeq + dist + 1U) & SEQ_MASK);
            lnk->tries     = 0U;
            lnk->txMarkMs  = nowMs;
            if (lnk->txBase >= lnk->txLen)
            {
                lnk->txActive = false;
                lnk->stats.messagesOut++;
            }
        }
    }
    if (((flags & MCULINK_F_NACK) != 0U) && lnk->txActive)
    {
        rewind_tx(lnk);
    }

    /* --- our receive side */
    if ((flags & MCULINK_F_DATA) == 0U)
    {
        return;
    }
    if (lnk->rxComplete)
    {
        /* We said BUSY in our own frames; this segment crossed it. Do not acknowledge - the sender keeps it. */
        return;
    }
    if ((flags & MCULINK_F_FIRST) != 0U)
    {
        /* A start anchors the sequence, so the two sides resynchronise after any restart without a handshake.
         * A repeated start is a retransmission of the same bytes and costs nothing. */
        lnk->rxLen       = 0U;
        lnk->rxActive    = true;
        lnk->rxExpectSeq = seq;
    }
    if (!lnk->rxActive)
    {
        lnk->stats.seqErrors++; /* a middle segment with no start: the start was lost */
        lnk->sendNack = true;
        return;
    }
    if (seq != lnk->rxExpectSeq)
    {
        lnk->stats.seqErrors++;
        lnk->sendNack = true;
        return;
    }
    if ((uint32_t)lnk->rxLen + (uint32_t)len > (uint32_t)lnk->rxMsgSize)
    {
        lnk->events |= MCULINK_EVT_RX_OVERFLOW;
        lnk->rxActive = false;
        lnk->rxLen    = 0U;
        return;
    }

    (void)memcpy(&lnk->rxMsg[lnk->rxLen], &frame[MCULINK_HEADER_LEN], len);
    lnk->rxLen       = (uint16_t)(lnk->rxLen + len);
    lnk->rxExpectSeq = (uint8_t)((seq + 1U) & SEQ_MASK);
    lnk->rxAckSeq    = seq;
    lnk->rxAckValid  = true;
    if ((flags & MCULINK_F_LAST) != 0U)
    {
        lnk->rxActive   = false;
        lnk->rxComplete = true;
        lnk->stats.messagesIn++;
    }
}

/* ---------------------------------------------------------------- readiness */

void mcuLink_peerReady(mcuLink_t* lnk, bool level)
{
    lnk->peerReady = lnk->readyLine ? level : true;
}

bool mcuLink_readyOut(mcuLink_t const* lnk) { return (!lnk->rxComplete) || lnk->txActive; }

bool mcuLink_exchangeDue(mcuLink_t const* lnk, uint32_t nowMs)
{
    if (!lnk->master)
    {
        return false; /* a slave never makes clock */
    }
    if (lnk->txActive && lnk->peerReady)
    {
        /* Either a segment is waiting or an acknowledgement is: both need a transaction to move. */
        return true;
    }
    if (lnk->readyLine && lnk->peerReady && !lnk->rxComplete)
    {
        return true; /* the line says the peer wants the bus */
    }
    return ((uint32_t)(nowMs - lnk->lastExchangeMs) >= (uint32_t)lnk->pollPeriodMs);
}
