# ECU state management (SYS-01): state machine, controller and RTE hand-over, OpenBSW independent
# (host test: test/host). The lifecycle component with console is ecu/application/src/systems/EcuStateSystem.cpp.
add_library(ecuState src/ecuState/EcuStateMachine.cpp src/ecuState/EcuStateController.cpp
                     src/ecuState/EcuStateRte.cpp)

target_include_directories(ecuState PUBLIC include)
# PLAT-28: interrupt lock of the platform for the read-and-clear of the lifecycle merkers (no std::atomic exchange,
# which needs libatomic on ARMv6-M)
target_link_libraries(ecuState PRIVATE bspInterrupts)
