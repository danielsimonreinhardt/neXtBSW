#pragma once

/* IO-05: configuration of the TMC6200 gate drivers of a project (module gdTmc6200), family port.
 *
 * No board and no project value is written here (PLAT-23): the SPI bus of an instance, the PWM wiring and the
 * project parameters come from the generated integration headers. What this header adds are the device facts of
 * the TMC6200 datasheet (SPI mode, frame length, SCK limit) and the mapping of the project parameters (units of the
 * function) to the register fields of gd::Tmc6200Config.
 *
 * Only included by the integration when the project uses the module; SPI bus and PWM group come from
 * the NX_DEVICE_<device>_SPI and _PWM generator values (IO-25). */
#include "integration/moduleConfiguration.h"

#if NX_MODULE_GDTMC6200_COUNT == 0
#error "no TMC6200 in this project (board devices, use.spi): bsp/gd/gateDriverConfiguration.h has nothing to offer"
#endif

#include "gd/Tmc6200.h"
// Generated only for projects with the module (PLAT-25: generates behind #if NX_MODULE_<MODULE>_COUNT)
#if NX_MODULE_GDTMC6200_COUNT > 0
#include "integration/gdTmc6200Configuration.h"
#endif

namespace bsp
{
namespace gd
{

/* SPI of a TMC6200 (datasheet p.18-20): 40-bit datagrams, MSB first, SPI mode 3. SCK is limited to 3.5 MHz
 * with the internal clock (p.20); 1 MHz leaves margin for the oscillator tolerance and the board wiring and
 * keeps a 5-datagram cycle below 0.3 ms. */
constexpr uint32_t SPI_BAUDRATE_LIMIT_HZ = 1000000UL;
constexpr uint8_t SPI_FRAME_BITS         = TMC6200_FRAME_BITS;
constexpr bool SPI_CPOL                  = (TMC6200_SPI_CPOL != 0U);
constexpr bool SPI_CPHA                  = (TMC6200_SPI_CPHA != 0U);
constexpr uint32_t SPI_TIMEOUT_US        = 5000UL;

static_assert(SPI_BAUDRATE_LIMIT_HZ <= TMC6200_SPI_MAX_SCK_HZ_INTERNAL_CLOCK, "TMC6200 SCK above the datasheet limit");

/** Register field for a project value, or KEEP (device reset value) for the "not set" marker of the manifest. */
constexpr int16_t fieldOrKeep(int32_t const value)
{
    return (value < 0) ? ::gd::KEEP : static_cast<int16_t>(value);
}

/**
 * gd::Tmc6200Config of one instance.
 * \param interfaceMode GCONF singleline from the board wiring (0: PWM pairs with individual high/low side
 *        signals, 1: polarity/enable), KEEP while the board does not describe it
 * \param otCelsius, retries, allBridges, faultDirect project parameters (module.yaml), "not set" = 0 / -1
 * \param s2gMv, s2vsMv, filterNs, delayNs short detection (module.yaml short_*, IO-35), "not set" = 0; a value the
 *        device cannot realize gives an invalid field (static_assert of the integration)
 * Break-before-make and drive strength stay KEEP (device reset value).
 */
constexpr ::gd::Tmc6200Config makeTmc6200Config(
    int16_t const interfaceMode,
    int32_t const otCelsius,
    int32_t const retries,
    int32_t const allBridges,
    int32_t const faultDirect,
    int32_t const s2gMv,
    int32_t const s2vsMv,
    int32_t const filterNs,
    int32_t const delayNs)
{
    ::gd::Tmc6200Config c = ::gd::keepAllConfig();
    c.interfaceMode       = interfaceMode;
    c.overtemperatureSelect = (otCelsius == 0) ? ::gd::KEEP
                                               : ((::gd::overtemperatureSelectForCelsius(otCelsius) == ::gd::KEEP)
                                                      ? static_cast<int16_t>(-2) // invalid: fails isValidTmc6200Config
                                                      : ::gd::overtemperatureSelectForCelsius(otCelsius));
    c.shortRetries    = fieldOrKeep(retries);
    c.protectParallel = fieldOrKeep(allBridges);
    c.faultDirect     = fieldOrKeep(faultDirect);
    c.shortToGroundLevel
        = ::gd::fieldForRequest(s2gMv, ::gd::shortLevelForMv(s2gMv, TMC6200_SHORT_LEVEL_S2G_MIN));
    c.shortToSupplyLevel
        = ::gd::fieldForRequest(s2vsMv, ::gd::shortLevelForMv(s2vsMv, TMC6200_SHORT_LEVEL_S2VS_MIN));
    c.shortFilter = ::gd::fieldForRequest(
        filterNs, (filterNs > 0) ? ::gd::shortFilterForNs(static_cast<uint32_t>(filterNs)) : ::gd::KEEP);
    c.shortDelay = ::gd::fieldForRequest(
        delayNs, (delayNs > 0) ? ::gd::shortDelayForNs(static_cast<uint32_t>(delayNs)) : ::gd::KEEP);
    return c;
}

/* Configuration of instance <device> from integration/gdTmc6200Configuration.h (project parameters).
 * \p interfaceMode: see makeTmc6200Config; the board links a gate driver to its PWM group (pwm[].gate_driver),
 * the integration passes 0 for a complementary PWM group, else ::gd::KEEP (IO-25). */
// clang-format off
#define GATE_DRIVER_CONFIG(device, interfaceMode)                                                     \
    ::bsp::gd::makeTmc6200Config(                                                                     \
        (interfaceMode),                                                                              \
        static_cast<int32_t>(NX_DEVICE_##device##_PARAM_OVERTEMPERATURE_SHUTDOWN_C),                  \
        static_cast<int32_t>(NX_DEVICE_##device##_PARAM_SHORT_RETRIES),                               \
        static_cast<int32_t>(NX_DEVICE_##device##_PARAM_SHORT_ALL_BRIDGES_OFF),                       \
        static_cast<int32_t>(NX_DEVICE_##device##_PARAM_FAULT_OUTPUT_EACH_SHORT),                     \
        static_cast<int32_t>(NX_DEVICE_##device##_PARAM_SHORT_TO_GROUND_MV),                          \
        static_cast<int32_t>(NX_DEVICE_##device##_PARAM_SHORT_TO_SUPPLY_MV),                          \
        static_cast<int32_t>(NX_DEVICE_##device##_PARAM_SHORT_FILTER_NS),                             \
        static_cast<int32_t>(NX_DEVICE_##device##_PARAM_SHORT_DELAY_NS))
// clang-format on

} // namespace gd
} // namespace bsp
