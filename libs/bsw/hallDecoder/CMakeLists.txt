# Hall sensor evaluation (IO-07): sector table, direction, debouncing, period, standstill and quality - no MCU, no
# register, no clock (doc/architecture/treiber-granularitaet.md). The access of a family (platforms/<family>/bsp/
# bspHall) links it. Host test: test/host/HallDecoderHostTest.cpp.
add_library(hallDecoder src/hallDecoder/HallDecoder.cpp)

target_include_directories(hallDecoder PUBLIC include)
