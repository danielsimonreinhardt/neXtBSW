# SYS-89: calibration segment of the application (stage K3 of doc/architecture/kalibrierung.md).
#
# Both libraries exist only for a project with memory.calibration (tools/bswgen writes NX_MEMORY_CAL_START into
# memory/memoryLayout.h then); every other build is unchanged. calibrationSegment: header layout, CRC, start-up
# check and reaction - plain C++, also built by the host test (test/, tools/test calibration-segment).
# appCalibration: the target side (linker symbols of the segment, working page, console "cal"). The layout
# identifier the application expects comes from RteCalibration.json, which tools/rtegen writes at every configure
# next to RteConfig.h (hash over the parameters, RTE-26).
#
# SYS-90: calibrationPlausibility - the plausibility checks of the safety-relevant calibration parameters (stage K6).
# Exists whenever tools/rtegen generated rte::PARAMETER_CHECKS (a parameter with a safety class other than qm,
# NX_RTE_HAS_PARAMETER_CHECKS in RteConfig.h), with or without a calibration segment: Plausibility.cpp is the pure
# check (also built by the host test, tools/rtegen/test_rtegen_plausibility.py), PlausibilityMonitor.cpp the start-up,
# XCP, log, fault memory and console part. RteSystem, XcpSystem and appCalibration link it if it exists; every other
# build is unchanged.
set(_NX_RTE_CONFIG_HEADER "${RTE_GENERATED_DIR}/RteConfig.h")
if (EXISTS "${_NX_RTE_CONFIG_HEADER}")
    file(STRINGS "${_NX_RTE_CONFIG_HEADER}" _nx_parameter_checks REGEX "^#define NX_RTE_HAS_PARAMETER_CHECKS ")
    if (_nx_parameter_checks)
        message(STATUS "Calibration plausibility: checks of the safety-relevant parameters (rte::PARAMETER_CHECKS)")
        add_library(calibrationPlausibility src/calibration/Plausibility.cpp src/calibration/PlausibilityMonitor.cpp)
        target_include_directories(calibrationPlausibility PUBLIC include)
        target_link_libraries(
            calibrationPlausibility
            PUBLIC rte util
            PRIVATE lifecycle dem)
    endif ()
endif ()

set(_NX_CAL_LAYOUT_HEADER "${BOARD_GENERATED_DIR}/memory/memoryLayout.h")
if (NOT EXISTS "${_NX_CAL_LAYOUT_HEADER}")
    return()
endif ()
file(STRINGS "${_NX_CAL_LAYOUT_HEADER}" _nx_cal_start REGEX "^#define NX_MEMORY_CAL_START ")
if (NOT _nx_cal_start)
    return()
endif ()
file(STRINGS "${_NX_CAL_LAYOUT_HEADER}" _nx_cal_production REGEX "^#define NX_MEMORY_CAL_PRODUCTION +1")

set(_NX_CAL_DESCRIPTION "${RTE_GENERATED_DIR}/RteCalibration.json")
file(READ "${_NX_CAL_DESCRIPTION}" _nx_cal_json)
string(JSON NX_CAL_LAYOUT_ID GET "${_nx_cal_json}" layout)
if (NOT NX_CAL_LAYOUT_ID MATCHES "^[0-9a-f]+$")
    message(FATAL_ERROR "calibration: layout '${NX_CAL_LAYOUT_ID}' of ${_NX_CAL_DESCRIPTION} is no hex number")
endif ()
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${_NX_CAL_DESCRIPTION}")
if (_nx_cal_production)
    set(NX_CAL_PROFILE "production")
else ()
    set(NX_CAL_PROFILE "development")
endif ()
set(NX_CAL_LAYOUT_ID "${NX_CAL_LAYOUT_ID}" PARENT_SCOPE)
set(NX_CAL_PROFILE "${NX_CAL_PROFILE}" PARENT_SCOPE)
message(STATUS "Calibration segment: ${NX_CAL_PROFILE} profile, layout 0x${NX_CAL_LAYOUT_ID} (memory.calibration)")

add_library(calibrationSegment src/calibration/CalibrationSegment.cpp)
target_include_directories(calibrationSegment PUBLIC include)
add_library(appCalibration src/calibration/Calibration.cpp)
target_include_directories(appCalibration PUBLIC include PRIVATE "${BOARD_GENERATED_DIR}")
# PUBLIC: RteSystem (console, start-up) and XcpSystem (reference page) compile their calibration part with it
target_compile_definitions(appCalibration PUBLIC NX_CALIBRATION_SEGMENT=1
                           PRIVATE NX_CAL_LAYOUT_ID=0x${NX_CAL_LAYOUT_ID}U)
target_link_libraries(
    appCalibration
    PUBLIC calibrationSegment util
    PRIVATE rte lifecycle)
if (TARGET calibrationPlausibility)
    target_link_libraries(appCalibration PUBLIC calibrationPlausibility) # SYS-90: check before use
endif ()
