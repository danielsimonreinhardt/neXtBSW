#pragma once

#include <cstdint>

/**
 * BSW service ports (RTE-05): hand-over between the BSW services and the RTE for the connection targets
 * bsw.power.<value>, bsw.reset.<value> and bsw.time.<value> of tools/rtegen.
 *
 * The targets are named by service, not by device: a software component connected to bsw.power.state sees the same
 * target on a board with a system basis chip, with a plain regulator and an ADC monitor, or with an MCU supply
 * monitor. Which module provides a service is decided by the integration (role power_supply,
 * doc/architecture/README.md section 5); the provider publishes here, the RTE reads here.
 *
 * Providers today:
 *   power  whoever holds the role power_supply and can monitor the supply calls power::publish() (RTE-16: the
 *          UJA113x system of the s32k1xx port from its battery monitor). A provider registers in CMake (global
 *          property NX_BSW_POWER_PROVIDER, see ecu/rte/CMakeLists.txt); the build defines NX_BSW_POWER_PROVIDER to
 *          1 or 0. Without a provider the service reports UNKNOWN with health FAILED (QLF_ERROR) from the start:
 *          nobody will ever publish, so waiting (QLF_DEBOUNCE) would be wrong.
 *   reset  two sources (RTE-16): the start reasons of the role power_supply as the ECU state management publishes
 *          them (ecustate::rte::readWakeupSources, 0 until the run level is reached) and the reset reason of the
 *          MCU itself (reset::mcuStartReasons(), bswService/McuResetReason.h, one provider per MCU family, known
 *          before main()), combined by reset::combine()
 *   time   the system timer of the platform (OpenBSW bsp/timer/SystemTimer.h)
 *
 * Every value is one atomic word: any task may read, the provider may publish from its own task. The numeric
 * values of the enumerations are part of the RTE interface (software components compare them), keep them stable;
 * tools/rtegen/test_rtegen.py compares them with the generator.
 */
namespace bswservice
{
/** Health of a service reading, turned into the qualifier values of COM-03 by the RTE (bsw.<service>.qlf). */
enum class Health : uint8_t
{
    PENDING = 0U, // no reading yet (provider not started, no provider): QLF_DEBOUNCE
    OK      = 1U, // provider reports: QLF_OK
    FAILED  = 2U, // provider cannot tell (supply monitor lost, reset reason unknown): QLF_ERROR
};

namespace power
{
/** State of the supply (bsw.power.state). */
enum class State : uint8_t
{
    UNKNOWN      = 0U, // no reading
    OK           = 1U, // within the monitored limits
    UNDERVOLTAGE = 2U, // below the undervoltage threshold of the monitor
    OVERVOLTAGE  = 3U, // above the overvoltage threshold of the monitor
    FAULT        = 4U, // regulator fault (overload, overtemperature, lost output)
};

/**
 * Provider (role power_supply): current state and supply voltage in mV (bsw.power.supply_mv, 0 if the provider
 * does not measure it). Sets the health to OK. One provider per ECU.
 */
void publish(State state, uint16_t supplyMv);

/** Provider lost its supply monitor (e.g. communication error): state and voltage stay, health becomes FAILED. */
void publishFailure();
} // namespace power

namespace reset
{
/** Reason of the current start (bsw.reset.reason). */
enum class Reason : uint8_t
{
    UNKNOWN  = 0U, // not known yet, or the provider cannot tell
    POWER_ON = 1U, // supply switched on
    RESET    = 2U, // reset without wake-up event (reset pin, debugger, software reset)
    WATCHDOG = 3U, // watchdog reset
    SUPPLY   = 4U, // restart after a supply fault (undervoltage, overload)
    WAKEUP   = 5U, // wake-up from sleep (CAN bus, local input, cyclic timer)
};

/**
 * One reason from the start reasons (ecustate::wakeup bits), most specific first: WATCHDOG, SUPPLY, WAKEUP
 * (a CAN, local or timer bit), POWER_ON, RESET, else UNKNOWN. A wake-up from a sleep with the MCU supply off also
 * reports POWER at some supplies; the wake-up is what the application wants to know then.
 */
Reason reasonOf(uint32_t startReasons);

/** Health of \p startReasons: PENDING while 0 (not known yet), FAILED if only wakeup::UNKNOWN, else OK. */
Health healthOf(uint32_t startReasons);

/**
 * Start reasons of this start from both sources (RTE-16): \p supplyReasons of the role power_supply (0 before the
 * run level) and \p mcuReasons, the MCU's own reset reason (0 if unknown). The union keeps the ranking of
 * reasonOf() for both: a watchdog reset seen by only one of them stays WATCHDOG, a wake-up of the supply with a
 * power-on reset of the MCU stays WAKEUP. wakeup::UNKNOWN of the supply is dropped when the MCU knows its reason.
 */
uint32_t combine(uint32_t supplyReasons, uint32_t mcuReasons);
} // namespace reset

namespace rte
{
// ---- RTE side: called by the generated RteConfig.cpp, which declares these functions itself (the RTE library
// does not see this include directory). The parameter types are part of the linker names, so a changed type
// does not link instead of reading garbage. Health values: bswservice::Health.

/** bsw.power.*: state (power::State), supply voltage in mV, health. */
void readPower(uint8_t& state, uint16_t& supplyMv, uint8_t& health);
/** bsw.reset.*: reason (reset::Reason), the start reasons of both sources as ecustate::wakeup bits, health. */
void readReset(uint8_t& reason, uint32_t& startReasons, uint8_t& health);
/** bsw.time.*: time since the start in ms and us from one reading of the system timer (both wrap around). */
void readTime(uint32_t& ms, uint32_t& us);

/**
 * Back to the initial values (host tests): power UNKNOWN, 0 mV, health PENDING with a registered provider
 * (NX_BSW_POWER_PROVIDER 1 or not defined), FAILED without (NX_BSW_POWER_PROVIDER 0).
 */
void reset();
} // namespace rte
} // namespace bswservice
