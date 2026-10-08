# FreeRTOS port for ARMv6-M (Cortex-M0/M0+) with SysTick, counterpart of
# external/openbsw/platforms/s32k1xx/3rdparty/freertos_cm4_sysTick (PLAT-10, README.md). Selected by
# ../freertosPort from NX_CORE, the target names follow the OpenBSW port.

add_library(freeRtosCm0SysTickPort INTERFACE)

target_include_directories(freeRtosCm0SysTickPort INTERFACE include)

add_library(freeRtosCm0SysTick src/port.c)

target_include_directories(freeRtosCm0SysTick PUBLIC include)

target_link_libraries(
    freeRtosCm0SysTick
    PUBLIC freeRtos
    PRIVATE bspInterrupts)

target_compile_options(freeRtosCm0SysTick PRIVATE -Wno-unused-but-set-variable)
