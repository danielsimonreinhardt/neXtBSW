# Calibration segment of an application image (SYS-89, stage K3 of doc/architecture/kalibrierung.md).
#
#   include("${NX_ROOT}/cmake/NxCalibration.cmake")
#   nx_calibration_seal(<executable> SEGMENT_HEX <hex file>)
#
# The linker template reserves the segment (header of 32 bytes, then the values of the calibration parameters) but
# cannot know its CRC or the layout identifier of the parameters. The POST_BUILD step of this function writes the
# header into the ELF (tools/calseg seal: layout identifier from RteCalibration.json, length, CRC-32 over the values,
# header CRC; in the development profile it first copies the defaults of the RAM working page into the flash
# segment). It checks that every parameter of rte::PARAMETERS lies in the segment or its working page, and stops the
# build otherwise. Then it writes the segment alone as Intel HEX (tools/calseg hex), the file a calibration tool
# takes with the A2L and that BL-31 will flash without the application.
#
# Call it before nx_rom_check_image() and the validity record: POST_BUILD steps run in the order they were added,
# and both must cover the sealed segment.

include_guard(GLOBAL)

function (nx_calibration_seal target)
    cmake_parse_arguments(PARSE_ARGV 1 _ARG "" "SEGMENT_HEX" "")
    if (NOT _ARG_SEGMENT_HEX)
        message(FATAL_ERROR "nx_calibration_seal(${target}): SEGMENT_HEX <file> missing")
    endif ()
    set(_calseg "${NX_ROOT}/tools/calseg/calseg.py")
    set(_description "${RTE_GENERATED_DIR}/RteCalibration.json")
    set(_rte_config "${RTE_GENERATED_DIR}/RteConfig.h")
    add_custom_command(
        TARGET ${target}
        POST_BUILD
        COMMAND "${Python3_EXECUTABLE}" "${_calseg}" seal --elf "$<TARGET_FILE:${target}>" --description
                "${_description}" --rte-config "${_rte_config}"
        COMMAND "${Python3_EXECUTABLE}" "${_calseg}" hex --elf "$<TARGET_FILE:${target}>" --rte-config
                "${_rte_config}" -o "${_ARG_SEGMENT_HEX}"
        COMMENT "Calibration segment: header into $<TARGET_FILE_NAME:${target}>, segment hex (SYS-89)"
        VERBATIM)
endfunction ()
