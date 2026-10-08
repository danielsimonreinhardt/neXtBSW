/* SYS-25 test profile of SafetyCycleHostTest and SafetyGateHostTest in place of the generated
 * integration/safeLifecycleConfiguration.h (tools/bswgen from modules.safeLifecycle of a project). The scenarios of
 * the period check (17 ms reported, 12 ms never, 5 ms and 7 ms at the end of the first window, bounds 14/68 ticks)
 * are worked out for these values: period 10 ms, lateness 2 ms, derived service interval 8.
 * SYS-53: LPO limits of the S32K1xx data sheet (113 ... 139 kHz at 128 kHz nominal, mcudb clock.lpo); the service
 * interval is derived at the fastest LPO (decision sys25-lpo), so this profile has a time-out of 270 ms to keep its
 * 8 cycles (the demo has 250 ms and 7 cycles now). */
#pragma once

#define NX_MODULE_SAFELIFECYCLE_PARAM_TASK_PERIOD_MS      10U  // ms
#define NX_MODULE_SAFELIFECYCLE_PARAM_WATCHDOG_TIMEOUT_MS 270U // ms
#define NX_MODULE_SAFELIFECYCLE_PARAM_CYCLE_LATENESS_MS   2U   // ms

// floor(clock.lpo.min_hz x 1000 / nominal_hz) = floor(113000000 / 128000)
#define NX_MODULE_SAFELIFECYCLE_DERIVED_LPO_MIN_PER_MILLE 882U
// ceil(clock.lpo.max_hz x 1000 / nominal_hz) = ceil(139000000 / 128000)
#define NX_MODULE_SAFELIFECYCLE_DERIVED_LPO_MAX_PER_MILLE 1086U
// floor(watchdog_timeout_ms x 1000 / (3 x task_period_ms x lpo_max_per_mille)) = floor(270000 / 32580)
#define NX_MODULE_SAFELIFECYCLE_DERIVED_WATCHDOG_SERVICE_CYCLES 8U
