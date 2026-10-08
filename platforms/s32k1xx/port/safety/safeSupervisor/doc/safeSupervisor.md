# Safe supervisor, project values of the s32k1xx port (`safeSupervisor`)

The safe supervisor itself is family neutral (`ecu/safety/safeSupervisor`, CMake target `safeSupervisor`): it maps
every safety event to its reaction (S1 safe state, S2 reset, S3 report), limits reset loops (SYS-43) and correlates
device reports per bus (SYS-29). This directory only holds its **module manifest** in the family port, because the
family port decides that the supervisor runs in its safety task and counts its times in cycles of that task
(`modules.safeLifecycle.task_period_ms`). Another family port that uses the supervisor brings its own copy.

Since SYS-53 the values that were code constants or provisional defaults are module parameters of the project
(`modules.safeSupervisor`, `project_parameters: true`), generated into
`integration/safeSupervisorConfiguration.h`.

## Configuration

Project (`project.yaml`, [modules reference](../../../../../../doc/reference/modules.md)):

| Parameter | Default | Range | Used as |
|---|---|---|---|
| `reset_loop_limit` | 3 | 1..255 | limit of `SafeSupervisor::startResetLoopLimit()` in the family `main.cpp` (SSR-065) |
| `reset_loop_clear_ms` | 60000 ms | 10..86400000, at least one task period | fault-free time that clears the count, converted into safety cycles in `main.cpp` (`resetloop::clearCyclesOf`, rounded up) |
| `device_bus_window_ms` | 200 ms | 1..60000 | window of the device-bus correlation, `SafeSupervisor::DEVICE_BUS_WINDOW_CYCLES` |

Derived by `tools/nxconfig` (`checks.py` `_safe_supervisor`), never set by the project:

- `NX_MODULE_SAFESUPERVISOR_DERIVED_DEVICE_BUS_WINDOW_CYCLES` = ceil(`device_bus_window_ms` /
  `safeLifecycle.task_period_ms`) - never shorter than the time set; demo 200 ms / 10 ms = 20 cycles.

Checked by `tools/nxconfig`, with the YAML path of the value:

- error: `reset_loop_clear_ms` shorter than one period of the safety task (the `static_assert` in `main.cpp`);
- warning: the window shorter than two supervision cycles (`cycle_ms`) of the slowest device on an SPI bus;
- warning: more device buses than the supervisor tracks at the same time (`SafeSupervisor::DEVICE_BUS_SLOTS`, 8).

**State:** verification `hardware` for the reset-loop limit with the default values (SYS-43 board checks B10/B11);
the device-bus window has no reporting device yet (SYS-28) and is covered by host and generator tests only.

## Tests

- `nxconfig-watchdog` (`tools/nxconfig/test_watchdog_timing.py`): derivation of the window, the checks, the defaults.
- `safe-supervisor`, `safe-supervisor-bare`: the correlation with the test profile
  `ecu/safety/safeSupervisor/test/host/stubs/integration/safeSupervisorConfiguration.h` (130 ms, 13 cycles - not
  the demo values).
- `bswgen-reference`: `integration/safeSupervisorConfiguration.h` of every reference profile.
