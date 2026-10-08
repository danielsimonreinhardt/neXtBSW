# CAN system, hardware independent parts (`canSystem`)

Header-only service for the CAN buses of the ECU. It holds the parts that do not touch a register and can
therefore be tested on the host:

- the **transmit gate** of a bus, following the readiness of its transceiver (`CanPhyGate`, role `can_phy`);
- the **readiness reports** of the transceiver providers (`CanPhyReadiness`, COM-23);
- the **supervision cycle** that evaluates gate and bus state (`CanSupervisionCycle`);
- the **bus state** with bus-off counters (`CanBusMonitor`, COM-12);
- the **behaviour after bus off** when bus off keeps coming back: logged in series, doubling pause, dropping the
  pending frames (`CanBusOffRecovery`, COM-35; project values per bus since COM-37);
- the text of the console command `can status` (`CanChannelStatus`, COM-24) and the lines of `can log`
  (`CanFrameLog`, COM-25).

The buses themselves - controller, interrupts, lifecycle component and console command - live in the port
(`platforms/s32k1xx/port/main/src/systems/CanSystem.cpp`, class `systems::CanSystem`), which reads the controller
and feeds the state into these classes.

**State:** verification `hardware` for gate, bus state up to error passive and console commands on one bus;
bus-off and a second bus only in host tests (see [Tests](#tests)).

## Interface

| Header | Content |
|---|---|
| `can/CanPhyGate.h` | `CanPhyGate::isReady(status)` / `isReady(phy, busId)`, `decide(open, muted, ready)` -> `Action::NONE/MUTE/UNMUTE`, `isTransmitAllowed(open, ready)` |
| `can/CanPhyReadiness.h` | `ICanPhyReadinessListener::phyReadinessChanged(phy)`; `CanPhyReadiness::setListener`, `removeListener`, `hasListener`, `notify(phy)` |
| `can/CanSupervisionCycle.h` | `GATE_CYCLE_MS` = 10, `BUS_STATE_CYCLE_MS` = 10; `run()` returns `true` when the bus state is due |
| `can/CanBusState.h` | `enum class CanBusState` (`UNKNOWN`, `ERROR_ACTIVE`, `ERROR_PASSIVE`, `BUS_OFF`); `CanBusMonitor` with `fromFaultConfinement`, `fromFlags`, `name`, `isBusError`, `update(state, elapsedMs)`, `stop()` and the counters |
| `can/CanBusOffRecovery.h` | `CanBusOffRecovery::Config` (`firstPauseMs`, `maxPauseMs`, `discardAfter`, `quietMs`, `summaryMs`), `DEFAULT_CONFIG` = {10, 1000, 3, 2000, 10000}; `feedMonitor(monitor, state, enteredBusOff, elapsedMs, events)`, `wentBusOff()`, `recovered()`, `stop()`, `cycle(busOff, elapsedMs)` -> `Step` (`discard`, `release`, `logDiscard`, `logSummary`, `logSeriesEnd`), series and summary counters |
| `can/CanChannelStatus.h` | `struct CanChannelStatus` (snapshot of one bus), `writeChannelStatus(writer, status)` |
| `can/CanFrameLog.h` | `CanFrameLog::lineCount`, `formatLine`, `entryBytes`; `BYTES_PER_LINE` = 16, `LINE_CAPACITY` = 96 |
| `can/ContextCall.h` | alias `can::ContextCall` of `console::ContextCall` from [`consoleContext`](../../consoleContext/doc/consoleContext.md) |

CMake target `canSystem` (INTERFACE, links `bsp` and `consoleContext`).

## Configuration

The module has no `board.yaml` keys and no module parameters
([modules reference](../../../../doc/reference/modules.md#m-cansystem)). The buses come from `can[]` of the board
and `use.can`/`can.<bus>` of the project (see the [board](../../../../doc/reference/board.md#f-can-list) and
[project](../../../../doc/reference/project.md#f-can-key) references). Per used bus the manifest requires one
provider of the role `can_phy` - the module of the bus's transceiver, for example `sbcUja113x` or
[`canPhyPassive`](../../canPhyPassive/doc/canPhyPassive.md).

Manifest: `lifecycle: { level: 2, task: can }`, console command group `can`.

### Behaviour after bus off (project `can.<bus>.bus_off`, COM-37)

Project values per bus, next to the bit timing of the bus. Every key may be left out; a missing entry means the
defaults, which are `CanBusOffRecovery::DEFAULT_CONFIG` (a generator test compares both).

| Key | Default | Range | Meaning |
|---|---|---|---|
| `first_pause_ms` | 10 | 0..65535 | pause before the first release of a series (one supervision cycle) |
| `max_pause_ms` | 1000 | 0..65535 | limit of the pause, which doubles with every bus off of the series; `0` = the controller is not held and recovers by itself |
| `discard_after` | 3 | 0..255 | from this bus off of a series on the pending transmit frames are dropped before the release; `0` = never |
| `quiet_ms` | 2000 | 1..65535 | a series ends after this long without bus off |
| `summary_ms` | 10000 | 1..65535 | at most one summary line per this period while a series lasts |

```yaml
can:
  CAN0: { bitrate: 500000 }                         # defaults
  CAN1:
    bitrate: 250000
    bus_off: { max_pause_ms: 200, discard_after: 1, quiet_ms: 500, summary_ms: 1000 }
```

`tools/nxconfig` checks the values against each other, with YAML path and line: `first_pause_ms` 0 or above
`max_pause_ms` while the bus is held is an error; `first_pause_ms` set with `max_pause_ms` 0 and `quiet_ms` not
above `max_pause_ms` are warnings. `tools/bswgen` generates `bsp/can/canBusOffConfiguration.h` with
`::bsp::can::<bus>_BUS_OFF_CONFIG` for every used bus; the family port gives each bus its own `CanBusOffRecovery`
with these values.

## Behaviour

### Transmit gate

- `CAN_PHY_ERROR` means "not ready"; `CAN_PHY_ERROR_NONE` and `CAN_PHY_ERROR_UNSUPPORTED` (no status available)
  count as ready.
- Open and not ready -> `MUTE`: the port stops transmitting on that bus and keeps receiving. Muted and ready
  again -> `UNMUTE`. Frames may be written only while the transceiver is open and ready.

### When the gate is evaluated

- A provider whose readiness can change calls `CanPhyReadiness::notify(phy)` after every change, in its own
  task. The registered listener (the CAN system of the port) only schedules its gate evaluation in its own task;
  several reports may collapse into one evaluation.
- There is one listener per ECU; `setListener` refuses a second one. The pointer is held in one
  `std::atomic` with plain loads and stores, no compare-and-swap (comment: ARMv6-M has none).
- As fallback for a provider that does not report, the supervision cycle checks the gate every 10 ms
  (`GATE_CYCLE_MS`). A passive transceiver never reports.
- History recorded in the header: COM-22 ran the gate every 1 ms (+1.15 % CPU measured on the eval board);
  COM-23 went back to 10 ms plus the reports.

### Bus state

- `fromFaultConfinement` maps the CAN encoding of FLTCONF (0 error active, 1 error passive, 2 and 3 bus off);
  `fromFlags` serves controllers with separate error-passive and bus-off flags.
- `update(state, elapsedMs)` returns the edge (`STARTED`, `WENT_BUS_OFF`, `RECOVERED`, `WENT_PASSIVE`,
  `WENT_ACTIVE`) and counts bus-off entries, recoveries, error-passive entries and the current, longest and total
  bus-off time (saturating). `stop()` sets the state back to `UNKNOWN` and keeps the counters.
- Only `BUS_OFF` is a bus error (`isBusError`, used for the signal qualifier, see
  [`signal-qualifier.md`](../../../../doc/architecture/signal-qualifier.md)). Error passive is deliberately not
  one: a node alone on the bus reaches it regularly.
- The class triggers no recovery; whether the controller recovers from bus off automatically is reported by the
  caller.
- The bus state is evaluated every `BUS_STATE_CYCLE_MS` = 10 ms.

### After bus off (`CanBusOffRecovery`)

- The port reads its controller every supervision cycle, including the latched "went bus off" flag (FlexCAN
  ESR1[BOFFINT], cleared after reading), and passes both to `feedMonitor()`. With the flag the monitor sees both
  edges of a bus off that started and ended between two readings.
- The first bus off of a series and its recovery are logged; further bus offs of the series are counted and
  summarised in at most one line per `summaryMs`; the end of the series (`quietMs` without bus off) is one line.
- With `maxPauseMs` > 0 the port holds the controller in bus off (FlexCAN CTRL1[BOFFREC] = 1) and releases it when
  `cycle()` says so, after a pause that doubles with every bus off of the series (`firstPauseMs` .. `maxPauseMs`).
  A controller without a way to hold bus off runs with `maxPauseMs` 0.
- From the `discardAfter`-th bus off of a series on, `cycle()` asks to drop the pending transmit frames before the
  release.
- The class touches no register and needs no clock; one instance per bus. Origin: board finding 2026-09-22 (a frame
  hanging in a transmit buffer with CANH shorted to CANL: 9812 bus offs in about ten minutes, two log lines each).

### Console text

- The port fills a `CanChannelStatus` in the CAN task and prints it with `writeChannelStatus` in the console
  context, so the CAN task no longer waits for the UART. The command itself runs through `ContextCall`
  (see [`consoleContext`](../../consoleContext/doc/consoleContext.md)). Commands: see
  [`can` in the console reference](../../../../doc/reference/console.md#c-can).
- The status text also shows the state the OpenBSW driver keeps, because that driver misreads FLTCONF; the port
  answers from the correctly read field instead (explained in `CanSystem.h` of the port, external/openbsw
  is not changed).
- `can log`: each received frame becomes one or more lines of at most 16 payload bytes; the first line carries
  the length, further lines the byte offset (`+16`, `+32`, `+48`). Each line is logged as one `"%s"` argument, so a
  logger entry has a fixed upper size (`entryBytes`) that the port checks at compile time. Bus names longer than
  16 characters are cut.

## Limits and known gaps

- Bus-off has not been forced on a board; bus-off handling and a second bus are covered by host tests only
  (verification note).
- Today only the s32k1xx family port uses the module (`platforms/s32k1xx/port/main`); no other port under
  `platforms/` includes these headers. `CanBusOffRecovery` knows no controller; the generated
  `bsp/can/canBusOffConfiguration.h` comes from the s32k1xx generator only (`tools/bswgen/bswgen.py`), a port of
  another family needs the same header from its generator.
- In the s32k1xx port the recovery state of a bus sits in a table found by bus id, not yet as a member of
  `CanSystem::Channel` (the port header was outside COM-37; proposal in the changelog of COM-37).
- The status text (`CanChannelStatus`) contains FlexCAN register names (CTRL1, ESR1, FLTCONF); what a
  controller of another family fills in there is not defined yet.

## Tests

Host-Tests, Testnamen und Verifikationsstand: [Modulkatalog](../../../../doc/reference/modules.md#m-cansystem).
