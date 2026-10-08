# Hardware safety mechanisms

Baseline: v0.2.0

**Standing.** This document describes safety mechanisms that act **without software** — paths inside the MCU (or
between a device and the MCU) that reach a safe condition even when no instruction is executed. It is a catalogue
and an analysis, not a specification of what neXtBSW does today: at the time of writing **none of the mechanisms
below is used**, and the document exists to say what is available, what it would cost and what it would buy.

**Update SYS-86 (2026-09-30).** The comparator path of section 2.2 is now **configurable** (section 2.4): a project
can name a hardware threshold on a board signal, and the family port routes CMP0 to a fault input of the PWM timer.
It is optional; **no checked-in project uses it** (the eval board has no meaningful signal at a comparator input,
section 4), it is verified on the host and in a target build only, and nothing in this document credits it — the
catalogue entry is SM-58 in `concept.md` 6.5, analysed in `fmea.md`, with no coverage claimed. The pin sources of
section 2.1 (gate driver fault, SBC interrupt) are still SYS-85.

**Update SYS-128 (2026-09-30).** The comparator path detects its own loss (section 2.5): a start test of the
comparator output before the route exists, and a check of every register of the path plus the consistency of the
output with the timer's fault latch in the 1 ms poll of the power stage; a finding forces the group inactive. Host
verified only; still nothing credited.

**Update SYS-125 (2026-09-30).** A second hardware mechanism is now in use, and unlike the comparator path it is not optional: the family port s32k1xx switches the MCU's low-voltage **detect** reset on in every start (`PMC_LVDSC1[LVDRE] = 1`, decision of the safety responsible, anomaly AN-066). A VDD below VLVD (2.80 … 3.00 V) resets the MCU in hardware and holds it in reset until the supply is back; no instruction is involved. Catalogue entry SM-59 in `concept.md` 6.4, analysed in `fmea.md`, **not credited**; the supply margin a 3.3 V board has to keep is AoU-39. Register facts and what is not re-checked: `hsi-s32k1xx.md` 4.8 and H9.

**Update SYS-130 (2026-09-30).** The latched fault (`FMS[FAULTF]`) and the findings of section 2.5 reach the safe
supervisor from the safety task through the narrow interface `safeSupervisor/HwShutoff.h`; the supervisor holds S1
until reset (no reset). The reader for the application exists; the RTE signal and the DTC need the generator and the
application (section 2.6).

**Update SYS-129 (2026-10-01, safety review SYS-86 H6).** The path behind the comparator is no longer untested: once
per start, with the timer running, every output masked and no pin muxed, the route is selected without its lock, the
fault is tripped through the comparator forced by its DAC and cleared again (section 2.7). A failure is a finding of
section 2.5 (group forced, reported as in section 2.6). Host verified only; still nothing credited.

Why it is separate from `concept.md` and `fmea.md`: those two argue the **stage 1** safety element, whose
mechanisms all share one property — they detect and react **in software**, from the safety task or a fault handler,
and they therefore all depend on the assumption that code still runs (`SM-01` covers the case where it does not).
The mechanisms here break that dependency. They belong to a different argument and mixing them into the stage-1
tables would blur exactly the line that makes the stage-1 argument honest.

**Board neutrality.** The catalogue is written for the MCU family, not for one board. The eval board appears only
as a worked example, in its own clearly marked section. A mechanism that exists only because of one board's wiring
is a board fact, not an architecture decision.

---

## 1. Why a hardware path is worth having

Every switch-off path in stage 1 today ends in software:

| Path | Needs |
|---|---|
| safe outputs table (`SM-23`) | the safety task or a caller of `enterSafeState()` runs |
| fault handlers (`SM-17`) | the exception is taken and its handler runs |
| halt deadman (`SM-06`) | the LPIT interrupt is serviced |
| gate driver reaction (`SM-40`, stage 2) | the driver's cycle runs and an SPI transfer completes |

All four fail together in the same class of fault: the core stops executing useful instructions (a lock-up, a
runaway loop with interrupts masked, a clock loss on the core domain, a debugger halt). `SM-01` — the external
watchdog — is the only stage-1 answer, and it answers by **resetting**, which takes a reset time plus a restart
before outputs are defined again.

A hardware path answers in **nanoseconds**, needs no code, and is unaffected by what the software is doing. It does
not replace the software paths (it detects nothing by itself — something must assert it) but it removes the shared
dependency that all of them have.

Two gaps in `concept.md` name this directly: **G1** ("nothing reads back that S1 took effect") and the coverage
note on **ASG-01** ("no base-software mechanism compares what is commanded with what happens").

---

## 2. What the S32K1xx offers

### 2.1 FTM fault inputs — PWM channels forced to a safe level in hardware

**What it does.** Each FlexTimer has fault inputs. An asserted fault input puts the PWM channels of that FTM into
the state defined by `FTM_POL` and `FTM_OUTINIT`, in hardware, within the timer's clock domain. No software runs.
This is the mechanism `AN5303` §4.2 describes.

