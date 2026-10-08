/* FreeRTOS configuration of the Cortex-M0+ build test (PLAT-10): static allocation only, no timers. Interrupt
 * priorities from platforms/common/interruptPriority (NX_NVIC_PRIORITY_BITS, NX_IRQ_MAX_SYSCALL_LEVEL from CMake). */

#ifndef FREERTOS_CONFIG_H
#define FREERTOS_CONFIG_H

#include "interrupts/nxInterruptPriority.h"

#define configUSE_PREEMPTION                    1
#define configUSE_PORT_OPTIMISED_TASK_SELECTION 0
#define configCPU_CLOCK_HZ                      (48000000UL)
#define configTICK_RATE_HZ                      (1000U)
#define configMAX_PRIORITIES                    (5)
#define configMINIMAL_STACK_SIZE                ((unsigned short)128)
#define configMAX_TASK_NAME_LEN                 (8)
#define configUSE_16_BIT_TICKS                  0
#define configIDLE_SHOULD_YIELD                 1
#define configUSE_MUTEXES                       1
#define configUSE_IDLE_HOOK                     0
#define configUSE_TICK_HOOK                     0
#define configSUPPORT_STATIC_ALLOCATION         1
#define configSUPPORT_DYNAMIC_ALLOCATION        0
#define configUSE_TIMERS                        0
#define configUSE_CO_ROUTINES                   0
#define configMAX_CO_ROUTINE_PRIORITIES         (1)
#define configCHECK_FOR_STACK_OVERFLOW          0

#define configKERNEL_INTERRUPT_PRIORITY      NX_FREERTOS_KERNEL_INTERRUPT_PRIORITY
#define configMAX_SYSCALL_INTERRUPT_PRIORITY NX_FREERTOS_MAX_SYSCALL_INTERRUPT_PRIORITY

#define INCLUDE_vTaskDelay 1

#define configASSERT(x) \
    if ((x) == 0)       \
    {                   \
        for (;;) {}     \
    }

/* Port handlers under the vector table names of the startup template */
#define vPortSVCHandler     SVC_Handler
#define xPortPendSVHandler  PendSV_Handler
#define xPortSysTickHandler SysTick_Handler

#endif
