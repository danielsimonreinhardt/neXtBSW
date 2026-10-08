# Events on digital inputs (IO-53, doc/architecture/pin-interrupts.md variant A): counter, time stamp, qualifier,
# software debounce and rate limit per input - no register, no pin, no clock, no heap. The pin interrupt
# dispatcher of the family (platforms/<family>/bsp, IO-52) calls PinEvents::onEdge(); the generated table and the
# task hook come from the integration (IO-54). Host test: test/host/PinEventHostTest.cpp.
add_library(bspPinEvent src/pinEvent/PinEvent.cpp)

target_include_directories(bspPinEvent PUBLIC include)

# Console group "pinevent" (OpenBSW util::command); linked only by an integration with a console.
add_library(bspPinEventCommand src/pinEvent/PinEventCommand.cpp)

target_link_libraries(bspPinEventCommand PUBLIC bspPinEvent util)
