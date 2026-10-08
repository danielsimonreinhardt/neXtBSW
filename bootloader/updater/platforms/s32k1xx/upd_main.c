/* Bootloader updater, family s32k1xx. Flashed like an application by the bootloader, or
 * started directly from the stub in sector 0 after an interrupted update.
 *
 * MCU watchdog: disabled in bl_system_init() (startup) like in the bootloader. A watchdog reset while
 * sector 0 is erased would be exactly the failure the write order avoids, and a single sector erase runs
 * with interrupts disabled. External watchdogs (e.g. SBC) must be off: bl_board_environment_ok() of the
 * generated bl_board.h, checked below. */

#include "bootloader/bl_board.h"
#include "bl_config.h"
#include "bl_hal.h"
#include "bl_update.h"
#include "mcu/mcu.h"
#include "upd_config.h"
#include "upd_hal.h"
#include "upd_micros.h" /* time measurement on the counter of the core architecture (PLAT-28/86) */

#include <stdbool.h>
#include <stdint.h>

#define CAN_TX_WAIT_MS        20UL
#define FINAL_REPORTS         5U
#define FINAL_REPORT_PERIOD   100UL
#define ERROR_REPORT_PERIOD   500UL

extern uint8_t const upd_blob[];
extern uint32_t const upd_blob_size;
extern uint32_t const __isr_vector;

static upd_stats_t lastStats;

static uint16_t tenth_ms(uint32_t us)
{
    uint32_t const v = us / 100UL;
    return (v > 0xFFFFUL) ? 0xFFFFU : (uint16_t)v;
}

static void wait_ms(uint32_t ms)
{
    uint32_t const start = hal_millis();
    while ((hal_millis() - start) < ms)
    {
    }
}

void upd_report(uint8_t step, uint8_t detail, upd_stats_t const* stats)
{
    lastStats = *stats;
    uint16_t const stub    = tenth_ms(stats->stubUs);
    uint16_t const final   = tenth_ms(stats->finalUs);
    uint16_t const secured = tenth_ms(stats->securedUs);
    uint8_t const frame[8] = {step,
                              (step == (uint8_t)UPD_STEP_RECORD) ? stats->retries : detail,
                              (uint8_t)(stub >> 8),
                              (uint8_t)stub,
                              (uint8_t)(final >> 8),
                              (uint8_t)final,
                              (uint8_t)(secured >> 8),
                              (uint8_t)secured};
    uint32_t const start = hal_millis();
    while (!hal_can_tx_idle() && ((hal_millis() - start) < CAN_TX_WAIT_MS))
    {
    }
    (void)hal_can_send(UPD_STATUS_CAN_ID, frame, sizeof(frame));
}

int main(void)
{
    SCB->VTOR = (uint32_t)&__isr_vector; /* sector 0 is erased while writing */

    upd_micros_start();

    hal_init();

#if defined(UPD_FORCE_REWRITE)
    bool const force = true;
#else
    bool const force = false;
#endif
    upd_result_t const result = upd_run(upd_blob, upd_blob_size, bl_board_environment_ok(), force, &lastStats);
    /* Rejected before writing and started by the bootloader: the record is invalid now. */
    bool const nothingWritten = (result >= UPD_ERR_HEADER) && (result < UPD_ERR_FLASH_STUB)
                                && (lastStats.start != (uint8_t)UPD_START_RESUME);

    if ((result == UPD_OK) || (result == UPD_OK_ALREADY_INSTALLED) || nothingWritten)
    {
        /* The record is invalid now: the (new or old) bootloader stays active after the reset. */
        for (uint32_t i = 0U; i < FINAL_REPORTS; ++i)
        {
            wait_ms(FINAL_REPORT_PERIOD);
            upd_report(nothingWritten ? (uint8_t)UPD_STEP_ERROR : (uint8_t)UPD_STEP_DONE, (uint8_t)result, &lastStats);
        }
        hal_reset();
    }

    /* Flash error while writing: no automatic reset (a reset retries from the stub). */
    for (;;)
    {
        wait_ms(ERROR_REPORT_PERIOD);
        upd_report((uint8_t)UPD_STEP_ERROR, (uint8_t)result, &lastStats);
    }
}
