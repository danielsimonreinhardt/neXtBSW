# Smart high-side switch (`highSideSwitch`)

Device driver for a smart high-side switch with input IN, diagnosis enable DEN and current sense output IS
(PROFET-like). It switches the load, supervises the load current for overcurrent, the fault level of the switch,
open load and an implausible reading while off, and switches off and latches on a fault. All thresholds are load
currents in mA; the conversion from the IS signal is board knowledge and happens in the integration. One instance per board
device; each instance has its own console command group.

**State:** verification `hardware` - current sense, overcurrent switch-off with latch, open load and the off check
confirmed on a board; the fault current threshold only in host tests (see [Tests](#tests)).

## Interface

| Header | Content |
|---|---|
| `highSideSwitch/HighSideSwitch.h` | `HighSideSwitchConfig` (levels in mA), `isValidHighSideSwitchConfig()`, state machine `HighSideSwitch` (`requestOn`, `requestOff`, `reset`, `step(adcOk, loadMa)`, state, last fault, counters) |
| `highSideSwitch/IHighSideSwitchIo.h` | `setEnable(on)` (IN), `setDiagEnable(on)` (DEN), `readSense(sample)` (IS: `SenseSample` with load current `loadMa` and the unconverted code `raw`) - implemented by the port |
| `highSideSwitch/HighSideSwitchSense.h` | `SenseConversion` (the generated characteristic `nxAdcPhys_<channel>`), `loadCurrentMa(convert, raw)` - used by the port to convert a sample |
| `highSideSwitch/HighSideSwitchChannel.h` | state machine plus pins: `init`, `cycle`, commands, `getOutputErrors`, `getLastSenseRaw` |
| `highSideSwitch/HighSideSwitchSystem.h` | lifecycle component with console command `<name> status/on/off/reset` |
| `highSideSwitch/HighSideSwitchNames.h` | console command, label (at most 16 characters, `hasValidLabel`) and help text of an instance |
| `highSideSwitch/HighSideSwitchIntegration.h` | `HIGH_SIDE_SWITCH_CONFIG(device)`, `HIGH_SIDE_SWITCH_NAMES(device)`: generated macros -> driver structures |
| `highSideSwitch/HighSideSwitchBinding.h` | ARCH-66: trait of the generated binding (`module.yaml binding`): configuration and names per instance, `Parts` (IN/DEN/IS on the family binders), `create()`, `SafeOff_in` |
| `highSideSwitch/HighSideSwitchSafeOff.h` | ARCH-66: what the safe_off connection `in` means for the driver (`off` = `requestOff`, `enables` = switch-ons, `setLock`/`updateLock` = switch-on lock of S1), for the generic adapter of the family integration |

CMake targets: `highSideSwitch` (state machine and channel, hardware independent) and `highSideSwitchSystem`
(lifecycle component; links OpenBSW `asyncConsole`, `lifecycle`, `util`). The manifest names
`highSideSwitchSystem`; lifecycle level 1 in task `bsp`.

## Configuration

Board (`board.yaml`, [board reference](../../../../doc/reference/board.md#f-devices-list)):

- `devices[].part` matching `BTS5030-1EJA` - the manifest lists only parts whose IS behaviour (sense ratio, fault
  current, DEN) matches the state machine; further parts are to be added after checking their data sheet;
- `devices[].connect.in` and `.den` -> entries of `dio.outputs[]`, `devices[].connect.is` -> an entry of `adc[]`.
- The IS channel **must** carry a characteristic that delivers the load current in mA, normally a sensor of kind
  `highside_is` with the sense ratio `k_ilis` and the sense resistor `r_is_ohm` and a `source`
  ([board reference](../../../../doc/reference/board.md#f-adc-list-sensor-kind-highside_is)). These part facts
  stand only there - not in the module, not in the project (IO-18/IO-21). Without them `tools/nxconfig` reports an
  error at the board path `adc[i]` ("the board facts that convert it into mA are missing").

Project (`project.yaml`): one entry per device under `modules.<device>`. The parameters, their ranges and the rules
between them are listed in the [modules reference](../../../../doc/reference/modules.md#m-highsideswitch); in
short: `cycle_ms` (default 10), `overcurrent_ma`, `overcurrent_debounce_ms`, `fault_ma`, `fault_debounce_ms`,
`switch_on_blanking_ms`, `open_load_ma`, `off_max_ma`, `off_debounce_ms` and `console`. All levels are **load
currents in mA** - the physical quantity of the function, reviewable against the load ("switch off at 3.6 A") and
unchanged when the part or the sense resistor changes
([`treiber-granularitaet.md`](../../../../doc/architecture/treiber-granularitaet.md) section 5). They have no
default, because they depend on the load. `tools/nxconfig` checks the rules; `isValidHighSideSwitchConfig` checks
the same rules again by `static_assert` in the integration.

The four levels are declared with `sense: is` in the manifest. For them `tools/nxconfig` (`sensed.py`) also checks,
with YAML path:

- the channel wired to `is` has a characteristic in mA (error at `adc[i]` or `adc[i].sensor` otherwise);
- each level lies in the range the channel can report over every converter code (error at
  `modules.<device>.<parameter>`, e.g. above the full scale of 7441 mA on the eval board: never reached);
- the characteristic is confirmed (`source`, no `verify`), otherwise a warning.

`nxconfig check` prints one `info:` line per level with the ADC codes it corresponds to, e.g.
`KL30B_SW (highSideSwitch) overcurrent_ma 3633 mA: channel CURRENTSENSE_KL30B value >= level at raw 2000..4095
(raw 2000 = 3634 mA, 2000 mV at the pin; 1.8 mA per code)`.

Conversion at run time: the trait's `Parts` (ARCH-66, before `BiosHighSideSwitchIo` of the family port) reads the
raw code of the IS channel through the family's analog binder and converts it with the generated characteristic
`nxAdcPhys_<channel>` of
`bsp/adc/analogSensorConfiguration.h` - the same `adc.phys` conversion the RTE uses, integer only, rounded to the
nearest mA. Its quality is not used: code 0 (switch off, no current) and full scale (fault current of the switch
drives the pin above the reference) are regular readings of the state machine. The driver itself knows no part
fact.

`tools/bswgen` renders the instances into `integration/highSideSwitchConfiguration.h`
(`NX_MODULE_HIGHSIDESWITCH_INSTANCES(X)`, `NX_DEVICE_<device>_PARAM_*`, `NX_DEVICE_<device>_IO_IN/_DEN/_IS`).

> Example: the S32K144 evaluation board has one BTS5030-1EJA (`KL30B_SW`) that switches the KL30B supply; the demo
> project sets its thresholds with `verify: true` and the console group `kl30b`. Board rules for `kl30b on` are in
> the [console reference](../../../../doc/reference/console.md#c-instance-highsideswitch).

## Behaviour

### States

| State | IN, DEN | Entered by |
|---|---|---|
| `OFF` | low | construction, `requestOff`, `reset` |
| `ON` | high | `requestOn` from `OFF` |
| `OVERCURRENT` (latched) | low | load current >= `overcurrent_ma` for `overcurrent_debounce_ms` |
| `DIAG_FAULT` (latched) | low | load current >= `fault_ma` for `fault_debounce_ms`; ADC read error while on or off; reading > `off_max_ma` while off for `off_debounce_ms` |

- A latched state is left only by `reset` (-> `OFF`); `requestOn` answers `LATCHED` until then. Switching on again
  needs a separate `on`. There is no automatic retry (reason in the header: the switch limits the current and
  restarts thermally on its own).
- While on, the current thresholds are ignored during `switch_on_blanking_ms`. After that the fault level is
  checked before overcurrent; if neither trips, a load current below `open_load_ma` is reported as open load (a
  warning only, counted per cycle, no switch-off; `open_load_ma: 0` switches it off).
- Debounce times are converted to cycles, rounded up, so a debounce time is never shortened by the cycle raster.

### Cycle and outputs

- Every `cycle_ms` the channel samples IS, runs one `step()` and writes IN and DEN. Commands take effect on the
  outputs at once.
- Write order: IN before DEN when switching off, DEN before IN when switching on. Outputs are rewritten every
  cycle, so another writer is overridden within one cycle. If a write fails while the switch is on, it is switched
  off and both outputs are written low again; failed updates are counted (`getOutputErrors`).
- The system is off at start, at shutdown and after every fault, and logs state changes.

### Safe state lock (SYS-36)

IN is a `safe_off` output (manifest), so entering the safe state S1 drives it low through the safe outputs table,
behind the back of the driver. Before SYS-36 the next cycle wrote it high again (board finding 2026-09-21: KL30B
off for about 10 ms after `safety dst`). Now:

- The integration attaches a switch-on lock to the channel (`HighSideSwitchChannel::setSwitchOnLock()`; on the
  S32K1 port the S1 latch of `hardFaultHandler/safeOutputs.h`, wired by the generic adapter `SafeOffOutput::attach()`
  of the generated binding with `HighSideSwitchSafeOff.h`, ARCH-66; before by `HighSideSwitchPowerStageOutput`).
- The channel asks it at the start of every cycle, before every command and before and after writing IN high.
  While it is set the switch is locked (`HighSideSwitch::lock()`): `ON` goes to `OFF`, IN and DEN are written low,
  `requestOn` answers `LOCKED` ("refused, safe state (locked until reset)"). A latched fault stays latched and
  `reset` still clears it; the off supervision keeps running.
- The lock is no output error and no fault: the system logs it once as a warning
  ("<label> locked by the safe state: off until reset"), `status` shows "(locked by the safe state)".
- Once the lock reports false again (today only after a reset; later the return path of SYS-35) the switch is
  unlocked and stays off until the next `on`.

### Console

`<name> status | on | off | reset` per instance, `<name>` from the parameter `console`. `status` shows the load
current in mA (last sample and maximum since switching on), the unconverted IS code (`IS raw`, for diagnosis
against `adc get`) and the thresholds in mA; log lines of faults and open load carry the same values. `on` switches
a load supply; whether that is permitted is a rule of the board, not of the driver.

The command runs in the console context (IO-42, pattern of SYS-13, `HighSideSwitchConsole.h`): only the snapshot of
the state resp. the switching command runs in the task of the switch, the text is formatted and written in
`TASK_IDLE`. Before IO-42 the whole command ran in `TASK_BSP`: `kl30b status` (about 340 characters, about 30 ms at
115200 Bd) held the task that also polls the 10 ms halt deadman of the debug halt guard, and every `kl30b status`
switched KL30B and DRV_EN off ("debug halt or stall (timer)", board 2026-09-21). See the
[console reference](../../../../doc/reference/console.md#c-instance-highsideswitch).

## Limits and known gaps

- The accuracy of the load current is that of the sense ratio: for the BTS5030-1EJA k_ILIS is specified for
  0.5 ... 7 A (±20 % at 0.5 A down to ±5.5 % at 7 A); below 0.5 A it is coarse (±50 % at 50 mA). Open-load and
  off levels in that range are rough limits, not measurements.
- The resolution is one converter code (eval board: 1.8 mA); levels are compared with the rounded current.
- Only `BTS5030-1EJA` is in the `parts:` list.
- The role `switched_output` is declared in the manifest; no generator in `tools/` consumes it yet, and the switch
  has no RTE port - it is operated through the console.
- The fault current threshold has not been reached on a board (verification note).

## Tests

Host-Tests, Testnamen und Verifikationsstand: [Modulkatalog](../../../../doc/reference/modules.md#m-highsideswitch).
