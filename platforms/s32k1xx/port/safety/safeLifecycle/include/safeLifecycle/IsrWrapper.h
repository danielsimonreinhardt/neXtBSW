/********************************************************************************
 * Copyright (c) 2025 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

#pragma once

// neXtBSW (COM-08): the CAN handlers of the OpenBSW reference (two fixed CAN0 vectors) come from the generated
// message buffer vectors of every used bus (NX_CAN_MB_VECTORS, PLAT-12/ARCH-07) instead.
#include "bsp/can/canDeviceConfiguration.h"
// neXtBSW (SYS-170, SYS69-S1): every other external vector of the derivative (generated list, tools/bswgen)
#include "interrupts/nxSafeIsrVectors.h"

// clang-format off
#define SAFE_ISR(func) \
void Safe_##func(void) \
{                      \
    SafePreIsrHook();  \
    func();            \
    SafePostIsrHook(); \
}

#define SAFE_ISR_DECL(func) \
void func(void);            \
void Safe_##func(void);
// clang-format on

extern "C"
{
void SafePreIsrHook(void);
void SafePostIsrHook(void);

SAFE_ISR_DECL(FTFC_Fault_IRQHandler);
SAFE_ISR_DECL(WDOG_EWM_IRQHandler);
SAFE_ISR_DECL(ERM_double_fault_IRQHandler);
// SYS-170: the halt deadman (IO-32), no hook wrapper - Safe_ is an alias of the handler (integration/Integration.cpp)
SAFE_ISR_DECL(LPIT0_Ch1_IRQHandler);
// SYS-170: the RTOS tick (core vector 15) runs through its wrapper as well
SAFE_ISR_DECL(SysTick_Handler);
// clang-format off
#define NX_SAFE_ISR_DECL_CAN_RX(bus, device, isr, irq)    SAFE_ISR_DECL(isr)
#define NX_SAFE_ISR_DECL_CAN_TX(bus, device, isr, irq)    SAFE_ISR_DECL(isr)
#define NX_SAFE_ISR_DECL_CAN_RX_TX(bus, device, isr, irq) SAFE_ISR_DECL(isr)
#define NX_SAFE_ISR_DECL_VECTOR(handler, served)          SAFE_ISR_DECL(handler)
// clang-format on
NX_CAN_MB_VECTORS(NX_SAFE_ISR_DECL_CAN)
NX_SAFE_ISR_VECTORS(NX_SAFE_ISR_DECL_VECTOR)
#undef NX_SAFE_ISR_DECL_CAN_RX
#undef NX_SAFE_ISR_DECL_CAN_TX
#undef NX_SAFE_ISR_DECL_CAN_RX_TX
#undef NX_SAFE_ISR_DECL_VECTOR
}
