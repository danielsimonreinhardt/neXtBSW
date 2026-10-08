# PWM group of the family s32k1xx (IO-61): channel values for edge and up-down counting, trigger channel and the
# write of all duties in one synchronisation step (ftmPwm/FtmPwmGroup.h, header only). The configuration comes from
# the generated bsp/io/outputPwm/ftmPwmConfiguration.h (${BOARD_GENERATED_DIR}, tools/bswgen). Host test:
# test/test_ftm_pwm_group.py (register model, own profiles, mutants).
add_library(bspFtmPwmGroup INTERFACE)

target_include_directories(bspFtmPwmGroup BEFORE INTERFACE "${BOARD_GENERATED_DIR}")
target_include_directories(bspFtmPwmGroup INTERFACE include)

target_link_libraries(bspFtmPwmGroup INTERFACE bspFtm bspMcu)
