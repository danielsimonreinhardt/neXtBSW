// SYS-150/SYS-151: safe outputs table of the own test profile of SafeOffCommandHoldHostTest.cpp and
// SafeOffOutputTesterHostTest.cpp, in the form tools/bswgen generates it (templates/safeOutputsConfiguration.h.j2).
// Invented project "irrigation_ctrl", not the eval board: the PWM group FAN on FTM2 (channels 0..3), then two safe_off
// outputs - PUMP_EN on PTB4, active high, and HEATER_N on PTE9, active low. The PWM group shares its name with an
// ordinary digital output FAN (a relay): only the position in the table may decide what is safe_off.

#pragma once

#include "mcu/mcu.h"

#include <stddef.h>

#define NX_SAFE_OUTPUT_COUNT 3

#define NX_SAFE_OFF_OUTPUT_COUNT 2

// clang-format off
#define NX_SAFE_OUTPUTS(X) \
    /* PWM group on FTM2: SWOCTRL */ \
    X(FAN, IP_FTM2_BASE + offsetof(FTM_Type, SWOCTRL), 0x0FU, IP_PCC_BASE + offsetof(PCC_Type, PCCn) + 4UL * PCC_FTM2_INDEX, PCC_PCCn_CGC_MASK) \
    /* PUMP.en (pumpDriver) on PTB4, active high: clear */ \
    X(PUMP_EN, IP_PTB_BASE + offsetof(GPIO_Type, PCOR), (1UL << 4U), IP_PCC_BASE + offsetof(PCC_Type, PCCn) + 4UL * PCC_PORTB_INDEX, PCC_PCCn_CGC_MASK) \
    /* HEATER.in (highSideSwitch) on PTE9, active low: set */ \
    X(HEATER_N, IP_PTE_BASE + offsetof(GPIO_Type, PSOR), (1UL << 9U), IP_PCC_BASE + offsetof(PCC_Type, PCCn) + 4UL * PCC_PORTE_INDEX, PCC_PCCn_CGC_MASK)

// clang-format on

// clang-format off
#define NX_SAFE_OUTPUT_READBACKS(X) \
    X(FAN, IP_FTM2_BASE + offsetof(FTM_Type, SWOCTRL), 0x0F0FU, 0x0FU, IP_PCC_BASE + offsetof(PCC_Type, PCCn) + 4UL * PCC_FTM2_INDEX, PCC_PCCn_CGC_MASK) \
    X(PUMP_EN, IP_PTB_BASE + offsetof(GPIO_Type, PDIR), (1UL << 4U), 0U, IP_PCC_BASE + offsetof(PCC_Type, PCCn) + 4UL * PCC_PORTB_INDEX, PCC_PCCn_CGC_MASK) \
    X(HEATER_N, IP_PTE_BASE + offsetof(GPIO_Type, PDIR), (1UL << 9U), (1UL << 9U), IP_PCC_BASE + offsetof(PCC_Type, PCCn) + 4UL * PCC_PORTE_INDEX, PCC_PCCn_CGC_MASK)

// clang-format on
