# FreeRTOS port ARMv6-M (Cortex-M0/M0+), SysTick

Counterpart of `external/openbsw/platforms/s32k1xx/3rdparty/freertos_cm4_sysTick` for cores without FPU and
without `BASEPRI` (S32K116/S32K118). PLAT-10.

## Source

- FreeRTOS-Kernel **V10.6.2** (tag object `07055b9`, commit `18ed8886fe3eed9dbe433a82cdc62ce5dc0315eb`),
  `portable/GCC/ARM_CM0/port.c` and `portable/GCC/ARM_CM0/portmacro.h`, downloaded from
  https://github.com/FreeRTOS/FreeRTOS-Kernel on 2026-09-15. The directory holds only these two files at that tag.
- License **MIT** (`LICENSE.md`, identical to `LICENSE.md` of the kernel and of the OpenBSW port). The file headers
  are kept unchanged.
- Same kernel version as OpenBSW: `external/openbsw/libs/3rdparty/freertos/src/tasks.c` is identical to `tasks.c`
  of V10.6.2, and the OpenBSW CM4F port differs from `portable/GCC/ARM_CM4F/port.c` of V10.6.2 only by
  its `ManualChanges.diff`.

## Changes

Only the OpenBSW modification (`ManualChanges.diff`, same lines as in the CM4F port): `vPortSetupTimerInterrupt()`
calls `setupApplicationsIsr()` after the SysTick is started. The board integration provides the function (interrupt
priorities and enables of the application). Reapply the diff after an update of the port.

## Differences to the CM4F port that matter for the integration

- No `BASEPRI`: kernel critical sections mask all interrupts (`PRIMASK`). `configMAX_SYSCALL_INTERRUPT_PRIORITY` has
  no effect, every interrupt may call `...FromISR` functions. Priority ranks: `platforms/common/interruptPriority`.
- PendSV and SysTick get the lowest priority (`0xFF`, `xPortStartScheduler`), not `configKERNEL_INTERRUPT_PRIORITY`.
- Handlers `vPortSVCHandler`, `xPortPendSVHandler`, `xPortSysTickHandler`: mapped to the vector names with
  `#define` in `FreeRTOSConfig.h`, as for the CM4F port.
