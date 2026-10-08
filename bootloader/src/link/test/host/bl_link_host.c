/* The bootloader's two ends of the link, talking to each other (BL-15).
 *
 * libs/bsw/mcuLink/test/host/mcuLinkBusHost.c proves the protocol. This program proves the **bootloader
 * binding**: bl_link_master.c as the master and bl_transport_link.c as the slave, wired together through a
 * simulated SPI port behind bl_link_hal.h. That is the real topology of multi-mcu-flash.md section 3(a), not
 * two library instances.
 *
 * What it therefore shows, and the library test cannot:
 *
 *  - bl_transport_link.c really satisfies bootloader/src/core/bl_transport.h - every function of BL-14's
 *    interface is called here, and the file is compiled with the generated bl_diag.h that selects
 *    BL_TRANSPORT_LINK, exactly as a slave's bootloader will be;
 *  - the maximum message length the core will promise the tester comes out of the link (ARCH-47);
 *  - the two halves of bl_link_hal.h are enough hardware for the link - nothing else is reached for, which is
 *    what leaves BL-16 a family driver and not a redesign.
 *
 * Run by libs/bsw/mcuLink/test/test_mcu_link_host.py; prints the failing check and exits non-zero. */

#include "bl_transport.h"

#include "bl_link_hal.h"
#include "bl_link_master.h"

#include <stdio.h>
#include <string.h>

#if BL_TRANSPORT != BL_TRANSPORT_LINK
#error "this build is meant for the link transport (bl_diag.h of the test layout, BL-15)"
#endif

#define FRAME_LEN 64U

static int failures;

static void check(bool ok, char const* what)
{
    if (!ok)
    {
        (void)printf("FAIL: %s\n", what);
        failures++;
    }
}

/* ------------------------------------------------------------------ the simulated port (bl_link_hal.h) */

static uint8_t* armedTx;
static uint8_t* armedRx;
static uint8_t armedLen;
static bool exchangeHappened;
static bool slaveReadyLevel = true;
static uint32_t transfers;

void bl_link_init(void) { armedTx = NULL; }

uint8_t bl_link_session(void) { return 0x41U; }

void bl_link_arm(uint8_t* txFrame, uint8_t* rxFrame, uint8_t len)
{
    armedTx  = txFrame;
    armedRx  = rxFrame;
    armedLen = len;
}

bool bl_link_exchange_done(void)
{
    bool const done  = exchangeHappened;
    exchangeHappened = false;
    return done;
}

void bl_link_ready_out(bool level) { slaveReadyLevel = level; }

bool bl_link_ready_in(uint8_t port)
{
    (void)port;
    return slaveReadyLevel;
}

bool bl_link_transfer(uint8_t port, uint8_t const* tx, uint8_t* rx, uint8_t len)
{
    (void)port;
    if ((armedTx == NULL) || (len != armedLen))
    {
        return false; /* the slave has not armed its peripheral: a transaction would read nothing */
    }
    /* Full duplex: both frames cross in the same clock. The slave's frame is the one it armed earlier. */
    uint8_t buffer[FRAME_LEN];
    (void)memcpy(buffer, armedTx, len);
    (void)memcpy(armedRx, tx, len);
    (void)memcpy(rx, buffer, len);
    exchangeHappened = true;
    transfers++;
    return true;
}

/* ------------------------------------------------------------------ the master's side */

static bl_link_master_t master;
static uint8_t masterRx[BL_UDS_BUFFER_SIZE];
static uint8_t masterTx[BL_UDS_BUFFER_SIZE];
static uint8_t masterFrameTx[FRAME_LEN];
static uint8_t masterFrameRx[FRAME_LEN];

static void master_open(uint32_t nowMs)
{
    mcuLinkConfig_t cfg;
    (void)memset(&cfg, 0, sizeof(cfg));
    cfg.rxMsg        = masterRx;
    cfg.txMsg        = masterTx;
    cfg.rxMsgSize    = (uint16_t)sizeof(masterRx);
    cfg.txMsgSize    = (uint16_t)sizeof(masterTx);
    cfg.ackTimeoutMs = 20U;
    cfg.pollPeriodMs = 1U;
    cfg.frameLen     = FRAME_LEN;
    cfg.window       = 2U;
    cfg.retries      = 8U;
    cfg.session      = 0x17U;
    cfg.readyLine    = false;
    check(bl_link_master_open(&master, 0U, &cfg, masterFrameTx, masterFrameRx, nowMs),
          "the master's end of the link opens");
}

