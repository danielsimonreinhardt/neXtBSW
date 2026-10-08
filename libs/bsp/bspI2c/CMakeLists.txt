# SPDX-License-Identifier: Apache-2.0
# IO-28: I2C master contract (header only). Added by the root module list and, until the coordinator puts it there,
# by the family port that implements it (platforms/s32k1xx/port/bspConfiguration) - the guard keeps the second
# add_subdirectory a no-op, whichever comes first.
if (TARGET bspI2c)
    return()
endif ()

add_library(bspI2c INTERFACE)

target_include_directories(bspI2c INTERFACE include)
