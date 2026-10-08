/* Host test stub of bsp/timer/SystemTimer.h (SYS-05): a virtual clock the test advances, so a runnable can be
 * made artificially slow without waiting. Defined in the test. */

#pragma once

#include <cstdint>

uint32_t getSystemTimeUs32Bit();
