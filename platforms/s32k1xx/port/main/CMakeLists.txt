# PLAT-20: own hard fault handler variant with the dump inside the no-init RAM of this linker script (the OpenBSW
# handler writes to the fixed address 0x2000EC00, outside the SRAM of the S32K144)
add_subdirectory("${NX_ROOT}/platforms/s32k1xx/hardFaultHandler" nxHardFaultHandler)

# PLAT-21: interrupt priorities as ranks (rank -> NVIC level of the derivative's priority bits). The values come
# from the generated interrupts/interruptPriorities.h (BOARD_GENERATED_DIR, through bspMcu).
add_subdirectory("${NX_ROOT}/platforms/common/interruptPriority" nxInterruptPriority)

# PLAT-85 (open item 1 of PLAT-72): start-up report of the clock check, one console line after staticBsp.init()
# (main.cpp). nxClockCheck() and its contract clock/nxClockCheck.h come from the family target bspClock
# (platforms/s32k1xx/bsp/bspClock), the reporter is family neutral (platforms/common/clockCheck).
add_subdirectory("${NX_ROOT}/platforms/common/clockCheck" nxClockCheckReport)
target_link_libraries(nxClockCheckReport PUBLIC bspClock)

# PLAT-12: startup per core architecture (platforms/s32k1xx/startup/README.md). ARMv6-M (Cortex-M0+) uses the
# template ../../startup/armv6m/startUp.S with the vector table from the derivative's interrupt list
# (generated startup/nxIrqVectors.inc); ARMv7-M keeps the hand written src/bsp/startUp.S of the family port
# until both are merged. The boot header is data only and architecture neutral.
include("${NX_ROOT}/platforms/common/cmake/NxCoreArchitecture.cmake")
nx_core_architecture(_NX_ARCHITECTURE)
if (_NX_ARCHITECTURE STREQUAL "armv6m")
    set(_NX_STARTUP_SOURCE "${NX_ROOT}/platforms/s32k1xx/startup/armv6m/startUp.S")
elseif (_NX_ARCHITECTURE STREQUAL "armv7m")
    set(_NX_STARTUP_SOURCE src/bsp/startUp.S)
else ()
    message(FATAL_ERROR "startUp: no startup template for core '${NX_CORE}' (${_NX_ARCHITECTURE}, "
                        "platforms/s32k1xx/startup/README.md)")
endif ()

add_library(startUpAsm OBJECT src/bsp/bootHeader.S "${_NX_STARTUP_SOURCE}")

set_target_properties(startUpAsm PROPERTIES COMPILE_WARNING_AS_ERROR OFF)
# The interrupt list of the derivative and the generated headers the startup template includes
target_include_directories(startUpAsm PRIVATE "${BOARD_GENERATED_DIR}")
# SYS-170 (SYS69-S1): every external vector and SysTick through its SAFE_ISR wrapper Safe_<handler>
# (safeLifecycle/IsrWrapper.cpp); the ARMv6-M template does so only with this switch, the ARMv7-M startup always
target_compile_definitions(startUpAsm PRIVATE NX_STARTUP_SAFE_ISR)

add_library(
    startUp
    "$<TARGET_OBJECTS:startUpAsm>"
    src/os/isr/isr_can.cpp
    src/os/isr/isr_erm.cpp
    src/os/isr/isr_ftfc.cpp
    src/os/isr/isr_sys.cpp
    src/os/isr/isr_wdg.cpp)

target_link_libraries(
    startUp
    PRIVATE bspMcu
            common
            nxHardFaultHandler
            safeLifecycle
            safeMemory)
# SYS-97: os/IsrStack.h (fill pattern and overflow record of the interrupt stack, defined in isr_sys.cpp)
target_include_directories(startUp PRIVATE include)

# RTOS port of the application (BUILD_TARGET_RTOS, root CMakeLists.txt; PLAT-56/71, ARCH-30 F7). THREADX takes the
# ThreadX parts of the OpenBSW reference application unchanged, as its main/CMakeLists.txt does: SysTick and handler
# priorities (tx_initialize_low_level.S, SysTick for a fixed 80 MHz core clock, checked in main.cpp), the empty
# profiling hook and the OS hooks. Not checked on a board.
if (BUILD_TARGET_RTOS STREQUAL "FREERTOS")
    set(_NX_RTOS_PORT_IMPL nxFreeRtosPortImpl)
