// SPDX-License-Identifier: Apache-2.0
// Host stub of the OpenBSW system timer for the LPSPI host test (IO-25): every read advances the time, so a
// polling loop that waits for a flag the model never sets runs into its timeout instead of hanging.
#pragma once

#include <cstdint>

extern uint32_t nxTestNowUs;

// SYS-95 (DFA-6 b): the test can stop the time (the DWT switched off) and count the reads (= polls of a wait)
inline bool nxTestTimeStopped  = false;
inline uint32_t nxTestTimeReads = 0U;

inline uint32_t getSystemTimeUs32Bit(void)
{
    ++nxTestTimeReads;
    if (!nxTestTimeStopped)
    {
        nxTestNowUs += 10U;
    }
    return nxTestNowUs;
}
