/* Bootloader hook of the module sbcUja113x: V2 on, CAN active, Normal mode (or registers read only; the bootloader
 * build then sets up and serves only the watchdog without software development mode, BL-42).
 * SPI frame (datasheet UJA113x series): 16 bit, MSB first, [addr(7) | RO(1)] [data(8)],
 * data shifted on the rising and sampled on the falling edge (CPOL 0, CPHA 1).
 * Family neutral (BL-05): the SPI port is the bootloader SPI HAL of the family (bl_spi.h, on s32k1xx
 * bootloader/platforms/s32k1xx/bl_spi_s32k1xx.c), which gets instance, pins and dividers from the generated
 * bootloader/bl_board.h. No register of any family is named here. */

#include "sbc_uja113x.h"

#include "bl_hal.h"
#include "bl_main.h"
#include "bl_spi.h"

#define NORMAL_MODE_DELAY_MS 20UL

/* BL-26: how long the SBC may take after power-on until it answers on SPI. Counted from the first access, because
 * the MCU may run before the SBC releases RSTN (reset output not wired to the MCU reset, e.g. a jumper open), so
 * the window has to hold the reset length of the SBC (at most 25 ms, RLC = 00) and the time after the rising edge
 * of RSTN in which SPI is not ready yet (tto(SPI)); source: UJA113x data sheet/app hints. tto(SPI) is not in the
 * documents at hand, hence four times the reset length. A ready SBC answers at once; only an SBC that never
 * answers plausibly costs the whole window. */
#define READY_TIMEOUT_MS 100UL

/* Identification codes of the UJA113x series (sbc/uja113xRegisters.h, register 0x7E): 0x00, 0x01, 0x10, 0x11 and
 * the /FD codes with bit 6 (0x40, 0x41, 0x50, 0x51, 0x60, 0x61, 0x70, 0x71). No other bit is ever set, and bit 5
 * (no HVIO) only together with bit 6. */
#define ID_CODE_BITS (UJA113X_ID_FD_MASK | UJA113X_ID_NO_HVIO_MASK | 0x11U)
#define ID_FD_BITS   (UJA113X_ID_FD_MASK | UJA113X_ID_NO_HVIO_MASK)

/* Modes (MC) the SBC can be in while the MCU is supplied, one bit per mode: Standby and Normal */
#define MODES_WITH_MCU ((1UL << UJA113X_MC_STANDBY) | (1UL << UJA113X_MC_NORMAL))

sbc_diag_t bl_sbc_diag;

#if defined(BL_SBC_WATCHDOG_SERVICE)
/* BL-28 (bootloader.sbc_watchdog: serve, defined by bootloader.cmake from the generated bl_board.cmake) and BL-41
 * (leave without software development mode, the bootloader build always has the service): the
 * bootloader serves the SBC watchdog itself, with a fixed setting that does not depend on the application:
 * autonomous (off in Standby, timeout watchdog in Normal mode without software development mode) and 1024 ms.
 * WMC/NWP change only in Standby - a change in Normal mode resets the system (data sheet 7.2, 7.3.3); the MTPNV is
 * never written. The trigger is a write of the same value, twice, because a single one right at 50 % of the
 * period may act like an ignored trigger (data sheet 7.2.5 case B); the bootloader triggers after a quarter of
 * the period and right before the jump, so the application has a whole period to take over (assumption of use,
 * BL-28). */
#define WATCHDOG_CONTROL    ((uint8_t)((UJA113X_WMC_AUTONOMOUS << UJA113X_WMC_SHIFT) | UJA113X_NWP_1024MS))
#define WATCHDOG_MASK       ((uint8_t)(UJA113X_WMC_MASK | UJA113X_NWP_MASK))
#define WATCHDOG_PERIOD_MS  1024UL
#define WATCHDOG_TRIGGER_MS (WATCHDOG_PERIOD_MS / 4UL)

sbc_watchdog_diag_t bl_sbc_watchdog;
#endif

