# Reference and supply supervision, s32k1xx (`safeSystem`)

Port module of the s32k1xx family: safety supervision of the ADC reference voltage (bandgap measured against VREFH)
and of the internal supplies of the MCU (SIM supply monitor), run in the safety task. It is S32K1 specific and lives
in the family port template since PLAT-23; before, every board of the family needed its own copy (finding ARCH-09
B6). The source is a modified copy of the OpenBSW file of the same name (tracked in `tools/openbswsync/copies.yaml`).

It has no board device; its parameters are module parameters of the project (`modules.safeSystem`,
`project_parameters: true`). The nominal reference voltage is a board fact (`board.adc_reference_mv`).

**State:** verification `hardware` - runs on a board without false alarms; a wrong reference voltage and failed
readings up to the limit only in host tests (see [Verification](#verification)).

## Interface

Header `safeSystem/SafeSystem.h`, source `src/safeSystem/SafeSystem.cpp`, CMake target `safeSystem` (links
`safeSupervisor`, `safeMemory`, `bspMcu`, `bspInterrupts`, `bspIo`).

| Item | Content |
|---|---|
| `init()` | enables the ADC supply monitor of the SIM (`SIM->CHIPCTL`) |
| `cyclic()` | one supervision cycle (called by the safety manager) |
| `getAdcDiagnostics()` -> `AdcDiagnostics` | `readErrors`, `consecutiveRefErrors`, `skippedRefChecks`, `skippedSupplyChecks`, `refVoltageMv`, `refVoltageValid`, `consecutiveSupplyErrors` (SYS-41) |
| `resetAdcDiagnostics()` | clears the counters; the running series of failures, the last reference voltage and its validity stay |
| `ADC_REF_ERROR_LIMIT` | from `NX_MODULE_SAFESYSTEM_PARAM_ADC_READ_ERROR_LIMIT` (generated `integration/safeSystemConfiguration.h`) |
| reference band (in `SafeSystem.cpp`) | nominal `NX_ADC_REFERENCE_MV` (generated `bsp/adc/adcReferenceConfiguration.h`), tolerance `NX_MODULE_SAFESYSTEM_PARAM_ADC_REFERENCE_TOLERANCE_MV` (generated `integration/safeSystemConfiguration.h`) |
| `AdcErrorContext` | context passed to the monitors: `ADC_CONTEXT_OUT_OF_RANGE` (1), `ADC_CONTEXT_READ_FAILURE` (2) |

Manifest: roles `adc_reference_monitor` and `internal_supply_monitor`, lifecycle level 1 in task `safety`, console
group `safeadc` (`stats`, `reset`; implemented in `platforms/s32k1xx/port/safety/adcSafetyConsole`, see the
[console reference](../../../../../../doc/reference/console.md#c-safeadc)).

## Configuration

Project (`project.yaml`, [modules reference](../../../../../../doc/reference/modules.md#m-s32k1xx-safesystem)):

- `modules.safeSystem.adc_read_error_limit`, default 10, range 1..100000 - failed bandgap readings in a row before
  the reference monitor is triggered with context 2. It counts cycles of the safety task, so the time follows from
  that task's period. Since SYS-41 the same limit applies to the internal supply channel (see Behaviour); the
  description in `module.yaml` still names the bandgap only.
- `modules.safeSystem.adc_reference_tolerance_mv`, default 200, range 1..1000, unit mV - allowed deviation of the
  measured reference from its nominal value. It has to cover the accuracy of the reference source and the spread of
  the bandgap.

Board (`board.yaml`): `board.adc_reference_mv` - nominal voltage at VREFH, the same value the ADC channels are
scaled with. `tools/bswgen` generates it into `bsp/adc/adcReferenceConfiguration.h` as `NX_ADC_REFERENCE_MV`. A
board without the key gets no macro, and `SafeSystem.cpp` stops the build with an `#error`; a project that reads
the bandgap channel is rejected earlier by `tools/nxconfig` ("defines no board.adc_reference_mv"). A tolerance that
is not below the nominal value stops the build with a `static_assert`.

Example: the eval board S32K144 (LM4132, 4096 mV) with the default tolerance checks 3896..4296 mV; a board whose
VREFH is the 5 V supply (`buildtest_s32k148`, `s32k116evb`) checks 4800..5200 mV.

## Behaviour

`cyclic()` reads the bandgap channel and the internal supply channel with all interrupts suspended and the fusa gate
of the memory protection closed, then evaluates both:

- **Reference:** VREFH = 1000 mV x 4095 / bandgap raw value (12-bit ADC, bandgap taken as 1000 mV). Outside
  `adc_reference_mv` +- `adc_reference_tolerance_mv` (limits inclusive) ->
  `adcReferenceMonitor.trigger(ADC_CONTEXT_OUT_OF_RANGE)`. Until the first valid reading, `refVoltageMv` of the
  diagnostics reports the nominal value (with `refVoltageValid` false).
- **Failed reading** (`AnalogInput::get` failed, or raw value 0, which the bandgap cannot deliver): counted, the check
  is left out and the last valid reference is kept. Only when `adc_read_error_limit` readings in a row failed is the
  monitor triggered, once, with `ADC_CONTEXT_READ_FAILURE`. Reason in the header: the safe supervisor answers an
  ADC reference error with limp home and a software reset, and a single transient of the serialized ADC access is no
  evidence of a wrong reference.
- **Supplies:** a counter 0..5 steps the SIM supply multiplexer - cycle 0 selects 3.3 V flash, 2 selects 3.3 V
  oscillator, 4 selects 1.2 V core; the cycles in between measure the selected supply. It is compared with its
  nominal value +- 200 mV using the last measured reference; outside -> `internalSuppliesMonitor.trigger(...)`.
  The comparison is left out (counted) while the reading failed or no valid reference is known yet.
- **Failed supply reading (SYS-41, finding F14 of `doc/safety/fmea.md`):** failed readings of the supply channel are
  counted in a row (the channel is read in every cycle); at `adc_read_error_limit` the supplies monitor is triggered,
  once, with `ADC_CONTEXT_READ_FAILURE`, and one good reading ends the series. Before SYS-41 a channel that never
  delivered ended the supervision of all three supplies without an event. A missing reference is not counted here -
  that is the bandgap's own limit.

What the monitors do on a trigger is decided by the safe supervisor, not by this module.

## Limits and known gaps

- **Reference band from the configuration (ARCH-32, closed):** until ARCH-32 the nominal reference
  `VREFH_NOMINAL_MV = 4096` (+- 200 mV, the eval board) was a constant in `SafeSystem.cpp`, and a board with
  another VREFH (for example 5000 mV) would have triggered the reference monitor. The nominal value now comes from
  `board.adc_reference_mv`, the tolerance from `modules.safeSystem.adc_reference_tolerance_mv`; the eval board
  binaries are byte-identical to before.
- Still constants: the supply tolerances (+- 200 mV), the nominal supplies (3.3 V, 1.2 V) and the bandgap value
  (1000 mV) - facts of the S32K1 derivative rather than of a board, not part of ARCH-32.
- s32k1xx only (SIM supply monitor).
- A wrong reference voltage and the read-failure limits (bandgap and, since SYS-41, supply channel) have not been
  provoked on a board (verification note).

## Tests

`tools/test -k bswgen` (entry `bswgen`, group python) compiles the production `SafeSystem.cpp` with
`test/host/SafeSystemHostTest.cpp` against stubs of its dependencies (`test/host/stubs`: a SIM register struct, a
memory protection that records the fusa gate, a safe supervisor that records every trigger with its context instead
of resetting, a scriptable `AnalogInput::get`). Focus: a failed ADC reading must not be taken as a wrong reference
(before IO-16 one transient read error rebooted the ECU). The same test runs as entry `safe-system` (group host).

The stubs configure the reference band as 5000 mV +- 300 mV on purpose (not the eval board), and the expectations
are derived from the macros. `BswGenAdcReferenceTest` in `tools/bswgen/test_bswgen.py` checks the generated headers
of its own profile (`tools/bswgen/test/profiles/projects/testproject_adc_reference`: 5000 mV +- 350 mV) and of the
repository projects. It runs the host test against the generated headers of that profile and of the eval board
demo, and it checks that `SafeSystem.cpp` with the old constants (4096 mV, 200 mV) or without one of the two limits
fails the test.

## Verification

From `module.yaml`: `verification: hardware` - "Reference and supply monitoring run on the eval board without false
alarms (bandgap readings, safeadc counters at 0, no safety reset); a wrong reference voltage and failed readings up to
the limit are covered by host tests only."
