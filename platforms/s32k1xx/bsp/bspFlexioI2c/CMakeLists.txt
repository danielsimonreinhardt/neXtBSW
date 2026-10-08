# SPDX-License-Identifier: Apache-2.0
# IO-09: I2C master emulated with FlexIO (S32K1xx). Linked only by an integration with an i2c bus on FlexIO.
add_library(bspFlexioI2c src/i2c/FlexioI2cMaster.cpp)

target_include_directories(bspFlexioI2c PUBLIC include)

target_link_libraries(
    bspFlexioI2c
    PUBLIC bspMcu
    PRIVATE bsp)
