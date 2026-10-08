// SPDX-License-Identifier: Apache-2.0
// Host stub of the OpenBSW system timer for the FlexIO I2C host test (IO-09): every read advances the time, so a
// polling loop that waits for a flag the model never sets runs into its timeout instead of hanging.
#pragma once

#include <cstdint>

extern uint32_t nxTestNowUs;

inline uint32_t getSystemTimeUs32Bit(void)
{
    nxTestNowUs += 10U;
    return nxTestNowUs;
}
