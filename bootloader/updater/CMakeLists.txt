# Bootloader updater (BL-01): application at the application start that replaces the bootloader with an embedded image.
# Family part in platforms/<family> (s32k1xx: flash configuration field, FTFC), memory layout and board part generated.

set(UPD_PLATFORM_DIR "${CMAKE_CURRENT_SOURCE_DIR}/platforms/${NX_FAMILY}")

# NX_ROOT is the neXtBSW tree (ARCH-57): in a workspace outside the tree CMAKE_SOURCE_DIR is the customer
# repository, so paths into the tree cannot use it. The host test of the cycle counter includes this file in
# script mode, where only CMAKE_SOURCE_DIR is set - hence the fallback.
if (NOT NX_ROOT)
    set(NX_ROOT "${CMAKE_SOURCE_DIR}")
endif ()

# Free running counter of the time measurement (upd_micros), per core architecture of the derivative (PLAT-28):
# NX_CORE_ARCHITECTURE comes from tools/nxconfig (derivative.core). ARMv7-M counts DWT cycles; ARMv6-M has no DWT,
# the family port counts a peripheral timer instead (s32k1xx: LPIT0 on FIRC).
include("${NX_ROOT}/platforms/common/cmake/NxCoreArchitecture.cmake")
nx_core_architecture(_upd_arch)
set(_UPD_CYCLE_COUNTERS armv7m=dwt armv6m=lpit)
set(UPD_CYCLE_COUNTER "")
foreach (_entry IN LISTS _UPD_CYCLE_COUNTERS)
    if (_entry MATCHES "^${_upd_arch}=(.+)$")
        set(UPD_CYCLE_COUNTER "${CMAKE_MATCH_1}")
    endif ()
endforeach ()
if (NOT UPD_CYCLE_COUNTER OR NOT EXISTS "${UPD_PLATFORM_DIR}/cycle_counter/${UPD_CYCLE_COUNTER}/upd_cycle_counter.h")
    message(FATAL_ERROR "blupdater: no cycle counter for core '${NX_CORE}' (${_upd_arch}) in family ${NX_FAMILY} "
                        "(${UPD_PLATFORM_DIR}/cycle_counter/<counter>/upd_cycle_counter.h, PLAT-28)")
endif ()

set(BLUPDATER_IMAGE
    ""
    CACHE FILEPATH "Bootloader hex embedded into the updater (empty: the bootloader built here)")
option(BLUPDATER_FORCE_REWRITE "Updater rewrites the bootloader even if it is already installed (board test)" OFF)

set(CANFLASH "${NX_ROOT}/tools/canflash/canflash.py")
# canflash reads the same generated memory layout as bootloader and updater
set(CANFLASH_LAYOUT --layout "${BL_GENERATED_DIR}/bl_memory.h")
if (BLUPDATER_IMAGE)
    set(UPD_IMAGE_HEX "${BLUPDATER_IMAGE}")
    set(UPD_IMAGE_DEPENDS "${BLUPDATER_IMAGE}")
else ()
    set(UPD_IMAGE_HEX "$<TARGET_FILE_DIR:bootloader>/bootloader.hex")
    set(UPD_IMAGE_DEPENDS bootloader)
endif ()

# canflash blimage checks the image (size, FCF, vectors, version) and aborts the build on errors.
set(UPD_BLOB_C "${CMAKE_CURRENT_BINARY_DIR}/$<CONFIG>/upd_blob.c")
add_custom_command(
    OUTPUT "${UPD_BLOB_C}"
    COMMAND "${Python3_EXECUTABLE}" "${CANFLASH}" ${CANFLASH_LAYOUT} blimage "${UPD_IMAGE_HEX}" --c-out "${UPD_BLOB_C}"
    DEPENDS ${UPD_IMAGE_DEPENDS} "${CANFLASH}" "${BL_GENERATED_DIR}/bl_memory.h"
    COMMENT "Embedding the bootloader image into the updater"
    VERBATIM)

# Family part besides the HAL, as in the bootloader (BL-34: low-voltage detect reset of bl_system_init()); included
# again here because source properties such as the include directory of a family source are per directory.
set(BL_FAMILY_SOURCES)
if (EXISTS "${BL_PLATFORM_DIR}/family.cmake")
    include("${BL_PLATFORM_DIR}/family.cmake")
endif ()

add_executable(
    blupdater
    src/core/bl_update.c
    "${UPD_PLATFORM_DIR}/upd_flash_${NX_FAMILY}.c"
    "${UPD_PLATFORM_DIR}/upd_main.c"
    "${UPD_PLATFORM_DIR}/upd_micros.c"
    ../src/core/crc32.c
    "${BL_PLATFORM_DIR}/hal_${NX_FAMILY}.c"
    ${BL_FAMILY_SOURCES}
    ${BL_DEVICE_SOURCES}
    "${BL_PLATFORM_DIR}/startup.S"
    "${UPD_BLOB_C}"
    # newlib system calls without link warning (PLAT-45/66), shared with bootloader and application
    "${NX_ROOT}/platforms/common/newlib/nx_newlib_syscalls.c")

set_target_properties(blupdater PROPERTIES SUFFIX ".elf" C_STANDARD 99 C_STANDARD_REQUIRED ON LINKER_LANGUAGE C)

# same CAN bus and device hooks as the bootloader (generated bl_board.h)
target_include_directories(
    blupdater PRIVATE include src/core ../include ../src/core "${BL_PLATFORM_DIR}"
                      "${UPD_PLATFORM_DIR}/cycle_counter/${UPD_CYCLE_COUNTER}" ${BL_DEVICE_INCLUDES}
                      ${_BSP_MCU_INCLUDES})

if (BLUPDATER_FORCE_REWRITE)
    target_compile_definitions(blupdater PRIVATE UPD_FORCE_REWRITE)
endif ()

target_compile_options(blupdater PRIVATE $<$<COMPILE_LANGUAGE:C>:-Wconversion -Wno-sign-conversion>)

target_link_options(blupdater PRIVATE -T "${UPD_PLATFORM_DIR}/linker.ld" "-L${BL_GENERATED_DIR}"
                    -Wl,-Map,blupdater.map)
set_target_properties(blupdater PROPERTIES LINK_DEPENDS "${UPD_PLATFORM_DIR}/linker.ld;${BL_GENERATED_DIR}/bl_memory.ld")

set(UPD_HEX "$<TARGET_FILE_DIR:blupdater>/blupdater.hex")
add_custom_command(
    TARGET blupdater
    POST_BUILD
    COMMAND ${CMAKE_OBJCOPY} -O ihex $<TARGET_FILE:blupdater> ${UPD_HEX}
    COMMAND "${Python3_EXECUTABLE}" "${CANFLASH}" ${CANFLASH_LAYOUT} record ${UPD_HEX}
            "$<TARGET_FILE_DIR:blupdater>/blupdater.record.hex"
    COMMAND ${CMAKE_SIZE} $<TARGET_FILE:blupdater>
    VERBATIM)

if (NOT DEFINED CAN_INTERFACE)
    set(CAN_INTERFACE "vector")
endif ()
if (NOT DEFINED CAN_CHANNEL)
    set(CAN_CHANNEL "0")
endif ()
add_custom_target(
    blupdate-can
    COMMAND "${Python3_EXECUTABLE}" "${CANFLASH}" ${CANFLASH_LAYOUT} blupdate ${UPD_HEX} --interface ${CAN_INTERFACE}
            --channel ${CAN_CHANNEL}
    DEPENDS blupdater
    COMMENT "Replacing the bootloader via CAN (asks for confirmation)"
    USES_TERMINAL
    VERBATIM)
