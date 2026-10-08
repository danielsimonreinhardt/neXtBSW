# Console commands in the console context (SYS-13, general form of COM-24): the command - parsing, formatting and
# the synchronous UART output - runs in TASK_IDLE, only the short access to a system's state runs in the task that
# owns it (console/ContextCall.h, hardware and OS independent; console/ConsoleContext.h, binding to ::async).
# Header only. Host tests: test/host/*HostTest.cpp (scheduler model with fixed priorities and a UART that costs
# the writing task 86.8 us per character).
# SYS-15: console input that shares the terminal with the log output (console/InputLine.h, pure logic;
# console/InterleavedConsoleInput.h, drop-in for OpenBSW StdioConsoleInput; console/IOutputInterleave.h, the calls
# the logger makes around its lines). Host test: test/host/InputLineHostTest.cpp.
add_library(consoleContext INTERFACE)

target_include_directories(consoleContext INTERFACE include)
