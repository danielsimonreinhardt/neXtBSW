/********************************************************************************
 * Copyright (c) 2026 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

/* PLAT-72: start-up report of the clock check, family and core neutral (README.md next to include/). C and C++.
 *
 * Reads the contract clock/nxClockCheck.h of the family that is built (platforms/stm32/bsp/bspClock,
 * platforms/s32k1xx/port/bspConfiguration) and turns its result into one console line. The family modules do not
 * depend on this one (platform rule P2); the integration links both. */

#pragma once

#include "clock/nxClockCheck.h"

#include <stddef.h>

#ifdef __cplusplus
extern "C"
{
#endif

/* Short English text of a fault code (NX_CLOCK_CHECK_*) */
char const* nxClockCheckFaultText(uint32_t fault);

/* Text of the clock the core runs from (NX_CLOCK_SOURCE_*, pllInput only matters for the PLL): "PLL on external
 * oscillator", "PLL on internal RC", "external oscillator", "internal fast RC", "internal slow RC", "unknown".
 * A fallback from the planned external clock is the "internal ... RC" text. At most 26 characters. */
char const* nxClockSourceText(uint32_t source, uint32_t pllInput);

/* One console line without line end, e.g.
 *   "clock: ok, core 80000000 Hz, source PLL on external oscillator"
 *   "clock: FAULT oscillator not ready - core 16000000 Hz instead of 96000000 Hz, source internal fast RC
 *    (regs 0x00058683 0x00000000 ...)"
 * At most 200 characters (the buffer of nxClockCheckReport has 256).
 * Returns what snprintf returns (the length the full line needs). */
int nxClockCheckFormat(nxClockCheckResult const* result, char* buffer, size_t size);

/* Start-up report: runs nxClockCheck() of the family, prints the line with printf and "\r\n", and copies the result
 * to *result when it is not NULL. Returns 1 if the clock is not the planned one or a clock monitor saw a loss, else
 * 0. The reaction (safe state, no CAN start, event) is the integration's; this function only makes it visible. */
int nxClockCheckReport(nxClockCheckResult* result);

#ifdef __cplusplus
}
#endif
