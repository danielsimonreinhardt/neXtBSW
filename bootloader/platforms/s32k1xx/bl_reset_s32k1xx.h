#pragma once

/* BL-33 (SYS-149, safety review SR-149-2 a): which reset of the family s32k1xx lets the bootloader skip its startup
 * window - the rule of hal_reset_skips_startup_window(), without register access, so the host test runs it on the
 * field masks of the vendor header (bootloader/test/host/test_bl_startup_window_host.py).
 *
 * The application tests the MCU watchdog after every reset that is not one of the test itself (OpenBSW
 * Watchdog::executeFastTest, safety mechanism SM-07): TST = 2 with TOVAL 0xFF -> watchdog reset, then TST = 3 ->
 * watchdog reset, then user mode TST = 1. WDOG_CS[TST] is cleared by power-on only, so the bootloader sees the step
 * of the test, and RCM_SRS shows the reset of exactly this start. Both test resets are WDOG alone; without the
 * window each costs ~28 ms instead of ~228 ms (board 2026-09-30: 3 x 228 ms before the first frame).
 *
 * Skipped only if both hold:
 *   - RCM_SRS is exactly WDOG: a watchdog timeout together with any other source (SW of the application's own
 *     watchdog interrupt, PIN, LOCKUP, POR, LVD, ...) keeps the window;
 *   - WDOG_CS[TST] is 2 or 3: a watchdog timeout in user mode (TST = 1) or after power-on (TST = 0) keeps it.
 * A power-on sets RCM_SRS[POR] and clears TST, so the window of the first start always stays - the rescue path of a
 * tester (canflash, power cycle) if an application ever resets in its test sequence for good.
 *
 * Needs the device header (RCM_SRS_*, WDOG_CS_TST_*) before it. */

#include <stdbool.h>
#include <stdint.h>

#define BL_WDOG_TST_FAST_LOW  2UL /* WDOG_CS[TST]: test mode, low byte of the counter */
#define BL_WDOG_TST_FAST_HIGH 3UL /* WDOG_CS[TST]: test mode, high byte of the counter */

static inline bool bl_s32k1xx_fast_test_reset(uint32_t rcmSrs, uint32_t wdogCs)
{
    uint32_t const tst = (wdogCs & WDOG_CS_TST_MASK) >> WDOG_CS_TST_SHIFT;
    return (rcmSrs == RCM_SRS_WDOG_MASK) && ((tst == BL_WDOG_TST_FAST_LOW) || (tst == BL_WDOG_TST_FAST_HIGH));
}
