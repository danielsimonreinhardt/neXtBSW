# The hardware independent parts of the CAN system: the transmit gate (CanPhyGate, host test
# test/host/CanPhyGateHostTest.cpp) and the bus state with its bus off counters (CanBusState.h, host test
# test/host/CanBusStateHostTest.cpp, COM-12) and the supervision cycle of both (CanSupervisionCycle.h, latency
# model in test/host/CanPhyGateHostTest.cpp, COM-22), the reports of the transceiver readiness from the phy providers
# (CanPhyReadiness.h, COM-23: the gate follows a report at once, the cycle is back at 10 ms; same host test), and
# the split of the console commands (COM-24): the status
# text (CanChannelStatus.h) and the call into the CAN task from the console context (ContextCall.h), host test
# test/host/CanConsoleHostTest.cpp, and the lines of "can log" (CanFrameLog.h, COM-25: each line fits one logger
# entry, CAN FD in lines of 16 bytes), host test test/host/CanFrameLogHostTest.cpp, and the behaviour after bus off
# (CanBusOffRecovery.h, COM-35, moved here from the s32k1xx port with COM-37; values per bus from project.yaml
# can.<bus>.bus_off, generated as bsp/can/canBusOffConfiguration.h), host test test/host/CanBusOffRecoveryHostTest.cpp,
# and bus-off as a source of the fault memory (CanBusOffReport.h, COM-46: event can.busoff.<bus>, libs/bsw/dem),
# host test test/host/CanBusOffReportHostTest.cpp.
# Header only; the buses
# themselves live in the board port (platforms/<family>/port, ARCH-07), which reads the controller and feeds the
# state in.
add_library(canSystem INTERFACE)

target_include_directories(canSystem INTERFACE include)

target_link_libraries(canSystem INTERFACE bsp consoleContext dem)
