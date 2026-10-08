# Bootloader (doc/architecture/README.md, section 7): core src/core, HAL platforms/<family>, device hooks
# devices/<module>, generated board part and memory layout (tools/bswgen/blgen.py from project memory/bootloader).

set(BL_PLATFORM_DIR "${CMAKE_CURRENT_SOURCE_DIR}/platforms/${NX_FAMILY}")
set(BL_GENERATED_DIR "${BOARD_GENERATED_DIR}/bootloader")
if (NOT EXISTS "${BL_GENERATED_DIR}/bl_memory.h")
    message(STATUS "bootloader: project ${NX_PROJECT_NAME} has no memory partitioning (project memory), "
                   "no bootloader targets")
    return()
endif ()
if (NOT EXISTS "${BL_PLATFORM_DIR}/hal_${NX_FAMILY}.c")
    message(FATAL_ERROR "bootloader: no bootloader HAL for family ${NX_FAMILY} (${BL_PLATFORM_DIR}, PLAT-16)")
endif ()

# CAN bus: the project's bootloader.can, or for one build another board CAN bus (e.g. -DBOOTLOADER_CAN_BUS=CAN1)
set(BOOTLOADER_CAN_BUS
    ""
    CACHE STRING "CAN bus of the bootloader: board CAN bus name (e.g. CAN1), empty: project bootloader.can")
# values before ARCH-08 in existing build directories: SBC was the default (project bus now), DIAG named no bus
if (BOOTLOADER_CAN_BUS STREQUAL "SBC")
    message(WARNING "BOOTLOADER_CAN_BUS=SBC (before ARCH-08) replaced by the project's bootloader.can")
    set(BOOTLOADER_CAN_BUS
        ""
        CACHE STRING "CAN bus of the bootloader: board CAN bus name (e.g. CAN1), empty: project bootloader.can" FORCE)
elseif (BOOTLOADER_CAN_BUS STREQUAL "DIAG")
    message(FATAL_ERROR "BOOTLOADER_CAN_BUS=DIAG is no longer supported (ARCH-08): give the board CAN bus, "
                        "e.g. -DBOOTLOADER_CAN_BUS=CAN1, or empty for the project's bootloader.can")
endif ()
if (DEFINED CACHE{BOOTLOADER_SBC_CONFIGURE})
    message(WARNING "BOOTLOADER_SBC_CONFIGURE is no longer used and was removed from the cache (ARCH-08): the "
                    "transceiver of the bootloader bus is switched on, other devices only with bootloader.init")
    unset(BOOTLOADER_SBC_CONFIGURE CACHE)
endif ()
set(_BLGEN_ARGS)
if (NOT BOOTLOADER_CAN_BUS STREQUAL "")
    set(_BLGEN_ARGS --can "${BOOTLOADER_CAN_BUS}")
endif ()
file(GLOB _BLGEN_INPUTS "${NX_ROOT}/tools/bswgen/blgen.py" "${NX_ROOT}/tools/bswgen/templates/bl_*"
     "${NX_ROOT}/tools/nxconfig/*.py" "${CMAKE_CURRENT_SOURCE_DIR}/devices/*/bootloader.cmake")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS ${_BLGEN_INPUTS})
execute_process(
    COMMAND "${Python3_EXECUTABLE}" "${NX_ROOT}/tools/bswgen/blgen.py" "${NX_PROJECT_FILE}"
            "${BOARD_GENERATED_DIR}" ${_BLGEN_ARGS}
    RESULT_VARIABLE _BLGEN_RESULT)
if (NOT _BLGEN_RESULT EQUAL 0)
    message(FATAL_ERROR "blgen failed for ${NX_PROJECT_FILE} (bootloader board part, messages above)")
endif ()
# NX_BL_CAN_BUS, NX_BL_DEVICES (modules with a bootloader hook in devices/<module>)
include("${BL_GENERATED_DIR}/bl_board.cmake")
message(STATUS "bootloader: CAN bus ${NX_BL_CAN_BUS}, device hooks: ${NX_BL_DEVICES}")
set(BL_DEVICE_SOURCES)
set(BL_DEVICE_INCLUDES)
foreach (device ${NX_BL_DEVICES})
    include("${CMAKE_CURRENT_SOURCE_DIR}/devices/${device}/bootloader.cmake")
endforeach ()

