/********************************************************************************
 * Copyright (c) 2024 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

#pragma once

/* Device-specific asyncFreeRtos configuration.
 *
 * This file contains the part of the FreeRTOS configuration for a specific device.
 * It is included into libs/bsw/asyncFreeRtos/freeRtosConfiguration/FreeRTOSConfig.h, whose
 * own content provides device-independent configuration of FreeRTOS for use with async API.
 *
 * For the description of the config parameters used below,
 * see https://www.freertos.org/Documentation/02-Kernel/03-Supported-devices/02-Customization
 */

#include "bsp/SystemTime.h"
// ARCH-13: core and bus clock of the generated clock plan (tools/bswgen, project clock.system_hz)
#include "bsp/clock/boardClock.h"

#ifdef __cplusplus
extern "C"
{
#endif

// SysTick runs from the core clock (port.c: configSYSTICK_CLOCK_HZ = configCPU_CLOCK_HZ)
#define configCPU_CLOCK_HZ (NX_CLOCK_CORE_HZ)

/* SYS-80 (finding R4 of doc/safety/rtos-comparison.md): only what the code uses is switched on - every switch below
 * that enables kernel code has its user named, and the test freertos-config
 * (platforms/s32k1xx/port/main/src/osHooks/test/test_freertos_config.py) fails for a switch without one and for a
 * name the kernel does not know. Removed with SYS-80: INCLUDE_vTaskPrioritySet, _uxTaskPriorityGet, _vTaskDelete,
 * _vTaskDelayUntil, _vTaskDelay, _eTaskGetState, _xQueueGetMutexHolder (no caller), INCLUDE_vTaskCleanUpResources,
 * _vResumeFromISR, _pcTaskGetTaskName, _xEventGroupSetBitFromISR, configUSE_FPU, configTOTAL_HEAP_SIZE,
 * configAPPLICATION_ALLOCATED_HEAP, configUSE_TICKLESS_IDLE_DECISION_HOOK, configEXPECTED_IDLE_TIME_BEFORE_SLEEP,
 * configBUS_CLOCK_HZ, configLIBRARY_* (names this kernel version does not read, or nobody reads),
 * configRECORD_STACK_HIGH_ADDRESS (a TCB field for debuggers, no code reads it); recursive mutexes, counting
 * semaphores, the queue registry, idle yield and the run-time statistics of the kernel are off (no caller - the
 * console statistics come from the OpenBSW runtime monitor, not from the kernel counters). */
#undef configMINIMAL_STACK_SIZE
#define configMINIMAL_STACK_SIZE  ((unsigned short)512)
#define configMAX_TASK_NAME_LEN   (10)
// no other task shares the idle priority (the idle task runs the async idle context), so there is nothing to yield to
#define configIDLE_SHOULD_YIELD   0
// libs/bsp/bspAdcAccess FreeRtosAdcLock.h (ADC lock_policy rtos_mutex of a project): xSemaphoreCreateMutexStatic
#define configUSE_MUTEXES         1
#define configQUEUE_REGISTRY_SIZE 0
/* Pattern check of the whole stack end at every context switch. SYS-80 (R1): a hit ends in the safe state and a reset
 * (vApplicationStackOverflowHook -> nxKernelStackOverflow, main/src/osHooks and main/src/os/isr/isr_sys.cpp). */
#undef configCHECK_FOR_STACK_OVERFLOW
#define configCHECK_FOR_STACK_OVERFLOW          2
#define configUSE_RECURSIVE_MUTEXES             0
#define configUSE_MALLOC_FAILED_HOOK            0
#define configUSE_APPLICATION_TASK_TAG          0
#define configUSE_COUNTING_SEMAPHORES           0
#define configNUM_THREAD_LOCAL_STORAGE_POINTERS 0

/* Co-routine definitions. */
#define configMAX_CO_ROUTINE_PRIORITIES (0)

/* Software timer definitions. */
#undef configTIMER_QUEUE_LENGTH

// Queue length for the timer task. This needs to account for all timers
// that are used across all tasks.
#define configTIMER_QUEUE_LENGTH 30
#undef configTIMER_TASK_STACK_DEPTH
#define configTIMER_TASK_STACK_DEPTH (configMINIMAL_STACK_SIZE)

/* API functions beyond the ones OpenBSW's FreeRTOSConfig.h switches on (vTaskSuspend, xTaskGetCurrentTaskHandle,
xTaskGetIdleTaskHandle, uxTaskGetStackHighWaterMark, xTimerPendFunctionCall): only the one a module calls. */
// libs/bsp/bspAdcAccess FreeRtosAdcLock.h (lock_policy rtos_mutex): locks only once the scheduler runs
#define INCLUDE_xTaskGetSchedulerState 1

/* Cortex-M specific definitions. */
#ifdef __NVIC_PRIO_BITS
#define configPRIO_BITS __NVIC_PRIO_BITS
#else
#define configPRIO_BITS 4
#endif

/* PLAT-12: the two register values the Cortex-M ports need come from the generated interrupt priorities of the
 * project (PLAT-21, interrupts/interruptPriorities.h -> platforms/common/interruptPriority): lowest NVIC level of
 * the derivative and the highest level that may call the RTOS (project interrupts.max_syscall_level).
 * Before that both were written as 8-bit values for 4 priority bits (0x0F and 0x05 shifted by 8 - bits), which is
 * wrong on a derivative with 2 bits (Cortex-M0+: 0x0F << 6 does not fit a priority register) and disagreed with
 * the static_assert of the integration (main.cpp). For 4 bits and max_syscall_level 5 the values are the same as
 * before (0xF0 and 0x50), so nothing changes for the S32K14x/S32K148.
 * The values are 8-bit register values as the ports expect them; ARMv6-M has no BASEPRI and needs level 0 there
 * (nxInterruptPriority.h checks it). */
#include "interrupts/interruptPriorities.h"

#ifndef configKERNEL_INTERRUPT_PRIORITY
#define configKERNEL_INTERRUPT_PRIORITY NX_FREERTOS_KERNEL_INTERRUPT_PRIORITY
#endif

#ifndef configMAX_SYSCALL_INTERRUPT_PRIORITY
#define configMAX_SYSCALL_INTERRUPT_PRIORITY NX_FREERTOS_MAX_SYSCALL_INTERRUPT_PRIORITY
#endif

#if defined(__GNUC__) && (!defined(__ASSEMBLER__))
#include "mcu/mcu.h"

#include <stdint.h>

/* SYS-80: reactions of the family port to faults the kernel finds in itself (main/src/os/isr/isr_sys.cpp). Both
 * switch the outputs off, write one SAFETY RESET line and reset through the safe supervisor (S1 + S2, events
 * RTOS_ASSERT and STACK_OVERFLOW) and never return; callable from a task, an interrupt, the context switch and before
 * the scheduler runs. */
void nxKernelAssertFailed(uint32_t line) __attribute__((noreturn));
void nxKernelStackOverflow(uint32_t taskNumber, char const* taskName) __attribute__((noreturn));
#endif

/* SYS-80 (finding R2 of doc/safety/rtos-comparison.md): kernel assertions are compiled in and end in the safe state.
 * Before SYS-80 configASSERT was an empty DEV_ASSERT (NXP SDK name), so the kernel checked nothing. The hook gets the
 * source line; the address of its call names the function and so the kernel file (the compiler shares one call among
 * the checks of a function) - a compare, a constant and a branch per check, and no file name in the image. */
#define configASSERT(x)                               \
    do                                                \
    {                                                 \
        if ((x) == 0)                                 \
        {                                             \
            nxKernelAssertFailed((uint32_t)__LINE__); \
        }                                             \
    } while (0)

/* Tickless Idle Mode */
#define configUSE_TICKLESS_IDLE 0

/* Definitions that map the FreeRTOS port interrupt handlers to their CMSIS
standard names. */
// ISR names in the interrupt weak functor table
#define vPortSVCHandler     SVC_Handler
#define xPortPendSVHandler  PendSV_Handler
#define xPortSysTickHandler SysTick_Handler

// SYS-80 (R4): off - nothing reads the kernel's run-time counters (the console statistics use the OpenBSW runtime
// monitor through the trace hooks), and each context switch no longer reads the system time for them
#define configGENERATE_RUN_TIME_STATS (0)

#define configTIMER_SERVICE_TASK_NAME "TIMER_OS"

#ifdef __cplusplus
} /* extern "C" */
#endif
