#pragma once

/* Register model of DWT and CoreDebug (ARMv7-M) for the host test of the updater's time measurement (PLAT-86,
 * bootloader/updater/test/test_upd_micros_host.py). Plain memory: the test plays the hardware between two calls
 * (counts CYCCNT only while TRCENA and CYCCNTENA are set, clears TRCENA like a debugger that closes its session).
 * Layout and names as CMSIS core_cm4.h; bit positions ARMv7-M ARM DDI 0403E C1.6.5 (DEMCR.TRCENA bit 24) and C1.8.7
 * (DWT_CTRL.CYCCNTENA bit 0). */

#include <stdint.h>

typedef struct
{
    volatile uint32_t CTRL;
    volatile uint32_t CYCCNT;
} UpdModelDwt;

typedef struct
{
    volatile uint32_t DEMCR;
} UpdModelCoreDebug;

extern UpdModelDwt upd_model_dwt;
extern UpdModelCoreDebug upd_model_core_debug;

#define DWT       (&upd_model_dwt)
#define CoreDebug (&upd_model_core_debug)

#define CoreDebug_DEMCR_TRCENA_Msk (1UL << 24U)
#define DWT_CTRL_CYCCNTENA_Msk     (1UL << 0U)
