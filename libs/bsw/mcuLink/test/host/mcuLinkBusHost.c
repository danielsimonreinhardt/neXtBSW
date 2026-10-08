/* SPDX-License-Identifier: Apache-2.0 */

/* Both sides of the link over a simulated SPI bus (BL-15, doc/architecture/multi-mcu-flash.md R9).
 *
 * R9 asks for exactly four fault pictures, and each one is a scenario below: **bit errors**, **lost frames**,
 * a **slow slave** and a **restart mid-message**. On top of that the clean case, a permanently broken wire
 * (so that failing is bounded and reported rather than hanging) and the throughput measurement that
 * section 9.4 marks as an estimate to be replaced by a measurement.
 *
 * What makes the simulation worth anything is that it reproduces the one property of a full-duplex SPI link
 * that a naive model gets wrong: **the slave has to fill its outgoing frame before it knows what the incoming
 * one contains.** Its frame for exchange k is built at the end of exchange k-1. A model without that lag
 * would let acknowledgements travel one exchange too early and would hide every window and retry bug this
 * protocol has to get right, so the exchange below is ordered deliberately:
 *
 *     master.frameOut(mTx)                 the master builds its frame just before the transaction
 *     wire: mTx -> sRx  and  sTx -> mRx    both directions cross, faults applied per direction
 *     slave.frameIn(sRx) ; slave.frameOut(sTx)   the slave answers into the *next* transaction
 *     master.frameIn(mRx)
 *
 * The bus is deterministic (a small xorshift with a fixed seed), so a failure is reproducible and a run does
 * not go green or red by luck.
 *
 * Run by libs/bsw/mcuLink/test/test_mcu_link_host.py; prints every failing check and exits non-zero. */

#include "mcuLink/McuLink.h"

#include <stdio.h>
#include <string.h>

#define UDS_BUFFER 1026U /* BL_MAX_BLOCK_LENGTH of the bootloader - the length the link has to carry */
#define MAX_FRAME  256U

static int failures;

static void check(bool ok, char const* what)
{
    if (!ok)
    {
        (void)printf("FAIL: %s\n", what);
        failures++;
    }
}

/* ---------------------------------------------------------------- the simulated bus */

typedef struct
{
    mcuLink_t master;
    mcuLink_t slave;
    uint8_t masterRx[UDS_BUFFER];
    uint8_t masterTx[UDS_BUFFER];
    uint8_t slaveRx[UDS_BUFFER];
    uint8_t slaveTx[UDS_BUFFER];

    uint8_t mFrame[MAX_FRAME]; /* the master's outgoing frame */
    uint8_t sFrame[MAX_FRAME]; /* the slave's outgoing frame, built one exchange ahead */
    uint8_t wire[MAX_FRAME];

    uint8_t frameLen;
    uint8_t slaveSession;
    uint32_t nowMs;
    uint32_t exchanges;
    uint32_t rng;

    /* fault injection */
    uint32_t bitErrorEvery;  /* every n-th frame gets one flipped bit; 0 = off */
    uint32_t lossEvery;      /* every n-th frame is lost entirely; 0 = off */
    bool wireBroken;         /* nothing arrives at all */
    uint32_t slaveHoldCycles;/* the slave's upper layer keeps a received message this many exchanges */
    uint32_t slaveHeld;
} sim_t;

static uint32_t rnd(sim_t* s)
{
    s->rng ^= s->rng << 13;
    s->rng ^= s->rng >> 17;
    s->rng ^= s->rng << 5;
    return s->rng;
}

static void link_config(mcuLinkConfig_t* cfg, uint8_t* rx, uint8_t* tx, uint8_t frameLen, uint8_t session,
                        bool master)
{
    (void)memset(cfg, 0, sizeof(*cfg));
    cfg->rxMsg         = rx;
    cfg->txMsg         = tx;
    cfg->rxMsgSize     = UDS_BUFFER;
    cfg->txMsgSize     = UDS_BUFFER;
    cfg->ackTimeoutMs  = 20U;
    cfg->pollPeriodMs  = 1U;
    cfg->frameLen      = frameLen;
    cfg->window        = 2U; /* fills the one-exchange lag of the wire */
    cfg->retries       = 8U;
    cfg->session       = session;
    cfg->master        = master;
    cfg->readyLine     = false; /* polling; the ready line has a scenario of its own */
}

