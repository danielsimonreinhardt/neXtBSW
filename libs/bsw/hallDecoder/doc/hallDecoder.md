# Hall decoder (`hallDecoder`)

Hardware independent evaluation of three digital Hall signals of a brushless motor (IO-07): state, sector,
electrical and mechanical period, speed, direction and a quality, from sampled states and microsecond time
stamps. It knows no MCU, register, pin or clock.

`kind: service`, not a device driver: it serves no board part and has no connection of its own. The board device,
its wiring and its project parameters belong to the device driver of the family - for s32k1xx the port module
[`hall`](../../../../platforms/s32k1xx/port/bspConfiguration/src/bsp/hall/doc/hall.md) with the access in
`platforms/s32k1xx/bsp/bspHall` - which hands the decoder a `HallDecoderConfig` derived by `tools/bswgen`. Reasoning:
comment in `module.yaml` and [`treiber-granularitaet.md`](../../../../doc/architecture/treiber-granularitaet.md).

The module was built deliberately without known Hall hardware (user decision recorded in `CLAUDE.md`).

**State:** verification `mock` - host tests only (see [Tests](#tests)).

## Interface

Header `hallDecoder/HallDecoder.h`, source `src/hallDecoder/HallDecoder.cpp`, CMake target `hallDecoder`.

| Item | Content |
|---|---|
| `HallDecoderConfig` | `sectors[8]` (state -> sector 1..6, 0 = the arrangement cannot produce it), `forwardSign` (+1/-1), `polePairs`, `debounceUs` (0: off), `standstillUs` |
| `isValidSectorTable(cfg)` | exactly six states map to the sectors 1..6, each once; `polePairs` != 0; `forwardSign` is +1 or -1 |
| `isValidTiming(cfg)` | `standstillUs` != 0, six standstill times fit into half a 32-bit wrap (`standstillUs <= 0xFFFFFFFF / 12`), `debounceUs < standstillUs` |
| `HallDecoder::start(state, nowUs)` | start from the position the motor stands in |
| `HallDecoder::edge(state, nowUs)` | one of the levels may have changed; state sampled now |
| `HallDecoder::read(state, nowUs)` -> `Reading` | current values; latches a standstill and settles a provisional step, therefore not `const` |
| `Reading` | `period_us`, `mech_period_us`, `speed_rpm`, `state`, `sector`, `direction`, `quality` |
| `Statistics` | `edges`, `bounces`, `invalidStates`, `sectorJumps`, `standstills`, `glitches` |
| `QUALITY_*` | `DEBOUNCE` 0, `OK` 1, `TIMEOUT` 8, `ERROR` 16 - the `QLF_*` values of the RTE (`tools/rtegen/test_rtegen.py` compares both lists) |
| `DIRECTION_*` | `UNKNOWN` 0, `FORWARD` 1, `BACKWARD` -1 |

`state` is `(u << 2) | (v << 1) | w` with the inversion of the board already applied; `nowUs` is a free-running
32-bit microsecond time that may wrap.

## Configuration

No keys of its own ([modules reference](../../../../doc/reference/modules.md#m-halldecoder)). The values are per
motor and stand per device under `modules.<device>` of the `hall` device driver (`arrangement`, `pole_pairs`,
`forward`, `min_speed_rpm`, `max_speed_rpm`, `debounce_us`, `standstill_ms`, see
[`hall` in the modules reference](../../../../doc/reference/modules.md#m-s32k1xx-hall)); `tools/bswgen` turns them
into `HallDecoderConfig`.

## Behaviour

- **Calls:** the access calls `edge()` whenever a level may have changed (on s32k1xx in the pin interrupt) and
  `read()` when a value is wanted. The access serializes both calls; the decoder takes no lock.
- **Sector:** from the sector table; a state the arrangement cannot produce gives sector 0.
- **Period:** time between an edge and the sixth edge before it (one electrical revolution), reported only after
  six consecutive steps in the same direction, so a missed edge cannot shorten it. No assumption of equal sector
  widths. Mechanical period = electrical period x pole pairs; the speed follows from it.
- **Direction:** from the transition (new sector is the neighbour above or below); `forwardSign` only chooses the
  sign.
- **Debouncing:** an edge closer than `debounceUs` to the last accepted edge is counted as a bounce and ignored.
  An accepted edge stays provisional until its debounce window has passed; then the level at the end of the window
  is compared. A glitch that ended on the old level is taken back completely (state, sector, run, stamps, fault)
  and counted as a glitch; a window that ended on a third state takes the glitch back and accepts that state with
  the time of the glitch. The check runs at the next edge or in `read()`, whichever comes first.
- **Quality**, in this order of precedence:
  1. `QUALITY_TIMEOUT` - standstill: no accepted edge within `standstillUs` (also: no sensor at all). State and
     sector stay valid, period and speed keep the last measured values, direction becomes unknown. The standstill
     is latched until the next accepted edge and discards the running revolution.
  2. `QUALITY_ERROR` - an invalid state or a jumped sector, until a clean revolution follows.
  3. `QUALITY_DEBOUNCE` - fewer than six consecutive steps in one direction so far.
  4. `QUALITY_OK` otherwise. The cause of a fault is visible in `statistics()`.

## Limits and known gaps

- Only the two arrangements whose sector table the generator derives (120 and 60 degrees, see the `hall` module)
  are tested; the decoder itself accepts any table that passes `isValidSectorTable`.
- The only access today is the pin interrupt of the s32k1xx family (`bspHall`). A timer-capture access or another
  family is mentioned in `module.yaml` as possible, but does not exist.
- No Hall sensor or signal source has been connected to a board (verification note).

## Tests

Host-Tests, Testnamen und Verifikationsstand: [Modulkatalog](../../../../doc/reference/modules.md#m-halldecoder).
