add_library(
    safeLifecycle
    src/safeLifecycle/SafetyManager.cpp src/safeLifecycle/IsrHooks.cpp
    src/safeLifecycle/IsrWrapper.cpp
    # SYS-95 (DFA-1, DFA-2, DFA-6 a): progress of the ROM-check passes, the deadman counter and the system time
    src/safeLifecycle/ProgressSupervision.cpp)

target_include_directories(safeLifecycle PUBLIC include)

# SYS-95 (DFA-1): the safety cycle watches the passes of the ROM re-check only in an image that runs it -
# nx_rom_check_image() (cmake/NxRomCheck.cmake) sets NX_ROM_CHECK_IMAGE for every consumer exactly then
set_property(GLOBAL APPEND PROPERTY NX_ROM_CHECK_CONSUMERS safeLifecycle)

# COM-08: IsrWrapper.h declares the CAN message buffer handlers from the generated vector list
# (bsp/can/canDeviceConfiguration.h, which includes the FlexCAN device configuration), public because every user of
# the header (startUp with os/isr/isr_can.cpp, the CAN system) needs the same list.
target_link_libraries(safeLifecycle PUBLIC bspFlexCan)

target_link_libraries(
    safeLifecycle
    PRIVATE safeSupervisor
            safeSystem
            safeIo
            safeWatchdog
            safeUtils
            safeMemory
            safeSupervisionCheck
            util
            bsp
            bspInterrupts
            startUp)

# SYS-130: with a hardware threshold the safety cycle reads its path (hwThreshold/HwThresholdMonitor.h, the generated
# hwThresholdConfiguration.h and ftmPwmConfiguration.h) and reports it to the safe supervisor; without one this
# library is built exactly as before (set by port/bspConfiguration/CMakeLists.txt)
get_property(_nxHwThresholdReport GLOBAL PROPERTY NX_HW_THRESHOLD_REPORT)
if (_nxHwThresholdReport)
    target_link_libraries(safeLifecycle PRIVATE bspConfiguration)
endif ()

# Console group "safeadc" of the SafeSystem (IO-19): its own library outside the MPU protected safety region,
# kept in the image by the call in SafetyManager::init()
if (PLATFORM_SUPPORT_IO)
    target_link_libraries(safeLifecycle PRIVATE adcSafetyConsole)
endif ()
