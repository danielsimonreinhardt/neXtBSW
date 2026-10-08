# Sample group (`bspSampleGroup`)

L1 contract of a **sample group**: a named set of analogue channels that is converted after every hardware trigger
of a PWM group, without software, with one interrupt at the end of the whole group. It is the event source of the
fast control loop (fast-control-loop.md (not published, available on request), section 3, IO-62).
Header only, board and family neutral.

**State:** host tests only (contract and s32k1xx family part); no image uses a sample group yet, the board port that
arms it comes with the fast-loop dispatcher (ARCH-65). Board proof open.

## Contract

| Element | Meaning |
|---|---|
| `init()` | converters and sequencers configured (and calibrated), nothing triggers yet |
| `arm()` / `disarm()` | the chain follows or ignores the triggers of the PWM group; the PWM itself is not touched |
| group complete | one interrupt per trigger, after the **last** conversion of the group; the family entry calls `Consumer::complete(Result const&)` of a static consumer type (no run-time registration) |
| `Result` | raw values in project order, number of values, error bits of this completion, sequence number since `arm()`, counter of the PWM group at interrupt entry |
| errors | `ERROR_SEQUENCE` (a conversion was triggered while its converter was busy), `ERROR_CONVERSION_MISSING` (another converter had not finished at group complete) |

`sampleGroup/SampleGroup.h` also holds the neutral bookkeeping every family implementation shares (`Bookkeeping`:
arms, completions, error counts, recoveries, sequence number that skips 0 on a wrap) and `missedBetween()`, the
number of completions a consumer missed between two sequence numbers. `NoConsumer` takes nothing.

The contract knows no phases, motors or registers. A raw value is converted by the consumer (IO-63/IO-64).

## Configuration

Project (`project.yaml`), checked by `tools/nxconfig/samplegroup.py`:

```yaml
pwm:
  BRIDGE: { frequency_hz: 20000, deadtime_ns: 500, alignment: center, trigger: { point: zero_low } }
sample_groups:
  MOT_SAMPLE: { pwm: BRIDGE, channels: [PHASE_I_U, PHASE_I_V, PHASE_I_W, DC_LINK_U], delay_ns: 500 }
interrupts:
  ranks:
    - { source: sample_group, name: MOT_SAMPLE, rank: 0 }
```

- `channels` are board ADC entries; the board already states on which ADC each one is (`adc[].instance`).
  Channels on different ADCs are sampled at the same instant, channels on one ADC one after another in the listed
  order - `nxconfig` warns and names the time offset.
- The instant is the trigger point of the PWM group (IO-61) plus `delay_ns`; `nxconfig check` prints the sample
  instant of every channel and the end of the group, which must lie inside one PWM period.
- The ADCs of a group are **exclusive** to it: a software conversion on the same ADC (`use.adc`, `adc_internal`) is
  an error. On s32k1xx the trigger mode is one setting per converter (`SC2[ADTRG]`); see
  [bspAdcAccess](../../bspAdcAccess/doc/bspAdcAccess.md#hardware-triggered-sample-groups).
- The derivative describes the trigger router and the sequencers (`mcudb` `trigger_router`, `sample_sequencers`);
  a derivative without them rejects `sample_groups`.

## Family implementations

| Family | Chain | Code |
|---|---|---|
| s32k1xx | FTM trigger (INITTRIG or channel match) -> TRGMUX_PDBx -> PDBx channel 0 (delay, back-to-back pre-triggers) -> ADCx slots, AIEN on the last slot of the sequencer that ends last | `platforms/s32k1xx/bsp/bspSampleGroup` (`SampleGroupChain.h`), generated `bsp/sampleGroup/sampleGroupConfiguration.h` |
| stm32, s32k3xx, rm4x | not implemented (timer TRGO -> injected group, BCTU, MibADC group; fast-control-loop.md 3.2) | - |

## Tests

Host-Tests, Testnamen und Verifikationsstand: [Modulkatalog](../../../../doc/reference/modules.md#m-bspsamplegroup).
