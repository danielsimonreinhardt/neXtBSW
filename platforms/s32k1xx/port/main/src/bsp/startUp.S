/********************************************************************************
 * Copyright (c) 2026 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/


/*****************************************************************************/
/* Version: GCC for ARM Embedded Processors                                  */
/*****************************************************************************/
    .syntax unified
    .arch armv7-m

    .section .isr_vector, "a"
    .align 2
    .globl __isr_vector
__isr_vector:
    .long   __StackTop                                      /* Top of Stack */
    .long   Reset_Handler                                   /* Reset Handler */
    .long   DefaultISR                                      /* NMI_Handler                                        NMI Handler */
    .long   HardFault_Handler                               /* Hard Fault Handler */
    .long   DefaultISR                                      /* MemManage_Handler                                  MPU Fault Handler */
    .long   BusFault_Handler                                /* Bus Fault Handler */
    .long   UsageFault_Handler                              /* Usage Fault Handler */
    .long   0                                               /* Reserved */
    .long   0                                               /* Reserved */
    .long   0                                               /* Reserved */
    .long   0                                               /* Reserved */
    .long   SVC_Handler                                     /* SVCall Handler */
    .long   DefaultISR                                      /* DebugMon_Handler                                   Debug Monitor Handler */
    .long   0                                               /* Reserved */
    .long   PendSV_Handler                                  /* PendSV Handler */
    .long   Safe_SysTick_Handler                            /* SysTick Handler (SYS-170: SAFE_ISR wrapper) */

                                                            /* External Interrupts */
/* SYS-170 (SYS69-S1, finding SFP-F01): generated from the derivative's interrupt list (tools/bswgen,
   startup/nxIrqVectors.inc) instead of the hand-written list of every S32K148 vector. Every external vector goes
   through Safe_<handler>: the SAFE_ISR wrapper of safeLifecycle/IsrWrapper.cpp (generic list
   interrupts/nxSafeIsrVectors.h) closes the MPU gate of the protected RAM before the handler runs, so no interrupt
   that preempts the safety cycle writes into that RAM. The safety handlers keep their own Safe_<handler> (FTFC
   fault, ERM double fault, naked watchdog path of os/isr/isr_wdg.cpp, halt deadman of LPIT0 channel 1 as an alias
   of its handler in integration/Integration.cpp). Reserved numbers and the numbers behind the
   derivative's last vector stay on DefaultISR, the table keeps its 255 entries and the TRIM word. */
#define NX_IRQ_RESERVED()      .long DefaultISR ;
#define NX_IRQ_VECTOR(handler) .long Safe_##handler ;
#include "startup/nxIrqVectors.inc"
#undef NX_IRQ_RESERVED
#undef NX_IRQ_VECTOR

    .if (. - __isr_vector) > (255 * 4)
    .error "startup/nxIrqVectors.inc: more than 239 external interrupts"
    .endif
    .rept (255 * 4 - (. - __isr_vector)) / 4
    .long   DefaultISR
    .endr
    .long   0xFFFFFFFF                                      /*  Reserved for user TRIM value */

    .size    __isr_vector, . - __isr_vector

/* No Flash Configuration Field here: it belongs to the bootloader at 0x400. */

    .section .text // , "a" // has no effect for gcc compiler
    .equ _NVIC_ICER,  0xE000E180
    .equ _NVIC_ICPR,  0xE000E280
    .equ _NVIC_VTOR,  0xE000ED08
    .equ _CPACR,      0xE000ED88
    .text
    .thumb

/* Reset Handler */

    .thumb_func
    .align 2
    .globl   Reset_Handler
    .type    Reset_Handler, %function

