# SYS-41 (finding F12 of doc/safety/fmea.md): configuration re-checks of the supervision peripherals of the family -
# the watchdog control fields the OpenBSW driver's check leaves out (SafeWatchdog) and the LPIT deadman of the
# debug-halt guard (SafetyManager). SYS-40: the LPTMR as the independent time base of the safety task's period
# check. Register access only; no data of its own (libsafe* archive: data would land in the MPU protected region).
add_library(safeSupervisionCheck src/safeSupervisionCheck/WatchdogControlCheck.cpp
                                 src/safeSupervisionCheck/LpitDeadmanCheck.cpp
                                 src/safeSupervisionCheck/LptmrCycleTimer.cpp
                                 # SYS-95 (DFA-2): does the deadman's counter move?
                                 src/safeSupervisionCheck/LpitDeadmanProgress.cpp)

target_include_directories(safeSupervisionCheck PUBLIC include)

target_link_libraries(safeSupervisionCheck PUBLIC bspMcu)
