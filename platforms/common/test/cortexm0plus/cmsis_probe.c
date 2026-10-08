/* Cortex-M0+ build test (PLAT-10): core_cm0plus.h compiles with the OpenBSW CMSIS compiler headers (CMSIS 6.1.0)
 * and the NVIC priority shift follows __NVIC_PRIO_BITS. Device part synthetic (no derivative header). */

typedef enum
{
    NonMaskableInt_IRQn = -14,
    HardFault_IRQn      = -13,
    SVCall_IRQn         = -5,
    PendSV_IRQn         = -2,
    SysTick_IRQn        = -1,
    TEST_IRQ0_IRQn      = 0,
    TEST_IRQ31_IRQn     = 31
} IRQn_Type;

#define __CM0PLUS_REV          0x0000U
#define __MPU_PRESENT          0U
#define __VTOR_PRESENT         1U
#define __NVIC_PRIO_BITS       2U
#define __Vendor_SysTickConfig 0U

#include "core_cm0plus.h"

#define NX_CORE_ARCHITECTURE_ARMV6M       1
#define NX_CORE_INTERRUPT_CONTROLLER_NVIC 1
#define NX_NVIC_PRIORITY_BITS    2
#define NX_IRQ_MAX_SYSCALL_LEVEL 0
#include "../../interruptPriority/include/interrupts/nxInterruptPriority.h"

void cmsisProbe(void);

void cmsisProbe(void)
{
    NVIC_SetPriority(TEST_IRQ31_IRQn, NX_IRQ_OS_LEVEL(3));
    NVIC_EnableIRQ(TEST_IRQ31_IRQn);
    SCB->VTOR = 0U;
    __disable_irq();
    __enable_irq();
}
