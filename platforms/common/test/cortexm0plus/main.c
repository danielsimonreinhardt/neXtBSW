/* Cortex-M0+ build test (PLAT-10): the integration functions the building blocks expect, one task, no hardware.
 * Only linked and inspected (readelf/objdump/nm), never run. */

#include "FreeRTOS.h"
#include "task.h"

#include <errno.h>
#include <sys/stat.h>

extern void customHardFaultHandler(void);

/* Startup template: called before the RAM initialization */
void boardInit(void) {}

/* FreeRTOS port (ManualChanges.diff): interrupt priorities by rank, then enable */
unsigned setupApplicationsIsrLevel;

void setupApplicationsIsr(void) { setupApplicationsIsrLevel = NX_IRQ_OS_LEVEL(NX_IRQ_OS_RANK_COUNT - 1U); }

/* Hard fault handler: vector without function call, LR keeps EXC_RETURN */
__attribute__((naked)) void HardFault_Handler(void)
{
    __asm__("ldr r0, =customHardFaultHandler\n bx r0\n .pool");
}

void HardFault_Handler_Final(void)
{
    for (;;) {}
}

static StackType_t idleStack[configMINIMAL_STACK_SIZE];
static StaticTask_t idleTcb;

void vApplicationGetIdleTaskMemory(StaticTask_t** tcb, StackType_t** stack, uint32_t* size)
{
    *tcb   = &idleTcb;
    *stack = idleStack;
    *size  = configMINIMAL_STACK_SIZE;
}

static StackType_t taskStack[256];
static StaticTask_t taskTcb;

static void task(void* parameter)
{
    (void)parameter;
    for (;;)
    {
        vTaskDelay(10);
    }
}

int main(void)
{
    (void)xTaskCreateStatic(&task, "test", 256, NULL, 1, taskStack, &taskTcb);
    vTaskStartScheduler();
    return 0;
}

/* __libc_init_array of the startup template without crti.o (-nostartfiles) */
void _init(void);
void _init(void) {}

/* newlib system calls (printf of the dump reader): _close/_lseek/_read/_write from platforms/common/newlib (PLAT-66),
 * the two the stdio of the test additionally needs here */
int _fstat(int file, struct stat* st)
{
    (void)file;
    st->st_mode = S_IFCHR;
    return 0;
}

int _isatty(int file)
{
    (void)file;
    return 1;
}
