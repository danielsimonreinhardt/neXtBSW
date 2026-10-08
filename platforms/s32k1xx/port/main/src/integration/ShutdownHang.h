#pragma once

/* SYS-173 board proof (test build NX_SHUTDOWN_HANG_COMMAND only, platforms/s32k1xx/port/main/CMakeLists.txt): a
 * lifecycle component that Integration.cpp adds at level 2 and that the console group "shutdownhang" can make hang in
 * its next shutdown (ShutdownHangCommand.cpp). Every other build has neither. */

#include <lifecycle/ILifecycleComponent.h>

namespace integration
{
/** The component of the test build (runs in TASK_BSP). */
::lifecycle::ILifecycleComponent& shutdownHangComponent();
} // namespace integration
