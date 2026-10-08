add_library(safeIo src/safeIo/SafeIo.cpp src/safeIo/SafeState.cpp src/safeIo/SafeStateDegraded.cpp)

target_include_directories(safeIo PUBLIC include)

# SYS-24: the register list SafeIo locks and checks, generated from the configuration (tools/bswgen,
# bsp/safeIo/safeIoConfiguration.h, found through bspMcu). Its own library, because the linker script allows no
# initialised read-only data in the safety libraries ("*libsafe*", .mpu_rodata must stay empty); the list lies in
# flash like the safe outputs table.
add_library(nxSafeIoRegisterList src/registerList/SafeIoRegisterList.cpp
            src/registerList/SafeOutputReadbackList.cpp) # SYS-30: read-back list of the safe outputs table
target_include_directories(nxSafeIoRegisterList PUBLIC include)
target_link_libraries(nxSafeIoRegisterList PRIVATE bspMcu)

# SYS-06: nxHardFaultHandler carries the safe outputs table of the project (hardFaultHandler/safeOutputs.h and the
# weak empty default of nxSafeOutputs). The safe state switches off the same table as the fault handlers, so the
# dependency is deliberate and there is no second list. The target is defined in port/main, which CMake adds after
# this directory - the name is resolved when the build system is generated.
target_link_libraries(safeIo PRIVATE safeSupervisor bspIo nxHardFaultHandler nxSafeIoRegisterList)
