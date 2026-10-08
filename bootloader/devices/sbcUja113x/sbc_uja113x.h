#pragma once

#include "sbc/uja113xRegisters.h"

#include <stdbool.h>
#include <stdint.h>

/* Register snapshot for debugging via J-Link (symbol bl_sbc_diag). */
typedef struct
{
    uint8_t stage; /* 1: read done (read only; with SDMS 0 the watchdog is set up as well, BL-42), 2: configured,
                    * 0xE0 | step: SPI error,
                    * 0xD0: not in SW development mode (updater build only, BL-41),
                    * 0xE4: no plausible answer within the start-up time of the SBC (BL-26),
                    * 0xE5: watchdog could not be set up for serving (BL-28, SBC not switched to Normal mode;
                    *       read only with SDMS 0: BL-42) */
    uint8_t id;
    uint8_t mode;
    uint8_t mainStatus;
    uint8_t watchdogStatus;
    uint8_t regulator;
    uint8_t canControl;
    uint8_t transceiverStatus;
    uint8_t startupControl;
    uint8_t sbcConfig;
    uint8_t modeAfter;
    uint8_t regulatorAfter;
    uint8_t canControlAfter;
    uint8_t transceiverStatusAfter;
    uint8_t readyMs; /* BL-26: ms from the first to the last read before the decision (start-up time seen by the MCU) */
} sbc_diag_t;

extern sbc_diag_t bl_sbc_diag;

/* Builds of the hook:
 * - BL_SBC_WATCHDOG_SERVICE: the bootloader (bootloader.cmake, always since BL-41). The hook can serve the watchdog
 *   and the core calls bl_device_service().
 * - BL_SBC_WATCHDOG_SERVE: in addition, the project selects bootloader.sbc_watchdog: serve (BL-28).
 * - neither: the updater (only reads the SBC and needs the watchdog off, sbc_watchdog_off()). */
#if defined(BL_SBC_WATCHDOG_SERVE) && !defined(BL_SBC_WATCHDOG_SERVICE)
#define BL_SBC_WATCHDOG_SERVICE 1
#endif

/* Why the hook took the watchdog over (sbc_watchdog_diag_t.reason) */
#define SBC_WD_REASON_NONE    0U /* left alone: leave with software development mode (SDMS 1), or nothing set up */
#define SBC_WD_REASON_PROJECT 1U /* bootloader.sbc_watchdog: serve (BL-28) */
#define SBC_WD_REASON_SDMS0   2U /* BL-41: leave, but no software development mode (SDMS 0) - the watchdog already
                                  * runs in Standby after an SBC reset (data sheet Table 7, 7.2.1, Table 8) */
#define SBC_WD_REASON_SDMS0_READ 3U /* BL-42: SBC only read (sbc_init(false), not in bootloader.init) and SDMS 0 -
                                     * only the watchdog is set up and served, the SBC stays in Standby */

/* BL-28: the watchdog served by the bootloader (symbol bl_sbc_watchdog; in the bootloader build only, not in the
 * updater). */
typedef struct
{
    uint32_t lastTriggerMs;
    uint32_t triggers;
    uint32_t errors;       /* trigger writes that failed on SPI */
    uint8_t controlBefore; /* register 0x00 as found */
    uint8_t controlAfter;  /* read back after the fixed setting; 0 if the watchdog was off anyway */
    uint8_t served;        /* 1: the watchdog runs in Normal mode (no software development mode) and is triggered */
    uint8_t reason;        /* BL-41: SBC_WD_REASON_*, why the hook set the watchdog up (also when served stays 0) */
} sbc_watchdog_diag_t;

extern sbc_watchdog_diag_t bl_sbc_watchdog;

/* configure: Normal mode with V2 and CAN active (SBC is the transceiver of the bootloader bus, bootloader.init);
 * otherwise the registers are only read. Bootloader build: the watchdog gets the fixed setting in Standby and
 * bl_device_service() (bl_main.h) triggers it - with serve (BL-28) always, with leave (default) whenever the SBC is
 * not in software development mode (SDMS 0, BL-41): then the watchdog runs from the SBC reset on (timeout, 128 ms,
 * data sheet Table 7 and 8) and nobody else would trigger it. leave takes effect only with SDMS 1: the watchdog is
 * not touched, the SBC enters Normal mode. Read only (configure false) with SDMS 0 (BL-42): only the watchdog is set
 * up and served, the SBC stays in (or, found in Normal mode, goes to) Standby; V2 and CAN are not written. With
 * SDMS 1 read only stays read only. Updater build: without software development mode the SBC is left as it is
 * (stage 0xD0), read only reads only. */
void sbc_init(bool configure);

/* Updater environment check: registers read and the SBC watchdog off (software development mode). */
static inline bool sbc_watchdog_off(void)
{
    sbc_diag_t const* const d = &bl_sbc_diag;
    bool const readOk         = (d->stage == 1U) || (d->stage == 2U);
    return readOk && ((d->watchdogStatus & UJA113X_SDMS_MASK) != 0U);
}