Reset_Handler:

    cpsid   i               /* Mask interrupts */
    ldr r0, =_NVIC_VTOR     /* vector table is behind the bootloader */
    ldr r1, =__isr_vector
    str r1, [r0]
    ldr r0, =_NVIC_ICER    /* Disable interrupts and clear pending flags */
    ldr r1, =_NVIC_ICPR
    ldr r2, =0xFFFFFFFF
    str r2, [r0]            /* NVIC_ICER - clear enable IRQ register 0x00 */
    str r2, [r1]            /* NVIC_ICPR - clear pending IRQ register  0x00 */

    str r2, [r0,#4]!            /* NVIC_ICER - clear enable IRQ register 0x04 */
    str r2, [r1,#4]!            /* NVIC_ICPR - clear pending IRQ register 0x04 */

    str r2, [r0,#4]!            /* NVIC_ICER - clear enable IRQ register 0x08 */
    str r2, [r1,#4]!            /* NVIC_ICPR - clear pending IRQ register 0x08 */

    str r2, [r0,#4]!            /* NVIC_ICER - clear enable IRQ register 0x0c */
    str r2, [r1,#4]!            /* NVIC_ICPR - clear pending IRQ register 0x0c */

    str r2, [r0,#4]!            /* NVIC_ICER - clear enable IRQ register 0x10 */
    str r2, [r1,#4]!            /* NVIC_ICPR - clear pending IRQ register 0x10 */

    str r2, [r0,#4]!            /* NVIC_ICER - clear enable IRQ register 0x14 */
    str r2, [r1,#4]!            /* NVIC_ICPR - clear pending IRQ register 0x14 */

    str r2, [r0,#4]!            /* NVIC_ICER - clear enable IRQ register 0x18 */
    str r2, [r1,#4]!            /* NVIC_ICPR - clear pending IRQ register 0x18 */

    str r2, [r0,#4]!            /* NVIC_ICER - clear enable IRQ register 0x1c */
    str r2, [r1,#4]!            /* NVIC_ICPR - clear pending IRQ register 0x1c */

    /* enable fpu */
    ldr r0, = _CPACR
    ldr r1, [r0]
    orr r1, r1, #0x00F00000
    str r1, [r0]

    /* Init the rest of the registers */
    ldr     r1,=0
    ldr     r2,=0
    ldr     r3,=0
    ldr     r4,=0
    ldr     r5,=0
    ldr     r6,=0
    ldr     r7,=0
    mov     r8,r7
    mov     r9,r7
    mov     r10,r7
    mov     r11,r7
    mov     r12,r7
    /* Initialize the stack pointer */
    ldr     r0,=__StackTop
    mov     r13,r0
/* c code call stack init . */
    subs r0, 32
    str r1, [r0,r3]    /*0*/
    movs    r3, #4
    str r1, [r0,r3]    /*4*/
    add r0, r0, r3
    str r1, [r0,r3]    /*8*/
    add r0, r0, r3
    str r1, [r0,r3]    /*12*/
    add r0, r0, r3
    str r1, [r0,r3]    /*16*/
    add r0, r0, r3
    str r1, [r0,r3]    /*20*/
    add r0, r0, r3
    str r1, [r0,r3]    /*24*/
    add r0, r0, r3
    str r1, [r0,r3]    /*28*/

    bl boardInit

    cpsie   i               /* Unmask interrupts */

    /* Init ECC RAM */

    ldr r1, =__DATA_RAM
    ldr r2, =__DATA_RAM_END

    subs    r2, r1
    ble .LC5

    movs    r0, 0
    movs    r3, #4
.LC4:
    str r0, [r1]
    add r1, r1, r3
    subs r2, 4
    bgt .LC4
.LC5:

/*     Loop to copy data from read only memory to RAM. The ranges
 *      of copy from/to are specified by following symbols evaluated in
 *      linker script.
 *      __etext: End of code section, i.e., begin of data sections to copy from.
 *      __data_start__/__data_end__: RAM address range that data should be
 *      copied to. Both must be aligned to 4 bytes boundary.  */

    ldr    r1, =__etext
    ldr    r2, =__data_start__
    ldr    r3, =__data_end__

    subs    r3, r2
    ble     .LC0

.LC1:
    subs    r3, 4
    ldr    r0, [r1,r3]
    str    r0, [r2,r3]
    bgt    .LC1
.LC0:

/*     Loop to copy general data from read only memory to MainRAM (SRAM_U).
 *      __DATA_ROM_GENERAL: Start of general data in ROM
 *      __data_general_start__/__data_general_end__: RAM address range in MainRAM */

    ldr    r1, =__DATA_ROM_GENERAL
    ldr    r2, =__data_general_start__
    ldr    r3, =__data_general_end__

    subs    r3, r2
    ble     .LC0_GEN

.LC1_GEN:
    subs    r3, 4
    ldr    r0, [r1,r3]
    str    r0, [r2,r3]
    bgt    .LC1_GEN
.LC0_GEN:

/*     Loop to copy code from read only memory to RAM. The ranges
 *      of copy from/to are specified by following symbols evaluated in
 *      linker script.
 *      __DATA_ROM_RX: Start of executable code on ROM to copy i.e., begin
 *      of launchFromRAM section to copy from.
 *      __LAUNCH_FROM_RAM_START/__LAUNCH_FROM_RAM_END: RAM address range that
 *      data should be copied to. Both must be aligned to 4 bytes boundary.  */

    ldr    r1, =__DATA_ROM_RX
    ldr    r2, =__LAUNCH_FROM_RAM_START
    ldr    r3, =__LAUNCH_FROM_RAM_END

    subs    r3, r2
    ble     .L2C0

.L2C1:
    subs    r3, 4
    ldr    r0, [r1,r3]
    str    r0, [r2,r3]
    bgt    .L2C1
.L2C0:

/*     This part of work usually is done in C library startup code. Otherwise,
 *     define this macro to enable it in this startup.
 *
 *     Loop to zero out BSS section, which uses following symbols
 *     in linker script:
 *      __bss_start__: start of BSS section. Must align to 4
 *      __bss_end__: end of BSS section. Must align to 4
 *
 *     PLAT-21: the loops clear [start, end) - decrement the offset before the store. Before, the first store went
 *     to the word AT the end symbol, one word behind the section (__bss_end__ = __HeapBase, __MPU_BSS_END =
 *     __data_start__, whose initialized word was overwritten after the .data copy above).
 */
    ldr r1, =__bss_start__
    ldr r2, =__bss_end__

    subs    r2, r1
    ble .LC3

    movs    r0, 0
.LC2:
    subs    r2, 4
    str r0, [r1, r2]
    bgt .LC2
.LC3:

/*
 * Zero out the MPU protected BSS section [__MPU_BSS_START, __MPU_BSS_END)
 */
    ldr r1, =__MPU_BSS_START
    ldr r2, =__MPU_BSS_END

    subs    r2, r1
    ble .LC7

    movs    r0, 0
.LC6:
    subs    r2, 4
    str r0, [r1, r2]
    bgt .LC6
.LC7:

#ifndef __START
#define __START _start
#endif
	bl    __libc_init_array

    bl    main /*__START*/
    .pool
    .size Reset_Handler, . - Reset_Handler


    .align  1
    .thumb_func
    .weak DefaultISR
    .type DefaultISR, %function
DefaultISR:
    b       DefaultISR
    .size DefaultISR, . - DefaultISR

/* SYS-170: no weak handler stubs here any more (IO-17 LPUART, IO-32 LPIT0_Ch1, IO-07 PORTx, ARCH-07 CAN message
   buffers): every external vector points at its wrapper Safe_<handler>, and safeLifecycle/IsrWrapper.cpp defines
   the weak default (DefaultISR) of every handler the integration does not define. */

Exception:
     cpsid   i               /* Mask interrupts */
     b    Exception

    .section .__startUpSection, "a"
    .thumb_func
    .align 2
    .globl   __start
__start:
    ldr r2, =Reset_Handler
    bx  r2

.end
