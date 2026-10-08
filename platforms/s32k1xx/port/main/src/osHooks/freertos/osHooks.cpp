/********************************************************************************
 * Copyright (c) 2024 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

// neXtBSW (SYS-80): vApplicationStackOverflowHook no longer prints a line and returns (finding R1 of
// doc/safety/rtos-comparison.md: the kernel went on with the memory below the stack possibly overwritten). It hands
// the task over to the kernel fault reaction of the family port, nxKernelStackOverflow() (os/isr/isr_sys.cpp,
// prototype in os/FreeRtosPlatformConfig.h): outputs off, SAFETY RESET line, reset - it does not return.

#include "FreeRTOS.h"
#include "task.h"

#include <etl/infinite_loop.h>

#include <cstdint>
#include <cstdio>

extern "C"
{
// Called by the kernel in the context switch (PendSV) with the task that is switched out, configCHECK_FOR_STACK_OVERFLOW
// 2: the pattern at the end of its stack was overwritten. The task control block lies above the stack (OpenBSW
// TaskImpl: stack first, then StaticTask_t), so the task's number and name are still its own; the supervisor copies at
// most a few printable characters of the name.
void vApplicationStackOverflowHook(TaskHandle_t xTask, char* pcTaskName)
{
    nxKernelStackOverflow(static_cast<uint32_t>(uxTaskGetTaskNumber(xTask)), pcTaskName);
}

void vIllegalISR()
{
    printf("vIllegalISR\r\n");
    etl::infinite_loop();
}
}
