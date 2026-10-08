/********************************************************************************
 * Copyright (c) 2026 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

/* PLAT-72: result of the clock start-up, family neutral contract.
 *
 * Every family implements nxClockCheck() next to its clock driver. The function reads the clock select and ready
 * status of the hardware and compares it with the generated clock plan (bsp/clock/boardClock.h). It does not rely
 * on anything the clock driver stored: the driver runs in boardInit() before the RAM initialization, so a global
 * variable written there is overwritten by the start-up code. The registers keep the result instead.
 *
 * Finding PLAT-15 (Nucleo F446RE, 2026-09-19): without the external clock (solder bridge SB50 open) the driver
 * stayed on the reset clock without any message - OS tick 167 Hz instead of 1 kHz, UART and CAN dividers wrong.
 * The reporter platforms/common/clockCheck turns this result into a console line at start-up.
 *
 * This file is identical in every family (platform rule P2: platforms/<family> does not depend on
 * platforms/common). The host test clock-check-contract compares the copies byte for byte.
 */

#pragma once

#include <stdint.h>

#ifdef __cplusplus
extern "C"
{
#endif

/* fault: the first thing that does not match the clock plan, in start-up order */
#define NX_CLOCK_CHECK_OK              0U /* source, dividers and ready flags as planned */
#define NX_CLOCK_CHECK_OSC_NOT_READY   1U /* the planned crystal or external clock is not ready */
#define NX_CLOCK_CHECK_PLL_NOT_LOCKED  2U /* the planned PLL is not locked */
#define NX_CLOCK_CHECK_SOURCE_MISMATCH 3U /* system clock source or dividers differ from the plan */

/* monitor: a clock monitor of the family saw a loss of clock (reset reason or pending flag) */
#define NX_CLOCK_CHECK_MONITOR_NONE  0U
#define NX_CLOCK_CHECK_MONITOR_EVENT 1U

/* source: the clock the system clock really runs from (from the select status registers, not from the plan).
 * Neutral names; per family: INTERNAL_SLOW = SIRC (s32k1xx, no counterpart on stm32), INTERNAL_FAST = FIRC / HSI,
 * EXTERNAL = SOSC / HSE, PLL = SPLL / main PLL. A start-up that fell back from the planned external clock shows up
 * here as INTERNAL_FAST although the plan says PLL or EXTERNAL. */
#define NX_CLOCK_SOURCE_UNKNOWN       0U /* not derivable (reserved select code) */
#define NX_CLOCK_SOURCE_INTERNAL_SLOW 1U
#define NX_CLOCK_SOURCE_INTERNAL_FAST 2U
#define NX_CLOCK_SOURCE_EXTERNAL      3U
#define NX_CLOCK_SOURCE_PLL           4U

#define NX_CLOCK_CHECK_REGISTERS 3U

typedef struct
{
    uint32_t fault;         /* NX_CLOCK_CHECK_OK ... NX_CLOCK_CHECK_SOURCE_MISMATCH */
    uint32_t monitor;       /* NX_CLOCK_CHECK_MONITOR_NONE or NX_CLOCK_CHECK_MONITOR_EVENT */
    uint32_t plannedCoreHz; /* core clock of the clock plan (NX_CLOCK_CORE_HZ) */
    uint32_t actualCoreHz;  /* core clock the select status registers give, 0 if not derivable */
    uint32_t registers[NX_CLOCK_CHECK_REGISTERS]; /* raw status registers for the log, meaning per family */
    uint32_t source;        /* NX_CLOCK_SOURCE_*: clock the system clock runs from */
    uint32_t pllInput;      /* NX_CLOCK_SOURCE_INTERNAL_FAST or _EXTERNAL: reference of the PLL if source is
                               NX_CLOCK_SOURCE_PLL, else NX_CLOCK_SOURCE_UNKNOWN */
} nxClockCheckResult;

/* Reads the clock status of the hardware. Call in run mode (not while a low power clock is selected). */
void nxClockCheck(nxClockCheckResult* result);

#ifdef __cplusplus
}
#endif
