/********************************************************************************
 * Copyright (c) 2024 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

#include "reset/softwareSystemReset.h"

#include <hardFaultHandler/HardFaultDump.h>
#include <mcu/mcu.h>
#include <os/IsrStack.h>
#include <safeMemory/SafeMemory.h>
#include <safeSupervisor/SafeConsole.h>

#include <etl/infinite_loop.h>

#include <cstdint>

#if defined(SUPPORT_FREERTOS)
// SYS-80: prototypes of the kernel fault hooks below (the kernel configuration of the family calls them)
#include <os/FreeRtosPlatformConfig.h>
#include <safeMemory/SafetyShell.h>
#include <safeSupervisor/SafeSupervisor.h>
#endif

namespace
{
/* SYS-80 (from SYS-59): the last line before the reset of a fault exception, written like the SAFETY RESET line of
 * the safe supervisor (safeSupervisor/SafeConsole.h): first the transmitter finishes what the interrupted writer left
 * in it, then the line, then its last character leaves the shift register - printf (the charIo layer) could drop the
 * first byte on a busy transmitter and the reset cut the last one (board captures 152, 63: "dump saved, reset"
 * directly followed by the next start's first line). Polling only, bounded, no printf, no logger, no RTOS: the fault
 * may have hit any of them. A transmitter that does not get idle costs one flush time-out and no line. The dump itself
 * is already complete in the no-init RAM (customHardFaultHandler) and is printed after the reset. */
template<uint32_t N>
void writeFaultLine(char const (&line)[N])
{
    if (::safety::safeConsoleFlush())
    {
        (void)::safety::safeConsoleWrite(line, N - 1U);
        (void)::safety::safeConsoleFlush();
    }
}
} // namespace

