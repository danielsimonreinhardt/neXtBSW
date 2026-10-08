# neXtBSW hard fault handler (PLAT-20), variant of external/openbsw/platforms/s32k1xx/hardFaultHandler with the dump
# location from the application linker script (symbols __hard_fault_dump_start/_end, __hard_fault_ram_start/_end,
# size HARD_FAULT_DUMP_SIZE from include/hardFaultHandler/hardFaultDumpLayout.h) and a reader for the report after reset.
#
# The handler is selected per core architecture (platforms/common/cmake/NxCoreArchitecture.cmake): ARMv7-M
# (Cortex-M4F/M7F) and ARMv6-M (Cortex-M0+, PLAT-10: Thumb-1 only, no fault status registers). Same layout and reader.

include("${CMAKE_CURRENT_LIST_DIR}/../../common/cmake/NxCoreArchitecture.cmake")
nx_core_architecture(_NX_ARCHITECTURE)

if (_NX_ARCHITECTURE STREQUAL "armv7m" OR _NX_ARCHITECTURE STREQUAL "armv6m")
    set(_NX_HARD_FAULT_HANDLER_SOURCE src/${_NX_ARCHITECTURE}/hardFaultHandler.S)
else ()
    message(FATAL_ERROR "nxHardFaultHandler: no hard fault handler variant for core '${NX_CORE}' "
                        "(${_NX_ARCHITECTURE}, platforms/s32k1xx/hardFaultHandler/src/<architecture>)")
endif ()

add_library(nxHardFaultHandler ${_NX_HARD_FAULT_HANDLER_SOURCE} src/HardFaultDump.cpp src/HardFaultDumpRegion.cpp
            src/SafeStateLatch.cpp) # SYS-36: latch of the safe state S1 (safeOutputs.h)

target_include_directories(nxHardFaultHandler PUBLIC include)

# SYS-116: a project in which a PWM group allows an active short (project.yaml pwm.<group>.safe_states) sets
# NX_SAFE_OUTPUT_SELECT in the generated bsp/safeOutputs/safeOutputsConfiguration.cmake (tools/bswgen). Only then are
# the walks built with the value sets of the safe states (public definition: the assembler handlers, the C walk of
# safeOutputs.h in every user, the degraded walk of safeIo) and the extended table, the selection words and the
# selection service linked (nxSafeOutputSelection). Every other project - and a build without generated board files,
# such as platforms/common/test/cortexm0plus - compiles exactly as before.
if (DEFINED BOARD_GENERATED_DIR AND EXISTS "${BOARD_GENERATED_DIR}/bsp/safeOutputs/safeOutputsConfiguration.cmake")
    include("${BOARD_GENERATED_DIR}/bsp/safeOutputs/safeOutputsConfiguration.cmake")
endif ()
if (NX_SAFE_OUTPUT_SELECT)
    # Own library: it reads the generated headers (bspMcu) and holds initialised read-only data, which the linker
    # script does not allow in a safety library (*libsafe*, .mpu_rodata).
    add_library(nxSafeOutputSelection src/SafeOutputSelection.cpp)
    target_include_directories(nxSafeOutputSelection PUBLIC include)
    target_compile_definitions(nxSafeOutputSelection PUBLIC NX_SAFE_OUTPUT_SELECT=1)
    target_link_libraries(nxSafeOutputSelection PRIVATE bspMcu)
    target_link_libraries(nxHardFaultHandler PUBLIC nxSafeOutputSelection)
endif ()
