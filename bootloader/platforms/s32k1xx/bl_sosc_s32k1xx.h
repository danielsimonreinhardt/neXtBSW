#pragma once

/* SOSC (external oscillator) settings of the family s32k1xx, derived from the crystal of the board: BL_OSC_HZ of
 * the generated bootloader/bl_board.h, i.e. board.clock.sosc_hz. Before BL-05 the HAL wrote RANGE(3) (high
 * range) unconditionally, so a board with a 4 MHz crystal would have had no oscillator, no FlexCAN clock and a
 * silent bootloader although the derivative database allows 4 MHz (ARCH-09, finding B7).
 *
 * Own header, not part of hal_s32k1xx.c, so that the derivation has a host test without the whole HAL
 * (bootloader/test/host/test_bl_platform_host.py). Nothing here reads a register: the board value stays in the
 * configuration, the family code only encodes it.
 *
 * [RM] S32K1xx Reference Manual, SCG_SOSCCFG:
 *   RANGE 1 low frequency range    32 kHz .. 40 kHz
 *   RANGE 2 medium frequency range  1 MHz ..  8 MHz
 *   RANGE 3 high frequency range    8 MHz .. 40 MHz
 * 8 MHz is in both the medium and the high range; the high range is used, like the clock driver of the
 * application (external/openbsw .../bspClock/src/clockConfig.cpp) and the clock model of the tools
 * (tools/nxconfig/clock.py, S32K1_SOSC_RANGE3_HZ) - a board with an 8 MHz crystal keeps the settings it has.
 *
 * EREFS: crystal (or resonator) on EXTAL/XTAL, not an external square wave. HGO: low gain, like the clock
 * driver of the application. Both are board facts; as long as every board here has a crystal on EXTAL/XTAL they
 * stay constants of the family HAL (proposal of BL-05: board.clock.sosc_mode/sosc_gain if a board differs). */

#ifndef BL_OSC_HZ
#error "BL_OSC_HZ is missing: the generated bootloader/bl_board.h must be included before this header"
#endif

#if (BL_OSC_HZ >= 8000000UL) && (BL_OSC_HZ <= 40000000UL)
#define BL_SOSC_RANGE 3U
#elif (BL_OSC_HZ >= 1000000UL) && (BL_OSC_HZ < 8000000UL)
#define BL_SOSC_RANGE 2U
#elif (BL_OSC_HZ >= 32000UL) && (BL_OSC_HZ <= 40000UL)
#define BL_SOSC_RANGE 1U
#else
#error "board.clock.sosc_hz is outside the SOSC ranges of s32k1xx (32..40 kHz, 1..8 MHz, 8..40 MHz)"
#endif

#define BL_SOSC_EREFS 1U
#define BL_SOSC_HGO   0U
