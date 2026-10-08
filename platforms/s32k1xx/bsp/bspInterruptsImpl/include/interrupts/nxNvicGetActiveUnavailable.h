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

/* PLAT-12: forced include for external/openbsw/platforms/s32k1xx/bsp/bspInterruptsImpl/src/interrupt_manager.c
 * on an ARMv6-M core (Cortex-M0+).
 *
 * **Finding (reported upstream as PLAT-03):** SYS_GetActive() forwards to NVIC_GetActive(), which ARMv6-M does
 * not have - the NVIC of a Cortex-M0+ has no Interrupt Active Bit Register (CMSIS says so itself:
 * platforms/common/3rdparty/cmsis/core_cm0plus.h, "NVIC_GetActive not available for Cortex-M0+"). The interrupt
 * manager therefore does not compile for the core, although every other function in it does.
 *
 * Nothing in this repository calls SYS_GetActive(); the definition below keeps the translation unit compiling
 * and answers "not active", which is the only answer a core without that register can give. A caller that really
 * needs the active interrupt must not use SYS_GetActive() on ARMv6-M - the honest fix belongs upstream (declare
 * the function only where the architecture has the register).
 */

#include <stdint.h>

#include "mcu/mcu.h"

__STATIC_INLINE uint32_t NVIC_GetActive(IRQn_Type IRQn)
{
    (void)IRQn;
    return 0U; /* ARMv6-M has no NVIC_IABR: the active interrupt cannot be read */
}
