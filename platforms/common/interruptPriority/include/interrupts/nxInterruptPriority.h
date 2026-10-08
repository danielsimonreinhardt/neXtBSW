/* Interrupt priorities as ranks (PLAT-10, ARCH-24, README.md): entry header for every core profile. C and C++,
 * header only, no hardware access.
 *
 * The project names a rank per interrupt; what a rank becomes depends on the interrupt controller of the core:
 *   NVIC (Cortex-M):  NVIC level = NX_IRQ_MAX_SYSCALL_LEVEL + rank
 *                     platforms/common/cortex-m/interruptPriority/include/interrupts/nxNvicPriority.h
 *   VIM  (Cortex-R):  VIM channel in the order of the ranks
 *                     platforms/common/cortex-r/interruptPriority/include/interrupts/nxVimChannel.h
 *
 * The profile comes from the generated mcu/derivativeFacts.h (NX_CORE_ARCHITECTURE_* and
 * NX_CORE_INTERRUPT_CONTROLLER_*, one table: tools/nxconfig config.CORES), never from __ARM_ARCH - GCC defines
 * __ARM_ARCH 7 for Cortex-M4F and Cortex-R4F alike, so a Cortex-R4F build silently took the BASEPRI branch of the
 * NVIC rules (finding ARCH-19). Without exactly one architecture and one controller the build stops.
 */

#ifndef NX_INTERRUPT_PRIORITY_H
#define NX_INTERRUPT_PRIORITY_H

#if (defined(NX_CORE_ARCHITECTURE_ARMV6M) + defined(NX_CORE_ARCHITECTURE_ARMV7M) + defined(NX_CORE_ARCHITECTURE_ARMV8M) \
     + defined(NX_CORE_ARCHITECTURE_ARMV7R))                                                                             \
    != 1
#error "nxInterruptPriority.h needs exactly one NX_CORE_ARCHITECTURE_* (generated mcu/derivativeFacts.h, nxconfig CORES)"
#endif

#if (defined(NX_CORE_INTERRUPT_CONTROLLER_NVIC) + defined(NX_CORE_INTERRUPT_CONTROLLER_VIM)) != 1
#error "nxInterruptPriority.h needs exactly one NX_CORE_INTERRUPT_CONTROLLER_* (generated mcu/derivativeFacts.h)"
#endif

#if defined(NX_CORE_INTERRUPT_CONTROLLER_NVIC)
#include "../../../cortex-m/interruptPriority/include/interrupts/nxNvicPriority.h"
#else
#include "../../../cortex-r/interruptPriority/include/interrupts/nxVimChannel.h"
#endif

#endif /* NX_INTERRUPT_PRIORITY_H */
