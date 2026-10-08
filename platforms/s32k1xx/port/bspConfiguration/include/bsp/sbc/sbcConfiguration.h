#pragma once

/* PLAT-24 (finding ARCH-09 B8): this header is the only part of bspConfiguration that needs the SBC driver
 * (libs/bsw/sbcUja113x) and the SPI driver of the family (platforms/s32k1xx/bsp/bspLpspi). Both are linked by
 * bspConfiguration/CMakeLists.txt only when the project uses the module sbcUja113x, so including this header
 * without it would fail with a missing include instead of a readable message. */
#include "integration/moduleConfiguration.h"

/* ARCH-41: the checks are chained, so a project the port cannot serve gets exactly one of the messages below and no
 * follow-up errors (tools/bswgen/test_bswgen.py, BswGenSbcConfigurationProbeTest). */
#if NX_MODULE_SBCUJA113X_COUNT == 0
#error "no SBC in this project (use.spi / board devices): bsp/sbc/sbcConfiguration.h has no configuration to offer"
#elif NX_MODULE_SBCUJA113X_COUNT > 1
#error "more than one UJA113x in this project: bsp/sbc/sbcConfiguration.h and SbcSystem support one SBC"
#else

#include "bsp/clock/boardClock.h"
#include "integration/sbcUja113xConfiguration.h"
#include "mcu/mcu.h"
#include "sbc/Uja113x.h"
#include "spi/Lpspi.h"

/* ARCH-40: the SPI bus of the SBC comes from the generat (board.yaml spi[] with device: <sbc>, project use.spi;
 * integration/sbcUja113xConfiguration.h), not from this file: device name, peripheral instance, chip select and the
 * functional clock of that instance (bsp/clock/boardClock.h). BSP_SBC_SPI(INSTANCE) is e.g. LPSPI1,
 * BSP_SBC_SPI(PCS) 0U, BSP_SBC_SPI(BUS) the board name of the bus (SbcEarlyStart muxes its pins). */
#define BSP_SBC_CONCAT_(a, b)   a##b
#define BSP_SBC_CONCAT(a, b)    BSP_SBC_CONCAT_(a, b)
#define BSP_SBC_DEVICE_NAME_(d) d
#define BSP_SBC_DEVICE          NX_MODULE_SBCUJA113X_INSTANCES(BSP_SBC_DEVICE_NAME_)
#define BSP_SBC_SPI(field)     BSP_SBC_CONCAT(BSP_SBC_CONCAT(NX_DEVICE_, BSP_SBC_DEVICE), BSP_SBC_CONCAT(_SPI_, field))

#if BSP_SBC_SPI(COUNT) != 1
#error "the UJA113x needs exactly one SPI bus (board.yaml spi[] with device: <sbc>, used in project.yaml use.spi)"
#else

