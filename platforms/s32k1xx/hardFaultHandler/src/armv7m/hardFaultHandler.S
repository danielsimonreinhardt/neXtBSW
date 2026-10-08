/********************************************************************************
 * Copyright (c) 2024 Accenture
 * Copyright (c) 2025 BMW AG
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

/* Modified for neXtBSW (PLAT-20), derived from
 * external/openbsw/platforms/s32k1xx/hardFaultHandler/src/hardFaultHandler.s:
 * - dump location from the linker script (__hard_fault_dump_start) instead of .equ 0x2000EC00, which lies
 *   outside the SRAM of the S32K144; layout in hardFaultHandler/hardFaultDumpLayout.h
 * - stack pointer checked against the RAM (__hard_fault_ram_start/_end) before the frame is read,
 *   stack words only copied while inside the RAM
 * - SP before the exception includes the FP extended frame and the alignment padding
 * - fault status registers, flags, magic word written last (the dump is cleared first)
 * - IO-32: before the dump, the safe outputs table switches the power outputs off (hardFaultHandler/safeOutputs.h) */

#include "hardFaultHandler/hardFaultDumpLayout.h"
#include "hardFaultHandler/safeOutputs.h"

.syntax unified
.arch armv7-m
.thumb

// Stack frame offsets
.equ FRAME_R0,          0x00
.equ FRAME_R1,          0x04
.equ FRAME_R2,          0x08
.equ FRAME_R3,          0x0C
.equ FRAME_R12,         0x10
.equ FRAME_LR,          0x14
.equ FRAME_PC,          0x18
.equ FRAME_PSR,         0x1C
.equ FRAME_SIZE,        0x20
.equ FRAME_FP_EXTRA,    0x48    // extended frame (EXC_RETURN bit 4 clear): 0x68 bytes
.equ PSR_STACK_ALIGN,   0x200   // stacked xPSR bit 9: 4 bytes padding above the frame

// System control block
.equ SCB_CFSR,          0xE000ED28
.equ SCB_HFSR_OFFSET,   0x04
.equ SCB_MMFAR_OFFSET,  0x0C
.equ SCB_BFAR_OFFSET,   0x10



//------------------------------------------------------------------------------
// WARNING:
// R4-R11 must not be used before they are written to the dump, since they are
// not saved in stack. Stack must not be used. LR must still hold EXC_RETURN:
// branch here directly from the exception vector (no function call before).
//------------------------------------------------------------------------------

.text
.globl customHardFaultHandler
.type customHardFaultHandler, %function
.thumb_func

customHardFaultHandler:

    // Save flags for dump in advance
    mrs     R12, XPSR

    // IO-32: safe outputs first - switch the power outputs off before dump and UART output (a watchdog handler that
    // prints for milliseconds would otherwise leave the bridge switching). Table nxSafeOutputs (layout
    // hardFaultHandler/safeOutputs.h, generated per project, weak empty default below): R0-R3 only (stacked by
    // the exception entry), no stack, no RAM; an entry whose peripheral clock is gated is skipped.
