add_library(bspLpspi src/spi/Lpspi.cpp)

target_include_directories(bspLpspi PUBLIC include)

target_link_libraries(
    bspLpspi
    PUBLIC bspMcu bspSpi
    PRIVATE bsp)
