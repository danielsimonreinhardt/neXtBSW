// SPDX-License-Identifier: Apache-2.0

/* Host test of bios::CortexMInterruptContext (ARCH-26): the platform answer to "may this caller block?"
 * of adc::FreeRtosAdcLock, over a model of IPSR, PRIMASK and BASEPRI (stubs/mcu/mcu.h).
 *
 * Compiled once per architecture of the core table (tools/nxconfig config.CORES) that the header serves:
 *   ARMv7-M (Cortex-M4F/M7F) and ARMv8-M (Cortex-M33): handler mode, PRIMASK and BASEPRI each lock,
 *   ARMv6-M (Cortex-M0+): handler mode and PRIMASK lock, BASEPRI does not exist and is never read.
 * Any other architecture (ARMv7-R) must stop the build with the #error of the header.
 *
 * Build and run (from the repository root), ARCH = ARMV7M, ARMV8M or ARMV6M:
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -DNX_CORE_ARCHITECTURE_<ARCH>=1 \
 *       -Iplatforms/s32k1xx/port/bspConfiguration/src/bsp/adc/test/host/stubs \
 *       platforms/s32k1xx/port/bspConfiguration/src/bsp/adc/test/host/CortexMInterruptContextHostTest.cpp \
 *       -o build/host/cortexm_interrupt_context_test.exe
 *   build/host/cortexm_interrupt_context_test.exe
 */

#include <cstdint>
#include <cstdio>
#include <initializer_list>

#include "../../CortexMInterruptContext.h"

namespace coreModel
{
uint32_t ipsr         = 0U;
uint32_t primask      = 0U;
uint32_t basepri      = 0U;
uint32_t basepriReads = 0U;
} // namespace coreModel

namespace
{
int failures = 0;
int checks   = 0;

#define CHECK(cond)                                                       \
    do                                                                    \
    {                                                                     \
        ++checks;                                                         \
        if (!(cond))                                                      \
        {                                                                 \
            ++failures;                                                   \
            std::printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
        }                                                                 \
    } while (false)

using Context = ::bios::CortexMInterruptContext;

void reset()
{
    coreModel::ipsr         = 0U;
    coreModel::primask      = 0U;
    coreModel::basepri      = 0U;
    coreModel::basepriReads = 0U;
}

void testThreadModeWithInterruptsEnabledMayBlock()
{
    reset();
    CHECK(!Context::interruptsLockedOrInIsr());
}

void testHandlerModeLocks()
{
    reset();
    for (uint32_t const exception : {2U, 3U, 11U, 14U, 15U, 16U, 16U + 47U, 0x1FFU})
    {
        coreModel::ipsr = exception; // NMI, HardFault, SVCall, PendSV, SysTick, IRQ0, IRQ47, highest
        CHECK(Context::interruptsLockedOrInIsr());
    }
}

void testPrimaskLocks()
{
    reset();
    coreModel::primask = 1U; // SuspendResumeAllInterruptsScopedLock, taskENTER_CRITICAL on ARMv6-M
    CHECK(Context::interruptsLockedOrInIsr());
}

#if defined(NX_CORE_ARCHITECTURE_ARMV6M)
void testArchitectureSpecific()
{
    // no BASEPRI on ARMv6-M: the stub has no accessor, so the header cannot read it; nothing else locks
    reset();
    CHECK(!Context::interruptsLockedOrInIsr());
    std::printf("ARMv6-M: BASEPRI not part of the answer\n");
}
#else
void testArchitectureSpecific()
{
    reset();
    for (uint32_t const level : {0x10U, 0x40U, 0x50U, 0xF0U})
    {
        coreModel::basepri = level; // taskENTER_CRITICAL raises BASEPRI to the syscall level
        CHECK(Context::interruptsLockedOrInIsr());
    }
    coreModel::basepri = 0U;
    CHECK(!Context::interruptsLockedOrInIsr());
    CHECK(coreModel::basepriReads > 0U);

    // handler mode or PRIMASK already answer, BASEPRI is not read any more
    coreModel::basepriReads = 0U;
    coreModel::ipsr         = 16U;
    CHECK(Context::interruptsLockedOrInIsr());
    coreModel::ipsr    = 0U;
    coreModel::primask = 1U;
    CHECK(Context::interruptsLockedOrInIsr());
    CHECK(coreModel::basepriReads == 0U);
    std::printf("ARMv7-M/ARMv8-M: BASEPRI part of the answer\n");
}
#endif

} // namespace

int main()
{
    testThreadModeWithInterruptsEnabledMayBlock();
    testHandlerModeLocks();
    testPrimaskLocks();
    testArchitectureSpecific();
    std::printf("%d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
