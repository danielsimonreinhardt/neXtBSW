# *******************************************************************************
# Copyright (c) 2024 Accenture
#
# This program and the accompanying materials are made available under the
# terms of the Apache License Version 2.0 which is available at
# https://www.apache.org/licenses/LICENSE-2.0
#
# SPDX-License-Identifier: Apache-2.0
# *******************************************************************************

# neXtBSW toolchain file for arm-none-eabi-gcc, one file for all cores (PLAT-09).
#
# Based on external/openbsw/cmake/toolchains/ArmNoneEabi-header.cmake, ArmNoneEabi.cmake and ArmNoneEabi-gcc.cmake
# (common options kept in the same order), but the core options are not fixed to Cortex-M4F: tools/nxconfig derives
# them from derivative.core and derivative.fpu (core table config.CORES) and writes them as NX_MCPU, NX_MFLOAT_ABI
# and NX_MFPU into the settings include file, which the root CMakeLists.txt includes before project().

include_guard(GLOBAL)

if (NOT DEFINED NX_MCPU OR NOT DEFINED NX_MFLOAT_ABI OR NOT DEFINED NX_MFPU)
    message(FATAL_ERROR "NxArmNoneEabi-gcc.cmake needs NX_MCPU, NX_MFLOAT_ABI and NX_MFPU from tools/nxconfig "
                        "(nxconfig cmake <project> <settings.cmake>, included before project())")
endif ()
# compiler checks (try_compile) run this file again in their own project
list(APPEND CMAKE_TRY_COMPILE_PLATFORM_VARIABLES NX_MCPU NX_MFLOAT_ABI NX_MFPU)

set(CMAKE_SYSTEM_NAME Generic)
set(CMAKE_SYSTEM_PROCESSOR ARM)

set(ARM_TARGET_TRIPLE arm-none-eabi)

set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY)

set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)

add_compile_definitions(REALTIME_OS=1)

set(CMAKE_ASM_SOURCE_FILE_EXTENSIONS "s;S")

# Only the C compiler is chosen, CMake deduces the others from it. Default as in the root CMakeLists.txt.
if (NOT DEFINED CMAKE_C_COMPILER AND NOT DEFINED ENV{CC})
    set(CMAKE_C_COMPILER arm-none-eabi-gcc)
    set(CMAKE_CXX_COMPILER arm-none-eabi-g++)
endif ()

# Size report of the POST_BUILD steps (bootloader, blupdater, app.ecu). CMake finds objcopy itself but knows no
# CMAKE_SIZE; unset, the step ran the .elf as a command and Windows opened it by file association (BL-10). Searched
# like the compiler: next to an absolute CMAKE_C_COMPILER, else on PATH (presets put NX_ARM_GCC_BIN in front).
if (NOT CMAKE_SIZE)
    set(_NX_SIZE_HINTS)
    if (IS_ABSOLUTE "${CMAKE_C_COMPILER}")
        get_filename_component(_NX_SIZE_HINTS "${CMAKE_C_COMPILER}" DIRECTORY)
    endif ()
    find_program(CMAKE_SIZE NAMES ${ARM_TARGET_TRIPLE}-size HINTS ${_NX_SIZE_HINTS})
    if (NOT CMAKE_SIZE)
        message(FATAL_ERROR "${ARM_TARGET_TRIPLE}-size not found (PATH, NX_ARM_GCC_BIN): the build reports the image "
                            "sizes with it")
    endif ()
endif ()

# Core options. No FPU (NX_MFPU empty): soft float ABI without -mfpu.
set(_NX_MFPU "")
if (NOT NX_MFPU STREQUAL "")
    set(_NX_MFPU "-mfpu=${NX_MFPU}")
endif ()

set(_ARCH_FLAGS
    " \
    -mcpu=${NX_MCPU} \
    -mfloat-abi=${NX_MFLOAT_ABI} \
    ${_NX_MFPU} \
    -fmessage-length=0")

