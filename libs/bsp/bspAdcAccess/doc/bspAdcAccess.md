# Serialized ADC access (`bspAdcAccess`)

Header-only, MCU- and RTOS-neutral layer that serializes synchronous (polled) ADC conversions from several tasks
and from contexts that must not wait (ISR, interrupts or scheduler locked, before the scheduler starts). It also
bounds the polling loop of a conversion by a value derived from the conversion time (IO-14, IO-16, IO-19) and
averages a channel over one PWM period (IO-31).

The module has no board device. Its parameters are module parameters of the project
(`modules.bspAdcAccess`, `project_parameters: true`).

**State:** verification `hardware` for the policy `interrupt_lock`; `rtos_mutex` and converter failures only in
host tests (see [Tests](#tests)).

## Interface

| Header | Content |
|---|---|
| `adc/AdcAccess.h` | `adc::AdcAccess<Lock, Converter, InstanceCount>`: `convert(instance, input, value)`, `getStatistics()`, `resetStatistics()`; `AdcAccessStatistics` |
| `adc/InterruptLockAdcLock.h` | lock policy without RTOS objects: every conversion with all interrupts suspended (OpenBSW `suspendResumeAllInterrupts`) |
| `adc/FreeRtosAdcLock.h` | lock policy with one statically allocated FreeRTOS mutex per converter instance (priority inheritance); needs `configUSE_MUTEXES`, `configSUPPORT_STATIC_ALLOCATION`, `INCLUDE_xTaskGetSchedulerState` and a platform type `InterruptContext::interruptsLockedOrInIsr()` |
| `adc/AdcPollLimit.h` | `constexpr` arithmetic of the polling bound: `sarConversionClocks`, `pollCyclesForClocks`, `pollLimit`, `clocksToNs` |
| `adc/AdcAveraging.h` | `adc::AdcAveraging<Lock>`: `AveragingSpec` (`samples`, `spacingNs`), `AdcAveragingStatistics` |

The **Lock** policy provides `canBlock`, `tryLock`, `lock` (bounded timeout), `unlock`, `enterExclusive`,
`exitExclusive` and `nowNs`; the **Converter** provides `convert(instance, input, value)` and
`restart(instance, input)`. Both are static types (architecture decision D1). Both converter calls must return after
a bounded time even if the converter never signals completion.

CMake target `bspAdcAccess` (INTERFACE; links OpenBSW `bsp` and `bspInterrupts`). A user of `FreeRtosAdcLock.h`
also links FreeRTOS.

## Configuration

Project (`project.yaml`, `modules.bspAdcAccess`); the full table with ranges and defaults is in the
[modules reference](../../../../doc/reference/modules.md#m-bspadcaccess):

| Parameter | Default | Meaning in short |
|---|---|---|
| `lock_policy` | `interrupt_lock` | `interrupt_lock` or `rtos_mutex` |
| `lock_timeout_ms` | 100 | longest wait for the lock (only `rtos_mutex`) |
| `poll_safety_factor` | 16 | polling bound = factor x derived conversion time |
| `max_interrupt_suspend_us` | 50 | interrupt suspension the project accepts for one conversion; checked only, changes nothing generated |
| `sample_clocks` | 42 | sample phase in ADC clocks |
| `clock_divider` | 1 | converter clock divider (family values only) |
| `averages` | 1 | hardware averaging (family values only); multiplies the conversion time |

`sample_clocks`, `clock_divider` and `averages` are converter settings; the comment in `module.yaml` says they
belong to the ADC contract and stay here until that contract exists (ARCH-03).

`tools/nxconfig/adc.py` derives the conversion time and the polling bound from these values, the resolution of the
derivative (`instances.<ADC>.resolution_bits`) and the clock plan. It reports an error for a divider or averaging
count the family converter does not have, for a missing functional clock and for used ADC instances at different
clocks, and a **warning** if one conversion (with `rtos_mutex`: two, for a caller that cannot wait) suspends the
interrupts longer than `max_interrupt_suspend_us`. `tools/bswgen` renders `integration/bspAdcAccessConfiguration.h`
and `bsp/adc/adcAccessConfiguration.h`.

The averaged channels are configured under `adc_sampling.<key>` of the project
([project reference](../../../../doc/reference/project.md#f-adc_sampling-key)).

## Behaviour

### Contexts (`AdcAccess`)

- **Task caller** (`canBlock()`): try the lock of the instance, otherwise wait with the bounded timeout (timeout ->
  `BSP_ERROR`, counted). Mark the instance busy, convert with the interrupts enabled, clear busy, unlock. The
  exclusive sections around the busy flag last a few instructions.
- **Caller that cannot wait**: convert inside an exclusive section. If a task conversion of the same instance was
  preempted, restart it afterwards, so the task still gets the value of its own input. Never blocks; added latency
  one or two conversions.
- With `interrupt_lock` every caller takes the exclusive path; there is no waiting and no priority inversion.
- An instance number out of range returns `BSP_ERROR`.
- Single core only: the exclusive section must stop every other caller.

Properties the header adds to the ADC contract: a synchronous read is callable from every context; it returns after a
time derived from the conversion time; how long it may suspend the interrupts is a project decision; a failed read is
reported, never replaced by a value.

### Polling bound (`AdcPollLimit`)

The loop count is the only portable bound. `cycles` assumes one loop iteration per core clock, so `limit =
cycles x poll_safety_factor` is a guaranteed lower bound of factor x conversion time. An unknown converter clock
(0 Hz) gives the largest value, so it can never cause a spurious timeout.

### Averaging (`AdcAveraging`)

`samples` conversions at equally spaced instants over exactly one PWM period, rounded mean of the raw values. Each
conversion goes through `AdcAccess` on its own, so the interrupt suspension stays that of one conversion. The
calling task busy-waits for one PWM period per read; averaged channels are meant for task callers. A failed
conversion or a stalled time source ends the read with `BSP_ERROR`; samples more than a quarter spacing late are
counted.

### Statistics and console

`AdcAccessStatistics`: conversions, contended, lock time-outs, exclusive, nested, errors, longest wait, conversion
and exclusive section. On s32k1xx the console group `adcaccess stats | reset` shows them
([console reference](../../../../doc/reference/console.md#c-adcaccess)).

### Hardware triggered sample groups

A sample group (IO-62, [bspSampleGroup](../../bspSampleGroup/doc/bspSampleGroup.md)) converts its channels after
every trigger of a PWM group without software. Its ADC instances are **exclusive** to that chain, and `AdcAccess`
never converts on them - the decision of IO-62, for these reasons:

- On s32k1xx the trigger mode is one setting per converter (`SC2[ADTRG]`). With the hardware trigger selected, the
  software start of `convert()` (write `SC1[0]`) starts nothing and the polling bound expires; switching the mode
  per conversion would drop the triggers of the chain for that time.
- A software conversion in between would take a result register of the sequence and delay its conversions; the
  sample instant the fast loop relies on would move by up to one conversion.
- The lock policies of this module suspend the interrupts for a whole conversion (`interrupt_lock`); an exclusive
  instance keeps that suspension out of the latency of the sample group interrupt (fast-control-loop.md 7.2).

`tools/nxconfig` therefore rejects a project that converts in software (`use.adc`, `adc_internal`) on an ADC of a
sample group, with the YAML path of the group channel. The converter settings of this module (`sample_clocks`,
`clock_divider`, `averages`) still apply: the sample group uses the same CFG1/CFG2/SC3 values and its timing is
derived from the same conversion time. Quantities that need slow supervision in software (IO-31
`pwm_period_average`, the safety reference channels) stay on an ADC without a group.

## Limits and known gaps

- A family model for the conversion time exists only for s32k1xx (`MODELS` in `tools/nxconfig/adc.py`); the
  implementation of the converter and the Cortex-M `InterruptContext` live in the s32k1xx port
  (`platforms/s32k1xx/port/bspConfiguration/src/bsp/adc`).
- The converter settings will move to the ADC contract (ARCH-03); until then they are parameters of this module.
- `rtos_mutex` and converter failures have not been run on a board (verification note).

## Tests

Host-Tests, Testnamen und Verifikationsstand: [Modulkatalog](../../../../doc/reference/modules.md#m-bspadcaccess).
