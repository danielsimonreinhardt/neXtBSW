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

/* Modified for neXtBSW (PLAT-10): ARMv6-M (Cortex-M0/M0+) variant of ../armv7m/hardFaultHandler.S (PLAT-20), which
 * is derived from external/openbsw/platforms/s32k1xx/hardFaultHandler/src/hardFaultHandler.s. Same dump layout
 * (hardFaultHandler/hardFaultDumpLayout.h), same linker symbols, same reader. Differences to ARMv7-M:
 * - Thumb-1 instruction set only: no IT blocks, no post-index addressing, no 32-bit data processing,
 *   immediates of at most 8 bits, str/ldr offsets only with low registers (R8-R11 copied to R4 first)
 * - no fault status registers (CFSR, HFSR, MMFAR, BFAR): the fields stay 0 and DUMP_FLAG_NO_FAULT_STATUS is set
 * - no FP extended frame; the alignment padding (stacked xPSR bit 9) exists as on ARMv7-M
 * - the final handler is reached with bx (b reaches only +-2 KB)
 * Same safe outputs loop before the dump as ARMv7-M (IO-32, hardFaultHandler/safeOutputs.h), Thumb-1 only. */

#include "hardFaultHandler/hardFaultDumpLayout.h"
#include "hardFaultHandler/safeOutputs.h"

.syntax unified
.arch armv6s-m
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
.equ PSR_STACK_ALIGN,   0x200   // stacked xPSR bit 9: 4 bytes padding above the frame
.equ EXC_RETURN_PSP,    0x4     // EXC_RETURN bit 2: frame on the process stack



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

    // Save flags for dump in advance (R12 is stacked, free to use)
    mrs     R12, XPSR

    // IO-32: safe outputs first - switch the power outputs off before dump and UART output (a watchdog handler that
    // prints for milliseconds would otherwise leave the bridge switching). Table nxSafeOutputs (layout
    // hardFaultHandler/safeOutputs.h, generated per project, weak empty default below): R0-R3 only (stacked by
    // the exception entry), no stack, no RAM; an entry whose peripheral clock is gated is skipped.
#if defined(NX_SAFE_OUTPUT_SELECT)
    // SYS-116: extended table nxSafeOutputsSelect, same rule as ARMv7-M (safeOutputs.h): one read of the group's
    // selection word per entry of a group, the short value only for exactly the short's pattern with the group's
    // timer clock on, the freewheel value otherwise. Thumb-1: register offset without shift, hence lsls first.
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
    movs    R0, #0
    mov     R1, R2
    ldr     R3, =__hard_fault_dump_start + HARD_FAULT_DUMP_SIZE
    1:
    str     R0, [R1]
    adds    R1, R1, #4
    cmp     R1, R3
    bne     1b

    // Save values of registers which are not framed; R4-R7 are free afterwards
    str     R4,  [R2, #DUMP_ORIGIN_R4]
    str     R5,  [R2, #DUMP_ORIGIN_R5]
    str     R6,  [R2, #DUMP_ORIGIN_R6]
    str     R7,  [R2, #DUMP_ORIGIN_R7]
    mov     R4, R8
    str     R4,  [R2, #DUMP_ORIGIN_R8]
    mov     R4, R9
    str     R4,  [R2, #DUMP_ORIGIN_R9]
    mov     R4, R10
    str     R4,  [R2, #DUMP_ORIGIN_R10]
    mov     R4, R11
    str     R4,  [R2, #DUMP_ORIGIN_R11]

    // Save PSR and LR
    mov     R4, R12
    str     R4,  [R2, #DUMP_HANDLER_PSR]
    mov     R5, LR
    str     R5,  [R2, #DUMP_HANDLER_LR]

    // No fault status registers on ARMv6-M: fields stay 0
    movs    R6, #DUMP_FLAG_NO_FAULT_STATUS

    // Analyze exception return code to find the stack which holds the frame (R5 = EXC_RETURN)
    movs    R0, #EXC_RETURN_PSP
    tst     R5, R0
    bne     2f
    mrs     R1, MSP
    b       3f
    2:
    mrs     R1, PSP
    3:

    // A corrupted stack pointer is a common fault cause: read the frame only if it lies in the RAM
    ldr     R0, =__hard_fault_ram_start
    cmp     R1, R0
    blo     .Lframe_invalid
    ldr     R0, =__hard_fault_ram_end - FRAME_SIZE
    cmp     R1, R0
    bhi     .Lframe_invalid

    movs    R0, #DUMP_FLAG_FRAME_VALID
    orrs    R6, R0
    str     R6, [R2, #DUMP_FLAGS]

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

    // SP before the exception: frame size and alignment padding (R0 = stacked xPSR)
    adds    R1, R1, #FRAME_SIZE
    ldr     R3, =PSR_STACK_ALIGN
    tst     R0, R3
    beq     4f
    adds    R1, R1, #4
    4:
    str     R1, [R2, #DUMP_ORIGIN_SP]

    // Add some stack contents to dump, as long as they lie in the RAM
    ldr     R4, =__hard_fault_ram_end
    ldr     R3, =__hard_fault_dump_start + DUMP_STACK_END
    adds    R2, R2, #DUMP_STACK_START
    1:
    cmp     R1, R4
    bhs     .Lchecksum
    ldr     R0, [R1]
    adds    R1, R1, #4
    str     R0, [R2]
    adds    R2, R2, #4
    cmp     R2, R3
    bne     1b
    b       .Lchecksum

.Lframe_invalid:
    // Raw stack pointer only, stacked registers and stack words stay zero
    str     R6, [R2, #DUMP_FLAGS]
    str     R1, [R2, #DUMP_ORIGIN_SP]

.Lchecksum:
    // Calculate and save checksum
    movs    R1, #0
    ldr     R2, =__hard_fault_dump_start
    ldr     R3, =__hard_fault_dump_start + DUMP_CHECKSUM
    1:
    ldr     R0, [R2]
    adds    R1, R1, R0
    adds    R2, R2, #4
    cmp     R2, R3
    bne     1b
    str     R1, [R3]

    // Magic last: marks the dump complete
    ldr     R0, =HARD_FAULT_DUMP_MAGIC
    ldr     R2, =__hard_fault_dump_start + DUMP_MAGIC
    str     R0, [R2]

    // Complete HardFault handling (LR still holds EXC_RETURN)
    ldr     R0, =HardFault_Handler_Final
    bx      R0

.pool
.size customHardFaultHandler, . - customHardFaultHandler

// IO-32: empty safe outputs table, used when the application defines none (strong symbol of the board port)
.section .rodata.nxSafeOutputsDefault, "a"
.balign 4
.weak nxSafeOutputs
.type nxSafeOutputs, %object
nxSafeOutputs:
    .word   0, 0, 0, 0
.size nxSafeOutputs, . - nxSafeOutputs
