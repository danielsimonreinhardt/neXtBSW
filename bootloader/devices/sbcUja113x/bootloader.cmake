# Bootloader hook of the module sbcUja113x (included by bootloader/CMakeLists.txt when a used device needs it)

# The hook talks over the bootloader SPI interface (bootloader/include/bl_spi.h); the port belongs to the family
# HAL (BL-05). Without it the hook cannot be built for this family - that is the check ARCH-08 asked for.
if (NOT EXISTS "${BL_PLATFORM_DIR}/bl_spi_${NX_FAMILY}.c")
    message(FATAL_ERROR "bootloader hook sbcUja113x needs the SPI HAL of the family ${NX_FAMILY} "
                        "(${BL_PLATFORM_DIR}/bl_spi_${NX_FAMILY}.c, PLAT-16)")
endif ()

list(APPEND BL_DEVICE_SOURCES "${CMAKE_CURRENT_LIST_DIR}/sbc_uja113x.c" "${BL_PLATFORM_DIR}/bl_spi_${NX_FAMILY}.c")
list(APPEND BL_DEVICE_INCLUDES "${CMAKE_CURRENT_LIST_DIR}" "${NX_ROOT}/libs/bsw/sbcUja113x/include")

# Source properties of this directory only, i.e. of the bootloader: the updater (subdirectory updater) builds the
# same hook without serving, like before - it refuses to run with an SBC watchdog that is not off
# (bl_board_environment_ok).
# BL-41: the bootloader always has the watchdog service. With leave (default) the hook serves the watchdog whenever
# the SBC is not in software development mode (SDMS 0): it runs from the SBC reset on (data sheet Table 7 and 8).
set_property(SOURCE "${CMAKE_CURRENT_LIST_DIR}/sbc_uja113x.c" APPEND PROPERTY COMPILE_DEFINITIONS
             BL_SBC_WATCHDOG_SERVICE)
set_property(SOURCE "${CMAKE_CURRENT_SOURCE_DIR}/src/core/bl_main.c" APPEND PROPERTY COMPILE_DEFINITIONS
             BL_DEVICE_SERVICE)
# BL-28: bootloader.sbc_watchdog: serve (generated bl_board.cmake, NX_BL_WATCHDOG_SERVED_BY): served with SDMS 1 too
if ("sbcUja113x" IN_LIST NX_BL_WATCHDOG_SERVED_BY)
    set_property(SOURCE "${CMAKE_CURRENT_LIST_DIR}/sbc_uja113x.c" APPEND PROPERTY COMPILE_DEFINITIONS
                 BL_SBC_WATCHDOG_SERVE)
    message(STATUS "bootloader: SBC watchdog served by the bootloader (bootloader.sbc_watchdog: serve)")
else ()
    message(STATUS "bootloader: SBC watchdog served by the bootloader without development mode (SDMS 0, BL-41)")
endif ()