static void sim_init(sim_t* s, uint8_t frameLen)
{
    (void)memset(s, 0, sizeof(*s));
    s->frameLen     = frameLen;
    s->rng          = 0x12345678UL;
    s->slaveSession = 0x41U;

    mcuLinkConfig_t cfg;
    link_config(&cfg, s->masterRx, s->masterTx, frameLen, 0x17U, true);
    check(mcuLink_init(&s->master, &cfg, 0U), "master link configuration accepted");
    link_config(&cfg, s->slaveRx, s->slaveTx, frameLen, s->slaveSession, false);
    check(mcuLink_init(&s->slave, &cfg, 0U), "slave link configuration accepted");

    /* The slave arms its peripheral once; from then on it re-arms after every transaction. */
    mcuLink_frameOut(&s->slave, s->sFrame, s->nowMs);
}

/* The slave restarts: a new power-on, a new session counter, everything else forgotten. */
static void sim_restart_slave(sim_t* s)
{
    mcuLinkConfig_t cfg;
    s->slaveSession = (uint8_t)(s->slaveSession + 1U);
    link_config(&cfg, s->slaveRx, s->slaveTx, s->frameLen, s->slaveSession, false);
    (void)mcuLink_init(&s->slave, &cfg, s->nowMs);
    s->slaveHeld = 0U;
    mcuLink_frameOut(&s->slave, s->sFrame, s->nowMs);
}

/* Moves one frame across one direction of the wire and applies the configured faults. */
static void deliver(sim_t* s, uint8_t const* from, uint8_t* to, bool* lost)
{
    (void)memcpy(to, from, s->frameLen);
    *lost = false;
    if (s->wireBroken)
    {
        (void)memset(to, 0x00, s->frameLen); /* no peer, no clock: what a real port reads */
        *lost = true;
        return;
    }
    if ((s->lossEvery != 0U) && ((rnd(s) % s->lossEvery) == 0U))
    {
        (void)memset(to, 0xFFU, s->frameLen);
        *lost = true;
        return;
    }
    if ((s->bitErrorEvery != 0U) && ((rnd(s) % s->bitErrorEvery) == 0U))
    {
        uint32_t const pos = rnd(s) % s->frameLen;
        uint8_t const bit  = (uint8_t)(1U << (rnd(s) % 8U));
        to[pos]            = (uint8_t)(to[pos] ^ bit);
    }
}

/* One SPI transaction, in the order the hardware imposes (see the file comment). */
static void exchange(sim_t* s)
{
    bool lost;

    /* The frame the slave put into its peripheral after the *previous* transaction. This snapshot is the
     * whole point: the slave's answer to what arrives now can only go out in the next transaction, and a
     * model that let the freshly built frame travel immediately would hide exactly the bugs the window and
     * the retry exist to prevent. */
    uint8_t armed[MAX_FRAME];
    (void)memcpy(armed, s->sFrame, s->frameLen);

    mcuLink_frameOut(&s->master, s->mFrame, s->nowMs);

    deliver(s, s->mFrame, s->wire, &lost);
    mcuLink_frameIn(&s->slave, s->wire, s->nowMs);

    /* The slave's upper layer, but only where a scenario asked for one: it works on a received message for
     * slaveHoldCycles transactions and fetches it afterwards. This is the slow slave - an erase of a flash
     * sector - and it is what MCULINK_F_BUSY is for. Without a configured hold the message is left lying for
     * the scenario to fetch, so that the harness never silently consumes what the test wants to inspect. */
    uint16_t len;
    if ((s->slaveHoldCycles != 0U) && (mcuLink_receive(&s->slave, &len) != NULL))
    {
        if (s->slaveHeld >= s->slaveHoldCycles)
        {
            s->slaveHeld = 0U;
            mcuLink_release(&s->slave);
        }
        else
        {
            s->slaveHeld++;
        }
    }

    mcuLink_frameOut(&s->slave, s->sFrame, s->nowMs); /* armed for the next transaction, not for this one */

    deliver(s, armed, s->wire, &lost);
    mcuLink_frameIn(&s->master, s->wire, s->nowMs);

    s->exchanges++;
    s->nowMs++;
    mcuLink_poll(&s->master, s->nowMs);
    mcuLink_poll(&s->slave, s->nowMs);
}

