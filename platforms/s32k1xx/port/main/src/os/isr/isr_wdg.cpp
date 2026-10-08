/********************************************************************************
 * Copyright (c) 2024 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

/* Modified for neXtBSW (IO-32):
 * - the safe outputs table of the project (strong nxSafeOutputs from the generated
 *   bsp/safeOutputs/safeOutputsConfiguration.h, tools/bswgen): customHardFaultHandler switches the power outputs off
 *   with it before the dump and before any UART output
 * - the watchdog vector goes straight into customHardFaultHandler (naked, no call before): before, the vector
 *   pointed at the SAFE_ISR wrapper Safe_WDOG_EWM_IRQHandler, which called SafePreIsrHook() first - C code on the
 *   possibly broken stack, and LR no longer held EXC_RETURN when the handler read it (dump EXC_RETURN 0x0001842f,
 *   board 2026-09-18). The hook only closes the MPU gate of the safety RAM for an interrupt that returns; this
 *   handler never returns (dump, then reset in HardFault_Handler_Final), like HardFault_Handler, which never had
 *   the hook. See safeLifecycle/IsrWrapper.cpp. */

#include "bsp/safeOutputs/safeOutputsConfiguration.h"
#include "hardFaultHandler/safeOutputs.h"

#include <cstdint>

namespace
{
// clang-format off
#define NX_SAFE_OUTPUT_ENTRY(name, address, value, gate, gateMask) \
    {static_cast<uint32_t>(address), static_cast<uint32_t>(value), static_cast<uint32_t>(gate), \
     static_cast<uint32_t>(gateMask)},
// clang-format on

// Compile time copy for the checks below; the exported table has the same initializer.
constexpr NxSafeOutput SAFE_OUTPUTS[NX_SAFE_OUTPUT_COUNT + 1U]
    = {NX_SAFE_OUTPUTS(NX_SAFE_OUTPUT_ENTRY){0U, 0U, 0U, 0U}};

// Every entry before the end entry complete, the end entry last (no comparison with the count: it may be 0).
constexpr bool safeOutputsValid()
{
    uint32_t entries = 0U;
    for (NxSafeOutput const& e : SAFE_OUTPUTS)
    {
        if (e.address == 0U)
        {
            break;
        }
        if (((e.address & 3U) != 0U) || (e.value == 0U) || (e.gateAddress == 0U) || (e.gateMask == 0U))
        {
            return false;
        }
        ++entries;
    }
    return (entries + 1U) == (sizeof(SAFE_OUTPUTS) / sizeof(SAFE_OUTPUTS[0]));
}

static_assert(safeOutputsValid(), "safe outputs: every entry needs an aligned register, a value and a clock gate");
static_assert(sizeof(NxSafeOutput) == NX_SAFE_OUTPUT_SIZE, "layout shared with customHardFaultHandler");
} // namespace

extern "C"
{
// Constant initializer only: placed in the flash (.rodata), read by the handler without RAM.
NxSafeOutput const nxSafeOutputs[NX_SAFE_OUTPUT_COUNT + 1U]
    = {NX_SAFE_OUTPUTS(NX_SAFE_OUTPUT_ENTRY){0U, 0U, 0U, 0U}};

#undef NX_SAFE_OUTPUT_ENTRY

#ifndef UNIT_TEST
// Watchdog/EWM vector (bswgen FAMILY_IRQ_HANDLERS, ARMv7-M startUp.S): no function call before the handler, LR
// keeps EXC_RETURN. ARMv6-M: b reaches only +-2 KB, R0 is stacked and free.
#if defined(__ARM_ARCH_6M__)
__attribute__((naked)) void Safe_WDOG_EWM_IRQHandler()
{
    asm volatile("ldr r0, =customHardFaultHandler\n bx r0\n .pool");
}

__attribute__((naked)) void WDOG_EWM_IRQHandler()
{
    asm volatile("ldr r0, =customHardFaultHandler\n bx r0\n .pool");
}
#else
__attribute__((naked)) void Safe_WDOG_EWM_IRQHandler() { asm volatile("b customHardFaultHandler"); }

__attribute__((naked)) void WDOG_EWM_IRQHandler() { asm volatile("b customHardFaultHandler"); }
#endif
#endif

} // extern "C"
