# SYS-29: DeviceReportRte.cpp - the reader of the device-bus verdict that the generated RTE calls (tools/rtegen)
# SYS-117: DegradedRte.cpp - the reader of the degraded groups (RTE signal safety.degraded)
# SYS-130: HwShutoff.cpp - the report of a hardware shut-off path and its reader (linked only when a family port reports)
add_library(
    safeSupervisor src/safeSupervisor/SafeSupervisor.cpp src/safeSupervisor/DeviceReportRte.cpp
                   src/safeSupervisor/DegradedRte.cpp src/safeSupervisor/HwShutoff.cpp)

target_include_directories(safeSupervisor PUBLIC include)

# SYS-130: a family port with a hardware shut-off path in this image (S32K1: a project with a hardware threshold,
# platforms/s32k1xx/port/bspConfiguration) sets the global property; only then SafeSupervisor.cpp refuses to leave S1
# through the register check while a report is latched. Private: the class layout does not depend on it.
get_property(_nxSafeSupervisorHwShutoff GLOBAL PROPERTY NX_SAFE_SUPERVISOR_HW_SHUTOFF)
if (_nxSafeSupervisorHwShutoff)
    target_compile_definitions(safeSupervisor PRIVATE NX_SAFE_SUPERVISOR_HW_SHUTOFF=1)
endif ()

# SYS-140: in a project with an active short the supervisor removes the short on a bridge fault through the family's
# safe state selection (safeSupervisor/SafeStateShort.h). That library exists only in such a project (S32K1:
# platforms/s32k1xx/hardFaultHandler, nxSafeOutputSelection); the generator expression is evaluated after every
# directory is read, so the order of the directories does not matter, and every other project links nothing more.
target_link_libraries(safeSupervisor PRIVATE $<TARGET_NAME_IF_EXISTS:nxSafeOutputSelection>)

# SYS-37: bspInterrupts public - the event box of the deferred monitors (SafetyEventMailbox.h, included by
# SafeSupervisor.h) takes the interrupt lock inline in the reporter's code
target_link_libraries(
    safeSupervisor
    PUBLIC safeMonitor bspInterrupts
    PRIVATE safeUtils common)

if (PLATFORM_SUPPORT_IO)
    target_link_libraries(safeSupervisor PRIVATE bspIo safeIo)
    # SYS-138: safeIo of the family port implements the read-back of the safe state (safeSupervisor/SafeStateReadback.h)
    # - the hold of S1, the degraded groups and the S1 + S2 sequence confirm the walk by it
    target_compile_definitions(safeSupervisor PRIVATE NX_SAFE_SUPERVISOR_READBACK=1)
endif ()

# SYS-23: the hold in the safe state (holdSafeState) services the MCU watchdog itself
if (PLATFORM_SUPPORT_WATCHDOG)
    target_link_libraries(safeSupervisor PRIVATE safeBspMcuWatchdog)
endif ()

target_link_libraries(safeSupervisor PUBLIC bspMcu)
