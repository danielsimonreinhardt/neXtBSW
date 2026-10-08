# PLAT-27: console (command input) and logger (ring buffer, log lines in the idle task) are a function of the project:
# project.yaml console.enabled -> NX_CONSOLE (tools/nxconfig/console.py, generated project.cmake). Without a project
# value (or an older project.cmake) both are in the image, as before. The terminal UART stays initialised either way:
# start-up lines and the line of the safe supervisor (safety::safeConsoleWrite) do not depend on console or logger.
if (NOT DEFINED NX_CONSOLE)
    set(NX_CONSOLE ON)
endif ()
if (NX_CONSOLE)
    set(_NX_CONSOLE_SOURCES src/console/console.cpp)
else ()
    set(_NX_CONSOLE_SOURCES)
endif ()

add_executable(
    app.ecu
    src/app/app.cpp
    ${_NX_CONSOLE_SOURCES}
    src/logger/logger.cpp
    src/systems/EcuStateSystem.cpp
    src/systems/RteSystem.cpp
    src/systems/RuntimeSystem.cpp
    src/systems/SysAdminSystem.cpp
    src/systems/SafetySystem.cpp
    src/main.cpp)

set_target_properties(app.ecu PROPERTIES SUFFIX ".elf")

get_target_property(LINKER_SCRIPT startUp PROP_LINKER_SCRIPT)
target_link_options(app.ecu PRIVATE -T ${LINKER_SCRIPT})
# SYS-89: a changed linker script relinks the application (LINK_DEPENDS of startUp, a library, has no effect on the
# link of app.ecu); the post-link steps (calibration seal, ROM check, validity record) run only after a link
set_property(TARGET app.ecu APPEND PROPERTY LINK_DEPENDS "${LINKER_SCRIPT}")

target_include_directories(app.ecu PRIVATE include)

# PLAT-27: app.cpp and logger.cpp compile console and logger only with NX_CONSOLE 1 (the libraries
# below stay on the link line; an archive member nobody references is not linked)
if (NX_CONSOLE)
    target_compile_definitions(app.ecu PRIVATE NX_CONSOLE=1)
else ()
    target_compile_definitions(app.ecu PRIVATE NX_CONSOLE=0)
    message(STATUS "console and logger: off (project.yaml console.enabled, PLAT-27)")
endif ()

target_link_libraries(
    app.ecu
    PRIVATE asyncBinding
            asyncConsole
            rte
            bspMcu
            configuration
            etl
            etlImpl
            lifecycle
            consoleCommands
            logger
            printf
            loggerIntegration
            main
            stdioConsoleInput
            osRtos
            asyncRtosImpl
            util
            time
            runtime
            safeLifecycle
            safeUtils
            safeWatchdog
            bspUart
            socBsp)

if (PLATFORM_SUPPORT_CAN)
    target_link_libraries(app.ecu PRIVATE cpp2can)
endif ()

# COM-09: console command "rte can" prints rte::CAN_RX_STATUS / CAN_TX_STATUS. Those tables exist only if the
# ECU description of the project connects CAN signals; the generator says so with RTE_CAN_SOURCES
# (rte_components.cmake, see ecu/rte/CMakeLists.txt), so a project without CAN signals still builds.
include("${RTE_GENERATED_DIR}/rte_components.cmake")
if (RTE_CAN_SOURCES)
    target_compile_definitions(app.ecu PRIVATE NX_RTE_HAS_CAN=1)
endif ()

