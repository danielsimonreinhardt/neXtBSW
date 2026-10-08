# Start-up report of the clock check (PLAT-72, README.md): one console line from the family's nxClockCheck().
#
# The contract clock/nxClockCheck.h and its implementation come from the family, not from this target (platform
# rule P2: platforms/<family> does not depend on platforms/common):
#   stm32   nxBspClock        (platforms/stm32/bsp/bspClock)
#   s32k1xx bspClock          (platforms/s32k1xx/bsp/bspClock, replaces the sources of the OpenBSW target)
# The integration adds this directory and links the family target, e.g.
#   add_subdirectory(platforms/common/clockCheck EXCLUDE_FROM_ALL)
#   target_link_libraries(nxClockCheckReport PUBLIC bspClock)

add_library(nxClockCheckReport src/nxClockCheckReport.cpp)

target_include_directories(nxClockCheckReport PUBLIC include)
