<!-- SPDX-License-Identifier: Apache-2.0 -->

# bspPinEvent — events on digital inputs

Status: IO-53/IO-54, 2026-09-29. Host tests and target builds; linked into a project with `pin_events` since IO-54
(generated table, s32k1xx adapter, RTE targets, console); no board test yet (IO-56). Concept: [pin interrupts](../../../../doc/architecture/pin-interrupts.md), variant A with the answers to
F2–F4 (logical edge, software debounce, automatic re-arm).

The module counts edges on digital inputs and offers per input what the RTE later maps to
`pinevent.<NAME>.count`, `.time_us` and `.qlf`: the number of accepted edges, the time of the last one in
microseconds and a signal qualifier. It debounces in software and protects the CPU against interrupt storms with a
rate limit. It is family neutral: no register, no pin, no clock, no heap, no RTOS object.

## Place in the layers

```
 RTE                  PinEvents::read()            per task cycle, consistent copy
 console              PinEventCommand "pinevent"   read only, console context
 owning task          PinEvents::cycle()           re-arm after the hold-off, expiry
 ----------------------------------------------------------------------------------------
 bspPinEvent          PinEvent (one per input), PinEvents (the table)
 ----------------------------------------------------------------------------------------
 family dispatcher    PinEvents::onEdge(input, nowUs, level)      per vector, IO-52
 (platforms/<family>) IPinEventHardware: level(input), arm(input, Detect)
```

The module sits in `libs/bsp` because it is the consumer end of the pin interrupt contract: the family dispatcher
links it directly, like `bspAdcAccess` sits on the ADC contract. It is a `service`, not a device driver: it serves no
board part.

## Interfaces

**Dispatcher → module (ISR side).** For every edge of an armed input the dispatcher calls

```cpp
bool PinEvents::onEdge(uint8_t input, uint32_t nowUs, bool level);
```

