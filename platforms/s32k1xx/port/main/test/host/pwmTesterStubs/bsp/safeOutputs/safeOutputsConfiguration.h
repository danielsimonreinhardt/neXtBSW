// SYS-162: safe outputs table of the own test profile "irrigation_ctrl" of CheckedOutputPwmTesterHostTest.cpp, in the
// form tools/bswgen generates it (templates/safeOutputsConfiguration.h.j2), reduced to what the console wrapper reads:
// the PWM group IRRIGATION (pairs FAN and VALVE of pwmTesterStubs/outputPwm/OutputPwm.h) first, then one safe_off
// output PUMP_EN. The register columns are not used by the wrapper and not expanded here.

#pragma once

#include <stddef.h>

#define NX_SAFE_OUTPUT_COUNT 2

#define NX_SAFE_OFF_OUTPUT_COUNT 1

// clang-format off
#define NX_SAFE_OUTPUTS(X) \
    /* PWM group on FTM2: SWOCTRL */ \
    X(IRRIGATION, 0x4003A000UL + 0x94UL, 0x0FU, 0x40065000UL + 0xE8UL, 0x40000000UL) \
    /* PUMP.en (pumpDriver) on PTB4, active high: clear */ \
    X(PUMP_EN, 0x400FF040UL + 0x8UL, (1UL << 4U), 0x40065000UL + 0x128UL, 0x40000000UL)
// clang-format on