**What it costs.** One pin per fault source, and the pin loses whatever other function it had.

**Configuration, from [RM13]:**

| Item | Register | Note |
|---|---|---|
| Which channels react | `FTM_FLTCTRL[FAULTM]` | all channels, or only the even ones |
| Enable per channel pair | `FTM_FLTCTRL[FAULTENx]` | per pair, not per channel |
| Polarity of each input | `FTM_FLTPOL` | which level means "fault" |
| Recovery | `FTM_MODE[FAULTM]` | automatic (fault gone → PWM returns) or manual (software must clear) |
| Filter | `FTM_FLTCTRL[FFVAL]`, `FFLTRxEN` | a glitch filter per input |

**Source of each fault input** — this is the part that is easy to get wrong. `SIM_FTMOPT0.FTMxFLTxSEL` selects it,
and the reference manual is explicit that the choice is **per input**:

> "Selects the source of the FTM0 fault. **Every bit means one fault input, respectively.**"
> `000b` – `FTMx_FLTx` pin  ·  `001b` – `TRGMUX_FTMx` out

So for fault inputs **0, 1 and 2** there are two possible sources, chosen independently per input:

- the dedicated pin (`FTMx_FLT0/1/2`), or
- the **trigger multiplexer** output (`TRGMUX_FTMx SEL1/SEL2/SEL3` → fault input 0/1/2).

Fault input **3** has no bit in `FTMxFLTxSEL` and therefore **only** the pin route (`FTMx_FLT3`).

**Why the TRGMUX route matters.** It widens the set of usable pins enormously. A pin that is not a `FTMx_FLTx` pin
but *is* a `TRGMUX_INn` pin can still reach a fault input. It also allows non-pin sources — a comparator output
(`CMP0_COUT`), an LPIT channel, an ADC conversion-complete — to force the bridge off in hardware. The TRGMUX input
list is in [RM13] ch. 19; every FTM (0–3) has three TRGMUX-driven fault inputs.

**Locking.** `TRGMUX_FTMx[LK]` locks the register until the next reset. A safety-relevant routing should set it,
and the register-check mechanism (`SM-21`/`SM-22`) should cover the registers involved.

### 2.2 Comparator (CMP0) — a threshold without the core

`CMP0` compares an input against an internal DAC reference and produces `CMP0_COUT`, which is a TRGMUX input
(`in14`). Combined with §2.1 this gives a complete hardware loop: an over-current or over-voltage crosses a
threshold, and the bridge is off before any interrupt is taken. The comparison itself is analogue and continuous;
nothing polls it.

This is the most interesting unused capability on the part, because it turns a *measurement* into a *reaction*
without the ADC, the RTE or the safety task being involved.

Facts from [RM13] and [DS13], checked for SYS-86: CMP0 has its own 8-bit DAC whose ladder uses either VDDA or the
internal 1 V bandgap (45.1.3; DAC output = V_in / 256 × (VOSEL + 1), 45.8.2), up to eight inputs from pins (which
pins per package: pin multiplexing `CMP0_IN0..7`), and its output is TRGMUX source `0x0E` (Table 19-1). The fault
inputs 0..2 of FTM0..FTM3 are `TRGMUX_FTMx` SEL1..SEL3 (Figure 19-2), each switched from its pin to the TRGMUX
separately (`SIM_FTMOPT0`, 11.3.1.3). Data sheet limits (Table 44, -40 … 125 °C, high-speed mode): input offset at
most 25 mV, DAC non-linearity at most 0.75 LSB, propagation delay at most 500 ns (small overdrive; 300 ns with a
large one), settling of comparator and DAC at most 30 µs; bandgap 0.97 … 1.03 V (Table 8).

### 2.4 The comparator path as configured by SYS-86

**Configuration, one value per level** (checked by `tools/nxconfig/hwthreshold.py`, messages with YAML path):

| Level | Value | Example |
|---|---|---|
| derivative (mcudb) | `comparators[]`: DAC bits, references, the limits above, which timer fault inputs the output reaches | S32K142/144/146/148: CMP0, FTM0..3 inputs 0..2. **Not described for the S32K116**: its FTM0 has one fault input and the manual does not say which, so nothing is claimed |
| board | `comparators.inputs[]`: which pin is which comparator input and **which board ADC entry's circuit** is in front of it (`signal`); `comparators.supply`: the voltage at VDDA | a shunt amplifier also wired to CMP0_IN3 |
| project | `hw_thresholds.<name>`: threshold **in the unit of the signal** (mA, mV), `above`/`below`, reference `supply`/`bandgap`, the PWM group, an optional glitch filter in ns | DC link current above 40 A switches `BRIDGE` off |