elseif (BUILD_TARGET_RTOS STREQUAL "THREADX")
    set(_NX_RTOS_PORT_IMPL threadXCortexM4)
    set(_NX_OPENBSW_S32K148EVB_MAIN "${OPENBSW_DIR}/executables/referenceApp/platforms/s32k148evb/main")
    target_sources(startUpAsm PRIVATE "${_NX_OPENBSW_S32K148EVB_MAIN}/src/bsp/threadx/tx_initialize_low_level.S")
    target_sources(startUp PRIVATE "${_NX_OPENBSW_S32K148EVB_MAIN}/src/bsp/threadx/tx_execution_initialize.c")
    target_link_libraries(startUpAsm PRIVATE asyncCoreConfiguration)
endif ()

add_library(main src/systems/BspSystem.cpp src/lifecycle/StaticBsp.cpp src/main.cpp)
# SYS-113: the cause of the safety reset before this start as a DTC (libs/bsw/dem dem/SafetyResetReport.h), handed to
# the fault memory by the BSP task
target_sources(main PRIVATE src/integration/SafetyResetReport.cpp)
target_link_libraries(main PRIVATE dem)
# SYS-150: console "out" without "set" for the safe_off outputs (systems/SafeOffOutputTester.h)
target_sources(main PRIVATE src/systems/SafeOffOutputTester.cpp)
# PLAT-110: console "pwm" without the unchecked set/setPeriod paths of OpenBSW (systems/CheckedOutputPwmTester.h);
# SYS-162: refuses the channels of the PWM groups of the safe outputs table and is not part of the product image
# (SR-110-1 b+c, cmake/NxProductBuild.cmake, tools/elfcheck product)
if (NOT NX_PRODUCT_BUILD)
    target_sources(main PRIVATE src/systems/CheckedOutputPwmTester.cpp)
endif ()

# PLAT-106: the console commands of the systems (bsp testers, can, sbc, gate driver, cell monitor, i2c, flexnvm ...)
# only in an image with console (project.yaml console.enabled -> NX_CONSOLE, PLAT-27; without a value: on, as
# before). A constructed AsyncCommandWrapper registers its command with the console during static initialization,
# so command and console framework are linked even if no console reads a line. The value is the same as the one of
# app.ecu (ecu/application/CMakeLists.txt), the headers of the systems depend on it. The test build options with
# console commands have no effect without console (nothing could run them); the start-up lines and the line
# SAFETY RESET of the safe supervisor (nxSafeConsole below) do not depend on it.
if (NOT DEFINED NX_CONSOLE OR NX_CONSOLE)
    target_compile_definitions(main PRIVATE NX_CONSOLE=1)
    # SYS-161: console "safeoff show" (take-backs of the hold per safe_off output; constructed in Integration.cpp)
    target_sources(main PRIVATE src/integration/SafeOffHoldCommand.cpp)
else ()
    target_compile_definitions(main PRIVATE NX_CONSOLE=0)
    foreach (_nx_option NX_FLEXNVM_PARTITION_COMMAND NX_DFLASH_COMMAND NX_HARDFAULT_TEST_COMMAND NX_GD_ENABLE_COMMAND
                        NX_SAFETY_FAULT_INJECTION_COMMAND NX_SHUTDOWN_HANG_COMMAND NX_SHORT_LOCK_MEASURE_COMMAND)
        if (${_nx_option})
            message(STATUS "${_nx_option}: no effect without console (project.yaml console.enabled false)")
        endif ()
        set(${_nx_option} OFF)
    endforeach ()
endif ()

