# Safety task, s32k1xx (`safeLifecycle`)

Port module of the s32k1xx family: the safety task. `SafetyManager` runs one cycle of the safety supervision per
period of the task (period check, deadman configuration check, watchdog service, memory, supply and I/O
supervision); `SafetySystem` (`ecu/application`) schedules the task at a fixed rate. The sources are modified
copies of the OpenBSW files of the same name (tracked in `tools/openbswsync/copies.yaml`).

Since SYS-25 its three times are module parameters of the project (`modules.safeLifecycle`,
`project_parameters: true`), generated into `integration/safeLifecycleConfiguration.h`. Before, they were code
constants in three files, kept equal by tests.

**State:** verification `hardware` - the demo values run on the eval board without watchdog reset; other values,
the derivation and the period check only in host and generator tests.

## Configuration

Project (`project.yaml`, [modules reference](../../../../../../doc/reference/modules.md#m-s32k1xx-safelifecycle)):

| Parameter | Default | Range | Used as |
|---|---|---|---|
| `task_period_ms` | 10 ms | 1..1000 | `SYSTEM_CYCLE_TIME` in `SafetySystem.cpp` (schedules the task) and `SafetyManager::CYCLE_PERIOD_MS` (period check) |
| `watchdog_timeout_ms` | 250 ms | 1..65535, S32K1: at most 511 | `SafeWatchdog::WATCHDOG_TIMEOUT_MS` (`ecu/safety/safeWatchdog`) |
| `cycle_lateness_ms` | 2 ms | 0..999, below `task_period_ms` | `SafetyManager::CYCLE_LATENESS_MS` (period check, SM-04) |

Derived by `tools/nxconfig` (`checks.py` `safety_timing`), never set by the project:

- The LPO limits in per mille of nominal, from the derivative database (`mcudb/<family>/<derivative>.yaml`
  `clock.lpo`, data sheet), rounded outwards: slowest floor(`min_hz` x 1000 / `nominal_hz`), fastest
  ceil(`max_hz` x 1000 / `nominal_hz`); S32K1xx 113 ... 139 kHz at 128 kHz: 882 and 1086. Generated as
  `NX_MODULE_SAFELIFECYCLE_DERIVED_LPO_MIN_PER_MILLE` / `..._MAX_PER_MILLE`; the period check and the watchdog
  relation of `SafetyManager.cpp` use them (SYS-53, before constants of `safeSupervisionCheck/LptmrCycleTimer.h`).
- `SafetyManager::WATCHDOG_CYCLIC_COUNTER` = floor(`watchdog_timeout_ms` x 1000 / (3 x `task_period_ms` x
  fastest LPO per mille)), generated as `NX_MODULE_SAFELIFECYCLE_DERIVED_WATCHDOG_SERVICE_CYCLES`. The WDOG counts
  the LPO, and its driver loads the counter for the nominal 128 kHz, so at the fastest LPO of the data sheet the
  time-out expires earlier (250 ms nominal: 230.2 ms at 139 kHz). Three service intervals fit into that shortest
  time-out by construction (finding F2 of `doc/safety/concept.md`, decision `sys25-lpo` of 2026-09-22); demo
  3 x 7 x 10 ms = 210 ms <= 230.2 ms (with the nominal derivation before SYS-53: 8 cycles, 240 ms). It is also the
  window of the period check (`CYCLE_PERIOD_WINDOW`).

Checked by `tools/nxconfig`, each with the YAML path of the value (the configure step stops on an error):

- time-out shorter than three periods (no service interval possible), and the relation 3 x interval <= time-out at
  the fastest LPO as counter-check of the derivation (the check of SYS-23);
- a derivative without `clock.lpo` (error at its `clock` entry): the S32K1 watchdog counts the LPO;
- time-out beyond the S32K1 WDOG: TOVAL is 16 bit at the nominal 128 kHz LPO of the OpenBSW driver, so 511 ms;
- the conditions of the `static_assert`s of the period check in `SafetyManager.cpp` (a window of at least one tick,
  one interval shorter than a window, a window within one wrap-around of the LPTMR), computed with the LPO limits
  above and the tick and width of `safeSupervisionCheck/LptmrCycleTimer.h` - e.g. a time-out of 30 ms at 10 ms
  gives a window of one cycle, which cannot work;
- the module parameters of the safe supervisor that count safety cycles (`modules.safeSupervisor`, manifest
  `../safeSupervisor/module.yaml`, SYS-53): the window of the device-bus correlation in cycles is derived from
  `device_bus_window_ms`; warnings if it is shorter than two supervision cycles (`cycle_ms`) of the slowest device
  on an SPI bus or if there are more device buses than its 8 slots; error if `reset_loop_clear_ms` is shorter than
  one period.

`SafetyManager.cpp` asserts the watchdog relation (at the fastest LPO) once more on the generated values at compile
time.

## Limits and open points

- The LPO range is the column S32K1xx of the data sheet (max 139 kHz). An S32K14xW derivative (max 141 kHz) needs
  its own mcudb file with `max_hz: 141000`; the per-mille limits and the service interval follow from it.
- The OpenBSW watchdog driver still loads TOVAL for the nominal 128 kHz; the fastest LPO is taken into account by
  servicing more often, not by a longer TOVAL.

## Tests

- `nxconfig-watchdog` (`tools/nxconfig/test_watchdog_timing.py`): derivation and every check with an own test
  profile (S32K144, two TMC6200 on two SPI buses), own source trees of the constants and own mcudb copies with
  another LPO range.
- `cycle-period-constants`: the code takes all values from the generated macros; bounds recomputed with fractions
  from the LPO range of mcudb.
- `safety-cycle`, `safety-gate`, `cycle-timer-off`: `SafetyManager` with the test profile
  `test/host/stubs/integration` (270 ms, 8 cycles).
- `bswgen-reference`: `integration/safeLifecycleConfiguration.h` of every reference profile (PLAT-78).
