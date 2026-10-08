# Platform of the family s32k1xx (PLAT-12), the same hook the family stm32 already uses (PLAT-14): the root
# CMakeLists.txt adds this directory instead of external/openbsw/platforms when it exists.
#
# The OpenBSW platform of the family is used **unchanged** and with the same binary directory as before
# (openbsw/platforms/s32k1xx), so nothing about an existing build changes. What follows are adjustments for
# derivatives the OpenBSW reference port does not cover; every one of them is a **no-op for a S32K14x/S32K148**
# and switched by a fact of the derivative (tools/nxconfig: NX_FEATURES, NX_CLOCK_PLL), never by a board name.
#
# Each one is a finding for OpenBSW (PLAT-03), not a design decision of neXtBSW - see doc/changelog.d/PLAT-12.md.

add_subdirectory("${OPENBSW_DIR}/platforms/s32k1xx" openbsw/platforms/s32k1xx EXCLUDE_FROM_ALL)

# 1. Processor code cache (bspCore, src/cache/cache.cpp): writes LMEM->PCCCR. A derivative without that cache
#    (mcudb derivative.features without lmem_cache; [RM] 33.1 "Variants of S32K11x series do not have LMEM") has
#    no LMEM block in its device header, and the file does not compile. The integration calls cacheEnable() only
#    with the feature (NX_FEATURE_LMEM_CACHE in main.cpp), so the translation unit is replaced by an empty one -
#    a call without the cache then fails to link with the name of the missing function instead of compiling
#    silently into a register access that does not exist.
if (NOT "lmem_cache" IN_LIST NX_FEATURES)
    set_property(TARGET bspCore PROPERTY SOURCES
                 "${CMAKE_CURRENT_LIST_DIR}/bsp/bspCore/src/cache/cacheUnavailable.cpp")
    message(STATUS "s32k1xx: ${NX_DERIVATIVE} has no LMEM code cache, bspCore without cache.cpp (PLAT-12)")
endif ()

# 2. Clock driver (bspClock, src/clockConfig.cpp): the driver switches the PLL off with `#if PLL_AVAILABLE`
#    (derived from SCG_SPLLCSR_SPLLEN_MASK), but `pllOff()` calls isSpllSysClk() and spllStop() unconditionally -
#    both only exist inside that guard. Without an SPLL the file therefore does not compile (finding of PLAT-11).
#    Until the fix is upstream (PLAT-03) the two primitives are supplied by a forced include; they do exactly
#    what a device without SPLL needs (the PLL is never the system clock, stopping it is nothing to do).
#    Without the SPLL the driver also leaves isSoscValid() unused (it is only called while starting the PLL), which
#    -Wall -Werror rejects. The warning is switched off for this one translation unit of the submodule, not for the
#    build: the unused function is part of the same upstream finding.
if (NOT NX_CLOCK_PLL)
    target_compile_options(bspClock PRIVATE
                           "-include${CMAKE_CURRENT_LIST_DIR}/bsp/bspClock/include/clock/nxSpllUnavailable.h"
                           "-Wno-unused-function")
    message(STATUS "s32k1xx: ${NX_DERIVATIVE} has no SPLL, bspClock with clock/nxSpllUnavailable.h (PLAT-12)")
endif ()

# 3. Interrupt manager (bspInterruptsImpl, src/interrupt_manager.c): SYS_GetActive() forwards to NVIC_GetActive(),
#    which ARMv6-M does not have (no NVIC_IABR on a Cortex-M0+; CMSIS says so itself). Same forced include
#    pattern; nothing in this repository calls SYS_GetActive().
if (NX_CORE_ARCHITECTURE STREQUAL "armv6m")
    target_compile_options(
        bspInterruptsImpl PRIVATE
        "-include${CMAKE_CURRENT_LIST_DIR}/bsp/bspInterruptsImpl/include/interrupts/nxNvicGetActiveUnavailable.h")
    message(STATUS "s32k1xx: ${NX_CORE} (armv6m), bspInterruptsImpl with interrupts/nxNvicGetActiveUnavailable.h "
                   "(PLAT-12)")
endif ()

# 4. FlexCAN driver (bspFlexCan, FlexCANDevice): fixed split for 32 message buffers, but FlexCAN1/2 of the S32K144
#    (FlexCAN1 of the S32K142, FlexCAN2 of the S32K146) have 16 - the first access to buffer 16 is an access error
#    (COM-05). Replaced (translation unit and header in front) by the family port, next to its only user, the CAN
#    system: port/bspConfiguration/CMakeLists.txt and port/bspConfiguration/bspFlexCan (COM-26). An instance with 32
#    buffers keeps the OpenBSW split; MCR[MAXMB] is 31 now (OpenBSW ORed 32 into the reset value 0x0F: 47).
