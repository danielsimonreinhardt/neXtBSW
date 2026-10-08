// SYS-30: safe outputs table and its read-back list of the test profile of SafeIoHostTest.cpp, in the form tools/bswgen
// generates them (tools/bswgen/templates/safeOutputsConfiguration.h.j2): the PWM group TEST on FTM1 (channels 0/1) and
// the safe_off output TEST_GD.drv_en on PTA7, active high. Not the eval board (FTM0 0x3F, PTC11).

#pragma once

#include "mcu/mcu.h"

#include <stddef.h>

#define NX_SAFE_OUTPUT_COUNT     2
#define NX_SAFE_OFF_OUTPUT_COUNT 1

// clang-format off
#define NX_SAFE_OUTPUTS(X) \
    /* PWM group on FTM1: SWOCTRL */ \
    X(TEST, IP_FTM1_BASE + offsetof(FTM_Type, SWOCTRL), 0x03U, IP_PCC_BASE + offsetof(PCC_Type, PCCn) + 4UL * PCC_FTM1_INDEX, PCC_PCCn_CGC_MASK) \
    /* TEST_GD.drv_en on PTA7, active high: clear */ \
    X(TEST_GD_DRV_EN, IP_PTA_BASE + offsetof(GPIO_Type, PCOR), (1UL << 7U), IP_PCC_BASE + offsetof(PCC_Type, PCCn) + 4UL * PCC_PORTA_INDEX, PCC_PCCn_CGC_MASK)

// clang-format on

// clang-format off
#define NX_SAFE_OUTPUT_READBACKS(X) \
    X(TEST, IP_FTM1_BASE + offsetof(FTM_Type, SWOCTRL), 0x0303U, 0x03U, IP_PCC_BASE + offsetof(PCC_Type, PCCn) + 4UL * PCC_FTM1_INDEX, PCC_PCCn_CGC_MASK) \
    X(TEST_GD_DRV_EN, IP_PTA_BASE + offsetof(GPIO_Type, PDIR), (1UL << 7U), 0U, IP_PCC_BASE + offsetof(PCC_Type, PCCn) + 4UL * PCC_PORTA_INDEX, PCC_PCCn_CGC_MASK)

// clang-format on
