# Inter-MCU link protocol (BL-15, doc/architecture/multi-mcu-flash.md): framing, CRC, acknowledgement, retry,
# flow control and the session counter for a point-to-point SPI link between two MCUs of one ECU.
#
# C99 with no OpenBSW dependency, no RTOS, no dynamic memory and no family header - the same source builds
# into an application through this target and into a bootloader through bootloader/src/link/link.cmake, which
# is what multi-mcu-flash.md section 6.3 requires. Host tests: test/host/mcuLinkBusHost.c, run by
# test/test_mcu_link_host.py (doc/mcuLink.md).
add_library(mcuLink src/mcuLink/McuLink.c)

target_include_directories(mcuLink PUBLIC include)

set_target_properties(
    mcuLink
    PROPERTIES C_STANDARD 99
               C_STANDARD_REQUIRED ON
               C_EXTENSIONS OFF)

target_compile_options(mcuLink PRIVATE $<$<C_COMPILER_ID:GNU,Clang>:-Wall -Wextra -Wconversion -Werror>)
