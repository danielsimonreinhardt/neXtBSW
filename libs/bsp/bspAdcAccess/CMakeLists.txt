# Serialized ADC access (IO-14): AdcAccess.h and InterruptLockAdcLock.h need only OpenBSW bsp and
# bspInterrupts. FreeRtosAdcLock.h additionally needs the consumer to link freeRtos and to name the interrupt
# context of its platform (ARCH-26, no core registers in this contract).
add_library(bspAdcAccess INTERFACE)

target_include_directories(bspAdcAccess INTERFACE include)

target_link_libraries(bspAdcAccess INTERFACE bsp bspInterrupts)
