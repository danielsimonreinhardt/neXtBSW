/* Bootloader linker script, family s32k1xx: vector table and flash configuration field at the start of the code
 * flash, code up to the end of the bootloader area, RAM of the bootloader only (the rest stays untouched for the
 * application). Addresses and sizes: generated bl_memory.ld (project memory, tools/bswgen/blgen.py). */

INCLUDE bl_memory.ld

ENTRY(Reset_Handler)

MEMORY
{
  VECTORS (rx)  : ORIGIN = BL_FLASH_ORIGIN, LENGTH = 0x00000400
  FCF     (rx)  : ORIGIN = BL_FLASH_ORIGIN + 0x00000400, LENGTH = 0x00000010
  FLASH   (rx)  : ORIGIN = BL_FLASH_ORIGIN + 0x00000410, LENGTH = BL_AREA_SIZE - 0x00000410
  RAM     (rwx) : ORIGIN = BL_RAM_ORIGIN, LENGTH = BL_RAM_LENGTH
}

STACK_SIZE = BL_STACK_SIZE;

SECTIONS
{
  .isr_vector : { KEEP(*(.isr_vector)) } > VECTORS

  /* BL-30: generated per core (tools/bswgen/blgen.py): with an ARMv6-M vector table (48 entries) the section
   * .text_gap takes code up to the FCF, with ARMv7-M (256 entries) the file has no statement, the image is unchanged */
  INCLUDE bl_sections.ld

  .flashconfig : { KEEP(*(.FlashConfig)) } > FCF

  .text :
  {
    /* BL-32: objects of the bootloader in link-line order, members of libraries (newlib, libgcc) sorted by section
     * name. The order in which ld pulls archive members depends on the member order inside the archive, i.e. on
     * the toolchain build (NXP GCC 11.4 and xPack GCC 11.3.1 swap memset/memcpy with identical code); sorted,
     * the image depends only on the code. */
    *(EXCLUDE_FILE(*.a:*) .text*)
    *.a:*(SORT_BY_NAME(.text*))
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

  /* BL-03: state of the security access that must survive a reset (seed counter and pool, failed attempts);
   * not cleared by the startup code, checked by its CRC in src/core/seedkey.c */
  .noinit (NOLOAD) :
  {
    . = ALIGN(4);
    *(.noinit*)
    . = ALIGN(4);
    __noinit_end = .;
  } > RAM

  __StackTop   = ORIGIN(RAM) + LENGTH(RAM);
  __StackLimit = __StackTop - STACK_SIZE;
  ASSERT(__noinit_end <= __StackLimit, "bootloader RAM overflow")
  ASSERT(__data_load + (__data_end - __data_start) <= BL_FLASH_ORIGIN + BL_AREA_SIZE, "bootloader flash overflow")
  ASSERT(BL_FLASH_ORIGIN + BL_AREA_SIZE == BL_APP_ORIGIN, "bootloader area must end at the application start")
  /* BL-30: vectors checked by the updater (reset, NMI, HardFault) point behind the FCF, not into .text_gap */
  ASSERT(Reset_Handler >= ORIGIN(FLASH) && Fault_Handler >= ORIGIN(FLASH), "vector handler in front of the FCF")
  /* NVM-15: this image erases sector 0 when it is flashed; without the whole field FSEC reads 0xFF (secured) after
   * the next reset. The value itself is checked after the link (tools/elfcheck fcf, bootloader/CMakeLists.txt). */
  ASSERT(SIZEOF(.flashconfig) == 0x10, "flash configuration field (.FlashConfig, 16 bytes at 0x400) missing")
}
