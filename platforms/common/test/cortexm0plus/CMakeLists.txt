# Build test of the Cortex-M0+ building blocks without a board (PLAT-10, ../test_cortex_m0plus.py): the settings
# include file written by "nxconfig cmake" for the test profile testproject_m0plus (tools/nxconfig/test) is included
# before project() like in the root CMakeLists.txt, then the FreeRTOS kernel (OpenBSW), the port selected from
# NX_CORE, the hard fault handler selected from NX_CORE and the ARMv6-M startup template are compiled with the core
# options of the toolchain file and linked into a small ELF with a synthetic memory map. No derivative header.
cmake_minimum_required(VERSION 3.28 FATAL_ERROR)

if (NOT NX_SETTINGS_FILE)
    message(FATAL_ERROR "pass -DNX_SETTINGS_FILE=<nxconfig cmake output>")
endif ()
include("${NX_SETTINGS_FILE}")
set(NX_REPO "${CMAKE_CURRENT_LIST_DIR}/../../../..")
if (NOT CMAKE_TOOLCHAIN_FILE)
    set(CMAKE_TOOLCHAIN_FILE "${NX_REPO}/cmake/toolchains/NxArmNoneEabi-gcc.cmake")
endif ()

project(
    nxcortexm0plustest
    LANGUAGES C CXX ASM)

set(OPENBSW_DIR "${NX_REPO}/external/openbsw")
if (NOT DEFINED NX_TEST_NVIC_PRIORITY_BITS)
    set(NX_TEST_NVIC_PRIORITY_BITS 2)
endif ()
set(BUILD_TARGET_RTOS FREERTOS)

# Stand-ins for the targets the OpenBSW FreeRTOS library links (board port in a real build)
add_library(osHooks INTERFACE)
add_library(bspInterrupts INTERFACE)
add_library(freeRtosConfiguration INTERFACE)
target_include_directories(freeRtosConfiguration INTERFACE include "${NX_REPO}/platforms/common/interruptPriority/include")
# NVIC priority bits of the ARMv6-M test derivative (the value mcudb nvic_priority_bits gives for Cortex-M0+).
# ARCH-24: the core architecture as the generated mcu/derivativeFacts.h names it, from the settings of nxconfig.
string(TOUPPER "${NX_CORE_ARCHITECTURE}" _NX_ARCH)
target_compile_definitions(freeRtosConfiguration INTERFACE NX_CORE_ARCHITECTURE_${_NX_ARCH}=1 NX_CORE_INTERRUPT_CONTROLLER_NVIC=1
                                                           NX_NVIC_PRIORITY_BITS=${NX_TEST_NVIC_PRIORITY_BITS}
                                                           NX_IRQ_MAX_SYSCALL_LEVEL=0)

add_subdirectory("${NX_REPO}/platforms/common/3rdparty/freertosPort" freertosPort)
add_library(freeRtosPort ALIAS nxFreeRtosPort)
add_library(freeRtosPortImpl ALIAS nxFreeRtosPortImpl)
add_subdirectory("${OPENBSW_DIR}/libs/3rdparty/freertos" freertos)

add_subdirectory("${NX_REPO}/platforms/s32k1xx/hardFaultHandler" nxHardFaultHandler)

add_library(nxStartupArmv6m OBJECT "${NX_REPO}/platforms/s32k1xx/startup/armv6m/startUp.S")
target_include_directories(nxStartupArmv6m PRIVATE include)

# CMSIS core header for Cortex-M0+ (platforms/common/3rdparty/cmsis) with the OpenBSW CMSIS compiler headers
add_library(cmsisProbe OBJECT cmsis_probe.c)
target_include_directories(cmsisProbe PRIVATE "${NX_REPO}/platforms/common/3rdparty/cmsis"
                                              "${OPENBSW_DIR}/libs/3rdparty/cmsis" "${OPENBSW_DIR}/libs/3rdparty/cmsis/m-profile")
target_compile_options(cmsisProbe PRIVATE -Wall -Wextra -Werror)

# newlib system calls _close/_lseek/_read/_write shared with every neXtBSW ELF (PLAT-66); main.c adds _fstat/_isatty
add_executable(m0plus.elf main.c "${NX_REPO}/platforms/common/newlib/nx_newlib_syscalls.c"
                          $<TARGET_OBJECTS:nxStartupArmv6m> $<TARGET_OBJECTS:cmsisProbe>)
target_link_libraries(m0plus.elf PRIVATE freeRtos nxHardFaultHandler)
target_compile_options(m0plus.elf PRIVATE -Wall -Wextra -Werror)
# linker script through the C preprocessor (dump size from hardFaultDumpLayout.h), as in the board port
set(_LD "${CMAKE_BINARY_DIR}/m0plus.ld")
add_custom_command(
    OUTPUT "${_LD}"
    COMMAND "${CMAKE_C_COMPILER}" -E -P -x c "-I${NX_REPO}/platforms/s32k1xx/hardFaultHandler/include"
            "${CMAKE_CURRENT_LIST_DIR}/m0plus.ld" -o "${_LD}"
    DEPENDS "${CMAKE_CURRENT_LIST_DIR}/m0plus.ld"
            "${NX_REPO}/platforms/s32k1xx/hardFaultHandler/include/hardFaultHandler/hardFaultDumpLayout.h")
add_custom_target(m0plusLinkerScript DEPENDS "${_LD}")
add_dependencies(m0plus.elf m0plusLinkerScript)
target_link_options(m0plus.elf PRIVATE "-T${_LD}" -nostartfiles -Wl,-Map,m0plus.map)
set_target_properties(m0plus.elf PROPERTIES LINK_DEPENDS "${_LD}")