/* Runs transactions until the predicate holds or the budget is spent. */
static uint32_t run_until_rx(sim_t* s, mcuLink_t* who, uint16_t* len, uint8_t const** msg, uint32_t budget)
{
    for (uint32_t i = 0U; i < budget; ++i)
    {
        uint8_t const* got = mcuLink_receive(who, len);
        if (got != NULL)
        {
            *msg = got;
            return i;
        }
        exchange(s);
    }
    *msg = mcuLink_receive(who, len);
    return budget;
}

static void fill(uint8_t* buf, uint16_t len, uint8_t seed)
{
    for (uint16_t i = 0U; i < len; ++i)
    {
        buf[i] = (uint8_t)(seed + (uint8_t)i + (uint8_t)(i >> 8));
    }
}

/* ---------------------------------------------------------------- scenarios */

/* A whole UDS message of the bootloader's buffer length, in both directions, on a clean wire. */
static void scenario_clean(uint8_t frameLen)
{
    sim_t s;
    sim_init(&s, frameLen);

    uint8_t request[UDS_BUFFER];
    fill(request, UDS_BUFFER, 0x10U);
    check(mcuLink_send(&s.master, request, UDS_BUFFER), "master accepts a message of the full buffer length");

    uint16_t len          = 0U;
    uint8_t const* got    = NULL;
    (void)run_until_rx(&s, &s.slave, &len, &got, 2000U);
    check(got != NULL, "clean: the slave receives the message");
    check(len == UDS_BUFFER, "clean: the length arrives unchanged");
    check((got != NULL) && (memcmp(got, request, UDS_BUFFER) == 0), "clean: the bytes arrive unchanged");
    mcuLink_release(&s.slave);

    /* and back: a short answer, the normal shape of a UDS response */
    uint8_t const answer[3] = {0x76U, 0x01U, 0x00U};
    check(mcuLink_send(&s.slave, answer, sizeof(answer)), "slave accepts its answer");
    (void)run_until_rx(&s, &s.master, &len, &got, 2000U);
    check((got != NULL) && (len == sizeof(answer)) && (memcmp(got, answer, sizeof(answer)) == 0),
          "clean: the answer arrives unchanged");

    check(mcuLink_txDone(&s.master), "clean: the master's message is acknowledged");
    check(s.master.stats.framesBad == 0U, "clean: no damaged frame on a clean wire");
    check(mcuLink_events(&s.master) == 0U, "clean: no event on a clean wire");
    (void)printf("  clean       frame=%3u  exchanges=%4u  bad=%u retrans=%u\n", (unsigned)frameLen,
                 (unsigned)s.exchanges, (unsigned)s.master.stats.framesBad,
                 (unsigned)s.master.stats.retransmits);
}

/* R9, bit errors: the CRC has to catch them and the retry has to repair them. */
static void scenario_bit_errors(void)
{
    sim_t s;
    sim_init(&s, 64U);
    s.bitErrorEvery = 7U; /* roughly every seventh frame in each direction is damaged */

    uint8_t request[512];
    fill(request, sizeof(request), 0x33U);
    check(mcuLink_send(&s.master, request, sizeof(request)), "bit errors: message accepted");

    uint16_t len       = 0U;
    uint8_t const* got = NULL;
    (void)run_until_rx(&s, &s.slave, &len, &got, 20000U);
    check(got != NULL, "bit errors: the message still arrives");
    check((got != NULL) && (len == sizeof(request)) && (memcmp(got, request, sizeof(request)) == 0),
          "bit errors: not one damaged byte reaches the upper layer");
    check(s.slave.stats.crcErrors > 0U, "bit errors: the CRC actually caught something (else the test is blind)");
    check(s.master.stats.retransmits > 0U, "bit errors: the retry actually ran");
    check((mcuLink_events(&s.master) & MCULINK_EVT_PEER_RESTART) == 0U,
          "bit errors: a damaged frame is never mistaken for a restart");
    (void)printf("  bit errors  crc=%u/%u  retrans=%u  exchanges=%u\n", (unsigned)s.slave.stats.crcErrors,
                 (unsigned)s.slave.stats.framesIn, (unsigned)s.master.stats.retransmits,
                 (unsigned)s.exchanges);
}

