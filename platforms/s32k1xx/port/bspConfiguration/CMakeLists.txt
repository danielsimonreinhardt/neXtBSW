add_library(
    bspConfiguration
    src/bsp/adc/AnalogInput.cpp
    src/bsp/capture/InputCapture.cpp
    src/bsp/pinIrq/PinIrqConfig.cpp
    src/bsp/stdIo/stdIo.cpp
    src/bsp/SystemTimer/SystemTimer.cpp
    src/bsp/uart/UartConfig.cpp)

target_include_directories(bspConfiguration BEFORE PUBLIC "${BOARD_GENERATED_DIR}")
target_include_directories(bspConfiguration PUBLIC include)
# PLAT-85: the clock driver of the family with bounded waits and the start-up check nxClockCheck() (PLAT-72, until
# then in this library) replace the translation unit of the OpenBSW target bspClock - platforms/s32k1xx/bsp/bspClock.
# Added from here because this is the first directory of the family read after the OpenBSW platform; its place is
# platforms/s32k1xx/CMakeLists.txt next to the other adjustments of the family (proposal in doc/changelog.d/PLAT-85.md).
# The console line comes from platforms/common/clockCheck (nxClockCheckReport), linked by port/main.
add_subdirectory("${CMAKE_CURRENT_LIST_DIR}/../../bsp/bspClock" nxBspClock)

target_link_libraries(
    bspConfiguration
    PUBLIC bspAdc
           bspAdcAccess
           bspCharInputOutput
           bspFtm
           bspFtmPwmGroup # IO-61: StaticBsp.h includes ComplementaryPwm.h, which uses ftmPwm/FtmPwmGroup.h
           bspInterrupts
           bspMcu
           bspOutputPwm
           bspPinEdge # IO-52: bsp/pinIrq/PinIrqConfig.h names the pin edge contract (header only)
           bspUart)

# PLAT-24 (finding ARCH-09 B8): the SBC driver and the LPSPI driver of the family are needed by exactly one
# file of this library, the public header include/bsp/sbc/sbcConfiguration.h. It belongs to the module
# sbcUja113x, so the targets follow the used modules of the project like in main/CMakeLists.txt
# (NX_MODULES/NX_MODULE_TARGETS, integration/modules.cmake, tools/bswgen/modules.py). On a board without SBC and
# without SPI nothing of the application depends on them any more; the header itself refuses to be included in
# that case.
# PLAT-25: the two targets are switched apart, because the LPSPI driver belongs to the **signal kind** SPI of
# the family, not to the SBC. NX_USE_SPI (generated next to NX_MODULES) is the list of SPI buses the project
# uses, so a board with another SPI device and without an SBC links the family driver here as well; the module
# of the device stays family neutral (it only knows libs/bsp/bspSpi). Both libraries are only built when a
# target links them: the root CMakeLists.txt adds the module directories with EXCLUDE_FROM_ALL.
if (NX_USE_SPI)
    target_link_libraries(bspConfiguration PUBLIC bspLpspi)
endif ()

# SYS-86: hardware threshold of the family (CMP0 -> TRGMUX -> FTM fault input, header only), included by the PWM driver
# bsp/io/outputPwm/ComplementaryPwm.h only when the generated NX_HW_THRESHOLD_COUNT is not 0.
add_subdirectory("${CMAKE_CURRENT_LIST_DIR}/../../bsp/bspHwThreshold" nxBspHwThreshold)
target_link_libraries(bspConfiguration PUBLIC bspHwThreshold)
# SYS-130: with a hardware threshold the safety task reports its shut-off path to the safe supervisor
# (safety/safeLifecycle, SafetyManager.cpp) - read from the generat, which tools/bswgen writes before this runs.
# NX_SAFE_SUPERVISOR_HW_SHUTOFF: ecu/safety/safeSupervisor/CMakeLists.txt; NX_HW_THRESHOLD_REPORT: safeLifecycle.
set(_nxHwThresholdHeader "${BOARD_GENERATED_DIR}/bsp/hwThreshold/hwThresholdConfiguration.h")
if (EXISTS "${_nxHwThresholdHeader}")
    file(STRINGS "${_nxHwThresholdHeader}" _nxHwThresholdCount REGEX "^#define NX_HW_THRESHOLD_COUNT [1-9]")
    if (_nxHwThresholdCount)
        set_property(GLOBAL PROPERTY NX_SAFE_SUPERVISOR_HW_SHUTOFF ON)
        set_property(GLOBAL PROPERTY NX_HW_THRESHOLD_REPORT ON)
    endif ()
endif ()