extern "C"
{
// SYS-97: see os/IsrStack.h. In the general .bss (library startUp, not the protected safety RAM): HardFault_Handler_Final
// writes it with the MPU gate in whatever state the fault left it.
uint32_t volatile nxIsrStackOverflowMsp = 0U;

#ifndef UNIT_TEST
// WARNING:
// Do NOT modify this function if possible. Use HardFault_Handler_Final instead.
// Refer to hardFaultHandler documentation for details.
// PLAT-20: naked and without printf before the branch. The OpenBSW version calls printf first, which
// overwrites EXC_RETURN in LR and moves MSP, so customHardFaultHandler would read the wrong stack frame.
#if defined(__ARM_ARCH_6M__)
// PLAT-12: ARMv6-M (Thumb-1) has only the +-2 KB `b`; customHardFaultHandler is in another library and was out of
// reach in the S32K116 image (R_ARM_THM_JUMP11 truncated). Through a register like the watchdog vector (isr_wdg.cpp);
// R0 is stacked by the exception entry, customHardFaultHandler reads it from the frame.
__attribute__((naked)) void HardFault_Handler() { asm volatile("ldr r0, =customHardFaultHandler\n bx r0\n .pool"); }
#else
__attribute__((naked)) void HardFault_Handler() { asm volatile("b customHardFaultHandler"); }
#endif
#else
void HardFault_Handler() { etl::infinite_loop(); }
#endif

// SYS-97: end of the hard fault reaction after an overflow of the interrupt stack into its guard
// (HardFault_Handler_Final moved the MSP back to the top): the stacked frame in the dump is not the one of the fault
// (it was never written), CFSR/BFAR are. Own function with its own line.
__attribute__((noinline, noreturn)) void nxIsrStackOverflowFinal()
{
    writeFaultLine("\r\nHARD FAULT RESET: interrupt stack overflow (guard), outputs off\r\n");
    softwareSystemReset();
    etl::infinite_loop();
}

// The C part of HardFault_Handler_Final (SYS-97: until then its body).
__attribute__((noinline, noreturn)) void nxHardFaultFinal()
{
    // The power outputs are off (safe outputs table, first action of customHardFaultHandler) and the dump
    // (platforms/s32k1xx/hardFaultHandler) is complete here; it is printed after the reset.
    // SYS-169: the watchdog's pre-reset interrupt (vector WDOG_EWM_IRQn + 16) takes this path too (SYS-103) and has
    // counted as a watchdog reset since SYS-148 - its line says so, a fault exception keeps "HARD FAULT RESET".
    // Only a valid dump names the vector (getDumpException is 0 otherwise), so every doubtful record stays a fault.
    if (::hardfault::getDumpException(::hardfault::getDumpRegion()) == (static_cast<uint32_t>(WDOG_EWM_IRQn) + 16U))
    {
        writeFaultLine("\r\nWATCHDOG RESET: outputs off, dump saved\r\n");
    }
    else
    {
        writeFaultLine("\r\nHARD FAULT RESET: outputs off, dump saved\r\n");
    }
    // SYS-98 (STK-4): no checkEccErrors() any more - its only effect was a Logger::debug line into the buffer of
    // the asynchronous logger, which the reset below discards, on the interrupt stack of the fault
    softwareSystemReset();
    etl::infinite_loop();
}

#ifndef UNIT_TEST
// SYS-97: customHardFaultHandler branches here after the safe outputs walk (always its first action, SM-17) and the
// dump, both without the stack, still on the stack of the fault. One check of the interrupt stack before any C code:
// an MSP below __StackLimit means the stack ran into the guard below it (MPU: the core may read the guard - the dump
// read the frame there - but not write it), which is what raised this fault; the first push of the C part would
// fault again, and a fault in the hard fault handler locks the core up. So the MSP is recorded and moved back to
// __StackTop: the interrupted contexts are abandoned, the reaction ends in the reset. naked: no prologue, only R0/R1
// (the dump has saved every register). ARMv7-M branches with b.w; ARMv6-M (Thumb-1, shares this file) has no long
// unconditional branch and goes through a register.
__attribute__((naked)) void HardFault_Handler_Final()
{
    asm volatile(
        "mrs   r0, msp\n"
        "ldr   r1, =__StackLimit\n"
        "cmp   r0, r1\n"
        "bhs   1f\n"
        "ldr   r1, =nxIsrStackOverflowMsp\n"
        "str   r0, [r1]\n"
        "ldr   r0, =__StackTop\n"
        "msr   msp, r0\n"
        "isb\n"
#if defined(__ARM_ARCH_6M__)
        "ldr   r0, =nxIsrStackOverflowFinal\n"
        "bx    r0\n"
        "1:\n"
        "ldr   r0, =nxHardFaultFinal\n"
        "bx    r0\n"
#else
        "b.w   nxIsrStackOverflowFinal\n"
        "1:\n"
        "b.w   nxHardFaultFinal\n"
#endif
    );
}
#else
// Host test: the record stands for the MSP check of the target (nxIsrStackOverflowMsp set = MSP below the limit)
void HardFault_Handler_Final()
{
    if (nxIsrStackOverflowMsp != 0U)
    {
        nxIsrStackOverflowFinal();
    }
    nxHardFaultFinal();
}
#endif

// The two handlers are not enabled (SCB->SHCSR), so a bus or usage fault escalates to the hard fault above; they stay
// as vectors of the family startup. SYS-80: the same line path as the hard fault, no printf.
void BusFault_Handler()
{
    writeFaultLine("\r\nHARD FAULT RESET: bus fault, no dump\r\n");
    softwareSystemReset();
    etl::infinite_loop();
}

void UsageFault_Handler()
{
    writeFaultLine("\r\nHARD FAULT RESET: usage fault, no dump\r\n");
    softwareSystemReset();
    etl::infinite_loop();
}

#if defined(SUPPORT_FREERTOS)
/* SYS-80 (findings R1/R2 of doc/safety/rtos-comparison.md): faults the kernel finds in itself go to the safe
 * supervisor - S1, mark of the reset-loop record, flush, SAFETY RESET line, flush, reset (SafeSupervisor::handle(),
 * the order of SYS-59) - instead of a printf and carrying on (stack overflow) or nothing at all (assertions).
 *
 * Contexts: the stack check runs in the context switch (PendSV, main stack), an assertion in a task, an interrupt or
 * in main() before the scheduler. The SafetyShell suspends all interrupts and opens the write access to the protected
 * RAM of the supervisor (MPU gate, restored by nobody: the reaction ends in the reset). The kernel is called from
 * app_main() only, after main() has constructed the supervisor; there is no kernel call before that.
 * Nothing on this path calls the kernel, so a second kernel fault during the reaction cannot happen; a hard fault
 * during it takes the hard fault path above (outputs off first). */
void nxKernelStackOverflow(uint32_t const taskNumber, char const* const taskName)
{
    ::safety::SafetyShell const shell;
    ::safety::SafeSupervisor::getInstance().reportStackOverflow(taskName, taskNumber);
    // Not reached: STACK_OVERFLOW is an S1 + S2 reaction. Should handle() ever come back, reset anyway.
    softwareSystemReset();
    etl::infinite_loop();
}

// noinline: the return address is the one of the kernel's call, not of an inlined copy
__attribute__((noinline)) void nxKernelAssertFailed(uint32_t const line)
{
    // Address of the call instruction (BL, 4 bytes) in the kernel function whose check failed; with `line` (its
    // __LINE__) that names file and line: arm-none-eabi-addr2line -f -e app.ecu.elf <address>
    uintptr_t const returnAddress = reinterpret_cast<uintptr_t>(__builtin_return_address(0));
    uint32_t const callSite       = static_cast<uint32_t>((returnAddress & ~static_cast<uintptr_t>(1U)) - 4U);
    ::safety::SafetyShell const shell;
    ::safety::SafeSupervisor::getInstance().reportRtosAssert(callSite, line);
    // Not reached: RTOS_ASSERT is an S1 + S2 reaction. Should handle() ever come back, reset anyway.
    softwareSystemReset();
    etl::infinite_loop();
}
#endif

} // extern "C"

