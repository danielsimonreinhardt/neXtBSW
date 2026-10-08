/* Host test of the VIM counterpart of nxInterruptPriority.h (ARCH-24): prints the derived values for the profile
 * given with -DNX_CORE_ARCHITECTURE_ARMV7R -DNX_CORE_INTERRUPT_CONTROLLER_VIM -DNX_VIM_CHANNEL_COUNT=... and the
 * fixed channel masks; platforms/common/test/test_cortex_m0plus.py compares them and compiles invalid profiles and
 * channels, which must fail. -DNX_TEST_CHANNEL=<n> uses one constant channel. */

#include "interrupts/nxInterruptPriority.h"

#include <stdio.h>

int main(void)
{
    unsigned fixed = 0U;
    unsigned first = 0U;
    unsigned ch;
    for (ch = (unsigned)NX_VIM_CHANNEL_COUNT; ch > 0U; --ch)
    {
        fixed += NX_VIM_CHANNEL_IS_FIXED(ch - 1U);
        if (NX_IRQ_OS_CHANNEL_VALID(ch - 1U))
        {
            first = ch - 1U;
        }
    }
    printf("channels=%u os_channels=%u fixed=%u first_free=%u", (unsigned)NX_VIM_CHANNEL_COUNT,
           (unsigned)NX_IRQ_OS_CHANNEL_COUNT, fixed, first);
    printf(" last=%u", (unsigned)NX_IRQ_OS_CHANNEL(NX_VIM_CHANNEL_COUNT - 1U));
#ifdef NX_TEST_CHANNEL
    printf(" test_channel=%u", (unsigned)NX_IRQ_OS_CHANNEL(NX_TEST_CHANNEL));
#endif
    printf("\n");
    return 0;
}