# IO-28: the I2C buses of the project (NX_USE_I2C, generated like NX_USE_SPI) as contract objects of libs/bsp/bspI2c
# over the FlexIO I2C master of the family, plus the console group "i2c". The contract directory is added here as
# long as the module list of the root CMakeLists.txt does not name it; its CMakeLists.txt makes a second
# add_subdirectory a no-op.
if (NX_USE_I2C)
    add_subdirectory("${NX_ROOT}/libs/bsp/bspI2c" nxBspI2c)
    target_sources(bspConfiguration PRIVATE src/bsp/i2c/FlexioI2cBuses.cpp src/bsp/i2c/I2cBuses.cpp
                                            src/bsp/i2c/I2cCommand.cpp)
    target_link_libraries(bspConfiguration PUBLIC bspI2c PRIVATE bspFlexioI2c)
endif ()

# COM-26: FlexCAN driver for instances with 16 message buffers (FlexCAN1/2 of the S32K144, FlexCAN1 of the S32K142,
# FlexCAN2 of the S32K146). OpenBSW stays unchanged; like the adjustments in platforms/s32k1xx/CMakeLists.txt
# (PLAT-12) the translation unit of its target bspFlexCan is replaced by a changed copy, and the copy's header is put
# in front of the OpenBSW one for bspFlexCan and everything that links it (CanFlex2Transceiver, the CAN system).
# bspFlexCan/ holds the copy (tools/openbswsync, root bspFlexCan-s32k1xx, status modified); the generated device
# configuration refuses the OpenBSW header (NX_FLEXCAN_MESSAGE_BUFFER_LAYOUT), so a wrong include order cannot
# compile silently into the fixed 32-buffer split.
if (TARGET bspFlexCan)
    set_property(TARGET bspFlexCan PROPERTY SOURCES
                 "${CMAKE_CURRENT_LIST_DIR}/bspFlexCan/src/can/FlexCANDevice.cpp")
    target_include_directories(bspFlexCan BEFORE PUBLIC "${CMAKE_CURRENT_LIST_DIR}/bspFlexCan/include")
endif ()
if ("sbcUja113x" IN_LIST NX_MODULES)
    target_link_libraries(bspConfiguration PUBLIC sbcUja113x)
endif ()

# IO-07/IO-52/IO-54: src/bsp/pinIrq/PinIrqConfig.cpp defines the pin interrupt handlers through the family dispatcher
# bspPinIrq and starts its consumers: the hall sensor sets (family driver bspHall, only when the project has one -
# NX_HALL_SENSOR_COUNT > 0, same condition as NX_MODULES) and the pin events of project pin_events (family adapter
# bspPinIrqPinEvent with libs/bsp/bspPinEvent). The dispatcher is linked only when a pin has a consumer; the counts
# come from the generated bsp/pinIrq/pinIrqConfiguration.cmake (tools/bswgen).
include("${BOARD_GENERATED_DIR}/bsp/pinIrq/pinIrqConfiguration.cmake")
if ("hall" IN_LIST NX_MODULES)
    target_link_libraries(bspConfiguration PUBLIC bspHall)
endif ()
if (NX_PIN_IRQ_PIN_COUNT GREATER 0)
    target_link_libraries(bspConfiguration PUBLIC bspPinIrq)
endif ()
if (NX_PIN_EVENT_COUNT GREATER 0)
    target_link_libraries(bspConfiguration PUBLIC bspPinIrqPinEvent)
endif ()

# Lock policy of the serialized ADC access (IO-14/IO-16/IO-19). The project decides it
# (modules.bspAdcAccess.lock_policy -> NX_ADC_ACCESS_POLICY, generated by tools/bswgen); the option is a build
# time override for a comparison build without touching the project. Only the FreeRTOS variant
# (adc::FreeRtosAdcLock) needs freeRtos, so the default build (interrupt lock) links nothing extra.
include("${BOARD_GENERATED_DIR}/bsp/adc/adcAccessConfiguration.cmake")
option(BSP_ADC_ACCESS_RTOS_MUTEX
       "Serialize ADC conversions with a FreeRTOS mutex instead of an interrupt lock (overrides the project)" OFF)
if (BSP_ADC_ACCESS_RTOS_MUTEX OR NX_ADC_ACCESS_POLICY STREQUAL "rtos_mutex")
    message(STATUS "ADC access: FreeRTOS mutex, ${NX_ADC_ACCESS_LOCK_TIMEOUT_MS} ms timeout "
                   "(project policy ${NX_ADC_ACCESS_POLICY})")
    if (BSP_ADC_ACCESS_RTOS_MUTEX)
        target_compile_definitions(bspConfiguration PRIVATE BSP_ADC_ACCESS_RTOS_MUTEX)
    endif ()
    target_link_libraries(bspConfiguration PUBLIC freeRtos freeRtosCoreConfiguration)
else ()
    message(STATUS "ADC access: interrupt lock per conversion of ${NX_ADC_CONVERSION_NS} ns, "
                   "poll limit ${NX_ADC_POLL_LIMIT}")
endif ()
