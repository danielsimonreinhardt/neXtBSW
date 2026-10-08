# Stops a configure run in a build directory that belongs to the host system instead of the cross toolchain (PLAT-47).
#
# CMake determines the target system once per build directory and records it in CMakeFiles/<version>/CMakeSystem.cmake.
# While CMakeCache.txt exists, later configure runs load that file again and never re-read the toolchain file. A
# directory that once recorded the host system (CMAKE_SYSTEM_NAME Windows, no toolchain include) therefore stays
# broken: the compiler check links arm-none-eabi-gcc with the Windows-GNU options (--out-implib,
# --major-image-version) and fails with "unrecognized option '--major-image-version'". --clean-first does not help,
# only a fresh directory does. This file names the cause instead.
#
# Project mode (presets nx-arm-gcc):
#   CMAKE_PROJECT_neXtBSW_INCLUDE_BEFORE: before the languages are enabled, checks the recorded CMakeSystem.cmake.
#   CMAKE_PROJECT_neXtBSW_INCLUDE:        after project(), checks that a 32-bit cross compiler was found (D11).
# Script mode (host test cmake/test/test_build_dir_check.py):
#   cmake -DNX_CHECK_SYSTEM_FILE=<CMakeSystem.cmake> -P cmake/NxBuildDirCheck.cmake

function (nx_check_recorded_system system_file build_dir)
    if (NOT EXISTS "${system_file}")
        return()
    endif ()
    file(STRINGS "${system_file}" _lines REGEX "^set\\(CMAKE_(SYSTEM_NAME|CROSSCOMPILING) ")
    set(_name "")
    set(_cross "")
    foreach (_line IN LISTS _lines)
        if (_line MATCHES "^set\\(CMAKE_SYSTEM_NAME \"([^\"]*)\"\\)")
            set(_name "${CMAKE_MATCH_1}")
        elseif (_line MATCHES "^set\\(CMAKE_CROSSCOMPILING \"([^\"]*)\"\\)")
            set(_cross "${CMAKE_MATCH_1}")
        endif ()
    endforeach ()
    if (NOT _cross)
        message(FATAL_ERROR "build directory ${build_dir} was configured for the host system "
                            "(CMAKE_SYSTEM_NAME '${_name}' without toolchain file, recorded in ${system_file}). "
                            "CMake keeps this decision and would link arm-none-eabi-gcc with host options such as "
                            "--major-image-version; --clean-first does not change it. Configure again with "
                            "'cmake --fresh --preset <preset>' (same -B) or delete the build directory (PLAT-47).")
    endif ()
endfunction ()

if (CMAKE_SCRIPT_MODE_FILE)
    if (NOT DEFINED NX_CHECK_SYSTEM_FILE)
        message(FATAL_ERROR "script mode needs -DNX_CHECK_SYSTEM_FILE=<CMakeFiles/<version>/CMakeSystem.cmake>")
    endif ()
    nx_check_recorded_system("${NX_CHECK_SYSTEM_FILE}" "(script mode)")
    message(STATUS "NxBuildDirCheck: ok")
elseif (NOT CMAKE_C_COMPILER_LOADED)
    nx_check_recorded_system("${CMAKE_BINARY_DIR}/CMakeFiles/${CMAKE_VERSION}/CMakeSystem.cmake" "${CMAKE_BINARY_DIR}")
else ()
    if (NOT CMAKE_CROSSCOMPILING OR NOT CMAKE_SIZEOF_VOID_P EQUAL 4)
        message(FATAL_ERROR "compiler ${CMAKE_C_COMPILER} (${CMAKE_C_COMPILER_ID}, ${CMAKE_SIZEOF_VOID_P}-byte pointers, "
                            "cross compiling: '${CMAKE_CROSSCOMPILING}') is not a 32-bit cross compiler: the toolchain "
                            "file is not in effect or CC/PATH/NX_ARM_GCC_BIN select a host compiler (PLAT-47)")
    endif ()
endif ()
