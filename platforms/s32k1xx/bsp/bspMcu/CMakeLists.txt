# Family MCU header of platforms/s32k1xx (PLAT-11): mcu/mcu.h without a derivative name.
#
# nxBspMcu is an INTERFACE target: it only adds include directories, in this order
#   1. this module (mcu/mcu.h, ahead of the OpenBSW header of the same name),
#   2. the generated board configuration (mcu/mcuDerivative.h from bswgen, ARCH-03),
#   3. the directory with the vendor device header of the derivative (NX_DEVICE_HEADER_DIR, or the OpenBSW one);
#      the S32K116 header is checked in under include/3rdparty/nxp (RTD headers from hal_nxp, PLAT-32) and found
#      through 1.,
#   4. the CMSIS core headers (core_cm0plus.h from platforms/common, core_cm4.h from OpenBSW) and mcu/typedefs.h
#      of the OpenBSW module.
# The sources of the OpenBSW module (softwareSystemReset.cpp) stay in the OpenBSW target bspMcu.

add_library(nxBspMcu INTERFACE)

target_include_directories(nxBspMcu INTERFACE include)

if (DEFINED NX_GENERATED_INCLUDE_DIR)
    target_include_directories(nxBspMcu INTERFACE "${NX_GENERATED_INCLUDE_DIR}")
endif ()

if (DEFINED NX_DEVICE_HEADER_DIR)
    target_include_directories(nxBspMcu INTERFACE "${NX_DEVICE_HEADER_DIR}")
else ()
    # OpenBSW ships the S32K148 header (BSD-3-Clause); derivatives it does not describe need NX_DEVICE_HEADER_DIR
    target_include_directories(
        nxBspMcu INTERFACE "${OPENBSW_DIR}/platforms/s32k1xx/bsp/bspMcu/include")
endif ()

include("${CMAKE_CURRENT_LIST_DIR}/../../../common/cmake/NxCoreArchitecture.cmake")
nx_core_architecture(_nx_arch)
if (_nx_arch STREQUAL armv6m)
    target_include_directories(
        nxBspMcu INTERFACE "${CMAKE_CURRENT_LIST_DIR}/../../../common/3rdparty/cmsis")
else ()
    target_include_directories(nxBspMcu INTERFACE "${OPENBSW_DIR}/libs/3rdparty/cmsis")
endif ()
target_include_directories(
    nxBspMcu INTERFACE "${OPENBSW_DIR}/libs/3rdparty/cmsis/m-profile"
                       "${OPENBSW_DIR}/platforms/s32k1xx/bsp/bspMcu/include")