/* One cycle of both bootloaders, as bl_poll() will run them. */
static uint32_t nowMs;

static void cycle(void)
{
    bl_link_master_poll(&master, nowMs);
    bl_transport_poll(nowMs);
    nowMs++;
}

int main(void)
{
    bl_transport_init(); /* the slave's bootloader */
    master_open(0U);

    check(bl_transport_max_message_len() == BL_UDS_BUFFER_SIZE,
          "the slave's transport carries a whole UDS message of the bootloader's buffer");
    check(bl_transport_tx_idle(), "a fresh transport has nothing on its way");
    check(bl_transport_tx_done(), "and nothing outstanding");
    check(bl_transport_receive(&(uint16_t){0}, &(bool){false}) == NULL, "and nothing received");

    /* --- the master sends a request of the full block length, as a TransferData will */
    uint8_t request[BL_UDS_BUFFER_SIZE];
    for (uint16_t i = 0U; i < sizeof(request); ++i)
    {
        request[i] = (uint8_t)(0x36U + i);
    }
    check(mcuLink_send(&master.link, request, (uint16_t)sizeof(request)), "the master hands the request over");

    uint16_t len        = 0U;
    bool functional     = true;
    uint8_t const* got  = NULL;
    for (uint32_t i = 0U; (i < 4000U) && (got == NULL); ++i)
    {
        cycle();
        got = bl_transport_receive(&len, &functional);
    }
    check(got != NULL, "the slave's bootloader receives the request through bl_transport_receive()");
    check(len == sizeof(request), "at its full length");
    check((got != NULL) && (memcmp(got, request, sizeof(request)) == 0), "and unchanged");
    check(!functional, "a point-to-point link never reports a functionally addressed request");
    bl_transport_release();

    /* --- and the slave answers, as uds.c will */
    uint8_t const response[4] = {0x76U, 0x01U, 0x00U, 0x00U};
    check(bl_transport_send(response, (uint16_t)sizeof(response), nowMs), "the slave's core sends its answer");
    check(!bl_transport_tx_idle(), "while it is on its way the transport is not idle");

    got = NULL;
    for (uint32_t i = 0U; (i < 4000U) && (got == NULL); ++i)
    {
        cycle();
        got = mcuLink_receive(&master.link, &len);
    }
    check(got != NULL, "the master receives the answer");
    check((got != NULL) && (len == sizeof(response)) && (memcmp(got, response, sizeof(response)) == 0),
          "the answer arrives unchanged");
    mcuLink_release(&master.link);

    /* The master has the answer, but its acknowledgement for the last segment has not gone out yet - it
     * rides in the next transaction. So the slave must still say "not done": this is precisely the promise
     * bl_transport_tx_done() makes to the core, and a transport that said "done" here would let the core
     * reset while the last response was still unconfirmed. */
    check(!bl_transport_tx_done(), "tx_done stays false while the answer is unacknowledged");

    for (uint32_t i = 0U; (i < 50U) && !bl_transport_tx_done(); ++i)
    {
        cycle();
    }
    check(bl_transport_tx_idle(), "afterwards the slave's transport is idle again");
    check(bl_transport_tx_done(),
          "and tx_done becomes true once the peer acknowledged - the point at which the core may reset");

    /* --- a second round, so that nothing depended on the very first frames */
    check(mcuLink_send(&master.link, request, 64U), "a second request is accepted");
    got = NULL;
    for (uint32_t i = 0U; (i < 4000U) && (got == NULL); ++i)
    {
        cycle();
        got = bl_transport_receive(&len, &functional);
    }
    check((got != NULL) && (len == 64U) && (memcmp(got, request, 64U) == 0), "the second request arrives too");
    bl_transport_release();

    (void)printf("bootloader binding: %u transactions\n", (unsigned)transfers);
    (void)printf("%s\n", (failures == 0) ? "OK" : "FAILURES");
    return (failures == 0) ? 0 : 1;
}
