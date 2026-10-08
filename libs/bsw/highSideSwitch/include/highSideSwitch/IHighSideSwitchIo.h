#pragma once

#include <cstdint>

namespace highside
{

/** One sample of the current sense path. */
struct SenseSample
{
    /** Load current in mA, converted by the integration from the sense channel (board facts). */
    uint32_t loadMa;
    /** Unconverted converter code of the sense channel, for diagnosis only (console, log). */
    uint32_t raw;
};

/**
 * Pins of one smart high-side switch as seen by the driver: input IN, diagnosis enable DEN and the
 * current sense IS. The integration implements it on top of the MCU abstraction (digital outputs and
 * an ADC channel of the project) and converts the sense signal into the load current with the board's
 * characteristic of that channel (IO-21: sense ratio and sense resistor are board facts, see
 * HighSideSwitchSense.h), so the driver stays board, part and family independent.
 */
class IHighSideSwitchIo
{
public:
    /** Drives IN. \return false if the output could not be written */
    virtual bool setEnable(bool on) = 0;

    /** Drives DEN. \return false if the output could not be written */
    virtual bool setDiagEnable(bool on) = 0;

    /** Samples the current sense. \return false if no valid sample is available (sample not meaningful) */
    virtual bool readSense(SenseSample& sample) = 0;

protected:
    ~IHighSideSwitchIo() = default;
};

} // namespace highside
