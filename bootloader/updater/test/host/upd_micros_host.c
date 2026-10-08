/* Host build of the updater's time measurement (PLAT-86), driven from Python
 * (bootloader/updater/test/test_upd_micros_host.py):
 *
 *   bootloader/updater/platforms/s32k1xx/upd_micros.c
 *   bootloader/updater/platforms/s32k1xx/cycle_counter/dwt/upd_cycle_counter.h
 *     -> DWT/CoreDebug register model (test/host/model_dwt/mcu/mcu.h), plain memory the test writes */

#include "mcu/mcu.h"
#include "upd_hal.h"
#include "upd_micros.h"

#include <stdint.h>

#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT __attribute__((visibility("default")))
#endif

UpdModelDwt upd_model_dwt;
UpdModelCoreDebug upd_model_core_debug;

/* upd_hal.h declares the rest of the updater HAL; only upd_micros() is linked into this DLL */

EXPORT void upd_test_reset(uint32_t cyccnt)
{
    upd_model_dwt.CTRL         = 0U;
    upd_model_dwt.CYCCNT       = cyccnt;
    upd_model_core_debug.DEMCR = 0U;
}

EXPORT void upd_test_start(void) { upd_micros_start(); }

EXPORT uint32_t upd_test_micros(void) { return upd_micros(); }

EXPORT uint32_t upd_test_restarts(void) { return upd_micros_restarts(); }

EXPORT uint32_t upd_test_demcr(void) { return upd_model_core_debug.DEMCR; }

EXPORT void upd_test_set_demcr(uint32_t value) { upd_model_core_debug.DEMCR = value; }

EXPORT uint32_t upd_test_ctrl(void) { return upd_model_dwt.CTRL; }

EXPORT void upd_test_set_ctrl(uint32_t value) { upd_model_dwt.CTRL = value; }

EXPORT uint32_t upd_test_cyccnt(void) { return upd_model_dwt.CYCCNT; }

EXPORT void upd_test_set_cyccnt(uint32_t value) { upd_model_dwt.CYCCNT = value; }
