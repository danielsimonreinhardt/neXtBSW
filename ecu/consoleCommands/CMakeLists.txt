add_library(
    consoleCommands
    src/lifecycle/console/LifecycleControlCommand.cpp
    src/lifecycle/console/StatisticsCommand.cpp)

# SYS-74: the console group "safety" only tests safety mechanisms (MPU violation, watchdog, safe state on and off) -
# not in the product image (cmake/NxProductBuild.cmake); SafetySystem registers it only outside the product build.
if (NOT NX_PRODUCT_BUILD)
    target_sources(consoleCommands PRIVATE src/safety/console/SafetyCommand.cpp)
endif ()

target_include_directories(consoleCommands PUBLIC include)

target_link_libraries(
    consoleCommands
    PUBLIC lifecycle
           asyncBinding
           asyncCoreConfiguration
           runtime
           configuration
           safeUtils
           etl
    PRIVATE safeIo bspIo bspMcu)

target_include_directories(consoleCommands PRIVATE "${NX_ROOT}/bootloader/include")

# PLAT-98: identification of the image at run time (include/identity/Identity.h): the name of the project prefixes
# every log line, the console commands "version" and "info" show project, neXtBSW version, board and bootloader.
# All values come from the build (nxconfig: project.name, board and derivative; VERSION of the tree, ARCH-55) - a
# customer project shows its own name. PLAT-79 adds its generated fields through identity/nxIdentityFields.h.
target_sources(consoleCommands PRIVATE src/identity/Identity.cpp src/identity/console/IdentityCommand.cpp)
target_compile_definitions(
    consoleCommands
    PRIVATE NX_IDENTITY_APPLICATION="${NX_PROJECT_NAME}" NX_IDENTITY_NEXTBSW_VERSION="${PROJECT_VERSION}"
            NX_IDENTITY_BOARD="${NX_BOARD}/${NX_DERIVATIVE}")
target_link_libraries(consoleCommands PUBLIC util)
