/* SPDX-License-Identifier: Apache-2.0 */
#ifndef MCULINK_MCULINK_H
#define MCULINK_MCULINK_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* mcuLink - the inter-MCU link protocol (BL-15, doc/architecture/multi-mcu-flash.md section 4 and R2).
 *
 * Carries whole messages - UDS messages up to the bootloader's buffer length - point to point between two MCUs
 * of one ECU over SPI: framing, CRC, acknowledgement, retry, flow control and a session counter. It is plain
 * C99 with no dependency on OpenBSW, on an RTOS, on a family or on a board, so the same core runs in the
 * bootloader of a slave, in the bootloader of a master (the gateway of BL-17) and in an application
 * (multi-mcu-flash.md section 6.3).
 *
 * What it is not: an SPI driver. The bytes are moved by whoever owns the hardware - bl_spi of the bootloader on
 * the master, an SPI slave peripheral on the slave, a simulated bus in the host test. This module only says
 * what the bytes mean.
 *
 *
 * The exchange
 * ------------
 * SPI is full duplex and only the master makes clock. Both facts are taken seriously instead of worked around:
 * one SPI transaction is one **frame of a fixed length in each direction at the same time**. The master's frame
 * and the slave's frame cross on the wire.
 *
 *   master |  frame k (out)  ------------\/------------>  |  slave
 *          |  frame k (in)   <-----------/\-------------  |
 *
 * Three things follow, and they are the reason for the design:
 *
 *  - **No length negotiation.** Neither side has to know in advance how long the other's answer is, which is
 *    the problem every variable-length SPI protocol has to solve with a second transaction.
 *  - **Readiness by polling costs nothing extra.** A master with nothing to say sends an idle frame; the frame
 *    it gets back carries the slave's state. The "poll" of multi-mcu-flash.md section 9.4 *is* the idle
 *    exchange, not a separate status service. A wired ready line (section 9.4, F3) only tells the master when
 *    an idle exchange would be worth making - it is configuration, never a second code path.
 *  - **The slave stays simple.** Fixed length means fixed buffers and a peripheral that can be armed once;
 *    no slave-side length parsing while the clock is already running.
 *
 * The slave has to fill its outgoing frame **before** it knows what will arrive in it. So the acknowledgement
 * for the segment of exchange k rides in the frame of exchange k+1. That one-exchange lag is a property of the
 * wire, not of this code; the window below exists to fill it.
 *
 *
 * Frame format (frameLen bytes, frameLen >= MCULINK_MIN_FRAME_LEN)
 * ----------------------------------------------------------------
 *   0                start of frame, MCULINK_SOF - tells an unclocked or absent peer (0x00/0xFF) from a real one
 *   1                flags, MCULINK_F_*
 *   2                session counter of the sender
 *   3                low nibble: sequence number of this segment; high nibble: sequence number acknowledged
 *   4                payload bytes in this frame, 0 .. frameLen - MCULINK_FRAME_OVERHEAD
 *   5 .. 4+len       payload
 *   .. frameLen-3    padding, zero
 *   frameLen-2/-1    CRC-16/CCITT-FALSE over bytes 0 .. frameLen-3, big endian
 *
 * The CRC covers the padding as well, so a frame is checked as one object of known length. Everything above
 * the frame is byte order neutral: the payload is handed through as it was given.
 *
 *
 * Sequence, acknowledgement and retry
 * -----------------------------------
 * A message is cut into segments of frameLen - MCULINK_FRAME_OVERHEAD bytes, each with a 4-bit sequence number.
 * The receiver acknowledges the highest segment it has taken in order; the acknowledgement is cumulative and
 * travels in the header of the frames going the other way, so an acknowledgement never costs a transaction of
 * its own.
 *
 * The sender keeps up to `window` segments unacknowledged (go back N). Because the whole message is already in
 * the sender's buffer, "going back" is an index, not a copy - the window costs no RAM. A window of 2 is what
 * fills the one-exchange lag above and is the useful default; 1 makes the link stop and wait.
 *
 * A segment that is not acknowledged within `ackTimeoutMs`, and a segment the receiver rejects (MCULINK_F_NACK,
 * sent on a CRC error or on a sequence gap), rewinds the sender to the first unacknowledged byte. After
 * `retries` rewinds the message fails with MCULINK_EVT_TX_FAILED; the upper layer decides what that means
 * (for the gateway of BL-17: NRC 0x22, multi-mcu-flash.md section 9.5).
 *
 *
 * Flow control
 * ------------
 * A receiver that still holds a complete message its upper layer has not fetched sets MCULINK_F_BUSY. The
 * sender then holds its segments back **and stops its retry timer**: a slow peer is flow control, not an
 * error, and must never be spent out of the retry budget. A slave that erases a flash sector for 200 ms is
 * exactly this case. The deadline over such a wait belongs to the layer that has one - P2* of the UDS server
 * (multi-mcu-flash.md section 9.3) - not to the link.
 *
 *
 * Session counter and restart (multi-mcu-flash.md section 10.2, ARCH-47 addition to BL-15)
 * ----------------------------------------------------------------------------------------
 * Every frame carries the session counter of its sender: a value that is new for each start of that MCU
 * (mcuLinkConfig_t.session; the caller takes it from a reset counter, from no-init RAM or from an entropy
 * source). A side that has not yet heard from its peer also sets MCULINK_F_RESET.
 *
 * A peer whose session counter changes, or which announces a reset, has restarted. If that happens while a
 * message is in flight in either direction, the partial state of both directions is dropped and
 * MCULINK_EVT_PEER_RESTART is latched. This is what lets the master tell "the slave rebooted in the middle of
 * the dialogue" from "the slave is slow", and it is what the tester is owed as NRC 0x24 requestSequenceError.
 * Without the counter the restart would look like a late answer and the download would continue onto an MCU
 * that has forgotten its RequestDownload.
 */

