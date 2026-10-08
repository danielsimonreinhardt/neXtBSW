/* Host proof of the transport interface (BL-14, doc/architecture/multi-mcu-flash.md requirement R1).
 *
 * This program is the bootloader core with a transport that is **not** CAN, next to a HAL that has no CAN
 * function at all. What the core needs of a transport is exactly bootloader/src/core/bl_transport.h: if uds.c
 * or bl_main.c still reached for hal_can_* or a CAN identifier, this program would not link. The generated
 * bl_diag.h of the test layout selects BL_TRANSPORT_LINK, so src/core/isotp.c compiles to nothing and the
 * transport below is the only one.
 *
 * It stands in for the SPI link of BL-15/BL-16 in one respect that matters here: it carries **shorter
 * messages** than the core's own block buffer, so the promise the core makes to the tester has to follow the
 * transport (ARCH-47, multi-mcu-flash.md section 17).
 *
 * Run by bootloader/test/test_bl_transport.py; prints the failing check and exits non-zero. */

#include "bl_transport.h"

#include "bl_hal.h"
#include "bl_main.h"
#include "uds.h"

#include <stdio.h>
#include <string.h>

#if BL_TRANSPORT != BL_TRANSPORT_LINK
#error "this build is meant for the link transport (bl_diag.h of the test layout, BL-14)"
#endif

/* Deliberately far below BL_UDS_BUFFER_SIZE, see the file comment. */
#define LINK_MAX_MESSAGE_LEN 64U

/* ------------------------------------------------------------------ the transport (bl_transport.h) */

static uint8_t rxBuf[LINK_MAX_MESSAGE_LEN];
static uint16_t rxLen;
static bool rxPending;
static uint8_t txBuf[LINK_MAX_MESSAGE_LEN];
static uint16_t txLen;
static bool txHeld; /* one message is on the wire until the test has taken it */

void bl_transport_init(void)
{
    rxPending = false;
    txLen     = 0U;
    txHeld    = false;
}

uint16_t bl_transport_max_message_len(void) { return LINK_MAX_MESSAGE_LEN; }

void bl_transport_poll(uint32_t nowMs) { (void)nowMs; }

uint8_t const* bl_transport_receive(uint16_t* len, bool* functional)
{
    if (!rxPending)
    {
        return NULL;
    }
    *len        = rxLen;
    *functional = false;
    return rxBuf;
}

void bl_transport_release(void) { rxPending = false; }

/* BL-29: messages arrive here in one piece, so a request is pending exactly while it waits for the core. */
bool bl_transport_rx_pending(void) { return rxPending; }

bool bl_transport_send(uint8_t const* msg, uint16_t len, uint32_t nowMs)
{
    (void)nowMs;
    if (txHeld || (len == 0U) || (len > sizeof(txBuf)))
    {
        return false;
    }
    (void)memcpy(txBuf, msg, len);
    txLen  = len;
    txHeld = true;
    return true;
}

bool bl_transport_tx_idle(void) { return !txHeld; }

bool bl_transport_tx_done(void) { return !txHeld; }

/* ------------------------------------------------------------------ the HAL, without a CAN function */

static uint8_t flash[BL_FLASH_SIZE];
static uint32_t nowMs;

void hal_init(void) { (void)memset(flash, 0xFF, sizeof(flash)); }
uint32_t hal_millis(void) { return nowMs; }
uint32_t hal_entropy(void) { return 0x5A5A5A5AUL; }
uint8_t const* hal_flash_ptr(uint32_t address) { return &flash[address - BL_FLASH_START]; }

bool hal_flash_erase_sector(uint32_t address)
{
    (void)address;
    return false; /* nothing in this test programs; a silent erase would hide a mistake */
}

bool hal_flash_program(uint32_t address, uint8_t const* data, uint32_t len)
{
    (void)address;
    (void)data;
    (void)len;
    return false;
}

bool hal_boot_request_consume(void) { return false; }
bool hal_reset_skips_startup_window(void) { return false; }
void hal_reset(void) {}
void hal_jump_to_app(uint32_t appStart)
{
    (void)appStart;
    (void)printf("FAIL: the core jumped into an application although the flash is erased\n");
}

/* ------------------------------------------------------------------ the checks */

static int failures;

static void check(bool ok, char const* what)
{
    if (!ok)
    {
        (void)printf("FAIL: %s\n", what);
        failures++;
    }
}

static void check_bytes(uint8_t const* expected, uint16_t len, char const* what)
{
    if (!txHeld || (txLen != len) || (memcmp(txBuf, expected, len) != 0))
    {
        (void)printf("FAIL: %s (got %u bytes:", what, (unsigned)(txHeld ? txLen : 0U));
        for (uint16_t i = 0U; txHeld && (i < txLen); ++i)
        {
            (void)printf(" %02X", txBuf[i]);
        }
        (void)printf(")\n");
        failures++;
    }
    txHeld = false;
}

static void request(uint8_t const* msg, uint16_t len)
{
    (void)memcpy(rxBuf, msg, len);
    rxLen     = len;
    rxPending = true;
    bl_poll();
}

int main(void)
{
    bl_init();

    /* The core answers UDS over a transport that knows no CAN identifier at all. */
    uint8_t const testerPresent[2] = {0x3EU, 0x00U};
    request(testerPresent, sizeof(testerPresent));
    uint8_t const testerPresentRsp[2] = {0x7EU, 0x00U};
    check_bytes(testerPresentRsp, sizeof(testerPresentRsp), "TesterPresent over the link transport");

    uint8_t const readSession[3] = {0x22U, 0xF1U, 0x86U};
    request(readSession, sizeof(readSession));
    uint8_t const readSessionRsp[4] = {0x62U, 0xF1U, 0x86U, UDS_SESSION_DEFAULT};
    check_bytes(readSessionRsp, sizeof(readSessionRsp), "DID 0xF186 over the link transport");

    /* ARCH-47: what RequestDownload promises must fit through the transport. The UDS state machine is driven
     * directly here, because the security access of this layout has no provisioned key. */
    uds_t uds;
    uds_init(&uds, 0U);
    uds.session          = UDS_SESSION_PROGRAMMING;
    uds.unlocked         = true;
    uds.fingerprintValid = true;
    uint8_t const requestDownload[11] = {0x34U,
                                         0x00U,
                                         0x44U,
                                         (uint8_t)(BL_APP_START >> 24),
                                         (uint8_t)(BL_APP_START >> 16),
                                         (uint8_t)(BL_APP_START >> 8),
                                         (uint8_t)BL_APP_START,
                                         0x00U,
                                         0x00U,
                                         0x00U,
                                         (uint8_t)BL_PHRASE_SIZE};
    uds_handle(&uds, requestDownload, sizeof(requestDownload), false, 0U);
    uint8_t const requestDownloadRsp[4] = {0x74U, 0x20U, (uint8_t)(LINK_MAX_MESSAGE_LEN >> 8),
                                           (uint8_t)LINK_MAX_MESSAGE_LEN};
    check_bytes(requestDownloadRsp, sizeof(requestDownloadRsp),
                "maxNumberOfBlockLength follows the transport, not BL_MAX_BLOCK_LENGTH");
    check(bl_transport_max_message_len() < BL_MAX_BLOCK_LENGTH,
          "the test transport must be shorter than the core's block buffer");

    (void)printf("%s\n", (failures == 0) ? "OK" : "FAILURES");
    return (failures == 0) ? 0 : 1;
}