**Derived, not configured.** The pin voltage of the threshold comes from the inverse of the signal's characteristic
(linear characteristics only). The DAC level is rounded **towards the safe side**: an `above` threshold trips at or
below the configured value, a `below` threshold at or above it — never later. A falling characteristic swaps the
direction at the pin, and the comparator output is inverted where the pin trips below the DAC. The first free fault
input of the group's timer is used. `nxconfig check` prints per threshold the DAC level, the realised threshold, its
band (offset + non-linearity, plus the bandgap tolerance; with the supply reference the supply's own tolerance is
**not** included and is said so) and the worst-case reaction time.

**Reaction and recovery.** On a fault the FTM forces every channel of the group's pairs to its `POL` value — the
inactive level, freewheel (47.5.18; `FSTATE` = 0, never tri-state). The fault control comes after software output
control, dead-time insertion and output mask in the FTM's feature order (Figure 47-86), so it **overrides `SWOCTRL`**:
a group whose safe state is an active short (SYS-116) ends in freewheel on this fault, as `partner-and-degraded.md`
5.6 and 5.9 require. Recovery is **manual only** (`MODE[FAULTM]` = 10b): the outputs stay forced until a reset,
the latching behaviour of every software path (SM-40). There is no service that clears the fault at run time.

**Time.** Worst case from the crossing at the pin to the forced outputs: comparator propagation (500 ns) plus the
fault path of the FTM — 3 FTM input clocks without filter, 4 + FFVAL with the filter (Table 47-15, `FLTPS` = 0).
At 64 MHz and a 200 ns filter (13 clocks): 766 ns. **Computed, not measured.**

