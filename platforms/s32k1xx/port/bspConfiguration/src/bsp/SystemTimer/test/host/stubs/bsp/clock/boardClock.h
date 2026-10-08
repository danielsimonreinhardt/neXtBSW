/* ARCH-14 host test stub for the generated bsp/clock/boardClock.h: only the frequencies SystemTimer.cpp uses.
 * Test profiles set them on the command line (-DNX_CLOCK_CORE_HZ=...), default is a 64 MHz system clock. */

#pragma once

#ifndef NX_CLOCK_CORE_HZ
#define NX_CLOCK_CORE_HZ 64000000U
#endif
#ifndef NX_CLOCK_FIRC_HZ
#define NX_CLOCK_FIRC_HZ 48000000U
#endif
