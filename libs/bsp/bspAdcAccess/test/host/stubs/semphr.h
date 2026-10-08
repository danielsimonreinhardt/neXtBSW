/* Host test stub of semphr.h (IO-16): a statically allocated FreeRTOS mutex with an owner and
 * priority inheritance. See FreeRTOS.h in this directory. */

#pragma once

#include <FreeRTOS.h>

struct StaticSemaphore_t
{
    int index = -1;
};

using SemaphoreHandle_t = StaticSemaphore_t*;

inline SemaphoreHandle_t xSemaphoreCreateMutexStatic(StaticSemaphore_t* const storage)
{
    storage->index = freertosStub::state.nextMutex++;
    if (storage->index < freertosStub::MUTEX_COUNT)
    {
        freertosStub::state.mutex[storage->index]         = freertosStub::MutexModel{};
        freertosStub::state.mutex[storage->index].created  = true;
    }
    return storage;
}

inline BaseType_t xSemaphoreTake(SemaphoreHandle_t const handle, TickType_t const ticks)
{
    auto& s        = freertosStub::state;
    auto& m        = s.mutex[freertosStub::indexOf(handle)];
    m.lastWait     = ticks;
    if (m.owner == freertosStub::NO_OWNER)
    {
        m.owner = s.currentTask;
        ++m.takes;
        return pdTRUE;
    }
    if (ticks == 0U)
    {
        ++m.failedTakes;
        return pdFALSE;
    }
    // Waiting task lends its priority to the owner (configUSE_MUTEXES priority inheritance).
    if (s.priority[s.currentTask] > s.effectivePriority[m.owner])
    {
        s.effectivePriority[m.owner] = s.priority[s.currentTask];
        m.maxInheritedFrom           = s.currentTask;
    }
    if (!s.ownerReleasesOnWait)
    {
        ++m.failedTakes;
        return pdFALSE; // timeout
    }
    s.effectivePriority[m.owner] = s.priority[m.owner];
    m.owner                      = s.currentTask;
    ++m.takes;
    return pdTRUE;
}

inline BaseType_t xSemaphoreGive(SemaphoreHandle_t const handle)
{
    auto& s = freertosStub::state;
    auto& m = s.mutex[freertosStub::indexOf(handle)];
    if (m.owner != freertosStub::NO_OWNER)
    {
        s.effectivePriority[m.owner] = s.priority[m.owner];
    }
    m.owner = freertosStub::NO_OWNER;
    ++m.gives;
    return pdTRUE;
}
