/********************************************************************************
 * Copyright (c) 2024 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

#include "safeLifecycle/IsrWrapper.h"

extern "C"
{
SAFE_ISR(FTFC_Fault_IRQHandler);
// neXtBSW (IO-32): no SAFE_ISR wrapper for WDOG_EWM_IRQHandler. Safe_WDOG_EWM_IRQHandler is the naked watchdog
// handler in main/src/os/isr/isr_wdg.cpp: it branches into customHardFaultHandler with LR = EXC_RETURN, which
// switches the power outputs off first and never returns (dump, reset). SafePreIsrHook() only closes the MPU gate of
// the safety RAM for an interrupt that returns to the interrupted code (SafePostIsrHook reopens it); running it here
// cost C code on a possibly broken stack before the outputs were off and overwrote EXC_RETURN.
SAFE_ISR(ERM_double_fault_IRQHandler);

/* neXtBSW (SYS-170, SYS69-S1, finding SFP-F01 of doc/safety/reviews/code-inspection/INS-SFP-1.md): until SYS-170 only
 * FTFC, ERM and the CAN message buffer vectors had a wrapper; every other interrupt that preempted
 * SafetyManager::cyclic() (gate open for the whole cycle) ran with write access to the protected RAM. Now the startup
 * routes every external vector of the derivative through Safe_<handler> (startup/nxIrqVectors.inc names the handler,
 * ARMv7-M main/src/bsp/startUp.S and the ARMv6-M template prefix it), and this file defines the wrapper of each one
 * from the generated list interrupts/nxSafeIsrVectors.h - the CAN message buffer vectors of the used buses
 * (COM-08, handlers in os/isr/isr_can.cpp) included, used or unused alike: an unused vector reaches DefaultISR through
 * its wrapper with the gate closed.
 *
 * The handler behind an unused vector is a weak alias of nxSafeIsrUnusedHandler (DefaultISR); the integration
 * overrides it with its strong definition (UART, pin interrupts, sample groups). `served` = 1 marks the
 * handlers whose object nothing but this reference pulls from its archive (isr_can.cpp): no weak default, a missing
 * definition is a link error instead of a silently dead vector.
 *
 * Not wrapped, by design (doc/safety/unit-design.md 5.4): the safety handlers above, the halt deadman of the debug
 * halt guard on LPIT0 channel 1 (IO-32: its first action is the safe outputs walk, no call before it; it stores only
 * peripheral registers and a flag outside the protected RAM - Safe_LPIT0_Ch1_IRQHandler is an alias of the handler in
 * integration/Integration.cpp, the weak default below without a guard) and the watchdog, HardFault,
 * BusFault and UsageFault (they never return - the hook would reopen nothing, and they must reach the safe outputs
 * without C code first), SVC and PendSV (RTOS context switch: PendSV stacks the outgoing task's registers onto its
 * process stack, which SafePreIsrHook() makes read-only for the safety task; and a post hook that reopens the gate
 * after a switch would hand it to the incoming task. The safety task has the highest priority, so no switch away from
 * it happens while its cycle runs). NMI, MemManage and DebugMon stay on DefaultISR. */
SAFE_ISR(SysTick_Handler);

extern void DefaultISR(void);

// The default behind every unused vector: DefaultISR of the startup or the strong one of OpenBSW's interrupt manager
// (vIllegalISR). Called from inside the wrapper, so the gate is closed while it runs.
void nxSafeIsrUnusedHandler(void) { DefaultISR(); }

// Vector of the halt deadman in a project without the debug halt guard (no strong alias in Integration.cpp)
void Safe_LPIT0_Ch1_IRQHandler(void) __attribute__((weak, alias("nxSafeIsrUnusedHandler")));

// clang-format off
#define NX_SAFE_ISR_DEFAULT_0(handler) void handler(void) __attribute__((weak, alias("nxSafeIsrUnusedHandler")));
#define NX_SAFE_ISR_DEFAULT_1(handler)
#define NX_SAFE_ISR_VECTOR(handler, served) NX_SAFE_ISR_DEFAULT_##served(handler) SAFE_ISR(handler)
// clang-format on
NX_SAFE_ISR_VECTORS(NX_SAFE_ISR_VECTOR)
#undef NX_SAFE_ISR_VECTOR
#undef NX_SAFE_ISR_DEFAULT_0
#undef NX_SAFE_ISR_DEFAULT_1
}
