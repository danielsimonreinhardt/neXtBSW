/* Host test of nxInterruptPriority.h (PLAT-10): prints the derived values for the profile given with
 * -DNX_NVIC_PRIORITY_BITS and -DNX_IRQ_MAX_SYSCALL_LEVEL; platforms/common/test/test_cortex_m0plus.py compares them
 * and compiles invalid profiles and ranks, which must fail. -DNX_TEST_RANK=<n> uses one constant rank. */

#include "interrupts/nxInterruptPriority.h"

#include <stdio.h>

int main(void)
{
    printf("levels=%u lowest=%u os_ranks=%u kernel=0x%02x max_syscall=0x%02x",
           (unsigned)NX_IRQ_LEVEL_COUNT,
           (unsigned)NX_IRQ_LOWEST_LEVEL,
           (unsigned)NX_IRQ_OS_RANK_COUNT,
           (unsigned)NX_FREERTOS_KERNEL_INTERRUPT_PRIORITY,
           (unsigned)NX_FREERTOS_MAX_SYSCALL_INTERRUPT_PRIORITY);
    printf(" rank0=%u last=%u", (unsigned)NX_IRQ_OS_LEVEL(0), (unsigned)NX_IRQ_OS_LEVEL(NX_IRQ_OS_RANK_COUNT - 1U));
#ifdef NX_TEST_RANK
    printf(" test_rank=%u", (unsigned)NX_IRQ_OS_LEVEL(NX_TEST_RANK));
#endif
    printf("\n");
    return 0;
}