/* ---------------------------------------------------------------- frame constants */

#define MCULINK_SOF            0x5AU /* byte 0 of every frame */
#define MCULINK_HEADER_LEN     5U
#define MCULINK_CRC_LEN        2U
#define MCULINK_FRAME_OVERHEAD (MCULINK_HEADER_LEN + MCULINK_CRC_LEN) /* per frame, both directions */
#define MCULINK_MIN_FRAME_LEN  (MCULINK_FRAME_OVERHEAD + 1U)
#define MCULINK_MAX_FRAME_LEN  255U /* the length field is one byte; frameLen itself is a uint8_t */

/* Flags, byte 1 of the frame. */
#define MCULINK_F_FIRST 0x01U /* this segment starts a message */
#define MCULINK_F_LAST  0x02U /* this segment ends a message */
#define MCULINK_F_DATA  0x04U /* the frame carries a segment; the length and sequence fields are valid */
#define MCULINK_F_ACK   0x08U /* the acknowledged sequence number in the header is valid */
#define MCULINK_F_NACK  0x10U /* the sender's last frame was rejected; rewind */
#define MCULINK_F_BUSY  0x20U /* the sender of this frame cannot take a segment at the moment */
#define MCULINK_F_RESET 0x40U /* the sender has (re)started and has not heard from the peer yet */

/* ---------------------------------------------------------------- events, read with mcuLink_events() */

#define MCULINK_EVT_PEER_RESTART 0x01U /* the peer restarted mid-dialogue (session counter) -> NRC 0x24 */
#define MCULINK_EVT_TX_FAILED    0x02U /* retries exhausted; the message was not delivered -> NRC 0x22 */
#define MCULINK_EVT_RX_OVERFLOW  0x04U /* the peer sent more than the receive buffer holds; message dropped */

/* ---------------------------------------------------------------- configuration */

typedef struct
{
    uint8_t* rxMsg;     /* receive buffer of the upper layer; the longest message this side accepts */
    uint8_t* txMsg;     /* transmit buffer; a message handed to mcuLink_send() is copied into it */
    uint16_t rxMsgSize; /* bytes */
    uint16_t txMsgSize; /* bytes */

    uint16_t ackTimeoutMs; /* an unacknowledged segment rewinds the sender after this time */
    uint16_t pollPeriodMs; /* master only: how often an idle exchange is made when nothing else is due */

    uint8_t frameLen; /* bytes per SPI transaction, the same on both sides */
    uint8_t window;   /* segments allowed unacknowledged, 1..14; 2 fills the full-duplex lag */
    uint8_t retries;  /* rewinds before a message fails */
    uint8_t session;  /* this side's session counter for this start of the MCU */

    bool master;    /* this side makes the SPI clock */
    bool readyLine; /* a ready line is wired; without one the master falls back to polling */
} mcuLinkConfig_t;

/* Counters for diagnosis and for the throughput measurement of the host test. Not part of the protocol. */
typedef struct
{
    uint32_t framesOut;
    uint32_t framesIn;      /* frames that passed SOF and CRC */
    uint32_t framesBad;     /* wrong SOF, bad CRC or an impossible length field */
    uint32_t crcErrors;     /* part of framesBad */
    uint32_t seqErrors;     /* a segment out of sequence */
    uint32_t retransmits;   /* rewinds of the transmit window */
    uint32_t busyHolds;     /* frames in which a segment was held back because the peer said BUSY */
    uint32_t messagesOut;   /* messages fully acknowledged */
    uint32_t messagesIn;    /* messages fully received */
} mcuLinkStats_t;

/* ---------------------------------------------------------------- state */

