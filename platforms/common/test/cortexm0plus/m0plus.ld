/* Synthetic memory map of the Cortex-M0+ build test (PLAT-10), derivative TESTM0P32 of the nxconfig test profiles:
 * 128 KB flash at 0, 16 KB RAM at 0x20000000. Provides the symbols of the ARMv6-M startup template and of the hard
 * fault handler (same names as the application linker script of the S32K1 board port). */

#include "hardFaultHandler/hardFaultDumpLayout.h"

MEMORY
{
    FLASH (rx)  : ORIGIN = 0x00000000, LENGTH = 0x00020000
    RAM   (rwx) : ORIGIN = 0x20000000, LENGTH = 0x00004000
}

ENTRY(Reset_Handler)

SECTIONS
{
    .isr_vector : { KEEP(*(.isr_vector)) } > FLASH
    .text :
    {
        KEEP(*(.__startUpSection))
        *(.text .text.*)
        *(.rodata .rodata.*)
        KEEP(*(.init)) KEEP(*(.fini))
        . = ALIGN(4);
        __init_array_start = .; KEEP(*(.init_array*)) __init_array_end = .;
        __preinit_array_start = .; KEEP(*(.preinit_array*)) __preinit_array_end = .;
        __fini_array_start = .; KEEP(*(.fini_array*)) __fini_array_end = .;
    } > FLASH
    .ARM.exidx : { *(.ARM.exidx*) } > FLASH
    . = ALIGN(4);
    __etext = .;
    __DATA_ROM_GENERAL = .;
    __DATA_ROM_RX = .;

    .data : AT(__etext)
    {
        __DATA_RAM = .;
        __data_start__ = .;
        *(.data .data.*)
        . = ALIGN(4);
        __data_end__ = .;
        __data_general_start__ = .;
        __data_general_end__ = .;
        __LAUNCH_FROM_RAM_START = .;
        __LAUNCH_FROM_RAM_END = .;
    } > RAM
    .bss (NOLOAD) :
    {
        __bss_start__ = .;
        *(.bss .bss.* COMMON)
        . = ALIGN(4);
        __bss_end__ = .;
        __MPU_BSS_START = .;
        __MPU_BSS_END = .;
        __DATA_RAM_END = .;
    } > RAM
    .noinit (NOLOAD) :
    {
        __hard_fault_dump_start = .;
        . += HARD_FAULT_DUMP_SIZE;
        __hard_fault_dump_end = .;
    } > RAM
    __StackTop = ORIGIN(RAM) + LENGTH(RAM);
    __hard_fault_ram_start = ORIGIN(RAM);
    __hard_fault_ram_end = ORIGIN(RAM) + LENGTH(RAM);
    end = __hard_fault_dump_end;
    _end = end;
}
