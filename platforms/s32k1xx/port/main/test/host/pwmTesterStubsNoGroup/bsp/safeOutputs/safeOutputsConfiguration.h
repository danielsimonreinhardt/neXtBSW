// SYS-162: safe outputs table of the second test profile "dosing_unit" of CheckedOutputPwmTesterHostTest.cpp: a project
// without a PWM group (ARCH-17) - the table holds one safe_off output only, OutputPwm only OpenBSW's static dummy
// channel (pwmTesterStubsNoGroup/outputPwm/OutputPwm.h). The console group "pwm" must work there exactly as in OpenBSW.

#pragma once

#include <stddef.h>

#define NX_SAFE_OUTPUT_COUNT 1

#define NX_SAFE_OFF_OUTPUT_COUNT 1

// clang-format off
#define NX_SAFE_OUTPUTS(X) \
    /* DOSING.en (pumpDriver) on PTC3, active high: clear */ \
    X(DOSING_EN, 0x400FF080UL + 0x8UL, (1UL << 3U), 0x40065000UL + 0x12CUL, 0x40000000UL)
// clang-format on
