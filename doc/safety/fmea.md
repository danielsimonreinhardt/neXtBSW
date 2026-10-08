# neXtBSW failure analysis of the base-software safety mechanisms (stage 1)

| | |
|---|---|
| Title | Failure analysis of the stage-1 mechanisms |
| Issue | **2**, 2026-10-04 (issue 1: 2026-09-21, SYS-20, written against `dev` at `63ea900`, with its update blocks up to 2026-10-03, DOC-45) |
| Author | SYS-20 (issue 1), SYS-49 and the tasks of the update blocks (section 12.1), DOC-55 (issue 2) — author side |
| Approver | the safety manager (the user): issue 1 accepted in `abn-sys20`, released with its updates up to DOC-38 on 2026-09-30; the state at `dev` aaa22c86 reviewed in the confirmation review CM-02 (record `reviews/records/CR-02-1.md` (not published, available on request), the user under D-01, 2026-10-04, `accepted with conditions`); issue 2 **released** on 2026-10-04 in the re-check of the findings F-01 … F-11 in CR-02-1 (steps 6 and 7 at `dev` efc474ea: all closed, verdict `accepted`, CM-02 closed; D-01, without independence) |
| Status | issue 2 **accepted** (released 2026-10-04 in the re-check of CM-02, record CR-02-1); update DOC-59 after it, as the reviewer decided there (BRD a): captures of B2 and B15 in the repository, B16 (a), (c), (d) and the LVD reset with SYS-198, S171-3 at SYS-171, the caveat of B17 in SM-10, SM-13 and N3; update DOC-60: upkeep after the re-check (finding RN, open point N7, in review); update SYS-209 (section 1.3: a clause reference instead of the usual figures for the classes) accepted by the user in the re-check of 2026-10-08 (C8, record CR-02-1; D-01, without independence) |
| Change history | section 12 (the update blocks of issue 1 unchanged in 12.1, the issues in 12.2) |

Working state: dev at c0928380 (after v1.0.0-rc1)
Readers: the user as architect, the integrator, and the agents of SYS-21, SYS-22, SYS-24, SYS-28 and DOC-19.
Language: English, like the rest of `doc/` (CLAUDE.md, user 2026-09-17).
Licence rule: the standard is licensed to the user since 2026-09-22 and compared in `iso26262-gap.md` (not published, available on request);
this document cites it by part and clause only and restates nothing of its text (issue 1 said it was not available).

This document is the failure analysis that the safety concept [`concept.md`](concept.md) hands to SYS-20 (concept
section 10.1). It uses the concept's identifiers unchanged: `ASG-*` (assumed safety goals), `AoU-*` (assumptions of
use), `SM-*` (mechanisms), `F1`–`F8` (findings) and `G1`–`G6` (coverage gaps). New findings of this analysis
continue the concept's numbering as **F9–F16**; they are listed in section 6 and referenced from concept section 9.1.

**Scope: stage 1 only** (user, `abn-sys19`, 2026-09-21: strict separation). Analysed are the mechanisms of the base
software whose detection runs on the MCU and uses only the MCU, its peripherals and the configuration (concept
section 1.4). Not analysed, and not credited: the device-dependent mechanisms SM-33 (transceiver status through the
SBC) and SM-52 (SBC reset counter), and the device packages SM-40 … SM-43 — they are **SYS-28** (concept section 13).
Where a stage-1 mechanism would be weakened by the absence of a stage-2 mechanism, this document says so instead of
counting the stage-2 one.

---

## 0. Result in one page

| Question of the task | Answer |
|---|---|
| Mechanisms analysed | **36** blocks in section 3: the **31 credited** stage-1 mechanisms - the 29 of issue 1 (SM-01 … SM-07, SM-10 … SM-17, SM-18 … SM-22, SM-23 … SM-25, SM-30 … SM-32, SM-50, SM-51, SM-53) and since then SM-34 and SM-55 - and **5 not credited**: SM-26, SM-27 (active short, SYS-116), SM-58 (hardware threshold, SYS-86 with SYS-128, SYS-129, SYS-130, SYS-135), SM-59 (low-voltage reset, SYS-125), SM-60 (FlexNVM lock, NVM-25) |
| Scope for Release R1 (`v1.0.0-rc1`) | **credited** for Release R1 are the 31 mechanisms above, with the classes of section 3; SM-26, SM-27, SM-58, SM-59 and SM-60 are analysed but **not claimed** (most of them host verified only, as their blocks say) and carry no class. The confirmation review CM-02 checked only their marking and references; their content is reviewed when they are credited (CM-02 decision Z4) |
| Diagnostic coverage | stated per mechanism as an **ordinal class** (high / medium / low, or "reaction" for mechanisms that do not detect) with the reasoning written out; **no percentages** (section 1.3). The classes stay for Release R1; open point N1 is closed with the answer of the gap analysis section 3 (no direct mapping), and the re-basing of every mechanism on element, technique and test interval (M2) is DOC-56, after Release R1 and before the first ASIL B release (CM-02 decision Z2) |
| Times against the fault tolerance time | per mechanism: detection time, reaction time, and the resulting **shortest fault tolerance time the mechanism can serve**. The concept's "10 ms" for the safety-task checks is the **nominal** value; the **guaranteed** bound was **31.25 ms** in issue 1 (finding **F9**) and is **17 ms** since SYS-40 while the task runs (section 2). *Issue 2:* SM-01 fires within up to about **283 ms** over the LPO range of the derivative (derived); the faults of the time bases of the supervision (stopped system time, gated or slow LPIT clock, lost SIRC with the residual risk DF-05) are section 2.1, as in concept 7.2 issue 3 |
| Verdicts on the assumed goals | concept section 2.2 **confirmed**: ASG-01 partial, ASG-02 covered with limits, ASG-03 and ASG-04 not covered, ASG-05 covered for CAN data, ASG-06 covered with limits, ASG-07 partial. ASG-02 and ASG-06 gain further limits from F9, F11, F13 (section 4) |
| F3 (ROM check in one go) | **decided:** no slicing needed while the check runs at idle priority without a lock; duration is computed at **about 3 … 6 ms** (not measured, board checklist); a **re-check period** is needed for ECUs that are rarely restarted → proposed task (section 5.1). **Since SYS-44** the re-check exists (T5 done, host verified); on the board one pass took at most 5 574 µs (B1) and the re-check ran every period (B9); the suspension during a program-flash write is not shown on the board yet (needs a real flash write, after PGMPART) |
| F4 (register lock/check) | **scoped for SYS-24** (section 5.2); since SYS-24 and SYS-153 the lists are generated from the configuration (classes `PIN_FUNCTION`, `GPIO_DIRECTION`, `CLOCK_GATE`, `PWM_TIMER`, `SUPERVISION`; 27 entries on the demo). F4 stays open until the class `SYSTEM_CLOCK` exists (**SYS-55**, AN-004) |
| F5 (SPI as common cause) | **decided:** not a stage-1 problem — no credited mechanism uses SPI. Assigned to G5 (bus instance in the device context word, correlation in `handle()`), SYS-28 (dependent-failure analysis) and SYS-21 (shared resource); no separate SPI supervision in stage 1 (section 5.3) |
| New findings | F9 safety-task period unsupervised · F10 RTE task activation unsupervised · F11 ECC poll incomplete · F12 watchdog configuration check incomplete · F13 no reset-loop limit in stage 1 · F14 internal-supply read failures unlimited · F15 safety modules without host test · F16 ADC reference tolerance is a silent gain error (section 6) |
| Proposed tasks | six (section 7), plus additions to SYS-21, SYS-24, SYS-28 and G5 |
| Status after SYS-40 | **F9 addressed** by the period part of SM-04 (section 3.1): every safety cycle compares its distance to the previous one with the LPTMR, a free-running counter on the 1 kHz LPO clock that the scheduler does not use. The guaranteed detection time of a check in the safety task is **17 ms** (nominal 10 ms) as long as the task runs; a cycle that comes later reports itself before its checks, a task that stops stays SM-01's (section 2). Host verified with mutants; board checks B4 and B7 passed 2026-09-22 (section 8.1) |
| Status after SYS-41 (SYS-49 update) | **T2 done** (SYS-41): F11 closed (ECC flags cleared, source in the event context), F12 closed for the watchdog, F14 closed (read-failure limit of SM-20), SM-04 with six checkpoints; F12 for the deadman: host verified, target binding pending (**SYS-48**) - *issue 2:* bound by SYS-48 and proven on the board by the fault injection of SYS-51 (F12 closed, AN-012). **T3 mostly done**: host tests for `safeWatchdog`, `SafetyManager::cyclic()` and the ECC part of `safeMemory`; `SafeIo` (SM-21/SM-22 detection) remains (F15 rest). Board check B6 passed 2026-09-22; B5 run 2026-09-24 (shows only that no flag survives a reset, 8.1) |
| Status after SYS-42 | **T3 done, F15 closed**: `safety-gate` (SM-11: real `SafetyManager::cyclic`, gate and supervisor on an MPU register model) and `safe-io` (SM-21/SM-22: real `SafeIo`, bspIo driver and supervisor on a PORT/GPIO register model with the PCR lock), each with 9 built-in faults that are all detected. **SM-21 corrected:** the read-back after the lock compared the MUX bits only, so a lock that did not take (write lost, `PCR[LK]` stuck at 0) passed; it now includes `LK`. Board check B8 passed 2026-09-22 (`LK` = 1, `MUX` = 001, no `LOCK_REGISTER_ERROR`; 8.1) |
| Verification | **mock/analysis only**: issue 1 by code review against `63ea900`, the host tests named as evidence, and the board records in `CHANGELOG.md`. Issue 2 (DOC-55) brings the blocks to the working state from the code, the anomaly register, [`dfa.md`](dfa.md), the safety manual and the board records of other tasks up to 2026-10-04 (sections 8.1 and 8.2). No board run was made for this document; the board checklist is section 8 |

---

## 1. Method

### 1.1 What is analysed per mechanism

For each mechanism, one table with the fields the task asks for:

| Field | Content |
|---|---|
| **Faults considered** | the failures of the element the mechanism is meant to catch (fault model of the concept, made concrete), and the failures it explicitly does **not** catch |
| **Detection** | what the code reads or compares, where, and how often |
| **Reaction** | safe state per concept section 3: S1, S1 held, S1 + S2, S3; who decides (always `SafeSupervisor::handle()` unless stated) |
| **Times** | detection time (fault present → event raised), reaction time (event → reaction in effect), and the **minimum FTTI served**: the shortest fault tolerance time for which the mechanism can be relied on today (section 1.2) |
| **Diagnostic coverage** | class and justification (section 1.3) |
| **Latent faults of the mechanism** | how a failure of the mechanism itself is noticed, and after how long |
| **Evidence** | test IDs from `tools/test/tests.yaml` (run with `.venv/Scripts/python tools/test -k <id>`), target-artefact tests, and board runs recorded in `CHANGELOG.md` with their date |
| **Residual / action** | what remains, with the finding or task that carries it |

A mechanism is a *software* safety mechanism: the analysis asks what the software detects and how fast it reacts,
not how often the hardware fails. Failure rates, the single-point and latent-fault metrics, and the manufacturer's
coverage claims for the MCU's own hardware features (ECC, watchdog, MPU, CRC unit) are the integrator's (AoU-09);
the MCU manufacturer's safety manual for the S32K1 family is **not available** to this project, so every hardware
coverage claim below is a claim about **how neXtBSW uses** the feature, not about the feature itself.

### 1.2 Time terms

These are the usual terms of the field, explained in our own words (issue 1 said the standard was not consulted;
issue 2: the terms agree with the fault tolerant time interval and the fault handling time interval that concept 7.2
issue 3 uses, proposal C7 of the gap analysis; the standard is cited by part and clause only):

- **Detection time** — from the moment the fault is present to the moment a mechanism raises its event.
- **Reaction time** — from the event to the moment the reaction has taken effect (outputs de-energised for S1; the
  MCU in reset for S2).
- **Fault tolerance time (FTT)** — the time the item tolerates the fault before a hazard can occur. It belongs to
  the integrator's item (AoU-01), not to neXtBSW.
- **Minimum FTTI served** — detection time + reaction time, worst case, as this analysis can bound it today. An
  integrator may rely on the mechanism for a goal only if his FTT is **longer** (AoU-05).

Times are given in two forms where they differ:

- **nominal** — with the demo's constants and the safety task running at its period (the concept's section 7.2
  values);
- **guaranteed** — what the element itself detects if the timing assumption breaks. Today these differ for every
  check in the safety task (F9, section 2).

Reaction times: **S1** (safe outputs walk) is computed at well under 1 µs at 80 MHz; on the board the walk from
the timer to the gate-driver enable is bounded at **≤ 21 µs** (B3, oscilloscope 2026-09-22; a tighter bound of ≤ 2 µs
of 2026-09-28 has its captures outside the repository, AN-008), so the computed value stays computed (F8). **S2**
brings the MCU into reset within the reset sequence of the core; the **restart time** afterwards is measured (B2,
section 8.1: up to 1 077 ms through the watchdog path, 858 ms through a fault handler, the stated values) and does not
add to the time to de-energise, but it is the time the ECU is out of service.

### 1.3 Diagnostic coverage: classes, not percentages

The concept forbids invented numbers (concept section 0), and the manufacturer data that a quantitative figure
would rest on is not available. The coverage of each mechanism is therefore given as an **ordinal class** with the
justification written out, so that a reader can disagree with the reasoning rather than with a number:

| Class | Meaning in this document |
|---|---|
| **high** | the mechanism checks the **whole** property it guards, by comparison with a reference that is independent of the fault, at a fixed interval (or on every access), and a failure of the mechanism itself is either detected or ends in a safe reaction |
| **medium** | the whole property is checked, but with a **known blind spot** that a realistic fault can fall into: sampling instead of continuous checking, a tolerance band, a reference that shares a cause with the checked value, or a part of the fault modes excluded |
| **low** | only a **small part** of the elements or fault modes is checked (demonstration scope, one pin, one path) |
| **reaction** | the mechanism does not detect anything; it is judged by whether it takes effect when called (SM-23 … SM-25, SM-53) |
| **none** | the fault is not covered by any stage-1 mechanism |

The standard rates the diagnostic coverage of safety mechanisms per hardware element and technique in its
informative annex 5-Annex D (with 11-5.1.12 for memories and 11-5.2.4 for analogue parts); the reader finds those
ratings in his own copy of the standard. **This document does not map its classes to them**: the class is an
engineering judgement about neXtBSW's use of a feature. *Issue 2 (N1 closed,
CM-02 decision Z2, measure M3):* the gap analysis `iso26262-gap.md` (not published, available on request) section 3 answers the
question - there is no direct mapping of these classes to the figures expected for the hardware metrics; a figure
needs each mechanism re-based on the hardware element it diagnoses, the technique and its test interval (measure M2).
The classes therefore stay as they are for Release R1, which makes no safety claim, and the hardware metrics stay with
the integrator (AoU-09, N2). M2 is task **DOC-56**, after Release R1 and before the first ASIL B release.

### 1.4 Evidence notation

- `test-id` — a test entry in `tools/test/tests.yaml`. `host` tests run the real production file against a model or
  stubs; `target` tests inspect the built ELF.
- **board YYYY-MM-DD** — a board run recorded in `CHANGELOG.md` under that date.
- **review** — the claim rests on reading the code at `63ea900` only; there is no test that would fail if the code
  broke. These are the weak spots of the evidence and are collected in F15.

---

## 2. The timing basis, corrected (F9)

The concept (section 7.2) gives **10 ms** as the detection latency of every check in the safety task. That is the
*nominal* value. The analysis looked for what guarantees it and found nothing below the watchdog:

- The safety task is scheduled with `scheduleAtFixedRate(…, SYSTEM_CYCLE_TIME = 10, …)`
  (`ecu/application/src/systems/SafetySystem.cpp`; project value `modules.safeLifecycle.task_period_ms` since SYS-25). No code compares the distance between two safety cycles with a
  time base.
- The watchdog is serviced every `WATCHDOG_CYCLIC_COUNTER` = 8 safety cycles (derived from
  `modules.safeLifecycle.watchdog_timeout_ms` and the period since SYS-25) and fires after 250 ms. It therefore
  fires only when **8 × (actual cycle) > 250 ms**, i.e. when the actual cycle exceeds **31.25 ms**.
- The watchdog runs in timeout mode (`Watchdog::enableWatchdog` sets no `WIN`), so a safety task that runs too
  **fast** is not caught by the hardware either; SM-03 only counts calls of the service function.

Consequence: a safety task that runs at any period between 10 and 31.25 ms — through a wrong period, a starving
higher-priority interrupt load, a clock plan error that slows the OS tick, or a timer fault — is **not detected**, and
every "10 ms" latency in section 3 becomes up to **31.25 ms** plus the reaction. The table below uses both values.

| Quantity | Nominal | Guaranteed today |
|---|---|---|
| detection latency of a check in the safety task | 10 ms | **31.25 ms** |
| SM-19 (10 consecutive failures) | 100 ms | 312.5 ms |
| SM-20 (full multiplexer round, 6 cycles) | 60 ms | 187.5 ms |
| SM-03 / SM-02 (every 8 cycles) | 80 ms | 250 ms |
| SM-01 watchdog | 250 ms | 250 ms (hardware counter on the LPO clock) |
| SM-06 deadman | 10 ms | 10 ms (hardware counter, LPIT) |

The two hardware counters (SM-01, SM-06) are the only times that do not depend on the safety task keeping its period.
Closing F9 — a check of the safety task's own period against a free-running hardware timer, or the watchdog's window
mode — turns every "guaranteed" value back into the nominal one (proposed task T1, section 7).

**Since SYS-40 (period part of SM-04, section 3.1).** Every safety cycle starts with a comparison of its distance to the previous
cycle start against the LPTMR, counting on the LPO clock (the watchdog's oscillator, independent of the core clock,
the OS tick and the OpenBSW system time). The bound is derived from the nominal period (10 ms), the tolerated
lateness of a cycle start (2 ms, an assumption until B4) and the data-sheet range of the LPO: an interval of
**17 ms or more is always reported**, one of 12 ms or less never. Over each window of 7 cycles (the watchdog service
interval, 8 before SYS-53) the sum must reach 59 ticks (68 before SYS-53), so a task that runs too fast, or a timer that runs too slow or stopped, is
reported as well. What "guaranteed" means now:

- while the safety task runs, two consecutive runs of every check are **at most 17 ms** apart, or the late cycle
  raises `SAFETY_CYCLE_PERIOD_VIOLATION` (S1 + S2) at its own start, **before** its checks run;
