# Product build without test and fault-injection console commands (SYS-74, iso26262-gap.md task 19 / SG6).
#
#   cmake --preset <preset> -B build/<preset>-product -DNX_PRODUCT_BUILD=ON
#
# The released software contains only specified functions. The console of the development build carries commands
# whose only purpose is to test a safety mechanism by provoking a fault or its reaction, or to pass a protection for
# a board test. NX_PRODUCT_BUILD=ON builds the same project without them:
#
#   - "safety" (mpu, wdg, prt, dst, est): the whole group (ecu/consoleCommands, SafetySystem)
#   - "lc" udef, pabt, dabt, assert (fault injection) and reboot, poweroff, level (lifecycle shut-down, which cancels
#     the safety cycle and disables the MCU watchdog without the safe state, coexistence.md CX-2); "lc bootloader"
#     stays (reset with boot request, the same entry as the programming session)
#   - "sbc failstart" with its start-up hook and report (platforms/s32k1xx/port/main)
#   - "pwm" (OpenBSW OutputPwmTester behind systems/CheckedOutputPwmTester, platforms/s32k1xx/port/main): writes duties
#     of PWM channels from the console, past the function that drives a group; the channels of the PWM groups of the
#     safe outputs table are refused in the development image as well (SYS-162, SR-110-1 b+c)
#   - SYS-119 (coexistence.md CX-9, safety plan N7): the service commands that write past the function owning the
#     register, frame, output or fault memory: "sbc write", "sbc pn", "<gd> write", "<cmu> write", "can send",
#     "i2c rd|wr", "out set" ("out" keeps "all" and "get"), "dem clear|cycle|setting" (UDS 14 and 85 are the
#     specified way); the reading commands of these groups stay. "nvm write" stays as a specified service function
#     (the write of the run-down brought forward, values from RAM only)
#   - SYS-119 review points S1, S2, S4 (decision of the safety review pending): "<hs> on|reset" (load supply on
#     without a specified function, latched fault released without a reset), "<gd> clear", "<cmu> clear", "sbc sleep"
#     (the product sleeps through "ecu sleep"); the switch-offs "<hs> off", "<gd> off", "<cmu> stop" stay (S3)
#   - the test-build options below must stay OFF: "hardfault", "inject", "<gd> enable", "flexnvm partition", "dflash",
#     "shutdownhang" (SYS-173), "shortlock" (SYS-172)
#
# It is a build decision, not a project value (like the test-build options, decision IO-34): the same project.yaml
# gives the development image and the product image. memory.calibration.profile (development/production) is a
# project value of the calibration segment only and does not exist in a project without calibration; the two are
# independent switches. Default OFF: the demo and every preset build as before. The check of the product image is
# tools/elfcheck/product.py (python tools/elfcheck product --app <app.ecu.elf>).
#
# The root CMakeLists.txt declares option(NX_PRODUCT_BUILD ... OFF) (where tools/docgen reads the build options) and
# includes this file before any subdirectory: the definition NX_PRODUCT_BUILD=1 reaches every target of the tree
# (add_compile_definitions of the root directory).

include_guard(GLOBAL)

# Test-build options of the family ports (declared in platforms/<family>/port/main/CMakeLists.txt); a product build
# refuses each of them. Read here from the cache, before the ports declare them.
set(NX_TEST_BUILD_OPTIONS
    NX_HARDFAULT_TEST_COMMAND
    NX_GD_ENABLE_COMMAND
    NX_SAFETY_FAULT_INJECTION_COMMAND
    NX_FLEXNVM_PARTITION_COMMAND
    NX_DFLASH_COMMAND
    NX_SHUTDOWN_HANG_COMMAND
    NX_SHORT_LOCK_MEASURE_COMMAND)

if (NX_PRODUCT_BUILD)
    set(_nx_test_options_on "")
    foreach (_nx_option IN LISTS NX_TEST_BUILD_OPTIONS)
        if (${_nx_option})
            list(APPEND _nx_test_options_on ${_nx_option})
        endif ()
    endforeach ()
    if (_nx_test_options_on)
        list(JOIN _nx_test_options_on ", " _nx_test_options_on)
        message(FATAL_ERROR "NX_PRODUCT_BUILD: test-build option(s) ${_nx_test_options_on} switched on - a product "
                            "image has no test or fault-injection commands (SYS-74). Configure without them.")
    endif ()
    add_compile_definitions(NX_PRODUCT_BUILD=1)
    message(STATUS "NX_PRODUCT_BUILD: product image without test and fault-injection console commands (SYS-74)")
endif ()