# Family part besides the HAL (platforms/<family>/family.cmake, optional): BL_FAMILY_SOURCES (BL-34)
set(BL_FAMILY_SOURCES)
if (EXISTS "${BL_PLATFORM_DIR}/family.cmake")
    include("${BL_PLATFORM_DIR}/family.cmake")
endif ()

add_executable(
    bootloader
    src/core/app_image.c
    src/core/bl_main.c
    src/core/crc32.c
    src/core/isotp.c
    src/core/seedkey.c
    src/core/sha256.c
    src/core/uds.c
    "${BL_PLATFORM_DIR}/hal_${NX_FAMILY}.c"
    "${BL_PLATFORM_DIR}/main.c"
    ${BL_FAMILY_SOURCES}
    ${BL_DEVICE_SOURCES}
    "${BL_PLATFORM_DIR}/startup.S"
    # newlib system calls without link warning (PLAT-45/66), shared with updater and application
    "${NX_ROOT}/platforms/common/newlib/nx_newlib_syscalls.c")

# Linked as C also when a family part is C++ (BL-34): nothing from the C++ runtime is linked in.
set_target_properties(bootloader PROPERTIES SUFFIX ".elf" C_STANDARD 99 C_STANDARD_REQUIRED ON LINKER_LANGUAGE C)

# platforms/common: rules shared by several families, e.g. arch/cortex_m_app_entry.inc of bl_family_image.inc
target_include_directories(
    bootloader PRIVATE include src/core "${BL_PLATFORM_DIR}" platforms/common ${BL_DEVICE_INCLUDES}
                       ${_BSP_MCU_INCLUDES})

# Built for size in every build type (BL-10, measured in BL-03): the security access (SHA-256, HMAC) next to the UDS
# services has to fit into the smallest bootloader area of a profile (S32K116: 10 KB). Comes after the -O of the
# build type, so it wins; the updater keeps the build type's optimisation.
target_compile_options(bootloader PRIVATE $<$<COMPILE_LANGUAGE:C>:-Os -Wconversion -Wno-sign-conversion>
                                          $<$<COMPILE_LANGUAGE:CXX>:-Os -Wconversion -Wno-sign-conversion>)

# linker.ld of the family includes the generated bl_memory.ld (library search path). BL-30: non-contiguous regions,
# so that the generated bl_sections.ld (s32k1xx, ARMv6-M) fills the gap between vector table and FCF and the rest
# continues in .text; where no input section matches two output sections the option changes nothing.
target_link_options(bootloader PRIVATE -T "${BL_PLATFORM_DIR}/linker.ld" "-L${BL_GENERATED_DIR}"
                    -Wl,--enable-non-contiguous-regions -Wl,-Map,bootloader.map)
set(_BL_LINK_DEPENDS "${BL_PLATFORM_DIR}/linker.ld" "${BL_GENERATED_DIR}/bl_memory.ld")
if (EXISTS "${BL_GENERATED_DIR}/bl_sections.ld")
    list(APPEND _BL_LINK_DEPENDS "${BL_GENERATED_DIR}/bl_sections.ld")
endif ()
set_target_properties(bootloader PROPERTIES LINK_DEPENDS "${_BL_LINK_DEPENDS}")

add_custom_command(
    TARGET bootloader
    POST_BUILD
    COMMAND ${CMAKE_OBJCOPY} -O ihex $<TARGET_FILE:bootloader> $<TARGET_FILE_DIR:bootloader>/bootloader.hex
    # NVM-15: the bootloader is the image that writes sector 0 - its flash configuration field (s32k1xx: 0x400,
    # FSEC 0xFE) must be complete and unsecured, else the next reset after flashing secures the chip. No-op for a
    # family without such a field.
    COMMAND "${Python3_EXECUTABLE}" "${NX_ROOT}/tools/elfcheck" fcf --family ${NX_FAMILY} --project
            "${NX_PROJECT_FILE}" $<TARGET_FILE:bootloader> $<TARGET_FILE_DIR:bootloader>/bootloader.hex
    COMMAND ${CMAKE_SIZE} $<TARGET_FILE:bootloader>
    VERBATIM)

if (EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/updater/platforms/${NX_FAMILY}")
    add_subdirectory(updater)
endif ()

# PLAT-18: debug probe of board.debug, tool of the workstation (root CMakeLists.txt, nx_add_flash_target)
nx_add_flash_target(
    flash-bootloader
    DEPENDS bootloader
    WHAT "bootloader.hex"
    IMAGES "$<TARGET_FILE_DIR:bootloader>/bootloader.hex")
