# Architecture profile of a core (PLAT-10): selects the architecture-specific platform code (hard fault handler,
# FreeRTOS port, startup template).
#
# PLAT-21: the core table lives in one place, tools/nxconfig/config.py CORES. "nxconfig cmake" writes the
# architecture of the project's derivative as NX_CORE_ARCHITECTURE into the settings include file; this function only
# hands it on, so nothing has to be kept in step here.
#
# nx_core_architecture(<out-var>) sets <out-var> to armv6m, armv7m, armv8m or armv7r (ARCH-23: Cortex-R4F), or stops
# with a message. Callers select their variant from it and stop with a message for an architecture they have no
# variant for (no fallback to Cortex-M code).

include_guard(GLOBAL)

function (nx_core_architecture out)
    if (NOT DEFINED NX_CORE_ARCHITECTURE OR NX_CORE_ARCHITECTURE STREQUAL "")
        message(
            FATAL_ERROR
                "NxCoreArchitecture.cmake: NX_CORE_ARCHITECTURE is not set (core '${NX_CORE}'). It comes from "
                "tools/nxconfig (\"nxconfig cmake\", config.py CORES): include the generated settings file before "
                "using the platform code.")
    endif ()
    set(${out}
        "${NX_CORE_ARCHITECTURE}"
        PARENT_SCOPE)
endfunction ()
