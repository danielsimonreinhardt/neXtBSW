/* Host test stub of mcu/mcu.h (IO-16): only the SIM registers and bits SafeSystem uses.
 * SIM is a plain struct here, so the test can read back what SafeSystem wrote. */

#pragma once

#include <cstdint>

struct SimType
{
    uint32_t CHIPCTL;
    uint32_t ADCOPT;
};

extern SimType simRegisters;

#define SIM (&simRegisters)

// Values of S32K148_SIM.h (identical on S32K144)
#define SIM_CHIPCTL_ADC_SUPPLY_MASK    (0x70000U)
#define SIM_CHIPCTL_ADC_SUPPLY_SHIFT   (16U)
#define SIM_CHIPCTL_ADC_SUPPLYEN_MASK  (0x80000U)
#define SIM_CHIPCTL_ADC_SUPPLYEN_SHIFT (19U)
