# Hardware independent state machine and channel (host test: test/host/HighSideSwitchHostTest.cpp)
add_library(highSideSwitch src/highSideSwitch/HighSideSwitch.cpp src/highSideSwitch/HighSideSwitchChannel.cpp)

target_include_directories(highSideSwitch PUBLIC include)

# Lifecycle component with console command (OpenBSW lifecycle, async, console, logger). IO-42: the command runs in
# the console context (consoleContext, SYS-13), only the snapshot runs in the task of the switch.
add_library(highSideSwitchSystem src/highSideSwitch/HighSideSwitchSystem.cpp)

target_link_libraries(
    highSideSwitchSystem
    PUBLIC highSideSwitch
           asyncConsole
           consoleContext
           lifecycle
           util)

# PLAT-106: the console command only in an image with console (project.yaml console.enabled -> NX_CONSOLE, PLAT-27;
# without a value: on). Public: the integration that creates the system (family port main) includes the same header.
if (NOT DEFINED NX_CONSOLE OR NX_CONSOLE)
    target_compile_definitions(highSideSwitchSystem PUBLIC NX_CONSOLE=1)
else ()
    target_compile_definitions(highSideSwitchSystem PUBLIC NX_CONSOLE=0)
endif ()
