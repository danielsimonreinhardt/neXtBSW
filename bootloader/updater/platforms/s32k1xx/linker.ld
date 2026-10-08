/* Bootloader updater, family s32k1xx: linked like an application (vector table at the application start, no flash
 * configuration field), RAM of the bootloader. Addresses and sizes: generated bl_memory.ld (project memory). */

INCLUDE bl_memory.ld

ENTRY(Reset_Handler)

MEMORY
{
  VECTORS (rx)  : ORIGIN = BL_APP_ORIGIN, LENGTH = 0x00000400
  FLASH   (rx)  : ORIGIN = BL_APP_ORIGIN + 0x00000400, LENGTH = BL_APP_LIMIT - (BL_APP_ORIGIN + 0x00000400)
  RAM     (rwx) : ORIGIN = BL_RAM_ORIGIN, LENGTH = BL_RAM_LENGTH
}

STACK_SIZE = BL_STACK_SIZE;

SECTIONS
{
  .isr_vector : { KEEP(*(.isr_vector)) } > VECTORS

  /* startup.S of the bootloader is reused; the FCF belongs to sector 0 only */
  /DISCARD/ : { *(.FlashConfig) }

  .text :
  {
    *(.text*)
    *(.rodata*)
    . = ALIGN(4);
  } > FLASH

  .ARM.exidx : { *(.ARM.exidx*) } > FLASH

  .data :
  {
    . = ALIGN(4);
    __data_start = .;
    *(.ramfunc*)
    *(.data*)
    . = ALIGN(4);
    __data_end = .;
  } > RAM AT > FLASH
  __data_load = LOADADDR(.data);

  .bss (NOLOAD) :
  {
    . = ALIGN(4);
    __bss_start = .;
    *(.bss*)
    *(COMMON)
    . = ALIGN(4);
    __bss_end = .;
  } > RAM

  __StackTop   = ORIGIN(RAM) + LENGTH(RAM);
  __StackLimit = __StackTop - STACK_SIZE;
  ASSERT(__bss_end <= __StackLimit, "updater RAM overflow")
  ASSERT(__data_load + (__data_end - __data_start) <= BL_APP_LIMIT, "updater flash overflow")
}