#ifndef UNIT_TEST
// SYS-97: fill pattern and high-water mark of the interrupt stack (os/IsrStack.h)
extern "C"
{
extern uint32_t __StackLimit[];
extern uint32_t __StackTop[];
extern uint32_t __StackGuardStart[];
extern uint32_t __StackGuardEnd[];
}

namespace isrstack
{
// Reserve below the stack pointer of this function that the fill leaves alone. fillWords is inline, so nothing of
// this function lies below its stack pointer; an interrupt entering meanwhile is harmless - its frame is dead again
// when the loop goes on.
static constexpr uintptr_t FILL_RESERVE = 64U;

void fillBelowStackPointer()
{
    uintptr_t sp = 0U;
    asm volatile("mov %0, sp" : "=r"(sp));
    uint32_t const volatile* const to
        = reinterpret_cast<uint32_t const volatile*>((sp - FILL_RESERVE) & ~uintptr_t{3U});
    fillWords(__StackLimit, to);
}

Use measure()
{
    uintptr_t const limit = reinterpret_cast<uintptr_t>(__StackLimit);
    uintptr_t const top   = reinterpret_cast<uintptr_t>(__StackTop);
    uint32_t const size   = static_cast<uint32_t>(top - limit);
    uint32_t const free   = untouchedBytes(__StackLimit, __StackTop);
    uintptr_t const guard = reinterpret_cast<uintptr_t>(__StackGuardStart);
    return Use{
        size,
        size - free,
        static_cast<uint32_t>(reinterpret_cast<uintptr_t>(__StackGuardEnd) - guard),
        static_cast<uint32_t>(guard)};
}
} // namespace isrstack
#endif
