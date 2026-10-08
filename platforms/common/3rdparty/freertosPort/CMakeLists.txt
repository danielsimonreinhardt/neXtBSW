# SPDX-License-Identifier: Apache-2.0
#
# FreeRTOS port selected by the core architecture (PLAT-10; ARCH-25 moved it here from
# platforms/s32k1xx/3rdparty/freertosPort, finding PLAT-14) instead of the fixed
# add_subdirectory(3rdparty/freertos_cm4_sysTick) of an OpenBSW platform. Profile neutral: it only picks, the ports
# themselves stay where they are.
#
# nxFreeRtosPort      include directory of portmacro.h   (board port: add_library(freeRtosPort ALIAS nxFreeRtosPort))
# nxFreeRtosPortImpl  port.c of the selected port         (board port: freeRtosPortImpl, osHooks, application)
#
# NX_CORE_ARCHITECTURE (tools/nxconfig CORES, platforms/common/cmake/NxCoreArchitecture.cmake) decides, never the
# core name or __ARM_ARCH:
#   armv6m  platforms/s32k1xx/3rdparty/freertos_cm0_sysTick (ARMv6-M SysTick port, family neutral)
#   armv7m  OpenBSW CM4F SysTick port (freeRtosCm4SysTick, freeRtosCm4SysTickPort); the platform of the family defines
#           these targets (external/openbsw/platforms/s32k1xx, platforms/stm32). Needs a core with FPU.
#   armv8m  no port yet
#   armv7r  platforms/rm4x/3rdparty/freertos_crx: ARM_CRx_No_GIC from FreeRTOS V11.3.1 on kernel V10.6.2 (PLAT-35,
#           user decisions PLAT-44 F4, ARCH-30 F3), calls setupApplicationsIsr() through its configuration layer.
#           Needs a VFPv3 double precision FPU (NX_MFPU, checked there).
# Every port calls setupApplicationsIsr() of the board integration. An architecture without a port stops here with a
# message instead of falling back to Cortex-M code.

include("${CMAKE_CURRENT_LIST_DIR}/../../cmake/NxCoreArchitecture.cmake")
nx_core_architecture(_NX_ARCHITECTURE)

set(_NX_PORT_MISSING "")
if (_NX_ARCHITECTURE STREQUAL "armv6m")
    # selected below
elseif (_NX_ARCHITECTURE STREQUAL "armv7m")
    if (NX_FPU STREQUAL "none")
        set(_NX_PORT_MISSING "the ARMv7-M port of OpenBSW (CM4F) needs a core with FPU (a port without FPU, "
                             "FreeRTOS ARM_CM3, is not integrated)")
    endif ()
elseif (_NX_ARCHITECTURE STREQUAL "armv8m")
    set(_NX_PORT_MISSING "no ARMv8-M port is integrated yet (FreeRTOS ARM_CM33_NTZ)")
elseif (_NX_ARCHITECTURE STREQUAL "armv7r")
    # selected below
else ()
    set(_NX_PORT_MISSING "unknown architecture (tools/nxconfig CORES)")
endif ()
if (NOT _NX_PORT_MISSING STREQUAL "")
    string(REPLACE ";" "" _NX_PORT_MISSING "${_NX_PORT_MISSING}")
    message(FATAL_ERROR "nxFreeRtosPort: no FreeRTOS port for core '${NX_CORE}' (${_NX_ARCHITECTURE}): port missing - "
                        "${_NX_PORT_MISSING} (platforms/common/3rdparty/freertosPort, "
                        "doc/architecture/plattformen.md section 4)")
endif ()

add_library(nxFreeRtosPort INTERFACE)
add_library(nxFreeRtosPortImpl INTERFACE)

if (_NX_ARCHITECTURE STREQUAL "armv6m")
    add_subdirectory("${CMAKE_CURRENT_LIST_DIR}/../../../s32k1xx/3rdparty/freertos_cm0_sysTick" freertos_cm0_sysTick)
    target_link_libraries(nxFreeRtosPort INTERFACE freeRtosCm0SysTickPort)
    target_link_libraries(nxFreeRtosPortImpl INTERFACE freeRtosCm0SysTick)
elseif (_NX_ARCHITECTURE STREQUAL "armv7r")
    # TOOL-244: the family rm4x is not part of the public tree yet (private until it is supported)
    if (NOT EXISTS "${CMAKE_CURRENT_LIST_DIR}/../../../rm4x/3rdparty/freertos_crx/CMakeLists.txt")
        message(FATAL_ERROR "nxFreeRtosPort: the ARMv7-R port (platforms/rm4x/3rdparty/freertos_crx) is not part of "
                            "this tree (family not published yet)")
    endif ()
    add_subdirectory("${CMAKE_CURRENT_LIST_DIR}/../../../rm4x/3rdparty/freertos_crx" freertos_crx)
    target_link_libraries(nxFreeRtosPort INTERFACE freeRtosCrxPort)
    target_link_libraries(nxFreeRtosPortImpl INTERFACE freeRtosCrx)
else ()
    if (NOT TARGET freeRtosCm4SysTick OR NOT TARGET freeRtosCm4SysTickPort)
        message(FATAL_ERROR "nxFreeRtosPort: core '${NX_CORE}' (armv7m) uses the OpenBSW CM4F port, but the platform "
                            "of family '${NX_FAMILY}' defines no targets freeRtosCm4SysTick/freeRtosCm4SysTickPort "
                            "(add the platform before platforms/common/3rdparty/freertosPort)")
    endif ()
    target_link_libraries(nxFreeRtosPort INTERFACE freeRtosCm4SysTickPort)
    target_link_libraries(nxFreeRtosPortImpl INTERFACE freeRtosCm4SysTick)
endif ()
