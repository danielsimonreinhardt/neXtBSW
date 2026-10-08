/* SYS-25 test profile of SafeWatchdogHostTest in place of the generated integration/safeLifecycleConfiguration.h
 * (tools/bswgen from modules.safeLifecycle of a project): a time-out of 400 ms instead of the module default of
 * 250 ms, so the test shows that SafeWatchdog arms the watchdog with the configured value (TOVAL = 128 x 400). */
#pragma once

#define NX_MODULE_SAFELIFECYCLE_PARAM_TASK_PERIOD_MS      20U  // ms
#define NX_MODULE_SAFELIFECYCLE_PARAM_WATCHDOG_TIMEOUT_MS 400U // ms
#define NX_MODULE_SAFELIFECYCLE_PARAM_CYCLE_LATENESS_MS   3U   // ms

// floor(watchdog_timeout_ms / (3 x task_period_ms)) = floor(400 / 60)
#define NX_MODULE_SAFELIFECYCLE_DERIVED_WATCHDOG_SERVICE_CYCLES 6U