- `input`: index into the generated table (the dispatcher's pin-to-consumer table carries it),
- `nowUs`: free-running 32-bit microsecond time of the edge (wraps; the same base as the task side's time source),
- `level`: the **logical** level (true = active, the board inversion applied), sampled **after** the interrupt flag
  was cleared, so every later change raises another interrupt,
- return `false`: the dispatcher disarms the pin (edge detector off) and calls the module no more for it until the
  task side arms it again. `false` also comes for an unknown index or an input that was never started.

The call is constant time: no loop, no call out, only the input's own state. Target size of `PinEvent::onEdge`
with the demo flags (Cortex-M4, -O2): 366 bytes of code.

**Module → family (task side only).** `IPinEventHardware` samples the logical level of an input and programs its edge
detector (`Detect::NONE | ACTIVATE | DEACTIVATE | BOTH`, logical like the project). The family turns the logical
edge into the physical one (rising/falling from the board's active level) and into its register values.
`detectorEdges(config)` says what to program: **both edges whenever `debounceUs > 0`** (the level confirmation needs
every change), otherwise the configured edge.

**Time source.** `PinEvents` takes a function returning the same microsecond time as the dispatcher's time stamps.
Readers call it *inside* their copy, so no edge in the copy is younger than the time they compare with.

## Behaviour

| Topic | What the module does |
|---|---|
| Counter | `uint32_t`, wraps from `0xFFFFFFFF` to 0 (defined unsigned arithmetic; a consumer counts differences) |
| Time stamp | time of the last accepted edge; with debounce the time of the edge that opened the lock-out, not of the confirmation |
| Qualifier | `QUALITY_DEBOUNCE` (0, `QLF_DEBOUNCE`: no accepted edge yet, time not valid), `QUALITY_OK` (1), `QUALITY_ERROR` (16, `QLF_ERROR`: disarmed by the rate limit). The values are the RTE's `QLF_*`; the RTE has no "not available" value, and `DEBOUNCE` is its meaning of "not valid yet" |
| Debounce | an edge outside the lock-out opens it (provisional edge towards the other level); edges within `debounceUs` are bounces. After the lock-out the last level reported inside it is the level at its end: equal to the new level → confirmed (counted if it is the configured edge), else a glitch, taken back and counted as such. `read()` shows the confirmation as soon as the lock-out has passed; the next `onEdge()` commits the same result (no double count) |
| Edge filter | `activate`, `deactivate` or `both`, relative to the board's active level. With debounce the other direction is tracked but not counted |
| Rate limit | a window opens with the first candidate edge of the configured direction and lasts `windowUs`; the candidate that makes it `maxEvents + 1` disarms the input, `QUALITY_ERROR`, trips + 1. Candidates count when they open a lock-out, so a storm of spikes that are all taken back as glitches trips too. More than `MAX_BOUNCES` (32) bounces inside one lock-out trip as well |
| Re-arm | `PinEvents::cycle()` in the owning task: `rearmUs` after the trip it samples the level, starts a fresh window and lock-out and arms the detector again. The qualifier returns to `OK` (or `DEBOUNCE` without any accepted edge); edges while disarmed are lost, which the `ERROR` phase told the consumer |
| Expiry | an input that stays quiet for longer than a wrap of the 32-bit time (71.6 min) must not alias into its old lock-out or window. `cycle()` marks a history older than `EXPIRE_AFTER_US` (2^30 µs, 17.9 min) by the sequence value it saw; the next `onEdge()` finds the mark and closes lock-out and window, `read()` treats a marked pending edge as settled. `cycle()` must run at least every ~53 min |

Worst-case interrupt load of one input: `maxEvents` candidate edges per window, plus with debounce at most one edge of
the other direction per `debounceUs` and `MAX_BOUNCES + 1` interrupts per lock-out — whatever the line does.

Configuration rules (`isValidConfig`, the same as `tools/nxconfig/pinevent.py`): edge 1..3, `debounceUs` ≤ 1 s,
`maxEvents` 1..65535, `windowUs` 1 µs..60 s, `rearmUs` 1 µs..600 s, `debounceUs · maxEvents < windowUs`.

## Hand-over between the sides (lock free)

Each input has a sequence counter. The ISR side is the only writer while the input is armed: it makes the counter
odd, writes, makes it even again. A reader copies the state between two reads of the counter and retries at most
`READ_ATTEMPTS` (4) times; if an interrupt wrote each time (only possible in a storm, which the limit ends) the read
fails and leaves its output unchanged — the RTE keeps its last value. The task side writes the state only while the
input is disarmed (start, re-arm after a trip), so there is never a second writer; a reader that preempts it sees an
odd counter and does not use the copy. The expiry mark is a single word written by the task side only. Single core:
compiler barriers (`std::atomic_signal_fence`) are enough; no exclusive access and no libatomic, which ARMv6-M lacks
(checked by the target compile).

## Console

Group `pinevent` (`PinEventCommand`, text in `PinEventConsole.h`), read only and lock free, so it runs in the console
context without a hop into the owning task:

```
> pinevent show
pin events: 2
DOOR: count 1, last 5000 us, qlf OK (1), trips 0, bounces 0, glitches 0, armed
PULSE: count 2, last 5001 us, qlf ERROR (16), trips 1, bounces 0, glitches 0, off, re-arm in 246 ms
> pinevent config
DOOR: edge activate, debounce 2000 us, limit 10 per 100000 us, re-arm after 100000 us, detector both
```

## Safety classification

Driver robustness (QM), **not** a credited base-software safety mechanism (safety concept section 1.4, strict
separation). The rate limit protects CPU availability; the credited detection of a delayed safety task stays SM-04
(period check), SM-06 (halt deadman) and SM-01 (watchdog), DF-37. Coexistence row CA-54 in
[`doc/safety/coexistence.md`](../../../../doc/safety/coexistence.md). If an integrator uses a pin event as input of a
safety function, the qualifier gives the information; the claim is his (assumption of use).

## Integration (IO-54)

Only a project with `pin_events` links anything of the module (`bsp/pinIrq/pinIrqConfiguration.cmake`:
`NX_PIN_EVENT_COUNT`).

- tools/bswgen writes `bsp/pinEvent/pinEventConfiguration.h` (family neutral): `pinevent::project::CONFIG[]` in the
  order of `pin_events` (`windowUs = window_ms · 1000`, `rearmUs = rearm_ms · 1000`, `name` = board input), the
  indices `Pe_<NAME>`, `static_assert(isValidConfig(...))` per entry, and `NX_PIN_EVENT_COUNT` (0 without events).
  The family part (pins, inversion, the dispatcher table with the edge of `detectorEdges()`: both while debouncing,
  else the physical edge of the configured one) is `bsp/pinIrq/pinIrqConfiguration.h`.
- s32k1xx: `bios::PinEventAdapter` (`platforms/s32k1xx/bsp/bspPinIrq`, CMake target `bspPinIrqPinEvent`) is the
  consumer of the dispatcher and the `IPinEventHardware`: it samples GPIO PDIR with the inversion of the board after
  the flag was cleared, calls `PinEvents::onEdge()` and switches the detector off on `false`; `arm()` maps the
  logical request to `PORT_PCR.IRQC` through `PinIrqDispatcher::setEdge()`. The instances (`PinEvent[]`,
  `PinEvents`, adapter, dispatcher) live in `bspConfiguration/src/bsp/pinIrq/PinIrqConfig.cpp`, all constant
  initialized.
- Start and cycle: `bsp::startPinInterrupts()` (dispatcher init → Hall decoders and `PinEvents::start()` → enable)
  once from the RTE init, `bsp::cyclePinEvents()` every cycle of one task (the first task that reads a pin event,
  else the first application task) - also when no component reads the events.
- tools/rtegen: targets `pinevent.<NAME>.count` (uint32), `.time_us` (uint32), `.qlf` (uint8/16/32, `QLF_*`) from
  `PinEvents::read()`; on `false` the buffers keep the values of the cycle before. `test_rtegen.py` compares the
  `QUALITY_*` values of `PinEvent.h` with `rtegen.QLF`; rtegen warns when `.qlf` of a read input is not connected.
- Console: `BspSystem` of the s32k1xx port registers one `PinEventCommand` in the console context (target
  `bspPinEventCommand`).
- nxconfig: with `debounce_us > 0` the derivative must detect `both` edges (the level confirmation needs every
  change).

## Tests

Host-Tests, Testnamen und Verifikationsstand: [Modulkatalog](../../../../doc/reference/modules.md#m-bsppinevent).
