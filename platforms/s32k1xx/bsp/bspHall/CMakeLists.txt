# Hall sensor access of the family s32k1xx (IO-07): pin interrupt of the PORT, time stamp per edge. The evaluation
# is the family neutral libs/bsw/hallDecoder. Everything about a board or a motor comes from the generated
# bsp/hall/hallSensorConfiguration.h and bsp/hall/hallDecoderConfiguration.h (${BOARD_GENERATED_DIR},
# tools/bswgen); without a hall device the generat reports NX_HALL_SENSOR_COUNT 0 and the source compiles to
# nothing (ARCH-17).
add_library(bspHall src/hall/HallSensors.cpp)

target_include_directories(bspHall BEFORE PUBLIC "${BOARD_GENERATED_DIR}")
target_include_directories(bspHall PUBLIC include)

target_link_libraries(
    bspHall
    PUBLIC hallDecoder
           bspPinIrq  # IO-52: the pin interrupt is the family dispatcher's, the Hall pins are one consumer
           bsp        # bsp/timer/SystemTimer.h: the microsecond time base (clock model, ARCH-13)
           bspInterrupts
           bspMcu)
