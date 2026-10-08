#pragma once

#include <cstdint>

/**
 * Second source of bsw.reset (RTE-16): the reset reason of the MCU itself, next to the start reasons of the role
 * power_supply. Family neutral: each MCU family provides this one function from its own reset status (s32k1xx:
 * RCM_SRS, platforms/s32k1xx/port/mcuResetReason; S32K3 MC_RGM, STM32 RCC_CSR or TMS570 SYSESR would do the same)
 * and registers its library in CMake (global property NX_BSW_MCU_RESET_REASON, see ecu/rte/CMakeLists.txt). A
 * family without one registers nothing: the build defines NX_BSW_MCU_RESET_REASON to 0 and the service uses the
 * role power_supply alone, as before RTE-16.
 */
namespace bswservice
{
namespace reset
{
/**
 * Reasons of the current start as the MCU sees them, as ecustate::wakeup bits: POWER (power-on reset), SUPPLY
 * (low-voltage reset without power-on), WATCHDOG, RESET (pin, software, debugger, lockup, clock loss); 0 if the MCU
 * cannot tell. Valid from before main() - captured before resets the platform causes itself during its start
 * (e.g. the watchdog fast test) - and constant afterwards, so any task may call it at any time.
 */
uint32_t mcuStartReasons();
} // namespace reset
} // namespace bswservice
