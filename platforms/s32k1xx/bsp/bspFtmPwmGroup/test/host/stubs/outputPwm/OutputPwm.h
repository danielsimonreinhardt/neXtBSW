// Host stub of outputPwm/OutputPwm.h: the generated channel enum and the duty scale of the RTE target pwm.<pair>.
#pragma once

#include "bsp/Bsp.h"

#include <cstdint>

namespace bios
{
class OutputPwm
{
public:
#include "bsp/io/outputPwm/outputPwmConfiguration.h"

    static uint32_t const MAX_DUTY_CYCLE = 10000;
};
} // namespace bios
