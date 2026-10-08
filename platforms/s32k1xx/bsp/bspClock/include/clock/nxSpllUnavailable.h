/********************************************************************************
 * Copyright (c) 2026 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

#pragma once

/* PLAT-12: forced include for external/openbsw/platforms/s32k1xx/bsp/bspClock/src/clockConfig.cpp on a
 * derivative without SPLL (S32K11x; mcudb clock without pll, tools/nxconfig NX_CLOCK_PLL=OFF).
 *
 * **Finding (PLAT-11, reported upstream as PLAT-03):** the driver guards the SPLL primitives with
 *     #ifdef SCG_SPLLCSR_SPLLEN_MASK -> PLL_AVAILABLE
 * and spllStart()/spllStop()/isSpllSysClk() only exist inside that guard - but `pllOff()` (line 437) calls
 * isSpllSysClk() and spllStop() **unconditionally**. The translation unit therefore does not compile without an
 * SPLL, although everything else in it is derivative tolerant.
 *
 * Both definitions below are what a device without SPLL means, not a workaround with a different behaviour:
 * the PLL can never be the system clock, and stopping a PLL that does not exist is nothing to do. They sit at
 * global scope, where the call inside the extern "C" block of the driver finds them (the anonymous namespace of
 * the driver defines no such name in this configuration).
 *
 * Remove this file as soon as the upstream fix is in the submodule.
 */

#ifdef __cplusplus
extern "C++"
{
#endif

/* SCG_RCCR[SCS] can never select the SPLL on a derivative without one */
inline bool isSpllSysClk() { return false; }

/* No SCG_SPLLCSR to clear */
inline void spllStop() {}

#ifdef __cplusplus
}
#endif
