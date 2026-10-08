/********************************************************************************
 * Copyright (c) 2026 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

/* Modified for neXtBSW (PLAT-10): startup and vector table template for ARMv6-M (Cortex-M0/M0+, no FPU), derived
 * from platforms/s32k1xx/port/main/src/bsp/startUp.S (from
 * external/openbsw/executables/referenceApp/platforms/s32k148evb/main/src/bsp/startUp.S). Changes:
 * - .arch armv6s-m (as -mcpu=cortex-m0plus), Thumb-1 instructions only (no post-index addressing, no 32-bit
 *   data processing)
 * - no FPU enable (CPACR), no MemManage/BusFault/UsageFault/DebugMon vectors (reserved on ARMv6-M)
 * - one NVIC ICER/ICPR register (at most 32 external interrupts on ARMv6-M)
 * - external interrupt vectors from the derivative's list startup/nxIrqVectors.inc (see README.md), each handler weak
 *   and defaulting to DefaultISR; system handlers weak as well
 * - BSS loops clear [start, end) (the ARMv7-M original also writes the word at the end address)
 * Same linker script symbols as the ARMv7-M startup, so the application linker template serves both. */

    .syntax unified
    .arch armv6s-m

/* External interrupt list: NX_IRQ_VECTOR(handler) per IRQ number from 0, NX_IRQ_RESERVED() for unused numbers.
   SYS-170 (SYS69-S1): with NX_STARTUP_SAFE_ISR (set by the application, platforms/s32k1xx/port/main/CMakeLists.txt)
   every external vector and SysTick point at the SAFE_ISR wrapper Safe_<handler> of safeLifecycle/IsrWrapper.cpp,
   which closes the MPU gate of the protected RAM around the handler; the weak defaults of the handlers are defined
   there, not here. Without it (build test of the template) the vectors point at the handlers as before. */
#define NX_IRQ_RESERVED()      .long 0 ;
#if defined(NX_STARTUP_SAFE_ISR)
#define NX_IRQ_VECTOR(handler) .long Safe_##handler ;
#define NX_SYSTICK_VECTOR      Safe_SysTick_Handler
#else
#define NX_IRQ_VECTOR(handler) .long handler ;
#define NX_SYSTICK_VECTOR      SysTick_Handler
#endif

    .section .isr_vector, "a"
    .align 2
    .globl __isr_vector
__isr_vector:
    .long   __StackTop                                      /* Top of Stack */
    .long   Reset_Handler                                   /* Reset Handler */
    .long   NMI_Handler                                     /* NMI Handler */
    .long   HardFault_Handler                               /* Hard Fault Handler */
    .long   0                                               /* Reserved */
    .long   0                                               /* Reserved */
    .long   0                                               /* Reserved */
    .long   0                                               /* Reserved */
    .long   0                                               /* Reserved */
    .long   0                                               /* Reserved */
    .long   0                                               /* Reserved */
    .long   SVC_Handler                                     /* SVCall Handler */
    .long   0                                               /* Reserved */
    .long   0                                               /* Reserved */
    .long   PendSV_Handler                                  /* PendSV Handler */
    .long   NX_SYSTICK_VECTOR                               /* SysTick Handler */

                                                            /* External Interrupts */
#include "startup/nxIrqVectors.inc"

    .if (. - __isr_vector) > ((16 + 32) * 4)
    .error "ARMv6-M has at most 32 external interrupts (startup/nxIrqVectors.inc)"
    .endif
    .size    __isr_vector, . - __isr_vector

/* No Flash Configuration Field here: it belongs to the bootloader at 0x400. */

    .section .text
    .equ _NVIC_ICER,  0xE000E180
    .equ _NVIC_ICPR,  0xE000E280
    .equ _NVIC_VTOR,  0xE000ED08
    .text
    .thumb

/* Reset Handler */

    .thumb_func
    .align 2
    .globl   Reset_Handler
    .type    Reset_Handler, %function

Reset_Handler:

    cpsid   i               /* Mask interrupts */
    ldr r0, =_NVIC_VTOR     /* vector table is behind the bootloader (needs VTOR, S32K116/S32K118: present) */
    ldr r1, =__isr_vector
    str r1, [r0]
    ldr r0, =_NVIC_ICER     /* Disable interrupts and clear pending flags: one register on ARMv6-M */
    ldr r1, =_NVIC_ICPR
    ldr r2, =0xFFFFFFFF
    str r2, [r0]
    str r2, [r1]

    /* Init the rest of the registers */
    movs    r1, #0
    movs    r2, #0
    movs    r3, #0
    movs    r4, #0
    movs    r5, #0
    movs    r6, #0
    movs    r7, #0
    mov     r8, r7
    mov     r9, r7
    mov     r10, r7
    mov     r11, r7
    mov     r12, r7
    /* Initialize the stack pointer */
    ldr     r0, =__StackTop
    mov     sp, r0
/* c code call stack init: clear the 32 bytes below the stack top */
    subs    r0, #32