static bool sbc_read(uint8_t reg, uint8_t* value)
{
    uint32_t rx = 0U;
    if (!bl_spi_transfer(((uint32_t)reg << UJA113X_SPI_ADDRESS_SHIFT) | UJA113X_SPI_RO_BIT, &rx))
    {
        return false;
    }
    *value = (uint8_t)rx;
    return true;
}

static bool sbc_write(uint8_t reg, uint8_t value)
{
    uint32_t rx = 0U;
    return bl_spi_transfer(((uint32_t)reg << UJA113X_SPI_ADDRESS_SHIFT) | value, &rx);
}

static void spi_init(void)
{
    /* Frame, clock polarity and phase are device values of the module (sbc/uja113xRegisters.h), the same ones the
     * application driver uses (sbc::Uja113xSpi); only the rate is a bootloader limit (bl_board.h). */
    bl_spi_frame_t const frame = {UJA113X_SPI_FRAME_BITS, UJA113X_SPI_CPOL != 0U, UJA113X_SPI_CPHA != 0U};
    bl_spi_init(&frame);
}

static bool read_all(bool after)
{
    sbc_diag_t* const d = &bl_sbc_diag;
    if (after)
    {
        return sbc_read(UJA113X_REG_MODE_CONTROL, &d->modeAfter) && sbc_read(UJA113X_REG_REGULATOR_CONTROL, &d->regulatorAfter)
               && sbc_read(UJA113X_REG_CAN_CONTROL, &d->canControlAfter)
               && sbc_read(UJA113X_REG_TRANSCEIVER_STATUS, &d->transceiverStatusAfter);
    }
    return sbc_read(UJA113X_REG_IDENTIFICATION_0, &d->id) && sbc_read(UJA113X_REG_MODE_CONTROL, &d->mode)
           && sbc_read(UJA113X_REG_MAIN_STATUS, &d->mainStatus)
           && sbc_read(UJA113X_REG_WATCHDOG_STATUS, &d->watchdogStatus)
           && sbc_read(UJA113X_REG_REGULATOR_CONTROL, &d->regulator)
           && sbc_read(UJA113X_REG_CAN_CONTROL, &d->canControl)
           && sbc_read(UJA113X_REG_TRANSCEIVER_STATUS, &d->transceiverStatus)
           && sbc_read(UJA113X_REG_STARTUP_CONTROL, &d->startupControl)
           && sbc_read(UJA113X_REG_SBC_CONFIG_CONTROL, &d->sbcConfig);
}

/* An SBC that is not ready yet answers 0x00 (board 2026-09-21: every register 0x00 after power-on), a missing one
 * lets the data line float to 0x00 or 0xFF. A completed transfer is therefore no proof of a read: plausible is an
 * identification code of the series together with a mode the SBC can be in while the MCU is supplied (Standby or
 * Normal). 0x00 alone is an identification code, mode 0 is none; 0xFF is neither. */
static bool plausible(sbc_diag_t const* d)
{
    uint32_t const id = d->id;
    uint32_t const mc = (uint32_t)d->mode & UJA113X_MC_MASK;
    if (((id & ~ID_CODE_BITS) != 0U) || ((id & ID_FD_BITS) == UJA113X_ID_NO_HVIO_MASK))
    {
        return false;
    }
    return ((MODES_WITH_MCU >> mc) & 1U) != 0U;
}

#if defined(BL_SBC_WATCHDOG_SERVICE)
/* Whether the watchdog runs once the SBC is in Normal mode (data sheet 7.2.4, Table 8): not in Forced Normal mode,
 * and in software development mode only if it is not autonomous (an application may have set timeout or window). */
static bool watchdog_may_run(uint8_t status, uint8_t control)
{
    if ((status & UJA113X_FNMS_MASK) != 0U)
    {
        return false;
    }
    if ((((uint32_t)control & UJA113X_WMC_MASK) >> UJA113X_WMC_SHIFT) != UJA113X_WMC_AUTONOMOUS)
    {
        return true;
    }
    return (status & UJA113X_SDMS_MASK) == 0U;
}

