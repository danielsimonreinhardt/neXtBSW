/* SYS-41 (finding F12 of doc/safety/fmea.md, SM-02): configuration re-check of the MCU watchdog, the part the
 * driver's own check leaves out.
 *
 * The OpenBSW driver (safeBspMcuWatchdog, Watchdog::checkWatchdogConfiguration, never changed in the submodule)
 * compares only CS[EN], CS[CLK] and TOVAL. A single bit flip in one of the other control fields was silent:
 * - CS[INT] cleared: the warning interrupt that walks the safe outputs table before the watchdog reset is gone
 *   (the de-energising of SM-01 then depends on the reset alone),
 * - CS[WIN] set: a window the service was never designed for - an early service resets the ECU,
 * - CS[PRES] set: the prescaler of 256 stretches the time-out from 250 ms to 64 s,
 * - CS[UPDATE] cleared: the watchdog can no longer be reconfigured - harmless for the running supervision, but no
 *   longer the configuration that was set up, and the next enableWatchdog() of the same run would hang.
 * Expected values are the ones Watchdog::enableWatchdog() writes (UPDATE 1, INT as requested, WIN 0, PRES 0).
 *
 * Whether UPDATE should be 0 after start (lock the configuration until the next reset) is a separate decision: it
 * needs a neXtBSW copy of the driver (fmea.md SM-02). Until then UPDATE = 1 is the expected value.
 *
 * Family interface: SafeWatchdog (ecu/safety/safeWatchdog) calls this function when PLATFORM_SUPPORT_WATCHDOG is
 * set; another family provides the same header with its own watchdog's fields.
 */

#pragma once

#include <cstdint>

namespace safety
{
namespace supervision
{
/** Deviating fields of the watchdog configuration, as a bit mask (0 = configuration as expected). */
enum WatchdogControlField : uint32_t
{
    WATCHDOG_FIELD_DRIVER_CHECK = 0x01U, ///< CS[EN], CS[CLK] or TOVAL (the driver's own check failed)
    WATCHDOG_FIELD_INT          = 0x02U, ///< CS[INT] differs from the requested warning interrupt
    WATCHDOG_FIELD_WIN          = 0x04U, ///< CS[WIN] set (window mode is not configured)
    WATCHDOG_FIELD_PRES         = 0x08U, ///< CS[PRES] set (prescaler 256 is not configured)
    WATCHDOG_FIELD_UPDATE       = 0x10U, ///< CS[UPDATE] cleared (configuration no longer updatable)
};

/**
 * Compares the watchdog control fields that the driver's check does not cover with the configuration of
 * Watchdog::enableWatchdog(timeout, interruptActive). Pure register read, no side effect.
 * \param interruptActive the value SafeWatchdog passed to enableWatchdog()
 * \return mask of WatchdogControlField without WATCHDOG_FIELD_DRIVER_CHECK (0 = as expected)
 */
uint32_t checkWatchdogControl(bool interruptActive);

} // namespace supervision
} // namespace safety
