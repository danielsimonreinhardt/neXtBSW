# Family port template of s32k1xx (PLAT-23, finding ARCH-09 B6).
#
# Everything here is family code: it holds no board and no derivative value. All of them come from the generated
# headers of the project (BOARD_GENERATED_DIR, tools/bswgen: clock, pins, CAN bit timing, memory layout, module
# integration, derivative facts) - see doc/architecture/README.md sections 4, 5 and 8.
#
#   bspConfiguration          L1 configuration of the family (ADC, CAN, UART, SystemTimer, stdIo, PWM)
#   safety                    safeLifecycle, safeMemory (NXP MPU), safeSystem, safeIo, adcSafetyConsole
#   main                      startUp.S, bootHeader.S, linker template, ISRs, StaticBsp, BspSystem, Integration
#   freeRtosCoreConfiguration FreeRtosPlatformConfig.h of the family
#   mcuResetReason            RCM_SRS as MCU reset reason of the BSW service bsw.reset (RTE-16)
#
# A board port (ecu/platforms/<board>) is Options.cmake plus its own board peculiarities and justified exceptions
# (for example working around a board fault) - no port ever points at another port again.
#
# ecu/CMakeLists.txt adds this directory for every board of the family; the board port is added afterwards, so it
# can override what it must.

add_subdirectory(bspConfiguration)
add_subdirectory(safety)
add_subdirectory(main)
add_subdirectory(freeRtosCoreConfiguration)
add_subdirectory(mcuResetReason)

if (BUILD_TARGET_RTOS STREQUAL "FREERTOS")
    # PLAT-21: FreeRTOS port of the derivative's core instead of the fixed CM4F port of the OpenBSW platform
    # (platforms/s32k1xx/3rdparty/freertosPort, added by the root CMakeLists.txt from NX_CORE_ARCHITECTURE, PLAT-10).
    add_library(freeRtosPort ALIAS nxFreeRtosPort)
    add_library(freeRtosPortImpl ALIAS nxFreeRtosPortImpl)
elseif (BUILD_TARGET_RTOS STREQUAL "THREADX")
    # PLAT-71 (ARCH-30 F7): ThreadX as in openbsw:executables/referenceApp/platforms/s32k148evb/CMakeLists.txt.
    # The OpenBSW platform only has a Cortex-M4 port (platforms/s32k1xx/3rdparty/threadx), so no other core.
    include("${NX_ROOT}/platforms/common/cmake/NxCoreArchitecture.cmake")
    nx_core_architecture(_NX_ARCHITECTURE)
    if (NOT _NX_ARCHITECTURE STREQUAL "armv7m")
        message(FATAL_ERROR "BUILD_TARGET_RTOS THREADX: OpenBSW has a ThreadX port for Cortex-M4 (armv7m) only, "
                            "core '${NX_CORE}' is ${_NX_ARCHITECTURE}")
    endif ()
    # tx_user.h of the reference application, used unchanged (ThreadX options from the generated async/Config.h)
    add_subdirectory("${OPENBSW_DIR}/executables/referenceApp/platforms/s32k148evb/threadXCoreConfiguration"
                     threadXCoreConfiguration)
    add_library(threadXPort ALIAS threadXCortexM4Port)
    add_library(threadXPortImpl ALIAS threadXCortexM4)
endif ()
