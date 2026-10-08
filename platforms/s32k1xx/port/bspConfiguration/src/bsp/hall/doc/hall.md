# Hall sensor set, s32k1xx (`hall`)

Port module of the s32k1xx family and device driver for a set of three digital Hall signals (U, V, W) of a
brushless motor (IO-07): state, sector, electrical and mechanical period, speed, direction and a quality,
measured in the pin interrupt of the port with a time stamp per edge. Every sensor set of a board is one instance.

The work is split by what is family knowledge ([`treiber-granularitaet.md`](../../../../../../../../doc/architecture/treiber-granularitaet.md)):

- **access** (family): `platforms/s32k1xx/bsp/bspHall`, class `bios::HallSensors` - samples the levels and
  hands state and time to the decoder (CMake target `bspHall`);
- **pin interrupt** (family, since IO-52): the pin-interrupt dispatcher `platforms/s32k1xx/bsp/bspPinIrq`
  (`bios::PinIrqDispatcher`) arms both edges on the Hall pins, owns the pin-detect vectors, clears only the flags
  of pins with a consumer and stamps the time; the Hall pins are one consumer of it
  ([pin edge contract](../../../../../../../../libs/bsp/bspPinEdge/doc/bspPinEdge.md), `hall/HallPinIrq.h`:
  channel = sensor index);
- **interrupt glue** (family port): `../pinIrq/PinIrqConfig.cpp` takes the dispatcher table tools/bswgen
  generates (`bsp/pinIrq/pinIrqConfiguration.h`: Hall pins and pin events, vectors from the derivative fact
  `pin_interrupts`, IO-54), defines the pin-detect handlers, wrapped by the safety ISR hooks like the CAN and UART
  interrupts, and starts all consumers once (`bsp::startPinInterrupts()`, called by the generated RTE);
- **evaluation** (family neutral): [`hallDecoder`](../../../../../../../../libs/bsw/hallDecoder/doc/hallDecoder.md).

The module was built deliberately without known Hall hardware (user decision recorded in `CLAUDE.md`).

