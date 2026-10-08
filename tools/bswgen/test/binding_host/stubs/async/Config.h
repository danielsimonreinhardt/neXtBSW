#pragma once

// Host stub of the generated task list async/Config.h (tools/rtegen, ecu/asyncCoreConfiguration/tasks.yaml): the BSW
// tasks with distinct numbers, so the test can tell which task an instance was created in (ARCH-52 binding test).

enum
{
    TASK_BACKGROUND = 1,
    TASK_BSP,
    TASK_SWC,
    TASK_CAN,
    TASK_SYSADMIN,
    TASK_SAFETY,
    ASYNC_CONFIG_TASK_COUNT,
};

enum
{
    TASK_IDLE = 0
};
