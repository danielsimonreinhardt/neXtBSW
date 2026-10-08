# neXtBSW – granularity of device drivers

Status: 2026-09-16. Decision of the user on the fundamental question "a generic driver that abstracts several
specific parts, or hardware-near drivers?" – **both, but the boundary runs along the protocol, not along the
part**. Binding for all modules in `libs/bsw` and for agent tasks on device drivers.
Basis: target picture [`README.md`](README.md), sections 3 (layers), 4 (configuration levels), 5 (manifests).

## 1. The decision rule

For a new part, the following applies in this order:

1. **Does the part differ from one already supported only in numbers** – thresholds, sense ratio, times,
   resolution – with the same control and the same diagnostics? → No new driver. The part goes into the
   `parts:` list of the existing module, its values into the configuration.
2. **Does the part bring its own protocol** – register model, fault latches with their own semantics, its own start
   or unlock sequence, CRC over register accesses? → **Hardware-near driver**, named after the part family
   (`sbcUja113x`, not `sbc`), with its own register file and its own host tests.
3. **Does the part have the same behaviour but a different access path** – for example multiplexed current sensing
   via DSEL instead of one line per channel, or SPI instead of pins? → The behaviour module stays, with a **thin
   adapter underneath** as the implementation of the access interface (`IHighSideSwitchIo`). No further parameter at
   the top.

What **never** happens: a generic driver whose parameter set is the union of all part capabilities. Then only
subsets of the parameters apply per instance, and `tools/nxconfig` can no longer check which combination makes
sense. That is the MCAL failure.

What also never happens: rewriting part-independent behaviour for each part. Debouncing, turn-on blanking,
overcurrent shutdown, open-load detection and plausibility in the off state are not part knowledge – they belong in
one place with host tests, otherwise they are wrong in a different way for each part.

## 2. Where the abstraction sits

The abstraction **never goes into the hardware-near driver**, but one layer above it:

```
L3 service         IGateDriver, IBatteryMonitor, ...    (only from the second part on, section 3)
L2 device driver   tmc6200        mc33771b              sbcUja113x
                   register model, protocol, fault latches of the part
L1 contract        bspSpi, bspAdcAccess, digital inputs/outputs
```

A hardware-near driver may use the terms of its datasheet – that is its purpose and makes it reviewable against the
datasheet. A generic behaviour module may not: its terms are those of the function (`overcurrent`, `open_load`),
and its access interface names signals, not registers.

## 3. Rule of two: interfaces only from the second instance on

An abstraction over parts of the same kind (gate driver, battery monitor, SBC) is created **only when a second part
of that kind is actually needed**. An interface derived from a single instance is always just that instance under a
different name – it creates work without carrying variance, and it breaks exactly when the second part arrives.

Conversely: as soon as the second part is there, the interface is derived from **both** datasheets, not taken over
from the first.

## 4. `parts:` only grows with a datasheet check

The `parts:` list in the manifest is a commitment, not a guess. A part only goes in when its datasheet is at hand
and it has been checked that control and diagnostic behaviour fit the module's state machine (for the high-side
switch: IS behaviour, fault current, DEN). The check note belongs as a comment on the list, and the source as
`source:` in `board.yaml`. `tools/nxconfig` rejects a board part whose `part` matches no pattern – that is intended
and must not be circumvented with a `*` pattern.

This includes the datasheet rule from `CLAUDE.md`: before a work package on a hardware part starts, the user is asked
for available documents. The exception is the Hall sensor (IO-07), which is deliberately developed without known
hardware – there the configuration carries the variance (arrangement, pole pairs, inversion, debouncing).

## 5. Units at the module boundary: physical, not part-related

A behaviour module is only truly generic when its **project parameters survive a change of part**. Therefore: at
the boundary to the project stand physical quantities of the function (load current in mA, speed, temperature), not
measured quantities of the part's signal path (voltage at IS in mV).

The conversion is board and part knowledge (sense ratio k_ILIS, sense resistor R_IS, ADC scaling) and belongs in the
board profile and the adapter. Two reasons:

- **Correctness:** otherwise every project value silently depends on part and shunt. If the project swaps the
  switch, all thresholds are wrong without any generator being able to notice.
- **Reviewability:** "shutdown at 12 A" can be checked against the load, "shutdown at 380 mV" cannot.

Permitted exception: a value that really is defined at the pin because it says nothing about the function (for
example a plausibility limit for the open input). It is justified in the manifest.

## 6. Review questions

The review question for device drivers is question 9 of [`README.md`](README.md) section 11; the reasoning for the
case of section 1 goes into the changelog fragment.

## 7. Current state in the repository

| Module | Kind | Reason |
|---|---|---|
| `libs/bsw/highSideSwitch` | generic behaviour + `IHighSideSwitchIo` | PROFET class: the variance is in numbers; `parts:` today `BTS5030-1EJA` |
| `libs/bsw/sbcUja113x` | hardware-near (family 113x) | own register model, MTPNV, reset counter, watchdog sequence |
| `libs/bsw/canPhyPassive` | generic | no protocol and no part facts, only the role; `parts:` are the transceivers whose mode pin the board may hard-wire |
| `platforms/s32k1xx/bsp/*` | family-near | L1 implementations, not device drivers |
| planned: TMC6200, MC33771B | hardware-near | own registers and protocols; abstraction only from the second instance on |
| planned: Hall (IO-07) | generic, without a part | no protocol, only edges – the extreme case at the other end |