.LCS:
    str     r1, [r0, r3]
    adds    r3, #4
    cmp     r3, #32
    bne     .LCS

    bl boardInit

    cpsie   i               /* Unmask interrupts */

    /* Init ECC RAM */

    ldr r1, =__DATA_RAM
    ldr r2, =__DATA_RAM_END

    subs    r2, r2, r1
    ble .LC5

    movs    r0, #0
.LC4:
    str     r0, [r1]
    adds    r1, #4
    subs    r2, #4
    bgt     .LC4
.LC5:

/*     Loop to copy data from read only memory to RAM.
 *      __etext: End of code section, i.e., begin of data sections to copy from.
 *      __data_start__/__data_end__: RAM address range that data should be
 *      copied to. Both must be aligned to 4 bytes boundary.  */

    ldr    r1, =__etext
    ldr    r2, =__data_start__
    ldr    r3, =__data_end__

    subs    r3, r3, r2
    ble     .LC0

.LC1:
    subs    r3, #4
    ldr     r0, [r1, r3]
    str     r0, [r2, r3]
    bgt     .LC1
.LC0:

/*     Loop to copy general data from read only memory to MainRAM.
 *      __DATA_ROM_GENERAL: Start of general data in ROM
 *      __data_general_start__/__data_general_end__: RAM address range in MainRAM */

    ldr    r1, =__DATA_ROM_GENERAL
    ldr    r2, =__data_general_start__
    ldr    r3, =__data_general_end__

    subs    r3, r3, r2
    ble     .LC0_GEN

.LC1_GEN:
    subs    r3, #4
    ldr     r0, [r1, r3]
    str     r0, [r2, r3]
    bgt     .LC1_GEN
.LC0_GEN:

/*     Loop to copy code from read only memory to RAM.
 *      __DATA_ROM_RX: Start of executable code on ROM to copy
 *      __LAUNCH_FROM_RAM_START/__LAUNCH_FROM_RAM_END: RAM address range that
 *      data should be copied to. Both must be aligned to 4 bytes boundary.  */

    ldr    r1, =__DATA_ROM_RX
    ldr    r2, =__LAUNCH_FROM_RAM_START
    ldr    r3, =__LAUNCH_FROM_RAM_END

    subs    r3, r3, r2
    ble     .L2C0

.L2C1:
    subs    r3, #4
    ldr     r0, [r1, r3]
    str     r0, [r2, r3]
    bgt     .L2C1
.L2C0:

/*     Zero out BSS [__bss_start__, __bss_end__), both aligned to 4 */
    ldr r1, =__bss_start__
    ldr r2, =__bss_end__

    subs    r2, r2, r1
    ble .LC3

    movs    r0, #0
.LC2:
    subs    r2, #4
    str     r0, [r1, r2]
    bgt     .LC2
.LC3:

/*     Zero out the MPU protected BSS section [__MPU_BSS_START, __MPU_BSS_END) */
    ldr r1, =__MPU_BSS_START
    ldr r2, =__MPU_BSS_END

    subs    r2, r2, r1
    ble .LC7

    movs    r0, #0
.LC6:
    subs    r2, #4
    str     r0, [r1, r2]
    bgt     .LC6
.LC7:

    bl    __libc_init_array

    bl    main
    .pool
    .size Reset_Handler, . - Reset_Handler


    .align  1
    .thumb_func
    .weak DefaultISR
    .type DefaultISR, %function
DefaultISR:
    /* PLAT-12: loop on a local label. `b DefaultISR` binds to the global symbol, and when the integration defines a
       strong DefaultISR (OpenBSW bspInterruptsImpl interrupt_manager.c) that is far away: the 11-bit Thumb-1 branch
       then does not reach it ("relocation truncated to fit: R_ARM_THM_JUMP11", first seen in the S32K116 build). */
.LDefaultISR_loop:
    b       .LDefaultISR_loop
    .size DefaultISR, . - DefaultISR

/* System handlers and external interrupt handlers: weak, DefaultISR unless the integration defines them */
    .weak NMI_Handler
    .thumb_set NMI_Handler, DefaultISR
    .weak HardFault_Handler
    .thumb_set HardFault_Handler, DefaultISR
    .weak SVC_Handler
    .thumb_set SVC_Handler, DefaultISR
    .weak PendSV_Handler
    .thumb_set PendSV_Handler, DefaultISR
    .weak SysTick_Handler
    .thumb_set SysTick_Handler, DefaultISR

#if !defined(NX_STARTUP_SAFE_ISR)
#undef NX_IRQ_RESERVED
#undef NX_IRQ_VECTOR
#define NX_IRQ_RESERVED()
#define NX_IRQ_VECTOR(handler) .weak handler ; .thumb_set handler, DefaultISR ;
#include "startup/nxIrqVectors.inc"
#endif

    .section .__startUpSection, "a"
    .thumb_func
    .align 2
    .globl   __start
__start:
    ldr r2, =Reset_Handler
    bx  r2