namespace bsp
{
namespace sbc
{

/* SPI master of the SBC bus: instance, chip select and functional clock from the generat (eval board: UJA1131HW/FD/5V/4
 * on LPSPI1, PCS0, FIRCDIV2 = 48 MHz). Rate, frame, clock polarity/phase and timeout are values of the device and
 * its driver (ARCH-41: sbc::Uja113xSpi in libs/bsw/sbcUja113x), not of the board. */
inline ::bsp::spi::LpspiConfig const SPI_CONFIG = {
    BSP_SBC_CONCAT(IP_, BSP_SBC_SPI(INSTANCE)),
    BSP_SBC_CONCAT(BSP_SBC_CONCAT(NX_CLOCK_, BSP_SBC_SPI(INSTANCE)), _HZ),
    ::sbc::Uja113xSpi::BAUDRATE_HZ,
    static_cast<uint8_t>(BSP_SBC_SPI(PCS)),
    ::sbc::Uja113xSpi::FRAME_BITS,
    ::sbc::Uja113xSpi::CPOL,
    ::sbc::Uja113xSpi::CPHA,
    ::sbc::Uja113xSpi::TIMEOUT_US,
};

/* IO-33: project values of the SBC instance (project.yaml modules.<sbc>, parameters of libs/bsw/sbcUja113x/module.yaml
 * with their defaults) and the wiring of its own pins (board.yaml devices[].pins), both from the generat
 * integration/sbcUja113xConfiguration.h. BSP_SBC_PARAM(UNDERVOLTAGE_MV) is e.g. NX_DEVICE_SBC_PARAM_UNDERVOLTAGE_MV,
 * BSP_SBC_PIN(HVIO2_IS_WAKE_INPUT) NX_DEVICE_SBC_PIN_HVIO2_IS_WAKE_INPUT (1/0). */
#define BSP_SBC_PARAM(name) BSP_SBC_CONCAT(BSP_SBC_CONCAT(NX_DEVICE_, BSP_SBC_DEVICE), BSP_SBC_CONCAT(_PARAM_, name))
#define BSP_SBC_PIN(name)   BSP_SBC_CONCAT(BSP_SBC_CONCAT(NX_DEVICE_, BSP_SBC_DEVICE), BSP_SBC_CONCAT(_PIN_, name))

/** Switch-off response out of the choice none | undervoltage | overvoltage | both of a project parameter. */
constexpr ::sbc::SupplyShutdown supplyShutdownOf(bool const undervoltage, bool const overvoltage, bool const both)
{
    return both           ? ::sbc::SupplyShutdown::UNDER_OR_OVERVOLTAGE
           : undervoltage ? ::sbc::SupplyShutdown::UNDERVOLTAGE
           : overvoltage  ? ::sbc::SupplyShutdown::OVERVOLTAGE
                          : ::sbc::SupplyShutdown::NONE;
}

/* Battery monitoring (IO-10, values from the project since IO-33). Thresholds in mV at the monitored pin, the driver
 * converts them to the register grid of the 20 V monitor (78 mV per step, 314 mV per hysteresis step); nxconfig
 * checks them against the data sheet and shows the values the SBC really uses. BAT is the pin behind the reverse
 * polarity diode, BATSENSE the one in front of it. CSC would default to "CAN off on undervoltage" at power-up,
 * which is why the switch-off responses are always written. shutdownDelayMs > 0: the driver switches CAN/V2 off
 * only after the event has lasted that long (the SBC itself has no filter, IO-13: inrush current of a load). */
inline ::sbc::BatteryMonitorConfig const BATTERY_MONITOR = {
    true,
    (BSP_SBC_PARAM(BATTERY_SOURCE_IS_BATSENSE) != 0) ? ::sbc::BatteryMonitorSource::BATSENSE
                                                     : ::sbc::BatteryMonitorSource::BAT,
    static_cast<uint16_t>(BSP_SBC_PARAM(UNDERVOLTAGE_MV)),
    static_cast<uint16_t>(BSP_SBC_PARAM(OVERVOLTAGE_MV)),
    static_cast<uint16_t>(BSP_SBC_PARAM(UNDERVOLTAGE_HYSTERESIS_MV)),
    static_cast<uint16_t>(BSP_SBC_PARAM(OVERVOLTAGE_HYSTERESIS_MV)),
    supplyShutdownOf(
        BSP_SBC_PARAM(CAN_SHUTDOWN_IS_UNDERVOLTAGE) != 0,
        BSP_SBC_PARAM(CAN_SHUTDOWN_IS_OVERVOLTAGE) != 0,
        BSP_SBC_PARAM(CAN_SHUTDOWN_IS_BOTH) != 0),
    supplyShutdownOf(
        BSP_SBC_PARAM(V2_SHUTDOWN_IS_UNDERVOLTAGE) != 0,
        BSP_SBC_PARAM(V2_SHUTDOWN_IS_OVERVOLTAGE) != 0,
        BSP_SBC_PARAM(V2_SHUTDOWN_IS_BOTH) != 0),
    static_cast<uint16_t>(BSP_SBC_PARAM(SHUTDOWN_DELAY_MS)),
};

/* HVIO (IO-33): a pin the board wires as wake_input becomes a wake-up input that is sampled permanently (IOnCC = 011,
 * IOnAC = 001). No edge is named, so nothing is armed in Normal mode and enterSleep() arms both edges of the inputs
 * the project selects (modules.ecuState.wakeup_local); Uja113xPowerSupply offers exactly these inputs as LOCAL<n>.
 * A pin wired as none or not described is not configured; a bank without any wake input stays untouched (as before
 * IO-33). Bank threshold absolute (2.4 .. 4.1 V against ground): valid for a switch to the battery and for a 5 V
 * signal alike. Short-circuit/open-load thresholds do not apply to an input. */
constexpr ::sbc::HvioConfig hvioPinOf(bool const wakeInput)
{
    return wakeInput ? ::sbc::HvioConfig{::sbc::HvioFunction::WAKE_INPUT, false, ::sbc::HvioActivation::ON,
                                         ::sbc::SupplyShutdown::NONE, false, false, false, false, 0U, 0U}
                     : ::sbc::HvioConfig{::sbc::HvioFunction::OFF, false, ::sbc::HvioActivation::OFF,
                                         ::sbc::SupplyShutdown::NONE, false, false, false, false, 0U, 0U};
}

#define BSP_SBC_WAKE(pin) (BSP_SBC_PIN(BSP_SBC_CONCAT(pin, _IS_WAKE_INPUT)) != 0)

// A bank is configured if any of its four pins is a wake input. A function, not a || chain of the macro: on a board
// where several pins have the same wiring the chain has equal operands after expansion (misc-redundant-expression).
constexpr bool anyWakeInput(bool const pin0, bool const pin1, bool const pin2, bool const pin3)
{
    return pin0 || pin1 || pin2 || pin3;
}

inline ::sbc::HvioBankConfig const HVIO_BANK0 = {
    anyWakeInput(BSP_SBC_WAKE(HVIO1), BSP_SBC_WAKE(HVIO2), BSP_SBC_WAKE(HVIO3), BSP_SBC_WAKE(HVIO4)),
    ::sbc::HvioWakeThreshold::ABSOLUTE,
    {hvioPinOf(BSP_SBC_WAKE(HVIO1)), hvioPinOf(BSP_SBC_WAKE(HVIO2)), hvioPinOf(BSP_SBC_WAKE(HVIO3)),
     hvioPinOf(BSP_SBC_WAKE(HVIO4))},
};

inline ::sbc::HvioBankConfig const HVIO_BANK1 = {
    anyWakeInput(BSP_SBC_WAKE(HVIO5), BSP_SBC_WAKE(HVIO6), BSP_SBC_WAKE(HVIO7), BSP_SBC_WAKE(HVIO8)),
    ::sbc::HvioWakeThreshold::ABSOLUTE,
    {hvioPinOf(BSP_SBC_WAKE(HVIO5)), hvioPinOf(BSP_SBC_WAKE(HVIO6)), hvioPinOf(BSP_SBC_WAKE(HVIO7)),
     hvioPinOf(BSP_SBC_WAKE(HVIO8))},
};

/* Values read from the eval board on 2026-09-13 (MTPNV as left by the former MBDT project):
 * ID 0x51; 0x73 = 0x00 (V2 off at power-up, IO2SFC/IO3SFC/IO4SFC = 0, so no HVIO limp home function);
 * 0x74 = 0x06 (software development mode, V2 on-board supply, Sleep supported). The driver only compares
 * the MTPNV, programming it is a manual decision.
 * V2 feeds +5VD and with it VREFH (LM4132): it stays on in Standby and Reset mode as well. */

/* EN pin. Only the use `none` exists so far (module.yaml pins.EN, IO-33): EN keeps the reset behaviour "driven
 * low" (ENC = 00), whether the board describes the pin as not wired or not at all. A board that switches a load
 * with EN needs a project value for the EN mode first. */
inline ::sbc::EnPinConfig const EN_PIN = {
    true,
    ::sbc::EnMode::LOW,
    ::sbc::EnDriver::PUSH_PULL,
    ::sbc::SupplyShutdown::NONE,
};

inline ::sbc::Uja113xConfig const SBC_CONFIG = {
    0x51U,
    0x00U,
    0x06U,
    ::sbc::WatchdogMode::AUTONOMOUS, // off in software development mode
    ::sbc::WatchdogPeriod::MS_128,
    ::sbc::V2Mode::ON_NORMAL_STANDBY_RESET,
    ::sbc::CanMode::ACTIVE,
    UJA113X_OTWIE_MASK | UJA113X_SPIFIE_MASK,
    UJA113X_SMPSSI_MASK | UJA113X_VEXTOI_MASK | UJA113X_VEXTUI_MASK | UJA113X_V1UI_MASK
        | UJA113X_BMOI_MASK | UJA113X_BMUI_MASK, // battery monitor events are reported, IO-10
    UJA113X_CFI_MASK,
    0U,     // RCC: three resets without a successful startup are tolerated, the fourth enters FSP/Sleep
    false,  // LIMP released after a successful startup
    EN_PIN,
    BATTERY_MONITOR,
    /* HVIO from the board (IO-33, above). A bank the device does not have (UJA113xFD/4: bank 1) is never written,
     * the driver takes the number of banks from the identification. */
    {HVIO_BANK0, HVIO_BANK1},
};

#undef BSP_SBC_WAKE

} // namespace sbc
} // namespace bsp

#endif // exactly one SPI bus
#endif // exactly one SBC
