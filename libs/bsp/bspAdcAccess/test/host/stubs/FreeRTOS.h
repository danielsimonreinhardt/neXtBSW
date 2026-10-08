/* Host test stub of FreeRTOS.h (IO-16).
 *
 * Models what adc::FreeRtosAdcLock uses: the scheduler state, a statically allocated mutex with
 * priority inheritance and an owner. Types and constants keep the FreeRTOS values so the policy
 * compiles unchanged. The fields insideInterrupt, primask and basepri are read by the interrupt
 * context of the test (FreeRtosAdcLockHostTest.cpp), not by the policy: ARCH-26 moved the core
 * registers out of the contract, so this stub deliberately has no CMSIS accessors and no
 * xPortIsInsideInterrupt - a policy using one of them again does not compile.
 */

#pragma once

#include <cstdint>

using BaseType_t  = long;
using UBaseType_t = unsigned long;
using TickType_t  = uint32_t;

#define pdFALSE ((BaseType_t)0)
#define pdTRUE  ((BaseType_t)1)

#define configTICK_RATE_HZ 1000U
#define pdMS_TO_TICKS(ms) \
    ((TickType_t)(((uint64_t)(ms) * (uint64_t)configTICK_RATE_HZ) / 1000ULL))

#define taskSCHEDULER_SUSPENDED   ((BaseType_t)0)
#define taskSCHEDULER_NOT_STARTED ((BaseType_t)1)
#define taskSCHEDULER_RUNNING     ((BaseType_t)2)

namespace freertosStub
{
constexpr int TASK_COUNT     = 4;
constexpr int MUTEX_COUNT    = 4;
constexpr int NO_OWNER       = -1;
constexpr int PRIORITY_COUNT = 32;

struct MutexModel
{
    bool created            = false;
    int owner               = NO_OWNER;
    uint32_t takes          = 0U;  ///< successful takes
    uint32_t failedTakes    = 0U;  ///< takes that timed out
    uint32_t gives          = 0U;
    TickType_t lastWait     = 0U;  ///< ticks the last take was asked to wait
    int maxInheritedFrom    = -1;  ///< priority the owner inherited, -1 if none
};

struct State
{
    BaseType_t schedulerState = taskSCHEDULER_RUNNING;
    BaseType_t insideInterrupt = pdFALSE;
    uint32_t primask          = 0U;
    uint32_t basepri          = 0U;
    int currentTask           = 0;
    int priority[TASK_COUNT]  = {5, 6, 7, 8};
    int effectivePriority[TASK_COUNT] = {5, 6, 7, 8};
    MutexModel mutex[MUTEX_COUNT];
    /// A blocking take succeeds only if the test says the owner gives the mutex back meanwhile.
    bool ownerReleasesOnWait = false;
    int nextMutex            = 0; ///< index handed to the next xSemaphoreCreateMutexStatic
    int suspendDepth         = 0;
    uint32_t suspendCalls    = 0U;
    uint32_t resumeCalls     = 0U;
    uint64_t timeNs          = 0U;

    void reset() { *this = State(); }
};

extern State state;

/** Index of a mutex in the model, from the handle the policy holds. */
int indexOf(void const* handle);

} // namespace freertosStub
