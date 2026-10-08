# SYS-86: hardware threshold of the family s32k1xx (CMP0 -> TRGMUX -> FTM fault input), header only
# (hwThreshold/HwThreshold.h). Used by the PWM driver of the board configuration (bsp/io/outputPwm/ComplementaryPwm.h)
# when the project has a hardware threshold (generated bsp/hwThreshold/hwThresholdConfiguration.h,
# NX_HW_THRESHOLD_COUNT); added from port/bspConfiguration like bspClock.
# SYS-128: hwThreshold/HwThresholdMonitor.h - start test, register check and consistency of the path, findings latch.
add_library(bspHwThreshold INTERFACE)

target_include_directories(bspHwThreshold INTERFACE include)

target_link_libraries(bspHwThreshold INTERFACE bspMcu)
