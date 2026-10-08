# XCP on CAN (TOOL-01): protocol engine (xcp, no OpenBSW dependency, host tests in test/), CAN binding to the RTE
# parameters and signals (appXcp) and the A2L of the application (tools/a2lgen, next to app.ecu.elf).
#
# Bus and CAN identifiers - exactly one source for target and A2L: the header xcp/xcpCanConfiguration.h, generated
# by tools/rtegen from project.yaml xcp: (TOOL-26) into ${RTE_GENERATED_DIR}. rtegen writes it for every project;
# without an xcp: section it only defines NX_XCP_MISSING and the application has no XCP slave. rtegen also puts the
# command id into the hardware receive filter of the bus (COM-08), and tools/nxconfig checks the ids with the YAML
# path (range, collisions with the diagnostic ids and the DBC of the bus).
set(NX_XCP_WRITE_TIMEOUT_MS
    "100"
    CACHE STRING "XCP: a write not taken over by the lowest RTE task within this time is dropped (ERR_CMD_BUSY)")

set(NX_XCP_CONFIG_DIR "${RTE_GENERATED_DIR}")
set(NX_XCP_CONFIG_HEADER "${NX_XCP_CONFIG_DIR}/xcp/xcpCanConfiguration.h")
if (NOT EXISTS "${NX_XCP_CONFIG_HEADER}")
    # the application links appXcp only if it exists ($<TARGET_NAME_IF_EXISTS:appXcp>)
    message(STATUS "XCP: off - no ${NX_XCP_CONFIG_HEADER} (tools/rtegen did not run for this project)")
    return()
endif ()
file(STRINGS "${NX_XCP_CONFIG_HEADER}" _xcp_missing REGEX "^#define NX_XCP_MISSING")
if (_xcp_missing)
    message(STATUS "XCP: off - the project has no xcp: section")
    return()
endif ()
file(STRINGS "${NX_XCP_CONFIG_HEADER}" _xcp_values REGEX "^#define NX_XCP_(BUS_NAME|CAN_ID_MASTER|CAN_ID_SLAVE) ")
string(REGEX REPLACE "#define NX_XCP_[A-Z_]+ +" "" _xcp_values "${_xcp_values}")
string(REGEX REPLACE " *//[^;]*" "" _xcp_values "${_xcp_values}")
string(REGEX REPLACE "UL(;|$)" "\\1" _xcp_values "${_xcp_values}")
string(REPLACE "\"" "" _xcp_values "${_xcp_values}")
string(REPLACE ";" ", " _xcp_values "${_xcp_values}")
message(STATUS "XCP: bus and ids from project.yaml xcp: (${_xcp_values})")

# Protocol engine: plain C++17, built unchanged by the host tests
add_library(xcp src/xcp/XcpSlave.cpp)
target_include_directories(xcp PUBLIC include)

# RTE-26: EPK (identity of the build) as xcp::EPK in the image; tools/a2lgen/epk.py runs at every build and rewrites
# the header only when the text changes (project, neXtBSW version, git describe of the project tree)
set(_XCP_EPK_DIR "${CMAKE_CURRENT_BINARY_DIR}/generated")
add_custom_target(
    xcpEpk
    COMMAND "${Python3_EXECUTABLE}" "${NX_ROOT}/tools/a2lgen/epk.py" -o "${_XCP_EPK_DIR}/xcp/xcpEpk.h" --name
            "${NX_PROJECT_NAME}" --tree "${NX_ROOT}" --project "${NX_PROJECT_FILE}"
    BYPRODUCTS "${_XCP_EPK_DIR}/xcp/xcpEpk.h"
    COMMENT "EPK of app.ecu (tools/a2lgen/epk.py)"
    VERBATIM)

add_library(appXcp src/systems/XcpSystem.cpp src/systems/XcpEpk.cpp)
target_include_directories(appXcp PUBLIC include "${NX_XCP_CONFIG_DIR}")
target_include_directories(appXcp PRIVATE "${_XCP_EPK_DIR}")
add_dependencies(appXcp xcpEpk)
target_compile_definitions(appXcp PRIVATE NX_XCP_WRITE_TIMEOUT_MS=${NX_XCP_WRITE_TIMEOUT_MS}U)
target_link_libraries(
    appXcp
    PUBLIC xcp
           cpp2can
           lifecycle
           async
           configuration
           rte
    PRIVATE util etl canSystem)
# SYS-89: the flash segment of the calibration parameters as read-only reference page (ecu/calibration)
if (TARGET appCalibration)
    target_link_libraries(appXcp PRIVATE appCalibration)
endif ()
# SYS-90: a write to a safety-relevant parameter is checked before it takes effect (ecu/calibration)
if (TARGET calibrationPlausibility)
    target_link_libraries(appXcp PRIVATE calibrationPlausibility)
endif ()

# A2L of the application: addresses from the ELF, types and conversion from the RTE tables in it, IF_DATA
# XCP_ON_CAN from the same configuration header. Output next to app.ecu.elf.
set(NX_XCP_A2L_AML
    ""
    CACHE FILEPATH "XCP: optional AML file for /include in the A2L (from the calibration tool; not part of the repo)")
set(_A2L "$<TARGET_FILE_DIR:app.ecu>/app.ecu.a2l")
set(_A2LGEN "${NX_ROOT}/tools/a2lgen/a2lgen.py")
set(_A2LGEN_ARGS
    "$<TARGET_FILE:app.ecu>"
    -o
    "${_A2L}"
    --rte-config
    "${RTE_GENERATED_DIR}/RteConfig.h"
    --xcp-config
    "${NX_XCP_CONFIG_HEADER}"
    --bit-timing
    "${BOARD_GENERATED_DIR}/bsp/can/canBitTimingConfiguration.h"
    --project
    "${NX_PROJECT_FILE}"
    --name
    "${NX_PROJECT_NAME}")
if (NOT NX_XCP_A2L_AML STREQUAL "")
    list(APPEND _A2LGEN_ARGS --aml "${NX_XCP_A2L_AML}")
endif ()
file(GLOB _A2LGEN_INPUTS "${NX_ROOT}/tools/a2lgen/*.py")
add_custom_command(
    OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/app.ecu.a2l.stamp"
    COMMAND "${Python3_EXECUTABLE}" "${_A2LGEN}" ${_A2LGEN_ARGS}
    COMMAND "${CMAKE_COMMAND}" -E touch "${CMAKE_CURRENT_BINARY_DIR}/app.ecu.a2l.stamp"
    DEPENDS app.ecu ${_A2LGEN_INPUTS} "${NX_XCP_CONFIG_HEADER}" "${RTE_GENERATED_DIR}/RteCalibration.json"
    COMMENT "A2L of app.ecu (tools/a2lgen)"
    VERBATIM)
add_custom_target(a2l ALL DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/app.ecu.a2l.stamp")
