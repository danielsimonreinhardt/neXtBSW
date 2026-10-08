# Sources of the inter-MCU link in a bootloader (BL-15, doc/architecture/multi-mcu-flash.md).
#
# Included by bootloader/CMakeLists.txt only for a project that needs the link - a slave (its transport) or a
# master with slaves (its gateway). This file is the build half of requirement R7: a project that declares no
# slaves never sees it, links nothing of the link, and produces the output it produces today. The source half
# is the `#if BL_TRANSPORT == BL_TRANSPORT_LINK` in bl_transport_link.c, so even a mistaken include cannot
# put a second transport into a CAN bootloader.
#
# Which of the two roles a project takes is a value of the generated bl_diag.h (ARCH-48); until then a caller
# sets BL_LINK_ROLE to `slave` or `master` before including this file.
#
# The family SPI driver behind bl_link_hal.h is not selected here: it belongs to the family
# (bootloader/platforms/<family>) and is added by BL-16 (slave port) and BL-17 (master port), the same way
# bootloader/devices/sbcUja113x/bootloader.cmake adds bl_spi_${NX_FAMILY}.c today.

if (NOT DEFINED BL_LINK_ROLE)
    message(FATAL_ERROR "bootloader/src/link/link.cmake needs BL_LINK_ROLE (slave or master) - BL-15")
endif ()

# The protocol itself, family neutral, shared by both roles and by the application (libs/bsw/mcuLink).
list(APPEND BL_LINK_SOURCES "${NX_ROOT}/libs/bsw/mcuLink/src/mcuLink/McuLink.c")
list(APPEND BL_LINK_INCLUDES "${NX_ROOT}/libs/bsw/mcuLink/include" "${CMAKE_CURRENT_LIST_DIR}")

if (BL_LINK_ROLE STREQUAL "slave")
    # The UDS transport of a slave's bootloader: bl_transport.h over the link instead of over CAN/ISO-TP.
    list(APPEND BL_LINK_SOURCES "${CMAKE_CURRENT_LIST_DIR}/bl_transport_link.c")
elseif (BL_LINK_ROLE STREQUAL "master")
    # The master drives one link per slave; the routing above it is BL-17.
    list(APPEND BL_LINK_SOURCES "${CMAKE_CURRENT_LIST_DIR}/bl_link_master.c")
else ()
    message(FATAL_ERROR "unknown BL_LINK_ROLE '${BL_LINK_ROLE}' (slave or master) - BL-15")
endif ()
