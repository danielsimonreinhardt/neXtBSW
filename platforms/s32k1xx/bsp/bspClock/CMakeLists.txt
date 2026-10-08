# Clock driver of the family s32k1xx (PLAT-85) - replaces the translation unit of the OpenBSW target bspClock.
#
# OpenBSW defines bspClock (external/openbsw/platforms/s32k1xx/bsp/bspClock, added by ../../CMakeLists.txt with the
# unchanged OpenBSW platform). Its driver waits without a time limit for every clock it starts (finding PLAT-72): a
# missing crystal stops the start-up in boardInit(), where the MCU watchdog is already disabled. The target keeps its
# name, its public header clock/clockConfig.h and its links (bspConfiguration for bsp/clock/boardClock.h, bspMcu,
# etl); only the sources are replaced, as for bspCore and bspFlexCan (PLAT-12, COM-26):
#
#   src/clockConfig.cpp  copy of the OpenBSW driver with bounded waits (tools/openbswsync/copies.yaml, root
#                        bspClock-s32k1xx, status modified)
#   src/ClockCheck.cpp   nxClockCheck() of the family (PLAT-72, contract include/clock/nxClockCheck.h), reads what
#                        the driver left in the SCG
#
# The forced include clock/nxSpllUnavailable.h for derivatives without SPLL (../../CMakeLists.txt, item 2) stays a
# compile option of the target and applies to the copy the same way.
#
# Added through add_subdirectory() after the OpenBSW platform, today from ../../port/bspConfiguration/CMakeLists.txt
# (proposal: from ../../CMakeLists.txt next to the other adjustments of the family, doc/changelog.d/PLAT-85.md).

if (NOT TARGET bspClock)
    message(FATAL_ERROR "platforms/s32k1xx/bsp/bspClock: the OpenBSW target bspClock must exist before (add this "
                        "directory after the OpenBSW platform external/openbsw/platforms/s32k1xx)")
endif ()

set_property(TARGET bspClock PROPERTY SOURCES "${CMAKE_CURRENT_LIST_DIR}/src/clockConfig.cpp"
                                              "${CMAKE_CURRENT_LIST_DIR}/src/ClockCheck.cpp")
target_include_directories(bspClock PUBLIC "${CMAKE_CURRENT_LIST_DIR}/include")
message(STATUS "s32k1xx: bspClock with bounded clock start-up waits and nxClockCheck (PLAT-85)")