typedef struct
{
    uint8_t* rxMsg;
    uint8_t* txMsg;
    uint16_t rxMsgSize;
    uint16_t txMsgSize;
    uint16_t ackTimeoutMs;
    uint16_t pollPeriodMs;
    uint8_t frameLen;
    uint8_t segLen; /* frameLen - MCULINK_FRAME_OVERHEAD, derived once */
    uint8_t window;
    uint8_t retries;
    uint8_t session;
    bool master;
    bool readyLine;

    uint8_t peerSession;
    bool peerKnown;
    bool peerBusy;
    bool peerReady;   /* level of the ready line; true throughout when no line is wired */
    bool peerWasReset;/* the peer's last frame carried MCULINK_F_RESET */
    bool announceReset;

    uint16_t txLen;
    uint16_t txBase;    /* first byte not yet acknowledged */
    uint16_t txNext;    /* first byte not yet put into a frame */
    uint8_t txBaseSeq;
    uint8_t txNextSeq;
    uint8_t tries;
    bool txActive;
    uint32_t txMarkMs;

    uint16_t rxLen;
    uint8_t rxExpectSeq;
    uint8_t rxAckSeq;
    bool rxAckValid;
    bool rxActive;
    bool rxComplete;
    bool sendNack;

    uint32_t lastExchangeMs;
    uint8_t events;
    mcuLinkStats_t stats;
} mcuLink_t;

/* ---------------------------------------------------------------- message level (the upper layer) */

/* Sets the link up. Returns false if the configuration cannot work (frame too short or too long, no buffer,
 * window out of range) - the caller has a configuration error, not a run-time one. */
bool mcuLink_init(mcuLink_t* lnk, mcuLinkConfig_t const* cfg, uint32_t nowMs);

/* The longest message this side can carry in one piece, for bl_transport_max_message_len(). */
uint16_t mcuLink_maxMessageLen(mcuLink_t const* lnk);

/* Hands one message over. False if a message is still on its way or the message does not fit. */
bool mcuLink_send(mcuLink_t* lnk, uint8_t const* msg, uint16_t len);

/* A completely received message, or NULL. It stays valid, and the link keeps telling the peer BUSY, until
 * mcuLink_release(). */
uint8_t const* mcuLink_receive(mcuLink_t* lnk, uint16_t* len);
void mcuLink_release(mcuLink_t* lnk);

/* Ready for the next message: nothing of an earlier one is still on its way. */
bool mcuLink_txIdle(mcuLink_t const* lnk);

/* Everything handed over has been acknowledged by the peer. Stronger than "it left the interface": on this
 * link the acknowledgement is the proof, which is what the core needs before it resets or jumps. */
bool mcuLink_txDone(mcuLink_t const* lnk);

/* Reads the latched events and clears them. */
uint8_t mcuLink_events(mcuLink_t* lnk);

/* Timers only (retry, nothing else). Call it in every cycle, also when no frame is exchanged. */
void mcuLink_poll(mcuLink_t* lnk, uint32_t nowMs);

/* ---------------------------------------------------------------- frame level (the driver) */

/* Fills the next outgoing frame, always exactly frameLen bytes. The master calls it before a transaction, the
 * slave whenever it can arm its peripheral. Calling it and then not putting the frame on the wire is allowed:
 * a frame that is never clocked out costs at most one retransmission. */
void mcuLink_frameOut(mcuLink_t* lnk, uint8_t* frame, uint32_t nowMs);

/* Takes one received frame of frameLen bytes. A frame from a peer that is not there yet (all zero, all ones)
 * or a damaged one is counted and dropped. */
void mcuLink_frameIn(mcuLink_t* lnk, uint8_t const* frame, uint32_t nowMs);

/* ---------------------------------------------------------------- readiness (section 9.4) */

/* The master reports the level of the peer's ready line, where one is wired. Without a line the link behaves
 * as if the peer were always ready and the master polls. */
void mcuLink_peerReady(mcuLink_t* lnk, bool level);

/* The level the slave should drive on its own ready line: it can take a segment, or it has one to deliver. */
bool mcuLink_readyOut(mcuLink_t const* lnk);

/* Master: is a transaction worth making now? True when a segment is waiting, when an acknowledgement is still
 * outstanding, when the ready line says the peer wants the bus, or when the polling period has elapsed. This
 * one function is where "ready line" and "polling" differ - and it is the only place. */
bool mcuLink_exchangeDue(mcuLink_t const* lnk, uint32_t nowMs);

/* ---------------------------------------------------------------- shared helper */

/* CRC-16/CCITT-FALSE (init 0xFFFF, polynomial 0x1021, no reflection, no final xor). Bitwise on purpose: a
 * table would cost more flash than the link itself. */
uint16_t mcuLink_crc16(uint8_t const* data, uint16_t len);

#ifdef __cplusplus
}
#endif

#endif /* MCULINK_MCULINK_H */