#if defined(NX_SAFE_OUTPUT_SELECT)
    // SYS-116: extended table nxSafeOutputsSelect - per entry of a PWM group with an active short the selection word
    // of the group is read (one word of ordinary RAM, no RAM write) and the short value written only for exactly the
    // short's pattern with the group's timer clock on; anything else writes the freewheel value (safeOutputs.h).
    ldr     R0, =nxSafeOutputsSelect
    5:
    ldr     R1, [R0, #NX_SAFE_SELECT_ADDRESS]
    cmp     R1, #0
    beq     8f
    ldr     R2, [R0, #NX_SAFE_SELECT_GATE_ADDRESS]
    cmp     R2, #0
    beq     6f
    ldr     R2, [R2]
    ldr     R3, [R0, #NX_SAFE_SELECT_GATE_MASK]
    tst     R2, R3
    beq     7f
    6:
    ldr     R2, [R0, #NX_SAFE_SELECT_GROUP]
    cmp     R2, #0
    beq     9f
    ldr     R2, [R0, #NX_SAFE_SELECT_GROUP_GATE]
    cmp     R2, #0
    beq     9f
    ldr     R2, [R2]
    ldr     R3, [R0, #NX_SAFE_SELECT_GROUP_GATE_MASK]
    tst     R2, R3
    beq     9f
    ldr     R2, [R0, #NX_SAFE_SELECT_GROUP]
    subs    R2, #1
    lsls    R2, R2, #2
    ldr     R3, =nxSafeStateSelection
    ldr     R3, [R3, R2]
    ldr     R2, =NX_SAFE_STATE_WORD_SHORT_LOW
    cmp     R3, R2
    bne     10f
    ldr     R2, [R0, #NX_SAFE_SELECT_SHORT_LOW]
    b       11f
    10:
    ldr     R2, =NX_SAFE_STATE_WORD_SHORT_HIGH
    cmp     R3, R2
    bne     9f
    ldr     R2, [R0, #NX_SAFE_SELECT_SHORT_HIGH]
    b       11f
    9:
    ldr     R2, [R0, #NX_SAFE_SELECT_VALUE]
    11:
    str     R2, [R1]
    7:
    adds    R0, #NX_SAFE_SELECT_SIZE
    b       5b
    8:
    dsb
#else
    ldr     R0, =nxSafeOutputs
    5:
    ldr     R1, [R0, #NX_SAFE_OUTPUT_ADDRESS]
    cmp     R1, #0
    beq     8f
    ldr     R2, [R0, #NX_SAFE_OUTPUT_GATE_ADDRESS]
    cmp     R2, #0
    beq     6f
    ldr     R2, [R2]
    ldr     R3, [R0, #NX_SAFE_OUTPUT_GATE_MASK]
    tst     R2, R3
    beq     7f
    6:
    ldr     R2, [R0, #NX_SAFE_OUTPUT_VALUE]
    str     R2, [R1]
    7:
    adds    R0, #NX_SAFE_OUTPUT_SIZE
    b       5b
    8:
    dsb
#endif

    // Clear the whole dump including the magic: a partly written dump is never valid
    ldr     R2, =__hard_fault_dump_start
    mov     R0, 0
    mov     R1, R2
    add     R3, R2, HARD_FAULT_DUMP_SIZE
    1:
    str     R0, [R1], 4
    cmp     R1, R3
    bne     1b

    // Save PSR and LR
    str     R12, [R2, #DUMP_HANDLER_PSR]
    str     LR,  [R2, #DUMP_HANDLER_LR]

    // Save values of registers which are not framed
    str     R4,  [R2, #DUMP_ORIGIN_R4]
    str     R5,  [R2, #DUMP_ORIGIN_R5]
    str     R6,  [R2, #DUMP_ORIGIN_R6]
    str     R7,  [R2, #DUMP_ORIGIN_R7]
    str     R8,  [R2, #DUMP_ORIGIN_R8]
    str     R9,  [R2, #DUMP_ORIGIN_R9]
    str     R10, [R2, #DUMP_ORIGIN_R10]
    str     R11, [R2, #DUMP_ORIGIN_R11]

    // Fault status registers (lost on the software reset that follows)
    ldr     R3, =SCB_CFSR
    ldr     R0, [R3]
    str     R0, [R2, #DUMP_CFSR]
    ldr     R0, [R3, #SCB_HFSR_OFFSET]
    str     R0, [R2, #DUMP_HFSR]
    ldr     R0, [R3, #SCB_MMFAR_OFFSET]
    str     R0, [R2, #DUMP_MMFAR]
    ldr     R0, [R3, #SCB_BFAR_OFFSET]
    str     R0, [R2, #DUMP_BFAR]

    // Analyze exception return code to find the stack which holds the frame
    tst     LR, 4
    ite     eq
    mrseq   R1, MSP
    mrsne   R1, PSP

    // A corrupted stack pointer is a common fault cause: read the frame only if it lies in the RAM
    ldr     R0, =__hard_fault_ram_start
    cmp     R1, R0
    blo     .Lframe_invalid
    ldr     R0, =__hard_fault_ram_end - FRAME_SIZE
    cmp     R1, R0
    bhi     .Lframe_invalid

    mov     R0, DUMP_FLAG_FRAME_VALID
    str     R0, [R2, #DUMP_FLAGS]

    // Save value of registers which are framed
    ldr     R0, [R1, #FRAME_R0]
    str     R0, [R2, #DUMP_ORIGIN_R0]
    ldr     R0, [R1, #FRAME_R1]
    str     R0, [R2, #DUMP_ORIGIN_R1]
    ldr     R0, [R1, #FRAME_R2]
    str     R0, [R2, #DUMP_ORIGIN_R2]
    ldr     R0, [R1, #FRAME_R3]
    str     R0, [R2, #DUMP_ORIGIN_R3]
    ldr     R0, [R1, #FRAME_R12]
    str     R0, [R2, #DUMP_ORIGIN_R12]
    ldr     R0, [R1, #FRAME_LR]
    str     R0, [R2, #DUMP_ORIGIN_LR]
    ldr     R0, [R1, #FRAME_PC]
    str     R0, [R2, #DUMP_ORIGIN_PC]
    ldr     R0, [R1, #FRAME_PSR]
    str     R0, [R2, #DUMP_ORIGIN_PSR]

    // SP before the exception: frame size, FP extended frame, alignment padding (R0 = stacked xPSR)
    mov     R3, FRAME_SIZE
    tst     LR, 0x10
    it      eq
    addeq   R3, R3, FRAME_FP_EXTRA
    tst     R0, PSR_STACK_ALIGN
    it      ne
    addne   R3, R3, 4
    add     R1, R1, R3
    str     R1, [R2, #DUMP_ORIGIN_SP]

    // Add some stack contents to dump, as long as they lie in the RAM
    ldr     R12, =__hard_fault_ram_end
    add     R3, R2, DUMP_STACK_END
    add     R2, R2, DUMP_STACK_START
    1:
    cmp     R1, R12
    bhs     .Lchecksum
    ldr     R0, [R1], 4
    str     R0, [R2], 4
    cmp     R2, R3
    bne     1b
    b       .Lchecksum

.Lframe_invalid:
    // Raw stack pointer only, stacked registers and stack words stay zero
    str     R1, [R2, #DUMP_ORIGIN_SP]

.Lchecksum:
    // Calculate and save checksum
    mov     R1, 0
    ldr     R2, =__hard_fault_dump_start
    add     R3, R2, DUMP_CHECKSUM
    1:
    ldr     R0, [R2], 4
    add     R1, R0
    cmp     R2, R3
    bne     1b
    str     R1, [R3]

    // Magic last: marks the dump complete
    ldr     R0, =HARD_FAULT_DUMP_MAGIC
    ldr     R2, =__hard_fault_dump_start
    str     R0, [R2, #DUMP_MAGIC]

    // Complete HardFault handling
    b       HardFault_Handler_Final

.size customHardFaultHandler, . - customHardFaultHandler

// IO-32: empty safe outputs table, used when the application defines none (strong symbol of the board port)
.section .rodata.nxSafeOutputsDefault, "a"
.balign 4
.weak nxSafeOutputs
.type nxSafeOutputs, %object
nxSafeOutputs:
    .word   0, 0, 0, 0
.size nxSafeOutputs, . - nxSafeOutputs
