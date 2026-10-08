/* PLAT-31: one startup for every Cortex-M core of the family. No .arch - the architecture comes from -mcpu (like
 * the application's bootHeader.S), and the code uses only the Thumb-1 subset (ARMv6-M): 16-bit instructions except
 * bl. The former `.arch armv7-m` with post-indexed ldr.w/str.w linked the S32K116 bootloader as v7 and faulted on
 * the Cortex-M0+ before main. tools/elfcheck/test/test_elfcheck.py checks the linked S32K116 images for it. */
    .syntax unified

    .section .isr_vector, "a"
    .align 2
    .globl __isr_vector
__isr_vector:
    .long   __StackTop
    .long   Reset_Handler
    .long   Fault_Handler       /* NMI */
    .long   Fault_Handler       /* HardFault */
    .long   Fault_Handler       /* MemManage */
    .long   Fault_Handler       /* BusFault */
    .long   Fault_Handler       /* UsageFault */
    .long   0
    .long   0
    .long   0
    .long   0
    .long   Fault_Handler       /* SVC */
    .long   Fault_Handler       /* DebugMon */
    .long   0
    .long   Fault_Handler       /* PendSV */
    .long   SysTick_Handler
    /* External interrupts are not used. BL-30: the table has the architectural maximum of the core, ARMv6-M ends
     * after 32 entries; linker.ld fills the rest of the first KB up to the FCF with code (.text_gap). */
#if defined(__ARM_ARCH_6M__)
    .rept   32
#else
    .rept   240
#endif
    .long   Fault_Handler
    .endr

/* Flash Configuration Field at 0x400: unsecured, NXP defaults */
    .section .FlashConfig, "a"
    .long   0xFFFFFFFF
    .long   0xFFFFFFFF
    .long   0xFFFFFFFF
    .long   0xFFFF7FFE

    .text
    .thumb
    .thumb_func
    .align 2
    .globl  Reset_Handler
    .type   Reset_Handler, %function
Reset_Handler:
    cpsid   i
    bl      bl_system_init      /* watchdog off; must not use initialized data */

    ldr     r0, =__data_load
    ldr     r1, =__data_start
    ldr     r2, =__data_end
copy_data:
    cmp     r1, r2
    bhs     zero_bss
    ldmia   r0!, {r3}           /* 16-bit with write-back on ARMv6-M and ARMv7-M */
    stmia   r1!, {r3}
    b       copy_data
zero_bss:
    ldr     r1, =__bss_start
    ldr     r2, =__bss_end
    movs    r3, #0
zero_loop:
    cmp     r1, r2
    bhs     start_main
    stmia   r1!, {r3}
    b       zero_loop
start_main:
    cpsie   i
    bl      main
    b       .
    .pool
    .size   Reset_Handler, . - Reset_Handler