# RTE-20: hand-written C components (swc.yaml implementation: c, rte.yaml components like every component). Their
# sources <path>/src/*.c are application code: unlike the Embedded Coder output (swcComponents in ecu/rte, warnings
# not fatal) they build with the rules of the application (CMAKE_COMPILE_WARNING_AS_ERROR, same flags) and are in
# the MISRA scope of tools/misra (not generated, not excluded). rtegen names them only when there is one; a change of
# their swc.yaml or data file runs rtegen again (RTE_C_COMPONENT_INPUTS), a new source file is found by the glob.
if (RTE_C_COMPONENT_SOURCE_DIRS)
    set(_SWC_C_SOURCES)
    foreach (dir ${RTE_C_COMPONENT_SOURCE_DIRS})
        file(GLOB _sources CONFIGURE_DEPENDS "${dir}/*.c")
        if (NOT _sources)
            message(FATAL_ERROR "C component: no source in ${dir}; create the skeleton with "
                                "python tools/swc/swc_skeleton.py create <component folder>")
        endif ()
        list(APPEND _SWC_C_SOURCES ${_sources})
    endforeach ()
    add_library(swcCComponents OBJECT ${_SWC_C_SOURCES})
    target_include_directories(swcCComponents PRIVATE ${RTE_C_COMPONENT_SOURCE_DIRS} "${RTE_GENERATED_DIR}")
    target_link_libraries(swcCComponents PRIVATE autosarBase)
    target_sources(app.ecu PRIVATE $<TARGET_OBJECTS:swcCComponents>)
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS ${RTE_C_COMPONENT_INPUTS})
endif ()

# SYS-01: ECU state management; after rte, which calls its RTE hand-over (ecustate::rte). The power supply and the
# run-down steps come from the board integration (main, integration/Integration.h, SYS-08).
target_link_libraries(app.ecu PRIVATE ecuState bspConfiguration consoleContext)

# SYS-05: the RTE reports a violated deadline (task cycle or runnable longer than its period) to the safe
# supervisor, which decides the reaction. Without the library and the macro the violation is only
# counted, as before SYS-05 (systems/RteSystem.cpp).
target_link_libraries(app.ecu PRIVATE safeSupervisor)
target_compile_definitions(app.ecu PRIVATE NX_RTE_SAFE_SUPERVISOR=1)

if (PLATFORM_SUPPORT_UDS)
    target_link_libraries(app.ecu PRIVATE appUds)
endif ()

# NVM-03: fault memory of the diagnostic description (libs/bsw/dem, header only); RteSystem runs it when the
# generated RteConfig.h says NX_RTE_HAS_DEM, otherwise nothing of it is compiled.
target_link_libraries(app.ecu PRIVATE dem)

# NVM-19: NvM core of libs/bsw/nvm (header only; QM blocks and ASIL blocks with two copies, repair, events, quality),
# used by RteSystem through systems/nvm/NvmRteBlocks.h when the generated RteConfig.h says NX_RTE_HAS_NVM. The fault
# injection of the core ("nvm inject <block> <a|b>", board proof of NVM-18) is part of the development image only:
# the product image (NX_PRODUCT_BUILD, SYS-74) compiles neither the command nor nvm::Manager::injectCopyFault().
target_link_libraries(app.ecu PRIVATE nvm)
if (NOT NX_PRODUCT_BUILD)
    target_compile_definitions(app.ecu PRIVATE NX_NVM_FAULT_INJECTION=1)
endif ()

# SYS-89: calibration segment (memory.calibration): start-up check, working page and console "cal" in RteSystem
if (TARGET appCalibration)
    target_link_libraries(app.ecu PRIVATE appCalibration)
endif ()
# SYS-90: plausibility of the safety-relevant calibration parameters (without a segment RteSystem runs the start-up check)
if (TARGET calibrationPlausibility)
    target_link_libraries(app.ecu PRIVATE calibrationPlausibility)
endif ()

# SYS-141: the selection service of the safe state (hardFaultHandler/safeStateSelection.h), only in a project in which
# a PWM group allows an active short (library of the family port, NX_SAFE_OUTPUT_SELECT); RteSystem uses it when the
# generated RteConfig.h says NX_RTE_HAS_SAFE_STATE
target_link_libraries(app.ecu PRIVATE $<TARGET_NAME_IF_EXISTS:nxSafeOutputSelection>)