**State:** verification `mock` - host tests only, no Hall sensor or signal source on a board yet (see
[Verification](#verification)).

## Interface

`hall/HallSensors.h` (in `platforms/s32k1xx/bsp/bspHall/include`):

| Function | Content |
|---|---|
| `init()` | starts every decoder from the position sampled now. Called by `bsp::startPinInterrupts()` of the port between the arming of the pins and the enabling of the vectors (dispatcher `init()`/`enable()`), after the pin configuration of the board port |
| `edge(sensor, time)` | an edge on a pin of the sensor, called by the dispatcher (through `hallEdgeConsumer`) with the time of the interrupt: the sampled state goes to the decoder |
| `get(sensor)` -> `Reading` | state and time sampled and the decoder read under one interrupt lock; callable from any task, no fixed cycle |
| `getStatistics(sensor)`, `getName(sensor)`, `SensorCountAll` | counters, board name, number of sensors |

`Reading`, `Statistics`, `QUALITY_*` and `DIRECTION_*` are those of `hallDecoder`. Without a `hall` device in the
project the class compiles to empty functions and every sensor reports `noReading()`.

The RTE reads the values through `rte.yaml` connections `hall.<Sensor>.<value>` with `<value>` one of `state`,
`sector`, `period_us`, `mech_period_us`, `speed_rpm`, `direction`, `qlf` (`tools/rtegen`, `HALL_VALUES`).

## Configuration

Board (`board.yaml`) - what is wired:

- a `devices[]` entry with `part` matching `hall3` or `hall3_*` (the class of the interface: three digital
  signals, one level each; a board with a fitted sensor adds its part number to the manifest);
- `devices[].connect.u`, `.v`, `.w` -> entries of `dio.inputs[]`, in the electrical order of the motor phases;
- per input `pull` (`up`/`down`, omitted: the board or the sensor drives both levels) and `active: low` for a
  signal the board inverts ([board reference](../../../../../../../../doc/reference/board.md#f-dio-inputs-list)).

Project (`project.yaml`, `modules.<device>`) - what the motor is: `arrangement` (`deg120`/`deg60`), `pole_pairs`
(required), `forward` (`sector_up`/`sector_down`), `min_speed_rpm` and `max_speed_rpm` (required), `debounce_us`
(default 20), `standstill_ms` (default 200); rule `max_speed_rpm >= min_speed_rpm`. Full descriptions in the
[modules reference](../../../../../../../../doc/reference/modules.md#m-s32k1xx-hall).

Interrupt rank: every Hall signal needs a rank in `interrupts.ranks[]` (source `dio`). All Hall signals of one port
must have the same rank, because they share the pin interrupt of that port, and the three signals of one sensor
must have the same rank, so its interrupts cannot preempt each other - `tools/bswgen` refuses otherwise
([project reference](../../../../../../../../doc/reference/project.md#f-interrupts-ranks-list)).

Derived by `tools/bswgen`, not configured twice: sector table, invalid states, inversion mask, pin masks, interrupt
vectors with their level and the plausibility of the times (debouncing against the shortest sector time at
`max_speed_rpm`, standstill against one electrical revolution at `min_speed_rpm` and against the wrap of the time
base). Outputs: `integration/hallConfiguration.h`, `bsp/hall/hallSensorConfiguration.h`,
`bsp/hall/hallDecoderConfiguration.h`.

## Behaviour

- Time base: `getSystemTimeUs32Bit()` of the OpenBSW system timer (32 bit, wraps after about 71.6 minutes). `get()`
  takes the time inside the lock, so an edge between sampling and reading cannot look like a standstill.
- Debouncing is done in the decoder, not with the digital input filter of the port: that filter is one setting per
  port and would change every other input of the port (manifest, `debounce_us`).
- `PinIrqConfig.cpp` checks at compile time that the vector table of the family port has a pin-detect vector for
  every port with a Hall signal; otherwise the handler would never run and the measurement would report standstill
  for ever. It also checks the dispatcher table (one consumer per pin, every port served by exactly one vector).
- Several flagged pins of one sensor in one interrupt give several calls with the same time; the decoder does not
  step on a repeated state, so the result is the same as the single call per port before IO-52.
- Evaluation, qualities and standstill: see [`hallDecoder`](../../../../../../../../libs/bsw/hallDecoder/doc/hallDecoder.md#behaviour).

## Limits and known gaps

- Open in todo IO-23: a console group `hall` and the board test with a signal source. The pin-interrupt
  dispatcher of that item exists since IO-52; the pin events of the project (IO-53/IO-54) become its second
  consumer.
- Until IO-54 the dispatcher table is derived from the Hall configuration only, and the generated vector list
  names one vector per port (`PORT<x>_IRQn`): a Hall sensor on an S32K11x (one `PORT` vector) is not supported yet.
- s32k1xx pin interrupt only; a timer-capture access or another family would bring its own access and manifest
  (comment in `module.yaml`).
- The host tests lie outside this directory (`platforms/s32k1xx/bsp/bspHall/test`), so the
  [modules reference](../../../../../../../../doc/reference/modules.md#m-s32k1xx-hall) shows "Host tests: none" for
  this module.

## Tests

`tools/test -k bswgen` (entry `bswgen`, group python), in `tools/bswgen/test_bswgen.py`:

- `BswGenHallTest` - generator on an own profile with two sensor sets (120 degrees, one pole pair, one port;
  60 degrees, eight pole pairs, inverted signals, `sector_down`, two ports) and on the eval board demo profile;
- `BswGenHallHostTest` - compiles `HallSensors` with the decoder and the pin-interrupt dispatcher against the
  generated configuration and runs `platforms/s32k1xx/bsp/bspHall/test/host/HallSensorsHostTest.cpp` against RAM
  models of GPIO and PORT with a pin-detect model (mutants of the access and of the dispatcher): register picture after init, sampled state with inversion, a revolution forwards and
  backwards through the pin interrupts of all ports, standstill, no sensor, two independent sensors, foreign
  interrupt flags, names, state and time under the lock. `NoHallSensorHostTest.cpp` covers a project without a
  Hall device;
- `HallDecoderHostTest` - the decoder itself.

## Verification

From `module.yaml`: `verification: mock` - "Host tests only (decoder and pin interrupt access against two
profiles); no Hall sensor or signal source has been connected to a board yet."
