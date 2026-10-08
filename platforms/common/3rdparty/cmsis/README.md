# CMSIS core headers not shipped by OpenBSW

OpenBSW brings only `core_cm4.h` (`external/openbsw/libs/3rdparty/cmsis`). Further core headers for neXtBSW
derivatives live here and use the OpenBSW compiler headers (`cmsis_compiler.h`, `cmsis_gcc.h`, `m-profile/`) of the
same CMSIS version.

| File | Core | Source | License |
|---|---|---|---|
| `core_cm0plus.h` | Cortex-M0+ (S32K116/S32K118) | ARM-software/CMSIS_6 **v6.1.0**, commit `b0bbb0423b278ca632cfe1474eb227961d835fd2`, `CMSIS/Core/Include/core_cm0plus.h`, downloaded 2026-09-15 | Apache-2.0 (`LICENSE` from the same commit) |

- Same revision as the OpenBSW CMSIS copy (`external/openbsw/libs/3rdparty/cmsis/.riminfo`: `revision_sha1`
  `b0bbb04…`, `core_cm0plus.h` listed under `ignores`). `LICENSE` is identical to the OpenBSW `LICENSE`.
- Unchanged. Unlike the OpenBSW `core_cm4.h` there is no "include mcu/mcu.h instead" guard; the per-derivative
  `mcu.h` (PLAT-11) includes it after the device header has defined `IRQn_Type`, `__NVIC_PRIO_BITS` and friends.
- `__NVIC_PRIO_BITS` must match `derivative.nvic_priority_bits` in mcudb (S32K116.h says 4, the core has 2):
  `platforms/common/interruptPriority/include/interrupts/nxInterruptPriority.h` stops the build on a mismatch.