**Start-up order** (family code `platforms/s32k1xx/bsp/bspHwThreshold`, called from the PWM driver's `init()`):
comparator and DAC configured and switched on, the fault input switched to the TRGMUX (which still delivers 0), then
the FTM configuration with fault control, the counter started, **30 µs settling**, then the TRGMUX selection written
and **locked until reset** (`LK`), and only then the pins are switched to the FTM. A threshold that is already
exceeded at that moment reaches the fault input as a rising edge and should latch the fault at once — the manual
describes fault detection as an edge; this is not verified on a target (section 5, point 6).

**Checked at run time.** The registers of the path join the register list of SafeIo (SM-22, one check per safety
cycle): the FTM's `MODE[FAULTM]`, `FLTCTRL`, `FLTPOL` and the `FAULTEN` bits of `COMBINE`, the TRGMUX selection with
its lock, the fault source bit of `SIM_FTMOPT0` and the comparator's `C0`/`C1`/`C2`. This matters because a
comparator switched off reads as its `INVT` bit: for an `above` threshold that is "not exceeded" — a silent loss of
the path that only the register check sees. A comparator that is on but broken (output stuck) is **not** detected by
anything; that is point 3 of section 5.

**What the software does not see.** The fault is latched in the FTM (`FMS[FAULTF]`, readable through
`hwThreshold::faultDetected()`), but no module reports it to the supervisor or the application yet. The software
paths keep commanding duties while the hardware holds the outputs; the drivers do not know why the bridge does not
respond. *SYS-130:* the safety task now reports it to the safe supervisor, which holds S1 until reset; the reader for
the application exists, its RTE signal and DTC are open (section 2.6).

### 2.5 Detection of a lost or stuck comparator path (SYS-128)

Safety review SYS-86, point H5 (user, 2026-09-30): a comparator switched off or stuck is an accepted residual risk of
SYS-86, its detection a follow-up. **Correction:** a comparator switched off does not trip in either direction - in
disabled mode `CMPO`, the signal to the trigger mux, is 0 ([RM13] 45.7.1); only the register bit `C0[COUT]` reads
`INVT`. The loss is silent for `above` and `below` thresholds alike.

Family code `hwThreshold/HwThresholdMonitor.h` (header only), three parts:

| Part | When | What | Finds |
|---|---|---|---|
| start test | once per start, in `ComplementaryPwm::init()` **before** `prepare()`: trigger mux still 0, timer not configured, pins not muxed - nothing reaches the fault input, no pulse on a power output | DAC at VDDA (`VOSEL` 255, `VRSEL` 0); pin (+) against VDDA (-) must read 0, VDDA (+) against the pin (-) must read 1; 30 µs settling each; the comparator is left off for `prepare()` | output stuck at "above" or at "not above" |
| register check | every 1 ms poll of the power stage (`integration/HwThresholdWatch.h`, after the debug halt guard in `DebugHaltGuardSystem`) | `CMP0` `C0` (configuration bits, not the flags), `C1`, `C2` round robin, `TRGMUX_FTMx` selection and `LK`, `SIM_FTMOPT0` bit, `MODE[FAULTM]`, `FLTCTRL`, `FLTPOL`, `FAULTEN` of the pairs | switched off or reconfigured - the same registers as SM-22, ten times as often |
| consistency | the same poll | `C0[COUT]` "above" (comparator on) on two polls in a row while `FMS[FAULTF]` is clear | the route or the fault logic after the comparator is broken |

A finding is latched until reset (one word of ordinary RAM: findings and their complement, written in one store; a
word that fails the check reads as a finding). **Reaction:** the channels of the group are forced inactive through
the software output control - at once in `init()` after a failed start test (before the counter runs and before a
pin is muxed), and in every poll while anything is latched; that is the state the tripped hardware path would leave.
The route is armed anyway, so a comparator stuck at "above" still trips the timer. What the ECU does about it is the
safe supervisor's decision (SYS-130, section 2.6). Only a project with a hardware threshold has any of this code.

**Limits.** A comparator output that sticks at "not above" **between** two starts is not detected at run time:
without a second, independent measurement of the signal a stuck output cannot be told from a quiet signal (that is
plausibility G2, SYS-31). The start test needs the input further below VDDA than the comparator's offset plus
hysteresis (data sheet: offset at most 25 mV); a signal at the rail fails it (fail-safe) - an assumption of use of the
board. It uses the VDDA ladder, not the configured reference: the bandgap reference of a threshold is covered by the
register check (`VRSEL`), not by the forced comparisons.

### 2.6 Report of the latched fault to the supervisor and the application (SYS-130)

Safety review SYS-86, point H7 (user, 2026-09-30): the latched fault (`FMS[FAULTF]`) was reported to nobody; the
report is this follow-up. The supervisor is reached through a **new narrow interface** of its own library,
`safeSupervisor/HwShutoff.h` (family neutral - a "hardware shut-off path of a PWM group", whatever the family builds it
from); no class layout, no event number and no existing reaction of the supervisor changes.

| Step | Where | What |
|---|---|---|
| detection | safety task, every cycle (`SafetyManager::checkSupervisionPeripherals()`, family port, `hwThreshold::shutoffContext`) | `FMS[FAULTF]` of the group's timer, the registers of the path read again (independent of the power stage poll, whose task may have stopped), and the findings latch of §2.5 (start test, consistency) |
| report | `hwShutoff::report(context)`, safety context only | context word: bit 0 = tripped, bits 23..8 = findings of the path, bits 31..24 = timer; only bits not reported before react |
| reaction (decided by the supervisor library) | `HwShutoff.cpp` | log line, **S1 held until reset without a reset**: every safe output off (the gate driver enable with it), the ECU keeps running for the application, the tester and the fault memory. No reset because the hardware path latches until reset (H1: manual recovery only) - a reset would re-arm the bridge by itself. The return path of the register check (`CHECK_REGISTER_ERROR` "ok") does not leave S1 while a report is latched |
| record | protected RAM of the supervisor, two copies | a mismatch reads as "everything reported" (fail-safe) and is written back so at the next report |
| application | `hwShutoff::read(context)`, read only from any task | the bits reported so far and whether the record is plausible - the reader for an RTE signal and a DTC |

**Open for the application (outside SYS-130's paths).** The reader exists; the generated RTE does not call it yet
and no DTC samples it. Needed: an RTE signal `safety.hw_shutoff` (boolean, 1 while anything is reported or the record
is implausible) and `safety.qlf.hw_shutoff` in `tools/rtegen` (like `safety.degraded` of SYS-117); a BSW event
`safety.hw_shutoff` with a fixed number in the safety block of the numbering plan (`tools/nxconfig/bswdtc.py`, e.g.
+0x05FE next to +0x05FF, because the report is no event of `SafeSupervisor::Event`), its entry in
`DEM_BSW_REPORTERS` and a logging reporter next to `safety.device_fault` in `ecu/application`
(`dem/SafetyFaultReport.h`, `RteSystem.cpp`: FAILED while `read()` reports anything or is implausible).

**Choice of the reaction** (for the review): S1 held without reset. The alternatives were the existing interface
`DEVICE_FAULT` (class `OUTPUT`: S1 + S2 - a reset re-arms the latched path, against H1; the degraded reaction SD only
with a mapping of a device instance the hardware path does not have) and `DEVICE_REPORT` (S3 only: the other outputs
and the gate driver enable would stay on). A reaction that degrades only the group (SD) would keep the rest of the ECU
running; it needs the event on the closed list of `Degraded.h` and a mapping in `tools/nxconfig/degraded.py`.

**Update SYS-135 (2026-09-30, safety review SYS-128/SYS-130 point C1).** The safety responsible decided for that
degraded reaction: a project may map the report to SD. `HW_SHUTOFF` stands on a second closed list of the supervisor,
`NX_SAFE_DEGRADABLE_REPORTS` in `safeSupervisor/Degraded.h` (id `0xFE` in the event field of a mapping word, above
every event number), because it is no event of `SafeSupervisor::Event`.

| Step | Where | What |
|---|---|---|
| project | `safety.degraded: [{event: HW_SHUTOFF, groups: [<group>]}]`, checked by `tools/nxconfig/degraded.py` with the YAML path | exactly one group, and it must be the group of a PWM group a hardware threshold switches off; refused as well: thresholds on more than one timer (the report record keeps all paths in one word, so a second path could add no new bit) and a timer without an instance number; derived `0xFE << 24 \| timer << 16 \| 0xFF << 8 \| group` |
| decision | `SafeSupervisor::degradeHwShutoff(context)`, called by `hwShutoff::report()` in the safety context with the interrupt lock | degraded only if the context says "tripped" and nothing else (bit 0, no findings in bits 23..8) and the timer of bits 31..24 is mapped; then the ordinary SD sequence of SYS-117 (outputs of the group, latch, record in no-init RAM with `0xFE` and the context, log), **no reset**, the last group escalates to S1 without a reset |
| fail-safe | `HwShutoff.cpp` | findings of the path, a report without context, an unmapped timer and an implausible record hold S1 as in SYS-130; `latched()` now means "S1 held by a report", so a degraded report does not keep the register check's return path closed - its group is held by the degraded walk of every safety cycle |
| after a reset | `Degraded.h` record | the reset re-arms the path, but the group stays degraded until the power cycle (decision B4 (a) of SYS-117) |

A project without the mapping, and every project without a threshold, is unchanged. The start test of the path needs
the input below VDDA by more than offset plus hysteresis: AoU-37 of the concept (point C3 of the same review).

### 2.7 Start test of the path behind the comparator (SYS-129)

Safety review SYS-86, point H6 (user, 2026-09-30): the path was never exercised - a broken route, a fault input that
does not see the comparator or a fault logic that does not latch stayed latent until a real over-current; the start
test is this follow-up. The start test of §2.5 proves only that the comparator output can move. The open point 3 of
section 5 named the obstacle: asserting the fault input from software costs a pulse on a power output. SYS-129 avoids
it by **when** it runs, not by what it asserts: the fault is real, but nothing behind the timer is connected yet.

Family code `hwThreshold::pathTest()` (`HwThresholdMonitor.h`), called once by `ComplementaryPwm::init()` after
`Ftm::start()` and **before** the settling wait, `route()` (selection and `LK`) and the pin multiplexing:

| Step | State | What | Finding |
|---|---|---|---|
| precondition | timer configured with its fault control (`MODE[FAULTM]` 10b, `FLTCTRL`, `FLTPOL` 0, `FAULTEN`), counter clocked, `OUTMASK` = every channel, **no pin of the group muxed to the timer**, `TRGMUX_FTMx` not locked, fault source select on the trigger mux (`prepare()`); start test of §2.5 passed (otherwise skipped - the group is forced and reported already) | - | - |
| A | comparator forced "not above" (pin (+) against VDDA (-)), then `TRGMUX_FTMx` `SEL(n+1)` = `CMP0_OUT` **without** `LK`; two waits | `FMS[FAULTF]`, `FAULTFn` of the configured input and `FAULTIN` must be 0 | `PATH_FAULT_UNFORCED` (0x0800): fault input active without the comparator - stuck, or another enabled input already latched |
| B | comparator forced "above" (VDDA (+) against the pin (-)); two waits | `FAULTF`, `FAULTFn` and `FAULTIN` must be 1 | `PATH_NO_TRIP` (0x1000): route, source select, fault input, its enable, the timer clock or the fault logic broken |
| C | comparator forced "not above" again; two waits; `FMS` read while set, then 0 written to `FAULTFn` of the configured input only (1 to `FAULTF` and the other flags - no effect, [RM13] 47.4.3.17); one wait | `FAULTF`, `FAULTFn` and `FAULTIN` must be 0 | `PATH_NO_CLEAR` (0x2000): the input stays asserted, or another input holds `FAULTF` - its flag is not cleared by the test |
| D | `SEL(n+1)` back to 0 (still unlocked) **before** the comparator changes; comparator configured as `prepare()` left it | - | - |

Every wait is `settleUs` (30 µs): one for the comparator and its DAC ([DS13] Table 44), one for the synchronizer and
the filter of the fault input - `(4 + FFVAL)` FTM input clocks ([RM13] Table 47-15); the driver checks by
`static_assert` that this is shorter than one wait at the generated FTM clock, and that the fault input is active
high (`FLTPOL` 0, as generated). The test costs seven waits, about 0.21 ms per start. After step D the driver goes on
exactly as before: settling wait, `route()` with `LK`, pins.

**Why there is no pulse on a power output.** The forced fault exists for about 90 µs between steps B and C. During
that time every channel is masked (`OUTMASK`, inactive level), the fault control forces the same inactive level, and
the pins of the group are still in their reset state (not muxed to the timer) - the timer's outputs reach no pad. The
argument does not rely on the gate driver enable (it rests on the pin multiplexing alone, which `ComplementaryPwm`
already relies on for its safe start). After the clearing the outputs
leave the fault control only at the start of the next PWM cycle ([RM13] 47.5.18.2) and are still masked.

**Findings and reaction.** A finding goes into the findings latch of §2.5 (bits 0x0800/0x1000/0x2000, in the report
context bits 23..8) and the driver forces the group inactive through the software output control before any pin is
muxed (freewheel, review SYS-128 C2); the route is still armed and locked. The first 1 ms poll of the power stage logs
the finding and keeps the group forced, the safety cycle reports it (`HW_SHUTOFF` with findings: **S1 held**, never
the degraded reaction of SYS-135, which degrades only "tripped and nothing else"). If the test's fault cannot be
cleared, `FMS[FAULTF]` stays set and the hardware keeps the group forced as well (fail-safe).

**Not covered.** The forcing of the channels by the fault control (`FAULTEN` → `POL`) is not observed: with every
channel masked the outputs read inactive with and without the fault (`CnSC[CHOV]` would show the final value only
with the mask open - a review point). `FAULTEN`, `FAULTM` and the polarity are covered by the register check of §2.5.
Faults that appear between two starts stay with the register check and the consistency check of §2.5 (latent-fault
interval = drive cycle). Same assumption of use as the start test (AoU-37): the input lies below VDDA by more than
offset plus hysteresis.

### 2.3 What is **not** a hardware safety mechanism

Worth stating, because all three are easy to mistake for one:

- **The watchdog (`WDOG`)** reacts by resetting. It is a hardware supervisor of software, not a hardware path to a
  safe output state, and it is already `SM-01`/`SM-02`.
- **The MPU** prevents a write; it does not put an output anywhere.
- **A device's own protection** (the TMC6200 switching its bridge off in < 2.2 µs) is a *device* mechanism, stage 2,
  `SM-40` — outside the MCU and outside this document's scope except as a **source** for §2.1.

---

## 3. Where such a mechanism belongs in the safety argument

An honest placement matters, and it is not obvious, so it is written out here rather than assumed.

A hardware path has two halves, and they can belong to different stages:

| Half | Example | Belongs to |
|---|---|---|
| **the reaction** — PWM channels forced to a safe level by the FTM | `FTM_FLTCTRL`, `FTM_POL` | the **MCU and its configuration** → a stage-1 candidate, same class as `SM-23` |
| **the detection** — whatever asserts the fault input | a gate driver's `FAULT` output | depends: a comparator on the MCU is stage 1; a device reporting over its own pin is **stage 2** (`SM-40` …) |

So a mechanism of this kind may be credited for its *reaction* while its *detection* is not credited — the same
split that `concept.md` §2.2 already applies. Whether it is credited at all is a decision for **SYS-20** (stage-1
analysis) and **SYS-28** (device packages), not for this document. What this document may state is what the
hardware does.

**Diagnostic coverage of the mechanism itself is not claimed anywhere here.** A fault path that has never been
asserted is a latent fault: nothing proves it still works. Any use of §2.1 needs a statement of how the path is
tested — at start-up, at each power cycle, or never — and that statement is part of the task that introduces it,
not of this catalogue.

---

## 4. Worked example: the eval board (`boards/evalboard_s32k144`)

**This section is an example, not a requirement.** The facts below were read from `board.yaml` and the pin
multiplexing tables of the derivative; the routing has **not** been measured on hardware.

The motor bridge runs on **FTM0** (`pwm[].instance: FTM0`, group `MOT1`, complementary mode). A hardware fault path
must therefore reach a fault input of **FTM0**.

Two candidate sources exist on this board, and — because the source is selectable per fault input — **both can be
used at the same time**:

| Source | Pin | Route to FTM0 | What it costs |
|---|---|---|---|
| **Gate driver `FAULT`** (TMC6200) | `PTC10` | **not** an `FTM0_FLTx` pin. But `PTC10` alt 6 is `TRGMUX_IN11` (`in13`) → `TRGMUX_FTM0[SEL1]` → **fault input 0**, with `FTM0FLTxSEL` bit 0 = 1 | the pin stops being a GPIO input — see below |
| **SBC interrupt 1** (UJA1131) | `PTE12` | `PTE12` alt 2 **is** `FTM0_FLT3` → **fault input 3** directly, pin-only route, no `SIM` bit involved | the pin stops being a GPIO input |

**What the gate driver path costs is close to nothing, and that is a real argument.** `IO-44` established that the
TMC6200 `FAULT` pin is the OR of the `GSTAT` bits and **carries no information the SPI read does not already
give** — the driver classifies faults from `GSTAT`, not from the pin. Giving the pin to the TRGMUX therefore loses
no diagnostic content while gaining a path that works when software does not. (One caveat from the same task: with
`faultdirect` = 0 the pin does **not** follow `shortdet`, so the configuration of the device decides what the
hardware path actually sees. That is a device parameter, and it would become safety-relevant the moment this path
is used.)

**What the SBC path costs** is one console line: `SBC_INTERRUPT1` is read only for the `sbc` status display
(`SbcSystem.cpp`, `SbcConsole.h`). No safety function reads it today.

**Not verified, and needed before any of this is designed in:**

- that `PTC10` and `PTE12` really carry these signals on the built board, and with which polarity and idle level;
- whether the TMC6200 `FAULT` output is push-pull or open-drain on this board, and whether a pull is fitted —
  an open-drain output with no pull would make the fault input float;
- what either pin does during reset and before the port is configured, since an asserted fault input at start-up
  and a floating one are very different failures.

**The comparator path (SYS-86) has no signal on this board.** From `board.yaml` and the pin multiplexing of the
S32K144 LQFP100, the eight CMP0 inputs carry: IN0 `PTA0` = `MOTOR_NTC` (pull-up R148 not fitted, the pin floats),
IN1 `PTA1` = Hall signal `HALL_UX`, IN2 `PTC4` = debug clock (JTAG_TCLK/SWD_CLK), IN3 `PTE8` = `PILOT_DUTY` (input
capture), IN4/IN5 `PTC3`/`PTC2` = CAN0, IN6/IN7 `PTD7`/`PTD6` = UART. None of the bridge's current or voltage
signals (`SENSE_TSCURRENT` `PTC15`, `ADC_CURRENT_U/V/W`, `BEMF_*`, `SENSE_TSVOLTAGE`) reaches a comparator input. So
the eval board gets no board fact and the demo no threshold. A board proof needs a signal wired to a free comparator
input — the least intrusive candidate is `PTA0` through J11 pin 5 (unused while R148 is not fitted), fed from a lab
source; that is a change to the board setup the user has to decide and make.

---

## 5. Open points

| # | Point |
|---|---|
| 1 | **The pin sources are not configurable.** Since SYS-86 the FTM fault control of a PWM group is generated when a project has a hardware threshold (comparator source, section 2.4); for everything else `tools/bswgen` still emits `FLTCTRL` and `FLTPOL` as a hard `0`, and there is no board fact for a fault **pin**, no project value, no check. Task **SYS-85** |
| 2 | **Automatic or manual recovery** is a safety decision, not a default. Automatic recovery means the bridge restarts by itself when the fault clears — which is the opposite of the latching behaviour every software path in neXtBSW has (`SM-40`: a shutdown fault latches, `enable()` is refused until cleared). Mixing the two silently would be a bad surprise. SYS-86 offers **manual recovery only** for the comparator path (latched until reset) and proposes the same for SYS-85 |
| 3 | **Testing the path.** See §3: an untested fault path is a latent fault. A start-up self-test is possible in principle (assert the input from software, observe the channels) but costs a pulse on a power output, which is exactly what must not happen at start-up. *SYS-128:* the comparator output alone is tested at every start before the route exists (no pulse, §2.5); a comparator output that sticks at "not above" between two starts stays undetected until an independent measurement of the signal exists (plausibility G2, SYS-31). The path behind the comparator (trigger mux, fault latch of the timer) stays untested: SYS-129. *SYS-129:* tested once per start after the timer runs and before any pin is muxed (§2.7) - a real fault, but no pad connected, so no pulse; the forcing of the channels themselves is not observed (masked), the board proof is open |
| 4 | **Interaction with the safe outputs table.** If hardware forces the channels off and `SM-23` also walks them, two mechanisms write the same registers. Which one wins, and what the register check (`SM-21`/`SM-22`) expects to read, has to be decided rather than discovered. For the comparator path SYS-86 states it (§2.4): the fault control comes after `SWOCTRL` in the FTM ([RM13] Figure 47-86), so the fault wins; SM-22 expects the configured fault registers, which a fault does not change (`FMS` is not in the list). For the pin sources it stays with SYS-85 |
| 5 | **CMP0 was completely unused** (§2.2). SYS-86 makes it configurable (§2.4), verified on the host (register model, fault path model, mutants) and in a target build; the board proof - approaching the threshold at a board with a signal at a comparator input - is open, and the eval board has no such signal (§4). *SYS-128:* the detection of §2.5 is host verified as well; its board proof is part of the same run - the two forced comparisons of the start test read back, and a comparator switched off while running (J-Link write `CMP0_C0[EN]` = 0) forcing the group within one poll, time from the write to the forced pins measured |
| 6 | **Start with the threshold already exceeded.** The route is connected after the FTM runs, so an exceeded threshold reaches the fault input as a rising edge; the manual describes fault detection as an edge ([RM13] 47.5.18) and does not say what an input that is already asserted when fault control is enabled does. Not verified on a target; until then a start into an exceeded threshold is not claimed to be caught (board test with SYS-86) |
| 7 | **Output buffers under fault control.** `SIM_MISCTRL0.FTMx_OBE_CTRL` (reset 0, [RM13] 11.1.1.2) describes the output buffer of an FTM channel in terms of fault control (`FAULTM` ≠ 0, `FSTATE` = 0) and PWM enable; with fault control enabled for the first time, that the channels drive the `POL` values (and are not tri-stated) has to be confirmed at the pins before the path is relied on (board test with SYS-86) |
| 8 | **SBC watchdog without software development mode (UJA113x SDMS 0).** According to the public UJA113x data sheet (Rev. 2, Tables 7 and 8, 7.2.1) the SBC watchdog of a device without software development mode starts after an SBC reset already in Standby (time-out 128 ms). With `bootloader.sbc_watchdog: leave` (the default) the bootloader of v1.0.0-rc1 does not touch it: without a valid application the SBC resets the MCU in a loop (outputs de-energised, safe state, but the device cannot be updated), with an application it must trigger within about 256 ms after the SBC start. The eval board (SDMS 1) is not affected. Found by the safety review SBC watchdog of 2026-10-06 (PLAT-117, decision SDMS0-2 a); correction with **BL-41**; until then use `bootloader.sbc_watchdog: serve`; board proof on the Longboard control unit after its return (SDMS0-3 a) |
---

## 6. Sources

- **[RM13]** S32K1xx Series Reference Manual, Rev. 13, 04/2020 — ch. 11 (`SIM_FTMOPT0`), ch. 19 (TRGMUX), the
  FlexTimer chapter (fault inputs).
- **[DS13]** S32K1xx Data Sheet, Rev. 13, 04/2020 — Table 44 (comparator with 8-bit DAC), Table 8 (bandgap); the
  values used by SYS-86 are in `mcudb/s32k1xx/S32K14x.yaml` `comparators` with page references.
- **[AN5303]** Features and Operation Modes of FlexTimer — §4.2, fault control.
- **[AN5426]** Hardware Design Guidelines for S32K1xx Microcontrollers — pin and board rules (task **PLAT-101**).
- `boards/evalboard_s32k144/board.yaml`, `mcudb/s32k1xx/S32K144_LQFP100.pinmux.yaml` — the board and pin facts of
  §4.
- `doc/safety/concept.md` §2.2 (the stage-1 / stage-2 separation), §9.5 (gaps G1 and the ASG-01 note),
  §13.3 (`SM-40`, the gate driver).
- SYS-86: `tools/nxconfig/hwthreshold.py` (checks and derivation), `tools/bswgen` (`collect_hw_threshold`,
  `templates/hwThresholdConfiguration.h.j2`), `platforms/s32k1xx/bsp/bspHwThreshold`; tests `nxconfig-hwthreshold`,
  `bswgen-hwthreshold`, `target-hwthreshold`.
- SYS-128: `platforms/s32k1xx/bsp/bspHwThreshold/include/hwThreshold/HwThresholdMonitor.h` (start test, register
  check, consistency, findings latch), `ComplementaryPwm::init()` (start test, forcing),
  `platforms/s32k1xx/port/main/src/integration/HwThresholdWatch.h` (1 ms poll); tests `hw-threshold-monitor`,
  `hw-threshold-monitor-mutations`. [RM13] 45.7.1 (disabled mode: `CMPO` 0), 45.8.1-45.8.3.
- SYS-130: `ecu/safety/safeSupervisor/include/safeSupervisor/HwShutoff.h`, `src/safeSupervisor/HwShutoff.cpp`
- SYS-135: `NX_SAFE_DEGRADABLE_REPORTS` in `safeSupervisor/Degraded.h`, `SafeSupervisor::degradeHwShutoff`,
  `tools/nxconfig/degraded.py` (`HW_SHUTOFF` mapping)
  (report, reaction, reader), `SafeSupervisor.cpp` (S1 not left through the register check while reported),
  `platforms/s32k1xx/port/safety/safeLifecycle/src/safeLifecycle/SafetyManager.cpp` (report in the safety cycle);
  tests `safe-supervisor-hw-shutoff`, `safe-supervisor-hw-shutoff-mutations`, `hw-threshold-monitor`.
- SYS-129: `hwThreshold::pathTest()` in `HwThresholdMonitor.h`, its call in `ComplementaryPwm::init()`; register
  facts [RM13] 47.4.3.17 (`FMS`: clearing of `FAULTF`/`FAULTFn`, `FAULTIN`), 47.5.18 and Table 47-15 (fault input
  filter delay), 47.5.18.2 (manual fault clearing), Figure 19-2 (`TRGMUX_FTMx` `SEL1..3`); tests
  `hw-threshold-monitor` (step model of the fault logic `test/host/FtmFaultModel.h`), `hw-threshold-monitor-mutations`,
  `bswgen-hwthreshold`.
- `doc/todo.yaml`: **SYS-85** (make §2.1 configurable), **IO-45** (gate driver ↔ PWM group in the board schema),
  **PLAT-101** (board rules from AN5426).
