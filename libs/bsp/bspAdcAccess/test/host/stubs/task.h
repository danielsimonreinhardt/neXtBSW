/* Host test stub of task.h (IO-16): scheduler state. See FreeRTOS.h here. */

#pragma once

#include <FreeRTOS.h>

inline BaseType_t xTaskGetSchedulerState() { return freertosStub::state.schedulerState; }