# NVM-02/NVM-07: FlexNVM of the family (platforms/s32k1xx/bsp/bspFlexNvm): the EEPROM driver of the NvM (header only,
# no flash command) and the console command "flexnvm show". The partition command itself (PGMPART, IRREVERSIBLE) is
# linked only with the option below, for a test build used with the user at the board - never in the standard build.
add_subdirectory("${NX_ROOT}/platforms/s32k1xx/bsp/bspFlexNvm" nxBspFlexNvm)
target_sources(main PRIVATE src/systems/FlexNvmCommand.cpp)
target_link_libraries(main PRIVATE bspFlexNvm)
# NVM-17: the same test build carries the way back of a partition with CSEc key space - console "csec"
# (platforms/s32k1xx/bsp/bspCsec: LOAD_KEY of the MASTER_ECU_KEY, INIT_RNG at the next start, DBG_CHAL, DBG_AUTH).
# A build that can write a key space can also undo it; a product build refuses the option (cmake/NxProductBuild.cmake)
# and tools/elfcheck product finds both commands in an image (SYS-74).
option(NX_FLEXNVM_PARTITION_COMMAND
       "console commands flexnvm partition and csec (FlexNVM partition and CSEc keys, IRREVERSIBLE; test builds with the user at the board only)"
       OFF)
if (NX_FLEXNVM_PARTITION_COMMAND)
    target_compile_definitions(main PRIVATE NX_FLEXNVM_PARTITION_COMMAND=1)
    target_link_libraries(main PRIVATE bspFlexNvmPartition)
    add_subdirectory("${NX_ROOT}/platforms/s32k1xx/bsp/bspCsec" nxBspCsec)
    target_sources(main PRIVATE src/systems/CsecCommand.cpp)
    target_link_libraries(main PRIVATE bspCsecRecovery)
    message(WARNING "NX_FLEXNVM_PARTITION_COMMAND: this build can write the FlexNVM partition (flexnvm partition, "
                    "IRREVERSIBLE) and load or erase CSEc keys (csec) - test build for the board session with the "
                    "user only")
endif ()

# NVM-23: data flash areas of the project (nvm.data_flash, generated nvm/nvmDataFlash.h): FlexNvmDataFlash, the
# run-down gate and dataflash::AreaService with the write hooks of the ROM check (romCheckFlashWrites, SYS-44), as
# lifecycle component "dflash" (systems/DataFlashSystem.h). The file compiles empty without areas.
target_sources(main PRIVATE src/systems/DataFlashSystem.cpp)
target_link_libraries(main PRIVATE romCheckFlashWrites)
# Console "dflash" (show, read, erase, write) for the board check of NVM-23 - test builds only: erase and write
# change calibration areas of the project.
option(NX_DFLASH_COMMAND "console command dflash (reads, erases and programs the data flash areas; test builds only)"
       OFF)
if (NX_DFLASH_COMMAND)
    target_compile_definitions(main PRIVATE NX_DFLASH_COMMAND=1)
    message(STATUS "NX_DFLASH_COMMAND: test build with the console command dflash (erases and programs data flash)")
endif ()

# ARCH-05: module integration (systems, roles, early-init hooks) selected by the generated
# integration/moduleConfiguration.h and NX_MODULES (integration/modules.cmake, tools/bswgen/modules.py)
target_sources(main PRIVATE src/integration/Integration.cpp)
# SYS-38: Integration.cpp starts the ROM check once the image carries the reference CRC (NX_ROM_CHECK_IMAGE from
# nx_rom_check_image(), cmake/NxRomCheck.cmake)
set_property(GLOBAL APPEND PROPERTY NX_ROM_CHECK_CONSUMERS main)
# SYS-08: role power_supply and run-down steps of the ECU state management (integration/Integration.h exposes
# ecustate types, so the module is public)
target_link_libraries(main PUBLIC ecuState)
# SYS-84: the start-up line of main() reports the reset status of the start from the capture before main()
# (../mcuResetReason, the family's library; linking it here also keeps that capture in every image, not only in
# one with the RTE's BSW services)
target_link_libraries(main PRIVATE bswMcuResetReason)
# SYS-10: logger/Config.h (BufferedLoggerOutputType) is the source of the buffer size the console flush of
# integration/IdleOutputDrain.h derives its time limit from (SYS-09). Only include paths are needed - the
# libraries themselves already hang on the application (ecu/application/CMakeLists.txt); logger and bspInterrupts
# are the headers logger/Config.h itself includes, loggerIntegration links them privately.
target_link_libraries(main PRIVATE loggerIntegration logger bspInterrupts)
# SYS-13: console commands in the console context (console/ConsoleContext.h), also used by ecu/application.
target_link_libraries(main PUBLIC consoleContext)

