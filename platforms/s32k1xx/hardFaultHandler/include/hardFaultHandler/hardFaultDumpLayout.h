/* Hard fault dump layout (PLAT-20), the single source for the handler (assembler), the application linker
 * script (C preprocessor, reserves HARD_FAULT_DUMP_SIZE in .noinit) and the reader (C++).
 *
 * Only plain #defines with unsuffixed numbers: the file is included by GNU as and by linker scripts.
 * The location is not defined here: the linker script provides __hard_fault_dump_start/_end and the RAM
 * bounds __hard_fault_ram_start/_end used to check the stack pointer before the frame is read.
 *
 * Derived from the OpenBSW format (external/openbsw/platforms/s32k1xx/hardFaultHandler): same register
 * offsets up to DUMP_ORIGIN_PSR, then the ARMv7-M fault status registers (zero on cores without them),
 * fewer stack words, checksum and a magic word written last. */

#ifndef HARD_FAULT_DUMP_LAYOUT_H
#define HARD_FAULT_DUMP_LAYOUT_H

#define HARD_FAULT_DUMP_SIZE  0x144
#define HARD_FAULT_DUMP_MAGIC 0x48464431 /* "HFD1": layout version 1, cleared after the report */

/* Dump field offsets */
#define DUMP_HANDLER_PSR 0x000 /* xPSR at handler entry (exception number) */
#define DUMP_HANDLER_LR  0x004 /* EXC_RETURN */
#define DUMP_ORIGIN_R0   0x008
#define DUMP_ORIGIN_R1   0x00C
#define DUMP_ORIGIN_R2   0x010
#define DUMP_ORIGIN_R3   0x014
#define DUMP_ORIGIN_R4   0x018
#define DUMP_ORIGIN_R5   0x01C
#define DUMP_ORIGIN_R6   0x020
#define DUMP_ORIGIN_R7   0x024
#define DUMP_ORIGIN_R8   0x028
#define DUMP_ORIGIN_R9   0x02C
#define DUMP_ORIGIN_R10  0x030
#define DUMP_ORIGIN_R11  0x034
#define DUMP_ORIGIN_R12  0x038
#define DUMP_ORIGIN_SP   0x03C /* SP before the exception, raw SP if the frame was outside the RAM */
#define DUMP_ORIGIN_LR   0x040
#define DUMP_ORIGIN_PC   0x044
#define DUMP_ORIGIN_PSR  0x048
#define DUMP_CFSR        0x04C /* SCB->CFSR */
#define DUMP_HFSR        0x050 /* SCB->HFSR */
#define DUMP_MMFAR       0x054 /* SCB->MMFAR */
#define DUMP_BFAR        0x058 /* SCB->BFAR */
#define DUMP_FLAGS       0x05C /* DUMP_FLAG_* */
#define DUMP_STACK_START 0x060 /* stack words above the origin SP, as far as they lie in the RAM */
#define DUMP_STACK_END   0x13C
#define DUMP_CHECKSUM    0x13C /* sum of all words before it (modulo 2^32) */
#define DUMP_MAGIC       0x140

#define DUMP_FLAG_FRAME_VALID     0x1 /* stacked registers and stack words were read (SP inside the RAM) */
#define DUMP_FLAG_NO_FAULT_STATUS 0x2 /* core without CFSR/HFSR/MMFAR/BFAR (ARMv6-M): fields are 0, not valid */

#endif
