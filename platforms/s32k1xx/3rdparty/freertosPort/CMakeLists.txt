# Moved (ARCH-25): the FreeRTOS port selection is profile neutral and lives in platforms/common/3rdparty/freertosPort.
# This directory only forwards to it for callers that still name the old place (platforms/stm32/test/buildtest,
# tools/nxconfig/test_build_integration.py); remove it once they point at platforms/common.

add_subdirectory("${CMAKE_CURRENT_LIST_DIR}/../../../common/3rdparty/freertosPort" common/freertosPort)
