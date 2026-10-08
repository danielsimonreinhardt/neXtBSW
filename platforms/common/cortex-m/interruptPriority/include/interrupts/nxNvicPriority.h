/* Interrupt priorities as ranks on an NVIC, Cortex-M profile (PLAT-10, ARCH-24, ARCH-25,
 * platforms/common/interruptPriority/README.md). C and C++, header only, no hardware access. Included through interrupts/nxInterruptPriority.h, not directly.
 *
 * The configuration names a rank per interrupt instead of an NVIC value: rank 0 is the most urgent interrupt that may
 * call the RTOS (...FromISR), rank 1 the next one, and so on. The rank is mapped to the NVIC level
 *     level = NX_IRQ_MAX_SYSCALL_LEVEL + rank          (level 0 = highest priority, 2^bits - 1 = lowest)
 * so the same project runs on 2 priority bits (Cortex-M0+) and 4 bits (Cortex-M4F) without changing numbers, and a
 * rank the derivative cannot represent stops the build instead of wrapping to a higher priority.
 *
 * Inputs (defined before including this header, from the configuration):
 *   NX_CORE_ARCHITECTURE_*    architecture of the core (generated mcu/derivativeFacts.h, tools/nxconfig CORES):
 *                             ARMV6M, ARMV7M or ARMV8M. Not __ARM_ARCH: that is 7 for Cortex-M4F and Cortex-R4F
 *                             alike (finding ARCH-19); the compiler's profile is only cross-checked.
 *   NX_NVIC_PRIORITY_BITS     derivative.nvic_priority_bits (mcudb), 2..8
 *   NX_IRQ_MAX_SYSCALL_LEVEL  highest NVIC level (numerically lowest) allowed to call the RTOS. ARMv7-M/ARMv8-M
 *                             ports mask up to it with BASEPRI: 1..2^bits-1, levels below it are never masked
 *                             and must not call the RTOS. ARMv6-M has no BASEPRI, the kernel masks everything
 *                             (PRIMASK): must be 0.
 */

#ifndef NX_NVIC_PRIORITY_H
#define NX_NVIC_PRIORITY_H

#if !defined(NX_NVIC_PRIORITY_BITS) || !defined(NX_IRQ_MAX_SYSCALL_LEVEL)
#error "nxInterruptPriority.h needs NX_NVIC_PRIORITY_BITS (mcudb) and NX_IRQ_MAX_SYSCALL_LEVEL"
#endif

#if (NX_NVIC_PRIORITY_BITS < 2) || (NX_NVIC_PRIORITY_BITS > 8)
#error "NX_NVIC_PRIORITY_BITS must be 2..8"
#endif

/* The generated architecture decides; a compiler building for another profile (e.g. -mcpu=cortex-r4f with the
 * settings of a Cortex-M derivative) is a configuration error, not a reason to pick another branch. */
#if defined(__ARM_ARCH_PROFILE) && (__ARM_ARCH_PROFILE != 'M')
#error "NVIC interrupt priorities need a Cortex-M compiler profile (__ARM_ARCH_PROFILE is not 'M')"
#endif

/* Number of NVIC levels and the lowest one (kernel: PendSV, SysTick) */
#define NX_IRQ_LEVEL_COUNT  (1U << NX_NVIC_PRIORITY_BITS)
#define NX_IRQ_LOWEST_LEVEL (NX_IRQ_LEVEL_COUNT - 1U)

#if defined(NX_CORE_ARCHITECTURE_ARMV6M)
#if (NX_IRQ_MAX_SYSCALL_LEVEL != 0)
#error "ARMv6-M has no BASEPRI: NX_IRQ_MAX_SYSCALL_LEVEL must be 0 (the kernel masks all interrupts)"
#endif
#elif defined(NX_CORE_ARCHITECTURE_ARMV7M) || defined(NX_CORE_ARCHITECTURE_ARMV8M)
#if (NX_IRQ_MAX_SYSCALL_LEVEL < 1) || (NX_IRQ_MAX_SYSCALL_LEVEL > ((1 << NX_NVIC_PRIORITY_BITS) - 1))
#error "NX_IRQ_MAX_SYSCALL_LEVEL must be 1..2^NX_NVIC_PRIORITY_BITS-1 (0 disables the BASEPRI masking)"
#endif
#else
#error "NVIC interrupt priorities are for the Cortex-M profile: NX_CORE_ARCHITECTURE_ARMV6M, ARMV7M or ARMV8M"
#endif

/* The CMSIS NVIC functions shift by __NVIC_PRIO_BITS: a different value in the device header (S32K116.h: 4, the core
 * has 2) would turn low priorities into high ones. */
#if defined(__NVIC_PRIO_BITS) && (__NVIC_PRIO_BITS != NX_NVIC_PRIORITY_BITS)
#error "__NVIC_PRIO_BITS of the device header differs from NX_NVIC_PRIORITY_BITS (mcudb nvic_priority_bits)"
#endif

/* Ranks available to interrupts that call the RTOS */
#define NX_IRQ_OS_RANK_COUNT (NX_IRQ_LEVEL_COUNT - NX_IRQ_MAX_SYSCALL_LEVEL)

#define NX_IRQ_OS_RANK_VALID(rank) (((unsigned)(rank)) < NX_IRQ_OS_RANK_COUNT)

/* NVIC level (for NVIC_SetPriority/SYS_SetPriority) of an RTOS-aware interrupt with the given rank. A constant rank
 * outside the available ranks is a compile error (negative array size). */
#define NX_IRQ_OS_LEVEL(rank)                                                                  \
    ((unsigned)(sizeof(char[NX_IRQ_OS_RANK_VALID(rank) ? 1 : -1]) * 0U)                        \
     + NX_IRQ_MAX_SYSCALL_LEVEL + (unsigned)(rank))

/* Values for FreeRTOSConfig.h (8-bit register values as the Cortex-M ports expect them) */
#define NX_FREERTOS_KERNEL_INTERRUPT_PRIORITY      (NX_IRQ_LOWEST_LEVEL << (8U - NX_NVIC_PRIORITY_BITS))
#define NX_FREERTOS_MAX_SYSCALL_INTERRUPT_PRIORITY (NX_IRQ_MAX_SYSCALL_LEVEL << (8U - NX_NVIC_PRIORITY_BITS))

#endif /* NX_NVIC_PRIORITY_H */