/* R9, lost frames: a transaction that never happened, in either direction. */
static void scenario_lost_frames(void)
{
    sim_t s;
    sim_init(&s, 64U);
    s.lossEvery = 5U;

    uint8_t request[512];
    fill(request, sizeof(request), 0x77U);
    check(mcuLink_send(&s.master, request, sizeof(request)), "lost frames: message accepted");

    uint16_t len       = 0U;
    uint8_t const* got = NULL;
    (void)run_until_rx(&s, &s.slave, &len, &got, 20000U);
    check(got != NULL, "lost frames: the message still arrives");
    check((got != NULL) && (len == sizeof(request)) && (memcmp(got, request, sizeof(request)) == 0),
          "lost frames: the bytes arrive unchanged");
    check(s.slave.stats.framesBad > 0U, "lost frames: losses really happened");
    (void)printf("  lost frames lost=%u  retrans=%u  exchanges=%u\n", (unsigned)s.slave.stats.framesBad,
                 (unsigned)s.master.stats.retransmits, (unsigned)s.exchanges);
}

/* R9, a slave too slow: flow control, not an error. A slave erasing a sector holds the link for a long time
 * and must not be spent out of the master's retry budget (McuLink.h, flow control). */
static void scenario_slow_slave(void)
{
    sim_t s;
    sim_init(&s, 64U);
    s.slaveHoldCycles = 400U; /* the slave keeps the first message for 400 transactions */

    uint8_t first[64];
    uint8_t second[64];
    fill(first, sizeof(first), 0x01U);
    fill(second, sizeof(second), 0x02U);
    check(mcuLink_send(&s.master, first, sizeof(first)), "slow slave: first message accepted");

    uint16_t len       = 0U;
    uint8_t const* got = NULL;
    for (uint32_t i = 0U; (i < 5000U) && !mcuLink_txDone(&s.master); ++i)
    {
        exchange(&s);
    }
    check(mcuLink_txDone(&s.master), "slow slave: the first message gets through");

    /* Now the slave is busy with it. The second message must wait, not fail. */
    check(mcuLink_send(&s.master, second, sizeof(second)), "slow slave: second message accepted");
    for (uint32_t i = 0U; i < 200U; ++i)
    {
        exchange(&s);
    }
    check(!mcuLink_txDone(&s.master), "slow slave: the second message is held back while the slave is busy");
    check((mcuLink_events(&s.master) & MCULINK_EVT_TX_FAILED) == 0U,
          "slow slave: a busy peer never spends the retry budget");
    check(s.master.stats.busyHolds > 0U, "slow slave: flow control really engaged");

    /* The slave is still holding the first message, untouched by everything the master tried meanwhile. */
    got = mcuLink_receive(&s.slave, &len);
    check((got != NULL) && (len == sizeof(first)) && (memcmp(got, first, sizeof(first)) == 0),
          "slow slave: the held message is not overwritten by the one waiting");

    /* Once it is fetched, the second goes through without anyone having repeated the request. */
    for (uint32_t i = 0U; (i < 5000U) && !mcuLink_txDone(&s.master); ++i)
    {
        exchange(&s);
    }
    check(mcuLink_txDone(&s.master), "slow slave: the second message gets through once the slave is free");
    got = mcuLink_receive(&s.slave, &len);
    check((got != NULL) && (len == sizeof(second)) && (memcmp(got, second, sizeof(second)) == 0),
          "slow slave: and it is the second message, unchanged");
    (void)printf("  slow slave  busyHolds=%u  exchanges=%u\n", (unsigned)s.master.stats.busyHolds,
                 (unsigned)s.exchanges);
}

/* The case the scenario above does not reach: BUSY arrives while a segment is already unacknowledged, which
 * is what happens when a slave starts erasing between two segments. The frame is built by hand because the
 * harness cannot make the peer busy at that exact instant.
 *
 * Honest note on what this proves, measured by mutating the source: deleting the `peerBusy` term from
 * mcuLink_poll() does **not** make this scenario red, and that is a property of the design rather than a hole
 * in the test. Flow control is already enforced in mcuLink_frameOut(), which holds the segment back; and the
 * first rewind collapses the window (txNext == txBase), after which the retry timer stops by itself. So the
 * term in mcuLink_poll() can cost at most one spurious retry per busy episode and can never exhaust the
 * budget. It is kept as a second line of defence, not because this test would catch its loss. What the
 * scenario does prove is the behaviour the upper layer depends on: a busy peer never fails the message. */
