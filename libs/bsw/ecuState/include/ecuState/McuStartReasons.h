#pragma once

#include "ecuState/EcuState.h"

#include <cstdint>

/**
 * SYS-18: second source of the start reasons next to the role power_supply - the reset status of the MCU itself.
 *
 * Why: the role power_supply only knows what its own device saw. A system basis chip with the debug jumper open
 * does not see a reset of the MCU alone, so a watchdog reset of the MCU arrives as "reset" (board test
 * 2026-09-19: "ecu state Run, wake-up sources 0x00000002" after "safety wdg", while bsw.reset.reason already
 * said WATCHDOG). A board without any supply device (PermanentPowerSupply) cannot tell at all. The MCU knows -
 * every family has a reset status register - so the ECU state management takes it as a second source.
 *
 * Family neutral: the ECU state management only knows this hook. The family port registers one function that
 * reports the reasons of the current start as ecustate::wakeup bits (s32k1xx: RCM_SRS,
 * platforms/s32k1xx/port/mcuResetReason; S32K3 MC_RGM, STM32 RCC_CSR, TMS570 SYSESR would do the same). A family
 * without one registers nothing and the ECU state management uses the role power_supply alone, as before.
 *
 * Same two sources and the same union as the BSW service bsw.reset (RTE-16, bswService/BswServiceRte.h): the RTE
 * service sits above the RTE and cannot be used from here, so combineStartReasons() holds the rule and
 * bswservice::reset::combine() is checked against it (bsw-providers host tests).
 */
namespace ecustate
{
/**
 * Reasons of the current start as the MCU sees them, as ecustate::wakeup bits: POWER (power-on reset), SUPPLY
 * (low-voltage reset without power-on), WATCHDOG, RESET (pin, software, debugger, lockup, clock loss);
 * wakeup::NONE if the MCU cannot tell. Must be valid before the run level is reached and constant afterwards,
 * because the start reasons are taken once, and callable from any task.
 */
using McuStartReasonsFn = uint32_t (*)();

/**
 * The family port registers its function here, before the run level is reached (s32k1xx: in the same static
 * constructor that captures RCM_SRS, so before main()). nullptr removes it again (host tests).
 */
void setMcuStartReasonSource(McuStartReasonsFn source);

/** What the registered family port reports, wakeup::NONE without one. */
uint32_t mcuStartReasons();

/**
 * Start reasons of this start from both sources: \p supplyReasons of the role power_supply and \p mcuReasons of
 * the MCU. The union keeps the ranking that reads them (WATCHDOG > SUPPLY > WAKEUP > POWER_ON > RESET,
 * bswservice::reset::reasonOf): a watchdog reset seen by only one of them stays WATCHDOG, a wake-up of the
 * supply together with a power-on reset of the MCU stays a wake-up. wakeup::UNKNOWN of the supply is dropped
 * when the MCU knows its reason - a board without supply device then reports the reason instead of "unknown".
 */
constexpr uint32_t combineStartReasons(uint32_t const supplyReasons, uint32_t const mcuReasons)
{
    return (mcuReasons == wakeup::NONE) ? supplyReasons : ((supplyReasons & ~wakeup::UNKNOWN) | mcuReasons);
}

} // namespace ecustate
