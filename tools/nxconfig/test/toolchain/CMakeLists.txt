# Configure test of cmake/toolchains/NxArmNoneEabi-gcc.cmake (PLAT-09, test_build_integration.py): the settings
# include file written by "nxconfig cmake" for a test profile is included before project(), like in the root
# CMakeLists.txt; the probe library shows which core options reach C, C++ and assembler.
cmake_minimum_required(VERSION 3.28 FATAL_ERROR)

if (NOT NX_SETTINGS_FILE)
    message(FATAL_ERROR "pass -DNX_SETTINGS_FILE=<nxconfig cmake output>")
endif ()
include("${NX_SETTINGS_FILE}")
if (NOT CMAKE_TOOLCHAIN_FILE)
    set(CMAKE_TOOLCHAIN_FILE "${CMAKE_CURRENT_LIST_DIR}/../../../../cmake/toolchains/NxArmNoneEabi-gcc.cmake")
endif ()

project(
    nxtoolchaintest
    LANGUAGES C CXX ASM)

add_library(probe STATIC probe.c probe.cpp probe.S)

file(
    WRITE "${CMAKE_BINARY_DIR}/flags.txt"
    "C=${CMAKE_C_FLAGS}\nCXX=${CMAKE_CXX_FLAGS}\nASM=${CMAKE_ASM_FLAGS}\nLINK=${CMAKE_EXE_LINKER_FLAGS}\n"
    "COMPILER=${CMAKE_C_COMPILER}\n")
