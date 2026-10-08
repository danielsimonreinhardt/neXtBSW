# Pin-interrupt dispatcher of the family s32k1xx (IO-52, doc/architecture/pin-interrupts.md section 3.1): owns the
# pin-detect vectors (S32K14x one per port, S32K11x one for all ports), clears only the flags of pins with a consumer
# and calls the consumer (libs/bsp/bspPinEdge) with the microsecond time. The table is a compile-time constant of the
# port configuration (bspConfiguration/src/bsp/pinIrq/PinIrqConfig.cpp, generated bsp/pinIrq/pinIrqConfiguration.h);
# linked only when a pin has a consumer.
add_library(bspPinIrq src/pinIrq/PinIrqDispatcher.cpp)

target_include_directories(bspPinIrq PUBLIC include)

target_link_libraries(
    bspPinIrq
    PUBLIC bspPinEdge
           bsp # bsp/timer/SystemTimer.h: the microsecond time base (clock model, ARCH-13)
           bspMcu)

# IO-54: family end of the pin events (libs/bsp/bspPinEvent): consumer of the dispatcher and pin access of the module
# (GPIO PDIR with the inversion of the board, edges through the dispatcher). Linked only by a project with pin_events.
add_library(bspPinIrqPinEvent src/pinIrq/PinEventAdapter.cpp)

target_link_libraries(bspPinIrqPinEvent PUBLIC bspPinIrq bspPinEvent)