- a task that **stops** is still detected by SM-01 only (250 ms). A stall that ends before the watchdog fires is
  reported when it ends, not after 17 ms: the check has no interrupt of its own (a hardware deadline needs a vector in
  the family's start-up code, proposed task T1b, section 7). SM-06 covers a stall of the whole software > 10 ms on
  boards with a power stage.

| Quantity | Nominal | Guaranteed before SYS-40 | Guaranteed since SYS-40 (task running) |
|---|---|---|---|
| detection latency of a check in the safety task | 10 ms | 31.25 ms | **17 ms** |
| SM-19 (10 consecutive failures) | 100 ms | 312.5 ms | 170 ms |
| SM-20 (full multiplexer round, 6 cycles) | 60 ms | 187.5 ms | 102 ms |
| SM-03 / SM-02 (every 7 cycles since SYS-53, 8 before) | 70 ms (80 ms before SYS-53) | 250 ms | 119 ms (136 ms before SYS-53) |
| SM-01 watchdog | 250 ms | 250 ms | 250 ms (unchanged: the stop case) |
| SM-06 deadman | 10 ms | 10 ms | 10 ms |

The sections of section 3 keep their original "guaranteed" statements; section 3.7 gives the values since SYS-40.

**SM-01 over the LPO range (issue 2).** The 250 ms of the watchdog are the nominal LPO; over the LPO range of the
derivative (mcudb `clock.lpo`, S32K1xx 113 … 139 kHz) the time-out lies between **230 and about 283 ms** (derived from
`WDOG_TOVAL`, [`hsi-s32k1xx.md`](hsi-s32k1xx.md)). The service interval of 7 safety cycles is derived at the fastest
LPO, so the watchdog never fires early; the worst case to the reset is **about 283 ms** (concept 7.2 issue 3).

### 2.1 Faults of the time bases of the supervision (issue 2)

The mechanisms of this section run on three time bases: the safety task on the OpenBSW system time (DWT on
ARMv7-M), SM-01 and the period part of SM-04 on the LPO, SM-06 on the LPIT's functional clock (SIRC). Issue 1 assumed
these clocks; the dependent failure analysis ([`dfa.md`](dfa.md) DF-05, DFA-2, DFA-6, implemented by SYS-95) and the
board of 2026-10-03/04 give what happens when one of them fails. The rows are those of concept 7.2 issue 3; every time
says whether it is a constant, derived or measured.

| Fault of a time base | What reacts | Detection | Outputs de-energised | Evidence |
|---|---|---|---|---|
| OpenBSW system time **stops** (for example `DEMCR.TRCENA` cleared) | SM-06, then SM-01 - not the period check of SM-04 (kind 4): the safety task is scheduled on this time base and does not run any more (DFA-6, corrected by the board) | SM-06 after its **10 ms** (constant of the family port), independent of the system time | after about **10 ms** (SM-06 walks the table); the reset follows through SM-01 after about **160 … 283 ms** (**derived**: the watchdog time-out over the LPO range minus at most one service interval of 70 ms) | board 2026-10-03/04 (SYS-95 B5: `WATCHDOG RESET: outputs off`, SM-06 expired, `CYCCNT` standing); `progress-checks`, `safety-progress` |
| OpenBSW system time **slow or fast** while it runs | SM-04, kinds 4 and 5 (DFA-6 (a), SYS-95) | at the end of a window of the period check (eval board: 7 cycles, 70 ms), against the LPTMR on the LPO | as SM-04: S1 + S2 | host (`progress-checks`, `safety-progress`); board SYS95-B4: no false alarm, also across a J-Link exit |
| LPIT functional clock **gated** (PCC) | SM-17 | the next access to the LPIT is a bus fault, within one poll of the halt guard, about **1 ms** (derived from the poll period) | about 1 ms plus the walk (derived) | board 2026-10-03 (SYS-95 B2: `HARD FAULT RESET: outputs off`) |
| LPIT functional clock **too slow** | SM-06 progress check (DFA-2, SYS-95) | the deadman's counter must change within a budget of two LPIT ticks per safety cycle (derived from the clock plan, 22 polls on the eval board); a counter that stands still or moves too slowly is `DEADMAN_CONFIGURATION_ERROR` field 0x100 | within a few safety cycles plus the walk (S1 + S2) | board 2026-10-04 (`SIRCDIV2` /64, deadman 640 ms instead of 10 ms: `SAFETY RESET (DEADMAN_CONFIGURATION_ERROR, fields 0x100)`); `lpit-deadman-progress`, `safety-progress` |
| **SIRC lost** while the core runs - **residual risk DF-05** | none in time; SM-01 by its hardware reset | SM-06 runs on the same clock and stops; no log line and no walk were observed | **not shown** before the watchdog reset, at most about **283 ms** (derived from the watchdog time-out); then the pins in their reset state (AoU-07) | board 2026-10-03 (SYS-95 B2b: hardware reset of the watchdog, `RCM_SRS` 0x20, no line, no walk); [`dfa.md`](dfa.md) DF-05, safety case D-16; investigation and architecture with **SYS-195** after Release R1 |

The integrator takes the last row's time for his FTTI or provides an independent shut-off path (AoU-13, concept 7.2).
SM-01 and SM-06 carry these faults in their blocks (latent faults, residual) since issue 2.

---

## 3. Per-mechanism analysis

### 3.1 Program flow and time (concept 6.1)

#### SM-01 — MCU watchdog

| Field | Content |
|---|---|
| Faults considered | the safety task stops: endless loop or deadlock at equal or higher priority, scheduler or OS tick dead, interrupt storm, core clock lost (the watchdog counts on the LPO clock, `CS[CLK] = 1`, independent of the core clock). **Not** considered by this mechanism: a slowed safety task (F9), a wrong flow inside the task that still reaches the service (SM-04), a service from the wrong place (SM-03), a service that comes too early (no window mode) |
| Detection | hardware time-out 250 ms at the nominal LPO (230.2 ms at the fastest, about 283 ms at the slowest LPO of the derivative; derived, section 2); serviced from the safety task every 7 cycles (since SYS-53, derived at the fastest LPO; 8 before) |
| Reaction | watchdog warning interrupt (`CS[INT] = 1`, IRQ 22) → its handler walks the safe outputs table as its first action (SM-17), then the watchdog reset (S2). If interrupts are masked when it expires (a stuck critical section), only the reset de-energises: the pins return to their reset state, which is safe only under AoU-07 |
| Times | detection ≤ 250 ms after the last service at the nominal LPO, **≤ about 283 ms** over the LPO range (derived, concept 7.2 issue 3); reaction < 1 µs (walk, computed; board bound ≤ 21 µs, B3) or the reset. **Minimum FTTI served: > 283 ms** (issue 1: > 250 ms, the nominal LPO only) |
| Diagnostic coverage | **medium.** Loss of execution of the safety task is detected completely and independently of the core clock. Blind spots: the task slowed below the 31.25 ms bound (F9; since SYS-40 covered by SM-04 against the same LPO), too-early service (no window; since SYS-40 the SM-04 window covers a service interval that is too short, against an independent clock, as an event instead of a reset), and a common cause on the chip (same supply, same die; AoU-13). SM-04 also covers a stopped LPO, which would stop the watchdog silently |
| Latent faults of the mechanism | watchdog unable to reset: SM-07 at every start. Configuration drift: SM-02 every 70 ms (nominal, 80 ms before SYS-53), since SYS-41 including `INT`, `WIN`, `PRES` and `UPDATE` — a cleared `CS[INT]` is now `SAFE_WATCHDOG_CONFIGURATION_ERROR` (context bit 2) instead of silently removing the de-energising before the reset (F12 closed for the watchdog) |
| Evidence | `nxconfig-watchdog` (relation 3 × service ≤ time-out); `safe-supervisor` (the hold services the watchdog); `safe-watchdog` (SYS-41: real `SafeWatchdog.cpp` and family check on a WDOG register model, every field falsified singly, service bracket); board 2026-09-18 (halt > 250 ms: watchdog warning handler walked the table, EXC_RETURN `0xffffffed`, DRV_EN off); board 2026-09-21 SYS-18 (`safety wdg` endless loop → watchdog reset, wake-up source `0x00000006`) |
| Residual / action | F9 → SM-04 (SYS-40); board check B6 passed 2026-09-22 (8.1). **Residual risk DF-05 (issue 2, CM-02 decision Z3):** after a loss of the SIRC the halt deadman SM-06 stands with its clock, nothing walks the table, and SM-01's hardware reset after **up to about 283 ms** (derived) is the only reaction - the outputs are not shown to be de-energised before it (board 2026-10-03, SYS-95 B2b; section 2.1). Carried by [`dfa.md`](dfa.md) DF-05, safety case D-16 and **SYS-195** (after Release R1); the integrator takes this time or provides an independent shut-off path (AoU-13). A stopped system time (section 2.1) also ends in SM-01's reset after about 160 … 283 ms, but SM-06 has de-energised after about 10 ms |

#### SM-02 — Watchdog configuration check

| Field | Content |
|---|---|
| Faults considered | the watchdog is disabled, switched to another clock, or its time-out changed after start |
| Detection | `checkWatchdogConfiguration(250 ms)` in `SafeWatchdog::cyclic()`, every 7 safety cycles (8 before SYS-53): `CS[EN]`, `CS[CLK]` and `TOVAL` against the expected values; since SYS-41 in the same call the family check `checkWatchdogControl()` (`platforms/s32k1xx/port/safety/safeSupervisionCheck`): `CS[INT]` against the requested value, `CS[WIN]` = 0, `CS[PRES]` = 0, `CS[UPDATE]` = 1 (as `enableWatchdog()` writes it) |
| Reaction | `SAFE_WATCHDOG_CONFIGURATION_ERROR` → S1 + S2, since SYS-41 **with the differing fields in the context word** (1 driver check, 2 INT, 4 WIN, 8 PRES, 16 UPDATE; `ValueMonitor` with a `uint32_t` context). The reset reconfigures the watchdog, so a transient corruption is healed; a permanent one loops (F13) |
| Times | nominal 70 ms (80 ms before SYS-53), guaranteed 250 ms; reaction < 1 µs. **Minimum FTTI served: > 250 ms** (but the watchdog itself still covers the stop case) |
| Diagnostic coverage | **medium.** Since SYS-41 every configuration field that changes the watchdog's effect is checked: disable, clock, time-out, warning interrupt, window, prescaler, update. Remaining blind spots: the check is sampled every 7 safety cycles, and `CS[UPDATE]` stays 1, so the watchdog remains reconfigurable at run time — a change is detected, not prevented |
| Latent faults of the mechanism | a check that is skipped is seen by the watchdog service bracket of SM-04 only if the service is skipped with it |
| Evidence | `safe-watchdog` (SYS-41: real `SafeWatchdog.cpp` with the family check and the real `SafeSupervisor` on a WDOG register model — the configuration of `enableWatchdog` passes, each field falsified singly is the event with that field in the context; mutants: family check not called, INT not compared, PRES left out, context dropped). Reaction: `safe-supervisor`. The OpenBSW driver in `external/openbsw/platforms/s32k1xx/safety/safeBspMcuWatchdog` stays outside our tests |
| Residual / action | F12 closed for the watchdog by SYS-41 (T2). Open: whether `UPDATE = 0` after start is wanted (needs a neXtBSW copy of the OpenBSW driver, which is never changed in the submodule); board check B6 |

#### SM-03 — Watchdog over-service check

| Field | Content |
|---|---|
| Faults considered | the watchdog is serviced by something other than the safety task (a runaway loop that calls the service function) |
| Detection | the service counter of `bsp::Watchdog` must advance by exactly one between two checks (every 7 cycles, 8 before SYS-53) |
| Reaction | `SAFE_WATCHDOG_SERVICE_DEVIATION` → S1 + S2 |
| Times | nominal 70 ms (80 ms before SYS-53), guaranteed 250 ms. **Minimum FTTI served: > 250 ms** |
| Diagnostic coverage | **low.** Only services through `Watchdog::serviceWatchdog()` are counted; a runaway that writes the refresh word to `WDOG->CNT` directly is invisible. There is no window, so timing of the service is not checked at all |
| Latent faults of the mechanism | none |
| Evidence | `safe-watchdog` (SYS-41: over-service is `SAFE_WATCHDOG_SERVICE_DEVIATION`); reaction `safe-supervisor` |
| Residual / action | the window mode (T1 alternative) would replace most of this check; F15 closed for `safeWatchdog` (T3, SYS-41) |

#### SM-04 — Safety manager sequence monitor

| Field | Content |
|---|---|
| Faults considered | control flow through the safety cycle is not the intended one: the cycle is entered twice without being left, or left without being entered; the same for the watchdog service bracket |
| Detection | `safetyManagerSequenceMonitor` (OpenBSW `safeMonitor::Sequence`) with, since SYS-41, **six checkpoints** per safety cycle: ENTER, MEMORY_CHECKED (after `safe_memory::cyclic`), SYSTEM_CHECKED (`safeSystem.cyclic`), IO_CHECKED (`safeIo.cyclic`), PERIPHERALS_CHECKED (deadman configuration, SM-06), LEAVE; `safeWatchdogSequenceMonitor` around the service |
| Reaction | `SAFETY_MANAGER_SEQUENCE_DEVIATION` / `SAFE_WATCHDOG_SEQUENCE_DEVIATION` → S1 + S2 |
| Times | a checkpoint out of order is seen at the next hit, a missing LEAVE at the next ENTER: nominal 10 ms, guaranteed 31.25 ms (17 ms since SYS-40). Since SYS-40 a seventh checkpoint PERIOD_CHECKED follows the period check (period part below) directly after ENTER |
| Diagnostic coverage | **medium** (was low before SYS-41). A cycle that returns early or skips one of the sub-checks hits the next checkpoint out of order. Blind spots: a sub-check that returns early **inside** itself still reaches its checkpoint; the watchdog service (every 7 cycles) has only its own bracket. A cycle that never starts is SM-01's |
| Latent faults of the mechanism | none |
| Evidence | `safety-cycle` (SYS-41: real `SafetyManager` and `SafeSupervisor`, 24 cycles without event, early end and a skipped sub-check are deviations; mutant: IO checkpoint removed); reaction `safe-supervisor`; the service bracket `safe-watchdog` |
| Residual / action | closed by SYS-41 (T2) |

**SM-04, period part (SYS-40, finding F9).** The sequence monitor answers "in which order"; since SYS-40 the same
mechanism also answers "at which period" for the safety cycle. Both parts share the reaction (S1 + S2) and the place
in the cycle; the period part has its own event, time base and evidence:

| Field | Content |
|---|---|
| Faults considered | the safety task runs at a period other than its nominal one while it still runs: a wrong period in the code, starvation by interrupt load or long critical sections, a clock plan error or a fault of the OS tick (SysTick) or of the OpenBSW system time that stretches or compresses it; too fast as well as too slow. Latent faults of the mechanism: the timer stopped, re-clocked, switched to another mode, its clock gate or the LPO1K clock off (F9) |
| Detection | `SafetyManager::checkCyclePeriod()` as the **first** step of every safety cycle, directly after ENTER: the family's timer configuration is checked (`checkCycleTimer()`: PCC clock gate, `CSR[TEN]`, time counter / free running / no interrupt / no DMA, `PSR` = LPO1K with prescaler bypass, `SIM_LPOCLKS[LPO1KCLKEN]`), then the LPTMR counter is read and handed to the hardware-independent `CyclePeriodCheck` (`ecu/safety/safeWatchdog`): **interval** to the previous cycle start > **14 ticks** → kind 1; **window** of 7 intervals (the watchdog service interval; 8 intervals and 68 ticks before SYS-53) < **59 ticks** → kind 2; deviating timer configuration → kind 3 (the timer is not read then). Bounds derived in `SafetyManager.cpp` from the period (10 ms), the tolerated lateness of a cycle start (2 ms, `CYCLE_LATENESS_MS`) and the LPO range of the derivative (mcudb `clock.lpo`, S32K1xx 113 … 139 kHz, i.e. 0.882 … 1.086 ticks per ms, SYS-53; 141 kHz / 1.102 before); a counter difference misses the true time by less than one tick |
| Time base | LPTMR0, 16-bit time counter, free running, on LPO1K_CLK (`PSR[PCS]` = 01, prescaler bypassed): 1 tick = 1 ms nominal, wrap after 65 s. The LPO is the watchdog's oscillator: always on, no SCG divider, no PCC clock select, never the core clock, so it does not depend on the project's clock plan. In time counter mode the LPTMR **stops while the core is halted** — a debugger halt is no period violation, like for the watchdog. Not the OS tick, not the OpenBSW system time (DWT, on ARMv6-M LPIT channel 0) and not LPIT channel 1 (SM-06) |
| Reaction | `SAFETY_CYCLE_PERIOD_VIOLATION` (appended to the event list) → S1 + S2, context: kind in bits 31..24, ticks resp. deviating fields in bits 23..0. The late cycle reacts **before** its own checks run |
| Times | an interval of **17 ms or more is always reported** (17 ms at the slowest LPO of the data sheet are 15.008 ticks), one of 12 ms or less (period + lateness) never, at any LPO; detection at the start of the late cycle. A window is always reported below a mean period of **7.6 ms**; a stopped timer is reported at the end of the first window (≤ 7 cycles). **Minimum FTTI served** for the checks of the safety task: **> 17 ms** plus reaction while the task runs; a task that stops is SM-01's (250 ms) |
| Diagnostic coverage | **medium.** A slowed or accelerated safety task is detected against an independent oscillator at every cycle, including faults of the mechanism's own timer (configuration re-checked at every cycle, a stopped counter seen by the window). Blind spots: detection only when the late cycle runs (no interrupt of its own, so a stall that ends before the watchdog fires is reported at its end, not after 17 ms — T1b); the tolerance band between 12 and 17 ms, which is wide because of the LPO's data-sheet range; a common drift of LPO and core clock in the same direction; the LPTMR shares the die (AoU-13). On ARMv6-M with the deadman (`MCR[DBG_EN] = 1`) the OpenBSW system time keeps counting through a debugger halt, so the scheduler catches up with a burst after the halt, which ends in a window report and a reset — a debugging restriction of S32K11x projects with a power stage, not a safety gap |
| Latent faults of the mechanism | the timer's configuration and clock enables are re-checked in every cycle (kind 3); a counter that stops without a configuration change is seen by the window (kind 2, value 0). The derived bounds are static assertions of the build (window longer than one interval, window within one wrap) and are recomputed independently by `cycle-period-constants` |
| Evidence | `cycle-period-check` (hardware-independent part with two own test profiles: 32-bit counter at 5 ms and 16-bit; reference sample, bounds, wrap-around, window, catch-up, stopped counter; five mutants); `lptmr-cycle-timer` (start sequence of [RM13] 49.5.1, latched read, each configuration field falsified singly, clock gate off without LPTMR access; five mutants); `safety-cycle` (real `SafetyManager` with the real check: 60 runs of 400 cycles with LPO 0.883 … 1.102 kHz, every phase and random lateness up to 2 ms without a report; 17 ms after the previous cycle reported before the memory check at every LPO; a task at 20 ms reported at its first slow cycle; 5 ms and 7 ms at the fastest LPO reported at the end of the first window; stopped timer; configuration deviation without read; five mutants); `cycle-period-constants` (values from the configuration, no literal in the code; bounds recomputed); reaction `safe-supervisor`. Board: B4 and B7 passed 2026-09-22 (issue 1 said "no board run yet"; 8.1) |
| Residual / action | since SYS-52 the reset of a period violation writes one synchronous console line; a stopped system time (debugger cleared DEMCR.TRCENA, PLAT-67) stops the safety task itself and is SM-01's (watchdog). Board checks **B4** (cycle distance under load, confirms the 2 ms lateness; passed 2026-09-22 without GPIO measurement) and **B7** (provoked violation; (a) to be repeated after SYS-52); **T1b** hardware deadline: the LPTMR compare interrupt as a deadman of the safety task (walk of the safe outputs table at the bound, independent of whether the task comes back) — needs the LPTMR vector in `platforms/s32k1xx/port/main/src/bsp/startUp.S` and in `tools/bswgen` (`FAMILY_IRQ_HANDLERS`), outside SYS-40's paths; the constants (period, lateness, LPO range) into their configuration levels: done with SYS-25/SYS-53. *Issue 2:* B4 and B7 (a) … (c) passed on the board 2026-09-22 (8.1; B7 (a) repeated with `SetSkipDebugDeInit`), B4 again 2026-10-03 for 32 min under UDS load (SYS-95) without a report |

**SM-04, progress of the system time (SYS-95, finding DFA-6 of [`dfa.md`](dfa.md), issue 2).** Since SYS-95 the
period part has two more kinds: over the window of the period check the OpenBSW system time must advance like the
LPTMR on LPO1K within the LPO range of the derivative, with a slack of one period plus the lateness (the one-off loss
after a debugger exit, PLAT-67). A system time that runs **too slow** is `SAFETY_CYCLE_PERIOD_VIOLATION` **kind 4**,
one that runs **too fast** **kind 5** (S1 + S2), at the end of a window (eval board: 7 cycles, 70 ms; `ProgressSupervision.cpp`).
Not covered by these kinds: a system time that **stops** - the safety task is scheduled on it and does not run any more;
SM-06 de-energises after about 10 ms and SM-01 resets after about 160 … 283 ms (section 2.1; board 2026-10-03/04,
SYS-95 B5). Evidence: `progress-checks`, `safety-progress` (host, with mutants); board SYS95-B4 (no false alarm, also
across a J-Link exit). A wake-up of the safety task that does not depend on the system time is for after Release R1
(dfa.md DFA-6).

#### SM-05 — RTE run-time supervision

| Field | Content |
|---|---|
| Faults considered | a task cycle takes longer than the task period, or a runnable longer than its own period. **Not** considered: a task that is not activated at all or activated late (starvation by higher priorities) — the RTE measures durations from the start of a cycle, and records the activation distance only as a statistic (`RteSystem.cpp`, "activation jitter"), without an event (F10) |
| Detection | measured per cycle in the generated RTE; latched once per measurement window; reported through the event mailbox to the safety task (SYS-37) |
| Reaction | **S3** (log only), by decision |
| Times | detection at the end of the offending cycle; delivery up to one safety cycle later. No de-energising reaction, so **no FTTI is served** |
| Diagnostic coverage | **medium** for overruns of activated tasks and runnables (every cycle, deadline from the configuration); **none** for missing or late activation |
| Latent faults of the mechanism | mailbox corruption and overflow are reported as own contexts (SYS-37) |
| Evidence | `rte-deadline` (with mutants: `>=` instead of `>`, no latch, deadline from task period), `safe-supervisor-mpu`, `safety-context`, `safety-context-elf`; board 2026-09-21 (eighth part): load-test build reports `RTE_TIMING_VIOLATION` per window, no bus fault |
| Residual / action | F10 → SYS-21 (temporal freedom from interference). Whether S3 is admissible for a violation was open point 6 of concept section 9.4 (issue 2); concept issue 3 gives the answer of the gap analysis: S3 only where the condition cannot violate a safety requirement within the FTTI or the reaction is allocated to another element - here the application, AoU-18 (C18) |

#### SM-06 — Debug-halt / stall deadman

| Field | Content |
|---|---|
| Faults considered | the core executes no software for longer than 10 ms: debugger halt, interrupt lock, starved BSP task (the retrigger runs in the BSP task's 1 ms poll), later a flash operation that stalls the core |
| Detection | LPIT channel 1, 32-bit down counter, `MCR[DBG_EN] = 1` (keeps counting while the core is halted), 10 ms, retriggered at every 1 ms poll; second source `DFSR` |
| Reaction | the interrupt walks the safe outputs table (S1 without reset); the guard then inhibits the PWM and takes DRV_EN away until the next explicit enable |
| Times | detection 10 ms (hardware counter, independent of the safety task); reaction: exception entry + walk on the first instruction after the halt. **Minimum FTTI served: > 10 ms** for a stall; for a debugger halt the reaction comes only when the core runs again (concept 6.1) |
| Diagnostic coverage | **high** for stalls and halts ≥ 10 ms. Blind spots accepted by the user (`io32-halt-erkennung`): a halt < 10 ms with a probe that clears `DFSR`. **Named blind spot (issue 2, CM-02 decision Z3):** the loss of its own clock - SM-06 runs on the SIRC (LPIT functional clock) and stands with it, so a lost SIRC is caught by SM-01's hardware reset only (residual risk DF-05, section 2.1, SYS-195); a gated clock is a bus fault (SM-17) and a slow clock is seen by the progress check since SYS-95 (DFA-2). The class stays **high** for what the mechanism guards - the stall and the halt of the software |
| Latent faults of the mechanism | the retrigger re-enables the channel (`SETTEN`) every millisecond, which heals a cleared enable. Since SYS-41 a configuration re-check exists: `checkLpitDeadman()` compares `MCR[M_CEN]`, `MCR[DBG_EN]`, the channel's `MIER` bit, `TCTRL` (32-bit periodic, no trigger; `T_EN` deliberately excluded because the retrigger and the handler switch it), `TVAL`, the NVIC enable and the priority of `LPIT0_Ch1_IRQn`, **every safety cycle from the safety task** (`SafetyManager::checkSupervisionPeripherals`, not from the BSP task that retriggers the deadman), and raises `DEADMAN_CONFIGURATION_ERROR` with the differing fields in the context (S1 + S2); detection nominal 10 ms, guaranteed 17 ms since SYS-40. The expected values belong to the owner of the deadman and come through `deadmanExpectedConfiguration()`; issue 1 said "host verified, target binding pending (SYS-48)" - **SYS-48** added the strong definition (out of line in `Integration.cpp` since SYS-133), and the fault injection of **SYS-51** proved the re-check on the target (falsified `TVAL`, `MIER`, NVIC priority and `MCR[DBG_EN]` each reset with last cause `0x110`, 8.1 B6; F12 closed, AN-012). **Progress check (SYS-95, DFA-2, issue 2):** the safety cycle polls the deadman's counter until it changes, within a budget of two LPIT ticks derived from the clock plan (22 polls on the eval board); a counter that stands still or moves too slowly with the channel enabled is `DEADMAN_CONFIGURATION_ERROR` field 0x100 (S1 + S2); a channel disabled by the retrigger or an expiry gives no verdict in that cycle. Not seen by either check: the lost SIRC (DF-05, above) |
| Evidence | `debug-halt-guard` (real drivers, PWM inhibited, DRV_EN and KL30B off until next enable), `safe-outputs-order` (vector of `LPIT0_Ch1` walks the table before any call), `lpit-deadman-check` (SYS-41: the re-check against an LPIT/NVIC register model, every field falsified singly, with and without `T_EN`), `safety-cycle` (re-check called in the safety task, event with context; without a deadman no check), `lpit-deadman-progress` and `safety-progress` (SYS-95: the progress check, with mutants); board 2026-09-21 (register part: `LPIT0_MCR = 0x9`, TIF1 set during halt) and 2026-09-21 (IO-32 fully passed: halts of 30 s and 60 s with running bridge, oscilloscope silent after the J-Link go command, 10 min without false trip); board 2026-09-22 (SYS-51: the re-check on the target, 8.1 B6); board 2026-10-03/04 (SYS-95: `SIRCDIV2` /64 gives field 0x100; a stopped system time expires SM-06; a lost SIRC ends in the watchdog reset without a walk - section 2.1) |
| Residual / action | the target binding (SYS-48) and its board check (SYS-51) are done. **Residual risk DF-05:** a lost SIRC stops SM-06 silently (section 2.1, SM-01); investigation and architecture (for example the deadman on another clock) with **SYS-195** after Release R1. The alternative of DFA-2 (`SIRCVLD` in the `SYSTEM_CLOCK` class of SM-22) stays with SYS-55 |

#### SM-07 — Startup watchdog self-test

| Field | Content |
|---|---|
| Faults considered | the watchdog hardware cannot reset the MCU (latent fault of SM-01) |
| Detection | OpenBSW `executeFastTest` from `main()`: two deliberate watchdog resets (test low / test high) at every start, before the application |
| Reaction | `WATCHDOG_STARTUP_CHECK_FAILURE` → **S1 held** (SYS-23) |
| Times | at start-up (about 6 ms); the latent-fault interval of SM-01 is therefore **the time between two starts** |
| Diagnostic coverage | **high** for "watchdog cannot reset": both test halves exercise the real reset path |
| Latent faults of the mechanism | none needed at this level |
| Evidence | `safe-supervisor`, `safe-supervisor-bare` (reaction: hold, no reset, outputs walked every round, watchdog serviced); board 2026-09-21 (fifth part): **reaction** proven with a simulated failure (J-Link breakpoint), console line, no reset for 16 s; the **detection** of a real hardware failure cannot be provoked |
| Residual / action | for ECUs that are rarely restarted, the latent interval equals the operating time → AoU-25 (section 6, F13/F3; *issue 1:* proposed - binding since concept issue 3, concept 4.2, CM-07 ZG) |

### 3.2 Memory (concept 6.2)

#### SM-10 — MPU: protected RAM (fusa gate)

| Field | Content |
|---|---|
| Faults considered | code outside the safety context writes the safety manager's data or stack (supervisor state, monitors, counters). **Since SYS-97** also an overflow of the interrupt stack (MSP), which nested interrupt levels and the start-up share: before, it overwrote `.heap` and the general `.bss` (RTE, deferred event box) without any detection |
| Detection | MPU region read-only outside the safety shell; any write from elsewhere is a bus fault on the access itself. Static rule and ELF check keep reporters outside the protected RAM (SYS-37). **SYS-97:** a guard of 1 KB directly below the MSP (linker `.stack_guard`, 32-byte aligned) with its own descriptor (core: read, no write), cut out of the full-access region that contains it (the SYSMPU grants an access if any descriptor grants it). The first push or exception frame that crosses `__StackLimit` writes into the guard. The guard is at least the largest frame on the MSP plus the larger of that frame and an exception frame, so no frame reserved without a write lets a write jump over it (checked by `tools/stackuse`, `isr_stack_guard_bytes`) |
| Reaction | fault exception → SM-17 (walk, dump, reset) |
| Times | on the faulty access (hardware) + exception entry + walk. **Minimum FTTI served: exception latency** |
| Diagnostic coverage | **high** for writes by the core from outside the shell. Blind spots: while the shell is open all interrupts are suspended, so only the shell's own code can write (by design); accesses by other bus masters are **not** covered: descriptor 0 (whole address space) grants the DMA (M2) and the ENET (M3, S32K148) rwx, and the SYSMPU grants an access when any valid descriptor grants it, so the "no access" for M2/M3 in the locked gate word and the guard has no effect (SYS-171, finding SFP-F03; open point N3 answered, HSI H10). *Caveat of B17 (DOC-59):* the board reads `RGD0_WORD3` = 0 (B17, 2026-10-02) - the valid bit of descriptor 0 is not set after `init()`, so its M2/M3 rights may not be in effect; until that is settled (S171-3, SYS-171) this analysis keeps the unfavourable reading. No neXtBSW driver enables the DMA or the ENET today (dfa DF-44); the **application's** data is not protected at all (SYS-21, AoU-17) |
| Latent faults of the mechanism | SM-11 (gate state), SM-12 (MPU enabled), SM-13 (descriptors) |
| Evidence | `safe-supervisor-mpu` (write to the protected page trapped by the host MMU; counter-test with the pre-SYS-37 code), `safety-context`, `safety-context-elf`, `bswgen-mpu`; board 2026-09-21 (fifth part): the pre-SYS-37 write from the RTE task into the protected RAM **was trapped on the target** (bus fault → HardFault → reset) — an unintended but real proof of the detection. SYS-97: `safety-gate` (guard descriptor on the MPU register model: every word of the guard readable, not writable by the core, heap below and stack above writable), `application-linker` (guard directly below the stack, size, alignment, rejected sizes), `stackuse-demo` (guard size against the largest frame, sizes equal to the linker symbols) |
| Residual / action | N3 (rights of the DMA/ENET master) → SYS-21: before the first such user restrict descriptor 0 for M2/M3 and grant the master only the regions it needs; until then a configuration that enables one must be refused. SYS-97: the guard write of the core may be an **imprecise** bus fault (buffered write); a fault during the exception entry of the hard fault itself can lock the core up, which ends in a reset (RCM_SRS[LOCKUP], counted by the reset-loop limit) without the line - both to be seen on the board (`inject mspoverflow`, test build). *Board (issue 2):* 2026-09-30 and 2026-10-03 (suite case BT-S2-07): MSP use 744 of 4 096 B, guard 1 024 B; `inject mspoverflow` gives `HARD FAULT RESET: interrupt stack overflow (guard), outputs off` with the dump and `reset loop 1 of 3` - the guard write was caught. DMA writes into the guard are not blocked (RGD0 grants the other masters - with the caveat of B17 above) |

#### SM-11 — MPU state check on entry and exit

| Field | Content |
|---|---|
| Faults considered | the gate was left open by a previous cycle or by other code, or is closed when the shell should have opened it |
| Detection | `mpuStatusCheckOnEnterMonitor` / `mpuStatusCheckOnLeaveMonitor`, every safety cycle |
| Reaction | S1 + S2 |
| Times | nominal 10 ms, guaranteed 31.25 ms |
| Diagnostic coverage | **medium**: complete for the gate bit, but sampled at the cycle boundaries; a gate opened and closed again between two cycles is not seen (the write it allowed is not undone) |
| Latent faults of the mechanism | none |
| Evidence | `safety-gate` (SYS-42): real `SafetyManager::cyclic`, gate (`MemoryProtection.cpp`, `ProtectedRamScopedUnlock.h`) and supervisor on an MPU register model with the descriptors of `MemoryProtection::init()` — gate left open, lost closing write and every single flipped bit of the gate word reported on entry before the first check, gate closed in the cycle and lost opening write reported on exit; `safety-gate-mutations` (9 built-in faults, all detected); reaction `safe-supervisor` |
| Residual / action | — (F15 closed for this mechanism by SYS-42); the sampling blind spot stays |

#### SM-12 — MPU enabled and complete

| Field | Content |
|---|---|
| Faults considered | the MPU is switched off, or reports fewer region descriptors than the derivative has |
| Detection | `mpu::isGlobalEnabled()` and `hasRegionsOfDerivative()` against `NX_MPU_REGIONS` (mcudb), every safety cycle; context = `CESR` |
| Reaction | `MPU_NOT_ENABLED` → S1 + S2 |
| Times | nominal 10 ms, guaranteed 31.25 ms; while the MPU is off, SM-10 does not protect — the window is this detection time |
| Diagnostic coverage | **high** for the enable and the descriptor count (both read from the hardware every cycle against a derivative fact) |
| Latent faults of the mechanism | none needed |
| Evidence | `bswgen-mpu` (MpuHostTest, derivative facts), reaction `safe-supervisor` |
| Residual / action | — |

#### SM-13 — MPU region descriptors unchanged

| Field | Content |
|---|---|
| Faults considered | a region descriptor overwritten (start, end, access rights, valid bit) |
| Detection | `areRegionsConfiguredCorrectly()` compares every safety cycle: descriptor 0 (`WORD2` against `REGION0_ACCESS`, the constant `init()` writes), descriptors 1 … 7 (start, end, `WORD2`, valid bit) and the unused descriptors up to `NX_MPU_REGIONS − 1` (not valid); context = failing region, 0 for descriptor 0. `WORD2` is compared in every field the S32K1 implements (bus masters 0 … 3 and M0PE/M1PE, `0x007DFFFF`); the reserved bits 17/23 and the enables of the masters 4 … 7, which the S32K1 does not implement ([RM13] 15.1.2), are left out. **SYS-171:** before, descriptor 0 was never read and `WORD2` was compared in the fields of M0 … M2 only (`0x0001FFFF`, findings SFP-F02/F04) |
| Reaction | `MPU_CONFIGURATION_ERROR` → S1 + S2 |
| Times | nominal 10 ms, guaranteed 31.25 ms |
| Diagnostic coverage | **high**: every implemented field of every descriptor in use against the expected value; not covered: `RGD0_WORD0/1/3` (fixed whole address space; its valid bit after `init()` is board check B17). The expected values of descriptors 1 … 7 are computed by `init()` into the protected RAM (`.mpu_bss` of the safety library, write-protected outside the shell, ECC SM-15); `REGION0_ACCESS`, the access words and the masks are constants in flash (SM-14, SM-16) |
| Latent faults of the mechanism | none needed |
| Evidence | `bswgen-mpu`; reaction `safe-supervisor`. SYS-97: `safety-gate` (guard descriptor with full access written into it or its valid bit cleared → configuration error; a guard no full-access region contains and an empty guard → error naming descriptor 6). SYS-171: `safe-memory-mpu-regions` and `-16` (8 and 16 descriptors; reserved bits read as written and as 0: every one of the 22 field bits of descriptor 0 and of descriptors 1 … 7 detected and named, the write of `StaticBsp` through `RGDAAC0` changes nothing, descriptor 0 reported first), `safe-memory-mpu-regions-mutations` (15 built-in faults, among them the state before SYS-171 and a comparison of the unimplemented M4 … M7 bits) |
| Residual / action | board check B17 **passed 2026-10-02** (8.1): no `MPU_CONFIGURATION_ERROR` in 60 s, `RGD0_WORD2 & 0x007DFFFF` = `0x007C77D8`, the descriptors in use as expected; `RGD0_WORD3` reads 0 - the valid bit of descriptor 0 is **not** set after `init()`, so the M2/M3 rights of descriptor 0 may not be in effect; HSI H10, N3 and SM-10 carry this caveat since DOC-59 (S171-3 of SYS-171; CM-02 re-check BRD a); the restriction of descriptor 0 stays SYS-21 |

#### SM-14 — ROM check (CRC-32 over the flash areas)

| Field | Content |
|---|---|
| Faults considered | the program image in flash is not the one that was linked: multi-bit corruption beyond what the flash ECC detects, a partially erased or partially programmed area, a different image. **Since SYS-91** also the calibration segment (`memory.calibration`) during operation, against its **own** reference: it is not in the list (a dataset may be flashed or generated without a build, and the build reference would hold the ECU before the start-up check of `ecu/calibration` could apply `on_invalid`, board 2026-09-28), and every pass runs the owner's check (`RomCheck::setOwnAreaCheck`, `calibration::recheckSegment`: header CRC, values CRC, header unchanged since the start-up check accepted it). **Not** covered: the checksum list and the reference themselves (the bootloader's CRC covers them, SM-51), the bootloader, flash outside the image, the data flash, a calibration segment the start-up check did not accept (refused, replaced by the defaults, or used under `on_invalid: warn`: not in use or knowingly used) |
| Detection | since SYS-38: in the idle context, first after lifecycle level 2 (component `romcheck`): plausibility of the list, then CRC-32 through the CRC unit over the five areas (demo: 197 044 bytes at SYS-44), compared with `__checksum_result` written by the post-link step. **Since SYS-44** every pass starts with a known-answer test of the CRC unit (five fixed words, expected `0x498CFAA3`, the value of the Python model of the post-link step), and the pass is **repeated** by the idle handler of the application with the period `modules.safeMemory.rom_check_period_ms` (start to start; default 60 000 ms; 0 = once per start). No pass starts while a flash write is open (`RomCheckFlashWrites`, bracketed by every NvM write through `nvm::FlashWriteBracket`) or a flash command runs (`FTFC FSTAT[CCIF]` clear); a pass during which one began is discarded without a report and repeated |
| Reaction | mismatch, implausible list, failed known-answer test (`ROMCHECK_UNIT_FAILED`), no pass possible for more than one period because of flash writes (`ROMCHECK_OVERDUE`, SYS-44) or the calibration segment off its own reference (`ROMCHECK_OWN_AREA_FAILED`, SYS-91) → `ROM_CHECK_FAILURE` → **S1 held**. For the segment this is the run-time reaction only; at start-up the calibration check applies `on_invalid` (refuse: application functions stopped, UDS and canflash reachable), which is also what the power cycle after a hold leads to |
| Times | at start-up, after lifecycle level 2, plus the CRC run: **about 3 … 6 ms computed** (43 049 words at a few core cycles per word at 80 MHz, flash wait states assumed; the demo image has grown to 49 261 words by SYS-44, about 3.5 … 7 ms by the same estimate), **not measured** (F3, board checklist B1). Between the start and the end of the check the application already runs. **Re-check since SYS-44:** a corruption after the first pass is found within one period plus one pass (default 60 s); while flash writes keep the pass off, within two periods plus one pass at most, otherwise `ROMCHECK_OVERDUE`. Period 0: no re-check, a corruption after the check is found at the next start |
| Diagnostic coverage | **high** for the image content at the time of the check (every flash byte of the image is inside an area or is the list, enforced by the build; a CRC-32 detects every error of up to three bits and every burst of up to 32 bits in these lengths, and misses other patterns with a probability of about 2⁻³²). As a run-time mechanism **high** with a re-check period (SYS-44; the latent interval is the period), **low** with period 0 (once per start, F3). The calibration segment (SYS-91) gets the same: CRC-32 of zlib over header and values (same detection properties), every single bit flip found (host), same period; with period 0 its start-up check is the once-per-start pass |
| Latent faults of the mechanism | since SYS-44 the known-answer test before every pass detects a CRC unit that returns a fixed value (e.g. the reference) or computes with a wrong polynomial or configuration. A flash write indicator stuck at "writing" (a writer that never ends its write, or a wild write into the indicator, which lies in ordinary RAM on purpose) postpones the pass; the deadline of one period turns that into `ROMCHECK_OVERDUE` instead of a silently disabled check — **except with period 0**, where the first pass waits as long as the write lasts. The CRC unit is reserved for SM-14 (no other user in the repository; a second user would have to be serialised with the pass). A false alarm is excluded by construction (concept 6.2 notes); a flash write during a pass discards the pass instead of reporting it |
| Evidence | `rom-check` (every single-bit flip detected, implausible lists refused without touching the CRC unit; since SYS-44 the period with its own test profile, wrap-around, period 0, a corruption found by the re-check, known-answer test against a stuck unit and a lost polynomial bit, postponement and discard during flash writes and commands, the deadline, the pass outside the safety shell), `rom-check-mutations` (17 mutants of `RomCheck.cpp` and the indicator, 21 since SYS-91, all detected), `nvm-flash-write-bracket` (every NvM write and no read inside the bracket), `rom-check-image` (post-link step, synthetic images, the real linker template), `safe-supervisor` (reaction); board 2026-09-21 (eighth part): valid image passes, an image with one changed character is accepted by the bootloader and **held in the safe state** by SM-14; finding SYS-39 (console line missing when the hold comes from the idle context). SYS-91: `rom-check` (own-area check after the list, flip found by the re-check, not asked when the list fails, discarded with a flash write), `calibration-segment` (every bit flip of header and values, refused segment not armed, other dataset valid, changed segment), `rom-check-image` and `target-calibration` (five areas, the segment outside the list, another dataset and a damaged header keep the build reference valid) |
| Residual / action | F3 decided in section 5.1; T5 done by SYS-44 (host). SYS-91: the software CRC of the segment check has no known-answer test of its own - the start-up check computes the same CRC and must have found the segment valid before the run-time check is armed; board repetition of SYS-89 point 3 open. Board (issue 2): one pass at most 5 574 µs (B1, 2026-09-22); the re-check ran every period on the target (B9, 2026-09-22/23: 13 passes in 26 s with a 2 s period, 60 s spacing on the demo; suite case BT-RUN-07 2026-10-02). Open: the suspension during a real program-flash write on the target (the NvM writes of B9 wrote nothing; possible after PGMPART, NVM-07); a lower bound for a period other than 0 (longer than the longest flash write) is only documented in the parameter, not checked by `tools/nxconfig` |

#### SM-15 — ECC double-bit error, RAM

| Field | Content |
|---|---|
| Faults considered | an uncorrectable (double-bit) error in SRAM |
| Detection | `ERM->SR0` non-correctable bits (NCE0, NCE1 where present) polled every safety cycle; since SYS-41 through `safe_memory::takeEccErrors()`, which reads `ERM_SR0` and `FTFC_FERSTAT[DFDIF]` once, keeps the address of the RAM error (`EARn`) and clears **exactly the flags it read** (write 1 to clear) **before** the report. The reference manual (S32K1xx RM Rev. 13, 22.4.1/22.4.2) confirms that the ERM records non-correctable and single-bit events in `SR0` **independently** of the enables in `ERM->CR0` (which only route them to an interrupt), so the poll detects without neXtBSW setting `CR0`. What the read itself does differs per core (S32K1xx RM Rev. 13, Table 31-5; SYS-73 corrected the earlier expectation of a bus fault): on the S32K14x the core's read completes without a fault exception (only the ERM interrupt, if enabled), on the S32K11x it ends in a hard fault (→ SM-17) — the poll is the route the concept relies on |
| Reaction | `INTEGRITY_EVENT_DMA_ECC_ERROR` → S1 + S2; since SYS-41 **with the source in the context word** (bit 0 RAM channel 0, bit 1 RAM channel 1, bit 2 flash) and the RAM address in the log. The event is still shared with SM-16 and keeps its name (the documents name it), although it is RAM and flash, not DMA |
| Times | nominal 10 ms, guaranteed 31.25 ms after the faulty **read**; a cell that is not read is not checked at all |
| Diagnostic coverage | **medium** for errors in memory that is read: ECC with single-error correction and double-error detection flags every one- and two-bit error in a word; errors of more bits are not guaranteed. Blind spots: memory that is not read (no scrubbing — latent). Start-up RAM test: **decided not** in SYS-41 — the startup writes the whole SRAM (ECC initialisation); a March test would be a task of its own. Single-bit corrections are counted since SYS-41 (`eccCorrectionCount()`) and cleared without a reaction, so a degrading cell is visible in the diagnostics, though not reported as an event |
| Latent faults of the mechanism | F11 closed by SYS-41: (a) the flags are cleared before the report, so the reaction (reset) starts clean whatever the reset does with the ERM — no reset loop, and a second error is reported again (no masking); an event between reading and clearing survives, because only the read flags are cleared; (b) `SR0` records without `CR0` (reference manual, above). A decoder or an ERM record that stopped working would stay latent until the error it should catch — since SYS-73 tested at every start by SM-55 |
| Evidence | `safe-memory-ecc` (SYS-41: real `IntegrityEccHandler.cpp` and `safe_memory::cyclic` against ERM and FTFC register models with write-1-to-clear; mutants: ERM flags not cleared, all ERM flags cleared, flash flag not cleared, no context); reaction `safe-supervisor` |
| Residual / action | board check B5 (for the record only — the behaviour no longer depends on the reset behaviour of the ERM): run 2026-09-24, `ERM` registers 0 before and after `safety wdg` - it shows only that no flag survives a reset; the detection with an injected error is SM-55's |

#### SM-16 — ECC double-bit error, flash

| Field | Content |
|---|---|
| Faults considered | an uncorrectable error on a program-flash read |
| Detection | `FTFC->FERSTAT[DFDIF]` polled every safety cycle; since SYS-41 read and cleared (write 1) by `takeEccErrors()` before the report, like SM-15 |
| Reaction | same event as SM-15 → S1 + S2, context bit 2 (flash) since SYS-41 |
| Times | as SM-15 |
| Diagnostic coverage | **medium**, same reasoning as SM-15; for the program flash SM-14 adds a complete check at every start and, since SYS-44, once per re-check period |
| Latent faults of the mechanism | as SM-15 (flag cleared before the report since SYS-41; F11 closed) |
| Evidence | `safe-memory-ecc` (flash flag with context, cleared, second error reported again); reaction `safe-supervisor` |
| Residual / action | — (F11, F15 closed for this mechanism by SYS-41); the latent faults of the flash ECC itself are not tested (SM-55) |

#### SM-55 — Latent-fault test of the RAM ECC

| Field | Content |
|---|---|
| Faults considered | the SEC-DED decoder of an SRAM array no longer corrects or no longer detects, or the ERM no longer records its events or records a wrong address — a latent fault that disables SM-15 without any symptom |
| Detection | since SYS-73 once per start in `main()`, after the watchdog self-test and the reset-loop limit and before the BSP (no DMA, no interrupt, no task): `safe_memory::eccLatentTestAtStart()`. Per ECC channel of the derivative (ERM/EIM channel = one SRAM array, S32K1xx RM Rev. 13 Tables 21-1, 22-1: S32K14x SRAM_L and SRAM_U, S32K11x SRAM_U) one reserved word of the image in that array (`nxEccLatentTestWordFirst` in the hot bss, `nxEccLatentTestWordLast` in the no-init RAM; the address decides the channel, a channel without a word fails). The word gets a pattern (fresh check bits); the EIM inverts bits only on the read path. (1) one data bit inverted: the value read must be the pattern, the ERM must record a single-bit correction of that channel and no non-correctable event, `EARn` = the word; (2) two data bits: a non-correctable event with the word's address (S32K14x only — the Cortex-M0+ takes such a read as a hard fault). Then the descriptor back to 0, a plain read must return the pattern without a record. Precondition: EIM off and no record of the channel — a real record stays for the SM-15 poll and the test does not pass. The injected read is one register-only sequence with all interrupts and faults masked (Cortex-M4: `FAULTMASK` plus `CCR.BFHFNMIGN`; M0+: `PRIMASK`); read-backs and barriers put the enable before and the disable after the read. `ERM_CR0` of the channel is off during the test and restored after the record was cleared, so neither the ERM interrupt nor the cyclic poll ever sees the test's own events |
| Reaction | `INTEGRITY_EVENT_DMA_ECC_ERROR` with context `ECC_SOURCE_LATENT_TEST` (bit 3), failed checks in bits 17..8, channel in bits 27..24 → S1 + S2; the reset counts for the reset-loop limit, so a permanent fault ends in S1 held after the configured number of starts (SM-50/SSR-065). Pass: console line `RAM ECC latent test (EIM) passed, n channel(s)` |
| Times | at start; about 40 register accesses per channel, estimated below 50 µs for two channels at 80/40 MHz (not measured; the console line of the pass takes longer, about 4 ms at 115200 baud). Latent interval = the time between two starts (AoU-25 applies as for SM-07) |
| Diagnostic coverage | **high** for the decoder and record paths exercised: data-bit correction, double-bit detection and the ERM's flag and address per array. Not exercised: the check-bit inputs of the decoder, every other data-bit position, and the S32K11x double-bit detection (a DMA read would be needed). The flash ECC has **no injection module**: without erasing or programming only the reporting path could be exercised (`FTFC_FERCNFG[FDFD]` forces `FERSTAT[DFDIF]`, reference manual 36.4.4.1.12) — not implemented, because it sets the flag on every flash read while active and its side effects on instruction fetch are not confirmed on the board; the decoder itself needs a flash word whose check bits do not match its data, and the flash controller generates the check bits when it programs - such a word can only come from erasing and programming (for example a deliberately interrupted or repeated programming of one phrase), which the start-up test must not do. Flash latent faults stay open (SYS-73 remainder, MCU safety manual) |
| Latent faults of the mechanism | the test itself runs once per start; an EIM that does not switch off is checked after every injected read and switched off; an injected read that is not register-only is checked on the linked image |
| Evidence | `safe-memory-ecc-latent`, `safe-memory-ecc-latent-s32k11x` (real `EccLatentTest.cpp` against EIM/ERM register models, S32K14x and S32K11x layout; every modelled hardware fault found with its check and channel, EIM off after every run, `CR0` masked and restored, precondition, coverage), `safe-memory-ecc-latent-mutations` (25 mutants), `safe-memory-ecc-latent-elf` (placement of the words, register-only window) |
| Residual / action | board (issue 2): 2026-09-29 the start line `RAM ECC latent test (EIM) passed, 2 channel(s)` at every start, 16 min without a reset; 2026-09-30 an injected fault gives the reaction (cause `0x107`; the missing `SAFETY RESET` line became SYS-137, fixed and board-passed the same evening); suite case BT-START-01 2026-10-02 (`ecc_channels=2`). Open: flash ECC latent faults; the MCU safety manual's software measures (SYS-73 remainder, N2, SYS-131) |

#### SM-17 — Fault handlers with dump

| Field | Content |
|---|---|
| Faults considered | any fault exception: bus fault (including MPU violations of SM-10 and ECC errors on access), usage fault, hard fault, watchdog warning interrupt |
| Detection | the core's exceptions |
| Reaction | **first** the safe outputs walk from assembler (R0–R3 only, no stack, no RAM), then the dump, then the reset. **SYS-97:** before the C part, `HardFault_Handler_Final` (naked) compares the MSP with `__StackLimit`; below it (overflow into the guard of SM-10) the MSP is recorded and moved back to `__StackTop`, and `nxIsrStackOverflowFinal` writes `HARD FAULT RESET: interrupt stack overflow (guard), outputs off` before the reset - otherwise the first push of the C part would fault again inside the handler. **SYS-169:** the watchdog's pre-reset interrupt (vector 38 on S32K1) takes the same path; `nxHardFaultFinal` reads the exception number of a valid dump and writes `WATCHDOG RESET: outputs off, dump saved` for it, every other record keeps `HARD FAULT RESET: ...` |
| Times | exception entry + walk (< 1 µs computed). **Minimum FTTI served: exception latency** |
| Diagnostic coverage | **high** for faults that raise an exception. A fault inside the handler before the walk would lock up the core; the lockup ends in a reset whose pin state is AoU-07's |
| Latent faults of the mechanism | the handler code and the table are in flash (SM-14 at start, SM-16) |
| Evidence | `safe-outputs-order` (disassembly of both architectures and of the demo ELF: table walked before dump and output, vectors 3 and 38 and the LPIT vector), `hard-fault-dump`; board 2026-09-18 (watchdog warning path) and 2026-09-21 (SYS-05 finding: HardFault → reset on the target). SYS-97: `kernel-fault-reaction` (overflow line with and without transmitter, ordinary line without record), `freertos-config-elf` (`HardFault_Handler_Final` without stack use, branches to both C parts, each with two flushes, one line, the reset), `hold-console-elf` (rule 5 for `nxIsrStackOverflowFinal`); board 2026-09-30 and 2026-10-03 (SYS-97: the overflow line before the reset, SM-10) |
| Residual / action | the read-back of S1 is not called in the fault handlers' path yet (G1 rest, SM-23 below, SSR-060 partial) |

#### SM-60 — Lock of the FlexNVM block

NVM-25 (decision of the safety responsible in the safety review of the night 2026-10-01, point NVM12-S1 b: one lock
for the NvM and the readers). **Proposed, not credited** (as SM-58 and SM-59): it claims nothing until the safety
responsible decides. The FlexNVM block does one thing at a time; a data-flash read during an erase, a program, an
EEPROM store of the NvM or a CSEc command returns undefined data ([RM13] 36.5.7). The hardware shows a running command
(`CCIF` 0) and a running store (`EEERDY` 0), but not a reader in the middle of its accesses. The family module
`platforms/s32k1xx/bsp/bspFlexNvm` (`flexnvm/FlexNvmBlockLock.h`) makes data-flash readers and EEPROM stores exclude
each other.

| Field | Content |
|---|---|
| Faults considered | a data-flash read overlaps a flash command or an EEPROM store started by another task (same priority in its time slice, or higher) and returns undefined bytes the reader takes as valid data (NvM data area, calibration segment, a map read in place). Of the mechanism itself: a reader that bypasses the lock (pointer reads outside the family port), a CSEc command that ignores a hold, a hold that is never released, an unbalanced release |
| Detection | not a detector, an exclusion. `read()` copies in sections of 32 bytes, each with interrupts locked and the block checked idle in the same section, so neither a store nor a command can fall into a section; a store begun between two sections that still runs ends `read()` with `BUSY`; while a `read()` runs no data-flash command starts. A read in place takes a hold (`IDataFlash::holdForDirectRead()`, RAII `dataflash::DirectRead`), granted only on an idle block; while a hold exists no EEPROM store and no data-flash command starts. Stores never wait for a `read()` (no priority inversion through `read()`). `RDCOLERR` stays as the second line |
| Reaction | `read()` returns `BUSY` instead of undefined data (the caller retries); a store under a hold waits as for a running command, at most `COMMAND_WAIT_LIMIT_US` = 260 ms, then the write fails and the NvM reports it - it never collides; in the run-down the gate of the family port grants no new hold |
| Times | interrupt lock per section: 32 bytes from the data flash, a few µs (not measured on a board - the lock times measured on 2026-10-03 are those of SYS-172, N6, another lock); a store delayed by a hold: at most 260 ms |
| Diagnostic coverage | **not claimed.** It prevents the collision rather than detecting it, for the readers of the family port (`read()` and holds through `AreaService`) |
| Latent faults of the mechanism | an unbalanced release is counted (`unbalanced()`), not under-run; held-off launches, waits for a hold and time-outs are counted and shown in the test build (`dflash show`) |
| Evidence | `flexnvm-s32k1xx` (register model: a store between two sections ends `read()` with `BUSY`, a command start during `read()` is refused, every section with interrupts locked, a store under a hold waits and fails at the limit or stores once after the release, no hold during a running command or store, limit of 255 holds; 66 mutants), `nvm-data-flash` (admission and direct read, 26 mutants), `data-flash-system` (hold through the gate, no new hold in the run-down, 15 mutants); board 2026-10-02 with NVM-29 (B-NVM29-1 … 9 passed; B-NVM29-7/8 cover B-NVM25-2, 3 and 6; B-NVM25-1, 4, 5 and 7 are not recorded on their own; captures not in the repository) |
| Residual / action | (1) readers by pointer outside the family port do not take the hold yet (the calibration segment in the profile `production` of `memory.calibration`, an XCP upload from the data flash) - follow-up task; until then the collision of NVM12-S1 remains there; (2) CSEc commands (`bspCsec`) do not check a hold: they run with interrupts locked and check `CCIF`, so they never hit a section of `read()`, but they can hit a hold; (3) a hold taken by a task of lower priority than a task that stores into the EEPROM can make that store fail after 260 ms (reported, never colliding) - assumption of use **AoU-40**; (4) a hold that exists when the run-down starts ends only at its holder; (5) board proof |

### 3.3 MCU periphery, supply and registers (concept 6.4)

#### SM-18 — ADC reference supervision

| Field | Content |
|---|---|
| Faults considered | VREFH drifted, so every analogue measurement is wrong by the same factor |
| Detection | bandgap channel (nominal 1000 mV) read every safety cycle; VREFH computed from it; compared with `board.adc_reference_mv` ± `modules.safeSystem.adc_reference_tolerance_mv` (demo: 4096 ± 200 mV) |
| Reaction | `ADC_REFERENCE_ERROR`, context 1 → S1 + S2 |
| Times | nominal 10 ms, guaranteed 31.25 ms |
| Diagnostic coverage | **medium.** A drift beyond the band is found every cycle. Blind spots: (a) the reference of the check is the on-chip bandgap — a bandgap error shifts the computed VREFH and can mask or fake a drift; its accuracy is a data-sheet value that is not in the repository; (b) a drift **inside** the band is a silent gain error of up to 200 mV in 4096 mV on every analogue value of the ECU (demo values), which may already exceed what a safety-related measurement tolerates (F16) |
| Latent faults of the mechanism | read failures: SM-19 |
| Evidence | `safe-system` (decision logic against stubs), `bswgen` (profile `testproject_adc_reference`), `nxconfig`; no board fault injection recorded |
| Residual / action | F16 → AoU-24 (section 6; *issue 1:* proposed - binding since concept issue 3, concept 4.3, CM-07 ZG; F16 accepted, register AN-016) |

#### SM-19 — ADC read failure limit

| Field | Content |
|---|---|
| Faults considered | the bandgap conversion fails permanently, so the reference can no longer be supervised |
| Detection | consecutive failed readings counted; the event fires once at `adc_read_error_limit` (default 10) |
| Reaction | `ADC_REFERENCE_ERROR`, context 2 → S1 + S2 |
| Times | nominal 100 ms, guaranteed 312.5 ms |
| Diagnostic coverage | **high** for a permanent failure of the bandgap conversion. Intermittent failures shorter than the limit are tolerated by design |
| Latent faults of the mechanism | none needed |
| Evidence | `safe-system` (failed reading) |
| Residual / action | — |

#### SM-20 — Internal supply supervision

| Field | Content |
|---|---|
| Faults considered | the MCU's 3.3 V flash, 3.3 V oscillator or 1.2 V core supply out of range |
| Detection | SIM supply monitor multiplexed over 6 safety cycles (select, measure), ± 200 mV around the nominal value, scaled with the measured VREFH. Since SYS-41 failed readings of the supply channel are counted in a row with the same limit as the bandgap, `modules.safeSystem.adc_read_error_limit` (demo 10 cycles); a good reading ends the series, a missing reference does not count (that is SM-19) |
| Reaction | `INTERNAL_SUPPLIES_ERROR` → S1 + S2; after `adc_read_error_limit` failed readings once with context 2 ("no measurement") |
| Times | nominal 60 ms per supply, guaranteed 187.5 ms; a permanently failing supply channel: nominal 100 ms, guaranteed 312.5 ms (as SM-19) |
| Diagnostic coverage | **low to medium.** The same ± 200 mV band is narrow for the 3.3 V supplies but one sixth of the nominal value for the 1.2 V core supply, which is wide for a core supply. The value depends on the measured VREFH (common cause with SM-18). A permanently failing supply channel is reported since SYS-41 (F14 closed); intermittent failures shorter than the limit are tolerated by design. The MCU's own hardware low-voltage detection is not part of this mechanism; since SYS-125 it is switched on by the family port as the separate hardware mechanism SM-59 (not credited), which watches VDD itself |
| Latent faults of the mechanism | loss of the measurement: the read-failure limit above (diagnostic value `consecutiveSupplyErrors`, console `safeadc stats`) |
| Evidence | `safe-system` (decision logic; since SYS-41 the read-failure limit: event at the limit, series reset by a good reading, missing reference not counted — each with a mutant) |
| Residual / action | — (F14 closed by SYS-41); a failing supply channel cannot be provoked on the board |

#### SM-21 — Safety register lock

*Issue 2 (CM-02 decision Z1, gap analysis measure M7):* issue 1 analysed the OpenBSW demonstration - **one** pin locked
and read back. Since SYS-24 the lock list is **generated from the configuration** (`tools/bswgen`, class
`PIN_FUNCTION`), and the block below describes that mechanism; the issue-1 statements are kept where they still hold.

| Field | Content |
|---|---|
| Faults considered | the function of a pin the safe state relies on is changed after start: a PWM pin of a used group, a `safe_off` output or the role pin remuxed to another function or to GPIO (wild write, a driver that reconfigures it); a lock that did not take (write lost, `PCR[LK]` stuck at 0). Not considered: registers other than the pin control (that is SM-22), a pin outside the list |
| Detection | `SafeIo::init()` writes the configured function and sets `PCR[LK]` for **every entry of the lock list** - the pins of the used PWM groups, every `safe_off` output and the role pin (`safety.locked_pin`) - and reads back function (`MUX`) and lock bit of each once (since SYS-42 including `LK`) |
| Reaction | `LOCK_REGISTER_ERROR` with the pin in the context → S1 + S2 |
| Times | start-up only; a locked `PCR` cannot be changed until the next reset, so the lock **prevents** the change after the read-back rather than detecting it |
| Diagnostic coverage | **medium** (issue 1: **low** - one pin, demonstration scope F4). The whole property - the function of every pin the safe outputs table and the PWM groups use - is locked and read back, by comparison with the configuration, and a lock that did not take is reported. Known blind spots: the check runs once per start (a fault of the read-back path itself is not repeated), the lock covers the pin control only (direction, clock gates, timer and clock registers are SM-22's), and the clock of the pins' peripherals (`SYSTEM_CLOCK`, HSI H5) is in no list yet - **F4 stays open until SYS-55** (AN-004). Whether the class can become **high** is to be checked after SYS-55 |
| Latent faults of the mechanism | the lock cannot be undone by software until the next reset; the read-back of every start re-verifies it |
| Evidence | `safe-io` (SYS-42): real `SafeIo.cpp`, bspIo driver and supervisor on a PORT register model with the PCR lock — every function other than GPIO (set before, locked before, MUX bit stuck) and a lock that did not take are reported with the pin id; `safe-io-mutations` (built-in faults for SM-21/SM-22, all detected); `bswgen-safe-io` (the generated lists of every profile against an independent expectation, generator mutants); reaction `safe-supervisor`. Board: B8 passed 2026-09-22 (role pin `LK` = 1, `MUX` = 001, no `LOCK_REGISTER_ERROR` at the start); the generated list of SYS-24 on the board 2026-09-22 |
| Residual / action | F4 rest → **SYS-55** (`SYSTEM_CLOCK`); re-rating to high after SYS-55 |

#### SM-22 — Safety register check

| Field | Content |
|---|---|
| Faults considered | a safety-relevant register drifted during operation: pin function (class `PIN_FUNCTION`), direction of a `safe_off` GPIO (`GPIO_DIRECTION`), clock gate of a timer or port in the safe outputs table (`CLOCK_GATE`), the configuration of a PWM timer (`PWM_TIMER`: the registers the PWM driver writes once - `SC`, `MOD`, `CNTIN`, `COMBINE`, `POL`, `INVCTRL`, `CONF.BDMMODE` and the dead times of the used pairs; the software output control word is the read-back of SM-23, not this list), and since SYS-153 the low-voltage reset of SM-59 (`SUPERVISION`, `PMC_LVDSC1`) |
| Detection | `SafeIo` compares **every entry of the generated check list** with its expected value under its mask **every safety cycle** (since SYS-24; 27 entries on the demo at SYS-24, plus `PMC_LVDSC1` since SYS-153); issue 1: the direction of **one** pin |
| Reaction | `CHECK_REGISTER_ERROR` with class and index in the context → S1 without reset, latched until the next `init()`; for the role pin's indicator the return releases it (AoU-30) |
| Times | nominal 10 ms, guaranteed 17 ms since SYS-40 (issue 1: 31.25 ms) |
| Diagnostic coverage | **medium** (issue 1: **low** - one bit of one register, F4). The whole register set the safe state relies on is compared with the configuration at a fixed interval. Known blind spots: the check **samples** once per safety cycle (a change and its reversal between two cycles is not seen; a changed register acts until the next cycle), and the class `SYSTEM_CLOCK` (SCG configuration, HSI H5) is missing - a wrong system clock is seen only through its effects (SM-04 against the LPO, SM-01) - **F4 stays open until SYS-55** (AN-004). Whether the class can become **high** is to be checked after SYS-55 |
| Latent faults of the mechanism | none of its own; a stopped safety task stops the check (SM-01, SM-04) |
| Evidence | `safe-io` (SYS-42: detection on a GPIO register model — output via `PDDR`, disabled input via `PIDR`, reconfiguration through the driver; once per change, the return releases the indicator, neighbouring pins silent; since SYS-24 every class of the list; since SYS-153 the byte register `PMC_LVDSC1`), `safe-io-mutations`, `bswgen-safe-io` (the generated list per profile against an independent expectation), `safe-supervisor`, `safe-state-hold` (S1 held against drivers that want to switch on); board 2026-09-21 (SYS-06: `safety dst` stops the square wave, DRV_EN and LED; SYS-36/IO-37: KL30B at 0 A for 6 s, `kl30b on` refused); board 2026-09-22 (SYS-24: the list in normal operation, no false report); board 2026-10-01 (SYS-153: `PMC_LVDSC1` written `0x00`/`0x30` gives `CHECK_REGISTER_ERROR`, S1 without reset) |
| Residual / action | F4 rest → **SYS-55** (`SYSTEM_CLOCK`; the alternative of DFA-2, `SIRCVLD`, with it). *SYS-87:* the reaction "release the indicator" switches `safety.safe_state_output` back with the level the application last wrote, and the mechanism relies on a `locked_pin` no driver reconfigures and a `test_pin` that is unconnected — assumption of use **AoU-30** (concept section 4.2); `nxconfig` warns when the application writes the indicator |

#### SM-59 — Low-voltage detect reset

SYS-125 (anomaly AN-066, finding H9 of `hsi-s32k1xx.md`, decision of the safety responsible 2026-09-30). A hardware
mechanism of the MCU that the family port s32k1xx configures: `boardInit()` writes `PMC_LVDSC1 = 0x10` (`LVDRE` on,
`LVDIE` off, `LVDACK` not written) in every start (`platforms/s32k1xx/port/main/include/pmc/LowVoltageDetectReset.h`).
**Not credited** (as SM-58): whether a hardware mechanism counts for stage 1 is the decision of the safety
responsible; coverage statements for the MCU's own hardware stay with the integrator and the MCU safety manual (N2,
AoU-09, AoU-36).

| Field | Content |
|---|---|
| Faults considered | VDD falls below the specified range (supply, wiring, a load step, a brown-out at switch-off) while the MCU keeps executing: below about 2.7 V the data sheet degrades ADC, comparator, pins and communication, so every analogue check (SM-18 … SM-20) and every output may act on wrong values. Of the mechanism itself: `LVDRE` not written (port without SYS-125, a start path that bypasses `boardInit()`), cleared later (bit flip, a write by other code), the interrupt enabled instead (the shared vector is `DefaultISR`), the LVD off in a low-power mode |
| Detection | the PMC compares VDD with VLVD (2.80 … 3.00 V falling, 50 mV hysteresis; [DS13] table 8) continuously; no instruction involved. Of the mechanism's own faults: since SYS-153 `PMC_LVDSC1` is in the SafeIo register list (class SUPERVISION, entry `PMC_LVDSC1`, mask `0x30`, expected `0x10`, 8-bit read) and compared every safety cycle (SM-22): `LVDRE` cleared or `LVDIE` set is CHECK_REGISTER_ERROR, S1 without reset, latched until the next `init()`; `LVDF` is not judged |
| Reaction | hardware reset with `RCM_SRS[LVD]`; the MCU is held in reset until VDD is above VLVD plus the hysteresis ([RM13] 25.2.2.2); all pins go to their reset state (AoU-07). The following start counts as power-on: the dump and the reset-loop record are cleared (SM-50, PLAT-20) |
| Times | the response time of the comparator (hardware, not in the data sheet table); no software time |
| Diagnostic coverage | **not claimed.** Below VLVD the whole MCU is reset, which also ends every software mechanism; between the internal regulators' drop-out and VLVD SM-20 and SM-18 still act (a 5 V board); on a 3.3 V board VLVD lies at most 0.3 V below the nominal supply (AoU-39). Not covered: an over-voltage, a supply in the low-power modes VLPR/VLPS (LVD off, only the LVR), a drop so short that the comparator does not respond |
| Latent faults of the mechanism | the reset path is never exercised in normal operation; a lost `LVDRE` or a set `LVDIE` is detected within one safety cycle (10 ms nominal, 17 ms guaranteed) by SM-22 (SYS-153) |
| Evidence | `low-voltage-detect-reset` (host: the real function on a register model of `PMC_LVDSC1` with the supply thresholds at both ends of VLVD - power-on, brown-out to 2.79 V resets and holds, 3.01 V does not, the port without SYS-125 keeps running at 2.71 V, software and watchdog resets without a follow-up reset, 3.3 V board at -5 % and -10 %), `low-voltage-detect-reset-mutations` (9 mutants of the header), `low-voltage-detect-reset-elf` (target: `boardInit()` of the demo stores the byte `0x10` to `0x4007D000` right after the watchdog is disabled, `Reset_Handler` calls it), `safe-io` / `safe-io-mutations` (SYS-153: byte register model, all 256 values against `lowVoltageDetectResetOn()`, 8 width mutants), `bswgen-safe-io` (entry in every profile, 7 generator mutants); board: `PMC_LVDSC1` reads `0x10` after the start (J-Link 2026-10-02, also halted in the bootloader 2026-10-03, BL-34), a written `0x00` or `0x30` gives `CHECK_REGISTER_ERROR` (SYS-153, 2026-10-01), power-on starts show `POR+LVD`; the reset at a falling lab supply is **not recorded** |
| Residual / action | (1) board proof with a lab supply (checklist in the changelog of SYS-125) - not recorded yet; carried by **SYS-198** after Release R1, a known gap of Release R1 (CM-02 re-check BRD a, 2026-10-04); (2) closed by SYS-153 (cyclic re-check), on the board 2026-10-01; (3) a **brown-out loop** (the supply sags under load after each start) resets over and over without the reset-loop limit (SM-50) counting it, because a start after LVD clears the record - as before with the LVR, now already below VLVD; (4) the bootloader runs after power-on with `LVDRE = 0` until the application starts (the bootloader is not changed by SYS-125); (5) the S32K14xW derivatives specify no VLVD (data sheet table 9); they are not in the derivative database, and adding one needs a decision whether the write stays |

### 3.4 Outputs and the safe state (concept 6.5)

SM-23 … SM-25 are **reactions**. They are judged by whether they take effect, not by coverage. SM-58 (SYS-86) is
a reaction with a detection half of its own and is not credited.

#### SM-23 — Safe outputs table, one list for three paths

| Field | Content |
|---|---|
| Faults considered (of the mechanism) | an output that should be de-energised is not in the table (AoU-06, SM-24); a table entry corrupted (flash: SM-14, SM-16); a peripheral with gated clock is skipped by design — if a fault gated the clock of an FTM while its outputs were active, the walk leaves those pins at whatever level the module leaves them; the walk wrote but the pin did not follow (G1) |
| Reaction | PWM groups to their inactive level via `SWOCTRL`, `safe_off` outputs inactive, `dsb` |
| Times | < 1 µs, **computed** (F8); on the board the walk from the timer to the gate-driver enable is bounded at **≤ 21 µs** (B3, oscilloscope 2026-09-22, 8.1), which does not resolve the computed value |
| Effectiveness | **covered with limits**: works for every caller, verified on the board for the demo's two outputs; since SYS-30 SafeIo reads back every entry (software output control, pin level of every `safe_off` output) in every safety cycle while S1 is latched and resets (S1 + S2, `LOCK_REGISTER_ERROR`, class `SAFE_OUTPUT`) if S1 did not take effect - host only |
| Evidence | `safe-state`, `safe-state-hold`, `debug-halt-guard`, `safe-outputs-order`, `safe-io` and `safe-io-mutations` (read-back, SYS-30: 9 mutants), `bswgen-safe-readback`; board 2026-09-21 (SYS-06, SYS-36, IO-37, IO-32) |
| Residual / action | *Issue 2:* S1 held, the groups of SD and the S1 + S2 sequence are read back since SYS-138 (the block below); G1 rest: the fault handlers' and the halt deadman's path, board proof B15/B16 (B15 run ad hoc in the board session of 2026-09-30, `doc/todo.yaml` SYS-30, captures in the repository since DOC-59 (8.2) - to be repeated with records as suite case BT-S1-03, which is on the board list of Release R1; B16 not run: (b) with BT-S1-03, (a), (c), (d) after Release R1 with SYS-198, a known gap of Release R1, CM-02 re-check BRD a). *Issue 1 text:* G1 rest (SYS-30 covers S1 only): no read-back in S1 held (no task runs; the hold re-walks every round), for the groups of SD, in the fault handlers' and the halt deadman's path; an entry whose clock is gated is not judged (the pin level of a clock-gated FTM stays unknown); with the input of a pin disabled (reported by SM-22 itself) its level reads 0; board proof B15. F8 |

**SYS-138 — the rest of G1 after SYS-30** (decision of the safety responsible 2026-09-30, review SYS-116/SYS-30 point
K6; K5: the reaction stays the reset through `LOCK_REGISTER_ERROR`). The read-back of SYS-30 is now one function of the
family port (`safeSupervisor/SafeStateReadback.h`, S32K1: `SafeIo.cpp`) that the safe supervisor uses as well:

| Path | Before SYS-138 | Since SYS-138 (host verified, board open: B16) |
|---|---|---|
| S1 latched, safety task running | read back every safety cycle (SYS-30) | unchanged, but a **clock-gated entry is not confirmed** (see below) |
| S1 held (SYS-23: watchdog self-test, ROM check, reset-loop limit, device fault IDENTITY) | not read back | read back in **every round of the hold loop, before its walk** (what the previous walk left). Not confirmed: `LOCK_REGISTER_ERROR` (class `SAFE_OUTPUT`, entry), S1 + S2. The hold loop is the only code that runs (interrupts suspended), so neither the halt deadman nor the idle context can carry the read-back |
| degraded groups SD (SYS-117/SYS-135) | not read back (no S1 latch) | read back in every safety cycle before their walk, only their entries (`holdDegradedGroups()`); not confirmed: `LOCK_REGISTER_ERROR`, S1 + S2. Every group degraded is S1 with its latch: SafeIo |
| S1 + S2 of the supervisor (every resetting event, kernel faults, `ETL_ASSERT`, ECC error from a fault interrupt) | walk, record, console line, reset | walk, record, **read-back**; not confirmed: **no console line**, the reset at once (the line costs up to two flush time-outs plus its transmission, SYS-59, while an output may stay energised); the cause stays in the reset-loop record |
| hard fault / watchdog handlers (`customHardFaultHandler`, port `isr_sys.cpp`), halt deadman (`LpitHaltDeadman.h`) | not read back | **open** - outside the paths of SYS-138 (`platforms/s32k1xx/hardFaultHandler`, `port/main`); the function exists and needs neither stack nor RAM beyond its frame, a call in `nxHardFaultFinal()` before the line is the follow-up. The hard fault path ends in the reset anyway; the deadman path returns and has no latch |

Entries whose clock is gated: up to SYS-138 not judged. **Now not confirmed** once `SafeIo::init()` has run: a timer
whose clock is gated keeps its outputs frozen where the walk cannot reach them, and the register list of SYS-24
expects every such gate on (`SafeIoRegisterList.cpp` checks at compile time that every gate of the read-back list
is a `CLOCK_GATE` entry of the register list expected on). A gated gate after `init()` was already a register fault
(SM-22, S1 without reset); with S1 latched it now ends in S1 + S2, whose reset puts the pins into their reset state.
Before `SafeIo::init()` (the hold from `main()` before the BSP start, a fault before the safety task) nothing is
judged: the pins are in their reset configuration, pin function disabled, driven by nothing - and an active-low
`safe_off` output would read 0 with its input disabled, a false finding. An active short selected for a group
(SYS-116/SYS-139, part C of `partner-and-degraded.md`) is a safe state in every path, as in SYS-30.

Residual after SYS-138: the hold of S1 before `SafeIo::init()` and every path before it are not read back (reset
configuration of the pins, see above); the fault handlers' and the halt deadman's path (see table); a read-back that
follows its walk by only a few bus clocks (S1 + S2 sequence) can see a pin level not yet synchronised - it then costs
the console line, never the reset; a reset loop of an S1 that never takes effect ends in the reset-loop limit (SYS-43),
whose hold then resets again while the fault persists - the pins are in their reset state for most of that loop.
Evidence: `safe-io` (real SafeIo and SafeSupervisor on the register model), `safe-io-mutations` (7 new mutants),
`safe-supervisor-degraded` (SD, hold and S1 + S2 against a faked port) and `safe-supervisor-degraded-mutations`
(9 new mutants), `hold-console-elf` on the demo image. Board check B16 (section 8).

**SYS-117 — the degraded reaction SD uses a subset of this table** (concept section 3, SSR-087, SSR-088). SD is a
reaction, not a mechanism of its own: it walks the entries of the mapped safe-output groups (an entry mask derived by
`nxconfig`, the table unchanged, the walk of `nxSafeOutputsSwitchOff()` with a mask in
`SafeState::enterDegradedState()`), latches the groups and keeps them until the power cycle. The fault handlers and
the halt deadman keep walking the whole table: they never degrade.

| Field | Content |
|---|---|
| Faults considered | a fail-safe event degraded (an MCU-internal fault, a wrong part, a malformed or inconsistent report); a group released before the power cycle (a later event, leaving S1, a driver or the debug halt guard rewriting the timer, a reset); the latch or the record corrupted (wild write); the mapping and the table drifting apart; the last working group degraded while the ECU still claims to run |
| Detection | not a detector. Guards: the closed list is a code fact, refused by `nxconfig` with the YAML path and by a `static_assert` per generated mapping; the report must be well-formed, not `IDENTITY` and name the configured bus; latch with inverted copy, record with key and check word |
| Reaction | only the mapped groups to their safe state, no reset; walked again every safety cycle; record re-applied at every start without power-on; implausible latch or record = every group; last group = S1 without reset |
| Times | the walk as S1 for fewer entries; the report to the application within one period of the reading task |
| Effectiveness | **covered with limits** for the PWM channels of a group; see residual |
| Evidence | `safe-supervisor-degraded` (fault injection: corrupted latch and record, lost bus, wrong bus, `IDENTITY`, malformed word, escalation, starts with and without power-on), `safe-supervisor-degraded-mutations` (21 mutants detected, 8 fail-safe configurations refused by the compiler), `safe-state-degraded` (subset walk against the full walk), `nxconfig-degraded`, `rtegen-safety`; no board run |
| Residual / action | (1) **Closed by IO-45 for a bound enable:** the `safe_off` output of a device bound to the group's PWM group (board `pwm[].gate_driver`) is an entry of the group (`nxconfig` derives it in the order of the `bswgen` table, `nxconfig-degraded` compares both), SD pulls it low with the channels, and its driver is locked while the group is degraded (`gateDriverGroupLock`, `SafeSupervisor::isEntryDegraded`: enable refused, no mismatch fault). An enable of a device bound to no PWM group still goes off with S1 alone (`nxconfig` warns); a gate driver bound to the PWM group of a project always joins, also with another number of pairs than phases (then only the enable rule of the driver is not applied). (2) **Closed by SYS-123:** after a safety reset the groups are forced before the BSP starts; the PWM driver's initialisation writes the output control of the timer from its configuration (0) - since SYS-123 the driver of a locked group (`ComplementaryPwm`, `NX_PWM_GROUP_LOCK`, lock = `SafeSupervisor::isEntryDegraded` of the group's table entry) forces it again inside `init()`, before the counter starts and before a pin is muxed to the timer, and `main()` of the family port walks the degraded groups again right after the BSP start (`applyDegradedGroups()`). Every duty write of a locked group (RTE `pwm.<pair>`, console `pwm set`) is refused and forces the output control again; the fast path (`FtmPwmGroup::writeDutiesQ15`) writes channel values and the software trigger only and cannot release it. (3) **Closed by SYS-123:** the debug halt guard's release (`FtmPwmInhibit::release`) keeps a locked group forced; a gate-driver enable of a degraded group is refused anyway (IO-45). Remaining, stated: the lock reads the supervisor's latch in protected RAM - a wild write that makes it implausible reads as every group degraded (safe direction); the output control register itself can still be rewritten by a wild write, which the hold of the next safety cycle corrects (at most one safety cycle, as before for any register of the table). Evidence: `pwm-group-lock` (register model, 6 mutants), `safe-supervisor-degraded` (`applyDegradedGroups`, entry lock), demo object code unchanged. (4) The record lies in ordinary no-init RAM like the reset-loop record: a wild write of a valid record with more groups degrades more (safe direction), with fewer is corrected from the latch within one cycle. (5) Independence of the groups beyond timer and bus (supply, sensor, connector) is the integrator's, AoU-32. (6) Board run open |

**Safety reviews of 2026-09-30 (user as safety responsible).**

- **SYS-117** (`review-sys117`): R1 … R12 as recommended. R2 — a mapping that covers every group gives only a warning
  (escalation to S1 without reset): *the warning is enough*. R4 — the window of at most one safety cycle was accepted
  for the time being with a follow-up task (closed since by SYS-123, residuals (2), (3)). R12 — the residuals (1) … (6)
  above are **accepted**.
- **IO-45 / SYS-123** (`review-io45`): G1 … G7 accepted. G1 "all three phases off" counts as allowed (11 of 12 runs
  without fault, one outlier 0x0800 caught by the short-circuit shut-off of IO-44). G2 the rule acts at the enable
  only; a later change of the duties into a mixture is protected only by the device's short detection — whether FOC
  needs more is decided in IO-61 … IO-66. G3 a number of pairs other than the number of phases stays a warning
  (*the warning is enough*). G4 a project with an integrated gate driver that does not use its group gets neither a
  warning nor a source (behaviour as before IO-44). G5 decision C2 (a) in its freewheel form only; the active short is
  SYS-116. G6 the lock reads the supervisor's latch; an implausible latch counts as every group locked, a wild write
  into `SWOCTRL` is corrected by the next safety cycle or duty write. G7 one PWM group per project (limit of the family
  port today): every degradation becomes S1 without reset, the group lock takes real effect only with several groups.
  **Open:** G8 — when a read-back from `CnV` is valid is not checked against the reference manual (documented: the
  answer leads the output by at most one period), to be clarified on the board or in the manual; G9 — board proof
  B-IO44-5 (with the user, 24 V power stage, test build) at the next board session.

#### SM-24 — One adapter per `safe_off` output, enforced at build time

| Field | Content |
|---|---|
| Faults considered | a new power output is declared `safe_off`, but no driver adapter exists, so the driver keeps believing it is on |
| Detection | `static_assert` over `NX_SAFE_OFF_OUTPUT_COUNT` (bswgen) against the adapters in `Integration.cpp` |
| Reaction | build error |
| Effectiveness | **high** for declared outputs; nothing for undeclared ones (AoU-06) |
| Evidence | `bswgen` (profile `testboard_safe_outputs`), the target build `target-demo` |
| Residual / action | — |

#### SM-25 — De-energise before the reset

| Field | Content |
|---|---|
| Faults considered | a bridge keeps switching in the window between the event and the reset |
| Reaction | `enterSafeState()` before `softwareSystemReset()` for every resetting event |
| Effectiveness | **high** within `handle()` |
| Evidence | `safe-supervisor` (every resetting event switches off before the reset) |
| Residual / action | — |

#### SM-26 — Selectable safe state of a PWM group with fallback to freewheel

SYS-116, optional per project (`pwm.<group>.safe_states`), concept and decisions:
[`partner-and-degraded.md`](partner-and-degraded.md) part C. Like SM-23 a reaction, not a detector: it changes
*which* values the one table walk writes for the entries of a group, in every path that walks it. *Issue 1:* refused
by `nxconfig` until two follow-ups exist (the supervisor's bridge-fault rule C4 and the family port's hold of the short
C2). *Issue 2:* both exist since SYS-140 and SYS-139 (the updates below), `nxconfig` accepts a short, and the start
test is called from the port since SYS-141/SYS-143 (SM-27). **Not credited for Release R1** (catalogue `CREDITED: no`,
CM-07 decision ZD): no class is claimed; CM-02 checked the marking only (decision Z4). The interrupt locks of SYS-172
around check and write of the selection and the lock of the short were measured on the board on 2026-10-03, all
**below 5 µs** (N6).

| Field | Content |
|---|---|
| Faults considered (of the mechanism) | selection word corrupted (wild write, bit flip) or stale; the pattern of a short the group does not allow written; timer clock gated while a short is selected (the timer entry is skipped - the enable must not stay on alone); a short applied onto a broken switch (shoot-through through one leg); the short pattern not dead-time protected on its way in; the gate-driver enable of the group switched off by another path (the short turns into freewheel); a reset while a short is selected; the walks with and without selection writing different registers; a project without a short compiled differently than before |
| Detection | not a detector. Guards: the word must hold **exactly** one of two 32-bit patterns (16 bits set, complements of each other; 0 after reset = freewheel); the short slot of a state the group does not allow holds the freewheel value (compile-time check against the allowed mask); the group's timer clock gate is read before the word; the extended table is checked at compile time entry by entry against `nxSafeOutputs` (same registers, same order, same freewheel values); the service writes only allowed, not withdrawn states |
| Reaction | per entry of a group: the selected short's value (timer: all channels forced, `CHnOCV` of one side; bound enable: a store of 0 to its set/clear register, i.e. left as it is) or the freewheel value; entries of no group as SM-23 |
| Times | the walk of SM-23 plus, per entry of a group, one read of the timer's clock gate, one read of the word and up to two compares — **computed, not measured**. The short lasts until the next reset; then the board's reset state (AoU-33) |
| Effectiveness | **not claimed**: host and disassembly verified only; accepted by `nxconfig` since SYS-139/SYS-140 (head paragraph; *issue 1:* refused in every project until the follow-ups); board proof open |
| Evidence | `safe-state-select` (C walk, S1, SD, service and start test on a register model with two groups: 149 checks, 16 mutants; both assembler handlers linked with the target-compiled table and **executed** by an interpreter of their Thumb instructions for 8 scenarios, 8 mutants per architecture; the prologue rules of `safe-outputs-order` for the extended walk), `bswgen-safe-state` (generated table against an independent expectation from board.yaml, 5 generator mutants, target layout for Cortex-M4 and M0+), `nxconfig-safestate` (every check with YAML path, code facts); object code of the demo and its product build unchanged (changelog SYS-116) |
| Residual / action | *Issue 2:* (1) and (2) below are the state of issue 1 - both follow-ups exist since SYS-140 and SYS-139 (head paragraph), and `nxconfig` accepts a short. (1) **C4 not in the supervisor yet**: a `DEVICE_FAULT` of the classes `OUTPUT`, `SUPPLY`, `INTERNAL` of the group's gate driver must withdraw the short (`nxSafeStateBlockShort`) before `handle()` walks the table; follow-up in `ecu/safety/safeSupervisor`, which then defines `NX_SAFE_BRIDGE_FAULT_CLASSES`. (2) **C2 not in the family port yet**: the `safe_off` adapter of a shorted group's enable must accept "on" in S1 while the short is selected, the halt deadman's guard must not take the enable away nor release/overwrite the short (`FtmPwmInhibit`), and the PWM group lock (SYS-123) must write the selected value instead of freewheel; follow-up in `platforms/s32k1xx/port/main` and `port/bspConfiguration`, which then defines `NX_SAFE_STATE_SHORT_HOLD`. Until both exist `nxconfig` refuses the short, so neither gap is reachable. (3) The output mask of the timer (`OUTMASK`) acts after the software output control: a group whose driver has masked its outputs (before the first duty, `disableOutputs`) stays in freewheel even with a short selected (safe direction; whether the walk should unmask is a question for the follow-up). (4) The forced pattern's dead-time protection and the complementary-mode behaviour of `SWOCTRL` for one side on are taken from the reference manual's order of the output logic and are **not** confirmed on the target (PD-5; board item B12). (5) A wild write of exactly one pattern selects that short where the group allows it - the residual of the same kind as the S1 latch. (6) An uncorrectable ECC error in exactly the selection word faults again inside the handler: lockup, reset, board reset state (freewheel later). (7) The word is read per entry, not once per group: a change between the timer's and the enable's entry gives "timer short, enable off" or "timer freewheel, enable on" - both freewheel at the bridge. (8) The selection's RTE port (C6) and the reaction to a refused request (S3) are follow-ups. |

**Update SYS-140 (2026-09-30, residual (1) closed in the supervisor, host only).** `safeSupervisor/Degraded.h` defines
`NX_SAFE_BRIDGE_FAULT_CLASSES(X) X(OUTPUT) X(SUPPLY) X(INTERNAL)`; in a project with a short `SafeSupervisor::handle()`
withdraws the short (`nxSafeStateBlockShort`, contract `safeSupervisor/SafeStateShort.h`) of the groups the device of a
`DEVICE_FAULT` of these classes is bound to - first in the case, under the interrupt lock, before the log line, SD, S1
held and S1 + S2 - and of every group for a malformed word (SSR-091). The bound devices are derived by `nxconfig`
(`bridge_fault_device_<n>`: instance << 8 | selection groups); `nxconfig` reads the class list and refuses every short
while it lacks one of the three. Evidence: `safe-supervisor-bridge-fault` (334 checks, own profile with three
selection groups, three bound devices and an SD mapping), `safe-supervisor-bridge-fault-mutations` (14 mutants),
`nxconfig-safestate`. New residuals: (9) C4 acts only for devices whose package raises `DEVICE_FAULT` (SYS-28); today's
TMC6200 driver switches its enable off by itself on its shutdown faults (freewheel at the bridge, the selection stays),
so the short stays selectable after such a fault until a `DEVICE_FAULT` comes; (10) `COMMUNICATION`, `CONFIGURATION`,
`DEVICE_RESET` and `IDENTITY` of the bound gate driver keep the short by the decision's list - a gate driver that
cannot be read or was reconfigured is not treated as a bridge fault (question for the safety review); (11) a report
only (`DEVICE_REPORT`, S3) never withdraws the short.

**Update SYS-139 (2026-09-30, residual (2) closed in the family port, host only).** `integration/SafeStateHold.h`
defines `NX_SAFE_STATE_SHORT_HOLD(X) X(gdTmc6200)`; in a project with a short (`ShortHold.h`, SSR-092): the lock of a
TMC6200 bound to the group reads "not locked" while the walks write the short **and** the driver is enabled (an enable
that was off stays off, `enable()` refused in S1), its adapter keeps DRV_EN after a debug halt and reports it held to
the hold of S1; the PWM inhibit (`FtmPwmInhibit`, on every entry into S1 and after a halt) and the group lock of
SYS-123 (`ComplementaryPwm::lockIfDegraded`) write the group's selected output control; `HwThresholdWatch` withdraws
the short on a finding or on the timer's fault latch before it forces the group (review SYS-128 C2: the hardware path
has priority). `nxconfig` refuses a short whose bound enable belongs to another module. Evidence: `short-hold` (119
checks: 10 s of driver cycles in S1 with DRV_EN and the pattern held and no DRV_EN write; driver off stays off;
withdrawal, gated clock, corrupted word give freewheel and DRV_EN off within one poll; halt; leaving S1; group lock),
`short-hold-mutations` (9 mutants); `safe-state-hold`, `debug-halt-guard`, `pwm-group-lock`, `hw-threshold-monitor`
unchanged green; demo object code unchanged. New residuals: (12) after S1 is left, or after a halt, the short stays
until the application disables and enables the gate driver again (`enable()` of an enabled driver is no explicit
enable, the guard keeps the PWM inhibited - safe direction, but an application must know it); (13) between the
hardware path's latch and the next 1 ms poll a walk of the safety task (HW_SHUTOFF, SYS-130/SYS-135) can still write
the short pattern into the output control - the fault input overrides it in hardware ([RM] 47.5.15, review SYS-128
C2) and DRV_EN stays on until the poll (to measure, B13 (c)); (14) the generic safe_off adapter of the generated
binding (ARCH-52) keeps no enable - `nxconfig` refuses such a group; (15) the Integration.cpp composition (lock per
bound instance) is covered by the target build and mirrored in the host test, not executed on the host itself.

#### SM-27 — Start-up test of the active short

SYS-116, building block of the family's handler library (`nxSafeStateShortTestBegin/End`). *Issue 1:* the family port
did not call it yet. *Issue 2:* called from `RteSystem::init()` since SYS-141, after a power-on reset only since SYS-143
(update below). **Not credited for Release R1** (CM-07 ZD, CM-02 Z4). Its interrupt lock (SYS-172) measured on the
board 2026-10-03: begin 2.72 µs, end 2.08 µs at the pin (DWT 2.2 µs and 1.5 µs; N6).

| Field | Content |
|---|---|
| Faults considered (of the mechanism) | the timer's software output control does not take the pattern (register stuck, clock off, write lost); the value before the test not restored; the test run twice or ended without a start; a failed test that leaves the short selectable |
| Detection | the pattern read back from the forcing register right after the store; the restored value read back; sequence and clock of the group's timer checked before anything is forced |
| Reaction | failure: the short is withdrawn for the power cycle and freewheel forced on the tested entries; the caller raises the event (proposed `SAFE_STATE_TEST_FAILURE`, degradable) |
| Times | at start: the configured `pwm.<group>.safe_state_test_us` plus a few register accesses |
| Effectiveness | **not claimed**: register level only - it proves the forcing register, not the gate driver, the switches or the phase voltages; *issue 1:* no call from the port yet - called from `RteSystem::init()` since SYS-141/SYS-143 (head paragraph) |
| Evidence | `safe-state-select` (force, read back, restore; stuck register; sequence, group and clock errors; 4 mutants of the test) |
| Residual / action | (1) phase voltages near 0 V before and during the short (IO-64) and the read-back of the gate-driver enable are the caller's (SYS-30 adds the enable read-back to the table); (2) standstill is the application's statement without phase voltages (AoU-33 (d)); (3) *issue 1:* the call from the family port before the first enable of the group and the event were follow-ups - the call is in place since SYS-141/SYS-143 (head paragraph and the update below) |

**SYS-143 (safety review SYS-136/SYS-141 E2-E4, 2026-09-30, host verified):** the caller of SYS-141
(`RteSystem::init()`, `safeState/SafeStateRte.h` `startTests`) now (E4) builds the shorts **only after a power-on
reset** and keeps the outcome over other resets in a no-init record (passed groups + check word, written before it is
read after power-on); after a reset without power-on a group keeps its shorts only with an intact record that says it
passed, otherwise freewheel until power-on - this closes a gap of SYS-141: the removal after a failed test lay in `.bss`
and a warm reset gave the short back untested. (E3) The gate-driver enable bound to the group must read **off** through
its driver before the short is built and after it is taken back (`ENABLE_ON` otherwise): the test is register level
and does not reach the bridge; residual (1) for the enable is closed for a group with a bound gate driver, the phase
voltages stay with IO-64. (E2) The hold ends when the microsecond clock stands still for 100 000 reads
(`CLOCK_STUCK`); `tools/rtegen` bounds the sum of all holds by a third of the MCU watchdog time-out. Faults considered
in addition: a test after a reset at a turning machine, a removal lost by a warm reset, an enable on during the test, a
clock that does not run, a hold that delays the lower tasks without bound. Evidence: `rtegen-safe-state` (the glue on
the real service: enable on before and during the hold, stuck clock, power-on then warm reset, failed test kept over a
warm reset, corrupted record, low-voltage reset; 11 further mutants of the glue, all detected; generator: reader in
the row, budget with YAML path), `target-active-short`. Residual: (4) a family port whose reset reason cannot tell
power-on gives freewheel only (fail-safe, availability); (5) the removal of a short by a bridge fault (SYS-140) or by
the hardware threshold path lies in `.bss` of the service and is still lost by a warm reset - the fault itself is
seen again only if it persists; (6) AoU-38 (standstill at power-on, enable of a group without bound gate driver,
budget of the lower tasks, availability after a reset).

#### SM-58 — Hardware threshold without the core

SYS-86, optional per project (`hw_thresholds`), **not credited** (whether a hardware path counts for stage 1 is
SYS-20's decision, `hardware-mechanisms.md` section 3). Unlike SM-23 … SM-25 it has a detection half, and that half
is on the MCU: the comparator. Concept and limits: `hardware-mechanisms.md` 2.4.

| Field | Content |
|---|---|
| Faults considered (of the mechanism) | threshold derived wrongly (characteristic inverted wrongly, rounding to the late side, wrong reference); comparator or DAC switched off or reconfigured by software (a disabled comparator reads its `INVT` bit: for an `above` threshold "not exceeded"); route broken (TRGMUX selection, fault source select, fault control, `FAULTEN`, polarity); comparator output stuck; the FTM not clocked; a threshold already exceeded at start; the fault latched but unknown to the software |
| Detection | the comparator against its DAC level, continuous, no instruction involved. Of the mechanism's own faults: derivation checked by `nxconfig` (inverse of the linear characteristic, rounding to the safe side, range of the reference) and by `static_assert` of the generat against the FTM configuration; the registers of the path are in the SafeIo list (SM-22, one safety cycle); the TRGMUX selection is locked until reset. **Not detected:** a stuck comparator output, a stopped FTM clock (outside SM-22's clock-gate entry), a start into an exceeded threshold (not verified, `hardware-mechanisms.md` 5 point 6) |
| Reaction | every channel of the group's pairs to `POL` (inactive, freewheel), over `SWOCTRL` as well; latched until reset (`FAULTM` 10b, manual clearing without a clearing service) |
| Times | comparator ≤ 500 ns ([DS13] Table 44) plus 3 FTM clocks, 4 + filter clocks with the glitch filter ([RM13] Table 47-15); profile 64 MHz, 200 ns filter: 766 ns. **Computed, not measured** |
| Effectiveness | **not claimed**: no board run, the path is never exercised (latent, point 3 of `hardware-mechanisms.md` 5), and the band of the real threshold includes the supply tolerance only when the bandgap is the reference |
| Evidence | `nxconfig-hwthreshold` (derivation against the hand calculation, every refusal with YAML path), `bswgen-hwthreshold` (real PWM driver with the generat on a register model: order of the steps, lock, a model of the fault path after the manual switching exactly at the DAC level; mutants in family code, driver, templates, generator and rounding), `target-hwthreshold` (target build of a demo copy with a synthetic comparator input); byte-identical images of the projects without a threshold (changelog SYS-86); no board run |
| Residual / action | (1) board proof with a signal at a comparator input (the eval board has none; `PTA0` through J11 pin 5 is the candidate, a setup change by the user); (2) start into an exceeded threshold and the output buffers under fault control (`hardware-mechanisms.md` 5 points 6, 7; AN-119, AN-120, to be clarified on the board); (3) a latent-fault test of the path (for example: gate driver disabled, outputs masked, DAC moved across the signal, `FAULTF` read, cleared) - not built, follow-up **SYS-129**; (4) the latched fault is not reported to the supervisor or the application, follow-up **SYS-130**; (5) the S32K11x is not described (one fault input of FTM0, which one unknown); (6) a switched-off or stuck comparator (below), follow-up **SYS-128** |

**Safety review SYS-86 (user as safety responsible, 2026-09-30, `review-sys86`).** H1 manual reset only — the
shut-off holds until a reset, no clearing service at run time: *agreed*. H2 rounding to the safe side — the threshold
moves by up to 1 LSB (19.5 mV at VDDA 5 V), the tolerance of the supply reference is named, not included in the band:
*accepted*. H3 SM-58 in the catalogue, **not credited** — effectiveness is not claimed, crediting is SYS-20's
decision: *agreed* (the trace check treats the row as not credited, DOC-38). H4 reaction time computed only (766 ns
in the profile, tDHSS 500 ns taken conservatively): *accepted until measured on the board*. H5 a switched-off
comparator reads "not exceeded" for `above`, only the SM-22 register check finds the loss (one safety cycle), and a
stuck output is not detected at all: **residual accepted, detection as follow-up SYS-128**. H6 latent fault — the
path is never exercised, a start test is only proposed: **start test as follow-up SYS-129**. H7 a latched fault
(`FMS[FAULTF]`) is not reported: **residual with follow-up SYS-130**. H8 start with the threshold already exceeded
(AN-119) and H9 the POL values under fault control (`SIM_MISCTRL0.FTMx_OBE_CTRL`, AN-120): **to be clarified on the
board**. H10 the fault control overrides `SWOCTRL` — an active short (SYS-116) ends in freewheel (as
`partner-and-degraded.md` 5.6): *agreed*.

**SYS-128 (2026-09-30, safety review SYS-86 H5: residual risk accepted, detection as a follow-up).** The table above is
the state of SYS-86; SYS-128 adds a detection half for the mechanism's own faults (`hardware-mechanisms.md` 2.5).
Correction of a fact first: a comparator switched off never trips the timer in **either** direction - in disabled
mode the output to the trigger mux (`CMPO`) is 0 ([RM13] 45.7.1); only `C0[COUT]` reads `INVT`. The SYS-86 text
"for an `above` threshold not exceeded" therefore holds for `below` thresholds as well (the fault-path model of
`bswgen-hwthreshold` modelled `CMPO` as `INVT` and is corrected).

| Field | Content (SYS-128) |
|---|---|
| Faults considered | comparator switched off or reconfigured (mode, inversion, filter, window, output select), DAC off or moved, round robin switched on, route or its lock changed, fault source select back on the pin, fault control of the timer changed; comparator output stuck at "above" or at "not above"; output "above" that does not latch the timer (route or fault logic broken after the comparator) |
| Detection | (a) **start test** in `ComplementaryPwm::init()` before anything of the route exists (trigger mux still 0, timer not configured, pins not muxed): the output is forced both ways by the DAC at VDDA against the input pin (pin against VDDA must read "not above", VDDA against the pin "above"); (b) **fast register check** of the path in the 1 ms poll of the power stage (`integration/HwThresholdWatch.h`, `DebugHaltGuardSystem::POLL_MS`) - the registers of SM-22 plus the fault control of the timer; (c) **consistency**: output "above" on two polls in a row without `FMS[FAULTF]`. Every finding is latched (one word in ordinary RAM with its complement, a corrupted word reads as a finding) until reset |
| Reaction | the channels of the group forced inactive through the software output control (the halt guard's PWM inhibit), at once in `init()` for a failed start test and in every poll while anything is latched - the state the tripped hardware path would leave; the decision on the ECU (S1) is the safe supervisor's (SYS-130) |
| Times | start test: 2 × 30 µs settling once per start; register check and consistency: one poll of 1 ms plus the latency of `TASK_BSP` (SM-22: 10 ms nominal, 17 ms guaranteed); consistency: two polls. **Computed, not measured** |
| Diagnostic coverage | **not claimed** (the mechanism itself is not credited). By construction: switching off and reconfiguration of every register of the path high (the same list as SM-22, ten times as often); stuck output **once per start** (latent-fault interval = drive cycle); a stuck output at "not above" that fails **between** two starts is not detected at run time - only a second, independent measurement of the signal can tell it from a quiet signal (plausibility G2, SYS-31) |
| Latent faults of the detection | the poll stops with `TASK_BSP` (the halt deadman SM-06 and SM-22 remain); the findings word lies in ordinary RAM - a wild write that clears it to a valid "nothing" word loses a finding, the next poll finds a persisting register fault again (a stuck output found only by the start test is lost until the next start) |
| Evidence | `hw-threshold-monitor` (host: start test with stuck outputs and a signal at the rail, 22 register faults injected one by one and 5 status changes that are no finding, consistency with and without the timer's latch, latch corruption, the real PWM driver and the real poll on register models, 169 checks), `hw-threshold-monitor-mutations` (23 built-in faults in family code, driver and poll, all detected), `bswgen-hwthreshold` (the order of SYS-86 with the start test in front, its 22 mutants still detected), `target-hwthreshold` (target build with a threshold); object code of the demo unchanged; no board run |
| Residual / action | (1) board proof: the start test on a board with a signal at a comparator input (the two forced comparisons read by J-Link or console), a switched-off comparator (J-Link write `CMP0_C0[EN]` = 0) forcing the group within one poll; (2) assumption of use: at the start the comparator input is further below VDDA than the comparator's offset plus hysteresis (data sheet: offset at most 25 mV) - a signal at the rail fails the start test (fail-safe, "stuck at not above"); (3) run-time detection of a stuck output needs the independent measurement (SYS-31); (4) a window of a few instructions between a release of the guard's inhibit and the next forcing in the same poll |

**SYS-130 (2026-09-30, safety review SYS-86 H7: report as a follow-up).** Residual (4) of the SYS-86 table - the
latched fault reported to nobody - is closed towards the supervisor; towards the application the reader exists, the
RTE signal and the DTC are open (`hardware-mechanisms.md` 2.6).

| Field | Content (SYS-130) |
|---|---|
| Faults considered | the threshold tripped (`FMS[FAULTF]` latched) or the path found broken (SYS-128), and nobody but the hardware knows: the drivers keep commanding duties, the other outputs and the gate driver enable stay on, the application does not learn why the bridge does not respond |
| Detection | safety task, every cycle, in the family port (`SafetyManager::checkSupervisionPeripherals()`): `FMS[FAULTF]`, the registers of the path read again (independent of the power stage poll), the findings latch of SYS-128; one context word (bit 0 tripped, bits 23..8 findings, bits 31..24 timer) |
| Reaction | through the new narrow interface `safeSupervisor/HwShutoff.h` (safety context only): log line and **S1 held until reset, no reset** - decided in the supervisor library, not by the reporter; only new bits react; S1 is not left through the register check's return path while reported. No reset because the path latches until reset (H1, manual recovery only) and a reset would re-arm it |
| Times | at most one safety cycle after the latch (10 ms nominal, 17 ms guaranteed); the hardware has switched the group off before (≤ 766 ns, SM-58) |
| Effectiveness | **not claimed** with SM-58. The report is kept in the protected RAM with a copy; a mismatch reads as "everything" (fail-safe) |
| Latent faults of the mechanism | a stopped safety task (SM-01, SM-04); a wild write that clears both copies loses the report - the next cycle reports again as long as `FAULTF` or the finding persists, a finding only of the start test is then lost until the next start |
| Evidence | `safe-supervisor-hw-shutoff` (real `HwShutoff.cpp` and `SafeSupervisor.cpp` with the switch of an image with a threshold: S1 without reset, new bits only, report without context = everything, corrupted record, the register check does not leave S1; 43 checks), `safe-supervisor-hw-shutoff-mutations` (9 built-in faults, all detected), `hw-threshold-monitor` (the context word of the safety cycle, 5 more built-in faults in `hw-threshold-monitor-mutations`), `safe-supervisor`, `safe-supervisor-degraded`, `safety-cycle`, `safety-gate` unchanged green; target build with a threshold warning-free, demo object code unchanged; no board run |
| Residual / action | (1) the RTE signal `safety.hw_shutoff` and the DTC `safety.hw_shutoff` (fixed number in the safety block, e.g. +0x05FE) need `tools/rtegen`, `tools/nxconfig/bswdtc.py` and `ecu/application` - outside SYS-130's paths; (2) the reaction is S1 for the whole ECU - a degraded reaction of only the group (SD) would need the report on the closed list of `Degraded.h` and a mapping in `tools/nxconfig/degraded.py` (decision); (3) board proof with a signal at a comparator input: threshold crossed → console line `Event: HW_SHUTOFF`, S1 (indicator, gate driver enable off), no reset, `safety` console or J-Link reads the record |

**SYS-135 (2026-09-30, safety review SYS-128/SYS-130 point C1: decision of the safety responsible).** Residual (2) of
the SYS-130 table is closed as decided: a project may map `HW_SHUTOFF` to SD, and a trip then degrades only the group
of the path (`hardware-mechanisms.md` 2.6, update SYS-135). Residual (2) of the SYS-128 table (the start test needs the
input below VDDA by more than offset plus hysteresis) is AoU-37 of the concept (point C3).

| Field | Content (SYS-135) |
|---|---|
| Faults considered | as SYS-130; in addition: a trip that holds S1 for the whole ECU although only one PWM group is affected (availability), and the wrong group or every group degraded, a finding of the path or an MCU fault degraded instead of held, a degraded report keeping S1 closed, the group released by the reset that re-arms the path |
| Detection | unchanged (SYS-130) |
| Reaction | project with `safety.degraded: [{event: HW_SHUTOFF, groups: [<group of the path>]}]`: context "tripped and nothing else" of a mapped timer → **SD of that group only** (outputs, latch, record in no-init RAM with id `0xFE` and the context, log), no reset, the last group → S1 without a reset; findings, a report without context, an unmapped timer, an implausible record → S1 held as SYS-130. Without the mapping: SYS-130 unchanged |
| Times | as SYS-130: at most one safety cycle after the latch; the hardware has forced the group before |
| Effectiveness | **not claimed** with SM-58 |
| Latent faults of the mechanism | as SYS-130 and SYS-117 (the degraded latch and record with their checks); new: the record of the S1 hold (`held` with its copy) - a mismatch reads as held (fail-safe); a wild write that clears both copies of both records of a degraded report is caught by nothing until the next new bit - the group stays degraded through the degraded latch and record, which are independent of it |
| Evidence | `safe-supervisor-hw-shutoff` (own profile: two groups, HW_SHUTOFF of timer 3 → group 0 and DEVICE_FAULT → group 1: SD of group 0 only and inside the interrupt lock, once, record with `0xFE`, findings/tripped with findings/trip then finding/undefined bit/unmapped timer/timer 0/no context/implausible record → S1 held, register check leaves S1 after SD and the group is held in the next cycle, last group → S1 without reset, group kept over a reset without power-on; 112 checks), `safe-supervisor-hw-shutoff-mutations` (18 built-in faults, all detected, and the unmutated test built without the mapping), `safe-supervisor-degraded-mutations` (26 faults detected, 9 configurations refused, among them a HW_SHUTOFF mapping in an image without the path), `nxconfig-degraded` (the closed list of reports read from `Degraded.h`, every refusal with YAML path, the derived word, a demo copy with the synthetic threshold of SYS-86); target build of that copy with the mapping warning-free (SD and HW_SHUTOFF compiled in); demo object code unchanged; no board run |
| Residual / action | (1) board proof with a signal at a comparator input and the mapping: trip → only the group's channels and its gate-driver enable off, the ECU keeps running (CAN, console), `safety.degraded` shows the group, no reset; (2) only one timer with thresholds per project while HW_SHUTOFF is mapped (the report record merges the paths; `nxconfig` refuses more); (3) *issue 1:* the released texts of concept 2.2/3 and AoU-04/AoU-32 still said "faults of one attached device"; *issue 2:* concept issue 3 names the trip of a hardware shut-off path as the second degradable case (CM-07 ZA, DOC-51) - done; (4) RTE signal and DTC of the report itself (SYS-130 residual (1)) still open - the degraded group reaches the application through `safety.degraded` |

**SYS-129 (2026-10-01, safety review SYS-86 H6: start test as a follow-up).** Residual (3) of the SYS-86 table - the
path behind the comparator never exercised - is closed for the start: a latent-fault test of the route, the fault
input and the fault latch of the timer once per start (`hardware-mechanisms.md` 2.7).

| Field | Content (SYS-129) |
|---|---|
| Faults considered | route not selectable (trigger mux register locked early, wrong register), fault source select back on the pin, fault input disabled or its polarity inverted, fault control off, timer not clocked, fault input stuck asserted or dead, fault logic that does not latch or cannot be cleared, a fault of another enabled input already latched at start |
| Detection | `hwThreshold::pathTest()` in `ComplementaryPwm::init()`, once per start, after the counter runs and before the route is locked and any pin is muxed (every channel masked): (A) comparator forced "not above", route selected without its lock - no fault may latch; (B) comparator forced "above" through the DAC at VDDA - `FAULTF`, the flag of the configured input and `FAULTIN` must be set; (C) forced "not above" again, only the test's flag cleared - nothing may remain; (D) route unselected, comparator configured again. Skipped after a failed start test of SYS-128 |
| Reaction | findings `PATH_FAULT_UNFORCED` / `PATH_NO_TRIP` / `PATH_NO_CLEAR` in the findings latch of SYS-128; the group forced inactive through the software output control before any pin is muxed (freewheel, review SYS-128 C2); the route still armed and locked; first power stage poll logs and keeps the group forced; the safety cycle reports the findings (`HW_SHUTOFF` with findings: S1 held, never SD); a fault that cannot be cleared also keeps `FMS[FAULTF]` and with it the hardware forcing |
| Times | seven waits of `settleUs` (30 µs) once per start, about 0.21 ms; the filter of the fault input is shorter than one wait (`static_assert` against the generated FTM clock) |
| Diagnostic coverage | **not claimed** (the mechanism itself is not credited). By construction: every fault of the path from the comparator output to `FMS[FAULTF]` that is present at the start; latent-fault interval = drive cycle. The forcing of the channels by the fault control (`FAULTEN` → `POL`) is **not** observed (outputs masked during the test) - its configuration is in the register check of SYS-128/SM-22 |
| Latent faults of the detection | the test runs only at start; a fault of the path that appears later stays with the register check and the consistency check of SYS-128; findings latch as SYS-128 |
| Evidence | `hw-threshold-monitor` (step model of the fault logic after [RM13] 47.4.3.17/47.5.18, `test/host/FtmFaultModel.h`: healthy test trips and clears with the route left unselected and unlocked and the comparator configured again; stuck and dead input, early-locked route, fault input disabled, fault control off, timer not clocked, source select on the pin, inverted polarity, a latched fault of another input that the test does not clear; the real driver with a failing test: group forced before the pin mux, route locked, context word `0x03280001`, first poll; 267 checks), `hw-threshold-monitor-mutations` (17 new built-in faults in the test and the driver, all detected), `bswgen-hwthreshold` (the order with the test in between, its 22 mutants still detected), `target-hwthreshold` (target build with the `static_assert`s); no board run |
| Residual / action | (1) board proof with the user (oscilloscope: no edge on the PWM pins during the start, J-Link read of `FMS` and the findings latch after the start; fault injection by J-Link before the test); (2) the forcing of the channels is not observed - option for the review: read `CnSC[CHOV]` with the mask open while the pins are still unmuxed - decided as the follow-up **SYS-165** (safety review group A 2026-10-01, P1 a; after the board proof of SYS-129, not part of Release R1); (3) AoU-37 covers the start test of SYS-128 - the path test relies on the same assumption, its text names only the start test; (4) a fault of another enabled fault input latched at start reads as a finding of this test (fail-safe, availability) |

### 3.5 Data and communication (concept 6.6)

#### SM-30 — Signal qualifier of the RTE

| Field | Content |
|---|---|
| Faults considered | a value is used although its measurement is invalid: raw value at the converter limits, amplifier outside its range, open pull-up, value outside the breakpoints, failed conversion |
| Detection | per channel and task, from the generated conversion: `QLF_ERROR_RANGE`, `QLF_ERROR`, else `QLF_OK` |
| Reaction | the application decides (AoU-12) |
| Times | one period of the reading task; the reaction time is the application's |
| Diagnostic coverage | **medium** for faults that drive the value out of its valid range (open, short to a rail, conversion failure). **None** for in-range faults: drift, stuck-at an in-range value, gain error — they need a second source (G2) or age supervision (G3) |
| Latent faults of the mechanism | none |
| Evidence | `adc-access`, `adc-access-averaging`, `bswgen`, `rtegen`, `demoswc-sil`, `boardtest` (qualifier phases against a simulated ECU) |
| Residual / action | G2, G3 |

#### SM-31 — E2E protection P01 / P02 / P11

| Field | Content |
|---|---|
| Faults considered | a CAN message corrupted, repeated, lost, out of sequence, or from the wrong sender |
| Detection | CRC-8, data ID and a 4-bit counter per message; status mapped to the qualifier, configured per signal by `rtegen` |
| Reaction | the application decides; the signal is marked |
| Times | one reception period; loss is only noticed when the application or the qualifier evaluates the absence (timeout priority in the qualifier, COM-12) |
| Diagnostic coverage | **high** for corruption (E2E CRC on top of the CAN CRC), repetition and sequence errors within the counter range, and masquerading between configured data IDs — **for the signals the integrator configured** (AoU-11). Blind spots: loss of more messages than the counter range, and signals without a profile |
| Latent faults of the mechanism | none |
| Evidence | `e2e`, `e2e-host`, `e2e-ref` (against the specification's examples), `e2e-mutations` (slow), `rtegen`, `can-bus-state`; board 2026-09-16 (COM-12/COM-03: seven steps with injected errors give the expected qualifiers) and 2026-09-21 (COM-29: counter jumps by exactly 1 after a communication pause) |
| Residual / action | — |

#### SM-32 — CAN bus state and transmit gate

| Field | Content |
|---|---|
| Faults considered | the ECU keeps sending into, or using data from, a bus that is not healthy |
| Detection | `CanBusMonitor` (fault confinement state, bus-off counters, per bus); `CanPhyGate` blocks transmission while no phy provider is ready |
| Reaction | transmission blocked; bus state feeds the qualifier (priority bus > timeout > CRC > alive) |
| Times | one supervision cycle |
| Diagnostic coverage | **medium**: the controller's own error confinement is reported per bus; a transceiver fault that the controller does not see is only visible through a phy provider — on boards with an SBC that is SM-33 (stage 2, not credited); on boards with a passive transceiver there is no transceiver diagnostic at all |
| Latent faults of the mechanism | none |
| Evidence | `can-bus-state` (two buses, board without SBC), `can-phy-gate`, `can-console`; board 2026-09-16 (COM-12) |
| Residual / action | — |

#### SM-34 — Device reports to the safe supervisor

| Field | Content |
|---|---|
| Faults considered | a fault found by a device package or a base-software service over device values is not attributed or reaches no reaction; a common cause on a device bus (F5) looks like several unrelated device faults; a switching-off reaction triggered by a task without safety context |
| Detection | none of its own: it carries what others detect. Context word (bus id, device instance, fault class, detail) checked on delivery, malformed words refused by the box and handled fail-safe; communication faults of two devices on one bus within 20 safety cycles are correlated into one "bus lost" event |
| Reaction | `DEVICE_REPORT`: S3 (log, bus verdict, `QLF_ERROR` on the device values of a lost bus); `DEVICE_FAULT`: S1 held for `IDENTITY`, S1 + S2 for every other class and a malformed word; `DEVICE_FAULT` only from the safety context (static rule `safety-context`) |
| Times | report: one safety cycle after posting plus the RTE task period for the qualifier; fault: within the safety cycle that detects it |
| Diagnostic coverage | **reaction** - the coverage belongs to the reporting package (SYS-28); credited only once a package reports through it |
| Latent faults of the mechanism | a lost box delivery is visible only as a missing report; the box lies in ordinary RAM (decision `sys37-postfach-abschaltend`: only reports without a switching-off reaction use it) |
| Evidence | `safe-supervisor` (9 built-in faults detected), `safety-context`; no board run yet (no driver reports, SYS-28). **SYS-194** (safety review BL38-P1, SYS147-B1 b): first user is a base-software mechanism, not a device package - the hold of the `safe_off` levels of the family port (SYS-151) reports every take-back of a foreign active level as `DEVICE_REPORT` (`NO_BUS`, `NO_DEVICE`, class `ACTUATOR_STATE`, detail = output number) through its own box in ordinary RAM, delivered by `SafetyManager::cyclic()`; `safe-off-command-hold` (rate, refusal, counter-check), `safe-supervisor` (S3 for the word, the same word as `DEVICE_FAULT` resets), `safety-context-elf` (box outside the protected RAM, strong `safeOffReportBox()`), `safe-off-hold-mutations`; board 2026-10-03 (SYS-147 step 5, demo image `7935d205`): a foreign high level on DRV_EN is taken back in 0.815 ms and reported as `DEVICE_REPORT` detail 3 about 8 ms later, a foreign KL30B switch-on in 0.27 ms (detail 7), no reset, no report for a refused or a commanded write; **SYS-196**: the take-back is recorded as DTC 0x1605FA (`19 02`, snapshot DID 0x0140, status after `11 01`, power cycle and `14 FFFFFF` as expected; two writes 1 … 2 s apart were counted once - open) |
| Residual / action | SYS-28 device packages; port call of `deliverDeviceReports()` for each further box (done for the `safe_off` hold, SYS-194); bus id as `NX_DEVICE_<D>_BUS_ID` from bswgen |

### 3.6 Start-up, reset and ECU state (concept 6.7)

#### SM-50 — Start reason from two independent sources

| Field | Content |
|---|---|
| Faults considered | a watchdog reset is not recognised, so the configured reaction to it (`after_watchdog_reset`) never happens |
| Detection | supply role (SBC reset source, where present) **and** `RCM_SRS` captured before `main()`, unified by a rank rule |
| Reaction | `after_watchdog_reset: sleep` also for a reset only the MCU saw |
| Diagnostic coverage | **high** for the recognition of a watchdog reset by the MCU's own register (independent of the SBC) |
| Evidence | `ecustate` (EcuStartReasonHostTest), `bsw-providers-sbc`, `bsw-providers-nosbc`, `bsw-providers-nosbc-nomcu`; board 2026-09-21 (SYS-18: `0x2` flash reset, `0x6` after `safety wdg`, `0x2` reset button, `0x1` power-on); reset-loop limit (below): board B10 and B11 passed 2026-09-22/23 |
| Residual / action | this is the recognition only; the **limit** on repeated safety resets was missing in stage 1 (F13) - **since SYS-43** see the reset-loop limit below |

**Reset-loop limit (SYS-43, T4, SSR-065)** - an extension of SM-50 on the reaction side, like the period part of SM-04:

| Field | Content |
|---|---|
| Faults considered | a permanent fault that an S1 + S2 mechanism answers with a reset restarts the ECU endlessly; at every restart the safe state is left until the first safety cycle finds the fault again (F13). Board without an SBC: no other limit; with an SBC its reset counter (SM-52) is device-dependent and not credited |
| Detection | a record of four words in the application's no-init RAM (`safeSupervisor/ResetLoopLimit.h`: count, pending mark, last cause, check word), placed by the family port (`platforms/s32k1xx/port/main/src/main.cpp`, section `.noinit`). Whether a start follows a **safety reset** is decided from independent sources: the supervisor's own mark, written in `SafeSupervisor::handle()` immediately before the reset of every S1 + S2 reaction (with the event); the MCU's reset status as SM-50 captured it before `main()` (a real watchdog reset - not the fast test of the start-up - counts, a power-on or low-voltage reset clears); the hard fault dump of SM-17 and a core lockup (`RCM_SRS[LOCKUP]`, a fault while the fault handler runs, so the dump may be missing) count as a fault exception. **Debugger case** (board finding 2026-09-22): a J-Link reset (`VC_CORERESET` + `AIRCR.SYSRESETREQ`) starts with RESET | WATCHDOG (start reasons `0x06`), a real watchdog time-out with WATCHDOG alone - so the watchdog reason counts only without another reset reason in the same start, and debug restarts neither count nor clear. Every other reset (pin, debugger, a software reset nobody marked, e.g. a diagnostic ECU reset or the jump into the bootloader) neither counts nor clears. Evaluated once per start in `main()` after the watchdog fast test, before the BSP, the tasks and the application start. Random content after power-on is recognised by the check word, and the family port clears the record on POR/LVD in `boardInit()` before its first read (the RAM has ECC) |
| Reaction | from `reset_loop_limit` consecutive safety resets on (default 3, i.e. the third safety reset in a row): `RESET_LOOP_LIMIT` → **S1 held without a reset** (`holdSafeState`, like `sys23-halt`), with one console line naming the count and the last cause (`SAFE STATE HELD (RESET_LOOP_LIMIT, 3 safety resets, last: event 9): ...`). Only a power cycle ends it: a reset from outside finds the count again and holds again. After `reset_loop_clear_ms` of operation without a safety reset (default 60 000 ms, counted in safety cycles, so a slow safety task clears later, never earlier) the count is cleared |
| Times | the limit acts at the start after the N-th safety reset, before anything of the application runs: bootloader, start-up and watchdog fast test only. The N - 1 restarts before it still run the application until the fault is found again |
| Diagnostic coverage | **high** for loops of S1 + S2 reactions of the supervisor, of the MCU watchdog and of fault exceptions. Not counted: a loop through resets the supervisor does not mark (e.g. a run-down step time-out of SM-53, a diagnostic reset request) and, with an SBC, resets of the SBC's watchdog (they reach the MCU as a pin reset; SM-52 counts them) |
| Latent faults of the mechanism | the record lies in ordinary RAM outside the MPU region (it must survive the reset and is written from `main()` and the safety task); a wild write that makes it invalid restarts the count from 0 (the next safety reset counts as the first) - it never leads to a hold by itself. A wild write that sets a plausible count is not detectable; the check word makes a random one unlikely |
| Evidence | `safe-supervisor` (SYS-43: record rules, the mark before every reset of an S1 + S2 reaction and never before a hold or a report, hold without reset from the limit on and again after an outside reset, clearing after the fault-free time, console line; 17 built-in faults all detected, `doc/changelog.d/SYS-43.md`), `safety-context` and `safety-context-elf` (the call only from `main()` inside its SafetyShell; the record in the NoInit region above the RAM the start-up initializes, outside the MPU region, below the boot request). Board (issue 2): B10 passed 2026-09-22/23 (three J-Link resets count nothing; three `safety wdg` give 1 of 3, 2 of 3, then `SAFE STATE HELD (RESET_LOOP_LIMIT ...)`, CAN silent; supply interruptions and the reset button do not count, the button in the held state holds again), B11 passed 2026-09-22 (count cleared after more than 60 s, record read by J-Link, `safety mpu` counts as cause `0x2`); suite case BT-S1H-04 2026-10-02 |
| Residual / action | the two values are provisional defaults in the family port until the module manifest of the supervisor exists (proposed `modules.safeSupervisor.reset_loop_limit` / `reset_loop_clear_ms`, `tools/nxconfig`/`tools/bswgen`, see `doc/changelog.d/SYS-43.md`); the SBC's start reason (second source of SM-50) is not yet an input, because the supply role reports only after the run level is reached; the fault memory entry belongs to NVM-03 |

#### SM-51 — Image check by the bootloader

| Field | Content |
|---|---|
| Faults considered | the application image is incomplete, from another project, or interrupted mid-flash |
| Detection | at every start (`app_image_valid()` in `bootloader/src/core/app_image.c`): record CRC, compatibility block, CRC-32 over the whole image, plausibility of the entry. **BL-36:** the start decision is checked again immediately before the jump, not taken from a RAM value of the past. **BL-38 (bootloader 0.3.8):** that check before the jump (`app_image_confirm()`) reads the record, the compatibility block and the entry again from flash, but computes no second CRC-32 over the image - the record's CRC and size must equal the values of the one CRC of this start (`bootloader/doc/README.md`, "One CRC per start") |
| Reaction | the bootloader does not start an invalid image and stays reachable for a download |
| Times | at start, before the application runs |
| Diagnostic coverage | **high** for the application image at start (CRC-32 over the complete image including the ROM-check list and reference). Not covered: the bootloader's own code; projects without the neXtBSW bootloader |
| Evidence | `bootloader-host`, `bl-update-host` (power loss at every flash operation), `bl-update-mutations` (slow), `canflash-tool`, `bl-platform-host`; board 2026-09-21 (VER-07: power loss at 52 % of a download, bootloader reachable, reflash succeeds; BL-26); board 2026-10-02/03 (BL-38: bootloader 0.3.9 with one CRC per start, about 144 ms per start; BL-39: diagnostic CAN on CAN1, VER-04 passed - update, flashing and the way back over both channels) |
| Residual / action | the integrity of the bootloader itself is not checked by any stage-1 mechanism (open point N4). **Since BL-38 (accepted in safety review BL38-P1, 2026-10-03, F5 a):** an image byte that changes between the CRC at start and the jump without a flash command (debugger write, bit fault) is no longer detected by the bootloader; with a held extended session that window can last minutes. The residual is left to the flash ECC and SM-14, which runs only after the start (idle context, lifecycle level 2) and whose coverage depends on the project's configuration (config-range CV-11); hence **AoU-CFG-7**: an image the neXtBSW bootloader starts keeps SM-14 active. Recorded as an assumption of use and not as a configuration check, because the start by the neXtBSW bootloader is a decision of the integrator's delivery and production flow that the configuration of the application image does not describe, and because the part that the configuration does describe - the re-check period - is already the integrator's data-set value (AoU-CFG-4) with the proposed check C2; SM-14 itself cannot be configured away in an image built through the build (AoU-CFG-1). **Observation BL-39 (safety review BL38-P1, F6 b):** during the development of the diagnostic CAN (BL-39) a HardFault in the bootloader caused a reset loop inside the bootloader - an observed case of the unsupervised bootloader phase (N4): the outputs stayed in their reset state, and nothing in the bootloader limits such a reset loop (the reset-loop limit SM-50 is a mechanism of the application). The restart time of the CAN1 build was not measured; it is covered by AoU-CFG-2 (the integrator measures his configuration) |

#### SM-53 — Defined run-down

| Field | Content |
|---|---|
| Faults considered | the run-down before the power down does not finish (a step, the lifecycle shutdown or the power down hangs or fails), so the ECU would stay half shut down; power outputs left at their last value until the supply goes off (SYS-174) |
| Reaction | state machine with per-step time-outs; a step that does not finish in time is aborted (`IShutdownStep::abort()`) and leads to a restart; the restart is supervised and, if the lifecycle does not carry it out within `shutdown_timeout_ms`, ended by a direct reset (safe outputs table, software reset; SYS-173). Last run-down step: the safe outputs table, the outputs locked off until the power down or the reset (SYS-174). `step_error: continue` never passes the NvM step (state machine and generator, SYS-174) |
| Effectiveness | **reaction**, bounded by the configured time-outs; the restart bounded by `shutdown_timeout_ms` as long as the ECU state management runs (lifecycle levels above 1) |
| Evidence | `ecustate` (EcuShutdownHostTest, EcuRestartSupervisionHostTest with a lifecycle that never completes, EcuSafeOutputsStepHostTest, EcuOutputFlushHostTest, integration test); `data-flash-system` (abort keeps the gate locked); `rtegen` (continue refused with an NvM step); board SYS-02 (b) (Rundown after 2001 ms, MCU unpowered, wake-up by CAN); board 2026-10-02/03 (issue 2): SYS-174 step limits NvM 1 000 ms, CAN 500 ms, outputs 50 ms, `lc reboot` runs down without a watchdog reset; `ecu sleep` with the oscilloscope: DRV_EN at 0 V from 3.2 s before the supply is off; SYS-173 with a test image that hangs the shutdown: restart requested after 5 000 ms, **direct reset 9 995 ms** after the hang began (bound 2 × 5 000 ms plus at most 10 ms), restart as software reset, no watchdog |
| Residual / action | the static-analysis finding SYS-26 touches this mechanism (concept 8.4). A level-1 component that hangs in its shutdown on the way to level 0 is not covered by the supervision (the ECU state management itself is at level 1), and `SafetySystem::shutdown()` still disables the watchdog there (WDG-F03). **Gap accepted by the safety responsible 2026-10-02 (review Welle-0210-Tag, S173-1, N5):** the watchdog is not kept running to level 0 because after its last service about 160 ms remain while the orderly reset comes only after the logger is drained (up to 5.7 s), so every restart would turn into a watchdog reset; a service from the run-down sequence needs a new service point and a bounded logger, which are separate tasks (S173-1 options b and c, not taken). **Residual risk:** a level-1 component that hangs in its shutdown during the run-down towards level 0 is not detected by any mechanism; the ECU then stays half shut down with the outputs at the last value of the safe outputs table step (SYS-174) or, if that step was not reached, at their last value, until the supply is removed. The risk is bounded by the facts that the situation needs a hang of a level-1 component in a rare event (shutdown or sleep request), that the application is stopped by then, and that the safe outputs table step is the last run-down step. The non-volatile data are not made consistent by SM-53: a cut write is detected by the NvM's own format (two slots and a check sum), not here. The output flush of SYS-09 drains the diagnostic output only |

### 3.7 Summary table

Guaranteed times of the checks in the safety task since SYS-40 (SM-04): while the task runs; a task that stops is
SM-01's (section 2).

| ID | Coverage class | Guaranteed detection (nominal) | Reaction | Evidence strength |
|---|---|---|---|---|
| SM-01 | medium | 250 ms nominal LPO, ≤ about 283 ms over the LPO range (derived) | EWM walk, S2 | host (relation), board; residual DF-05 (lost SIRC: the reset is the only reaction) |
| SM-02 | medium | 119 ms (70 ms) since SYS-53, 136 ms (80 ms) since SYS-40 | S1 + S2 | host with mutants (SYS-41) |
| SM-03 | low | 119 ms (70 ms) since SYS-53, 136 ms (80 ms) since SYS-40 | S1 + S2 | host (SYS-41) |
| SM-04 | medium (SYS-41, period part SYS-40) | 17 ms (10 ms) since SYS-40; period: at the late cycle, window 7 cycles (SYS-53); system time slow/fast: kinds 4/5 at the end of a window (SYS-95) | S1 + S2 | host with mutants; board B4, B7 passed (2026-09-22), B4 again 2026-10-03 |
| SM-05 | medium / none (activation) | end of cycle | S3 | host with mutants, board |
| SM-06 | high (blind spot: its own clock, DF-05) | 10 ms; configuration re-check 17 ms (10 ms) on the target since SYS-48; progress check (DFA-2) within a few cycles | S1 | host, target, board (re-check SYS-51, progress check 2026-10-04) |
| SM-07 | high | at start | S1 held | host, board (reaction) |
| SM-10 | high | on access | fault → S1 + S2 | host, target, board (MSP guard 2026-09-30, 2026-10-03) |
| SM-11 | medium | 17 ms (10 ms) | S1 + S2 | host with mutants (SYS-42) |
| SM-12 | high | 17 ms (10 ms) | S1 + S2 | host |
| SM-13 | high | 17 ms (10 ms) | S1 + S2 | host with mutants (SYS-171), board B17 passed 2026-10-02 |
| SM-14 | high at start; at run time high with a re-check period (SYS-44), low with period 0 | at start (+ at most 5 574 µs measured, B1); then one period + one pass (default 60 s) | S1 held | host with mutants, target, board (first pass, B1, B9 re-check); suspension during a flash write not on the board |
| SM-15 | medium | 17 ms (10 ms) after read | S1 + S2 | host with mutants (SYS-41) |
| SM-16 | medium | 17 ms (10 ms) after read | S1 + S2 | host with mutants (SYS-41) |
| SM-55 | high (RAM decoder and record paths exercised); flash not covered | at start | S1 + S2, then S1 held by the reset-loop limit | host with mutants, target (ELF), board 2026-09-29/30 |
| SM-17 | high | on exception | walk, dump, S2 | target, host, board |
| SM-60 | not claimed (exclusion, NVM-25; not credited for Release R1) | prevents: `read()` answers `BUSY` at once; a store under a hold fails after at most 260 ms | `BUSY` / reported write error | host with mutants; board with NVM-29 2026-10-02 (in part) |
| SM-18 | medium | 17 ms (10 ms) | S1 + S2 | host |
| SM-19 | high | 170 ms (100 ms) | S1 + S2 | host |
| SM-20 | low to medium | 102 ms (60 ms); read failures 170 ms (100 ms) | S1 + S2 | host with mutants (SYS-41) |
| SM-21 | medium (issue 1: low; blind spot `SYSTEM_CLOCK` until SYS-55) | start-up (generated lock list) | S1 + S2 | host with mutants (SYS-42, SYS-24), board B8 passed 2026-09-22 |
| SM-22 | medium (issue 1: low; sampled per cycle, blind spot `SYSTEM_CLOCK` until SYS-55) | 17 ms (10 ms) | S1 | host with mutants (SYS-42, SYS-24, SYS-153), board 2026-09-22, 2026-10-01 |
| SM-59 | not claimed (hardware, SYS-125; not credited for Release R1) | comparator response, no software time | hardware reset, held below VLVD | host with mutants, target (ELF), cyclic re-check SM-22 (SYS-153, board 2026-10-01); reset at a falling supply not recorded (SYS-198, after Release R1) |
| SM-23 | reaction | < 1 µs computed; board ≤ 21 µs (B3, bound) | — | host, target, board |
| SM-24 | high (build) | build time | build error | generator test, build |
| SM-25 | reaction | — | S1 before S2 | host |
| SM-26 | reaction, not claimed (optional, SYS-116; accepted by `nxconfig` since SYS-139/SYS-140; not credited for Release R1) | as the path that walks the table | selected short or freewheel | host with mutants, disassembly executed; lock times measured (SYS-172, < 5 µs); board open (B12 … B14) |
| SM-27 | not claimed (building block, SYS-116; called by the port since SYS-141/SYS-143; not credited for Release R1) | at start after power-on | short withdrawn, freewheel | host with mutants; lock times measured (SYS-172) |
| SM-58 | not claimed (optional, SYS-86) | comparator ≤ 500 ns + 3 (4 + filter) timer clocks, computed | group to `POL`, latched until reset | host with mutants, target build; board open |
| SM-58 (SYS-128/SYS-130) | not claimed | path lost: one 1 ms poll; stuck output: at start; tripped or lost to the supervisor: one safety cycle | group forced inactive in the poll; S1 held until reset by the supervisor (SYS-135: a trip mapped to SD degrades only the group, no reset) | host with mutants, target build; board open |
| SM-30 | medium | one task period | application | host |
| SM-31 | high (configured signals) | one reception period | application | host with mutants, board |
| SM-32 | medium | one supervision cycle | transmission blocked | host, board |
| SM-34 | reaction (the coverage is the reporter's) | report: one safety cycle; fault: in the detecting cycle | S3 / S1 held / S1 + S2 | host with mutants, board (SYS-194/SYS-196 2026-10-03) |
| SM-50 | high | at start | configured behaviour; since SYS-43 S1 held after N safety resets in a row | host, board; reset-loop limit host with mutants (SYS-43), board B10, B11 passed (2026-09-22/23) |
| SM-51 | high | at start | image not started | host with mutants, board (BL-38/BL-39 2026-10-02/03) |
| SM-53 | reaction | per step time-out; restart ended by a direct reset after `shutdown_timeout_ms` | restart | host, board (SYS-02; SYS-173/SYS-174 2026-10-02/03) |

---

## 4. The assumed safety goals, checked

Concept section 2.2 asks SYS-20 to confirm or correct the per-goal verdicts. All seven are **confirmed**; the
analysis adds limits but changes no verdict.

| Goal | Concept verdict | SYS-20 | What the analysis adds |
|---|---|---|---|
| ASG-01 unintended torque | partial | **confirmed** | the termination path is sound for faults the MCU detects (SM-06, SM-10, SM-17 high; the safety-task checks up to 31.25 ms, F9). What is not detected stays not detected: G1, G2, F4. A clock-gated FTM keeps its outputs out of reach of the walk (SM-23) — G1 must read the pin, not the FTM. *Issue 2:* the safety-task checks are bounded at 17 ms since SYS-40; F4 is narrowed by SYS-24/SYS-153 (open until SYS-55); G1 is closed on the host for S1, S1 held, SD and S1 + S2 (SYS-30, SYS-138); the deductive view is the fault tree FT-ASG-01 of [`architecture-safety-analysis.md`](architecture-safety-analysis.md) section 5.1 |
| ASG-02 switch-off path available | covered with limits | **confirmed** | further limits: the de-energising before the watchdog reset depends on `CS[INT]`, which nobody checked (F12; checked since SYS-41, SM-02); without an SBC nothing limits a reset loop, and every restart runs the application until the first safety cycle finds the fault again (F13). **After SYS-43:** the reset-loop limit holds S1 at the start after the N-th safety reset in a row (host verified; board B10/B11 passed 2026-09-22/23). *Issue 2:* the residual risk DF-05 (lost SIRC: no walk before the watchdog reset, up to about 283 ms; section 2.1, SYS-195) limits this goal as well; deductive view FT-ASG-02 of the architecture safety analysis section 5.2 |
| ASG-03 cell values outside SOA | not covered | **confirmed** | the base-software contributions that do exist (SM-18 … SM-20, SM-30, SM-31) protect the MCU's own measurement path and the transport; F16 shows that even the MCU's own path carries a silent gain error inside the SM-18 band |
| ASG-04 unintended balancing | not covered | **confirmed** | no stage-1 mechanism reaches a bus-commanded actuator (G4) |
| ASG-05 corrupted / stale data | covered for CAN data | **confirmed** | for signals with an E2E profile (AoU-11); "stale" for device values stays G3 |
| ASG-06 control software failure → safe state | covered with limits | **confirmed** | further limits: the safety task's own period (F9), task activation (F10), ECC poll gaps (F11), watchdog configuration (F12), supply channel read failures (F14); the evidence for SM-02, SM-03, SM-11, SM-15, SM-16, SM-21 is review only (F15). **After SYS-41:** F11, F14 and F12 (watchdog) closed with host tests, SM-02, SM-03, SM-15, SM-16 host-tested; F12 for the deadman host verified, target binding pending (SYS-48); F9, F10 and review-only SM-11, SM-21 remain. **After SYS-42:** SM-11, SM-21 and SM-22 host-tested with mutants, F15 closed; F10 remains. The verdict stays "covered with limits". *Issue 2:* F12 for the deadman closed on the target (SYS-48, SYS-51); the faults of the time bases of section 2.1; deductive view FT-ASG-06 of the architecture safety analysis section 5.3 |
| ASG-07 intended image | partial | **confirmed** | start: SM-51 high; run: SM-14 high at start, low at run time (F3) — **since SYS-44** high at run time with a re-check period (host verified, board B9 passed for the re-check); the bootloader's own integrity is not covered (N4) |

---

## 5. Decisions on the findings handed to SYS-20

### 5.1 F3 — the ROM check in one go

The concept names two problems: an unbounded run in idle, and a once-per-start check that is not a run-time
mechanism. Since SYS-38 the check exists; the analysis decides:

1. **No slicing while the check runs at idle priority.** `RomCheck::idle()` runs in the idle context of the
   lifecycle (`RomCheckSystem.h`), without any lock (the pass, since SYS-44 `checkPass()`, is outside the safety shell; only the report is
   inside). Every supervised task preempts it. Its only cost is that other work of the idle context (console, log
   drain) waits for the few milliseconds of the run. Slicing is required **only** if the check is ever moved into a
   timed context (a task with a deadline) — that becomes a requirement for SYS-22: *"The ROM check shall not run
   with interrupts suspended and shall not run in a context with an RTE deadline unless it is sliced."*
2. **Duration: computed about 3 … 6 ms** for the demo image (43 049 words; assumption: a few core cycles per word
   for the flash read and the CRC write at 80 MHz). Not measured — board checklist item B1 (section 8).
3. **A re-check period is needed**, because the first intended applications include battery management, where ECUs
   are often powered for long periods without a restart. Today the run-time protection of the program flash rests on
   SM-16 (double-bit errors on read) alone. Proposed task **T5**: periodic re-check with a period as a module
   parameter of `safeMemory` (milliseconds; 0 = once per start, today's behaviour), restart of the pass after each
   full run, a known-answer test of the CRC unit before each pass, the CRC unit reserved for SM-14 (no other user
   today; a second user must be serialised), and suspension while the program flash is written (a later in-application
   update must not produce a false `ROM_CHECK_FAILURE`, which holds the ECU until a power cycle). Until T5 exists,
   proposed **AoU-25**: the integrator restarts the ECU at least as often as his latent-fault interval requires, or
   accepts SM-16 as the only run-time check of the flash.

**Implemented by SYS-44** (host verified with mutants; board B1 and B9 open in issue 1 - passed since, 8.1 and 8.2):
- Period: module parameter `modules.safeMemory.rom_check_period_ms` (manifest
  `platforms/s32k1xx/port/safety/safeMemory/module.yaml`, generated into `integration/safeMemoryConfiguration.h`),
  default 60 000 ms, 0 = once per start. Start to start, on the system time of the platform.
- Context: `RomCheck::idle()` is called by the idle handler of the application (`ecu/application` `app.cpp`, only in
  an image with the reference CRC) and, as before, once by the component `romcheck`; both run in the idle context. The
  pass itself runs without a lock and outside the safety shell; only the bookkeeping at its end and the report enter
  the shell (one shell per pass, none per round of the idle loop while it waits). Decision 1 above therefore still
  holds: no slicing.
- Known-answer test of the CRC unit before every pass (`ROMCHECK_UNIT_FAILED` → `ROM_CHECK_FAILURE`).
- Suspension: every flash writer brackets its write with `safety::FlashWriteScope` (indicator `RomCheckFlashWrites`
  in ordinary RAM, library `romCheckFlashWrites`, outside the MPU protected sections). The NvM does so for every write
  of its memory (`nvm::FlashWriteBracket` around the `IEepromDriver` of the port: on the S32K1 the FlexRAM stores of
  the FlexNVM EEPROM emulation). A flash command started elsewhere is seen through `FTFC FSTAT[CCIF]`. No pass starts
  while a write is open; a pass during which one began is discarded and repeated, never reported. A due pass kept off
  for more than one period is reported (`ROMCHECK_OVERDUE`), so the indicator cannot switch SM-14 off silently.
- Not done: slicing (not needed, decision 1); a check in `tools/nxconfig` that a period other than 0 exceeds the
  longest flash write (the manifest says so; the tool is outside this task).

### 5.2 F4 — scope of the register lock and check (input for SYS-24)

What SYS-24 must cover to support ASG-01 and ASG-02, by register class. All entries are **derived** from data the
configuration already has (the safe outputs table, the PWM groups, the clock plan), so no new configuration value is
needed for the list itself — only a per-project switch for optional classes.

*Issue 2:* SYS-24 implemented the classes pin function, direction, clock gates and PWM timer as the generated lists
`PIN_FUNCTION`, `GPIO_DIRECTION`, `CLOCK_GATE`, `PWM_TIMER` (board 2026-09-22), SYS-153 added `SUPERVISION`
(`PMC_LVDSC1`), and the supervision peripherals are checked by SYS-41/SYS-48. The class **system clock** is open:
**SYS-55** (F4, AN-004). The table below is the scope of issue 1, kept as the input it was.

| Class | Registers (S32K1) | Lock or check | Derived from | Why |
|---|---|---|---|---|
| Pin function of every safe output and PWM pin | `PORTx_PCRn` (MUX, drive, pull) | **lock** (`PCR[LK]`) + read-back at start; cyclic check of the value | safe outputs table, PWM groups | a remuxed pin is out of reach of the walk |
| Direction and level of every `safe_off` GPIO | `GPIOx_PDDR`, `PDOR` (for inactive state), `PDIR` (read-back) | **check** cyclic | safe outputs table | the check that SM-22 does for one pin today; also the basis of G1 |
| PWM timer configuration | FTM `CONF`, `POL`, `OUTMASK`, `DEADTIME`, `COMBINE`, `FLTCTRL`, `SWOCTRL` (inactive level word), `INVCTRL` | **check** cyclic; `FTM` write protection (`MODE[WPDIS]`/`FMS[WPEN]`) where usable | PWM groups | wrong polarity or dead time is a hazard of ASG-01 that no other mechanism sees |
| Clock gates of the peripherals in the table | `PCC` entries of FTM and PORT | **check** cyclic | safe outputs table | a gated clock makes the walk skip a peripheral (SM-23) |
| System clock | `SCG` clock source control and status (`LK` bits where the family has them), `SCG_CSR` | **lock** at start where possible, **check** cyclic | clock plan (mcudb/board) | a wrong clock stretches every software time; the watchdog and the deadman run on their own clocks and remain as backstop |
| Supervision peripherals | `WDOG_CS` (full mask, F12), `LPIT0` channel 1 (`TCTRL`, `TVAL`, `MIER`, `MCR[DBG_EN]`) and the NVIC enable/priority of its vector | **check** cyclic | fixed by the mechanisms | latent faults of SM-01/SM-06 (F12) — done by SYS-41 (`checkWatchdogControl`, `checkLpitDeadman`; the deadman part runs on the target since SYS-48, board SYS-51) |

The lock bits and write-protection features named here must be confirmed against the reference manual by SYS-24;
this table names the classes, not verified register details. Distribution over several safety cycles is allowed as
long as the resulting detection time is stated (the OpenBSW comment in `SafeIo.cpp` suggests it); each check
reports through `CHECK_REGISTER_ERROR` or a new event with the register class in the context word.

### 5.3 F5 — SPI as a common cause

**Decided: not a stage-1 problem, and no separate SPI supervision in stage 1.** Reasoning:

- No credited stage-1 mechanism uses SPI. The safe outputs walk writes port and FTM registers; the watchdog, the
  deadman, the MPU, ECC, ROM check and the supply checks are MCU-internal. A failed SPI master therefore removes no
  stage-1 detection and no stage-1 reaction.
- What depends on SPI is stage 2: the device diagnostics of the gate driver, the cell controller and the SBC. For them
  the common cause is real and belongs into the **dependent-failure analysis of SYS-28**.
- The SBC watchdog, where enabled, is triggered over SPI; a failed SPI therefore ends in an SBC reset — a safe
  direction (stage 2, SM-42).

What stage 1 contributes is the report path: the device context word proposed in **G5** shall carry the **bus
instance**, so that `SafeSupervisor::handle()` can recognise communication faults of several devices on the same bus
within one window as one event ("bus lost") and choose one reaction. That is an addition to the G5 task, not a task
of its own. SYS-21 carries the other side: the SPI master is a resource shared between partitions.

**Status after SYS-29 (host verified):** bus id in bits 31..24 of the context word (kind SPI/I2C/UART/CAN/other,
position of the bus among the used buses of that kind in board order - `safeSupervisor/DeviceReport.h`, the same rule
in `tools/rtegen` `device_bus_id`). Communication faults (`DEVICE_REPORT`, class `COMMUNICATION`) of two different
devices on one bus within 20 safety cycles (200 ms nominal) are logged once as "bus lost", further ones in that
window are folded into it; the verdict ends after a window without a communication fault. The one reaction is S3
plus `QLF_ERROR` on the qualifier of every device value on that bus (`gd.<device>.qlf` today): by the user's decision
`sys37-postfach-abschaltend` a verdict built from reports that other tasks posted may not switch off - a package that
needs a switching-off reaction to a lost bus detects it in the safety task and raises `DEVICE_FAULT`. Limits: 8 buses
tracked at the same time (a further one is logged without correlation), the window is a code constant (two cycles of
the slowest `cycle_ms` a manifest allows; SYS-25 moves such constants into a configuration level), and the verdict
freezes while the safety task stops (safe direction: it stays lost).

### 5.4 Status of the other findings

| Finding | Status after SYS-20 |
|---|---|
| F1 | closed by SYS-23; reaction board-proven (simulated failure, 2026-09-21); ROM path board-proven with SYS-38 |
| F2 | check exists (`nxconfig-watchdog`); configuration level is SYS-25. F9 adds that the relation holds only if the safety task keeps its period |
| F6 | *issue 2:* measured (B2, 8.1: 1 077 ms watchdog path and 858 ms fault-handler path, the stated values; with bootloader 0.3.9 within them); register AN-006 `review` (SYS-45 open in the planning) |
| F7 | closed on the board (IO-37, 2026-09-21: KL30B 0 A for 6 s after `safety dst`) |
| F8 | *issue 2:* bound measured (B3, 8.1: ≤ 21 µs walk to the gate-driver enable, 2026-09-22); the computed < 1 µs stays computed; register AN-008 `review` (SYS-45 open) |

---

## 6. New findings of the failure analysis

| ID | Finding | Why it matters | Proposed owner | Status (issue 2, as in the anomaly register) |
|---|---|---|---|---|
| **F9** | The safety task's own period is not supervised. The watchdog fires only when the service interval (8 cycles) exceeds 250 ms, i.e. at a cycle > 31.25 ms; the watchdog has no window | every "10 ms" in the concept is nominal; the guaranteed bound is 31.25 ms (section 2) | **T1** | **addressed** by SYS-40 (SM-04, host verified with mutants): 17 ms while the task runs, too fast and a stopped timer detected; board B4/B7 **passed** 2026-09-22. Open: the hardware deadline for a stall that ends before the watchdog (T1b, **SYS-57**). Register AN-009 `in progress` |
| **F10** | RTE task **activation** is not supervised: SM-05 measures durations from the start of a cycle; a starved or late task raises nothing. The activation distance is recorded as a statistic only | temporal interference by a higher-priority task goes unnoticed | **SYS-21** (temporal freedom from interference) | open (SYS-21). Register AN-010 `open` |
| **F11** | ECC poll incomplete: status flags never cleared (reset loop or masking, depending on reset behaviour to be confirmed); whether `ERM->SR0` records without `ERM->CR0` enables is unconfirmed; RAM and flash share one event without context under a DMA name; no start-up RAM test or scrubbing; single-bit corrections invisible | SM-15/SM-16 are claimed "medium" only under the assumptions that must be confirmed | **T2** | **closed in the code** by SYS-41 (flags read and cleared before the report, source in the context, `SR0` without `CR0` confirmed in the reference manual, single-bit corrections counted; start-up RAM test decided not; test `safe-memory-ecc`); B5 on the board shows only that no flag survives a reset. Register AN-011 `review` (proof with an injected error: SM-55, SYS-73) |
| **F12** | Configuration checks of the supervision peripherals incomplete: watchdog `INT`, `WIN`, `PRES`, `UPDATE`; the deadman's `MIER`, NVIC enable/priority, `TVAL`, `MCR[DBG_EN]` | a single bit flip silently removes the de-energising before a watchdog reset, or the deadman | **T2** | **closed**: watchdog by SYS-41 (`safe-watchdog`, board B6 2026-09-22), deadman bound on the target by SYS-48 and proven by the fault injection of SYS-51 (8.1 B6). Register AN-012 `closed` (issue 1 said "target binding pending (SYS-48)") |
| **F13** | Stage 1 has **no limit on repeated safety resets**. A permanent fault detected by an S1 + S2 mechanism restarts the ECU endlessly, and each restart runs the application until the first safety cycle; the only limit (SBC reset counter, SM-52) is device-dependent and not credited, and boards without an SBC have none | the safe state is left briefly at every restart; decision `sys23-halt` rejected a no-init-RAM "blocked start" only for the ROM check, pending NVM-03 | **T4** (needs a user decision) | **closed** by SYS-43 (decision `sys43-resetschleife`; reset-loop limit, section 3.6 under SM-50; host verified with mutants), limits as module parameters by SYS-53, board B10/B11 **passed** 2026-09-22/23. Open beside it: the SBC's start reason as a further input. Register AN-013 `closed` |
| **F14** | SM-20 skips a failed reading of the internal supply channel silently; there is no limit like SM-19's | a permanently failing channel removes the supply supervision without an event | **T2** | **closed in the code** by SYS-41 (limit `adc_read_error_limit`, context 2; `safe-system`); a failing supply channel cannot be provoked on the board. Register AN-014 `review` |
| **F15** | Safety code without a host test: `safeWatchdog` (SM-01 … SM-03), `SafetyManager` (the order of all cyclic checks, SM-04, SM-11), the ECC part of `safeMemory` (SM-15, SM-16), `SafeIo` lock and check (SM-21, SM-22) | the evidence for these mechanisms is code review only | **T3** | **closed**: by SYS-41 `safeWatchdog` (`safe-watchdog`), `SafetyManager::cyclic()` (`safety-cycle`), ECC part of `safeMemory` (`safe-memory-ecc`); by SYS-42 `SafeIo` lock and check (`safe-io`, SM-21/SM-22, with a corrected read-back of SM-21, board B8 passed) and the MPU gate check (`safety-gate`, SM-11). Register AN-015 `closed` |
| **F16** | SM-18 accepts VREFH within ± 200 mV of 4096 mV (demo), i.e. a gain error of up to ± 4.9 % on every analogue value, without any event | an integrator whose safety-related analogue value needs better accuracy gets no warning | proposed **AoU-24** | **accepted**: AoU-24 is binding (safety manual section 7, review DOC-19 D3; a node of concept 4.3 since concept issue 3, CM-07 ZG). Register AN-016 `accepted` |

Proposed assumptions of use (for concept section 4 and DOC-19; numbers to be confirmed when carried over): **AoU-24** (ADC reference tolerance against the accuracy needed, F16) and **AoU-25** (restart or re-check interval against the latent-fault interval of SM-07 and SM-14) - nodes with the status `proposed` in [`assumptions.sdoc`](../requirements/safety/assumptions.sdoc) (moved from this table by DOC-21; text unchanged). *Issue 2:* both are binding - status `concept` in concept section 4.3 (AoU-24) and 4.2 (AoU-25) since concept issue 3 (CM-07 decision ZG) and in the safety manual section 7.

---

## 7. Proposed tasks

For the coordinator to carry into `doc/todo.yaml` (IDs to be assigned there). All are stage-1 base-software work; none
depends on manufacturer documents. Priority follows the project's order (safety before OTA and multi-MCU ECU).

| # | Title | Priority, dependencies | Touches (expected) | Closes |
|---|---|---|---|---|
| **T1** | Supervise the safety task's own period: compare successive cycle starts with a free-running hardware timer (LPIT channel 0 system time) and raise an event beyond a bound derived from the period; alternatively evaluate the watchdog window mode. Restores the nominal 10 ms as a guarantee | P2; with SYS-25 (same constants) | `platforms/s32k1xx/port/safety/safeLifecycle`, `ecu/safety/safeSupervisor` (new event), `tools/nxconfig` (derived bound) | F9 — **done by SYS-40** (SM-04: LPTMR on the LPO clock instead of LPIT channel 0, which is the system time on ARMv6-M; window mode not used, section 3.1 and the changelog give the reasons; the bound is derived in the family port, not in `tools/nxconfig` - SYS-25) |
| **T1b** | Hardware deadline for the safety task: the LPTMR compare interrupt of SM-04 retriggered by every safety cycle, walking the safe outputs table when the bound passes - the guarantee then holds even for a stall that ends before the watchdog fires | P2; after SYS-40 | `platforms/s32k1xx/port/main/src/bsp/startUp.S` (LPTMR0 vector), `tools/bswgen` (`FAMILY_IRQ_HANDLERS`, generated vector tables), `platforms/s32k1xx/port/safety/safeSupervisionCheck` | F9 rest |
| **T2** | Close detection holes in existing mechanisms: ECC (clear and record the flags, separate events with context, confirm the `SR0`/`CR0` behaviour in the reference manual, decide a start-up RAM test), watchdog configuration mask (`INT`, `WIN`, `PRES`, `UPDATE`), deadman configuration re-check, read-failure limit for the internal supply channel, one sequence checkpoint per sub-check of the safety cycle | P2 | `platforms/s32k1xx/port/safety/{safeMemory,safeSystem,safeLifecycle}`, `ecu/safety/safeWatchdog`, `ecu/safety/safeSupervisor`, `platforms/s32k1xx/port/main/src/integration` (deadman); a neXtBSW copy of the OpenBSW watchdog driver if `UPDATE = 0` is wanted | F11, F12, F14, SM-04 — **done by SYS-41** (one event with the source in its context instead of separate events; no start-up RAM test; the deadman re-check runs on the target only after **SYS-48** binds its expected values; `UPDATE = 0` not decided) |
| **T3** | Host tests with deliberately broken production code for `safeWatchdog`, `SafetyManager::cyclic()`, the ECC part of `safeMemory` and `SafeIo` lock/check; register them for the TOOL-40 coverage thresholds | P2; before or with T2 | `ecu/safety/safeWatchdog/test`, `platforms/s32k1xx/port/safety/*/test`, `tools/test/tests.yaml` (coordinator) | F15 — **done** by SYS-41 (`safe-watchdog`, `safety-cycle`, `safe-memory-ecc`, `lpit-deadman-check`) and SYS-42 (`safe-io`, `safety-gate`, with `safe-io-mutations` and `safety-gate-mutations`) |
| **T4** | Reset-loop limit in the base software: count consecutive safety resets in no-init RAM (survives a software reset, cleared at power-on and after a configured time in normal operation), go to S1 held after a configured number. **Decision board item first** — it revisits the "blocked start" rejected in `sys23-halt` for a different purpose, and it needs the no-init RAM that `SafeSupervisor::init()` already marks as TODO | P2; decision first; relation to NVM-03 | `ecu/safety/safeSupervisor`, linker script (no-init section), `platforms/s32k1xx/port/mcuResetReason` | F13 — **done by SYS-43** (decision `sys43-resetschleife`; no change to the linker script: the record goes into its existing `.noinit` input sections; `mcuResetReason` is used through the ecuState hook, unchanged; `SafeSupervisor::init()` keeps its TODO for the event log of NVM-03) |
| **T5** | ROM check re-check period (module parameter, 0 = once per start), known-answer test of the CRC unit, CRC unit reserved for SM-14, suspension during program-flash writes; board measurement of one pass | P2; replaces the open rest of G6 | `platforms/s32k1xx/port/safety/safeMemory`, `platforms/s32k1xx/port/main/src/integration/RomCheckSystem.h`, module manifest | F3, G6 rest — **done by SYS-44** (host verified with mutants; the periodic call comes from the idle handler in `ecu/application`, `RomCheckSystem.h` unchanged; suspension for NvM writes and running flash commands; board B1 and B9 open) |
| **T6** | Board measurements of the analysis: checklist of section 8 | P2; hardware | none (measurements, changelog) | F6, F8, B1 … B6 |

Additions to existing tasks:

- **SYS-21:** F10 (activation supervision as part of temporal freedom from interference); N3 (MPU rights of the DMA
  master: descriptor 0 grants M2/M3 rwx, SYS-171 - restrict it before the first DMA/ENET user); SPI master as a shared
  resource (F5).
- **SYS-24:** the register list of section 5.2.
- **G5 task:** bus instance in the device context word and correlation of communication faults per bus (F5) —
  **done by SYS-29** (section 5.3).
- **SYS-28:** dependent-failure analysis of the SPI bus for its device packages (F5).
- **SYS-22:** the requirement on where the ROM check may run (section 5.1), and each "guaranteed" time of section 2
  as a verifiable requirement once T1 exists.
- **DOC-19:** AoU-24 and AoU-25 (proposed), and the two time columns of section 3.7.

---

## 8. Board checklist (not run for this task)

No hardware was used for SYS-20. The following measurements would turn computed values into measured ones; each is
read-only or uses the existing console commands, and none needs the power stage enabled unless stated.

| # | Measurement | How | Expected |
|---|---|---|---|
| B1 | Duration of one ROM-check pass (F3) | toggle a spare GPIO around `RomCheck::idle()` in a test build and measure with the oscilloscope, or read the system time before and after in a test build | about 3 … 6 ms at 80 MHz |
| B2 | Restart time after a safety reset (F6) | `safety wdg` (watchdog path) and a resetting event; measure from the last CAN frame to the first application frame, and from the reset pin to the first frame | a number for AoU-05 |
| B3 | Duration of the safe outputs walk (F8) | oscilloscope on DRV_EN and a PWM phase, trigger on a GPIO set immediately before `enterSafeState()` in a test build | well below 1 µs |
| B4 | Safety cycle under load (F9) | load-test build (`NX_RTE_LOAD_TEST_US`) plus a GPIO toggled at every safety cycle; measure the maximum distance between two cycles. Since SYS-40 also: no `SAFETY_CYCLE_PERIOD_VIOLATION` in 10 min under load, with the console and CAN traffic running | ≤ 10 ms plus preemption; shows how far the nominal value holds. Confirms the 2 ms lateness of SM-04 (`CYCLE_LATENESS_MS`) or gives the value to use |
| B7 | SM-04 on the target (SYS-40) | J-Link: (a) clear `LPTMR0_CSR[TEN]` while running, with `exec SetSkipDebugDeInit` before `exit` (otherwise PLAT-67: the J-Link clears DEMCR.TRCENA, the system time stops and the watchdog fires with a hard fault dump) → console line `SAFETY RESET (SAFETY_CYCLE_PERIOD_VIOLATION, kind 3, value 2): outputs off` (SYS-52), reset without hard fault dump, next start `reset loop: … last cause 0x111`; (b) a test build that makes the safety task wait 20 ms once (or a breakpoint-free busy loop in the `safety` console context) → kind 1 with about 20 ticks; (c) halt with the debugger for 30 s and resume → **no** report (the LPTMR stops in debug mode). Read `LPTMR0_CNR` twice 1 s apart → about 1000 ticks (LPO1K runs) | (a)–(c) as stated; confirms the choice of LPO1K and the debug behaviour of [RM13] 49.5.4 |
| B5 | ECC status after a software reset (F11) | J-Link: write a non-correctable error injection if the derivative supports it (ERM/EIM), read `ERM_SR0` before and after `safety wdg` | since SYS-41 for the record only: the flags are cleared before the report, so neither a reset loop nor masking can follow |
| B6 | Watchdog configuration check (F12) | J-Link: clear `WDOG_CS[INT]` while running; before SYS-41: **no** event (F12); since SYS-41: `SAFE_WATCHDOG_CONFIGURATION_ERROR` with fields 0x2, S1 + S2 | confirms that F12 is closed on the target |
| B9 | ROM re-check and suspension on the target (SYS-44) | test image with a short period (project `modules.safeMemory.rom_check_period_ms: 2000`); J-Link after `connect`, `g`, `sleep 10000`: read `safety::RomCheck::_statistics` and `_result`; then `nvm write` on the console ten times in a row and read them again; `safety::romCheckFlashWrites` at rest | `passes` about 5 after 10 s and one more per period, `_result` = 1 (OK); no `ROM_CHECK_FAILURE` or `ROMCHECK_OVERDUE` during the NvM writes (`postponements`/`discarded` may grow); `_begun` = `_ended` at rest |
| B10 | Reset-loop limit through a resetting event (SYS-43) | demo image with SYS-43, power cycle; first three J-Link resets (`r`, `g`) in a row - start reasons `0x06`, **no** `reset loop` line, no hold (board finding 2026-09-22); then three times `safety wdg` on the console (watchdog path: counts through the MCU's reset status), reading the console after each restart; then press the reset button; then power cycle | after the 1st and 2nd: `reset loop: 1 of 3 ...` / `2 of 3 ..., last cause 0x1`, application runs; after the 3rd: `SAFE STATE HELD (RESET_LOOP_LIMIT, 3 safety resets, last: watchdog): outputs off, no reset - power cycle required`, no further restart, safe outputs off (LED1 of `safety.safe_state_output`), CAN silent; after the reset button: held again; after the power cycle: normal start without a `reset loop` line |
| B11 | Fault-free time and software safety resets (SYS-43) | power cycle; `safety wdg` twice (count 2), wait **more than 60 s**, then `safety wdg` again; J-Link (after `connect`, `g`, `sleep 3000`): read `safety::resetLoopRecord` (four words, `0x20006D44` in the demo ELF of SYS-43 - take the address from the ELF). The other two causes: `safety mpu` (MPU violation, hard fault handler SM-17 resets after the dump) and, with J-Link, clearing `WDOG_CS[INT]` while running (B6: `SAFE_WATCHDOG_CONFIGURATION_ERROR`, an S1 + S2 reaction of the supervisor) | after the wait the count restarts: the third `safety wdg` gives `reset loop: 1 of 3`, no hold; the record reads count, pending 0, last cause, check word = `~(0x524C3433 ^ count ^ rotl(pending, 11) ^ rotl(lastCause, 21))`; `safety mpu` counts with `last cause 0x2` (fault exception), the watchdog configuration error with `last cause 0x103` (event 3) |
| B8 | SM-21 read-back with the lock bit (SYS-42) | flash the demo image with SYS-42 and start it normally: the log shows **no** `LOCK_REGISTER_ERROR`; J-Link (after `connect`, `g`, `sleep 3000`): read the PCR of the locked role pin of the project (`safety.locked_pin`) → `LK` (bit 15) = 1 and `MUX` = 001 | confirms on the target that the extended read-back does not report a correctly locked pin (otherwise a reset at every start) |

SYS-116 (SM-26, SM-27) - **only with the user present**: motor connected, 24 V power stage, `MOT1_GD_DRV_EN` switched
on by the user (CLAUDE.md), and only after the follow-ups that let `nxconfig` accept a short (a test build of a demo
copy with `safe_states` and the facts):

| # | Measurement | How | Expected |
|---|---|---|---|
| B12 | Short pattern and its dead time at standstill | oscilloscope on the high-side and low-side gate of one leg (and the phase), PWM running at a small duty, motor at standstill; select `active_short_low`, then enter S1 (`safety dst`); repeat with the halt deadman (debugger halt) and a fault handler (`lc dabt`) | every low side on, every high side off after the walk; the low side comes on only after the configured dead time behind its high side; no overlap on any leg; DRV_EN stays high in S1 and in the deadman path |
| B13 | Fallbacks to freewheel | as B12, then (a) clear the timer's clock gate, (b) write a corrupted selection word with the J-Link, (c) raise the timer's fault input (SYS-86 or SYS-85), (d) a resetting event | (a)(b) all gates off (freewheel) after the next walk; (c) freewheel by hardware while the short is selected; (d) the board's reset state during reset and restart |
| B14 | Start-up test | test build that calls the start test before the first enable, motor at standstill, then with the motor turned by hand | pattern read back, restored; with a stuck register (J-Link write lost) the short is withdrawn; the phase voltages during the test (IO-64) near 0 V |

SYS-30 (read-back of S1, G1) - no power stage needed, the gate driver stays disabled:

| # | Measurement | How | Expected |
|---|---|---|---|
| B15 | S1 confirmed by read-back, and S1 that does not act | demo image; `safety dst` (S1): console for 10 s; then with the J-Link (after `connect`, `g`, `sleep 3000`) write `FTM0_SWOCTRL = 0` while S1 is latched (gate driver disabled, no power stage: harmless). The pin cases - `PTC_PSOR` bit 11 (MOT1_GD_DRV_EN) and `PTD_PSOR` bit 17 (switches KL30B on) - **only with the user** (CLAUDE.md: MOT1_GD_DRV_EN, `kl30b on`) | after `safety dst` no reset for 10 s; after the write either the hold of S1 (integration, every poll) restores the register before SafeIo reads it - no report, S1 holds itself - or within one safety cycle `SAFETY RESET (LOCK_REGISTER_ERROR, register 117440512 ...)` (class `SAFE_OUTPUT` = 7, entry 0; `117440513`/`117440514` for the two pins), restart. A write repeated by a J-Link loop faster than the hold must end in the reset |

SYS-138 (read-back in the hold, for SD and before the reset) - no power stage needed, the gate driver stays disabled:

| # | Measurement | How | Expected |
|---|---|---|---|
| B16 | Hold, S1 + S2 and a gated timer | demo image. (a) S1 + S2 with a working walk: `lc assert` (ETL_ASSERT, the shared S1 + S2 sequence) - console; (b) `safety dst` (S1), then with the J-Link clear the clock gate of FTM0 (`PCC_FTM0` CGC) while S1 is latched; (c) a hold (test build with a forced `ROM_CHECK_FAILURE`), then with a J-Link loop write `FTM0_SWOCTRL = 0` faster than one hold round; (d) SD: `target-active-short`-like project on a board with a degraded mapping, J-Link write to the software output control of the degraded group | (a) `SAFETY RESET (...)` line as before (a read-back right after the walk is confirmed - the pin level is not behind); (b) within one safety cycle a reset without a line, the reset-loop record names `LOCK_REGISTER_ERROR`; (c) the hold ends in a reset without a line; (d) reset (S1 + S2) within one safety cycle |

SYS-171 (SM-13 region check, SFP-F02/F04) - read-only, J-Link, no power stage:

| # | Measurement | How | Expected |
|---|---|---|---|
| B17 | MPU words as the region check expects them | demo image with SYS-171, normal start, console for 60 s; then J-Link (after `connect`, `g`, `sleep 3000`, `h`): `mem32 0x4000D400, 4` (RGD0 WORD0..3), `mem32 0x4000D410, 28` (RGD1..7), `mem32 0x4000D480, 4` (RGD8 - must not be valid); resume with `g` | no `MPU_CONFIGURATION_ERROR` and no limp home in 60 s; `RGD0_WORD2 & 0x007DFFFF` = `0x007C77D8`; `RGDn_WORD2 & 0x007DFFFF` of 1, 3, 4, 5, 7 = `0x001C77DF` (full access) and of 6 = `0x0061871C` (read only), gate (2) the one or the other; bits 24 … 31 noted (reserved: 0 or as written); `RGD0_WORD3[VLD]` noted (expected 0 per [RM13] 15.4.7, then the M2/M3 rights of descriptor 0 are not effective - otherwise H10 holds as written) |

### 8.1 Board results 2026-09-22 (evalboard_s32k144_demo, S32K144 at 80 MHz)

Captures in `tools/boardtest/captures/evalboard_s32k144_demo/2026-09-22_board/`. B1, B3, B4 and B7(b) used a
test build with measuring variables in the safety task (not committed, removed after the run).

| # | Result | Captures |
|---|---|---|
| B1 | one ROM-check pass (`RomCheck::idle()`, idle priority) took at most **5 574 µs** without load, inside the computed 3 … 6 ms (F3); safety cycle without load 9 996 … 10 004 µs | 112–113 |
| B2 | restart time last to first application frame: **1 027 ms** through `safety wdg` (including the 250 ms watchdog timeout), **852 ms** through `safety mpu` (F6, number for AoU-05). **Re-measured 2026-09-30** (DOC-19 D7, SYS-149; CAN trace and oscilloscope on RESET_b and CAN0-TX): `safety wdg` 985 / 995 / 1 056 / **1 077 ms**, `safety mpu` 819 / 845 / 858 / 817 ms - the value for AoU-05 is the maximum, **1 077 ms** (watchdog path) and **858 ms** (fault handler). Composition from RESET_b: three reset pulses (< 1.3 ms each) at 0, 228.7 and 455.5 ms - the fault reset and the two resets of the MCU watchdog fast test (SM-07), not the SBC (debug jumper open, SBC reset source unchanged, start record without PIN) and not the reset-loop limit (it counts, it does not reset) - then the first frame at 786 … 787 ms. Each of the three starts passes the bootloader's start-up window (200 ms) plus about 28 ms of start-up; the application needs about 100 ms from its fast test to the first frame. The rest is the time from the last frame to the reset: 198 … 290 ms on the watchdog path (250 ms time-out counted from the last service of the watchdog, which lies before or after the last frame), 30 … 71 ms on the MPU path (phase of the fault in the 100 ms frame cycle). **Bootloader 0.3.9** (BL-38/BL-39, 2026-10-02 evening, two runs per path, frame gaps without oscilloscope - a plausibility check, safety review BL38-P1 F4): `safety wdg` 883 / 893 ms, `safety mpu` 700 / 700 ms. *Issue 2 - re-measured 2026-10-03* (session M1/M2 of BL-33, CAN frame gaps, the composition with the oscilloscope on RESET_b): `safety mpu` **699.7 ms** in 3 runs (a fourth run ended in the reset-loop protection), `safety wdg` **883 … 933 ms** in 4 runs (933.2 / 913.2 / 883.0 / 923.3 ms); within the stated **1 077 / 858 ms**, which stay the values for AoU-05 (review BL38-P1 F2 a). Composition: RESET_b pulses at 0, +229.0 and +173.0 ms after the fault reset; the first start is the bootloader's initialisation (about 20.6 ms), its 200 ms window (the one CRC of BL-38, about 144 ms, inside it) and about 8 ms to the application's fast test; the two starts of the watchdog fast test (SM-07) each need about 152 ms (initialisation, CRC, 8 ms); then the application to its first frame. The MPU path is 36 ms longer than with 0.3.6 (664 ms): about 11 ms come from the longer CRC of the grown image, the rest from the application side - the split is **SYS-197** (after Release R1). The breakdown "three starts with a 200 ms window each" above is the state before bootloader 0.3.6 | 96; `2026-09-30_board/abend/` `d7_*`, `osz_o1_*`, `scope/c-20260930-224625-02.csv` (wdg), `scope/c-20260930-224743-03.csv` (mpu) - in the repository since DOC-59 (CM-02 re-check BRD a; before only in the local working tree) |
| B3 | walk SWOCTRL → DRV_EN: **upper bound ≤ 21 µs** (24 V, oscilloscope on DRV_EN and phase V): the last high phase of V ended complete 21 µs before DRV_EN fell, after that both FETs off, no glitch. The expected < 1 µs was not resolved because the phase was already low; order on the console `enter safe state` → `MOT1_GD enabled -> disabled` → `2 power stage output(s) locked` within 1 ms. A sharper measurement needs the probe on the low-side MCU pin while it is on (F8 stays computed below 1 µs, bound measured) | 119–127 |
| B4 | 10 min under load (9 000 µs busy wait in the application task, console and CAN traffic): safety cycle **9 982 … 10 019 µs** over 63 798 cycles, 0 overruns, no `SAFETY_CYCLE_PERIOD_VIOLATION`, ROM check every 60 s passed; the 2 ms lateness of SM-04 holds with margin. Under load a ROM-check pass is preempted (upper bound 89 618 µs, not a pass duration) | 25–28, 117–118 |
| B7 | (a) kind 3, fields 2, console line before the reset, no hard fault dump; (b) blocking the safety task 1 … 4 ms: no report; **5 ms: kind 1, value 15**, 6 ms: value 16, each with the `SAFETY RESET` line and `reset loop: 1 of 3`, last cause `0x111` — the threshold matches `maxIntervalTicks` = 14 at this LPO; (c) 30 s debugger halt: no report | 32–33, 105–106, 114–116, 128–133 |
| B6 | test build with the console fault injection of SYS-51: `inject wdogint` clears `WDOG_CS[INT]` while running → reset with last cause `0x103` (`SAFE_WATCHDOG_CONFIGURATION_ERROR`), **no** hard fault dump, `WDOG_CS` reads `0x25EB` again after the restart; **F12 closed on the target**. Since SYS-59 the line `SAFETY RESET (SAFE_WATCHDOG_CONFIGURATION_ERROR, fields 0x2): outputs off` comes before every such reset (15/15 injections on 2026-09-22, captures 160–174, deadman fields 0x10/0x4/0x40/0x2 likewise); the time to the reset is not measured. Same build, deadman (SYS-48): falsified `TVAL`, `MIER`, NVIC priority and `MCR[DBG_EN]` each reset with last cause `0x110` | 141–149 |
| B5, B8–B11 | not run in this round (issue 1); *issue 2:* run since, see 8.2 | — |

### 8.2 Board results after the round of 2026-09-22 (issue 2)

Recorded by the tasks named, at the eval board (`evalboard_s32k144_demo`, S32K144 at 80 MHz), with the image of the
day; captures under `tools/boardtest/captures/evalboard_s32k144_demo/` where they are in the repository. Issue 1 of
this document still said "board open" for most of them (CM-02 finding F-10).

| Check | Date, task | Result | Captures |
|---|---|---|---|
| B8 (SM-21) | 2026-09-22, SYS-42 | passed: no `LOCK_REGISTER_ERROR` at the start, J-Link reads the role pin's `PCR` = `0x00008100` (`LK` = 1, `MUX` = 001) | `2026-09-22_board/01_flash_boot.console.txt`, `02_sys42_b8_jlink.txt` |
| B9 (SM-14) | 2026-09-22/23, SYS-44/SYS-45 | re-check passed: 13 passes in 26 s with a 2 s period, 60 s spacing on the demo, no failure or overdue pass; the NvM writes of the test wrote nothing, so the suspension during a real flash write is **not shown** (after PGMPART, NVM-07) | `2026-09-22_board/29_sys44_b9_flash.log`, `30_sys44_b9_nvm.console.txt`, `31_sys44_b9_result.jlink.txt`; suite `2026-10-02_suite-integ12/BT-RUN-07.console.txt` |
| B10 (SM-50) | 2026-09-22/23, SYS-43 | passed: three J-Link resets count nothing; `safety wdg` three times gives 1 of 3, 2 of 3, then `SAFE STATE HELD (RESET_LOOP_LIMIT ...)`, CAN silent; power cycle starts normally; supply interruptions and the reset button do not count, the button in the held state holds again | `2026-09-22_board/62_…` … `65_sys43_b10_*`, `152_…`/`153_sys43_b10_*`; `2026-09-23_board/13_…` … `18_sys43_*` |
| B11 (SM-50) | 2026-09-22, SYS-43 | passed: after more than 60 s without a safety reset the count starts at 1 again; the record read by J-Link; `safety mpu` counts with cause `0x2` | `2026-09-22_board/66_sys43_b11_*`, `67_sys43_b11_record.*`, `68_sys43_b11_mpu*`; suite `2026-10-02_suite-integ12/BT-S1H-04.console.txt` |
| B5 (SM-15) | 2026-09-24, SYS-45 | run: `ERM` registers 0 before and after `safety wdg` - shows only that no flag survives a reset (weak; the detection is SM-55's) | not in the repository |
| B15 (SM-23, SYS-30) | 2026-09-30, SYS-30 | run ad hoc in the evening session: a J-Link write in S1 ends in `LOCK_REGISTER_ERROR` (register 117440513 for DRV_EN), a single KL30B write is taken back by the hold, a loop of 20 writes resets (117440514); recorded in `doc/todo.yaml` SYS-30; the console and J-Link logs of that session are in the repository since DOC-59 (CM-02 re-check BRD a); to be repeated with records as suite case BT-S1-03, which is on the board list of Release R1 (SYS-167, verification plan 9.1) | `2026-09-30_board/abend/` `sys30_b15a.*`, `sys30_b15b.*`, `sys30_b15_pin_drven.*`, `sys30_b15_pin_kl30b.*`, `sys30_b15_pin_kl30b_diag.*`, `sys30_b15_pin_kl30b_loop.*` (`.console.txt`, `.jlink.log`) |
| B16 (SYS-138) | — | **not run**: (b) is part of suite case BT-S1-03 on the board list of Release R1 (SYS-167); (a), (c) and (d) after Release R1 with **SYS-198** - a known gap of Release R1 (CM-02 re-check BRD a, 2026-10-04) | — |
| B17 (SM-13) | 2026-10-02, SYS-171 | passed: no `MPU_CONFIGURATION_ERROR` in 60 s; `RGD0_WORD2` masked = `0x007C77D8`, descriptors 1, 3, 4, 5, 7 full access, 6 read only (guard), 2 the gate; `RGD0_WORD3` = 0 (valid bit of descriptor 0 not set; H10, N3 and SM-10 carry this caveat since DOC-59, S171-3 of SYS-171) | `2026-10-02_board/welle1002t/a1_60s.txt`, `a1_mpu.jlink`, `a1_mpu2.jlink` |
| SM-55 | 2026-09-29/30, SYS-73, SYS-137 | start line `RAM ECC latent test (EIM) passed, 2 channel(s)` at every start, 16 min without reset; an injected fault gives cause `0x107`; the missing `SAFETY RESET` line fixed by SYS-137 and passed the same evening | `2026-09-29_board/board0929_02_session.console.txt`; suite `2026-10-02_suite-integ12/BT-START-01.console.txt` |
| SM-59 | 2026-10-01 … 03, SYS-125, SYS-153, BL-34 | `PMC_LVDSC1` reads `0x10` (also halted in the bootloader); a written `0x00`/`0x30` gives `CHECK_REGISTER_ERROR`; the reset at a falling lab supply is **not recorded** - after Release R1 with **SYS-198** (SYS-125 was booked done without it), a known gap of Release R1 (CM-02 re-check BRD a) | `2026-10-03_board/sys172/s172_poweron.txt` (`POR+LVD` at power-on) |
| SM-60 | 2026-10-02, NVM-25/NVM-29 | B-NVM29-1 … 9 passed (B-NVM29-7/8 = B-NVM25-2, 3, 6); B-NVM25-1, 4, 5, 7 not recorded on their own | not in the repository |
| SM-10/SM-17 (SYS-97) | 2026-09-30, 2026-10-03 | MSP use 744 of 4 096 B, guard 1 024 B; `inject mspoverflow` gives `HARD FAULT RESET: interrupt stack overflow (guard), outputs off` with the dump | `2026-10-03_board/bl039-suite/BT-S2-07.console.txt` |
| SM-53 (SYS-173, SYS-174) | 2026-10-02/03 | step limits NvM 1 000 / CAN 500 / outputs 50 ms; `lc reboot` without a watchdog reset; `ecu sleep`: DRV_EN at 0 V before the supply is off; a hanging shutdown ends in the direct reset after **9 995 ms** (bound 2 × 5 000 ms + 10 ms) | `2026-10-02_board/welle1002t/a3.txt`, `a3_reboot.txt`; `2026-10-03_board/sys173/s173_console.txt` |
| SM-34 (SYS-194), SYS-196 | 2026-10-03 | foreign DRV_EN high taken back in 0.815 ms, `DEVICE_REPORT` detail 3; foreign KL30B in 0.27 ms, detail 7; no report for refused or commanded writes; DTC 0x1605FA with snapshot, status after reset and clear as expected; two writes 1 … 2 s apart counted once (open) | `2026-10-03_board/sys194_196/` |
| SYS-95 (section 2.1) | 2026-10-03/04 | 32 min under UDS load and at 10/14/12 V without a report; gated LPIT clock: `HARD FAULT RESET` (SM-17); lost SIRC: watchdog hardware reset without line or walk (DF-05); stopped `CYCCNT`: `WATCHDOG RESET`, SM-06 expired; `SIRCDIV2` /64: `DEADMAN_CONFIGURATION_ERROR` field 0x100 | `2026-10-03_board/sys95/` |
| SYS-172 (N6) | 2026-10-03 | every interrupt lock of the active short **below 5 µs**: start test begin 2.72 µs, end 2.08 µs, selection about 0.5 µs (first call 0.71 µs), block 0.93 µs at the pin (DWT 2.2 µs, 1.5 µs, ≤ 0.35 µs, 0.26 µs; the pin pulse includes the GPIO bookkeeping) | `2026-10-03_board/sys172/` (`scope_165011-23.csv` begin/end, `scope_164301-21.csv` selection, `scope_165046-24.csv` block) |
| B2 (F6) | 2026-10-03, BL-33 | `safety mpu` 699.7 ms (3 runs), `safety wdg` 883 … 933 ms (4 runs) with bootloader 0.3.9; composition in 8.1 | `2026-10-03_board/bl33/` |

---

## 9. Open points

| ID | Point |
|---|---|
| N1 | Mapping of the coverage classes to the figures the standard expects at ASIL B (issue 1: concept 9.4, point 7 and the diagnostic-coverage question; needs the standard and the MCU safety manual). **Closed in issue 2** (CM-02 decision Z2, measure M3): the gap analysis `iso26262-gap.md` (not published, available on request) section 3 answers it - no direct mapping; a figure needs each mechanism re-based on the hardware element, the technique and its test interval (M2), which is task **DOC-56** after Release R1 and before the first ASIL B release; the classes stay for Release R1 (section 1.3). Register AN-023 |
| N2 | The MCU manufacturer's safety manual for the S32K1 family (required hardware measures, coverage of ECC, watchdog, MPU, CRC unit) is not available; every class in section 3 is about neXtBSW's use of the feature. AoU-09 applies |
| N3 | Whether the MPU region descriptors restrict the DMA master's write access to the protected RAM (SM-10). **Answered by SYS-171: they do not** (by the register description; caveat of B17 below) - descriptor 0 (whole address space) grants M2 (DMA) and M3 (ENET) rwx, which overrides the "no access" of the locked words ([RM13] 15.1.2; HSI H10). Remains open as an action: restrict descriptor 0 before the first DMA/ENET user (SYS-21); until then **AoU-41** forbids any bus master other than the core (decision 2026-10-02, review Welle-0210-Tag S171-1); whether descriptor 0 stays valid after `init()` is board check B17 - *issue 2:* B17 (2026-10-02) reads `RGD0_WORD3` = 0, the valid bit is **not** set, so the M2/M3 rights of descriptor 0 may not be in effect; H10, this point and SM-10 carry this caveat since DOC-59 (S171-3 of SYS-171, CM-02 re-check BRD a) and keep the unfavourable reading until it is settled; the restriction of descriptor 0 stays SYS-21 |
| N4 | Integrity of the bootloader itself: no stage-1 mechanism checks it. Observed case of the unsupervised bootloader phase (BL-39, development of the diagnostic CAN, recorded by safety review BL38-P1 F6 b): a HardFault in the bootloader led to a reset loop inside the bootloader with the outputs in their reset state; no bootloader mechanism limits that loop (SM-51 block) |
| N5 | Gap accepted: the watchdog is switched off on the way to level 0 (`SafetySystem::shutdown()`), so a level-1 component that hangs in its shutdown stays undetected; justification and residual risk in the SM-53 block of section 3.6. Decision of the safety responsible 2026-10-02, review Welle-0210-Tag S173-1 (INS-WDG-1 WDG-F03, INS-ECS-1 ECS-F01). The same gap is finding ASA-3 of the architecture safety analysis (registered once, AN-130); confirmed in CM-02 (CR-02-1, Z6) |
| N6 | Assumption: all tasks run privileged, because the interrupt lock (`cpsid`) around check and write in the selection, the lock and the start test of the active short (SYS-172) is ignored in the unprivileged thread mode; carried as AoU-42. Decision of the safety responsible 2026-10-02, review Welle-0210-Tag SYS-172 P1 (INS-HFH-1 HFH-F06). *Issue 2:* the lock times are measured on the board (2026-10-03, scope on a pin and DWT, section 8.2): all **below 5 µs** - start test begin 2.72 µs and end 2.08 µs, selection about 0.5 µs, block 0.93 µs at the pin; the estimate of 2 … 3 µs holds. Accepted gap confirmed in the confirmation review CM-02 (CR-02-1, Z6) |
| N7 | Upkeep after the re-check of CM-02 (record `reviews/records/CR-02-1.md` (not published, available on request), finding RN minor, remarks R2, R3, R4, R11 and Z4; 2026-10-04): stale places of issue 2 - "proposed AoU-24/AoU-25" in SM-18 and SM-07, the SYS-135 residual (3) at the texts before concept issue 3, the SYS-129 residual (2) without SYS-165, the cells of issue 1 in SM-26 (effectiveness, residuals (1)/(2)) and SM-27 (effectiveness, residual (3)) against their head paragraphs, the SM-23 row of 3.7 without the B3 bound, section 0 "Verification" pointing to 8.1 only, the header "update blocks up to 2026-10-02"; outside this document the architecture safety analysis section 11 (`NvmManager.h`) and the register text of AN-016. **Corrected in issue 2 (update DOC-60)**; the reviewer's check is open (register AN-177). The failure mode of the diagnostic CAN on CAN1 (BL-39, R8) follows with DOC-56 after Release R1 |

---

## 10. Review questions of the architecture (section 11)

This task changed no reusable code, no generator and no bootloader; it added this document, a consistency test
under `doc/safety/test/`, and pointers in `concept.md`. The questions are answered for the analysis.

1. **Board without an SBC?** Yes. Only stage-1 mechanisms are credited; none uses the SBC. F13 names the
   consequence explicitly: without an SBC nothing limits a reset loop. Since SYS-43 the reset-loop limit (T4) does,
   with the MCU's own reset status and a record in no-init RAM only.
2. **Different crystal and system clock?** Yes. All times are stated as times; the one computed duration (ROM check,
   walk) names the 80 MHz it assumes. F9 and section 5.2 (system clock check) make a wrong clock a detected fault
   rather than an assumption.
3. **Different derivative, S32K3 / STM32 / Cortex-R?** Yes. The analysis follows the concept's split into neutral
   and family mechanisms; the register list of section 5.2 is by class and names the S32K1 registers as the
   instance of the family port. Other families get their own entries.
4. **Two or more CAN buses?** Yes. SM-31/SM-32 are analysed per bus; F5's correlation is per bus instance.
5. **Several tasks and free priorities?** Yes, and F9/F10 are exactly about task timing that is assumed rather than
   supervised.
6. **Every new value in exactly one configuration level?** No new value is introduced. Proposed ones name their
   level: the re-check period of T5 is a module parameter of `safeMemory` (project level, since SYS-44
   `modules.safeMemory.rom_check_period_ms`); the reset-loop limit of T4
   is a module parameter of the supervisor (SYS-43: provisional defaults in the family port until its manifest
   exists, proposed `modules.safeSupervisor.reset_loop_limit` / `reset_loop_clear_ms`); the register list of SYS-24 is derived, not configured.
7. **Host test with its own test profile?** The new test `doc/safety/test/test_fmea.py` checks the document against
   the concept and `tools/test/tests.yaml` (every stage-1 mechanism analysed, every cited test exists, no coverage
   percentages); it needs no board profile. The code gaps are F15/T3.
8. **Tool core free of Qt, messages with a YAML path?** Not applicable.
9. **Device driver granularity?** Not applicable — no driver analysed (stage 2).
10. **Placement under `platforms/`?** Not applicable — no new file there.

---

## 11. Sources

Issue 1: all from inside the repository at `63ea900`; ISO 26262 was not consulted then (concept section 0 of issue 2).
Issue 2: the repository at the working state; the standard only through `iso26262-gap.md` (not published, available on request), by part
and clause.

- Concept: [`concept.md`](concept.md) issue 2 with the SYS-38 update (issue 1); **issue 3** (DOC-51, CM-07) for issue 2 of
  this document - section 7.2 with the faults of the time bases, 9.4 answered by the gap analysis.
- Issue 2: [`dfa.md`](dfa.md) (DF-05, DFA-2, DFA-6, section 8.1), [`safety-manual.md`](safety-manual.md) sections 4 and 5,
  [`anomaly-register.md`](anomaly-register.md), the board records of section 8.2.
- Code: `ecu/safety/safeSupervisor/src/safeSupervisor/SafeSupervisor.cpp`,
  `ecu/safety/safeWatchdog/src/safeWatchdog/SafeWatchdog.cpp`,
  `platforms/s32k1xx/port/safety/safeLifecycle/src/safeLifecycle/SafetyManager.cpp`,
  `platforms/s32k1xx/port/safety/safeMemory/src/safeMemory/{SafeMemory,IntegrityEccHandler,RomCheck}.cpp`,
  `platforms/s32k1xx/port/safety/safeSystem/src/safeSystem/SafeSystem.cpp`,
  `platforms/s32k1xx/port/safety/safeIo/src/safeIo/SafeIo.cpp`,
  `platforms/s32k1xx/port/main/src/integration/{DebugHaltGuardSystem.h,RomCheckSystem.h,Integration.cpp}`,
  `external/openbsw/platforms/s32k1xx/safety/safeBspMcuWatchdog/src/watchdog/Watchdog.cpp` (read only),
  `ecu/application/src/systems/{SafetySystem.cpp,RteSystem.cpp}`, `ecu/asyncCoreConfiguration/tasks.yaml`,
  `bootloader/src/core/app_image.c`, `boards/evalboard_s32k144/board.yaml`.
- Tests: `tools/test/tests.yaml`.
- Board records: `CHANGELOG.md`, board sessions of 2026-09-16, 2026-09-18 and 2026-09-21 (parts 4 to 8).
- By name only, not quoted: NXP AN12148 "S32K1xx and SBC in safety applications" (`doc/confidential/s32k1xx`),
  which refers to the S32K1 safety manual that the project does not have (N2).

---

## 12. Change history

### 12.1 The status and update blocks of issue 1 (unchanged)

Issue 1 recorded its changes as blocks at the head of the document. They are kept here verbatim (CM-02 decision Z5);
the blocks name the state of their day, which issue 2 brings up to date in the sections themselves.

Status: 2026-09-21, **issue 1 (SYS-20)**, written against `dev` at `63ea900` (after SYS-23, SYS-36, SYS-37, SYS-38).
Update 2026-09-22 (**SYS-49**, against `dev` at `f2e2c43`): the results of **SYS-41** (task T2 and most of T3) are
carried in — F11, F12 (watchdog), F14 and the SM-04 checkpoints are closed with host tests; the deadman configuration
re-check (F12, SM-06) is **host verified, target binding pending (SYS-48)**. Sections 3, 3.7, 6, 7 and 8 say so per
item; the rest of the analysis is unchanged.
Update 2026-09-22 (**SYS-40**, against `dev` at `1bbba28`): finding **F9** is addressed by a period check that extends **SM-04**
(the safety task's period against the LPTMR on the LPO clock, host verified, board checks B4/B7 open). Sections 0, 2,
3.1, 3.7, 6, 7 and 8 carry it; the guaranteed times of section 2 are restated.
Update 2026-09-22 (**SYS-42**): the rest of task **T3** — host tests with built-in faults for the MPU gate check
(**SM-11**, `safety-gate`) and for `SafeIo` lock and check (**SM-21**, **SM-22**, `safe-io`), 9 mutants each. **F15 is
closed.** The SafeIo test found a gap of SM-21 (the read-back after the lock ignored the lock bit), closed in the same
task. Sections 0, 3.2, 3.3, 3.7, 4, 6, 7 and 8 say so per item.
Update 2026-09-22 (**SYS-44**): task **T5** — the ROM check (**SM-14**) is repeated with a period set as a module
parameter (`modules.safeMemory.rom_check_period_ms`, default 60 000 ms, 0 = once per start), preceded by a
known-answer test of the CRC unit, and suspended while a flash write is open or a flash command runs; a pass that could
not run for more than one period is reported. **Host verified with mutants** (`rom-check`, `rom-check-mutations`,
`nvm-flash-write-bracket`), board checks B1 and B9 open. Sections 0, 3.2, 3.7, 4, 5.1, 6, 7 and 8 say so per item.

Update 2026-09-22 (**SYS-43**): task **T4** — the **reset-loop limit** (decision `sys43-resetschleife`): consecutive
safety resets are counted in no-init RAM and the start after the N-th one holds S1 without a reset (like `sys23-halt`);
power-on and a configured time of fault-free operation clear the count. It extends the reaction side of **SM-50** (the
start reason decides whether a start counts). **Host verified with 17 built-in faults** (`safe-supervisor`), placement
of the record checked in the ELF (`safety-context-elf`); board checks B10 and B11 open. **F13 is addressed.** Sections
3.6, 4, 6, 7, 8 and 10 say so per item.

Update 2026-09-22 (**SYS-29**): the report path that section 5.3 assigns to G5 exists - bus id in the device context
word and correlation of communication faults per bus in `SafeSupervisor::handle()` (new mechanism **SM-34**, proposed for
concept section 6.6, host verified with 9 built-in faults). Only sections 5.3 and 7 were changed; SM-34 detects
nothing itself and gets no row in section 3 until a device package reports through it (SYS-28).
Update 2026-09-28 (**SYS-91**): **SM-14** is extended. The calibration segment has left the checksum list (a dataset
flashed without a build held the ECU through the build reference before the calibration start-up check could act,
board 2026-09-28) and is re-checked in every pass against its own CALV header; a deviation is `ROM_CHECK_FAILURE` as
before. Host verified, board repetition open. Only the SM-14 row of section 3 changed.
Update 2026-09-30 (**SYS-97**, findings STK-1/STK-2/STK-6 of `sw-architecture.md`): the interrupt stack (MSP) gets
a guard - 1 KB below `__StackLimit` that the core may read but not write, an MPU descriptor of its own. It extends
**SM-10** (a second protected area: an overflow of the MSP is caught on its first write into the guard instead of
overwriting heap and `.bss`), **SM-13** (the guard descriptor is compared every cycle like the others; a layout in
which the guard cannot be cut out of a full-access region is reported as a configuration error) and **SM-17** (the
hard fault moves an MSP below the limit back to the top before any C code and writes its own line). The MSP grows
from 1 KB to 4 KB against a tool-proven need of ≥ 2 788 B. **Host verified** (`safety-gate`, `kernel-fault-reaction`,
`freertos-config-elf`, `application-linker`, `stackuse`, `stackuse-demo`); board proof (`inject mspoverflow`,
`inject mspuse`) open. Only the rows SM-10, SM-13 and SM-17 of section 3 changed.
Update 2026-09-30 (**DOC-38**): the decisions of the safety responsible in the safety reviews of SYS-117
(`review-sys117`), IO-45/SYS-123 (`review-io45`) and SYS-86 (`review-sys86`) are recorded under SM-23 and SM-58
(section 3.4); the residuals of SM-58 name their follow-up tasks SYS-128, SYS-129, SYS-130 and the board points
AN-119, AN-120. The document was released by the user on 2026-09-30 (document register). Nothing else changed.
Update 2026-10-01 (**SYS-153**, **NVM-25**): **SM-59** - `PMC_LVDSC1` is in the SafeIo register list since SYS-153
and compared every safety cycle by SM-22 (rest of H9 closed, board proof open); new **SM-60** (lock of the FlexNVM
block, NVM-25, proposed and not credited) in section 3.2 with the proposed **AoU-40**. Only the rows SM-59 and SM-60
of sections 3 and 3.7 changed.
Update 2026-10-02 (**SYS-171**, safety review check point SYS69-S2, findings SFP-F02 … SFP-F04 of
`reviews/code-inspection/INS-SFP-1.md`): **SM-13** now really compares descriptor 0 (debugger region) every cycle and
the access word of every descriptor in all fields of the bus masters 0 … 3 (before: descriptor 0 never read, only
M0 … M2 compared); **SM-10** and open point **N3** are corrected - descriptor 0 grants the DMA (M2) and the ENET (M3)
rwx over the whole address space, so the MPU does not keep other bus masters out of the protected RAM (no such user
today). Host verified with 15 mutants; board check B17 open. Only the rows SM-10 and SM-13 of section 3, the SYS-21
line of section 7, section 8 (B17) and N3 of section 9 changed.
Update 2026-10-02 (**SYS-173**, **SYS-174**, safety review 2026-10-02, check points SYS69-S4 and SYS69-S5): **SM-53**
is restated to what the unit does - a bounded, supervised run-down with restart; the restart itself is supervised and
ended by a direct reset after `shutdown_timeout_ms`; a new last run-down step walks the safe outputs table; a timed-out
step is aborted; `continue` never passes the NvM step. Only the SM-53 block of section 3.6 changed; the summary row in
3.7 is unchanged (reaction, per step time-out, restart).
Update 2026-10-02 (review Welle-0210-Tag, decisions of the safety responsible): the watchdog gap on the way to level 0 is
justified in the SM-53 block and carried as N5 (accepted, S173-1); the privileged-mode assumption of the interrupt lock
of SYS-172 is carried as N6 (AoU-42, accepted, SYS-172 P1); N3 names AoU-41 (S171-1). No mechanism changed.

Changes of issue 1 that the blocks above do not name (finding F-02 of CR-02-1, read from the history of the file):
SYS-117 (degraded reaction SD), SYS-86 (SM-58), IO-45 and SYS-123 (group lock), SYS-128 and SYS-130 (detection and
report of SM-58), SYS-116 (SM-26, SM-27), SYS-30 (read-back of S1), SYS-135 (HW_SHUTOFF to SD), SYS-139 and SYS-140
(active short C2/C4), SYS-143 (start test after power-on), SYS-133 (deadman definitions in `Integration.cpp`),
SYS-138 (read-back in the hold, SD and before the reset) - all 2026-09-30; SYS-148/SYS-149 (watchdog dump, restart
times) and SYS-129 (path test of SM-58) - 2026-10-01; SYS-169 (watchdog line of SM-17) - 2026-10-02; SYS-194 (SM-34
first user) and DOC-45 (BL-38/BL-39 in SM-51, N4) - 2026-10-03. Each changed only the block of its mechanism.

### 12.2 Issues

| Issue | Date | Task | Change |
|---|---|---|---|
| 1 | 2026-09-21 | SYS-20 | first issue: 29 stage-1 mechanisms, coverage classes, timing basis, findings F9 … F16, tasks T1 … T6, board checklist |
| 1 (updates) | 2026-09-22 … 2026-10-03 | SYS-49 … DOC-45 | the blocks of 12.1 and the changes named after them; released by the user with the updates up to DOC-38 on 2026-09-30 |
| **2** | 2026-10-04 | DOC-55 | corrections after the confirmation review CM-02 (record `reviews/records/CR-02-1.md`, the user under D-01, baseline `dev` at aaa22c86, `accepted with conditions`; decision Z5): header table and state line `Working state` instead of `Baseline: v0.2.0` (F-01, F-02); this history (F-02); the scope of Release R1 in section 0 (Z4); the times: SM-01 up to about 283 ms over the LPO range, the faults of the time bases of concept 7.2 issue 3 in section 2.1, the board bound of B3 in SM-23, the measured lock times of SYS-172, B2 with bootloader 0.3.9 and its composition, the mechanism count of section 0 (F-03); stale references to concept 9.4, issue 2, `requirements.yaml`, "demo scope" (F-04); the residual risk DF-05 in SM-01 and SM-06, the licence line (F-05, Z3); SM-04 kinds 4/5 (DFA-6), SM-06 progress check (DFA-2) and the stopped system time, SM-21/SM-22 at the state of SYS-24/SYS-153 (F-07); SM-21/SM-22 `medium` with the blind spot `SYSTEM_CLOCK` (Z1), N1 closed with the gap analysis section 3 (M3), M2 with DOC-56 (Z2) (F-09); the board results of 2026-09-22 … 2026-10-04 in 8.1 and in the mechanism blocks (F-10); the statuses of F6, F8, F9, F12, F13, F16 and N1 as in the anomaly register (F-11); SM-26/SM-27 marked as accepted by `nxconfig` and called by the port, not credited for Release R1; N6 with the measured lock times (Z6) |
| 2 (accepted) | 2026-10-04 | DOC-59 | re-check of CM-02 by the user (D-01, without independence) at `dev` efc474ea: F-01 … F-11 closed, issue 2 released (record CR-02-1 `accepted`). After it, as decided there (BRD a, known gaps of Release R1): the captures of B2 (8.1) and B15 (8.2) of 2026-09-30 checked in after a check for key material, 8.1/8.2 point to them; BT-S1-03 on the board list of Release R1; B16 (a), (c), (d) and the LVD reset at a lab supply (SM-59, 3.7, 8.2) with SYS-198 after Release R1; S171-3 back to SYS-171 (SM-13, N3); the caveat of B17 (`RGD0_WORD3` = 0) in SM-10, SM-13 and N3; state line |
| 2 (update) | 2026-10-04 | DOC-60 | upkeep after the re-check of CM-02 (finding RN, remarks R2, R3, R4, R11, Z4; open point N7, register AN-177): header line "update blocks up to 2026-10-03" (R2), SM-23 row of 3.7 with the board bound of B3 (R3), SM-07 and SM-18 with the binding AoU-25/AoU-24 (R4, R11), SYS-135 residual (3) at concept issue 3 and SYS-129 residual (2) with SYS-165 (Z4), SM-26 effectiveness and residuals (1)/(2), SM-27 effectiveness and residual (3) at the state of SYS-139 … SYS-143 (Z4), section 0 "Verification" with 8.2; no class, time or verdict changed |
| 2 (update) | 2026-10-08 | SYS-209 | section 1.3: the sentence that related the classes low / medium / high to the coverage figures commonly used with them is replaced by a reference to the clause (5-Annex D, 11-5.1.12, 11-5.2.4), without figures and without text of the standard (decision C8 of the user in the re-check of 2026-10-08, "replace"); accepted by the user in the re-check of 2026-10-08 (C8) as a delta of CM-02, record `reviews/records/CR-02-1.md` (not published, available on request); header status, state line `Working state: dev at c0928380 (after v1.0.0-rc1)`. No class, mechanism, time or count changed |
