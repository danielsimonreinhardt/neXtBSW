#pragma once

#include <cstdint>

namespace highside
{

/**
 * Conversion of the sense channel into the load current (IO-21). The integration passes the generated
 * characteristic of the ADC channel wired to the IS connection (nxAdcPhys_<channel> of
 * bsp/adc/analogSensorConfiguration.h, tools/nxconfig/sensor.py: highside_is k_ILIS * U / R_IS in mA from the
 * board's adc[].sensor). The part facts stay in the board description; this header knows none of them.
 */
using SenseConversion = int32_t (*)(uint16_t raw, uint8_t* quality);

/**
 * Load current in mA of one converter code.
 *
 * The quality of the characteristic is not used: it marks the ends of the converter, and both ends are
 * regular readings of a high-side switch - code 0 while the switch is off or the load draws nothing, full
 * scale when the fault current of the switch drives the pin above the reference (IS fault level). A failed
 * conversion is reported by the caller (IHighSideSwitchIo::readSense returns false). A negative value (not
 * produced by highside_is, possible with an offset of another characteristic) counts as 0 mA.
 */
inline uint32_t loadCurrentMa(SenseConversion const convert, uint16_t const raw)
{
    uint8_t quality     = 0U;
    int32_t const value = convert(raw, &quality);
    return (value > 0) ? static_cast<uint32_t>(value) : 0U;
}

} // namespace highside
