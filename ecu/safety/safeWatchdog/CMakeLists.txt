# SYS-40: CyclePeriodCheck - the hardware independent part of the safety task's period check (SM-04)
# SYS-95: ProgressChecks - progress of the ROM-check passes (DFA-1) and of the system time (DFA-6)
add_library(safeWatchdog src/safeWatchdog/SafeWatchdog.cpp src/safeWatchdog/CyclePeriodCheck.cpp
                         src/safeWatchdog/ProgressChecks.cpp)

target_include_directories(safeWatchdog PUBLIC include)

target_link_libraries(safeWatchdog PRIVATE safeSupervisor safeUtils)

if (PLATFORM_SUPPORT_WATCHDOG)
    # SYS-41: safeSupervisionCheck of the family port - watchdog control fields beyond the driver's check
    target_link_libraries(safeWatchdog PRIVATE bspMcu safeBspMcuWatchdog safeSupervisionCheck)
endif ()