static void scenario_busy_stops_the_retry_timer(void)
{
    sim_t s;
    sim_init(&s, 64U);

    uint8_t msg[128];
    fill(msg, sizeof(msg), 0x5AU);
    check(mcuLink_send(&s.master, msg, sizeof(msg)), "busy timer: message accepted");
    exchange(&s);
    check(s.master.txNext != s.master.txBase, "busy timer: a segment really is in flight");

    /* The peer's next frame says BUSY and acknowledges nothing. */
    uint8_t frame[64];
    (void)memcpy(frame, s.sFrame, sizeof(frame));
    frame[1] = (uint8_t)((frame[1] | MCULINK_F_BUSY) & (uint8_t)(~(MCULINK_F_ACK | MCULINK_F_DATA)));
    uint16_t const crc = mcuLink_crc16(frame, (uint16_t)(sizeof(frame) - 2U));
    frame[sizeof(frame) - 2U] = (uint8_t)(crc >> 8);
    frame[sizeof(frame) - 1U] = (uint8_t)crc;
    mcuLink_frameIn(&s.master, frame, s.nowMs);
    check(s.master.peerBusy, "busy timer: the master sees the peer as busy");

    /* Far longer than ackTimeoutMs * retries. A retry timer that kept running would give up in here. */
    for (uint32_t t = 0U; t < (20U * (8U + 4U)); ++t)
    {
        mcuLink_poll(&s.master, s.nowMs + t);
    }
    check((mcuLink_events(&s.master) & MCULINK_EVT_TX_FAILED) == 0U,
          "busy timer: a peer that is merely busy never spends the retry budget");
    check(!mcuLink_txIdle(&s.master), "busy timer: the message is still there, waiting rather than failed");

    /* And it is not simply stuck: once the peer stops saying BUSY the message completes. */
    s.nowMs += 20U * 12U;
    uint16_t len       = 0U;
    uint8_t const* got = NULL;
    (void)run_until_rx(&s, &s.slave, &len, &got, 2000U);
    check((got != NULL) && (len == sizeof(msg)) && (memcmp(got, msg, sizeof(msg)) == 0),
          "busy timer: the message goes through once the peer is free again");
    (void)printf("  busy timer  retry budget kept, message delivered\n");
}

/* R9 and ARCH-47: a restart in the middle of a message. Without the session counter this is invisible and the
 * download continues onto an MCU that has forgotten its RequestDownload (multi-mcu-flash.md section 10.2). */
static void scenario_restart_mid_message(void)
{
    sim_t s;
    sim_init(&s, 64U);

    uint8_t request[1026];
    fill(request, sizeof(request), 0x5CU);
    check(mcuLink_send(&s.master, request, sizeof(request)), "restart: message accepted");

    for (uint32_t i = 0U; i < 6U; ++i) /* a few segments are already on the slave */
    {
        exchange(&s);
    }
    check(s.slave.rxLen > 0U, "restart: the slave really had a partial message (else the test proves nothing)");
    check((mcuLink_events(&s.master) & MCULINK_EVT_PEER_RESTART) == 0U, "restart: nothing latched too early");

    sim_restart_slave(&s);
    for (uint32_t i = 0U; i < 10U; ++i)
    {
        exchange(&s);
    }

    uint8_t const events = mcuLink_events(&s.master);
    check((events & MCULINK_EVT_PEER_RESTART) != 0U,
          "restart: the master notices the slave's restart (session counter) -> NRC 0x24");
    check(mcuLink_txIdle(&s.master), "restart: the message in flight is dropped, not silently continued");

    uint16_t len       = 0U;
    uint8_t const* got = mcuLink_receive(&s.slave, &len);
    check(got == NULL, "restart: the slave's partial message is gone, not delivered as a short one");

    /* And the tester repeats the sequence: the link has to work again without any reset of the master. */
    check(mcuLink_send(&s.master, request, sizeof(request)), "restart: the link takes a new message");
    (void)run_until_rx(&s, &s.slave, &len, &got, 5000U);
    check((got != NULL) && (len == sizeof(request)) && (memcmp(got, request, sizeof(request)) == 0),
          "restart: the repeated message goes through unchanged (multi-mcu-flash.md section 10.1)");
    (void)printf("  restart     detected, repeat ok, exchanges=%u\n", (unsigned)s.exchanges);
}