set(_CC_CXX_COMMON
    "${_ARCH_FLAGS} \
     \
    -Wno-psabi \
    -fdata-sections \
    -ffunction-sections \
    -fno-asynchronous-unwind-tables \
    -fno-builtin \
    -fno-common \
    -fshort-enums \
    -fstack-usage \
    -mno-unaligned-access \
    -mthumb")

set(_C_FLAGS "${_CC_CXX_COMMON} -funsigned-bitfields -ffreestanding")

set(_CXX_FLAGS
    "${_CC_CXX_COMMON} \
    -femit-class-debug-always -funsigned-bitfields \
    -fno-exceptions \
    -fno-non-call-exceptions \
    -fno-rtti \
    -fno-threadsafe-statics \
    -fno-use-cxa-atexit")

set(_EXE_LINKER_FLAGS
    "${_ARCH_FLAGS} \
    -specs=nano.specs -specs=nosys.specs \
    -static \
    -Wl,--gc-sections \
    -Wl,-Map,application.map,--cref")

# As OpenBSW the assembler gets the core and no float ABI, but also the FPU (PLAT-58): assembler sources that save or
# enable VFP registers (FreeRTOS port ARM_CRx_No_GIC, ARMv7-R startup) need it, and without it every such target had
# to add -mfpu locally. Empty NX_MFPU (no FPU): no -mfpu, as for C/C++.
set(_ASM_FLAGS "-g -mcpu=${NX_MCPU} ${_NX_MFPU}")

# Flags given in the cache (presets) are kept behind the toolchain flags. A build directory belongs to one core
# (root CMakeLists.txt), so finding any -mcpu means the flags were merged before.
if (DEFINED CMAKE_CXX_FLAGS)
    string(FIND "${CMAKE_CXX_FLAGS}" "-mcpu=" _TOOLCHAIN_FLAGS_FOUND)
    if (_TOOLCHAIN_FLAGS_FOUND EQUAL -1)
        set(CMAKE_CXX_FLAGS
            "${_CXX_FLAGS} ${CMAKE_CXX_FLAGS}"
            CACHE STRING "C++ flags" FORCE)
    endif ()
else ()
    set(CMAKE_CXX_FLAGS_INIT "${_CXX_FLAGS}")
endif ()

if (DEFINED CMAKE_C_FLAGS)
    string(FIND "${CMAKE_C_FLAGS}" "-mcpu=" _TOOLCHAIN_FLAGS_FOUND)
    if (_TOOLCHAIN_FLAGS_FOUND EQUAL -1)
        set(CMAKE_C_FLAGS
            "${_C_FLAGS} ${CMAKE_C_FLAGS}"
            CACHE STRING "C flags" FORCE)
    endif ()
else ()
    set(CMAKE_C_FLAGS_INIT "${_C_FLAGS}")
endif ()

if (DEFINED CMAKE_ASM_FLAGS)
    string(FIND "${CMAKE_ASM_FLAGS}" "-mcpu=" _TOOLCHAIN_FLAGS_FOUND)
    if (_TOOLCHAIN_FLAGS_FOUND EQUAL -1)
        set(CMAKE_ASM_FLAGS
            "${_ASM_FLAGS} ${CMAKE_ASM_FLAGS}"
            CACHE STRING "ASM flags" FORCE)
    endif ()
else ()
    set(CMAKE_ASM_FLAGS_INIT "${_ASM_FLAGS}")
endif ()

if (DEFINED CMAKE_EXE_LINKER_FLAGS)
    string(FIND "${CMAKE_EXE_LINKER_FLAGS}" "-specs=nano.specs" _LINKER_FLAGS_FOUND)
    if (_LINKER_FLAGS_FOUND EQUAL -1)
        set(CMAKE_EXE_LINKER_FLAGS
            "${_EXE_LINKER_FLAGS} ${CMAKE_EXE_LINKER_FLAGS}"
            CACHE STRING "linker flags" FORCE)
    endif ()
else ()
    set(CMAKE_EXE_LINKER_FLAGS_INIT "${_EXE_LINKER_FLAGS}")
endif ()
