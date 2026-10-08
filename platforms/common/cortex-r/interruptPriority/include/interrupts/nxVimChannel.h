/* Interrupt ranks on a vectored interrupt manager (VIM), Cortex-R profile (ARCH-24, ARCH-25,
 * platforms/common/interruptPriority/README.md). C and C++, header only, no hardware access. Included through interrupts/nxInterruptPriority.h, not directly.
 *
 * The VIM has no priority levels and no priority mask: the priority of a request is the number of the channel it is
 * mapped to (channel 0 highest), and the core masks IRQ and FIQ as a whole (CPSR I/F). So a rank of the project does
 * not become a level but a position: the ranked interrupts, most urgent first, take the channels the derivative does
 * not fix (tools/nxconfig checks.vim_channel_order). Every ranked interrupt is an IRQ and may call the RTOS; a FIQ
 * never may and is no rank. There is no syscall level.
 *
 * Inputs (generated mcu/derivativeFacts.h, mcudb vim):
 *   NX_CORE_ARCHITECTURE_ARMV7R  architecture of the core (tools/nxconfig CORES), not __ARM_ARCH (finding ARCH-19)
 *   NX_VIM_CHANNEL_COUNT         vim.channels, 2..128 (the fixed channels are given in four 32-bit words)
 *   NX_VIM_FIXED_CHANNEL_COUNT   number of vim.fixed entries
 *   NX_VIM_FIXED_MASK0..3        vim.fixed[].channel as bits: word n holds channels 32n..32n+31
 * Output macros: NX_IRQ_OS_CHANNEL_COUNT, NX_VIM_CHANNEL_IS_FIXED(channel), NX_IRQ_OS_CHANNEL(channel). The
 * generator writes one NX_IRQ_CHANNEL_<SOURCE>_<NAME> = NX_IRQ_OS_CHANNEL(<channel>) per rank.
 */

#ifndef NX_VIM_CHANNEL_H
#define NX_VIM_CHANNEL_H

#if !defined(NX_CORE_ARCHITECTURE_ARMV7R)
#error "VIM interrupt channels are for the Cortex-R profile: NX_CORE_ARCHITECTURE_ARMV7R"
#endif

#if defined(__ARM_ARCH_PROFILE) && (__ARM_ARCH_PROFILE != 'R')
#error "VIM interrupt channels need a Cortex-R compiler profile (__ARM_ARCH_PROFILE is not 'R')"
#endif

/* NVIC inputs have no meaning here; a leftover of a Cortex-M configuration must not be silently ignored. */
#if defined(NX_NVIC_PRIORITY_BITS)
#error "a VIM core has no NVIC priority bits: NX_NVIC_PRIORITY_BITS must not be defined"
#endif
#if defined(NX_IRQ_MAX_SYSCALL_LEVEL) && (NX_IRQ_MAX_SYSCALL_LEVEL != 0)
#error "a VIM core has no syscall level (IRQ may call the RTOS, FIQ never): NX_IRQ_MAX_SYSCALL_LEVEL must be 0 or undefined"
#endif

#if !defined(NX_VIM_CHANNEL_COUNT) || !defined(NX_VIM_FIXED_CHANNEL_COUNT) || !defined(NX_VIM_FIXED_MASK0)   \
    || !defined(NX_VIM_FIXED_MASK1) || !defined(NX_VIM_FIXED_MASK2) || !defined(NX_VIM_FIXED_MASK3)
#error "nxInterruptPriority.h on a VIM core needs NX_VIM_CHANNEL_COUNT, NX_VIM_FIXED_CHANNEL_COUNT and NX_VIM_FIXED_MASK0..3 (mcudb vim)"
#endif

#if (NX_VIM_CHANNEL_COUNT < 2) || (NX_VIM_CHANNEL_COUNT > 128)
#error "NX_VIM_CHANNEL_COUNT must be 2..128"
#endif

#if (NX_VIM_FIXED_CHANNEL_COUNT >= NX_VIM_CHANNEL_COUNT)
#error "NX_VIM_FIXED_CHANNEL_COUNT leaves no channel for a ranked interrupt"
#endif

/* Channels a ranked interrupt may take */
#define NX_IRQ_OS_CHANNEL_COUNT ((unsigned)NX_VIM_CHANNEL_COUNT - (unsigned)NX_VIM_FIXED_CHANNEL_COUNT)

#define NX_VIM_FIXED_MASK(word)                                                                                  \
    ((unsigned long)(((word) == 0U)   ? NX_VIM_FIXED_MASK0                                                        \
                     : ((word) == 1U) ? NX_VIM_FIXED_MASK1                                                        \
                     : ((word) == 2U) ? NX_VIM_FIXED_MASK2                                                        \
                                      : NX_VIM_FIXED_MASK3))

/* 1 if the derivative fixes the channel (vim.fixed), else 0 */
#define NX_VIM_CHANNEL_IS_FIXED(channel)                                                                         \
    ((unsigned)((NX_VIM_FIXED_MASK(((unsigned)(channel)) / 32U) >> (((unsigned)(channel)) % 32U)) & 1UL))

#define NX_IRQ_OS_CHANNEL_VALID(channel)                                                                         \
    ((((unsigned)(channel)) < (unsigned)NX_VIM_CHANNEL_COUNT) && (NX_VIM_CHANNEL_IS_FIXED(channel) == 0U))

/* VIM channel of an RTOS-aware interrupt. A constant channel outside the derivative or on a fixed channel is a
 * compile error (negative array size), the second line of defence behind nxconfig and the generator. */
#define NX_IRQ_OS_CHANNEL(channel)                                                                               \
    ((unsigned)(sizeof(char[NX_IRQ_OS_CHANNEL_VALID(channel) ? 1 : -1]) * 0U) + (unsigned)(channel))

#endif /* NX_VIM_CHANNEL_H */