# TOOL-01: XCP on CAN, only if ecu/xcp created it (NX_XCP, a CAN bus in the project); ecu/xcp is added later
target_link_libraries(app.ecu PRIVATE $<TARGET_NAME_IF_EXISTS:appXcp>)
target_compile_definitions(app.ecu PRIVATE $<$<TARGET_EXISTS:appXcp>:NX_XCP=1>)

set(APP_HEX "$<TARGET_FILE_DIR:app.ecu>/app.ecu.hex")
set(APP_RECORD_HEX "$<TARGET_FILE_DIR:app.ecu>/app.ecu.record.hex")
set(CANFLASH "${NX_ROOT}/tools/canflash/canflash.py")

# SYS-38: the hex carries the checksum list and the reference CRC of the ROM check (SM-14): the post-link step of the
# family writes the reference into the ELF and produces the hex before canflash computes the validity record over it.
# The list follows the image directly (no longer at the end of the flash range), so the CAN download stays as long
# as the image. The step stops the build if the list is missing or does not cover every flash byte.
include("${NX_ROOT}/cmake/NxRomCheck.cmake")
# SYS-44: where the family has a ROM check, the application repeats it from its idle handler (re-check period of
# the project, modules.safeMemory.rom_check_period_ms) and brackets its flash writes (NvM) with the flash write
# indicator of the check. NX_ROM_CHECK_IMAGE is set for every consumer by nx_rom_check_image(), only with a tool.
get_property(_nx_rom_check_tool GLOBAL PROPERTY NX_ROM_CHECK_TOOL)
if (_nx_rom_check_tool)
    set_property(GLOBAL APPEND PROPERTY NX_ROM_CHECK_CONSUMERS app.ecu)
    target_link_libraries(app.ecu PRIVATE safeMemory romCheckFlashWrites)
endif ()
# SYS-89: the header of the calibration segment (layout identifier, length, CRC) is written into the ELF after the link
# and before the ROM check computes its reference over the image (cmake/NxCalibration.cmake, tools/calseg)
if (TARGET appCalibration)
    include("${NX_ROOT}/cmake/NxCalibration.cmake")
    nx_calibration_seal(app.ecu SEGMENT_HEX "$<TARGET_FILE_DIR:app.ecu>/app.ecu.cal.hex")
endif ()
nx_rom_check_image(app.ecu HEX ${APP_HEX})
add_custom_command(
    TARGET app.ecu
    POST_BUILD
    COMMAND "${Python3_EXECUTABLE}" "${CANFLASH}" record ${APP_HEX} ${APP_RECORD_HEX}
    COMMAND ${CMAKE_SIZE} $<TARGET_FILE:app.ecu>
    VERBATIM)

# PLAT-18: debug probe of board.debug, tool of the workstation (root CMakeLists.txt, nx_add_flash_target)
nx_add_flash_target(
    flash
    DEPENDS app.ecu
    WHAT "app.ecu.hex and its validity record"
    IMAGES "${APP_HEX}" "${APP_RECORD_HEX}")

set(CAN_INTERFACE
    "vector"
    CACHE STRING "python-can interface for flash-can")
set(CAN_CHANNEL
    "0"
    CACHE STRING "python-can channel for flash-can")
# COM-09: no --wait any more. canflash repeats 10 02 until the bootloader answers (enter_bootloader), so the
# target works with a running application, with the bootloader already active and after a reset - nobody has to
# type "lc bootloader" on the console first. --wait <seconds> is still there for "reset or power-cycle the board".
add_custom_target(
    flash-can
    COMMAND "${Python3_EXECUTABLE}" "${CANFLASH}" flash ${APP_HEX} --interface ${CAN_INTERFACE} --channel
            ${CAN_CHANNEL}
    DEPENDS app.ecu
    COMMENT "Flashing app.ecu.hex via CAN bootloader"
    USES_TERMINAL
    VERBATIM)