/* Before Normal mode (or, read only, instead of it, BL-42): a watchdog that may run is set to the fixed setting in
 * Standby. After an MCU-only reset the
 * SBC may still be in Normal mode with the watchdog of the application (period and, for window, phase unknown):
 * Standby first, where it acts as timeout watchdog and may be changed. Written twice (required when the period
 * gets shorter) and read back. */
static bool watchdog_setup(sbc_diag_t const* d)
{
    sbc_watchdog_diag_t* const w = &bl_sbc_watchdog;
    if (!sbc_read(UJA113X_REG_WATCHDOG_CONTROL, &w->controlBefore))
    {
        return false;
    }
    if (!watchdog_may_run(d->watchdogStatus, w->controlBefore))
    {
        return true; /* off in Normal mode as well: nothing to serve, no write to register 0x00 */
    }
    bool ok = true;
    if (((uint32_t)d->mode & UJA113X_MC_MASK) != UJA113X_MC_STANDBY)
    {
        ok = sbc_write(UJA113X_REG_MODE_CONTROL, UJA113X_MC_STANDBY);
    }
    ok = (bool)(ok && sbc_write(UJA113X_REG_WATCHDOG_CONTROL, WATCHDOG_CONTROL)
                && sbc_write(UJA113X_REG_WATCHDOG_CONTROL, WATCHDOG_CONTROL)
                && sbc_read(UJA113X_REG_WATCHDOG_CONTROL, &w->controlAfter));
    if (!ok || ((w->controlAfter & WATCHDOG_MASK) != WATCHDOG_CONTROL))
    {
        return false;
    }
    w->lastTriggerMs = hal_millis();
    /* in software development mode the fixed setting (autonomous) switches it off */
    w->served = (uint8_t)(((d->watchdogStatus & UJA113X_SDMS_MASK) == 0U) ? 1U : 0U);
    return true;
}

void bl_device_service(uint32_t nowMs, bool beforeJump)
{
    sbc_watchdog_diag_t* const w = &bl_sbc_watchdog;
    if ((w->served == 0U) || (!beforeJump && ((nowMs - w->lastTriggerMs) < WATCHDOG_TRIGGER_MS)))
    {
        return;
    }
    /* the trigger is the same value written twice (data sheet 7.2.5 case B, see WATCHDOG_CONTROL), the second write
     * only after the first succeeded */
    bool const first = sbc_write(UJA113X_REG_WATCHDOG_CONTROL, WATCHDOG_CONTROL);
    if (first && sbc_write(UJA113X_REG_WATCHDOG_CONTROL, WATCHDOG_CONTROL))
    {
        w->triggers++;
        w->lastTriggerMs = nowMs;
    }
    else
    {
        w->errors++; /* again in the next poll */
    }
}
#endif