/* Failing has to be bounded and reported: a wire that is simply dead. */
static void scenario_broken_wire(void)
{
    sim_t s;
    sim_init(&s, 64U);
    s.wireBroken = true;

    uint8_t msg[32];
    fill(msg, sizeof(msg), 0x99U);
    check(mcuLink_send(&s.master, msg, sizeof(msg)), "broken wire: message accepted");
    for (uint32_t i = 0U; (i < 4000U) && mcuLink_txIdle(&s.master) == false; ++i)
    {
        exchange(&s);
    }
    check(mcuLink_txIdle(&s.master), "broken wire: the attempt ends instead of hanging");
    check((mcuLink_events(&s.master) & MCULINK_EVT_TX_FAILED) != 0U,
          "broken wire: the failure is reported -> NRC 0x22 (multi-mcu-flash.md section 9.5)");
    check(s.master.stats.framesBad > 0U, "broken wire: an absent peer is seen as such");
    (void)printf("  broken wire reported after %u exchanges\n", (unsigned)s.exchanges);
}

/* A message longer than the peer's buffer must be refused, not overrun it. */
static void scenario_overflow(void)
{
    sim_t s;
    sim_init(&s, 64U);
    check(!mcuLink_send(&s.master, (uint8_t const*)"x", 0U), "a message of zero bytes is refused");
    check(mcuLink_maxMessageLen(&s.master) == UDS_BUFFER, "the maximum message length is the buffer length");

    uint8_t big[UDS_BUFFER + 1U];
    fill(big, sizeof(big), 0x22U);
    check(!mcuLink_send(&s.master, big, (uint16_t)(UDS_BUFFER + 1U)),
          "a message longer than the buffer is refused, not truncated");
    check(mcuLink_send(&s.master, big, UDS_BUFFER), "a message of exactly the buffer length is accepted");

    /* A frame shorter than the header cannot carry anything: a configuration error, refused at init. */
    mcuLink_t bad;
    mcuLinkConfig_t cfg;
    link_config(&cfg, s.masterRx, s.masterTx, MCULINK_FRAME_OVERHEAD, 1U, true);
    check(!mcuLink_init(&bad, &cfg, 0U), "a frame with no room for payload is refused at init");
    link_config(&cfg, s.masterRx, s.masterTx, 64U, 1U, true);
    cfg.window = 0U;
    check(!mcuLink_init(&bad, &cfg, 0U), "a window of zero is refused at init");
    (void)printf("  limits      max message = %u bytes\n", (unsigned)mcuLink_maxMessageLen(&s.master));
}

/* The ready line is the other half of section 9.4: the same protocol, a different reason to make clock. */
static void scenario_ready_line(void)
{
    sim_t s;
    (void)memset(&s, 0, sizeof(s));
    s.frameLen = 64U;
    s.rng      = 0xABCDEF01UL;

    mcuLinkConfig_t cfg;
    link_config(&cfg, s.masterRx, s.masterTx, 64U, 0x17U, true);
    cfg.readyLine    = true;
    cfg.pollPeriodMs = 1000U; /* deliberately far too slow: only the line may drive this link */
    check(mcuLink_init(&s.master, &cfg, 0U), "ready line: master configured");
    link_config(&cfg, s.slaveRx, s.slaveTx, 64U, 0x41U, false);
    check(mcuLink_init(&s.slave, &cfg, 0U), "ready line: slave configured");
    mcuLink_frameOut(&s.slave, s.sFrame, 0U);

    uint8_t msg[16];
    fill(msg, sizeof(msg), 0x44U);
    check(mcuLink_send(&s.master, msg, sizeof(msg)), "ready line: message accepted");

    mcuLink_peerReady(&s.master, false);
    check(!mcuLink_exchangeDue(&s.master, 1U), "ready line: a slave that is not ready is left alone");

    check(mcuLink_readyOut(&s.slave), "ready line: an empty slave reports itself ready");
    mcuLink_peerReady(&s.master, mcuLink_readyOut(&s.slave));
    check(mcuLink_exchangeDue(&s.master, 1U), "ready line: the line, not the poll period, starts the transfer");

    uint16_t len       = 0U;
    uint8_t const* got = NULL;
    for (uint32_t i = 0U; (i < 500U) && (got == NULL); ++i)
    {
        mcuLink_peerReady(&s.master, mcuLink_readyOut(&s.slave));
        if (mcuLink_exchangeDue(&s.master, s.nowMs))
        {
            exchange(&s);
        }
        else
        {
            s.nowMs++;
        }
        got = mcuLink_receive(&s.slave, &len);
    }
    check((got != NULL) && (len == sizeof(msg)), "ready line: the message arrives, driven by the line alone");

    /* And the line reports flow control too: a slave holding a message is not ready. */
    check(!mcuLink_readyOut(&s.slave) || (mcuLink_receive(&s.slave, &len) == NULL),
          "ready line: a slave holding an unfetched message does not claim to be ready");
    (void)printf("  ready line  driven without polling, exchanges=%u\n", (unsigned)s.exchanges);
}

