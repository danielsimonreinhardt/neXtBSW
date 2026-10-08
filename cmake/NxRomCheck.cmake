# Hex of an application image with the reference CRC of the ROM check (SM-14, SYS-38).
#
#   include("${NX_ROOT}/cmake/NxRomCheck.cmake")
#   nx_rom_check_image(<executable> HEX <hex file>)
#
# The run-time ROM check (safeMemory RomCheck) compares the CRC over the flash areas of the linker script's checksum
# list with a reference that only exists after the link. The family port that has such a check registers its
# post-link tool in the global property NX_ROM_CHECK_TOOL (s32k1xx: port/safety/safeMemory/CMakeLists.txt). With a
# tool the function adds a POST_BUILD step that writes the reference into the ELF and produces the hex from it -
# before any later POST_BUILD step of the caller (canflash record, size report) reads the hex, so the validity record
# of the bootloader covers the image with its reference. The tool stops the build if the list is missing or
# implausible or flash content lies outside the areas: an image without a valid reference never leaves the build.
#
# The targets registered in the global property NX_ROM_CHECK_CONSUMERS (s32k1xx: main, which starts the check) get
# NX_ROM_CHECK_IMAGE=1: the run-time check is only compiled into an image whose reference the build produced, so a
# missing post-link step can never hold the ECU in the safe state (SYS-23 holds S1 on ROM_CHECK_FAILURE).
#
# Without a registered tool (platform without ROM check) the function produces the plain hex.

include_guard(GLOBAL)

function (nx_rom_check_image target)
    cmake_parse_arguments(PARSE_ARGV 1 _ARG "" "HEX" "")
    if (NOT _ARG_HEX)
        message(FATAL_ERROR "nx_rom_check_image(${target}): HEX <file> missing")
    endif ()
    get_property(_tool GLOBAL PROPERTY NX_ROM_CHECK_TOOL)
    if (_tool)
        add_custom_command(
            TARGET ${target}
            POST_BUILD
            COMMAND "${Python3_EXECUTABLE}" "${_tool}" inject --elf "$<TARGET_FILE:${target}>" --hex "${_ARG_HEX}"
                    --objcopy "${CMAKE_OBJCOPY}"
            COMMENT "ROM check: reference CRC into $<TARGET_FILE_NAME:${target}> and its hex (SYS-38)"
            VERBATIM)
        get_property(_consumers GLOBAL PROPERTY NX_ROM_CHECK_CONSUMERS)
        foreach (_consumer IN LISTS _consumers)
            target_compile_definitions(${_consumer} PRIVATE NX_ROM_CHECK_IMAGE=1)
        endforeach ()
    else ()
        add_custom_command(
            TARGET ${target}
            POST_BUILD
            COMMAND ${CMAKE_OBJCOPY} -O ihex "$<TARGET_FILE:${target}>" "${_ARG_HEX}"
            VERBATIM)
    endif ()
endfunction ()