if (PLATFORM_SUPPORT_CAN)
    # SYS-02 run-down step CAN_STOP, wired in Integration.cpp
    target_sources(main PRIVATE src/systems/CanSystem.cpp src/integration/CanShutdownStep.cpp)
    # COM-08: can/canRxFilterConfiguration.h (receive filter per bus, tools/rtegen)
    target_include_directories(main PRIVATE "${RTE_GENERATED_DIR}")
    target_link_libraries(main PRIVATE canflex2Transceiver canSystem)
    if ("canPhyPassive" IN_LIST NX_MODULES)
        target_link_libraries(main PRIVATE canPhyPassive)
    endif ()
endif ()

if ("sbcUja113x" IN_LIST NX_MODULES)
    # SBC system and SYS-07 early start: RCC compensation before the watchdog fast test, failed-start board
    # test (no-init RAM next to the bootloader's boot request, bl_config.h)
    target_sources(main PRIVATE src/systems/SbcSystem.cpp src/sbc/SbcEarlyStart.cpp)
    target_include_directories(main PRIVATE "${NX_ROOT}/bootloader/include")
    # sbcUja113xPowerSupply: role power_supply of the SBC (Integration.cpp, SYS-01)
    # (the LPSPI driver of the family comes with the signal kind SPI below, not with the SBC)
    target_link_libraries(main PRIVATE sbcUja113x sbcUja113xCanPhy sbcUja113xPowerSupply)
    # RTE-16: SbcSystem provides the BSW service bsw.power from the battery monitor of the SBC (bswServices of
    # ecu/rte, registered here because this directory is read before ecu/rte)
    target_link_libraries(main PRIVATE bswServices)
    set_property(GLOBAL PROPERTY NX_BSW_POWER_PROVIDER sbcUja113x)
endif ()

# ARCH-52/ARCH-66: the modules whose integration is generated (module.yaml binding, integration/moduleBinding.h, today
# the high-side switch): their CMake targets from the generated NX_BINDING_TARGETS (integration/modules.cmake) - one
# generic line instead of one per driver; empty without a bound module.
target_link_libraries(main PRIVATE ${NX_BINDING_TARGETS})

# PLAT-25/ARCH-66: the LPSPI master of the family belongs to the signal kind SPI, not to the SBC. Integration.cpp
# creates one for every SPI device of the project (the gate driver, the SPI binder of the generated binding in
# ModuleBinders.h, the SBC), so it is linked whenever the project uses an SPI bus (NX_USE_SPI, generated next to
# NX_MODULES) - also on a board without an SBC.
if (NX_USE_SPI)
    target_link_libraries(main PRIVATE bspLpspi)
endif ()

if ("gdTmc6200" IN_LIST NX_MODULES)
    # IO-05: TMC6200 gate driver system per instance (console per instance, DRV_EN never switched on by itself)
    target_sources(main PRIVATE src/systems/GateDriverSystem.cpp)
    target_link_libraries(main PRIVATE gdTmc6200)
endif ()

