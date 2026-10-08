#include "bl_main.h"

#include "app_image.h"
#include "bl_config.h"
#include "bl_hal.h"
#include "bl_transport.h"
#include "uds.h"

#include <stdbool.h>
#include <stddef.h>

static uds_t uds;
static bool stayInBootloader;
static bool appValid;
static app_image_check_t checkedImage; /* what the image check of bl_init() computed (BL-38) */
static uint32_t windowStartMs;
static uint32_t windowMs; /* BL_STARTUP_WINDOW_MS, or 0 after a reset the family lets pass (BL-33) */

void bl_init(void)
{
    hal_init();
    uint32_t const now = hal_millis();
    bl_transport_init();
    uds_init(&uds, now);

    bool const bootRequest = hal_boot_request_consume();
    appValid               = app_image_valid(&checkedImage);
    stayInBootloader       = bootRequest || !appValid;
    windowStartMs          = now;
    /* BL-33: no window after a reset of a sequence the application runs on its own (family hook, e.g. the
     * watchdog fast test). Asked only without a boot request; an invalid application stays anyway. */
    windowMs = (!bootRequest && hal_reset_skips_startup_window()) ? 0U : BL_STARTUP_WINDOW_MS;
    if (bootRequest)
    {
        uds_enter_programming_session(&uds, now);
    }
}

void bl_poll(void)
{
#if defined(BL_DEVICE_SERVICE)
    bl_device_service(hal_millis(), false);
#endif

    /* BL-14: whatever the transport is, it takes its messages off the hardware here. The core sees no frames. */
    bl_transport_poll(hal_millis());

    uint32_t const now = hal_millis();
    uint16_t reqLen;
    bool functional;
    uint8_t const* req = bl_transport_receive(&reqLen, &functional);
    if ((req != NULL) && !uds_busy(&uds) && bl_transport_tx_idle())
    {
        uds_handle(&uds, req, reqLen, functional, now);
        bl_transport_release();
    }

    uds_poll(&uds, hal_millis());

    if (uds.session == UDS_SESSION_PROGRAMMING)
    {
        stayInBootloader = true;
    }

    /* BL-21: an extendedDiagnosticSession holds the jump back without latching like the programming session
     * does. A tester that opened the preprogramming step (ISO 14229-1 16.3.1.1) keeps talking to the same
     * server until it is done; when it stops, S3 returns the session to default and the jump happens. */
    if (!stayInBootloader && (uds.session == UDS_SESSION_DEFAULT) && appValid
        && ((hal_millis() - windowStartMs) >= windowMs) && !uds_busy(&uds)
        && bl_transport_tx_done())
    {
        /* BL-36 (SYS69-S11, BLI-F06): appValid is one RAM word computed in bl_init(), possibly minutes ago (a
         * tester held the extended session). The start decision is taken on the image as it is now: record,
         * compatibility and entry are checked again right before the jump, and the record's CRC field must still
         * be the CRC computed over the image in bl_init(). BL-38: no second CRC over the image (it cost a whole
         * CRC per start after the window, ~160 ms on the eval board): between bl_init() and here the bootloader
         * issues no flash command - erase and download need the programming session, which latches
         * stayInBootloader above. If the check fails the bootloader stays and remains reachable over UDS; the
         * devices are not handed over. */
        if (!app_image_confirm(&checkedImage))
        {
            appValid         = false;
            stayInBootloader = true;
        }
        else
        {
#if defined(BL_DEVICE_SERVICE)
            bl_device_service(hal_millis(), true);
#endif
            hal_jump_to_app(BL_APP_START);
        }
    }
}
