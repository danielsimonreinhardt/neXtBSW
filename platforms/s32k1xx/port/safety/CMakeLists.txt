add_subdirectory(safeLifecycle)
add_subdirectory(safeMemory)
add_subdirectory(safeSystem)
add_subdirectory(safeIo)
add_subdirectory(safeSupervisionCheck)      # SYS-41: re-checks of watchdog control fields and deadman
if (PLATFORM_SUPPORT_IO)
    add_subdirectory(adcSafetyConsole)          # console group "safeadc" (IO-19), outside the safety region
endif ()
