// SPDX-License-Identifier: Apache-2.0

/**
 * "May this caller block?" for Cortex-M cores (ARCH-26): the platform part of adc::FreeRtosAdcLock
 * (InterruptContext), next to the interrupt lock of the family (suspendResumeAllInterrupts uses
 * PRIMASK).
 *
 * A caller must not block
 *   - in handler mode (IPSR != 0: ISR or exception),
 *   - with PRIMASK set (SuspendResumeAllInterruptsScopedLock),
 *   - with BASEPRI raised (taskENTER_CRITICAL of the ARMv7-M/ARMv8-M Mainline FreeRTOS port);
 *     ARMv6-M has no BASEPRI, its port masks with PRIMASK.
 * The architecture comes from the generated mcu/derivativeFacts.h (NX_CORE_ARCHITECTURE_*, via
 * mcu/mcu.h), never from __ARM_ARCH (ARCH-24). Any other architecture stops the build: a Cortex-R
 * port provides its own context (CPSR mode and I bit).
 *
 * Nothing in here is specific to the S32K1 family; it lives in the family port only until a common
 * place for Cortex-M platform parts exists (platforms/common, see changelog ARCH-26).
 */

#pragma once

#include <mcu/mcu.h>

#if !defined(NX_CORE_ARCHITECTURE_ARMV6M) && !defined(NX_CORE_ARCHITECTURE_ARMV7M) \
    && !defined(NX_CORE_ARCHITECTURE_ARMV8M)
#error "CortexMInterruptContext.h needs NX_CORE_ARCHITECTURE_ARMV6M, ARMV7M or ARMV8M (mcu/derivativeFacts.h)"
#endif

namespace bios
{

struct CortexMInterruptContext
{
    static bool interruptsLockedOrInIsr()
    {
        if ((__get_IPSR() != 0U) || (__get_PRIMASK() != 0U))
        {
            return true;
        }
#if defined(NX_CORE_ARCHITECTURE_ARMV7M) || defined(NX_CORE_ARCHITECTURE_ARMV8M)
        return __get_BASEPRI() != 0U;
#else
        return false;
#endif
    }
};

} // namespace bios
