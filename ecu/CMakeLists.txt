# PLAT-23 (finding ARCH-09 B6): the port of a family is a template, not board code. NX_FAMILY_PORT_DIR
# (platforms/<family>/port, set by the root CMakeLists.txt) brings bspConfiguration, safety, main and
# freeRtosCoreConfiguration for every board of the family; the board port ecu/platforms/<board> is Options.cmake
# plus its own board peculiarities and justified exceptions. It is added after the template so that it can add or
# replace targets, and it is optional - a board without peculiarities has no CMakeLists.txt at all.
add_subdirectory("${NX_FAMILY_PORT_DIR}" platforms/familyPort)
if (EXISTS "${ECU_PLATFORM_DIR}/CMakeLists.txt")
    add_subdirectory(platforms/${OPENBSW_TARGET})
endif ()

# async and RTOS libraries by BUILD_TARGET_RTOS, as in the OpenBSW reference application (PLAT-56)
if (BUILD_TARGET_RTOS STREQUAL "FREERTOS")
    add_library(asyncPlatform ALIAS asyncFreeRtos)
    add_library(osRtos ALIAS freeRtos)
    add_library(asyncRtosImpl ALIAS asyncFreeRtosImpl)
elseif (BUILD_TARGET_RTOS STREQUAL "THREADX")
    add_library(asyncPlatform ALIAS asyncThreadX)
    add_library(osRtos ALIAS threadX)
    add_library(asyncRtosImpl ALIAS asyncThreadXImpl)
endif ()

# SYS-89: calibration segment (memory.calibration); before application, which links appCalibration if it exists
add_subdirectory(calibration)
add_subdirectory(application)
# PLAT-56 (ARCH-30 F7): Ethernet/lwIP stays includable (NX_ETHERNET, root CMakeLists.txt; off by default). The lwIP
# configuration is the unchanged one of the OpenBSW reference application (lwipopts.h, rng); the stack is linked
# into the application. The MAC/PHY driver system (OpenBSW S32K148EvbEthernetSystem, bspEthernet, bspTja1101) is
# not integrated - it belongs to a board with an Ethernet PHY.
if (PLATFORM_SUPPORT_ETHERNET)
    add_subdirectory("${OPENBSW_DIR}/executables/referenceApp/lwipConfiguration" openbsw/lwipConfiguration)
    # the logger of the application (logger.cpp) names the components of all of them, as in the reference app
    target_link_libraries(app.ecu PRIVATE cpp2ethernet cpp2someip lwipSocket lwipcore)
    if (PLATFORM_SUPPORT_TRANSPORT)
        target_link_libraries(app.ecu PRIVATE doip)
    endif ()
endif ()
add_subdirectory(asyncBinding)
add_subdirectory(asyncCoreConfiguration)
add_subdirectory(configuration)
add_subdirectory(consoleCommands)
add_subdirectory(rte)
add_subdirectory(safety)
