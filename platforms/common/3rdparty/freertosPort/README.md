# FreeRTOS port selection (neXtBSW code, no third-party source)

This folder holds a single file, `CMakeLists.txt`, written for neXtBSW (Apache-2.0, SPDX header in the file). It
contains **no FreeRTOS source**: it only picks the FreeRTOS port that matches the core architecture
(`NX_CORE_ARCHITECTURE`) and exposes it as `nxFreeRtosPort` and `nxFreeRtosPortImpl`. The folder lives under
`3rdparty/` because it sits next to the ports it selects.

The ports themselves, with their own origin and license files:

| Architecture | Port | Where | License |
|---|---|---|---|
| ARMv6-M | FreeRTOS-Kernel V10.6.2, `portable/GCC/ARM_CM0` | `platforms/s32k1xx/3rdparty/freertos_cm0_sysTick` | MIT (`LICENSE.md` there) |
| ARMv7-M | OpenBSW CM4F SysTick port | `external/openbsw` (submodule, unchanged) | Apache-2.0 / MIT, see OpenBSW |
| ARMv7-R | FreeRTOS-Kernel V11.3.1, `portable/GCC/ARM_CRx_No_GIC` | `platforms/rm4x/3rdparty/freertos_crx` | MIT (`LICENSE.md` there) |

Do not add a third-party license file here unless FreeRTOS source is added to this folder.