/* ---------------------------------------------------------------- throughput (section 9.4, F6) */

/* Measures what the protocol really costs for the download of a 256 KB image: 256 UDS messages of 1026 bytes
 * with a short answer each, counted in transactions, converted to time at a few SPI clocks. No estimate of
 * the frame count - it is counted. Flash programming on the slave is deliberately not included; this is the
 * link's own share, which is what BL-15 was asked for. */
static void throughput(uint8_t frameLen)
{
    sim_t s;
    sim_init(&s, frameLen);

    uint8_t block[UDS_BUFFER];
    fill(block, UDS_BUFFER, 0x60U);
    uint8_t const answer[2] = {0x76U, 0x01U};

    uint32_t const blocks = 256U; /* 256 * 1024 data bytes = 256 KB */
    uint16_t len          = 0U;
    uint8_t const* got    = NULL;
    for (uint32_t b = 0U; b < blocks; ++b)
    {
        check(mcuLink_send(&s.master, block, UDS_BUFFER), "throughput: block accepted");
        (void)run_until_rx(&s, &s.slave, &len, &got, 4000U);
        if (got == NULL)
        {
            check(false, "throughput: a block did not get through");
            return;
        }
        mcuLink_release(&s.slave);
        (void)mcuLink_send(&s.slave, answer, sizeof(answer));
        (void)run_until_rx(&s, &s.master, &len, &got, 4000U);
        mcuLink_release(&s.master);
    }

    uint64_t const wireBytes = (uint64_t)s.exchanges * (uint64_t)frameLen;
    uint64_t const payload   = (uint64_t)blocks * 1024U;
    (void)printf("THROUGHPUT frame=%u exchanges=%u wirebytes=%llu payload=%llu efficiency=%u.%u%%\n",
                 (unsigned)frameLen, (unsigned)s.exchanges, (unsigned long long)wireBytes,
                 (unsigned long long)payload, (unsigned)((payload * 100U) / wireBytes),
                 (unsigned)(((payload * 1000U) / wireBytes) % 10U));
    for (uint32_t mhz = 1U; mhz <= 16U; mhz *= 2U)
    {
        /* one SPI clock carries one bit in each direction at the same time */
        uint64_t const ms = (wireBytes * 8U) / ((uint64_t)mhz * 1000U);
        (void)printf("THROUGHPUT frame=%u clock=%uMHz image256k=%llu.%03llus\n", (unsigned)frameLen,
                     (unsigned)mhz, (unsigned long long)(ms / 1000U), (unsigned long long)(ms % 1000U));
    }
}

/* ---------------------------------------------------------------- */

int main(void)
{
    (void)printf("mcuLink over a simulated SPI bus (BL-15)\n");

    scenario_clean(32U);
    scenario_clean(64U);
    scenario_clean(248U);
    scenario_bit_errors();
    scenario_lost_frames();
    scenario_slow_slave();
    scenario_busy_stops_the_retry_timer();
    scenario_restart_mid_message();
    scenario_broken_wire();
    scenario_overflow();
    scenario_ready_line();

    throughput(32U);
    throughput(64U);
    throughput(248U);

    (void)printf("%s\n", (failures == 0) ? "OK" : "FAILURES");
    return (failures == 0) ? 0 : 1;
}
