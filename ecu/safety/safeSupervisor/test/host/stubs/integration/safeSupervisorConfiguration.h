/* SYS-53 test profile of the host tests that include safeSupervisor/SafeSupervisor.h, in place of the generated
 * integration/safeSupervisorConfiguration.h (tools/bswgen from modules.safeSupervisor of a project). Deliberately
 * not the demo values: a window of 130 ms at a 10 ms safety task is 13 cycles (demo: 200 ms, 20 cycles), so the
 * correlation tests of SafeSupervisorHostTest.cpp show that the supervisor follows the configuration. */
#pragma once

#define NX_MODULE_SAFESUPERVISOR_PARAM_RESET_LOOP_LIMIT     3U
#define NX_MODULE_SAFESUPERVISOR_PARAM_RESET_LOOP_CLEAR_MS  60000U // ms
#define NX_MODULE_SAFESUPERVISOR_PARAM_DEVICE_BUS_WINDOW_MS 130U   // ms

// ceil(device_bus_window_ms / safeLifecycle.task_period_ms) = ceil(130 / 10)
#define NX_MODULE_SAFESUPERVISOR_DERIVED_DEVICE_BUS_WINDOW_CYCLES 13U
