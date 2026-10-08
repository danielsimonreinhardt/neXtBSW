# TMC6200 gate driver (IO-05): hardware independent driver, host test test/host/Tmc6200HostTest.cpp
add_library(gdTmc6200 src/gd/Tmc6200.cpp)

target_include_directories(gdTmc6200 PUBLIC include)