void sbc_init(bool configure)
{
    sbc_diag_t* const d = &bl_sbc_diag;
    spi_init();

    /* BL-26: read again until the answer is plausible or the start-up time of the SBC is over */
    uint32_t const readStart = hal_millis();
    uint32_t elapsed         = 0U;
    bool ready               = false;
    while (!ready && (elapsed < READY_TIMEOUT_MS))
    {
        if (!read_all(false))
        {
            d->stage = 0xE1U;
            return;
        }
        ready   = plausible(d);
        elapsed = hal_millis() - readStart;
    }
    d->readyMs = (uint8_t)elapsed; /* below READY_TIMEOUT_MS plus one pass (well under 1 ms) */
    /* Never ready: nothing is written. The watchdog state (SDMS) is unknown, so Normal mode might start the SBC
     * watchdog, and an SBC that gives no plausible answer cannot be trusted to take a command either. The CAN
     * controller runs regardless (family HAL); whether the transceiver does is up to the SBC. */
    if (!ready)
    {
        d->stage = 0xE4U;
        return;
    }
    d->stage = 1U;
#if defined(BL_SBC_WATCHDOG_SERVICE)
    bool const developmentMode = (d->watchdogStatus & UJA113X_SDMS_MASK) != 0U;
    if (!configure)
    {
        /* BL-42 (safety review bootloader 0.3.10 2026-10-07, BL41-2 b): the SBC is only read (not in
         * bootloader.init), but without software development mode its watchdog runs from the SBC reset on, in
         * Standby too (data sheet Table 7, 7.2.1, Table 8) - left alone it would reset the system over and over. So
         * the watchdog alone gets the fixed setting (in Standby, data sheet 7.2, 7.3.3) and is triggered by
         * bl_device_service(). Nothing else is written: regulator and CAN control stay as found and the SBC is not
         * switched to Normal mode - it is not needed for serving (autonomous in Standby: off while INTN1 is high,
         * timeout while an interrupt is pending, Table 8). Only an SBC found in Normal mode (MCU-only reset,
         * watchdog of the application with unknown period and phase) is switched to Standby, which the setting
         * needs, and stays there until the application takes over. With SDMS 1 the SBC is only read. */
        uint8_t const readReason = (!developmentMode) ? SBC_WD_REASON_SDMS0_READ : SBC_WD_REASON_NONE;
        bl_sbc_watchdog.reason   = readReason;
        if ((readReason != SBC_WD_REASON_NONE) && !watchdog_setup(d))
        {
            d->stage = 0xE5U;
        }
        return; /* BL-42: read only - no V2, no CAN, no Normal mode */
    }
#else
    if (!configure)
    {
        return;
    }
#endif
#if defined(BL_SBC_WATCHDOG_SERVICE)
#if defined(BL_SBC_WATCHDOG_SERVE)
    (void)developmentMode;
    uint8_t const reason = SBC_WD_REASON_PROJECT; /* BL-28: the bootloader serves the watchdog Normal mode may start */
#else
    /* BL-41 (safety review SBC watchdog 2026-10-06, SDMS0-1 c): leave takes effect only in software development
     * mode. Without it the watchdog runs from the SBC reset on, in Standby too (data sheet Table 7, 7.2.1, Table 8:
     * WMC timeout, 128 ms) - left alone it would reset the SBC over and over without a valid application, and give
     * the application only what is left of 128 ms. So the bootloader serves it as with serve. */
    uint8_t const reason = ((d->watchdogStatus & UJA113X_SDMS_MASK) != 0U) ? SBC_WD_REASON_NONE : SBC_WD_REASON_SDMS0;
#endif
    bl_sbc_watchdog.reason = reason;
    if ((reason != SBC_WD_REASON_NONE) && !watchdog_setup(d))
    {
        d->stage = 0xE5U;
        return;
    }
#else
    /* Only with the SBC watchdog guaranteed off: entering Normal mode would otherwise start it. */
    if ((d->watchdogStatus & UJA113X_SDMS_MASK) == 0U)
    {
        d->stage = 0xD0U;
        return;
    }
#endif
    bool ok = sbc_write(UJA113X_REG_REGULATOR_CONTROL, (uint8_t)((d->regulator & ~UJA113X_V2C_MASK) | (UJA113X_V2C_ON_NORMAL << UJA113X_V2C_SHIFT)))
              && sbc_write(UJA113X_REG_CAN_CONTROL, (uint8_t)((d->canControl & ~UJA113X_CMC_MASK) | UJA113X_CMC_ACTIVE))
              && sbc_write(UJA113X_REG_MODE_CONTROL, UJA113X_MC_NORMAL);
    if (!ok)
    {
        d->stage = 0xE2U;
        return;
    }
    uint32_t const start = hal_millis();
    while ((hal_millis() - start) < NORMAL_MODE_DELAY_MS)
    {
    }
    d->stage = read_all(true) ? 2U : 0xE3U;
}