if ("bccMc33771" IN_LIST NX_MODULES)
    # IO-06: MC33771C cell monitor system per instance (console per instance, balancing never switched on by itself)
    # PLAT-114: driver and system are NDA material and not part of the open distribution (tools/distcheck). There a
    # project that names the module stops here with that reason instead of a missing file, and the link names the
    # target only if it exists (generator expression), so the exported tree builds without the module folder.
    if (NOT EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/src/systems/CellMonitorSystem.cpp")
        message(FATAL_ERROR "module bccMc33771 (IO-06) is not part of this tree: the MC33771C driver is NDA "
                            "material and not in the open distribution")
    endif ()
    target_sources(main PRIVATE src/systems/CellMonitorSystem.cpp)
    target_link_libraries(main PRIVATE $<TARGET_NAME_IF_EXISTS:bccMc33771>)
endif ()

# IO-54: console "pinevent" (BspSystem, console context) only for a project with pin_events; the count comes from the
# generated bsp/pinIrq/pinIrqConfiguration.cmake (tools/bswgen), the pin events themselves from bspConfiguration
include("${BOARD_GENERATED_DIR}/bsp/pinIrq/pinIrqConfiguration.cmake")
if (NX_PIN_EVENT_COUNT GREATER 0)
    target_link_libraries(main PRIVATE bspPinEventCommand)
endif ()

# PLAT-12: the processor code cache of OpenBSW (bspCore, cache/cache.cpp) writes LMEM registers. A derivative
# without that cache (mcudb derivative.features without lmem_cache, e.g. every S32K11x - [RM] 33.1 "Variants of
# S32K11x series do not have LMEM") has no LMEM block in its device header, so the file does not even compile.
# main.cpp calls cacheEnable() only with the feature (NX_FEATURE_LMEM_CACHE), so the library is only linked with it.
if ("lmem_cache" IN_LIST NX_FEATURES)
    target_link_libraries(main PRIVATE bspCore)
endif ()

# PLAT-20: console command "hardfault" to trigger a hard fault on purpose, only in test builds
option(NX_HARDFAULT_TEST_COMMAND "console command hardfault (triggers hard faults, test builds only)" OFF)
if (NX_HARDFAULT_TEST_COMMAND)
    target_sources(main PRIVATE src/systems/HardFaultTestCommand.cpp)
    target_compile_definitions(main PRIVATE NX_HARDFAULT_TEST_COMMAND=1)
endif ()

# IO-34: console subcommand "<gd> enable" (DRV_EN on through Tmc6200::enable()), only in test builds - a build
# decision, not a project value. The standard build never switches DRV_EN on through the console. A project without
# gate driver module builds unchanged: the option then has no effect.
option(NX_GD_ENABLE_COMMAND "console command <gd> enable (switches the gate driver bridge on, test builds only)" OFF)
if (NX_GD_ENABLE_COMMAND)
    if ("gdTmc6200" IN_LIST NX_MODULES)
        target_compile_definitions(main PRIVATE NX_GD_ENABLE_COMMAND=1)
    else ()
        message(STATUS "NX_GD_ENABLE_COMMAND: project has no gate driver (module gdTmc6200), option without effect")
    endif ()
endif ()

# SYS-51: console group "inject" - falsifies the LPIT deadman configuration resp. clears WDOG_CS[INT] while running,
# so that the board checks SYS-48 point 2 and SYS-45 B6 can see the reaction of the safety task (a J-Link cannot
# inject either, board 2026-09-22). Only in test builds; the standard build has neither the command nor its code.
# As objects on the link line: nothing references the command, it registers itself during static initialization,
# so an archive member would never be pulled.
option(NX_SAFETY_FAULT_INJECTION_COMMAND
       "console command inject (falsifies the deadman / clears WDOG_CS[INT], ends in a safety reset; test builds only)"
       OFF)
if (NX_SAFETY_FAULT_INJECTION_COMMAND)
    add_library(nxSafetyFaultInjection OBJECT src/integration/SafetyFaultInjectionCommand.cpp)
    target_link_libraries(
        nxSafetyFaultInjection
        PRIVATE asyncConsole
                bspInterrupts
                bspMcu
                consoleContext
                etl
                safeSupervisionCheck
                safeSupervisor
                util)
    # SYS-97: os/IsrStack.h for "inject mspuse" / "inject mspoverflow"
    target_include_directories(nxSafetyFaultInjection PRIVATE include)
    target_link_libraries(main INTERFACE "$<TARGET_OBJECTS:nxSafetyFaultInjection>")
    message(STATUS "NX_SAFETY_FAULT_INJECTION_COMMAND: test build with the console command inject (safety reset)")
endif ()

# SYS-173 board proof (SM-53): console group "shutdownhang" and a lifecycle component at level 2 (Integration.cpp) that
# hangs in its next shutdown when armed - "ecu sleep" then has to end in the direct reset of the ECU state management
# after twice shutdown_timeout_ms. Test builds only; nothing else changes (the component finishes every transition at
# once while not armed).
option(NX_SHUTDOWN_HANG_COMMAND
       "console command shutdownhang (a level-2 component hangs in its next shutdown, ends in the direct reset of SM-53; test builds only)"
       OFF)
if (NX_SHUTDOWN_HANG_COMMAND)
    target_sources(main PRIVATE src/integration/ShutdownHangCommand.cpp)
    target_compile_definitions(main PRIVATE NX_SHUTDOWN_HANG_COMMAND=1)
    message(STATUS "NX_SHUTDOWN_HANG_COMMAND: test build with the console command shutdownhang (SYS-173)")
endif ()

# SYS-172 board proof (open item P3 a): the interrupt lock of the safe state selection measured per call site (DWT
# cycle counter) and the console group "shortlock". Only a project with an active short has the selection
# (NX_SAFE_OUTPUT_SELECT, library nxSafeOutputSelection); in every other project the option has no effect.
# NX_SHORT_LOCK_PROBE_PIN (e.g. PTC8): a GPIO output of the board that is high while the lock is held, for the
# oscilloscope - a board choice of the test build, not a project value; the pin must already be a GPIO output of the
# project (dio.outputs), the measurement only writes its set/clear registers. Empty: no pin.
option(NX_SHORT_LOCK_MEASURE_COMMAND
       "console command shortlock (lock time of the safe state selection, optional probe pin; test builds only)" OFF)
set(NX_SHORT_LOCK_PROBE_PIN "" CACHE STRING "SYS-172 test build: GPIO output (PT<port><n>) high while the lock is held")
if (NX_SHORT_LOCK_MEASURE_COMMAND)
    if (TARGET nxSafeOutputSelection)
        target_compile_definitions(nxSafeOutputSelection PRIVATE NX_SAFE_STATE_LOCK_MEASURE=1)
        if (NX_SHORT_LOCK_PROBE_PIN)
            if (NOT NX_SHORT_LOCK_PROBE_PIN MATCHES "^PT([A-E])([0-9]+)$")
                message(FATAL_ERROR "NX_SHORT_LOCK_PROBE_PIN '${NX_SHORT_LOCK_PROBE_PIN}': expected PT<A..E><n>, "
                                    "e.g. PTC8")
            endif ()
            target_compile_definitions(nxSafeOutputSelection PRIVATE NX_SAFE_STATE_LOCK_PROBE_GPIO=IP_PT${CMAKE_MATCH_1}
                                                                     NX_SAFE_STATE_LOCK_PROBE_BIT=${CMAKE_MATCH_2}U)
        endif ()
        add_library(nxShortLockMeasure OBJECT src/systems/ShortLockMeasureCommand.cpp)
        target_link_libraries(nxShortLockMeasure PRIVATE asyncConsole bspMcu consoleContext etl nxSafeOutputSelection
                                                         util)
        target_link_libraries(main INTERFACE "$<TARGET_OBJECTS:nxShortLockMeasure>")
        message(STATUS "NX_SHORT_LOCK_MEASURE_COMMAND: test build with the console command shortlock (SYS-172), "
                       "probe pin '${NX_SHORT_LOCK_PROBE_PIN}'")
    else ()
        message(STATUS "NX_SHORT_LOCK_MEASURE_COMMAND: project without active short (NX_SAFE_OUTPUT_SELECT), option "
                       "without effect")
    endif ()
endif ()

target_include_directories(main PUBLIC include)

# PLAT-45/66: newlib system calls without link warning (newer newlib references _close/_lseek/_read/_write), shared
# with bootloader and updater (platforms/common/newlib). As objects on the link line of the executable, not as archive
# member: a member would not be pulled for symbols the C library references after this archive was scanned.
add_library(nxNewlibSyscalls OBJECT "${NX_ROOT}/platforms/common/newlib/nx_newlib_syscalls.c")
target_link_libraries(main INTERFACE "$<TARGET_OBJECTS:nxNewlibSyscalls>")

# SYS-39: safety::safeConsoleWrite() (safeSupervisor/SafeConsole.h), the synchronous console line of the safe
# supervisor's hold. As objects on the link line for the same reason: its only caller, the safeSupervisor archive,
# comes after this archive, so an archive member would not be pulled (undefined reference).
add_library(nxSafeConsole OBJECT src/safeConsole/SafeConsole.cpp)
target_link_libraries(nxSafeConsole PRIVATE bspUart bspConfiguration etl safeSupervisor)
target_link_libraries(main INTERFACE "$<TARGET_OBJECTS:nxSafeConsole>")

# DOC-28 (dev-environment.md 4.6 P5, finding DE5): own operator delete (both forms) into the fault reaction, so
# libstdc++'s version and with it newlib's allocator are no longer linked (tools/elfcheck: heap-symbol). As objects
# on the link line: an archive member would lose against libstdc++ for references of archives scanned after it.
add_library(nxNoHeapOperatorDelete OBJECT src/cxxRuntime/NoHeapOperatorDelete.cpp)
target_link_libraries(nxNoHeapOperatorDelete PRIVATE bspMcu etl safeSupervisor)
target_link_libraries(main INTERFACE "$<TARGET_OBJECTS:nxNoHeapOperatorDelete>")

target_link_libraries(
    main
    PRIVATE asyncConsole
            bsp
            bspClock
            nxClockCheckReport
            bspUart
            bspConfiguration
            configuration
            etl
            lifecycle
            nxHardFaultHandler
            startUp
            util
            safeBspMcuWatchdog
            safeLifecycle
            safeSupervisor
            # SYS-48: integration/DebugHaltGuardSystem.h defines deadmanExpectedConfiguration() (LpitDeadmanCheck.h)
            safeSupervisionCheck
            safeMemory
            safeWatchdog
            ${_NX_RTOS_PORT_IMPL}
            nxInterruptPriority
    PUBLIC bspInputManager bspOutputManager)

if (BUILD_TARGET_RTOS STREQUAL "FREERTOS")
    add_library(osHooks src/osHooks/freertos/osHooks.cpp)
    target_include_directories(osHooks PRIVATE include)
    target_link_libraries(osHooks PRIVATE common nxFreeRtosPortImpl)
elseif (BUILD_TARGET_RTOS STREQUAL "THREADX")
    # PLAT-71: OS hooks of the reference application, used unchanged (execution change notify -> async hooks)
    add_library(osHooks "${_NX_OPENBSW_S32K148EVB_MAIN}/src/osHooks/threadx/osHooks.cpp")
    target_include_directories(osHooks PRIVATE "${_NX_OPENBSW_S32K148EVB_MAIN}/include/osHooks/threadx")
    target_link_libraries(osHooks PRIVATE common threadXCortexM4 asyncThreadX threadXConfiguration)
endif ()

set(LINKERSCRIPT_SOURCE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/linkerscript")
set(LINKERSCRIPT_BINARY_DIR "${CMAKE_CURRENT_BINARY_DIR}/linkerscript")
set(GENERATED_LINKER_SCRIPT "${LINKERSCRIPT_BINARY_DIR}/application.dld")
set(HOT_FILE_LIST "${LINKERSCRIPT_SOURCE_DIR}/hot-files.inc")
set(HARD_FAULT_DUMP_INCLUDE_DIR "${NX_ROOT}/platforms/s32k1xx/hardFaultHandler/include")
# ARCH-08: memory layout generated from derivative + project memory (same source as the bootloader)
set(MEMORY_LAYOUT_HEADER "${BOARD_GENERATED_DIR}/memory/memoryLayout.h")
if (NOT EXISTS "${MEMORY_LAYOUT_HEADER}")
    message(FATAL_ERROR "${MEMORY_LAYOUT_HEADER} missing: project ${NX_PROJECT_NAME} needs the memory partitioning "
                        "(project memory) for the application linker script")
endif ()

add_custom_command(
    OUTPUT "${GENERATED_LINKER_SCRIPT}"
    COMMAND ${CMAKE_COMMAND} -E make_directory "${LINKERSCRIPT_BINARY_DIR}"
    COMMAND
        ${CMAKE_C_COMPILER} -E -P -x c -I "${LINKERSCRIPT_SOURCE_DIR}" -I "${HARD_FAULT_DUMP_INCLUDE_DIR}" -I
        "${BOARD_GENERATED_DIR}" "${LINKERSCRIPT_SOURCE_DIR}/application.dld.in" -o "${GENERATED_LINKER_SCRIPT}"
    DEPENDS "${LINKERSCRIPT_SOURCE_DIR}/application.dld.in" "${HOT_FILE_LIST}"
            "${HARD_FAULT_DUMP_INCLUDE_DIR}/hardFaultHandler/hardFaultDumpLayout.h" "${MEMORY_LAYOUT_HEADER}"
    VERBATIM)

add_custom_target(generateStartUpLinkerScript
                  DEPENDS "${GENERATED_LINKER_SCRIPT}")

add_dependencies(startUp generateStartUpLinkerScript)

set_target_properties(startUp PROPERTIES PROP_LINKER_SCRIPT
                                         "${GENERATED_LINKER_SCRIPT}")
set_target_properties(startUp PROPERTIES LINK_DEPENDS "${GENERATED_LINKER_SCRIPT}")
